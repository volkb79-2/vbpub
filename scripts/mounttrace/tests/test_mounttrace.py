#!/usr/bin/env python3
"""Tests for mounttrace.py against REAL kernel mount topologies.

Every scenario runs inside a throwaway user + mount namespace
(`unshare -rm`), so no root and no change to the host's mounts are needed:
the tmpfs mounts vanish with the namespace. A "container" is a further
`unshare -m --propagation <mode>` child running `sleep`, whose PID is handed
to mounttrace via --pid, exactly like a Docker container's main PID.

The scenarios double as executable evidence for the kernel behaviour that
mounttrace.md describes (ghost copies, slave vs private vs shared, tucking,
EBUSY on busy slave copies, mounting over a bind source).

VmRootTests (real btrfs on a loop device, real Docker bind propagation) need
root and a kernel of their own: they run ONLY inside the QEMU guest of
testing/vm/run-vm-tests.sh, whose sealed base carries
/etc/mounttrace-test-vm and which sets MOUNTTRACE_VM=1 and
MOUNTTRACE_REQUIRE_VM_TESTS=1 (then a skip is a failure). They check the
marker and systemd-detect-virt themselves, so they never touch another host.

Supported full-suite command: testing/vm/run-vm-tests.sh (everything, inside the VM)
"""

import importlib.util
import io
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
MT = os.path.join(os.path.dirname(HERE), 'mounttrace.py')
TEST_VM_MARKER = '/etc/mounttrace-test-vm'

PRELUDE = r'''
set -euo pipefail
MT="$1"; T="$2/fs"; mkdir -p "$T"
mount -t tmpfs hostfs "$T"
mount --make-shared "$T"
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT
spawn() {  # spawn PROPAGATION 'setup commands run inside the new namespace'
  local ready="$T.ready.$RANDOM$RANDOM"
  unshare -m --propagation "$1" bash -c "set -e; $2"$'\n'"touch '$ready'; exec sleep 300" &
  SPAWNED=$!; PIDS+=("$SPAWNED")
  for _ in $(seq 200); do [ -e "$ready" ] && break; sleep 0.05; done
  [ -e "$ready" ] || { echo "spawn failed"; exit 99; }
}
mt() { local rc=0; python3 "$MT" "$@" || rc=$?; echo "RC=$rc"; }
wait_for() {  # wait_for 'shell condition' WHAT: bounded (60 s), fails the scenario loudly
  for _ in $(seq 600); do eval "$1" && return 0; sleep 0.1; done
  echo "timed out waiting for $2"; exit 98
}
'''


def userns_available():
    if not shutil.which('unshare'):
        return False
    probe = subprocess.run(['unshare', '-rm', 'sh', '-c', 'mount -t tmpfs x /mnt 2>/dev/null || '
                            'mount -t tmpfs x /tmp'], capture_output=True)
    return probe.returncode == 0


