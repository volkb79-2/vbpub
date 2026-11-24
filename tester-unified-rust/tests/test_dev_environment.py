"""Stub-driven tests of the hostobs developer container wrapper and its image sources.

No docker daemon, no ssh and no network are used: `docker`, `id` and the host-table transport
(`hx.sh`, via the test-only HOSTOBS_DEV_HX seam) are stubs on PATH that record their argv. The
stub transport copies the real scripts/controller/hx.sh layering (guard block under `set -e`,
then the command, the whole thing shlex-quoted into `bash -c`, run through `sh -c`), so every
quoting layer of the remote script is exercised. The wrapper's REMOTE mode is exercised end to
end by letting that transport run the remote script locally under a temporary HOME. Needs HOSTOBS_ROOT.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
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
    if "{{{{.State.StartedAt}}}}" in args:
        print(os.environ.get("STUB_STARTED", "2020-01-01T00:00:00Z"))
        sys.exit(0)
    if "{{{{.Id}}}}" in args:
        sys.exit(0 if os.environ.get("STUB_EXISTS") == "1" else 1)
    if args[-1] == os.environ.get("STUB_SELF", "-"):
        sys.stdout.write(os.environ.get("STUB_MOUNTS", ""))
        sys.exit(0)
    sys.exit(1)
if verb == "volume" and args[1] == "inspect":
    sys.exit(0 if os.environ.get("STUB_VOLUME", "1") == "1" else 1)
if verb == "run":
    fds = {{}}
    for n in (8, 9):
        try:
            fds[n] = os.readlink("/proc/self/fd/%d" % n)
        except OSError:
            fds[n] = None
    with open(os.path.join(os.environ["STUB_LOG_DIR"], "fds.log"), "a") as fh:
        fh.write(json.dumps(fds) + "\\n")
    hold = os.environ.get("STUB_RUN_HOLD")
    if hold:
        import time
        open(hold + ".started", "w").close()
        for _ in range(300):
            if os.path.exists(hold):
                break
            time.sleep(0.1)
    if "--cidfile" in args:
        with open(args[args.index("--cidfile") + 1], "w") as fh:
            if not os.environ.get("STUB_NO_CID"):
                fh.write("cid-stub-1")
    sys.exit(int(os.environ.get("STUB_RUN_RC", "0")))
sys.exit(0)
"""

# the real layering of scripts/controller/hx.sh: guard under `set -e`, then the command, shlex-quoted into `bash -c`
REAL_GIT = shutil.which("git") or "/usr/bin/git"
GIT_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["STUB_LOG_DIR"], "git.log"), "a") as fh:
    fh.write(json.dumps({{"args": args, "fds": sorted(os.listdir("/proc/self/fd"))}}) + "\\n")
