"""Public module-entrypoint contracts for CMRU command modules."""
from __future__ import annotations

import runpy

import pytest
from cmru import bundle, handlers, runner


def test_bundle_module_entrypoint_builds_minimal_archive(tmp_path, monkeypatch):
    config = tmp_path / "bundle.toml"
    config.write_text(
        'project_root = "."\n'
        'dist_dir = "dist"\n'
        'bundle_dir = "bundle"\n'
        'client_dir = "client"\n'
        '[wheel]\n'
        'enabled = false\n'
        '[archive]\n'
        'name_template = "bundle-{version}.tar.xz"\n'
        'version_env = "VERSION"\n'
        'format = "xztar"\n'
        '[copy]\n'
        'files = []\n'
        'dirs = []\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    monkeypatch.setattr("sys.argv", ["cmru.bundle", "--config", str(config)])
    runpy.run_path(bundle.__file__, run_name="__main__")
    assert (tmp_path / "dist" / "bundle-1.2.3.tar.xz").is_file()


def test_runner_module_entrypoint_reports_missing_project_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["cmru.runner", "--config", str(tmp_path / "missing.toml"), "--step", "run"],
    )
    with pytest.raises(SystemExit):
        runpy.run_path(runner.__file__, run_name="__main__")


def test_handlers_module_entrypoint_refuses_unconfigured_wheel_builder(tmp_path, monkeypatch):
    monkeypatch.delenv("CMRU_WHEEL_BUILDER_IMAGE", raising=False)
    monkeypatch.setattr("sys.argv", ["cmru.handlers", "wheel-build", "--cwd", str(tmp_path)])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(handlers.__file__, run_name="__main__")
    assert raised.value.code == 3
