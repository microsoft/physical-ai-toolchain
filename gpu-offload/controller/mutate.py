from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import logging
import os
import signal
import ssl
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import yaml
from kubernetes import client, config, watch

logger = logging.getLogger(__name__)

SUPPORTED_KINDS = {"Pod", "Deployment", "Job", "StatefulSet"}
SUPPORTED_OPERATIONS = {"CREATE"}
XAVIER_CONFIG_ANNOTATION = "xavierconfig"
XAVIER_LABEL = "xavier"
XAVIER_PARENT_KIND_LABEL = "xavier-parent-kind"
XAVIER_PARENT_NAME_LABEL = "xavier-parent-name"
XAVIER_DEPLOYMENT_LABEL = "xavierdeployment"
SERVER_SELECTOR_LABEL = "apprmt"
XAVIER_CONFIG_VOLUME_NAME = "xavierconfig"
XAVIER_CONFIG_MOUNT_PATH = "/xavierconfig"
REMOTE_CONFIG_PATH = f"{XAVIER_CONFIG_MOUNT_PATH}/remote.yaml"
DEFAULT_TLS_CERT_PATH = "/tls/tls.crt"
DEFAULT_TLS_KEY_PATH = "/tls/tls.key"
DEFAULT_PORT = 8443
ALLOWED_SERVER_HOST_PATHS_ENV = "ALLOWED_SERVER_HOST_PATHS"

READINESS_PROBE = {
    "exec": {"command": ["cat", "/ready.txt"]},
    "initialDelaySeconds": 5,
    "periodSeconds": 5,
}

# "secret" is deliberately excluded: the client and server are separately-specified
# workloads (potentially different images/nodes), and copying a client's Secret
# volume onto the server would expose its credentials outside their intended scope.
ALLOWED_SERVER_VOLUME_TYPES = {
    "configMap",
    "downwardAPI",
    "emptyDir",
    "ephemeral",
    "hostPath",
    "persistentVolumeClaim",
    "projected",
}


class XavierConfigError(ValueError):
    """Raised when a Xavier workload config is malformed or incompatible."""


def _json_error(message: str, status: HTTPStatus) -> tuple[int, dict[str, Any]]:
    return status, {"error": message}


