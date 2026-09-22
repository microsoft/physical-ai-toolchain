from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime

from termcolor import cprint

# log levels:
# fatal/critical 50, error 40, warn/warning 30, info 20, debug 10, notset 0

levels: dict[str, int] = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "notset": logging.NOTSET,  # print everything
}

revlevels = {v: k for k, v in levels.items()}  # int->str mapping


def is_file_open(path: str) -> bool:
    """
    Check if the file is currently open by any process using lsof.
    Returns True if open, False otherwise.
    """
    if os.getenv("NO_LSOF_CHECK", None) is not None:
        return False

    try:
        result = subprocess.run(["lsof", path], capture_output=True, text=True)
        return bool(result.stdout.strip())  # output means file is open
    except FileNotFoundError:
        return False


def rollover(filename, keep=4):
    # rollover log files
    cnt = 1
    origfilename = filename
    while True:
        try:
            if sys.platform.startswith("linux") and is_file_open(filename):  # only linux has lsof
                raise Exception("Log file is currently open.")
            # delete files older than keep
            for i in range(keep, 1000):
                oldname = f"{filename}.{i}"
                if os.path.exists(oldname):
                    os.remove(oldname)
                else:
                    break
            if os.path.exists(filename):
                for i in range(keep - 1, 0, -1):
                    oldname = f"{filename}.{i}"
                    newname = f"{filename}.{i + 1}"
                    if os.path.exists(oldname):
                        os.rename(oldname, newname)
                os.rename(filename, f"{filename}.1")
            break
        except Exception:
            # print(f"Error during log rollover: {e}")
            # try different filename with increassing extension
            base, ext = os.path.splitext(origfilename)
            # filename = f"{base}-{os.urandom(4).hex()}{ext}"
            filename = f"{base}-{cnt}{ext}"
            cnt += 1
    return filename


# prepend "2025-10-27 13:24:26 INFO" to each log line (optional) - fixed length prepend
# prepend date along with level of the log message
class SimpleLog:
    def __init__(self, filename, loglevel=10, printlevel=10, noprepend=False, useutc=False):
        # if environment variable LOGLEVEL is set, override loglevel
        envlevel = None
        if "LOGLEVEL" in os.environ:
            envlevel = os.environ["LOGLEVEL"].lower()  # always a string
        if "XAVIER_LOGLEVEL" in os.environ:
            envlevel = os.environ["XAVIER_LOGLEVEL"].lower()  # always a string
        if envlevel is not None:
            if envlevel in levels:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
                loglevel = levels[envlevel]
            else:
                loglevel = int(envlevel)  # if envlevel is supposed to be int, then let it crash if it is not
        envlevel = None
        if "PRINTLOGLEVEL" in os.environ:
            envlevel = os.environ["PRINTLOGLEVEL"].lower()  # always a string
        if "XAVIER_PRINTLOGLEVEL" in os.environ:
            envlevel = os.environ["XAVIER_PRINTLOGLEVEL"].lower()  # always a string
        if envlevel is not None:
            if envlevel in levels:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
                printlevel = levels[envlevel]
            else:
                printlevel = int(envlevel)  # if envlevel is supposed to be int, then let it crash if it is not
        if isinstance(loglevel, str):
            loglevel = levels.get(loglevel.lower(), 10)
        if isinstance(printlevel, str):
            printlevel = levels.get(printlevel.lower(), 10)
        # if filename is not absolute or relative path, put in a "log" directory
        if filename and not (filename.startswith("/") or filename.startswith(".") or ":" in filename):
            if "LOGDIR" in os.environ:
                logdir = os.environ["LOGDIR"]
            else:
                # get home directory
                if "HOME" in os.environ:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
                    homedir = os.environ["HOME"]
                else:
                    homedir = os.path.expanduser("~")
                logdir = os.path.join(homedir, "logs")
            if not os.path.exists(logdir):
                os.makedirs(logdir)
            filename = os.path.join(logdir, filename)
        self.filename = rollover(filename)
        print(f"Log file: {self.filename} - loglevel: {loglevel}, printlevel: {printlevel}")
        self.file = open(self.filename, "w", encoding="utf-8") if self.filename else None  # noqa: SIM115 vendored from microsoft/xavier, not refactored
        self.loglevel = loglevel
        self.printlevel = printlevel
        self.prepend = not noprepend
        self.useutc = useutc
        atexit.register(self.close)

    def getprepend(self, level: int | None) -> str:
        if not self.prepend:
            return ""
        if self.useutc:
            now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # use local timezone
        if level is None:
            return f"{now} {'None':<8} "
        levelname = revlevels.get(level, str(level)).upper()  # for example for 25, return 25
        return f"{now} {levelname:<8} "

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    # print if severity of event is more than desired loglevel/printlevel
    def log(self, level, color, *args, **kwargs):
        prepend = None
        if level is None or level >= self.loglevel:
            if prepend is None:
                prepend = self.getprepend(level)
            print(prepend, end="", file=self.file, flush=True)
            print(*args, **kwargs, file=self.file, flush=True)
        if level is None or level >= self.printlevel:
            if prepend is None:
                prepend = self.getprepend(level)
            cprint(prepend, end="", color=color)
            # if multiple args, join them with space
            if len(args) > 1:
                # execute a mock print
                # buf = io.StringIO()
                # with contextlib.redirect_stdout(buf):
                #     print(*args, **kwargs)
                cprint(" ".join(str(a) for a in args), color=color, flush=True, **kwargs)
            else:
                cprint(*args, **kwargs, color=color, flush=True, **kwargs)

    def levelprint(self, level: str, *args, **kwargs):
        if "color" in kwargs:  # noqa: SIM108 vendored from microsoft/xavier, not refactored
            color = kwargs.pop("color")
        else:
            color = None
        try:
            self.log(levels[level], color, *args, **kwargs)
        except Exception:
            self.log(logging.ERROR, "Logging error", "red")

    def debug(self, *args, **kwargs):
        self.levelprint("debug", *args, **kwargs)

    def info(self, *args, **kwargs):
        self.levelprint("info", *args, **kwargs)

    def warning(self, *args, **kwargs):
        self.levelprint("warning", *args, **kwargs)

    def error(self, *args, **kwargs):
        self.levelprint("error", *args, **kwargs)

    def critical(self, *args, **kwargs):
        self.levelprint("critical", *args, **kwargs)

    def cprint(self, msg, color, **kwargs):
        self.log(None, color, msg, **kwargs)

    def print(self, msg="", **kwargs):
        self.log(None, None, msg, **kwargs)


logger: SimpleLog = None


def initlog(filename, loglevel=logging.DEBUG, printlevel=logging.INFO):
    global logger
    # each caller gets its own SimpleLog instance (distinct log file); this intentionally does not
    # reuse an existing instance even if one was already created by another module
    logger = SimpleLog(filename, loglevel, printlevel)
    return logger


def lprint(level, color, *args, **kwargs):
    global logger
    if logger is not None:
        logger.log(level, color, *args, **kwargs)
    else:
        print(*args, **kwargs, flush=True)


def lcprint(msg, color, **kwargs):
    global logger
    if logger is not None:
        logger.cprint(msg, color, **kwargs)
    else:
        cprint(msg, color, **kwargs, flush=True)
