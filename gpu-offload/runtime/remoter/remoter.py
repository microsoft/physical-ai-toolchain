"""
Remote function call module
"""

from __future__ import annotations

# pylint: disable=missing-module-docstring, W0604, W1203, W0719, broad-exception-raised, R0913, R0917
import asyncio
import atexit
import copy
import ctypes
import importlib
import inspect
import logging
import multiprocessing
import os
import random
import secrets
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial, wraps
from multiprocessing import Process
from queue import Queue
from threading import Thread
from types import ModuleType
from typing import Any

from . import class2dict, msgqueue, msgsock, msgtcp, msgudp, msgunix, rmtconfig, rmtconfigkube, simplelog
from .k8sutils_compat import utils
from .safe_codec import (
    AdapterContext,
    AdapterRegistry,
    CodecError,
    CodecLimits,
    TypeAdapter,
)
from .safe_codec import (
    dumps as codec_dumps,
)
from .safe_codec import (
    loads as codec_loads,
)


# create a thread local variable
class ThreadContext(threading.local):
    def __init__(self):
        self.noremote = False


threadctx = ThreadContext()  # instantiated once per thread

_CODEC_LIMITS = CodecLimits()
_CODEC_REGISTRY = AdapterRegistry()
_CODEC_CONTEXT = AdapterContext(role="remoter")


def serialize_payload(obj) -> bytes:
    oldnoremote = threadctx.noremote
    threadctx.noremote = True
    try:
        return codec_dumps(
            obj,
            _CODEC_REGISTRY,
            limits=_CODEC_LIMITS,
            context=_CODEC_CONTEXT,
        )
    finally:
        threadctx.noremote = oldnoremote


def deserialize_payload(payload: bytes) -> Any:
    oldnoremote = threadctx.noremote
    threadctx.noremote = True
    try:
        return codec_loads(
            payload,
            _CODEC_REGISTRY,
            limits=_CODEC_LIMITS,
            context=_CODEC_CONTEXT,
        )
    finally:
        threadctx.noremote = oldnoremote


def copyobj(obj: Any) -> Any:
    oldnoremote = threadctx.noremote
    threadctx.noremote = True
    try:
        return copy.deepcopy(obj)
    finally:
        threadctx.noremote = oldnoremote


def modifyloc(loc: str, key: str, actclasskey: str) -> str:
    if loc == "direct" or loc == "directqueue":
        return loc
    try:
        protocol, addr = loc.split("://")  # noqa: RUF059 vendored from microsoft/xavier, not refactored
        return loc
    except ValueError:
        # handle no protocol specified case, default to tcp and check override for function
        if loc.startswith("unix:"):
            loc = f"unix://{loc[5:]}"
        else:
            useudp = os.environ.get("USE_UDP", "false").lower() in ["true", "1", "yes"]
            useudp = getparam("udp", key, actclasskey, useudp)  # check config for whether to use udp for this function
            if useudp:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
                loc = f"udp://{loc}"
            else:
                loc = f"tcp://{loc}"
        return loc


# new exception for stopped by user
class FunctionStoppedException(Exception):
    pass


@dataclass(frozen=True)
class RemoteErrorDescriptor:
    """Wire-safe remote error representation."""

    type_name: str
    message: str
    traceback: str


class RemoteExecutionError(Exception):
    """Raised locally from a remote exception descriptor."""

    def __init__(self, descriptor: RemoteErrorDescriptor) -> None:
        self.descriptor = descriptor
        super().__init__(f"{descriptor.type_name}: {descriptor.message}\n{descriptor.traceback}")


stub_to_class = {}  # stub class to actual class
class_to_stub = {}  # actual class to stub class

needmultiproc = False
remotedclassmetadata = {}  # key: class object id -> metadata dict for object


def setstubclasses(stub2class):
    # of the form a.b.c: d.e
    # where a.b.c is the stub class, d.e is the actual class
    stub_to_class.update(stub2class)
    class_to_stub.update({v: k for k, v in stub2class.items()})


# just keep track of classes that are remoted, do not modify them
remotedclasses = set()


def addremotedclass(cls):
    remotedclasses.add(cls)


fixedlocs = {}


def setfixedlocs(fixedloc2: dict):
    fixedlocs.update(fixedloc2)


def classkey(cls: type) -> str:
    return f"{cls.__module__}/{cls.__name__}"


def issingleinstanceclass(x: object) -> bool:
    return classkey(x.__class__) in singleinstanceclass


singleinstanceclass = set()
singleinstanceclassinstance: dict[str, tuple[threading.Lock, Any, dict, threading.Event]] = {}


def addsingleinstanceclass(cls):
    logger.debug(f"Adding single instance class {cls} to cache")
    singleinstanceclass.add(classkey(cls))


singleinstanceclasslock = threading.Lock()

# key -> (lock, result, state, event)
singleinstancefunc: dict[str, tuple[threading.Lock, Any, dict, threading.Event]] = {}
singleinstancefunclock = threading.Lock()


def addsingleinstancefunc(funcname: str):
    with singleinstancefunclock:
        if funcname not in singleinstancefunc:
            logger.debug(f"Adding single instance function {funcname} to cache")
            singleinstancefunc[funcname] = (threading.Lock(), None, {"state": "notinit"}, threading.Event())


singleinstanceclassids = set()

remoterparams = {}
remoterclassparams = {}
remoterfuncparams = {}
paramsset = False


def setparams(config: dict):
    global paramsset
    if paramsset:
        return
    configcopy = copy.deepcopy(config)
    remoteclasses = configcopy.pop("remoteclasses", [])
    remotefuncs = configcopy.pop("remotefuncs", [])
    remoterparams.update(configcopy)
    for classitem in remoteclasses:
        for classpath, params in classitem.items():
            remoterclassparams[classpath] = params
    for funcitem in remotefuncs:
        for funcpath, params in funcitem.items():
            remoterfuncparams[funcpath] = params
    if "port" not in remoterparams:
        remoterparams["port"] = int(os.environ.get("REMOTERPORT", "9000"))
    if "socketpath" not in remoterparams:
        remoterparams["socketpath"] = os.environ.get("REMOTERSOCK", None)
    if "server" not in remoterparams:
        remoterparams["server"] = os.environ.get("SERVER", "false").lower() in ["true", "1", "yes"]
    paramsset = True


# for example, if base class has mod.base.func, classkey is mod.base, funckey is mod.class.func
#    however, actclass can be different such as modd.derived (modd is a different module with derived class 'derived' inheriting from base)  # noqa: E501 vendored from microsoft/xavier, not refactored
#    funckey is found using
#       func.__module__ and func.__qualname__ to get module, class, and function name, and then constructing mod.class.func  # noqa: E501 vendored from microsoft/xavier, not refactored
#       func.__qualname__ which gives mod.class.func, and then splitting by "." to get class name and function name
#    classkey however is found using
#       obj.__class__.__module__ and obj.__class__.__name__ to get module and class name, and then constructing mod.class  # noqa: E501 vendored from microsoft/xavier, not refactored
def getparam(key: str, funckey: str | None, actclasskey: str | None, default: Any):
    # print(key, funckey)
    logger.debug(f"Getting param {key} for function {funckey} -- default={default}")
    ret = default
    if funckey is None:
        module, classname = None, None
    else:
        # classname here could be baseclass or derivedclass
        module, classname, _ = funckey.split("/")
    classkey = f"{module}/{classname}"  # this could be baseclass
    # priority order for params: overall -> base class -> actual class -> function specific
    if key in remoterparams:
        ret = remoterparams[key]
    if classkey in remoterclassparams and key in remoterclassparams[classkey]:
        ret = remoterclassparams[classkey][key]
    if actclasskey in remoterclassparams and key in remoterclassparams[actclasskey]:
        ret = remoterclassparams[actclasskey][key]
    if funckey in remoterfuncparams and key in remoterfuncparams[funckey]:
        ret = remoterfuncparams[funckey][key]
    return ret


def getdictparam(key: str, funckey: str, actclasskey: str) -> dict:
    # print(key, funckey)
    logger.debug(f"Getting param {key} for function {funckey}")
    ret = {}
    if funckey is None:
        module, classname = None, None
    else:
        module, classname, _ = funckey.split("/")
    classkey = f"{module}/{classname}"
    # priority order for params: overall -> base class -> actual class -> function specific, with later ones overwriting earlier ones  # noqa: E501 vendored from microsoft/xavier, not refactored
    if key in remoterparams:
        ret.update(remoterparams[key])  # overall params
    if classkey in remoterclassparams and key in remoterclassparams[classkey]:
        ret.update(remoterclassparams[classkey][key])  # overwrite with base class params (if exist)
    if actclasskey in remoterclassparams and key in remoterclassparams[actclasskey]:
        ret.update(remoterclassparams[actclasskey][key])  # overwrite with actual class params (if exist)
    if funckey in remoterfuncparams and key in remoterfuncparams[funckey]:
        ret.update(remoterfuncparams[funckey][key])  # overwrite with function params (if exist)
    return ret


# ==================

imported_modules: dict[str, ModuleType] = {}
imported_functions: dict[str, Callable] = {}

allowed_functions = set()
allowed_print = False
is_process = False
mprunq = None
mpresults = None


def default_func_key(func):
    key, _, _, _ = getfuncname(func)
    return key


def getclassinstancefromname(name):
    try:
        module_name, class_name = name.split("/")
        if module_name not in imported_modules:
            imported_modules[module_name] = importlib.import_module(module_name)
        module = imported_modules[module_name]
        class_type = getattr(module, class_name)
        return class_type.__new__(class_type)
    except Exception as e:
        logger.error(f"failed to create class instance from name {name}: {e}\n{traceback.format_exc()}", color="red")
        raise e


# returns key, module_name, func_name, class_name
def getfuncname(func) -> tuple[str, str, str, str]:
    module_name = func.__module__
    func_name = func.__name__
    qualname = func.__qualname__
    # Check if the function is a method of a class
    if "." in qualname:
        class_name = qualname.split(".")[0]
        func_name = qualname.split(".")[1]
    else:
        class_name = ""
    key = f"{module_name}/{class_name}/{func_name}"
    return key, module_name, func_name, class_name


def getfuncobjfromname(key, module_name, func_name, class_name):
    # Get the module object
    if module_name not in imported_modules:
        imported_modules[module_name] = importlib.import_module(module_name)
    module = imported_modules[module_name]

    # Get the function object
    if key not in imported_functions:
        if class_name != "":
            class_obj = getattr(module, class_name)
            func_obj = getattr(class_obj, func_name)
        else:
            func_obj = getattr(module, func_name)
        # Store the function object in the dictionary
        imported_functions[key] = func_obj
    func_obj = imported_functions[key]
    return func_obj


def localhasattr(obj, name: str) -> bool:
    try:
        object.__getattribute__(obj, name)
        return True
    except AttributeError:
        return False


# ===================================

remotedclasskey = {}


