"""B108 step 3a (W9): every ``raise`` in ``campaign.py`` is reachable through the public command.

One row per refusal: ``(case_id, mutation, message_fragment)``. Each mutation edits a
complete valid ``r2_pass`` campaign (progress records 0 run, 1 candidates, 2 baseline,
3-4 candidate, 5 end, 6 verdict_written) and the command must exit 2 with an
evidence error whose first message holds the fragment. The LOG maps every raise line
to its case id; a raise no input can reach is a numbered QUESTION there, not a pragma.
"""

from __future__ import annotations

import json
import os
import subprocess
import types

import pytest

from analysis.tests.test_analysis_campaign import (
    _complete_fixture,
    _fixture_document,
    _invoke,
    _plan_rows,
    _rewrite_progress,
    _validate,
)
from assay.mutation import select_mutation_shard
from assay_analysis import campaign as campaign_api

_ZERO = "0" * 40
# The real planner entry, captured before any fixture replaces it with a stub.
_REAL_LANE_PLAN = campaign_api._lane_plan


def _progress(change):
    """A mutation that edits the progress records in place."""

    def mutate(env):
        _rewrite_progress(env.progress, change)
        return {}

    return mutate


def _raw(text: bytes):
    def mutate(env):
        env.progress.write_bytes(text)
        return {}

    return mutate


def _cap(name: str, value: int):
    def mutate(env):
        env.monkeypatch.setattr(campaign_api, name, value)
        return {}

    return mutate


def _elsewhere(make):
    def mutate(env):
        return {"progress": make(env)}

    return mutate


def _fstat_lie(env, *, mtime_shift: int = 0, size: int | None = None):
    """Make ``os.fstat`` on the progress file lie, leaving every other descriptor alone."""
    real = os.fstat
    target = str(env.progress.resolve())
    calls = []

    def fake(fd):
        result = real(fd)
        try:
            same = os.readlink(f"/proc/self/fd/{fd}") == target
        except OSError:
            same = False
        if not same:
            return result
        calls.append(fd)
        first = len(calls) == 1
        return types.SimpleNamespace(
            st_mode=result.st_mode, st_ino=result.st_ino, st_dev=result.st_dev,
            st_size=size if (first and size is not None) else result.st_size,
            st_mtime_ns=result.st_mtime_ns + (0 if first else mtime_shift),
        )

    env.monkeypatch.setattr(campaign_api.os, "fstat", fake)


def _grown_after_stat(env):
    _cap("MAX_PROGRESS_BYTES", 10)(env)
    _fstat_lie(env, size=0)
    return {}


def _changed_while_reading(env):
    _fstat_lie(env, mtime_shift=1)
    return {}


def _symlink(env):
    link = env.tmp_path / "progress-link.jsonl"
    link.symlink_to(env.progress)
    return {"progress": link}


def _directory(env):
    return {"progress": env.tmp_path}


def _missing(env):
    return {"progress": env.tmp_path / "absent-progress.jsonl"}


def _torn(env):
    env.progress.write_text(env.progress.read_text().rstrip("\n"))
    return {}


def _empty_without_verdict(env):
    env.progress.write_text("")
    return {"verdict": None, "command_exit": None}


def _many_runs(env):
    env.monkeypatch.setattr(campaign_api, "MAX_PROGRESS_RUNS", 1)

    def change(records):
        records.append(dict(records[0]))

    _rewrite_progress(env.progress, change)
    return {}


def _other_commit_run(records):
    records.append({**records[0], "commit": "1" * 40})


def _insert(index, *events):
    def change(records):
        records[index:index] = list(events)

    return _progress(change)


def _set(index, **fields):
    return _progress(lambda records: records[index].update(fields))


def _drop(*indexes):
    def change(records):
        for index in sorted(indexes, reverse=True):
            del records[index]

    return _progress(change)


def _drop_then_insert(indexes, at, *events):
    def change(records):
        for index in sorted(indexes, reverse=True):
            del records[index]
        records[at:at] = list(events)

    return _progress(change)


def _append(*events):
    return _progress(lambda records: records.extend(events))


def _two_events_at(index, event):
    return _insert(index, event, dict(event))


def _verdict_bytes(text: bytes):
    def mutate(env):
        env.verdict.write_bytes(text)
        return {}

    return mutate


