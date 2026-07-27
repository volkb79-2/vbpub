"""Tests for the D-CORRECT-4 mutation gate.

Every generated mutant is a deterministic observable — each test drives a real
code path and asserts the expected transformation.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

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


@pytest.mark.parametrize(
    ("source", "description", "expected"),
    [
        ("def f():\n    return None\n", "None->[]", "return []"),
        ("def f():\n    return []\n", "[]->None", "return None"),
        ("def f():\n    return 0\n", "0->None", "return None"),
        ("def f():\n    return \"\"\n", "''->None", "return None"),
    ],
)
def test_falsy_swap_changes_direct_return_values(source, description, expected):
    """Each required falsy return substitution changes the generated source."""
    mutants = mg.generate_mutants(source, {2})
    assert len(mutants) == 1
    mutant = mutants[0]
    assert mutant.operator == "falsy-swap"
    assert mutant.description == description
    assert expected in mutant.mutated_source


@pytest.mark.parametrize("source", [
    "def f():\n    return ()\n",
    "def f():\n    return {}\n",
])
def test_falsy_swap_covers_other_empty_literals(source):
    """Empty tuple and dict literals are also distinct falsy return values."""
    mutants = mg.generate_mutants(source, {2})
    assert len(mutants) == 1
    assert mutants[0].operator == "falsy-swap"
    assert mutants[0].description.endswith("->None")
    assert "return None" in mutants[0].mutated_source


def test_falsy_swap_is_restricted_to_return_statements():
    """Falsy values in assignments do not create falsy-swap mutants."""
    source = "value = None\nitems = []\ncount = 0\ntext = \"\"\n"
    mutants = mg.generate_mutants(source, {1, 2, 3, 4})
    assert [m for m in mutants if m.operator == "falsy-swap"] == []


def test_falsy_swap_respects_target_lines():
    """A direct falsy return outside target_lines remains untouched."""
    source = "def f():\n    return None\n"
    assert mg.generate_mutants(source, {1}) == []


def test_falsy_swap_ignores_other_return_values():
    """A truthy constant in a return is not an eligible falsy swap."""
    source = "def f():\n    return 1\n"
    assert mg.generate_mutants(source, {2}) == []


@pytest.mark.parametrize("value", ["True", "False"])
def test_boolean_returns_only_use_bool_const_flip(value):
    """Boolean returns are not duplicated by falsy-swap."""
    mutants = mg.generate_mutants(f"def f():\n    return {value}\n", {2})
    assert len(mutants) == 1
    assert mutants[0].operator == "bool-const-flip"
    assert "falsy-swap" not in [mutant.operator for mutant in mutants]


def test_falsy_swap_disambiguates_same_line_returns():
    """Multiple direct falsy returns on one line receive distinct mutations."""
    source = "def f(): return None; return []\n"
    mutants = mg.generate_mutants(source, {1})
    assert [mutant.description for mutant in mutants] == ["None->[]", "[]->None"]
    assert mutants[0].mutated_source != mutants[1].mutated_source


def test_falsy_swap_is_deterministic():
    """Repeated generation preserves both mutant contents and ordering."""
    source = (
        "def f(flag):\n"
        "    if flag:\n"
        "        return []\n"
        "    return None\n"
    )
    first = mg.generate_mutants(source, {3, 4})
    second = mg.generate_mutants(source, {3, 4})
    assert first == second


def test_falsy_swap_motivating_survivor(tmp_path):
    """A normal-path-only test cannot distinguish an error [] from [] normally."""
    source = (
        "def load(should_fail):\n"
        "    if should_fail:\n"
        "        return []\n"
        "    return []\n"
    )
    source_file = tmp_path / "loader.py"
    source_file.write_text(source)
    (tmp_path / "test_loader.py").write_text(
        "from loader import load\n"
        "\n"
        "def test_normal_empty_result():\n"
        "    assert load(False) == []\n"
    )

    mutants = mg.generate_mutants(source, {3})
    assert len(mutants) == 1
    mutant = mutants[0]
    assert "return None" in mutant.mutated_source
    assert mg._run_is_killed(
        str(tmp_path),
        "loader.py",
        mutant,
        [sys.executable, "-m", "pytest", "-q", "test_loader.py"],
    ) is False


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
# evaluate — EQUIVALENCE ORACLE: parallel fan-out must match the old serial
# algorithm byte-for-byte (D-mutation-fanout, load-bearing per the handoff)
# --------------------------------------------------------------------------- #

def _serial_reference_evaluate(targets, run_is_killed):
    """The PRE-parallelization `evaluate` algorithm, verbatim (a strictly
    serial for-loop over the same real generated mutants + the same injected
    runner). Used only as a comparison oracle -- not exported by the module
    under test."""
    total = 0
    killed = 0
    survivors: list[tuple[str, int, str]] = []
    for path, (source, changed_lines) in sorted(targets.items()):
        mutants = mg.generate_mutants(source, changed_lines)
        for mutant in mutants:
            total += 1
            if run_is_killed(path, mutant):
                killed += 1
            else:
                survivors.append((path, mutant.lineno, mutant.description))
    survivors.sort(key=lambda x: (x[0], x[1], x[2]))
    return mg.MutationResult(total=total, killed=killed, survivors=survivors)


def test_evaluate_parallel_matches_serial_reference():
    """The parallelized (ThreadPoolExecutor-backed) `evaluate` yields an
    IDENTICAL MutationResult (same total/killed, same sorted survivors) to a
    hand-rolled SERIAL reference implementation of the pre-parallelization
    algorithm -- same real generated mutants, same injected deterministic
    runner. Proves fan-out changed no verdict."""
    targets = {
        "a.py": ("if x < 5 and y > 3:\n    z = True\n", {1, 2}),
        "b.py": ("def f(n):\n    return n == 0 or n != 1\n", {2}),
        "c.py": ("if p is None and q is not None:\n    pass\n", {1}),
    }

    def stub(path, mutant):
        # Deterministic function of (path, lineno, description) ONLY -- the
        # same inputs always produce the same verdict, so execution order
        # (serial vs. concurrent) cannot change the outcome.
        #
        # crc32, NOT the builtin hash(). hash() of a str is salted per
        # interpreter (PYTHONHASHSEED), so it is stable only WITHIN one
        # process. That is enough for the parallel-vs-serial comparison
        # below -- both halves run here -- but NOT for the sanity assertions
        # at the end, which require the split to actually contain a mix. With
        # a per-process salt the whole split is redrawn on every run, and
        # ~1.4% of seeds put all 10 mutants on one side, failing
        # `0 < len(survivors) < total`. Measured: PYTHONHASHSEED=25 and =83
        # both give 0 survivors and fail deterministically; 7 of 501 seeds
        # do. Under xdist each worker is a fresh process with its own salt,
        # so the suite drew that seed regularly. crc32 is a fixed function of
        # the bytes with no salt, so the split is identical in every process
        # forever (currently 2 survivors of 10).
        key = f"{path}:{mutant.lineno}:{mutant.description}"
        return (zlib.crc32(key.encode()) % 3) != 0  # arbitrary but FIXED split

    parallel_result = mg.evaluate(targets, stub)
    serial_result = _serial_reference_evaluate(targets, stub)

    assert parallel_result.total == serial_result.total
    assert parallel_result.killed == serial_result.killed
    assert parallel_result.survivors == serial_result.survivors
    # Sanity: the fixture actually exercises multiple mutants and a mix of
    # killed/survived outcomes -- otherwise this oracle would be hollow.
    assert parallel_result.total > 3
    assert 0 < len(parallel_result.survivors) < parallel_result.total


def test_evaluate_shuffled_delayed_completion_order_is_still_deterministic():
    """A stub runner whose mutants complete in a SHUFFLED/DELAYED order
    (later-submitted jobs finish first) must still yield the same, correctly
    sorted MutationResult -- aggregation in `evaluate` is keyed by job
    position, not by arrival/completion order."""
    source = "if a < 1 and b < 2 and c < 3:\n    pass\n"
    targets = {"f.py": (source, {1})}
    mutants = mg.generate_mutants(source, {1})
    n = len(mutants)
    assert n >= 3  # 3 compare-swaps + 1 boolop-swap, sanity

    def stub(path, mutant):
        # Sleep INVERSELY to submission order (mutants are generated/queued
        # in a fixed deterministic order) so later jobs tend to finish
        # before earlier ones -- deliberately out-of-order completion.
        idx = mutants.index(mutant)
        time.sleep((n - idx) * 0.02)
        # Deterministic verdict: kill every compare-swap, let the boolop
        # survive -- independent of timing.
        return mutant.operator == "compare-swap"

    result = mg.evaluate(targets, stub)
    expected_survivors = sorted(
        (path, m.lineno, m.description)
        for path, (src, lines) in targets.items()
        for m in mg.generate_mutants(src, lines)
        if m.operator != "compare-swap"
    )
    assert result.survivors == expected_survivors
    assert result.total == n
    assert result.killed == n - len(expected_survivors)


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

    # A non-existent command makes the subprocess fail to start. The exact
    # OSError subclass is environment-dependent (FileNotFoundError in some
    # environments, PermissionError in the tester-unified gate), so assert the
    # common parent OSError to keep the test environment-stable.
    with pytest.raises(OSError):
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


def _make_nested_repo(tmp_path):
    """Like `_make_small_repo`, but the project root is a SUBDIRECTORY of the
    git top-level -- mirrors nyxloom self-hosting inside the vbpub monorepo
    (see `gate_runner._repo_root`'s docstring), exercising
    `_run_is_killed_isolated`'s subdir-offset resolution."""
    top = tmp_path / "monorepo"
    top.mkdir()
    project = top / "proj"
    src_dir = project / "src" / "nyxloom"
    src_dir.mkdir(parents=True)
    (src_dir / "hello.py").write_text(
        "def greet(name):\n    return 'Hello, ' + name + '!'\n"
    )
    test_dir = project / "tests"
    test_dir.mkdir()
    (test_dir / "test_hello.py").write_text(
        "from src.nyxloom.hello import greet\n"
        "def test_greet():\n"
        "    assert greet('World') == 'Hello, World!'\n"
    )
    subprocess.run(["git", "init"], cwd=str(top), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(top), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(top), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(top), capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(top), capture_output=True)
    return project  # the "repo" arg mutation_gate is invoked with


def _worktree_listing(repo_root: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_root, "worktree", "list"],
        capture_output=True, text=True,
    ).stdout


