"""P21 work item 8 / A-O14 / A-181 -- :mod:`assay.output`, the reserved,
atomic verdict destination.

The claim under attack: **a requested artifact that physically cannot exist
is refused BEFORE the lane's command runs, and a destination assay did not
itself observe is never destroyed.**

Before this package, ``--verdict-json <unwritable>`` ran the lane's command
to completion and then died with a bare ``OSError`` and exit 1 -- a tooling
failure a consumer reads as FAIL, with the side effects already committed.

Every check here is an I/O fact, never a timing guess (§3b.A), and every
temporary this module asserts about is asserted by DIRECTORY LISTING rather
than by a name pattern, so a leaked temp cannot hide behind a different
nonce.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.output import VerdictOutput, reserve_verdict_output


def _names(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir())


# --- stdout mode --------------------------------------------------------


def test_stdout_mode_emits_the_complete_text_and_never_closes_the_stream():
    stream = io.StringIO()
    destination = reserve_verdict_output("-", stdout=stream)

    destination.emit('{"schema_version": 5}\n')
    destination.close()

    assert stream.getvalue() == '{"schema_version": 5}\n'
    assert not stream.closed, "the caller owns its own stdout"


def test_stdout_mode_refuses_a_stream_that_cannot_be_written():
    """The zero-length probe: reservation must fail EARLY for a broken
    stream, not at emission time after the lane has already run."""
    stream = io.StringIO()
    stream.close()

    with pytest.raises(AssayError) as caught:
        reserve_verdict_output("-", stdout=stream)

    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert caught.value.outcome is Outcome.ERROR


# --- file mode: reservation refuses what cannot work --------------------


def test_a_missing_parent_is_refused_at_reservation(tmp_path: Path):
    with pytest.raises(AssayError) as caught:
        reserve_verdict_output(str(tmp_path / "nope" / "verdict.json"), stdout=io.StringIO())

    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED


def test_a_directory_destination_is_refused(tmp_path: Path):
    target = tmp_path / "verdict.json"
    target.mkdir()

    with pytest.raises(AssayError, match="not an ordinary regular file"):
        reserve_verdict_output(str(target), stdout=io.StringIO())


def test_a_symlinked_destination_is_refused(tmp_path: Path):
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "verdict.json"
    link.symlink_to(real)

    with pytest.raises(AssayError, match="not an ordinary regular file"):
        reserve_verdict_output(str(link), stdout=io.StringIO())


def test_a_symlinked_parent_component_is_refused(tmp_path: Path):
    """``O_NOFOLLOW`` applied per component: a single ``open(dir)`` would
    follow this happily, which is how an artifact lands somewhere the
    consumer never named."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (tmp_path / "link").symlink_to(real_dir)

    with pytest.raises(AssayError, match="without following a symlink"):
        reserve_verdict_output(str(tmp_path / "link" / "verdict.json"), stdout=io.StringIO())


def test_an_unwritable_parent_is_refused_by_a_real_probe(tmp_path: Path):
    """`os.access` would answer about permission BITS; the probe answers
    about what emission will actually do."""
    parent = tmp_path / "readonly"
    parent.mkdir()
    os.chmod(parent, 0o500)
    try:
        with pytest.raises(AssayError, match="does not accept a new file"):
            reserve_verdict_output(str(parent / "verdict.json"), stdout=io.StringIO())
    finally:
        os.chmod(parent, 0o700)


def test_the_filesystem_root_names_no_file_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``/`` normalizes to a path with no basename left, so there is nothing
    to write. A distinct branch from the directory-destination case below,
    where the basename is real and the object at it simply is not a regular
    file."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssayError, match="does not name a file"):
        reserve_verdict_output("/", stdout=io.StringIO())


@pytest.mark.parametrize("spelling", ["", ".", "..", "some/dir/verdict.json"])
def test_an_unusable_spelling_is_the_typed_output_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
):
    """Whatever the branch, the terminal is the same closed one -- never a
    bare ``OSError`` a consumer would read as FAIL."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssayError) as caught:
        reserve_verdict_output(spelling, stdout=io.StringIO())

    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert caught.value.outcome is Outcome.ERROR


def test_reservation_leaves_no_probe_behind(tmp_path: Path):
    """A-181's own defect: a temp held across the lane is observable to the
    command AND is untracked worktree state that can make P20's dirt guard
    refuse the run. Asserted by listing the directory, not by guessing a
    name."""
    destination = reserve_verdict_output(str(tmp_path / "verdict.json"), stdout=io.StringIO())

    assert _names(tmp_path) == []

    destination.close()
    assert _names(tmp_path) == []


# --- file mode: the CLI process namespace -------------------------------


def test_a_relative_target_is_anchored_to_the_process_cwd_not_a_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A-181: the target belongs to the CLI process's own cwd. Anchoring it
    to the lane's ``project_root`` instead would silently relocate a
    consumer's requested artifact into the tree under test."""
    caller_cwd = tmp_path / "caller"
    project_root = tmp_path / "project"
    caller_cwd.mkdir()
    project_root.mkdir()
    monkeypatch.chdir(caller_cwd)

    destination = reserve_verdict_output("verdict.json", stdout=io.StringIO())
    destination.emit("{}\n")
    destination.close()

    assert (caller_cwd / "verdict.json").read_text(encoding="utf-8") == "{}\n"
    assert _names(project_root) == []


