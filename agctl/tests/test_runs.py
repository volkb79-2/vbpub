"""The legacy run records and the bounded reads — temp files only, never the live estate.

Every fixture here is written into `tmp_path`; the reader is driven with a real `Config` whose
directories all live there, so no test can see a real run, stream or prompt.
"""

from __future__ import annotations

from pathlib import Path

from agctl import bounded
from agctl import config as ag_config
from agctl import runs as ag_runs


def _config(tmp_path: Path, *extra: str) -> ag_config.Config:
    """A config whose runs directories are all under `tmp_path` (no live path is reachable)."""

    runs_dirs = tuple(tmp_path / name for name in ("runs", *extra))
    return ag_config.Config(
        home=tmp_path / "home",
        runs_dirs=runs_dirs,
        state_dirs=(),
        repo_state_dir=None,
        peers_dir=tmp_path / "peers",
        gate_lock_dir=tmp_path / "locks",
        remote_gate_out=tmp_path / "gates",
        toml_path=tmp_path / "agctl.toml",
        toml_state="absent",
        toml_legacy_dirs=(),
    )


# --- bounded reads -------------------------------------------------------------------------


def test_head_reads_a_small_file_and_refuses_an_absent_one(tmp_path):
    path = tmp_path / "small"
    path.write_text("EXIT=0 OUTCOME=SUCCESS\n", encoding="utf-8")

    assert bounded.head(path) == "EXIT=0 OUTCOME=SUCCESS\n"
    assert bounded.head(tmp_path / "absent") is None


def test_head_truncates_a_file_larger_than_its_limit(tmp_path):
    path = tmp_path / "big"
    path.write_bytes(b"x" * 100)

    assert bounded.head(path, 10) == "x" * 10


def test_tail_reads_the_end_and_drops_a_partial_first_line(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"first\nsecond\nthird\n")

    assert bounded.tail(path, 100) == "first\nsecond\nthird\n"
    # A window that starts inside "second" can only produce a false match, so that partial
    # line is dropped and the tail begins at the next line boundary.
    assert bounded.tail(path, 13) == "third\n"


def test_tail_reports_an_unreadable_file_as_absent(tmp_path):
    assert bounded.tail(tmp_path / "absent") is None


def test_walk_lists_a_directory_and_states_the_reason_it_cannot(tmp_path):
    directory = tmp_path / "runs"
    directory.mkdir()
    (directory / "a.done").write_text("EXIT=0\n", encoding="utf-8")

    entries, reason = bounded.walk(directory)

    assert [entry.name for entry in entries] == ["a.done"]
    assert reason == ""

    entries, reason = bounded.walk(tmp_path / "absent")

    assert entries == []
    assert "cannot read" in reason


def test_whole_reads_only_a_file_it_can_read_completely(tmp_path):
    path = tmp_path / "doc"
    path.write_text("{}", encoding="utf-8")

    assert bounded.whole(path, 10) == "{}"
    assert bounded.whole(path, 1) is None
    assert bounded.whole(tmp_path / "absent", 10) is None


def test_last_line_with_prefix_is_the_last_matching_line_not_a_tail_window(tmp_path):
    path = tmp_path / "lane.log"
    path.write_text(
        "run-gate: lane 'tests' verdict PASS; exit_code 0\n" + "noise\n" * 2000 + "tail noise\n",
        encoding="utf-8",
    )

    # The verdict sits far outside any single tail window; it is still the answer.
    assert bounded.last_line_with_prefix(path, "run-gate: lane '") == "run-gate: lane 'tests' verdict PASS; exit_code 0"
    # Small chunks prove the streaming path does not depend on the file fitting one block.
    assert bounded.last_line_with_prefix(path, "run-gate: lane '", chunk=16) == "run-gate: lane 'tests' verdict PASS; exit_code 0"
    assert bounded.last_line_with_prefix(tmp_path / "absent", "x") is None
    assert bounded.last_line_with_prefix(path, "nothing-matches") is None


