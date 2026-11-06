import json
import re
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests import _repo_root_scan as repo_root_scan
from tests._cx_paths import CX_LIBRARY

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coverage_canary  # noqa: E402
import mutation_campaign  # noqa: E402
import project_fixture  # noqa: E402
from project_fixture import copy_project_fixture  # noqa: E402

# A stand-in for the real coverage command: like pytest collecting the real
# suite, it fails with exit 2 when a repository-root file it reads is absent,
# and it refuses the canary's appended function as pytest-cov's fail-under does.
FAKE_COVERAGE_COMMAND = textwrap.dedent('''\
    import pathlib
    import sys

    project = pathlib.Path.cwd()
    for needed in sys.argv[2:]:
        if not (project.parent / needed).is_file():
            print(f"ERROR collecting: No such file or directory: {project.parent / needed}")
            sys.exit(2)
    if "_cmru_coverage_canary_unreached" in (project / "src/cmru/config.py").read_text():
        if sys.argv[1] == "coverage":
            print("FAIL Required test coverage of 100% not reached. Total coverage: 99.98%")
        else:
            print("1 failed")
        sys.exit(1)
    print("1 passed")
''')


#: Tracked files of each SUITE_COLLECTION_INPUTS pathspec in the estate below.
SUPPORT_FILES = (
    "tester-base/Dockerfile",
    "tester-unified/Dockerfile",
    "nyxloom/pyproject.toml",
    "nyxloom/src/nyxloom/__init__.py",
    "nyxloom/src/nyxloom/templates/handoff.md",
    ".gitignore",
    "run-gate-project/cmru.toml",
    "assay/src/assay/__init__.py",
    "assay/src/assay/git.py",
    "docs/BOOTSTRAP-FROM-ZERO.md",
    "wings-patchstack/stack.conf",
)


