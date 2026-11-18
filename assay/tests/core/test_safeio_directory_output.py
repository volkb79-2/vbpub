"""B245 / U1 -- the directory-artifact reservation in :mod:`assay.safeio`.

A tool such as cargo-mutants writes a DIRECTORY (``mutants.out`` holding
``outcomes.json`` and ``diff/*.diff``) into an output directory ``<out>`` that
assay reserves empty. The reservation holds ``<out>`` itself, never the child,
because the tool renames a pre-existing child to ``mutants.out.old`` and a held
child descriptor would follow the rotated inode (design section 1.1).

Every property here is proved on a real temporary tree, with no fake of the
reservation itself: the only monkeypatch is ``os.open`` where a race between two
syscalls of one ``consume`` has to be placed deterministically.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from pathlib import Path
from types import MappingProxyType

import pytest

from assay import safeio
from assay.errors import AssayError, Outcome, ReasonCode

CHILD = "mutants.out"
ROTATED = "mutants.out.old"
REPORT = "outcomes.json"
MEMBER = "src__lib.rs_line_9_col_5_001.diff"
REPORT_BYTES = b'{"outcomes": []}'
DIFF_BYTES = b"--- a/src/lib.rs\n+++ b/src/lib.rs\n"

Members = Callable[[bytes], Sequence[str]]


@pytest.fixture(autouse=True)
def _no_descriptor_leak():
    """Every test in this module leaves the process with the descriptors it
    started with, whatever it refused."""
    before = len(os.listdir("/proc/self/fd"))
    yield
    assert len(os.listdir("/proc/self/fd")) == before


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _limits(**override: int) -> dict[str, int]:
    limits = {
        "report_limit": 4096,
        "member_limit": 64,
        "max_members": 8,
        "max_total_bytes": 256,
    }
    limits.update(override)
    return limits


def _reserve(
    root: Path, out_dir: str = "out", *, create_missing_parents: bool = True, **limits: int
) -> safeio.DirectoryArtifactReservation:
    return safeio.reserve_directory_output(
        root,
        out_dir,
        child=CHILD,
        rotated=ROTATED,
        report_name=REPORT,
        create_missing_parents=create_missing_parents,
        **_limits(**limits),
    )


def _armed(root: Path, **limits: int) -> safeio.DirectoryArtifactReservation:
    reservation = _reserve(root, **limits)
    reservation.arm()
    return reservation


def _write_child(
    out: Path, *, members: dict[str, bytes] | None = None, report: bytes = REPORT_BYTES
) -> Path:
    """What the tool leaves behind: ``mutants.out/{outcomes.json,diff/*}``."""
    child = out / CHILD
    (child / "diff").mkdir(parents=True)
    (child / REPORT).write_bytes(report)
    for name, data in (members if members is not None else {MEMBER: DIFF_BYTES}).items():
        (child / "diff" / name).write_bytes(data)
    return child


def _names(*members: str) -> Members:
    return lambda raw: [f"diff/{member}" for member in members]


def _one_member() -> Members:
    return _names(MEMBER)


def _assert_refused(excinfo: pytest.ExceptionInfo[AssayError]) -> str:
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    return str(excinfo.value)


# --- reserve ------------------------------------------------------------------


def test_reserve_creates_out_and_holds_it_not_child(tmp_path: Path):
    reservation = _reserve(tmp_path)
    out = tmp_path / "out"

    assert out.is_dir()
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert list(out.iterdir()) == [], "reserve creates <out> EMPTY and nothing in it"
    assert reservation.artifact == f"out/{CHILD}"
    held = os.fstat(reservation._held_fd)  # the descriptor IS <out>, not the child
    assert (held.st_dev, held.st_ino) == (out.stat().st_dev, out.stat().st_ino)

    reservation.arm()
    assert list(out.iterdir()) == [], "arm writes nothing into <out> either"

    # The tool then creates the child; the held <out> descriptor reads it, which
    # a held child descriptor could not once the tool rotates it.
    child = _write_child(out)
    artifact = reservation.consume(members=_one_member())
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert artifact.identity == (child.stat().st_dev, child.stat().st_ino)


def test_reserve_creates_every_missing_intermediate_component(tmp_path: Path):
    reservation = _reserve(tmp_path, "target/assay/out")
    try:
        assert (tmp_path / "target" / "assay" / "out").is_dir()
        assert reservation.artifact == f"target/assay/out/{CHILD}"
    finally:
        reservation.close()


def test_reserve_default_refuses_a_missing_out_and_creates_nothing(tmp_path: Path):
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path, create_missing_parents=False)
    assert "'out'" in _assert_refused(excinfo)
    assert not (tmp_path / "out").exists()


def test_reserve_accepts_an_existing_empty_out_without_creating_anything(tmp_path: Path):
    (tmp_path / "out").mkdir()
    reservation = _reserve(tmp_path, create_missing_parents=False)
    reservation.close()
    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "regular-file"])
def test_reserve_refuses_an_out_that_is_not_a_real_directory(tmp_path: Path, kind: str):
    if kind == "symlink":
        (tmp_path / "real").mkdir()
        (tmp_path / "out").symlink_to(tmp_path / "real")
    else:
        (tmp_path / "out").write_text("x")
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path)
    _assert_refused(excinfo)


@pytest.mark.parametrize("present", [CHILD, ROTATED])
def test_reserve_refuses_preexisting_child(tmp_path: Path, present: str):
    """Only a commit or a link can have put it into a fresh snapshot, and the
    tool would rotate or reuse it: refused, left exactly as found."""
    out = tmp_path / "out"
    (out / present).mkdir(parents=True)
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path)
    message = _assert_refused(excinfo)
    assert present in message
    assert "already exists" in message
    assert (out / present).is_dir()


def test_reserve_refuses_preexisting_rotated(tmp_path: Path):
    """The rotated name present with NO child: the second half of the check."""
    out = tmp_path / "out"
    out.mkdir()
    (out / ROTATED).write_text("leftover")
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path)
    assert ROTATED in _assert_refused(excinfo)
    assert (out / ROTATED).read_text() == "leftover"


@pytest.mark.parametrize("entry", ["notes.txt", ".keep", ".hidden-dir/"])
def test_reserve_refuses_nonempty_out(tmp_path: Path, entry: str):
    """The stop-gate precondition (design section 8 item 1): the tool copies a
    pre-created <out> into every mutant tree, so only an EMPTY directory may be
    reserved, hidden entries included. Whatever is there is left untouched."""
    out = tmp_path / "out"
    out.mkdir()
    if entry.endswith("/"):
        (out / entry).mkdir()
    else:
        (out / entry).write_text("a test could read this")
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path)
    message = _assert_refused(excinfo)
    assert "not empty at reservation" in message
    assert [p.name for p in out.iterdir()] == [entry.rstrip("/")]
    if not entry.endswith("/"):
        assert (out / entry).read_text() == "a test could read this"


def test_reserve_refuses_an_out_that_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "out").mkdir()

    real = os.listdir

    def broken_listdir(path):
        if isinstance(path, int):  # the held descriptor; the fd-leak fixture lists a path
            raise PermissionError(13, "no listing")
        return real(path)

    monkeypatch.setattr(os, "listdir", broken_listdir)
    with pytest.raises(AssayError) as excinfo:
        _reserve(tmp_path)
    assert "could not be listed" in _assert_refused(excinfo)


@pytest.mark.parametrize(
    "override",
    [
        {"report_limit": 0},
        {"member_limit": 0},
        {"max_members": 0},
        {"max_total_bytes": -1},
    ],
)
def test_reserve_rejects_non_positive_bounds_before_touching_anything(
    tmp_path: Path, override: dict[str, int]
):
    with pytest.raises(ValueError, match="must be positive"):
        _reserve(tmp_path, **override)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("child", ""),
        ("child", "."),
        ("child", ".."),
        ("child", "a/b"),
        ("rotated", "x\0y"),
        ("report_name", "../outcomes.json"),
    ],
)
def test_reserve_rejects_a_name_that_is_not_one_component(tmp_path: Path, label: str, value: str):
    names = {"child": CHILD, "rotated": ROTATED, "report_name": REPORT, label: value}
    with pytest.raises(ValueError, match=label):
        safeio.reserve_directory_output(tmp_path, "out", **names, **_limits())
    assert not (tmp_path / "out").exists()


# --- arm ----------------------------------------------------------------------


@pytest.mark.parametrize("appears", [CHILD, ROTATED])
def test_arm_refuses_child_appearing_after_reserve(tmp_path: Path, appears: str):
    reservation = _reserve(tmp_path)
    (tmp_path / "out" / appears).mkdir()
    try:
        with pytest.raises(AssayError) as excinfo:
            reservation.arm()
        message = _assert_refused(excinfo)
        assert appears in message
        assert "appeared after reservation but before arming" in message
        assert (tmp_path / "out" / appears).is_dir(), "arm never unlinks"
        with pytest.raises(RuntimeError, match="not armed"):
            reservation.consume(members=_one_member())
    finally:
        reservation.close()


def test_arm_twice_and_arm_after_close_are_runtime_errors(tmp_path: Path):
    reservation = _reserve(tmp_path)
    reservation.arm()
    with pytest.raises(RuntimeError, match="already armed"):
        reservation.arm()
    reservation.close()
    with pytest.raises(RuntimeError, match="already closed"):
        reservation.arm()


# --- consume: the ordinary and the absent case --------------------------------


def test_consume_reads_report_and_members_in_sorted_order(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(
        tmp_path / "out", members={"b.diff": b"B", "a.diff": b"A", "c.diff": b"C"}
    )
    seen: list[bytes] = []

    def members(raw: bytes) -> list[str]:
        seen.append(raw)
        return ["diff/c.diff", "diff/a.diff", "diff/b.diff"]

    artifact = reservation.consume(members=members)

    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert seen == [REPORT_BYTES], "the members callable gets the report BYTES, once"
    assert artifact.artifact == f"out/{CHILD}"
    assert artifact.report == REPORT_BYTES
    assert list(artifact.members) == ["diff/a.diff", "diff/b.diff", "diff/c.diff"]
    assert dict(artifact.members) == {"diff/a.diff": b"A", "diff/b.diff": b"B", "diff/c.diff": b"C"}
    assert artifact.identity == (child.stat().st_dev, child.stat().st_ino)
    assert isinstance(artifact.members, MappingProxyType)
    with pytest.raises(TypeError):
        artifact.members["diff/x.diff"] = b""  # type: ignore[index]
    with pytest.raises(AttributeError):
        artifact.report = b""  # type: ignore[misc]


def test_consume_with_no_members_needs_no_diff_directory(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out", members={})
    (child / "diff").rmdir()
    artifact = reservation.consume(members=lambda raw: [])
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert dict(artifact.members) == {}


def test_consume_absent_child_returns_absent(tmp_path: Path):
    reservation = _armed(tmp_path)
    result = reservation.consume(members=_one_member())
    assert result is safeio.ABSENT
    assert result is not None
    assert isinstance(result, safeio.Absent)
    assert list((tmp_path / "out").iterdir()) == []


def test_consume_twice_runtime_error(tmp_path: Path):
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    reservation.consume(members=_one_member())
    with pytest.raises(RuntimeError, match="already closed or consumed"):
        reservation.consume(members=_one_member())


def test_consume_twice_after_absent_is_also_a_runtime_error(tmp_path: Path):
    reservation = _armed(tmp_path)
    assert reservation.consume(members=_one_member()) is safeio.ABSENT
    with pytest.raises(RuntimeError, match="already closed or consumed"):
        reservation.consume(members=_one_member())


def test_consume_before_arm_is_a_runtime_error(tmp_path: Path):
    reservation = _reserve(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="not armed"):
            reservation.consume(members=_one_member())
    finally:
        reservation.close()


def test_consume_after_close_is_a_runtime_error(tmp_path: Path):
    reservation = _armed(tmp_path)
    reservation.close()
    with pytest.raises(RuntimeError, match="already closed or consumed"):
        reservation.consume(members=_one_member())


# --- consume: rotation --------------------------------------------------------


def test_consume_real_rotation_refused(tmp_path: Path):
    """The real cargo-mutants layout after a rerun into one --output: the new
    run's mutants.out beside a mutants.out.old. The rotated copy proves the
    command ran twice (or something else wrote there), so the evidence is
    refused even though the new child is complete and readable."""
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out)
    shutil.copytree(out / CHILD, out / ROTATED)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    message = _assert_refused(excinfo)
    assert ROTATED in message
    assert "rotated" in message


# --- consume: symlinks and hard links -----------------------------------------


def _decoy_tree(root: Path) -> Path:
    """A complete, readable tree OUTSIDE <out> that a followed symlink would read."""
    decoy = root / "decoy"
    (decoy / "diff").mkdir(parents=True)
    (decoy / REPORT).write_bytes(b"decoy report")
    (decoy / "diff" / MEMBER).write_bytes(b"decoy diff")
    return decoy


def test_consume_child_symlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    (tmp_path / "out" / CHILD).symlink_to(_decoy_tree(tmp_path))
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "not a directory" in _assert_refused(excinfo)


def test_consume_child_that_is_a_regular_file_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    (tmp_path / "out" / CHILD).write_text("not a directory")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "not a directory" in _assert_refused(excinfo)


def test_consume_report_symlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    (child / REPORT).unlink()
    (child / REPORT).symlink_to(_decoy_tree(tmp_path) / REPORT)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert REPORT in _assert_refused(excinfo)


def test_consume_diff_symlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    (child / "diff" / MEMBER).unlink()
    (child / "diff" / MEMBER).symlink_to(_decoy_tree(tmp_path) / "diff" / MEMBER)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert MEMBER in _assert_refused(excinfo)


def test_consume_diff_dir_symlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    shutil.rmtree(child / "diff")
    (child / "diff").symlink_to(_decoy_tree(tmp_path) / "diff")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "diff" in _assert_refused(excinfo)


def test_consume_report_hardlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    os.link(child / REPORT, tmp_path / "second-name")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "hard links" in _assert_refused(excinfo)


def test_consume_member_hardlink_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    os.link(child / "diff" / MEMBER, tmp_path / "second-name")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "hard links" in _assert_refused(excinfo)


# --- consume: the held <out> was replaced -------------------------------------


def test_consume_out_rmrf_recreated_refused(tmp_path: Path):
    """A tool (or build step) that rm -rf's <out> and recreates it writes a
    complete artifact at the declared path that the held descriptor cannot
    reach. B049's own text, shared with OutputReservation."""
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    shutil.rmtree(out)
    out.mkdir()
    _write_child(out)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    message = _assert_refused(excinfo)
    assert "deleted and recreated" in message
    assert "'out'" in message
    assert f"out/{CHILD}" in message


def test_consume_out_renamed_and_replaced_refused(tmp_path: Path):
    """A rename-away keeps st_nlink above zero, so only the by-name identity
    comparison sees a different directory now answering to the path."""
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    out.rename(tmp_path / "out.moved")
    out.mkdir()
    _write_child(out)
    assert os.fstat(reservation._held_fd).st_nlink > 0
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "renamed away or replaced" in _assert_refused(excinfo)


def test_consume_out_renamed_away_without_replacement_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    (tmp_path / "out").rename(tmp_path / "out.moved")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "renamed away or replaced" in _assert_refused(excinfo)


# --- consume: races inside one consume ----------------------------------------


def _patch_os_open_once(
    monkeypatch: pytest.MonkeyPatch, name: str, action: Callable[[], None]
) -> None:
    """Run *action* just before the first ``os.open(name, dir_fd=...)``: the
    window between the lstat of the child and its open."""
    real_open = os.open
    fired: list[bool] = []

    def fake_open(path, flags, *args, **kwargs):
        if path == name and kwargs.get("dir_fd") is not None and not fired:
            fired.append(True)
            action()
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fake_open)


def test_consume_child_swapped_between_lstat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out)

    def swap() -> None:
        (out / CHILD).rename(out / "moved-away")  # the original inode stays alive
        _write_child(out)  # a complete child at the same name, a NEW inode

    _patch_os_open_once(monkeypatch, CHILD, swap)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "swapped between inspection and opening" in _assert_refused(excinfo)


def test_consume_child_replaced_by_a_symlink_between_lstat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out)

    def swap() -> None:
        (out / CHILD).rename(out / "moved-away")
        (out / CHILD).symlink_to(out / "moved-away")

    _patch_os_open_once(monkeypatch, CHILD, swap)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "could not be opened safely" in _assert_refused(excinfo)


def test_consume_child_replaced_while_it_was_being_read_refused(tmp_path: Path):
    """The final re-lstat: the identity opened at the start is no longer what
    answers to the child's name once the members have been read."""
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out)

    def members(raw: bytes) -> list[str]:
        (out / CHILD).rename(out / "moved-away")
        _write_child(out)
        return [f"diff/{MEMBER}"]

    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=members)
    assert "replaced while it was being read" in _assert_refused(excinfo)


def test_consume_child_removed_while_it_was_being_read_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out)

    def members(raw: bytes) -> list[str]:
        (out / CHILD).rename(out / "moved-away")
        return [f"diff/{MEMBER}"]

    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=members)
    assert "replaced while it was being read" in _assert_refused(excinfo)


# --- consume: a child without its report --------------------------------------


def test_consume_missing_report_in_present_child_refused(tmp_path: Path):
    """The child exists but the tool died before writing outcomes.json. A
    missing report is a refusal, never an absent result: the child's presence
    is the evidence that the run began."""
    reservation = _armed(tmp_path)
    (tmp_path / "out" / CHILD).mkdir()
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    message = _assert_refused(excinfo)
    assert REPORT in message
    assert "died mid-run" in message


def test_consume_oversize_report_refused(tmp_path: Path):
    reservation = _armed(tmp_path, report_limit=8)
    _write_child(tmp_path / "out", report=b"x" * 9)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "8-byte bound" in _assert_refused(excinfo)


# --- members: the name grammar ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../x.diff",
        "diff/../x.diff",
        "diff/a/b.diff",
        "diff//x.diff",
        "/etc/x.diff",
        "x.diff",
        "diff/x.txt",
        "diff/x",
        "diff/a\0b.diff",
        "diff/a\\b.diff",
        "diff/..",
        "diff/.",
        "diff/",
        "diff",
        "other/x.diff",
        "",
    ],
)
def test_member_name_grammar_refuses(tmp_path: Path, name: str):
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    _write_child(out, members={"x.diff": b"inside", "x.txt": b"inside"})
    (out / "x.diff").write_bytes(b"outside the child, a lax reader would take it")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=lambda raw: [name])
    assert "is not exactly 'diff/<name>.diff'" in _assert_refused(excinfo)


def test_member_name_that_is_not_a_string_is_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=lambda raw: [None])  # type: ignore[list-item]
    assert "is not exactly 'diff/<name>.diff'" in _assert_refused(excinfo)


@pytest.mark.parametrize(
    "base",
    [
        "src__módulo.rs_line_2_col_5.diff",
        "src__with space.rs_line_1_col_1.diff",
        "src__lib.rs_line_9_col_5_001.diff",
        "..diff",
        ".diff",
        "a b\tc.diff",
    ],
)
def test_member_name_grammar_accepts(tmp_path: Path, base: str):
    """Non-ASCII, a space and a leading dot are all one safe component: the
    member is opened by name through the held descriptor and never globbed."""
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out", members={base: b"payload"})
    artifact = reservation.consume(members=_names(base))
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert dict(artifact.members) == {f"diff/{base}": b"payload"}


def test_member_name_grammar_accepts_the_full_measured_name_set(tmp_path: Path):
    reservation = _armed(tmp_path)
    base_names = [MEMBER, "src__módulo.rs_line_2_col_5.diff", "src__lib.rs_line_9_col_5_002.diff"]
    _write_child(tmp_path / "out", members={name: name.encode() for name in base_names})
    artifact = reservation.consume(members=_names(*base_names))
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert sorted(artifact.members) == sorted(f"diff/{name}" for name in base_names)


def test_duplicate_member_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_names(MEMBER, MEMBER))
    message = _assert_refused(excinfo)
    assert "named twice" in message
    assert MEMBER in message


def test_member_missing_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_names(MEMBER, "gone.diff"))
    message = _assert_refused(excinfo)
    assert "gone.diff" in message
    assert "missing" in message


def test_member_named_but_diff_directory_absent_refused(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out", members={})
    (child / "diff").rmdir()
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "diff could not be opened safely" in _assert_refused(excinfo)


# --- members: the three bounds ------------------------------------------------


def test_member_count_bound(tmp_path: Path):
    """Exactly max_members is accepted; one more refuses before any member is
    opened (the bound is on the report's claim, not on what is read)."""
    names = {f"m{i}.diff": b"x" for i in range(3)}
    reservation = _armed(tmp_path, max_members=3)
    _write_child(tmp_path / "out", members=names)
    artifact = reservation.consume(members=_names(*names))
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert len(artifact.members) == 3

    over = _armed_in(tmp_path, "out2", max_members=2)
    _write_child(tmp_path / "out2", members=names)
    with pytest.raises(AssayError) as excinfo:
        over.consume(members=_names(*names))
    message = _assert_refused(excinfo)
    assert "3 members" in message
    assert "2-member bound" in message


def _armed_in(root: Path, out_dir: str, **limits: int) -> safeio.DirectoryArtifactReservation:
    reservation = _reserve(root, out_dir, **limits)
    reservation.arm()
    return reservation


def test_member_size_bound(tmp_path: Path):
    """A member of exactly member_limit bytes reads; one byte more refuses."""
    reservation = _armed(tmp_path, member_limit=16, max_total_bytes=1024)
    _write_child(tmp_path / "out", members={"fits.diff": b"x" * 16})
    artifact = reservation.consume(members=_names("fits.diff"))
    assert isinstance(artifact, safeio.DirectoryArtifact)

    over = _armed_in(tmp_path, "out2", member_limit=16, max_total_bytes=1024)
    _write_child(tmp_path / "out2", members={"big.diff": b"x" * 17})
    with pytest.raises(AssayError) as excinfo:
        over.consume(members=_names("big.diff"))
    assert "16-byte bound" in _assert_refused(excinfo)


def test_aggregate_size_bound(tmp_path: Path):
    """Every member is within member_limit, but together they cross
    max_total_bytes: reading stops with a refusal at the member that crosses."""
    members = {"a.diff": b"x" * 10, "b.diff": b"x" * 10, "c.diff": b"x" * 10}
    fits = _armed(tmp_path, member_limit=16, max_total_bytes=30)
    _write_child(tmp_path / "out", members=members)
    artifact = fits.consume(members=_names(*members))
    assert isinstance(artifact, safeio.DirectoryArtifact)
    assert sum(len(data) for data in artifact.members.values()) == 30

    over = _armed_in(tmp_path, "out2", member_limit=16, max_total_bytes=29)
    _write_child(tmp_path / "out2", members=members)
    with pytest.raises(AssayError) as excinfo:
        over.consume(members=_names(*members))
    assert "29-byte aggregate bound" in _assert_refused(excinfo)


def test_aggregate_bound_stops_reading_at_the_crossing_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    members = {"a.diff": b"x" * 10, "b.diff": b"x" * 10, "c.diff": b"x" * 10}
    reservation = _armed(tmp_path, member_limit=16, max_total_bytes=15)
    _write_child(tmp_path / "out", members=members)
    opened: list[str] = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    with pytest.raises(AssayError):
        reservation.consume(members=_names(*members))
    monkeypatch.undo()
    assert "a.diff" in opened and "b.diff" in opened
    assert "c.diff" not in opened, "reading stopped when the aggregate was exceeded"


# --- close --------------------------------------------------------------------


def test_close_idempotent_never_unlinks(tmp_path: Path):
    out = tmp_path / "out"
    reservation = _armed(tmp_path)
    child = _write_child(out)
    (out / ROTATED).mkdir()
    snapshot = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    before = _fd_count()

    reservation.close()
    after_first = _fd_count()
    reservation.close()
    reservation.close()

    assert after_first == before - 1, "the first close released the one held descriptor"
    assert _fd_count() == after_first, "later closes are no-ops"
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == snapshot
    assert (child / REPORT).read_bytes() == REPORT_BYTES


def test_close_before_arm_and_after_consume_are_safe(tmp_path: Path):
    unarmed = _reserve(tmp_path)
    unarmed.close()
    unarmed.close()

    consumed = _armed_in(tmp_path, "out2")
    _write_child(tmp_path / "out2")
    consumed.consume(members=_one_member())
    consumed.close()


# --- descriptors --------------------------------------------------------------


def _scenario_rotated(root: Path, out: Path) -> Members:
    _write_child(out)
    (out / ROTATED).mkdir()
    return _one_member()


def _scenario_child_symlink(root: Path, out: Path) -> Members:
    (out / CHILD).symlink_to(_decoy_tree(root))
    return _one_member()


def _scenario_missing_report(root: Path, out: Path) -> Members:
    (out / CHILD).mkdir()
    return _one_member()


def _scenario_report_hardlink(root: Path, out: Path) -> Members:
    child = _write_child(out)
    os.link(child / REPORT, root / "second-name")
    return _one_member()


def _scenario_diff_dir_symlink(root: Path, out: Path) -> Members:
    child = _write_child(out)
    shutil.rmtree(child / "diff")
    (child / "diff").symlink_to(_decoy_tree(root) / "diff")
    return _one_member()


def _scenario_member_symlink(root: Path, out: Path) -> Members:
    child = _write_child(out)
    (child / "diff" / MEMBER).unlink()
    (child / "diff" / MEMBER).symlink_to(_decoy_tree(root) / "diff" / MEMBER)
    return _one_member()


def _scenario_member_missing(root: Path, out: Path) -> Members:
    _write_child(out)
    return _names(MEMBER, "gone.diff")


def _scenario_member_hardlink_after_good_one(root: Path, out: Path) -> Members:
    child = _write_child(out, members={MEMBER: b"ok", "second.diff": b"ok"})
    os.link(child / "diff" / "second.diff", root / "second-name")
    return _names(MEMBER, "second.diff")


def _scenario_bad_grammar(root: Path, out: Path) -> Members:
    _write_child(out)
    return lambda raw: ["diff/../x.diff"]


def _scenario_duplicate(root: Path, out: Path) -> Members:
    _write_child(out)
    return _names(MEMBER, MEMBER)


def _scenario_too_many(root: Path, out: Path) -> Members:
    _write_child(out)
    return _names(*(f"{i}.diff" for i in range(9)))


def _scenario_aggregate(root: Path, out: Path) -> Members:
    names = {f"{i}.diff": b"x" * 64 for i in range(5)}
    _write_child(out, members=names)
    return _names(*names)


def _scenario_out_rmrf(root: Path, out: Path) -> Members:
    shutil.rmtree(out)
    out.mkdir()
    _write_child(out)
    return _one_member()


def _scenario_out_renamed(root: Path, out: Path) -> Members:
    out.rename(root / "out.moved")
    out.mkdir()
    _write_child(out)
    return _one_member()


SCENARIOS = [
    _scenario_rotated,
    _scenario_child_symlink,
    _scenario_missing_report,
    _scenario_report_hardlink,
    _scenario_diff_dir_symlink,
    _scenario_member_symlink,
    _scenario_member_missing,
    _scenario_member_hardlink_after_good_one,
    _scenario_bad_grammar,
    _scenario_duplicate,
    _scenario_too_many,
    _scenario_aggregate,
    _scenario_out_rmrf,
    _scenario_out_renamed,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda fn: fn.__name__.removeprefix("_scenario_"))
def test_fds_are_closed_on_every_refusal(tmp_path: Path, scenario: Callable[[Path, Path], Members]):
    before = _fd_count()
    reservation = _armed(tmp_path)
    assert _fd_count() == before + 1, "a reservation holds exactly one descriptor"
    members = scenario(tmp_path, tmp_path / "out")

    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=members)

    _assert_refused(excinfo)
    assert _fd_count() == before, "consume closed the held, child and diff descriptors"
    reservation.close()
    assert _fd_count() == before


def test_fds_are_closed_when_the_members_callable_raises(tmp_path: Path):
    before = _fd_count()
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")

    def broken(raw: bytes) -> list[str]:
        raise KeyError("the parser rejected the report")

    with pytest.raises(KeyError):
        reservation.consume(members=broken)
    assert _fd_count() == before
    with pytest.raises(RuntimeError, match="already closed or consumed"):
        reservation.consume(members=_one_member())


def test_fds_are_closed_after_success_absent_and_failed_reserve(tmp_path: Path):
    before = _fd_count()
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    reservation.consume(members=_one_member())
    assert _fd_count() == before

    absent = _armed_in(tmp_path, "out2")
    assert absent.consume(members=_one_member()) is safeio.ABSENT
    assert _fd_count() == before

    (tmp_path / "out3").mkdir()
    (tmp_path / "out3" / "junk").write_text("x")
    with pytest.raises(AssayError):
        _reserve(tmp_path, "out3")
    assert _fd_count() == before, "a refused reserve released the descriptor it had opened"


# --- review round 1: member names that are not text, or alias another name -----


@pytest.mark.parametrize(
    "bad",
    [["diff/x.diff"], {"diff/x.diff": 1}, b"diff/x.diff", 7, None],
    ids=["list-unhashable", "dict-unhashable", "bytes", "int", "none"],
)
def test_non_str_member_name_refuses_as_assay_error_not_type_error(tmp_path: Path, bad: object):
    """An unhashable name used to reach the duplicate check and raise a raw
    TypeError; the grammar now runs first, so every non-str refuses the same way."""
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out", members={"x.diff": b"x"})
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=lambda raw: [bad])  # type: ignore[list-item,return-value]
    assert "is not exactly 'diff/<name>.diff'" in _assert_refused(excinfo)


def test_lone_surrogate_member_name_refuses_as_assay_error_not_unicode_error(tmp_path: Path):
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=lambda raw: ["diff/\ud800.diff"])
    assert "strict UTF-8" in _assert_refused(excinfo)


def test_surrogateescape_alias_of_a_real_file_is_refused_not_read(tmp_path: Path):
    """CHOSEN: refuse every name that is not strict UTF-8. '\\udcff' would
    otherwise open the REAL file whose name holds the raw byte 0xff, and two
    distinct strings could name one file past the duplicate check."""
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out", members={})
    raw_name = b"\xff.diff"
    (child / "diff" / os.fsdecode(raw_name)).write_bytes(b"read through an alias")
    assert os.fsencode("\udcff.diff") == raw_name, "precondition: the alias is real"
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=lambda raw: ["diff/\udcff.diff"])
    assert "strict UTF-8" in _assert_refused(excinfo)


def test_two_names_aliasing_one_file_cannot_both_get_in(tmp_path: Path):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out", members={})
    (child / "diff" / os.fsdecode(b"\xff.diff")).write_bytes(b"x")
    with pytest.raises(AssayError):
        reservation.consume(members=lambda raw: ["diff/\udcff.diff", "diff/\udcff.diff"])


# --- review round 1: arm re-checks emptiness -----------------------------------


@pytest.mark.parametrize("entry", ["late.txt", ".keep", ".late-dir/"])
def test_arm_refuses_an_out_that_gained_an_entry_after_reserve(tmp_path: Path, entry: str):
    reservation = _reserve(tmp_path)
    out = tmp_path / "out"
    try:
        if entry.endswith("/"):
            (out / entry).mkdir()
        else:
            (out / entry).write_text("x")
        with pytest.raises(AssayError) as excinfo:
            reservation.arm()
        assert "not empty at arming" in _assert_refused(excinfo)
        assert [p.name for p in out.iterdir()] == [entry.rstrip("/")], "arm unlinks nothing"
        with pytest.raises(RuntimeError, match="not armed"):
            reservation.consume(members=_one_member())
    finally:
        reservation.close()


def test_arm_refuses_an_out_that_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reservation = _reserve(tmp_path)
    try:
        monkeypatch.setattr(
            os, "listdir", lambda path: (_ for _ in ()).throw(PermissionError(13, "no"))
        )
        with pytest.raises(AssayError) as excinfo:
            reservation.arm()
        assert "could not be listed" in _assert_refused(excinfo)
    finally:
        monkeypatch.undo()
        reservation.close()


# --- review round 1: an lstat that fails for any reason but absence ------------

needs_permission_checks = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permission bits"
)


@needs_permission_checks
def test_reserve_refuses_a_preexisting_out_with_mode_0400(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o400)
    try:
        with pytest.raises(AssayError) as excinfo:
            _reserve(tmp_path, create_missing_parents=False)
        assert "could not be inspected" in _assert_refused(excinfo)
    finally:
        out.chmod(0o700)


@needs_permission_checks
def test_arm_refuses_when_the_command_chmods_out_after_reserve(tmp_path: Path):
    reservation = _reserve(tmp_path)
    out = tmp_path / "out"
    out.chmod(0o600)
    try:
        with pytest.raises(AssayError) as excinfo:
            reservation.arm()
        assert "could not be inspected" in _assert_refused(excinfo)
    finally:
        out.chmod(0o700)
        reservation.close()


@needs_permission_checks
def test_consume_refuses_when_the_command_chmods_out_after_arm(tmp_path: Path):
    before = _fd_count()
    reservation = _armed(tmp_path)
    out = tmp_path / "out"
    out.chmod(0o600)
    try:
        with pytest.raises(AssayError) as excinfo:
            reservation.consume(members=_one_member())
        assert "could not be inspected" in _assert_refused(excinfo)
        assert _fd_count() == before, "the held descriptor was closed on the refusal"
    finally:
        out.chmod(0o700)


@pytest.mark.parametrize(
    ("target", "nth", "site"),
    [(ROTATED, 1, "rotated"), (CHILD, 1, "child"), (CHILD, 2, "final re-lstat"), ("out", 1, "<out> walk")],
    ids=["rotated", "child", "final-relstat", "out-walk"],
)
def test_consume_refuses_an_lstat_that_fails_at_every_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, nth: int, site: str
):
    """Each lstat of consume, failing with EACCES (deterministic, independent of
    the uid the lane runs as), is a refusal and never a raw PermissionError."""
    before = _fd_count()
    reservation = _armed(tmp_path)
    _write_child(tmp_path / "out")
    real = safeio._lstat_relative
    seen: dict[str, int] = {}

    def failing(name: str, *, dir_fd: int):
        seen[name] = seen.get(name, 0) + 1
        if name == target and seen[name] == nth:
            raise PermissionError(13, "Permission denied")
        return real(name, dir_fd=dir_fd)

    monkeypatch.setattr(safeio, "_lstat_relative", failing)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "could not be inspected" in _assert_refused(excinfo)
    assert _fd_count() == before


# --- review round 1: FIFOs never block, intermediate components are walked -----


class _Timeout(Exception):
    pass


@pytest.fixture
def bounded():
    """Fail (not hang) when a FIFO read would block: a 5 s interval timer."""
    import signal

    def on_alarm(signum, frame):
        raise _Timeout("a FIFO blocked the reservation")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_a_fifo_as_report_is_refused_without_blocking(tmp_path: Path, bounded):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    (child / REPORT).unlink()
    os.mkfifo(child / REPORT)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "not a regular file" in _assert_refused(excinfo)


def test_a_fifo_as_member_is_refused_without_blocking(tmp_path: Path, bounded):
    reservation = _armed(tmp_path)
    child = _write_child(tmp_path / "out")
    (child / "diff" / MEMBER).unlink()
    os.mkfifo(child / "diff" / MEMBER)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "not a regular file" in _assert_refused(excinfo)


def test_a_fifo_as_child_is_refused_without_blocking(tmp_path: Path, bounded):
    reservation = _armed(tmp_path)
    os.mkfifo(tmp_path / "out" / CHILD)
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert "not a directory" in _assert_refused(excinfo)


@pytest.mark.parametrize(
    ("how", "expected"),
    [
        ("replaced", "renamed away or replaced"),
        ("symlinked", "not an existing, non-symlink directory"),
        ("moved-away", "does not exist"),
    ],
)
def test_swap_of_an_intermediate_component_above_out_is_caught(
    tmp_path: Path, how: str, expected: str
):
    """target/assay is swapped while <out> below it is held: the held inode
    keeps a positive link count, so only the by-name walk can notice."""
    before = _fd_count()
    reservation = _armed_in(tmp_path, "target/assay/out")
    middle = tmp_path / "target" / "assay"
    middle.rename(tmp_path / "target" / "assay.moved")
    if how == "replaced":
        (middle / "out").mkdir(parents=True)
        _write_child(middle / "out")
    elif how == "symlinked":
        middle.symlink_to(tmp_path / "target" / "assay.moved")
    assert os.fstat(reservation._held_fd).st_nlink > 0
    with pytest.raises(AssayError) as excinfo:
        reservation.consume(members=_one_member())
    assert expected in _assert_refused(excinfo)
    assert _fd_count() == before
