#!/usr/bin/env python3
"""Select a deterministic, source-bound pilot for Assay's analysis R2 lane.

The bounded file/Git/output primitives are shared with B110's selector. The
selection policy here is analysis-specific: one candidate from every
candidate-bearing file/operator cell, every candidate of the rare
``falsy-swap`` operator, then a deterministic sample from the remaining plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import b110_pilot_select as common
from assay.cli import _plan_rows_from_jobs, _resolve_declared_adapters
from assay.config import load_lane_file
from assay.mutation import (
    MutationTarget,
    UNSUPPORTED,
    candidate_id,
    collect_mutation_sites,
)

LANE = "analysis-r2"
SOURCE_ROOT = "assay/analysis/src/assay_analysis"
TARGETS = (
    f"{SOURCE_ROOT}/__init__.py",
    f"{SOURCE_ROOT}/campaign.py",
    f"{SOURCE_ROOT}/cli.py",
    f"{SOURCE_ROOT}/evidence.py",
    f"{SOURCE_ROOT}/plan_estimate.py",
)
OPERATORS = (
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)
RARE_OPERATOR = "python:falsy-swap"
DEFAULT_SIZE = 40
DEFAULT_SEED = "analysis-r2-pilot-2026-10-v1"


def _rank(seed: str, candidate_id: str) -> str:
    return hashlib.blake2b(
        (seed + candidate_id).encode("ascii"), digest_size=16
    ).hexdigest()


def _lane_targets(repo_root: Path, commit: str) -> tuple[str, ...]:
    raw = common._git(
        repo_root, "show", f"{commit}:assay/assay.toml", text=False
    ).stdout
    try:
        document = tomllib.loads(raw.decode("utf-8"))
        lane = document["lanes"][LANE]
        judge = lane["judge"]
        mutation = judge["mutation"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise common.SelectionError(
            f"committed {LANE} declaration is incomplete: {exc}"
        ) from exc
    if lane.get("rigor") != ["R0", "R1", "R2"]:
        raise common.SelectionError(f"committed {LANE} must declare exactly R0/R1/R2")
    if judge.get("mode") != "whole_target":
        raise common.SelectionError(f"committed {LANE} must use whole_target mode")
    if judge.get("source_roots") != ["analysis/src/assay_analysis"]:
        raise common.SelectionError(f"committed {LANE} has the wrong source root")
    targets = judge.get("targets")
    if not isinstance(targets, list) or targets != [
        "analysis/src/assay_analysis/__init__.py",
        "analysis/src/assay_analysis/campaign.py",
        "analysis/src/assay_analysis/cli.py",
        "analysis/src/assay_analysis/evidence.py",
        "analysis/src/assay_analysis/plan_estimate.py",
    ]:
        raise common.SelectionError(f"committed {LANE} target inventory changed")
    if mutation.get("operators") != list(OPERATORS):
        raise common.SelectionError(f"committed {LANE} operator inventory changed")
    tracked = common._git(
        repo_root,
        "ls-tree",
        "-r",
        "--name-only",
        commit,
        "--",
        f"{SOURCE_ROOT}/",
    ).stdout.splitlines()
    tracked_python = tuple(sorted(path for path in tracked if path.endswith(".py")))
    if tracked_python != tuple(sorted(TARGETS)):
        raise common.SelectionError(
            f"committed {LANE} source inventory differs from its exact targets"
        )
    return tuple(sorted(TARGETS))


def _derive_committed_inventory(
    repo_root: Path, commit: str
) -> list[dict[str, Any]]:
    """Rebuild the full native mutation inventory from committed source bytes."""
    rows, _replacement_sha256_by_id = _derive_committed_inventory_and_replacements(
        repo_root, commit
    )
    return rows


def _derive_committed_inventory_and_replacements(
    repo_root: Path, commit: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Rebuild plan rows and the state-required replacement digests."""
    root = repo_root.resolve(strict=True)
    config_relative = "assay/assay.toml"
    config_raw = common._read_tree_files(
        (config_relative,), root, commit=commit
    )[config_relative]
    config_path = root / config_relative
    try:
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("assay.toml is not a regular file in the checkout")
        if config_path.read_bytes() != config_raw:
            raise ValueError("checkout assay.toml differs from its committed bytes")
        lane_file = load_lane_file(config_path)
        if config_path.read_bytes() != config_raw:
            raise ValueError("assay.toml changed while the lane loader read it")
        lane = lane_file.lane(LANE)
    except Exception as exc:
        raise common.SelectionError(
            f"cannot load the committed {LANE} lane declaration: {exc}"
        ) from exc
    mutation = lane.judge.mutation if lane.judge is not None else None
    if (
        lane_file.project_root != root / "assay"
        or lane.judge is None
        or lane.judge.language != "python"
        or lane.judge.mode != "whole_target"
        or lane.judge.targets is None
        or mutation is None
        or mutation.operators != OPERATORS
        or mutation.max_mutants is None
    ):
        raise common.SelectionError(
            f"committed {LANE} lane cannot produce a complete Python inventory"
        )
    adapter = _resolve_declared_adapters(lane)
    if adapter is None:
        raise common.SelectionError(f"committed {LANE} lane resolves no adapter")

    project_prefix = lane_file.project_root.relative_to(root).as_posix()
    source_paths = tuple(
        sorted(f"{project_prefix}/{relative}" for relative in lane.judge.targets)
    )
    expected_paths = tuple(sorted(TARGETS))
    if source_paths != expected_paths:
        raise common.SelectionError(
            f"committed {LANE} target inventory differs from the fixed analysis target set"
        )
    try:
        sources = common._read_tree_files(source_paths, root, commit=commit)
        targets: list[MutationTarget] = []
        for path in source_paths:
            text = sources[path].decode("utf-8")
            line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
            targets.append(
                MutationTarget(
                    path=path,
                    text=text,
                    lines=frozenset(range(1, line_count + 1)),
                )
            )
        jobs = collect_mutation_sites(
            targets,
            adapter=adapter,
            operators=mutation.operators,
            limit=mutation.max_mutants + 1,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise common.SelectionError(
            f"cannot derive the complete committed {LANE} inventory: {exc}"
        ) from exc
    if jobs == UNSUPPORTED or len(jobs) > mutation.max_mutants:
        raise common.SelectionError(
            f"committed {LANE} lane does not expose a bounded complete candidate inventory"
        )
    rows = _plan_rows_from_jobs(jobs)
    replacement_sha256_by_id = {
        candidate_id(job): job.site.replacement_sha256 for job in jobs
    }
    if set(replacement_sha256_by_id) != {row["id"] for row in rows}:
        raise common.SelectionError(
            "committed analysis candidate ids do not match their replacement digests"
        )
    return rows, replacement_sha256_by_id


def _parse_bound_plan(
    raw: bytes, *, repo_root: Path
) -> tuple[dict[str, Any], str, str, list[dict[str, Any]], tuple[str, ...]]:
    commit, tree, rows = common._parse_plan(raw)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=common._unique_object)
    repo_commit, repo_tree = common._repository_identity(repo_root)
    if commit != repo_commit or tree != repo_tree:
        raise common.SelectionError("plan commit/tree do not match the clean repo-root")
    if document.get("lane") != LANE:
        raise common.SelectionError(f"plan lane must be {LANE!r}")
    if document.get("shard") is not None:
        raise common.SelectionError("pilot selection requires an unsharded plan")
    if type(document.get("candidate_count")) is not int or document["candidate_count"] != len(rows):
        raise common.SelectionError("plan candidate_count differs from its rows")
    if not rows:
        raise common.SelectionError("analysis R2 plan has no candidates to sample")
    if type(document.get("jobs")) is not int or document["jobs"] != 1:
        raise common.SelectionError("analysis R2 plan must start with one worker")
    resource_observation = document.get("resource_observation")
    if not isinstance(resource_observation, dict) or resource_observation.get("available") is not True:
        raise common.SelectionError(
            "analysis R2 plan requires visible cgroup resource observation"
        )
    cold = document.get("cold_witness")
    if not isinstance(cold, dict) or cold.get("eligible") is not True:
        raise common.SelectionError("analysis R2 plan is not eligible for cold witness")

    targets = _lane_targets(repo_root, commit)
    if any(row["path"] not in targets for row in rows):
        raise common.SelectionError("plan contains a candidate outside the analysis target inventory")
    observed_operators = {row["operator"] for row in rows}
    if not observed_operators <= set(OPERATORS):
        raise common.SelectionError("plan contains an undeclared Python operator")
    expected_by_file = dict(sorted(Counter(row["path"] for row in rows).items()))
    expected_by_operator = dict(sorted(Counter(row["operator"] for row in rows).items()))
    if document.get("by_file") != expected_by_file:
        raise common.SelectionError("plan by_file counts differ from candidate rows")
    if document.get("by_operator") != expected_by_operator:
        raise common.SelectionError("plan by_operator counts differ from candidate rows")
    common._read_sources(rows, repo_root, commit=commit)
    expected_rows, replacement_sha256_by_id = (
        _derive_committed_inventory_and_replacements(repo_root, commit)
    )
    if rows != expected_rows:
        raise common.SelectionError(
            "plan candidate inventory differs from the complete committed analysis lane"
        )
    bound_rows = [
        {**row, "replacement_sha256": replacement_sha256_by_id[row["id"]]}
        for row in rows
    ]
    return document, commit, tree, bound_rows, targets


