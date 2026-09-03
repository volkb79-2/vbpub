"""Unit suite for tools/buildkite/ — the Buildkite seams of
REMOTE-LANES-BUILDKITE.md. This commit covers seam 2, the §3 pipeline
generator.

`pipeline.sh` is pure output, so the real script runs against a fake project
directory whose `run-gate.py` is a stub printing a fixed `--list` table. That
is the whole contract between them — `name<TAB>kind<TAB>environment`, one lane
per line — so a stub is a faithful stand-in, and no test here may parse
`run-gate.toml` any more than the generator may. No test in this file starts a
container or touches the network.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

try:                                    # optional: strengthens the YAML asserts
    import yaml
except ImportError:                     # pragma: no cover - depends on the venv
    yaml = None

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "buildkite"
PIPELINE_SH = TOOLS / "pipeline.sh"

LISTING = "mutation\tassay\ttester-unified\n" \
          "nightly-properties\tassay\ttester-unified\n" \
          "selftest\tcommand\thost\n"


def _stub_project(root: Path, listing: str = LISTING) -> Path:
    """A directory that answers `./run-gate.py --list` and nothing else."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "run-gate.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] != ['--list']:\n"
        "    sys.exit('stub: only --list is implemented')\n"
        "sys.stdout.write(%r)\n" % listing
    )
    script.chmod(0o755)
    return root


def _uncommented(script: Path) -> str:
    return "\n".join(ln for ln in script.read_text().splitlines()
                     if not ln.lstrip().startswith("#"))


def _run(script: Path, *args, cwd=None, env=None):
    environ = dict(os.environ)
    environ.pop("RUN_GATE_LANES", None)
    environ.pop("RUN_GATE_QUEUE", None)
    environ.pop("RUN_GATE_TIMEOUT_MINUTES", None)
    environ.update(env or {})
    return subprocess.run(
        [str(script), *args], cwd=str(cwd) if cwd else None, env=environ,
        capture_output=True, text=True)


# ---------------------------------------------------------------------------
# seam 2 — tools/buildkite/pipeline.sh
# ---------------------------------------------------------------------------

def test_pipeline_emits_the_section_3_step_shape(tmp_path):
    """Every key of the §3 step, for every lane the listing shows, in order."""
    proj = _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-alpha"})
    assert res.returncode == 0, res.stderr
    assert res.stdout == textwrap.dedent("""\
        steps:
          - label: "run-gate: mutation on gate-alpha"
            command: "cd proj && ./run-gate.py mutation"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "mutation"
          - label: "run-gate: nightly-properties on gate-alpha"
            command: "cd proj && ./run-gate.py nightly-properties"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "nightly-properties"
          - label: "run-gate: selftest on gate-alpha"
            command: "cd proj && ./run-gate.py selftest"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "selftest"
        """)
    assert proj.joinpath("run-gate.py").exists()


@pytest.mark.skipif(yaml is None, reason="PyYAML not importable in this venv")
def test_pipeline_output_parses_as_the_documented_pipeline(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-alpha",
                    "RUN_GATE_LANES": "mutation"})
    assert res.returncode == 0, res.stderr
    doc = yaml.safe_load(res.stdout)
    assert list(doc) == ["steps"]
    (step,) = doc["steps"]
    assert step == {
        "label": "run-gate: mutation on gate-alpha",
        "command": "cd proj && ./run-gate.py mutation",
        "agents": {"queue": "gate-alpha"},
        "concurrency": 1,
        "concurrency_group": "gate/gate-alpha",
        "timeout_in_minutes": 300,
        "artifact_paths": ["proj/.assay/**/*", "proj/.run-gate/history.json"],
        "env": {"RUN_GATE_LANE": "mutation"},
    }


def test_pipeline_default_project_is_dot_and_emits_no_cd(tmp_path):
    proj = _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, cwd=proj, env={"RUN_GATE_QUEUE": "q1",
                                           "RUN_GATE_LANES": "selftest"})
    assert res.returncode == 0, res.stderr
    assert '    command: "./run-gate.py selftest"\n' in res.stdout
    assert '      - ".assay/**/*"\n' in res.stdout
    assert '      - ".run-gate/history.json"\n' in res.stdout
    assert "cd " not in res.stdout


