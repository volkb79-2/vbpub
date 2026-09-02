#!/usr/bin/env python3
"""mdt host-setup — reactive dev-tier container cap watcher.

Everything mdt-apply-dev-caps.sh's periodic sweep does for per-container
caps, it does up to SWEEP_INTERVAL late: a container created right after a
sweep runs unbounded until the next one. This watches dev-interactive.slice
and dev-background.slice directly via inotify and applies MemoryMax the
moment a new docker-*.scope appears — proven live (2026-08-28) to fire
within the same second a container is created, using nothing but a
read-only inotify watch on cgroupfs (no Docker API, no plugin, no proxy).

Default MemoryMax exists to bound the blast radius of any single
unlabelled dev/test/build container: without it, one container ballooning
under memory pressure can force reclaim on every OTHER cgroup sharing the
tier (including, transitively, anything memory.low/min protection on a
sibling tier is supposed to shield) before the tier's own MemoryHigh/Max
ever triggers. A caller's own explicit `--memory` always wins — this only
fills in containers that didn't ask for anything.

Cannot fix cgroup-parent placement itself (create-time only, see
CGROUP-NOTES.md #1) — this only reacts to attributes WITHIN a cgroup
Docker already placed correctly. Keep mdt-apply-dev-caps.sh's periodic
sweep running too: it is the backstop for whatever this watcher misses
across its own restart window, and it is still the only mechanism for the
IO caps this script does not touch.
"""
import ctypes
import ctypes.util
import fnmatch
import os
import re
import select
import struct
import subprocess
import sys
import time

CG = os.environ.get("CG", "/sys/fs/cgroup")
CONF = os.environ.get("CONF", "/etc/mdt/host-setup.env")
log_prefix = "[mdt-dev-cap-watcher]"


def log(msg: str) -> None:
    print(f"{log_prefix} {msg}", flush=True)


def load_env(path: str) -> dict:
    """Minimal KEY=VALUE parser — same shell-env shape mdt-apply-dev-caps.sh
    reads, so one file stays the single source of truth for both."""
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_env = load_env(CONF)
DEV_CAP_MEMORY_MAX = os.environ.get(
    "DEV_CAP_MEMORY_MAX", _env.get("DEV_CAP_MEMORY_MAX", "1G")
)
WATCHED_SLICES = ["dev-interactive.slice", "dev-background.slice"]

# Additional cgroup properties worth capping on creation, left commented —
# uncomment and set a value (in host-setup.env, then export it below, or
# hardcode here) to also apply them. Not enabled by default: MemoryMax
# alone is the one the operator asked for; these are documented, not
# guessed at.
#
# DEV_CAP_MEMORY_SWAP_MAX = os.environ.get("DEV_CAP_MEMORY_SWAP_MAX", _env.get("DEV_CAP_MEMORY_SWAP_MAX", ""))
# DEV_CAP_CPU_WEIGHT      = os.environ.get("DEV_CAP_CPU_WEIGHT",      _env.get("DEV_CAP_CPU_WEIGHT", ""))
# DEV_CAP_IO_WEIGHT       = os.environ.get("DEV_CAP_IO_WEIGHT",       _env.get("DEV_CAP_IO_WEIGHT", ""))
# DEV_CAP_TASKS_MAX       = os.environ.get("DEV_CAP_TASKS_MAX",       _env.get("DEV_CAP_TASKS_MAX", ""))

IN_CREATE = 0x00000100
IN_ISDIR = 0x40000000
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
WATCH_MASK = IN_CREATE | IN_ISDIR | IN_DELETE_SELF | IN_MOVE_SELF

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


def check_inotify_available() -> None:
    """Fail loudly and immediately if this kernel can't do inotify at all,
    rather than let a confusing errno surface later on the real watch."""
    fd = _libc.inotify_init1(0)
    if fd < 0:
        errno = ctypes.get_errno()
        log(f"FATAL: inotify_init1 failed (errno={errno}) — this kernel/"
            f"container cannot use inotify; the reactive watcher cannot "
            f"run. mdt-apply-dev-caps.sh's periodic sweep still applies IO "
            f"caps on its own schedule, but per-container MemoryMax will "
            f"only ever be as fresh as that sweep without this.")
        sys.exit(1)
    os.close(fd)


def inotify_add_watch(fd: int, path: str, mask: int) -> int:
    wd = _libc.inotify_add_watch(fd, path.encode(), mask)
    if wd < 0:
        raise OSError(ctypes.get_errno(), f"inotify_add_watch({path!r}) failed")
    return wd


