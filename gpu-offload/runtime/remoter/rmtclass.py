from __future__ import annotations

import logging
import os
import traceback
import types
from fnmatch import fnmatchcase

from . import remoter
from .simplelog import initlog

logger = initlog("rmtclass.log", logging.DEBUG, logging.INFO)


# not to directly be called since will get wrong uuid
def _getfromremote(x):
    # return {'x': x, 'uuid': x.uuid_rmt0bf}
    return x


def syncwithremote(x):
    rmtloc = x.rmtloc_rmt0bf
    assert not x.rmtowner_rmt0bf, "syncwithremote should be called on client stub instance"
    if x.failed_rmt0bf:
        raise RuntimeError("Remote class instance has failed")
    ret = x._getfromremote()
    newx = ret
    # newx = ret['x']
    # uuidret = ret['uuid']
    logger.debug("X newx:" + str(newx.__dict__))
    logger.debug("X old:" + str(x.__dict__))
    # assert uuidret == uuid, "Remote class UUID mismatch"
    # set all members of x to the new values
    x.__dict__.update(newx.__dict__)  # any members not in newx will stay the same
    x.rmtloc_rmt0bf = rmtloc  # restore rmtloc from old instance
    x.rmtowner_rmt0bf = False  # ensure still not owner
    logger.debug("X synced:" + str(x.__dict__))
    return x


# usemetadatastore = True
usemetadatastore = False


def getmetadata(self, name):
    if usemetadatastore:
        if id(self) not in remoter.remotedclassmetadata:
            return object.__getattribute__(self, name)
        try:
            return remoter.remotedclassmetadata[id(self)][name]
        except KeyError:
            print(f"getmatadata {name} does not exist")
            raise AttributeError(name)  # noqa: B904 vendored from microsoft/xavier, not refactored
    else:
        return object.__getattribute__(self, name)


def setmetadata(self, name, val):
    if usemetadatastore:
        store = remoter.remotedclassmetadata.setdefault(id(self), {})
        store[name] = val
    else:
        object.__setattr__(self, name, val)


def objgetattr(self, name):
    try:
        # see if this is remoted class
        remoted = object.__getattribute__(self, "uuid_rmt0bf")
        if remoted:
            return getattribute(self, name)
    except Exception:
        pass
    ret = object.__getattribute__(self, name)
    return ret


def objsetattr(self, name, value):
    return object.__setattr__(self, name, value)


def isattrlocal(self, name):
    if name.startswith("__") and name.endswith("__"):
        return True
    if remoter.threadctx.noremote:
        return True
    if name in ["setremoteloc"]:
        return True
    try:
        ret = object.__getattribute__(self, name)
        if callable(ret):
            return True
    except Exception:
        pass
    try:
        isowner = getmetadata(self, "rmtowner_rmt0bf")
        if isowner:
            return True
    except Exception as ex:
        logger.error(f"isattrlocal exception: {ex}\n{traceback.format_exc()}", color="red")
        logger.error(
            f"Remoteable class {type(self)} has not been properly initialized -- make sure __init__ is called",
            color="red",
        )
        raise ex
    return False


def getattribute(self, name):
    if name.endswith("_rmt0bf"):
        return getmetadata(self, name)
    elif isattrlocal(self, name):
        return object.__getattribute__(self, name)
    else:
        try:
            taskname = self.remotedclasskey_rmt0bf
            rmtloc = getattribute(self, "rmtloc_rmt0bf")  # rmtloc must be set
        except Exception as e:
            # no rmtloc set
            logger.error(f"getattribute returns exception {e}\n{traceback.format_exc()}", color="red")
            # return object.__getattribute__(self, name)
            raise AttributeError(f"{name} not found -- perhaps rmtloc not set yet")  # noqa: B904 vendored from microsoft/xavier, not refactored
        actclasskey = f"{self.__class__.__module__}/{self.__class__.__name__}"
        timeout = remoter.getparam("getattrtimeout", taskname + "/", actclasskey, None)
        timeout = remoter.getparam(
            "getattrtimeout" + "/" + name, taskname + "/", actclasskey, timeout
        )  # more specific timeout for this attribute
        return remoter.remoter.runSyncFunction(
            taskname, "threadpooltask", False, timeout, rmtloc, objgetattr, self, name
        )


