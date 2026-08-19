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

from assay.adapters.go import GoAdapter
from assay.adapters.python import PythonAdapter
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
    """Transparency for the union decisions this package made: A-102's own
    declaration (``requires_span_attribution=False``, confirmed against
    the real block-based coverprofile parser, not assumed) and A-087's own
    declaration (no external tool prerequisite -- the lexer is pure
    Python text processing with no subprocess boundary)."""
    adapter = GoAdapter()

    assert adapter.source_globs == ("*.go",)
    assert adapter.excluded_dir_names == frozenset()
    assert adapter.requires_span_attribution is False
    assert adapter.external_tools == ()


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
        profile=profile,
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
