"""copilot, openclaw, claudelink and aider are installed at the version `cmru versions` vetted under the
14-day rule (MDTC-03 X1); the R16 tools (claude, codex, reasonix, opencode, pi) are unaffected."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_ai_cli_tools as installer  # noqa: E402
import stage_tool_artifacts as stage  # noqa: E402
from test_oci_tool_staging import fake_version_records  # noqa: E402

VETTED = {"npm.copilot": "1.0.92-4", "npm.openclaw": "2026.9.8", "npm.claudelink": "1.6.1"}
ENV_KEYS = {"COPILOT_VER": "npm.copilot", "OPENCLAW_VER": "npm.openclaw", "CLAUDELINK_VER": "npm.claudelink"}
AIDER_PIN = {"AIDER_COMMIT": "5dc9490bb35f9729ef2c95d00a19ccd30c26339c", "AIDER_VER": "0.86.3.dev53+g5dc9490bb"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("COPILOT_VERSION", "OPENCLAW_VERSION", "CLAUDELINK_VERSION", "AIDER_VERSION", "AIDER_COMMIT",
                 "INSTALL_COPILOT", "INSTALL_OPENCLAW", "INSTALL_CLAUDELINK", "INSTALL_AIDER"):
        monkeypatch.delenv(name, raising=False)


def test_the_staged_versions_equal_the_cmru_record() -> None:
    vetted = stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED))

    assert vetted == {**{env: VETTED[target] for env, target in ENV_KEYS.items()}, **AIDER_PIN}


def test_resolve_versions_stages_the_record_not_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_oci_tool_staging import _stub_other_resolvers

    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(stage, "_resolve_claude_code_version", lambda _v: "2.1.302")
    monkeypatch.setattr(stage, "_load_version_records", lambda path=None: fake_version_records(VETTED))

    resolved = stage._resolve_versions()

    assert {k: resolved[k] for k in ENV_KEYS} == {env: VETTED[target] for env, target in ENV_KEYS.items()}
    assert (resolved["AIDER_COMMIT"], resolved["AIDER_VER"]) == (AIDER_PIN["AIDER_COMMIT"], AIDER_PIN["AIDER_VER"])


def test_an_unresolved_target_fails_staging_naming_cmru_versions_resolve() -> None:
    records = fake_version_records(VETTED)
    del records["npm.openclaw"]["resolved"]
    for broken in (records, {**fake_version_records(VETTED), "npm.openclaw": {"resolved": {"version": "", "resolved_at": "x"}}}):
        with pytest.raises(stage.StageError) as ctx:
            stage._vetted_ai_cli_versions(lambda broken=broken: broken)
        assert "cmru versions resolve" in str(ctx.value)


def test_a_missing_target_fails_staging_naming_cmru_versions_resolve() -> None:
    partial = {k: v for k, v in VETTED.items() if k != "npm.openclaw"}
    with pytest.raises(stage.StageError) as ctx:
        stage._vetted_ai_cli_versions(lambda: fake_version_records(partial))
    assert "cmru versions resolve" in str(ctx.value)


def test_a_stale_record_fails_and_a_fresh_one_passes() -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=stage.RECORD_MAX_AGE_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    young = (datetime.now(timezone.utc) - timedelta(days=stage.RECORD_MAX_AGE_DAYS - 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with pytest.raises(stage.StageError, match="cmru versions resolve"):
        stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED, resolved_at=old))
    assert stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED, resolved_at=young))["COPILOT_VER"] == "1.0.92-4"
    with pytest.raises(stage.StageError, match="cmru versions resolve"):
        stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED, resolved_at="yesterday"))


def test_an_unreadable_cmru_toml_fails_naming_cmru_versions_resolve(tmp_path: Path) -> None:
    with pytest.raises(stage.StageError, match="cmru versions resolve"):
        stage._load_version_records(tmp_path / "missing.toml")
    (tmp_path / "cmru.toml").write_text("[project]\nid = 'x'\n", encoding="utf-8")
    with pytest.raises(stage.StageError, match="cmru versions resolve"):
        stage._load_version_records(tmp_path / "cmru.toml")


def test_the_staging_never_runs_cmru_or_the_network_for_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("staging must read the committed record offline"))
    assert stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED))["CLAUDELINK_VER"] == "1.6.1"


@pytest.mark.parametrize("env_name", ["COPILOT_VERSION", "OPENCLAW_VERSION", "CLAUDELINK_VERSION", "AIDER_VERSION", "AIDER_COMMIT"])
def test_an_environment_pin_is_refused_not_ignored(monkeypatch: pytest.MonkeyPatch, env_name: str) -> None:
    monkeypatch.setenv(env_name, "9.9.9")
    with pytest.raises(stage.StageError) as ctx:
        stage._vetted_ai_cli_versions(lambda: fake_version_records(VETTED))
    assert env_name in str(ctx.value) and "reason" in str(ctx.value) and "cmru versions resolve" in str(ctx.value)


def test_a_tool_that_is_not_installed_needs_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTALL_OPENCLAW", "false")
    partial = {k: v for k, v in VETTED.items() if k != "npm.openclaw"}

    vetted = stage._vetted_ai_cli_versions(lambda: fake_version_records(partial))

    assert "OPENCLAW_VER" not in vetted and len(vetted) == 4  # copilot, claudelink + aider's commit and version


def test_none_of_the_four_can_reach_registry_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging: the registry resolvers are never asked about the four packages."""
    from test_oci_tool_staging import _stub_other_resolvers

    asked: list[tuple[str, str]] = []
    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(stage, "_resolve_claude_code_version", lambda _v: "2.1.302")
    monkeypatch.setattr(stage, "_resolve_npm_version", lambda requested, package: asked.append(("npm", package)) or "x")
    monkeypatch.setattr(stage, "_resolve_pypi_version", lambda requested, package: asked.append(("pypi", package)) or "x")
    monkeypatch.setattr(stage, "_load_version_records", lambda path=None: fake_version_records(VETTED))

    stage._resolve_versions()

    forbidden = {("npm", "openclaw"), ("npm", "@github/copilot"), ("npm", "claudelink"), ("pypi", "aider-chat")}
    assert not forbidden & set(asked), asked


