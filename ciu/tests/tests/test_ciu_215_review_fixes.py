"""CIU-215 review fixes F1-F7: lstat discipline, limits, modules, git answers,
stale fragments, `$` escaping and one real end-to-end prepare_stack."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ciu import engine, worktree  # noqa: E402
from test_ciu_215_gitdir_mount import COMPOSE, _binds, _git, _write, repos  # noqa: E402,F401
from test_ciu_215_wiring import TABLE, _repo  # noqa: E402


def _stack(repos):
    _main, linked, _phys = repos
    stack = linked / "ciu" / "stack"
    stack.mkdir(parents=True, exist_ok=True)
    return stack


def _sentinel(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "sentinel"
    marker.write_text("keep", encoding="utf-8")
    return marker


# --- F1: nothing is written or deleted through a planted link ----------------


def test_a_symlinked_gitdir_state_dir_refuses_and_the_target_is_untouched(repos, tmp_path):
    stack = _stack(repos)
    victim = tmp_path / "victim"
    marker = _sentinel(victim)
    (victim / "9.config").write_text("host file")
    (victim / "hooks-empty").mkdir()
    (stack / ".ciu").mkdir()
    (stack / ".ciu" / "gitdir").symlink_to(victim)
    for mode in ("ro", "rw"):
        with pytest.raises(ValueError, match=r"\[CIU-215\].*real directory owned"):
            _write(repos, tmp_path, mode)
    assert marker.read_text() == "keep"
    assert sorted(p.name for p in victim.iterdir()) == ["9.config", "hooks-empty", "sentinel"]


def test_a_symlinked_machine_dir_refuses_and_the_target_is_untouched(repos, tmp_path):
    stack = _stack(repos)
    victim = tmp_path / "victim-ciu"
    marker = _sentinel(victim)
    (stack / ".ciu").symlink_to(victim)
    with pytest.raises(ValueError, match=r"\[CIU-215\].*real directory owned"):
        _write(repos, tmp_path, "rw")
    assert [p.name for p in victim.iterdir()] == ["sentinel"] and marker.read_text() == "keep"


def test_a_foreign_owned_state_dir_refuses(repos, tmp_path, monkeypatch):
    (_stack(repos) / ".ciu" / "gitdir").mkdir(parents=True)
    other = os.getuid() + 1
    monkeypatch.setattr(engine.os, "getuid", lambda: other)
    with pytest.raises(ValueError, match="real directory owned by the current user"):
        _write(repos, tmp_path, "ro")


def test_a_planted_tmp_symlink_is_not_written_through(repos, tmp_path):
    stack = _stack(repos)
    victim = tmp_path / "host-file"
    victim.write_text("host secret")
    state = stack / ".ciu" / "gitdir"
    state.mkdir(parents=True)
    (state / "0.config.tmp").symlink_to(victim)
    (stack / ".ciu" / "ciu.compose.gitdir.yml.tmp").symlink_to(victim)
    _write(repos, tmp_path, "ro")
    assert victim.read_text() == "host secret"
    copy = state / "0.config"
    assert copy.is_file() and not copy.is_symlink() and oct(copy.stat().st_mode & 0o777) == "0o644"
    assert not (stack / ".ciu" / "ciu.compose.gitdir.yml").is_symlink()


def test_a_fragment_that_is_a_symlink_is_replaced_not_followed(repos, tmp_path):
    stack = _stack(repos)
    victim = tmp_path / "host-file"
    victim.write_text("host secret")
    (stack / ".ciu").mkdir()
    (stack / ".ciu" / "ciu.compose.gitdir.yml").symlink_to(victim)
    path = _write(repos, tmp_path, "ro")[2]
    assert victim.read_text() == "host secret" and not path.is_symlink()


@pytest.mark.parametrize("kind", ["file", "symlink", "dir-with-content"])
def test_hooks_empty_is_recreated_without_following_anything(repos, tmp_path, kind):
    stack = _stack(repos)
    state = stack / ".ciu" / "gitdir"
    state.mkdir(parents=True)
    hooks = state / "hooks-empty"
    victim = tmp_path / "victim-hooks"
    marker = _sentinel(victim)
    if kind == "file":
        hooks.write_text("x")
    elif kind == "symlink":
        hooks.symlink_to(victim)
    else:
        hooks.mkdir()
        (hooks / "planted").write_text("hook")
    _write(repos, tmp_path, "rw")
    assert hooks.is_dir() and not hooks.is_symlink() and list(hooks.iterdir()) == []
    assert marker.read_text() == "keep"


def test_stale_copy_cleanup_only_removes_regular_numbered_config_files(repos, tmp_path):
    stack = _stack(repos)
    state = stack / ".ciu" / "gitdir"
    state.mkdir(parents=True)
    victim = tmp_path / "target.config"
    victim.write_text("host")
    (state / "7.config").symlink_to(victim)
    (state / "notes.config").write_text("mine")
    (state / "8.config").write_text("stale")
    _write(repos, tmp_path, "ro")
    assert victim.read_text() == "host"
    assert (state / "notes.config").read_text() == "mine"
    assert not (state / "8.config").exists()
    assert (state / "7.config").is_symlink()


# --- F2: the gitfile bind protects only a checkout mounted at its own path ----


def test_a_warning_names_the_limit_when_no_service_mounts_the_checkout_path(
        repos, tmp_path, caplog):
    _main, linked, _phys = repos
    _stack(repos)
    with caplog.at_level(logging.WARNING, logger=engine.logger.name):
        _write(repos, tmp_path, "ro")
    assert [r.getMessage() for r in caplog.records if "[CIU-215]" in r.getMessage()
            and str(linked) in r.getMessage()]
    caplog.clear()
    covered = yaml.safe_dump({"services": {"a": {"volumes": [f"/h:{linked}"]}}})
    with caplog.at_level(logging.WARNING, logger=engine.logger.name):
        _write(repos, tmp_path, "ro", compose=covered)
    assert not [r for r in caplog.records if "[CIU-215]" in r.getMessage()]


# --- F3: rw protects submodule hooks and info too ----------------------------


def _module(main: Path, name: str, hooks=True, info=True) -> Path:
    module = main / ".git" / "modules" / name
    module.mkdir(parents=True)
    (module / "HEAD").write_text("ref: refs/heads/x\n")
    (module / "config").write_text("[a]\n\tb = c\n")
    for flag, folder in ((hooks, "hooks"), (info, "info")):
        if flag:
            (module / folder).mkdir()
    return module


def test_rw_overlays_submodule_hooks_and_info_but_ro_needs_nothing(repos, tmp_path):
    main, _l, phys = repos
    module = _module(main, "lib")
    _module(main, "bare", hooks=False, info=False)
    stack, _mount, path = _write(repos, tmp_path, "rw")
    binds = _binds(path)
    empty = binds[str(main / ".git" / "hooks")]["source"]
    assert binds[str(module / "hooks")] == dict(
        binds[str(module / "hooks")], source=empty, read_only=True)
    assert binds[str(module / "info")]["source"] == str(phys / ".git" / "modules" / "lib" / "info")
    assert binds[str(module / "info")]["read_only"] is True
    assert str(main / ".git" / "modules" / "bare" / "hooks") not in binds
    assert str(module / "hooks") not in _binds(_write(repos, tmp_path, "ro")[2])


def test_a_symlinked_submodule_hooks_dir_refuses(repos, tmp_path):
    main, _l, _p = repos
    module = _module(main, "lib", hooks=False)
    (module / "hooks").symlink_to(main / ".git" / "info")
    with pytest.raises(ValueError, match="submodule git hooks directory is unsafe"):
        _write(repos, tmp_path, "rw")


def test_too_many_submodule_git_dirs_refuse_and_the_cap_itself_passes(repos, tmp_path):
    main, _l, _p = repos
    from ciu.gitconfig_sanitize import MAX_GIT_OVERLAYS
    for number in range(MAX_GIT_OVERLAYS):
        module = main / ".git" / "modules" / f"m{number}"
        module.mkdir(parents=True)
        (module / "HEAD").write_text("ref: x\n")
    assert _write(repos, tmp_path, "rw")[2] is not None
    extra = main / ".git" / "modules" / "one-more"
    extra.mkdir()
    (extra / "HEAD").write_text("ref: x\n")
    with pytest.raises(ValueError, match="submodule git directories"):
        _write(repos, tmp_path, "rw")


# --- F4: an opted-in stack never silently loses the mount ---------------------


@pytest.mark.parametrize("outcome,message", [
    (OSError("no git"), "git is not runnable"),
    (SimpleNamespace(returncode=129, stdout="", stderr="error: unknown option `path-format'"),
     "unknown option"),
    (SimpleNamespace(returncode=128, stdout="", stderr="fatal: detected dubious ownership"),
     "dubious ownership"),
    (SimpleNamespace(returncode=0, stdout="/only/one\n", stderr=""), "no output"),
])
def test_git_that_cannot_answer_refuses_naming_the_cause(monkeypatch, tmp_path, outcome, message):
    def fake(cmd, **_kw):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(worktree.procutil, "run_cmd", fake)
    with pytest.raises(worktree.WorktreeError, match=r"\[CIU-215\].*" + message):
        worktree.require_git_answers(tmp_path)


def test_a_clean_answer_passes_for_primary_and_linked_checkouts(repos):
    main, linked, _p = repos
    assert worktree.require_git_answers(main) is None
    assert worktree.require_git_answers(linked) is None


def test_the_engine_refuses_when_git_cannot_answer_for_an_opted_in_stack(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch)

    def cannot(_root):
        raise worktree.WorktreeError("[CIU-215] cannot tell")

    monkeypatch.setattr(worktree, "require_git_answers", cannot)
    with pytest.raises(ValueError, match="cannot tell"):
        engine.prepare_stack(working_dir=stack, define_root=tmp_path, skip_hostdir_check=True)


# --- F5: no stale fragment survives -------------------------------------------


def test_nothing_to_mount_deletes_a_stale_fragment_and_state(repos, tmp_path):
    main, _l, _p = repos
    stack, _mount, path = _write(repos, tmp_path, "ro")
    assert path.exists() and (stack / ".ciu" / "gitdir").is_dir()
    covered = yaml.safe_dump({"services": {"a": {"volumes": [f"/x:{main}/.git"]}}})
    assert _write(repos, tmp_path, "ro", compose=covered)[2] is None
    assert not path.exists() and not (stack / ".ciu" / "gitdir").exists()
    _write(repos, tmp_path, "ro")
    assert _write(repos, tmp_path, "ro", compose="{}\n")[2] is None
    assert not path.exists()


def test_remove_handles_a_fragment_without_a_state_dir(repos):
    stack = _stack(repos)
    (stack / ".ciu").mkdir()
    (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).write_text("services: {}\n")
    assert engine.remove_gitdir_artifacts(stack) == [stack / ".ciu" / engine.GITDIR_OVERLAY_NAME]


def test_remove_refuses_a_planted_link_or_directory_and_keeps_the_target(repos, tmp_path):
    stack = _stack(repos)
    assert engine.remove_gitdir_artifacts(stack) == []
    victim = tmp_path / "victim"
    marker = _sentinel(victim)
    (stack / ".ciu").mkdir()
    (stack / ".ciu" / "gitdir").symlink_to(victim)
    with pytest.raises(ValueError, match="real directory owned"):
        engine.remove_gitdir_artifacts(stack)
    assert marker.read_text() == "keep"
    (stack / ".ciu" / "gitdir").unlink()
    (stack / ".ciu" / "ciu.compose.gitdir.yml").mkdir()
    with pytest.raises(ValueError, match="must not be a directory"):
        engine.remove_gitdir_artifacts(stack)


def test_a_removed_table_deletes_the_old_fragment_at_render(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch, table="")
    machine = stack / ".ciu"
    machine.mkdir(exist_ok=True)
    (machine / engine.GITDIR_OVERLAY_NAME).write_text("services: {}\n")
    (machine / "gitdir").mkdir()
    engine.prepare_stack(working_dir=stack, define_root=tmp_path, skip_hostdir_check=True)
    assert not (machine / engine.GITDIR_OVERLAY_NAME).exists()
    assert not (machine / "gitdir").exists()


# --- F6: `$` is escaped for compose interpolation ----------------------------


def test_dollar_signs_in_paths_are_escaped_for_compose():
    bind = engine._gitdir_bind(Path("/p/$HOME/${x}"), Path("/t/$y"), True)
    assert bind["source"] == "/p/$$HOME/$${x}" and bind["target"] == "/t/$$y"
    assert engine._compose_escape(Path("/plain")) == "/plain"


# --- F7: one real end-to-end prepare_stack in a real linked worktree ----------


@pytest.mark.parametrize("mode", ["ro", "rw"])
def test_prepare_stack_in_a_real_linked_worktree_renders_the_fragment(
        tmp_path, monkeypatch, mode):
    stack = _repo(tmp_path, monkeypatch, table=TABLE.replace('"ro"', f'"{mode}"'))
    linked = tmp_path / ".worktrees" / "wt"
    subprocess.run(["git", "worktree", "add", "-b", "wt", str(linked)], cwd=tmp_path,
                   capture_output=True, check=True)
    for key in ("REPO_ROOT", "PHYSICAL_REPO_ROOT"):
        monkeypatch.setenv(key, str(linked))
    linked_stack = linked / "applications" / "demo"
    prepared = engine.prepare_stack(
        working_dir=linked_stack, define_root=linked, skip_hostdir_check=True)
    assert prepared.file_args[-2:] == ["-f", f".ciu/{engine.GITDIR_OVERLAY_NAME}"]
    fragment = linked_stack / ".ciu" / engine.GITDIR_OVERLAY_NAME
    binds = _binds(fragment, "demo")
    common = tmp_path / ".git"
    assert binds[str(common)]["read_only"] is (mode == "ro")
    assert binds[str(linked / ".git")]["read_only"] is True
    assert (str(common / "worktrees") in binds) is (mode == "rw")
    assert binds[str(common / "config")]["read_only"] is True
    for entry in binds.values():
        assert entry["bind"] == {"create_host_path": False}
    assert yaml.safe_load(fragment.read_text())["services"].keys() == {"demo"}
    assert not (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).exists()