def test_last_line_matching_takes_the_last_line_of_any_of_several_prefixes(tmp_path):
    path = tmp_path / "last.txt"
    path.write_text("READY run=a\nnoise\nYIELD run=b gauge=1k\nmore noise\n", encoding="utf-8")

    assert bounded.last_line_matching(path, ("READY run=", "YIELD run=")) == "YIELD run=b gauge=1k"
    assert bounded.last_line_matching(path, ("YIELD run=",)) == "YIELD run=b gauge=1k"
    assert bounded.last_line_matching(tmp_path / "absent", ("READY run=",)) is None


def test_last_line_with_prefix_sees_a_match_on_the_final_line_without_a_newline(tmp_path):
    path = tmp_path / "lane.log"
    path.write_text("noise\nrun-gate: lane 'tests' verdict FAIL", encoding="utf-8")

    assert bounded.last_line_with_prefix(path, "run-gate: lane '") == "run-gate: lane 'tests' verdict FAIL"


# --- the .done outcome rules -----------------------------------------------------------------


def test_done_reads_exit_and_outcome_from_the_first_line():
    assert ag_runs.parse_done("EXIT=0 OUTCOME=SUCCESS\n") == (0, "SUCCESS", "")
    assert ag_runs.parse_done("EXIT=3 AUTORESUME=0\n") == (3, "", "")


def test_a_review_token_wins_over_an_exit_zero():
    exit_code, outcome, review = ag_runs.parse_done("EXIT=0 OUTCOME=SUCCESS\nREVIEW-MODIFIED-TREE\n")

    assert exit_code == 0
    assert outcome == "REVIEW-MODIFIED-TREE"
    assert review == "REVIEW-MODIFIED-TREE"


def test_a_review_outcome_in_the_first_line_is_still_a_review():
    assert ag_runs.parse_done("EXIT=5 OUTCOME=REVIEW-BUDGET\n") == (5, "REVIEW-BUDGET", "")


def test_done_reporting_an_absent_file_shape_is_all_empty():
    assert ag_runs.parse_done(None) == (None, "", "")
    assert ag_runs.parse_done("") == (None, "", "")


def test_a_non_numeric_exit_is_unknown_rather_than_zero():
    assert ag_runs.parse_done("EXIT=x OUTCOME=ERROR\n") == (None, "ERROR", "")


# --- last.txt: the READY/YIELD position --------------------------------------------------------


def test_the_last_readiness_line_is_the_run_position():
    text = (
        "READY run=a-y1 mark=2026-11-25T04:23:02Z felt=\"x\" readback=OK\n"
        'YIELD run=a-y2 gauge=270k boundary="b" brief=/x next="n"\n'
    )

    assert ag_runs.parse_last(text) == ("YIELD", "a-y2", "270k", "b", "")


def test_a_yield_without_a_boundary_still_reads_as_a_yield():
    assert ag_runs.parse_last('YIELD run=r gauge=12k brief=/b next="n"\n') == ("YIELD", "r", "12k", "", "")


def test_a_readiness_line_carries_its_mark_and_no_gauge():
    assert ag_runs.parse_last("READY run=r mark=2026-11-25T04:23:02Z readback=OK\n") == (
        "READY", "r", "", "", "2026-11-25T04:23:02Z",
    )


def test_a_last_text_without_a_readiness_line_is_not_a_position():
    assert ag_runs.parse_last("VERDICT: FIX-FIRST\n") == ("", "", "", "", "")
    assert ag_runs.parse_last(None) == ("", "", "", "", "")


def test_a_readiness_line_with_no_run_value_is_a_stated_empty_position_not_a_crash():
    # `last.txt` is written from the agent's final message, so an agent that QUOTES the format
    # ("a line like `READY run=...`") produces exactly this. An IndexError here would take the
    # whole estate view down for one bad line among hundreds of records.
    assert ag_runs.parse_last("READY run=\n") == ("READY", "", "", "", "")
    assert ag_runs.parse_last("READY run=   \nMARK=later\n") == ("READY", "", "", "", "")
    assert ag_runs.parse_last("YIELD run=\n") == ("YIELD", "", "", "", "")
    assert ag_runs.parse_last("YIELD run=   gauge=12k\n") == ("YIELD", "", "12k", "", "")


# --- the live context ---------------------------------------------------------------------------


