#!/usr/bin/env python3
"""mounttrace - explain how a process's view of a path is assembled from mounts.

For PATH as seen by one process (default: this one; --pid: another one, e.g. a
container's main process) it reports:

  * EFFECTIVE MOUNT BOUNDARIES (leaf -> root): the mounts crossed while the
    kernel resolves PATH, with anything stacked underneath each one;
  * WRITE ROUTING: the mount and filesystem object that serve PATH now;
  * KERNEL PARENT-ID CHAIN of that mount (structural ancestry);
  * HIDDEN mounts on or below PATH, and why they are hidden;
  * propagation state (shared/slave/private) of every mount shown.

Comparisons and audits (all optional, all read-only):

  --host-path P        analyse P in the invoking process's view too and compare
                       the two backing objects, incl. the propagation link
  --peer PID:PATH      the same for further processes (repeatable)
  --audit-prefix H[=C] compare every mountpoint under H (invoking view) with
                       C (the --pid view; default C = H)
  --predict P          where would a NEW mount made at P in the invoking view
                       appear (peer groups, slaves, FSROOT, tucking)?
  --state-file F       audit a soulmask_tmpfs state file against live mounts
  --watch SECONDS      re-run whenever an inspected mount table changes

Linux only (reads /proc/PID/mountinfo); Python 3.9+, standard library only.
It never mounts, unmounts, enters namespaces or runs external commands.
PATH and --peer paths are interpreted in that process's root; --host-path,
--predict and the H side of --audit-prefix in the invoking process's root.
Inspecting another process's root needs root (ptrace access).

Exit status: 0 report printed, no ALERT; 3 at least one ALERT; 1 error;
2 usage error.  Guide: mounttrace.md next to this script.

Examples:
  mounttrace.py /srv/data/project --submounts
  sudo mounttrace.py /data/project --pid 12345 --host-path /srv/data/project
  sudo mounttrace.py /var/lib/pterodactyl/volumes --pid "$wings_pid" \\
      --audit-prefix /var/lib/pterodactyl/volumes --state-file /run/soulmask_tmpfs.state
  sudo mounttrace.py /home/container --pid "$game_pid" \\
      --audit-prefix /var/lib/pterodactyl/volumes/$uuid=/home/container
  sudo mounttrace.py / --pid "$wings_pid" --predict /var/lib/pterodactyl/volumes/$uuid/WS/Saved/Logs
"""

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
import fcntl
import os
import posixpath
import re
import stat
import struct
import sys
import time

VERSION = '3.2.0'


_CONTROL_CHAR = re.compile(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2069\ufeff]')


def safe_text(value, keep_layout=False):
    """Render terminal controls visibly without changing the value we inspect."""
    text = str(value)

    def visible(match):
        char = match.group(0)
        if keep_layout and char in ('\n', '\t'):
            return char
        codepoint = ord(char)
        if codepoint > 0xff:
            return f'\\u{codepoint:04x}'
        return f'\\x{codepoint:02x}'

    return _CONTROL_CHAR.sub(visible, text)


class TerminalSafeStream:
    """Escape controls in text written by the CLI while preserving layout."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        self.stream.write(safe_text(text, keep_layout=True))
        return len(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


# btrfs: struct btrfs_ioctl_get_subvol_info_args is 504 bytes (kernel >= 4.18,
# unprivileged). _IOR(0x94, 60, 504).
BTRFS_IOC_GET_SUBVOL_INFO = (2 << 30) | (504 << 16) | (0x94 << 8) | 60
BTRFS_ROOT_SUBVOL_RDONLY = 1 << 0  # GET_SUBVOL_INFO returns the root item flags
BTRFS_FIRST_FREE_OBJECTID = 256    # inode number of every subvolume's root directory
BTRFS_EMPTY_SUBVOL_DIR_OBJECTID = 2  # stub left in a snapshot where a nested subvolume was


# --------------------------------------------------------------------------
# mountinfo model
# --------------------------------------------------------------------------

def unescape(value):
    """Decode mountinfo's octal escapes (\\040 space, \\011 tab, \\012 newline, \\134 backslash)."""
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), value)


@dataclass(frozen=True)
class Mount:
    id: int          # mount ID (unique system-wide while the mount exists)
    parent: int      # parent mount ID
    dev: str         # st_dev of the superblock, major:minor
    root: str        # FSROOT: directory of the filesystem that is this mount's root
    where: str       # mountpoint relative to the process root
    vfs: str         # per-mount options (ro/rw, nosuid, ...)
    optional: tuple  # shared:N master:N propagate_from:N unbindable
    fstype: str
    source: str
    fsopts: str      # superblock options

    def _tag(self, name):
        for value in self.optional:
            if value.startswith(name + ':'):
                return int(value.split(':', 1)[1])
        return None

    @property
    def shared(self):
        return self._tag('shared')

    @property
    def master(self):
        return self._tag('master')

    @property
    def propagate_from(self):
        return self._tag('propagate_from')

    @property
    def propagation(self):
        if 'unbindable' in self.optional:
            kind = 'unbindable'
        elif self.shared is not None:
            kind = 'shared+slave' if self.master is not None else 'shared'
        elif self.master is not None:
            kind = 'slave'
        else:
            kind = 'private'
        tags = [v for v in self.optional if v != 'unbindable']
        return kind + (' (' + ' '.join(tags) + ')' if tags else '')

    @property
    def vfs_ro(self):
        return 'ro' in self.vfs.split(',')

    @property
    def sb_ro(self):
        return 'ro' in self.fsopts.split(',')

    @property
    def readonly(self):
        return self.vfs_ro or self.sb_ro

    @property
    def ro_label(self):
        if self.vfs_ro and self.sb_ro:
            return 'RO(mount+superblock)'
        if self.vfs_ro:
            return 'RO(mount)'
        if self.sb_ro:
            return 'RO(superblock)'
        return 'rw'

    def fsopt(self, key):
        for item in self.fsopts.split(','):
            name, sep, value = item.partition('=')
            if name == key:
                return value if sep else ''
        return None


def mountinfo_file(pid):
    return f'/proc/{pid}/mountinfo' if pid is not None else '/proc/self/mountinfo'