def _repo_top_level(repo: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    ).stdout.strip()


# --------------------------------------------------------------------------- #
# _fanout_safe — clean/dirty/non-git detection
# --------------------------------------------------------------------------- #

def test_fanout_safe_false_for_non_git_dir(tmp_path):
    """A bare directory with no git history is never fan-out-safe."""
    assert mg._fanout_safe(str(tmp_path)) is False


def test_fanout_safe_true_for_clean_repo(tmp_path):
    """A freshly-committed repo with no pending edits is fan-out-safe."""
    repo = _make_small_repo(tmp_path)
    assert mg._fanout_safe(str(repo)) is True


def test_fanout_safe_false_for_dirty_repo(tmp_path):
    """An uncommitted edit anywhere in the tree makes it NOT fan-out-safe --
    isolating at HEAD would silently drop that edit."""
    repo = _make_small_repo(tmp_path)
    (repo / "untracked.txt").write_text("dirty\n")
    assert mg._fanout_safe(str(repo)) is False


# --------------------------------------------------------------------------- #
# _run_is_killed — ISOLATION oracle: real git worktree, clean tree
# --------------------------------------------------------------------------- #

def test_run_is_killed_isolated_leaves_live_checkout_untouched(tmp_path):
    """On a clean git tree, the real `_run_is_killed` isolates the mutant
    into a scratch `git worktree`: (a) the LIVE checkout's target file is
    byte-unchanged, (b) the scratch worktree is removed afterward, and (c) a
    grep-style test command proves the mutant WAS written into the scratch
    and seen by the test run."""
    repo = _make_small_repo(tmp_path)
    live_file = repo / "src" / "nyxloom" / "hello.py"
    original_text = live_file.read_text()
    assert mg._fanout_safe(str(repo)) is True  # sanity: fixture is clean

    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="ISOLATION_MARKER = 1\n",
    )
    result = mg._run_is_killed(
        str(repo), "src/nyxloom/hello.py", mutant,
        ["grep", "ISOLATION_MARKER", "src/nyxloom/hello.py"],
    )
    # grep found the marker INSIDE THE SCRATCH worktree -> exit 0 -> survived
    assert result is False

    # (a) live checkout untouched
    assert live_file.read_text() == original_text

    # (b) no leftover scratch worktree
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))


