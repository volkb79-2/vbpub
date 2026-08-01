#!/usr/bin/env python3
"""Whole-project, commit-addressed mutation audit worker.

This is deliberately an AUDIT, not a nyxloom merge gate.  It runs every
supported mutation in the source roots named by a trusted project manifest,
continues after survivors/errors, and writes a durable JSONL evidence record.
The host launcher creates a disposable clone and tester image; this program runs
inside that tester image.

Lifecycle hooks are trusted project configuration, not model input.  They allow
an audit to bring up resettable test infrastructure.  A non-empty reset/snapshot
hook requires jobs=1 so externally mutable state cannot cross-contaminate mutant
workers.  Source mutation itself is always isolated in a disposable git worktree.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import socket
import subprocess
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nyxloom.mutation_gate import Mutant, generate_mutants


WORKTREE_LOCK = threading.Lock()
PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True)
class AuditConfig:
    project: str
    project_root: Path
    source_roots: tuple[Path, ...]
    test_argv: tuple[str, ...]
    test_env: dict[str, str]
    jobs: int
    timeout_seconds: int
    setup: tuple[tuple[str, ...], ...]
    reset: tuple[tuple[str, ...], ...]
    teardown: tuple[tuple[str, ...], ...]
    snapshot_create: tuple[tuple[str, ...], ...]
    snapshot_restore: tuple[tuple[str, ...], ...]
    snapshot_destroy: tuple[tuple[str, ...], ...]


def _commands(raw: Any, name: str) -> tuple[tuple[str, ...], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(
        not isinstance(cmd, list) or not cmd or any(not isinstance(part, str) for part in cmd)
        for cmd in raw
    ):
        raise ValueError(f"[infra].{name} must be an array of non-empty argv arrays")
    return tuple(tuple(cmd) for cmd in raw)


def load_config(manifest: Path) -> AuditConfig:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    audit = data.get("audit", {})
    infra = data.get("infra", {})
    required = ("project", "project_root", "source_roots", "test_argv")
    missing = [key for key in required if key not in audit]
    if missing:
        raise ValueError(f"[audit] missing required key(s): {', '.join(missing)}")
    roots = audit["source_roots"]
    argv = audit["test_argv"]
    if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
        raise ValueError("[audit].source_roots must be an array of strings")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("[audit].test_argv must be a non-empty argv array")
    env = audit.get("test_env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("[audit].test_env must be a string table")
    project_name = str(audit["project"])
    if PROJECT_RE.fullmatch(project_name) is None:
        raise ValueError("[audit].project must be a safe 1-128 character path component")
    project_root = _relative_path(audit["project_root"], "[audit].project_root", allow_dot=True)
    source_roots = tuple(_relative_path(x, "[audit].source_roots", allow_dot=False) for x in roots)
    cfg = AuditConfig(
        project=project_name,
        project_root=project_root,
        source_roots=source_roots,
        test_argv=tuple(argv),
        test_env=dict(env),
        jobs=max(1, int(audit.get("jobs", 1))),
        timeout_seconds=max(1, int(audit.get("timeout_seconds", 600))),
        setup=_commands(infra.get("setup"), "setup"),
        reset=_commands(infra.get("reset"), "reset"),
        teardown=_commands(infra.get("teardown"), "teardown"),
        snapshot_create=_commands(infra.get("snapshot_create"), "snapshot_create"),
        snapshot_restore=_commands(infra.get("snapshot_restore"), "snapshot_restore"),
        snapshot_destroy=_commands(infra.get("snapshot_destroy"), "snapshot_destroy"),
    )
    if (cfg.reset or cfg.snapshot_restore) and cfg.jobs != 1:
        raise ValueError("reset/snapshot_restore hooks require [audit].jobs = 1")
    return cfg


def _relative_path(raw: Any, name: str, *, allow_dot: bool) -> Path:
    path = Path(str(raw))
    if path.is_absolute() or ".." in path.parts or (not allow_dot and path == Path(".")):
        raise ValueError(f"{name} must stay beneath the audited checkout")
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run(argv: tuple[str, ...], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)


def _hook(commands: tuple[tuple[str, ...], ...], *, cwd: Path, env: dict[str, str], label: str) -> None:
    for argv in commands:
        proc = _run(argv, cwd, env, 1800)
        if proc.returncode:
            raise RuntimeError(f"{label} failed ({proc.returncode}): {' '.join(argv)}\n{proc.stderr[-4000:]}")


def _record(handle, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")
    handle.flush()


def _audit_verdict(counts: dict[str, int], mutant_count: int) -> tuple[str, int]:
    if mutant_count == 0:
        return "INCONCLUSIVE_NO_MUTANTS", 2
    if counts["ERROR"] or counts["TIMEOUT"]:
        return "INCONCLUSIVE_ERRORS", 2
    if counts["SURVIVED"]:
        return "SURVIVORS", 1
    return "PASS", 0


def _host_lifecycle(socket_path: Path, operation: str, mutant_id: str | None = None) -> None:
    token = os.environ.get("NYXLOOM_ZFS_BROKER_TOKEN", "")
    if not token:
        raise RuntimeError("host lifecycle capability token is missing")
    request = {"operation": operation, "token": token}
    if mutant_id is not None:
        request["mutant_id"] = mutant_id
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1800)
        client.connect(str(socket_path))
        client.sendall((json.dumps(request, sort_keys=True) + "\n").encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    try:
        reply = json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"invalid host lifecycle response for {operation}") from exc
    if reply.get("outcome") != "PASS":
        raise RuntimeError(f"host lifecycle {operation} failed: {reply.get('error', 'unknown error')}")


def _source_jobs(
    repo: Path,
    cfg: AuditConfig,
    max_mutants: int | None,
    start_at: int,
    shard_index: int,
    shard_count: int,
) -> tuple[int, list[tuple[int, str, Mutant]]]:
    jobs: list[tuple[int, str, Mutant]] = []
    inventory_count = 0
    project = repo / cfg.project_root
    for root in cfg.source_roots:
        absolute = project / root
        if not absolute.is_dir():
            raise ValueError(f"source root does not exist: {absolute}")
        for source in sorted(absolute.rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            lines = set(range(1, len(text.splitlines()) + 1))
            relative = source.relative_to(project).as_posix()
            for mutant in generate_mutants(text, lines):
                inventory_count += 1
                if inventory_count < start_at or (inventory_count - 1) % shard_count != shard_index:
                    continue
                jobs.append((inventory_count, relative, mutant))
                if max_mutants is not None and len(jobs) >= max_mutants:
                    return inventory_count, jobs
    return inventory_count, jobs


def _mutant_id(path: str, mutant: Mutant, ordinal: int) -> str:
    safe = path.replace("/", "__").replace(".py", "")
    return f"{ordinal:07d}-{safe}-L{mutant.lineno}-{mutant.operator}"


def _run_one(repo: Path, cfg: AuditConfig, path: str, mutant: Mutant, ordinal: int,
             logs: Path, allow_infra: bool, host_lifecycle_socket: Path | None) -> dict[str, Any]:
    started = time.monotonic()
    mutant_id = _mutant_id(path, mutant, ordinal)
    scratch = repo / ".worktrees" / f"mutation-audit-{ordinal}-{uuid.uuid4().hex[:8]}"
    env = dict(os.environ)
    env.update(cfg.test_env)
    env["NYXLOOM_MUTATION_AUDIT_ID"] = mutant_id
    result: dict[str, Any] = {
        "kind": "mutant", "id": mutant_id, "path": path, "line": mutant.lineno,
        "operator": mutant.operator, "description": mutant.description, "started_at": _now(),
    }
    try:
        if allow_infra:
            _hook(cfg.reset, cwd=repo / cfg.project_root, env=env, label="reset")
        if host_lifecycle_socket is not None:
            _host_lifecycle(host_lifecycle_socket, "restore", mutant_id)
        if allow_infra:
            _hook(cfg.snapshot_restore, cwd=repo / cfg.project_root, env=env, label="snapshot_restore")
        with WORKTREE_LOCK:
            add = _run(("git", "-C", str(repo), "worktree", "add", "--detach", str(scratch), "HEAD"), repo, env, 180)
        if add.returncode:
            raise RuntimeError(f"worktree add failed: {add.stderr[-4000:]}")
        target = scratch / cfg.project_root / path
        target.write_text(mutant.mutated_source, encoding="utf-8")
        try:
            proc = _run(cfg.test_argv, scratch / cfg.project_root, env, cfg.timeout_seconds)
            (logs / f"{mutant_id}.stdout.log").write_text(proc.stdout, encoding="utf-8")
            (logs / f"{mutant_id}.stderr.log").write_text(proc.stderr, encoding="utf-8")
            result["outcome"] = "KILLED" if proc.returncode else "SURVIVED"
            result["test_exit_code"] = proc.returncode
        except subprocess.TimeoutExpired as exc:
            (logs / f"{mutant_id}.stdout.log").write_text(exc.stdout or "", encoding="utf-8")
            (logs / f"{mutant_id}.stderr.log").write_text(exc.stderr or "", encoding="utf-8")
            result["outcome"] = "TIMEOUT"
            result["error"] = f"test command exceeded {cfg.timeout_seconds}s"
    except Exception as exc:  # audit must continue and preserve infrastructure failures
        result["outcome"] = "ERROR"
        result["error"] = str(exc)
    finally:
        if scratch.exists():
            with WORKTREE_LOCK:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(scratch)], capture_output=True, text=True)
        if allow_infra:
            try:
                _hook(cfg.reset, cwd=repo / cfg.project_root, env=env, label="post_mutant_reset")
                if host_lifecycle_socket is not None:
                    _host_lifecycle(host_lifecycle_socket, "restore", mutant_id)
                _hook(cfg.snapshot_restore, cwd=repo / cfg.project_root, env=env, label="post_mutant_snapshot_restore")
            except Exception as exc:
                result["outcome"] = "ERROR"
                result["error"] = f"post-mutant reset failed: {exc}"
    result["finished_at"] = _now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--max-mutants", type=int, help="bounded smoke/pilot only")
    parser.add_argument("--start-at", type=int, default=1, help="first stable whole-tree mutant ordinal")
    parser.add_argument("--shard-index", type=int, default=0, help="zero-based shard index")
    parser.add_argument("--shard-count", type=int, default=1, help="number of stable whole-tree shards")
    parser.add_argument("--allow-infra", action="store_true", help="allow trusted [infra] hooks")
    parser.add_argument("--host-lifecycle-socket", type=Path, help="narrow host snapshot broker socket")
    args = parser.parse_args(argv)
    if args.max_mutants is not None and args.max_mutants < 1:
        parser.error("--max-mutants must be positive")
    if args.start_at < 1:
        parser.error("--start-at must be positive")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("require --shard-count > 0 and 0 <= --shard-index < --shard-count")
    cfg = load_config(args.manifest)
    repo = Path.cwd().resolve()
    project = repo / cfg.project_root
    if not (repo / ".git").exists() or not project.is_dir():
        raise SystemExit("run from the audited repository root; project_root must exist")
    if (cfg.setup or cfg.reset or cfg.teardown or cfg.snapshot_create or cfg.snapshot_restore or cfg.snapshot_destroy or args.host_lifecycle_socket) and not args.allow_infra:
        raise SystemExit("manifest declares [infra] hooks; pass --allow-infra explicitly")
    if args.host_lifecycle_socket is not None and cfg.jobs != 1:
        raise SystemExit("host lifecycle snapshots require [audit].jobs = 1")

    args.report_dir.mkdir(parents=True, exist_ok=True)
    logs = args.report_dir / "logs"
    logs.mkdir(exist_ok=True)
    events = (args.report_dir / "events.jsonl").open("w", encoding="utf-8")
    env = dict(os.environ)
    env.update(cfg.test_env)
    sha = _run(("git", "rev-parse", "HEAD"), repo, env, 30).stdout.strip()
    evidence = {
        "image_id": os.environ.get("NYXLOOM_AUDIT_IMAGE_ID"),
        "image_reference": os.environ.get("NYXLOOM_AUDIT_IMAGE_REFERENCE"),
        "manifest_sha256": os.environ.get("NYXLOOM_AUDIT_MANIFEST_SHA256"),
        "worker_commit": os.environ.get("NYXLOOM_AUDIT_WORKER_COMMIT", sha),
    }
    selection = {
        "start_at": args.start_at, "max_mutants": args.max_mutants,
        "shard_index": args.shard_index, "shard_count": args.shard_count,
    }
    configuration = {
        "source_roots": [str(path) for path in cfg.source_roots],
        "test_argv": list(cfg.test_argv), "jobs": cfg.jobs,
        "timeout_seconds": cfg.timeout_seconds,
    }
    _record(events, {
        "kind": "run", "project": cfg.project, "commit": sha,
        "started_at": _now(), "manifest": str(args.manifest),
        "evidence": evidence, "selection": selection, "configuration": configuration,
    })
    counts = {outcome: 0 for outcome in ("KILLED", "SURVIVED", "TIMEOUT", "ERROR")}
    mutant_count = 0
    baseline_outcome = "NOT_RUN"
    baseline_seconds: float | None = None
    audit_outcome = "INCONCLUSIVE_ERRORS"
    return_code = 2
    audit_started = time.monotonic()
    try:
        if args.allow_infra:
            _hook(cfg.setup, cwd=project, env=env, label="setup")
            _hook(cfg.snapshot_create, cwd=project, env=env, label="snapshot_create")
        if args.host_lifecycle_socket is not None:
            _host_lifecycle(args.host_lifecycle_socket, "snapshot_create")
        baseline_started = time.monotonic()
        baseline = _run(cfg.test_argv, project, env, cfg.timeout_seconds)
        baseline_seconds = round(time.monotonic() - baseline_started, 3)
        (logs / "baseline.stdout.log").write_text(baseline.stdout, encoding="utf-8")
        (logs / "baseline.stderr.log").write_text(baseline.stderr, encoding="utf-8")
        if baseline.returncode:
            baseline_outcome = "BASELINE_BROKEN"
            audit_outcome = "BASELINE_BROKEN"
            _record(events, {"kind": "baseline", "outcome": "BASELINE_BROKEN", "exit_code": baseline.returncode, "elapsed_seconds": baseline_seconds, "finished_at": _now()})
            return_code = 3
        else:
            baseline_outcome = "PASS"
            _record(events, {"kind": "baseline", "outcome": "PASS", "elapsed_seconds": baseline_seconds, "finished_at": _now()})
            inventory_count, jobs = _source_jobs(
                repo, cfg, args.max_mutants, args.start_at, args.shard_index, args.shard_count,
            )
            _record(events, {"kind": "inventory", "scanned_through": inventory_count, "selected": len(jobs), "finished_at": _now()})
            results: list[dict[str, Any]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.jobs) as pool:
                futures = [
                    pool.submit(
                        _run_one, repo, cfg, path, mutant, ordinal, logs,
                        args.allow_infra, args.host_lifecycle_socket,
                    )
                    for ordinal, path, mutant in jobs
                ]
                for future in futures:
                    result = future.result()
                    results.append(result)
                    _record(events, result)
            mutant_count = len(results)
            counts = {outcome: sum(r["outcome"] == outcome for r in results) for outcome in counts}
            audit_outcome, return_code = _audit_verdict(counts, mutant_count)
    except Exception as exc:
        counts["ERROR"] += 1
        _record(events, {"kind": "runner", "outcome": "ERROR", "error": str(exc), "finished_at": _now()})
    finally:
        if args.allow_infra:
            try:
                _hook(cfg.reset, cwd=project, env=env, label="final_reset")
                if args.host_lifecycle_socket is not None:
                    _host_lifecycle(args.host_lifecycle_socket, "restore")
                _hook(cfg.snapshot_destroy, cwd=project, env=env, label="snapshot_destroy")
                if args.host_lifecycle_socket is not None:
                    _host_lifecycle(args.host_lifecycle_socket, "snapshot_destroy")
                _hook(cfg.teardown, cwd=project, env=env, label="teardown")
            except Exception as exc:
                counts["ERROR"] += 1
                audit_outcome = "INCONCLUSIVE_ERRORS"
                return_code = 2
                _record(events, {"kind": "teardown", "outcome": "ERROR", "error": str(exc), "finished_at": _now()})
        summary = {
            "project": cfg.project, "commit": sha, "outcome": audit_outcome,
            "baseline": baseline_outcome, "baseline_seconds": baseline_seconds,
            "mutants": mutant_count, "counts": counts, "evidence": evidence,
            "selection": selection, "configuration": configuration,
            "elapsed_seconds": round(time.monotonic() - audit_started, 3),
            "finished_at": _now(),
        }
        (args.report_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        events.close()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
