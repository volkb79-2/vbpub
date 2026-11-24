"""RG-294/RG-295: parent-owned scratch lifetime and the prune backstop."""

import importlib.util
import json, os, signal, stat, subprocess, sys, time
from pathlib import Path

import pytest

RUN_GATE = Path(__file__).resolve().parent.parent / "run_gate.py"
_SPEC = importlib.util.spec_from_file_location("run_gate_scratch", RUN_GATE)
run_gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_gate
_SPEC.loader.exec_module(run_gate)


def _project(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in (run_gate.ROOT_CONFIG_NAME, run_gate.CONFIG_NAME):
        (root / name).write_text("schema_version = 1\n")
    return root


def _age(path: Path) -> None:
    os.utime(path, (time.time() - 7200,) * 2, follow_symlinks=False)

def _dir(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir()
    (path / "payload").write_text("orphan\n")
    return path


def _owner(root: Path, **overrides) -> None:
    payload = {
        "schema": run_gate.SCRATCH_OWNER_SCHEMA,
        "scratch_run_id": _run_id("a"),
        "owner_pid": os.getpid(),
        "owner_start": run_gate.process_start_ticks(os.getpid()),
        "boot_id": run_gate.boot_id(),
        "pid_ns": run_gate.pid_ns_inode(),
    }
    payload.update(overrides)
    (root / run_gate.SCRATCH_OWNER_NAME).write_text(json.dumps(payload))

def _run_id(tag: str) -> str:
    return f"123-1700000000000000000-{tag * 16}"

def _record(project: Path, run_id: str, root: Path) -> None:
    now = time.time()
    payload = {
        "schema": run_gate.INFLIGHT_SCHEMA, "lane": "suite",
        "runner": "container", "container": "run-gate-test-container",
        "container_id": "sha256:" + "1" * 64, "started_at": "2026-11-24",
        "started_epoch": now, "budget_started_epoch": now,
        "log_path": str(project / ".run-gate" / "suite.log"),
        "owner_pid": os.getpid(),
        "owner_start": run_gate.process_start_ticks(os.getpid()),
        "boot_id": run_gate.boot_id(), "pid_ns": run_gate.pid_ns_inode(),
        "commit": "0" * 40, "worktree": str(project),
        "project_dir": str(project), "revision": run_gate.__revision__,
        "scratch_run_id": run_id,
    }
    path = project / ".run-gate" / run_gate.INFLIGHT_DIR_NAME / "suite.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))


def _prune(project: Path, temp_dir: Path, monkeypatch, *extra: str) -> int:
    monkeypatch.chdir(project)
    monkeypatch.setattr(run_gate.tempfile, "gettempdir", lambda: str(temp_dir))
    return run_gate.main(["doctor", "--prune", *extra])


def test_prune_keeps_live_and_young_and_removes_old_orphans(
        tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "repo")
    temp_dir, store = tmp_path / "system-temp", project / ".run-gate"
    temp_dir.mkdir()
    (store / "tmp").mkdir(parents=True)
    live_id, young_id = _run_id("a"), _run_id("b")
    old_id, fallback_id = _run_id("c"), _run_id("d")
    live, young = _dir(temp_dir, f"run-gate-{live_id}"), \
        _dir(store / "tmp", young_id)
    old, fallback = _dir(temp_dir, f"run-gate-{old_id}"), \
        _dir(store / "tmp", fallback_id)
    legacy = _dir(store, "run-gate-gitcfg-legacy")
    _record(project, live_id, live)
    for path in (live, old, fallback, legacy):
        _age(path)

    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 0
    output = capsys.readouterr().out
    assert live.is_dir() and young.is_dir()
    assert not old.exists() and not fallback.exists() and not legacy.exists(), \
        output
    assert "run-gate-<run-id>: removed 1, kept-live 1, kept-young 0" in output
    assert "tmp/<run-id>: removed 1, kept-live 0, kept-young 1" in output
    assert "run-gate-gitcfg-*: removed 1, kept-live 0, kept-young 0" in output


def test_prune_dry_run_reports_paths_without_deleting(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    orphan = _dir(temp_dir, f"run-gate-{_run_id('e')}")
    _age(orphan)
    assert _prune(project, temp_dir, monkeypatch,
                  "--older-than", "1h", "--dry-run") == 0
    output = capsys.readouterr().out
    assert orphan.is_dir()
    assert f"would remove {orphan}" in output
    assert "dry-run would remove 1" in output and "removed 1" not in output


def test_prune_refuses_symlink_and_ignores_nonmatching_entries(
        tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "repo")
    temp_dir = tmp_path / "system-temp"
    temp_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("outside\n")
    link = temp_dir / "run-gate-gitcfg-symlink"
    link.symlink_to(outside, target_is_directory=True)
    _age(link)
    ordinary = temp_dir / "ordinary-application-data"
    ordinary.mkdir()
    (ordinary / "keep").write_text("keep\n")
    _age(ordinary)
    non_dir = temp_dir / "run-gate-gitcfg-file"
    non_dir.write_text("not a directory\n")
    _age(non_dir)

    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 0
    captured = capsys.readouterr()
    assert link.is_symlink() and sentinel.read_text() == "outside\n"
    assert (ordinary / "keep").read_text() == "keep\n" and \
        non_dir.read_text() == "not a directory\n"
    assert "refusing symlink" in captured.err and \
        "refusing non-directory" in captured.err


def test_prune_defaults_cli_refusals_and_no_probe_mode(
        tmp_path, monkeypatch, capsys):
    budgets = {"a": {"budget": "1h"}, "b": {"budget": "2h"}}
    assert run_gate._scratch_prune_age_seconds(budgets, None)[0] == 7200
    assert run_gate._scratch_prune_age_seconds(
        {}, None)[0] == run_gate.SCRATCH_PRUNE_FALLBACK_SECONDS
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "temp"
    temp_dir.mkdir()
    (project / run_gate.CONFIG_NAME).write_text(
        'schema_version = 1\n[environments.bare-host]\nmode = "host"\n'
        '[assay]\ncommand = ["assay"]\nenvironment = "bare-host"\n'
        'import = { environment = "bare-host", lanes = "all" }\n'
        '[assay.pins.assay]\nsha256 = "tools/assay.pyz.sha256"\n'
        'version = "1.0.0"\n')
    calls = tmp_path / "docker-calls.txt"
    shims = tmp_path / "bin"
    shims.mkdir()
    docker = shims / "docker"
    docker.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shims}:{os.environ['PATH']}")
    assert _prune(project, temp_dir, monkeypatch) == 0
    assert not calls.exists(), "doctor --prune invoked a container command"
    assert "not queried (`doctor --prune` starts no containers)" in \
        capsys.readouterr().err
    for argv, message in (
            (["suite", "--prune"], "`doctor` verb only"),
            (["doctor", "--older-than", "1h"], "requires `run-gate doctor"),
            (["doctor", "--prune", "--json"], "accepts only [--worktree")):
        assert run_gate.main(argv) == 2
        assert message in capsys.readouterr().err


def test_in_process_main_stamps_and_removes_allocated_scratch(
        tmp_path, monkeypatch):
    repo, seen = _project(tmp_path / "repo"), {}
    (repo / ".gitignore").write_text(".run-gate/\n")
    monkeypatch.setattr(run_gate, "physical_path", lambda path, **_: Path(path))
    def dispatch(_argv):
        first, _physical = run_gate._make_private_dir(repo)
        second, _physical = run_gate._make_private_dir(repo)
        payload = {}
        assert run_gate.write_inflight_record(repo, repo, "suite", payload)
        seen.update(payload, root=first.parent, made=(first, second))
        raise RuntimeError("planted")
    monkeypatch.setattr(run_gate, "_dispatch", dispatch)
    assert run_gate.main([]) == 2
    assert not seen["root"].exists()
    assert seen["scratch_run_id"] == seen["root"].name.removeprefix(
        run_gate.SCRATCH_TEMP_PREFIX)


def test_inflight_record_without_active_scratch_omits_run_id(tmp_path):
    project = _project(tmp_path / "repo")
    (project / ".gitignore").write_text(".run-gate/\n")
    record = project / ".run-gate" / run_gate.INFLIGHT_DIR_NAME / "suite.json"
    _record(project, _run_id("a"), project)
    payload = json.loads(record.read_text())
    payload.pop("scratch_run_id")

    run_gate._end_scratch_run()
    assert run_gate.write_inflight_record(
        project, project, "suite", payload)
    written = json.loads(record.read_text())
    assert written["schema"] == run_gate.INFLIGHT_SCHEMA
    assert written["lane"] == "suite"
    assert "scratch_run_id" not in written


def test_private_allocation_outside_run_refuses_without_creating(tmp_path):
    run_gate._end_scratch_run()
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(
            RuntimeError,
            match="run-gate scratch allocation outside a run"):
        run_gate._make_private_dir(repo)
    assert not (repo / ".run-gate").exists()


def test_scratch_base_regular_file_is_an_infra_error(tmp_path):
    base = tmp_path / "scratch-base"
    base.write_text("not a directory\n")
    with pytest.raises(
            run_gate.GateInfraError, match="is not a real directory"):
        run_gate._ensure_scratch_base(base)
    assert base.read_text() == "not a directory\n"


def test_private_allocation_chmod_failure_removes_partial_dir(
        tmp_path, monkeypatch):
    active = run_gate._active_scratch_run()
    assert active is not None and active.root is not None
    root = active.root
    real_chmod = Path.chmod

    def deny_private_chmod(path, mode, *args, **kwargs):
        if path.parent == root and path.name.startswith("private-"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", deny_private_chmod)
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(
            run_gate.GateInfraError,
            match="no host-visible private directory"):
        run_gate._make_private_dir(repo)
    assert not list(root.glob("private-*"))
    assert (root / run_gate.SCRATCH_OWNER_NAME).is_file()


def test_scratch_root_mode_owner_and_symlinked_base(tmp_path, monkeypatch):
    active = run_gate._active_scratch_run()
    assert active.root is not None
    assert stat.S_IMODE(active.root.stat().st_mode) == 0o700
    assert (active.root / run_gate.SCRATCH_OWNER_NAME).is_file()

    repo = _project(tmp_path / "repo-link")
    target = tmp_path / "real-store"
    target.mkdir()
    (repo / ".run-gate").symlink_to(target, target_is_directory=True)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block system temp\n")
    monkeypatch.setattr(run_gate.tempfile, "gettempdir", lambda: str(blocker))
    state = run_gate._ScratchRun(_run_id("2"))
    root, _physical = run_gate._scratch_root(repo, state)
    assert root.parent.resolve() == target / run_gate.SCRATCH_STORE_TMP
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert (root / run_gate.SCRATCH_OWNER_NAME).is_file()
    orphan = _dir(target / run_gate.SCRATCH_STORE_TMP, _run_id('3'))
    _age(orphan)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    assert _prune(repo, system_temp, monkeypatch, "--older-than", "1h") == 0
    assert not orphan.exists()
    run_gate._cleanup_scratch_run(state)

    repo_default = _project(tmp_path / "repo-default")
    old_umask = os.umask(0o022)
    try:
        run_gate._ensure_scratch_base(repo_default / ".run-gate")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(
        (repo_default / ".run-gate").stat().st_mode) == 0o755


def test_prune_matches_current_root_directly_under_repo_store(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "repo")
    temp_dir = tmp_path / "system-temp"
    temp_dir.mkdir()
    store = project / ".run-gate"
    store.mkdir()
    live_id, old_id = _run_id("a"), _run_id("b")
    live = _dir(store, f"{run_gate.SCRATCH_TEMP_PREFIX}{live_id}")
    old = _dir(store, f"{run_gate.SCRATCH_TEMP_PREFIX}{old_id}")
    _record(project, live_id, live)
    _age(live)
    _age(old)
    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 0
    assert live.is_dir() and not old.exists()


def test_prune_uses_root_owner_liveness_and_age_fallback(
        tmp_path, monkeypatch):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    live = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('1')}")
    dead = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('2')}")
    garbage_old = _dir(
        temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('3')}")
    garbage_young = _dir(
        temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('4')}")
    reused = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('5')}")
    owner_link = _dir(
        temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('8')}")
    bad_schema = _dir(
        temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('9')}")
    _owner(live)
    _owner(dead, owner_pid=999999999)
    (garbage_old / run_gate.SCRATCH_OWNER_NAME).write_text("{not json")
    (garbage_young / run_gate.SCRATCH_OWNER_NAME).write_text("garbage\n")
    _owner(reused, owner_start=(run_gate.process_start_ticks(os.getpid()) or 0) + 1)
    owner_link.joinpath(run_gate.SCRATCH_OWNER_NAME).symlink_to(live)
    bad_schema.joinpath(run_gate.SCRATCH_OWNER_NAME).write_text(
        '{"schema": 2}\n')
    for path in (live, dead, garbage_old, reused, owner_link):
        _age(path)
    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 0
    assert live.is_dir() and garbage_young.is_dir() and bad_schema.is_dir()
    assert not dead.exists() and not garbage_old.exists() and not reused.exists()
    assert not owner_link.exists()


