"""The `estate` handler over every input: temp trees only, a fake runtime, never the live estate.

The fixture assemblies are built by hand (`_view`) so a test names exactly the estate it
asserts on: which branches exist, which run records belong to which package, which gate slots
are held and which peer prose names which package id.
"""

from __future__ import annotations

import json

import pytest

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
            tokens: int | None = None, last_state: str = "", gauge: str = "") -> ag_runs.RunRecord:
    return ag_runs.RunRecord(
        id=run_id, source=source, files=files, exit_code=0, outcome=outcome, review="",
        route="luna", thread="", worktree="", tokens=tokens, live_mtime=None,
        last_state=last_state, last_run="", gauge=gauge, boundary="", mark="",
    )


def _run_set(tmp_path, records: tuple[ag_runs.RunRecord, ...]) -> ag_runs.RunSet:
    return ag_runs.RunSet(records=records, dirs=(str(tmp_path / "runs"),), readable=(str(tmp_path / "runs"),))


def _slot(host: str = "v1001", name: str = "v1001.slot1", state: str = "free", pid: int | None = None) -> ag_sources.Slot:
    return ag_sources.Slot(host=host, name=name, kind="slot", state=state, holder_pid=pid, detail="", path=f"/locks/{name}")


def _result(tmp_path, name: str = "rgw-tests-1", project: str = "cmru", lane: str = "tests", verdict: str = "PASS",
            mtime: float = 1000.0, sha: str = "5e02d731b") -> ag_sources.GateResult:
    return ag_sources.GateResult(
        name=name, host="v1001", project=project, lane=lane, sha="5e02d731b", verdict=verdict,
        verdict_source="lane.log: run-gate verdict line", started="2026-11-25T21:22:23Z", mtime=mtime, detail="",
    )


def _peer(tmp_path, peer: str = "platform", package: str = "plat-22c", state_state: str = "present",
          handover: float | None = 1000.0) -> ag_sources.PeerState:
    lines = ()
    if state_state == "present":
        lines = (ag_sources.StateLine(peer=peer, path=f"/state/{peer}/STATE.md", line=26, package=package,
                                       token=f"P-{package}", text="prose line about the package"),)
    return ag_sources.PeerState(
        peer=peer, state_path=f"/state/{peer}/STATE.md", state_mtime=1000.0, state_state=state_state,
        state_detail="", handover_path=f"/state/{peer}/HANDOFF.md", handover_mtime=handover, lines=lines,
    )


def _view(git: gitstate.GitView, run_set: ag_runs.RunSet, *, slots=(), results=(), peers=(), handover: float | None = 1000.0,
          now: float = 4000.0) -> agg.View:
    return agg.View(
        git=git, run_set=run_set, slots=tuple(slots), results=tuple(results), peers=tuple(peers),
        handover_path="/repo/.controller-state/HANDOFF.md", handover_mtime=handover, handover_detail="" if handover else "missing",
        now=now,
    )


# --- ages -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("age", "expected"), [(0, "<1h"), (3599, "<1h"), (3600, "1-6h"), (6 * 3600 - 1, "1-6h"), (6 * 3600, "6-24h"), (24 * 3600, ">24h")])
def test_age_buckets_are_fixed_edges(age, expected):
    assert agg.age_bucket(age) == expected


def test_an_unknown_age_is_not_a_bucket():
    assert agg.age_bucket(None) == agg.AGE_UNKNOWN


# --- rows ----------------------------------------------------------------------------------------


def test_rows_join_branches_runs_and_gate_results_on_the_package_key(tmp_path):
    git = _git(tmp_path, ("cmru-20261124_053644-backlog-ki188",))
    run_set = _run_set(tmp_path, (_record("cmru-20261124T053644-ci", source=str(tmp_path / "runs")),))
    view = _view(git, run_set, results=(_result(tmp_path),), slots=(_slot(state="held", pid=42),))

    (row,) = agg.rows_from_inputs(view)

    assert row.package == "cmru"
    assert row.branches == ("cmru-20261124_053644-backlog-ki188",)
    assert "1 run(s), 0 active" in row.fields["runs"].value
    assert row.fields["gates"].value.startswith("tests PASS v1001")
    assert "1 slot(s) held estate-wide" in row.fields["gates"].detail
    assert row.fields["merged"].value == "no: cmru-20261124_053644-backlog-ki188"


