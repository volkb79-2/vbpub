"""Focused coverage for probe_ref stack dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.provisioning import probe_ref  # noqa: E402


def test_probe_ref_stack_healthy_dispatches_to_stack_probe(tmp_path):
    def docker_exec_fn(container, cmd):
        if cmd == ["inspect"]:
            return 0, "service state: healthy"
        return 1, ""

    result = probe_ref(
        "stack:api:healthy",
        config={},
        repo_root=tmp_path,
        docker_exec_fn=docker_exec_fn,
    )

    assert result.satisfied is True
    assert result.reason == "Stack 'api' is healthy"
