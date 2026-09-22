from __future__ import annotations

import base64
import copy
import http.client
import importlib.util
import json
import os
import threading
import types

import pytest
from kubernetes import client


def _load_mutate_module():
    here = os.path.dirname(__file__)
    modpath = os.path.join(here, "..", "mutate.py")
    spec = importlib.util.spec_from_file_location("gpu_offload_mutate", modpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeConfigMap:
    def __init__(self, data):
        self.data = data


class _FakeCoreApi:
    def __init__(self, configmaps=None):
        self.configmaps = configmaps or {}

    def read_namespaced_config_map(self, name, namespace):
        key = (namespace, name)
        if key not in self.configmaps:
            raise client.exceptions.ApiException(status=404)
        return _FakeConfigMap(self.configmaps[key])


class _FakeAppsApi:
    def __init__(self, *, parent_deployments=None, parent_statefulsets=None, deployments=None):
        self.parent_deployments = parent_deployments or {}
        self.parent_statefulsets = parent_statefulsets or {}
        self.deployments = deployments or {}
        self.actions = []

    def read_namespaced_deployment(self, name, namespace):
        key = (namespace, name)
        if key in self.parent_deployments:
            return copy.deepcopy(self.parent_deployments[key])
        if key not in self.deployments:
            raise client.exceptions.ApiException(status=404)
        return copy.deepcopy(self.deployments[key])

    def read_namespaced_stateful_set(self, name, namespace):
        key = (namespace, name)
        if key not in self.parent_statefulsets:
            raise client.exceptions.ApiException(status=404)
        return copy.deepcopy(self.parent_statefulsets[key])

    def create_namespaced_deployment(self, namespace, body):
        self.deployments[(namespace, body["metadata"]["name"])] = copy.deepcopy(body)
        self.actions.append(("create", namespace, body["metadata"]["name"]))

    def patch_namespaced_deployment(self, name, namespace, body):
        self.deployments[(namespace, name)] = copy.deepcopy(body)
        self.actions.append(("patch", namespace, name))

    def delete_namespaced_deployment(self, name, namespace):
        self.deployments.pop((namespace, name), None)
        self.actions.append(("delete", namespace, name))

    def list_namespaced_deployment(self, namespace, label_selector=None):
        key_filter, _, value_filter = (label_selector or "").partition("=")
        items = []
        for (ns, name), body in self.deployments.items():
            if ns != namespace:
                continue
            metadata = body.get("metadata", {}) or {}
            if label_selector:
                labels = metadata.get("labels", {}) or {}
                if labels.get(key_filter) != value_filter:
                    continue
            owner_references = [
                types.SimpleNamespace(uid=owner.get("uid")) for owner in metadata.get("ownerReferences", []) or []
            ]
            items.append(
                types.SimpleNamespace(metadata=types.SimpleNamespace(name=name, owner_references=owner_references))
            )
        return types.SimpleNamespace(items=items)


class _FakeBatchApi:
    def __init__(self, *, parent_jobs=None):
        self.parent_jobs = parent_jobs or {}

    def read_namespaced_job(self, name, namespace):
        key = (namespace, name)
        if key not in self.parent_jobs:
            raise client.exceptions.ApiException(status=404)
        return copy.deepcopy(self.parent_jobs[key])


def _decode_patch(response):
    return json.loads(base64.b64decode(response["response"]["patch"]).decode("utf-8"))


def _base_workload(kind="Deployment"):
    if kind == "Pod":
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "client",
                "namespace": "default",
                "uid": "pod-uid",
                "annotations": {"xavierconfig": "remoteablecm: client-cm\n"},
            },
            "spec": {
                "serviceAccountName": "existing-sa",
                "containers": [
                    {
                        "name": "app",
                        "image": "repo/app:1",
                        "command": ["sleep", "3600"],
                    }
                ],
            },
        }
    return {
        "apiVersion": "apps/v1" if kind != "Job" else "batch/v1",
        "kind": kind,
        "metadata": {
            "name": "client",
            "namespace": "default",
            "uid": "workload-uid",
            "annotations": {"xavierconfig": "remoteablecm: client-cm\n"},
        },
        "spec": {
            "template": {
                "metadata": {},
                "spec": {
                    "serviceAccountName": "existing-sa",
                    "containers": [
                        {
                            "name": "app",
                            "image": "repo/app:1",
                            "command": ["sleep", "3600"],
                        }
                    ],
                },
            }
        },
    }


