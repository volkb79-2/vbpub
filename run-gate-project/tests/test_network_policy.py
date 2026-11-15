"""RG-256 lane-network validation, translation, and documentation contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

RUN_GATE_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "run_gate_network_policy", RUN_GATE_DIR / "run_gate.py")
assert _SPEC is not None and _SPEC.loader is not None
run_gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_gate
_SPEC.loader.exec_module(run_gate)


@pytest.mark.parametrize("value", ["bridge", "host", "", None, True, 0])
def test_lane_network_vocabulary_is_closed(value: object) -> None:
    with pytest.raises(run_gate.GateError, match="network.*none.*default"):
        run_gate._validate_lane(
            "suite", {"kind": "command", "environment": "runner",
                      "argv": ["true"], "network": value}, "fixture")


@pytest.mark.parametrize("value", ["none", "default"])
def test_lane_network_accepts_each_declared_value(value: str) -> None:
    run_gate._validate_lane(
        "suite", {"kind": "command", "environment": "runner",
                  "argv": ["true"], "network": value}, "fixture")


@pytest.mark.parametrize("value", ["bridge", "host", "", None, True, 0])
def test_environment_network_vocabulary_is_closed(value: object) -> None:
    with pytest.raises(run_gate.GateError, match="network.*none.*default"):
        run_gate._validate_environment(
            "runner", {"mode": "ephemeral", "image": "runner:local",
                      "network": value}, "fixture")


@pytest.mark.parametrize("value", ["none", "default"])
def test_ephemeral_environment_accepts_each_network_value(value: str) -> None:
    run_gate._validate_environment(
        "runner", {"mode": "ephemeral", "image": "runner:local",
                  "network": value}, "fixture")


@pytest.mark.parametrize("value", ["none", "default"])
def test_environment_network_key_requires_ephemeral_mode(value: str) -> None:
    for mode, image in (("exec", "runner:local"), ("host", None)):
        environment = {"mode": mode, "network": value}
        if image is not None:
            environment["image"] = image
        with pytest.raises(run_gate.GateError,
                           match="network.*only with mode = 'ephemeral'"):
            run_gate._validate_environment("runner", environment, "fixture")


@pytest.mark.parametrize(
    ("mode", "network"),
    [("exec", "none"), ("exec", "default"),
     ("host", "none"), ("host", "default")],
)
def test_lane_network_key_refuses_modes_that_cannot_honor_it(
        mode: str, network: str) -> None:
    environment = {"mode": mode}
    if mode == "exec":
        environment["image"] = "runner:local"
    lane = {"kind": "command", "environment": "runner", "argv": ["true"],
            "network": network}
    project = {"environments": {"runner": environment}}
    with pytest.raises(run_gate.GateError,
                       match="network.*cannot be honored.*ephemeral"):
        run_gate.resolve_environment(
            lane, "suite", project, {}, Path("/project/run-gate.toml"), None)


@pytest.mark.parametrize(("mode", "network"),
                         [("exec", "none"), ("host", "default")])
def test_config_load_refuses_lane_network_on_non_ephemeral_modes(
        tmp_path, mode: str, network: str) -> None:
    image = 'image = "runner:local"\n' if mode == "exec" else ""
    config = (
        "schema_version = 1\n"
        "[environments.runner]\n"
        f'mode = "{mode}"\n'
        f"{image}"
        "[lanes.suite]\n"
        'kind = "command"\n'
        'environment = "runner"\n'
        'argv = ["true"]\n'
        f'network = "{network}"\n'
    )
    (tmp_path / "run-gate.toml").write_text(config, encoding="utf-8")
    with pytest.raises(run_gate.GateError,
                       match="network.*cannot be honored.*ephemeral"):
        run_gate.load_config(tmp_path)


def test_lane_network_setting_is_valid_for_ephemeral_mode() -> None:
    lane = {"kind": "command", "environment": "runner", "argv": ["true"],
            "network": "none"}
    project = {"environments": {
        "runner": {"mode": "ephemeral", "image": "runner:local"}}}
    environment, source = run_gate.resolve_environment(
        lane, "suite", project, {}, Path("/project/run-gate.toml"), None)
    assert environment["mode"] == "ephemeral"
    assert source.startswith("[environments.runner]")


def test_environment_network_setting_is_retained_for_lane_resolution() -> None:
    lane = {"kind": "command", "environment": "runner", "argv": ["true"]}
    project = {"environments": {
        "runner": {"mode": "ephemeral", "image": "runner:local",
                   "network": "none"}}}
    environment, _source = run_gate.resolve_environment(
        lane, "suite", project, {}, Path("/project/run-gate.toml"), None)
    assert environment["network"] == "none"


def test_effective_network_validation_uses_assay_environment_default() -> None:
    project = {
        "assay": {"environment": "runner"},
        "environments": {
            "runner": {"mode": "ephemeral", "image": "runner:local"}},
        "lanes": {"unit": {"kind": "assay", "network": "none"}},
    }
    run_gate._validate_effective_network_settings(
        project, {}, Path("/project/run-gate.toml"))


def test_effective_network_validation_defers_unresolved_environment_errors() -> None:
    project = {"lanes": {
        "no-name": {"kind": "assay", "network": "none"},
        "missing": {"kind": "command", "environment": "missing",
                    "network": "none"},
    }}
    run_gate._validate_effective_network_settings(
        project, {}, Path("/project/run-gate.toml"))


def _run_container(monkeypatch, tmp_path: Path, lane: dict, environment: dict,
                   *, dry_run: bool = False) -> list[list[str]]:
    calls: list[list[str]] = []

    def docker_run(argv: list[str], **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "container-id\n", "")

    monkeypatch.setattr(run_gate.shutil, "which", lambda _name: "/bin/docker")
    monkeypatch.setattr(run_gate, "resolve_inflight", lambda *_a, **_k: None)
    monkeypatch.setattr(run_gate, "container_mount_flags",
                        lambda *_a, **_k: [])
    monkeypatch.setattr(run_gate, "extra_mount_flags", lambda: [])
    monkeypatch.setattr(run_gate, "cowtree_mount_flags", lambda *_a: [])
    monkeypatch.setattr(
        run_gate, "resource_observer_docker_flags",
        lambda lane, _env: (["--mount", "type=bind,source=/cg.sock,"
                             "target=/cg.sock,readonly"]
                            if lane.get("resource_observer") == "cgprofile"
                            else []))
    monkeypatch.setattr(run_gate, "resource_observer_version_preflight",
                        lambda *_a: None)
    monkeypatch.setattr(run_gate, "control_socket_from_env",
                        lambda: "/cg-control.sock")
    monkeypatch.setattr(run_gate, "verify_slice_loaded", lambda *_a: None)
    monkeypatch.setattr(run_gate, "build_command_inner", lambda *_a: "true")
    monkeypatch.setattr(run_gate, "build_assay_inner", lambda *_a, **_k: "true")
    monkeypatch.setattr(run_gate, "assure_assay_state_root",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(run_gate, "physical_path", lambda path, **_k: Path(path))
    monkeypatch.setattr(run_gate, "head_commit", lambda *_a: "a" * 40)
    monkeypatch.setattr(run_gate, "lane_container_identity",
                        lambda *_a: {"name": "lane-test", "labels": {}})
    monkeypatch.setattr(run_gate, "clear_previous_assay_verdict",
                        lambda *_a: None)
    monkeypatch.setattr(run_gate, "write_inflight_record",
                        lambda *_a, **_k: True)
    monkeypatch.setattr(run_gate, "await_container", lambda *_a, **_k: 0)
    monkeypatch.setattr(run_gate, "make_progress_watch", lambda *_a, **_k: None)
    monkeypatch.setattr(run_gate, "boot_id", lambda: "boot")
    monkeypatch.setattr(run_gate, "process_start_ticks", lambda _pid: 1)
    monkeypatch.setattr(run_gate, "pid_ns_inode", lambda: "pid-ns")
    monkeypatch.setattr(run_gate, "print_lane_bounds", lambda *_a: None)
    monkeypatch.setattr(run_gate, "log_forwarded_env", lambda *_a: None)
    monkeypatch.setattr(run_gate, "log_resource_forwarded_env", lambda *_a: None)
    monkeypatch.setattr(run_gate.subprocess, "run", docker_run)

    result = run_gate.run_container_lane(
        lane, "suite", tmp_path, tmp_path, tmp_path,
        {"mode": "ephemeral", "image": "tester:local", **environment},
        "fixture environment", "dev-gates.slice", "fixture slice",
        dry_run=dry_run,
        profile_plan={"enabled": False, "disabled_reason": "test",
                      "source": "test fixture"})
    assert result == 0
    return calls


def test_rg256_network_policy_does_not_reach_ephemeral_probe_argv(
        monkeypatch, tmp_path: Path) -> None:
    environment = {"mode": "ephemeral", "image": "tester:local",
                   "network": "none"}
    monkeypatch.setattr(run_gate, "validate_resource_mount_inputs",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_gate, "extra_mount_flags", lambda: [])
    monkeypatch.setattr(run_gate, "container_mount_flags",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(run_gate, "cowtree_mount_flags", lambda *_args: [])
    monkeypatch.setattr(run_gate, "resource_forwarded_env", lambda *_args: {})

    argv = run_gate.build_env_probe_argv(
        "/bin/docker", environment, "runner", tmp_path, tmp_path,
        "fixture environment", "dev-gates.slice", "true",
        inspect_mounts=False)

    assert argv[:2] == ["/bin/docker", "run"]
    assert not any(arg == "--network" or arg.startswith("--network=")
                   for arg in argv)


@pytest.mark.parametrize(
    ("environment_network", "lane_network", "expected"),
    [(None, None, None),
     ("none", None, "none"),
     ("none", "default", None),
     ("default", "none", "none"),
     ("default", None, None)],
)
def test_rg256_network_reaches_only_the_effective_lane_container_argv(
        monkeypatch, tmp_path, capsys, environment_network, lane_network,
        expected) -> None:
    lane = {"kind": "command", "environment": "runner", "argv": ["true"]}
    environment = {}
    if lane_network is not None:
        lane["network"] = lane_network
    if environment_network is not None:
        environment["network"] = environment_network

    calls = _run_container(monkeypatch, tmp_path, lane, environment)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[1:2] == ["run"]
    assert argv.count("--network") == (1 if expected == "none" else 0)
    image_index = argv.index("tester:local")
    if expected == "none":
        flag_index = argv.index("--network")
        assert argv[flag_index:flag_index + 2] == ["--network", "none"]
        assert flag_index < image_index
    output = capsys.readouterr().out
    source = ("lane" if lane_network is not None else
              "environment" if environment_network is not None else "unset")
    selected = expected or "default"
    assert f"effective network: {selected} (source: {source})" in output


def test_rg256_dry_run_keeps_cgprofile_socket_mount_with_network_none(
        monkeypatch, tmp_path, capsys) -> None:
    lane = {"kind": "assay", "environment": "runner", "assay_lane": "r1",
            "network": "none", "resource_observer": "cgprofile"}
    assert _run_container(monkeypatch, tmp_path, lane, {}, dry_run=True) == []
    output = capsys.readouterr().out
    assert "effective network: none (source: lane)" in output
    line = next(line for line in output.splitlines()
                if "run-gate: docker argv:" in line)
    argv = shlex.split(line.split("run-gate: docker argv:", 1)[1].strip())
    assert argv.count("--network") == 1
    assert argv[argv.index("--network"):argv.index("--network") + 2] == [
        "--network", "none"]
    assert "type=bind,source=/cg.sock,target=/cg.sock,readonly" in argv


def test_rg256_network_contract_stays_synchronized_in_user_docs() -> None:
    docs = {
        "SPEC.md": ("R-07", "R-08", "R-15", '"none"', '"default"',
                    "lane's own `network` wins", "--network none",
                    "admission and observer probes"),
        "CONSUMERS.md": ("Lane network mode", 'network = "none"',
                         'network = "default"', "lane value wins",
                         "mode = \"ephemeral\"", "bind-mounted Unix socket",
                         '`"none"` adds exactly `--network none` to the disposable'),
        "LANE-AUTHORING.md": ('network = "none"', 'network = "default"',
                              "lane wins", "bind-mounted Unix socket",
                              '`"none"` asks Run-Gate to start that lane container with\n'
                              '  `--network none`'),
        "README.md": ('network = "none"', "CONSUMERS.md#lane-network-mode",
                      "DESIGN-GUIDE.md#per-lane-network-boundaries-belong-on-the-ephemeral-lane-container"),
        "docs/DESIGN-GUIDE.md": ("Per-lane network boundaries belong on the ephemeral lane container",
                                 '"none"', '"default"',
                                 "Admission and observer",
                                 "probes keep their own network behavior",
                                 "Unix socket mounted into the lane"),
        # CMRU folds [Unreleased] into the newest dated section at release
        # preparation, so keep the changelog contract check section-agnostic.
        "CHANGES.md": ('network = "none"', 'network = "default"'),
    }
    for relative, required in docs.items():
        text = (RUN_GATE_DIR / relative).read_text(encoding="utf-8")
        missing = [fragment for fragment in required if fragment not in text]
        assert not missing, f"{relative} lacks RG-256 contract text: {missing}"
