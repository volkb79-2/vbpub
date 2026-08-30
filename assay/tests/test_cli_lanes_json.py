"""B044 -- ``assay lanes --json``: one machine-readable inventory document.

Golden JSON tests over the shared lane templates: a plain R0 lane, a Python
R1 lane, a JavaScript R1 lane (B036), a SQL R2 lane (P34), and a lane that
delegates its comparison base (B019). Each test asserts the FULL entry dict
so an added, dropped, or renamed key fails here rather than being noticed
only by a downstream gate consumer (CIU-72). Runs nothing and writes nothing
besides stdout, exactly like the text form (`test_cli_lanes.py`).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from conftest import R0_LANE, R1_LANE, Project, drop_key

from assay import __version__
from assay.cli import main

#: A JavaScript R1 lane in B041 pattern (a)'s shape -- offline install then
#: a `--no-install` runner -- with its base DELEGATED, the shape the first
#: consumer (dstdns) actually declares.
JS_LANE = """\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["bash", "-c", "npm ci --offline && npx --no-install vitest run --coverage"]
env = {}
env_passthrough = ["PATH", "HOME"]
budget = "15m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "javascript"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-istanbul-json", artifact = ".assay/coverage-final.json" }
base_source = "request"
"""

#: A SQL R2 lane (P34), the same shape `test_config_accept.py` proves loads.
SQL_LANE = """\
schema_version = 2

[lanes.schema]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["scripts/schema-gate.sh"]
env = {}
env_passthrough = ["PATH"]
budget = "20m"
allow_argv_append = false

[lanes.schema.isolation]
snapshot_selection = "repository"

[lanes.schema.judge]
language = "sql"
source_roots = ["src"]
base = "origin/main"

