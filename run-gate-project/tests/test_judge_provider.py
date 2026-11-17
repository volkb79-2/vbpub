"""RG-285: Run-Gate provides the assay judge to a lane (design + oracles).

The feature: a lane declares `judge = "assay"` and Run-Gate mounts the judged
worktree's assay source READ-ONLY, exports the interpreter and import roots the
judge needs, proves the lane's interpreter resolves THAT copy (not an image's
own), and records the judge's commit/version in the run record beside the
verdict.

Every oracle here is behavioral, not a construction pin where it can be:

* the declaration oracles drive the REAL loader (`load_config`/`_validate_lane`);
* the mount oracles feed a real lane run through the fake docker argv, and a
  refusal the daemon would otherwise answer with its own message;
* the preflight oracles drive `preflight_judge_provider` with real directories;
* the end-to-end oracle runs a lane in an image that carries NO assay and NO
  install path -- the fixture's fake docker EXECUTES the assembled inner script
  on the host, so the judge is genuinely reached through the mounted source
  (a shim that echoed "passed" would pin nothing);
* the two negative probes are the ones the interim hostobs route was proven
  with: the judge's own totals are read from the verdict artifact, and a judge
  whose lane loses a test FAILS.

WHAT THE FIXTURE'S JUDGE IS, AND IS NOT (round-1 review). `_minimal_judge`
writes a small `assay` package and a `assay.cli` runner into the fixture
repository. It is NOT Assay: it reads the lane file, runs the lane's command and
writes a verdict-shaped artifact. What that PROVES is the mechanism this
package actually adds -- the mount destination/PYTHONPATH wiring, the
`find_spec` origin guard, the reserved exit code and its verdict mapping, and
that the lane image's own `PATH` contains no way to reach a judge. What it does
NOT prove, and no oracle here claims: that the REAL Assay source imports and
runs under a REAL image, or that Docker's own read-only enforcement holds
(`fake_docker_running_lane` runs the inner script on the host, where `:ro` is an
argv string, not a kernel mount). Those two are filed as RG-286 and must be
proven by a real-image smoke before `hostobs` migrates.

Design: docs/DESIGN-judge-provider-2026-11.md.
"""

from __future__ import annotations

import atexit
import contextlib
import copy
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
                           lane_runs, make_project, make_repo, make_worktree,
                           run_gate, run_tool, shim_dir_of)

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
    """Write a small, source-only, importable STAND-IN judge under `repo/assay`.

    It is a stand-in, not Assay: it reads a lane file, runs the lane's command
    and writes a verdict-shaped artifact with the closed outcome vocabulary.
    Its purpose is to make the MECHANISM observable -- `python -m assay.cli`
    can only resolve because `PYTHONPATH` carried the mounted tree, and the
    artifact it writes is what the negative probe turns red -- so no oracle
    here needs (or claims) the real judge's own semantics. What this cannot
    show is that REAL Assay imports and runs in a real image, or that Docker
    enforces the read-only mount (see the module docstring; filed as RG-286).
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


def _git(repo: Path, *args: str) -> str:
    """Run one fixture git command and return its stdout.

    Round-2 review, the `True/False` sweep: EVERY fixture git call goes
    through this one helper so `check=True`, `capture_output=True` and
    `text=True` are declared ONCE instead of repeated (and independently
    flippable) at 10-odd call sites. `check=True` is the meaningful one: a
    failed fixture command means the fixture did not build the state the
    oracle thinks it did, and it must fail loudly rather than let a later
    assertion explain it.
    """
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)
    return proc.stdout


def docker_mounts(call: list[str]) -> list[str]:
    return [call[index + 1] for index, item in enumerate(call)
            if item in ("-v", "--mount")]


class FlushRecorder:
    """A stream that records every `write` and `flush` IN ORDER.

    Round-2 review: `flush=True` on a disclosure line is a CONTRACT, not a
    habit -- run-gate's stdout is a pipe whenever a caller consumes it live,
    and a pipe is block buffered, so an unflushed line can reach the operator
    AFTER the container output it is supposed to precede. A test that only
    compared the text would stay green with the flush removed, which is
    exactly the `True -> False` mutant R2 found alive; this records the ORDER
    instead, so `print(..., flush=True)` (which writes its pieces and then
    flushes) satisfies `line_is_flushed` and a plain `print` cannot.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def write(self, text: str) -> int:
        self.events.append(("write", text))
        return len(text)

    def flush(self) -> None:
        self.events.append(("flush", ""))

    def text(self) -> str:
        return "".join(piece for kind, piece in self.events
                       if kind == "write")

    def line_is_flushed(self, needle: str) -> bool:
        """Whether the PRINT carrying `needle` ends on an immediate flush.

        A `print(..., flush=True)` emits `write(text)`, the newline `write`, and
        then `flush()` -- in that order, with separator writes in between when
        the call has several args. A plain `print` emits the same writes and
        NO flush, so the distinction is exactly "is the terminator followed by
        a flush", which no text comparison can see.
        """
        for index, (kind, piece) in enumerate(self.events):
            if kind != "write" or needle not in piece:
                continue
            cursor = index
            while cursor < len(self.events):
                cursor_kind, cursor_text = self.events[cursor]
                if cursor_kind != "write":
                    return False        # a flush BEFORE the terminator
                if "\n" in cursor_text:
                    # The line just ended here: the very next event decides.
                    return (cursor + 1 < len(self.events)
                            and self.events[cursor + 1] == ("flush", ""))
                cursor += 1
            return False
        return False


# ---------------------------------------------------------------------------
# 1. Declaration: one closed key, on a lane kind that can receive it
# ---------------------------------------------------------------------------

