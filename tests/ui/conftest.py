from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app  # type: ignore[misc]


def pump(ms: int = 50) -> None:
    """Run the Qt event loop for `ms`. Sleeps with time.sleep (QTest.qWait kept the GIL and starved the daemon thread)."""
    import time

    end = time.monotonic() + ms / 1000
    while True:
        QApplication.processEvents()
        if time.monotonic() >= end:
            return
        time.sleep(0.005)
