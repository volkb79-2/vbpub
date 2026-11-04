"""Rigor C-1: the optional ``[project.release].record_step``.

Inert for projects that declare nothing; otherwise it runs after the build and before
``push`` with protected inputs, accepts exactly ``record.json`` + ``release-body.md``
within size caps, hands the validated paths to ``push`` and fails before publication.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru import cli, config, record_step  # noqa: E402
from cmru.errors import RefusedBeforeChange  # noqa: E402

from test_cli_config_100_adversarial import _write_project  # noqa: E402

_TAG = "demo-v1.0.0"
_OID = "a" * 40


# --- config ------------------------------------------------------------------------

def _with_record_step(tmp_path, *, value='"record"', extra_step=True, git_tag=True):
    path = _write_project(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace('build_step = "build"', f'build_step = "build"\nrecord_step = {value}')
    if not git_tag:
        text = text.replace("git_tag = true", "git_tag = false")
    if extra_step:
        text += '\n[steps.record]\nquiet = true\ncommands = [{label = "r", argv = ["true"], cwd = "."}]\n'
    path.write_text(text, encoding="utf-8")
    return path


def test_record_step_defaults_to_inert(tmp_path):
    path = _write_project(tmp_path)
    assert config.load_forge_config(path).projects["demo"].record_step == ""


def test_record_step_is_parsed_and_reaches_the_cli_project_config(tmp_path, monkeypatch):
    path = _with_record_step(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    assert config.load_forge_config(path).projects["demo"].record_step == "record"
    assert cli.load_config(path)[1]["demo"].record_step == "record"


@pytest.mark.parametrize("kwargs, expected", [
    ({"value": '"missing"', "extra_step": False}, "not declared"),
    ({"value": '"push"'}, "separate step"),
    ({"value": '"build"'}, "separate step"),
    ({"value": '"run-tests"'}, "separate step"),
    ({"value": '""'}, "non-empty"),
    ({"value": "3"}, "non-empty"),
    ({"git_tag": False}, "requires git_tag"),
])
def test_record_step_refusals(tmp_path, capsys, kwargs, expected):
    path = _with_record_step(tmp_path, **kwargs)
    with pytest.raises(SystemExit) as exc:
        config.load_forge_config(path)
    assert exc.value.code == 2
    assert expected in capsys.readouterr().err


def test_record_environment_names_are_reserved_for_cmru():
    for key in record_step.RECORD_ENV_KEYS:
        assert config.is_reserved_internal_env(key)


# --- record_step module -----------------------------------------------------------------

def _out(tmp_path, files):
    out = tmp_path / "out"
    out.mkdir()
    for name, content in files.items():
        (out / name).write_bytes(content)
    return out


def test_validate_outputs_accepts_exactly_the_two_files(tmp_path):
    out = _out(tmp_path, {"record.json": b"{}", "release-body.md": b"body"})
    env = record_step.validate_outputs("demo", out)
    assert env == {
        "CMRU_RECORD_JSON": str(out / "record.json"),
        "CMRU_RECORD_BODY": str(out / "release-body.md"),
    }


@pytest.mark.parametrize("files, expected", [
    ({"record.json": b"{}", "release-body.md": b"b", "extra.txt": b"x"}, "undeclared"),
    ({"record.json": b"{}"}, "did not write release-body.md"),
    ({"release-body.md": b"b"}, "did not write record.json"),
    ({"record.json": b"{}", "release-body.md": b"x" * (8 * 1024 + 1)}, "over the"),
    ({"record.json": b"x" * (64 * 1024 + 1), "release-body.md": b"b"}, "over the"),
    ({"record.json": b"", "release-body.md": b"b"}, "empty"),
    ({"record.json": b"{}", "release-body.md": b"\xff\xfe"}, "UTF-8"),
])
def test_validate_outputs_refuses(tmp_path, files, expected):
    out = _out(tmp_path, files)
    with pytest.raises(RefusedBeforeChange, match=expected):
        record_step.validate_outputs("demo", out)


def test_validate_outputs_accepts_the_size_limits_exactly(tmp_path):
    out = _out(tmp_path, {
        "record.json": b"x" * (64 * 1024), "release-body.md": b"y" * (8 * 1024),
    })
    record_step.validate_outputs("demo", out)


def test_validate_outputs_refuses_links_and_directories(tmp_path):
    out = _out(tmp_path, {"release-body.md": b"b"})
    (tmp_path / "target").write_text("{}")
    (out / "record.json").symlink_to(tmp_path / "target")
    with pytest.raises(RefusedBeforeChange, match="not a regular file"):
        record_step.validate_outputs("demo", out)
    (out / "record.json").unlink()
    (out / "record.json").mkdir()
    with pytest.raises(RefusedBeforeChange, match="not a regular file"):
        record_step.validate_outputs("demo", out)


def test_private_dir_is_private_empty_and_removed():
    with record_step.private_record_dir() as private:
        assert (private.stat().st_mode & 0o777) == 0o700
        assert list((private / "out").iterdir()) == []
        kept = private
    assert not kept.exists()


def test_inventory_and_inputs(tmp_path):
    evidence = tmp_path / "ev"
    evidence.mkdir()
    (tmp_path / "f.txt").write_text("x")
    assert record_step.first_evidence_directory(tmp_path, ("f.txt", "missing", "ev")) == str(evidence)
    assert record_step.first_evidence_directory(tmp_path, ("f.txt",)) is None
    (tmp_path / "ln").symlink_to(evidence)
    assert record_step.first_evidence_directory(tmp_path, ("ln",)) is None
    path = record_step.write_inventory(
        tmp_path, project="demo", tag=_TAG, commit=_OID,
        artifacts=[{"directory": "dist", "files": []}],
    )
    assert '"kind": "cmru-release-inventory"' in path.read_text(encoding="utf-8")
    inputs = record_step.protected_inputs(tmp_path, path, None)
    assert inputs == {
        "CMRU_RECORD_OUT": str(tmp_path / "out"),
        "CMRU_BUILD_INVENTORY": str(path),
        "CMRU_GATE_EVIDENCE_DIR": None,
    }


# --- the release flow ----------------------------------------------------------------------

class _Flow:
    """Fake `_run_project_steps`: records order and environment, writes outputs."""

    def __init__(self, monkeypatch, tmp_path, *, record_files=None, fail_on=None):
        self.calls: list[tuple[str, dict]] = []
        self.rolled_back: list[str] = []
        self.reported: list[str] = []
        self.private: Path | None = None
        self.inventory = ""
        project_root = tmp_path / "w" / "demo"
        (project_root / "dist").mkdir(parents=True)
        (project_root / "dist" / "demo.whl").write_bytes(b"wheel")
        (project_root / "ev").mkdir()
        self.record_files = (
            {"record.json": b'{"a": 1}', "release-body.md": b"## Rigor"}
            if record_files is None else record_files
        )
        self.project = SimpleNamespace(
            name="demo", cwd="demo", record_step="record", artifact_dirs=("dist",),
            evidence_paths=("ev",), runner_steps={},
        )

        def run(_root, _configs, _names, steps, **kwargs):
            env = dict(kwargs.get("protected_env") or {})
            self.calls.append((steps[0], env))
            if fail_on == steps[0]:
                raise RuntimeError(f"{fail_on} failed")
            if steps[0] == "record":
                out = Path(env["CMRU_RECORD_OUT"])
                self.private = out.parent
                self.inventory = Path(env["CMRU_BUILD_INVENTORY"]).read_text(encoding="utf-8")
                for name, content in self.record_files.items():
                    (out / name).write_bytes(content)
            if steps[0] == "push" and "CMRU_RECORD_JSON" in env:
                assert Path(env["CMRU_RECORD_JSON"]).read_bytes() == b'{"a": 1}'

        monkeypatch.setattr(cli, "_run_project_steps", run)
        monkeypatch.setattr(cli, "_assert_release_candidate_unchanged", lambda *a, **k: None)
        monkeypatch.setattr(
            cli, "_rollback_unpublished_release_tag",
            lambda *_a, **k: self.rolled_back.append(str(k["cause"])) or True,
        )
        monkeypatch.setattr(
            cli, "_report_publication_started",
            lambda _root, _ws, tag: self.reported.append(tag),
        )
        self.tmp_path = tmp_path

    def run(self, phases=("build",)):
        workspace = cli.transaction.ReleaseWorkspace(
            self.tmp_path / "w", self.tmp_path / "w", "cmru-release-x", "b" * 40,
        )
        cli._run_tagged_build_and_publish(
            self.tmp_path / "w", {"demo": self.project}, "demo", workspace, _TAG,
            list(phases), candidate_sha=_OID, git_auth=None, github_config=None,
            env_config=None,
        )


def test_record_step_runs_between_build_and_push_with_protected_inputs(monkeypatch, tmp_path):
    flow = _Flow(monkeypatch, tmp_path)
    flow.run()
    assert [step for step, _ in flow.calls] == ["build", "record", "push"]
    record_env = flow.calls[1][1]
    assert record_env["CMRU_RELEASE_TAG"] == _TAG and record_env["CMRU_RELEASE_COMMIT"] == _OID
    assert record_env["CMRU_GATE_EVIDENCE_DIR"] == str(tmp_path / "w" / "demo" / "ev")
    assert "CMRU_RECORD_JSON" not in record_env
    push_env = flow.calls[2][1]
    assert push_env["CMRU_RECORD_BODY"].endswith("release-body.md")
    assert push_env["CMRU_RELEASE_TAG"] == _TAG
    assert "CMRU_RECORD_OUT" not in push_env
    assert flow.rolled_back == [] and flow.reported == []
    assert flow.private is not None and not flow.private.exists()  # cleaned up
    assert '"demo.whl"' in flow.inventory and '"directory": "dist"' in flow.inventory
    assert _TAG in flow.inventory and _OID in flow.inventory


def test_prepare_only_build_still_runs_the_record_step(monkeypatch, tmp_path):
    flow = _Flow(monkeypatch, tmp_path)
    flow.run(phases=())
    assert [step for step, _ in flow.calls] == ["record", "push"]


@pytest.mark.parametrize("files, message", [
    ({"record.json": b"{}", "release-body.md": b"b", "stray": b"x"}, "undeclared"),
    ({"record.json": b"{}", "release-body.md": b"x" * 9000}, "over the"),
    ({"record.json": b"{}"}, "did not write"),
])
def test_a_refused_output_stops_before_publication_and_rolls_back(
    monkeypatch, tmp_path, files, message,
):
    flow = _Flow(monkeypatch, tmp_path, record_files=files)
    with pytest.raises(RefusedBeforeChange, match=message):
        flow.run()
    assert [step for step, _ in flow.calls] == ["build", "record"]  # push never ran
    assert len(flow.rolled_back) == 1 and flow.reported == []
    assert flow.private is not None and not flow.private.exists()


def test_a_failed_record_step_stops_before_publication(monkeypatch, tmp_path):
    flow = _Flow(monkeypatch, tmp_path, fail_on="record")
    with pytest.raises(RuntimeError, match="record failed"):
        flow.run()
    assert [step for step, _ in flow.calls] == ["build", "record"]
    assert flow.rolled_back == ["record failed"] and flow.reported == []


def test_a_push_failure_after_the_record_step_keeps_the_tag(monkeypatch, tmp_path):
    flow = _Flow(monkeypatch, tmp_path, fail_on="push")
    with pytest.raises(RuntimeError, match="push failed"):
        flow.run()
    assert flow.rolled_back == [] and flow.reported == [_TAG]
    assert flow.private is not None and not flow.private.exists()


def test_projects_without_record_step_are_untouched(monkeypatch, tmp_path):
    flow = _Flow(monkeypatch, tmp_path)
    flow.project.record_step = ""
    flow.run()
    assert [step for step, _ in flow.calls] == ["build", "push"]
    assert flow.calls[1][1] == {"CMRU_RELEASE_COMMIT": _OID, "CMRU_RELEASE_TAG": _TAG}


def test_every_ordinary_step_clears_the_record_environment(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(step, project_root, log_root, **kwargs):
        captured.update(kwargs["protected_env"])

    monkeypatch.setattr(cli, "execute_step", fake_execute)
    for key in record_step.RECORD_ENV_KEYS:
        monkeypatch.setenv(key, "ambient")
    project = SimpleNamespace(
        name="demo", cwd="demo", runner_steps={"build": object()}, env={},
        build_metadata={}, token_source="cmru", github_token="", changelog=None,
        runtime_kind="none",
    )
    (tmp_path / "demo").mkdir()
    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert all(captured[key] is None for key in record_step.RECORD_ENV_KEYS)


# --- release dry-run ---------------------------------------------------------------------------

@pytest.mark.real_release_notes_preflight
def test_dry_run_preview_prints_the_record_plan_only_when_declared(monkeypatch, tmp_path):
    messages: list[str] = []
    monkeypatch.setattr(cli, "log_info", messages.append)
    monkeypatch.setattr(cli, "_validated_oci_release_image", lambda *a, **k: None)
    monkeypatch.setattr("cmru.runner.render_step_plan", lambda step, root: ["would run r"])
    declared = SimpleNamespace(
        name="demo", cwd="demo", record_step="record", changelog=None, git_tag=True,
        runner_steps={"record": object()}, known_project_names=frozenset(),
    )
    plain = SimpleNamespace(
        name="plain", cwd="plain", record_step="", changelog=None, git_tag=True,
        runner_steps={}, known_project_names=frozenset(),
    )
    cli._preview_release_bodies(tmp_path, {"demo": declared, "plain": plain}, ["demo", "plain"])
    joined = "\n".join(messages)
    assert "record step 'record' would run after the build" in joined
    assert "demo:record: would run r" in joined
    assert "plain" not in joined
    messages.clear()
    cli._preview_release_bodies(tmp_path, {"demo": declared}, ["demo"], no_build=True)
    assert messages == []