def proc_root(pid):
    return f'/proc/{pid}/root' if pid is not None else '/proc/self/root'


def parse_mountinfo_line(line):
    # Fields are single-space separated; split(' ') (not split()) keeps an
    # empty mount source from shifting the fields after it.
    left, sep, right = line.partition(' - ')
    if not sep:
        raise RuntimeError(f'unparseable mountinfo line: {line!r}')
    a, b = left.split(' '), right.split(' ')
    if len(a) < 6 or len(b) < 2:
        raise RuntimeError(f'unparseable mountinfo line: {line!r}')
    return Mount(int(a[0]), int(a[1]), a[2], unescape(a[3]), unescape(a[4]), a[5],
                 tuple(a[6:]), b[0], unescape(b[1]), ' '.join(b[2:]))


def read_mountinfo(pid=None):
    # newline='\n': the kernel escapes \n in paths but not \r; universal newlines would split on it.
    with open(mountinfo_file(pid), encoding='utf-8', errors='surrogateescape', newline='\n') as file:
        return [parse_mountinfo_line(line.rstrip('\n')) for line in file if line.strip()]


def namespace_id(pid):
    try:
        return os.readlink(f'/proc/{pid if pid is not None else "self"}/ns/mnt')
    except OSError:
        return None


def inside(path, prefix):
    return prefix == '/' or path == prefix or path.startswith(prefix.rstrip('/') + '/')


def rebase(path, old_prefix, new_prefix):
    """Map PATH below OLD_PREFIX to the same relative location below NEW_PREFIX."""
    rel = posixpath.relpath(path, old_prefix)
    return posixpath.normpath(posixpath.join(new_prefix, rel)) if rel != '.' else new_prefix


def prefixes(path):
    yield '/'
    current = ''
    for comp in path.strip('/').split('/'):
        if comp:
            current += '/' + comp
            yield current


def canonicalize(path, pid, no_resolve=False):
    """Resolve symlinks componentwise inside the selected process's root.

    Components are read through /proc/PID/root one at a time, so absolute
    symlinks restart at *that* process's root (realpath() on the
    /proc/PID/root/... string would escape to ours). A missing component keeps
    the rest of the path lexically (useful for a not-yet-created leaf).
    """
    if not path.startswith('/'):
        raise ValueError(f'expected an absolute path, got {path!r}')
    if no_resolve:
        return posixpath.normpath(path), 'symlinks not resolved (--no-resolve)'
    base = proc_root(pid)
    pending = deque(path.split('/'))
    resolved = []
    links = 0
    while pending:
        component = pending.popleft()
        if component in ('', '.'):
            continue
        if component == '..':
            if resolved:
                resolved.pop()
            continue
        candidate = '/' + '/'.join(resolved + [component])
        try:
            metadata = os.lstat(base + candidate)
        except FileNotFoundError:
            rest = '/'.join(pending)
            return (posixpath.normpath(candidate + ('/' + rest if rest else '')),
                    f'{candidate} does not exist; the rest of the path is analysed lexically')
        except OSError as err:
            hint = ' (run as root to inspect another process root)' if isinstance(err, PermissionError) else ''
            return posixpath.normpath(path), f'could not resolve symlinks: {err}{hint}; lexical analysis only'
        if stat.S_ISLNK(metadata.st_mode):
            links += 1
            if links > 40:
                raise RuntimeError(f'too many symbolic links resolving {path}')
            link = os.readlink(base + candidate)
            if link.startswith('/'):
                resolved = []
            pending.extendleft(reversed(link.split('/')))
        else:
            resolved.append(component)
    return '/' + '/'.join(resolved), None


def stat_identity(pid, path):
    """(major, minor, inode) of PATH resolved inside PID's root, or None."""
    try:
        info = os.stat(proc_root(pid) + path)
    except OSError:
        return None
    return os.major(info.st_dev), os.minor(info.st_dev), info.st_ino


def _fmt_uuid(raw):
    if not any(raw):
        return None
    h = raw.hex()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}'


def btrfs_subvol_info(pid, path):
    """Subvolume containing PATH (resolved in PID's root), via the unprivileged
    BTRFS_IOC_GET_SUBVOL_INFO ioctl; None if unavailable. Opens only
    directories (the nearest existing one), never devices or FIFOs."""
    target = proc_root(pid) + path
    while True:
        try:
            if stat.S_ISDIR(os.stat(target).st_mode):
                break
        except OSError:
            pass
        parent = posixpath.dirname(target.rstrip('/'))
        if parent == target or len(parent) < len(proc_root(pid)):
            return None
        target = parent
    try:
        fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(504)
        fcntl.ioctl(fd, BTRFS_IOC_GET_SUBVOL_INFO, buf, True)
    except OSError:
        return None
    finally:
        os.close(fd)
    treeid, = struct.unpack_from('=Q', buf, 0)
    name = bytes(buf[8:264]).split(b'\0', 1)[0].decode('utf-8', 'surrogateescape')
    parent_id, _dirid, generation, flags = struct.unpack_from('=4Q', buf, 264)
    return {
        'id': treeid, 'name': name or '<top level>', 'parent_id': parent_id,
        'generation': generation, 'readonly': bool(flags & BTRFS_ROOT_SUBVOL_RDONLY),
        'uuid': _fmt_uuid(bytes(buf[296:312])), 'parent_uuid': _fmt_uuid(bytes(buf[312:328])),
        'received_uuid': _fmt_uuid(bytes(buf[328:344])),
    }


def btrfs_label(info):
    if not info:
        return 'subvolume unknown (ioctl unavailable)'
    text = f'subvolume id={info["id"]} name={info["name"]!r} parent={info["parent_id"]}'
    if info['readonly']:
        text += ' READ-ONLY'
    if info['parent_uuid']:
        text += f' (snapshot of {info["parent_uuid"]})'
    return text


