from __future__ import annotations

import copy
import os
import threading
import traceback

import yaml
from kubernetes import client, config, watch

from . import rmtconfig
from .k8sutils_compat import utils


def create_client(config_file) -> client.CoreV1Api:
    if config_file:
        config.load_kube_config(config_file)
    else:
        try:
            # use default kubeconfig
            config.load_kube_config()
        except Exception:
            # try in-cluster config
            config.load_incluster_config()
    return client.CoreV1Api()


def get_pod_remoter_port(pod: client.V1Pod) -> str:
    if pod.metadata and pod.metadata.labels and "remoterPort" in pod.metadata.labels:
        return pod.metadata.labels["remoterPort"]
    # now check envvars for REMOTERPORT
    if pod.spec and pod.spec.containers:
        for container in pod.spec.containers:
            if container.env:
                for envvar in container.env:
                    if envvar.name == "REMOTERPORT":
                        return envvar.value
    return "9000"  # default port


def get_pod_socket_path(pod: client.V1Pod) -> str | None:
    ret = None
    if pod.metadata and pod.metadata.labels and "remoterSock" in pod.metadata.labels:
        ret = pod.metadata.labels["remoterSock"]
    # now check envvars for REMOTERSOCK
    if pod.spec and pod.spec.containers:
        for container in pod.spec.containers:
            if container.env:
                for envvar in container.env:
                    if envvar.name == "REMOTERSOCK":
                        ret = envvar.value
    # if ret is directory or does not end in .sock, assume it specifies directory and generate socket file name as remotersock/podname-poduid.sock  # noqa: E501 vendored from microsoft/xavier, not refactored
    if (
        ret
        and (os.path.isdir(ret) or not ret.endswith(".sock"))
        and pod.metadata
        and pod.metadata.namespace
        and pod.metadata.name
        and pod.metadata.uid
    ):
        ret = utils.socketpath(ret, pod.metadata.namespace, pod.metadata.name, pod.metadata.uid)
    return ret


def on_pod(event_type: str, pod: client.V1Pod, lock: threading.Lock, keys: dict, locations: dict, configfile: str):
    add = False
    remove = False
    if event_type == "DELETED" or (pod.metadata and pod.metadata.deletion_timestamp is not None):
        remove = True
    assert pod.status is not None, "Pod status is None"
    assert pod.metadata is not None, "Pod metadata is None"
    if (event_type == "ADDED" or event_type == "MODIFIED") and not remove:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
        # check if pod is running
        if pod.status and pod.status.phase == "Running":
            # also check if containers are ready
            all_ready = True
            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if not cs.ready:
                        all_ready = False
                        break
            if all_ready:
                print(f"Pod {pod.metadata.name} is running and ready at {pod.status.pod_ip}")
                add = True
            else:
                print(f"Pod {pod.metadata.name} is running but not ready yet")

    # convert pod labels k=v list
    labelskv = []
    if pod.metadata and pod.metadata.labels:
        for k, v in pod.metadata.labels.items():
            labelskv.append(f"{k}={v}")

    with lock:
        for serverlabel in keys:
            print("Checking pod labels:", labelskv, "for serverlabel", serverlabel)
            if serverlabel in labelskv:
                if serverlabel not in locations:
                    locations[serverlabel] = set()
                loc = f"{pod.status.pod_ip}:{get_pod_remoter_port(pod)}"
                # if NODE_NAME exists check if other pod on same node
                if os.environ.get("NODE_NAME", None) and pod.spec and pod.spec.node_name == os.environ["NODE_NAME"]:
                    # also check if REMOTERSOCK envvar exists on other pod and if it does use unix sockets
                    socket_path = get_pod_socket_path(pod)
                    if socket_path:
                        loc = f"unix:{socket_path}"
                if add:
                    locations[serverlabel].add(loc)
                    print("Added location for serverlabel", serverlabel, ":", loc)
                elif remove:
                    locations[serverlabel].discard(loc)
                    print("Removed location for serverlabel", serverlabel, ":", loc)
        # update config
        config = createconfig(keys, locations)
        print("Updated config:", config)
        rmtconfig.update_config_file(configfile, config)