def test_run_is_killed_isolated_kills_when_test_fails(tmp_path):
    """The isolated runner reports KILLED (True) when the test command exits
    non-zero, mirroring the in-place runner's contract."""
    repo = _make_small_repo(tmp_path)
    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="ISOLATION_MARKER = 1\n",
    )
    result = mg._run_is_killed(
        str(repo), "src/nyxloom/hello.py", mutant,
        ["grep", "NOT_PRESENT_ANYWHERE", "src/nyxloom/hello.py"],
    )
    # grep does NOT find the pattern -> exit 1 -> tests "failed" -> killed
    assert result is True
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))


def test_run_is_killed_isolated_resolves_subdir_offset(tmp_path):
    """When `repo` is a SUBDIRECTORY of the git top-level (the nyxloom
    self-hosting shape), the isolated runner still writes the mutant at, and
    runs tests from, the RIGHT path inside the scratch worktree."""
    project = _make_nested_repo(tmp_path)
    live_file = project / "src" / "nyxloom" / "hello.py"
    original_text = live_file.read_text()
    assert mg._fanout_safe(str(project)) is True

    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="ISOLATION_MARKER = 1\n",
    )
    result = mg._run_is_killed(
        str(project), "src/nyxloom/hello.py", mutant,
        ["grep", "ISOLATION_MARKER", "src/nyxloom/hello.py"],
    )
    assert result is False  # found in the scratch's subdir copy -> survived
    assert live_file.read_text() == original_text  # live subdir untouched

    # The scratch worktree is registered against the TOP-LEVEL repo, not the
    # subdir passed in as `repo`.
    top = str(project.parent)
    assert "mut-" not in _worktree_listing(top)


