"""W0-TESTER: tester-unified image inputs (KI-52(b), BG-05).

The image is not built here (no docker builds). What is testable offline is the
requirements generator, run against synthetic trees and against the real
estate trees, and the Dockerfile / .dockerignore text that carries the policy.
"""
from __future__ import annotations

import importlib.util
import re
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tester-unified" / "gen-requirements.py"
DOCKERFILE = REPO_ROOT / "tester-unified" / "Dockerfile"


def _load():
    spec = importlib.util.spec_from_file_location("gen_requirements", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The assay canary runs the suite from an isolated copy of ``cmru/`` alone, with
# no repository-root files; these tests judge those files, so they only run
# where the repository tree is present (the coverage lane and the cockpit).
pytestmark = pytest.mark.skipif(
    not GENERATOR.exists(), reason="repository-root tester-unified files absent (isolated canary tree)",
)
gen = _load() if GENERATOR.exists() else None


def _tree(root: Path, **projects: str) -> Path:
    """Write ``<root>/<relative>/pyproject.toml`` for every project."""
    for relative, body in projects.items():
        path = root / relative.replace("__", "/")
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def _all_projects(root: Path, **overrides: str) -> Path:
    base = {
        "ciu": '[project]\nname = "ciu"\ndependencies = ["Jinja2>=3"]\n'
               '[project.optional-dependencies]\nssh = ["paramiko>=5"]\ntest = ["pytest>=8"]\n',
        "cmru": '[project]\nname = "cmru"\ndependencies = []\n'
                '[project.optional-dependencies]\ntest = ["pytest-cov>=5"]\n'
                '[build-system]\nrequires = ["setuptools==1", "setuptools_scm==2"]\n',
        "assay": '[project]\nname = "assay"\ndependencies = []\n'
                 '[project.optional-dependencies]\ntest = ["hypothesis>=6"]\n'
                 '[build-system]\nrequires = ["setuptools==9"]\n',
        "topos": '[project]\nname = "topos"\ndependencies = ["textual>=8"]\n'
                 '[project.optional-dependencies]\nzstandard = ["zstandard>=0.22"]\n'
                 'dev = ["pytest>=8", "topos[zstandard]"]\n',
        "nyxloom": '[project]\nname = "nyxloom"\ndependencies = ["PyYAML>=6"]\n'
                   '[project.optional-dependencies]\ntest = ["coverage>=7"]\n',
        "cgroup-profiler": '[project]\nname = "cgroup-profiler"\ndependencies = []\n'
                           '[project.optional-dependencies]\ntest = ["numpy>=2"]\n',
        "libraries__cli-extended": '[project]\nname = "cli-extended"\ndependencies = []\n'
                                   '[build-system]\nrequires = ["setuptools==1", "wheel==3"]\n',
    }
    base.update({key.replace("/", "__"): value for key, value in overrides.items()})
    return _tree(root, **base)


def test_generator_emits_the_third_party_closure_and_expands_self_extras(tmp_path):
    _all_projects(tmp_path)
    produced = list(gen.requirements(tmp_path))
    assert "Jinja2>=3" in produced and "paramiko>=5" in produced
    assert "zstandard>=0.22" in produced  # topos[dev] -> topos[zstandard] expanded
    assert not any(item.startswith("topos[") for item in produced)  # never handed to pip
    assert "setuptools==9" in produced  # assay's build backend (no-build-isolation lanes)
    assert "setuptools==1" not in produced  # cmru's pins go to the throwaway build venv
    assert produced[-1] == "build"


@pytest.mark.parametrize(
    "bad",
    ["cli-extended>=0.2.0", "cli_extended", "Worktree", "cmru==1", "assay", "ciu>=1",
     "nyxloom", "topos[dev]>=1", "CGroup.Profiler", "run_gate"],
)
def test_generator_refuses_estate_internal_names_in_dependencies(tmp_path, bad):
    """BG-05: a PyPI-default pip must never be asked for an estate-internal
    name (unclaimed `cli-extended`, unrelated look-alike `worktree`)."""
    _all_projects(tmp_path, cmru=(
        '[project]\nname = "cmru"\n'
        f'dependencies = ["{bad}"]\n'
    ))
    with pytest.raises(SystemExit, match="estate-internal"):
        list(gen.requirements(tmp_path))


def test_generator_refuses_internal_names_in_extras_and_build_requires(tmp_path):
    _all_projects(tmp_path, ciu=(
        '[project]\nname = "ciu"\ndependencies = []\n'
        '[project.optional-dependencies]\nssh = []\ntest = ["worktree>=1"]\n'
    ))
    with pytest.raises(SystemExit, match="'worktree'"):
        list(gen.requirements(tmp_path))
    _all_projects(tmp_path, assay=(
        '[project]\nname = "assay"\ndependencies = []\n'
        '[project.optional-dependencies]\ntest = []\n'
        '[build-system]\nrequires = ["cli-extended"]\n'
    ))
    with pytest.raises(SystemExit, match="'cli-extended'"):
        list(gen.requirements(tmp_path))
    other = _tree(tmp_path / "x", cmru='[build-system]\nrequires = ["cmru"]\n')
    with pytest.raises(SystemExit, match="'cmru'"):
        list(gen.build_requirements(other, ["cmru/pyproject.toml"]))


def test_generator_refuses_direct_url_requirements(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\n'
        'dependencies = ["somepkg @ https://example.invalid/somepkg.whl"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    with pytest.raises(SystemExit, match="direct-URL"):
        list(gen.requirements(tmp_path))


def test_generator_build_requires_mode_prints_only_the_build_backends(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path), "--build-requires",
                     "cmru/pyproject.toml", "libraries/cli-extended/pyproject.toml"]) == 0
    assert capsys.readouterr().out.splitlines() == ["setuptools==1", "setuptools_scm==2", "wheel==3"]


