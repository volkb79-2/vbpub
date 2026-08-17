"""O3 — the Python adapter registers cleanly through P05's own, unmodified
``registry.py`` (this package touches no line of it -- see the LOG's O3
section for the ``git diff --stat`` confirming that), and declares the
protocol surface this package chose, transparently.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome
from assay.evaluate import evaluate_coverage
from assay.registry import RegistryEntry, get_adapter, new_registry


def test_the_python_adapter_registers_under_its_own_declared_name():
    adapter = PythonAdapter()
    registry = new_registry(RegistryEntry(adapter=adapter, rigor=frozenset({"R1"})))

    assert get_adapter(registry, "python", "R1") is adapter
    assert adapter.name == "python"


def test_the_python_adapter_declares_the_expected_protocol_surface():
    """Transparency for the union decisions this package made where the
    three reference gates gave no guidance (LOG's own self-review):
    ``excluded_dir_names`` is the genuine (empty) union -- none of dstdns,
    topos, nyxloom excludes any directory by bare name -- and
    ``requires_span_attribution`` is True, this adapter's own honest
    declaration that real Python coverage.py has the multi-line-statement
    gap A-098 reserves for P07."""
    adapter = PythonAdapter()

    assert adapter.source_globs == ("*.py",)
    assert adapter.excluded_dir_names == frozenset()
    assert adapter.requires_span_attribution is True
    assert adapter.external_tools == ()


def test_a_registry_built_adapter_evaluates_coverage_identically_to_a_direct_one():
    """No hidden state or behaviour differs between an adapter constructed
    directly and one retrieved back out of a fresh ``Registry`` -- the
    registry is a pure lookup (P05's own O2), proven here by actually
    driving ``evaluate_coverage`` with the registry-obtained instance."""
    registry = new_registry(
        RegistryEntry(adapter=PythonAdapter(), rigor=frozenset({"R1"}))
    )
    adapter = get_adapter(registry, "python", "R1")

    added = AddedLines(by_file=MappingProxyType({"pkg/mod.py": frozenset({1})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "pkg/mod.py": FileCoverage(
                    executed=frozenset({1}), missing=frozenset(), excluded=frozenset()
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
