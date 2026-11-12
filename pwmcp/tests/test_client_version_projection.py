"""The resolver's client VERSION projection: regeneration, the check and every refusal."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve-playwright-version.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_resolver_client_version", MODULE_PATH)
assert SPEC and SPEC.loader
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


def test_update_writes_the_mapped_version(tmp_path: Path) -> None:
    target = tmp_path / "VERSION"
    resolver.update_client_version(target, "1.64.1-r3")
    assert target.read_text(encoding="utf-8") == "1.64.1.post3\n"


def test_update_refuses_a_non_release(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        resolver.update_client_version(tmp_path / "VERSION", "1.64.1")
    assert "not a pwmcp release" in capsys.readouterr().err


def test_check_accepts_the_mapped_version_and_names_every_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = tmp_path / "VERSION"
    monkeypatch.setattr(resolver, "CLIENT_VERSION_FILE", version)
    version.write_text("1.64.1.post2\n", encoding="utf-8")
    resolver.check_client_version("1.64.1-r2")
    version.write_text("0.1.0\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.check_client_version("1.64.1-r2")
    assert "requires '1.64.1.post2'" in capsys.readouterr().err
    version.unlink()
    with pytest.raises(SystemExit):
        resolver.check_client_version("1.64.1-r2")
    assert "is ''" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        resolver.check_client_version("oops")
    assert "not a pwmcp release" in capsys.readouterr().err


def test_helper_load_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(resolver, "CLIENT_RELEASE_MODULE", tmp_path / "release.txt")  # no .py suffix: no loader
    with pytest.raises(SystemExit):
        resolver._release_helper()
    assert "cannot load" in capsys.readouterr().err
