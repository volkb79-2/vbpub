"""CIU-215 -- opt-in git common-dir mount for services of a linked worktree.

Real git repositories (a primary checkout and a linked worktree) are built in
``tmp_path``; the daemon-namespace translation is faked by pointing
``workspace_env._detect_physical_repo_root`` at a different root. Nothing here
runs Docker.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import conftest as suite_conftest  # noqa: E402
from ciu import config_model, deploy, engine, workspace_env, worktree  # noqa: E402

COMPOSE = """\
services:
  runner:
    image: alpine:3
    volumes:
      - ${PHYSICAL_REPO_ROOT:?required host path}/x:/work
  other:
    image: alpine:3
"""


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=t@e.x", "-c", "user.name=T", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch):
    """Keep git hermetic and its automatic maintenance synchronous-free (N4, CIU-223)."""
    for key, value in (
        ("GIT_CONFIG_GLOBAL", "/dev/null"), ("GIT_CONFIG_NOSYSTEM", "1"),
        ("GIT_CONFIG_COUNT", "2"),
        ("GIT_CONFIG_KEY_0", "gc.auto"), ("GIT_CONFIG_VALUE_0", "0"),
        ("GIT_CONFIG_KEY_1", "maintenance.auto"), ("GIT_CONFIG_VALUE_1", "false"),
        ("GIT_AUTHOR_NAME", "T"), ("GIT_AUTHOR_EMAIL", "t@e.x"),
        ("GIT_COMMITTER_NAME", "T"), ("GIT_COMMITTER_EMAIL", "t@e.x"),
    ):
        monkeypatch.setenv(key, value)


def test_the_leftover_child_guard_names_a_child_and_its_cmdline(monkeypatch):
    """The CIU-223 guard reports a live direct child without signalling it."""
    before = set(suite_conftest._direct_children())
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; print('ready', flush=True); sys.stdin.readline()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"

        def forbidden_path_use(*_args, **_kwargs):
            raise AssertionError("the process guard must not use pathlib")

        monkeypatch.setattr(Path, "iterdir", forbidden_path_use)
        monkeypatch.setattr(Path, "read_text", forbidden_path_use)
        monkeypatch.setattr(Path, "read_bytes", forbidden_path_use)
        leaked = suite_conftest._leftover_child_descriptions(before)
        named = [item for item in leaked if item.startswith(f"pid {child.pid} ")]
        assert len(named) == 1
        assert "sys.stdin.readline()" in named[0]
    finally:
        assert child.stdin is not None
        try:
            child.stdin.write("\n")
        except BrokenPipeError:
            pass
        finally:
            child.stdin.close()
        child.wait(timeout=60)
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A primary checkout and a linked worktree whose daemon root differs."""
    main = (tmp_path / "main").resolve()
    main.mkdir()
    _git(["init", "-b", "main"], main)
    _git(["commit", "--allow-empty", "-m", "init"], main)
    _git(["remote", "add", "origin", "https://tok:secret@example.invalid/r.git"], main)
    linked = main / ".worktrees" / "wt"
    _git(["worktree", "add", "-b", "wt", str(linked)], main)
    phys = tmp_path / "phys"
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: phys)
    return main, linked, phys


def _table(mode="ro", services=None):
    return config_model.LinkedWorktreeConfig(mode, services)


def _write(repos, tmp_path, mode="ro", services=None, compose=COMPOSE):
    _main, linked, _phys = repos
    stack = linked / "ciu" / "stack"
    stack.mkdir(parents=True, exist_ok=True)
    mount = worktree.git_common_dir_mount(stack)
    path = engine.write_gitdir_overlay(stack, compose, _table(mode, services), mount)
    return stack, mount, path


def _binds(path, service="runner"):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {b["target"]: b for b in doc["services"][service]["volumes"]}


# --- derivation -------------------------------------------------------------


