"""CLI output contracts for refusals, no-ops, cancellations and partial failures."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from augeaswrap import ApplyFailed, JsonKey, PlanRefused, Problem, Request, Result, StalePlan
from augeaswrap import cli as cli_module
from augeaswrap import transactions as transactions_module
from augeaswrap.check import Check, PathMode
from augeaswrap.cli import build_cli
from augeaswrap.engine import apply, journal, plan
from augeaswrap.file import File
from cli_extended.identity import CliIdentity

IDENTITY = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")


class TTY(io.StringIO):
    def isatty(self):
        return True


def fake_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    return root


def request_file(tmp_path: Path, text: str = "managed") -> Path:
    path = tmp_path / "request.toml"
    path.write_text(Request("cli test", (File("/etc/service.conf", "present", text, "0640"),)).to_toml(), encoding="utf-8")
    return path


def refusing_request_file(tmp_path: Path) -> Path:
    check = Check("present", path_mode=PathMode("/etc/missing", 0, 0, "0644", "must-match"))
    path = tmp_path / "refusing.toml"
    path.write_text(Request("cli refusal", (check,)).to_toml(), encoding="utf-8")
    return path


def run(*argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    status = build_cli(IDENTITY).run(
        argv=list(argv),
        stdout=out,
        stderr=err,
        stdin=io.StringIO(stdin) if isinstance(stdin, str) else stdin,
    )
    return status, out.getvalue(), err.getvalue()


def boom(exc):
    def raiser(*_args, **_kwargs):
        raise exc

    return raiser


def applied(tmp_path):
    root = fake_root(tmp_path)
    request = Request("cli test", (File("/etc/service.conf", "present", "managed", "0640"),))
    txn = apply(plan(request, root=root)).txn
    assert txn
    return root, txn


def test_plan_refusal_is_reported_as_text_and_as_json(tmp_path):
    root = fake_root(tmp_path)
    path = refusing_request_file(tmp_path)
    status, out, err = run("plan", str(path), "--root", str(root))
    assert status == 2 and "expected /etc/missing to exist" in err and out == ""
    status, out, err = run("plan", str(path), "--root", str(root), "--json")
    document = json.loads(out)
    assert status == 2 and document["status"] == "refused"
    assert document["problems"][0]["code"] == "check.path_mode.missing" and "plan" not in document


def test_apply_reports_unloadable_and_refused_requests(tmp_path):
    root = fake_root(tmp_path)
    status, _out, err = run("apply", str(tmp_path / "missing.toml"), "--root", str(root), "--yes")
    assert status == 2 and "cannot load Augeaswrap request" in err
    status, out, _err = run("plan", str(tmp_path / "missing.toml"), "--root", str(root), "--json")
    assert status == 2 and json.loads(out)["problems"][0]["code"] == "request.invalid"
    status, _out, err = run("apply", str(refusing_request_file(tmp_path)), "--root", str(root), "--yes")
    assert status == 2 and "expected /etc/missing to exist" in err


def test_apply_with_nothing_to_change_is_a_clean_noop(tmp_path):
    root = fake_root(tmp_path)
    path = request_file(tmp_path)
    assert run("apply", str(path), "--root", str(root), "--yes")[0] == 0
    status, out, _err = run("apply", str(path), "--root", str(root), "--yes")
    assert status == 0 and "no changes" in out
    status, out, _err = run("apply", str(path), "--root", str(root), "--yes", "--json")
    document = json.loads(out)
    assert status == 0 and document["status"] == "noop"
    assert document["result"] == {"txn": None, "changed": [], "actions": [], "pending": []}


def test_apply_noop_surfaces_a_plan_that_went_stale(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    path = request_file(tmp_path)
    assert run("apply", str(path), "--root", str(root), "--yes")[0] == 0
    monkeypatch.setattr(cli_module, "apply", boom(StalePlan("moved on")))
    status, _out, err = run("apply", str(path), "--root", str(root), "--yes")
    assert status == 2 and "moved on" in err
    monkeypatch.setattr(cli_module, "apply", boom(PlanRefused([Problem("x.y", "refused at apply")])))
    status, out, _err = run("apply", str(path), "--root", str(root), "--yes", "--json")
    document = json.loads(out)
    assert status == 2 and document["problems"][0]["message"] == "refused at apply" and "plan" in document


def test_apply_without_confirmation_in_json_mode_reports_a_refusal_document(tmp_path):
    root = fake_root(tmp_path)
    status, out, _err = run("apply", str(request_file(tmp_path)), "--root", str(root), "--json")
    document = json.loads(out)
    assert status == 2 and document["status"] == "refused" and "--yes" in document["error"]
    assert not (root / "etc/service.conf").exists()


def test_apply_dry_run_and_declined_prompt_are_distinct_json_statuses(tmp_path):
    root = fake_root(tmp_path)
    path = request_file(tmp_path)
    status, out, _err = run("apply", str(path), "--root", str(root), "--dry-run", "--json")
    assert status == 0 and json.loads(out)["status"] == "dry_run"
    status, out, _err = run("apply", str(path), "--root", str(root), "--json", stdin=TTY("n\n"))
    assert status == 0 and json.loads(out)["status"] == "cancelled"
    status, out, _err = run("apply", str(path), "--root", str(root), stdin=TTY("n\n"))
    assert status == 0 and "Applied" not in out
    assert not (root / "etc/service.conf").exists()


@pytest.mark.parametrize(
    ("error", "expect"),
    [
        (PlanRefused([Problem("x.y", "refused at apply")]), "refused at apply"),
        (StalePlan("plan is stale"), "plan is stale"),
    ],
)
def test_apply_reports_refusals_raised_during_the_transaction(tmp_path, monkeypatch, error, expect):
    root = fake_root(tmp_path)
    path = request_file(tmp_path)
    monkeypatch.setattr(cli_module, "apply", boom(error))
    status, _out, err = run("apply", str(path), "--root", str(root), "--yes")
    assert status == 2 and expect in err
    status, out, _err = run("apply", str(path), "--root", str(root), "--yes", "--json")
    assert status == 2 and json.loads(out)["status"] == "refused"


def test_apply_reports_a_partial_transaction_as_text_and_json(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    path = request_file(tmp_path)
    failure = ApplyFailed("20261108T000000Z-0001", ["commit file:/etc/service.conf"], "post-action reload: exit 1", 3)
    monkeypatch.setattr(cli_module, "apply", boom(failure))
    status, _out, err = run("apply", str(path), "--root", str(root), "--yes")
    assert status == 4 and "augeaswrap revert 20261108T000000Z-0001" in err
    status, out, _err = run("apply", str(path), "--root", str(root), "--yes", "--json")
    document = json.loads(out)
    assert status == 4 and document["status"] == "partial" and document["txn"] == "20261108T000000Z-0001"
    assert document["done"] == ["commit file:/etc/service.conf"] and "revert" in document["undo"]


def test_apply_text_reports_when_the_transaction_applied_nothing(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    monkeypatch.setattr(cli_module, "apply", lambda planned: Result(None))
    status, out, _err = run("apply", str(request_file(tmp_path)), "--root", str(root), "--yes")
    assert status == 0 and "No changes applied." in out


def test_revert_reports_unknown_transactions_and_nothing_to_revert(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    status, _out, err = run("revert", "20990101T000000Z-0001", "--root", str(root), "--yes")
    assert status == 2 and "cannot read transaction" in err

    def commit_fails(self, ctx, staged):
        raise RuntimeError("commit broke")

    monkeypatch.setattr(File, "commit", commit_fails)
    with pytest.raises(ApplyFailed) as failure:
        apply(plan(Request("cli test", (File("/etc/service.conf", "present", "managed", "0640"),)), root=root))
    monkeypatch.undo()
    txn = failure.value.txn
    status, out, _err = run("revert", txn, "--root", str(root), "--yes")
    assert status == 0 and "Nothing to revert." in out
    status, out, _err = run("revert", txn, "--root", str(root), "--yes", "--json")
    document = json.loads(out)
    assert status == 0 and document["status"] == "noop" and document["result"]["txn"] is None


def test_revert_confirmation_outcomes_in_json_mode(tmp_path):
    root, txn = applied(tmp_path)
    status, out, _err = run("revert", txn, "--root", str(root), "--json")
    document = json.loads(out)
    assert status == 2 and document["status"] == "refused" and "--yes" in document["error"]
    status, _out, err = run("revert", txn, "--root", str(root))
    assert status == 2 and "--yes" in err
    status, out, _err = run("revert", txn, "--root", str(root), "--dry-run", "--json")
    assert status == 0 and json.loads(out)["status"] == "dry_run"
    status, out, _err = run("revert", txn, "--root", str(root), "--json", stdin=TTY("n\n"))
    assert status == 0 and json.loads(out)["status"] == "cancelled"
    assert (root / "etc/service.conf").exists()


@pytest.mark.parametrize(
    ("error", "expect", "status"),
    [
        (PlanRefused([Problem("x.y", "refused at revert")]), "refused at revert", 2),
        (StalePlan("revert is stale"), "revert is stale", 2),
        (ApplyFailed("20261108T000000Z-0002", [], "commit broke", 1), "augeaswrap revert 20261108T000000Z-0002", 4),
    ],
)
def test_revert_reports_failures_raised_during_the_restore(tmp_path, monkeypatch, error, expect, status):
    root, txn = applied(tmp_path)
    monkeypatch.setattr(cli_module, "revert", boom(error))
    code, _out, err = run("revert", txn, "--root", str(root), "--yes")
    assert code == status and expect in err
    code, out, _err = run("revert", txn, "--root", str(root), "--yes", "--json")
    assert code == status and json.loads(out)["status"] in {"refused", "partial"}


def test_journal_show_and_list_formats_and_argument_errors(tmp_path):
    root = fake_root(tmp_path)
    status, out, _err = run("journal", "list", "--root", str(root))
    assert status == 0 and "No Augeaswrap transactions." in out
    root, txn = applied(tmp_path / "applied")
    status, out, _err = run("journal", "list", "--root", str(root))
    assert status == 0 and txn in out and "cli test" in out
    status, out, _err = run("journal", "show", txn, "--root", str(root))
    assert status == 0 and f"{txn} done (apply)" in out and "commit file:/etc/service.conf" in out
    assert journal(txn, root=root)["id"] == txn
    status, _out, err = run("journal", "show", "--root", str(root))
    assert status == 2 and "requires a transaction id" in err
    status, _out, err = run("journal", "list", txn, "--root", str(root))
    assert status == 2 and "does not accept a transaction id" in err


def _pending_applied(root, monkeypatch):
    monkeypatch.setattr(transactions_module.time, "monotonic_ns", lambda: 1_000_000_000)
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["debug"],
        "present",
        True,
        on_change={"unit": "docker.service", "action": "pending"},
    )
    result = apply(plan(Request("pending CLI test", (resource,)), root=root))
    assert result.txn
    return result.txn


def _systemctl_output(active_enter: int, active_state: str):
    return f"ActiveEnterTimestampMonotonic={active_enter}\nActiveState={active_state}\nLoadState=loaded\n"


def canned_systemctl_result(*, returncode=0, stdout="", stderr=""):
    def runner(argv, *, timeout):
        assert tuple(argv)[0] == "systemctl"
        assert timeout == 30.0
        return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)

    return runner


def test_journal_pending_omits_unit_entered_active_after_transaction(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    calls = []

    def runner(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(list(argv), 0, _systemctl_output(1_000_001, "active"), "")

    monkeypatch.setattr(transactions_module.shadow, "run_command", runner)
    status, out, err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0 and err == "" and json.loads(out) == []
    assert calls == [
        (("systemctl", "show", "-p", "ActiveEnterTimestampMonotonic,ActiveState,LoadState", "--", "docker.service"), 30.0)
    ]


@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/option-spelling/option:route:entrypoint:augeaswrap/journal/--pending/--pending")
def test_journal_pending_lists_unit_that_never_entered_active(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(0, "inactive")),
    )

    status, out, err = run("journal", "--pending", "--root", str(root), "--json")
    values = json.loads(out)

    assert status == 0 and err == ""
    assert len(values) == 1
    assert values[0]["state"] == "pending" and values[0]["unit_state"] == "inactive"
    assert values[0]["created_monotonic_usec"] == 1_000_000
    assert values[0]["active_enter_timestamp_monotonic"] == 0


def test_journal_pending_keeps_active_timestamp_at_or_before_transaction(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(1_000_000, "active")),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    assert len(json.loads(out)) == 1
    assert json.loads(out)[0]["unit_state"] == "active"


def test_journal_pending_conservatively_lists_old_journal_without_monotonic_time(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    value.pop("created_monotonic_usec")
    journal_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(9_000_000, "active")),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    result = json.loads(out)[0]
    assert result["created_monotonic_usec"] is None
    assert result["unit_state"] == "active"


def test_journal_pending_lists_missing_unit_with_its_state(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    output = "LoadState=not-found\nActiveState=inactive\nActiveEnterTimestampMonotonic=0\n"
    calls = []

    def runner(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(list(argv), 0, output, "")

    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        runner,
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    assert json.loads(out)[0]["unit_state"] == "missing"
    assert calls == [
        (("systemctl", "show", "-p", "ActiveEnterTimestampMonotonic,ActiveState,LoadState", "--", "docker.service"), 30.0)
    ]


def test_journal_pending_lists_nonzero_not_found_diagnostic_as_missing(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(returncode=4, stderr="Unit docker.service could not be found."),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    value = json.loads(out)[0]
    assert value["unit_state"] == "missing"
    assert value["active_enter_timestamp_monotonic"] is None


@pytest.mark.parametrize(
    "output",
    [
        "ActiveState=inactive\nLoadState=loaded\n",
        "ActiveEnterTimestampMonotonic=0\nLoadState=loaded\n",
        "ActiveEnterTimestampMonotonic=0\nActiveState=inactive\n",
    ],
)
def test_journal_pending_refuses_missing_systemd_properties(tmp_path, monkeypatch, output):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "omitted requested properties" in document["problems"][0]["message"]


@pytest.mark.parametrize(
    ("active_state", "load_state"),
    [("", "loaded"), ("inactive", "")],
)
def test_journal_pending_refuses_empty_systemd_properties(tmp_path, monkeypatch, active_state, load_state):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    output = (
        f"ActiveEnterTimestampMonotonic=0\nActiveState={active_state}\nLoadState={load_state}\n"
    )
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "empty requested property" in document["problems"][0]["message"]


@pytest.mark.parametrize(
    "output",
    [
        "ActiveEnterTimestampMonotonic=0\nActiveState=active\nLoadState=not-found\n",
        "ActiveEnterTimestampMonotonic=0\nActiveState=not-found\nLoadState=loaded\n",
    ],
)
def test_journal_pending_refuses_inconsistent_systemd_properties(tmp_path, monkeypatch, output):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "inconsistent" in document["problems"][0]["message"]


def test_journal_pending_ignores_completed_actions(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    completed = dict(value["actions"][0])
    completed.update(status="done", pending=False)
    value["actions"].append(completed)
    journal_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(0, "inactive")),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    assert len(json.loads(out)) == 1


def test_journal_pending_skips_other_post_action_shapes(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    other = dict(value["actions"][0])
    other.update(name="other pending action", argv=["systemctl", "restart", "--", "docker.service", "extra"])
    value["actions"].append(other)
    journal_path.write_text(json.dumps(value), encoding="utf-8")
    calls = []

    def runner(argv, *, timeout):
        assert timeout == 30.0
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(list(argv), 0, _systemctl_output(0, "inactive"), "")

    monkeypatch.setattr(transactions_module.shadow, "run_command", runner)

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")

    assert status == 0
    assert len(json.loads(out)) == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("pending", False),
        ("argv", "not-an-argv-list"),
        ("argv", ["systemctl", 12]),
    ],
)
def test_journal_pending_refuses_malformed_pending_action_records(tmp_path, monkeypatch, field, bad_value):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    value["actions"][0][field] = bad_value
    journal_path.write_text(json.dumps(value), encoding="utf-8")

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.invalid"
    assert "invalid pending action" in document["problems"][0]["message"]


def test_journal_pending_runner_failure_is_an_explicit_refusal(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(returncode=1, stderr="systemctl unavailable"),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["status"] == "refused"
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "systemctl unavailable" in document["problems"][0]["message"]
    assert document["problems"] != []


def test_journal_pending_operational_systemd_failure_is_not_a_missing_unit(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    diagnostic = (
        "System has not been booted with systemd as init system (PID 1). Can't operate.\n"
        "Failed to connect to system scope bus via local transport: Host is down\n"
    )
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(returncode=1, stderr=diagnostic),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["status"] == "refused"
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "System has not been booted with systemd" in document["problems"][0]["message"]
    assert document["problems"][0]["message"] != "unit_state=missing"


def test_journal_pending_runner_exception_is_an_explicit_refusal(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)

    def runner(argv, *, timeout):
        assert timeout == 30.0
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(transactions_module.shadow, "run_command", runner)
    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "systemctl" in document["problems"][0]["message"]


@pytest.mark.parametrize("timestamp", [-1, True, "not-a-number"])
def test_journal_pending_refuses_invalid_transaction_timestamp(tmp_path, monkeypatch, timestamp):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    value["created_monotonic_usec"] = timestamp
    journal_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(0, "inactive")),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.invalid"
    assert "monotonic timestamp" in document["problems"][0]["message"]


@pytest.mark.parametrize("timestamp", ["not-a-number", "-1"])
def test_journal_pending_refuses_invalid_systemd_timestamp(tmp_path, monkeypatch, timestamp):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    output = f"ActiveEnterTimestampMonotonic={timestamp}\nActiveState=inactive\nLoadState=loaded\n"
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "timestamp" in document["problems"][0]["message"]


def test_journal_pending_refuses_duplicate_systemd_properties(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    output = "ActiveEnterTimestampMonotonic=0\nActiveEnterTimestampMonotonic=1\nActiveState=inactive\nLoadState=loaded\n"
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)

    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.query.failed"
    assert "duplicate ActiveEnterTimestampMonotonic" in document["problems"][0]["message"]


def test_journal_pending_rejects_combined_list_or_show_arguments(tmp_path):
    root, txn = applied(tmp_path)
    status, _out, err = run("journal", "show", txn, "--pending", "--root", str(root))
    assert status == 2 and "does not accept list, show" in err


def test_journal_pending_rejects_an_action_without_a_transaction(tmp_path):
    root = fake_root(tmp_path)

    status, _out, err = run("journal", "list", "--pending", "--root", str(root))

    assert status == 2
    assert "journal --pending does not accept list, show, or a transaction id" in err


def test_endpoint_help_does_not_advertise_unavailable_progress_option():
    status, out, _err = run("endpoints", "--help")

    assert status == 0
    assert "--progress" not in out


def test_journal_pending_text_output_and_missing_journal_warning(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    status, out, err = run("journal", "--pending", "--root", str(root))
    assert status == 0 and err == "" and out.strip() == "No pending unit restarts."

    txn_dir = root / "var/lib/augeaswrap/txn/20261109T000000Z-0001"
    txn_dir.mkdir(parents=True)
    status, out, err = run("journal", "--pending", "--root", str(root))
    assert status == 0 and out.strip() == "No pending unit restarts."
    assert "has no journal.json" in err

    _pending_applied(root, monkeypatch)
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=_systemctl_output(0, "inactive")),
    )
    status, out, err = run("journal", "--pending", "--root", str(root))
    assert status == 0 and "docker.service state=inactive" in out
    assert "has no journal.json" in err


def test_journal_list_and_show_report_invalid_journals(tmp_path):
    root = fake_root(tmp_path)
    txn_id = "20261109T000000Z-0001"
    txn_dir = root / "var/lib/augeaswrap/txn" / txn_id
    txn_dir.mkdir(parents=True)
    (txn_dir / "journal.json").write_text("not JSON", encoding="utf-8")

    status, out, _err = run("journal", "list", "--root", str(root), "--json")
    assert status == 2 and json.loads(out)["problems"][0]["code"] == "txn.journal.invalid"
    status, out, _err = run("journal", "show", "bad-id", "--root", str(root), "--json")
    assert status == 2 and json.loads(out)["problems"][0]["code"] == "txn.id.invalid"


@pytest.mark.parametrize(
    ("actions", "message"),
    [({}, "has invalid actions"), ([None], "has an invalid action record")],
)
def test_journal_pending_refuses_malformed_journal_actions(tmp_path, monkeypatch, actions, message):
    root = fake_root(tmp_path)
    txn = _pending_applied(root, monkeypatch)
    journal_path = root / "var/lib/augeaswrap/txn" / txn / "journal.json"
    value = json.loads(journal_path.read_text(encoding="utf-8"))
    value["actions"] = actions
    journal_path.write_text(json.dumps(value), encoding="utf-8")

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    document = json.loads(out)
    assert status == 2
    assert document["problems"][0]["code"] == "journal.pending.invalid"
    assert message in document["problems"][0]["message"]


def test_journal_pending_ignores_unrequested_systemd_properties(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    _pending_applied(root, monkeypatch)
    output = "not-a-property\nDescription=ignored\n" + _systemctl_output(0, "inactive")
    monkeypatch.setattr(
        transactions_module.shadow,
        "run_command",
        canned_systemctl_result(stdout=output),
    )

    status, out, _err = run("journal", "--pending", "--root", str(root), "--json")
    assert status == 0 and len(json.loads(out)) == 1