def test_pipeline_selects_named_lanes_in_the_requested_order(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj/", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-beta",
                    "RUN_GATE_LANES": "  selftest   mutation "})
    assert res.returncode == 0, res.stderr
    labels = [ln.strip() for ln in res.stdout.splitlines() if "label:" in ln]
    assert labels == ['- label: "run-gate: selftest on gate-beta"',
                      '- label: "run-gate: mutation on gate-beta"']
    # the trailing slash of "proj/" is normalized away, not doubled
    assert "proj//" not in res.stdout


def test_pipeline_timeout_comes_from_the_environment(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_LANES": "selftest",
                    "RUN_GATE_TIMEOUT_MINUTES": "720"})
    assert res.returncode == 0, res.stderr
    assert "    timeout_in_minutes: 720\n" in res.stdout


def test_pipeline_refuses_a_bad_timeout(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_TIMEOUT_MINUTES": "5m"})
    assert res.returncode == 2
    assert "RUN_GATE_TIMEOUT_MINUTES='5m'" in res.stderr


def test_pipeline_refuses_an_unknown_lane_by_name(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1",
                    "RUN_GATE_LANES": "selftest nosuchlane"})
    assert res.returncode == 2
    assert "nosuchlane" in res.stderr
    assert "mutation" in res.stderr and "selftest" in res.stderr  # what it does show
    assert res.stdout == ""


def test_pipeline_refuses_a_missing_queue_by_name(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path)
    assert res.returncode == 2
    assert "RUN_GATE_QUEUE" in res.stderr
    assert res.stdout == ""


def test_pipeline_refuses_an_unquotable_queue(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": 'q" x'})
    assert res.returncode == 2
    assert "RUN_GATE_QUEUE" in res.stderr


def test_pipeline_refuses_a_listing_with_a_fourth_column(tmp_path):
    """The generator is written against name<TAB>kind<TAB>environment; a wider
    listing means the contract moved, and guessing is how a second parser is
    born."""
    _stub_project(tmp_path / "proj", listing="lint\tcommand\thost\tremote\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "three documented columns" in res.stderr


def test_pipeline_skips_a_kind_it_was_not_written_against(tmp_path):
    _stub_project(tmp_path / "proj",
                  listing="futurish\tsomethingelse\thost\nselftest\tcommand\thost\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 0, res.stderr
    assert "futurish" not in res.stdout
    assert "selftest" in res.stdout


def test_pipeline_refuses_when_no_lane_survives(tmp_path):
    _stub_project(tmp_path / "proj", listing="futurish\tsomethingelse\thost\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "nothing to run remotely" in res.stderr


def test_pipeline_refuses_a_project_without_run_gate_py(tmp_path):
    (tmp_path / "empty").mkdir()
    res = _run(PIPELINE_SH, "empty", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "run-gate.py" in res.stderr


def test_pipeline_refuses_when_the_listing_itself_fails(tmp_path):
    proj = _stub_project(tmp_path / "proj")
    (proj / "run-gate.py").write_text("#!/usr/bin/env python3\n"
                                      "raise SystemExit('broken config')\n")
    (proj / "run-gate.py").chmod(0o755)
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "--list' failed" in res.stderr


def test_pipeline_help_exits_zero_and_names_the_env_contract():
    res = _run(PIPELINE_SH, "--help")
    assert res.returncode == 0
    for key in ("RUN_GATE_QUEUE", "RUN_GATE_LANES", "RUN_GATE_TIMEOUT_MINUTES"):
        assert key in res.stdout


def test_pipeline_never_reads_run_gate_toml():
    """The anti-goal, pinned as a test: no second parser of run-gate.toml."""
    assert "run-gate.toml" not in _uncommented(PIPELINE_SH)