def test_mount_plan_derives_the_gitfile_names_and_translates_to_the_daemon(repos):
    main, linked, phys = repos
    mount = worktree.git_common_dir_mount(linked / "sub")  # a root below the git top
    assert mount.top == linked
    assert mount.gitfile == linked / ".git"
    assert mount.common == main / ".git"
    assert mount.admin == main / ".git" / "worktrees" / "wt"
    assert mount.physical(mount.common) == phys / ".git"
    assert worktree.is_linked_git_worktree(linked) is True
    assert worktree.is_linked_git_worktree(main) is False


def test_relative_gitfile_is_joined_lexically_against_the_checkout(repos):
    main, linked, _phys = repos
    (linked / ".git").write_text("gitdir: ../../.git/worktrees/wt\n", encoding="utf-8")
    assert worktree.git_common_dir_mount(linked).admin == main / ".git" / "worktrees" / "wt"


def test_a_commondir_that_disagrees_with_git_refuses_naming_both(
        repos, tmp_path, monkeypatch):
    _main, linked, _phys = repos
    other = tmp_path / "other"
    other.mkdir()
    for answer, text in ((str(other), str(other)), (None, "(no answer)")):
        def fake(cmd, answer=answer, **_kw):
            return SimpleNamespace(returncode=0 if answer else 1, stdout=answer or "")
        monkeypatch.setattr(worktree.procutil, "run_cmd", fake)
        with pytest.raises(worktree.WorktreeError) as exc:
            worktree.git_common_dir_mount(linked)
        assert text in str(exc.value) and "git reports" in str(exc.value)
        assert str(_main / ".git") in str(exc.value)


def _bypass_git(monkeypatch, linked, common):
    """Let the pointer checks run on files git itself would reject."""
    shared = worktree._shared_worktree()
    monkeypatch.setattr(shared, "discover_git_root", lambda _p: (linked, common))


def test_a_non_gitfile_or_unreadable_pointer_refuses(repos, monkeypatch):
    main, linked, _phys = repos
    _bypass_git(monkeypatch, linked, main / ".git")
    (linked / ".git").write_text("nonsense\n", encoding="utf-8")
    with pytest.raises(worktree.WorktreeError, match="not a linked-worktree gitfile"):
        worktree.git_common_dir_mount(linked)
    _bypass_git(monkeypatch, main, main / ".git")
    with pytest.raises(worktree.WorktreeError, match="cannot read gitfile"):
        worktree.git_common_dir_mount(main)  # a primary checkout's .git is a directory


def test_a_missing_commondir_file_refuses(repos, monkeypatch):
    main, linked, _phys = repos
    admin = Path((linked / ".git").read_text().split("gitdir:")[1].strip())
    (admin / "commondir").unlink()
    _bypass_git(monkeypatch, linked, main / ".git")
    with pytest.raises(worktree.WorktreeError, match="cannot read commondir file"):
        worktree.git_common_dir_mount(linked)


def test_a_path_outside_any_repository_refuses(repos):
    main, _linked, _phys = repos
    with pytest.raises(worktree.WorktreeError, match="cannot determine the git checkout"):
        worktree.git_common_dir_mount(main.parent)


# --- the ro layout ----------------------------------------------------------


def test_ro_mounts_the_common_dir_flat_with_sanitized_configs(repos, tmp_path):
    main, linked, phys = repos
    stack, mount, path = _write(repos, tmp_path, "ro")
    assert path == stack / ".ciu" / "ciu.compose.gitdir.yml"
    for service in ("runner", "other"):
        binds = _binds(path, service)
        common = binds[str(main / ".git")]
        assert common == {
            "type": "bind", "source": str(phys / ".git"), "target": str(main / ".git"),
            "read_only": True, "bind": {"create_host_path": False},
        }
        config = binds[str(main / ".git" / "config")]
        assert config["read_only"] is True
        assert config["source"].startswith(str(phys / ".worktrees" / "wt"))
        assert set(binds) == {
            str(main / ".git"), str(main / ".git" / "config"), str(linked / ".git")}
        gitfile = binds[str(linked / ".git")]
        assert gitfile["read_only"] is True
        assert gitfile["source"] == str(phys / ".worktrees" / "wt" / ".git")
        assert gitfile["bind"] == {"create_host_path": False}
    copy = stack / ".ciu" / "gitdir" / "0.config"
    assert "secret" not in copy.read_text() and "example.invalid" in copy.read_text()
    assert not (stack / ".ciu" / "gitdir" / "hooks-empty").exists()