def _write(path: Path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _estate(repo_root: Path) -> Path:
    """A Git worktree shaped like vbpub around the CMRU project."""
    project_root = repo_root / "cmru"
    for filename in (
        *project_fixture.ROOT_CMRU_ARTIFACTS,
        "wheel-builder/Dockerfile",
        "docs/ciu-vs-cmru.md",
        "docs/RELEASE-TOOLING.md",
        "docs/plan-cmru-release-modes.md",
        "run-gate-project/CONSUMERS.md",
        "topos/cmru.toml",
        "nyxloom/cmru.toml",
        # Suite collection inputs, and their tracked siblings that must stay out:
        # gen-requirements.py and build-image.sh are the tester modules' skip keys.
        "tester-base/Dockerfile",
        "wings-patchstack/stack.conf",
        "tester-unified/Dockerfile",
        "tester-unified/gen-requirements.py",
        "tester-unified/build-image.sh",
        "nyxloom/pyproject.toml",
        "nyxloom/src/nyxloom/__init__.py",
        "nyxloom/src/nyxloom/templates/handoff.md",
        "nyxloom/tests/test_nyxloom.py",
        "run-gate-project/cmru.toml",
        "run-gate-project/tools/release_candidate.py",
        "assay/src/assay/__init__.py",
        "assay/src/assay/git.py",
        "assay/tests/test_assay.py",
        "docs/BOOTSTRAP-FROM-ZERO.md",
        # Registered estate configs beyond the declared two stay out: the full set
        # (incl. cli-extended's and tls-edge's) switches estate tests out of their skip.
        "libraries/cli-extended/cmru.toml",
        "tls-edge/cmru.toml",
        "ciu/cmru.toml",
    ):
        _write(repo_root / filename)
    _write(project_root / "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(project_root / "src/cmru/__init__.py", "")
    _write(project_root / "src/cmru/config.py", "VALUE = 1\n")
    _write(project_root / "check_coverage.py", FAKE_COVERAGE_COMMAND)
    (project_root / "config-link.py").symlink_to("src/cmru/config.py")
    for library, package in (
        ("cli-extended", "cli_extended"),  # present in the repo, must NOT be copied
        ("worktree", "worktree"),
    ):
        _write(repo_root / "libraries" / library / "src" / package / "__init__.py", "")
    _write(repo_root / ".gitignore", "*.egg-info/\n")
    # The fixture copies only TRACKED inputs, resolved through a real Git worktree.
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=True)
    _write(repo_root / "nyxloom/src/nyxloom/scratch.py", "UNTRACKED = True\n")
    _write(repo_root / "nyxloom/src/nyxloom.egg-info/PKG-INFO", "Name: nyxloom\n")
    _write(repo_root / "tester-unified/local-wheel/cli_extended-1-py3-none-any.whl", "wheel\n")
    return project_root


def test_disposable_project_fixture_copies_worktree_library_never_cli_extended_and_estate_configs(tmp_path):
    project_root = _estate(tmp_path / "repo")
    workspace = tmp_path / "scratch"
    workspace.mkdir()

    copied_project = copy_project_fixture(
        repo_root=tmp_path / "repo",
        project_root=project_root,
        workspace=workspace,
    )

    assert (copied_project / "src/cmru/__init__.py").is_file()
    # CX-D1: cli-extended is a wheel dependency of the gate environment; a fixture
    # (and so every mutated candidate) never carries a vendored copy of its source.
    assert not (workspace / CX_LIBRARY).exists()
    assert (workspace / "libraries/worktree/src/worktree/__init__.py").is_file()
    assert (workspace / "topos/cmru.toml").is_file()
    assert (workspace / "nyxloom/cmru.toml").is_file()


def test_fixture_carries_the_suite_collection_inputs_and_keeps_skip_keys_and_untracked_state_out(tmp_path):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    workspace = tmp_path / "scratch"

    copied_project = copy_project_fixture(
        repo_root=repo_root, project_root=project_root, workspace=workspace,
    )

    # Read by the full suite in the fixture (see SUITE_COLLECTION_INPUTS).
    for relative in SUPPORT_FILES:
        assert (workspace / relative).read_bytes() == (repo_root / relative).read_bytes(), relative
    # Their presence would switch suite modules out of their isolated-tree skip.
    assert not (workspace / "tester-unified/gen-requirements.py").exists()
    assert not (workspace / "tester-unified/build-image.sh").exists()
    assert not (workspace / "run-gate-project/tools").exists()
    assert not (workspace / "tls-edge").exists()
    assert not (workspace / "ciu").exists()
    # Nothing beyond the declared pathspecs, nothing untracked or ignored.
    assert not (workspace / "assay/tests").exists()
    assert not (workspace / "nyxloom/tests").exists()
    assert not (workspace / "nyxloom/src/nyxloom/scratch.py").exists()
    assert not (workspace / "nyxloom/src/nyxloom.egg-info").exists()
    assert not (workspace / "tester-unified/local-wheel").exists()
    assert not (workspace / CX_LIBRARY).exists()
    # A tracked symlink keeps its link text rather than becoming a copy.
    assert (copied_project / "config-link.py").is_symlink()
    assert (copied_project / "config-link.py").readlink() == Path("src/cmru/config.py")


@pytest.mark.parametrize("relative", [path.as_posix() for path in project_fixture.SUITE_COLLECTION_INPUTS])
def test_fixture_refuses_when_a_suite_collection_input_is_not_tracked(tmp_path, relative):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    subprocess.run(["git", "-C", str(repo_root), "rm", "-rq", "--cached", relative], check=True)

    with pytest.raises(ValueError, match=f"required tracked CMRU fixture inputs are missing: {relative}$"):
        copy_project_fixture(repo_root=repo_root, project_root=project_root, workspace=tmp_path / "scratch")


def test_mutation_manifest_pins_the_suite_collection_inputs_it_copies(tmp_path):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    workspace = tmp_path / "scratch"
    copy_project_fixture(repo_root=repo_root, project_root=project_root, workspace=workspace)

    manifest = mutation_campaign._test_manifest(repo_root, Path("cmru"))

    assert set(SUPPORT_FILES) <= set(manifest["fixture_files"])
    assert mutation_campaign._fixture_matches_manifest(
        template_root=workspace, project_prefix=Path("cmru"), manifest=manifest,
    )
    (workspace / "nyxloom/src/nyxloom/__init__.py").write_text("drift\n", encoding="utf-8")
    assert not mutation_campaign._fixture_matches_manifest(
        template_root=workspace, project_prefix=Path("cmru"), manifest=manifest,
    )


def _canary(project_root: Path, evidence: Path, mode: str, *needed: str) -> int:
    return coverage_canary.run([
        "--repo-root", str(project_root.parent),
        "--project-root", str(project_root),
        "--target", "src/cmru/config.py",
        "--evidence", str(evidence),
        "--", sys.executable, "check_coverage.py", mode, *needed,
    ])


def test_canary_control_passes_and_the_uncovered_candidate_fails_for_the_coverage_reason(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"
    needed = [
        "tester-unified/Dockerfile", "nyxloom/pyproject.toml", "nyxloom/src/nyxloom/__init__.py",
    ]

    assert _canary(project_root, evidence, "coverage", *needed) == 0

    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["control_exit_code"] == 0
    assert document["transformed_exit_code"] == 1
    assert document["expected_reason"] == "Required test coverage of 100% not reached"
    assert document["observed_expected_reason"] is True
    assert "_cmru_coverage_canary_unreached" not in (project_root / "src/cmru/config.py").read_text()


def test_canary_reports_a_missing_support_input_as_a_control_failure_never_a_coverage_refusal(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"

    # Tracked in the repository but not a fixture input: the suite cannot collect.
    with pytest.raises(RuntimeError, match="known-good canary control failed with exit 2") as raised:
        _canary(project_root, evidence, "coverage", "tester-unified/gen-requirements.py")

    assert "No such file or directory" in str(raised.value)
    assert not evidence.exists()


def test_canary_refuses_an_uncovered_candidate_that_fails_for_another_reason(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"

    with pytest.raises(RuntimeError, match="did not fail for the expected coverage reason"):
        _canary(project_root, evidence, "other")

    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["control_exit_code"] == 0
    assert document["transformed_exit_code"] == 1
    assert document["observed_expected_reason"] is False


# KI-88: the closure scan resolves every repository-root spelling with an AST walk
# (tests/_repo_root_scan.py) over each test module, per function, not per module.
# A read is exempt from "the fixture must carry it" in exactly three ways, all
# declared per MODULE below and all verified rather than trusted:
#
# 1. MODULE_SKIP_MARKERS: the module assigns a skipif to ``pytestmark``, so its
#    function bodies skip in an isolated tree. Import-time reads still run before
#    the marker applies and stay in scope. The marker's presence is asserted.
# 2. A function carrying its own skipif decorator (or a module-level skipif marker
#    name) is exempt for its body; detected from the AST, so a new unguarded test in
#    the same module is not.
# 3. RUNTIME_GUARDED: a named path a test reaches only behind a runtime check
#    (``pytest.skip`` or an ``is_file()`` branch). The guard text must still occur
#    in the module, and an entry that no longer matches any read fails as stale.
MODULE_SKIP_MARKERS = {
    "test_tester_image_cli_extended_identity.py": "tester-unified absent (isolated canary tree)",
    "test_tester_unified_image.py": "tester-unified generator absent (isolated canary tree)",
}
# (module, path prefix) -> (guard that must still be in the module source, why).
RUNTIME_GUARDED = {
    ("test_cli_build_output_semantics.py", "tls-edge"): (
        r'pytest\.skip\("tls-edge tree is not part of the disposable gate fixture"\)',
        "tls-edge is outside the disposable fixture; the test skips itself",
    ),
    ("test_installer_extensions.py", "tls-edge"): (
        r'pytest\.skip\("tls-edge not present"\)', "tls-edge is outside the fixture; skips",
    ),
    ("test_installer_extensions.py", "ciu"): (
        r'pytest\.skip\("ciu enroll fragment not present"\)', "ciu is outside the fixture; skips",
    ),
    ("test_installer_w1_round1.py", "tls-edge"): (
        r'pytest\.skip\("tls-edge not present"\)', "tls-edge is outside the fixture; skips",
    ),
    ("test_installed_wheel_subprocess.py", "libraries/cli-extended"): (
        r"_needs_build_tools = pytest\.mark\.skipif",
        "never copied (CX-D1); build tests skip, the rest assert it is absent",
    ),
    ("test_w2_pkg2_delegates.py", "libraries/cli-extended"): (
        r'root / "libraries" / "cli-extended" / "src"',
        "a sys.path string for a subprocess; nothing is read from it",
    ),
    ("test_w2_integ.py", "mdt-cockpit"): (
        r"if mdt\.is_file\(\):", "the sparse fixture lacks it; the read is branch-guarded",
    ),
}
# (module, scanned path) -> (concrete paths it expands to, why). A read whose tail is a
# variable cannot be resolved by the scan, so each is named with what it can reach.
NONLITERAL_READS = {
    ("test_docs_config_examples.py", "<non-literal>/cmru.toml"): (
        ("topos/cmru.toml", "nyxloom/cmru.toml"),
        "loops over exactly topos and nyxloom, both declared ESTATE_CONFIG_FIXTURES",
    ),
    ("test_w2_integ.py", "<non-literal>"): (
        (), "REPO_ROOT / caller, behind `if (REPO_ROOT / caller).is_file()`",
    ),
}


def _fixture_pathspecs() -> list[str]:
    return project_fixture.fixture_pathspecs("cmru")


def _scanned_modules() -> dict[str, tuple[str, str]]:
    """Every test module of the suite: name -> (source, path below the repository root)."""
    modules: dict[str, tuple[str, str]] = {}
    for folder in ("tests", "tools"):
        for module in sorted((Path(__file__).resolve().parents[1] / folder).glob("test_*.py")):
            modules[module.name] = (module.read_text(encoding="utf-8"), f"cmru/{folder}/{module.name}")
    return modules


def _in_scope_reads(modules):
    """Yield (module, read) for every read the fixture-closure rule applies to."""
    for name, (source, file_below_root) in modules.items():
        has_marker = name in MODULE_SKIP_MARKERS
        for read in repo_root_scan.scan_module(source, file_below_root):
            if read.predicate or not read.parts or read.parts[0] == "cmru":
                continue  # an existence check reads nothing; cmru/ is the carried project
            if read.function is not None and (read.guarded or has_marker):
                continue
            yield name, read


def _carried(path: str, pathspecs, tracked) -> bool:
    if not any(path == spec or path.startswith(spec + "/") for spec in pathspecs):
        return False
    if tracked is None:
        return True
    return any(item == path or item.startswith(path + "/") for item in tracked)


def test_fixture_carries_every_repo_root_file_the_suite_reads():
    repo_root = Path(__file__).resolve().parents[2]
    modules = _scanned_modules()
    # The scan must see reads, or this test would pass vacuously.
    seen = list(_in_scope_reads(modules))
    assert len(seen) >= 20, seen
    pathspecs = _fixture_pathspecs()
    # In a Git checkout the fixture is built from tracked files, so a carried path must
    # be tracked too; the fixture copy itself has no .git and checks existence only.
    tracked = (
        project_fixture.tracked_paths(repo_root, pathspecs) if (repo_root / ".git").exists() else None
    )

    for name, reason in MODULE_SKIP_MARKERS.items():
        assert reason and repo_root_scan.module_has_skip_marker(modules[name][0]), name
    for (name, _prefix), (guard, reason) in RUNTIME_GUARDED.items():
        assert reason and re.search(guard, modules[name][0]), f"{name}: guard text is gone: {guard}"

    used_guards: set[tuple[str, str]] = set()
    used_nonliteral: set[tuple[str, str]] = set()
    missing: list[str] = []
    for name, read in seen:
        if not read.literal:
            key = (name, read.path)
            if key not in NONLITERAL_READS:
                missing.append(f"{name}:{read.line}: non-literal read {read.path!r} is not declared")
                continue
            used_nonliteral.add(key)
            missing.extend(
                f"{name}: {path} (declared expansion of {read.path!r})"
                for path in NONLITERAL_READS[key][0]
                if not _carried(path, pathspecs, tracked) or not (repo_root / path).exists()
            )
            continue
        guarded = [
            key for key in RUNTIME_GUARDED
            if key[0] == name and (read.path == key[1] or read.path.startswith(key[1] + "/"))
        ]
        if guarded:
            used_guards.update(guarded)
            continue
        if not _carried(read.path, pathspecs, tracked) or not (repo_root / read.path).exists():
            missing.append(f"{name}:{read.line}: {read.path}")
    assert not missing, (
        "the canary/mutation fixture does not carry these repository-root files (or a "
        f"non-literal read is undeclared), so the known-good control fails: {missing}"
    )
    assert used_guards == set(RUNTIME_GUARDED), set(RUNTIME_GUARDED) - used_guards
    assert used_nonliteral == set(NONLITERAL_READS), set(NONLITERAL_READS) - used_nonliteral


SCAN_PRELUDE = "from pathlib import Path\nREPO_ROOT = Path(__file__).resolve().parents[2]\n"


def _scan(body: str, file_below_root: str = "cmru/tests/test_x.py"):
    return repo_root_scan.scan_module(SCAN_PRELUDE + body, file_below_root)


@pytest.mark.parametrize("spelling", [
    'REPO_ROOT / "a" / "b.txt"',
    'REPO_ROOT.joinpath("a", "b.txt")',
    'Path(__file__).resolve().parents[2] / "a" / "b.txt"',
    'Path(__file__).resolve().parent.parent.parent / "a" / "b.txt"',
    '(REPO_ROOT / "a").joinpath("b.txt")',
])
def test_scan_resolves_every_repo_root_spelling_inside_functions(spelling):
    reads = _scan(f"def test_it():\n    return ({spelling}).read_text()\n")
    assert [read.path for read in reads] == ["a/b.txt"], reads
    assert reads[0].function == "test_it" and not reads[0].guarded


def test_scan_follows_aliases_and_reports_reads_where_the_name_is_used():
    reads = _scan(
        'REPO = REPO_ROOT.parent.parent\nTARGET = REPO / "docs"\n'
        'def test_it(tmp_path):\n    root = REPO / "x"\n    return (root / "y").read_text()\n',
        "cmru/tests/test_x.py",
    )
    # REPO_ROOT is the repository root here, so .parent stays at the root (never above it).
    assert [read.path for read in reads] == ["x/y"]


def test_scan_flags_non_literal_tails_instead_of_ignoring_them():
    reads = _scan('def test_it(name):\n    return (REPO_ROOT / name / "cmru.toml").read_text()\n')
    assert [(read.path, read.literal) for read in reads] == [("<non-literal>/cmru.toml", False)]


def test_scan_treats_existence_checks_as_predicates_not_reads():
    reads = _scan('def test_it():\n    return (REPO_ROOT / "a").is_file()\n')
    assert [(read.path, read.predicate) for read in reads] == [("a", True)]


def test_scan_exempts_per_function_skipif_but_not_the_next_function():
    reads = _scan(
        "import pytest\n"
        'GUARD = pytest.mark.skipif(False, reason="x")\n'
        '@GUARD\ndef test_a():\n    return (REPO_ROOT / "one").read_text()\n'
        '@pytest.mark.skipif(False, reason="x")\ndef test_b():\n    return (REPO_ROOT / "two").read_text()\n'
        'def test_c():\n    return (REPO_ROOT / "three").read_text()\n'
    )
    assert {read.path: read.guarded for read in reads} == {"one": True, "two": True, "three": False}


def test_scan_sees_import_time_reads_and_a_module_skip_marker():
    source = (
        SCAN_PRELUDE + "import pytest\n"
        'pytestmark = pytest.mark.skipif(False, reason="x")\n'
        'STACK = (REPO_ROOT / "wings" / "stack.conf").read_text()\n'
    )
    assert repo_root_scan.module_has_skip_marker(source)
    (read,) = repo_root_scan.scan_module(source, "cmru/tests/test_x.py")
    assert (read.path, read.function) == ("wings/stack.conf", None)
    assert not repo_root_scan.module_has_skip_marker(SCAN_PRELUDE)


def test_scan_places_a_nested_tools_module_below_the_project():
    reads = _scan(
        'def test_it():\n    return (Path(__file__).resolve().parents[2] / "assay" / "src").exists()\n',
        "cmru/tools/test_y.py",
    )
    assert [read.path for read in reads] == ["assay/src"]


def test_canary_control_failure_streams_full_output_without_a_temporary_log(
    tmp_path, monkeypatch, capsys
):
    project_root = _estate(tmp_path / "repo")
    summary = "".join(f"FAILED tests/test_x.py::test_case_number_{n:03d}\n" for n in range(80))
    assert len(summary) > 2000  # the old stdout[-2000:] kept only this
    full_output = (
        "....F.....\n"
        "=================================== FAILURES ===================================\n"
        "____ test_real_cause ____\n"
        "E   FileNotFoundError: /scratch/tester-base/Dockerfile\n"
        "=========================== short test summary info ============================\n"
        + summary
        + "== 80 failed in 1.00s ==\n"
    )
    (project_root / "noisy_control.py").write_text(textwrap.dedent(f'''\
        import sys
        print("....F.....")
        print("=================================== FAILURES ===================================")
        print("____ test_real_cause ____")
        print("E   FileNotFoundError: /scratch/tester-base/Dockerfile")
        print("=========================== short test summary info ============================")
        sys.stdout.write({summary!r})
        print("== 80 failed in 1.00s ==")
        sys.exit(1)
    '''), encoding="utf-8")
    subprocess.run(["git", "-C", str(project_root.parent), "add", "-A"], check=True)  # the fixture copies tracked files
    evidence = tmp_path / "canary.json"
    monkeypatch.setattr(coverage_canary.tempfile, "tempdir", str(tmp_path))

    with pytest.raises(RuntimeError, match="known-good canary control failed with exit 1") as raised:
        coverage_canary.run([
            "--repo-root", str(project_root.parent), "--project-root", str(project_root),
            "--target", "src/cmru/config.py", "--evidence", str(evidence),
            "--", sys.executable, "noisy_control.py",
        ])

    error = str(raised.value)
    assert "FileNotFoundError: /scratch/tester-base/Dockerfile" in error
    assert "full output is above in the lane log" in error
    assert "full output:" not in error
    assert capsys.readouterr().err == "known-good canary control output follows:\n" + full_output + "\n"
    assert not list(tmp_path.glob("cmru-coverage-canary-*.control.log"))
    assert not evidence.exists()
    assert not evidence.with_suffix(".control.log").exists()


def _real_pytest_output(tmp_path: Path, source: str) -> str:
    """The output of a real ``pytest -q`` run over ``source`` (never a hand-made imitation)."""
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path),
         "-c", "/dev/null", "-o", "addopts=", str(tmp_path / "test_sample.py")],
        cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    assert result.returncode != 0, result.stdout
    return result.stdout


_LONG_FAILING_TEST = "def test_long_block():\n" + "".join(
    f"    value_{n} = {n}  # padding so pytest prints a long test source\n" for n in range(60)
) + (
    "    assert open('/nonexistent/tester-base/Dockerfile').read()\n"
    "\n\ndef test_second():\n    assert False, 'SECOND-FAILURE'\n"
)


@pytest.mark.parametrize("limit", [2000, 1000])
def test_failure_excerpt_leads_with_the_cause_of_the_first_failure_even_after_a_long_block(tmp_path, limit):
    output = _real_pytest_output(tmp_path, _LONG_FAILING_TEST)
    block = output.split("test_long_block ___", 1)[1].split("test_second ___", 1)[0]
    assert block.index("E   ") > 3000, block.index("E   ")  # the cause really is deep in the block

    excerpt = project_fixture.failure_excerpt(output, limit)

    assert len(excerpt) <= limit
    assert excerpt.startswith("_") and "test_long_block" in excerpt.splitlines()[0]
    assert "FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent/tester-base/Dockerfile'" in excerpt
    assert "test_sample.py:" in excerpt  # the file:line location of the failing frame
    assert "SECOND-FAILURE" not in excerpt  # only the first failure


_LONG_NAME_A = "test_" + "a_very_long_test_name_that_makes_pytest_pad_its_header_down_to_single_underscores_first"
_LONG_NAME_B = "test_" + "a_very_long_test_name_that_makes_pytest_pad_its_header_down_to_single_underscores_second"
_LONG_NAME_SOURCE = (
    "def helper():\n    raise KeyError('FIRST-CAUSE')\n\n\n"
    f"def {_LONG_NAME_A}():\n    helper()\n\n\n"
    f"def {_LONG_NAME_B}():\n    raise ValueError('SECOND-CAUSE')\n"
)


def test_failure_excerpt_handles_single_underscore_block_headers_and_frame_separators(tmp_path):
    output = _real_pytest_output(tmp_path, _LONG_NAME_SOURCE)
    headers = [line for line in output.splitlines() if _LONG_NAME_A[:20] in line or _LONG_NAME_B[:20] in line]
    assert headers and headers[0].startswith("_ ") and headers[0].endswith(" _"), headers  # real padding
    assert any(line.startswith("_ _ _") for line in output.splitlines())  # a frame separator is present

    excerpt = project_fixture.failure_excerpt(output, 2000)

    assert _LONG_NAME_A in excerpt.splitlines()[0]
    assert "KeyError: 'FIRST-CAUSE'" in excerpt
    assert re.search(r"^test_sample\.py:\d+: KeyError", excerpt, re.MULTILINE), excerpt  # the first test's location
    assert "SECOND-CAUSE" not in excerpt and _LONG_NAME_B not in excerpt  # the second failure is another block


def test_failure_excerpt_keeps_the_real_collection_error_cause(tmp_path):
    output = _real_pytest_output(tmp_path, "import module_that_does_not_exist_anywhere\n")

    excerpt = project_fixture.failure_excerpt(output, 1000)

    assert "ERROR collecting" in excerpt.splitlines()[0]
    assert "ModuleNotFoundError: No module named 'module_that_does_not_exist_anywhere'" in excerpt


def test_failure_excerpt_labels_the_tail_fallback_and_empty_output():
    assert project_fixture.failure_excerpt("", 100) == "[no output]"
    labelled = project_fixture.failure_excerpt("0123456789", 4)
    assert labelled.startswith("[no FAILURES/ERRORS section; last 4 characters of output]")
    assert labelled.endswith("6789")


def _noisy_control(project_root: Path) -> None:
    (project_root / "noisy_control.py").write_text(
        "import sys\nprint('=' * 35 + ' FAILURES ' + '=' * 35)\nprint('____ test_cause ____')\n"
        "print('E   boom')\nsys.exit(1)\n", encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(project_root.parent), "add", "-A"], check=True)


def _run_noisy_canary(project_root: Path, evidence: Path) -> None:
    coverage_canary.run([
        "--repo-root", str(project_root.parent), "--project-root", str(project_root),
        "--target", "src/cmru/config.py", "--evidence", str(evidence),
        "--", sys.executable, "noisy_control.py",
    ])


def test_a_green_canary_control_removes_a_stale_control_log(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"
    stale = tmp_path / "canary.control.log"
    stale.write_text("an earlier red run\n", encoding="utf-8")

    assert _canary(project_root, evidence, "coverage", "tester-unified/Dockerfile") == 0

    assert not stale.exists()


def test_an_unremovable_stale_control_log_is_reported_not_ignored(tmp_path):
    project_root = _estate(tmp_path / "repo")
    stale = tmp_path / "canary.control.log"
    stale.mkdir()
    (stale / "occupied").write_text("x", encoding="utf-8")  # unlink() of a non-empty directory fails

    with pytest.raises(RuntimeError, match="could not remove the stale control log"):
        _canary(project_root, tmp_path / "canary.json", "coverage", "tester-unified/Dockerfile")
