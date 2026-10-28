"""Target and work-dir guard (IOB-TGT-01..10, IOB-SAF-09).

A target is usable only with iobench's own marker.  Every refusal names its
condition; a refusal that blocks must have its legitimate state constructed in
the tests, so the legitimate states (a clean partition beside a mounted root on
the same disk, a work dir on another disk) are all accepted here.
"""
from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Sequence

from . import devio
from .errors import Refusal
from .host import TAIL_BYTES, Host, HostError, TargetRef

MARKER_MAGIC = "IOBENCH-TARGET-1"
GIB = 1 << 30
SIGNATURE_KEYS = ("TYPE=", "PTTYPE=", "USAGE=")
BAD_FS_TYPES = ("tmpfs", "ramfs")
ROOT_MOUNTS = ("/", "/boot")
SIDECAR_SUFFIX = ".iobench-target"
ROOT_TARGET_FREE_SHARE = 0.10
WORKDIR_MARK = ".iobench-workdir"
MIN_WORKDIR_FREE = 4 * GIB


# -- low-level facts ------------------------------------------------------------
@dataclass(frozen=True)
class MountEntry:
    devno: str
    mountpoint: str
    fstype: str
    source: str
    options: str


_OCTAL = re.compile(r"\\([0-7]{3})")


def _unescape(field: str) -> str:
    """mountinfo writes a space, tab, newline or backslash in a path as a 3-digit octal escape (``\\040``)."""
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[MountEntry]:
    out = []
    for line in text.splitlines():
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        out.append(MountEntry(fields[2], _unescape(fields[4]), tail[0], _unescape(tail[1]), fields[5]))
    return out


@dataclass(frozen=True)
class Chain:
    """sysfs relations of one block device."""

    name: str
    devno: str
    is_partition: bool
    disk_name: str
    disk_devno: str
    children: tuple[str, ...]  # devnos of partitions of this device
    holders: tuple[str, ...]  # devnos of holders of this device and its partitions


def _read_devno(host: Host, sysdir: str) -> str:
    return host.read_text(sysdir + "/dev").strip()


def device_chain(host: Host, devno: str) -> Chain:
    sysdir = host.realpath(f"/sys/dev/block/{devno}")
    part = host.exists(sysdir + "/partition")
    disk_dir = posixpath.dirname(sysdir) if part else sysdir
    child_dirs = [f"{sysdir}/{sub}" for sub in host.listdir(sysdir) if host.exists(f"{sysdir}/{sub}/partition")]
    holders = []
    for base in [sysdir, *child_dirs]:
        if host.exists(base + "/holders"):
            holders += [_read_devno(host, host.realpath(f"/sys/class/block/{h}")) for h in host.listdir(base + "/holders")]
    return Chain(posixpath.basename(sysdir), devno, part, posixpath.basename(disk_dir), _read_devno(host, disk_dir),
                 tuple(_read_devno(host, d) for d in child_dirs), tuple(holders))


def disk_attr(host: Host, disk_name: str, rel: str) -> str | None:
    path = f"/sys/class/block/{disk_name}/{rel}"
    if not host.exists(path):
        return None
    return host.read_text(path).strip() or None


@dataclass(frozen=True)
class DeviceFacts:
    path: str
    devno: str
    name: str
    size_bytes: int
    partuuid: str | None
    serial: str | None
    wwn: str | None
    model: str | None
    chain: Chain
    scheduler: str | None = None
    rotational: str | None = None

    @property
    def related(self) -> set[str]:
        return {self.devno, *self.chain.children, *self.chain.holders}


def _size(host: Host, devno: str) -> int:
    return int(host.read_text(host.realpath(f"/sys/dev/block/{devno}") + "/size")) * 512


def blkid_fields(host: Host, path: str) -> dict[str, str]:
    out = devio.probe(host, "blkid", devio.blkid_argv(path), ok=(0, 2)).stdout  # 2 = "no signature found"
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def read_device(host: Host, path: str) -> DeviceFacts:
    devno = host.devno(path)
    if devno is None:
        raise Refusal("not_block_device", f"{path} is not a block device", "name a dedicated partition such as /dev/vda7")
    chain = device_chain(host, devno)
    fields = blkid_fields(host, path)
    return DeviceFacts(path, devno, chain.name, _size(host, devno), fields.get("PART_ENTRY_UUID"),
                       disk_attr(host, chain.disk_name, "device/serial"), disk_attr(host, chain.disk_name, "wwid"),
                       disk_attr(host, chain.disk_name, "device/model"), chain)


