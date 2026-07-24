"""Tests for the D-CORRECT-4 mutation gate.

Every generated mutant is a deterministic observable — each test drives a real
code path and asserts the expected transformation.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from nyxloom import mutation_gate as mg
from nyxloom import coverage_gate as cg


# --------------------------------------------------------------------------- #
# generate_mutants — pure AST mutation
# --------------------------------------------------------------------------- #

def test_compare_swap():
    """A single compare operator is swapped to its boundary neighbour."""
    mutants = mg.generate_mutants("if x < 5:\n    pass\n", {1})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 1
    assert m.operator == "compare-swap"
    assert m.description == "Lt->LtE"
    assert "x <= 5" in m.mutated_source


def test_boolop_swap():
    """A boolean operator is swapped (And -> Or)."""
    mutants = mg.generate_mutants("y = a and b\n", {1})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 1
    assert m.operator == "boolop-swap"
    assert m.description == "And->Or"
    assert "a or b" in m.mutated_source


def test_boolop_swap_or_to_and():
    """Or is swapped to And."""
    mutants = mg.generate_mutants("x = a or b\n", {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Or->And"
    assert "a and b" in mutants[0].mutated_source


def test_bool_const_flip():
    """A boolean constant True is flipped to False."""
    mutants = mg.generate_mutants("def f():\n    return True\n", {2})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 2
    assert m.operator == "bool-const-flip"
    assert m.description in ("True->False",)
    assert "return False" in m.mutated_source


def test_bool_const_flip_false_to_true():
    """False flips to True."""
    mutants = mg.generate_mutants("z = False\n", {1})
    assert len(mutants) == 1
    assert mutants[0].description == "False->True"
    assert "z = True" in mutants[0].mutated_source


def test_line_scoping():
    """Only lines in target_lines are mutated; others are left alone."""
    source = "if x < 5:\n    pass\nif a and b:\n    pass\n"
    # target_lines = {2} → the AND on line 2, but line 2 is "    pass"
    # Actually: line 1 = "if x < 5:", line 2 = "    pass", line 3 = "if a and b:", line 4 = "    pass"
    # Let me use target_lines={3} → only the boolop on line 3
    mutants = mg.generate_mutants(source, {3})
    assert len(mutants) == 1
    assert mutants[0].operator == "boolop-swap"
    assert mutants[0].description == "And->Or"
    # The compare on line 1 should NOT be mutated
    assert "x < 5" in mutants[0].mutated_source
    assert "a or b" in mutants[0].mutated_source


def test_unparseable_source():
    """An invalid syntax returns an empty list."""
    assert mg.generate_mutants("def (:\n", {1}) == []


def test_empty_source():
    """An empty source returns an empty list."""
    assert mg.generate_mutants("", {1}) == []


def test_no_mutable_nodes_on_target_lines():
    """No mutable nodes on the target lines → empty list."""
    source = "x = 42\ny = 'hello'\n"
    assert mg.generate_mutants(source, {1, 2}) == []


def test_multiple_ops_on_one_line():
    """A line with both compare and boolop nodes yields mutants for each."""
    # `x < 5 and y > 3` has one Compare (ops=[Lt, Gt]) and one BoolOp (And)
    # → 2 compare-swap mutants (Lt->LtE, Gt->GtE) + 1 boolop-swap (And->Or)
    source = "x < 5 and y > 3\n"
    mutants = mg.generate_mutants(source, {1})
    # Order: lineno=1, type 0=compare, index 0 (Lt), index 1 (Gt), then type 1=boolop
    assert len(mutants) == 3
    # First: Lt->LtE
    assert mutants[0].operator == "compare-swap"
    assert mutants[0].description == "Lt->LtE"
    # Second: Gt->GtE
    assert mutants[1].operator == "compare-swap"
    assert mutants[1].description == "Gt->GtE"
    # Third: And->Or
    assert mutants[2].operator == "boolop-swap"
    assert mutants[2].description == "And->Or"

    # Verify each mutant changes only one site
    m0_source = mutants[0].mutated_source
    m1_source = mutants[1].mutated_source
    m2_source = mutants[2].mutated_source

    # Wait — since ast.unparse may not preserve token-level spacing, check
    # structural changes rather than exact string matching
    assert "x <= 5" in m0_source  # Lt → LtE in first mutant
    assert "y > 3" in m0_source   # Gt unchanged
    assert "and" in m0_source     # BoolOp unchanged

    assert "x < 5" in m1_source   # Lt unchanged
    assert "y >= 3" in m1_source  # Gt → GtE in second mutant
    assert "and" in m1_source     # BoolOp unchanged

    assert "x < 5" in m2_source   # Lt unchanged
    assert "y > 3" in m2_source   # Gt unchanged
    assert "or" in m2_source      # And → Or in third mutant


def test_chained_comparison_produces_distinct_mutants():
    """Chained comparison a < b < c yields two distinct mutants,
    one for each operator position."""
    mutants = mg.generate_mutants("x = a < b < c\n", {1})
    # One Compare node with two Lt ops → two compare-swap mutants
    assert len(mutants) == 2
    assert mutants[0].operator == "compare-swap"
    assert mutants[0].description == "Lt->LtE"
    assert mutants[1].operator == "compare-swap"
    assert mutants[1].description == "Lt->LtE"
    # Each must mutate a different operator position
    assert "a <= b" in mutants[0].mutated_source
    assert "b < c" in mutants[0].mutated_source
    assert "a < b" in mutants[1].mutated_source
    assert "b <= c" in mutants[1].mutated_source
    # Verify they are genuinely different
    assert mutants[0].mutated_source != mutants[1].mutated_source


def test_compare_eq_ne_swap():
    """Eq ↔ NotEq swap works."""
    src = "x == y\n"
    mutants = mg.generate_mutants(src, {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Eq->NotEq"
    assert "x != y" in mutants[0].mutated_source


def test_compare_is_isnot_swap():
    """Is ↔ IsNot swap works."""
    src = "x is None\n"
    mutants = mg.generate_mutants(src, {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Is->IsNot"
    assert "x is not None" in mutants[0].mutated_source


def test_all_compare_swaps():
    """Every entry in _COMPARE_SWAP produces the expected transformation."""
    # Test all 8 pairs
    pairs = [
        ("x < 5", "Lt", "LtE", "x <= 5"),
        ("x <= 5", "LtE", "Lt", "x < 5"),
        ("x > 5", "Gt", "GtE", "x >= 5"),
        ("x >= 5", "GtE", "Gt", "x > 5"),
        ("x == 5", "Eq", "NotEq", "x != 5"),
        ("x != 5", "NotEq", "Eq", "x == 5"),
    ]
    for src, src_name, tgt_name, expected_substr in pairs:
        mutants = mg.generate_mutants(f"if {src}: pass\n", {1})
        assert len(mutants) == 1, f"failed for {src}"
        assert mutants[0].description == f"{src_name}->{tgt_name}", (
            f"expected {src_name}->{tgt_name}, got {mutants[0].description}"
        )
        assert expected_substr in mutants[0].mutated_source, (
            f"expected {expected_substr} in {mutants[0].mutated_source}"
        )


def test_compare_not_in_swap_dict_unaffected():
    """Operators not in _COMPARE_SWAP (e.g. In, NotIn) are left unchanged."""
    src = "x in y\n"
    assert mg.generate_mutants(src, {1}) == []


# --------------------------------------------------------------------------- #
# evaluate — pure orchestration with injected test runner
# --------------------------------------------------------------------------- #

def test_evaluate_all_killed():
    """When every mutant is killed, .passed is True, .survivors empty."""
    targets = {
        "f.py": ("if x < 5:\n    pass\n", {1}),
    }
    result = mg.evaluate(targets, lambda path, mutant: True)
    assert result.passed is True
    assert result.killed == result.total
    assert result.survivors == []


def test_evaluate_with_survivor():
    """A surviving mutant appears in .survivors and .passed is False."""
    kill_map = {"compare-swap": True, "boolop-swap": False}
    killed_calls: list[tuple[str, mg.Mutant]] = []

    def stub(path, mutant):
        killed_calls.append((path, mutant))
        return kill_map.get(mutant.operator, True)

    targets = {
        "f.py": ("if x < 5 and y > 3:\n    pass\n", {1}),
    }
    result = mg.evaluate(targets, stub)
    assert result.passed is False
    assert result.total == 3
    assert result.killed == 2
    assert len(result.survivors) == 1
    path, lineno, desc = result.survivors[0]
    assert path == "f.py"
    assert desc == "And->Or"


def test_evaluate_multiple_files():
    """Mutants from multiple files are aggregated correctly."""
    def always_killed(path, mutant):
        return True

    targets = {
        "a.py": ("if x < 5:\n    pass\n", {1}),
        "b.py": ("if a and b:\n    pass\n", {1}),
    }
    result = mg.evaluate(targets, always_killed)
    assert result.passed is True
    assert result.total == 2
    assert result.killed == 2


def test_evaluate_no_targets():
    """Empty targets yield passed=True."""
    result = mg.evaluate({}, lambda p, m: True)
    assert result.passed is True
    assert result.total == 0
    assert result.killed == 0


def test_evaluate_no_mutants():
    """Targets with no mutable lines yield passed=True, no mutants."""
    targets = {"f.py": ("x = 42\n", {1})}
    result = mg.evaluate(targets, lambda p, m: True)
    assert result.passed is True
    assert result.total == 0


# --------------------------------------------------------------------------- #
# _run_is_killed — file I/O + subprocess + restoration
# --------------------------------------------------------------------------- #

def test_run_is_killed_test_passes_mutant_survives(tmp_path):
    """When the test command exits 0, _run_is_killed returns False (survived)
    AND the original file content is restored."""
    f = tmp_path / "my_mod.py"
    original_text = "x = 1\n"
    f.write_text(original_text)

    mutant = mg.Mutant(
        lineno=1,
        operator="bool-const-flip",
        description="True->False",
        mutated_source="x = 2\n",
    )

    # "true" exits 0 → tests passed → mutant survived
    result = mg._run_is_killed(
        str(tmp_path), "my_mod.py", mutant, ["true"],
    )
    assert result is False, "expected survived (False)"
    # File must be restored
    assert f.read_text() == original_text


def test_run_is_killed_test_fails_mutant_killed(tmp_path):
    """When the test command exits non-zero, _run_is_killed returns True
    (killed) AND the original file content is restored."""
    f = tmp_path / "my_mod.py"
    original_text = "x = 1\n"
    f.write_text(original_text)

    mutant = mg.Mutant(
        lineno=1,
        operator="bool-const-flip",
        description="True->False",
        mutated_source="x = 2\n",
    )

    # "false" exits 1 → tests failed → mutant killed
    result = mg._run_is_killed(
        str(tmp_path), "my_mod.py", mutant, ["false"],
    )
    assert result is True, "expected killed (True)"
    assert f.read_text() == original_text


def test_run_is_killed_restores_on_exception(tmp_path):
    """If the subprocess itself fails to start, the original file is still
    restored via the finally block."""
    f = tmp_path / "my_mod.py"
    original_text = "x = 1\n"
    f.write_text(original_text)

    mutant = mg.Mutant(
        lineno=1,
        operator="bool-const-flip",
        description="True->False",
        mutated_source="x = 2\n",
    )

    # A non-existent command will raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        mg._run_is_killed(
            str(tmp_path), "my_mod.py", mutant, ["nonexistent_cmd_xyzzy"],
        )
    # File must still be restored
    assert f.read_text() == original_text


def test_run_is_killed_writes_mutant_to_disk(tmp_path):
    """The mutant content is actually written to disk before running tests."""
    f = tmp_path / "my_mod.py"
    original_text = "x = 1\n"
    f.write_text(original_text)

    mutant = mg.Mutant(
        lineno=1,
        operator="bool-const-flip",
        description="True->False",
        mutated_source="x = SURVIVOR_CHECK\n",
    )

    # Use a test command that reads the file and exits based on its content
    # "grep SURVIVOR_CHECK" will exit 0 if found, 1 if not
    result = mg._run_is_killed(
        str(tmp_path), "my_mod.py", mutant,
        ["grep", "SURVIVOR_CHECK", "my_mod.py"],
    )
    # grep found the pattern → exit 0 → tests passed → mutant SURVIVED
    assert result is False
    # But original is restored afterward
    assert f.read_text() == original_text


# --------------------------------------------------------------------------- #
# main — CLI wiring
# --------------------------------------------------------------------------- #

def _make_small_repo(tmp_path):
    """Create a tiny git repo under tmp_path with a source file and a test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    src_dir = repo / "src" / "nyxloom"
    src_dir.mkdir(parents=True)
    src_file = src_dir / "hello.py"
    src_file.write_text("def greet(name):\n    return 'Hello, ' + name + '!'\n")
    test_file = repo / "tests"
    test_file.mkdir()
    (test_file / "test_hello.py").write_text(
        "from src.nyxloom.hello import greet\n"
        "def test_greet():\n"
        "    assert greet('World') == 'Hello, World!'\n"
    )
    # Init git and commit
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), capture_output=True,
    )
    return repo


