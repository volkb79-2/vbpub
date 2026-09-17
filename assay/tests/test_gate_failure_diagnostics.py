"""A failed self-hosted gate consumes evidence without replaying the job."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("analysis_exit", [0, 1])
def test_red_lane_stays_red_when_diagnostic_inspection_succeeds_or_fails(tmp_path, analysis_exit):
    driver = Path(__file__).resolve().parents[1] / "tools" / "tester-unified-gate.sh"
    source = driver.read_text()
    start = source.index("run_self_hosted_lane() {")
    end = source.index("\n}\n", start) + 3
    function = source[start:end]
    root = tmp_path / "worktree"
    (root / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scripts = scratch / "run-venv" / "bin"
    scripts.mkdir(parents=True)
    calls = tmp_path / "calls.jsonl"
    program = f"""#!{sys.executable}
import json, os, sys
with open(os.environ['DIAGNOSTIC_CALLS'], 'a') as stream:
    stream.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')
name = os.path.basename(sys.argv[0])
if name == 'assay':
    if sys.argv[1] == 'run':
        sys.exit(1)
    print('captured real failure diagnosis')
    sys.exit({analysis_exit})
if name == 'git':
    if 'rev-parse' in sys.argv:
        print('a' * 40)
    else:
        print('dirty-source.txt')
    sys.exit(0)
sys.exit(90)
"""
    for name in ("assay", "git", "python"):
        script = scripts / name
        script.write_text(program)
        script.chmod(0o755)
    env = os.environ.copy()
    env["DIAGNOSTIC_CALLS"] = str(calls)
    proc = subprocess.run(
        ["bash", "-c", function + '\nrun_self_hosted_lane "$1" "$2" "$3" "$4"',
         "diagnostic-test", str(root), str(scratch), "test-version", "test-wheel"],
        capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 1
    assert "captured real failure diagnosis" in proc.stderr
    assert "dirty-source.txt" in proc.stderr
    assert "ASSAY_GATE_PHASE=" not in proc.stdout + proc.stderr
    assert ("captured-verdict-unavailable-or-invalid" in proc.stderr) == (analysis_exit != 0)
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [call[1] for call in invocations if call[0] == "assay"] == ["run", "analyze"]
    assert ["assay", "analyze", "verdict", str(scratch / "verdict.json"),
            "--expected-commit", "a" * 40, "--format", "text"] in invocations
    assert not any(call[0] == "python" for call in invocations)
