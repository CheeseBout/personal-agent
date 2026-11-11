"""Entry point: `agent-core` starts the daemon on localhost."""

from __future__ import annotations

import json
import sys

import uvicorn

from personal_agent.core.app import create_app
from personal_agent.core.attachments import WS_MAX_BYTES
from personal_agent.observability.logging import setup_logging
from personal_agent.settings.settings import load_settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    s = load_settings()
    if "--connection" in sys.argv:  # how a local UI finds and authenticates to the daemon (stdout only, never logged)
        from personal_agent.settings.secrets import get_api_token

        print(json.dumps({"host": s.server.host, "port": s.server.port, "token": get_api_token()}))
        return
    setup_logging()
    if s.server.host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing to bind to non-local host '{s.server.host}'")
    uvicorn.run(create_app(), host=s.server.host, port=s.server.port, log_config=None, ws_max_size=WS_MAX_BYTES)


if __name__ == "__main__":
    main()