def _select(
    rows: list[dict[str, Any]], *, seed: str, size: int
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    if type(size) is not int or size < 1:
        raise common.SelectionError("size must be a positive integer")
    common._validate_seed(seed)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_operator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["path"], row["operator"])].append(row)
        by_operator[row["operator"]].append(row)
    ranks = {row["id"]: _rank(seed, row["id"]) for row in rows}
    selected: dict[str, str] = {}
    for key in sorted(groups):
        winner = min(groups[key], key=lambda row: (ranks[row["id"]], row["id"]))
        selected[winner["id"]] = "file-operator-stratum"
    for row in by_operator.get(RARE_OPERATOR, ()):
        selected[row["id"]] = "rare-operator-census"
    required = len(selected)
    target_size = min(size, len(rows))
    if required > target_size:
        raise common.SelectionError(
            f"size {size} cannot cover {required} required file/operator and rare-operator candidates"
        )
    for row in sorted(rows, key=lambda item: (ranks[item["id"]], item["id"])):
        if len(selected) >= target_size:
            break
        selected.setdefault(row["id"], "deterministic-fill")
    ordered = [
        {
            **row,
            "selection_reason": selected[row["id"]],
            "rank": ranks[row["id"]],
        }
        for row in rows
        if row["id"] in selected
    ]
    selected_by_file = dict(sorted(Counter(row["path"] for row in ordered).items()))
    selected_by_operator = dict(
        sorted(Counter(row["operator"] for row in ordered).items())
    )
    if set(selected_by_file) != {path for path, _op in groups}:
        raise common.SelectionError("selection does not cover every candidate-bearing file")
    if set(selected_by_operator) != {operator for _path, operator in groups}:
        raise common.SelectionError("selection does not cover every candidate-bearing operator")
    return ordered, selected_by_file, selected_by_operator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidates_output = report_output = plan_input = None
    candidates_temporary = report_temporary = None
    exit_code = 0
    cleanup_errors: list[str] = []

    def cleanup(label: str, operation: Callable[[], object]) -> None:
        try:
            operation()
        except Exception as exc:  # cleanup diagnostics must not mask the first refusal
            cleanup_errors.append(f"{label}: {exc}")

    try:
        if args.size < 1:
            raise common.SelectionError("size must be a positive integer")
        candidates_output = common._pin_output(args.out)
        report_output = common._pin_output(args.report)
        plan_input = common._pin_input(args.plan)
        plan_identity = plan_input.identity
        if plan_identity in (candidates_output.identity, report_output.identity):
            raise common.SelectionError("--plan, --out, and --report must name different files")
        if candidates_output.identity == report_output.identity:
            raise common.SelectionError("--out and --report must name different files")
        raw_plan = common._read_plan(plan_input.path, descriptor=plan_input.file_fd)
        plan, commit, tree, rows, targets = _parse_bound_plan(
            raw_plan, repo_root=args.repo_root
        )
        source_paths = common._planned_source_paths(rows, args.repo_root)
        source_identities = {common._path_identity(path) for path in source_paths.values()}
        if candidates_output.identity in source_identities or report_output.identity in source_identities:
            raise common.SelectionError("selection outputs must not overwrite planned source files")
        selected, selected_by_file, selected_by_operator = _select(
            rows, seed=args.seed, size=args.size
        )
        selected_ids = [row["id"] for row in selected]
        plan_order_ids = [row["id"] for row in rows if row["id"] in set(selected_ids)]
        if selected_ids != plan_order_ids:
            raise common.SelectionError("selected candidate order differs from the plan order")
        candidate_bytes = (
            f"# {LANE} bounded pilot seed={args.seed} size={args.size} "
            f"plan_candidates={len(rows)}\n"
            + "".join(f"{identity}\n" for identity in selected_ids)
        ).encode("utf-8")
        if len(candidate_bytes) > common._CANDIDATE_FILE_LIMIT:
            raise common.SelectionError("selected candidates file exceeds Assay's 1 MiB input bound")
        selection_sha256 = common._plan_sha256(selected_ids)
        report = {
            "schema": "assay-analysis-r2-pilot-selection/1",
            "lane": LANE,
            "plan_commit": commit,
            "plan_tree": tree,
            "plan_sha256": hashlib.sha256(raw_plan).hexdigest(),
            "plan_candidate_count": len(rows),
            "targets": list(targets),
            "files": sorted({row["path"] for row in rows}),
            "operators": sorted({row["operator"] for row in rows}),
            "seed": args.seed,
            "size": args.size,
            "selected_ids": selected_ids,
            "selected": selected,
            "selected_by_file": selected_by_file,
            "selected_by_operator": selected_by_operator,
            "candidate_file_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "selection_sha256": selection_sha256,
            "rare_operator_census": RARE_OPERATOR,
        }
        report_bytes = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
        candidates_temporary = common._stage_atomic(
            candidates_output.parent_fd, candidates_output.name, candidate_bytes
        )
        report_temporary = common._stage_atomic(
            report_output.parent_fd, report_output.name, report_bytes
        )
        allowed_untracked: set[bytes] = set()
        root = args.repo_root.resolve(strict=True)
        for output, temporary in (
            (candidates_output, candidates_temporary),
            (report_output, report_temporary),
        ):
            try:
                relative = (output.parent_path / temporary).relative_to(root)
            except ValueError:
                continue
            allowed_untracked.add(os.fsencode(relative.as_posix()))
        common._acquire_output_locks((candidates_output, report_output))
        final_commit, final_tree = common._repository_identity(
            root, allowed_untracked=frozenset(allowed_untracked)
        )
        if (final_commit, final_tree) != (commit, tree):
            raise common.SelectionError("repo-root changed before pilot selection publication")
        common._verify_output_parent_identity(candidates_output)
        common._verify_output_parent_identity(report_output)
        common._publish_pair(
            candidates_output,
            candidates_temporary,
            report_output,
            report_temporary,
        )
        candidates_temporary = report_temporary = None
        print("ANALYSIS_R2_PILOT_SELECTION=READY")
    except (common.SelectionError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"analysis_r2_pilot_select: {exc}", file=sys.stderr)
        exit_code = 2
    finally:
        if plan_input is not None:
            cleanup("plan input", plan_input.close)
        if candidates_output is not None:
            cleanup(
                "candidate temporary",
                lambda: common._cleanup_staged(candidates_output.parent_fd, candidates_temporary),
            )
            cleanup("candidate output directory", candidates_output.close)
        if report_output is not None:
            cleanup(
                "report temporary",
                lambda: common._cleanup_staged(report_output.parent_fd, report_temporary),
            )
            cleanup("report output directory", report_output.close)
    if cleanup_errors:
        print("analysis_r2_pilot_select: cleanup failed: " + "; ".join(cleanup_errors), file=sys.stderr)
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
