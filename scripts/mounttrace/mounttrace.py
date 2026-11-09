#!/usr/bin/env python3
"""Read-only mount view analysis for Linux.

Paths use one spelling everywhere: /absolute/path is the invoking process's
view, and PID:/absolute/path is that process's root and mount namespace.

Commands:
  explain [PID:]PATH       explain write routing, mount layers and hidden mounts
  compare REF OTHER...     compare one object across views (DIFFERENT exits 3)
  audit PREFIX PID:PREFIX compare mountpoints below host and process prefixes
  predict PATH PID...      predict propagation of a new host mount
  soulmask PID...          audit the state file and the volumes tree

Global options include --json, -v, --watch SECONDS, --no-probe and --version.
--no-probe skips path/filesystem probes; soulmask still reads its state file.
The tool never mounts, unmounts, enters namespaces or runs external commands.

Exit status: 0 clean report, 3 ALERT or DIFFERENT, 1 operational error, 2
usage error. See mounttrace.md next to this script for the full guide.
"""

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass, field
import fcntl
import json
import os
import posixpath
import re
import stat
import struct
import sys
import textwrap
import time

VERSION = '3.2.0'
DEFAULT_STATE_FILE = '/run/soulmask_tmpfs.state'
DEFAULT_VOLUMES_ROOT = '/var/lib/pterodactyl/volumes'
CANONICAL_WARNING_MISSING_LEAF = 'missing-leaf'
CANONICAL_WARNING_RESOLUTION_FAILED = 'resolution-failed'


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
            return 'unbindable'
        if self.propagate_from is not None:
            return f'from({self.propagate_from})'
        if self.shared is not None and self.master is not None:
            return f'shared({self.shared})+slave({self.master})'
        if self.shared is not None:
            return f'shared({self.shared})'
        if self.master is not None:
            return f'slave({self.master})'
        return 'private'

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
        if self.vfs_ro:
            return 'ro'
        if self.sb_ro:
            return 'ro:sb'
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


def canonicalize(path, pid, no_probe=False):
    """Resolve symlinks componentwise inside the selected process's root.

    Components are read through /proc/PID/root one at a time, so absolute
    symlinks restart at *that* process's root (realpath() on the
    /proc/PID/root/... string would escape to ours). A missing component keeps
    the rest of the path lexically (useful for a not-yet-created leaf). Return
    the normalized path, optional display warning, and optional warning kind.
    """
    if not path.startswith('/'):
        raise ValueError(f'expected an absolute path, got {path!r}')
    if no_probe:
        return posixpath.normpath(path), None, None
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
                    f'{candidate} does not exist; the rest of the path is analysed lexically',
                    CANONICAL_WARNING_MISSING_LEAF)
        except OSError as err:
            hint = ' (run as root to inspect another process root)' if isinstance(err, PermissionError) else ''
            return (posixpath.normpath(path),
                    f'could not resolve symlinks: {err}{hint}; lexical analysis only',
                    CANONICAL_WARNING_RESOLUTION_FAILED)
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
    return '/' + '/'.join(resolved), None, None


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


@dataclass
class Finding:
    level: str
    cls: str
    text: str
    next: str = None

    def as_dict(self):
        return {'level': self.level, 'class': self.cls, 'text': self.text, 'next': self.next}


@dataclass
class Report:
    command: str
    header: str
    verdict: dict
    findings: list = field(default_factory=list)
    kv: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)
    change_line: str = None

    def finding(self, level, cls, text, next_action=None):
        if level == 'ALERT' and next_action and any(
                item.level == 'ALERT' and item.cls == (cls or '') and item.next
                for item in self.findings):
            next_action = None
        self.findings.append(Finding(level, cls or '', text, next_action))

    def table(self, name, columns, rows, key=None):
        self.tables.append({'name': name, 'columns': list(columns), 'rows': list(rows), 'key': key})

    def exit_status(self):
        if any(f.level == 'ALERT' for f in self.findings):
            return 3
        return 0

    def as_dict(self):
        return {
            'schema': 1,
            'command': self.command,
            'header': self.header,
            'verdict': dict(self.verdict),
            'findings': [f.as_dict() for f in self.findings],
            'kv': self.kv,
            'tables': [{'name': t['name'], 'columns': t['columns'], 'rows': t['rows']}
                       for t in self.tables],
        }


def identity(view, pid, path, no_probe=False):
    m = view.effective(path)
    return m, fs_object(m, path), None if no_probe else stat_identity(pid, path)


def same_object(a, b):
    """Compare two identity() results -> (same, evidence), preferring stat identity."""
    ma, oa, sa = a
    mb, ob, sb = b
    if sa is not None and sb is not None:
        return sa == sb, 'stat dev+inode'
    if ma.dev != mb.dev:
        return False, 'different superblocks (mountinfo dev)'
    return oa == ob, 'mountinfo dev + filesystem path'


def mount_row(m, where=None):
    source = m.source
    if source.startswith('/dev/mapper/'):
        source = source[len('/dev/mapper/'):]
    if source == m.fstype:
        source = '-'
    return {
        'MOUNTPOINT': m.where if where is None else where,
        'ID': f'#{m.id}',
        'FSTYPE': m.fstype,
        'SOURCE': source or '-',
        'FSROOT': m.root,
        'RO': m.ro_label,
        'PROPAGATION': m.propagation,
    }


MOUNT_COLUMNS = ('MOUNTPOINT', 'ID', 'FSTYPE', 'SOURCE', 'FSROOT', 'RO', 'PROPAGATION')
AUDIT_NEXT = {
    'DIVERGENT': 'Check which mount should serve this path, then repair the bind or mount source.',
    'HOST-ONLY': 'Make the bind rslave and restart the process so later host mounts reach it.',
    'PID-ONLY': 'Check for a retained private copy; stop the process and restart it to drop a ghost.',
}


