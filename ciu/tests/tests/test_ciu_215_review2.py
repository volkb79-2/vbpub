"""CIU-215 delta review: N1 fail-closed lstat on every config candidate and the
modules tree, N2 destination-is-a-directory, N3 WARN precision."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ciu import config_model, engine, gitconfig_sanitize, worktree  # noqa: E402
from test_ciu_215_gitdir_mount import (  # noqa: E402,F401
    _write, hermetic_git, repos,
)

HOST = "[secret]\n\tmarker = HOSTDATA\n"


def _host(tmp_path: Path) -> Path:
    path = tmp_path / "host.cfg"
    path.write_text(HOST, encoding="utf-8")
    return path


def _planted_module_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "host-modules"
    (directory / "x").mkdir(parents=True)
    (directory / "x" / "HEAD").write_text("ref: x\n")
    (directory / "x" / "config").write_text(HOST)
    return directory


def _module(main: Path, name="lib") -> Path:
    module = main / ".git" / "modules" / name
    module.mkdir(parents=True)
    (module / "HEAD").write_text("ref: refs/heads/x\n")
    (module / "config").write_text("[a]\n\tb = c\n")
    return module


def _plant(case: str, main: Path, tmp_path: Path) -> Path:
    """Plant the hostile entry; returns the path the message must name."""
    common = main / ".git"
    host = _host(tmp_path)
    admin = common / "worktrees" / "wt"
    if case == "common-config":
        (common / "config").unlink()
        (common / "config").symlink_to(host)
        return common / "config"
    if case == "common-config.worktree":
        (common / "config.worktree").symlink_to(host)
        return common / "config.worktree"
    if case == "admin-config.worktree":
        (admin / "config.worktree").symlink_to(host)
        return admin / "config.worktree"
    if case == "modules-root":
        (common / "modules").symlink_to(_planted_module_dir(tmp_path))
        return common / "modules"
    if case == "modules-root-file":
        (common / "modules").write_text("file")
        return common / "modules"
    if case == "module-config":
        module = _module(main)
        (module / "config").unlink()
        (module / "config").symlink_to(host)
        return module / "config"
    if case == "module-config.worktree":
        module = _module(main)
        (module / "config.worktree").symlink_to(host)
        return module / "config.worktree"
    if case == "module-config-dir":
        module = _module(main)
        (module / "config").unlink()
        (module / "config").mkdir()
        return module / "config"
    if case == "symlinked-dir-inside-modules":
        module = _module(main)
        (module / "sub").symlink_to(_planted_module_dir(tmp_path))
        return module / "sub"
    if case == "sibling-config.worktree":
        sibling = common / "worktrees" / "sib"
        sibling.mkdir()
        (sibling / "config.worktree").symlink_to(host)
        return sibling / "config.worktree"
    raise AssertionError(case)


CASES = [
    "common-config", "common-config.worktree", "admin-config.worktree", "modules-root",
    "modules-root-file", "module-config", "module-config.worktree", "module-config-dir",
    "symlinked-dir-inside-modules", "sibling-config.worktree",
]


@pytest.mark.parametrize("mode", ["rw", "ro"])
@pytest.mark.parametrize("case", CASES)
def test_a_planted_entry_refuses_and_the_host_file_is_never_read(repos, tmp_path, case, mode):
    main, linked, _phys = repos
    named = _plant(case, main, tmp_path)
    stack = linked / "ciu" / "stack"
    with pytest.raises(ValueError, match=r"\[CIU-215\]") as exc:
        _write(repos, tmp_path, mode)
    assert str(named) in str(exc.value)
    assert not (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).exists()
    for copy in (stack / ".ciu" / "gitdir").glob("*"):
        if copy.is_file():
            assert "HOSTDATA" not in copy.read_text()


def test_a_swap_between_the_scan_and_the_read_is_caught(repos, tmp_path, monkeypatch):
    main, linked, _phys = repos
    module = _module(main)
    host = _host(tmp_path)
    original = gitconfig_sanitize.scan_modules

    def scan_then_swap(modules):
        found = original(modules)
        (module / "config").unlink()
        (module / "config").symlink_to(host)
        return found

    monkeypatch.setattr(gitconfig_sanitize, "scan_modules", scan_then_swap)
    with pytest.raises(ValueError, match="not a regular file"):
        _write(repos, tmp_path, "rw")
    stack = linked / "ciu" / "stack"
    assert not (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).exists()
    for copy in (stack / ".ciu" / "gitdir").glob("*.config"):
        assert "HOSTDATA" not in copy.read_text()


def test_an_unreadable_directory_inside_modules_refuses(repos, tmp_path):
    main, _l, _p = repos
    module = _module(main)
    locked = module / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.fail("the fixture cannot make a directory unreadable here")
        with pytest.raises(ValueError, match=r"cannot walk"):
            _write(repos, tmp_path, "rw")
    finally:
        locked.chmod(0o755)


def test_a_module_dir_is_scanned_and_clean_entries_still_work(repos, tmp_path):
    main, _l, _p = repos
    module = _module(main)
    (module / "hooks").mkdir()
    (module / "config.worktree").write_text("[a]\n\tb = c\n")
    path = _write(repos, tmp_path, "rw")[2]
    assert path is not None


def test_a_submodule_hooks_entry_that_is_a_file_refuses_but_absent_is_fine(repos, tmp_path):
    main, _l, _p = repos
    module = _module(main)
    assert _write(repos, tmp_path, "rw")[2] is not None  # no hooks/info: nothing to overlay
    (module / "hooks").write_text("not a directory")
    with pytest.raises(ValueError, match="submodule git hooks directory is unsafe"):
        _write(repos, tmp_path, "rw")


def test_scan_modules_directly_handles_absence_and_returns_dirs_and_configs(tmp_path):
    assert gitconfig_sanitize.scan_modules(tmp_path / "nope") == ([], [])
    module = tmp_path / "modules" / "lib"
    (module / "objects").mkdir(parents=True)
    (module / "objects" / "config").write_text("[skipped]\n")
    (module / "HEAD").write_text("ref: x\n")
    (module / "config").write_text("[a]\n")
    assert gitconfig_sanitize.scan_modules(tmp_path / "modules") == ([module], [module / "config"])
    assert gitconfig_sanitize.require_regular_config(module / "nothing") is False


# --- N2a: the destination became a directory ----------------------------------


def test_a_copy_destination_turned_into_a_directory_is_replaced(repos, tmp_path):
    stack = (repos[1] / "ciu" / "stack")
    state = stack / ".ciu" / "gitdir"
    (state / "0.config" / "inner").mkdir(parents=True)
    (state / "0.config" / "inner" / "file").write_text("x")
    _write(repos, tmp_path, "ro")
    assert (state / "0.config").is_file() and not (state / "0.config").is_symlink()


# --- N3: the checkout-path WARN is precise ------------------------------------


def _warns(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "[CIU-215] no selected service" in r.getMessage()]


def _overlay(repos, root: Path, volume: str, caplog) -> list[str]:
    _main, linked, _phys = repos
    stack = linked / "ciu" / "stack"
    stack.mkdir(parents=True, exist_ok=True)
    mount = worktree.git_common_dir_mount(root)
    compose = yaml.safe_dump({"services": {"a": {"volumes": [volume]}}})
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=engine.logger.name):
        engine.write_gitdir_overlay(stack, compose, config_model.LinkedWorktreeConfig("ro", None), mount)
    return _warns(caplog)


def test_a_descendant_mount_covers_only_when_the_ciu_root_is_below_the_git_top(repos, caplog):
    _main, linked, _phys = repos
    below = linked / "ciu"
    below.mkdir(exist_ok=True)
    assert _overlay(repos, below, f"/h:{linked}/ciu", caplog) == []
    assert _overlay(repos, linked, f"/h:{linked}/ciu", caplog) != []
    assert _overlay(repos, below, "/h:/elsewhere", caplog) != []


def test_a_volume_entry_without_a_usable_target_is_ignored(repos, tmp_path):
    compose = yaml.safe_dump({"services": {"a": {"volumes": [
        {"type": "volume", "source": "named"}, {"target": 5}]}}})
    assert _write(repos, tmp_path, "ro", compose=compose)[2] is not None


def test_an_unresolved_target_expression_does_not_warn(repos, caplog):
    _main, linked, _phys = repos
    assert _overlay(repos, linked, "/h:${CHECKOUT_PATH:-/x}", caplog) == []
    assert _overlay(repos, linked, "/h:/elsewhere", caplog) != []
