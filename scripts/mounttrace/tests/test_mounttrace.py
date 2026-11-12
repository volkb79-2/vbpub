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

import argparse
import importlib.util
import io
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cli_extended import CliIdentity

from mounttrace_cli.cli import main as cli_main

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
MT = os.path.join(PROJECT, 'mounttrace.py')
CORE = os.path.join(PROJECT, 'src', 'mounttrace_core.py')
TEST_VM_MARKER = '/etc/mounttrace-test-vm'
TEST_INPUTS = os.path.join(HERE, 'test-inputs.txt')
TEST_IDENTITY = CliIdentity('mounttrace', '3.2.0', 'read-only mount namespace analyzer', 'mounttrace')
_CORE_MODULE = None

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
mt() {
  local rc=0 human_rc=0 columns=120
  COLUMNS="$columns" python3 "$MT" "$@" >"$T.mounttrace-human" 2>&1 || human_rc=$?
  awk -v columns="$columns" 'length($0) > columns { print "over-width mounttrace line: " length($0); bad=1 } END { exit bad }' \
    "$T.mounttrace-human"
  COLUMNS="$columns" python3 "$MT" --json "$@" || rc=$?
  echo "RC=$rc"
}
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


def json_reports(output):
    reports = []
    for line in output.splitlines():
        if line.startswith('{'):
            reports.append(json.loads(line))
    return reports


def report_findings(output, level=None):
    findings = [finding for report in json_reports(output) for finding in report['findings']]
    return findings if level is None else [finding for finding in findings if finding['level'] == level]


def report_rows(output, table_name=None):
    return [row for report in json_reports(output) for table in report['tables']
            if table_name is None or table['name'] == table_name for row in table['rows']]


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
    except (OSError, ValueError):
        return f'{TEST_VM_MARKER} marker missing or unreadable; refusing to touch this kernel'
    if not marker:
        return f'{TEST_VM_MARKER} marker is empty; refusing to touch this kernel'
    if os.environ.get('MOUNTTRACE_VM') != '1':
        return 'MOUNTTRACE_VM=1 is set only inside the test VM (testing/vm/run-vm-tests.sh)'
    return None


