"""Tests for the finalizer's PWMCP network join (a fake docker CLI on a temporary PATH).

No real docker runs: `docker` is a script in a temporary directory that is the only PATH
entry. It serves canned `ps`/`inspect` answers from a JSON world file and appends every
invocation to a log, so `network connect` is only ever recorded, never executed.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import finalize_container_environment as finalize  # noqa: E402

FAKE_DOCKER = f"""#!{sys.executable}
import json, os, sys
world = json.load(open(os.environ["FAKE_WORLD"]))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
a = sys.argv[1:]
if a[0] == "ps":
    print("\\n".join(world.get("ps", [])))
elif a[0] == "inspect":
    nets = world["containers"].get(a[-1])
    if nets is None:
        sys.stderr.write("No such object\\n")
        sys.exit(1)
    print(json.dumps({{n: {{"Aliases": al}} for n, al in nets.items()}}))
elif a[:2] == ["network", "connect"]:
    if world.get("connect_rc", 0):
        sys.stderr.write("boom: denied\\n")
    sys.exit(world.get("connect_rc", 0))
else:
    sys.exit(2)
"""

ME = "mdt-cockpit-test"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    world_file = tmp_path / "world.json"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_WORLD", str(world_file))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("HOSTNAME", ME)

    def set_world(**kw) -> None:
        world_file.write_text(json.dumps(kw), encoding="utf-8")

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    return set_world, calls


def test_a_running_stack_on_another_network_gets_this_container_connected(world, capsys) -> None:
    set_world, calls = world
    set_world(
        ps=["pwmcp-abc-pwmcp"],
        containers={
            "pwmcp-abc-pwmcp": {"proj-abc-network": ["pwmcp-abc-pwmcp", "pwmcp", "pwmcp-mcp"]},
            ME: {"bridge": []},
        },
    )
    finalize.join_pwmcp_network()
    assert f"network connect proj-abc-network {ME}" in calls()
    assert "joined network proj-abc-network" in capsys.readouterr().out


def test_already_attached_is_idempotent(world) -> None:
    set_world, calls = world
    set_world(
        ps=["pwmcp-abc-pwmcp"],
        containers={
            "pwmcp-abc-pwmcp": {"proj-abc-network": ["pwmcp"]},
            ME: {"bridge": [], "proj-abc-network": []},
        },
    )
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())


def test_no_running_stack_logs_how_to_start_and_connects_nothing(world, capsys) -> None:
    set_world, calls = world
    set_world(ps=[], containers={ME: {"bridge": []}})
    finalize.join_pwmcp_network()
    out = capsys.readouterr().out
    assert "ciu up" in out
    assert not any(c.startswith("network connect") for c in calls())


def test_a_network_without_the_pwmcp_alias_is_not_joined(world) -> None:
    set_world, calls = world
    set_world(
        ps=["pwmcp-abc-pwmcp"],
        containers={"pwmcp-abc-pwmcp": {"other-net": ["pwmcp-abc-pwmcp"]}, ME: {"bridge": []}},
    )
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())


def test_a_failed_connect_warns_and_does_not_raise(world, capsys) -> None:
    set_world, calls = world
    set_world(
        ps=["pwmcp-abc-pwmcp"],
        containers={"pwmcp-abc-pwmcp": {"n1": ["pwmcp"]}, ME: {"bridge": []}},
        connect_rc=1,
    )
    finalize.join_pwmcp_network()
    assert "network connect n1 failed: boom: denied" in capsys.readouterr().err


def test_unknown_self_is_skipped_without_connecting(world) -> None:
    set_world, calls = world
    set_world(ps=["pwmcp-abc-pwmcp"], containers={"pwmcp-abc-pwmcp": {"n1": ["pwmcp"]}})
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())


def test_the_queries_are_filtered_and_by_exact_name(world) -> None:
    set_world, calls = world
    set_world(ps=["pwmcp-abc-pwmcp"], containers={"pwmcp-abc-pwmcp": {"n1": ["pwmcp"]}, ME: {"n1": []}})
    finalize.join_pwmcp_network()
    ps = [c for c in calls() if c.startswith("ps")]
    assert len(ps) == 1 and "name=^pwmcp-" in ps[0] and "compose.service=pwmcp" in ps[0]
    assert all(c.split()[-1] in ("pwmcp-abc-pwmcp", ME) for c in calls() if c.startswith("inspect"))


def test_no_docker_cli_is_a_silent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    finalize.join_pwmcp_network()
