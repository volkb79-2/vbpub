"""Unit suite for run-gate.py — construction pinned against a FAKE docker.

Construction is NOT acceptance: these pins prove argv SHAPE only (the P06/P07
lesson); live acceptance is oracle O4, run against real docker separately.
Every argv assertion compares the LIST, never a joined string.
"""

import os
import stat
import subprocess
import sys
import textwrap
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
               "time", "tomllib", "pathlib"}
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
    argv = ["echo", "hello"]
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
        assert "ciu up" in proc.stderr

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
        cfg = EXEC_LANE.replace('["echo", "hello"]',
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
