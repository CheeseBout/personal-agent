"""Docker sandbox: started lazily, locked down, auto-stopped when idle.

Only the dedicated staging directory is mounted (as /work). No network unless the task opts in
(which the policy makes a T2 approval). Docker is driven through its CLI with argv lists (no shell).
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_agent.settings.settings import app_root

NAME = "pa-sandbox"
Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


class SandboxUnavailable(RuntimeError):
    """Docker is missing or its engine is not running."""


class DockerSandbox:
    def __init__(self, cfg: Callable[[], dict[str, Any]], work_dir: Path, runner: Runner = subprocess.run) -> None:
        self._cfg, self._work, self._run = cfg, work_dir, runner
        self._timer: threading.Timer | None = None
        self._avail: tuple[float, bool] = (0.0, False)
        self._lock = threading.Lock()
        self._started = False

    # ---- plumbing ------------------------------------------------------------------------------------------
    def _docker(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._run(["docker", *args], capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError as e:
            raise SandboxUnavailable("Docker is not installed") from e
        except subprocess.TimeoutExpired as e:
            raise SandboxUnavailable("Docker did not respond in time") from e

    def available(self) -> bool:
        """Is the Docker engine reachable? (cached briefly: `docker info` is slow when the engine is down)."""
        if time.monotonic() - self._avail[0] < 15:
            return self._avail[1]
        try:
            ok = self._docker("info", timeout=8).returncode == 0
        except SandboxUnavailable:
            ok = False
        self._avail = (time.monotonic(), ok)
        return ok

    def run_args(self, network: bool) -> list[str]:
        c = self._cfg()
        return [
            "run", "-d", "--rm", "--name", NAME, "--label", f"pa.network={str(network).lower()}",
            "--network", "bridge" if network else "none",
            "--cpus", str(c.get("cpus", 1)), "--memory", str(c.get("memory", "512m")),
            "--memory-swap", str(c.get("memory", "512m")), "--pids-limit", str(c.get("pids", 128)),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:rw,size=64m", "--user", "1000:1000",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
            "-v", f"{self._work}:/work:rw",  # the ONLY mount
            "-w", "/work", str(c.get("image", "personal-agent-sandbox:1")), "sleep", "infinity",
        ]

    def ensure_image(self) -> None:
        image = str(self._cfg().get("image", "personal-agent-sandbox:1"))
        if self._docker("image", "inspect", image).returncode == 0:
            return
        # the Dockerfile ships next to config/ (repo root, or the PyInstaller bundle)
        ctx = app_root() / "docker" / "sandbox"
        r = self._docker("build", "-t", image, str(ctx), timeout=900)  # first use only; needs network
        if r.returncode != 0:
            raise SandboxUnavailable("could not build the sandbox image: " + r.stderr.decode(errors="replace")[-300:])

    def ensure_running(self, network: bool) -> None:
        if not self.available():
            raise SandboxUnavailable(
                "Docker is not running. Start Docker Desktop, or do the work with approved (T2) host tools instead.")
        with self._lock:
            r = self._docker("inspect", "-f", "{{.State.Running}} {{index .Config.Labels \"pa.network\"}}", NAME)
            if r.returncode == 0 and r.stdout.decode().split() == ["true", str(network).lower()]:
                self._started = True
                self._touch()
                return
            self._docker("rm", "-f", NAME)  # wrong network mode or stale: recreate
            self.ensure_image()
            started = self._docker(*self.run_args(network), timeout=60)
            if started.returncode != 0:
                raise SandboxUnavailable("could not start the sandbox: " + started.stderr.decode(errors="replace")[-300:])
            self._started = True
            self._touch()

    def exec(self, command: str, timeout_s: int, network: bool = False) -> tuple[int, str]:
        self.ensure_running(network)
        t = max(1, min(timeout_s, int(self._cfg().get("exec_timeout_s", 60)) * 5))
        r = self._docker("exec", "-w", "/work", NAME, "timeout", "-s", "KILL", str(t), "sh", "-c", command,
                         timeout=t + 15)
        self._touch()
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", errors="replace")

    # ---- lifecycle -------------------------------------------------------------------------------------------
    def _touch(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(float(self._cfg().get("idle_timeout_s", 300)), self.stop)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if not self._started:
            return  # never started in this process: don't even touch Docker
        self._started = False
        try:
            self._docker("rm", "-f", NAME, timeout=20)
        except SandboxUnavailable:
            pass  # nothing to stop
