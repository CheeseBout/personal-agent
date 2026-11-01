"""PyInstaller entry point: the desktop UI (agent-ui). Starts the daemon next to it when it is not running."""

from personal_agent.ui.app import main

if __name__ == "__main__":
    main()
