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
    if world.get("ps_rc", 0):
        sys.stderr.write("Cannot connect to the Docker daemon\\n")
        sys.exit(world["ps_rc"])
    if any("project.working_dir=" in x for x in a):
        print("\\n".join(world.get("ps_by_dir", [])))
    else:
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
    monkeypatch.setenv("INSTANCE_ID", "abc")
    monkeypatch.delenv("PWMCP_STACK_CONTAINER", raising=False)
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(tmp_path / "no-instance"))
    monkeypatch.chdir(tmp_path)

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
    assert len(ps) == 2 and all("name=^pwmcp-" in c and "compose.service=pwmcp" in c for c in ps)
    assert "project.working_dir=" in ps[1]
    inspects = [c for c in calls() if c.startswith("inspect")]
    assert inspects and all(c.split()[-1] in ("pwmcp-abc-pwmcp", ME) for c in inspects)
    assert all(c.startswith("inspect --type container ") for c in inspects)


def test_no_docker_cli_is_a_silent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    finalize.join_pwmcp_network()


def _two_stacks(set_world, **kw) -> None:
    set_world(
        ps=["pwmcp-abc-pwmcp", "pwmcp-zzz-pwmcp"],
        containers={
            "pwmcp-abc-pwmcp": {"net-abc": ["pwmcp"]},
            "pwmcp-zzz-pwmcp": {"net-zzz": ["pwmcp"]},
            ME: {"bridge": []},
        },
        **kw,
    )


def test_several_stacks_the_one_of_this_instance_is_chosen(world) -> None:
    set_world, calls = world
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    connects = [c for c in calls() if c.startswith("network connect")]
    assert connects == [f"network connect net-abc {ME}"]


def test_several_stacks_none_of_this_instance_joins_nothing_and_names_the_override(
    world, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    set_world, calls = world
    monkeypatch.setenv("INSTANCE_ID", "other")
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())
    out = capsys.readouterr().out
    assert "PWMCP_STACK_CONTAINER" in out and "pwmcp-abc-pwmcp" in out and "pwmcp-zzz-pwmcp" in out


def test_unknown_identity_joins_nothing_even_with_one_stack(
    world, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    set_world, calls = world
    monkeypatch.delenv("INSTANCE_ID")
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())
    assert "PWMCP_STACK_CONTAINER" in capsys.readouterr().out


def test_explicit_override_chooses_the_stack(world, monkeypatch: pytest.MonkeyPatch) -> None:
    set_world, calls = world
    monkeypatch.setenv("PWMCP_STACK_CONTAINER", "pwmcp-zzz-pwmcp")
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-zzz {ME}"]


def test_identity_is_read_from_the_workspace_ciu_env(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_world, calls = world
    monkeypatch.delenv("INSTANCE_ID")
    (tmp_path / "ciu.env").write_text('export REPO_NAME="x"\nexport INSTANCE_ID="zzz"\n', encoding="utf-8")
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-zzz {ME}"]


def test_the_shipped_instance_ciu_env_beats_the_workspace_instance_id(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The workspace INSTANCE_ID (abc) is another ciu instance; the instance's own file names zzz."""
    set_world, calls = world
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "ciu.env").write_text('export INSTANCE_ID="zzz"\n', encoding="utf-8")
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(inst))
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-zzz {ME}"]


def test_the_dotciu_instance_file_is_read_too(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_world, calls = world
    inst = tmp_path / "inst"
    (inst / ".ciu").mkdir(parents=True)
    (inst / ".ciu" / "ciu.env").write_text("INSTANCE_ID=zzz\n", encoding="utf-8")
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(inst))
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-zzz {ME}"]


def test_override_still_beats_the_instance_file(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_world, calls = world
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "ciu.env").write_text('INSTANCE_ID="zzz"\n', encoding="utf-8")
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(inst))
    monkeypatch.setenv("PWMCP_STACK_CONTAINER", "pwmcp-abc-pwmcp")
    _two_stacks(set_world)
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-abc {ME}"]


def test_instance_without_a_file_is_found_by_its_compose_working_dir_label(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live case: no ciu.env in the instance dir and the workspace id (abc) matches no stack."""
    set_world, calls = world
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(tmp_path / "no-such-instance"))
    _two_stacks(set_world, ps_by_dir=["pwmcp-zzz-pwmcp"])
    finalize.join_pwmcp_network()
    assert [c for c in calls() if c.startswith("network connect")] == [f"network connect net-zzz {ME}"]


def test_a_label_match_not_among_the_stacks_is_ignored(
    world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_world, calls = world
    monkeypatch.setenv("PWMCP_INSTANCE_DIR", str(tmp_path / "no-such-instance"))
    monkeypatch.setenv("INSTANCE_ID", "other")
    _two_stacks(set_world, ps_by_dir=["pwmcp-ghost-pwmcp", "pwmcp-zzz-pwmcp"])
    finalize.join_pwmcp_network()
    assert not any(c.startswith("network connect") for c in calls())


def test_a_failing_docker_ps_warns_with_the_error_not_no_stack(world, capsys) -> None:
    set_world, calls = world
    set_world(ps_rc=1, containers={ME: {"bridge": []}})
    finalize.join_pwmcp_network()
    cap = capsys.readouterr()
    assert "docker ps failed" in cap.err and "Cannot connect to the Docker daemon" in cap.err
    assert "ciu up" not in cap.out + cap.err
    assert not any(c.startswith("network connect") for c in calls())