def test_mutation_adds_configmap_volume_env_and_template_labels():
    mod = _load_mutate_module()
    deploy = _base_workload()
    obj = copy.deepcopy(deploy)

    mutated = mod.DoMutate(obj, strict=True)

    assert mutated is True
    spec = obj["spec"]["template"]["spec"]
    container = spec["containers"][0]
    assert any(volume.get("configMap", {}).get("name") == "client-cm" for volume in spec["volumes"])
    assert any(mount.get("name") == "xavierconfig" for mount in container["volumeMounts"])
    assert mod.get_env_var(container, "REMOTER_CONFIG") == "/xavierconfig/remote.yaml"
    assert mod.get_env_var(container, "SERVERLABEL") == "apprmt=client-remote-server"
    assert obj["spec"]["template"]["metadata"]["labels"]["xavier"] == "true"
    assert obj["spec"]["template"]["metadata"]["labels"]["xavier-parent-name"] == "client"
    assert spec["serviceAccountName"] == "existing-sa"


def test_remoteable_container_filter_only_mutates_listed_containers():
    mod = _load_mutate_module()
    deploy = _base_workload()
    deploy["metadata"]["annotations"]["xavierconfig"] = "remoteablecm: client-cm\nremoteableconts:\n  - sidecar\n"
    deploy["spec"]["template"]["spec"]["containers"] = [
        {"name": "app", "image": "repo/app:1"},
        {"name": "sidecar", "image": "repo/sidecar:1"},
    ]

    obj = copy.deepcopy(deploy)
    assert mod.DoMutate(obj, strict=True) is True

    app = next(container for container in obj["spec"]["template"]["spec"]["containers"] if container["name"] == "app")
    sidecar = next(
        container for container in obj["spec"]["template"]["spec"]["containers"] if container["name"] == "sidecar"
    )
    assert app.get("volumeMounts") is None
    assert any(mount.get("name") == "xavierconfig" for mount in sidecar["volumeMounts"])


def test_parent_pod_only_gets_perclient_label_on_xavier_container():
    mod = _load_mutate_module()
    pod = {
        "kind": "Pod",
        "metadata": {
            "name": "client-pod",
            "namespace": "default",
            "labels": {
                "xavier-parent-name": "parent-deployment",
                "xavier-parent-kind": "Deployment",
            },
        },
        "spec": {
            "containers": [
                {"name": "xavier", "env": [{"name": "XAVIER_CONTAINER", "value": "true"}]},
                {"name": "plain"},
            ]
        },
    }

    assert mod.DoMutate(pod) is True
    assert mod.get_env_var(pod["spec"]["containers"][0], "PERCLIENTSERVERLABEL") == "apprmt=client-pod-remote-server"
    assert pod["spec"]["containers"][1].get("env") is None
    assert pod["spec"].get("volumes") is None


def test_generated_parent_pod_defers_perclient_label_to_runtime():
    mod = _load_mutate_module()
    pod = {
        "kind": "Pod",
        "metadata": {
            "generateName": "client-job-",
            "namespace": "default",
            "labels": {
                "xavier-parent-name": "parent-job",
                "xavier-parent-kind": "Job",
            },
        },
        "spec": {
            "containers": [
                {"name": "xavier", "env": [{"name": "XAVIER_CONTAINER", "value": "true"}]},
            ]
        },
    }

    assert mod.DoMutate(pod) is True
    assert mod.get_env_var(pod["spec"]["containers"][0], "PERCLIENTSERVERLABEL") == "unknown"


