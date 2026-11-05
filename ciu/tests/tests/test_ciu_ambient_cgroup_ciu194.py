"""CIU-194 — governance tests must not depend on the runner's cgroup env.

A gate container only has ``CGROUP_PARENT_DEV_BACKGROUND`` when the launcher
forwards it. Four tests reach ``governance.resolve_cgroup_parent`` without
configuring a parent and passed only because an earlier test on the same xdist
worker had leaked the variable into ``os.environ`` (CIU-192 stopped the leak).
The suite now pins the variable per test; this runs two of them in a child
pytest whose environment lacks it, which failed deterministically before.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CIU_ROOT = Path(__file__).resolve().parents[2]

_NODEIDS = (
    "tests/tests/test_ciu_release_gate_coverage.py::"
    "test_builtin_ksm_overlay_builds_once_then_uses_a_daemon_visible_path",
    "tests/tests/test_spec_contracts.py::TestVaultBackedFlows::"
    "test_gen_to_vault_generates_once_across_reruns",
)


def test_governance_tests_pass_without_ambient_cgroup_parent():
    env = {k: v for k, v in os.environ.items() if k != "CGROUP_PARENT_DEV_BACKGROUND"}
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(CIU_ROOT / "src"), env.get("PYTHONPATH", "")])
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", "-p", "no:cov", "-o", "addopts=", "--cli-case-partial", *_NODEIDS],
        cwd=CIU_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
