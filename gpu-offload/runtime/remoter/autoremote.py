from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
import sys

# use by sitecustomize.py
import yaml

from . import remoter, rmtclass, rmtconfigkube
from .simplelog import initlog


def replaceenvvars(o):
    if isinstance(o, str):
        # replace ${VAR} with environment variable
        pattern = re.compile(r"\$\{([^}]+)\}")

        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return pattern.sub(replacer, o)
    elif isinstance(o, dict):
        return {replaceenvvars(k): replaceenvvars(v) for k, v in o.items()}
    elif isinstance(o, list):
        return [replaceenvvars(i) for i in o]
    elif isinstance(o, tuple):
        return tuple(replaceenvvars(i) for i in o)
    else:
        return o


def load_config(configpath) -> dict:
    try:
        with open(configpath, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            if cfg is None:
                return {}
            cfg = replaceenvvars(cfg)
            logger.info(f"Loaded config from {configpath}: {cfg}", color="green")
            return cfg
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


def get_main_script_dir():
    """
    Returns the absolute directory path of the main script
    that started the Python process.
    """
    try:
        # sys.argv[0] is the path to the main script
        main_path = os.path.abspath(sys.argv[0])
        return os.path.dirname(main_path)
    except Exception as e:
        # Fallback if something goes wrong
        print(f"Error determining main script directory: {e}")
        return None


def importmodule(mod):
    try:
        return importlib.import_module(mod)
    except Exception as e:
        # if module not found, try to import from main script directory
        main_script_dir = get_main_script_dir()
        if main_script_dir is not None:
            sys.path.insert(0, main_script_dir)
            try:
                return importlib.import_module(mod)
            except Exception as e2:
                logger.error(f"Failed to import module {mod} from main script directory {main_script_dir}: {e2}")
                raise e2
        else:
            logger.error(f"Failed to import module {mod} and could not determine main script directory: {e}")
            raise e


def apply_decorators_from_config(configpath) -> bool:
    logger.info(f"Applying remoter decorators from config file: {configpath}", color="cyan")
    cfg = load_config(configpath)
    isserver = remoter.remoterparams["server"]

    # print(f"Command line exe: {sys.argv[0]}\n{sys.argv}")
    if len(sys.argv) > 0:
        exe_path = os.path.abspath(sys.argv[0])
        if "remotescripts" in cfg:
            found = False
            for script_pattern in cfg["remotescripts"]:
                if re.match(script_pattern, exe_path):
                    found = True
                    logger.info(f"Applying remoter decorators for script {exe_path} matching pattern {script_pattern}")
                    break
            if not found:
                logger.info(
                    f"Skipping remoter decorators for script {exe_path} -- no matching pattern in remotescripts"
                )
                return False

    # convert cfg('remotefuncs', []) to dict
    funcparams = {}
    for func in cfg.get("remotefuncs", []):
        for target_path, params in func.items():
            funcparams[target_path] = params

    # do functions first since some classes may have methods which are also decorated as remotetasks
    # and we want to make sure to use the remotetask version of the method when decorating the class
    for func in cfg.get("remotefuncs", []):
        for target_path, params in func.items():
            if isserver:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                if cfg.get("stubs", {}).get(target_path, None) is not None:
                    logger.info(
                        f"Using actual function path for stub {target_path} using {cfg['stubs'][target_path]}",
                        color="cyan",
                    )
                    target_path = cfg["stubs"][target_path]  # use actual function path instead of stub

            # module_path, _, attr_name = target_path.rpartition(".")
            module_path, class_name, attr_name = target_path.split("/")
            try:
                mod = importmodule(module_path)
                if class_name:
                    cls = getattr(mod, class_name)
                    target = getattr(cls, attr_name)
                else:
                    target = getattr(mod, attr_name)
            except Exception as e:
                logger.error(f"Failed to import {target_path} (module {module_path}): {e}", color="red")
                continue

            taskkey = params.get("taskkey", remoter.default_func_key(target))
            functype = params.get("functype", "threadpooltask")
            timeout = params.get("timeout", None)

            if inspect.isfunction(target):
                new_target = remoter.createRemotedTask(target, taskkey, functype, timeout=timeout)
                if class_name:
                    setattr(cls, attr_name, new_target)
                else:
                    setattr(mod, attr_name, new_target)
                logger.info(
                    f"Decorated function {target_path} with remotetask (taskkey={taskkey}, functype={functype}, module={mod})",  # noqa: E501 vendored from microsoft/xavier, not refactored
                    color="cyan",
                )
                rmtclass.setfixedloc(target_path, funcparams)
                if funcparams[target_path].get("singleinstance", False):
                    remoter.addsingleinstancefunc(target_path)
            else:
                logger.info(f"Skipped {target_path}: not a function")

    for cls in cfg.get("remoteclasses", []):
        for target_path, params in cls.items():
            if isserver:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                if cfg.get("stubs", {}).get(target_path, None) is not None:
                    logger.info(
                        f"Using actual class path for stub {target_path} using {cfg['stubs'][target_path]}",
                        color="cyan",
                    )
                    target_path = cfg["stubs"][target_path]  # use actual class path instead of stub

            # module_path, _, attr_name = target_path.rpartition(".")
            module_path, attr_name = target_path.split("/", 1)
            try:
                mod = importmodule(module_path)
                target = getattr(mod, attr_name)
            except Exception as e:
                logger.error(f"Failed to import {target_path} (module {module_path}): {e}", color="red")
                continue

            if inspect.isclass(target):
                logger.info(f"Add to remoted classes {target_path} from module {mod}", color="cyan")
                remoter.addremotedclass(target)
                rmtclass.allowallfunctions(target, isserver)
                rmtclass.addsingleinstance(target, params)
            else:
                logger.info(f"Skipped {target_path}: not a class")

    if "stubs" in cfg and not isserver:
        logger.info("Setting stub classes from config: " + str(cfg["stubs"]))
        remoter.setstubclasses(cfg["stubs"])

    return True


logger = initlog("autoremote.log", logging.DEBUG, logging.INFO)


def start(serveronly=True):
    import os

    # print(traceback.format_stack()[0].strip()) # for debugging to see who called autoremote

    remoteconfig = os.environ.get("REMOTER_CONFIG", "remote.yaml")
    remoteconfig = os.path.abspath(remoteconfig)

    # check if remoteconfig is in writeable location, since other files have to be written in this location as well
    # if it is not in writeable location, use a temporary directory which can persist for lifetime of the process
    if not os.access(os.path.dirname(remoteconfig), os.W_OK):
        import tempfile

        tempdir = tempfile.mkdtemp()
        newremoteconfig = os.path.join(tempdir, os.path.basename(remoteconfig))
        logger.info(
            f"Remote config {remoteconfig} is not in a writeable location. Copying to temporary directory {newremoteconfig}"  # noqa: E501 vendored from microsoft/xavier, not refactored
        )
        import shutil

        shutil.copy(remoteconfig, newremoteconfig)
        remoteconfig = newremoteconfig

    cfg = load_config(remoteconfig)

    locconfigfile = cfg.get("configfile", os.environ.get("CONFIGFILE", None))
    if locconfigfile is not None and not os.path.exists(locconfigfile):
        # check for configfile in same dir as remoteconfigpath
        dir, fname = os.path.split(remoteconfig)  # noqa: RUF059 vendored from microsoft/xavier, not refactored
        locconfigfile = os.path.abspath(os.path.join(dir, locconfigfile))
    logger.info(f"Location Config file: {locconfigfile} -- Remote Task config file: {remoteconfig}")
    if cfg.get("configfromkube", False) or os.environ.get("CONFIGFROMKUBE", "false").lower() == "true":
        locconfigfile = os.path.abspath(
            os.path.join(os.path.dirname(remoteconfig), "rmtconfigkube.yaml")
        )  # override locconfigfile
        # now also initialize rmtconfigkube
        remoter.Remoter.waituntillocation = True
        if ":" in os.environ.get("WRITE_READY_MESSAGE", ""):
            file, msg = os.environ["WRITE_READY_MESSAGE"].split(":", 1)
            remoter.Remoter.writeToReadyFile = (file, msg)
        cfg, newremoteconfig = rmtconfigkube.rmtconfigkube_init(remoteconfig, locconfigfile)
    else:
        newremoteconfig = remoteconfig

    remoter.setparams(load_config(newremoteconfig))
    remoted = apply_decorators_from_config(newremoteconfig)
    if remoted:
        remoter.initRemoter(
            cfg,
            cfg.get("host", os.environ.get("REMOTERHOST", "0.0.0.0")),
            cfg.get("port", int(os.environ.get("REMOTERPORT", "9000"))),
            cfg.get("remotersock", os.environ.get("REMOTERSOCK", None)),
            cfg.get("remoteloc", os.environ.get("REMOTELOC", None)),
            cfg.get("remoteport", int(os.environ.get("REMOTEPORT", 0))),
            False,
            locconfigfile,
            # kubeconfig already handled - needs to be done prior to remoteable so that decorators can be applied correctly  # noqa: E501 vendored from microsoft/xavier, not refactored
            None,
            serveronly,
        )


if __name__ == "__main__":
    start(True)