@pytest.mark.parametrize("function,ver,label", [
    ("install_openclaw", "OPENCLAW_VER", "openclaw"), ("install_copilot", "COPILOT_VER", "copilot"),
    ("install_claudelink", "CLAUDELINK_VER", "claudelink"), ("install_aider", "AIDER_VER", "aider"),
])
@pytest.mark.parametrize("value", [None, "", "latest", "main"])
def test_the_installers_refuse_an_unstaged_latest_or_main_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: str, ver: str, label: str, value: str | None
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(installer, "require_command", lambda *a, **k: None)
    for name in (ver, ver.replace("_VER", "_VERSION")):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ver.replace("_VER", "_VERSION"), "9.9.9")  # a build ARG must not stand in
    if value is not None:
        monkeypatch.setenv(ver, value)
    ctx = type("Ctx", (), {"venv_python": tmp_path / "python"})()

    with pytest.raises(installer.InstallerError, match=ver):
        getattr(installer, function)(*((ctx,) if function == "install_aider" else ()))

    assert calls == []


def test_the_installers_install_exactly_the_staged_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(installer, "require_command", lambda *a, **k: None)
    for ver, value in (("OPENCLAW_VER", "2026.9.8"), ("COPILOT_VER", "1.0.92-4"), ("CLAUDELINK_VER", "1.6.1"),
                       ("AIDER_VER", AIDER_PIN["AIDER_VER"]), ("AIDER_COMMIT", AIDER_PIN["AIDER_COMMIT"])):
        monkeypatch.setenv(ver, value)
    monkeypatch.setattr(installer, "installed_pip_version", lambda _python, _package: AIDER_PIN["AIDER_VER"])
    ctx = type("Ctx", (), {"venv_python": tmp_path / "python"})()

    installer.install_openclaw()
    installer.install_copilot()
    installer.install_claudelink()
    installer.install_aider(ctx)

    flat = [" ".join(c) for c in calls]
    assert flat[0].endswith("openclaw@2026.9.8") and flat[1].endswith("@github/copilot@1.0.92-4")
    assert flat[2].endswith("claudelink@1.6.1") and flat[3].endswith("git+https://github.com/Aider-AI/aider.git@" + AIDER_PIN["AIDER_COMMIT"])


def test_the_r16_tools_are_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """claude, codex, reasonix, opencode, pi still follow upstream latest: exact version recorded, no cmru, no age window."""
    from test_oci_tool_staging import _stub_other_resolvers

    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(stage, "_resolve_claude_code_version", lambda _v: "2.1.302")
    monkeypatch.setattr(stage, "_load_version_records", lambda path=None: fake_version_records(VETTED))

    resolved = stage._resolve_versions()

    assert (resolved["CLAUDE_CODE_VER"], resolved["CODEX_VER"], resolved["REASONIX_VER"], resolved["OPENCODE_VER"], resolved["PI_VER"]) == (
        "2.1.302", "codex-x", "npm-reasonix", "npm-opencode-ai", "npm-@earendil-works/pi-coding-agent")
    for r16 in ("CODEX_VER", "CLAUDE_CODE_VER", "REASONIX_VER", "OPENCODE_VER", "PI_VER"):
        assert r16 not in {env for env in ENV_KEYS}


