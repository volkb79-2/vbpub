"""Behavioral checks for B105's independent verdict identity gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import shutil
import sys
import tomllib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT, REPO_ROOT
from assay.verify import verify_document

CHECKER = PROJECT_ROOT / "tools" / "b105_report_check.py"
VERSION = "7.1.1.dev-b105"
WHEEL_SHA256 = hashlib.sha256(b"selected assay wheel").hexdigest()


def test_checker_git_argv_disables_automatic_maintenance():
    spec = importlib.util.spec_from_file_location("b105_report_check_test", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = module._git_argv("rev-parse", "HEAD")
    pairs = tuple(zip(argv, argv[1:]))

    assert ("-c", "maintenance.auto=false") in pairs
    assert ("-c", "maintenance.autoDetach=false") in pairs
    assert ("-c", "gc.autoDetach=false") in pairs


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
    committed_assay_toml = tomllib.loads(
        _committed_source(_git_value("rev-parse", "HEAD"), "assay/assay.toml").decode("utf-8")
    )
    targets = committed_assay_toml["lanes"][lane]["judge"]["targets"]
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
    if lane == "self-qualification":
        committed_lane = tomllib.loads(
            _committed_source(document["commit"], "assay/assay.toml").decode("utf-8")
        )["lanes"][lane]
        argv = committed_lane["argv"]
        transformed = [
            token
            for token in argv
            if token != "--cov-branch"
            and not (token.startswith("--cov=") and len(token) > len("--cov="))
            and not (
                token.startswith("--cov-report=")
                and len(token) > len("--cov-report=")
            )
        ]
        manifest = _manifest_bytes()
        collection_sha = _manifest_digest(manifest)
        common = {
            "collection_count": 2,
            "collection_sha256": collection_sha,
            "duplicates": 0,
            "hook_fingerprint_sha256": "a" * 64,
            "hook_count": 3,
            "runtime_fingerprint_sha256": "b" * 64,
        }
        document.update(
            {
                "argv_declared": argv,
                "argv_appended": [],
                "argv_effective": argv,
                "argv_modified": False,
                "env_declared": {},
                "env_effective": {},
                "env_passthrough": ["PATH"],
            }
        )
        r2_policy = document["judgment"]["r2"]
        r2_policy["cold_witness_kills"] = True
        r2_policy["r2_command"] = {
            "transform": "assay-r2-pytest-nocov/1",
            "argv_declared": argv,
            "argv_transformed": transformed,
            "appended": ["-p", "no:pytest_cov"],
            "cwd": "assay",
            "config_sha256": hashlib.sha256(
                _committed_source(document["commit"], "assay/pyproject.toml")
            ).hexdigest(),
            "coverage_baseline": common,
            "r2_baseline": {
                **common,
                "hook_fingerprint_sha256": "c" * 64,
                "wall_s": 1.25,
            },
        }

    started = datetime.fromisoformat(document["started"])
    ended = datetime.fromisoformat(document["ended"])
    created = (started - timedelta(minutes=1)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    expires = (ended + timedelta(minutes=1)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    tree = _git_value("rev-parse", "HEAD^{tree}")
    document["campaign"] = {
        "name": f"b105-test-{document['commit'][:12]}",
        "deadline_sha256": "",
        "created_at_utc": created,
        "expires_at_utc": expires,
    }
    document["campaign"]["deadline_sha256"] = hashlib.sha256(
        _deadline_bytes(document, document["commit"], tree)
    ).hexdigest()
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
_AUTO_PLAN = object()
_AUTO_MANIFEST = object()


def _committed_source(commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout


def _deadline_bytes(document: dict, commit: str, tree: str) -> bytes:
    binding = document.get("campaign") or {}
    deadline = {
        "schema": "assay-campaign-deadline/1",
        "campaign": binding.get("name", "b105-test"),
        "commit": commit,
        "git_tree": tree,
        "created_at_utc": binding.get("created_at_utc", "2026-08-06T08:59:00Z"),
        "expires_at_utc": binding.get("expires_at_utc", "2026-08-06T10:01:00Z"),
        "lanes": ["self-qualification", "self-qualification-preflight"],
        "plan_sha256": {
            "self-qualification": None,
            "self-qualification-preflight": None,
        },
        "assay_version": VERSION,
        "wheel_sha256": WHEEL_SHA256,
    }
    return json.dumps(deadline, sort_keys=True, indent=2).encode("utf-8")


def _manifest_bytes() -> bytes:
    return b"tests/test_a.py::test_a\ntests/test_b.py::test_b\n"


def _manifest_digest(raw: bytes) -> str:
    digest = hashlib.sha256()
    for line in raw.splitlines():
        digest.update(str(len(line)).encode("ascii"))
        digest.update(b":")
        digest.update(line)
        digest.update(b",")
    return digest.hexdigest()


def _plan_for(document: dict, commit: str, tree: str) -> dict:
    """The unsharded ``assay plan`` inventory that agrees with the report's R2 claim."""
    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    identities = claim["mutation"]["candidate_ids"]
    return {
        "status": "ok",
        "commit": commit,
        "tree": tree,
        "shard": None,
        "candidate_count": len(identities),
        "candidates": [{"id": identity} for identity in identities],
    }


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
    plan: object = _AUTO_PLAN,
    manifest: object = _AUTO_MANIFEST,
    deadline_raw: bytes | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the checker in full mode.

    ``receipt``: by default the lane ``self-qualification`` gets the matching
    same-commit receipt and every other lane none; ``None`` passes no flag; a
    ``Path`` is passed as is; anything else is written as the receipt's JSON.
    ``plan`` works the same way: by default a rigor holding R2 gets the plan
    that agrees with the report and this commit and tree, any other rigor none;
    a ``str`` is written as the file's raw text.
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
    if plan is _AUTO_PLAN:
        plan = _plan_for(document, commit, tree) if "R2" in rigor else None
    plan_flags: list[str] = []
    if isinstance(plan, Path):
        plan_flags = ["--plan-json", str(plan)]
    elif plan is not None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
        plan_flags = ["--plan-json", str(plan_path)]
    if manifest is _AUTO_MANIFEST:
        manifest = _manifest_bytes() if "R2" in rigor else None
    manifest_flags: list[str] = []
    if isinstance(manifest, Path):
        manifest_flags = ["--r2-manifest", str(manifest)]
    elif isinstance(manifest, bytes):
        manifest_path = tmp_path / "r2-manifest.txt"
        manifest_path.write_bytes(manifest)
        manifest_flags = ["--r2-manifest", str(manifest_path)]
    elif manifest is not None:
        raise TypeError("manifest must be bytes, Path or None")
    deadline_path = tmp_path / "campaign-deadline.json"
    deadline_path.write_bytes(
        deadline_raw if deadline_raw is not None else _deadline_bytes(document, commit, tree)
    )
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
            "--deadline",
            str(deadline_path),
            *receipt_flags,
            *plan_flags,
            *manifest_flags,
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
    tree = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    document["campaign"]["deadline_sha256"] = hashlib.sha256(
        _deadline_bytes(document, document["commit"], tree)
    ).hexdigest()
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
    reason = {
        "other-commit": "tester-unified receipt commit",
        "other-tree": "tester-unified receipt tree",
        "no-flag": "requires --tester-unified-receipt",
        "missing-file": "absent.json",
    }[receipt_kind]
    assert reason in result.stderr, result.stderr


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