def test_a_row_without_run_or_gate_records_states_unknown_not_zero(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit-20261125_020339",))
    view = _view(git, _run_set(tmp_path, ()), results=(_result(tmp_path, project="other"),))

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["runs"].value == "0 runs"
    assert row.fields["runs"].state == agg.PRESENT
    assert row.fields["gates"].value == agg.UNKNOWN
    assert "no remote-gate record" in row.fields["gates"].detail
    assert row.fields["owner"].value == agg.UNKNOWN


def test_a_package_named_by_peer_prose_is_reported_unverified_with_its_line(tmp_path):
    git = _git(tmp_path, ("plat-22c-resumebusy-20261125_031456",))
    view = _view(git, _run_set(tmp_path, ()), peers=(_peer(tmp_path, handover=4000.0 - 2 * 3600),))

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["owner"].value == "platform"
    assert row.fields["owner"].state == agg.UNVERIFIED
    assert row.fields["owner"].source.endswith("STATE.md:26")
    assert "P-plat-22c" in row.fields["owner"].detail
    assert row.fields["plan"].state == agg.UNVERIFIED
    assert row.fields["handover"].value == "1-6h"


def test_a_peer_whose_state_is_unreadable_is_named_in_the_unknown_detail(tmp_path):
    git = _git(tmp_path, ("core-ciu-r2fix",))
    view = _view(git, _run_set(tmp_path, ()), peers=(_peer(tmp_path, peer="core", state_state="missing"),))

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["owner"].state == agg.UNKNOWN
    assert "unreadable peer state: core" in row.fields["owner"].detail


def test_a_missing_own_handover_is_stated_missing(tmp_path):
    git = _git(tmp_path, ("edge-relgate-20261125",))
    view = _view(git, _run_set(tmp_path, ()), handover=None)

    (row,) = agg.rows_from_inputs(view)

    assert row.fields["handover"].state == agg.MISSING
    assert "HANDOFF.md" in row.fields["handover"].source


def test_a_peer_without_a_handover_reports_unknown_not_an_age(tmp_path):
    git = _git(tmp_path, ("assay-20261124_152636-p12-combine",))
    view = _view(git, _run_set(tmp_path, ()), peers=(_peer(tmp_path, peer="assay", package="assay", handover=None),))

    (row,) = agg.rows_from_inputs(view)

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
    records = (
        _record("plat-22c-pi", source=source, files=("done",), outcome="SUCCESS"),
        _record("plat-22c-pi-y1", source=source, files=(), outcome="", tokens=53),
        _record("plat-22c-pi-y2", source=source, files=("live",), outcome="", tokens=90000, last_state="READY"),
    )
    git = _git(tmp_path, ("plat-22c-pi-branch",))
    view = _view(git, _run_set(tmp_path, records))

    (row,) = agg.rows_from_inputs(view)

    assert "3 run(s), 2 active" in row.fields["runs"].value
    assert "chain plat-22c-pi>plat-22c-pi-y1>plat-22c-pi-y2" in row.fields["runs"].detail
    assert "plat-22c-pi-y2 active 90k" in row.fields["runs"].detail


def test_no_readable_runs_directory_makes_the_runs_cell_unknown(tmp_path):
    git = _git(tmp_path, ("p-x",))
    run_set = ag_runs.RunSet(records=(), dirs=(str(tmp_path / "runs"),), readable=())

    (row,) = agg.rows_from_inputs(_view(git, run_set))

    assert row.fields["runs"].state == agg.MISSING
    assert str(tmp_path / "runs") in row.fields["runs"].detail


# --- text rendering ----------------------------------------------------------------------------


def test_text_rendering_states_every_section_and_the_unknown_count(tmp_path):
    view = _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ()), slots=(_slot(state="held"), _slot(name="r1002.slot1", state="unknown")))

    text = agg.render_text(agg.rows_from_inputs(view), view)

    assert "1 package row(s)" in text
    assert "gate slots 2 (1 held, 1 unknown" in text
    assert "PACKAGE plat-19m" in text
    assert agg.SLOTS_SOURCE in text
    assert "unverified:" in text
    assert "unknown/missing:" in text


def test_text_rendering_states_unreadable_run_and_merge_sources(tmp_path):
    git = gitstate.GitView(str(tmp_path), (), frozenset(), "unknown", "no remote")
    run_set = ag_runs.RunSet(records=(), dirs=(str(tmp_path / "runs"),), readable=())

    text = agg.render_text([], _view(git, run_set))

    assert "merge state UNKNOWN (no remote)" in text
    assert "run records UNKNOWN" in text


