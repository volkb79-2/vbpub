"""P58 — Daemon MCP frontend (optional groop[mcp] extra).
A read-only Model Context Protocol server exposing the daemon's frames,
health, and entity data as typed MCP tools. Importing this package must
only happen inside the ``groop mcp serve`` subcommand path — no MCP SDK
import at module level.
"""
from groop.mcp.server import McpServer, SignalRegistration
__all__ = ["McpServer", "SignalRegistration"]