def btrfs_boundaries(pid, m, path):
    """Subvolume boundaries crossed below mount M on the way to PATH, which btrfs
    creates WITHOUT a mount: [(path, kind, info)] root -> leaf.  kind is
    'subvolume' (a nested subvolume root: new st_dev, inode 256) or 'stub' (an
    empty placeholder, inode 2, where a snapshot did not include a nested subvolume)."""
    out = []
    try:
        previous = os.stat(proc_root(pid) + m.where).st_dev
    except OSError:
        return out
    for part in prefixes(path):
        if not inside(part, m.where) or part == m.where:
            continue
        try:
            info = os.lstat(proc_root(pid) + part)
        except OSError:
            break
        if not stat.S_ISDIR(info.st_mode):
            break
        if info.st_ino == BTRFS_EMPTY_SUBVOL_DIR_OBJECTID:
            out.append((part, 'stub', None))
            break
        if info.st_dev != previous and info.st_ino == BTRFS_FIRST_FREE_OBJECTID:
            out.append((part, 'subvolume', btrfs_subvol_info(pid, part)))
        previous = info.st_dev
    return out


def fs_object(m, path):
    """Path of PATH's object inside m's filesystem (FSROOT + suffix below the mountpoint)."""
    suffix = posixpath.relpath(path, m.where)
    return posixpath.normpath(posixpath.join(m.root, '' if suffix == '.' else suffix))


class View:
    """One process's mount table with pathname-resolution helpers."""

    def __init__(self, mounts, label='', pid=None):
        self.mounts = mounts
        self.label = label
        self.pid = pid
        self.warnings = []
        self.by_id = {m.id: m for m in mounts}
        self.children = defaultdict(list)
        for m in mounts:
            if m.parent != m.id:
                self.children[m.parent].append(m)
        root_mounts = [m for m in mounts if m.where == '/']
        bases = [m for m in root_mounts if m.parent not in self.by_id or m.parent == m.id]
        if len(bases) != 1:
            ids = {r.id for r in root_mounts}
            bases = [m for m in root_mounts if m.parent not in ids]
        if len(bases) != 1:
            raise RuntimeError(f'{label or "mount table"}: could not uniquely locate the process-visible / '
                               f'(candidates: {[m.id for m in root_mounts]})')
        self.base = bases[0]
        self._effective = {}
        self._visible = None

    def _warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def top_at(self, where, initial):
        """Follow a stack of mounts at one pathname via parent IDs (not ID order)."""
        m, covered, seen = initial, [], set()
        while m.id not in seen:
            seen.add(m.id)
            covering = [c for c in self.children[m.id] if c.where == where]
            if not covering:
                return m, covered
            if len(covering) > 1:
                # Pre-4.11 "shadow mounts" from propagation. The kernel's hash
                # lookup returns the most recently attached one, which is the
                # last in mountinfo (attach) order.
                self._warn(f'several mounts share parent #{m.id} at {where}: '
                           f'{[c.id for c in covering]}; assuming the last one is effective')
            if m.where == where:   # the starting mount is usually the parent directory's
                covered.append(m)
            m = covering[-1]
        raise RuntimeError(f'cycle in the mount stack at {where}')

    def trace(self, path):
        """[(prefix, effective mount, mounts covered at that prefix)] root -> leaf, one per change."""
        if not path.startswith('/'):
            raise ValueError(f'expected an absolute path, got {path!r}')
        top, covered = self.top_at('/', self.base)
        steps = [('/', top, covered)]
        for part in list(prefixes(path))[1:]:
            nxt, shadows = self.top_at(part, top)
            if nxt.id != top.id:
                top = nxt
                steps.append((part, top, shadows))
        return steps

    def effective(self, path):
        if path not in self._effective:
            self._effective[path] = self.trace(path)[-1][1]
        return self._effective[path]

    def visible_ids(self):
        # A mount is reachable iff resolving its own mountpoint ends on it. A
        # mount hidden because an ANCESTOR directory was overmounted can have a
        # TARGET quite different from the covering mount's.
        if self._visible is None:
            self._visible = {m.id for m in self.mounts if self.effective(m.where).id == m.id}
        return self._visible

    def hidden_reason(self, m, _depth=0):
        eff = self.effective(m.where)
        parent = self.by_id.get(m.parent)
        if (parent is not None and parent.id != m.id and parent.where != m.where
                and parent.id not in self.visible_ids() and _depth < 64):
            # The parent itself is covered; whatever now sits at m.where is not stacked on m.
            # (A parent at the same mountpoint is just a lower layer of m's own stack.)
            return (f'unreachable: its parent mount #{parent.id} at {parent.where} is itself hidden '
                    f'({self.hidden_reason(parent, _depth + 1)})')
        if eff.where == m.where:
            return f'overmounted at the same mountpoint; #{eff.id} ({eff.fstype} {eff.source!r}) is on top'
        return f'unreachable: resolving {m.where} ends in #{eff.id} mounted at {eff.where} ({eff.fstype} {eff.source!r})'

    def parents(self, m):
        out, seen = [], set()
        while m.id not in seen:
            out.append(m)
            seen.add(m.id)
            if m.parent == m.id or m.parent not in self.by_id:
                break
            m = self.by_id[m.parent]
        return out


# --------------------------------------------------------------------------
# propagation
# --------------------------------------------------------------------------

def propagation_receivers(group, views):
    """Mount IDs (in the given views) that receive mount events of peer group GROUP.

    Events go to every peer (shared:G) and every slave (master:G) of the group;
    a slave that is itself shared (shared:K master:G) forwards to its peers K
    and their slaves, and so on. propagate_from:G marks a slave whose master
    group is not visible in its namespace but which receives from G. Mounts in
    namespaces not inspected here can also receive the events; they are simply
    not visible to this tool.

    Returns {mount id: (label, both_directions)}; only direct peers of GROUP
    send events back to it.
    """
    everything = {m.id: m for v in views for m in v.mounts}.values()
    groups, received = {group}, {}
    changed = True
    while changed:
        changed = False
        for m in everything:
            if m.id in received:
                continue
            if m.shared == group:
                received[m.id] = (f'peer (shared:{m.shared})', True)
            elif m.shared in groups:
                received[m.id] = (f'peer (shared:{m.shared}) of a slave of the group', False)
            elif m.master in groups:
                received[m.id] = (f'slave (master:{m.master})', False)
            elif m.propagate_from in groups:
                received[m.id] = (f'slave (master:{m.master} propagate_from:{m.propagate_from})', False)
            else:
                continue
            changed = True
            if m.shared is not None:
                groups.add(m.shared)
    return received


