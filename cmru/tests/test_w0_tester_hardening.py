"""W0-TESTER (cmru program 2026-10): tester-gate container hardening.

Closes KI-52(a), BG-01 (a, a', c'), BG-02, BG-06, BG-07 (M04), BG-12 (M09),
BG-13.  The 2026-10-05 incident: a gate ran as PID 1 without ``--init``, git's
detached auto-maintenance orphaned one process per commit (19,108 zombies),
the host-default pids ceiling was exhausted and assay recorded 192/192 false
kills.  Nothing here starts a real container: ``docker`` is a fake on PATH
that records every call.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import handlers, runner, tester_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = "debian@sha256:" + "a" * 64
DIND = "docker@sha256:" + "b" * 64

FAKE_DOCKER = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    argv = sys.argv[1:]
    with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\\n")
    cmd = argv[0] if argv else ""
    if cmd == "run":
        if "systemctl" in argv:
            print("LoadState=loaded")
            print("FragmentPath=/etc/systemd/system/dev-gates.slice")
            sys.exit(0)
        if "-d" in argv:
            sys.exit(0)
        if "cmru-events-wrapper" in argv:
            if os.environ.get("FAKE_HANG"):
                open(os.environ["FAKE_HANG"], "w").close()
                time.sleep(600)
            events = argv[argv.index("cmru-events-wrapper") + 1]
            mount = next(a for a in argv if a.endswith(",dst=/worktree"))
            src = mount.split("src=", 1)[1].split(",", 1)[0]
            mode = os.environ.get("FAKE_EVENTS", "clean")
            text = {
                "clean": "pids.events max 0\\nmemory.events low 0\\nmemory.events oom_kill 0\\n",
                "pids": "pids.events max 7\\nmemory.events oom_kill 0\\n",
                "oom": "pids.events max 0\\nmemory.events oom_kill 2\\n",
                "partial": "memory.events oom_kill 0\\n",
            }.get(mode)
            if text is not None:
                with open(os.path.join(src, events[len("/worktree/"):]), "w") as out:
                    out.write(text)
            sys.exit(int(os.environ.get("FAKE_RC", "0")))
        sys.exit(0)
    if cmd == "exec":
        print("29.0.0")
    sys.exit(0)
''')


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def fake_docker(tmp_path, monkeypatch):
    """A git worktree as cwd, a recording fake ``docker`` first on PATH, and
    every required tester variable declared."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "CMRU_TESTER_UNIFIED_IMAGE": "tester:test",
        "CMRU_TESTER_MEMORY": "1g",
        "CMRU_TESTER_MEMORY_SWAP": "2g",
        "CMRU_TESTER_CPUS": "1",
        "CMRU_TESTER_PIDS_LIMIT": "4096",
        "CMRU_TESTER_CGROUP_PROBE_IMAGE": PROBE,
        "CMRU_TESTER_CGROUP_PARENT": "dev-gates.slice",
        "CMRU_TESTER_DIND_IMAGE": DIND,
        "CMRU_TESTER_DIND_MEMORY": "2g",
        "CMRU_TESTER_DIND_CPUS": "1.5",
        "CMRU_TESTER_DIND_PIDS_LIMIT": "2048",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("FAKE_EVENTS", "FAKE_RC", "FAKE_HANG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(tester_gate, "_physical_path", lambda path: path)
    monkeypatch.chdir(repo)
    return SimpleNamespace(repo=repo, log=log, env=env, bindir=bindir)


def _calls(fake) -> list[list[str]]:
    if not fake.log.exists():
        return []
    return [json.loads(line) for line in fake.log.read_text().splitlines()]


def _name(argv) -> str:
    return argv[argv.index("--name") + 1]


def _run_gate(*extra) -> int:
    return tester_gate.main(["--cwd", ".", *extra, "--", "true"])


# --- KI-52(a) / BG-01(a): --init on every container for a command cmru does not control

def test_every_container_cmru_starts_has_the_declared_argv_policy(fake_docker):
    assert _run_gate("--enable-docker") == 0
    runs = [argv for argv in _calls(fake_docker) if argv[0] == "run"]
    probes = [a for a in runs if "systemctl" in a]
    dinds = [a for a in runs if "-d" in a]
    gates = [a for a in runs if "cmru-events-wrapper" in a]
    assert (len(probes), len(dinds), len(gates)) == (1, 1, 1)

    # Commands cmru does not control: the gate workload and the DinD sidecar.
    for argv in dinds + gates:
        assert "--init" in argv, argv
    assert gates[0][gates[0].index("--pids-limit") + 1] == "4096"
    assert dinds[0][dinds[0].index("--memory") + 1] == "2g"
    assert dinds[0][dinds[0].index("--cpus") + 1] == "1.5"
    assert dinds[0][dinds[0].index("--pids-limit") + 1] == "2048"

    # Privileged / host-PID images are never pulled implicitly (BG-06).
    for argv in probes + dinds:
        assert "--pull=never" in argv and "--privileged" in argv
    assert PROBE in probes[0] and DIND in dinds[0]

    # Every container has a unique exact name of the declared shape (BG-02).
    names = [_name(a) for a in runs]
    assert len(set(names)) == len(names) == 3
    assert re.fullmatch(r"cmru-probe-[0-9a-f]{8}", _name(probes[0]))
    assert re.fullmatch(r"cmru-tester-dind-[0-9a-f]{12}", _name(dinds[0]))
    assert re.fullmatch(r"cmru-tester-[0-9a-f]{8}", _name(gates[0]))
    # Every container also lands in the declared tier.
    assert all("--cgroup-parent=dev-gates.slice" in a for a in runs)


def test_run_command_surface_has_no_unnamed_or_uninitialised_gate_launch():
    """Static twin of the runtime test: the ONLY place a gate workload argv is
    assembled passes --init (tester_gate.py has exactly two non-probe
    ``docker run`` builders)."""
    source = (Path(tester_gate.__file__)).read_text(encoding="utf-8")
    assert source.count('"--init"') == 2  # build_docker_command + _dind_start_argv


# --- BG-02: exact-name cleanup in finally, on success and on failure

def test_each_started_container_is_stopped_then_removed_by_exact_name(fake_docker):
    assert _run_gate("--enable-docker") == 0
    calls = _calls(fake_docker)
    started = [_name(a) for a in calls if a[0] == "run"]
    for name in started:
        stops = [i for i, a in enumerate(calls) if a[0] == "stop" and a[-1] == name]
        removes = [i for i, a in enumerate(calls) if a[:2] == ["rm", "-f"] and a[-1] == name]
        if name.startswith("cmru-probe-"):
            # A probe that finished normally is --rm'd by docker itself.
            assert not stops and not removes
            continue
        assert stops and removes and stops[0] < removes[0], (name, calls)


def test_probe_timeout_removes_that_probe_by_exact_name(fake_docker, monkeypatch):
    real_run = subprocess.run
    seen = []

    def run(argv, **kwargs):
        if argv[:2] == ["docker", "run"] and "systemctl" in argv:
            seen.append(_name(argv))
            raise subprocess.TimeoutExpired(argv, 30)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(tester_gate.subprocess, "run", run)
    exists, note = tester_gate.check_slice_unit("dev-gates.slice", PROBE, "dev-gates.slice")
    assert exists is False and "could not probe" in note
    removes = [a for a in _calls(fake_docker) if a[:2] == ["rm", "-f"]]
    assert removes == [["rm", "-f", seen[0]]]
    assert seen[0].startswith("cmru-probe-")


def test_sigterm_stops_and_removes_the_gate_container_by_name(fake_docker, tmp_path):
    """Signal delivery: SIGTERM mid-gate turns into SystemExit, the docker CLI
    child is killed, and ``docker stop`` then ``docker rm -f`` run by EXACT
    name (before, the container leaked and the privileged sidecar's finally
    never ran)."""
    marker = tmp_path / "gate-started"
    env = {**os.environ, **fake_docker.env, "FAKE_HANG": str(marker),
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from cmru.tester_gate import main; raise SystemExit(main(sys.argv[1:]))",
         "--cwd", ".", "--", "true"],
        cwd=fake_docker.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists():
            assert proc.poll() is None, proc.communicate()
            assert time.monotonic() < deadline, "gate never started"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 128 + signal.SIGTERM
    calls = _calls(fake_docker)
    gate = next(_name(a) for a in calls if a[0] == "run" and "cmru-events-wrapper" in a)
    stop = next(i for i, a in enumerate(calls) if a[0] == "stop" and a[-1] == gate)
    remove = next(i for i, a in enumerate(calls) if a[:2] == ["rm", "-f"] and a[-1] == gate)
    assert stop < remove
    assert not list(fake_docker.repo.glob(".cmru/*")), "events file left behind"


def test_run_command_kills_and_reaps_its_child_on_keyboard_interrupt(tmp_path):
    pidfile = tmp_path / "child.pid"
    child = (
        f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); "
        "print('line', flush=True); time.sleep(120)"
    )

    class Interrupting:
        def write(self, _text):
            raise KeyboardInterrupt

        def flush(self):
            pass

    with pytest.raises(KeyboardInterrupt):
        runner.run_command([sys.executable, "-c", child], tmp_path, Interrupting())
    pid = int(pidfile.read_text())
    # ``kill(pid, 0)`` still succeeds for an unreaped zombie, so this proves
    # the child was both killed AND waited for.
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# --- BG-01(c'): the in-container cgroup events file

