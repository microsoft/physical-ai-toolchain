from __future__ import annotations

import os

# server calls autoremote.start() manually, so we don't want to start it automatically when this file is imported
# server has envvar "SERVER": "true"
if os.environ.get("SERVER") != "true":
    from remoter import autoremote

    autoremote.start(False)