os.execv({REAL_GIT!r}, [{REAL_GIT!r}] + args)
"""

HX_STUB = """#!/bin/bash
n=$(( $(cat "$STUB_LOG_DIR/hx.count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_LOG_DIR/hx.count"
printf '%s\\n' "$1" >> "$STUB_LOG_DIR/hx.log"
printf '%s' "$2" > "$STUB_LOG_DIR/hx-$n.sh"
if [[ -n ${STUB_HX_RC:-} ]]; then exit "$STUB_HX_RC"; fi
guard='true'
remote_script=$(printf 'set -e\\nif ! { %s\\n}; then printf "GUARD-FAIL\\\\n" >&2; exit 98; fi\\n%s\\n' "$guard" "$2")
quoted_script=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$remote_script") || exit 2
remote="bash -c $quoted_script"
if [[ $n == 1 && -n ${STUB_INJECT:-} ]]; then
  sh -c "$remote"; rc=$?
  printf '%b' "$STUB_INJECT"
  exit $rc
fi
exec sh -c "$remote"
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
TIMEOUT_PREFIX = ["timeout", "--foreground", "-k", "5", "3600"]
SKIP = 1 + len(TIMEOUT_PREFIX)     # words after "-c3" before the user's command
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
        for name, text in (("docker", DOCKER_STUB), ("hx", HX_STUB), ("id", ID_STUB), ("git", GIT_STUB)):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        (self.tmp / "memory.current").write_text(str(1 << 30) + "\n")
        self.write_psi("0.00", "0.00")
        self.repo = self.make_repo(self.tmp / "repo")
        self.hex = worktree_hex(self.repo)
        self.dev = self.repo / "hostobs" / "tools" / "dev"

    def write_psi(self, memory_some: str, io_full: str, io_some: str = "0.00") -> None:
        (self.tmp / "psi" / "memory").write_text(
            f"some avg10={memory_some} avg60=0.00 avg300=0.00 total=0\nfull avg10=99.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.tmp / "psi" / "io").write_text(
            f"some avg10={io_some} avg60=0.00 avg300=0.00 total=0\nfull avg10={io_full} avg60=0.00 avg300=0.00 total=0\n")

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
        return env

    def run_dev(self, *args: str, stdin=subprocess.DEVNULL, unset: tuple = (), **extra: str):
        env = self.env(**extra)
        for key in unset:
            env.pop(key, None)
        return subprocess.run([str(self.dev), *args], env=env, stdin=stdin, capture_output=True, text=True, timeout=120)

    def run_pty(self, *args: str, **extra: str):
        """Run with stdin AND stdout on a pseudo terminal; returns (returncode, terminal output)."""
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen([str(self.dev), *args], env=self.env(**extra), stdin=slave, stdout=slave,
                                    stderr=subprocess.PIPE, text=True)
            _, err = proc.communicate(timeout=120)
            chunks = b""
            while select.select([master], [], [], 0.2)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks += data
        finally:
            os.close(master)
            os.close(slave)
        return proc.returncode, chunks.decode(errors="replace"), err

    @staticmethod
    def argv_of(proc) -> list:
        lines = proc.stdout.splitlines()
        begin, end = lines.index("DOCKER-ARGV-BEGIN"), lines.index("DOCKER-ARGV-END")
        return lines[begin + 1:end]

    def docker_calls(self) -> list:
        path = self.logs / "docker.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def runs(self) -> list:
        return [c for c in self.docker_calls() if c[0] == "run"]


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
        self.assertEqual(self.option(argv, "--stop-timeout"), "10")
        self.assertEqual(self.option(argv, "--name"), f"hostobs-dev-{self.hex}")
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, text, fragment)
        for arg in argv:
            self.assertNotIn(arg.split("=")[0], FORBIDDEN_FLAGS, arg)
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(mounts, [f"{hostdir}:/work", f"{hostdir}/.git:/work/.git:ro",
                                  f"hostobs-dev-target-{self.hex}:/home/gate/.cache/hostobs-target"])
        self.assertEqual(self.option(argv, "-w"), "/work/hostobs")
        for env in ("CARGO_NET_OFFLINE=true", "RUST_BACKTRACE=1", "RUST_LIB_BACKTRACE=1"):
            self.assertEqual(argv[argv.index(env) - 1], "-e")
        self.assertNotIn("CARGO_TARGET_DIR", text)
        caps = [argv[i + 1] for i, a in enumerate(argv) if a == "--cap-add"]
        self.assertEqual(caps, ["SYS_PTRACE"] if sub[0] in ("gdb", "strace") else [])
        # core dumps are off everywhere: a piped core_pattern would send them to a host handler outside the cgroup
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--ulimit"], ["core=0:0"])
        image = argv.index("tester-unified-rust-dev:rs-20261124")
        self.assertEqual(argv[image + 1:image + 6], ["nice", "-n", "10", "ionice", "-c3"])
        # the deadline also runs INSIDE the container, so a killed wrapper cannot leave an unbounded container
        self.assertEqual(argv[image + 6:image + 11], TIMEOUT_PREFIX)

    def test_remote_is_the_default_mode_and_every_subcommand_is_safe(self) -> None:
        for sub in ALL_SUBS:
            with self.subTest(sub=sub):
                proc = self.run_dev("--dry-run", *sub)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("STEP 2: preflight:", proc.stdout)
                self.assertIn("STEP 6: upload:", proc.stdout)
                self.assertIn("git bundle create in.bundle HEAD", proc.stdout)
                self.assertIn("git diff HEAD --binary", proc.stdout)
                argv = self.argv_of(proc)
                self.assert_safe(sub, argv, f"$HOME/hostobs-dev/{self.hex}/work")
                self.assertEqual(self.option(argv, "--cidfile"), f"$HOME/hostobs-dev/{self.hex}/cid")
        # a dry run touches neither docker nor the transport
        self.assertEqual(self.docker_calls(), [])
        self.assertFalse((self.logs / "hx.log").exists())

    def test_the_git_directory_is_mounted_read_only_for_every_container_subcommand(self) -> None:
        for sub in ALL_SUBS:
            argv = self.argv_of(self.run_dev("--dry-run", *sub))
            self.assertIn(f"$HOME/hostobs-dev/{self.hex}/work/.git:/work/.git:ro", argv, sub)
            self.assertLess(argv.index(f"$HOME/hostobs-dev/{self.hex}/work:/work"),
                            argv.index(f"$HOME/hostobs-dev/{self.hex}/work/.git:/work/.git:ro"))
        script = self.run_dev("--dry-run", "shell").stdout
        self.assertIn('-v "$W/.git:/work/.git:ro"', script)

    def test_the_host_side_git_calls_are_hardened(self) -> None:
        script = self.run_dev("--dry-run", "shell").stdout
        self.assertIn("export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null", script)
        self.assertIn("g() { git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c gc.auto=0 -c maintenance.auto=false -c protocol.allow=never -c protocol.file.allow=always", script)
        for line in script.splitlines():
            if re.search(r"(?:^|[\s(`])git\s+-", line) and "g() {" not in line:
                # every other git call (read-only probes in the preflight and status) carries hooksPath and fsmonitor off
                self.assertIn("core.hooksPath=/dev/null", line, line)
                self.assertIn("core.fsmonitor=false", line, line)
        self.assertIn('rm -rf "$W/.git/hooks"', script)
        self.assertIn('mkdir -m 0755 "$W/.git/hooks"', script)
        self.assertNotRegex(script, r"(?m)^(?!.*core\.hooksPath)(?!g\(\))[^#\n]*\bgit (clone|init|fetch|checkout|reset|clean|apply)\b")

    def test_local_mode_is_refused_without_the_override_and_safe_with_it(self) -> None:
        proc = self.run_dev("--dry-run", "--local", "shell")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not recommended", proc.stderr)
        self.assertEqual(self.docker_calls(), [])
        mounts = f"{self.tmp}\t/host/base\n"
        for sub in ALL_SUBS:
            with self.subTest(sub=sub):
                proc = self.run_dev("--dry-run", "--local", *sub, HOSTOBS_DEV_ALLOW_LOCAL="1",
                                    STUB_SELF="selfbox", HOSTOBS_DEV_SELF_CONTAINER="selfbox", STUB_MOUNTS=mounts)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assert_safe(sub, self.argv_of(proc), "/host/base/repo")
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
            return argv[argv.index("-c3") + SKIP:]
        cargo = "tools/cargo-offline.sh"
        self.assertEqual(tail("build", "-p", "a"), [cargo, "build", "--offline", "--locked", "-p", "a"])
        self.assertEqual(tail("check"), [cargo, "check", "--offline", "--locked"])
        self.assertEqual(tail("test", "-p", "a", "t"), [cargo, "nextest", "run", "--offline", "--locked", "-p", "a", "t"])
        self.assertEqual(tail("test", "--no-nextest", "-p", "a"), [cargo, "test", "--offline", "--locked", "-p", "a"])
        self.assertEqual(tail("clippy"), [cargo, "clippy", "--offline", "--locked", "--workspace", "--all-targets", "--", "-D", "warnings"])
        self.assertEqual(tail("fmt", "--check"), [cargo, "fmt", "--all", "--check"])
        run_example = [cargo, "run", "--offline", "--locked", "-p", "hostobs-proto", "--example", "sp4_wire_size"]
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size", "a"), run_example + ["--", "a"])
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size"), run_example)
        # a leading `--` of the example arguments is not doubled
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size", "--", "x", "y"), run_example + ["--", "x", "y"])
        self.assertEqual(tail("example", "hostobs-proto", "sp4_wire_size", "--"), run_example)
        self.assertEqual(tail("run", "--", "ls", "-l"), ["ls", "-l"])
        self.assertEqual(tail("shell"), ["bash"])
        self.assertEqual(tail("fixtures-shell"), ["bash"])
        self.assertEqual(self.run_dev("--dry-run", "run").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "nonsense").returncode, 2)
        self.assertEqual(self.run_dev("--dry-run", "example", "only-one").returncode, 2)

    def test_tty_flags_only_when_stdin_and_stdout_are_terminals(self) -> None:
        self.assertNotIn("-it", self.argv_of(self.run_dev("--dry-run", "shell")))
        master, slave = pty.openpty()
        try:
            only_stdin = self.run_dev("--dry-run", "shell", stdin=slave)
        finally:
            os.close(master)
            os.close(slave)
        self.assertNotIn("-it", self.argv_of(only_stdin))
        rc, out, err = self.run_pty("--dry-run", "shell")
        self.assertEqual(rc, 0, err)
        self.assertIn("-it", out.replace("\r", "").splitlines())

    def test_resource_limits_follow_the_environment_within_their_bounds(self) -> None:
        argv = self.argv_of(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MEMORY="2g", HOSTOBS_DEV_CPUS="1.5", HOSTOBS_DEV_PIDS="512"))
        self.assertEqual((argv[argv.index("--memory") + 1], argv[argv.index("--memory-swap") + 1], argv[argv.index("--cpus") + 1],
                          argv[argv.index("--pids-limit") + 1]), ("2g", "2g", "1.5", "512"))
        for value in ("512m", "4g", "4096m", "2048m"):
            self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MEMORY=value).returncode, 0, value)
        for value in ("3", "3.0"):
            self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_CPUS=value).returncode, 0, value)

    def test_limits_cannot_be_lifted_or_zeroed(self) -> None:
        for value in ("0", "0g", "0m", "100g", "5g", "511m", "-1", "3g --privileged", "3g\n--privileged", "64", "", "3G", "1.5g"):
            with self.subTest(memory=value):
                proc = self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MEMORY=value)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("HOSTOBS_DEV_MEMORY", proc.stderr)
        for value in ("0", "0.0", "0.000", "3.001", "4", "100", "-1", "x", "2 --privileged", "2\n3", ""):
            with self.subTest(cpus=value):
                proc = self.run_dev("--dry-run", "shell", HOSTOBS_DEV_CPUS=value)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("HOSTOBS_DEV_CPUS", proc.stderr)
        for value in ("0", "1025", "99999", "-1", "x", "10 --privileged"):
            with self.subTest(pids=value):
                self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_PIDS=value).returncode, 2)
        for value in ("0", "14401", "x", "-1", "5 6"):
            with self.subTest(max_s=value):
                self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MAX_S=value).returncode, 2)

    def test_the_governed_slice_must_be_exactly_dev_gates(self) -> None:
        for args in (("--dry-run", "shell"), ("--dry-run", "--local", "shell")):
            proc = self.run_dev(*args, unset=("CGROUP_PARENT_DEV_GATES",), HOSTOBS_DEV_ALLOW_LOCAL="1")
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("CGROUP_PARENT_DEV_GATES is required", proc.stderr)
        for value in ("-.slice", "x.slice", "-x.slice", "dev-gates.slice ", "dev-gates", "dev.slice", "dev-gates.slice\nx", "", "system.slice"):
            with self.subTest(slice=value):
                proc = self.run_dev("--dry-run", "shell", CGROUP_PARENT_DEV_GATES=value)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("CGROUP_PARENT_DEV_GATES", proc.stderr)

    def test_the_memory_gate_reads_the_dev_gates_slice(self) -> None:
        script = self.run_dev("--dry-run", "shell", unset=("HOSTOBS_DEV_TEST_SLICE_MEM_FILE",)).stdout
        self.assertIn("MEM_FILE=/sys/fs/cgroup/dev.slice/dev-gates.slice/memory.current", script)

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

    def test_an_untracked_nested_git_directory_is_refused_not_shipped(self) -> None:
        nested = self.repo / "hostobs" / "nested"
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True)
        (nested / "file.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(nested), "-c", "user.name=t", "-c", "user.email=t@e.invalid", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(nested), "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q", "-m", "n"], check=True)
        for args in (("--dry-run", "shell"), ("shell",)):
            proc = self.run_dev(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("nested", proc.stderr)
            self.assertIn("cannot be synced", proc.stderr)
        self.assertFalse((self.logs / "hx.log").exists())

    def test_the_container_has_a_runtime_limit_and_the_dry_run_shows_it(self) -> None:
        script = self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MAX_S="120").stdout
        self.assertIn("timeout --foreground -s TERM -k 15 120 ", script)
        self.assertIn("--stop-timeout 10", script)
        default = self.run_dev("--dry-run", "shell").stdout
        self.assertIn("timeout --foreground -s TERM -k 15 3600 ", default)
        self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MAX_S="14400").returncode, 0)