class TestJudgeFixtureContract:
    """Round-2 review, the `True/False` sweep over the FIXTURE helpers.

    A fixture argument that is never exercised by a mutant is not a fact the
    suite holds; these two are the ones worth keeping, so each is pinned by
    asserting the BEHAVIOUR it exists for rather than by leaving the keyword
    unexamined.
    """

    def test_git_helper_FAILS_LOUDLY_on_a_command_git_refuses(self, tmp_path):
        """`_git` carries `check=True`: a fixture command that fails must stop
        the test, because a silent failure leaves a half-built repository and
        a later assertion then explains a state the oracle never created."""
        repo = make_repo(tmp_path)
        with pytest.raises(subprocess.CalledProcessError):
            _git(repo, "rev-parse", "--verify", "no-such-ref")
        # …and the helper really does return stdout for the calls it serves.
        assert len(_git(repo, "rev-parse", "HEAD").strip()) == 40

    def test_minimal_judge_is_idempotent_on_a_repository_it_already_prepared(
            self, tmp_path):
        """`_minimal_judge`'s `parents`/`exist_ok` are what make it callable on
        a repository that already carries the judge trees (an oracle that adds
        the judge to an existing fixture), instead of raising FileExistsError
        on the directory it just confirmed."""
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        _minimal_judge(repo)
        assert (repo / "assay" / "pyproject.toml").is_file()


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
        with pytest.raises(run_gate.GateError,
                           match=r"requires an ephemeral container"):
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
        with pytest.raises(run_gate.GateError,
                           match=r"host mode, which starts no container"):
            run_gate.load_config(proj)

    # -- the resolution paths the load-time refusal used to MISS (review 1) --

    def test_an_undeclared_environment_name_is_refused_at_load(
            self, tmp_path, monkeypatch):
        """Before the fix a lane whose `environment` did not resolve to a dict
        at load slipped through to the RUNTIME preflight, so `run-gate lanes`
        and every load-only reader saw a lane that could never run."""
        repo = make_repo(tmp_path)
        proj = make_project(
            repo, JUDGE_CONFIG.replace('{image}', 'rust:local').replace(
                'environment = "rust"', 'environment = "typo"'))
        with pytest.raises(run_gate.GateError,
                           match=r"environment 'typo' is not defined"):
            run_gate.load_config(proj)

    def test_a_legacy_host_name_is_refused_at_load_with_its_remedy(
            self, tmp_path):
        """An undeclared legacy `host`/`bare-host` name is a real config
        state (RG-101's migration window). The loader answers with the resolver
        that names the migration, not with a generic 'not ephemeral'.

        `upgrade_modes=False` on purpose: the shared fixture helper would
        MATERIALIZE a `[environments.bare-host]` table, which is the state this
        test is not about."""
        repo = make_repo(tmp_path)
        proj = make_project(
            repo, JUDGE_CONFIG.replace('{image}', 'rust:local').replace(
                'environment = "rust"', 'environment = "bare-host"'),
            upgrade_modes=False)
        with pytest.raises(run_gate.GateError,
                           match=r"Legacy 'bare-host'"):
            run_gate.load_config(proj)

    def test_an_inherited_central_exec_environment_is_refused_at_load(
            self, tmp_path):
        """A lane declared in the project but resolving through the CENTRAL
        config's environments table -- the case the old load check could not
        see, because it only looked at this project's own table."""
        repo = make_repo(tmp_path)
        (repo / run_gate.ROOT_CONFIG_NAME).write_text(textwrap.dedent("""\
            schema_version = 1
            [environments.shared-runner]
            mode = "exec"
            image = "runner:local"
            """))
        proj = make_project(repo, JUDGE_CONFIG.replace(
            '{image}', 'rust:local').replace('environment = "rust"',
                                            'environment = "shared-runner"'))
        with pytest.raises(run_gate.GateError,
                           match=r"in central"):
            run_gate.load_config(proj)

    def test_an_inherited_central_ephemeral_environment_loads(self, tmp_path):
        """The same inheritance path must NOT refuse a healthy lane: the
        refusal is about the resolved mode, not about where it was declared."""
        repo = make_repo(tmp_path)
        (repo / run_gate.ROOT_CONFIG_NAME).write_text(textwrap.dedent("""\
            schema_version = 1
            [environments.shared-rust]
            mode = "ephemeral"
            image = "rust:local"
            """))
        proj = make_project(repo, JUDGE_CONFIG.replace(
            '{image}', 'rust:local').replace('environment = "rust"',
                                            'environment = "shared-rust"'))
        assert run_gate.load_config(proj)[0]["lanes"]["rust-r1"]["judge"] \
            == "assay"

    @pytest.mark.parametrize("tree", run_gate.JUDGE_SOURCE_TREES)
    def test_a_project_that_IS_a_judge_tree_cannot_be_judged_by_it(
            self, tmp_path, tree):
        """Both trees, at their EXACT paths, not only a subdirectory of one:
        the judged project would BE the read-only mount, so there is nothing
        left to write. (The earlier fixture only nested a project under
        `assay/`, which left the `libraries/cowtree` half of the loop
        unexercised and the equality branch unproven.)"""
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = repo / tree
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

    @pytest.mark.parametrize("tree", run_gate.JUDGE_SOURCE_TREES)
    def test_a_project_INSIDE_a_judge_tree_cannot_be_judged_by_it(
            self, tmp_path, tree):
        """…and one level down, in both trees (the nesting branch)."""
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = repo / tree / "consumer"
        # `parents` is redundant: `repo / tree` exists (the tree is what this
        # fixture creates), so only the last component is new.
        proj.mkdir()
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
# 2. Mounts: read-only, at their own path, dual, and never shadowed
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
        # …and EVERY flag is read-only, and binds the PHYSICAL source it claims:
        # the destination may be the physical path or the judged worktree's
        # namespace view of it, and in both cases the source is that same
        # derived physical path (an assertion that can FAIL, unlike the
        # `... or True` placeholder this test used to carry).
        specs = [flags[index + 1] for index, item in enumerate(flags)
                 if item == "-v"]
        assert len(specs) == 4
        for spec in specs:
            source, destination = spec.split(":")[:2]
            assert spec.endswith(":ro"), spec
            assert source == "/phys/tree/assay" \
                or source == "/phys/tree/libraries/cowtree", spec
            assert destination in (
                source, str(worktree / "assay"),
                str(worktree / "libraries" / "cowtree")), spec

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

    def test_the_container_mount_set_is_the_judge_overlay_plus_the_worktree(
            self, tmp_path, monkeypatch):
        """The lane really runs with the overlay in its argv AND with no
        competing mount on a judge tree -- and the judge is disclosed first.

        This replaces the earlier "the overlay comes last, so it wins" oracle.
        That claim was wrong about Docker (it orders binds by destination
        depth, and refuses a same-path pair as a duplicate), so ORDER proves
        nothing; what the gate must guarantee is that no OTHER mount lands
        at/under a judge tree, which the refusal oracles below drive directly.
        """
        repo, proj = _judge_repo(tmp_path, image="tester-unified:run-gate-2560")
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        wt = make_worktree(repo, tmp_path)
        proc = run_tool(proj, "rust-r1", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        call = [run for run in docker_runs(log) if "-d" in run][0]
        mounts = docker_mounts(call)
        overlay = [item for item in mounts if item.endswith(":ro")
                   and (item.split(":")[0].endswith("/assay")
                        or item.split(":")[0].endswith("/libraries/cowtree"))]
        assert overlay, mounts
        # The judged worktree itself is mounted read-write (the lane writes
        # evidence and may commit), and the judge trees are read-only inside
        # it: that is the whole shape.
        assert any(item.split(":")[1] == str(wt) and not item.endswith(":ro")
                   for item in mounts), mounts
        # …and no destination appears twice: a duplicate pair is refused by
        # Docker as `Duplicate mount point`, so the gate could not even start
        # the lane. (The collision RULE itself is driven in
        # TestJudgeMountCollisions below, where it can be made to fail.)
        destinations = [item.split(":")[1] for item in mounts
                        if ":" in item and not item.startswith("type=")]
        assert destinations, mounts
        assert len(set(destinations)) == len(destinations), mounts
        # …and the judge line is disclosed BEFORE the lane starts.
        assert "run-gate: judge: assay" in proc.stdout
        assert "mounted read-only from" in proc.stdout


# ---------------------------------------------------------------------------
# 2b. No other mount may land on a judge tree (round-1 review)
# ---------------------------------------------------------------------------

class TestJudgeMountCollisions:
    """Measured on a real daemon: Docker orders binds by DESTINATION DEPTH,
    not by argv order; two mounts at ONE destination are refused as
    `Duplicate mount point`; and a DEEPER destination inside a read-only tree
    wins for its subtree (or fails the container with
    `mkdirat ... read-only file system`). An argv-order claim therefore protects
    nothing -- the gate must refuse the collision itself."""

    def _lane(self):
        return {"kind": "command", "environment": "rust", "judge": "assay",
                "argv": ["true"]}

    @pytest.fixture(autouse=True)
    def _bare_host_mounts(self, monkeypatch):
        # A bare host: the derived physical path EQUALS the namespace path, so
        # the second destination comes from the declared alias — the same
        # declaration RG-3 requires of any container lane here.
        monkeypatch.setattr(run_gate, "physical_path", lambda path, **kw: path)

    def _worktree(self, tmp_path, monkeypatch):
        """A fixture worktree WITH RG-3's namespace alias declared: a bare host
        derives no second view, so the alias is the only way the judge gets
        both of its destinations (and the only way `_dual` answers at all)."""
        worktree = tmp_path / "tree"
        (worktree / "assay").mkdir(parents=True)
        (worktree / "libraries" / "cowtree").mkdir(parents=True)
        monkeypatch.setenv(run_gate.MOUNT_ALIAS_ENV_VAR,
                           f"{worktree}=/workspaces/estate")
        return worktree

    def test_a_second_mount_at_the_judge_path_is_refused(
            self, tmp_path, monkeypatch):
        worktree = self._worktree(tmp_path, monkeypatch)
        with pytest.raises(run_gate.GateError, match=r"Duplicate mount point"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["-v", f"/tmp/other:{worktree / 'assay'}:ro"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    def test_a_mount_inside_a_judge_tree_is_refused(
            self, tmp_path, monkeypatch):
        """Deeper wins for its subtree, which is exactly the read-only claim
        a deeper mount would break."""
        worktree = self._worktree(tmp_path, monkeypatch)
        with pytest.raises(run_gate.GateError, match=r"INSIDE the judge tree"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["-v", f"/tmp/other:{worktree / 'libraries/cowtree/sub' }"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    def test_a_shallow_mount_of_the_judged_worktree_is_accepted(
            self, tmp_path, monkeypatch):
        """The worktree mount is the judge tree's PARENT and is necessary:
        a refusal that also matched it would refuse every healthy judge lane
        (the superset-refusal hazard)."""
        worktree = self._worktree(tmp_path, monkeypatch)
        run_gate.refuse_mounts_over_the_judge(
            self._lane(), "rust-r1", worktree,
            ["-v", f"{worktree}:{worktree}",
             "--mount", "type=bind,source=/cow,target=/cow"],
            "this lane's mount set")

    def test_a_SIBLING_sharing_the_prefix_is_not_inside_the_judge_tree(
            self, tmp_path, monkeypatch):
        """Round-3 review (mutant: drop the trailing `/` from the child
        check): `startswith(judge + "/")` is what makes this a CHILD test.
        Without it, a directory whose NAME merely begins with the judge
        tree's name (`assay-extra`, `assay2`) is refused as if it were inside
        it -- a refusal whose condition also matches an ordinary, legitimate
        mount (the superset-refusal hazard), which is exactly how a guard
        teaches operators to switch it off."""
        worktree = self._worktree(tmp_path, monkeypatch)
        for sibling in ("assay2", "assay-extra", "libraries/cowtree2"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["-v", f"/tmp/other:{worktree / sibling}"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["-v", f"/tmp/other:/workspaces/estate/{sibling}"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    def test_the_exact_path_and_a_real_child_are_BOTH_refused(
            self, tmp_path, monkeypatch):
        """The other direction of the same comparison: the sibling case must
        not be bought by weakening the child test into the exact test."""
        worktree = self._worktree(tmp_path, monkeypatch)
        exact = str(worktree / "assay")
        child = str(worktree / "libraries" / "cowtree" / "src" / "cowtree")
        with pytest.raises(run_gate.GateError, match=r"Duplicate mount point"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["--mount", f"type=bind,source=/else,target={exact}"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")
        with pytest.raises(run_gate.GateError, match=r"INSIDE the judge tree"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["--mount", f"type=bind,source=/else,target={child}"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    @pytest.mark.parametrize("key", run_gate.MOUNT_DESTINATION_KEYS)
    def test_every_documented_destination_spelling_is_parsed(
            self, tmp_path, monkeypatch, key):
        """Round-3 review: Docker documents `target=`, `dst=` and
        `destination=` for the SAME key. Only `target=` was read, so a mount
        written with an alias would have landed on a judge tree unseen by the
        refusal. The vocabulary is driven from `MOUNT_DESTINATION_KEYS`, the
        same tuple the parser reads."""
        worktree = self._worktree(tmp_path, monkeypatch)
        destination = str(worktree / "assay")
        with pytest.raises(run_gate.GateError, match=r"Duplicate mount point"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["--mount",
                 f"type=bind,source=/else,{key}={destination},readonly"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")
        # …and a deeper path through an alias is the child case, too.
        with pytest.raises(run_gate.GateError, match=r"INSIDE the judge tree"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["--mount",
                 f"type=bind,source=/else,{key}={destination}/sub"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    def test_the_parser_reads_both_shapes_and_ignores_the_source(self):
        """A direct oracle for the parser itself, so the collision tests above
        are not the only reader of the destination vocabulary."""
        flags = ["-v", "/else:/judge:ro",
                 "--mount", "type=bind,source=/src,target=/one,readonly",
                 "--mount", "type=bind,dst=/two,source=/src",
                 "--mount", "type=bind,destination=/three,source=/src",
                 "--mount", "type=tmpfs,target=/four"]
        assert run_gate.mount_flag_destinations(flags) == [
            "/judge", "/one", "/two", "/three", "/four"]
        # An entry with no destination key contributes nothing rather than an
        # empty string that would silently compare equal to `""`.
        assert run_gate.mount_flag_destinations(
            ["--mount", "type=bind,source=/src"]) == []
        # …and neither does a destination key written with no value: an empty
        # destination is not a place a mount can land, so reporting it would
        # add a phantom entry to the collision set.
        assert run_gate.mount_flag_destinations(
            ["--mount", "type=bind,source=/src,target="]) == []
        assert run_gate.mount_flag_destinations(["target=/orphan"]) == []

    def test_the_alias_view_of_a_judge_tree_is_also_refused(
            self, tmp_path, monkeypatch):
        """The judge is dual-mounted, so the namespace destination is a
        real second way to collide with it."""
        worktree = self._worktree(tmp_path, monkeypatch)
        with pytest.raises(run_gate.GateError, match=r"Duplicate mount point"):
            run_gate.refuse_mounts_over_the_judge(
                self._lane(), "rust-r1", worktree,
                ["-v", "/tmp/other:/workspaces/estate/assay"],
                f"${run_gate.EXTRA_MOUNT_ENV_VAR}")

    def test_the_live_run_refuses_an_extra_mount_over_the_judge(
            self, tmp_path, monkeypatch):
        """End to end through the runner: $RUN_GATE_EXTRA_MOUNTS is the
        operator's own knob and it must not be able to re-open the judge.
        Nothing is started (no `docker run` in the log)."""
        repo, proj = _judge_repo(tmp_path, image="tester-unified:run-gate-2560")
        log = fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        monkeypatch.setenv(run_gate.EXTRA_MOUNT_ENV_VAR,
                           f"/tmp/other={repo / 'assay'}/sub")
        proc = run_tool(proj, "rust-r1")
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "INSIDE the judge tree" in proc.stderr
        assert [run for run in docker_runs(log) if "-d" in run] == []


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
        _git(repo, "tag", "assay-v9.9.9")
        (repo / "assay" / "extra.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "after tag")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert identity["commit"] == head
        assert identity["name"] == "assay"
        assert identity["provided_by"] == "run-gate"
        assert identity["version"].startswith("assay-v9.9.9-1-g"), identity
        assert identity["dirty"] is False

    def test_dirty_comes_from_a_REAL_git_status_on_a_real_repository(
            self, tmp_path):
        """Round-2 review: R2 killed `capture_output=True` on the status call
        only because OTHER fixtures noticed; the DIRT case itself must be
        proven through a real `git status` against a real repository, so the
        pair (modified judge file -> True, clean tree -> False) is a fact
        about git rather than about a stub that answers what the test wants.
        No monkeypatch: the subprocess is real, and a mutant that drops
        `capture_output` reads an EMPTY stdout and would report `False`. """
        repo, _proj = _judge_repo(tmp_path)
        clean = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert clean["dirty"] is False, clean["dirty_source"]
        tracked = repo / "assay" / "src" / "assay" / "cli.py"
        assert tracked.is_file()
        tracked.write_text(tracked.read_text() + "# edited judge source\n")
        modified = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert modified["dirty"] is True
        # Both answers came from the same real command, so the value is the
        # tree's state rather than a default.
        assert modified["dirty_source"].startswith(
            "git status --porcelain --ignored=traditional --")
        # …and the command the record states is the one that RAN, bytecode
        # excludes included (round 3), so an operator can repeat it.
        for exclude in run_gate.JUDGE_BYTECODE_EXCLUDES:
            assert exclude in modified["dirty_source"]

    def test_a_non_utf8_path_in_the_judge_tree_is_still_answered(
            self, tmp_path):
        """Round-2 review: `text=True` on the status call would raise
        UnicodeDecodeError (a ValueError, NOT the `except OSError` there) on a
        `core.quotepath=false` repository containing a non-UTF-8 filename, so
        the identity would crash instead of stating that the tree is dirty.
        The BYTES of `git status` answer the only question asked here."""
        repo, _proj = _judge_repo(tmp_path)
        _git(repo, "config", "core.quotepath", "false")
        Path(repo / os.fsdecode(b"assay/src/\xff-odd.txt")).write_bytes(b"x\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True, identity["dirty_source"]

    def test_a_non_utf8_tag_name_cannot_break_the_version_derivation(
            self, tmp_path):
        """The same failure mode one call over: `git describe` renders an
        arbitrary tag name verbatim, and text mode's strict decoding would
        raise out of a function whose contract is `null` WITH a reason."""
        repo, _proj = _judge_repo(tmp_path)
        _git(repo, "tag", os.fsdecode(b"assay-v1.0.0-\xff"))
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is False
        assert identity["version"] is None
        assert identity["version_source"].startswith("no assay-v*")

    def test_an_uncommitted_judge_is_recorded_as_dirty_never_as_clean(
            self, tmp_path):
        repo, _proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / "assay" / "touched.py").write_text("x = 1\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True
        assert "DIRTY" in run_gate.judge_identity_line(identity)

    def test_dirt_ELSEWHERE_in_the_repo_leaves_the_judge_clean(self, tmp_path):
        """`dirty` is SCOPED to the judge trees: an edit to the project under
        judgment is not an edit to the judge (round-1 review item 4c)."""
        repo, _proj = _judge_repo(tmp_path)
        (repo / "unrelated.txt").write_text("x\n")
        (repo / "proj" / "edited.txt").write_text("y\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is False, identity["dirty_source"]

    def test_an_IGNORED_file_under_the_judge_tree_makes_it_dirty(
            self, tmp_path):
        """`git status --porcelain` alone never reports an ignored file, so a
        judge tree whose only change was a build artifact read as clean -- a
        false certification about exactly the bytes the mount contains
        (round-1 review item 5). The flags below were verified against git's
        own behaviour in a scratch repository (`--ignored=traditional` shows
        it, and it stays hidden without any `--ignored` mode)."""
        repo, _proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / ".gitignore").write_text("artifact.txt\n")
        commit_all(repo, "ignore a build artifact")
        (repo / "assay" / "src" / "artifact.txt").write_text("built\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True
        # The scoped command is the reason, and it names the mode that makes
        # this answer honest rather than merely conventional.
        assert "--ignored=traditional" in identity["dirty_source"]

    def test_an_ignored_BYTECODE_CACHE_does_not_make_the_judge_dirty(
            self, tmp_path):
        """Round 3: `--ignored=matching` lists an ignored `__pycache__` as a
        DIRECTORY (`!! <tree>/__pycache__/`), which no pathspec exclude can
        remove (measured). A developer worktree where assay or pytest once ran
        on the host therefore warned DIRTY on every judge lane. Bytecode is
        regenerated, never the source a verdict describes, so it is excluded --
        while every OTHER ignored file still makes the judge dirty (the next
        oracle), which is what keeps the exclusion from being a blanket
        "ignore ignored files" waiver."""
        repo, _proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / ".gitignore").write_text(
            "__pycache__/\n*.pyc\n")
        commit_all(repo, "ignore bytecode")
        package = repo / "assay" / "src" / "assay"
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "cli.cpython-314.pyc").write_bytes(b"x")
        (package / "stray.pyc").write_bytes(b"y")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is False, identity["dirty_source"]
        assert "__pycache__" in identity["dirty_source"]
        # An UNTRACKED real source file beside the caches is still dirty: the
        # exclusion is about bytecode, not about untracked files as a class.
        (package / "new_module.py").write_text("x = 1\n")
        assert run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)["dirty"] is True

    def test_an_ignored_NON_bytecode_file_still_makes_the_judge_dirty(
            self, tmp_path):
        """The other half of the exclusion: every ignored file that is NOT a
        bytecode cache keeps marking the judge dirty, so "clean" cannot be
        bought by widening the pathspec."""
        repo, _proj = _judge_repo(tmp_path)
        (repo / "assay" / "src" / ".gitignore").write_text("build.log\n")
        commit_all(repo, "ignore a build artifact")
        (repo / "assay" / "src" / "build.log").write_text("log line\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True, identity["dirty_source"]

    def test_a_repository_that_HIDES_untracked_files_still_reports_them(
            self, tmp_path):
        """The plain command inherits the repository's own
        `status.showUntrackedFiles` setting, and a consumer config can set it
        to `no`. Forcing `all` per invocation is what makes the answer a fact
        about the tree instead of about somebody's `.git/config`."""
        repo, _proj = _judge_repo(tmp_path)
        _git(repo, "config", "status.showUntrackedFiles", "no")
        (repo / "assay" / "src" / "assay" / "untracked.py").write_text("x\n")
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is True

    def test_git_status_FAILING_is_unknown_with_its_own_reason(
            self, tmp_path, monkeypatch):
        """A non-zero status is not "clean": it is an answer that never came
        (round-1 review item 4b). Distinct from the git-absent case, and the
        record says which."""
        repo, _proj = _judge_repo(tmp_path)
        original_run = run_gate.subprocess.run

        def run(argv, **kw):
            if "status" in argv:
                # Every OTHER git call (describe, rev-parse) still answers.
                return SimpleNamespace(returncode=128, stdout=b"",
                                       stderr="fatal: bad repo")
            return original_run(argv, **kw)
        # No `raising=False`: `run_gate.subprocess` IS the stdlib module and
        # `run` exists on it, so the flag only stepped over a hypothetical
        # absent attribute -- if that ever became true, this stub should fail
        # loudly at SET time rather than silently patch nothing (round-2
        # review's bool sweep).
        monkeypatch.setattr(run_gate.subprocess, "run", run)
        identity = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert identity["dirty"] is None
        assert "answered 128" in identity["dirty_source"]
        assert "dirty state unknown" in run_gate.judge_identity_line(identity)

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
        _git(repo, "tag", "run-gate-v1.2.3")
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
        # `parents` is required here: `libraries/` does not exist yet.
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
    # Round 3: the guard's interpreter is chosen by the LANE (`command -v
    # python3` in the image), so an oracle must not borrow the interpreter it
    # is itself RUN under. The first cut did exactly that (`sys.executable`,
    # inherited `os.environ`), and this suite's interpreter carries a REAL
    # `assay` -- an installed/editable distribution in site-packages, or on an
    # inherited `PYTHONPATH` -- which made the refusal arm resolve a file where
    # `tester-unified:run-gate-2560` sees nothing but a namespace directory. The
    # oracle passed here and failed in the lane (the same class as assay B226).
    #
    # `-S` is the flag that fits: it drops site-packages, `.pth` hooks and
    # editable finders while KEEPING `PYTHONPATH` -- which is the mechanism
    # under test (`-I` would discard `PYTHONPATH` and prove nothing). The
    # environment is built from scratch, so no inherited `PYTHONPATH` reaches
    # the probe, and the cwd is a directory that holds no `assay` at all, so
    # `sys.path[0]` cannot inject a namespace portion by accident.
    GUARD_ENV = {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    @pytest.fixture
    def guard_cwd(self, tmp_path):
        """A cwd that holds no `assay` at all, so `sys.path[0]` cannot inject
        a namespace portion the probe did not ask for.

        A fixture rather than a `mkdir(exist_ok=True)` inside the probe: the
        SAME oracle calls the probe several times (one accepted arm, several
        refused arms) and must reuse ONE directory, so "it already exists" is
        the fixture's own documented lifetime instead of a boolean standing in
        for it.
        """
        cwd = tmp_path / "guard-cwd"
        cwd.mkdir()
        return cwd

    def _guard(self, tmp_path, pythonpath, cwd):
        roots = ":".join(str(tmp_path / entry)
                         for entry in run_gate.JUDGE_IMPORT_ROOTS)
        # `check` is left at its default (False) DELIBERATELY and the return
        # codes are asserted instead: one arm MUST succeed and the others MUST
        # fail, and `check=True` would turn those into a CalledProcessError the
        # assertions never reach.
        return subprocess.run(
            [sys.executable, "-S", "-c", run_gate._JUDGE_IMPORT_GUARD_CODE,
             roots, *(part for name, rel in run_gate.JUDGE_IMPORT_NAMES
                      for part in (name, rel))],
            capture_output=True, text=True,
            env={"PATH": os.defpath, "PYTHONPATH": pythonpath,
                 **self.GUARD_ENV},
            cwd=str(cwd))

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
        # assay must not judge silently. The code it ends on is the RESERVED
        # one, not a lane-remappable 2 (see TestJudgeExitCodeBoundary).
        assert "was NOT provided to this lane" in joined
        assert f"exit {run_gate.JUDGE_REFUSAL_EXIT_CODE}" in joined
        assert "exit 2" not in joined

    def test_a_lane_without_the_key_gets_no_setup(self, tmp_path):
        assert run_gate.judge_source_setup(
            {"kind": "command", "environment": "rust", "argv": ["true"]},
            tmp_path) == []

    def test_the_guard_accepts_the_provided_tree_and_refuses_another(
            self, tmp_path, guard_cwd):
        """Both arms against fixtures the TEST built: the provided tree on
        `PYTHONPATH` is accepted, and a SECOND complete judge tree -- nowhere
        near the provided one -- is refused by name.

        The refusal arm used to lean on the interpreter's own `assay`, so the
        oracle asked what the RUNNING interpreter already had rather than what
        the guard decides (round 3: green here, `AssertionError: None` in the
        lane image). A second fixture tree makes the answer a fact of the test.
        """
        _minimal_judge(tmp_path)
        provided = ":".join(str(tmp_path / entry)
                             for entry in run_gate.JUDGE_IMPORT_ROOTS)
        accepted = self._guard(tmp_path, provided, guard_cwd)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        elsewhere = tmp_path / "elsewhere"
        _minimal_judge(elsewhere)
        refused = self._guard(tmp_path, ":".join(
            str(elsewhere / entry) for entry in run_gate.JUDGE_IMPORT_ROOTS),
            guard_cwd)
        # The GUARD's own status is 2 -- it is a probe run by the lane's
        # shell, which is what translates it into the RESERVED lane code
        # (JUDGE_REFUSAL_EXIT_CODE) so the lane's `exit_map` cannot remap a
        # refusal into a verdict.
        assert refused.returncode == 2, refused.stdout
        # …and the refusal NAMES the module and the origin it resolved instead.
        # This also pins `text=True` on the probe: dropped, `stdout` would be
        # bytes and this `str` assertion could not even be written (a round-2
        # bool-sweep finding -- the return-code assertion alone never noticed
        # the flag go away).
        assert isinstance(refused.stdout, str)
        assert isinstance(accepted.stdout, str)
        named = refused.stdout.strip()
        assert named.startswith("assay: "), refused.stdout
        assert str(elsewhere / "assay/src/assay/__init__.py") in named, \
            refused.stdout
        assert all(str(tmp_path / entry / "assay/__init__.py") not in named
                   for entry in run_gate.JUDGE_IMPORT_ROOTS), refused.stdout

    def test_a_shadow_directory_is_NAMED_not_printed_as_None(self, tmp_path,
                                                             guard_cwd):
        """The exact real-lane shape (round 3): a directory called `assay`
        with no `__init__.py` on the path is a NAMESPACE PORTION, `find_spec`
        answers `origin is None`, and the first cut printed the literal string
        `None` -- into a message whose whole job is to say where the module
        came from. It must name the module and the portion that shadowed it."""
        _minimal_judge(tmp_path)
        shadow = tmp_path / "shadow" / "assay"
        shadow.mkdir(parents=True)
        refused = self._guard(tmp_path, str(shadow.parent), guard_cwd)
        assert refused.returncode == 2, refused.stdout
        assert "assay: a namespace package with no __init__.py" \
            in refused.stdout, refused.stdout
        assert str(shadow) in refused.stdout, refused.stdout

    def test_an_absent_judge_is_refused_as_absent_not_as_a_bare_None(
            self, tmp_path, guard_cwd):
        """The third shape: nothing of that name on `sys.path` at all. A
        refusal that cannot say so sends the operator looking for a file that
        does not exist (`None` is printed as if it were a location)."""
        _minimal_judge(tmp_path)
        empty = tmp_path / "nothing-here"
        empty.mkdir()
        refused = self._guard(tmp_path, str(empty), guard_cwd)
        assert refused.returncode == 2, refused.stdout
        assert "assay: nothing: no module of this name is on sys.path" \
            in refused.stdout, refused.stdout

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
    # No `exist_ok`: the directory does NOT exist here (a fresh `tmp_path`),
    # so the flag was a default standing in for a fact the fixture already
    # knows. If it ever did exist, that is a fixture bug worth failing on
    # rather than stepping over (round-2 review's bool sweep).
    state.mkdir()
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


def _remove_quietly(path: Path) -> None:
    """Remove a fixture directory without ever failing the run.

    Registered with `atexit`, so it runs at INTERPRETER EXIT, where an
    exception cannot be attributed to a test and would only corrupt the
    session's exit status. That is the behaviour, so it is written as
    behaviour (a swallowed `OSError`) instead of as `ignore_errors=True` --
    one fewer unpinned boolean, and the promise is visible at the call.
    """
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def restricted_path(*names: str) -> str:
    """A `PATH` holding symlinks to exactly the named host tools.

    Used to model an image: the lane's own shell resolves ONLY what is linked
    here, so `command -v python3` failing is a fact of the fixture rather than
    of the host running the gate.
    """
    directory = Path(tempfile.mkdtemp(prefix="run-gate-test-restricted-"))
    atexit.register(_remove_quietly, directory)
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
        `assay_analysis` must fail the lane (ERROR, naming what was not
        provided) instead of running a partially importable judge. This is the
        oracle a construction-only pin of the guard string cannot supply."""
        repo, proj = _judge_repo(tmp_path)
        shutil.rmtree(repo / "assay" / "analysis")
        commit_all(repo, "drop the analysis sibling")
        fake_docker_running_lane(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_TEST_LANE_PATH",
                           restricted_path(*self.LANE_PATH_TOOLS))
        proc = run_tool(proj, "rust-r1")
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "verdict ERROR" in proc.stdout
        assert f"reason {run_gate.JUDGE_REFUSAL_REASON}" in proc.stdout
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
        assert "verdict ERROR" in proc.stdout
        assert f"reason {run_gate.JUDGE_REFUSAL_REASON}" in proc.stdout
        assert "does not install an interpreter" in proc.stdout
        # …and the refusal names the declaration that needs it.
        assert "judge" in proc.stdout


# ---------------------------------------------------------------------------
# 5b. The refusal's exit code belongs to RUN-GATE, not to `exit_map`
# ---------------------------------------------------------------------------

class TestJudgeExitCodeBoundary:
    """Round-1 review: an in-lane refusal that ends on an ordinary code
    passes through the lane's own `exit_map`, so "the judge was not provided"
    arrived as a FAILED LANE (no mapping), or as a PASS (`2 = "PASS"`).
    Run-gate owns the mapping for the refusal IT emits: the same refusal must
    read ERROR in all three configurations, and a lane that tries to remap the
    reserved code is refused at load rather than trusted to report honestly."""

    def _run_with_map(self, tmp_path, monkeypatch, exit_map):
        config = JUDGE_CONFIG
        if exit_map is None:
            config = config.replace(
                'exit_map = { "0" = "PASS", "2" = "ERROR" }\n', "")
        else:
            config = config.replace(
                'exit_map = { "0" = "PASS", "2" = "ERROR" }',
                "exit_map = { " + ", ".join(
                    f'"{code}" = "{verdict}"'
                    for code, verdict in exit_map.items()) + " }")
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = make_project(repo, config.replace("{image}",
                                                 "tester-unified-rust:local"))
        (proj / "assay.toml").write_text(
            _lanes_toml(["/bin/sh", "-c", "true"]))
        commit_all(repo, "fixture")
        # A judge tree that cannot serve every module: the guard MUST refuse.
        shutil.rmtree(repo / "assay" / "analysis")
        commit_all(repo, "drop the analysis sibling")
        fake_docker_running_lane(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_TEST_LANE_PATH",
                           restricted_path(*TestJudgeEndToEnd.LANE_PATH_TOOLS))
        return proj, run_tool(proj, "rust-r1")

    @pytest.mark.parametrize("exit_map", [None, {"2": "FAIL"},
                                          {"2": "PASS"}])
    def test_a_judge_refusal_is_error_whatever_exit_map_says(
            self, tmp_path, monkeypatch, exit_map):
        _proj, proc = self._run_with_map(tmp_path, monkeypatch, exit_map)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "verdict ERROR" in proc.stdout, proc.stdout
        assert "verdict FAIL" not in proc.stdout, proc.stdout
        assert "verdict PASS" not in proc.stdout, proc.stdout
        assert f"reason {run_gate.JUDGE_REFUSAL_REASON}" in proc.stdout

    def test_the_mapping_is_owned_before_the_lane_table(self):
        """The boundary is a ORDERING fact, driven directly: the reserved
        code is answered before `exit_map` is even read."""
        lane = {"kind": "command", "environment": "rust", "judge": "assay",
                "exit_map": {"2": "PASS"}}
        result = run_gate.command_lane_result(
            run_gate.JUDGE_REFUSAL_EXIT_CODE, lane)
        assert result.verdict == "ERROR"
        assert result.reason == run_gate.JUDGE_REFUSAL_REASON
        # …and a NON-judge lane's own 63 still maps as its author wrote it,
        # so the reservation is scoped to the lane the refusal belongs to.
        assert run_gate.command_lane_result(
            run_gate.JUDGE_REFUSAL_EXIT_CODE, {"argv": ["true"]}).verdict \
            == "FAIL"

    def test_a_judge_lane_may_not_remap_the_reserved_code(self):
        with pytest.raises(run_gate.GateError, match=r"RESERVED"):
            run_gate._validate_lane(
                "rust-r1", {"kind": "command", "environment": "rust",
                            "argv": ["true"], "judge": "assay",
                            "exit_map": {
                                str(run_gate.JUDGE_REFUSAL_EXIT_CODE): "PASS"}},
                "fixture")
        # Every other code maps as usual: the reservation is one code, not
        # the table.
        run_gate._validate_lane(
            "rust-r1", {"kind": "command", "environment": "rust",
                        "argv": ["true"], "judge": "assay",
                        "exit_map": {"2": "ERROR"}}, "fixture")



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
                returncode=0,
                # describe answers in BYTES, exactly like the production
                # call; rev-parse keeps its `text=True` str answer.
                stdout=(b"assay-v-not-a-release-0-gdeadbeef\n"
                        if "describe" in argv else f'{"a" * 40}\n')))
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
                returncode=0,
                stdout=(b"assay-v9.9.9\n" if "describe" in argv
                        else f'{"a" * 40}\n')))
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

    def test_a_clean_judge_prints_no_warning(
            self, tmp_path, monkeypatch, capsys):
        """The three states of `dirty` must read differently (round-1 review
        item 4d): clean says nothing, True says DIRTY, None says UNKNOWN with
        the reason -- a ternary collapsed the last two into one sentence."""
        _repo, _proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "rust-r1")
        assert code == 0, streams.err
        assert "(clean;" in streams.out
        assert "WARNING: judge source" not in streams.out

    def test_an_UNDETERMINED_cleanliness_is_disclosed_as_unknown(
            self, tmp_path, monkeypatch, capsys):
        """`dirty: null` is the third state, and it may not borrow the dirty
        warning's wording: the record already carries the reason."""
        repo, proj = _judge_repo(tmp_path)
        original_run = run_gate.subprocess.run

        def run(argv, **kw):
            if "status" in argv:
                return SimpleNamespace(returncode=128, stdout=b"",
                                       stderr="fatal: bad repo")
            return original_run(argv, **kw)
        monkeypatch.setattr(run_gate.subprocess, "run", run)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.chdir(proj)
        assert run_gate.main(["rust-r1"]) == 0
        streams = capsys.readouterr()
        assert "dirty state unknown" in streams.out
        assert "cleanliness is UNKNOWN" in streams.out
        assert "WARNING: judge source is dirty" not in streams.out
        latest = json.loads(
            (proj / ".run-gate/history.json").read_text()
        )["lanes"]["rust-r1"]["latest"]
        assert latest["judge"]["dirty"] is None
        assert "answered 128" in latest["judge"]["dirty_source"]

    @pytest.mark.parametrize("field, stale_value", [
        ("commit", "0" * 40),
        # Round-3 review: only the commit was ever varied, so a refresh that
        # compared JUST the commit survived -- although `version` and `dirty`
        # are recorded beside it for the same reason (a tag landing, or a file
        # being edited, between the preflight and the container start would
        # write an identity the mount does not hold). Each field now has its
        # own stale-only case, and the mutant that keeps two of the three
        # comparison terms dies.
        ("version", "assay-v0.0.1-1-gdeadbeef"),
        ("dirty", True),
    ])
    def test_every_COMPARED_field_alone_triggers_the_re_derivation(
            self, tmp_path, capsys, field, stale_value):
        """The refresh names the change and records what the mount holds, for
        EACH of the three fields it compares."""
        repo, _proj = _judge_repo(tmp_path)
        fresh = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        # The stale value must DIFFER from the fresh one, or the case would
        # pass for the opposite reason (a comparison that never had a change
        # to see). The fixture is a clean repository with no release tag, so
        # both of these hold; assert it rather than assume it.
        assert fresh[field] != stale_value, (field, fresh[field])
        stale = dict(fresh, **{field: stale_value})
        lane = {"kind": "command", "environment": "rust",
                "judge": "assay", "_judge_identity": stale}
        record: dict = {}
        returned = run_gate.refresh_judge_identity(lane, "rust-r1", repo,
                                                  record)
        assert record["judge"] == fresh
        assert record["judge"][field] == fresh[field]
        assert returned[field] == fresh[field]
        assert lane["_judge_identity"][field] == fresh[field]
        assert f"{field} {stale_value!r} -> {fresh[field]!r}" \
            in capsys.readouterr().err

    def test_the_identity_is_RE_DERIVED_at_the_container_start(
            self, tmp_path, monkeypatch, capsys):
        """Round-1 review item 5, second half: the preflight derives the
        identity before admission and the container starts later, so a commit
        landing in between would be recorded under the wrong bytes. The
        refresh names the change and records what the mount actually holds."""
        repo, proj = _judge_repo(tmp_path)
        stale = dict(run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo))
        stale["commit"] = "0" * 40
        lane = {"kind": "command", "environment": "rust",
                "judge": "assay", "_judge_identity": stale}
        record = {"commit": "0" * 40}
        monkeypatched = run_gate.refresh_judge_identity(
            lane, "rust-r1", repo, record)
        assert record["judge"]["commit"] != "0" * 40
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert record["judge"]["commit"] == head
        assert lane["_judge_identity"]["commit"] == head
        assert monkeypatched["commit"] == head
        captured = capsys.readouterr()
        assert "changed between the preflight and this container's start" \
            in captured.err

    def test_the_runner_re_derives_a_STALE_preflight_snapshot(
            self, tmp_path, monkeypatch, capsys):
        """Driven through `run_gate.main`, not by calling the helper: an oracle
        that only calls `refresh_judge_identity` itself would stay green if the
        runner stopped calling it (round-1 mutant M10)."""
        stale = dict(run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            tmp_path / "elsewhere"))
        stale["commit"] = "0" * 40
        real_preflight = run_gate.preflight_judge_provider

        def preflight(lane, lane_name, worktree, env, source):
            identity = real_preflight(lane, lane_name, worktree, env, source)
            return {**identity, "commit": stale["commit"]} \
                if identity is not None else None
        monkeypatch.setattr(run_gate, "preflight_judge_provider", preflight)
        _repo, proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "rust-r1")
        assert code == 0, streams.err
        head = _git(proj.parent, "rev-parse", "HEAD").strip()
        latest = json.loads(
            (proj / ".run-gate/history.json").read_text()
        )["lanes"]["rust-r1"]["latest"]
        assert latest["judge"]["commit"] == head
        assert latest["judge"]["commit"] != "0" * 40
        assert "changed between the preflight and this container's start" \
            in streams.err

    def test_the_inflight_record_carries_the_judge_it_started_with(
            self, tmp_path, monkeypatch, capsys):
        """A re-attach can only adopt what was WRITTEN. The payload is spied
        on rather than read back, because the record is cleared when the run
        finishes -- the fact under test is what a client that DIES would have
        left behind (round-1 mutant M11)."""
        written = {}
        real_write = run_gate.write_inflight_record

        def spy(project_dir, worktree, lane_name, payload):
            written[lane_name] = copy.deepcopy(payload)
            return real_write(project_dir, worktree, lane_name, payload)
        monkeypatch.setattr(run_gate, "write_inflight_record", spy)
        repo, _proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "rust-r1")
        assert code == 0, streams.err
        payload = written["rust-r1"]
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert isinstance(payload["judge"], dict), payload["judge"]
        assert payload["judge"]["name"] == "assay"
        assert payload["judge"]["commit"] == head

    def test_an_unchanged_identity_is_not_re_announced(
            self, tmp_path, monkeypatch, capsys):
        """The refresh is silent when nothing moved -- a warning on every run
        would be the noise that teaches readers to ignore it."""
        repo, _proj = _judge_repo(tmp_path)
        lane = {"kind": "command", "environment": "rust",
                "judge": "assay"}
        current = run_gate.judge_identity(lane, repo)
        lane["_judge_identity"] = dict(current)
        record: dict = {}
        run_gate.refresh_judge_identity(lane, "rust-r1", repo, record)
        assert record["judge"]["commit"] == current["commit"]
        assert "changed between" not in capsys.readouterr().err

    def test_help_states_which_judge_run_gate_will_mount(
            self, tmp_path, monkeypatch, capsys):
        _repo, _proj, code, streams = self._run(
            tmp_path, monkeypatch, capsys, "--help")
        assert code == 0
        assert "judge=assay (provided by run-gate)" in streams.out

    def test_a_re_attached_run_records_the_judge_it_started_with(
            self, tmp_path):
        """Round-1 review item 5, first half: adopting a prior run must not
        record the CURRENT worktree's judge as the original run's. The inflight
        record carries the identity, and adopting it wins over a fresh
        derivation; a pre-feature record (no `judge` key) records none at all.
        Driven at the boundary itself, so no container fixture is involved."""
        repo, _proj = _judge_repo(tmp_path)
        started_with = {"provided_by": "run-gate", "name": "assay",
                        "commit": "1" * 40, "version": None,
                        "version_source": "recorded by the starting client",
                        "dirty": None, "dirty_source": "not re-checked",
                        "source": f"{repo}/assay", "pythonpath": [],
                        "mounts": ["assay"]}
        record: dict = {}
        run_gate.adopt_inflight_start(
            record, {"started_at": "2026-09-02T11:00:00Z",
                     "judge": started_with})
        assert record["judge"] == started_with
        # A legacy record (no judge at all) adopts NONE: today's derivation is
        # not evidence about a run that started without one.
        legacy: dict = {"judge": {"commit": "2" * 40}}
        run_gate.adopt_inflight_start(
            legacy, {"started_at": "2026-09-02T11:00:00Z"})
        assert "judge" not in legacy
        # …and the inflight payload a fresh container writes carries it, so a
        # later re-attach HAS something to adopt.
        fresh = run_gate.judge_identity(
            {"kind": "command", "environment": "rust", "judge": "assay"},
            repo)
        assert fresh["commit"] == _git(repo, "rev-parse", "HEAD").strip()


# ---------------------------------------------------------------------------
# 6b. A SEQUENCE preflights its judge members before its first member runs
# ---------------------------------------------------------------------------

class TestJudgeDisclosureOrdering:
    """Round-2 review: the three R2 survivors were `flush=True -> False` on
    the disclosure lines. They survived because every oracle compared TEXT,
    and text is identical flushed or not. These record the ORDER instead --
    `FlushRecorder` counts `write` and `flush` as they happen -- so a removed
    flush is a red test rather than a comment. The property matters for the
    stated reason: run-gate's stdout is a pipe whenever a caller consumes it
    live, and a pipe is block buffered."""

    LANE = {"kind": "command", "environment": "rust", "judge": "assay"}

    def _identity(self, dirty, dirty_source="git status ..."):
        return {"provided_by": "run-gate", "name": "assay", "commit": "0" * 40,
                "version": None, "version_source": "no assay-v* release tag",
                "dirty": dirty, "dirty_source": dirty_source,
                "source": "/tree/assay", "pythonpath": [], "mounts": ["assay"]}

    @pytest.mark.parametrize("dirty", [False, True, None])
    def test_each_disclosure_line_is_flushed_immediately_after_its_write(
            self, dirty):
        lane = {**self.LANE, "_judge_identity": self._identity(dirty)}
        stream = FlushRecorder()
        with contextlib.redirect_stdout(stream):
            run_gate.print_judge_provider(lane)
        assert stream.line_is_flushed("run-gate: judge: assay"), stream.events
        if dirty is True:
            assert stream.line_is_flushed("WARNING: judge source is dirty")
        elif dirty is None:
            assert stream.line_is_flushed("cleanliness is UNKNOWN")
        else:
            # Clean says nothing beyond the one line, so nothing else may be
            # flushed either -- a warning would be a false alarm.
            assert len(stream.events) == 3     # write, newline, flush

    def test_the_re_derivation_warning_is_flushed_on_stderr(
            self, tmp_path, capsys):
        """The same contract for the OTHER judge disclosure: a changed
        identity between preflight and container start."""
        repo, _proj = _judge_repo(tmp_path)
        stale = dict(run_gate.judge_identity(self.LANE, repo))
        stale["commit"] = "0" * 40
        lane = {**self.LANE, "_judge_identity": stale}
        stream = FlushRecorder()
        with contextlib.redirect_stderr(stream):
            run_gate.refresh_judge_identity(lane, "rust-r1", repo, {})
        assert stream.line_is_flushed("changed between the preflight"), \
            stream.events

    def test_the_sequence_member_line_is_flushed(
            self, tmp_path, monkeypatch, capsys):
        """And the third: the line the sequence prints before member one, so
        a piped log cannot show member one's output first."""
        config = textwrap.dedent("""\
            schema_version = 1

            [environments.rust]
            mode = "ephemeral"
            image = "tester-unified:run-gate-2560"

            [lanes.judged]
            kind = "command"
            environment = "rust"
            judge = "assay"
            argv = ["bash", "-c", "echo judged-marker"]
            clean_tree = false

            [lanes.gate]
            kind = "sequence"
            lanes = ["judged"]
            """)
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = make_project(repo, config)
        monkeypatch.chdir(proj)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        stream = FlushRecorder()
        with contextlib.redirect_stdout(stream):
            assert run_gate.main(["gate"]) == 0
        assert stream.line_is_flushed("preflight: member 'judged'"), \
            stream.events


class TestJudgeSequencePreflight:
    """Round-1 review item 8: every member is its own invocation, so a judge
    member's `external-missing` used to surface only AFTER the members ahead
    of it had run -- a sequence that spent work and then reported a
    precondition. The precondition now belongs at the sequence's own start."""

    def test_a_judge_member_is_preflighted_BEFORE_the_first_member_runs(
            self, tmp_path, monkeypatch, capsys):
        config = textwrap.dedent("""\
            schema_version = 1

            [environments.rust]
            mode = "ephemeral"
            image = "tester-unified:run-gate-2560"

            [lanes.first]
            kind = "command"
            environment = "rust"
            argv = ["bash", "-c", "echo first-marker"]
            clean_tree = false

            [lanes.judged]
            kind = "command"
            environment = "rust"
            judge = "assay"
            argv = ["bash", "-c", "echo judged-marker"]
            clean_tree = false

            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "judged"]
            """)
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = make_project(repo, config)
        monkeypatch.chdir(proj)
        # …and the judge tree the LATER member needs is gone: the sequence must
        # refuse before its FIRST member starts.
        shutil.rmtree(repo / "assay")
        commit_all(repo, "drop the judge the second member needs")
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        code = run_gate.main(["gate"])
        streams = capsys.readouterr()
        assert code == 3, streams.out + streams.err
        assert "reason external-missing" in streams.out, streams.out
        assert "judged" in streams.err, streams.err
        assert "first-marker" not in streams.out
        # …and nothing at all was started, so the members' own preflights
        # never had a container to refuse into.
        assert [call for call in lane_runs(
            tmp_path / "docker-calls.log")] == []

    def test_a_healthy_sequence_discloses_each_judge_member_up_front(
            self, tmp_path, monkeypatch, capsys):
        """The positive side: the preflight runs on the healthy path too and
        SAYS which judge each judge member will get, before member one."""
        config = textwrap.dedent("""\
            schema_version = 1

            [environments.rust]
            mode = "ephemeral"
            image = "tester-unified:run-gate-2560"

            [lanes.first]
            kind = "command"
            environment = "rust"
            argv = ["bash", "-c", "echo first-marker"]
            clean_tree = false

            [lanes.judged]
            kind = "command"
            environment = "rust"
            judge = "assay"
            argv = ["bash", "-c", "echo judged-marker"]
            clean_tree = false

            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "judged"]
            """)
        repo = make_repo(tmp_path)
        _minimal_judge(repo)
        proj = make_project(repo, config)
        monkeypatch.chdir(proj)
        fake_docker(tmp_path, monkeypatch)
        monkeypatch.setenv("RUN_GATE_ALLOW_MAIN_CHECKOUT", "1")
        assert run_gate.main(["gate"]) == 0
        streams = capsys.readouterr()
        line = ("sequence 'gate' preflight: member 'judged' provides its "
                "judge")
        assert line in streams.out, streams.out
        # …and it was said before ANY member produced a line of its own.
        assert streams.out.index(line) < streams.out.index(
            "run-gate: lane 'first'"), streams.out
        # Round-3 review (d): the sequence has BOTH kinds of member, so the
        # `leaf.get("judge") is None -> continue` guard is what decides which
        # ones are preflighted. Exactly ONE preflight line may appear, and it
        # must name the judge member: a flipped guard (preflight nothing, or
        # preflight only the NON-judge members) dies here, and so does one that
        # drops the `continue` (a non-judge leaf has no identity to disclose).
        preflights = [piece for piece in streams.out.split("\n")
                      if "provides its judge" in piece]
        assert len(preflights) == 1, preflights
        assert "member 'judged'" in preflights[0], preflights
        # …and the NON-judge member still RAN, unchanged: the preflight adds
        # nothing to a lane that asks for no judge (it did not acquire an
        # identity line, and its own output is exactly its own).
        assert "judge" not in "".join(
            piece for piece in streams.out.split("\n")
            if "lane 'first'" in piece), streams.out
        assert "run-gate: lane 'judged'" in streams.out, streams.out


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

    def test_the_mount_mechanism_is_documented_as_the_destination(self):
        """The retracted claim must not come back.

        The first cut documented (and pinned) "the overlay is appended after
        every other mount, so it wins". Docker orders binds by DESTINATION
        DEPTH and refuses two at one path, so that claim was false and the
        protection is the refusal of a colliding destination. A document that
        still sells argv order would tell a lane author the opposite of the
        rule the code enforces, so it goes red here.
        """
        for relative in ("README.md", "CONSUMERS.md", "SPEC.md",
                         "docs/DESIGN-GUIDE.md",
                         "docs/DESIGN-judge-provider-2026-11.md"):
            text = self._read(relative).lower()
            # The retracted claim, in the shape each document carried it.
            assert "after every other mount" not in text, relative
            assert "appended after every other mount" not in text, relative
            # …and the rule that replaced it, so a document cannot lose the
            # fact AND the retraction in one careless edit.
            assert "destination" in text, relative

    def test_the_non_vbpub_limit_and_its_alternative_are_documented(self):
        """A standalone consumer (no `assay/` of its own) must be able to read
        what happens to it and what to do instead — otherwise the feature's
        own refusal is the only place that fact exists."""
        consumers = self._read("CONSUMERS.md")
        assert "RG-287" in consumers
        assert "assay_command" in consumers.split("RG-287")[0][-2500:], (
            "the non-vbpub section must name the external-judge alternative")
        backlog = self._read("KNOWN_ISSUES_TODO_BACKLOG.md")
        assert backlog.count("## RG-287 —") == 1
        assert backlog.count("## RG-286 —") == 1
        body = backlog.split("## RG-285 —")[1].split("\n## RG-2", 1)[0]
        assert "RG-286" in body, (
            "RG-285's own entry must name the real-image gap it leaves")

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

    def test_the_flush_and_decode_contracts_are_documented(self):
        """Round-2 review: two facts a reader must be able to find, because
        each is a CONTRACT rather than a preference -- an unflushed disclosure
        line can arrive after the output it precedes, and a decode choice
        decides whether an odd filename/tag crashes or is answered.

        The oracle fails on a document that stops saying either, so the pin
        lives with the rule instead of only in the code comment."""
        for relative in ("SPEC.md", "CONSUMERS.md", "docs/DESIGN-GUIDE.md",
                         "docs/DESIGN-judge-provider-2026-11.md",
                         "README.md", "CHANGES.md"):
            text = self._read(relative)
            assert "flush" in text.lower(), relative
            assert "BYTES" in text or "bytes" in text, relative
        # SPEC carries the normative form of both, including the ONE place a
        # decode failure is stated rather than avoided.
        spec = self._read("SPEC.md")
        assert "FLUSHED" in spec
        assert "not valid UTF-8" in spec or "BYTES" in spec