def _excluded(env, name: str):
    """A worktree path the repository's own committed ``.gitignore`` hides, so the
    dirty-worktree refusal in front of the lane binding stays quiet. The commit moves
    HEAD; the lane binding is checked before the verdict and progress bind to it."""
    (env.root / ".gitignore").write_text(f"{name}\n")
    for arguments in (("add", ".gitignore"), ("commit", "-q", "-m", "ignore")):
        subprocess.run(["git", "-C", str(env.root), *arguments], check=True, capture_output=True)
    env.head = subprocess.run(
        ["git", "-C", str(env.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return env.root / name


def _lane_symlink(env):
    link = _excluded(env, "ignored-link.toml")
    link.symlink_to(env.root / "assay.toml")
    return {"head": env.head, "extra": ("--file", str(link))}


def _lane_outside(env):
    return {"extra": ("--file", str(env.tmp_path / "outside.toml"))}


def _lane_untracked(env):
    untracked = _excluded(env, "ignored.toml")
    untracked.write_text("schema_version = 2\n")
    return {"head": env.head, "extra": ("--file", str(untracked))}


def _lane_edited(env):
    """Edited after the commit but hidden from ``git status`` by skip-worktree."""
    lane = env.root / "assay.toml"
    subprocess.run(
        ["git", "-C", str(env.root), "update-index", "--skip-worktree", "assay.toml"],
        check=True, capture_output=True,
    )
    lane.write_text(lane.read_text() + "\n# edited after the commit\n")
    return {}


def _commit_all(env):
    for arguments in (("add", "-A"), ("commit", "-q", "-m", "lane edit")):
        subprocess.run(["git", "-C", str(env.root), *arguments], check=True, capture_output=True)
    env.head = subprocess.run(
        ["git", "-C", str(env.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _lane_directory(env):
    """The lane path names a committed directory: ``ls-tree`` reports one ``tree`` entry."""
    (env.root / "lanes").mkdir()
    (env.root / "lanes" / "x.toml").write_text("x = 1\n")
    _commit_all(env)
    return {"head": env.head, "extra": ("--file", str(env.root / "lanes"))}


def _lane_unreadable(env):
    """Only the lane read fails; the progress and verdict reads keep the real reader."""
    real = campaign_api._read_artifact

    def read(path, *, limit, label):
        if label == "lane file":
            raise OSError("boom")
        return real(path, limit=limit, label=label)

    env.monkeypatch.setattr(campaign_api, "_read_artifact", read)
    return {}


def _request_base_lane(env):
    """The committed lane delegates its base to the request; no verdict, no ``--request-base``."""
    lane = env.root / "assay.toml"
    lane.write_text(lane.read_text().replace('base = "main"\n', 'base_source = "request"\n'))
    _commit_all(env)
    return {"head": env.head, "verdict": None, "command_exit": None}


def _no_mutation_plan(env):
    """Assay's own planner answers ``UNSUPPORTED`` for the (native R2) lane."""
    env.monkeypatch.setattr(campaign_api, "_lane_plan", _REAL_LANE_PLAN)
    env.monkeypatch.setattr("assay.cli.plan_jobs", lambda *_args, **_kwargs: "UNSUPPORTED")
    return {}


def _plan_base_differs(env):
    rows = _plan_rows(_fixture_document("r2_pass"))
    env.monkeypatch.setattr(
        campaign_api, "_lane_plan",
        lambda *_args: {"status": "ok", "candidates": rows, "_resolved_base": _ZERO},
    )
    return {}


def _verdict_edit(change):
    """Edit the (unsigned) verdict document on disk before the command reads it."""

    def mutate(env):
        document = json.loads(env.verdict.read_text())
        change(document)
        env.verdict.write_text(json.dumps(document))
        return {}

    return mutate


def _shard_progress(*, keep_selected: bool):
    """Rewrite the progress to a two-way shard run holding ONE candidate event, either the
    one the deterministic shard 0 assigns (``keep_selected``) or one outside it."""

    def change(records):
        events = [record for record in records if record["event"] == "candidate"]
        ids = [event["candidate_id"] for event in events]
        assigned = {ids[position] for position in select_mutation_shard(ids, index=0, count=2)}
        chosen = next(event for event in events if (event["candidate_id"] in assigned) == keep_selected)
        run, candidates, baseline = records[0], records[1], records[2]
        end, terminal = next(r for r in records if r["event"] == "end"), records[-1]
        candidates.update(selected_total=1, pending_total=1)
        chosen.update(candidate_index=0, candidate_total=1)
        end["buckets"] = {name: 0 for name in end["buckets"]}
        end["buckets"][chosen["outcome_bucket"]] = 1
        shard = {"event": "shard", "shard_index": 0, "shard_count": 2, "selected_total": 1}
        records[:] = [run, shard, candidates, baseline, chosen, end, terminal]

    return _progress(change)


def _verdict_sharded_progress_is_not(env):
    """A verdict for shard 0 of 2 (one outcome) beside an unsharded progress that records both."""
    original = _fixture_document("r2_pass")
    ids = [row["id"] for row in _plan_rows(original)]
    assigned = {ids[position] for position in select_mutation_shard(ids, index=0, count=2)}

    def change(document):
        document["judgment"]["r2"].update(shard_index=0, shard_count=2)
        mutation = next(claim for claim in document["claims"] if claim["rigor"] == "R2")["mutation"]
        for bucket in ("killed", "survived", "equivalent", "crashed", "hung", "budget_exceeded"):
            mutation[bucket] = [o for o in mutation.get(bucket, []) if o["candidate_id"] in assigned]
        mutation["candidate_ids"] = [i for i in mutation["candidate_ids"] if i in assigned]
        mutation["total"] = mutation["candidate_count"] = len(assigned)

    return _verdict_edit(change)(env)


def _both(*mutations):
    def mutate(env):
        merged = {}
        for one in mutations:
            merged.update(one(env))
        return merged

    return mutate


def _options(**options):
    """Replace a harness option (``command_exit``, ``extra``, ...) without touching the fixture."""

    def mutate(env):
        return options

    return mutate


def _log_missing(env):
    return {"extra": ("--log", str(env.tmp_path / "absent-gate.log"))}


RESUME = {"event": "resume", "resumed_total": 0, "rejected_total": 0, "rejudged_total": 0}
SHARD = {"event": "shard", "shard_index": 0, "shard_count": 1, "selected_total": 2}
MERGED = {"event": "resume_merged", "resumed_total": 0}

CASES = (
    # -- artifact reads (progress carries the shared reader) ------------------
    ("progress-missing", _missing, "progress artifact is missing"),
    ("progress-symlink", _symlink, "cannot open progress artifact"),
    ("progress-directory", _directory, "progress artifact is not a regular file"),
    ("progress-oversize-declared", _cap("MAX_PROGRESS_BYTES", 10), "progress artifact exceeds 10 bytes"),
    ("progress-oversize-while-reading", _grown_after_stat, "progress artifact exceeds 10 bytes"),
    ("progress-changed-while-reading", _changed_while_reading, "changed while it was being read"),
    # -- progress stream framing ---------------------------------------------
    ("progress-not-utf8", _raw(b"\xff\xfe\n"), "progress artifact is not UTF-8"),
    ("progress-torn-with-verdict", _torn, "torn final record"),
    ("progress-unknown-event", _append({"event": "bogus"}), "malformed progress event at line 8"),
    ("run-commit-not-full", _set(0, commit="abc"), "progress run lacks a full commit at line 1"),
    ("run-wrong-lane", _set(0, lane="other"), "progress run at line 1 names lane"),
    ("too-many-runs", _many_runs, "progress contains more than 1 runs"),
    ("event-before-first-run", _insert(0, {"event": "baseline"}), "progress event precedes first run at line 1"),
    ("event-conflicting-commit", _set(2, commit=_ZERO), "progress event has conflicting commit at line 3"),
    ("event-wrong-lane", _set(2, lane="other"), "progress event names wrong lane at line 3"),
    ("event-after-terminal", _append({"event": "baseline"}), "events after verdict_written at line 8"),
    ("sweep-event-after-end", _insert(6, {"event": "baseline"}), "sweep events after end at line 7"),
    ("no-run-at-all", _empty_without_verdict, "progress has no run at expected commit"),
    ("latest-run-other-commit", _progress(_other_commit_run), "latest progress run is for commit"),
    # -- run milestones ---------------------------------------------------------
    ("candidates-repeated", _two_events_at(2, {"event": "candidates"}), "repeats its candidates milestone"),
    ("candidates-pilot-selection", _set(1, selection_sha256="x"), "pilot selection unsupported before P7"),
    ("candidates-bad-judge", _set(1, judge_sha256="zz"), "progress judge_sha256 must be a SHA-256 digest"),
    ("shard-repeated", _two_events_at(1, SHARD), "repeats its shard milestone"),
    ("shard-out-of-order", _insert(2, SHARD), "shard milestone is out of order"),
    ("resume-repeated", _two_events_at(1, RESUME), "repeats its resume milestone"),
    ("resume-out-of-order", _insert(2, RESUME), "resume milestone is out of order"),
    ("resume-merged-repeated", _two_events_at(2, MERGED), "repeats its resume_merged milestone"),
    ("resume-merged-out-of-order", _insert(1, MERGED), "resume_merged milestone is out of order"),
    ("candidate-before-candidates", _drop(1), "candidate event precedes its candidates milestone"),
    ("candidate-invalid-id", _set(3, candidate_id="nope"), "progress candidate event has an invalid candidate_id"),
    (
        "candidate-repeated",
        _progress(lambda r: r[4].update(candidate_id=r[3]["candidate_id"])),
        "progress run repeats candidate",
    ),
    ("candidate-outside-plan", _set(3, candidate_id="f" * 64), "candidate outside the current plan"),
    ("candidate-unknown-bucket", _set(3, outcome_bucket="weird"), "unknown outcome_bucket"),
    ("candidate-field-differs", _set(3, lineno=999), "lineno differs from the current plan"),
    ("candidate-invalid-index", _set(3, candidate_index=-1), "invalid candidate_index"),
    (
        "candidate-index-repeated",
        _progress(lambda r: r[4].update(candidate_index=r[3]["candidate_index"])),
        "progress run repeats candidate_index 0",
    ),
    ("end-out-of-order", _drop_then_insert((1, 3, 4), 1, RESUME), "end milestone is out of order"),
    # -- run totals ---------------------------------------------------------------
    ("shard-malformed", _insert(1, {**SHARD, "shard_index": "a"}), "shard milestone is malformed"),
    ("shard-invalid", _insert(1, {**SHARD, "shard_index": 5}), "shard milestone is invalid"),
    ("shard-selected-differs", _insert(1, {**SHARD, "selected_total": 1}), "differs from its deterministic assignment"),
    ("selected-total-not-integer", _set(1, selected_total="x"), "progress selected_total must be a non-negative integer"),
    ("resumed-total-negative", _insert(1, {**RESUME, "resumed_total": -1}), "progress resumed_total must be a non-negative integer"),
    ("candidate-total-differs", _set(1, candidate_total=99), "candidate_total differs from the current plan"),
    ("selected-total-differs", _set(1, selected_total=1, pending_total=1), "selected_total differs from the current selected inventory"),
    ("totals-do-not-reconcile", _set(1, pending_total=1), "selected, resumed, and pending totals do not reconcile"),
    (
        "events-exceed-pending",
        _progress(lambda r: (r.insert(1, {**RESUME, "resumed_total": 1}), r[2].update(pending_total=1))),
        "candidate events exceed pending_total",
    ),
    ("index-outside-range", _set(4, candidate_index=5), "candidate_index is outside the pending candidate range"),
    (
        "index-not-contiguous",
        _progress(lambda r: (r.pop(3), r[3].update(candidate_index=1))),
        "not a contiguous execution sequence",
    ),
    ("event-total-disagrees", _set(3, candidate_total=9), "candidate events disagree with pending_total"),
    (
        "mutation-events-without-candidates",
        _drop_then_insert((1, 3, 4, 5), 1, RESUME),
        "mutation events without a candidates milestone",
    ),
    ("merged-without-resume", _insert(2, MERGED), "resume_merged milestone lacks a resume milestone"),
    (
        "merged-count-differs",
        _progress(lambda r: (r.insert(1, RESUME), r.insert(3, {**MERGED, "resumed_total": 5}))),
        "resume_merged count differs from its resume milestone",
    ),
    ("end-total-not-integer", _set(5, candidate_total="x"), "end candidate_total is not a non-negative integer"),
    ("end-buckets-not-canonical", _set(5, buckets={}), "end buckets do not match the canonical mutation outcomes"),
    (
        "end-bucket-negative",
        _progress(lambda r: r[5]["buckets"].update(killed=-1)),
        "end bucket 'killed' is not a non-negative integer",
    ),
    ("end-without-candidates", _drop(1, 3, 4), "completed sweep without a candidates milestone"),
    ("end-reason-after-candidates", _set(5, reason="x"), "pre-submission end after candidates were selected"),
    ("end-total-differs", _set(5, candidate_total=9), "end candidate_total differs from candidate plan total"),
    (
        "end-buckets-do-not-reconcile",
        _progress(lambda r: r[5]["buckets"].update(killed=1)),
        "end buckets do not reconcile with selected_total",
    ),
    (
        "end-buckets-disagree-with-verdict",
        _progress(lambda r: r[5]["buckets"].update(killed=0, survived=2)),
        "end buckets disagree with verified verdict outcomes",
    ),
    ("terminal-exit-code-not-integer", _set(6, exit_code="0"), "verdict_written event has no integer exit_code"),
    # -- field helpers reached through a candidate event -----------------------
    ("elapsed-not-a-number", _set(3, elapsed_seconds="x"), "candidate elapsed_seconds must be a number"),
    ("elapsed-negative", _set(3, elapsed_seconds=-1), "candidate elapsed_seconds must be finite and non-negative"),
    ("phase-seconds-shape", _set(3, phase_seconds={"a": 1}), "candidate phase_seconds must be null or an object"),
    ("emitted-at-empty", _set(3, emitted_at=""), "candidate emitted_at must be a non-empty ISO timestamp"),
    ("emitted-at-not-iso", _set(3, emitted_at="not-a-date"), "candidate emitted_at is not an ISO timestamp"),
    ("emitted-at-no-timezone", _set(3, emitted_at="2026-09-27T12:00:10"), "candidate emitted_at must include a timezone"),
    # -- verdict bytes ---------------------------------------------------------------
    ("verdict-not-utf8", _verdict_bytes(b"\xff\xfe"), "verdict artifact is not UTF-8"),
    ("verdict-fails-verification", _verdict_bytes(b'{"schema_version": 1}'), "invalid Assay verdict"),
    # -- lane file binding -------------------------------------------------------------
    ("lane-file-symlink", _lane_symlink, "lane file is a symlink"),
    ("lane-file-outside-worktree", _lane_outside, "lane file must be inside the expected worktree"),
    ("lane-file-untracked", _lane_untracked, "lane file is not a single committed path"),
    ("lane-file-edited", _lane_edited, "lane file bytes differ from the expected committed tree"),
    ("lane-file-directory", _lane_directory, "lane file is not a committed regular file: lanes"),
    ("lane-file-unreadable", _lane_unreadable, "cannot read lane file"),
    # -- verdict bound to the declared lane ---------------------------------------------
    (
        "verdict-r2-policy-differs",
        _verdict_edit(lambda d: d["judgment"]["r2"].update(jobs=d["judgment"]["r2"]["jobs"] + 1)),
        "R2 verdict policy jobs differs from the named lane declaration",
    ),
    # -- plan reconstruction -------------------------------------------------------------
    (
        "plan-base-needs-verdict-or-request-base",
        _request_base_lane,
        "plan base cannot be reconstructed without --verdict or --request-base",
    ),
    ("plan-unsupported", _no_mutation_plan, "lane has no mutation plan"),
    ("plan-base-differs-from-verdict", _plan_base_differs, "reconstructed plan base differs from the verified verdict"),
    # -- progress against the verdict's selected scope ------------------------------------
    (
        "progress-candidate-outside-the-verdict-shard",
        _verdict_sharded_progress_is_not,
        "has no corresponding outcome in the verified verdict",
    ),
    (
        "progress-shard-differs-from-verdict",
        _shard_progress(keep_selected=True),
        "latest progress selected inventory differs from the verified verdict",
    ),
    (
        "progress-candidate-outside-its-shard",
        _both(_shard_progress(keep_selected=False), _options(verdict=None, command_exit=None)),
        "latest progress run includes candidates outside its selected scope",
    ),
    # -- argument checks -----------------------------------------------------------------
    ("command-exit-negative", _options(command_exit=-1), "command exit must be a non-negative integer"),
    ("offset-negative", _options(extra=("--offset", "-1")), "detail offset must be a non-negative integer"),
    ("limit-zero", _options(extra=("--limit", "0")), "detail limit must be between 1 and"),
    (
        "outcome-duplicated",
        _options(extra=("--outcome", "killed", "--outcome", "killed")),
        "outcome filters contain duplicates",
    ),
    ("gate-log-missing", _log_missing, "gate log artifact is missing"),
)


@pytest.mark.parametrize(("case_id", "mutate", "fragment"), CASES, ids=[case[0] for case in CASES])
def test_every_campaign_refusal_is_reachable(tmp_path, monkeypatch, case_id, mutate, fragment):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    env = types.SimpleNamespace(
        tmp_path=tmp_path, monkeypatch=monkeypatch, root=root, head=head,
        verdict=verdict, progress=progress,
    )
    options = {"verdict": verdict, "command_exit": 0, "progress": progress, **mutate(env)}
    code, out, _err = _invoke(
        root, options.pop("head", head), options.pop("verdict"), options.pop("progress"), **options
    )
    document = json.loads(out)
    _validate(document)
    messages = [error["message"] for error in document["errors"]]
    assert code == 2, (case_id, document.get("status"))
    assert fragment in messages[0], (case_id, messages)