def place_event(view, receiver, rel):
    """Where a mount event at filesystem path REL (relative to the superblock)
    lands in VIEW through RECEIVER, and whether it is visible there.

    Returns (path or None, status text, visible flag).
    """
    if not inside(rel, receiver.root):
        return None, (f'not reachable: the receiver exposes {receiver.root!r} of that filesystem, '
                      f'which does not contain {rel!r}'), False
    path = rebase(rel, receiver.root, receiver.where)
    if receiver.id not in view.visible_ids():
        return path, f'receiver #{receiver.id} is itself hidden in this view ({view.hidden_reason(receiver)})', False
    existing = [c for c in view.children[receiver.id] if c.where == path]
    if existing:
        return path, (f'TUCKED beneath existing mount #{existing[-1].id} at {path} '
                      f'(kernel >= 4.11 slides the copy under it): not visible until #{existing[-1].id} '
                      f'is unmounted'), False
    eff = view.effective(path)
    if eff.id == receiver.id:
        return path, 'APPEARS', True
    return path, f'attached but hidden: {path} resolves through #{eff.id} mounted at {eff.where}', False


def link_status(host_mount, host_path, other_view, other_path, host_view):
    """Will a future host mount at HOST_PATH (served now by HOST_MOUNT, which becomes
    the new mount's parent) show up at OTHER_PATH in OTHER_VIEW?

    The propagated copy is made under every mount that receives HOST_MOUNT's peer
    group, so the receiver is whichever of them places it at OTHER_PATH, not
    necessarily the mount serving OTHER_PATH now (that can be a private mount
    made in that view, or a retained copy, on top of the real receiver)."""
    if other_view.effective(other_path).id == host_mount.id:
        return 'same mount object (same namespace)'
    if host_mount.shared is None:
        return ('NOT LINKED: host mount #%d is %s, so mount events under it are not propagated '
                'to any other mount' % (host_mount.id, host_mount.propagation))
    group = host_mount.shared
    # An audit asks this for every pair: compute the receivers once per group and view pair
    # (views are rebuilt on every pass, so the cache never outlives its tables).
    cache = other_view.__dict__.setdefault('_receivers', {})
    key = (group, id(host_view))
    if key not in cache:
        received = propagation_receivers(group, [host_view, other_view])
        cache[key] = (received, [m for m in other_view.mounts if m.id in received])
    received, candidates = cache[key]
    rel = fs_object(host_mount, host_path)
    hits, elsewhere = [], set()
    for m in candidates:
        if m.id == host_mount.id:
            continue
        path, status, visible = place_event(other_view, m, rel)
        if path == other_path:
            hits.append((not visible, m.id, m, status))
        elif path is not None:
            elsewhere.add(path)
    if not hits:
        text = (f'NOT LINKED: no mount of this view receives host shared:{group} events at '
                f'{other_path}; later host mounts/umounts there will not reach this view')
        if elsewhere:
            text += f' (copies would arrive at {", ".join(sorted(elsewhere))} instead)'
        return text
    _hidden, _id, m, status = min(hits)
    label, both = received[m.id]
    direction = 'both directions' if both else 'host -> this view only'
    return (f'LINKED as {label} of host shared:{group} via #{m.id} ({direction}); '
            f'a new host mount here: {status}')


# --------------------------------------------------------------------------
# findings / output helpers
# --------------------------------------------------------------------------

class Findings:
    def __init__(self):
        self.alerts = 0

    def alert(self, text, indent='    '):
        self.alerts += 1
        print(f'{indent}ALERT: {safe_text(text)}')


FINDINGS = Findings()


def desc(m, with_opts=False):
    result = (f'#{m.id} parent={m.parent} {safe_text(m.fstype)} {m.source!r} dev={m.dev} '
              f'FSROOT={m.root!r} [{m.ro_label}] propagation={m.propagation}')
    if m.fstype == 'btrfs':
        result += f' subvol={m.fsopt("subvol")!r} subvolid={m.fsopt("subvolid")}'
    if with_opts:
        result += (f'\n      mount options: {safe_text(m.vfs)}'
                   f'\n      superblock options: {safe_text(m.fsopts)}')
    return result


def identity(view, pid, path):
    m = view.effective(path)
    return m, fs_object(m, path), stat_identity(pid, path)


def same_object(a, b):
    """Compare two identity() results -> (True/False/None, evidence)."""
    ma, oa, sa = a
    mb, ob, sb = b
    if sa is not None and sb is not None:
        return sa == sb, 'stat dev+inode'
    if ma.dev != mb.dev:
        return False, 'different superblocks (mountinfo dev)'
    return oa == ob, 'mountinfo dev + filesystem path (stat unavailable)'


# --------------------------------------------------------------------------
# main report
# --------------------------------------------------------------------------

