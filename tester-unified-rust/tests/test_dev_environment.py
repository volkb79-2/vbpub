"""Stub-driven tests of the hostobs developer container wrapper and its image sources.

No docker daemon, no ssh and no network are used: `docker`, `id` and the host-table transport
(`hx.sh`, via the test-only HOSTOBS_DEV_HX seam) are stubs on PATH that record their argv. The
wrapper's REMOTE mode is exercised end to end by letting the stub transport run the remote
script locally under a temporary HOME. Needs HOSTOBS_ROOT.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOSTOBS_ROOT = Path(os.environ["HOSTOBS_ROOT"]).resolve()
REPO_ROOT = HOSTOBS_ROOT.parent
PROJECT_ROOT = REPO_ROOT / "tester-unified-rust"
DEV = HOSTOBS_ROOT / "tools" / "dev"
DOCKERFILE = PROJECT_ROOT / "Dockerfile.dev"
BUILD_DEV = PROJECT_ROOT / "build-dev-image.sh"

DOCKER_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["STUB_LOG_DIR"], "docker.log"), "a") as fh:
    fh.write(json.dumps(args) + "\\n")
verb = args[0] if args else ""
if verb == "ps":
    sys.stdout.write(os.environ.get("STUB_PS", ""))
    sys.exit(0)
if verb == "inspect":
    if "{{{{.State.Running}}}}" in args:
        state = os.environ.get("STUB_RUNNING", "absent")
        if state == "absent":
            sys.exit(1)
        print(state)
        sys.exit(0)
    if args[-1] == os.environ.get("STUB_SELF", "-"):
        sys.stdout.write(os.environ.get("STUB_MOUNTS", ""))
        sys.exit(0)
    sys.exit(1)
if verb == "volume" and args[1] == "inspect":
    sys.exit(0 if os.environ.get("STUB_VOLUME", "1") == "1" else 1)
if verb == "run":
    sys.exit(int(os.environ.get("STUB_RUN_RC", "0")))
sys.exit(0)
"""

HX_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_LOG_DIR/hx.count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_LOG_DIR/hx.count"
printf '%s\\n' "$1" >> "$STUB_LOG_DIR/hx.log"
printf '%s' "$2" > "$STUB_LOG_DIR/hx-$n.sh"
if [[ -n ${STUB_HX_RC:-} ]]; then exit "$STUB_HX_RC"; fi
exec bash -c "$2"
"""

ID_STUB = """#!/bin/bash
case "$1" in
  -u) echo "${STUB_UID:-1003}" ;;
  -g) echo "${STUB_GID:-1003}" ;;
  *) exec /usr/bin/id "$@" ;;
