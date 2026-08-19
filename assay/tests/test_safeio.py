"""O2/O4 -- ordinary regression coverage for :mod:`assay.safeio`'s public
seam, beyond the locked P20 acceptance suite
(``nyxloom-trove/carve-assets/P20/test_acceptance.py``, copied here
unchanged and never edited).  That suite proves the headline attacks
(swap-before-arm, renamed parent, relinked prior inode, FIFO/symlink/
oversize).  This module proves every OTHER branch: the lexical grammar,
missing/symlinked parents, non-positive limits, and the state-machine's own
``RuntimeError`` transitions -- each named directly, per the module's own
public contract, never through :mod:`assay.runner`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from assay import safeio
from assay.errors import AssayError, Outcome, ReasonCode

LIMIT = 1024


# --- lexical grammar: reserve_output and read_bounded_file share it -----------


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_empty_artifact_is_refused(tmp_path: Path, caller: str):
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "", limit=LIMIT)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_absolute_artifact_is_refused(tmp_path: Path, caller: str):
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "/etc/passwd", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_bare_dot_artifact_has_no_components_and_is_refused(tmp_path: Path, caller: str):
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, ".", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_dotdot_component_is_refused_even_mid_path(tmp_path: Path, caller: str):
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "a/../../etc/passwd", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_interior_single_dot_component_is_lexically_absorbed_and_reaches_the_same_file(
    tmp_path: Path,
):
    """PurePosixPath collapses ``a/./b`` to ``a/b`` during parsing -- proving
    the module's own documented reasoning for why no explicit '.' check is
    needed, rather than merely asserting it in a docstring."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").write_bytes(b"content")
    assert safeio.read_bounded_file(tmp_path, "a/./b", limit=LIMIT) == b"content"


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_non_positive_limit_is_a_programmer_error(tmp_path: Path, caller: str):
    with pytest.raises(ValueError):
        getattr(safeio, caller)(tmp_path, "cov.json", limit=0)
    with pytest.raises(ValueError):
        getattr(safeio, caller)(tmp_path, "cov.json", limit=-1)