def report(view, path, pid=None, submounts=False, all_hidden=False, no_resolve=False):
    canon, warning = canonicalize(path, pid, no_resolve)
    nsid = namespace_id(pid) or '(namespace unavailable)'
    print(f'\n==== {view.label}: {safe_text(path)}  [{safe_text(nsid)}] ====')
    if pid is not None and nsid == namespace_id(None):
        print('  (same mount namespace as the invoking process)')
    if canon != path:
        print(f'  resolved -> {safe_text(canon)}')
    if warning:
        print(f'  NOTE: {safe_text(warning)}')

    steps = view.trace(canon)
    # btrfs subvolume boundaries are crossed without any mount; list them with
    # the mount boundaries (they matter for snapshots, quotas and read-only).
    sub_bounds = {}
    for i, (where, m, _covered) in enumerate(steps):
        if m.fstype == 'btrfs':
            upto = canon
            if i + 1 < len(steps):
                upto = posixpath.dirname(steps[i + 1][0]) or '/'
            for item in btrfs_boundaries(pid, m, upto):
                sub_bounds.setdefault(i, []).append(item)
    print('\nEFFECTIVE MOUNT BOUNDARIES (leaf -> root; the kernel resolves root -> leaf):')
    for i in reversed(range(len(steps))):
        where, m, covered = steps[i]
        for bpath, kind, info in reversed(sub_bounds.get(i, [])):
            if kind == 'stub':
                print(f'  {safe_text(bpath)}  ->  btrfs EMPTY-SUBVOLUME STUB (inode 2): a snapshot did not include '
                      f'the nested subvolume that was here; nothing below it can be created')
            else:
                print(f'  {safe_text(bpath)}  ->  btrfs nested {safe_text(btrfs_label(info))} (boundary without a mount)')
        print(f'  {safe_text(where)}  ->  {desc(m)}')
        for c in covered:
            print(f'     covers #{c.id} at the same mountpoint: {safe_text(c.fstype)} {c.source!r} FSROOT={c.root!r}')

    last = steps[-1][1]
    obj = fs_object(last, canon)
    print('\nWRITE ROUTING (which mount serves the path; not a permission test):')
    print(f'  {safe_text(canon)} -> {desc(last, with_opts=True)}')
    print(f'  object inside that filesystem: {safe_text(obj)}')
    if last.fstype == 'btrfs':
        info = btrfs_subvol_info(pid, canon)
        print(f'  btrfs: data lands in {safe_text(btrfs_label(info))}')
        if info and info['readonly']:
            print('  NOTE: read-only subvolume (e.g. a read-only snapshot): writes fail with EROFS '
                  'although the mount is rw')
    if last.fstype == 'overlay':
        upper = last.fsopt('upperdir')
        print('  overlay: new and modified file data goes to upperdir'
              + (f' = {safe_text(upper)}' if upper else ' (upperdir not shown: read-only overlay or hidden options)'))
    if last.readonly:
        print(f'  NOTE: {last.ro_label}: writes through this path fail with EROFS')

    chain = view.parents(last)
    print('\nKERNEL PARENT-ID CHAIN (leaf -> root; structural, not necessarily pathname crossings):')
    for i, m in enumerate(chain):
        print(f'  {"  " * i}#{m.id} {safe_text(m.where)}  {safe_text(m.fstype)} {m.source!r} [parent={m.parent}]')
    if chain[-1].parent not in view.by_id and chain[-1].parent != chain[-1].id:
        print(f'  (parent #{chain[-1].parent} is outside this process\'s root / mount table)')

    visible = view.visible_ids()
    hidden = [m for m in view.mounts if m.id not in visible and
              (all_hidden or inside(canon, m.where) or inside(m.where, canon))]
    print('\nHIDDEN MOUNTS on or below the path' + (' (all)' if all_hidden else '') + ':')
    if not hidden:
        print('  (none)')
    for m in sorted(hidden, key=lambda m: (m.where, m.id)):
        print(f'  {safe_text(m.where)}: {desc(m)}\n     {safe_text(view.hidden_reason(m))}')

    if submounts:
        children = [m for m in view.mounts if m.id in visible and inside(m.where, canon) and m.id != last.id]
        print('\nVISIBLE SUBMOUNTS below the path:')
        if not children:
            print('  (none)')
        for m in sorted(children, key=lambda m: (m.where.count('/'), m.where, m.id)):
            print(f'  {safe_text(m.where)} -> {desc(m)}')
    for text in view.warnings:
        print(f'  WARNING: {safe_text(text)}')
    return canon, (last, obj, stat_identity(pid, canon))


def compare_results(name_a, result_a, name_b, result_b, link=None):
    (ma, oa, sa), (mb, ob, sb) = result_a, result_b
    print(f'\n==== COMPARE: {name_a} <-> {name_b} ====')
    same, how = same_object(result_a, result_b)
    if same:
        print(f'  SAME object ({how})' + (f': dev={sa[0]}:{sa[1]} inode={sa[2]}' if sa and sb else ''))
    else:
        print(f'  DIFFERENT objects ({how}): {name_a} {safe_text(ma.fstype)} dev={ma.dev} {oa!r}'
              f'{" ino=%d" % sa[2] if sa else ""} vs {name_b} {safe_text(mb.fstype)} dev={mb.dev} {ob!r}'
              f'{" ino=%d" % sb[2] if sb else ""}')
        print('  Writes through the two paths land in different places.')
    if ma.readonly != mb.readonly:
        print(f'  read-only status differs: {ma.ro_label} vs {mb.ro_label}')
    if 'overlay' in (ma.fstype, mb.fstype):
        print('  overlay involved: compare upperdir as well (see WRITE ROUTING).')
    if link:
        print(f'  propagation: {safe_text(link)}')


# --------------------------------------------------------------------------
# audits
# --------------------------------------------------------------------------

def parse_peer(spec):
    pid_str, sep, path = spec.partition(':')
    if not sep or not pid_str.isdecimal() or not path.startswith('/'):
        raise ValueError(f'--peer expects PID:/absolute/path (path inside that process root), got {spec!r}')
    return int(pid_str), path


def parse_audit(spec):
    host, sep, other = spec.partition('=')
    if not host.startswith('/') or (sep and not other.startswith('/')):
        raise ValueError(f'--audit-prefix expects /host/prefix[=/prefix/in/pid/view], got {spec!r}')
    host = posixpath.normpath(host)
    return host, posixpath.normpath(other) if sep else host


