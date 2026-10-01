from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config


class StateError(RuntimeError):
    pass


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class StateStore:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "state.json"
        self.dry_run = False

    @staticmethod
    def new(config: Config) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": os.urandom(8).hex(),
            "phase": "stage1",
            "status": "running",
            "config": {key: value for key, value in asdict(config).items() if not key.startswith("telegram_bot_token")},
            "steps": {},
            "telegram_thread_id": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise StateError(f"state manifest does not exist: {self.path}")
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot load state manifest: {exc}") from exc
        if not isinstance(state, dict):
            raise StateError("state manifest root must be a JSON object")
        if state.get("schema_version") != 1 or not isinstance(state.get("config"), dict):
            raise StateError("state manifest has an unsupported or corrupt schema")
        if "steps" in state and not isinstance(state["steps"], dict):
            raise StateError("state manifest steps must be a JSON object")
        return state

    @staticmethod
    def _without_secrets(state: dict[str, Any]) -> dict[str, Any]:
        config = state.get("config")
        if isinstance(config, dict):
            state["config"] = {key: value for key, value in config.items() if not key.startswith("telegram_bot_token")}
        return state

    def save_new(self, state: dict[str, Any]) -> None:
        if self.dry_run:
            return
        state = self._without_secrets(state)
        _atomic_write(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    def save(self, **changes: Any) -> dict[str, Any]:
        if self.dry_run:
            return self.load()
        state = self.load()
        state.update(changes)
        _atomic_write(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")
        return state

    def mark_step(self, name: str, status: str, detail: str = "") -> None:
        if self.dry_run:
            return
        state = self.load()
        state.setdefault("steps", {})[name] = {"status": status, "detail": detail}
        _atomic_write(self.path, json.dumps(state, indent=2, sort_keys=True) + "\n")