# --- campaign scope: the R2 claim is the complete, unsharded plan (W8 C, O11) ---------


def _r2_case():
    """A valid self-qualification report, and the plan that agrees with it."""
    document = _verifier_valid_report(SELF_QUALIFICATION, SELF_QUALIFICATION_RIGOR)
    commit, tree = _own_commit_and_tree()
    return document, _plan_for(document, commit, tree)


def _refused_by_scope(tmp_path, document, plan, message):
    result = _run_checker(
        tmp_path, document, lane=SELF_QUALIFICATION, rigor=SELF_QUALIFICATION_RIGOR, plan=plan
    )
    assert result.returncode == 2, result.stdout
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert message in result.stderr, result.stderr
    assert "B105_REPORT_ACCEPTED" not in result.stdout


def test_o11_the_plan_that_agrees_with_the_report_is_accepted(tmp_path):
    document, plan = _r2_case()
    result = _run_checker(
        tmp_path, document, lane=SELF_QUALIFICATION, rigor=SELF_QUALIFICATION_RIGOR, plan=plan
    )
    assert result.returncode == 0, result.stderr
    assert "B105_REPORT_ACCEPTED=self-qualification" in result.stdout


def test_o11_refusal_1_an_r2_report_without_a_plan(tmp_path):
    document, _ = _r2_case()
    _refused_by_scope(tmp_path, document, None, "no plan was given")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "unsupported"}, "plan status is 'unsupported'"),  # 2
        ({"shard": "0/2"}, "plan is sharded ('0/2')"),  # 3
        ({"candidate_count": 3}, "plan candidate_count does not match its candidates"),  # 4
    ],
)
def test_o11_refusals_2_to_4_the_plan_must_be_ok_unsharded_and_consistent(tmp_path, change, message):
    document, plan = _r2_case()
    plan.update(change)
    _refused_by_scope(tmp_path, document, plan, message)