def test_admission_review_returns_patch_for_opted_workload():
    mod = _load_mutate_module()
    controller = mod.XavierAdmissionController()
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "1234",
            "operation": "CREATE",
            "object": _base_workload(),
        },
    }

    status_code, response = controller.handle_admission_review(review)

    assert status_code == 200
    assert response["response"]["allowed"] is True
    assert response["response"]["patchType"] == "JSONPatch"
    patch = _decode_patch(response)
    assert any(operation["path"].endswith("/volumes") for operation in patch)
    assert any(
        "xavier-parent-name" in operation["path"] or "xavier-parent-name" in (operation.get("value") or {})
        for operation in patch
    )


def test_admission_review_passes_through_non_opted_workload():
    mod = _load_mutate_module()
    controller = mod.XavierAdmissionController()
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "1234",
            "operation": "CREATE",
            "object": {
                "kind": "Deployment",
                "metadata": {"name": "plain", "namespace": "default"},
                "spec": {"template": {"metadata": {}, "spec": {"containers": [{"name": "app"}]}}},
            },
        },
    }

    status_code, response = controller.handle_admission_review(review)

    assert status_code == 200
    assert response["response"]["allowed"] is True
    assert "patch" not in response["response"]


def test_admission_review_denies_malformed_xavierconfig():
    mod = _load_mutate_module()
    controller = mod.XavierAdmissionController()
    review = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "1234",
            "operation": "CREATE",
            "object": {
                "kind": "Deployment",
                "metadata": {
                    "name": "broken",
                    "namespace": "default",
                    "annotations": {"xavierconfig": "remoteablecm: [unterminated"},
                },
                "spec": {"template": {"metadata": {}, "spec": {"containers": [{"name": "app"}]}}},
            },
        },
    }

    status_code, response = controller.handle_admission_review(review)

    assert status_code == 200
    assert response["response"]["allowed"] is False
    assert "valid YAML" in response["response"]["status"]["message"]


