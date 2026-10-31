"""A tiny real MCP server (stdio) used by the tests."""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def whoami_env() -> str:
    """Report the value of the TEST_TOKEN env var given to this server."""
    return os.environ.get("TEST_TOKEN", "(unset)")


if __name__ == "__main__":
    mcp.run()