class VmGuardTests(unittest.TestCase):
    def test_vm_guard_refusal_reasons_and_acceptance(self):
        with tempfile.TemporaryDirectory(prefix='mounttrace-marker-test-') as tmp:
            marker = os.path.join(tmp, 'mounttrace-test-vm')
            missing_marker = os.path.join(tmp, 'missing-marker')
            empty_marker = os.path.join(tmp, 'empty-marker')
            invalid_marker = os.path.join(tmp, 'invalid-marker')
            with open(empty_marker, 'w', encoding='ascii'):
                pass
            with open(invalid_marker, 'wb') as file:
                file.write(b'\xff')
            cases = [
                ('non-root', 1000, '/usr/bin/systemd-detect-virt', 'qemu', marker, {'MOUNTTRACE_VM': '1'},
                 'needs root inside the test VM'),
                ('missing-virt-tool', 0, None, 'qemu', marker, {'MOUNTTRACE_VM': '1'},
                 'systemd-detect-virt missing'),
                ('wrong-virt', 0, '/usr/bin/systemd-detect-virt', 'docker', marker, {'MOUNTTRACE_VM': '1'},
                 'not a QEMU/KVM guest (docker)'),
                ('missing-marker', 0, '/usr/bin/systemd-detect-virt', 'qemu', missing_marker,
                 {'MOUNTTRACE_VM': '1'}, f'{missing_marker} marker missing or unreadable'),
                ('empty-marker', 0, '/usr/bin/systemd-detect-virt', 'qemu', empty_marker, {'MOUNTTRACE_VM': '1'},
                 f'{empty_marker} marker is empty'),
                ('non-ascii-marker', 0, '/usr/bin/systemd-detect-virt', 'qemu', invalid_marker,
                 {'MOUNTTRACE_VM': '1'}, f'{invalid_marker} marker missing or unreadable'),
                ('missing-opt-in', 0, '/usr/bin/systemd-detect-virt', 'qemu', marker, {}, 'MOUNTTRACE_VM=1'),
                ('accepting', 0, '/usr/bin/systemd-detect-virt', 'qemu', None, {'MOUNTTRACE_VM': '1'}, None),
            ]
            with open(marker, 'w', encoding='ascii') as file:
                file.write('test-vm-nonce\n')
            for name, euid, tool, virt, marker_path, env, expected in cases:
                with patch(__name__ + '.TEST_VM_MARKER', marker_path or marker), \
                        patch.object(os, 'geteuid', return_value=euid), \
                        patch.object(shutil, 'which', return_value=tool), \
                        patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess(
                            ['systemd-detect-virt', '--vm'], 0, f'{virt}\n', '')) as run, \
                        patch.dict(os.environ, env, clear=True):
                    reason = vm_root_unavailable()
                if expected is None:
                    self.assertIsNone(reason, name)
                else:
                    self.assertIsNotNone(reason, name)
                    self.assertIn(expected, reason, name)
                if tool is None or euid != 0:
                    run.assert_not_called()


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
    global _CORE_MODULE
    if _CORE_MODULE is None:
        spec = importlib.util.spec_from_file_location('mounttrace_core', CORE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _CORE_MODULE = module
    return _CORE_MODULE


def capture_stdout(call):
    old_stdout = sys.stdout
    stdout = io.StringIO()
    try:
        sys.stdout = stdout
        call()
    finally:
        sys.stdout = old_stdout
    return stdout.getvalue()


def invoke_main(mt, argv, read_tables, one_pass=None):
    old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
    old_read_tables, old_one_pass = mt.read_tables, mt.one_pass
    stdout, stderr = io.StringIO(), io.StringIO()
    mt.read_tables = read_tables
    if one_pass is not None:
        mt.one_pass = one_pass
    try:
        sys.argv = argv
        sys.stdout, sys.stderr = stdout, stderr
        status = cli_main(argv[1:], core_module=mt, cli_identity=TEST_IDENTITY)
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
        mt.read_tables, mt.one_pass = old_read_tables, old_one_pass
    return status, stdout.getvalue(), stderr.getvalue()


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
        self.assertEqual(m.propagation, 'shared(7)+slave(1)')
        self.assertEqual(m.fsopt('size'), '1k')

    def test_propagation_labels_use_the_shared_vocabulary(self):
        parse = self.mt.parse_mountinfo_line
        cases = [
            ('shared:7', 'shared(7)'),
            ('master:1', 'slave(1)'),
            ('shared:7 master:1', 'shared(7)+slave(1)'),
            ('master:1 propagate_from:2', 'from(2)'),
            ('unbindable', 'unbindable'),
            ('', 'private'),
        ]
        for optional, expected in cases:
            with self.subTest(optional=optional):
                m = parse(f'20 1 0:5 / / rw {optional} - tmpfs src rw')
                self.assertEqual(m.propagation, expected)

    def test_canonicalize_missing_leaf_has_a_stable_display_warning_and_kind(self):
        real_lstat = self.mt.os.lstat
        target_root = '/proc/42/root'
        missing_path = target_root + '/future-mount'
        seen = []

        def missing(path, *args, **kwargs):
            candidate = self.mt.os.fspath(path)
            seen.append(candidate)
            if candidate == missing_path:
                raise FileNotFoundError()
            return real_lstat(path, *args, **kwargs)

        with patch.object(self.mt, 'proc_root', return_value=target_root), \
                patch.object(self.mt.os, 'lstat', new=missing):
            self.assertEqual(
                self.mt.canonicalize('/future-mount', 42),
                ('/future-mount',
                 '/future-mount does not exist; the rest of the path is analysed lexically',
                 self.mt.CANONICAL_WARNING_MISSING_LEAF))
        self.assertIn(missing_path, seen)

    def test_terminal_controls_are_sanitized_at_output_boundaries(self):
        mt = self.mt
        raw_controls = '\x00 \x1f \x7f \x80 \x85 \x9f \r'
        self.assertEqual(mt.safe_text(raw_controls),
                         r'\x00 \x1f \x7f \x80 \x85 \x9f \x0d')
        self.assertEqual(mt.safe_text('\n\t'), r'\x0a\x09')
        self.assertEqual(mt.safe_text('\n\t', keep_layout=True), '\n\t')
        ordinary = 'backslash \\ \xa0 é日本'
        self.assertEqual(mt.safe_text(ordinary), ordinary)
        invisible_codes = (0x2028, 0x2029, *range(0x200b, 0x2010),
                           *range(0x202a, 0x202f), *range(0x2060, 0x206a), 0xfeff)
        invisibles = ''.join(chr(codepoint) for codepoint in invisible_codes)
        escaped_invisibles = ''.join(f'\\u{codepoint:04x}' for codepoint in invisible_codes)
        self.assertEqual(mt.safe_text(invisibles), escaped_invisibles)

        stream = io.StringIO()
        safe_stream = mt.TerminalSafeStream(stream)
        stream_text = 'first\n\tsecond\x1b\u202e\r'
        self.assertEqual(safe_stream.write(stream_text), len(stream_text))
        self.assertEqual(stream.getvalue(), 'first\n\tsecond\\x1b\\u202e\\x0d')

        raw = 'rw,mode=1777,tag=\x1b\x07\nnext'
        m = mt.parse_mountinfo_line(f'20 1 0:5 / / rw - tmpfs src {raw}')
        self.assertEqual(m.fsopts, raw)
        self.assertEqual(m.propagation, 'private')
        self.assertEqual(m.ro_label, 'rw')

    def test_table_cells_are_sanitized_before_width_measurement(self):
        mt = self.mt
        table = {'name': 'SYNTHETIC', 'columns': ['MOUNTPOINT', 'SOURCE'], 'key': 'MOUNTPOINT',
                 'rows': [{'MOUNTPOINT': '/', 'SOURCE': 'x' * 75 + '\x1b'}]}
        lines = mt.render_table(table, 80)
        rendered = '\n'.join(lines)
        self.assertNotIn('\x1b', rendered)
        self.assertIn(r'\x1b', rendered)
        self.assertTrue(all(len(line) <= 80 for line in lines), rendered)

    def test_mount_table_preserves_protected_values_under_width_pressure(self):
        mt = self.mt
        row = {'MOUNTPOINT': '/', 'ID': '#12345678', 'FSTYPE': 'long-filesystem-name',
               'SOURCE': 'source-' * 20, 'FSROOT': '/filesystem/root/' * 8, 'RO': 'ro:sb',
               'PROPAGATION': 'shared(123456789012345)+slave(987654321098765)'}
        lines = mt.render_table({'name': 'MOUNTS', 'columns': list(mt.MOUNT_COLUMNS),
                                 'rows': [row], 'key': 'MOUNTPOINT'}, 80)
        rendered = '\n'.join(lines)
        self.assertTrue(all(len(line) <= 80 for line in lines), rendered)
        for value in (row['ID'], row['FSTYPE'], row['RO'], row['PROPAGATION']):
            self.assertIn(value, rendered.replace('\n', ''))

    def test_source_equal_to_fstype_is_compact_in_mount_row(self):
        mount = self.mt.parse_mountinfo_line('1 1 0:1 / / rw - tmpfs tmpfs rw')
        self.assertEqual(self.mt.mount_row(mount)['SOURCE'], '-')


class CliReportTests(unittest.TestCase):
    def setUp(self):
        self.mt = load_module()

    def fixture_tables(self):
        parse = self.mt.parse_mountinfo_line
        host = [parse('1 1 0:1 / / rw shared:10 - ext4 host rw'),
                parse('2 1 0:2 / /data rw shared:11 - tmpfs data rw'),
                parse('3 1 0:3 / /ram rw - tmpfs ram rw'),
                parse('4 2 0:3 / /data/u/WS/Content/Paks rw - tmpfs ram rw')]
        pid = [parse('10 10 0:1 / / rw master:10 - ext4 host rw'),
               parse('11 10 0:4 / /data rw - tmpfs private rw'),
               parse('12 10 0:3 / /ram rw - tmpfs ram rw'),
               parse('13 11 0:3 / /data/u/WS/Content/Paks rw - tmpfs ram rw')]
        return {'host': host, 'pids': {42: pid}, 'state': None}

    def test_json_report_has_one_stable_top_level_key_set(self):
        mt = self.mt
        tables = self.fixture_tables()
        status, stdout, stderr = invoke_main(mt, ['mounttrace', '--json', 'explain', '/data', '--no-probe'],
                                             lambda _args: tables)
        self.assertEqual(status, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(set(report), {'schema', 'command', 'header', 'verdict', 'findings', 'kv', 'tables'})
        self.assertEqual(report['schema'], 1)
        self.assertEqual(report['command'], 'explain')
        self.assertEqual(report['verdict']['status'], 'OK')
        self.assertEqual(report['kv']['mount']['ID'], '#2')
        self.assertEqual(report['tables'][0]['columns'], list(mt.MOUNT_COLUMNS))

    def test_compare_different_exits_three_and_uses_fields(self):
        status, stdout, _stderr = invoke_main(
            self.mt,
            ['mounttrace', 'compare', '/data', '42:/data', '--json', '--no-probe'],
            lambda _args: self.fixture_tables())
        report = json.loads(stdout)
        self.assertEqual(status, 3)
        self.assertEqual(report['verdict']['status'], 'DIFFERENT')
        self.assertEqual(report['kv']['comparisons'][0]['same'], 'different')
        self.assertEqual(report['tables'][0]['rows'][0]['PATH'], '/data')
        self.assertEqual(report['findings'][0]['class'], 'DIFFERENT')

    def test_no_probe_soulmask_ramdisk_never_touches_paths(self):
        mt = self.mt
        real_stat, real_lstat, real_readlink = mt.os.stat, mt.os.lstat, mt.os.readlink
        translated_roots = (mt.proc_root(42), mt.proc_root(None))
        fixture_paths = ("/state", "/ram", "/data", "/btrfs", "/dev/loop0")

        def refused(name, real):
            def guarded(path, *args, **kwargs):
                if isinstance(path, int):
                    return real(path, *args, **kwargs)
                try:
                    candidate = mt.os.fsdecode(mt.os.fspath(path))
                except TypeError:
                    return real(path, *args, **kwargs)
                if candidate.startswith('/proc/self/fd/'):
                    return real(path, *args, **kwargs)
                if any(candidate == root or candidate.startswith(root.rstrip('/') + '/')
                       for root in translated_roots):
                    raise AssertionError(f"{name} probed translated path {candidate}")
                if any(candidate == fixture or candidate.startswith(fixture.rstrip('/') + '/')
                       for fixture in fixture_paths):
                    raise AssertionError(f"{name} probed fixture path {candidate}")
                return real(path, *args, **kwargs)
            return guarded

        tables = self.fixture_tables()
        tables['state'] = (b'RAMDISK=/ram\nREADONLY=0\nPI_ERRORS=0\n'
                           b'TARGET=/data/u/WS/Content/Paks\n')
        btrfs_tables = self.fixture_tables()
        btrfs_tables['host'].append(mt.parse_mountinfo_line(
            '20 1 0:20 / /btrfs rw - btrfs /dev/loop0 rw,subvolid=5'))
        with patch.object(mt.os, 'stat', new=refused('stat', real_stat)), \
                patch.object(mt.os, 'lstat', new=refused('lstat', real_lstat)), \
                patch.object(mt.os, 'readlink', new=refused('readlink', real_readlink)), \
                patch.object(mt.fcntl, 'ioctl', side_effect=AssertionError('ioctl called')), \
                patch.object(argparse, '_', new=lambda message: message):
            status, stdout, stderr = invoke_main(
                mt, ['mounttrace', 'soulmask', '42', '--state-file', '/state', '--volumes-root', '/data',
                     '--no-probe', '--json'], lambda _args: tables)
            btrfs_status, btrfs_stdout, btrfs_stderr = invoke_main(
                mt, ['mounttrace', 'explain', '/btrfs', '--no-probe', '--json'],
                lambda _args: btrfs_tables)
        report = json.loads(stdout)
        self.assertEqual(status, 3, stderr)
        self.assertEqual(report['command'], 'soulmask')
        self.assertEqual(report['kv']['state']['ramdisk']['MOUNTPOINT'], '/ram')
        self.assertEqual(len(report['kv']['audits']), 1)
        self.assertIn('path identity probes skipped (--no-probe)',
                      [item['text'] for item in report['findings']])
        btrfs_report = json.loads(btrfs_stdout)
        self.assertEqual((btrfs_status, btrfs_stderr), (0, ''))
        self.assertEqual(btrfs_report['kv']['mount']['FSTYPE'], 'btrfs')

    def test_soulmask_defaults_to_the_runtime_state_file(self):
        from mounttrace_cli.cli import build_cli

        args = build_cli(core_module=self.mt).command_parsers['soulmask'].parse_args(['42'])
        self.assertEqual(args.state_file, '/run/soulmask_tmpfs.state')

    def test_removed_option_modes_are_not_accepted_as_aliases(self):
        mt = self.mt
        for option in ('--all-hidden', '--host-path', '--peer', '--audit-prefix', '--predict',
                       '--state-file', '--volumes-root', '--no-resolve'):
            with self.subTest(option=option):
                status, _stdout, stderr = invoke_main(
                    mt, ['mounttrace', 'explain', '/', option], lambda _args: {})
                self.assertEqual(status, 2)
                self.assertIn(option, stderr)

    def test_report_rows_can_select_a_table_before_row_fields(self):
        output = json.dumps({'tables': [
            {'name': 'PREDICTION', 'rows': [{'RESULT': 'not reached'}]},
            {'name': 'PATH WALK', 'rows': [{'SOURCE': 'ctr-own'}]},
        ]})
        selected = report_rows(output, 'PATH WALK')
        self.assertEqual([row['SOURCE'] for row in selected], ['ctr-own'])


class DocumentationTests(unittest.TestCase):
    def test_current_docs_do_not_advertise_removed_option_modes(self):
        project = os.path.dirname(HERE)
        with open(TEST_INPUTS, encoding='utf-8') as manifest:
            inputs = [line.strip() for line in manifest
                      if line.strip() and not line.lstrip().startswith('#')]
        inputs = [path for path in inputs if path.endswith('.md')]
        paths = [os.path.join(project, path) for path in inputs]
        removed_flags = ('--all-hidden', '--host-path', '--peer', '--audit-prefix',
                         '--predict', '--pid', '--no-resolve')
        for path in paths:
            with self.subTest(document=os.path.relpath(path, project)):
                with open(path, encoding='utf-8') as file:
                    contents = file.read()
                for flag in removed_flags:
                    self.assertNotIn(flag, contents, f'{path} still advertises {flag}')
                for line_number, line in enumerate(contents.splitlines(), 1):
                    for flag in ('--state-file', '--volumes-root'):
                        if flag in line:
                            self.assertIn('soulmask', line.lower(),
                                          f'{path}:{line_number} uses {flag} outside soulmask')


class VmInputManifestTests(unittest.TestCase):
    script = os.path.join(os.path.dirname(HERE), 'testing/vm/run-vm-tests.sh')
    guest_test_files = (
        'mounttrace.py',
        'pyproject.toml',
        'README.md',
        'mounttrace.md',
        'docs/CONSUMERS.md',
        'docs/DESIGN-GUIDE.md',
        'testing/vm/run-vm-tests.sh',
    )

    def check_inputs(self, manifest=None):
        argv = [self.script, '--check-inputs-only']
        if manifest is not None:
            argv += ['--input-manifest', manifest]
        return subprocess.run(argv, capture_output=True, text=True, timeout=10)

    def test_vm_lane_manifest_covers_every_guest_test_file(self):
        with open(TEST_INPUTS, encoding='utf-8') as manifest:
            inputs = [line.strip() for line in manifest
                      if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(inputs, list(self.guest_test_files))

    def test_vm_lane_finds_every_declared_test_input(self):
        result = self.check_inputs()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.guest_test_files:
            self.assertEqual(result.stderr.count(path), 1, result.stderr)

    def test_vm_lane_fails_loudly_for_a_missing_test_input(self):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as manifest:
            manifest.write('README.md\ndocs/missing.md\n')
            manifest_path = manifest.name
        try:
            result = self.check_inputs(manifest_path)
        finally:
            os.unlink(manifest_path)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('test input missing:', result.stderr)
        self.assertIn('docs/missing.md', result.stderr)

    def test_vm_lane_copies_and_imports_the_exact_source_trees(self):
        with open(self.script, encoding='utf-8') as file:
            script = file.read()
        self.assertIn('SOURCE_PROJECT=scripts/mounttrace', script)
        self.assertIn('export MDT_VM_SOURCE_DIR="$REPO_ROOT"', script)
        self.assertIn('"/source/$SOURCE_PROJECT/src/" "$GUEST_DIR/src/"', script)
        # cli-extended: allow-path-assertion
        self.assertIn('/source/libraries/cli-extended/src/', script)
        self.assertIn('PYTHONPATH=$GUEST_DIR/src:$GUEST_DIR/cli-extended-src', script)
        self.assertIn(
            'PYTHONPATH=/opt/mounttrace/src:/opt/mounttrace/cli-extended-src',
            script,
        )


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


    def test_no_optional_fields_and_readonly_layers(self):
        m = self.mt.parse_mountinfo_line('20 1 0:5 / / ro,relatime - ext4 /dev/sda1 rw')
        self.assertEqual(m.optional, ())
        self.assertEqual(m.propagation, 'private')
        self.assertEqual(m.ro_label, 'ro')
        m = self.mt.parse_mountinfo_line('20 1 0:5 / / rw - tmpfs t ro,size=1k')
        self.assertEqual(m.ro_label, 'ro:sb')

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


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.mt = load_module()

    def test_watch_reports_process_lookup_error_as_vanished_pid(self):
        mt = self.mt

        def run_watch(error):
            calls = 0

            def read_tables(_args):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise error
                root = mt.parse_mountinfo_line('1 1 0:1 / / rw - ext4 root rw')
                return {'host': None, 'pids': {42: [root]}, 'state': None}

            with patch.object(mt.time, 'sleep', return_value=None):
                return invoke_main(mt, ['mounttrace', '--watch', '0.1', 'explain', '42:/', '--no-probe'],
                                   read_tables)

        status, _stdout, stderr = run_watch(
            ProcessLookupError(3, 'No such process', '/proc/42/mountinfo\nWATCH_INJECTED'))
        self.assertEqual(status, 1)
        self.assertIn('pid 42 vanished', stderr)
        self.assertFalse(any(line.startswith('WATCH_INJECTED') for line in stderr.splitlines()))

        status, _stdout, stderr = run_watch(ProcessLookupError(3, 'No such process'))
        self.assertEqual(status, 1)
        self.assertIn('pid 42 vanished', stderr)

        status, _stdout, stderr = run_watch(PermissionError(13, 'Permission denied', '/proc/42/mountinfo'))
        self.assertEqual(status, 1)
        self.assertIn('cannot inspect pid 42: permission denied', stderr)

    def test_initial_read_errors_are_actionable_outside_watch(self):
        mt = self.mt

        def invoke_error(argv, error):
            def read_tables(_args):
                raise error
            return invoke_main(mt, ['mounttrace'] + argv, read_tables)

        errors = [
            ('bare ENOENT', FileNotFoundError(2, 'No such file or directory')),
            ('proc ENOENT', FileNotFoundError(2, 'No such file or directory', '/proc/42/mountinfo')),
            ('bare ESRCH', ProcessLookupError(3, 'No such process')),
        ]
        for label, error in errors:
            with self.subTest(error=label):
                status, _stdout, stderr = invoke_error(
                    ['explain', '42:/', '--no-probe'], error)
                self.assertEqual(status, 1)
                self.assertIn('pid 42 vanished', stderr)
                self.assertNotIn('[Errno', stderr)

        status, _stdout, stderr = invoke_error(
            ['compare', '41:/', '42:/', '--no-probe'],
            ProcessLookupError(3, 'No such process'))
        self.assertEqual(status, 1)
        self.assertIn('a selected process vanished', stderr)

        status, _stdout, stderr = invoke_error(
            ['explain', '42:/', '--no-probe'], PermissionError(13, 'Permission denied'))
        self.assertEqual(status, 1)
        self.assertIn('cannot inspect pid 42: permission denied; run as root', stderr)

        status, _stdout, stderr = invoke_error(
            ['compare', '41:/', '42:/', '--no-probe'], PermissionError(13, 'Permission denied'))
        self.assertEqual(status, 1)
        self.assertIn('cannot inspect a selected process: permission denied', stderr)

    def test_selected_pid_errors_cover_every_command_and_phase(self):
        mt = self.mt
        root = mt.parse_mountinfo_line('1 1 0:1 / / rw - ext4 root rw')
        tables = {'host': [root], 'pids': {41: [root], 42: [root]}, 'state': None}
        commands = [
            ('explain', ['explain', '42:/', '--no-probe'], 'pid 42'),
            ('compare', ['compare', '41:/', '42:/', '--no-probe'], 'a selected process'),
            ('audit', ['audit', '/', '42:/', '--no-probe'], 'pid 42'),
            ('predict', ['predict', '/', '42', '--no-probe'], 'pid 42'),
            ('soulmask', ['soulmask', '42', '--no-probe'], 'pid 42'),
        ]
        errors = [
            ('ENOENT', FileNotFoundError),
            ('ESRCH', ProcessLookupError),
            ('PermissionError', PermissionError),
        ]

        def make_error(error_type):
            if error_type is PermissionError:
                return error_type(13, 'Permission denied')
            if error_type is FileNotFoundError:
                return error_type(2, 'No such file or directory')
            return error_type(3, 'No such process')

        for phase in ('initial read', 'watch refresh'):
            for command, command_args, selected in commands:
                for error_name, error_type in errors:
                    with self.subTest(phase=phase, command=command, error=error_name):
                        calls = 0

                        def read_tables(_args):
                            nonlocal calls
                            calls += 1
                            if phase == 'initial read' or calls > 1:
                                raise make_error(error_type)
                            return tables

                        argv = ['mounttrace']
                        if phase == 'watch refresh':
                            argv += ['--watch', '0.1']
                        argv += command_args
                        if phase == 'watch refresh':
                            with patch.object(mt.time, 'sleep', return_value=None):
                                status, stdout, stderr = invoke_main(mt, argv, read_tables)
                            self.assertTrue(stdout)
                        else:
                            status, stdout, stderr = invoke_main(mt, argv, read_tables)

                        self.assertEqual(status, 1)
                        if error_type is PermissionError:
                            self.assertIn(f'cannot inspect {selected}: permission denied', stderr)
                        else:
                            self.assertIn(f'{selected} vanished', stderr)
                        self.assertNotIn('[Errno', stderr)

    def test_watch_emits_change_line_and_ctrl_c_returns_last_status(self):
        mt = self.mt
        parse = mt.parse_mountinfo_line
        original = {'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
                    'pids': {42: [parse('10 10 0:1 / / rw - ext4 root rw')]}, 'state': None}
        changed = {'host': list(original['host']),
                   'pids': {42: [parse('10 10 0:9 / / rw - ext4 replacement rw'),
                                 parse('11 10 0:2 / /late rw - tmpfs late rw')]}, 'state': None}
        passes = iter([original, changed])
        with patch.object(mt.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                patch.dict(os.environ, {'COLUMNS': '120'}):
            status, stdout, stderr = invoke_main(
                mt, ['mounttrace', '--watch', '0.1', 'compare', '/', '42:/', '--no-probe'],
                lambda _args: next(passes))
        self.assertEqual(status, 3)
        self.assertEqual(stderr, '')
        self.assertIn('mount tables changed: pid 42 +#11 /late ~#10 /', stdout)

    def test_watch_flushes_first_and_changed_reports_to_buffered_stdout(self):
        mt = self.mt
        parse = mt.parse_mountinfo_line
        original = {'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
                    'pids': {42: [parse('10 10 0:1 / / rw - ext4 root rw')]}, 'state': None}
        changed = {'host': list(original['host']),
                   'pids': {42: [parse('10 10 0:9 / / rw - ext4 replacement rw'),
                                 parse('11 10 0:2 / /late rw - tmpfs late rw')]}, 'state': None}
        passes = iter([original, changed])

        class BufferedStdout:
            def __init__(self):
                self.pending = ''
                self.visible = ''
                self.flushes = 0

            def write(self, text):
                self.pending += text
                return len(text)

            def flush(self):
                self.visible += self.pending
                self.pending = ''
                self.flushes += 1

            def isatty(self):
                return False

        stdout = BufferedStdout()
        before_sleeps = []

        def sleep(_seconds):
            before_sleeps.append(stdout.visible)
            if len(before_sleeps) == 2:
                raise KeyboardInterrupt

        old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
        old_read_tables = mt.read_tables
        mt.read_tables = lambda _args: next(passes)
        try:
            sys.argv = ['mounttrace', '--watch', '0.1', 'compare', '/', '42:/', '--no-probe']
            sys.stdout, sys.stderr = stdout, io.StringIO()
            with patch.object(mt.time, 'sleep', side_effect=sleep):
                status = cli_main(sys.argv[1:], core_module=mt, cli_identity=TEST_IDENTITY)
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
            mt.read_tables = old_read_tables
        self.assertEqual(status, 3)
        self.assertIn('compare / in host', before_sleeps[0])
        self.assertIn('compare / in host', stdout.visible)
        self.assertIn('mount tables changed: pid 42 +#11 /late', stdout.visible)
        self.assertGreaterEqual(stdout.flushes, len(stdout.visible.splitlines()))

    def test_watch_first_pass_is_visible_on_a_file_before_sleep(self):
        mt = self.mt
        parse = mt.parse_mountinfo_line
        original = {'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
                    'pids': {42: [parse('10 10 0:1 / / rw - ext4 root rw')]}, 'state': None}
        changed = {'host': list(original['host']),
                   'pids': {42: [parse('10 10 0:9 / / rw - ext4 replacement rw')]}, 'state': None}
        passes = iter([original, changed])
        calls = []
        old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
        old_read_tables = mt.read_tables
        mt.read_tables = lambda _args: next(passes)
        try:
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stream, \
                    patch.dict(os.environ, {'COLUMNS': '120'}):
                sys.argv = ['mounttrace', '--watch', '0.1', 'compare', '/', '42:/', '--no-probe']
                sys.stdout, sys.stderr = stream, io.StringIO()

                def fake_sleep(_seconds):
                    size = os.fstat(stream.fileno()).st_size
                    data = os.pread(stream.fileno(), size, 0).decode('utf-8') if size else ''
                    calls.append(data)
                    if len(calls) == 2:
                        raise KeyboardInterrupt

                with patch.object(mt.time, 'sleep', side_effect=fake_sleep):
                    status = cli_main(sys.argv[1:], core_module=mt, cli_identity=TEST_IDENTITY)
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr
            mt.read_tables = old_read_tables
        self.assertEqual(status, 3)
        self.assertIn('compare / in host', calls[0])
        self.assertIn('mount tables changed: pid 42 ~#10 /', calls[1])


class HumanGoldenTests(unittest.TestCase):
    """Stable human-output snapshots from mountinfo-backed synthetic Views."""

    def setUp(self):
        self.mt = load_module()
        parse = self.mt.parse_mountinfo_line
        self.tables = {
            'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
            'pids': {42: [parse('10 10 0:1 / / rw - ext4 root rw')]},
            'state': None,
        }

    def output(self, argv, columns='120', tables=None, expected_status=0):
        with patch.dict(os.environ):
            if columns is None:
                os.environ.pop('COLUMNS', None)
            else:
                os.environ['COLUMNS'] = columns
            status, stdout, stderr = invoke_main(
                self.mt, ['mounttrace'] + argv, lambda _args: self.tables if tables is None else tables)
        self.assertEqual((status, stderr), (expected_status, ''))
        return stdout

    def assert_snapshot(self, actual, expected, width):
        self.assertEqual(actual, expected)
        self.assertTrue(all(len(line) <= width for line in actual.splitlines()), actual)

    def test_explain_at_120_columns(self):
        self.assert_snapshot(self.output(['explain', '/', '--no-probe']),
                             'host /\n'
                             'OK writes go to ext4 via #1, rw\n'
                             'NOTE: hidden: none on or below this path\n'
                             'PATH WALK\n'
                             'MOUNTPOINT ID FSTYPE SOURCE FSROOT RO PROPAGATION\n'
                             '/          #1 ext4   root   /      rw private\n', 120)

    def test_canonicalize_resolution_failure_has_kind_and_predict_reports_it(self):
        mt = self.mt
        warning = ('could not resolve symlinks: [Errno 13] Permission denied '
                   '(run as root to inspect another process root); lexical analysis only')
        target_root = '/proc/42/root'
        denied_path = target_root + '/denied'
        real_lstat = mt.os.lstat

        def denied(path, *args, **kwargs):
            if mt.os.fspath(path) == denied_path:
                raise PermissionError(13, 'Permission denied')
            return real_lstat(path, *args, **kwargs)

        with patch.object(mt, 'proc_root', return_value=target_root), \
                patch.object(mt.os, 'lstat', new=denied):
            self.assertEqual(
                mt.canonicalize('/denied', 42),
                ('/denied', warning, mt.CANONICAL_WARNING_RESOLUTION_FAILED))
            status, stdout, stderr = invoke_main(
                mt, ['mounttrace', '--json', 'predict', '/denied', '42'],
                lambda _args: self.tables)
        self.assertEqual((status, stderr), (0, ''))
        self.assertIn(warning, stdout)

    def test_predict_suppresses_only_missing_leaf_warning_kind(self):
        warnings = [
            (self.mt.CANONICAL_WARNING_MISSING_LEAF, 'missing leaf wording may change', False),
            (self.mt.CANONICAL_WARNING_RESOLUTION_FAILED, 'could not resolve path', True),
        ]
        for warning_kind, warning_text, should_report in warnings:
            with self.subTest(warning_kind=warning_kind), patch.object(
                    self.mt, 'canonicalize',
                    return_value=('/future-mount', warning_text, warning_kind)):
                status, stdout, stderr = invoke_main(
                    self.mt, ['mounttrace', '--json', 'predict', '/future-mount', '42'],
                    lambda _args: self.tables)
            self.assertEqual((status, stderr), (0, ''))
            self.assertEqual(warning_text in stdout, should_report)
            self.assertEqual(json_reports(stdout)[0]['kv']['path'], '/future-mount')

    def test_overlay_upperdir_is_explained_as_mountinfo_backing_metadata(self):
        parse = self.mt.parse_mountinfo_line
        tables = {'host': [parse('1 1 0:1 / / rw - overlay overlay rw,upperdir=relative-upper')],
                  'pids': {}, 'state': None}
        status, stdout, stderr = invoke_main(
            self.mt, ['mounttrace', '--json', 'explain', '/', '--no-probe'],
            lambda _args: tables)
        self.assertEqual((status, stderr), (0, ''))
        report = json_reports(stdout)[0]
        notes = [item['text'] for item in report['findings'] if item['class'] == 'LAYER']
        self.assertTrue(any('overlay upperdir: relative-upper' in item and
                            'writable backing layer, not a volume' in item and
                            'recorded in mountinfo' in item for item in notes), notes)

    def test_explain_at_80_columns(self):
        path = ('/srv/vol/12345678-1234-1234-1234-123456789012/WS/Saved/Logs/'
                'very-long-run-name-that-preserves-the-uuid-tail/CrashReport.log')
        parse = self.mt.parse_mountinfo_line
        tables = {'host': [parse('1 1 0:1 / / rw - ext4 root rw'),
                           parse(f'2 1 0:2 / {path} rw - tmpfs instance rw')],
                  'pids': {42: [parse('10 10 0:1 / / rw - ext4 root rw')]}, 'state': None}
        actual = self.output(['explain', path, '--no-probe'], '80', tables=tables)
        self.assertIn('reserves-the-uuid-tail/CrashReport.log', actual)
        self.assert_snapshot(actual, 'host /srv/vol/12345678-1234-1234-1234-1...reserves-the-uuid-tail/CrashReport.log\n'
                             'OK writes go to tmpfs via #2, rw\n'
                             'NOTE: hidden: none on or below this path\n'
                             'PATH WALK\n'
                             'MOUNTPOINT                                ID FSTYPE SOURCE FSROOT RO PROPAGATION\n'
                             '/                                         #1 ext4   root   /      rw private\n'
                             '                                          #2 tmpfs         /      rw private\n'
                             'MOUNTPOINT /srv/vol/12345678-1234-1234-...reserves-the-uuid-tail/CrashReport.log\n'
                             'SOURCE instance\n', 80)

    def test_compare_at_80_keeps_uuid_tails_and_never_clips_status(self):
        uuid_a = '3f9a1c2e-7b44-4d10-9a3e-5c8d1e0f2a61'
        uuid_c = '3f9a1c2e-7b44-4d10-9a3e-5c8d1e0f2a62'
        path_a = f'/var/lib/pterodactyl/volumes/{uuid_a}/WS/Saved/Logs'
        path_c = f'/var/lib/pterodactyl/volumes/{uuid_c}/WS/Saved/Logs'
        parse = self.mt.parse_mountinfo_line
        tables = {
            'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
            'pids': {42: [parse('10 10 0:9 / / rw master:201 - ext4 other rw')]},
            'state': None,
        }
        actual = self.output(['compare', path_a, f'42:{path_c}', '--no-probe'],
                             '80', tables=tables, expected_status=3)
        self.assert_snapshot(
            actual,
            'compare /var/lib/pterodactyl/volumes/3f...a3e-5c8d1e0f2a61/WS/Saved/Logs in host\n'
            'DIFFERENT 1 of 1 compared views differ\n'
            'ALERT DIFFERENT: writes through the compared paths land in different objects\n'
            'next: Check the bind source and mount propagation for each view.\n'
            'VIEWS\n'
            'VIEW PATH MOUNT FSTYPE SOURCE FSROOT OBJECT DEV:INODE RO PROPAGATION LINK  SAME\n'
            '          #10   ext4   other  /             0:9       rw slave(201)\n'
            'VIEW pid 42\n'
            'PATH /var/lib/pterodactyl/volumes/3f9a1...4-4d10-9a3e-5c8d1e0f2a62/WS/Saved/Logs\n'
            'OBJECT /var/lib/pterodactyl/volumes/3f9...4-4d10-9a3e-5c8d1e0f2a62/WS/Saved/Logs\n'
            'LINK NOT LINKED\n'
            'SAME different\n',
            80)

    def test_compare_at_80_documents_prefix_only_uuid_middle_cut(self):
        uuid_a = '3f9a1c2e-7b44-4d10-9a3e-5c8d1e0f2a61'
        uuid_b = '3f9a1c2f-7b44-4d10-9a3e-5c8d1e0f2a61'
        path_a = f'/var/lib/pterodactyl/volumes/{uuid_a}/WS/Saved/Logs'
        path_b = f'/var/lib/pterodactyl/volumes/{uuid_b}/WS/Saved/Logs'
        default = self.output(['compare', path_a, path_b, '--no-probe'], '80',
                              expected_status=3)
        verbose = self.output(['compare', path_a, path_b, '--no-probe', '-v'], '80',
                              expected_status=3)
        self.assert_snapshot(
            default,
            'compare /var/lib/pterodactyl/volumes/3f...a3e-5c8d1e0f2a61/WS/Saved/Logs in host\n'
            'DIFFERENT 1 of 1 compared views differ\n'
            'ALERT DIFFERENT: writes through the compared paths land in different objects\n'
            'next: Check the bind source and mount propagation for each view.\n'
            'VIEWS\n'
            'VIEW PATH MOUNT FSTYPE SOURCE FSROOT OBJECT DEV:INODE RO PROPAGATION LINK SAME\n'
            'host      #1    ext4   root   /             0:1       rw private     -\n'
            'PATH /var/lib/pterodactyl/volumes/3f9a1...4-4d10-9a3e-5c8d1e0f2a61/WS/Saved/Logs\n'
            'OBJECT /var/lib/pterodactyl/volumes/3f9...4-4d10-9a3e-5c8d1e0f2a61/WS/Saved/Logs\n'
            'SAME different\n',
            80)
        self.assertEqual(verbose.splitlines()[0], f'compare {path_a} in host')

    def test_compare_host_only_paths_are_distinguished_in_the_table(self):
        parse = self.mt.parse_mountinfo_line
        tables = {'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
                  'pids': {}, 'state': None}
        paths = ['/srv/vol/a/WS/Saved/Logs', '/srv/vol/b/WS/Saved/Logs',
                 '/srv/vol/c/WS/Saved/Logs']
        actual = self.output(['compare'] + paths + ['--no-probe'], '120',
                             tables=tables, expected_status=3)
        self.assert_snapshot(
            actual,
            'compare /srv/vol/a/WS/Saved/Logs in host\n'
            'DIFFERENT 2 of 2 compared views differ\n'
            'ALERT DIFFERENT: writes through the compared paths land in different objects\n'
            'next: Check the bind source and mount propagation for each view.\n'
            'VIEWS\n'
            'VIEW PATH                    MOUNT FSTYPE SOURCE FSROOT OBJECT                   '
            'DEV:INODE RO PROPAGATION LINK SAME\n'
            'host                         #1    ext4   root   /      /srv/vol/b/WS/Saved/Logs '
            '0:1       rw private     -    different\n'
            'PATH /srv/vol/b/WS/Saved/Logs\n'
            'host                         #1    ext4   root   /      /srv/vol/c/WS/Saved/Logs '
            '0:1       rw private     -    different\n'
            'PATH /srv/vol/c/WS/Saved/Logs\n',
            120)

    def test_verbose_preserves_a_long_header_at_80_columns(self):
        path = '/srv/volumes/' + ('instance-path-segment-' * 5) + 'CrashReport.log'
        default = self.output(['explain', path, '--no-probe'], '80')
        verbose = self.output(['explain', path, '--no-probe', '-v'], '80')
        self.assertLessEqual(len(default.splitlines()[0]), 80)
        self.assertNotEqual(default.splitlines()[0], f'host {path}')
        self.assertEqual(verbose.splitlines()[0], f'host {path}')

    def test_compare_at_120_columns(self):
        self.assert_snapshot(self.output(['compare', '/', '42:/', '--no-probe']),
                             'compare / in host\n'
                             'OK objects are the same in all 2 inspected views\n'
                             'VIEWS\n'
                             'VIEW   PATH MOUNT FSTYPE SOURCE FSROOT OBJECT DEV:INODE RO PROPAGATION LINK       SAME\n'
                             'pid 42 /    #10   ext4   root   /      /      0:1       rw private     NOT LINKED same\n', 120)

    def test_audit_at_120_columns(self):
        self.assert_snapshot(self.output(['audit', '/', '42:/', '--no-probe']),
                             'audit / in host vs 42:/\n'
                             'OK 1 mountpoints agree across the inspected views\n'
                             'MOUNT AUDIT (paths relative to prefix)\n'
                             'STATUS CLASS PATH             HOST PID 42 SAME LINK\n'
                             'NOTE   -     1 OK mountpoints -    -      same use -v to list\n', 120)

    def test_alert_audit_starts_at_column_zero(self):
        parse = self.mt.parse_mountinfo_line
        tables = {'host': [parse('1 1 0:1 / / rw - ext4 root rw')],
                  'pids': {42: [parse('10 10 0:9 / / rw - ext4 other rw')]},
                  'state': None}
        actual = self.output(['audit', '/', '42:/', '--no-probe'], tables=tables, expected_status=3)
        self.assert_snapshot(actual, 'audit / in host vs 42:/\n'
                             'ALERT 1 mountpoint differences need attention\n'
                             'ALERT DIVERGENT: .: both views have a mount here, but they serve different objects\n'
                             'next: Check which mount should serve this path, then repair the bind or mount source.\n'
                             'MOUNT AUDIT (paths relative to prefix)\n'
                             'STATUS    CLASS     PATH HOST         PID 42         SAME      LINK\n'
                             'DIVERGENT DIVERGENT .    #1 ext4 root #10 ext4 other different NOT LINKED\n', 120)

    def test_predict_at_120_columns(self):
        actual = self.output(['predict', '/', '42', '--no-probe'])
        self.assert_snapshot(actual, 'predict a new host mount at /\n'
                             'OK stays in the host view; this mount has no shared peer group\n'
                             'NOTE: no new mount in 1 inspected view\n', 120)
        verbose = self.output(['predict', '/', '42', '--no-probe', '-v'])
        self.assertIn('PREDICTION\n', verbose)
        self.assertIn('pid 42 not reached', verbose)

    def test_soulmask_at_120_columns(self):
        self.assert_snapshot(self.output(['soulmask', '42', '--volumes-root', '/srv/vol', '--no-probe']),
                             'soulmask audit /srv/vol\n'
                             'OK state and volume mount audits are clean\n'
                             'NOTE: no mountpoints in scope\n'
                             'NOTE: no state file: scanning for leftover mounts only\n'
                             'MOUNT AUDIT (paths relative to prefix): none\n', 120)

    def test_non_tty_without_columns_uses_120_columns(self):
        with patch.dict(os.environ):
            os.environ.pop('COLUMNS', None)
            with patch.object(sys, 'stdout', io.StringIO()):
                self.assertEqual(self.mt.output_width(), 120)
        with patch.dict(os.environ, {'COLUMNS': '40'}):
            self.assertEqual(self.mt.output_width(), 80)
        self.assert_snapshot(self.output(['explain', '/', '--no-probe'], None),
                             'host /\n'
                             'OK writes go to ext4 via #1, rw\n'
                             'NOTE: hidden: none on or below this path\n'
                             'PATH WALK\n'
                             'MOUNTPOINT ID FSTYPE SOURCE FSROOT RO PROPAGATION\n'
                             '/          #1 ext4   root   /      rw private\n', 120)


@unittest.skipUnless(userns_available(), 'unprivileged user+mount namespaces unavailable')
class TopologyTests(unittest.TestCase):

    def test_terminal_controls_are_visible_across_report_sites(self):
        out = run(r'''
        weird=$'m\033x\a\177\u0085\u202eINJECTED\nline'; state_name=$'s\033x\a\177\u0085\u202eINJECTED\nfile'
mkdir -p "$T/$weird" "$T/clean"
mount -t tmpfs lower "$T/$weird"
mount -t tmpfs upper "$T/$weird"
mkdir -p "$T/$weird/child"
mount -t tmpfs child "$T/$weird/child"
mount -t tmpfs target "$T/clean"
ln -s "$T/$weird" "$T/link"
S="$T/$state_name"
{ printf 'PI_ERRORS=1\n'; printf 'PI_ERROR=bad\033\a\n'; printf 'TARGET=%s\n' "$T/clean"; } > "$S"
mt explain "$$:$T/$weird" --submounts --no-probe
mt compare "$T/link" "$$:$T/$weird" "$$:$T/link" --no-probe
mt audit "$T/$weird" "$$:$T/$weird" --no-probe
mt soulmask "$$" --state-file "$S" --volumes-root "$T" --no-probe
mt predict "$T/$weird" "$$" --no-probe
''')
        reports = json_reports(out)
        self.assertEqual([item['command'] for item in reports],
                         ['explain', 'compare', 'audit', 'soulmask', 'predict'])
        self.assertIn(r'\u001b', out)
        self.assertIn(r'\u0007', out)
        self.assertIn(r'\nline', out)
        self.assertIn(r'\nfile', out)
        self.assertIn(r'\u007f', out)
        self.assertIn(r'\u0085', out)
        self.assertIn(r'\u202e', out)
        self.assertNotIn('\u202e', out)
        self.assertIn('\x1b', reports[0]['kv']['path'])
        self.assertIn('\n', reports[0]['kv']['path'])
        self.assertEqual(len(report_findings(out)), sum(len(item['findings']) for item in reports))
        for code in list(range(32)) + list(range(127, 160)):
            if code == 10:  # report layout uses line feed
                continue
            self.assertNotIn(chr(code), out, f'raw terminal control U+{code:04X}')

    def test_absolute_symlink_restarts_at_the_selected_root(self):
        out = run(r'''
mkdir -p "$T/absolute/target"
ln -s "$T/absolute/target" "$T/absolute/link"
mt explain "$T/absolute/link"
''')
        report = json_reports(out)[0]
        self.assertRegex(report['kv']['resolved_path'], r'/tmp/mounttrace-test-[^/]+/fs/absolute/target')
        self.assertIn('RC=0', out)

    def test_divergent_mount_audit_is_an_alert(self):
        out = run(r'''
mkdir -p "$T/divergent"
mount -t tmpfs host "$T/divergent"
spawn private "mount -t tmpfs container '$T/divergent'"
mt audit "$T/divergent" "$SPAWNED:$T/divergent"
''')
        reports = json_reports(out)
        self.assertEqual(reports[0]['verdict']['status'], 'ALERT')
        self.assertIn('DIVERGENT', [item['class'] for item in report_findings(out, 'ALERT')])
        self.assertIn('RC=3', out)

    def test_mountpoint_with_carriage_return_survives_mountinfo_read(self):
        out = run(r'''
weird=$'mount\rpoint'
mkdir -p "$T/$weird"
mount -t tmpfs carriage "$T/$weird"
echo "EXPECTED_PATH=$T/$weird"
mt explain "$T/$weird"
''')
        report = json_reports(out)[0]
        self.assertIn('\r', report['kv']['path'])
        self.assertTrue(any(row['SOURCE'] == 'carriage' for row in report_rows(out)))
        self.assertIn('RC=0', out)

    def test_soulmask_no_probe_keeps_symlinked_state_target_lexical(self):
        out = run(r'''
mkdir -p "$T/real"
mount -t tmpfs target "$T/real"
ln -s "$T/real" "$T/link"
printf 'RAMDISK=%s\nTARGET=%s\n' "$T/link" "$T/link" > "$T/state"
echo "EXPECTED_TARGET=$T/link"
echo "STATE_PATH=$T/state"
mt soulmask "$$" --state-file "$T/state" --volumes-root "$T" --no-probe
''')
        target = re.search(r'EXPECTED_TARGET=(.*)', out).group(1)
        report = json_reports(out)[0]
        self.assertEqual(report['kv']['targets'][0]['path'], target)
        self.assertTrue(any('is not a host mountpoint' in item['text'] for item in report['findings']))
        self.assertEqual(report['kv']['state_file'], re.search(r'STATE_PATH=([^\n]+)', out).group(1))

    def test_overmount_and_ancestor_hidden(self):
        out = run(r'''
mkdir -p "$T/d" "$T/x/sub"
mount -t tmpfs lowerA "$T/d"; touch "$T/d/a"
mount -t tmpfs upperB "$T/d"
mount -t tmpfs innerC "$T/x/sub"
mount -t tmpfs coverD "$T/x"
mt explain "$T/d"
mt explain "$T" --submounts
''')
        reports = json_reports(out)
        sources = [row['SOURCE'] for row in report_rows(out)]
        self.assertIn('lowerA', sources)
        self.assertIn('upperB', sources)
        self.assertIn('innerC', sources)
        self.assertIn('coverD', sources)
        notes = [finding['text'] for finding in report_findings(out, 'NOTE')]
        self.assertTrue(any('overmounted at the same mountpoint' in note for note in notes))
        self.assertTrue(any('resolving' in note and '/x/sub' in note for note in notes))
        self.assertEqual([item['command'] for item in reports], ['explain', 'explain'])
        self.assertIn('RC=0', out)

    def test_bind_fsroot_with_space_and_symlink(self):
        out = run(r'''
mkdir -p "$T/src dir/sub" "$T/b"
mount --bind "$T/src dir/sub" "$T/b"
ln -s "b" "$T/link"
mt explain "$T/link"
''')
        report = json_reports(out)[0]
        self.assertIn('/src dir/sub', [row['FSROOT'] for row in report_rows(out)])
        self.assertRegex(report['kv']['resolved_path'], r'/fs/b$')

    def test_private_container_keeps_ghost_after_host_umount(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
mount -t tmpfs soulmask-pi-abcdef12 "$T/vol/logs"
spawn private ':'
umount "$T/vol/logs"
mt audit "$T/vol" "$SPAWNED:$T/vol"
mt soulmask "$SPAWNED" --state-file "$T/none.state" --volumes-root "$T/vol"
''')
        findings = report_findings(out, 'ALERT')
        self.assertTrue(any(item['class'] == 'PID-ONLY' for item in findings))
        self.assertTrue(any('ghost copy' in item['text'] for item in findings))
        self.assertTrue(any('no state file' in item['text'] for item in report_findings(out, 'NOTE')))
        self.assertEqual(out.count('RC=3'), 2)

    def test_mapped_audit_game_container_view(self):
        # like a Wings game container: host volume dir bound to /home/container, rprivate
        out = run(r'''
U="$T/volumes/u1"; H="$T/home"; mkdir -p "$U/WS/Saved/Logs" "$H"
mount -t tmpfs soulmask-pi-u1 "$U/WS/Saved/Logs"
spawn private "mount --rbind '$U' '$H'"
echo "== running"
mt audit "$U" "$SPAWNED:$H" -v
umount "$U/WS/Saved/Logs"
echo "== after host teardown"
mt audit "$U" "$SPAWNED:$H"
''')
        running, after = out.split('== running', 1)[1].split('== after host teardown', 1)
        self.assertIn('LAYER', [row['CLASS'] for row in report_rows(running)])
        self.assertTrue(any(row['PATH'] == 'WS/Saved/Logs' and row['CLASS'] == 'OK'
                            for row in report_rows(running)))
        self.assertIn('RC=0', running)
        self.assertTrue(any(row['PATH'] == 'WS/Saved/Logs' and row['CLASS'] == 'PID-ONLY'
                            for row in report_rows(after)))
        self.assertIn('RC=3', after)

    def test_slave_receives_host_mounts_but_does_not_send(self):
        out = run(r'''
mkdir -p "$T/vol/logs" "$T/vol/c"
spawn slave "mount -t tmpfs ctr-own '$T/vol/c'"
mount -t tmpfs late "$T/vol/logs"
mt compare "$T/vol/logs" "$SPAWNED:$T/vol/logs"
mt audit "$T/vol" "$SPAWNED:$T/vol" -v
mt predict "$T/vol/new" "$SPAWNED"
''')
        reports = json_reports(out)
        self.assertTrue(any(row.get('SAME') == 'same' and row.get('LINK') == 'linked'
                            for row in report_rows(out)))
        self.assertTrue(any(row.get('CLASS') == 'OK' and row.get('PATH', '').endswith('logs')
                            for row in report_rows(out)))
        self.assertTrue(any(row.get('CLASS') == 'PID-ONLY' and row.get('PATH') == 'c'
                            for row in report_rows(out)))
        self.assertTrue(any(row.get('RESULT') == 'APPEARS' for row in report_rows(out)))
        self.assertEqual([item['command'] for item in reports], ['compare', 'audit', 'predict'])

    def test_shared_peer_sends_both_ways(self):
        out = run(r'''
mkdir -p "$T/vol/c"
spawn shared ':'
nsenter -t "$SPAWNED" -m mount -t tmpfs from-ctr "$T/vol/c"
mt explain "$T/vol/c"
mt predict "$T/vol/x" "$SPAWNED"
''')
        self.assertTrue(any(row['SOURCE'] == 'from-ctr' for row in report_rows(out)))
        self.assertIn('predict', [item['command'] for item in json_reports(out)])

    def test_private_container_is_not_linked(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn private ':'
mt compare "$T/vol/logs" "$SPAWNED:$T/vol/logs"
mt predict "$T/vol/logs" "$SPAWNED"
mount -t tmpfs late "$T/vol/logs"
mt audit "$T/vol" "$SPAWNED:$T/vol"
''')
        self.assertTrue(any(row.get('LINK') == 'NOT LINKED' for row in report_rows(out)))
        self.assertTrue(any(row.get('RESULT') == 'not reached' for row in report_rows(out)))
        self.assertTrue(any(row.get('CLASS') == 'HOST-ONLY' for row in report_rows(out)))

    def test_tucking_prediction_matches_kernel(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn slave "mount -t tmpfs ctr-own '$T/vol/logs'"
mt predict "$T/vol/logs" "$SPAWNED"
mount -t tmpfs host-late "$T/vol/logs"
mt explain "$SPAWNED:$T/vol/logs"
''')
        self.assertTrue(any(row.get('RESULT') == 'tucked' for row in report_rows(out)))
        # reality: the container still writes into its own tmpfs; the host's copy sits beneath it
        path_walk_rows = report_rows(out, 'PATH WALK')
        self.assertTrue(any(row['SOURCE'] == 'ctr-own' for row in path_walk_rows))
        hidden_rows = report_rows(out, 'HIDDEN MOUNTS')
        self.assertTrue(any(row['SOURCE'] == 'host-late' for row in hidden_rows))

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
mt predict "$T/vol" "$SPAWNED"
mount -t tmpfs newsrc "$T/vol"
echo "== after mount over the bind source directory"
mt explain "$SPAWNED:$T/cont"
umount "$T/src2"; mount -t tmpfs new2 "$T/src2"
echo "== after replacing the bind source MOUNT"
mt compare "$T/src2" "$SPAWNED:$T/cont2"
''')
        self.assertTrue(any(row.get('RESULT') == 'APPEARS' for row in report_rows(out)))
        after = out.split('== after mount over the bind source directory', 1)[1]
        self.assertTrue(any(row['SOURCE'] == 'newsrc' for row in report_rows(after)))
        after2 = out.split('== after replacing the bind source MOUNT', 1)[1]
        self.assertTrue(any(row['SOURCE'] == 'old' for row in report_rows(after2)))
        self.assertEqual(json_reports(after2)[0]['verdict']['status'], 'DIFFERENT')
        self.assertIn('NOT LINKED', [row.get('LINK') for row in report_rows(after2)])

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
mt soulmask "$SPAWNED" --state-file "$S" --volumes-root "$V"
# --options-mode ignore: a userns cannot re-apply the inherited uid= option
mount --options-mode ignore -o remount,ro "$R"
mkdir -p "$V/$A/stray"; mount -t tmpfs soulmask-pi-aaaaaaaa "$V/$A/stray"
echo "== broken"
mt soulmask "$$" --state-file "$S" --volumes-root "$V"
''')
        clean, broken = out.split('== clean', 1)[1].split('== broken', 1)
        clean_report = json_reports(clean)[0]
        notes = [item['text'] for item in report_findings(clean, 'NOTE')]
        self.assertTrue(any('shared WS/Content/Paks' in text for text in notes))
        self.assertTrue(any('per-instance WS/Saved/Logs' in text for text in notes))
        self.assertFalse(report_findings(clean, 'ALERT'))
        self.assertEqual(clean_report['verdict']['status'], 'OK')
        self.assertIn('RC=0', clean)
        broken_text = [item['text'] for item in report_findings(broken, 'ALERT')]
        self.assertTrue(any('READONLY=0 recorded but the RAMDISK is read-only' in text for text in broken_text))
        self.assertTrue(any('tagged per-instance tmpfs' in text for text in broken_text))
        self.assertIn('RC=3', broken)

    def test_watch_reports_change_and_vanished_pid(self):
        out = run(r'''
mkdir -p "$T/vol/logs"
spawn slave ':'
python3 "$MT" --json --watch 0.2 soulmask "$SPAWNED" --volumes-root "$T/vol" > "$T.watch" 2>&1 & W=$!
wait_for 'grep -q "\"command\":\"soulmask\"" "$T.watch"' 'the first pass'
mount -t tmpfs late "$T/vol/logs"
wait_for 'grep -q "mount_changes" "$T.watch"' 'the change report'
kill "$SPAWNED"; wait "$SPAWNED" 2>/dev/null || true
rc=0; wait "$W" || rc=$?
cat "$T.watch"; echo "WATCH_RC=$rc"
''')
        self.assertIn('mount_changes', out)
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
echo "== nested"; mt explain "$T/a/nested/dir"
echo "== bind"; mt explain "$T/b"
echo "== same"; mt compare "$T/top/@a/plain" "$T/a/plain"
''')
        show_id = re.search(r'SHOW_ID=(\d+)', out).group(1)
        d1, d2 = re.search(r'STATDEV=(\d+):(\d+)', out).groups()
        self.assertNotEqual(d1, d2, 'stat st_dev differs per subvolume')
        self.assertRegex(out, r'MIDEV=\d+:\d+=\d+:\d+\n')
        m1, m2 = re.search(r'MIDEV=(\d+:\d+)=(\d+:\d+)', out).groups()
        self.assertEqual(m1, m2, 'mountinfo shows one dev for all subvolume mounts of a filesystem')
        nested = json_reports(out)[0]
        self.assertEqual(nested['kv']['btrfs']['id'], int(show_id))
        self.assertTrue(any(item['path'].endswith('/nested') and item['kind'] == 'subvolume'
                            for item in nested['kv']['btrfs_boundaries']))
        self.assertIn('/@a', [row['FSROOT'] for table in nested['tables'] for row in table['rows']])
        bind = json_reports(out)[1]
        self.assertIn('/@a/plain', [row['FSROOT'] for table in bind['tables'] for row in table['rows']])
        same = json_reports(out)[2]
        self.assertEqual(same['verdict']['status'], 'OK')

    def test_btrfs_readonly_snapshot_and_empty_subvolume_stub(self):
        out = run_root(BTRFS_SETUP + r'''
btrfs subvolume snapshot -r "$T/top/@a" "$T/top/@a-snap" >/dev/null
echo "STUBINO=$(stat -c %i "$T/top/@a-snap/nested")"
if touch "$T/top/@a-snap/plain/f" 2>/dev/null; then echo TOUCH=ok; else echo TOUCH=refused; fi
echo "== snap"; mt explain "$T/top/@a-snap/plain"
echo "== stub"; mt explain "$T/top/@a-snap/nested/x"
''')
        self.assertIn('TOUCH=refused', out)
        snap = json_reports(out)[0]
        self.assertTrue(snap['kv']['btrfs']['readonly'])
        self.assertEqual(snap['verdict']['status'], 'OK')
        self.assertTrue(any(item['class'] == 'EROFS' for item in snap['findings']))
        self.assertIn('STUBINO=2', out)
        stub = json_reports(out)[1]
        self.assertTrue(any(item['kind'] == 'stub' for item in stub['kv']['btrfs_boundaries']))

    def test_btrfs_mount_through_one_mount_is_invisible_through_another(self):
        # The same btrfs directory reached via two separate mounts: a mount made through
        # one is attached to (that mount, dentry) and does not appear through the other.
        out = run_root(BTRFS_SETUP + r'''
echo "== predict"; mt predict "$T/top/@a/plain" "$$"
mount -t tmpfs via-top "$T/top/@a/plain"
echo "== via a"; mt explain "$T/a/plain"
''')
        prediction = json_reports(out)[0]
        self.assertEqual(prediction['kv']['filesystem_path'], '/@a/plain')
        via_a = json_reports(out)[1]
        self.assertTrue(any(row['FSTYPE'] == 'btrfs' for row in via_a['tables'][0]['rows']))
        self.assertNotIn('via-top', [row['SOURCE'] for table in via_a['tables'] for row in table['rows']])

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
echo "== default"; mt audit "$T/vol" "$p1:/data"
echo "== rslave"; mt audit "$T/vol" "$p2:/data" -v
echo "== link"; mt compare "$T/vol/late" "$p2:/data/late"
''', host_ns=True, containers=('default', 'rslave'))
        self.assertIn('PROP=rprivate:rslave', out)
        default = out.split('== default', 1)[1].split('== rslave', 1)[0]
        default_rows = report_rows(default)
        self.assertTrue(any(row.get('CLASS') == 'PID-ONLY' and row.get('PATH') == 'logs'
                            for row in default_rows))   # ghost of the unmounted tmpfs
        self.assertTrue(any(row.get('CLASS') == 'HOST-ONLY' and row.get('PATH') == 'late'
                            for row in default_rows))  # later host mount never arrives
        self.assertIn('RC=3', default)
        rslave = out.split('== rslave', 1)[1].split('== link', 1)[0]
        self.assertTrue(any(row.get('CLASS') == 'OK' and row.get('PATH') == 'late'
                            for row in report_rows(rslave)))
        self.assertNotIn('logs', [row.get('PATH') for row in report_rows(rslave)])
        self.assertIn('RC=0', rslave)
        self.assertIn('same', [row.get('SAME') for row in report_rows(out.split('== link', 1)[1])])

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
