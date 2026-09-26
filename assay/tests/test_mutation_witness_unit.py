"""B105 source coverage for B106's bounded pytest evidence boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from assay.mutation_witness import (
    WITNESS_LIVENESS_PLUGIN_PATH_ENV,
    WITNESS_PLUGIN_PATH_ENV,
    WITNESS_TARGET_ENV,
    inject_witness_plugin,
    make_attempt_plan,
    read_internal_receipt,
    replay_witness_from_receipt,
    supports_sequential_pytest,
    witness_from_receipt,
)
from assay.runner import CommandPlan


def _plan(argv: tuple[str, ...], *, env: dict[str, str] | None = None) -> CommandPlan:
    return CommandPlan(
        argv_declared=argv,
        argv_appended=(),
        argv_effective=argv,
        env_declared={},
        env_effective={} if env is None else env,
        env_passthrough=(),
        allow_argv_append=False,
        budget_seconds=60,
        project_prefix=None,
    )


def _receipt(**overrides):
    value = {
        "unsupported": False,
        "target_node_id": None,
        "target_count": None,
        "earlier_failure": False,
        "auxiliary_failure": False,
        "witness_node_id": "tests/test_example.py::test_case",
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": False,
    }
    value.update(overrides)
    return value


def test_sequential_pytest_refuses_empty_override_and_untrusted_plugin_inputs():
    argv = (sys.executable, "-m", "pytest", "tests")
    assert not supports_sequential_pytest((), env={})
    assert not supports_sequential_pytest((*argv, "-o", "addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "--override-ini=addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "-n", "2"), env={})
    assert not supports_sequential_pytest(argv, env={"PYTEST_PLUGINS": "external"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "'unterminated"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "-n 2"})


@pytest.mark.parametrize(
    ("name", "contents", "expected"),
    [
        ("pytest.ini", "[pytest]\naddopts = -q\n", True),
        (".pytest.ini", "[pytest]\naddopts = --dist=load\n", False),
        ("tox.ini", "[pytest]\naddopts = --numprocesses 2\n", False),
        ("setup.cfg", "[tool:pytest]\naddopts = -q\n", True),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = "-q --maxfail=1"\n',
            True,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "--maxfail=1"]\n',
            True,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "-n 2"]\n',
            False,
        ),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-q", "--maxfail=1"]\n',
            True,
        ),
        ("pytest.ini", "[pytest]\n[pytest]\n", False),
        ("pyproject.toml", "[tool.pytest.ini_options\n", False),
    ],
)
def test_pytest_config_files_are_read_fail_closed(tmp_path, name, contents, expected):
    config = tmp_path / name
    config.write_text(contents, encoding="utf-8")
    assert supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"), cwd=tmp_path, env={}
    ) is expected


@pytest.mark.parametrize(
    "contents",
    [
        '[tool.pytest.ini_options]\naddopts = 9\n',
        '[tool.pytest.ini_options]\naddopts = ["-q", 9]\n',
    ],
)
def test_malformed_pyproject_addopts_are_not_assumed_sequential(tmp_path, contents):
    config = tmp_path / "pyproject.toml"
    config.write_text(contents, encoding="utf-8")
    assert not supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"),
        cwd=tmp_path,
        env={},
    )


def test_explicit_pytest_config_paths_and_missing_arguments(tmp_path):
    config = tmp_path / "custom.ini"
    config.write_text("[pytest]\naddopts = -n 2\n", encoding="utf-8")
    argv = (sys.executable, "-m", "pytest")
    assert not supports_sequential_pytest((*argv, "-c"), cwd=tmp_path, env={})
    assert not supports_sequential_pytest(
        (*argv, "--config-file", "custom.ini"), cwd=tmp_path, env={}
    )
    assert not supports_sequential_pytest(
        (*argv, f"--config-file={config}"), cwd=tmp_path, env={}
    )
    assert not supports_sequential_pytest(
        (*argv, "--config-file=missing.ini"), cwd=tmp_path, env={}
    )


def test_plugin_injection_writes_once_and_preserves_declared_cli_and_environment(
    tmp_path, monkeypatch
):
    plugin_dir = tmp_path / "plugins"
    plan = _plan(
        (sys.executable, "-m", "pytest", "tests"),
        env={
            "PYTHONPATH": str(plugin_dir),
            WITNESS_LIVENESS_PLUGIN_PATH_ENV: "stale",
        },
    )
    injected = inject_witness_plugin(
        plan, plugin_dir=plugin_dir, liveness_plugin_path=tmp_path / "liveness.py"
    )
    plugin_path = plugin_dir / "assay_mutation_witness_plugin.py"
    assert injected.active
    assert injected.plan.argv_effective[-2:] == ("-p", "assay_mutation_witness_plugin")
    assert injected.plan.env_effective[WITNESS_PLUGIN_PATH_ENV] == str(plugin_path)
    assert injected.plan.env_effective[WITNESS_LIVENESS_PLUGIN_PATH_ENV] == str(
        tmp_path / "liveness.py"
    )
    assert injected.plan.env_effective["PYTHONPATH"] == str(plugin_dir)

    def no_rewrite(*args, **kwargs):
        raise AssertionError("identical plugin source should not be rewritten")

    monkeypatch.setattr(Path, "write_text", no_rewrite)
    unchanged = inject_witness_plugin(plan, plugin_dir=plugin_dir)
    assert unchanged.active
    assert WITNESS_LIVENESS_PLUGIN_PATH_ENV not in unchanged.plan.env_effective


def test_plugin_injection_refuses_wrappers_and_prepends_a_new_pythonpath(tmp_path):
    unsupported = inject_witness_plugin(
        _plan(("sh", "-c", "pytest tests")), plugin_dir=tmp_path / "plugins"
    )
    assert not unsupported.active
    assert unsupported.reason == "unsupported-pytest-command"

    plan = _plan(("pytest", "tests"), env={"PYTHONPATH": "existing"})
    injected = inject_witness_plugin(plan, plugin_dir=tmp_path / "plugins")
    assert injected.plan.env_effective["PYTHONPATH"].split(":") == [
        str(tmp_path / "plugins"),
        "existing",
    ]


def test_attempt_plan_adds_or_clears_target_node_id(tmp_path):
    plan = _plan(("pytest", "tests"), env={WITNESS_TARGET_ENV: "old"})
    targeted = make_attempt_plan(
        plan, receipt_path=tmp_path / "receipt.json", target_node_id="test::target"
    )
    assert targeted.env_effective[WITNESS_TARGET_ENV] == "test::target"
    assert targeted.env_effective["ASSAY_MUTATION_WITNESS_FILE"] == str(
        tmp_path / "receipt.json"
    )
    untargeted = make_attempt_plan(
        plan, receipt_path=tmp_path / "receipt.json", target_node_id=None
    )
    assert WITNESS_TARGET_ENV not in untargeted.env_effective


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b'{"a":1,"a":2}',
        b"\xff",
        b"NaN",
        b"[]",
        b"[" * 1200 + b"0" + b"]" * 1200,
    ],
)
def test_receipt_reader_returns_no_witness_for_unreadable_json(tmp_path, raw):
    path = tmp_path / "receipt.json"
    path.write_bytes(raw)
    assert read_internal_receipt(path) is None


def test_receipt_reader_returns_only_json_objects_and_handles_missing_files(tmp_path):
    missing = tmp_path / "absent.json"
    assert read_internal_receipt(missing) is None
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert read_internal_receipt(path) == {"ok": True}


def test_valid_failure_receipt_projects_and_replays_only_for_its_exact_target():
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        target_node_id=node,
        target_count=1,
        witness_node_id=node,
        stopped_at_target=True,
    )
    expected = {
        "node_id": node,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    assert witness_from_receipt(receipt, process_exit_status=1) == expected
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) == expected
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id="another::test"
    ) is None


@pytest.mark.parametrize(
    ("changes", "process_status"),
    [
        ({"unsupported": True}, 1),
        ({"session_exit_status": True}, 1),
        ({"session_exit_status": 0}, 1),
        ({"witness_node_id": ""}, 1),
        ({"witness_node_id": "x" * 4097}, 1),
        ({"witness_node_id": "bad\ud800"}, 1),
        ({"witness_when": "setup"}, 1),
        ({"witness_outcome": "passed"}, 1),
        ({"auxiliary_failure": True}, 1),
        ({"target_count": True}, 1),
        ({"target_count": 2}, 1),
        ({"target_node_id": "other::test"}, 1),
        ({"unexpected": "extra"}, 1),
        ({}, True),
        ({}, 0),
    ],
)
def test_failure_receipt_projection_rejects_ambiguous_or_foreign_facts(
    changes, process_status
):
    receipt = _receipt(**changes)
    assert witness_from_receipt(receipt, process_exit_status=process_status) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_count": None},
        {"target_node_id": None},
        {"stopped_at_target": False},
        {"earlier_failure": True},
    ],
)
def test_replay_receipt_requires_one_exact_target_and_no_earlier_failure(overrides):
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        target_node_id=node, target_count=1, witness_node_id=node,
        stopped_at_target=True,
    )
    receipt.update(overrides)
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) is None


def test_replay_receipt_rejects_wrong_target_count_and_target_name():
    node = "tests/test_example.py::test_case"
    for changes in (
        {"target_count": True},
        {"target_count": 2},
        {"target_node_id": "other::test"},
    ):
        receipt = _receipt(
            target_node_id=node,
            target_count=1,
            witness_node_id=node,
            stopped_at_target=True,
        )
        receipt.update(changes)
        assert replay_witness_from_receipt(
            receipt, process_exit_status=1, target_node_id=node
        ) is None
