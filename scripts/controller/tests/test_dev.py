"""Tests of the generic remote dev tool scripts/controller/dev (contract: docs/design/agent-dev/DESIGN.md).

No real host, docker daemon, ssh or network. The tool runs in a temporary git repository under a temporary HOME with fakes on
PATH: `hx` (the host transport, AGENT_DEV_HX; it copies the real hx.sh layering and runs the REMOTE script locally), `docker`
(images by tag and id per role, an argv log, flock checks of the start ticket and the worktree lock, `rm` and `run` records),
`id` (uid 1003) and `sleep` (the 20 s ticket settle waits for a token file, so no test depends on wall time). Time is the fake
clock file (AGENT_DEV_TEST_CLOCK) and pressure is a fake PSI directory (AGENT_DEV_TEST_PSI_DIR). DEV_SCRIPT points the suite at a
mutated copy of the tool (tests/test-mutations.sh). Signals are sent only to a process whose command line carries this run's
worktree id, and only inside the gate container.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROLLER = REPO_ROOT / "scripts" / "controller"
DEV = Path(os.environ.get("DEV_SCRIPT") or CONTROLLER / "dev")
DEV_PROFILE = Path(os.environ.get("DEV_PROFILE_SCRIPT") or CONTROLLER / "dev_profile.py")   # a mutated copy, tests/test-mutations.sh
PROFILES = CONTROLLER / "dev-profiles.toml"
ROOT_TOML = REPO_ROOT / "run-gate.root.toml"

DOCKER_STUB = r'''#!@PY@
import fcntl, json, os, sys, time
args = sys.argv[1:]
log = os.environ["STUB_LOG_DIR"]
role = os.environ.get("STUB_ROLE", "cockpit")
with open(os.path.join(log, "docker.log"), "a") as fh:
    fh.write(json.dumps({"role": role, "argv": args}) + "\n")

def lines(name):
    try:
        with open(os.path.join(log, name)) as fh:
            return [l.rstrip("\n") for l in fh if l.strip()]
    except OSError:
        return []

def held(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)

verb = args[0] if args else ""
if args[:2] == ["image", "inspect"]:
    try:
        with open(os.path.join(log, "images-%s.json" % role)) as fh:
            entry = json.load(fh).get(args[4])
    except OSError:
        entry = None
    if entry is None:
        sys.exit(1)
    print(entry["label"] if "Labels" in args[3] else entry["id"])
    sys.exit(0)
if verb == "inspect":
    sys.exit(0 if args[-1] in lines("containers.txt") else 1)
if verb == "ps":
    prefix = args[args.index("--filter") + 1].split("^", 1)[1]
    for name in lines("containers.txt"):
        if name.startswith(prefix):
            print(name)
    sys.exit(0)
if verb == "volume":
    if args[1] == "ls":
        for v in lines("volumes.txt"):
            print(v)
    elif args[1] == "rm":
        kept = [v for v in lines("volumes.txt") if v != args[2]]  # read BEFORE open(..., "w") truncates the file
        with open(os.path.join(log, "volumes.txt"), "w") as fh:
            fh.write("".join(v + "\n" for v in kept))
    elif args[1] == "inspect":
        sys.exit(0 if args[-1] in lines("volumes.txt") else 1)
    sys.exit(0)
if verb == "run":
    name = args[args.index("--name") + 1]
    home = os.environ["HOME"]
    def parent(pid):
        with open("/proc/%d/stat" % pid) as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[1])
    rec = {"argv": args,
           "ticket_held": held(os.path.join(home, "host-admission", "ticket")),
           "lock_held": held(os.path.join(home, "agent-dev", name.rsplit("-", 1)[1], "lock")),
           "remote_pid": parent(parent(os.getpid()))}
    with open(os.path.join(log, "run.log"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    if "--cidfile" in args and not os.environ.get("STUB_NO_CID"):
        with open(args[args.index("--cidfile") + 1], "w") as fh:
            fh.write("cid-" + name)
    if not os.environ.get("STUB_SETTLE_KEEP"):
        open(os.path.join(log, "settle.release"), "w").close()
    hold = os.environ.get("STUB_RUN_HOLD")
    if hold:
        open(hold + ".started", "w").close()
        for _ in range(1200):
            if os.path.exists(hold):
                break
            time.sleep(0.1)
    sys.exit(int(os.environ.get("STUB_RUN_RC", "0")))
sys.exit(0)
'''

# the real layering of scripts/controller/hx.sh: a guard under `set -e`, then the command, shlex-quoted into `bash -c`
HX_STUB = r'''#!/bin/bash
d=$STUB_LOG_DIR
n=$(( $(cat "$d/hx.count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$d/hx.count"
printf '%s\n' "$1" >> "$d/hx.hosts"
printf '%s' "$2" > "$d/hx-$n.sh"
if [[ -n ${STUB_HX_RC:-} ]]; then exit "$STUB_HX_RC"; fi
remote_script=$(printf 'set -e\nif ! { %s\n}; then printf "GUARD-FAIL\\n" >&2; exit 98; fi\n%s\n' true "$2")
quoted=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$remote_script") || exit 2
export STUB_ROLE=host
exec sh -c "bash -c $quoted"
'''

ID_STUB = '''#!/bin/bash
case "$1" in
  -u) echo "${STUB_UID:-1003}" ;;
  -g) echo "${STUB_GID:-1003}" ;;
  *) exec /usr/bin/id "$@" ;;
esac
'''

# passthrough git and tar that fail a named host-side step on request (STUB_GIT_FAIL=apply|fetch, STUB_TAR_FAIL=1)
GIT_STUB = '''#!/bin/bash
if [[ -n ${STUB_GIT_FAIL:-} && " $* " == *" $STUB_GIT_FAIL "* ]]; then echo "git stub: $STUB_GIT_FAIL fails" >&2; exit 128; fi
exec @GIT@ "$@"
'''
TAR_STUB = '''#!/bin/bash
if [[ -n ${STUB_TAR_FAIL:-} && " $* " == *" -xf "*untracked.tar* ]]; then echo "tar stub: unpack fails" >&2; exit 2; fi
exec @TAR@ "$@"
'''

# util-linux `script -qefc CMD FILE` without a pty: runs CMD with $SHELL -c, as a NEW process that sees only exported variables
SCRIPT_STUB = '''#!/bin/bash
[[ ${1:-} == -qefc ]] || { echo "script stub: unexpected arguments: $*" >&2; exit 2; }
exec "${SHELL:-/bin/sh}" -c "$2"
'''

# `sleep 20` is the ticket settle: it ends when a token file appears (the fake docker creates it once it has looked at the ticket)
SLEEP_STUB = '''#!/bin/bash
if [[ ${1:-} == 20 && -n ${STUB_LOG_DIR:-} ]]; then
  for _ in $(seq 1 2400); do
    if [[ -e $STUB_LOG_DIR/settle.release ]]; then rm -f "$STUB_LOG_DIR/settle.release"; exit 0; fi
    /bin/sleep 0.05
  done
  exit 0
fi
exec /bin/sleep "$@"
'''

PROFILE_DATA = tomllib.loads(PROFILES.read_text())
ENV_IMAGES = {k: v["image"] for k, v in tomllib.loads(ROOT_TOML.read_text())["environments"].items() if "image" in v}
PY_IMAGE = ENV_IMAGES["tester-unified"]
RS_IMAGE = ENV_IMAGES["tester-unified-rust"]
RS_DEV_IMAGE = PROFILE_DATA["profiles"]["hostobs"]["dev_image"]
FORBIDDEN = ("--privileged", "--pid", "--pid=host", "--network=host", "--net", "--cgroupns", "--ipc", "--uts", "--userns", "--device", "--volumes-from")


def image_id(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


def worktree_hex(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]


def option(argv: list, name: str) -> str:
    return argv[argv.index(name) + 1]


class DevCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devtest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stubs, self.logs, self.home, self.psi = (self.tmp / n for n in ("stubs", "logs", "home", "psi"))
        for d in (self.stubs, self.logs, self.home, self.psi):
            d.mkdir()
        for name, text in (("docker", DOCKER_STUB.replace("@PY@", sys.executable)), ("hx", HX_STUB), ("id", ID_STUB), ("sleep", SLEEP_STUB), ("script", SCRIPT_STUB),
                           ("git", GIT_STUB.replace("@GIT@", shutil.which("git") or "git")), ("tar", TAR_STUB.replace("@TAR@", shutil.which("tar") or "tar"))):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o755)
        self.clock = self.tmp / "clock"
        self.set_clock(1000)
        self.write_psi()
        for role in ("host", "cockpit"):
            self.set_images(role)
        # safety net: a settle that is never released must not outlive the test
        self.addCleanup(lambda: (self.logs / "settle.release").write_text(""))
        self.repo = self.make_repo(self.tmp / "repo")
        self.hex = worktree_hex(self.repo)
        self.dev = self.repo / "scripts" / "controller" / "dev"

    # ---- fixtures
    def make_repo(self, root: Path) -> Path:
        for rel in ("scripts/controller/dev_profile.py", "scripts/controller/dev-profiles.toml", "run-gate.root.toml",
                    "cmru/run-gate.toml", "cmru/assay.toml", "hostobs/run-gate.toml", "hostobs/tools/dev", "scripts/controller/run-gate.toml",
                    "libraries/cli-extended/run-gate.toml"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, root / rel)
        shutil.copy2(DEV, root / "scripts" / "controller" / "dev")
        shutil.copy2(DEV_PROFILE, root / "scripts" / "controller" / "dev_profile.py")
        for proj in ("cmru", "hostobs"):
            (root / proj / "keep.txt").write_text("committed\n")
            (root / proj / "gone.txt").write_text("to be deleted\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(root)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
        return root

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    def set_images(self, role: str, **overrides: dict) -> None:
        table = {t: {"id": image_id(t), "label": image_id(RS_IMAGE) if t == RS_DEV_IMAGE else ""} for t in (PY_IMAGE, RS_IMAGE, RS_DEV_IMAGE)}
        for tag, entry in overrides.items():
            table[tag.replace("__", ":")] = entry
        (self.logs / f"images-{role}.json").write_text(json.dumps(table))

    def write_psi(self, mem_some="0.00", mem_full="0.00", io_some="0.00", io_full="0.00", cpu_some="0.00", cpu_full="0.00") -> None:
        for name, some, full in (("memory", mem_some, mem_full), ("io", io_some, io_full), ("cpu", cpu_some, cpu_full)):
            (self.psi / name).write_text(f"some avg10={some} avg60=0.00 avg300=0.00 total=0\nfull avg10={full} avg60=0.00 avg300=0.00 total=0\n")

    def set_clock(self, value: int) -> None:
        self.clock.write_text(f"{value}\n")

    def env(self, **extra: str) -> dict:
        env = {
            "PATH": f"{self.stubs}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home), "LANG": "C", "TMPDIR": str(self.tmp),
            "STUB_LOG_DIR": str(self.logs), "CONTROLLER_REMOTE_GATE_HOSTS": "v1001",
            "AGENT_DEV_HX": str(self.stubs / "hx"), "AGENT_DEV_TEST_PSI_DIR": str(self.psi),
        }
        env.update(extra)
        return env

    def run_dev(self, *args: str, dev: Path | None = None, unset: tuple = (), **extra: str) -> subprocess.CompletedProcess:
        env = self.env(**extra)
        for key in unset:
            env.pop(key, None)
        return subprocess.run([str(dev or self.dev), *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)

    # ---- records
    def calls(self, role: str = "host") -> list:
        path = self.logs / "docker.log"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [r["argv"] for r in rows if r["role"] == role]

    def runs(self) -> list:
        path = self.logs / "run.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def rm_calls(self) -> list:
        return [c for c in self.calls() if c[0] == "rm"]

    def hx_scripts(self) -> list:
        return sorted(self.logs.glob("hx-*.sh"), key=lambda p: int(p.stem.split("-")[1]))

    @staticmethod
    def argv_of(proc: subprocess.CompletedProcess) -> list:
        lines = proc.stdout.splitlines()
        return lines[lines.index("DOCKER-ARGV-BEGIN") + 1:lines.index("DOCKER-ARGV-END")]

    def ticket_free(self) -> bool:
        ticket = self.home / "host-admission" / "ticket"
        for _ in range(600):
            fd = os.open(ticket, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                time.sleep(0.05)
            finally:
                os.close(fd)
        return False


class ArgvTests(DevCase):
    """Acceptance 1, 2 (argv half), 3, 4 and the memory flags."""

    def test_dry_run_argv_for_a_python_profile_partial_args_only_with_test_arguments(self) -> None:
        proc = self.run_dev("--dry-run", "-p", "cmru", "test", "--", "-k", "x y")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.argv_of(proc)
        self.assertEqual(argv[:2], ["docker", "run"])
        for flag in ("--rm", "--init", "--memory-swap=-1", "--memory-reservation=1g", "--memory=8g"):
            self.assertIn(flag, argv)
        for name, value in (("--pull", "never"), ("--network", "none"), ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"),
                            ("--cgroup-parent", "dev-gates.slice"), ("--name", f"agent-dev-cmru-{self.hex}"), ("--user", "1003:1003"),
                            ("--pids-limit", "1024"), ("--cpus", "2"), ("--ulimit", "core=0:0")):
            self.assertEqual(option(argv, name), value, name)
        for word in FORBIDDEN + ("--cap-add",):
            self.assertNotIn(word, argv)
        lane_env = tomllib.loads((REPO_ROOT / "cmru" / "assay.toml").read_text())["lanes"]["cmru"].get("env", {})
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-e"], [f"{k}={v}" for k, v in lane_env.items()])
        image = argv.index(PY_IMAGE)
        self.assertEqual(argv[image + 1:image + 11], ["nice", "-n", "10", "ionice", "-c3", "timeout", "--foreground", "-k", "5", "3600"])
        cmd = argv[image + 11:]
        end = cmd.index('exec "$@"')  # the dry-run prints one argv word per line; the setup script spans lines
        cmd = cmd[:2] + ["\n".join(cmd[2:end + 1])] + cmd[end + 1:]
        self.assertEqual(cmd[0:2], ["bash", "-c"])
        self.assertIn("--editable ../assay", cmd[2])
        self.assertTrue(cmd[2].endswith('exec "$@"'))
        self.assertEqual(cmd[3:], ["dev", "/opt/tester-venv/bin/python", "-m", "pytest", "--cli-case-partial", "-k", "x y"])
        bare = self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test"))
        self.assertEqual(bare[-3:], ["/opt/tester-venv/bin/python", "-m", "pytest"])
        self.assertNotIn("--cli-case-partial", bare)
        self.assertEqual(self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "run", "--", "echo", "hi"))[-2:], ["echo", "hi"])

    def test_dry_run_argv_for_a_rust_profile_and_its_verbs(self) -> None:
        argv = self.argv_of(self.run_dev("--dry-run", "-p", "hostobs", "test", "-p", "hostobs-proto"))
        self.assertIn(RS_DEV_IMAGE, argv)
        self.assertIn("--memory-reservation=3g", argv)
        self.assertEqual(option(argv, "-w"), "/work/hostobs")
        self.assertEqual(option(argv, "--name"), f"agent-dev-hostobs-{self.hex}")
        self.assertIn(f"agent-dev-cache-hostobs-{self.hex}:/home/gate/.cache/hostobs-target", argv)
        self.assertIn("CARGO_NET_OFFLINE=true", argv)
        self.assertEqual(argv[argv.index("3600") + 1:], ["tools/cargo-offline.sh", "nextest", "run", "--offline", "--locked", "-p", "hostobs-proto"])
        self.assertNotIn("--cap-add", argv)
        self.assertNotIn("--cli-case-partial", argv)
        plain = self.argv_of(self.run_dev("--dry-run", "-p", "hostobs", "test", "--no-nextest", "-p", "x"))
        self.assertEqual(plain[argv.index("3600") + 1:][:2], ["tools/cargo-offline.sh", "test"])
        clippy = self.argv_of(self.run_dev("--dry-run", "-p", "hostobs", "clippy"))
        self.assertEqual(clippy[-5:], ["--workspace", "--all-targets", "--", "-D", "warnings"])
        gdb = self.argv_of(self.run_dev("--dry-run", "-p", "hostobs", "gdb", "--args", "/bin/true"))
        self.assertEqual(option(gdb, "--cap-add"), "SYS_PTRACE")

    def test_the_container_name_follows_the_project_directory(self) -> None:
        for project, proj in (("libraries/cli-extended", "cli-extended"), ("scripts/controller", "scripts-controller")):
            with self.subTest(project=project):
                argv = self.argv_of(self.run_dev("--dry-run", "-p", project, "shell"))
                self.assertEqual(option(argv, "--name"), f"agent-dev-{proj}-{self.hex}")

    def test_a_hostile_word_stays_exactly_one_argument_end_to_end(self) -> None:
        marker = self.tmp / "INJECTED"
        words = [f"a b;$(touch {marker})", "-leading", "two\nlines", "it's", f'"dq" `touch {marker}`', "$HOME", "*", ""]
        for project, verb in (("hostobs", ["run", "--"]), ("cmru", ["test", "--"])):
            with self.subTest(project=project):
                before = len(self.runs())
                proc = self.run_dev("-p", project, *verb, *words)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.runs()[before]["argv"][-len(words):], words)
                self.assertFalse(marker.exists())

    def test_limits_are_bounded_and_cannot_be_lifted(self) -> None:
        bad = {"AGENT_DEV_CPUS": ["0", "3.1", "-1", "x", "2;1", ""], "AGENT_DEV_MEMORY": ["511m", "9g", "x", "3", "1g;x", ""],
               "AGENT_DEV_PIDS": ["0", "1025", "x", ""], "AGENT_DEV_MAX_S": ["0", "14401", "x", ""], "AGENT_DEV_WAIT_S": ["x", "86401", "-1"]}
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    proc = self.run_dev("--dry-run", "-p", "cmru", "test", **{name: value})
                    self.assertEqual(proc.returncode, 2, proc.stdout)
                    self.assertEqual(proc.stderr.splitlines()[-1], "dev: exit 2")
        good = {"AGENT_DEV_CPUS": "3", "AGENT_DEV_MEMORY": "512m", "AGENT_DEV_PIDS": "1024", "AGENT_DEV_MAX_S": "14400", "AGENT_DEV_WAIT_S": "86400"}
        for name, value in good.items():
            with self.subTest(name=name, value=value):
                self.assertEqual(self.run_dev("--dry-run", "-p", "cmru", "test", **{name: value}).returncode, 0)
        self.assertIn("--memory-reservation=8g", self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test", AGENT_DEV_MEMORY="8g")))
        self.assertIn("--cpus", self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test", AGENT_DEV_CPUS="1.5")))

    def test_usage_errors_unknown_project_host_and_local_exit_2(self) -> None:
        cases = {
            "unknown project": (["-p", "nope", "test"], {}), "path-like project": (["-p", "../cmru", "test"], {}),
            "unknown verb": (["-p", "cmru", "frobnicate"], {}), "no project": (["test"], {}), "no verb": (["-p", "cmru"], {}),
            "host nano1": (["-p", "cmru", "test"], {"AGENT_DEV_HOST": "nano1"}),
            "r1002 not listed": (["-p", "cmru", "test"], {"AGENT_DEV_HOST": "r1002"}),
            "v1001 not listed": (["-p", "cmru", "test"], {"CONTROLLER_REMOTE_GATE_HOSTS": "r1002"}),
            "local": (["--local", "-p", "cmru", "test"], {}), "local with override": (["--local", "-p", "cmru", "test"], {"AGENT_DEV_ALLOW_LOCAL": "1"}),
            "old env": (["-p", "cmru", "test"], {"HOSTOBS_DEV_FOO": "1"}), "old image override": (["-p", "cmru", "test"], {"HOSTOBS_DEV_IMAGE": "x"}),
        }
        for label, (args, env) in cases.items():
            with self.subTest(label):
                proc = self.run_dev("--dry-run", *args, **env)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr.splitlines()[-1], "dev: exit 2")
        self.assertEqual(self.calls() + self.calls("cockpit"), [])
        listed = self.run_dev("--dry-run", "-p", "cmru", "test", AGENT_DEV_HOST="r1002", CONTROLLER_REMOTE_GATE_HOSTS="v1001 r1002")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(" r1002 ", listed.stdout)

    def test_profiles_cannot_override_the_safety_invariants(self) -> None:
        base = PROFILES.read_text()
        edits = {f"{key}-in-defaults": base.replace("[defaults]\n", f"[defaults]\n{key} = {value}\n", 1)
                 for key, value in (("network", '"host"'), ("cap_drop", '[]'), ("security_opt", '[]'), ("privileged", "true"), ("pid", '"host"'),
                                    ("init", "false"), ("cgroup_parent", '"x.slice"'), ("name", '"x"'), ("pull", '"always"'))}
        edits["network-in-profile"] = base.replace("[profiles.cmru]\n", '[profiles.cmru]\nnetwork = "host"\n', 1)
        edits["network-in-kind"] = base.replace("[kinds.python]\n", '[kinds.python]\nnetwork = "host"\n', 1)
        python_only = ("network-in-profile", "network-in-kind")
        edits["root user"] = base.replace('user = "1003:1003"', 'user = "0:0"')
        edits["foreign capability"] = base.replace('gdb = ["SYS_PTRACE"]', 'gdb = ["SYS_ADMIN"]')
        edits["escaping mount"] = base.replace('"{work}:/work"', '"/:/work"')
        for label, mounts in (("writable .git mount", '"{work}/.git:/work/.git"'), ("read-only .git mount", '"{work}/.git:/work/.git:ro"'),
                              ("a mount over .git", '"{cache}:/work/x/../.git"'), ("a mount below .git", '"{cache}:/work/.git/hooks"')):
            edits[label] = base.replace('mounts = ["{work}:/work"]', f'mounts = ["{{work}}:/work", {mounts}]')
        edits["hostile cache path"] = base.replace('cache = "/home/gate/.cache/hostobs-target"', "cache = " + json.dumps('/x"; touch Z; "'))
        edits["memory_max below the reservation"] = base.replace('memory_max = "8g"', 'memory_max = "512m"')
        for label, text in edits.items():
            with self.subTest(label):
                self.assertNotEqual(text, base)
                path = self.tmp / "profiles.toml"
                path.write_text(text)
                args = ["-p", "cmru", "test"] if label in python_only else ["-p", "hostobs", "gdb", "--args", "x"]
                proc = self.run_dev("--dry-run", *args, AGENT_DEV_TEST_PROFILES=str(path))
                self.assertEqual(proc.returncode, 2, proc.stdout)
        path = self.tmp / "profiles.toml"
        path.write_text(base.replace('memory_max = "8g"', 'memory_max = "4g"').replace('memory = "1g"', 'memory = "2g"', 1))
        argv = self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test", AGENT_DEV_TEST_PROFILES=str(path)))
        self.assertIn("--memory=4g", argv)
        self.assertIn("--memory-reservation=2g", argv)
        self.assertIn("--memory-swap=-1", argv)

    def test_the_read_only_git_mount_is_the_tools_own_whatever_the_profile_says(self) -> None:
        git_mount = f"$HOME/agent-dev/{self.hex}/work/.git:/work/.git:ro"
        self.assertEqual(self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test")).count(git_mount), 1)
        path = self.tmp / "profiles.toml"
        path.write_text(PROFILES.read_text().replace("[profiles.cmru]\n", "[profiles.cmru]\nmounts = []\n", 1))
        argv = self.argv_of(self.run_dev("--dry-run", "-p", "cmru", "test", AGENT_DEV_TEST_PROFILES=str(path)))
        self.assertEqual(argv.count(git_mount), 1)
        self.assertNotIn(f"$HOME/agent-dev/{self.hex}/work:/work", argv)

    def test_host_side_values_are_validated_and_hostile_ones_are_refused_before_any_host_call(self) -> None:
        marker = self.tmp / "INJECTED"
        payloads = [f'x"; touch {marker}; "', f"$(touch {marker})", f"`touch {marker}`", "a;b", "a\nb", "a b", "a\n"]
        root = ROOT_TOML.read_text()
        profiles = PROFILES.read_text()
        path = self.tmp / "profiles.toml"
        for payload in payloads + ["-x", "A"]:   # the last two are no image either (uppercase, leading dash) but are fine project keys
            quoted = json.dumps(payload)
            with self.subTest(payload=payload):
                (self.repo / "run-gate.root.toml").write_text(root.replace(f'"{PY_IMAGE}"', quoted))
                cases = [("lane image", ["-p", "cmru", "run", "--", "true"], {})]
                path.write_text(profiles.replace(f'"{RS_DEV_IMAGE}"', quoted))
                cases.append(("dev_image", ["-p", "hostobs", "run", "--", "true"], {"AGENT_DEV_TEST_PROFILES": str(path)}))
                key_path = self.tmp / "key-profiles.toml"
                key_path.write_text(profiles.replace("[profiles.cmru]", "[profiles." + quoted + "]"))
                if payload in payloads:
                    cases.append(("profile key", ["-p", payload, "run", "--", "true"], {"AGENT_DEV_TEST_PROFILES": str(key_path)}))
                for label, args, env in cases:
                    proc = self.run_dev(*args, **env)
                    self.assertEqual(proc.returncode, 2, (label, proc.stderr))
                    self.assertIn("must match", proc.stderr, label)   # refused by the profile validator, not by a later image check
                    self.assertFalse(marker.exists(), label)
                    self.assertEqual(self.hx_scripts(), [], label)
        (self.repo / "run-gate.root.toml").write_text(root)

    def test_the_shim_produces_the_argv_of_the_generic_tool_and_maps_the_old_environment(self) -> None:
        shim = self.repo / "hostobs" / "tools" / "dev"
        direct = self.run_dev("--dry-run", "-p", "hostobs", "test", "-p", "hostobs-proto")
        via = self.run_dev("--dry-run", "test", "-p", "hostobs-proto", dev=shim)
        self.assertEqual(via.returncode, 0, via.stderr)
        self.assertEqual(self.argv_of(via), self.argv_of(direct))
        mapped = self.run_dev("--dry-run", "shell", dev=shim, HOSTOBS_DEV_MEMORY="2g", HOSTOBS_DEV_CPUS="1", HOSTOBS_DEV_MAX_S="60")
        argv = self.argv_of(mapped)
        self.assertIn("--memory-reservation=2g", argv)
        self.assertEqual(option(argv, "--cpus"), "1")
        self.assertEqual(argv[argv.index("60") - 1], "5")
        unknown = self.run_dev("--dry-run", "shell", dev=shim, HOSTOBS_DEV_SLICE_MEM_MAX_MB="3000")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("HOSTOBS_DEV_SLICE_MEM_MAX_MB", unknown.stderr)

    def test_every_profile_agrees_with_its_projects_main_lane(self) -> None:
        for project, profile in PROFILE_DATA["profiles"].items():
            with self.subTest(project=project):
                gate = tomllib.loads((REPO_ROOT / project / "run-gate.toml").read_text())
                lane = gate["lanes"][profile["lane"]]
                self.assertEqual(lane["environment"], profile["environment"])
                self.assertEqual(profile["kind"] == "rust", profile["environment"] == "tester-unified-rust")
                self.assertEqual(profile["kind"] == "rust", "dev_image" in profile)
                raw = (REPO_ROOT / project / "run-gate.toml").read_text()
                if lane["kind"] == "assay":
                    self.assertEqual(profile["workdir"], project)
                    raw += (REPO_ROOT / project / "assay.toml").read_text()
                else:
                    self.assertRegex(" ".join(lane["argv"]), r"cd \{worktree\}/" + re.escape(profile["workdir"]) + r"(?![\w/.-])")
                text = raw.replace('\\"', "").replace('"', "")
                depth = len(profile["workdir"].split("/"))
                for step in profile.get("setup", []):
                    self.assertIn(" ".join(step), text)
                if profile.get("partial_args"):
                    self.assertIn(" ".join(profile["partial_args"]), text)
                for pair in profile.get("env", []):
                    self.assertIn(pair.replace("../" * depth, "{worktree}/"), text)
                res = subprocess.run(["python3", str(CONTROLLER / "dev_profile.py"), str(REPO_ROOT), project, "shell"], capture_output=True)
                self.assertEqual(res.returncode, 0, res.stderr)
                records = res.stdout.decode().split("\0")
                self.assertIn("image=" + ENV_IMAGES[profile["environment"]], records)
                self.assertIn("workdir=/work/" + profile["workdir"], records)


class RemoteTests(DevCase):
    """The remote script runs locally through the fake hx under the temporary HOME."""

    def work(self) -> Path:
        return self.home / "agent-dev" / self.hex / "work"

    def test_sync_resets_to_the_head_applies_the_overlay_and_never_accumulates(self) -> None:
        (self.repo / "cmru" / "keep.txt").write_text("edited\n")
        (self.repo / "cmru" / "gone.txt").unlink()
        (self.repo / "cmru" / "new.txt").write_text("untracked\n")
        self.assertEqual(self.run_dev("-p", "cmru", "run", "--", "true").returncode, 0)
        self.assertEqual((self.work() / "cmru" / "keep.txt").read_text(), "edited\n")
        self.assertFalse((self.work() / "cmru" / "gone.txt").exists())
        self.assertEqual((self.work() / "cmru" / "new.txt").read_text(), "untracked\n")
        (self.work() / "cmru" / "stale.txt").write_text("host-side leftover\n")
        hook = self.work() / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {self.tmp / 'HOOKRAN'}\n")
        hook.chmod(0o755)
        self.assertEqual(self.run_dev("-p", "cmru", "run", "--", "true").returncode, 0)
        self.assertFalse((self.work() / "cmru" / "stale.txt").exists())
        self.assertEqual(list((self.work() / ".git" / "hooks").iterdir()), [])
        self.assertFalse((self.tmp / "HOOKRAN").exists())
        run = self.runs()[0]["argv"]
        self.assertIn(f"{self.work()}:/work", run)
        self.assertIn(f"{self.work()}/.git:/work/.git:ro", run)
        self.assertEqual((self.logs / "hx.hosts").read_text().split(), ["v1001"] * 4)

    def test_the_patch_does_not_depend_on_the_cockpits_git_config(self) -> None:
        for key, value in (("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"), ("color.diff", "always"), ("color.ui", "always"), ("diff.external", "/bin/false")):
            subprocess.run(["git", "-C", str(self.repo), "config", key, value], check=True)
        (self.repo / "cmru" / "keep.txt").write_text("edited\n")
        (self.repo / "cmru" / "gone.txt").unlink()
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.work() / "cmru" / "keep.txt").read_text(), "edited\n")
        self.assertFalse((self.work() / "cmru" / "gone.txt").exists())

    def test_a_failed_host_side_sync_step_is_exit_3_naming_the_step_and_nothing_runs(self) -> None:
        (self.repo / "cmru" / "keep.txt").write_text("edited\n")
        (self.repo / "cmru" / "new.txt").write_text("untracked\n")
        for label, step, extra in (("apply", "git apply of the uncommitted changes", {"STUB_GIT_FAIL": "apply"}),
                                   ("fetch", "git fetch of the bundle", {"STUB_GIT_FAIL": "fetch"}), ("tar", "tar unpack of the untracked files", {"STUB_TAR_FAIL": "1"})):
            with self.subTest(label):
                shutil.rmtree(self.home / "agent-dev", ignore_errors=True)
                proc = self.run_dev("-p", "cmru", "run", "--", "true", **extra)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                lines = proc.stderr.splitlines()
                self.assertIn(f"dev: sync failed at: {step} (host side; the command never ran)", lines)
                self.assertEqual(lines[-2], "dev: exit 3")
                self.assertEqual(self.runs(), [])

    def test_an_oversized_overlay_is_refused_before_any_host_call(self) -> None:
        (self.repo / "cmru" / "big.bin").write_bytes(b"x" * (21 * 1024 * 1024))
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.hx_scripts(), [])

    def test_the_command_exit_code_propagates_and_the_container_is_removed_by_its_cid(self) -> None:
        proc = self.run_dev("-p", "cmru", "run", "--", "false", STUB_RUN_RC="7")
        self.assertEqual(proc.returncode, 7)
        self.assertNotIn("dev: exit", proc.stderr)
        self.assertEqual(self.rm_calls(), [["rm", "-f", f"cid-agent-dev-cmru-{self.hex}"]])
        self.assertFalse((self.home / "agent-dev" / self.hex / "cid").exists())
        before = len(self.rm_calls())
        self.assertEqual(self.run_dev("-p", "cmru", "run", "--", "true", STUB_RUN_RC="125", STUB_NO_CID="1").returncode, 125)
        self.assertEqual(len(self.rm_calls()), before)
        timed = self.run_dev("-p", "cmru", "run", "--", "true", STUB_RUN_RC="124")
        self.assertEqual(timed.returncode, 124)
        self.assertIn("dev: exit 124", timed.stderr)

    def test_tty_mode_hands_the_cidfile_and_the_work_mount_to_the_inner_bash(self) -> None:
        proc = self.run_dev("-p", "cmru", "run", "--", "true", AGENT_DEV_TEST_TTY="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.hx_scripts()), 3, "preflight, upload, run")
        argv = self.runs()[0]["argv"]
        self.assertIn("-it", argv)
        self.assertEqual(option(argv, "--cidfile"), str(self.home / "agent-dev" / self.hex / "cid"))
        self.assertIn(f"{self.work()}:/work", argv)
        self.assertIn(f"{self.work()}/.git:/work/.git:ro", argv)
        self.assertEqual(self.rm_calls(), [["rm", "-f", f"cid-agent-dev-cmru-{self.hex}"]])

    def test_the_banner_is_first_and_last_and_dirty_exactly_when_the_overlay_is_not_empty(self) -> None:
        sha = self.head()[:12]
        (self.repo / ".git" / "info" / "exclude").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("ignored\n")
        for label, change, dirty in (("clean", None, False), ("tracked change", ("cmru/keep.txt", "edited\n"), True), ("untracked file", ("cmru/new.txt", "n\n"), True)):
            with self.subTest(label):
                subprocess.run(["git", "-C", str(self.repo), "checkout", "-q", "--", "."], check=True)
                subprocess.run(["git", "-C", str(self.repo), "clean", "-q", "-fd", "-e", "ignored.txt"], check=True)
                if change:
                    (self.repo / change[0]).write_text(change[1])
                proc = self.run_dev("-p", "cmru", "run", "--", "true")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                banner = f"AGENT-DEV cmru v1001 {sha}{'+dirty' if dirty else ''}: NOT GATE EVIDENCE"
                lines = proc.stderr.splitlines()
                self.assertEqual((lines[0], lines[-1]), (banner, banner))

    def test_own_exit_codes_are_printed_before_the_closing_banner(self) -> None:
        sha = self.head()[:12]
        banner = f"AGENT-DEV cmru v1001 {sha}: NOT GATE EVIDENCE"
        self.set_images("host", **{PY_IMAGE.replace(":", "__"): {"id": image_id("other"), "label": ""}})
        for label, code, extra in (("image mismatch", 2, {}), ("transport", 3, {"STUB_HX_RC": "255"}), ("identity", 3, {"STUB_HX_RC": "97"}), ("guard", 3, {"STUB_HX_RC": "98"})):
            with self.subTest(label):
                proc = self.run_dev("-p", "cmru", "run", "--", "true", **extra)
                self.assertEqual(proc.returncode, code, proc.stderr)
                lines = proc.stderr.splitlines()
                self.assertEqual((lines[0], lines[-2], lines[-1]), (banner, f"dev: exit {code}", banner))
        self.assertEqual(self.runs(), [])

    def test_wrong_uid_and_a_busy_worktree_exit_3(self) -> None:
        proc = self.run_dev("-p", "cmru", "run", "--", "true", STUB_UID="1000")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("1003:1003", proc.stderr)
        lock = self.home / "agent-dev" / self.hex
        lock.mkdir(parents=True, exist_ok=True)
        with (lock / "lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("another dev call for this worktree", proc.stderr)
        self.assertEqual((self.runs(), self.rm_calls(), self.work().exists()), ([], [], False))
        (self.logs / "containers.txt").write_text(f"agent-dev-cmru-{self.hex}\n")
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("dev stop", proc.stderr)
        self.assertEqual(self.runs(), [])

    def test_every_docker_call_on_the_host_is_by_exact_name_or_own_cid_or_a_fixed_prefix(self) -> None:
        self.run_dev("-p", "cmru", "run", "--", "true")
        self.run_dev("-p", "cmru", "status")
        self.run_dev("-p", "cmru", "stop")
        self.run_dev("gc", "--dry-run")
        names = {f"agent-dev-cmru-{self.hex}", f"cid-agent-dev-cmru-{self.hex}", f"agent-dev-cache-cmru-{self.hex}"}
        for call in self.calls():
            self.assertFalse(any("ancestor" in a or a in ("prune", "--all") for a in call), call)
            if call[0] == "ps":
                self.assertIn(call[call.index("--filter") + 1], ("name=^agent-dev-", "name=^hostobs-dev-"))
            elif call[0] in ("inspect", "rm") or call[:2] == ["volume", "inspect"]:
                self.assertTrue(set(call) & names, call)
            else:
                self.assertIn(call[0], ("run", "image", "volume"), call)

    # ---- admission (host-admission/1)
    def test_memory_psi_full_below_3_is_admitted_and_3_waits_then_75_at_the_deadline(self) -> None:
        self.write_psi(mem_full="2.90")
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.runs()), 1)
        self.write_psi(mem_full="3.00")
        self.set_clock(1000)
        proc = self.run_dev("-p", "cmru", "run", "--", "true", AGENT_DEV_TEST_CLOCK=str(self.clock), AGENT_DEV_WAIT_S="300")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(int(self.clock.read_text()), 1300)
        self.assertEqual(sum(1 for l in proc.stderr.splitlines() if l.startswith("dev: waiting for host admission")), 4)
        self.assertIn("memory PSI full avg10 3.00 >= 3", proc.stderr)
        self.assertIn("dev: exit 75", proc.stderr)

    def test_only_memory_full_pressure_blocks(self) -> None:
        for label, kwargs in (("memory some", {"mem_some": "80.00"}), ("io full", {"io_full": "90.00", "io_some": "95.00"}), ("cpu", {"cpu_some": "99.00", "cpu_full": "99.00"})):
            with self.subTest(label):
                self.write_psi(**kwargs)
                before = len(self.runs())
                proc = self.run_dev("-p", "cmru", "run", "--", "true", AGENT_DEV_WAIT_S="5")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(len(self.runs()), before + 1)
                self.assertNotIn("waiting for host admission", proc.stderr)

    def test_unreadable_pressure_is_75_at_once(self) -> None:
        for label, content in (("missing", None), ("no full line", "some avg10=0.00 avg60=0 avg300=0 total=0\n"), ("garbage", "full avg10=abc avg60=0\n"), ("empty", "")):
            with self.subTest(label):
                self.write_psi()
                if content is None:
                    (self.psi / "memory").unlink()
                else:
                    (self.psi / "memory").write_text(content)
                self.set_clock(1000)
                proc = self.run_dev("-p", "cmru", "run", "--", "true", AGENT_DEV_TEST_CLOCK=str(self.clock))
                self.assertEqual(proc.returncode, 75, proc.stderr)
                self.assertIn("cannot read memory PSI", proc.stderr)
                self.assertEqual(int(self.clock.read_text()), 1000)
        self.assertEqual(self.runs(), [])

    def test_the_start_ticket_and_the_lock_are_held_when_docker_runs_and_serialise_starts(self) -> None:
        proc = self.run_dev("-p", "cmru", "run", "--", "true", STUB_SETTLE_KEEP="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = self.runs()[0]
        self.assertTrue(rec["ticket_held"], "the ticket must stay held through the settle, past the start")
        self.assertTrue(rec["lock_held"])
        # the ticket is still held by the settle: a second start cannot get it
        proc = self.run_dev("-p", "cmru", "run", "--", "true", STUB_SETTLE_KEEP="1", AGENT_DEV_WAIT_S="1")
        self.assertEqual(proc.returncode, 75, proc.stderr)
        self.assertIn("no start ticket", proc.stderr)
        self.assertEqual(len(self.runs()), 1)
        (self.logs / "settle.release").write_text("")
        self.assertTrue(self.ticket_free())
        self.assertEqual(self.run_dev("-p", "cmru", "run", "--", "true").returncode, 0)
        self.assertEqual(len(self.runs()), 2)
        self.assertTrue(self.ticket_free())

    def test_the_trap_removes_the_container_by_cid_on_every_signal(self) -> None:
        def restore() -> None:
            for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGPIPE, signal.SIGTERM):
                signal.signal(sig, signal.SIG_DFL)
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGPIPE, signal.SIGTERM):
            with self.subTest(signal=sig.name):
                hold = self.tmp / f"hold-{sig.name}"
                self.rm_before = len(self.rm_calls())
                proc = subprocess.Popen([str(self.dev), "-p", "cmru", "run", "--", "true"], env=self.env(STUB_RUN_HOLD=str(hold)),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=restore)
                try:
                    for _ in range(600):
                        if Path(f"{hold}.started").exists():
                            break
                        time.sleep(0.1)
                    self.assertTrue(Path(f"{hold}.started").exists(), "the container never started")
                    pid = self.runs()[-1]["remote_pid"]
                    self.assertIn(f"HEX={self.hex}".encode(), Path(f"/proc/{pid}/cmdline").read_bytes(), "refusing to signal a foreign process")
                    os.kill(pid, sig)
                    for _ in range(300):
                        if len(self.rm_calls()) > self.rm_before:
                            break
                        time.sleep(0.1)
                    # the docker client is still held: the trap must not wait for it
                    self.assertFalse(hold.exists())
                    self.assertEqual(self.rm_calls()[self.rm_before:], [["rm", "-f", f"cid-agent-dev-cmru-{self.hex}"]], "removed only after the client ended")
                finally:
                    hold.write_text("go")
                    proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 128 + int(sig), sig.name)
                self.assertEqual(self.rm_calls()[self.rm_before:], [["rm", "-f", f"cid-agent-dev-cmru-{self.hex}"]])
                self.assertFalse((self.home / "agent-dev" / self.hex / "cid").exists())

    # ---- images
    def test_an_image_id_mismatch_exits_2_before_admission(self) -> None:
        self.set_images("host", **{PY_IMAGE.replace(":", "__"): {"id": image_id("stale"), "label": ""}})
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        for text in (PY_IMAGE, image_id("stale"), image_id(PY_IMAGE), "push-image.sh"):
            self.assertIn(text, proc.stderr)
        self.assertEqual((self.runs(), self.work().exists(), (self.home / "host-admission").exists()), ([], False, False))
        self.assertEqual(len(self.hx_scripts()), 1)

    def test_a_missing_host_image_and_a_foreign_dev_image_label_exit_2_and_a_missing_cockpit_tag_is_presence_only(self) -> None:
        self.set_images("host", **{PY_IMAGE.replace(":", "__"): {"id": "", "label": ""}})
        proc = self.run_dev("-p", "cmru", "run", "--", "true")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("push-image.sh", proc.stderr)
        self.set_images("host", **{RS_DEV_IMAGE.replace(":", "__"): {"id": image_id(RS_DEV_IMAGE), "label": image_id("older base")}})
        proc = self.run_dev("-p", "hostobs", "run", "--", "true")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(RS_DEV_IMAGE, proc.stderr)
        self.set_images("host")
        (self.logs / "images-cockpit.json").write_text("{}")
        proc = self.run_dev("-p", "hostobs", "run", "--", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("the cockpit has no", proc.stderr)
        self.assertEqual(len(self.runs()), 1)

    # ---- gc
    def test_gc_dry_run_lists_only_stale_exact_names_and_a_real_gc_removes_exactly_those(self) -> None:
        day = 86400
        def make(base: str, h: str, age: int) -> Path:
            d = self.home / base / h
            d.mkdir(parents=True)
            (d / "work").mkdir()
            stamp = d / "last-use"
            stamp.write_text("")
            os.utime(stamp, (time.time() - age * day,) * 2)
            return d
        stale, young, busy, running, legacy = ("a" * 12, "b" * 12, "c" * 12, "d" * 12, "e" * 12)
        own_old = make("agent-dev", self.hex, 4)
        dirs = {n: make("agent-dev", h, a) for n, h, a in (("stale", stale, 4), ("young", young, 1), ("busy", busy, 40), ("running", running, 40))}
        dirs["legacy"] = make("hostobs-dev", legacy, 5)
        junk = self.home / "agent-dev" / "not-a-hex"
        junk.mkdir()
        seed = self.home / "checkouts" / ("1" * 40)
        seed.mkdir(parents=True)
        os.utime(seed, (time.time() - 90 * day,) * 2)
        (self.logs / "containers.txt").write_text(f"agent-dev-cmru-{running}\n")
        volumes = [f"agent-dev-cache-hostobs-{stale}", f"agent-dev-cache-cmru-{stale}", f"agent-dev-cache-hostobs-{young}", f"hostobs-dev-target-{legacy}",
                   f"other-volume-{stale}", f"agent-dev-cache-hostobs-{running}"]
        (self.logs / "volumes.txt").write_text("".join(v + "\n" for v in volumes))
        with (dirs["busy"] / "lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            dry = self.run_dev("gc", "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            expected_dry = {f"would remove volume agent-dev-cache-hostobs-{stale}", f"would remove volume agent-dev-cache-cmru-{stale}", f"would remove {dirs['stale']}",
                            f"would remove volume hostobs-dev-target-{legacy}", f"would remove {dirs['legacy']}",
                            f"skip {busy}: lock held", f"skip {running}: a container exists"}
            self.assertEqual(set(dry.stdout.splitlines()), expected_dry)
            self.assertTrue(all(d.exists() for d in (*dirs.values(), own_old, junk, seed)))
            self.assertEqual([c for c in self.calls() if c[:2] == ["volume", "rm"]], [])
            real = self.run_dev("gc")
            self.assertEqual(real.returncode, 0, real.stderr)
        self.assertEqual([c[2] for c in self.calls() if c[:2] == ["volume", "rm"]],
                         [f"agent-dev-cache-hostobs-{stale}", f"agent-dev-cache-cmru-{stale}", f"hostobs-dev-target-{legacy}"])
        self.assertFalse(dirs["stale"].exists() or dirs["legacy"].exists())
        self.assertTrue(all(d.exists() for d in (dirs["young"], dirs["busy"], dirs["running"], own_old, junk, seed)))
        os.utime(own_old / "last-use", (time.time() - 31 * day,) * 2)
        self.assertIn(f"would remove {own_old}", self.run_dev("gc", "--dry-run").stdout)

    def test_clean_through_the_shim_removes_only_this_worktrees_state(self) -> None:
        mine, other = self.home / "agent-dev" / self.hex, self.home / "agent-dev" / ("f" * 12)
        for d in (mine, other):
            d.mkdir(parents=True)
        (self.logs / "volumes.txt").write_text(f"agent-dev-cache-hostobs-{self.hex}\nagent-dev-cache-hostobs-{'f' * 12}\n")
        proc = self.run_dev("clean", dev=self.repo / "hostobs" / "tools" / "dev")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())
        self.assertEqual([c[2] for c in self.calls() if c[:2] == ["volume", "rm"]], [f"agent-dev-cache-hostobs-{self.hex}"])


class SourceTests(unittest.TestCase):
    def test_scripts_are_valid_bash_python_and_executable(self) -> None:
        for script in (CONTROLLER / "dev", REPO_ROOT / "hostobs" / "tools" / "dev"):
            self.assertTrue(os.access(script, os.X_OK), script)
            self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0, script)
        text = (CONTROLLER / "dev").read_text()
        self.assertIn("set -euo pipefail", text)
        for fragment in ("--privileged", "--pid=host", "--net=host", "--network host", "docker.sock", "ancestor=", "--cap-add SYS_ADMIN"):
            self.assertNotIn(fragment, text, fragment)
        self.assertNotRegex(text, r"(?m)^\s*ssh\b")
        self.assertLessEqual(len((REPO_ROOT / "hostobs" / "tools" / "dev").read_text().splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
