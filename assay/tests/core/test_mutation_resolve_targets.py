"""P18 work item 2 — :func:`assay.mutation.resolve_mutation_targets`: R2's
own per-file candidate list, built from the SAME resolved
:class:`~assay.diff.AddedLines` R1 measures against, under the identical
source-root/excluded-directory/source-glob/test-path gates
:func:`assay.evaluate.evaluate_coverage`'s own (private) ``_is_considered``
already applies for R1 (this module's own duplicated copy — see the
function's own docstring for why it is not imported instead).
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import FakeAdapter

from assay.diff import AddedLines
from assay.mutation import MutationTarget, resolve_mutation_targets


def _added(by_file: dict[str, frozenset[int]]) -> AddedLines:
    return AddedLines(by_file=MappingProxyType(dict(by_file)))


@pytest.fixture
def repo_top(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    return root


def _reader(root: Path, seen: list[str]):
    def read(path: str) -> str:
        seen.append(path)
        return (root / path).read_text(encoding="utf-8")

    return read


def test_a_considered_file_becomes_one_target_with_its_own_text_and_lines(
    repo_top: Path,
):
    (repo_top / "src").mkdir()
    (repo_top / "src" / "mod.zzz").write_text("CONTENT\n", encoding="utf-8")
    added = _added({"src/mod.zzz": frozenset({2, 4})})
    seen: list[str] = []

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, seen),
    )

    assert targets == (
        MutationTarget(path="src/mod.zzz", text="CONTENT\n", lines=frozenset({2, 4})),
    )
    assert seen == ["src/mod.zzz"]


def test_a_file_outside_every_source_root_is_excluded(repo_top: Path):
    (repo_top / "other").mkdir()
    (repo_top / "other" / "mod.zzz").write_text("x\n", encoding="utf-8")
    added = _added({"other/mod.zzz": frozenset({1})})
    seen: list[str] = []

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, seen),
    )

    assert targets == ()
    assert seen == [], "an excluded file's text must never be read"


def test_a_file_inside_an_excluded_directory_is_excluded(repo_top: Path):
    (repo_top / "src" / "vendor").mkdir(parents=True)
    (repo_top / "src" / "vendor" / "mod.zzz").write_text("x\n", encoding="utf-8")
    added = _added({"src/vendor/mod.zzz": frozenset({1})})

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, []),
    )

    assert targets == ()


def test_a_file_not_matching_source_globs_is_excluded(repo_top: Path):
    (repo_top / "src").mkdir()
    (repo_top / "src" / "notes.txt").write_text("x\n", encoding="utf-8")
    added = _added({"src/notes.txt": frozenset({1})})

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, []),
    )

    assert targets == ()


def test_a_test_path_is_excluded(repo_top: Path):
    (repo_top / "src").mkdir()
    (repo_top / "src" / "mod_test.zzz").write_text("x\n", encoding="utf-8")
    added = _added({"src/mod_test.zzz": frozenset({1})})

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, []),
    )

    assert targets == ()


def test_multiple_considered_files_are_returned_in_path_order(repo_top: Path):
    (repo_top / "src").mkdir()
    (repo_top / "src" / "z.zzz").write_text("Z\n", encoding="utf-8")
    (repo_top / "src" / "a.zzz").write_text("A\n", encoding="utf-8")
    # deliberately inserted out of path order.
    added = _added(
        {
            "src/z.zzz": frozenset({1}),
            "src/a.zzz": frozenset({1}),
        }
    )

    targets = resolve_mutation_targets(
        added,
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, []),
    )

    assert [target.path for target in targets] == ["src/a.zzz", "src/z.zzz"]


def test_no_added_files_produces_no_targets(repo_top: Path):
    targets = resolve_mutation_targets(
        _added({}),
        repo_top=repo_top,
        source_root_paths=(repo_top / "src",),
        adapter=FakeAdapter(),
        read_source_text=_reader(repo_top, []),
    )

    assert targets == ()
