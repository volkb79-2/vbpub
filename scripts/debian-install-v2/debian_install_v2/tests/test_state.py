from __future__ import annotations

import json
from pathlib import Path

from debian_install_v2.config import Config
from debian_install_v2.state import StateStore


def test_state_manifest_never_serializes_bot_token(tmp_path: Path):
    config = Config(
        state_dir=str(tmp_path),
        telegram_bot_token="123:secret",
        telegram_chat_id="123123",
    )
    store = StateStore(str(tmp_path))
    store.save_new(StateStore.new(config))
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert "telegram_bot_token" not in manifest["config"]
    assert "123:secret" not in raw
    assert manifest["config"]["telegram_chat_id"] == "123123"