def output_width():
    raw = os.environ.get('COLUMNS')
    if raw and raw.isdecimal() and int(raw) > 0:
        width = int(raw)
    elif getattr(sys.stdout, 'isatty', lambda: False)():
        try:
            width = os.get_terminal_size(sys.stdout.fileno()).columns
        except (OSError, AttributeError, ValueError):
            width = 120
    else:
        width = 120
    return max(80, width)


def middle_cut(value, width):
    if len(value) <= width:
        return value
    if width <= 3:
        return '.' * width
    keep = width - 3
    left = (keep + 1) // 2
    right = keep - left
    return value[:left] + '...' + (value[-right:] if right else '')


def wrap_safe(value, width):
    value = safe_text(value)
    return textwrap.wrap(value, width=max(1, width), break_long_words=True,
                         break_on_hyphens=False, replace_whitespace=False) or ['']


def render_table(table, width, verbose=False):
    columns, rows = table['columns'], table['rows']
    if not rows:
        return wrap_safe(safe_text(table['name']) + ': none', width)
    lines = wrap_safe(safe_text(table['name']), width)
    cells = [[safe_text(row.get(col, '-')) for col in columns] for row in rows]
    natural = [max([len(col)] + [len(row[i]) for row in cells]) for i, col in enumerate(columns)]
    if verbose:
        lines.append(' '.join(col.ljust(natural[i]) for i, col in enumerate(columns)).rstrip())
        lines.extend(' '.join(value.ljust(natural[i]) for i, value in enumerate(row)).rstrip()
                     for row in cells)
        return lines
    key_idx = columns.index(table['key']) if table.get('key') in columns else None
    flex_names = {'SOURCE', 'FSROOT'}
    flex = [i for i, col in enumerate(columns) if col in flex_names]
    protected = {'ID', 'MOUNT', 'FSTYPE', 'RO', 'PROPAGATION', 'DEV:INODE'}
    gaps = 1
    widths = list(natural)
    max_table = width - gaps * (len(columns) - 1)
    def shrink(indices):
        indices = list(indices)
        while sum(widths) > max_table:
            candidates = [i for i in indices if widths[i] > len(columns[i])]
            if not candidates:
                return
            idx = max(candidates, key=lambda i: widths[i] - len(columns[i]))
            widths[idx] -= 1

    # Preserve source/root evidence first, then other flexible cells and finally
    # the row key. ID/FSTYPE/RO/propagation values stay intact and move to a
    # labeled continuation line only if the table itself is exceptionally wide.
    shrink(flex)
    shrink(i for i in range(len(widths))
           if i != key_idx and i not in flex and columns[i] not in protected)
    if key_idx is not None:
        shrink([key_idx])
    if sum(widths) > max_table:
        shrink(i for i, col in enumerate(columns) if col in protected)
    def cell(value, col, idx):
        if len(value) <= widths[idx]:
            return value
        if idx in flex or col in ('PATH', 'MOUNTPOINT', 'OBJECT', 'LANDS AT'):
            return middle_cut(value, widths[idx])
        if col in protected:
            return value
        return value[:widths[idx]]

    lines.append(' '.join(col.ljust(widths[i]) for i, col in enumerate(columns)).rstrip())
    for row in cells:
        values = [cell(value, columns[i], i) for i, value in enumerate(row)]
        overflow = []
        key_value = row[key_idx] if key_idx is not None else None
        if key_idx is not None and len(key_value) > widths[key_idx]:
            key_label = columns[key_idx]
            overflow.extend(wrap_safe(f'{key_label} {key_value}', width))
            values[key_idx] = ''
        for i, value in enumerate(row):
            if columns[i] in protected and len(value) > widths[i]:
                overflow.extend(wrap_safe(f'{columns[i]} {value}', width))
                values[i] = ''
        lines.append(' '.join(value.ljust(widths[i]) for i, value in enumerate(values)).rstrip())
        lines.extend(overflow)
    return lines


def render_report(report, as_json=False, verbose=False):
    if as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=True, separators=(',', ':')))
        return
    width = output_width()
    if report.change_line:
        for line in wrap_safe(report.change_line, width):
            print(line)
    header_text = safe_text(report.header)
    print(header_text if verbose else middle_cut(header_text, width))
    verdict = report.verdict
    answer = f"{verdict['status']} {verdict['text']}"
    for line in wrap_safe(answer, width):
        print(line)
    for finding in report.findings:
        prefix = finding.level + (f' {finding.cls}' if finding.cls else '') + ': '
        for i, line in enumerate(wrap_safe(finding.text, max(1, width - len(prefix)))):
            print((prefix if i == 0 else '  ') + line)
        if finding.next:
            for i, line in enumerate(wrap_safe(finding.next, max(1, width - 7))):
                print(('next: ' if i == 0 else '      ') + line)
    for table in report.tables:
        display_table = table
        if not verbose and table['name'] == 'PREDICTION':
            visible_rows = [row for row in table['rows'] if row.get('RESULT') != 'not reached']
            if not visible_rows:
                continue
            display_table = dict(table, rows=visible_rows)
        for line in render_table(display_table, width, verbose):
            print(line)
    if verbose:
        for item in report.kv.get('parent_chain', []):
            print(safe_text(f"parent #{item['ID']} {item['MOUNTPOINT']} {item['FSTYPE']} {item['SOURCE']}"))
        for key, title in (('mount_options', 'mount options'),
                           ('superblock_options', 'superblock options'),
                           ('overlay_lowerdir', 'overlay lowerdir')):
            if key in report.kv and report.kv[key] is not None:
                print(safe_text(f'{title}: {report.kv[key]}'))


