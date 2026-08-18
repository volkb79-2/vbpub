"""Wave-1 item 5 (A-270) -- the three mechanical documentation checks ruled
in `decisions.md` A-270 and recorded in `docs/DESIGN-GUIDE.md` §16 and the
estate-wide `AGENTS.md`:

1. every TOML example in ``README.md``, ``docs/CONSUMERS.md`` and
   ``docs/DESIGN-GUIDE.md`` parses with the SHIPPED loader and declares the
   current :data:`assay.config.LANE_SCHEMA_VERSION`. Examples are extracted
   from the rendered documents' own fenced code blocks -- never a duplicated
   copy pasted into this module, which would drift silently. A deliberate
   fragment (not a runnable lane on its own) carries an explicit
   ``<!-- assay-doc-example:skip reason="..." -->`` marker on the line
   immediately before its fence; nothing is silently exempted otherwise.
2. every value of every closed public vocabulary a consumer must type --
   ``isolation.snapshot_selection``, ``judge.mode``, the rigor levels, the
   coverage ``format`` registry, the closed ``ReasonCode`` vocabulary
   (A-277), and (P34/A-287) the mutation-operator vocabulary of every
   REGISTERED language -- appears in at least one of the three documents.
   Each vocabulary is DERIVED from the shipped module, never hand-copied,
   and the derived sets are asserted non-empty so an import that silently
   yields nothing cannot make this check pass forever.
3. every ``docs/DESIGN-GUIDE.md#...`` anchor ``README.md`` links to resolves
   against a real DESIGN-GUIDE heading.

Every check pairs a real must-succeed proof with a must-fail control that
proves the SAME checking logic can go red -- a check that cannot fail is
this project's most expensive recurring defect (A-124, A-131).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest

from assay.cli import _built_in_registry
from assay.config import (
    JUDGE_MODES,
    LANE_SCHEMA_VERSION,
    RIGOR_LEVELS,
    SNAPSHOT_SELECTIONS,
    LaneConfigError,
    load_lane_file,
)
from assay.coverage import FORMAT_REGISTRY
from assay.verdict import ReasonCode
from assay.vocabulary import MUTATION_OPERATORS, MUTATION_OPERATORS_BY_LANGUAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
CONSUMERS = REPO_ROOT / "docs" / "CONSUMERS.md"
DESIGN_GUIDE = REPO_ROOT / "docs" / "DESIGN-GUIDE.md"
DOCS = (README, CONSUMERS, DESIGN_GUIDE)

# --- (1) TOML example extraction --------------------------------------------

#: A skip marker is the line immediately preceding a ```toml fence -- no
#: blank line between the two, so an author cannot "accidentally" leave a
#: marker orphaned above unrelated prose while the fence below it silently
#: goes unchecked.
_FENCE_RE = re.compile(
    r'(?:<!--\s*assay-doc-example:skip\s+reason="(?P<reason>[^"]*)"\s*-->\n)?'
    r"```toml\n(?P<body>.*?)\n```",
    re.DOTALL,
)


class TomlExample(NamedTuple):
    doc: Path
    line: int
    body: str
    skip_reason: str | None


def _extract_toml_examples(text: str, *, doc: Path = Path("<memory>")) -> list[TomlExample]:
    """Pure extraction over *text* -- kept separate from file I/O so the
    marker/no-marker logic itself is directly testable against synthetic
    fixtures, independent of what the real documents happen to contain today.
    """
    examples: list[TomlExample] = []
    for match in _FENCE_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        examples.append(
            TomlExample(
                doc=doc, line=line, body=match.group("body"), skip_reason=match.group("reason")
            )
        )
    return examples


def _extract_from_file(doc: Path) -> list[TomlExample]:
    text = doc.read_text(encoding="utf-8")
    return [ex._replace(doc=doc) for ex in _extract_toml_examples(text, doc=doc)]


def _all_examples() -> list[TomlExample]:
    examples: list[TomlExample] = []
    for doc in DOCS:
        examples.extend(_extract_from_file(doc))
    return examples


_ALL_EXAMPLES = _all_examples()
_LIVE_EXAMPLES = [ex for ex in _ALL_EXAMPLES if ex.skip_reason is None]
_SKIPPED_EXAMPLES = [ex for ex in _ALL_EXAMPLES if ex.skip_reason is not None]


def _materialize_lane_dependencies(project_root: Path, document: dict) -> None:
    """Pre-create exactly what :func:`assay.config.load_lane_file` checks for
    on disk at load time: a declared ``source_roots`` directory, and a
    declared ``canary.target`` file. ``judge.targets`` (B005) is deliberately
    left absent -- the loader itself never checks it, precisely so a
    whole-target lane stays judgeable from any commit (``_load_targets``'s
    own docstring)."""
    lanes = document.get("lanes", {})
    if not isinstance(lanes, dict):
        return
    for lane_table in lanes.values():
        if not isinstance(lane_table, dict):
            continue
        judge = lane_table.get("judge")
        if not isinstance(judge, dict):
            continue
        for root in judge.get("source_roots", ()):
            (project_root / root).mkdir(parents=True, exist_ok=True)
        canary = judge.get("canary")
        if isinstance(canary, dict) and "target" in canary:
            target_path = project_root / canary["target"]
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not target_path.exists():
                target_path.write_text("# assay-doc-example canary target\n", encoding="utf-8")


def _check_example(example: TomlExample, tmp_path: Path) -> None:
    """The one checking routine both the parametrized real-document test and
    its must-fail control below call, so the control proves the ACTUAL
    checking logic can go red rather than a re-description of it."""
    document = tomllib.loads(example.body)
    assert document.get("schema_version") == LANE_SCHEMA_VERSION, (
        f"{example.doc.name}:{example.line} does not declare the current "
        f"LANE_SCHEMA_VERSION ({LANE_SCHEMA_VERSION}); got "
        f"{document.get('schema_version')!r}"
    )
    _materialize_lane_dependencies(tmp_path, document)
    lane_file_path = tmp_path / "assay.toml"
    lane_file_path.write_text(example.body, encoding="utf-8")
    load_lane_file(lane_file_path)  # raises LaneConfigError on any defect


@pytest.mark.parametrize(
    "example",
    _LIVE_EXAMPLES,
    ids=[f"{ex.doc.name}:{ex.line}" for ex in _LIVE_EXAMPLES],
)
def test_every_live_toml_example_parses_with_the_shipped_loader(
    example: TomlExample, tmp_path: Path
):
    _check_example(example, tmp_path)


def test_at_least_one_live_toml_example_exists_in_each_of_the_three_documents():
    """The must-succeed control for the extraction+parametrization above: if
    any one of the three documents had zero live examples, the parametrized
    test above would simply never run for it and silently report nothing
    wrong -- exactly the vacuous-check failure A-270(1) exists to prevent."""
    for doc in DOCS:
        live_for_doc = [ex for ex in _LIVE_EXAMPLES if ex.doc == doc]
        assert live_for_doc, f"{doc.name} has no live (non-skipped) TOML example"


def test_a_stale_schema_version_is_refused_by_the_same_check_that_passes_the_real_examples(
    tmp_path: Path,
):
    """Must-fail control, proving check (1) can go red: an otherwise
    well-formed lane declaring the OLD schema_version is refused by the exact
    routine the real, live examples above pass. Paired against a REAL live
    example (must-succeed, already proven above) that differs from this one
    ONLY in the schema_version line."""
    real = _LIVE_EXAMPLES[0].body
    stale = real.replace(
        f"schema_version = {LANE_SCHEMA_VERSION}", "schema_version = 1", 1
    )
    assert stale != real, "fixture setup bug: the replacement did not change anything"
    stale_example = TomlExample(doc=Path("<synthetic>"), line=0, body=stale, skip_reason=None)
    with pytest.raises(AssertionError, match="LANE_SCHEMA_VERSION"):
        _check_example(stale_example, tmp_path)


def test_a_malformed_lane_is_refused_by_the_loader_itself(tmp_path: Path):
    """A second, independent must-fail control: a document that declares the
    CURRENT schema_version but is otherwise malformed (missing a required
    top-level field) must still be refused -- proving the check does not
    stop at the version comparison and actually calls the real loader."""
    malformed = (
        f"schema_version = {LANE_SCHEMA_VERSION}\n\n"
        '[lanes.broken]\nscope = "S1"\nrigor = ["R0"]\nenforcement = "gate"\n'
        # argv/env/env_passthrough/budget/allow_argv_append deliberately omitted
    )
    example = TomlExample(doc=Path("<synthetic>"), line=0, body=malformed, skip_reason=None)
    with pytest.raises(LaneConfigError):
        _check_example(example, tmp_path)


def test_a_marked_fragment_is_excluded_from_the_live_set():
    """The skip marker itself is a documented opt-out, not a silent one:
    prove the extractor actually recognizes it (must-succeed) and that an
    otherwise-identical fence with NO marker is NOT excluded (the paired
    must-fail control -- absent the marker, the same fragment would have
    entered the live set and failed to parse)."""
    marked = (
        '<!-- assay-doc-example:skip reason="illustrative fragment only" -->\n'
        "```toml\nnot a real lane\n```\n"
    )
    unmarked = "```toml\nnot a real lane\n```\n"
    marked_examples = _extract_toml_examples(marked)
    unmarked_examples = _extract_toml_examples(unmarked)
    assert len(marked_examples) == 1
    assert marked_examples[0].skip_reason == "illustrative fragment only"
    assert len(unmarked_examples) == 1
    assert unmarked_examples[0].skip_reason is None


def test_every_skip_marker_in_the_real_documents_carries_a_non_empty_reason():
    assert _SKIPPED_EXAMPLES, "expected at least one deliberately-skipped fragment"
    for ex in _SKIPPED_EXAMPLES:
        assert ex.skip_reason.strip(), f"{ex.doc.name}:{ex.line} has an empty skip reason"


# --- `_materialize_lane_dependencies` -- exercised directly, not only        --
# --- incidentally through the real documents' own shapes ---------------------


def test_materialize_lane_dependencies_ignores_a_non_dict_lanes_table(tmp_path: Path):
    _materialize_lane_dependencies(tmp_path, {"lanes": "not-a-table"})
    assert list(tmp_path.iterdir()) == []


def test_materialize_lane_dependencies_ignores_a_non_dict_lane_entry(tmp_path: Path):
    _materialize_lane_dependencies(tmp_path, {"lanes": {"x": "not-a-table"}})
    assert list(tmp_path.iterdir()) == []


def test_materialize_lane_dependencies_ignores_a_lane_with_no_judge_table(tmp_path: Path):
    """Paired must-succeed control below (creates a source root) proves this
    is genuinely about the ABSENT judge table, not a fixture that happens to
    create nothing for some other reason."""
    _materialize_lane_dependencies(tmp_path, {"lanes": {"x": {"scope": "S1"}}})
    assert list(tmp_path.iterdir()) == []


def test_materialize_lane_dependencies_ignores_a_judge_with_no_source_roots_or_canary(
    tmp_path: Path,
):
    _materialize_lane_dependencies(tmp_path, {"lanes": {"x": {"judge": {}}}})
    assert list(tmp_path.iterdir()) == []


def test_materialize_lane_dependencies_creates_declared_source_roots(tmp_path: Path):
    _materialize_lane_dependencies(
        tmp_path, {"lanes": {"x": {"judge": {"source_roots": ["a/b"]}}}}
    )
    assert (tmp_path / "a" / "b").is_dir()


def test_materialize_lane_dependencies_creates_a_declared_canary_target_only_if_missing(
    tmp_path: Path,
):
    doc = {"lanes": {"x": {"judge": {"canary": {"target": "c/d.py"}}}}}
    _materialize_lane_dependencies(tmp_path, doc)
    created = tmp_path / "c" / "d.py"
    assert created.is_file()
    # Second call over a target that already exists must not clobber real
    # content -- the paired must-succeed control for the branch above.
    created.write_text("real content\n", encoding="utf-8")
    _materialize_lane_dependencies(tmp_path, doc)
    assert created.read_text(encoding="utf-8") == "real content\n"


# --- (2) closed public vocabulary coverage ----------------------------------


def _docs_text() -> str:
    return "\n".join(doc.read_text(encoding="utf-8") for doc in DOCS)


def _missing_from(values: frozenset[str] | tuple[str, ...], haystack: str) -> set[str]:
    return {value for value in values if value not in haystack}


def test_every_snapshot_selection_value_is_documented():
    assert SNAPSHOT_SELECTIONS, "SNAPSHOT_SELECTIONS must not be empty"
    missing = _missing_from(SNAPSHOT_SELECTIONS, _docs_text())
    assert not missing, f"undocumented isolation.snapshot_selection value(s): {missing}"


def test_every_judge_mode_value_is_documented():
    assert JUDGE_MODES, "JUDGE_MODES must not be empty"
    missing = _missing_from(JUDGE_MODES, _docs_text())
    assert not missing, f"undocumented judge.mode value(s): {missing}"


def test_every_rigor_level_is_documented():
    assert RIGOR_LEVELS, "RIGOR_LEVELS must not be empty"
    missing = _missing_from(RIGOR_LEVELS, _docs_text())
    assert not missing, f"undocumented rigor level(s): {missing}"


def test_every_coverage_format_is_documented():
    formats = tuple(FORMAT_REGISTRY)
    assert formats, "FORMAT_REGISTRY must not be empty"
    missing = _missing_from(formats, _docs_text())
    assert not missing, f"undocumented coverage format(s): {missing}"


def test_every_reason_code_is_documented():
    """(A-277) The fifth derived vocabulary, added in wave 2 because the
    first four did not include it and `ALL_MUTANTS_EQUIVALENT` was therefore
    absent from every user-facing document from v5 until wave 2 found it --
    while the DESIGN-GUIDE's own table declared itself CLOSED and told
    implementers to stop and ask for anything not listed."""
    codes = tuple(code.value for code in ReasonCode)
    assert codes, "ReasonCode must not be empty"
    missing = _missing_from(codes, _docs_text())
    assert not missing, f"undocumented reason_code(s): {missing}"


#: (P34/A-287) The sixth derived vocabulary. `judge.mutation.operators` is a
#: closed public vocabulary a consumer types, and `MUTATION_OPERATORS` (all
#: 14 names across python/go/sql) is NOT the right required set: the
#: controller's ruling is that adding it wholesale would demand documenting
#: three `go:*` operators for which no adapter is registered and no
#: producer exists in THIS build -- documenting an operator a consumer
#: cannot actually run is worse than the gap it closes. So the required set
#: is scoped to the operators of every language `cli._built_in_registry()`
#: actually registers (today: python and sql), derived through
#: `vocabulary.MUTATION_OPERATORS_BY_LANGUAGE` rather than hand-copied --
#: the day a later package registers a Go adapter, this set (and the docs
#: gate below) expands BY ITSELF.
_REGISTERED_LANGUAGES: frozenset[str] = frozenset(_built_in_registry().entries)

REQUIRED_MUTATION_OPERATORS: frozenset[str] = frozenset(
    operator
    for language in _REGISTERED_LANGUAGES
    for operator in MUTATION_OPERATORS_BY_LANGUAGE.get(language, ())
)


def test_every_registered_language_mutation_operator_is_documented():
    """(A-287) The must-succeed proof for check (2)'s sixth vocabulary: every
    operator of every REGISTERED language -- not the whole closed catalogue
    -- appears in at least one of the three documents. Non-empty per A-278:
    a check with nothing to check is not a passing check."""
    assert REQUIRED_MUTATION_OPERATORS, "required mutation-operator set must not be empty"
    missing = _missing_from(REQUIRED_MUTATION_OPERATORS, _docs_text())
    assert not missing, f"undocumented mutation operator(s): {missing}"


def test_the_undocumented_mutation_operators_are_exactly_the_unregistered_languages():
    """(A-287) The companion test the ruling itself requires: the operators
    this check deliberately does NOT demand documentation for
    (`MUTATION_OPERATORS` minus the required set) must be EXACTLY the
    operators of the languages `_built_in_registry()` does not register
    (today: go's three) -- never a looser "some operators are excluded"
    claim, and never vacuously empty (which would mean this build's registry
    already covers the whole closed catalogue and the scoping bought
    nothing)."""
    unregistered_languages = frozenset(MUTATION_OPERATORS_BY_LANGUAGE) - _REGISTERED_LANGUAGES
    assert unregistered_languages, (
        "expected at least one unregistered language (go) for this test to "
        "exercise anything -- a fully-registered build would make this "
        "assertion vacuous"
    )
    excluded = frozenset(MUTATION_OPERATORS) - REQUIRED_MUTATION_OPERATORS
    expected_excluded = frozenset(
        operator
        for language in unregistered_languages
        for operator in MUTATION_OPERATORS_BY_LANGUAGE[language]
    )
    assert excluded == expected_excluded
    assert excluded, "excluded mutation-operator set must not be empty"


def test_a_fabricated_vocabulary_value_is_reported_missing_the_broken_control():
    """Must-fail control for check (2): the exact membership-checking
    routine every vocabulary test above calls must actually detect an
    absent value, proving the check is not vacuously true for any input.
    Paired against the six real, non-empty derived sets above, which all
    pass the identical routine."""
    fabricated = frozenset({"this-value-does-not-appear-in-any-shipped-doc-9f3c2"})
    missing = _missing_from(fabricated, _docs_text())
    assert missing == fabricated


def test_derived_vocabularies_are_not_accidentally_identical_placeholders():
    """Guards against a degenerate derivation (e.g. every vocabulary
    resolving to the same single-element set) that would let the checks
    above pass without actually exercising six independent module facts."""
    reason_codes = {code.value for code in ReasonCode}
    assert SNAPSHOT_SELECTIONS != JUDGE_MODES
    assert set(RIGOR_LEVELS) != SNAPSHOT_SELECTIONS
    assert set(FORMAT_REGISTRY) != JUDGE_MODES
    assert reason_codes != set(FORMAT_REGISTRY)
    assert reason_codes != set(RIGOR_LEVELS)
    assert REQUIRED_MUTATION_OPERATORS != SNAPSHOT_SELECTIONS
    assert REQUIRED_MUTATION_OPERATORS != JUDGE_MODES
    assert REQUIRED_MUTATION_OPERATORS != set(RIGOR_LEVELS)
    assert REQUIRED_MUTATION_OPERATORS != set(FORMAT_REGISTRY)
    assert REQUIRED_MUTATION_OPERATORS != reason_codes


# --- (3) DESIGN-GUIDE anchor resolution --------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_LINK_RE = re.compile(r"\]\(docs/DESIGN-GUIDE\.md#([A-Za-z0-9_-]+)\)")


def _github_slug(heading_text: str) -> str:
    """GitHub's own heading-anchor algorithm, reverse-engineered against this
    file's own PRE-EXISTING links (verified below against real anchors this
    module did not invent): lowercase; drop every character that is not a
    letter, digit, space, hyphen or underscore; then replace each remaining
    space with a hyphen (one for one -- runs of spaces become runs of
    hyphens, never collapsed to one)."""
    lowered = heading_text.lower()
    kept = "".join(ch for ch in lowered if ch.isalnum() or ch in " -_")
    return kept.replace(" ", "-")


def _design_guide_anchors() -> set[str]:
    text = DESIGN_GUIDE.read_text(encoding="utf-8")
    return {_github_slug(match.group(2)) for match in _HEADING_RE.finditer(text)}


def _readme_design_guide_links() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return _LINK_RE.findall(text)


def test_slugify_reproduces_known_preexisting_anchors():
    """Differential proof the slugify function is right, not merely
    self-consistent: these three anchors existed in README.md's links to
    DESIGN-GUIDE.md before this wave touched either file, so they are an
    independent check on the algorithm rather than a value this module
    could have derived from its own bug."""
    assert _github_slug("0. The one invariant") == "0-the-one-invariant"
    assert _github_slug("3. The three tiers of evidence") == "3-the-three-tiers-of-evidence"
    assert (
        _github_slug("4. The boundary with ciu, and why they are not one tool")
        == "4-the-boundary-with-ciu-and-why-they-are-not-one-tool"
    )
    # CMRU / tester-unified integration (docs/CONSUMERS.md) -- the double
    # hyphen is not a typo: removing "/" from " / " leaves two spaces, and
    # GitHub replaces spaces one-for-one rather than collapsing runs.
    assert _github_slug("CMRU / tester-unified integration") == "cmru--tester-unified-integration"


def test_every_readme_design_guide_link_resolves():
    links = _readme_design_guide_links()
    assert links, "expected at least one docs/DESIGN-GUIDE.md#... link in README.md"
    anchors = _design_guide_anchors()
    assert anchors, "DESIGN-GUIDE.md must have at least one heading"
    dangling = [link for link in links if link not in anchors]
    assert not dangling, f"dangling README -> DESIGN-GUIDE anchor(s): {dangling}"


def test_a_dangling_anchor_is_detected_the_broken_control():
    """Must-fail control for check (3): a link naming an anchor DESIGN-GUIDE
    does not carry must be reported dangling by the same membership check
    the real links above pass."""
    anchors = _design_guide_anchors()
    fabricated_anchor = "this-heading-does-not-exist-in-design-guide-7a1e9"
    assert fabricated_anchor not in anchors
    dangling = [link for link in [fabricated_anchor] if link not in anchors]
    assert dangling == [fabricated_anchor]


def test_wave1_new_anchors_are_present_and_resolve():
    """Names the specific wave-1 anchors README links to, so a future rename
    of any one of these headings fails here with a clear cause instead of
    only inside the generic sweep above."""
    anchors = _design_guide_anchors()
    for expected in (
        "two-r1-modes-one-claim-per-lane-a-260",
        "branch-coverage-is-judged-whenever-the-artifact-reports-it-a-258",
        "require_branch-governs-absence-never-presence-a-259",
        "snapshot-selection-an-affirmative-materialisation-boundary-not-a-sandbox-b006a",
    ):
        assert expected in anchors, f"missing DESIGN-GUIDE anchor: {expected}"
        assert expected in _readme_design_guide_links(), (
            f"README does not link to {expected}"
        )
