#!/usr/bin/env python3
"""mdt host-setup — reactive dev-tier container cap watcher.

Everything mdt-apply-dev-caps.sh's periodic sweep does for per-container
caps, it does up to SWEEP_INTERVAL late: a container created right after a
sweep runs unbounded until the next one. This watches dev-interactive.slice,
dev-background.slice AND dev-gates.slice directly via inotify and applies a
per-slice default MemoryMax/MemoryHigh/MemorySwapMax the moment a new
docker-*.scope appears — proven live (2026-08-28) to fire within the same
second a container is created, using nothing but a read-only inotify watch
on cgroupfs (no Docker API, no plugin, no proxy).

Default MemoryMax exists to bound the blast radius of any single
unlabelled dev/test/build/gate container: without it, one container
ballooning under memory pressure can force reclaim on every OTHER cgroup
sharing the tier (including, transitively, anything memory.low/min
protection on a sibling tier is supposed to shield) before the tier's own
MemoryHigh/Max ever triggers. A caller's own explicit `--memory` always
wins — this only fills in containers that didn't ask for anything.

MemoryHigh is the same soft-throttle-before-the-hard-cap pairing the slices
themselves already use, computed as a percentage of whichever Max applies
(WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT, default 80%) so it tracks gates'
different Max automatically. MemorySwapMax is what makes this a real
per-consumer "tight ceiling, generous swap" pair rather than MemoryMax
alone: cgroup v2 has no anon-only cap, so bounding total resident while
allowing swap absorbs anon growth beyond the cap rather than OOM-killing at
the boundary. Computed from the matched slice's own LIVE memory.swap.max
(cgroupfs), not re-derived from host-setup.env — see
read_slice_swap_max_bytes()'s own docstring for why.

dev-gates.slice (RG-55 D-19, RW-32/D1) gets its OWN knob,
WATCHER_PER_CONTAINER_GATES_MEMORY_MAX, defaulting to the tier's own
MemoryHigh (4G) — NOT the shared WATCHER_PER_CONTAINER_MEMORY_MAX (1G):
today's run-gate lane containers
land in dev-background.slice with no --memory of their own (this watcher's
whole reason to exist), and the moment a consumer honours
CGROUP_PARENT_DEV_GATES those same unlabelled containers move to
dev-gates.slice. Capping them at 1G there would be BELOW the ~2 GiB of
headroom the 2026-08-04 incident says a gate already needed — re-creating,
inside the very tier built to fix it, the problem dev-gates.slice exists to
solve. 4G lets one unlabelled lane alone drive the tier into MemoryHigh
throttle but never past MemoryMax (6G); two such lanes are bounded by the
tier's own oomd pressure kill, not by this watcher.

This is the COARSE, tier-wide backstop. It COMPOSES with, and is NOT
withdrawn by, the daemon's future per-lane placement caps (D-20/D-25,
cgroup-profiler/run-gate packages): placement gives an exact per-lane
memory.high/max on a leaf under dev-gates.slice for lanes that ask for one;
this watcher still catches whatever a lane did NOT ask for, exactly as it
already does for the other two tiers today.

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


def _get(name: str, default: str = "") -> str:
    if name in os.environ:
        return os.environ[name]
    return _env.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = _get(name, str(default)).strip()
    if not raw:
        log(f"WARN: {name} is empty -- using default {default}")
        return default
    try:
        return int(raw)
    except ValueError:
        log(f"WARN: {name}={raw!r} is not an integer -- using default {default}")
        return default


def _get_pct(name: str, default: int) -> int:
    value = _get_int(name, default)
    if not 1 <= value <= 100:
        log(f"WARN: {name}={value} is outside 1-100 -- using default {default}")
        return default
    return value


# Per-container defaults, namespaced separately from the DEV_* slice-level
# settings (see host-setup.env.example).
WATCHER_PER_CONTAINER_MEMORY_MAX = _get("WATCHER_PER_CONTAINER_MEMORY_MAX", "1G")
# dev-gates.slice's own knob (RW-32/D1) -- deliberately NOT
# WATCHER_PER_CONTAINER_MEMORY_MAX, see the module docstring for why 1G
# there would re-create the 2026-08-04 incident inside the tier built to fix
# it. Default = the tier's own MemoryHigh (host-setup.env.example
# DEV_GATES_MEMORY_HIGH=4G).
WATCHER_PER_CONTAINER_GATES_MEMORY_MAX = _get("WATCHER_PER_CONTAINER_GATES_MEMORY_MAX", "4G")
# Soft ceiling below the hard Max, same High+Max pairing the slices
# themselves already use — computed as a percentage of whichever Max
# resolves per-tier (not a separate absolute value) so it automatically
# tracks gates' different Max instead of needing its own override.
WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT = _get_pct("WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT", 80)
# Per-container swap ceiling: DEV_SWAP_CASCADE_PCT% of the MATCHED SLICE's
# own LIVE memory.swap.max (read from cgroupfs at apply time, not re-derived
# from host-setup.env) -- install.sh auto-computes DEV_*_MEMORY_SWAP_MAX
# from the host's total swap when left unset there, but does NOT write that
# computed number back into /etc/mdt/host-setup.env, so re-parsing the env
# file here would see empty for an auto-detected host. Reading the live
# cgroup value is correct either way, auto-detected or explicit override.
DEV_SWAP_CASCADE_PCT = _get_pct("DEV_SWAP_CASCADE_PCT", 80)
WATCHED_SLICES = ["dev-interactive.slice", "dev-background.slice", "dev-gates.slice"]
# Per-slice default MemoryMax -- every entry in WATCHED_SLICES MUST have one
# here (checked at startup in main()), so a slice added to one list without
# the other fails loudly instead of silently falling back to a value nobody
# chose for it.
SLICE_DEFAULT_MEMORY_MAX = {
    "dev-interactive.slice": WATCHER_PER_CONTAINER_MEMORY_MAX,
    "dev-background.slice": WATCHER_PER_CONTAINER_MEMORY_MAX,
    "dev-gates.slice": WATCHER_PER_CONTAINER_GATES_MEMORY_MAX,
}

# Additional cgroup properties worth capping on creation, left commented —
# uncomment and set a value (in host-setup.env, then export it below, or
# hardcode here) to also apply them. Not enabled by default: these are
# documented, not guessed at.
#
# WATCHER_PER_CONTAINER_CPU_WEIGHT = _get("WATCHER_PER_CONTAINER_CPU_WEIGHT", default="")
# WATCHER_PER_CONTAINER_IO_WEIGHT  = _get("WATCHER_PER_CONTAINER_IO_WEIGHT", default="")
# WATCHER_PER_CONTAINER_TASKS_MAX  = _get("WATCHER_PER_CONTAINER_TASKS_MAX", default="")

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


_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGTPE]?)(?:i?[Bb])?$", re.IGNORECASE)
_SIZE_SUFFIXES = {
    "": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
    "E": 1024**6,
}


def parse_size(value: str) -> int:
    """A systemd-style size string (bare bytes, or K/M/G/T/P/E with optional
    iB/B suffix, base 1024 — matches MemoryMax values accepted in unit files)."""
    match = _SIZE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid systemd size string: {value!r}")
    number, suffix = match.groups()
    return int(float(number) * _SIZE_SUFFIXES[suffix.upper()])


def read_slice_swap_max_bytes(slice_path: str) -> int | None:
    """Live memory.swap.max for a slice, in bytes. None if unbounded ('max')
    or unreadable — callers should skip a per-container swap cap rather than
    derive a percentage of 'unlimited'. Read from cgroupfs, not re-derived
    from host-setup.env: install.sh auto-computes DEV_*_MEMORY_SWAP_MAX from
    the host's total swap when left unset there, but never writes that
    number back into the env file, so re-parsing it here would see empty on
    a host that auto-detected rather than set an explicit override."""
    try:
        raw = open(os.path.join(slice_path, "memory.swap.max"), encoding="utf-8").read().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


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


def apply_default_cap(slice_path: str, slice_name: str, scope_name: str) -> None:
    container_id = resolve_container_id(scope_name)
    if container_id is None:
        return
    if has_explicit_memory_limit(container_id):
        log(f"{scope_name}: explicit --memory already set, leaving as-is")
        return
    unit = scope_name
    default_memory_max = SLICE_DEFAULT_MEMORY_MAX[slice_name]
    max_bytes = parse_size(default_memory_max)
    # Soft throttle-into-reclaim step below the hard cap, same High+Max
    # pairing the slices themselves already use.
    high_bytes = max_bytes * WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT // 100
    props = [f"MemoryMax={default_memory_max}", f"MemoryHigh={high_bytes}"]
    # Per-container swap ceiling: the "tight anon ceiling, generous swap"
    # pairing — cgroup v2 has no anon-only cap, so a total-resident cap
    # (MemoryMax/High above) plus a
    # per-container swap allowance is what expresses "cache gets dropped
    # first, anon growth beyond the cap gets pushed to swap rather than
    # OOM-killed" in kernel terms. None means the parent slice itself is
    # unbounded for swap — skip rather than derive a percentage of "max".
    swap_bytes = read_slice_swap_max_bytes(slice_path)
    if swap_bytes is not None:
        props.append(f"MemorySwapMax={swap_bytes * DEV_SWAP_CASCADE_PCT // 100}")
    # Uncomment corresponding lines above and here to also apply them:
    # if WATCHER_PER_CONTAINER_CPU_WEIGHT:
    #     props.append(f"CPUWeight={WATCHER_PER_CONTAINER_CPU_WEIGHT}")
    # if WATCHER_PER_CONTAINER_IO_WEIGHT:
    #     props.append(f"IOWeight={WATCHER_PER_CONTAINER_IO_WEIGHT}")
    # if WATCHER_PER_CONTAINER_TASKS_MAX:
    #     props.append(f"TasksMax={WATCHER_PER_CONTAINER_TASKS_MAX}")
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
    missing = [s for s in WATCHED_SLICES if s not in SLICE_DEFAULT_MEMORY_MAX]
    if missing:
        log(f"FATAL: {missing} in WATCHED_SLICES but SLICE_DEFAULT_MEMORY_MAX "
            f"has no default for it -- every watched slice needs a chosen "
            f"default, add one instead of falling back silently")
        sys.exit(1)
    check_inotify_available()
    fd = _libc.inotify_init1(0)
    if fd < 0:
        log(f"FATAL: inotify_init1 failed unexpectedly (errno={ctypes.get_errno()})")
        sys.exit(1)

    watches: dict[int, tuple[str, str]] = {}
    for slice_name in WATCHED_SLICES:
        watches.update(watch_slice(fd, slice_name))

    log(f"per-slice default MemoryMax: {SLICE_DEFAULT_MEMORY_MAX}; watched: {WATCHED_SLICES}")

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
            apply_default_cap(slice_path, slice_name, name)


if __name__ == "__main__":
    main()
