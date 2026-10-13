from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import py_compile
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "assay" / "src"))
from assay import git as assay_git

sys.path.insert(0, str(Path(__file__).parent))
import mutation_campaign
import project_fixture


def test_disposable_fixture_copies_shared_libraries_and_estate_configs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    workspace = tmp_path / "fixture"
    workspace.mkdir()
    project.mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "tests" / "test_fixture.py").write_text(
        "def test_fixture(): assert 'original' == 'original'\n", encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n", encoding="utf-8",
    )
    (project / ".gitignore").write_text(
        ".run-gate/\n.assay/\ncoverage.json\njunit-coverage.xml\n", encoding="utf-8",
    )
    for relative in project_fixture.SHARED_LIBRARIES:
        package_name = relative.name.replace("-", "_")
        package = repo / relative / "src" / package_name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            f"name = {package_name!r}\n", encoding="utf-8"
        )
    for relative in (Path("topos/cmru.toml"), Path("nyxloom/cmru.toml")):
        source = repo / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"name = {relative.parts[0]!r}\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "libraries/worktree", "topos", "nyxloom"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
        check=True,
    )
    (project / ".run-gate" / "inflight").mkdir(parents=True)
    (project / ".run-gate" / "inflight" / "mutation.json").write_text("{}\n", encoding="utf-8")
    (project / ".assay").mkdir()
    (project / ".assay" / "progress.jsonl").write_text("{}\n", encoding="utf-8")
    (project / "coverage.json").write_text("{}\n", encoding="utf-8")
    (project / "junit-coverage.xml").write_text("<testsuite/>\n", encoding="utf-8")
    (project / "untracked.py").write_text("SHOULD_NOT_COPY = True\n", encoding="utf-8")

    monkeypatch.setattr(project_fixture, "ROOT_CMRU_ARTIFACTS", ())
    monkeypatch.setattr(project_fixture, "EXTERNAL_DOC_ARTIFACTS", ())
    monkeypatch.setattr(
        project_fixture,
        "ESTATE_CONFIG_FIXTURES",
        (Path("topos/cmru.toml"), Path("nyxloom/cmru.toml")),
    )

    copied = project_fixture.copy_project_fixture(
        repo_root=repo, project_root=project, workspace=workspace
    )

    assert copied == workspace / "cmru"
    for relative in project_fixture.SHARED_LIBRARIES:
        package_name = relative.name.replace("-", "_")
        copied_package = workspace / relative / "src" / package_name / "__init__.py"
        assert copied_package.read_text(encoding="utf-8") == (
            f"name = {package_name!r}\n"
        )
    for relative in project_fixture.ESTATE_CONFIG_FIXTURES:
        assert (workspace / relative).read_text(encoding="utf-8") == (
            f"name = {relative.parts[0]!r}\n"
        )
    assert not (copied / ".run-gate").exists()
    assert not (copied / ".assay").exists()
    assert not (copied / "coverage.json").exists()
    assert not (copied / "junit-coverage.xml").exists()
    assert not (copied / "untracked.py").exists()
    manifest = mutation_campaign._test_manifest(repo, Path("cmru"))
    assert mutation_campaign._fixture_matches_manifest(
        template_root=workspace,
        project_prefix=Path("cmru"),
        manifest=manifest,
    )

    (project / "tests" / "test_fixture.py").write_text(
        "def test_fixture(): assert 'changed' == 'changed'\n", encoding="utf-8",
    )
    candidate_workspace = tmp_path / "candidate"
    candidate_workspace.mkdir()
    frozen_candidate = mutation_campaign._copy_frozen_fixture(
        template_root=workspace,
        project_prefix=Path("cmru"),
        workspace=candidate_workspace,
    )
    assert (frozen_candidate / "tests" / "test_fixture.py").read_text(encoding="utf-8") == (
        "def test_fixture(): assert 'original' == 'original'\n"
    )
    assert mutation_campaign._fixture_matches_manifest(
        template_root=workspace,
        project_prefix=Path("cmru"),
        manifest=manifest,
    )
    (workspace / "cmru" / "tests" / "test_fixture.py").write_text(
        "def test_fixture(): assert False\n", encoding="utf-8",
    )
    assert not mutation_campaign._fixture_matches_manifest(
        template_root=workspace,
        project_prefix=Path("cmru"),
        manifest=manifest,
    )