def _safe_yaml_mapping(raw: str, *, source: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise XavierConfigError(f"{source} is not valid YAML: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise XavierConfigError(f"{source} must decode to a mapping")
    return copy.deepcopy(loaded)


def _normalize_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
    raise XavierConfigError(f"{field} must be a boolean")


def _validate_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise XavierConfigError(f"{field} must be a non-empty string")
    return value


def _validate_string_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise XavierConfigError(f"{field} must be a mapping")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized[str(_validate_string(key, field=f"{field} key"))] = str(
            _validate_string(item, field=f"{field}[{key!r}]")
        )
    return normalized


def _validate_env_list(value: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise XavierConfigError(f"{field} must be a list")
    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise XavierConfigError(f"{field}[{index}] must be a mapping")
        name = _validate_string(entry.get("name"), field=f"{field}[{index}].name")
        if "value" not in entry:
            raise XavierConfigError(f"{field}[{index}].value is required")
        env_value = entry["value"]
        if not isinstance(env_value, str):
            raise XavierConfigError(f"{field}[{index}].value must be a string")
        normalized.append({"name": name, "value": env_value})
    return normalized


def _validate_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise XavierConfigError(f"{field} must be a list")
    return [_validate_string(item, field=f"{field}[{index}]") for index, item in enumerate(value)]


def _validate_resources(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XavierConfigError(f"{field} must be a mapping")
    return copy.deepcopy(value)


def _validate_security_context(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XavierConfigError(f"{field} must be a mapping")
    normalized = copy.deepcopy(value)
    if normalized.get("privileged") is True:
        raise XavierConfigError(f"{field}.privileged=true is not supported")
    run_as_user = normalized.get("runAsUser")
    if run_as_user == 0:
        raise XavierConfigError(f"{field}.runAsUser=0 is not supported")
    if normalized.get("runAsNonRoot") is False:
        raise XavierConfigError(f"{field}.runAsNonRoot=false is not supported")
    if normalized.get("allowPrivilegeEscalation") is True:
        raise XavierConfigError(f"{field}.allowPrivilegeEscalation=true is not supported")
    capabilities = normalized.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("add"):
        raise XavierConfigError(f"{field}.capabilities.add is not supported")
    seccomp_profile = normalized.get("seccompProfile")
    if isinstance(seccomp_profile, dict) and seccomp_profile.get("type") == "Unconfined":
        raise XavierConfigError(f"{field}.seccompProfile.type=Unconfined is not supported")
    return normalized


def _validate_stage(stage: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(stage, dict):
        raise XavierConfigError(f"serverstages[{index}] must be a mapping")
    normalized = copy.deepcopy(stage)
    normalized["name"] = str(stage.get("name", ""))
    if "perclient" in normalized:
        normalized["perclient"] = _normalize_bool(normalized["perclient"], field=f"serverstages[{index}].perclient")
    if "noserverdeployment" in normalized:
        normalized["noserverdeployment"] = _normalize_bool(
            normalized["noserverdeployment"],
            field=f"serverstages[{index}].noserverdeployment",
        )
    if "serverimage" in normalized:
        normalized["serverimage"] = _validate_string(
            normalized["serverimage"],
            field=f"serverstages[{index}].serverimage",
        )
    if "serverreplicas" in normalized:
        replicas = normalized["serverreplicas"]
        if not isinstance(replicas, int) or replicas < 1:
            raise XavierConfigError(f"serverstages[{index}].serverreplicas must be a positive integer")
    if "nodeSelector" in normalized:
        normalized["nodeSelector"] = _validate_string_map(
            normalized["nodeSelector"],
            field=f"serverstages[{index}].nodeSelector",
        )
    if "securityContext" in normalized:
        normalized["securityContext"] = _validate_security_context(
            normalized["securityContext"],
            field=f"serverstages[{index}].securityContext",
        )
    if "env" in normalized:
        normalized["env"] = _validate_env_list(normalized["env"], field=f"serverstages[{index}].env")
    if "remoteableenv" in normalized:
        normalized["remoteableenv"] = _validate_string_list(
            normalized["remoteableenv"],
            field=f"serverstages[{index}].remoteableenv",
        )
    if "resources" in normalized:
        normalized["resources"] = _validate_resources(normalized["resources"], field=f"serverstages[{index}].resources")
    return normalized


def validate_xavier_config(
    raw_config: dict[str, Any],
    *,
    source: str,
    require_remoteablecm: bool,
    materialize_default_stage: bool = True,
) -> dict[str, Any]:
    normalized = copy.deepcopy(raw_config)
    if require_remoteablecm:
        normalized["remoteablecm"] = _validate_string(normalized.get("remoteablecm"), field=f"{source}.remoteablecm")
    elif "remoteablecm" in normalized:
        normalized["remoteablecm"] = _validate_string(normalized["remoteablecm"], field=f"{source}.remoteablecm")

    if "remoteableconts" in normalized:
        normalized["remoteableconts"] = _validate_string_list(
            normalized["remoteableconts"],
            field=f"{source}.remoteableconts",
        )
    if "serverimage" in normalized:
        normalized["serverimage"] = _validate_string(normalized["serverimage"], field=f"{source}.serverimage")
    if "serverreplicas" in normalized:
        replicas = normalized["serverreplicas"]
        if not isinstance(replicas, int) or replicas < 1:
            raise XavierConfigError(f"{source}.serverreplicas must be a positive integer")
    if "nodeSelector" in normalized:
        normalized["nodeSelector"] = _validate_string_map(normalized["nodeSelector"], field=f"{source}.nodeSelector")
    if "securityContext" in normalized:
        normalized["securityContext"] = _validate_security_context(
            normalized["securityContext"],
            field=f"{source}.securityContext",
        )
    if "env" in normalized:
        normalized["env"] = _validate_env_list(normalized["env"], field=f"{source}.env")
    if "remoteableenv" in normalized:
        normalized["remoteableenv"] = _validate_string_list(
            normalized["remoteableenv"], field=f"{source}.remoteableenv"
        )
    if "resources" in normalized:
        normalized["resources"] = _validate_resources(normalized["resources"], field=f"{source}.resources")
    if "noserverdeployment" in normalized:
        normalized["noserverdeployment"] = _normalize_bool(
            normalized["noserverdeployment"],
            field=f"{source}.noserverdeployment",
        )

    top_level_perclient = False
    if "perclient" in normalized:
        top_level_perclient = _normalize_bool(normalized["perclient"], field=f"{source}.perclient")
        normalized["perclient"] = top_level_perclient

    stages = normalized.get("serverstages")
    if stages is None:
        if materialize_default_stage:
            normalized["serverstages"] = [{"name": "", "perclient": top_level_perclient}]
    else:
        if not isinstance(stages, list):
            raise XavierConfigError(f"{source}.serverstages must be a list")
        normalized["serverstages"] = [_validate_stage(stage, index=index) for index, stage in enumerate(stages)]
    return normalized


def _load_annotation_config(raw_annotation: str, *, strict: bool) -> dict[str, Any] | None:
    try:
        parsed = _safe_yaml_mapping(raw_annotation, source=XAVIER_CONFIG_ANNOTATION)
        return validate_xavier_config(
            parsed,
            source=XAVIER_CONFIG_ANNOTATION,
            require_remoteablecm=True,
            materialize_default_stage=False,
        )
    except XavierConfigError:
        if strict:
            raise
        return None


def get_metadata_spec(
    obj: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    kind = obj.get("kind")
    if kind == "Pod":
        metadata = obj.get("metadata", {})
        spec = obj.get("spec", {})
    elif kind in {"Deployment", "Job", "StatefulSet"}:
        metadata = obj.get("metadata", {})
        spec = obj.get("spec", {}).get("template", {}).get("spec", {})
    else:
        return None, None, None

    annotations = metadata.get("annotations") or {}
    raw_annotation = annotations.get(XAVIER_CONFIG_ANNOTATION)
    if raw_annotation is None:
        return metadata, spec, None
    return metadata, spec, _load_annotation_config(raw_annotation, strict=strict)


def get_template_metadata(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("kind") in {"Deployment", "Job", "StatefulSet"}:
        template = obj.setdefault("spec", {}).setdefault("template", {})
        return template.setdefault("metadata", {})
    return None


def kubernetes_object_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return copy.deepcopy(obj)
    return client.ApiClient().sanitize_for_serialization(obj)


def env_vars_to_dict(container: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for env_var in container.get("env", []) or []:
        name = env_var.get("name")
        value = env_var.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name] = value
    return result


def get_env_var(container: dict[str, Any], name: str) -> str | None:
    for env_var in container.get("env", []) or []:
        if env_var.get("name") == name and "value" in env_var:
            return env_var["value"]
    return None


def set_env_var_if_not_exists(container: dict[str, Any], name: str, value: Any) -> bool:
    env_vars = container.setdefault("env", [])
    for env_var in env_vars:
        if env_var.get("name") == name:
            return False
    env_vars.append({"name": name, "value": str(value)})
    return True


def add_configmap_volume(spec: dict[str, Any], cm_name: str) -> bool:
    volumes = spec.setdefault("volumes", [])
    for volume in volumes:
        if volume.get("name") == XAVIER_CONFIG_VOLUME_NAME:
            return False
    volumes.append({"name": XAVIER_CONFIG_VOLUME_NAME, "configMap": {"name": cm_name}})
    return True


def add_volume_mounts_to_container(container: dict[str, Any]) -> bool:
    mounts = container.setdefault("volumeMounts", [])
    for mount in mounts:
        if mount.get("name") == XAVIER_CONFIG_VOLUME_NAME:
            return False
    mounts.append(
        {
            "name": XAVIER_CONFIG_VOLUME_NAME,
            "mountPath": XAVIER_CONFIG_MOUNT_PATH,
            "readOnly": True,
        }
    )
    return True


def serverlabelkey() -> str:
    return SERVER_SELECTOR_LABEL


def serverlabelval(name: str) -> str:
    return f"{name}-remote-server"


def getserverlabel(name: str) -> str:
    return f"{serverlabelkey()}={serverlabelval(name)}"


def _all_containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(spec.get("containers", []) or []), *(spec.get("initContainers", []) or [])]


def _is_parent_labeled_pod(metadata: dict[str, Any]) -> bool:
    labels = metadata.get("labels") or {}
    return bool(labels.get(XAVIER_PARENT_NAME_LABEL))


def _is_opted_root_workload(metadata: dict[str, Any]) -> bool:
    annotations = metadata.get("annotations") or {}
    return XAVIER_CONFIG_ANNOTATION in annotations


def _has_xavier_label(metadata: dict[str, Any]) -> bool:
    labels = metadata.get("labels") or {}
    return labels.get(XAVIER_LABEL) in ("true", "True", "TRUE", "1")


def _has_remoterport_env(spec: dict[str, Any]) -> bool:
    return any(get_env_var(container, "REMOTERPORT") is not None for container in _all_containers(spec))


def _is_fully_opted_root_workload(metadata: dict[str, Any], spec: dict[str, Any]) -> bool:
    """Require all three opt-in signals from the contract (annotation, label, REMOTERPORT).

    The admission webhook is only ever invoked for labeled objects because the
    MutatingWebhookConfiguration's objectSelector filters CREATE requests by the
    `xavier` label at the Kubernetes API server -- so `_is_opted_root_workload`
    (annotation only) is sufficient there. Reconciliation is a separate watch loop
    with no such selector, so it must check all three signals itself before
    creating a server Deployment for an object.
    """
    return _is_opted_root_workload(metadata) and _has_xavier_label(metadata) and _has_remoterport_env(spec)


def _has_xavier_env(container: dict[str, Any]) -> bool:
    return get_env_var(container, "XAVIER_CONTAINER") == "true"


# env var names the controller itself injects onto the client (see _add_client_env
# below); these are offload-protocol metadata, not client-authored data, so they
# always transfer to the generated server regardless of remoteableenv
_PROTOCOL_ENV_NAMES = {
    "REMOTER_CONFIG",
    "CONFIGFROMKUBE",
    "SERVERLABEL",
    "XAVIER_CONTAINER",
    "STAGE_NAME",
    "PERCLIENTSERVERLABEL",
}


def _add_client_env(container: dict[str, Any], server_label: str, *, perclient_label: str | None) -> bool:
    changed = False
    changed |= set_env_var_if_not_exists(container, "REMOTER_CONFIG", REMOTE_CONFIG_PATH)
    changed |= set_env_var_if_not_exists(container, "CONFIGFROMKUBE", "true")
    changed |= set_env_var_if_not_exists(container, "SERVERLABEL", server_label)
    changed |= set_env_var_if_not_exists(container, "XAVIER_CONTAINER", "true")
    changed |= set_env_var_if_not_exists(container, "STAGE_NAME", "client")
    if perclient_label is not None:
        changed |= set_env_var_if_not_exists(container, "PERCLIENTSERVERLABEL", perclient_label)
    return changed


def DoMutate(obj: dict[str, Any], objOrig: dict[str, Any] | None = None, *, strict: bool = False) -> bool:
    kind = obj.get("kind")
    if kind not in SUPPORTED_KINDS:
        return False

    metadata, spec, xaviercfg = get_metadata_spec(obj, strict=strict)
    if metadata is None or spec is None:
        return False

    if _is_parent_labeled_pod(metadata):
        changed = False
        for container in _all_containers(spec):
            if _has_xavier_env(container):
                pod_name = metadata.get("name")
                perclient_label = getserverlabel(pod_name) if pod_name else "unknown"
                changed |= set_env_var_if_not_exists(container, "PERCLIENTSERVERLABEL", perclient_label)
        return changed

    if xaviercfg is None:
        return False

    changed = False
    template_metadata = get_template_metadata(obj)
    if template_metadata is not None:
        labels = template_metadata.setdefault("labels", {})
        if labels.get(XAVIER_LABEL) != "true":
            labels[XAVIER_LABEL] = "true"
            changed = True
        if labels.get(XAVIER_PARENT_KIND_LABEL) != kind:
            labels[XAVIER_PARENT_KIND_LABEL] = kind
            changed = True
        parent_name = metadata.get("name", "")
        if labels.get(XAVIER_PARENT_NAME_LABEL) != parent_name:
            labels[XAVIER_PARENT_NAME_LABEL] = parent_name
            changed = True

    changed |= add_configmap_volume(spec, xaviercfg["remoteablecm"])

    remoteable_containers = set(xaviercfg.get("remoteableconts", []) or [])
    workload_name = metadata.get("name", "unknown")
    perclient_label = getserverlabel(workload_name) if kind == "Pod" else None
    server_label = getserverlabel(workload_name)
    for container in _all_containers(spec):
        if remoteable_containers and container.get("name") not in remoteable_containers:
            continue
        changed |= add_volume_mounts_to_container(container)
        changed |= _add_client_env(container, server_label, perclient_label=perclient_label)
    return changed


def getparam(xavierconfig: dict[str, Any], stage: str, param: str) -> Any:
    for stageobj in xavierconfig.get("serverstages", []):
        if stageobj.get("name", "") == stage and param in stageobj:
            return stageobj[param]
    return xavierconfig.get(param)


def get_xavier_container(spec: dict[str, Any]) -> dict[str, Any] | None:
    for container in _all_containers(spec):
        if _has_xavier_env(container):
            return container
    return None


_MAX_K8S_LABEL_LENGTH = 63


def _deployment_name(metadata: dict[str, Any], stage: str) -> str:
    deployment_name = f"{metadata['name']}-remote-server"
    if stage:
        deployment_name = f"{deployment_name}-{stage}"
    if len(deployment_name) <= _MAX_K8S_LABEL_LENGTH:
        return deployment_name
    # Kubernetes object names allow up to 253 characters, but this value is also
    # used as a label value (63-character limit) -- truncate the human-readable
    # prefix and append a short hash of the full name so two names that collide
    # after truncation don't collide with each other too.
    digest = hashlib.sha256(deployment_name.encode()).hexdigest()[:8]
    prefix_len = _MAX_K8S_LABEL_LENGTH - len(digest) - 1
    return f"{deployment_name[:prefix_len].rstrip('-')}-{digest}"


def _api_version_for_owner(owner_obj: dict[str, Any]) -> str:
    api_version = owner_obj.get("apiVersion") or owner_obj.get("api_version")
    if isinstance(api_version, str) and api_version:
        return api_version
    if owner_obj.get("kind") in {"Deployment", "StatefulSet"}:
        return "apps/v1"
    if owner_obj.get("kind") == "Job":
        return "batch/v1"
    return "v1"


def add_owner_reference(deployment: dict[str, Any], owner_obj: dict[str, Any]) -> None:
    owner_metadata = owner_obj.get("metadata", {})
    uid = owner_metadata.get("uid")
    if not uid:
        return
    deployment.setdefault("metadata", {})["ownerReferences"] = [
        {
            "apiVersion": _api_version_for_owner(owner_obj),
            "kind": owner_obj["kind"],
            "name": owner_metadata["name"],
            "uid": uid,
            "blockOwnerDeletion": True,
        }
    ]


def _volume_lookup(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {volume.get("name"): volume for volume in spec.get("volumes", []) or [] if volume.get("name")}


def _allowed_server_host_paths() -> set[str]:
    raw = os.getenv(ALLOWED_SERVER_HOST_PATHS_ENV, "[]")
    try:
        configured_paths = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise XavierConfigError(f"{ALLOWED_SERVER_HOST_PATHS_ENV} must be a JSON list of paths") from exc
    if not isinstance(configured_paths, list) or not all(
        isinstance(path, str) and path.startswith("/") for path in configured_paths
    ):
        raise XavierConfigError(f"{ALLOWED_SERVER_HOST_PATHS_ENV} must be a JSON list of absolute paths")
    return set(configured_paths)


def _volume_is_allowed_for_server(volume: dict[str, Any], allowed_host_paths: set[str]) -> bool:
    if "hostPath" in volume:
        host_path = volume.get("hostPath")
        return isinstance(host_path, dict) and host_path.get("path") in allowed_host_paths
    if "projected" in volume:
        # a projected volume can combine a "secret" source with otherwise-safe ones
        # (configMap, downwardAPI, serviceAccountToken); reject it if it does, same
        # as a bare "secret" volume
        sources = volume.get("projected", {}).get("sources", []) or []
        return not any(isinstance(source, dict) and "secret" in source for source in sources)
    return any(key in volume for key in ALLOWED_SERVER_VOLUME_TYPES)


def copy_allowed_volumes_and_mounts(
    destination_spec: dict[str, Any],
    source_spec: dict[str, Any],
    from_container: dict[str, Any],
) -> None:
    source_volumes = _volume_lookup(source_spec)
    allowed_host_paths = _allowed_server_host_paths()
    mount_names = {mount.get("name") for mount in from_container.get("volumeMounts", []) or [] if mount.get("name")}
    destination_volumes = destination_spec.setdefault("volumes", [])
    existing_volume_names = {volume.get("name") for volume in destination_volumes}
    for mount_name in mount_names:
        source_volume = source_volumes.get(mount_name)
        if source_volume is None or not _volume_is_allowed_for_server(source_volume, allowed_host_paths):
            continue
        if mount_name not in existing_volume_names:
            destination_volumes.append(copy.deepcopy(source_volume))
            existing_volume_names.add(mount_name)

    destination_container = destination_spec["containers"][0]
    destination_mounts = destination_container.setdefault("volumeMounts", [])
    existing_mount_names = {mount.get("name") for mount in destination_mounts}
    for mount in from_container.get("volumeMounts", []) or []:
        mount_name = mount.get("name")
        if mount_name not in existing_volume_names or mount_name in existing_mount_names:
            continue
        destination_mounts.append(copy.deepcopy(mount))
        existing_mount_names.add(mount_name)


def _merge_env_lists(*env_lists: list[dict[str, str]] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for env_list in env_lists:
        if env_list is None:
            continue
        for env_var in env_list:
            merged[env_var["name"]] = env_var["value"]
    return merged


def _append_value_from_envs(
    destination_container: dict[str, Any], source_container: dict[str, Any], allowed_names: set[str]
) -> None:
    existing = {env.get("name") for env in destination_container.get("env", []) or []}
    for env_var in source_container.get("env", []) or []:
        name = env_var.get("name")
        if name in existing or name not in allowed_names:
            continue
        if "valueFrom" in env_var:
            destination_container.setdefault("env", []).append(copy.deepcopy(env_var))
            existing.add(name)


def create_server_deployment_spec(
    metadata: dict[str, Any],
    spec: dict[str, Any],
    xavierconfig: dict[str, Any],
    stage: str,
    obj: dict[str, Any],
) -> dict[str, Any] | None:
    if getparam(xavierconfig, stage, "noserverdeployment") is True:
        return None

    xavier_container = get_xavier_container(spec)
    if xavier_container is None:
        return None

    serverimage = getparam(xavierconfig, stage, "serverimage") or xavier_container.get("image")
    if not serverimage:
        return None

    replicas = getparam(xavierconfig, stage, "serverreplicas") or 1
    deployment_name = _deployment_name(metadata, stage)
    namespace = metadata.get("namespace", "default")
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": deployment_name,
            "namespace": namespace,
            "labels": {
                "app": deployment_name,
                XAVIER_DEPLOYMENT_LABEL: "true",
            },
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": deployment_name}},
            "template": {
                "metadata": {
                    "labels": {
                        "app": deployment_name,
                        SERVER_SELECTOR_LABEL: deployment_name,
                    }
                },
                "spec": {
                    "terminationGracePeriodSeconds": 0,
                    "containers": [
                        {
                            "name": "remote-server",
                            "image": serverimage,
                            "command": ["python3", "-m", "remoter.autoremote"],
                            "readinessProbe": copy.deepcopy(READINESS_PROBE),
                        }
                    ],
                },
            },
        },
    }

    if spec.get("serviceAccountName"):
        deployment["spec"]["template"]["spec"]["serviceAccountName"] = spec["serviceAccountName"]
    if spec.get("imagePullSecrets"):
        deployment["spec"]["template"]["spec"]["imagePullSecrets"] = copy.deepcopy(spec["imagePullSecrets"])
    if spec.get("runtimeClassName"):
        deployment["spec"]["template"]["spec"]["runtimeClassName"] = spec["runtimeClassName"]

    copy_allowed_volumes_and_mounts(deployment["spec"]["template"]["spec"], spec, xavier_container)

    container = deployment["spec"]["template"]["spec"]["containers"][0]
    if "imagePullPolicy" in xavier_container:
        container["imagePullPolicy"] = xavier_container["imagePullPolicy"]
    # the client's env is not trusted by default -- forwarding everything (including
    # hardcoded credentials) to a separately specified server image/node would
    # violate least privilege, so only offload-protocol fields and explicitly
    # allow-listed names (remoteableenv) transfer
    allowed_env_names = set(getparam(xavierconfig, stage, "remoteableenv") or []) | _PROTOCOL_ENV_NAMES
    env_dict = {name: value for name, value in env_vars_to_dict(xavier_container).items() if name in allowed_env_names}
    env_dict.update(_merge_env_lists(xavierconfig.get("env"), getparam(xavierconfig, stage, "env")))
    env_dict.update(
        {
            "SERVER": "true",
            "DEPLOYMENT_NAME": deployment_name,
            "STAGE_NAME": stage,
            "WRITE_READY_MESSAGE": "/ready.txt:Server ready",
            "REMOTER_CONFIG": REMOTE_CONFIG_PATH,
        }
    )
    if env_dict.get("PERCLIENTSERVERLABEL") == "unknown":
        # recompute rather than strip "-{stage}" off deployment_name: once
        # _deployment_name truncates+hashes for length, that suffix isn't there
        base_deployment_name = _deployment_name(metadata, "")
        env_dict["PERCLIENTSERVERLABEL"] = f"{serverlabelkey()}={base_deployment_name}"
    container["env"] = [{"name": name, "value": value} for name, value in env_dict.items()]
    _append_value_from_envs(container, xavier_container, allowed_env_names)

    node_selector = getparam(xavierconfig, stage, "nodeSelector")
    if node_selector is not None:
        deployment["spec"]["template"]["spec"]["nodeSelector"] = copy.deepcopy(node_selector)

    resources = getparam(xavierconfig, stage, "resources")
    if resources is not None:
        container["resources"] = copy.deepcopy(resources)

    security_context = getparam(xavierconfig, stage, "securityContext")
    if security_context is not None:
        container["securityContext"] = copy.deepcopy(security_context)

    add_owner_reference(deployment, obj)
    return deployment


def get_metadata_spec_parent(
    obj: dict[str, Any],
    apps_api: client.AppsV1Api,
    batch_api: client.BatchV1Api,
    core_api: client.CoreV1Api | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    del core_api
    if obj.get("kind") != "Pod":
        return None, None, None
    metadata = obj.get("metadata", {})
    labels = metadata.get("labels") or {}
    parent_name = labels.get(XAVIER_PARENT_NAME_LABEL)
    parent_kind = labels.get(XAVIER_PARENT_KIND_LABEL)
    if not parent_name or not parent_kind:
        return None, None, None
    namespace = metadata.get("namespace", "default")
    kind = str(parent_kind).lower()
    if kind == "deployment":
        parent_obj = apps_api.read_namespaced_deployment(name=parent_name, namespace=namespace)
    elif kind == "job":
        parent_obj = batch_api.read_namespaced_job(name=parent_name, namespace=namespace)
    elif kind == "statefulset":
        parent_obj = apps_api.read_namespaced_stateful_set(name=parent_name, namespace=namespace)
    else:
        return None, None, None
    return get_metadata_spec(kubernetes_object_to_dict(parent_obj), strict=True)


def merge_configmap_config(
    core_api: client.CoreV1Api,
    xaviercfg: dict[str, Any],
    namespace: str,
) -> dict[str, Any]:
    configmap = core_api.read_namespaced_config_map(name=xaviercfg["remoteablecm"], namespace=namespace)
    configmap_raw = configmap.data.get("remote.yaml", "{}") if configmap.data else "{}"
    configmap_cfg = validate_xavier_config(
        _safe_yaml_mapping(configmap_raw, source=f"ConfigMap {namespace}/{xaviercfg['remoteablecm']} remote.yaml"),
        source=f"ConfigMap {namespace}/{xaviercfg['remoteablecm']} remote.yaml",
        require_remoteablecm=False,
        materialize_default_stage=False,
    )
    merged = copy.deepcopy(configmap_cfg)
    for key, value in xaviercfg.items():
        if key != "remoteablecm":
            if key == "env":
                merged_env = _merge_env_lists(merged.get("env"), value)
                merged["env"] = [{"name": name, "value": env_value} for name, env_value in merged_env.items()]
            else:
                merged[key] = copy.deepcopy(value)
    merged["remoteablecm"] = xaviercfg["remoteablecm"]
    return validate_xavier_config(merged, source="merged xavier config", require_remoteablecm=True)


def build_desired_server_deployments(
    obj: dict[str, Any],
    *,
    core_api: client.CoreV1Api,
    apps_api: client.AppsV1Api,
    batch_api: client.BatchV1Api,
) -> dict[str, dict[str, Any] | None]:
    desired: dict[str, dict[str, Any] | None] = {}
    metadata, spec, xaviercfg = get_metadata_spec(obj, strict=True)
    if (
        metadata is not None
        and spec is not None
        and xaviercfg is not None
        and not _is_parent_labeled_pod(metadata)
        and _is_fully_opted_root_workload(metadata, spec)
    ):
        xavierconfig = merge_configmap_config(core_api, xaviercfg, metadata.get("namespace", "default"))
        for stageobj in xavierconfig.get("serverstages", []):
            if stageobj.get("perclient", False) and obj.get("kind") != "Pod":
                continue
            stage_name = stageobj.get("name", "")
            deployment_name = _deployment_name(metadata, stage_name)
            desired[deployment_name] = create_server_deployment_spec(metadata, spec, xavierconfig, stage_name, obj)
    if obj.get("kind") == "Pod":
        _, _, parent_cfg = get_metadata_spec_parent(obj, apps_api, batch_api, core_api)
        if metadata is not None and spec is not None and parent_cfg is not None:
            xavierconfig = merge_configmap_config(core_api, parent_cfg, metadata.get("namespace", "default"))
            for stageobj in xavierconfig.get("serverstages", []):
                if not stageobj.get("perclient", False):
                    continue
                stage_name = stageobj.get("name", "")
                deployment_name = _deployment_name(metadata, stage_name)
                desired[deployment_name] = create_server_deployment_spec(metadata, spec, xavierconfig, stage_name, obj)
    return desired


def _normalize_deployment_for_compare(deployment_obj: Any) -> dict[str, Any]:
    deployment = kubernetes_object_to_dict(deployment_obj)
    metadata = deployment.setdefault("metadata", {})
    metadata.pop("creationTimestamp", None)
    metadata.pop("resourceVersion", None)
    metadata.pop("uid", None)
    metadata.pop("generation", None)
    metadata.pop("managedFields", None)
    deployment.pop("status", None)
    return deployment


def _is_subset(desired: Any, actual: Any) -> bool:
    """True if every key/value `desired` specifies is present with an equal value
    in `actual`. Kubernetes populates many fields we never set (spec.strategy,
    revisionHistoryLimit, progressDeadlineSeconds, restartPolicy, container
    terminationMessagePolicy, etc.); comparing for exact equality against the
    stored object would never match, forcing a patch -- and therefore a MODIFIED
    watch event that re-triggers reconciliation -- on every single pass.
    """
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and _is_subset(value, actual[key]) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(desired) == len(actual)
            and all(_is_subset(d, a) for d, a in zip(desired, actual, strict=True))
        )
    return desired == actual


def reconcile_named_server_deployment(
    apps_api: client.AppsV1Api,
    *,
    namespace: str,
    deployment_name: str,
    desired_spec: dict[str, Any] | None,
) -> str:
    try:
        existing = apps_api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    except client.exceptions.ApiException as exc:
        if exc.status != 404:
            raise
        if desired_spec is None:
            return "absent"
        apps_api.create_namespaced_deployment(namespace=namespace, body=desired_spec)
        return "created"

    if desired_spec is None:
        apps_api.delete_namespaced_deployment(name=deployment_name, namespace=namespace)
        return "deleted"

    if _is_subset(_normalize_deployment_for_compare(desired_spec), _normalize_deployment_for_compare(existing)):
        return "unchanged"
    apps_api.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=desired_spec)
    return "patched"


def _orphaned_server_deployment_names(
    apps_api: client.AppsV1Api,
    *,
    namespace: str,
    parent_name: str,
    parent_uid: str,
    desired_names: set[str],
) -> list[str]:
    """Names of previously-created server Deployments for `parent_name` that are no
    longer in `desired_names` -- e.g. because a stage was renamed or removed from
    serverstages. Without this, reconcile_object only ever touches the current
    desired set and an old server Deployment (and its GPU allocation) is never
    cleaned up while the parent workload still exists.

    Only a Deployment whose ownerReference actually points back to `parent_uid` is
    treated as an orphan: names are not unique across kinds, so an unrelated
    object (e.g. a Pod) can legally share a name with an opted-in Deployment that
    owns an active, in-use `<name>-remote-server*` Deployment.
    """
    if not parent_name or not parent_uid:
        return []
    prefix = f"{parent_name}-remote-server"
    existing = apps_api.list_namespaced_deployment(
        namespace=namespace,
        label_selector=f"{XAVIER_DEPLOYMENT_LABEL}=true",
    )
    orphans = []
    for item in existing.items:
        name = item.metadata.name
        if not name.startswith(prefix) or name in desired_names:
            continue
        owners = getattr(item.metadata, "owner_references", None) or []
        if any(getattr(owner, "uid", None) == parent_uid for owner in owners):
            orphans.append(name)
    return orphans


def reconcile_object(
    obj: dict[str, Any],
    *,
    core_api: client.CoreV1Api,
    apps_api: client.AppsV1Api,
    batch_api: client.BatchV1Api,
) -> dict[str, str]:
    if obj.get("kind") not in SUPPORTED_KINDS:
        return {}
    desired_deployments = build_desired_server_deployments(
        obj,
        core_api=core_api,
        apps_api=apps_api,
        batch_api=batch_api,
    )
    namespace = obj.get("metadata", {}).get("namespace", "default")
    outcomes: dict[str, str] = {}
    for deployment_name, desired_spec in desired_deployments.items():
        outcomes[deployment_name] = reconcile_named_server_deployment(
            apps_api,
            namespace=namespace,
            deployment_name=deployment_name,
            desired_spec=desired_spec,
        )

    # Orphan cleanup only makes sense -- and is only safe -- for an object that is
    # itself a fully opted-in root workload: that's the only case where this
    # object is the (sole) owner of a `<name>-remote-server*` family of
    # Deployments, and the only case where "no longer in serverstages" is a
    # meaningful signal at all.
    metadata, spec, xaviercfg = get_metadata_spec(obj, strict=False)
    is_opted_root = (
        metadata is not None
        and spec is not None
        and xaviercfg is not None
        and not _is_parent_labeled_pod(metadata)
        and _is_fully_opted_root_workload(metadata, spec)
    )
    if is_opted_root:
        orphan_names = _orphaned_server_deployment_names(
            apps_api,
            namespace=namespace,
            parent_name=metadata.get("name", ""),
            parent_uid=metadata.get("uid", ""),
            desired_names=set(desired_deployments),
        )
        for deployment_name in orphan_names:
            outcomes[deployment_name] = reconcile_named_server_deployment(
                apps_api,
                namespace=namespace,
                deployment_name=deployment_name,
                desired_spec=None,
            )
    return outcomes


def _json_pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def create_json_patch(source: Any, target: Any, path: str = "") -> list[dict[str, Any]]:
    if source == target:
        return []
    if isinstance(source, dict) and isinstance(target, dict):
        patch: list[dict[str, Any]] = []
        source_keys = set(source)
        target_keys = set(target)
        for key in sorted(source_keys - target_keys):
            patch.append({"op": "remove", "path": f"{path}/{_json_pointer_escape(key)}"})
        for key in sorted(target_keys - source_keys):
            patch.append(
                {
                    "op": "add",
                    "path": f"{path}/{_json_pointer_escape(key)}",
                    "value": copy.deepcopy(target[key]),
                }
            )
        for key in sorted(source_keys & target_keys):
            patch.extend(create_json_patch(source[key], target[key], f"{path}/{_json_pointer_escape(key)}"))
        return patch
    if isinstance(source, list) and isinstance(target, list):
        return [{"op": "replace", "path": path or "/", "value": copy.deepcopy(target)}]
    return [{"op": "replace", "path": path or "/", "value": copy.deepcopy(target)}]


def admission_response(
    *,
    uid: str,
    allowed: bool,
    patch: list[dict[str, Any]] | None = None,
    status_message: str | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": allowed,
        },
    }
    if status_message is not None:
        response["response"]["status"] = {"message": status_message}
    if patch:
        patch_bytes = json.dumps(patch, separators=(",", ":")).encode("utf-8")
        response["response"]["patchType"] = "JSONPatch"
        response["response"]["patch"] = base64.b64encode(patch_bytes).decode("ascii")
    return response


class XavierAdmissionController:
    def __init__(
        self,
        *,
        core_api: client.CoreV1Api | None = None,
        apps_api: client.AppsV1Api | None = None,
        batch_api: client.BatchV1Api | None = None,
    ) -> None:
        self.core_api = core_api
        self.apps_api = apps_api
        self.batch_api = batch_api

    def handle_admission_review(self, review: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if review.get("apiVersion") != "admission.k8s.io/v1" or review.get("kind") != "AdmissionReview":
            return _json_error("Request must be an admission.k8s.io/v1 AdmissionReview", HTTPStatus.BAD_REQUEST)

        request = review.get("request")
        if not isinstance(request, dict):
            return _json_error("AdmissionReview.request is required", HTTPStatus.BAD_REQUEST)

        uid = request.get("uid")
        if not isinstance(uid, str) or not uid:
            return _json_error("AdmissionReview.request.uid is required", HTTPStatus.BAD_REQUEST)

        operation = request.get("operation")
        obj = request.get("object")
        if not isinstance(obj, dict):
            return HTTPStatus.OK, admission_response(
                uid=uid, allowed=False, status_message="AdmissionReview.request.object must be a JSON object"
            )
        if operation not in SUPPORTED_OPERATIONS:
            return HTTPStatus.OK, admission_response(uid=uid, allowed=True)

        kind = obj.get("kind")
        if kind not in SUPPORTED_KINDS:
            return HTTPStatus.OK, admission_response(uid=uid, allowed=True)

        metadata = obj.get("metadata") or {}
        if not _is_opted_root_workload(metadata) and not _is_parent_labeled_pod(metadata):
            return HTTPStatus.OK, admission_response(uid=uid, allowed=True)

        try:
            original = copy.deepcopy(obj)
            mutated = copy.deepcopy(obj)
            changed = DoMutate(mutated, original, strict=True)
        except XavierConfigError as exc:
            return HTTPStatus.OK, admission_response(uid=uid, allowed=False, status_message=str(exc))
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception("Unhandled mutation failure")
            return HTTPStatus.OK, admission_response(
                uid=uid, allowed=False, status_message=f"Unhandled mutation failure: {exc}"
            )

        if not changed:
            return HTTPStatus.OK, admission_response(uid=uid, allowed=True)

        patch = create_json_patch(original, mutated)
        return HTTPStatus.OK, admission_response(uid=uid, allowed=True, patch=patch)

    def reconcile_object(self, obj: dict[str, Any]) -> dict[str, str]:
        if self.core_api is None or self.apps_api is None or self.batch_api is None:
            raise RuntimeError("Kubernetes clients are not configured for reconciliation")
        return reconcile_object(obj, core_api=self.core_api, apps_api=self.apps_api, batch_api=self.batch_api)


class AdmissionHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "gpu-offload-controller/1.0"

    def do_GET(self) -> None:
        if urlsplit(self.path).path not in {"/healthz", "/readyz"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send_json(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/mutate":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length must be an integer"})
            return
        body = self.rfile.read(content_length)
        try:
            review = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Request body must be valid JSON: {exc}"})
            return
        status_code, payload = self.server.controller.handle_admission_review(review)
        self._send_json(status_code, payload)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class AdmissionHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        controller: XavierAdmissionController,
        *,
        ssl_context: ssl.SSLContext | None,
    ) -> None:
        super().__init__(server_address, AdmissionHTTPRequestHandler)
        self.controller = controller
        if ssl_context is not None:
            self.socket = ssl_context.wrap_socket(self.socket, server_side=True)


def build_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


def load_kubernetes_clients(
    kubeconfig_path: str | None,
) -> tuple[client.CoreV1Api, client.AppsV1Api, client.BatchV1Api]:
    if kubeconfig_path:
        config.load_kube_config(config_file=kubeconfig_path)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api(), client.BatchV1Api()


class ReconcileRuntime:
    def __init__(self, controller: XavierAdmissionController) -> None:
        self.controller = controller
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        self.reconcile_existing_objects()
        for kind, list_fn in (
            ("Pod", self.controller.core_api.list_pod_for_all_namespaces),
            ("Deployment", self.controller.apps_api.list_deployment_for_all_namespaces),
            ("Job", self.controller.batch_api.list_job_for_all_namespaces),
            ("StatefulSet", self.controller.apps_api.list_stateful_set_for_all_namespaces),
        ):
            thread = threading.Thread(target=self._watch_kind, args=(kind, list_fn), daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self) -> None:
        self.stop_event.set()

    def reconcile_existing_objects(self) -> None:
        for list_fn in (
            self.controller.core_api.list_pod_for_all_namespaces,
            self.controller.apps_api.list_deployment_for_all_namespaces,
            self.controller.batch_api.list_job_for_all_namespaces,
            self.controller.apps_api.list_stateful_set_for_all_namespaces,
        ):
            for item in list_fn().items:
                obj = kubernetes_object_to_dict(item)
                self._reconcile_obj(obj)

    def _watch_kind(self, kind: str, list_fn: Callable[..., Any]) -> None:
        while not self.stop_event.is_set():
            watcher = watch.Watch()
            try:
                for event in watcher.stream(list_fn, timeout_seconds=30):
                    if self.stop_event.is_set():
                        watcher.stop()
                        break
                    obj = event.get("object")
                    if obj is None:
                        continue
                    event_type = event.get("type")
                    if event_type not in {"ADDED", "MODIFIED"}:
                        continue
                    obj_dict = kubernetes_object_to_dict(obj)
                    if obj_dict.get("metadata", {}).get("deletionTimestamp") is not None:
                        continue
                    self._reconcile_obj(obj_dict)
            except Exception:  # pragma: no cover - defensive path around watch loops
                logger.exception("Watch for %s failed; restarting", kind)

    def _reconcile_obj(self, obj: dict[str, Any]) -> None:
        try:
            self.controller.reconcile_object(obj)
        except XavierConfigError as exc:
            logger.warning(
                "Skipping reconcile for %s/%s: %s", obj.get("kind"), obj.get("metadata", {}).get("name"), exc
            )
        except client.exceptions.ApiException as exc:
            logger.warning(
                "Kubernetes API error while reconciling %s/%s: %s",
                obj.get("kind"),
                obj.get("metadata", {}).get("name"),
                exc,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="GPU offload Xavier-compatible admission controller")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address for the admission server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Admission server port")
    parser.add_argument("--cert-file", default=DEFAULT_TLS_CERT_PATH, help="TLS certificate path")
    parser.add_argument("--key-file", default=DEFAULT_TLS_KEY_PATH, help="TLS private key path")
    parser.add_argument("--kubeconfig", default=None, help="Optional kubeconfig path for out-of-cluster use")
    parser.add_argument(
        "--disable-reconcile", action="store_true", help="Run admission only without background reconciliation"
    )
    parser.add_argument("--disable-tls", action="store_true", help="Disable TLS for local debugging only")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s"
    )

    core_api = apps_api = batch_api = None
    runtime: ReconcileRuntime | None = None
    if not args.disable_reconcile:
        core_api, apps_api, batch_api = load_kubernetes_clients(args.kubeconfig)
    controller = XavierAdmissionController(core_api=core_api, apps_api=apps_api, batch_api=batch_api)
    if not args.disable_reconcile:
        runtime = ReconcileRuntime(controller)
        # Run the initial cluster-wide sync and watch loops in the background so the
        # webhook server below can start accepting requests immediately instead of
        # only after the sync completes -- otherwise Kubernetes can route admission
        # requests (or pass a readiness probe) to this Pod before anything is
        # actually listening for them.
        threading.Thread(target=runtime.start, name="reconcile-runtime", daemon=True).start()

    ssl_context = None if args.disable_tls else build_ssl_context(args.cert_file, args.key_file)
    server = AdmissionHTTPServer((args.host, args.port), controller, ssl_context=ssl_context)

    def _shutdown_handler(signum: int, frame: Any) -> None:
        del signum, frame
        server.shutdown()

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    try:
        server.serve_forever()
    finally:
        if runtime is not None:
            runtime.stop()
        server.server_close()


__all__ = [
    "READINESS_PROBE",
    "REMOTE_CONFIG_PATH",
    "AdmissionHTTPServer",
    "DoMutate",
    "XavierAdmissionController",
    "XavierConfigError",
    "admission_response",
    "build_desired_server_deployments",
    "create_json_patch",
    "create_server_deployment_spec",
    "get_metadata_spec",
    "get_metadata_spec_parent",
    "getserverlabel",
    "merge_configmap_config",
    "reconcile_named_server_deployment",
    "reconcile_object",
    "validate_xavier_config",
]


if __name__ == "__main__":
    main()