# represents a remoted class on the client side
class MetaRemotedUUID:
    def __init__(self, x):
        self.uuid_rmt0bf = x.uuid_rmt0bf  # x must be an instance of a class with uuid attribute
        self.rmtloc_rmt0bf = x.rmtloc_rmt0bf
        name = f"{x.__class__.__module__}/{x.__class__.__name__}"
        # self.name always store the actual class name
        if name in stub_to_class:  # noqa: SIM401 vendored from microsoft/xavier, not refactored
            self.name = stub_to_class[name]
        else:
            self.name = name
        logger.debug(
            f"Created MetaRemotedUUID of type {type(x)} with ID {self.uuid_rmt0bf} at location {self.rmtloc_rmt0bf}"
        )
        self.obj = None  # overwrite if wish to transmit actual object

    def issingleinstance(self) -> bool:
        return self.name in singleinstanceclass

    def getinstance(self, alternateloc):  # get instance of actual class/stub class from MetaRemotedUUID
        isserver = remoterparams["server"]
        if self.obj is None:
            if (self.name in class_to_stub) and not isserver:  # get stub class on client side
                x = getclassinstancefromname(class_to_stub[self.name])
            else:  # on server side, always get actual class
                x = getclassinstancefromname(self.name)
        else:
            x = self.obj
        x.uuid_rmt0bf = self.uuid_rmt0bf
        x.rmtloc_rmt0bf = self.rmtloc_rmt0bf
        if x.rmtloc_rmt0bf in ["direct", "directqueue"] and alternateloc is not None:
            logger.info(
                f"Using alternateloc {alternateloc} for direct/directqueue remoted class instance for object of type {type(x)} with ID {self.uuid_rmt0bf}"  # noqa: E501 vendored from microsoft/xavier, not refactored
            )
            x.rmtloc_rmt0bf = alternateloc
        # if remoter.islocself(x.rmtloc_rmt0bf):
        #    x.rmtowner_rmt0bf = True
        x.rmtowner_rmt0bf = False  # set to True for server side once __init__ is called
        x.failed_rmt0bf = False
        x.remotedclasskey_rmt0bf = remotedclasskey[type(x)]
        logger.debug(f"Created instance of type {type(x)} with ID {self.uuid_rmt0bf} at location {self.rmtloc_rmt0bf}")
        return x


def _encode_meta_remoted_uuid(obj: MetaRemotedUUID, _context: AdapterContext) -> dict[str, Any]:
    return {
        "uuid": obj.uuid_rmt0bf,
        "rmtloc": obj.rmtloc_rmt0bf,
        "name": obj.name,
        "obj": obj.obj,
    }


def _decode_meta_remoted_uuid(payload: Any, _context: AdapterContext) -> MetaRemotedUUID:
    if not isinstance(payload, dict):
        raise CodecError("MetaRemotedUUID payload must be a dict")
    if "uuid" not in payload or "rmtloc" not in payload or "name" not in payload:
        raise CodecError("MetaRemotedUUID payload missing required fields")
    obj = MetaRemotedUUID.__new__(MetaRemotedUUID)
    obj.uuid_rmt0bf = payload["uuid"]
    obj.rmtloc_rmt0bf = payload["rmtloc"]
    obj.name = payload["name"]
    obj.obj = payload.get("obj")
    return obj


def _encode_remote_error_descriptor(obj: RemoteErrorDescriptor, _context: AdapterContext) -> dict[str, str]:
    return {
        "type_name": obj.type_name,
        "message": obj.message,
        "traceback": obj.traceback,
    }


def _decode_remote_error_descriptor(payload: Any, _context: AdapterContext) -> RemoteErrorDescriptor:
    if not isinstance(payload, dict):
        raise CodecError("RemoteErrorDescriptor payload must be a dict")
    return RemoteErrorDescriptor(
        type_name=str(payload.get("type_name", "Exception")),
        message=str(payload.get("message", "")),
        traceback=str(payload.get("traceback", "")),
    )


def register_codec_adapter(adapter: TypeAdapter) -> None:
    _CODEC_REGISTRY.register(adapter)


def register_class2dict_type(cls: type[Any], *, wire_name: str | None = None) -> None:
    """Register a stable class2dict wire name or map a remote name to a local class."""
    class2dict.register_type(cls, name=wire_name)


def register_state_adapter(
    *,
    ext_code: int,
    cls: type[Any],
    encode_state: Callable[[Any], Any],
    decode_state: Callable[[Any], Any],
) -> None:
    """Register an explicit adapter for custom types (e.g., ImageP)."""

    register_codec_adapter(
        TypeAdapter(
            ext_code=ext_code,
            py_type=cls,
            encode=lambda obj, _ctx: encode_state(obj),
            decode=lambda payload, _ctx: decode_state(payload),
        )
    )


register_codec_adapter(
    TypeAdapter(
        ext_code=16,
        py_type=MetaRemotedUUID,
        encode=_encode_meta_remoted_uuid,
        decode=_decode_meta_remoted_uuid,
    )
)

register_codec_adapter(
    TypeAdapter(
        ext_code=17,
        py_type=RemoteErrorDescriptor,
        encode=_encode_remote_error_descriptor,
        decode=_decode_remote_error_descriptor,
    )
)

_CODEC_REGISTRY.register_fallback(
    TypeAdapter(
        ext_code=18,
        py_type=object,
        encode=lambda obj, _context: class2dict.to_dict(obj),
        decode=lambda payload, _context: class2dict.from_dict(payload, limits=_CODEC_LIMITS),
    )
)

_RESULT_SERIALIZATION_FAILURE_PAYLOAD = serialize_payload(
    (
        {"key": "unknown", "loc": "unknown"},
        None,
        RemoteErrorDescriptor(
            type_name="ResultSerializationError",
            message="Failed to serialize remote result",
            traceback="",
        ),
    )
)


def initfields(x):
    x.uuid_rmt0bf = uuid.uuid4()  # consistent on client and server side
    x.rmtloc_rmt0bf = None  # only set on client side
    x.failed_rmt0bf = False  # only valid on client side
    x.rmtowner_rmt0bf = False  # for both client and server side
    x.remotedclasskey_rmt0bf = remotedclasskey[type(x)]


nodehydrate = ["remoter.rmtclass//_getfromremote"]


def setRemotedClassInCache(obj, remotedClasses: dict, callbackonCacheAdd: Callable | None) -> None:
    # if single instance class, only keep one instance in cache
    if issingleinstanceclass(obj):
        classkeystr = classkey(obj.__class__)
        if classkeystr not in singleinstanceclassinstance:
            with singleinstanceclasslock:
                # single instance class stored in singleinstanceclassinstance
                # logger.info(f"ID of obj.__class__: {id(obj.__class__)}")
                logger.debug(f"Keys: {list(singleinstanceclassinstance.keys())}", color="yellow")
                if classkeystr not in singleinstanceclassinstance:  # check again inside lock to avoid race
                    singleinstanceclassinstance[classkeystr] = (
                        threading.Lock(),
                        obj,
                        {"state": "notinit"},
                        threading.Event(),
                    )
                    logger.info(f"Adding single instance classobject {obj.__class__} to cache -- id: {obj.uuid_rmt0bf}")
                    # logger.info(f"Dict of object being added: {obj.__dict__}")
                    logger.debug(f"Keys: {list(singleinstanceclassinstance.keys())}")
                    singleinstanceclassids.add(obj.uuid_rmt0bf)
    else:
        if obj.uuid_rmt0bf not in remotedClasses:
            logger.info(f"Adding remoted classobject {obj.__class__} to cache -- id: {obj.uuid_rmt0bf}")
            remotedClasses[obj.uuid_rmt0bf] = obj
            if callbackonCacheAdd is not None:
                callbackonCacheAdd(obj)


def singleton_new(cls, *args, **kwargs):
    classkeystr = classkey(cls)
    if classkeystr not in singleinstanceclassinstance:
        with singleinstanceclasslock:
            if classkeystr not in singleinstanceclassinstance:  # check again inside
                ret = object.__new__(cls)
                logger.info(f"Created single instance __new__ {classkeystr}")
                singleinstanceclassinstance[classkeystr] = (
                    threading.Lock(),
                    ret,
                    {"state": "notinit"},
                    threading.Event(),
                )
    return singleinstanceclassinstance[classkeystr][1]


def singleton_init(x, *args, **kwargs):
    classkeystr = classkey(x.__class__)
    with singleinstanceclasslock:
        lock, instance, state, event = singleinstanceclassinstance[classkeystr]
    with lock:
        if state["state"] in ["notinit", "initializing"]:
            logger.info(f"Initializing single instance {classkeystr}")
            x.__orig_init__(*args, **kwargs)
            singleinstanceclassinstance[classkeystr] = (lock, instance, {"state": "initialized"}, event)
            logger.info(f"Initialized single instance {classkeystr}")


# def printSI():
#     try:
#         cprint(str(singleinstanceclassinstance['rmt/F'][1].__dict__), 'yellow')
#     except Exception as e:
#         print(f"Error printing single instance F: {e}")


def getRemotedClassFromCache(objremoted: MetaRemotedUUID, remotedClasses: dict) -> Any | None:
    # if obj is set in objremoted, return None so that a new instance is created
    if objremoted.obj is not None:
        return None
    if objremoted.issingleinstance():
        if objremoted.name in singleinstanceclassinstance:
            _, instance, _, _ = singleinstanceclassinstance[objremoted.name]
            return instance
    elif objremoted.uuid_rmt0bf in remotedClasses:
        return remotedClasses[objremoted.uuid_rmt0bf]
    return None


# Server -------------- Client
#                  <--  DehydrateArgs
# RehydrateArgs
# DehydrateResult  -->
#                       RehydrateResult
#
# Server class cache is only on server side
# Add to cache should occur on
# 1. RehydrateArgs
# 2. DehydrateResult
def dehydrate(obj: Any, key: str, remotedclasscache: dict, loc: str, isresult: bool, callbackOnCacheAdd) -> Any:
    dehydratermt = (key not in nodehydrate) or (
        not isresult
    )  # results are not dehydrated if in nodehydrate, args must always be dehydrated
    if not isresult and localhasattr(obj, "setremoteloc"):
        obj.setremoteloc(loc)  # on client side set location to where object is going to be located
    # go through the object and replace any RemotedClass with its uuid
    if type(obj) in remotedclasses and (
        obj.remoteable_rmt0bf or isresult
    ):  # this is more appropriate since isinstance will return true for subclasses
        if not localhasattr(obj, "uuid_rmt0bf"):
            assert isresult, (
                f"Remoted class instance of type {obj.__class__} without uuid_rmt0bf found during dehydration on client side"  # noqa: E501 vendored from microsoft/xavier, not refactored
            )
            initfields(obj)
            if isresult:
                # set remoteloc to loc - on server side loc is coming from funcargs which is the location of server as seen by client  # noqa: E501 vendored from microsoft/xavier, not refactored
                obj.rmtloc_rmt0bf = loc
                obj.rmtowner_rmt0bf = True
        ret = MetaRemotedUUID(obj)
        logger.info(f"Dehydrating for key {key}")
        logger.info(
            f"Dehydrating remoted class of type {obj.__class__} with ID {obj.uuid_rmt0bf} - dehydrate={dehydratermt} result={isresult}"  # noqa: E501 vendored from microsoft/xavier, not refactored
        )
        if not dehydratermt:
            if loc in ["direct", "directqueue"]:
                ret.obj = copyobj(obj)
            else:
                ret.obj = obj
        if isresult:
            setRemotedClassInCache(obj, remotedclasscache, callbackOnCacheAdd)
        return ret
    elif isinstance(obj, list):
        return [dehydrate(o, key, remotedclasscache, loc, isresult, callbackOnCacheAdd) for o in obj]
    elif isinstance(obj, tuple):
        return tuple(dehydrate(o, key, remotedclasscache, loc, isresult, callbackOnCacheAdd) for o in obj)
    elif isinstance(obj, dict):
        return {k: dehydrate(v, key, remotedclasscache, loc, isresult, callbackOnCacheAdd) for k, v in obj.items()}
    else:
        return obj


