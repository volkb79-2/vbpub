"""The `estate` handler over every input: temp trees only, a fake runtime, never the live estate.

The fixture assemblies are built by hand (`_view`) so a test names exactly the estate it
asserts on: which branches exist, which run records belong to which package, which gate slots
are held and which peer prose names which package id.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest
from cli_extended import CliFailure

from agctl import config as ag_config
from agctl import estate as agg
from agctl import gitstate
from agctl import runs as ag_runs
from agctl import sources as ag_sources


class FakeOutput:
    def __init__(self) -> None:
        self.values: list[object] = []

    def primary(self, value: object) -> None:
        self.values.append(value)

    @property
    def last(self) -> object:
        return self.values[-1]


class FakeRuntime:
    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode
        self.output = FakeOutput()


class Args:
    def __init__(self, header: bool = False) -> None:
        self.header = header


def _config(tmp_path, **overrides) -> ag_config.Config:
    values = dict(
        home=tmp_path / "home",
        runs_dirs=(tmp_path / "runs",),
        state_dirs=(),
        repo_state_dir=None,
        peers_dir=tmp_path / "peers",
        gate_lock_dir=tmp_path / "locks",
        remote_gate_out=tmp_path / "gates",
        toml_path=tmp_path / "agctl.toml",
        toml_state="absent",
        toml_legacy_dirs=(),
    )
    values.update(overrides)
    return ag_config.Config(**values)


def _worktree(tmp_path, branch: str | None, head: str | None = "2" * 40) -> gitstate.Worktree:
    return gitstate.Worktree(str(tmp_path / "repo" / ".worktrees" / (branch or "detached")), head, branch, branch is None, False, None)


def _git(tmp_path, branches: tuple[str, ...], merged: frozenset[str] = frozenset({"main"})) -> gitstate.GitView:
    worktrees = (gitstate.Worktree(str(tmp_path / "repo"), "1" * 40, "main", False, False, None), *(_worktree(tmp_path, branch) for branch in branches))
    return gitstate.GitView(str(tmp_path / "repo"), worktrees, merged, "present")


def _record(run_id: str, *, source: str, files: tuple[str, ...] = ("done",), outcome: str = "SUCCESS",
            tokens: int | None = None, last_state: str = "", gauge: str = "", worktree: str = "") -> ag_runs.RunRecord:
    return ag_runs.RunRecord(
        id=run_id, source=source, files=files, exit_code=0, outcome=outcome, review="",
        route="luna", thread="", worktree=worktree, tokens=tokens, live_mtime=None,
        last_state=last_state, last_run="", gauge=gauge, boundary="", mark="",
    )


def _run_set(tmp_path, records: tuple[ag_runs.RunRecord, ...], *, unreadable: tuple[tuple[str, str], ...] = ()) -> ag_runs.RunSet:
    return ag_runs.RunSet(
        records=records,
        dirs=(str(tmp_path / "runs"),),
        readable=(str(tmp_path / "runs"),),
        unreadable=unreadable,
    )


def _slot(host: str = "v1001", name: str = "v1001.slot1", state: str = "free", pid: int | None = None) -> ag_sources.Slot:
    return ag_sources.Slot(host=host, name=name, kind="slot", state=state, holder_pid=pid, detail="", path=f"/locks/{name}")


def _result(tmp_path, name: str = "rgw-tests-1", project: str = "cmru", lane: str = "tests", verdict: str = "PASS",
            mtime: float = 1000.0, sha: str = "2" * 40) -> ag_sources.GateResult:
    return ag_sources.GateResult(
        name=name, host="v1001", project=project, lane=lane, sha=sha, verdict=verdict,
        verdict_source="lane.log: run-gate verdict line", started="2026-11-25T21:22:23Z", mtime=mtime, detail="",
    )


def _peer(tmp_path, peer: str = "platform", package: str = "plat-22c", state_state: str = "present",
          handover: float | None = 1000.0) -> ag_sources.PeerState:
    lines = ()
    if state_state == "present":
        lines = (ag_sources.StateLine(peer=peer, path=f"/state/{peer}/STATE.md", line=26, package=package,
                                      topic=package.split("-")[0], designator=package.split("-")[-1],
                                      token=f"P-{package}", text="prose line about the package"),)
    return ag_sources.PeerState(
        peer=peer, state_path=f"/state/{peer}/STATE.md", state_mtime=1000.0, state_state=state_state,
        state_detail="", handover_path=f"/state/{peer}/HANDOFF.md", handover_mtime=handover, lines=lines,
    )


def _locks(*entries: ag_sources.LockEntry) -> ag_sources.LockTable:
    return ag_sources.LockTable(tuple(entries), True, "")


def _live_record(tmp_path, run_id: str, *, tokens: int | None = None) -> tuple[ag_runs.RunRecord, pathlib.Path]:
    """A run the kernel lock proves alive: an existing `.lock` with a matching `/proc/locks` row.

    "Active" is not something a record asserts about itself (DESIGN 4a derives DIED from a FREE
    lock), so every fixture that means "this run is live" carries a held one.
    """

    path = tmp_path / "runs" / f"{run_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    record = ag_runs.RunRecord(
        id=run_id, source=str(tmp_path / "runs"), files=("live", "lock"), exit_code=None, outcome="",
        review="", route="luna", thread="", worktree="", tokens=tokens, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="", paths={"lock": path},
    )
    return record, path


def _held(paths: tuple[pathlib.Path, ...]) -> ag_sources.LockTable:
    entries = []
    for path in paths:
        status = path.stat()
        entries.append(
            ag_sources.LockEntry(
                kind="FLOCK",
                pid=99,
                device=(os.major(status.st_dev), os.minor(status.st_dev)),
                inode=status.st_ino,
            )
        )
    return _locks(*entries)


def _view(git: gitstate.GitView, run_set: ag_runs.RunSet, *, slots=(), results=(), peers=(), handover: float | None = 1000.0,
          now: float = 4000.0, locks: ag_sources.LockTable | None = None, merge_queue: str = "",
          slots_detail: str = "", results_detail: str = "", peers_detail: tuple[str, ...] = ()) -> agg.View:
    return agg.View(
        git=git, run_set=run_set, slots=tuple(slots), results=tuple(results), peers=tuple(peers),
        handover_path="/repo/.controller-state/HANDOFF.md", handover_mtime=handover,
        handover_detail="" if handover else "missing", now=now,
        locks=locks if locks is not None else ag_sources.LockTable((), True, ""),
        slots_detail=slots_detail, results_detail=results_detail, peers_detail=peers_detail,
        merge_queue=merge_queue, merge_queue_detail="" if merge_queue else "no MERGE QUEUE line in the handover",
    )


# --- ages and context buckets ------------------------------------------------------------------


@pytest.mark.parametrize(("age", "expected"), [(0, "<1h"), (3599, "<1h"), (3600, "1-6h"), (6 * 3600 - 1, "1-6h"), (6 * 3600, "6-24h"), (24 * 3600, ">24h")])
def test_age_buckets_are_fixed_edges(age, expected):
    assert agg.age_bucket(age) == expected


def test_an_unknown_age_is_not_a_bucket():
    assert agg.age_bucket(None) == agg.AGE_UNKNOWN


@pytest.mark.parametrize(("tokens", "expected"), [(None, "ctx-unknown"), (0, "<50k"), (49_999, "<50k"), (50_000, "50-150k"), (149_999, "50-150k"), (150_000, "150-250k"), (250_000, ">250k")])
def test_the_context_figure_is_bucketed_so_it_does_not_move_on_every_write(tokens, expected):
    assert agg.ctx_bucket(tokens) == expected


# --- rows ----------------------------------------------------------------------------------------


def test_rows_join_branches_runs_and_gate_results_on_the_head(tmp_path):
    branch = "cmru-20261124_053644-backlog-ki188"
    git = _git(tmp_path, (branch,))
    records = (_record("cmru-p07-pi", source=str(tmp_path / "runs"), worktree=_worktree(tmp_path, branch).path),)
    run_set = _run_set(tmp_path, records)
    view = _view(git, run_set, results=(_result(tmp_path),), slots=(_slot(state="held", pid=42, name="v1001.slot1")))

    (row,) = agg.rows_from_inputs(view)

    assert row.package == "cmru-backlog-ki188"
    assert row.branches == (branch,)
    assert "1 run(s)" in row.fields["runs"].value and "1 ended" in row.fields["runs"].value
    # The gate result names this row's head, so it is this row's verdict.
    assert row.fields["gates"].value.startswith("tests PASS v1001")
    assert "1 slot(s) held estate-wide" in row.fields["gates"].detail
    assert row.fields["merged"].value == f"no: {branch}"


def test_the_row_key_is_the_whole_slug_after_the_stamp(tmp_path):
    git = _git(tmp_path, ("core-ciu-r2fix", "core-cmru-ki208", "core-thrashguard-design"))

    rows = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ())))

    assert [row.package for row in rows] == ["core-ciu-r2fix", "core-cmru-ki208", "core-thrashguard-design"]


def test_a_package_with_runs_but_no_worktree_still_gets_a_row(tmp_path):
    # The README's promise: a run is reported whether or not its branch still exists.
    records = (_record("assay-p4-stall", source=str(tmp_path / "runs"), files=("live",), outcome="", tokens=9000),)

    (row,) = agg.rows_from_inputs(_view(_git(tmp_path, ()), _run_set(tmp_path, records)))

    assert row.package == "assay-p4-stall"
    assert row.fields["runs"].value.startswith("1 run(s)")
    assert row.fields["worktree"].state == agg.MISSING
    assert "the branch may be gone" in row.fields["worktree"].detail
    assert row.fields["gates"].state == agg.UNKNOWN


def test_a_gate_result_on_another_head_is_not_this_rows_verdict(tmp_path):
    branch = "cmru-20261124_053644-backlog-ki188"
    git = _git(tmp_path, (branch,))
    results = (_result(tmp_path, project="cmru", sha="5e02d731b" * 4 + "ab", mtime=2000.0),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), results=results))

    assert row.fields["gates"].value == agg.UNKNOWN
    assert row.fields["gates"].state == agg.UNKNOWN
    assert "NOT this row's head" in row.fields["gates"].detail
    # The collision is NAMED in the detail, never printed as the cell's value.
    assert "5e02d731b" in row.fields["gates"].detail


def test_a_gate_result_may_name_the_head_by_its_short_form(tmp_path):
    branch = "cmru-20261124_053644-backlog-ki188"
    git = _git(tmp_path, (branch,))
    results = (_result(tmp_path, project="cmru", sha="22222222"[:9]),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), results=results))

    assert row.fields["gates"].value.startswith("tests PASS v1001 22222222")


def test_a_row_without_run_or_gate_records_states_unknown_not_zero(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit-20261125_020339",))
    view = _view(git, _run_set(tmp_path, ()), results=(_result(tmp_path, project="other"),))

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["runs"].value == "0 runs"
    assert row.fields["runs"].state == agg.PRESENT
    assert row.fields["gates"].value == agg.UNKNOWN
    assert "no recorded gate result names this row's head" in row.fields["gates"].detail
    assert row.fields["owner"].value == agg.UNKNOWN


def test_a_package_named_by_peer_prose_is_reported_unverified_with_its_line(tmp_path):
    git = _git(tmp_path, ("plat-22c-resumebusy-20261125_031456",))
    peers = (_peer(tmp_path, handover=4000.0 - 2 * 3600),)
    view = _view(git, _run_set(tmp_path, ()), peers=peers)

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["owner"].value == "platform"
    assert row.fields["owner"].state == agg.UNVERIFIED
    assert row.fields["owner"].source.endswith("STATE.md:26")
    assert "P-plat-22c" in row.fields["owner"].detail
    assert row.fields["plan"].state == agg.UNVERIFIED
    assert row.fields["plan_id"].value == "22c"
    assert row.fields["plan_id"].state == agg.UNVERIFIED
    assert row.fields["handover"].value == "1-6h"


def test_a_topic_level_branch_joins_peer_prose_by_topic_and_names_the_designator(tmp_path):
    # The real estate's shape: branches carry a topic and a slug (`core-ciu-r2fix`), STATE.md
    # names a designator (`P-core-08`). Joining on the designator is impossible; joining on the
    # topic is the only honest join, and the designator gets its own cell.
    git = _git(tmp_path, ("core-ciu-r2fix",))
    peers = (_peer(tmp_path, peer="core", package="core-08", handover=4000.0 - 7 * 3600),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), peers=peers))

    assert row.fields["owner"].value == "core"
    assert row.fields["owner"].state == agg.UNVERIFIED
    assert "prose line names P-core-08" in row.fields["owner"].detail
    assert row.fields["plan_id"].value == "08"
    assert row.fields["plan_id"].state == agg.UNVERIFIED
    assert row.fields["handover"].value == "6-24h"


def test_a_peer_whose_state_is_unreadable_is_named_in_the_unknown_detail(tmp_path):
    git = _git(tmp_path, ("core-ciu-r2fix",))
    peers = (_peer(tmp_path, peer="core", package="other", state_state="missing"),)

    (row,) = agg.rows_from_inputs(
        _view(git, _run_set(tmp_path, ()), peers=peers, peers_detail=("cannot read state: Permission denied",))
    )

    assert row.fields["owner"].state == agg.UNKNOWN
    assert "unreadable peer state: core" in row.fields["owner"].detail
    assert "unreadable state directory: cannot read state" in row.fields["owner"].detail
    assert "no STATE.md line names this package id or its topic" in row.fields["owner"].detail


def test_a_missing_own_handover_is_stated_missing(tmp_path):
    git = _git(tmp_path, ("edge-relgate-20261125",))
    view = _view(git, _run_set(tmp_path, ()), handover=None)

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["handover"].state == agg.MISSING
    assert "HANDOFF.md" in row.fields["handover"].source


def test_a_peer_without_a_handover_reports_unknown_not_an_age(tmp_path):
    git = _git(tmp_path, ("assay-20261124_152636-p12-combine",))
    peers = (_peer(tmp_path, peer="assay", package="assay", handover=None),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), peers=peers))

    assert row.fields["handover"].value == agg.UNKNOWN
    assert row.fields["handover"].state == agg.MISSING


def test_a_merge_list_that_could_not_be_read_leaves_merged_unknown(tmp_path):
    git = gitstate.GitView(str(tmp_path), (_worktree(tmp_path, "p-x"),), frozenset(), "unknown", "origin/main is missing")
    view = _view(git, _run_set(tmp_path, ()))

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["merged"].value == agg.UNKNOWN
    assert row.fields["merged"].detail == "origin/main is missing"


def test_a_run_record_with_a_chain_and_a_live_token_renders_both(tmp_path):
    source = str(tmp_path / "runs")
    branch = "plat-22c-pi-branch"
    worktree = _worktree(tmp_path, branch).path
    records = (
        _record("plat-22c-pi", source=source, files=("done",), outcome="SUCCESS", worktree=worktree),
        _record("plat-22c-pi-y1", source=source, files=(), outcome="", tokens=53, worktree=worktree),
        _record("plat-22c-pi-y2", source=source, files=("live",), outcome="", tokens=90000, last_state="READY", worktree=worktree),
    )
    git = _git(tmp_path, (branch,))
    view = _view(git, _run_set(tmp_path, records))

    (row,) = agg.rows_from_inputs(view)

    assert "3 run(s)" in row.fields["runs"].value
    assert "1 ended" in row.fields["runs"].value
    assert "chain plat-22c-pi>plat-22c-pi-y1>plat-22c-pi-y2" in row.fields["runs"].detail
    assert "plat-22c-pi-y2 active 90k" in row.fields["runs"].detail


def test_no_readable_runs_directory_makes_the_runs_cell_unknown(tmp_path):
    git = _git(tmp_path, ("p-x",))
    run_set = ag_runs.RunSet(records=(), dirs=(str(tmp_path / "runs"),), readable=())

    (row,) = agg.rows_from_inputs(_view(git, run_set))

    assert row.fields["runs"].state == agg.MISSING
    assert str(tmp_path / "runs") in row.fields["runs"].detail


def test_a_run_whose_lock_is_free_and_which_never_ended_is_died(tmp_path):
    path = tmp_path / "runs" / "p.lock"
    # A record with a lock path but no `.done`: the raw `RunRecord` is built with its paths map.
    record = ag_runs.RunRecord(
        id="p-pi", source=str(tmp_path / "runs"), files=("live", "lock"), exit_code=None, outcome="", review="",
        route="luna", thread="", worktree="", tokens=9000, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="", paths={"lock": path},
    )
    view = _view(_git(tmp_path, ("p-pi",)), _run_set(tmp_path, (record,)),
                 locks=ag_sources.LockTable((), True, ""))

    (row,) = agg.rows_from_inputs(view)

    assert "1 died" in row.fields["runs"].value
    assert "p-pi DIED (lock free, never ended)" in row.fields["runs"].detail


def test_a_run_with_no_lock_file_is_undecided_rather_than_active(tmp_path):
    record = ag_runs.RunRecord(
        id="p-pi", source=str(tmp_path / "runs"), files=("live",), exit_code=None, outcome="", review="",
        route="luna", thread="", worktree="", tokens=1, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="",
    )

    (row,) = agg.rows_from_inputs(_view(_git(tmp_path, ()), _run_set(tmp_path, (record,))))

    assert "1 undecided" in row.fields["runs"].value
    assert "undecided (no readable lock file)" in row.fields["runs"].detail


def test_a_run_whose_lock_is_held_is_active(tmp_path):
    path = tmp_path / "runs" / "p.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    status = path.stat()
    held = ag_sources.LockEntry(
        kind="FLOCK",
        pid=99,
        device=(os.major(status.st_dev), os.minor(status.st_dev)),
        inode=status.st_ino,
    )
    record = ag_runs.RunRecord(
        id="p-pi", source=str(tmp_path / "runs"), files=("live", "lock"), exit_code=None, outcome="", review="",
        route="luna", thread="", worktree="", tokens=1, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="", paths={"lock": path},
    )

    (row,) = agg.rows_from_inputs(_view(_git(tmp_path, ()), _run_set(tmp_path, (record,)), locks=_locks(held)))

    assert "1 active" in row.fields["runs"].value
    assert "p-pi active" in row.fields["runs"].detail


def test_an_unreadable_runs_directory_is_named_without_losing_the_records(tmp_path):
    records = (_record("p-pi", source=str(tmp_path / "runs")),)
    run_set = ag_runs.RunSet(
        records=records, dirs=(str(tmp_path / "runs"), str(tmp_path / "other")),
        readable=(str(tmp_path / "runs"),),
        unreadable=((str(tmp_path / "other"), "cannot read /other: Permission denied"),),
    )

    (row,) = agg.rows_from_inputs(_view(_git(tmp_path, ()), run_set))

    assert row.fields["runs"].state == agg.PRESENT
    assert "unreadable run directory: cannot read /other" in row.fields["runs"].detail


# --- text rendering ----------------------------------------------------------------------------


def test_text_rendering_states_every_section_and_the_unknown_count(tmp_path):
    view = _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ()),
                 slots=(_slot(state="held"), _slot(name="r1002.slot1", state="unknown")))

    text = agg.render_text(agg.rows_from_inputs(view), view)

    assert "1 package row(s)" in text
    assert "gate slots 2 (1 held, 1 unknown" in text
    assert "PACKAGE plat-19m-acceptkit" in text
    assert agg.SLOTS_SOURCE in text
    assert "unverified:" in text
    assert "unknown/missing:" in text


def test_text_rendering_states_unreadable_run_and_merge_sources(tmp_path):
    git = gitstate.GitView(str(tmp_path), (), frozenset(), "unknown", "no remote")
    run_set = ag_runs.RunSet(records=(), dirs=(str(tmp_path / "runs"),), readable=())

    text = agg.render_text([], _view(git, run_set))

    assert "merge state UNKNOWN (no remote)" in text
    assert "run records UNKNOWN" in text


def test_text_rendering_states_an_unlistable_git_without_dropping_the_other_rows(tmp_path):
    git = gitstate.GitView("", (), frozenset(), "unknown", "no git work tree at /cwd")
    records = (_record("p-pi", source=str(tmp_path / "runs")),)

    text = agg.render_text(agg.rows_from_inputs(_view(git, _run_set(tmp_path, records))), _view(git, _run_set(tmp_path, records)))

    assert "git UNKNOWN (no git work tree at /cwd)" in text
    assert "PACKAGE p-pi" in text


def test_text_rendering_states_an_unlistable_lock_and_peer_directory(tmp_path):
    view = _view(_git(tmp_path, ("p-x",)), _run_set(tmp_path, ()),
                 slots_detail="cannot read locks: Permission denied",
                 results_detail="cannot read gates: Permission denied",
                 peers_detail=("cannot read state: Permission denied",))

    text = agg.render_text(agg.rows_from_inputs(view), view)

    assert "gate slots UNKNOWN (cannot read locks" in text
    assert "gate results UNKNOWN (cannot read gates" in text
    assert "peer state PARTIAL (cannot read state" in text


# --- the header ----------------------------------------------------------------------------------


def test_the_header_lists_merge_candidates_leases_live_runs_and_ages(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit-20261125_020339",), merged=frozenset({"main"}))
    record, lock = _live_record(tmp_path, "plat-19m-pi-y1", tokens=9000)
    view = _view(
        git,
        _run_set(tmp_path, (record,)),
        locks=_held((lock,)),
        slots=(_slot(state="held", pid=7), _slot(host="r1002", name="r1002.slot1", state="free")),
        peers=(_peer(tmp_path, peer="core", package="other", handover=4000.0 - 7 * 3600),),
        handover=4000.0 - 30 * 3600,
    )

    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert text.splitlines() == [
        "MERGE 1: plat-19m-acceptkit-20261125_020339 " + "2" * 40,
        "LEASES 1: v1001 v1001.slot1 pid 7",
        "LIVE 1: plat-19m-pi-y1 luna <50k",
        "PEERS: core 6-24h",
        "HANDOVER own >24h",
    ]


def test_the_header_says_none_and_unknown_instead_of_omitting_a_section(tmp_path):
    view = _view(_git(tmp_path, ()), _run_set(tmp_path, ()), handover=None)

    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert "MERGE 0: none" in text
    assert "LEASES 0: none" in text
    assert "LIVE 0: none" in text
    assert "PEERS: none" in text
    assert "HANDOVER own unknown" in text


def test_the_header_states_an_unreadable_run_source_as_unknown(tmp_path):
    run_set = ag_runs.RunSet(records=(), dirs=(), readable=())

    text = agg.render_header([], _view(_git(tmp_path, ()), run_set))

    assert "LIVE: unknown (no readable runs directory)" in text


def test_a_died_run_is_not_listed_as_live(tmp_path):
    path = tmp_path / "runs" / "p.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    record = ag_runs.RunRecord(
        id="assay-p4-stall", source=str(tmp_path / "runs"), files=("live", "lock"), exit_code=None, outcome="",
        review="", route="luna", thread="", worktree="", tokens=9000, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="", paths={"lock": path},
    )
    view = _view(_git(tmp_path, ()), _run_set(tmp_path, (record,)), locks=ag_sources.LockTable((), True, ""))

    undecided = ag_runs.RunRecord(
        id="cf", source=str(tmp_path / "runs"), files=("live",), exit_code=None, outcome="", review="",
        route="luna", thread="", worktree="", tokens=1000, live_mtime=None,
        last_state="", last_run="", gauge="", boundary="", mark="",
    )
    view = _view(_git(tmp_path, ()), _run_set(tmp_path, (record, undecided)),
                 locks=ag_sources.LockTable((), True, ""))

    text = agg.render_header(agg.rows_from_inputs(view), view)
    live_line = next(line for line in text.splitlines() if line.startswith("LIVE"))

    # The reader learns the set is not "runs believed alive", and neither the dead nor the
    # undecidable run is in it.
    assert live_line == "LIVE 0: none (excluded: 1 died, 1 undecided)"
    assert "assay-p4-stall" not in live_line
    assert "cf " not in live_line


def test_an_unchanged_estate_prints_a_byte_identical_header_even_as_the_live_count_moves(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",))
    peers = (_peer(tmp_path),)
    first, lock = _live_record(tmp_path, "plat-19m-pi", tokens=5000)
    # A second writer update INSIDE the same bucket: the figure moved, the header must not.
    second = _live_record(tmp_path, "plat-19m-pi", tokens=5100)[0]
    first_records, second_records = (first,), (second,)

    first_text = agg.render_header(
        agg.rows_from_inputs(_view(git, _run_set(tmp_path, first_records), peers=peers, now=4000.0, locks=_held((lock,)))), 
        _view(git, _run_set(tmp_path, first_records), peers=peers, now=4000.0, locks=_held((lock,))),
    )
    # One second later, with a writer update inside the same bucket: the text must not move.
    second_text = agg.render_header(
        agg.rows_from_inputs(_view(git, _run_set(tmp_path, second_records), peers=peers, now=4001.0, locks=_held((lock,)))),
        _view(git, _run_set(tmp_path, second_records), peers=peers, now=4001.0, locks=_held((lock,))),
    )

    assert first_text == second_text
    assert "2026-" not in first_text


def test_a_context_bucket_edge_does_change_the_header(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",))
    below, lock = _live_record(tmp_path, "plat-19m-pi", tokens=49_999)

    below_view = _view(git, _run_set(tmp_path, (below,)), locks=_held((lock,)))
    assert "LIVE 1: plat-19m-pi luna <50k" in agg.render_header(agg.rows_from_inputs(below_view), below_view)

    above, lock = _live_record(tmp_path, "plat-19m-pi", tokens=50_000)
    above_view = _view(git, _run_set(tmp_path, (above,)), locks=_held((lock,)))
    assert "LIVE 1: plat-19m-pi luna 50-150k" in agg.render_header(agg.rows_from_inputs(above_view), above_view)


def test_each_header_line_is_bounded_so_the_later_sections_survive_a_large_estate(tmp_path):
    branches = tuple(f"cmru-20261124_053644-backlog-ki{i}" for i in range(200))
    git = _git(tmp_path, branches, merged=frozenset())
    results = tuple(_result(tmp_path, name=f"rgw-{i}", project="agctl") for i in range(200))
    peers = tuple(_peer(tmp_path, peer=f"p{i}", package="other") for i in range(50))
    records = tuple(_live_record(tmp_path, f"r{i}", tokens=1) for i in range(100))
    view = _view(
        git,
        _run_set(tmp_path, tuple(record for record, _lock in records)),
        results=results,
        peers=peers,
        handover=None,
        locks=_held(tuple(lock for _record_, lock in records)),
    )

    text = agg.render_header(agg.rows_from_inputs(view), view)
    lines = text.splitlines()

    assert len(text.encode("utf-8")) <= agg.HEADER_LIMIT
    assert agg.HEADER_MARKER not in text
    assert [line.split()[0] for line in lines] == ["MERGE", "LEASES", "LIVE", "PEERS", "HANDOVER"]
    assert "+192 more" in lines[0]
    assert "+92 more" in lines[2]
    assert "+42 more" in lines[3]


def test_the_merge_queue_line_is_read_from_the_handover_when_it_exists(tmp_path):
    view = _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ()), merge_queue="q293 plat-19m, q294 core-ciu")

    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert text.splitlines()[0] == "MERGE QUEUE 1: q293 plat-19m, q294 core-ciu [the current handover's MERGE QUEUE line (prose), unverified]"


def test_the_merge_line_falls_back_to_unmerged_branches_only_when_the_queue_is_absent(tmp_path):
    view = _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ()), merge_queue="")

    text = agg.render_header(agg.rows_from_inputs(view), view)

    # The label itself says which source this is: `MERGE` is the DESIGN 8 item 1d fallback,
    # `MERGE QUEUE` is the handover line.
    assert text.splitlines()[0] == "MERGE 1: plat-19m-acceptkit " + "2" * 40
    assert "MERGE QUEUE" not in text
    assert view.merge_queue_detail == "no MERGE QUEUE line in the handover"


def test_a_header_exactly_at_the_limit_is_not_marked(tmp_path):
    git = _git(tmp_path, ("p",))
    view = _view(git, _run_set(tmp_path, ()))
    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert len(text) < agg.HEADER_LIMIT
    assert agg.HEADER_MARKER not in text


def test_truncation_leaves_a_block_that_fits_completely_alone():
    short = ["MERGE 0: none", "LEASES 0: none"]

    assert agg._truncate(short).splitlines() == short


def test_truncation_after_one_fitting_line_keeps_that_line_only():
    truncated = agg._truncate(["MERGE 0: none", "L" * 6000])

    assert truncated.splitlines() == ["MERGE 0: none", agg.HEADER_MARKER]


def test_truncation_counts_BYTES_not_code_points():
    # A non-ASCII line is longer in bytes than in code points; the cap is documented in bytes.
    line = "é" * 4000

    truncated = agg._truncate([line])

    assert len(truncated.encode("utf-8")) <= agg.HEADER_LIMIT
    assert truncated.endswith(agg.HEADER_MARKER)


def test_truncation_keeps_whole_lines_and_marks_the_block():
    truncated = agg._truncate(["é" * 10, "b" * 5000])

    assert truncated.splitlines() == ["é" * 10, agg.HEADER_MARKER]
    assert len(truncated.encode("utf-8")) <= agg.HEADER_LIMIT


def test_truncation_of_a_single_over_long_line_cuts_it_and_marks_it():
    truncated = agg._truncate(["x" * 6000])

    assert truncated.endswith("\n" + agg.HEADER_MARKER)
    assert len(truncated.encode("utf-8")) <= agg.HEADER_LIMIT


def test_a_runs_cell_without_a_context_figure_says_so(tmp_path):
    git = _git(tmp_path, ("p-ciu-r2fix",))
    records = (_record("p-ciu-r2fix-pi", source=str(tmp_path / "runs"), files=("done",), outcome="SUCCESS",
                       tokens=None, worktree=_worktree(tmp_path, "p-ciu-r2fix").path),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, records)))

    assert "p-ciu-r2fix-pi SUCCESS exit 0" in row.fields["runs"].detail
    assert "k " not in row.fields["runs"].detail and "0k" not in row.fields["runs"].detail


def test_a_gate_result_with_no_slot_held_says_so(tmp_path):
    git = _git(tmp_path, ("cmru-x",))

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), results=(_result(tmp_path, project="x", sha="2" * 40),)))

    assert row.fields["gates"].value.startswith("tests PASS")
    assert "no gate slot held" in row.fields["gates"].detail


# --- the handler ----------------------------------------------------------------------------------


def test_the_handler_keeps_the_other_sources_when_no_root_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(gitstate, "discover_root", lambda repo, cwd: (None, "not a git repository"))
    records = (_record("assay-p4-stall", source=str(tmp_path / "runs")),)
    monkeypatch.setattr(ag_runs, "read", lambda config: _run_set(tmp_path, records))
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: ag_sources.LockTable((), True, ""))
    monkeypatch.setattr(ag_sources, "scan_gate_slots", lambda config, locks: ag_sources.SlotScan((_slot(state="held", pid=7),)))
    monkeypatch.setattr(ag_sources, "scan_results", lambda config: ag_sources.GateScan(()))
    monkeypatch.setattr(ag_sources, "scan_peers", lambda config: ag_sources.PeerScan((_peer(tmp_path, package="assay"),)))
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: ("/h", 1000.0, ""))
    runtime = FakeRuntime()

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    text = str(runtime.output.last)

    assert "estate: git UNKNOWN (not a git repository)" in text
    assert "PACKAGE assay-p4-stall" in text
    assert "gate slots 1 (1 held" in text
    assert "PACKAGE assay-p4-stall" in text


def test_the_json_handler_reports_an_unresolvable_root_as_structured_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(gitstate, "discover_root", lambda repo, cwd: (None, "not a git repository"))
    monkeypatch.setattr(ag_runs, "read", lambda config: _run_set(tmp_path, ()))
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: ag_sources.LockTable((), True, ""))
    monkeypatch.setattr(ag_sources, "scan_gate_slots", lambda config, locks: ag_sources.SlotScan(()))
    monkeypatch.setattr(ag_sources, "scan_results", lambda config: ag_sources.GateScan(()))
    monkeypatch.setattr(ag_sources, "scan_peers", lambda config: ag_sources.PeerScan(()))
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: ("", None, "no CONTROLLER_REPO is configured"))
    runtime = FakeRuntime(json_mode=True)

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    payload = runtime.output.last

    assert payload["root"] is None
    assert payload["rows"] == []
    assert "detail" in payload["unknown"]["root"]
    assert payload["sources"]["gate_slots"] == []


def _patched(monkeypatch, view):
    monkeypatch.setattr(gitstate, "discover_root", lambda repo, cwd: (view.git.root, view.git.detail))
    monkeypatch.setattr(gitstate, "read_view", lambda root: view.git)
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: view.locks)
    monkeypatch.setattr(ag_sources, "scan_gate_slots", lambda config, locks: ag_sources.SlotScan(view.slots, view.slots_detail))
    monkeypatch.setattr(ag_sources, "scan_results", lambda config: ag_sources.GateScan(view.results, view.results_detail))
    monkeypatch.setattr(ag_sources, "scan_peers", lambda config: ag_sources.PeerScan(view.peers, view.peers_detail))
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: (view.handover_path, view.handover_mtime, view.handover_detail))
    monkeypatch.setattr(ag_runs, "read", lambda config: view.run_set)


def test_the_handler_prints_the_json_schema_with_every_source(monkeypatch, tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",))
    peers = (_peer(tmp_path, package="plat-19m"),)
    results = (_result(tmp_path, project="plat-19m", sha="2" * 40),)
    view = _view(git, _run_set(tmp_path, ()), peers=peers, results=results, slots=(_slot(state="held"),))
    _patched(monkeypatch, view)
    runtime = FakeRuntime(json_mode=True)

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    payload = runtime.output.last

    assert payload["schema"] == "agctl-estate/1"
    assert payload["root"] == git.root
    assert payload["git"]["worktrees"] == 2
    assert payload["sources"]["runs"]["records"] == 0
    assert payload["sources"]["gate_slots"][0]["state"] == "held"
    assert payload["sources"]["gate_results"] == 1
    assert payload["sources"]["peers"][0]["lines"] == 1
    assert payload["sources"]["handover"]["mtime"] == 1000.0
    row = payload["rows"][0]
    assert row["package"] == "plat-19m-acceptkit"
    assert row["branches"] == ["plat-19m-acceptkit"]
    assert row["unmerged"] == ["plat-19m-acceptkit"]
    assert set(row) == {"package", "branches", "unmerged", *agg.FIELDS}
    assert row["merged"]["value"].startswith("no")
    assert row["gates"]["value"].startswith("tests PASS")


def test_the_handler_prints_rows_as_text(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ())))
    runtime = FakeRuntime()

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0

    assert "PACKAGE plat-19m-acceptkit" in str(runtime.output.last)


def test_the_header_flag_prints_the_header_text(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ())))
    runtime = FakeRuntime()

    assert agg.run(Args(header=True), runtime, config=_config(tmp_path)) == 0

    assert str(runtime.output.last).startswith("MERGE 1: plat-19m-acceptkit")


def test_the_header_refuses_a_json_request_with_the_documented_exit_code(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m",)), _run_set(tmp_path, ())))

    with pytest.raises(CliFailure, match="cannot be combined with --json") as raised:
        agg.run(Args(header=True), FakeRuntime(json_mode=True), config=_config(tmp_path))

    # README and DESIGN both document 2 for a usage refusal, and cli-extended reads
    # `getattr(exc, "exit_code", 1)`: without `exit_code` the answer would silently be 1.
    assert raised.value.exit_code == 2
    assert raised.value.hint == "drop one of --header / --json"


def test_the_estate_cli_reports_the_refusal_with_exit_two_end_to_end(capsys):
    # The exit status is the CONTRACT a script tests for, so it is asserted through the real
    # cli-extended runner rather than on the exception alone. Nothing is read: the refusal
    # comes before the first source, so this test needs no fixture tree at all.
    from agctl import cli as agcli

    code = agcli.build_cli(agcli.CliIdentity("AGCTL", "1.0.0", "n", "agctl")).run(
        argv=["estate", "--header", "--json"]
    )

    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_the_header_refusal_needs_no_source_read_at_all(monkeypatch, tmp_path):
    calls: list[str] = []

    def refuse(config):
        calls.append("read")
        return _run_set(tmp_path, ())

    monkeypatch.setattr(ag_runs, "read", refuse)

    with pytest.raises(CliFailure):
        agg.run(Args(header=True), FakeRuntime(json_mode=True), config=_config(tmp_path))

    assert calls == []


def test_read_view_takes_the_clock_it_is_given(tmp_path, monkeypatch):
    # `now` is injectable so an age assertion is decidable; without it the clock is time.time().
    monkeypatch.setattr(gitstate, "discover_root", lambda repo, cwd: (str(tmp_path / "repo"), ""))
    monkeypatch.setattr(gitstate, "read_view", lambda root: _git(tmp_path, ()))
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: ag_sources.LockTable((), True, ""))
    monkeypatch.setattr(ag_sources, "scan_gate_slots", lambda config, locks: ag_sources.SlotScan(()))
    monkeypatch.setattr(ag_sources, "scan_results", lambda config: ag_sources.GateScan(()))
    monkeypatch.setattr(ag_sources, "scan_peers", lambda config: ag_sources.PeerScan(()))
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: ("/h", 10.0, ""))
    monkeypatch.setattr(ag_runs, "read", lambda config: _run_set(tmp_path, ()))

    view = agg.read_view(_config(tmp_path), now=4000.0)

    assert view.now == 4000.0
    assert view.git.root == str(tmp_path / "repo")


def test_a_handover_without_a_merge_queue_line_says_so(tmp_path):
    handover = tmp_path / "HANDOFF.md"
    handover.write_text("# handover\nno queue line here\n", encoding="utf-8")

    value, detail = agg._merge_queue(str(handover))

    assert value == ""
    assert "no MERGE QUEUE line" in detail


def test_a_short_sha_is_not_enough_to_name_a_head(tmp_path):
    # A 6-character prefix would match unrelated commits; `SHORT_SHA` is the floor.
    assert agg._sha_matches("2" * 6, "2" * 40) is False
    assert agg._sha_matches("2" * 7, "2" * 40) is True
    assert agg._sha_matches("9" * 7, "2" * 40) is False


def test_a_merge_queue_line_is_cut_to_its_bound(tmp_path):
    handover = tmp_path / "HANDOFF.md"
    handover.write_text("MERGE QUEUE: " + "x" * 300 + "\n", encoding="utf-8")

    value, detail = agg._merge_queue(str(handover))

    assert detail == ""
    assert len(value) <= agg.MERGE_QUEUE_LIMIT
    assert value.endswith("~")


def test_a_merge_queue_line_is_found_even_when_it_sits_far_from_the_end(tmp_path):
    handover = tmp_path / "HANDOFF.md"
    handover.write_text("MERGE QUEUE (ready now): q293\n" + "prose\n" * 5000, encoding="utf-8")

    value, detail = agg._merge_queue(str(handover))

    assert value == "MERGE QUEUE (ready now): q293"
    assert detail == ""


def test_no_handover_path_is_stated_not_silently_empty():
    value, detail = agg._merge_queue("")

    assert value == ""
    assert "no handover path is configured" in detail


def test_the_handler_reads_the_live_inputs_only_through_the_readers(tmp_path, monkeypatch):
    # A config with the live shapes but every reader patched: the handler itself must not read
    # a file, and the JSON payload must carry only what the readers returned.
    git = _git(tmp_path, ("p-x",))
    view = _view(git, _run_set(tmp_path, ()))
    _patched(monkeypatch, view)
    runtime = FakeRuntime(json_mode=True)

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    payload = json.loads(json.dumps(runtime.output.last))
    assert payload["sources"]["gate_slots"] == []


def test_a_branch_merged_into_origin_main_reads_as_yes(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",), merged=frozenset({"main", "plat-19m-acceptkit"}))

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ())))

    assert row.fields["merged"].value == "yes"
    assert row.unmerged == ()
    assert row.fields["merged"].detail == ""


def test_a_merge_list_that_is_present_but_does_not_name_the_branch_is_no(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",), merged=frozenset({"main"}))

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ())))

    assert row.unmerged == ("plat-19m-acceptkit",)
    assert "merge candidate" in row.fields["merged"].detail
