from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, release_controller, transaction


def _installed_distribution(tmp_path: Path, direct_url) -> SimpleNamespace:
    package = tmp_path / "site-packages" / "cmru"
    metadata = tmp_path / "site-packages" / "cmru-6.2.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    (package / "__init__.py").write_text("\n", encoding="utf-8")
    module_path = package / "cli.py"
    module_path.write_text("release controller\n", encoding="utf-8")
    (metadata / "METADATA").write_text("Name: cmru\nVersion: 6.2.0\n", encoding="utf-8")
    direct_url_text = direct_url if isinstance(direct_url, str) else json.dumps(direct_url)
    files = [
        Path("cmru/__init__.py"),
        Path("cmru/cli.py"),
        Path("cmru-6.2.0.dist-info/METADATA"),
    ]

    return SimpleNamespace(
        files=files,
        version="6.2.0",
        locate_file=lambda item: tmp_path / "site-packages" / item,
        read_text=lambda name: direct_url_text if name == "direct_url.json" else None,
        module_path=module_path,
    )


def _wheel_url(digest: str = "a" * 64) -> dict:
    return {
        "url": "file:///wheelhouse/cmru-6.2.0-py3-none-any.whl",
        "archive_info": {"hashes": {"sha256": digest}},
        "dir_info": {"editable": False},
    }


def test_release_controller_records_noneditable_wheel_and_installed_files(
    tmp_path, monkeypatch,
):
    installed = _installed_distribution(tmp_path, _wheel_url())
    monkeypatch.setattr(release_controller, "distribution", lambda name: installed)

    identity = release_controller.release_controller_identity(installed.module_path)

    assert identity["version"] == "6.2.0"
    assert identity["wheel_sha256"] == "a" * 64
    assert identity["module_path"] == str(installed.module_path.resolve())
    assert identity["distribution_sha256"] == hashlib.sha256(
        b"cmru-6.2.0.dist-info/METADATA\0Name: cmru\nVersion: 6.2.0\n"
        b"cmru/__init__.py\0\n"
        b"cmru/cli.py\0release controller\n"
    ).hexdigest()


def test_installed_controller_identity_is_available_when_self_release_loads_source(
    tmp_path, monkeypatch,
):
    installed = _installed_distribution(tmp_path, _wheel_url())
    monkeypatch.setattr(release_controller, "distribution", lambda name: installed)

    identity = release_controller.installed_release_controller_identity()

    assert identity["module_path"] == str(installed.module_path.resolve())
    assert identity["wheel_sha256"] == "a" * 64


def test_release_controller_accepts_legacy_pep610_archive_hash(tmp_path, monkeypatch):
    installed = _installed_distribution(tmp_path, {
        "url": "file:///wheelhouse/cmru-6.2.0-py3-none-any.whl",
        "archive_info": {"hash": "sha256=" + "c" * 64},
        "dir_info": {"editable": False},
    })
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)

    identity = release_controller.release_controller_identity(installed.module_path)

    assert identity["wheel_sha256"] == "c" * 64


def test_release_controller_refuses_missing_distribution(monkeypatch, tmp_path):
    def missing(_name):
        raise PackageNotFoundError("cmru")

    monkeypatch.setattr(release_controller, "distribution", missing)
    with pytest.raises(RuntimeError, match="requires an installed CMRU wheel"):
        release_controller.release_controller_identity(tmp_path / "cli.py")


def test_release_controller_refuses_empty_file_manifest(monkeypatch, tmp_path):
    installed = SimpleNamespace(files=[], version="6.2.0")
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)
    with pytest.raises(RuntimeError, match="no installed file manifest"):
        release_controller.release_controller_identity(tmp_path / "cli.py")


