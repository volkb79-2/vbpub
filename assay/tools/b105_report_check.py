#!/usr/bin/env python3
"""Independently bind B105 verdicts to the gate's source and lane facts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

#: The one source root B105 measures, relative to the assay project directory.
B105_SOURCE_ROOT = "src/assay"
#: Source trees that are deliberately outside B105, each with the decision that put
#: it there. A tracked top-level entry under ``src/`` other than B105's root is
#: refused; the packages under ``analysis/src/`` are pinned by the analysis tests.
OUT_OF_SCOPE_BY_DECISION = {"analysis/src/assay_analysis": "A-478"}
PROJECT_DIR = Path(__file__).resolve().parents[1]


def _ls_tree(repo_root: Path, commit: str, *args: str, paths: tuple[str, ...]) -> list[str]:
    """Names git tracks in *commit* under *paths* (never the working tree)."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", *args, commit, "--", *paths],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot list tracked files: {result.stderr.strip()}")
    return result.stdout.splitlines()


def verify_scope(document: dict, *, repo_root: Path, expected_commit: str) -> None:
    """Refuse a verdict whose measured scope is not exactly the tracked ``src/assay``."""
    try:
        prefix = PROJECT_DIR.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        raise ValueError(f"checker project {PROJECT_DIR} is not inside repo root {repo_root}") from None
    under = "" if prefix == "." else prefix + "/"

    judgment = document.get("judgment")
    judgment = judgment if isinstance(judgment, dict) else {}
    resolved = judgment.get("resolved")
    source_roots = resolved.get("source_roots") if isinstance(resolved, dict) else None
    if source_roots != [B105_SOURCE_ROOT]:
        raise ValueError(
            f"judgment.resolved.source_roots {source_roots!r} != {[B105_SOURCE_ROOT]!r}"
        )

    top_level = sorted(
        line.rsplit("/", 1)[-1]
        for line in _ls_tree(repo_root, expected_commit, "--name-only", paths=(f"{under}src/",))
    )
    if top_level != ["assay"]:
        unclassified = [name for name in top_level if name != "assay"] or top_level
        raise ValueError(f"unclassified package under src/: {', '.join(unclassified)}")

    for path in OUT_OF_SCOPE_BY_DECISION:
        if not _ls_tree(repo_root, expected_commit, "--name-only", paths=(f"{under}{path}/__init__.py",)):
            raise ValueError(f"stale out-of-scope declaration: {path}")

    for tier in ("r1", "r2", "r3"):
        block = judgment.get(tier)
        targets = block.get("targets") if isinstance(block, dict) else None
        for target in targets if isinstance(targets, list) else []:
            for path, decision in OUT_OF_SCOPE_BY_DECISION.items():
                if target == path or str(target).startswith(path + "/"):
                    raise ValueError(
                        f"judgment.{tier}.targets names {target}, out of B105 scope by decision {decision}"
                    )
            if tier == "r3" and not str(target).startswith(B105_SOURCE_ROOT + "/"):
                raise ValueError(f"judgment.r3.targets names {target}, not under {B105_SOURCE_ROOT}/")

    tracked = sorted(
        line[len(under):]
        for line in _ls_tree(
            repo_root, expected_commit, "-r", "--name-only", paths=(f"{under}{B105_SOURCE_ROOT}/",)
        )
        if line.endswith(".py")
    )
    for tier in ("r1", "r2"):
        block = judgment.get(tier)
        if tier == "r2" and block is None:
            continue
        targets = block.get("targets") if isinstance(block, dict) else None
        targets = targets if isinstance(targets, list) else []
        if targets != tracked:
            missing = sorted(set(tracked) - set(targets))
            extra = sorted(set(targets) - set(tracked))
            raise ValueError(
                f"judgment.{tier}.targets differ from the tracked {B105_SOURCE_ROOT} sources: "
                f"missing {missing}, extra {extra}"
                + ("" if missing or extra else ", or are not sorted")
            )


_CANDIDATE_ID = re.compile(r"[0-9a-f]{64}\Z")


