"""Tests for lib.store: run-id formatting, and the run-directory contract —
atomic-enough appends, a reader that survives a torn line, and an atomic
manifest replace. See DESIGN.md §3 for why marks.jsonl in particular has to
tolerate concurrent, cross-container writers.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import os
import re
from pathlib import Path

import pytest

from lib import store

RUN_ID_RE = re.compile(r"^run-\d{8}-\d{6}-[0-9a-f]{4}$")


# ── new_run_id ───────────────────────────────────────────────────────────────

def test_new_run_id_matches_expected_shape():
    run_id = store.new_run_id()
    assert RUN_ID_RE.match(run_id), run_id


def test_new_run_id_uses_given_prefix():
    run_id = store.new_run_id(prefix="attach")
    assert run_id.startswith("attach-")


def test_new_run_id_is_unique_even_for_the_same_instant():
    when = 1_754_325_600.0
    ids = {store.new_run_id(when=when) for _ in range(50)}
    assert len(ids) == 50


def test_new_run_id_embeds_the_given_time():
    run_id = store.new_run_id(when=1_754_325_600.0)
    # 1754325600 is a fixed instant; only the date/time component is
    # deterministic (the host's local timezone determines the exact digits),
    # so just check the shape survived a real timestamp round-trip.
    assert RUN_ID_RE.match(run_id)


# ── RunDir construction ──────────────────────────────────────────────────────

def test_rundir_creates_directory_by_default(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0001")
    assert rd.path == str(tmp_path / "run-test-0001")
    assert os.path.isdir(rd.path)


def test_rundir_create_false_does_not_make_directory(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0002", create=False)
    assert not os.path.exists(rd.path)


def test_rundir_generates_its_own_run_id_when_absent(tmp_path: Path):
    rd = store.RunDir(str(tmp_path))
    assert RUN_ID_RE.match(rd.run_id)


def test_stream_path_is_under_the_run_directory(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003", compress=())
    assert rd.stream_path("samples.jsonl") == os.path.join(rd.path, "samples.jsonl")
    assert rd.stream_path("samples") == os.path.join(rd.path, "samples.jsonl")


def test_compressed_streams_get_a_gz_extension(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003b")
    assert rd.stream_path("samples") == os.path.join(rd.path, "samples.jsonl.gz")
    # Named either way, a stream resolves to the same file.
    assert rd.stream_path("samples.jsonl") == rd.stream_path("samples")
    # Marks and events stay plain so a human can tail them mid-run.
    assert rd.stream_path("events") == os.path.join(rd.path, "events.jsonl")


def test_non_stream_filenames_keep_their_own_extension(tmp_path: Path):
    # Regression: a generic "add .jsonl if missing" rule rewrote manifest.json
    # as manifest.jsonl, which silently produced a run with no manifest.
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003c")
    assert rd.stream_path("manifest.json") == os.path.join(rd.path, "manifest.json")


def test_an_existing_plain_stream_wins_over_the_compressed_default(tmp_path: Path):
    # A run captured before compression existed must stay readable.
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003d")
    plain = os.path.join(rd.path, "samples.jsonl")
    with open(plain, "w") as fh:
        fh.write('{"seq": 1}\n')
    assert rd.stream_path("samples") == plain
    assert [r["seq"] for r in rd.read("samples")] == [1]


def test_compressed_round_trip_and_truncated_tail(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003e")
    for seq in range(20):
        rd.append("samples", {"seq": seq})
    assert [r["seq"] for r in rd.read("samples")] == list(range(20))

    # A collector killed mid-write leaves a half-finished gzip member. Every
    # record before it is still good data and must survive.
    path = rd.stream_path("samples")
    with open(path, "rb") as fh:
        data = fh.read()
    with open(path, "wb") as fh:
        fh.write(data[:-12])
    recovered = [r["seq"] for r in rd.read("samples")]
    assert recovered == list(range(19))


def test_gzip_stream_skips_a_valid_member_with_invalid_json(tmp_path: Path):
    # A gzip member can decompress perfectly fine and still not be JSON —
    # distinct from the truncated-member case, which fails at decompression
    # rather than at json.loads.
    rd = store.RunDir(str(tmp_path), run_id="run-test-0003f")
    rd.append("samples", {"seq": 1})
    path = rd.stream_path("samples")
    with open(path, "ab") as fh:
        fh.write(gzip.compress(b"not json at all\n", compresslevel=6, mtime=0))
    rd.append("samples", {"seq": 2})
    result = [r["seq"] for r in rd.read("samples")]
    assert result == [1, 2]


# ── append / read round trip ─────────────────────────────────────────────────

def test_append_then_read_round_trips_in_order(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0004")
    records = [{"seq": i, "mono": float(i)} for i in range(5)]
    for record in records:
        rd.append("samples.jsonl", record)
    assert list(rd.read("samples.jsonl")) == records


def test_read_missing_stream_yields_nothing(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0005")
    assert list(rd.read("damon.jsonl")) == []


def test_read_empty_file_yields_nothing(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0006")
    open(rd.stream_path("events.jsonl"), "w").close()
    assert list(rd.read("events.jsonl")) == []


def test_read_skips_truncated_final_line(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0007")
    path = rd.stream_path("marks.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"name": "startup"}) + "\n")
        fh.write(json.dumps({"name": "job-a"}) + "\n")
        fh.write('{"name": "still-writ')   # no closing brace, no newline
    result = list(rd.read("marks.jsonl"))
    assert result == [{"name": "startup"}, {"name": "job-a"}]


def test_read_skips_an_invalid_json_line_anywhere_not_just_last(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0008")
    path = rd.stream_path("events.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "a"}) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"kind": "b"}) + "\n")
    result = list(rd.read("events.jsonl"))
    assert result == [{"kind": "a"}, {"kind": "b"}]


def test_read_skips_a_blank_line_that_is_not_at_eof(tmp_path: Path):
    # Distinct from the truncated-tail case: a genuinely empty line sitting
    # *between* two good records, with real data still to come after it.
    rd = store.RunDir(str(tmp_path), run_id="run-test-0008b")
    path = rd.stream_path("events.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "a"}) + "\n")
        fh.write("\n")
        fh.write(json.dumps({"kind": "b"}) + "\n")
    result = list(rd.read("events.jsonl"))
    assert result == [{"kind": "a"}, {"kind": "b"}]


def test_append_to_a_read_only_directory_raises(tmp_path: Path):
    # Our own writes are not the foreign-process case marks.jsonl has to
    # tolerate — a misconfigured output directory is a real, fatal error and
    # must surface loudly, not be swallowed.
    rd = store.RunDir(str(tmp_path), run_id="run-test-readonly")
    os.chmod(rd.path, 0o555)
    try:
        with pytest.raises(OSError):
            rd.append("samples", {"seq": 1})
    finally:
        os.chmod(rd.path, 0o755)   # so tmp_path teardown can still clean up


def test_append_is_atomic_across_real_separate_processes(tmp_path: Path):
    # concurrent.futures above proves it for threads (shared fd table);
    # this proves the same O_APPEND-single-write property across genuinely
    # separate processes with independent fd tables — the actual scenario
    # marks.jsonl exists for (DESIGN.md §3: "possibly from inside another
    # container sharing the directory").
    if not hasattr(os, "fork"):
        pytest.skip("os.fork() not available on this platform")

    rd = store.RunDir(str(tmp_path), run_id="run-test-multiproc")
    n_procs, n_each = 4, 25

    child_pids = []
    for proc_id in range(n_procs):
        pid = os.fork()
        if pid == 0:
            try:
                for i in range(n_each):
                    rd.append("marks.jsonl", {"proc": proc_id, "i": i, "pad": "y" * 40})
            finally:
                os._exit(0)
        child_pids.append(pid)

    for pid in child_pids:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    records = list(rd.read("marks.jsonl"))
    assert len(records) == n_procs * n_each
    seen = {(r["proc"], r["i"]) for r in records}
    assert seen == {(p, i) for p in range(n_procs) for i in range(n_each)}


def test_append_is_a_single_write_under_o_append_concurrently(tmp_path: Path):
    """Several threads (independent fds, all O_APPEND) appending at once must
    never interleave into a corrupt line — the whole point of routing append
    through one os.write() rather than a buffered file object.
    """
    rd = store.RunDir(str(tmp_path), run_id="run-test-0009")
    n_threads, n_each = 8, 25

    def worker(thread_id: int) -> None:
        for i in range(n_each):
            rd.append("marks.jsonl", {"thread": thread_id, "i": i, "pad": "x" * 40})

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(worker, range(n_threads)))

    records = list(rd.read("marks.jsonl"))
    assert len(records) == n_threads * n_each
    seen = {(r["thread"], r["i"]) for r in records}
    assert seen == {(t, i) for t in range(n_threads) for i in range(n_each)}


# ── manifest ──────────────────────────────────────────────────────────────────

def test_write_and_read_manifest_round_trip(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0010")
    manifest = {"run_id": rd.run_id, "started": 1_754_325_600.0, "targets": ["a", "b"]}
    rd.write_manifest(manifest)
    assert rd.read_manifest() == manifest


def test_write_manifest_leaves_no_tmp_file_behind(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0011")
    rd.write_manifest({"run_id": rd.run_id})
    leftovers = [n for n in os.listdir(rd.path) if ".tmp-" in n]
    assert leftovers == []


def test_write_manifest_replaces_a_previous_manifest_atomically(tmp_path: Path):
    rd = store.RunDir(str(tmp_path), run_id="run-test-0012")
    rd.write_manifest({"run_id": rd.run_id, "phase": "first"})
    rd.write_manifest({"run_id": rd.run_id, "phase": "second"})
    assert rd.read_manifest()["phase"] == "second"
    contents = os.listdir(rd.path)
    assert contents.count("manifest.json") == 1


# ── latest() ──────────────────────────────────────────────────────────────────

def test_latest_returns_none_for_missing_base(tmp_path: Path):
    assert store.RunDir.latest(str(tmp_path / "does-not-exist")) is None


def test_latest_returns_none_for_empty_base(tmp_path: Path):
    base = tmp_path / "runs"
    base.mkdir()
    assert store.RunDir.latest(str(base)) is None


def test_latest_picks_the_lexically_greatest_run_id(tmp_path: Path):
    base = tmp_path / "runs"
    base.mkdir()
    for run_id in ("run-20260101-000000-aaaa", "run-20260804-120000-bbbb", "run-20260304-000000-cccc"):
        store.RunDir(str(base), run_id=run_id)
    latest = store.RunDir.latest(str(base))
    assert latest is not None
    assert latest.run_id == "run-20260804-120000-bbbb"


def test_latest_ignores_non_directory_entries(tmp_path: Path):
    base = tmp_path / "runs"
    base.mkdir()
    store.RunDir(str(base), run_id="run-20260101-000000-aaaa")
    (base / "run-99999999-999999-zzzz.txt").write_text("not a run dir")
    latest = store.RunDir.latest(str(base))
    assert latest is not None
    assert latest.run_id == "run-20260101-000000-aaaa"