# -- mount namespace and swap evidence ---------------------------------------------
def _touching(entries: Sequence[MountEntry], devnos: set[str], names: set[str]) -> set[MountEntry]:
    return {e for e in entries if e.devno in devnos or e.source in names}


INIT_PID_NS = "pid:[4026531836]"  # the kernel's fixed inode (PROC_PID_INIT_INO) of the initial PID namespace


def require_host_namespaces(host: Host) -> None:
    """SAF-09 / TGT-01: refuse unless this process provably runs in the host's initial PID namespace and mount namespace.

    Inside a container /proc/1 is the container's own init, so ``/proc/self`` and ``/proc/1`` always agree and cannot
    expose a mount that exists only in the host's namespace; the fact is therefore derived from the namespace link
    targets (the initial PID namespace has a fixed inode), and anything that cannot be read is a refusal.
    """
    try:
        own_pid, init_pid = host.readlink("/proc/self/ns/pid"), host.readlink("/proc/1/ns/pid")
        own_mnt, init_mnt = host.readlink("/proc/self/ns/mnt"), host.readlink("/proc/1/ns/mnt")
    except OSError as exc:
        raise Refusal("namespace_unprovable", f"cannot prove that this process runs in the host's initial PID namespace ({exc})",
                      "run on the host, not in a container (as root, so that /proc/1/ns is readable)") from exc
    if own_pid != INIT_PID_NS or init_pid != INIT_PID_NS:
        raise Refusal("container_pid_namespace", f"this process is not in the host's initial PID namespace ({own_pid}; PID 1 is {init_pid}, "
                      f"the host's is {INIT_PID_NS})", "run on the host, not in a container")
    if own_mnt != init_mnt:
        raise Refusal("mount_namespace_mismatch", f"this process is in a different mount namespace ({own_mnt}) than PID 1 ({init_mnt})",
                      "run on the host, not in a container or a private mount namespace")


def mount_evidence(host: Host, devnos: set[str], names: set[str]) -> tuple[set[MountEntry], set[MountEntry]]:
    require_host_namespaces(host)
    try:
        own = parse_mountinfo(host.read_text("/proc/self/mountinfo"))
        init = parse_mountinfo(host.read_text("/proc/1/mountinfo"))
    except OSError as exc:
        raise Refusal("namespace_unprovable", f"cannot read the mount tables of this process and PID 1 ({exc})",
                      "run on the host, not in a container (as root, so that /proc/1/mountinfo is readable)") from exc
    return _touching(own, devnos, names), _touching(init, devnos, names)


def check_namespaces(host: Host, devnos: set[str], names: set[str], what: str) -> set[MountEntry]:
    """SAF-09: the caller's and PID 1's view of every entry touching ``what`` must agree."""
    own, init = mount_evidence(host, devnos, names)
    if own != init:
        condition = "mounted_other_namespace" if (not own or not init) else "namespace_mismatch"
        where = "PID 1's mount namespace only" if not own else ("this mount namespace only" if not init else "differently in PID 1's mount namespace")
        raise Refusal(condition, f"{what} is mounted {where}; /proc/self/mountinfo and /proc/1/mountinfo differ",
                      "run iobench in the host mount namespace (not a container with a private view)")
    return own


def swap_devnos(host: Host) -> set[str]:
    lines = host.read_text("/proc/swaps").splitlines()[1:]
    return {d for d in (host.devno(line.split()[0]) for line in lines if line.strip()) if d is not None}


@dataclass(frozen=True)
class Violation:
    condition: str
    message: str
    overridable: bool = False


