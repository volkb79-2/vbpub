from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "build-push.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_build_push_coverage", MODULE_PATH)
assert SPEC and SPEC.loader
build_push = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_push
SPEC.loader.exec_module(build_push)


REMOTE_INSPECT = """Name:           test-builder
Driver:        remote
Nodes:
Name:           test-builder
Endpoint:       unix:///run/test-buildkit.sock
Status:         running
BuildKit:       v0.32.2
"""


def _config() -> build_push.BuilderConfig:
    return build_push.BuilderConfig(
        name="test-builder",
        endpoint="unix:///run/test-buildkit.sock",
    )


def test_log_and_fail_flush_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: calls.append(kwargs))
    build_push.log("message")
    with pytest.raises(SystemExit):
        build_push.fail("failure")
    assert calls == [{"flush": True}, {"file": sys.stderr, "flush": True}]


def test_sync_visibility_is_noop_without_package_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "GitHubPackages", lambda *args: pytest.fail("not called"))
    build_push.sync_ghcr_package_visibility([])
    build_push.sync_ghcr_package_visibility(["", "   "])


def test_sync_visibility_reports_all_missing_identity_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("GITHUB_USERNAME", "GITHUB_REPO", "GITHUB_PUSH_PAT", "GITHUB_OWNER_TYPE"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit):
        build_push.sync_ghcr_package_visibility(["pwmcp"])

    error = capsys.readouterr().err
    assert "GITHUB_USERNAME" in error
    assert "GITHUB_OWNER_TYPE" in error


@pytest.mark.parametrize("missing", ["GITHUB_USERNAME", "GITHUB_REPO", "GITHUB_PUSH_PAT", "GITHUB_OWNER_TYPE"])
def test_sync_visibility_refuses_each_missing_identity_field(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    for name, value in {
        "GITHUB_USERNAME": "owner",
        "GITHUB_REPO": "repo",
        "GITHUB_PUSH_PAT": "secret",
        "GITHUB_OWNER_TYPE": "User",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit):
        build_push.sync_ghcr_package_visibility(["pwmcp"])


def test_sync_visibility_mirrors_each_normalized_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
    monkeypatch.setenv("GITHUB_OWNER_TYPE", "User")
    calls: list[tuple[str, object]] = []

    class FakePackages:
        def __init__(self, *args: str) -> None:
            assert args == ("owner", "repo", "secret", "User")

        def repo_visibility(self) -> str:
            return "private"

        def mirror_package_visibility(self, name: str, *, expected_visibility: str) -> None:
            calls.append((name, expected_visibility))

    monkeypatch.setattr(build_push, "GitHubPackages", FakePackages)
    build_push.sync_ghcr_package_visibility([" pwmcp ", "", "bundle"])
    assert calls == [("pwmcp", "private"), ("bundle", "private")]


def test_builder_config_is_frozen() -> None:
    config = _config()
    with pytest.raises(FrozenInstanceError):
        config.name = "changed"  # type: ignore[misc]


def test_run_uses_pwmcp_directory_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], bool, str]] = []

    def fake_run(argv: list[str], *, check: bool, cwd: str) -> None:
        calls.append((argv, check, cwd))

    monkeypatch.setattr(build_push.subprocess, "run", fake_run)
    build_push.run(["docker", "version"])
    assert calls == [(["docker", "version"], True, str(build_push.PWMCP_DIR))]


def test_run_accepts_explicit_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda argv, *, check, cwd: calls.append(cwd),
    )
    build_push.run(["docker", "version"], cwd=tmp_path)
    assert calls == [str(tmp_path)]


def test_assert_remote_builder_accepts_configured_endpoint() -> None:
    build_push._assert_remote_builder(_config(), REMOTE_INSPECT)


@pytest.mark.parametrize(
    "output",
    [
        "Driver:        docker-container\nEndpoint:      unix:///run/test-buildkit.sock\n",
        "Driver:        remote\nEndpoint:      unix:///run/other.sock\n",
        "Driver:        remote\n",
    ],
)
def test_assert_remote_builder_rejects_driver_or_endpoint(output: str) -> None:
    with pytest.raises(SystemExit):
        build_push._assert_remote_builder(_config(), output)


def test_inspect_builder_reports_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="socket missing"),
    )
    with pytest.raises(SystemExit):
        build_push._inspect_builder(_config(), bootstrap=False)


