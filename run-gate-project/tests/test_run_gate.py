"""Unit suite for run-gate.py — construction pinned against a FAKE docker.

Construction is NOT acceptance: these pins prove argv SHAPE only (the P06/P07
lesson); live acceptance is oracle O4, run against real docker separately.
Every argv assertion compares the LIST, never a joined string.
"""

import fcntl
import os
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

RUN_GATE_DIR = Path(__file__).resolve().parent.parent
_TOOL = RUN_GATE_DIR / "run-gate.py"  # hyphenated filename: load via importlib

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_gate", _TOOL)
run_gate = importlib.util.module_from_spec(_spec)
sys.modules["run_gate"] = run_gate
_spec.loader.exec_module(run_gate)

CGROUP_VAR = "CGROUP_PARENT_DEV_BACKGROUND"


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit; returns repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def commit_all(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def make_project(repo: Path, config: str, name: str = "proj") -> Path:
    """Project subdir with a committed run-gate.toml; returns project dir."""
    proj = repo / name
    proj.mkdir()
    (proj / "run-gate.toml").write_text(textwrap.dedent(config))
    commit_all(repo, f"lane config {name}")
    return proj


def fake_docker(tmp_path: Path, monkeypatch, wait_code: int | str = 0) -> Path:
    """PATH-shim docker that RECORDS every invocation losslessly (args joined
    with \\037 = \\x1f — plain `echo "$@"` destroys quoting; \\xHH is NOT
    portable printf, octal is)."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker-calls.log"
    log.write_text("")
    shim = shim_dir / "docker"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\037' "$@" >> "{log}"
        printf '\\n' >> "{log}"
        case "$1" in
          run) echo "fake-container-id" ;;
          logs) echo "FAKE-LOGS-LINE" ;;
          wait) printf '%s\\n' "{wait_code}" ;;
          rm) : ;;
        esac
        exit 0
    """))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
    return log


def docker_runs(log: Path) -> list[list[str]]:
    out = []
    for line in log.read_text().splitlines():
        parts = line.split("\x1f")
        if parts and parts[0] == "run":
            out.append([p for p in parts if p != ""])
    return out


def docker_execs(log: Path) -> list[list[str]]:
    out = []
    for line in log.read_text().splitlines():
        parts = line.split("\x1f")
        if parts and parts[0] == "exec":
            out.append([p for p in parts if p != ""])
    return out


def shim_dir_of(monkeypatch) -> Path:
    return Path(os.environ["PATH"].split(":")[0])


@pytest.fixture(autouse=True)
def ambient_cgroup(monkeypatch):
    """Tests declare slices explicitly or set the var themselves."""
    monkeypatch.setenv(CGROUP_VAR, "dev-background.slice")


SIMPLE_LANE = """\
    schema_version = 1

    [environments.tester-unified]
    image = "tester-unified:local"

    [lanes.suite]
    kind = "command"
    environment = "tester-unified"
    argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]
    clean_tree = false
"""


def run_tool(proj: Path, *args, cwd=None):
    return subprocess.run([sys.executable, str(_TOOL), *args],
                          cwd=cwd or proj, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# O1 — UX surface (R-01..R-05)
# ---------------------------------------------------------------------------

class TestUxSurface:
    def test_help_prints_revision_and_lanes(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj, "--help")
        assert proc.returncode == 0
        assert f"rev {run_gate.__revision__}" in proc.stdout
        assert "suite" in proc.stdout and "environment=tester-unified" in proc.stdout

    def test_no_args_prints_usage(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj)
        assert proc.returncode == 0
        assert "usage:" in proc.stdout

    def test_no_config_is_one_line_not_traceback(self, tmp_path):
        proc = run_tool(tmp_path, "--help")  # empty dir: no config anywhere
        assert proc.returncode == 2
        assert proc.stderr.count("\n") == 1
        assert "Traceback" not in proc.stderr

    def test_reserved_exit_codes_documented_in_usage(self, tmp_path):
        # RG-11: the codes are part of the scripting contract — invisible
        # usage text would be provenance theater about them.
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj, "--help")
        assert proc.returncode == 0
        assert "exit codes:" in proc.stdout
        assert "2 = configuration/refusal" in proc.stdout
        assert "3 = execution infrastructure" in proc.stdout

    def test_list_machine_readable_sorted(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE + """
            [lanes.alpha]
            kind = "command"
            environment = "host"
            argv = ["true"]
            clean_tree = false
        """)
        proc = run_tool(proj, "--list")
        assert proc.returncode == 0
        assert proc.stdout.splitlines() == [
            "alpha\tcommand\thost",
            "suite\tcommand\ttester-unified",
        ]

    def test_unknown_lane_names_known_lanes_and_config(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj, "nope")
        assert proc.returncode == 2
        assert "unknown lane 'nope'" in proc.stderr
        assert "suite" in proc.stderr
        assert str(proj / "run-gate.toml") in proc.stderr

    def test_symlink_parent_is_the_project_not_the_target(self, tmp_path):
        """R-01/§1: invoked-symlink's PARENT resolves the config (regression:
        .resolve() used to follow the link to run-gate-project itself)."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        link = proj / "run-gate.py"
        link.symlink_to(RUN_GATE_DIR / "run-gate.py")
        proc = subprocess.run([sys.executable, str(link), "--list"],
                              cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("suite\t")


# ---------------------------------------------------------------------------
# O2 — config validation: every error names key + file
# ---------------------------------------------------------------------------

BAD_CONFIGS = {
    "schema_version": 'schema_version = 2\n',
    "unknown_top_key": "schema_version = 1\nwat = 1\n",
    "unknown_lane_key": """\
        schema_version = 1
        [lanes.a]
        kind = "command"
        environment = "host"
        argv = ["true"]
        floorg = 1
    """,
    "unknown_kind": """\
        schema_version = 1
        [lanes.a]
        kind = "makefile"
        environment = "host"
    """,
    "command_missing_argv": """\
        schema_version = 1
        [lanes.a]
        kind = "command"
        environment = "host"
    """,
    "assay_missing_assay_lane": """\
        schema_version = 1
        [lanes.a]
        kind = "assay"
        environment = "host"
        assay_command = ["assay"]
    """,
    "assay_missing_assay_command": """\
        schema_version = 1
        [lanes.a]
        kind = "assay"
        environment = "host"
        assay_lane = "x"
    """,
    "pin_missing_sha256": """\
        schema_version = 1
        [lanes.a]
        kind = "assay"
        environment = "host"
        assay_lane = "x"
        assay_command = ["assay"]
        [lanes.a.pins.assay]
        version = "2.1.0"
    """,
    "pin_version_not_string": """\
        schema_version = 1
        [lanes.a]
        kind = "assay"
        environment = "host"
        assay_lane = "x"
        assay_command = ["assay"]
        [lanes.a.pins.assay]
        sha256 = "x/y.sha256"
        version = 21
    """,
    "bad_budget": """\
        schema_version = 1
        [lanes.a]
        kind = "command"
        environment = "host"
        argv = ["true"]
        budget = "20 minutes"
    """,
    "bad_memory": """\
        schema_version = 1
        [lanes.a]
        kind = "command"
        environment = "host"
        argv = ["true"]
        memory = "lots"
    """,
    "clean_tree_not_bool": """\
        schema_version = 1
        [lanes.a]
        kind = "command"
        environment = "host"
        argv = ["true"]
        clean_tree = "yes"
    """,
    "host_redefined": """\
        schema_version = 1
        [environments.host]
        image = "nope"

        [lanes.a]
        kind = "command"
        environment = "host"
        argv = ["true"]
    """,
}


class TestConfigValidation:
    @pytest.mark.parametrize("case", sorted(BAD_CONFIGS))
    def test_each_error_names_key_and_file(self, tmp_path, case):
        repo = make_repo(tmp_path)
        proj = make_project(repo, BAD_CONFIGS[case])
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert str(proj / "run-gate.toml") in str(exc.value), \
            f"{case}: error must name the file"

    def test_unknown_environment_names_both_sources(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.a]
            kind = "command"
            environment = "missing-env"
            argv = ["true"]
        """)
        cfg, cfg_path, central, cpath = run_gate.load_config(proj)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.resolve_environment(cfg["lanes"]["a"], "a", cfg, central,
                                         cfg_path, cpath)
        assert "missing-env" in str(exc.value)

    def test_central_lanes_inherited_and_shadowed(self, tmp_path):
        """RG-16: central configs may define shared lanes; the project
        shadows by name WHOLESALE (no field merging)."""
        repo = make_repo(tmp_path)
        (repo / "run-gate.toml").write_text(textwrap.dedent("""\
            schema_version = 1
            [lanes.shared]
            kind = "command"
            environment = "host"
            argv = ["echo", "from-central"]
            clean_tree = false
            [lanes.shadowed]
            kind = "command"
            environment = "host"
            argv = ["echo", "central-version"]
            clean_tree = false
        """))
        commit_all(repo, "central shared lanes")
        proj = make_project(repo, SIMPLE_LANE + """
            [lanes.shadowed]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "cd {worktree}/proj && echo project-version"]
            clean_tree = false
        """)
        cfg, cfg_path, central, cpath = run_gate.load_config(proj)
        merged = run_gate.merge_lanes(cfg.get("lanes", {}), central, proj,
                                      cfg_path, cpath)
        assert set(merged) == {"suite", "shared", "shadowed"}
        assert merged["shadowed"]["argv"] == [
            "bash", "-c", "cd {worktree}/proj && echo project-version"]
        # inherited marker computed for usage() excludes shadowed names
        inherited = set(central.get("lanes", {})) - set(cfg.get("lanes", {}))
        assert inherited == {"shared"}

    def test_central_lane_missing_pin_sidecar_refuses_for_consumer(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "run-gate.toml").write_text(textwrap.dedent("""\
            schema_version = 1
            [lanes.assay-shared]
            kind = "assay"
            environment = "host"
            assay_lane = "gate"
            assay_command = ["./tools/assay.pyz"]
            clean_tree = false
            [lanes.assay-shared.pins.assay]
            version = "2.1.0"
            sha256 = "tools/assay.pyz.sha256"
        """))
        commit_all(repo, "central assay lane")
        proj = make_project(repo, SIMPLE_LANE)  # does NOT vendor tools/assay.pyz.sha256
        cfg, cfg_path, central, cpath = run_gate.load_config(proj)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.merge_lanes(cfg.get("lanes", {}), central, proj,
                                 cfg_path, cpath)
        assert "does not exist in this project" in str(exc.value)
        assert "tools/assay.pyz.sha256" in str(exc.value)

    def test_central_lane_malformed_still_fails_loudly(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "run-gate.toml").write_text(
            "schema_version = 1\n[lanes.bad]\nkind = 'makefile'\n")
        proj = make_project(repo, SIMPLE_LANE)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "[lanes.bad]" in str(exc.value)

    def test_invoking_a_shared_central_lane_end_to_end(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        (repo / "run-gate.toml").write_text(textwrap.dedent("""\
            schema_version = 1
            [lanes.shared]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]
            clean_tree = false
            [environments.tester-unified]
            image = "tester-unified:local"
        """))
        commit_all(repo, "central lane + env")
        proj = make_project(repo, SIMPLE_LANE)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "shared")
        assert proc.returncode == 0, proc.stderr
        inner = docker_runs(log)[0][-1]
        assert "echo gate-ran" in inner