def test_generator_deduplicates_and_prints_one_requirement_per_line(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(set(lines)) and lines[-1] == "build"


def test_real_estate_trees_have_no_internal_requirement_in_the_pypi_bound_closure(tmp_path):
    """The generator against the REAL pyprojects, laid out as the Dockerfile
    COPYs them: today's closure must be accepted, and must stay clean when
    cmru later declares cli-extended/worktree as dependencies (KI-51)."""
    for name in ("ciu", "cmru", "assay", "topos", "nyxloom"):
        (tmp_path / name).symlink_to(REPO_ROOT / name)
    (tmp_path / "cgroup-profiler").symlink_to(REPO_ROOT / "scripts" / "cgroup-profiler")
    (tmp_path / "libraries").mkdir()
    (tmp_path / "libraries" / "cli-extended").symlink_to(REPO_ROOT / "libraries" / "cli-extended")
    closure = list(gen.requirements(tmp_path))
    names = {gen.requirement_name(item) for item in closure}
    assert not names & gen.ESTATE_INTERNAL
    assert {"pytest", "jinja2", "textual"} <= names
    build = list(gen.build_requirements(
        tmp_path, ["cmru/pyproject.toml", "libraries/cli-extended/pyproject.toml"],
    ))
    assert any(item.startswith("setuptools") for item in build)


# --- Dockerfile / .dockerignore policy text (the image itself is not built here)

def _dockerfile_run_lines() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_dockerfile_disables_detached_git_maintenance_system_wide_and_asserts_it():
    code = _dockerfile_run_lines()
    assert "git config --system maintenance.autoDetach false" in code
    assert "git config --system gc.autoDetach false" in code
    assert 'git config --system --get maintenance.autoDetach)" = false' in code
    assert 'git config --system --get gc.autoDetach)" = false' in code
    # The setting must be in place (as root) before the image drops to uid 1003.
    assert code.index("gc.autoDetach false") < code.index("USER 1003")


def test_dockerfile_installs_estate_internal_packages_only_offline_from_copied_sources():
    code = _dockerfile_run_lines()
    # The old install of cmru from a PyPI-default pip is gone.
    assert "pip install --no-cache-dir --no-deps /src/cmru" not in code
    wheel = re.search(r"pip wheel (?P<flags>[^\n]*\\\n[^\n]*)", code)
    assert wheel is not None
    for flag in ("--no-index", "--no-deps", "--no-build-isolation"):
        assert flag in wheel["flags"], flag
    assert "/src/libraries/cli-extended" in wheel["flags"] and "/src/cmru" in wheel["flags"]
    assert "--no-index --no-deps /tmp/internal-wheels/*.whl" in code
    # The PyPI-bound requirements come from the refusing generator.
    assert "gen-requirements.py --root /src" in code
    assert "pip install --no-cache-dir -r /tmp/tester-requirements.txt" in code
    # Relative layout cmru's pyproject packages (`../libraries/...`).
    assert "COPY libraries/ /src/libraries/" in code


def test_dockerfile_asserts_where_cli_extended_imports_from():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "import cli_extended" in text and "import worktree" in text
    assert "\n    assert location.startswith(site)," in text and 'site = "/opt/tester-venv/"' in text
    assert '\n    assert version.endswith("+tester.unified"), (' in text


def _dockerignore_included(relative: str) -> bool:
    """Docker's .dockerignore semantics: the LAST matching pattern decides;
    ``!`` re-includes. Patterns here are plain paths/globs without ``**``."""
    included = True
    parts = relative.split("/")
    candidates = ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]
    for raw in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!")
        if pattern.startswith("**/"):
            continue  # global noise rules (.git, __pycache__ ...) are not relevant here
        # `*` never crosses a path separator; a pattern also applies to a path
        # whose parent directory it matches.
        regex = re.compile("".join("[^/]*" if char == "*" else re.escape(char) for char in pattern))
        if any(regex.fullmatch(candidate) for candidate in candidates):
            included = negate
    return included