def rehydrate(
    objremoted: MetaRemotedUUID, remotedclasscache: dict, loc: str, isresult: bool, callbackOnCacheAdd
) -> Any:
    if isinstance(objremoted, MetaRemotedUUID):
        if isresult:
            return objremoted.getinstance(loc)  # no cache for rehydrate of results (client side)
        logger.info(f"Rehydrating remoted class with ID {objremoted.uuid_rmt0bf} of type {objremoted.name}")
        assert objremoted.obj is None, "obj should not exist on rehydration of arguments"
        ret = getRemotedClassFromCache(objremoted, remotedclasscache)
        if ret is not None:
            logger.debug(f"Found object with ID {objremoted.uuid_rmt0bf} of type {type(ret)} in cache")
            return ret
        # if not in cache, create new instance
        rehydrated = objremoted.getinstance(loc)
        setRemotedClassInCache(rehydrated, remotedclasscache, callbackOnCacheAdd)  # not isresult (arguments)
        logger.debug(f"Rehydrated object with ID {objremoted.uuid_rmt0bf} of type {type(rehydrated)}")
        return rehydrated
    elif isinstance(objremoted, list):
        return [rehydrate(o, remotedclasscache, loc, isresult, callbackOnCacheAdd) for o in objremoted]
    elif isinstance(objremoted, tuple):
        return tuple(rehydrate(o, remotedclasscache, loc, isresult, callbackOnCacheAdd) for o in objremoted)
    elif isinstance(objremoted, dict):
        return {k: rehydrate(v, remotedclasscache, loc, isresult, callbackOnCacheAdd) for k, v in objremoted.items()}
    else:
        return objremoted


def dehydrate_args(
    key: str, args: tuple, kwargs: dict, remotedclasscache: dict, loc: str, callbackOnCacheAdd
) -> tuple[tuple, dict]:
    argsn = list(copy.copy(args))
    kwargsn = copy.copy(kwargs)
    # go through args and kwargs and convert any RemotedClass to its uuid
    # arguments are not results (server side) so set isresult to False
    for i, arg in enumerate(argsn):
        argsn[i] = dehydrate(arg, key, remotedclasscache, loc, False, callbackOnCacheAdd)
    for k, v in kwargsn.items():
        kwargsn[k] = dehydrate(v, key, remotedclasscache, loc, False, callbackOnCacheAdd)
    return (tuple(argsn), kwargsn)


def rehydrate_args(
    args: tuple, kwargs: dict, remotedclasscache: dict, loc: str, callbackOnCacheAdd
) -> tuple[tuple, dict]:
    argsn = list(copy.copy(args))
    kwargsn = copy.copy(kwargs)
    # go through args and kwargs and convert any MetaRemotedUUID to actual object
    for i, arg in enumerate(argsn):
        argsn[i] = rehydrate(arg, remotedclasscache, loc, False, callbackOnCacheAdd)
    for k, v in kwargsn.items():
        kwargsn[k] = rehydrate(v, remotedclasscache, loc, False, callbackOnCacheAdd)
    return (tuple(argsn), kwargsn)


def get_func_from_stub_func(key: str):
    module_name, class_name, func_name = key.split("/")
    if key in stub_to_class:
        real_module_name, real_class_name, real_func_name = stub_to_class[
            f"{module_name}/{class_name}/{func_name}"
        ].split("/")
        real_key = f"{real_module_name}/{real_class_name}/{real_func_name}"
        logger.info(f"Mapping stub function {key} to real function {real_key}")
        return real_key, real_module_name, real_func_name, real_class_name
    elif f"{module_name}/{class_name}" in stub_to_class:
        real_module_name, real_class_name = stub_to_class[f"{module_name}/{class_name}"].split("/")
        real_key = f"{real_module_name}/{real_class_name}/{func_name}"
        logger.info(f"Mapping stub class function {key} to real class function {real_key}")
        return real_key, real_module_name, func_name, real_class_name
    else:
        return key, module_name, func_name, class_name


def encode_function_call(func, loc: str, remotedclasscache: dict, callbackOnCacheAdd, *args, **kwargs) -> bytes:
    key, _, _, _ = getfuncname(func)
    key, module_name, func_name, class_name = get_func_from_stub_func(key)
    # args is a tuple, kwargs is a dictionary
    # shallow copy of args into list
    argsn, kwargsn = dehydrate_args(key, args, kwargs, remotedclasscache, loc, callbackOnCacheAdd)
    payload = serialize_payload((loc, key, module_name, func_name, class_name, argsn, kwargsn))
    logger.info(f"Encoded function payload of length {len(payload)} for key {key}")
    return payload


def decode_function_call(
    payload, conn, remotedClasses, callbackOnCacheAdd
) -> tuple[Callable | None, dict, tuple, dict, CodecError | None]:
    try:
        decoded = deserialize_payload(payload)
    except (CodecError, TypeError, ValueError) as exc:
        decode_error = exc if isinstance(exc, CodecError) else CodecError(str(exc))
        logger.error(f"Failed to decode function call payload: {decode_error}")
        return None, {"key": "unknown", "loc": "unknown"}, (), {}, decode_error
    if not isinstance(decoded, tuple) or len(decoded) != 7:
        exc = CodecError("Function call payload must contain seven fields")
        logger.error(f"Failed to decode function call payload: {exc}")
        return None, {"key": "unknown", "loc": "unknown"}, (), {}, exc
    loc, key, module_name, func_name, class_name, args, kwargs = decoded
    if not all(isinstance(value, str) for value in (loc, key, module_name, func_name, class_name)):
        exc = CodecError("Function call metadata fields must be strings")
        logger.error(f"Failed to decode function call payload: {exc}")
        return None, {"key": "unknown", "loc": "unknown"}, (), {}, exc
    if not isinstance(args, tuple) or not isinstance(kwargs, dict):
        exc = CodecError("Function call arguments must be a tuple and mapping")
        logger.error(f"Failed to decode function call payload: {exc}")
        return None, {"key": "unknown", "loc": "unknown"}, (), {}, exc
    logger.debug(f"=====Decoded function for key {key} to location {loc}=====")
    funcargs = {
        "key": key,
        "loc": loc,  # location of server as seen by client
        "module_name": module_name,
        "func_name": func_name,
        "class_name": class_name,
    }
    # rehydrate args on server side
    if conn is not None:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
        clientloc = conn.get("key", "")
    else:
        clientloc = None
    argsn, kwargs = rehydrate_args(args, kwargs, remotedClasses, clientloc, callbackOnCacheAdd)
    if func_name == "__init__":  # noqa: SIM102 vendored from microsoft/xavier, not refactored
        if len(argsn) > 0 and type(argsn[0]) in remotedClasses:
            # set the remoted class owner to True
            argsn[0].rmtowner_rmt0bf = True
            logger.debug(f"Setting remoted class with ID {argsn[0].uuid_rmt0bf} owner to True")
    # Get the function object
    func_obj = getfuncobjfromname(key, module_name, func_name, class_name)
    return func_obj, funcargs, argsn, kwargs, None


# return a message to send to the remote server
class MessageType:
    FunctionCall = 1
    FunctionResult = 2
    FunctionCancel = 3
    DeallocateClass = 4


class FunctionType:
    Direct = "direct"
    Thread = "thread"
    Process = "process"
    ThreadPool = "threadpooltask"
    ProcessPool = "processpooltask"

    @staticmethod
    def toint(functype):
        if functype == FunctionType.Direct:
            return 0
        elif functype == FunctionType.Thread:
            return 1
        elif functype == FunctionType.Process:
            return 2
        elif functype == FunctionType.ThreadPool:
            return 3
        elif functype == FunctionType.ProcessPool:
            return 4
        else:
            raise Exception(f"Invalid function type: {functype}")

    @staticmethod
    def fromint(functype):
        if functype == 0:
            return FunctionType.Direct
        elif functype == 1:
            return FunctionType.Thread
        elif functype == 2:
            return FunctionType.Process
        elif functype == 3:
            return FunctionType.ThreadPool
        elif functype == 4:
            return FunctionType.ProcessPool
        else:
            raise Exception(f"Invalid function type: {functype}")


def functionToMsg(func, loc: str, functype, fnid: uuid.UUID, remotedClassesCache, *args, **kwargs) -> bytes:
    # message type is the first byte
    msg = int.to_bytes(MessageType.FunctionCall, 1, "big")
    msg += int.to_bytes(FunctionType.toint(functype), 1, "big")
    # add the function ID to the message
    msg += fnid.bytes
    payload = encode_function_call(func, loc, remotedClassesCache, None, *args, **kwargs)
    msg += payload
    return msg


def msgToFunction(
    msg, conn, remotedClasses, callbackOnCacheAdd
) -> tuple[uuid.UUID, str, Callable | None, dict, tuple, dict, CodecError | None]:
    msgtype = msg[0]
    assert msgtype == MessageType.FunctionCall, f"Invalid message type: {msgtype}"
    functype = FunctionType.fromint(msg[1])
    # Get the function ID
    fnid = uuid.UUID(bytes=msg[2:18])
    payload = msg[18:]
    func_obj, funcargs, args, kwargs, decode_error = decode_function_call(
        payload, conn, remotedClasses, callbackOnCacheAdd
    )
    return fnid, functype, func_obj, funcargs, args, kwargs, decode_error


