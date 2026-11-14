"""Regression coverage for KI-162, KI-163, KI-164 and KI-165 item 2."""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

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
    wheel_enabled: bool = False,
    fmt: str = "xztar",
    keep_tree: bool | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
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
    ]
    if wheel_enabled:
        rows.extend(["[wheel]", "enabled = true"])
    rows.extend([
        "[archive]",
        *archive_rows,
        "[copy]",
        "files = []",
        "dirs = " + json.dumps(list(copy_dirs)),
    ])
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
    (source / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    config = _write_bundle(tmp_path)

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    out = capsys.readouterr().err
    assert "[WARN]" in out and "src/app-defaults.env" in out
    assert "[WARN]" in out and "src/cacert.pem" in out
    assert "[WARN]" in out and "src/pyproject.toml" in out
    manifest, names = _archive_contents(archive)
    assert "app/src/app-defaults.env" not in names
    assert "app/src/cacert.pem" not in names
    assert "app/src/pyproject.toml" not in names
    assert "src/app-defaults.env" not in manifest["files"]
    assert "src/cacert.pem" not in manifest["files"]
    assert "src/pyproject.toml" not in manifest["files"]


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


@pytest.mark.parametrize("filename", ["minisign.key", "private.key", "pyproject.toml"])
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
    ("src/../secret.env", "relative POSIX"),
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


@pytest.mark.parametrize("variant_build", [False, True])
def test_keep_tree_false_removes_bundle_and_wheel_trees_after_success(
    tmp_path, monkeypatch, variant_build,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config_path = _write_bundle(tmp_path, wheel_enabled=True)
    variant = None
    if variant_build:
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'name_template = "demo-v{version}.tar.xz"',
                'name_template = "demo-v{version}-{variant}.tar.xz"',
            ),
            encoding="utf-8",
        )
        variant = SimpleNamespace(name="py311", build_arg="3.11", label="Python 3.11")

    def build_wheel(config):
        config.client_dir.mkdir(parents=True, exist_ok=True)
        (config.client_dir / "demo-client.whl").write_bytes(b"wheel bytes\n")

    monkeypatch.setattr(bundle, "build_wheel", build_wheel)
    config = bundle.parse_config(config_path)
    archive = bundle.run_bundle(
        config_path,
        variant=variant,
        manifest_project="demo",
        manifest_tag="demo-v1.2.3",
    )

    manifest, names = _archive_contents(archive)
    assert "app/client/demo-client.whl" in names
    assert "client/demo-client.whl" in manifest["files"]
    assert sorted(path.name for path in config.dist_dir.iterdir()) == [archive.name]
    assert not config.bundle_dir.exists()
    assert not config.client_dir.exists()