def parse_path_spec(value):
    pid_text, sep, path = value.partition(':')
    if sep and pid_text.isdecimal():
        if int(pid_text) < 1:
            raise ValueError(f'PID must be positive, got {pid_text!r}')
        if not path.startswith('/'):
            raise ValueError(f'expected PID:/absolute/path, got {value!r}')
        return (int(pid_text), path)
    if not value.startswith('/'):
        raise ValueError(f'expected /absolute/path or PID:/absolute/path, got {value!r}')
    return (None, value)


def view_name(pid):
    return 'host' if pid is None else f'pid {pid}'


def view_for(spec, tables):
    pid, _path = spec
    mounts = tables['host'] if pid is None else tables['pids'][pid]
    return View(mounts, view_name(pid), pid)


def path_mount_result(spec, tables, no_probe):
    pid, path = spec
    view = view_for(spec, tables)
    canon, warning, _warning_kind = canonicalize(path, pid, no_probe)
    mount = view.effective(canon)
    result = (mount, fs_object(mount, canon), None if no_probe else stat_identity(pid, canon))
    return view, canon, warning, result


def explain_report(args, tables):
    spec = parse_path_spec(args.path)
    pid, path = spec
    view, canon, warning, result = path_mount_result(spec, tables, args.no_probe)
    mount, obj, _identity = result
    nsid = None if args.no_probe else namespace_id(pid)
    header = f'{view_name(pid)} {path}' + (f' mnt:{nsid}' if nsid else '')
    status = 'OK'
    text = f'writes go to {mount.fstype} via #{mount.id}, {mount.ro_label}'
    if mount.readonly:
        text = f'writes FAIL (EROFS) on {mount.ro_label} mount #{mount.id} ({mount.fstype})'
    report = Report('explain', header, {'status': status, 'text': text})
    report.kv.update({'view': view_name(pid), 'path': path, 'resolved_path': canon,
                      'object': obj, 'mount': mount_row(mount), 'mount_namespace': nsid})
    if canon != path:
        report.finding('NOTE', '', f'resolved path: {canon}')
    if warning:
        report.finding('NOTE', '', warning)
    steps = view.trace(canon)
    report.table('PATH WALK', MOUNT_COLUMNS,
                 [mount_row(m, where) for where, m, _covered in steps], key='MOUNTPOINT')
    boundaries = []
    if not args.no_probe:
        for index, (where, current, _covered) in enumerate(steps):
            if current.fstype != 'btrfs':
                continue
            upto = canon if index + 1 == len(steps) else (posixpath.dirname(steps[index + 1][0]) or '/')
            for boundary_path, kind, info in btrfs_boundaries(pid, current, upto):
                boundaries.append({'path': boundary_path, 'kind': kind, 'info': info})
    if boundaries:
        report.kv['btrfs_boundaries'] = boundaries
    if mount.fstype == 'overlay':
        lower = mount.fsopt('lowerdir')
        layers = len(lower.split(':')) if lower else 0
        report.kv['overlay'] = {'layers': layers, 'upperdir': mount.fsopt('upperdir')}
        if lower and not args.verbose:
            report.finding('NOTE', 'LAYER', f'overlay lowerdir collapsed ({layers} layers)')
        if mount.fsopt('upperdir'):
            report.finding(
                'NOTE', 'LAYER',
                f'overlay upperdir: {mount.fsopt("upperdir")} '
                '(writable backing layer, not a volume; value shown as recorded in mountinfo)')
    if mount.fstype == 'btrfs' and not args.no_probe:
        info = btrfs_subvol_info(pid, canon)
        report.kv['btrfs'] = info
        if info and info['readonly']:
            report.verdict = {'status': 'OK',
                              'text': f'writes FAIL (EROFS) btrfs subvolume {info["id"]} {info["name"]} '
                                      f'is READ-ONLY; mount #{mount.id} is {mount.ro_label}'}
            report.finding('NOTE', 'EROFS', 'read-only btrfs subvolume; writes fail although the mount is rw')
    if mount.readonly:
        report.finding('NOTE', 'EROFS', f'{mount.ro_label}: writes through this path fail with EROFS')
    hidden = [m for m in view.mounts if m.id not in view.visible_ids() and
              (inside(canon, m.where) or inside(m.where, canon))]
    if hidden:
        report.table('HIDDEN MOUNTS', MOUNT_COLUMNS, [mount_row(m) for m in hidden], key='MOUNTPOINT')
        for item in hidden:
            report.finding('NOTE', 'HIDDEN', view.hidden_reason(item))
    else:
        report.finding('NOTE', '', 'hidden: none on or below this path')
    if args.submounts:
        children = [m for m in view.mounts if m.id in view.visible_ids() and inside(m.where, canon)
                    and m.id != mount.id]
        report.table('VISIBLE SUBMOUNTS', MOUNT_COLUMNS,
                     [mount_row(m) for m in sorted(children, key=lambda m: (m.where, m.id))], key='MOUNTPOINT')
    if args.verbose:
        chain = view.parents(mount)
        report.kv['parent_chain'] = [mount_row(m) for m in chain]
        report.kv['mount_options'] = mount.vfs
        report.kv['superblock_options'] = mount.fsopts
        if mount.fstype == 'overlay':
            report.kv['overlay_lowerdir'] = mount.fsopt('lowerdir')
    for message in view.warnings:
        report.finding('NOTE', 'MOUNTINFO', message)
    return report


