"""The CIU-141 live acceptance remains a separate, opt-in registered lane."""

from __future__ import annotations

import tomllib
from pathlib import Path

CIU_ROOT = Path(__file__).resolve().parents[2]


def test_os_acceptance_is_registered_behind_the_isolated_dind_lane():
    config = tomllib.loads((CIU_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    lane = config["lanes"]["ciu-run-os-integration"]

    assert lane["kind"] == "command"
    assert lane["environment"] == "enroll-tester"
    assert lane["required_env"] == ["DOCKER_HOST", "CGROUP_PARENT_DEV_GATES"]
    command = " ".join(lane["argv"])
    assert "CIU_RUN_OS_INTEGRATION=1" in command
    assert "tests/os_integration/test_ciu_run_real_docker.py" in command
    assert "-m os_integration" in command


def test_real_docker_test_is_marked_and_requires_the_registered_lane():
    source = CIU_ROOT / "tests" / "os_integration" / "test_ciu_run_real_docker.py"
    text = source.read_text(encoding="utf-8")

    assert "@pytest.mark.os_integration" in text
    assert 'CIU_RUN_OS_INTEGRATION") != "1"' in text
    assert 'f"{project_name}-foreground"' in text
    assert '["rm", "-f", name]' in text
