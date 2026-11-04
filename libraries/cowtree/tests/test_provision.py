from __future__ import annotations

import os
import stat

import pytest
from cowtree import adapters, provision
from cowtree.errors import BackendFailed, KeyMissing, PathRefused
from cowtree.provision import LevelUnavailable, Probes, Provisioner, Settings, decide

KEY = "a" * 16


def _build(tree):
    os.makedirs(os.path.join(tree, "pkg"))
    with open(os.path.join(tree, "pkg", "mod.py"), "w") as handle:
        handle.write("x = 1\n")
    os.symlink("pkg/mod.py", os.path.join(tree, "link"))


def _dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir(mode=0o770)
    return str(path)


def _probes(**overrides):
    base = dict(socket_usable=lambda p: True, can_mount=lambda: True, writable_dir=lambda p: True,
                is_tmpfs=lambda p: True, reflink=lambda p: True)
    base.update(overrides)
    return Probes(**base)


class FakeRunner:
    def __init__(self, status=0):
        self.calls = []
        self.status = status

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.status


def test_decide_auto_walks_levels_and_reports(tmp_path):
    full = Settings(shared_root="/s", upper_root="/u", docker_socket="/d.sock", tmpfs_dir="/t", clone_dir="/c", copy_dir="/p")
    assert decide(full, _probes()).level == "A"
    decision = decide(full, _probes(socket_usable=lambda p: False))
    assert decision.level == "B" and decision.checks["A"] == "docker socket is not usable"
    assert decision.report() == {"level": "B", "requested": "auto", "checks": decision.checks}
    assert decide(full, _probes(socket_usable=lambda p: False, is_tmpfs=lambda p: False)).level == "C"
    assert decide(full, _probes(socket_usable=lambda p: False, is_tmpfs=lambda p: False, reflink=lambda p: False)).level == "D"


def test_decide_reason_strings():
    probes = _probes()
    cases = [
        (Settings(), "shared_root and upper_root are not configured", "A"),
        (Settings(shared_root="/s", upper_root="/u"), "docker_socket is not configured", "A"),
        (Settings(shared_root="/s", upper_root="/u", overlay_via="x"), "overlay_via must be", "A"),
        (Settings(), "tmpfs_dir is not configured", "B"),
        (Settings(), "clone_dir is not configured", "C"),
        (Settings(), "copy_dir is not configured", "D"),
    ]
    for settings, text, level in cases:
        assert text in provision._CHECKS[level](settings, probes)
    nowrite = _probes(writable_dir=lambda p: False)
    assert "shared_root is not a writable" in provision._check_a(Settings(shared_root="/s", upper_root="/u"), nowrite)
    assert "upper_root is not a writable" in provision._check_a(
        Settings(shared_root="/s", upper_root="/u"), _probes(writable_dir=lambda p: p == "/s"))
    assert "tmpfs_dir is not a writable" in provision._check_b(Settings(tmpfs_dir="/t"), nowrite)
    assert "not on a tmpfs" in provision._check_b(Settings(tmpfs_dir="/t"), _probes(is_tmpfs=lambda p: False))
    assert "clone_dir is not a writable" in provision._check_c(Settings(clone_dir="/c"), nowrite)
    assert "reflink" in provision._check_c(Settings(clone_dir="/c"), _probes(reflink=lambda p: False))
    assert "copy_dir is not a writable" in provision._check_d(Settings(copy_dir="/p"), nowrite)
    mount_only = Settings(shared_root="/s", upper_root="/u", overlay_via="mount")
    assert provision._check_a(mount_only, probes) == "ok"
    assert "CAP_SYS_ADMIN" in provision._check_a(mount_only, _probes(can_mount=lambda: False))


def test_decide_pinned_level_is_never_replaced():
    settings = Settings(level="A", copy_dir="/p")
    with pytest.raises(LevelUnavailable, match="A: shared_root"):
        decide(settings, _probes())
    assert decide(Settings(level="D", copy_dir="/p"), _probes()).level == "D"
    with pytest.raises(PathRefused):
        decide(Settings(level="Z"))
    with pytest.raises(LevelUnavailable, match="no usable level"):
        decide(Settings(), _probes())


