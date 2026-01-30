from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class PlaywrightMCPConfig:
    ws_url: str
    auth_token: str
    base_url: str
    external_base_url: str
    proxy_base_url: str
    timeout: float
    nav_timeout_ms: int
    wait_state: str
    viewport_width: int
    viewport_height: int
    artifacts_dir: Path

    @classmethod
    def from_env(cls) -> "PlaywrightMCPConfig":
        ws_url = os.getenv("PWMCP_WS_URL") or os.getenv("WS_URL") or "ws://localhost:3000"
        auth_token = os.getenv("PWMCP_AUTH_TOKEN") or os.getenv("WS_AUTH_TOKEN") or os.getenv("ACCESS_TOKEN", "")
        base_url = os.getenv("PWMCP_BASE_URL") or os.getenv("UI_BASE_URL", "")
        external_base_url = os.getenv("PWMCP_EXTERNAL_BASE_URL", "")
        proxy_base_url = os.getenv("PWMCP_PROXY_BASE_URL", "")
        timeout = float(os.getenv("PWMCP_TIMEOUT", "30"))
        nav_timeout_ms = int(os.getenv("PWMCP_NAV_TIMEOUT_MS", "30000"))
        wait_state = os.getenv("PWMCP_WAIT_STATE", "networkidle")
        viewport_width = int(os.getenv("PWMCP_VIEWPORT_WIDTH", "1280"))
        viewport_height = int(os.getenv("PWMCP_VIEWPORT_HEIGHT", "720"))
        artifacts_dir = Path(os.getenv("PWMCP_ARTIFACTS_DIR", "artifacts"))

        return cls(
            ws_url=ws_url,
            auth_token=auth_token,
            base_url=base_url,
            external_base_url=external_base_url,
            proxy_base_url=proxy_base_url,
            timeout=timeout,
            nav_timeout_ms=nav_timeout_ms,
            wait_state=wait_state,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            artifacts_dir=artifacts_dir,
        )