def _events(path: Path, text: str | None) -> Path:
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


CLEAN = "pids.events max 0\nmemory.events low 0\nmemory.events oom_kill 0\n"


@pytest.mark.parametrize(
    ("text", "returncode", "expected", "named"),
    [
        (CLEAN, 0, 0, None),
        (CLEAN, 7, 7, None),  # the command's own exit status is preserved
        ("pids.events max 3\nmemory.events oom_kill 0\n", 0, 3, "pids.events max=3"),
        ("pids.events max 3\nmemory.events oom_kill 0\n", 7, 3, "pids.events max=3"),
        ("pids.events max 0\nmemory.events oom_kill 1\n", 0, 3, "memory.events oom_kill=1"),
        ("memory.events oom_kill 0\n", 0, 3, "pids.events has no 'max' counter"),
        ("pids.events max 0\n", 0, 3, "memory.events has no 'oom_kill' counter"),
        ("garbage\n", 0, 3, "malformed line 'garbage'"),
        # A non-integer value is an infrastructure failure with a message, never a crash.
        ("pids.events max abc\nmemory.events oom_kill 0\n", 0, 3, "malformed line 'pids.events max abc'"),
        ("pids.events max -1\nmemory.events oom_kill 0\n", 0, 3, "malformed line"),
        ("pids.events max 0 9\nmemory.events oom_kill 0\n", 0, 3, "malformed line"),
        (None, 0, 3, "missing or unreadable"),  # a missing file proves nothing
        (None, 0, 3, "uid baked into the image"),  # ... and the message says why it usually happens
    ],
)
def test_gate_exit_code_reads_the_events_file(tmp_path, capsys, text, returncode, expected, named):
    path = _events(tmp_path / "events.txt", text)
    assert tester_gate.gate_exit_code(returncode, path) == expected
    err = capsys.readouterr().err
    if named is None:
        assert err == ""
    else:
        assert "INFRASTRUCTURE failure" in err and named in err
        assert f"command exit status {returncode}" in err


