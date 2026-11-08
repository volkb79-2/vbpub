"""CLI output contracts for refusals, no-ops, cancellations and partial failures."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from augeaswrap import ApplyFailed, PlanRefused, Problem, Request, Result, StalePlan
from augeaswrap import cli as cli_module
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