esac
"""

ALL_SUBS = [
    ["shell"],
    ["fixtures-shell"],
    ["run", "--", "echo", "hi"],
    ["build"],
    ["check", "-p", "hostobs-proto"],
    ["test", "-p", "hostobs-proto"],
    ["test", "--no-nextest", "-p", "hostobs-proto"],
    ["clippy"],
    ["fmt", "--check"],
    ["example", "hostobs-proto", "sp4_wire_size", "--", "x"],
    ["gdb", "--args", "/bin/true"],
    ["strace", "-c", "/bin/true"],
]
FORBIDDEN_FLAGS = ("--privileged", "--pid", "--net", "--cgroupns", "--ipc", "--uts", "--userns", "--device", "--mount", "--volumes-from")
FORBIDDEN_FRAGMENTS = ("docker.sock", "/sys", "/proc", "/dev", "propagation", "shared", "slave")


def worktree_hex(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]


class DevCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devtest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stubs = self.tmp / "stubs"
        self.logs = self.tmp / "logs"
        self.home = self.tmp / "home"
        for d in (self.stubs, self.logs, self.home, self.tmp / "psi"):
            d.mkdir()
        for name, text in (("docker", DOCKER_STUB), ("hx", HX_STUB), ("id", ID_STUB)):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        (self.tmp / "memory.current").write_text(str(1 << 30) + "\n")
        (self.tmp / "psi" / "memory").write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.tmp / "psi" / "io").write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        self.repo = self.make_repo(self.tmp / "repo")
        self.hex = worktree_hex(self.repo)
        self.dev = self.repo / "hostobs" / "tools" / "dev"

    def make_repo(self, root: Path) -> Path:
        (root / "hostobs" / "tools").mkdir(parents=True)
        shutil.copy2(DEV, root / "hostobs" / "tools" / "dev")
        (root / "hostobs" / "Cargo.toml").write_text("[workspace]\n")
        (root / "hostobs" / "keep.txt").write_text("committed\n")
        (root / "hostobs" / "gone.txt").write_text("to be deleted\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(root)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
        return root

    def env(self, **extra: str) -> dict:
        env = {
            "PATH": f"{self.stubs}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "LANG": "C",
            "STUB_LOG_DIR": str(self.logs),
            "CONTROLLER_HOME": str(self.tmp / "ctl"),
            "CONTROLLER_REMOTE_GATE_HOSTS": "v1001",
            "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
            "HOSTOBS_DEV_HX": str(self.stubs / "hx"),
            "HOSTOBS_DEV_TEST_SLICE_MEM_FILE": str(self.tmp / "memory.current"),
            "HOSTOBS_DEV_TEST_PSI_DIR": str(self.tmp / "psi"),
        }
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def run_dev(self, *args: str, stdin=subprocess.DEVNULL, unset: tuple = (), **extra: str):
        env = self.env(**extra)
        for key in unset:
            env.pop(key, None)
        return subprocess.run([str(self.dev), *args], env=env, stdin=stdin, capture_output=True, text=True, timeout=120)

    @staticmethod
    def argv_of(proc) -> list:
        lines = proc.stdout.splitlines()
        begin, end = lines.index("DOCKER-ARGV-BEGIN"), lines.index("DOCKER-ARGV-END")
        return lines[begin + 1:end]

    def docker_calls(self) -> list:
        path = self.logs / "docker.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class WrapperArgvTests(DevCase):
    def option(self, argv: list, name: str) -> str:
        return argv[argv.index(name) + 1]

    def assert_safe(self, sub: list, argv: list, hostdir: str) -> None:
        text = "\n".join(argv)
        self.assertEqual(argv[:2], ["docker", "run"])
        for flag in ("--rm", "--init"):
            self.assertIn(flag, argv)
        self.assertEqual(self.option(argv, "--network"), "none")
        self.assertEqual(self.option(argv, "--cap-drop"), "ALL")
        self.assertEqual(self.option(argv, "--security-opt"), "no-new-privileges")
        self.assertEqual(self.option(argv, "--pids-limit"), "1024")
        self.assertEqual(self.option(argv, "--memory"), "3g")
        self.assertEqual(self.option(argv, "--memory-swap"), "3g")
        self.assertEqual(self.option(argv, "--cpus"), "2")
        self.assertEqual(self.option(argv, "--cgroup-parent"), "dev-gates.slice")
        self.assertEqual(self.option(argv, "--user"), "1003:1003")
        self.assertEqual(self.option(argv, "--pull"), "never")
        self.assertEqual(self.option(argv, "--name"), f"hostobs-dev-{self.hex}")
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, text, fragment)
        for arg in argv:
            self.assertNotIn(arg.split("=")[0], FORBIDDEN_FLAGS, arg)
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(mounts, [f"{hostdir}:/work", f"hostobs-dev-target-{self.hex}:/home/gate/.cache/hostobs-target"])
        self.assertEqual(self.option(argv, "-w"), "/work/hostobs")
        for env in ("CARGO_NET_OFFLINE=true", "RUST_BACKTRACE=1", "RUST_LIB_BACKTRACE=1"):
            self.assertEqual(argv[argv.index(env) - 1], "-e")
        self.assertNotIn("CARGO_TARGET_DIR", text)
        caps = [argv[i + 1] for i, a in enumerate(argv) if a == "--cap-add"]
        self.assertEqual(caps, ["SYS_PTRACE"] if sub[0] in ("gdb", "strace") else [])
        core = self.option(argv, "--ulimit")
        self.assertEqual(core, "core=-1:-1" if sub[0] == "gdb" else "core=0:0")
        image = argv.index("tester-unified-rust-dev:local")
        self.assertEqual(argv[image + 1:image + 6], ["nice", "-n", "10", "ionice", "-c3"])

    def test_remote_is_the_default_mode_and_every_subcommand_is_safe(self) -> None:
        for sub in ALL_SUBS:
            with self.subTest(sub=sub):
                proc = self.run_dev("--dry-run", *sub)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("STEP 2: preflight:", proc.stdout)
                self.assertIn("STEP 6: sync:", proc.stdout)
                self.assertIn("git bundle create in.bundle HEAD", proc.stdout)
                self.assertIn("git diff HEAD --binary", proc.stdout)
                self.assert_safe(sub, self.argv_of(proc), f"$HOME/hostobs-dev/{self.hex}/work")
        # a dry run touches neither docker nor the transport
        self.assertEqual(self.docker_calls(), [])
        self.assertFalse((self.logs / "hx.log").exists())

    def test_local_mode_is_refused_without_the_override_and_safe_with_it(self) -> None:
        proc = self.run_dev("--dry-run", "--local", "shell")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not recommended", proc.stderr)
        self.assertEqual(self.docker_calls(), [])
        # the worktree is /tmp/.../repo: map it through a mount whose destination is its parent
        mounts = f"{self.tmp}\t/host/base\n"
        for sub in ALL_SUBS:
            with self.subTest(sub=sub):
                proc = self.run_dev("--dry-run", "--local", *sub, HOSTOBS_DEV_ALLOW_LOCAL="1",
                                    STUB_SELF="selfbox", HOSTOBS_DEV_SELF_CONTAINER="selfbox", STUB_MOUNTS=mounts)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assert_safe(sub, self.argv_of(proc), "/host/base/repo")
        # the only docker calls are the read-only inspect of ourselves and the prefix listing; never `run`
        for call in self.docker_calls():
            self.assertIn(call[0], ("inspect", "ps"), call)
            if call[0] == "ps":
                self.assertEqual(call, ["ps", "--filter", "name=^hostobs-dev-", "--format", "{{.Names}}"])
            self.assertFalse(any("ancestor" in a or a in ("-a", "--all") for a in call), call)

    def test_local_mode_without_a_mount_for_the_worktree_is_refused(self) -> None:
        proc = self.run_dev("--dry-run", "--local", "shell", HOSTOBS_DEV_ALLOW_LOCAL="1",
                            STUB_SELF="selfbox", HOSTOBS_DEV_SELF_CONTAINER="selfbox", STUB_MOUNTS="/elsewhere\t/x\n")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not on a bind mount", proc.stderr)

    def test_gdb_and_strace_are_the_only_subcommands_with_sys_ptrace(self) -> None:
        for sub in ALL_SUBS:
            argv = self.argv_of(self.run_dev("--dry-run", *sub))
            self.assertEqual("--cap-add" in argv, sub[0] in ("gdb", "strace"), sub)
        gdb = self.argv_of(self.run_dev("--dry-run", "gdb", "--args", "/bin/true"))
        self.assertEqual(gdb[gdb.index("rust-gdb"):],
                         ["rust-gdb", "-iex", "set cwd /home/gate/.cache/hostobs-target",
                          "-iex", "set disable-randomization off", "--args", "/bin/true"])

    def test_subcommands_map_to_the_checked_cargo_wrapper(self) -> None:
        def tail(*args: str) -> list:
            argv = self.argv_of(self.run_dev("--dry-run", *args))
            return argv[argv.index("-c3") + 1:]
        cargo = "tools/cargo-offline.sh"
        self.assertEqual(tail("build", "-p", "a"), [cargo, "build", "--offline", "--locked", "-p", "a"])
        self.assertEqual(tail("check"), [cargo, "check", "--offline", "--locked"])
        self.assertEqual(tail("test", "-p", "a", "t"), [cargo, "nextest", "run", "--offline", "--locked", "-p", "a", "t"])
        self.assertEqual(tail("test", "--no-nextest", "-p", "a"), [cargo, "test", "--offline", "--locked", "-p", "a"])
        self.assertEqual(tail("clippy"), [cargo, "clippy", "--offline", "--locked", "--workspace", "--all-targets", "--", "-D", "warnings"])
        self.assertEqual(tail("fmt", "--check"), [cargo, "fmt", "--all", "--check"])
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size", "a"),
                         [cargo, "run", "--offline", "--locked", "-p", "hostobs-proto", "--example", "sp4_wire_size", "--", "a"])
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size"),
                         [cargo, "run", "--offline", "--locked", "-p", "hostobs-proto", "--example", "sp4_wire_size"])
        self.assertEqual(tail("run", "--", "ls", "-l"), ["ls", "-l"])
        self.assertEqual(tail("shell"), ["bash"])
        self.assertEqual(tail("fixtures-shell"), ["bash"])
        self.assertEqual(self.run_dev("--dry-run", "run").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "nonsense").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "example", "only-one").returncode, 2)

    def test_tty_flags_only_when_stdin_is_a_terminal(self) -> None:
        batch = self.argv_of(self.run_dev("--dry-run", "shell"))
        self.assertNotIn("-it", batch)
        self.assertNotIn("-t", batch)
        master, slave = pty.openpty()
        try:
            proc = self.run_dev("--dry-run", "shell", stdin=slave)
        finally:
            os.close(master)
            os.close(slave)
        self.assertIn("-it", self.argv_of(proc))

    def test_resource_limits_follow_the_environment_and_are_validated(self) -> None:
        argv = self.argv_of(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MEMORY="2g", HOSTOBS_DEV_CPUS="1.5"))
        self.assertEqual((argv[argv.index("--memory") + 1], argv[argv.index("--memory-swap") + 1], argv[argv.index("--cpus") + 1]),
                         ("2g", "2g", "1.5"))
        self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MEMORY="3g --privileged").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_CPUS="x").returncode, 2)

    def test_the_governed_slice_is_required(self) -> None:
        for args in (("--dry-run", "shell"), ("--dry-run", "--local", "shell")):
            proc = self.run_dev(*args, unset=("CGROUP_PARENT_DEV_GATES",), HOSTOBS_DEV_ALLOW_LOCAL="1")
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("CGROUP_PARENT_DEV_GATES is required", proc.stderr)

    def test_only_the_v1001_alias_is_accepted(self) -> None:
        self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_HOST="r1002", CONTROLLER_REMOTE_GATE_HOSTS="r1002 v1001").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "shell", CONTROLLER_REMOTE_GATE_HOSTS="r1002 v1001").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_HOST="v1001", CONTROLLER_REMOTE_GATE_HOSTS="r1002 v1001").returncode, 0)

    def test_sync_cap_refuses_an_oversized_overlay_before_any_call(self) -> None:
        big = self.repo / "hostobs" / "big.bin"
        with big.open("wb") as fh:
            fh.truncate(21 * 1024 * 1024)
        for args in (("--dry-run", "shell"), ("shell",)):
            proc = self.run_dev(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("sync cap", proc.stderr)
        self.assertFalse((self.logs / "hx.log").exists())
        big.unlink()
        with big.open("wb") as fh:
            fh.truncate(19 * 1024 * 1024)
        self.assertEqual(self.run_dev("--dry-run", "shell").returncode, 0)


class WrapperRemoteExecutionTests(DevCase):
    """The stub transport runs each remote script locally under the temporary HOME."""

    def work(self) -> Path:
        return self.home / "hostobs-dev" / self.hex / "work"

    def test_sync_resets_to_the_bundled_head_applies_the_patch_and_never_accumulates(self) -> None:
        (self.repo / "hostobs" / "keep.txt").write_text("edited\n")
        (self.repo / "hostobs" / "gone.txt").unlink()
        (self.repo / "hostobs" / "new.txt").write_text("untracked\n")
        proc = self.run_dev("run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        work = self.work() / "hostobs"
        self.assertEqual((work / "keep.txt").read_text(), "edited\n")
        self.assertFalse((work / "gone.txt").exists())
        self.assertEqual((work / "new.txt").read_text(), "untracked\n")
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(subprocess.run(["git", "-C", str(self.work()), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), head)
        # stale state on the host is wiped by the next sync
        (work / "stale.txt").write_text("left behind\n")
        (work / "keep.txt").write_text("host-side edit\n")
        proc = self.run_dev("run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((work / "stale.txt").exists())
        self.assertEqual((work / "keep.txt").read_text(), "edited\n")
        # three transport calls per run (preflight, sync, run), every one by the v1001 alias, all through the stub
        hosts = (self.logs / "hx.log").read_text().split()
        self.assertEqual(hosts, ["v1001"] * 6)
        runs = [c for c in self.docker_calls() if c[0] == "run"]
        self.assertEqual(len(runs), 2)
        self.assertIn(f"{self.home}/hostobs-dev/{self.hex}/work:/work", runs[0])

    def test_the_exit_code_of_the_command_is_propagated_and_the_container_removed_by_exact_name(self) -> None:
        proc = self.run_dev("run", "--", "false", STUB_RUN_RC="7")
        self.assertEqual(proc.returncode, 7)
        rms = [c for c in self.docker_calls() if c[0] == "rm"]
        self.assertEqual(rms, [["rm", "-f", f"hostobs-dev-{self.hex}"]])

    def test_every_docker_call_on_the_host_is_by_exact_name_or_the_own_prefix(self) -> None:
        self.run_dev("run", "--", "true")
        self.run_dev("status", STUB_RUNNING="false")
        self.run_dev("clean", STUB_RUNNING="absent")
        for call in self.docker_calls():
            self.assertFalse(any("ancestor" in a or a in ("-a", "--all", "prune") for a in call), call)
            if call[0] == "ps":
                self.assertEqual(call, ["ps", "--filter", "name=^hostobs-dev-", "--format", "{{.Names}}"])
            elif call[0] in ("inspect", "rm", "exec") or call[:2] in (["volume", "inspect"], ["volume", "rm"]):
                self.assertTrue(any(a in (f"hostobs-dev-{self.hex}", f"hostobs-dev-target-{self.hex}") for a in call), call)
            else:
                self.assertEqual(call[0], "run", call)

    def test_memory_gate_below_at_and_above_the_threshold(self) -> None:
        limit = 3584 * 1048576
        for value, expected in ((limit - 1, 0), (limit, 0), (limit + 1, 75), (8 << 30, 75)):
            with self.subTest(memory_current=value):
                (self.tmp / "memory.current").write_text(f"{value}\n")
                before = len([c for c in self.docker_calls() if c[0] == "run"])
                proc = self.run_dev("run", "--", "true")
                self.assertEqual(proc.returncode, expected, proc.stderr)
                started = len([c for c in self.docker_calls() if c[0] == "run"]) - before
                self.assertEqual(started, 1 if expected == 0 else 0)
                if expected:
                    self.assertIn("dev-gates.slice memory.current", proc.stderr)
        # an unreadable value is a refusal, never a pass
        (self.tmp / "memory.current").write_text("garbage\n")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        # the limit is a single variable
        (self.tmp / "memory.current").write_text(f"{limit + 1}\n")
        self.assertEqual(self.run_dev("run", "--", "true", HOSTOBS_DEV_SLICE_MEM_MAX_MB="4096").returncode, 0)

    def test_psi_gate_uses_memory_some_and_io_full_with_the_remote_gate_limit(self) -> None:
        def write(memory_some: str, io_full: str, cpu_like_io_some: str = "0.00") -> None:
            (self.tmp / "psi" / "memory").write_text(f"some avg10={memory_some} avg60=0 avg300=0 total=0\nfull avg10=99.00 avg60=0 avg300=0 total=0\n")
            (self.tmp / "psi" / "io").write_text(f"some avg10={cpu_like_io_some} avg60=0 avg300=0 total=0\nfull avg10={io_full} avg60=0 avg300=0 total=0\n")
        write("25.00", "25.00", "99.00")      # at the limit, memory full and io some are not signals
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        write("25.01", "0.00")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        write("0.00", "25.01")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)

    def test_a_running_dev_container_refuses_with_3_unless_parallel_is_allowed(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_PS="hostobs-dev-abcdefabcdef\n")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("already running", proc.stderr)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "run"], [])
        proc = self.run_dev("run", "--", "true", STUB_PS="hostobs-dev-abcdefabcdef\n", HOSTOBS_DEV_ALLOW_PARALLEL="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # the LOCAL mode applies the same rule
        proc = self.run_dev("--dry-run", "--local", "shell", HOSTOBS_DEV_ALLOW_LOCAL="1", STUB_PS="hostobs-dev-x\n",
                            STUB_SELF="s", HOSTOBS_DEV_SELF_CONTAINER="s", STUB_MOUNTS=f"{self.tmp}\t/h\n")
        self.assertEqual(proc.returncode, 3)

    def test_wrong_uid_is_refused_where_the_files_live(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_UID="1000", STUB_GID="1000")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("1003:1003", proc.stderr)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "run"], [])
        proc = self.run_dev("--dry-run", "--local", "shell", HOSTOBS_DEV_ALLOW_LOCAL="1", STUB_UID="1000",
                            STUB_SELF="s", HOSTOBS_DEV_SELF_CONTAINER="s", STUB_MOUNTS=f"{self.tmp}\t/h\n")
        self.assertEqual(proc.returncode, 2)

    def test_identity_and_transport_failures_map_to_3(self) -> None:
        for rc in ("97", "98", "255"):
            with self.subTest(hx_exit=rc):
                self.assertEqual(self.run_dev("run", "--", "true", STUB_HX_RC=rc).returncode, 3)

    def test_admission_slot_busy_is_75_and_a_free_slot_is_released(self) -> None:
        lockdir = self.tmp / "ctl" / "remote-gate-locks"
        lockdir.mkdir(parents=True)
        held = []
        try:
            for i in range(1, 5):
                fh = (lockdir / f"v1001.slot{i}").open("w")
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(fh)
            proc = self.run_dev("run", "--", "true", CONTROLLER_REMOTE_SLOT_WAIT_S="0")
            self.assertEqual(proc.returncode, 75, proc.stderr)
            self.assertFalse((self.logs / "hx.log").exists())
        finally:
            for fh in held:
                fh.close()
        self.assertEqual(self.run_dev("run", "--", "true", CONTROLLER_REMOTE_SLOT_WAIT_S="0").returncode, 0)
        # released afterwards: the next call can take the slot again
        self.assertEqual(self.run_dev("run", "--", "true", CONTROLLER_REMOTE_SLOT_WAIT_S="0").returncode, 0)

    def test_status_and_clean_name_the_exact_volume_and_refuse_while_running(self) -> None:
        proc = self.run_dev("status", STUB_RUNNING="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"hostobs-dev-{self.hex}", proc.stdout)
        self.assertIn(f"hostobs-dev-target-{self.hex}", proc.stdout)
        proc = self.run_dev("clean", STUB_RUNNING="true")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual([c for c in self.docker_calls() if c[:2] == ["volume", "rm"]], [])
        proc = self.run_dev("clean", "--checkout", STUB_RUNNING="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"volume: hostobs-dev-target-{self.hex}", proc.stdout)
        self.assertEqual([c for c in self.docker_calls() if c[:2] == ["volume", "rm"]], [["volume", "rm", f"hostobs-dev-target-{self.hex}"]])
        self.assertEqual(self.run_dev("clean", "--everything").returncode, 2)


class ImageSourceTests(unittest.TestCase):
    def test_scripts_are_valid_bash_and_executable(self) -> None:
        for script in (DEV, BUILD_DEV):
            self.assertTrue(os.access(script, os.X_OK), script)
            self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0, script)
        self.assertIn("set -euo pipefail", DEV.read_text())

    def test_wrapper_never_asks_for_host_namespaces_or_a_second_transport(self) -> None:
        text = DEV.read_text()
        for fragment in ("--privileged", "--pid=host", "--net=host", "--network host", "--cgroupns=host", "docker.sock", "ancestor="):
            self.assertNotIn(fragment, text, fragment)
        self.assertIn("scripts/controller/hx.sh", text)
        self.assertNotRegex(text, r"(?m)^\s*ssh\b")

    def test_dockerfile_dev_installs_exactly_the_listed_packages_and_ends_as_the_gate_user(self) -> None:
        text = DOCKERFILE.read_text()
        self.assertRegex(text, r"(?m)^ARG BASE_IMAGE=tester-unified-rust:local$")
        self.assertIn("FROM ${BASE_IMAGE}", text)
        self.assertIn('org.vbpub.tester-unified-rust-dev.base-image="${BASE_IMAGE}"', text)
        install = re.search(r"apt-get install -y --no-install-recommends \\\n\s+(.+?) \\\n", text)
        self.assertIsNotNone(install)
        self.assertEqual(set(install.group(1).split()), {"gdb", "strace", "procps", "less", "ca-certificates"})
        self.assertEqual(text.count("apt-get install"), 1)
        users = re.findall(r"(?m)^USER (.+)$", text)
        self.assertEqual(users, ["root", "1003:1003"])
        directives = [line.split()[0] for line in text.splitlines() if re.match(r"^[A-Z]+ ", line)]
        self.assertIn("USER", directives)
        self.assertLess(text.index("USER 1003:1003"), text.index("WORKDIR /work"))
        self.assertEqual(directives[-1], "CMD")
        self.assertRegex(text, r"(?m)^WORKDIR /work$")

    def test_dockerfile_dev_copies_nothing_and_leaves_the_cache_and_identity_alone(self) -> None:
        text = "\n".join(line for line in DOCKERFILE.read_text().splitlines() if not line.lstrip().startswith("#"))
        for directive in ("COPY", "ADD"):
            self.assertIsNone(re.search(rf"(?m)^{directive}\b", text), directive)
        for fragment in ("chown", "cargo fetch", "cargo install", "/usr/local/cargo", "registry", "rm -rf /usr/local",
                         "Cargo.lock.registry-sha256", "rust-crate-cache", "rustup update", "rustup toolchain", "rustup install",
                         "rustup default", "rustup self", "curl", "wget"):
            self.assertNotIn(fragment, text, fragment)
        # the one mention of the baked lock is the read-only verifier call
        self.assertEqual(text.count("hostobs-Cargo.lock"), 1)
        self.assertIn("/usr/local/bin/verify-crate-cache /usr/local/share/hostobs-Cargo.lock", text)

    def test_dockerfile_dev_pins_the_installed_toolchain_and_fails_on_any_change(self) -> None:
        text = DOCKERFILE.read_text()
        self.assertIn('before="$(rustc -vV)"', text)
        self.assertIn('after="$(rustc -vV)"', text)
        self.assertIn('test "$before" = "$after"', text)
        self.assertIn('toolchain="$(rustup show active-toolchain | cut -d\' \' -f1)"', text)
        self.assertEqual(text.count("rustup component add"), 1)
        self.assertIn('rustup component add --toolchain "$toolchain" rust-src rust-analyzer', text)
        self.assertLess(text.index('before="$(rustc -vV)"'), text.index("rustup component add"))
        self.assertLess(text.index("rustup component add"), text.index('after="$(rustc -vV)"'))
        self.assertIn("dpkg-query -W gdb strace procps less ca-certificates", text)
        self.assertIn("rust-analyzer --version", text)

    def test_build_dev_image_mirrors_build_image_and_records_versions(self) -> None:
        text = BUILD_DEV.read_text()
        self.assertIn('image_tag="tester-unified-rust-dev:local"', text)
        self.assertIn('base_image="tester-unified-rust:local"', text)
        self.assertIn('[[ -n "$cgroup_parent" ]] || {', text)
        self.assertIn('refusing an ungoverned image build', text)
        self.assertIn('builder="mdt-managed"', text)
        self.assertIn('--build-arg "BASE_IMAGE=$base_image"', text)
        self.assertIn('--cgroup-parent "$cgroup_parent"', text)
        self.assertIn("--base)", text)
        self.assertIn("--record)", text)
        self.assertIn("org.vbpub.tester-unified-rust-dev.base-image", text)
        self.assertIn("dpkg-query -W gdb strace procps less ca-certificates", text)
        self.assertIn("rust-analyzer", text)
        self.assertIn("build date", text)

    def test_build_dev_image_refuses_without_a_governed_slice_and_documents_options(self) -> None:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": tempfile.gettempdir()}
        proc = subprocess.run([str(BUILD_DEV)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("refusing an ungoverned image build", proc.stderr)
        proc = subprocess.run([str(BUILD_DEV), "--help"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2)
        for option in ("--tag", "--base", "--cgroup-parent", "--record"):
            self.assertIn(option, proc.stderr)

    def test_selftest_lane_syntax_checks_the_new_scripts(self) -> None:
        text = (PROJECT_ROOT / "run-gate.toml").read_text()
        self.assertIn("build-dev-image.sh", text)
        self.assertIn('"$HOSTOBS_ROOT/tools/dev"', text)


if __name__ == "__main__":
    unittest.main()
