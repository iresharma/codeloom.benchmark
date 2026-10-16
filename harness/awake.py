"""Keep the laptop awake for the duration of a run while it is on AC power."""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager


@contextmanager
def stay_awake():
    if os.uname().sysname != "Darwin" or not shutil.which("caffeinate"):
        yield
        return
    process = subprocess.Popen(["caffeinate", "-dimsu"])
    try:
        yield
    finally:
        process.terminate()
        process.wait(timeout=5)