# --- the rw layout ----------------------------------------------------------


def test_rw_applies_the_run_gate_layout(repos, tmp_path):
    main, linked, phys = repos
    common, admin = main / ".git", main / ".git" / "worktrees" / "wt"
    (admin / "config.worktree").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    sibling = common / "worktrees" / "sib"
    sibling.mkdir()
    (sibling / "config.worktree").write_text("[a]\n\tb = c\n", encoding="utf-8")
    module = common / "modules" / "lib"
    module.mkdir(parents=True)
    (module / "HEAD").write_text("ref: refs/heads/x\n")
    (module / "config").write_text("[remote \"o\"]\n\turl = https://t@h/r\n")
    (module / "objects").mkdir()
    (module / "objects" / "config").write_text("[skipped]\n")
    stack, mount, path = _write(repos, tmp_path, "rw")
    binds = _binds(path)
    assert binds[str(common)]["read_only"] is False
    assert binds[str(common / "worktrees")]["read_only"] is True
    assert binds[str(admin)]["read_only"] is False
    assert binds[str(common / "hooks")]["read_only"] is True
    assert binds[str(common / "hooks")]["source"] == str(
        phys / ".worktrees" / "wt" / "ciu" / "stack" / ".ciu" / "gitdir" / "hooks-empty")
    assert binds[str(common / "info")]["source"] == str(phys / ".git" / "info")
    for pointer in (linked / ".git", admin / "gitdir", admin / "commondir"):
        assert binds[str(pointer)]["read_only"] is True
    configs = {t for t in binds if t.endswith(("config", "config.worktree"))}
    assert configs == {
        str(common / "config"), str(admin / "config.worktree"),
        str(module / "config"), str(sibling / "config.worktree"),
    }
    assert not os.listdir(stack / ".ciu" / "gitdir" / "hooks-empty")
    for copy in (stack / ".ciu" / "gitdir").glob("*.config"):
        text = copy.read_text()
        assert "tok:" not in text and "t@h" not in text


def test_rw_refuses_unsafe_hooks_and_pointers_and_too_many_configs(repos, tmp_path):
    main, linked, _phys = repos
    common = main / ".git"
    hooks = common / "hooks"
    hooks.rename(common / "hooks.real")
    with pytest.raises(ValueError, match="hooks directory is missing or unsafe"):
        _write(repos, tmp_path, "rw")
    (common / "hooks.real").rename(hooks)
    admin = common / "worktrees" / "wt"
    (admin / "gitdir").rename(admin / "gitdir.real")
    (admin / "gitdir").symlink_to(admin / "gitdir.real")
    with pytest.raises(ValueError, match="pointer is not a regular file"):
        _write(repos, tmp_path, "rw")
    (admin / "gitdir").unlink()
    (admin / "gitdir.real").rename(admin / "gitdir")
    for number in range(engine_cap() + 1):
        sib = common / "worktrees" / f"s{number}"
        sib.mkdir()
        (sib / "config.worktree").write_text("[a]\n\tb = c\n")
    with pytest.raises(ValueError, match="cannot be sanitized safely"):
        _write(repos, tmp_path, "rw")


def engine_cap() -> int:
    from ciu.gitconfig_sanitize import MAX_GIT_OVERLAYS
    return MAX_GIT_OVERLAYS


