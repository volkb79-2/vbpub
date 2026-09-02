"""O3 -- the Go adapter registers cleanly through P05's own, UNMODIFIED
``registry.py`` (this package touches no line of it -- see the LOG's O3
section for the ``git diff --stat`` confirming that), declares the
protocol surface this package chose transparently, and coexists with the
Python adapter in one registry -- the concrete, mechanical form of
"a second language is additive" (the claim this whole package attacks).
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from conftest import as_statement_attributed

from assay.adapters.go import GoAdapter
from assay.adapters.python import PythonAdapter
from assay.cli import _built_in_registry
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome
from assay.evaluate import evaluate_coverage
from assay.registry import RegistryEntry, get_adapter, new_registry


def test_the_go_adapter_registers_under_its_own_declared_name():
    adapter = GoAdapter()
    registry = new_registry(RegistryEntry(adapter=adapter, rigor=frozenset({"R1"})))

    assert get_adapter(registry, "go", "R1") is adapter
    assert adapter.name == "go"


def test_the_go_adapter_declares_the_expected_protocol_surface():
    """Transparency for the union decisions this adapter carries, including
    the two the P27 re-carve changed.

    ``requires_span_attribution`` is still ``False`` (A-102, confirmed
    against the real block-based coverprofile parser, not assumed): Go's
    format leaves no unattributed line, so there is no gap to rescue.

    ``requires_statement_attribution`` is ``True`` and ``external_tools`` is
    ``("go",)`` -- both changed here, and they change together. A-217 ruled
    that Go statement positions must come from a source-side oracle running
    the real ``cmd/cover`` segmentation, because the
    ``collision-col{A,B}.go`` witness pair proves no profile-only rule can be
    right on both; running that oracle is a subprocess, and A-013 says a
    subprocess boundary is declared up front. The pair is asserted TOGETHER
    on purpose: declaring one without the other is the broken state (an
    undeclared toolchain crashes mid-lane; a declared toolchain with no
    consumer refuses lanes for nothing), and this test goes red on either
    half alone."""
    adapter = GoAdapter()

    assert adapter.source_globs == ("*.go",)
    assert adapter.excluded_dir_names == frozenset()
    assert adapter.requires_span_attribution is False
    assert adapter.requires_statement_attribution is True
    assert adapter.external_tools == ("go",)


def test_the_adapter_the_REGISTRY_hands_a_lane_is_the_undowngraded_one():
    """B057's third acceptance box: a test that goes RED if the shipped
    declaration is ever flipped, so **the double can never quietly become the
    product**.

    The test above asserts it of ``GoAdapter()``. This asserts it of the
    object a real lane actually resolves, which is the one that matters and
    is not the same claim. When this was written the suite carried a real
    example of the hazard: ``tests/test_canary_go_pipeline.py``'s
    ``_PreOracleGoAdapter``, a SUBCLASS of this very class with
    ``requires_statement_attribution`` flipped to ``False``. F008-A4 retired
    that double -- the regenerated fixtures are joined against a real oracle
    document, so the shipped adapter judges them undowngraded -- and this
    test is kept precisely BECAUSE the double is gone: nothing else would
    notice a downgraded instance reappearing. A subclass registered by
    accident, or a downgraded instance constructed in
    ``cli._built_in_registry``, would satisfy every ``isinstance`` check in
    the suite while removing A-392's guard from every Go lane this build runs
    -- and a removed guard is invisible, because an uncorrected profile
    parses cleanly and yields a plausible percentage.

    ``type(...) is GoAdapter`` rather than ``isinstance`` is therefore the
    point of the second assertion, not pedantry."""
    registry = _built_in_registry()
    adapter = get_adapter(registry, "go", "R1")

    assert type(adapter) is GoAdapter, (
        f"the built-in registry hands a Go lane a {type(adapter).__name__}, "
        "not the shipped adapter itself"
    )
    assert adapter.requires_statement_attribution is True
    assert adapter.external_tools == ("go",)


def test_the_go_adapters_statement_spans_returns_none_unconditionally():
    """P07's own protocol extension is satisfied only structurally here
    (A-101/A-102): ``evaluate.py`` never calls this method for an adapter
    declaring ``requires_span_attribution=False``, so its body is trivial
    by design -- proven directly, not merely by absence of a call site."""
    adapter = GoAdapter()

    assert adapter.statement_spans("package x\n\nfunc F() {}\n") is None
    assert adapter.statement_spans("") is None


def test_a_registry_built_go_adapter_evaluates_coverage_identically_to_a_direct_one():
    """No hidden state or behaviour differs between an adapter constructed
    directly and one retrieved back out of a fresh ``Registry`` -- the
    registry is a pure lookup (P05's own O2), proven here by actually
    driving ``evaluate_coverage`` with the registry-obtained Go instance."""
    registry = new_registry(RegistryEntry(adapter=GoAdapter(), rigor=frozenset({"R1"})))
    adapter = get_adapter(registry, "go", "R1")

    added = AddedLines(by_file=MappingProxyType({"pkg/f.go": frozenset({1})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/f.go": FileCoverage(
                    executed=frozenset({1}), missing=frozenset(), excluded=None
                )
            }
        )
    )

    def read_source_text(path: str) -> str:
        raise AssertionError("this file has a coverage entry")

    result = evaluate_coverage(
        added=added,
        # (A-392) Hand-built, already statement-granular line sets; the flag
        # is what lets a `requires_statement_attribution` adapter judge them.
        profile=as_statement_attributed(profile),
        adapter=adapter,
        repo_top=Path("/repo"),
        project_root=Path("/repo"),
        source_root_paths=(Path("/repo"),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    assert result.outcome is Outcome.PASS
    assert result.covered == 1


def test_go_and_python_adapters_coexist_in_one_registry_each_independently_addressable():
    """The mechanical proof of additivity: registering BOTH real adapters
    together, neither shadows nor is required by the other -- the registry
    (and every module it depends on) needed zero changes to accommodate a
    second language (O3)."""
    go_adapter = GoAdapter()
    python_adapter = PythonAdapter()
    registry = new_registry(
        RegistryEntry(adapter=go_adapter, rigor=frozenset({"R1"})),
        RegistryEntry(adapter=python_adapter, rigor=frozenset({"R1"})),
    )

    assert get_adapter(registry, "go", "R1") is go_adapter
    assert get_adapter(registry, "python", "R1") is python_adapter
    assert get_adapter(registry, "go", "R1") is not get_adapter(registry, "python", "R1")


def test_a_real_built_in_registry_entry_now_exercises_the_external_tool_preflight():
    """The fact that falsified A-087's premise, pinned where a future reader
    will trip over it (Wave C round-1 review, BLOCKER 2).

    ``registry.py``'s module docstring used to justify adding no preflight
    machinery of its own with "every adapter a built-in registry can
    advertise today (Python, Go, and P34's own SQL) declares
    ``external_tools = ()``, so no real entry here ever exercises one." That
    was true when A-087 wrote it and is false now: B047 item 2 gave
    ``GoAdapter`` ``external_tools = ("go",)`` and A-394 registered that
    adapter in ``cli._built_in_registry`` at R1, so a real entry exercises
    ``run_lane``'s preflight on every Go lane.

    The assertion is deliberately made over the WHOLE built-in registry
    rather than over ``GoAdapter`` alone (which
    ``test_the_go_adapter_declares_the_expected_protocol_surface`` already
    pins): the stale sentence was about what a *registry* can advertise, so
    the guard has to be too. It goes red if Go is unregistered, if its
    declaration reverts to ``()``, or if some later wave drops the entry --
    each of which would make the deleted sentence true again and this
    module's rewritten paragraph the stale one."""
    entries = _built_in_registry().entries

    declaring = {
        name: entry.adapter.external_tools
        for name, entry in entries.items()
        if entry.adapter.external_tools
    }

    assert declaring, (
        "no built-in registry entry declares an external tool; "
        "registry.py's rewritten preflight paragraph is now the stale one"
    )
    assert declaring["go"] == ("go",)
    assert "R1" in entries["go"].rigor
