#!/usr/bin/env python3
"""WP0b replay pilot: how long does ONE isolated replay of a carried kill take?

Reads the committed cmru R2 progress streams, keeps the last ``killed``
candidate record per ``candidate_id``, and reconstructs each recorded mutant
BYTE-EXACTLY at the checkout this tool runs in (Assay's own Python adapter
finds the site, ``MutationSite.apply`` splices it, and the sha256 must equal
the record's ``mutated_file_sha256``).  A pick is valid only then; that is
the carry-forward situation an old kill faces on today's tree.

Each valid pick is replayed ALONE (``pytest <file>::<node>``) on a mutant copy
(expected to fail) and a pristine copy (expected to pass) of the cmru project
tree, with the interpreter, env and flags of the ``cmru-first-release-r2``
lane.  A tiny pytest plugin splits wall time into collection and call.  The
checkout itself is never modified.  Exit 0 when the measurement completed,
3 when it could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from assay.adapters.python import PythonAdapter  # noqa: E402
from assay.mutation import (  # noqa: E402
    MAX_CANDIDATE_CEILING,
    UNSUPPORTED,
    MutationTarget,
    collect_mutation_sites,
)
from assay.vocabulary import MUTATION_OPERATORS_BY_LANGUAGE  # noqa: E402

DEFAULT_PYTHON = "/opt/tester-venv/bin/python"
PROGRESS_NAME = "progress-replay-pilot.jsonl"
SUMMARY_NAME = "replay-pilot-summary.log"
# Lane `cmru-first-release-r2` (cmru/assay.toml): env PYTHONPATH + no bytecode.
LANE_PYTHONPATH = "src:../libraries/worktree/src"
COPIED = ("cmru", "libraries/worktree")
PLUGIN_NAME = "replay_pilot_timing"
#: Extra pytest arguments for every call (``--pytest-arg``). cmru's conftest
#: refuses a focused run of single nodes unless ``--cli-case-partial`` is set.
EXTRA_PYTEST_ARGS: list[str] = []
PLUGIN_SOURCE = '''import json, os, time
_t = {}
def pytest_sessionstart(session):
    _t["start"] = time.monotonic()
def pytest_collection_finish(session):
    _t["collected"] = time.monotonic()
def pytest_sessionfinish(session, exitstatus):
    _t["end"] = time.monotonic()
    with open(os.environ["REPLAY_PILOT_TIMES"], "w") as handle:
        json.dump(_t, handle)
'''


# --- pure parts ------------------------------------------------------------


def load_killed(evidence: Path) -> tuple[list[dict[str, Any]], int]:
    """Last event per candidate wins (by ``emitted_at``); returns the killed
    records that name a witness, plus the count of null-witness kills."""
    last: dict[str, tuple[str, dict[str, Any]]] = {}
    for stream in sorted(evidence.glob("r2-*/progress-*.jsonl")):
        for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("event") != "candidate":
                continue
            cid, stamp = rec.get("candidate_id"), str(rec.get("emitted_at", ""))
            if isinstance(cid, str) and (cid not in last or stamp >= last[cid][0]):
                last[cid] = (stamp, rec)
    killed = [r for _, r in last.values() if r.get("outcome_bucket") == "killed"]
    named = [r for r in killed if isinstance(r.get("first_failing_test"), str)
             and r["first_failing_test"]]
    return named, len(killed) - len(named)


def witness_file(record: Mapping[str, Any]) -> str:
    return str(record["first_failing_test"]).split("::", 1)[0]


@lru_cache(maxsize=8)
def _sites(original: bytes) -> tuple[Any, ...]:
    text = original.decode("utf-8")
    count = text.count("\n") + (0 if text.endswith("\n") else 1)
    jobs = collect_mutation_sites(
        [MutationTarget(path="x.py", text=text, lines=frozenset(range(1, count + 1)))],
        adapter=PythonAdapter(),
        operators=MUTATION_OPERATORS_BY_LANGUAGE["python"],
        limit=MAX_CANDIDATE_CEILING,
    )
    if jobs == UNSUPPORTED:
        raise RuntimeError("Python adapter reported UNSUPPORTED")
    return tuple(job.site for job in jobs)


def reconstruct_mutant(
    record: Mapping[str, Any], original: bytes
) -> tuple[bytes | None, str | None]:
    """The mutant bytes if they match the record's sha256, else a skip reason."""
    try:
        sites = _sites(original)
    except Exception:  # undecodable or unparsable source: no site can be named
        return None, "no site"
    key = (record["start_byte"], record["end_byte"], record["operator"],
           record["description"])
    matches = [s for s in sites
               if (s.start_byte, s.end_byte, s.operator, s.description) == key]
    if not matches:
        return None, "no site"
    for site in matches:
        mutated = site.apply(original)
        if hashlib.sha256(mutated).hexdigest() == record["mutated_file_sha256"]:
            return mutated, None
    return None, "sha mismatch"


