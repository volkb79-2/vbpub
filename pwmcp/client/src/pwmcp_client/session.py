from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pwmcp_client.artifacts import ArtifactManager
from pwmcp_client.client import PlaywrightWSClient


@dataclass(frozen=True)
class SessionBundle:
    storage_state_path: Path
    cookies_path: Path


@dataclass
class SessionManager:
    client: PlaywrightWSClient
    artifacts: ArtifactManager
    storage_filename: str = "storage-state.json"
    cookies_filename: str = "cookies.json"

    def _storage_path(self) -> Path:
        return self.artifacts.base_dir / self.storage_filename

    def _cookies_path(self) -> Path:
        return self.artifacts.base_dir / self.cookies_filename

    async def save(self) -> SessionBundle:
        self.artifacts.ensure_base_dir()
        await self.client.export_storage_state(path=self.storage_filename)
        cookies = await self.client.cookies()
        self.artifacts.write_json(self._cookies_path(), cookies)
        return SessionBundle(storage_state_path=self._storage_path(), cookies_path=self._cookies_path())

    async def load(self) -> SessionBundle:
        await self.client.import_storage_state(path=self.storage_filename)
        payload = json.loads(self._cookies_path().read_text(encoding="utf-8"))
        cookies = payload.get("cookies", payload)
        await self.client.set_cookies(cookies)
        return SessionBundle(storage_state_path=self._storage_path(), cookies_path=self._cookies_path())

    async def ensure(self, login_callback: Optional[Callable[[], Awaitable[object]]] = None) -> SessionBundle:
        storage_exists = self._storage_path().exists()
        cookies_exists = self._cookies_path().exists()
        if storage_exists and cookies_exists:
            return await self.load()

        if login_callback is not None:
            await login_callback()
        return await self.save()
