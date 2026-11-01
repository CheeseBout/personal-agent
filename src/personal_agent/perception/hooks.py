"""Runtime hooks the daemon fills in so the perception / control code can reach the UI and the kill switch without importing the app."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# emit(event): broadcast to every connected UI (thread-safe). Used for highlights and "the agent looked at your screen".
emit: Callable[[dict[str, Any]], None] = lambda ev: None
# observers(): number of connected UIs. Control refuses to run with nobody there to show the highlight and let you abort.
observers: Callable[[], int] = lambda: 0
# killed(): the global kill switch is engaged.
killed: Callable[[], bool] = lambda: False
agent_name: Callable[[], str] = lambda: ""
# latest_frame(): the newest "meaningful change" frame from continuous mode, or None
latest_frame: Callable[[], Any] = lambda: None


def reset() -> None:
    global emit, observers, killed, agent_name, latest_frame
    emit, observers, killed, agent_name, latest_frame = (lambda ev: None), (lambda: 0), (lambda: False), (lambda: ""), (lambda: None)