@pytest.mark.parametrize(
    ("events", "rc", "expected"),
    [("clean", "0", 0), ("clean", "7", 7), ("pids", "0", 3), ("oom", "0", 3),
     ("pids", "7", 3), ("partial", "0", 3), ("missing", "0", 3)],
)
def test_main_exits_infrastructure_failure_on_counter_or_missing_file(
    fake_docker, monkeypatch, capsys, events, rc, expected,
):
    monkeypatch.setenv("FAKE_EVENTS", events)
    monkeypatch.setenv("FAKE_RC", rc)
    assert _run_gate() == expected
    assert not list(fake_docker.repo.glob(".cmru/*")), "events file must be removed"
    assert not (fake_docker.repo / ".cmru").exists(), "empty .cmru directory must be removed"
    if expected == 3:
        assert "INFRASTRUCTURE failure" in capsys.readouterr().err


def test_gate_wrapper_copies_both_counter_files_and_preserves_the_exit_status(tmp_path):
    """The shell wrapper itself, run for real against fake cgroup files."""
    cgroup = tmp_path / "cg"
    cgroup.mkdir()
    (cgroup / "pids.events").write_text("max 0\n")
    (cgroup / "memory.events").write_text("low 0\nmax 0\noom 0\noom_kill 0\n")
    script = tester_gate._EVENTS_WRAPPER.replace("/sys/fs/cgroup", str(cgroup))
    events = tmp_path / "events.txt"
    done = subprocess.run(
        ["sh", "-c", script, "wrapper", str(events), "sh", "-c", "exit 7"], check=False,
    )
    assert done.returncode == 7
    assert tester_gate.read_events_problems(events) == []
    assert "pids.events max 0" in events.read_text()
    (cgroup / "pids.events").write_text("max 5\n")
    subprocess.run(["sh", "-c", script, "wrapper", str(events), "true"], check=True)
    assert tester_gate.gate_exit_code(0, events) == 3