class Remoter:
    waituntillocation = False  # if true, will wait until at least one location available
    writeToReadyFile: tuple[str, str] | None = None  # if not None, will write a message once remoter is initialized

    @staticmethod
    def createemptyinstance():
        # empty instance of the class (for pylance)
        x = Remoter.__new__(Remoter)
        x.init = False
        return x

    def __init__(self, config: dict, host, port, sockpath, fixedrmtloc, rmtport, allowall, locconfig):
        # param init
        setparams(config)
        # config
        self.config = config
        # init
        self.init = True
        # finished
        self.finished = False
        # allowall functions
        self.allowall = allowall
        # message handler callback has signature: handleFn(msg, self.uid, *self.args, **self.kwargs)
        # host a function remoter on the given port
        # can start both if fnserver is different
        if os.environ.get("USE_UDP", "false").lower() in ["true", "1", "yes"]:
            logger.info("Starting UDP server for remoter communication")
            self.fnserverudp = msgudp.MessageServerUDP(
                host, port, self.initconn, partial(self.msgHandler, True, False, None), self.closeconn
            )
        else:
            self.fnserverudp = None
        if os.environ.get("USE_TCP", "true").lower() in ["true", "1", "yes"]:
            logger.info("Starting TCP server for remoter communication")
            self.fnservertcp = msgtcp.MessageServerTCP(
                host, port, self.initconn, partial(self.msgHandler, True, False, None), self.closeconn
            )
        else:
            self.fnservertcp = None
        self.port = port
        self.rmtport = rmtport
        # create the threadpool
        self.pool: ThreadPoolExecutor | None = None
        self.processPool: ProcessPoolExecutor | None = None
        self.poolLock = threading.Lock()
        # also host a queue processor
        self.fnqueue = Queue()
        self.fnqueueProc = msgqueue.QueueProc(self.fnqueue, partial(self.msgHandler, True, True, None))
        # also create a server for unix socket if sockpath is provided
        self.sockpath = sockpath
        if sockpath is not None:
            self.sockserver = msgunix.MessageServerUnix(
                sockpath, self.initconn, partial(self.msgHandler, True, False, None), self.closeconn
            )
            t = Thread(target=self.sockserver.serve_forever)
            t.daemon = True
            t.start()
        # ========
        self.fnlock = threading.RLock()  # results/events guarded by this lock
        self.results: dict[
            uuid.UUID, tuple[Any, Exception | None]
        ] = {}  # dictionary to hold the results of function calls
        self.events: dict[uuid.UUID, Any] = {}  # dictionary to hold the events for function calls
        self.tasks: dict[uuid.UUID, dict] = {}  # dictionary to hold the tasks (local or remote)
        self.tasksByName: dict[str, set[uuid.UUID]] = {}  # dictionary to hold the tasks by connection (location)
        self.runningTasks: dict[
            uuid.UUID, dict
        ] = {}  # dictionary to hold the running tasks (local or on behalf of remote client)
        self.remotedClasses: dict[uuid.UUID, dict] = {}  # remoted classes cache
        self.remotedClassesConn: dict[uuid.UUID, msgsock.Messenger] = {}  # remoted class connection on client side
        self.functionCount = {}
        # =========
        self.connlock = threading.Lock()  # conns/recvconn guarded by this lock
        self.conns = {}  # active connections to remote locations, key is host:port, value is Messenger
        self.recvconn = {}  # connections received by MessageServer
        # convert to a list of tuples to be used by random.choices
        self.fixedrmtloc = fixedrmtloc
        self.runloc = {}
        self.waitevents = {}
        self.loclock = threading.Lock()  # lock for runloc
        if locconfig is not None:
            logger.info(f"Using locconfig: {locconfig}")
            self.locconfig = rmtconfig.Config(locconfig, self.updateRunLoc)
            atexit.register(self.locconfig.stopwatch)
        else:
            logger.info("No locconfig provided to remoter")
        # start the function cleanup thread
        t = Thread(target=self.cleanupFunctions)
        t.daemon = True
        self.cleanupQueue = Queue()
        self.alreadyCleaned = set()  # to avoid cleaning up the same function multiple times
        t.start()
        # multiprocessing support
        if self.config.get("multiproc", False) or needmultiproc:
            self.mprunq = multiprocessing.Queue()
            self.mpresults = multiprocessing.Manager().dict()
            t = Thread(target=self.multiprocHandler)
            t.daemon = True
            t.start()
        # start the server
        if self.fnservertcp is not None:
            t = Thread(target=self.fnservertcp.serve_forever)
            t.daemon = True
            t.start()
            logger.info(f"Start TCP function server started on port {port}")
            self.serverThreadTCP = t
        if self.fnserverudp is not None:
            t = Thread(target=self.fnserverudp.serve_forever)
            t.daemon = True
            t.start()
            logger.info(f"Start UDP function server started on port {port}")
            self.serverThreadUDP = t
        if Remoter.writeToReadyFile is not None:
            filename, message = Remoter.writeToReadyFile
            if not os.path.exists(filename):
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(message)
            else:
                logger.warning(f"Ready file {filename} already exists -- not writing message")

    def stopremoter(self):
        self.finished = True

    def cancelTasksByName(self, taskname: str):
        # cancel all tasks with the given name
        with self.fnlock:
            if taskname in self.tasksByName:
                for uid in self.tasksByName[taskname]:
                    logger.info(f"Cancelling function {uid} with task name {taskname} due to user request")
                    self.cancelRemotedFunction(uid)

    def cancelTaskById(self, taskid: uuid.UUID):
        # cancel a task with the given id
        with self.fnlock:
            if taskid in self.tasks:
                logger.info(f"Cancelling function {taskid} due to user request")
                self.cancelRemotedFunction(taskid)

    def updateRunLoc(self, runlocconfig):
        with self.loclock:
            for k, v in runlocconfig.items():
                self.runloc[k] = {}
                self.runloc[k]["choices"] = list(v["locations"].keys())
                # change port to remote port in choices
                if self.rmtport is not None and self.rmtport != 0:
                    for i, host in enumerate(self.runloc[k]["choices"]):
                        if ":" in host:
                            host = host.split(":")[0]  # remove port if present
                            self.runloc[k]["choices"][i] = f"{host}:{self.rmtport}"
                        else:
                            self.runloc[k]["choices"][i] = f"{host}:{self.rmtport}"
                logger.debug(f"Run location {k} choices: {self.runloc[k]['choices']}")
                self.runloc[k]["weights"] = [float(v["locations"][k]) for k in v["locations"].keys()]  # noqa: SIM118 vendored from microsoft/xavier, not refactored
                if v.get(
                    "allowterminate", True
                ):  # and k not in remotedclasskey - even if it is, let it terminate so new class gets created
                    with self.fnlock:
                        tasks = self.tasksByName.get(k, set())
                        for uid in tasks:
                            loc = self.tasks[uid]["loc"]
                            if v["locations"].get(loc, 0.0) == 0.0:
                                logger.debug(f"Cancelling function {uid} at location {loc} due to runloc change")
                                self.cancelRemotedFunction(uid)  # cancel the function if its location is not allowed

    def onlyRunServer(self):
        # run the server only
        logger.print("Running only the server")
        if self.fnservertcp is not None:
            self.serverThreadTCP.join()
        if self.fnserverudp is not None:
            self.serverThreadUDP.join()

    def createThreadPool(self, max_workers=6):
        # create a thread pool for running functions
        if self.pool is None:  # initial check to avoid need for lock
            with self.poolLock:
                if self.pool is None:
                    self.pool = ThreadPoolExecutor(max_workers=max_workers)

    def createProcessPool(self, max_workers=6):
        # create a process pool for running functions
        if self.processPool is None:
            with self.poolLock:
                if self.processPool is None:
                    self.processPool = ProcessPoolExecutor(max_workers=max_workers)

    def initconn(self, msgr: msgsock.Messenger, sockkey: str):
        with self.connlock:
            if sockkey not in self.recvconn:
                self.recvconn[sockkey] = {
                    "key": sockkey,
                    "conn": msgr,
                    "fns": {},
                    "classes": set(),
                    "server": True,
                    "lock": threading.Lock(),
                    "alive": True,
                }

    def stopfn(
        self, uid: uuid.UUID, fn: dict, callback: Callable[[uuid.UUID, Any, Exception | None, dict | None], None] | None
    ):
        logger.debug(f"Stopping function with ID {uid} -- function: {fn}")
        if "thread" in fn:
            # stop the thread - this is a hack
            t: threading.Thread = fn["thread"]
            ctypes.pythonapi.PyThreadState_SetAsyncExc(t.native_id, ctypes.py_object(SystemExit))
        elif "process" in fn:
            p: Process = fn["process"]
            p.terminate()
        elif "threadpooltask" in fn:
            # stop the task
            task: Future = fn["threadpooltask"]
            task.cancel()
        elif "processpooltask" in fn:
            # stop the task
            task: Future = fn["processpooltask"]
            task.cancel()
        else:
            # else nothing to cancel
            logger.debug(f"No currently running cancellable task found for function with ID {uid}")
        # no result since function was stopped, only exception
        self.addForCleanup(uid, callback, None, FunctionStoppedException("Function stopped by user"), {"key": None})

    # closeconn does not receive a valid socket
    # this is on the server side
    def closeconn(self, msgr: msgsock.Messenger, sockkey: str):
        # print(f"Closing connection to {sock}")
        # key = self.socktokey(sock)
        logger.debug(f"Closing connection with key {sockkey}")
        with self.connlock:
            if sockkey in self.recvconn:
                # get the functions to stop & delete
                conn = self.recvconn[sockkey]
                del self.recvconn[sockkey]
            else:
                logger.error(f"Connection {sockkey} not found in recvconn")
                return
        with conn["lock"]:
            conn["alive"] = False
            for fnid, fn in conn["fns"].items():
                logger.debug(f"Stopping function {fnid} -- {fn} since connection closed")
                self.stopfn(fnid, fn, None)
            for classuid in conn["classes"]:
                logger.debug(f"Deallocating class with ID {classuid} since connection closed")
                self.addClassForCleanup(classuid)

    def setfnevent(self, fnid: uuid.UUID):
        # self.events[fnid].set()
        if fnid not in self.events:
            return
        if isinstance(self.events[fnid], tuple):
            ev, loop = self.events[fnid]
            assert isinstance(ev, asyncio.Event)
            loop.call_soon_threadsafe(ev.set)
            return ev
        else:
            self.events[fnid].set()
            return self.events[fnid]

    # this is on the client side
    def closeclientconn(self, loc: str, msgr: msgsock.Messenger, sockkey: str):
        with self.connlock:
            if loc in self.conns:
                # remove the connection from the conns dictionary
                conn = self.conns[loc]
                del self.conns[loc]
            else:
                return
        with conn["lock"]:
            conn["alive"] = False
            # remove the connection from the recvconn dictionary
            for fnid in conn["fnuid"]:
                self.setfnevent(fnid)
                # but keep the result not present in the dictionary so we know the function was not completed
            for classuid, obj in conn["classes"].items():
                obj.failed_rmt0bf = True
                self.remotedClassesConn.pop(classuid, None)

    def runfunc(self, func, *args, **kwargs):
        # get the function object
        if hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        if inspect.iscoroutinefunction(func):  # noqa: SIM108 vendored from microsoft/xavier, not refactored
            result = asyncio.run(func(*args, **kwargs))
        else:
            result = func(*args, **kwargs)
        return result

    def cleanupFunctions(self):
        while True:
            cleanup, args = self.cleanupQueue.get()
            if cleanup == "fn":
                fnid, callback, result, ex, funcargs = args  # unpack the tuple
                logger.debug(f"Cleaning up function with ID {fnid}")
                # remove from the running tasks
                with self.fnlock:
                    self.runningTasks.pop(fnid, None)
                if callback is not None:
                    callback(fnid, result, ex, funcargs)
            elif cleanup == "class":
                (classuid,) = args  # unpack the tuple
                logger.debug(f"Cleaning up class with ID {classuid}")
                with self.fnlock:
                    if classuid in self.remotedClasses:
                        logger.info(f"Removing class with ID {classuid}")
                        # remove the class from the remoted classes
                        obj = self.remotedClasses[classuid]
                        remotedclassmetadata.pop(obj, None)
                        del self.remotedClasses[classuid]

    def removeFromCleanup(self, fnid: uuid.UUID):
        with self.fnlock:
            self.alreadyCleaned.discard(fnid)

    def addForCleanup(
        self,
        fnid: uuid.UUID,
        callback: Callable[[uuid.UUID, Any, Exception | None, dict | None], None] | None,
        result: Any,
        ex: Exception | None,
        funcargs: dict,
    ):
        with self.fnlock:
            if fnid in self.alreadyCleaned or fnid not in self.runningTasks:
                # already cleaned up this function
                logger.debug("Already cleaned up function with ID " + str(fnid))
                return
            logger.debug("Adding function with ID " + str(fnid) + " for cleanup")
            self.alreadyCleaned.add(fnid)
            self.cleanupQueue.put(("fn", (fnid, callback, result, ex, funcargs)))
            threading.Timer(
                5.0, self.removeFromCleanup, args=(fnid,)
            ).start()  # remove from cleanup after 5 seconds if not cleaned up

    def addClassForCleanup(self, classuid: uuid.UUID):
        with self.fnlock:
            if classuid in self.alreadyCleaned or classuid not in self.remotedClasses:
                # already cleaned up this class
                logger.debug("Already cleaned up class with ID " + str(classuid))
                return
            logger.debug("Adding class with ID " + str(classuid) + " for cleanup")
            self.alreadyCleaned.add(classuid)
            self.cleanupQueue.put(("class", (classuid,)))
            threading.Timer(
                5.0, self.removeFromCleanup, args=(classuid,)
            ).start()  # remove from cleanup after 5 seconds if not cleaned up

    def funcWrap(
        self,
        fnid: uuid.UUID,
        func: Callable,
        funcargs: dict,
        callback: Callable[[uuid.UUID, Any, Exception | None], None],
        *args,
        **kwargs,
    ) -> None:
        global allowed_print
        if not allowed_print:
            # print the allowed functions only once
            logger.debug(f"Allowed functions: {allowed_functions}")
            logger.debug(f"Remoted class keys: {remotedclasskey}")
            allowed_print = True
        try:
            key, module_name, func_name, class_name = getfuncname(func)  # noqa: RUF059 vendored from microsoft/xavier, not refactored
            logger.debug(f"Running function with ID {fnid} -- function: {key}")
            if key not in allowed_functions and not self.allowall:
                logger.error(f"Function {key} is not allowed to be called remotely")
                raise Exception(f"Function {key} is not allowed to be called remotely")
            result = self.runfunc(func, *args, **kwargs)
            logger.debug(f"Function with ID {fnid} completed execution")
            if funcargs["func_name"] == "__init__":  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                if len(args) > 0 and type(args[0]) in remotedclasses:
                    assert result is None, "__init__ should not return a value"
                    result = args[0].uuid_rmt0bf
            ex = None
        except Exception as e:
            # if len(args) > 0:
            #     logger.error(f"Args0: {args[0].__dict__}", color="red")
            logger.error(f"Error running function {fnid}\n{funcargs}\n -- {e}\n{traceback.format_exc()}", color="red")
            result = None
            ex = e
        if getparam("noresultprint", None, None, True):
            logger.info(f"Function {fnid} {func} completed with exception: {ex} -- is_process: {is_process}")
        else:
            logger.info(
                f"Function {fnid} {func} completed with result: {result} -- exception: {ex} -- is_process: {is_process}"
            )
        if is_process and mprunq is not None:
            mprunq.put({"type": "addforcleanup", "fnid": fnid, "result": result, "ex": ex})
        else:
            self.addForCleanup(fnid, callback, result, ex, funcargs)

    def funcWrapProc(self, _mprunq, _mpresults, *args, **kwargs) -> None:
        global allowed_print
        global is_process
        global mprunq
        global mpresults
        is_process = True
        allowed_print = False
        mprunq = _mprunq
        mpresults = _mpresults
        self.funcWrap(*args, **kwargs)

    def getrunloc(self, taskname, actclasskey, key, *args) -> tuple[str, uuid.UUID | None]:
        isremotedclass, classuid = self.isremotedclass(args)
        if isremotedclass and args[0].failed_rmt0bf:
            raise Exception("Class is in a failed state -- cannot run function")

        for arg in args:
            # if a location set, use that location & set the location for the first argument if it is a remoted class
            if localhasattr(arg, "rmtloc_rmt0bf") and arg.rmtloc_rmt0bf is not None:
                # if any of the remoted class arguments has a location set, use that location
                logger.debug(
                    f"Using remoted class argument location {arg.rmtloc_rmt0bf} for function {taskname} - classuid: {classuid}"  # noqa: E501 vendored from microsoft/xavier, not refactored
                )
                if isremotedclass:
                    args[
                        0
                    ].rmtloc_rmt0bf = (
                        arg.rmtloc_rmt0bf
                    )  # for remoted classes, set the location so that it can be used later
                return arg.rmtloc_rmt0bf, classuid
        if Remoter.waituntillocation:
            while True:
                with self.loclock:
                    if taskname in self.runloc and len(self.runloc[taskname]["choices"]) > 0:
                        break
                logger.debug(f"Waiting for location to be available for task {taskname}")
                time.sleep(2)
        with self.loclock:
            if taskname in self.runloc:
                # print(self.runloc[taskname]['choices'], self.runloc[taskname]['weights'])
                if len(self.runloc[taskname]["choices"]) == 0:
                    loc = "direct"
                else:
                    loc = random.choices(self.runloc[taskname]["choices"], self.runloc[taskname]["weights"])[0]
            else:
                loc = "direct"
            # if rmtloc is set
            if self.fixedrmtloc is not None and self.fixedrmtloc != "":
                loc = self.fixedrmtloc
            if key in fixedlocs:
                loc = fixedlocs[key]
            if len(args) > 0:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                if type(args[0]) in remotedclasskey:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                    if key in ["remoter.rmtclass//objgetattr", "remoter.rmtclass//objsetattr"]:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                        if remotedclasskey[type(args[0])] in fixedlocs:
                            loc = fixedlocs[remotedclasskey[type(args[0])]]
        loc = modifyloc(loc, key, actclasskey)
        if isremotedclass:
            assert len(args) > 0, "Remoted class method must have at least one argument"
            args[0].rmtloc_rmt0bf = loc  # for remoted classes, set the location so that it can be used later
        logger.debug(
            f"taskname: {taskname} -- isremotedclass: {isremotedclass} -- classuid: {classuid} -- location: {loc}"
        )
        return loc, classuid

    def getconn(self, loc: str, uid: uuid.UUID, classuid: uuid.UUID | None, obj):
        # get the connection to the server
        with self.connlock:
            if loc not in self.conns:
                # create a new connection to the server
                protocol, addr = loc.split("://")  # noqa: RUF059 vendored from microsoft/xavier, not refactored
                handlefn = partial(self.msgHandler, False, False, loc)

                def closefn(msgr, sockkey):
                    return self.closeclientconn(loc, msgr, sockkey)

                if protocol == "unix":
                    conn = msgunix.MessengerUnix(self.sockpath, None, loc, None, handlefn, closefn)
                elif protocol == "udp":
                    conn = msgudp.MessengerUDP(None, loc, False, None, handlefn, closefn)
                elif protocol == "tcp":
                    conn = msgtcp.MessengerTCP(None, loc, None, handlefn, closefn)
                else:
                    raise Exception(f"Invalid protocol: {protocol}")
                self.conns[loc] = {
                    "key": loc,
                    "conn": conn,
                    "fnuid": set(),
                    "classes": {},
                    "server": False,
                    "lock": threading.Lock(),
                    "alive": True,
                }
                logger.info(f"Created new connection to location {loc} for function with ID {uid}", color="green")
            else:
                conn = self.conns[loc]["conn"]
            self.conns[loc]["fnuid"].add(uid)
            if classuid is not None:
                self.conns[loc]["classes"][classuid] = obj
        return conn

    def cancelRemotedFunction(self, uid: uuid.UUID):
        # send cancellation message to the server
        with self.fnlock:
            if uid in self.tasks:
                taskinfo = self.tasks[uid]
            else:
                raise Exception(f"Function {uid} not found in tasks")
            runningtask = self.runningTasks.get(uid, None)

        loc = taskinfo["loc"]
        logger.debug(
            f"Sending cancellation message to connection {loc} for function with ID {uid} -- running task: {runningtask}"  # noqa: E501 vendored from microsoft/xavier, not refactored
        )
        if loc == "direct" and runningtask is not None:
            # stop the function locally
            self.stopfn(uid, runningtask, self.qcallback)
        else:
            msg = int.to_bytes(MessageType.FunctionCancel, 1, "big")
            msg += uid.bytes
            if loc == "directqueue":
                # send the message to the queue
                self.fnqueueProc.putMessage(msg)
            else:
                with self.connlock:
                    conn = self.conns.get(loc, None)
                if conn is not None:
                    conn["conn"].senddata(msg)

    def deallocateClass(self, obj):
        _, classuid = self.isremotedclass((obj,))
        if classuid is not None:
            logger.debug(f"Deallocating class with ID {classuid}")
            loc = obj.rmtloc_rmt0bf
            if loc == "direct":
                self.addClassForCleanup(classuid)
            else:
                msg = int.to_bytes(MessageType.DeallocateClass, 1, "big")
                msg += classuid.bytes
                if loc == "directqueue":
                    # send the message to the queue
                    self.fnqueueProc.putMessage(msg)
                else:
                    conn = self.remotedClassesConn.get(classuid, None)
                    if conn is not None:
                        conn.senddata(msg)

    def initrmtclassonclient(self, *args):
        if len(args) > 0:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
            if type(args[0]) in remotedclasses:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                if not localhasattr(args[0], "uuid_rmt0bf") or args[0].uuid_rmt0bf is None:  # not init yet
                    initfields(args[0])
                    logger.debug(f"Initializing remote class on client with id {args[0].uuid_rmt0bf}")

    def islocself(self, loc: str) -> bool:
        if loc == "direct":
            return True
        if self.fnservertcp and self.fnservertcp.isself(loc):
            return True
        if self.fnserverudp and self.fnserverudp.isself(loc):
            return True
        if hasattr(self, "sockserver") and self.sockserver.isself(loc):  # noqa: SIM103 vendored from microsoft/xavier, not refactored
            return True
        return False

    def runRemotedfunction(
        self, taskname, functype, nowait, fixedloc, func, *args, **kwargs
    ) -> tuple[bool, uuid.UUID, asyncio.Event | threading.Event]:

        self.initrmtclassonclient(*args)
        key, module_name, func_name, class_name = getfuncname(func)
        isremotedclass, classuid = self.isremotedclass(args)
        if isremotedclass:
            actclasskey = f"{args[0].__class__.__module__}/{args[0].__class__.__name__}"
            cls = type(args[0])
            if not cls.remoteable_rmt0bf:
                assert False, "Single instance non-remoteable classes cannot run remoted functions"  # noqa: B011 vendored from microsoft/xavier, not refactored
        else:
            actclasskey = None
        loc, classuid = self.getrunloc(taskname, actclasskey, key, *args)
        if fixedloc is not None:
            loc = fixedloc  # override the location if fixedloc is provided
        isasync = inspect.iscoroutinefunction(func)
        uid = uuid.uuid4()
        taskinfo = {"loc": loc, "func_name": func_name, "args": args}
        logger.info(
            f"===Running remoted function {key} with ID {uid} -- classuid {classuid} -- location: {loc} -- async: {isasync}===="  # noqa: E501 vendored from microsoft/xavier, not refactored
        )
        if key in ["remoter.rmtclass//objgetattr", "remoter.rmtclass//objsetattr"]:
            logger.info(f"Getting attribute {args[1]} of remoted class {args[0].uuid_rmt0bf} of tpe {type(args[0])}")
        if isasync:
            # create an async event which can be awaited
            event = asyncio.Event()
            # print(asyncio.get_running_loop())
            with self.fnlock:
                self.events[uid] = (event, asyncio.get_running_loop())
                self.tasks[uid] = taskinfo
                self.tasksByName.setdefault(taskname, set()).add(uid)
        else:
            event = threading.Event()
            with self.fnlock:
                self.events[uid] = event
                self.tasks[uid] = taskinfo
                self.tasksByName.setdefault(taskname, set()).add(uid)
        if self.islocself(loc):
            # round trip through dehydration/rehydration to simulate remote call
            argsn, kwargsn = dehydrate_args(key, args, kwargs, self.remotedClasses, loc, None)
            argsn, kwargsn = rehydrate_args(argsn, kwargsn, self.remotedClasses, loc, None)
            funcargs = {
                "key": key,
                "loc": loc,
                "module_name": module_name,
                "func_name": func_name,
                "class_name": class_name,
            }
            if getparam("direct2direct", key, actclasskey, not isasync and functype == FunctionType.ThreadPool):
                logger.debug(f"Running function {key} directly without threadpool")
                functype = FunctionType.Direct
            logger.debug(f"Calling Direct Funcname = {func_name} key = {key}")
            self.callfunction(functype, uid, func, funcargs, self.qcallback, loc, *argsn, **kwargsn)
            return True, uid, event
        else:
            # serialize the function and arguments
            msg = functionToMsg(func, loc, functype, uid, self.remotedClasses, *args, **kwargs)
            if loc == "directqueue":
                logger.debug(f"Sending function {taskname} to queue with ID {uid} - msglen: {len(msg)}")
                self.fnqueueProc.putMessage(msg)
                return True, uid, event
            else:
                if isremotedclass and classuid in self.remotedClassesConn:
                    assert classuid is not None
                    logger.debug(f"Reuse connection for class {classuid} with ID {uid}")
                    conn = self.remotedClassesConn[classuid]
                else:
                    if isremotedclass:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
                        obj = args[0]
                    else:
                        obj = None
                    conn = self.getconn(loc, uid, classuid, obj)
                if isremotedclass:
                    assert classuid is not None
                    assert not args[0].failed_rmt0bf, "Class is in a failed state -- cannot run function"
                    with self.fnlock:
                        if classuid not in self.remotedClassesConn:
                            self.remotedClassesConn[classuid] = conn
                # send the message to the server
                success = conn.senddata(msg)
                return success, uid, event

    def getResult(self, taskname, uid) -> tuple[Any, Exception | None]:
        with self.fnlock:
            taskinfo = self.tasks.pop(uid, None)
            if taskname in self.tasksByName:
                self.tasksByName[taskname].discard(uid)
            if uid in self.results:
                result, ex = self.results[uid]  # result, exception tuple
                logger.debug(
                    f"Getting result for function {taskname} - {taskinfo['func_name']} - with uid {uid} of type {type(result)}"  # noqa: E501 vendored from microsoft/xavier, not refactored
                )
                del self.results[uid]
                del self.events[uid]
                if taskinfo is not None:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                    if len(taskinfo["args"]) > 0 and taskinfo["func_name"] == "__init__":  # noqa: SIM102 vendored from microsoft/xavier, not refactored
                        if type(taskinfo["args"][0]) in remotedclasses:
                            if result != taskinfo["args"][0].uuid_rmt0bf:
                                logger.info(
                                    f"Overwriting uuid of remoted class instance to {result} "
                                    f"-- old {taskinfo['args'][0].uuid_rmt0bf}",
                                    color="cyan",
                                )
                            taskinfo["args"][0].uuid_rmt0bf = result
                            result = None
                # logger.info("Result for function {} with uid {}: {}".format(taskname, uid, result))
                if result is not None and localhasattr(result, "uuid_rmt0bf"):
                    logger.debug("Result class uuid: " + str(result.uuid_rmt0bf))
                return result, ex
            else:
                raise Exception(f"Function {taskname} with uid {uid} not found in results - perhaps connection closed")

    def multiprocHandler(self):
        while True:
            ret = self.mprunq.get()
            if ret["type"] == "addforcleanup":
                fnid = ret["fnid"]
                result = ret["result"]
                ex = ret["ex"]
                assert fnid in self.runningTasks
                callback = self.runningTasks[fnid]["callback"]
                funcargs = self.runningTasks[fnid]["funcargs"]
                self.addForCleanup(fnid, callback, result, ex, funcargs)
            elif ret["type"] == "runsyncfunction":
                uid, event, args, kwargs = ret["args"]
                try:
                    res = self.runSyncFunctionProc(*args, **kwargs)
                    self.mpresults[uid] = (res, None)
                except Exception as e:
                    self.mpresults[uid] = (None, e)
                event.set()
            else:
                assert False, f"Unknown multiprocHandler message type: {ret['type']}"  # noqa: B011 vendored from microsoft/xavier, not refactored

    async def runAsyncFunction(self, taskname, functype, nowait, timeout, loc, func, *args, **kwargs):
        global is_process
        if is_process:
            raise Exception("Cannot run async function from within a process pool")
        success, uid, event = self.runRemotedfunction(taskname, functype, nowait, loc, func, *args, **kwargs)
        if not success:
            raise Exception(f"Function {taskname} with uid {uid} not found in results - perhaps connection closed")
        event: asyncio.Event = event
        logger.debug(f"Waiting for event with hash {hash(event)}")
        if timeout is not None:
            try:
                await asyncio.wait_for(event.wait(), timeout)
            except TimeoutError:
                self.cancelRemotedFunction(uid)
                raise Exception(f"Function {taskname} with uid {uid} timed out after {timeout} seconds")  # noqa: B904 vendored from microsoft/xavier, not refactored
        else:
            await event.wait()
        logger.debug(f"Event with hash {hash(event)} set")
        result, ex = self.getResult(taskname, uid)
        if ex is None:
            return result
        else:
            raise ex

    def runSyncFunctionProc(self, *args, **kwargs):
        # add to the multiprocessing queue
        event = multiprocessing.Manager().Event()
        uid = uuid.uuid4()
        mprunq.put({"type": "runsyncfunction", "args": (uid, event, args, kwargs)})
        event.wait()
        result, ex = mpresults.pop(uid, (None, Exception("Function did not complete")))
        if ex is None:
            return result
        else:
            raise ex

    def runSyncFunction(self, taskname, functype, nowait, timeout, loc, func, *args, **kwargs):
        if is_process:
            return self.runSyncFunctionProc(taskname, functype, nowait, loc, func, *args, **kwargs)
        success, uid, event = self.runRemotedfunction(taskname, functype, nowait, loc, func, *args, **kwargs)
        if not success:
            raise Exception(f"Function {taskname} with uid {uid} not found in results - perhaps connection closed")
        event: threading.Event = event
        if timeout is not None:
            completed = event.wait(timeout)
            if not completed:
                # cancel the function since it timed out
                self.cancelRemotedFunction(uid)
                raise Exception(f"Function {taskname} with uid {uid} timed out after {timeout} seconds")
        else:
            event.wait()
        result, ex = self.getResult(taskname, uid)
        # try:
        #     print("Result type:", type(result))
        #     print("ResultDict:", result.__dict__)
        # except Exception:
        #     pass
        if ex is None:
            return result
        else:
            raise ex

    def runfunctionrmt(self, taskname, functype, nowait, timeout, fixedloc, func, *args, **kwargs):
        # run a function on a remote server
        if inspect.iscoroutinefunction(func):
            return self.runAsyncFunction(taskname, functype, nowait, timeout, fixedloc, func, *args, **kwargs)
        else:
            return self.runSyncFunction(taskname, functype, nowait, timeout, fixedloc, func, *args, **kwargs)

    def isremotedclass(self, args) -> tuple[bool, uuid.UUID | None]:
        if len(args) > 0 and localhasattr(args[0], "uuid_rmt0bf"):
            return True, args[0].uuid_rmt0bf
        else:
            return False, None

    def modifycall_singleinstance_init(self, args0, fnid, orig_callback, func):
        initlock, _, initstate, initevent = singleinstanceclassinstance[classkey(type(args0))]

        def callbackinit(*args, **kwargs):
            initstate["state"] = "initialized"
            initevent.set()
            logger.debug(
                f"Initialization complete for single instance class {args0.__class__} with ID {fnid} - set event"
            )
            orig_callback(*args, **kwargs)

        def funcinit(*args, **kwargs):
            initevent.wait()  # wait for the class to be initialized and do no initialization

        with initlock:
            if initstate["state"] == "notinit":
                initstate["state"] = "initializing"
                logger.debug(f"Initializing single instance class {args0.__class__} with ID {fnid}")
                callback = callbackinit  # this will do the initialization and set the event
                # func remains the same, it will be called with the same arguments
            else:
                logger.debug(f"Using already initialized single instance class {args0.__class__} with ID {fnid}")
                func = funcinit  # this will do nothing and wait for the event to be set
                allowed_functions.add("remoter.remoter/Remoter/modifycall_singleinstance_init")
                callback = orig_callback
                # callback remains the same, it will be called with the same arguments
        return callback, func

    def modifycall_singleinstance(self, key, orig_callback, orig_func):
        initlock, _, initstate, initevent = singleinstancefunc[key]

        # logger.debug(f"Single instance function -- remotedClasses{self.remotedClasses}")
        def callbackfirst(*args, **kwargs):
            result = args[1]
            ex = args[2]
            with initlock:
                singleinstancefunc[key] = (initlock, (result, ex), initstate, initevent)
            initevent.set()
            if getparam("noresultprint", None, None, True):
                logger.debug(f"Single instance function {key} completed - set event")
            else:
                logger.debug(f"Single instance function {key} completed - set event -- result: {result}")
            orig_callback(*args, **kwargs)

        def funcnotfirst(*args, **kwargs):
            initevent.wait()  # wait for the class to be initialized and do no initialization
            with initlock:
                _, res, _, _ = singleinstancefunc[key]
                result, ex = res
                if getparam("noresultprint", None, None, True):
                    logger.debug(f"Using already initialized single instance function for {key}")
                else:
                    logger.debug(f"Using already initialized single instance function for {key} - result: {result}")
                if ex is not None:
                    raise ex
                return result

        with initlock:
            if initstate["state"] == "notinit":
                initstate["state"] = "init"
                logger.debug(f"Initializing single instance function for {key}")
                callback = callbackfirst  # this will do the initialization and set the event
                func = orig_func  # this will do the same arguments and set arguments
                # func remains the same, it will be called with the same arguments
            else:
                logger.debug(f"Using already initialized single instance function for {key}")
                func = funcnotfirst  # this will do nothing and wait for the event to be set
                allowed_functions.add("remoter.remoter/Remoter/modifycall_singleinstance")
                callback = orig_callback
                # callback remains the same, it will be called with the same arguments
        return callback, func

    # ideally should really only use one of following:
    # 1. Direct for quick guaranteed non-blocking calls
    # 2. Threadpooltask for longer blocking calls that can be parallelized
    # 3. Process for heavy CPU bound tasks that need isolation - and may have internal threading
    # Dangers of Thread or ProcessPoolTask
    # - these are non-cancellable once started - thread cannot be reliably cancelled, process pool task cannot be cancelled once started  # noqa: E501 vendored from microsoft/xavier, not refactored
    # - only use thread for windows since function pickling is an issue
    def callfunction(
        self,
        functype,
        fnid: uuid.UUID,
        func: Callable,
        funcargs: dict,
        callback: Callable[[uuid.UUID, Any, Exception | None, dict], None],
        loc: str | None,
        *args,
        **kwargs,
    ) -> tuple[dict, uuid.UUID | None]:
        if hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        key, _, funcname, _ = getfuncname(func)
        # check if function's first argument is a remoted class
        isremotedclass, classuid = self.isremotedclass(args)
        callargs = args
        if (funcname == "__init__") and isremotedclass and (classkey(type(args[0])) in singleinstanceclass):
            orig_callback = callback
            callback, func = self.modifycall_singleinstance_init(args[0], fnid, orig_callback, func)
        if key in singleinstancefunc:
            orig_callback = callback
            orig_func = func
            callback, func = self.modifycall_singleinstance(key, orig_callback, orig_func)
        # print(callargs, kwargs)
        logger.debug(
            f"==Calling function {key} with ID {fnid} -- classuid {classuid} -- functype: {functype} -- loc: {loc}"
        )
        # before starting the function, put empty in for runningTasks so that there is something there
        with self.fnlock:
            self.functionCount[key] = self.functionCount.get(key, 0) + 1
            # print if multiple of 10 calls
            if self.functionCount[key] % 10 == 0:
                logger.print(f"Function {key} has been called {self.functionCount[key]} times")
            self.runningTasks[fnid] = {"callback": callback, "funcargs": funcargs}
        # call the function
        if functype == FunctionType.Direct:
            # run the function directly
            self.funcWrap(fnid, func, funcargs, callback, *callargs, **kwargs)
            ret = {}
        elif functype == FunctionType.Thread:
            # run the function in a thread
            t = Thread(target=self.funcWrap, args=(fnid, func, funcargs, callback, *callargs), kwargs=kwargs)
            t.daemon = True  # terminate the thread when the parent process exits
            t.start()
            ret = {"thread": t}
        elif functype == FunctionType.Process:
            # run the function in a process
            p = Process(
                target=self.funcWrapProc,
                args=(self.mprunq, self.mpresults, fnid, func, funcargs, callback, *callargs),
                kwargs=kwargs,
            )
            p.daemon = True  # terminate the process when the parent process exits
            p.start()
            ret = {"process": p}
        elif functype == FunctionType.ThreadPool:
            self.createThreadPool()
            assert self.pool is not None
            task = self.pool.submit(self.funcWrap, fnid, func, funcargs, callback, *callargs, **kwargs)
            ret = {"threadpooltask": task}
        elif functype == FunctionType.ProcessPool:
            self.createProcessPool()
            assert self.processPool is not None
            task = self.processPool.submit(
                self.funcWrapProc, self.mprunq, self.mpresults, fnid, func, funcargs, callback, *callargs, **kwargs
            )
            ret = {"processpooltask": task}
        else:
            raise Exception(f"Invalid function type: {functype}")

        with self.fnlock:
            if fnid in self.runningTasks:
                self.runningTasks[fnid].update(ret)  # else already removed in case function completed quickly
        return ret, classuid

    # extract a function from the message, call it, then call the callback
    # callback is a function to call when the function is complete
    def execfunction(
        self, msg: bytes, callback: Callable[[uuid.UUID, Any, Exception | None], None], conn
    ) -> tuple[uuid.UUID, uuid.UUID | None, dict]:

        # get the function object and arguments
        # print(msg)
        fnid, functype, func_obj, funcargs, args, kwargs, decode_error = msgToFunction(
            msg, conn, self.remotedClasses, partial(self.addclasstoconn, conn)
        )
        if decode_error is not None:
            logger.error(f"Failed to decode function call with ID {fnid}: {decode_error}")
            callback(fnid, None, decode_error, funcargs)
            return fnid, None, {}
        assert func_obj is not None
        ret, classuid = self.callfunction(functype, fnid, func_obj, funcargs, callback, None, *args, **kwargs)
        return fnid, classuid, ret

    # qcallback is on result path
    def qcallback(self, fnid: uuid.UUID, result: Any, ex: Exception | None, funcargs: dict):
        with self.fnlock:
            if result is not None and funcargs["loc"] in ["direct", "directqueue"]:
                # go through dehydrate/rehydrate cycle to simulate remote
                logger.debug("========= DEHYDRATE/REHYDRATE CYCLE FOR DIRECT RESULT =========" + str(type(result)))
                result = dehydrate(result, funcargs["key"], self.remotedClasses, funcargs["loc"], True, None)
                result = rehydrate(result, self.remotedClasses, funcargs["loc"], True, None)
            self.results[fnid] = (result, ex)  # the result will have dehydrated classes only
            ev = self.setfnevent(fnid)
            if getparam("noresultprint", None, None, True):
                logger.debug(f"Function completed with ID {fnid} -- setevent with hash {hash(ev)}")
            else:
                logger.debug(f"Function completed with ID {fnid} -- result: {result} -- setevent with hash {hash(ev)}")

    def sendResult(self, msgr: msgsock.Messenger, conn, fnid, result: Any, ex: Exception | None, funcargs: dict):
        # send the result back to the client
        logger.debug(f"Sending result for function {fnid} -- funckey: {funcargs['key']}")
        msg = int.to_bytes(MessageType.FunctionResult, 1, "big")
        msg += fnid.bytes
        try:
            payload = self.encode_result(funcargs, result, ex, partial(self.addclasstoconn, conn))
        except Exception as serialization_error:
            logger.error(f"Failed to encode result for function {fnid}: {serialization_error}", exc_info=True)
            try:
                error_payload = RemoteErrorDescriptor(
                    type_name=type(serialization_error).__name__,
                    message=str(serialization_error),
                    traceback=traceback.format_exc(),
                )
                payload = serialize_payload(({"key": "unknown", "loc": "unknown"}, None, error_payload))
            except Exception:
                logger.error(f"Failed to encode serialization error for function {fnid}", exc_info=True)
                payload = _RESULT_SERIALIZATION_FAILURE_PAYLOAD
        msg += payload
        # send the message to the client
        logger.info(f"Sending result message of length {len(msg)} for function {fnid}")
        msgr.senddata(msg)
        # remove the function from the connection
        with conn["lock"]:
            if fnid in conn["fns"]:
                del conn["fns"][fnid]  # function is complete
            else:
                conn["fns"][fnid] = (
                    None  # function is complete but happens before the function is added to the connection
                )

    def addclasstoconn(self, conn, obj):
        classuid = obj.uuid_rmt0bf
        if conn is not None:
            with conn["lock"]:
                if conn["alive"]:
                    if classuid in singleinstanceclassids:
                        logger.info(f"Not adding single instance class {classuid} to connection")
                    else:
                        logger.info(f"Adding class {classuid} to connection")
                        conn["classes"].add(classuid)
                elif classuid is not None:
                    self.addClassForCleanup(classuid)

    def encode_result(self, funcargs: dict, result: Any, ex: Exception | None, callbackOnCacheAdd) -> bytes:
        ex_payload: RemoteErrorDescriptor | None = None
        if ex is not None:
            ex_payload = RemoteErrorDescriptor(
                type_name=type(ex).__name__,
                message=str(ex),
                traceback=traceback.format_exc() if ex.__traceback__ is not None else "",
            )
        if result is None:
            return serialize_payload((funcargs, None, ex_payload))
        resn = dehydrate(result, funcargs["key"], self.remotedClasses, funcargs["loc"], True, callbackOnCacheAdd)
        logger.debug(f"Funcargs: {funcargs}, Dehydrated result: {resn} Exception: {ex_payload}")
        return serialize_payload((funcargs, resn, ex_payload))

    def decode_result(self, payload: bytes, loc: str, conn) -> tuple[dict, Any, Exception | None]:
        logger.debug(f"Decoding result of length {len(payload)}")
        if not payload:
            raise CodecError("Empty payload received for function result")
        decoded = deserialize_payload(payload)
        if not isinstance(decoded, tuple) or len(decoded) != 3:
            raise CodecError("Function result payload must contain three fields")
        funcargs, result, ex_payload = decoded
        if not isinstance(funcargs, dict):
            raise CodecError("Function result metadata must be a mapping")
        try:
            result = rehydrate(result, self.remotedClasses, loc, True, None)
        except Exception:
            logger.error("Error rehydrating result", exc_info=True)
            raise
        ex: Exception | None
        if ex_payload is None:
            ex = None
        elif isinstance(ex_payload, RemoteErrorDescriptor):
            if ex_payload.type_name == "AttributeError":
                ex = AttributeError(ex_payload.message)
            else:
                ex = RemoteExecutionError(ex_payload)
        else:
            ex = RemoteExecutionError(
                RemoteErrorDescriptor(
                    type_name="RemoteError",
                    message=str(ex_payload),
                    traceback="",
                )
            )
        if getparam("noresultprint", None, None, True):
            logger.debug(f"Decoded result for function {funcargs['key']} -- exception: {ex}")
        else:
            logger.debug(f"Decoded result: {result}, exception: {ex}")
        return funcargs, result, ex

    def unpackResult(self, message, loc, conn):
        # get the function ID
        fnid = uuid.UUID(bytes=message[1:17])
        payload = message[17:]
        try:
            funcargs, result, ex = self.decode_result(payload, loc, conn)
        except Exception as exc:
            logger.error(f"Failed to decode result for function {fnid}", exc_info=True)
            funcargs = {"key": "unknown", "loc": loc}
            result = None
            ex = RemoteExecutionError(
                RemoteErrorDescriptor(
                    type_name="ResultDecodeError",
                    message=str(exc),
                    traceback=traceback.format_exc(),
                )
            )
        # set the event to unblock the waiting thread
        self.qcallback(fnid, result, ex, funcargs)

    # self.handleFn(msg, self.uid, *self.args, **self.kwargs)
    def msgHandler(self, ismsgserver, fromqueue, loc, message: bytes, _, sockkey: str):
        logger.debug(f"Handle function message from {sockkey} -- message length: {len(message)}")
        if not fromqueue:
            with self.connlock:
                if ismsgserver:
                    # get the connection from the recvconn dictionary
                    assert sockkey is not None
                    conn = self.recvconn[sockkey]
                else:
                    # get the connection from the conns dictionary
                    conn = self.conns[loc]
                msgr: msgsock.Messenger = conn["conn"]
            callback = partial(self.sendResult, msgr, conn)
        else:
            conn = None
            callback = self.qcallback

        msgtype = message[0]
        if msgtype == MessageType.FunctionCall:
            # handle function call message
            fnid, classuid, fn = self.execfunction(message, callback, conn)
            logger.debug(f"Function call executed with ID {fnid} - classuid: {classuid} - sockkey: {sockkey}")
            if conn is not None and fn != {}:
                with conn["lock"]:
                    if conn["alive"]:
                        if fnid in conn["fns"]:
                            assert conn["fns"][fnid] is None, f"Function {fnid} already in connection??"
                            # remove the function from the connection
                            del conn["fns"][fnid]
                        else:
                            conn["fns"][fnid] = fn
                    else:
                        self.stopfn(fnid, fn, None)
        elif msgtype == MessageType.FunctionResult:
            # handle function result message - only come here if (loc != direct) and (loc != directqueue)
            assert loc != "direct" and loc != "directqueue", (
                "Function result message received for direct or directqueue"
            )
            self.unpackResult(message, loc, conn)
        elif msgtype == MessageType.FunctionCancel:
            # handle function cancel message
            fnid = uuid.UUID(bytes=message[1:17])
            with self.fnlock:
                runningtask = self.runningTasks.get(fnid, None)
            logger.debug(f"Cancel function with ID {fnid} -- running task: {runningtask}")
            if runningtask is not None:
                self.stopfn(fnid, runningtask, callback)
        elif msgtype == MessageType.DeallocateClass:
            classuid = uuid.UUID(bytes=message[1:17])
            logger.debug(f"Deallocate class with ID {classuid}")
            self.addClassForCleanup(classuid)
        else:
            logger.error(f"Unknown message type: {msgtype}")


