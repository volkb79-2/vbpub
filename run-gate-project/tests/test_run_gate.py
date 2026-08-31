"""Unit suite for run-gate.py — construction pinned against a FAKE docker.

Construction is NOT acceptance: these pins prove argv SHAPE only (the P06/P07
lesson); live acceptance is oracle O4, run against real docker separately.
Every argv assertion compares the LIST, never a joined string.
"""

import atexit
import fcntl
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
            version = "2.2.0"
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
                             "tools/assay/assay-2.1.0.pyz"]

            [lanes.ciu.pins.assay]
            version = "2.1.0"
            sha256 = "tools/assay/assay-2.1.0.pyz.sha256"
        """)
        # Load-time sidecar existence is checked for project lanes too; the
        # docker shim only records argv, so a placeholder suffices.
        sidecar = proj / "tools/assay/assay-2.1.0.pyz.sha256"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("0" * 64 + "  assay-2.1.0.pyz\n")
        commit_all(repo, "vendor sidecar")
        log = fake_docker(tmp_path, monkeypatch)
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
                         "tools/assay/assay-2.1.0.pyz"]

        [lanes.ciu.pins.assay]
        version = "2.1.0"
        sha256 = "tools/assay/assay-2.1.0.pyz.sha256"
    """

    def _repo_with_worktree(self, tmp_path, config: str | None = None):
        repo = make_repo(tmp_path)
        proj = make_project(repo, config or self.ASSAY_CFG)
        # The pin sidecar must exist in the JUDGED tree (load-time existence
        # check is symmetric for project lanes now); content is irrelevant —
        # the docker shim only records the assembled command.
        sidecar = proj / "tools/assay/assay-2.1.0.pyz.sha256"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("0" * 64 + "  assay-2.1.0.pyz\n")
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
               "ast"}  # ast: RG-23 helper-wrapped env reads in --check-env
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
        assert '[ "$tok" = 2.1.0 ]' in inner and "version mismatch" in inner

    def test_prefix_version_never_matches_longer_reported(self, tmp_path):
        """Review fix: the old substring glob let declared '2.1' pass for a
        reported '2.11.0' — a claim the artifact never made."""
        proc = self._run_inner(tmp_path, "2.1", "assay 2.11.0")
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
        proc = self._run_inner(tmp_path, "2.1.0", "assay 9.9.9")
        assert proc.returncode != 0
        assert "version mismatch" in proc.stderr
        assert "2.1.0" in proc.stderr and "9.9.9" in proc.stderr

    def test_matching_version_runs_silently(self, tmp_path):
        proc = self._run_inner(tmp_path, "2.1.0", "assay 2.1.0")
        assert proc.returncode == 0, proc.stderr

    def test_punctuated_report_still_matches(self, tmp_path):
        """Trailing punctuation (v2.1.0,) or a leading bracket must not
        break the whole-token match."""
        proc = self._run_inner(tmp_path, "2.1.0", "(assay) reports: v2.1.0, ok")
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
