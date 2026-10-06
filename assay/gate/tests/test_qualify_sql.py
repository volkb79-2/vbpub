"""Tests for the self-contained SQL qualification harness (``gate/python/qualify_sql.py``, A-480, B126).

Nothing here starts a container or reads a clock: every docker call goes through
the module's one subprocess seam ``_run`` (stubbed), and the readiness wait
through ``_sleep`` (stubbed). The real-PostgreSQL evidence is the outer phase of
the registered ``tester-unified`` gate (``tools/tester-unified-gate.sh``); these
tests pin the pieces around it: the fixtures (T0-T2), the bucket derivation
(T3), the witness cross-check (T4), the container mechanics (T5), the
dstdns-free source tree (T6) and the gate wiring (T7).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from gate.tests.support import PROJECT_ROOT, requires_parent_repository
from gate.tests.test_distribution_gate import (
    HEX40,
    RECEIPT_RELATIVE,
    _git_commit,
    _host_environ,
    _rev,
    run_bash,
)

_MODULE_PATH = PROJECT_ROOT / "gate" / "python" / "qualify_sql.py"
_SPEC = importlib.util.spec_from_file_location("qualify_sql", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
q = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = q
_SPEC.loader.exec_module(q)

_FIXTURE_ROOT = PROJECT_ROOT / "gate" / "python" / "fixtures" / "sql"
_SCHEMA_PATH = PROJECT_ROOT / "tests" / "fixtures" / "mutation" / "sql" / "qualification" / "01-schema.sql"
_WITNESS_PATH = _FIXTURE_ROOT / "expected" / "sql-r2-witness.json"
_GATE_SCRIPT = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"


def _matrix() -> list[dict]:
    return json.loads((_FIXTURE_ROOT / "matrix.json").read_text(encoding="utf-8"))


def _schema_text() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def test_sql_witness_git_children_pin_automatic_maintenance(monkeypatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):
        captured.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    monkeypatch.setattr(q, "_run", fake_run)
    q._git(tmp_path, "status")
    q._git_commit(tmp_path, "fixture", env={})

    expected = (
        ("-c", "maintenance.auto=false"),
        ("-c", "maintenance.autoDetach=false"),
        ("-c", "gc.autoDetach=false"),
    )
    assert len(captured) == 2
    for argv in captured:
        pairs = tuple(zip(argv, argv[1:]))
        assert all(pair in pairs for pair in expected)


def test_sql_witness_git_boundary_overrides_repository_local_maintenance(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, timeout=30)
    keys = ("maintenance.auto", "maintenance.autoDetach", "gc.autoDetach")
    for key in keys:
        subprocess.run(["git", "-C", str(repo), "config", "--local", key, "true"], check=True, timeout=30)

    assert [q._git(repo, "config", "--get", key) for key in keys] == ["false", "false", "false"]


# =============================================================================
# T0 -- the pinned fixture bytes
# =============================================================================


def test_the_schema_and_the_probes_are_the_pinned_bytes() -> None:
    q.verify_fixture_hashes(_SCHEMA_PATH, _FIXTURE_ROOT)  # must not raise
    assert q.SCHEMA_SHA256.startswith("b7b07e97") and q.SCHEMA_SHA256.endswith("3af1")
    assert q.PROBES_SHA256.startswith("b5c4eb28") and q.PROBES_SHA256.endswith("1e10")


def test_one_extra_byte_in_the_schema_or_in_a_probe_fails_the_pin(tmp_path: Path) -> None:
    schema = tmp_path / "01-schema.sql"
    schema.write_bytes(_SCHEMA_PATH.read_bytes() + b"\n")
    with pytest.raises(q.QualificationError, match="schema sha256"):
        q.verify_fixture_hashes(schema, _FIXTURE_ROOT)
    root = tmp_path / "root"
    shutil.copytree(_FIXTURE_ROOT, root)
    with (root / "tests" / "K05.sql").open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(q.QualificationError, match="probes sha256"):
        q.verify_fixture_hashes(_SCHEMA_PATH, root)


# =============================================================================
# T1 -- the sites the shipped adapter finds are the matrix rows
# =============================================================================


def test_check_sites_maps_every_tag_to_its_row() -> None:
    sites = q.check_sites(_schema_text(), _matrix())
    assert sorted(sites) == [row["id"] for row in _matrix()]
    assert len(sites) == 24


def test_an_untagged_new_site_is_named_by_its_line() -> None:
    text = _schema_text().replace("  slot integer,\n", "  slot integer,\n  x integer NOT NULL,\n", 1)
    line = text.split("\n").index("  x integer NOT NULL,") + 1
    with pytest.raises(q.QualificationError, match=rf"line {line}: 1 site\(s\) but tags none \(untagged line\)"):
        q.check_sites(text, _matrix())


def test_a_tagged_line_without_its_site_names_the_row() -> None:
    text = _schema_text().replace("  code text UNIQUE, -- [K10]", "  code text, -- [K10]", 1)
    with pytest.raises(q.QualificationError) as excinfo:
        q.check_sites(text, _matrix())
    assert "line 14" in str(excinfo.value) and "K10" in str(excinfo.value)


def test_swapped_operators_in_the_matrix_name_both_rows() -> None:
    matrix = _matrix()
    by_id = {row["id"]: row for row in matrix}
    by_id["K06"]["operator"], by_id["K25"]["operator"] = by_id["K25"]["operator"], by_id["K06"]["operator"]
    with pytest.raises(q.QualificationError) as excinfo:
        q.check_sites(_schema_text(), matrix)
    assert "K06" in str(excinfo.value) and "K25" in str(excinfo.value)


def test_a_tag_absent_from_the_matrix_is_refused() -> None:
    with pytest.raises(q.QualificationError, match="K10: tagged in the schema but absent from the matrix"):
        q.check_sites(_schema_text(), [row for row in _matrix() if row["id"] != "K10"])


def test_the_trap_lines_carry_no_site() -> None:
    lines = {site.lineno for site in q.discover_sites(_schema_text())}
    assert not lines & {2, 3, 15, 29, 32, 50, 52, 74, 75, 76}
    assert len(q.discover_sites(_schema_text())) == 24


# =============================================================================
# T2 -- matrix and probe files agree
# =============================================================================


def _probe_problems(matrix: list[dict], tests_dir: Path) -> list[str]:
    killed = {row["id"] for row in matrix if row["expected"] == "killed"}
    present = {path.stem for path in tests_dir.glob("K*.sql")}
    return [f"missing {i}" for i in sorted(killed - present)] + [f"stray {i}" for i in sorted(present - killed)]


def test_matrix_ids_and_line_operator_pairs_are_unique() -> None:
    matrix = _matrix()
    assert len({row["id"] for row in matrix}) == len(matrix) == 24
    assert len({(row["line"], row["operator"]) for row in matrix}) == len(matrix)
    assert [row["id"] for row in matrix] == sorted(row["id"] for row in matrix)
    assert all(set(row) == {"id", "line", "operator", "expected"} for row in matrix)
    assert (_FIXTURE_ROOT / "matrix.json").read_text(encoding="utf-8") == json.dumps(matrix, indent=2) + "\n"


def test_schema_tags_and_matrix_ids_are_the_same_set() -> None:
    tags = set(re.findall(r"\[(K[0-9]{2})\]", _schema_text()))
    assert tags == {row["id"] for row in _matrix()}


def test_every_operator_has_a_killed_row() -> None:
    killed = {row["operator"] for row in _matrix() if row["expected"] == "killed"}
    assert killed == set(q.ALL_OPERATORS)


def test_expected_buckets_are_the_documented_ones() -> None:
    odd = {row["id"]: row["expected"] for row in _matrix() if row["expected"] != "killed"}
    assert odd == {"K02": "survived", "K25": "survived", "K09": "equivalent"}


def test_a_probe_file_exists_exactly_for_the_killed_rows() -> None:
    assert _probe_problems(_matrix(), _FIXTURE_ROOT / "tests") == []


def test_a_missing_or_a_stray_probe_is_reported(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    shutil.copytree(_FIXTURE_ROOT / "tests", tests)
    (tests / "K05.sql").unlink()
    assert _probe_problems(_matrix(), tests) == ["missing K05"]
    (tests / "K05.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tests / "K02.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert _probe_problems(_matrix(), tests) == ["stray K02"]


def test_every_probe_names_its_own_id_and_rolls_back() -> None:
    for path in sorted((_FIXTURE_ROOT / "tests").glob("K*.sql")):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("BEGIN;\n") and text.endswith("ROLLBACK;\n"), path.name
        assert f"RAISE EXCEPTION '{path.stem}'" in text, path.name


# =============================================================================
# T3 -- derivation
# =============================================================================


def _derive(row_id="K05", *, exit_code, dump, baseline="BASE", failed=()):
    return q.derive_bucket(row_id, exit_code=exit_code, dump=dump, baseline_dump=baseline, failed_ids=frozenset(failed))


def test_derive_bucket_takes_the_five_branches_in_order() -> None:
    assert _derive(exit_code=3, dump=None) == "crashed"
    assert _derive(exit_code=0, dump="BASE") == "equivalent"
    assert _derive(exit_code=0, dump="OTHER") == "survived"
    assert _derive(exit_code=1, dump="OTHER", failed={"K05", "K24"}) == "killed"
    with pytest.raises(q.QualificationError, match="K05: test failed without naming K05"):
        _derive(exit_code=1, dump="OTHER", failed={"K24"})


def test_an_equal_dump_is_equivalent_even_when_the_tests_failed() -> None:
    assert _derive(exit_code=1, dump="BASE", failed={"K05"}) == "equivalent"


def test_a_missing_dump_outranks_an_equal_one() -> None:
    assert _derive(exit_code=0, dump=None, baseline=None) == "crashed"


def test_a_bucket_other_than_expected_is_refused_and_crashed_never_passes() -> None:
    q._require_bucket("K05", "killed", "killed")  # must not raise
    with pytest.raises(q.QualificationError, match="K05: derived crashed, expected killed"):
        q._require_bucket("K05", "crashed", "killed")


def test_parse_failed_ids() -> None:
    assert q.parse_failed_ids(None) == frozenset()
    assert q.parse_failed_ids("schema test command failed (exit 1): ASSAY_SQL_FAILED=none\n") == frozenset()
    assert q.parse_failed_ids("schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,K24") == {"K05", "K24"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "schema test command failed (exit 0): ASSAY_SQL_FAILED=K05",
        "schema test command failed (exit 1): ASSAY_SQL_FAILED=K5",
        "schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,",
        "schema test command failed (exit 1): ASSAY_SQL_FAILED=K05\nextra",
        "psql exit 2",
    ],
)
def test_a_malformed_kill_signal_is_refused(text: str) -> None:
    with pytest.raises(q.QualificationError, match="malformed kill signal"):
        q.parse_failed_ids(text)


# =============================================================================
# T4 -- the witness verdict, row by row
# =============================================================================


def _verdict(matrix: list[dict] | None = None, place: dict[str, str] | None = None) -> dict:
    matrix = matrix or _matrix()
    where = {row["id"]: row["expected"] for row in matrix} | (place or {})
    mutation: dict[str, list] = {bucket: [] for bucket in q.BUCKETS}
    for row in matrix:
        entry = {"lineno": row["line"], "operator": row["operator"]}
        if where[row["id"]] == "killed":
            entry["kill_signal"] = f"schema test command failed (exit 1): ASSAY_SQL_FAILED={row['id']}"
        mutation[where[row["id"]]].append(entry)
    return {
        "outcome": "FAIL",
        "reason_code": "MUTANTS_SURVIVED",
        "claims": [
            {"rigor": "R0", "status": "PASS"},
            {"rigor": "R2", "status": "FAIL", "reason_code": "MUTANTS_SURVIVED", "mutation": mutation},
        ],
    }


def _entry_of(verdict: dict, row_id: str) -> tuple[str, dict]:
    row = next(row for row in _matrix() if row["id"] == row_id)
    for bucket, entries in verdict["claims"][1]["mutation"].items():
        for entry in entries:
            if (entry["lineno"], entry["operator"]) == (row["line"], row["operator"]):
                return bucket, entry
    raise AssertionError(row_id)


def test_a_built_verdict_is_accepted() -> None:
    q.cross_check(_verdict(), _matrix())
    q.check_witness_verdict(_verdict(), _matrix())


def test_a_bucket_mismatch_is_refused_per_row() -> None:
    with pytest.raises(q.QualificationError, match=r"K03: in \['survived'\]"):
        q.cross_check(_verdict(place={"K03": "survived"}), _matrix())


def test_k05_reported_as_survived_is_refused() -> None:
    with pytest.raises(q.QualificationError, match="K05"):
        q.cross_check(_verdict(place={"K05": "survived"}), _matrix())


def test_a_killed_entry_whose_signal_lacks_its_id_is_refused() -> None:
    verdict = _verdict()
    _entry_of(verdict, "K05")[1]["kill_signal"] = "schema test command failed (exit 1): ASSAY_SQL_FAILED=K24"
    with pytest.raises(q.QualificationError, match="K05: killed but its kill signal does not name K05"):
        q.cross_check(verdict, _matrix())


def test_swapping_k01_and_k02_is_refused_although_the_bucket_counts_agree() -> None:
    verdict = _verdict(place={"K01": "survived", "K02": "killed"})
    counts = {bucket: len(entries) for bucket, entries in verdict["claims"][1]["mutation"].items()}
    assert counts == {bucket: len(entries) for bucket, entries in _verdict()["claims"][1]["mutation"].items()}
    with pytest.raises(q.QualificationError) as excinfo:
        q.cross_check(verdict, _matrix())
    assert "K01" in str(excinfo.value) and "K02" in str(excinfo.value)


def test_an_unmatched_entry_is_refused() -> None:
    verdict = _verdict()
    verdict["claims"][1]["mutation"]["survived"].append({"lineno": 999, "operator": "sql:drop-check"})
    with pytest.raises(q.QualificationError, match=r"unmatched survived entry at \(999, sql:drop-check\)"):
        q.cross_check(verdict, _matrix())


def test_a_hung_entry_is_inconclusive_not_a_failure() -> None:
    verdict = _verdict(place={"K02": "hung"})
    with pytest.raises(q.QualificationError):
        q.cross_check(verdict, _matrix())
    with pytest.raises(q.InconclusiveError, match="witness incomplete: FAIL/MUTANTS_SURVIVED"):
        q.check_witness_verdict(verdict, _matrix())


def test_an_incomplete_witness_exits_3_before_any_shape_check(monkeypatch, tmp_path, capsys) -> None:
    verdict = _verdict(place={"K02": "budget_exceeded"})
    verdict["outcome"] = "BUDGET_EXCEEDED"
    verdict["reason_code"] = "LANE_TIMEOUT"
    verdict["claims"][1]["reason_code"] = "LANE_TIMEOUT"

    def fake_run_qualification(**_kwargs):
        q.check_witness_verdict(verdict, _matrix())

    monkeypatch.setattr(q, "run_qualification", fake_run_qualification)
    code = q.main(
        ["--scratch", str(tmp_path / "s"), "--container-name", "run-gate-assay-sql-1-2", "--ownership-token", _OWNER_TOKEN, "--cgroup-parent", "x.slice"]
    )
    assert code == 3
    assert "ASSAY_SQL_INCONCLUSIVE=witness incomplete: BUDGET_EXCEEDED/LANE_TIMEOUT" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["LANE_TIMEOUT", "CANDIDATE_HUNG"])
def test_a_claim_with_a_timeout_reason_is_incomplete(reason: str) -> None:
    verdict = _verdict()
    verdict["claims"][0]["reason_code"] = reason
    with pytest.raises(q.InconclusiveError, match=f"witness incomplete: FAIL/{reason}"):
        q.check_witness_verdict(verdict, _matrix())


def test_completeness_is_checked_before_the_fail_requirement() -> None:
    verdict = _verdict(place={"K02": "hung"})
    verdict["outcome"] = "PASS"  # would be a QualificationError if the shape were checked first
    with pytest.raises(q.InconclusiveError):
        q.check_witness_verdict(verdict, _matrix())


def test_r0_and_the_outcome_are_required_once_the_witness_is_complete() -> None:
    verdict = _verdict()
    verdict["claims"][0]["status"] = "FAIL"
    with pytest.raises(q.QualificationError, match="R0 claim"):
        q.check_witness_verdict(verdict, _matrix())
    verdict = _verdict()
    verdict["outcome"] = "PASS"
    with pytest.raises(q.QualificationError, match="expected FAIL/MUTANTS_SURVIVED"):
        q.check_witness_verdict(verdict, _matrix())


def test_exactly_one_r2_claim_is_required() -> None:
    verdict = _verdict()
    verdict["claims"] = verdict["claims"][:1]
    with pytest.raises(q.QualificationError, match="exactly one R2 claim"):
        q.check_witness_verdict(verdict, _matrix())


# =============================================================================
# normalize_verdict / compare_with_witness / commit pin
# =============================================================================


def _minimal_verdict(**overrides) -> dict:
    document = {
        "assay_version": "9.9.9",
        "commit": "1" * 40,
        "started": "2026-08-18T00:00:00+00:00",
        "ended": "2026-08-18T00:01:00+00:00",
        "judgment": {"resolved": {"base": "2" * 40}},
        "outcome": "PASS",
    }
    document.update(overrides)
    return document


def test_normalize_verdict_replaces_the_four_placeholder_fields() -> None:
    normalized = q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)
    assert normalized["assay_version"] == "@ASSAY_VERSION@"
    assert normalized["commit"] == "@HEAD_OID@"
    assert normalized["started"] == "@STARTED@"
    assert normalized["ended"] == "@ENDED@"
    assert normalized["judgment"]["resolved"]["base"] == "@BASE_OID@"


def test_normalize_verdict_discards_the_run_derived_candidate_budget() -> None:
    document = _minimal_verdict(
        judgment={"resolved": {"base": "2" * 40}, "r2": {"budget_per_candidate_derived_s": 63.089135}}
    )
    normalized = q.normalize_verdict(document, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)
    assert "budget_per_candidate_derived_s" not in normalized["judgment"]["r2"]


def test_normalize_verdict_refuses_a_wrong_assay_version() -> None:
    with pytest.raises(q.QualificationError, match="assay_version"):
        q.normalize_verdict(_minimal_verdict(), assay_version="0.0.1", head_oid="1" * 40, base_oid="2" * 40)


def test_normalize_verdict_refuses_a_wrong_commit() -> None:
    with pytest.raises(q.QualificationError, match="not the disposable HEAD"):
        q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="f" * 40, base_oid="2" * 40)


def test_normalize_verdict_refuses_a_wrong_base() -> None:
    with pytest.raises(q.QualificationError, match="resolved.base"):
        q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="1" * 40, base_oid="f" * 40)


@pytest.mark.parametrize("field", ["started", "ended"])
def test_normalize_verdict_refuses_an_empty_timestamp(field: str) -> None:
    with pytest.raises(q.QualificationError, match="nonempty timestamp"):
        q.normalize_verdict(
            _minimal_verdict(**{field: ""}), assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40
        )


_PLACEHOLDERS = {
    "@ASSAY_VERSION@": "9.9.9",
    "@HEAD_OID@": "1" * 40,
    "@BASE_OID@": "2" * 40,
    "@STARTED@": "2026-08-18T00:00:00+00:00",
    "@ENDED@": "2026-08-18T00:01:00+00:00",
}


def _witness_as_actual() -> dict:
    def replace(value):
        if isinstance(value, str) and value in _PLACEHOLDERS:
            return _PLACEHOLDERS[value]
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(json.loads(_WITNESS_PATH.read_text(encoding="utf-8")))


def test_compare_with_witness_accepts_the_committed_witness_round_tripped() -> None:
    q.compare_with_witness(
        _witness_as_actual(), _WITNESS_PATH, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40
    )  # must not raise


def test_compare_with_witness_ignores_absolute_resource_counters_but_keeps_the_delta() -> None:
    actual = _witness_as_actual()
    counters = actual["claims"][1]["mutation"]["killed"][0]["resource_limit_evidence"]["pids_events"]["max"]
    counters.update(before=37, after=37)
    memory = actual["claims"][1]["mutation"]["killed"][0]["resource_limit_evidence"]["memory_events"]["max"]
    memory.update(before=19, after=19)
    q.compare_with_witness(
        actual, _WITNESS_PATH, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40
    )

    counters.update(after=38, delta=1)
    with pytest.raises(q.QualificationError, match="differs from the frozen witness"):
        q.compare_with_witness(
            actual, _WITNESS_PATH, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40
        )


def test_normalize_verdict_refuses_noninteger_resource_counter_baselines() -> None:
    actual = _witness_as_actual()
    counters = actual["claims"][1]["mutation"]["killed"][0]["resource_limit_evidence"]["pids_events"]["max"]
    counters["before"] = "not-a-counter"
    with pytest.raises(q.QualificationError, match="resource-limit counters and delta"):
        q.normalize_verdict(actual, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)


@pytest.mark.parametrize(("before", "after", "delta"), [(37, 38, 0), (38, 37, 0), (37, 38, -1)])
def test_normalize_verdict_refuses_invalid_resource_counter_deltas(before: int, after: int, delta: int) -> None:
    actual = _witness_as_actual()
    counters = actual["claims"][1]["mutation"]["killed"][0]["resource_limit_evidence"]["pids_events"]["max"]
    counters.update(before=before, after=after, delta=delta)
    with pytest.raises(q.QualificationError, match="resource-limit"):
        q.normalize_verdict(actual, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)


def test_compare_with_witness_refuses_a_corrupted_mutation_bucket() -> None:
    actual = _witness_as_actual()
    actual["claims"][1]["mutation"]["killed"] = []
    with pytest.raises(q.QualificationError, match="differs from the frozen witness"):
        q.compare_with_witness(actual, _WITNESS_PATH, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)


def test_the_witness_is_the_current_schema_and_says_what_it_judged() -> None:
    document = json.loads(_WITNESS_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == q.VERDICT_SCHEMA_VERSION
    assert document["judgment"]["r2"]["mode"] == "changed_lines"
    assert document["judgment"]["resolved"]["language"] == "sql"
    assert document["judgment"]["resolved"]["source_roots"] == ["db/schema"]
    assert document["outcome"] == "FAIL" and document["reason_code"] == "MUTANTS_SURVIVED"
    matrix = _matrix()
    mutation = document["claims"][1]["mutation"]
    q.cross_check(document, matrix)  # the committed witness itself satisfies the matrix, row by row
    assert mutation["total"] == 24


def test_require_witness_commit_matches_accepts_the_disposable_head() -> None:
    q._require_witness_commit_matches({"commit": "1" * 40}, "1" * 40)  # must not raise


def test_require_witness_commit_matches_refuses_a_wrong_commit() -> None:
    with pytest.raises(q.QualificationError, match="not the disposable HEAD"):
        q._require_witness_commit_matches({"commit": "f" * 40}, "1" * 40)


# =============================================================================
# _assay_argv / the witness lane and wrapper (pure string construction)
# =============================================================================


def test_assay_argv_bootstraps_sys_path_and_forwards_arguments() -> None:
    argv = q._assay_argv("/usr/bin/python3", "run", "lane-name")
    assert argv[0] == "/usr/bin/python3" and argv[1] == "-c"
    assert str(q._SRC_ROOT) in argv
    assert argv[-2:] == ["run", "lane-name"]


def test_assay_argv_bootstrap_script_is_syntactically_valid_python() -> None:
    compile(q._assay_argv(sys.executable)[2], "<bootstrap>", "exec")  # must not raise


def _wrapper(**overrides) -> str:
    args = {"container_name": "my-container", "dbname": "witness", "restrict_key": "thekey"} | overrides
    return q._witness_wrapper_script(**args)


def test_witness_wrapper_script_is_valid_posix_sh() -> None:
    proc = subprocess.run(["sh", "-n", "-c", _wrapper()], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_witness_wrapper_script_interpolates_container_dbname_and_key() -> None:
    script = _wrapper()
    assert "my-container" in script
    assert "SCHEMA_GATE_DBNAME=witness" in script
    assert "SCHEMA_GATE_RESTRICT_KEY=thekey" in script
    assert "sh /schema-gate.sh" in script
    assert "'SCHEMA_GATE_TEST_CMD=sh /run-assertions.sh'" in script


def test_witness_wrapper_script_copies_the_probes_under_a_fresh_name_and_moves_them() -> None:
    """A plain ``docker cp db/tests NAME:/tests`` nests as ``/tests/tests`` and
    silently runs the fixture-root probes."""
    script = _wrapper()
    assert "rm -rf /corpus /corpus_new /tests /tests_new" in script
    assert "docker cp db/tests my-container:/tests_new\n" in script
    assert "docker exec my-container mv /tests_new /tests\n" in script
    assert "docker cp db/tests my-container:/tests\n" not in script
    assert "SCHEMA_GATE_ASSERT_DIR=/tests" in script
    assert "docker cp db/schema my-container:/corpus_new\n" in script


def test_witness_wrapper_script_drops_its_database_just_before_exiting() -> None:
    script = _wrapper()
    assert script.rstrip("\n").splitlines()[-2:] == [
        "docker exec my-container psql -v ON_ERROR_STOP=1 -U postgres -c 'DROP DATABASE witness;'",
        "exit $rc",
    ]
    assert script.index("rc=$?") < script.index("DROP DATABASE witness;")


def test_the_witness_lane_renders_valid_toml_for_the_current_lane_schema() -> None:
    rendered = q._WITNESS_LANE_TEMPLATE.format(
        lane_schema=q.LANE_SCHEMA_VERSION, lane="sql_qualification", operators=list(q.ALL_OPERATORS)
    )
    document = tomllib.loads(rendered)
    lane = document["lanes"]["sql_qualification"]
    assert document["schema_version"] == q.LANE_SCHEMA_VERSION
    assert lane["judge"]["source_roots"] == ["db/schema"]
    assert lane["judge"]["mutation"]["max_mutants"] == 24 and lane["judge"]["mutation"]["jobs"] == 1
    assert lane["judge"]["mutation"]["operators"] == list(q.ALL_OPERATORS)
    assert lane["budget"] == "60m"
    assert lane["env_passthrough"] == []


def test_capture_witness_resumes_and_writes_progress_under_the_disposable_repo(tmp_path: Path, monkeypatch) -> None:
    real_run = q._run
    seen: list[str] = []

    def recording_run(argv, **kwargs):
        if any("from assay.cli import main" in arg for arg in argv):
            seen.extend(argv)
            repo = Path(kwargs["cwd"])
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            artifact = Path(argv[argv.index("--verdict-json") + 1])
            artifact.write_text(json.dumps({"commit": commit}), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 1, "", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(q, "_run", recording_run)
    q.capture_witness(
        container=q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN),
        schema_bytes=_SCHEMA_PATH.read_bytes(),
        fixture_root=_FIXTURE_ROOT,
        scratch=tmp_path / "witness",
    )

    assert "--resume" in seen
    progress = Path(seen[seen.index("--progress") + 1])
    lane_file = Path(seen[seen.index("--file") + 1])
    assert lane_file.parent == progress.parent.parent
    assert progress == lane_file.parent / ".assay" / "progress-sql_qualification.jsonl"


# =============================================================================
# the scripts
# =============================================================================


def _sh_syntax(path: Path) -> None:
    proc = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def _generated_wrapper_file(tmp_path: Path) -> Path:
    path = tmp_path / "witness-gate.sh"
    path.write_text(_wrapper(), encoding="utf-8")
    return path


def test_the_scripts_have_valid_posix_sh_syntax(tmp_path: Path) -> None:
    for path in (_FIXTURE_ROOT / "schema-gate.sh", _FIXTURE_ROOT / "run-assertions.sh", _generated_wrapper_file(tmp_path)):
        _sh_syntax(path)


def test_the_scripts_pass_shellcheck_when_available(tmp_path: Path) -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed in this environment")
    for path in (_FIXTURE_ROOT / "schema-gate.sh", _FIXTURE_ROOT / "run-assertions.sh", _generated_wrapper_file(tmp_path)):
        proc = subprocess.run([shellcheck, "-s", "sh", str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_gate_script_declares_the_documented_required_env_vars() -> None:
    source = (_FIXTURE_ROOT / "schema-gate.sh").read_text(encoding="utf-8")
    for var in (
        "SCHEMA_GATE_INIT_SCRIPTS_DIR",
        "SCHEMA_GATE_DBNAME",
        "SCHEMA_GATE_DUMP_PATH",
        "SCHEMA_GATE_KILL_SIGNAL_PATH",
        "SCHEMA_GATE_RESTRICT_KEY",
        "SCHEMA_GATE_TEST_CMD",
    ):
        assert var in source


def test_the_gate_script_header_names_no_consumer_revision_or_script() -> None:
    lines = (_FIXTURE_ROOT / "schema-gate.sh").read_text(encoding="utf-8").splitlines()
    header = "\n".join(line for line in lines if line.startswith("#"))
    assert re.search(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{8}\b", header) is None
    executable = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    assert not any("scripts/schema-gate.sh" in line for line in executable)
    assert "A-480" in header and "A-279" in header and "NB-6" in header


def test_the_gate_script_applies_numbered_files_without_a_marker_or_seed_exclusion() -> None:
    source = (_FIXTURE_ROOT / "schema-gate.sh").read_text(encoding="utf-8")
    assert '"$SCRIPT_DIR"/[0-9][0-9]-*.sql' in source
    assert "95-" not in source and "99-" not in source


def _fake_bin(tmp_path: Path, *, psql_rc: dict[str, int]) -> dict[str, str]:
    """PATH stubs: ``psql -f <probe>`` exits per probe id, ``pg_dump`` prints a fixed dump."""
    stub_dir = tmp_path / "fake-bin"
    stub_dir.mkdir()
    cases = "\n".join(f"  *{name}.sql) exit {rc} ;;" for name, rc in psql_rc.items())
    (stub_dir / "psql").write_text(
        '#!/bin/sh\nfor last; do :; done\ncase "$last" in\n' + cases + "\n  *) exit 0 ;;\nesac\n",
        encoding="utf-8",
    )
    (stub_dir / "pg_dump").write_text("#!/bin/sh\necho DUMP\n", encoding="utf-8")
    for name in ("psql", "pg_dump"):
        (stub_dir / name).chmod(0o755)
    return {**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"}


def _run_assertions(tmp_path: Path, psql_rc: dict[str, int]) -> subprocess.CompletedProcess[str]:
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("K01", "K05", "K24"):
        (tests / f"{name}.sql").write_text("SELECT 1;\n", encoding="utf-8")
    env = {**_fake_bin(tmp_path, psql_rc=psql_rc), "SCHEMA_GATE_DBNAME": "qual", "SCHEMA_GATE_ASSERT_DIR": str(tests)}
    return subprocess.run(
        ["sh", str(_FIXTURE_ROOT / "run-assertions.sh")], capture_output=True, text=True, env=env, timeout=60
    )


def test_run_assertions_names_every_failing_probe_in_id_order(tmp_path: Path) -> None:
    proc = _run_assertions(tmp_path, {"K05": 3, "K24": 3})
    assert proc.returncode == 1
    assert proc.stdout.splitlines() == ["ASSAY_SQL_FAILED=K05,K24"]


def test_run_assertions_is_silent_and_green_when_every_probe_passes(tmp_path: Path) -> None:
    proc = _run_assertions(tmp_path, {})
    assert (proc.returncode, proc.stdout) == (0, "")


def test_run_assertions_treats_any_other_psql_exit_as_infrastructure(tmp_path: Path) -> None:
    proc = _run_assertions(tmp_path, {"K05": 2})
    assert proc.returncode == 2
    assert "ASSAY_SQL_FAILED" not in proc.stdout
    assert "psql exit 2 on K05" in proc.stderr


def test_run_assertions_refuses_an_empty_probe_directory(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    env = {**_fake_bin(tmp_path, psql_rc={}), "SCHEMA_GATE_DBNAME": "qual", "SCHEMA_GATE_ASSERT_DIR": str(tmp_path / "tests")}
    proc = subprocess.run(
        ["sh", str(_FIXTURE_ROOT / "run-assertions.sh")], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 2 and "no probes" in proc.stderr


def _schema_gate(tmp_path: Path, psql_rc: dict[str, int]) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "01-a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (corpus / "90-b.txt").write_text("not sql\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("K05", "K24"):
        (tests / f"{name}.sql").write_text("SELECT 1;\n", encoding="utf-8")
    dump, kill = tmp_path / "dump.sql", tmp_path / "kill.txt"
    env = {
        **_fake_bin(tmp_path, psql_rc=psql_rc),
        "SCHEMA_GATE_INIT_SCRIPTS_DIR": str(corpus),
        "SCHEMA_GATE_DBNAME": "qual",
        "SCHEMA_GATE_DUMP_PATH": str(dump),
        "SCHEMA_GATE_KILL_SIGNAL_PATH": str(kill),
        "SCHEMA_GATE_RESTRICT_KEY": "k",
        "SCHEMA_GATE_TEST_CMD": f"sh {_FIXTURE_ROOT / 'run-assertions.sh'}",
        "SCHEMA_GATE_ASSERT_DIR": str(tests),
    }
    proc = subprocess.run(
        ["sh", str(_FIXTURE_ROOT / "schema-gate.sh")], capture_output=True, text=True, env=env, timeout=60
    )
    return proc, dump, kill


def test_a_failing_test_leaves_the_dump_and_a_kill_signal_naming_the_ids(tmp_path: Path) -> None:
    proc, dump, kill = _schema_gate(tmp_path, {"K05": 3, "K24": 3})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert dump.read_text(encoding="utf-8") == "DUMP\n"  # the dump landed before the test ran (A-279)
    assert kill.read_text(encoding="utf-8") == "schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,K24\n"
    assert "applying: 01-a.sql" in proc.stdout and "90-b" not in proc.stdout


def test_a_passing_test_writes_no_kill_signal(tmp_path: Path) -> None:
    proc, dump, kill = _schema_gate(tmp_path, {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert dump.is_file() and not kill.exists()


def test_an_infrastructure_failure_signals_none(tmp_path: Path) -> None:
    proc, _dump, kill = _schema_gate(tmp_path, {"K05": 2})
    assert proc.returncode == 2
    assert kill.read_text(encoding="utf-8") == "schema test command failed (exit 2): ASSAY_SQL_FAILED=none\n"


# =============================================================================
# _run / main argument handling
# =============================================================================


def test_run_raises_on_a_command_failure_when_check_is_requested() -> None:
    with pytest.raises(q.QualificationError, match="command failed"):
        q._run(["false"], check=True)


def test_run_does_not_raise_on_failure_when_check_is_not_requested() -> None:
    assert q._run(["false"], check=False).returncode != 0


def test_run_passes_env_through_when_given() -> None:
    proc = q._run(["sh", "-c", "echo $ASSAY_SQL_PROBE"], env=q._env_with({"ASSAY_SQL_PROBE": "hi"}))
    assert proc.stdout.strip() == "hi"


def _argv(tmp_path: Path, **overrides) -> list[str]:
    values = {
        "--scratch": str(tmp_path / "scratch"),
        "--container-name": "run-gate-assay-sql-123-456",
        "--cgroup-parent": "dev-gates.slice",
        "--ownership-token": _OWNER_TOKEN,
    } | overrides
    return [item for pair in values.items() for item in pair]


def test_main_refuses_a_pre_existing_scratch_directory(tmp_path: Path) -> None:
    (tmp_path / "scratch").mkdir()
    with pytest.raises(SystemExit) as excinfo:
        q.main(_argv(tmp_path))
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "filename", ["postgres.cid", "postgres.removed", "postgres.launch-attempted"]
)
def test_main_refuses_stale_postgres_ownership_files(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).write_text("a" * 64, encoding="ascii")
    with pytest.raises(SystemExit) as excinfo:
        q.main(_argv(tmp_path))
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"--container-name": "assay-sql-1-2"},
        {"--container-name": "run-gate-assay-sql-1-2-3"},
        {"--container-name": "run-gate-assay-sql-x-2"},
        {"--container-name": "run-gate-assay-sql-1-2\n"},
        {"--runner-name": "run-gate-assay-sql-123-456"},
        {"--ownership-token": "not-a-token"},
        {"--cgroup-parent": ""},
    ],
)
def test_main_refuses_a_bad_container_name_or_an_empty_slice(tmp_path: Path, overrides: dict) -> None:
    with pytest.raises(SystemExit) as excinfo:
        q.main(_argv(tmp_path, **overrides))
    assert excinfo.value.code == 2


def test_main_refuses_python_older_than_3_11(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 10, 9, "final", 0))
    with pytest.raises(SystemExit) as excinfo:
        q.main(_argv(tmp_path))
    assert excinfo.value.code == 2


def test_module_dispatches_to_main_when_run_as_a_script(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["qualify_sql.py"])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_MODULE_PATH), run_name="__main__")
    assert excinfo.value.code == 2  # argparse: the required options are missing


# =============================================================================
# T5 -- container mechanics, with `_run` and `_sleep` stubbed
# =============================================================================

_IMAGE = "postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2"
_NAME = "run-gate-assay-sql-123-456"
_FAKE_CONTAINER_ID = "a" * 64
_OWNER_TOKEN = "b" * 64
_FAKE_RUNNER_ID = "b" * 64
_AMBIGUOUS_POSTGRES_MARKER = "cidfile-ambiguous\n"

_DF_OUT = "Filesystem 1K-blocks Used Available Use% Mounted on\ntmpfs 262144 1024 261120 1% /var/lib/postgresql\n"


class FakeDocker:
    """A stand-in for ``_run`` that records every argv and answers like a healthy docker."""

    def __init__(
        self,
        *,
        ps: str = "",
        version_rc: int = 0,
        inspect_rc: int = 0,
        run_rc: int = 0,
        run_stderr: str = "",
        cidfile_on_run_failure: bool = False,
        empty_cidfile_on_run_failure: bool = False,
        ready_rc: int = 0,
        ps_rc: int = 0,
        rm_rc: int = 0,
        inspect_name: str | None = None,
        inspect_token: str | None = None,
        on_call=None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.ps_rc, self.rm_rc = ps_rc, rm_rc
        self.ps, self.version_rc, self.inspect_rc = ps, version_rc, inspect_rc
        self.run_rc, self.run_stderr, self.ready_rc = run_rc, run_stderr, ready_rc
        self.cidfile_on_run_failure = cidfile_on_run_failure
        self.empty_cidfile_on_run_failure = empty_cidfile_on_run_failure
        self.inspect_name = inspect_name or f"/{_NAME}"
        self.inspect_token = inspect_token or _OWNER_TOKEN
        self.on_call = on_call

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv)
        rc, out, err = 0, "", ""
        if argv[:2] == ["docker", "version"]:
            rc = self.version_rc
        elif argv[:3] == ["docker", "image", "inspect"]:
            rc = self.inspect_rc
        elif argv[:3] == ["docker", "inspect", "--format"]:
            rc, out = self.inspect_rc, f"{self.inspect_name}|{self.inspect_token}\n"
        elif argv[:2] == ["docker", "ps"]:
            rc, out = self.ps_rc, self.ps
        elif argv[:2] == ["docker", "rm"]:
            rc, err = self.rm_rc, "removal refused" if self.rm_rc else ""
        elif argv[:2] == ["docker", "run"]:
            rc, err = self.run_rc, self.run_stderr
            if rc == 0 or self.cidfile_on_run_failure:
                if "--cidfile" in argv:
                    Path(argv[argv.index("--cidfile") + 1]).write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
            elif self.empty_cidfile_on_run_failure and "--cidfile" in argv:
                Path(argv[argv.index("--cidfile") + 1]).write_text("", encoding="ascii")
            if rc == 0:
                out = f"{_FAKE_CONTAINER_ID}\n"
        elif argv[:3] == ["docker", "exec", _FAKE_CONTAINER_ID] and argv[3:4] == ["psql"] and "-h" in argv:
            rc, out = self.ready_rc, "1\n" if self.ready_rc == 0 else ""
        elif argv[:4] == ["docker", "exec", _FAKE_CONTAINER_ID, "df"]:
            out = _DF_OUT
        proc = subprocess.CompletedProcess(argv, rc, out, err)
        if kwargs.get("check", True) and rc:
            raise q.QualificationError(f"command failed ({rc}): {argv!r}")
        return proc

    def kinds(self) -> list[str]:
        return [" ".join(call[:3]) for call in self.calls]

    def ran(self) -> bool:
        return any(call[:2] == ["docker", "run"] for call in self.calls)


@pytest.fixture
def stubbed(monkeypatch):
    sleeps: list[int] = []
    monkeypatch.setattr(q, "_sleep", sleeps.append)
    monkeypatch.setattr(q.shutil, "which", lambda name: f"/usr/bin/{name}")

    def install(fake: FakeDocker) -> FakeDocker:
        monkeypatch.setattr(q, "_run", fake)
        return fake

    install.sleeps = sleeps
    return install


def _main_argv(tmp_path: Path) -> list[str]:
    return _argv(
        tmp_path,
        **{
            "--container-name": _NAME,
            "--runner-name": "run-gate-assay-sql-runner-123-456",
        },
    )


def _sentinel(*_args):
    raise AssertionError("main installed no SIGTERM handler")


def _assert_removed_last(fake: FakeDocker) -> None:
    assert fake.calls[-1] == ["docker", "rm", "-f", "-v", _FAKE_CONTAINER_ID], fake.calls[-3:]
    assert sum(1 for call in fake.calls if call[:2] == ["docker", "rm"]) == 1


def _run_main_under_sentinel(argv: list[str]) -> int | BaseException:
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sentinel)
    try:
        try:
            result: int | BaseException = q.main(argv)
        except SystemExit as exc:
            result = exc
        assert signal.getsignal(signal.SIGTERM) is _sentinel  # main restored what it found
        return result
    finally:
        signal.signal(signal.SIGTERM, original)


def test_docker_run_is_exactly_the_pinned_bounded_isolated_command(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker())
    _run_main_under_sentinel(_main_argv(tmp_path))  # fails at the baseline; the argv is what matters
    run = next(call for call in fake.calls if call[:2] == ["docker", "run"])
    assert run == [
        "docker", "run", "-d", "--pull=never", "--network", "none",
        "--label", f"assay.sql-gate.owner={_OWNER_TOKEN}",
        "--cidfile", str(tmp_path / "postgres.cid"), "--name", _NAME,
        "--cgroup-parent=dev-gates.slice", "--cpus", "1", "--memory", "512m", "--memory-swap", "512m",
        "--pids-limit", "256",
        "--mount", "type=tmpfs,destination=/var/lib/postgresql,tmpfs-size=268435456",
        "-e", "POSTGRES_HOST_AUTH_METHOD=trust", _IMAGE,
        "postgres", "-c", "max_wal_size=64MB", "-c", "min_wal_size=32MB",
    ]  # fmt: skip
    assert "--rm" not in run and "--pull=always" not in run


def test_the_image_is_inspected_by_digest_and_never_pulled(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker())
    _run_main_under_sentinel(_main_argv(tmp_path))
    assert ["docker", "image", "inspect", _IMAGE] in fake.calls
    assert "@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2" in _IMAGE
    assert not any(call[:2] == ["docker", "pull"] for call in fake.calls)


def test_the_fixture_files_are_copied_in_after_readiness(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker())
    _run_main_under_sentinel(_main_argv(tmp_path))
    copies = [call for call in fake.calls if call[:2] == ["docker", "cp"]][:3]
    assert [call[3] for call in copies] == [f"{_FAKE_CONTAINER_ID}:/schema-gate.sh", f"{_FAKE_CONTAINER_ID}:/run-assertions.sh", f"{_FAKE_CONTAINER_ID}:/tests"]
    ready = next(i for i, call in enumerate(fake.calls) if "-h" in call)
    assert fake.calls.index(copies[0]) > ready


def test_a_qualification_error_still_removes_the_container_last(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker())
    result = _run_main_under_sentinel(_main_argv(tmp_path))
    assert result == 1  # the stubbed baseline is not a clean apply
    _assert_removed_last(fake)
    assert fake.calls[-2][:4] == ["docker", "exec", _FAKE_CONTAINER_ID, "df"]


def test_the_readiness_failsafe_is_inconclusive_and_removes_the_container_last(tmp_path: Path, stubbed, capsys) -> None:
    fake = stubbed(FakeDocker(ready_rc=1))
    result = _run_main_under_sentinel(_main_argv(tmp_path))
    assert result == 3
    assert len(stubbed.sleeps) == 300 and set(stubbed.sleeps) == {1}
    assert "ASSAY_SQL_INCONCLUSIVE=readiness failsafe" in capsys.readouterr().err
    _assert_removed_last(fake)


def test_sigterm_becomes_exit_3_and_the_container_is_still_removed_last(tmp_path: Path, stubbed) -> None:
    def terminate(argv):
        if argv[:2] == ["docker", "cp"] and argv[-1].endswith(":/corpus_new"):
            signal.raise_signal(signal.SIGTERM)

    fake = stubbed(FakeDocker(on_call=terminate))
    result = _run_main_under_sentinel(_main_argv(tmp_path))
    assert isinstance(result, SystemExit) and result.code == 3
    _assert_removed_last(fake)


def test_main_installs_its_own_sigterm_handler_while_it_runs(tmp_path: Path, stubbed) -> None:
    seen: list[object] = []
    stubbed(FakeDocker(on_call=lambda argv: argv[:2] == ["docker", "run"] and seen.append(signal.getsignal(signal.SIGTERM))))
    _run_main_under_sentinel(_main_argv(tmp_path))
    assert seen and all(handler is not _sentinel and callable(handler) for handler in seen)


def test_a_failing_df_does_not_stop_the_removal(tmp_path: Path, stubbed, capsys) -> None:
    def hang(argv):
        if argv[:4] == ["docker", "exec", _FAKE_CONTAINER_ID, "df"]:
            raise subprocess.TimeoutExpired(argv, 30)

    fake = stubbed(FakeDocker(on_call=hang))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    _assert_removed_last(fake)
    assert "ASSAY_SQL_DF_FAILED=" in capsys.readouterr().err


def test_a_failing_removal_is_reported_and_the_handler_is_restored(tmp_path: Path, stubbed, capsys) -> None:
    def hang(argv):
        if argv[:3] == ["docker", "rm", "-f"]:
            raise OSError("docker went away")

    stubbed(FakeDocker(on_call=hang))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    assert "ASSAY_SQL_RM_FAILED=" in capsys.readouterr().err


def test_a_failed_owned_container_removal_cannot_exit_the_context_cleanly(stubbed, capsys) -> None:
    fake = stubbed(FakeDocker(rm_rc=1))
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._owned = True
    container._container_id = _FAKE_CONTAINER_ID

    with pytest.raises(q.QualificationError, match="could not remove PostgreSQL qualification container"):
        container.__exit__(None, None, None)

    assert fake.calls[-1] == ["docker", "rm", "-f", "-v", _FAKE_CONTAINER_ID]
    assert "ASSAY_SQL_RM_FAILED=exit 1" in capsys.readouterr().err


def test_owned_postgres_cleanup_targets_its_id_even_if_the_name_is_reused(tmp_path: Path, stubbed) -> None:
    ownership_file = tmp_path / "postgres.cid"
    ownership_file.write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
    fake = stubbed(FakeDocker())
    container = q.ThrowawayPostgres(
        _NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_file=ownership_file, ownership_token=_OWNER_TOKEN
    )
    container._owned = True

    assert container._remove() is None

    assert ["docker", "rm", "-f", "-v", _FAKE_CONTAINER_ID] in fake.calls
    assert not any(call == ["docker", "rm", "-f", "-v", _NAME] for call in fake.calls)
    assert fake.calls[-1] == ["docker", "rm", "-f", "-v", _FAKE_CONTAINER_ID]


def test_owned_postgres_cleanup_refuses_a_cidfile_for_an_unrelated_container(stubbed) -> None:
    fake = stubbed(FakeDocker(inspect_name="/unrelated-container"))
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._owned = True
    container._container_id = _FAKE_CONTAINER_ID

    error = container._remove()

    assert isinstance(error, q.QualificationError)
    assert "ownership id resolves to /unrelated-container|" in str(error)
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)
    assert not any(call[:3] == ["docker", "exec", _FAKE_CONTAINER_ID] for call in fake.calls)


def test_owned_postgres_cleanup_refuses_same_name_without_this_launch_token(stubbed) -> None:
    fake = stubbed(FakeDocker(inspect_token="c" * 64))
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._owned = True
    container._container_id = _FAKE_CONTAINER_ID

    error = container._remove()

    assert isinstance(error, q.QualificationError)
    assert f"expected /{_NAME}|{_OWNER_TOKEN}" in str(error)
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)


def test_the_removal_ignores_sigterm_and_restores_the_saved_handler(stubbed) -> None:
    during: list[object] = []

    def record(argv):
        if argv[:3] == ["docker", "rm", "-f"]:
            during.append(signal.getsignal(signal.SIGTERM))

    stubbed(FakeDocker(on_call=record))
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._owned = True
    container._container_id = _FAKE_CONTAINER_ID
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sentinel)
    try:
        container._remove()
        assert during == [signal.SIG_IGN]
        assert signal.getsignal(signal.SIGTERM) is _sentinel
    finally:
        signal.signal(signal.SIGTERM, original)


def test_name_conflict_with_a_candidate_cid_fails_closed_without_removing_an_unverified_container(
    tmp_path: Path, stubbed, capsys
) -> None:
    fake = stubbed(FakeDocker(
        run_rc=125,
        run_stderr=f'Conflict. The container name "/{_NAME}" is already in use',
        cidfile_on_run_failure=True,
    ))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    assert "ASSAY_SQL_FAILED:" in capsys.readouterr().err
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)
    assert not any(call[:3] == ["docker", "exec", _NAME] for call in fake.calls)
    assert (tmp_path / "postgres.launch-attempted").read_text(encoding="ascii") == _AMBIGUOUS_POSTGRES_MARKER


def test_a_name_conflict_with_an_empty_cidfile_clears_the_prelaunch_marker(
    tmp_path: Path, stubbed, capsys
) -> None:
    fake = stubbed(
        FakeDocker(
            run_rc=125,
            run_stderr=f'Conflict. The container name "/{_NAME}" is already in use',
            empty_cidfile_on_run_failure=True,
        )
    )

    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3

    assert "ASSAY_SQL_INCONCLUSIVE=container name in use" in capsys.readouterr().err
    assert not (tmp_path / "postgres.cid").exists()
    assert not (tmp_path / "postgres.launch-attempted").exists()
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)


@pytest.mark.parametrize(
    ("run_rc", "message"),
    [
        (125, 'Conflict. The container name "/different-name" is already in use'),
        (125, "Conflict. unrelated Docker resource conflict"),
        (1, f'Conflict. The container name "/{_NAME}" is already in use'),
    ],
)
def test_an_ambiguous_docker_conflict_keeps_postgres_launch_evidence(
    tmp_path: Path, stubbed, run_rc: int, message: str
) -> None:
    fake = stubbed(FakeDocker(run_rc=run_rc, run_stderr=message))

    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1

    assert (tmp_path / "postgres.launch-attempted").exists()
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)


def test_a_docker_run_failure_without_an_ownership_file_does_not_remove_by_name(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker(run_rc=125, run_stderr="no space left"))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    assert not any(call[:2] == ["docker", "rm"] for call in fake.calls)
    assert (tmp_path / "postgres.launch-attempted").exists(), "ambiguous launch failure keeps recovery evidence"


def test_a_docker_run_failure_with_an_ownership_file_retries_exact_cleanup(tmp_path: Path, stubbed) -> None:
    fake = stubbed(
        FakeDocker(run_rc=125, run_stderr="daemon response lost", cidfile_on_run_failure=True)
    )
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    _assert_removed_last(fake)


def test_an_absent_image_is_inconclusive_and_never_pulled(tmp_path: Path, stubbed, capsys) -> None:
    fake = stubbed(FakeDocker(inspect_rc=1))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert f"ASSAY_SQL_INCONCLUSIVE=image absent: docker pull {_IMAGE}" in capsys.readouterr().err
    assert not fake.ran() and not any(call[:2] == ["docker", "rm"] for call in fake.calls)


def test_a_missing_docker_binary_is_inconclusive(tmp_path: Path, stubbed, monkeypatch, capsys) -> None:
    fake = stubbed(FakeDocker())
    monkeypatch.setattr(q.shutil, "which", lambda name: None)
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert "ASSAY_SQL_INCONCLUSIVE=docker unavailable" in capsys.readouterr().err
    assert fake.calls == []


def test_a_failing_docker_version_is_inconclusive(tmp_path: Path, stubbed, capsys) -> None:
    fake = stubbed(FakeDocker(version_rc=1))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert "ASSAY_SQL_INCONCLUSIVE=docker unavailable" in capsys.readouterr().err
    assert not fake.ran()


def test_a_running_gate_container_makes_the_host_busy_and_nothing_starts(tmp_path: Path, stubbed, capsys) -> None:
    fake = stubbed(FakeDocker(ps="edge-traefik\nrun-gate-x\nrun-gate-y\n"))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert "ASSAY_SQL_INCONCLUSIVE=host busy — rerun: run-gate-x,run-gate-y" in capsys.readouterr().err
    assert not fake.ran() and not any(call[:2] == ["docker", "rm"] for call in fake.calls)
    ps = [call for call in fake.calls if call[:2] == ["docker", "ps"]]
    assert ps == [["docker", "ps", "--no-trunc", "--format", "{{.Names}}"]]


def test_the_qualification_runner_excludes_only_its_own_visible_container(
    tmp_path: Path, stubbed, capsys
) -> None:
    runner = "run-gate-assay-sql-runner-123-456"
    fake = stubbed(FakeDocker(ps=f"{runner}\n"))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1  # fake baseline has no SQL dump
    assert fake.ran()
    assert "ASSAY_SQL_INCONCLUSIVE=host busy" not in capsys.readouterr().err


def test_shared_host_lets_other_projects_gates_run_and_names_them_on_stderr(tmp_path: Path, stubbed, capsys) -> None:
    """CD50 (S1)."""
    fake = stubbed(FakeDocker(ps="run-gate-x\n"))
    _run_main_under_sentinel([*_main_argv(tmp_path), "--allow-shared-host"])
    assert fake.ran()
    captured = capsys.readouterr()
    assert "ASSAY_SQL_SHARED_HOST=run-gate-x" in captured.err.splitlines()
    assert "ASSAY_SQL_INCONCLUSIVE" not in captured.err


def test_shared_host_still_refuses_another_sql_qualification_container(tmp_path: Path, stubbed, capsys) -> None:
    """CD50 (S2): one W5 container at a time, opt-in or not."""
    fake = stubbed(FakeDocker(ps="run-gate-x\nrun-gate-assay-sql-9-1\n"))
    assert _run_main_under_sentinel([*_main_argv(tmp_path), "--allow-shared-host"]) == 3
    err = capsys.readouterr().err
    assert "ASSAY_SQL_INCONCLUSIVE=host busy — rerun: run-gate-assay-sql-9-1" in err
    assert "run-gate-x" not in err
    assert not fake.ran() and not any(call[:2] == ["docker", "rm"] for call in fake.calls)


def test_a_failing_docker_ps_is_inconclusive_and_nothing_starts(tmp_path: Path, stubbed, capsys) -> None:
    """W5C-1: a ``docker ps`` that fails cannot show the host is free (CD32), so it is exit 3, not a product FAIL."""
    fake = stubbed(FakeDocker(ps_rc=1))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert "ASSAY_SQL_INCONCLUSIVE=host check failed (docker ps)" in capsys.readouterr().err
    assert not fake.ran() and not any(call[:2] == ["docker", "rm"] for call in fake.calls)


def test_a_failed_container_removal_is_reported_on_stderr(tmp_path: Path, stubbed, capsys) -> None:
    """W5C-5: a non-zero ID-based ``docker rm -f -v`` is loud, never silent."""
    fake = stubbed(FakeDocker(rm_rc=1))
    _run_main_under_sentinel(_main_argv(tmp_path))  # fails at the baseline; the removal is what matters
    assert "ASSAY_SQL_RM_FAILED=exit 1: 'removal refused'" in capsys.readouterr().err
    assert [call for call in fake.calls if call[:2] == ["docker", "rm"]] == [["docker", "rm", "-f", "-v", _FAKE_CONTAINER_ID]]


def test_a_pin_mismatch_fails_before_any_container_at_run_time(tmp_path: Path, stubbed, monkeypatch, capsys) -> None:
    """W5C-3: the standalone CLI enforces the pinned schema/probe bytes, not only the pytest."""
    monkeypatch.setattr(q, "SCHEMA_SHA256", "0" * 64)
    fake = stubbed(FakeDocker())
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 1
    assert "schema sha256" in capsys.readouterr().err
    assert not fake.ran()


def _daemon_stub(monkeypatch, stderr: str) -> None:
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.setattr(q.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    "stderr",
    [
        "Error response from daemon: container 0123abcd is not running",
        "Error response from daemon: No such container: run-gate-assay-sql-1-2",
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
    ],
)
def test_a_lost_docker_environment_mid_run_is_inconclusive(monkeypatch, stderr: str) -> None:
    """W5C-6: container OOM-killed/removed or the daemon gone is a contention case, exit 3, not a FAIL."""
    _daemon_stub(monkeypatch, stderr)
    with pytest.raises(q.InconclusiveError, match="docker environment lost"):
        q._run(["docker", "exec", _NAME, "true"], check=True)


def test_a_lost_docker_environment_does_not_raise_when_check_is_not_requested(monkeypatch) -> None:
    """W5C-6: ``check=False`` (the removal path) must still return so ``docker rm`` is reached."""
    _daemon_stub(monkeypatch, "Error response from daemon: No such container: x")
    assert q._run(["docker", "rm", "-f", "-v", _NAME], check=False).returncode == 1


def test_an_ordinary_docker_failure_stays_a_qualification_error(monkeypatch) -> None:
    _daemon_stub(monkeypatch, "psql: error: syntax error")
    with pytest.raises(q.QualificationError, match="command failed"):
        q._run(["docker", "exec", _NAME, "psql"], check=True)


def test_other_containers_do_not_make_the_host_busy(tmp_path: Path, stubbed) -> None:
    fake = stubbed(FakeDocker(ps="edge-traefik\nnot-run-gate-x\n"))
    _run_main_under_sentinel(_main_argv(tmp_path))
    assert fake.ran()


def test_a_command_timeout_is_inconclusive(tmp_path: Path, stubbed, capsys) -> None:
    def hang(argv):
        if argv[:2] == ["docker", "run"]:
            Path(argv[argv.index("--cidfile") + 1]).write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
            raise subprocess.TimeoutExpired(argv, 180)

    fake = stubbed(FakeDocker(on_call=hang))
    assert _run_main_under_sentinel(_main_argv(tmp_path)) == 3
    assert "ASSAY_SQL_INCONCLUSIVE=command timed out" in capsys.readouterr().err
    _assert_removed_last(fake)


def test_wait_ready_raises_inconclusive_when_the_container_never_becomes_ready(stubbed) -> None:
    fake = stubbed(FakeDocker(ready_rc=1))
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._container_id = _FAKE_CONTAINER_ID
    with pytest.raises(q.InconclusiveError, match="never became ready after 2 attempts"):
        container._wait_ready(attempts=2)
    assert stubbed.sleeps == [1, 1] and len(fake.calls) == 2


def test_wait_ready_retries_until_a_real_query_answers(stubbed) -> None:
    answers = iter([1, 0])

    class Flaky(FakeDocker):
        def __call__(self, argv, **kwargs):
            self.ready_rc = next(answers) if "-h" in argv else 0
            return super().__call__(argv, **kwargs)

    fake = stubbed(Flaky())
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._container_id = _FAKE_CONTAINER_ID
    container._wait_ready(attempts=5)
    assert stubbed.sleeps == [1] and len(fake.calls) == 2


def test_remove_is_a_no_op_on_a_container_that_never_attempted_docker_run(stubbed) -> None:
    fake = stubbed(FakeDocker())
    container = q.ThrowawayPostgres(_NAME, "dev-gates.slice", _FIXTURE_ROOT, ownership_token=_OWNER_TOKEN)
    container._remove()
    assert fake.calls == []


# =============================================================================
# T6 -- no consumer checkout, script or revision is named in the tracked tree
# =============================================================================

#: Concatenated so this file does not contain the tokens it forbids.
_FORBIDDEN = (
    "/workspaces/" + "dstdns",
    "dstdns" + "-sql",
    "qualify_" + "dstdns",
    "dstdns" + "-21-create",
    "151cda" + "0d",
    "113154" + "e6",
    "820d4c" + "3c",
    "88de91" + "2d",
    "fc1a69" + "4d",
    "e18805" + "3a",
    "d4b394" + "ad",
    "84b043" + "f6",
)


def _forbidden_in(root: Path, relpaths: list[str]) -> list[str]:
    hits = []
    for relpath in relpaths:
        data = (root / relpath).read_bytes()
        hits += [f"{relpath}: {token}" for token in _FORBIDDEN if token.encode() in data]
    return hits


@requires_parent_repository
def test_no_forbidden_token_survives_in_the_tracked_tree() -> None:
    listing = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "-z", "--", "src", "tests", "gate", "tools"],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout.decode()
    paths = [p for p in listing.split("\0") if p and not p.startswith("tests/fixtures/verdicts/")]
    assert paths, "git ls-files listed nothing"
    assert _forbidden_in(PROJECT_ROOT, paths) == []


def test_the_sweep_finds_a_planted_token_in_a_shell_script(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "clean.sh").write_text("echo hello\n", encoding="utf-8")
    (tmp_path / "tools" / "planted.sh").write_text(f"cd {_FORBIDDEN[0]}\n", encoding="utf-8")
    assert _forbidden_in(tmp_path, ["tools/clean.sh", "tools/planted.sh"]) == [f"tools/planted.sh: {_FORBIDDEN[0]}"]


def test_a_bare_consumer_name_stays_legal(tmp_path: Path) -> None:
    (tmp_path / "note.py").write_text("# dstdns-shaped layouts\n", encoding="utf-8")
    assert _forbidden_in(tmp_path, ["note.py"]) == []


# =============================================================================
# T7 -- the gate wiring (real bash functions from the script, PATH stubs)
# =============================================================================

@pytest.fixture(scope="session")
def gate_functions(tmp_path_factory) -> Path:
    """The gate script's function definitions only (the entry-point dispatch is
    dropped, so sourcing never runs a mode). The venv path the build functions
    hard-code is left alone: none of the functions these tests call uses it."""
    marker = "# --- entry points"
    source = _GATE_SCRIPT.read_text(encoding="utf-8")
    assert marker in source
    socket_path = tmp_path_factory.mktemp("sql-gate-socket") / "docker.sock"
    test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    test_socket.bind(str(socket_path))
    test_socket.close()
    out = tmp_path_factory.mktemp("sql-gate-functions") / "gate-functions.sh"
    body = source.split(marker, 1)[0].replace(
        "docker_socket=/var/run/docker.sock", f"docker_socket={socket_path}"
    )
    out.write_text(body, encoding="utf-8")
    return out


def _sql_worktree(tmp_path: Path) -> tuple[Path, str, str]:
    worktree = tmp_path / "wt"
    harness = worktree / "assay" / "gate" / "python"
    harness.mkdir(parents=True)
    (harness / "qualify_sql.py").write_text("# qualification runner test fixture\n", encoding="utf-8")
    (worktree / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    _git_commit(worktree, "one", init=True)
    return worktree, _rev(worktree, "HEAD"), _rev(worktree, "HEAD^{tree}")


def _sql_env(
    tmp_path: Path,
    *,
    out: str,
    rc: int,
    fail_rm_prefix: str = "",
    hang_logs: bool = False,
    execute_runner: bool = False,
    run_launch_rc: int = 0,
    run_launch_cidfile_on_failure: bool = False,
    run_launch_empty_cidfile: bool = False,
    run_cidfile_id: str | None = None,
    run_launch_error: str | None = None,
    postgres_on_runner_remove: bool = False,
    postgres_launch_attempted: bool = False,
    postgres_launch_ambiguous: bool = False,
    inspect_name_override: str = "",
) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "stub-bin"
    fake_bin.mkdir()
    docker_log, harness_log = tmp_path / "docker.log", tmp_path / "harness.log"
    runner_out, runner_rc = tmp_path / "runner.out", tmp_path / "runner.rc"
    fake_python_log = tmp_path / "qualification-python.json"
    postgres_name = tmp_path / "postgres-name.txt"
    runner_name = tmp_path / "runner-name.txt"
    ownership_token = tmp_path / "ownership-token.txt"
    removed_postgres_id = tmp_path / "removed-postgres-id.txt"
    removed_runner_id = tmp_path / "removed-runner-id.txt"
    late_postgres_cidfile = tmp_path / "late-postgres-cidfile-path.txt"
    fake_python = fake_bin / "qualification-python"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        "argv = sys.argv[1:]\n"
        "pathlib.Path(os.environ['FAKE_PYTHON_LOG']).write_text(json.dumps(argv), encoding='utf-8')\n"
        "if len(argv) < 2 or argv[0] != '-I' or pathlib.Path(argv[1]).name != 'qualify_sql.py' or not pathlib.Path(argv[1]).is_file():\n"
        "    raise SystemExit(91)\n"
        "harness_args = argv[2:]\n"
        "scratch = pathlib.Path(harness_args[harness_args.index('--scratch') + 1])\n"
        "cid = os.environ['FAKE_CID']\n"
        "(scratch.parent / 'postgres.cid').write_text(cid + '\\n', encoding='ascii')\n"
        "removed = subprocess.run(['docker', 'rm', '-f', '-v', cid], capture_output=True, text=True)\n"
        "if removed.returncode != 0:\n"
        "    sys.stderr.write(removed.stderr)\n"
        "    raise SystemExit(1)\n"
        "sys.stdout.write('ASSAY_SQL_QUALIFIED=1\\n')\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    stub = fake_bin / "docker"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_STUB_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['run', '-d']:\n"
        "    launch_rc = int(os.environ.get('FAKE_RUN_LAUNCH_RC', '0'))\n"
        "    if launch_rc:\n"
        "        runner = args[args.index('--name') + 1]\n"
        "        if os.environ.get('FAKE_RUN_LAUNCH_CIDFILE_ON_FAILURE') == '1':\n"
        "            i = args.index('bash')\n"
        "            _label, worktree, scratch, commit, cgroup, postgres, runner, shared, owner_token = args[i + 3:]\n"
        "            pathlib.Path(os.environ['FAKE_POSTGRES_NAME_FILE']).write_text(postgres, encoding='ascii')\n"
        "            pathlib.Path(os.environ['FAKE_RUNNER_NAME_FILE']).write_text(runner, encoding='ascii')\n"
        "            token = args[args.index('--label') + 1].split('=', 1)[1]\n"
        "            pathlib.Path(os.environ['FAKE_OWNER_TOKEN_FILE']).write_text(token, encoding='ascii')\n"
        "            pathlib.Path(args[args.index('--cidfile') + 1]).write_text(os.environ['FAKE_RUNNER_CID'] + '\\n', encoding='ascii')\n"
        "            pathlib.Path(os.environ['FAKE_LATE_POSTGRES_CIDFILE_PATH']).write_text(str(pathlib.Path(scratch) / 'postgres.cid'), encoding='utf-8')\n"
        "        if os.environ.get('FAKE_RUN_LAUNCH_EMPTY_CIDFILE') == '1' and '--cidfile' in args:\n"
        "            pathlib.Path(args[args.index('--cidfile') + 1]).write_text('', encoding='ascii')\n"
        "        quote = chr(34)\n"
        "        conflict = os.environ.get('FAKE_RUN_LAUNCH_ERROR')\n"
        "        if conflict:\n"
        "            conflict = conflict.replace('{container_name}', runner)\n"
        "        else:\n"
        "            conflict = 'Conflict. The container name ' + quote + '/' + runner + quote + ' is already in use by container ' + quote + os.environ['FAKE_RUNNER_CID'] + quote + '.'\n"
        "        print(conflict, file=sys.stderr)\n"
        "        raise SystemExit(launch_rc)\n"
        "    i = args.index('bash')\n"
        "    _label, worktree, scratch, commit, cgroup, postgres, runner, shared, owner_token = args[i + 3:]\n"
        "    pathlib.Path(os.environ['FAKE_POSTGRES_NAME_FILE']).write_text(postgres, encoding='ascii')\n"
        "    pathlib.Path(os.environ['FAKE_RUNNER_NAME_FILE']).write_text(runner, encoding='ascii')\n"
        "    token = args[args.index('--label') + 1].split('=', 1)[1]\n"
        "    pathlib.Path(os.environ['FAKE_OWNER_TOKEN_FILE']).write_text(token, encoding='ascii')\n"
        "    pathlib.Path(os.environ['FAKE_SCRIPT']).write_text(args[i + 2], encoding='utf-8')\n"
        "    pathlib.Path(args[args.index('--cidfile') + 1]).write_text(os.environ.get('FAKE_RUNNER_CIDFILE', os.environ['FAKE_RUNNER_CID']) + '\\n', encoding='ascii')\n"
        "    with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as handle:\n"
        "        handle.write(' '.join(['--scratch', scratch + '/sql', '--container-name', postgres, '--runner-name', runner, '--ownership-token', token, '--cgroup-parent', cgroup] + (['--allow-shared-host'] if shared == '1' else [])) + '\\n')\n"
        "    if os.environ.get('FAKE_EXECUTE_RUNNER') == '1':\n"
        "        command = list(args[i:])\n"
        "        command[2] = command[2].replace('/opt/tester-venv/bin/python', os.environ['FAKE_PYTHON'])\n"
        "        proc = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy(), timeout=60)\n"
        "        pathlib.Path(os.environ['FAKE_RUNNER_OUT']).write_text(proc.stdout + proc.stderr, encoding='utf-8')\n"
        "        pathlib.Path(os.environ['FAKE_RUNNER_RC']).write_text(str(proc.returncode), encoding='ascii')\n"
        "    else:\n"
        "        if os.environ.get('FAKE_POSTGRES_LAUNCH_ATTEMPTED') == '1':\n"
        "            marker = pathlib.Path(scratch, 'postgres.launch-attempted')\n"
        "            if os.environ.get('FAKE_POSTGRES_LAUNCH_AMBIGUOUS') == '1':\n"
        f"                marker.write_text({_AMBIGUOUS_POSTGRES_MARKER!r}, encoding='ascii')\n"
        "            else:\n"
        "                marker.touch()\n"
        "        if os.environ['FAKE_RC'] != '3':\n"
        "            pathlib.Path(scratch, 'postgres.cid').write_text(os.environ['FAKE_CID'] + '\\n', encoding='ascii')\n"
        "            if os.environ['FAKE_RC'] == '0':\n"
        "                pathlib.Path(os.environ['FAKE_REMOVED_POSTGRES_ID']).write_text(os.environ['FAKE_CID'], encoding='ascii')\n"
        "        if os.environ['FAKE_RC'] == '0' and os.environ['FAKE_OUT'] == 'ASSAY_SQL_QUALIFIED=1\\n':\n"
        "            pathlib.Path(scratch, 'qualified.marker').write_text('ASSAY_SQL_QUALIFIED=1\\n', encoding='utf-8')\n"
        "    print(os.environ['FAKE_RUNNER_CID'])\n"
        "elif args[:2] == ['logs', '--follow']:\n"
        "    if os.environ.get('FAKE_HANG_LOGS') == '1':\n"
        "        import time; time.sleep(60)\n"
        "    output_path = pathlib.Path(os.environ['FAKE_RUNNER_OUT'])\n"
        "    sys.stdout.write(output_path.read_text(encoding='utf-8') if output_path.exists() else os.environ['FAKE_OUT'])\n"
        "elif args[:1] == ['wait']:\n"
        "    status_path = pathlib.Path(os.environ['FAKE_RUNNER_RC'])\n"
        "    print(status_path.read_text(encoding='ascii') if status_path.exists() else os.environ['FAKE_RC'])\n"
        "elif args[:1] == ['ps']:\n"
        "    if '--all' in args:\n"
        "        container_id = next(value[3:] for value in args if value.startswith('id='))\n"
        "        token = os.environ.get('FAKE_OWNER_TOKEN_OVERRIDE') or pathlib.Path(os.environ['FAKE_OWNER_TOKEN_FILE']).read_text(encoding='ascii')\n"
        "        if container_id == os.environ['FAKE_RUNNER_CID']:\n"
        "            removed = pathlib.Path(os.environ['FAKE_REMOVED_RUNNER_ID'])\n"
        "            if not removed.exists():\n"
        "                name = pathlib.Path(os.environ['FAKE_RUNNER_NAME_FILE']).read_text(encoding='ascii')\n"
        "                print(container_id + '|' + name + '|' + token)\n"
        "        elif container_id == os.environ['FAKE_CID']:\n"
        "            removed = pathlib.Path(os.environ['FAKE_REMOVED_POSTGRES_ID'])\n"
        "            if not removed.exists():\n"
        "                name = pathlib.Path(os.environ['FAKE_POSTGRES_NAME_FILE']).read_text(encoding='ascii')\n"
        "                name = os.environ.get('FAKE_INSPECT_NAME_OVERRIDE') or name\n"
        "                print(container_id + '|' + name.lstrip('/') + '|' + token)\n"
        "elif args[:3] == ['inspect', '--format', '{{.Name}}']:\n"
        "    expected = pathlib.Path(os.environ['FAKE_POSTGRES_NAME_FILE']).read_text(encoding='ascii')\n"
        "    print(os.environ.get('FAKE_INSPECT_NAME_OVERRIDE') or '/' + expected)\n"
        "elif args[:2] == ['rm', '-f']:\n"
        "    if len(args) == 3 and args[2] == os.environ['FAKE_RUNNER_CID'] and os.environ.get('FAKE_POSTGRES_ON_RUNNER_REMOVE') == '1':\n"
        "        cidfile = pathlib.Path(pathlib.Path(os.environ['FAKE_LATE_POSTGRES_CIDFILE_PATH']).read_text(encoding='utf-8'))\n"
        "        cidfile.write_text(os.environ['FAKE_CID'] + '\\n', encoding='ascii')\n"
        "    prefix = os.environ.get('FAKE_FAIL_RM_PREFIX', '')\n"
        "    if prefix and len(args) > 2 and args[-1].startswith(prefix):\n"
        "        raise SystemExit(1)\n"
        "    if len(args) == 3 and args[2] == os.environ['FAKE_RUNNER_CID']:\n"
        "        pathlib.Path(os.environ['FAKE_REMOVED_RUNNER_ID']).write_text(os.environ['FAKE_RUNNER_CID'], encoding='ascii')\n"
        "    if '-v' in args and len(args) > 3 and args[-1] == os.environ['FAKE_CID']:\n"
        "        pathlib.Path(os.environ['FAKE_REMOVED_POSTGRES_ID']).write_text(os.environ['FAKE_CID'], encoding='ascii')\n"
        "else:\n"
        "    raise SystemExit(90)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        **_host_environ(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_STUB_LOG": str(docker_log),
        "FAKE_LOG": str(harness_log),
        "FAKE_SCRIPT": str(harness_log.with_suffix(".sh")),
        "FAKE_PYTHON": str(fake_python),
        "FAKE_PYTHON_LOG": str(fake_python_log),
        "FAKE_POSTGRES_NAME_FILE": str(postgres_name),
        "FAKE_RUNNER_NAME_FILE": str(runner_name),
        "FAKE_OWNER_TOKEN_FILE": str(ownership_token),
        "FAKE_REMOVED_POSTGRES_ID": str(removed_postgres_id),
        "FAKE_REMOVED_RUNNER_ID": str(removed_runner_id),
        "FAKE_INSPECT_NAME_OVERRIDE": inspect_name_override,
        "FAKE_OWNER_TOKEN_OVERRIDE": "",
        "FAKE_RUNNER_OUT": str(runner_out),
        "FAKE_RUNNER_RC": str(runner_rc),
        "FAKE_RUNNER_CID": _FAKE_RUNNER_ID,
        "FAKE_RUNNER_CIDFILE": run_cidfile_id or _FAKE_RUNNER_ID,
        "FAKE_EXECUTE_RUNNER": "1" if execute_runner else "0",
        "FAKE_RUN_LAUNCH_RC": str(run_launch_rc),
        "FAKE_RUN_LAUNCH_CIDFILE_ON_FAILURE": "1" if run_launch_cidfile_on_failure else "0",
        "FAKE_RUN_LAUNCH_EMPTY_CIDFILE": "1" if run_launch_empty_cidfile else "0",
        "FAKE_POSTGRES_ON_RUNNER_REMOVE": "1" if postgres_on_runner_remove else "0",
        "FAKE_POSTGRES_LAUNCH_ATTEMPTED": "1" if postgres_launch_attempted else "0",
        "FAKE_POSTGRES_LAUNCH_AMBIGUOUS": "1" if postgres_launch_ambiguous else "0",
        "FAKE_LATE_POSTGRES_CIDFILE_PATH": str(late_postgres_cidfile),
        "FAKE_RUN_LAUNCH_ERROR": run_launch_error or "",
        "HOME": str(fake_home),
        "FAKE_OUT": out,
        "FAKE_RC": str(rc),
        "FAKE_CID": _FAKE_CONTAINER_ID,
        "FAKE_HANG_LOGS": "1" if hang_logs else "0",
        "FAKE_FAIL_RM_PREFIX": fail_rm_prefix,
    }
    return env, docker_log, harness_log


_MARKER = "ASSAY_SQL_QUALIFIED=1\n"


def _gate(
    tmp_path: Path,
    gate_functions: Path,
    worktree: Path,
    *,
    out: str,
    rc: int,
    tester: str = "echo stubbed-tester",
    fail_rm_prefix: str = "",
    execute_runner: bool = False,
    run_launch_rc: int = 0,
    run_launch_cidfile_on_failure: bool = False,
    run_launch_empty_cidfile: bool = False,
    run_cidfile_id: str | None = None,
    run_launch_error: str | None = None,
    postgres_on_runner_remove: bool = False,
    postgres_launch_attempted: bool = False,
    postgres_launch_ambiguous: bool = False,
    inspect_name_override: str = "",
):
    env, docker_log, harness_log = _sql_env(
        tmp_path,
        out=out,
        rc=rc,
        fail_rm_prefix=fail_rm_prefix,
        execute_runner=execute_runner,
        run_launch_rc=run_launch_rc,
        run_launch_cidfile_on_failure=run_launch_cidfile_on_failure,
        run_launch_empty_cidfile=run_launch_empty_cidfile,
        run_cidfile_id=run_cidfile_id,
        run_launch_error=run_launch_error,
        postgres_on_runner_remove=postgres_on_runner_remove,
        postgres_launch_attempted=postgres_launch_attempted,
        postgres_launch_ambiguous=postgres_launch_ambiguous,
        inspect_name_override=inspect_name_override,
    )
    proc = run_bash(
        f"run_registered_tester_container() {{ {tester}; }}\n"
        # This unit harness stubs the separate live B145 Docker acceptance probes.
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )
    return proc, docker_log, harness_log


def _harness_args(harness_log: Path) -> dict[str, str]:
    words = harness_log.read_text(encoding="utf-8").split()
    return dict(zip(words[::2], words[1::2]))


def _docker_calls(docker_log: Path) -> list[list[str]]:
    return [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]


def test_a_green_tester_then_a_green_harness_marks_the_phase_once_between_tester_and_finisher(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, docker_log, harness_log = _gate(
        tmp_path, gate_functions, worktree, out=_MARKER, rc=0, execute_runner=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = worktree / RECEIPT_RELATIVE
    lines = proc.stdout.splitlines()
    assert lines[0] == "stubbed-tester"
    assert lines[1].startswith("ASSAY_SQL_RUNNER_CONTAINER=run-gate-assay-sql-runner-")
    assert lines[2:5] == [
        "ASSAY_SQL_QUALIFIED=1",
        "ASSAY_GATE_PHASE=sql-qualified",
        f"ASSAY_REGISTERED_GATE_RECEIPT={receipt}",
    ]
    assert lines[5] == "ASSAY_REGISTERED_GATE_COMPLETE=1"
    args = _harness_args(harness_log)
    assert args["--cgroup-parent"] == "dev-gates.slice"
    assert re.fullmatch(r"[0-9a-f]{64}", args["--ownership-token"])
    assert re.fullmatch(r"run-gate-assay-sql-[0-9]+-[0-9]+", args["--container-name"])
    assert re.fullmatch(r"run-gate-assay-sql-runner-[0-9]+-[0-9]+", args["--runner-name"])
    assert not Path(args["--scratch"]).parent.exists()  # the scratch clone was removed
    calls = _docker_calls(docker_log)
    assert calls[0] == ["ps", "--no-trunc", "--format", "{{.Names}}"]
    launch = next(call for call in calls if call[:2] == ["run", "-d"])
    assert "--init" in launch and "--cgroupns=host" in launch
    assert launch[launch.index("--label") + 1] == f"assay.sql-gate.owner={args['--ownership-token']}"
    assert "--cgroup-parent=dev-gates.slice" in launch and "--network=none" in launch
    assert "--group-add" in launch and launch[launch.index("--group-add") + 1].isdigit()
    assert "--cidfile" in launch
    mounts = [launch[i + 1] for i, value in enumerate(launch[:-1]) if value == "--mount"]
    assert "type=bind,src=/host/vbpub,dst=/host/vbpub" in mounts
    assert "type=bind,src=/host/vbpub,dst=/workspaces/vbpub" in mounts
    socket_source = gate_functions.read_text(encoding="utf-8").split("docker_socket=", 1)[1].split()[0]
    assert f"type=bind,src={socket_source},dst=/var/run/docker.sock" in mounts
    assert not any(value == "-v" for value in launch)
    assert ["logs", "--follow", _FAKE_RUNNER_ID] in calls
    assert ["wait", _FAKE_RUNNER_ID] in calls
    postgres_remove = ["rm", "-f", "-v", _FAKE_CONTAINER_ID]
    assert postgres_remove in calls
    assert calls.index(postgres_remove) < calls.index(["logs", "--follow", _FAKE_RUNNER_ID])
    assert ["rm", "-f", _FAKE_RUNNER_ID] in calls
    assert calls[-1][:2] == ["ps", "--all"]
    assert f"id={_FAKE_CONTAINER_ID}" in calls[-1]
    python_invocation = json.loads((tmp_path / "qualification-python.json").read_text(encoding="utf-8"))
    scratch_root = Path(args["--scratch"]).parent
    assert python_invocation[:2] == [
        "-I",
        str(scratch_root / "clone" / "assay" / "gate" / "python" / "qualify_sql.py"),
    ]
    runner_script = harness_log.with_suffix(".sh")
    syntax = subprocess.run(["bash", "-n", str(runner_script)], capture_output=True, text=True, timeout=10)
    assert syntax.returncode == 0, syntax.stderr


def test_runner_id_cidfile_mismatch_preserves_scratch_after_verified_id_cleanup(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    planted_cidfile_id = "a" * 64
    proc, docker_log, _harness_log = _gate(
        tmp_path,
        gate_functions,
        worktree,
        out="",
        rc=1,
        run_cidfile_id=planted_cidfile_id,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SQL qualification runner cidfile does not match its returned ID" in proc.stderr
    assert "preserving SQL scratch" in proc.stderr
    assert "ASSAY_REGISTERED_GATE_COMPLETE=1" not in proc.stdout
    calls = _docker_calls(docker_log)
    assert calls.count(["rm", "-f", _FAKE_RUNNER_ID]) == 1
    assert ["rm", "-f", planted_cidfile_id] not in calls
    runner_launch = next(call for call in calls if call[:2] == ["run", "-d"])
    runner_cidfile = Path(runner_launch[runner_launch.index("--cidfile") + 1])
    assert runner_cidfile.read_text(encoding="ascii").strip() == planted_cidfile_id
    assert runner_cidfile.parent.exists(), "contradictory launch evidence must be retained"


def test_sql_runner_name_conflict_never_removes_the_existing_container(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, docker_log, harness_log = _gate(
        tmp_path,
        gate_functions,
        worktree,
        out="",
        rc=0,
        run_launch_rc=125,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_DIAGNOSTIC=sql-qualification-runner-name-conflict" in proc.stdout
    assert "ASSAY_GATE_INCONCLUSIVE=sql-qualification-runner-name-conflict" in proc.stderr
    assert "ASSAY_GATE_PHASE=sql-qualified" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT=" not in proc.stdout
    calls = _docker_calls(docker_log)
    assert any(call[:2] == ["run", "-d"] and "--cidfile" in call for call in calls)
    assert not any(call[:2] == ["rm", "-f"] for call in calls)
    runner_cidfile = next(call[call.index("--cidfile") + 1] for call in calls if "--cidfile" in call)
    assert not Path(runner_cidfile).parent.exists(), "a confirmed prelaunch conflict needs no recovery clone"


@pytest.mark.parametrize(
    ("launch_rc", "launch_error"),
    [
        (125, 'Conflict. The container name "/different-name" is already in use'),
        (125, "Conflict. unrelated Docker resource conflict"),
        (1, 'Conflict. The container name "/{container_name}" is already in use'),
    ],
)
def test_an_ambiguous_runner_conflict_preserves_the_sql_recovery_scratch(
    tmp_path: Path, gate_functions: Path, launch_rc: int, launch_error: str
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, docker_log, _harness_log = _gate(
        tmp_path,
        gate_functions,
        worktree,
        out="",
        rc=0,
        run_launch_rc=launch_rc,
        run_launch_error=launch_error,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "could not start the cgroup-visible SQL qualification container" in proc.stderr
    calls = _docker_calls(docker_log)
    assert not any(call[:2] == ["rm", "-f"] for call in calls)
    runner_cidfile = next(call[call.index("--cidfile") + 1] for call in calls if "--cidfile" in call)
    assert Path(runner_cidfile).parent.exists(), "an ambiguous failure must keep recovery evidence"


def test_empty_runner_cidfile_with_an_exact_name_conflict_is_inconclusive(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, docker_log, _harness_log = _gate(
        tmp_path,
        gate_functions,
        worktree,
        out="",
        rc=0,
        run_launch_rc=125,
        run_launch_empty_cidfile=True,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=sql-qualification-runner-name-conflict" in proc.stderr
    calls = _docker_calls(docker_log)
    runner_cidfile = next(call[call.index("--cidfile") + 1] for call in calls if "--cidfile" in call)
    assert not Path(runner_cidfile).parent.exists(), "empty ownership file proves the prelaunch refusal"


def test_accepted_runner_launch_failure_removes_postgres_created_during_runner_stop(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    env, docker_log, harness_log = _sql_env(
        tmp_path,
        out="",
        rc=1,
        run_launch_rc=125,
        run_launch_cidfile_on_failure=True,
        postgres_on_runner_remove=True,
    )
    proc = run_bash(
        f"run_registered_tester_container() {{ echo stubbed-tester; }}\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ASSAY_REGISTERED_GATE_COMPLETE=1" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT=" not in proc.stdout
    calls = _docker_calls(docker_log)
    stop_runner = ["rm", "-f", _FAKE_RUNNER_ID]
    remove_postgres = ["rm", "-f", "-v", _FAKE_CONTAINER_ID]
    assert stop_runner in calls
    assert remove_postgres in calls
    assert calls.index(stop_runner) < calls.index(remove_postgres)
    late_cidfile = Path(env["FAKE_LATE_POSTGRES_CIDFILE_PATH"]).read_text(encoding="utf-8")
    scratch_root = Path(late_cidfile).parent
    assert scratch_root.exists(), "a runner launch response with a cid must retain its evidence"
    assert (scratch_root / "runner.cid").read_text(encoding="ascii").strip() == _FAKE_RUNNER_ID
    assert not harness_log.exists()


def test_postgres_removal_failure_from_the_sql_runner_cannot_reach_receipt(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, docker_log, _harness_log = _gate(
        tmp_path,
        gate_functions,
        worktree,
        out="",
        rc=0,
        fail_rm_prefix=_FAKE_CONTAINER_ID[:1],
        execute_runner=True,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "ASSAY_GATE_PHASE=sql-qualified" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT=" not in proc.stdout
    calls = _docker_calls(docker_log)
    assert calls.count(["rm", "-f", "-v", _FAKE_CONTAINER_ID]) == 2
    assert ["rm", "-f", _FAKE_RUNNER_ID] in calls


def test_sql_runner_removal_failure_prevents_receipt_and_completion_marker(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    stale = worktree / RECEIPT_RELATIVE
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b'{"an earlier": "green receipt"}\n')
    env, docker_log, harness_log = _sql_env(
        tmp_path, out=_MARKER, rc=0, fail_rm_prefix=_FAKE_RUNNER_ID[:1]
    )
    proc = run_bash(
        f"run_registered_tester_container() {{ echo stubbed-tester; }}\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "could not remove SQL qualification container" in proc.stderr
    assert "ASSAY_REGISTERED_GATE_COMPLETE=1" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT=" not in proc.stdout
    assert not stale.exists()
    args = _harness_args(harness_log)
    calls = _docker_calls(docker_log)
    assert calls.count(["rm", "-f", _FAKE_RUNNER_ID]) == 2  # normal removal, then EXIT retry
    scratch_root = Path(args["--scratch"]).parent
    assert scratch_root.exists(), "preserve ownership records when runner removal fails"
    assert (scratch_root / "runner.cid").read_text(encoding="ascii").strip() == _FAKE_RUNNER_ID


def test_a_hung_sql_log_stream_is_bounded_and_exit_cleanup_still_removes_the_runner(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    env, docker_log, harness_log = _sql_env(tmp_path, out=_MARKER, rc=0, hang_logs=True)
    source = gate_functions.read_text(encoding="utf-8")
    production_wait = 'wait_for_container_log_follower "$_assay_sql_runner_logs_pid" 30'
    assert production_wait in source
    short_functions = tmp_path / "gate-functions-short-log-wait.sh"
    short_functions.write_text(source.replace(production_wait, production_wait[:-2] + "1"), encoding="utf-8")
    proc = run_bash(
        f"run_registered_tester_container() {{ echo stubbed-tester; }}\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=short_functions,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "could not collect SQL qualification logs (exit 124)" in proc.stderr
    assert "ASSAY_REGISTERED_GATE_COMPLETE=1" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT=" not in proc.stdout
    args = _harness_args(harness_log)
    calls = _docker_calls(docker_log)
    assert ["rm", "-f", _FAKE_RUNNER_ID] in calls
    assert not Path(args["--scratch"]).parent.exists()


def test_exit_cleanup_does_not_remove_a_container_without_this_run_cidfile(
    tmp_path: Path, gate_functions: Path
) -> None:
    env, docker_log, _harness_log = _sql_env(tmp_path, out="", rc=3)
    env["FAKE_OWNERSHIP_FILE"] = str(tmp_path / "postgres.cid")
    proc = run_bash(
        '_assay_sql_container_name="run-gate-assay-sql-123-456"\n'
        '_assay_sql_ownership_file="$FAKE_OWNERSHIP_FILE"\n'
        "trap cleanup_assay_gate_container EXIT\nexit 3",
        gate_functions=gate_functions,
        env=env,
        timeout=10,
    )

    assert proc.returncode == 3
    assert not docker_log.exists()


def test_exit_cleanup_refuses_a_planted_cid_for_an_unrelated_container(
    tmp_path: Path, gate_functions: Path
) -> None:
    env, docker_log, _harness_log = _sql_env(tmp_path, out="", rc=1)
    ownership_file = tmp_path / "postgres.cid"
    ownership_file.write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
    Path(env["FAKE_POSTGRES_NAME_FILE"]).write_text(
        "run-gate-assay-sql-123-456", encoding="ascii"
    )
    Path(env["FAKE_OWNER_TOKEN_FILE"]).write_text(_OWNER_TOKEN, encoding="ascii")
    env["FAKE_INSPECT_NAME_OVERRIDE"] = "/unrelated-container"
    env["FAKE_OWNERSHIP_FILE"] = str(ownership_file)
    scratch = tmp_path / "sql-scratch"
    scratch.mkdir()
    proc = run_bash(
        '_assay_sql_container_name="run-gate-assay-sql-123-456"\n'
        '_assay_sql_ownership_file="$FAKE_OWNERSHIP_FILE"\n'
        '_assay_sql_ownership_token="$_OWNER_TOKEN"\n'
        '_assay_sql_scratch="$FAKE_SCRATCH_PATH"\n'
        "trap cleanup_assay_gate_container EXIT\nexit 1",
        gate_functions=gate_functions,
        env={**env, "FAKE_SCRATCH_PATH": str(scratch), "_OWNER_TOKEN": _OWNER_TOKEN},
        timeout=10,
    )

    assert proc.returncode == 1
    assert "resolves to unexpected container" in proc.stderr
    assert f"{_FAKE_CONTAINER_ID}|unrelated-container|{_OWNER_TOKEN}" in proc.stderr
    assert not any(call[:2] == ["rm", "-f"] for call in _docker_calls(docker_log))
    assert scratch.exists(), "preserve scratch for ownership recovery"


def test_exit_cleanup_refuses_same_name_without_this_launch_token(
    tmp_path: Path, gate_functions: Path
) -> None:
    env, docker_log, _harness_log = _sql_env(tmp_path, out="", rc=1)
    ownership_file = tmp_path / "postgres.cid"
    ownership_file.write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
    Path(env["FAKE_POSTGRES_NAME_FILE"]).write_text(
        "run-gate-assay-sql-123-456", encoding="ascii"
    )
    Path(env["FAKE_OWNER_TOKEN_FILE"]).write_text(_OWNER_TOKEN, encoding="ascii")
    env["FAKE_OWNER_TOKEN_OVERRIDE"] = "c" * 64
    env["FAKE_OWNERSHIP_FILE"] = str(ownership_file)
    scratch = tmp_path / "sql-scratch"
    scratch.mkdir()
    proc = run_bash(
        '_assay_sql_container_name="run-gate-assay-sql-123-456"\n'
        '_assay_sql_ownership_file="$FAKE_OWNERSHIP_FILE"\n'
        '_assay_sql_ownership_token="$_OWNER_TOKEN"\n'
        '_assay_sql_scratch="$FAKE_SCRATCH_PATH"\n'
        "trap cleanup_assay_gate_container EXIT\nexit 1",
        gate_functions=gate_functions,
        env={**env, "FAKE_SCRATCH_PATH": str(scratch), "_OWNER_TOKEN": _OWNER_TOKEN},
        timeout=10,
    )

    assert proc.returncode == 1
    assert "resolves to unexpected container" in proc.stderr
    assert not any(call[:2] == ["rm", "-f"] for call in _docker_calls(docker_log))
    assert scratch.exists()


def test_exit_cleanup_refuses_a_planted_runner_cid_without_this_launch_token(
    tmp_path: Path, gate_functions: Path
) -> None:
    env, docker_log, _harness_log = _sql_env(tmp_path, out="", rc=1)
    runner_ownership_file = tmp_path / "runner.cid"
    runner_ownership_file.write_text(f"{_FAKE_RUNNER_ID}\n", encoding="ascii")
    Path(env["FAKE_RUNNER_NAME_FILE"]).write_text(
        "run-gate-assay-sql-runner-123-456", encoding="ascii"
    )
    Path(env["FAKE_OWNER_TOKEN_FILE"]).write_text(_OWNER_TOKEN, encoding="ascii")
    env["FAKE_OWNER_TOKEN_OVERRIDE"] = "c" * 64
    scratch = tmp_path / "sql-scratch"
    scratch.mkdir()
    proc = run_bash(
        '_assay_sql_runner_container_name="run-gate-assay-sql-runner-123-456"\n'
        '_assay_sql_runner_ownership_file="$FAKE_RUNNER_OWNERSHIP_FILE"\n'
        '_assay_sql_runner_launch_attempted=1\n'
        '_assay_sql_ownership_token="$_OWNER_TOKEN"\n'
        '_assay_sql_scratch="$FAKE_SCRATCH_PATH"\n'
        "trap cleanup_assay_gate_container EXIT\nexit 1",
        gate_functions=gate_functions,
        env={
            **env,
            "FAKE_RUNNER_OWNERSHIP_FILE": str(runner_ownership_file),
            "FAKE_SCRATCH_PATH": str(scratch),
            "_OWNER_TOKEN": _OWNER_TOKEN,
        },
        timeout=10,
    )

    assert proc.returncode == 1
    assert "resolves to unexpected container" in proc.stderr
    assert not any(call[:2] == ["rm", "-f"] for call in _docker_calls(docker_log))
    assert scratch.exists()


def test_exit_cleanup_does_not_trust_a_planted_postgres_removed_marker(
    tmp_path: Path, gate_functions: Path
) -> None:
    env, docker_log, _harness_log = _sql_env(tmp_path, out="", rc=1)
    scratch = tmp_path / "sql-scratch"
    scratch.mkdir()
    ownership_file = scratch / "postgres.cid"
    ownership_file.write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
    (scratch / "postgres.removed").write_text(f"{_FAKE_CONTAINER_ID}\n", encoding="ascii")
    Path(env["FAKE_POSTGRES_NAME_FILE"]).write_text(
        "run-gate-assay-sql-123-456", encoding="ascii"
    )
    Path(env["FAKE_OWNER_TOKEN_FILE"]).write_text(_OWNER_TOKEN, encoding="ascii")
    proc = run_bash(
        '_assay_sql_container_name="run-gate-assay-sql-123-456"\n'
        '_assay_sql_ownership_file="$FAKE_OWNERSHIP_FILE"\n'
        '_assay_sql_ownership_token="$_OWNER_TOKEN"\n'
        '_assay_sql_scratch="$FAKE_SCRATCH_PATH"\n'
        "trap cleanup_assay_gate_container EXIT\nexit 1",
        gate_functions=gate_functions,
        env={
            **env,
            "FAKE_OWNERSHIP_FILE": str(ownership_file),
            "FAKE_SCRATCH_PATH": str(scratch),
            "_OWNER_TOKEN": _OWNER_TOKEN,
        },
        timeout=10,
    )

    assert proc.returncode == 1
    calls = _docker_calls(docker_log)
    assert ["rm", "-f", "-v", _FAKE_CONTAINER_ID] in calls
    assert calls.index(["ps", "--all", "--no-trunc", "--filter", f"id={_FAKE_CONTAINER_ID}", "--format", '{{.ID}}|{{.Names}}|{{.Label "assay.sql-gate.owner"}}']) < calls.index(["rm", "-f", "-v", _FAKE_CONTAINER_ID])
    assert not scratch.exists()


def test_a_red_tester_never_reaches_the_sql_harness(tmp_path: Path, gate_functions: Path) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, _docker_log, harness_log = _gate(tmp_path, gate_functions, worktree, out=_MARKER, rc=0, tester="return 7")
    assert proc.returncode == 7, proc.stdout + proc.stderr
    assert not harness_log.exists()
    assert "ASSAY_GATE_PHASE" not in proc.stdout and "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout


def test_a_failing_harness_stops_the_gate_with_no_phase_marker_receipt_or_complete(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    stale = worktree / RECEIPT_RELATIVE
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b'{"an earlier": "green receipt"}\n')
    proc, docker_log, harness_log = _gate(tmp_path, gate_functions, worktree, out="", rc=1)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SQL qualification failed (exit 1)" in proc.stderr
    assert "ASSAY_GATE_PHASE" not in proc.stdout and "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
    assert not stale.exists()
    args = _harness_args(harness_log)
    calls = _docker_calls(docker_log)
    assert ["rm", "-f", "-v", _FAKE_CONTAINER_ID] in calls
    assert ["rm", "-f", _FAKE_RUNNER_ID] in calls
    assert not Path(args["--scratch"]).parent.exists()


def test_the_marker_with_a_non_zero_exit_is_still_a_failure(tmp_path: Path, gate_functions: Path) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, _docker_log, _harness_log = _gate(tmp_path, gate_functions, worktree, out=_MARKER, rc=1)
    assert proc.returncode == 1
    assert "ASSAY_GATE_PHASE" not in proc.stdout and not (worktree / RECEIPT_RELATIVE).exists()


def test_the_marker_plus_an_extra_line_is_not_the_exact_marker(tmp_path: Path, gate_functions: Path) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, _docker_log, _harness_log = _gate(tmp_path, gate_functions, worktree, out=_MARKER + "extra\n", rc=0)
    assert proc.returncode == 1
    assert "SQL qualification printed no exact marker" in proc.stderr
    assert "ASSAY_GATE_PHASE" not in proc.stdout and not (worktree / RECEIPT_RELATIVE).exists()


def test_no_marker_at_all_is_a_failure(tmp_path: Path, gate_functions: Path) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    proc, _docker_log, _harness_log = _gate(tmp_path, gate_functions, worktree, out="", rc=0)
    assert proc.returncode == 1
    assert "SQL qualification printed no exact marker" in proc.stderr


def test_an_inconclusive_harness_makes_the_gate_exit_3_with_no_receipt_and_no_complete(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    stale = worktree / RECEIPT_RELATIVE
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b'{"an earlier": "green receipt"}\n')
    proc, docker_log, harness_log = _gate(tmp_path, gate_functions, worktree, out="", rc=3)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive" in proc.stdout.splitlines()
    assert "ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun" in proc.stderr.splitlines()
    assert "ASSAY_GATE_PHASE" not in proc.stdout and "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
    assert not stale.exists() and "ASSAY_REGISTERED_GATE_RECEIPT" not in proc.stdout
    args = _harness_args(harness_log)
    calls = _docker_calls(docker_log)
    assert not any(call[:2] == ["rm", "-f"] and call[2] == "-v" for call in calls)
    assert ["rm", "-f", _FAKE_RUNNER_ID] in calls
    assert not Path(args["--scratch"]).parent.exists(), "a preflight refusal before PostgreSQL launch needs no recovery clone"


def test_inconclusive_after_a_postgres_launch_attempt_preserves_recovery_scratch(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    env, _docker_log, harness_log = _sql_env(
        tmp_path, out="", rc=3, postgres_launch_attempted=True
    )
    proc = run_bash(
        f"run_registered_tester_container() {{ echo stubbed-tester; }}\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "no PostgreSQL ownership ID; preserving scratch" in proc.stderr
    args = _harness_args(harness_log)
    scratch_root = Path(args["--scratch"]).parent
    assert (scratch_root / "postgres.launch-attempted").exists()
    assert scratch_root.exists(), "an ambiguous postlaunch result must retain recovery evidence"


def test_ambiguous_postgres_cid_evidence_survives_confirmed_container_cleanup(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    env, docker_log, harness_log = _sql_env(
        tmp_path,
        out="",
        rc=1,
        postgres_launch_attempted=True,
        postgres_launch_ambiguous=True,
    )
    proc = run_bash(
        f"run_registered_tester_container() {{ echo stubbed-tester; }}\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    calls = _docker_calls(docker_log)
    assert ["rm", "-f", "-v", _FAKE_CONTAINER_ID] in calls
    args = _harness_args(harness_log)
    scratch_root = Path(args["--scratch"]).parent
    assert (scratch_root / "postgres.cid").read_text(encoding="ascii").strip() == _FAKE_CONTAINER_ID
    assert (scratch_root / "postgres.launch-attempted").read_text(encoding="ascii") == _AMBIGUOUS_POSTGRES_MARKER
    assert scratch_root.exists(), "confirmed container cleanup must not erase contradictory launch evidence"


def test_a_clone_that_is_not_the_gated_commit_is_refused_before_any_harness_or_container(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, _commit, _tree = _sql_worktree(tmp_path)
    env, docker_log, harness_log = _sql_env(tmp_path, out=_MARKER, rc=0)
    proc = run_bash(
        f'worktree="{worktree}"\nhost_repo_root="/host/vbpub"\ntrap cleanup_assay_gate_container EXIT\nrun_sql_qualification "{HEX40}" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"SQL clone is not the gated commit {HEX40}" in proc.stderr
    assert not harness_log.exists()
    assert not docker_log.exists()  # no container name existed yet, so the trap removed nothing


def _run_sql_phase(tmp_path: Path, gate_functions: Path, allow: str | None):
    worktree, commit, _tree = _sql_worktree(tmp_path)
    env, _docker_log, harness_log = _sql_env(tmp_path, out=_MARKER, rc=0)
    env.pop("ASSAY_GATE_ALLOW_SHARED_HOST", None)
    if allow is not None:
        env["ASSAY_GATE_ALLOW_SHARED_HOST"] = allow
    proc = run_bash(
        f'worktree="{worktree}"\nhost_repo_root="/host/vbpub"\ntrap cleanup_assay_gate_container EXIT\nrun_sql_qualification "{commit}" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return harness_log.read_text(encoding="utf-8").split()


def test_the_shared_host_flag_reaches_the_harness_only_for_the_exact_value_one(
    tmp_path: Path, gate_functions: Path
) -> None:
    """CD50 (G4)."""
    assert "--allow-shared-host" in _run_sql_phase(tmp_path, gate_functions, "1")


@pytest.mark.parametrize("allow", [None, "", "true", "0"])
def test_the_shared_host_flag_is_absent_otherwise(tmp_path: Path, gate_functions: Path, allow: str | None) -> None:
    assert "--allow-shared-host" not in _run_sql_phase(tmp_path, gate_functions, allow)


def test_the_phase_and_its_state_live_above_the_entry_points_and_before_the_finisher() -> None:
    source = _GATE_SCRIPT.read_text(encoding="utf-8")
    assert source.index("run_sql_qualification() {") < source.index("# --- entry points")
    body = source.split("run_registered_gate() {", 1)[1].split("\n}\n", 1)[0]
    assert body.index("run_registered_tester_container") < body.index("run_sql_qualification") < body.index("finish_registered_gate")
    assert source.count("echo 'ASSAY_GATE_PHASE=sql-qualified'") == 1
    cleanup = source.split("cleanup_assay_gate_container() {", 1)[1].split("\n}\n", 1)[0]
    assert cleanup.index("trap - EXIT") < cleanup.index("_assay_sql_container_name") < cleanup.index("_assay_gate_logs_pid")
    assert "no PostgreSQL ownership ID; preserving scratch" in cleanup
    assert "remove_owned_sql_container" in cleanup
    inventory = source.split("container_inventory() {", 1)[1].split("sql_container_inventory() {", 1)[0]
    removal = source.split("remove_owned_sql_container() {", 1)[1].split("\n}\n", 1)[0]
    assert "docker ps --all --no-trunc" in inventory
    assert '"$inventory" != "$container_id|$expected_name|$ownership_token"' in removal
    assert 'docker rm -f -v "$container_id"' in removal
    sql = source.split("run_sql_qualification() {", 1)[1].split("# --- entry points", 1)[0]
    assert sql.index('remove_owned_sql_container "$container_id"') < sql.index("echo 'ASSAY_GATE_PHASE=sql-qualified'")
    assert 'wait_for_container_log_follower "$_assay_sql_runner_logs_pid" 30' in sql