@pytest.mark.parametrize("path", [
    "tester-unified/gen-requirements.py",
    "libraries/cli-extended/pyproject.toml",
    "libraries/cli-extended/README.md",
    "libraries/cli-extended/src/cli_extended/__init__.py",
    "libraries/worktree/src/worktree/__init__.py",
    "cmru/pyproject.toml",
])
def test_dockerignore_admits_every_file_the_offline_build_needs(path):
    assert _dockerignore_included(path), path


@pytest.mark.parametrize("path", [
    "tester-unified/run",
    "tester-unified/Dockerfile",
    "libraries/cli-extended/tests/test_x.py",
    "libraries/worktree/tests/test_core.py",
    ".git/config",
])
def test_dockerignore_still_excludes_everything_else(path):
    assert not _dockerignore_included(path), path


def test_refusal_set_includes_every_pyproject_name_under_the_root(tmp_path):
    _all_projects(tmp_path)
    assert "foo-lib" not in gen.derive_internal(tmp_path)
    _tree(tmp_path, libraries__foo='[project]\nname = "Foo_Lib"\n', brandnew='[project]\nname = "brand.new"\n')
    derived = gen.derive_internal(tmp_path)
    assert {"foo-lib", "brand-new"} <= derived
    assert gen.ESTATE_INTERNAL <= derived  # the static list stays as a floor


def test_new_library_name_is_refused_as_a_dependency(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\ndependencies = ["foo_lib>=1"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    assert "foo_lib>=1" in list(gen.requirements(tmp_path))  # unknown name: third-party
    _tree(tmp_path, libraries__foo='[project]\nname = "foo-lib"\n')
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.requirements(tmp_path))
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.build_requirements(
            _tree(tmp_path / "y", libraries__foo='[project]\nname = "foo-lib"\n',
                  cmru='[build-system]\nrequires = ["foo-lib"]\n'),
            ["cmru/pyproject.toml"],
        ))


def test_dockerfile_builds_both_internal_projects_from_the_build_requires_mode():
    """The throwaway build venv must get the pinned backends of BOTH internal
    projects it builds (a dropped one would only fail at image build time)."""
    code = _dockerfile_run_lines()
    match = re.search(r"--build-requires (?P<paths>[^\n\\]*)", code)
    assert match is not None
    assert match["paths"].split() == ["cmru/pyproject.toml", "libraries/cli-extended/pyproject.toml"]
    wheel = re.search(r"pip wheel (?P<flags>[^\n]*\\\n[^\n]*)", code)
    assert "/src/libraries/cli-extended" in wheel["flags"] and "/src/cmru" in wheel["flags"]
