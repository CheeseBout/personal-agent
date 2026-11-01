"""Find and start the daemon. It is started detached, so closing the UI never stops running tasks."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path


def is_up(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _windowless_python() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w) if exe.name.lower() == "python.exe" and w.exists() else str(exe)


def start_daemon() -> subprocess.Popen[bytes]:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return subprocess.Popen([_windowless_python(), "-m", "personal_agent.core.main"], creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