remoter: Remoter | None = None
_remoter_init_lock = threading.Lock()


def _require_remoter() -> Remoter:
    runtime = remoter
    if runtime is None or not runtime.init:
        raise RuntimeError("Remoter not initialized")
    return runtime


def initRemoter(
    config: dict, host, port, sockpath, rmtloc, rmtport, allowall, locconfig, configfromkube, onlyrunserver=False
):
    # if socketpath is an existing directory or does not end in .sock, assume it specifies directory
    # generate socket file name as <socketpath>/POD_UID.sock - raise exception if envvar not set
    if sockpath is not None and (os.path.isdir(sockpath) or not sockpath.endswith(".sock")):
        sockpath = utils.socketpath(
            sockpath, os.environ["POD_NAMESPACE"], os.environ["POD_NAME"], os.environ["POD_UID"]
        )
    # initialize the remoter
    global remoter
    with _remoter_init_lock:
        if configfromkube is not None:
            rmtconfigkube.rmtconfigkube_init(
                configfromkube, locconfig
            )  # won't properly work if initialized from here if remoteloc, remoteable params are set
        if remoter is None or not remoter.init:
            logger.info(f"Initializing remoter on {host}:{port}")
            remoter = Remoter(config, host, port, sockpath, rmtloc, rmtport, allowall, locconfig)
        else:
            logger.info("Remoter already initialized")
        runtime = remoter
    if onlyrunserver:
        runtime.onlyRunServer()  # does not return
    return runtime


