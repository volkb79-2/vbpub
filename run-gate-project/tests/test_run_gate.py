"""Unit suite for run-gate.py — construction pinned against a FAKE docker.

Construction is NOT acceptance: these pins prove argv SHAPE only (the P06/P07
lesson); live acceptance is oracle O4, run against real docker separately.
Every argv assertion compares the LIST, never a joined string.
"""

import atexit
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import tomllib
import warnings
import zipfile
from pathlib import Path

import pytest

RUN_GATE_DIR = Path(__file__).resolve().parent.parent
_TOOL = RUN_GATE_DIR / "run-gate.py"  # hyphenated filename: load via importlib

# run-gate-project now dogfoods itself (run-gate-project/run-gate.toml, the
# selftest lane), so RUN_GATE_DIR is no longer an config-free directory: per
# find_project_dir(), "directory of the invoked script path" is checked
# BEFORE the CWD fallback, so invoking _TOOL directly would always resolve
# THIS project's own config instead of a fixture's. Every fixture-driven
# subprocess invocation goes through this neutral symlink instead — the same
# indirection real external consumers use ("symlink's parent, never the
# target's dir"), living in a directory that never gets a run-gate.toml.
# The selftest lane now runs in HOST mode (real host /tmp, not a throwaway
# container filesystem), so this tempdir is cleaned up on interpreter exit
# rather than left to accumulate across every real gate run.
_TOOL_INVOKE_DIR = tempfile.mkdtemp(prefix="run-gate-test-invoke-")
_TOOL_INVOKE = Path(_TOOL_INVOKE_DIR) / "run-gate.py"
_TOOL_INVOKE.symlink_to(_TOOL)
atexit.register(shutil.rmtree, _TOOL_INVOKE_DIR, ignore_errors=True)

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


def fake_docker_executing(tmp_path, monkeypatch) -> Path:
    """A docker shim that RECORDS like fake_docker but also EXECUTES the
    inner `bash -c <script>` on the host for `run`/`exec`.

    RG-25/RG-26 probes are only meaningful if the script actually runs: the
    fitness check's whole content is `command -v` inside the environment, and
    the inventory's is a real `assay lanes --json`. A shim that echoed canned
    output would pin construction, not acceptance — the exact substitute-
    interpreter failure class this project exists to kill (README "an argv
    proves construction, not acceptance").
    """
    log = fake_docker(tmp_path, monkeypatch)
    shim = shim_dir_of(monkeypatch) / "docker"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\037' "$@" >> "{log}"
        printf '\\n' >> "{log}"
        case "$1" in
          run)
            # `-d` is the JUDGED lane (detached, R-15) — recorded, not run.
            # Anything else is an RG-25/RG-26 probe (`--rm`): really execute.
            case "$2" in
              -d) echo "fake-container-id" ;;
              *) for last; do :; done; exec bash -c "$last" ;;
            esac
            ;;
          exec)
            for last; do :; done
            exec bash -c "$last"
            ;;
          logs) echo "FAKE-LOGS-LINE" ;;
          wait) printf '0\\n' ;;
          rm) : ;;
          ps) printf 'probe-runner\\n' ;;
        esac
        exit 0
    """))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return log


def install_fake_assay(monkeypatch, body: str, name: str = "assay") -> Path:
    """A PATH-shim `assay` whose `lanes --json` output the test dictates."""
    path = shim_dir_of(monkeypatch) / name
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


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


def lane_runs(log: Path) -> list[list[str]]:
    """Only the JUDGED container runs. Since RG-26 an assay lane also issues a
    read-only `assay lanes --json` probe (`docker run --rm`), so index 0 of
    docker_runs() is no longer necessarily the lane — the detached form is
    what identifies it (R-15)."""
    return [call for call in docker_runs(log) if "-d" in call]


def lane_execs(log: Path) -> list[list[str]]:
    """Only the JUDGED `docker exec`s — never the RG-26 inventory probe."""
    return [call for call in docker_execs(log) if "lanes --json" not in call[-1]]


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
    return subprocess.run([sys.executable, str(_TOOL_INVOKE), *args],
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
        version = "3.1.0"
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
            version = "3.1.0"
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

    def test_project_lane_pin_sidecar_checked_symmetrically(self, tmp_path):
        """Review fix: a PROJECT lane's own pins get the same load-time
        existence check as inherited central lanes — previously the sha256
        verify failed only mid-run, inside the container."""
        repo = make_repo(tmp_path)
        cfg = textwrap.dedent("""\
            schema_version = 1
            [environments.tester-unified]
            image = "tester-unified:local"
            [lanes.pinned]
            kind = "assay"
            environment = "tester-unified"
            assay_lane = "gate"
            assay_command = ["./tools/assay.pyz"]
            clean_tree = false
            [lanes.pinned.pins.assay]
            version = "3.2.0"
            sha256 = "tools/assay.pyz.sha256"
        """)
        proj = make_project(repo, cfg)
        c, cp, central, cpath = run_gate.load_config(proj)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.merge_lanes(c.get("lanes", {}), central, proj, cp, cpath)
        assert "[lanes.pinned]" in str(exc.value)
        assert "does not exist in this project" in str(exc.value)

    def test_reserved_verb_lane_name_refused_at_load(self, tmp_path):
        """Review fix: a lane named like a CLI verb can never be invoked (the
        verb wins) and validate-pointers exempts the verbs — refuse the
        shadowing name at load."""
        repo = make_repo(tmp_path)
        proj = make_project(
            repo, SIMPLE_LANE.replace("[lanes.suite]", "[lanes.doctor]"))
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "'doctor' is reserved" in str(exc.value)

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
        log = fake_docker(tmp_path, monkeypatch)
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
        log = fake_docker(tmp_path, monkeypatch)
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
        assert "git config --global --replace-all safe.directory '*'" in inner
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
                             "tools/assay/assay-3.1.0.pyz"]

            [lanes.ciu.pins.assay]
            version = "3.1.0"
            sha256 = "tools/assay/assay-3.1.0.pyz.sha256"
        """)
        # Load-time sidecar existence is checked for project lanes too; the
        # docker shim only records argv, so a placeholder suffices.
        sidecar = proj / "tools/assay/assay-3.1.0.pyz.sha256"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("0" * 64 + "  assay-3.1.0.pyz\n")
        commit_all(repo, "vendor sidecar")
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        proc = run_tool(proj, "ciu")
        assert proc.returncode == 0, proc.stderr
        inner = lane_runs(log)[0][-1]
        # pin verified FROM the pin's own directory, bare filename (P07 trap)
        assert f"(cd {proj}/tools/assay && sha256sum -c assay-3.1.0.pyz.sha256)" \
            in inner
        assert f"cd {proj}" in inner          # assay runs from the PROJECT dir
        assert "mkdir -p .assay" in inner
        assert "--file assay.toml --verdict-json .assay/verdict-ciu.json" in inner
        assert "/opt/tester-venv/bin/python tools/assay/assay-3.1.0.pyz run ciu" \
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
        log = fake_docker(tmp_path, monkeypatch)
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
        log = fake_docker(tmp_path, monkeypatch)
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
        log = fake_docker(tmp_path, monkeypatch)
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
                         "tools/assay/assay-3.1.0.pyz"]

        [lanes.ciu.pins.assay]
        version = "3.1.0"
        sha256 = "tools/assay/assay-3.1.0.pyz.sha256"
    """

    def _repo_with_worktree(self, tmp_path, config: str | None = None):
        repo = make_repo(tmp_path)
        proj = make_project(repo, config or self.ASSAY_CFG)
        # The pin sidecar must exist in the JUDGED tree (load-time existence
        # check is symmetric for project lanes now); content is irrelevant —
        # the docker shim only records the assembled command.
        sidecar = proj / "tools/assay/assay-3.1.0.pyz.sha256"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("0" * 64 + "  assay-3.1.0.pyz\n")
        commit_all(repo, "vendor sidecar")
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
        inner = lane_runs(log)[0][-1]
        # cd target AND pin verification relocated INTO the selected tree…
        assert f"cd {wt}/proj" in inner
        assert f"(cd {wt}/proj/tools/assay && " \
            f"sha256sum -c assay-3.1.0.pyz.sha256)" in inner
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
        inner = lane_execs(log)[0][-1]
        assert f"cd {wt}/proj" in inner
        assert "--verdict-json" in inner        # the JUDGED exec, not the probe
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
        log = fake_docker(tmp_path, monkeypatch)
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
        log = fake_docker(tmp_path, monkeypatch)
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
               "time", "tomllib", "pathlib", "fcntl",  # fcntl: stdlib, Linux-only (RG-20 locks)
               "ast",   # ast: RG-23 helper-wrapped env reads in --check-env
               "json"}  # json: RG-25 `assay lanes --json` inventory
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


class TestWorktreeScopedContainerName:
    """RG-24: an exec-mode container's name is a fact about the JUDGED TREE.

    `repo` is the checkout owning the shared `.git` — the MAIN checkout for
    any linked worktree — so resolving a LIVE DEPLOYED container's name from
    it silently targets the main landscape's runner whenever a per-worktree
    deployment exists (dstdns "Mode-B"). The failure is partial and therefore
    believable: the inner `cd {worktree}` still collects the right FILES, only
    the container's own network/env are wrong. These tests pin the precedence
    in both directions, since only the differing case exposes the defect.
    """

    def _repo_with_worktree(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        return repo, proj, wt

    @staticmethod
    def _ps_returns(monkeypatch, *names: str) -> None:
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        listing = "\\n".join(names)
        body = body.replace('case "$1" in',
                            f'case "$1" in\n  ps) printf \'{listing}\\n\' ;;')
        shim.write_text(body)

    def test_worktree_own_ciu_global_wins_over_repo(self, tmp_path, monkeypatch):
        """The regression oracle: two DIFFERENT [deploy] tables, one at the
        shared-.git-owning repo and one in the judged worktree beneath it."""
        repo, proj, wt = self._repo_with_worktree(tmp_path)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'mainland'\nenvironment_tag = '98535c'\n")
        (wt / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'p147b'\nenvironment_tag = '8a6bc3'\n")
        log = fake_docker(tmp_path, monkeypatch)
        # BOTH containers exist and are running — the wrong one is reachable,
        # which is exactly why the pre-fix behaviour produced a green run.
        self._ps_returns(monkeypatch, "mainland-98535c-runner",
                         "p147b-8a6bc3-runner")
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        call = docker_execs(log)[0]
        assert "p147b-8a6bc3-runner" in call
        assert "mainland-98535c-runner" not in call
        # …and the disclosure names WHICH config decided it (R-05 mechanics).
        assert "judged worktree" in proc.stdout
        assert str(wt / "ciu.global.toml") in proc.stdout

    def test_worktree_without_own_config_falls_back_to_repo(
            self, tmp_path, monkeypatch):
        """Additive precedence, not a replacement: a plain (non-adopted)
        worktree keeps today's repo-relative resolution exactly."""
        repo, proj, wt = self._repo_with_worktree(tmp_path)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'mainland'\nenvironment_tag = '98535c'\n")
        log = fake_docker(tmp_path, monkeypatch)
        self._ps_returns(monkeypatch, "mainland-98535c-runner")
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        assert "mainland-98535c-runner" in docker_execs(log)[0]
        assert f"repo: {repo / 'ciu.global.toml'}" in proc.stdout

    def test_worktree_network_name_derivation_is_also_worktree_scoped(
            self, tmp_path, monkeypatch):
        """The network_name fallback derivation reads the same file."""
        repo, proj, wt = self._repo_with_worktree(tmp_path)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nnetwork_name = 'mainland-network'\n")
        (wt / "ciu.global.toml").write_text(
            "[deploy]\nnetwork_name = 'p147b-8a6bc3-network'\n")
        log = fake_docker(tmp_path, monkeypatch)
        self._ps_returns(monkeypatch, "mainland-runner", "p147b-8a6bc3-runner")
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        assert "p147b-8a6bc3-runner" in docker_execs(log)[0]
        assert "judged worktree" in proc.stdout

    def test_missing_config_names_both_candidate_paths(self, tmp_path, monkeypatch):
        """With worktree != repo the refusal must name BOTH files tried —
        naming only one sends the operator to render the wrong tree."""
        repo, proj, wt = self._repo_with_worktree(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 2
        assert str(wt / "ciu.global.toml") in proc.stderr
        assert str(repo / "ciu.global.toml") in proc.stderr
        assert "judged worktree" in proc.stderr

    # In-process unit oracles for the resolution itself. The end-to-end tests
    # above prove the WIRING (run_exec_lane passes the judged worktree, the
    # disclosure shows it); these pin the precedence function's own branches
    # without a subprocess in between, which is also what the diff-coverage
    # floor can actually measure.
    @staticmethod
    def _resolve(repo: Path, worktree: Path):
        return run_gate.resolve_container_name(
            "runner", {}, repo, worktree, "project /x/run-gate.toml")

    def test_unit_worktree_config_preferred(self, tmp_path):
        repo, wt = tmp_path / "repo", tmp_path / "repo" / ".worktrees" / "w1"
        wt.mkdir(parents=True)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'mainland'\nenvironment_tag = '98535c'\n")
        (wt / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'p147b'\nenvironment_tag = '8a6bc3'\n")
        name, src, remedy = self._resolve(repo, wt)
        assert name == "p147b-8a6bc3-runner"
        assert src.startswith("ciu.global.toml deploy.project_name+environment_tag")
        assert f"judged worktree: {wt / 'ciu.global.toml'}" in src
        assert str(wt / "ciu.global.toml") in remedy  # `ciu render` the RIGHT tree

    def test_unit_repo_config_used_when_worktree_has_none(self, tmp_path):
        repo, wt = tmp_path / "repo", tmp_path / "repo" / ".worktrees" / "w1"
        wt.mkdir(parents=True)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'mainland'\nenvironment_tag = '98535c'\n")
        name, src, _ = self._resolve(repo, wt)
        assert name == "mainland-98535c-runner"
        assert f"repo: {repo / 'ciu.global.toml'}" in src

    def test_unit_network_name_fallback_reports_scope(self, tmp_path):
        repo, wt = tmp_path / "repo", tmp_path / "repo" / ".worktrees" / "w1"
        wt.mkdir(parents=True)
        (wt / "ciu.global.toml").write_text(
            "[deploy]\nnetwork_name = 'p147b-8a6bc3-network'\n")
        name, src, _ = self._resolve(repo, wt)
        assert name == "p147b-8a6bc3-runner"
        assert "network_name stripped" in src and "judged worktree" in src

    def test_unit_no_config_anywhere_names_both_paths(self, tmp_path):
        repo, wt = tmp_path / "repo", tmp_path / "repo" / ".worktrees" / "w1"
        wt.mkdir(parents=True)
        with pytest.raises(run_gate.GateError) as exc:
            self._resolve(repo, wt)
        assert str(wt / "ciu.global.toml") in str(exc.value)
        assert str(repo / "ciu.global.toml") in str(exc.value)

    def test_unit_plain_checkout_message_names_one_path_once(self, tmp_path):
        """worktree == repo (no override, plain checkout): the refusal must
        not print the same path twice as if two trees were searched."""
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(run_gate.GateError) as exc:
            self._resolve(repo, repo)
        assert str(exc.value).count(str(repo / "ciu.global.toml")) == 1
        assert "judged worktree" not in str(exc.value)

    def test_declared_container_name_still_wins_over_both(
            self, tmp_path, monkeypatch):
        """RG-24 changes only the DERIVED path; an explicit declaration is
        still the top of the precedence chain."""
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace('mode = "exec"',
                                'mode = "exec"\ncontainer_name = "declared-one"')
        proj = make_project(repo, cfg)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        (wt / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'p147b'\nenvironment_tag = '8a6bc3'\n")
        log = fake_docker(tmp_path, monkeypatch)
        self._ps_returns(monkeypatch, "declared-one", "p147b-8a6bc3-runner")
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        assert "declared-one" in docker_execs(log)[0]


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


def test_safe_directory_write_survives_preexisting_entries(tmp_path):
    """RG-22: a plain `git config --global safe.directory '*'` (no
    --replace-all) fails with "cannot overwrite multiple values" the moment
    the isolated gitconfig at /tmp/run-gate-gitconfig already carries more
    than one safe.directory entry — reproduced from dstdns P126/P127's
    linked-worktree runners sharing a host. --replace-all makes the write
    succeed regardless of how many entries were already there.

    This test touches the REAL /tmp/run-gate-gitconfig path (the inner
    command hard-codes it, matching TestPinVersionVerify's live-subprocess
    pattern elsewhere in this file) and restores its prior content
    afterward — other tests in this suite write a single entry there too.
    """
    gitconfig = Path("/tmp/run-gate-gitconfig")
    original = gitconfig.read_bytes() if gitconfig.exists() else None
    try:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gitconfig)}
        for path in ("/some/project", "/other/project"):
            proc = subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", path],
                env=env, capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
        inner = run_gate.build_command_inner(
            {"argv": ["echo", "ok"]}, tmp_path)
        proc = subprocess.run(["bash", "-c", inner], cwd=tmp_path,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout
    finally:
        if original is None:
            gitconfig.unlink(missing_ok=True)
        else:
            gitconfig.write_bytes(original)


class TestExecDisclosure:
    """Review fix (R-05/R-28): exec lanes disclose mechanics exactly like
    ephemeral lanes do — the governing slice (naming-only: docker exec can
    neither place nor cap work) and the fully assembled redacted argv, live
    AND dry. Declarations that LOOK like governance on an exec lane draw a
    loud warning instead of silent no-op enforcement theater."""

    def _runner_up(self, tmp_path, monkeypatch) -> tuple[Path, Path, Path]:
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n"
        )
        commit_all(repo, "ciu config")
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in',
                            'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;')
        shim.write_text(body)
        return repo, proj, log

    def test_live_run_discloses_slice_and_redacted_argv(
            self, tmp_path, monkeypatch):
        repo, proj, _log = self._runner_up(tmp_path, monkeypatch)
        monkeypatch.setenv(CGROUP_VAR, "dev-background.slice")
        monkeypatch.setenv("SECRET_TOKEN", "hunter2")
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace(
            'mode = "exec"',
            'mode = "exec"\n    forward_env = ["SECRET_TOKEN"]'))
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert ("slice dev-background.slice "
                "($CGROUP_PARENT_DEV_BACKGROUND, naming-only)") in proc.stdout
        assert "docker argv:" in proc.stdout
        assert "SECRET_TOKEN=<redacted>" in proc.stdout
        assert "hunter2" not in proc.stdout + proc.stderr

    def test_dry_run_prints_same_argv_and_execs_nothing(
            self, tmp_path, monkeypatch):
        repo, proj, log = self._runner_up(tmp_path, monkeypatch)
        monkeypatch.setenv(CGROUP_VAR, "dev-background.slice")
        monkeypatch.setenv("SECRET_TOKEN", "hunter2")
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace(
            'mode = "exec"',
            'mode = "exec"\n    forward_env = ["SECRET_TOKEN"]'))
        proc = run_tool(proj, "suite", "--worktree", str(repo), "--dry-run")
        assert proc.returncode == 0, proc.stderr
        assert "docker argv:" in proc.stdout
        assert "SECRET_TOKEN=<redacted>" in proc.stdout
        assert "hunter2" not in proc.stdout + proc.stderr
        assert "DRY RUN — the argv above is what a live run would exec" \
            in proc.stdout
        # The `ps` preflight ran (rehearsed); no `exec` ever did.
        assert docker_execs(log) == []

    def test_declared_cgroup_slice_warns_naming_only(
            self, tmp_path, monkeypatch):
        repo, proj, _log = self._runner_up(tmp_path, monkeypatch)
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace(
            'mode = "exec"',
            'mode = "exec"\n    cgroup_slice = "dev-background.slice"'))
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert "WARNING: cgroup_slice on exec environment" in proc.stdout
        assert "naming-only" in proc.stdout
        assert "slice dev-background.slice (declared" in proc.stdout

    def test_resources_on_exec_lane_warned_not_enforced(
            self, tmp_path, monkeypatch):
        repo, proj, _log = self._runner_up(tmp_path, monkeypatch)
        monkeypatch.setenv(CGROUP_VAR, "dev-background.slice")
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace(
            "clean_tree = false",
            'clean_tree = false\n\n    [lanes.suite.resources]\n'
            '    memory = "999G"\n    shared = ["db"]'))
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr  # 999G over any budget: NOT enforced
        assert ("resources/memory but its environment is exec-mode") in proc.stdout

    def test_no_slice_anywhere_still_runs(self, tmp_path, monkeypatch):
        """Behavior-compat oracle: disclosure never adds an exec-mode refusal."""
        repo, proj, _log = self._runner_up(tmp_path, monkeypatch)
        monkeypatch.delenv(CGROUP_VAR, raising=False)
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        assert "slice (none)" in proc.stdout


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
        exec_calls = lane_execs(log)   # never the RG-26 inventory probe
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
        inner = run_gate.build_assay_inner(self._lane("3.1.0"), Path("/proj"))
        assert "./tools/assay/assay.pyz --version" in inner
        assert '[ "$tok" = 3.1.0 ]' in inner and "version mismatch" in inner

    def test_prefix_version_never_matches_longer_reported(self, tmp_path):
        """Review fix: the old substring glob let declared '3.1' pass for a
        reported '3.11.0' — a claim the artifact never made."""
        proc = self._run_inner(tmp_path, "3.1", "assay 3.11.0")
        assert proc.returncode != 0
        assert "version mismatch" in proc.stderr

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
        proc = self._run_inner(tmp_path, "3.1.0", "assay 9.9.9")
        assert proc.returncode != 0
        assert "version mismatch" in proc.stderr
        assert "3.1.0" in proc.stderr and "9.9.9" in proc.stderr

    def test_matching_version_runs_silently(self, tmp_path):
        proc = self._run_inner(tmp_path, "3.1.0", "assay 3.1.0")
        assert proc.returncode == 0, proc.stderr

    def test_punctuated_report_still_matches(self, tmp_path):
        """Trailing punctuation (v3.1.0,) or a leading bracket must not
        break the whole-token match."""
        proc = self._run_inner(tmp_path, "3.1.0", "(assay) reports: v3.1.0, ok")
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

    def test_leading_dash_named_as_position_not_charset(self):
        """Review fix: '-' is legal INSIDE a gate-safe path; a leading dash
        is a position problem and the message must say so, not list '-' as
        an offending character."""
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.check_worktree_charset(Path("-weird/tree"))
        assert "starts with '-'" in str(exc.value)
        assert "offending character" not in str(exc.value)

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


