"""B074 -- ``judge.allow_test_path_targets``: an EXPLICITLY DECLARED
``judge.targets`` entry may opt out of the test-path veto
:func:`assay.evaluate._resolve_whole_target` applies, and nothing else may.

The claim this module defends, in the four halves the backlog entry asks for:

1. the SAME lane, same target, differing ONLY in the flag, reaches a real
   ``PASS``/``FAIL`` on its coverage floor with it and ``ERROR``/
   ``BAD_LANE_CONFIG`` without it -- the controlled comparison proving the
   flag is what changed the outcome, not some incidental edit;
2. the SWEEP side is untouched: a changed-line judgment over a diff that
   touches the same file still skips it, because the flag is not a parameter
   of that path at all;
3. the flag relaxes the DIRECTORY half of an adapter's test-path convention
   and never the FILENAME half -- ``tests/_harness/lib.py`` yes,
   ``tests/test_foo.py``/``tests/conftest.py`` no, in every adapter shipped;
4. the other five ``_resolve_whole_target`` gates still refuse WITH the flag
   set, so the relaxation is one gate wide.

Proven against the REAL :class:`~assay.adapters.python.PythonAdapter` rather
than only the synthetic fake: B074's repro is a Python repository whose
deployed harness library lives under ``tests/``, and a fake whose test-path
rule this module wrote itself could not fail the way the real one did.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import (
    _is_test_filename,
    _resolve_whole_target,
    evaluate_coverage,
    evaluate_targets,
)

PY = PythonAdapter()


def _profile(files: dict[str, FileCoverage]) -> CoverageProfile:
    return CoverageProfile(files=MappingProxyType(dict(files)))


@pytest.fixture()
def harness_repo(tmp_path: Path) -> Path:
    """B074's own reproduced layout: deployed LIBRARY code under a ``tests/``
    segment (dstdns's ``tests/_harness/``), beside a genuine test module and a
    ``conftest.py`` in the same directory -- the three files the flag must
    treat differently.
    """
    harness = tmp_path / "tests" / "_harness"
    harness.mkdir(parents=True)
    (harness / "lib.py").write_text("def serve():\n    return 1\n")
    (harness / "test_lib.py").write_text("def test_serve():\n    pass\n")
    (harness / "conftest.py").write_text("import pytest\n")
    return tmp_path


# --- 1. the controlled comparison: the flag is what changed the outcome ------


def test_a_declared_library_target_under_tests_resolves_with_the_flag(
    harness_repo: Path,
):
    assert (
        _resolve_whole_target(
            "tests/_harness/lib.py",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
        == "tests/_harness/lib.py"
    )


def test_the_same_target_without_the_flag_still_refuses(harness_repo: Path):
    """The wrong implementation, run deliberately: identical call, flag
    omitted (its default), so the refusal cannot be attributed to anything
    but the flag."""
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "tests/_harness/lib.py",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
        )
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    message = str(exc.value)
    # names the target AND the gate, per the acceptance box
    assert "tests/_harness/lib.py" in message
    assert "test path" in message
    # and names the remedy, so a correctly-configured consumer is not left
    # guessing which knob exists (the B068/N2 diagnostic discipline)
    assert "judge.allow_test_path_targets" in message


def test_the_flag_reaches_a_real_pass_on_the_coverage_floor(harness_repo: Path):
    """`PASS`/`FAIL` on the floor, not merely "resolution stopped raising":
    the acceptance box asks for a JUDGED target."""
    profile = _profile({
        "tests/_harness/lib.py": FileCoverage(
            executed=frozenset({1, 2}), missing=frozenset(), excluded=frozenset(),
        ),
    })
    result = evaluate_targets(
        profile=profile,
        adapter=PY,
        repo_top=harness_repo,
        project_root=harness_repo,
        targets=("tests/_harness/lib.py",),
        source_root_paths=(harness_repo / "tests",),
        fail_under=100.0,
        allow_excluded=False,
        allow_test_path_targets=True,
    )
    assert result.outcome is Outcome.PASS
    assert (result.covered, result.executable) == (2, 2)
    assert result.considered == 1


def test_the_flag_reaches_a_real_fail_on_the_coverage_floor(harness_repo: Path):
    """The other half of "judged": an under-covered opted-in target FAILS on
    its floor rather than passing because it was hard to refuse."""
    profile = _profile({
        "tests/_harness/lib.py": FileCoverage(
            executed=frozenset({1}), missing=frozenset({2}), excluded=frozenset(),
        ),
    })
    result = evaluate_targets(
        profile=profile,
        adapter=PY,
        repo_top=harness_repo,
        project_root=harness_repo,
        targets=("tests/_harness/lib.py",),
        source_root_paths=(harness_repo / "tests",),
        fail_under=100.0,
        allow_excluded=False,
        allow_test_path_targets=True,
    )
    assert result.outcome is Outcome.FAIL
    assert (result.covered, result.executable) == (1, 2)
    # the uncovered line is reported against the opted-in target by name,
    # so a FAIL says WHICH line of it regressed
    assert dict(result.missing_lines) == {
        "tests/_harness/lib.py": frozenset({2})
    }


def test_evaluate_targets_without_the_flag_refuses_the_same_target(
    harness_repo: Path,
):
    profile = _profile({
        "tests/_harness/lib.py": FileCoverage(
            executed=frozenset({1, 2}), missing=frozenset(), excluded=frozenset(),
        ),
    })
    with pytest.raises(AssayError) as exc:
        evaluate_targets(
            profile=profile,
            adapter=PY,
            repo_top=harness_repo,
            project_root=harness_repo,
            targets=("tests/_harness/lib.py",),
            source_root_paths=(harness_repo / "tests",),
            fail_under=100.0,
            allow_excluded=False,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG


# --- 2. the sweep side is untouched -----------------------------------------


def test_the_changed_line_sweep_still_skips_the_same_file(harness_repo: Path):
    """A `changed_lines` judgment over a diff touching the same file skips
    it exactly as before -- proving the relaxation is scoped to explicitly
    declared targets and did not widen the diff path. `evaluate_coverage`
    takes no such parameter AT ALL, which is the strongest form of this
    guarantee: there is no argument a caller could pass to change it.
    """
    assert "allow_test_path_targets" not in evaluate_coverage.__code__.co_varnames
    added = AddedLines(by_file=MappingProxyType({
        "tests/_harness/lib.py": frozenset({1, 2}),
    }))
    def read_source_text(path: str) -> str:  # pragma: no cover - never reached
        raise AssertionError(
            f"a skipped test path must never be read for classification: {path}"
        )

    result = evaluate_coverage(
        added=added,
        profile=_profile({}),
        adapter=PY,
        repo_top=harness_repo,
        project_root=harness_repo,
        source_root_paths=(harness_repo / "tests",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )
    # skipped, not judged: nothing considered, nothing missing, and no
    # NO_MEASUREMENT for a file that is absent from an empty profile --
    # exactly the pre-B074 behaviour
    assert result.considered == 0
    assert result.executable == 0
    assert result.files_missing_coverage == ()


# --- 3. the FILENAME half is never relaxed, in any adapter ------------------


@pytest.mark.parametrize("name", ["test_lib.py", "conftest.py"])
def test_a_genuine_test_file_still_refuses_with_the_flag(
    harness_repo: Path, name: str
):
    """The wave prompt's one ambiguous edge case, decided: the ``tests/``
    SEGMENT alternative of the adapter's convention is what a declared target
    can override; the FILENAME alternatives stay in force."""
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            f"tests/_harness/{name}",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    message = str(exc.value)
    assert name in message
    assert "FILENAME" in message
    # and it explains WHY the flag did not help, rather than repeating the
    # generic test-path refusal the flag was meant to answer
    assert "does not override" in message


def test_a_test_filename_outside_a_test_directory_also_refuses_with_the_flag(
    tmp_path: Path,
):
    """`src/conftest.py` is a test path by FILENAME alone -- no `tests/`
    segment involved -- so the flag has nothing to relax there either."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "conftest.py").write_text("import pytest\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "src/conftest.py",
            adapter=PY,
            project_root=tmp_path,
            source_root_paths=(tmp_path / "src",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "FILENAME" in str(exc.value)


#: One case per REGISTERED language, keyed by the registry's own name.
#:
#: * ``directory_case`` -- an ``is_test_path`` positive that matches only on a
#:   DIRECTORY branch of that adapter's convention, so the flag may override
#:   it. ``None`` means the adapter has no directory branch at all, which is a
#:   real answer (Go), not a missing case.
#: * ``filename_case`` -- an ``is_test_path`` positive that matches on a
#:   FILENAME branch, which the flag must never override.
#:
#: Hand-written per language, because each entry encodes that language's own
#: convention and deriving it from the regex under test would be the tautology
#: A-067 forbids. What is DERIVED is the SET this table must cover -- see the
#: completeness guard below.
_SPLIT_CASES: dict[str, tuple[str | None, str]] = {
    # `tests/` is the directory branch; `test_*.py`/`conftest.py` the filename
    # branches.
    "python": ("tests/_harness/lib.py", "tests/_harness/test_lib.py"),
    # `tests/`/`test/` are directory branches; `test_*.sql`/`*_test.sql` the
    # filename ones.
    "sql": ("tests/ddl/schema.sql", "tests/ddl/schema_test.sql"),
    # `__tests__/` is the directory branch; `*.test.ts` the filename one.
    "javascript": ("__tests__/helper.ts", "__tests__/helper.test.ts"),
    # Go's convention is PURELY a filename suffix, so there is no directory
    # branch for a declaration to be wrong about and the flag is correctly
    # inert for a Go lane. `None` records that as an answer.
    "go": (None, "internal/pkg/thing_test.go"),
}


def _registered_languages() -> dict[str, object]:
    from assay.cli import _built_in_registry

    return {name: entry.adapter for name, entry in _built_in_registry().entries.items()}


def test_every_registered_adapter_has_a_split_case():
    """(A-270, round-1 Blocker 3.) `_resolve_whole_target`'s docstring,
    `CHANGES.md` and `CONSUMERS.md` all claim the directory/filename split
    holds "in every adapter" and "for any future one". That claim is only as
    strong as the set it is checked over, so the set is DERIVED from the
    shipped registry rather than hand-copied beside it: a fifth adapter added
    without a case here turns this red instead of silently narrowing the
    claim to four languages while the prose still says "every".
    """
    assert set(_SPLIT_CASES) == set(_registered_languages()), (
        "every registered language needs a directory/filename case in "
        "_SPLIT_CASES (or an explicit None directory_case if it has no "
        "directory branch)"
    )


@pytest.mark.parametrize("language", sorted(_SPLIT_CASES))
def test_is_test_filename_splits_directory_from_filename_per_adapter(language: str):
    """The split, asserted against each REGISTERED adapter's own live rule --
    both cases are `is_test_path` positives, and only the filename one is a
    FILENAME positive. Without this asymmetry the flag would either relax
    nothing or relax everything.

    The adapter comes from the registry, not from a local constructor, so this
    exercises the object a real lane resolves.
    """
    adapter = _registered_languages()[language]
    directory_case, filename_case = _SPLIT_CASES[language]

    assert adapter.is_test_path(filename_case), filename_case
    assert _is_test_filename(adapter, filename_case), (
        f"{language}: {filename_case} must stay refused WITH the flag set"
    )

    if directory_case is None:
        # No directory branch: the flag relaxes nothing here, and the pair
        # that would prove it does not exist. Assert the ABSENCE positively
        # rather than skipping, so "this adapter has no directory branch"
        # stays a checked claim -- a `tests/`-segment path is simply not a
        # test path for this language.
        assert not adapter.is_test_path("tests/helper.go"), (
            f"{language} was declared to have no directory branch, but one "
            f"matched"
        )
        return

    assert adapter.is_test_path(directory_case), directory_case
    assert not _is_test_filename(adapter, directory_case), (
        f"{language}: {directory_case} is a DIRECTORY positive, so a declared "
        f"target must be able to override it"
    )


# --- 4. the other five gates still refuse WITH the flag ---------------------


def test_the_flag_does_not_relax_the_symlink_gate(harness_repo: Path):
    link = harness_repo / "tests" / "_harness" / "link.py"
    link.symlink_to(harness_repo / "tests" / "_harness" / "lib.py")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "tests/_harness/link.py",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "symlink" in str(exc.value)


def test_the_flag_does_not_relax_the_source_root_containment_gate(
    harness_repo: Path,
):
    (harness_repo / "other").mkdir()
    (harness_repo / "other" / "lib.py").write_text("x = 1\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "other/lib.py",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not contained beneath any declared source root" in str(exc.value)


def test_the_flag_does_not_relax_the_regular_file_gate(harness_repo: Path):
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "tests/_harness",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "regular file, never a directory" in str(exc.value)


def test_the_flag_does_not_relax_the_excluded_directory_gate(harness_repo: Path):
    excluded = harness_repo / "tests" / "node_modules"
    excluded.mkdir()
    (excluded / "lib.py").write_text("x = 1\n")
    adapter = PythonAdapter(excluded_dir_names=frozenset({"node_modules"}))
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "tests/node_modules/lib.py",
            adapter=adapter,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "excluded directory" in str(exc.value)


def test_the_flag_does_not_relax_the_adapter_source_glob_gate(harness_repo: Path):
    (harness_repo / "tests" / "_harness" / "fixture.json").write_text("{}\n")
    with pytest.raises(AssayError) as exc:
        _resolve_whole_target(
            "tests/_harness/fixture.json",
            adapter=PY,
            project_root=harness_repo,
            source_root_paths=(harness_repo / "tests",),
            allow_test_path_targets=True,
        )
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not adapter-recognised source" in str(exc.value)


# --- 5. the artifact side: schema, reconstruction and `assay verify` --------
#
# NOTE on the identifier: this module's "B074" is the `judge.
# allow_test_path_targets` backlog entry. Several pre-existing comments in
# `src/assay/verify.py`, `src/assay/coverage_parsers/` and
# `src/assay/adapters/go_stmtpos.py` also say "B074" but mean the untrusted-
# JSON `RecursionError` sweep, which the backlog RENUMBERED to B075 at merge
# time after those comments had already shipped. Both names are in the tree;
# only the backlog is authoritative.


def _fixture_document() -> dict:
    """The shipped whole-target verdict fixture, loaded fresh per test so a
    mutation in one test cannot leak into another."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).parent
        / "fixtures"
        / "verdicts"
        / "r1_no_measurement_target_not_measured.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_opted_in_flag_survives_verify_and_reconstruction():
    """The acceptance box's last line: the flag is visible in the verdict, so
    a reviewer can see that a graded target was one assay would otherwise
    have refused -- and `assay verify` accepts the document carrying it."""
    from assay.verify import verify_document

    document = _fixture_document()
    document["judgment"]["r1"]["allow_test_path_targets"] = True
    assert verify_document(document) == []


def test_a_verdict_without_the_flag_still_verifies_unchanged():
    """The no-regression half: every verdict written before B074 is still
    accepted verbatim, which is what makes the field additive."""
    from assay.verify import verify_document

    assert verify_document(_fixture_document()) == []


def test_verify_refuses_the_flag_recorded_under_changed_line_mode():
    """A hand-forged document, not one any producer can write: the raw
    schema and the reconstructed model must BOTH refuse it, which is why the
    rule is expressed in the schema's own `allOf` and in
    `JudgmentR1.__post_init__` rather than in one of the two."""
    from assay.verify import verify_document

    document = _fixture_document()
    r1 = document["judgment"]["r1"]
    r1["mode"] = "changed_lines"
    r1.pop("targets")
    r1["allow_test_path_targets"] = True
    problems = verify_document(document)
    assert problems
    assert any("allow_test_path_targets" in problem for problem in problems)


def test_verify_refuses_the_flag_spelled_as_an_explicit_false():
    """`false` is spelled as ABSENCE (A-051's omitted-never-null rule applied
    to a boolean whose false value is its historical value), so a document
    carrying `false` is a producer disagreeing with `to_dict` and is refused
    rather than quietly normalised."""
    from assay.verify import verify_document

    document = _fixture_document()
    document["judgment"]["r1"]["allow_test_path_targets"] = False
    assert verify_document(document)
