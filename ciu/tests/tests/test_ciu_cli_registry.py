"""The registry adapters: what the shared parser hands the implementation layer (D-ARCH).

Every public command is validated by `ciu.cli_registry` and re-spelled into the token list
the implementation behind it understands. These tests run the REAL ``cli.main`` with the
implementation layer recorded (``cli_review_harness``) and pin that re-spelling, plus the small
policies the registry owns (exit status of a stopped implementation, the version/identity
policy shared with every delegated registry).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_review_harness as harness  # noqa: E402
from ciu import cli, cli_registry  # noqa: E402


def _tokens(*argv: str) -> list[str]:
    status, out, err, recorded = harness.run(list(argv))
    assert status == 0, (argv, out, err)
    assert len(recorded) == 1, recorded
    return recorded[0]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # value options keep their spelling and order; repeatable options repeat
        (["up", "--profile", "a", "--profile", "b", "--phases", "1,2"],
         ["up", "--profile", "a", "--profile", "b", "--phases", "1,2"]),
        # library controls become the implementation's flags
        (["up", "--yes", "--dry-run"], ["up", "--yes", "--dry-run"]),
        # CIU-201: --dry-run reaches the implementation of the two removal verbs
        (["clean", "--dry-run"], ["clean", "--dry-run"]),
        (["worktree", "rm", "one", "--dry-run"], ["worktree", "rm", "one", "--dry-run"]),
        (["status", "--json"], ["status", "--json"]),
        (["graph", "--json"], ["graph", "--format", "json"]),
        (["resolve", "--json"], ["resolve", "--json"]),
        # a positional comes before the options of a lifecycle verb
        (["worktree", "create", "one", "--base", "main", "--json"],
         ["worktree", "create", "one", "--base", "main", "--json"]),
        (["worktree", "rm", "one", "--keep-branch", "--discard-changes", "b1",
          "--discard-changes", "b2", "--yes"],
         ["worktree", "rm", "one", "--keep-branch", "--discard-changes", "b1",
          "--discard-changes", "b2", "--yes"]),
        # the command after `--` reaches the child byte for byte, `--` included
        (["exec", "api:web", "--", "pytest", "-q", "--help"],
         ["exec", "api:web", "--", "pytest", "-q", "--help"]),
        (["run", "--root-folder", "/repo", "--profile", "test", "--name", "rg-one",
          "--label", "run-gate.job=job1", "--env-name", "SCHEMA_GATE_DSN",
          "--extend", "/tmp/limits.yml", "--detach", "--json", "tools/runner:worker",
          "--", "python", "-c", "print('lane')"],
         ["run", "--root-folder", "/repo", "--profile", "test", "--name", "rg-one",
          "--label", "run-gate.job=job1", "--env-name", "SCHEMA_GATE_DSN",
          "--extend", "/tmp/limits.yml", "--detach", "--json", "tools/runner:worker",
          "--", "python", "-c", "print('lane')"]),
        (["run", "--root-folder", "/repo", "--older-than", "30m", "reap", "--yes"],
         ["run", "--root-folder", "/repo", "--older-than", "30m", "--yes", "reap"]),
        (["run", "--dry-run", "--json", "tools/runner:worker", "--", "python", "-V"],
         ["run", "--json", "--dry-run", "tools/runner:worker", "--", "python", "-V"]),
        (["worktree", "exec", "one", "--target", "web", "--", "ls", "-l"],
         ["worktree", "exec", "one", "--target", "web", "--", "ls", "-l"]),
        (["ssh", "web", "--admin", "--", "uptime"], ["ssh", "web", "--admin", "--", "uptime"]),
        (["ssh", "web"], ["ssh", "web"]),
        (["bake", "a", "b", "--no-cache"], ["bake", "--no-cache", "a", "b"]),
        # a renamed verb reaches the unchanged implementation token
        (["show", "profiles"], ["profiles"]),
        (["show", "layouts", "--json"], ["layouts", "--json"]),
        (["governance", "ksm", "--force"], ["ksm", "build", "--force"]),
        (["governance", "iops-baseline", "--runtime", "5"], ["iops-baseline", "--runtime", "5"]),
        (["schema", "capabilities", "--json"], ["capabilities", "--json"]),
        (["migrate", "--check", "--json"], ["migration-check", "--json"]),
        (["secrets", "host", "h1", "--list"], ["host-secrets", "h1", "--list"]),
        (["secrets", "reset", "--dir", "x", "--name", "n", "--yes"],
         ["secrets", "reset", "--dir", "x", "--name", "n", "--yes"]),
        (["env", "generate"], ["env", "generate"]),
        (["host", "enroll", "box", "--ssh-host", "1.2.3.4", "--port", "2222"],
         ["host", "enroll", "box", "--port", "2222", "--ssh-host", "1.2.3.4"]),
    ],
)
def test_adapters_respell_the_validated_command(argv, expected):
    assert _tokens(*argv) == expected


def test_status_modes_route_to_the_report_the_gate_and_the_preflight():
    assert _tokens("status", "--profile", "core") == ["status", "--profile", "core"]
    assert _tokens("status", "--gate", "--phases", "1", "--host", "h", "--thin") == [
        "health", "--phases", "1", "--host", "h", "--thin",
    ]
    assert _tokens("status", "--preflight", "--strict") == ["health", "--preflight", "--strict"]


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["status", "--strict"], "--strict requires --preflight"),
        (["status", "--phases", "1"], "--phases requires --gate"),
        (["status", "--gate", "--json"], "cannot be used together"),
        (["clean", "--identity", "x", "--profile", "p"], "cannot be used together"),
        (["clean", "--expected-checkout", "/x"], "--expected-checkout requires --identity"),
        (["host", "enroll", "n", "--abort", "--replace"], "cannot be used together"),
        (["host", "enroll", "n", "--fingerprint", "SHA256:x"], "--fingerprint requires --ssh-host"),
        (["worktree", "create", "a", "--name", "n", "--prefix", "p"], "cannot be used together"),
        (["worktree", "lease", "a"], "--extend"),
        (["worktree", "branches", "--discard-protected-state", "b"], "requires"),
        (["migrate"], "needs --check"),
    ],
)
def test_declared_rules_refuse_before_the_implementation_runs(argv, needle):
    status, _out, err, recorded = harness.run(argv)
    assert status == 2 and recorded == []
    assert needle in err


def test_every_delegated_registry_shares_the_one_exception_policy():
    cli_ = cli_registry.build_cli()
    assert cli_.unexpected_exceptions == cli_registry.UNEXPECTED_EXCEPTIONS_POLICY == "report"
    for name, group in cli_.delegates.items():
        assert group.unexpected_exceptions == "report", name
    assert set(cli_.delegates) >= {"env", "show", "governance", "secrets", "host", "worktree",
                                   "schema", "skills"}


def test_registry_factory_owns_identity_and_policy():
    with pytest.raises(TypeError, match="owns 'identity'"):
        cli_registry.ciu_registry("ciu x", "d", identity=None)
    with pytest.raises(TypeError, match="owns 'unexpected_exceptions'"):
        cli_registry.ciu_registry("ciu x", "d", unexpected_exceptions="raise")


def test_run_legacy_maps_every_way_an_implementation_stops(monkeypatch, capsys):
    def stop_with(code):
        def dispatch(_tokens):
            raise SystemExit(code)

        return dispatch

    monkeypatch.setattr(cli, "_legacy_dispatch", stop_with(7))
    assert cli._run_legacy(["x"]) == 7
    monkeypatch.setattr(cli, "_legacy_dispatch", stop_with(None))
    assert cli._run_legacy(["x"]) == 0
    monkeypatch.setattr(cli, "_legacy_dispatch", stop_with("a message"))
    assert cli._run_legacy(["x"]) == 1
    assert "a message" in capsys.readouterr().err
    monkeypatch.setattr(cli, "_legacy_dispatch", lambda _tokens: None)
    assert cli._run_legacy(["x"]) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["-h", "tools/runner", "--", "true"],
        ["--dry", "--json", "tools/runner", "--", "true"],
    ],
)
def test_run_parser_refuses_help_and_abbreviated_options(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        cli._run_cli(argv)

    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_run_registry_refuses_progress_option():
    status, _out, err, _dispatch = harness.run(
        ["run", "--progress", "quiet", "tools/runner", "--", "true"],
    )

    assert status == 2
    assert "[ERROR] ciu run: unrecognized arguments: --progress" in err


def test_ksm_overrides_set_the_ambient_variable_for_this_run(monkeypatch):
    from ciu import governance

    seen: list[str | None] = []
    monkeypatch.setattr(
        cli, "_legacy_dispatch",
        lambda _tokens: seen.append(cli.os.environ.get(governance.KSM_ENV_VAR)),
    )
    monkeypatch.setenv(governance.KSM_ENV_VAR, "off")  # restored by monkeypatch afterwards
    for flag in ("--ksm", "--no-ksm"):
        monkeypatch.setattr(sys, "argv", ["ciu", "render", flag])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
    assert seen == [governance.BUILTIN_KSM, "off"]


def test_unexpected_exception_is_one_error_line_and_traceback_opts_in(monkeypatch):
    def boom(_tokens):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "_legacy_dispatch", boom)
    for flag, expect_stack in ((None, False), ("--traceback", True)):
        argv = ["ciu", "render", *([flag] if flag else [])]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(BaseException) as raised:
            cli.main()
        if expect_stack:
            assert isinstance(raised.value, RuntimeError)
        else:
            assert isinstance(raised.value, SystemExit) and raised.value.code == 1
