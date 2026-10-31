"""Entry point: `agent-core` starts the daemon on localhost."""

from __future__ import annotations

import uvicorn

from personal_agent.core.app import create_app
from personal_agent.observability.logging import setup_logging
from personal_agent.settings.settings import load_settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    setup_logging()
    s = load_settings()
    if s.server.host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing to bind to non-local host '{s.server.host}'")
    uvicorn.run(create_app(), host=s.server.host, port=s.server.port, log_config=None)


if __name__ == "__main__":
    main()
