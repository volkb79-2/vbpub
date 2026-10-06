"""LT-NC2: ``scp-api snapshots SERVER delete SNAPSHOT_NAME``.

Every test replays an invocation through the real ``main()`` against the routed
fake client (``case_harness.RoutedClient``): no test can reach the provider API,
and an unplanned GET fails the test.  The wire shape (DELETE
/api/v1/servers/{serverId}/snapshots/{name}, 202 TaskInfo) is the one in
``netcup-scp-openapi.json``.
"""
from __future__ import annotations

import io
import json

import pytest

from case_harness import SCP_ROUTES, run_scp_api

SNAPSHOTS = "/api/v1/servers/42/snapshots"
TWO = [
    {"uuid": "snap-1", "name": "before", "state": "READY"},
    {"uuid": "snap-2", "name": "other", "state": "READY"},
]


def _run(explore_mod, argv, tmp_path, monkeypatch, capsys, routes=None):
    merged = dict(SCP_ROUTES)
    merged[SNAPSHOTS] = TWO
    merged.update(routes or {})
    return run_scp_api(
        explore_mod, list(argv), tmp_path=tmp_path, monkeypatch=monkeypatch,
        capsys=capsys, routes=merged,
    )


def _deletes(run):
    return [call for call in run.calls if call[0] == "delete"]


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def test_delete_with_yes_sends_exactly_one_delete_for_the_named_snapshot(
    explore_mod, tmp_path, monkeypatch, capsys
):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert run.calls == [("get", SNAPSHOTS, None), ("delete", f"{SNAPSHOTS}/before", None)]
    assert "deleted" in run.out


def test_delete_reports_the_task_the_api_returns(explore_mod, tmp_path, monkeypatch, capsys):
    import case_harness

    # RoutedClient answers {} for a DELETE; the spec's 202 answer is a TaskInfo.
    def with_task(self, endpoint, params=None):
        self._answer("delete", endpoint, params)
        return {"uuid": "task-9", "name": "deleteSnapshot", "state": "PENDING"}

    monkeypatch.setattr(case_harness.RoutedClient, "delete", with_task)
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert "task-9" in run.out and "monitor-task.py watch task-9" in run.out


def test_delete_name_is_url_quoted_in_the_path(explore_mod, tmp_path, monkeypatch, capsys):
    routes = {SNAPSHOTS: [{"uuid": "s", "name": "a b/c", "state": "READY"}]}
    run = _run(explore_mod, ["snapshots", "42", "delete", "a b/c", "--yes"], tmp_path, monkeypatch, capsys, routes)
    assert run.status == 0
    assert _deletes(run) == [("delete", f"{SNAPSHOTS}/a%20b%2Fc", None)]


def test_declined_prompt_deletes_nothing(explore_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Terminal("n\n"))
    run = _run(explore_mod, ["snapshots", "42", "delete", "before"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert "aborted" in run.out
    assert _deletes(run) == []


def test_accepted_prompt_deletes(explore_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Terminal("y\n"))
    run = _run(explore_mod, ["snapshots", "42", "delete", "before"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert len(_deletes(run)) == 1


def test_non_interactive_without_yes_refuses_with_exit_2(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "confirmation is required, but stdin is not interactive" in run.err
    assert _deletes(run) == []


def test_dry_run_resolves_the_snapshot_and_sends_no_delete(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--dry-run"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert run.calls == [("get", SNAPSHOTS, None)]
    assert "would delete" in run.out and "before" in run.out


def test_dry_run_with_yes_still_sends_no_delete(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--dry-run", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert _deletes(run) == []


def test_dry_run_on_an_unknown_name_fails_like_a_real_delete(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "nope", "--dry-run"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert _deletes(run) == []


def test_dry_run_json_shape(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--dry-run", "--json"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert json.loads(run.out) == {"dryRun": True, "serverId": 42, "wouldDelete": TWO[0]}


def test_dry_run_on_create_creates_nothing(explore_mod, tmp_path, monkeypatch, capsys):
    """The verb now declares --dry-run, so create must honour it too."""
    run = _run(explore_mod, ["snapshots", "42", "create", "--dry-run", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert [c for c in run.calls if c[0] in ("post", "delete")] == []


def test_unknown_name_is_refused_before_any_delete(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "nope", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "no snapshot named 'nope'" in run.err
    assert "before" in run.err and "other" in run.err
    assert run.calls == [("get", SNAPSHOTS, None)]


@pytest.mark.parametrize(
    "flags",
    [[], ["--yes"], ["--dry-run"], ["--yes", "--dry-run"]],
    ids=["no-flag", "yes", "dry-run", "yes-dry-run"],
)
@pytest.mark.parametrize(
    "env_text",
    ["NETCUP_SCP_API_PROTECTED_SERVERS=v42\n", "NETCUP_SCP_API_PROTECTED_SERVER_IDS=42\n"],
    ids=["by-name", "by-id"],
)
def test_protected_server_is_refused_end_to_end(explore_mod, tmp_path, monkeypatch, capsys, flags, env_text):
    """Real main() and real .env loader (a test file in cwd): the denylist applies
    to every delete form, including --dry-run, before any snapshot or DELETE request."""
    merged = dict(SCP_ROUTES)
    merged[SNAPSHOTS] = TWO
    run = run_scp_api(
        explore_mod, ["snapshots", "42", "delete", "before", *flags], tmp_path=tmp_path,
        monkeypatch=monkeypatch, capsys=capsys, routes=merged, env_text=env_text,
    )
    assert run.status not in (0, None)
    assert "protected by the local denylist" in run.err
    # The guard's own server read is unavoidable; nothing else may be requested.
    assert run.calls == [("get", "/api/v1/servers/42", None)]


@pytest.mark.parametrize("near_miss", ["Before", "bef", "before "])
def test_lookup_is_exact_not_prefix_or_case_folded(explore_mod, tmp_path, monkeypatch, capsys, near_miss):
    run = _run(explore_mod, ["snapshots", "42", "delete", near_miss, "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert _deletes(run) == []
    assert run.calls == [("get", SNAPSHOTS, None)]


def test_duplicate_names_are_refused(explore_mod, tmp_path, monkeypatch, capsys):
    routes = {SNAPSHOTS: [TWO[0], {"uuid": "snap-3", "name": "before", "state": "READY"}]}
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--yes"], tmp_path, monkeypatch, capsys, routes)
    assert run.status == 2
    assert "2 snapshots named 'before'" in run.err
    assert run.calls == [("get", SNAPSHOTS, None)]


def test_json_output_shape_without_a_task(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "delete", "before", "--yes", "--json"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert json.loads(run.out) == {"serverId": 42, "snapshot": "before", "deleted": True}


def test_delete_requires_a_name_and_a_server_before_any_request(explore_mod, tmp_path, monkeypatch, capsys):
    no_name = _run(explore_mod, ["snapshots", "42", "delete", "--yes"], tmp_path, monkeypatch, capsys)
    assert no_name.status == 2 and "requires a SNAPSHOT_NAME" in no_name.err and no_name.calls == []


def test_a_name_with_another_action_is_refused(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "create", "before", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2 and "only valid with the delete action" in run.err and run.calls == []


@pytest.mark.parametrize("verb_args", [["snapshots", "42"], ["snapshots"]])
def test_existing_list_reads_are_unchanged(explore_mod, tmp_path, monkeypatch, capsys, verb_args):
    run = _run(explore_mod, verb_args, tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert _deletes(run) == []
    assert "before" in run.out
