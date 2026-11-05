from __future__ import annotations

import csv
import io
import os
import shutil
import stat
from types import SimpleNamespace

import pytest
from cowtree import overlay
from cowtree.cache import Cache
from cowtree.errors import BackendFailed, KeyMissing, PathRefused, Unrepairable

KEY = "a" * 16
KEY2 = "b" * 16


def _build(tree):
    os.makedirs(os.path.join(tree, "pkg"))
    for name, text in (("pkg/mod.py", "x = 1\n"), ("top.txt", "top\n")):
        with open(os.path.join(tree, name), "w", encoding="utf-8") as handle:
            handle.write(text)
    os.chmod(os.path.join(tree, "top.txt"), 0o640)
    os.utime(os.path.join(tree, "top.txt"), ns=(5_000_000_000, 7_000_000_000))
    os.symlink("pkg/mod.py", os.path.join(tree, "link"))


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "cache"
    upper = tmp_path / "upper"
    root.mkdir(mode=0o770)
    upper.mkdir(mode=0o770)
    cache = Cache(str(root), require_tmpfs=False)
    cache.ensure(KEY, _build)
    cache.ensure(KEY2, _build)
    return SimpleNamespace(cache=cache, upper=str(upper), tmp=tmp_path)


def test_overlay_volume_options_and_argv():
    volume = overlay.overlay_volume("rg-tslot-t-w0", "/c/k/tree", "/u/t/w0/u", "/u/t/w0/w")
    assert volume.options == (
        "lowerdir=/c/k/tree,upperdir=/u/t/w0/u,workdir=/u/t/w0/w,redirect_dir=on,xino=auto"
    )
    assert volume.create_argv() == [
        "docker", "volume", "create", "--driver", "local", "--opt", "type=overlay", "--opt", "device=overlay",
        "--opt", "o=" + volume.options, "rg-tslot-t-w0",
    ]
    assert volume.remove_argv("podman") == ["podman", "volume", "rm", "rg-tslot-t-w0"]
    argv = volume.mount_argv("/work/w0")
    assert argv[0] == "--mount"
    fields = next(csv.reader(io.StringIO(argv[1])))
    assert fields == [
        "type=volume", "src=rg-tslot-t-w0", "dst=/work/w0", "volume-driver=local", "volume-opt=type=overlay",
        "volume-opt=device=overlay", "volume-opt=o=" + volume.options,
    ]
    assert "userxattr" not in volume.options


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "bad name"},
        {"lower": "/c/k,upperdir=/etc"},
        {"upper": "/u:/v"},
        {"work": "relative"},
        {"work": '/w"x'},
        {"redirect_dir": "userxattr"},
        {"xino": "yes"},
    ],
)
def test_overlay_volume_refuses_injection(kwargs):
    with pytest.raises(PathRefused):
        overlay.overlay_volume(
            kwargs.get("name", "v"), kwargs.get("lower", "/l"), kwargs.get("upper", "/u"), kwargs.get("work", "/w"),
            redirect_dir=kwargs.get("redirect_dir", "on"), xino=kwargs.get("xino", "auto"),
        )


def test_mount_arg_refuses_bad_destination():
    volume = overlay.overlay_volume("v", "/l", "/u", "/w", redirect_dir="nofollow", xino="off")
    assert "redirect_dir=nofollow,xino=off" in volume.options
    with pytest.raises(PathRefused):
        volume.mount_arg("/work,evil")


def test_create_worker_layout_marker_and_volume(env):
    dirs = overlay.create_worker(env.upper, "tok1", 0, env.cache, KEY)
    assert os.path.isdir(dirs.upper) and os.path.isdir(dirs.work)
    lower = env.cache.tree_path(KEY)
    assert stat.S_IMODE(os.stat(dirs.upper).st_mode) == stat.S_IMODE(os.stat(lower).st_mode)
    assert os.listdir(dirs.upper) == [] and os.listdir(dirs.work) == []
    volume = overlay.worker_volume(dirs, env.cache, KEY, "rg-tslot-tok1-w0", xino="off")
    assert volume.lowerdir == lower and volume.upperdir == dirs.upper and volume.xino == "off"
    assert overlay.reuse_worker(env.upper, "tok1", 0, KEY) == dirs
    with pytest.raises(PathRefused, match="different lower"):
        overlay.reuse_worker(env.upper, "tok1", 0, KEY2)
    with pytest.raises(PathRefused, match="already exists"):
        overlay.create_worker(env.upper, "tok1", 0, env.cache, KEY)


def test_create_worker_requires_published_key_and_valid_index(env):
    with pytest.raises(KeyMissing):
        overlay.create_worker(env.upper, "tok", 0, env.cache, "c" * 16)
    for bad in (-1, True, "0"):
        with pytest.raises(PathRefused):
            overlay.worker_dirs(env.upper, "tok", bad)
    with pytest.raises(PathRefused):
        overlay.worker_dirs(env.upper, "../x", 0)