def test_http_server_exposes_health_and_mutate_routes():
    mod = _load_mutate_module()
    controller = mod.XavierAdmissionController()
    server = mod.AdmissionHTTPServer(("127.0.0.1", 0), controller, ssl_context=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/healthz?verbose=true")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok"}

        review = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "request": {"uid": "server-test", "operation": "CREATE", "object": _base_workload()},
        }
        payload = json.dumps(review)
        connection.request("POST", "/mutate?timeout=10s", body=payload, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["response"]["allowed"] is True
        assert body["response"]["patchType"] == "JSONPatch"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_validate_xavier_config_rejects_privileged_root_settings():
    mod = _load_mutate_module()

    with pytest.raises(mod.XavierConfigError, match="privileged=true"):
        mod.validate_xavier_config(
            {"remoteablecm": "cm", "securityContext": {"privileged": True}},
            source="annotation",
            require_remoteablecm=True,
        )

    with pytest.raises(mod.XavierConfigError, match="runAsUser=0"):
        mod.validate_xavier_config(
            {"remoteablecm": "cm", "securityContext": {"runAsUser": 0}},
            source="annotation",
            require_remoteablecm=True,
        )


def test_validate_xavier_config_rejects_escalation_capabilities_and_unconfined_seccomp():
    mod = _load_mutate_module()

    with pytest.raises(mod.XavierConfigError, match="allowPrivilegeEscalation=true"):
        mod.validate_xavier_config(
            {"remoteablecm": "cm", "securityContext": {"allowPrivilegeEscalation": True}},
            source="annotation",
            require_remoteablecm=True,
        )

    with pytest.raises(mod.XavierConfigError, match=r"capabilities\.add"):
        mod.validate_xavier_config(
            {"remoteablecm": "cm", "securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}},
            source="annotation",
            require_remoteablecm=True,
        )

    with pytest.raises(mod.XavierConfigError, match=r"seccompProfile\.type=Unconfined"):
        mod.validate_xavier_config(
            {"remoteablecm": "cm", "securityContext": {"seccompProfile": {"type": "Unconfined"}}},
            source="annotation",
            require_remoteablecm=True,
        )

    # a benign securityContext is still accepted
    normalized = mod.validate_xavier_config(
        {
            "remoteablecm": "cm",
            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
        },
        source="annotation",
        require_remoteablecm=True,
    )
    assert normalized["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_build_desired_server_deployments_merges_supported_schema_fields():
    mod = _load_mutate_module()
    pod = _base_workload(kind="Pod")
    pod["metadata"]["labels"] = {"xavier": "true"}
    pod["spec"]["containers"][0].update(
        {
            "env": [
                {"name": "XAVIER_CONTAINER", "value": "true"},
                {"name": "REMOTERPORT", "value": "30001"},
                {"name": "KEEP_ME", "value": "from-client"},
                {"name": "FROM_FIELD", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
            ],
            "volumeMounts": [
                {"name": "xavierconfig", "mountPath": "/xavierconfig", "readOnly": True},
                {"name": "host-socket", "mountPath": "/socket"},
            ],
        }
    )
    pod["spec"]["volumes"] = [
        {"name": "xavierconfig", "configMap": {"name": "client-cm"}},
        {"name": "host-socket", "hostPath": {"path": "/var/run/docker.sock"}},
    ]
    pod["metadata"]["annotations"]["xavierconfig"] = (
        "remoteablecm: client-cm\nenv:\n  - name: ANNOTATION_ENV\n    value: annotation\n"
    )
    core_api = _FakeCoreApi(
        {
            ("default", "client-cm"): {
                "remote.yaml": (
                    "serverimage: registry/default:1\n"
                    "serverreplicas: 2\n"
                    "remoteableenv:\n"
                    "  - KEEP_ME\n"
                    "  - FROM_FIELD\n"
                    "nodeSelector:\n"
                    "  accelerator: gpu\n"
                    "securityContext:\n"
                    "  runAsNonRoot: true\n"
                    "  runAsUser: 1000\n"
                    "env:\n"
                    "  - name: GLOBAL_ENV\n"
                    "    value: global\n"
                    "resources:\n"
                    "  limits:\n"
                    '    nvidia.com/gpu: "1"\n'
                    "serverstages:\n"
                    '  - name: ""\n'
                    "  - name: perclient\n"
                    "    perclient: true\n"
                    "    serverimage: registry/perclient:2\n"
                    "    serverreplicas: 3\n"
                    "    nodeSelector:\n"
                    "      tier: edge\n"
                    "    securityContext:\n"
                    "      runAsNonRoot: true\n"
                    "      runAsUser: 1001\n"
                    "    env:\n"
                    "      - name: STAGE_ENV\n"
                    "        value: stage\n"
                    "    resources:\n"
                    "      requests:\n"
                    '        cpu: "500m"\n'
                    "  - name: skip\n"
                    "    noserverdeployment: true\n"
                )
            }
        }
    )
    apps_api = _FakeAppsApi()
    batch_api = _FakeBatchApi()

    desired = mod.build_desired_server_deployments(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    default_deployment = desired["client-remote-server"]
    stage_deployment = desired["client-remote-server-perclient"]
    assert desired["client-remote-server-skip"] is None
    assert default_deployment["spec"]["replicas"] == 2
    assert default_deployment["spec"]["template"]["spec"]["serviceAccountName"] == "existing-sa"
    assert default_deployment["spec"]["template"]["spec"]["nodeSelector"] == {"accelerator": "gpu"}
    assert default_deployment["spec"]["template"]["spec"]["containers"][0]["securityContext"]["runAsUser"] == 1000
    assert (
        default_deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    )
    env_names = {env["name"] for env in default_deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert {"KEEP_ME", "ANNOTATION_ENV", "GLOBAL_ENV", "SERVER", "DEPLOYMENT_NAME", "REMOTER_CONFIG"} <= env_names
    assert any(
        env["name"] == "FROM_FIELD" and "valueFrom" in env
        for env in default_deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    )
    assert all("hostPath" not in volume for volume in default_deployment["spec"]["template"]["spec"].get("volumes", []))
    copied_volume_names = {
        volume["name"] for volume in default_deployment["spec"]["template"]["spec"].get("volumes", [])
    }
    copied_mount_names = {
        mount["name"]
        for mount in default_deployment["spec"]["template"]["spec"]["containers"][0].get("volumeMounts", [])
    }
    assert copied_mount_names == {"xavierconfig"}
    assert copied_mount_names <= copied_volume_names
    assert stage_deployment["spec"]["replicas"] == 3
    assert stage_deployment["spec"]["template"]["spec"]["nodeSelector"] == {"tier": "edge"}
    assert stage_deployment["spec"]["template"]["spec"]["containers"][0]["image"] == "registry/perclient:2"
    assert stage_deployment["spec"]["template"]["spec"]["containers"][0]["securityContext"]["runAsUser"] == 1001
    assert stage_deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] == "500m"


@pytest.mark.parametrize(
    "labels,env",
    [
        pytest.param({}, [{"name": "REMOTERPORT", "value": "30001"}], id="missing-label"),
        pytest.param({"xavier": "true"}, [], id="missing-remoterport-env"),
        pytest.param({}, [], id="missing-both"),
    ],
)
def test_build_desired_server_deployments_requires_all_opt_in_signals(labels, env):
    mod = _load_mutate_module()
    pod = _base_workload(kind="Pod")
    pod["metadata"]["labels"] = labels
    pod["spec"]["containers"][0]["env"] = env
    core_api = _FakeCoreApi({("default", "client-cm"): {"remote.yaml": 'serverstages:\n  - name: ""\n'}})
    apps_api = _FakeAppsApi()
    batch_api = _FakeBatchApi()

    desired = mod.build_desired_server_deployments(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    assert desired == {}


def test_build_desired_server_deployments_uses_parent_config_for_perclient_pods():
    mod = _load_mutate_module()
    pod = {
        "kind": "Pod",
        "metadata": {
            "name": "client-pod",
            "namespace": "default",
            "uid": "pod-uid",
            "labels": {
                "xavier-parent-name": "owner",
                "xavier-parent-kind": "Deployment",
            },
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "repo/app:1",
                    "env": [
                        {"name": "XAVIER_CONTAINER", "value": "true"},
                        {"name": "PERCLIENTSERVERLABEL", "value": "apprmt=client-pod-remote-server"},
                    ],
                    "volumeMounts": [{"name": "xavierconfig", "mountPath": "/xavierconfig"}],
                }
            ],
            "volumes": [{"name": "xavierconfig", "configMap": {"name": "client-cm"}}],
        },
    }
    parent = _base_workload()
    parent["metadata"]["name"] = "owner"
    parent["metadata"]["annotations"]["xavierconfig"] = "remoteablecm: client-cm\n"
    parent["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "XAVIER_CONTAINER", "value": "true"}]
    core_api = _FakeCoreApi(
        {("default", "client-cm"): {"remote.yaml": "serverstages:\n  - name: perclient\n    perclient: true\n"}}
    )
    apps_api = _FakeAppsApi(parent_deployments={("default", "owner"): parent})
    batch_api = _FakeBatchApi()

    desired = mod.build_desired_server_deployments(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    deployment = desired["client-pod-remote-server-perclient"]
    env_dict = {
        env["name"]: env.get("value")
        for env in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        if "value" in env
    }
    assert env_dict["PERCLIENTSERVERLABEL"] == "apprmt=client-pod-remote-server"


def test_reconcile_named_server_deployment_creates_patches_and_deletes():
    mod = _load_mutate_module()
    apps_api = _FakeAppsApi()
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "client-remote-server", "namespace": "default"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "client-remote-server"}},
            "template": {
                "metadata": {"labels": {"app": "client-remote-server"}},
                "spec": {"containers": [{"name": "remote-server", "image": "repo/server:1"}]},
            },
        },
    }

    assert (
        mod.reconcile_named_server_deployment(
            apps_api, namespace="default", deployment_name="client-remote-server", desired_spec=desired
        )
        == "created"
    )
    assert (
        mod.reconcile_named_server_deployment(
            apps_api, namespace="default", deployment_name="client-remote-server", desired_spec=desired
        )
        == "unchanged"
    )
    patched = copy.deepcopy(desired)
    patched["spec"]["replicas"] = 2
    assert (
        mod.reconcile_named_server_deployment(
            apps_api, namespace="default", deployment_name="client-remote-server", desired_spec=patched
        )
        == "patched"
    )
    assert (
        mod.reconcile_named_server_deployment(
            apps_api, namespace="default", deployment_name="client-remote-server", desired_spec=None
        )
        == "deleted"
    )
    assert apps_api.actions == [
        ("create", "default", "client-remote-server"),
        ("patch", "default", "client-remote-server"),
        ("delete", "default", "client-remote-server"),
    ]


def test_reconcile_named_server_deployment_ignores_kubernetes_defaulted_fields():
    mod = _load_mutate_module()
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "client-remote-server", "namespace": "default"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "client-remote-server"}},
            "template": {
                "metadata": {"labels": {"app": "client-remote-server"}},
                "spec": {"containers": [{"name": "remote-server", "image": "repo/server:1"}]},
            },
        },
    }
    # what the API server actually stores: our desired fields plus values it
    # defaults on our behalf (strategy, revisionHistoryLimit, restartPolicy, ...)
    stored = copy.deepcopy(desired)
    stored["spec"]["revisionHistoryLimit"] = 10
    stored["spec"]["progressDeadlineSeconds"] = 600
    stored["spec"]["strategy"] = {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"},
    }
    stored["spec"]["template"]["spec"]["restartPolicy"] = "Always"
    stored["spec"]["template"]["spec"]["dnsPolicy"] = "ClusterFirst"
    stored["spec"]["template"]["spec"]["containers"][0]["terminationMessagePolicy"] = "File"
    apps_api = _FakeAppsApi(deployments={("default", "client-remote-server"): stored})

    outcome = mod.reconcile_named_server_deployment(
        apps_api, namespace="default", deployment_name="client-remote-server", desired_spec=desired
    )

    assert outcome == "unchanged"
    assert apps_api.actions == []