def test_prune_reports_removal_errors_and_continues(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    victim = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('6')}")
    survivor = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('7')}")
    _age(victim)
    _age(survivor)
    victim.chmod(0o500)
    real_rmtree = run_gate.shutil.rmtree

    def deny_read_only_victim(path, *args, **kwargs):
        # Some gate images run tests as root, where mode 0500 alone cannot
        # deny unlink. Keep the real read-only fixture and inject its exact
        # permission boundary so any uid exercises the error path.
        if Path(path).name == victim.name:
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(run_gate.shutil, "rmtree", deny_read_only_victim)
    try:
        assert _prune(project, temp_dir, monkeypatch,
                      "--older-than", "1h") == 1
        captured = capsys.readouterr()
        assert victim.is_dir() and not survivor.exists()
        assert "WARNING: cannot remove" in captured.err
        assert ("run-gate-<run-id>: removed 1, kept-live 0, "
                "kept-young 0, error 1") in captured.out
    finally:
        if victim.exists():
            victim.chmod(0o700)
            real_rmtree(victim)


def test_prune_open_failure_is_an_infra_exit(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    store = project / ".run-gate"
    store.mkdir()
    real_open = run_gate.os.open

    def deny_store_open(path, *args, **kwargs):
        if Path(path) == store:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(run_gate.os, "open", deny_store_open)
    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 2
    error = capsys.readouterr().err
    assert "cannot open scratch base" in error
    assert str(store) in error and "Permission denied" in error


def test_prune_entry_stat_failure_is_an_infra_exit(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    victim = _dir(temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('b')}")
    _age(victim)
    real_stat = run_gate.os.stat

    def deny_victim_stat(path, *args, **kwargs):
        if (isinstance(path, (str, os.PathLike))
                and Path(path).name == victim.name
                and kwargs.get("dir_fd") is not None):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(run_gate.os, "stat", deny_victim_stat)
    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 2
    assert victim.is_dir()
    assert "cannot inspect scratch entry" in capsys.readouterr().err


def test_prune_skips_entry_that_vanishes_before_rmtree(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "system-temp"
    temp_dir.mkdir()
    vanished = _dir(
        temp_dir, f"{run_gate.SCRATCH_TEMP_PREFIX}{_run_id('c')}")
    _age(vanished)
    real_rmtree = run_gate.shutil.rmtree

    def vanish_before_rmtree(path, *args, **kwargs):
        if Path(path).name == vanished.name:
            raise FileNotFoundError(str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(run_gate.shutil, "rmtree", vanish_before_rmtree)
    assert _prune(project, temp_dir, monkeypatch, "--older-than", "1h") == 0
    captured = capsys.readouterr()
    assert vanished.is_dir()
    assert ("run-gate-<run-id>: removed 0, kept-live 0, "
            "kept-young 0, error 0") in captured.out
    assert "WARNING" not in captured.err


def test_prune_live_state_and_cleanup_edges(
        tmp_path, monkeypatch, capsys):
    project, temp_dir = _project(tmp_path / "repo"), tmp_path / "temp"
    temp_dir.mkdir()
    run_id = _run_id("f"); old = _dir(temp_dir, f"run-gate-{run_id}")
    record = project / ".run-gate" / run_gate.INFLIGHT_DIR_NAME / "suite.json"
    _record(project, run_id, old)
    data = json.loads(record.read_text())
    _age(old)
    prune = lambda: _prune(project, temp_dir, monkeypatch,
                           "--older-than", "1h")
    data.pop("scratch_run_id"), record.write_text(json.dumps(data))
    assert run_gate._scratch_live_run_ids(project, Path("/proc")) == set()
    data["scratch_run_id"] = "unsafe id"
    record.write_text(json.dumps(data))
    assert prune() == 2 and old.is_dir()
    data.update(scratch_run_id=run_id, owner_pid=999999999)
    record.write_text(json.dumps(data))
    assert prune() == 0 and not old.exists()
    _dir(temp_dir, "old"), record.write_text("{not json")
    assert prune() == 2
    capsys.readouterr()
    root = _dir(tmp_path, "cleanup-root")
    state = run_gate._ScratchRun("x", root=root)
    monkeypatch.setattr(run_gate.shutil, "rmtree",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            OSError("remove interrupted")))
    run_gate._cleanup_scratch_run(state)
    assert (root / "payload").is_file() and "WARNING" in \
        capsys.readouterr().err


_CHILD = """\
import importlib.util, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("scratch_child", sys.argv[1])
run_gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run_gate
spec.loader.exec_module(run_gate)
run_gate.physical_path = lambda path, *args, **kwargs: Path(path)
repo, marker = Path(sys.argv[2]), Path(sys.argv[3])
def dispatch(_argv):
    made, physical = run_gate._make_private_dir(repo)
    marker.write_text(str(made.parent))
    results = {"normal": ("PASS", 0), "fail": ("FAIL", 1)}
    if sys.argv[4] in results:
        return run_gate.LaneResult(*results[sys.argv[4]])
    if sys.argv[4] == "raise": raise RuntimeError("planted")
    if sys.argv[4] == "interrupt": raise KeyboardInterrupt
    while True:
        signal.pause()
run_gate._dispatch = dispatch
raise SystemExit(run_gate.main([]))
"""


def _start_child(tmp_path: Path, mode: str):
    repo, temp_dir, marker = tmp_path / "repo", tmp_path / "system-temp", \
        tmp_path / "scratch-root.txt"
    repo.mkdir(), temp_dir.mkdir()
    script = tmp_path / "scratch-child.py"
    script.write_text(_CHILD)
    argv = [sys.executable, str(script), str(RUN_GATE), str(repo), str(marker),
            mode]
    return subprocess.Popen(argv, env=dict(os.environ, TMPDIR=str(temp_dir)),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True), marker


def _communicate(proc) -> tuple[str, str]:
    try:
        return proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=60)


@pytest.mark.parametrize(("mode", "exit_code"), [
    ("normal", 0), ("fail", 1), ("raise", 2), ("interrupt", 2)])
def test_main_removes_scratch_root_for_normal_failure_and_raise(
        tmp_path, mode, exit_code):
    proc, marker = _start_child(tmp_path, mode)
    stdout, stderr = _communicate(proc)
    assert proc.returncode == exit_code, (stdout, stderr)
    assert not Path(marker.read_text()).exists()


def test_main_removes_scratch_root_after_sigterm(tmp_path):
    proc, marker = _start_child(tmp_path, "sigterm")
    deadline = time.monotonic() + 60
    try:
        while not marker.exists():
            assert proc.poll() is None
            assert time.monotonic() < deadline, "scratch child did not publish"
            time.sleep(0.01)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = _communicate(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=60)
    assert proc.returncode == 2, (stdout, stderr)
    assert not Path(marker.read_text()).exists()