def run_group(cmd, timeout):
    """subprocess.run in its own process group; a timeout kills the whole group
    (the scenario's background sleeps too, not only the direct child)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        out, err = proc.communicate()
        raise AssertionError(f'scenario timed out after {timeout}s:\n{out}{err}')
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run(body):
    base = tempfile.mkdtemp(prefix='mounttrace-test-')
    try:
        proc = run_group(['unshare', '-rm', 'bash', '-c', PRELUDE + body, 'test', MT, base], 120)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f'scenario failed (rc={proc.returncode}):\n{out}')
    return out


def vm_root_unavailable():
    """None when root-in-the-test-VM scenarios may run, else the reason."""
    if os.geteuid() != 0:
        return 'needs root inside the test VM'
    if not shutil.which('systemd-detect-virt'):
        return 'systemd-detect-virt missing; cannot prove this is the test VM'
    virt = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True).stdout.strip()
    if virt not in ('qemu', 'kvm'):
        return f'not a QEMU/KVM guest ({virt or "none"}); refusing to touch this kernel'
    try:
        with open(TEST_VM_MARKER, encoding='ascii') as marker_file:
            marker = marker_file.read().strip()
    except OSError:
        return f'{TEST_VM_MARKER} marker missing or unreadable; refusing to touch this kernel'
    if not marker:
        return f'{TEST_VM_MARKER} marker is empty; refusing to touch this kernel'
    if os.environ.get('MOUNTTRACE_VM') != '1':
        return 'MOUNTTRACE_VM=1 is set only inside the test VM (testing/vm/run-vm-tests.sh)'
    return None


class VmGuardTests(unittest.TestCase):
    def test_kvm_without_marker_refuses_root_tests_with_marker_reason(self):
        with tempfile.TemporaryDirectory(prefix='mounttrace-marker-test-') as tmp:
            marker = os.path.join(tmp, 'mounttrace-test-vm')
            with patch(__name__ + '.TEST_VM_MARKER', marker), \
                    patch.object(os, 'geteuid', return_value=0), \
                    patch.object(shutil, 'which', return_value='/usr/bin/systemd-detect-virt'), \
                    patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess(
                        ['systemd-detect-virt', '--vm'], 0, 'qemu\n', '')), \
                    patch.dict(os.environ, {'MOUNTTRACE_VM': '1'}, clear=True):
                reason = vm_root_unavailable()
        self.assertIn(marker, reason)
        self.assertIn('marker', reason)


def run_root(body, host_ns=False, timeout=900, containers=()):
    """Root scenario in the VM. host_ns=False: a private mount namespace (mounts die with it).
    host_ns=True: the guest's initial namespace (needed when dockerd must see the paths);
    the prelude's trap unmounts everything below the scenario dir.
    CONTAINERS: names the scenario passes to `docker run --name`, as "$CTR_<name>";
    they are removed by exact name afterwards, also when the scenario failed midway."""
    base = tempfile.mkdtemp(prefix='mounttrace-vm-')
    script = PRELUDE.replace('trap cleanup EXIT',
                             'trap \'cleanup; umount -R "$T" 2>/dev/null || umount -lR "$T" 2>/dev/null || true\' EXIT') + body
    cmd = ['bash', '-c', script, 'test', MT, base]
    if not host_ns:
        cmd = ['unshare', '-m', '--propagation', 'private'] + cmd
    names = {f'CTR_{c}': f'mounttrace-test-{os.getpid()}-{c}' for c in containers}
    if names:
        cmd = ['env'] + [f'{k}={v}' for k, v in names.items()] + cmd
    try:
        proc = run_group(cmd, timeout)
    finally:
        for name in names.values():
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['umount', '-R', base], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f'scenario failed (rc={proc.returncode}):\n{out}')
    return out


def load_module():
    spec = importlib.util.spec_from_file_location('mounttrace', MT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.mt = load_module()

    def test_escapes_optional_fields_and_empty_source(self):
        line = (r'36 35 98:0 /mnt\040dir /a\011b rw,noatime master:1 shared:7 - tmpfs  rw,size=1k')
        m = self.mt.parse_mountinfo_line(line)
        self.assertEqual((m.id, m.parent, m.dev), (36, 35, '98:0'))
        self.assertEqual(m.root, '/mnt dir')
        self.assertEqual(m.where, '/a\tb')
        self.assertEqual(m.source, '')
        self.assertEqual(m.fstype, 'tmpfs')
        self.assertEqual((m.shared, m.master), (7, 1))
        self.assertTrue(m.propagation.startswith('shared+slave'))
        self.assertEqual(m.fsopt('size'), '1k')

    def test_desc_renders_option_controls_visibly_without_mutating_options(self):
        raw = 'rw,mode=1777,tag=\x1b\x07\nnext'
        m = self.mt.parse_mountinfo_line(f'20 1 0:5 / / rw - tmpfs src {raw}')
        output = self.mt.desc(m, with_opts=True)
        self.assertIn(r'mount options: rw', output)
        self.assertIn(r'superblock options: rw,mode=1777,tag=\x1b\x07\x0anext', output)
        self.assertEqual(m.fsopts, raw)


class TopologyModelTests(unittest.TestCase):
    def setUp(self):
        self.mt = load_module()

    def test_sibling_mount_prefix_does_not_include_data2(self):
        parse = self.mt.parse_mountinfo_line
        view = self.mt.View([parse('1 1 0:1 / / rw - ext4 root rw'),
                             parse('2 1 0:2 / /data rw - tmpfs data rw'),
                             parse('3 1 0:3 / /data2 rw - tmpfs data2 rw')])
        self.assertEqual(view.effective('/data2').id, 3)
        self.assertFalse(self.mt.inside('/data2', '/data'))
        self.assertFalse(self.mt.inside('/data2/logs', '/data'))


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.mt = load_module()

    def test_watch_reports_process_lookup_error_as_vanished_pid(self):
        mt = self.mt
        tables = ([], None, [], None)
        calls = 0

        def read_tables(_args):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ProcessLookupError(3, 'No such process', '/proc/42/mountinfo')
            return tables

        mt.read_tables = read_tables
        mt.one_pass = lambda _args, _tables: 0
        old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            sys.argv = ['mounttrace', '/', '--pid', '42', '--watch', '0.1']
            sys.stdout, sys.stderr = stdout, stderr
            with patch.object(mt.time, 'sleep', return_value=None):
                status = mt.main()
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
        self.assertEqual(status, 1)
        self.assertIn('vanished', stderr.getvalue())
        self.assertIn('/proc/42/mountinfo', stderr.getvalue())

    def test_no_optional_fields_and_readonly_layers(self):
        m = self.mt.parse_mountinfo_line('20 1 0:5 / / ro,relatime - ext4 /dev/sda1 rw')
        self.assertEqual(m.optional, ())
        self.assertEqual(m.propagation, 'private')
        self.assertEqual(m.ro_label, 'RO(mount)')
        m = self.mt.parse_mountinfo_line('20 1 0:5 / / rw - tmpfs t ro,size=1k')
        self.assertEqual(m.ro_label, 'RO(superblock)')

    def test_receivers_follow_shared_slaves(self):
        p = self.mt.parse_mountinfo_line
        host = [p('1 0 0:1 / / rw shared:1 - ext4 a rw'), p('2 1 0:2 / /x rw shared:5 - tmpfs b rw')]
        ctr = [p('10 9 0:1 / / rw master:1 - ext4 a rw'),
               p('11 10 0:1 /x /y rw shared:8 master:1 - ext4 a rw'),
               p('12 10 0:1 /x /z rw master:8 - ext4 a rw')]
        views = [self.mt.View(host), self.mt.View(ctr)]
        got = self.mt.propagation_receivers(1, views)
        self.assertEqual(set(got), {1, 10, 11, 12})
        self.assertNotIn(2, got)

    def test_receiver_direction_and_propagate_from(self):
        p = self.mt.parse_mountinfo_line
        host = self.mt.View([p('1 0 0:1 / / rw shared:1 - tmpfs a rw')])
        other = self.mt.View([p('4 4 0:9 / / rw - tmpfs r rw'),
                              p('5 4 0:1 / /x rw shared:5 master:1 - tmpfs a rw'),
                              p('6 4 0:1 / /y rw shared:5 - tmpfs a rw'),
                              p('7 4 0:1 / /z rw master:7 propagate_from:1 - tmpfs a rw')])
        got = self.mt.propagation_receivers(1, [host, other])
        self.assertEqual(got[1][1], True)            # direct peer: both directions
        self.assertEqual(got[5][1], False)           # slave
        self.assertEqual(got[6], ('peer (shared:5) of a slave of the group', False))
        self.assertIn('propagate_from:1', got[7][0])
        link = self.mt.link_status(host.by_id[1], '/new', other, '/y/new', host)
        self.assertIn('via #6 (host -> this view only)', link)
        self.assertIn('via #7', self.mt.link_status(host.by_id[1], '/new', other, '/z/new', host))

    def test_link_uses_the_receiver_not_the_serving_mount(self):
        # a slave container mounted its own private tmpfs at /vol/logs: the host's
        # next mount there is still received (by #10) and tucked beneath #11
        p = self.mt.parse_mountinfo_line
        host = self.mt.View([p('1 0 0:1 / / rw shared:1 - tmpfs a rw')])
        ctr = self.mt.View([p('10 10 0:1 / / rw master:1 - tmpfs a rw'),
                            p('11 10 0:7 / /vol/logs rw - tmpfs own rw')])
        link = self.mt.link_status(host.by_id[1], '/vol/logs', ctr, '/vol/logs', host)
        self.assertIn('LINKED as slave (master:1) of host shared:1 via #10', link)
        self.assertIn('TUCKED beneath existing mount #11', link)
        self.assertIn('NOT LINKED', self.mt.link_status(host.by_id[1], '/vol/logs', ctr, '/elsewhere', host))

    def test_hidden_because_the_parent_is_covered(self):
        p = self.mt.parse_mountinfo_line
        view = self.mt.View([p('1 1 0:1 / / rw - tmpfs root rw'),
                             p('2 1 0:2 / /a rw - tmpfs P rw'),
                             p('3 2 0:3 / /a rw - tmpfs Q rw'),
                             p('4 2 0:4 / /a/b rw - tmpfs M rw'),
                             p('5 3 0:5 / /a/b rw - tmpfs R rw')])
        self.assertNotIn(4, view.visible_ids())
        self.assertTrue(view.hidden_reason(view.by_id[4]).startswith(
            'unreachable: its parent mount #2 at /a is itself hidden (overmounted at the same mountpoint'))
        # a middle layer of a stack at one mountpoint is simply overmounted
        stack = self.mt.View([p('1 1 0:1 / / rw - tmpfs root rw'), p('2 1 0:2 / /d rw - tmpfs A rw'),
                              p('3 2 0:3 / /d rw - tmpfs B rw'), p('4 3 0:4 / /d rw - tmpfs C rw')])
        self.assertTrue(stack.hidden_reason(stack.by_id[3]).startswith('overmounted at the same mountpoint; #4'))


@unittest.skipUnless(userns_available(), 'unprivileged user+mount namespaces unavailable')
class TopologyTests(unittest.TestCase):

    def test_terminal_controls_are_visible_across_report_sites(self):
        out = run(r'''
weird=$'m\033x\a\nline'; state_name=$'s\033x\a\nfile'
mkdir -p "$T/$weird" "$T/clean"
mount -t tmpfs lower "$T/$weird"
mount -t tmpfs upper "$T/$weird"
mkdir -p "$T/$weird/child"
mount -t tmpfs child "$T/$weird/child"
mount -t tmpfs target "$T/clean"
ln -s "$T/$weird" "$T/link"
S="$T/$state_name"
{ printf 'PI_ERRORS=1\n'; printf 'PI_ERROR=bad\033\a\n'; printf 'TARGET=%s\n' "$T/clean"; } > "$S"
mt "$T/$weird" --pid "$$" --host-path "$T/link" --peer "$$:$T/link" \
  --audit-prefix "$T/$weird" --state-file "$S" --volumes-root "$T" \
  --predict "$T/$weird" --submounts --all-hidden
''')
        self.assertIn(r'\x1b', out)
        self.assertIn(r'\x07', out)
        self.assertIn(r'\x0aline', out)
        self.assertIn(r'\x0afile', out)
        self.assertIn('VISIBLE SUBMOUNTS', out)
        self.assertIn('HIDDEN MOUNTS', out)
        self.assertIn('COMPARE:', out)
        self.assertIn('MOUNT AUDIT:', out)
        self.assertIn('SOULMASK TMPFS AUDIT:', out)
        self.assertIn('PREDICT:', out)
        self.assertNotIn('m\x1bx\x07\nline', out)
        for code in list(range(32)) + list(range(127, 160)):
            if code == 10:  # report layout uses line feed
                continue
            self.assertNotIn(chr(code), out, f'raw terminal control U+{code:04X}')

    def test_absolute_symlink_restarts_at_the_selected_root(self):
        out = run(r'''
mkdir -p "$T/absolute/target"
ln -s "$T/absolute/target" "$T/absolute/link"
mt "$T/absolute/link"
''')
        self.assertRegex(out, r'resolved -> /tmp/mounttrace-test-[^/]+/fs/absolute/target\n')
        self.assertIn('RC=0', out)

    def test_divergent_mount_audit_is_an_alert(self):
        out = run(r'''
mkdir -p "$T/divergent"
mount -t tmpfs host "$T/divergent"
spawn private "mount -t tmpfs container '$T/divergent'"
mt "$T/divergent" --pid "$SPAWNED" --audit-prefix "$T/divergent"
''')
        self.assertIn('DIVERGENT:', out)
        self.assertIn('both views have a mount here, but they serve different objects', out)
        self.assertIn('RC=3', out)

    def test_mountpoint_with_carriage_return_survives_mountinfo_read(self):
        out = run(r'''
weird=$'mount\rpoint'
mkdir -p "$T/$weird"
mount -t tmpfs carriage "$T/$weird"
mt "$T/$weird"
''')
        self.assertIn(r'mount\x0dpoint', out)
        self.assertIn("tmpfs 'carriage'", out)
        self.assertIn('RC=0', out)

    def test_state_audit_honors_no_resolve_for_symlinked_target(self):
        out = run(r'''
mkdir -p "$T/real"
mount -t tmpfs target "$T/real"
ln -s "$T/real" "$T/link"
printf 'TARGET=%s\n' "$T/link" > "$T/state"
echo "EXPECTED=$T/link"
mt / --state-file "$T/state" --volumes-root "$T" --no-resolve
''')
        target = re.search(r'EXPECTED=(.*)', out).group(1)
        self.assertIn(f'shared-bind: {target}', out)
        self.assertIn('recorded target is not a mountpoint on the host', out)
        self.assertIn('RC=3', out)

    def test_overmount_and_ancestor_hidden(self):
        out = run(r'''
mkdir -p "$T/d" "$T/x/sub"
mount -t tmpfs lowerA "$T/d"; touch "$T/d/a"
mount -t tmpfs upperB "$T/d"
mount -t tmpfs innerC "$T/x/sub"
mount -t tmpfs coverD "$T/x"
mt "$T/d"
mt "$T" --all-hidden
''')
        covers = [l for l in out.splitlines() if 'covers #' in l]
        self.assertTrue(covers)
        self.assertTrue(all("'lowerA'" in l for l in covers), covers)   # only the real stack, never a parent
        self.assertRegex(out, r"tmpfs 'lowerA'[^\n]*\n\s+overmounted at the same mountpoint; #\d+ \(tmpfs 'upperB'\)")
        self.assertRegex(out, r"tmpfs 'innerC'[^\n]*\n\s+unreachable: resolving .*/x/sub ends in #\d+ mounted at .*/x \(tmpfs 'coverD'")
        self.assertIn('RC=0', out)

    def test_bind_fsroot_with_space_and_symlink(self):
        out = run(r'''
mkdir -p "$T/src dir/sub" "$T/b"
mount --bind "$T/src dir/sub" "$T/b"
ln -s "b" "$T/link"
mt "$T/link"
''')
        self.assertIn("FSROOT='/src dir/sub'", out)
        self.assertRegex(out, r'resolved -> .*/fs/b\n')

    def test_private_container_keeps_ghost_after_host_umount(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
mount -t tmpfs soulmask-pi-abcdef12 "$T/vol/logs"
spawn private ':'
umount "$T/vol/logs"
mt "$T/vol" --pid "$SPAWNED" --audit-prefix "$T/vol"
mt "$T/vol" --pid "$SPAWNED" --state-file "$T/none.state" --volumes-root "$T/vol"
''')
        self.assertRegex(out, r'PID-ONLY: .*/vol/logs')
        self.assertIn('ghost', out)
        self.assertIn('no state file', out)
        self.assertRegex(out, r"PID \d+: tmpfs .*/vol/logs .*'soulmask-pi-abcdef12'.*ghost copy")
        self.assertEqual(out.count('RC=3'), 2)

    def test_mapped_audit_game_container_view(self):
        # like a Wings game container: host volume dir bound to /home/container, rprivate
        out = run(r'''
U="$T/volumes/u1"; H="$T/home"; mkdir -p "$U/WS/Saved/Logs" "$H"
mount -t tmpfs soulmask-pi-u1 "$U/WS/Saved/Logs"
spawn private "mount --rbind '$U' '$H'"
echo "== running"
mt "$H" --pid "$SPAWNED" --audit-prefix "$U=$H"
umount "$U/WS/Saved/Logs"
echo "== after host teardown"
mt "$H" --pid "$SPAWNED" --audit-prefix "$U=$H"
''')
        running, after = out.split('== running', 1)[1].split('== after host teardown', 1)
        self.assertRegex(running, r'PID-ONLY \(same object\): .*/volumes/u1 = .*/home\n')
        self.assertRegex(running, r'OK: .*/WS/Saved/Logs = .*/home/WS/Saved/Logs')
        self.assertIn('RC=0', running)
        self.assertRegex(after, r'PID-ONLY: .*/WS/Saved/Logs = .*/home/WS/Saved/Logs')
        self.assertIn('RC=3', after)

    def test_slave_receives_host_mounts_but_does_not_send(self):
        out = run(r'''
mkdir -p "$T/vol/logs" "$T/vol/c"
spawn slave "mount -t tmpfs ctr-own '$T/vol/c'"
mount -t tmpfs late "$T/vol/logs"
mt "$T/vol/logs" --pid "$SPAWNED" --host-path "$T/vol/logs"
mt "$T/vol" --pid "$SPAWNED" --audit-prefix "$T/vol"
mt / --pid "$SPAWNED" --predict "$T/vol/new"
''')
        self.assertIn('SAME object (stat dev+inode)', out)
        self.assertIn('LINKED as slave', out)
        self.assertRegex(out, r'OK: .*/vol/logs')
        self.assertRegex(out, r'PID-ONLY: .*/vol/c')   # container-made mount never reaches the host
        self.assertRegex(out, r'PID \d+:\n\s+via #\d+ .* \(slave \(master:\d+\)\): .*/vol/new: APPEARS')

    def test_shared_peer_sends_both_ways(self):
        out = run(r'''
mkdir -p "$T/vol/c"
spawn shared ':'
nsenter -t "$SPAWNED" -m mount -t tmpfs from-ctr "$T/vol/c"
mt "$T/vol/c"
mt / --pid "$SPAWNED" --predict "$T/vol/x"
''')
        self.assertRegex(out, r"WRITE ROUTING.*\n.* tmpfs 'from-ctr'")
        self.assertIn('(peer (shared:', out)

    def test_private_container_is_not_linked(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn private ':'
mt "$T/vol/logs" --pid "$SPAWNED" --host-path "$T/vol/logs"
mt / --pid "$SPAWNED" --predict "$T/vol/logs"
mount -t tmpfs late "$T/vol/logs"
mt "$T/vol" --pid "$SPAWNED" --audit-prefix "$T/vol"
''')
        self.assertIn('NOT LINKED', out)
        self.assertIn('no mount of this view is a peer or slave of that group: nothing appears', out)
        self.assertRegex(out, r'HOST-ONLY: .*/vol/logs')

    def test_tucking_prediction_matches_kernel(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn slave "mount -t tmpfs ctr-own '$T/vol/logs'"
mt / --pid "$SPAWNED" --predict "$T/vol/logs"
mount -t tmpfs host-late "$T/vol/logs"
mt "$T/vol/logs" --pid "$SPAWNED"
''')
        self.assertIn('TUCKED beneath existing mount', out)
        # reality: the container still writes into its own tmpfs; the host's copy sits beneath it
        self.assertRegex(out, r"WRITE ROUTING.*\n.* tmpfs 'ctr-own'")
        self.assertRegex(out, r"tmpfs 'host-late'[^\n]*\n\s+overmounted at the same mountpoint")

    def test_busy_slave_copy_blocks_host_umount(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn slave ':'
mount -t tmpfs late "$T/vol/logs"
nsenter -t "$SPAWNED" -m sh -c "cd '$T/vol/logs' && touch '$T.busy' && exec sleep 300" &
PIDS+=($!); wait_for '[ -e "$T.busy" ]' 'the busy cwd'
if umount "$T/vol/logs" 2>/dev/null; then echo UMOUNT=ok; else echo UMOUNT=busy; fi
umount -l "$T/vol/logs" && echo LAZY=ok
''')
        self.assertIn('UMOUNT=busy', out)
        self.assertIn('LAZY=ok', out)

    def test_mount_over_bind_source_vs_replacing_source_mount(self):
        out = run(r'''
mkdir -p "$T/vol" "$T/cont" "$T/src2" "$T/cont2"
mount -t tmpfs old "$T/src2"; mount --make-shared "$T/src2"
spawn slave "mount --bind '$T/vol' '$T/cont'; mount --bind '$T/src2' '$T/cont2'"
mt / --pid "$SPAWNED" --predict "$T/vol"
mount -t tmpfs newsrc "$T/vol"
echo "== after mount over the bind source directory"
mt "$T/cont" --pid "$SPAWNED"
umount "$T/src2"; mount -t tmpfs new2 "$T/src2"
echo "== after replacing the bind source MOUNT"
mt "$T/cont2" --pid "$SPAWNED" --host-path "$T/src2"
''')
        self.assertRegex(out, r'via #\d+ .*/cont \(slave \(master:\d+\)\): .*/cont: APPEARS')
        after = out.split('== after mount over the bind source directory', 1)[1]
        self.assertRegex(after, r"WRITE ROUTING.*\n.* tmpfs 'newsrc'")
        after2 = out.split('== after replacing the bind source MOUNT', 1)[1]
        self.assertRegex(after2, r"WRITE ROUTING.*\n.*/cont2 -> .* tmpfs 'old'")
        self.assertIn('DIFFERENT objects', after2)
        self.assertIn('NOT LINKED', after2)

    def test_soulmask_state_file_audit(self):
        out = run(r'''
V="$T/volumes"; R="$T/ramdisk"; A=aaaaaaaa-1111; B=bbbbbbbb-2222
mkdir -p "$R"; mount -t tmpfs soulmask_tmpfs "$R"; mkdir -p "$R/WS/Content/Paks"
for u in $A $B; do mkdir -p "$V/$u/WS/Content/Paks" "$V/$u/WS/Saved/Logs"; done
spawn slave ':'
for u in $A $B; do
  mount --bind "$R/WS/Content/Paks" "$V/$u/WS/Content/Paks"
  mount -t tmpfs -o size=1m "soulmask-pi-${u:0:8}" "$V/$u/WS/Saved/Logs"
done
S="$T/state"
{ echo "RAMDISK=$R"; echo READONLY=0; echo PI_ERRORS=0
  for u in $A $B; do echo "TARGET=$V/$u/WS/Content/Paks"; done
  for u in $A $B; do echo "PI_TARGET=$V/$u/WS/Saved/Logs"
                     echo "PI_DERIVED=$V/$u/WS/Saved/Logs 0 0 0755 $u"; done; } > "$S"
echo "== clean"
mt "$V" --pid "$SPAWNED" --state-file "$S" --volumes-root "$V"
# --options-mode ignore: a userns cannot re-apply the inherited uid= option
mount --options-mode ignore -o remount,ro "$R"
mkdir -p "$V/$A/stray"; mount -t tmpfs soulmask-pi-aaaaaaaa "$V/$A/stray"
echo "== broken"
mt "$V" --state-file "$S" --volumes-root "$V"
''')
        clean, broken = out.split('== clean', 1)[1].split('== broken', 1)
        self.assertIn('OK shared WS/Content/Paks: all 2 instances resolve to the SAME inode', clean)
        self.assertIn('OK per-instance WS/Saved/Logs: all 2 instances have their own tmpfs', clean)
        self.assertIn('same object: True', clean)
        self.assertNotIn('ALERT', clean)
        self.assertIn('RC=0', clean)
        self.assertIn('READONLY=0 recorded but the RAMDISK is read-only', broken)
        self.assertIn('tagged per-instance tmpfs', broken)
        self.assertIn('RC=3', broken)

    def test_watch_reports_change_and_vanished_pid(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn slave ':'
python3 "$MT" "$T/vol" --pid "$SPAWNED" --watch 0.2 > "$T.watch" 2>&1 & W=$!
wait_for 'grep -q "Watching mount tables" "$T.watch"' 'the first pass'
mount -t tmpfs late "$T/vol/logs"
wait_for 'grep -q "MOUNT TABLE CHANGE" "$T.watch"' 'the change report'
kill "$SPAWNED"; wait "$SPAWNED" 2>/dev/null || true
rc=0; wait "$W" || rc=$?
cat "$T.watch"; echo "WATCH_RC=$rc"
''')
        self.assertIn('MOUNT TABLE CHANGE', out)
        self.assertIn('vanished', out)
        self.assertIn('WATCH_RC=1', out)


BTRFS_SETUP = r'''
img="$T/img"; truncate -s 512M "$img"; mkfs.btrfs -q "$img"
mkdir -p "$T/top" "$T/a" "$T/b"
mount -o loop "$img" "$T/top"; dev=$(findmnt -no SOURCE "$T/top")
btrfs subvolume create "$T/top/@a" >/dev/null
btrfs subvolume create "$T/top/@a/nested" >/dev/null
mkdir -p "$T/top/@a/nested/dir" "$T/top/@a/plain"
mount -o subvol=@a "$dev" "$T/a"
'''


class VmRootTests(unittest.TestCase):
    """Root-only scenarios; run inside the test VM only (see module docstring)."""

    def setUp(self):
        reason = vm_root_unavailable()
        tools = ['mkfs.btrfs', 'btrfs'] if 'btrfs' in self._testMethodName else ['docker']
        missing = [t for t in tools if not shutil.which(t)]
        if reason is None and missing:
            reason = f'missing tools in the guest: {missing}'
        if reason:
            if os.environ.get('MOUNTTRACE_REQUIRE_VM_TESTS') == '1':
                self.fail(f'VM test required but cannot run: {reason}')
            self.skipTest(reason)

    def test_btrfs_nested_subvolume_bind_and_identity(self):
        out = run_root(BTRFS_SETUP + r'''
mount --bind "$T/a/plain" "$T/b"
echo "SHOW_ID=$(btrfs subvolume show "$T/a/nested" | awk '/Subvolume ID:/{print $3}')"
echo "STATDEV=$(stat -c %d "$T/a"):$(stat -c %d "$T/a/nested")"
midev() { awk -v m="$1" '$5 == m { print $3 }' /proc/self/mountinfo; }
echo "MIDEV=$(midev "$T/top")=$(midev "$T/a")"
echo "== nested"; mt "$T/a/nested/dir"
echo "== bind"; mt "$T/b"
echo "== same"; mt "$T/a/plain" --host-path "$T/top/@a/plain"
''')
        show_id = re.search(r'SHOW_ID=(\d+)', out).group(1)
        d1, d2 = re.search(r'STATDEV=(\d+):(\d+)', out).groups()
        self.assertNotEqual(d1, d2, 'stat st_dev differs per subvolume')
        self.assertRegex(out, r'MIDEV=\d+:\d+=\d+:\d+\n')
        m1, m2 = re.search(r'MIDEV=(\d+:\d+)=(\d+:\d+)', out).groups()
        self.assertEqual(m1, m2, 'mountinfo shows one dev for all subvolume mounts of a filesystem')
        nested = out.split('== nested', 1)[1].split('== bind', 1)[0]
        self.assertRegex(nested, rf"/a/nested  ->  btrfs nested subvolume id={show_id} name='nested'"
                                 r" parent=\d+ \(boundary without a mount\)")
        self.assertIn(f"data lands in subvolume id={show_id} name='nested'", nested)
        self.assertIn("FSROOT='/@a'", nested)
        self.assertIn("subvol='/@a'", nested)
        bind = out.split('== bind', 1)[1].split('== same', 1)[0]
        self.assertIn("FSROOT='/@a/plain'", bind)
        self.assertIn("data lands in subvolume", bind)
        same = out.split('== same', 1)[1]
        self.assertIn('SAME object (stat dev+inode)', same)

    def test_btrfs_readonly_snapshot_and_empty_subvolume_stub(self):
        out = run_root(BTRFS_SETUP + r'''
btrfs subvolume snapshot -r "$T/top/@a" "$T/top/@a-snap" >/dev/null
echo "STUBINO=$(stat -c %i "$T/top/@a-snap/nested")"
if touch "$T/top/@a-snap/plain/f" 2>/dev/null; then echo TOUCH=ok; else echo TOUCH=refused; fi
echo "== snap"; mt "$T/top/@a-snap/plain"
echo "== stub"; mt "$T/top/@a-snap/nested/x"
''')
        self.assertIn('TOUCH=refused', out)
        snap = out.split('== snap', 1)[1].split('== stub', 1)[0]
        self.assertRegex(snap, r"data lands in subvolume id=\d+ name='@a-snap' parent=5 READ-ONLY \(snapshot of [0-9a-f-]{36}\)")
        self.assertIn('writes fail with EROFS although the mount is rw', snap)
        self.assertIn('STUBINO=2', out)
        stub = out.split('== stub', 1)[1]
        self.assertIn('btrfs EMPTY-SUBVOLUME STUB (inode 2)', stub)

    def test_btrfs_mount_through_one_mount_is_invisible_through_another(self):
        # The same btrfs directory reached via two separate mounts: a mount made through
        # one is attached to (that mount, dentry) and does not appear through the other.
        out = run_root(BTRFS_SETUP + r'''
echo "== predict"; mt / --predict "$T/top/@a/plain"
mount -t tmpfs via-top "$T/top/@a/plain"
echo "== via a"; mt "$T/a/plain"
''')
        predict = out.split('== predict', 1)[1].split('== via a', 1)[0]
        self.assertIn("at '/@a/plain' of that filesystem", predict)
        self.assertIn('nothing appears', predict)
        via_a = out.split('== via a', 1)[1]
        self.assertRegex(via_a, r"WRITE ROUTING.*\n.* btrfs ")
        self.assertNotIn("'via-top'", via_a.split('HIDDEN MOUNTS', 1)[0])

    def test_docker_default_rprivate_vs_rslave(self):
        out = run_root(r'''
systemctl is-active --quiet docker || systemctl start docker
mkdir -p "$T/vol/logs" "$T/vol/late"
mount -t tmpfs early "$T/vol/logs"
c1=$(docker run -d --rm --name "$CTR_default" --mount "type=bind,src=$T/vol,dst=/data" mt-busybox:1 sleep 900)
c2=$(docker run -d --rm --name "$CTR_rslave" --mount "type=bind,src=$T/vol,dst=/data,bind-propagation=rslave" mt-busybox:1 sleep 900)
echo "PROP=$(docker inspect -f '{{range .Mounts}}{{.Propagation}}{{end}}' "$c1"):$(docker inspect -f '{{range .Mounts}}{{.Propagation}}{{end}}' "$c2")"
p1=$(docker inspect -f '{{.State.Pid}}' "$c1"); p2=$(docker inspect -f '{{.State.Pid}}' "$c2")
mount -t tmpfs late "$T/vol/late"
umount "$T/vol/logs"
echo "== default"; mt /data --pid "$p1" --audit-prefix "$T/vol=/data"
echo "== rslave"; mt /data --pid "$p2" --audit-prefix "$T/vol=/data"
echo "== link"; mt /data/late --pid "$p2" --host-path "$T/vol/late"
''', host_ns=True, containers=('default', 'rslave'))
        self.assertIn('PROP=rprivate:rslave', out)
        default = out.split('== default', 1)[1].split('== rslave', 1)[0]
        self.assertRegex(default, r'PID-ONLY: .*/vol/logs = /data/logs')   # ghost of the unmounted tmpfs
        self.assertRegex(default, r'HOST-ONLY: .*/vol/late = /data/late')  # later host mount never arrives
        self.assertIn('RC=3', default)
        rslave = out.split('== rslave', 1)[1].split('== link', 1)[0]
        self.assertRegex(rslave, r'OK: .*/vol/late = /data/late')
        self.assertNotIn('/data/logs', rslave)
        self.assertIn('RC=0', rslave)
        self.assertIn('LINKED as slave', out.split('== link', 1)[1])

    def test_docker_rslave_requires_shared_or_slave_source(self):
        out = run_root(r'''
systemctl is-active --quiet docker || systemctl start docker
mkdir -p "$T/priv"; mount -t tmpfs priv "$T/priv"; mount --make-private "$T/priv"
if msg=$(docker run --rm --name "$CTR_refused" --mount "type=bind,src=$T/priv,dst=/d,bind-propagation=rslave" mt-busybox:1 true 2>&1); then
  echo RUN=ok
else
  echo "RUN=refused $msg"
fi
''', host_ns=True, containers=('refused',))
        self.assertIn('RUN=refused', out)
        self.assertIn('not a shared or slave mount', out)


if __name__ == '__main__':
    sys.exit(unittest.main())