def link_label(host_mount, host_path, other_view, other_path, host_view):
    text = link_status(host_mount, host_path, other_view, other_path, host_view)
    if text.startswith('NOT LINKED'):
        return 'NOT LINKED'
    if 'TUCKED' in text:
        return 'tucked'
    if text.startswith('same mount object'):
        return 'linked'
    return 'linked'


def compare_report(args, tables):
    specs = [parse_path_spec(s) for s in [args.ref] + args.other]
    results = [path_mount_result(spec, tables, args.no_probe) for spec in specs]
    ref_view, ref_path, _warning, ref_result = results[0]
    rows, comparisons, mismatches = [], [], []
    for spec, (view, path, _warning, result) in zip(specs[1:], results[1:]):
        same, evidence = same_object(ref_result, result)
        row = {'VIEW': view.label, 'MOUNT': f'#{result[0].id}', 'FSTYPE': result[0].fstype,
               'SOURCE': result[0].source or '-', 'FSROOT': result[0].root,
               'OBJECT': result[1], 'DEV:INODE': f'{result[0].dev}:{result[2][2]}' if result[2] else result[0].dev,
               'RO': result[0].ro_label, 'PROPAGATION': result[0].propagation,
               'LINK': '-'}
        if specs[0][0] is None and spec[0] is not None:
            row['LINK'] = link_label(ref_result[0], ref_path, view, path, ref_view)
        row['SAME'] = 'same' if same else 'different'
        rows.append(row)
        item = {'view': view.label, 'path': path, 'same': 'same' if same else 'different',
                'evidence': evidence, 'mount': mount_row(result[0]), 'object': result[1]}
        comparisons.append(item)
        if not same:
            mismatches.append(item)
    status = 'DIFFERENT' if mismatches else 'OK'
    answer = (f'{len(mismatches)} of {len(rows)} compared views differ' if mismatches
              else f'objects are the same in all {len(rows) + 1} inspected views')
    report = Report('compare', f'compare {ref_path} in {ref_view.label}',
                    {'status': status, 'text': answer})
    report.kv.update({'reference': {'view': ref_view.label, 'path': ref_path,
                                   'mount': mount_row(ref_result[0]), 'object': ref_result[1]},
                      'comparisons': comparisons})
    if mismatches:
        report.finding('ALERT', 'DIFFERENT', 'writes through the compared paths land in different objects',
                       'Check the bind source and mount propagation for each view.')
    report.table('VIEWS', ('VIEW', 'MOUNT', 'FSTYPE', 'SOURCE', 'FSROOT', 'OBJECT',
                           'DEV:INODE', 'RO', 'PROPAGATION', 'LINK', 'SAME'), rows)
    return report


def path_pair_rows(host_prefix, pid_prefix, host_view, pid_view, pid, no_probe):
    pairs = set()
    for mount in host_view.mounts:
        if inside(mount.where, host_prefix):
            pairs.add((mount.where, rebase(mount.where, host_prefix, pid_prefix)))
    for mount in pid_view.mounts:
        if inside(mount.where, pid_prefix):
            pairs.add((rebase(mount.where, pid_prefix, host_prefix), mount.where))
    rows, counts = [], defaultdict(int)
    for host_path, pid_path in sorted(pairs):
        hm, pm = host_view.effective(host_path), pid_view.effective(pid_path)
        on_host, on_pid = hm.where == host_path, pm.where == pid_path
        if not on_host and not on_pid:
            continue
        same, how = same_object(identity(host_view, None, host_path, no_probe),
                                identity(pid_view, pid, pid_path, no_probe))
        if on_host and on_pid:
            kind = 'OK' if same else 'DIVERGENT'
        elif on_host:
            kind = 'LAYER' if same else 'HOST-ONLY'
        else:
            kind = 'LAYER' if same else 'PID-ONLY'
        rel = posixpath.relpath(host_path, host_prefix)
        if rel == '.':
            rel = '.'
        link = link_label(hm, host_path, pid_view, pid_path, host_view)
        counts[kind] += 1
        rows.append({'STATUS': kind, 'CLASS': kind, 'PATH': rel,
                     'HOST': f'#{hm.id} {hm.fstype} {hm.source or "-"}',
                     f'PID {pid}': f'#{pm.id} {pm.fstype} {pm.source or "-"}',
                     'SAME': 'same' if same else 'different', 'LINK': link,
                     '_evidence': how, '_host_path': host_path, '_pid_path': pid_path,
                     '_on_host': on_host, '_on_pid': on_pid})
    return rows, dict(counts)