def audit_prefix(spec, host_view, pid, pid_view, no_resolve=False):
    host_prefix, pid_prefix = parse_audit(spec)
    # Resolve symlinks in each view (mountinfo lists resolved mountpoints).
    host_prefix = canonicalize(host_prefix, None, no_resolve)[0]
    pid_prefix = canonicalize(pid_prefix, pid, no_resolve)[0]
    mapped = host_prefix != pid_prefix
    print(f'\n==== MOUNT AUDIT: invoking view {safe_text(host_prefix)}  <->  PID {pid} {safe_text(pid_prefix)} ====')
    pairs = set()
    for m in host_view.mounts:
        if inside(m.where, host_prefix):
            pairs.add((m.where, rebase(m.where, host_prefix, pid_prefix)))
    for m in pid_view.mounts:
        if inside(m.where, pid_prefix):
            pairs.add((rebase(m.where, pid_prefix, host_prefix), m.where))
    counts = defaultdict(int)
    for host_path, pid_path in sorted(pairs):
        hm, pm = host_view.effective(host_path), pid_view.effective(pid_path)
        on_host, on_pid = hm.where == host_path, pm.where == pid_path
        if not on_host and not on_pid:
            continue  # only hidden mounts at this path; they show up in the HIDDEN section
        a, b = identity(host_view, None, host_path), identity(pid_view, pid, pid_path)
        same, how = same_object(a, b)
        label = safe_text(host_path) if not mapped else f'{safe_text(host_path)} = {safe_text(pid_path)}'
        link = link_status(hm, host_path, pid_view, pid_path, host_view)
        if on_host and on_pid:
            kind = 'OK' if same else 'DIVERGENT'
        elif on_host:
            kind = 'HOST-ONLY (same object)' if same else 'HOST-ONLY'
        else:
            kind = 'PID-ONLY (same object)' if same else 'PID-ONLY'
        counts[kind] += 1
        print(f'  {kind}: {label}')
        if kind == 'OK':
            continue
        print(f'    HOST #{hm.id}: {safe_text(hm.fstype)} {hm.source!r} dev={hm.dev} mount={safe_text(hm.where)} '
              f'FSROOT={hm.root!r} {hm.ro_label}')
        print(f'    PID  #{pm.id}: {safe_text(pm.fstype)} {pm.source!r} dev={pm.dev} mount={safe_text(pm.where)} '
              f'FSROOT={pm.root!r} {pm.ro_label}')
        print(f'    same object: {same} ({how}); propagation: {safe_text(link)}')
        if kind == 'DIVERGENT':
            FINDINGS.alert('both views have a mount here, but they serve different objects')
        elif kind == 'HOST-ONLY':
            FINDINGS.alert('the PID view does not see this host mount (made after the process '
                           'started without a propagation link, or unmounted only in that view)')
        elif kind == 'PID-ONLY':
            FINDINGS.alert('the PID view keeps a mount the host no longer has here: a retained / '
                           '"ghost" copy, or a mount made inside that namespace')
        else:
            print('    (an extra mount layer that reaches the same object, e.g. a bind of the '
                  'directory onto itself or the container\'s own volume bind)')
    summary = ', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'no mountpoints in scope'
    print(f'  summary: {summary}')


def parse_state_file(filename):
    try:
        with open(filename, encoding='utf-8', errors='surrogateescape') as file:
            lines = file.read().splitlines()
    except FileNotFoundError:
        return None
    state = {'RAMDISK': None, 'READONLY': None, 'PI_ERRORS': None,
             'targets': [], 'pi_errors': [], 'pi_uuid': {}}
    for line in lines:
        key, sep, value = line.partition('=')
        if not sep or line.startswith('#'):
            continue
        if key == 'TARGET' and value:
            state['targets'].append(('shared-bind', value))
        elif key == 'PI_TARGET' and value:
            state['targets'].append(('per-instance', value))
        elif key == 'PI_DERIVED':
            fields = value.split(' ')
            if len(fields) >= 5:
                state['pi_uuid'][fields[0]] = fields[4]
        elif key == 'PI_ERROR':
            state['pi_errors'].append(value)
        elif key in ('RAMDISK', 'READONLY', 'PI_ERRORS'):
            state[key] = value
    return state


def instance_of(path, volumes_root):
    """(uuid, path relative to the instance volume) for a path below VOLUMES_ROOT."""
    if not inside(path, volumes_root) or path == volumes_root:
        return None, None
    rest = posixpath.relpath(path, volumes_root).split('/', 1)
    return rest[0], rest[1] if len(rest) > 1 else ''


def audit_state_file(filename, host_view, pid, pid_view, volumes_root, no_resolve=False):
    print(f'\n==== SOULMASK TMPFS AUDIT: {safe_text(filename)} (volumes root {safe_text(volumes_root)}) ====')
    state = parse_state_file(filename)
    recorded = set()
    if state is None:
        print('  no state file: the service is off or never ran; scanning for leftover mounts only')
    else:
        # Compare in the kernel's spelling: resolved symlinks, no trailing slash.
        state['targets'] = [(kind, canonicalize(t, None, no_resolve)[0] if t.startswith('/') else t)
                            for kind, t in state['targets']]
        if state['RAMDISK'] and state['RAMDISK'].startswith('/'):
            state['RAMDISK'] = canonicalize(state['RAMDISK'], None, no_resolve)[0]
        recorded = {t for _kind, t in state['targets']}
        _audit_state_entries(state, host_view, pid, pid_view, volumes_root)
    _audit_leftovers(host_view, pid, pid_view, volumes_root, recorded)
    print('  The state file is declarative; the live mount tables above are the authority.')


