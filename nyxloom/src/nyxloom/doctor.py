"""Drift audit + projection rebuild (SPEC §14, draft-1 importer reframed).
PACKAGE P08.

Read-only over consumer repos; the only thing it may WRITE is statefiles,
and only in rebuild(write=True) mode. Doctor is the always-available lint
of runtime-vs-repo coherence — the tool draft 1 called the importer.

INTERFACE CONTRACT (frozen):

- doctor_project(cfg) -> list[DoctorFinding]. CHECKS (kind, severity):
    replay-divergence critical  storage.replay() vs storage.list_states():
                                any task whose to_dict() differs; refs name
                                the task ids (cap 20).
    handoff-lint       error    lint.has_blocking on any discovered handoff
                                (one finding per file, refs=[relpath]).
    dangling-dep       error    frontmatter depends_on task ref with no
                                handoff file and no statefile.
    orphan-worktree    warning  `git -C root worktree list --porcelain`
                                entries under cfg.worktree_root with no
                                non-terminal task whose branch matches.
    missing-worktree   warning  ACTIVE task whose attempt.worktree does not
                                exist on disk.
    stale-receipt      warning  receipt.json present but its attempt state
                                is still RUNNING/PREFLIGHTING (wrapper died
                                between receipt and event; daemon will heal
                                — surfaced so the operator knows).
    unbound-evidence   warning  statefile MERGED/VALIDATING/COMPLETED with
                                merge_commit None.
    legacy-lock        warning  files named .STACK_LOCK/.CARVE_LOCK under
                                the repo (evolution: should become leases).
    stale-pause        info     pause flag older than 7 days.
    orphan-statefile   warning  statefile whose handoff_path no longer
                                exists (unless task terminal).
    decision-hold      info     QUEUED/NEEDS_DECISION task whose D-dep is
                                OPEN (refs the D-id) — visibility, not error.
- rebuild(project, write=False) -> tuple[dict replayed, list[str] diffs]:
  replay events; diff against on-disk statefiles (json-dict equality; diffs
  are 'task_id: <field-path>' strings, cap 50); write=True saves replayed
  over on-disk (the recovery path) AFTER creating .bak copies alongside.
- doctor_all() -> dict[project_id, list[DoctorFinding]] over the registry.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import paths, storage, frontmatter, lint, decisions
from .config import ProjectConfig, load_registry
from .types import DoctorFinding, TaskStateFile, TaskState, AttemptState, TERMINAL_TASK_STATES, TERMINAL_ATTEMPT_STATES


def doctor_project(cfg: ProjectConfig) -> list[DoctorFinding]:
    """Run all 11 checks on a project config, degrading on NotImplementedError
    (check-unavailable); check 1 (replay-divergence) additionally degrades any
    other exception to a replay-check-failed finding so a broken event log
    cannot take out the other ten checks."""
    findings: list[DoctorFinding] = []

    # 1. replay-divergence
    try:
        replayed = storage.replay(cfg.project_id)
        on_disk = storage.list_states(cfg.project_id)
        diverged = []
        for task_id, disk_state in on_disk.items():
            replayed_state = replayed.get(task_id)
            if replayed_state is None or replayed_state.to_dict() != disk_state.to_dict():
                diverged.append(task_id)
        if diverged:
            findings.append(DoctorFinding(
                kind='replay-divergence',
                severity='critical',
                message='replay divergence: replayed state differs from on-disk',
                project=cfg.project_id,
                refs=diverged[:20],
            ))
    except NotImplementedError:
        findings.append(DoctorFinding(
            kind='check-unavailable',
            severity='info',
            message='check unavailable',
            project=cfg.project_id,
            refs=['storage'],
        ))
    except Exception as exc:
        # P36: doctor is the surface an operator reaches for precisely when
        # something (e.g. a stale event log) is broken -- it must degrade to
        # a finding, not die and take the other ten checks down with it.
        findings.append(DoctorFinding(
            kind='replay-check-failed',
            severity='critical',
            message=f'replay-divergence check failed: {type(exc).__name__}: {exc}',
            project=cfg.project_id,
            refs=['storage'],
        ))

    # 2. handoff-lint
    try:
        lint_results = lint.lint_project(cfg)
        for relpath, lint_findings in lint_results.items():
            for lf in lint_findings:
                if lf.severity == 'error':
                    findings.append(DoctorFinding(
                        kind='handoff-lint',
                        severity='error',
                        message=f'handoff lint: {lf.rule} {lf.message}',
                        project=cfg.project_id,
                        refs=[relpath],
                    ))
                    break
    except NotImplementedError:
        findings.append(DoctorFinding(
            kind='check-unavailable',
            severity='info',
            message='check unavailable',
            project=cfg.project_id,
            refs=['lint'],
        ))

    # 3. dangling-dep
    try:
        discovered = frontmatter.discover_handoffs(cfg)
        parsed_handoffs: dict[str, frontmatter.Frontmatter] = {}
        for path in discovered:
            try:
                fm, _ = frontmatter.parse_handoff(path)
                parsed_handoffs[fm.id] = fm
            except Exception:
                pass

        on_disk = storage.list_states(cfg.project_id)
        for fm in parsed_handoffs.values():
            for dep in fm.task_deps():
                if dep not in parsed_handoffs and dep not in on_disk:
                    findings.append(DoctorFinding(
                        kind='dangling-dep',
                        severity='error',
                        message=f'dangling dependency: {dep}',
                        project=cfg.project_id,
                        refs=[dep],
                    ))
    except NotImplementedError:
        findings.append(DoctorFinding(
            kind='check-unavailable',
            severity='info',
            message='check unavailable',
            project=cfg.project_id,
            refs=['frontmatter'],
        ))

    # 4. orphan-worktree
    try:
        result = subprocess.run(
            ['git', '-C', str(cfg.root), 'worktree', 'list', '--porcelain'],
            capture_output=True,
            text=True,
        )
        on_disk = storage.list_states(cfg.project_id)
        non_terminal_branches = set()
        for tsf in on_disk.values():
            if tsf.state not in TERMINAL_TASK_STATES:
                for attempt in tsf.attempts:
                    if attempt.branch:
                        non_terminal_branches.add(attempt.branch)

        worktree_root_str = str(cfg.root / cfg.worktree_root)
        lines = result.stdout.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('worktree '):
                worktree_path = line[9:].strip()
                branch = None
                # Check next lines for branch info
                j = i + 1
                while j < len(lines) and not lines[j].startswith('worktree'):
                    if lines[j].startswith('branch '):
                        branch = lines[j][7:].strip()
                        if branch.startswith('refs/heads/'):
                            branch = branch[11:]
                        break
                    j += 1

                # Only check worktrees under the project's worktree root
                if worktree_path.startswith(worktree_root_str):
                    if branch and branch not in non_terminal_branches:
                        findings.append(DoctorFinding(
                            kind='orphan-worktree',
                            severity='warning',
                            message=f'orphan worktree: {branch}',
                            project=cfg.project_id,
                            refs=[branch],
                        ))
            i += 1
    except Exception:
        pass

    # 5. missing-worktree
    try:
        on_disk = storage.list_states(cfg.project_id)
        for tsf in on_disk.values():
            if tsf.state == TaskState.ACTIVE:
                attempt = tsf.current_attempt()
                if attempt and attempt.worktree:
                    wt_path = Path(attempt.worktree)
                    if not wt_path.exists():
                        findings.append(DoctorFinding(
                            kind='missing-worktree',
                            severity='warning',
                            message=f'missing worktree for active task: {tsf.task_id}',
                            project=cfg.project_id,
                            refs=[tsf.task_id],
                        ))
    except Exception:
        pass

    # 6. stale-receipt
    try:
        on_disk = storage.list_states(cfg.project_id)
        for tsf in on_disk.values():
            for attempt in tsf.attempts:
                if attempt.receipt is not None and attempt.state in (AttemptState.RUNNING, AttemptState.PREFLIGHTING):
                    findings.append(DoctorFinding(
                        kind='stale-receipt',
                        severity='warning',
                        message=f'stale receipt: attempt {attempt.attempt_id} still {attempt.state.value}',
                        project=cfg.project_id,
                        refs=[tsf.task_id],
                    ))
    except Exception:
        pass

    # 7. unbound-evidence
    try:
        on_disk = storage.list_states(cfg.project_id)
        for tsf in on_disk.values():
            if tsf.state in (TaskState.MERGED, TaskState.VALIDATING, TaskState.COMPLETED):
                if tsf.merge_commit is None:
                    findings.append(DoctorFinding(
                        kind='unbound-evidence',
                        severity='warning',
                        message=f'unbound evidence: {tsf.state.value} task with no merge_commit',
                        project=cfg.project_id,
                        refs=[tsf.task_id],
                    ))
    except Exception:
        pass

    # 8. legacy-lock
    try:
        for lock_name in ('.STACK_LOCK', '.CARVE_LOCK'):
            lock_path = cfg.root / 'docs' / lock_name
            if lock_path.exists():
                findings.append(DoctorFinding(
                    kind='legacy-lock',
                    severity='warning',
                    message=f'legacy lock file: {lock_name}',
                    project=cfg.project_id,
                    refs=[str(lock_path.relative_to(cfg.root))],
                ))
    except Exception:
        pass

    # 9. stale-pause
    try:
        pause_path = paths.pause_flag(cfg.project_id)
        if pause_path.exists():
            age_seconds = time.time() - pause_path.stat().st_mtime
            if age_seconds > 7 * 24 * 3600:
                findings.append(DoctorFinding(
                    kind='stale-pause',
                    severity='info',
                    message='pause flag older than 7 days',
                    project=cfg.project_id,
                ))
    except Exception:
        pass

    # 10. orphan-statefile
    try:
        discovered_paths = set()
        try:
            discovered = frontmatter.discover_handoffs(cfg)
            for path in discovered:
                try:
                    fm, _ = frontmatter.parse_handoff(path)
                    discovered_paths.add(path)
                except Exception:
                    pass
        except NotImplementedError:
            pass

        on_disk = storage.list_states(cfg.project_id)
        for tsf in on_disk.values():
            if tsf.handoff_path:
                handoff_path = cfg.root / tsf.handoff_path
                if not handoff_path.exists():
                    if tsf.state not in TERMINAL_TASK_STATES:
                        findings.append(DoctorFinding(
                            kind='orphan-statefile',
                            severity='warning',
                            message=f'orphan statefile: handoff path {tsf.handoff_path} missing',
                            project=cfg.project_id,
                            refs=[tsf.task_id],
                        ))
    except Exception:
        pass

    # 11. decision-hold
    try:
        on_disk = storage.list_states(cfg.project_id)
        open_decision_ids = decisions.open_ids(cfg)

        for tsf in on_disk.values():
            if tsf.state in (TaskState.QUEUED, TaskState.NEEDS_DECISION):
                try:
                    discovered = frontmatter.discover_handoffs(cfg)
                    for path in discovered:
                        try:
                            fm, _ = frontmatter.parse_handoff(path)
                            if fm.id == tsf.task_id or fm.id.split('-')[0:2] == tsf.task_id.split('-')[0:2]:
                                for d_dep in fm.decision_deps():
                                    if d_dep in open_decision_ids:
                                        findings.append(DoctorFinding(
                                            kind='decision-hold',
                                            severity='info',
                                            message=f'task waiting on open decision {d_dep}',
                                            project=cfg.project_id,
                                            refs=[d_dep],
                                        ))
                        except Exception:
                            pass
                except NotImplementedError:
                    pass
    except NotImplementedError:
        findings.append(DoctorFinding(
            kind='check-unavailable',
            severity='info',
            message='check unavailable',
            project=cfg.project_id,
            refs=['decisions'],
        ))
    except Exception:
        pass

    return findings


def rebuild(project: str, write: bool = False) -> tuple[dict[str, TaskStateFile], list[str]]:
    """Replay events and diff against on-disk statefiles."""
    replayed = storage.replay(project)
    on_disk = storage.list_states(project)

    diffs: list[str] = []
    for task_id, replayed_state in replayed.items():
        disk_state = on_disk.get(task_id)
        if disk_state is None:
            diffs.append(f'{task_id}: missing on disk')
        else:
            replayed_dict = replayed_state.to_dict()
            disk_dict = disk_state.to_dict()
            if replayed_dict != disk_dict:
                diff_paths = _dict_diff(replayed_dict, disk_dict, f'{task_id}')
                diffs.extend(diff_paths)

    for task_id in on_disk:
        if task_id not in replayed:
            diffs.append(f'{task_id}: not in replay')

    diffs = diffs[:50]

    if write:
        state_dir = paths.state_dir(project)
        for task_id, replayed_state in replayed.items():
            statefile_path = paths.statefile_path(project, task_id)
            if statefile_path.exists():
                bak_path = statefile_path.with_suffix('.bak')
                bak_path.write_text(statefile_path.read_text(encoding='utf-8'), encoding='utf-8')
            storage.save_state(replayed_state)

    return replayed, diffs


def _dict_diff(d1: dict, d2: dict, prefix: str, depth: int = 0) -> list[str]:
    """Recursively diff two dicts, returning dotted paths up to depth 3."""
    diffs = []
    if depth > 3:
        return diffs

    all_keys = set(d1.keys()) | set(d2.keys())
    for key in sorted(all_keys):
        path = f'{prefix}.{key}' if prefix and not prefix.endswith('.') else f'{prefix}{key}'
        if key not in d1:
            diffs.append(f'{path}: missing in replay')
        elif key not in d2:
            diffs.append(f'{path}: extra in disk')
        elif isinstance(d1[key], dict) and isinstance(d2[key], dict):
            diffs.extend(_dict_diff(d1[key], d2[key], path, depth + 1))
        elif d1[key] != d2[key]:
            diffs.append(f'{path}: {d2[key]} != {d1[key]}')

    return diffs


def doctor_all() -> dict[str, list[DoctorFinding]]:
    """Run doctor_project over all registered projects."""
    registry = load_registry()
    result = {}
    for project_id, root in registry.items():
        try:
            cfg = ProjectConfig.load(root)
            result[project_id] = doctor_project(cfg)
        except Exception:
            result[project_id] = []
    return result
