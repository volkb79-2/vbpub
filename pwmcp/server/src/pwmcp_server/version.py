"""Version utilities for Playwright MCP server."""
from __future__ import annotations

import os


def _resolve_version() -> str:
	version = os.getenv("PWMCP_VERSION")
	if version:
		return version
	version = os.getenv("VERSION")
	if version:
		return version
	version = os.getenv("BUILD_DATE")
	if version:
		return version
	return "0.1.0"


__version__ = _resolve_version()