def violations(host: Host, facts: DeviceFacts) -> list[Violation]:
    out: list[Violation] = []
    watched = facts.related | {facts.chain.disk_devno}
    names = {facts.path, *(f"/dev/{posixpath.basename(host.realpath(f'/sys/dev/block/{d}'))}" for d in watched)}
    entries = check_namespaces(host, watched, names, facts.path)
    for e in sorted(entries, key=lambda e: e.mountpoint):
        if e.mountpoint in ROOT_MOUNTS:
            out.append(Violation("root", f"{facts.path} contains {e.mountpoint} (mounted from {e.source})"))
        elif e.devno == facts.devno:
            out.append(Violation("mounted", f"{facts.path} is mounted at {e.mountpoint}"))
        elif e.devno in facts.chain.children:
            out.append(Violation("parent_of_mounted", f"{facts.path} is the parent of {e.source}, mounted at {e.mountpoint}"))
        elif e.devno in facts.chain.holders:
            out.append(Violation("holder", f"{e.source}, a holder of {facts.path}, is mounted at {e.mountpoint}"))
        else:
            out.append(Violation("mounted", f"the parent disk of {facts.path} is mounted at {e.mountpoint}"))
    if facts.chain.holders and not any(v.condition == "holder" for v in out):
        out.append(Violation("holder_active", f"{facts.path} has active holders ({', '.join(sorted(facts.chain.holders))}: md, dm, LVM ...)"))
    swaps = swap_devnos(host)
    if swaps & watched:
        out.append(Violation("swap", f"{facts.path} (or a partition or holder of it) is swap-active (swapon)"))
    fields = blkid_fields(host, facts.path)
    sigs = [k for k in fields if f"{k}=".startswith(SIGNATURE_KEYS)]
    wipe = [ln for ln in devio.probe(host, "wipefs", devio.wipefs_check_argv(facts.path)).stdout.splitlines() if ln.strip() and not ln.startswith("#")]
    if sigs or wipe:
        what = ", ".join(f"{k}={fields[k]}" for k in sigs) or "a wipefs signature"
        out.append(Violation("signature", f"{facts.path} carries a signature ({what}) other than an iobench marker", True))
    return out


# -- markers -------------------------------------------------------------------------
def marker_text(size_bytes: int, partuuid: str | None, serial: str | None, created: str, **extra: object) -> str:
    return json.dumps({"magic": MARKER_MAGIC, "size_bytes": size_bytes, "partuuid": partuuid, "serial": serial,
                       "created": created, **extra}, sort_keys=True)


def parse_marker(text: str) -> dict | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and doc.get("magic") == MARKER_MAGIC else None


@dataclass(frozen=True)
class Target:
    kind: str
    path: str
    size_bytes: int
    devno: str | None = None
    partuuid: str | None = None
    serial: str | None = None
    fs_uuid: str | None = None
    inode: int | None = None
    disk_name: str | None = None
    wwn: str | None = None
    model: str | None = None

    @property
    def usable_bytes(self) -> int:
        return self.size_bytes - TAIL_BYTES

    @property
    def ref(self) -> TargetRef:
        return TargetRef(self.kind, self.path, self.size_bytes, self.devno, self.partuuid, self.serial, self.fs_uuid, self.inode)


def _need(cond: bool, condition: str, message: str, remedy: str = "") -> None:
    if not cond:
        raise Refusal(condition, message, remedy)


def init_device(host: Host, path: str, *, own: bool, expect_size: int | None, expect_partuuid: str | None,
                expect_serial: str | None, created: str, dry_run: bool = False) -> Target:
    """``iobench target init DEV`` (IOB-TGT-01/02): refuse, or write the marker."""
    facts = read_device(host, path)
    found = violations(host, facts)
    hard = [v for v in found if not v.overridable]
    if hard:
        raise Refusal(hard[0].condition, "; ".join(v.message for v in hard),
                      "use a dedicated unmounted partition; mounted, swap and root targets can never be overridden")
    _need(facts.partuuid is not None or facts.serial is not None, "identity_missing",
          f"{path} has neither a PARTUUID nor a serial; size alone is not an identity",
          "create a GPT partition (it carries a PARTUUID) and target that")
    if found:
        _need(own, "signature", found[0].message,
              "if this device is yours to destroy, pass --i-own-this-device with --expect-size and --expect-partuuid (or --expect-serial)")
    _need(expect_size == facts.size_bytes, "expect_size_mismatch",
          f"--expect-size {expect_size} does not equal the actual size {facts.size_bytes} of {path}",
          f"pass --expect-size {facts.size_bytes} if this is the intended device")
    if facts.serial is not None:
        _need(expect_serial == facts.serial or expect_partuuid == facts.partuuid and facts.partuuid is not None,
              "expect_identity_mismatch", f"--expect-serial / --expect-partuuid do not match {path}",
              "pass the serial (or PARTUUID) shown by `lsblk -o NAME,SERIAL,PARTUUID`")
    else:
        _need(expect_partuuid == facts.partuuid, "expect_identity_mismatch",
              f"--expect-partuuid {expect_partuuid} does not equal the actual PARTUUID of {path}",
              "pass the PARTUUID shown by `lsblk -o NAME,PARTUUID`")
    target = Target("device", path, facts.size_bytes, facts.devno, facts.partuuid, facts.serial, None, None,
                    facts.chain.disk_name, facts.wwn, facts.model)
    if not dry_run:
        try:
            host.write_marker(target.ref, marker_text(facts.size_bytes, facts.partuuid, facts.serial, created))
        except HostError as exc:  # the identity re-verification (or the kernel's exclusive open) said no: a refusal, exit 2
            raise Refusal("identity_changed", f"the device could not be claimed for the marker ({exc})",
                          "check that the device is still the intended, unused one and rerun") from exc
    return target


