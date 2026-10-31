"""WP3: R3 import-break attribution uses a per-attempt execution receipt."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from datetime import datetime
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    GitRepo,
    completed_process,
    make_deadline,
    make_lane,
    make_plan,
    make_r3_judge,
    prepared_snapshot,
)

from assay import canary
from assay.adapters.go import GoAdapter
from assay.adapters.javascript import JavaScriptAdapter
from assay.adapters.python import PythonAdapter
from assay.adapters.sql import SqlAdapter
from assay.config import CanaryConfig
from assay.contention import ContentionObservation
from assay.errors import AssayError, Outcome, ReasonCode
from assay.process_tree import ProcessTreeEvidence, ProcessTreeProcessRunner
from assay.runner import CommandResult, default_process_runner, execute_plan
from assay.termination import CREDIT_JUDGEABLE, Termination
from assay.verdict import CanaryAttempt
from termination_table import LIMITS_CLEAR, PRECEDENCE_CASES, TERMINATION_CASES

MOMENT_TEXT = "2026-08-10T12:00:00+00:00"
NONCE = "0123456789abcdef0123456789abcdef"
RECEIPT_ENV = "ASSAY_CANARY_RECEIPT"


def _lane(
    repo: GitRepo,
    *,
    target: str = "pkg/mod.py",
    language: str = "python",
    argv: tuple[str, ...] = ("check",),
    path: bool = False,
):
    source_root = (repo.path / target).parent
    return make_lane(
        rigor=("R0", "R3"),
        judge=make_r3_judge(
            language=language,
            source_root_paths=(source_root,),
            canary=CanaryConfig(mechanism="import-break", target=target),
        ),
        argv=argv,
        env_passthrough=("PATH",) if path else (),
    )


def _seed(repo: GitRepo) -> str:
    repo.write(".gitignore", "__pycache__/\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def value():\n    return 1\n")
    return repo.commit_all("seed receipt canary target")


@pytest.mark.parametrize(
    ("adapter", "source", "receipt_markers"),
    (
        (
            PythonAdapter(),
            "def value():\n    return 1\n",
            ("ASSAY_CANARY_RECEIPT", "write_text", NONCE),
        ),
        (
            JavaScriptAdapter(),
            "export const value = 1\n",
            ("ASSAY_CANARY_RECEIPT", "writeFileSync", NONCE),
        ),
        (
            GoAdapter(),
            "package sample\n",
            (f"panic(\"assay-canary-import-break:{NONCE}\")",),
        ),
    ),
    ids=("python", "javascript", "go"),
)
def test_import_break_adapter_receipt_protocol_is_in_parity(
    adapter, source: str, receipt_markers: tuple[str, ...]
):
    method = adapter.inject_import_break
    assert signature(method).parameters["nonce"].kind.name == "KEYWORD_ONLY"

    transformed, description = method(source, nonce=NONCE)

    for marker in receipt_markers:
        assert marker in transformed
    assert NONCE not in description


def test_sql_adapter_keeps_import_break_unreachable_with_the_new_signature():
    with pytest.raises(NotImplementedError, match="inject_import_break"):
        SqlAdapter().inject_import_break("SELECT 1;", nonce=NONCE)


def test_python_import_break_is_one_line_and_preserves_line2_cookie():
    source = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: latin-1 -*-\n"
        "value = 1\n"
    )

    transformed, _description = PythonAdapter().inject_import_break(
        source, nonce=NONCE
    )

    lines = transformed.splitlines()
    assert lines[:2] == source.splitlines()[:2]
    assert len(lines) == len(source.splitlines()) + 1
    assert 'raise AssertionError("assay-canary-import-break")' in lines[2]


def test_js_import_break_is_one_line_with_ts_ignore():
    transformed, _description = JavaScriptAdapter().inject_import_break(
        "export const value = 1\n", nonce=NONCE
    )

    lines = transformed.splitlines()
    ts_ignore = lines.index("// @ts-ignore")
    assert lines[ts_ignore + 1].startswith(";(() => {")
    assert "getBuiltinModule" in lines[ts_ignore + 1]
    assert lines[ts_ignore + 2] == 'throw new Error("assay-canary-import-break")'


def _attempt(
    *,
    outcome: Outcome,
    observed_reason: ReasonCode | None,
    expected_reason: ReasonCode,
    description: str,
) -> tuple[Outcome, ReasonCode | None]:
    return canary.judge_attempt(
        CanaryAttempt(
            target="pkg/mod.py",
            description=description,
            control_outcome=Outcome.PASS,
            transformed_outcome=outcome,
            expected_reason_code=expected_reason,
            observed_reason_code=observed_reason,
        )
    )


def _command_result(
    *,
    outcome: Outcome,
    reason_code: ReasonCode | None,
    returncode: int | None,
    process_tree: ProcessTreeEvidence | None,
) -> CommandResult:
    return CommandResult(
        plan=make_plan(make_lane()),
        outcome=outcome,
        reason_code=reason_code,
        returncode=returncode,
        started=MOMENT_TEXT,
        ended=MOMENT_TEXT,
        process_tree_evidence=None if process_tree is None else process_tree.to_dict(),
    )


@pytest.mark.parametrize(
    "case",
    TERMINATION_CASES
    + tuple(case for case in PRECEDENCE_CASES if not case.inputs["decided_by_report"]),
    ids=lambda case: case.name,
)
def test_canary_command_classification_matches_the_shared_termination_table(case):
    process_tree = case.inputs["process_tree"]
    observation_status = case.inputs["observation"]
    limits = case.inputs["limits"]
    if observation_status == "unavailable":
        observation = ContentionObservation(
            "unavailable", reasons=("observer-unavailable",)
        )
    elif observation_status == "clear" or limits is not None:
        observation = ContentionObservation("clear", limits=limits or LIMITS_CLEAR)
    elif observation_status == "not_requested":
        observation = None
    else:
        observation = ContentionObservation(observation_status)
    result = _command_result(
        outcome=case.inputs["outcome"],
        reason_code=case.inputs["reason_code"],
        returncode=case.inputs["returncode"],
        process_tree=process_tree,
    )

    classified = canary._classify_result(
        outcome=case.inputs["outcome"],
        reason_code=case.inputs["reason_code"],
        result=result,
        observation=observation,
        observation_required=case.inputs["observation_required"],
    )

    assert classified is case.termination
    expected_control = (
        Outcome.PASS
        if case.termination is Termination.PASSED
        else Outcome.FAIL
        if case.termination in CREDIT_JUDGEABLE
        else Outcome.INCONCLUSIVE
    )
    assert canary._attribute_control(classified) is expected_control


def test_control_clean_fail_is_recorded_as_fail():
    control_outcome = canary._attribute_control(Termination.FAILED)

    assert control_outcome is Outcome.FAIL
    attempt = CanaryAttempt(
        target="pkg/mod.py",
        description="control failed before the canary transform",
        control_outcome=control_outcome,
        transformed_outcome=Outcome.INCONCLUSIVE,
        expected_reason_code=ReasonCode.COMMAND_FAILED,
        observed_reason_code=ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert canary.judge_attempt(attempt) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )


@pytest.mark.parametrize(
    ("observation", "required", "expected"),
    (
        (None, False, Termination.FAILED),
        (ContentionObservation("unobserved"), False, Termination.FAILED),
        (ContentionObservation("unobserved"), True, Termination.UNOBSERVED),
    ),
    ids=("not-requested", "optional-unobserved", "required-unobserved"),
)
def test_canary_classifier_maps_unobserved_contention_to_the_shared_vocabulary(
    observation, required: bool, expected: Termination
):
    result = _command_result(
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=1,
        process_tree=None,
    )

    assert canary._classify_result(
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        result=result,
        observation=observation,
        observation_required=required,
    ) is expected


@pytest.mark.parametrize(
    "case",
    tuple(
        case
        for case in TERMINATION_CASES
        if case.termination not in {Termination.PASSED, Termination.FAILED}
    ),
    ids=lambda case: case.name,
)
@pytest.mark.parametrize("mechanism", ("import-break", "uncovered-line"))
def test_nonjudgeable_termination_stays_inconclusive_even_with_a_receipt(
    case, mechanism: str, tmp_path: Path
):
    receipt_path = tmp_path / f"{case.name}-{mechanism}.receipt"
    receipt_path.write_bytes(NONCE.encode("ascii"))

    transformed, observed_reason, _description = canary._attribute_transform(
        mechanism=mechanism,
        description="transformed source",
        termination=case.termination,
        observed_reason_code=case.inputs["reason_code"],
        receipt_path=receipt_path,
        nonce=NONCE,
    )

    assert (transformed, observed_reason) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )


@pytest.mark.parametrize(
    (
        "name",
        "mechanism",
        "termination",
        "observed_reason",
        "receipt_bytes",
        "receipt_unreadable",
        "expected_transform",
        "expected_observed",
        "description_fragment",
        "expected_attempt",
        "expected_attempt_reason",
    ),
    (
        (
            "import failure with matching receipt",
            "import-break",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            NONCE.encode(),
            False,
            Outcome.FAIL,
            ReasonCode.COMMAND_FAILED,
            "receipt matched",
            Outcome.PASS,
            None,
        ),
        (
            "import failure without receipt",
            "import-break",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            None,
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "no receipt",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "import failure with wrong reason",
            "import-break",
            Termination.FAILED,
            ReasonCode.UNCOVERED_LINES,
            NONCE.encode(),
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "receipt matched",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "import accepted after receipt",
            "import-break",
            Termination.PASSED,
            None,
            NONCE.encode(),
            False,
            Outcome.PASS,
            None,
            "defect executed, suite passed",
            Outcome.FAIL,
            ReasonCode.CANARY_SURVIVED,
        ),
        (
            "import accepted without receipt",
            "import-break",
            Termination.PASSED,
            None,
            None,
            False,
            Outcome.PASS,
            None,
            "no receipt",
            Outcome.FAIL,
            ReasonCode.CANARY_SURVIVED,
        ),
        (
            "overlong receipt is not a match",
            "import-break",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            NONCE.encode() + b"x",
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "no receipt",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "wrong nonce is not a match",
            "import-break",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            b"f" * len(NONCE),
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "no receipt",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "uncovered-line failure at expected R1 reason",
            "uncovered-line",
            Termination.FAILED,
            ReasonCode.UNCOVERED_LINES,
            None,
            False,
            Outcome.FAIL,
            ReasonCode.UNCOVERED_LINES,
            "appended uncovered lines",
            Outcome.PASS,
            None,
        ),
        (
            "uncovered-line R0 failure is unrelated",
            "uncovered-line",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            None,
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "appended uncovered lines",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "uncovered-line accepted",
            "uncovered-line",
            Termination.PASSED,
            None,
            None,
            False,
            Outcome.PASS,
            None,
            "appended uncovered lines",
            Outcome.FAIL,
            ReasonCode.CANARY_SURVIVED,
        ),
        (
            "uncovered-line timeout is inconclusive",
            "uncovered-line",
            Termination.TIMED_OUT,
            ReasonCode.LANE_TIMEOUT,
            None,
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "appended uncovered lines",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "infrastructure failure remains inconclusive",
            "import-break",
            Termination.LIMIT_EVENT,
            ReasonCode.COMMAND_FAILED,
            NONCE.encode(),
            False,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "receipt matched",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
        (
            "unreadable receipt is absent",
            "import-break",
            Termination.FAILED,
            ReasonCode.COMMAND_FAILED,
            NONCE.encode(),
            True,
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "no receipt",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
    ),
    ids=lambda row: row if isinstance(row, str) and " " in row else None,
)
def test_transform_attribution_table(
    tmp_path: Path,
    monkeypatch,
    name: str,
    mechanism: str,
    termination: Termination,
    observed_reason: ReasonCode | None,
    receipt_bytes: bytes | None,
    receipt_unreadable: bool,
    expected_transform: Outcome,
    expected_observed: ReasonCode | None,
    description_fragment: str,
    expected_attempt: Outcome,
    expected_attempt_reason: ReasonCode | None,
):
    receipt_path = tmp_path / "receipt"
    if receipt_bytes is not None:
        receipt_path.write_bytes(receipt_bytes)
    description = (
        "appended uncovered lines"
        if mechanism == "uncovered-line"
        else "inserted import break"
    )
    if receipt_unreadable:

        def denied_open(self, *args, **kwargs):
            raise PermissionError("receipt is unreadable")

        monkeypatch.setattr(Path, "open", denied_open)

    transformed, transformed_reason, final_description = canary._attribute_transform(
        mechanism=mechanism,
        description=description,
        termination=termination,
        observed_reason_code=observed_reason,
        receipt_path=receipt_path,
        nonce=NONCE,
    )

    assert transformed is expected_transform, name
    assert transformed_reason is expected_observed, name
    assert description_fragment in final_description, name
    assert _attempt(
        outcome=transformed,
        observed_reason=transformed_reason,
        expected_reason=(
            ReasonCode.COMMAND_FAILED
            if mechanism == "import-break"
            else ReasonCode.UNCOVERED_LINES
        ),
        description=final_description,
    ) == (expected_attempt, expected_attempt_reason), name


def test_receipt_env_is_transform_only_and_receipt_dir_is_external_and_removed(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    head = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        canary, "secrets", SimpleNamespace(token_hex=lambda size: NONCE), raising=False
    )
    calls: list[tuple[dict[str, str], Path, bool, int | None]] = []

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        transformed = "assay-canary-import-break" in source
        receipt_path = env.get(RECEIPT_ENV)
        receipt_mode = (
            stat.S_IMODE(Path(receipt_path).parent.stat().st_mode)
            if transformed and receipt_path is not None
            else None
        )
        calls.append((dict(env), Path(cwd), transformed, receipt_mode))
        if transformed and receipt_path is not None:
            Path(receipt_path).write_text(NONCE, encoding="ascii")
        return completed_process(
            list(argv),
            1 if transformed else 0,
            NONCE if transformed else "",
            "",
        )

    lane = _lane(git_repo)
    plan = make_plan(lane)
    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=plan,
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process,
            clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
        )

    assert len(calls) == 2
    assert RECEIPT_ENV not in calls[0][0]
    assert calls[1][0][RECEIPT_ENV]
    assert calls[0][3] is None
    assert calls[1][3] == 0o700
    assert RECEIPT_ENV not in plan.env_effective
    receipt_path = Path(calls[1][0][RECEIPT_ENV])
    assert not receipt_path.is_relative_to(calls[1][1])
    assert not receipt_path.exists()
    assert not receipt_path.parent.exists()
    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "receipt matched" in attempt.description
    assert NONCE not in attempt.description
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)


def test_legacy_receipt_dir_is_system_temp(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    head = _seed(git_repo)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(
        canary.tempfile, "gettempdir", lambda: os.fspath(system_temp)
    )
    real_mkdtemp = tempfile.mkdtemp
    receipt_dirs: list[tuple[Path, str | None]] = []

    def capture_mkdtemp(*, prefix: str, dir: str | None = None) -> str:
        path = Path(real_mkdtemp(prefix=prefix, dir=dir))
        receipt_dirs.append((path, dir))
        return os.fspath(path)

    monkeypatch.setattr(canary.tempfile, "mkdtemp", capture_mkdtemp)
    lane = _lane(git_repo)

    def process(argv, *, env, cwd, timeout):
        receipt_path = env.get(RECEIPT_ENV)
        if receipt_path is not None:
            Path(receipt_path).write_bytes(NONCE.encode("ascii"))
        return completed_process(list(argv), 1 if receipt_path else 0, "", "")

    pipeline_calls = 0

    def pipeline(lane, *, project_root, process_runner, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        transformed = "assay-canary-import-break" in (
            project_root / "pkg/mod.py"
        ).read_text(encoding="utf-8")
        if transformed:
            process_runner(("check",), env={}, cwd=project_root, timeout=None)
        outcome = Outcome.FAIL if transformed else Outcome.PASS
        reason = ReasonCode.COMMAND_FAILED if transformed else None
        return outcome, reason, _command_result(
            outcome=outcome,
            reason_code=reason,
            returncode=1 if transformed else 0,
            process_tree=None,
        )

    monkeypatch.setattr(canary, "_run_pipeline", pipeline)
    canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=head,
        target_path="pkg/mod.py",
        adapter=PythonAdapter(),
        mechanism="import-break",
        process_runner=process,
        clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
    )

    assert pipeline_calls == 2
    assert len(receipt_dirs) == 1
    receipt_dir, dir_argument = receipt_dirs[0]
    assert dir_argument is None
    assert receipt_dir.parent == system_temp
    assert not receipt_dir.exists()


def test_echoed_nonce_without_receipt_does_not_attribute_failure(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    head = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        canary, "secrets", SimpleNamespace(token_hex=lambda size: NONCE), raising=False
    )

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        transformed = "assay-canary-import-break" in source
        return completed_process(
            list(argv),
            1 if transformed else 0,
            NONCE if transformed else "",
            "assay-canary-import-break" if transformed else "",
        )

    lane = _lane(git_repo)
    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process,
            clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
        )

    attempt = result.attempts[0]
    assert attempt.transformed_outcome is Outcome.INCONCLUSIVE
    assert attempt.observed_reason_code is ReasonCode.CANARY_INCONCLUSIVE
    assert canary.judge_attempt(attempt) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )


def test_node_esm_import_break_canary_writes_a_receipt(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write(".gitignore", "node_modules/\n")
    git_repo.write("package.json", '{"type":"module"}\n')
    git_repo.write("src/mod.js", "export function value() { return 1 }\n")
    head = git_repo.commit_all("seed JavaScript receipt canary target")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _lane(
        git_repo,
        target="src/mod.js",
        language="javascript",
        argv=("node", "--input-type=module", "-e", "await import('./src/mod.js')"),
        path=True,
    )

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane, passthrough_source={"PATH": os.environ["PATH"]}),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="src/mod.js",
            adapter=JavaScriptAdapter(),
            process_runner=default_process_runner,
            clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
        )

    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "receipt matched" in attempt.description
    assert NONCE not in attempt.description
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)


@pytest.mark.parametrize(
    "process_runner",
    (default_process_runner, ProcessTreeProcessRunner()),
    ids=("default", "process-tree"),
)
def test_real_pytest_import_break_writes_receipt(
    git_repo: GitRepo, tmp_path: Path, monkeypatch, process_runner
):
    _seed(git_repo)
    git_repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    git_repo.write(
        "tests/test_import_target.py",
        "from pkg import mod\n\n\ndef test_imported_value():\n    assert mod.value() == 1\n",
    )
    head = git_repo.commit_all("add real pytest importer")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        canary, "secrets", SimpleNamespace(token_hex=lambda size: NONCE), raising=False
    )
    lane = _lane(
        git_repo,
        argv=(sys.executable, "-m", "pytest", "tests", "-q"),
    )

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="pkg/mod.py",
            adapter=PythonAdapter(),
            process_runner=process_runner,
            clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
        )

    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "receipt matched" in attempt.description
    assert NONCE not in attempt.description
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)


def test_node_cjs_import_break_canary_writes_a_receipt(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write("src/mod.cjs", "exports.value = 1\n")
    head = git_repo.commit_all("seed CommonJS receipt canary target")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _lane(
        git_repo,
        target="src/mod.cjs",
        language="javascript",
        argv=("node", "-e", "require('./src/mod.cjs')"),
        path=True,
    )

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        result = canary.run_isolated_canary(
            lane,
            prepared=prepared,
            plan=make_plan(lane, passthrough_source={"PATH": os.environ["PATH"]}),
            deadline=make_deadline(),
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            target="src/mod.cjs",
            adapter=JavaScriptAdapter(),
            process_runner=default_process_runner,
            clock=lambda: datetime.fromisoformat(MOMENT_TEXT),
        )

    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "receipt matched" in attempt.description
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)


def test_transform_oserror_through_receipt_wrapper_is_lane_error(tmp_path: Path):
    class ProcessTreeRunner:
        is_process_tree_runner = True

        def __call__(self, argv, *, env, cwd, timeout):
            raise OSError("process-tree observer failed")

    lane = make_lane()
    plan = make_plan(lane)
    wrapped_runner = canary._receipt_runner(
        ProcessTreeRunner(), tmp_path / "receipt"
    )

    with pytest.raises(AssayError, match="process execution infrastructure failed"):
        execute_plan(
            plan,
            cwd=tmp_path,
            timeout=1,
            process_runner=wrapped_runner,
        )
