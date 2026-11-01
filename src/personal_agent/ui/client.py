"""WebSocket link to the daemon (QtWebSockets, so no asyncio loop is needed in the UI). Reconnects by itself, so closing,
restarting or crashing the UI or the daemon never loses the user's place: pending approvals are re-pushed on connect."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

BACKOFF_S = (1, 2, 4, 8)  # then stays at the last value


class DaemonClient(QObject):
    message = Signal(dict)
    connected = Signal()
    disconnected = Signal()

    def __init__(self, host: str, port: int, token: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url, self._token = QUrl(f"ws://{host}:{port}/ws"), token
        self._ws = QWebSocket()  # no Origin header: the daemon only rejects a *foreign* one
        self._ws.textMessageReceived.connect(self._on_text)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self.open)
        self._attempt = 0
        self._wanted = False

    @property
    def is_connected(self) -> bool:
        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def start(self) -> None:
        self._wanted = True
        self.open()

    def stop(self) -> None:
        self._wanted = False
        self._retry.stop()
        self._ws.close()

    def open(self) -> None:
        if self._ws.state() != QAbstractSocket.SocketState.UnconnectedState:
            return
        req = QNetworkRequest(self._url)
        req.setRawHeader(b"Authorization", f"Bearer {self._token}".encode())
        self._ws.open(req)

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.is_connected:
            return False
        self._ws.sendTextMessage(json.dumps(payload, ensure_ascii=False))
        return True

    def _on_connected(self) -> None:
        self._attempt = 0
        self.connected.emit()

    def _on_disconnected(self) -> None:
        self.disconnected.emit()
        if self._wanted:
            self._retry.start(BACKOFF_S[min(self._attempt, len(BACKOFF_S) - 1)] * 1000)
            self._attempt += 1

    def _on_text(self, text: str) -> None:
        try:
            ev = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(ev, dict) and "type" in ev:
            self.message.emit(ev)
