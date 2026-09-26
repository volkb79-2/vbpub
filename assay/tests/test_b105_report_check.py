"""Behavioral checks for B105's independent verdict identity gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT, REPO_ROOT
from assay.verify import verify_document

CHECKER = PROJECT_ROOT / "tools" / "b105_report_check.py"
VERSION = "7.1.1.dev-b105"
WHEEL_SHA256 = hashlib.sha256(b"selected assay wheel").hexdigest()


def _git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _verifier_valid_report(lane: str, rigor: tuple[str, ...]) -> dict:
    fixture_dir = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"
    document = json.loads((fixture_dir / "pass.json").read_text(encoding="utf-8"))
    if lane == "self-qualification":
        r2 = json.loads((fixture_dir / "r2_pass.json").read_text(encoding="utf-8"))
        r3 = json.loads((fixture_dir / "r3_pass.json").read_text(encoding="utf-8"))
        document["claims"].extend(
            [deepcopy(r2["claims"][1]), deepcopy(r3["claims"][1])]
        )
        document["judgment"].update(
            {
                key: deepcopy(value)
                for key, value in r2["judgment"].items()
                if key != "resolved"
            }
        )
        document["judgment"].update(
            {
                key: deepcopy(value)
                for key, value in r3["judgment"].items()
                if key != "resolved"
            }
        )
        document["judgment"]["resolved"]["source_roots"] = ["pkg"]
    document.update(
        {
            "assay_version": VERSION,
            "lane": lane,
            "commit": _git_value("rev-parse", "HEAD"),
            "declared_rigor": list(rigor),
            "judge_provenance": {
                "artifact": "wheel",
                "name": "assay",
                "digest_algorithm": "sha256",
                "digest": WHEEL_SHA256,
                "version": VERSION,
            },
        }
    )
    failures = verify_document(document)
    assert failures == [], f"positive fixture is not accepted by assay verify: {failures}"
    return document


def _verify_with_assay_cli(tmp_path: Path, document: dict) -> None:
    report_path = tmp_path / "assay-verified.json"
    report_path.write_text(json.dumps(document), encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "assay.cli", "verify", str(report_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _run_checker(
    tmp_path: Path,
    document: dict,
    *,
    lane: str,
    rigor: tuple[str, ...],
    expected_tree: str | None = None,
    producer_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "verdict.json"
    report_path.write_text(json.dumps(document), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--report",
            str(report_path),
            "--repo-root",
            str(REPO_ROOT),
            "--expected-commit",
            _git_value("rev-parse", "HEAD"),
            "--expected-tree",
            expected_tree or _git_value("rev-parse", "HEAD^{tree}"),
            "--expected-lane",
            lane,
            "--expected-rigor",
            ",".join(rigor),
            "--expected-version",
            VERSION,
            "--expected-wheel-sha256",
            WHEEL_SHA256,
            "--producer-exit",
            str(producer_exit),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("lane", "rigor"),
    [
        ("self-qualification-preflight", ("R0", "R1")),
        ("self-qualification", ("R0", "R1", "R2", "R3")),
    ],
)
def test_accepts_only_exact_pass_for_both_registered_lanes(
    tmp_path, lane, rigor
):
    document = _verifier_valid_report(lane, rigor)
    _verify_with_assay_cli(tmp_path, document)
    result = _run_checker(
        tmp_path, document, lane=lane, rigor=rigor
    )

    assert result.returncode == 0, result.stderr
    assert f"B105_REPORT_ACCEPTED={lane}" in result.stdout


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("commit", "0" * 40, "verdict commit"),
        ("lane", "tester-unified", "verdict lane"),
        ("declared_rigor", ["R0"], "declared_rigor"),
        ("outcome", "FAIL", "outcome"),
        ("exit_code", 1, "exit_code"),
        (
            "claims",
            [{"rigor": "R0", "status": "PASS"}, {"rigor": "R1", "status": "FAIL"}],
            "claim 'R1'",
        ),
        (
            "judge_provenance",
            {
                "artifact": "wheel",
                "name": "assay",
                "digest_algorithm": "sha256",
                "digest": "0" * 64,
                "version": VERSION,
            },
            "judge_provenance.digest",
        ),
    ],
)
def test_rejects_report_that_is_not_the_exact_expected_pass(
    tmp_path, field, value, message
):
    lane = "self-qualification-preflight"
    rigor = ("R0", "R1")
    document = _verifier_valid_report(lane, rigor)
    document[field] = value

    result = _run_checker(tmp_path, document, lane=lane, rigor=rigor)

    assert result.returncode == 2
    assert message in result.stderr


def test_rejects_a_commit_whose_tree_differs_from_the_captured_tree(tmp_path):
    lane = "self-qualification-preflight"
    rigor = ("R0", "R1")

    result = _run_checker(
        tmp_path,
        _verifier_valid_report(lane, rigor),
        lane=lane,
        rigor=rigor,
        expected_tree="0" * 40,
    )

    assert result.returncode == 2
    assert "captured commit tree" in result.stderr


def test_rejects_nonzero_producer_exit_even_with_a_pass_report(tmp_path):
    lane = "self-qualification-preflight"
    rigor = ("R0", "R1")
    document = _verifier_valid_report(lane, rigor)
    _verify_with_assay_cli(tmp_path, document)

    result = _run_checker(
        tmp_path,
        document,
        lane=lane,
        rigor=rigor,
        producer_exit=1,
    )

    assert result.returncode == 2
    assert "producer exit" in result.stderr