def resolve_container_id(scope_name: str) -> str | None:
    m = re.fullmatch(r"docker-([0-9a-f]{12,64})\.scope", scope_name)
    return m.group(1) if m else None


def has_explicit_memory_limit(container_id: str) -> bool:
    """A container created with its own --memory (cmru's tester-gate
    pattern, e.g.) must keep it — this watcher only fills in containers
    that asked for nothing."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.HostConfig.Memory}}", container_id],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if out.returncode != 0:
        return False
    try:
        return int(out.stdout.strip()) != 0
    except ValueError:
        return False


def apply_default_cap(slice_path: str, scope_name: str) -> None:
    container_id = resolve_container_id(scope_name)
    if container_id is None:
        return
    if has_explicit_memory_limit(container_id):
        log(f"{scope_name}: explicit --memory already set, leaving as-is")
        return
    unit = scope_name
    props = [f"MemoryMax={DEV_CAP_MEMORY_MAX}"]
    # Uncomment corresponding lines above and here to also apply them:
    # if DEV_CAP_MEMORY_SWAP_MAX:
    #     props.append(f"MemorySwapMax={DEV_CAP_MEMORY_SWAP_MAX}")
    # if DEV_CAP_CPU_WEIGHT:
    #     props.append(f"CPUWeight={DEV_CAP_CPU_WEIGHT}")
    # if DEV_CAP_IO_WEIGHT:
    #     props.append(f"IOWeight={DEV_CAP_IO_WEIGHT}")
    # if DEV_CAP_TASKS_MAX:
    #     props.append(f"TasksMax={DEV_CAP_TASKS_MAX}")
    result = subprocess.run(
        ["systemctl", "set-property", "--runtime", unit, *props],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        log(f"{scope_name}: applied default {', '.join(props)}")
    else:
        log(f"WARN: {scope_name}: set-property failed: "
            f"{result.stderr.strip() or result.returncode}")


def watch_slice(fd: int, slice_name: str) -> dict:
    """(re)installs a watch for one slice, tolerating it not existing yet
    (e.g. dev-interactive.slice before the first devcontainer starts)."""
    path = os.path.join(CG, "dev.slice", slice_name)
    if not os.path.isdir(path):
        return {}
    try:
        wd = inotify_add_watch(fd, path, WATCH_MASK)
    except OSError as exc:
        log(f"WARN: could not watch {path}: {exc}")
        return {}
    log(f"watching {path} (wd={wd})")
    return {wd: (path, slice_name)}


def main() -> None:
    check_inotify_available()
    fd = _libc.inotify_init1(0)
    if fd < 0:
        log(f"FATAL: inotify_init1 failed unexpectedly (errno={ctypes.get_errno()})")
        sys.exit(1)

    watches: dict[int, tuple[str, str]] = {}
    for slice_name in WATCHED_SLICES:
        watches.update(watch_slice(fd, slice_name))

    log(f"default MemoryMax={DEV_CAP_MEMORY_MAX}; watched: {WATCHED_SLICES}")

    last_retry = 0.0
    while True:
        # Retry any slice that didn't exist yet (dev-interactive.slice
        # activates only once the first devcontainer starts) roughly once
        # a minute, without a busy loop.
        now = time.monotonic()
        if now - last_retry > 60:
            for slice_name in WATCHED_SLICES:
                if slice_name not in {v[1] for v in watches.values()}:
                    watches.update(watch_slice(fd, slice_name))
            last_retry = now

        ready, _, _ = select.select([fd], [], [], 60)
        if fd not in ready:
            continue
        data = os.read(fd, 64 * 1024)
        pos = 0
        while pos < len(data):
            wd, mask, _cookie, length = struct.unpack_from("iIII", data, pos)
            pos += 16
            name = data[pos:pos + length].rstrip(b"\0").decode(errors="replace")
            pos += length
            if wd not in watches:
                continue
            slice_path, slice_name = watches[wd]
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                log(f"WARN: {slice_path} watch invalidated (deleted/moved) — "
                    f"will retry")
                del watches[wd]
                continue
            if not (mask & IN_CREATE and mask & IN_ISDIR):
                continue
            if not fnmatch.fnmatch(name, "docker-*.scope"):
                continue
            apply_default_cap(slice_path, name)


if __name__ == "__main__":
    main()