# ---------------------------------------------------------------------------
# §4.2a / R-10..R-12 — environment facts: DERIVE / READ / FAIL
# ---------------------------------------------------------------------------

class TestNoSilentDefaults:
    def test_missing_cgroup_env_var_fails_naming_it(self, tmp_path, monkeypatch):
        monkeypatch.delenv(CGROUP_VAR, raising=False)
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        cfg, cfg_path, central, cpath = run_gate.load_config(proj)
        env, src = run_gate.resolve_environment(cfg["lanes"]["suite"], "suite",
                                                cfg, central, cfg_path, cpath)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.resolve_slice(env, src)
        assert CGROUP_VAR in str(exc.value)
        assert "cgroup_slice" in str(exc.value)  # names the legitimate alternative

    def test_declared_slice_beats_missing_env_var(self, tmp_path, monkeypatch):
        monkeypatch.delenv(CGROUP_VAR, raising=False)
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"
            cgroup_slice = "declared.slice"

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo hi"]
            clean_tree = false
        """)
        log = log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        run_call = docker_runs(log)[0]
        assert run_call[run_call.index("--cgroup-parent") + 1] == "declared.slice"

    def test_loadstate_checked_only_where_systemd_reachable(self, tmp_path, monkeypatch):
        """systemd reachable + probe says not-loaded -> loud failure."""
        real_run = subprocess.run

        def spy(cmd, **kw):
            if cmd and cmd[0] == "systemctl":
                return subprocess.CompletedProcess(cmd, 0, stdout="inactive\n",
                                                   stderr="")
            return real_run(cmd, **kw)

        monkeypatch.setattr(run_gate.os.path, "isdir", lambda p: True)
        monkeypatch.setattr(run_gate.subprocess, "run", spy)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.verify_slice_loaded("dev-background.slice")
        assert "not LoadState=loaded" in str(exc.value)

    def test_loadstate_probe_output_must_be_loaded(self, tmp_path, monkeypatch):
        real_run = subprocess.run

        def spy(cmd, **kw):
            if cmd and cmd[0] == "systemctl":
                return subprocess.CompletedProcess(cmd, 0,
                                                   stdout="LoadState=loaded\n",
                                                   stderr="")
            return real_run(cmd, **kw)

        monkeypatch.setattr(run_gate.os.path, "isdir", lambda p: True)
        monkeypatch.setattr(run_gate.subprocess, "run", spy)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.verify_slice_loaded("s.slice")  # --value prints bare state;
        assert "not LoadState=loaded" in str(exc.value)  # labeled form != loaded

    def test_loadstate_skipped_without_systemd(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("systemctl must not be invoked without systemd")
        monkeypatch.setattr(run_gate.subprocess, "run", boom)
        run_gate.verify_slice_loaded("any.slice")  # this container: no systemd


MOUNTINFO = "\n".join([
    "30 29 0:28 / / rw,nosuid - overlay overlay rw",
    "99 30 253:0 /home/vb/volkb79-2/vbpub /workspaces/vbpub rw - ext4 /dev/vda rw",
    "98 30 253:0 /home/vb/volkb79-2/vbpub\\040(sp) /mnt/weird rw - ext4 /dev/vda rw",
])


class TestPhysicalPath:
    def test_maps_through_longest_bind_mount(self):
        got = run_gate.physical_path(Path("/workspaces/vbpub/.worktrees/w1"),
                                     MOUNTINFO, container=True)
        assert got == Path("/home/vb/volkb79-2/vbpub/.worktrees/w1")

    def test_octal_escapes_decoded(self):
        got = run_gate.physical_path(Path("/mnt/weird/sub"), MOUNTINFO,
                                     container=True)
        assert got == Path("/home/vb/volkb79-2/vbpub (sp)/sub")

    def test_root_overlay_never_used_as_mapping(self):
        with pytest.raises(run_gate.GateError):
            run_gate.physical_path(Path("/etc/passwd"), MOUNTINFO, container=True)

    def test_outside_container_identity(self):
        p = Path("/some/where")
        assert run_gate.physical_path(p, "", container=False) == p


# ---------------------------------------------------------------------------
# O3 — argv CONSTRUCTION pinned against the fake docker (LISTS, not strings)
# ---------------------------------------------------------------------------

class TestArgvConstruction:
    def test_command_lane_full_docker_argv(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", "/wt/tree")
        assert proc.returncode == 0, proc.stderr
        run_call = docker_runs(log)[0]
        idx = lambda flag: run_call.index(flag)  # noqa: E731
        assert run_call[0:3] == ["run", "-d", "--name"]
        assert run_call[idx("--name") + 1].startswith("run-gate-repo-suite-")
        assert run_call[idx("--cgroup-parent") + 1] == "dev-background.slice"
        assert run_call[idx("-e") + 1] == f"{CGROUP_VAR}=dev-background.slice"
        # REAL derivation (subprocess can't see module monkeypatches): /tmp is
        # bind-mounted here, so physical_path must resolve it via mountinfo.
        phys = str(run_gate.physical_path(repo))
        mounts = sorted(run_call[i + 1] for i, p in enumerate(run_call) if p == "-v")
        assert mounts == [f"{phys}:{phys}", f"{phys}:{repo}"]  # dual mount
        assert "--rm" not in run_call
        assert run_call[-4:-2] == ["tester-unified:local", "bash"]
        inner = run_call[-1]
        assert inner.startswith("set -euo pipefail && ")
        assert "git config --global safe.directory '*'" in inner
        assert "cd /wt/tree/proj && echo gate-ran" in inner  # {worktree} substituted
        # transparency: the docker argv is printed, never buried
        assert "docker argv:" in proc.stdout

    def test_assay_lane_inner_shape_and_verdict_line(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"

            [lanes.ciu]
            kind = "assay"
            assay_lane = "ciu"
            environment = "tester-unified"
            assay_command = ["/opt/tester-venv/bin/python",
                             "tools/assay/assay-2.1.0.pyz"]

            [lanes.ciu.pins.assay]
            version = "2.1.0"
            sha256 = "tools/assay/assay-2.1.0.pyz.sha256"
        """)
        log = log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "ciu")
        assert proc.returncode == 0, proc.stderr
        inner = docker_runs(log)[0][-1]
        # pin verified FROM the pin's own directory, bare filename (P07 trap)
        assert f"(cd {proj}/tools/assay && sha256sum -c assay-2.1.0.pyz.sha256)" \
            in inner
        assert f"cd {proj}" in inner          # assay runs from the PROJECT dir
        assert "mkdir -p .assay" in inner
        assert "--file assay.toml --verdict-json .assay/verdict-ciu.json" in inner
        assert "/opt/tester-venv/bin/python tools/assay/assay-2.1.0.pyz run ciu" \
            in inner
        assert f"verdict artifact: {proj}/.assay/verdict-ciu.json" in proc.stdout

    def test_exit_status_passthrough_no_masking(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch, wait_code=7)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 7
        assert "exit 7" in proc.stdout

    def test_wait_garbage_refuses_to_guess(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch, wait_code="garbage")
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3
        assert "refusing to guess" in proc.stderr

    def test_docker_run_failure_cleans_up_and_fails_loud(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text().replace('run) echo "fake-container-id" ;;',
                                        'run) echo "docker: bad flag" >&2; exit 125 ;;')
        shim.write_text(body)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3
        assert "docker run failed" in proc.stderr
        rm_calls = [l.split() for l in log.read_text().splitlines()
                    if l.split()[:1] == ["rm"]]
        assert rm_calls  # cleanup attempted despite the failed start

    def test_memory_flag_passed(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.e]
            image = "img:1"
            [lanes.big]
            kind = "command"
            environment = "e"
            argv = ["bash", "-c", "true"]
            memory = "4g"
            clean_tree = false
        """)
        log = log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "big")
        assert proc.returncode == 0, proc.stderr
        run_call = docker_runs(log)[0]
        assert run_call[run_call.index("--memory") + 1] == "4g"

    # -- wiring guards: the reviewer's mutation probes showed these three
    # load-bearing behaviors survived deletion with zero reds. Each test
    # kills one such regression class by observing SIDE EFFECTS of the lane
    # path itself (in-process so module patches apply).

    def _in_process_lane(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py"), "suite"])
        return proj, log

    def test_lane_path_calls_loadstate_guard_where_systemd_reachable(
            self, tmp_path, monkeypatch):
        """R-11 WIRING: deleting verify_slice_loaded from the lane path must
        red here — the guard protects against fail-open transient slices."""
        proj, log = self._in_process_lane(tmp_path, monkeypatch)
        real_run = subprocess.run

        systemctl_cmds = []

        def spy(cmd, **kw):
            if cmd and cmd[0] == "systemctl":
                systemctl_cmds.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="loaded\n",
                                                   stderr="")
            return real_run(cmd, **kw)

        # Make ONLY /run/systemd/system appear reachable — a blanket
        # isdir->True would make shutil.which reject the docker shim
        # (_access_check skips anything isdir() calls a directory).
        real_isdir = run_gate.os.path.isdir
        monkeypatch.setattr(run_gate.os.path, "isdir",
                            lambda p: p == "/run/systemd/system"
                            or real_isdir(p))
        monkeypatch.setattr(run_gate.subprocess, "run", spy)
        code = run_gate.main(["suite"])
        assert code == 0
        assert systemctl_cmds, "LoadState probe never ran on the lane path"
        assert "--property=LoadState" in systemctl_cmds[0]

    def test_lane_streams_logs_with_follow(self, tmp_path, monkeypatch):
        """R-17 WIRING: `docker logs -f` must be invoked (streaming till exit);
        a no-op logs call would bury the job's output."""
        proj, log = self._in_process_lane(tmp_path, monkeypatch)
        code = run_gate.main(["suite"])
        calls = [l.split("\x1f") for l in log.read_text().splitlines()]
        logs_calls = [c for c in calls if c and c[0] == "logs"]
        assert code == 0
        assert any(c[:2] == ["logs", "-f"] for c in logs_calls), \
            f"no `logs -f` call recorded: {logs_calls}"

    def test_successful_run_removes_its_container(self, tmp_path, monkeypatch):
        """R-15 WIRING: the finally-cleanup must fire on SUCCESS too — only
        pinning failed-start cleanup let every green run leak a container."""
        proj, log = self._in_process_lane(tmp_path, monkeypatch)
        code = run_gate.main(["suite"])
        calls = [l.split("\x1f") for l in log.read_text().splitlines()]
        run_call = next(c for c in calls if c and c[0] == "run")
        name = run_call[run_call.index("--name") + 1]
        rm_calls = [c for c in calls if c and c[0] == "rm"]
        assert code == 0
        assert any(c[1:3] == ["-f", name] for c in rm_calls), \
            f"container {name} not cleaned up: {rm_calls}"


# ---------------------------------------------------------------------------
# R-13/R-14 — tree resolution + clean tree
# ---------------------------------------------------------------------------

class TestTreeResolution:
    def test_plain_repo_toplevel_is_repo(self, tmp_path):
        repo = make_repo(tmp_path)
        got_repo, wt, toplevel = run_gate.resolve_repo_and_worktree(repo, None)
        assert got_repo == repo and wt == repo and toplevel == repo

    def test_linked_worktree_resolves_common_owner(self, tmp_path):
        repo = make_repo(tmp_path)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        got_repo, got_wt, toplevel = run_gate.resolve_repo_and_worktree(wt, None)
        assert got_repo == repo   # mounts must cover the MAIN checkout
        assert got_wt == wt       # judged tree = the worktree itself
        assert toplevel == wt     # relocation base = invocation toplevel

    def test_worktree_override_honored(self, tmp_path):
        repo = make_repo(tmp_path)
        _, wt, toplevel = run_gate.resolve_repo_and_worktree(repo, "/elsewhere/tree")
        assert wt == Path("/elsewhere/tree")


class TestEffectiveTreeExecution:
    """RG-15: --worktree relocates ALL user-declared execution paths into the
    judged tree — the invocation checkout is never judged by side effect.
    Exit-status-only assertions are insufficient here: these pin WHERE the
    lane executes and WHERE artifacts land."""

    ASSAY_CFG = """\
        schema_version = 1

        [environments.tester-unified]
        image = "tester-unified:local"

        [lanes.ciu]
        kind = "assay"
        assay_lane = "ciu"
        environment = "tester-unified"
        assay_command = ["/opt/tester-venv/bin/python",
                         "tools/assay/assay-2.1.0.pyz"]

        [lanes.ciu.pins.assay]
        version = "2.1.0"
        sha256 = "tools/assay/assay-2.1.0.pyz.sha256"
    """

    def _repo_with_worktree(self, tmp_path, config: str | None = None):
        repo = make_repo(tmp_path)
        proj = make_project(repo, config or self.ASSAY_CFG)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        return repo, proj, wt

    def test_assay_lane_judges_selected_worktree(self, tmp_path, monkeypatch):
        repo, proj, wt = self._repo_with_worktree(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "ciu", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        inner = docker_runs(log)[0][-1]
        # cd target AND pin verification relocated INTO the selected tree…
        assert f"cd {wt}/proj" in inner
        assert f"(cd {wt}/proj/tools/assay && " \
            f"sha256sum -c assay-2.1.0.pyz.sha256)" in inner
        # …and the invocation checkout appears NOWHERE in the judged command
        # (controlled wrong implementation: pre-RG-15 built this exact string)
        assert str(proj) not in inner
        # verdict location follows the judged tree (R-18 discipline)
        assert f"verdict artifact: {wt}/proj/.assay/verdict-ciu.json" \
            in proc.stdout

    def test_exec_assay_lane_judges_selected_worktree(self, tmp_path, monkeypatch):
        repo, proj, wt = self._repo_with_worktree(tmp_path, """\
            schema_version = 1
            [environments.runner]
            image = "r:latest"
            mode = "exec"

            [lanes.a]
            kind = "assay"
            environment = "runner"
            assay_lane = "mock"
            assay_command = ["assay"]
        """)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu global")
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in',
                            'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "a", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        inner = docker_execs(log)[0][-1]
        assert f"cd {wt}/proj" in inner
        assert str(proj) not in inner

    def test_host_lane_cwd_is_the_effective_project_dir(self, tmp_path):
        repo, proj, wt = self._repo_with_worktree(tmp_path, """\
            schema_version = 1

            [lanes.where]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "pwd"]
            clean_tree = false
        """)
        proc = run_tool(proj, "where", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        assert str(wt / "proj") in proc.stdout  # pwd ran INSIDE the judged tree

    def test_no_override_keeps_project_dir_exactly(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        assert run_gate.effective_project_dir(proj, repo, repo) == proj

    def test_project_outside_its_toplevel_refuses_relocation(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        repo_a = make_repo(tmp_path / "a")
        repo_b = make_repo(tmp_path / "b")
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.effective_project_dir(repo_b, repo_a, Path("/wt/tree"))
        assert "outside its git toplevel" in str(exc.value)


class TestCleanTree:
    def test_dirty_tree_refused_with_count_and_escape(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "dirt.txt").write_text("uncommitted\n")
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.check_clean_tree(repo)
        assert "dirty" in str(exc.value) and "--allow-dirty" in str(exc.value)

    def test_clean_tree_passes_silently(self, tmp_path):
        repo = make_repo(tmp_path)
        run_gate.check_clean_tree(repo)

    def test_dirty_worktree_blocks_lane_without_flag(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.tester-unified]
            image = "tester-unified:local"
            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo gate-ran"]
            clean_tree = true
        """)
        log = fake_docker(tmp_path, monkeypatch)
        (repo / "dirt.txt").write_text("x\n")
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "dirty" in proc.stderr
        log = Path(tmp_path / "docker-calls.log")
        assert "gate-ran" not in log.read_text()

    def test_allow_dirty_bypasses_check(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.tester-unified]
            image = "tester-unified:local"
            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo gate-ran"]
            clean_tree = true
        """)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        (repo / "dirt.txt").write_text("x\n")
        proc = run_tool(proj, "suite", "--allow-dirty")
        assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# host lanes + central defaults inheritance
# ---------------------------------------------------------------------------

class TestHostLane:
    def test_host_lane_runs_argv_directly_exit_passthrough(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.smoke]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "exit 5"]
            clean_tree = false
        """)
        proc = run_tool(proj, "smoke")
        assert proc.returncode == 5
        assert "built-in 'host'" in proc.stdout

    def test_host_lane_substitutes_worktree(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.echo]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "test -n '{worktree}' && test -d '{worktree}'"]
            clean_tree = false
        """)
        proc = run_tool(proj, "echo")
        assert proc.returncode == 0, proc.stderr


class TestCentralDefaults:
    CENTRAL = """\
        schema_version = 1

        [environments.tester-unified]
        image = "tester-unified:local"
    """

    def _central(self, repo: Path):
        (repo / "run-gate.toml").write_text(self.CENTRAL)
        commit_all(repo, "central env facts")

    def test_ancestor_provides_environment(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        self._central(repo)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "true"]
            clean_tree = false
        """)
        log = log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "tester-unified:local" in docker_runs(log)[0]
        assert "central" in proc.stdout  # source named transparently

    def test_project_shadows_central_by_name(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        self._central(repo)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.tester-unified]
            image = "project-override:2"

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "true"]
            clean_tree = false
        """)
        log = log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "project-override:2" in docker_runs(log)[0]

    def test_nearest_ancestor_wins(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        (repo / "run-gate.toml").write_text(self.CENTRAL)
        nested = repo / "group"
        nested.mkdir()
        (nested / "run-gate.toml").write_text("""\
            schema_version = 1
            [environments.tester-unified]
            image = "nearest:9"
        """)
        commit_all(repo, "two-level centrals")
        proj = make_project(nested, """\
            schema_version = 1
            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "true"]
            clean_tree = false
        """)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "nearest:9" in docker_runs(Path(tmp_path / "docker-calls.log"))[0]


# ---------------------------------------------------------------------------
# misc units
# ---------------------------------------------------------------------------

def test_substitute_worktree_replaces_all():
    got = run_gate.substitute_worktree(["cd {worktree}/a", "--base {worktree}"],
                                       Path("/wt"))
    assert got == ["cd /wt/a", "--base /wt"]


def test_budget_advisory_printed_not_enforced(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    proj = make_project(repo, """\
        schema_version = 1
        [environments.tester-unified]
        image = "tester-unified:local"
        [lanes.suite]
        kind = "command"
        environment = "tester-unified"
        budget = "20m"
        argv = ["bash", "-c", "true"]
        clean_tree = false
    """)
    log = fake_docker(tmp_path, monkeypatch)
    monkeypatch.setattr(run_gate, "physical_path",
                        lambda p, **k: Path("/phys"))
    # in-process main() discovers the project from sys.argv[0] — pin it
    monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py"), "suite"])
    code = run_gate.main(["suite"])
    out = capsys.readouterr().out
    assert code == 0
    assert "budget 20m (advisory)" in out


def test_no_stdlib_violations():
    """Anti-goal: non-stdlib imports. Parse the import table."""
    src = _TOOL.read_text()
    imports = [l.split()[1].split(".")[0] for l in src.splitlines()
               if l.startswith("import ") or l.startswith("from ")]
    allowed = {"argparse", "os", "re", "shlex", "shutil", "subprocess", "sys",
               "time", "tomllib", "pathlib", "fcntl"}  # fcntl: stdlib, Linux-only (RG-20 locks)
    assert set(imports) <= allowed, f"non-stdlib/unplanned imports: {imports}"


# ---------------------------------------------------------------------------
# Rev 2 — exec mode, extra mounts, safe.directory scope
# ---------------------------------------------------------------------------

EXEC_LANE = """\
    schema_version = 1

    [environments.runner]
    image = "runner:latest"
    mode = "exec"

    [lanes.suite]
    kind = "command"
    environment = "runner"
    # {worktree} token present: exec-mode command lanes invoked WITH
    # --worktree must actually honor it (RG-1 validator).
    argv = ["bash", "-c", "cd {worktree}/proj && echo hello"]
    clean_tree = false
"""


class TestExecMode:
    def _setup_repo(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n"
        )
        commit_all(repo, "ciu config")
        return repo, proj

    def test_exec_mode_resolves_container_from_ciu_global(self, tmp_path, monkeypatch):
        repo, proj = self._setup_repo(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        calls = log.read_text().splitlines()
        exec_calls = docker_execs(log)
        assert len(exec_calls) == 1
        call = exec_calls[0]
        idx = lambda flag: call.index(flag)
        assert str(repo) in call
        assert "bash" in call

    def test_exec_mode_fails_when_runner_not_running(self, tmp_path, monkeypatch):
        repo, proj = self._setup_repo(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) : ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 2
        assert "not running" in proc.stderr
        # RG-6: the remedy names the ciu lifecycle AND the config file used.
        assert "ciu up" in proc.stderr
        assert "ciu.global.toml" in proc.stderr

    def test_exec_mode_declared_name_refusal_prescribes_project_authority(
            self, tmp_path, monkeypatch):
        # RG-6 oracle: a dstdns-shaped project (declared container_name, no
        # ciu.global.toml anywhere) must NEVER be told to run a ciu command.
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace('mode = "exec"',
                                'mode = "exec"\ncontainer_name = "custom-name"')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) : ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 2
        assert "declared container_name" in proc.stderr
        assert "deployment authority" in proc.stderr
        assert "ciu" not in proc.stderr

    def test_exec_mode_fails_without_ciu_config_or_declared_name(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 2
        assert "container_name" in proc.stderr
        assert "ciu.global.toml" in proc.stderr

    def test_exec_mode_declared_name_wins(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace('mode = "exec"', 'mode = "exec"\ncontainer_name = "custom-name"')
        proj = make_project(repo, cfg)
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "custom-name" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        calls = log.read_text().splitlines()
        exec_calls = docker_execs(log)
        assert "custom-name" in exec_calls[0]


class TestExtraMounts:
    def _simple_ephemeral(self, tmp_path):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [environments.t]
            image = "t:latest"
            [lanes.s]
            kind = "command"
            environment = "t"
            argv = ["true"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        return repo, proj

    def test_extra_mounts_appended_to_ephemeral_lanes(self, tmp_path, monkeypatch):
        repo, proj = self._simple_ephemeral(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_EXTRA_MOUNTS",
                          "/var/run/docker.sock=/var/run/docker.sock")
        monkeypatch.setattr(run_gate, "physical_path", lambda p, **k: Path("/phys/host"))
        proc = run_tool(proj, "s")
        assert proc.returncode == 0, proc.stderr
        run_call = docker_runs(log)[0]
        mounts = sorted(run_call[i + 1] for i, p in enumerate(run_call) if p == "-v")
        assert "/var/run/docker.sock:/var/run/docker.sock" in mounts

    def test_extra_mounts_malformed_entry_rejected(self, tmp_path, monkeypatch):
        repo, proj = self._simple_ephemeral(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_EXTRA_MOUNTS", "no-equals-sign")
        monkeypatch.setattr(run_gate, "physical_path", lambda p, **k: Path("/phys/host"))
        proc = run_tool(proj, "s")
        assert proc.returncode == 2
        assert "host=container" in proc.stderr


def test_safe_directory_uses_git_config_global_env():
    inner = run_gate.build_command_inner(
        {"argv": ["echo"]}, Path("/wt"))
    assert "GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig" in inner


class TestExecInnerWiring:
    """Reviewer's hollow-wiring probe: the exec path must carry the REAL
    inner command, not a stub. These tests fail if build_command_inner is
    replaced with 'true' in run_exec_lane."""

    def test_exec_inner_contains_substituted_argv(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace(
            '["bash", "-c", "cd {worktree}/proj && echo hello"]',
            '["bash", "-c", "cd {worktree} && echo gate-ran"]')
        proj = make_project(repo, cfg)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu")
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        calls = log.read_text().splitlines()
        exec_calls = docker_execs(log)
        inner = exec_calls[0][-1]
        assert "echo gate-ran" in inner and "GIT_CONFIG_GLOBAL" in inner

    def test_exec_assay_lane_builds_assay_inner_not_crash(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [environments.runner]
            image = "r:latest"
            mode = "exec"
            [lanes.a]
            kind = "assay"
            environment = "runner"
            assay_lane = "mock"
            assay_command = ["assay"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu")
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "a", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        calls = log.read_text().splitlines()
        exec_calls = docker_execs(log)
        inner = exec_calls[0][-1]
        assert "--file assay.toml" in inner
        assert "GIT_CONFIG_GLOBAL" in inner


def test_assay_inner_has_git_config_global():
    inner = run_gate.build_assay_inner(
        {"assay_lane": "x", "assay_command": ["assay"], "pins": {}},
        Path("/proj"))
    assert "export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig" in inner


class TestPinVersionVerify:
    """RG-4: a declared pins.*.version is a CLAIM the artifact must satisfy,
    verified in-lane via `<assay_command> --version` — never provenance
    theater. Controlled wrong implementation (the pre-fix no-check) fails
    the first test."""

    def _lane(self, version):
        pin = {"sha256": "tools/assay/assay.pyz.sha256"}
        if version is not None:
            pin["version"] = version
        return {"assay_lane": "x", "assay_command": ["./tools/assay/assay.pyz"],
                "pins": {"assay": pin}}

    def test_declared_version_probed_in_lane(self):
        inner = run_gate.build_assay_inner(self._lane("2.1.0"), Path("/proj"))
        assert "./tools/assay/assay.pyz --version" in inner
        assert "*2.1.0*" in inner and "version mismatch" in inner

    def test_undeclared_version_never_probes(self):  # controlled wrong impl
        inner = run_gate.build_assay_inner(self._lane(None), Path("/proj"))
        assert "--version" not in inner

    def _live_proj(self, tmp_path, reported_version: str) -> Path:
        """Project whose fake pinned artifact reports `reported_version`."""
        proj = tmp_path / "proj"
        (proj / "tools/assay").mkdir(parents=True)
        artifact = proj / "tools/assay/assay.pyz"
        artifact.write_text("#!/bin/sh\n"
                            f'case "$1" in --version) echo "{reported_version}";; '
                            '*) exit 0;; esac\n')
        artifact.chmod(artifact.stat().st_mode | stat.S_IEXEC)
        import hashlib
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (proj / "tools/assay/assay.pyz.sha256").write_text(f"{digest}  assay.pyz\n")
        return proj

    def _run_inner(self, tmp_path, declared_version, reported_version):
        proj = self._live_proj(tmp_path, reported_version)
        inner = run_gate.build_assay_inner(self._lane(declared_version), proj)
        proc = subprocess.run(["bash", "-c", inner], cwd=proj,
                              capture_output=True, text=True)
        return proc

    def test_mismatched_version_refuses_naming_both_values(self, tmp_path):
        proc = self._run_inner(tmp_path, "2.1.0", "assay 9.9.9")
        assert proc.returncode != 0
        assert "version mismatch" in proc.stderr
        assert "2.1.0" in proc.stderr and "9.9.9" in proc.stderr

    def test_matching_version_runs_silently(self, tmp_path):
        proc = self._run_inner(tmp_path, "2.1.0", "assay 2.1.0")
        assert proc.returncode == 0, proc.stderr

    def test_empty_version_declaration_rejected(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"

            [lanes.ciu]
            kind = "assay"
            assay_lane = "ciu"
            environment = "tester-unified"
            assay_command = ["assay"]

            [lanes.ciu.pins.assay]
            version = ""
            sha256 = "x.sha256"
        """)
        proc = run_tool(proj, "--list")
        assert proc.returncode == 2
        assert "'version' must be a non-empty string" in proc.stderr


def test_extra_mounts_empty_element_rejected(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    proj = make_project(repo, SIMPLE_LANE)
    log = fake_docker(tmp_path, monkeypatch)
    monkeypatch.setenv("RUN_GATE_EXTRA_MOUNTS", "/a=/b::/c=/d")
    monkeypatch.setattr(run_gate, "physical_path", lambda p, **k: Path("/phys"))
    proc = run_tool(proj, "suite")
    assert proc.returncode == 2
    assert "empty element" in proc.stderr


class TestDualMountGuard:
    """RG-3: outside the cockpit namespace phys == repo would collapse both
    -v flags into one silent single mount; that state must be declared, not
    guessed. Controlled wrong implementation (pre-fix collapse) fails these."""

    def test_distinct_views_dual_mount_unchanged(self, tmp_path):
        repo = make_repo(tmp_path)
        phys = Path("/phys/host/root")
        assert run_gate.dual_mount_flags(repo, phys) == [
            "-v", f"{phys}:{phys}", "-v", f"{phys}:{repo}"]

    def test_collapsed_views_without_alias_refuse_loudly(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        monkeypatch.delenv(run_gate.MOUNT_ALIAS_ENV_VAR, raising=False)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.dual_mount_flags(repo, repo)
        assert "collapse" in str(exc.value)
        assert run_gate.MOUNT_ALIAS_ENV_VAR in str(exc.value)

    def test_alias_malformed_or_mismatched_rejected(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        for raw in ("no-equals", f"{repo}=", "=namespace", "/other/path=ns"):
            monkeypatch.setenv(run_gate.MOUNT_ALIAS_ENV_VAR, raw)
            with pytest.raises(run_gate.GateError) as exc:
                run_gate.dual_mount_flags(repo, repo)
            assert run_gate.MOUNT_ALIAS_ENV_VAR in str(exc.value)

    def test_declared_alias_yields_namespace_second_view(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        monkeypatch.setenv(run_gate.MOUNT_ALIAS_ENV_VAR,
                           f"{repo}=/workspaces/estate")
        flags = run_gate.dual_mount_flags(repo, repo)
        assert flags == ["-v", f"{repo}:{repo}",
                         "-v", f"{repo}:/workspaces/estate"]

    def test_bare_host_container_lane_refuses_then_runs_with_alias(
            self, tmp_path, monkeypatch, capsys):
        # In-PROCESS (main()) so the identity patch applies: a subprocess run
        # derives REAL views and this devcontainer bind-mounts /tmp distinct
        # from its namespace path, hiding the bare-host collapse.
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(run_gate, "physical_path", lambda p, **k: p)
        monkeypatch.delenv(run_gate.MOUNT_ALIAS_ENV_VAR, raising=False)
        assert run_gate.main(["suite"]) == 2
        assert "collapse" in capsys.readouterr().err
        monkeypatch.setenv(run_gate.MOUNT_ALIAS_ENV_VAR,
                           f"{repo}=/workspaces/vbpub")
        assert run_gate.main(["suite"]) == 0
        last_run = docker_runs(log)[-1]
        mounts = sorted(last_run[i + 1] for i, p in enumerate(last_run) if p == "-v")
        assert mounts == [f"{repo}:{repo}", f"{repo}:/workspaces/vbpub"]


class TestWorktreeCharsetGuard:
    """RG-5: {worktree} is substituted textually into consumer bash strings,
    so a tree whose resolved path carries whitespace/shell metacharacters is
    refused before any lane runs — every kind, uniformly."""

    def test_estate_real_paths_are_gate_safe(self):
        for p in ("/home/vb/volkb79-2/vbpub",
                  "/workspaces/vbpub/.worktrees/run-gate-rg-sweep",
                  "/tmp/pytest-of-vscode/pytest-1/test_x_0/repo.d"):
            run_gate.check_worktree_charset(Path(p))  # must not raise

    def test_metachars_rejected_at_helper(self):
        for p in ("/tmp/a b", "/tmp/$(x)", "/tmp/`x`",
                  "/tmp/a;b", "/tmp/x|y", "/tmp/'q'"):
            with pytest.raises(run_gate.GateError) as exc:
                run_gate.check_worktree_charset(Path(p))
            assert "gate-safe" in str(exc.value)

    def test_offending_characters_named_in_error(self):
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.check_worktree_charset(Path("/tmp/wei`rd"))
        assert "'`'" in str(exc.value)

    def test_space_path_refused_end_to_end_container_lane(self, tmp_path):
        base = tmp_path / "bad path"
        base.mkdir()
        repo = make_repo(base)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "gate-safe" in proc.stderr
        assert str(repo) in proc.stderr

    def test_space_path_refused_for_host_lane_too(self, tmp_path):
        base = tmp_path / "also bad"
        base.mkdir()
        repo = make_repo(base)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.suite]
            kind = "command"
            environment = "host"
            argv = ["echo", "hi"]
            """)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "gate-safe" in proc.stderr


class TestUsageEnvironmentContract:
    """RG-7: usage() exposes the environment contract, flag semantics, and
    per-lane metadata; --list stays 3-column machine-readable."""

    def test_help_documents_env_contract_and_flag_caveat(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        proc = run_tool(proj, "--help")
        assert proc.returncode == 0, proc.stderr
        for var in ("CGROUP_PARENT_DEV_BACKGROUND", "RUN_GATE_EXTRA_MOUNTS",
                    "RUN_GATE_MOUNT_ALIAS"):
            assert var in proc.stdout
        assert "still enforce assay's own clean-tree rule" in proc.stdout

    def test_table_shows_budget_memory_clean_tree_and_description(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.tester-unified]
            image = "tester-unified:local"
            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["true"]
            budget = "30m"
            memory = "2g"
            description = "unit suite in the unified tester"
            """)
        out = run_tool(proj, "--help").stdout
        assert "budget=30m (advisory)" in out
        assert "memory=2g" in out
        assert "clean_tree=true" in out
        assert "unit suite in the unified tester" in out

    def test_dirty_ok_lane_marked_false_in_usage(self, tmp_path):
        # SIMPLE_LANE already ships clean_tree = false — the FALSE marker
        # must be loud so a reader knows the default was consciously waived.
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        assert "clean_tree=FALSE" in run_tool(proj, "--help").stdout

    def test_empty_description_rejected(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE.replace(
            'environment = "tester-unified"',
            'environment = "tester-unified"\ndescription = ""'))
        proc = run_tool(proj, "--list")
        assert proc.returncode == 2
        assert "'description' must be a non-empty string" in proc.stderr

    def test_list_output_stays_three_column(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE.replace(
            'environment = "tester-unified"',
            'environment = "tester-unified"\nbudget = "5m"\ndescription = "x"'))
        proc = run_tool(proj, "--list")
        assert proc.returncode == 0
        assert proc.stdout.splitlines() == ["suite\tcommand\ttester-unified"]


class TestRequiredEnv:
    """RG-17/RG-19: declared inputs are verified by the GATE — preflight
    before execution, allowlist reachability for containers, names-only
    forwarding log, and an advisory drift sweep."""

    def _proj(self, tmp_path, extra_env="", extra_lane=""):
        repo = make_repo(tmp_path)
        cfg = f"""\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"
            forward_env = ["SCHEMA_GATE_PW"{', ' + extra_env if extra_env else ''}]

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo ran"]
            clean_tree = false
            required_env = ["SCHEMA_GATE_PW"]
            {extra_lane}
            """
        return make_project(repo, cfg)

    def test_missing_required_var_refuses_before_any_execution(self, tmp_path, monkeypatch):
        proj = self._proj(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.delenv("SCHEMA_GATE_PW", raising=False)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "SCHEMA_GATE_PW" in proc.stderr
        assert "requires" in proc.stderr
        assert log.read_text() == ""  # docker never invoked

    def test_empty_required_var_counts_as_absent(self, tmp_path, monkeypatch):
        proj = self._proj(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("SCHEMA_GATE_PW", "")
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "unset or empty" in proc.stderr

    def test_satisfied_requirement_runs_and_logs_names_not_values(
            self, tmp_path, monkeypatch):
        proj = self._proj(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("SCHEMA_GATE_PW", "sekret-value")
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "forwarded: SCHEMA_GATE_PW" in proc.stdout
        assert "sekret-value" not in proc.stdout  # names only, never values

    def test_declared_but_absent_is_visible_in_log(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"
            forward_env = ["PRESENT_VAR", "ABSENT_VAR"]

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo ran"]
            clean_tree = false
            """
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("PRESENT_VAR", "x")
        monkeypatch.delenv("ABSENT_VAR", raising=False)
        out = run_tool(proj, "suite").stdout
        assert "forwarded: PRESENT_VAR" in out
        assert "declared but ABSENT: ABSENT_VAR" in out

    def test_container_cannot_receive_unlisted_requirement(self, tmp_path, monkeypatch):
        # RG-17 oracle: required var missing from forward_env refuses at load.
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo ran"]
            required_env = ["SCHEMA_GATE_PW"]
            """
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("SCHEMA_GATE_PW", "sekret-value")  # present but unreachable
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "SCHEMA_GATE_PW" in proc.stderr
        assert "forward_env" in proc.stderr
        assert "tester-unified" in proc.stderr

    def test_host_lane_enforces_preflight_without_allowlist(self, tmp_path, monkeypatch):
        # host lanes have no forwarding boundary: presence check only.
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1

            [lanes.suite]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "echo ran"]
            required_env = ["HOST_ONLY_VAR"]
            """
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.delenv("HOST_ONLY_VAR", raising=False)
        refused = run_tool(proj, "suite")
        assert refused.returncode == 2
        assert "HOST_ONLY_VAR" in refused.stderr
        monkeypatch.setenv("HOST_ONLY_VAR", "x")
        assert run_tool(proj, "suite").returncode == 0

    def test_check_env_flags_uncovered_reference(self, tmp_path, monkeypatch):
        proj = self._proj(tmp_path)
        (proj / "tests").mkdir()
        (proj / "tests/test_x.py").write_text(
            "import os\n"
            "COVERED = os.environ['SCHEMA_GATE_PW']\n"
            "STRAY = os.environ.get('UNLISTED_VAR')\n")
        proc = run_tool(proj, "--check-env")
        assert proc.returncode == 0
        assert "UNLISTED_VAR" in proc.stdout
        assert "test_x.py:3" in proc.stdout
        assert "env-drift: $SCHEMA_GATE_PW" not in proc.stdout

    def test_check_env_quiet_when_everything_covered(self, tmp_path, monkeypatch):
        proj = self._proj(tmp_path)
        (proj / "tests").mkdir()
        (proj / "tests/test_x.py").write_text(
            "import os\nX = os.environ['SCHEMA_GATE_PW']\n")
        proc = run_tool(proj, "--check-env")
        assert proc.returncode == 0
        assert "0 uncovered reference(s)" in proc.stdout

    def test_invalid_required_env_entries_rejected(self, tmp_path):
        for i, bad in enumerate(('["9BAD"]', '["A B"]', '["X", "X"]',
                                 '"JUST_A_STRING"', '[""]')):
            base = tmp_path / f"case{i}"
            base.mkdir()
            repo = make_repo(base)
            cfg = SIMPLE_LANE.replace(
                'environment = "tester-unified"',
                f'environment = "tester-unified"\nrequired_env = {bad}')
            proj = make_project(repo, cfg)
            proc = run_tool(proj, "--list")
            assert proc.returncode == 2, bad
            assert "'required_env'" in proc.stderr


class TestConjunctionOverrideGuard:
    """RG-1: a conjunction/container command lane whose argv carries no
    {worktree} token cannot honor --worktree — refusing beats the silent
    false-PASS where sub-steps re-derive their own tree."""

    EPH_NO_TOKEN = """\
        schema_version = 1

        [environments.tester-unified]
        image = "tester-unified:local"

        [lanes.suite]
        kind = "command"
        environment = "tester-unified"
        argv = ["bash", "-c", "echo ran"]     # NOTE: no {worktree} token
        clean_tree = false
        """

    CONJ_FORWARDED = """\
        schema_version = 1

        [environments.tester-unified]
        image = "tester-unified:local"

        [lanes.suite]
        kind = "command"
        environment = "tester-unified"
        argv = ["bash", "-c", "cd {worktree}/proj && pwd"]
        clean_tree = false

        [lanes.gate]
        kind = "command"
        environment = "host"
        # RG-1 fixed shape: every sub-invocation carries the override.
        argv = ["bash", "-c", "./run-gate.py --worktree {worktree} suite"]
        clean_tree = false
        """

    CONJ_BARE = CONJ_FORWARDED.replace(
        "./run-gate.py --worktree {worktree} suite",
        "./run-gate.py suite")  # the pre-RG-1 shape — must refuse

    def _conjunction_proj(self, tmp_path, gate_config: str):
        base = tmp_path / "repo"
        base.mkdir()
        repo = make_repo(base)
        proj = repo / "proj"
        proj.mkdir()
        shutil.copy(_TOOL, proj / "run-gate.py")
        (proj / "run-gate.py").chmod(0o755)
        (proj / "run-gate.toml").write_text(textwrap.dedent(gate_config))
        commit_all(repo, "conjunction fixture")
        return repo, proj

    def test_ephemeral_lane_without_token_refuses_override(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.EPH_NO_TOKEN)
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", "/wt/tree")
        assert proc.returncode == 2
        assert "SILENTLY IGNORED" in proc.stderr
        assert "{worktree}" in proc.stderr
        assert "'suite'" in proc.stderr
        assert log.read_text() == ""  # docker never invoked

    def test_token_carrying_lane_still_accepts_override(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", "/wt/tree")
        assert proc.returncode == 0, proc.stderr

    def test_host_lane_without_token_still_accepts_override(self, tmp_path, monkeypatch):
        # host lanes relocate via cwd (R-19/R-21), so no token is needed.
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [lanes.suite]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "pwd"]
            clean_tree = false
            """)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        assert str(wt / "proj") in proc.stdout

    def test_conjunction_forwards_worktree_to_sublane(self, tmp_path, monkeypatch):
        repo, proj = self._conjunction_proj(tmp_path, self.CONJ_FORWARDED)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "gate", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        inner = docker_runs(log)[-1][-1]  # the SUB-lane's recorded container
        assert f"cd {wt}/proj" in inner   # sub-lane judged the OVERRIDE tree

    def test_bare_host_conjunction_safe_by_cwd_relocation(
            self, tmp_path, monkeypatch):
        # The pre-RG-1 BARE shape survives for HOST conjunction lanes only
        # structurally: the host runner's cwd relocates into the override
        # tree (R-19/R-21), so bare sub-calls derive the same toplevel. This
        # is why the guard targets CONTAINER command lanes (ephemeral/exec)
        # — their inner commands do NOT inherit that cwd.
        repo, proj = self._conjunction_proj(tmp_path, self.CONJ_BARE)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "gate", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        inner = docker_runs(log)[-1][-1]
        assert f"cd {wt}/proj" in inner  # judged W, not the invocation tree


class TestFailingContainerEvidence:
    """RG-12: failing containers leave readable logs behind — evidence is
    preserved BEFORE `rm -f`, the printed path must be readable afterwards,
    and a failed `docker run` shows a real stderr tail (not just one line)."""

    def _evidence_dir(self, tmp_path, monkeypatch) -> Path:
        ev = tmp_path / "evidence"
        monkeypatch.setenv(run_gate.EVIDENCE_DIR_ENV_VAR, str(ev))
        return ev

    def test_failed_lane_leaves_readable_log_after_container_gone(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = fake_docker(tmp_path, monkeypatch, wait_code="7")
        ev = self._evidence_dir(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 7  # the job's own status still passes through
        marker = "full container logs preserved at "
        assert marker in proc.stdout
        log_path = Path(proc.stdout.split(marker)[1].splitlines()[0].strip()
                        .rstrip(".;"))
        assert log_path.is_relative_to(ev)
        assert log_path.read_text() == "FAKE-LOGS-LINE\n"
        # container really was removed AFTER capture: rm recorded in the log
        calls = log.read_text().splitlines()
        assert any("rm" in c and "-f" in c for c in calls)

    def test_docker_run_failure_shows_multi_line_stderr_tail(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        self._evidence_dir(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text().replace(
            'run) echo "fake-container-id" ;;',
            'run) echo "Unable to find image locally" >&2;'
            ' echo "docker: pull access denied" >&2; exit 125 ;;')
        shim.write_text(body)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3  # infrastructure failure class
        assert "last stderr line(s)" in proc.stderr
        assert "pull access denied" in proc.stderr      # line 2 — not just last-line-only...
        assert "Unable to find image locally" in proc.stderr  # ...first line kept too

    def test_evidence_dir_env_var_controls_location(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch, wait_code="3")
        custom = tmp_path / "custom-evidence"
        monkeypatch.setenv(run_gate.EVIDENCE_DIR_ENV_VAR, str(custom))
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3
        assert str(custom) in proc.stdout
        files = list(custom.glob("*.log"))
        assert len(files) == 1 and files[0].read_text() == "FAKE-LOGS-LINE\n"


def test_exec_lane_passes_cgroup_env_to_container(tmp_path, monkeypatch):
    """Reviewer's cgroup-placement probe: exec-mode must forward
    CGROUP_PARENT_DEV_BACKGROUND into the persistent runner so nested
    docker run calls inherit the bounded slice."""
    repo = make_repo(tmp_path)
    proj = make_project(repo, EXEC_LANE)
    (repo / "ciu.global.toml").write_text(
        "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
    commit_all(repo, "ciu")
    log = fake_docker(tmp_path, monkeypatch)
    monkeypatch.setenv(CGROUP_VAR, "bounded.slice")
    shim = shim_dir_of(monkeypatch) / "docker"
    body = shim.read_text()
    body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
    shim.write_text(body)
    proc = run_tool(proj, "suite", "--worktree", str(repo))
    assert proc.returncode == 0, proc.stderr
    calls = docker_execs(log)
    assert calls, "no docker exec call recorded"
    call = calls[0]
    assert f"{CGROUP_VAR}=bounded.slice" in call


def test_exec_lane_forwards_declared_environment_values(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    config = EXEC_LANE.replace(
        '[environments.runner]\n    image = "runner:latest"\n    mode = "exec"',
        '[environments.runner]\n    image = "runner:latest"\n    mode = "exec"\n'
        '    forward_env = ["SCHEMA_GATE_DSN", "MOCK_MODE"]',
    )
    proj = make_project(repo, config)
    (repo / "ciu.global.toml").write_text(
        "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
    commit_all(repo, "ciu")
    log = fake_docker(tmp_path, monkeypatch)
    monkeypatch.setenv("SCHEMA_GATE_DSN", "postgresql://example/db")
    monkeypatch.setenv("MOCK_MODE", "true")
    shim = shim_dir_of(monkeypatch) / "docker"
    body = shim.read_text()
    body = body.replace('case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
    shim.write_text(body)
    proc = run_tool(proj, "suite", "--worktree", str(repo))
    assert proc.returncode == 0, proc.stderr
    call = docker_execs(log)[0]
    assert "SCHEMA_GATE_DSN=postgresql://example/db" in call
    assert "MOCK_MODE=true" in call


def test_environment_rejects_invalid_forward_env_name(tmp_path):
    repo = make_repo(tmp_path)
    config = SIMPLE_LANE.replace(
        '[environments.tester-unified]\n    image = "tester-unified:local"',
        '[environments.tester-unified]\n    image = "tester-unified:local"\n'
        '    forward_env = ["not-a-name"]',
    )
    proj = make_project(repo, config)
    proc = run_tool(proj, "--list")
    assert proc.returncode == 2
    assert "'forward_env' must be a list" in proc.stderr


# ---------------------------------------------------------------------------
# RG-10 — declared artifacts + evidence-path disclosure on every lane exit
# ---------------------------------------------------------------------------

class TestArtifactsDisclosure:
    """R-18 amendment: after EVERY run — any kind, any runner mode, success or
    failure — the gate says where the evidence landed. Assay lanes always
    disclose the verdict convention; declared `artifacts` add to it. Paths
    resolve against the EFFECTIVE project dir (R-21); `{worktree}` tokens in
    entries are substituted."""

    def test_ephemeral_command_lane_prints_declared_artifacts(self, tmp_path,
                                                              monkeypatch):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n",
            "    clean_tree = false\n"
            '    artifacts = ["out/coverage.json", "reports/x.txt"]\n')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert f"run-gate: artifact: {proj}/out/coverage.json" in proc.stdout
        assert f"run-gate: artifact: {proj}/reports/x.txt" in proc.stdout

    def test_assay_lane_dedupes_verdict_entry(self, tmp_path, monkeypatch):
        """An artifacts entry equal to the verdict convention is disclosed ONCE."""
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [environments.tester-unified]
            image = "tester-unified:local"
            [lanes.a]
            kind = "assay"
            environment = "tester-unified"
            assay_lane = "mock"
            assay_command = ["assay"]
            clean_tree = false
            artifacts = [".assay/verdict-mock.json"]
        """
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "a")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.count("run-gate: verdict artifact:") == 1
        assert "run-gate: artifact:" not in proc.stdout

    def test_artifacts_entries_get_worktree_substitution(self, tmp_path,
                                                         monkeypatch):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n",
            "    clean_tree = false\n"
            '    artifacts = ["{worktree}/proj/absolute-report.txt"]\n')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        # Absolute AFTER substitution — never re-joined under the project dir.
        assert f"run-gate: artifact: {repo}/proj/absolute-report.txt" in proc.stdout

    def test_exec_assay_lane_discloses_verdict(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [environments.runner]
            image = "r:latest"
            mode = "exec"
            [lanes.a]
            kind = "assay"
            environment = "runner"
            assay_lane = "mock"
            assay_command = ["assay"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu")
        fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in',
                            'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        proc = run_tool(proj, "a", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert f"run-gate: verdict artifact: {repo}/proj/.assay/verdict-mock.json" \
            in proc.stdout

    def test_host_lane_discloses_declared_artifacts(self, tmp_path):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [lanes.smoke]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "true"]
            clean_tree = false
            artifacts = ["smoke-result.txt"]
        """
        proj = make_project(repo, cfg)
        proc = run_tool(proj, "smoke")
        assert proc.returncode == 0, proc.stderr
        assert f"run-gate: artifact: {proj}/smoke-result.txt" in proc.stdout

    def test_failing_lane_still_discloses(self, tmp_path, monkeypatch):
        """Disclosure is unconditional — a FAILED lane names its evidence too
        (that is exactly when the reader needs the paths)."""
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n",
            "    clean_tree = false\n"
            '    artifacts = ["out/partial.json"]\n')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch, wait_code=7)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 7
        assert f"run-gate: artifact: {proj}/out/partial.json" in proc.stdout

    @pytest.mark.parametrize("bad", ['artifacts = "out.json"', "artifacts = []"])
    def test_invalid_artifacts_rejected(self, tmp_path, bad):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n", f"    clean_tree = false\n    {bad}\n")
        proj = make_project(repo, cfg)
        proc = run_tool(proj, "--list")
        assert proc.returncode == 2
        assert "'artifacts'" in proc.stderr


# ---------------------------------------------------------------------------
# RG-2 — pointer↔lane linkage: validate-pointers + estate certification
# ---------------------------------------------------------------------------

class TestPointerLinkage:
    """The dispatched artifact (the consumer pointer) is certified by a test:
    renaming a lane while pointers still name the old one goes RED HERE — at
    test time, not at daemon dispatch time."""

    def _estate(self, tmp_path, pointer: str, *, trove_name="nyxloom.toml"):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        trove_dir = proj / "nyxloom-trove"
        trove_dir.mkdir()
        doc = '[gates.tester-unified]\nargv = ["bash", "-c", ' \
              f'"{pointer.replace(chr(34), chr(92)+chr(34))}"]\n'
        (trove_dir / trove_name).write_text(doc)
        commit_all(repo, "trove")
        return repo, proj, trove_dir / trove_name

    def _validate(self, *args):
        return subprocess.run(
            [sys.executable, str(_TOOL), "validate-pointers", *[str(a) for a in args]],
            capture_output=True, text=True, cwd=str(RUN_GATE_DIR))

    CANONICAL = ("cd {worktree}/proj && exec ./run-gate.py "
                 "--worktree {worktree} suite")

    def test_valid_pointer_certifies(self, tmp_path):
        repo, proj, trove = self._estate(tmp_path, self.CANONICAL)
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 1 invocation(s)" in proc.stdout

    def test_renamed_lane_goes_red_at_test_time(self, tmp_path):
        """THE oracle: lane renamed in the SSOT, pointer still names it."""
        repo, proj, trove = self._estate(tmp_path, self.CANONICAL)
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace("[lanes.suite]", "[lanes.suiteX]"))
        commit_all(repo, "rename lane without touching pointer")
        proc = self._validate(trove)
        assert proc.returncode == 2
        assert "suite" in proc.stdout and "known lanes: suiteX" in proc.stdout

    def test_missing_worktree_forwarding_rejected(self, tmp_path):
        repo, proj, trove = self._estate(
            tmp_path, "cd {worktree}/proj && exec ./run-gate.py suite")
        proc = self._validate(trove)
        assert proc.returncode == 2
        assert "drops '--worktree {worktree}'" in proc.stdout

    def test_wrong_project_dir_rejected(self, tmp_path):
        repo, proj, trove = self._estate(
            tmp_path, "cd {worktree}/assayX && exec ./run-gate.py "
                      "--worktree {worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 2
        assert "has no run-gate.toml" in proc.stdout

    def test_noncanonical_cd_target_rejected(self, tmp_path):
        repo, proj, trove = self._estate(
            tmp_path, "cd $PWD && exec ./run-gate.py --worktree {worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 2
        assert "'{worktree}/<project-relative>'" in proc.stdout

    def test_no_pointer_document_is_clean(self, tmp_path):
        """srdm shape: gates that never invoke run-gate certify nothing."""
        repo, proj, trove = self._estate(
            tmp_path, "exec tools/gate.sh {worktree} unit")
        proc = self._validate(trove)
        assert proc.returncode == 0
        assert "no run-gate pointers" in proc.stdout

    def test_list_form_argv_is_joined_and_resolves_to_file_project(self, tmp_path):
        """cmru [steps.run-tests] shape: argv = ["./run-gate.py", "gate"] with
        no cd — the project resolves to the document's own directory."""
        repo, proj, _ = self._estate(tmp_path, self.CANONICAL)  # reuse lanes
        steps = proj / "consumer-steps.toml"
        steps.write_text('[[steps]]\nlabel = "x"\n'
                         'argv = ["./run-gate.py", "suite"]\ncwd = "."\n')
        proc = self._validate(steps, "--root", str(repo))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 1 invocation(s)" in proc.stdout

    def test_root_override_flag(self, tmp_path):
        repo, proj, trove = self._estate(tmp_path, self.CANONICAL)
        proc = self._validate(trove, "--root", str(tmp_path / "elsewhere"))
        assert proc.returncode == 2
        assert "is not a directory" in proc.stderr
        proc = self._validate(trove, "--root", str(repo))
        assert proc.returncode == 0, proc.stdout

    def test_invalid_toml_refused(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("not [ valid toml ===")
        proc = self._validate(bad, "--root", str(tmp_path))
        assert proc.returncode == 2
        assert "invalid TOML" in proc.stderr


class TestPointerLinkageEstate:
    """RG-2's real payoff: THIS checkout's actual consumer documents are
    certified against their SSOT lane tables on every suite run."""

    ESTATE_DOCS = sorted(RUN_GATE_DIR.parent.glob("*/nyxloom-trove/nyxloom.toml"))

    @pytest.mark.parametrize("doc", ESTATE_DOCS,
                             ids=[d.parent.parent.name for d in ESTATE_DOCS])
    def test_trove_pointers_name_real_lanes(self, doc):
        proc = subprocess.run(
            [sys.executable, str(_TOOL), "validate-pointers", str(doc)],
            capture_output=True, text=True, cwd=str(RUN_GATE_DIR))
        assert proc.returncode == 0, f"{doc}:\n{proc.stdout}{proc.stderr}"

    def test_cmru_release_step_names_a_real_lane(self):
        cmru_doc = RUN_GATE_DIR.parent / "cmru" / "cmru.toml"
        if not cmru_doc.is_file():
            pytest.skip("cmru not present in this checkout")
        proc = subprocess.run(
            [sys.executable, str(_TOOL), "validate-pointers", str(cmru_doc)],
            capture_output=True, text=True, cwd=str(RUN_GATE_DIR))
        assert proc.returncode == 0, f"{cmru_doc}:\n{proc.stdout}{proc.stderr}"


# ---------------------------------------------------------------------------
# RG-8 — --dry-run: rehearse every preflight, execute nothing
# ---------------------------------------------------------------------------

def _docker_argv_line(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if "docker argv:" in ln]
    assert len(lines) == 1, stdout
    return lines[0].split("docker argv:", 1)[1].strip()


class TestDryRun:
    def _proj(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        return repo, proj

    def test_container_lane_dry_run_prints_identical_argv_runs_nothing(
            self, tmp_path, monkeypatch):
        """THE oracle: no `docker run`, and the printed argv is what a live
        run would use (container NAME normalized out — it embeds pid/epoch)."""
        repo, proj = self._proj(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)

        live = run_tool(proj, "suite")
        assert live.returncode == 0, live.stderr
        live_line = _docker_argv_line(live.stdout)
        import re as _re
        live_norm = _re.sub(r"--name \S+", "--name N", live_line)

        log.write_text("")
        dry = run_tool(proj, "suite", "--dry-run")
        assert dry.returncode == 0, dry.stderr
        assert docker_runs(log) == [], "dry-run must not start a container"
        dry_line = _docker_argv_line(dry.stdout)
        assert _re.sub(r"--name \S+", "--name N", dry_line) == live_norm
        assert "DRY RUN" in dry.stdout

    def test_assay_lane_dry_run_discloses_no_verdict(self, tmp_path,
                                                     monkeypatch):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace('kind = "command"', 'kind = "assay"') \
            .replace('argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]',
                     'assay_lane = "mock"\n            assay_command = ["assay"]')
        proj = make_project(repo, cfg)
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--dry-run")
        assert proc.returncode == 0, proc.stderr
        assert docker_runs(log) == []
        assert "verdict artifact:" not in proc.stdout  # nothing ran, none landed
        assert "--file assay.toml" in _docker_argv_line(proc.stdout)

    def test_host_lane_dry_run_does_not_execute(self, tmp_path):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [lanes.smoke]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "exit 5"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        proc = run_tool(proj, "smoke", "--dry-run")
        # exit 5 would be the LIVE passthrough; a dry-run never runs it.
        assert proc.returncode == 0
        assert f"would run in {proj}" in proc.stdout
        assert "'exit' '5'" in proc.stdout or "exit 5" in proc.stdout

    def test_exec_lane_dry_run_rehearses_runner_preflight(self, tmp_path,
                                                          monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu")
        log = fake_docker(tmp_path, monkeypatch)

        # Runner DOWN: the refusal is rehearsed identically.
        down = run_tool(proj, "suite", "--dry-run")
        assert down.returncode == 2
        assert "is not running" in down.stderr

        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in',
                            'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        up = run_tool(proj, "suite", "--dry-run")
        assert up.returncode == 0, up.stderr
        assert docker_execs(log) == [], "dry-run must not exec anything"
        assert "DRY RUN" in up.stdout and "myproj-dev1-runner" in up.stdout

    def test_preflights_are_rehearsed_dirty_tree(self, tmp_path):
        repo = make_repo(tmp_path)
        # clean_tree defaults TRUE — SIMPLE_LANE opts out deliberately.
        proj = make_project(repo, SIMPLE_LANE.replace("clean_tree = false",
                                                      "clean_tree = true"))
        (proj / "uncommitted.txt").write_text("dirty\n")
        proc = run_tool(proj, "suite", "--dry-run")
        assert proc.returncode == 2
        assert "--allow-dirty" in proc.stderr
        proc = run_tool(proj, "suite", "--dry-run", "--allow-dirty")
        assert proc.returncode == 0, proc.stderr

    def test_preflights_are_rehearsed_required_env(self, tmp_path):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            '    clean_tree = false\n',
            '    clean_tree = false\n    required_env = ["SCHEMA_GATE_PW"]\n')
        proj = make_project(repo, cfg)
        proc = run_tool(proj, "suite", "--dry-run")
        assert proc.returncode == 2
        assert "SCHEMA_GATE_PW" in proc.stderr


# ---------------------------------------------------------------------------
# RG-20 — resource-aware admission: slice-RAM budget + shared-infra locks
# ---------------------------------------------------------------------------

def _fake_cgroupfs(tmp_path: Path, *, max_raw: str, current: str) -> Path:
    """A cgroupfs root exposing dev.slice/dev-background.slice numbers."""
    sl = tmp_path / "cgfs" / "dev.slice" / "dev-background.slice"
    sl.mkdir(parents=True, exist_ok=True)
    (sl / "memory.max").write_text(max_raw + "\n")
    (sl / "memory.current").write_text(current + "\n")
    return tmp_path / "cgfs"


GB = 1024 ** 3


class TestResourceAdmission:
    def _proj(self, tmp_path, resources: str):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE + (
            "    [lanes.suite.resources]\n"
            f"{textwrap.indent(textwrap.dedent(resources), '    ').rstrip()}\n")
        proj = make_project(repo, cfg)
        return repo, proj

    @pytest.mark.parametrize("snippet", [
        'memory = "512m"\nwat = 1',
        'cpu_weight = "high"',
        'cpu_weight = 0',
        'io_weight = 20000',
        'shared = ["ok", ""]',
        'shared = ["has space"]',
        'shared = ["dup", "dup"]',
    ])
    def test_invalid_resources_rejected(self, tmp_path, snippet):
        repo, proj = self._proj(tmp_path, snippet)
        proc = run_tool(proj, "--list")
        assert proc.returncode == 2

    def test_dual_memory_declaration_conflict(self, tmp_path):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n",
            '    clean_tree = false\n    memory = "1g"\n'
            "    [lanes.suite.resources]\n    memory = \"2g\"\n")
        proj = make_project(repo, cfg)
        proc = run_tool(proj, "--list")
        assert proc.returncode == 2
        assert "declare RAM once" in proc.stderr

    def test_memory_admission_refuses_when_over_budget(self, tmp_path,
                                                       monkeypatch):
        repo, proj = self._proj(tmp_path, 'memory = "512m"')
        cgfs = _fake_cgroupfs(tmp_path, max_raw=str(1 * GB),
                              current=str(int(0.9 * GB)))
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT", str(cgfs))
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "resource admission REFUSED" in proc.stderr
        assert "922MB of its 1024MB budget" in proc.stderr
        assert docker_runs(Path(tmp_path / "docker-calls.log")) == []

    def test_memory_admission_passes_and_caps_container(self, tmp_path,
                                                        monkeypatch):
        repo, proj = self._proj(tmp_path, 'memory = "512m"')
        cgfs = _fake_cgroupfs(tmp_path, max_raw=str(2 * GB),
                              current=str(256 * 1024 * 1024))
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT", str(cgfs))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "admission OK" in proc.stdout
        run_argv = docker_runs(log)[0]
        assert "--memory" in run_argv and "512m" in run_argv

    def test_unbounded_slice_warns_and_proceeds(self, tmp_path, monkeypatch):
        repo, proj = self._proj(tmp_path, 'memory = "512m"')
        cgfs = _fake_cgroupfs(tmp_path, max_raw="max", current="123")
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT", str(cgfs))
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "no derivable memory ceiling" in proc.stdout
        assert "admission by shared-infra rules only" in proc.stdout

    def test_missing_cgroupfs_warns_names_override(self, tmp_path, monkeypatch):
        repo, proj = self._proj(tmp_path, 'memory = "512m"')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "RUN_GATE_CGROUPFS_ROOT" in proc.stdout

    def test_lane_without_memory_declaration_not_accounted(self, tmp_path,
                                                           monkeypatch):
        repo, proj = self._proj(tmp_path, 'io_weight = 100')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "declares no resources.memory — not memory-accounted" \
            in proc.stdout

    def test_memory_swap_reaches_docker_argv(self, tmp_path, monkeypatch):
        repo, proj = self._proj(tmp_path, 'memory_swap = "16g"')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        run_argv = docker_runs(log)[0]
        assert "--memory-swap" in run_argv and "16g" in run_argv

    def test_shared_infra_serializes_concurrent_gates(self, tmp_path,
                                                      monkeypatch, capsys):
        """THE oracle: second gate on the same service name WAITS for the
        first; isolated names never meet."""
        svc = f"pg-{os.getpid()}"
        repo, proj = self._proj(tmp_path, f'shared = ["{svc}"]')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)

        lock_path = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(holder, fcntl.LOCK_EX)  # the "first gate" holds it

        result = {}

        def gate():
            result["rc"] = run_gate.main(["suite"])

        worker = threading.Thread(target=gate)
        worker.start()
        deadline = time.time() + 5
        while worker.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        assert worker.is_alive(), "gate must block while another gate holds " \
                                  f"the '{svc}' lock"
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)  # "first gate" finished -> waiter proceeds
        worker.join(timeout=15)
        assert not worker.is_alive()
        assert result["rc"] == 0
        out = capsys.readouterr().out
        assert f"waiting for shared infra '{svc}'" in out

    def test_shared_infra_dry_run_never_blocks(self, tmp_path, monkeypatch,
                                               capsys):
        svc = f"pg-dry-{os.getpid()}"
        repo, proj = self._proj(tmp_path, f'shared = ["{svc}"]')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)

        lock_path = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        holder = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            rc = run_gate.main(["suite", "--dry-run"])
            assert rc == 0
            out = capsys.readouterr().out
            assert "serialization planned for" in out
            assert "waiting for shared infra" not in out
        finally:
            os.close(holder)

    def test_host_lane_honors_shared_lock_without_memory_accounting(
            self, tmp_path, monkeypatch, capsys):
        svc = f"host-{os.getpid()}"
        cfg = """\
            schema_version = 1
            [lanes.smoke]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "true"]
            clean_tree = false
            [lanes.smoke.resources]
""" + textwrap.dedent(f"""\
            memory = "256m"
            shared = ["{svc}"]
""")
        repo = make_repo(tmp_path)
        proj = make_project(repo, cfg)
        monkeypatch.chdir(proj)
        rc = run_gate.main(["smoke"])
        assert rc == 0
        out = capsys.readouterr().out
        # host lane: no slice to account against — declaration stays advisory
        assert "admission" not in out or "not memory-accounted" not in out
        lock_path = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        probe = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released?
        finally:
            os.close(probe)
