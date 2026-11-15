"""The built-in registry reaches only the Rust R1 path (B243)."""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.rust import RustAdapter
from assay.cli import _built_in_registry
from assay.errors import AssayError, Outcome, ReasonCode
from assay.registry import get_adapter


def test_the_built_in_registry_returns_the_rust_adapter_at_r1_only():
    registry = _built_in_registry()
    adapter = get_adapter(registry, "rust", "R1")

    assert type(adapter) is RustAdapter
    assert registry.entries["rust"].rigor == frozenset({"R1"})
    assert adapter.name == "rust"
    assert adapter.source_globs == ("*.rs",)
    assert adapter.excluded_dir_names == frozenset({"vendor", "target"})
    assert adapter.requires_span_attribution is False
    assert adapter.requires_statement_attribution is False
    assert adapter.external_tools == ()
    assert adapter.for_project(repo_top=Path("/repo"), project_root=Path("/repo")) is adapter


@pytest.mark.parametrize("rigor", ["R2", "R3"])
def test_the_registry_refuses_rust_rigors_without_a_producer_path(rigor: str):
    with pytest.raises(AssayError) as excinfo:
        get_adapter(_built_in_registry(), "rust", rigor)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "rust" in str(excinfo.value)
    assert rigor in str(excinfo.value)


def test_rust_adapter_exposes_only_the_declared_r1_text_hooks():
    adapter = RustAdapter()

    assert adapter.normalize_coverage_key("/repo/crates/lib.rs") == "/repo/crates/lib.rs"
    assert adapter.normalize_coverage_text("TN:\n") == "TN:\n"
    assert adapter.statement_spans("fn f() {}\n") is None
    assert adapter.statement_blocks(Path("/repo"), ()) is None
    assert adapter.generate_mutation_sites("fn f() {}", {1}, operators=(), limit=1) == "UNSUPPORTED"
    with pytest.raises(NotImplementedError, match="R-4"):
        adapter.inject_import_break("fn f() {}", nonce="x")
    with pytest.raises(NotImplementedError, match="R-4"):
        adapter.inject_uncovered_line("fn f() {}")
