"""Final behavioral contracts for small CMRU operational modules."""
from __future__ import annotations

import io
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import dependencies, manifest, output
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