def add_audit(report, host_prefix, pid, pid_prefix, host_view, pid_view, no_probe, verbose):
    host_prefix, warning_h, _warning_kind_h = canonicalize(host_prefix, None, no_probe)
    pid_prefix, warning_p, _warning_kind_p = canonicalize(pid_prefix, pid, no_probe)
    rows, counts = path_pair_rows(host_prefix, pid_prefix, host_view, pid_view, pid, no_probe)
    alerts = [row for row in rows if row['CLASS'] in ('HOST-ONLY', 'PID-ONLY', 'DIVERGENT')]
    shown = rows if verbose else [row for row in rows if row['CLASS'] != 'OK']
    collapsed_ok = counts.get('OK', 0) if not verbose else 0
    shown = [{key: value for key, value in row.items() if not key.startswith('_')} for row in shown]
    if collapsed_ok:
        shown.append({'STATUS': 'NOTE', 'CLASS': '-', 'PATH': f'{collapsed_ok} OK mountpoints',
                      'HOST': '-', f'PID {pid}': '-', 'SAME': 'same', 'LINK': 'use -v to list'})
    report.table('MOUNT AUDIT (paths relative to prefix)',
                 ('STATUS', 'CLASS', 'PATH', 'HOST', f'PID {pid}', 'SAME', 'LINK'),
                 shown, key='PATH')
    report.kv.setdefault('audits', []).append({'host_prefix': host_prefix, 'pid': pid,
                                               'pid_prefix': pid_prefix, 'counts': counts})
    if warning_h or warning_p:
        for warning in (warning_h, warning_p):
            if warning:
                report.finding('NOTE', '', warning)
    if not rows:
        report.finding('NOTE', '', 'no mountpoints in scope')
    for row in alerts:
        cls = row['CLASS']
        message = {'DIVERGENT': 'both views have a mount here, but they serve different objects',
                   'HOST-ONLY': 'the PID view does not see this host mount',
                   'PID-ONLY': 'the PID view has a mount with no matching host mount'}[cls]
        report.finding('ALERT', cls, f'{row["PATH"]}: {message}', AUDIT_NEXT[cls])
    report.kv['audits'][-1]['alerts'] = len(alerts)


def parse_state_file(filename, contents=None):
    if contents is None:
        try:
            with open(filename, encoding='utf-8', errors='surrogateescape') as file:
                contents = file.read()
        except FileNotFoundError:
            return None
    if isinstance(contents, bytes):
        contents = contents.decode('utf-8', 'surrogateescape')
    lines = contents.splitlines()
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


# --------------------------------------------------------------------------
# subcommands and report builders
# --------------------------------------------------------------------------

def audit_report(args, tables):
    host_pid, host_prefix = parse_path_spec(args.host_prefix)
    other_pid, other_prefix = parse_path_spec(args.pid_prefix)
    if host_pid is not None or other_pid is None:
        raise ValueError('audit expects HOST_PREFIX and PID:PREFIX')
    host_view = view_for((None, host_prefix), tables)
    pid_view = view_for((other_pid, other_prefix), tables)
    report = Report('audit', f'audit {host_prefix} in host vs {other_pid}:{other_prefix}',
                    {'status': 'OK', 'text': 'mountpoints match across the inspected views'})
    add_audit(report, host_prefix, other_pid, other_prefix, host_view, pid_view,
              args.no_probe, args.verbose)
    if any(f.level == 'ALERT' for f in report.findings):
        count = sum(f.level == 'ALERT' for f in report.findings)
        report.verdict = {'status': 'ALERT', 'text': f'{count} mountpoint differences need attention'}
    else:
        total = sum(report.kv['audits'][0]['counts'].values())
        report.verdict = {'status': 'OK', 'text': f'{total} mountpoints agree across the inspected views'}
    report.kv['prefixes'] = {'host': host_prefix, f'pid {other_pid}': other_prefix}
    return report


def _state_finding(report, text, cls='SOULMASK', next_action=None):
    report.finding('ALERT', cls, text, next_action or 'Compare the state file with the live mount tables before teardown.')