def test_live_reports_tokens_and_its_update_epoch():
    assert ag_runs.parse_live('{"latest_total_tokens": 53562, "updated_epoch": 1.5}') == (53562, 1.5)


def test_a_malformed_or_odd_live_document_is_unknown_not_zero():
    for text in (None, "not json", "[]", '{"latest_total_tokens": true}', '{"latest_total_tokens": "5"}'):
        assert ag_runs.parse_live(text) == (None, None)
    assert ag_runs.parse_live('{"latest_total_tokens": 5}') == (5, None)
    # An unreadable `updated_epoch` costs that field only: the token count that IS there is a
    # fact and is kept.
    assert ag_runs.parse_live('{"latest_total_tokens": 5, "updated_epoch": "x"}') == (5, None)


# --- the join key -------------------------------------------------------------------------------


def test_the_package_key_drops_the_stamp_and_the_chain_suffix_and_nothing_else():
    # Everything that survives is what tells two packages of one topic apart: the real estate
    # holds `core-ciu-r2fix`, `core-cmru-ki208` and `core-thrashguard-design` at the same time.
    assert ag_runs.package_key("plat-22c-resumebusy-20261125_031456") == "plat-22c-resumebusy"
    assert ag_runs.package_key("hostobs-20261124_183122-p11b-vm-demo") == "hostobs-p11b-vm-demo"
    assert ag_runs.package_key("core-ciu-r2fix") == "core-ciu-r2fix"
    assert ag_runs.package_key("edge-relgate-20261125") == "edge-relgate"
    assert ag_runs.package_key("plat-22c-pi-y2") == "plat-22c-pi"
    assert ag_runs.package_key("assay-p4-stall") == "assay-p4-stall"


def test_the_topic_is_the_first_token_of_a_package_key():
    assert ag_runs.topic_of("core-ciu-r2fix") == "core"
    assert ag_runs.topic_of("plat-22c-resumebusy") == "plat"
    assert ag_runs.topic_of("20261125T042108") == "20261125T042108"


def test_a_name_without_any_usable_token_falls_back_to_itself():
    assert ag_runs.package_key("20261125T042108") == "20261125T042108"


# --- chains -------------------------------------------------------------------------------------


def test_a_chain_is_the_base_plus_its_numbered_members():
    def record(run_id: str) -> ag_runs.RunRecord:
        return ag_runs.RunRecord(
            id=run_id, source="/runs", files=("done",), exit_code=0, outcome="SUCCESS", review="",
            route="luna", thread="", worktree="", tokens=None, live_mtime=None,
            last_state="", last_run="", gauge="", boundary="", mark="",
        )

    records = (record("p"), record("p-y1"), record("p-y2"), record("q"))

    chains = ag_runs.yield_chains(records)

    assert chains["p"] == ("p", "p-y1", "p-y2")
    # A base with no record of its own starts the chain at its first successor.
    assert chains["q"] == ("q",)


# --- the record set -----------------------------------------------------------------------------


def _write(tmp_path: Path, directory: str, name: str, text: str) -> None:
    target = tmp_path / directory
    target.mkdir(exist_ok=True)
    (target / name).write_text(text, encoding="utf-8")


def test_a_record_set_merges_the_extensions_of_one_run_name(tmp_path):
    _write(tmp_path, "runs", "p.done", "EXIT=0 OUTCOME=SUCCESS\n")
    _write(tmp_path, "runs", "p.route", "luna\n")
    _write(tmp_path, "runs", "p.thread", "01a1\n")
    _write(tmp_path, "runs", "p.wt", "/repo/.worktrees/p\n")
    _write(tmp_path, "runs", "p.live", '{"latest_total_tokens": 9000, "updated_epoch": 7}')
    _write(tmp_path, "runs", "p.last.txt", "READY run=p mark=2026-11-25T04:23:02Z readback=OK\n")

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record is not None
    assert record.files == ("done", "last.txt", "live", "route", "thread", "wt")
    assert record.outcome == "SUCCESS"
    assert record.exit_code == 0
    assert record.route == "luna" and record.thread == "01a1" and record.worktree == "/repo/.worktrees/p"
    assert record.tokens == 9000
    assert record.last_state == "READY" and record.mark == "2026-11-25T04:23:02Z"
    assert record.state() == ag_runs.ENDED
    assert record.path("done") is not None
    assert record.path("live") is not None
    assert record.path("jsonl") is None


