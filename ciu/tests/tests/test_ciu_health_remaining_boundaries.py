"""Remaining behavioral boundaries for CIU health/readiness (S7.7, S9.3)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_cmd_shell_with_unclosed_quote_probes_no_invented_executable() -> None:
    """Malformed shell source cannot create a spurious preflight warning."""
    assert health._parse_cmd_shell_tools("curl -fsS 'unterminated") == []


def test_extract_healthcheck_tools_keeps_only_real_healthcheck_executables(tmp_path: Path) -> None:
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """\
services:
  api:
    image: example/api:1
    healthcheck:
      test: [CMD-SHELL, 'TOKEN=x curl -fsS http://localhost/health && exit 0']
  direct:
    image: example/direct:1
    healthcheck:
      test: [CMD, /usr/local/bin/check-ready, --quiet]
  disabled:
    image: example/disabled:1
    healthcheck:
      test: [NONE]
  incomplete:
    healthcheck:
      test: [CMD, curl]
""",
        encoding="utf-8",
    )

    assert health.extract_healthcheck_tools(compose) == {
        "api": ("example/api:1", ["curl"]),
        "direct": ("example/direct:1", ["check-ready"]),
    }


@pytest.mark.parametrize(
    "contents",
    [
        "- not-a-compose-model\n",
        "services: []\n",
        "services:\n  api: not-a-service-mapping\n",
    ],
)
def test_malformed_compose_model_is_a_preflight_configuration_error(tmp_path: Path, contents: str) -> None:
    """Malformed Compose cannot become a misleading green preflight result."""
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[S7\.7\].*must be a mapping"):
        health.extract_healthcheck_tools(compose)


def test_probe_image_tools_uses_declared_entrypoint_when_shell_probe_is_unavailable(monkeypatch) -> None:
    """A distroless direct-CMD health executable remains available without sh."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout='{"Entrypoint":["/bin/check-ready"],"Cmd":null}',
                stderr="",
            )
        raise FileNotFoundError("docker unavailable for shell probe")

    monkeypatch.setattr(health._subprocess, "run", fake_run)

    assert health.probe_image_tools("example/distroless", ["check-ready", "curl"]) == {
        "check-ready": True,
        "curl": False,
    }
    assert calls[0][1:3] == ["image", "inspect"]


def test_preflight_reports_missing_tool_but_not_cached_available_tool(monkeypatch, tmp_path: Path) -> None:
    """Strict callers can fail on the returned real missing executable warning."""
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    first.write_text(
        "services:\n  api:\n    image: example/api:1\n    healthcheck:\n      test: [CMD, curl]\n",
        encoding="utf-8",
    )
    second.write_text(
        "services:\n  worker:\n    image: example/api:1\n    healthcheck:\n      test: [CMD, wget]\n",
        encoding="utf-8",
    )
    probed: list[tuple[str, tuple[str, ...]]] = []

    def fake_probe(image: str, tools: list[str]) -> dict[str, bool]:
        probed.append((image, tuple(tools)))
        return {tool: tool == "curl" for tool in tools}

    monkeypatch.setattr(health, "probe_image_tools", fake_probe)
    warnings: list[str] = []
    info: list[str] = []

    result = health.preflight_probe([first, second], warn_fn=warnings.append, info_fn=info.append)

    assert result == warnings
    assert len(result) == 1
    assert "worker" in result[0] and "wget" in result[0]
    assert all("curl" not in warning for warning in result)
    assert probed == [("example/api:1", ("curl",)), ("example/api:1", ("wget",))]
    assert any("api" in message for message in info)


def test_wait_tcp_retries_refusal_then_returns_ready_without_wall_clock() -> None:
    """A transient refusal is not green until the injected connection succeeds."""
    outcomes = iter([OSError("refused"), True])
    sleeps: list[float] = []
    clock_values = iter([10.0, 10.0])

    def connect(_host: str, _port: int) -> object:
        outcome = next(outcomes)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    assert health.wait_tcp(
        "cache.internal",
        6379,
        timeout_s=5,
        interval_s=0.5,
        connect_fn=connect,
        sleep_fn=sleeps.append,
        clock=lambda: next(clock_values),
    ) is True
    assert sleeps == [0.5]


def test_wait_healthy_retries_a_nonready_status_before_reporting_ready() -> None:
    """A hook does not proceed during a starting health state."""
    statuses = iter(["starting", "healthy"])
    sleeps: list[float] = []
    clock_values = iter([2.0, 2.0])

    assert health.wait_healthy(
        lambda: next(statuses),
        timeout_s=5,
        interval_s=1,
        sleep_fn=sleeps.append,
        clock=lambda: next(clock_values),
    ) is True
    assert sleeps == [1]