def test_run_is_killed_isolated_worktree_add_failure_falls_back_to_in_place(
        monkeypatch, tmp_path):
    """If `git worktree add` itself fails (e.g. a racing cleanup or disk
    issue) despite a clean tree, `_run_is_killed_isolated` falls back to the
    in-place path rather than silently skipping the mutant."""
    repo = _make_small_repo(tmp_path)
    live_file = repo / "src" / "nyxloom" / "hello.py"
    original_text = live_file.read_text()
    assert mg._fanout_safe(str(repo)) is True  # sanity: still clean

    real_run = subprocess.run

    def fake_run(argv, *a, **kw):
        if "worktree" in argv and "add" in argv:
            return subprocess.CompletedProcess(
                argv, returncode=1, stdout="", stderr="simulated failure",
            )
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(mg.subprocess, "run", fake_run)

    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="ISOLATION_MARKER = 1\n",
    )
    result = mg._run_is_killed(
        str(repo), "src/nyxloom/hello.py", mutant,
        ["grep", "ISOLATION_MARKER", "src/nyxloom/hello.py"],
    )
    # Fell back to in-place: grep found the marker written directly into the
    # LIVE file -> survived, and the live file is restored afterward.
    assert result is False
    assert live_file.read_text() == original_text


def test_run_is_killed_concurrent_calls_do_not_clobber(tmp_path):
    """Several concurrent `_run_is_killed` calls against the SAME clean repo
    each get their OWN scratch worktree: no cross-mutant clobbering, and
    every scratch worktree is cleaned up once all calls complete -- the core
    correctness property the mutation-fanout package exists to provide."""
    repo = _make_small_repo(tmp_path)
    live_file = repo / "src" / "nyxloom" / "hello.py"
    original_text = live_file.read_text()

    def worker(i):
        mutant = mg.Mutant(
            lineno=1, operator="marker", description=f"m{i}",
            mutated_source=f"ISOLATION_MARKER_{i} = 1\n",
        )
        return mg._run_is_killed(
            str(repo), "src/nyxloom/hello.py", mutant,
            ["grep", f"ISOLATION_MARKER_{i}", "src/nyxloom/hello.py"],
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, range(6)))

    # Every concurrent call saw exactly ITS OWN marker (grep exit 0) -> all
    # survived. If two calls had shared a path, at least one grep would have
    # seen the WRONG (or no) marker and returned killed (True) instead.
    assert results == [False] * 6
    assert live_file.read_text() == original_text
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))


# --------------------------------------------------------------------------- #
# _run_is_killed — DIRTY-tree fallback (preserves today's in-place behavior)
# --------------------------------------------------------------------------- #