def test_missing_config_and_unreadable_config_refuse(repos, tmp_path, monkeypatch):
    main, _linked, _phys = repos
    from ciu import gitconfig_sanitize

    def failing(cmd, **_kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: bad config\n")

    with monkeypatch.context() as patch:
        patch.setattr(gitconfig_sanitize, "procutil", SimpleNamespace(run_cmd=failing))
        with pytest.raises(ValueError, match="cannot read git config.*bad config"):
            _write(repos, tmp_path, "ro")
    (main / ".git" / "config").unlink()
    with pytest.raises(ValueError, match="is missing"):
        _write(repos, tmp_path, "ro")


def test_stale_copies_are_removed_and_rewrites_are_atomic(repos, tmp_path):
    stack, _mount, _path = _write(repos, tmp_path, "ro")
    stale = stack / ".ciu" / "gitdir" / "9.config"
    stale.write_text("old")
    _write(repos, tmp_path, "ro")
    assert not stale.exists()
    assert not list((stack / ".ciu" / "gitdir").glob("*.tmp"))


# --- selection and de-duplication ------------------------------------------


def test_services_narrows_and_an_unknown_name_refuses(repos, tmp_path):
    stack, _m, path = _write(repos, tmp_path, "ro", services=("other",))
    assert list(yaml.safe_load(path.read_text())["services"]) == ["other"]
    with pytest.raises(ValueError, match=r"'nope'.*not a service"):
        _write(repos, tmp_path, "ro", services=("nope",))


def test_no_services_in_the_compose_writes_nothing(repos, tmp_path):
    for text in ("{}\n", "services: {}\n", "- a\n", ""):
        assert _write(repos, tmp_path, "ro", compose=text)[2] is None


@pytest.mark.parametrize("volume,skipped", [
    ("${PHYSICAL_REPO_ROOT:?required host path - source ciu.env}/../../.git:@COMMON@", True),
    ("${A:-${B:-x}}/g:@COMMON@:ro", True),
    ("@COMMON@", True),
    ("/src:/elsewhere", False),
    ({"type": "bind", "source": "/s", "target": "@COMMON@"}, True),
])
def test_a_service_that_already_mounts_the_common_dir_is_skipped(
        repos, tmp_path, capsys, volume, skipped):
    main, _l, _p = repos
    common = str(main / ".git")
    if isinstance(volume, dict):
        volume = dict(volume, target=common)
    else:
        volume = volume.replace("@COMMON@", common)
    compose = yaml.safe_dump({"services": {
        "owner": {"image": "x", "volumes": [volume, 5, "relative-only"]}, "free": {"image": "x"}}})
    doc = yaml.safe_load(_write(repos, tmp_path, "ro", compose=compose)[2].read_text())
    assert ("owner" not in doc["services"]) is skipped
    assert "free" in doc["services"]
    assert ("service 'owner' already mounts" in capsys.readouterr().out) is skipped


def test_an_ancestor_target_and_root_cover_but_a_sibling_does_not(repos, tmp_path):
    main, _l, _p = repos
    compose = yaml.safe_dump({"services": {
        "anc": {"volumes": [f"/x:{main}"]}, "root": {"volumes": ["/x:/"]},
        "sib": {"volumes": [f"/x:{main}/.gitx"]}, "bare": {"volumes": 3},
        "strprefix": {"volumes": [f"/x:{str(main)[:-1]}"]}}})
    doc = yaml.safe_load(_write(repos, tmp_path, "ro", compose=compose)[2].read_text())
    assert sorted(doc["services"]) == ["bare", "sib", "strprefix"]


def test_every_service_covered_writes_nothing(repos, tmp_path):
    main, _l, _p = repos
    compose = yaml.safe_dump({"services": {"a": {"volumes": [f"/x:{main}/.git"]}}})
    assert _write(repos, tmp_path, "ro", compose=compose)[2] is None


# --- table validation -------------------------------------------------------


@pytest.mark.parametrize("table,message", [
    ("x", "must be a table"),
    ({"git_common_dir": "ro", "bogus": 1}, "unknown key"),
    ({}, "requires git_common_dir"),
    ({"git_common_dir": "flat"}, r"must be \"ro\" or \"rw\""),
    ({"git_common_dir": "ro", "services": "a"}, "non-empty list"),
    ({"git_common_dir": "ro", "services": []}, "non-empty list"),
    ({"git_common_dir": "ro", "services": [""]}, "non-empty list"),
])
def test_invalid_tables_refuse_with_the_ticket_tag(table, message):
    with pytest.raises(ValueError, match=r"\[CIU-215\].*" + message):
        config_model.validate_linked_worktree_table({"linked_worktree": table})


def test_valid_tables_normalize():
    check = config_model.validate_linked_worktree_table
    assert check({}) is None
    assert check({"linked_worktree": {"git_common_dir": "rw"}}) == _table("rw", None)
    assert check({"linked_worktree": {"git_common_dir": "ro", "services": ["a"]}}) == _table(
        "ro", ("a",))


@pytest.mark.parametrize("name", ["hooks", "info"])
def test_a_symlinked_hooks_or_info_dir_refuses(repos, tmp_path, name):
    main, _l, _p = repos
    real = main / ".git" / f"{name}.real"
    (main / ".git" / name).rename(real)
    (main / ".git" / name).symlink_to(real)
    with pytest.raises(ValueError, match=f"{name} directory is missing or unsafe"):
        _write(repos, tmp_path, "rw")


def test_exactly_the_cap_of_sibling_configs_is_accepted(repos, tmp_path):
    main, _l, _p = repos
    for number in range(engine_cap()):
        sib = main / ".git" / "worktrees" / f"s{number}"
        sib.mkdir()
        (sib / "config.worktree").write_text("[a]\n\tb = c\n")
    path = _write(repos, tmp_path, "ro")[2]
    assert len(_binds(path)) == 3 + engine_cap()


def test_a_vanished_pointer_file_refuses(repos, tmp_path):
    main, _l, _p = repos
    (main / ".git" / "worktrees" / "wt" / "gitdir").unlink()
    with pytest.raises(ValueError, match="pointer is not a regular file"):
        _write(repos, tmp_path, "rw")


def test_the_gitfile_is_bound_read_only_in_both_modes(repos, tmp_path):
    _main, linked, phys = repos
    for mode in ("ro", "rw"):
        binds = _binds(_write(repos, tmp_path, mode)[2])
        entry = binds[str(linked / ".git")]
        assert entry["read_only"] is True
        assert entry["source"] == str(phys / ".worktrees" / "wt" / ".git")


def test_an_existing_gitfile_bind_is_kept_but_an_ancestor_mount_still_gets_ours(
        repos, tmp_path):
    main, linked, _phys = repos
    gitfile = str(linked / ".git")
    compose = yaml.safe_dump({"services": {
        "own": {"volumes": [f"/h/.git:{gitfile}:rw"]},
        "ancestor": {"volumes": [f"/h:{linked}"]},
    }})
    doc = yaml.safe_load(_write(repos, tmp_path, "ro", compose=compose)[2].read_text())
    own = {v["target"] for v in doc["services"]["own"]["volumes"]}
    ancestor = {v["target"] for v in doc["services"]["ancestor"]["volumes"]}
    assert gitfile not in own and str(main / ".git") in own
    assert gitfile in ancestor


@pytest.mark.parametrize("mode", ["ro", "rw"])
def test_a_gitfile_that_is_not_a_regular_file_refuses(repos, tmp_path, mode):
    main, linked, _phys = repos
    plan = worktree.GitMountPlan(
        repo_root=linked, top=linked, gitfile=tmp_path / "no-such-gitfile",
        admin=main / ".git" / "worktrees" / "wt", common=main / ".git")
    with pytest.raises(ValueError, match="pointer is not a regular file"):
        engine._gitdir_mount_specs(plan, mode, tmp_path / "state")