def test_real_probes_on_this_host(tmp_path):
    real = Probes()
    assert real.writable_dir(str(tmp_path)) and not real.writable_dir(str(tmp_path / "none"))
    assert real.is_tmpfs("/dev/shm") in (True, False)
    assert real.can_mount() in (True, False)
    assert real.reflink(str(tmp_path)) in (True, False)
    assert provision._socket_usable(str(tmp_path / "none")) is False
    assert provision._socket_usable(str(tmp_path)) is False


def test_can_mount_reads_capeff(tmp_path, monkeypatch):
    import builtins
    real_open = builtins.open
    for text, expected in (("CapEff:\t0000000000200000\n", True), ("CapEff:\t0000000000000000\n", False), ("Name: x\n", False)):
        status = tmp_path / "status"
        status.write_text(text)
        monkeypatch.setattr(builtins, "open", lambda path, *a, **k: real_open(status if path == "/proc/self/status" else path, *a, **k))
        assert provision._can_mount() is expected
        monkeypatch.setattr(builtins, "open", real_open)


def test_unix_socket_is_usable(tmp_path):
    import socket
    path = str(tmp_path / "d.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    try:
        assert provision._socket_usable(path) is True
    finally:
        server.close()


def test_is_tmpfs_uses_mountinfo(tmp_path):
    info = f"1 0 8:1 / / rw - ext4 /dev/sda rw\n2 1 0:5 / {os.path.realpath(tmp_path)} rw - tmpfs tmpfs rw\n"
    assert provision._is_tmpfs(str(tmp_path), lambda: info) is True
    assert provision._is_tmpfs(str(tmp_path), lambda: "1 0 8:1 / / rw - ext4 /dev/sda rw\n") is False
    assert provision._read_mountinfo()


def _fake_clone(monkeypatch, supported=True):
    def ioctl(dst, request, src):
        if not supported:
            raise OSError(95, "not supported")
        os.write(dst, os.pread(src, 1 << 24, 0))

    monkeypatch.setattr(provision.fcntl, "ioctl", ioctl)


def test_reflink_probe_and_clone_file(tmp_path, monkeypatch):
    _fake_clone(monkeypatch, True)
    assert provision._reflink_supported(str(tmp_path)) is True
    assert os.listdir(tmp_path) == []
    _fake_clone(monkeypatch, False)
    assert provision._reflink_supported(str(tmp_path)) is False
    with pytest.raises(BackendFailed, match="reflink"):
        provision._clone_file(__file__, str(tmp_path / "x"))


@pytest.mark.parametrize("level", ["B", "C", "D"])
def test_levels_b_c_d_give_identical_trees(tmp_path, monkeypatch, level):
    _fake_clone(monkeypatch)
    directory = _dir(tmp_path, "base")
    settings = Settings(level=level, tmpfs_dir=directory, clone_dir=directory, copy_dir=directory)
    prov = Provisioner(settings, probes=_probes())
    assert prov.level == level and prov.decision.report()["level"] == level
    published = prov.publish(KEY, _build, owner="lane")
    published.lease.release()
    with prov.lease(KEY, "w") as lease:
        assert lease.key == KEY
    slot = prov.worker("tok", 0, KEY)
    assert prov.verify_worker(slot)
    with open(os.path.join(slot.path, "pkg", "mod.py"), "a") as handle:
        handle.write("changed\n")
    assert not prov.verify_worker(slot)
    assert os.readlink(os.path.join(slot.path, "link")) == "pkg/mod.py"
    with pytest.raises(PathRefused, match="already exists"):
        prov.worker("tok", 0, KEY)
    with pytest.raises(PathRefused, match="not a docker overlay"):
        slot.mount_argv("/work")
    other = prov.worker("tok", 1, KEY)
    prov.release_worker(slot)
    assert not os.path.exists(slot.path)
    prov.cleanup("tok")
    assert not os.path.exists(os.path.dirname(os.path.dirname(other.path)))
    assert prov.gc(0)[0].status == "removed"


def test_worker_requires_published_key(tmp_path):
    prov = Provisioner(Settings(level="D", copy_dir=_dir(tmp_path, "p")), probes=_probes())
    with pytest.raises(KeyMissing):
        prov.worker("tok", 0, KEY)


def test_level_a_docker_mode_uses_socket_and_fake_runner(tmp_path):
    runner = FakeRunner()
    settings = Settings(level="A", shared_root=_dir(tmp_path, "s"), upper_root=_dir(tmp_path, "u"),
                        docker_socket="/run/custom.sock")
    prov = Provisioner(settings, probes=_probes(), runner=runner, cleanup_image="img")
    prov.publish(KEY, _build)
    slot = prov.worker("tok", 0, KEY)
    assert slot.path is None and slot.docker == ["docker", "--host", "unix:///run/custom.sock"]
    argv = slot.mount_argv("/work/w0")
    assert argv[0] == "--mount" and "volume-opt=o=lowerdir=" in argv[1] and "redirect_dir=on" in argv[1]
    assert prov.verify_worker(slot) is False  # an overlay is observed through the container, not on the host
    prov.release_worker(slot)
    assert runner.calls == [["docker", "--host", "unix:///run/custom.sock", "volume", "rm", "-f", "cowtree-slot-tok-w0"]]
    prov.worker("tok", 1, KEY)
    prov.cleanup("tok")
    assert runner.calls[-1][-2:] == ["rm", "-f"] or "volume" in runner.calls[-1]
    assert not os.path.exists(os.path.join(settings.upper_root, "tok"))


def test_level_a_mount_mode(tmp_path):
    runner = FakeRunner()
    target = _dir(tmp_path, "mnt")
    settings = Settings(level="A", shared_root=_dir(tmp_path, "s"), upper_root=_dir(tmp_path, "u"), overlay_via="mount")
    prov = Provisioner(settings, probes=_probes(), runner=runner)
    assert prov._docker == ["docker"]
    prov.publish(KEY, _build)
    with pytest.raises(PathRefused, match="needs a target"):
        prov.worker("tok", 0, KEY)
    os.rmdir(os.path.join(settings.upper_root, "tok", "w0", "u"))  # leftover from the refused attempt
    prov.cleanup("tok")
    slot = prov.worker("tok", 1, KEY, target=target)
    assert slot.path == target and runner.calls[0][:5] == ["mount", "-t", "overlay", "overlay", "-o"]
    with pytest.raises(PathRefused, match="not a docker overlay"):
        slot.mount_argv("/w")
    prov.release_worker(slot)
    assert runner.calls[1] == ["umount", target]
    prov.cleanup("tok")


def test_runner_failure_is_typed(tmp_path):
    runner = FakeRunner(status=32)
    settings = Settings(level="A", shared_root=_dir(tmp_path, "s"), upper_root=_dir(tmp_path, "u"), overlay_via="mount")
    prov = Provisioner(settings, probes=_probes(), runner=runner)
    prov.publish(KEY, _build)
    with pytest.raises(BackendFailed, match="mount exited 32"):
        prov.worker("tok", 0, KEY, target=_dir(tmp_path, "mnt"))


def test_adapter_translates_host_policy_and_library_ignores_env(monkeypatch):
    settings = adapters.mdt_settings({"MDT_TREE_CACHE_ROOT": "/srv/tree-cache", "MDT_TREE_UPPER_ROOT": "/srv/tree-upper"},
                                     tmpfs_dir="/dev/shm")
    assert (settings.shared_root, settings.upper_root, settings.docker_socket, settings.tmpfs_dir) == (
        "/srv/tree-cache", "/srv/tree-upper", "/var/run/docker.sock", "/dev/shm")
    assert adapters.mdt_settings({"MDT_TREE_DOCKER_SOCKET": "/x.sock"}).docker_socket == "/x.sock"
    assert adapters.mdt_settings({}).shared_root is None
    monkeypatch.setenv("MDT_TREE_CACHE_ROOT", "/from/env")
    assert adapters.mdt_settings().shared_root == "/from/env"
    assert Settings().shared_root is None  # the library default never consults the environment


def test_library_modules_read_no_vbpub_env_names():
    import pathlib
    for name in ("cache", "overlay", "provision"):
        text = (pathlib.Path(provision.__file__).parent / f"{name}.py").read_text()
        assert "MDT_" not in text and "os.environ" not in text and "getenv" not in text and "dev-cache" not in text
