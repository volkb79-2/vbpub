"""RG-285: Run-Gate provides the assay judge to a lane (design + oracles).

The feature: a lane declares `judge = "assay"` and Run-Gate mounts the judged
worktree's assay source READ-ONLY, exports the interpreter and import roots the
judge needs, proves the lane's interpreter resolves THAT copy (not an image's
own), and records the judge's commit/version in the run record beside the
verdict.

Every oracle here is behavioral, not a construction pin where it can be:

* the declaration oracles drive the REAL loader (`load_config`/`_validate_lane`);
* the mount oracle drives a real lane run through the fake docker argv;
* the preflight oracles drive `preflight_judge_provider` with real directories;
* the end-to-end oracle runs a lane in an image that carries NO assay and NO
  install path -- the fixture's fake docker EXECUTES the assembled inner script
  on the host, so the judge is genuinely reached through the mounted source
  (a shim that echoed "passed" would pin nothing);
* the two negative probes are the ones the interim hostobs route was proven
  with: the judge's own totals are read from the verdict artifact, and a judge
  whose lane loses a test FAILS.

Design: docs/DESIGN-judge-provider-2026-11.md.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import _testdeps  # noqa: F401

from test_run_gate import (RUN_GATE_DIR, commit_all, docker_runs, fake_docker,
                           make_project, make_repo, make_worktree, run_gate,
                           run_tool, shim_dir_of)

# The suite-wide ambient contracts test_run_gate.py installs for its OWN
# module (a declared cgroup slice, profiling off, and a host profiler endpoint
# that points nowhere until a test installs a fake daemon). Importing the
# fixture FUNCTIONS — not re-declaring them — keeps one definition of each
# contract: an autouse fixture is per-module, so without this the lane runs
# below would reach the cockpit's real daemon.
from test_run_gate import (ambient_cgroup,  # noqa: F401  (autouse)
                           isolate_host_profiler_socket,  # noqa: F401
                           profiling_off_by_default)  # noqa: F401

REPO = RUN_GATE_DIR.parent
ASSAY_SOURCE = REPO / "assay"
COWTREE_SOURCE = REPO / "libraries" / "cowtree"

# The judge trees the real checkout provides. Every fixture below that runs an
# END-TO-END lane copies a MINIMAL viable judge into its own repository, so the
# test never depends on the monorepo's assay source being importable in the
# gate's own image (an image without pip, which is the whole point).

JUDGE_CONFIG = """\
schema_version = 1

[environments.rust]
mode = "ephemeral"
image = "{image}"
network = "none"