def test_a_position_is_read_from_anywhere_in_last_txt_not_only_its_tail(tmp_path):
    # A `last.txt` can hold kilobytes of prose after the run's last READY/YIELD line. A tail
    # window would then report NO position, which is indistinguishable from a file that never
    # had one ("absence for emptiness"). The scan streams the whole file instead.
    _write(tmp_path, "runs", "p.last.txt", "READY run=p-y1 mark=2026-11-25T04:23:02Z\n" + "prose\n" * 3000)

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.last_state == "READY"
    assert record.mark == "2026-11-25T04:23:02Z"


def test_a_record_without_a_done_file_is_active(tmp_path):
    _write(tmp_path, "runs", "p.live", '{"latest_total_tokens": 1}')

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.state() == ag_runs.ACTIVE
    assert record.outcome == ""
    assert record.exit_code is None


def test_an_unlistable_runs_directory_costs_its_records_but_not_the_others(tmp_path, monkeypatch):
    import agctl.bounded as bounded_module

    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "other-runs").mkdir()
    (tmp_path / "other-runs" / "q.done").write_text("EXIT=0 OUTCOME=SUCCESS\n", encoding="utf-8")
    real_walk = bounded_module.walk

    def walk(path):
        if path.name == "runs":
            return [], f"cannot read {path}: Permission denied"
        return real_walk(path)

    monkeypatch.setattr(bounded_module, "walk", walk)

    set_ = ag_runs.read(_config(tmp_path, "other-runs"))

    assert [record.id for record in set_.records] == ["q"]
    assert set_.unreadable == ((str(tmp_path / "runs"), f"cannot read {tmp_path / 'runs'}: Permission denied"),)
    assert set_.readable == (str(tmp_path / "other-runs"),)


def test_a_directory_that_is_absent_is_not_a_readable_source(tmp_path):
    set_ = ag_runs.read(_config(tmp_path))

    assert set_.records == ()
    assert set_.readable == ()
    assert set_.unreadable == ()
    assert set_.dirs == (str(tmp_path / "runs"),)


def test_the_first_directory_wins_for_the_same_run_name(tmp_path):
    _write(tmp_path, "runs", "p.route", "luna\n")
    _write(tmp_path, "other-runs", "p.route", "deepseek\n")
    _write(tmp_path, "other-runs", "p.done", "EXIT=0 OUTCOME=SUCCESS\n")

    record = ag_runs.read(_config(tmp_path, "other-runs")).by_id("p")

    # `.route` comes from the first directory, `.done` from wherever it exists: each extension
    # resolves on its own, so an archived record does not hide a still-present one.
    assert record.route == "luna"
    assert record.outcome == "SUCCESS"
    assert record.path("done").parent == tmp_path / "other-runs"


def test_a_file_that_is_not_a_record_and_a_name_without_a_stem_are_ignored(tmp_path):
    _write(tmp_path, "runs", "p.jsonl", "stream\n")
    _write(tmp_path, "runs", ".done", "EXIT=0\n")
    (tmp_path / "runs" / "subdir").mkdir()

    set_ = ag_runs.read(_config(tmp_path))

    assert set_.records == ()
    assert set_.readable == (str(tmp_path / "runs"),)


def test_for_key_selects_the_records_of_one_topic(tmp_path):
    _write(tmp_path, "runs", "plat-22c-pi.done", "EXIT=0 OUTCOME=SUCCESS\n")
    _write(tmp_path, "runs", "plat-19m-pi.done", "EXIT=0 OUTCOME=SUCCESS\n")

    set_ = ag_runs.read(_config(tmp_path))

    assert [record.id for record in set_.for_key("plat-22c-pi")] == ["plat-22c-pi"]
    assert set_.for_key("nothing") == ()


# --- registry records (`runs/<id>/run.json`) -----------------------------------------------------


