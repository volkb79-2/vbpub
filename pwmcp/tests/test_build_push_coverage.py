from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "build-push.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_build_push_coverage", MODULE_PATH)
assert SPEC and SPEC.loader
build_push = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_push
SPEC.loader.exec_module(build_push)


def _config() -> build_push.BuilderConfig:
    return build_push.BuilderConfig(
        name="test-builder",
        memory="4g",
        memory_swap="12g",
        cpu_shares=128,
        cpu_quota=400000,
        cpu_period=100000,
    )


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


def test_create_builder_declares_all_governed_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(build_push, "run", calls.append)
    build_push._create_builder(_config())
    assert calls == [[
        "docker", "buildx", "create", "--name", "test-builder",
        "--driver", "docker-container",
        "--driver-opt", "memory=4g",
        "--driver-opt", "memory-swap=12g",
        "--driver-opt", "cpu-shares=128",
        "--driver-opt", "cpu-quota=400000",
        "--driver-opt", "cpu-period=100000",
    ]]


def test_ensure_builder_creates_missing_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = subprocess.CompletedProcess([], 1)
    monkeypatch.setattr(build_push.subprocess, "run", lambda *args, **kwargs: probe)
    calls: list[list[str]] = []
    monkeypatch.setattr(build_push, "run", calls.append)
    monkeypatch.setattr(
        build_push.subprocess,
        "check_output",
        lambda *args, **kwargs: "4294967296 12884901888 128 400000 100000",
    )

    build_push.ensure_builder(_config())
    assert calls[0][0:4] == ["docker", "buildx", "create", "--name"]
    assert calls[-1] == ["docker", "buildx", "inspect", "test-builder", "--bootstrap"]


def test_ensure_builder_recreates_builder_when_limits_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )
    outputs = iter([
        "1 2 3 4 5",
        "4294967296 12884901888 128 400000 100000",
    ])
    monkeypatch.setattr(build_push.subprocess, "check_output", lambda *args, **kwargs: next(outputs))
    calls: list[list[str]] = []
    monkeypatch.setattr(build_push, "run", calls.append)

    build_push.ensure_builder(_config())
    assert ["docker", "buildx", "rm", "test-builder"] in calls
    assert sum(call[:4] == ["docker", "buildx", "create", "--name"] for call in calls) == 1


def test_ensure_builder_refuses_persistent_limit_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )
    monkeypatch.setattr(
        build_push.subprocess,
        "check_output",
        lambda *args, **kwargs: "1 2 3 4 5",
    )
    monkeypatch.setattr(build_push, "run", lambda argv, cwd=None: None)
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
    monkeypatch.delenv("GITHUB_USERNAME", raising=False)
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    with pytest.raises(SystemExit):
        build_push.do_push()


def test_do_push_logs_in_bakes_and_syncs_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "load_vars", lambda: {})
    monkeypatch.setattr(build_push, "load_builder_config", _config)
    monkeypatch.setattr(build_push, "ensure_builder", lambda config: None)
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
    monkeypatch.setenv("GHCR_PACKAGE_NAMES", "pwmcp, bundle")
    login: list[tuple[list[str], bytes]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[None]:
        login.append((argv, kwargs["input"]))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(build_push.subprocess, "run", fake_run)
    bake: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(build_push, "run", lambda argv, cwd=None: bake.append((argv, cwd)))
    visibility: list[list[str]] = []
    monkeypatch.setattr(build_push, "sync_ghcr_package_visibility", visibility.append)

    build_push.do_push()
    assert login == [([
        "docker", "login", "ghcr.io", "-u", "owner", "--password-stdin",
    ], b"secret")]
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
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )
    monkeypatch.setattr(
        build_push.subprocess,
        "check_output",
        lambda *args, **kwargs: "4294967296 12884901888 128 400000 100000",
    )
    monkeypatch.setattr(build_push.sys, "argv", ["build-push.py", "--build"])
    runpy.run_path(str(MODULE_PATH), run_name="__main__")
