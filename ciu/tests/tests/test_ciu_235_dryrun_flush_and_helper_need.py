"""CIU-235 -- two dry-run lines and the helper verdict, at their real boundaries.

Campaign `rgw-ciu-20261125T202211-v1001` (lane `ciu`, base ciu-v7.27.0) left
three `engine.py` candidates unjudged because the R2 per-candidate budget cut
the whole suite before a surviving mutant could be classified (CIU-235,
`ciu/assay.toml`). Two of them are `flush=True` -> `False` on the S6 dry-run
lines; one is the helper verdict (`helper_needed = False` -> `True`).

**Why a stream-boundary oracle for the flush mutants.** "The process exits, so
the buffer is flushed anyway" is true and insufficient: ciu's progress output is
read through a PIPE next to child-process output (a `docker compose up` stream,
an exec runner, the foreground one-shot), and a line still buffered when the
next child starts is delivered out of order, or only at exit. The contract is
therefore *when* the text reaches the stream, not merely that it eventually
does. A fake `sys.stdout` that keeps `write` and `flush` as separate events is
the honest boundary for that: a line is emitted-flushed only when a `flush()`
immediately publishes the chunk that ends with it, so a line that merely
reached the buffer fails even though a LATER flush would eventually publish it.

**Why the helper verdict needs a dry run.** `_create_dir`'s summary line carries
`{'REQUIRED' if helper_needed else 'not needed'}`, and only `plan_only` prints
it, so a real run can never see the false branch of that conditional. The
existing REQUIRED case stays covered by
`test_ciu_227_230_hostdir_preconditions.py::
test_plan_only_creates_no_hostdir_with_a_satisfiable_precondition`; this file
pins the "not needed" half, where the target ownership is one this process can
apply itself (CIU-227's derivation).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402

OWN_UID, OWN_GID = 1003, 1003


def _fail(message: str):
    raise AssertionError(message)


def _config(hostdir: dict | None = None) -> dict:
    return {
        "deploy": {"env": {"shared": {
            "CONTAINER_UID": str(OWN_UID), "DOCKER_GID": str(OWN_GID),
        }}},
        "service": {"name": "api", "hostdir": hostdir or {"data": ""}},
    }


def _own_process(monkeypatch) -> None:
    """A non-root run whose target ownership it can apply itself (no helper)."""
    monkeypatch.setattr(engine.os, "geteuid", lambda: OWN_UID)
    monkeypatch.setattr(engine.os, "getegid", lambda: OWN_GID)
    monkeypatch.setattr(engine.os, "getgroups", lambda: [OWN_GID])
    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    # A dry run must not create, chown or chmod anything.
    monkeypatch.setattr(engine.os, "chown", lambda *_a: _fail("a dry run must not chown"))
    monkeypatch.setattr(engine.os, "chmod", lambda *_a: _fail("a dry run must not chmod"))


class RecordingStdout:
    """A stdout that keeps WRITTEN text apart from FLUSHED (published) text.

    `published` holds one entry per `flush()`: exactly the text a reader of the
    pipe received at that moment. Text written after the last flush stays in
    `pending` and is deliberately NOT in `published`.
    """

    encoding = "utf-8"

    def __init__(self) -> None:
        self.published: list[str] = []
        self.pending = ""

    def write(self, text: str) -> int:
        self.pending += text
        return len(text)

    def flush(self) -> None:
        self.published.append(self.pending)
        self.pending = ""

    def isatty(self) -> bool:
        return False


def test_a_dry_run_of_self_ownable_hostdirs_reports_the_helper_as_not_needed(
    tmp_path, monkeypatch, capsys
):
    """engine.py:921 -- `helper_needed` starts False, and a dry run says so.

    Flipping the initialisation to `True` makes every satisfiable dry run claim
    `S6.5 root helper REQUIRED`, which sends an operator chasing a cgroup parent
    the run does not need. Nothing else in the output changes, so only the
    helper word distinguishes the two code paths.
    """
    _own_process(monkeypatch)

    engine.create_hostdirs(
        _config({"data": "", "logs": ""}), tmp_path,
        repo_root=tmp_path, physical_root=tmp_path, plan_only=True,
    )

    out = capsys.readouterr().out
    assert "Would create 2 volume directories" in out
    assert "helper not needed" in out
    assert "S6.5 root helper REQUIRED" not in out
    assert not (tmp_path / "vol-api-data").exists()
    assert not (tmp_path / "vol-api-logs").exists()


def test_every_dry_run_line_reaches_the_stream_in_the_flush_that_publishes_it(
    tmp_path, monkeypatch
):
    """engine.py:999 and engine.py:1073 -- each line is published by its own flush.

    The dry run is the operator's preview: the lines must be readable through the
    pipe while the run is still going (they are printed next to child-process
    output), so each of them is written with `flush=True`. The contract asserted
    here is the delivery contract, not a print granularity: **the flush that
    publishes a line carries nothing written after it** -- the text a reader
    received at that moment ends with that line, and the run writes nothing more
    into that same chunk. A `flush=False` leaves the line for whichever flush
    comes next (or for process exit), so that flush then carries the line
    TOGETHER with whatever was written after it: a preview that is not readable
    until the next line is printed. That is the delivery change under test, and
    it is what makes the two mutants fail. This test's narrower oracle requires
    each of the four preview lines to end its own distinct flush chunk.
    """
    _own_process(monkeypatch)

    recorder = RecordingStdout()
    monkeypatch.setattr(sys, "stdout", recorder)

    engine.create_hostdirs(
        _config({"data": "", "logs": ""}), tmp_path,
        repo_root=tmp_path, physical_root=tmp_path, plan_only=True,
    )

    # A dry run of two hostdirs prints FOUR lines the operator must be able to
    # read while the run is still going: the scan banner, two `Would create:`
    # lines and the summary. The contract asserted is the DELIVERY one, per line:
    # **the flush that publishes a line carries nothing written after it**. Each
    # line must be the final complete line in its own distinct flush chunk; a
    # combined two-line flush is outside this oracle's narrower contract. It
    # refuses the mutant's shape, where a line is only a prefix of a chunk that
    # carries LATER text.
    published = recorder.published
    expected_lines = (
        ("[INFO] Scanning for volume directories", 1),
        ("[INFO]   Would create: ", 2),
        ("[INFO] Would create ", 1),
    )
    carriers_all: list[str] = []
    for prefix, count in expected_lines:
        # The line a flush published is the LAST line of that chunk: anything
        # written after it would have been in the same chunk instead.
        carriers = [
            chunk for chunk in published
            if chunk.split("\n")[-2:-1]
            and chunk.split("\n")[-2].startswith(prefix)
        ]
        assert len(carriers) == count, (prefix, published)
        carriers_all.extend(carriers)
        for chunk in carriers:
            line = chunk.split("\n")[-2]
            # The chunk ENDS with that line: nothing was written after it into
            # the same flush, which is the contract this file documents.
            assert chunk[-1] == "\n", chunk
            if prefix == "[INFO]   Would create: ":
                # The ownership and mode are part of the preview the operator
                # reads, so they must be in the line the flush published.
                assert line.endswith(", 0o775)"), line
            if prefix == "[INFO] Would create ":
                assert "--dry-run creates none" in line, line
    # These four are DISTINCT flushes -- a line swept up into a later chunk is
    # not a chunk that ends with it, so it could never appear here.
    assert len({published.index(chunk) for chunk in carriers_all}) == 4, published
    # Nothing the dry run printed is left buffered.
    assert not recorder.pending, recorder.pending