def setattribute(self, name, val):
    if name.endswith("_rmt0bf"):
        return setmetadata(self, name, val)
    elif isattrlocal(self, name):
        return object.__setattr__(self, name, val)
    else:
        try:
            taskname = self.remotedclasskey_rmt0bf
            rmtloc = getattribute(self, "rmtloc_rmt0bf")  # rmtloc must be set
        except Exception as e:
            # no rmtloc set
            logger.error(f"getattribute returns exception {e}\n{traceback.format_exc()}", color="red")
            # return object.__setattr__(self, name, val)
            raise AttributeError(f"{name} not found -- perhaps rmtloc not set yet")  # noqa: B904 vendored from microsoft/xavier, not refactored
        actclasskey = f"{self.__class__.__module__}/{self.__class__.__name__}"
        timeout = remoter.getparam("setattrtimeout", taskname + "/", actclasskey, None)
        timeout = remoter.getparam(
            "setattrtimeout" + "/" + name, taskname + "/", actclasskey, timeout
        )  # more specific timeout for this attribute
        return remoter.remoter.runSyncFunction(
            taskname, "threadpooltask", False, timeout, rmtloc, objsetattr, self, name, val
        )


def getallmethods(bases: tuple, attrs: dict):
    methodsKV = attrs
    for base in bases:
        for base2 in base.__mro__:
            for attr_name, attr_value in base2.__dict__.items():
                if attr_name not in methodsKV:
                    methodsKV[attr_name] = attr_value
    return methodsKV