def test_reuse_worker_without_marker_or_with_bad_marker(env):
    with pytest.raises(PathRefused, match="no readable marker"):
        overlay.reuse_worker(env.upper, "none", 0, KEY)
    dirs = overlay.create_worker(env.upper, "tok", 0, env.cache, KEY)
    with open(dirs.marker, "w") as handle:
        handle.write("[]")
    with pytest.raises(PathRefused, match="different lower"):
        overlay.reuse_worker(env.upper, "tok", 0, KEY)
    with open(dirs.marker, "w") as handle:
        handle.write("{")
    with pytest.raises(PathRefused, match="no readable marker"):
        overlay.reuse_worker(env.upper, "tok", 0, KEY)


def test_match_owner_calls_chown_only_when_owner_differs(env):
    calls = []
    other = SimpleNamespace(st_uid=os.getuid() + 1, st_gid=os.getgid())
    overlay._match_owner(env.upper, other, lambda *a: calls.append(a))
    assert calls == [(env.upper, os.getuid() + 1, os.getgid())]
    calls.clear()
    overlay._match_owner(env.upper, os.stat(env.upper), lambda *a: calls.append(a))
    assert calls == []

    def refuse(*_a):
        raise PermissionError(1, "no")

    with pytest.raises(BackendFailed, match="chown"):
        overlay._match_owner(env.upper, other, refuse)


def test_create_worker_uses_injected_chown(env, monkeypatch):
    seen = []
    monkeypatch.setattr(overlay.os, "stat", lambda path: SimpleNamespace(
        st_uid=os.getuid() + 7, st_gid=os.getgid(), st_mode=0o40750) if path.endswith("/tree") else os.lstat(path))
    overlay.create_worker(env.upper, "tokc", 0, env.cache, KEY, chown=lambda *a: seen.append(a))
    assert seen and seen[0][1] == os.getuid() + 7


def test_remove_worker_and_token_and_reset(env):
    dirs = overlay.create_worker(env.upper, "tok", 0, env.cache, KEY)
    overlay.create_worker(env.upper, "tok", 1, env.cache, KEY)
    open(os.path.join(dirs.upper, "dirty"), "w").close()
    assert overlay.reset_worker(env.upper, "tok", 0, env.cache, KEY2) == dirs
    assert os.listdir(dirs.upper) == []
    assert overlay.reuse_worker(env.upper, "tok", 0, KEY2) == dirs
    overlay.remove_worker(env.upper, "nope", 0)
    overlay.remove_token(env.upper, "tok")
    assert not os.path.exists(os.path.join(env.upper, "tok"))
    overlay.remove_token(env.upper, "tok")  # already gone


def test_remove_worker_refuses_symlink_and_remove_token_non_empty(env):
    target = env.tmp / "victim"
    target.mkdir()
    (target / "keep").write_text("x")
    os.makedirs(os.path.join(env.upper, "tok"))
    os.symlink(str(target), os.path.join(env.upper, "tok", "w0"))
    with pytest.raises(PathRefused, match="symlink"):
        overlay.remove_worker(env.upper, "tok", 0)
    assert (target / "keep").exists()
    os.unlink(os.path.join(env.upper, "tok", "w0"))
    (env.tmp / "upper" / "tok" / "stray").write_text("x")
    with pytest.raises(BackendFailed, match="not empty"):
        overlay.remove_token(env.upper, "tok")
    os.unlink(os.path.join(env.upper, "tok", "stray"))
    link = os.path.join(env.upper, "linktok")
    os.symlink(str(target), link)
    overlay.remove_token(env.upper, "linktok")
    assert (target / "keep").exists()


def test_remove_worker_permission_fallback_runs_helper_once(env, monkeypatch):
    dirs = overlay.create_worker(env.upper, "tok", 0, env.cache, KEY)
    real = overlay._force_rmtree
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "root-owned work dir")
        real(path)

    monkeypatch.setattr(overlay, "_force_rmtree", flaky)
    argvs = []
    overlay.remove_worker(env.upper, "tok", 0, runner=lambda argv: argvs.append(list(argv)) or 0, image="img:1")
    assert argvs == [[
        "docker", "run", "--rm", "--mount", f"type=bind,src={dirs.base},dst=/x", "img:1", "rm", "-rf", "/x/u", "/x/w",
    ]]
    assert calls["n"] == 2 and not os.path.exists(dirs.base)