def test_run_is_killed_dirty_tree_uses_in_place_fallback(tmp_path):
    """A dirty working tree (uncommitted edit elsewhere) must NOT isolate at
    HEAD -- that would silently drop the uncommitted edit. It falls back to
    the original in-place behavior: the LIVE file is mutated then restored."""
    repo = _make_small_repo(tmp_path)
    live_file = repo / "src" / "nyxloom" / "hello.py"
    original_text = live_file.read_text()
    (repo / "untracked.txt").write_text("dirty\n")
    assert mg._fanout_safe(str(repo)) is False  # sanity: tree is dirty

    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="ISOLATION_MARKER = 1\n",
    )
    result = mg._run_is_killed(
        str(repo), "src/nyxloom/hello.py", mutant,
        ["grep", "ISOLATION_MARKER", "src/nyxloom/hello.py"],
    )
    # grep found the marker written directly into the LIVE file -> survived
    assert result is False
    # ... and the live file is restored afterward, exactly like today.
    assert live_file.read_text() == original_text
    # No scratch worktree was ever created for the dirty-tree path.
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))


# --------------------------------------------------------------------------- #
# _run_is_killed_isolated — cleanup on subprocess failure
# --------------------------------------------------------------------------- #

def test_run_is_killed_isolated_cleans_up_scratch_on_subprocess_failure(tmp_path):
    """If the isolated runner's test subprocess fails to start (OSError),
    the scratch worktree it created is still removed via the finally block."""
    repo = _make_small_repo(tmp_path)
    assert mg._fanout_safe(str(repo)) is True

    mutant = mg.Mutant(
        lineno=1, operator="marker", description="m",
        mutated_source="x = 1\n",
    )
    with pytest.raises(OSError):
        mg._run_is_killed(
            str(repo), "src/nyxloom/hello.py", mutant,
            ["nonexistent_cmd_xyzzy"],
        )
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))


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


# --------------------------------------------------------------------------- #
# _derive_test_command — sibling test derivation (F017)
# --------------------------------------------------------------------------- #

def test_derive_test_command_derives_sibling():
    """src/nyxloom/foo.py -> tests/test_foo.py."""
    cmd = mg._derive_test_command("src/nyxloom/foo.py")
    assert cmd == ["python", "-m", "pytest", "-q", "tests/test_foo.py"]


def test_derive_test_command_deep_module():
    """A deeper path still takes the last component."""
    cmd = mg._derive_test_command("src/nyxloom/sub/mod.py")
    assert cmd == ["python", "-m", "pytest", "-q", "tests/test_mod.py"]


def test_derive_test_command_no_py_suffix():
    """A path without .py returns None."""
    assert mg._derive_test_command("src/nyxloom/data.json") is None


def test_derive_test_command_already_test():
    """A path starting with test_ returns None (test files have no test-of-test)."""
    assert mg._derive_test_command("tests/test_foo.py") is None


def test_derive_test_command_windows_path():
    """Windows backslash paths are normalized."""
    cmd = mg._derive_test_command("src\\nyxloom\\bar.py")
    assert cmd == ["python", "-m", "pytest", "-q", "tests/test_bar.py"]


# --------------------------------------------------------------------------- #
# main — derivation mode (no --test)
# --------------------------------------------------------------------------- #

def test_main_derived_test_finds_sibling(monkeypatch, tmp_path, capsys):
    """Omitting --test derives the test command from the changed source file;
    when the sibling test exists, mutation runs against it."""
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
        # no --test → derive
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK" in out


def test_main_derived_test_missing_sibling_fails(monkeypatch, tmp_path, capsys):
    """Omitting --test when a changed source file has NO sibling test file
    causes a nonzero exit with a message naming the file — never a silent
    whole-suite run."""
    repo = _make_small_repo(tmp_path)
    # Add a source file WITH NO sibling test
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "calc.py").write_text("def is_positive(x):\n    return x > 0\n")

    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/calc.py": {2},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
        # no --test → derive (no test/calc.py exists)
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "src/nyxloom/calc.py" in err
    assert "no sibling test file" in err


