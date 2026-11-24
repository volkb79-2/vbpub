"""Pure-part controls for assay/tools/replay_pilot.py (no pytest subprocesses)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

from assay.adapters.python import PythonAdapter
from assay.mutation import (
    MAX_CANDIDATE_CEILING,
    MutationTarget,
    _progress_event,
    collect_mutation_sites,
)

TOOL = Path(__file__).resolve().parents[2] / "tools" / "replay_pilot.py"
SOURCE = b"def f(a, b):\n    return a < b\n"


def _load():
    spec = importlib.util.spec_from_file_location("replay_pilot_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


def _record(cid: str, test_file: str) -> dict:
    return {"candidate_id": cid, "first_failing_test": f"{test_file}::test_x"}


def _accept_all(record):
    return record["candidate_id"], None


def test_selection_round_robins_files_by_kill_count_and_is_deterministic():
    records = (
        [_record(f"a{i}", "tests/test_a.py") for i in range(5)]
        + [_record(f"b{i}", "tests/test_b.py") for i in range(3)]
        + [_record("c0", "tests/test_c.py")]
    )
    first, _ = tool.select_picks(records, 5, 0, _accept_all)
    again, _ = tool.select_picks(list(reversed(records)), 5, 0, _accept_all)
    files = [tool.witness_file(rec) for rec, _ in first]
    assert files == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py",
                     "tests/test_a.py", "tests/test_b.py"]
    assert [r["candidate_id"] for r, _ in first] == [r["candidate_id"] for r, _ in again]
    assert len({r["candidate_id"] for r, _ in first}) == 5


def test_selection_counts_skips_and_moves_on_to_the_next_candidate():
    records = [_record(f"a{i}", "tests/test_a.py") for i in range(3)]

    def accept(record):
        if record["candidate_id"] != "a1":
            return None, "no site"
        return b"ok", None

    picks, skips = tool.select_picks(records, 5, 0, accept)
    assert [rec["candidate_id"] for rec, _ in picks] == ["a1"]
    assert dict(skips) == {"no site": 2}


CMRU_OPERATORS = (  # cmru/assay.toml lane operators
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)


def _emitted_record() -> tuple[dict, bytes]:
    """A progress record produced by assay's own emitter, plus its mutant bytes."""
    text = SOURCE.decode()
    jobs = collect_mutation_sites(
        [MutationTarget(path="cmru/src/cmru/x.py", text=text, lines=frozenset({1, 2}))],
        adapter=PythonAdapter(),
        operators=CMRU_OPERATORS,
        limit=MAX_CANDIDATE_CEILING,
    )
    assert jobs and jobs != "UNSUPPORTED"
    record = _progress_event(candidate_index=0, candidate_total=len(jobs), job=jobs[0])
    return record, jobs[0].site.apply(SOURCE)


def test_reconstruction_accepts_a_matching_sha_and_skips_the_rest():
    good, mutated = _emitted_record()
    assert mutated != SOURCE
    assert good["mutated_file_sha256"] == hashlib.sha256(mutated).hexdigest()
    assert tool.reconstruct_mutant(good, SOURCE) == (mutated, None)
    assert tool.reconstruct_mutant({**good, "mutated_file_sha256": "0" * 64},
                                   SOURCE) == (None, "sha mismatch")
    assert tool.reconstruct_mutant({**good, "description": "nope"},
                                   SOURCE) == (None, "no site")
    assert tool.reconstruct_mutant(good, b"def f(:\n") == (None, "no site")


def _run(wall: float, collect: float, *, failed=False, passed=False, timed_out=False):
    return {"wall_s": wall, "collect_s": collect, "call_s": wall - collect,
            "failed": failed, "passed": passed, "timed_out": timed_out}


def test_summary_computes_median_p90_and_replay_confirms_kill():
    results = [
        {"m": _run(4.0, 2.0, failed=True), "p": _run(6.0, 2.0, passed=True)},
        {"m": _run(6.0, 2.0, failed=True), "p": _run(8.0, 2.0, passed=False)},
        {"m": _run(8.0, 2.0), "p": _run(10.0, 2.0, passed=True)},
        # exit 2 (collection error, e.g. a mutant that breaks an import) is a kill
        {"m": _run(2.0, 2.0, failed=tool.mutant_failed(2)), "p": _run(2.0, 1.0, passed=True)},
    ]
    assert [tool.mutant_failed(c) for c in (0, 1, 2, 4, 5, None)] == [
        False, True, True, False, False, False]
    summary = tool.summarize(results, {"no site": 4}, 12.5, {"tests/test_a.py": 1.5})
    assert summary["n_valid"] == 4 and summary["n_skipped"] == 4
    assert summary["replay_confirms_kill"] == 2
    assert summary["wall_pair_s"] == {"median": 12.0, "p90": 18.0}
    assert summary["wall_m_s"]["median"] == 5.0
    assert summary["wall_p_s"]["p90"] == 10.0
    assert summary["collection_share"] == 15.0 / 46.0
    text = tool.render_summary(summary)
    assert "replay_confirms_kill: 2/4" in text
    assert "full collection (tests): 12.50s" in text
    assert "one-file collection tests/test_a.py: 1.50s" in text