def add_soulmask_state(report, filename, contents, host_view, pid, pid_view, volumes_root, no_probe):
    state = parse_state_file(filename, contents)
    if state is None:
        report.finding('NOTE', '', 'no state file: scanning for leftover mounts only')
        targets, recorded = [], set()
    else:
        targets = [(kind, canonicalize(path, None, no_probe)[0] if path.startswith('/') else path)
                   for kind, path in state['targets']]
        state['targets'] = targets
        if state['RAMDISK'] and state['RAMDISK'].startswith('/'):
            state['RAMDISK'] = canonicalize(state['RAMDISK'], None, no_probe)[0]
        recorded = {path for _kind, path in targets}
        report.kv.setdefault('state', {})['entries'] = len(targets)
        ramdisk, readonly = state['RAMDISK'], state['READONLY']
        shared_targets = [path for kind, path in targets if kind == 'shared-bind']
        rm = host_view.effective(ramdisk) if ramdisk else None
        if rm and rm.where != ramdisk:
            if shared_targets:
                _state_finding(report, f'RAMDISK={ramdisk} is not a mountpoint on the host')
            rm = None
        elif rm:
            report.kv['state']['ramdisk'] = mount_row(rm)
            if rm.fstype != 'tmpfs':
                _state_finding(report, f'RAMDISK is {rm.fstype}, expected tmpfs')
            if readonly == '1' and not rm.readonly:
                _state_finding(report, 'READONLY=1 recorded but the RAMDISK is writable')
            if readonly == '0' and rm.readonly:
                _state_finding(report, 'READONLY=0 recorded but the RAMDISK is read-only')
        if state['PI_ERRORS'] not in (None, '', '0'):
            _state_finding(report, f'PI_ERRORS={state["PI_ERRORS"]} recorded by setup')
            for message in state['pi_errors']:
                report.finding('NOTE', 'PI_ERROR', message)
        for kind, target in targets:
            if not target.startswith('/'):
                report.finding('NOTE', 'IGNORED', f'malformed target {target!r}')
                continue
            uuid, rel = instance_of(target, volumes_root)
            hm = host_view.effective(target)
            if hm.where != target:
                _state_finding(report, f'{kind} target {target} is not a host mountpoint')
            elif hm.fstype != 'tmpfs':
                _state_finding(report, f'{kind} target {target} is {hm.fstype}, expected tmpfs')
            elif kind == 'shared-bind':
                if rm is not None and hm.dev != rm.dev:
                    _state_finding(report, f'{target} is not served by the recorded RAMDISK')
                if not no_probe and rm is not None and rel:
                    expected = stat_identity(None, posixpath.join(ramdisk, rel))
                    actual = stat_identity(None, target)
                    if expected and actual and expected != actual:
                        _state_finding(report, f'{target} resolves to a different inode than RAMDISK/{rel}')
                if readonly == '1' and not hm.readonly:
                    _state_finding(report, f'{target} is writable but READONLY=1')
            elif kind == 'per-instance':
                uuid = state['pi_uuid'].get(target, uuid)
                tag = f'soulmask-pi-{uuid[:8]}' if uuid else None
                if tag and hm.source != tag:
                    _state_finding(report, f'{target} has source {hm.source!r}, expected {tag!r}')
                if hm.readonly:
                    _state_finding(report, f'{target} is read-only; the game cannot write its logs')
            if pid_view is not None:
                pm = pid_view.effective(target)
                same, how = same_object(identity(host_view, None, target, no_probe),
                                        identity(pid_view, pid, target, no_probe))
                report.kv.setdefault('targets', []).append({
                    'kind': kind, 'path': target, 'host_mount': mount_row(hm),
                    'pid_mount': mount_row(pm), 'same': 'same' if same else 'different',
                    'evidence': how,
                })
                if hm.where == target and pm.where != target:
                    _state_finding(report, f'{target} is missing from pid {pid} (missing propagation)')
                elif not same:
                    _state_finding(report, f'{target} resolves to different objects in host and pid {pid}')
        if no_probe:
            report.finding('NOTE', '', 'path identity probes skipped (--no-probe)')
        else:
            groups = defaultdict(list)
            for kind, path in targets:
                uuid, rel = instance_of(path, volumes_root)
                if uuid:
                    groups[(kind, rel)].append(path)
            for (kind, rel), paths in sorted(groups.items()):
                if len(paths) < 2:
                    continue
                objects = [stat_identity(None, path) for path in paths]
                if None in objects:
                    report.finding('NOTE', 'IDENTITY', f'{kind} {rel}: identity could not be verified')
                elif kind == 'shared-bind' and len(set(objects)) != 1:
                    _state_finding(report, f'shared {rel}: instances resolve to different objects')
                elif kind == 'per-instance' and len({item[:2] for item in objects}) != len(paths):
                    _state_finding(report, f'per-instance {rel}: instances share a superblock')
                elif kind == 'shared-bind':
                    report.finding('NOTE', 'OK', f'shared {rel}: all {len(paths)} instances resolve to the same inode')
                else:
                    report.finding('NOTE', 'OK', f'per-instance {rel}: all {len(paths)} instances have their own tmpfs')
    leftover_rows = []
    for mount in host_view.mounts:
        if (mount.fstype != 'tmpfs' or not inside(mount.where, volumes_root) or mount.where in recorded
                or mount.id not in host_view.visible_ids()):
            continue
        leftover_rows.append(mount_row(mount))
        if mount.source.startswith('soulmask-pi-'):
            _state_finding(report, f'tagged per-instance tmpfs {mount.where} is not recorded')
        else:
            report.finding('NOTE', 'LEFTOVER', f'unrecorded tmpfs mount {mount.where} ({mount.source})')
    if pid_view is not None:
        for mount in pid_view.mounts:
            if (mount.fstype != 'tmpfs' or not inside(mount.where, volumes_root)
                    or mount.id not in pid_view.visible_ids()):
                continue
            host_mount = host_view.effective(mount.where)
            if host_mount.where != mount.where or host_mount.dev != mount.dev:
                _state_finding(report, f'pid {pid} tmpfs {mount.where} has no matching host mount (ghost copy)')
    if leftover_rows:
        report.table('UNRECORDED HOST TMPFS', MOUNT_COLUMNS, leftover_rows, key='MOUNTPOINT')
    report.kv.setdefault('state', {})['recorded_mounts'] = sorted(recorded)


def soulmask_report(args, tables):
    report = Report('soulmask', f'soulmask audit {args.volumes_root}',
                    {'status': 'OK', 'text': 'state and volume mount audits are clean'})
    volumes_root, warning, warning_kind = canonicalize(args.volumes_root, None, args.no_probe)
    report.kv.update({'state_file': args.state_file, 'volumes_root': volumes_root,
                      'pids': list(args.pids)})
    if warning and warning_kind != CANONICAL_WARNING_MISSING_LEAF:
        report.finding('NOTE', '', warning)
    state_data = tables.get('state')
    for pid in args.pids:
        host_view = view_for((None, volumes_root), tables)
        pid_view = view_for((pid, volumes_root), tables)
        add_audit(report, volumes_root, pid, volumes_root, host_view, pid_view,
                  args.no_probe, args.verbose)
        add_soulmask_state(report, args.state_file, state_data, host_view, pid, pid_view,
                           volumes_root, args.no_probe)
    alerts = sum(f.level == 'ALERT' for f in report.findings)
    report.verdict = ({'status': 'ALERT', 'text': f'{alerts} findings need attention'} if alerts
                      else {'status': 'OK', 'text': 'state and volume mount audits are clean'})
    return report


