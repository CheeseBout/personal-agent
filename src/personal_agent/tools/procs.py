"""Run host subprocesses so the kill switch can really stop them (a cancelled asyncio task cannot)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

_lock = threading.Lock()
_procs: set[subprocess.Popen[bytes]] = set()
_stopped = False


class KillSwitchEngaged(RuntimeError):
    """A new process was requested while the kill switch is engaged."""


def _kill_tree(p: subprocess.Popen[bytes]) -> None:
    # /T also ends children (e.g. what PowerShell spawned). Only ever targets a PID this module started.
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
    p.kill()


def run(cmdline: list[str], cwd: Path, timeout: float, input_data: bytes | None = None) -> tuple[int, bytes, bytes]:
    with _lock:  # check-and-start is atomic with kill_all(): no process can slip in after a kill
        if _stopped:
            raise KillSwitchEngaged
        p = subprocess.Popen(cmdline, cwd=cwd, stdin=subprocess.PIPE if input_data is not None else None,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _procs.add(p)
    try:
        out, err = p.communicate(input=input_data, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.communicate()
        raise
    finally:
        with _lock:
            _procs.discard(p)


def kill_all() -> None:
    global _stopped
    with _lock:
        _stopped = True
        victims = list(_procs)
    for p in victims:
        _kill_tree(p)
    for p in victims:  # do not report 'killed' until the processes are really gone (bounded wait)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def reset() -> None:
    global _stopped
    with _lock:
        _stopped = False
