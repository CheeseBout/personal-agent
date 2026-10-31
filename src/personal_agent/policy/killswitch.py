"""Global kill switch: halts all runs, rejects pending approvals, stops the sandbox. Code-enforced."""

from __future__ import annotations

from collections.abc import Callable

import structlog

log = structlog.get_logger()


class KillSwitch:
    def __init__(self) -> None:
        self.engaged = False
        self._callbacks: list[Callable[[], None]] = []
        self._release_callbacks: list[Callable[[], None]] = []

    def on_engage(self, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)

    def engage(self) -> None:
        self.engaged = True
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                log.exception("kill_switch_callback_failed")

    def on_release(self, cb: Callable[[], None]) -> None:
        self._release_callbacks.append(cb)

    def release(self) -> None:
        self.engaged = False
        for cb in self._release_callbacks:
            cb()
