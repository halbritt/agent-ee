"""Run tools with a read-only candidate and one writable scratch directory."""

import hashlib
import os
import secrets
import signal
import stat
import subprocess
import tempfile
import time
from threading import Event, Lock
from pathlib import Path

from .storage import json_bytes, publish

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Tools resolve on this fixed PATH inside the sandbox; /usr is the host's, read-only.
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"