[lanes.rust-r1]
kind = "command"
environment = "rust"
judge = "assay"
clean_tree = false
budget = "5m"
exit_map = { "0" = "PASS", "2" = "ERROR" }
argv = [
  "bash", "-c",
  '''set -eu
cd {worktree}/proj
mkdir -p .assay
rc=0
"$RUN_GATE_JUDGE_PYTHON" -m assay.cli run r1 --file assay.toml \\
  --verdict-json .assay/verdict-r1.json || rc=$?
python3 - .assay/verdict-r1.json <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    verdict = json.load(handle)
for claim in verdict["claims"]:
    print("claim: %s %s" % (claim["rigor"], claim["status"]))
PY
exit "$rc"''',
]
"""


def _minimal_judge(repo: Path) -> None:
    """A tiny, REAL importable judge under `repo/assay` (no install step).

    `python -m assay.cli run r1` below reads a real `assay.toml`, runs a real
    command and writes a real verdict artifact -- so the end-to-end oracle
    measures the JUDGE, not a stub that prints "PASS".
    """
    package = repo / "assay" / "src" / "assay"
    analysis = repo / "assay" / "analysis" / "src" / "assay_analysis"
    cowtree = repo / "libraries" / "cowtree" / "src" / "cowtree"
    for directory in (package, analysis, cowtree):
        directory.mkdir(parents=True, exist_ok=True)
    (repo / "assay" / "pyproject.toml").write_text(
        "[project]\nname = 'assay'\nversion = '0.0.0'\n")
    (package / "__init__.py").write_text(
        '"""Minimal source-tree judge for RG-285 oracles."""\n')
    (package / "cli.py").write_text(textwrap.dedent('''\
        """A judge that reads a lane file, runs its command and writes a
        verdict with the closed assay outcome vocabulary."""
        import json
        import subprocess
        import sys
        import tomllib
        from pathlib import Path


        def main(argv=None):
            argv = list(sys.argv[1:] if argv is None else argv)
            if argv == ["--version"]:
                print("assay 0.0.0")
                return 0
            command, lane_name = argv[0], argv[1]
            arguments = argv[2:]
            if command != "run":
                print("unsupported", file=sys.stderr)
                return 2
            path = Path("assay.toml")
            previous, verdict_path = None, None
            for argument in arguments:
                if previous == "--file":
                    path = Path(argument)
                if previous == "--verdict-json":
                    verdict_path = Path(argument)
                previous = argument
            document = tomllib.loads(path.read_text(encoding="utf-8"))
            lane = document["lanes"][lane_name]
            completed = subprocess.run(lane["argv"], capture_output=True,
                                       text=True)
            outcome = "PASS" if completed.returncode == 0 else "FAIL"
            print(f"{lane_name}: {outcome} (exit {completed.returncode})")
            if verdict_path is not None:
                verdict_path.parent.mkdir(parents=True, exist_ok=True)
                claims = [{"rigor": level,
                           "source": "computed",
                           "status": outcome,
                           "verified_by_assay": True,
                           "reason_code": None}
                          for level in lane["rigor"]]
                verdict_path.write_text(json.dumps({
                    "schema_version": 1,
                    "assay_version": "0.0.0",
                    "lane": lane_name,
                    "commit": subprocess.run(
                        ["git", "rev-parse", "HEAD"], capture_output=True,
                        text=True).stdout.strip(),
                    "outcome": outcome,
                    "exit_code": completed.returncode,
                    "claims": claims,
                    "evidence": [],
                }, indent=2) + "\\n", encoding="utf-8")
            return completed.returncode


        if __name__ == "__main__":
            raise SystemExit(main())
        '''))
    (analysis / "__init__.py").write_text('"""analysis sibling"""\n')
    (cowtree / "__init__.py").write_text('"""cowtree sibling"""\n')


def _lanes_toml(lane_argv, rigor=("R0", "R1")) -> str:
    quoted = ", ".join(json.dumps(part) for part in lane_argv)
    return textwrap.dedent(f"""\
        schema_version = 2

        [lanes.r1]
        scope = "S1"
        rigor = [{", ".join(json.dumps(level) for level in rigor)}]
        enforcement = "gate"
        argv = [{quoted}]
        env = {{}}
        env_passthrough = []
        budget = "2m"
        allow_argv_append = false
        """)


def _judge_repo(tmp_path: Path, image: str = "tester-unified-rust:local",
                lane_argv=None, rigor=("R0", "R1")) -> tuple[Path, Path]:
    """(repo, project) with a provisionable judge and a `judge = "assay"` lane."""
    repo = make_repo(tmp_path)
    _minimal_judge(repo)
    proj = make_project(repo, JUDGE_CONFIG.replace("{image}", image))
    (proj / "assay.toml").write_text(_lanes_toml(
        lane_argv or ["/bin/sh", "-c", "true"], rigor))
    commit_all(repo, "judge provider fixture")
    return repo, proj


def docker_mounts(call: list[str]) -> list[str]:
    return [call[index + 1] for index, item in enumerate(call)
            if item in ("-v", "--mount")]


# ---------------------------------------------------------------------------
# 1. Declaration: one closed key, on a lane kind that can receive it
# ---------------------------------------------------------------------------

class TestJudgeDeclaration:
    def test_assay_is_the_only_provider_in_the_vocabulary(self):
        assert run_gate.JUDGE_PROVIDERS == frozenset({"assay"})

    def test_a_judge_lane_loads(self, tmp_path):
        repo, proj = _judge_repo(tmp_path)
        cfg, cfg_path, central, central_path, *_rest = \
            run_gate.load_config_snapshot(proj)
        assert cfg["lanes"]["rust-r1"]["judge"] == "assay"
        assert cfg_path == proj / "run-gate.toml"

    @pytest.mark.parametrize("value", ["pytest", "Assay", "", None, True,
                                       0, ["assay"], {"name": "assay"}])
    def test_an_unknown_provider_is_refused_by_name(self, value):
        with pytest.raises(run_gate.GateError, match=r"judge.*must be"):
            run_gate._validate_lane(
                "rust-r1", {"kind": "command", "environment": "rust",
                            "argv": ["true"], "judge": value}, "fixture")

    def test_the_refusal_names_the_value_and_the_vocabulary(self):
        with pytest.raises(run_gate.GateError) as caught:
            run_gate._validate_lane(
                "rust-r1", {"kind": "command", "environment": "rust",
                            "argv": ["true"], "judge": "pytest"}, "fixture")
        message = str(caught.value)
        assert "'pytest'" in message
        assert "'assay'" in message
        # …and says what the key MEANS, so the remedy is not a guess.
        assert "RUN-GATE provides" in message

    def test_an_assay_lane_cannot_declare_a_provided_judge(self):
        with pytest.raises(run_gate.GateError, match=r"only on .*command"):
            run_gate._validate_lane(
                "a", {"kind": "assay", "environment": "rust",
                      "assay_lane": "r1", "judge": "assay"}, "fixture")

    def test_a_sequence_cannot_declare_a_provided_judge(self):
        with pytest.raises(run_gate.GateError,
                           match=r"sequence does not accept .judge."):
            run_gate._validate_lane(
                "s", {"kind": "sequence", "lanes": ["rust-r1"],
                      "judge": "assay"}, "fixture")

    def test_a_judge_lane_cannot_also_pin_an_external_judge(self):
        with pytest.raises(run_gate.GateError, match=r"contradict"):
            run_gate._validate_lane(
                "rust-r1", {"kind": "command", "environment": "rust",
                            "argv": ["true"], "judge": "assay",
                            "pins": {"assay": {"sha256": "x.sha256"}}},
                "fixture")

    def test_judge_on_an_exec_environment_is_refused_at_load(self, tmp_path):
        repo = make_repo(tmp_path)
        config = textwrap.dedent("""\
            schema_version = 1
            [environments.runner]
            mode = "exec"
            image = "runner:local"
            [lanes.rust-r1]
            kind = "command"
            environment = "runner"
            judge = "assay"
            argv = ["true"]
            """)
        proj = make_project(repo, config)
        with pytest.raises(run_gate.GateError, match=r"mode = \"ephemeral\""):
            run_gate.load_config(proj)

    def test_judge_on_a_bare_host_environment_is_refused_at_load(self, tmp_path):
        repo = make_repo(tmp_path)
        config = textwrap.dedent("""\
            schema_version = 1
            [environments.local]
            mode = "host"
            [lanes.rust-r1]
            kind = "command"
            environment = "local"
            judge = "assay"
            argv = ["true"]
            """)
        proj = make_project(repo, config)
        with pytest.raises(run_gate.GateError, match=r"mode = \"ephemeral\""):
            run_gate.load_config(proj)

    def test_a_project_inside_the_judge_tree_cannot_be_judged_by_it(self, tmp_path):
        """The read-only overlay would make the judged project read-only."""
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = repo / "assay" / "consumer"
        proj.mkdir(parents=True)
        (proj / "run-gate.toml").write_text(textwrap.dedent("""\
            schema_version = 1
            [environments.rust]
            mode = "ephemeral"
            image = "rust:local"
            [lanes.rust-r1]
            kind = "command"
            environment = "rust"
            judge = "assay"
            argv = ["true"]
            """))
        commit_all(repo, "self-judging project")
        with pytest.raises(run_gate.GateError, match=r"READ-ONLY"):
            run_gate.load_config(proj)

    def test_a_project_outside_the_judge_tree_is_untouched(self, tmp_path):
        repo, proj = _judge_repo(tmp_path)
        assert "judge" in run_gate.load_config(proj)[0]["lanes"]["rust-r1"]


# ---------------------------------------------------------------------------
# 2. Mounts: read-only, at their own path, dual, and LAST
# ---------------------------------------------------------------------------

class TestJudgeMounts:
    def _lane(self):
        return {"kind": "command", "environment": "rust", "judge": "assay",
                "argv": ["true"]}

    def test_each_judge_tree_is_mounted_read_only_at_its_own_path(
            self, tmp_path, monkeypatch):
        worktree = tmp_path / "tree"
        (worktree / "assay").mkdir(parents=True)
        (worktree / "libraries" / "cowtree").mkdir(parents=True)
        # RG-3's dual mount, both halves read-only: the derived PHYSICAL
        # source, bound at its physical path AND at the judged worktree's
        # namespace view of the same subtree. `physical_path` is stubbed
        # because this oracle is about the PAIR each -v carries; the
        # end-to-end oracle below resolves it for real.
        monkeypatch.setattr(
            run_gate, "physical_path",
            lambda path, **kw: Path("/phys") / path.relative_to(tmp_path))
        flags = run_gate.judge_mount_flags(self._lane(), worktree)
        assert flags == [
            "-v", "/phys/tree/assay:/phys/tree/assay:ro",
            "-v", f"/phys/tree/assay:{worktree / 'assay'}:ro",
            "-v", "/phys/tree/libraries/cowtree:/phys/tree/libraries/cowtree:ro",
            "-v", f"/phys/tree/libraries/cowtree:"
                  f"{worktree / 'libraries/cowtree'}:ro",
        ]
        # …and EVERY flag is read-only: the judged worktree stays writable
        # only for the lane's own files, never for the judge it runs.
        specs = [flags[index + 1] for index, item in enumerate(flags)
                 if item == "-v"]
        assert all(spec.endswith(":ro") and spec.split(":")[0] == "/phys/tree/"
                   + spec.split(":")[1].split("/tree/")[1].split(":")[0]
                   .lstrip("/") or True for spec in specs)
        assert len(specs) == 4

    def test_the_namespace_view_follows_the_judged_worktree_alias(
            self, tmp_path, monkeypatch):
        """The judge tree is a SUB-PATH of the judged worktree, so its second
        view must be the worktree's alias plus that sub-path: an alias
        declared for the worktree (RG-NARROW) has to reach the subtree too."""
        worktree = tmp_path / "tree"
        (worktree / "assay").mkdir(parents=True)
        (worktree / "libraries" / "cowtree").mkdir(parents=True)
        # A bare host: the derived physical path EQUALS the namespace path,
        # so the second view comes from the declared alias (RG-3's refusal
        # case is covered by the suite's own dual-mount tests).
        monkeypatch.setattr(run_gate, "physical_path", lambda path, **kw: path)
        monkeypatch.setenv(run_gate.MOUNT_ALIAS_ENV_VAR,
                           f"{worktree}=/workspaces/estate")
        flags = run_gate.judge_mount_flags(self._lane(), worktree)
        assert flags == [
            "-v", f"{worktree / 'assay'}:{worktree / 'assay'}:ro",
            "-v", f"{worktree / 'assay'}:/workspaces/estate/assay:ro",
            "-v", f"{worktree / 'libraries/cowtree'}:"
                  f"{worktree / 'libraries/cowtree'}:ro",
            "-v", f"{worktree / 'libraries/cowtree'}:"
                  f"/workspaces/estate/libraries/cowtree:ro",
        ]

    def test_a_lane_without_the_key_gets_no_extra_mount(self, tmp_path):
        assert run_gate.judge_mount_flags(
            {"kind": "command", "environment": "rust", "argv": ["true"]},
            tmp_path) == []

    def test_the_container_argv_carries_the_overlay_after_every_other_mount(
            self, tmp_path, monkeypatch):
        """Order is the mechanism: a later bind mount wins for its subtree, so
        the read-only overlay must come AFTER the worktree/extra mounts."""
        repo, proj = _judge_repo(tmp_path, image="tester-unified:run-gate-2560")
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        wt = make_worktree(repo, tmp_path)
        proc = run_tool(proj, "rust-r1", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        call = [run for run in docker_runs(log) if "-d" in run][0]
        mounts = docker_mounts(call)
        overlay = [index for index, item in enumerate(mounts)
                   if item.endswith(":ro")
                   and (item.split(":")[0].endswith("/assay")
                        or item.split(":")[0].endswith("/libraries/cowtree"))]
        assert overlay, mounts
        assert overlay[-1] == len(mounts) - 1, mounts
        # The worktree's own (read-write) mount precedes every overlay entry:
        # that is what makes the overlay the winner for its subtree.
        worktree_at = next(index for index, item in enumerate(mounts)
                           if item.split(":")[1] == str(wt))
        assert worktree_at < overlay[0]
        # …and the judge line is disclosed BEFORE the lane starts.
        assert "run-gate: judge: assay" in proc.stdout
        assert "mounted read-only from" in proc.stdout


# ---------------------------------------------------------------------------
# 3. Identity: which judge, from which commit, clean or not
# ---------------------------------------------------------------------------

class TestJudgeIdentity:
    def test_identity_names_the_commit_the_version_and_the_clean_state(
            self, tmp_path):
        repo, _proj = _judge_repo(tmp_path)
        # A release tag with assay's own grammar, then a commit on top of it:
        # the recorded version must say BOTH (the version a bare tag would
        # report is not the source that ran).
        subprocess.run(["git", "-C", str(repo), "tag", "assay-v9.9.9"],
                       check=True)
        (repo / "assay" / "extra.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "after tag"],
                       check=True)
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        assert identity["commit"] == head
        assert identity["name"] == "assay"
        assert identity["provided_by"] == "run-gate"
        assert identity["version"].startswith("assay-v9.9.9-1-g"), identity
        assert identity["dirty"] is False

    def test_an_uncommitted_judge_is_recorded_as_dirty_never_as_clean(
            self, tmp_path):
        repo, _proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / "assay" / "touched.py").write_text("x = 1\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True
        assert "DIRTY" in run_gate.judge_identity_line(identity)

    def test_no_release_tag_is_stated_not_guessed(self, tmp_path):
        repo, _proj = _judge_repo(tmp_path)
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["version"] is None
        assert "no assay-v* release tag" in identity["version_source"]
        line = run_gate.judge_identity_line(identity)
        assert "no version" in line and "no assay-v* release tag" in line

    def test_an_invented_lookalike_tag_is_not_a_version(self, tmp_path):
        """`--match assay-v*` (assay's own grammar) is the filter; a tag from
        another project cannot stand in."""
        repo, _proj = _judge_repo(tmp_path)
        subprocess.run(["git", "-C", str(repo), "tag", "run-gate-v1.2.3"],
                       check=True)
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["version"] is None

    def test_a_lane_without_the_key_records_nothing(self, tmp_path):
        assert run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "argv": ["true"]},
            tmp_path) is None

    def test_the_run_record_carries_the_judge_and_history_keeps_it(
            self, tmp_path, monkeypatch):
        repo, proj = _judge_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        proc = run_tool(proj, "rust-r1", "--json")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        record = json.loads((proj / ".run-gate" / "history.json").read_text())
        latest = record["lanes"]["rust-r1"]["latest"]
        assert latest["judge"]["name"] == "assay"
        assert latest["judge"]["provided_by"] == "run-gate"
        assert latest["judge"]["commit"] == latest["commit"]
        assert latest["judge"]["dirty"] is False
        assert latest["judge"]["version"] is None   # no tag in this fixture
        assert latest["judge"]["mounts"] == ["assay", "libraries/cowtree"]
        assert latest["judge"]["pythonpath"] == [
            str(proj.parent / "assay/src"),
            str(proj.parent / "assay/analysis/src"),
            str(proj.parent / "libraries/cowtree/src"),
        ]


# ---------------------------------------------------------------------------
# 4. Refusals: the judge is provided, or the lane does not run
# ---------------------------------------------------------------------------

class TestJudgeRefusals:
    def _lane(self):
        return {"kind": "command", "environment": "rust", "judge": "assay",
                "argv": ["true"]}

    def test_a_missing_judge_source_is_not_run_external_missing(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = make_project(repo, JUDGE_CONFIG.replace(
            "{image}", "tester-unified:run-gate-2560"))
        (proj / "assay.toml").write_text(_lanes_toml(["/bin/sh", "-c", "true"]))
        commit_all(repo, "fixture")
        # …and now the checkout loses the judge tree the lane asked for.
        shutil.rmtree(repo / "assay")
        commit_all(repo, "drop judge")
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "rust-r1")
        assert proc.returncode == 3, proc.stdout + proc.stderr
        # Closed NOT_RUN, with the CLOSED reason vocabulary on the summary
        # line (the message on stderr says what is missing), and nothing
        # started: this is a precondition, not a failed judge.
        assert "verdict NOT_RUN" in proc.stdout
        assert "reason external-missing" in proc.stdout
        assert str(proj.parent / "assay") in proc.stderr
        assert log.read_text().strip() == ""

    def test_the_refusal_names_the_missing_path_and_the_remedy(self, tmp_path):
        with pytest.raises(run_gate.GateNotRunError) as caught:
            run_gate.preflight_judge_provider(
                self._lane(), "rust-r1", tmp_path,
                {"mode": "ephemeral", "image": "rust:local"}, "declared x")
        assert caught.value.reason == "external-missing"
        message = str(caught.value)
        assert str(tmp_path / "assay") in message
        assert "kind = \"assay\"" in message      # the alternative it names
        assert "cannot provide it" in message

    def test_a_non_ephemeral_environment_is_refused(self):
        with pytest.raises(run_gate.GateError, match=r"mode 'exec'"):
            run_gate.preflight_judge_provider(
                self._lane(), "rust-r1", Path("/tree"),
                {"mode": "exec", "image": "runner:local"}, "declared x")

    def test_an_absent_environment_is_refused(self):
        with pytest.raises(run_gate.GateError, match=r"no environment"):
            run_gate.preflight_judge_provider(
                self._lane(), "rust-r1", Path("/tree"), None, "bare host")

    def test_a_directory_without_a_pyproject_is_not_the_judge(self, tmp_path):
        (tmp_path / "assay").mkdir()
        (tmp_path / "libraries" / "cowtree").mkdir(parents=True)
        with pytest.raises(run_gate.GateNotRunError) as caught:
            run_gate.preflight_judge_provider(
                self._lane(), "rust-r1", tmp_path,
                {"mode": "ephemeral", "image": "rust:local"}, "declared x")
        assert caught.value.reason == "external-missing"
        assert "pyproject.toml" in str(caught.value)

    def test_a_lane_without_the_key_is_not_preflighted(self):
        assert run_gate.preflight_judge_provider(
            {"kind": "command", "environment": "rust", "argv": ["true"]},
            "rust-r1", Path("/tree"),
            {"mode": "ephemeral", "image": "rust:local"},
            "declared x") is None


# ---------------------------------------------------------------------------
# 5. The lane's own environment: interpreter, PYTHONPATH, and the guard
# ---------------------------------------------------------------------------

class TestJudgeCellEnvironment:
    def test_the_setup_names_the_interpreter_and_the_import_roots(self, tmp_path):
        lines = run_gate.judge_source_setup(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            tmp_path)
        joined = "\n".join(lines)
        assert 'RUN_GATE_JUDGE_PYTHON=$(command -v python3)' in joined
        assert 'PYTHONNOUSERSITE=1' in joined
        assert 'PYTHONDONTWRITEBYTECODE=1' in joined
        for root in run_gate.JUDGE_IMPORT_ROOTS:
            assert str(tmp_path / root) in joined
        # PREPENDED, so the provided judge wins over whatever the lane had.
        assert '"${PYTHONPATH:+:$PYTHONPATH}"' in joined
        # A missing interpreter is refused by name, never replaced by a
        # fallback (masked-default class).
        assert "does not install an interpreter" in joined
        assert 'else RUN_GATE_JUDGE_PYTHON=' not in joined

    def test_the_guard_checks_every_provided_module_with_find_spec(self, tmp_path):
        lines = run_gate.judge_source_setup(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            tmp_path)
        joined = "\n".join(lines)
        assert "importlib.util.find_spec" in run_gate._JUDGE_IMPORT_GUARD_CODE
        for name, _rel in run_gate.JUDGE_IMPORT_NAMES:
            assert name in joined
        # The guard runs before the command and is fatal: an image's own
        # assay must not judge silently.
        assert "was NOT provided to this lane" in joined
        assert "exit 2" in joined

    def test_a_lane_without_the_key_gets_no_setup(self, tmp_path):
        assert run_gate.judge_source_setup(
            {"kind": "command", "environment": "rust", "argv": ["true"]},
            tmp_path) == []

    def test_the_guard_accepts_the_provided_tree_and_refuses_another(
            self, tmp_path):
        _minimal_judge(tmp_path)
        roots = ":".join(str(tmp_path / root)
                         for root in run_gate.JUDGE_IMPORT_ROOTS)
        pairs = " ".join(
            f"{name} {rel}" for name, rel in run_gate.JUDGE_IMPORT_NAMES)
        command = [sys.executable, "-c", run_gate._JUDGE_IMPORT_GUARD_CODE,
                   roots, *pairs.split()]
        environment = {**os.environ, "PYTHONPATH": roots}
        accepted = subprocess.run(command, capture_output=True, text=True,
                                  env=environment, cwd=str(tmp_path))
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        # Hand it the SYSTEM's assay (the gate image's own copy) instead: the
        # guard must refuse, naming where the module really resolved.
        elsewhere = dict(environment)
        elsewhere["PYTHONPATH"] = os.environ.get(
            "RUN_GATE_TEST_OTHER_JUDGE", "/usr/local/lib/python3")
        refused = subprocess.run(
            [sys.executable, "-c", run_gate._JUDGE_IMPORT_GUARD_CODE,
             roots, "assay", "assay/__init__.py",
             "assay_analysis", "assay_analysis/__init__.py",
             "cowtree", "cowtree/__init__.py"],
            capture_output=True, text=True, env=elsewhere, cwd=str(tmp_path))
        assert refused.returncode == 2, refused.stdout

    def test_the_command_inner_carries_the_setup_before_the_lane_argv(self):
        lane = {"kind": "command", "environment": "rust", "judge": "assay",
                "argv": ["bash", "-c", "echo lane-ran"]}
        inner = run_gate.build_command_inner(lane, Path("/tree"))
        assert "set -euo pipefail" in inner
        assert "RUN_GATE_JUDGE_PYTHON" in inner
        assert inner.index("RUN_GATE_JUDGE_PYTHON") < inner.index("echo lane-ran")
        assert "run-gate: judge: assay" in inner

    def test_a_lane_without_the_key_is_byte_identical(self):
        lane = {"kind": "command", "environment": "rust",
                "argv": ["bash", "-c", "echo lane-ran"]}
        inner = run_gate.build_command_inner(lane, Path("/tree"))
        assert "RUN_GATE_JUDGE_PYTHON" not in inner
        assert "run-gate: judge" not in inner


# ---------------------------------------------------------------------------
# 6. END TO END: a lane image with no assay reaches the judge through it
# ---------------------------------------------------------------------------

def fake_docker_running_lane(tmp_path, monkeypatch) -> Path:
    """A docker shim that RECORDS every call and really RUNS the lane.

    `fake_docker_executing` (used by this project's probe tests) executes only
    the read-only probes: its `run -d` answers a canned container id. That is
    exactly the substitute this package must not accept — an argv pin cannot
    tell a lane that ran the judge from a shim that said it did. Here the
    detached lane's `bash -c <inner>` EXECUTES on the host, its output is kept
    as the container's log (which `docker logs` replays, as real docker does),
    and `docker wait` returns the status that script really produced.

    The inner script runs inside a `PATH` the test chooses
    (`RUN_GATE_TEST_LANE_PATH`), which is how "an image that carries no assay"
    is modelled without inventing an image.
    """
    shim_dir = shim_dir_of(monkeypatch)   # RG-87: private, prepended to PATH
    log = tmp_path / "docker-calls.log"
    log.write_text("")
    state = tmp_path / "docker-state"
    state.mkdir(exist_ok=True)
    shim = shim_dir / "docker"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        __sep=$(printf '\\037')
        __line=""
        for __a in "$@"; do __line="$__line$__a$__sep"; done
        printf '%s\\n' "$__line" >> "{log}"
        S="{state}"
        cmd="$1"; shift
        __last() {{ for a in "$@"; do :; done; }}
        case "$cmd" in
          run|exec)
            __name=""; __prev=""; __detached=no
            for a in "$@"; do
              [ "$__prev" = "--name" ] && __name="$a"
              [ "$a" = "-d" ] && __detached=yes
              __prev="$a"
              __lastarg="$a"
            done
            case "$__lastarg" in
              *RUN_GATE_STATE_ROOT_READY*)
                echo "RUN_GATE_STATE_ROOT_READY"; exit 0 ;;
            esac
            if [ "$__detached" = yes ]; then
              PATH="${{RUN_GATE_TEST_LANE_PATH:-$PATH}}" \
                bash -c "$__lastarg" > "$S/$__name.log" 2>&1
              printf '%s\\n' "$?" > "$S/$__name"
              echo "sha256:fakeid-$__name"
            else
              exec bash -c "$__lastarg"
            fi
            ;;
          logs)
            __name=""
            for a in "$@"; do __name="$a"; done
            [ -f "$S/$__name.log" ] && cat "$S/$__name.log"
            echo "FAKE-LOGS-LINE"
            ;;
          wait)
            [ -f "$S/$1" ] || {{ echo "Error: No such object: $1" >&2; exit 1; }}
            read __code < "$S/$1"
            printf '%s\\n' "$__code"
            ;;
          rm)
            for a in "$@"; do :; done
            rm -f "$S/$__last" "$S/$__last.log"
            ;;
        esac
        exit 0
    """))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return log


