"""Behavioral checks for B105's independent verdict identity gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT, REPO_ROOT
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
    targets = tomllib.loads((PROJECT_ROOT / "assay.toml").read_text(encoding="utf-8"))[
        "lanes"
    ][lane]["judge"]["targets"]
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
        document["judgment"]["r2"].update({"mode": "whole_target", "targets": targets})
        document["judgment"]["r3"]["targets"] = ["src/assay/cli.py"]
        document["claims"][3]["canary"]["attempts"][0]["target"] = "src/assay/cli.py"
    document["judgment"]["resolved"] = {"language": "python", "source_roots": ["src/assay"]}
    document["judgment"]["r1"].update(
        {"mode": "whole_target", "targets": targets, "require_branch": True}
    )
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


_AUTO_RECEIPT = object()


def _receipt(commit: str, tree: str) -> dict:
    """The exact document the registered gate writes after a green run."""
    return {"schema_version": 1, "lane": "tester-unified", "commit": commit, "tree": tree}


def _run_checker(
    tmp_path: Path,
    document: dict,
    *,
    lane: str,
    rigor: tuple[str, ...],
    expected_tree: str | None = None,
    producer_exit: int = 0,
    checker: Path = CHECKER,
    repo_root: Path = REPO_ROOT,
    receipt: object = _AUTO_RECEIPT,
) -> subprocess.CompletedProcess[str]:
    """Run the checker in full mode.

    ``receipt``: by default the lane ``self-qualification`` gets the matching
    same-commit receipt and every other lane none; ``None`` passes no flag; a
    ``Path`` is passed as is; anything else is written as the receipt's JSON.
    """
    report_path = tmp_path / "verdict.json"
    report_path.write_text(json.dumps(document), encoding="utf-8")
    commit = _git_value("-C", str(repo_root), "rev-parse", "HEAD")
    tree = expected_tree or _git_value("-C", str(repo_root), "rev-parse", "HEAD^{tree}")
    if receipt is _AUTO_RECEIPT:
        receipt = _receipt(commit, tree) if lane == "self-qualification" else None
    receipt_flags: list[str] = []
    if isinstance(receipt, Path):
        receipt_flags = ["--tester-unified-receipt", str(receipt)]
    elif receipt is not None:
        receipt_path = tmp_path / "tester-unified.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_flags = ["--tester-unified-receipt", str(receipt_path)]
    return subprocess.run(
        [
            sys.executable,
            str(checker),
            "--report",
            str(report_path),
            "--repo-root",
            str(repo_root),
            "--expected-commit",
            commit,
            "--expected-tree",
            tree,
            *receipt_flags,
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
        cwd=repo_root,
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


# --- scope: B105 measures exactly the tracked src/assay (A-478) -----------------

PREFLIGHT = "self-qualification-preflight"
PREFLIGHT_RIGOR = ("R0", "R1")


def test_rejects_an_analysis_target_naming_the_decision(tmp_path):
    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    document["judgment"]["r1"]["targets"].append("analysis/src/assay_analysis/cli.py")

    result = _run_checker(tmp_path, document, lane=PREFLIGHT, rigor=PREFLIGHT_RIGOR)

    assert result.returncode == 2
    assert "analysis/src/assay_analysis/cli.py" in result.stderr and "A-478" in result.stderr


def test_rejects_a_declared_target_list_missing_a_tracked_source(tmp_path):
    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    document["judgment"]["r1"]["targets"].remove("src/assay/vocabulary.py")

    result = _run_checker(tmp_path, document, lane=PREFLIGHT, rigor=PREFLIGHT_RIGOR)

    assert result.returncode == 2
    assert "missing ['src/assay/vocabulary.py']" in result.stderr


def test_rejects_a_verdict_measured_over_the_whole_src_directory(tmp_path):
    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    document["judgment"]["resolved"]["source_roots"] = ["src"]

    result = _run_checker(tmp_path, document, lane=PREFLIGHT, rigor=PREFLIGHT_RIGOR)

    assert result.returncode == 2
    assert "judgment.resolved.source_roots" in result.stderr


def test_rejects_an_r3_target_outside_src_assay(tmp_path):
    lane, rigor = "self-qualification", ("R0", "R1", "R2", "R3")
    document = _verifier_valid_report(lane, rigor)
    document["judgment"]["r3"]["targets"] = ["tools/b105_report_check.py"]

    result = _run_checker(tmp_path, document, lane=lane, rigor=rigor)

    assert result.returncode == 2
    assert "judgment.r3.targets" in result.stderr


def _hermetic_repo(
    tmp_path: Path,
    *,
    analysis_init: bool = True,
    tracked: tuple[str, ...] = (),
    untracked: tuple[str, ...] = (),
) -> Path:
    """A throwaway repository holding a copy of the checker at ``assay/tools/``."""
    repo = tmp_path / "hermetic"
    files = {
        "assay/src/assay/__init__.py": "",
        "assay/src/assay/mod.py": "",
        **{name: "" for name in tracked},
    }
    if analysis_init:
        files["assay/analysis/src/assay_analysis/__init__.py"] = ""
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    (repo / "assay" / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy(CHECKER, repo / "assay" / "tools" / CHECKER.name)
    for args in (
        ("init", "-q"),
        ("add", "-A"),
        ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    for name in untracked:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("", encoding="utf-8")
    return repo


def _run_hermetic(tmp_path: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    document["commit"] = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    document["judgment"]["r1"]["targets"] = ["src/assay/__init__.py", "src/assay/mod.py"]
    return _run_checker(
        tmp_path,
        document,
        lane=PREFLIGHT,
        rigor=PREFLIGHT_RIGOR,
        checker=repo / "assay" / "tools" / CHECKER.name,
        repo_root=repo,
    )


def test_a_tracked_tree_that_matches_the_declared_scope_is_accepted(tmp_path):
    result = _run_hermetic(tmp_path, _hermetic_repo(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(
        " scope=src/assay out_of_scope=analysis/src/assay_analysis:A-478"
    )


def test_an_untracked_extra_package_changes_nothing(tmp_path):
    repo = _hermetic_repo(tmp_path, untracked=("assay/src/extra/x.py",))

    assert _run_hermetic(tmp_path, repo).returncode == 0


def test_a_committed_unclassified_package_under_src_is_refused(tmp_path):
    repo = _hermetic_repo(tmp_path, tracked=("assay/src/extra/x.py",))

    result = _run_hermetic(tmp_path, repo)

    assert result.returncode == 2
    assert "unclassified package under src/: extra" in result.stderr


def test_an_out_of_scope_declaration_without_a_tracked_package_is_stale(tmp_path):
    result = _run_hermetic(tmp_path, _hermetic_repo(tmp_path, analysis_init=False))

    assert result.returncode == 2
    assert "stale out-of-scope declaration: analysis/src/assay_analysis" in result.stderr


def test_a_committed_source_missing_from_the_declared_targets_is_named(tmp_path):
    repo = _hermetic_repo(tmp_path, tracked=("assay/src/assay/new.py",))

    result = _run_hermetic(tmp_path, repo)

    assert result.returncode == 2
    assert "missing ['src/assay/new.py']" in result.stderr


# --- S1: the same-commit tester-unified receipt (B123) --------------------------

COMMIT = "a" * 40
TREE = "b" * 40


def _run_receipt_only(tmp_path: Path, receipt: object, *, commit: str = COMMIT, tree: str = TREE, extra=()):
    """``--receipt-only`` on a receipt file (or on the named path when a ``Path``)."""
    if isinstance(receipt, Path):
        path = receipt
    else:
        path = tmp_path / "tester-unified.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--receipt-only",
            "--tester-unified-receipt",
            str(path),
            "--expected-commit",
            commit,
            "--expected-tree",
            tree,
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_the_receipt_only_mode_accepts_the_exact_receipt(tmp_path):
    result = _run_receipt_only(tmp_path, _receipt(COMMIT, TREE))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"B105_TESTER_UNIFIED_PASS=commit={COMMIT} tree={TREE}\n"
    assert result.stderr == ""


def test_the_receipt_only_mode_accepts_sha256_length_object_names(tmp_path):
    commit, tree = "c" * 64, "d" * 64

    result = _run_receipt_only(tmp_path, _receipt(commit, tree), commit=commit, tree=tree)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("label", "receipt"),
    [
        ("other commit", _receipt("1" * 40, TREE)),
        ("other tree", _receipt(COMMIT, "2" * 40)),
        ("uppercase hex of the right commit", _receipt(COMMIT.upper(), TREE)),
        ("right commit plus 24 extra hex digits", _receipt(COMMIT + "0" * 24, TREE)),
        ("right tree plus 24 extra hex digits", _receipt(COMMIT, TREE + "0" * 24)),
        ("another lane", {**_receipt(COMMIT, TREE), "lane": "self-qualification"}),
        ("schema_version True", {**_receipt(COMMIT, TREE), "schema_version": True}),
        ("schema_version 2", {**_receipt(COMMIT, TREE), "schema_version": 2}),
        ("an extra key", {**_receipt(COMMIT, TREE), "assay_version": "7.2.0"}),
        ("a missing key", {"schema_version": 1, "lane": "tester-unified", "commit": COMMIT}),
        ("a non-object", [_receipt(COMMIT, TREE)]),
        ("null", None),
    ],
)
def test_the_receipt_only_mode_refuses_anything_but_the_exact_receipt(tmp_path, label, receipt):
    result = _run_receipt_only(tmp_path, receipt)

    assert result.returncode == 2, label
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert result.stdout == ""


def test_the_receipt_only_mode_refuses_a_missing_file(tmp_path):
    result = _run_receipt_only(tmp_path, tmp_path / "absent.json")

    assert result.returncode == 2
    assert result.stderr.startswith("B105_REPORT_REJECTED=")
    assert "absent.json" in result.stderr


def test_the_receipt_only_mode_refuses_text_that_is_not_json(tmp_path):
    path = tmp_path / "tester-unified.json"
    path.write_text("COMPLETE=1\n", encoding="utf-8")

    result = _run_receipt_only(tmp_path, path)

    assert result.returncode == 2
    assert result.stderr.startswith("B105_REPORT_REJECTED=")


def test_the_receipt_only_mode_takes_no_report_flags(tmp_path):
    result = _run_receipt_only(tmp_path, _receipt(COMMIT, TREE), extra=("--expected-lane", "self-qualification"))

    assert result.returncode == 2
    assert "--expected-lane" in result.stderr
    assert "B105_TESTER_UNIFIED_PASS" not in result.stdout


def test_the_full_mode_names_every_missing_flag(tmp_path):
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--report", str(tmp_path / "verdict.json")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    for flag in (
        "--repo-root", "--expected-commit", "--expected-tree", "--expected-lane",
        "--expected-rigor", "--expected-version", "--expected-wheel-sha256", "--producer-exit",
    ):
        assert flag in result.stderr
    assert "--report," not in result.stderr


SELF_QUALIFICATION = "self-qualification"
SELF_QUALIFICATION_RIGOR = ("R0", "R1", "R2", "R3")


def _own_commit_and_tree() -> tuple[str, str]:
    return _git_value("-C", str(REPO_ROOT), "rev-parse", "HEAD"), _git_value("-C", str(REPO_ROOT), "rev-parse", "HEAD^{tree}")


@pytest.mark.parametrize("receipt_kind", ["other-commit", "other-tree", "no-flag", "missing-file"])
def test_a_valid_full_report_is_refused_without_this_commits_receipt(tmp_path, receipt_kind):
    """S1: the report is exactly valid (the same one the accepting test uses), so
    only the receipt can be what refuses it."""
    document = _verifier_valid_report(SELF_QUALIFICATION, SELF_QUALIFICATION_RIGOR)
    _verify_with_assay_cli(tmp_path, document)
    commit, tree = _own_commit_and_tree()
    receipt = {
        "other-commit": _receipt("1" * 40, tree),
        "other-tree": _receipt(commit, "2" * 40),
        "no-flag": None,
        "missing-file": tmp_path / "absent.json",
    }[receipt_kind]

    result = _run_checker(tmp_path, document, lane=SELF_QUALIFICATION, rigor=SELF_QUALIFICATION_RIGOR, receipt=receipt)

    assert result.returncode == 2
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert "B105_REPORT_ACCEPTED" not in result.stdout


def test_the_receipt_is_checked_before_the_report(tmp_path):
    """A report that would itself be refused is reported as the receipt's refusal:
    the S1 requirement is not something a good report can talk its way past."""
    document = _verifier_valid_report(SELF_QUALIFICATION, SELF_QUALIFICATION_RIGOR)
    document["outcome"] = "FAIL"

    result = _run_checker(tmp_path, document, lane=SELF_QUALIFICATION, rigor=SELF_QUALIFICATION_RIGOR, receipt=None)

    assert result.returncode == 2
    assert "requires --tester-unified-receipt" in result.stderr


def test_the_preflight_lane_takes_no_receipt(tmp_path):
    lane, rigor = PREFLIGHT, PREFLIGHT_RIGOR
    document = _verifier_valid_report(lane, rigor)
    _verify_with_assay_cli(tmp_path, document)
    commit, tree = _own_commit_and_tree()

    result = _run_checker(tmp_path, document, lane=lane, rigor=rigor, receipt=_receipt(commit, tree))

    assert result.returncode == 2
    assert "takes no --tester-unified-receipt" in result.stderr