def initRemoterFromArgs(args):
    if args.norandom:
        logger.info("Not reseeding random number generator")
        random.seed(4851399312)
    else:
        # logger.info("Reseeding random number generator")
        random.seed()
    if args.genkey:
        # generate a key for the remoter
        key = secrets.token_bytes(32)
        with open(args.key, "wb") as f:
            f.write(key)
        logger.info(f"Generated key (hex): {key.hex()}")
        exit(0)
    if args.key:
        if os.path.exists(args.key):
            with open(args.key, "rb") as f:
                key = f.read()
            logger.info(f"Using key (hex): {key.hex()}")
        else:
            # load from hex string
            key = bytes.fromhex(args.key)
            assert len(key) == 32, "Key must be 32 bytes long (256 bits)"
        msgsock.msgkey = key
    if args.configserver:
        configserver = rmtconfig.ConfigServer(args.confighost, args.configport, args.ssl, args.config)
        configserver.run()  # this returns since flask app started as thread
    return initRemoter(
        {},
        args.host,
        args.port,
        args.sockpath,
        args.rmtloc,
        args.rmtport,
        args.allowall,
        args.config,
        args.configfromkube,
        args.fnserver,
    )


def ismetaremotedclass(args) -> bool:
    return len(args) > 0 and localhasattr(args[0], "rmtowner_rmt0bf")


