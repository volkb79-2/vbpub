"""The CMRU release wrapper delegates only the selected Assay gate commands."""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT
from assay.config import load_lane_file

SCRIPT = PROJECT_ROOT / "tools" / "release-gate.sh"
REPO_ROOT = PROJECT_ROOT.parent
CMRU_FILE = PROJECT_ROOT / "cmru.toml"
RUN_GATE_FILE = PROJECT_ROOT / "run-gate.toml"
ASSAY_FILE = PROJECT_ROOT / "assay.toml"
CONSUMERS_FILE = PROJECT_ROOT / "docs" / "CONSUMERS.md"
WORKFLOW_FILE = PROJECT_ROOT / "nyxloom-trove" / "WORKFLOW.md"


FAKE_REMOTE_GATE = r'''#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path

args = sys.argv[1:]
# The wrapper scrubs the environment and resolves REMOTE_GATE_OUT itself, so the
# fake's control file cannot live in the resolved out dir (a resolution test moves
# it). The python shim below always passes it here.
config_path = Path(args[1])
args = args[2:]
remote_out = Path(os.environ["REMOTE_GATE_OUT"])
config = json.loads(config_path.read_text(encoding="utf-8"))
calls_path = remote_out / "remote-calls.jsonl"

def record_call(argv):
    with calls_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(argv) + "\n")

if args[0] == "cancel":
    record_call(args)
    (remote_out / "remote-cancelled.txt").write_text("cancelled", encoding="utf-8")
    (remote_out / "remote-cancel-env-secrets.json").write_text(
        json.dumps({
            key: os.environ.get(key) is not None
            for key in (
                "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
                "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
            )
        }),
        encoding="utf-8",
    )
    cancel_hold_until = config.get("cancel_hold_until")
    if cancel_hold_until:
        # A cancel that is slow on purpose. The wrapper is blocked in this call, so a
        # heartbeat it failed to stop keeps printing into the wrapper's stderr while
        # `cancel` runs. The hold ends only when the test releases it, and the tick count
        # gives the test an event (`ticks >= 10`, i.e. at least 100 ms of holding) instead
        # of a fixed pause: a slower host only holds longer, which can never turn a
        # correct run into a failure.
        (remote_out / "remote-cancel-holding.txt").write_text("holding", encoding="utf-8")
        ticks_path = remote_out / "remote-cancel-hold-ticks.txt"
        ticks_tmp_path = ticks_path.with_name(ticks_path.name + ".tmp")
        ticks = 0
        deadline = time.monotonic() + float(config.get("cancel_hold_max_seconds", 120))
        while not Path(cancel_hold_until).exists():
            if time.monotonic() > deadline:
                print(
                    "fake remote-gate: cancel_hold_until was never released",
                    file=sys.stderr,
                )
                raise SystemExit(91)
            ticks += 1
            # Written to a side name and renamed over the counter, so a reader can
            # never catch the file between truncation and the new digits: the test
            # waits for this counter, and a read of a half-written file is a test
            # defect, not a slow host.
            ticks_tmp_path.write_text(str(ticks), encoding="utf-8")
            ticks_tmp_path.replace(ticks_path)
            time.sleep(0.01)
    if config.get("signal_mode"):
        pid = int((remote_out / "remote-child.pid").read_text(encoding="utf-8"))
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            pass
    raise SystemExit(int(config.get("cancel_status", 0)))

if args[0] != "run" or "--detach" in args:
    raise SystemExit("release-gate must use attached remote-gate mode")
record_call(args)
name = args[args.index("--name") + 1]
lane = args[3]
run_dir = remote_out / name
run_dir.mkdir(parents=True, exist_ok=False)
if config.get("write_meta", True):
    (run_dir / "meta.json").write_text(
        json.dumps({"sha": config["meta_sha"]}), encoding="utf-8"
    )
if config.get("write_result", True):
    (run_dir / "result.json").write_text(
        json.dumps({
            "lane": lane,
            "verdict": config.get("verdict", "PASS"),
            "exit_code": int(config.get("lane_status", 0)),
        }),
        encoding="utf-8",
    )
(remote_out / "remote-env-secrets.json").write_text(
    json.dumps({
        key: os.environ.get(key) is not None
        for key in (
            "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
            "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
        )
    }),
    encoding="utf-8",
)
(remote_out / "remote-env-keys.json").write_text(
    json.dumps(sorted(os.environ)), encoding="utf-8"
)
(remote_out / "remote-gate-selection.json").write_text(
    json.dumps({
        "REMOTE_GATE_HOSTS": os.environ.get("REMOTE_GATE_HOSTS"),
        "REMOTE_GATE_OUT": os.environ.get("REMOTE_GATE_OUT"),
    }),
    encoding="utf-8",
)
child_pid_path = remote_out / "remote-child.pid"
child_pid_tmp_path = child_pid_path.with_name(child_pid_path.name + ".tmp")
child_pid_tmp_path.write_text(str(os.getpid()), encoding="utf-8")
child_pid_tmp_path.replace(child_pid_path)
hold_until = config.get("hold_until")
if hold_until:
    # Event-driven, so the test's assertions never rest on a duration: the wrapper's
    # heartbeat is observed first, then the test writes this file to let the run end.
    # The ceiling is above the test's own wait ceiling (L5), so only that wait decides a
    # verdict and a heavily loaded host cannot make the fake give up first.
    deadline = time.monotonic() + float(config.get("hold_max_seconds", 120))
    while not Path(hold_until).exists():
        if time.monotonic() > deadline:
            print(
                "fake remote-gate: hold_until was never released",
                file=sys.stderr,
            )
            raise SystemExit(90)
        time.sleep(0.01)
if config.get("signal_mode"):
    released = False
    def release(_sig, _frame):
        global released
        released = True
    def handle_signal(sig, _frame):
        (remote_out / "remote-signal.txt").write_text(str(sig), encoding="utf-8")
        while not released:
            signal.pause()
        (remote_out / "remote-child-finished.txt").write_text("finished", encoding="utf-8")
        raise SystemExit(128 + sig)
    signal.signal(signal.SIGUSR1, release)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    with open(config["ready_fifo"], "w", encoding="utf-8") as stream:
        stream.write("ready\n")
    while True:
        signal.pause()
remote_status = config.get("remote_status")
if remote_status is None:
    remote_status = config.get("lane_status", 0)
raise SystemExit(int(remote_status))
'''