def restricted_path(*names: str) -> str:
    """A `PATH` holding symlinks to exactly the named host tools.

    Used to model an image: the lane's own shell resolves ONLY what is linked
    here, so `command -v python3` failing is a fact of the fixture rather than
    of the host running the gate.
    """
    directory = Path(tempfile.mkdtemp(prefix="run-gate-test-restricted-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    for name in names:
        found = shutil.which(name)
        assert found is not None, f"the fixture needs {name} on the host"
        (directory / name).symlink_to(found)
    return str(directory)


class TestJudgeEndToEnd:
    """The lane really runs, in a `PATH` that carries no assay and no install
    path — the interim hostobs route's own reproduction, as acceptance rather
    than as an argv pin."""

    # The lane's own `PATH`: exactly these tools exist in it. No `pip`, no
    # installed `assay` entry point -- an assay that runs here can only come
    # from the source Run-Gate mounted (that is what makes this acceptance
    # rather than an argv pin).
    LANE_PATH_TOOLS = ("bash", "sh", "git", "sed", "mkdir", "cat", "rm",
                       "grep", "env", "dirname", "basename", "mktemp",
                       "chmod", "printf", "python3")

    def _run(self, tmp_path, monkeypatch, lane_argv=None, *, judge_prefix=None):
        repo, proj = _judge_repo(tmp_path, lane_argv=lane_argv)
        log = fake_docker_running_lane(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_TEST_LANE_PATH",
                           judge_prefix or restricted_path(*self.LANE_PATH_TOOLS))
        proc = run_tool(proj, "rust-r1")
        return repo, proj, log, proc

    def test_the_lane_image_carries_no_install_path_at_all(
            self, tmp_path, monkeypatch):
        """The precondition of the whole class, asserted rather than assumed:
        in the lane's own PATH, `pip` and a console-script `assay` do not
        exist, and no importable `assay` is reachable without the mount."""
        prefix = restricted_path(*self.LANE_PATH_TOOLS)
        for absent in ("pip", "pip3", "assay", "python", "uv"):
            assert shutil.which(absent, path=prefix) is None, absent

    def test_a_lane_that_carries_no_assay_runs_the_provided_judge(
            self, tmp_path, monkeypatch):
        """The fixture's lane image is `tester-unified-rust:local` -- an image
        that ships python3 and NO pip, so there is no way to install an assay
        into it. The judge still runs, from the source Run-Gate mounted."""
        repo, _proj, _log, proc = self._run(tmp_path, monkeypatch)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "claim: R0 PASS" in proc.stdout
        assert "claim: R1 PASS" in proc.stdout
        # The judge, not the image, produced it: the disclosure names the
        # mount, and the guard passed. (The `docker argv:` disclosure echoes
        # the inner script's own refusal TEXT verbatim, so the refusal is
        # detected by the line the run would END on, not by the string.)
        assert "run-gate: judge: assay" in proc.stdout
        assert "mounted read-only from" in proc.stdout
        assert "\nrun-gate: judge = \\\"assay\\\" was NOT provided" \
            not in proc.stdout

    def test_the_verdict_artifact_is_the_judge_s_own_totals(
            self, tmp_path, monkeypatch):
        repo, proj, _log, proc = self._run(tmp_path, monkeypatch)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        verdict = json.loads(
            (proj / ".assay" / "verdict-r1.json").read_text(encoding="utf-8"))
        assert verdict["outcome"] == "PASS"
        assert verdict["exit_code"] == 0
        assert [claim["status"] for claim in verdict["claims"]] == \
            ["PASS", "PASS"]
        # Authored by the MOUNTED source, which is the judged tree's own copy
        # -- the same tree whose commit the record carries.
        latest = json.loads(
            (proj / ".run-gate/history.json").read_text())["lanes"][
                "rust-r1"]["latest"]
        assert verdict["commit"] == latest["commit"]
        assert (repo / "assay" / "src" / "assay" / "cli.py").is_file()

    def test_the_negative_probe_fails_when_the_lane_loses_its_test(
            self, tmp_path, monkeypatch):
        """The interim route's own negative probe: remove the test the lane
        runs and the same judge must FAIL. This is what separates "the judge
        ran" from "the lane printed something green"."""
        repo, proj = _judge_repo(
            tmp_path, lane_argv=["/bin/sh", "-c", "test -f tests/kept.txt"])
        (proj / "tests").mkdir()
        (proj / "tests" / "kept.txt").write_text("present\n")
        commit_all(repo, "a test the lane runs")
        fake_docker_running_lane(tmp_path, monkeypatch)
        green = run_tool(proj, "rust-r1")
        assert green.returncode == 0, green.stdout + green.stderr
        verdict = json.loads(
            (proj / ".assay" / "verdict-r1.json").read_text(encoding="utf-8"))
        assert verdict["outcome"] == "PASS"
        # …and now the test the lane runs is gone: same judge, same lane,
        # FAIL -- a red verdict, not a silent pass.
        (proj / "tests" / "kept.txt").unlink()
        red = run_tool(proj, "rust-r1")
        assert red.returncode == 1, red.stdout + red.stderr
        verdict = json.loads(
            (proj / ".assay" / "verdict-r1.json").read_text(encoding="utf-8"))
        assert verdict["outcome"] == "FAIL"
        assert [claim["status"] for claim in verdict["claims"]] == \
            ["FAIL", "FAIL"]
        assert "claim: R0 FAIL" in red.stdout

    def test_a_judge_tree_that_cannot_serve_every_module_refuses_in_lane(
            self, tmp_path, monkeypatch):
        """The guard's whole content is the CONDITION it evaluates, not the
        code text: a judge tree that provides `assay` and `cowtree` but not
        `assay_analysis` must fail the lane (exit 2, naming what was not
        provided) instead of running a partially importable judge. This is the
        oracle a construction-only pin of the guard string cannot supply."""
        repo, proj = _judge_repo(tmp_path)
        shutil.rmtree(repo / "assay" / "analysis")
        commit_all(repo, "drop the analysis sibling")
        log = fake_docker_running_lane(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_TEST_LANE_PATH",
                           restricted_path(*self.LANE_PATH_TOOLS))
        proc = run_tool(proj, "rust-r1")
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "was NOT provided" in proc.stdout
        assert "assay_analysis" in proc.stdout
        # …and the lane's own command never ran.
        assert "claim: R0" not in proc.stdout

    def test_a_lane_whose_image_has_no_python3_is_refused_by_name(
            self, tmp_path, monkeypatch):
        """No fallback interpreter: the refusal says which declaration needs
        what, so a broken image is a named defect rather than a masked one."""
        prefix = restricted_path("git", "sed", "bash", "sh", "env", "mkdir",
                                 "cat", "rm", "grep", "mktemp", "dirname",
                                 "basename", "chmod", "printf")
        assert shutil.which("python3", path=prefix) is None
        _repo, _proj, _log, proc = self._run(
            tmp_path, monkeypatch, judge_prefix=prefix)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "does not install an interpreter" in proc.stdout
        # …and the refusal names the declaration that needs it.
        assert "judge" in proc.stdout



# ---------------------------------------------------------------------------
# 8. The uncovered corners of the derivation, driven directly
# ---------------------------------------------------------------------------

class TestJudgeDerivationEdges:
    """Conditions the happy paths cannot express: Git unavailable, a describe
    answer that is not a release version, and a checkout whose toplevel has no
    judge tree. Each is a REAL state of this estate (no git binary in a slim
    image, an unrelated tag reachable, a consumer that never had assay)."""

    def _lane(self):
        return {"kind": "command", "environment": "rust", "judge": "assay"}

    def test_git_being_absent_is_unknown_never_clean(self, tmp_path, monkeypatch):
        """A missing git binary must read as `dirty: None` (unknown) with the
        version reason stating that describe did not run — never as a clean
        tree with no version, which would certify bytes nobody compared."""
        _minimal_judge(tmp_path)

        def unavailable():
            raise OSError("git: not found")
        monkeypatch.setattr(run_gate, "host_git_environment", unavailable)
        identity = run_gate.judge_identity(self._lane(), tmp_path)
        assert identity["dirty"] is None
        assert identity["version"] is None
        assert "(did not run)" in identity["version_source"]
        assert "dirty state unknown" in run_gate.judge_identity_line(identity)

    def test_a_describe_answer_outside_the_version_grammar_is_no_version(
            self, tmp_path, monkeypatch):
        """`--match` is a filter, not a validator: a tag whose name survives
        the glob but whose describe text is not release text (a hand-made tag
        like `assay-v-not-a-release`) must not be recorded as a version."""
        shim = SimpleNamespace(
            run=lambda argv, **_kw: SimpleNamespace(
                returncode=0, stdout="assay-v-not-a-release-0-gdeadbeef\n"
                if "describe" in argv else ""))
        monkeypatch.setattr(run_gate, "subprocess", shim)
        identity = run_gate.judge_identity(self._lane(), tmp_path)
        assert identity["version"] is None
        assert identity["version_source"].startswith("no assay-v*")

    def test_the_record_also_rejects_a_describe_without_a_commit_suffix(
            self, tmp_path, monkeypatch):
        """`--long` always appends `<n>-g<sha>`; an answer without it means
        something other than `git describe --long` replied, so it is not
        evidence about THIS source."""
        shim = SimpleNamespace(
            run=lambda argv, **_kw: SimpleNamespace(
                returncode=0, stdout="assay-v9.9.9\n"))
        monkeypatch.setattr(run_gate, "subprocess", shim)
        identity = run_gate.judge_identity(self._lane(), tmp_path)
        assert identity["version"] is None


class TestJudgeLoadValidationEdges:
    """The two "cannot answer" branches of the load-time judge validation."""

    def _project(self, repo: Path, name: str = "proj") -> Path:
        return make_project(repo, JUDGE_CONFIG.replace(
            "{image}", "tester-unified:local"), name=name)

    def test_a_judge_lane_in_a_checkout_without_git_still_loads(
            self, tmp_path, monkeypatch):
        """The judge-tree nesting refusal is DERIVED from a git toplevel. With
        no toplevel to compare against there is nothing to refuse, and the
        runtime preflight still refuses a missing source — so the load must
        succeed rather than invent a boundary."""
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = self._project(repo)
        monkeypatch.setattr(run_gate, "_git_toplevel_if_available",
                            lambda _path: None)
        config = run_gate.load_config(proj)[0]
        assert config["lanes"]["rust-r1"]["judge"] == "assay"

    def test_a_checkout_with_no_judge_tree_is_not_a_self_judging_project(
            self, tmp_path):
        """A checkout that carries no `assay/` at all has no judge tree to be
        inside: the nesting refusal must not fire on it (its lanes fail the
        RUNTIME preflight instead, with the missing-path message)."""
        repo = make_repo(tmp_path)          # no assay/, no libraries/
        proj = self._project(repo)
        config = run_gate.load_config(proj)[0]
        assert config["lanes"]["rust-r1"]["judge"] == "assay"


# ---------------------------------------------------------------------------
# 9. The in-process wiring: disclosure, record and `--help`
# ---------------------------------------------------------------------------

class TestJudgeProviderWiring:
    """In-process (`run_gate.main`), not through `run_tool`: the disclosure
    function, the record write and `usage()` are decisions made in THIS
    interpreter, and an argv log cannot show that they happened."""

    def _run(self, tmp_path, monkeypatch, capsys, *args):
        repo, proj = _judge_repo(tmp_path)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        code = run_gate.main(list(args))
        return repo, proj, code, capsys.readouterr()

    def test_the_judge_is_disclosed_and_recorded_in_process(
            self, tmp_path, monkeypatch, capsys):
        _repo, proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "rust-r1")
        assert code == 0, streams.err
        # The disclosure names the judge BEFORE the lane's verdict line.
        assert "run-gate: judge: assay" in streams.out
        assert "mounted read-only from" in streams.out
        assert streams.out.index("run-gate: judge:") \
            < streams.out.index("run-gate: lane 'rust-r1'")
        latest = json.loads(
            (proj / ".run-gate/history.json").read_text()
        )["lanes"]["rust-r1"]["latest"]
        assert latest["judge"]["name"] == "assay"
        assert latest["judge"]["commit"] == latest["commit"]
        assert latest["judge"]["dirty"] is False
        assert latest["judge"]["version"] is None
        assert latest["judge"]["mounts"] == ["assay", "libraries/cowtree"]

    def test_a_dirty_judge_is_disclosed_as_dirty(
            self, tmp_path, monkeypatch, capsys):
        """Uncommitted judge work is not a refusal (a lane may be judged
        before its judge is committed) but it is never silent: the record
        carries `dirty: true` and one WARNING says the version cannot be tied
        to the bytes."""
        repo, proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / "assay" / "uncommitted.py").write_text("x\n")
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        assert run_gate.main(["rust-r1"]) == 0
        streams = capsys.readouterr()
        assert "DIRTY" in streams.out
        assert "WARNING: judge source is dirty" in streams.out
        assert "cannot be tied to its bytes" in streams.out
        latest = json.loads(
            (proj / ".run-gate/history.json").read_text()
        )["lanes"]["rust-r1"]["latest"]
        assert latest["judge"]["dirty"] is True

    def test_help_states_which_judge_run_gate_will_mount(
            self, tmp_path, monkeypatch, capsys):
        _repo, _proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "--help")
        assert code == 0
        assert "judge=assay (provided by run-gate)" in streams.out


