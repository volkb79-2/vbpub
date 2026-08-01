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
import shutil
import subprocess
import sys
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
    cfg = AuditConfig(
        project=str(audit["project"]),
        project_root=Path(str(audit["project_root"])),
        source_roots=tuple(Path(x) for x in roots),
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


def _source_jobs(repo: Path, cfg: AuditConfig, max_mutants: int | None) -> list[tuple[str, Mutant]]:
    jobs: list[tuple[str, Mutant]] = []
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
                jobs.append((relative, mutant))
                if max_mutants is not None and len(jobs) >= max_mutants:
                    return jobs
    return jobs


def _mutant_id(path: str, mutant: Mutant, ordinal: int) -> str:
    safe = path.replace("/", "__").replace(".py", "")
    return f"{ordinal:07d}-{safe}-L{mutant.lineno}-{mutant.operator}"


def _run_one(repo: Path, cfg: AuditConfig, path: str, mutant: Mutant, ordinal: int,
             logs: Path, allow_infra: bool) -> dict[str, Any]:
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
            _hook(cfg.snapshot_restore, cwd=repo / cfg.project_root, env=env, label="snapshot_restore")
            _hook(cfg.reset, cwd=repo / cfg.project_root, env=env, label="reset")
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
    parser.add_argument("--allow-infra", action="store_true", help="allow trusted [infra] hooks")
    args = parser.parse_args(argv)
    cfg = load_config(args.manifest)
    repo = Path.cwd().resolve()
    project = repo / cfg.project_root
    if not (repo / ".git").exists() or not project.is_dir():
        raise SystemExit("run from the audited repository root; project_root must exist")
    if (cfg.setup or cfg.reset or cfg.teardown or cfg.snapshot_create or cfg.snapshot_restore or cfg.snapshot_destroy) and not args.allow_infra:
        raise SystemExit("manifest declares [infra] hooks; pass --allow-infra explicitly")

    args.report_dir.mkdir(parents=True, exist_ok=True)
    logs = args.report_dir / "logs"
    logs.mkdir(exist_ok=True)
    events = (args.report_dir / "events.jsonl").open("w", encoding="utf-8")
    env = dict(os.environ)
    env.update(cfg.test_env)
    sha = _run(("git", "rev-parse", "HEAD"), repo, env, 30).stdout.strip()
    _record(events, {"kind": "run", "project": cfg.project, "commit": sha, "started_at": _now(), "manifest": str(args.manifest)})
    counts = {outcome: 0 for outcome in ("KILLED", "SURVIVED", "TIMEOUT", "ERROR")}
    mutant_count = 0
    baseline_outcome = "NOT_RUN"
    return_code = 2
    try:
        if args.allow_infra:
            _hook(cfg.setup, cwd=project, env=env, label="setup")
            _hook(cfg.snapshot_create, cwd=project, env=env, label="snapshot_create")
        baseline = _run(cfg.test_argv, project, env, cfg.timeout_seconds)
        (logs / "baseline.stdout.log").write_text(baseline.stdout, encoding="utf-8")
        (logs / "baseline.stderr.log").write_text(baseline.stderr, encoding="utf-8")
        if baseline.returncode:
            baseline_outcome = "BASELINE_BROKEN"
            _record(events, {"kind": "baseline", "outcome": "BASELINE_BROKEN", "exit_code": baseline.returncode, "finished_at": _now()})
            return_code = 3
        else:
            baseline_outcome = "PASS"
            _record(events, {"kind": "baseline", "outcome": "PASS", "finished_at": _now()})
            jobs = _source_jobs(repo, cfg, args.max_mutants)
            _record(events, {"kind": "inventory", "mutants": len(jobs), "finished_at": _now()})
            results: list[dict[str, Any]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.jobs) as pool:
                futures = [pool.submit(_run_one, repo, cfg, path, mutant, n, logs, args.allow_infra) for n, (path, mutant) in enumerate(jobs, 1)]
                for future in futures:
                    result = future.result()
                    results.append(result)
                    _record(events, result)
            mutant_count = len(results)
            counts = {outcome: sum(r["outcome"] == outcome for r in results) for outcome in counts}
            return_code = 2 if counts["ERROR"] or counts["TIMEOUT"] else (1 if counts["SURVIVED"] else 0)
    except Exception as exc:
        counts["ERROR"] += 1
        _record(events, {"kind": "runner", "outcome": "ERROR", "error": str(exc), "finished_at": _now()})
    finally:
        if args.allow_infra:
            try:
                _hook(cfg.snapshot_destroy, cwd=project, env=env, label="snapshot_destroy")
                _hook(cfg.teardown, cwd=project, env=env, label="teardown")
            except Exception as exc:
                counts["ERROR"] += 1
                return_code = 2
                _record(events, {"kind": "teardown", "outcome": "ERROR", "error": str(exc), "finished_at": _now()})
        summary = {
            "project": cfg.project, "commit": sha, "baseline": baseline_outcome,
            "mutants": mutant_count, "counts": counts, "finished_at": _now(),
        }
        (args.report_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        events.close()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