class TestEnvReferenceScan:
    """RG-23: `--check-env`'s comparison must be as wide as its message.

    The line regex it replaces could only see a name spelled as a literal
    inside `getenv(...)`/`os.environ[...]`. dstdns reads its live-test flag
    through `_env_flag_enabled("RUN_LIVE_TESTS")`, whose body does
    `os.getenv(name, "")` — literal and read in different functions — so the
    sweep certified a clean bill of health over the exact variable whose
    silent absence made an all-skipped pytest run report GREEN.
    """

    def scan(self, src: str):
        return run_gate.scan_env_references(textwrap.dedent(src))

    def names(self, src: str):
        return sorted({name for name, _line, _form in self.scan(src)})

    def test_direct_shapes_all_seen(self):
        assert self.names("""\
            import os
            from os import environ
            a = os.environ["SUBSCRIPT_VAR"]
            b = os.environ.get("GET_VAR")
            c = os.environ.setdefault("SETDEFAULT_VAR", "x")
            d = os.environ.pop("POP_VAR", None)
            e = os.getenv("GETENV_VAR")
            f = getenv("BARE_GETENV_VAR")
            g = environ["BARE_ENVIRON_VAR"]
            h = "MEMBERSHIP_VAR" in os.environ
        """) == ["BARE_ENVIRON_VAR", "BARE_GETENV_VAR", "GETENV_VAR",
                 "GET_VAR", "MEMBERSHIP_VAR", "POP_VAR", "SETDEFAULT_VAR",
                 "SUBSCRIPT_VAR"]

    def test_helper_wrapped_read_is_seen(self):
        """THE RG-23 oracle — dstdns conftest's real shape."""
        refs = self.scan("""\
            import os

            def _env_flag_enabled(name: str) -> bool:
                return os.getenv(name, "").lower() in ("1", "true")

            RUN_LIVE = _env_flag_enabled("RUN_LIVE_TESTS")
        """)
        assert ("RUN_LIVE_TESTS", 6, "helper _env_flag_enabled()") in refs

    def test_async_helper_and_method_call_site(self):
        refs = self.scan("""\
            import os

            class C:
                async def flag(self, name):
                    return os.environ.get(name)

            async def go(c):
                return await c.flag("METHOD_WRAPPED_VAR")
        """)
        assert ("METHOD_WRAPPED_VAR", 8, "helper flag()") in refs

    def test_positional_only_parameter_position_respected(self):
        """The literal is taken from the parameter position that is actually
        read — a helper reading its SECOND argument must not report the
        first, which would name the wrong variable with full confidence."""
        refs = self.scan("""\
            import os

            def read(default, name, /):
                return os.environ.get(name, default)

            V = read("FALLBACK", "SECOND_POSITION_VAR")
        """)
        assert [r for r in refs if r[2].startswith("helper")] == \
            [("SECOND_POSITION_VAR", 6, "helper read()")]

    def test_non_env_lookalikes_are_not_reported(self):
        """Superset refusal is the failure mode here: a sweep that flags
        ordinary dict reads trains its consumers to ignore it."""
        assert self.names("""\
            import os
            cfg = {}
            a = cfg["NOT_AN_ENV_VAR"]
            b = cfg.get("ALSO_NOT")
            c = "NOT_EITHER" in cfg
            d = os.environ.get(computed_name)
            e = os.path.join("NOR_THIS", "x")
            f = (lambda: 1)()
        """) == []

    def test_helper_call_with_too_few_arguments_is_skipped(self):
        assert self.names("""\
            import os

            def reader(name):
                return os.getenv(name)

            V = reader()
        """) == []

    def test_syntax_error_propagates_for_the_caller_to_disclose(self):
        with pytest.raises(SyntaxError):
            run_gate.scan_env_references("def broken(:\n")

    def test_check_env_reports_helper_shape_and_its_form(self, tmp_path,
                                                         monkeypatch, capsys):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        (proj / "conftest.py").write_text(textwrap.dedent("""\
            import os

            def _env_flag_enabled(name):
                return os.getenv(name, "") == "1"

            LIVE = _env_flag_enabled("RUN_LIVE_TESTS")
        """))
        monkeypatch.chdir(proj)
        assert run_gate.main(["--check-env"]) == 0   # advisory, never refuses
        out = capsys.readouterr().out
        assert "$RUN_LIVE_TESTS" in out
        assert "helper _env_flag_enabled()" in out
        assert "1 uncovered reference(s)" in out

    def test_check_env_unparseable_file_says_so_instead_of_nothing(
            self, tmp_path, monkeypatch, capsys):
        """'Could not read it' must never be reported as 'nothing there'."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        (proj / "future_syntax.py").write_text(
            "import os\nX = os.environ['REGEX_ONLY_VAR']\ndef broken(:\n")
        monkeypatch.chdir(proj)
        assert run_gate.main(["--check-env"]) == 0
        out = capsys.readouterr().out
        assert "future_syntax.py does not parse" in out
        assert "fell back to a line regex" in out
        assert "$REGEX_ONLY_VAR" in out   # degraded, but not silent


class TestEstateExecForwardEnvAudit:
    """RG-23 acceptance: the estate audit is a TEST, not a one-off note — a
    new vbpub exec-mode consumer must not silently re-acquire the assumption
    that `MOCK_MODE`/`RUN_LIVE_TESTS` are forwarded implicitly (they were
    until assay's ba8908d6; they are not since)."""

    IMPLICIT_BEFORE = ("MOCK_MODE", "RUN_LIVE_TESTS")

    def test_no_estate_lane_argv_relies_on_the_dropped_implicit_names(self):
        offenders = []
        for cfg_path in sorted(RUN_GATE_DIR.parent.glob("*/run-gate.toml")):
            cfg = tomllib.loads(cfg_path.read_text())
            environments = cfg.get("environments", {})
            exec_envs = {name for name, env in environments.items()
                         if env.get("mode") == "exec"}
            if not exec_envs:
                continue
            for lane_name, lane in cfg.get("lanes", {}).items():
                if lane.get("environment") not in exec_envs:
                    continue
                forwarded = set(
                    environments[lane["environment"]].get("forward_env", []))
                text = " ".join(lane.get("argv", []))
                for var in self.IMPLICIT_BEFORE:
                    if var in text and var not in forwarded:
                        offenders.append(f"{cfg_path}:[lanes.{lane_name}] "
                                         f"uses ${var} but environment "
                                         f"{lane['environment']!r} does not "
                                         f"forward it")
        assert not offenders, "\n".join(offenders)


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

    def test_green_lane_leaves_no_evidence(self, tmp_path, monkeypatch):
        """Review fix (R-26): evidence is for FAILING containers — a passing
        lane must not litter the evidence dir with a full log of everything
        the suite echoed."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)  # wait exits 0
        ev = self._evidence_dir(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert not ev.exists() or not list(ev.glob("*.log"))

    def test_failed_log_is_owner_only(self, tmp_path, monkeypatch):
        """Review fix (R-26): container logs may echo credential material —
        preserved evidence is mode 0600, never world-readable."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch, wait_code="7")
        ev = self._evidence_dir(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 7
        (log_path,) = ev.glob("*.log")
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


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

    def test_verdict_dedup_normalizes_path_spellings(self, tmp_path, monkeypatch):
        """Review fix (R-18): './.assay/verdict-mock.json' and the absolute
        {worktree}-spelled form of the same file ARE the verdict convention —
        each disclosed once via the verdict line, never as a second artifact."""
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
            artifacts = ["./.assay/verdict-mock.json",
                         "{worktree}/proj/.assay/verdict-mock.json"]
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

    # --- console-script form (review fix): RG-14 made `run-gate` real -------

    def test_console_script_form_certifies(self, tmp_path):
        repo, proj, trove = self._estate(
            tmp_path,
            "cd {worktree}/proj && exec run-gate --worktree {worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 1 invocation(s)" in proc.stdout

    def test_console_script_form_catches_unknown_lane(self, tmp_path):
        """The bare form gets the SAME certification, not a pass-by."""
        repo, proj, trove = self._estate(
            tmp_path,
            "cd {worktree}/proj && exec run-gate --worktree {worktree} nope")
        proc = self._validate(trove)
        assert proc.returncode == 2
        assert "'nope'" in proc.stdout and "known lanes: suite" in proc.stdout

    def test_prose_and_config_mentions_certify_nothing(self, tmp_path):
        """run-gate.toml / run-gate-project / run-gateway are names, not
        invocations — collecting them would certify nothing and could only
        manufacture false defects."""
        repo, proj, trove = self._estate(
            tmp_path,
            "cat {worktree}/proj/run-gate.toml && ls {worktree}/run-gate-project "
            "&& echo run-gateway-notes")
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "nothing to certify" in proc.stdout

    def test_discovery_snippet_inside_pointer_tolerated(self, tmp_path):
        """"--list names no lane by design — and needs no --worktree either
        (it reads config beside the script). The real invocation after &&
        still gets certified."""
        repo, proj, trove = self._estate(
            tmp_path,
            "cd {worktree}/proj && ./run-gate.py --list && "
            "exec ./run-gate.py --worktree {worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 2 invocation(s)" in proc.stdout

    def test_equals_form_worktree_accepted(self, tmp_path):
        repo, proj, trove = self._estate(
            tmp_path,
            "cd {worktree}/proj && exec ./run-gate.py --worktree={worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 1 invocation(s)" in proc.stdout

    def test_absolute_path_console_form_fail_closed(self, tmp_path):
        """/usr/local/bin/run-gate ... is deliberately NOT recognized (the
        bare-name boundary excludes path-anchored forms): fail closed means
        uncertified — never waved through as valid."""
        repo, proj, trove = self._estate(
            tmp_path,
            "/usr/local/bin/run-gate --worktree {worktree} suite")
        proc = self._validate(trove)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "nothing to certify" in proc.stdout

    def test_prose_label_field_not_an_invocation(self, tmp_path):
        """A label DESCRIBES an invocation, it doesn't run one. The widened
        bare-form collector manufactured 'trailing arguments' out of the real
        cmru.toml label 'cmru: run-gate gate conjunction'; prose-named fields
        are not command surface."""
        repo, proj, _trove = self._estate(tmp_path, self.CANONICAL)
        steps = proj / "consumer-steps.toml"
        steps.write_text('[[steps]]\n'
                         'label = "proj: run-gate gate conjunction"\n'
                         'argv = ["./run-gate.py", "--worktree", '
                         '"{worktree}", "suite"]\ncwd = "."\n')
        proc = self._validate(steps, "--root", str(repo))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK: 1 invocation(s)" in proc.stdout


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

    def test_cmru_toml_id_matches_orchestration_key(self):
        """cmru's config loader errors ('config declares project.id=X,
        expected Y') if run-gate-project/cmru.toml's id ever diverges from
        the key it's registered under in the root orchestration file — a
        future rename of either side without the other silently orphans
        the release, since cmru derives its change-detection watch path
        from the ORCHESTRATION KEY (the directory holding whatever the
        key's own `config = "..."` names), never from `id` itself."""
        cmru_doc = tomllib.loads((RUN_GATE_DIR / "cmru.toml").read_text())
        orch_doc = tomllib.loads(
            (RUN_GATE_DIR.parent / "cmru.orchestration.toml").read_text())
        entries = orch_doc["orchestration"]["project"]
        matches = [key for key, entry in entries.items()
                  if entry.get("config", "").startswith("run-gate-project/")]
        assert matches, "run-gate-project is not registered in " \
                        "cmru.orchestration.toml at all"
        assert cmru_doc["project"]["id"] in matches


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
        # No JUDGED container. The RG-26 base-delegation probe DOES run (it is
        # a preflight, and --dry-run rehearses every preflight; `assay lanes`
        # runs nothing), so the assertion is about the detached lane form.
        # The EXACT call set is pinned by
        # test_assay_lane_dry_run_runs_the_probe_and_no_judged_container.
        assert lane_runs(log) == []
        assert "verdict artifact:" not in proc.stdout  # nothing ran, none landed
        assert "--file assay.toml" in _docker_argv_line(proc.stdout)

    def test_assay_lane_dry_run_runs_the_probe_and_no_judged_container(
            self, tmp_path, monkeypatch):
        """B1 oracle: `--dry-run` DOES start a container since RG-26 — the
        read-only inventory probe, because it is what resolves the base the
        printed plan must show. `lane_runs()` filters on `-d` and therefore
        cannot see it, so the exact call set is pinned here: every `docker
        run` is the probe, no `-d`, no `docker exec`."""
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace('kind = "command"', 'kind = "assay"') \
            .replace('argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]',
                     'assay_lane = "mock"\n            assay_command = ["assay"]')
        proj = make_project(repo, cfg)
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite", "--dry-run")
        assert proc.returncode == 0, proc.stderr
        runs = docker_runs(log)
        assert len(runs) == 1, runs                # exactly one, and it is…
        assert "--rm" in runs[0] and "-d" not in runs[0]
        assert "lanes --json" in runs[0][-1]       # …the inventory probe
        assert docker_execs(log) == []
        assert lane_runs(log) == []                # no judged container at all

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

    def test_uppercase_size_units_admit_and_cap(self, tmp_path, monkeypatch):
        # Review fix (blocker): '512M' passed _validate_memory (IGNORECASE
        # since earlier revs) but crashed parse_size_bytes at admission with
        # a raw traceback / exit 1. One case-insensitive grammar everywhere.
        repo, proj = self._proj(tmp_path, 'memory = "512M"')
        cgfs = _fake_cgroupfs(tmp_path, max_raw=str(2 * GB),
                              current=str(256 * 1024 * 1024))
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT", str(cgfs))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "admission OK" in proc.stdout
        run_argv = docker_runs(log)[0]
        assert "--memory" in run_argv and "512M" in run_argv

    def test_uppercase_legacy_top_level_memory_admits(self, tmp_path,
                                                      monkeypatch):
        repo = make_repo(tmp_path)
        cfg = SIMPLE_LANE.replace(
            "    clean_tree = false\n",
            '    clean_tree = false\n    memory = "4G"\n')
        proj = make_project(repo, cfg)
        cgfs = _fake_cgroupfs(tmp_path, max_raw=str(8 * GB),
                              current=str(1 * GB))
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT", str(cgfs))
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        assert "admission OK" in proc.stdout
        run_argv = docker_runs(log)[0]
        assert "--memory" in run_argv and "4G" in run_argv

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

    def test_sorted_acquisition_kills_abba(self, tmp_path, monkeypatch,
                                           capsys):
        """Review MAJOR fix: locks are taken in SORTED-name order (a
        canonical global order), not declared order. A gate declaring
        [zzz, aaa] must block on 'aaa' while holding NOTHING on 'zzz' —
        under declared-order acquisition it held zzz while waiting, which
        is the half-open state of an ABBA deadlock."""
        svc_a = f"aaa-{os.getpid()}"
        svc_z = f"zzz-{os.getpid()}"
        repo, proj = self._proj(tmp_path,
                                f'shared = ["{svc_z}", "{svc_a}"]')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)

        a_lock = Path("/tmp") / f"run-gate-shared-{svc_a}.lock"
        holder = os.open(a_lock, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(holder, fcntl.LOCK_EX)

        result = {}

        def gate():
            result["rc"] = run_gate.main(["suite"])

        worker = threading.Thread(target=gate)
        worker.start()
        deadline = time.time() + 5
        while worker.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        assert worker.is_alive(), "gate must block on the contended name"
        # While blocked, the gate must NOT hold the alphabetically-later
        # service: probe its lock non-blocking from here.
        z_lock = Path("/tmp") / f"run-gate-shared-{svc_z}.lock"
        probe = os.open(z_lock, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must succeed
        except BlockingIOError:
            raise AssertionError(
                "gate held the later-sorted service while waiting on the "
                "earlier one — declared-order acquisition is back")
        finally:
            fcntl.flock(probe, fcntl.LOCK_UN)
            os.close(probe)
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)
        worker.join(timeout=15)
        assert not worker.is_alive()
        assert result["rc"] == 0

    def test_unusable_lock_path_is_infra_failure_not_traceback(self, tmp_path,
                                                               monkeypatch):
        svc = f"dir-{os.getpid()}"
        repo, proj = self._proj(tmp_path, f'shared = ["{svc}"]')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        lock_path = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        lock_path.mkdir()  # a directory where the flock file must go
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3
        assert "shared-infra lock" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_planted_symlink_at_lock_refused(self, tmp_path, monkeypatch):
        svc = f"sym-{os.getpid()}"
        repo, proj = self._proj(tmp_path, f'shared = ["{svc}"]')
        monkeypatch.setenv("RUN_GATE_CGROUPFS_ROOT",
                           str(tmp_path / "no-such-cgfs"))
        fake_docker(tmp_path, monkeypatch)
        target = tmp_path / "victim"
        target.write_text("x")
        lock_path = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        if lock_path.exists():
            lock_path.unlink()
        lock_path.symlink_to(target)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 3
        assert "shared-infra lock" in proc.stderr
        assert "Traceback" not in proc.stderr
        # O_NOFOLLOW: the symlink's TARGET must be untouched.
        assert target.read_text() == "x"

    def test_admission_refusal_precedes_lock_wait(self, tmp_path,
                                                  monkeypatch):
        """R-29 ordering sentence: an admission refusal must never wait
        behind a held shared-infra lock. Over-budget memory AND a contended
        service -> refusal arrives promptly at exit 2; under the old
        ordering this invocation hung until the timeout."""
        svc = f"order-{os.getpid()}"
        repo, proj = self._proj(
            tmp_path, f'memory = "512m"\nshared = ["{svc}"]')
        cgfs = _fake_cgroupfs(tmp_path, max_raw=str(1 * GB),
                              current=str(int(0.9 * GB)))
        env = {**os.environ, "RUN_GATE_CGROUPFS_ROOT": str(cgfs)}
        a_lock = Path("/tmp") / f"run-gate-shared-{svc}.lock"
        holder = os.open(a_lock, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            proc = subprocess.run(
                [sys.executable, str(_TOOL_INVOKE), "suite"],
                capture_output=True, text=True, cwd=str(proj), env=env,
                timeout=20)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "resource admission REFUSED" in proc.stderr

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


# ---------------------------------------------------------------------------
# RG-9 — doctor: one preflight command for the first-contact failure classes
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_healthy_container_project_all_ok(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 0, proc.stdout
        assert "[OK] docker:" in proc.stdout
        assert f"[OK] slice for env tester-unified: dev-background.slice" \
            in proc.stdout
        assert "[OK] git: " in proc.stdout
        assert "check(s):" in proc.stdout and ", 0 failure(s)" in proc.stdout

    def test_unresolvable_slice_fails_doctor(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.delenv(CGROUP_VAR, raising=False)  # no var, no declared slice
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 2
        assert "[FAIL] slice for env tester-unified" in proc.stdout

    def test_missing_image_warns_not_fails(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        body = shim.read_text()
        body = body.replace('case "$1" in',
                            'case "$1" in\n  image) exit 1 ;;')
        shim.write_text(body)
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 0, proc.stdout  # advisory only
        assert "[WARN] image tester-unified" in proc.stdout

    def test_docker_absent_is_a_failure(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        shim_dir = tmp_path / "empty-path"
        shim_dir.mkdir()
        monkeypatch.setenv("PATH", str(shim_dir))
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 2
        assert "[FAIL] docker" in proc.stdout

    def test_host_only_project_skips_slice_checks(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = """\
            schema_version = 1
            [lanes.smoke]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "true"]
            clean_tree = false
        """
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.delenv(CGROUP_VAR, raising=False)  # host needs no slice
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 0, proc.stdout
        assert "slice for env" not in proc.stdout

    def test_mountinfo_bare_host_view_warns(self, tmp_path, monkeypatch):
        """phys == repo (no alias derivable): container lanes would need the
        declared alias — doctor says so instead of letting RG-3 surprise."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        proc = subprocess.run(
            [sys.executable, str(_TOOL_INVOKE), "doctor"],
            capture_output=True, text=True, cwd=str(proj),
            env={**os.environ,
                 "PYTHONPATH": str(RUN_GATE_DIR / "tests")},
        )
        # Either WARN (bare-host view) or OK (namespace alias derivable):
        # both are healthy outcomes here — this pins that mountinfo is
        # REPORTED, never silently skipped.
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "[WARN] mountinfo" in proc.stdout or \
            "[OK] mountinfo" in proc.stdout

    def test_exec_env_without_slice_warns_not_fails(self, tmp_path, monkeypatch):
        """Review fix (R-30): exec environments need NO slice — the old
        unconditional resolve_slice made doctor report a bogus [FAIL] for a
        healthy exec project that has neither a declared slice nor ambient
        var."""
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace('mode = "exec"',
                                'mode = "exec"\n    container_name = "ext-runner"')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.delenv(CGROUP_VAR, raising=False)
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "[WARN] slice for env runner (exec)" in proc.stdout
        assert "[FAIL]" not in proc.stdout

    def test_exec_env_declared_slice_is_naming_only_ok(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        cfg = EXEC_LANE.replace(
            'mode = "exec"',
            'mode = "exec"\n    container_name = "ext-runner"\n'
            '    cgroup_slice = "dev-background.slice"')
        proj = make_project(repo, cfg)
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "doctor")
        assert proc.returncode == 0, proc.stdout
        assert ("[OK] slice for env runner (exec): dev-background.slice "
                "(declared") in proc.stdout

    def test_verify_slice_loaded_survives_missing_systemctl(self, monkeypatch,
                                                            capsys):
        """Review fix (R-30): run-dir present but systemctl not runnable —
        loud skip on stderr, never a FileNotFoundError traceback."""
        monkeypatch.setattr(run_gate.os.path, "isdir",
                            lambda p: p == "/run/systemd/system")

        def boom(argv, **kw):
            raise FileNotFoundError(2, "No such file or directory", "systemctl")

        monkeypatch.setattr(run_gate.subprocess, "run", boom)
        run_gate.verify_slice_loaded("dev-background.slice")  # must not raise
        assert "cannot LoadState-check dev-background.slice" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# RG-14 — wheel as SECOND artifact + version discipline (R-31)
# ---------------------------------------------------------------------------

class TestLinkedWorktreeHostLaneWarning:
    """RG-21: run-gate forwards `{worktree}` correctly and its exit status
    stays honest — the breakage is one layer DOWN, in a harness that
    bind-mounts only its own `$repo_root` by host path. From a linked
    worktree that subtree's `.git` is a FILE naming an absolute gitdir under
    the MAIN checkout, outside the mount, and every in-container git plumbing
    call dies (`not a git repository: …`; srdm's covergate, the evidence
    case). run-gate's own container lanes are unaffected — R-23 dual-mounts
    the REPO root — so the warning is scoped to projects declaring a HOST
    lane, the only kind that can reach such a harness.
    """

    HOST_LANE = """\
        schema_version = 1
        [lanes.smoke]
        kind = "command"
        environment = "host"
        argv = ["bash", "-c", "true"]
        clean_tree = false
    """

    def test_plain_checkout_gitdir_is_none(self, tmp_path):
        repo = make_repo(tmp_path)
        assert run_gate.linked_worktree_gitdir(repo) is None

    def test_linked_worktree_gitdir_is_the_absolute_target(self, tmp_path):
        repo = make_repo(tmp_path)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        gitdir = run_gate.linked_worktree_gitdir(wt)
        assert gitdir is not None
        assert gitdir == (repo / ".git" / "worktrees" / "w1")
        assert not gitdir.is_relative_to(wt)   # the whole point

    def test_gitfile_pointing_inside_the_tree_is_benign(self, tmp_path):
        """A gitdir INSIDE the judged tree travels with any mount of it —
        reporting that as the alarming condition would be a false alarm."""
        tree = tmp_path / "t"
        (tree / "nested").mkdir(parents=True)
        (tree / ".git").write_text("gitdir: nested\n")
        assert run_gate.linked_worktree_gitdir(tree) is None

    def test_gitfile_without_a_gitdir_line_is_none(self, tmp_path):
        tree = tmp_path / "t"
        tree.mkdir()
        (tree / ".git").write_text("# not a gitfile\n")
        assert run_gate.linked_worktree_gitdir(tree) is None

    def test_doctor_warns_from_a_linked_worktree_with_a_host_lane(
            self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        make_project(repo, self.HOST_LANE)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(wt / "proj")
        assert run_gate.main(["doctor"]) == 0     # advisory, never a refusal
        out = capsys.readouterr().out
        assert "[WARN] host-lane git view (RG-21)" in out
        assert str(repo / ".git" / "worktrees" / "w1") in out
        assert "not a git repository" in out       # the exact symptom, named
        assert "GIT_DIR" in out and "main checkout" in out   # both remedies

    def test_doctor_ok_from_a_plain_checkout_with_a_host_lane(
            self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.HOST_LANE)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        assert run_gate.main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "[OK] host-lane git view (RG-21)" in out

    def test_no_host_lane_no_check_at_all(self, tmp_path, monkeypatch, capsys):
        """Scoped, not universal: a container-only project cannot hit this,
        and a warning that fires where it cannot bite gets switched off."""
        repo = make_repo(tmp_path)
        make_project(repo, SIMPLE_LANE)          # container lane only
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(wt / "proj")
        assert run_gate.main(["doctor"]) == 0
        assert "host-lane git view (RG-21)" not in capsys.readouterr().out


def _inventory(**over) -> str:
    """One assay 3.2.0 inventory entry, shell-quoted into a fake judge."""
    entry = {"name": "ui_unit", "scope": "S1", "rigor": ["R0"],
             "enforcement": "gate", "language": None, "rigor_reachable": [],
             "coverage": None, "mutation": None, "canary": None,
             "base_source": None, "external_tools": [], "argv0": None,
             "env_required": [], "environment_command": False,
             "infrastructure_facts": [], "budget": None, "cwd": None,
             "link_paths": [], "snapshot_selection": None}
    entry.update(over)
    doc = {"assay_version": "3.2.0", "inventory_schema": 1, "lanes": [entry]}
    return json.dumps(doc)


def _fake_judge(payload: str, *, exit_code: int = 0) -> str:
    return f"""\
        #!/bin/sh
        case "$*" in
          *"lanes --json"*)
            {'exit ' + str(exit_code) if exit_code else ''}
            cat <<'JSON'
{payload}
JSON
            ;;
          *--version*) echo "assay 3.2.0" ;;
        esac
        exit 0
    """


ASSAY_LANE_CFG = """\
    schema_version = 1

    [environments.tester-unified]
    image = "tester-unified:local"

    [lanes.ui-unit]
    kind = "assay"
    environment = "tester-unified"
    assay_lane = "ui_unit"
    assay_command = ["assay"]
    clean_tree = false
"""


class TestAssayToolchainFitness:
    """RG-25: `doctor`/`--check-env` could not see that an assay lane's
    LANGUAGE needs a toolchain in its environment. run-gate never parses
    assay.toml — it ASKS the judge (`assay lanes --json`, assay B044) through
    the same in-environment probe path, exactly as it already asks
    `--version`. FAIL is reserved for a fact the inventory established;
    everything meaning "I could not determine this" is SKIP, so an assay
    older than B044 can never turn a healthy project red.
    """

    def _project(self, tmp_path, monkeypatch, cfg=ASSAY_LANE_CFG):
        repo = make_repo(tmp_path)
        proj = make_project(repo, cfg)
        (proj / "assay.toml").write_text("# judged by the fake judge\n")
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        monkeypatch.chdir(proj)
        return repo, proj

    def _doctor(self, capsys) -> tuple[int, str]:
        code = run_gate.main(["doctor"])
        return code, capsys.readouterr().out

    def test_missing_declared_external_tool_fails_naming_all_three(
            self, tmp_path, monkeypatch, capsys):
        """The acceptance oracle: lane, tool AND environment are named — a
        message naming only the tool cannot be acted on."""
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["definitely-not-installed-xyz"])))
        code, out = self._doctor(capsys)
        assert code == 2
        assert "[FAIL] lane 'ui-unit' toolchain" in out
        assert "definitely-not-installed-xyz" in out
        assert "tester-unified" in out

    def test_present_tool_reports_ok_with_the_tools_verified(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["sh"], argv0="bash")))
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert "[OK] lane 'ui-unit' toolchain: sh, bash" in out

    def test_javascript_language_derives_node_and_npm(
            self, tmp_path, monkeypatch, capsys):
        """assay's own inventory reports `external_tools: []` for EVERY
        shipped adapter, and its CONSUMERS.md says the node/npm fact has to
        come from `language`. A check keyed only on external_tools would
        report a clean bill of health for a JavaScript lane in an
        environment with no Node at all."""
        tools, caveat = run_gate.assay_lane_toolchain(
            {"language": "javascript", "external_tools": [], "argv0": None})
        assert tools == ["node", "npm"] and caveat is None

        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(language="javascript", external_tools=[])))
        # A PATH with no Node at all — this devcontainer has real node/npm in
        # /usr/bin beside bash, so the absent case has to be constructed.
        clean = tmp_path / "clean-bin"
        clean.mkdir()
        for tool in ("bash", "sh", "cat", "git"):
            (clean / tool).symlink_to(shutil.which(tool))
        monkeypatch.setenv("PATH", f"{shim}:{clean}")

        code, out = self._doctor(capsys)
        assert code == 2, out
        assert "[FAIL] lane 'ui-unit' toolchain" in out
        assert "needs node, npm in environment 'tester-unified'" in out

        install_fake_assay(monkeypatch, "#!/bin/sh\nexit 0\n", name="node")
        code, out = self._doctor(capsys)
        assert code == 2, out
        assert "needs npm in environment 'tester-unified'" in out
        assert "needs node" not in out            # node IS there — precision

        install_fake_assay(monkeypatch, "#!/bin/sh\nexit 0\n", name="npm")
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert "[OK] lane 'ui-unit' toolchain: node, npm" in out

    def test_go_language_derives_the_go_toolchain(self):
        """S8: the `go` row was executed but never asserted — gutting it to
        `()` reddened nothing, which makes it untested content in a table
        whose whole job is to state facts run-gate cannot read."""
        tools, caveat = run_gate.assay_lane_toolchain(
            {"language": "go", "external_tools": [], "argv0": None})
        assert tools == ["go"] and caveat is None

    def test_language_toolchain_is_unioned_not_replaced(self):
        """The three sources compose, in a stable order, without duplicates:
        language first, then declared external_tools, then argv0."""
        tools, caveat = run_gate.assay_lane_toolchain(
            {"language": "javascript", "external_tools": ["npm", "jq"],
             "argv0": "bash"})
        assert tools == ["node", "npm", "jq", "bash"] and caveat is None

    def test_unknown_language_reports_a_caveat_not_a_clean_bill(self):
        tools, caveat = run_gate.assay_lane_toolchain(
            {"language": "rust", "external_tools": [], "argv0": "cargo"})
        assert tools == ["cargo"]
        assert "rust" in caveat and "only argv0/external_tools" in caveat

    def test_lane_absent_from_the_inventory_fails(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(_inventory(name="other")))
        code, out = self._doctor(capsys)
        assert code == 2
        assert "assay lane 'ui_unit' is not declared in assay.toml" in out
        assert "declared: other" in out

    def test_judge_without_json_skips_never_fails(
            self, tmp_path, monkeypatch, capsys):
        """The pin declares the version this lane needs; run-gate must not
        impose an assay floor it never declared."""
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, """\
            #!/bin/sh
            echo "assay: unrecognized arguments: --json" >&2
            exit 2
        """)
        code, out = self._doctor(capsys)
        assert code == 0, out                      # SKIP, never FAIL
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        assert "older than 3.2.0" in out

    def test_non_json_output_skips_with_the_reason(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, "#!/bin/sh\necho 'not json at all'\n")
        code, out = self._doctor(capsys)
        assert code == 0
        assert "no usable JSON" in out

    def test_unknown_inventory_schema_skips_with_the_value(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            json.dumps({"inventory_schema": 2, "lanes": []})))
        code, out = self._doctor(capsys)
        assert code == 0
        assert "inventory_schema is 2, not 1" in out

    def test_probe_failure_is_a_skip_not_a_clean_bill(
            self, tmp_path, monkeypatch, capsys):
        """'Could not reach it' must never be folded into 'nothing is
        missing' — the `command -v` probe's own failure is indeterminacy."""
        self._project(tmp_path, monkeypatch)
        log = fake_docker(tmp_path, monkeypatch)   # records, never executes
        shim = shim_dir_of(monkeypatch) / "docker"
        shim.write_text("#!/bin/sh\n"
                        f'printf "%s\\037" "$@" >> "{log}"; printf "\\n" >> "{log}"\n'
                        'case "$1" in run|exec) for l; do :; done;\n'
                        '  case "$l" in *"lanes --json"*) '
                        f"cat <<'JSON'\n{_inventory(external_tools=['sh'])}\nJSON\n"
                        '    exit 0 ;;\n'
                        '  *) echo "probe transport broke" >&2; exit 7 ;; esac ;;\n'
                        'esac\nexit 0\n')
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        code, out = self._doctor(capsys)
        assert code == 0
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        assert "could not run `command -v`" in out and "exit 7" in out

    def test_host_environment_lane_skips(self, tmp_path, monkeypatch, capsys):
        cfg = ASSAY_LANE_CFG.replace('environment = "tester-unified"',
                                     'environment = "host"', 1)
        cfg = cfg.replace('[environments.tester-unified]\n    image = "tester-unified:local"\n',
                          "")
        self._project(tmp_path, monkeypatch, cfg)
        fake_docker_executing(tmp_path, monkeypatch)
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        assert "built-in 'host'" in out

    def test_docker_absent_skips_the_probe(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        empty = tmp_path / "empty-path"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        code, out = self._doctor(capsys)
        assert code == 2                            # docker itself FAILs
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        assert "docker not on PATH" in out

    def test_unresolvable_slice_skips_the_probe_rather_than_tracebacking(
            self, tmp_path, monkeypatch, capsys):
        """An ephemeral probe is a container this tool starts, so it needs a
        slice by the same policy a lane does (R-10). With none derivable the
        probe is SKIPped with the refusal as its reason — never run
        unconfined next to production, and never a traceback."""
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(_inventory()))
        monkeypatch.delenv(CGROUP_VAR, raising=False)
        code, out = self._doctor(capsys)
        assert code == 2                            # the slice FAIL, not a crash
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        assert CGROUP_VAR in out

    def test_no_toolchain_requirement_reports_ok(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(_inventory()))
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert "assay declares no toolchain requirement" in out

    def test_exec_environment_probes_through_docker_exec(
            self, tmp_path, monkeypatch, capsys):
        cfg = ASSAY_LANE_CFG.replace(
            'image = "tester-unified:local"',
            'image = "tester-unified:local"\n    mode = "exec"\n'
            '    container_name = "probe-runner"')
        _repo, _proj = self._project(tmp_path, monkeypatch, cfg)
        log = fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["sh"])))
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert "[OK] lane 'ui-unit' toolchain: sh" in out
        # the probe went through docker exec into the declared runner, and
        # started NO container of its own
        assert docker_runs(log) == []
        assert any("probe-runner" in call for call in docker_execs(log))

    def test_check_env_exits_2_on_a_toolchain_failure(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["definitely-not-installed-xyz"])))
        assert run_gate.main(["--check-env"]) == 2
        out = capsys.readouterr().out
        assert "check-env: [FAIL] lane 'ui-unit' toolchain" in out
        # the env-DRIFT half stays advisory — it did not cause the exit
        assert "ADVISORY ONLY" not in out or "uncovered reference(s)" in out

    def test_check_env_exits_0_when_the_toolchain_is_present(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(_inventory(argv0="sh")))
        assert run_gate.main(["--check-env"]) == 0
        assert "check-env: [OK] lane 'ui-unit' toolchain: sh" in \
            capsys.readouterr().out

    def test_command_lane_project_gets_no_toolchain_lines(
            self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker_executing(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        assert run_gate.main(["doctor"]) == 0
        assert "toolchain" not in capsys.readouterr().out

    THREE_LANE_CFG = """\
        schema_version = 1

        [environments.tester-unified]
        image = "tester-unified:local"

        [lanes.a-unit]
        kind = "assay"
        environment = "tester-unified"
        assay_lane = "ui_unit"
        assay_command = ["assay"]
        clean_tree = false

        [lanes.b-unit]
        kind = "assay"
        environment = "tester-unified"
        assay_lane = "ui_unit"
        assay_command = ["assay"]
        clean_tree = false

        [lanes.c-unit]
        kind = "assay"
        environment = "tester-unified"
        assay_lane = "ui_unit"
        assay_command = ["assay"]
        clean_tree = false
    """

    def test_probe_cost_is_one_inventory_plus_one_tool_probe_per_environment(
            self, tmp_path, monkeypatch, capsys):
        """B2 oracle: the cost claim in SPEC R-30 is a NUMBER, so a test owns
        it. Probing per LANE cost one container per lane on a shared
        environment (4 for 3 lanes) while the spec promised one — a
        quantitatively false claim is still a false claim. The union of every
        lane's tools is a property of the environment's PATH, not of the lane
        asking, so it is asked once."""
        self._project(tmp_path, monkeypatch, self.THREE_LANE_CFG)
        log = fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["sh"], argv0="bash")))
        code, out = self._doctor(capsys)
        assert code == 0, out
        assert out.count("[OK] lane ") == 3          # every lane still reported
        probes = [call for call in docker_runs(log) if "--rm" in call]
        assert len(probes) == 2, probes              # 1 inventory + 1 `command -v`
        assert sum("lanes --json" in call[-1] for call in probes) == 1
        assert sum("command -v" in call[-1] for call in probes) == 1

    def test_batched_tool_probe_still_names_only_each_lane_own_missing_tool(
            self, tmp_path, monkeypatch, capsys):
        """Batching must not smear one lane's missing tool onto another: the
        union is probed once, but each lane is judged against ITS OWN list."""
        cfg = self.THREE_LANE_CFG.replace(
            '[lanes.b-unit]\n        kind = "assay"\n        environment = "tester-unified"\n'
            '        assay_lane = "ui_unit"',
            '[lanes.b-unit]\n        kind = "assay"\n        environment = "tester-unified"\n'
            '        assay_lane = "js_unit"')
        self._project(tmp_path, monkeypatch, cfg)
        fake_docker_executing(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch)
        doc = {"assay_version": "3.2.0", "inventory_schema": 1, "lanes": [
            json.loads(_inventory(external_tools=["sh"], argv0="bash"))["lanes"][0],
            json.loads(_inventory(name="js_unit", language="javascript"))["lanes"][0],
        ]}
        install_fake_assay(monkeypatch, _fake_judge(json.dumps(doc)))
        clean = tmp_path / "clean-bin"
        clean.mkdir()
        for tool in ("bash", "sh", "cat", "git"):
            (clean / tool).symlink_to(shutil.which(tool))
        monkeypatch.setenv("PATH", f"{shim}:{clean}")
        code, out = self._doctor(capsys)
        assert code == 2, out
        assert "[FAIL] lane 'b-unit' toolchain: needs node, npm" in out
        # the OTHER two lanes are green — node/npm were in the batched probe
        # but are not THEIR requirement
        assert "[OK] lane 'a-unit' toolchain: sh, bash" in out
        assert "[OK] lane 'c-unit' toolchain: sh, bash" in out

    def test_one_in_environment_probe_builder(self):
        """RG-25 acceptance: ONE construction site for reaching an
        environment — a second `docker run`/`docker exec` argv shape is
        exactly the untested-argv class this project exists to kill."""
        src = _TOOL.read_text()
        builders = set()
        for chunk in src.split("\ndef ")[1:]:
            name = chunk.split("(")[0]
            if re.search(r'\[docker, "(run|exec)"', chunk):
                builders.add(name)
        assert builders == {"run_container_lane", "run_exec_lane",
                            "build_env_probe_argv"}, builders
        for consumer in ("assay_inventory", "probe_missing_tools"):
            body = src.split(f"\ndef {consumer}(")[1].split("\ndef ")[0]
            assert "build_env_probe_argv" in body
            assert '[docker, "' not in body

    def test_worktree_flag_relocates_the_probes_cd_target(
            self, tmp_path, monkeypatch, capsys):
        """RG-30: `doctor --worktree B` must relocate the inventory probe's
        `cd` target into B's project dir — mounting B's repo but `cd`ing into
        the INVOKING checkout's absolute path would not probe B, it would run
        against a directory the probe container never mounted (or,
        coincidentally, the wrong one)."""
        repo, proj = self._project(tmp_path, monkeypatch)
        commit_all(repo, "add assay.toml")
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        log = fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["sh"])))
        code = run_gate.main(["doctor", "--worktree", str(wt)])
        out = capsys.readouterr().out
        assert code == 0, out
        assert "[OK] lane 'ui-unit' toolchain: sh" in out
        probes = [call for call in docker_runs(log) if "--rm" in call]
        inventory_call = next(c for c in probes if "lanes --json" in c[-1])
        assert str(wt / "proj") in inventory_call[-1]
        assert str(proj) not in inventory_call[-1]

    def test_bad_worktree_skip_names_the_real_problem_not_assay_version(
            self, tmp_path, monkeypatch, capsys):
        """RG-31: this probe's OWN worktree resolution used the run-path's
        lenient `resolve_repo_and_worktree` (no upfront validation), so a
        bad `--worktree` silently built a `probe_dir` nothing mounted and
        the resulting SKIP blamed "assay older than 3.2.0" instead of the
        real cause — which check 3 ([FAIL] git) already names correctly two
        checks earlier in the SAME report. Now routed through the identical
        validated `resolve_worktree_scope()` check 3 uses, so both checks
        raise and report the SAME cause."""
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(external_tools=["sh"], argv0="bash")))
        code = run_gate.main(["doctor", "--worktree", str(tmp_path / "nope")])
        out = capsys.readouterr().out
        assert code == 2
        assert "[FAIL] git" in out and "not a directory" in out  # check 3
        assert "[SKIP] lane 'ui-unit' toolchain" in out
        toolchain_line = out.split("[SKIP] lane 'ui-unit' toolchain")[1]
        assert "not a directory" in toolchain_line   # the real cause, repeated
        assert "older than 3.2.0" not in out          # not the misleading guess
        assert "Traceback" not in out


class TestDoctorAndCheckEnvWorktreeReadScope:
    """RG-30: `doctor` and `--check-env` both passed `None` to
    `resolve_repo_and_worktree` instead of the caller's `--worktree` value,
    so `doctor --worktree B` silently reported the INVOKING tree's answers,
    not B's — including RG-21's worktree-specific host-lane git-view WARN,
    exactly the kind of per-tree answer that legitimately differs between
    trees. RG-27 (`history`, B1) closed the identical read-scope hazard for
    that verb; this closes the last remaining instance estate-wide, with the
    same disclosure discipline (the report NAMES the tree it describes)."""

    HOST_LANE = TestLinkedWorktreeHostLaneWarning.HOST_LANE

    def _two_trees(self, tmp_path, monkeypatch, cfg):
        repo = make_repo(tmp_path)
        proj = make_project(repo, cfg)
        wt = tmp_path / "w1"
        git(repo, "worktree", "add", "-q", "-b", "w1", str(wt))
        fake_docker(tmp_path, monkeypatch)
        return repo, proj, wt

    # -- doctor: RG-21 flips with --worktree, proving the read scope follows
    #    the flag instead of the invoking checkout -----------------------

    def test_doctor_worktree_flag_reports_the_named_trees_state(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, self.HOST_LANE)
        monkeypatch.chdir(proj)                 # invoking tree: plain checkout (OK)
        assert run_gate.main(["doctor", "--worktree", str(wt)]) == 0
        out = capsys.readouterr().out
        assert "[WARN] host-lane git view (RG-21)" in out
        assert str(repo / ".git" / "worktrees" / "w1") in out
        assert f"--worktree {wt}" in out
        assert "THAT tree, not the invoking checkout" in out

    def test_doctor_worktree_flag_does_not_leak_the_invoking_trees_answer(
            self, tmp_path, monkeypatch, capsys):
        """The other direction: invoked FROM the linked worktree (which
        would itself WARN unflagged) but pointed at the plain checkout via
        --worktree — the answer must be the checkout's OK, never the
        invoking tree's WARN presented under the wrong name."""
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, self.HOST_LANE)
        monkeypatch.chdir(wt / "proj")          # invoking tree: linked worktree (WARN)
        assert run_gate.main(["doctor", "--worktree", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "[OK] host-lane git view (RG-21)" in out
        assert "[WARN] host-lane git view (RG-21)" not in out

    def test_doctor_without_the_flag_still_answers_for_the_invoking_checkout(
            self, tmp_path, monkeypatch, capsys):
        """No regression: unflagged doctor keeps answering about the
        invoking checkout, with no disclosure banner — nothing substituted,
        nothing to name."""
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, self.HOST_LANE)
        monkeypatch.chdir(wt / "proj")
        assert run_gate.main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "[WARN] host-lane git view (RG-21)" in out
        assert "--worktree" not in out

    def test_doctor_bad_worktree_fails_the_git_check_not_a_false_ok(
            self, tmp_path, monkeypatch, capsys):
        """A garbage --worktree must not let the RG-21 check read "no
        gitdir file here" as "plain checkout, nothing to warn about" — it
        FAILs the git check instead (never reaching RG-21), and every other
        check still runs."""
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, self.HOST_LANE)
        monkeypatch.chdir(proj)
        code = run_gate.main(["doctor", "--worktree", str(tmp_path / "nope")])
        out = capsys.readouterr().out
        assert code == 2
        assert "[FAIL] git" in out and "not a directory" in out
        assert "host-lane git view" not in out    # never reached -> no false OK
        assert "[OK] docker" in out                # other checks still ran

    def test_doctor_non_git_worktree_fails_with_gits_own_message(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, self.HOST_LANE)
        monkeypatch.chdir(proj)
        outside = tmp_path / "plain-dir"
        outside.mkdir()
        code = run_gate.main(["doctor", "--worktree", str(outside)])
        captured = capsys.readouterr()
        assert code == 2
        assert "[FAIL] git" in captured.out
        assert "host-lane git view" not in captured.out
        assert "Traceback" not in captured.err

    # -- --check-env: the drift scan and the toolchain probe both follow
    #    --worktree ------------------------------------------------------

    def test_check_env_worktree_flag_scans_the_named_trees_sources(
            self, tmp_path, monkeypatch, capsys):
        """The env-drift scan must read the SELECTED tree's Python sources,
        not the invoking checkout's — proven with a helper module that
        exists ONLY on the worktree's branch, committed after the worktree
        was created so the main checkout never sees it."""
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, SIMPLE_LANE)
        (wt / "proj" / "extra.py").write_text(
            "import os\nos.environ['DRIFTY']\n")
        git(wt, "add", "proj/extra.py")
        git(wt, "commit", "-q", "-m", "drift only in w1")
        monkeypatch.chdir(proj)
        assert not (proj / "extra.py").exists()
        run_gate.main(["--check-env", "--worktree", str(wt)])
        out = capsys.readouterr().out
        assert "$DRIFTY" in out
        assert f"--worktree {wt}" in out
        assert "THAT tree, not the invoking checkout" in out

    def test_check_env_without_the_flag_never_sees_the_other_trees_drift(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, SIMPLE_LANE)
        (wt / "proj" / "extra.py").write_text(
            "import os\nos.environ['DRIFTY']\n")
        git(wt, "add", "proj/extra.py")
        git(wt, "commit", "-q", "-m", "drift only in w1")
        monkeypatch.chdir(proj)
        run_gate.main(["--check-env"])
        out = capsys.readouterr().out
        assert "$DRIFTY" not in out
        assert "--worktree" not in out

    def test_check_env_bad_worktree_refuses_rather_than_scanning_nothing(
            self, tmp_path, monkeypatch, capsys):
        """Unlike `doctor`, `--check-env` has no per-check ledger for a bad
        override to land in gracefully — it refuses outright rather than let
        a nonexistent tree yield an empty (misleadingly clean) scan under
        that tree's name."""
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, SIMPLE_LANE)
        monkeypatch.chdir(proj)
        code = run_gate.main(["--check-env", "--worktree",
                              str(tmp_path / "nope")])
        captured = capsys.readouterr()
        assert code == 2
        assert "not a directory" in captured.err
        assert "--check-env" in captured.err
        assert "Traceback" not in captured.err

    def test_check_env_non_git_worktree_refuses_with_gits_own_message(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, wt = self._two_trees(tmp_path, monkeypatch, SIMPLE_LANE)
        monkeypatch.chdir(proj)
        outside = tmp_path / "plain-dir"
        outside.mkdir()
        code = run_gate.main(["--check-env", "--worktree", str(outside)])
        captured = capsys.readouterr()
        assert code == 3
        assert "Traceback" not in captured.err


class TestComparisonBasePassthrough:
    """RG-26: assay 3.0.0 shipped `judge.base_source = "request"` (B019) — a
    changed-line lane that omits `judge.base` and takes its comparison base
    from the gate. Such a lane invoked WITHOUT `--request-base` refuses by
    design, and run-gate had no `--base`, so a shipped judge feature was
    unusable from every consumer. The delegation fact is DERIVED from
    `assay lanes --json`, never restated as a run-gate.toml key.
    """

    def _project(self, tmp_path, monkeypatch, cfg=ASSAY_LANE_CFG):
        repo = make_repo(tmp_path)
        proj = make_project(repo, cfg)
        (proj / "assay.toml").write_text("# judged by the fake judge\n")
        commit_all(repo, "assay config")
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys/host/root"))
        monkeypatch.chdir(proj)
        return repo, proj

    @staticmethod
    def _judge(monkeypatch, base_source):
        install_fake_assay(monkeypatch, _fake_judge(
            _inventory(base_source=base_source)))

    def test_delegating_lane_with_base_carries_request_base(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 0
        inner = lane_runs(log)[0][-1]
        assert "--request-base deadbeef" in inner
        out = capsys.readouterr().out
        assert "comparison base deadbeef (from --base) → --request-base" in out

    def test_delegating_lane_without_base_uses_the_upstream_merge_base(
            self, tmp_path, monkeypatch, capsys):
        repo, _proj = self._project(tmp_path, monkeypatch)
        # give the judged tree a real upstream to derive from
        git(repo, "branch", "-f", "origin-main", "HEAD")
        git(repo, "branch", "--set-upstream-to=origin-main", "main")
        expected = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit"]) == 0
        inner = lane_runs(log)[0][-1]
        assert f"--request-base {expected}" in inner
        assert "merge-base HEAD @{upstream}" in capsys.readouterr().out

    def test_delegating_lane_without_base_and_without_upstream_refuses(
            self, tmp_path, monkeypatch, capsys):
        """A guessed base is not a base — assay never falls back to HEAD, and
        neither does the gate."""
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit"]) == 2
        err = capsys.readouterr().err
        assert "lane 'ui-unit' delegates its comparison base" in err
        assert "pass --base REF (worktree has no upstream)" in err

    def test_non_delegating_assay_lane_with_base_refuses(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "declared")
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 2
        err = capsys.readouterr().err
        assert "does not delegate its comparison base" in err
        assert "base_source 'declared'" in err
        assert lane_runs(log) == []            # refused BEFORE the judged run

    def test_non_delegating_assay_lane_without_base_is_unchanged(
            self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, None)
        assert run_gate.main(["ui-unit"]) == 0
        assert "--request-base" not in lane_runs(log)[0][-1]

    def test_judge_without_json_and_no_base_behaves_exactly_as_before(
            self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, "#!/bin/sh\n"
                           'echo "unrecognized arguments: --json" >&2\nexit 2\n')
        assert run_gate.main(["ui-unit"]) == 0
        assert "--request-base" not in lane_runs(log)[0][-1]

    def test_judge_without_json_and_base_given_refuses_naming_the_floor(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, "#!/bin/sh\n"
                           'echo "unrecognized arguments: --json" >&2\nexit 2\n')
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 2
        err = capsys.readouterr().err
        assert "cannot tell whether lane 'ui-unit' delegates" in err
        assert "assay 3.2.0 (B044)" in err
        assert lane_runs(log) == []

    def test_lane_absent_from_inventory_with_base_refuses(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker_executing(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, _fake_judge(_inventory(name="other")))
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 2
        assert "is not declared in assay.toml" in capsys.readouterr().err

    def test_conjunction_lane_propagates_base_to_every_sub_invocation(
            self, tmp_path, monkeypatch, capfd):
        """RG-1's rule: an override given to the gate reaches every sub-lane.
        A conjunction declares it the same way it declares `--worktree` — a
        token in its own argv."""
        cfg = """\
            schema_version = 1
            [lanes.gate]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c",
                    "echo ./run-gate.py --base {base} a && echo ./run-gate.py --base {base} b"]
            clean_tree = false
        """
        self._project(tmp_path, monkeypatch, cfg)
        assert run_gate.main(["gate", "--base", "deadbeef"]) == 0
        out = capfd.readouterr().out   # fd-level: the sub-shell's own stdout
        assert out.count("./run-gate.py --base deadbeef") == 2
        # the token itself never survives into a sub-invocation
        assert "./run-gate.py --base {base}" not in out
        assert "comparison base deadbeef (from --base) → {base} in the lane argv" in out

    def test_conjunction_lane_without_base_and_without_upstream_refuses(
            self, tmp_path, monkeypatch, capsys):
        cfg = """\
            schema_version = 1
            [lanes.gate]
            kind = "command"
            environment = "host"
            argv = ["bash", "-c", "echo ./run-gate.py --base {base} a"]
            clean_tree = false
        """
        self._project(tmp_path, monkeypatch, cfg)
        assert run_gate.main(["gate"]) == 2
        assert "delegates its comparison base" in capsys.readouterr().err

    def test_command_lane_without_the_token_refuses_base(
            self, tmp_path, monkeypatch, capsys):
        """The ref could only be silently dropped — RG-1's own hazard class."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        assert run_gate.main(["suite", "--base", "deadbeef"]) == 2
        err = capsys.readouterr().err
        assert "lane 'suite' does not delegate a comparison base" in err
        assert "{base}" in err

    def test_host_assay_lane_probes_locally_without_docker(
            self, tmp_path, monkeypatch, capsys):
        """A `host` environment IS this machine: the same probe script, no
        container to enter."""
        cfg = ASSAY_LANE_CFG.replace('environment = "tester-unified"',
                                     'environment = "host"', 1)
        self._project(tmp_path, monkeypatch, cfg)
        log = fake_docker(tmp_path, monkeypatch)   # records, never executes
        self._judge(monkeypatch, "request")
        install_fake_assay(monkeypatch, "#!/bin/sh\nexit 0\n", name="fake-shell")
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 0
        assert docker_runs(log) == [] and docker_execs(log) == []
        assert "comparison base deadbeef" in capsys.readouterr().out

    def test_dry_run_discloses_the_resolved_ref_and_starts_no_lane(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit", "--base", "deadbeef",
                              "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "comparison base deadbeef (from --base) → --request-base" in out
        assert "--request-base deadbeef" in _docker_argv_line(out)
        assert lane_runs(log) == []            # the probe ran; the lane did not

    def test_exec_environment_lane_carries_request_base(
            self, tmp_path, monkeypatch, capsys):
        cfg = ASSAY_LANE_CFG.replace(
            'image = "tester-unified:local"',
            'image = "tester-unified:local"\n    mode = "exec"\n'
            '    container_name = "probe-runner"')
        self._project(tmp_path, monkeypatch, cfg)
        log = fake_docker_executing(tmp_path, monkeypatch)
        self._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 0
        inner = lane_execs(log)[0][-1]
        assert "--request-base deadbeef" in inner
        assert "--verdict-json" in inner        # the judged exec, not the probe

    def test_docker_absent_with_base_refuses_instead_of_guessing(
            self, tmp_path, monkeypatch, capsys):
        """Indeterminacy plus --base is a refusal, not an assumption: without
        the inventory run-gate does not know whether the lane delegates, and
        appending --request-base to a lane that does not would make assay
        refuse for a reason the operator never caused."""
        self._project(tmp_path, monkeypatch)
        # git must stay reachable — an empty PATH would fail earlier, in tree
        # resolution, and prove nothing about this branch.
        nodocker = tmp_path / "no-docker-bin"
        nodocker.mkdir()
        for tool in ("git", "bash", "sh"):
            (nodocker / tool).symlink_to(shutil.which(tool))
        monkeypatch.setenv("PATH", str(nodocker))
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 2
        err = capsys.readouterr().err
        assert "cannot tell whether lane 'ui-unit' delegates" in err
        assert "docker is not on PATH" in err

    def test_host_assay_lane_actually_builds_the_assay_inner(
            self, tmp_path, monkeypatch, capfd):
        """RG-28's own oracle (S3). The sibling test above proves the base
        reached a host assay lane; this one proves the lane BODY is the real
        assay inner and not a stub — pin/cd/verdict all present, executed
        with the effective project dir as cwd. Gutting run_host_lane's assay
        branch back to `lane["argv"]` reddens this with KeyError; replacing
        `build_assay_inner(...)` with `"true"` reddens every assertion."""
        cfg = ASSAY_LANE_CFG.replace('environment = "tester-unified"',
                                     'environment = "host"', 1)
        _repo, proj = self._project(tmp_path, monkeypatch, cfg)
        fake_docker(tmp_path, monkeypatch)
        # A judge that ECHOES its own argv, so the executed inner is visible.
        install_fake_assay(monkeypatch, """\
            #!/bin/sh
            case "$*" in
              *"lanes --json"*) echo '{"inventory_schema": 1, "lanes": [
                  {"name": "ui_unit", "base_source": "request",
                   "external_tools": [], "argv0": null, "language": null}]}' ;;
              *) echo "JUDGE-RAN-IN: $(pwd)"; echo "JUDGE-ARGV: $*" ;;
            esac
            exit 0
        """)
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 0
        out = capfd.readouterr().out
        assert f"JUDGE-RAN-IN: {proj}" in out          # cwd = effective project dir
        assert "JUDGE-ARGV: run ui_unit --file assay.toml" in out
        assert "--verdict-json .assay/verdict-ui_unit.json" in out
        assert "--request-base deadbeef" in out
        assert (proj / ".assay").is_dir()              # `mkdir -p .assay` ran

    def test_substitute_worktree_leaves_base_token_when_none_resolved(self):
        """Defence in depth: the run path never reaches here with an
        unresolved token (plan_comparison_base refuses first), so the
        substitution must not invent an empty string either."""
        assert run_gate.substitute_worktree(["{worktree}/x", "--base", "{base}"],
                                            Path("/w")) == \
            ["/w/x", "--base", "{base}"]


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


_SCM_PRETEND_VERSION = "1.2.3"  # fixed test version — the point of pretend-version
                                # is that the wheel build needs NO git history at all


class TestWheelPackaging:
    """The wheel never replaces the script; it wraps the SAME bytes.

    Version identity is TWO-TIER (release-adoption program, superseding
    RG-14's original coupling): `__revision__` inside the script stays the
    copy-drift marker external repos compare; the wheel's version is
    DERIVED from the git tag (`run-gate-vX.Y.Z`) by setuptools-scm, matching
    ciu/cmru/assay/topos/nyxloom. A real release build never runs `git
    describe` live — cmru's wheel-build step sets
    `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE` from the tag it just
    minted (cmru.toml's `scm_dist = "run_gate"`), which is exactly what
    these tests do too, so no git history needs to travel into the tmp
    build stage.
    """

    def test_pyproject_declares_scm_version(self):
        cfg = tomllib.loads((RUN_GATE_DIR / "pyproject.toml").read_text())
        proj = cfg["project"]
        assert cfg["build-system"]["build-backend"] == "setuptools.build_meta"
        # version comes from the git tag, never a second static declaration:
        assert "version" not in proj
        assert "version" in proj["dynamic"]
        scm = cfg["tool"]["setuptools_scm"]
        assert scm["root"] == ".."
        assert scm["tag_regex"] == r"^run-gate-v(?P<version>[0-9].*)$"
        assert scm["git_describe_command"][-2:] == ["--match", "run-gate-v*"]
        # console script + module mapping + zero runtime deps (unchanged):
        assert proj["scripts"] == {"run-gate": "run_gate:main"}
        assert cfg["tool"]["setuptools"]["py-modules"] == ["run_gate"]
        assert proj["dependencies"] == []
        # the importable name exists and is the SAME bytes as the canonical
        # script (committed symlink run_gate.py -> run-gate.py):
        link = RUN_GATE_DIR / "run_gate.py"
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(RUN_GATE_DIR / "run-gate.py")
        assert link.read_bytes() == (RUN_GATE_DIR / "run-gate.py").read_bytes()

    @pytest.fixture(scope="class")
    def built_wheel(self, tmp_path_factory):
        """Build the real wheel once, exactly the way cmru's publish flow
        does (`python -m build`, pretend-version env var). Lives entirely
        in tmp: the worktree gains no dist/ or egg-info."""
        if not (_has_module("setuptools") and _has_module("build")
                and _has_module("setuptools_scm")):
            # Review fix: a silent skip hides toolchain drift — make it loud
            # in the warnings summary even when the suite stays green.
            warnings.warn("run-gate wheel tests SKIPPED: wheel toolchain "
                          "unavailable (setuptools/build/setuptools_scm not "
                          "installed)")
            pytest.skip("wheel toolchain unavailable")
        import setuptools
        from importlib.metadata import version as _dist_version
        pyproject = tomllib.loads((RUN_GATE_DIR / "pyproject.toml").read_text())
        for dist, have in (("setuptools", setuptools.__version__),
                           ("setuptools_scm", _dist_version("setuptools_scm"))):
            pin = next(r for r in pyproject["build-system"]["requires"]
                      if r.startswith(f"{dist}=="))
            want = pin.split("==")[1]
            if have != want:
                warnings.warn(f"run-gate wheel tests SKIPPED: local {dist} "
                              f"{have} != pinned {want}")
                pytest.skip(
                    f"local {dist} {have} != pinned {want} (python -m build "
                    "refuses a mismatched --no-isolation closure)")
        stage = tmp_path_factory.mktemp("wheel-stage")
        for name in ("run-gate.py", "pyproject.toml", "README.md"):
            shutil.copy2(RUN_GATE_DIR / name, stage / name)
        # copy2 FOLLOWS the symlink: the staged tree holds the dereferenced
        # copy a git-archive/sdist build would see.
        shutil.copy2(RUN_GATE_DIR / "run_gate.py", stage / "run_gate.py")
        # No .git in this stage at all (matches a real sdist/tarball build):
        # the pretend-version env var is the ONLY version source, exactly
        # like cmru's release build sets it from the tag it just minted.
        env = {**os.environ,
              "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE": _SCM_PRETEND_VERSION}
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation", "."],
            cwd=stage, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
        wheels = list((stage / "dist").glob("*.whl"))
        assert len(wheels) == 1, wheels
        return wheels[0]

    def test_built_wheel_ships_identical_module_and_derived_version(
            self, built_wheel):
        v = _SCM_PRETEND_VERSION
        assert built_wheel.name == f"run_gate-{v}-py3-none-any.whl"
        with zipfile.ZipFile(built_wheel) as z:
            names = z.namelist()
            assert "run_gate.py" in names
            top = {n.split("/")[0] for n in names}
            assert top == {"run_gate.py", f"run_gate-{v}.dist-info"}, sorted(top)
            ep = z.read(f"run_gate-{v}.dist-info/entry_points.txt").decode()
            assert "[console_scripts]" in ep
            assert "run-gate = run_gate:main" in ep
            meta = z.read(f"run_gate-{v}.dist-info/METADATA").decode()
            assert f"Version: {v}" in meta
            # THE truthfulness core: what pip installs is byte-identical to
            # the canonical script regardless of the wheel's own version —
            # __revision__ (unaffected by this) is the copies' drift marker.
            assert z.read("run_gate.py") == \
                (RUN_GATE_DIR / "run-gate.py").read_bytes()

    def test_installed_console_script_matches_script_behavior(
            self, built_wheel, tmp_path):
        install = tmp_path / "install"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--no-index",
             "--no-deps", "--target", str(install), str(built_wheel)],
            check=True, capture_output=True, text=True)
        bin_gate = install / "bin" / "run-gate"
        assert bin_gate.exists(), "console script not exposed by the wheel"
        # An adopting project via CONSUMERS step 1: COPIED canonical script,
        # no wheel anywhere on its path.
        proj = tmp_path / "proj"
        proj.mkdir()
        shutil.copy2(RUN_GATE_DIR / "run-gate.py", proj / "run-gate.py")
        (proj / "run-gate.toml").write_text(
            'schema_version = 1\n'
            '[lanes.suite]\nkind = "command"\nenvironment = "host"\n'
            'argv = ["true"]\nclean_tree = false\n')
        canon = subprocess.run([os.path.join(".", "run-gate.py"), "--list"],
                               cwd=proj, capture_output=True, text=True)
        wheeled = subprocess.run(
            [str(bin_gate), "--list"], cwd=proj, capture_output=True,
            text=True, env={**os.environ, "PYTHONPATH": str(install)})
        assert canon.returncode == 0, canon.stderr
        assert wheeled.returncode == 0, wheeled.stderr
        assert canon.stdout.strip() != ""
        assert wheeled.stdout == canon.stdout


# ---------------------------------------------------------------------------
# RG-13 item 5 — estate-wide budget↔timeout pairing sweep (R-32)
# ---------------------------------------------------------------------------

_BUDGET_UNITS = {"s": 1, "m": 60, "h": 3600}


def _budget_seconds(value: str) -> int:
    return int(value[:-1]) * _BUDGET_UNITS[value[-1]]


class TestEstateBudgetTimeoutPairing:
    """Every consumer gate that runs a project's lane must give it at least
    the lane's declared budget: run-gate's budget is advisory and PRINTED,
    but a consumer timeout tighter than the budget silently truncates the
    lane before its own declared wall-clock expires — the drift RG-13 filed.
    Pairing rule (assay's assert-it pattern, replicated estate-wide): for
    each nyxloom trove whose project declares lanes in run-gate.toml (loaded
    with the REAL parser), any [gates.X] table whose argv names that lane as
    a whole token must carry timeout_seconds >= lane budget. cmru.toml steps
    carry no timeout field, so they have nothing to pair. A gate whose argv
    invokes a helper rather than a lane (srdm's canary-run.sh) pairs with
    nothing and is skipped by construction."""

    # Review fix: accumulated across the parametrized sweep so a final
    # aggregate test can prove the pairing mechanism is ALIVE estate-wide
    # (per-trove skips must never add up to a vacuously green sweep).
    PAIRINGS_SEEN: list[tuple[str, str]] = []

    @pytest.mark.parametrize("trove", sorted(
        (RUN_GATE_DIR.parent.glob("*/nyxloom-trove/nyxloom.toml"))),
        ids=lambda p: p.parent.parent.name)
    def test_consumer_timeouts_never_cut_lanes_short(self, trove):
        proj_dir = trove.parent.parent
        if not (proj_dir / "run-gate.toml").is_file():
            pytest.skip(f"{proj_dir.name} has no run-gate.toml")
        project, _, central, central_path = run_gate.load_config(proj_dir)
        lanes = run_gate.merge_lanes(
            project.get("lanes", {}), central,
            proj_dir, proj_dir.resolve().parent, central_path)
        gates = tomllib.loads(trove.read_text()).get("gates", {})
        paired = []
        for gate_name, gate in gates.items():
            timeout = gate.get("timeout_seconds")
            if timeout is not None and not isinstance(timeout, int):
                # Review fix: an unparseable timeout used to be silently
                # skipped — exactly the rot this sweep exists to catch.
                pytest.fail(
                    f"{trove}: [gates.{gate_name}] timeout_seconds must be "
                    f"integer seconds, got {timeout!r} — the pairing sweep "
                    f"refuses to skip it silently")
            if timeout is None:
                continue
            argv_text = " ".join(gate.get("argv", []))
            for lane_name, lane in lanes.items():
                budget = lane.get("budget")
                if budget is None:
                    continue
                # Whole-token on BOTH sides anchored at whitespace/ends
                # (review fix): the old lookaround let 'out/suite.json'
                # pair as lane 'suite'.
                if re.search(rf"(?:^|\s){re.escape(lane_name)}(?:\s|$)",
                             argv_text):
                    paired.append((gate_name, lane_name))
                    self.PAIRINGS_SEEN.append((proj_dir.name, gate_name))
                    assert timeout >= _budget_seconds(budget), (
                        f"{trove}: [gates.{gate_name}] timeout_seconds="
                        f"{timeout} is TIGHTER than {proj_dir.name} lane "
                        f"'{lane_name}' budget {budget} — widen the consumer "
                        f"timeout or shrink the declared budget")
        assert paired, (
            f"{trove}: no gate↔lane pairing found — either the trove stopped "
            f"pointing at run-gate lanes or the pairing regex rotted")

    def test_estate_pairing_sweep_is_alive(self):
        """Rot-guard (review fix): the per-trove tests each prove their own
        pairings, but a mass rename could empty every trove at once. The
        aggregate demands the sweep found REAL gate↔lane pairs somewhere."""
        if not self.PAIRINGS_SEEN:
            pytest.skip("no troves scanned (estate layout absent)")
        assert len(self.PAIRINGS_SEEN) >= 3, (
            f"estate-wide pairing collapsed to {self.PAIRINGS_SEEN} — the "
            f"sweep's grammar and the estate's gate tables have drifted "
            f"apart; fix one or the other, never widen this guard to pass")


# ---------------------------------------------------------------------------
# RG-27 / R-36 — lane invocation history + the `history` query verb
# ---------------------------------------------------------------------------

HISTORY_LANE = """\
    schema_version = 1

    [environments.tester-unified]
    image = "tester-unified:local"

    [lanes.suite]
    kind = "command"
    environment = "tester-unified"
    argv = ["bash", "-c", "cd {worktree}/proj && echo gate-ran"]
    # clean_tree = false DELIBERATELY: history eligibility must key on whether
    # the tree WAS dirty, never on whether dirt was permitted, so this lane
    # proves a clean run of a dirt-tolerating lane still reaches history.
    clean_tree = false
"""


def make_history_repo(tmp_path: Path, config: str = HISTORY_LANE,
                      ignore: str = ".run-gate/\n") -> tuple[Path, Path]:
    """A repo whose .gitignore covers the store, plus a project inside it."""
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(ignore)
    proj = repo / "proj"
    proj.mkdir()
    (proj / "run-gate.toml").write_text(textwrap.dedent(config))
    commit_all(repo, "history fixture")
    return repo, proj


def new_commit(repo: Path, tag: str) -> str:
    (repo / f"f-{tag}.txt").write_text(tag)
    commit_all(repo, f"commit {tag}")
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def record_run(proj: Path, repo: Path, *, lane: str = "suite",
               exit_code: int | None = 0, error: BaseException | None = None,
               seconds: float = 1.0, keep: int = 10) -> dict:
    """Drive the REAL recorder — real git repo, real ignore check, real lock,
    real atomic write. Only the clock is substituted."""
    rec = run_gate.start_run_record(lane, repo, repo)
    rec["_started_monotonic"] = time.monotonic() - seconds
    if error is not None:
        run_gate.finish_run_record(rec, error=error)
    else:
        run_gate.finish_run_record(rec, exit_code=exit_code)
    run_gate.record_invocation(proj, repo, rec, keep)
    return rec


def read_store(proj: Path) -> dict:
    return json.loads((proj / ".run-gate" / "history.json").read_text())


def lane_slot(proj: Path, lane: str = "suite") -> dict:
    return read_store(proj)["lanes"][lane]


class TestHistoryConfigPolicy:
    """The retention BOUND is declared config (auditable, shadowable); the
    data it bounds is per-instance state. Two different questions."""

    def test_keep_defaults_to_ten_and_says_so(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        cfg, cfg_path, central, central_path = run_gate.load_config(proj)
        keep, source = run_gate.resolve_history_keep(cfg, cfg_path, central,
                                                     central_path)
        assert keep == 10
        assert "default" in source

    def test_project_history_table_declares_the_bound(self, tmp_path):
        _, proj = make_history_repo(
            tmp_path, HISTORY_LANE + "\n[history]\nkeep = 3\n")
        cfg, cfg_path, central, central_path = run_gate.load_config(proj)
        keep, source = run_gate.resolve_history_keep(cfg, cfg_path, central,
                                                     central_path)
        assert keep == 3
        assert str(cfg_path) in source

    def test_project_history_shadows_central(self, tmp_path):
        repo, proj = make_history_repo(
            tmp_path, HISTORY_LANE + "\n[history]\nkeep = 3\n")
        (repo / "run-gate.toml").write_text(
            "schema_version = 1\n[history]\nkeep = 99\n")
        commit_all(repo, "central history policy")
        cfg, cfg_path, central, central_path = run_gate.load_config(proj)
        assert run_gate.resolve_history_keep(
            cfg, cfg_path, central, central_path)[0] == 3

    def test_central_history_is_inherited_when_project_is_silent(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        (repo / "run-gate.toml").write_text(
            "schema_version = 1\n[history]\nkeep = 4\n")
        commit_all(repo, "central history policy")
        cfg, cfg_path, central, central_path = run_gate.load_config(proj)
        keep, source = run_gate.resolve_history_keep(cfg, cfg_path, central,
                                                     central_path)
        assert keep == 4
        assert "central" in source

    @pytest.mark.parametrize("value", ["0", "-1", "true", '"5"', "1.5"])
    def test_bad_keep_values_refuse_at_load(self, tmp_path, value):
        _, proj = make_history_repo(
            tmp_path, HISTORY_LANE + f"\n[history]\nkeep = {value}\n")
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "'keep' must be an integer >= 1" in str(exc.value)

    def test_unknown_history_key_refuses_naming_key_and_file(self, tmp_path):
        _, proj = make_history_repo(
            tmp_path, HISTORY_LANE + "\n[history]\nkeepp = 3\n")
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "keepp" in str(exc.value)
        assert "run-gate.toml" in str(exc.value)

    def test_lane_named_history_is_reserved(self, tmp_path):
        _, proj = make_history_repo(tmp_path, HISTORY_LANE.replace(
            "[lanes.suite]", "[lanes.history]"))
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "reserved" in str(exc.value)


class TestHistoryRollingSeries:
    """TRAP 1 (RG-27's own words): 'recording only the latest invocation with
    no rolling stat — a single slow outlier run would look like the lane's
    permanent cost'. Every test here fails on a latest-only implementation."""

    def test_series_survives_across_commits_not_just_the_last(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        for tag, secs in (("a", 10.0), ("b", 10.0), ("c", 100.0)):
            new_commit(repo, tag)
            record_run(proj, repo, seconds=secs)
        slot = lane_slot(proj)
        # A latest-only store would hold ONE entry here. Three commits ran.
        assert len(slot["history"]) == 3
        assert [e["duration_seconds"] for e in slot["history"]] == [10.0, 10.0,
                                                                   100.0]

    def test_one_slow_outlier_does_not_become_the_typical_cost(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        for tag, secs in (("a", 10.0), ("b", 10.0), ("c", 100.0)):
            new_commit(repo, tag)
            record_run(proj, repo, seconds=secs)
        store = run_gate.load_history_store(proj / ".run-gate/history.json")
        stats = run_gate.lane_history_report(store, "suite")["stats"]
        # The whole point: 'what does this lane cost' answers 10, not 100 —
        # while the outlier stays VISIBLE as max rather than being discarded.
        assert stats["completed"]["median_seconds"] == 10.0
        assert stats["completed"]["max_seconds"] == 100.0
        assert stats["completed"]["min_seconds"] == 10.0
        assert stats["completed"]["count"] == 3

    def test_latest_reflects_the_outlier_while_the_series_does_not(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        for tag, secs in (("a", 10.0), ("b", 100.0)):
            new_commit(repo, tag)
            record_run(proj, repo, seconds=secs)
        slot = lane_slot(proj)
        assert slot["latest"]["duration_seconds"] == 100.0
        assert len(slot["history"]) == 2

    def test_window_evicts_the_oldest_beyond_keep(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        shas = []
        for tag in ("a", "b", "c", "d"):
            shas.append(new_commit(repo, tag))
            record_run(proj, repo, seconds=1.0, keep=2)
        slot = lane_slot(proj)
        assert [e["commit"] for e in slot["history"]] == shas[-2:]

    def test_rerun_of_one_commit_replaces_its_entry_not_the_window(self, tmp_path):
        """Keyed by (lane, commit): ten re-runs of one commit must not evict
        nine other commits' measurements from a ten-deep window."""
        repo, proj = make_history_repo(tmp_path)
        first = new_commit(repo, "a")
        record_run(proj, repo, seconds=5.0)
        second = new_commit(repo, "b")
        for secs in (7.0, 8.0, 9.0):
            record_run(proj, repo, seconds=secs)
        slot = lane_slot(proj)
        assert [e["commit"] for e in slot["history"]] == [first, second]
        assert slot["history"][-1]["duration_seconds"] == 9.0

    def test_completed_fail_joins_history_carrying_its_outcome(self, tmp_path):
        """The design call RG-27 flagged, resolved YES — and resolved SAFELY:
        the fail is kept WITH its outcome and the stats are split, so a
        consumer that must not mix them never has to."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, exit_code=0, seconds=30.0)
        new_commit(repo, "b")
        record_run(proj, repo, exit_code=1, seconds=3.0)
        slot = lane_slot(proj)
        assert [e["outcome"] for e in slot["history"]] == ["pass", "fail"]
        store = run_gate.load_history_store(proj / ".run-gate/history.json")
        stats = run_gate.lane_history_report(store, "suite")["stats"]
        # Split series: the short-circuiting fail never dilutes the pass cost.
        assert stats["passes"] == {"count": 1, "min_seconds": 30.0,
                                   "median_seconds": 30.0, "max_seconds": 30.0}
        assert stats["completed"]["count"] == 2
        assert stats["completed"]["median_seconds"] == 16.5


class TestHistoryEligibilityGuard:
    """TRAP 2 (RG-27's own words): 'an aborted/dirty-tree run silently
    corrupting the bounded history's commit-keyed entries (e.g. overwriting a
    real commit's history slot with a dirty-tree duration)'."""

    def _clean_baseline(self, tmp_path) -> tuple[Path, Path, str]:
        repo, proj = make_history_repo(tmp_path)
        sha = new_commit(repo, "a")
        record_run(proj, repo, exit_code=0, seconds=10.0)
        assert lane_slot(proj)["history"] == [lane_slot(proj)["latest"]]
        return repo, proj, sha

    def test_dirty_run_never_overwrites_the_commits_history_entry(self, tmp_path):
        repo, proj, sha = self._clean_baseline(tmp_path)
        (repo / "scratch.txt").write_text("uncommitted")   # SAME commit, dirty
        record_run(proj, repo, exit_code=0, seconds=999.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["history"][0]["commit"] == sha
        assert slot["history"][0]["duration_seconds"] == 10.0, (
            "the dirty run's duration was written into a real commit's slot")
        # …while `latest` DID move, which is the other half of the contract.
        assert slot["latest"]["duration_seconds"] == 999.0
        assert slot["latest"]["dirty"] is True
        assert slot["latest"]["history_eligible"] is False
        assert "dirty" in slot["latest"]["excluded_reason"]

    def test_aborted_run_updates_latest_only(self, tmp_path):
        repo, proj, sha = self._clean_baseline(tmp_path)
        record_run(proj, repo, error=KeyboardInterrupt(), seconds=2.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["history"][0]["duration_seconds"] == 10.0
        assert slot["latest"]["outcome"] == "aborted"
        assert slot["latest"]["history_eligible"] is False
        assert "KeyboardInterrupt" in slot["latest"]["excluded_reason"]

    def test_infrastructure_error_updates_latest_only(self, tmp_path):
        repo, proj, sha = self._clean_baseline(tmp_path)
        record_run(proj, repo,
                   error=run_gate.GateInfraError("docker died"), seconds=2.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["latest"]["outcome"] == "error"
        assert slot["latest"]["history_eligible"] is False

    def test_mid_rebase_run_updates_latest_only(self, tmp_path):
        repo, proj, sha = self._clean_baseline(tmp_path)
        gitdir = Path(subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True).stdout.strip())
        (gitdir / "rebase-merge").mkdir()
        record_run(proj, repo, exit_code=0, seconds=999.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["history"][0]["duration_seconds"] == 10.0
        assert slot["latest"]["git_operation"] == "rebase-merge"
        assert slot["latest"]["history_eligible"] is False
        assert "transient" in slot["latest"]["excluded_reason"]

    def test_undeterminable_cleanliness_excludes_rather_than_assumes(self,
                                                                    tmp_path,
                                                                    monkeypatch):
        """'Could not determine' must not collapse into 'clean'. A possibly-
        wrong trend entry is invisible; a missing one shows up in `count`."""
        repo, proj, sha = self._clean_baseline(tmp_path)
        monkeypatch.setattr(run_gate, "worktree_is_dirty", lambda _wt: None)
        record_run(proj, repo, exit_code=0, seconds=999.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["history"][0]["duration_seconds"] == 10.0
        assert slot["latest"]["history_eligible"] is False
        assert "could not determine" in slot["latest"]["excluded_reason"]

    def test_detached_head_without_a_commit_is_excluded(self, tmp_path,
                                                        monkeypatch):
        repo, proj, sha = self._clean_baseline(tmp_path)
        monkeypatch.setattr(run_gate, "head_commit", lambda _wt: None)
        record_run(proj, repo, exit_code=0, seconds=999.0)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert "HEAD did not resolve" in slot["latest"]["excluded_reason"]

    def test_dirt_tolerance_is_not_the_discriminator(self, tmp_path):
        """`clean_tree = false` (HISTORY_LANE declares it) on a CLEAN tree is
        a perfectly good measurement — excluding it would confuse policy with
        fact and quietly halve the series of every dirt-tolerant lane."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        rec = record_run(proj, repo, exit_code=0, seconds=10.0)
        assert rec["dirty"] is False
        assert lane_slot(proj)["history"][0]["duration_seconds"] == 10.0

    def test_tree_state_is_sampled_before_the_lane_not_after(self, tmp_path):
        """A lane that leaves artifacts behind must not retro-disqualify its
        own (clean at start) measurement."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        rec = run_gate.start_run_record("suite", repo, repo)
        (repo / "lane-artifact.txt").write_text("written BY the lane")
        rec["_started_monotonic"] = time.monotonic() - 4.0
        run_gate.finish_run_record(rec, exit_code=0)
        run_gate.record_invocation(proj, repo, rec, 10)
        assert lane_slot(proj)["history"][0]["duration_seconds"] == 4.0


class TestHistoryStoreSafety:
    """Storage location + concurrent-write safety — the two questions RG-27
    demanded an explicit answer to rather than an assumption."""

    def test_store_is_scoped_per_worktree_and_per_project(self, tmp_path):
        """The PRIMARY concurrency answer is scope, not arbitration: two
        worktrees' gates address two different files and never meet."""
        repo, proj = make_history_repo(tmp_path)
        other = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        str(other), "-b", "side"], check=True,
                       capture_output=True, text=True)
        a = run_gate.history_store_path(proj)
        b = run_gate.history_store_path(other / "proj")
        assert a != b
        assert a.parent.parent == proj and b.parent.parent == other / "proj"

    def test_the_lock_is_a_sibling_file_not_the_store_itself(self, tmp_path):
        """The store is REPLACED by rename, so its inode changes on every
        write — a lock taken on it would guard a file nobody writes next."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo)
        before = (proj / ".run-gate/history.json").stat().st_ino
        lock_ino = (proj / ".run-gate/history.lock").stat().st_ino
        new_commit(repo, "b")
        record_run(proj, repo)
        assert (proj / ".run-gate/history.json").stat().st_ino != before
        assert (proj / ".run-gate/history.lock").stat().st_ino == lock_ino

    def test_lock_file_is_owner_only(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo)
        mode = (proj / ".run-gate/history.lock").stat().st_mode & 0o777
        assert mode == 0o600

    def test_concurrent_recorders_lose_no_entries(self, tmp_path):
        """Read-modify-write without mutual exclusion is last-writer-wins:
        N concurrent recorders would leave 1 entry, not N."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "base")
        records = []
        for i in range(8):
            rec = run_gate.start_run_record("suite", repo, repo)
            rec["commit"] = f"{i:040x}"     # 8 distinct synthetic commits
            rec["_started_monotonic"] = time.monotonic() - (i + 1)
            run_gate.finish_run_record(rec, exit_code=0)
            records.append(rec)
        barrier = threading.Barrier(len(records))

        def writer(rec):
            barrier.wait()
            run_gate.record_invocation(proj, repo, rec, 20)

        threads = [threading.Thread(target=writer, args=(r,))
                   for r in records]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        commits = {e["commit"] for e in lane_slot(proj)["history"]}
        assert commits == {r["commit"] for r in records}

    def test_readers_take_no_lock(self, tmp_path):
        """Atomic rename is what makes lock-free reads correct — the query
        verb must answer even while a gate holds the writer lock."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, seconds=3.0)
        lock = proj / ".run-gate/history.lock"
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            out = run_tool(proj, "history", "suite", "--json")
        finally:
            os.close(fd)
        assert out.returncode == 0, out.stderr
        payload = json.loads(out.stdout)
        assert payload["lanes"]["suite"]["latest"]["duration_seconds"] == 3.0

    def test_a_held_lock_never_blocks_a_gate_forever(self, tmp_path,
                                                     monkeypatch):
        """Unlike RG-20's shared-infra lock (blocks on purpose, it protects
        the RUN), this one is bounded: a stuck writer degrades telemetry, it
        does not hang the gate."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo)
        monkeypatch.setattr(run_gate, "HISTORY_LOCK_TIMEOUT", 0.2)
        fd = os.open(proj / ".run-gate/history.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            rec = run_gate.start_run_record("suite", repo, repo)
            run_gate.finish_run_record(rec, exit_code=0)
            started = time.monotonic()
            assert run_gate.record_invocation(proj, repo, rec, 10) is False
            assert time.monotonic() - started < 10
        finally:
            os.close(fd)

    def test_corrupt_store_is_replaced_not_fatal(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo)
        (proj / ".run-gate/history.json").write_text("{ not json")
        new_commit(repo, "b")
        assert record_run(proj, repo) is not None
        assert lane_slot(proj)["latest"] is not None

    def test_write_failure_is_one_warning_never_a_traceback(self, tmp_path,
                                                            monkeypatch,
                                                            capsys):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(run_gate, "_write_json_atomic", boom)
        rec = run_gate.start_run_record("suite", repo, repo)
        run_gate.finish_run_record(rec, exit_code=0)
        assert run_gate.record_invocation(proj, repo, rec, 10) is False
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "OSError: disk full" in err

    def test_unignored_store_is_refused_with_the_remedy(self, tmp_path,
                                                        capsys):
        """Refuses to WRITE rather than leaving the tree dirty for the next
        lane's clean-tree check — the adoption obligation made executable."""
        repo, proj = make_history_repo(tmp_path, ignore="unrelated\n")
        new_commit(repo, "a")
        rec = run_gate.start_run_record("suite", repo, repo)
        run_gate.finish_run_record(rec, exit_code=0)
        assert run_gate.record_invocation(proj, repo, rec, 10) is False
        err = capsys.readouterr().err
        assert ".gitignore" in err and ".run-gate/" in err
        assert not (proj / ".run-gate").exists()

    def test_partially_ignored_store_is_still_refused(self, tmp_path, capsys):
        """`git check-ignore a b` exits 0 when ANY argument matches. Reading
        that exit status as 'both are ignored' would certify a store whose
        LOCK file still dirties the tree."""
        repo, proj = make_history_repo(
            tmp_path, ignore="proj/.run-gate/history.json\n")
        new_commit(repo, "a")
        rec = run_gate.start_run_record("suite", repo, repo)
        run_gate.finish_run_record(rec, exit_code=0)
        assert run_gate.record_invocation(proj, repo, rec, 10) is False
        assert "not fully git-ignored" in capsys.readouterr().err

    def test_directory_ignore_pattern_works_on_the_very_first_run(self,
                                                                  tmp_path):
        """`git check-ignore` on the bare DIRECTORY answers 'not ignored'
        while it does not exist yet — asking about the files is what makes a
        correctly-configured project record on run one, not run two."""
        repo, proj = make_history_repo(tmp_path)
        assert not (proj / ".run-gate").exists()
        assert run_gate.paths_are_git_ignored(
            repo, run_gate.history_written_paths(proj)) is True

    def test_tracked_store_counts_as_not_ignored(self, tmp_path):
        """Index-aware on purpose: a TRACKED path dirties the tree whatever
        .gitignore says, so that is the question actually asked."""
        repo, proj = make_history_repo(tmp_path)
        (proj / ".run-gate").mkdir()
        (proj / ".run-gate/history.json").write_text("{}")
        git(repo, "add", "-f", "proj/.run-gate/history.json")
        commit_all(repo, "someone committed the store")
        assert run_gate.paths_are_git_ignored(
            repo, run_gate.history_written_paths(proj)) is False

    def test_git_error_status_is_indeterminate_not_a_green_light(self,
                                                                 tmp_path):
        """git exits 128 outside a repo. That is 'I could not answer', and
        folding it into either answer would be the absence-for-emptiness
        anti-pattern — here in its dangerous direction."""
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        assert run_gate.paths_are_git_ignored(
            outside, run_gate.history_written_paths(outside)) is None

    def test_git_failure_is_indeterminate_not_a_green_light(self, tmp_path,
                                                            monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        monkeypatch.setattr(run_gate.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError("no git")))
        assert run_gate.paths_are_git_ignored(
            repo, run_gate.history_written_paths(proj)) is None


class TestHistoryEndToEnd:
    """The recorder wired into a REAL `run-gate.py <lane>` invocation — the
    unit tests above drive the recorder, these prove the gate calls it."""

    def test_a_real_lane_run_records_pass_in_both_slots(self, tmp_path,
                                                        monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        sha = new_commit(repo, "a")
        out = run_tool(proj, "suite")
        assert out.returncode == 0, out.stderr
        slot = lane_slot(proj)
        assert slot["latest"]["outcome"] == "pass"
        assert slot["latest"]["exit_code"] == 0
        assert slot["latest"]["commit"] == sha
        assert slot["latest"]["worktree"] == str(repo)
        assert slot["latest"]["revision"] == run_gate.__revision__
        assert isinstance(slot["latest"]["duration_seconds"], float)
        assert [e["commit"] for e in slot["history"]] == [sha]

    def test_a_completed_fail_is_recorded_and_the_exit_status_is_untouched(
            self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch, wait_code=7)
        sha = new_commit(repo, "a")
        out = run_tool(proj, "suite")
        assert out.returncode == 7, out.stderr   # passthrough, R-04
        slot = lane_slot(proj)
        assert slot["latest"]["outcome"] == "fail"
        assert slot["latest"]["exit_code"] == 7
        assert [e["outcome"] for e in slot["history"]] == ["fail"]

    def test_dry_run_records_nothing(self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        out = run_tool(proj, "suite", "--dry-run")
        assert out.returncode == 0, out.stderr
        assert not (proj / ".run-gate/history.json").exists()

    def test_a_clean_tree_refusal_lands_in_latest_only(self, tmp_path,
                                                       monkeypatch):
        """A refusal IS an invocation result the caller wants to see — and it
        is never a measurement of the commit it refused on."""
        repo, proj = make_history_repo(
            tmp_path, HISTORY_LANE.replace("clean_tree = false",
                                           "clean_tree = true"))
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        (repo / "dirt.txt").write_text("x")
        out = run_tool(proj, "suite")
        assert out.returncode == 2, out.stdout + out.stderr
        slot = lane_slot(proj)
        assert slot["latest"]["outcome"] == "error"
        assert slot["history"] == []

    def test_configuration_errors_record_no_invocation_at_all(self, tmp_path,
                                                              monkeypatch):
        """Unknown lane names no invocation to be `latest` of."""
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        out = run_tool(proj, "nope")
        assert out.returncode == 2
        assert not (proj / ".run-gate").exists()

    def test_worktree_override_records_into_the_judged_tree(self, tmp_path,
                                                            monkeypatch):
        """R-21's rule applied to telemetry: judging tree B must not write B's
        measurement into A's store."""
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        other = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        str(other), "-b", "side"], check=True,
                       capture_output=True, text=True)
        out = run_tool(proj, "suite", "--worktree", str(other))
        assert out.returncode == 0, out.stderr
        assert not (proj / ".run-gate").exists()
        assert lane_slot(other / "proj")["latest"]["worktree"] == str(other)

    def test_an_unignored_store_warns_without_changing_the_verdict(
            self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path, ignore="unrelated\n")
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        out = run_tool(proj, "suite")
        assert out.returncode == 0, out.stderr
        assert "lane history not recorded" in out.stderr
        assert "Traceback" not in out.stderr
        assert not (proj / ".run-gate").exists()


class TestHistoryQueryVerb:
    """R-36 query surface: human table by default, `--json` for machines."""

    def test_human_table_shows_latest_series_and_the_typical_cost(self,
                                                                  tmp_path):
        repo, proj = make_history_repo(tmp_path)
        for tag, secs in (("a", 10.0), ("b", 100.0)):
            new_commit(repo, tag)
            record_run(proj, repo, seconds=secs)
        out = run_tool(proj, "history", "suite")
        assert out.returncode == 0, out.stderr
        assert "lane suite" in out.stdout
        assert "latest:  pass exit 0  100.0s" in out.stdout
        assert "history: 2 of at most 10 commit(s)" in out.stdout
        assert "median 55.0s" in out.stdout      # the SERIES, not the outlier
        assert "max 100.0s" in out.stdout

    def test_human_table_names_the_exclusion_reason(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, error=KeyboardInterrupt(), seconds=1.0)
        out = run_tool(proj, "history", "suite")
        assert "NOT in history:" in out.stdout
        assert "history: (empty; keep=10)" in out.stdout

    def test_json_carries_latest_history_and_split_stats(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, exit_code=0, seconds=10.0)
        new_commit(repo, "b")
        record_run(proj, repo, exit_code=1, seconds=2.0)
        out = run_tool(proj, "history", "suite", "--json")
        assert out.returncode == 0, out.stderr
        payload = json.loads(out.stdout)
        assert payload["schema"] == 1
        assert payload["keep"] == 10 and "default" in payload["keep_source"]
        assert payload["store"] == str(proj / ".run-gate/history.json")
        lane = payload["lanes"]["suite"]
        assert lane["latest"]["outcome"] == "fail"
        assert [e["outcome"] for e in lane["history"]] == ["pass", "fail"]
        assert lane["stats"]["passes"]["count"] == 1
        assert lane["stats"]["completed"]["count"] == 2

    def test_no_lane_argument_reports_every_declared_lane(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "history", "--json")
        assert out.returncode == 0, out.stderr
        assert set(json.loads(out.stdout)["lanes"]) == {"suite"}

    def test_empty_store_is_an_answer_not_a_failure(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "history")
        assert out.returncode == 0
        assert "(not written yet)" in out.stdout
        assert "(no recorded invocation)" in out.stdout

    def test_unknown_lane_refuses_naming_the_known_ones(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "history", "nope")
        assert out.returncode == 2
        assert "unknown lane 'nope'" in out.stderr and "suite" in out.stderr

    def test_the_verb_runs_no_lane(self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        log = fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        run_tool(proj, "history")
        assert log.read_text() == ""

    def test_declared_keep_is_disclosed_by_the_query(self, tmp_path):
        _, proj = make_history_repo(
            tmp_path, HISTORY_LANE + "\n[history]\nkeep = 3\n")
        payload = json.loads(run_tool(proj, "history", "--json").stdout)
        assert payload["keep"] == 3
        assert payload["keep_source"].endswith("run-gate.toml")

    def test_usage_documents_the_verb_and_the_store_contract(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "--help")
        assert "run-gate.py history [LANE] [--worktree PATH] [--json]" \
            in out.stdout
        assert "read scope" in out.stdout
        assert ".run-gate/history.json" in out.stdout
        assert "[history] keep" in out.stdout
        assert "MUST be git-ignored" in out.stdout


class TestHistoryInProcess:
    """The same surfaces as above, driven THROUGH `main()` in-process — the
    subprocess tests prove the shipped entrypoint, these reach the wiring
    (and the printer) where coverage can see it."""

    def _project(self, tmp_path, monkeypatch, config=HISTORY_LANE):
        repo, proj = make_history_repo(tmp_path, config)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        return repo, proj

    def test_main_records_a_pass_then_the_verb_prints_it(self, tmp_path,
                                                         monkeypatch, capsys):
        repo, proj = self._project(tmp_path, monkeypatch)
        sha = new_commit(repo, "a")
        assert run_gate.main(["suite"]) == 0
        capsys.readouterr()
        assert run_gate.main(["history", "suite"]) == 0
        out = capsys.readouterr().out
        assert "lane suite" in out
        assert "latest:  pass exit 0" in out
        assert sha[:12] in out
        assert "history: 1 of at most 10 commit(s)" in out
        assert "passes: n=1 median" in out
        assert "completed (passes + fails): n=1" in out

    def test_main_records_a_refusal_into_latest_only(self, tmp_path,
                                                     monkeypatch, capsys):
        repo, proj = self._project(
            tmp_path, monkeypatch,
            HISTORY_LANE.replace("clean_tree = false", "clean_tree = true"))
        new_commit(repo, "a")
        (repo / "dirt.txt").write_text("x")
        assert run_gate.main(["suite"]) == 2
        assert "refusing to judge a dirty tree" in capsys.readouterr().err
        slot = lane_slot(proj)
        assert slot["latest"]["outcome"] == "error"
        assert slot["history"] == []

    def test_main_records_an_abort_and_re_raises_untouched(self, tmp_path,
                                                           monkeypatch):
        repo, proj = self._project(tmp_path, monkeypatch)
        new_commit(repo, "a")

        def interrupted(*_a, **_k):
            raise KeyboardInterrupt

        monkeypatch.setattr(run_gate, "run_container_lane", interrupted)
        with pytest.raises(KeyboardInterrupt):
            run_gate.main(["suite"])
        slot = lane_slot(proj)
        assert slot["latest"]["outcome"] == "aborted"
        assert slot["latest"]["history_eligible"] is False
        assert slot["history"] == []

    def test_verb_prints_the_exclusion_reason_and_an_empty_series(
            self, tmp_path, monkeypatch, capsys):
        repo, proj = self._project(tmp_path, monkeypatch)
        new_commit(repo, "a")
        record_run(proj, repo, error=KeyboardInterrupt(), seconds=1.0)
        assert run_gate.main(["history"]) == 0
        out = capsys.readouterr().out
        assert "NOT in history:" in out
        assert "history: (empty; keep=10)" in out
        assert "keep:  10  (default (10))" in out

    def test_verb_reports_an_untouched_lane_and_a_missing_store(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main(["history"]) == 0
        out = capsys.readouterr().out
        assert "(not written yet)" in out
        assert "(no recorded invocation)" in out
        assert "history: (empty; keep=10)" in out

    def test_verb_json_shape(self, tmp_path, monkeypatch, capsys):
        repo, proj = self._project(tmp_path, monkeypatch)
        new_commit(repo, "a")
        record_run(proj, repo, seconds=4.0)
        assert run_gate.main(["history", "suite", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["lanes"]["suite"]["stats"]["passes"]["count"] == 1
        assert payload["store"].endswith(".run-gate/history.json")

    def test_verb_refuses_an_unknown_lane(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main(["history", "nope"]) == 2
        assert "unknown lane 'nope'" in capsys.readouterr().err

    def test_verb_survives_a_project_with_no_lanes(self, tmp_path,
                                                   monkeypatch, capsys):
        self._project(tmp_path, monkeypatch, "schema_version = 1\n")
        assert run_gate.main(["history"]) == 0
        assert "(no lanes defined)" in capsys.readouterr().out

    def test_dry_run_through_main_records_nothing(self, tmp_path, monkeypatch,
                                                  capsys):
        repo, proj = self._project(tmp_path, monkeypatch)
        new_commit(repo, "a")
        assert run_gate.main(["suite", "--dry-run"]) == 0
        capsys.readouterr()
        assert not (proj / ".run-gate").exists()


class TestHistoryDegradedInputs:
    """Every "could not determine" path, proven to answer *indeterminate*
    rather than inventing a convenient value (AGENTS 'defaults are hazards',
    'absence for emptiness')."""

    @staticmethod
    def _no_git(monkeypatch):
        def boom(*_a, **_k):
            raise OSError("git is gone")
        monkeypatch.setattr(run_gate.subprocess, "run", boom)

    def test_dirtiness_is_indeterminate_when_git_cannot_run(self, tmp_path,
                                                            monkeypatch):
        repo, _ = make_history_repo(tmp_path)
        self._no_git(monkeypatch)
        assert run_gate.worktree_is_dirty(repo) is None

    def test_dirtiness_is_indeterminate_when_git_errors(self, tmp_path):
        assert run_gate.worktree_is_dirty(tmp_path / "not-a-repo") is None

    def test_git_operation_is_indeterminate_when_git_cannot_run(self, tmp_path,
                                                                monkeypatch):
        repo, _ = make_history_repo(tmp_path)
        self._no_git(monkeypatch)
        assert run_gate.git_operation_in_progress(repo) is None

    def test_git_operation_is_none_outside_a_repo(self, tmp_path):
        assert run_gate.git_operation_in_progress(tmp_path) is None

    def test_git_operation_resolves_a_relative_git_dir(self, tmp_path):
        """`rev-parse --git-dir` answers a RELATIVE path from inside the
        toplevel; resolving it against CWD instead of the worktree would
        look for the markers in the wrong place and always answer 'none'."""
        repo, _ = make_history_repo(tmp_path)
        (repo / ".git" / "MERGE_HEAD").write_text("deadbeef\n")
        assert run_gate.git_operation_in_progress(repo) == "MERGE_HEAD"

    def test_head_commit_is_none_when_git_cannot_run(self, tmp_path,
                                                     monkeypatch):
        repo, _ = make_history_repo(tmp_path)
        self._no_git(monkeypatch)
        assert run_gate.head_commit(repo) is None

    def test_head_commit_is_none_before_the_first_commit(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        assert run_gate.head_commit(empty) is None

    def test_history_table_must_be_a_table(self, tmp_path):
        _, proj = make_history_repo(
            tmp_path, HISTORY_LANE.replace("schema_version = 1",
                                           "schema_version = 1\nhistory = 3"))
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        assert "'history' must be a table" in str(exc.value)

    @pytest.mark.parametrize("body", ['["not", "a", "store"]', '{"lanes": 3}',
                                      '"scalar"'])
    def test_a_wrongly_shaped_store_reads_as_empty(self, tmp_path, body):
        path = tmp_path / "history.json"
        path.write_text(body)
        assert run_gate.load_history_store(path) == {"schema": 1, "lanes": {}}

    def test_a_missing_store_reads_as_empty(self, tmp_path):
        assert run_gate.load_history_store(tmp_path / "nope.json") == \
            {"schema": 1, "lanes": {}}

    def test_stats_over_nothing_report_nothing_not_zero(self):
        """`min 0.0s` would be a measurement nobody took."""
        assert run_gate.duration_stats([]) == {
            "count": 0, "min_seconds": None, "median_seconds": None,
            "max_seconds": None}
        assert run_gate.duration_stats([{"duration_seconds": None}])["count"] \
            == 0
        assert run_gate._fmt_seconds(None) == "-"
        assert "none recorded" in run_gate._fmt_stats(
            "passes", run_gate.duration_stats([]))

    def test_odd_and_even_series_both_get_a_median(self):
        assert run_gate.duration_stats(
            [{"duration_seconds": v} for v in (1.0, 9.0, 2.0)]
        )["median_seconds"] == 2.0
        assert run_gate.duration_stats(
            [{"duration_seconds": v} for v in (1.0, 2.0, 3.0, 100.0)]
        )["median_seconds"] == 2.5

    def test_a_record_with_no_commit_still_renders(self, tmp_path,
                                                   monkeypatch, capsys):
        repo, proj = make_history_repo(tmp_path)
        monkeypatch.setattr(run_gate, "head_commit", lambda _wt: None)
        record_run(proj, repo, seconds=1.0)
        run_gate.cmd_history({"suite": {}}, proj, {"lanes": {}},
                             proj / "run-gate.toml", {}, None, "suite", False)
        assert "(no commit)" in capsys.readouterr().out


class TestHistoryReadScope:
    """Review blocker B1: `--worktree` redirects the READ exactly as R-36f
    redirects the WRITE. Answering with the invoking checkout's medians under
    another tree's name is the single failure this feature exists to prevent
    — and it would be silent, which is worse than wrong."""

    def _two_trees(self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        other = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        str(other), "-b", "side"], check=True,
                       capture_output=True, text=True)
        # Two distinct measurements, one per tree.
        new_commit(repo, "a")
        record_run(proj, repo, seconds=11.0)
        rec = run_gate.start_run_record("suite", other, repo)
        rec["_started_monotonic"] = time.monotonic() - 77.0
        run_gate.finish_run_record(rec, exit_code=0)
        run_gate.record_invocation(other / "proj", other, rec, 10)
        return repo, proj, other

    def test_worktree_flag_reads_that_trees_store(self, tmp_path, monkeypatch):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        out = run_tool(proj, "history", "suite", "--worktree", str(other),
                       "--json")
        assert out.returncode == 0, out.stderr
        payload = json.loads(out.stdout)
        assert payload["lanes"]["suite"]["latest"]["duration_seconds"] == 77.0
        assert payload["store"] == str(other / "proj/.run-gate/history.json")
        assert payload["worktree_scope"] == str(other)

    def test_without_the_flag_the_invoking_checkout_answers(self, tmp_path,
                                                            monkeypatch):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        payload = json.loads(run_tool(proj, "history", "suite", "--json").stdout)
        assert payload["lanes"]["suite"]["latest"]["duration_seconds"] == 11.0
        assert payload["worktree_scope"] is None

    def test_the_answer_names_the_tree_it_describes(self, tmp_path,
                                                    monkeypatch):
        """R-05: mechanics are disclosed, never left to be inferred."""
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        out = run_tool(proj, "history", "suite", "--worktree", str(other))
        assert out.returncode == 0, out.stderr
        assert f"tree:  {other}" in out.stdout
        assert str(other / "proj/.run-gate/history.json") in out.stdout
        assert "77.0s" in out.stdout

    def test_read_and_write_scopes_agree(self, tmp_path, monkeypatch):
        """The end-to-end statement of B1: run a lane against tree B, then
        ask about tree B, and get the run you just did."""
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        new_commit(repo, "a")
        other = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        str(other), "-b", "side"], check=True,
                       capture_output=True, text=True)
        assert run_tool(proj, "suite", "--worktree", str(other)).returncode == 0
        assert not (proj / ".run-gate").exists()
        payload = json.loads(run_tool(proj, "history", "suite", "--worktree",
                                      str(other), "--json").stdout)
        assert payload["lanes"]["suite"]["latest"]["worktree"] == str(other)
        assert payload["lanes"]["suite"]["latest"]["outcome"] == "pass"

    def test_a_nonexistent_worktree_refuses_rather_than_reporting_no_data(
            self, tmp_path):
        """Falling back to the invoking checkout here would reintroduce B1
        through the ERROR path: an unvalidated override computes a store path
        under a tree that is not there and answers "(not written yet)" —
        silence presented as tree B's answer."""
        repo, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "history", "--worktree", str(tmp_path / "nope"))
        assert out.returncode == 2, out.stdout + out.stderr
        assert "not a directory" in out.stderr and "--worktree" in out.stderr
        assert "not written yet" not in out.stdout
        assert "Traceback" not in out.stderr

    def test_a_non_git_worktree_refuses_with_gits_own_message(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        outside = tmp_path / "plain-dir"
        outside.mkdir()
        out = run_tool(proj, "history", "--worktree", str(outside))
        assert out.returncode == 3, out.stdout + out.stderr
        assert "not written yet" not in out.stdout
        assert "Traceback" not in out.stderr

    def test_unflagged_history_needs_no_git(self, tmp_path, monkeypatch,
                                            capsys):
        """Resolution is opt-in so a read stays answerable where git is not:
        an unflagged query must not acquire a git dependency it never had."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, seconds=3.0)
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        calls = []
        real = run_gate.resolve_repo_and_worktree
        monkeypatch.setattr(run_gate, "resolve_repo_and_worktree",
                            lambda *a, **k: calls.append(a) or real(*a, **k))
        assert run_gate.main(["history", "suite"]) == 0
        assert calls == []
        assert "3.0s" in capsys.readouterr().out


class TestHistoryFlushIsAtMostOnce:
    """Review blocker B2: the normal-path flush runs INSIDE main()'s try and
    is not instantaneous (it spawns `git check-ignore` and may wait up to
    HISTORY_LOCK_TIMEOUT on the lock). A Ctrl-C landing there is caught by
    the BaseException handler, which flushes again — and a second flush of an
    already-consumed record used to raise KeyError, replacing the real signal
    with a traceback R-36h/R-04 both forbid."""

    def test_a_second_flush_is_a_clean_no_op(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        rec = run_gate.start_run_record("suite", repo, repo)
        rec["_project_dir"] = proj
        rec["_keep"] = 10
        rec["_started_monotonic"] = time.monotonic() - 5.0
        run_gate.flush_run_record(rec, exit_code=0)
        first = lane_slot(proj)["latest"]["duration_seconds"]
        run_gate.flush_run_record(rec, error=KeyboardInterrupt())  # no raise
        assert lane_slot(proj)["latest"]["duration_seconds"] == first
        assert lane_slot(proj)["latest"]["outcome"] == "pass"

    def test_ctrl_c_during_the_write_surfaces_as_keyboardinterrupt(
            self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        new_commit(repo, "a")
        seen = []

        def interrupted_write(*_a, **_k):
            seen.append(1)
            raise KeyboardInterrupt

        monkeypatch.setattr(run_gate, "record_invocation", interrupted_write)
        # The real signal, not KeyError('_started_monotonic').
        with pytest.raises(KeyboardInterrupt):
            run_gate.main(["suite"])
        assert seen == [1], "the second flush must not re-enter the record"

    def test_a_record_with_no_start_stamp_never_raises(self, tmp_path):
        """Defence in depth behind the sentinel: finish_run_record must not
        be the thing that turns a recording problem into a traceback."""
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        rec = run_gate.start_run_record("suite", repo, repo)
        rec.pop("_started_monotonic")
        run_gate.finish_run_record(rec, exit_code=0)
        assert rec["duration_seconds"] is None
        assert rec["history_eligible"] is False
        assert "no duration was measured" in rec["excluded_reason"]

    def test_an_unmeasured_run_never_reaches_history(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        new_commit(repo, "a")
        record_run(proj, repo, seconds=10.0)
        rec = run_gate.start_run_record("suite", repo, repo)
        rec.pop("_started_monotonic")
        run_gate.finish_run_record(rec, exit_code=0)
        run_gate.record_invocation(proj, repo, rec, 10)
        slot = lane_slot(proj)
        assert len(slot["history"]) == 1
        assert slot["history"][0]["duration_seconds"] == 10.0
        assert slot["latest"]["duration_seconds"] is None


class TestJsonFlagScope:
    """Review S1: `--json` was accepted and ignored everywhere but `history`,
    so a consumer piping `--list --json` into a parser got a TSV. Same rule
    as RG-1's --worktree and RG-26's --base: refuse by name, never no-op."""

    @pytest.mark.parametrize("args", [["--list", "--json"],
                                      ["--json"],
                                      ["suite", "--json"],
                                      ["doctor", "--json"]])
    def test_other_verbs_refuse_json_by_name(self, tmp_path, args):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, *args)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "--json is honored by the `history` verb only" in out.stderr
        assert "Traceback" not in out.stderr

    def test_list_without_json_is_unchanged(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "--list")
        assert out.returncode == 0
        assert out.stdout == "suite\tcommand\ttester-unified\n"

    def test_usage_says_where_json_is_accepted(self, tmp_path):
        _, proj = make_history_repo(tmp_path)
        out = run_tool(proj, "--help")
        assert "`history` ONLY" in out.stdout
        assert "REFUSES it by name" in out.stdout
        assert "run-gate.py history [LANE] [--worktree PATH] [--json]" \
            in out.stdout


class TestHistoryReadScopeInProcess:
    """The B1 and S1 paths driven through `main()` in-process — the
    subprocess tests above prove the shipped entrypoint, these reach the
    dispatch wiring where coverage can see it."""

    def _two_trees(self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path)
        other = tmp_path / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        str(other), "-b", "side"], check=True,
                       capture_output=True, text=True)
        new_commit(repo, "a")
        record_run(proj, repo, seconds=11.0)
        rec = run_gate.start_run_record("suite", other, repo)
        rec["_started_monotonic"] = time.monotonic() - 77.0
        run_gate.finish_run_record(rec, exit_code=0)
        run_gate.record_invocation(other / "proj", other, rec, 10)
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        return repo, proj, other

    def test_worktree_read_names_and_reads_that_tree(self, tmp_path,
                                                     monkeypatch, capsys):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        assert run_gate.main(["history", "suite", "--worktree",
                              str(other)]) == 0
        out = capsys.readouterr().out
        assert f"tree:  {other}" in out
        assert "THAT tree, not the invoking checkout" in out
        assert str(other / "proj/.run-gate/history.json") in out
        assert "77.0s" in out and "11.0s" not in out

    def test_worktree_read_json_carries_the_scope(self, tmp_path, monkeypatch,
                                                  capsys):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        assert run_gate.main(["history", "suite", "--worktree", str(other),
                              "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["worktree_scope"] == str(other)
        assert payload["lanes"]["suite"]["latest"]["duration_seconds"] == 77.0

    def test_nonexistent_worktree_refuses_instead_of_answering(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        assert run_gate.main(["history", "--worktree",
                              str(tmp_path / "nope")]) == 2
        captured = capsys.readouterr()
        assert "not a directory" in captured.err
        assert "not written yet" not in captured.out

    def test_non_git_worktree_refuses_with_gits_own_status(self, tmp_path,
                                                           monkeypatch,
                                                           capsys):
        repo, proj, other = self._two_trees(tmp_path, monkeypatch)
        plain = tmp_path / "plain"
        plain.mkdir()
        assert run_gate.main(["history", "--worktree", str(plain)]) == 3
        assert "not written yet" not in capsys.readouterr().out

    @pytest.mark.parametrize("args", [["--list", "--json"], ["--json"],
                                      ["suite", "--json"], ["doctor", "--json"]])
    def test_json_outside_history_refuses_by_name(self, tmp_path, monkeypatch,
                                                  capsys, args):
        repo, proj = make_history_repo(tmp_path)
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        assert run_gate.main(args) == 2
        assert "--json is honored by the `history` verb only" \
            in capsys.readouterr().err


class TestResumeAndProgressAlways:
    """RG-33 (R-38): every `kind = "assay"` lane is invoked with `--resume`
    and `--progress .assay/progress-<assay_lane>.jsonl`, unconditionally.
    Measured on dstdns's `sql-mutation` lane (2026-09-02): three
    budget-capped retries, each restarting file 1 from mutant #1, because
    the constructed argv never carried `--resume` and no
    `.assay/mutation-state/` was ever written. What assay DOES with the two
    flags (no-op without R2; resume keyed by the file's exact bytes) is
    assay's contract and is proven in assay's own suite
    (`tests/test_mutation_resume_sharding.py`,
    `tests/test_mutation_progress_budget_plan.py`); these tests prove only
    that run-gate hands them over, in the executed argv, on every runner.
    Controlled wrong implementation (the pre-fix builder) reddens all five.
    """

    _LANE = {"assay_lane": "sql_mutation",
             "assay_command": ["./tools/assay/assay.pyz"], "pins": {}}

    def test_inner_carries_both_flags_after_the_verdict(self):
        inner = run_gate.build_assay_inner(self._LANE, Path("/proj"))
        assert ("run sql_mutation --file assay.toml "
                "--verdict-json .assay/verdict-sql_mutation.json "
                "--resume --progress .assay/progress-sql_mutation.jsonl") in inner

    def test_request_base_still_comes_last(self):
        """RG-26's flag keeps its position: appended only for a delegating
        lane, after everything the lane always gets."""
        inner = run_gate.build_assay_inner(self._LANE, Path("/proj"),
                                           request_base="deadbeef")
        assert ("--progress .assay/progress-sql_mutation.jsonl "
                "--request-base deadbeef") in inner

    def test_progress_lands_in_the_directory_the_inner_creates(self):
        """The progress file lives beside the verdict under `.assay/` — the
        directory `mkdir -p .assay` creates one step earlier and every
        adopter git-ignores (R-32). A progress file anywhere in the judged
        tree would make assay refuse NO_MEASUREMENT/DIRTY_TREE on the lane's
        NEXT run, so the location is not a style choice."""
        inner = run_gate.build_assay_inner(self._LANE, Path("/proj"))
        assert inner.index("mkdir -p .assay") < inner.index(
            "--progress .assay/progress-sql_mutation.jsonl")
        assert "--progress .assay/" in inner and "--progress /" not in inner

    def test_the_executed_judge_receives_both_flags(
            self, tmp_path, monkeypatch, capfd):
        """RG-28's echo oracle: the judge prints the argv it was EXECUTED
        with, so this is the real handover, not the builder's string."""
        cfg = ASSAY_LANE_CFG.replace('environment = "tester-unified"',
                                     'environment = "host"', 1)
        TestComparisonBasePassthrough._project(self, tmp_path, monkeypatch, cfg)
        fake_docker(tmp_path, monkeypatch)
        install_fake_assay(monkeypatch, """\
            #!/bin/sh
            case "$*" in
              *"lanes --json"*) echo '{"inventory_schema": 1, "lanes": [
                  {"name": "ui_unit", "base_source": "request",
                   "external_tools": [], "argv0": null, "language": null}]}' ;;
              *) echo "JUDGE-ARGV: $*" ;;
            esac
            exit 0
        """)
        assert run_gate.main(["ui-unit", "--base", "deadbeef"]) == 0
        out = capfd.readouterr().out
        assert ("JUDGE-ARGV: run ui_unit --file assay.toml "
                "--verdict-json .assay/verdict-ui_unit.json "
                "--resume --progress .assay/progress-ui_unit.jsonl "
                "--request-base deadbeef") in out

    def test_dry_run_docker_argv_discloses_both_flags(
            self, tmp_path, monkeypatch, capsys):
        """R-05: the printed container argv is the one that would run."""
        TestComparisonBasePassthrough._project(self, tmp_path, monkeypatch)
        log = fake_docker_executing(tmp_path, monkeypatch)
        TestComparisonBasePassthrough._judge(monkeypatch, "request")
        assert run_gate.main(["ui-unit", "--base", "deadbeef",
                              "--dry-run"]) == 0
        argv_line = _docker_argv_line(capsys.readouterr().out)
        assert "--resume --progress .assay/progress-ui_unit.jsonl" in argv_line
        assert lane_runs(log) == []

    # --- the judge floor (R-38, last bullet) --------------------------------

    def _pinned(self, version):
        pin = {"sha256": "tools/assay/assay.pyz.sha256"}
        if version is not None:
            pin["version"] = version
        return {**self._LANE, "pins": {"assay": pin}}

    def test_a_pin_below_the_floor_refuses_by_name_before_anything_runs(self):
        """cmru's real state when this landed: assay 2.3.0 pinned, which
        knows neither flag. Refused at construction with lane, pin, declared
        version, floor and remedy — never inside the container under
        assay's own `unrecognized arguments` line."""
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.build_assay_inner(self._pinned("2.3.0"), Path("/proj"))
        msg = str(exc.value)
        assert "lane 'sql_mutation': pin 'assay' declares assay 2.3.0" in msg
        assert "below 2.4.1" in msg and "--resume" in msg and "--progress" in msg
        assert "re-pin the judge to >= 2.4.1" in msg

    @pytest.mark.parametrize("declared", ["2.4.1", "v2.4.1", "3.2.0", "4.1.0"])
    def test_a_pin_at_or_above_the_floor_carries_the_flags(self, declared):
        inner = run_gate.build_assay_inner(self._pinned(declared), Path("/proj"))
        assert "--resume --progress .assay/progress-sql_mutation.jsonl" in inner

    def test_a_short_claim_below_the_floor_still_refuses(self):
        """`2.4` is a claim of the 2.4 line; the tuple order says it is
        below 2.4.1, and the message names what was declared verbatim."""
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.build_assay_inner(self._pinned("2.4"), Path("/proj"))
        assert "declares assay 2.4," in str(exc.value)

    @pytest.mark.parametrize("declared", [None, "", "latest", "4.1.0rc1"])
    def test_no_comparable_claim_is_not_held_to_the_floor(self, declared):
        """No declared version, or one that is not dotted integers, is no
        claim; the flags still go, and an old judge fails loudly by itself."""
        inner = run_gate.build_assay_inner(self._pinned(declared), Path("/proj"))
        assert "--resume --progress" in inner

    @pytest.mark.parametrize("declared, expected", [
        ("2.4.1", (2, 4, 1)), ("v4.1.0", (4, 1, 0)), ("3.1", (3, 1)),
        ("", None), ("latest", None), ("4.1.0rc1", None), ("v", None)])
    def test_declared_version_tuple(self, declared, expected):
        assert run_gate.declared_version_tuple(declared) == expected


# ---------------------------------------------------------------------------
# RG-35 / R-39 — the inflight record and re-attach
# ---------------------------------------------------------------------------

def fake_docker_stateful(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A docker shim that keeps CONTAINER STATE, and the state directory.

    `fake_docker` cannot express RG-35's question at all: "is the container
    this record names still there, and what did it do?" is a question about
    state, and a shim that answers a canned line for every `inspect` would
    pin construction while proving nothing about the decision. Here
    `run -d` CREATES a container (a file named after it, holding
    `<status> <exit-code>`), `inspect` answers from it or exits 1 like real
    docker's `No such object`, `wait` returns its recorded code, and
    `rm -f` destroys it. Touching `<state>/.hang` makes `logs -f` block, so
    a test can kill the client while the container is still running — the
    exact sequence RG-35 was filed for.
    """
    log = fake_docker(tmp_path, monkeypatch)
    state = tmp_path / "docker-state"
    state.mkdir(exist_ok=True)
    shim = shim_dir_of(monkeypatch) / "docker"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\037' "$@" >> "{log}"
        printf '\\n' >> "{log}"
        S="{state}"
        cmd="$1"; shift
        case "$cmd" in
          run)
            name=""; prev=""; detached=no
            for a in "$@"; do
              [ "$prev" = "--name" ] && name="$a"
              [ "$a" = "-d" ] && detached=yes
              prev="$a"
            done
            [ "$detached" = yes ] && printf 'running 0\\n' > "$S/$name"
            echo "sha256:fakeid-$name"
            ;;
          inspect)
            name="$3"
            [ -f "$S/$name" ] || {{ echo "Error: No such object: $name" >&2; exit 1; }}
            read status code < "$S/$name"
            printf '%s|%s|2026-09-02T12:00:00Z\\n' "$status" "$code"
            ;;
          logs)
            while [ -f "$S/.hang" ]; do sleep 0.2; done
            echo "FAKE-LOGS-LINE"
            ;;
          wait)
            [ -f "$S/$1" ] || {{ echo "Error: No such container" >&2; exit 1; }}
            read status code < "$S/$1"
            printf '%s\\n' "$code"
            ;;
          rm)
            for last; do :; done
            rm -f "$S/$last"
            ;;
        esac
        exit 0
    """))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return log, state


def state_containers(state: Path) -> list[str]:
    return sorted(p.name for p in state.glob("run-gate-*"))


class TestReattachAcrossADeadClient:
    """RG-35 (R-39). The controlled WRONG implementation is the one that
    shipped through rev 33: `docker run -d` … `docker rm -f` in a `finally`
    the killed client never reaches, and nothing on disk that names the
    container it left behind. Against it this test observes a SECOND
    `docker run` for the same lane and the same commit — the one-gate rule
    broken by the tool, on a host that shares 8 cores with a production
    workload.
    """

    def _repo(self, tmp_path):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE)
        return repo, proj

    def test_a_killed_client_leaves_a_container_the_next_run_re_attaches_to(
            self, tmp_path, monkeypatch):
        repo, proj = self._repo(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        (state / ".hang").write_text("")      # `docker logs -f` blocks
        client = subprocess.Popen([sys.executable, str(_TOOL_INVOKE), "suite"],
                                  cwd=proj, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 30
        while not state_containers(state) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert state_containers(state), "the fixture never started a container"
        # …and then until the client has written its record, or 3 s pass.
        # Waiting is not an assertion: the PRE-FIX client never writes one,
        # so it is killed on the deadline and still produces the duplicate
        # this test exists to catch.
        record = proj / ".run-gate" / "inflight" / "suite.json"
        deadline = time.monotonic() + 3
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        client.kill()
        client.wait(timeout=30)
        first = state_containers(state)
        assert len(lane_runs(log)) == 1

        (state / ".hang").unlink()            # the container finishes
        out = run_tool(proj, "suite")
        assert len(lane_runs(log)) == 1, (
            "a SECOND container was started for a lane that already had one "
            f"running: {lane_runs(log)}")
        assert f"run-gate: re-attached to {first[0]}" in out.stdout, out.stdout
        assert out.returncode == 0
        assert state_containers(state) == []   # removed in the finally
        assert not (proj / ".run-gate" / "inflight" / "suite.json").exists()


def plant_inflight(proj: Path, repo: Path, state: Path | None, *,
                   lane: str = "suite", container: str = "run-gate-planted",
                   status: str | None = "running", code: int = 0,
                   commit: str | None = "HEAD", **over) -> Path:
    """Write an inflight record for `lane`, optionally giving the stateful
    shim a container to match it. `commit="HEAD"` means the repo's real HEAD
    (the matching case); anything else is used verbatim."""
    if commit == "HEAD":
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    path = proj / ".run-gate" / "inflight" / f"{lane}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "lane": lane, "container": container,
               "container_id": f"sha256:fakeid-{container}",
               "started_at": "2026-09-02T11:00:00Z",
               "started_epoch": time.time() - 754,
               "commit": commit, "worktree": str(repo),
               "project_dir": str(proj), "verdict": None, "progress": None,
               "revision": run_gate.__revision__}
    payload.update(over)
    path.write_text(json.dumps(payload))
    if state is not None and status is not None:
        (state / container).write_text(f"{status} {code}\n")
    return path


def _docker_calls(log: Path) -> list[list[str]]:
    return [[p for p in line.split("\x1f") if p != ""]
            for line in log.read_text().splitlines()]


class TestInflightRecordDecisions:
    """RG-35 / R-39, branch by branch, driven THROUGH `main()` in-process —
    the killed-client test above proves the shipped entrypoint, these reach
    the decision (and its disclosure) where coverage can see it. Every one is
    disclosed by name: silence is what turns a surviving container into a
    duplicate."""

    def _fixture(self, tmp_path, monkeypatch, config=SIMPLE_LANE):
        repo, proj = make_history_repo(tmp_path, config)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        return repo, proj, log, state

    def test_an_exited_container_is_collected_with_its_real_exit_code(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(tmp_path / "ev"))
        plant_inflight(proj, repo, state, status="exited", code=7)
        assert run_gate.main(["suite"]) == 7
        out = capsys.readouterr().out
        assert ("run-gate: collected run-gate-planted (exited 7 at "
                "2026-09-02T12:00:00Z)") in out
        assert lane_runs(log) == []            # nothing new was started
        assert (tmp_path / "ev" / "run-gate-planted.log").exists()
        assert "logs preserved at" in out
        assert not (proj / ".run-gate" / "inflight" / "suite.json").exists()
        # RW-3: the collected run joins history ONCE, with its real outcome.
        assert lane_slot(proj)["latest"]["exit_code"] == 7

    def test_a_running_container_is_re_attached_not_re_run(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        plant_inflight(proj, repo, state, status="running", code=0)
        assert run_gate.main(["suite"]) == 0
        out = capsys.readouterr().out
        assert ("run-gate: re-attached to run-gate-planted (started "
                "2026-09-02T11:00:00Z, running for 12m 34s)") in out
        assert "re-attach — no new container was started" in out
        assert lane_runs(log) == []
        # `--since` the CONTAINER's start, so a reconnecting client sees the
        # run from its beginning rather than only what happened after it
        # arrived.
        assert ["logs", "-f", "--since", "2026-09-02T11:00:00Z",
                "run-gate-planted"] in _docker_calls(log)
        assert not (proj / ".run-gate" / "inflight" / "suite.json").exists()

    def test_a_gone_container_is_reported_cleared_and_the_lane_runs_fresh(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        record = plant_inflight(proj, repo, state, status=None)
        assert run_gate.main(["suite"]) == 0
        assert ("run-gate: inflight record names run-gate-planted (started "
                "2026-09-02T11:00:00Z) but no such container exists"
                ) in capsys.readouterr().out
        assert len(lane_runs(log)) == 1        # ran fresh, exactly once
        assert not record.exists()

    def test_a_record_for_another_commit_refuses_and_names_both(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        plant_inflight(proj, repo, state, commit="dead" * 10)
        assert run_gate.main(["suite"]) == 2
        err = capsys.readouterr().err
        assert "deaddeaddead" in err and "--fresh" in err
        assert "will not start a second container" in err
        assert lane_runs(log) == []

    def test_an_unresolvable_head_refuses_too(self, tmp_path, monkeypatch,
                                              capsys):
        """"Could not determine" resolves toward refusal, exactly as it does
        for history eligibility: attaching a run to a commit nobody could
        name is the substitution this record exists to prevent."""
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        plant_inflight(proj, repo, state)
        monkeypatch.setattr(run_gate, "head_commit", lambda w: None)
        assert run_gate.main(["suite"]) == 2
        assert "is now at None" in capsys.readouterr().err
        assert lane_runs(log) == []

    def test_fresh_removes_the_recorded_container_and_runs_anew(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        plant_inflight(proj, repo, state)
        assert run_gate.main(["suite", "--fresh"]) == 0
        assert ("run-gate: --fresh: removing inflight container "
                "run-gate-planted (started 2026-09-02T11:00:00Z, running)"
                ) in capsys.readouterr().out
        assert ["rm", "-f", "run-gate-planted"] in [
            c[:3] for c in _docker_calls(log) if c[0] == "rm"]
        assert len(lane_runs(log)) == 1

    def test_fresh_without_a_record_says_so_and_runs(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        assert run_gate.main(["suite", "--fresh"]) == 0
        assert ("run-gate: --fresh: no inflight record for lane 'suite' — "
                "nothing to remove") in capsys.readouterr().out
        assert len(lane_runs(log)) == 1

    def test_dry_run_discloses_a_live_record_and_touches_nothing(
            self, tmp_path, monkeypatch, capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        record = plant_inflight(proj, repo, state)
        before = record.read_text()
        assert run_gate.main(["suite", "--dry-run"]) == 0
        assert ("run-gate: DRY RUN: an inflight record names container "
                "run-gate-planted (started 2026-09-02T11:00:00Z, state "
                "running) — a live run would re-attach to it or collect it"
                ) in capsys.readouterr().out
        assert lane_runs(log) == []
        assert record.read_text() == before    # not cleared, not rewritten
        assert (state / "run-gate-planted").exists()   # not removed

    def test_dry_run_discloses_a_lost_record_too(self, tmp_path, monkeypatch,
                                                 capsys):
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        record = plant_inflight(proj, repo, state, status=None)
        assert run_gate.main(["suite", "--dry-run"]) == 0
        assert ("state gone) — a live run would report it lost"
                in capsys.readouterr().out)
        assert record.exists()

    def test_an_unreadable_container_state_refuses_rather_than_guessing(
            self, tmp_path, monkeypatch, capsys):
        """Guessing "gone" on an answer that does not parse would start the
        duplicate container this whole mechanism exists to prevent."""
        repo, proj, log, state = self._fixture(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        shim.write_text("#!/bin/sh\necho 'who knows'\nexit 0\n")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        plant_inflight(proj, repo, None)
        assert run_gate.main(["suite"]) == 3
        err = capsys.readouterr().err
        assert "could not read the state of container run-gate-planted" in err
        assert "refusing to guess" in err


class TestPinKeysAreValidated:
    """RG-32 / R-04. `[lanes.<n>.pins.<p>].budget` looked exactly like the
    real, load-bearing lane-level `budget` one nesting level up and was dead
    text: three readers on ONE dstdns session (two Opus review agents and the
    controller) each read `pins.assay.budget = "90m"` as the governing bound
    of a mutation lane whose `assay.toml` actually said `120m`. Refused by
    name now — a BREAKING config change with its migration in CHANGES."""

    PIN_LANE = """\
        schema_version = 1
        [environments.tester-unified]
        image = "tester-unified:local"
        [lanes.sql-mutation]
        kind = "assay"
        environment = "tester-unified"
        assay_lane = "cw2b_schema"
        assay_command = ["./tools/assay.pyz"]
        clean_tree = false
        [lanes.sql-mutation.pins.assay]
        version = "4.1.0"
        sha256 = "tools/assay.pyz.sha256"
    """

    def _load(self, tmp_path, extra: str):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.PIN_LANE + extra)
        with pytest.raises(run_gate.GateError) as exc:
            run_gate.load_config(proj)
        return str(exc.value)

    def test_budget_under_a_pin_refuses_and_names_its_real_owner(
            self, tmp_path):
        msg = self._load(tmp_path, '        budget = "90m"\n')
        assert "pin 'assay' declares 'budget'" in msg
        assert "run-gate never enforced it" in msg
        # The remedy names the file AND the exact table that owns the value.
        assert "assay.toml [lanes.cw2b_schema]" in msg
        assert "delete this key" in msg
        assert "the lane-level run-gate 'budget' stays advisory" in msg
        assert "[lanes.sql-mutation].pins.assay" in msg

    def test_any_other_unknown_pin_key_refuses_too(self, tmp_path):
        """A pin table that silently accepted anything is HOW `budget`
        survived there; the generic check is the durable half of the fix."""
        msg = self._load(tmp_path, '        budget_hint = "90m"\n')
        assert "unknown key(s) budget_hint" in msg
        assert "allowed: sha256, version" in msg
        assert "[lanes.sql-mutation].pins.assay" in msg

    def test_the_two_real_pin_keys_still_load(self, tmp_path):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.PIN_LANE)
        cfg, _, _, _ = run_gate.load_config(proj)
        assert cfg["lanes"]["sql-mutation"]["pins"]["assay"] == {
            "version": "4.1.0", "sha256": "tools/assay.pyz.sha256"}

    def test_a_lane_level_budget_is_still_accepted_and_still_advisory(
            self, tmp_path, monkeypatch, capsys):
        """The whole point of the refusal is that ONE of the two lookalikes
        is real. That one keeps working, unchanged."""
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.PIN_LANE.replace(
            'clean_tree = false', 'clean_tree = false\nbudget = "120m"'))
        cfg, _, _, _ = run_gate.load_config(proj)
        assert cfg["lanes"]["sql-mutation"]["budget"] == "120m"


class TestContainerFinishPathsInProcess:
    """The finish (`await_container`) and the start refusals, driven through
    `main()`. These behaviours predate RG-35 and were proven through the
    shipped entrypoint; RG-35 moved them into one shared finish, so they are
    re-proven here where coverage can see the code that now serves the
    fresh, re-attached and collected paths alike."""

    def _project(self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        return repo, proj

    def _shim(self, tmp_path, monkeypatch, body: str) -> Path:
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        shim.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\\037' "$@" >> "{log}"
            printf '\\n' >> "{log}"
            {body}
        """))
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        return log

    def test_no_docker_on_path_is_an_infrastructure_refusal(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate.shutil, "which", lambda _n: None)
        assert run_gate.main(["suite"]) == 3
        assert "docker not found on PATH" in capsys.readouterr().err

    def test_a_failed_docker_run_preserves_partial_logs_and_names_the_tail(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(tmp_path / "ev"))
        self._shim(tmp_path, monkeypatch, """\
            case "$1" in
              run) echo "pull access denied" >&2; exit 125 ;;
              logs) echo "PARTIAL-ENTRYPOINT-OUTPUT" ;;
              rm) : ;;
            esac
            exit 0
        """)
        assert run_gate.main(["suite"]) == 3
        err = capsys.readouterr().err
        assert "docker run failed (exit 125)" in err
        assert "pull access denied" in err
        assert "partial container logs:" in err

    def test_an_unreadable_exit_status_refuses_and_keeps_the_logs(
            self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(tmp_path / "ev"))
        self._shim(tmp_path, monkeypatch, """\
            case "$1" in
              run) echo "fake-container-id" ;;
              logs) echo "SOME-OUTPUT"; exit 1 ;;
              wait) echo "not-a-number" ;;
              rm) : ;;
            esac
            exit 0
        """)
        assert run_gate.main(["suite"]) == 3
        err = capsys.readouterr().err
        assert "docker logs exit 1" in err
        assert "could not read the container's exit status" in err
        assert "refusing to guess" in err
        assert list((tmp_path / "ev").glob("run-gate-*.log"))

    def test_an_unknown_lane_names_the_known_ones(self, tmp_path, monkeypatch,
                                                  capsys):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main(["nope"]) == 2
        err = capsys.readouterr().err
        assert "unknown lane 'nope'" in err and "known lanes: suite" in err


class TestInflightRecordStore:
    """The record is written under the SAME `.run-gate/` store discipline the
    history file already has (R-36f/g), and every failure to write it
    degrades to one warning — a lane must not die because its re-attach hint
    could not be saved."""

    def test_the_record_names_the_container_commit_and_tree(
            self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        # The record is cleared in the same finally that removes the
        # container, so it is read from INSIDE the run it describes.
        seen = {}
        real = run_gate.await_container

        def spy(*a, **k):
            seen["record"] = json.loads(
                (proj / ".run-gate" / "inflight" / "suite.json").read_text())
            return real(*a, **k)

        monkeypatch.setattr(run_gate, "await_container", spy)
        assert run_gate.main(["suite"]) == 0
        data = seen["record"]
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        assert data["container"] == lane_runs(log)[0][3]
        assert data["container_id"] == f"sha256:fakeid-{data['container']}"
        assert data["commit"] == head
        assert data["worktree"] == str(repo)
        assert data["project_dir"] == str(proj)
        assert data["revision"] == run_gate.__revision__
        assert data["schema"] == run_gate.INFLIGHT_SCHEMA
        assert data["verdict"] is None and data["progress"] is None
        assert data["started_at"].endswith("Z")
        assert isinstance(data["started_epoch"], float)

    def test_an_assay_lane_records_its_verdict_and_progress_paths(self):
        lane = {"kind": "assay", "assay_lane": "sql_mutation",
                "assay_command": ["./a.pyz"]}
        verdict, progress = run_gate.assay_artifact_paths(lane, Path("/p"))
        assert verdict == "/p/.assay/verdict-sql_mutation.json"
        assert progress == "/p/.assay/progress-sql_mutation.jsonl"
        assert run_gate.assay_artifact_paths(
            {"kind": "command"}, Path("/p")) == (None, None)

    def test_an_unignored_store_disables_re_attach_but_not_the_lane(
            self, tmp_path, monkeypatch, capsys):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE, ignore="nope\n")
        fake_docker_stateful(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda p, **k: Path("/phys"))
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        assert run_gate.main(["suite"]) == 0
        err = capsys.readouterr().err
        assert "re-attach record NOT written" in err
        assert "could not be confirmed git-ignored" in err
        assert "will start a second one (RG-35)" in err
        assert not (proj / ".run-gate" / "inflight").exists()

    def test_an_unwritable_store_degrades_to_a_warning(
            self, tmp_path, monkeypatch, capsys):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE)
        idir = proj / ".run-gate" / "inflight"
        idir.mkdir(parents=True)
        idir.chmod(0o500)
        try:
            assert run_gate.write_inflight_record(
                proj, repo, "suite", {"container": "c"}) is False
        finally:
            idir.chmod(0o700)
        assert "re-attach record not written" in capsys.readouterr().err

    def test_clearing_a_record_warns_instead_of_dying(
            self, tmp_path, monkeypatch, capsys):
        repo, proj = make_history_repo(tmp_path, SIMPLE_LANE)
        idir = proj / ".run-gate" / "inflight"
        idir.mkdir(parents=True)
        (idir / "suite.json").write_text("{}")
        idir.chmod(0o500)
        try:
            run_gate.clear_inflight_record(proj, "suite")
        finally:
            idir.chmod(0o700)
        assert "could not clear" in capsys.readouterr().err

    @pytest.mark.parametrize("body", ["not json", "[]", '{"lane": "suite"}'])
    def test_a_record_without_a_container_is_no_record(self, tmp_path, body):
        path = tmp_path / "rec.json"
        path.write_text(body)
        assert run_gate.load_inflight_record(path) is None

    def test_a_missing_record_is_no_record(self, tmp_path):
        assert run_gate.load_inflight_record(tmp_path / "absent.json") is None


class TestReattachedRunHistory:
    """RW-3: a re-attached or collected run is recorded ONCE, with the
    duration measured from the CONTAINER's start — not from the four seconds
    this client happened to be attached."""

    def test_duration_comes_from_the_container_start(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        rec = run_gate.start_run_record("suite", repo, repo)
        run_gate.adopt_inflight_start(
            rec, {"started_at": "2026-09-02T11:00:00Z",
                  "started_epoch": time.time() - 3600})
        run_gate.finish_run_record(rec, exit_code=0)
        assert rec["started_at"] == "2026-09-02T11:00:00Z"
        assert 3599 <= rec["duration_seconds"] <= 3610
        assert rec["history_eligible"] is True

    def test_a_record_without_an_epoch_keeps_the_client_clock(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        rec = run_gate.start_run_record("suite", repo, repo)
        rec["_started_monotonic"] = time.monotonic() - 5
        run_gate.adopt_inflight_start(
            rec, {"started_at": "2026-09-02T11:00:00Z", "started_epoch": None})
        run_gate.finish_run_record(rec, exit_code=0)
        assert 5 <= rec["duration_seconds"] < 30

    def test_a_lost_run_is_recorded_as_aborted_never_as_a_pass(self, tmp_path):
        repo, proj = make_history_repo(tmp_path)
        run_gate.record_lost_run(proj, repo, repo, "suite",
                                 {"container": "run-gate-x",
                                  "commit": "abc", "started_at": "T",
                                  "revision": 34}, 10)
        latest = lane_slot(proj)["latest"]
        assert latest["outcome"] == "aborted"
        assert latest["exit_code"] is None
        assert latest["history_eligible"] is False
        assert "run-gate-x is gone" in latest["excluded_reason"]
        assert lane_slot(proj)["history"] == []

    @pytest.mark.parametrize("epoch, expected", [
        (None, "for an unknown time"), (True, "for an unknown time"),
        ("2026", "for an unknown time")])
    def test_an_unknown_start_is_said_to_be_unknown(self, epoch, expected):
        assert run_gate._fmt_age(epoch) == expected

    def test_a_known_start_reads_as_minutes_and_seconds(self):
        assert run_gate._fmt_age(time.time() - 125) == "for 2m 05s"


class TestFreshFlagScope:
    """R-25/R-35's rule for RG-35's flag: a runner that starts no container
    of run-gate's own refuses --fresh by name instead of accepting it and
    doing nothing."""

    def _project(self, tmp_path, monkeypatch, config=HISTORY_LANE):
        repo, proj = make_history_repo(tmp_path, config)
        monkeypatch.setattr(sys, "argv", [str(proj / "run-gate.py")])
        return repo, proj

    @pytest.mark.parametrize("verb", ["doctor", "history", "validate-pointers"])
    def test_the_query_and_preflight_verbs_refuse_it(self, tmp_path,
                                                     monkeypatch, capsys, verb):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main([verb, "--fresh"]) == 2
        assert ("--fresh is honored on the run path only"
                in capsys.readouterr().err)

    def test_a_bare_invocation_refuses_it(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main(["--fresh"]) == 2
        assert ("--fresh is honored on the run path only"
                in capsys.readouterr().err)

    def test_a_host_lane_refuses_it(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch, """\
            schema_version = 1
            [lanes.suite]
            kind = "command"
            environment = "host"
            argv = ["true"]
            clean_tree = false
        """)
        assert run_gate.main(["suite", "--fresh"]) == 2
        err = capsys.readouterr().err
        assert "the built-in host environment" in err
        assert "nothing to re-attach to or replace" in err

    def test_an_exec_lane_refuses_it(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch, EXEC_LANE)
        assert run_gate.main(["suite", "--fresh"]) == 2
        assert "exec-mode environment" in capsys.readouterr().err

    def test_the_usage_text_documents_it(self, tmp_path, monkeypatch, capsys):
        self._project(tmp_path, monkeypatch)
        assert run_gate.main(["--help"]) == 0
        out = capsys.readouterr().out
        assert "[--fresh]" in out
        assert "RE-ATTACHES to the container that client left behind" in out