def _method_patterns(field: str, actclasskey: str) -> list[str]:
    patterns = remoter.getparam(field, actclasskey + "/", actclasskey, [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern for pattern in patterns):
        raise ValueError(f"{field} for {actclasskey} must be a list of non-empty strings")
    return patterns


def _expand_method_patterns(
    patterns: list[str],
    method_names: set[str],
    *,
    field: str,
    actclasskey: str,
) -> set[str]:
    expanded: set[str] = set()
    for pattern in patterns:
        matches = {method_name for method_name in method_names if fnmatchcase(method_name, pattern)}
        if not matches:
            raise ValueError(f"{field} pattern {pattern!r} for {actclasskey} matches no methods")
        if any(character in pattern for character in "*?["):
            logger.warning(
                f"{field} wildcard {pattern!r} for {actclasskey} expands to {sorted(matches)}",
                color="yellow",
            )
        expanded.update(matches)
    return expanded


def _server_callable_methods(methodsKV: dict, actclasskey: str) -> set[str]:
    class_params = remoter.remoterclassparams.get(actclasskey, {})
    legacy_fields = sorted({"remoteableserver", "remoteableon"} & class_params.keys())
    if legacy_fields:
        raise ValueError(
            f"{', '.join(legacy_fields)} for {actclasskey} is not supported; use servercallablemethods"
        )
    method_names = {
        attr_name for attr_name, attr_value in methodsKV.items() if isinstance(attr_value, types.FunctionType)
    }
    allowed = _expand_method_patterns(
        _method_patterns("servercallablemethods", actclasskey),
        method_names,
        field="servercallablemethods",
        actclasskey=actclasskey,
    )
    denied = _expand_method_patterns(
        _method_patterns("serverdeniedmethods", actclasskey),
        method_names,
        field="serverdeniedmethods",
        actclasskey=actclasskey,
    )
    effective = allowed - denied
    logger.info(f"Server-callable methods for {actclasskey}: {sorted(effective)}", color="green")
    return effective


def allowallfunctions(cls, isserver):
    # get all methods including class attributes from bases and this class,
    # with this class attributes taking precedence over base class attributes
    methodsKV = getallmethods(cls.__bases__, dict(cls.__dict__))
    # print("MethodsKV:", methodsKV)
    # print(remoter.remoterclassparams)
    initfound = False
    actclasskey = f"{cls.__module__}/{cls.__name__}"
    server_callable_methods = _server_callable_methods(methodsKV, actclasskey) if isserver else set()
    noremotefuncs = remoter.getparam("noremotefuncs", actclasskey + "/", actclasskey, [])
    for attr_name, attr_value in methodsKV.items():
        if isinstance(attr_value, types.FunctionType):
            # this key consists of mod.baseclass.func since qualname uses
            # baseclass if method is inherited from base class, and module is the module where the function is defined
            # if function is defined in this class, then qualname uses this class, and module
            # in this case actclasskey is mod.thisclass, and funcname is func, so key is mod.thisclass.func
            key, module_name, func_name, class_name = remoter.getfuncname(attr_value)  # noqa: RUF059 vendored from microsoft/xavier, not refactored
            callable_key = f"{actclasskey}/{attr_name}"
            # assert func_name == attr_name, "Function name mismatch" -- this fails sometimes
            if func_name != attr_name:
                logger.warning(f"Function name mismatch: {func_name} != {attr_name}", color="yellow")
            if attr_name == "__init__":
                initfound = True
            taskname = remoter.getparam("taskname", key, actclasskey, actclasskey)
            functype = remoter.getparam("functype", key, actclasskey, "threadpooltask")
            remoteloc = remoter.getparam("remoteloc", key, actclasskey, None)
            timeout = remoter.getparam("timeout", key, actclasskey, None)
            if isserver:
                if attr_name in server_callable_methods:
                    remoter.allow_function(callable_key)
                    logger.info(f"Allowed server method {callable_key} without RPC wrapping", color="green")
                else:
                    remoter.disallow_function(callable_key)
            elif attr_name not in noremotefuncs:
                logger.info(f"Adding function {callable_key} to allowed functions")
                remotefunc = remoter.createRemotedTask(
                    attr_value,
                    taskname,
                    functype,
                    timeout=timeout,
                    callable_key=callable_key,
                )  # overwrite functions
                setattr(cls, attr_name, remotefunc)
                logger.info(f"Function {callable_key} remoteloc={remoteloc}", color="green")
                if remoteloc is not None:
                    remoter.setfixedlocs({callable_key: remoteloc})
            else:
                remoter.disallow_function(callable_key)
    assert initfound, "No __init__ method found in remoted class"
    remoteableclass = not isserver
    singleinstanceclass = remoter.getparam("singleinstance", actclasskey + "/", actclasskey, False)
    remotelocclass = remoter.getparam("remoteloc", actclasskey + "/", actclasskey, None)
    if remotelocclass is not None:
        remoter.setfixedlocs({actclasskey: remotelocclass})
    logger.info(
        f"Class {actclasskey} remoteable={remoteableclass} remoteloc={remotelocclass} singleinstance={singleinstanceclass}",  # noqa: E501 vendored from microsoft/xavier, not refactored
        color="green",
    )
    taskname = remoter.getparam("taskname", actclasskey + "/", actclasskey, actclasskey)
    timeout = remoter.getparam("getfromremotetimeout", actclasskey + "/", actclasskey, None)
    remoter.remotedclasskey[cls] = taskname
    # always override these functions
    cls.syncwithremote = syncwithremote
    cls._getfromremote = remoter.createRemotedTask(_getfromremote, actclasskey, "threadpooltask", timeout=timeout)
    # class attributes
    cls.remoteable_rmt0bf = remoteableclass
    cls.singleinstance_rmt0bf = singleinstanceclass
    if remoteableclass:
        cls.__getattribute__ = getattribute
        cls.__setattr__ = setattribute
    remoter.allow_function("remoter.rmtclass//_getfromremote")
    remoter.allow_function("remoter.rmtclass//objgetattr")
    remoter.allow_function("remoter.rmtclass//objsetattr")


def addsingleinstance(cls, classparams):
    if classparams.get("singleinstance", False):
        # single instance class
        logger.info(f"Class {cls.__name__} is single instance class")
        remoter.addsingleinstanceclass(cls)


def setfixedloc(funckey, funcparams):
    if "remoteloc" in funcparams[funckey]:
        remoter.setfixedlocs({funckey: funcparams[funckey]["remoteloc"]})


def createRemotedClass(cls, taskname, params):
    # if already remoted class then return cls
    if cls in remoter.remotedclasskey:
        logger.info(f"Class {cls} already remoted, skipping", color="yellow")
        return cls
    isserver = os.environ.get("SERVER", "false").lower() in ["true", "1", "yes"]  # override if set in env
    params.update({"taskname": taskname})
    classkkey = f"{cls.__module__}/{cls.__name__}"
    remoter.remoterclassparams[classkkey] = params
    logger.debug(remoter.remoterclassparams)
    logger.debug(isserver)
    allowallfunctions(cls, isserver)
    remoter.addremotedclass(cls)
    return cls


def remotedclass(taskname=None, params={}):  # noqa: B006 vendored from microsoft/xavier, not refactored
    def decorator(cls):
        return createRemotedClass(cls, taskname, params)

    return decorator
