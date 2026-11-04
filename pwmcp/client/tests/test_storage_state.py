"""Operator-supplied storage state: scoping to one host, the 0600 rule, and never echoing values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pwmcp_client import storage_state as ss

SECRET = "s3cr3t-session-value"
STATE = {
    "cookies": [
        {"name": "cf_clearance", "value": SECRET, "domain": ".geekbench.com", "path": "/"},
        {"name": "sid", "value": SECRET, "domain": "browser.geekbench.com", "path": "/"},
        {"name": "other", "value": SECRET, "domain": "elsewhere.example", "path": "/"},
        {"name": "lookalike", "value": SECRET, "domain": "notgeekbench.com", "path": "/"},
        {"name": "nodomain", "value": SECRET},
        "not-a-cookie",
    ],
    "origins": [
        {"origin": "https://browser.geekbench.com", "localStorage": [{"name": "k", "value": SECRET}]},
        {"origin": "https://browser.geekbench.com:8443", "localStorage": []},
        {"origin": "http://browser.geekbench.com", "localStorage": []},
        {"origin": "https://other.example", "localStorage": []},
        {"origin": "https://bad.example:99999999", "localStorage": []},
        {"origin": 7},
    ],
}


def _write(tmp_path: Path, data: object, mode: int = 0o600, name: str = "state.json") -> Path:
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def test_only_the_target_host_is_applied(tmp_path: Path) -> None:
    scoped = ss.load_scoped(_write(tmp_path, STATE), "https://browser.geekbench.com/v7/cpu/1")
    assert [c["name"] for c in scoped.state["cookies"]] == ["cf_clearance", "sid"]
    assert [o["origin"] for o in scoped.state["origins"]] == ["https://browser.geekbench.com"]
    assert (scoped.cookies, scoped.origins) == (2, 1)


def test_a_parent_domain_cookie_does_not_leak_to_a_lookalike_host(tmp_path: Path) -> None:
    scoped = ss.load_scoped(_write(tmp_path, STATE), "https://notgeekbench.com/")
    assert [c["name"] for c in scoped.state["cookies"]] == ["lookalike"]


def test_default_ports_are_normalised_and_explicit_ports_must_match(tmp_path: Path) -> None:
    path = _write(tmp_path, STATE)
    assert ss.load_scoped(path, "https://browser.geekbench.com:443/").origins == 1
    assert ss.load_scoped(path, "https://browser.geekbench.com:8443/").origins == 1
    assert ss.load_scoped(path, "http://browser.geekbench.com/").origins == 1


def test_a_file_with_nothing_for_the_host_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ss.StorageStateError, match="holds no cookies or origins for unrelated.test"):
        ss.load_scoped(_write(tmp_path, STATE), "https://unrelated.test/")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_file_readable_by_group_or_others_is_refused(tmp_path: Path, mode: int) -> None:
    path = _write(tmp_path, STATE, mode=mode)
    with pytest.raises(ss.StorageStateError, match=rf"accessible by group or others \(mode {mode:04o}\).*chmod 600") as info:
        ss.load_scoped(path, "https://browser.geekbench.com/")
    assert SECRET not in str(info.value)


def test_a_missing_file_a_directory_and_bad_json_are_refused_without_content(tmp_path: Path) -> None:
    with pytest.raises(ss.StorageStateError, match="cannot read the storage-state file"):
        ss.load_scoped(tmp_path / "missing.json", "https://a.test/")
    with pytest.raises(ss.StorageStateError, match="not a regular file"):
        ss.load_scoped(tmp_path, "https://a.test/")
    bad = _write(tmp_path, '{"cookies": "' + SECRET, name="bad.json")
    with pytest.raises(ss.StorageStateError, match="not readable JSON") as info:
        ss.load_scoped(bad, "https://a.test/")
    assert SECRET not in str(info.value) and SECRET not in repr(info.value.__cause__)


def test_a_read_failure_is_reported_without_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, STATE)

    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError(f"denied: {SECRET}")

    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(ss.StorageStateError, match="not readable JSON") as info:
        ss.load_scoped(path, "https://browser.geekbench.com/")
    assert SECRET not in str(info.value)


@pytest.mark.parametrize("data", [[], {"cookies": {}}, {"cookies": [], "origins": "x"}, "null"])
def test_a_non_playwright_shape_is_refused(tmp_path: Path, data: object) -> None:
    with pytest.raises(ss.StorageStateError, match="not a Playwright storage state"):
        ss.load_scoped(_write(tmp_path, data), "https://a.test/")


def test_cookies_only_and_origins_only_files_work(tmp_path: Path) -> None:
    cookies_only = _write(tmp_path, {"cookies": [{"domain": "a.test", "name": "n", "value": "v"}]}, name="c.json")
    assert ss.load_scoped(cookies_only, "https://a.test/").state["origins"] == []
    origins_only = _write(tmp_path, {"origins": [{"origin": "https://a.test"}]}, name="o.json")
    assert ss.load_scoped(origins_only, "https://a.test/").state["cookies"] == []


def test_tilde_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path, STATE)
    assert ss.load_scoped("~/state.json", "https://browser.geekbench.com/").cookies == 2
