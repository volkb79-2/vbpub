#!/usr/bin/env python3
"""Independently bind B105 verdicts to the gate's source and lane facts."""

from __future__ import annotations

import argparse
import json
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
) -> None:
    """Refuse a valid-but-adverse or internally unrelated verdict."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-lane", required=True)
    parser.add_argument("--expected-rigor", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--producer-exit", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.report.read_text(encoding="utf-8"))
        rigor = tuple(args.expected_rigor.split(","))
        if not rigor or any(not item for item in rigor):
            raise ValueError("expected rigor must be a non-empty comma-separated list")
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