# --- BG-01(a'): the pids limit is required, positive, never defaulted

def test_pids_limit_is_required_and_has_no_hidden_default(monkeypatch):
    assert "CMRU_TESTER_PIDS_LIMIT" in tester_gate.REQUIRED_TESTER_ENV
    monkeypatch.delenv("CMRU_TESTER_PIDS_LIMIT", raising=False)
    with pytest.raises(SystemExit, match="no pids limit resolvable"):
        tester_gate.resolve_pids_limit(None)
    monkeypatch.setenv("CMRU_TESTER_PIDS_LIMIT", "256")
    assert tester_gate.resolve_pids_limit(None) == "256"
    assert tester_gate.resolve_pids_limit("8") == "8"


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", "1e3", "0x10", "04096x"])
def test_pids_limit_refuses_unlimited_or_malformed_values(value):
    with pytest.raises(SystemExit, match="positive integer"):
        tester_gate.resolve_pids_limit(value)


@pytest.mark.skipif(
    not (REPO_ROOT / "cmru.orchestration.toml").exists(),
    reason="repository-root configs absent (isolated canary tree)",
)
def test_estate_configs_declare_the_pids_limit_and_pinned_helper_images():
    orchestration = tomllib.loads((REPO_ROOT / "cmru.orchestration.toml").read_text())
    env = orchestration["orchestration"]["defaults"]["env"]
    assert env["CMRU_TESTER_PIDS_LIMIT"] == "4096"
    assert tester_gate.require_digest_pinned(env["CMRU_TESTER_CGROUP_PROBE_IMAGE"], "probe")
    mdt = tomllib.loads((REPO_ROOT / "modern-debian-tools-python-debug" / "cmru.toml").read_text())["env"]
    assert tester_gate.require_digest_pinned(mdt["CMRU_TESTER_DIND_IMAGE"], "dind")
    for key in tester_gate.DIND_TESTER_ENV:
        assert mdt[key].strip(), key
    for template in (REPO_ROOT / "cmru" / "templates" / "cmru.toml.tmpl",
                     REPO_ROOT / "cmru" / "src" / "cmru" / "templates" / "orchestration.toml"):
        assert "CMRU_TESTER_PIDS_LIMIT" in template.read_text(), template


# --- BG-06 / BG-13: image references

@pytest.mark.parametrize("resolver", ["resolve_cgroup_probe_image", "resolve_dind_image"])
@pytest.mark.parametrize("value", ["debian@sha256:<digest>", "debian@sha256:<replace-with-digest>", "<digest>"])
def test_template_placeholder_gets_a_replace_it_message_not_a_generic_refusal(resolver, value):
    with pytest.raises(SystemExit) as refused:
        getattr(tester_gate, resolver)(value)
    message = str(refused.value)
    assert "template placeholder" in message
    assert "docker image inspect --format '{{index .RepoDigests 0}}' <image>" in message
    assert "invalid" not in message and "not digest-pinned" not in message


def test_two_launches_get_different_container_names(fake_docker):
    """A constant name would collide between concurrent gates (and make exact-name
    cleanup remove another gate's container)."""
    assert tester_gate._container_name("tester") != tester_gate._container_name("tester")
    assert _run_gate() == 0
    assert _run_gate() == 0
    names = [_name(a) for a in _calls(fake_docker) if a[0] == "run"]
    assert len(names) == 4 and len(set(names)) == 4


@pytest.mark.parametrize("resolver", ["resolve_cgroup_probe_image", "resolve_dind_image"])
@pytest.mark.parametrize("value", ["debian:trixie-slim", "debian@sha256:abc", "debian@sha256:" + "A" * 64])
def test_privileged_images_must_be_digest_pinned(resolver, value):
    with pytest.raises(SystemExit, match="not digest-pinned"):
        getattr(tester_gate, resolver)(value)


@pytest.mark.parametrize("value", ["-v/:/host", "--privileged", "", "   ", "a b", "img;rm", "ima\nge"])
def test_image_references_that_docker_would_parse_as_options_are_refused(value):
    with pytest.raises(SystemExit, match="invalid"):
        tester_gate.validate_image_reference(value, "tester image")
    with pytest.raises(SystemExit):
        tester_gate.build_docker_command(
            Path("/repo"), ".", ["true"], image=value, cgroup_parent="s.slice", memory="1g",
            memory_swap="2g", cpus="1", pids_limit="64",
        )