def test_test_manifest_fingerprints_dangling_fixture_symlinks_without_following_them(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8",
    )
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    linked = repo / "libraries" / "worktree" / "run-gate.py"
    linked.parent.mkdir(parents=True)
    linked.symlink_to("missing-run-gate.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "libraries/worktree"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )

    manifest = mutation_campaign._test_manifest(repo, Path("cmru"))

    assert manifest["fixture_files"]["libraries/worktree/run-gate.py"] == hashlib.sha256(
        b"symlink\0missing-run-gate.py"
    ).hexdigest()


def _job(path: str = "cmru/src/cmru/thing.py", line: int = 7):
    return SimpleNamespace(
        path=path,
        site=SimpleNamespace(
            lineno=line,
            operator="python:compare-swap",
            description="Eq->NotEq",
        ),
    )


def _make_assay_source(repo: Path) -> Path:
    assay = repo / "assay"
    (assay / "src" / "assay").mkdir(parents=True)
    (assay / "src" / "assay" / "__init__.py").write_text(
        "__version__ = '1'\n", encoding="utf-8",
    )
    (assay / "analysis" / "src" / "assay_analysis").mkdir(parents=True)
    (assay / "analysis" / "src" / "assay_analysis" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8",
    )
    (assay / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n"
        "[tool.setuptools]\npackage-dir = {'' = 'src'}\n"
        "[tool.setuptools.packages.find]\nwhere = ['src', 'analysis/src']\n",
        encoding="utf-8",
    )
    return assay


def test_assay_install_status_refuses_even_ignored_build_hooks(tmp_path):
    repo = tmp_path / "repo"
    assay = _make_assay_source(repo)
    (repo / ".gitignore").write_text(
        "assay/setup.py\nassay/.assay/\nassay/.run-gate/\n"
        "assay/src/assay/__pycache__/\nassay/src/assay/*.pyc\n"
        "assay/.hypothesis/\nassay/coverage.json\nassay/junit-coverage.xml\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assay", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    (assay / "setup.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    (assay / ".assay").mkdir()
    (assay / ".assay" / "progress.jsonl").write_text("{}\n", encoding="utf-8")
    (assay / ".run-gate").mkdir()
    (assay / ".run-gate" / "history.json").write_text("{}\n", encoding="utf-8")
    cache = assay / "src" / "assay" / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"generated")
    hypothesis_constants = assay / ".hypothesis" / "constants"
    hypothesis_constants.mkdir(parents=True)
    (hypothesis_constants / "example-cache").write_bytes(b"generated")
    unicode_cache = assay / ".hypothesis" / "unicode_data" / "16.0.0"
    unicode_cache.mkdir(parents=True)
    (unicode_cache / "charmap.json.gz").write_bytes(b"generated")
    (assay / "coverage.json").write_text("{}\n", encoding="utf-8")
    (assay / "junit-coverage.xml").write_text("<testsuite/>\n", encoding="utf-8")

    status = mutation_campaign._assay_install_tree_status(assay)

    assert "assay/setup.py" in status
    for generated in (
        ".assay", ".run-gate", "__pycache__", ".hypothesis",
        "coverage.json", "junit-coverage.xml",
    ):
        assert generated not in status
    (assay / "setup.py").unlink()
    assert mutation_campaign._assay_install_tree_status(assay) == ""


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ("[tool.setuptools.packages.find\nwhere = ['src']\n", "could not derive"),
        ("[tool.setuptools.packages.find]\nwhere = ['../src']\n", "unsafe package source root"),
        ("[tool.setuptools]\n", "could not derive"),
        ("[tool.setuptools.packages.find]\nwhere = []\n", "non-empty"),
        ("[tool.setuptools.packages.find]\nwhere = '../src'\n", "non-empty"),
        ("[tool.setuptools.packages.find]\nwhere = ['/tmp/assay']\n", "unsafe package source root"),
        ("[tool.setuptools.packages.find]\nwhere = ['src', 'src']\n", "duplicate package source root"),
        ("[tool.setuptools.packages.find]\nwhere = ['missing']\n", "escapes or is missing"),
        ("[tool.setuptools]\ndata-files = { share = ['data.txt'] }\n"
         "[tool.setuptools.packages.find]\nwhere = ['src']\n", "unsupported setuptools settings"),
        ("[tool.setuptools]\next-modules = []\n"
         "[tool.setuptools.packages.find]\nwhere = ['src']\n", "unsupported setuptools settings"),
        ("[tool.setuptools]\nfuture-setting = true\n"
         "[tool.setuptools.packages.find]\nwhere = ['src']\n", "unsupported setuptools settings"),
        ("[tool.setuptools.packages.find]\nwhere = [1]\n", "invalid package source root"),
        ("[tool.setuptools.packages.find]\nwhere = ['']\n", "invalid package source root"),
        ("[tool.setuptools.packages.find]\nwhere = ['src/*']\n", "non-literal package source root"),
        ("[tool.setuptools]\npackage-dir = 'bad'\n"
         "[tool.setuptools.packages.find]\nwhere = ['src']\n", "package-dir.*mapping"),
        ("[tool.setuptools]\npackage-dir = { assay = 'src' }\n"
         "[tool.setuptools.packages.find]\nwhere = ['src']\n", "unsupported named package-dir mapping"),
    ],
)
def test_assay_package_roots_refuse_unreadable_or_unsupported_build_metadata(
    tmp_path, metadata, message,
):
    source = tmp_path / "assay"
    (source / "src").mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n" + metadata,
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=message):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_py_modules_until_import_binding_is_supported(tmp_path):
    source = tmp_path / "assay"
    (source / "src").mkdir(parents=True)
    (source / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n"
        "[tool.setuptools]\npy-modules = ['helper']\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="unsupported setuptools settings: py-modules"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_a_missing_pyproject(tmp_path):
    source = tmp_path / "assay"
    source.mkdir()

    with pytest.raises(RuntimeError, match="regular file"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_a_non_setuptools_build_backend(tmp_path):
    source = tmp_path / "assay"
    (source / "src").mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'hatchling.build'\n\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="setuptools.build_meta"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_a_symlinked_source_directory(tmp_path):
    source = tmp_path / "assay"
    external = tmp_path / "external"
    source.mkdir()
    external.mkdir()
    (source / "src").symlink_to(external, target_is_directory=True)
    (source / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="escapes or is missing|must not be a symlink"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_a_symlink_component_inside_a_declared_root(tmp_path):
    source = tmp_path / "assay"
    source.mkdir()
    (source / "real" / "src").mkdir(parents=True)
    (source / "link").symlink_to(source / "real", target_is_directory=True)
    (source / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n"
        "[tool.setuptools.packages.find]\nwhere = ['link/src']\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="symlinked path component"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_package_roots_refuse_symlinked_package_files(tmp_path):
    source = _make_assay_source(tmp_path / "repo")
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    (source / "src" / "assay" / "helper.py").symlink_to(outside)

    with pytest.raises(RuntimeError, match="contains a symlink entry"):
        mutation_campaign._assay_source_package_paths(source)


def test_assay_source_status_detects_ignored_core_package_files_even_if_assay_git_lies(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    assay = _make_assay_source(repo)
    (repo / ".gitignore").write_text("core\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assay", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )

    ignored_module = assay / "analysis" / "src" / "assay_analysis" / "core" / "hidden.py"
    ignored_module.parent.mkdir(parents=True)
    ignored_module.write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setattr(assay_git, "run", lambda *args, **kwargs: "")
    assert mutation_campaign._assay_source_tree_status(assay)


def test_assay_source_status_allows_only_generated_cache_and_egg_info(tmp_path):
    repo = tmp_path / "repo"
    assay = _make_assay_source(repo)
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n*.egg-info/\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assay", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    cache = assay / "src" / "assay" / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-313.pyc").write_bytes(b"cache")
    egg_info = assay / "analysis" / "src" / "assay_analysis.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text("generated\n", encoding="utf-8")

    assert mutation_campaign._assay_source_tree_status(assay) == ""


def test_mutation_campaign_uses_a_fresh_cache_before_importing_assay_source(
    tmp_path, monkeypatch,
):
    source_root = tmp_path / "source"
    package = source_root / "assay_cache_probe"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    source = package / "__init__.py"
    source.write_text("VALUE = 'unchecked cache'\n", encoding="utf-8")
    bytecode = cache / f"__init__.{sys.implementation.cache_tag}.pyc"
    py_compile.compile(
        str(source),
        cfile=str(bytecode),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )
    source.write_text("VALUE = 'committed source'\n", encoding="utf-8")
    stale = bytearray(bytecode.read_bytes())
    stale[4:8] = (1).to_bytes(4, "little")  # PEP 552 unchecked-hash cache.
    bytecode.write_bytes(stale)

    monkeypatch.delitem(sys.modules, "assay_cache_probe", raising=False)

    original_sys_path = list(sys.path)

    def import_from_assay_source(_argv, *, template_root):
        assert template_root.is_absolute()
        assert sys.pycache_prefix is not None
        assert "cmru-mutation-campaign-" in sys.pycache_prefix
        sys.path.insert(0, str(source_root))
        try:
            module = importlib.import_module("assay_cache_probe")
            return 0 if module.VALUE == "committed source" else 1
        finally:
            sys.path[:] = original_sys_path

    monkeypatch.setattr(mutation_campaign, "_run_campaign", import_from_assay_source)

    assert mutation_campaign.run([]) == 0
    sys.modules.pop("assay_cache_probe", None)


def test_assay_source_status_refuses_a_stale_pyc_outside_pycache(tmp_path):
    repo = tmp_path / "repo"
    assay = _make_assay_source(repo)
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assay", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    stale_bytecode = assay / "src" / "assay" / "removed_module.pyc"
    stale_bytecode.write_bytes(b"old importable bytecode")

    assert mutation_campaign._assay_source_tree_status(assay)


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_assay_source_status_refuses_hidden_index_entries(tmp_path, index_flag):
    repo = tmp_path / "repo"
    assay = _make_assay_source(repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    source_file = assay / "src" / "assay" / "__init__.py"
    subprocess.run(
        ["git", "-C", str(repo), "update-index", index_flag, "assay/src/assay/__init__.py"],
        check=True,
    )
    source_file.write_text("__version__ = 'changed'\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="assume-unchanged or skip-worktree"):
        mutation_campaign._assay_source_tree_status(assay)


def test_campaign_status_refuses_ignored_test_files_but_allows_pytest_cache(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_ok.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "cmru/tests/hidden_test.py\ncmru/.pytest_cache/\ncmru/.run-gate/\n"
        "cmru/.assay/\ncmru/coverage.json\ncmru/junit-coverage.xml\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    (project / "tests" / "hidden_test.py").write_text("def test_hidden(): assert False\n", encoding="utf-8")
    cache = project / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "nodeids").write_text("[]\n", encoding="utf-8")
    run_gate_state = project / ".run-gate" / "inflight"
    run_gate_state.mkdir(parents=True)
    (run_gate_state / "inflight.lock").write_text("lock\n", encoding="utf-8")
    (project / ".assay").mkdir()
    (project / ".assay" / "progress.jsonl").write_text("{}\n", encoding="utf-8")
    (project / "coverage.json").write_text("{}\n", encoding="utf-8")
    (project / "junit-coverage.xml").write_text("<testsuite/>\n", encoding="utf-8")

    status = mutation_campaign._campaign_tree_status(repo, Path("cmru"))
    assert "hidden_test.py" in status
    for generated in (".pytest_cache", ".run-gate", ".assay", "coverage.json", "junit-coverage.xml"):
        assert generated not in status


def test_assay_import_must_come_from_a_configured_source_root(tmp_path):
    source = _make_assay_source(tmp_path / "repo")

    assert mutation_campaign._assay_import_is_bound_to_source(
        source, str(source / "src" / "assay" / "__init__.py"),
    )
    assert not mutation_campaign._assay_import_is_bound_to_source(
        source, str(tmp_path / "installed" / "assay" / "__init__.py"),
    )
    assert not mutation_campaign._assay_import_is_bound_to_source(source, None)


def test_assay_import_binding_accepts_a_real_import_from_a_configured_root(tmp_path):
    import importlib.util

    source = _make_assay_source(tmp_path / "repo")
    package_file = source / "src" / "assay" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "assay", package_file, submodule_search_locations=[str(package_file.parent)],
    )
    assert spec is not None and spec.loader is not None
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)

    assert mutation_campaign._assay_import_is_bound_to_source(source, imported.__file__)


def test_mutation_campaign_refuses_dirty_analysis_source_before_baseline(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "src" / "cmru" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    dirty = assay / "analysis" / "src" / "assay_analysis" / "new_module.py"
    dirty.write_text("VALUE = 2\n", encoding="utf-8")
    import assay as assay_module

    monkeypatch.setattr(
        assay_module, "__file__", str(assay / "src" / "assay" / "__init__.py"),
    )
    monkeypatch.setattr(assay_git, "run", lambda *_args, **_kwargs: "")
    real_import = builtins.__import__

    def refuse_assay_import(name, *args, **kwargs):
        if name == "assay":
            pytest.fail("dirty Assay source was imported before the independent Git check")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_assay_import)
    original_sys_path = list(sys.path)

    def baseline_must_not_run(*_args, **_kwargs):
        pytest.fail("campaign baseline ran before the Assay source closure check")

    monkeypatch.setattr(mutation_campaign, "_read_resource_limit_counters", baseline_must_not_run)
    monkeypatch.setattr(mutation_campaign, "_run", baseline_must_not_run)
    try:
        with pytest.raises(ValueError, match="clean Assay source tree"):
            mutation_campaign.run([
                "--repo-root", str(repo),
                "--project-root", str(project),
                "--assay-source", str(assay),
                "--base", head,
                "--max-mutants", "1",
                "--evidence", str(project / ".assay" / "evidence.json"),
                "--progress", str(project / ".assay" / "progress.jsonl"),
                "--timeout-seconds", "1",
                "--", sys.executable, "-m", "pytest", "tests",
            ])
    finally:
        sys.path[:] = original_sys_path


def test_mutation_resume_refusal_happens_before_baseline(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "src" / "cmru" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    evidence = project / ".assay" / "evidence.json"
    evidence.parent.mkdir()
    evidence.write_text('{"schema_version": 4}', encoding="utf-8")
    import assay as assay_module

    monkeypatch.setattr(assay_module, "__file__", str(assay / "src" / "assay" / "__init__.py"))
    monkeypatch.setattr(
        mutation_campaign, "_run",
        lambda *_args, **_kwargs: pytest.fail("baseline ran before resume evidence was validated"),
    )
    original_sys_path = list(sys.path)
    try:
        with pytest.raises(ValueError, match="predates the current process-tree evidence schema"):
            mutation_campaign.run([
                "--repo-root", str(repo),
                "--project-root", str(project),
                "--assay-source", str(assay),
                "--base", head,
                "--max-mutants", "1",
                "--evidence", str(evidence),
                "--progress", str(project / ".assay" / "progress.jsonl"),
                "--timeout-seconds", "1",
                "--resume",
                "--", sys.executable, "-m", "pytest", "tests",
            ])
    finally:
        sys.path[:] = original_sys_path


def test_resume_requires_an_assay_source_root_argument(tmp_path):
    with pytest.raises(TypeError, match="assay_source_root"):
        mutation_campaign._resume_results(
            evidence_path=tmp_path / "missing.json",
            repo_root=tmp_path,
            resume=False,
            base="base",
            head="head",
            project_prefix=Path("cmru"),
            assay_source_commit="assay",
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resume_refuses_an_explicit_none_assay_source_root(tmp_path):
    with pytest.raises(ValueError, match="requires an Assay source root"):
        mutation_campaign._resume_results(
            evidence_path=tmp_path / "missing.json",
            repo_root=tmp_path,
            resume=False,
            base="base",
            head="head",
            project_prefix=Path("cmru"),
            assay_source_commit="assay",
            assay_source_root=None,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resume_compares_assay_source_from_its_linked_worktree(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    linked_root = repo / ".worktrees" / "assay-source"
    linked_root.parent.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(linked_root), base],
        check=True,
    )
    linked_assay = linked_root / "assay"
    changed_file = linked_assay / "analysis" / "src" / "assay_analysis" / "__init__.py"
    changed_file.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(linked_root), "add", "assay/analysis/src"], check=True)
    subprocess.run(
        ["git", "-C", str(linked_root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "change Assay source"],
        check=True,
    )
    changed_assay_commit = subprocess.run(
        ["git", "-C", str(linked_root), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    job = _job()
    previous = {
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": base,
        "head": base,
        "project_prefix": "cmru",
        "assay_source_commit": base,
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "timeout_seconds": 120,
        "candidate_count": 1,
        "test_manifest": mutation_campaign._test_manifest(repo, Path("cmru")),
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "exit_code": 1,
            "outcome": "killed",
            "resource_limit_evidence": _zero_resource_evidence(),
        }],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(_with_process_tree_contract(previous)), encoding="utf-8")

    with pytest.raises(ValueError, match="predates an Assay source change"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=base,
            head=base,
            project_prefix=Path("cmru"),
            assay_source_commit=changed_assay_commit,
            assay_source_root=linked_assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )


def _zero_resource_evidence():
    counter = {"before": 0, "after": 0, "delta": 0}
    return {
        "cgroup_version": 2,
        "pids_events": {"max": dict(counter)},
        "memory_events": {
            "max": dict(counter),
            "oom": dict(counter),
            "oom_kill": dict(counter),
            "oom_group_kill": dict(counter),
        },
    }


def _limited_resource_evidence():
    evidence = _zero_resource_evidence()
    evidence["memory_events"]["oom_kill"]["after"] = 1
    evidence["memory_events"]["oom_kill"]["delta"] = 1
    return evidence


def _process_tree_evidence(
    *, leader_status: str = "exited", outliving_descendants: int = 0,
    descendants_signalled: int = 0,
):
    return {
        "protocol": "assay-process-tree/1",
        "leader_status": leader_status,
        "group_signalled_before_reap": leader_status == "timeout-killed",
        "outliving_descendants": outliving_descendants,
        "descendants_signalled": descendants_signalled,
        "cleanup_seconds": 0.001,
        "clean": True,
    }


def _with_process_tree_contract(document):
    document["schema_version"] = 5
    document["candidate_isolation"] = "process-tree/1"
    document["baseline_process_tree_evidence"] = _process_tree_evidence()
    for result in document.get("results", []):
        if result is None:
            continue
        result.setdefault("resource_limit_evidence", _zero_resource_evidence())
        if result.get("outcome") == "killed":
            result.setdefault("termination", "exit")
        status = "timeout-killed" if result.get("termination") == "timeout" else "exited"
        result["process_tree_evidence"] = _process_tree_evidence(
            leader_status=status,
            outliving_descendants=result.get("descendants_killed", 0),
            descendants_signalled=result.get("descendants_killed", 0),
        )
    return document


def _run_in_subreaper_process(argv, *, cwd, timeout_seconds, output_dir):
    helper = '''
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import mutation_campaign
try:
    result = mutation_campaign._run(
        json.loads(sys.argv[3]), cwd=Path(sys.argv[4]),
        timeout_seconds=float(sys.argv[5]), output_dir=Path(sys.argv[6]),
    )
    payload = {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "descendants_killed": result.descendants_killed,
        "process_tree_evidence": result.process_tree_evidence,
    }
except subprocess.TimeoutExpired as exc:
    payload = {
        "timed_out": True,
        "stdout": exc.output,
        "descendants_killed": exc.descendants_killed,
        "process_tree_evidence": exc.process_tree_evidence,
    }
except mutation_campaign.CandidateIsolationError as exc:
    payload = {"isolation_error": str(exc)}
print(json.dumps(payload))
'''
    process = subprocess.run(
        [
            sys.executable, "-c", helper,
            str(Path(mutation_campaign.__file__).parent),
            str(REPO_ROOT / "assay" / "src"), json.dumps(list(argv)),
            str(cwd), str(timeout_seconds), str(output_dir),
        ],
        cwd=cwd, capture_output=True, text=True, timeout=20, check=False,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_mutation_subprocess_has_a_hard_timeout(tmp_path):
    result = _run_in_subreaper_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path, timeout_seconds=2.0, output_dir=tmp_path,
    )
    assert result["timed_out"] is True
    assert result["descendants_killed"] == 0


def test_mutation_timeout_reaps_a_setsid_grandchild_before_returning(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    child = (
        "import os,time; os.setsid(); "
        f"open({str(pid_file)!r},'w').write(str(os.getpid())); "
        "print('detached-output', flush=True); time.sleep(30)"
    )
    parent = (
        "import subprocess,sys,time; print('parent-output', flush=True); "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(30)"
    )
    result = _run_in_subreaper_process(
        [sys.executable, "-c", parent, child],
        cwd=tmp_path, timeout_seconds=2.0, output_dir=tmp_path,
    )

    child_pid = int(pid_file.read_text(encoding="ascii"))
    assert result["timed_out"] is True
    assert result["descendants_killed"] >= 1
    assert "parent-output" in result["stdout"]
    assert "detached-output" in result["stdout"]
    assert not Path(f"/proc/{child_pid}").exists()


def test_mutation_normal_exit_with_setsid_grandchild_is_crashed(tmp_path):
    pid_file = tmp_path / "normal-grandchild.pid"
    child = (
        "import os,time; os.setsid(); "
        f"open({str(pid_file)!r},'w').write(str(os.getpid())); "
        "print('grandchild-output', flush=True); time.sleep(30)"
    )
    parent = (
        "import pathlib,subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "pid_file=pathlib.Path(sys.argv[2]); "
        "exec('while not pid_file.exists():\\n time.sleep(0.01)'); "
        "print('parent-output', flush=True); sys.exit(1)"
    )
    result = _run_in_subreaper_process(
        [sys.executable, "-c", parent, child, str(pid_file)],
        cwd=tmp_path, timeout_seconds=5, output_dir=tmp_path,
    )

    child_pid = int(pid_file.read_text(encoding="ascii"))
    assert result["returncode"] == 1
    assert result["descendants_killed"] >= 1
    assert result["process_tree_evidence"]["outliving_descendants"] >= 1
    assert not Path(f"/proc/{child_pid}").exists()


def test_mutation_output_is_bounded_to_the_last_mebibyte(tmp_path):
    result = _run_in_subreaper_process(
        [sys.executable, "-c", "print('x' * ((1 << 20) + 100))"],
        cwd=tmp_path, timeout_seconds=5, output_dir=tmp_path,
    )
    assert len(result["stdout"].encode("utf-8")) == \
        mutation_campaign._CANDIDATE_OUTPUT_TAIL_BYTES


def test_timeout_is_inconclusive_and_cannot_count_as_a_kill():
    result = mutation_campaign._candidate_result(
        _job(), returncode=None, elapsed_seconds=120, timed_out=True,
        process_tree_evidence=_process_tree_evidence(leader_status="timeout-killed"),
    )
    assert result.outcome == "crashed"
    assert result.termination == "timeout"
    assert result.exit_code == 124


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4])
def test_legacy_resume_evidence_without_subreaper_identity_is_refused(
        tmp_path, schema_version):
    evidence = tmp_path / "mutation.json"
    evidence.write_text(json.dumps({"schema_version": schema_version}), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=f"predates the current process-tree evidence schema.*{evidence}",
    ):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=tmp_path,
            resume=True,
            base="base",
            head="head",
            project_prefix=Path("cmru"),
            assay_source_commit="assay",
            assay_source_root=tmp_path,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resource_limit_event_overrides_a_test_failure_kill():
    evidence = SimpleNamespace(
        limit_hit=True,
        to_dict=lambda: _limited_resource_evidence(),
    )
    result = mutation_campaign._candidate_result(
        _job(), returncode=1, elapsed_seconds=2, timed_out=False,
        resource_limit_evidence=evidence,
        process_tree_evidence=_process_tree_evidence(),
    )
    assert result.outcome == "crashed"
    assert result.termination == "resource-limit"
    assert result.resource_limit_evidence == _limited_resource_evidence()


def test_descendant_leak_is_crashed_and_cannot_count_as_a_kill():
    result = mutation_campaign._candidate_result(
        _job(), returncode=1, elapsed_seconds=2, timed_out=False,
        descendants_killed=1,
        process_tree_evidence=_process_tree_evidence(
            outliving_descendants=1, descendants_signalled=1
        ),
    )
    assert result.outcome == "crashed"
    assert result.termination == "descendant-leak"


def test_resume_reuses_killed_candidates_and_retries_other_outcomes(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    jobs = [_job(line=7), _job(line=8), _job(line=9)]
    previous = {
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": head,
        "head": head,
        "project_prefix": "cmru",
        "assay_source_commit": head,
        "max_mutants": 10,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "timeout_seconds": 120,
        "candidate_count": 3,
        "test_manifest": mutation_campaign._test_manifest(repo, Path("cmru")),
        "results": [
            {
                "path": jobs[0].path,
                "line": jobs[0].site.lineno,
                "operator": jobs[0].site.operator,
                "description": jobs[0].site.description,
                "exit_code": 1,
                "outcome": "killed",
                "resource_limit_evidence": _zero_resource_evidence(),
            },
            {
                "path": jobs[1].path,
                "line": jobs[1].site.lineno,
                "operator": jobs[1].site.operator,
                "description": jobs[1].site.description,
                "outcome": "crashed",
            },
            {
                "path": jobs[2].path,
                "line": jobs[2].site.lineno,
                "operator": jobs[2].site.operator,
                "description": jobs[2].site.description,
                "exit_code": 124,
                "outcome": "crashed",
                "termination": "timeout",
            },
        ],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    missing_manifest = dict(previous)
    missing_manifest.pop("test_manifest")
    evidence.write_text(
        json.dumps(_with_process_tree_contract(missing_manifest)), encoding="utf-8",
    )

    def resume_results():
        return mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=head,
            head=head,
            project_prefix=Path("cmru"),
            assay_source_commit=head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=jobs,
            max_mutants=10,
            timeout_seconds=120,
        )

    with pytest.raises(ValueError, match="no exact test and fixture manifest"):
        resume_results()

    evidence.write_text(json.dumps(_with_process_tree_contract(previous)), encoding="utf-8")

    resumed = resume_results()
    assert resumed[0] == previous["results"][0]
    assert resumed[1] is None
    assert resumed[2] is None  # timeout outcomes are retried, never reused.

    with pytest.raises(ValueError, match="does not match this campaign"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=head,
            head=head,
            project_prefix=Path("cmru"),
            assay_source_commit=head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=jobs,
            max_mutants=10,
            timeout_seconds=60,
        )


def test_resume_refuses_changed_source(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    source = project / "src" / "cmru" / "thing.py"
    source.write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    previous_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "cmru/src"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "source change"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    # Raw Git would now see the replaced old commit as the new HEAD and reuse
    # results for a source tree whose Assay-discovered candidate set changed.
    subprocess.run(["git", "-C", str(repo), "replace", previous_head, head], check=True)
    raw_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", previous_head, head, "--", "cmru/src"],
        check=False,
    )
    assert raw_diff.returncode == 0
    job = _job()
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(_with_process_tree_contract({
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": previous_head,
        "head": previous_head,
        "project_prefix": "cmru",
        "assay_source_commit": previous_head,
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "candidate_count": 1,
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "outcome": "killed",
            "resource_limit_evidence": _zero_resource_evidence(),
        }],
    })), encoding="utf-8")

    with pytest.raises(ValueError, match="predates a product-source change"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=head,
            project_prefix=Path("cmru"),
            assay_source_commit=head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resume_refuses_changed_shared_library_in_the_executed_fixture(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    dependency = repo / "libraries" / "worktree" / "src" / "worktree" / "__init__.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay", "libraries/worktree"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    job = _job()
    previous = {
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": base,
        "head": base,
        "project_prefix": "cmru",
        "assay_source_commit": base,
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "timeout_seconds": 120,
        "candidate_count": 1,
        "test_manifest": mutation_campaign._test_manifest(repo, Path("cmru")),
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "exit_code": 1,
            "outcome": "killed",
            "resource_limit_evidence": _zero_resource_evidence(),
        }],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(_with_process_tree_contract(previous)), encoding="utf-8")

    dependency.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "libraries/worktree"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "change copied library"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    with pytest.raises(ValueError, match="test suite and fixture closure"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=base,
            head=head,
            project_prefix=Path("cmru"),
            assay_source_commit=head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resume_refuses_added_or_changed_test_files(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    assay = _make_assay_source(repo)
    (project / "src" / "cmru").mkdir(parents=True)
    tests = project / "tests"
    tests.mkdir()
    source = project / "src" / "cmru" / "thing.py"
    source.write_text("value = 1\n", encoding="utf-8")
    test_file = tests / "test_thing.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    previous_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    job = _job()
    previous = {
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": previous_head,
        "head": previous_head,
        "project_prefix": "cmru",
        "assay_source_commit": previous_head,
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "timeout_seconds": 120,
        "candidate_count": 1,
        "test_manifest": mutation_campaign._test_manifest(repo, Path("cmru")),
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "exit_code": 1,
            "outcome": "killed",
            "resource_limit_evidence": _zero_resource_evidence(),
        }],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(_with_process_tree_contract(previous)), encoding="utf-8")

    (tests / "test_more_contracts.py").write_text(
        "def test_more(): assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "cmru/tests"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "add tests"],
        check=True,
    )
    additive_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    with pytest.raises(ValueError, match="test suite and fixture closure"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=additive_head,
            project_prefix=Path("cmru"),
            assay_source_commit=additive_head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )

    test_file.write_text("def test_ok(): assert False\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "cmru/tests"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "rewrite existing test"],
        check=True,
    )
    changed_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    with pytest.raises(ValueError, match="test suite and fixture closure"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=changed_head,
            project_prefix=Path("cmru"),
            assay_source_commit=changed_head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )


def test_resume_accepts_monorepo_commits_with_unchanged_assay_source(tmp_path):
    repo = tmp_path / "repo"
    project = repo / "cmru"
    (project / "src" / "cmru").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src" / "cmru" / "thing.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_thing.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    assay = repo / "assay"
    (assay / "src" / "assay").mkdir(parents=True)
    (assay / "src" / "assay" / "__init__.py").write_text("__version__ = '1'\n", encoding="utf-8")
    (assay / "analysis" / "src" / "assay_analysis").mkdir(parents=True)
    (assay / "analysis" / "src" / "assay_analysis" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8",
    )
    (assay / "pyproject.toml").write_text(
        "[build-system]\nbuild-backend = 'setuptools.build_meta'\n\n"
        "[project]\nname = 'assay'\n"
        "[tool.setuptools.packages.find]\nwhere = ['src', 'analysis/src']\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "cmru", "assay"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        check=True,
    )
    previous_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    job = _job()
    previous = {
        "schema_version": 3,
        "candidate_isolation": "subreaper/1",
        "base": previous_head,
        "head": previous_head,
        "project_prefix": "cmru",
        "assay_source_commit": previous_head,
        "max_mutants": 1,
        "operators": list(mutation_campaign.OPERATORS),
        "test_argv": ["pytest", "tests"],
        "timeout_seconds": 120,
        "candidate_count": 1,
        "test_manifest": mutation_campaign._test_manifest(repo, Path("cmru")),
        "results": [{
            "path": job.path,
            "line": job.site.lineno,
            "operator": job.site.operator,
            "description": job.site.description,
            "exit_code": 1,
            "outcome": "killed",
            "resource_limit_evidence": _zero_resource_evidence(),
        }],
    }
    evidence = project / ".assay" / "mutation-cmru.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps(_with_process_tree_contract(previous)), encoding="utf-8")

    (repo / "other").mkdir()
    (repo / "other" / "note.md").write_text("documentation only\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "other"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "add docs"],
        check=True,
    )
    additive_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    resumed = mutation_campaign._resume_results(
        evidence_path=evidence,
        repo_root=repo,
        resume=True,
        base=previous_head,
        head=additive_head,
        project_prefix=Path("cmru"),
        assay_source_commit=additive_head,
        assay_source_root=assay,
        assay_git=assay_git,
        test_argv=["pytest", "tests"],
        jobs=[job],
        max_mutants=1,
        timeout_seconds=120,
    )
    assert resumed == [previous["results"][0]]

    (assay / "analysis" / "src" / "assay_analysis" / "new_module.py").write_text(
        "VALUE = 2\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires a clean Assay source tree"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=additive_head,
            project_prefix=Path("cmru"),
            assay_source_commit=additive_head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )
    (assay / "analysis" / "src" / "assay_analysis" / "new_module.py").unlink()
    (assay / "analysis" / "src" / "assay_analysis" / "__init__.py").write_text(
        "VALUE = 2\n", encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "assay/analysis/src"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "change assay"],
        check=True,
    )
    changed_assay_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    with pytest.raises(ValueError, match="predates an Assay source change"):
        mutation_campaign._resume_results(
            evidence_path=evidence,
            repo_root=repo,
            resume=True,
            base=previous_head,
            head=changed_assay_head,
            project_prefix=Path("cmru"),
            assay_source_commit=changed_assay_head,
            assay_source_root=assay,
            assay_git=assay_git,
            test_argv=["pytest", "tests"],
            jobs=[job],
            max_mutants=1,
            timeout_seconds=120,
        )