def _environment(tmp_path: Path, *, host: str = "v1001") -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_python = sys.executable
    fake_remote = fake_bin / "remote-gate.py"
    fake_remote.write_text(FAKE_REMOTE_GATE, encoding="utf-8")
    fake_remote.chmod(0o755)

    python = fake_bin / "python3"
    python.write_text(
        f"#!{real_python}\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        f"fake_remote = {str(fake_remote)!r}\n"
        f"real_python = {real_python!r}\n"
        f"fake_config = {str(tmp_path / 'fake-config.json')!r}\n"
        "if sys.argv[1:2] == ['-'] and sys.argv[2:3] == [\n"
        "    '../scripts/netcup/runner/remote-gate.py'\n"
        "]:\n"
        "    # release-gate.sh reads remote-gate.py's DEFAULT_HOSTS/DEFAULT_OUT by\n"
        "    # importing the module. A test that pins the \"module default last\"\n"
        "    # resolution order must control those two values instead of inheriting\n"
        "    # whatever this host's real defaults happen to be.\n"
        "    try:\n"
        "        defaults = json.loads(open(fake_config, encoding='utf-8').read())\n"
        "    except (OSError, ValueError):\n"
        "        defaults = {}\n"
        "    wanted = defaults.get('module_defaults')\n"
        "    if wanted is not None:\n"
        "        hosts = wanted.get('hosts')\n"
        "        if hosts is None:\n"
        "            # remote-gate.py's DEFAULT_HOSTS is\n"
        "            # os.path.expanduser('~/.config/remote-gate/hosts.json'), so derive it\n"
        "            # from HOME here instead of inventing a fixed path: a test that\n"
        "            # removes HOME must see the missing-path refusal the real module\n"
        "            # would produce.\n"
        "            hosts = os.path.expanduser('~/.config/remote-gate/hosts.json')\n"
        "        if wanted.get('import_failure'):\n"
        "            # The import that failed is not an empty default (finding 5):\n"
        "            # the loader must report the failure, never a blank path.\n"
        "            print('remote-gate.py cannot be imported', file=sys.stderr)\n"
        "            raise SystemExit(3)\n"
        "        if wanted.get('import_failure_silent'):\n"
        "            # The round-3 finding: an import that dies printing NOTHING (a\n"
        "            # SIGKILL or OOM-kill, or a bare SystemExit) leaves no last stderr\n"
        "            # line to quote. An empty reason would make both consumers report\n"
        "            # 'DEFAULT_HOSTS resolved to an empty path' -- a path nothing\n"
        "            # supplied. The status is the one fact left; the loader names it.\n"
        "            raise SystemExit(3)\n"
        "        print(hosts)\n"
        "        print(wanted['out'])\n"
        "        raise SystemExit(0)\n"
        "if sys.argv[1:2] == ['../scripts/netcup/runner/remote-gate.py']:\n"
        "    argv = [real_python, fake_remote, '--fake-config', fake_config,\n"
        "            *sys.argv[2:]]\n"
        "else:\n"
        "    argv = [real_python, *sys.argv[1:]]\n"
        "os.execv(real_python, argv)\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    remote_out = tmp_path / "remote-runs"
    remote_out.mkdir()
    (tmp_path / "fake-config.json").write_text(
        json.dumps(
            {
                "meta_sha": commit,
                "verdict": "PASS",
                "lane_status": 0,
                "write_meta": True,
                "write_result": True,
                # remote-gate.py's own default output directory, as this test's fake
                # python shim answers it, so no release-gate test imports the real
                # module and no test reads the cockpit's real ~/.config. The default
                # HOST TABLE is not fixed here: the shim derives it from HOME with
                # os.path.expanduser, exactly as remote-gate.py:38 does, so a test that
                # removes HOME pins the refusal the real module produces.
                "module_defaults": {
                    "out": str(remote_out),
                },
            }
        ),
        encoding="utf-8",
    )
    # A hermetic HOME: remote-gate.py derives its own default host table from it,
    # so the "module default last" resolution order is reachable without reading
    # the cockpit's real ~/.config. A HOME with no host table would instead make
    # every base-case test refuse.
    home = tmp_path / "home"
    default_hosts_dir = home / ".config" / "remote-gate"
    default_hosts_dir.mkdir(parents=True)
    (default_hosts_dir / "hosts.json").write_text("{}", encoding="utf-8")
    # An empty CONTROLLER_HOME means "this controller configures nothing", so a
    # base-case test never reads the cockpit's own controller.local.env.
    controller_home = tmp_path / "controller"
    controller_home.mkdir()
    env = {
        **os.environ,
        # The self-hosted lane scrubs HOME (env_passthrough is PATH only) and
        # release-gate.sh forwards HOME under `set -u`: the helper must not
        # depend on the caller having one.
        "HOME": str(home),
        "CONTROLLER_HOME": str(controller_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REMOTE_GATE_OUT": str(remote_out),
        "CMRU_RELEASE_GATE_COMMIT": commit,
        # Test-only: where the fake remote-gate reads its control file, and where
        # the test looks for the calls it recorded. Neither key reaches a child
        # process: the wrapper runs remote-gate under `env -i` with an allowlist.
        "ASSAY_TEST_FAKE_CONFIG": str(tmp_path / "fake-config.json"),
        "ASSAY_TEST_OUT": str(remote_out),
    }
    # The caller's own remote-gate settings must not steer the wrapper's resolution.
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("ASSAY_RELEASE_GATE_HEARTBEAT_S", None)
    for name in (
        "CONTROLLER_HOSTS_FILE",
        "CONTROLLER_REMOTE_HOSTS_FILE",
        "CONTROLLER_REMOTE_OUT",
    ):
        env.pop(name, None)
    if host:
        env["ASSAY_RELEASE_GATE_HOST"] = host
    else:
        env.pop("ASSAY_RELEASE_GATE_HOST", None)
    return env


def _fake_config(env: dict[str, str]) -> Path:
    # The fake remote-gate's control file lives outside the resolved output
    # directory on purpose: a resolution test moves REMOTE_GATE_OUT, and the
    # wrapper's `env -i` scrub means this test-only key never reaches a child.
    return Path(env["ASSAY_TEST_FAKE_CONFIG"])


def _configure_fake(env: dict[str, str], **updates: object) -> None:
    path = _fake_config(env)
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(updates)
    path.write_text(json.dumps(config), encoding="utf-8")


def _remote_calls(env: dict[str, str]) -> list[list[str]]:
    # Deliberately reads the test's own copy of the default output directory: an
    # assertion that no remote run started must not be vacuous just because the
    # test removed REMOTE_GATE_OUT to force the wrapper to resolve one.
    return _remote_calls_at(Path(env["ASSAY_TEST_OUT"]))


def _remote_calls_at(out_dir: Path) -> list[list[str]]:
    path = out_dir / "remote-calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _remote_selection_at(out_dir: Path) -> dict[str, str | None]:
    """What the wrapper actually handed remote-gate.py as its two settings."""
    return json.loads(
        (out_dir / "remote-gate-selection.json").read_text(encoding="utf-8")
    )


COMBINED_REFUSAL_HEADER = (
    "assay release-gate: the remote release-gate environment is not usable:"
)


def _combined_refusal(problems: list[str]) -> str:
    return COMBINED_REFUSAL_HEADER + "".join(f"\n  - {problem}" for problem in problems) + "\n"


# A heartbeat interval must be 30 s or more, so a test cannot observe one inside a
# reasonable run without speeding that ONE sleep up: the BASH_ENV definition replaces
# the wrapper's `sleep 300` heartbeat wait with 50 ms and passes every other duration
# through unchanged. The assertion itself is event-driven (a line is waited for, never
# a fixed pause), so no result depends on how fast the host runs the loop.
FAST_HEARTBEAT_ENV = (
    "sleep() {\n"
    '  case "${1:-}" in\n'
    "    300) /bin/sleep 0.05 ;;\n"
    '    *) command sleep "$@" ;;\n'
    "  esac\n"
    "}\n"
)
HEARTBEAT_LINE = re.compile(
    r"^assay release-gate: waiting for remote run "
    r"(assay-rel-[0-9a-f]{12}-(?:tester-unified|self-qualification-provisional)-[0-9a-z]{7}) "
    r"on (v1001|r1002) \((\d+)s\)$"
)


def _fast_heartbeat_sleep(tmp_path: Path) -> str:
    path = tmp_path / "fast-heartbeat-sleep.sh"
    path.write_text(FAST_HEARTBEAT_ENV, encoding="utf-8")
    return str(path)


class Wrapper:
    """A running release-gate.sh with its output files, closed together."""

    def __init__(self, tmp_path: Path, env: dict[str, str]) -> None:
        self.stderr_path = tmp_path / "wrapper.stderr"
        self.stdout_path = tmp_path / "wrapper.stdout"
        self.stderr_path.write_text("", encoding="utf-8")
        self.stdout_path.write_text("", encoding="utf-8")
        self._streams = (
            self.stdout_path.open("a", encoding="utf-8"),
            self.stderr_path.open("a", encoding="utf-8"),
        )
        self.proc = subprocess.Popen(
            ["/bin/bash", str(SCRIPT), "tester-unified"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=self._streams[0],
            stderr=self._streams[1],
            start_new_session=True,
        )

    def stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8")

    def wait_for(self, predicate, what: str, *, timeout_s: float = 60.0) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            text = self.stderr()
            if predicate(text):
                return text
            time.sleep(0.01)
        raise AssertionError(
            f"the wrapper never {what}; exit {self.proc.poll()}; stderr so far:\n"
            f"{self.stderr()}"
        )

    def reap(self, *, timeout_s: float = 60.0) -> int:
        try:
            self.proc.wait(timeout=timeout_s)
        finally:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if self.proc.poll() is None:
                self.proc.wait(timeout=timeout_s)
            for stream in self._streams:
                stream.close()
        return self.proc.returncode

    def kill_leftovers(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _heartbeat_lines(text: str) -> list[re.Match[str]]:
    return [
        match
        for line in text.splitlines()
        if (match := HEARTBEAT_LINE.match(line)) is not None
    ]


def _pid_state(pid: int) -> str | None:
    """The kernel's state letter for `pid`, or None when the pid no longer exists."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[0]


def _pid_running(pid: int) -> bool:
    """True while `pid` is a live process; a zombie has exited and is only unreaped."""
    return _pid_state(pid) not in (None, "Z")


def _wait_until(predicate, what: str, *, timeout_s: float = 60.0) -> None:
    """An event-driven wait with a ceiling; never a fixed pause deciding a verdict."""
    _wait_for_value(lambda: True if predicate() else None, what, timeout_s=timeout_s)


def _wait_for_value(producer, what: str, *, timeout_s: float = 60.0):
    """The first truthy value `producer` returns before the ceiling.

    Same event-driven contract as `_wait_until`, for an event whose value the test
    needs (a pid it must watch) rather than only its occurrence.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = producer()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out after {timeout_s} s waiting for {what}")


def _publish_text(path: Path, text: str) -> None:
    """Publish a whole event file under its final name."""
    side = path.with_name(path.name + ".tmp")
    side.write_text(text, encoding="utf-8")
    side.replace(path)


def _hold_ticks_reached(path: Path, wanted: int) -> bool:
    """Has the fake's held-cancel counter reached `wanted`?

    The counter is the test's event. The fake publishes it by writing a side name
    and renaming it over the counter, so a file a reader can open holds whole
    digits; the predicate still treats an absent or unreadable file as "not yet",
    because the caller's own ceiling -- never a fixed pause -- decides a failure.
    """
    try:
        return int(path.read_text(encoding="utf-8")) >= wanted
    except (OSError, ValueError):
        return False


def _parent_pid(pid: int) -> int | None:
    """The kernel's recorded parent of `pid`, or None when its stat cannot be read."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[1].split()
    return int(fields[1])


def _descends_from(pid: int, ancestor: int) -> bool:
    """True when `pid`'s parent chain reaches `ancestor`."""
    seen: set[int] = set()
    current = _parent_pid(pid)
    while current is not None and current > 0 and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        current = _parent_pid(current)
    return False


def _heartbeat_sleep(ancestor: int, interval: int) -> tuple[int, int] | None:
    """The heartbeat's live `sleep <interval>` child: (sleep pid, heartbeat pid).

    The wrapper forks the heartbeat subshell, which forks exactly one foreground
    `sleep` per iteration. Both carry the wrapper's own command line, because a
    subshell does not exec, so this finds the pair by walking /proc: a process
    whose argv is exactly `sleep <interval>` and whose parent chain reaches the
    wrapper is either the sleep the trap must kill or the next iteration's.
    """
    wanted = b"".join(f"{part}\0".encode() for part in ("sleep", str(interval)))
    found: list[tuple[int, int]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        pid = int(entry.name)
        if cmdline != wanted or not _pid_running(pid):
            continue
        parent = _parent_pid(pid)
        if parent is None or parent == ancestor:
            continue
        if _descends_from(pid, ancestor):
            found.append((pid, parent))
    return found[0] if len(found) == 1 else None


# Finding 3 (B579 round 2 review): the heartbeat's trap must name only a job the
# loop still owns. This hook holds the heartbeat inside its own `trap` builtin, at
# the instant the real trap is installed: the loop's first `sleep ... &` has not run
# yet, so the subshell's job table must be empty, and `$!` still carries the pid the
# subshell INHERITED from its parent -- the wrapper's `remote-gate.py` child
# (`remote_gate_pid`). The first seam line is `$BASHPID` and that inherited `$!`; the
# lines after it are the job table the trap would take its targets from. The hook
# publishes the seam under a side name and renames it into place, so a reader that
# sees the file at all sees a whole one, and it holds until the test's release file
# appears (the `finally` block always writes it) rather than for a counted number of
# ticks.
HEARTBEAT_TRAP_HOOK = (
    "trap() {\n"
    '  case "$*" in\n'
    "    *stop_heartbeat_sleeps*) heartbeat_trap_seam \"$@\" ;;\n"
    '    *INT*TERM*) heartbeat_trap_seam "$@" ;;\n'
    '    *) builtin trap "$@" ;;\n'
    "  esac\n"
    "}\n"
    "heartbeat_trap_seam() {\n"
    '  { printf \'%s\\t%s\\n\' "$BASHPID" "${!:-}"; jobs -p; } >"$ASSAY_HEARTBEAT_TRAP_SEAM.tmp"\n'
    '  mv -f -- "$ASSAY_HEARTBEAT_TRAP_SEAM.tmp" "$ASSAY_HEARTBEAT_TRAP_SEAM"\n'
    '  builtin trap "$@"\n'
    '  while [[ ! -e "$ASSAY_HEARTBEAT_TRAP_RELEASE" ]] && kill -0 "$PPID" 2>/dev/null; do\n'
    "    /bin/sleep 0.01\n"
    "  done\n"
    "}\n"
)


@pytest.mark.parametrize("value", ["10", "1501", "zero", "300.5", "", " 300"])
def test_release_gate_rejects_an_unusable_heartbeat_interval(
    tmp_path: Path, value: str
) -> None:
    env = _environment(tmp_path)
    env["ASSAY_RELEASE_GATE_HEARTBEAT_S"] = value
    proc = _run(tmp_path, "tester-unified", env)

    # L1: the interval is one of the combined refusal's problems, so it is reported
    # beside every other unusable setting rather than as a refusal of its own.
    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [
            "ASSAY_RELEASE_GATE_HEARTBEAT_S must be an integer "
            f"from 30 to 1500; got '{value}'"
        ]
    )
    assert not _remote_calls(env)


def test_release_gate_reports_the_heartbeat_interval_with_the_other_settings(
    tmp_path: Path,
) -> None:
    # L1's scenario: an invalid interval AND an unset host in one run used to take two
    # attempts, because the interval died before the host was ever looked at.
    env = _environment(tmp_path, host="")
    env["ASSAY_RELEASE_GATE_HEARTBEAT_S"] = "1501"
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [
            "ASSAY_RELEASE_GATE_HOST is unset; set it to local, v1001, or r1002",
            "ASSAY_RELEASE_GATE_HEARTBEAT_S must be an integer from 30 to 1500; got '1501'",
        ]
    )
    assert not _remote_calls(env)


@pytest.mark.parametrize("value", ["30", "300", "1500"])
def test_release_gate_accepts_the_heartbeat_interval_bounds(
    tmp_path: Path, value: str
) -> None:
    env = _environment(tmp_path)
    env["ASSAY_RELEASE_GATE_HEARTBEAT_S"] = value
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    assert _heartbeat_lines(proc.stderr) == []
    assert len(_remote_calls(env)) == 1


def test_release_gate_heartbeats_while_a_remote_run_is_slow(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    env["BASH_ENV"] = _fast_heartbeat_sleep(tmp_path)
    released = tmp_path / "release-the-fake-run"
    _configure_fake(env, hold_until=str(released), lane_status=1, verdict="FAIL")
    wrapper = Wrapper(tmp_path, env)
    try:
        stderr = wrapper.wait_for(
            # Both conditions, waited for (never a fixed pause): a line proves the
            # heartbeat loop runs, the recorded call proves the fake remote-gate
            # started and therefore names the run.
            lambda text: len(_heartbeat_lines(text)) >= 2 and bool(_remote_calls(env)),
            "printed two heartbeat lines for a started remote run",
        )
        beats = _heartbeat_lines(stderr)
        run_name = _remote_calls(env)[0]
        name = run_name[run_name.index("--name") + 1]
        # Every line names this one run on this one host, however many arrived.
        assert {match.group(1) for match in beats} == {name}
        assert {match.group(2) for match in beats} == {"v1001"}
        # The shape is fixed: run name, host and a whole-seconds elapsed count.
        assert int(beats[0].group(3)) >= 0
        assert int(beats[-1].group(3)) >= int(beats[0].group(3))
        released.write_text("go", encoding="utf-8")
        status = wrapper.reap()
    finally:
        wrapper.kill_leftovers()

    # The heartbeat must not swallow the lane's own exit status.
    assert status == 1, wrapper.stderr()
    assert len(_remote_calls(env)) == 1


def test_release_gate_heartbeat_still_cancels_the_remote_run_on_term(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    env["BASH_ENV"] = _fast_heartbeat_sleep(tmp_path)
    secret_names = (
        "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
    )
    env.update({name: f"secret-{name}" for name in secret_names})
    ready_fifo = tmp_path / "remote-ready.fifo"
    os.mkfifo(ready_fifo)
    # The cancel is held until this test releases it (M2): the wrapper is blocked inside
    # `remote-gate.py cancel`, so a heartbeat that was NOT stopped would keep printing
    # during the hold. Waiting for ten hold ticks (>= 100 ms of holding, event-driven) on
    # a loaded host only holds longer, which can never turn a correct run red.
    cancel_release = tmp_path / "release-the-cancel"
    _configure_fake(
        env,
        signal_mode=True,
        ready_fifo=str(ready_fifo),
        cancel_hold_until=str(cancel_release),
    )
    out_dir = Path(env["REMOTE_GATE_OUT"])
    hold_ticks = out_dir / "remote-cancel-hold-ticks.txt"
    ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
    wrapper = Wrapper(tmp_path, env)
    try:
        wrapper.wait_for(
            lambda text: len(_heartbeat_lines(text)) >= 1, "printed a heartbeat line"
        )
        ready, _, _ = select.select([ready_fd], [], [], 15)
        assert ready, "fake remote-gate did not reach its signal-ready handshake"
        assert os.read(ready_fd, 32) == b"ready\n"
        wrapper.proc.send_signal(signal.SIGTERM)
        wrapper.wait_for(
            lambda _text: (out_dir / "remote-cancel-holding.txt").exists()
            and _hold_ticks_reached(hold_ticks, 10),
            "held the remote cancel for ten ticks",
        )
        # The assertion M2 replaces: `stderr[cancel_at:]` is the whole window in which a
        # heartbeat that was NOT stopped would keep printing -- the hold blocks the
        # wrapper inside `remote-gate.py cancel`, so it cannot print anything itself.
        # The wrapper prints "attempting to cancel" before the call and "requested"
        # only after it returns, so the window starts at the attempt.
        stderr = wrapper.stderr()
        cancel_at = stderr.index("attempting to cancel remote run")
        assert _heartbeat_lines(stderr)[-1].end() <= cancel_at
        assert _heartbeat_lines(stderr[cancel_at:]) == []
        cancel_release.write_text("go", encoding="utf-8")
        status = wrapper.reap()
    finally:
        cancel_release.write_text("go", encoding="utf-8")
        os.close(ready_fd)
        wrapper.kill_leftovers()

    stderr = wrapper.stderr()
    assert status == 143, stderr
    assert (out_dir / "remote-signal.txt").read_text(
        encoding="utf-8"
    ) == str(signal.SIGTERM.value)
    assert (out_dir / "remote-child-finished.txt").exists()
    calls = _remote_calls(env)
    assert [call[0] for call in calls] == ["run", "cancel"]
    assert "remote cancellation was requested" in stderr
    cancel_secrets = json.loads(
        (out_dir / "remote-cancel-env-secrets.json").read_text(encoding="utf-8")
    )
    assert all(cancel_secrets[name] is False for name in secret_names)
    # And still nothing after the cancel once the run has ended.
    assert _heartbeat_lines(stderr[stderr.index("attempting to cancel remote run"):]) == []


def test_release_gate_does_not_signal_the_reaped_client_while_stopping_heartbeat(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    held = tmp_path / "stop-heartbeat-held.txt"
    release = tmp_path / "stop-heartbeat-release.txt"
    signal_target = tmp_path / "reaped-client-signal.txt"
    hook = tmp_path / "stop-heartbeat-hook.sh"
    hook.write_text(
        "publish_event() {\n"
        '  local path="$1" text="$2" side="$1.tmp"\n'
        '  printf "%s\\n" "$text" >"$side"\n'
        '  mv -f -- "$side" "$path"\n'
        "}\n"
        "kill() {\n"
        "  local child_pid\n"
        '  if [[ "${1:-}" == "-s" && "${2:-}" == "TERM" && -f "$ASSAY_REMOTE_CHILD_PID" ]]; then\n'
        '    IFS= read -r child_pid <"$ASSAY_REMOTE_CHILD_PID"\n'
        '    if [[ "${3:-}" == "$child_pid" ]]; then\n'
        '      publish_event "$ASSAY_REAPED_CLIENT_SIGNAL" "$*"\n'
        "      return 0\n"
        "    fi\n"
        "  fi\n"
        '  if [[ "$#" == 1 && ! -e "$ASSAY_STOP_HEARTBEAT_HELD" ]]; then\n'
        '    publish_event "$ASSAY_STOP_HEARTBEAT_HELD" "$1"\n'
        '    while [[ ! -e "$ASSAY_STOP_HEARTBEAT_RELEASE" ]] && /bin/kill -0 "$PPID" 2>/dev/null; do\n'
        '      /bin/sleep 0.01\n'
        "    done\n"
        "  fi\n"
        '  command kill "$@"\n'
        "}\n",
        encoding="utf-8",
    )
    env.update(
        {
            "BASH_ENV": str(hook),
            "ASSAY_STOP_HEARTBEAT_HELD": str(held),
            "ASSAY_STOP_HEARTBEAT_RELEASE": str(release),
            "ASSAY_REMOTE_CHILD_PID": str(Path(env["REMOTE_GATE_OUT"]) / "remote-child.pid"),
            "ASSAY_REAPED_CLIENT_SIGNAL": str(signal_target),
        }
    )
    child_pid_path = Path(env["REMOTE_GATE_OUT"]) / "remote-child.pid"
    wrapper = Wrapper(tmp_path, env)
    try:
        _wait_until(
            lambda: held.exists() and child_pid_path.exists(),
            "the wrapper to hold inside stop_heartbeat after the remote wait",
        )
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        wrapper.proc.send_signal(signal.SIGTERM)
        _publish_text(release, "go")
        wrapper.wait_for(
            lambda text: "attempting to cancel remote run" in text,
            "to request cancellation after the remote client was reaped",
        )
        status = wrapper.reap()
    finally:
        _publish_text(release, "go")
        wrapper.kill_leftovers()

    stderr = wrapper.stderr()
    assert status == 143, stderr
    assert not signal_target.exists(), (
        f"the reaped remote client {child_pid} was signalled: "
        f"{signal_target.read_text(encoding='utf-8') if signal_target.exists() else ''}"
    )
    assert [call[0] for call in _remote_calls(env)] == ["run", "cancel"]
    assert "remote cancellation was requested" in stderr


def _trap_seam_lines(seam: Path) -> tuple[int, int, list[int]]:
    """The hook's published seam: (heartbeat pid, inherited `$!`, job table).

    The hook writes a side name and renames it into place, so a file a reader can
    open holds every line; a reader that never gets a whole file is still waited
    for by the caller's ceiling, never decided by a fixed pause.
    """
    lines = seam.read_text(encoding="utf-8").splitlines()
    heartbeat_text, inherited_text = lines[0].split("\t")
    return int(heartbeat_text), int(inherited_text), [int(line) for line in lines[1:]]


def test_release_gate_heartbeat_trap_never_names_a_job_it_does_not_own(
    tmp_path: Path,
) -> None:
    # Finding 3: the trap took `${!:-}` as a kill target. A subshell INHERITS `$!`
    # from its parent -- at trap-install time the wrapper's `remote-gate.py` child,
    # which it forked and recorded (`remote_gate_pid`) -- so a TERM before the first
    # `sleep ... &` used to signal the run the trap exists to protect; and after a
    # reaped `wait`, `$!` names a freed and possibly reused pid instead. The trap now
    # takes its targets from the subshell's own job table, which the hook records.
    env = _environment(tmp_path)
    released = tmp_path / "release-the-fake-run"
    seam = tmp_path / "heartbeat-trap-seam.txt"
    seam_release = tmp_path / "release-the-heartbeat-trap"
    hook = tmp_path / "heartbeat-trap-hook.sh"
    hook.write_text(HEARTBEAT_TRAP_HOOK, encoding="utf-8")
    env.update(
        {
            "BASH_ENV": str(hook),
            "ASSAY_HEARTBEAT_TRAP_SEAM": str(seam),
            "ASSAY_HEARTBEAT_TRAP_RELEASE": str(seam_release),
        }
    )
    # A fake run that stays attached and alive until this test releases it, so nothing
    # is decided by a duration.
    _configure_fake(env, hold_until=str(released))
    out_dir = Path(env["REMOTE_GATE_OUT"])
    child_pid_path = out_dir / "remote-child.pid"
    wrapper = Wrapper(tmp_path, env)
    try:
        _wait_until(
            lambda: seam.exists() and child_pid_path.exists(),
            "the heartbeat's trap seam and the fake remote-gate's recorded pid",
        )
        heartbeat_pid, inherited_pid, job_table = _trap_seam_lines(seam)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        # The pre-first-fork window the old code got wrong: the inherited `$!` really
        # is the remote-gate child here, and the job table the trap reads is empty.
        assert inherited_pid == child_pid, (
            "the seam must hold the window in which the inherited $! is the child"
        )
        assert job_table == [], (
            "before the loop's first fork the trap's job table must be empty"
        )
        assert _pid_running(child_pid), "the fake remote-gate must still be running"
        # The trap is installed by the time the seam is published, so this TERM
        # exercises it, not the deferred or uninstalled path.
        os.kill(heartbeat_pid, signal.SIGTERM)
        seam_release.write_text("go", encoding="utf-8")
        # A zombie has exited (only its parent has not reaped it yet), so this is the
        # exit event and not a race against `stop_heartbeat`.
        _wait_until(lambda: not _pid_running(heartbeat_pid), "the heartbeat subshell to exit")
        assert _pid_running(child_pid), (
            "the heartbeat's trap signal must not reach the remote-gate child"
        )
        released.write_text("go", encoding="utf-8")
        status = wrapper.reap()
    finally:
        released.write_text("go", encoding="utf-8")
        seam_release.write_text("go", encoding="utf-8")
        wrapper.kill_leftovers()

    # The run that the heartbeat was watching still ends with its own verdict.
    assert status == 0, wrapper.stderr()


def test_release_gate_heartbeat_trap_kills_the_sleep_it_owns(
    tmp_path: Path,
) -> None:
    # Decision 3's second direction: the job-table fix must not turn the trap into a
    # no-op. The wrapper is TERMed while its heartbeat waits on a real `sleep 300`, and
    # the test then requires the very process whose pid it captured beforehand to be
    # gone. It captures the pid while the sleep is still a descendant of this wrapper,
    # so a trap that killed nothing is caught no matter what the kernel does with the
    # orphan afterwards (a reparented survivor would be found by ancestry, not by this
    # exact pid, which is the false-pass this capture avoids).
    env = _environment(tmp_path)
    released = tmp_path / "release-the-fake-run"
    _configure_fake(env, hold_until=str(released))
    child_pid_path = Path(env["REMOTE_GATE_OUT"]) / "remote-child.pid"
    wrapper = Wrapper(tmp_path, env)
    try:
        sleep_pid, heartbeat_pid = _wait_for_value(
            # Both events, never a pause: the fake records its pid only after it has
            # written the run's meta.json, so this also proves the wrapper's cancel
            # path can find the run it was watching.
            lambda: child_pid_path.exists()
            and _heartbeat_sleep(wrapper.proc.pid, 300),
            "the heartbeat's live sleep below the wrapper and the fake run's pid",
        )
        assert _pid_running(sleep_pid) and _pid_running(heartbeat_pid)
        # The trap is installed before the loop's first fork, so this TERM exercises it.
        wrapper.proc.send_signal(signal.SIGTERM)
        _wait_until(
            lambda: not _pid_running(sleep_pid),
            "the heartbeat's trap to kill the sleep it owns",
        )
        status = wrapper.reap()
    finally:
        released.write_text("go", encoding="utf-8")
        wrapper.kill_leftovers()

    # The trap did its job without changing the cancellation's exit status.
    assert status == 143, wrapper.stderr()
    assert "attempting to cancel remote run" in wrapper.stderr()


def _run(tmp_path: Path, lane: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), lane],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_cmru_release_steps_use_the_delegating_wrapper() -> None:
    commands = tomllib.loads(CMRU_FILE.read_text(encoding="utf-8"))["steps"]["run-tests"]["commands"]

    assert commands[0]["argv"] == ["bash", "tools/release-gate.sh", "tester-unified"]
    assert commands[1]["argv"] == [
        "bash", "tools/release-gate.sh", "self-qualification-provisional"
    ]
    assert commands[0]["cwd"] == commands[1]["cwd"] == "."


def test_pre_merge_tooling_lane_binds_the_release_attempt_failure_groups() -> None:
    lane = tomllib.loads(RUN_GATE_FILE.read_text(encoding="utf-8"))["lanes"][
        "tooling-tests"
    ]
    command = lane["argv"][2]
    for test_path in (
        "gate/tests/test_b105_report_check.py",
        "gate/tests/test_release_gate.py",
        "gate/tests/test_distribution_gate.py",
        "gate/tests/test_self_qualification_container.py",
        # B536: the release-wrapper refusal/prior-marker assertions, now runnable
        # in the container lane (its main-checkout fixture skips with a named
        # reason instead of dying on an unwritable shared .worktrees directory).
        "gate/tests/test_self_lane.py",
        "tests/core/test_docs_examples_and_vocabulary.py",
        # B533: cross-cutting contracts the 12.2.0 release attempt found red.
        "tests/core/test_termination.py",
        "tests/core/test_termination_alignment.py",
        "tests/core/test_canary_receipt.py",
        "tests/core/test_trust_boundary.py",
        "tests/core/test_dataclass_contract.py",
        "tests/core/test_b105_source_coverage_controls.py",
        # Test-file layout rule: a misplaced new test file was found only by the
        # host R1 lane (Rust R-1 merged-branch qualification).
        "tests/core/test_import_contracts.py",
        "tests/parsers/coverage/test_coverage_rust_lcov_normalization.py",
    ):
        assert test_path in command

    workflow = " ".join(WORKFLOW_FILE.read_text(encoding="utf-8").split())
    assert "run-gate tooling-tests" in workflow
    # B534: judge and analysis sources need their R1 coverage lane before merge.
    assert "## Pre-merge coverage gate for judge and analysis sources (B534)" in workflow
    assert "rg -q '^assay/src/'" in workflow
    assert "bash tools/release-gate.sh self-qualification-provisional" in workflow
    assert "rg -q '^assay/analysis/src/'" in workflow
    assert "bash tools/release-gate.sh tester-unified" in workflow
    for trigger in (
        "assay/tools/**",
        "assay/gate/**",
        "assay/assay.toml",
        "assay/README.md",
        "assay/docs/DESIGN-GUIDE.md",
        "assay/docs/CONSUMERS.md",
    ):
        assert trigger in workflow


def _hours(budget: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)h", budget)
    assert match, f"budget {budget!r} is not a whole-hour value"
    return int(match.group(1))


def test_native_assay_r2_lane_matches_b535_scope_without_changing_b105_lanes() -> None:
    run_gate = tomllib.loads(RUN_GATE_FILE.read_text(encoding="utf-8"))["lanes"]
    assay_lanes = tomllib.loads(ASSAY_FILE.read_text(encoding="utf-8"))["lanes"]
    parsed_lanes = load_lane_file(ASSAY_FILE)

    assert list(parsed_lanes.lanes) == [
        "tester-unified",
        "self-qualification",
        "assay-r2",
        "self-qualification-preflight",
        "self-qualification-provisional",
        "analysis",
        "analysis-r2",
    ]
    gate_lane = run_gate["assay-r2"]
    assert gate_lane["kind"] == "assay"
    assert gate_lane["assay_lane"] == "assay-r2"
    assert gate_lane["environment"] == "tester-unified"
    assert gate_lane["jobs"] == "auto"
    assert gate_lane["resources"] == {"memory": "3g", "pids": 8192, "cpus": "3"}

    native_lane = assay_lanes["assay-r2"]
    parsed_native_lane = parsed_lanes.lane("assay-r2")
    full_lane = assay_lanes["self-qualification"]
    provisional_lane = assay_lanes["self-qualification-provisional"]
    assert native_lane["scope"] == "S1"
    assert native_lane["rigor"] == ["R0", "R1", "R2"]
    assert parsed_native_lane.scope == "S1"
    assert parsed_native_lane.rigor == ("R0", "R1", "R2")
    assert native_lane["judge"]["source_roots"] == ["src/assay"]
    assert native_lane["judge"]["mode"] == "whole_target"
    assert native_lane["judge"]["targets"] == full_lane["judge"]["targets"]
    assert native_lane["judge"]["fail_under"] == full_lane["judge"]["fail_under"] == 100.0
    assert native_lane["judge"]["require_branch"] is True
    assert native_lane["isolation"] == full_lane["isolation"]
    assert parsed_native_lane.isolation == parsed_lanes.lane("self-qualification").isolation
    assert native_lane["argv"][:-1] == full_lane["argv"][:-1]
    # The R1 floor and the R2 target set cover every shipped module: a new
    # src/assay file must be added to the lane, not silently left out.
    shipped = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src" / "assay").rglob("*.py")
    )
    assert native_lane["judge"]["targets"] == shipped
    # Same mutation operators and cap as the full lane; only jobs and fail-fast
    # (and no canary: R3 stays with the qualification lanes) differ.
    full_mutation = full_lane["judge"]["mutation"]
    native_mutation = native_lane["judge"]["mutation"]
    assert native_mutation["operators"] == full_mutation["operators"]
    assert native_mutation["max_mutants"] == full_mutation["max_mutants"]
    assert native_mutation["liveness"] is True
    assert "canary" not in native_lane["judge"]
    assert native_lane["allow_argv_append"] is False
    assert native_lane["env_passthrough"] == ["PATH"]
    assert native_lane["infrastructure"] == full_lane["infrastructure"]
    artifact = native_lane["judge"]["coverage"]["artifact"]
    assert artifact == ".assay/coverage-assay-r2.json"
    assert artifact in native_lane["argv"][-1]
    assert artifact not in {
        lane["judge"]["coverage"]["artifact"]
        for name, lane in assay_lanes.items()
        if name != "assay-r2" and "coverage" in lane.get("judge", {})
    }
    # The campaign must bind one immutable tree and use the cgroup observer;
    # the Assay per-attempt bound must fit inside the Run-Gate outer bound.
    assert gate_lane["clean_tree"] is True
    assert gate_lane["resource_observer"] == "cgprofile"
    assert _hours(gate_lane["budget"]) >= _hours(native_lane["budget"]) > 0
    assert native_lane["judge"]["mutation"]["candidate_fail_fast"] is True
    assert native_lane["judge"]["mutation"]["budget_per_candidate"] == "auto"
    assert parsed_native_lane.judge is not None
    assert parsed_native_lane.judge.mutation is not None
    assert parsed_native_lane.judge.mutation.jobs_max == 3
    assert parsed_native_lane.judge.mutation.candidate_fail_fast is True

    assert full_lane["rigor"] == ["R0", "R1", "R2", "R3"]
    assert provisional_lane["rigor"] == ["R0", "R1", "R3"]
    for lane_name in ("self-qualification", "self-qualification-provisional"):
        assert run_gate[lane_name]["kind"] == "command"
        assert run_gate[lane_name]["argv"] == [
            "bash",
            "{worktree}/assay/tools/self-qualification-container.sh",
            "{worktree}",
            lane_name,
        ]

    consumers = " ".join(CONSUMERS_FILE.read_text(encoding="utf-8").split())
    assert "T3 changed-line R2 campaign (B535)" in consumers
    assert "run-gate assay-r2" in consumers
    assert "--only-candidates" in consumers
    assert "--merge-shards 2" in consumers


def test_release_gate_requires_the_host_selector(tmp_path: Path) -> None:
    env = _environment(tmp_path, host="")
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2
    assert proc.stderr == (
        "assay release-gate: the remote release-gate environment is not usable:\n"
        "  - ASSAY_RELEASE_GATE_HOST is unset; set it to local, v1001, or r1002\n"
    )
    assert not _remote_calls(env)


@pytest.mark.parametrize("home", [None, ""])
def test_remote_release_gate_reports_an_unset_or_empty_home_with_the_other_settings(
    tmp_path: Path, home: str | None
) -> None:
    env = _environment(tmp_path)
    # nit2: HOME is also what remote-gate.py expands its own DEFAULT_HOSTS with
    # (os.path.expanduser('~/.config/remote-gate/hosts.json'), remote-gate.py:38), so
    # removing it really does remove that source too and the refusal names it. Only
    # the controller config drops out silently here (it cannot be read without HOME);
    # an empty HOME resolves the default to /.config/remote-gate/hosts.json, which
    # does not exist either, so both spellings are the same refusal.
    if home is None:
        env.pop("HOME")
    else:
        env["HOME"] = home
    env.pop("REMOTE_GATE_OUT", None)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [
            "host table from remote-gate.py DEFAULT_HOSTS is missing: "
            "/.config/remote-gate/hosts.json",
            "HOME is unset; remote-gate needs it for the host table and ~/.ssh",
        ]
    )
    assert not _remote_calls(env)


def test_release_gate_reports_a_defaults_temp_file_failure_as_its_own_reason(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    # Finding 2: a temporary file that cannot be created is not a default that
    # resolved to nothing. With no HOME the controller configuration is not read at
    # all, so the only `mktemp` this run can reach is the one the module-defaults
    # reader uses; before the fix that failure was reported as the host table
    # "resolved to an empty path", naming DEFAULT_HOSTS for a file nothing read.
    env.pop("HOME")
    env["TMPDIR"] = str(tmp_path / "no-such-tmpdir")
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2, proc.stderr
    assert proc.stderr == _combined_refusal(
        [
            "cannot read remote-gate.py's default host table: cannot create a "
            "temporary file for the remote-gate.py defaults import",
            "HOME is unset; remote-gate needs it for the host table and ~/.ssh",
        ]
    )
    assert not _remote_calls(env)


def test_release_gate_judges_the_missing_remote_gate_file_before_the_settings(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path, host="")
    # Finding 3: CONSUMERS.md says which check runs first. The missing-script check
    # (tools/release-gate.sh:55-57) is before the settings resolution, so a fresh
    # clone that also has no host hears only the exit-3 file refusal and a SECOND
    # run is needed for the settings -- the two-attempt cost B579 removed for the
    # settings themselves. Running the real script (an absolute path) in a directory
    # whose `../scripts/netcup/runner` does not exist reproduces that clone without
    # copying the script.
    outside = tmp_path / "outside"
    outside.mkdir()
    assert not (outside / ".." / "scripts" / "netcup" / "runner" / "remote-gate.py").exists()
    proc = subprocess.run(
        ["/bin/bash", str(SCRIPT), "tester-unified"],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert proc.returncode == 3, proc.stderr
    assert proc.stderr == (
        "assay release-gate: remote-gate.py is missing at "
        "../scripts/netcup/runner/remote-gate.py\n"
    )
    assert COMBINED_REFUSAL_HEADER not in proc.stderr
    assert not _remote_calls(env)


def test_release_gate_reports_every_unusable_setting_in_one_refusal(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path, host="")
    # Nothing may resolve: no explicit setting, no controller config (HOME is
    # unset, so controller_init cannot even run), and module defaults that do
    # not exist. Today the wrapper refuses one setting per attempt (B579), so
    # this test pins the single combined refusal.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    missing_hosts = tmp_path / "missing-hosts.json"
    _configure_fake(
        env,
        module_defaults={
            "hosts": str(missing_hosts),
            "out": str(blocker / "remote-gates"),
        },
    )
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    env.pop("HOME")
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [
            "ASSAY_RELEASE_GATE_HOST is unset; set it to local, v1001, or r1002",
            f"host table from remote-gate.py DEFAULT_HOSTS is missing: {missing_hosts}",
            "output directory from remote-gate.py DEFAULT_OUT cannot be created: "
            f"{blocker} is not a directory",
            "HOME is unset; remote-gate needs it for the host table and ~/.ssh",
        ]
    )
    # One refusal, no work: no remote run is launched and the wrapper never waits.
    assert _remote_calls(env) == []


def _controller_home(
    tmp_path: Path,
    *,
    hosts_name: str = "controller-hosts.json",
    remote_hosts_name: str | None = None,
    out_name: str = "controller-gates",
    include_out: bool = True,
) -> Path:
    """A controller home the way scripts/controller/lib.sh reads one.

    `controller.local.env` keeps the RELATIVE spellings (as a real controller
    file may), so the wrapper has to make them absolute against CONTROLLER_HOME.
    With `include_out=False` the file sets no CONTROLLER_REMOTE_OUT at all, which
    is the production cockpit's shape (M3).
    """
    controller = tmp_path / "controller-home"
    controller.mkdir()
    (controller / out_name).mkdir()
    (controller / hosts_name).write_text("{}", encoding="utf-8")
    lines = [f"CONTROLLER_HOSTS_FILE={hosts_name}"]
    if include_out:
        lines.append(f"CONTROLLER_REMOTE_OUT={out_name}")
    if remote_hosts_name is not None:
        (controller / remote_hosts_name).write_text("{}", encoding="utf-8")
        lines.append(f"CONTROLLER_REMOTE_HOSTS_FILE={remote_hosts_name}")
    (controller / "controller.local.env").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return controller


BROKEN_CONTROLLER_VARIABLE = "ASSAY_TEST_CONTROLLER_FILE_NEEDS_THIS"
BROKEN_CONTROLLER_REASON = "the controller file needs an unset variable"


def _broken_controller_home(tmp_path: Path) -> Path:
    """A controller file that aborts the reader while its values would be usable.

    `: "${VAR:?message}"` is a fatal `set -u` expansion, so the reader exits
    non-zero with the message on stderr even under `set -uo pipefail` (no `-e`).
    Its last stderr line is the reason the refusal must quote (M1).
    """
    controller = tmp_path / "broken-controller-home"
    controller.mkdir()
    (controller / "controller-hosts.json").write_text("{}", encoding="utf-8")
    (controller / "controller.local.env").write_text(
        "CONTROLLER_HOSTS_FILE=controller-hosts.json\n"
        f': "${{{BROKEN_CONTROLLER_VARIABLE}:?{BROKEN_CONTROLLER_REASON}}}"\n',
        encoding="utf-8",
    )
    return controller


def test_release_gate_explicit_remote_gate_settings_win_over_controller_config(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    controller = _controller_home(tmp_path)
    env["CONTROLLER_HOME"] = str(controller)
    explicit_hosts = tmp_path / "explicit-hosts.json"
    explicit_hosts.write_text("{}", encoding="utf-8")
    env["REMOTE_GATE_HOSTS"] = str(explicit_hosts)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    # The remote-gate process is the only witness that matters: it reports the
    # REMOTE_GATE_HOSTS and REMOTE_GATE_OUT the wrapper actually handed it.
    selection = _remote_selection_at(Path(env["REMOTE_GATE_OUT"]))
    assert selection == {
        "REMOTE_GATE_HOSTS": str(explicit_hosts),
        "REMOTE_GATE_OUT": env["REMOTE_GATE_OUT"],
    }
    assert not (controller / "controller-gates" / "remote-calls.jsonl").exists()


def test_release_gate_refuses_an_unusable_explicit_setting_instead_of_falling_through(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    env["CONTROLLER_HOME"] = str(_controller_home(tmp_path))
    missing = tmp_path / "missing-hosts.json"
    env["REMOTE_GATE_HOSTS"] = str(missing)
    proc = _run(tmp_path, "tester-unified", env)

    # A value the caller stated is a fact, not a default: it is refused rather
    # than silently replaced by the controller's own host table. L2: the refusal
    # names the setting that supplied it.
    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [f"host table from REMOTE_GATE_HOSTS is missing: {missing}"]
    )
    assert _remote_calls(env) == []


def test_release_gate_refuses_an_unusable_explicit_out_instead_of_falling_through(
    tmp_path: Path,
) -> None:
    # Finding 5a: only the HOSTS half of "an explicit setting is a fact" was pinned.
    # Here the controller configuration supplies a usable output directory and the
    # operator's own REMOTE_GATE_OUT is a regular file: falling through to the
    # controller value would silently ignore the value B579's operator hand-set.
    env = _environment(tmp_path)
    controller = _controller_home(tmp_path)
    env["CONTROLLER_HOME"] = str(controller)
    unusable_out = tmp_path / "explicit-out-is-a-file"
    unusable_out.write_text("not a directory", encoding="utf-8")
    env["REMOTE_GATE_OUT"] = str(unusable_out)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2, proc.stderr
    assert proc.stderr == _combined_refusal(
        [
            "output directory from REMOTE_GATE_OUT exists but is not a directory: "
            f"{unusable_out}"
        ]
    )
    # Neither the controller's output directory nor the module default may run work.
    assert not (controller / "controller-gates" / "remote-calls.jsonl").exists()
    assert _remote_calls(env) == []


@pytest.mark.parametrize("setting", ["REMOTE_GATE_HOSTS", "REMOTE_GATE_OUT"])
def test_release_gate_refuses_a_set_but_empty_explicit_setting(
    tmp_path: Path, setting: str
) -> None:
    # Finding 5b: `[[ -v ]]` deliberately makes an empty stated value a refusal
    # ("resolved to an empty path") instead of the pre-change dedicated die, and
    # `${VAR:-}` semantics would silently treat "" as unset and fall through.
    env = _environment(tmp_path)
    env["CONTROLLER_HOME"] = str(_controller_home(tmp_path))
    env[setting] = ""
    proc = _run(tmp_path, "tester-unified", env)

    label = "host table" if setting == "REMOTE_GATE_HOSTS" else "output directory"
    assert proc.returncode == 2, proc.stderr
    assert proc.stderr == _combined_refusal(
        [f"{label} from {setting} resolved to an empty path"]
    )
    assert _remote_calls(env) == []


def test_release_gate_reports_a_module_default_that_could_not_be_imported(
    tmp_path: Path,
) -> None:
    # Finding 5c: nothing made the shim fail its import, so "an import that failed
    # is not an empty default" was an untested claim. An import failure must name
    # the import, never report the table as resolved to an empty path.
    env = _environment(tmp_path)
    env["CONTROLLER_HOME"] = str(tmp_path / "no-controller")
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    _configure_fake(env, module_defaults={"import_failure": True})
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2, proc.stderr
    assert proc.stderr == _combined_refusal(
        [
            "cannot read remote-gate.py's default host table: "
            "remote-gate.py cannot be imported",
            "cannot read remote-gate.py's default output directory: "
            "remote-gate.py cannot be imported",
        ]
    )
    assert _remote_calls(env) == []


def test_release_gate_names_the_exit_status_of_a_silent_defaults_import(
    tmp_path: Path,
) -> None:
    # Finding 1 of the round-2 review: the failure branch quoted the import's LAST
    # stderr line, so an import that died with no output at all (SIGKILL on the host,
    # cgroup OOM-kill, a bare SystemExit) left that variable empty. Both consumers then
    # took their success-side branch over the empty string and told the operator
    # "DEFAULT_HOSTS resolved to an empty path", the false-source message the previous
    # round removed, through its sibling door. The reason now names the status.
    env = _environment(tmp_path)
    env["CONTROLLER_HOME"] = str(tmp_path / "no-controller")
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    _configure_fake(env, module_defaults={"import_failure_silent": True})
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2, proc.stderr
    assert proc.stderr == _combined_refusal(
        [
            "cannot read remote-gate.py's default host table: "
            "remote-gate.py defaults import exited 3",
            "cannot read remote-gate.py's default output directory: "
            "remote-gate.py defaults import exited 3",
        ]
    )
    assert "resolved to an empty path" not in proc.stderr
    assert _remote_calls(env) == []


def _probe_controller_home(tmp_path: Path) -> Path:
    """A controller file whose bare probe fails but whose values are usable (finding 4).

    The reader must run `set -uo pipefail` WITHOUT `-e` (tools/release-gate.sh:109,
    matching scripts/controller/remote-gate.sh:24): a `command -v` for a tool this
    cockpit lacks, or a failing `cd`, exits non-zero without being fatal, and the
    values the file already defined stay usable. `_broken_controller_home` above
    pins the FATAL half (a `set -u` expansion error, which kills the reader under
    `-u` alone); this file pins the half that only `-e` would kill.
    """
    controller = tmp_path / "probe-controller-home"
    controller.mkdir()
    (controller / "controller-hosts.json").write_text("{}", encoding="utf-8")
    (controller / "controller.local.env").write_text(
        "CONTROLLER_HOSTS_FILE=controller-hosts.json\n"
        # A bare probe this host does not satisfy: exit 1, not fatal.
        "command -v assay-absent-probe-tool >/dev/null\n"
        # ... and a failing `cd`, the other shape the comment names.
        "cd -- /nonexistent-assay-probe-dir 2>/dev/null\n",
        encoding="utf-8",
    )
    return controller


def test_release_gate_reads_a_controller_file_whose_bare_probe_fails(
    tmp_path: Path,
) -> None:
    # Finding 4: the reader was changed to `set -uo pipefail` (no `-e`) so a
    # controller file with a failing-but-non-fatal line is not reported as
    # unreadable. Neither earlier test covers that half: `_broken_controller_home`
    # fails fatally under `-u` alone. Re-adding `-e` to the reader turns this run
    # into a "cannot read the controller configuration" refusal.
    env = _environment(tmp_path)
    controller = _probe_controller_home(tmp_path)
    env["CONTROLLER_HOME"] = str(controller)
    env.pop("REMOTE_GATE_HOSTS", None)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    assert "cannot read the controller configuration" not in proc.stderr
    # The child is the witness that the values survived the failing probe, and that
    # the controller's host table is the one the wrapper handed it (remote-gate.sh:24
    # parity is the comment's claim).
    selection = _remote_selection_at(Path(env["REMOTE_GATE_OUT"]))
    assert selection["REMOTE_GATE_HOSTS"] == str(controller / "controller-hosts.json")
    assert len(_remote_calls(env)) == 1


def test_release_gate_reads_the_controller_config_only_when_a_setting_is_missing(
    tmp_path: Path,
) -> None:
    # M1: a controller file that cannot be read must not block a fully explicit
    # REMOTE_GATE_HOSTS/REMOTE_GATE_OUT pair. The pair outranks that file, and the
    # controller file is exactly what the operator may be working around.
    env = _environment(tmp_path)
    env["CONTROLLER_HOME"] = str(_broken_controller_home(tmp_path))
    explicit_hosts = tmp_path / "explicit-hosts.json"
    explicit_hosts.write_text("{}", encoding="utf-8")
    env["REMOTE_GATE_HOSTS"] = str(explicit_hosts)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    assert "cannot read the controller configuration" not in proc.stderr
    assert _remote_selection_at(Path(env["REMOTE_GATE_OUT"])) == {
        "REMOTE_GATE_HOSTS": str(explicit_hosts),
        "REMOTE_GATE_OUT": env["REMOTE_GATE_OUT"],
    }
    assert len(_remote_calls(env)) == 1


def test_release_gate_refuses_an_unreadable_controller_config_with_its_reason(
    tmp_path: Path,
) -> None:
    # M1: with one setting missing the controller file IS consulted, and a file that
    # aborts the reader is refused with the reader's own last stderr line: the earlier
    # `2>/dev/null` discarded the only thing that explains the refusal.
    env = _environment(tmp_path)
    controller = _broken_controller_home(tmp_path)
    env["CONTROLLER_HOME"] = str(controller)
    env.pop("REMOTE_GATE_HOSTS", None)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 2
    assert proc.stderr == _combined_refusal(
        [
            "cannot read the controller configuration "
            "(../scripts/controller/lib.sh controller_init: "
            f"{controller / 'controller.local.env'}: line 2: "
            f"{BROKEN_CONTROLLER_VARIABLE}: {BROKEN_CONTROLLER_REASON})"
        ]
    )
    assert _remote_calls(env) == []


def test_release_gate_resolves_the_controller_default_output_directory(
    tmp_path: Path,
) -> None:
    # M3: the production shape -- the controller file sets its host table and NO
    # CONTROLLER_REMOTE_OUT, so the output directory is $CONTROLLER_HOME/remote-gates.
    # This is the value the B579 operator hand-set, and the branch this cockpit takes.
    env = _environment(tmp_path)
    controller = _controller_home(tmp_path, out_name="remote-gates", include_out=False)
    env["CONTROLLER_HOME"] = str(controller)
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    # Module defaults that cannot work, so a PASS proves the controller value won.
    _configure_fake(
        env,
        module_defaults={
            "hosts": str(tmp_path / "unusable-module-hosts.json"),
            "out": str(tmp_path / "unusable-module-out"),
        },
    )
    controller_out = controller / "remote-gates"
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    assert len(_remote_calls_at(controller_out)) == 1
    assert _remote_selection_at(controller_out) == {
        "REMOTE_GATE_HOSTS": str(controller / "controller-hosts.json"),
        "REMOTE_GATE_OUT": str(controller_out),
    }
    assert not _remote_calls(env)


def test_release_gate_resolves_settings_from_the_controller_config(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    controller = _controller_home(tmp_path, remote_hosts_name="controller-remote-hosts.json")
    env["CONTROLLER_HOME"] = str(controller)
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    # Module defaults that cannot work, so a PASS proves the controller config won.
    _configure_fake(
        env,
        module_defaults={
            "hosts": str(tmp_path / "unusable-module-hosts.json"),
            "out": str(tmp_path / "unusable-module-out"),
        },
    )
    proc = _run(tmp_path, "tester-unified", env)
    controller_out = controller / "controller-gates"

    assert proc.returncode == 0, proc.stderr
    assert len(_remote_calls_at(controller_out)) == 1
    # CONTROLLER_REMOTE_HOSTS_FILE wins over CONTROLLER_HOSTS_FILE, exactly as
    # scripts/controller/remote-gate.sh:102-103 does; the relative spelling is
    # made absolute against CONTROLLER_HOME.
    assert _remote_selection_at(controller_out) == {
        "REMOTE_GATE_HOSTS": str(controller / "controller-remote-hosts.json"),
        "REMOTE_GATE_OUT": str(controller_out),
    }
    assert not _remote_calls(env)


def test_release_gate_falls_back_to_remote_gate_module_defaults_last(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    # A controller home that does not exist configures nothing (its default
    # hosts.local.json spelling is absent), so the module defaults must apply.
    env["CONTROLLER_HOME"] = str(tmp_path / "no-controller")
    env.pop("REMOTE_GATE_HOSTS", None)
    env.pop("REMOTE_GATE_OUT")
    module_out = tmp_path / "module-default-gates"
    module_out.mkdir()
    module_hosts = tmp_path / "module-default-hosts.json"
    module_hosts.write_text("{}", encoding="utf-8")
    _configure_fake(
        env, module_defaults={"hosts": str(module_hosts), "out": str(module_out)}
    )
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    assert len(_remote_calls_at(module_out)) == 1
    assert _remote_selection_at(module_out) == {
        "REMOTE_GATE_HOSTS": str(module_hosts),
        "REMOTE_GATE_OUT": str(module_out),
    }
    assert not _remote_calls(env)


@pytest.mark.parametrize(
    ("verdict", "lane_status", "expected_status"),
    [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
)
def test_release_gate_checks_remote_verdict_and_returns_lane_status(
    tmp_path: Path, verdict: str, lane_status: int, expected_status: int
) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, verdict=verdict, lane_status=lane_status)
    proc = _run(tmp_path, "self-qualification-provisional", env)

    assert proc.returncode == expected_status, proc.stderr
    assert (f"refusing self-qualification-provisional verdict {verdict}" in proc.stderr) is (
        verdict != "PASS"
    )
    args = _remote_calls(env)[0]
    assert args[:4] == [
        "run",
        "v1001",
        str(REPO_ROOT),
        "self-qualification-provisional",
    ]
    assert args[args.index("--project-dir") + 1] == "assay"
    assert args[args.index("--repo") + 1] == "vbpub"
    name = args[args.index("--name") + 1]
    commit = env["CMRU_RELEASE_GATE_COMMIT"]
    assert name.startswith(f"assay-rel-{commit[:12]}-self-qualification-provisional-")
    assert re.fullmatch(r"assay-rel-[0-9a-f]{12}-self-qualification-provisional-[0-9a-z]{7}", name)
    assert len(name) <= 61
    assert "--detach" not in args


def test_release_gate_scrubs_publisher_and_gh_credentials_from_remote_gate(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    credentials = {
        "GITHUB_PUSH_PAT": "publisher-secret",
        "GITHUB_TOKEN": "github-token-secret",
        "CMRU_GIT_AUTH_TOKEN": "git-auth-secret",
        "GH_TOKEN": "gh-token-secret",
        "GH_ENTERPRISE_TOKEN": "gh-enterprise-secret",
    }
    env.update(credentials)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    observed = json.loads(
        (Path(env["REMOTE_GATE_OUT"]) / "remote-env-secrets.json").read_text(
            encoding="utf-8"
        )
    )
    assert observed == {key: False for key in credentials}
    observed_keys = set(
        json.loads(
            (Path(env["REMOTE_GATE_OUT"]) / "remote-env-keys.json").read_text(
                encoding="utf-8"
            )
        )
    )
    # Python's locale coercion may add LC_CTYPE when starting with env -i.
    # REMOTE_GATE_HOSTS is always present because the wrapper resolves a host
    # table even when the caller names none (B579).
    allowed_keys = {"PATH", "HOME", "REMOTE_GATE_OUT", "REMOTE_GATE_HOSTS", "LC_CTYPE"}
    if env.get("SSH_AUTH_SOCK"):
        allowed_keys.add("SSH_AUTH_SOCK")
    assert observed_keys == allowed_keys


def test_release_gate_refuses_a_remote_sha_mismatch(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, meta_sha="f" * 40)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert "remote-gate meta.json SHA mismatch" in proc.stderr
    assert "expected " + env["CMRU_RELEASE_GATE_COMMIT"] in proc.stderr


def test_release_gate_refuses_a_commit_that_does_not_match_head(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    actual_head = env["CMRU_RELEASE_GATE_COMMIT"]
    expected = "f" * 40 if not actual_head.startswith("f") else "e" * 40
    env["CMRU_RELEASE_GATE_COMMIT"] = expected

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert expected in proc.stderr
    assert actual_head in proc.stderr
    assert "does not match worktree HEAD" in proc.stderr
    assert not _remote_calls(env)


def test_release_gate_rejects_remote_vs_lane_exit_mismatch(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, remote_status=0, lane_status=1)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert "remote-gate exit status 0 differs from result.json lane exit code 1" in proc.stderr


def test_release_gate_propagates_remote_failure_without_result_files(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, remote_status=75, write_meta=False, write_result=False)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 75
    assert "remote-gate produced no valid result" in proc.stderr
    assert len(_remote_calls(env)) == 1
    assert not (Path(env["REMOTE_GATE_OUT"]) / "remote-cancelled.txt").exists()


def test_release_gate_cancels_unattended_remote_job_after_failed_polls(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    secret_names = (
        "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
    )
    env.update({name: f"secret-{name}" for name in secret_names})
    _configure_fake(env, remote_status=3, write_meta=True, write_result=False)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    calls = _remote_calls(env)
    assert len(calls) == 2
    assert calls[0][0] == "run"
    assert calls[1][0] == "cancel"
    assert calls[1][1] == calls[0][calls[0].index("--name") + 1]
    assert "attempting to cancel unattended remote run" in proc.stderr
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-cancelled.txt").exists()
    # The cancel call of the unattended-job path runs under the same credential
    # scrub as the run call (review lows-1 F3).
    cancel_secrets = json.loads(
        (Path(env["REMOTE_GATE_OUT"]) / "remote-cancel-env-secrets.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(cancel_secrets[name] is False for name in secret_names)


@pytest.mark.parametrize(
    ("sig", "expected_status"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_release_gate_forwards_real_signals_and_cancels_before_waiting(
    tmp_path: Path,
    sig: signal.Signals,
    expected_status: int,
) -> None:
    env = _environment(tmp_path)
    secret_names = (
        "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
    )
    env.update({name: f"secret-{name}" for name in secret_names})
    ready_fifo = tmp_path / "remote-ready.fifo"
    os.mkfifo(ready_fifo)
    _configure_fake(env, signal_mode=True, ready_fifo=str(ready_fifo))
    wait_hook = tmp_path / "bash-env.sh"
    wait_calls = tmp_path / "wrapper-wait-calls.txt"
    wait_reaped = tmp_path / "wrapper-wait-completed.txt"
    wait_hook.write_text(
        "wait() {\n"
        '  local child_pid=${1:-unknown} status=0\n'
        '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_CALLS"\n'
        '  builtin wait "$@" || status=$?\n'
        '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_REAPED"\n'
        '  return "$status"\n'
        "}\n",
        encoding="utf-8",
    )
    env.update(
        {
            "BASH_ENV": str(wait_hook),
            "ASSAY_WAIT_CALLS": str(wait_calls),
            "ASSAY_WAIT_REAPED": str(wait_reaped),
        }
    )
    ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
    stdout_path = tmp_path / "wrapper.stdout"
    stderr_path = tmp_path / "wrapper.stderr"
    stdout_stream = stdout_path.open("w", encoding="utf-8")
    stderr_stream = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "tester-unified"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=stdout_stream,
        stderr=stderr_stream,
        start_new_session=True,
    )
    try:
        ready, _, _ = select.select([ready_fd], [], [], 15)
        assert ready, "fake remote-gate did not reach its signal-ready handshake"
        assert os.read(ready_fd, 32) == b"ready\n"
        proc.send_signal(sig)
        proc.wait(timeout=15)
    finally:
        os.close(ready_fd)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            proc.wait(timeout=15)
        stdout_stream.close()
        stderr_stream.close()

    stdout = stdout_path.read_text(encoding="utf-8")
    stderr = stderr_path.read_text(encoding="utf-8")

    assert proc.returncode == expected_status, (stdout, stderr)
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-signal.txt").read_text(
        encoding="utf-8"
    ) == str(sig.value)
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-child-finished.txt").exists()
    child_pid = (Path(env["REMOTE_GATE_OUT"]) / "remote-child.pid").read_text(encoding="utf-8")
    assert wait_calls.read_text(encoding="utf-8").splitlines().count(child_pid) >= 2
    assert child_pid in wait_reaped.read_text(encoding="utf-8").splitlines()
    run_secrets = json.loads((Path(env["REMOTE_GATE_OUT"]) / "remote-env-secrets.json").read_text(encoding="utf-8"))
    cancel_secrets = json.loads((Path(env["REMOTE_GATE_OUT"]) / "remote-cancel-env-secrets.json").read_text(encoding="utf-8"))
    assert all(run_secrets[name] is False and cancel_secrets[name] is False for name in secret_names)
    calls = _remote_calls(env)
    assert [call[0] for call in calls] == ["run", "cancel"]
    assert "remote cancellation was requested" in stderr


@pytest.mark.parametrize("payload_kind", ("host", "lane"))
def test_release_gate_rejects_host_and_lane_injection(
    tmp_path: Path, payload_kind: str
) -> None:
    marker = tmp_path / f"{payload_kind}-injection-ran"
    host = f"v1001;touch {marker}" if payload_kind == "host" else "v1001"
    lane = "tester-unified" if payload_kind == "host" else f"tester-unified;touch {marker}"
    env = _environment(tmp_path, host=host)
    proc = _run(tmp_path, lane, env)

    assert proc.returncode == 2
    assert ("ASSAY_RELEASE_GATE_HOST" in proc.stderr) is (payload_kind == "host")
    assert ("unsupported release-gate lane" in proc.stderr) is (payload_kind == "lane")
    assert not _remote_calls(env)
    assert not marker.exists()


@pytest.mark.parametrize(
    ("lane", "expected_command"),
    [
        ("tester-unified", "tools/tester-unified-gate.sh .."),
        ("self-qualification-provisional", "self-qualification-provisional"),
    ],
)
def test_local_release_gate_execs_the_existing_lane_command(
    tmp_path: Path, lane: str, expected_command: str
) -> None:
    env = _environment(tmp_path, host="local")
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    for command in ("bash", "run-gate"):
        path = fake_bin / command
        path.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" > "$FAKE_LOCAL_COMMAND"\n'
            'exit "${FAKE_LOCAL_STATUS:-17}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
    env["FAKE_LOCAL_COMMAND"] = str(tmp_path / "local-command.txt")
    proc = _run(tmp_path, lane, env)

    assert proc.returncode == 17
    assert Path(env["FAKE_LOCAL_COMMAND"]).read_text(encoding="utf-8").strip() == expected_command
    assert not _remote_calls(env)


def test_local_release_gate_stays_available_without_home(tmp_path: Path) -> None:
    env = _environment(tmp_path, host="local")
    env.pop("HOME")
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    command = fake_bin / "bash"
    command.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" > "$FAKE_LOCAL_COMMAND"\n'
        'exit "${FAKE_LOCAL_STATUS:-17}"\n',
        encoding="utf-8",
    )
    command.chmod(0o755)
    env["FAKE_LOCAL_COMMAND"] = str(tmp_path / "local-command.txt")

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 17
    assert Path(env["FAKE_LOCAL_COMMAND"]).read_text(encoding="utf-8").strip() == (
        "tools/tester-unified-gate.sh .."
    )
    assert not _remote_calls(env)