@pytest.mark.parametrize("key", ["shard_index", "shard_count"])
def test_o11_refusal_5_a_sharded_report_is_refused(tmp_path, key):
    document, plan = _r2_case()
    document["judgment"]["r2"][key] = 0
    _refused_by_scope(tmp_path, document, plan, f"judgment.r2.{key} is 0")


@pytest.mark.parametrize(
    "ids",
    [
        "missing",
        "not-a-list",
        ["not-64-hex"],
        ["A" * 64, "b" * 64],
        "duplicates",
    ],
)
def test_o11_refusal_6_the_candidate_ids_must_be_a_list_of_distinct_64_hex_ids(tmp_path, ids):
    document, plan = _r2_case()
    mutation = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
    first = mutation["candidate_ids"][0]
    if ids == "missing":
        del mutation["candidate_ids"]
    elif ids == "duplicates":
        mutation["candidate_ids"] = [first, first]
        plan["candidates"] = [{"id": first}, {"id": first}]
    elif ids == "not-a-list":
        mutation["candidate_ids"] = first
    else:
        mutation["candidate_ids"] = ids
    message = "contains duplicates" if ids == "duplicates" else "missing or not a list of 64-hex ids"
    _refused_by_scope(tmp_path, document, plan, message)


@pytest.mark.parametrize("change", ["drop-from-report", "extra-in-report", "swap-one"])
def test_o11_refusal_7_the_report_ids_must_be_exactly_the_plan_ids(tmp_path, change):
    document, plan = _r2_case()
    mutation = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
    if change == "drop-from-report":
        mutation["candidate_ids"] = mutation["candidate_ids"][:1]
    elif change == "extra-in-report":
        mutation["candidate_ids"] = mutation["candidate_ids"] + ["c" * 64]
    else:
        mutation["candidate_ids"] = mutation["candidate_ids"][:1] + ["d" * 64]
    _refused_by_scope(tmp_path, document, plan, "R2 candidate_ids differ from the plan")


def test_o11_refusal_8_a_plan_without_r2_is_an_argument_refusal_before_the_report_is_read(tmp_path):
    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    commit, tree = _own_commit_and_tree()
    document["outcome"] = "FAIL"  # would be refused if the report were ever read

    result = _run_checker(
        tmp_path, document, lane=PREFLIGHT, rigor=PREFLIGHT_RIGOR,
        plan=_plan_for(_verifier_valid_report(SELF_QUALIFICATION, SELF_QUALIFICATION_RIGOR), commit, tree),
    )

    assert result.returncode == 2
    assert "--plan-json is only valid when R2 is in --expected-rigor" in result.stderr
    assert "B105_REPORT_REJECTED" not in result.stderr


