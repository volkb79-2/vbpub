"""CIU-141: the `ciu run` CLI verb parses, validates and dispatches correctly.

`oneshot.run_cli` / `oneshot.reap_cli` have their own behavioural tests; these
tests pin the front end in ``cli._run_cli``: argument grammar, the refusals that
must happen before any Docker work, the exact keyword hand-off, and the
error-to-exit-code mapping. No Docker, no host state.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, oneshot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_root(monkeypatch, tmp_path):
    """Stub root resolution/seeding (they have their own tests); record hand-offs."""
    seeded: list[Path | None] = []
    monkeypatch.setattr(cli, "_seed_selected_root_env", seeded.append)
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    return seeded


def _record(monkeypatch, name, result=0):
    seen: list[tuple[tuple, dict]] = []

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(oneshot, name, fake)
    return seen


def test_missing_selector_is_a_usage_error(capsys):
    assert cli._run_cli([]) == 2
    assert "requires a stack selector or `reap`" in capsys.readouterr().err


def test_run_without_separator_or_command_is_refused_before_any_launch(monkeypatch, capsys):
    seen = _record(monkeypatch, "run_cli")
    assert cli._run_cli(["tools/api"]) == 2
    assert cli._run_cli(["tools/api", "--"]) == 2
    assert "requires `--` and a command" in capsys.readouterr().err
    assert seen == []


@pytest.mark.parametrize(("flags", "message"), [
    (["--dry-run"], "`ciu run --dry-run` requires --json"),
    (["--detach"], "`ciu run --detach` requires --json"),
    (["--json"], "foreground `ciu run` uses --events-fd or --events-file, not --json"),
])
def test_launch_flag_combinations_are_refused_before_any_launch(
    monkeypatch, capsys, flags, message,
):
    seen = _record(monkeypatch, "run_cli")
    assert cli._run_cli(["tools/api", *flags, "--", "true"]) == 2
    assert message in capsys.readouterr().err
    assert seen == []


def test_run_hands_every_option_to_the_launcher_and_returns_its_status(
    monkeypatch, tmp_path, _isolated_root,
):
    seen = _record(monkeypatch, "run_cli", result=7)
    rc = cli._run_cli([
        "--root-folder", str(tmp_path / "root"),
        "--profile", "apps", "--profile", "test",
        "--name", "job", "--label", "a=b", "--label", "c=d",
        "--env-name", "TOKEN", "--extend", "extra.yml",
        "--detach", "--json", "--events-fd", "5", "--events-file", "ev.log",
        "--owner-pid", "42", "--skip-hooks", "tools/api:api",
        "--", "python", "--version", "--dry-run",
    ])
    assert rc == 7
    # The wrapped command's own flags after `--` are never parsed by ciu.
    assert seen == [((tmp_path, "tools/api:api", ["python", "--version", "--dry-run"]), {
        "profiles": ["apps", "test"], "name": "job", "labels": ["a=b", "c=d"],
        "env_names": ["TOKEN"], "extend": "extra.yml", "detach": True,
        "json_output": True, "dry_run": False, "events_fd": 5,
        "events_file": "ev.log", "owner_pid": 42, "skip_hooks": True,
    })]
    assert _isolated_root == [tmp_path / "root"]


def test_run_defaults_labels_and_env_names_to_empty_lists(monkeypatch, tmp_path):
    seen = _record(monkeypatch, "run_cli")
    assert cli._run_cli(["tools/api", "--", "true"]) == 0
    kwargs = seen[0][1]
    assert kwargs["labels"] == [] and kwargs["env_names"] == []
    assert kwargs["profiles"] is None and kwargs["detach"] is False


@pytest.mark.parametrize(("error", "expected"), [
    (RuntimeError("compose broke"), 1), (ValueError("bad"), 2), (OSError("gone"), 2),
])
def test_run_maps_expected_errors_to_exit_codes(monkeypatch, capsys, error, expected):
    _record(monkeypatch, "run_cli", result=error)
    assert cli._run_cli(["tools/api", "--", "true"]) == expected
    assert f"[ERROR] {error}" in capsys.readouterr().err


def test_reap_hands_age_and_confirmation_to_the_reaper(monkeypatch, tmp_path):
    seen = _record(monkeypatch, "reap_cli", result=3)
    assert cli._run_cli(["reap", "--older-than", "2h", "--yes"]) == 3
    assert seen == [((tmp_path,), {"older_than": "2h", "yes": True})]
    seen.clear()
    assert cli._run_cli(["reap"]) == 3
    assert seen == [((tmp_path,), {"older_than": None, "yes": False})]


def test_reap_refuses_a_command_after_the_separator(monkeypatch, capsys):
    seen = _record(monkeypatch, "reap_cli")
    assert cli._run_cli(["reap", "--", "true"]) == 2
    assert cli._run_cli(["reap", "--"]) == 2
    assert "does not accept a command after `--`" in capsys.readouterr().err
    assert seen == []


@pytest.mark.parametrize("launch_option", [
    ["--detach"], ["--json"], ["--dry-run"], ["--events-fd", "3"],
    ["--events-file", "ev.log"], ["--name", "job"], ["--label", "a=b"],
    ["--env-name", "TOKEN"], ["--extend", "x.yml"], ["--profile", "apps"],
    ["--skip-hooks"], ["--owner-pid", "9"],
])
def test_reap_refuses_every_launch_option(monkeypatch, capsys, launch_option):
    seen = _record(monkeypatch, "reap_cli")
    assert cli._run_cli(["reap", *launch_option]) == 2
    assert "launch options do not apply to `ciu run reap`" in capsys.readouterr().err
    assert seen == []


@pytest.mark.parametrize(("error", "expected"), [
    (RuntimeError("docker"), 1), (ValueError("bad age"), 2), (OSError("read"), 2),
])
def test_reap_maps_expected_errors_to_exit_codes(monkeypatch, capsys, error, expected):
    _record(monkeypatch, "reap_cli", result=error)
    assert cli._run_cli(["reap"]) == expected
    assert f"[ERROR] {error}" in capsys.readouterr().err


def test_main_dispatches_the_run_verb(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_run_cli", lambda argv: called.append(argv) or 9)
    monkeypatch.setattr(sys, "argv", ["ciu", "run", "tools/api", "--", "true"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 9
    assert called == [["tools/api", "--", "true"]]
