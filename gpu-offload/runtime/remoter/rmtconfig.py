"""
PyRemote Configuration File Management
This module provides functions to read, write, and update configuration files in YAML format.
It uses the `portalocker` library to ensure that the file is locked during read/write
operations to prevent concurrent access issues.
"""

from __future__ import annotations

import copy
import logging
import os
import threading

import filelock
import flask
import watchfiles
import yaml

from .simplelog import initlog


def read_config_file(file_path):
    # lock the file to prevent concurrent access
    with filelock.FileLock(file_path + ".lock"):
        try:
            with open(file_path, encoding="utf-8") as file:
                # read the YAML file
                config = yaml.safe_load(file)
        except (FileNotFoundError, yaml.YAMLError) as e:
            logger.error(f"Error reading configuration file {file_path}: {e}")
            config = {}  # return an empty dict if file not found or error in YAML format
    try:
        os.remove(file_path + ".lock")
        logger.info(f"Removed lock file for {file_path}")
    except Exception:
        logger.error(f"Error removing lock file for {file_path}")
        pass
    # return the configuration
    return config


def write_config_file(file_path, config):
    # lock the file to prevent concurrent access
    with filelock.FileLock(file_path + ".lock"):
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                # write the YAML file
                yaml.safe_dump(config, file)
        except (OSError, yaml.YAMLError) as e:
            logger.error(f"Error writing configuration file {file_path}: {e}")
            raise e  # re-raise the exception to notify the caller
    try:
        os.remove(file_path + ".lock")
        logger.info(f"Removed lock file for {file_path}")
    except Exception:
        logger.error(f"Error removing lock file for {file_path}")
        pass


def update_config_file(file_path, new_config):
    # read the existing configuration
    config = read_config_file(file_path)
    # update the configuration with new values
    config.update(new_config)
    # write the updated configuration back to the file
    write_config_file(file_path, config)


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

    def watch_config_file(self):
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
                    logger.info(f"Configuration file {self.file_path} deleted, stopping watch...")
                    self.config = {}
                    break
        self.stopevent.set()

    def stopwatch(self):
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
        try:
            os.remove(self.file_path + ".lock")
            logger.info(f"Removed lock file for {self.file_path}")
        except Exception:
            logger.error(f"Error removing lock file for {self.file_path}")
            pass
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