def predict_report(args, tables):
    _pid, path = parse_path_spec(args.path)
    if _pid is not None:
        raise ValueError('predict PATH must be in the host view')
    host_view = view_for((None, path), tables)
    canon, warning, warning_kind = canonicalize(path, None, args.no_probe)
    serving = host_view.effective(canon)
    rel = fs_object(serving, canon)
    views = [view_for((pid, '/'), tables) for pid in args.pids]
    if not args.no_probe:
        host_ns = namespace_id(None)
        views = [view for view in views if namespace_id(view.pid) != host_ns]
    targets = [host_view] + views
    received = propagation_receivers(serving.shared, targets) if serving.shared is not None else {}
    rows = []
    appears = 0
    for view in views:
        hits = [mount for mount in view.mounts if mount.id in received and mount.id != serving.id]
        if not hits:
            rows.append({'VIEW': view.label, 'RESULT': 'not reached', 'VIA': '-', 'LANDS AT': '-'})
        for mount in sorted(hits, key=lambda item: item.where):
            target, status, _visible = place_event(view, mount, rel)
            if target is None:
                continue
            result = 'APPEARS' if status == 'APPEARS' else ('tucked' if status.startswith('TUCKED') else status)
            appears += result == 'APPEARS'
            rows.append({'VIEW': view.label, 'RESULT': result,
                         'VIA': f'#{mount.id} {received[mount.id][0]}', 'LANDS AT': target})
    answer = f'appears in {appears} of {len(views)} inspected views'
    if serving.shared is None:
        answer = 'stays in the host view; this mount has no shared peer group'
    report = Report('predict', f'predict a new host mount at {canon}', {'status': 'OK', 'text': answer})
    report.kv.update({'path': canon, 'mount': mount_row(serving), 'filesystem_path': rel,
                      'shared_group': serving.shared, 'views': rows})
    if warning and warning_kind != CANONICAL_WARNING_MISSING_LEAF:
        report.finding('NOTE', '', warning)
    not_reached = sum(row['RESULT'] == 'not reached' for row in rows)
    if not_reached:
        report.finding('NOTE', '', f'no new mount in {not_reached} inspected view'
                       f'{"s" if not_reached != 1 else ""}')
    report.table('PREDICTION', ('VIEW', 'RESULT', 'VIA', 'LANDS AT'), rows, key='LANDS AT')
    return report


def one_pass(args, tables):
    if args.command == 'explain':
        return explain_report(args, tables)
    if args.command == 'compare':
        return compare_report(args, tables)
    if args.command == 'audit':
        return audit_report(args, tables)
    if args.command == 'predict':
        return predict_report(args, tables)
    return soulmask_report(args, tables)


def all_pids(args):
    if args.command == 'explain':
        pid, _path = parse_path_spec(args.path)
        return [] if pid is None else [pid]
    if args.command == 'compare':
        return sorted({pid for pid, _path in map(parse_path_spec, [args.ref] + args.other) if pid is not None})
    if args.command == 'audit':
        return [parse_path_spec(args.pid_prefix)[0]]
    if args.command in ('predict', 'soulmask'):
        return list(args.pids)
    return []


def needs_host(args):
    if args.command in ('audit', 'predict', 'soulmask'):
        return True
    if args.command == 'explain':
        return parse_path_spec(args.path)[0] is None
    return any(pid is None for pid, _path in map(parse_path_spec, [args.ref] + args.other))


def read_tables(args):
    pids = all_pids(args)
    host = read_mountinfo() if needs_host(args) else None
    pid_tables = {pid: read_mountinfo(pid) for pid in pids}
    state = None
    if args.command == 'soulmask':
        try:
            with open(args.state_file, 'rb') as file:
                state = file.read()
        except FileNotFoundError:
            pass
    return {'host': host, 'pids': pid_tables, 'state': state}


def mount_changes(old, new):
    changes = []
    names = ['host'] + [f'pid {pid}' for pid in sorted(set(old['pids']) | set(new['pids']))]
    for name in names:
        if name == 'host':
            before, after = old['host'] or [], new['host'] or []
        else:
            pid = int(name.split()[1])
            before, after = old['pids'].get(pid, []), new['pids'].get(pid, [])
        by_old, by_new = {m.id: m for m in before}, {m.id: m for m in after}
        added = sorted(set(by_new) - set(by_old))
        removed = sorted(set(by_old) - set(by_new))
        modified = sorted(mid for mid in set(by_old) & set(by_new) if by_old[mid] != by_new[mid])
        if added or removed or modified:
            bits = [f'+#{mid} {by_new[mid].where}' for mid in added]
            bits += [f'-#{mid} {by_old[mid].where}' for mid in removed]
            bits += [f'~#{mid} {by_new[mid].where}' for mid in modified]
            changes.append({'view': name, 'added': added, 'removed': removed,
                            'modified': modified, 'text': ' '.join(bits)})
    if old.get('state') != new.get('state'):
        changes.append({'view': 'state file', 'added': [], 'removed': [], 'modified': [],
                        'text': 'state file changed'})
    return changes


def render_change_line(changes):
    summaries = [f'{item["view"]} {item["text"]}' for item in changes]
    return f'--- {time.strftime("%Y-%m-%d %H:%M:%S")} mount tables changed: ' + '; '.join(summaries)


def selected_pid_specs(args):
    """Return one PID for each process-view argument in the selected command."""
    if args.command == 'explain':
        pid, _path = parse_path_spec(args.path)
        return [] if pid is None else [pid]
    if args.command == 'compare':
        return [pid for pid, _path in map(parse_path_spec, [args.ref] + args.other)
                if pid is not None]
    if args.command == 'audit':
        return [parse_path_spec(args.pid_prefix)[0]]
    if args.command in ('predict', 'soulmask'):
        return list(args.pids)
    return []