def load_target(host: Host, path: str) -> Target:
    """Read facts and marker of an existing target; refuse unless it is an iobench target (TGT-04/07)."""
    if host.devno(path) is None:
        return load_file_target(host, path)
    facts = read_device(host, path)
    target = Target("device", path, facts.size_bytes, facts.devno, facts.partuuid, facts.serial, None, None,
                    facts.chain.disk_name, facts.wwn, facts.model)
    marker = parse_marker(host.read_marker(target.ref))
    _need(marker is not None, "no_marker", f"{path} carries no iobench marker",
          "run `iobench target init` (with its ownership flags) first")
    _need(marker["size_bytes"] == facts.size_bytes, "marker_size_changed",
          f"the device size changed since the marker was written ({marker['size_bytes']} -> {facts.size_bytes})",
          "re-run `iobench target init` if the device was resized on purpose")
    _need(marker["partuuid"] == facts.partuuid, "marker_partuuid_changed",
          f"the PARTUUID of {path} changed since the marker was written", "re-run `iobench target init`")
    for v in violations(host, facts):
        if v.condition != "signature":
            raise Refusal(v.condition, v.message, "do not run iobench against this device")
    fields = blkid_fields(host, path)
    _need(not any(f"{k}=".startswith(SIGNATURE_KEYS) for k in fields), "foreign_signature",
          f"{path} now carries a foreign signature besides the iobench marker", "do not run iobench against it; restore the device")
    return target


def verify_target(host: Host, target: Target) -> None:
    """Re-verify before EVERY writing stage (IOB-TGT-04 / -07); `--installer` never bypasses this."""
    if target.kind == "file":
        _verify_file(host, target)
        return
    live = load_target(host, target.path)
    _need(live.devno == target.devno, "devno_changed", f"the device number of {target.path} changed")


# -- file targets ----------------------------------------------------------------------
@dataclass(frozen=True)
class FsInfo:
    mountpoint: str
    source: str
    fstype: str
    options: str
    devno: str
    uuid: str | None
    total: int
    free: int

    @property
    def mount_discard(self) -> str:
        return "true" if "discard" in self.options.split(",") else "false"


def fs_info(host: Host, path: str) -> FsInfo:
    path = host.realpath(path)  # `..` and symlinks resolved first: a textual prefix match would put /mnt/data/../x under /mnt/data
    entries = [e for e in parse_mountinfo(host.read_text("/proc/self/mountinfo"))
               if path == e.mountpoint or path.startswith(e.mountpoint.rstrip("/") + "/")]
    e = max(entries, key=lambda e: len(e.mountpoint))
    uuid = blkid_fields(host, e.source).get("UUID") if e.source.startswith("/dev/") else None
    total, free = host.statfs(path)
    return FsInfo(e.mountpoint, e.source, e.fstype, e.options, e.devno, uuid, total, free)


def on_root_fs(host: Host, fs: FsInfo) -> bool:
    """Whether ``fs`` is the filesystem that holds ``/``: the same device or backing source, not only a mount point of ``/``
    (a btrfs subvolume or a bind mount of the root filesystem is the root filesystem too)."""
    root = next((e for e in parse_mountinfo(host.read_text("/proc/self/mountinfo")) if e.mountpoint == "/"), None)
    return root is not None and (fs.devno == root.devno or (fs.source.startswith("/dev/") and fs.source == root.source))