# --- the header ----------------------------------------------------------------------------------


def test_the_header_lists_merge_candidates_leases_live_runs_and_ages(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit-20261125_020339",), merged=frozenset({"main"}))
    records = (_record("plat-19m-pi-y1", source=str(tmp_path / "runs"), files=("live",), outcome="", tokens=9000, last_state="READY"),)
    view = _view(
        git,
        _run_set(tmp_path, records),
        slots=(_slot(state="held", pid=7), _slot(host="r1002", name="r1002.slot1", state="free")),
        peers=(_peer(tmp_path, peer="core", package="other", handover=4000.0 - 7 * 3600),),
        handover=4000.0 - 30 * 3600,
    )

    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert text.splitlines() == [
        "MERGE 1: plat-19m-acceptkit-20261125_020339 " + "2" * 40,
        "LEASES 1: v1001 v1001.slot1 pid 7",
        "LIVE 1: plat-19m-pi-y1 luna 9k",
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


def test_an_unchanged_estate_prints_a_byte_identical_header(tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",))
    records = (_record("plat-19m-pi", source=str(tmp_path / "runs"), files=("live",), outcome="", tokens=5000, last_state="READY"),)
    peers = (_peer(tmp_path),)

    first = agg.render_header(agg.rows_from_inputs(_view(git, _run_set(tmp_path, records), peers=peers, now=4000.0)), _view(git, _run_set(tmp_path, records), peers=peers, now=4000.0))
    # One second later, inside the same bucket: the text must not move.
    second = agg.render_header(
        agg.rows_from_inputs(_view(git, _run_set(tmp_path, records), peers=peers, now=4001.0)),
        _view(git, _run_set(tmp_path, records), peers=peers, now=4001.0),
    )

    assert first == second
    assert "2026-" not in first


def test_the_header_is_capped_and_marked_when_it_is_too_long(tmp_path):
    branches = tuple(f"cmru-20261124_053644-backlog-ki{i}" for i in range(200))
    git = _git(tmp_path, branches, merged=frozenset())

    text = agg.render_header(agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()))), _view(git, _run_set(tmp_path, ())))

    assert len(text.encode()) <= agg.HEADER_LIMIT
    assert text.rstrip("\n").endswith(agg.HEADER_MARKER)
    # The cap keeps whole lines only: every line before the marker is a complete section line.
    assert all(line.startswith(("MERGE ", "LEASES ", "LIVE ", "PEERS: ", "HANDOVER ")) for line in text.splitlines()[:-1])


def test_a_header_exactly_at_the_limit_is_not_marked(tmp_path):
    git = _git(tmp_path, ("p",))
    view = _view(git, _run_set(tmp_path, ()))
    text = agg.render_header(agg.rows_from_inputs(view), view)

    assert len(text) < agg.HEADER_LIMIT
    assert agg.HEADER_MARKER not in text


# --- the handler ----------------------------------------------------------------------------------


def test_the_handler_reports_an_unresolvable_root_as_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(gitstate, "discover_root", lambda start: (None, "not a git repository"))
    runtime = FakeRuntime()

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    assert str(runtime.output.last).startswith("estate: UNKNOWN")


def test_the_json_handler_reports_an_unresolvable_root_as_structured_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(gitstate, "discover_root", lambda start: (None, "not a git repository"))
    runtime = FakeRuntime(json_mode=True)

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0
    payload = runtime.output.last
    assert payload["root"] is None
    assert payload["rows"] == []
    assert "detail" in payload["unknown"]["root"]


def _patched(monkeypatch, view):
    monkeypatch.setattr(gitstate, "discover_root", lambda start: (view.git.root, ""))
    monkeypatch.setattr(gitstate, "read_view", lambda root: view.git)
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: ag_sources.LockTable((), True, ""))
    monkeypatch.setattr(ag_sources, "gate_slots", lambda config, locks: view.slots)
    monkeypatch.setattr(ag_sources, "read_results", lambda config: view.results)
    monkeypatch.setattr(ag_sources, "read_peers", lambda config: view.peers)
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: (view.handover_path, view.handover_mtime, view.handover_detail))
    monkeypatch.setattr(ag_runs, "read", lambda config: view.run_set)