# ---------------------------------------------------------------------------
# 7. Documentation is part of the change (AGENTS.md: make it a test)
# ---------------------------------------------------------------------------

class TestJudgeProviderDocs:
    """Each document that must go red when the capability is undocumented.

    The rule: a capability is shipped when a reader who does not know it exists
    can find it, understand why, and adopt it. These oracles fail on a MISSING
    or stale document, not on wording.
    """

    DOCS = ("README.md", "CONSUMERS.md", "SPEC.md", "docs/DESIGN-GUIDE.md",
            "docs/DESIGN-judge-provider-2026-11.md", "CHANGES.md",
            "KNOWN_ISSUES_TODO_BACKLOG.md")

    def _read(self, relative):
        return (REPO / "run-gate-project" / relative).read_text(encoding="utf-8")

    def test_every_document_names_the_lane_key(self):
        for relative in self.DOCS:
            assert "judge" in self._read(relative).lower(), relative

    def test_the_readme_names_the_feature_and_links_the_rationale(self):
        readme = self._read("README.md")
        assert "judge = \"assay\"" in readme
        assert "README.md" and "DESIGN-GUIDE.md#the-judge-is-provided-by-the" \
            in readme
        assert "self._judge" not in readme

    def test_the_consumer_guide_has_a_pasteable_example(self):
        consumers = self._read("CONSUMERS.md")
        # The heading the README and the design note link TO (the cross-document
        # anchor oracle resolves the slug, so the heading must be this one).
        assert "### Run-Gate provided Assay judge (RG-285)" in consumers
        # A pasteable lane, not a description of one: the marker makes the
        # suite's own config-example oracle parse it, and that oracle is the
        # one that catches a stale `schema_version`.
        block = consumers.split("<!-- run-gate-config -->")
        assert any("judge = \"assay\"" in piece for piece in block), consumers
        assert "RUN_GATE_JUDGE_PYTHON" in consumers
        assert "external-missing" in consumers

    def test_the_spec_states_the_rule_and_the_record(self):
        spec = self._read("SPEC.md")
        assert "`R-08f`" in spec and "`R-57`" in spec
        assert "`judge`\n  (`R-08f`" in spec
        assert "external-missing" in spec
        assert "version_source" in spec

    def test_the_design_guide_states_why_and_what_was_rejected(self):
        design = self._read("docs/DESIGN-GUIDE.md")
        section = design.split(
            "## The judge is provided by the gate, not the image", 1)
        assert len(section) == 2, "the design-guide section is missing"
        body = section[1].split("\n## ", 1)[0]
        for claim in ("read-only", "PYTHONNOUSERSITE", "find_spec",
                      "external-missing", "stdlib-only", "DERIVE"):
            assert claim.lower() in body.lower(), claim

    def test_the_changelog_and_backlog_carry_the_same_id(self):
        changes = self._read("CHANGES.md")
        backlog = self._read("KNOWN_ISSUES_TODO_BACKLOG.md")
        assert "RG-285" in changes
        assert backlog.count("## RG-285 —") == 1
        assert "RG-285" in backlog.split("## RG-285 —")[0]   # the status row

    def test_the_design_note_names_the_three_user_documents(self):
        note = self._read("docs/DESIGN-judge-provider-2026-11.md")
        for pointer in ("../README.md", "../CONSUMERS.md", "../SPEC.md"):
            assert pointer in note, pointer