def checkForRemotedClass(taskname, func, *args):
    isremoted = ismetaremotedclass(args)
    if isremoted and args[0].rmtowner_rmt0bf:  # noqa: SIM102 vendored from microsoft/xavier, not refactored
        # print(f"Rmt: {args[0]}")
        # print(f"Taskname: {taskname}, Class key: {remotedclasskey[type(args[0])]}")
        if taskname == remotedclasskey[type(args[0])]:
            # for remoted classes, if the taskname matches the class key and it is on server side, run __origfunc__ directly  # noqa: E501 vendored from microsoft/xavier, not refactored
            logger.debug(
                f"Taskname {taskname} matches remoted class key {remotedclasskey[type(args[0])]} and is on server side, running {func.__name__} directly"  # noqa: E501 vendored from microsoft/xavier, not refactored
            )
            return True
    return False


def createRemotedTask(
    func, taskname, functype="threadpooltask", nowait=False, fallbackfn=None, timeout=None
) -> Callable:
    key, _, _, _ = getfuncname(func)
    allowed_functions.add(key)
    logger.info(f"Adding remoted function {key} to allowed functions")

    if localhasattr(func, "__isremoted__"):
        logger.info(f"Function {key} already has __isremoted__ attribute, skipping remoter wrapping")
        return func

    if functype.lower() in ["process", "processpooltask"]:
        global needmultiproc
        needmultiproc = True

    @wraps(func)
    def wrapper(*args, **kwargs):
        runtime = _require_remoter()
        # Call the remote function
        remotedclassfunc = checkForRemotedClass(taskname, func, *args)
        if remotedclassfunc:
            if fallbackfn is not None:
                logger.debug(f"Using fallback function for remoted class function {taskname}")
                return fallbackfn(*args, **kwargs)
            return func(*args, **kwargs)
        return runtime.runSyncFunction(taskname, functype, nowait, timeout, None, func, *args, **kwargs)

    @wraps(func)
    def wrapper_async(*args, **kwargs):
        runtime = _require_remoter()
        # Call the remote function
        remotedclassfunc = checkForRemotedClass(taskname, func, *args)
        if remotedclassfunc:
            if fallbackfn is not None:
                logger.debug(f"Using fallback function for remoted class function {taskname}")
                return fallbackfn(*args, **kwargs)
            return func(*args, **kwargs)
        return runtime.runAsyncFunction(taskname, functype, nowait, timeout, None, func, *args, **kwargs)

    if inspect.iscoroutinefunction(func):
        isasync = True
        ret = wrapper_async
    else:
        isasync = False
        ret = wrapper

    ret.__isremoted__ = True
    ret.__remotedtaskname__ = taskname
    ret.__remotedfunctype__ = functype
    ret.__isasync__ = isasync
    ret.__origfunc__ = func

    return ret