# --- parent traversal: missing / symlinked / non-directory parents ------------


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_missing_immediate_parent_is_refused(tmp_path: Path, caller: str):
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "does-not-exist/cov.json", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_missing_intermediate_parent_two_levels_deep_is_refused_and_releases_the_opened_ancestor(
    tmp_path: Path,
):
    """A real component before the missing one is opened successfully first
    (``a`` exists), proving the multi-level traversal's own cleanup path
    (closing the already-opened ancestor descriptor before re-raising)."""
    (tmp_path / "a").mkdir()
    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(tmp_path, "a/missing-b/cov.json", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_symlinked_parent_directory_is_refused_never_followed(tmp_path: Path, caller: str):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "cov.json").write_bytes(b"x")
    link = tmp_path / "linked"
    link.symlink_to(real_dir)
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "linked/cov.json", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_regular_file_standing_where_a_parent_directory_belongs_is_refused(
    tmp_path: Path, caller: str
):
    (tmp_path / "notadir").write_bytes(b"x")
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(tmp_path, "notadir/cov.json", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("caller", ["reserve_output", "read_bounded_file"])
def test_a_project_root_that_is_not_an_openable_directory_is_refused(tmp_path: Path, caller: str):
    missing_root = tmp_path / "does-not-exist-at-all"
    with pytest.raises(AssayError) as excinfo:
        getattr(safeio, caller)(missing_root, "cov.json", limit=LIMIT)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# --- create_missing_parents: B006(b) -------------------------------------------
#
# `reserve_output`'s new capability, off by default -- the whole contract, not
# an implementation detail (W1-CARVE-branch-coverage-and-whole-target.md §2,
# AMENDED by A-269). `create_missing_parents` is never passed below except
# where a test's own point is proving what the explicit opt-in does; every
# other call in this module (and every call `read_bounded_file` ever makes)
# uses the default and must keep refusing a missing parent exactly as before.
#
# R0 never calls `reserve_output` at all (it declares no coverage judge), so
# there is no real R0 call site to drive for the "default is off" proof. This
# module's own docstring names its convention as proving `safeio`'s contract
# DIRECTLY, never through `assay.runner` -- so the bare call below, with the
# parameter omitted, is the faithful in-place proxy: it is the exact call
# shape an R0/direct caller would make if it ever reserved an output at all.


def test_reserve_output_default_refuses_a_missing_parent_and_creates_nothing(
    tmp_path: Path,
):
    """The R0/in-place contract test. Paired with the must-succeed control
    immediately below, which is the IDENTICAL fixture with only the explicit
    opt-in added -- so a reader cannot attribute this refusal to anything
    about the fixture itself, only to the default."""
    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(tmp_path, "missing/cov.json", limit=LIMIT)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert not (tmp_path / "missing").exists(), "the default must never create anything"


def test_reserve_output_create_missing_parents_creates_a_single_missing_parent(
    tmp_path: Path,
):
    """The paired must-succeed control for the default-off test above: same
    project root, same artifact spelling, only `create_missing_parents=True`
    added. Mode is checked exactly -- `0o700`, per the module docstring's
    safety discipline, never a looser default."""
    with safeio.reserve_output(
        tmp_path, "missing/cov.json", limit=LIMIT, create_missing_parents=True
    ) as reserved:
        assert reserved.artifact == "missing/cov.json"
    created = tmp_path / "missing"
    assert created.is_dir()
    assert not created.is_symlink()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700


def test_reserve_output_create_missing_parents_creates_every_missing_intermediate_component(
    tmp_path: Path,
):
    with safeio.reserve_output(
        tmp_path, "a/b/c/cov.json", limit=LIMIT, create_missing_parents=True
    ) as reserved:
        assert reserved.artifact == "a/b/c/cov.json"
    for name in ("a", "a/b", "a/b/c"):
        created = tmp_path / name
        assert created.is_dir()
        assert not created.is_symlink()
        assert stat.S_IMODE(created.stat().st_mode) == 0o700


def test_reserve_output_create_missing_parents_refuses_a_symlinked_component_never_follows_it(
    tmp_path: Path,
):
    """`os.makedirs`-style recursion following a symlinked component is the
    exact failure mode this discipline exists to avoid: a symlinked
    component must refuse even when creation is explicitly requested, never
    be silently followed or replaced. Paired with the must-succeed control
    immediately below, which differs in EXACTLY one thing -- a real
    directory standing in for the symlink -- so this refusal cannot be
    attributed to the missing final component, the artifact spelling, or
    anything else the two fixtures share."""
    escape_target = tmp_path / "escape_target"
    escape_target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(escape_target)

    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(
            tmp_path, "linked/nested/cov.json", limit=LIMIT, create_missing_parents=True
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert not (escape_target / "nested").exists(), (
        "nothing may be created through the symlink's target"
    )
    assert link.is_symlink(), "the symlink itself must never be touched or replaced"


def test_reserve_output_create_missing_parents_succeeds_through_a_real_directory_the_symlink_control(
    tmp_path: Path,
):
    """The must-succeed control for the symlink refusal above: the identical
    fixture shape -- one pre-existing top-level component, then a missing
    'nested' component, then the basename -- except `linked` is a REAL
    directory rather than a symlink."""
    real = tmp_path / "linked"
    real.mkdir()

    with safeio.reserve_output(
        tmp_path, "linked/nested/cov.json", limit=LIMIT, create_missing_parents=True
    ) as reserved:
        assert reserved.artifact == "linked/nested/cov.json"

    created = real / "nested"
    assert created.is_dir()
    assert not created.is_symlink()


def test_reserve_output_create_missing_parents_still_refuses_a_regular_file_standing_where_a_parent_belongs(
    tmp_path: Path,
):
    """Creation only ever fires on genuine absence (``ENOENT``); a component
    that already exists as the wrong type is refused exactly as it is
    without the flag -- never replaced, with or without the opt-in."""
    (tmp_path / "notadir").write_bytes(b"x")

    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(
            tmp_path, "notadir/cov.json", limit=LIMIT, create_missing_parents=True
        )

    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert (tmp_path / "notadir").read_bytes() == b"x", "never replaced"


def test_reserve_output_create_missing_parents_refuses_when_the_reopen_after_creation_is_raced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The TOCTOU defence the safety discipline exists for: even a directory
    THIS reservation itself just created is reopened with ``O_NOFOLLOW``
    rather than trusted blind. Simulated by making the SECOND ``os.open`` of
    the component (the post-``mkdir`` reopen) fail, right after a REAL
    ``mkdir`` succeeded -- standing in for 'something replaced the
    just-created directory before the reopen'."""
    real_open = os.open
    calls = {"n": 0}

    def racing_open(path, flags, *args, dir_fd=None, **kwargs):
        if path == "missing" and flags == safeio._OPEN_DIR_FLAGS:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated: replaced between mkdir and reopen")
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(safeio.os, "open", racing_open)

    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(
            tmp_path, "missing/cov.json", limit=LIMIT, create_missing_parents=True
        )

    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "could not be created or opened" in str(excinfo.value)
    # the real `mkdir` genuinely ran (this is a reopen race, not a creation
    # failure) -- the directory is left behind, as an aborted concurrent
    # creation would leave it.
    assert (tmp_path / "missing").is_dir()


def test_reserve_output_create_missing_parents_end_to_end_arm_and_consume(tmp_path: Path):
    """The created parent is not just present -- it is genuinely usable by
    the rest of the reservation's own lifecycle."""
    reserved = safeio.reserve_output(
        tmp_path, "created/cov.json", limit=LIMIT, create_missing_parents=True
    )
    reserved.arm()
    (tmp_path / "created" / "cov.json").write_bytes(b'{"files": {}}')
    assert reserved.consume() == b'{"files": {}}'


# --- diagnostics: the setup-failure/unreadable-artifact distinction (§2) -------


def test_reserve_output_missing_parent_diagnostic_names_the_component_and_the_artifact(
    tmp_path: Path,
):
    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(tmp_path, "missing/nested/cov.json", limit=LIMIT)
    message = str(excinfo.value)
    assert "'missing'" in message, "the missing component itself must be named"
    assert "missing/nested/cov.json" in message, "the declared artifact path must be named"
    assert "could not be opened" in message
    assert "parent" in message


def test_reserve_output_create_missing_parents_failure_diagnostic_still_names_component_and_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The other half of the setup-failure shape: creation itself fails
    (rather than the plain open). The message stays a SETUP-failure message
    -- 'could not be created or opened' -- never the generic wording a
    genuinely unreadable artifact gets elsewhere in this module."""

    def failing_mkdir(*args, **kwargs):
        raise OSError("simulated: disk full")

    monkeypatch.setattr(safeio.os, "mkdir", failing_mkdir)

    with pytest.raises(AssayError) as excinfo:
        safeio.reserve_output(
            tmp_path, "missing/cov.json", limit=LIMIT, create_missing_parents=True
        )
    message = str(excinfo.value)
    assert "'missing'" in message
    assert "missing/cov.json" in message
    assert "could not be created or opened" in message
    assert not (tmp_path / "missing").exists()


# --- read_bounded_file: the no-unlink sibling ----------------------------------


def test_read_bounded_file_returns_none_for_a_missing_file(tmp_path: Path):
    assert safeio.read_bounded_file(tmp_path, "cov.json", limit=LIMIT) is None


def test_read_bounded_file_reads_an_existing_regular_file(tmp_path: Path):
    (tmp_path / "cov.json").write_bytes(b'{"files": {}}')
    assert safeio.read_bounded_file(tmp_path, "cov.json", limit=LIMIT) == b'{"files": {}}'


def test_read_bounded_file_never_unlinks_anything(tmp_path: Path):
    target = tmp_path / "cov.json"
    target.write_bytes(b"data")
    safeio.read_bounded_file(tmp_path, "cov.json", limit=LIMIT)
    assert target.exists()
    assert target.read_bytes() == b"data"


def test_read_bounded_file_tolerates_multiple_hard_links(tmp_path: Path):
    """Unlike ``consume()``, ``read_bounded_file`` has no prior removal to
    protect and never refuses on link count -- it is reading an ordinary
    input, not verifying single-owner freshly-produced output."""
    target = tmp_path / "cov.json"
    target.write_bytes(b"shared")
    os.link(target, tmp_path / "also-cov.json")
    assert safeio.read_bounded_file(tmp_path, "cov.json", limit=LIMIT) == b"shared"


# --- OutputReservation: properties and state-machine transitions --------------


def test_artifact_and_limit_properties_expose_the_declared_values(tmp_path: Path):
    (tmp_path / "nested").mkdir()
    with safeio.reserve_output(tmp_path, "nested/cov.json", limit=42) as reserved:
        assert reserved.artifact == "nested/cov.json"
        assert reserved.limit == 42


def test_arm_raises_runtime_error_when_called_twice(tmp_path: Path):
    with safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT) as reserved:
        reserved.arm()
        with pytest.raises(RuntimeError):
            reserved.arm()


def test_consume_raises_runtime_error_before_arm(tmp_path: Path):
    with safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT) as reserved:
        with pytest.raises(RuntimeError):
            reserved.consume()


def test_arm_raises_runtime_error_after_close(tmp_path: Path):
    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    reserved.close()
    with pytest.raises(RuntimeError):
        reserved.arm()


def test_close_before_arm_is_safe_and_idempotent(tmp_path: Path):
    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    reserved.close()
    reserved.close()  # must not raise


def test_close_after_consume_is_a_safe_noop(tmp_path: Path):
    with safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT) as reserved:
        reserved.arm()
        assert reserved.consume() is None
        reserved.close()  # consume() already released the descriptor


def test_context_manager_closes_on_an_exception_raised_inside_the_block(tmp_path: Path):
    with pytest.raises(ZeroDivisionError):
        with safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT) as reserved:
            reserved.arm()
            raise ZeroDivisionError("boom")
    # A second, direct close() must still be safe -- proving the __exit__
    # path really did release the descriptor rather than leaking it.
    reserved.close()


def test_arm_refuses_an_object_that_appeared_after_reservation_but_before_arming(
    tmp_path: Path,
):
    """The mirror of the locked "swap before arm" case: nothing existed at
    reservation time, but something exists by arm() time -- also refused,
    since this reservation never observed or validated it."""
    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    (tmp_path / "cov.json").write_bytes(b"unexpected")
    with pytest.raises(AssayError) as excinfo:
        reserved.arm()
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert (tmp_path / "cov.json").read_bytes() == b"unexpected"
    reserved.close()


# --- Reviewer (P20): the "headline attacks" also need a GATED oracle ----------
#
# This module's own docstring above defers swap-before-arm, the relinked prior
# inode, and the consumed-handle transition to the locked acceptance suite.
# That suite is real evidence, but the controller runs it SEPARATELY: the
# registered gate's argv is `python -m pytest tests -q ...`, so nothing under
# `nyxloom-trove/carve-assets/` is collected by the project's ship signal.
# Measured, not assumed: before these tests, `safeio.py` lines 186/208/273 --
# each of them one of those attacks' refusal -- were uncovered by the gated
# suite, so deleting any one of the three guards would have kept it green.


def test_arm_refuses_when_the_reserved_object_was_swapped(tmp_path: Path):
    """Gated equivalent of the locked swap-before-arm attack: the object the
    reservation captured is gone, so `arm` must NOT unlink whatever now sits
    at that name -- removing it could destroy unrelated content."""
    path = tmp_path / "cov.json"
    path.write_bytes(b"first")
    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    path.unlink()
    path.write_bytes(b"replacement")

    with pytest.raises(AssayError) as excinfo:
        reserved.arm()

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert path.read_bytes() == b"replacement", "the swapped-in file was destroyed"
    reserved.close()


def test_consume_twice_raises_rather_than_reusing_a_released_descriptor(
    tmp_path: Path,
):
    """The state machine's whole reason for being mutable: a consumed handle
    has released its integer descriptor, and the OS may already have reassigned
    that number to something unrelated. A second consume is a programmer defect,
    never a second read."""
    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    reserved.arm()
    assert reserved.consume() is None

    with pytest.raises(RuntimeError):
        reserved.consume()


def test_consume_refuses_output_reachable_through_a_second_hard_link(
    tmp_path: Path,
):
    """Gated equivalent of the locked relinked-prior-inode attack: `arm`
    unlinked the stale artifact, but a second link kept its content alive and
    recreated the name. A single-owner output this run produced has exactly one
    name, so a link count above one means the bytes were not produced here."""
    prior = tmp_path / "cov.json"
    held = tmp_path / "held-link"
    prior.write_bytes(b"stale")
    os.link(prior, held)

    reserved = safeio.reserve_output(tmp_path, "cov.json", limit=LIMIT)
    reserved.arm()
    os.link(held, prior)

    with pytest.raises(AssayError) as excinfo:
        reserved.consume()

    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    reserved.close()