def watch_pods(
    v1api: client.CoreV1Api,
    resource_version: str,
    namespace: str,
    on_pod,
    lock: threading.Lock,
    keys: dict,
    locations: dict,
    configfile: str,
):
    while True:
        try:
            w = watch.Watch()
            if namespace:
                for event in w.stream(
                    v1api.list_namespaced_pod, namespace=namespace, resource_version=resource_version
                ):
                    pod = event["object"]
                    etype = event["type"]
                    on_pod(etype, pod, lock, keys, locations, configfile)
            else:
                for event in w.stream(v1api.list_pod_for_all_namespaces, resource_version=resource_version):
                    pod = event["object"]
                    etype = event["type"]
                    on_pod(etype, pod, lock, keys, locations, configfile)
        except Exception as e:
            print(f"watch_pods exception: {e}\n{traceback.format_exc()}")
            pass


def createconfig(keys: dict, locations: dict) -> dict:
    config = {}
    for serverlabel, keyset in keys.items():
        for key in keyset:
            config[key] = {"locations": {}}
            if locations.get(serverlabel):
                for loc in locations[serverlabel]:
                    config[key]["locations"][loc] = 1.0 / len(locations[serverlabel])
    return config


def getparam(key, config, localconfig, default):
    ret = default
    if key in config:
        ret = config[key]
    if localconfig and key in localconfig:
        ret = localconfig[key]
    return ret


def getdictparam(key, config, localconfig):
    ret = {}
    # print(key, localconfig)
    if key in config:
        ret = config[key]
    if localconfig and key in localconfig:
        ret.update(localconfig[key])
    return ret


def isremotable(config, localconfig) -> bool:
    if getparam("remoteableserver", config, localconfig, False):
        return True
    remoteableon = getdictparam("remoteableon", config, localconfig)
    # print(remoteableon)
    stage_name = os.environ.get("STAGE_NAME", "")
    print("Stage name:", stage_name)
    for loc, val in remoteableon.items():
        if loc == stage_name:
            return val
    return False


# loc specifies stagename of remotelocation
# SERVERLABEL and PERCLIENTSERVERLABEL of form "apprmt=<name>-remote-server"
# if loc is client, then remove -remote-server-client suffix
def getserverlabel(config, loc) -> str:
    # find if stage is perclient or not
    ret = None
    perclientlabel = os.environ.get("PERCLIENTSERVERLABEL", "")
    if perclientlabel == "unknown":
        # only for client pods generated by deployment -- for these use hostname to generate perclient label
        podname = os.environ.get("HOSTNAME", "unknownpod")
        perclientlabel = f"apprmt={podname}-remote-server"
    for stageobj in config.get("serverstages", []):
        if stageobj["name"] == loc:
            if stageobj.get("perclient", False):
                ret = f"{perclientlabel}-{loc}"  # for perclient, use pod-specific serverlabel
            else:
                ret = f"{os.environ['SERVERLABEL']}-{loc}"  # for non-perclient, use common serverlabel which must exist
            break
    if ret:
        if loc == "client" and ret.endswith("-remote-server-client"):
            ret = ret[: -len("-remote-server-client")]
        return ret
    raise ValueError(f"Location {loc} not found in serverstages")


