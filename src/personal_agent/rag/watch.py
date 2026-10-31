"""Background watcher for RAG folders: OS change events (no polling), debounced, low priority, pausable."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()
DEBOUNCE_S = 2.0


def _lower_priority() -> None:
    try:
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except (AttributeError, OSError):
        pass  # not Windows: nothing to do


class FolderWatcher:
    def __init__(self, folders: list[Path], sync: Callable[[Path], Any], debounce: float = DEBOUNCE_S) -> None:
        self.folders, self._sync, self._debounce = folders, sync, debounce
        self.paused = False
        self._timers: dict[Path, threading.Timer] = {}
        self._observer: Any = None
        self._lock = threading.Lock()

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        outer = self

        class Handler(FileSystemEventHandler):
            def __init__(self, folder: Path) -> None:
                self.folder = folder

            def on_any_event(self, event: Any) -> None:
                if not event.is_directory:
                    outer._schedule(self.folder)

        self._observer = Observer()
        for f in self.folders:
            if f.is_dir():
                self._observer.schedule(Handler(f), str(f), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def _schedule(self, folder: Path) -> None:
        if self.paused:
            return
        with self._lock:
            if t := self._timers.get(folder):
                t.cancel()
            t = threading.Timer(self._debounce, self._run, args=(folder,))
            t.daemon = True
            self._timers[folder] = t
            t.start()

    def _run(self, folder: Path) -> None:
        if self.paused:
            return
        _lower_priority()
        try:
            for line in self._sync(folder):
                log.info("rag_sync", detail=line)
        except Exception:
            log.exception("rag_sync_failed", folder=str(folder))

    def stop(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)