def test_keep_tree_true_retains_bundle_and_wheel_trees_after_success(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config_path = _write_bundle(tmp_path, wheel_enabled=True, keep_tree=True)

    def build_wheel(config):
        config.client_dir.mkdir(parents=True, exist_ok=True)
        (config.client_dir / "demo-client.whl").write_bytes(b"wheel bytes\n")

    monkeypatch.setattr(bundle, "build_wheel", build_wheel)
    config = bundle.parse_config(config_path)
    archive = bundle.run_bundle(
        config_path, manifest_project="demo", manifest_tag="demo-v1.2.3",
    )

    assert archive.is_file()
    assert (config.bundle_dir / "client/demo-client.whl").is_file()
    assert (config.client_dir / "demo-client.whl").is_file()


@pytest.mark.parametrize("failure_point", ["stage", "archive"])
def test_keep_tree_false_retains_bundle_and_wheel_trees_on_failure(
    tmp_path, monkeypatch, failure_point,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config_path = _write_bundle(
        tmp_path, wheel_enabled=True, stage=failure_point == "stage",
    )

    def build_wheel(config):
        config.client_dir.mkdir(parents=True, exist_ok=True)
        (config.client_dir / "demo-client.whl").write_bytes(b"wheel bytes\n")

    monkeypatch.setattr(bundle, "build_wheel", build_wheel)
    config = bundle.parse_config(config_path)
    if failure_point == "stage":
        def fail_stage(_argv, _cwd):
            secret = config.bundle_dir / "failed.env"
            secret.write_text("TOKEN=retained for diagnosis\n", encoding="utf-8")
            raise subprocess.CalledProcessError(7, ["stage"])

        runner = fail_stage
    else:
        def fail_archive(*args, **kwargs):
            raise OSError("archive write failed")

        monkeypatch.setattr(bundle, "create_archive", fail_archive)
        runner = None

    error = subprocess.CalledProcessError if failure_point == "stage" else OSError
    with pytest.raises(error):
        bundle.run_bundle(
            config_path,
            runner=runner,
            manifest_project="demo",
            manifest_tag="demo-v1.2.3",
        )

    assert (config.client_dir / "demo-client.whl").is_file()
    assert (config.bundle_dir / "client/demo-client.whl").is_file()
    assert (config.bundle_dir / "src/app.py").is_file()
    if failure_point == "stage":
        assert (config.bundle_dir / "failed.env").read_text(encoding="utf-8") == "TOKEN=retained for diagnosis\n"
    assert not list(config.dist_dir.glob("*.tar.xz"))


def test_archive_rejects_a_file_added_after_manifest_creation(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config_path = _write_bundle(tmp_path)
    config = bundle.parse_config(config_path)
    write_manifest = bundle.write_bundle_manifest

    def add_file_after_manifest(config, *, project, tag):
        manifest_path = write_manifest(config, project=project, tag=tag)
        (config.bundle_dir / "late-output.txt").write_text("created after manifest\n", encoding="utf-8")
        return manifest_path

    monkeypatch.setattr(bundle, "write_bundle_manifest", add_file_after_manifest)
    with pytest.raises(ValueError, match="changed after the manifest"):
        bundle.run_bundle(
            config_path, manifest_project="demo", manifest_tag="demo-v1.2.3",
        )

    assert (config.bundle_dir / "late-output.txt").is_file()
    assert not list(config.dist_dir.glob("*.tar.xz"))


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
    # keep_tree defaults to false for a variant build too: only the archive remains.
    assert sorted(path.name for path in (tmp_path / "dist").iterdir()) == [archive.name]


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


def test_keep_config_rejects_duplicate_paths_and_non_boolean_keep_tree(tmp_path):
    duplicate = _write_bundle(tmp_path / "duplicate", keep=("src/app.env", "src/app.env"))
    with pytest.raises(ValueError, match="duplicate path"):
        bundle.parse_config(duplicate)

    bad_tree = _write_bundle(tmp_path / "bad-tree")
    bad_tree.write_text(
        bad_tree.read_text(encoding="utf-8").replace(
            'format = "xztar"', 'format = "xztar"\nkeep_tree = "false"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="keep_tree must be true or false"):
        bundle.parse_config(bad_tree)


def test_copy_sources_rejects_bad_file_and_directory_inputs(tmp_path):
    (tmp_path / "target.txt").write_text("target\n", encoding="utf-8")
    (tmp_path / "alias.txt").symlink_to(tmp_path / "target.txt")
    config_path = _write_bundle(tmp_path, copy_dirs=())
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "files = []", 'files = ["alias.txt"]',
        ),
        encoding="utf-8",
    )
    config = bundle.parse_config(config_path)
    config.bundle_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="symlink"):
        bundle.copy_sources(config)

    file_path = _write_bundle(tmp_path / "copy-directory", copy_dirs=("not-a-dir",))
    (tmp_path / "copy-directory/not-a-dir").write_text("file\n", encoding="utf-8")
    config = bundle.parse_config(file_path)
    config.bundle_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a directory"):
        bundle.copy_sources(config)

    (tmp_path / "copy-file-directory/folder").mkdir(parents=True)
    file_path = _write_bundle(tmp_path / "copy-file-directory", copy_dirs=())
    file_path.write_text(
        file_path.read_text(encoding="utf-8").replace(
            "files = []", 'files = ["folder"]',
        ),
        encoding="utf-8",
    )
    config = bundle.parse_config(file_path)
    config.bundle_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a regular file"):
        bundle.copy_sources(config)


def test_copy_sources_warns_and_skips_a_hard_excluded_directory(tmp_path, capsys):
    (tmp_path / ".git").mkdir()
    config_path = _write_bundle(tmp_path, copy_dirs=())
    config = bundle.parse_config(config_path)
    config.copy_dirs.append(".git")
    config.bundle_dir.mkdir(parents=True)

    bundle.copy_sources(config)

    assert "[WARN]" in capsys.readouterr().err
    assert not (config.bundle_dir / ".git").exists()


@pytest.mark.parametrize("origin", ["copy", "stage"])
def test_suffix_named_directories_are_not_filtered_as_protected_files(tmp_path, origin):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    if origin == "copy":
        (tmp_path / "src/certs.pem").mkdir(parents=True)
        (tmp_path / "src/certs.pem/ca.txt").write_text("public CA catalog\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=origin == "stage")

    def runner(_argv, _cwd):
        directory = tmp_path / "dist/app/certs.pem"
        directory.mkdir(parents=True)
        (directory / "ca.txt").write_text("public CA catalog\n", encoding="utf-8")

    archive = bundle.run_bundle(
        config,
        runner=runner if origin == "stage" else None,
        manifest_project="demo",
        manifest_tag="demo-v1.2.3",
    )

    manifest, names = _archive_contents(archive)
    relative = "src/certs.pem/ca.txt" if origin == "copy" else "certs.pem/ca.txt"
    assert f"app/{relative}" in names
    assert relative in manifest["files"]


def test_duplicate_copy_inputs_warn_once_for_each_skipped_path(tmp_path, capsys):
    (tmp_path / "src/.git").mkdir(parents=True)
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src/.git/config").write_text("source private ref\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True, fmt="gztar")

    def stage_private_path(_argv, _cwd):
        private = tmp_path / "dist/app/src/.git/config"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("generated private ref\n", encoding="utf-8")

    bundle.run_bundle(
        config, runner=stage_private_path,
        manifest_project="demo", manifest_tag="demo-v1.2.3",
    )

    warning = "[WARN] Skipping hard-excluded bundle path: src/.git"
    assert capsys.readouterr().err.count(warning) == 1


def test_copy_sources_rejects_non_regular_entries(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    os.mkfifo(source / "pipe")
    config_path = _write_bundle(tmp_path)
    config = bundle.parse_config(config_path)
    config.bundle_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="not a regular file or directory"):
        bundle.copy_sources(config)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_stage_output_rejects_symlinks_and_non_regular_entries(tmp_path, kind):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)
    bundle_root = tmp_path / "dist/app"

    def runner(_argv, _cwd):
        target = bundle_root / "generated.bin"
        if kind == "symlink":
            external = tmp_path / "outside.bin"
            external.write_bytes(b"outside\n")
            target.symlink_to(external)
        else:
            os.mkfifo(target)

    message = "symlink" if kind == "symlink" else "not a regular file or directory"
    with pytest.raises(ValueError, match=message):
        bundle.run_bundle(
            config, runner=runner, manifest_project="demo", manifest_tag="demo-v1.2.3",
        )


def test_stage_pyc_is_filtered_from_archive_and_manifest(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)

    def runner(_argv, _cwd):
        (tmp_path / "dist/app/generated.pyc").write_bytes(b"cache\n")

    archive = bundle.run_bundle(
        config, runner=runner, manifest_project="demo", manifest_tag="demo-v1.2.3",
    )

    manifest, names = _archive_contents(archive)
    assert "app/generated.pyc" not in names
    assert "generated.pyc" not in manifest["files"]


def test_allowlist_rechecks_excludes_after_resolving_a_direct_file(tmp_path):
    (tmp_path / "private.env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "catalog.py").symlink_to(tmp_path / "private.env")

    assert bundle.collect_allowlist_members(
        tmp_path, ["catalog.py"], archive_prefix="",
    ) == []


def _dev_version_inputs(tmp_path, *, prefix="demo-v"):
    project = SimpleNamespace(version=SimpleNamespace(strategy="semver"), prefix=prefix)
    forge = SimpleNamespace(repo_root=tmp_path, projects={"demo": project})
    return forge, project


def test_bundle_dev_version_uses_next_release_and_git_identity(tmp_path, monkeypatch):
    from cmru import version

    forge, project = _dev_version_inputs(tmp_path)
    monkeypatch.setattr(version, "status_records", lambda *_: [{
        "project": "demo", "next_version": "demo-v1.2.4", "last_tag": "demo-v1.2.3",
    }])
    git_results = iter(["4", "ABCDEF123456"])
    calls = []

    def fake_git(root, *args):
        calls.append(args)
        return next(git_results)

    monkeypatch.setattr(version, "_git", fake_git)

    assert handlers._bundle_dev_version(forge, "demo", project) == "1.2.4.dev4+gabcdef123456"
    assert calls[0] == ("rev-list", "--count", "demo-v1.2.3..HEAD")


def test_bundle_dev_version_can_fall_back_to_last_tag_and_default_prefix(tmp_path, monkeypatch):
    from cmru import version

    forge, project = _dev_version_inputs(tmp_path, prefix=None)
    monkeypatch.setattr(version, "status_records", lambda *_: [{
        "project": "demo", "last_tag": "demo-v1.2.3",
    }])
    git_results = iter(["0", "abcdef123456"])
    monkeypatch.setattr(version, "_git", lambda *_: next(git_results))

    assert handlers._bundle_dev_version(forge, "demo", project) == "1.2.3.dev0+gabcdef123456"


def test_bundle_dev_version_refuses_missing_or_mismatched_version_sources(tmp_path, monkeypatch):
    from cmru import version

    forge, project = _dev_version_inputs(tmp_path)
    with pytest.raises(RuntimeError, match="set the configured archive version"):
        handlers._bundle_dev_version(
            forge, "demo", SimpleNamespace(version=None, prefix="demo-v"),
        )
    with pytest.raises(RuntimeError, match="set the configured archive version"):
        handlers._bundle_dev_version(
            forge, "demo", SimpleNamespace(
                version=SimpleNamespace(strategy="external:tool"), prefix="demo-v",
            ),
        )

    with pytest.raises(RuntimeError, match="no repository root"):
        handlers._bundle_dev_version(
            SimpleNamespace(repo_root=None, projects={"demo": project}), "demo", project,
        )

    records = []
    monkeypatch.setattr(version, "status_records", lambda *_: records)
    with pytest.raises(RuntimeError, match="no record"):
        handlers._bundle_dev_version(forge, "demo", project)

    records.append({"project": "demo", "next_version": "other-v1.2.3"})
    with pytest.raises(RuntimeError, match="does not start with"):
        handlers._bundle_dev_version(forge, "demo", project)

    records[:] = [{"project": "demo", "last_tag": "other-v1.2.3"}]
    with pytest.raises(RuntimeError, match="unexpected release tag"):
        handlers._bundle_dev_version(forge, "demo", project)

    records[:] = [{"project": "demo"}]
    with pytest.raises(RuntimeError, match="no version"):
        handlers._bundle_dev_version(forge, "demo", project)


@pytest.mark.parametrize("git_results", [("count", "abcdef123456"), ("3", "not-a-hash")])
def test_bundle_dev_version_rejects_invalid_git_identity(tmp_path, monkeypatch, git_results):
    from cmru import version

    forge, project = _dev_version_inputs(tmp_path)
    monkeypatch.setattr(version, "status_records", lambda *_: [{
        "project": "demo", "next_version": "demo-v1.2.4",
    }])
    results = iter(git_results)
    monkeypatch.setattr(version, "_git", lambda *_: next(results))

    with pytest.raises(RuntimeError, match="invalid commit count or short hash"):
        handlers._bundle_dev_version(forge, "demo", project)


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


def _without_manifest(config: Path) -> None:
    config.write_text(
        config.read_text(encoding="utf-8").replace('[manifest]\nname = "manifest.json"\n', ""),
        encoding="utf-8",
    )


def test_keep_names_one_exact_path_not_a_suffix_or_a_basename(tmp_path, capsys):
    for directory in ("src", "xsrc", "other"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (tmp_path / directory / "cacert.pem").write_text("public CA bundle\n", encoding="utf-8")
    config = _write_bundle(
        tmp_path, copy_dirs=("src", "xsrc", "other"), keep=("src/cacert.pem",),
    )

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")

    manifest, names = _archive_contents(archive)
    assert "app/src/cacert.pem" in names and "src/cacert.pem" in manifest["files"]
    for lookalike in ("xsrc/cacert.pem", "other/cacert.pem"):
        assert f"app/{lookalike}" not in names
        assert lookalike not in manifest["files"]
    err = capsys.readouterr().err
    assert "xsrc/cacert.pem" in err and "other/cacert.pem" in err


def test_stage_written_lookalike_of_a_keep_path_still_fails_closed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src/cacert.pem").write_text("public CA bundle\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True, keep=("src/cacert.pem",))

    def runner(_argv, _cwd):
        (tmp_path / "dist/app/vendor/src").mkdir(parents=True)
        (tmp_path / "dist/app/vendor/src/cacert.pem").write_text("other\n", encoding="utf-8")
        (tmp_path / "dist/app/cacert.pem").write_text("other\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[archive\]\.keep") as raised:
        bundle.run_bundle(
            config, runner=runner, manifest_project="demo", manifest_tag="demo-v1.2.3",
        )
    assert "cacert.pem, vendor/src/cacert.pem" in str(raised.value)


def test_stage_written_unkeepable_name_reports_that_keep_cannot_help(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)

    def runner(_argv, _cwd):
        (tmp_path / "dist/app/nested").mkdir()
        (tmp_path / "dist/app/nested/ciu.env").write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be listed in") as raised:
        bundle.run_bundle(
            config, runner=runner, manifest_project="demo", manifest_tag="demo-v1.2.3",
        )
    assert "nested/ciu.env" in str(raised.value)


@pytest.mark.parametrize("fmt, suffix", [("xztar", ".tar.xz"), ("gztar", ".tar.gz")])
def test_stage_written_cache_and_private_names_never_reach_any_archive_format(
    tmp_path, fmt, suffix,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True, fmt=fmt)
    config.write_text(
        config.read_text(encoding="utf-8").replace(".tar.xz", suffix), encoding="utf-8",
    )
    _without_manifest(config)

    def runner(_argv, _cwd):
        root = tmp_path / "dist/app"
        (root / "pkg/__pycache__").mkdir(parents=True)
        (root / "pkg/__pycache__/module.txt").write_text("cache\n", encoding="utf-8")
        (root / "pkg/ok.txt").write_text("ok\n", encoding="utf-8")
        (root / "module.pyc").write_bytes(b"cache\n")
        (root / ".git").mkdir()
        (root / ".git/config").write_text("[core]\n", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules/index.js").write_text("x\n", encoding="utf-8")

    archive = bundle.run_bundle(config, runner=runner)

    with tarfile.open(archive, "r:xz" if fmt == "xztar" else "r:gz") as tf:
        names = set(tf.getnames())
    assert "app/pkg/ok.txt" in names
    assert not {n for n in names if any(
        marker in n for marker in ("__pycache__", ".pyc", ".git", "node_modules")
    )}


@pytest.mark.parametrize("kind", ["secret", "symlink"])
def test_stage_output_is_validated_even_without_a_manifest(tmp_path, kind):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    config = _write_bundle(tmp_path, stage=True)
    _without_manifest(config)

    def runner(_argv, _cwd):
        root = tmp_path / "dist/app"
        if kind == "secret":
            (root / "server.pem").write_text("private\n", encoding="utf-8")
        else:
            outside = tmp_path / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            (root / "public.txt").symlink_to(outside)

    with pytest.raises(ValueError, match=r"\[archive\]\.keep" if kind == "secret" else "symlink"):
        bundle.run_bundle(config, runner=runner)
    assert not list((tmp_path / "dist").glob("*.tar.xz"))


def test_copy_files_are_flattened_so_their_keep_path_is_the_basename(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra/cacert.pem").write_text("public CA bundle\n", encoding="utf-8")
    config = _write_bundle(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace("files = []", 'files = ["extra/cacert.pem"]'),
        encoding="utf-8",
    )

    archive = bundle.run_bundle(config, manifest_project="demo", manifest_tag="demo-v1.2.3")
    manifest, names = _archive_contents(archive)
    assert "app/cacert.pem" not in names and "cacert.pem" not in manifest["files"]
    assert "[WARN]" in capsys.readouterr().err

    kept = _write_bundle(tmp_path, keep=("cacert.pem",))
    kept.write_text(
        kept.read_text(encoding="utf-8").replace("files = []", 'files = ["extra/cacert.pem"]'),
        encoding="utf-8",
    )
    archive = bundle.run_bundle(kept, manifest_project="demo", manifest_tag="demo-v1.2.3")
    manifest, names = _archive_contents(archive)
    assert "app/cacert.pem" in names and "cacert.pem" in manifest["files"]