def _plan_structure(
    plan: Any, *, expected_commit: str | None, expected_tree: str | None
) -> dict:
    """Refuse a plan whose shape cannot be trusted (refusal 9); never a bare TypeError."""
    if isinstance(plan, ValueError):  # the file could not be read: refused at this step
        raise plan
    if not isinstance(plan, dict):
        raise ValueError("plan is not an object")
    if not isinstance(plan.get("status"), str):
        raise ValueError("plan status is not a string")
    count = plan.get("candidate_count")
    if type(count) is not int:
        raise ValueError("plan candidate_count is not an integer")
    rows = plan.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("plan candidates is not a list")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"plan candidate {index} is not an object")
        identity = row.get("id")
        if not isinstance(identity, str) or not _CANDIDATE_ID.fullmatch(identity):
            raise ValueError(f"plan candidate {index} has no 64-hex id")
    shard = plan.get("shard")
    if shard is not None and not isinstance(shard, str):
        raise ValueError("plan shard is neither null nor a string")
    if expected_commit is not None and (
        plan.get("commit") != expected_commit or plan.get("tree") != expected_tree
    ):
        raise ValueError("plan commit/tree differ from the expected source")
    return plan


def check_campaign_scope(
    document: dict,
    plan: dict,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> None:
    """Refuse a report whose R2 campaign is not exactly the complete, unsharded plan.

    Order (first failure wins): the plan's structure (9, and its commit/tree when the
    expected source is given), the plan itself (2-4), then the report against it (5-7).
    """
    plan = _plan_structure(plan, expected_commit=expected_commit, expected_tree=expected_tree)
    if plan["status"] != "ok":
        raise ValueError(f"plan status is {plan['status']!r}, expected 'ok'")
    if plan.get("shard") is not None:
        raise ValueError(f"plan is sharded ({plan['shard']!r}); the campaign must be complete")
    if plan["candidate_count"] != len(plan["candidates"]):
        raise ValueError("plan candidate_count does not match its candidates")

    judgment = document.get("judgment")
    r2 = judgment.get("r2") if isinstance(judgment, dict) else None
    if isinstance(r2, dict):
        for key in ("shard_index", "shard_count"):
            if r2.get(key) is not None:
                raise ValueError(f"judgment.r2.{key} is {r2[key]!r}; the campaign must be unsharded")
    claims = document.get("claims")
    claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R2"),
        None,
    ) if isinstance(claims, list) else None
    mutation = claim.get("mutation") if isinstance(claim, dict) else None
    identities = mutation.get("candidate_ids") if isinstance(mutation, dict) else None
    if not isinstance(identities, list) or any(
        not isinstance(item, str) or not _CANDIDATE_ID.fullmatch(item) for item in identities
    ):
        raise ValueError("R2 claim mutation.candidate_ids is missing or not a list of 64-hex ids")
    if len(set(identities)) != len(identities):
        raise ValueError("R2 claim mutation.candidate_ids contains duplicates")
    planned = {row["id"] for row in plan["candidates"]}
    if set(identities) != planned or len(identities) != plan["candidate_count"]:
        raise ValueError(
            f"R2 candidate_ids differ from the plan: only in report "
            f"{sorted(set(identities) - planned)}, only in plan {sorted(planned - set(identities))}"
        )