class HostileInputTests(DevCase):
    """Nothing the user types may reach the host shell: every word is quoted, whatever it contains."""

    def words(self, marker: Path) -> list:
        return [
            "a b", "q'uote", "x;y", "$(id)", "`id`", "$HOME/x\"; touch " + str(marker) + "; echo \"",
            "*", "a\nb", "-v", "--privileged", '"; touch ' + str(marker) + '; "', "$HOME/hostobs-dev/x:/work", "\\", "$IFS",
        ]

    def test_remote_run_passes_every_word_byte_for_byte_and_runs_nothing_on_the_host(self) -> None:
        marker = self.tmp / "PWNED"
        words = self.words(marker)
        proc = self.run_dev("run", "--", *words)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(marker.exists())
        runs = self.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][runs[0].index("-c3") + SKIP:], words)
        # the hostile words never reached the container's option positions
        self.assertEqual(runs[0].count("--privileged"), 1)
        self.assertEqual(runs[0].count("-v"), 4)

    def test_every_subcommand_quotes_its_arguments(self) -> None:
        marker = self.tmp / "PWNED2"
        evil = "$HOME/x\"; touch " + str(marker) + "; echo \""
        for sub in (["test", "-p", evil], ["build", evil], ["clippy", evil], ["gdb", "--args", evil], ["strace", evil],
                    ["example", "hostobs-proto", "sp4_wire_size", evil], ["fmt", evil]):
            with self.subTest(sub=sub[0]):
                self.assertEqual(self.run_dev(*sub).returncode, 0)
                self.assertFalse(marker.exists())
                self.assertEqual(self.runs()[-1][-1], evil)

    def test_the_tty_path_quotes_too(self) -> None:
        marker = self.tmp / "PWNED3"
        evil = "$HOME/x\"; touch " + str(marker) + "; echo \""
        rc, out, err = self.run_pty("run", "--", "echo", evil, "a b", "$(id)")
        self.assertEqual(rc, 0, err + out)
        self.assertFalse(marker.exists())
        run = self.runs()[-1]
        self.assertIn("-it", run)
        self.assertEqual(run[run.index("-c3") + SKIP:], ["echo", evil, "a b", "$(id)"])

    def test_the_only_literal_host_text_in_the_run_script_is_built_from_the_validated_hex(self) -> None:
        script = self.run_dev("--dry-run", "run", "--", "$HOME/x").stdout
        self.assertIn(f'D="$HOME/hostobs-dev/{self.hex}"; W="$D/work"; ', script)
        self.assertIn('-v "$W:/work" -v "$W/.git:/work/.git:ro"', script)
        # the user's $HOME word is escaped, never double-quoted
        self.assertIn(r"\$HOME/x", script)
        self.assertNotIn('"$HOME/x"', script)


