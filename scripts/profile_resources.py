"""Measures what the agent costs when idle: daemon cold start, daemon + UI memory/CPU, and the heaviest imports.

Run from the repo root:  python -m uv run python scripts/profile_resources.py [--no-ui]
Uses a throw-away data dir and an unused port; nothing of yours is touched. The UI is measured offscreen (no real window).
Targets (docs/decisions.md): daemon idle <= 110 MB (<= 200 MB after the first chat), UI <= 110 MB offscreen, idle CPU ~0%, daemon start <= 5 s.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import psutil


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def tree_rss(pid: int) -> float:
    """Resident memory of the process and its children (the venv python.exe is a launcher that spawns the real one)."""
    try:
        p = psutil.Process(pid)
        procs = [p, *p.children(recursive=True)]
        return max(x.memory_info().rss for x in procs) / 1_048_576
    except psutil.Error:
        return 0.0


def real_proc(pid: int) -> psutil.Process:
    p = psutil.Process(pid)
    return max([p, *p.children(recursive=True)], key=lambda x: x.memory_info().rss)


def importtime(module: str, top: int = 25) -> list[tuple[float, str]]:
    """Slowest imports by cumulative time (each line includes what it imported), at any depth, first occurrence only."""
    out = subprocess.run([sys.executable, "-X", "importtime", "-c", f"import {module}"], capture_output=True, text=True, check=False).stderr
    rows: dict[str, float] = {}
    for line in out.splitlines():
        m = re.match(r"import time:\s+(\d+) \|\s+(\d+) \| (\s*)(\S+)", line)
        if m and m.group(4) != module:
            rows.setdefault(m.group(4), int(m.group(2)) / 1000)
    return sorted(((ms, name) for name, ms in rows.items()), reverse=True)[:top]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    port = free_port()
    home = tempfile.mkdtemp(prefix="pa-prof-")
    cfgdir = os.path.join(home, "cfg")
    os.makedirs(cfgdir)
    import shutil

    shutil.copy("config/default.yaml", os.path.join(cfgdir, "default.yaml"))
    shutil.copy("config/policy.yaml", os.path.join(cfgdir, "policy.yaml"))
    # point the throw-away daemon at an unused port through the user override file
    with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"server:\n  port: {port}\n")
    env = {**os.environ, "PERSONAL_AGENT_HOME": home, "PERSONAL_AGENT_CONFIG_DIR": cfgdir, "PERSONAL_AGENT_TOKEN": "prof",
           "PERSONAL_AGENT_ENV_FILE": "nul", "QT_QPA_PLATFORM": "offscreen"}
    res: dict[str, object] = {}
    t0 = time.perf_counter()
    d = subprocess.Popen([sys.executable, "-m", "personal_agent.core.main"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ready = None
    while time.perf_counter() - t0 < 30:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.3).read()
            ready = time.perf_counter() - t0
            break
        except OSError:
            time.sleep(0.05)
    res["daemon_cold_start_s"] = round(ready, 2) if ready else None
    time.sleep(6)  # let start-up work settle
    rp = real_proc(d.pid)
    rp.cpu_percent(None)
    time.sleep(5)
    res["daemon_idle_rss_mb"] = round(rp.memory_info().rss / 1_048_576)
    res["daemon_idle_cpu_pct"] = rp.cpu_percent(None)
    res["daemon_threads"] = rp.num_threads()
    ui = None
    if not a.no_ui:
        ui = subprocess.Popen([sys.executable, "-m", "personal_agent.ui", "--no-daemon"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(9)
        up = real_proc(ui.pid)
        up.cpu_percent(None)
        time.sleep(5)
        res["ui_idle_rss_mb"] = round(up.memory_info().rss / 1_048_576)
        res["ui_idle_cpu_pct"] = up.cpu_percent(None)
        res["ui_threads"] = up.num_threads()
    for p in (ui, d):
        if p:
            p.terminate()
    res["daemon_top_imports_ms"] = [(round(ms), m) for ms, m in importtime("personal_agent.core.app")]
    if not a.no_ui:
        res["ui_top_imports_ms"] = [(round(ms), m) for ms, m in importtime("personal_agent.ui.app")]
    if a.json:
        print(json.dumps(res, indent=2))
        return
    for k, v in res.items():
        if isinstance(v, list):
            print(f"{k}:")
            for ms, m in v:
                print(f"    {ms:>6} ms  {m}")
        else:
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
