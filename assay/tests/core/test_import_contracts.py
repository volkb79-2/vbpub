"""Component import contracts and test-directory layout (B130, A-478).

Part 1 - import contracts.  The judge (``src/assay``) is one package with named
components: the core, the four language adapters, three parser families and
the CLI composition root (the analysis package is outside it, A-478).  This test walks
every module's imports with ``ast`` (module level, function level and
``TYPE_CHECKING``) and refuses any edge that crosses a component boundary the
rules below do not allow.  It is stdlib-only on purpose: an ``import-linter``
wheel would have to join the gate's pinned closure (CD10).

Part 2 - layout.  Every ``tests/**/test_*.py`` lives in the folder its name
implies.  The layout claims nothing about which tests kill which mutants.
"""

from __future__ import annotations

import ast
from pathlib import Path

from conftest import PROJECT_ROOT, TESTS_ROOT

SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_DIR = SRC_ROOT / "assay"

# --------------------------------------------------------------------------
# Part 1: import contracts
# --------------------------------------------------------------------------


def component(m: str) -> str:
    if m in ("assay.adapters", "assay.adapters.base"):
        return "core"
    for lang in ("python", "javascript", "go", "sql"):
        if m.startswith(f"assay.adapters.{lang}"):
            return f"adapter.{lang}"
    for pkg, name in (
        ("coverage_parsers", "parsers.coverage"),
        ("mutation_parsers", "parsers.mutation"),
        ("result_reports", "parsers.result_reports"),
    ):
        if m == f"assay.{pkg}" or m.startswith(f"assay.{pkg}."):
            return name
    if m == "assay.cli":
        return "cli"  # composition root
    return "core"


# CD27: the core leaf modules adapters and parsers may use, nothing else from core.
ADAPTER_DEPS = {
    "assay.adapters.base",
    "assay.errors",
    "assay.mutation",
    "assay.safeio",
    "assay.statement_attribution",
    "assay.records",
    "assay.guards",
}
PARSER_DEPS = {"assay.errors", "assay.vocabulary", "assay.records", "assay.guards"}
CORE_PARSER_SURFACE = {
    "assay.coverage_parsers.model",
    "assay.mutation_parsers",
    "assay.mutation_parsers.model",
    "assay.result_reports",
}


def allowed(importer: str, imported: str) -> bool:
    ci, ct = component(importer), component(imported)
    if ci == "cli":
        return True
    if ci == ct:
        return True
    if ci.startswith("adapter."):
        return imported in ADAPTER_DEPS
    if ci.startswith("parsers."):
        return imported in PARSER_DEPS
    if ct == "parsers.coverage" and importer == "assay.coverage":
        return True  # format dispatcher
    return imported in CORE_PARSER_SURFACE


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _module_paths() -> dict[str, Path]:
    return {module_name(p): p for p in sorted(PACKAGE_DIR.rglob("*.py"))}


def edges_of(importer: str, source: str, is_package: bool, known: set[str]) -> set[tuple[str, str]]:
    """Assay-internal import edges of one module, from every ``Import``/``ImportFrom`` node."""
    package = importer if is_package else importer.rpartition(".")[0]
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")
                up = up[: len(up) - (node.level - 1)]
                base = ".".join(up + ([node.module] if node.module else []))
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                imported.append(candidate if candidate in known else base)
        for name in imported:
            if (name == "assay" or name.startswith("assay.")) and name != importer:
                found.add((importer, name))
    return found


def import_edges() -> tuple[set[str], set[tuple[str, str]]]:
    paths = _module_paths()
    known = set(paths)
    edges: set[tuple[str, str]] = set()
    for name, path in paths.items():
        edges |= edges_of(name, path.read_text(encoding="utf-8"), path.name == "__init__.py", known)
    return known, edges


def test_real_tree_has_no_boundary_violation():
    modules, edges = import_edges()
    expected = {module_name(p) for p in sorted((PROJECT_ROOT / "src" / "assay").rglob("*.py"))}
    assert modules and modules == expected
    assert ("assay.cli", "assay.adapters.python") in edges
    assert ("assay.coverage", "assay.coverage_parsers.lcov") in edges
    assert ("assay.mutation_parsers", "assay.errors") in edges
    violations = sorted(f"{a} -> {b}" for a, b in edges if not allowed(a, b))
    assert not violations, "component boundary violations:\n" + "\n".join(violations)


