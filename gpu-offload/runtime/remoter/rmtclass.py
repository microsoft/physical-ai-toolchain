from __future__ import annotations

import logging
import os
import traceback
import types

from . import remoter
from .msgsock import isselfip
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


def isremoteable(isserver: bool, key: str, actclasskey: str) -> bool:
    if not isserver:
        return True  # server classes always remoteable
    if remoter.getparam("remoteableserver", key, actclasskey, False):
        return True
    remoteableon = remoter.getdictparam("remoteableon", key, actclasskey)
    for loc, val in remoteableon.items():  # noqa: B007 vendored from microsoft/xavier, not refactored
        if ":" in loc:
            host, port = loc.split(":")
            if isselfip(host, port, remoter.remoterparams["port"]):
                return True
        else:  # unixpaath
            if loc == remoter.remoterparams["socketpath"]:
                return True
    return False


def allowallfunctions(cls, isserver):
    # get all methods including class attributes from bases and this class,
    # with this class attributes taking precedence over base class attributes
    methodsKV = getallmethods(cls.__bases__, dict(cls.__dict__))
    # print("MethodsKV:", methodsKV)
    # print(remoter.remoterclassparams)
    initfound = False
    actclasskey = f"{cls.__module__}/{cls.__name__}"
    noremotefuncs = remoter.getparam("noremotefuncs", actclasskey + "/", actclasskey, [])
    for attr_name, attr_value in methodsKV.items():
        if isinstance(attr_value, types.FunctionType):
            # this key consists of mod.baseclass.func since qualname uses
            # baseclass if method is inherited from base class, and module is the module where the function is defined
            # if function is defined in this class, then qualname uses this class, and module
            # in this case actclasskey is mod.thisclass, and funcname is func, so key is mod.thisclass.func
            key, module_name, func_name, class_name = remoter.getfuncname(attr_value)  # noqa: RUF059 vendored from microsoft/xavier, not refactored
            # assert func_name == attr_name, "Function name mismatch" -- this fails sometimes
            if func_name != attr_name:
                logger.warning(f"Function name mismatch: {func_name} != {attr_name}", color="yellow")
            if attr_name == "__init__":
                initfound = True
            logger.info(f"Adding function {key} to allowed functions")
            remoter.allowed_functions.add(key)
            # now check if func is remotable task - client is always remotable by default, server not
            remoteable = isremoteable(isserver, key, actclasskey)
            singleinstance = remoter.getparam("singleinstance", key, actclasskey, False)
            taskname = remoter.getparam("taskname", key, actclasskey, actclasskey)
            functype = remoter.getparam("functype", key, actclasskey, "threadpooltask")
            remoteloc = remoter.getparam("remoteloc", key, actclasskey, None)
            timeout = remoter.getparam("timeout", key, actclasskey, None)
            if not remoteable and singleinstance and attr_name == "__init__":
                assert not hasattr(attr_value, "__isremoted__"), "Function __init__ already decorated"
                cls.__new__ = remoter.singleton_new
                cls.__orig_init__ = attr_value
                setattr(cls, attr_name, remoter.singleton_init)
                remoter.allowed_functions.add("remoter.remoter//singleton_init")
                # remoter.allowed_functions.add(f"remoter.remoter//singleton_new")
                logger.info(f"Single instance non-remoteable class {actclasskey} __init__ decorated", color="green")
            elif remoteable and (attr_name not in noremotefuncs):
                # if already has "__isremoted__" attribute, skip
                if hasattr(attr_value, "__isremoted__"):
                    logger.info(f"Function {key} already decorated, skipping", color="yellow")
                    continue
                remotefunc = remoter.createRemotedTask(
                    attr_value, taskname, functype, timeout=timeout
                )  # overwrite functions
                setattr(cls, attr_name, remotefunc)
                logger.info(f"Function {key} remoteable={remoteable} remoteloc={remoteloc}", color="green")
            if remoteloc is not None:
                remoter.setfixedlocs({key: remoteloc})
    assert initfound, "No __init__ method found in remoted class"
    remoteableclass = isremoteable(isserver, actclasskey + "/", actclasskey)
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
    remoter.allowed_functions.add("remoter.rmtclass//_getfromremote")
    remoter.allowed_functions.add("remoter.rmtclass//objgetattr")
    remoter.allowed_functions.add("remoter.rmtclass//objsetattr")


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