def test_the_r16_tool_pins_still_work_through_their_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_VERSION", "0.34.0")
    assert stage._resolve_codex_version("0.34.0")  # an R16 env pin is untouched by the cmru path


def test_the_vetted_state_is_declared_in_the_project_config() -> None:
    config = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
    targets = {k: v for k, v in config["versions"]["targets"].items() if k.startswith("npm.")}
    assert set(ENV_KEYS.values()) <= set(targets)
    assert "pypi.aider-chat" not in targets, "aider is the documented main-commit exception (aider-main-pin.toml)"
    assert targets["npm.copilot"]["npm"]["name"] == "@github/copilot"
    assert targets["npm.openclaw"]["npm"]["name"] == "openclaw"
    assert targets["npm.claudelink"]["npm"]["name"] == "claudelink"
    for target in targets.values():
        assert target["constraint"] == "*" and "version" not in target, "an override needs a reason and is a reviewed edit"
        assert target["resolved"]["version"], "the resolved state is committed"
    r16 = re.compile(r"claude-code|claude$|codex|reasonix|opencode|earendil")
    assert not [name for name in targets if r16.search(name)], "the R16 tools are not vetted by cmru"


def test_the_build_inputs_no_longer_carry_a_version_arg_for_the_four() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    bake = (ROOT / "docker-bake.hcl").read_text(encoding="utf-8")
    cmru = (ROOT / "cmru.toml").read_text(encoding="utf-8")
    for name in ("COPILOT_VERSION", "OPENCLAW_VERSION", "CLAUDELINK_VERSION", "AIDER_VERSION"):
        assert name not in dockerfile and name not in bake and name not in cmru, name


def test_the_aider_pin_file_is_an_exact_commit_and_names_its_exit_condition() -> None:
    pin = stage._aider_main_pin()
    assert re.fullmatch(r"[0-9a-f]{40}", pin["AIDER_COMMIT"]) and pin["AIDER_COMMIT"][:9] in pin["AIDER_VER"]
    text = (ROOT / "aider-main-pin.toml").read_text(encoding="utf-8")
    assert "until an aider release supports Python 3.14" in text


@pytest.mark.parametrize("body", [
    'commit = "main"\nversion = "0.86.3.dev53+g5dc9490bb"\n',
    'commit = "5dc9490bb"\nversion = "0.86.3.dev53+g5dc9490bb"\n',
    'commit = "5dc9490bb35f9729ef2c95d00a19ccd30c26339c"\nversion = "0.86.2"\n',
    'commit = "5dc9490bb35f9729ef2c95d00a19ccd30c26339c"\n',
    "not toml [",
])
def test_an_unpinned_or_inconsistent_aider_pin_fails_staging(tmp_path: Path, body: str) -> None:
    pin = tmp_path / "aider-main-pin.toml"
    pin.write_text(body, encoding="utf-8")
    with pytest.raises(stage.StageError):
        stage._aider_main_pin(pin)
    with pytest.raises(stage.StageError):
        stage._aider_main_pin(tmp_path / "missing.toml")


@pytest.mark.parametrize("commit", [None, "", "main", "5dc9490bb", "5DC9490BB35F9729EF2C95D00A19CCD30C26339C"])
def test_the_aider_installer_refuses_an_unpinned_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, commit: str | None) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setenv("AIDER_VER", AIDER_PIN["AIDER_VER"])
    monkeypatch.delenv("AIDER_COMMIT", raising=False)
    if commit is not None:
        monkeypatch.setenv("AIDER_COMMIT", commit)
    with pytest.raises(installer.InstallerError, match="AIDER_COMMIT"):
        installer.install_aider(type("Ctx", (), {"venv_python": tmp_path / "python"})())
    assert calls == []


def test_the_aider_installer_fails_when_the_built_version_differs_from_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "run_command", lambda *a, **k: None)
    monkeypatch.setattr(installer, "installed_pip_version", lambda _p, _n: "0.86.3.dev99+gdeadbeef0")
    monkeypatch.setenv("AIDER_VER", AIDER_PIN["AIDER_VER"])
    monkeypatch.setenv("AIDER_COMMIT", AIDER_PIN["AIDER_COMMIT"])
    with pytest.raises(installer.InstallerError, match="records"):
        installer.install_aider(type("Ctx", (), {"venv_python": tmp_path / "python"})())