[lanes.schema.judge.mutation]
jobs = 1
max_mutants = 200
operators = ["sql:drop-check", "sql:weaken-delete-action"]
equivalence_artifact = ".assay/schema-dump.sql"
kill_signal_artifact = ".assay/kill-signal.txt"
"""


def _delegating_r1_lane() -> str:
    """`R1_LANE` with the base DELEGATED instead of declared (B019) -- the
    same one-line edit `test_config_judge_base_source.py` proves loads."""
    lane = R1_LANE.replace('base = "main"', 'base_source = "request"', 1)
    assert 'base_source = "request"' in lane and "base = " not in lane
    return lane


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def lanes_json(path: Path) -> dict:
    code, out, err = run(["lanes", "--json", "--file", str(path)])
    assert code == 0, err
    assert err == ""
    return json.loads(out)


#: The Wave-B keys every entry carries, always in this exact null/[] shape
#: until B043/B041(b)/B045 land (schema v9). Declared once so every expected
#: entry below inherits it via `| _WAVE_B_STUB` rather than repeating it.
_WAVE_B_STUB = {"cwd": None, "link_paths": []}


def test_document_envelope(project: Project):
    path = project.write(R0_LANE)

    doc = lanes_json(path)

    assert doc["inventory_schema"] == 1
    assert doc["assay_version"] == __version__
    assert list(doc.keys()) == ["assay_version", "inventory_schema", "lanes"]


def test_an_r0_only_lane(project: Project):
    path = project.write(R0_LANE)

    doc = lanes_json(path)

    assert doc["lanes"] == [
        {
            "name": "package",
            "scope": "S1",
            "rigor": ["R0"],
            "enforcement": "gate",
            "language": None,
            "rigor_reachable": [],
            "coverage": None,
            "mutation": None,
            "canary": None,
            "base_source": None,
            "external_tools": [],
            "argv0": "pytest",
            "env_required": [],
            "environment_command": False,
            "infrastructure_facts": [],
            "budget": "5m",
            "snapshot_selection": None,
            **_WAVE_B_STUB,
        }
    ]


def test_a_python_r1_lane_with_a_declared_base(project: Project):
    path = project.write(R1_LANE)

    doc = lanes_json(path)

    assert doc["lanes"] == [
        {
            "name": "package",
            "scope": "S2",
            "rigor": ["R0", "R1"],
            "enforcement": "advisory",
            "language": "python",
            "rigor_reachable": ["R1", "R2", "R3"],
            "coverage": {
                "format": "coverage-py-json",
                "artifact": "cov.json",
                "producer": None,
            },
            "mutation": None,
            "canary": None,
            # B019/A-328: absent from the file means "declared" -- resolved
            # here, not passed through as null, so a gate can tell this
            # apart from a lane with no base concept at all (A-347).
            "base_source": "declared",
            "external_tools": [],
            "argv0": "pytest",
            "env_required": [],
            "environment_command": False,
            "infrastructure_facts": [],
            "budget": "1h30m",
            "snapshot_selection": "repository",
            **_WAVE_B_STUB,
        }
    ]


def test_a_javascript_r1_lane_that_delegates_its_base(project: Project):
    path = project.write(JS_LANE)

    doc = lanes_json(path)

    assert doc["lanes"] == [
        {
            "name": "ui",
            "scope": "S1",
            "rigor": ["R0", "R1"],
            "enforcement": "gate",
            "language": "javascript",
            "rigor_reachable": ["R1"],
            "coverage": {
                "format": "coverage-istanbul-json",
                "artifact": ".assay/coverage-final.json",
                # B045/schema v9 -- not yet declarable.
                "producer": None,
            },
            "mutation": None,
            "canary": None,
            "base_source": "request",
            "external_tools": [],
            "argv0": "bash",
            "env_required": [],
            "environment_command": False,
            "infrastructure_facts": [],
            "budget": "15m",
            "snapshot_selection": "repository",
            **_WAVE_B_STUB,
        }
    ]


def test_a_sql_r2_lane(project: Project):
    path = project.write(SQL_LANE)

    doc = lanes_json(path)

    assert doc["lanes"] == [
        {
            "name": "schema",
            "scope": "S1",
            "rigor": ["R0", "R2"],
            "enforcement": "gate",
            "language": "sql",
            "rigor_reachable": ["R2"],
            # SQL has no R1, so no coverage table exists to declare.
            "coverage": None,
            "mutation": {
                "jobs": 1,
                "max_mutants": 200,
                "operators": ["sql:drop-check", "sql:weaken-delete-action"],
                "kill_signal_artifact": ".assay/kill-signal.txt",
                "equivalence_artifact": ".assay/schema-dump.sql",
            },
            "canary": None,
            "base_source": "declared",
            "external_tools": [],
            "argv0": "scripts/schema-gate.sh",
            "env_required": [],
            "environment_command": False,
            "infrastructure_facts": [],
            "budget": "20m",
            "snapshot_selection": "repository",
            **_WAVE_B_STUB,
        }
    ]


def test_a_lane_delegating_its_base_records_base_source_request(project: Project):
    path = project.write(_delegating_r1_lane())

    doc = lanes_json(path)

    assert doc["lanes"][0]["base_source"] == "request"


def test_every_declared_lane_appears_in_file_order(project: Project):
    # The two templates each open with their own `schema_version` line; keep
    # only one and concatenate the `[lanes.*]` tables that follow it.
    text = R0_LANE + "\n" + "\n".join(JS_LANE.splitlines()[2:])
    path = project.write(text)

    doc = lanes_json(path)

    assert [entry["name"] for entry in doc["lanes"]] == ["package", "ui"]


# --- refusal: exit 2, empty stdout, no partial document ---------------------


def test_a_lane_file_that_fails_to_load_exits_two_with_no_json_on_stdout(
    project: Project,
):
    path = project.write(drop_key(R0_LANE, "budget"))

    code, out, err = run(["lanes", "--json", "--file", str(path)])

    assert code == 2
    assert out == "", "no partial document on a load failure"
    assert "ERROR/BAD_LANE_CONFIG" in err


def test_a_missing_lane_file_exits_two_with_no_json_on_stdout(project: Project):
    code, out, err = run(
        ["lanes", "--json", "--file", str(project.root / "absent.toml")]
    )

    assert code == 2
    assert out == ""
    assert "absent.toml" in err


# --- it must not execute a lane, and it writes no verdict artifact ----------


def test_lanes_json_does_not_execute_the_lane_argv(project: Project, tmp_path: Path):
    marker = tmp_path / "EXECUTED"
    text = R0_LANE.replace(
        'argv = ["pytest", "tests/unit", "-q"]',
        f'argv = ["/bin/sh", "-c", "touch {marker}"]',
        1,
    )
    path = project.write(text)

    code, out, _ = run(["lanes", "--json", "--file", str(path)])

    assert code == 0
    assert json.loads(out)["lanes"][0]["argv0"] == "/bin/sh"
    assert not marker.exists()


def test_lanes_json_writes_no_verdict_artifact(project: Project):
    def snapshot(root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*")}

    path = project.write(R1_LANE)
    before = snapshot(project.root)

    run(["lanes", "--json", "--file", str(path)])

    assert snapshot(project.root) == before
