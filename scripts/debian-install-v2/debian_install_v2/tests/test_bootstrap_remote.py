"""bootstrap-remote.py — the curl|python3 remote-install wrapper.

Filename has a hyphen (matches the sibling debian-install-v2.py entrypoint
convention), so it's loaded by path via importlib, same as
test_inuse_partition_editor.py does for inuse_partition_editor.py.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "bootstrap-remote.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bootstrap_remote", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


# --- env -> config translation -----------------------------------------

def test_build_config_maps_named_env_vars(mod, monkeypatch):
    monkeypatch.setenv("SWAP_DISK_TOTAL_GB", "64")
    monkeypatch.setenv("SWAP_FILE_COUNT", "4")
    monkeypatch.setenv("ZSWAP_COMPRESSOR", "zstd")
    monkeypatch.setenv("VM_SWAPPINESS", "50")
    monkeypatch.setenv("NEVER_REBOOT", "no")
    monkeypatch.setenv("AUTO_REBOOT_AFTER_STAGE1", "yes")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    config = mod.build_config()
    assert config == {
        "swap_disk_total_gb": 64,
        "swap_file_count": 4,
        "zswap_compressor": "zstd",
        "vm_swappiness": 50,
        "never_reboot": False,
        "auto_reboot_after_stage1": True,
        "telegram_bot_token": "123:token",
        "telegram_chat_id": "456",
    }


def test_build_config_empty_when_nothing_set(mod, monkeypatch):
    for name in list(mod._STRING_FIELDS) + list(mod._INT_FIELDS) + list(mod._BOOL_FIELDS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VBPUB_CONFIG_EXTRA_JSON", raising=False)
    assert mod.build_config() == {}


def test_auto_tristate_is_rejected_not_silently_guessed(mod, monkeypatch):
    monkeypatch.setenv("AUTO_REBOOT_AFTER_STAGE1", "auto")
    with pytest.raises(SystemExit, match="not yes/no"):
        mod.build_config()


@pytest.mark.parametrize("value", ["maybe", "y", "sometimes"])
def test_bool_env_rejects_unrecognized_values(mod, monkeypatch, value):
    monkeypatch.setenv("NEVER_REBOOT", value)
    with pytest.raises(SystemExit, match="not yes/no"):
        mod.build_config()


def test_int_env_rejects_non_numeric(mod, monkeypatch):
    monkeypatch.setenv("SWAP_FILE_COUNT", "eight")
    with pytest.raises(SystemExit, match="not an integer"):
        mod.build_config()


def test_extra_json_wins_over_named_vars(mod, monkeypatch):
    monkeypatch.setenv("SWAP_FILE_COUNT", "8")
    monkeypatch.setenv("VBPUB_CONFIG_EXTRA_JSON", json.dumps({"swap_file_count": 16, "credential_mode": "systemd"}))
    config = mod.build_config()
    assert config["swap_file_count"] == 16
    assert config["credential_mode"] == "systemd"


def test_extra_json_must_be_an_object(mod, monkeypatch):
    monkeypatch.setenv("VBPUB_CONFIG_EXTRA_JSON", "[1,2,3]")
    with pytest.raises(SystemExit, match="must be a JSON object"):
        mod.build_config()


# --- fetch_subtree -------------------------------------------------------

def _fake_tarball(files: dict[str, bytes], *, executable: set[str] = frozenset()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"vbpub-main/{name}")
            info.size = len(content)
            info.mode = 0o755 if name in executable else 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def test_fetch_subtree_extracts_only_the_matching_subtree(mod, monkeypatch, tmp_path):
    tarball = _fake_tarball({
        "scripts/debian-install-v2/debian-install-v2.py": b"#!/usr/bin/env python3\n",
        "scripts/debian-install-v2/debian_install_v2/installer.py": b"# installer\n",
        "scripts/other-tool/README.md": b"unrelated\n",
        "README.md": b"repo root readme\n",
    }, executable={"scripts/debian-install-v2/debian-install-v2.py"})

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))

    install_dir = tmp_path / "install"
    mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", install_dir, debug=False)

    assert (install_dir / "debian-install-v2.py").read_bytes() == b"#!/usr/bin/env python3\n"
    assert (install_dir / "debian_install_v2" / "installer.py").is_file()
    assert not (install_dir / "other-tool").exists()
    assert not (install_dir / "README.md").exists()
    mode = (install_dir / "debian-install-v2.py").stat().st_mode
    assert mode & 0o111


def test_fetch_subtree_raises_when_nothing_matches(mod, monkeypatch, tmp_path):
    tarball = _fake_tarball({"README.md": b"nothing relevant here\n"})
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))
    with pytest.raises(SystemExit, match="found nothing under"):
        mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", tmp_path / "install", debug=False)
