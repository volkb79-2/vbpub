"""Final behavioral contracts for small CMRU operational modules."""
from __future__ import annotations

import io
import argparse
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import bundle, dependencies, handlers, manifest, output, runner, tester_gate, transaction
from cmru.agent import adapter, protocol, selfupdate, state
from cmru.controller import planner


def test_output_stream_reconfiguration_flush_and_proxy_contract():
    stream = io.StringIO()
    wrapped = output.SeverityStream(stream, time_short=False, colour=False)
    wrapped.configure(time_short=True, colour=True)
    assert wrapped._time_short is True
    assert wrapped._colour is True
    assert wrapped.write("[INFO]") == len("[INFO]")
    wrapped.flush()
    assert "[INFO]" in stream.getvalue()
    assert wrapped.getvalue() == stream.getvalue()
    original_stdout, original_stderr = output.sys.stdout, output.sys.stderr
    try:
        output.sys.stdout = wrapped
        output.sys.stderr = wrapped
        output.configure(time_short=False)
        assert wrapped._time_short is False
    finally:
        output.sys.stdout, output.sys.stderr = original_stdout, original_stderr


def test_dependencies_reports_absolute_manifest_source_when_outside_repo(tmp_path):
    provider = tmp_path / "provider"
    consumer = tmp_path / "consumer"
    provider.mkdir(); consumer.mkdir()
    wheels = consumer / "pip" / "wheels.list"
    wheels.parent.mkdir()
    wheels.write_text("provider\n", encoding="utf-8")
    projects = {
        "provider": SimpleNamespace(project_root=provider, scm_dist="provider"),
        "consumer": SimpleNamespace(project_root=consumer, scm_dist="consumer"),
    }
    report = dependencies.build_report(
        repo_root=tmp_path / "different-root",
        project_order=["provider", "consumer"],
        declared={"consumer": ["provider"]},
        projects=projects,
    )
    assert report.ok
    assert report.edges[-1].source == str(wheels)


def test_selfupdate_replaces_stale_link_atomically_in_dry_run(tmp_path):
    venv = tmp_path / "venv-2"
    venv.mkdir()
    current = tmp_path / "venv-current"
    current.symlink_to(tmp_path / "old")
    stale = tmp_path / "venv-current.new"
    stale.symlink_to(tmp_path / "stale")
    selfupdate.handoff_via_systemd("2", venv, dry_run=True)
    assert current.is_symlink()
    assert current.resolve() == venv
    assert not stale.exists()


def test_controller_cli_module_entrypoint_exposes_help():
    with patch("sys.argv", ["cmru-controller", "--help"]):
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(__import__("cmru.controller.cli", fromlist=["__file__"]).__file__, run_name="__main__")
    assert raised.value.code == 0


def test_adapter_loader_reports_unloadable_spec(tmp_path):
    adapter_file = tmp_path / "adapter.py"
    adapter_file.write_text("class Adapter: pass\n", encoding="utf-8")
    with patch.object(adapter.importlib.util, "spec_from_file_location", return_value=None):
        with pytest.raises(RuntimeError, match="Cannot load adapter"):
            adapter.load_adapter(tmp_path)


def test_protocol_manifest_state_and_plan_validation_refuse_bad_shapes(tmp_path, monkeypatch):
    with pytest.raises(protocol.DesiredStateError, match="JSON object"):
        protocol.validate_desired([])
    with pytest.raises(TypeError, match="images must be a dict"):
        manifest._validate_images([], "demo")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state.read_identity("user") is None
    with pytest.raises(ValueError, match=r"\[plan\] section required"):
        planner.load_plan_json("{}")
    empty_nodes = {
        "plan": {
            "id": "p", "landscape": "l", "release_tag": "r",
            "manifest_url": "u", "manifest_sha256": "h",
            "waves": [{"phase": 1, "name": "w", "nodes": []}],
        }
    }
    with pytest.raises(ValueError, match="nodes must be a non-empty list"):
        planner.load_plan_json(json.dumps(empty_nodes))
    empty_profile = empty_nodes["plan"].copy()
    empty_profile["waves"] = [{"phase": 1, "name": "w", "nodes": ["n"], "profiles": [""]}]
    with pytest.raises(ValueError, match="profiles entries"):
        planner.load_plan_json(json.dumps({"plan": empty_profile}))