# serverlabel only written if remoteloc is specified
def rewrite_taskconfig(taskconfig: str):
    isserver = os.environ.get("SERVER", "false").lower() in ["true", "1", "yes"]
    with open(taskconfig) as f:
        cfg = yaml.safe_load(f)

    # rewrite following fields: remoteableserver, remoteoableon, remoteloc
    cfgnew = copy.deepcopy(cfg)
    cfgnew.pop("remoteloc", None)
    cfgnew.pop("remoteableserver", None)
    cfgnew.pop("remoteableon", None)
    for func in cfgnew.get("remotefuncs", []):
        for target_path, params in func.items():  # noqa: B007 vendored from microsoft/xavier, not refactored
            # functions are always remoteable
            if "remoteloc" in params:
                serverlabel = getserverlabel(cfg, params["remoteloc"])
                params["serverlabel"] = serverlabel
                params.pop("remoteloc", None)
            params.pop("remoteableserver", None)
            params.pop("remoteableon", None)
    for cls in cfgnew.get("remoteclasses", []):
        for target_path, params in cls.items():  # noqa: B007 vendored from microsoft/xavier, not refactored
            # classes are remoteable based on config
            if "remoteloc" in params:
                serverlabel = getserverlabel(cfg, params["remoteloc"])
                params["serverlabel"] = serverlabel
                params.pop("remoteloc", None)
            if isserver:
                remoteable = isremotable(cfg, params)
                params["remoteableserver"] = remoteable
            else:
                params.pop("remoteableserver", None)
            params.pop("remoteableon", None)

    # Generated state must be outside the read-only ConfigMap mount.
    newtaskconfig = "/tmp/remoter_rewritten.yaml"
    with open(newtaskconfig, "w") as f:
        yaml.safe_dump(cfgnew, f)

    return cfgnew, newtaskconfig


def get_keys(cfg):
    # default server label
    defserverlabel = os.environ.get("SERVERLABEL", "remoteserver=true")
    keys = {}
    for func in cfg.get("remotefuncs", []):
        for target_path, params in func.items():
            taskkey = params.get("taskkey")
            serverlabel = params.get("serverlabel", defserverlabel)
            if serverlabel not in keys:
                keys[serverlabel] = set()
            if taskkey:
                keys[serverlabel].add(taskkey)
            else:
                keys[serverlabel].add(target_path)
    for cls in cfg.get("remoteclasses", []):
        for target_path, params in cls.items():
            key = params.get("classkey")
            serverlabel = params.get("serverlabel", defserverlabel)
            if serverlabel not in keys:
                keys[serverlabel] = set()
            if key:
                keys[serverlabel].add(key)
            else:
                keys[serverlabel].add(target_path)
    return keys


initdone = False
initlock = threading.Lock()
g_cfg = {}
g_remoteconfig = ""


# keys is a dictionary of serverlabel -> set of (funcnames, classkeys) which utilize the serverlabel
def rmtconfigkube_init(taskconfig, locconfigfile) -> tuple[dict, str]:
    with initlock:
        global initdone, g_cfg, g_remoteconfig
        if initdone:
            return g_cfg, g_remoteconfig
        initdone = True

    cfg, newremoteconfig = rewrite_taskconfig(taskconfig)
    g_cfg = cfg
    g_remoteconfig = newremoteconfig

    # collect all keys into single dictionary of serverlabel -> set of taskkeys
    keys = get_keys(cfg)
    print("Server labels and keys:", keys)

    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            namespace = f.read().strip()
    except Exception as e:
        print(f"Error reading namespace: {e}")
        namespace = None

    locations = {}
    v1api = create_client(os.environ.get("KUBECONFIG", None))

    # watch for pods
    lock = threading.Lock()

    # add existing pods
    if namespace:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
        pods = v1api.list_namespaced_pod(namespace)
    else:
        pods = v1api.list_pod_for_all_namespaces()
    for pod in pods.items:
        on_pod("ADDED", pod, lock, keys, locations, locconfigfile)
    print("Initial pod locations:", locations)
    rv = pods.metadata.resource_version if pods.metadata else None

    # watch pods which have any of the serverlabels
    watch_thread = threading.Thread(
        target=watch_pods, args=(v1api, rv, namespace, on_pod, lock, keys, locations, locconfigfile), daemon=True
    )
    watch_thread.start()

    print("rmtconfigkube initialized")
    return cfg, newremoteconfig