def createNoRemoteTask(func) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    @wraps(func)
    def wrapper_async(*args, **kwargs):
        return func(*args, **kwargs)

    if localhasattr(func, "__isremoted__"):
        logger.info(f"Function {func.__name__} already has __isremoted__ attribute, skipping remoter wrapping")
        return func

    if inspect.iscoroutinefunction(func):
        isasync = True
        ret = wrapper_async
    else:
        isasync = False
        ret = wrapper

    ret.__isremoted__ = False
    ret.__isasync__ = isasync
    ret.__origfunc__ = func

    return ret


# a decorator to mark a function as remote - default functype is threadpooltask
def remotetask(taskname, functype="threadpooltask", nowait=False, fallbackfn=None, timeout=None):
    def decorator(func):
        return createRemotedTask(func, taskname, functype, nowait, fallbackfn, timeout)

    return decorator


def noremote():
    def decorator(func):
        # return a function that does not use the remoter
        # this is useful for functions that should not be remoted
        # but still need to be wrapped in a decorator
        # so that they can be used in the same way as remotetask
        # but without the remoting functionality
        return createNoRemoteTask(func)

    return decorator


def add_args(parser, **kwargs):
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=9000, help="Port to bind to for function executation information")
    parser.add_argument("--sockpath", type=str, default=None, help="Socket path for Unix domain socket")
    parser.add_argument("--config", type=str, default=kwargs.get("config", "remoterconfig.yaml"), help="Config file")
    parser.add_argument("--fnserver", action="store_true", default=False, help="Run only as function server")
    parser.add_argument(
        "--key", type=str, default="", help="Key for the remoter (for security) -- symmetric encryption"
    )
    parser.add_argument("--genkey", action="store_true", default=False, help="Generate a key for the remoter")
    parser.add_argument("--rmtloc", type=str, default=None, help="Remote host to connect to (for client)")
    parser.add_argument("--rmtport", type=int, default=None, help="Remote port to connect to (for client)")
    parser.add_argument(
        "--allowall",
        action="store_true",
        help="Allow remoting arbitrary functions (not recommended) -- otherwise only remotetask",
    )
    parser.add_argument(
        "--configserver",
        action="store_true",
        default=False,
        help="Run a config server to provide configuration information for remote tasks/classes",
    )
    parser.add_argument(
        "--configfromkube",
        default=None,
        help="Generate config using number of k8s pods running as remote function server -- argument is taskconfig",
    )
    parser.add_argument("--confighost", type=str, default="0.0.0.0", help="Host to bind to for config information")
    parser.add_argument("--configport", type=int, default=10000, help="Port to bind to for config information")
    parser.add_argument(
        "--norandom", action="store_true", help="Do not reseed random number generator (for testing purposes)"
    )
    parser.add_argument("--ssl", "-ssl", default=None, help="Use SSL with this certificate file (for config server)")


logger = simplelog.initlog("remoter.log", logging.DEBUG, logging.INFO)