def test_release_controller_refuses_source_module_or_missing_module(
    tmp_path, monkeypatch,
):
    installed = _installed_distribution(tmp_path, _wheel_url())
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)
    source_module = tmp_path / "source" / "cli.py"
    source_module.parent.mkdir()
    source_module.write_text("source copy\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="active CLI module"):
        release_controller.release_controller_identity(source_module)

    with pytest.raises(RuntimeError, match="cannot resolve the active CMRU CLI module"):
        release_controller.release_controller_identity(tmp_path / "missing.py")


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (None, "requires wheel provenance"),
        ("not-json", "direct_url.json is malformed"),
        ("[]", "direct_url.json is malformed"),
        ({**_wheel_url(), "dir_info": {"editable": True}}, "refuses an editable"),
        ({"url": "file:///checkout/cmru", "dir_info": {"editable": False}}, "requires a non-editable wheel"),
        ({"url": "file:///wheel/cmru.whl", "archive_info": {"hashes": {"sha256": "z" * 64}}}, "requires a non-editable wheel"),
        ({"url": "file:///wheel/cmru.whl", "archive_info": {"hashes": {"sha256": "a" * 63}}}, "requires a non-editable wheel"),
        ({"url": 7, "archive_info": {"hashes": {"sha256": "a" * 64}}}, "requires a non-editable wheel"),
        ({"url": "file:///wheel/cmru.whl", "archive_info": {"hash": "md5=" + "a" * 64}}, "requires a non-editable wheel"),
        ({
            **_wheel_url(),
            "archive_info": {"hashes": {"sha256": "a" * 64}, "hash": "sha256=" + "b" * 64},
        }, "conflicting SHA-256 digests"),
    ],
)
def test_release_controller_refuses_unproven_installations(
    tmp_path, monkeypatch, direct_url, expected,
):
    installed = _installed_distribution(tmp_path, direct_url)
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)
    with pytest.raises(RuntimeError, match=expected):
        release_controller.release_controller_identity(installed.module_path)


def test_release_controller_refuses_missing_installed_file(tmp_path, monkeypatch):
    installed = _installed_distribution(tmp_path, _wheel_url())
    (tmp_path / "site-packages" / "cmru-6.2.0.dist-info" / "METADATA").unlink()
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)

    with pytest.raises(RuntimeError, match="installed CMRU file is missing"):
        release_controller.release_controller_identity(installed.module_path)


def test_release_controller_reports_unreadable_installed_file(
    tmp_path, monkeypatch,
):
    installed = _installed_distribution(tmp_path, _wheel_url())
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)
    original_open = Path.open

    def fail_module_open(path, *args, **kwargs):
        if path == installed.module_path:
            raise OSError("read denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_module_open)
    with pytest.raises(RuntimeError, match="cannot read installed CMRU file cmru/cli.py"):
        release_controller.release_controller_identity(installed.module_path)


@pytest.mark.release_controller_provenance
def test_release_launcher_records_controller_identity_before_transaction(
    monkeypatch, tmp_path,
):
    identity = {
        "version": "6.2.0.dev20261014+gabc1234",
        "wheel_sha256": "a" * 64,
        "distribution_sha256": "b" * 64,
        "module_path": "/site-packages/cmru/cli.py",
    }
    monkeypatch.setattr(release_controller, "release_controller_identity", lambda _path: identity)
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_a, **_kw: None)
    info = []
    errors = []
    monkeypatch.setattr(cli, "log_info", info.append)
    monkeypatch.setattr(cli, "log_debug", lambda _message: None)
    monkeypatch.setattr(cli, "log_error", errors.append)

    def stop_before_workspace(*_args):
        raise RuntimeError("stop after recording controller")

    monkeypatch.setattr(transaction, "source_git_root_for_projects", stop_before_workspace)
    vargs = SimpleNamespace(
        dry_run=False, resume="retained", target=None, allow_uncommitted=False,
    )
    with pytest.raises(SystemExit) as exited:
        cli._release_launcher(
            [], vargs, tmp_path / "cmru.orchestration.toml", tmp_path, {}, {}, [],
            ["demo"], ["demo"], None, None, github_config=None,
        )

    assert exited.value.code == 1
    assert any(
        "CMRU release controller: version=6.2.0.dev20261014+gabc1234" in message
        and "wheel-sha256=" + "a" * 64 in message
        and "installed-files-sha256=" + "b" * 64 in message
        for message in info
    )
    assert errors == ["stop after recording controller"]