class WrapperRemoteExecutionTests(DevCase):
    """The stub transport (real hx.sh layering) runs each remote script locally under the temporary HOME."""

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
        # stale state on the host is wiped by the next sync, and a planted hook is replaced by an empty directory
        (work / "stale.txt").write_text("left behind\n")
        (work / "keep.txt").write_text("host-side edit\n")
        hook = self.work() / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\ntouch " + str(self.tmp / "HOOKRAN") + "\n")
        hook.chmod(0o755)
        proc = self.run_dev("run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((work / "stale.txt").exists())
        self.assertEqual((work / "keep.txt").read_text(), "edited\n")
        self.assertFalse(hook.exists())
        self.assertEqual(list((self.work() / ".git" / "hooks").iterdir()), [])
        self.assertFalse((self.tmp / "HOOKRAN").exists())
        hosts = (self.logs / "hx.log").read_text().split()
        self.assertEqual(hosts, ["v1001"] * 6)
        self.assertEqual(len(self.runs()), 2)
        self.assertIn(f"{self.home}/hostobs-dev/{self.hex}/work:/work", self.runs()[0])
        self.assertIn(f"{self.home}/hostobs-dev/{self.hex}/work/.git:/work/.git:ro", self.runs()[0])

    def test_a_poisoned_git_config_hook_setting_does_not_run_on_the_host(self) -> None:
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        marker = self.tmp / "FSMONITORRAN"
        evil = self.tmp / "evil.sh"
        evil.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
        evil.chmod(0o755)
        for key, value in (("core.fsmonitor", str(evil)), ("core.hooksPath", str(self.tmp / "hooks-evil"))):
            subprocess.run(["git", "-C", str(self.work()), "config", key, value], check=True)
        (self.tmp / "hooks-evil").mkdir()
        (self.tmp / "hooks-evil" / "post-checkout").write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
        (self.tmp / "hooks-evil" / "post-checkout").chmod(0o755)
        (self.repo / "hostobs" / "keep.txt").write_text("edited again\n")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        self.assertFalse(marker.exists())

    def test_the_exit_code_is_propagated_and_the_container_is_removed_by_the_id_of_our_own_run(self) -> None:
        proc = self.run_dev("run", "--", "false", STUB_RUN_RC="7")
        self.assertEqual(proc.returncode, 7)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "-f", "cid-stub-1"]])
        self.assertFalse((self.home / "hostobs-dev" / self.hex / "cid").exists())

    def test_a_failed_run_that_created_no_container_removes_nothing(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_RUN_RC="125", STUB_NO_CID="1")
        self.assertEqual(proc.returncode, 125)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [])

    def test_the_sync_refuses_while_the_container_name_exists(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_EXISTS="1")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("dev stop", proc.stderr)
        self.assertEqual(self.runs(), [])
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [])
        self.assertFalse(self.work().exists())

    def test_two_wrappers_for_one_worktree_serialise_even_with_parallel_allowed(self) -> None:
        lock_dir = self.home / "hostobs-dev" / self.hex
        lock_dir.mkdir(parents=True)
        with (lock_dir / "lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.run_dev("run", "--", "true", HOSTOBS_DEV_ALLOW_PARALLEL="1")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("another dev call for this worktree", proc.stderr)
        self.assertEqual(self.runs(), [])
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [])
        self.assertFalse(self.work().exists())
        self.assertEqual(list(lock_dir.glob("up-*")), [])
        # the lock is released with the holder: the next call goes through
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)

    def test_every_docker_call_on_the_host_is_by_exact_name_or_own_id_or_the_own_prefix(self) -> None:
        self.run_dev("run", "--", "true")
        self.run_dev("status", STUB_RUNNING="false")
        self.run_dev("stop")
        self.run_dev("clean", STUB_RUNNING="absent")
        names = (f"hostobs-dev-{self.hex}", f"hostobs-dev-target-{self.hex}", "cid-stub-1")
        for call in self.docker_calls():
            self.assertFalse(any("ancestor" in a or a in ("-a", "--all", "prune") for a in call), call)
            if call[0] == "ps":
                self.assertEqual(call, ["ps", "--filter", "name=^hostobs-dev-", "--format", "{{.Names}}"])
            elif call[0] in ("inspect", "rm", "exec") or call[:2] in (["volume", "inspect"], ["volume", "rm"]):
                self.assertTrue(any(a in names for a in call), call)
            else:
                self.assertEqual(call[0], "run", call)

    def test_memory_gate_below_at_and_above_the_threshold(self) -> None:
        limit = 3584 * 1048576
        for value, expected in ((limit - 1, 0), (limit, 0), (limit + 1, 75), (8 << 30, 75)):
            with self.subTest(memory_current=value):
                (self.tmp / "memory.current").write_text(f"{value}\n")
                before = len(self.runs())
                proc = self.run_dev("run", "--", "true")
                self.assertEqual(proc.returncode, expected, proc.stderr)
                self.assertEqual(len(self.runs()) - before, 1 if expected == 0 else 0)
                if expected:
                    self.assertIn("dev-gates.slice memory.current", proc.stderr)
                    self.assertIn("page cache", proc.stderr)
        (self.tmp / "memory.current").write_text("garbage\n")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        (self.tmp / "memory.current").unlink()
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        # the limit can only be LOWERED through the environment
        (self.tmp / "memory.current").write_text(f"{3000 * 1048576}\n")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        self.assertEqual(self.run_dev("run", "--", "true", HOSTOBS_DEV_SLICE_MEM_MAX_MB="2000").returncode, 75)

    def test_the_memory_limit_variable_is_bounded(self) -> None:
        for value in ("", "x", "3585", "99999999999999999999", "9999999999", "-1", "0", "0000", "3584 --privileged", "3584\n1", "1.5"):
            with self.subTest(value=value):
                proc = self.run_dev("--dry-run", "shell", HOSTOBS_DEV_SLICE_MEM_MAX_MB=value)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("HOSTOBS_DEV_SLICE_MEM_MAX_MB", proc.stderr)
        for value in ("1", "3584", "0100"):
            self.assertEqual(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_SLICE_MEM_MAX_MB=value).returncode, 0, value)

    def test_the_container_command_has_an_in_container_deadline(self) -> None:
        argv = self.argv_of(self.run_dev("--dry-run", "shell", HOSTOBS_DEV_MAX_S="120"))
        self.assertEqual(argv[argv.index("-c3") + 1:argv.index("-c3") + SKIP], ["timeout", "--foreground", "-k", "5", "120"])
        self.assertEqual(argv[argv.index("-c3") + SKIP:], ["bash"])
        self.assertEqual(self.run_dev("run", "--", "true", HOSTOBS_DEV_MAX_S="77").returncode, 0)
        run = self.runs()[-1]
        self.assertEqual(run[run.index("-c3") + 1:run.index("-c3") + SKIP], ["timeout", "--foreground", "-k", "5", "77"])

    def test_the_global_lock_serialises_worktrees_and_parallel_skips_only_it(self) -> None:
        lock_dir = self.home / "hostobs-dev"
        lock_dir.mkdir(parents=True)
        with (lock_dir / ".all.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.run_dev("run", "--", "true")
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertIn(".all.lock", proc.stderr)
            self.assertEqual(self.runs(), [])
            self.assertFalse(self.work().exists())
            # parallel mode skips ONLY the global lock ...
            proc = self.run_dev("run", "--", "true", HOSTOBS_DEV_ALLOW_PARALLEL="1")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(self.runs()), 1)
            # ... and still passes the memory admission
            (self.tmp / "memory.current").write_text(f"{8 << 30}\n")
            proc = self.run_dev("run", "--", "true", HOSTOBS_DEV_ALLOW_PARALLEL="1")
            self.assertEqual(proc.returncode, 75)
            self.assertEqual(len(self.runs()), 1)

    def test_two_different_worktrees_cannot_both_run_a_container(self) -> None:
        other = self.make_repo(self.tmp / "repo2")
        self.assertNotEqual(worktree_hex(other), self.hex)
        hold = self.tmp / "release-first"
        first = subprocess.Popen([str(self.dev), "run", "--", "true"], env=self.env(STUB_RUN_HOLD=str(hold)),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(300):
                if Path(str(hold) + ".started").exists():
                    break
                time.sleep(0.1)
            self.assertTrue(Path(str(hold) + ".started").exists(), "the first container never started")
            second = subprocess.run([str(other / "hostobs" / "tools" / "dev"), "run", "--", "true"], env=self.env(),
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            self.assertEqual(second.returncode, 3, second.stderr)
            self.assertIn("another dev container call is in progress", second.stderr)
            self.assertEqual(len(self.runs()), 1)
            # with parallel allowed the second worktree gets in
            third = subprocess.run([str(other / "hostobs" / "tools" / "dev"), "run", "--", "true"],
                                   env=self.env(HOSTOBS_DEV_ALLOW_PARALLEL="1"), stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(third.returncode, 0, third.stderr)
        finally:
            hold.write_text("go")
            first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(len(self.runs()), 2)

    def test_the_lock_descriptors_are_inherited_by_the_docker_client(self) -> None:
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        fds = json.loads((self.logs / "fds.log").read_text().splitlines()[-1])
        self.assertEqual(fds["8"], str(self.home / "hostobs-dev" / ".all.lock"))
        self.assertEqual(fds["9"], str(self.home / "hostobs-dev" / self.hex / "lock"))
        self.assertEqual(self.run_dev("run", "--", "true", HOSTOBS_DEV_ALLOW_PARALLEL="1").returncode, 0)
        fds = json.loads((self.logs / "fds.log").read_text().splitlines()[-1])
        self.assertIsNone(fds["8"])
        self.assertEqual(fds["9"], str(self.home / "hostobs-dev" / self.hex / "lock"))

    def test_a_stale_cidfile_never_gets_a_foreign_container_removed(self) -> None:
        (self.home / "hostobs-dev" / self.hex).mkdir(parents=True)
        (self.home / "hostobs-dev" / self.hex / "cid").write_text("old-foreign-id")
        proc = self.run_dev("run", "--", "true", STUB_EXISTS="1")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [])
        self.assertFalse((self.home / "hostobs-dev" / self.hex / "cid").exists())
        # a normal run after a stale file removes only the id its own run wrote
        (self.home / "hostobs-dev" / self.hex / "cid").write_text("old-foreign-id")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "-f", "cid-stub-1"]])
        # and a run that creates no container removes nothing, whatever an earlier call left behind
        (self.home / "hostobs-dev" / self.hex / "cid").write_text("old-foreign-id")
        self.run_dev("run", "--", "true", STUB_RUN_RC="125", STUB_NO_CID="1")
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "-f", "cid-stub-1"]])

    def test_stop_and_clean_checkout_do_not_act_under_a_running_sync(self) -> None:
        d = self.home / "hostobs-dev" / self.hex
        (d / "work").mkdir(parents=True)
        with (d / "lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.run_dev("stop")
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertIn("in progress (syncing)", proc.stderr)
            self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [])
            proc = self.run_dev("clean", "--checkout", STUB_RUNNING="absent")
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertIn("in progress (syncing)", proc.stderr)
            self.assertTrue((d / "work").exists())
            self.assertEqual([c for c in self.docker_calls() if c[:2] == ["volume", "rm"]], [])
            # a container that exists can still be stopped while the call that started it holds the lock
            proc = self.run_dev("stop", STUB_EXISTS="1")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "-f", f"hostobs-dev-{self.hex}"]])
        # without the lock both act
        self.assertEqual(self.run_dev("clean", "--checkout", STUB_RUNNING="absent").returncode, 0)
        self.assertFalse(d.exists())

    def test_host_git_calls_close_the_lock_descriptors_and_never_auto_gc(self) -> None:
        (self.repo / "hostobs" / "new.txt").write_text("untracked\n")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        records = [json.loads(line) for line in (self.logs / "git.log").read_text().splitlines()]
        sync = [r for r in records if "core.hooksPath=/dev/null" in r["args"] and any(v in r["args"] for v in ("checkout", "reset", "clean", "apply", "init", "fetch"))]
        self.assertGreaterEqual(len(sync), 4)
        for record in sync:
            for fd in ("7", "8", "9"):
                self.assertNotIn(fd, record["fds"], record)
            self.assertIn("gc.auto=0", record["args"])
            self.assertIn("maintenance.auto=false", record["args"])
        script = (self.logs / "hx-3.sh").read_text()
        self.assertIn('-C "$W" 7>&- 8>&- 9>&-', script.replace('"$UP/untracked.tar" -C "$W" 7>&-', '-C "$W" 7>&-'))
        self.assertIn('docker rm -f "$(cat "$D/cid")" >/dev/null 2>&1 7>&- 8>&- 9>&-', script)
        self.assertIn('7>&- 8>&- 9>&-; }', script)

    def test_a_non_hex_have_line_from_the_host_is_ignored_by_the_bundle_logic(self) -> None:
        # `HAVE=HEAD` resolves locally; used as a bundle base it would produce no bundle and the host sync would fail
        proc = self.run_dev("run", "--", "true", STUB_INJECT="HAVE=HEAD\\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.work() / ".git").exists())
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(subprocess.run(["git", "-C", str(self.work()), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), head)

    def test_hostile_lines_from_the_host_never_reach_the_next_remote_script(self) -> None:
        marker = self.tmp / "SEEDPWNED"
        good = "a" * 40
        inject = (f"SEED={good};touch {marker}\\nSEED=$(touch {marker})\\nHAVE=$(touch {marker})\\n"
                  f"SEED={'b' * 39}\\nSEED=../../x\\nSEED=ABCDEF{'0' * 34}\\nHAVE=zz\\n")
        proc = self.run_dev("run", "--", "true", STUB_INJECT=inject)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(marker.exists())
        for n in (2, 3):
            script = (self.logs / f"hx-{n}.sh").read_text()
            self.assertNotIn("touch", script)
            self.assertIn("\nSEED=\n", script)

    def test_psi_gate_uses_memory_some_and_io_full_with_the_remote_gate_limit(self) -> None:
        self.write_psi("25.00", "25.00", io_some="99.00")      # at the limit; memory full and io some are not signals
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 0)
        self.write_psi("25.01", "0.00")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        self.write_psi("0.00", "25.01")
        self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)

    def test_psi_gate_fails_closed_when_a_file_is_missing_or_unparsable(self) -> None:
        self.write_psi("0.00", "0.00")
        before = len(self.runs())
        for victim in ("memory", "io"):
            with self.subTest(missing=victim):
                saved = (self.tmp / "psi" / victim).read_text()
                (self.tmp / "psi" / victim).unlink()
                proc = self.run_dev("run", "--", "true")
                self.assertEqual(proc.returncode, 75, proc.stderr)
                self.assertIn("cannot read the PSI files", proc.stderr)
                (self.tmp / "psi" / victim).write_text(saved)
        for garbage in ("", "garbage\n", "some avg10=abc total=0\nfull avg10=abc total=0\n", "some avg10= total=0\nfull avg10= total=0\n"):
            with self.subTest(garbage=garbage):
                (self.tmp / "psi" / "memory").write_text(garbage)
                (self.tmp / "psi" / "io").write_text(garbage)
                self.assertEqual(self.run_dev("run", "--", "true").returncode, 75)
        self.assertEqual(len(self.runs()), before)

    def test_a_running_dev_container_refuses_with_3_unless_parallel_is_allowed(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_PS="hostobs-dev-abcdefabcdef\n")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("already running", proc.stderr)
        self.assertEqual(self.runs(), [])
        proc = self.run_dev("run", "--", "true", STUB_PS="hostobs-dev-abcdefabcdef\n", HOSTOBS_DEV_ALLOW_PARALLEL="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_dev("--dry-run", "--local", "shell", HOSTOBS_DEV_ALLOW_LOCAL="1", STUB_PS="hostobs-dev-x\n",
                            STUB_SELF="s", HOSTOBS_DEV_SELF_CONTAINER="s", STUB_MOUNTS=f"{self.tmp}\t/h\n")
        self.assertEqual(proc.returncode, 3)

    def test_wrong_uid_is_refused_where_the_files_live(self) -> None:
        proc = self.run_dev("run", "--", "true", STUB_UID="1000", STUB_GID="1000")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("1003:1003", proc.stderr)
        self.assertEqual(self.runs(), [])
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
        self.assertEqual(self.run_dev("run", "--", "true", CONTROLLER_REMOTE_SLOT_WAIT_S="0").returncode, 0)

    def test_status_shows_a_running_container_with_its_age(self) -> None:
        proc = self.run_dev("status", STUB_RUNNING="true", STUB_STARTED="2020-01-01T00:00:00Z")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"hostobs-dev-{self.hex}", proc.stdout)
        self.assertIn(f"hostobs-dev-target-{self.hex}", proc.stdout)
        self.assertIn("running:   true", proc.stdout)
        match = re.search(r"age:\s+(\d+) s", proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        self.assertGreater(int(match.group(1)), 60 * 60 * 24 * 365)
        self.assertIn("dev stop", proc.stdout)
        proc = self.run_dev("status", STUB_RUNNING="false")
        self.assertNotIn("age:", proc.stdout)

    def test_stop_removes_this_worktrees_container_by_exact_name(self) -> None:
        dry = self.run_dev("--dry-run", "stop")
        self.assertEqual(dry.returncode, 0)
        self.assertIn(f'docker rm -f "$NAME"', dry.stdout)
        self.assertEqual(self.docker_calls(), [])
        proc = self.run_dev("stop", STUB_EXISTS="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"stopped and removed container hostobs-dev-{self.hex}", proc.stdout)
        self.assertEqual([c for c in self.docker_calls() if c[0] == "rm"], [["rm", "-f", f"hostobs-dev-{self.hex}"]])
        proc = self.run_dev("stop")
        self.assertIn("nothing to stop", proc.stdout)
        self.assertEqual(self.run_dev("stop", "now").returncode, 2)

    def test_clean_names_the_exact_volume_and_refuses_while_running(self) -> None:
        proc = self.run_dev("clean", STUB_RUNNING="true")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual([c for c in self.docker_calls() if c[:2] == ["volume", "rm"]], [])
        proc = self.run_dev("clean", "--checkout", STUB_RUNNING="false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"volume: hostobs-dev-target-{self.hex}", proc.stdout)
        self.assertEqual([c for c in self.docker_calls() if c[:2] == ["volume", "rm"]], [["volume", "rm", f"hostobs-dev-target-{self.hex}"]])
        self.assertEqual(self.run_dev("clean", "--everything").returncode, 2)

    def test_the_run_script_enforces_the_runtime_limit_and_reports_it(self) -> None:
        self.assertEqual(self.run_dev("run", "--", "true", HOSTOBS_DEV_MAX_S="77").returncode, 0)
        script = (self.logs / "hx-3.sh").read_text()
        self.assertIn("timeout --foreground -s TERM -k 15 77 docker run", script)
        self.assertIn("--stop-timeout 10", script)
        self.assertIn("exit 124: the in-container or the host runtime limit, or the command itself returned 124", script)


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
        # no user word is ever inspected to decide how to quote it
        self.assertNotIn("'$HOME/'*)", text)

    def test_dockerfile_dev_installs_exactly_the_listed_packages_and_ends_as_the_gate_user(self) -> None:
        text = DOCKERFILE.read_text()
        self.assertRegex(text, r"(?m)^ARG BASE_IMAGE=tester-unified-rust:local$")
        self.assertIn("FROM ${BASE_IMAGE}", text)
        self.assertIn('org.vbpub.tester-unified-rust-dev.base-image="${BASE_IMAGE_TAG}"', text)
        self.assertRegex(text, r"(?m)^ARG BASE_IMAGE_TAG=")
        self.assertIn('org.vbpub.tester-unified-rust-dev.base-image-id="${BASE_IMAGE_ID}"', text)
        install = re.search(r"apt-get install -y --no-install-recommends \\\n\s+(.+?) \\\n", text)
        self.assertIsNotNone(install)
        self.assertEqual(set(install.group(1).split()), {"gdb", "strace", "procps", "less", "ca-certificates"})
        self.assertEqual(text.count("apt-get install"), 1)
        self.assertEqual(re.findall(r"(?m)^USER (.+)$", text), ["root", "1003:1003"])
        directives = [line.split()[0] for line in text.splitlines() if re.match(r"^[A-Z]+ ", line)]
        self.assertLess(text.index("USER 1003:1003"), text.index("WORKDIR /work"))
        self.assertEqual(directives[-1], "CMD")

    def test_dockerfile_dev_copies_nothing_and_leaves_the_cache_and_identity_alone(self) -> None:
        text = "\n".join(line for line in DOCKERFILE.read_text().splitlines() if not line.lstrip().startswith("#"))
        for directive in ("COPY", "ADD"):
            self.assertIsNone(re.search(rf"(?m)^{directive}\b", text), directive)
        for fragment in ("chown", "cargo fetch", "cargo install", "/usr/local/cargo", "registry", "rm -rf /usr/local",
                         "Cargo.lock.registry-sha256", "rust-crate-cache", "rustup update", "rustup toolchain", "rustup install",
                         "rustup default", "rustup self", "curl", "wget"):
            self.assertNotIn(fragment, text, fragment)
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

    def test_selftest_lane_syntax_checks_the_new_scripts(self) -> None:
        text = (PROJECT_ROOT / "run-gate.toml").read_text()
        self.assertIn("build-dev-image.sh", text)
        self.assertIn('"$HOSTOBS_ROOT/tools/dev"', text)


BUILD_DOCKER_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["STUB_LOG_DIR"], "docker.log"), "a") as fh:
    fh.write(json.dumps(args) + "\\n")
BASE_ID = "sha256:" + "a" * 64
if args[:2] == ["image", "inspect"]:
    fmt, image = args[3], args[4]
    if image == os.environ.get("STUB_MISSING_IMAGE"):
        sys.exit(1)
    if fmt == "{{{{json .RootFS.Layers}}}}":
        if image.startswith("sha256:"):
            print('["sha256:l1","sha256:l2"]')
        else:
            print(os.environ.get("STUB_DEV_LAYERS", '["sha256:l1","sha256:l2","sha256:l3"]'))
    elif fmt == "{{{{.Id}}}}":
        print(os.environ.get("STUB_BASE_ID", BASE_ID))
    elif fmt.endswith('base-image-id"}}}}'):
        print(os.environ.get("STUB_LABEL_ID", BASE_ID))
    elif fmt.endswith('base-image"}}}}'):
        print(os.environ.get("STUB_LABEL_BASE", "tester-unified-rust:local"))
    sys.exit(0)
if args[:2] == ["buildx", "inspect"]:
    sys.exit(1 if args[2] == os.environ.get("STUB_UNKNOWN_BUILDER") else 0)
if args[:2] == ["buildx", "build"]:
    sys.exit(0)
if args[0] == "run":
    i = max(i for i, a in enumerate(args) if a in ("dpkg-query", "rustc", "rust-analyzer", "rustup"))
    image = args[i - 1]
    tool = args[i]
    base = [("bash", "5.2-1"), ("libc6", "2.36-9"), ("ca-certificates", "20230311")]
    dev = base[:2] + [("ca-certificates", "20240203"), ("gdb", "13.1-3"), ("libpython3.11", "3.11.2-6"), ("libdebuginfod1", "0.188-2")]
    if tool == "dpkg-query":
        for name, version in (base if image.startswith("sha256:") else dev):
            print(f"{{name}}\\t{{version}}")
    elif tool == "rustc":
        print("rustc 1.99.0 (abcdef 2026-01-01)")
    elif tool == "rust-analyzer":
        print("rust-analyzer 1.99.0")
    elif tool == "rustup":
        print("rust-src-x86_64-unknown-linux-gnu")
        print("rust-analyzer-x86_64-unknown-linux-gnu")
    sys.exit(0)
sys.exit(0)
"""


class BuildDevImageTests(unittest.TestCase):
    SLICE = "dev-gates.slice"
    SLICE_PATH = "dev.slice/dev-gates.slice"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="builddev-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        (self.tmp / "logs").mkdir()
        (self.tmp / "bin" / "docker").write_text(BUILD_DOCKER_STUB)
        (self.tmp / "bin" / "docker").chmod(0o755)

    def run_build(self, *args: str, **extra: str):
        (self.tmp / "logs" / "docker.log").unlink(missing_ok=True)
        env = {"PATH": f"{self.tmp / 'bin'}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp), "STUB_LOG_DIR": str(self.tmp / "logs"),
               "TMPDIR": str(self.tmp)}
        env.update(extra)
        proc = subprocess.run([str(BUILD_DEV), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        log = self.tmp / "logs" / "docker.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return proc, calls

    def test_refuses_without_a_cgroup_parent_and_documents_options(self) -> None:
        proc, calls = self.run_build()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is required (the estate rule for every image build); refusing to build without it", proc.stderr)
        self.assertNotIn("ungoverned image build", proc.stderr)
        self.assertEqual(calls, [])
        proc, _ = self.run_build("--help")
        self.assertEqual(proc.returncode, 2)
        for option in ("--tag", "--base", "--cgroup-parent", "--record", "--builder", "TESTER_UNIFIED_RUST_BUILDER"):
            self.assertIn(option, proc.stderr)

    def test_builder_precedence_is_option_then_environment_then_default(self) -> None:
        def builders(calls):
            return ([c[c.index("inspect") + 1] for c in calls if c[:2] == ["buildx", "inspect"]],
                    [c[c.index("--builder") + 1] for c in calls if c[:2] == ["buildx", "build"]])
        proc, calls = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(builders(calls), (["default"], ["default"]))
        self.assertIn("builder=default\n", proc.stdout)
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, TESTER_UNIFIED_RUST_BUILDER="envb")
        self.assertEqual(builders(calls), (["envb"], ["envb"]))
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", "optb", TESTER_UNIFIED_RUST_BUILDER="envb")
        self.assertEqual(builders(calls), (["optb"], ["optb"]))
        for bad in ("-x", "a b", "", "a;b", "é"):
            proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", bad)
            self.assertEqual(proc.returncode, 2, bad)
            self.assertEqual(calls, [], bad)
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, TESTER_UNIFIED_RUST_BUILDER="-bad")
        self.assertEqual(proc.returncode, 2)

    def test_missing_base_is_refused_before_any_build(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--base", "nope:1", STUB_MISSING_IMAGE="nope:1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not in the local daemon", proc.stderr)
        self.assertEqual([c for c in calls if c[0] == "buildx"], [])

    def test_the_build_records_the_base_tag_and_id_and_verifies_the_labels(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--tag", "x:y", "--base", "tester-unified-rust:local")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        args = [build[i + 1] for i, a in enumerate(build) if a == "--build-arg"]
        # FROM is the immutable id (no tag race); the tag travels separately, for the label
        self.assertEqual(args, ["BASE_IMAGE=tester-unified-rust-base:" + "a" * 64, "BASE_IMAGE_TAG=tester-unified-rust:local",
                                "BASE_IMAGE_ID=sha256:" + "a" * 64])
        # the content-named tag is created from the exact id, before the build, and never removed
        names = [c[:2] for c in calls]
        self.assertEqual([c for c in calls if c[0] == "tag"], [["tag", "sha256:" + "a" * 64, "tester-unified-rust-base:" + "a" * 64]])
        self.assertLess(names.index(["tag", "sha256:" + "a" * 64]), names.index(["buildx", "build"]))
        self.assertEqual([c for c in calls if c[0] in ("rmi", "image") and "rm" in c[:2]], [])
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)
        self.assertIn("-t", build)
        self.assertIn("base_id=sha256:" + "a" * 64, proc.stdout)
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_LABEL_ID="sha256:" + "b" * 64)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not record the requested base", proc.stderr)
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_LABEL_BASE="other:tag")
        self.assertEqual(proc.returncode, 2)

    def test_the_dev_image_must_sit_on_the_layers_of_the_base_image(self) -> None:
        proc, _ = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for layers in ('["sha256:l1","sha256:lX","sha256:l3"]', '["sha256:l1","sha256:l2"]', '["sha256:l2","sha256:l1","sha256:l3"]', "null", ""):
            with self.subTest(layers=layers):
                proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_DEV_LAYERS=layers)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("not built on the layers of base image", proc.stderr)

    def test_the_build_states_the_measured_truth_about_the_governed_slice(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = [line for line in proc.stderr.splitlines() if line.startswith("NOTE:")]
        self.assertEqual(len(note), 1, proc.stderr)
        self.assertIn("cgroup PATH dev.slice/dev-gates.slice (derived from the slice name dev-gates.slice)", note[0])
        self.assertIn("measured on v1001 (2026-11-18) with the docker-driver builder `default`, the BuildKit RUN steps run inside the governed slice", note[0])
        self.assertIn("plain slice name would put them in an ungoverned root-level cgroup", note[0])
        self.assertIn("Probes use the slice name", note[0])
        self.assertNotIn("was not measured", note[0])
        for stale in ("does NOT place", "watch its memory", "/sys/fs/cgroup/dev-gates.slice/buildkit"):
            self.assertNotIn(stale, proc.stderr)
        # buildx gets the PATH form, exactly once
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        self.assertEqual(build.count("--cgroup-parent"), 1)
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)

    def test_the_note_claims_governed_placement_only_for_the_measured_builder(self) -> None:
        for builder in ("mdt-managed", "other"):
            with self.subTest(builder=builder):
                proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", builder)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                note = [line for line in proc.stderr.splitlines() if line.startswith("NOTE:")]
                self.assertEqual(len(note), 1, proc.stderr)
                self.assertIn(f"placement of builder {builder} was not measured", note[0])
                self.assertIn("only the docker-driver builder `default` on v1001, 2026-11-18", note[0])
                self.assertIn("check /proc/<pid>/cgroup of a RUN-step process", note[0])
                self.assertIn("cgroup PATH dev.slice/dev-gates.slice", note[0])
                self.assertNotIn("run inside the governed slice", note[0])
                build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
                self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)

    def test_buildx_gets_the_path_and_probes_the_name_for_every_slice_shape(self) -> None:
        for name, path in (
            ("dev-gates.slice", "dev.slice/dev-gates.slice"),
            ("a-b-c.slice", "a.slice/a-b.slice/a-b-c.slice"),
            ("x.slice", "x.slice"),
        ):
            with self.subTest(name=name):
                proc, calls = self.run_build("--cgroup-parent", name)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                builds = [c for c in calls if c[:2] == ["buildx", "build"]]
                self.assertEqual(len(builds), 1)
                self.assertEqual(builds[0][builds[0].index("--cgroup-parent") + 1], path)
                probes = [c for c in calls if c[0] == "run"]
                self.assertGreaterEqual(len(probes), 4)
                for probe in probes:
                    self.assertEqual(probe.count("--cgroup-parent"), 1)
                    self.assertEqual(probe[probe.index("--cgroup-parent") + 1], name)
                # no other docker call carries a cgroup parent at all
                for call in calls:
                    if call[:2] != ["buildx", "build"] and call[0] != "run":
                        self.assertNotIn("--cgroup-parent", call)

    def test_the_slice_input_also_comes_from_the_environment(self) -> None:
        proc, calls = self.run_build(CGROUP_PARENT_DEV_GATES=self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)
        probe = [c for c in calls if c[0] == "run"][0]
        self.assertEqual(probe[probe.index("--cgroup-parent") + 1], self.SLICE)

    def test_an_invalid_slice_name_is_refused_with_exit_2_before_any_docker_call(self) -> None:
        for bad in ("-.slice", "a--b.slice", "a-.slice", "dev.slice/dev-gates.slice", "/dev.slice/dev-gates.slice",
                    "../a.slice", "a b.slice", "a.slice\n", "dev-gates", "é.slice", ""):
            with self.subTest(bad=bad):
                proc, calls = self.run_build("--cgroup-parent", bad)
                self.assertEqual(proc.returncode, 2, repr(bad))
                self.assertEqual(calls, [], repr(bad))
                self.assertTrue("invalid --cgroup-parent" in proc.stderr or "is required" in proc.stderr, proc.stderr)
        proc, calls = self.run_build(CGROUP_PARENT_DEV_GATES="a/b.slice")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(calls, [])
        self.assertIn("invalid --cgroup-parent / CGROUP_PARENT_DEV_GATES", proc.stderr)
        self.assertIn("systemd slice name", proc.stderr)

    def test_the_old_claims_are_gone_from_the_scripts_and_the_docs_state_both_spellings(self) -> None:
        # Negative greps cover the two scripts only (their NOTE and comments); prose docs may quote history.
        stale = (
            "does NOT place", "does not place", "NOT in a governed", "not in the governed slice",
            "untested path form", "watch its memory", "not because it governs this build",
            "passed because the estate requires it", "(unlike the build above)",
        )
        for relative in ("build-dev-image.sh", "build-image.sh"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            for phrase in stale:
                self.assertNotIn(phrase, text, f"{relative}: {phrase}")
        for relative in ("docs/DEV.md", "docs/CONSUMERS.md"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("dev.slice/dev-gates.slice", text, relative)
            self.assertIn("/dev-gates.slice/buildkit", text, relative)
        # the governed claim is scoped to the measured builder everywhere it is made
        for relative in ("docs/DEV.md", "docs/CONSUMERS.md", "README.md"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("docker-driver builder `default`", text, relative)
            self.assertIn("/proc/<pid>/cgroup", text, relative)
        self.assertIn("1,533,550,592", (PROJECT_ROOT / "docs/DEV.md").read_text(encoding="utf-8"))
        self.assertIn("docker-driver builder `default`", (PROJECT_ROOT / "CHANGES.md").read_text(encoding="utf-8"))

    def test_probe_containers_are_governed_and_networkless(self) -> None:
        _, calls = self.run_build("--cgroup-parent", self.SLICE)
        probes = [c for c in calls if c[0] == "run"]
        self.assertGreaterEqual(len(probes), 4)
        for probe in probes:
            self.assertEqual(probe[probe.index("--network") + 1], "none")
            self.assertEqual(probe[probe.index("--cap-drop") + 1], "ALL")
            # `docker run` takes the slice NAME: the systemd cgroup driver refuses the path form
            self.assertEqual(probe[probe.index("--cgroup-parent") + 1], self.SLICE)
            self.assertIn("--pull", probe)

    def test_record_lists_the_whole_package_diff_and_the_toolchain(self) -> None:
        record = self.tmp / "record.tsv"
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, "--record", str(record))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = record.read_text().splitlines()
        header = [line for line in lines if line.startswith("#")]
        self.assertTrue(any("BUILD date" in line and "release date" in line for line in header), header)
        self.assertTrue(any("base=tester-unified-rust:local" in line and "base_id=sha256:" + "a" * 64 in line for line in header), header)
        rows = {line.split("\t")[0]: line.split("\t") for line in lines if not line.startswith("#")}
        # added AND upgraded packages (the dependency closure), never the unchanged ones
        for name, version in (("gdb", "13.1-3"), ("libpython3.11", "3.11.2-6"), ("libdebuginfod1", "0.188-2"), ("ca-certificates", "20240203")):
            self.assertEqual(rows[name][1], version, name)
            self.assertRegex(rows[name][2], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertNotIn("bash", rows)
        self.assertNotIn("libc6", rows)
        self.assertEqual(rows["rustc"][1], "rustc 1.99.0 (abcdef 2026-01-01)")
        self.assertEqual(rows["rust-analyzer"][1], "rust-analyzer 1.99.0")
        components = [line for line in lines if line.startswith("component\t")]
        self.assertEqual(len(components), 2)
        self.assertIn("packages added or upgraded", proc.stdout)


if __name__ == "__main__":
    unittest.main()