def error_message(error, args):
    filename = getattr(error, 'filename', None) or ''
    match = re.search(r'/proc/(\d+)(?:/|$)', str(filename))
    if match:
        pid = match.group(1)
        if isinstance(error, PermissionError):
            return f'cannot inspect pid {pid}: permission denied; run as root to inspect another process'
        if isinstance(error, (FileNotFoundError, ProcessLookupError)):
            return f'pid {pid} vanished (process exited or container restarted; look up the new PID)'
    selected_pids = selected_pid_specs(args)
    if isinstance(error, (FileNotFoundError, ProcessLookupError)) and selected_pids:
        selected = (f'pid {selected_pids[0]}' if len(selected_pids) == 1
                    else 'a selected process')
        return f'{selected} vanished (process exited or container restarted; look up the new PID)'
    if isinstance(error, PermissionError) and not filename and selected_pids:
        selected = (f'pid {selected_pids[0]}' if len(selected_pids) == 1
                    else 'a selected process')
        return f'cannot inspect {selected}: permission denied; run as root to inspect another process'
    if isinstance(error, PermissionError):
        return f'permission denied: {filename or error.strerror or error}'
    return safe_text(error)


def make_parser():
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                        help='write one JSON report per pass')
    parent.add_argument('-v', '--verbose', action='store_true', default=argparse.SUPPRESS,
                        help='expand collapsed details and disable truncation')
    parent.add_argument('--watch', type=float, metavar='SECONDS', default=argparse.SUPPRESS,
                        help='repeat when inspected mount data changes')
    parent.add_argument('--no-probe', action='store_true', default=argparse.SUPPRESS,
                        help='skip path and filesystem probes (soulmask still reads its state file)')
    ap = argparse.ArgumentParser(description='Explain mount views, compare paths, audit trees, predict propagation, and inspect Soulmask state.')
    ap.add_argument('--json', action='store_true', help='write one JSON report per pass')
    ap.add_argument('-v', '--verbose', action='store_true', help='expand collapsed details and disable truncation')
    ap.add_argument('--watch', type=float, metavar='SECONDS', help='repeat when inspected mount data changes')
    ap.add_argument('--no-probe', action='store_true',
                    help='skip path and filesystem probes (soulmask still reads its state file)')
    ap.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    commands = ap.add_subparsers(dest='command', required=True)
    explain = commands.add_parser('explain', parents=[parent], help='explain one path in one view')
    explain.add_argument('path', metavar='[PID:]PATH')
    explain.add_argument('--submounts', action='store_true', help='list visible mounts below the path')
    compare = commands.add_parser('compare', parents=[parent], help='compare one path across views')
    compare.add_argument('ref', metavar='REF')
    compare.add_argument('other', nargs='+', metavar='OTHER')
    audit = commands.add_parser('audit', parents=[parent], help='compare mountpoints below host and PID prefixes')
    audit.add_argument('host_prefix', metavar='PREFIX')
    audit.add_argument('pid_prefix', metavar='PID:PREFIX')
    predict = commands.add_parser('predict', parents=[parent], help='predict propagation of a new host mount')
    predict.add_argument('path', metavar='PATH')
    predict.add_argument('pids', nargs='+', type=int, metavar='PID')
    soulmask = commands.add_parser('soulmask', parents=[parent], help='audit Soulmask state and volume mounts')
    soulmask.add_argument('pids', nargs='+', type=int, metavar='PID')
    soulmask.add_argument('--state-file', default=DEFAULT_STATE_FILE, metavar='FILE',
                          help='Soulmask state file (default: %(default)s)')
    soulmask.add_argument('--volumes-root', default=DEFAULT_VOLUMES_ROOT, metavar='DIR',
                          help='volume tree to audit (default: %(default)s)')
    return ap


def validate_args(ap, args):
    if args.watch is not None and args.watch <= 0:
        ap.error('--watch must be positive')
    try:
        if args.command == 'explain':
            parse_path_spec(args.path)
        elif args.command == 'compare':
            for value in [args.ref] + args.other:
                parse_path_spec(value)
        elif args.command == 'audit':
            parse_path_spec(args.host_prefix)
            parse_path_spec(args.pid_prefix)
            if not args.host_prefix.startswith('/'):
                ap.error('audit PREFIX must be an absolute host path')
            if not re.match(r'^\d+:/', args.pid_prefix):
                ap.error('audit PID:PREFIX must name a PID and absolute path')
        elif args.command == 'predict':
            if not args.path.startswith('/'):
                ap.error('predict PATH must be absolute')
            if any(pid < 1 for pid in args.pids):
                ap.error('PID values must be positive')
        elif args.command == 'soulmask':
            if not args.volumes_root.startswith('/'):
                ap.error('--volumes-root must be absolute')
            if any(pid < 1 for pid in args.pids):
                ap.error('PID values must be positive')
    except ValueError as error:
        ap.error(str(error))


def main():
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name)
        if not isinstance(stream, TerminalSafeStream):
            setattr(sys, name, TerminalSafeStream(stream))
    ap = make_parser()
    args = ap.parse_args()
    # Root options may occur before the subcommand; child options follow it.
    for name, default in (('json', False), ('verbose', False), ('watch', None), ('no_probe', False)):
        if not hasattr(args, name):
            setattr(args, name, default)
    validate_args(ap, args)
    try:
        tables = read_tables(args)
        report = one_pass(args, tables)
        render_report(report, args.json, args.verbose)
        last_status = report.exit_status()
        if args.watch is None:
            return last_status
        while True:
            time.sleep(args.watch)
            current = read_tables(args)
            if current != tables:
                changes = mount_changes(tables, current)
                tables = current
                report = one_pass(args, tables)
                report.change_line = render_change_line(changes)
                report.kv['mount_changes'] = changes
                render_report(report, args.json, args.verbose)
                last_status = report.exit_status()
                sys.stdout.flush()
    except KeyboardInterrupt:
        return last_status if 'last_status' in locals() else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f'mounttrace: {error_message(error, args)}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
