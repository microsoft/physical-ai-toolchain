from __future__ import annotations

import os
import sys

sys.path.insert(0, "/opt/xavier/lerobot/diagnostics")

if os.environ.get("SERVER") != "true" and os.environ.get("REMOTER_CONFIG"):
    from remoter import autoremote

    autoremote.start(False)