def test_allowed_refuses_and_accepts_the_documented_edges():
    refused = [
        ("assay.runner", "assay.adapters.python"),
        ("assay.adapters.go", "assay.adapters.python"),
        ("assay.coverage_parsers.lcov", "assay.runner"),
        ("assay.evaluate", "assay.coverage_parsers.lcov"),
        ("assay.verdict", "assay.cli"),  # core must not import the composition root
        ("assay.adapters.sql", "assay.config"),
        ("assay.adapters.go", "assay.runner"),
    ]
    accepted = [
        ("assay.coverage", "assay.coverage_parsers.lcov"),
        ("assay.adapters.go", "assay.adapters.go_modfile"),
        ("assay.adapters.go", "assay.records"),
        ("assay.coverage_parsers.lcov", "assay.guards"),
    ]
    assert [edge for edge in refused if allowed(*edge)] == []
    assert [edge for edge in accepted if not allowed(*edge)] == []


def test_edge_walk_sees_type_checking_and_function_level_relative_imports():
    known = set(_module_paths())
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from assay.runner import X\n"
        "def f():\n"
        "    from ..adapters import python\n"
    )
    assert edges_of("assay.coverage_parsers.fake", source, False, known) == {
        ("assay.coverage_parsers.fake", "assay.runner"),
        ("assay.coverage_parsers.fake", "assay.adapters.python"),
    }
    assert edges_of("assay.adapters", "from .base import LanguageAdapter\n", True, known) == {
        ("assay.adapters", "assay.adapters.base")
    }


PARSER_PACKAGES = ("coverage_parsers", "mutation_parsers", "result_reports")


def unmapped_modules(paths: dict[str, Path]) -> list[str]:
    """Modules under adapters/ or a parser package that component() files under core by default."""
    bad = []
    for name, path in paths.items():
        rel = path.relative_to(PACKAGE_DIR)
        if rel.parts[0] == "adapters" and rel.name not in ("__init__.py", "base.py"):
            if not component(name).startswith("adapter."):
                bad.append(name)
        elif rel.parts[0] in PARSER_PACKAGES and not component(name).startswith("parsers."):
            bad.append(name)
    return sorted(bad)


def test_no_adapter_or_parser_module_falls_into_core_by_default():
    assert unmapped_modules(_module_paths()) == []
    synthetic = {"assay.adapters.rust": PACKAGE_DIR / "adapters" / "rust.py"}
    assert unmapped_modules(synthetic) == ["assay.adapters.rust"]


# --------------------------------------------------------------------------
# Part 2: test-directory layout
# --------------------------------------------------------------------------

ROOT_PINNED = frozenset(
    {  # TEMPORARY (CD23): W4 moves every remaining name into a component folder and deletes this list.
        # Each package removes its own names in its own commit (W1 3, W2 2, W5 1, W4 the rest).
        "test_self_hosting.py",
        "test_runner_snapshot_selection.py",
        "test_lane_schema_v2_locked_successors.py",
        "test_verdict_v13_successors.py",  # path-referenced by gate script/assay.toml until W1 edits them
        "test_b106_reuse_and_witness.py",  # generated-code literals contain Path(__file__) (:668,745,860,872)
        "test_distribution_build_release.py",  # path-referenced from build_release.py
        "test_verdict_conformance.py",
        "test_errors.py",  # conformance imports errors (:296); carve-assets/P23 pin
        # rewritten by W5, moved by W2 or W4
        "test_gate_qualify_dstdns_sql.py",
        "test_distribution_gate.py",
        "test_distribution_release_wheel.py",
        "test_standalone.py",
        "test_cgroup_parent.py",
        "test_self_lane.py",
        "test_go_helper_is_packaged.py",
        "test_verdict_schema_is_packaged.py",
        "test_dependency_purity.py",
        "test_b105_report_check.py",
        "test_gate_failure_diagnostics.py",
    }
)
EXCEPTIONS = {"test_evaluate_javascript_end_to_end.py": "parsers/coverage"}  # imports test_coverage_istanbul_default_arg_signature
LANGS = ("python", "javascript", "go", "sql")


def expected_dir(name: str) -> str:
    if name in ROOT_PINNED:
        return ""
    if name in EXCEPTIONS:
        return EXCEPTIONS[name]
    words = name[len("test_") : -len(".py")].split("_")
    if words[0] == "adapters" and words[1] in LANGS:
        return f"adapters/{words[1]}"
    if words[0] == "coverage":
        return "parsers/coverage"
    if name in ("test_mutation_format_registry.py", "test_mutation_report_json_parser.py"):
        return "parsers/mutation"
    if words[:2] in (["result", "report"], ["result", "reports"]):
        return "parsers/result_reports"
    for lang in LANGS:
        if lang in words:
            return f"adapters/{lang}"
    return "core"


def _tests_files() -> list[Path]:
    """Every file under tests/ relative to it, minus fixtures/ and qualification/."""
    skipped = ("fixtures", "qualification")
    return sorted(
        p.relative_to(TESTS_ROOT)
        for p in TESTS_ROOT.rglob("*")
        if p.is_file() and p.relative_to(TESTS_ROOT).parts[0] not in skipped and "__pycache__" not in p.parts
    )


