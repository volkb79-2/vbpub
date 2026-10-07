"""B105 source coverage for B106's bounded pytest evidence boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _pytest.config import get_config

from assay.mutation_witness import (
    COLD_PYTEST_FLAG_OPTIONS,
    COLD_PYTEST_VALUE_OPTIONS,
    HOOK_FINGERPRINT_HOOKS,
    _PLUGIN_SOURCE,
    WITNESS_COLD_ENV,
    WITNESS_LIVENESS_PLUGIN_PATH_ENV,
    WITNESS_MANIFEST_FILE_ENV,
    WITNESS_PLUGIN_PATH_ENV,
    WITNESS_TARGET_ENV,
    cold_shape_refusal,
    cold_witness_from_receipt,
    declared_failure_proof_ok,
    inject_witness_plugin,
    make_attempt_plan,
    read_internal_receipt,
    receipt_facts,
    replay_witness_from_receipt,
    supports_sequential_pytest,
    survivor_proof_ok,
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
        "unsupported_pytest_cov_only": False,
        "replay_supported": False,
        "target_node_id": None,
        "target_count": None,
        "earlier_failure": False,
        "auxiliary_failure": False,
        "witness_node_id": "tests/test_example.py::test_case",
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": False,
        "cold_requested": False,
        "stopped_cold": False,
        "collection_error": False,
        "manifest_supported": True,
        "collection_count": 1,
        "collection_sha256": "a" * 64,
        "collection_duplicates": 0,
        "started_count": 1,
        "started_prefix_ok": True,
        "failed_call_index": None,
        "hook_fingerprint_sha256": "b" * 64,
        "hook_count": 10,
        "runtime_fingerprint_sha256": "c" * 64,
        "config_sha256": None,
        "archive_hook_exception_used": False,
    }
    value.update(overrides)
    return value


def _run_child_pytest(
    project: Path,
    plugin_dir: Path,
    receipt_path: Path,
    *,
    pytest_args: tuple[str, ...] = (),
    env_overrides: dict[str, str] | None = None,
    cold: bool = False,
):
    env = os.environ.copy()
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTEST_ADDOPTS", None)
    if env_overrides:
        env.update(env_overrides)
    plan = _plan(
        (sys.executable, "-m", "pytest", "-q", *pytest_args, "tests"),
        env=env,
    )
    injected = inject_witness_plugin(plan, plugin_dir=plugin_dir, cwd=project)
    assert injected.active
    attempt = make_attempt_plan(
        injected.plan,
        receipt_path=receipt_path,
        target_node_id=None,
        cold=cold,
        manifest_path=receipt_path.parent / "manifest.txt" if cold else None,
    )
    result = subprocess.run(
        attempt.argv_effective,
        cwd=project,
        env=attempt.env_effective,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, read_internal_receipt(receipt_path)


def test_sequential_pytest_refuses_empty_override_and_untrusted_plugin_inputs():
    argv = (sys.executable, "-m", "pytest", "tests")
    assert not supports_sequential_pytest((), env={})
    assert not supports_sequential_pytest((*argv, "-o", "addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "--override-ini=addopts=-n 2"), env={})
    assert not supports_sequential_pytest((*argv, "-n", "2"), env={})
    assert not supports_sequential_pytest(argv, env={"PYTEST_PLUGINS": "external"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "'unterminated"})
    assert not supports_sequential_pytest(argv, env={"PYTEST_ADDOPTS": "-n 2"})


def test_cold_short_option_classes_match_installed_pytest_and_xdist_parser():
    config = get_config()
    config.pluginmanager.import_plugin("xdist.plugin")
    flags: set[str] = set()
    values: set[str] = set()
    for action in config._parser.optparser._actions:
        for option in action.option_strings:
            if len(option) != 2 or not option.startswith("-"):
                continue
            if action.nargs == 0:
                flags.add(option[1])
            else:
                values.add(option[1])

    assert flags == COLD_PYTEST_FLAG_OPTIONS
    assert values == COLD_PYTEST_VALUE_OPTIONS


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


def test_attempt_plan_sets_cold_and_manifest_only_for_that_attempt(tmp_path):
    plan = _plan(("pytest", "tests"), env={WITNESS_COLD_ENV: "stale", WITNESS_MANIFEST_FILE_ENV: "stale"})
    cold = make_attempt_plan(
        plan,
        receipt_path=tmp_path / "receipt.json",
        target_node_id=None,
        cold=True,
        manifest_path=tmp_path / "manifest.txt",
    )
    assert cold.env_effective[WITNESS_COLD_ENV] == "1"
    assert cold.env_effective[WITNESS_MANIFEST_FILE_ENV] == str(tmp_path / "manifest.txt")
    full = make_attempt_plan(plan, receipt_path=tmp_path / "full.json", target_node_id=None)
    assert WITNESS_COLD_ENV not in full.env_effective
    assert WITNESS_MANIFEST_FILE_ENV not in full.env_effective


@pytest.mark.parametrize(
    ("argv", "appended", "env", "reason"),
    [
        (("pytest", "tests", "--cov"), (), {}, "unrecognized coverage option"),
        (("pytest", "tests", "-qoaddopts=-n4"), (), {}, "pytest override"),
        (("pytest", "tests", "-cfoo.ini"), (), {}, "pytest configuration override"),
        (("pytest", "tests", "-qx"), (), {}, "fail-fast option"),
        (("pytest", "tests", "-pxdist"), (), {}, "parallel option"),
        (("pytest", "tests", "-prandomly"), (), {}, "order-changing option"),
        (("pytest", "tests", "-ppytest_cov"), (), {}, "coverage plugin re-enabled"),
        (("pytest", "tests"), ("-pxdist",), {}, "parallel option"),
        (("pytest", "tests"), (), {"PYTEST_ADDOPTS": "-x"}, "pytest environment option"),
        (("pytest", "tests"), (), {"COVERAGE_PROCESS_START": ".coveragerc"}, "coverage re-enabled by environment"),
    ],
)
def test_cold_shape_refusal_rejects_unprovable_pytest_shapes(argv, appended, env, reason):
    assert reason in (cold_shape_refusal(argv, env, appended=appended) or "")


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "tests", "-qkexpr"),
        ("pytest", "tests", "-qk", "expr"),
        ("pytest", "tests", "-pno:randomly"),
    ],
)
def test_cold_shape_refusal_accepts_selection_and_disabled_randomizer(argv):
    assert cold_shape_refusal(argv, {}) is None


def test_plugin_source_and_hook_fingerprint_contract_are_current():
    compile(_PLUGIN_SOURCE, "assay_mutation_witness_plugin.py", "exec")
    expected_hooks = (
        "pytest_runtestloop", "pytest_runtest_protocol", "pytest_runtest_logstart",
        "pytest_runtest_logreport", "pytest_runtest_call", "pytest_runtest_makereport",
        "pytest_runtest_setup",
        "pytest_runtest_teardown", "pytest_collectreport",
        "pytest_collection_modifyitems", "pytest_sessionfinish",
    )
    namespace = {}
    exec(compile(_PLUGIN_SOURCE, "assay_mutation_witness_plugin.py", "exec"), namespace)
    assert HOOK_FINGERPRINT_HOOKS == expected_hooks
    assert namespace["_HOOKS"] == expected_hooks


def test_cold_and_survivor_proofs_require_complete_matching_receipts():
    expected = receipt_facts(_receipt())
    assert expected is not None
    survivor = _receipt(session_exit_status=0)
    assert survivor_proof_ok(survivor, process_exit_status=0, expected=expected, command="r2")
    assert survivor_proof_ok(
        {**survivor, "unsupported": True, "unsupported_pytest_cov_only": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported_pytest_cov_only": True},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    assert not survivor_proof_ok(
        {**survivor, "unsupported": True},
        process_exit_status=0,
        expected=expected,
        command="r2",
    )
    assert not survivor_proof_ok(
        {**survivor, "started_prefix_ok": False},
        process_exit_status=0,
        expected=expected,
        command="declared",
    )
    cold = _receipt(
        cold_requested=True,
        stopped_cold=True,
        failed_call_index=0,
    )
    proof = cold_witness_from_receipt(cold, process_exit_status=1, expected=expected)
    assert proof is not None
    assert proof[1:] == (1, 0)
    assert proof[0]["node_id"] == "tests/test_example.py::test_case"
    assert cold_witness_from_receipt(
        {**cold, "collection_error": True}, process_exit_status=1, expected=expected
    ) is None
    assert cold_witness_from_receipt(
        {**cold, "session_exit_status": True}, process_exit_status=1, expected=expected
    ) is None


def test_makereport_wrapper_changes_fingerprint_and_cannot_prove_a_cold_kill(tmp_path):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    plugin_dir = tmp_path / "plugins"
    baseline_result, baseline = _run_child_pytest(
        project, plugin_dir, tmp_path / "baseline.json", cold=True
    )
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert baseline is not None and baseline["unsupported"] is False
    baseline_facts = receipt_facts(baseline)
    assert baseline_facts is not None

    (tests / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True, tryfirst=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    report = outcome.get_result()\n"
        "    if report.when == 'call' and report.outcome == 'passed':\n"
        "        report.outcome = 'failed'\n"
        "        report.longrepr = 'forged failure for cold-witness test'\n",
        encoding="utf-8",
    )
    forged_result, forged = _run_child_pytest(
        project, plugin_dir, tmp_path / "forged.json", cold=True
    )
    assert forged_result.returncode == 1
    assert forged is not None
    assert forged["unsupported"] is True
    assert forged["unsupported_pytest_cov_only"] is False
    assert forged["hook_fingerprint_sha256"] != baseline["hook_fingerprint_sha256"]
    assert cold_witness_from_receipt(
        forged, process_exit_status=forged_result.returncode, expected=baseline_facts
    ) is None


def test_declared_failure_proof_only_allows_reviewed_pytest_cov_unsupported_hooks():
    node = "tests/test_example.py::test_case"
    coverage_baseline = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        session_exit_status=0,
        witness_node_id=None,
        witness_when=None,
        witness_outcome=None,
        started_count=0,
    )
    expected = receipt_facts(coverage_baseline)
    assert expected is not None
    declared_failure = _receipt(
        unsupported=True,
        unsupported_pytest_cov_only=True,
        witness_node_id=node,
        started_count=1,
    )
    assert declared_failure_proof_ok(
        declared_failure,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )

    refusals = (
        {"unsupported_pytest_cov_only": False},
        {"unsupported_pytest_cov_only": 1},
        {"unsupported_pytest_cov_only": True, "unsupported": False},
        {"collection_error": True},
        {"auxiliary_failure": True},
        {"earlier_failure": True},
        {"started_prefix_ok": False},
        {"hook_fingerprint_sha256": "d" * 64},
    )
    for change in refusals:
        assert not declared_failure_proof_ok(
            {**declared_failure, **change},
            process_exit_status=1,
            expected=expected,
            manifest_node_ids=(node,),
        ), change
    missing_fact = dict(declared_failure)
    del missing_fact["unsupported_pytest_cov_only"]
    assert not declared_failure_proof_ok(
        missing_fact,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )
    assert not declared_failure_proof_ok(
        None,
        process_exit_status=1,
        expected=expected,
        manifest_node_ids=(node,),
    )


def test_installed_pytest_cov_hooks_are_classified_as_the_only_declared_exception(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tests").mkdir()
    (project / "tests" / "test_ok.py").write_text(
        "import os\n\n"
        "def test_ok():\n"
        "    if os.environ.get('FAIL_CALL'):\n"
        "        assert False\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins"
    receipt_path = tmp_path / "declared-receipt.json"
    result, receipt = _run_child_pytest(
        project,
        plugin_dir,
        receipt_path,
        pytest_args=("--cov=tests",),
    )
    assert result.returncode == 0, result.stderr
    assert receipt is not None
    assert receipt["unsupported"] is True
    assert receipt["unsupported_pytest_cov_only"] is True
    facts = receipt_facts(receipt)
    assert facts is not None
    assert survivor_proof_ok(
        receipt, process_exit_status=0, expected=facts, command="declared"
    )

    failure_result, failure_receipt = _run_child_pytest(
        project,
        plugin_dir,
        tmp_path / "declared-failure.json",
        pytest_args=("--cov=tests",),
        env_overrides={"FAIL_CALL": "1"},
    )
    assert failure_result.returncode == 1
    assert failure_receipt is not None
    assert failure_receipt["unsupported"] is True
    assert failure_receipt["unsupported_pytest_cov_only"] is True
    assert declared_failure_proof_ok(
        failure_receipt,
        process_exit_status=failure_result.returncode,
        expected=facts,
        manifest_node_ids=("tests/test_ok.py::test_ok",),
    )


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


def test_legacy_b106_replay_support_does_not_certify_a_cold_witness():
    node = "tests/test_example.py::test_case"
    receipt = _receipt(
        unsupported=True,
        replay_supported=True,
        target_node_id=node,
        target_count=1,
        witness_node_id=node,
        stopped_at_target=True,
    )
    assert witness_from_receipt(receipt, process_exit_status=1) is not None
    assert replay_witness_from_receipt(
        receipt, process_exit_status=1, target_node_id=node
    ) is not None

    expected = receipt_facts(_receipt())
    assert expected is not None
    cold_receipt = _receipt(
        unsupported=True,
        replay_supported=True,
        cold_requested=True,
        stopped_cold=True,
        failed_call_index=0,
    )
    assert cold_witness_from_receipt(
        cold_receipt, process_exit_status=1, expected=expected
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