def verify_report_document(
    document: Any,
    *,
    repo_root: Path,
    expected_commit: str,
    expected_tree: str,
    expected_lane: str,
    expected_rigor: tuple[str, ...],
    expected_version: str,
    expected_wheel_sha256: str,
    producer_exit: int,
    plan: dict | None = None,
) -> None:
    """Refuse a valid-but-adverse or internally unrelated verdict.

    ``plan`` is ``assay plan``'s JSON, required exactly when R2 is expected.
    """
    if type(producer_exit) is not int or producer_exit != 0:
        raise ValueError(f"Assay producer exit was {producer_exit!r}, expected 0")
    if not isinstance(document, dict):
        raise ValueError("verdict root is not an object")

    if document.get("commit") != expected_commit:
        raise ValueError(
            f"verdict commit {document.get('commit')!r} != {expected_commit!r}"
        )
    if document.get("lane") != expected_lane:
        raise ValueError(
            f"verdict lane {document.get('lane')!r} != {expected_lane!r}"
        )
    if document.get("declared_rigor") != list(expected_rigor):
        raise ValueError(
            f"verdict declared_rigor {document.get('declared_rigor')!r} "
            f"!= {list(expected_rigor)!r}"
        )

    tree_result = subprocess.run(
        ["git", "rev-parse", f"{expected_commit}^{{tree}}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if tree_result.returncode != 0:
        raise ValueError(
            f"cannot resolve captured commit tree: {tree_result.stderr.strip()}"
        )
    actual_tree = tree_result.stdout.strip()
    if actual_tree != expected_tree:
        raise ValueError(
            f"captured commit tree {actual_tree!r} != {expected_tree!r}"
        )
    verify_scope(document, repo_root=repo_root, expected_commit=expected_commit)

    if document.get("outcome") != "PASS":
        raise ValueError(f"verdict outcome is {document.get('outcome')!r}, expected PASS")
    report_exit = document.get("exit_code")
    if type(report_exit) is not int or report_exit != 0:
        raise ValueError(f"verdict exit_code is {report_exit!r}, expected 0")

    claims = document.get("claims")
    if not isinstance(claims, list):
        raise ValueError("verdict claims is not an array")
    claim_rigor: list[str] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"verdict claim {index} is not an object")
        claim_rigor.append(claim.get("rigor"))
        if claim.get("status") != "PASS":
            raise ValueError(
                f"verdict claim {claim.get('rigor')!r} is "
                f"{claim.get('status')!r}, expected PASS"
            )
    if claim_rigor != list(expected_rigor):
        raise ValueError(
            f"verdict claim rigor {claim_rigor!r} != {list(expected_rigor)!r}"
        )

    if document.get("assay_version") != expected_version:
        raise ValueError(
            f"verdict assay_version {document.get('assay_version')!r} "
            f"!= {expected_version!r}"
        )
    provenance = document.get("judge_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("verdict has no judge_provenance object")
    expected_provenance = {
        "artifact": "wheel",
        "name": "assay",
        "digest_algorithm": "sha256",
        "digest": expected_wheel_sha256,
        "version": expected_version,
    }
    for field, expected in expected_provenance.items():
        if provenance.get(field) != expected:
            raise ValueError(
                f"judge_provenance.{field} {provenance.get(field)!r} "
                f"!= {expected!r}"
            )
    if "R2" in expected_rigor:
        if plan is None:
            raise ValueError("R2 is expected but no plan was given (--plan-json)")
        check_campaign_scope(
            document, plan, expected_commit=expected_commit, expected_tree=expected_tree
        )


#: The lane whose receipt the full B105 qualification requires (S1, B123).
RECEIPT_LANE = "tester-unified"
#: The lane that requires it: only the full qualification, not the preflight (CD9).
RECEIPT_REQUIRED_FOR = "self-qualification"
_RECEIPT_KEYS = frozenset({"schema_version", "lane", "commit", "tree"})
#: The full-mode flags, each required unless `--receipt-only`.
_FULL_FLAGS = (
    "report",
    "repo_root",
    "expected_commit",
    "expected_tree",
    "expected_lane",
    "expected_rigor",
    "expected_version",
    "expected_wheel_sha256",
    "producer_exit",
)


def verify_tester_unified_receipt(document: Any, *, expected_commit: str, expected_tree: str) -> None:
    """Refuse unless ``document`` is exactly the registered gate's receipt for this commit and tree.

    The document is the four-key object ``finish_registered_gate`` writes after a
    green ``tester-unified`` run. Commit and tree are compared exactly, so neither
    uppercase hex nor a longer digest of the right prefix matches.
    """
    if not isinstance(document, dict):
        raise ValueError("tester-unified receipt is not a JSON object")
    if set(document) != _RECEIPT_KEYS:
        raise ValueError(f"tester-unified receipt keys {sorted(document)} != {sorted(_RECEIPT_KEYS)}")
    version = document["schema_version"]
    if type(version) is not int or version != 1:
        raise ValueError(f"tester-unified receipt schema_version {version!r} != 1")
    if document["lane"] != RECEIPT_LANE:
        raise ValueError(f"tester-unified receipt lane {document['lane']!r} != {RECEIPT_LANE!r}")
    if document["commit"] != expected_commit:
        raise ValueError(f"tester-unified receipt commit {document['commit']!r} != {expected_commit!r}")
    if document["tree"] != expected_tree:
        raise ValueError(f"tester-unified receipt tree {document['tree']!r} != {expected_tree!r}")


def _read_receipt(path: Path, *, expected_commit: str, expected_tree: str) -> None:
    verify_tester_unified_receipt(
        json.loads(path.read_text(encoding="utf-8")),
        expected_commit=expected_commit,
        expected_tree=expected_tree,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree")
    parser.add_argument("--expected-lane")
    parser.add_argument("--expected-rigor")
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-wheel-sha256")
    parser.add_argument("--producer-exit", type=int)
    parser.add_argument("--receipt-only", action="store_true")
    parser.add_argument("--tester-unified-receipt", type=Path)
    parser.add_argument("--plan-json", type=Path)
    args = parser.parse_args(argv)

    if args.receipt_only:
        extra = [f"--{name.replace('_', '-')}" for name in (*_FULL_FLAGS, "plan_json") if name not in ("expected_commit", "expected_tree") and getattr(args, name) is not None]
        if extra:
            parser.error(f"--receipt-only takes no other flag than the receipt, commit and tree: {', '.join(extra)}")
        missing = [
            flag
            for flag, value in (
                ("--tester-unified-receipt", args.tester_unified_receipt),
                ("--expected-commit", args.expected_commit),
                ("--expected-tree", args.expected_tree),
            )
            if value is None
        ]
        if missing:
            parser.error(f"--receipt-only requires {', '.join(missing)}")
        try:
            _read_receipt(args.tester_unified_receipt, expected_commit=args.expected_commit, expected_tree=args.expected_tree)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"B105_REPORT_REJECTED={exc}", file=sys.stderr)
            return 2
        print(f"B105_TESTER_UNIFIED_PASS=commit={args.expected_commit} tree={args.expected_tree}")
        return 0

    missing = [f"--{name.replace('_', '-')}" for name in _FULL_FLAGS if getattr(args, name) is None]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    if args.plan_json is not None and "R2" not in args.expected_rigor.split(","):
        parser.error("--plan-json is only valid when R2 is in --expected-rigor")

    try:
        if args.expected_lane == RECEIPT_REQUIRED_FOR:
            if args.tester_unified_receipt is None:
                raise ValueError(f"lane {args.expected_lane} requires --tester-unified-receipt")
            _read_receipt(
                args.tester_unified_receipt,
                expected_commit=args.expected_commit,
                expected_tree=args.expected_tree,
            )
        elif args.tester_unified_receipt is not None:
            raise ValueError(f"lane {args.expected_lane} takes no --tester-unified-receipt")
        document = json.loads(args.report.read_text(encoding="utf-8"))
        rigor = tuple(args.expected_rigor.split(","))
        if not rigor or any(not item for item in rigor):
            raise ValueError("expected rigor must be a non-empty comma-separated list")
        plan: Any = None
        if args.plan_json is not None:
            try:
                plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                plan = ValueError(f"cannot read plan {args.plan_json}: {exc}")
        verify_report_document(
            document,
            repo_root=args.repo_root,
            expected_commit=args.expected_commit,
            expected_tree=args.expected_tree,
            expected_lane=args.expected_lane,
            expected_rigor=rigor,
            expected_version=args.expected_version,
            expected_wheel_sha256=args.expected_wheel_sha256,
            producer_exit=args.producer_exit,
            plan=plan,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"B105_REPORT_REJECTED={exc}", file=sys.stderr)
        return 2

    out_of_scope = ",".join(f"{path}:{decision}" for path, decision in OUT_OF_SCOPE_BY_DECISION.items())
    print(
        f"B105_REPORT_ACCEPTED={args.expected_lane}"
        f" commit={args.expected_commit} tree={args.expected_tree}"
        f" scope={B105_SOURCE_ROOT} out_of_scope={out_of_scope}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