def _registry(directory: Path, **fields) -> None:
    import json

    directory.mkdir(parents=True, exist_ok=True)
    document = {"schema": "agctl-run/1", "id": directory.name}
    document.update(fields)
    (directory / "run.json").write_text(json.dumps(document), encoding="utf-8")


def test_a_registry_record_is_read_and_its_values_win_over_a_legacy_file(tmp_path):
    _write(tmp_path, "runs", "p.route", "legacy-route\n")
    _registry(tmp_path / "runs" / "p", route="luna", thread="01a1", worktree="/repo/wt", state="running", outcome=None, exit=None)

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.route == "luna"
    assert record.thread == "01a1"
    assert record.worktree == "/repo/wt"
    assert record.registry_state == "running"
    assert record.state() == ag_runs.ACTIVE
    assert "run.json" in record.files


def test_an_ended_registry_record_reports_ended_even_without_a_done_file(tmp_path):
    _registry(tmp_path / "runs" / "p", state="ended", outcome="SUCCESS", exit=0, route="luna")

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.state() == ag_runs.ENDED
    assert record.outcome == "SUCCESS"
    assert record.exit_code == 0


def test_a_registry_record_with_a_foreign_id_or_schema_is_ignored(tmp_path):
    import json

    directory = tmp_path / "runs" / "p"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"schema": "agctl-run/1", "id": "other"}), encoding="utf-8")
    other = tmp_path / "runs" / "q"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({"schema": "other/1", "id": "q"}), encoding="utf-8")

    assert ag_runs.read(_config(tmp_path)).records == ()


def test_an_unreadable_registry_record_is_ignored_not_guessed(tmp_path):
    directory = tmp_path / "runs" / "p"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text("{not json", encoding="utf-8")
    huge = tmp_path / "runs" / "q"
    huge.mkdir()
    (huge / "run.json").write_text("x" * (bounded.SMALL_LIMIT + 1), encoding="utf-8")

    assert ag_runs.parse_registry("{not json", "p") is None
    assert ag_runs.read(_config(tmp_path)).records == ()


def test_a_registry_state_outside_the_vocabulary_is_not_a_state(tmp_path):
    _registry(tmp_path / "runs" / "p", state="weird")

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.registry_state == ""
    # With no `.done` and no usable registry state, the record is active — an unknown state is
    # not silently read as either end of the vocabulary.
    assert record.state() == ag_runs.ACTIVE


def test_by_id_answers_none_for_a_name_that_is_not_there(tmp_path):
    _write(tmp_path, "runs", "p.done", "EXIT=0\n")

    assert ag_runs.read(_config(tmp_path)).by_id("absent") is None


def test_first_line_of_an_empty_document_is_empty():
    assert ag_runs._first_line(None) == ""
    assert ag_runs._first_line("") == ""
    assert ag_runs._first_line("\n\nsecond\n") == ""
    assert ag_runs._first_line("  luna  \n") == "luna"


def test_an_empty_route_file_leaves_the_route_blank(tmp_path):
    _write(tmp_path, "runs", "p.route", "\n\n")
    _write(tmp_path, "runs", "p.done", "EXIT=0 OUTCOME=SUCCESS\n")

    record = ag_runs.read(_config(tmp_path)).by_id("p")

    assert record.route == ""
    assert record.outcome == "SUCCESS"


def test_a_record_whose_live_file_disappeared_still_reads(tmp_path):
    _write(tmp_path, "runs", "p.live", "{}")

    record_paths = {**ag_runs.read(_config(tmp_path)).by_id("p").paths}
    missing = tmp_path / "runs" / "p.live"
    missing.unlink()

    # The stat of a file that vanished between the read and the stat is not a crash: the
    # record keeps its other fields and reports no live mtime.
    rebuilt = ag_runs._record("p", str(tmp_path / "runs"), record_paths, None)

    assert rebuilt.tokens is None
    assert rebuilt.live_mtime is None


def test_a_directory_entry_that_is_neither_file_nor_directory_is_ignored(tmp_path):
    import os

    target = tmp_path / "runs" / "gp.done"
    target.parent.mkdir(parents=True)
    os.symlink(tmp_path / "runs" / "nowhere", target)

    assert ag_runs.read(_config(tmp_path)).records == ()