@pytest.mark.release_controller_provenance
def test_release_launcher_refuses_unproven_controller_before_workspace(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        release_controller,
        "release_controller_identity",
        lambda _path: (_ for _ in ()).throw(RuntimeError("editable controller")),
    )
    errors = []
    monkeypatch.setattr(cli, "log_error", errors.append)
    workspace_calls = []
    monkeypatch.setattr(
        transaction, "source_git_root_for_projects",
        lambda *_args: workspace_calls.append(True),
    )
    vargs = SimpleNamespace(
        dry_run=False, resume="retained", target=None, allow_uncommitted=False,
    )
    with pytest.raises(SystemExit) as exited:
        cli._release_launcher(
            [], vargs, tmp_path / "cmru.orchestration.toml", tmp_path, {}, {}, [],
            ["demo"], ["demo"], None, None, github_config=None,
        )

    assert exited.value.code == 1
    assert errors == ["editable controller"]
    assert workspace_calls == []


def test_transaction_child_requires_launcher_controller_identity(monkeypatch, tmp_path):
    identity = {
        "version": "6.2.0",
        "wheel_sha256": "a" * 64,
        "distribution_sha256": "b" * 64,
        "module_path": "/site-packages/cmru/cli.py",
    }
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_VERSION_ENV, "6.2.0")
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_WHEEL_SHA256_ENV, "a" * 64)
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_FILES_SHA256_ENV, "b" * 64)
    monkeypatch.setattr(cli, "_record_release_controller_identity", lambda _phase: identity)

    assert cli._release_child_controller_identity(tmp_path, ["demo"]) == identity

    changed = {**identity, "distribution_sha256": "c" * 64}
    monkeypatch.setattr(cli, "_record_release_controller_identity", lambda _phase: changed)
    with pytest.raises(RuntimeError, match="changed between launcher and transaction"):
        cli._release_child_controller_identity(tmp_path, ["demo"])


def test_self_release_compares_installed_bootstrap_wheel_to_launcher(monkeypatch, tmp_path):
    identity = {
        "version": "6.2.0",
        "wheel_sha256": "a" * 64,
        "distribution_sha256": "b" * 64,
        "module_path": "/site-packages/cmru/cli.py",
    }
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_VERSION_ENV, "6.2.0")
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_WHEEL_SHA256_ENV, "a" * 64)
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_FILES_SHA256_ENV, "b" * 64)
    candidate_module = tmp_path / "cmru" / "src" / "cmru" / "cli.py"
    candidate_module.parent.mkdir(parents=True)
    candidate_module.write_text("# candidate controller\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(candidate_module))
    calls = []
    monkeypatch.setattr(
        cli, "_record_installed_release_controller_identity",
        lambda phase: calls.append(phase) or identity,
    )

    assert cli._release_child_controller_identity(tmp_path, ["cmru"]) == identity
    assert calls == ["transaction"]


@pytest.mark.release_controller_provenance
def test_release_child_accepts_candidate_source_loaded_by_transaction_pythonpath(
    monkeypatch, tmp_path,
):
    from cmru import version

    installed = _installed_distribution(tmp_path, _wheel_url())
    monkeypatch.setattr(release_controller, "distribution", lambda _name: installed)
    identity = release_controller.installed_release_controller_identity()
    monkeypatch.setenv(transaction.RELEASE_CONTROLLER_VERSION_ENV, identity["version"])
    monkeypatch.setenv(
        transaction.RELEASE_CONTROLLER_WHEEL_SHA256_ENV, identity["wheel_sha256"],
    )
    monkeypatch.setenv(
        transaction.RELEASE_CONTROLLER_FILES_SHA256_ENV,
        identity["distribution_sha256"],
    )
    candidate_source = tmp_path / "cmru" / "src"
    candidate_module = candidate_source / "cmru" / "cli.py"
    candidate_module.parent.mkdir(parents=True)
    candidate_module.write_text("# selected transaction source\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(candidate_module))
    monkeypatch.setenv("PYTHONPATH", str(candidate_source))

    class ReachedReleasePlan(Exception):
        pass

    def stop_at_release_plan(*_args, **_kwargs):
        raise ReachedReleasePlan

    monkeypatch.setattr(version, "detect_changed_projects", stop_at_release_plan)
    vargs = SimpleNamespace(
        dry_run=False,
        allow_tag_ahead_of_head=False,
        allow_stale_tool_deps=False,
    )

    with pytest.raises(ReachedReleasePlan):
        cli._release_child(
            vargs,
            tmp_path,
            configs={},
            ordered={"cmru": object()},
            project_order=["cmru"],
            selected_names=["cmru"],
            github_config=None,
            env_config={},
            git_auth=None,
        )