def test_the_handler_prints_the_json_schema_with_every_source(monkeypatch, tmp_path):
    git = _git(tmp_path, ("plat-19m-acceptkit",))
    view = _view(git, _run_set(tmp_path, ()), peers=(_peer(tmp_path, package="plat-19m"),), results=(_result(tmp_path, project="plat-19m", sha="abc"),), slots=(_slot(state="held"),))
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
    assert row["package"] == "plat-19m"
    assert row["branches"] == ["plat-19m-acceptkit"]
    assert row["unmerged"] == ["plat-19m-acceptkit"]
    assert set(row) == {"package", "branches", "unmerged", *agg.FIELDS}
    assert row["merged"]["value"].startswith("no")
    assert row["gates"]["value"].startswith("tests PASS")


def test_the_handler_prints_rows_as_text(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ())))
    runtime = FakeRuntime()

    assert agg.run(Args(), runtime, config=_config(tmp_path)) == 0

    assert "PACKAGE plat-19m" in str(runtime.output.last)


def test_the_header_flag_prints_the_header_text(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m-acceptkit",)), _run_set(tmp_path, ())))
    runtime = FakeRuntime()

    assert agg.run(Args(header=True), runtime, config=_config(tmp_path)) == 0

    assert str(runtime.output.last).startswith("MERGE 1: plat-19m-acceptkit")


def test_the_header_refuses_a_json_request_rather_than_printing_two_answers(monkeypatch, tmp_path):
    _patched(monkeypatch, _view(_git(tmp_path, ("plat-19m",)), _run_set(tmp_path, ())))

    with pytest.raises(ValueError, match="cannot be combined with --json"):
        agg.run(Args(header=True), FakeRuntime(json_mode=True), config=_config(tmp_path))


def test_read_view_takes_the_clock_it_is_given(tmp_path, monkeypatch):
    # `now` is injectable so an age assertion is decidable; without it the clock is time.time().
    monkeypatch.setattr(gitstate, "discover_root", lambda start: (str(tmp_path / "repo"), ""))
    monkeypatch.setattr(gitstate, "read_view", lambda root: _git(tmp_path, ()))
    monkeypatch.setattr(ag_sources, "read_proc_locks", lambda: ag_sources.LockTable((), True, ""))
    monkeypatch.setattr(ag_sources, "gate_slots", lambda config, locks: ())
    monkeypatch.setattr(ag_sources, "read_results", lambda config: ())
    monkeypatch.setattr(ag_sources, "read_peers", lambda config: ())
    monkeypatch.setattr(ag_sources, "read_own_handover", lambda config: ("/h", 10.0, ""))
    monkeypatch.setattr(ag_runs, "read", lambda config: _run_set(tmp_path, ()))

    view, detail = agg.read_view(_config(tmp_path), now=4000.0)

    assert detail == ""
    assert view.now == 4000.0


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


def test_truncation_leaves_a_block_that_fits_completely_alone():
    short = ["MERGE 0: none", "LEASES 0: none"]

    assert agg._truncate(short).splitlines() == short


def test_truncation_after_one_fitting_line_keeps_that_line_only():
    truncated = agg._truncate(["MERGE 0: none", "L" * 6000])

    assert truncated.splitlines() == ["MERGE 0: none", agg.HEADER_MARKER]


def test_truncation_keeps_whole_lines_and_marks_the_block():
    truncated = agg._truncate(["a" * 10, "b" * 5000])

    assert truncated.splitlines() == ["a" * 10, agg.HEADER_MARKER]
    assert len(truncated) <= agg.HEADER_LIMIT


def test_truncation_of_a_single_over_long_line_cuts_it_and_marks_it():
    truncated = agg._truncate(["x" * 6000])

    assert truncated.endswith("\n" + agg.HEADER_MARKER)
    assert len(truncated) <= agg.HEADER_LIMIT


def test_a_live_runs_cell_without_a_context_figure_says_so(tmp_path):
    git = _git(tmp_path, ("p-x",))
    records = (_record("p-x-pi", source=str(tmp_path / "runs"), files=("live",), outcome="", tokens=None),)

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, records)))

    assert "p-x-pi active" in row.fields["runs"].detail
    assert "40k" not in row.fields["runs"].detail


def test_a_gate_result_with_no_slot_held_says_so(tmp_path):
    git = _git(tmp_path, ("cmru-x",))

    (row,) = agg.rows_from_inputs(_view(git, _run_set(tmp_path, ()), results=(_result(tmp_path),)))

    assert row.fields["gates"].value.startswith("tests PASS")
    assert "no gate slot held" in row.fields["gates"].detail