@pytest.mark.parametrize(
    ("label", "mutate", "message"),
    [
        ("list", lambda plan: [], "plan is not an object"),
        ("status-not-a-string", lambda plan: {**plan, "status": 1}, "plan status is not a string"),
        ("bool-count", lambda plan: {**plan, "candidate_count": True}, "plan candidate_count is not an integer"),
        ("candidates-not-a-list", lambda plan: {**plan, "candidates": {}}, "plan candidates is not a list"),
        ("row-not-an-object", lambda plan: {**plan, "candidates": [1]}, "plan candidate 0 is not an object"),
        ("row-without-id", lambda plan: {**plan, "candidates": [{}]}, "plan candidate 0 has no 64-hex id"),
        ("row-id-not-hex", lambda plan: {**plan, "candidates": [{"id": "G" * 64}]}, "plan candidate 0 has no 64-hex id"),
        ("shard-int", lambda plan: {**plan, "shard": 0}, "plan shard is neither null nor a string"),
        ("no-commit", lambda plan: {k: v for k, v in plan.items() if k != "commit"},
         "plan commit/tree differ from the expected source"),
    ],
)
def test_o11_refusal_9_a_structurally_invalid_plan_is_a_named_refusal_not_a_crash(tmp_path, label, mutate, message):
    document, plan = _r2_case()
    result = _run_checker(
        tmp_path, document, lane=SELF_QUALIFICATION, rigor=SELF_QUALIFICATION_RIGOR, plan=mutate(plan)
    )
    assert result.returncode == 2, (label, result.stdout, result.stderr)
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert message in result.stderr and "Traceback" not in result.stderr


def test_o11_refusal_9_an_unreadable_or_non_json_plan_is_refused(tmp_path):
    document, _ = _r2_case()
    for plan, message in (("{not json", "cannot read plan"), (tmp_path / "absent.json", "cannot read plan")):
        _refused_by_scope(tmp_path, document, plan, message)


@pytest.mark.parametrize("key", ["commit", "tree"])
def test_o11_cd41_a_plan_made_at_another_source_is_refused(tmp_path, key):
    document, plan = _r2_case()
    plan[key] = "0" * 40
    _refused_by_scope(tmp_path, document, plan, "plan commit/tree differ from the expected source")


def test_o11_the_documented_refusal_order_is_structure_then_plan_then_report(tmp_path):
    document, plan = _r2_case()
    mutation = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
    mutation["candidate_ids"] = ["c" * 64]  # refusal 7 material
    plan["status"] = "unsupported"  # refusal 2 material
    plan["candidates"] = [{}]  # refusal 9 material
    _refused_by_scope(tmp_path, document, plan, "plan candidate 0 has no 64-hex id")
    plan["candidates"] = []
    plan["candidate_count"] = 0
    _refused_by_scope(tmp_path, document, plan, "plan status is 'unsupported'")
    plan["status"] = "ok"
    _refused_by_scope(tmp_path, document, plan, "R2 candidate_ids differ from the plan")


def test_o11_earlier_refusals_win_over_the_campaign_scope(tmp_path):
    document, plan = _r2_case()
    plan["shard"] = "0/2"  # a refusal 3 plan
    document["assay_version"] = "0.0.0"  # a step 5 refusal
    _refused_by_scope(tmp_path, document, plan, "verdict assay_version")


def _r2_policy(document: dict) -> dict:
    return document["judgment"]["r2"]


def _r2_mutation(document: dict) -> dict:
    return next(item["mutation"] for item in document["claims"] if item["rigor"] == "R2")


@pytest.mark.parametrize(
    ("label", "mutate", "message"),
    [
        ("C1", lambda d: _r2_policy(d).__setitem__("cold_witness_kills", False),
         "B105 R2 requires cold_witness_kills true"),
        ("C2", lambda d: _r2_policy(d).__setitem__("r2_command", None),
         "B105 R2 report has no r2_command"),
        ("C3", lambda d: d["argv_declared"].append("--ignore=other.py"),
         "declared argv differs from assay.toml"),
        ("C4", lambda d: _r2_policy(d)["r2_command"].__setitem__("transform", "guess"),
         "unexpected R2 transform"),
        ("C5", lambda d: _r2_policy(d)["r2_command"]["argv_transformed"].append("--cov=left"),
         "argv_transformed is not the transform of the committed argv"),
        ("C6", lambda d: _r2_policy(d)["r2_command"].__setitem__("appended", []),
         "unexpected R2 appended argv"),
        ("C7", lambda d: _r2_policy(d)["r2_command"].__setitem__("cwd", "."),
         "unexpected R2 cwd"),
        ("C8", lambda d: _r2_policy(d)["r2_command"]["coverage_baseline"].__setitem__("collection_count", 99),
         "R2 and coverage baseline collections differ"),
        ("C9", lambda d: d["env_effective"].__setitem__("ASSAY_B105_SOURCE_COMMIT", "leak"),
         "archive-hook variables present in the qualification run"),
        ("C13", lambda d: _r2_policy(d)["r2_command"].__setitem__("config_sha256", "0" * 64),
         "R2 pytest config differs from assay/pyproject.toml at"),
    ],
)
def test_p3d_v15_source_and_consistency_checks_refuse_each_tamper(
    tmp_path, label, mutate, message
):
    document, plan = _r2_case()
    mutate(document)
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2, (label, result.stdout, result.stderr)
    assert message in result.stderr, result.stderr