def test_ensure_builder_verifies_registration_and_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=REMOTE_INSPECT, stderr="")

    monkeypatch.setattr(build_push.subprocess, "run", fake_run)
    build_push.ensure_builder(_config())
    assert [argv for argv, _ in calls] == [
        ["docker", "buildx", "inspect", "test-builder"],
        ["docker", "buildx", "inspect", "test-builder", "--bootstrap"],
    ]
    assert all(kwargs == {"capture_output": True, "text": True, "check": False} for _, kwargs in calls)


def test_ensure_builder_refuses_bootstrap_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        subprocess.CompletedProcess([], 0, stdout=REMOTE_INSPECT, stderr=""),
        subprocess.CompletedProcess([], 1, stdout="", stderr="connection lost"),
    ])
    monkeypatch.setattr(build_push.subprocess, "run", lambda *args, **kwargs: next(responses))
    with pytest.raises(SystemExit):
        build_push.ensure_builder(_config())


def test_do_build_loads_prepared_vars_and_bakes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(build_push, "load_vars", lambda: {"PWMCP_VERSION": "x"})
    monkeypatch.setattr(build_push, "load_builder_config", _config)
    monkeypatch.setattr(build_push, "ensure_builder", lambda config: None)
    monkeypatch.setattr(build_push, "run", lambda argv, cwd=None: calls.append((argv, cwd)))
    monkeypatch.setenv("PLAYWRIGHT_VERSION", "1.63.0")
    monkeypatch.setenv("PWMCP_VERSION", "1.63.0-r2")

    build_push.do_build()
    assert calls == [(
        ["docker", "buildx", "bake", "--builder", "test-builder", "all", "--load"],
        build_push.PWMCP_DIR,
    )]


def test_do_push_requires_credentials_after_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "load_vars", lambda: {})
    monkeypatch.setattr(build_push, "load_builder_config", _config)
    monkeypatch.setattr(build_push, "ensure_builder", lambda config: None)
    for missing in ("GITHUB_USERNAME", "GITHUB_PUSH_PAT"):
        monkeypatch.setenv("GITHUB_USERNAME", "owner")
        monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
        monkeypatch.delenv(missing)
        with pytest.raises(SystemExit):
            build_push.do_push()


def test_do_push_logs_in_bakes_and_syncs_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "load_vars", lambda: {})
    monkeypatch.setattr(build_push, "load_builder_config", _config)
    monkeypatch.setattr(build_push, "ensure_builder", lambda config: None)
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
    monkeypatch.setenv("GHCR_PACKAGE_NAMES", "pwmcp, bundle")
    login: list[tuple[list[str], bytes, bool]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[None]:
        login.append((argv, kwargs["input"], kwargs["check"]))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(build_push.subprocess, "run", fake_run)
    bake: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(build_push, "run", lambda argv, cwd=None: bake.append((argv, cwd)))
    visibility: list[list[str]] = []
    monkeypatch.setattr(build_push, "sync_ghcr_package_visibility", visibility.append)

    build_push.do_push()
    assert login == [([
        "docker", "login", "ghcr.io", "-u", "owner", "--password-stdin",
    ], b"secret", True)]
    assert bake == [(
        ["docker", "buildx", "bake", "--builder", "test-builder", "all", "--push"],
        build_push.PWMCP_DIR,
    )]
    assert visibility == [["pwmcp", "bundle"]]


@pytest.mark.parametrize("flag, expected", [("--build", "build"), ("--push", "push")])
def test_main_dispatches_selected_operation(
    monkeypatch: pytest.MonkeyPatch, flag: str, expected: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(build_push, "do_build", lambda: calls.append("build"))
    monkeypatch.setattr(build_push, "do_push", lambda: calls.append("push"))
    monkeypatch.setattr(build_push.sys, "argv", ["build-push.py", flag])
    build_push.main()
    assert calls == [expected]


def test_module_entrypoint_dispatches_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover the script entrypoint used by CMRU's ``python3 build-push.py``."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["docker", "buildx", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, stdout=REMOTE_INSPECT, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(build_push.subprocess, "run", fake_run)
    monkeypatch.setattr(sys.modules["_vars"], "load_vars", lambda: {})
    monkeypatch.setattr(build_push.sys, "argv", ["build-push.py", "--build"])
    runpy.run_path(str(MODULE_PATH), run_name="__main__")


def test_main_requires_an_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push.sys, "argv", ["build-push.py"])
    monkeypatch.setattr(build_push, "do_push", lambda: pytest.fail("missing operation must be rejected by argparse"))
    with pytest.raises(SystemExit):
        build_push.main()