def test_dash_prefixed_image_is_refused_before_any_container_starts(fake_docker, monkeypatch, capsys):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "--privileged")
    assert _run_gate() != 0
    assert "invalid tester image" in capsys.readouterr().err
    assert _calls(fake_docker) == []


# --- BG-07: DinD limits, readiness probe (M04), timeout

@pytest.mark.parametrize(
    ("returncode", "stdout", "ready"),
    [(0, "29.0.0\n", True), (1, "29.0.0\n", False), (0, "", False), (0, "  \n", False), (125, "", False)],
)
def test_dind_ready_requires_exit_zero_and_a_version(monkeypatch, returncode, stdout, ready):
    """Kills M04 (readiness ignoring the exit code): a failing exec that still
    printed a version must NOT count as ready."""
    monkeypatch.setattr(
        tester_gate.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert tester_gate._dind_ready("cmru-tester-dind-x") is ready


def test_dind_sidecar_never_ready_when_exec_fails_with_output(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tester_gate.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(tester_gate.time, "sleep", lambda s: clock.update(t=clock["t"] + s))

    def run(argv, **_kw):
        if argv[:2] == ["docker", "exec"]:
            return SimpleNamespace(returncode=1, stdout="29.0.0\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(tester_gate.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="did not become ready"):
        with tester_gate.dind_sidecar(DIND, cgroup_parent="s.slice", ready_timeout=3.0,
                                      memory="1g", cpus="1", pids_limit="64"):
            pass  # pragma: no cover


def test_dind_ready_probe_has_a_timeout_and_a_hang_counts_as_not_ready(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(tester_gate.subprocess, "run", run)
    assert tester_gate._dind_ready("cmru-tester-dind-x") is False
    assert 0 < seen["timeout"] <= 30


@pytest.mark.parametrize("resolver", ["resolve_dind_memory", "resolve_dind_cpus", "resolve_dind_pids_limit"])
def test_dind_limits_are_required(monkeypatch, resolver):
    for name in tester_gate.DIND_TESTER_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="resolvable"):
        getattr(tester_gate, resolver)(None)


def test_dind_limits_are_validated(monkeypatch):
    with pytest.raises(SystemExit, match="positive integer"):
        tester_gate.resolve_dind_pids_limit("0")
    with pytest.raises(SystemExit, match="minimum 0.00001"):
        tester_gate.resolve_dind_cpus("0")
    assert tester_gate.resolve_dind_cpus("1.5") == "1.5"


def test_standards_requires_every_dind_variable_for_a_docker_gate():
    from cmru import standards

    assert standards.DIND_TESTER_ENV == tester_gate.DIND_TESTER_ENV


# --- BG-12: equal-length mount points pick the LAST (visible) entry (M09)

SHADOWED = (
    "10 1 0:1 /shadowed /cockpit rw - bind x y\n"
    "11 1 0:2 /visible /cockpit rw - bind x y\n"
)


def test_physical_path_tie_picks_the_visible_last_mount():
    assert tester_gate._physical_path(Path("/cockpit/cmru"), SHADOWED) == Path("/visible/cmru")


def test_handlers_host_bind_source_tie_picks_the_visible_last_mount(monkeypatch):
    monkeypatch.setattr(handlers.Path, "read_text", lambda *a, **k: SHADOWED)
    assert handlers._host_bind_source(Path("/cockpit/cmru")) == "/visible/cmru"


# --- signal-handler scoping and a shared .cmru directory

def test_terminate_as_exit_installs_and_restores_handlers_and_tolerates_other_threads():
    import threading

    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    with tester_gate._terminate_as_exit():
        for signum in before:
            assert signal.getsignal(signum) is not before[signum]
            with pytest.raises(SystemExit) as exited:
                signal.raise_signal(signum)
            assert exited.value.code == 128 + signum
    assert {s: signal.getsignal(s) for s in before} == before

    outcome = []

    def worker():  # signal.signal() is main-thread only: must degrade, not crash
        with tester_gate._terminate_as_exit():
            outcome.append("ran")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert outcome == ["ran"]


def test_other_files_in_the_cmru_directory_survive_a_gate_run(fake_docker):
    (fake_docker.repo / ".cmru").mkdir()
    keep = fake_docker.repo / ".cmru" / "someone-elses.txt"
    keep.write_text("keep")
    assert _run_gate() == 0
    assert keep.read_text() == "keep"
    assert [p.name for p in (fake_docker.repo / ".cmru").iterdir()] == ["someone-elses.txt"]
