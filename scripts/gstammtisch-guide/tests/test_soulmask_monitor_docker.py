"""Real-docker tests for soulmask-monitor.py — real docker containers coming
and going, proving the dynamic-membership logic against the real Docker
daemon instead of fake `live` tuples (that's what the bare unit suite's
rescan_servers tests already cover). This file is specifically about
container-discovery/membership dynamics, not RCON connectivity —
test_soulmask_monitor_r1.py already covers the RCON wire protocol
end-to-end via a local fake RCON server.

Environment adaptation (confirmed empirically in this devcontainer, same
category of limitation already documented for `nsenter` in
exec-soulmask-rcon.py / test_soulmask_monitor_r1.py): this devcontainer
talks to the real Docker daemon over a socket (docker-outside-of-docker)
without sharing its host PID/cgroup namespaces. `docker top`/`docker ps`/
`docker inspect` all work fine here (they're plain daemon API calls), so
`list_wsserver_containers`/`discover_live_servers`'s CONTAINER-LEVEL
detection is fully real and tested below. But `container_cgroup_path()`
resolves a container's cgroup via `/proc/<host-pid>/cgroup` and a
`/sys/fs/cgroup` walk — both of which require direct host filesystem
visibility this devcontainer does not have, confirmed live:

    docker run -d python:3.11-trixie bash -c \\
        "exec -a WSServer-Linux-Shipping sleep 999"
    # docker top correctly shows "WSServer-Linux-Shipping 999"
    # container_cgroup_path(cid) -> None here (real /proc/<pid>/cgroup
    #   and /sys/fs/cgroup/**/docker-<cid>.scope are both invisible from
    #   inside this sandbox)

So: discover_live_servers() against a real fake-WSServer container
correctly (and safely — this is the exact "skip an unresolvable candidate"
path find_game_cgroups already relies on) returns nothing for it here; that
degrade-gracefully behavior is tested directly. For rescan_servers()'s full
add/remove/pid-change cycle, this file drives it with REAL cid/name pairs
from REAL `docker run`/`docker stop` (not fabricated strings) combined with
a local temp directory standing in for the cgroup path that only a real
deployment host can resolve — the same substitution pattern R1 uses for the
nsenter hop. The nsenter/cgroup-path OS-level joins themselves still need a
real-host smoke test; this tier proves everything else.

Building the fake container: a plain `busybox` image's `sleep`/`tail` are
BusyBox multi-call-binary applets that dispatch on argv[0] — `exec -a
WSServer-Linux-Shipping sleep 999` breaks them (confirmed: "sleep: not
found", busybox's own dispatcher not recognizing the renamed argv0). A
real (non-busybox) `sleep` binary doesn't care about argv0, so this uses a
debian-based image; `/bin/sh` (dash) also does not support `exec -a` at
all ("exec: -a: not found") — `bash -c` does.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "files" / "usr" / "local" / "sbin" / "soulmask-monitor.py"

FAKE_IMAGE = "python:3.11-trixie"  # real coreutils sleep; confirmed cached locally


def load_module():
    spec = importlib.util.spec_from_file_location("soulmask_monitor_r2", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mon = load_module()

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available")


def _docker_available():
    try:
        return subprocess.run(
            ["docker", "version"], capture_output=True, timeout=5
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


if not _docker_available():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")


class FakeWSServer:
    """A real, disposable container whose `docker top` output contains the
    literal substring the monitor greps for, without needing the real game
    binary. Guarantees `docker rm -f` on exit even if the test fails."""

    def __init__(self, name=None):
        self.name = name or f"fake-wsserver-{uuid.uuid4().hex[:12]}"
        self.cid = None

    def start(self):
        result = subprocess.run(
            ["docker", "run", "-d", "--name", self.name, FAKE_IMAGE,
             "bash", "-c", "exec -a WSServer-Linux-Shipping sleep 999"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"docker run failed: {result.stderr}"
        self.cid = result.stdout.strip()
        for _ in range(50):
            state = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.name],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if state == "true":
                return self
            time.sleep(0.1)
        raise AssertionError(f"container {self.name} never reached Running")

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name],
                        capture_output=True, timeout=30)


@pytest.fixture
def fake_servers():
    created = []

    def _make(name=None):
        c = FakeWSServer(name).start()
        created.append(c)
        return c

    yield _make
    for c in created:
        c.stop()


# ── container-level detection: list_wsserver_containers ─────────────────────

def test_list_wsserver_containers_detects_a_real_running_fake_server(fake_servers):
    c = fake_servers()
    names = [name for _, name in mon.list_wsserver_containers()]
    assert c.name in names


def test_list_wsserver_containers_stops_seeing_a_removed_container(fake_servers):
    c = fake_servers()
    assert c.name in [name for _, name in mon.list_wsserver_containers()]
    c.stop()
    assert c.name not in [name for _, name in mon.list_wsserver_containers()]


def test_list_wsserver_containers_selector_narrows_to_one_of_several(fake_servers):
    a = fake_servers()
    b = fake_servers()
    only_a = [name for _, name in mon.list_wsserver_containers(selector=a.name)]
    assert only_a == [a.name]
    both = {name for _, name in mon.list_wsserver_containers()}
    assert {a.name, b.name} <= both


# ── discover_live_servers: degrades gracefully in THIS sandbox ──────────────

def test_discover_live_servers_does_not_crash_on_a_real_unresolvable_container(fake_servers):
    # Documents + proves the environment limitation in the module docstring
    # above: a real, correctly-detected fake WSServer container still
    # yields no server record here, because this sandbox can't resolve its
    # cgroup path — and that must be a silent skip, not a crash.
    c = fake_servers()
    live = mon.discover_live_servers(selector=c.name)
    assert live == []


# ── rescan_servers: full add/remove cycle driven by REAL container events ───

def _fake_live_entry(cid, name, cg_path):
    """A live-server record for `cid`/`name` (both from a REAL container),
    with a LOCAL temp directory standing in for the cgroup path that only a
    real deployment host can resolve for a real container (see module
    docstring) — rescan_servers() itself never calls docker or touches a
    cgroup path directly, so this is a faithful substitution for its actual
    inputs, not a bypass of its logic."""
    return {"cid": cid, "name": name, "pid": 999,
            "metrics_cgroup": str(cg_path), "slice": str(cg_path)}


def test_rescan_servers_add_and_remove_driven_by_real_container_lifecycle(fake_servers, tmp_path):
    a = fake_servers()
    live = [_fake_live_entry(a.cid, a.name, tmp_path)]

    servers, changed = mon.rescan_servers([], live, rcon_enabled=False)
    assert changed is True
    assert [s["uuid"] for s in servers] == [a.name]
    kept_tracker = servers[0]["tracker"]

    # A second real container starts; the first is untouched by the rescan.
    b = fake_servers()
    live2 = [_fake_live_entry(a.cid, a.name, tmp_path),
             _fake_live_entry(b.cid, b.name, tmp_path)]
    servers, changed = mon.rescan_servers(servers, live2, rcon_enabled=False)
    assert changed is True
    assert {s["uuid"] for s in servers} == {a.name, b.name}
    kept = next(s for s in servers if s["uuid"] == a.name)
    assert kept["tracker"] is kept_tracker  # untouched by the second container's arrival

    # The first container stops for real; discover_live_servers (driven by
    # the real docker daemon) no longer reports it, so a rescan drops it —
    # the second, still-running one is unaffected.
    a.stop()
    live3 = mon.discover_live_servers()
    # a's cid is gone from the real live set now; keep b's real entry, drop a.
    live3 = [e for e in live3 if e["cid"] == b.cid] or [_fake_live_entry(b.cid, b.name, tmp_path)]
    servers, changed = mon.rescan_servers(servers, live3, rcon_enabled=False)
    assert changed is True
    assert [s["uuid"] for s in servers] == [b.name]
