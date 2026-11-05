"""Client version <-> server release mapping, the skew policy, `pwmcp versions` and the doctor skew check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.version import Version

import pwmcp_client
from pwmcp_client import cli as cli_module
from pwmcp_client import diagnostics
from pwmcp_client.release import Skew, assess_skew, release_to_version, split_release, version_to_release

PWMCP = Path(__file__).resolve().parents[2]


def _payload(release: str) -> dict:
    return {
        "schema_version": 1,
        "release": release,
        "playwright": {"python": release.split("-")[0], "protocol": "1.64"},
        "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
        "run_server": {"limits": {
            "default_lease_seconds": 10, "max_lease_seconds": 20, "max_clients": 2, "idle_recycle_seconds": 30}},
    }


@pytest.mark.parametrize("release, version", [
    ("1.64.1-r2", "1.64.1.post2"),
    ("1.61.0-r10", "1.61.0.post10"),
    ("2.0.0-r1", "2.0.0.post1"),
    ("1.64.1-r02", "1.64.1.post2"),
])
def test_mapping_round_trips_and_is_pep440_canonical(release: str, version: str) -> None:
    assert release_to_version(release) == version
    assert Version(release) == Version(version)  # PEP 440 itself reads -rN as .postN
    assert str(Version(release)) == version  # so installed metadata always equals the VERSION file
    assert version_to_release(version) == release.replace("r02", "r2")


@pytest.mark.parametrize("bad", ["1.64.1", "1.64-r2", "v1.64.1-r2", "1.64.1-r", "1.64.1-rc2", "", "1.64.1.post2"])
def test_release_to_version_refuses_non_releases(bad: str) -> None:
    with pytest.raises(ValueError, match="not a pwmcp release"):
        release_to_version(bad)
    with pytest.raises(ValueError, match="not a pwmcp release"):
        split_release(bad)


@pytest.mark.parametrize("bad", ["0.1.0", "1.64.1", "1.64.1-r2", "1.64.1.post", "1.64.1.dev2", ""])
def test_version_to_release_refuses_non_client_versions(bad: str) -> None:
    with pytest.raises(ValueError, match="not a pwmcp client version"):
        version_to_release(bad)


def test_split_release() -> None:
    assert split_release("1.64.1-r2") == ("1.64.1", 2)


def test_skew_policy() -> None:
    assert assess_skew("1.64.1.post2", "1.64.1-r2") == Skew("ok", "client and server are the same release (1.64.1-r2)")
    warn = assess_skew("1.64.1.post2", "1.64.1-r3")
    assert warn.status == "warn" and "same Playwright 1.64.1, r2 vs r3" in warn.message
    fail = assess_skew("1.64.1.post2", "1.63.0-r1")
    assert fail.status == "fail" and "Playwright 1.63.0" in fail.message and "pins Playwright 1.64.1" in fail.message
    dev = assess_skew("0.1.0", "1.64.1-r2")
    assert dev.status == "warn" and "cannot compare" in dev.message
    assert assess_skew("1.64.1.post2", "weird").status == "warn"


def test_committed_client_version_is_the_mapped_contract_release() -> None:
    """The repo VERSION file always equals release_to_version(pwmcp.contract.json release)."""
    contract = json.loads((PWMCP / "pwmcp.contract.json").read_text(encoding="utf-8"))
    version = (PWMCP / "client" / "src" / "pwmcp_client" / "VERSION").read_text(encoding="utf-8")
    assert version == release_to_version(contract["release"]) + "\n"
    assert cli_module.identity().version == version.strip()


def _contract_file(tmp_path: Path, release: str) -> str:
    path = tmp_path / f"{release}.json"
    path.write_text(json.dumps(_payload(release)), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("release, status", [
    (None, "ok"), ("-r99", "warn"), ("OTHERPW", "fail"),
])
def test_versions_verb_reports_client_server_and_skew(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], release: str | None, status: str
) -> None:
    client_release = pwmcp_client.__version__ and version_to_release(pwmcp_client.__version__)
    pw = client_release.split("-")[0]
    server = {None: client_release, "-r99": f"{pw}-r99", "OTHERPW": "0.9.0-r1"}[release]
    source = _contract_file(tmp_path, server)
    assert cli_module.main(["versions", "--contract", source]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"pwmcp {pwmcp_client.__version__} (server {server})\n"
    assert (status in captured.err) == (status != "ok")
    assert cli_module.main(["versions", "--json", "--contract", source]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "client": pwmcp_client.__version__, "client_release": client_release, "server": server,
        "skew": status, "skew_message": payload["skew_message"],
    }


def test_versions_verb_when_the_server_is_unreachable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = str(tmp_path / "absent.json")
    assert cli_module.main(["versions", "--contract", missing]) == 0
    assert capsys.readouterr().out == f"pwmcp {pwmcp_client.__version__} (server unreachable)\n"
    assert cli_module.main(["versions", "--json", "--contract", missing]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["server"] is None and payload["skew"] == "unknown"


def test_versions_json_for_a_development_client_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cli_extended import CliIdentity

    cli = cli_module.build_cli(CliIdentity("PWMCP", "0.1.0", "client utility", "pwmcp"))
    assert cli.run(argv=["versions", "--json", "--contract", _contract_file(tmp_path, "1.64.1-r2")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["client_release"] is None and payload["skew"] == "warn"


def test_doctor_skew_check_statuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client_release = version_to_release(pwmcp_client.__version__)
    pw = client_release.split("-")[0]
    assert cli_module.main(["doctor", "--check", "skew", "--contract", _contract_file(tmp_path, client_release)]) == 0
    assert "[OK] skew: client and server are the same release" in capsys.readouterr().out
    # only the r-number differs -> WARN (exit status stays 0)
    assert cli_module.main(["doctor", "--check", "skew", "--contract", _contract_file(tmp_path, f"{pw}-r99")]) == 0
    out = capsys.readouterr().out
    assert "[WARN] skew:" in out and "remedy:" in out
    # a different Playwright version -> FAIL
    assert cli_module.main(["doctor", "--check", "skew", "--contract", _contract_file(tmp_path, "0.9.0-r1")]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] skew:" in out and "pwmcp-client-install.py --version 0.9.0-r1" in out
    # no contract -> skipped
    assert cli_module.main(["doctor", "--check", "skew", "--contract", str(tmp_path / "none.json")]) == 0
    assert "[SKIP] skew:" in capsys.readouterr().out


def test_client_version_helper_matches_identity() -> None:
    assert diagnostics.client_version() == cli_module.identity().version
