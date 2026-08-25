"""Deploy health inspection failure boundary.

Docker may return stale stdout alongside a non-zero inspect status (for example
after a container disappears), and a daemon/proxy may return non-JSON output.
Neither may be interpreted as a healthy container by CIU's public health gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def test_health_gate_fails_closed_for_failed_or_malformed_docker_inspect(monkeypatch) -> None:
    """A requested health gate stays red when Docker cannot supply trustworthy state."""
    calls: list[list[str]] = []

    def docker(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "project-prod-gone":
            # A non-zero inspect must win over stale JSON that claims health.
            return subprocess.CompletedProcess(argv, 1, '{"Health": {"Status": "healthy"}}', "No such object")
        return subprocess.CompletedProcess(argv, 0, "not JSON", "")

    monkeypatch.setattr(deploy.procutil, "docker", docker)

    passed, summary = deploy.run_container_health_gate(
        {"project-prod-gone": 0, "project-prod-proxy-error": 0}, interval_s=0
    )

    assert passed is False
    assert summary["not_found"] == ["project-prod-gone", "project-prod-proxy-error"]
    assert calls == [
        ["inspect", "--format", "{{json .State}}", "project-prod-gone"],
        ["inspect", "--format", "{{json .State}}", "project-prod-proxy-error"],
    ]
