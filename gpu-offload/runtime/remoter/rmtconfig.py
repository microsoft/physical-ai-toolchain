"""
PyRemote Configuration File Management
This module provides functions to read, write, and update configuration files in YAML format.
Each operation holds a `filelock` lock on `<file>.lock`, and writes go to a temporary file in the
same directory that then replaces the configuration file, so readers never see a partial write.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import shutil
import threading
import uuid

import filelock
import flask
import watchfiles
import yaml

from .simplelog import initlog


def _lock(file_path: str) -> filelock.FileLock:
    # This module never deletes the lock file. Unlinking it while another process holds or waits for the
    # lock would let a third process lock a new file at the same path and enter at the same time.
    return filelock.FileLock(file_path + ".lock")


def _read(file_path: str) -> dict:
    try:
        with open(file_path, encoding="utf-8") as file:
            return yaml.safe_load(file)
    except FileNotFoundError as e:
        logger.error(f"Error reading configuration file {file_path}: {e}")
        return {}
    except yaml.YAMLError as e:
        # Raise rather than return {}, which update_config_file would write back over the file.
        logger.error(f"Error parsing configuration file {file_path}: {e}")
        raise


def _write(file_path: str, config: dict) -> None:
    tmp_path = f"{file_path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "x", encoding="utf-8") as file:
            # Keep the permission bits of the file being replaced, before any content is written.
            with contextlib.suppress(FileNotFoundError):
                shutil.copymode(file_path, tmp_path)
            yaml.safe_dump(config, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, file_path)
    except (OSError, yaml.YAMLError) as e:
        logger.error(f"Error writing configuration file {file_path}: {e}")
        raise
    finally:
        # After a successful os.replace the temporary file is already gone.
        with contextlib.suppress(OSError):
            os.remove(tmp_path)


def read_config_file(file_path: str) -> dict:
    with _lock(file_path):
        return _read(file_path)


def write_config_file(file_path: str, config: dict) -> None:
    with _lock(file_path):
        _write(file_path, config)


def update_config_file(file_path: str, new_config: dict) -> None:
    # One lock around the read, merge and write, so concurrent updates cannot drop each other's keys.
    with _lock(file_path):
        config = _read(file_path)
        config.update(new_config)
        _write(file_path, config)


class Config:
    def __init__(self, file_path, callback):
        self.file_path = file_path
        self.callback = callback  # callback function to notify about changes
        if not os.path.exists(file_path):
            logger.info(f"Configuration file {file_path} does not exist, creating it.")
            write_config_file(file_path, {})
        try:
            self.config = read_config_file(file_path)
        except Exception as e:  # file not found or yaml load error
            logger.error(f"Error reading configuration file {file_path}: {e}")
            self.config = {}
        logger.info(f"Initial configuration: {self.config}")
        if self.callback:
            self.callback(self.config)
        self.stop = False
        self.stopevent = threading.Event()
        threading.Thread(target=self.watch_config_file, daemon=True).start()
        # try:
        #     with open(self.file_path, 'a'):
        #         os.utime(self.file_path, None)
        # except Exception:
        #     pass

    def watch_config_file(self) -> None:
        # watch the configuration file for changes
        for changes in watchfiles.watch(self.file_path):
            if self.stop:
                break
            for change in changes:
                if change[0] == watchfiles.Change.modified or change[0] == watchfiles.Change.added:
                    logger.info(f"Configuration file {self.file_path} changed, reloading...")
                    oldconfig = copy.deepcopy(self.config)
                    try:
                        self.config = read_config_file(self.file_path)
                        logger.debug(f"New configuration: {self.config}")
                        if self.callback:
                            self.callback(self.config)  # notify about the change
                    except Exception as e:
                        logger.error(f"Error reading configuration file {self.file_path}: {e}")
                        self.config = oldconfig
                elif change[0] == watchfiles.Change.deleted:
                    if os.path.exists(self.file_path):
                        # Replaced by a rename (os.replace), which some platforms report as deleted
                        # plus added; the added event reloads it.
                        continue
                    logger.info(f"Configuration file {self.file_path} deleted, stopping watch...")
                    self.config = {}
                    break
        self.stopevent.set()

    def stopwatch(self) -> None:
        # stop watching the config file
        logger.info(f"Stopping watch on configuration file {self.file_path}")
        # currently no way to stop watchfiles.watch, so just exit the thread
        self.stop = True
        # trigger a dummy change to exit the watch
        try:
            with open(self.file_path, "a"):
                os.utime(self.file_path, None)
        except Exception:
            pass
        ret = self.stopevent.wait(timeout=5)
        if not ret:
            logger.warning(f"Timeout waiting for watch thread to stop for file {self.file_path}")

    def get_config(self):
        return self.config


class ConfigServer:
    def __init__(self, host, port, ssl, configfile):
        self.server = flask.Flask("Config Server")
        self.server.add_url_rule("/config", None, self.get_config, methods=["GET"])
        self.server.add_url_rule("/update", None, self.update_config, methods=["POST"])
        self.configfile = configfile
        self.server_args = {"host": host, "port": port}
        if ssl:
            self.server_args["ssl_context"] = ssl

    def run(self):
        threading.Thread(target=self.server.run, kwargs=self.server_args, daemon=True).start()

    def get_config(self):
        # return the current configuration
        try:
            config = read_config_file(self.configfile)
            return flask.jsonify(config)
        except Exception as e:
            logger.error(f"Error reading configuration: {e}")
            return flask.jsonify({"error": "failed to read configuration"}), 500

    def update_config(self):
        # update the configuration
        try:
            new_config = flask.request.json
            logger.info(f"Updating configuration with: {new_config}")
            update_config_file(self.configfile, new_config)
            return flask.jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"Error updating configuration: {e}")
            logger.error(f"Cannot parse content: {flask.request.data}")
            return flask.jsonify({"error": "failed to update configuration"}), 500


# to test
# python3 ./rmtconfig.py test.yaml
if __name__ == "__main__":
    # test
    import sys

    config_file = sys.argv[1]
    contents = {"name": "test", "version": 1.0, "description": "test config file"}
    write_config_file(config_file, contents)
    print("Wrote config file:", contents)
    config2 = read_config_file(config_file)
    print("Read config file:", config2)
    # assert config2 == contents
    assert config2 == contents
    # update the config file
    new_config = {"version": 2.0, "description": "updated test config file"}
    update_config_file(config_file, new_config)
    config3 = read_config_file(config_file)
    print("Updated config file:", new_config)
    contents.update(new_config)
    assert config3 == contents
    print("Read updated config file:", config3)

    import time

    timestart = time.time()
    c = Config(config_file, lambda *args, **kwargs: print("Config changed:", args, kwargs))
    while (time.time() - timestart) < 50:
        print("Current config:", c.get_config())
        time.sleep(1)

logger = initlog("rmtconfig.log", logging.DEBUG, logging.INFO)
