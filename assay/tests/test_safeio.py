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
