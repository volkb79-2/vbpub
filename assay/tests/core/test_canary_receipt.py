"""WP3: R3 import-break attribution uses a per-attempt execution receipt."""

from __future__ import annotations

import os
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
from assay.errors import Outcome, ReasonCode
from assay.process_tree import ProcessTreeEvidence
from assay.runner import CommandResult, default_process_runner
from assay.termination import Termination
from assay.verdict import CanaryAttempt
from termination_table import LIMITS_CLEAR, TERMINATION_CASES

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
    assert "nonce" in signature(method).parameters

    transformed, _ = method(source, nonce=NONCE)

    for marker in receipt_markers:
        assert marker in transformed


def test_sql_adapter_keeps_import_break_unreachable_with_the_new_signature():
    with pytest.raises(NotImplementedError, match="inject_import_break"):
        SqlAdapter().inject_import_break("SELECT 1;", nonce=NONCE)


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


@pytest.mark.parametrize("case", TERMINATION_CASES, ids=lambda case: case.name)
def test_canary_command_classification_matches_the_shared_termination_table(case):
    process_tree = case.inputs["process_tree"]
    observation_status = case.inputs["observation"]
    limits = case.inputs["limits"]
    observation = (
        None
        if observation_status == "not_requested"
        else ContentionObservation(
            observation_status,
            limits=(limits or LIMITS_CLEAR) if observation_status == "clear" else None,
            reasons=("observer-unavailable",)
            if observation_status == "unavailable"
            else (),
        )
    )
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
    assert canary._attribute_control(classified) is (
        Outcome.PASS if case.termination is Termination.PASSED else Outcome.INCONCLUSIVE
    )


@pytest.mark.parametrize(
    (
        "name",
        "mechanism",
        "termination",
        "observed_reason",
        "receipt_bytes",
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
            Outcome.PASS,
            None,
            "appended uncovered lines",
            Outcome.FAIL,
            ReasonCode.CANARY_SURVIVED,
        ),
        (
            "infrastructure failure remains inconclusive",
            "import-break",
            Termination.LIMIT_EVENT,
            ReasonCode.COMMAND_FAILED,
            NONCE.encode(),
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
            "receipt matched",
            Outcome.INCONCLUSIVE,
            ReasonCode.CANARY_INCONCLUSIVE,
        ),
    ),
    ids=lambda row: row if isinstance(row, str) else None,
)
def test_transform_attribution_table(
    tmp_path: Path,
    name: str,
    mechanism: str,
    termination: Termination,
    observed_reason: ReasonCode | None,
    receipt_bytes: bytes | None,
    expected_transform: Outcome,
    expected_observed: ReasonCode | None,
    description_fragment: str,
    expected_attempt: Outcome,
    expected_attempt_reason: ReasonCode | None,
):
    receipt_path = tmp_path / "receipt"
    if receipt_bytes is not None:
        receipt_path.write_bytes(receipt_bytes)
    description = "appended uncovered lines" if mechanism == "uncovered-line" else "inserted import break"

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


def test_unreadable_receipt_is_treated_as_absent(tmp_path: Path, monkeypatch):
    receipt_path = tmp_path / "receipt"
    receipt_path.write_bytes(NONCE.encode())

    def denied_open(self, *args, **kwargs):
        raise PermissionError("receipt is unreadable")

    monkeypatch.setattr(Path, "open", denied_open)
    transformed, reason, description = canary._attribute_transform(
        mechanism="import-break",
        description="inserted import break",
        termination=Termination.FAILED,
        observed_reason_code=ReasonCode.COMMAND_FAILED,
        receipt_path=receipt_path,
        nonce=NONCE,
    )

    assert transformed is Outcome.INCONCLUSIVE
    assert reason is ReasonCode.CANARY_INCONCLUSIVE
    assert "no receipt" in description


def test_receipt_env_is_transform_only_and_receipt_dir_is_external_and_removed(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    head = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        canary, "secrets", SimpleNamespace(token_hex=lambda size: NONCE), raising=False
    )
    calls: list[tuple[dict[str, str], Path, bool]] = []

    def process(argv, *, env, cwd, timeout):
        source = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        transformed = "assay-canary-import-break" in source
        calls.append((dict(env), Path(cwd), transformed))
        receipt_path = env.get(RECEIPT_ENV)
        if transformed and receipt_path is not None:
            Path(receipt_path).write_text(NONCE, encoding="ascii")
        return completed_process(
            list(argv),
            1 if transformed else 0,
            NONCE if transformed else "",
            "",
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

    assert len(calls) == 2
    assert RECEIPT_ENV not in calls[0][0]
    assert calls[1][0][RECEIPT_ENV]
    receipt_path = Path(calls[1][0][RECEIPT_ENV])
    assert not receipt_path.is_relative_to(calls[1][1])
    assert not receipt_path.exists()
    assert not receipt_path.parent.exists()
    attempt = result.attempts[0]
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert "receipt matched" in attempt.description
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)


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
    assert canary.judge_attempt(attempt) == (Outcome.PASS, None)