def test_remove_worker_fallback_failures(env, monkeypatch):
    overlay.create_worker(env.upper, "tok", 0, env.cache, KEY)

    def denied(path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(overlay, "_force_rmtree", denied)
    with pytest.raises(BackendFailed, match="no cleanup image"):
        overlay.remove_worker(env.upper, "tok", 0)
    with pytest.raises(BackendFailed, match="exited 3"):
        overlay.remove_worker(env.upper, "tok", 0, runner=lambda argv: 3, image="img")
    with pytest.raises(BackendFailed, match="denied"):
        overlay.remove_worker(env.upper, "tok", 0, runner=lambda argv: 0, image="img")


def test_run_argv_returns_status():
    assert overlay.run_argv(["true"]) == 0
    assert overlay.run_argv(["false"]) == 1


def test_usage_fraction_and_threshold(monkeypatch):
    monkeypatch.setattr(overlay.os, "statvfs", lambda path: SimpleNamespace(f_bavail=10, f_blocks=100))
    assert overlay.usage_fraction("/x") == pytest.approx(0.9)
    assert overlay.upper_full("/x") and not overlay.upper_full("/x", 0.95)
    monkeypatch.setattr(overlay.os, "statvfs", lambda path: SimpleNamespace(f_bavail=0, f_blocks=0))
    assert overlay.usage_fraction("/x") == 1.0


@pytest.fixture
def trees(tmp_path):
    template = tmp_path / "tpl"
    merged = tmp_path / "mrg"
    for base in (template, merged):
        (base / "pkg").mkdir(parents=True)
        (base / "pkg" / "mod.py").write_text("x = 1\n")
    (template / "top.txt").write_text("top\n")
    os.chmod(template / "top.txt", 0o640)
    os.utime(template / "top.txt", ns=(5_000_000_000, 7_000_000_000))
    os.symlink("pkg/mod.py", template / "link")
    return str(template), str(merged)


def test_repair_entry_file_states(trees):
    template, merged = trees
    assert overlay.repair_entry(template, merged, "top.txt") == "restored"
    info = os.stat(os.path.join(merged, "top.txt"))
    assert stat.S_IMODE(info.st_mode) == 0o640 and info.st_mtime_ns == 7_000_000_000
    assert overlay.repair_entry(template, merged, "top.txt") == "unchanged"
    with open(os.path.join(merged, "top.txt"), "w") as handle:
        handle.write("TOP\n")  # same size, different bytes, mtime now differs
    assert overlay.repair_entry(template, merged, "top.txt") == "restored"
    with open(os.path.join(merged, "top.txt"), "r+") as handle:
        handle.write("TOP\n")
    os.utime(os.path.join(merged, "top.txt"), ns=(5_000_000_000, 7_000_000_000))
    assert overlay.repair_entry(template, merged, "top.txt") == "restored"  # same stat, different bytes
    assert open(os.path.join(merged, "top.txt")).read() == "top\n"
    assert not [name for name in os.listdir(merged) if name.startswith(".cowtree-repair")]


def test_repair_entry_replaces_type_changes_and_removes_additions(trees):
    template, merged = trees
    os.mkdir(os.path.join(merged, "link-target"))
    open(os.path.join(merged, "link"), "w").close()  # a file where the template has a symlink
    assert overlay.repair_entry(template, merged, "link") == "restored"
    assert os.readlink(os.path.join(merged, "link")) == "pkg/mod.py"
    assert overlay.repair_entry(template, merged, "link") == "unchanged"
    os.unlink(os.path.join(merged, "link"))
    os.symlink("elsewhere", os.path.join(merged, "link"))
    assert overlay.repair_entry(template, merged, "link") == "restored"
    open(os.path.join(merged, "added.txt"), "w").close()
    assert overlay.repair_entry(template, merged, "added.txt") == "removed"
    assert overlay.repair_entry(template, merged, "added.txt") == "absent"
    os.symlink("top.txt", os.path.join(merged, "top.txt"))  # symlink where the template has a file
    assert overlay.repair_entry(template, merged, "top.txt") == "restored"
    assert not os.path.islink(os.path.join(merged, "top.txt"))


def test_repair_entry_unrepairable_cases(trees):
    template, merged = trees
    with pytest.raises(Unrepairable, match="directory"):
        overlay.repair_entry(template, merged, "pkg")
    os.mkdir(os.path.join(merged, "newdir"))
    with pytest.raises(Unrepairable, match="directory"):
        overlay.repair_entry(template, merged, "newdir")
    shutil.rmtree(os.path.join(merged, "pkg"))
    with pytest.raises(Unrepairable, match="parent"):
        overlay.repair_entry(template, merged, "pkg/mod.py")
    with pytest.raises(Unrepairable, match="parent"):
        overlay._replace(os.path.join(merged, "pkg", "z"), lambda path: os.symlink("a", path))


@pytest.mark.parametrize("rel", ["", "/abs", "../up", "a//b", "a/./b", "a/..", "n\x00ul"])
def test_repair_entry_refuses_bad_relative_paths(trees, rel):
    with pytest.raises(PathRefused):
        overlay.repair_entry(*trees, rel)