def file_margin(total: int) -> int:
    return max(2 * GIB, total // 20)


def sidecar_path(path: str) -> str:
    return path + SIDECAR_SUFFIX


def init_file(host: Host, path: str, *, size: int | None, own: bool, expect_fs_uuid: str | None, confirm_quiet_window: bool,
              allow_large_root_target: bool, created: str, dry_run: bool = False) -> Target:
    """``iobench target init --file`` (IOB-TGT-06)."""
    require_host_namespaces(host)  # the root-filesystem and free-space rules read the mount table: it must be the host's
    _need(size is not None, "size_required", "a file target needs --size (the operator's upper bound for everything iobench writes)",
          "pass --size BYTES")
    _need(own, "own_file_required", f"{path} would be created and overwritten by iobench", "pass --i-own-this-file")
    _need(":" not in path, "path_colon", f"{path} contains ':' (fio would split it into several files)", "use a path without ':'")
    st = host.stat(path)
    _need(st is None or not st.is_symlink, "symlink", f"{path} is a symlink", "name a regular file path")
    fs = fs_info(host, posixpath.dirname(path))
    _need(expect_fs_uuid is not None and fs.uuid == expect_fs_uuid, "fs_uuid_mismatch",
          f"--expect-fs-uuid {expect_fs_uuid} does not equal the filesystem UUID {fs.uuid} under {path}",
          f"pass --expect-fs-uuid {fs.uuid} if this is the intended filesystem")
    _need(fs.fstype not in BAD_FS_TYPES + ("overlay",), "fs_type", f"{path} is on a {fs.fstype} filesystem",
          "use a persistent disk filesystem (ext4, xfs)")
    _need(st is None or sidecar_matches(host, path, st, fs), "exists_without_sidecar",
          f"{path} exists and has no iobench sidecar matching its inode, size and filesystem", "remove it yourself, or pick another path")
    _need(st is None or st.nlink <= 1, "hard_link", f"{path} has a link count above 1", "use a file with a single link")
    margin = file_margin(fs.total)
    _need(fs.free >= size + margin, "free_space",
          f"free space {fs.free} is below size + margin ({size} + {margin})", "pass a smaller --size")
    if on_root_fs(host, fs):
        _need(confirm_quiet_window, "root_fs_needs_quiet_window", f"{path} is on the root filesystem",
              "pass --confirm-quiet-window in an agreed window")
        _need(allow_large_root_target or size <= ROOT_TARGET_FREE_SHARE * fs.free, "root_target_too_large",
              f"--size {size} exceeds 10 % of the free space ({fs.free}) of the root filesystem",
              "pass a smaller --size or --allow-large-root-target")
    _need(dry_run or host.probe_odirect(posixpath.dirname(path)), "odirect_unsupported",
          f"the filesystem under {path} refuses O_DIRECT", "use another filesystem")
    if dry_run:
        return Target("file", path, size, None, None, None, fs.uuid, 0)
    host.allow_scratch(posixpath.dirname(path))  # the verified directory: the only place the bound fallocate may create the file
    try:
        run = host.preallocate(path, size)  # the bound fallocate: a plain `run` refuses every fallocate
    except HostError as exc:  # a path that is not a plain, cleanly named file place (a device node, a symlinked parent, odd characters)
        raise Refusal("path_unclean", str(exc), "name a new or regular file on the intended filesystem, with a plain path") from exc
    _need(run.returncode == 0, "fallocate_failed", f"fallocate failed: {run.stderr.strip()}", "free some space or pass a smaller --size")
    inode = host.stat(path).inode
    target = Target("file", path, size, None, None, None, fs.uuid, inode)
    host.write_marker(target.ref, marker_text(size, None, None, created, fs_uuid=fs.uuid, inode=inode))
    host.write_atomic(sidecar_path(path), json.dumps({"fs_uuid": fs.uuid, "inode": inode, "size": size, "created": created}))
    return target


def sidecar_matches(host: Host, path: str, st, fs: FsInfo) -> bool:
    """TGT-06: an existing file may be re-initialised only through a sidecar that MATCHES it (inode, size, filesystem uuid): a stale
    sidecar next to a user file that was later created at the same path must not authorise overwriting that file."""
    if not host.exists(sidecar_path(path)):
        return False
    try:
        doc = json.loads(host.read_text(sidecar_path(path)))
        return doc["inode"] == st.inode and doc["size"] == st.size and doc["fs_uuid"] == fs.uuid
    except (ValueError, KeyError, TypeError):
        return False


def load_file_target(host: Host, path: str) -> Target:
    require_host_namespaces(host)
    side = host.read_text(sidecar_path(path)) if host.exists(sidecar_path(path)) else None
    _need(side is not None, "no_marker", f"{path} has no iobench sidecar", "run `iobench target init --file` first")
    doc = json.loads(side)
    st = host.stat(path)
    _need(st is not None, "target_missing", f"{path} does not exist", "run `iobench target init --file` first")
    fs = fs_info(host, posixpath.dirname(path))
    target = Target("file", path, doc["size"], None, None, None, doc["fs_uuid"], doc["inode"])
    return _check_file(host, target, st, fs)


def _check_file(host: Host, target: Target, st, fs: FsInfo) -> Target:
    _need(not st.is_symlink and st.nlink <= 1, "file_not_exclusive", f"{target.path} is a symlink or has a link count above 1 now",
          "re-run `iobench target init --file` on a plain file with a single link")
    _need(st.inode == target.inode, "inode_changed", f"the inode of {target.path} changed (file replaced)", "re-run `iobench target init --file`")
    _need(st.size == target.size_bytes, "size_changed", f"{target.path} was truncated or grown", "re-run `iobench target init --file`")
    _need(fs.uuid == target.fs_uuid, "fs_uuid_changed", f"the filesystem UUID under {target.path} changed", "re-run `iobench target init --file`")
    marker = parse_marker(host.read_marker(target.ref))
    _need(marker is not None and marker.get("inode") == target.inode and marker.get("size_bytes") == target.size_bytes,
          "no_marker", f"the marker in {target.path} is missing or does not match its sidecar", "re-run `iobench target init --file`")
    return target


def _verify_file(host: Host, target: Target) -> None:
    load_file_target(host, target.path)
    side = json.loads(host.read_text(sidecar_path(target.path)))
    _need(side["size"] == target.size_bytes and side["inode"] == target.inode and side["fs_uuid"] == target.fs_uuid,
          "target_replaced", f"{target.path} is a different target than the one in use (sidecar changed)", "start the run again")


# -- sizing (IOB-TGT-10) ---------------------------------------------------------------
def file_ws_max(size: int, free: int, margin: int, r_est: float, fill_budget_s: float) -> tuple[int, int]:
    """``(WS_max, fresh_extent)`` of a file target; the file never exceeds ``size``."""
    first = max(0.0, min(size - TAIL_BYTES, 0.85 * free - margin, r_est * fill_budget_s))
    fresh = int(min(0.10 * first, 2 * GIB))
    return int(max(0, min(first, size - fresh - TAIL_BYTES))), fresh


# -- work dir (IOB-TGT-05) -----------------------------------------------------------------
@dataclass(frozen=True)
class Workdir:
    path: str
    fs: FsInfo
    disk_name: str
    cross_disk: bool
    on_root: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def check_workdir(host: Host, path: str, *, planned_volume: int, installer: bool, confirm_quiet_window: bool,
                  target_disk: str | None) -> Workdir:
    _need(":" not in path, "path_colon", f"{path} contains ':' (fio would split it into several files)", "use a path without ':'")
    entries = host.listdir(path) if host.exists(path) else []
    _need(all(n == WORKDIR_MARK for n in entries), "workdir_not_empty",
          f"{path} is not empty", f"use a new or empty directory (only {WORKDIR_MARK} may exist)")
    fs = fs_info(host, path)
    _need(fs.fstype not in BAD_FS_TYPES, "workdir_fs", f"{path} is on a {fs.fstype} filesystem",
          "use a directory on a persistent disk filesystem")
    _need(fs.fstype != "overlay" or installer, "workdir_fs", f"{path} is on an overlay filesystem",
          "use a directory on a persistent disk filesystem (installers may pass --installer)")
    need = max(MIN_WORKDIR_FREE, 3 * planned_volume)
    _need(fs.free >= need, "workdir_free_space", f"{path} has {fs.free} bytes free; {need} are required",
          "use a directory on a larger filesystem")
    on_root = on_root_fs(host, fs)
    _need(not on_root or confirm_quiet_window, "workdir_on_root", f"{path} is on the root filesystem",
          "pass --confirm-quiet-window in an agreed window")
    check_namespaces(host, {fs.devno}, {fs.source}, path)
    has_disk = host.exists(f"/sys/dev/block/{fs.devno}")
    disk = device_chain(host, fs.devno).disk_name if has_disk else ""
    cross = target_disk is not None and disk != target_disk
    return Workdir(path, fs, disk, cross, on_root, ("cross_disk",) if cross else ())