def test_bundle_wheel_build_includes_declared_find_links(tmp_path, monkeypatch):
    config = bundle.BundleConfig(
        project_root=tmp_path,
        wheel_project_root=tmp_path,
        dist_dir=tmp_path / "dist",
        bundle_dir=tmp_path / "bundle",
        client_dir=tmp_path / "client",
        wheel_enabled=True,
        wheel_python_bin="python3",
        wheel_find_links=tmp_path / "links",
        archive_template="x-{version}.tar",
        archive_version_env="VERSION",
        archive_format="tar",
        copy_files=[],
        copy_dirs=[],
    )
    calls = []
    monkeypatch.setattr(bundle.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    bundle.build_wheel(config)
    assert "--find-links" in calls[0][0]
    assert str(config.wheel_find_links) in calls[0][0]
    no_links = config.__class__(**{**config.__dict__, "wheel_find_links": None})
    calls.clear()
    bundle.build_wheel(no_links)
    assert "--find-links" not in calls[0][0]


def test_handlers_and_tester_gate_choose_longest_mount_and_strip_separator(monkeypatch):
    mountinfo = (
        "10 1 0:1 /host /cockpit rw - bind ext4 /dev\n"
        "11 1 0:1 /host/project /cockpit/project rw - bind ext4 /dev\n"
        "12 1 0:1 /host /cockpit rw - bind ext4 /dev\n"
    )
    with patch.object(handlers.Path, "read_text", return_value=mountinfo):
        assert handlers._host_bind_source(Path("/cockpit/project/src")) == "/host/project/src"
    with patch.object(handlers.Path, "read_text", return_value="10 1 0:1 /host /unrelated rw - bind ext4 /dev\n"):
        with pytest.raises(RuntimeError, match="no matching mount"):
            handlers._host_bind_source(Path("/cockpit/project/src"))
    assert tester_gate._physical_path(Path("/cockpit/project/src"), mountinfo) == Path("/host/project/src")
    assert tester_gate._physical_path(Path("/outside"), mountinfo) == Path("/outside")
    monkeypatch.setenv("GITHUB_USERNAME", "alice")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")

    with patch.object(handlers, "GitHubReleases", lambda *args: object()), \
         patch.object(handlers, "validate_latest_release", return_value={
             "version": "1.0", "asset": "demo.whl", "url": "https://example/demo.whl",
         }):
        handlers.cmd_wheel_validate(SimpleNamespace(prefix="demo"))

    wheel = Path("demo.whl")
    with patch.object(handlers, "GitHubReleases", lambda *args: object()), \
         patch.object(handlers, "find_built_wheel", return_value=wheel), \
         patch.object(handlers, "read_wheel_version", return_value="1.0"), \
         patch.object(handlers, "publish_versioned", return_value={"sha256": "abc"}):
        handlers.cmd_wheel_publish(SimpleNamespace(
            prefix="demo", cwd=".", glob="*.whl", notes_env=None, extra_asset=[],
        ))


def test_runner_metadata_evidence_clean_dir_and_unset_bake_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILD_DATE", "already-set")
    monkeypatch.setattr(runner, "apply_reproducible_env", lambda _root: None)
    runner.compute_build_date({"build_metadata": {"date_env": "BUILD_DATE"}}, tmp_path)
    assert runner._success_evidence(["Ran 1 test in 0.1s", "OK"]) == "Ran 1 test in 0.1s; OK"
    assert runner._success_evidence(["Ran 1 test in 0.1s", "not OK"]) is None

    captured = []
    monkeypatch.setattr(
        runner,
        "run_command",
        lambda argv, cwd, handle, **kwargs: captured.append(argv) or runner.CommandResult(0.1, None),
    )
    step = runner.StepConfig(
        "build", [{"label": "echo", "argv": ["echo", "ok"], "cwd": "."}],
        "prefix-", ["UNSET_BAKE"], None, ["missing-dir"], [], None, {}, None, [], True,
    )
    monkeypatch.delenv("UNSET_BAKE", raising=False)
    runner._execute_step(step, tmp_path, tmp_path / "logs")
    assert captured == [["echo", "ok"]]


def test_tester_gate_public_cli_strips_separator_command(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda *_: (True, "ok"))
    commands = []
    monkeypatch.setattr(tester_gate, "build_docker_command", lambda *args, **kwargs: commands.append(args[2]) or ["true"])
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_: (tmp_path, "."))
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    parsed = SimpleNamespace(
        cwd=".", image="img", cgroup_parent="slice", cgroup_probe_image="probe",
        memory="1G", memory_swap="2G", cpus="1", device_read_iops="",
        device_write_iops="", device_read_bps="", device_write_bps="",
        enable_docker=False, dind_image=None, command=["--", "true"],
    )
    with patch.object(argparse.ArgumentParser, "parse_args", return_value=parsed):
        with pytest.raises(SystemExit) as raised:
            tester_gate.main([])
    assert raised.value.code == 0
    assert commands == [["true"]]


def test_transaction_worktree_listing_ignores_malformed_records(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "_git", lambda *args, **kwargs: "worktree\n\n")
    assert transaction.list_cmru_workspaces(tmp_path) == []