def test_reconcile_object_deletes_server_deployment_for_removed_stage():
    mod = _load_mutate_module()
    deploy = _base_workload()
    deploy["metadata"]["labels"] = {"xavier": "true"}
    deploy["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "XAVIER_CONTAINER", "value": "true"},
        {"name": "REMOTERPORT", "value": "30001"},
    ]
    # a server Deployment left over from a stage that no longer exists in serverstages,
    # owned by this same parent (matching what create_server_deployment_spec sets)
    stale_name = "client-remote-server-stale"
    apps_api = _FakeAppsApi(
        deployments={
            ("default", stale_name): {
                "metadata": {
                    "name": stale_name,
                    "namespace": "default",
                    "labels": {"xavierdeployment": "true"},
                    "ownerReferences": [{"uid": "workload-uid"}],
                },
                "spec": {},
            }
        }
    )
    core_api = _FakeCoreApi({("default", "client-cm"): {"remote.yaml": 'serverstages:\n  - name: ""\n'}})
    batch_api = _FakeBatchApi()

    outcomes = mod.reconcile_object(deploy, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    assert outcomes["client-remote-server"] == "created"
    assert outcomes[stale_name] == "deleted"
    assert ("default", stale_name) not in apps_api.deployments


def test_reconcile_object_does_not_delete_unowned_deployment_with_same_name_prefix():
    mod = _load_mutate_module()
    # an unrelated, non-opted-in Pod that happens to share a name with a
    # Deployment which legitimately owns "client-remote-server"
    pod = {
        "kind": "Pod",
        "metadata": {"name": "client", "namespace": "default", "uid": "unrelated-pod-uid"},
        "spec": {"containers": [{"name": "app", "image": "repo/app:1"}]},
    }
    active_name = "client-remote-server"
    apps_api = _FakeAppsApi(
        deployments={
            ("default", active_name): {
                "metadata": {
                    "name": active_name,
                    "namespace": "default",
                    "labels": {"xavierdeployment": "true"},
                    "ownerReferences": [{"uid": "some-other-deployments-uid"}],
                },
                "spec": {},
            }
        }
    )
    core_api = _FakeCoreApi()
    batch_api = _FakeBatchApi()

    outcomes = mod.reconcile_object(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    assert outcomes == {}
    assert ("default", active_name) in apps_api.deployments


def test_create_server_deployment_spec_excludes_secret_bearing_volumes():
    mod = _load_mutate_module()
    pod = _base_workload(kind="Pod")
    pod["metadata"]["labels"] = {"xavier": "true"}
    pod["spec"]["containers"][0].update(
        {
            "env": [{"name": "XAVIER_CONTAINER", "value": "true"}, {"name": "REMOTERPORT", "value": "30001"}],
            "volumeMounts": [
                {"name": "creds", "mountPath": "/creds"},
                {"name": "proj-creds", "mountPath": "/proj-creds"},
                {"name": "config", "mountPath": "/config"},
            ],
        }
    )
    pod["spec"]["volumes"] = [
        {"name": "creds", "secret": {"secretName": "db-password"}},
        {
            "name": "proj-creds",
            "projected": {"sources": [{"secret": {"name": "db-password"}}, {"downwardAPI": {"items": []}}]},
        },
        {"name": "config", "configMap": {"name": "app-config"}},
    ]
    core_api = _FakeCoreApi({("default", "client-cm"): {"remote.yaml": 'serverstages:\n  - name: ""\n'}})
    apps_api = _FakeAppsApi()
    batch_api = _FakeBatchApi()

    desired = mod.build_desired_server_deployments(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    server_spec = desired["client-remote-server"]["spec"]["template"]["spec"]
    volume_names = {volume["name"] for volume in server_spec.get("volumes", [])}
    mount_names = {mount["name"] for mount in server_spec["containers"][0].get("volumeMounts", [])}
    assert volume_names == {"config"}
    assert mount_names == {"config"}


def test_create_server_deployment_spec_only_forwards_allow_listed_env():
    mod = _load_mutate_module()
    pod = _base_workload(kind="Pod")
    pod["metadata"]["labels"] = {"xavier": "true"}
    pod["spec"]["containers"][0]["env"] = [
        {"name": "XAVIER_CONTAINER", "value": "true"},
        {"name": "REMOTERPORT", "value": "30001"},
        {"name": "API_TOKEN", "value": "super-secret"},
        {"name": "SAFE_VALUE", "value": "ok-to-forward"},
        {"name": "FROM_SECRET", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}},
    ]
    core_api = _FakeCoreApi(
        {
            ("default", "client-cm"): {
                "remote.yaml": "\n".join(["serverstages:", '  - name: ""', "remoteableenv:", "  - SAFE_VALUE", ""])
            }
        }
    )
    apps_api = _FakeAppsApi()
    batch_api = _FakeBatchApi()

    desired = mod.build_desired_server_deployments(pod, core_api=core_api, apps_api=apps_api, batch_api=batch_api)

    env_names = {
        env["name"] for env in desired["client-remote-server"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert "SAFE_VALUE" in env_names
    assert "API_TOKEN" not in env_names
    assert "FROM_SECRET" not in env_names


def test_deployment_name_truncates_and_hashes_when_too_long():
    mod = _load_mutate_module()
    metadata = {"name": "a" * 80}

    name = mod._deployment_name(metadata, "stage-one")

    assert len(name) <= 63
    # deterministic: same inputs produce the same bounded name
    assert name == mod._deployment_name(metadata, "stage-one")
    # a different stage for the same long base name produces a different result
    assert name != mod._deployment_name(metadata, "stage-two")


def test_forbidden_mutations_are_not_applied():
    mod = _load_mutate_module()
    pod = _base_workload(kind="Pod")
    pod["spec"]["containers"][0]["securityContext"] = {"runAsUser": 1000}
    obj = copy.deepcopy(pod)

    assert mod.DoMutate(obj, strict=True) is True
    assert obj["spec"]["containers"][0]["command"] == ["sleep", "3600"]
    assert obj["spec"].get("serviceAccountName") == "existing-sa"
    assert all("hostPath" not in volume for volume in obj["spec"].get("volumes", []))
    assert obj["spec"]["containers"][0]["securityContext"]["runAsUser"] == 1000
