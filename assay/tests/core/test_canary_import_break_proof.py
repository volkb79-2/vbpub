"""Cause proof for the import-break transform across the registered R3 adapters.

Python and JavaScript are the built-in R3 languages. Python's real command
path is exercised by ``test_canary_python_pipeline.py``; this module adds a
real Node execution for JavaScript and pins the shared nonce/path helpers,
including the dormant Go diagnostic grammar exposed by the public canary
runner.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    GitRepo,
    completed_process,
    isolated_process_runner,
    make_deadline,
    make_lane,
    make_plan,
    make_r3_judge,
    prepared_snapshot,
)

from assay import canary, contention
from assay.adapters.go import GoAdapter
from assay.adapters.javascript import JavaScriptAdapter
from assay.adapters.python import PythonAdapter
from assay.config import CanaryConfig
from assay.errors import Outcome, ReasonCode


def _now() -> datetime:
    return datetime.now(UTC)


def _failed_command(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        stdout_tail=stdout,
        stderr_tail=stderr,
        stdout_dropped_bytes=0,
        stderr_dropped_bytes=0,
    )


@pytest.mark.parametrize(
    ("adapter", "source"),
    [
        (PythonAdapter(), "VALUE = 7\n"),
        (JavaScriptAdapter(), "module.exports = { value: 7 }\n"),
        (GoAdapter(), "package target\n\nfunc Value() int { return 7 }\n"),
    ],
)
def test_import_break_binds_a_nonce_to_the_adapter_inserted_statement(adapter, source):
    nonce = "a" * 32

    transformed, _ = canary._apply_mechanism(
        adapter,
        canary.MECHANISM_IMPORT_BREAK,
        source,
        import_break_nonce=nonce,
    )

    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    assert transformed.count(token) == 1
    assert canary._import_break_nonce_line(transformed, nonce) == next(
        index
        for index, source_line in enumerate(transformed.splitlines(), start=1)
        if token in source_line
    )
    assert canary._IMPORT_BREAK_MARKER not in source


def test_import_break_without_a_nonce_keeps_the_adapter_template():
    transformed, _ = canary._apply_mechanism(
        PythonAdapter(),
        canary.MECHANISM_IMPORT_BREAK,
        "VALUE = 7\n",
    )

    assert canary._IMPORT_BREAK_MARKER in transformed
    assert f"{canary._IMPORT_BREAK_MARKER}:" not in transformed
    assert canary._import_break_nonce_line(transformed, "a" * 32) is None


def test_diagnostic_path_matching_decodes_file_urls_and_rejects_empty_paths(
    tmp_path: Path,
):
    target = tmp_path / "pkg" / "space name.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")

    assert canary._diagnostic_path_matches(
        target.as_uri(),
        target_path="pkg/space name.py",
        target_file=target,
        cwd=".",
    )
    assert not canary._diagnostic_path_matches(
        "  ",
        target_path="pkg/space name.py",
        target_file=target,
        cwd=".",
    )


def test_diagnostic_path_matching_treats_resolution_errors_as_no_match(
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_resolve(self, *args, **kwargs):
        raise OSError("path disappeared")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert not canary._diagnostic_path_matches(
        "/snapshot/pkg/mod.py",
        target_path="pkg/mod.py",
        target_file=Path("/snapshot/pkg/mod.py"),
        cwd=".",
    )


def test_go_panic_proof_requires_the_nonce_and_exact_injected_line(tmp_path: Path):
    adapter = GoAdapter()
    nonce = "b" * 32
    transformed, _ = canary._apply_mechanism(
        adapter,
        canary.MECHANISM_IMPORT_BREAK,
        "package target\n",
        import_break_nonce=nonce,
    )
    line = canary._import_break_nonce_line(transformed, nonce)
    target = tmp_path / "pkg" / "mod.go"
    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    diagnostic = (
        f"panic: {token}\n\n"
        "goroutine 1 [running]:\n"
        f"example/pkg.init.0()\n\t{target}:{line} +0x1\n"
    )

    assert line is not None
    assert canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.go",
        target_file=target,
        line=line,
        language="go",
        cwd=".",
    )


def test_go_panic_without_the_run_nonce_is_not_cause_proof(tmp_path: Path):
    nonce = "d" * 32
    target = tmp_path / "pkg" / "mod.go"
    diagnostic = (
        "panic: unrelated panic\n\n"
        "goroutine 1 [running]:\n"
        f"example/pkg.init.0()\n\t{target}:9 +0x1\n"
    )

    assert not canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.go",
        target_file=target,
        line=9,
        language="go",
        cwd=".",
    )


def test_go_panic_nonce_at_another_file_is_not_cause_proof(tmp_path: Path):
    nonce = "e" * 32
    target = tmp_path / "pkg" / "mod.go"
    other = tmp_path / "pkg" / "other.go"
    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    diagnostic = (
        f"panic: {token}\n\n"
        "goroutine 1 [running]:\n"
        f"example/pkg.init.0()\n\t{other}:9 +0x1\n"
    )

    assert not canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.go",
        target_file=target,
        line=9,
        language="go",
        cwd=".",
    )


def test_javascript_nonce_without_a_native_error_header_is_not_cause_proof(
    tmp_path: Path,
):
    nonce = "c" * 32
    line = 9
    target = tmp_path / "pkg" / "mod.js"
    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    diagnostic = (
        f"AssertionError: {token}\n    at Object.<anonymous> ({target}:{line}:7)\n"
    )

    assert not canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.js",
        target_file=target,
        line=line,
        language="javascript",
        cwd=".",
    )


def test_javascript_nonce_at_another_file_is_not_cause_proof(tmp_path: Path):
    nonce = "f" * 32
    target = tmp_path / "pkg" / "mod.js"
    other = tmp_path / "pkg" / "other.js"
    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    diagnostic = f"Error: {token}\n    at Object.<anonymous> ({other}:9:7)\n"

    assert not canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.js",
        target_file=target,
        line=9,
        language="javascript",
        cwd=".",
    )


def test_go_diagnostic_grammar_is_not_used_for_sql(tmp_path: Path):
    nonce = "1" * 32
    target = tmp_path / "pkg" / "mod.sql"
    token = f"{canary._IMPORT_BREAK_MARKER}:{nonce}"
    diagnostic = (
        f"panic: {token}\n\n"
        "goroutine 1 [running]:\n"
        f"example/pkg.init.0()\n\t{target}:9 +0x1\n"
    )

    assert not canary._has_import_break_diagnostic(
        _failed_command(stderr=diagnostic),
        nonce=nonce,
        target_path="pkg/mod.sql",
        target_file=target,
        line=9,
        language="sql",
        cwd=".",
    )


def test_legacy_python_canary_downgrades_an_unrelated_command_failure(
    git_repo: GitRepo,
):
    repo = git_repo
    base_commit = repo.head()
    repo.write("pkg/mod.py", "VALUE = 7\n")
    repo.commit_all("add a Python canary target")
    lane = make_lane(rigor=("R0",), argv=("not-executed-by-test-double",))
    transformed_lines: list[tuple[str, int]] = []

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        match = re.search(
            rf"{re.escape(canary._IMPORT_BREAK_MARKER)}:([0-9a-f]{{32}})",
            source,
        )
        if match is None:
            return completed_process(list(argv), 0, "", "")
        token = match.group(0)
        line = next(
            index
            for index, source_line in enumerate(source.splitlines(), start=1)
            if token in source_line
        )
        transformed_lines.append((token, line))
        return completed_process(list(argv), 1, "unrelated test assertion\n", "")

    result = canary.run_python_canary(
        lane,
        repo=repo.path,
        project_root=repo.path,
        base_commit=base_commit,
        target_path="pkg/mod.py",
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_IMPORT_BREAK,
        process_runner=process,
        clock=_now,
    )

    attempt = result.attempts[0]
    assert len(transformed_lines) == 1
    assert transformed_lines[0][0].startswith(f"{canary._IMPORT_BREAK_MARKER}:")
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.CANARY_SURVIVED
    assert "lacked a matching per-run import-break stack frame" in attempt.description


def test_real_javascript_import_break_proves_the_nonce_and_exact_runtime_frame(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    node = shutil.which("node")
    assert node is not None, (
        "tester-unified provides Node for the real JavaScript canary"
    )

    repo = git_repo
    repo.write("pkg/mod.js", "module.exports = { value: 7 }\n")
    repo.write(
        "tests/check.cjs",
        "const { value } = require('../pkg/mod.js')\n"
        "if (value !== 7) process.exit(17)\n",
    )
    commit = repo.commit_all("add a JavaScript canary target and consumer")
    target_path = "pkg/mod.js"
    judge = make_r3_judge(
        language="javascript",
        source_root_paths=(repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target=target_path),
    )
    lane = make_lane(
        rigor=("R0", "R3"),
        argv=(str(Path(node).resolve()), "tests/check.cjs"),
        judge=judge,
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    transformed_tokens: list[tuple[str, int]] = []

    def resource_window(**kwargs):
        class Window:
            observation = contention.ContentionObservation(
                "clear", reasons=("cgroup_psi.cpu.some_us=1",)
            )

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        return Window()

    monkeypatch.setattr(canary.contention, "ContentionWindow", resource_window)

    def native_process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / target_path).read_text(encoding="utf-8")
        match = re.search(
            rf"{re.escape(canary._IMPORT_BREAK_MARKER)}:([0-9a-f]{{32}})",
            source,
        )
        if match is not None:
            token = match.group(0)
            line = next(
                index
                for index, source_line in enumerate(source.splitlines(), start=1)
                if token in source_line
            )
            transformed_tokens.append((token, line))
        return isolated_process_runner(argv, env=env, cwd=cwd, timeout=timeout)

    with prepared_snapshot(repo, commit=commit, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(budget_seconds=60.0),
            project_root=repo.path,
            resolved_base=None,
            mechanism="import-break",
            target=target_path,
            adapter=JavaScriptAdapter(),
            process_runner=native_process,
            clock=_now,
        )

    attempt = result.attempts[0]
    assert len(transformed_tokens) == 1
    assert transformed_tokens[0][0].startswith(f"{canary._IMPORT_BREAK_MARKER}:")
    assert transformed_tokens[0][1] > 0
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.expected_reason_code is ReasonCode.COMMAND_FAILED
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "control cgroup resource telemetry (clear)" in attempt.description
    assert "transform cgroup resource telemetry (clear)" in attempt.description
    assert canary.build_canary_claim(result).status is Outcome.PASS