def test_dot_segments_are_normalized_lexically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    destination = reserve_verdict_output("../../verdict.json", stdout=io.StringIO())
    destination.emit("{}\n")
    destination.close()

    assert (tmp_path / "verdict.json").read_text(encoding="utf-8") == "{}\n"


# --- file mode: emission ------------------------------------------------


def test_emission_writes_utf8_atomically_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "verdict.json"
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())

    destination.emit('{"lane": "pkg", "note": "éè"}\n')
    destination.close()

    assert target.read_text(encoding="utf-8") == '{"lane": "pkg", "note": "éè"}\n'
    assert _names(tmp_path) == ["verdict.json"]


def test_an_unchanged_prior_artifact_is_replaced(tmp_path: Path):
    """The permitted case: assay observed this exact file at reservation and
    nothing touched it since, so replacing it destroys nothing unaccounted
    for."""
    target = tmp_path / "verdict.json"
    target.write_text("old\n", encoding="utf-8")

    destination = reserve_verdict_output(str(target), stdout=io.StringIO())
    destination.emit("new\n")
    destination.close()

    assert target.read_text(encoding="utf-8") == "new\n"
    assert _names(tmp_path) == ["verdict.json"]


def test_a_destination_that_appeared_after_reservation_is_preserved(tmp_path: Path):
    """The absent -> present race. Assay never destroys an object it did not
    observe, and invents no fallback path to write to instead."""
    target = tmp_path / "verdict.json"
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())

    target.write_text("intruder\n", encoding="utf-8")

    with pytest.raises(AssayError) as caught:
        destination.emit("new verdict\n")

    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert target.read_text(encoding="utf-8") == "intruder\n"
    assert _names(tmp_path) == ["verdict.json"], "no fallback artifact, no temp residue"


def test_a_destination_replaced_by_a_different_object_is_preserved(tmp_path: Path):
    """The present -> changed race, which the absent -> present case above
    cannot cover: same name, different inode."""
    target = tmp_path / "verdict.json"
    target.write_text("original\n", encoding="utf-8")
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())

    target.unlink()
    target.write_text("someone else's\n", encoding="utf-8")

    with pytest.raises(AssayError, match="changed after it was reserved"):
        destination.emit("new\n")

    assert target.read_text(encoding="utf-8") == "someone else's\n"
    assert _names(tmp_path) == ["verdict.json"]


def test_a_destination_rewritten_in_place_is_preserved(tmp_path: Path):
    """Inode reuse: a writer that truncates and rewrites keeps the inode, so
    identity has to cover size/mtime too."""
    target = tmp_path / "verdict.json"
    target.write_text("original\n", encoding="utf-8")
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())

    os.utime(target, (0, 0))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("rewritten in place\n")

    with pytest.raises(AssayError, match="changed after it was reserved"):
        destination.emit("new\n")

    assert target.read_text(encoding="utf-8") == "rewritten in place\n"


# --- the state machine --------------------------------------------------


def test_emitting_twice_is_a_programmer_error_not_a_verdict(tmp_path: Path):
    """`RuntimeError`, deliberately: a caller emitting twice is a bug in
    assay, not a fact about the consumer's filesystem. Laundering it into
    `OUTPUT_WRITE_FAILED` would let a real defect ship as an ordinary
    artifact-write failure."""
    destination = reserve_verdict_output(str(tmp_path / "verdict.json"), stdout=io.StringIO())
    destination.emit("{}\n")

    with pytest.raises(RuntimeError, match="emits exactly once"):
        destination.emit("{}\n")


def test_emitting_after_close_is_a_programmer_error(tmp_path: Path):
    destination = reserve_verdict_output(str(tmp_path / "verdict.json"), stdout=io.StringIO())
    destination.close()

    with pytest.raises(RuntimeError):
        destination.emit("{}\n")


def test_close_is_idempotent_and_never_touches_the_destination(tmp_path: Path):
    target = tmp_path / "verdict.json"
    target.write_text("prior\n", encoding="utf-8")
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())

    destination.close()
    destination.close()

    assert target.read_text(encoding="utf-8") == "prior\n"


def test_the_context_manager_closes_on_exit(tmp_path: Path):
    target = tmp_path / "verdict.json"
    with reserve_verdict_output(str(target), stdout=io.StringIO()) as destination:
        assert isinstance(destination, VerdictOutput)
        assert destination.target == str(target)
        destination.emit("{}\n")

    assert target.read_text(encoding="utf-8") == "{}\n"


def test_a_failed_cleanup_does_not_replace_the_real_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`_unlink_own` swallows deliberately: both callers are already
    reporting something more actionable, and a second unlink attempt cannot
    repair anything. This pins that decision rather than leaving it as an
    incidental `except: pass`."""
    from assay import output

    target = tmp_path / "verdict.json"
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())
    target.write_text("intruder\n", encoding="utf-8")

    def exploding_unlink(*args, **kwargs):
        raise OSError("cleanup itself failed")

    monkeypatch.setattr(output.os, "unlink", exploding_unlink)

    # The destination-changed refusal still surfaces, not the cleanup error.
    with pytest.raises(AssayError, match="changed after it was reserved"):
        destination.emit("new\n")
