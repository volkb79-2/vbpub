"""Whole-source controls for small adapter and package boundaries."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import argparse
from pathlib import Path
import json
import sys
from types import MappingProxyType

import pytest

from assay.adapters.base import HelperInvocation, StatementBlockReport
from assay.adapters.base import LanguageAdapter
from assay.runner import ProcessRunner
from conftest import TESTS_ROOT, _validate_b105_exclusion_inventory
from assay.errors import LaneConfigError
from assay import cli


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool", ""),
        ("resolved_path", "  "),
        ("identity", None),
    ],
)
def test_helper_identity_refuses_missing_or_blank_fields(field, value):
    fields = {"tool": "go", "resolved_path": "/usr/bin/go", "identity": "go version"}
    fields[field] = value
    with pytest.raises(ValueError, match=f"HelperInvocation.{field}"):
        HelperInvocation(**fields)


def test_source_tree_package_version_fallback_is_explicit(monkeypatch):
    import assay

    real_version = importlib.metadata.version

    def missing_distribution(name):
        assert name == "assay"
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)
    try:
        importlib.reload(assay)
        assert assay.__version__ == "0+unknown"
    finally:
        monkeypatch.setattr(importlib.metadata, "version", real_version)
        importlib.reload(assay)


def test_go_adapter_passes_statement_block_request_and_deadline_to_oracle(
    tmp_path, monkeypatch
):
    import assay.adapters.go as go_module

    adapter = go_module.GoAdapter()
    paths = ("pkg/a.go", "pkg/b.go")
    deadline = lambda: 12.5
    expected = object()
    seen = {}

    def derive(repo_top, rel_paths, *, remaining, sensitive_values):
        seen.update(
            repo_top=repo_top,
            rel_paths=rel_paths,
            remaining=remaining,
            sensitive_values=sensitive_values,
        )
        return expected

    monkeypatch.setattr(go_module, "derive_statement_blocks", derive)
    assert adapter.statement_blocks(tmp_path, paths, remaining=deadline) is expected
    assert seen == {
        "repo_top": tmp_path,
        "rel_paths": paths,
        "remaining": deadline,
        "sensitive_values": (),
    }


def test_sql_statement_blocks_refuses_the_r1_only_protocol_call():
    from assay.adapters.sql import SqlAdapter

    with pytest.raises(NotImplementedError, match="statement_blocks"):
        SqlAdapter().statement_blocks(Path("/repo"), ("schema.sql",))


def test_every_language_adapter_protocol_stub_is_executed(tmp_path):
    class ProbeAdapter(LanguageAdapter):
        pass

    adapter = ProbeAdapter()
    assert adapter.for_project(repo_top=tmp_path, project_root=tmp_path) is None
    assert adapter.is_test_path("tests/test_sample.py") is None
    assert adapter.has_executable_code("src/sample.py", "pass\n") is None
    assert adapter.normalize_coverage_key("src/sample.py") is None
    assert adapter.statement_spans("pass\n") is None
    assert adapter.statement_blocks(tmp_path, ("src/sample.py",)) is None
    assert adapter.inject_import_break("pass\n") is None
    assert adapter.inject_uncovered_line("pass\n") is None
    assert adapter.generate_mutation_sites(
        "pass\n", set(), operators=(), limit=0
    ) is None


def test_statement_block_report_freezes_a_mutable_mapping():
    report = StatementBlockReport(
        blocks_by_path={"src/sample.go": ()},
        helper=HelperInvocation(
            tool="go", resolved_path="/usr/bin/go", identity="go version go1.26"
        ),
    )

    with pytest.raises(TypeError):
        report.blocks_by_path["src/other.go"] = ()


def test_statement_block_report_keeps_an_already_frozen_mapping():
    frozen = MappingProxyType({"src/sample.go": ()})
    report = StatementBlockReport(
        blocks_by_path=frozen,
        helper=HelperInvocation(
            tool="go", resolved_path="/usr/bin/go", identity="go version go1.26"
        ),
    )

    assert report.blocks_by_path is frozen


def test_process_runner_protocol_stub_is_executed(tmp_path):
    class ProbeProcessRunner(ProcessRunner):
        pass

    assert ProbeProcessRunner()(["true"], env={}, cwd=tmp_path, timeout=None) is None


def test_b105_coverage_exclusion_inventory_requires_exact_raw_lines():
    fixture = TESTS_ROOT / "fixtures/b105-coverage-exclusions.json"
    exclusion_map = json.loads(fixture.read_text(encoding="utf-8"))
    raw = {
        "files": {
            path: {"excluded_lines": entry["lines"]}
            for path, entry in exclusion_map["files"].items()
        }
    }
    _validate_b105_exclusion_inventory(raw, exclusion_map)


def test_every_b105_exclusion_line_is_a_pragma_line_or_inside_its_block():
    fixture = TESTS_ROOT / "fixtures/b105-coverage-exclusions.json"
    exclusion_map = json.loads(fixture.read_text(encoding="utf-8"))
    for rel, entry in exclusion_map["files"].items():
        text = (TESTS_ROOT.parent / rel).read_text(encoding="utf-8")
        pragma_lines = [n for n, line in enumerate(text.splitlines(), 1) if "pragma: no cover" in line]
        blocks: dict[int, tuple[int, int]] = {}
        for node in ast.walk(ast.parse(text)):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and getattr(node, "lineno", None) in pragma_lines:
                blocks[node.lineno] = (body[0].lineno, node.end_lineno)
        for line in entry["lines"]:
            assert line in pragma_lines or any(
                first <= line <= last for first, last in blocks.values()
            ), f"{rel}:{line} is neither a pragma line nor inside a pragma line's block"
        stale = [n for n in pragma_lines if n not in entry["lines"]]
        assert not stale, f"{rel}: pragma lines {stale} are not listed in the exclusion map"


def test_b105_coverage_exclusion_inventory_refuses_new_or_missing_lines():
    exclusion_map = {
        "format": 1,
        "files": {
            "src/assay/example.py": {
                "lines": [4],
                "reason": "annotation-only import",
            }
        },
    }
    with pytest.raises(ValueError, match="differ from the reviewed inventory"):
        _validate_b105_exclusion_inventory(
            {"files": {"src/assay/example.py": {"excluded_lines": [4, 5]}}},
            exclusion_map,
        )


def test_cli_dispatch_assertion_is_reachable_if_parser_contract_breaks(monkeypatch):
    class ParserProbe:
        @staticmethod
        def parse_args(_argv):
            return argparse.Namespace(command="unregistered")

    monkeypatch.setattr(cli, "build_parser", lambda: ParserProbe())
    with pytest.raises(AssertionError, match="unhandled command 'unregistered'"):
        cli.main(["unregistered"])


def test_cli_reports_a_symlink_that_disappears_during_readlink(tmp_path, monkeypatch):
    destination = tmp_path / "destination"
    destination.mkdir()
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)

    def raced_readlink(_path):
        raise FileNotFoundError("simulated link removal")

    monkeypatch.setattr(cli.os, "readlink", raced_readlink)
    with pytest.raises(LaneConfigError, match="link -> <unreadable>"):
        cli._refuse_a_destination_reached_through_a_symlink(
            "link/state.json",
            flag="--state-dir",
            what="resume state",
            root=tmp_path,
            probe=Path("link/state.json"),
        )


def test_cli_module_guard_runs_in_process_under_main_name(monkeypatch, capsys):
    import assay.cli as cli_module

    monkeypatch.setattr(sys, "argv", ["assay", "--help"])
    namespace = {"__name__": "__main__", "__package__": "assay"}
    source = Path(cli_module.__file__).read_bytes()
    with pytest.raises(SystemExit) as stopped:
        exec(compile(source, str(cli_module.__file__), "exec"), namespace)
    assert stopped.value.code == 0
    assert "usage: assay" in capsys.readouterr().out