def test_main_explicit_test_overrides_derivation(monkeypatch, tmp_path, capsys):
    """When --test is given explicitly, derivation is NOT used — the explicit
    command runs as-is, even if the sibling test is missing."""
    repo = _make_small_repo(tmp_path)
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "calc.py").write_text("def is_positive(x):\n    return x > 0\n")

    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/calc.py": {2},
        },
    )
    # explicit --test "false" (exits 1 = mutant killed → pass)
    rc = mg.main([
        "--repo", str(repo),
        "--test", "false",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK" in out


def test_derive_test_command_bare_filename_no_slash():
    """A bare filename (no directory separator) hits the else branch
    of the '/' check."""
    cmd = mg._derive_test_command("foo.py")
    assert cmd == ["python", "-m", "pytest", "-q", "tests/test_foo.py"]


def test_main_derived_test_non_py_target_hits_missing_append(
        monkeypatch, tmp_path, capsys):
    """A changed file that is not a .py file causes _derive_test_command
    to return None, hitting the cmd is None -> missing_test.append branch."""
    repo = _make_small_repo(tmp_path)
    # Write data.json so it exists on disk and enters the target loop
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "data.json").write_text('{"key": "value"}\n')
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/hello.py": {1},
            "src/nyxloom/data.json": {1},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "src/nyxloom/data.json" in err
    assert "no sibling test file" in err


def test_main_derived_test_multiple_modules_concatenates(
        monkeypatch, tmp_path, capsys):
    """Two changed source modules, each with a sibling test file, produce
    a combined test command when the modules differ."""
    repo = _make_small_repo(tmp_path)
    # Add a second source module. Target line 1 has no mutable operators,
    # so no mutants are generated regardless of test outcome.
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "calc.py").write_text("def is_positive(x):\n    return x > 0\n")
    test_dir = repo / "tests"
    (test_dir / "test_calc.py").write_text(
        "from src.nyxloom.calc import is_positive\n"
        "def test_is_positive():\n"
        "    assert is_positive(1)\n"
    )
    monkeypatch.setattr(
        cg, "_resolve_base",
        lambda repo_arg, base: "HEAD",
    )
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {
            "src/nyxloom/hello.py": {1},
            # calc.py line 1 = "def is_positive(x):" has no mutable ops
            "src/nyxloom/calc.py": {1},
        },
    )
    rc = mg.main([
        "--repo", str(repo),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK" in out


# --------------------------------------------------------------------------- #
# main — end-to-end PARALLEL execution (real generate_mutants + real
# _run_is_killed_isolated + real ThreadPoolExecutor fan-out, no stubs)
# --------------------------------------------------------------------------- #

def test_main_parallel_fanout_kills_all_real_mutants(monkeypatch, tmp_path, capsys):
    """A changed line that generates SEVERAL real mutants (3 compare-swaps +
    1 boolop-swap) runs them all concurrently through the real isolated
    runner; a thorough sibling test kills every one of them. Exercises the
    full real pipeline end to end (no injected/stub runner) -- the isolation
    + parallel fan-out working together, not just each piece individually."""
    repo = _make_small_repo(tmp_path)
    src_dir = repo / "src" / "nyxloom"
    (src_dir / "multi.py").write_text(
        "def check(a, b, c):\n"
        "    return a < 1 and b > 2 and c == 3\n"
    )
    test_dir = repo / "tests"
    (test_dir / "test_multi.py").write_text(
        "from src.nyxloom.multi import check\n"
        "def test_check():\n"
        "    assert check(0, 3, 3) is True\n"
        "    assert check(1, 3, 3) is False\n"   # kills Lt->LtE (a<1)
        "    assert check(0, 2, 3) is False\n"   # kills Gt->GtE (b>2)
        "    assert check(0, 3, 4) is False\n"   # kills Eq->NotEq (c==3)
        "    assert check(0, 1, 5) is False\n"   # kills And->Or
    )
    # Commit the new files so the tree stays CLEAN -- this test exercises
    # the ISOLATED (worktree fan-out) path specifically, not the dirty
    # fallback (that path has its own dedicated tests above).
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add multi.py"], cwd=str(repo), capture_output=True)
    assert mg._fanout_safe(str(repo)) is True  # sanity: still clean

    monkeypatch.setattr(cg, "_resolve_base", lambda repo_arg, base: "HEAD")
    monkeypatch.setattr(
        cg, "_git_added_lines",
        lambda repo_arg, base, source: {"src/nyxloom/multi.py": {2}},
    )
    rc = mg.main(["--repo", str(repo)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "mutation OK: 4/4 mutants killed" in out
    # No leftover scratch worktrees after the whole run.
    assert "mut-" not in _worktree_listing(_repo_top_level(str(repo)))
    # The live checkout's source is untouched by the isolated fan-out.
    assert (src_dir / "multi.py").read_text() == (
        "def check(a, b, c):\n"
        "    return a < 1 and b > 2 and c == 3\n"
    )
