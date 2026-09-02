"""A-405 -- the `//line`-directive witness, end to end.

**Every number here comes from the real Go toolchain, not from this test**
(A-334). Two committed artifacts, both produced inside
`tester-unified-go:local` under `--network=none` by
`nyxloom-trove/carve-assets/P27-recarve/probe-linedup.sh`, both with their own
`PROVENANCE.md` block:

* `linedup.out` -- `go test -covermode=count -coverprofile` over Go's OWN
  canonical duplicate-position corpus, `cmd/cover/cover_test.go`'s
  `lineDupContents` (the source `TestLineDup` uses). Nine records, six of them
  carrying a zero column.
* `linedup-oracle.json` -- `assay/helpers/go/stmtpos/` over the same source
  bytes. Nine blocks, the same nine extents.

The fixture is Go's own rather than one assay invented, because `cmd/cover`
names it: go1.25.14 `/usr/local/go/src/cmd/cover/cover.go:1055-1060` says
"positions can repeat when there is a line directive that does not specify
column information and the input has not been passed through gofmt. See issues
#27530 and #30746. Tests are TestHtmlUnformatted and TestLineDup."

What this module proves, in the order the pipeline meets it:

1. the parser ACCEPTS those bytes (it used to refuse the whole artifact,
   "column number 0 ... is not positive" -- a guessed fact about `cmd/cover`
   that `cmd/cover` disproves);
2. the FILE is flagged `line_directive_remapped`, per file and conservatively;
3. the oracle reproduces all nine extents and all nine `num_stmts`, which
   discharges REPORT §5 item 4's own "the `dedup` replication is unproven";
4. `attribute_statements` empties a flagged file and leaves an ordinary file in
   the same profile untouched;
5. `evaluate` IGNORES a flagged file outside the judged set and REFUSES,
   naming `//line` and the file, one inside it -- in both modes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import load_go_statement_oracle

from assay.adapters.go import GoAdapter
from assay.coverage_parsers import go_cover
from assay.coverage_parsers.model import CoverageBlock, CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import evaluate_coverage, evaluate_targets
from assay.statement_attribution import StatementBlock, attribute_statements

_RECARVE = (
    Path(__file__).resolve().parents[1]
    / "nyxloom-trove"
    / "carve-assets"
    / "P27-recarve"
)
_LINEDUP_PROFILE = _RECARVE / "linedup.out"
_LINEDUP_ORACLE = _RECARVE / "linedup-oracle.json"

#: The profile's own key for the witness file. `go test` keys records by
#: import path, and the probe's module is `linedup`.
_KEY = "linedup/linedup.go"


def _parsed() -> CoverageProfile:
    return go_cover.parse(_LINEDUP_PROFILE.read_text(), producer="go-test")


def test_the_witness_profile_really_is_the_toolchains_own_zero_column_output():
    """The anti-vacuity guard for every test below: if the committed profile
    ever stopped carrying a zero column, this module would be proving nothing
    and would say so here rather than passing quietly.

    Also pins the specific shape the review reported -- `100.0,102.0` and
    friends -- so a regenerated fixture that lost the directive is visible as
    a diff in an assertion rather than as silence."""
    text = _LINEDUP_PROFILE.read_text()

    assert text.startswith("mode: count\n")
    assert "linedup/linedup.go:100.0,102.0 1 50" in text
    records = [line for line in text.splitlines()[1:] if line]
    assert len(records) == 9
    # Eight of the nine carry a zero column; only the `for` header above the
    # first `//line` directive keeps real ones.
    assert sum(".0," in line or ".0 " in line for line in records) == 8


def test_the_parser_accepts_a_real_line_directive_profile():
    """BLOCKER 1's own probe, inverted into a regression test.

    At `d938ab8c` this raised `ERROR`/`UNREADABLE_ARTIFACT` -- "go
    coverprofile: column number 0 in '100.0' is not positive" -- for an
    artifact that is byte-for-byte what `go test -coverprofile` wrote. One
    generated file in a project poisoned the entire lane."""
    profile = _parsed()

    assert set(profile.files) == {_KEY}
    blocks = profile.files[_KEY].blocks
    assert blocks is not None
    assert len(blocks) == 9
    assert any(block.start_col == 0 for block in blocks)
    assert any(block.end_col == 0 for block in blocks)
    # Lines are still 1-based, and every column is still non-negative: the
    # bound moved by exactly one value, on exactly one coordinate pair.
    assert all(block.start_line >= 1 and block.end_line >= 1 for block in blocks)
    assert all(block.start_col >= 0 and block.end_col >= 0 for block in blocks)


def test_a_negative_column_is_still_refused():
    """The bound moved to `>= 0`, not away. `go/token` emits 0 for a
    `//line`-remapped position and nothing below it, so a negative column is
    corruption and stays an `UNREADABLE_ARTIFACT`."""
    with pytest.raises(AssayError) as excinfo:
        go_cover.parse("mode: set\nm/x.go:5.-1,6.2 1 1\n", producer=None)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "negative" in str(excinfo.value)


def test_the_file_is_flagged_remapped_and_an_ordinary_file_is_not():
    """The flag is per FILE and conservative: this profile mixes `6.21,7.25`
    (physical, columns present) with `100.0,102.1` (virtual) in ONE file, and
    within a file carrying a `//line` directive there is no way to tell which
    records are which -- so one zero column flags the whole file."""
    profile = _parsed()

    assert profile.files[_KEY].line_directive_remapped is True

    ordinary = FileCoverage(
        executed=frozenset({4}),
        missing=frozenset(),
        excluded=None,
        blocks=(
            CoverageBlock(
                start_line=3,
                start_col=22,
                end_line=7,
                end_col=2,
                num_stmts=2,
                count=1,
            ),
        ),
    )
    assert ordinary.line_directive_remapped is False
    # A line-based format has no blocks at all, so the question does not
    # arise and the answer is False rather than unknown.
    assert (
        FileCoverage(
            executed=frozenset({1}), missing=frozenset(), excluded=None
        ).line_directive_remapped
        is False
    )


def test_the_oracle_reproduces_all_nine_extents_and_statement_counts():
    """**This is the witness REPORT §5 item 4 said did not exist.**

    `cmd/cover`'s `dedup` (cover.go:1073-1090) bumps a repeated position's end
    column until the pair is unique -- the `100.0,100.0` / `100.0,102.0` /
    `100.0,102.1` ladder in the profile is that loop's output. assay's oracle
    transcribes it, and until this fixture existed nothing exercised the
    transcription: every frozen P27 witness and every F008-A4 fixture has
    unique positions, so `dedup` never fired.

    The join is on the whole four-part extent AND on `num_stmts`, both
    directions, so neither side may carry a block the other does not."""
    parsed = _parsed().files[_KEY].blocks
    assert parsed is not None
    oracle = load_go_statement_oracle(_LINEDUP_ORACLE)["linedup.go"]

    assert len(oracle) == 9
    assert {block.extent for block in parsed} == {block.extent for block in oracle}
    by_extent = {block.extent: block.num_stmts for block in oracle}
    assert {block.extent: block.num_stmts for block in parsed} == by_extent
    # The `dedup` ladder itself, spelled out: three extents sharing a start
    # position, distinguished only by their end column.
    assert (100, 0, 100, 0) in by_extent
    assert (100, 0, 102, 0) in by_extent
    assert (100, 0, 102, 1) in by_extent


def test_the_oracles_own_output_carries_zero_columns_too():
    """`StatementBlock` used to assert "a 1-based source position is never
    below 1" about all four coordinates, so the oracle's own correct answer
    for this file would have been refused by `go_stmtpos._read_block` as a
    malformed document. Loading it IS the assertion."""
    oracle = load_go_statement_oracle(_LINEDUP_ORACLE)["linedup.go"]

    remapped = [block for block in oracle if block.start_col == 0 or block.end_col == 0]
    # Eight of the nine. The only survivor is `6.21,7.25`, the `for` header,
    # which is above the first `//line` directive in the source.
    assert len(remapped) == 8
    raw = json.loads(_LINEDUP_ORACLE.read_text())
    assert raw["go_version"] == "go1.25.14"


def _flagged_and_ordinary() -> CoverageProfile:
    """One profile carrying the real remapped file AND an ordinary one, so
    every downstream assertion has its own control in the same object."""
    parsed = _parsed()
    files = dict(parsed.files)
    files["linedup/plain.go"] = FileCoverage(
        executed=frozenset(),
        missing=frozenset(),
        excluded=None,
        blocks=(
            CoverageBlock(
                start_line=3,
                start_col=22,
                end_line=7,
                end_col=2,
                num_stmts=2,
                count=1,
            ),
        ),
    )
    return CoverageProfile(files=MappingProxyType(files))


_PLAIN_ORACLE = (
    StatementBlock(
        start_line=3, start_col=22, end_line=7, end_col=2, num_stmts=2, stmt_lines=(4, 6)
    ),
)


def test_attribution_empties_the_flagged_file_and_corrects_the_ordinary_one():
    """The flagged file is skipped rather than sent to the oracle: the oracle
    derives PHYSICAL positions from the real 24-line source, the profile
    carries the directive's virtual 100-105, and the resulting extent
    mismatch would refuse the WHOLE artifact -- the blast radius BLOCKER 1
    called wrong.

    Emptied, not passed through: its recorded line numbers are another file's
    coordinates, so `executed`/`missing` would be claims about lines this file
    does not have. The blocks stay, which is what keeps the flag derivable one
    layer up so `evaluate` can still refuse."""
    corrected = attribute_statements(
        _flagged_and_ordinary(), {"linedup/plain.go": _PLAIN_ORACLE}
    )

    assert corrected.statement_attributed is True

    flagged = corrected.files[_KEY]
    assert flagged.executed == frozenset()
    assert flagged.missing == frozenset()
    assert flagged.line_directive_remapped is True

    plain = corrected.files["linedup/plain.go"]
    assert plain.executed == frozenset({4, 6})
    assert plain.missing == frozenset()


class _NoSourceRead:
    def __call__(self, path: str) -> str:
        raise AssertionError(f"no source read expected, got {path!r}")


def _evaluate(added: dict[str, frozenset[int]], profile: CoverageProfile):
    return evaluate_coverage(
        added=AddedLines(by_file=MappingProxyType(added)),
        profile=profile,
        adapter=GoAdapter(module_path="linedup"),
        repo_top=Path("/repo"),
        project_root=Path("/repo"),
        source_root_paths=(Path("/repo"),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_NoSourceRead(),
    )


def test_a_flagged_file_outside_the_judged_set_is_ignored_and_the_lane_proceeds():
    """DA-R2's central asymmetry, and the reason shape (iii) -- refuse the
    whole lane -- was not ruled: a Go project carrying one generated file
    would otherwise be unable to use R1 at all.

    The north star's own words: pre-existing code outside the diff is
    invisible to the verdict by construction, because it is not what is under
    review. Nothing here changed `linedup.go`, so its records contribute
    nothing and the ordinary file is judged normally."""
    corrected = attribute_statements(
        _flagged_and_ordinary(), {"linedup/plain.go": _PLAIN_ORACLE}
    )

    result = _evaluate({"plain.go": frozenset({4, 6})}, corrected)

    assert result.outcome is Outcome.PASS
    assert result.considered == 1
    assert result.executable == 2
    assert result.covered == 2


def test_a_flagged_file_inside_the_judged_set_refuses_and_names_the_cause():
    """The other half, and the reason emptying the line sets is safe rather
    than a silent 0/0: the file HAS changed lines, they are inside
    `source_roots`, and without this refusal it would contribute 0 executable
    of 0 changed and the lane would report a clean percentage over a file
    nothing measured.

    `ERROR`/`BAD_LANE_CONFIG`, from the closed vocabulary, no new code. The
    artifact is not at fault -- it is exactly what `go test` wrote -- the LANE
    is, and the message says so and names the remedy."""
    corrected = attribute_statements(
        _flagged_and_ordinary(), {"linedup/plain.go": _PLAIN_ORACLE}
    )

    with pytest.raises(AssayError) as excinfo:
        _evaluate({"linedup.go": frozenset({7})}, corrected)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    message = str(excinfo.value)
    assert "`//line`" in message
    assert "linedup.go" in message
    assert "judge.source_roots" in message


def test_the_same_refusal_fires_in_whole_target_mode():
    """`evaluate_targets` has its own copy of the judged-set question --
    "declared in judge.targets" rather than "changed inside source_roots" --
    and the check is placed BEFORE its `TARGET_NOT_MEASURED` guard on
    purpose: an emptied file would trip that one instead, and "this target has
    zero executable lines" names the wrong cause and implies the wrong remedy
    (write tests)."""
    corrected = attribute_statements(
        _flagged_and_ordinary(), {"linedup/plain.go": _PLAIN_ORACLE}
    )

    with pytest.raises(AssayError) as excinfo:
        evaluate_targets(
            targets=("linedup.go",),
            profile=corrected,
            adapter=GoAdapter(module_path="linedup"),
            repo_top=Path("/repo"),
            project_root=Path("/repo"),
            source_root_paths=(Path("/repo"),),
            fail_under=100.0,
            allow_excluded=False,
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "judge.targets" in str(excinfo.value)