def select_picks(
    records: Sequence[Mapping[str, Any]],
    n: int,
    seed: int,
    accept: Callable[[Mapping[str, Any]], tuple[Any, str | None]],
) -> tuple[list[tuple[Mapping[str, Any], Any]], Counter[str]]:
    """Round-robin one valid pick per witness file, files ordered by kill count
    descending; deterministic for a given ``seed``."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        groups.setdefault(witness_file(rec), []).append(rec)
    order = sorted(groups, key=lambda f: (-len(groups[f]), f))
    rng = random.Random(seed)
    queues = {}
    for name in order:
        queue = sorted(groups[name], key=lambda r: r["candidate_id"])
        rng.shuffle(queue)
        queues[name] = queue
    picks: list[tuple[Mapping[str, Any], Any]] = []
    skips: Counter[str] = Counter()
    while len(picks) < n and any(queues.values()):
        for name in order:
            queue = queues[name]
            while queue and len(picks) < n:
                rec = queue.pop()
                payload, reason = accept(rec)
                if reason is None:
                    picks.append((rec, payload))
                    break
                skips[reason] += 1
    return picks, skips


def mutant_failed(exit_code: int | None) -> bool:
    """Assay counts any judgeable non-pass as a kill: exit 2 is a collection
    error (e.g. a mutant breaking an import). Exit 4 (node not found), 5 (no
    tests collected) and timeouts (None) are not kills."""
    return exit_code in (1, 2)


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def summarize(
    results: Sequence[Mapping[str, Any]],
    skips: Mapping[str, int],
    full_collect_s: float | None,
    file_collect_s: Mapping[str, float | None],
) -> dict[str, Any]:
    def stats(values: list[float]) -> dict[str, float | None]:
        return {"median": statistics.median(values) if values else None,
                "p90": _percentile(values, 90)}

    runs = [r[side] for r in results for side in ("m", "p")]
    wall_total = sum(run["wall_s"] for run in runs)
    return {
        "event": "summary",
        "n_valid": len(results),
        "skips": dict(skips),
        "n_skipped": sum(skips.values()),
        "wall_pair_s": stats([r["m"]["wall_s"] + r["p"]["wall_s"] for r in results]),
        "wall_m_s": stats([r["m"]["wall_s"] for r in results]),
        "wall_p_s": stats([r["p"]["wall_s"] for r in results]),
        "collection_share": (sum(run["collect_s"] for run in runs) / wall_total
                             if wall_total else None),
        "replay_confirms_kill": sum(
            1 for r in results if r["m"]["failed"] and r["p"]["passed"]),
        "timeouts": sum(1 for run in runs if run["timed_out"]),
        "full_collect_s": full_collect_s,
        "one_file_collect_s": dict(file_collect_s),
    }


def render_summary(summary: Mapping[str, Any]) -> str:
    fmt = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    lines = [f"replay pilot: {summary['n_valid']} valid picks, "
             f"{summary['n_skipped']} skipped {summary['skips']}"]
    for label, key in (("M+P", "wall_pair_s"), ("M", "wall_m_s"), ("P", "wall_p_s")):
        s = summary[key]
        lines.append(f"replay wall {label}: median {fmt(s['median'])}s p90 {fmt(s['p90'])}s")
    share = summary["collection_share"]
    lines.append("collection share of replay wall: " + ("n/a" if share is None else f"{share:.1%}"))
    lines.append(f"replay_confirms_kill: {summary['replay_confirms_kill']}/{summary['n_valid']}"
                 f" (timeouts: {summary['timeouts']})")
    lines.append(f"full collection (tests): {fmt(summary['full_collect_s'])}s")
    for name, secs in sorted(summary["one_file_collect_s"].items()):
        lines.append(f"one-file collection {name}: {fmt(secs)}s")
    return "\n".join(lines) + "\n"


# --- process parts ---------------------------------------------------------


def run_pytest(python: str, tree: Path, args: Sequence[str], plugin_dir: Path,
               timeout: float) -> dict[str, Any]:
    times = plugin_dir / "times.json"
    times.unlink(missing_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": f"{LANE_PYTHONPATH}:{plugin_dir}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "REPLAY_PILOT_TIMES": str(times),
    }
    argv = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", PLUGIN_NAME, *EXTRA_PYTEST_ARGS, *args]
    begin, timed_out, exit_code, tail = time.monotonic(), False, None, ""
    try:
        done = subprocess.run(argv, cwd=tree / "cmru", env=env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        exit_code, tail = done.returncode, done.stdout.decode(errors="replace")[-300:]
    except subprocess.TimeoutExpired:
        timed_out = True
    wall = time.monotonic() - begin
    collect = call = 0.0
    try:
        t = json.loads(times.read_text())
        if "collected" in t:
            collect, call = t["collected"] - t["start"], t["end"] - t["collected"]
        else:
            collect = t["end"] - t["start"]
    except (OSError, ValueError, KeyError):
        pass
    return {"exit": exit_code, "timed_out": timed_out, "wall_s": round(wall, 3),
            "collect_s": round(collect, 3), "call_s": round(call, 3), "tail": tail}


def _copy_tree(worktree: Path, dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".git", ".assay", "evidence")
    for rel in COPIED:
        shutil.copytree(worktree / rel, dest / rel, ignore=ignore, symlinks=True)


def _log(message: str) -> None:
    print(message, flush=True)


def _selection(worktree: Path, evidence: Path, n: int, seed: int):
    records, null_witness = load_killed(evidence)

    def accept(rec: Mapping[str, Any]) -> tuple[bytes | None, str | None]:
        path = str(rec["path"])
        source = worktree / path
        witness = worktree / "cmru" / witness_file(rec)
        if not path.startswith("cmru/") or not source.is_file() or not witness.is_file():
            return None, "file missing"
        return reconstruct_mutant(rec, source.read_bytes())

    picks, skips = select_picks(records, n, seed, accept)
    if null_witness:
        skips["null witness"] += null_witness
    return picks, skips


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="output directory (not needed with --select-only)")
    ap.add_argument("--evidence", type=Path, help="default: <worktree>/cmru/docs/evidence")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per pytest call")
    ap.add_argument("--python", default=DEFAULT_PYTHON)
    ap.add_argument("--select-only", action="store_true",
                    help="print the reconstructed selection and exit")
    ap.add_argument("--pytest-arg", action="append", default=[],
                    help="extra pytest argument for every call (repeatable)")
    args = ap.parse_args(argv)
    EXTRA_PYTEST_ARGS[:] = args.pytest_arg
    worktree = args.worktree.resolve()
    evidence = args.evidence or worktree / "cmru/docs/evidence"
    if not args.select_only and args.out is None:
        ap.error("--out is required unless --select-only")

    picks, skips = _selection(worktree, evidence, args.n, args.seed)
    _log(f"selection: {len(picks)} valid pick(s); skipped by reason: {dict(skips)}")
    for rec, _ in picks:
        _log(f"  {rec['candidate_id'][:12]} {rec['path']}:{rec['lineno']} "
             f"{rec['operator']} {rec['description']} <- {rec['first_failing_test']}")
    if not picks:
        return 3
    if args.select_only:
        return 0
    if not Path(args.python).is_file():
        _log(f"interpreter not found: {args.python}")
        return 3

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="replay-pilot-") as tmp_name:
        tmp = Path(tmp_name)
        plugin_dir = tmp / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / f"{PLUGIN_NAME}.py").write_text(PLUGIN_SOURCE)
        pristine, mutant = tmp / "pristine", tmp / "mutant"
        _copy_tree(worktree, pristine)
        _copy_tree(worktree, mutant)
        full = run_pytest(args.python, pristine, ["--collect-only", "-q", "tests"],
                          plugin_dir, args.timeout)
        with (out / PROGRESS_NAME).open("w", encoding="utf-8") as progress:
            for rec, mutated in picks:
                node = rec["first_failing_test"]
                target = mutant / rec["path"]
                original = target.read_bytes()
                target.write_bytes(mutated)
                try:
                    m = run_pytest(args.python, mutant, [node], plugin_dir, args.timeout)
                finally:
                    target.write_bytes(original)
                p = run_pytest(args.python, pristine, [node], plugin_dir, args.timeout)
                m["failed"], p["passed"] = mutant_failed(m["exit"]), p["exit"] == 0
                row = {"event": "replay", "candidate_id": rec["candidate_id"],
                       "node": node, "path": rec["path"], "m": m, "p": p}
                results.append(row)
                print(f"  replay {rec['candidate_id'][:12]} M exit={m['exit']} P exit={p['exit']}"
                      f" M tail={m.get('tail', '')[-120:]!r}", flush=True)
                progress.write(json.dumps(row, sort_keys=True) + "\n")
                progress.flush()
            files = sorted({witness_file(rec) for rec, _ in picks})
            file_collect = {
                f: (None if (r := run_pytest(args.python, pristine,
                                             ["--collect-only", "-q", f], plugin_dir,
                                             args.timeout))["timed_out"] else r["wall_s"])
                for f in files}
            summary = summarize(results, skips,
                                None if full["timed_out"] else full["wall_s"], file_collect)
            progress.write(json.dumps(summary, sort_keys=True) + "\n")
    (out / SUMMARY_NAME).write_text(render_summary(summary), encoding="utf-8")
    _log(render_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