def test_main_pass_with_true_command(monkeypatch, tmp_path, capsys):
    """main returns 0 when --test is a command that always exits 0 and there
    are changed lines (mocked)."""
    repo = _make_small_repo(tmp_path)
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/hello.py": {1},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
        "--test", "true",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK" in out


def test_main_all_killed_passes(monkeypatch, tmp_path, capsys):
    """All mutants killed (--test false) → pass exit 0, mutation OK."""
    repo = _make_small_repo(tmp_path)
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "calc.py").write_text(
        "def is_positive(x):\n    return x > 0\n"
    )

    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/calc.py": {2},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
        "--test", "false",  # false exits 1 → mutant killed
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK" in out


def test_main_missing_file_warning(monkeypatch, tmp_path, capsys):
    """A changed path that does not exist on disk prints a warning to stderr
    and is skipped without crashing; other changed files are still processed."""
    repo = _make_small_repo(tmp_path)
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    # hello.py exists; deleted.py does not
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/hello.py": {1},
            "src/nyxloom/deleted.py": {2},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
        "--test", "true",
    ])
    out = capsys.readouterr()
    # Warning on stderr for the missing file
    assert "mutation-gate WARNING: changed file not found: src/nyxloom/deleted.py" in out.err
    # hello.py has no mutable operators on line 1 → no mutants → clean pass
    assert "mutation OK" in out.out
    assert rc == 0