def test_p3d_c10_manifest_must_be_well_formed_and_match_the_r2_baseline(tmp_path):
    document, plan = _r2_case()
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
        manifest=b"tests/test_a.py::test_a",
    )
    assert result.returncode == 2
    assert "R2 manifest sidecar does not match r2_baseline" in result.stderr


def test_p3d_c11_every_cold_kill_must_name_the_manifest_entry_at_its_failed_index(tmp_path):
    document, plan = _r2_case()
    mutation = _r2_mutation(document)
    outcome = mutation["killed"][0]
    outcome["execution"] = {
        "mode": "witness-cold",
        "witness": {
            "node_id": "tests/test_wrong.py::test_wrong",
            "when": "call",
            "outcome": "failed",
            "session_exit_status": 1,
            "process_exit_status": 1,
        },
    }
    outcome["evidence"] = {"started_count": 1, "failed_call_index": 0}
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2
    assert f"cold kill {outcome['candidate_id']} is not the manifest's node at its failed index" in result.stderr


def test_p3d_c12_survivor_evidence_must_match_its_selected_baseline(tmp_path):
    document, plan = _r2_case()
    mutation = _r2_mutation(document)
    outcome = mutation["killed"].pop()
    mutation["survived"].append(outcome)
    outcome["execution"] = {"mode": "full"}
    outcome["evidence"] = {
        "command": "r2",
        "collection_sha256": "f" * 64,
    }
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2
    assert "candidate evidence collection differs from r2_baseline" in result.stderr


def test_p3d_deadline_checks_bind_the_report_and_fail_closed(tmp_path):
    document, plan = _r2_case()
    document.pop("campaign")
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2
    assert "B105 report carries no campaign binding" in result.stderr

    document, plan = _r2_case()
    document["campaign"]["deadline_sha256"] = "0" * 64
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2
    assert "campaign deadline_sha256 does not match the deadline file" in result.stderr

    document, plan = _r2_case()
    commit, tree = _own_commit_and_tree()
    deadline = json.loads(_deadline_bytes(document, commit, tree))
    deadline["commit"] = "0" * 40
    raw_deadline = json.dumps(deadline, sort_keys=True, indent=2).encode("utf-8")
    document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
        deadline_raw=raw_deadline,
    )
    assert result.returncode == 2
    assert "deadline file does not bind this commit/tree/lane/version" in result.stderr

    document, plan = _r2_case()
    document["started"] = "2026-08-06T00:00:00+00:00"
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
    )
    assert result.returncode == 2
    assert "report was not produced inside its campaign window" in result.stderr


def test_p3d_manifest_and_campaign_arguments_are_checked_before_reading_report(tmp_path):
    document, plan = _r2_case()
    document["outcome"] = "FAIL"
    result = _run_checker(
        tmp_path,
        document,
        lane=SELF_QUALIFICATION,
        rigor=SELF_QUALIFICATION_RIGOR,
        plan=plan,
        manifest=None,
    )
    assert result.returncode == 2
    assert "--r2-manifest is required for an R2 report" in result.stderr
    assert "B105_REPORT_REJECTED" not in result.stderr

    document = _verifier_valid_report(PREFLIGHT, PREFLIGHT_RIGOR)
    result = _run_checker(
        tmp_path,
        document,
        lane=PREFLIGHT,
        rigor=PREFLIGHT_RIGOR,
        manifest=_manifest_bytes(),
    )
    assert result.returncode == 2
    assert "--r2-manifest given for a report without R2" in result.stderr
    assert "B105_REPORT_REJECTED" not in result.stderr
