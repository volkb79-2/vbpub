#!/usr/bin/env python3
"""Independently bind B105 verdicts to the gate's source and lane facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

#: The one source root B105 measures, relative to the assay project directory.
B105_SOURCE_ROOT = "src/assay"
#: Source trees that are deliberately outside B105, each with the decision that put
#: it there. A tracked top-level entry under ``src/`` other than B105's root is
#: refused; the packages under ``analysis/src/`` are pinned by the analysis tests.
OUT_OF_SCOPE_BY_DECISION = {"analysis/src/assay_analysis": "A-478"}
PROJECT_DIR = Path(__file__).resolve().parents[1]
_GIT_MAINTENANCE_CONFIG = (
    "-c",
    "maintenance.auto=false",
    "-c",
    "maintenance.autoDetach=false",
    "-c",
    "gc.autoDetach=false",
)


def _git_argv(*args: str) -> list[str]:
    """Pin report-checker Git queries against detached maintenance."""
    return ["git", *_GIT_MAINTENANCE_CONFIG, *args]


def _ls_tree(repo_root: Path, commit: str, *args: str, paths: tuple[str, ...]) -> list[str]:
    """Names git tracks in *commit* under *paths* (never the working tree)."""
    result = subprocess.run(
        _git_argv("-C", str(repo_root), "ls-tree", *args, commit, "--", *paths),
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
    if (expected_commit is not None or expected_tree is not None) and (
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
    planned = [row["id"] for row in plan["candidates"]]
    if identities != planned or len(identities) != plan["candidate_count"]:
        raise ValueError(
            "R2 candidate_ids differ from the plan: report inventory must match "
            "the ordered plan exactly"
        )
    from assay._mutation_inventory import verify_complete_mutation_inventory

    verify_complete_mutation_inventory(
        mutation,
        planned,
        context="B105 R2",
    )


def _ordered_plan_sha256(candidate_ids: list[str]) -> str:
    """Hash candidate IDs in discovery order using the campaign netstring format."""
    digest = hashlib.sha256()
    for identity in candidate_ids:
        digest.update(f"{len(identity)}:{identity},".encode("ascii"))
    return digest.hexdigest()


_CAMPAIGN_BINDING_KEYS = frozenset(
    {"name", "deadline_sha256", "created_at_utc", "expires_at_utc"}
)
_B105_ARCHIVE_ENV = frozenset(
    {
        "ASSAY_B105_COVERAGE_SOURCE",
        "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
        "ASSAY_B105_SOURCE_COMMIT",
        "ASSAY_B105_SOURCE_TREE",
    }
)
_R2_TRANSFORM = "assay-r2-pytest-nocov/1"
_R2_APPENDED = ["-p", "no:pytest_cov"]
_MANIFEST_MAX_BYTES = 16 * 1024 * 1024
_MANIFEST_MAX_NODE_ID_BYTES = 4096
_DEADLINE_MAX_BYTES = 64 * 1024
_PLAN_MAX_BYTES = 16 * 1024 * 1024
_REPORT_MAX_BYTES = 64 * 1024 * 1024
_RECEIPT_MAX_BYTES = 4 * 1024


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_bounded_regular_nofollow(path: Path, *, max_bytes: int) -> bytes:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("maximum file size must be a non-negative integer")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("this platform cannot safely open no-follow regular files")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if info.st_size > max_bytes:
            raise ValueError(f"{path} exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{path} exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_bounded_json(path: Path, *, max_bytes: int) -> Any:
    raw = _read_bounded_regular_nofollow(path, max_bytes=max_bytes)
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{path} is not valid unique-key JSON: {exc}") from exc


def verify_deadline_wheel_sha256(deadline_path: Path, *, expected_sha256: str) -> None:
    """Refuse a B105 retry whose persisted campaign names another wheel.

    This small pre-run check is also used before the run environment is
    installed, so it stays stdlib-only and reads the bounded regular file
    without following a symlink.
    """
    if not isinstance(expected_sha256, str) or _CANDIDATE_ID.fullmatch(expected_sha256) is None:
        raise ValueError("expected wheel SHA-256 must be 64 lowercase hexadecimal characters")
    try:
        deadline_bytes = _read_bounded_regular_nofollow(
            deadline_path, max_bytes=_DEADLINE_MAX_BYTES
        )
    except OSError as exc:
        raise ValueError(f"cannot read campaign deadline {deadline_path}: {exc}") from exc
    try:
        deadline = json.loads(
            deadline_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"campaign deadline {deadline_path} is not valid unique-key JSON: {exc}") from exc
    if not isinstance(deadline, dict) or "wheel_sha256" not in deadline:
        raise ValueError(f"campaign deadline {deadline_path} has no wheel_sha256")
    actual_sha256 = deadline["wheel_sha256"]
    if not isinstance(actual_sha256, str) or _CANDIDATE_ID.fullmatch(actual_sha256) is None:
        raise ValueError(f"campaign deadline {deadline_path} has an invalid wheel_sha256")
    if actual_sha256 != expected_sha256:
        raise ValueError("deadline wheel_sha256 does not match the deterministic source wheel")


def _read_committed_file(repo_root: Path, commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        _git_argv("-C", str(repo_root), "show", f"{commit}:{relative_path}"),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"cannot read committed {relative_path} at {commit}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def _project_prefix(repo_root: Path) -> str:
    try:
        prefix = PROJECT_DIR.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        raise ValueError(
            f"checker project {PROJECT_DIR} is not inside repo root {repo_root}"
        ) from None
    return "" if prefix == "." else prefix + "/"


def _committed_lane(
    repo_root: Path, commit: str, lane: str
) -> tuple[dict[str, Any], str]:
    prefix = _project_prefix(repo_root)
    try:
        raw = _read_committed_file(repo_root, commit, f"{prefix}assay.toml")
        root = tomllib.loads(raw.decode("utf-8"))
        lanes = root["lanes"]
        value = lanes[lane]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read committed lane {lane!r}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"committed lane {lane!r} is not a table")
    return value, prefix


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed


def _committed_r2_plan_sha256(
    repo_root: Path,
    *,
    expected_commit: str,
    expected_tree: str,
    lane: str,
) -> str:
    """Recompute another R2 lane's full ordered plan from the bound commit."""
    prefix = _project_prefix(repo_root)
    config_path = PROJECT_DIR / "assay.toml"
    try:
        committed_config = _read_committed_file(
            repo_root, expected_commit, f"{prefix}assay.toml"
        )
        working_config = config_path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: {exc}"
        ) from exc
    if working_config != committed_config:
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: "
            "working assay.toml differs from the judged commit"
        )

    head = subprocess.run(
        _git_argv("-C", str(repo_root), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode != 0 or head.stdout.strip() != expected_commit:
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: "
            "checkout HEAD differs from the judged commit"
        )
    plan_environment = os.environ.copy()
    plan_environment.pop("PYTHONPATH", None)
    # `assay plan --allow-dirty` still derives candidates from the committed
    # snapshot. It records unrelated worktree changes and refuses a changed
    # loaded lane file; the byte comparison above also binds that file to the
    # judged commit. This keeps the digest independent of untracked files that
    # are outside the lane's committed source roots.
    planned = subprocess.run(
        [
            sys.executable,
            "-m",
            "assay.cli",
            "plan",
            lane,
            "--file",
            str(config_path),
            "--allow-dirty",
        ],
        cwd=repo_root,
        env=plan_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if planned.returncode != 0:
        detail = planned.stderr.strip() or planned.stdout.strip()
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: {detail}"
        )
    try:
        plan = json.loads(
            planned.stdout,
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
        plan = _plan_structure(
            plan,
            expected_commit=expected_commit,
            expected_tree=expected_tree,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: {exc}"
        ) from exc
    candidate_ids = [row["id"] for row in plan["candidates"]]
    max_mutants = plan.get("max_mutants")
    if (
        plan.get("status") != "ok"
        or plan.get("shard") is not None
        or plan["candidate_count"] != len(candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
        or type(max_mutants) is not int
        or max_mutants < len(candidate_ids)
    ):
        raise ValueError(
            f"cannot recompute committed R2 plan for lane {lane!r}: "
            "plan is not complete, unsharded, and within max_mutants"
        )
    return _ordered_plan_sha256(candidate_ids)


def _check_deadline_binding(
    document: dict[str, Any],
    *,
    repo_root: Path,
    deadline_path: Path,
    expected_commit: str,
    expected_tree: str,
    expected_lane: str,
    expected_version: str,
    expected_wheel_sha256: str,
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    campaign = document.get("campaign")
    if not isinstance(campaign, dict) or set(campaign) != _CAMPAIGN_BINDING_KEYS:
        raise ValueError("B105 report carries no campaign binding")
    try:
        deadline_bytes = _read_bounded_regular_nofollow(
            deadline_path, max_bytes=_DEADLINE_MAX_BYTES
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            "campaign deadline_sha256 does not match the deadline file"
        ) from exc
    if hashlib.sha256(deadline_bytes).hexdigest() != campaign["deadline_sha256"]:
        raise ValueError("campaign deadline_sha256 does not match the deadline file")

    try:
        deadline = json.loads(
            deadline_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs
        )
        lanes = deadline.get("lanes") if isinstance(deadline, dict) else None
        plan_sha256 = deadline.get("plan_sha256") if isinstance(deadline, dict) else None
        valid = (
            isinstance(deadline, dict)
            and deadline.get("schema") == "assay-campaign-deadline/1"
            and deadline.get("campaign") == campaign["name"]
            and deadline.get("created_at_utc") == campaign["created_at_utc"]
            and deadline.get("expires_at_utc") == campaign["expires_at_utc"]
            and deadline.get("commit") == expected_commit
            and deadline.get("git_tree") == expected_tree
            and isinstance(lanes, list)
            and lanes == sorted(set(lanes))
            and all(isinstance(lane, str) and lane for lane in lanes)
            and expected_lane in lanes
            and deadline.get("assay_version") == expected_version
            and isinstance(plan_sha256, dict)
            and set(plan_sha256) == set(lanes)
            and all(
                value is None or (isinstance(value, str) and _CANDIDATE_ID.fullmatch(value))
                for value in plan_sha256.values()
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("deadline file does not bind this commit/tree/lane/version")
    if deadline.get("wheel_sha256") != expected_wheel_sha256:
        raise ValueError("deadline wheel_sha256 does not match the expected wheel")
    r2_lanes: list[str] = []
    for lane_name in lanes:
        lane_config, _ = _committed_lane(repo_root, expected_commit, lane_name)
        lane_rigor = lane_config.get("rigor")
        if (
            not isinstance(lane_rigor, list)
            or any(not isinstance(item, str) for item in lane_rigor)
        ):
            raise ValueError(f"committed lane {lane_name!r} has no valid rigor list")
        lane_plan_sha256 = deadline["plan_sha256"][lane_name]
        if "R2" not in lane_rigor and lane_plan_sha256 is not None:
            reason = (
                f"deadline plan_sha256 for lane {lane_name!r} must be null"
                if lane_name == expected_lane
                else f"deadline plan_sha256 for non-R2 lane {lane_name!r} must be null"
            )
            raise ValueError(
                reason
            )
        if "R2" in lane_rigor:
            r2_lanes.append(lane_name)

    try:
        created = _parse_utc(campaign["created_at_utc"])
        expires = _parse_utc(campaign["expires_at_utc"])
        started = _parse_utc(document.get("started"))
        ended = _parse_utc(document.get("ended"))
        inside_window = created <= started and ended <= expires
    except (TypeError, ValueError, OverflowError):
        inside_window = False
    if not inside_window:
        raise ValueError("report was not produced inside its campaign window")
    return tuple(r2_lanes), dict(deadline["plan_sha256"])


def _check_other_r2_deadline_plan_sha256(
    *,
    repo_root: Path,
    expected_commit: str,
    expected_tree: str,
    expected_lane: str,
    r2_lanes: tuple[str, ...],
    deadline_plan_sha256: dict[str, str | None],
) -> None:
    """Bind every non-report R2 lane after the report's prior refusals run."""
    for lane_name in r2_lanes:
        if lane_name == expected_lane:
            continue
        observed = _committed_r2_plan_sha256(
            repo_root,
            expected_commit=expected_commit,
            expected_tree=expected_tree,
            lane=lane_name,
        )
        if deadline_plan_sha256[lane_name] != observed:
            raise ValueError(
                f"deadline plan_sha256 for other R2 lane {lane_name!r} "
                "does not match its committed ordered plan"
            )


def _independent_r2_transform(argv: list[str]) -> list[str]:
    """Reimplement P3b's narrow transform without importing assay code."""
    transformed: list[str] = []
    for token in argv:
        if not isinstance(token, str):
            raise ValueError("argv entries are not strings")
        if token == "--cov-branch":
            continue
        if token.startswith("--cov=") and len(token) > len("--cov="):
            continue
        if token.startswith("--cov-report=") and len(token) > len("--cov-report="):
            continue
        if (
            token in {"--cov", "--no-cov"}
            or token.startswith("--cov")
            or token.startswith("--no-cov")
        ):
            raise ValueError(f"unrecognized coverage option {token}")
        transformed.append(token)
    return transformed


def _manifest_lines(path: Path) -> list[bytes]:
    try:
        raw = _read_bounded_regular_nofollow(path, max_bytes=_MANIFEST_MAX_BYTES)
    except (OSError, ValueError) as exc:
        raise ValueError("R2 manifest sidecar does not match r2_baseline") from exc
    if len(raw) > _MANIFEST_MAX_BYTES or (raw and not raw.endswith(b"\n")):
        raise ValueError("R2 manifest sidecar does not match r2_baseline")
    if not raw:
        return []
    lines = raw.split(b"\n")[:-1]
    if any(
        not line
        or b"\r" in line
        or len(line) > _MANIFEST_MAX_NODE_ID_BYTES
        for line in lines
    ):
        raise ValueError("R2 manifest sidecar does not match r2_baseline")
    try:
        for line in lines:
            line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("R2 manifest sidecar does not match r2_baseline") from exc
    if len(lines) != len(set(lines)):
        raise ValueError("R2 manifest sidecar does not match r2_baseline")
    return lines


def _manifest_digest(lines: list[bytes]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(str(len(line)).encode("ascii"))
        digest.update(b":")
        digest.update(line)
        digest.update(b",")
    return digest.hexdigest()


def _r2_claim(
    document: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    claims = document.get("claims")
    claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R2"),
        None,
    ) if isinstance(claims, list) else None
    judgment = document.get("judgment")
    policy = judgment.get("r2") if isinstance(judgment, dict) else None
    mutation = claim.get("mutation") if isinstance(claim, dict) else None
    if not isinstance(claim, dict) or not isinstance(policy, dict) or not isinstance(mutation, dict):
        raise ValueError("B105 R2 report has no mutation payload")
    return claim, policy, mutation


def _check_v15_r2(
    document: dict[str, Any],
    *,
    repo_root: Path,
    expected_commit: str,
    expected_lane: str,
    r2_manifest: Path,
) -> None:
    _, policy, mutation = _r2_claim(document)
    raw_command = policy.get("r2_command")
    if policy.get("cold_witness_kills") is not True:
        raise ValueError("B105 R2 requires cold_witness_kills true")
    if not isinstance(raw_command, dict):
        raise ValueError("B105 R2 report has no r2_command")
    try:
        source_lane, prefix = _committed_lane(repo_root, expected_commit, expected_lane)
        source_argv = source_lane.get("argv")
    except ValueError as exc:
        raise ValueError(
            f"declared argv differs from assay.toml at {expected_commit}"
        ) from exc
    if (
        not isinstance(source_argv, list)
        or source_argv != document.get("argv_declared")
        or source_argv != raw_command.get("argv_declared")
    ):
        raise ValueError(f"declared argv differs from assay.toml at {expected_commit}")
    if raw_command.get("transform") != _R2_TRANSFORM:
        raise ValueError("unexpected R2 transform")
    try:
        transformed = _independent_r2_transform(source_argv)
    except ValueError as exc:
        raise ValueError(
            "argv_transformed is not the transform of the committed argv"
        ) from exc
    if raw_command.get("argv_transformed") != transformed:
        raise ValueError("argv_transformed is not the transform of the committed argv")
    if raw_command.get("appended") != _R2_APPENDED:
        raise ValueError("unexpected R2 appended argv")
    if raw_command.get("cwd") != "assay":
        raise ValueError("unexpected R2 cwd")

    coverage = raw_command.get("coverage_baseline")
    r2 = raw_command.get("r2_baseline")
    if not isinstance(coverage, dict) or not isinstance(r2, dict) or any(
        coverage.get(key) != r2.get(key)
        for key in ("collection_sha256", "collection_count")
    ):
        raise ValueError("R2 and coverage baseline collections differ")
    for name, baseline in (("coverage_baseline", coverage), ("r2_baseline", r2)):
        count = baseline.get("collection_count")
        if type(count) is not int or count < 0:
            raise ValueError(f"{name} collection_count must be an integer >= 0")
        for field in ("collection_sha256", "hook_fingerprint_sha256"):
            value = baseline.get(field)
            if not isinstance(value, str) or _CANDIDATE_ID.fullmatch(value) is None:
                raise ValueError(f"{name} {field} must be a SHA-256 digest")

    if source_lane.get("env") != {} or source_lane.get("env_passthrough") != ["PATH"]:
        raise ValueError("committed qualification lane environment policy differs")
    effective_env = document.get("env_effective")
    if not isinstance(effective_env, dict) or _B105_ARCHIVE_ENV & set(effective_env):
        raise ValueError("archive-hook variables present in the qualification run")

    lines = _manifest_lines(r2_manifest)
    if (
        _manifest_digest(lines) != r2.get("collection_sha256")
        or len(lines) != r2.get("collection_count")
    ):
        raise ValueError("R2 manifest sidecar does not match r2_baseline")

    killed = mutation.get("killed")
    if not isinstance(killed, list):
        raise ValueError("cold kill manifest binding could not inspect killed outcomes")
    manifest_nodes = [line.decode("utf-8") for line in lines]
    evidence_fields = {
        "command",
        "collection_count",
        "collection_sha256",
        "hook_fingerprint_sha256",
        "started_count",
        "failed_call_index",
    }

    def is_failed_call_witness(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and set(value)
            == {
                "node_id",
                "when",
                "outcome",
                "session_exit_status",
                "process_exit_status",
            }
            and isinstance(value.get("node_id"), str)
            and value.get("when") == "call"
            and value.get("outcome") == "failed"
            and type(value.get("session_exit_status")) is int
            and value.get("session_exit_status") == 1
            and type(value.get("process_exit_status")) is int
            and value.get("process_exit_status") == 1
        )

    for outcome in killed:
        if not isinstance(outcome, dict):
            raise ValueError("cold kill manifest binding found a non-object outcome")
        candidate_id = outcome.get("candidate_id", "<unknown>")
        execution = outcome.get("execution")
        if not isinstance(execution, dict):
            raise ValueError(f"cold kill {candidate_id} has no execution object")
        mode = execution.get("mode")
        evidence = outcome.get("evidence")
        witness = execution.get("witness")
        node_id = witness.get("node_id") if isinstance(witness, dict) else None
        if mode == "witness-cold":
            failed_index = evidence.get("failed_call_index") if isinstance(evidence, dict) else None
            started_count = evidence.get("started_count") if isinstance(evidence, dict) else None
            if (
                type(failed_index) is not int
                or type(started_count) is not int
                or failed_index + 1 != started_count
                or failed_index < 0
                or failed_index >= len(lines)
                or not is_failed_call_witness(witness)
                or lines[failed_index].decode("utf-8") != node_id
            ):
                raise ValueError(
                    f"cold kill {candidate_id} is not the manifest's node at its failed index"
                )
        elif mode == "full":
            if not isinstance(evidence, dict) or evidence.get("command") != "declared":
                raise ValueError(
                    f"cold full kill {candidate_id} requires declared-command evidence"
                )
            if set(evidence) != evidence_fields:
                raise ValueError(
                    f"cold full kill {candidate_id} must carry the six v15 evidence fields"
                )
            if evidence.get("started_count") is not None or evidence.get("failed_call_index") is not None:
                raise ValueError(
                    f"cold full kill {candidate_id} cannot carry started-prefix evidence"
                )
            if not is_failed_call_witness(witness):
                raise ValueError(
                    f"cold full kill {candidate_id} requires a valid failed-call witness"
                )
            if node_id not in manifest_nodes:
                raise ValueError(
                    f"cold full kill {candidate_id} witness node is not in the R2 manifest"
                )
        elif mode == "witness-prefix":
            if not isinstance(evidence, dict) or evidence.get("command") != "r2":
                raise ValueError(
                    f"cold prefix kill {candidate_id} requires R2 evidence"
                )
            if evidence.get("started_count") is not None or evidence.get("failed_call_index") is not None:
                raise ValueError(
                    f"cold prefix kill {candidate_id} cannot carry started-prefix evidence"
                )
            if (
                not is_failed_call_witness(witness)
                or node_id not in manifest_nodes
                or execution.get("prior_node_id") != node_id
                or execution.get("current_node_id") != node_id
            ):
                raise ValueError(
                    f"cold prefix kill {candidate_id} witness is not bound to the R2 manifest"
                )
        else:
            raise ValueError(
                f"cold kill {candidate_id} has unsupported execution mode {mode!r}"
            )

    survived = mutation.get("survived")
    for bucket, outcomes in (("survived", survived), ("killed", killed)):
        if not isinstance(outcomes, list):
            raise ValueError(f"cold-witness {bucket} outcomes are not an array")
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                raise ValueError(f"cold-witness {bucket} outcome is not an object")
            execution = outcome.get("execution")
            evidence = outcome.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError(f"cold-witness {bucket} outcome has no collection evidence")
            if set(evidence) != evidence_fields:
                raise ValueError(
                    f"cold-witness {bucket} evidence must carry the six v15 fields"
                )
            count = evidence.get("collection_count")
            if type(count) is not int or count < 0:
                raise ValueError("candidate evidence collection_count must be an integer >= 0")
            for field in ("collection_sha256", "hook_fingerprint_sha256"):
                digest = evidence.get(field)
                if not isinstance(digest, str) or _CANDIDATE_ID.fullmatch(digest) is None:
                    raise ValueError(f"candidate evidence {field} must be a SHA-256 digest")
            started = evidence.get("started_count")
            failed_index = evidence.get("failed_call_index")
            if (started is None) != (failed_index is None):
                raise ValueError(
                    "candidate evidence started_count and failed_call_index must appear together"
                )
            if started is not None and (
                type(started) is not int
                or type(failed_index) is not int
                or started < 1
                or failed_index != started - 1
                or started > count
            ):
                raise ValueError("candidate evidence has invalid started-prefix facts")
            command = evidence.get("command")
            if bucket == "killed":
                mode = execution.get("mode") if isinstance(execution, dict) else None
                expected_command = "declared" if mode == "full" else "r2"
                if command != expected_command:
                    raise ValueError(
                        f"cold kill evidence command differs from execution mode {mode!r}"
                    )
            elif command not in ("r2", "declared"):
                raise ValueError("survivor evidence command must be 'r2' or 'declared'")
            elif evidence.get("started_count") is not None:
                raise ValueError("survivor evidence cannot carry started-prefix facts")
            baseline_name = "r2_baseline" if command == "r2" else "coverage_baseline"
            baseline = r2 if command == "r2" else coverage
            if evidence.get("collection_sha256") != baseline.get("collection_sha256"):
                raise ValueError(
                    f"candidate evidence collection differs from {baseline_name}"
                )
            for field in ("collection_count", "hook_fingerprint_sha256"):
                if evidence.get(field) != baseline.get(field):
                    raise ValueError(
                        f"candidate evidence {field} differs from {baseline_name}"
                    )

    try:
        committed_config = _read_committed_file(
            repo_root, expected_commit, f"{prefix}pyproject.toml"
        )
    except ValueError as exc:
        raise ValueError(
            f"R2 pytest config differs from assay/pyproject.toml at {expected_commit}"
        ) from exc
    if raw_command.get("config_sha256") != hashlib.sha256(committed_config).hexdigest():
        raise ValueError(
            f"R2 pytest config differs from assay/pyproject.toml at {expected_commit}"
        )
    if policy.get("equivalence_ledger") is not None:
        raise ValueError("ledger binding not implemented (B110-P10b)")


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
    plan: dict | Path | ValueError | None = None,
    r2_manifest: Path | None = None,
    deadline: Path,
) -> None:
    """Refuse a valid-but-adverse or internally unrelated verdict.

    ``plan`` is ``assay plan``'s JSON, required exactly when R2 is expected.
    ``deadline`` is the exact campaign deadline file passed to the producer.
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
        _git_argv("rev-parse", f"{expected_commit}^{{tree}}"),
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
    deadline_r2_lanes, deadline_plan_sha256 = _check_deadline_binding(
        document,
        repo_root=repo_root,
        deadline_path=deadline,
        expected_commit=expected_commit,
        expected_tree=expected_tree,
        expected_lane=expected_lane,
        expected_version=expected_version,
        expected_wheel_sha256=expected_wheel_sha256,
    )

    if "R2" in expected_rigor:
        if plan is None:
            raise ValueError("R2 is expected but no plan was given (--plan-json)")
        if isinstance(plan, Path):
            plan_path = plan
            try:
                plan = _read_bounded_json(plan_path, max_bytes=_PLAN_MAX_BYTES)
            except (OSError, ValueError) as exc:
                raise ValueError(f"cannot read plan {plan_path}: {exc}") from exc
        deadline_plan = _plan_structure(
            plan, expected_commit=expected_commit, expected_tree=expected_tree
        )
        expected_plan_sha256 = _ordered_plan_sha256(
            [row["id"] for row in deadline_plan["candidates"]]
        )
        if deadline_plan_sha256.get(expected_lane) != expected_plan_sha256:
            raise ValueError(
                f"deadline plan_sha256 for lane {expected_lane!r} "
                "does not match the ordered plan"
            )
    if "R2" in expected_rigor:
        assert plan is not None
        check_campaign_scope(
            document, plan, expected_commit=expected_commit, expected_tree=expected_tree
        )
        if r2_manifest is None:
            raise ValueError("--r2-manifest is required for an R2 report")
        _check_v15_r2(
            document,
            repo_root=repo_root,
            expected_commit=expected_commit,
            expected_lane=expected_lane,
            r2_manifest=r2_manifest,
        )
    _check_other_r2_deadline_plan_sha256(
        repo_root=repo_root,
        expected_commit=expected_commit,
        expected_tree=expected_tree,
        expected_lane=expected_lane,
        r2_lanes=deadline_r2_lanes,
        deadline_plan_sha256=deadline_plan_sha256,
    )

    # Verify the exact bounded snapshot parsed by this checker. The shell
    # also runs `assay verify`, but its separate file read cannot bind these
    # independent source/plan checks to the same bytes.
    from assay.verify import verify_document

    verifier_failures = verify_document(document)
    if verifier_failures:
        raise ValueError(
            "Assay verifier rejected the parsed verdict snapshot: "
            + "; ".join(verifier_failures)
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
    "deadline",
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
        _read_bounded_json(path, max_bytes=_RECEIPT_MAX_BYTES),
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
    parser.add_argument("--r2-manifest", type=Path)
    parser.add_argument("--deadline", type=Path)
    parser.add_argument("--deadline-wheel-check-only", action="store_true")
    args = parser.parse_args(argv)

    if args.deadline_wheel_check_only:
        if args.receipt_only:
            parser.error("--deadline-wheel-check-only cannot be combined with --receipt-only")
        extra = [
            f"--{name.replace('_', '-')}"
            for name in (
                *_FULL_FLAGS,
                "tester_unified_receipt",
                "plan_json",
                "r2_manifest",
            )
            if name not in ("expected_wheel_sha256", "deadline")
            and getattr(args, name) is not None
        ]
        if extra:
            parser.error(
                "--deadline-wheel-check-only takes only --deadline and "
                f"--expected-wheel-sha256: {', '.join(extra)}"
            )
        missing = [
            flag
            for flag, value in (
                ("--deadline", args.deadline),
                ("--expected-wheel-sha256", args.expected_wheel_sha256),
            )
            if value is None
        ]
        if missing:
            parser.error(f"--deadline-wheel-check-only requires {', '.join(missing)}")
        try:
            verify_deadline_wheel_sha256(
                args.deadline,
                expected_sha256=args.expected_wheel_sha256,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            print(f"B105_DEADLINE_WHEEL_REJECTED={exc}", file=sys.stderr)
            return 2
        print(f"B105_DEADLINE_WHEEL_SHA256={args.expected_wheel_sha256}")
        return 0

    if args.receipt_only:
        extra = [f"--{name.replace('_', '-')}" for name in (*_FULL_FLAGS, "plan_json", "r2_manifest") if name not in ("expected_commit", "expected_tree") and getattr(args, name) is not None]
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
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError, IndexError) as exc:
            print(f"B105_REPORT_REJECTED={exc}", file=sys.stderr)
            return 2
        print(f"B105_TESTER_UNIFIED_PASS=commit={args.expected_commit} tree={args.expected_tree}")
        return 0

    missing = [f"--{name.replace('_', '-')}" for name in _FULL_FLAGS if getattr(args, name) is None]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    if args.plan_json is not None and "R2" not in args.expected_rigor.split(","):
        parser.error("--plan-json is only valid when R2 is in --expected-rigor")
    if "R2" in args.expected_rigor.split(",") and args.r2_manifest is None:
        parser.error("--r2-manifest is required for an R2 report")
    if "R2" not in args.expected_rigor.split(",") and args.r2_manifest is not None:
        parser.error("--r2-manifest given for a report without R2")

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
        document = _read_bounded_json(args.report, max_bytes=_REPORT_MAX_BYTES)
        rigor = tuple(args.expected_rigor.split(","))
        if not rigor or any(not item for item in rigor):
            raise ValueError("expected rigor must be a non-empty comma-separated list")
        plan = args.plan_json
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
            r2_manifest=args.r2_manifest,
            deadline=args.deadline,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError, IndexError) as exc:
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