def _audit_state_entries(state, host_view, pid, pid_view, volumes_root):
    ramdisk, readonly = state['RAMDISK'], state['READONLY']
    shared_targets = [t for kind, t in state['targets'] if kind == 'shared-bind']
    rm = None
    if ramdisk:
        rm = host_view.effective(ramdisk)
        if rm.where != ramdisk:
            if shared_targets:
                FINDINGS.alert(f'RAMDISK={ramdisk} is not a mountpoint on the host, '
                               f'but {len(shared_targets)} TARGET= entries are recorded', indent='  ')
            rm = None
        else:
            print(f'  RAMDISK {safe_text(ramdisk)}: #{rm.id} {safe_text(rm.fstype)} dev={rm.dev} {rm.ro_label} '
                  f'size={safe_text(rm.fsopt("size") or "default (50% of RAM)")} propagation={rm.propagation}')
            if rm.fstype != 'tmpfs':
                FINDINGS.alert(f'RAMDISK is {rm.fstype}, expected tmpfs', indent='  ')
            if readonly == '1' and not rm.readonly:
                FINDINGS.alert('READONLY=1 recorded but the RAMDISK is writable', indent='  ')
            if readonly == '0' and rm.readonly:
                FINDINGS.alert('READONLY=0 recorded but the RAMDISK is read-only (Wings chown walk will hit EROFS)',
                               indent='  ')
    if state['PI_ERRORS'] not in (None, '', '0'):
        FINDINGS.alert(f'PI_ERRORS={state["PI_ERRORS"]} recorded by setup', indent='  ')
        for text in state['pi_errors']:
            print(f'    PI_ERROR: {safe_text(text)}')

    for kind, target in state['targets']:
        if not target.startswith('/'):
            print(f'  IGNORED malformed target {target!r}')
            continue
        uuid, rel = instance_of(target, volumes_root)
        print(f'  {kind}: {safe_text(target)}')
        hm = host_view.effective(target)
        print(f'    HOST #{hm.id} {safe_text(hm.fstype)} {hm.source!r} dev={hm.dev} mount={safe_text(hm.where)} {hm.ro_label}')
        if hm.where != target:
            FINDINGS.alert('recorded target is not a mountpoint on the host')
        elif hm.fstype != 'tmpfs':
            FINDINGS.alert(f'host mount is {hm.fstype}, expected tmpfs')
        elif kind == 'shared-bind':
            if rm is not None and hm.dev != rm.dev:
                FINDINGS.alert(f'bind is not served by RAMDISK (dev {hm.dev} vs {rm.dev}): stale bind '
                               'from an older tmpfs instance?')
            if rm is not None and rel:
                expect = stat_identity(None, posixpath.join(ramdisk, rel))
                got = stat_identity(None, target)
                if expect and got and expect != got:
                    FINDINGS.alert(f'bind does not resolve to RAMDISK/{safe_text(rel)} (different inode)')
            if readonly == '1' and not hm.readonly:
                FINDINGS.alert('READONLY=1 but this bind is writable')
        elif kind == 'per-instance':
            uuid = state['pi_uuid'].get(target, uuid)
            tag = f'soulmask-pi-{uuid[:8]}' if uuid else None
            if tag and hm.source != tag:
                FINDINGS.alert(f'per-instance tmpfs source is {hm.source!r}, expected tag {tag!r}')
            if hm.readonly:
                FINDINGS.alert('per-instance tmpfs is read-only; the game cannot write its logs')
        if pid_view is not None:
            pm = pid_view.effective(target)
            same, how = same_object(identity(host_view, None, target), identity(pid_view, pid, target))
            print(f'    PID  #{pm.id} {safe_text(pm.fstype)} {pm.source!r} dev={pm.dev} mount={safe_text(pm.where)} {pm.ro_label}'
                  f'  same object: {same} ({how})')
            if hm.where == target and pm.where != target:
                FINDINGS.alert('the PID view does not see this mount (missing propagation)')
            elif same is False:
                FINDINGS.alert('host and PID view resolve this target to different objects (stale copy?)')

    groups = defaultdict(list)
    for kind, path in state['targets']:
        uuid, rel = instance_of(path, volumes_root)
        if uuid:
            groups[(kind, rel)].append(path)
    for (kind, rel), paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        objects = [stat_identity(None, p) for p in paths]
        if None in objects:
            print(f'  {kind} {safe_text(rel)}: identity could not be verified for all {len(paths)} instances')
        elif kind == 'shared-bind':
            if len(set(objects)) == 1:
                print(f'  OK shared {safe_text(rel)}: all {len(paths)} instances resolve to the SAME inode')
            else:
                FINDINGS.alert(f'shared {rel}: instances resolve to {len(set(objects))} different objects',
                               indent='  ')
        else:
            devices = {o[:2] for o in objects}
            if len(devices) == len(paths):
                print(f'  OK per-instance {safe_text(rel)}: all {len(paths)} instances have their own tmpfs')
            else:
                FINDINGS.alert(f'per-instance {rel}: instances share a superblock', indent='  ')


def _audit_leftovers(host_view, pid, pid_view, volumes_root, recorded):
    for m in host_view.mounts:
        if m.fstype != 'tmpfs' or not inside(m.where, volumes_root) or m.where in recorded:
            continue
        if m.id not in host_view.visible_ids():
            continue
        if m.source.startswith('soulmask-pi-'):
            FINDINGS.alert(f'host: tagged per-instance tmpfs {safe_text(m.where)} ({m.source}) is not recorded '
                           '(teardown finds it by its tag; setup refuses to keep it)', indent='  ')
        else:
            print(f'  NOTE host: unrecorded tmpfs mount {safe_text(m.where)} ({m.source!r}, #{m.id})')
    if pid_view is None:
        return
    for m in pid_view.mounts:
        if m.fstype != 'tmpfs' or not inside(m.where, volumes_root) or m.id not in pid_view.visible_ids():
            continue
        hm = host_view.effective(m.where)
        if hm.where == m.where and hm.dev == m.dev:
            continue
        FINDINGS.alert(f'PID {pid}: tmpfs {safe_text(m.where)} (#{m.id} {m.source!r} dev={m.dev}) has no matching host '
                       f'mount (host serves it from #{hm.id} {safe_text(hm.fstype)} at {safe_text(hm.where)}): ghost copy', indent='  ')


def predict(host_view, host_path, others):
    canon, warning = canonicalize(host_path, None)
    print(f'\n==== PREDICT: a new mount at {safe_text(canon)} in the invoking view ====')
    if warning:
        print(f'  NOTE: {safe_text(warning)}')
    h = host_view.effective(canon)
    rel = fs_object(h, canon)
    print(f'  it attaches to #{h.id} ({safe_text(h.fstype)} {h.source!r} at {safe_text(h.where)}) at {rel!r} of that filesystem;'
          f' #{h.id} is {h.propagation}')
    if h.shared is None:
        print('  -> no peer group: the mount stays in this mount namespace only'
              + (' (slaves receive, they never send)' if h.master is not None else ''))
        return
    host_ns = namespace_id(None)
    views = [host_view] + [v for v in others if v.pid is None or namespace_id(v.pid) != host_ns]
    received = propagation_receivers(h.shared, views)
    for view in views:
        print(f'  {view.label}:')
        hits = [m for m in view.mounts if m.id in received and m.id != h.id]
        if not hits:
            print('    no mount of this view is a peer or slave of that group: nothing appears')
        for m in sorted(hits, key=lambda m: m.where):
            path, status, _visible = place_event(view, m, rel)
            if path is None:
                continue
            print(f'    via #{m.id} {safe_text(m.where)} ({received[m.id][0]}): {safe_text(path)}: {safe_text(status)}')
    print('  A later umount at the original location is propagated to the same set; a non-lazy umount '
          'fails with EBUSY while any of those copies is busy. Namespaces not inspected here may receive it too.')


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def needs_host(args):
    return bool(args.host_path or args.audit_prefix or args.state_file or args.predict)