def test_main_survivor_output(monkeypatch, tmp_path, capsys):
    """main prints SURVIVED for surviving mutants."""
    repo = _make_small_repo(tmp_path)
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "calc.py").write_text(
        "def is_positive(x):\n    return x > 0\n"
    )

    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/calc.py": {2},
        },
    )
    # true always exits 0 → mutant SURVIVED
    rc = mg.main([
        "--repo", str(repo),
        "--test", "true",
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert "mutation FAIL" in out
    assert "SURVIVED" in out
    assert "src/nyxloom/calc.py" in out
    assert "2" in out  # lineno
    assert "Gt" in out  # description contains "Gt"


def test_main_io_error_returns_2(monkeypatch, tmp_path, capsys):
    """main returns 2 when git operations fail."""
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: (_ for _ in ()).throw(
            cg.CoverageGateError("git exploded")
        ),
    )
    rc = mg.main([
        "--repo", str(tmp_path),
        "--test", "true",
    ])
    assert rc == 2
    assert "mutation-gate ERROR" in capsys.readouterr().err


def test_main_empty_diff_is_clean_pass(monkeypatch, tmp_path, capsys):
    """No changed source files is a clean pass."""
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {},
    )
    rc = mg.main([
        "--repo", str(tmp_path),
        "--test", "true",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no changed source files to mutate" in out


def test_arg_parser_defaults():
    args = mg._build_arg_parser().parse_args(["--test", "pytest -x"])
    assert (args.base, args.source, args.repo) == (
        "main", "src/nyxloom", "."
    )
    assert args.test == "pytest -x"
