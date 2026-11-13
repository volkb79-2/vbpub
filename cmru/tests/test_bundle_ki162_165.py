"""Regression coverage for KI-162, KI-163, KI-164 and KI-165 item 2."""
from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from cmru import bundle, handlers


@pytest.fixture(autouse=True)
def _build_environment(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    monkeypatch.setenv("DEMO_VERSION", "1.2.3")


def _write_bundle(
    root: Path,
    *,
    copy_dirs: tuple[str, ...] = ("src",),
    keep: tuple[str, ...] = (),
    stage: bool = False,
    vendor: bool = False,
    fmt: str = "xztar",
    keep_tree: bool | None = None,
) -> Path:
    archive_rows = [
        'name_template = "demo-v{version}.tar.xz"',
        'version_env = "DEMO_VERSION"',
        f'format = "{fmt}"',
    ]
    if keep:
        archive_rows.append("keep = " + json.dumps(list(keep)))
    if keep_tree is not None:
        archive_rows.append(f"keep_tree = {str(keep_tree).lower()}")
    rows = [
        'project_root = "."',
        'dist_dir = "dist"',
        'bundle_dir = "app"',
        "[archive]",
        *archive_rows,
        "[copy]",
        "files = []",
        "dirs = " + json.dumps(list(copy_dirs)),
    ]
    if vendor:
        (root / "requirements-runtime.txt").write_text("demo-dependency==1.0\n", encoding="utf-8")
        rows.extend([
            "[vendor]",
            'requirements = ["requirements-runtime.txt"]',
            'platforms = ["manylinux2014_x86_64"]',
            'dest = "vendor"',
        ])
    if stage:
        rows.extend(["[stage]", 'argv = ["stage", "{bundle_dir}"]'])
    rows.extend(["[manifest]", 'name = "manifest.json"'])
    config = root / "bundle.toml"
    config.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return config


def _archive_contents(archive: Path) -> tuple[dict, set[str]]:
    mode = "r:gz" if archive.name.endswith(".tar.gz") else "r:xz"
    with tarfile.open(archive, mode) as tf:
        names = set(tf.getnames())
        manifest_file = tf.extractfile("app/manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    return manifest, names


def test_copy_rejects_source_symlink_instead_of_bundling_its_target(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "private.key").write_text("private material\n", encoding="utf-8")
    (source / "public.txt").symlink_to(tmp_path / "private.key")
    config = _write_bundle(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    assert not list((tmp_path / "dist").glob("*.tar.xz"))


def test_copy_keep_paths_are_exact_for_archive_and_manifest(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "app-defaults.env").write_text("MODE=public\n", encoding="utf-8")
    (source / "cacert.pem").write_text("public CA bundle\n", encoding="utf-8")
    config = _write_bundle(
        tmp_path,
        keep=("src/app-defaults.env", "src/cacert.pem"),
        keep_tree=True,
    )

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    manifest, names = _archive_contents(archive)
    assert "app/src/app-defaults.env" in names
    assert "app/src/cacert.pem" in names
    assert {"src/app-defaults.env", "src/cacert.pem"} <= set(manifest["files"])
    assert (tmp_path / "dist/app/src/app-defaults.env").is_file()
    assert (tmp_path / "dist/app/src/cacert.pem").is_file()


def test_keep_filter_also_applies_to_non_xztar_archive_format(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "app-defaults.env").write_text("MODE=public\n", encoding="utf-8")
    config = _write_bundle(
        tmp_path, keep=("src/app-defaults.env",), fmt="gztar", keep_tree=True,
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(".tar.xz", ".tar.gz"),
        encoding="utf-8",
    )

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    manifest, names = _archive_contents(archive)
    assert "app/src/app-defaults.env" in names
    assert "src/app-defaults.env" in manifest["files"]


def test_copy_without_keep_warns_and_omits_excluded_source_files(tmp_path, capsys):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "app-defaults.env").write_text("MODE=public\n", encoding="utf-8")
    (source / "cacert.pem").write_text("public CA bundle\n", encoding="utf-8")
    config = _write_bundle(tmp_path)

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    out = capsys.readouterr().err
    assert "[WARN]" in out and "src/app-defaults.env" in out
    assert "[WARN]" in out and "src/cacert.pem" in out
    manifest, names = _archive_contents(archive)
    assert "app/src/app-defaults.env" not in names
    assert "app/src/cacert.pem" not in names
    assert "src/app-defaults.env" not in manifest["files"]
    assert "src/cacert.pem" not in manifest["files"]


def test_copy_still_rejects_missing_configured_excluded_file(tmp_path):
    config = _write_bundle(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace("files = []", 'files = ["missing.env"]'),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="missing.env"):
        bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")


def test_stage_keep_allows_public_suffix_files_and_vendor_wheels_remain_included(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(
        tmp_path,
        keep=("app-defaults.env", "certifi/cacert.pem"),
        stage=True,
        vendor=True,
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'name_template = "demo-v{version}.tar.xz"',
            'name_template = "demo-v{version}-{variant}.tar.xz"',
        ),
        encoding="utf-8",
    )
    bundle_root = tmp_path / "dist/app"

    def runner(argv, cwd):
        if "pip" in argv:
            destination = Path(argv[argv.index("--dest") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
        else:
            (bundle_root / "app-defaults.env").write_text("MODE=public\n", encoding="utf-8")
            (bundle_root / "certifi").mkdir(parents=True, exist_ok=True)
            (bundle_root / "certifi/cacert.pem").write_text("public CA bundle\n", encoding="utf-8")

    archive = bundle.run_bundle(
        config,
        variant=bundle.VariantConfig("py311", "3.11", None),
        runner=runner,
        manifest_project="demo",
        manifest_tag="demo-v1.2.3",
    )

    manifest, names = _archive_contents(archive)
    assert "app/app-defaults.env" in names
    assert "app/certifi/cacert.pem" in names
    assert "app/vendor/demo-1.0-py3-none-any.whl" in names
    assert {"app-defaults.env", "certifi/cacert.pem"} <= set(manifest["files"])
    assert "vendor/demo-1.0-py3-none-any.whl" in manifest["files"]


@pytest.mark.parametrize("filename", ["minisign.key", "private.key"])
def test_stage_written_secret_suffix_files_fail_closed_with_path(tmp_path, filename):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)

    def runner(argv, cwd):
        (tmp_path / "dist/app" / filename).write_text("private material\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[archive\]\.keep") as raised:
        bundle.run_bundle(
            config,
            runner=runner,
            manifest_project="demo",
            manifest_tag="demo-v1.2.3",
        )

    assert filename in str(raised.value)
    assert (tmp_path / "dist/app" / filename).is_file()
    assert not list((tmp_path / "dist").glob("*.tar.xz"))


def test_keep_paths_are_bundle_relative_for_nested_copy_directories(tmp_path):
    source = tmp_path / "a/b"
    source.mkdir(parents=True)
    (source / "app-defaults.env").write_text("MODE=public\n", encoding="utf-8")
    config = _write_bundle(tmp_path, copy_dirs=("a/b",), keep=("b/app-defaults.env",))

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    manifest, names = _archive_contents(archive)
    assert "app/b/app-defaults.env" in names
    assert "b/app-defaults.env" in manifest["files"]


@pytest.mark.parametrize("keep, message", [
    ("/absolute/secret.env", "relative POSIX"),
    ("src/*.env", "glob"),
    ("src/.git/config.env", "hard-excluded"),
    ("src/minisign.key", "hard-excluded"),
    ("src/__pycache__/module.py", "hard-excluded"),
    ("src/module.pyc", "hard-excluded"),
])
def test_keep_configuration_rejects_unsafe_entries(tmp_path, keep, message):
    config = _write_bundle(tmp_path, keep=(keep,))

    with pytest.raises(ValueError, match=message):
        bundle.parse_config(config)


def test_keep_configuration_rejects_nonexistent_bundle_path_as_config_error(
    tmp_path, capsys,
):
    (tmp_path / "src").mkdir()
    config, cmru_config = _write_cmru_project(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'format = "xztar"', 'format = "xztar"\nkeep = ["src/missing.env"]',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_handler_args(config, cmru_config))

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "src/missing.env" in error
    assert "[archive].keep" in error


def test_nonkeepable_cache_paths_are_absent_from_archive_and_manifest(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)

    def runner(argv, cwd):
        root = tmp_path / "dist/app"
        (root / "__pycache__").mkdir(parents=True, exist_ok=True)
        (root / "__pycache__/module.pyc").write_bytes(b"cache")
        (root / "module.pyc").write_bytes(b"cache")
        (root / ".git").mkdir(parents=True, exist_ok=True)
        (root / ".git/config").write_text("private ref\n", encoding="utf-8")
        (root / "node_modules/lib").mkdir(parents=True, exist_ok=True)
        (root / "node_modules/lib/index.js").write_text("module\n", encoding="utf-8")

    archive = bundle.run_bundle(
        config, runner=runner, manifest_project="demo", manifest_tag="demo-v1.2.3",
    )

    manifest, names = _archive_contents(archive)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert "app/.git/config" not in names
    assert "app/node_modules/lib/index.js" not in names
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in manifest["files"])
    assert "node_modules/lib/index.js" not in manifest["files"]


def test_keep_tree_defaults_to_removed_only_after_archive_success(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path)

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    assert archive.is_file()
    assert not (tmp_path / "dist/app").exists()


def test_keep_tree_true_and_build_failures_retain_the_staged_tree(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, keep_tree=True)
    bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")
    assert (tmp_path / "dist/app/src/app.py").is_file()

    failed = _write_bundle(tmp_path, stage=True, keep_tree=False)
    def fail_stage(_argv, _cwd):
        raise subprocess.CalledProcessError(7, ["stage"])

    with pytest.raises(subprocess.CalledProcessError):
        bundle.run_bundle(
            failed,
            runner=fail_stage,
            manifest_project="demo",
            manifest_tag="demo-v1.2.3",
        )
    assert (tmp_path / "dist/app/src/app.py").is_file()

    failed = _write_bundle(tmp_path, stage=True, keep_tree=False)
    def fail_archive(*args, **kwargs):
        raise OSError("archive write failed")
    monkeypatch.setattr(bundle, "create_archive", fail_archive)
    with pytest.raises(OSError, match="archive write failed"):
        bundle.run_bundle(
            failed,
            runner=lambda _argv, _cwd: None,
            manifest_project="demo",
            manifest_tag="demo-v1.2.3",
        )
    assert (tmp_path / "dist/app/src/app.py").is_file()


def test_untagged_handler_uses_cmru_next_version_and_commit_identity(tmp_path, monkeypatch):
    bundle_config, cmru_config = _write_cmru_project(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "bundle-test@example.invalid")
    _git(tmp_path, "config", "user.name", "Bundle Test")
    _git(tmp_path, "add", "cmru.toml", "bundle.toml", "src/app.py")
    _git(tmp_path, "commit", "-q", "-m", "feat(demo): initial bundle")
    monkeypatch.delenv("DEMO_VERSION", raising=False)
    monkeypatch.delenv("CMRU_RELEASE_TAG", raising=False)

    handlers.cmd_bundle_build(_handler_args(bundle_config, cmru_config))

    archive = next((tmp_path / "dist").glob("*.tar.xz"))
    manifest, _names = _archive_contents(archive)
    version = archive.name.removeprefix("demo-v").removesuffix("-py311.tar.xz")
    assert version.startswith("0.1.0.dev1+g")
    assert manifest["tag"] == f"demo-v{version.replace('+', '-')}"
    from cmru.release import is_release_version
    assert not is_release_version(version)


@pytest.mark.parametrize("prefix", ["demo", "demo-v"])
def test_manifest_tag_matches_release_tag_for_both_prefix_forms(tmp_path, monkeypatch, prefix):
    bundle_config, cmru_config = _write_cmru_project(tmp_path, prefix=prefix)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "bundle-test@example.invalid")
    _git(tmp_path, "config", "user.name", "Bundle Test")
    _git(tmp_path, "add", "cmru.toml", "bundle.toml", "src/app.py")
    _git(tmp_path, "commit", "-q", "-m", "feat(demo): initial bundle")
    _git(tmp_path, "tag", "demo-v1.2.3")
    monkeypatch.setenv("DEMO_VERSION", "1.2.3")
    monkeypatch.setenv("CMRU_RELEASE_TAG", "demo-v1.2.3")

    handlers.cmd_bundle_build(_handler_args(bundle_config, cmru_config))

    archive = next((tmp_path / "dist").glob("*.tar.xz"))
    manifest, _names = _archive_contents(archive)
    assert manifest["tag"] == "demo-v1.2.3"


def _write_cmru_project(root: Path, *, prefix: str = "demo-v") -> tuple[Path, Path]:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    cmru_config = root / "cmru.toml"
    cmru_config.write_text(
        f'''schema_version = 1
[runtime]
kind = "none"
[github]
owner = "example-owner"
repo = "demo"
owner_type = "user"
token_source = "gh"
[targets]
host = "github"
registry = ["ghcr.io"]
[project]
id = "demo"
description = "offline bundle test"
template_revision = 5
prefix = "{prefix}"
artifacts = ["tarball"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
[[project.variants]]
name = "py311"
build_arg = "PYTHON_VERSION=3.11"
label = "Python 3.11"
[steps.build]
quiet = true
commands = [{{ label = "noop", argv = ["true"], cwd = "." }}]
[steps.run-tests]
quiet = true
commands = [{{ label = "noop", argv = ["true"], cwd = "." }}]
[steps.push]
quiet = true
commands = [{{ label = "noop", argv = ["true"], cwd = "." }}]
''',
        encoding="utf-8",
    )
    bundle_config = root / "bundle.toml"
    bundle_config.write_text(
        '''project_root = "."
dist_dir = "dist"
bundle_dir = "app"
[archive]
name_template = "demo-v{version}-{variant}.tar.xz"
version_env = "DEMO_VERSION"
format = "xztar"
[copy]
files = []
dirs = ["src"]
[manifest]
name = "manifest.json"
''',
        encoding="utf-8",
    )
    return bundle_config, cmru_config


def _handler_args(bundle_config: Path, cmru_config: Path):
    from argparse import Namespace
    return Namespace(
        bundle_config=str(bundle_config),
        cmru_config=str(cmru_config),
        name="demo",
        variant="all",
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()