def read_tables(args):
    selected = read_mountinfo(args.pid)
    host = read_mountinfo() if needs_host(args) else None
    peers = [(pid, read_mountinfo(pid)) for pid, _path in map(parse_peer, args.peer)]
    state = None
    if args.state_file:
        try:
            with open(args.state_file, 'rb') as file:
                state = file.read()
        except FileNotFoundError:
            pass
    return selected, host, peers, state


def one_pass(args, tables):
    FINDINGS.alerts = 0
    selected, host, peers, _state = tables
    label = f'PID {args.pid}' if args.pid is not None else 'INVOKING PROCESS'
    sel_view = View(selected, label, args.pid)
    host_view = View(host, 'HOST (invoking process)') if host is not None else None
    opts = dict(submounts=args.submounts, all_hidden=args.all_hidden, no_resolve=args.no_resolve)
    canon, primary = report(sel_view, args.path, pid=args.pid, **opts)

    host_result = host_canon = None
    if args.host_path is not None:
        host_canon, host_result = report(host_view, args.host_path, **opts)
        link = link_status(host_result[0], host_canon, sel_view, canon, host_view)
        compare_results('HOST', host_result, label, primary, link)

    peer_views = []
    for (pid, mounts), spec in zip(peers, args.peer):
        _pid, path = parse_peer(spec)
        view = View(mounts, f'PEER PID {pid}', pid)
        peer_views.append(view)
        peer_canon, result = report(view, path, pid=pid, **opts)
        compare_results(label, primary, view.label, result)
        if host_result is not None:
            link = link_status(host_result[0], host_canon, view, peer_canon, host_view)
            compare_results('HOST', host_result, view.label, result, link)

    if args.audit_prefix:
        if args.pid is None:
            raise ValueError('--audit-prefix compares against --pid; give the PID to compare with')
        audit_prefix(args.audit_prefix, host_view, args.pid, sel_view, args.no_resolve)
    if args.state_file:
        audit_state_file(args.state_file, host_view, args.pid,
                         sel_view if args.pid is not None else None, args.volumes_root, args.no_resolve)
    for path in args.predict:
        others = ([sel_view] if args.pid is not None else []) + peer_views
        predict(host_view, path, others)
    if FINDINGS.alerts:
        print(f'\n{FINDINGS.alerts} ALERT(s).')
    return 3 if FINDINGS.alerts else 0


def main():
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name)
        if not isinstance(stream, TerminalSafeStream):
            setattr(sys, name, TerminalSafeStream(stream))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path', metavar='PATH', help='absolute path inside the selected process root')
    ap.add_argument('--pid', type=int, metavar='PID', help='inspect this process\'s root and mount namespace')
    ap.add_argument('--host-path', metavar='PATH', help='also analyse PATH in the invoking view and compare')
    ap.add_argument('--peer', action='append', default=[], metavar='PID:PATH',
                    help='also analyse PATH in another process\'s view and compare (repeatable)')
    ap.add_argument('--audit-prefix', metavar='HOST[=PIDPATH]',
                    help='compare all mountpoints under HOST (invoking view) with PIDPATH in --pid')
    ap.add_argument('--predict', action='append', default=[], metavar='PATH',
                    help='predict where a new mount at PATH (invoking view) appears (repeatable)')
    ap.add_argument('--state-file', metavar='FILE', help='audit a soulmask_tmpfs state file against live mounts')
    ap.add_argument('--volumes-root', default='/var/lib/pterodactyl/volumes', metavar='DIR',
                    help='instance volume root for --state-file (default: %(default)s)')
    ap.add_argument('--submounts', action='store_true', help='list visible mounts below PATH')
    ap.add_argument('--all-hidden', action='store_true', help='list every hidden mount, not only related ones')
    ap.add_argument('--no-resolve', action='store_true', help='do not resolve symlinks or check existence')
    ap.add_argument('--watch', type=float, metavar='SECONDS', help='re-run when an inspected mount table changes')
    ap.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    args = ap.parse_args()
    # Mount paths are bytes; undecodable ones arrive as surrogates and must print, not crash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    # Usage errors exit 2 before any output.
    if args.watch is not None and args.watch <= 0:
        ap.error('--watch must be positive')
    for name, value in [('PATH', args.path), ('--host-path', args.host_path),
                        ('--volumes-root', args.volumes_root)] + [('--predict', p) for p in args.predict]:
        if value is not None and not value.startswith('/'):
            ap.error(f'{name} must be an absolute path, got {value!r}')
    if args.audit_prefix and args.pid is None:
        ap.error('--audit-prefix compares against --pid; give the PID to compare with')
    try:
        for spec in args.peer:
            parse_peer(spec)
        if args.audit_prefix:
            parse_audit(args.audit_prefix)
    except ValueError as err:
        ap.error(str(err))
    if args.pid is not None and os.geteuid() != 0:
        print('NOTE: not root; another process\'s root is usually not accessible, identity checks then '
              'fall back to mountinfo only', file=sys.stderr)
    try:
        args.volumes_root = canonicalize(args.volumes_root, None, args.no_resolve)[0]
        tables = read_tables(args)
        status = one_pass(args, tables)
        if args.watch is None:
            return status
        print(f'\nWatching mount tables every {args.watch:g}s; Ctrl-C to stop.', flush=True)
        while True:
            time.sleep(args.watch)
            try:
                current = read_tables(args)
            except (FileNotFoundError, ProcessLookupError) as err:
                print(f'mounttrace: {safe_text(err.filename or err)} vanished (process exited or container restarted; '
                      'look up the new PID)', file=sys.stderr)
                return 1
            if current != tables:
                tables = current
                print(f'\n===== MOUNT TABLE CHANGE: {time.strftime("%Y-%m-%d %H:%M:%S")} =====', flush=True)
                one_pass(args, tables)
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as err:
        print(f'mounttrace: {safe_text(err)}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