def misplaced(rel_paths: list[Path]) -> list[str]:
    bad = []
    for rel in rel_paths:
        if rel.name.startswith("test_") and rel.suffix == ".py":
            want = expected_dir(rel.name)
            if rel.parent.as_posix() != (want or "."):
                bad.append(f"{rel.as_posix()} should be in tests/{want}".rstrip("/"))
    return bad


def test_every_test_file_is_in_its_component_folder():
    assert misplaced(_tests_files()) == []


def test_test_basenames_are_unique_and_no_extra_packages_or_conftests():
    files = _tests_files()
    names = [p.name for p in files if p.name.startswith("test_") and p.suffix == ".py"]
    assert names and len(names) == len(set(names))
    assert [p.as_posix() for p in files if p.name == "__init__.py"] == []
    assert [p.as_posix() for p in files if p.name == "conftest.py" and p.parent != Path(".")] == []


def test_misplaced_test_file_is_reported():
    assert misplaced([Path("test_cli_run.py")]) == ["test_cli_run.py should be in tests/core"]
    assert misplaced([Path("core/test_cli_run.py"), Path("test_self_lane.py")]) == []


def test_expected_dir_table():
    table = {
        "test_adapters_go_x.py": "adapters/go",
        "test_coverage_lcov_x.py": "parsers/coverage",
        "test_mutation_format_registry.py": "parsers/mutation",
        "test_result_reports_x.py": "parsers/result_reports",
        "test_runner_sql_x.py": "adapters/sql",
        "test_evaluate_javascript_end_to_end.py": "parsers/coverage",
        "test_self_lane.py": "",
        "test_cli_run.py": "core",
    }
    assert {name: expected_dir(name) for name in table} == table


def suffix_style_test_files(rel_paths: list[Path]) -> list[str]:
    """pytest also collects `*_test.py`; the layout rule is written for `test_*.py` only."""
    return [p.as_posix() for p in rel_paths if p.suffix == ".py" and p.name.endswith("_test.py")]


def test_no_suffix_style_test_files():
    assert suffix_style_test_files(_tests_files()) == []
    assert suffix_style_test_files([Path("escape_test.py")]) == ["escape_test.py"]


def cross_folder_test_imports(sources: dict[Path, str]) -> list[str]:
    """`from test_x import ...` resolves only when test_x's folder is on sys.path (pytest's prepend
    import mode inserts each collected file's folder), so it passes in a full run and fails in a
    one-folder run unless both files share a folder."""
    folder_of = {rel.stem: rel.parent for rel in sources}
    bad = []
    for rel, source in sorted(sources.items()):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.startswith("test_") and folder_of.get(name) != rel.parent:
                    bad.append(f"{rel.as_posix()} imports {name}")
    return bad


def test_test_modules_import_only_test_modules_in_their_own_folder():
    files = [p for p in _tests_files() if p.name.startswith("test_") and p.suffix == ".py"]
    assert cross_folder_test_imports({p: (TESTS_ROOT / p).read_text(encoding="utf-8") for p in files}) == []
    synthetic = {
        Path("adapters/javascript/test_javascript_x.py"): "def test_a():\n    from test_coverage_y import z\n",
        Path("parsers/coverage/test_coverage_y.py"): "z = 1\n",
    }
    assert cross_folder_test_imports(synthetic) == ["adapters/javascript/test_javascript_x.py imports test_coverage_y"]


# --------------------------------------------------------------------------
# Part 3: the one lazy seam to the analysis package (A-478)
# --------------------------------------------------------------------------


def analysis_import_sites(source: str) -> list[str | None]:
    """The enclosing ``FunctionDef`` name (``None`` at module level) of every import of ``assay_analysis``."""
    hits: list[str | None] = []

    def visit(node: ast.AST, enclosing: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            names = []
        hits.extend(enclosing for name in names if name == "assay_analysis" or name.startswith("assay_analysis."))
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(ast.parse(source), None)
    return hits


def test_only_cli_run_analyze_imports_assay_analysis():
    hits = [
        (path.relative_to(PROJECT_ROOT).as_posix(), site)
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        for site in analysis_import_sites(path.read_text(encoding="utf-8"))
    ]
    assert hits == [("src/assay/cli.py", "_run_analyze")]


def test_analysis_import_checker_refuses_a_module_level_import():
    assert analysis_import_sites("import assay_analysis.cli\n") == [None]
    assert analysis_import_sites("from assay_analysis import cli\n") == [None]
    assert analysis_import_sites("def f():\n    from assay_analysis.cli import main\n") == ["f"]
    assert analysis_import_sites("import assay_analysis_other\nimport json\n") == []
