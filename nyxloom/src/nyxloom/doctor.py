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

CR-16 2026-08-03 (liveness, channel health, silent-failure detection;
RISK-007) adds three checks, folded into doctor_project's sweep AND
available standalone via `liveness_findings(cfg)` (the fast path
`nyxloom doctor --liveness` and the container healthcheck use — see
cli.cmd_doctor and nyxloomd/docker-compose.yml):
    reconcile-deadman   critical no evidence of a completed reconcile pass
                                (RECONCILE_HEARTBEAT, or any event, absent a
                                heartbeat) within reconcile_interval_seconds
                                * cfg.policy.deadman_multiple.
    tick-error-streak   critical watchdog.detect_runaways' 'tick-error-streak'
                                pattern fired over the recent event window:
                                the daemon is looping (so the deadman above
                                is silent) but raising every pass.
    notify-transport-unreachable
                        critical notify.probe_transport(cfg.notify) reports
                                'unreachable'. 'unconfigured'/'healthy' ->
                                no finding, same convention as
                                docker-transport-lying below.
These three are the reason `nyxloom doctor` — a plain CLI invocation that
constructs no Daemon and starts no HTTP server — is RISK-007's escape
path: every one of them is readable, and reportable via this process's own
exit code, with the daemon dead, wedged, or its own push channel down.
- rebuild(project, write=False) -> tuple[dict replayed, list[str] diffs]:
  replay events; diff against on-disk statefiles (json-dict equality; diffs
  are 'task_id: <field-path>' strings, cap 50); write=True saves replayed
  over on-disk (the recovery path) AFTER creating .bak copies alongside.
- doctor_all() -> dict[project_id, list[DoctorFinding]] over the registry.
- doctor_host() -> list[DoctorFinding]. Host-scoped (project=None), added for
  the two host-level hazards a per-project check cannot see (docs/
  infra/agent-cli/README.md, infra/slices/README.md):
    docker-transport-lying critical  transport_check.probe_default() reports
                                      `lying` (canonical L18 defeat 4);
                                      `unavailable` and `healthy` -> no finding.
    cgroup-slice-missing   critical  a `--cgroup-parent=<slice>` named in any
                                      registered project's gate argv does not
                                      exist as a real systemd unit (fail-open
                                      hazard: an unbounded transient slice).
    cgroup-slice-unverified warning  the systemctl probe for a named slice
                                      itself failed (never a silent skip).
    cgroup-slice-unchecked info      no systemctl on this host at all (slice
                                      checks skipped entirely; not an error).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import paths, storage, frontmatter, lint, decisions, notify, transport_check, watchdog
from .config import ProjectConfig, load_registry
from .types import (
    DoctorFinding, EventType, TaskStateFile, TaskState, AttemptState,
    TERMINAL_TASK_STATES, TERMINAL_ATTEMPT_STATES, utc_now,
)


_LOSSY_ATTEMPT_FIELDS = ("usage", "receipt", "session_handle")


def _replayable_projection(tsf: TaskStateFile) -> dict:
    """The subset of a TaskStateFile's to_dict() that storage.replay() can
    genuinely reconstruct from the event log, for divergence comparison.

    replay() rebuilds attempts via ATTEMPT_* upsert-by-attempt_id (see
    storage.apply_event) and CANNOT faithfully reconstruct certain rich
    per-attempt fields in every history: usage/cost, receipt, and
    session_handle. A task with a complex or duplicate-TASK_CREATED history
    (a second TASK_CREATED replaces the whole projected statefile, per
    storage.apply_event) or an in-flight synthetic carve task (ACTIVE state
    the daemon sets directly on TASK_CREATED) can transiently show a
    replayed attempt with these fields unset/stale even though the task's
    actual state (and everything else replay derives) matches on-disk
    exactly. Comparing the full to_dict() therefore raises false positives
    on these fields alone; strip them before comparing so only a genuine
    divergence (starting with `.state`, but including every other field
    replay is expected to derive precisely) is ever flagged.
    """
    d = tsf.to_dict()
    d["attempts"] = [
        {k: v for k, v in att.items() if k not in _LOSSY_ATTEMPT_FIELDS}
        for att in (d.get("attempts") or [])
    ]
    return d


#: liveness_findings reads a bounded tail of the event log -- the same
#: window convention daemon.py's watchdog callers use (`[-500:]`) -- so a
#: project with years of history costs this check nothing extra.
_LIVENESS_EVENT_WINDOW = 500


def _deadman_finding(cfg: ProjectConfig, events: list) -> DoctorFinding | None:
    """CR-16 (RISK-007): a project this daemon has stopped reconciling is
    invisible to a TCP healthcheck and to every push-based alarm, because
    both require the daemon to be the one raising them. Read the last
    evidence of a completed pass straight from the store -- the SAME
    RECONCILE_HEARTBEAT `daemon.Daemon._record_heartbeat` writes at the end
    of every run_pass, win, lose, or draw -- so this answers correctly even
    when the daemon process that used to write it is gone. No RECONCILE_
    HEARTBEAT at all falls back to the most recent event of any type (a
    just-started project has a DAEMON_STARTED to anchor on); no events at
    all means the project has never ticked, which is not yet a fault."""
    heartbeats = [ev.timestamp for ev in events if ev.type is EventType.RECONCILE_HEARTBEAT]
    if heartbeats:
        reference = max(heartbeats)
    elif events:
        reference = max(ev.timestamp for ev in events)
    else:
        return None
    threshold = cfg.policy.reconcile_interval_seconds * cfg.policy.deadman_multiple
    age = (utc_now() - reference).total_seconds()
    if age <= threshold:
        return None
    return DoctorFinding(
        kind='reconcile-deadman',
        severity='critical',
        message=(f'no evidence of a completed reconcile pass in {int(age)}s '
                  f'(threshold {threshold}s = reconcile_interval_seconds * deadman_multiple)'),
        project=cfg.project_id,
        refs=[],
    )


def _tick_error_streak_finding(cfg: ProjectConfig, events: list) -> DoctorFinding | None:
    """CR-16 (RISK-007): a daemon that is up, looping (so `_deadman_finding`
    above stays silent), healthy by its TCP check, and raising every single
    pass -- reusing watchdog.detect_runaways' pattern (d) rather than a
    second, divergent scan of the same event shape."""
    signals = watchdog.detect_runaways(events, watchdog.WatchdogConfig())
    streak = next((s for s in signals if s.pattern == "tick-error-streak"), None)
    if streak is None:
        return None
    return DoctorFinding(
        kind='tick-error-streak',
        severity='critical',
        message=f'daemon failing every pass: {streak.detail}',
        project=cfg.project_id,
        refs=[],
    )


def _transport_finding(cfg: ProjectConfig) -> DoctorFinding | None:
    """CR-16 (RISK-007): the second, independent alarm path. An active probe
    of the SAME channel notify.notify_event would use -- but this call, and
    the finding it produces, never travels over that channel: it is reported
    through doctor's findings table and this process's own exit code, which
    is exactly what makes it independent of the transport it is checking."""
    probe = notify.probe_transport(cfg.notify)
    if probe.status != "unreachable":
        return None
    return DoctorFinding(
        kind='notify-transport-unreachable',
        severity='critical',
        message=f'{probe.channel} transport unreachable: {probe.detail}',
        project=cfg.project_id,
        refs=[probe.channel] if probe.channel else [],
    )


def liveness_findings(cfg: ProjectConfig) -> list[DoctorFinding]:
    """CR-16 (RISK-007): the three checks that answer "is this project's
    reconciliation actually alive", not just "is a socket listening" --
    deadman, TICK_ERROR streak, and notification-transport reachability.
    Deliberately separable from doctor_project's other 11 checks (rather
    than only reachable by running the whole sweep) so a fast, narrowly-
    scoped caller -- the container healthcheck via `nyxloom doctor
    --liveness` -- pays for exactly these three, not a full replay-
    divergence pass and a git subprocess call, on a tight polling interval.
    """
    try:
        events = list(storage.iter_events(cfg.project_id))[-_LIVENESS_EVENT_WINDOW:]
    except Exception as exc:  # census: cleanup/containment (CR-16)
        # The event log is the thing every one of these three checks reads.
        # Unreadable is not "nothing to report" -- it is the loudest possible
        # liveness fault, so it becomes one itself rather than three silent
        # skips.
        return [DoctorFinding(
            kind='liveness-check-failed',
            severity='critical',
            message=f'liveness check could not read the event log: {type(exc).__name__}: {exc}',
            project=cfg.project_id,
            refs=['storage'],
        )]
    findings: list[DoctorFinding | None] = []
    for kind, check in (
        ("reconcile-deadman", lambda: _deadman_finding(cfg, events)),
        ("tick-error-streak", lambda: _tick_error_streak_finding(cfg, events)),
        ("notify-transport-unreachable", lambda: _transport_finding(cfg)),
    ):
        try:
            findings.append(check())
        except Exception as exc:  # census: cleanup/containment (CR-16)
            # Isolation between the three checks (same idea as doctor_
            # project's per-check try/except below): one check's own bug
            # must not silence the other two on a surface whose entire job
            # is reporting, not staying quiet.
            findings.append(DoctorFinding(
                kind='liveness-check-failed',
                severity='critical',
                message=f'{kind} check failed: {type(exc).__name__}: {exc}',
                project=cfg.project_id,
                refs=[kind],
            ))
    return [f for f in findings if f is not None]


def doctor_project(cfg: ProjectConfig) -> list[DoctorFinding]:
    """Run all 11 checks on a project config, degrading on NotImplementedError
    (check-unavailable); check 1 (replay-divergence) additionally degrades any
    other exception to a replay-check-failed finding so a broken event log
    cannot take out the other ten checks. CR-16 folds in three more --
    reconcile-deadman, tick-error-streak, notify-transport-unreachable --
    via `liveness_findings` (see its docstring for why it also stands alone)."""
    findings: list[DoctorFinding] = list(liveness_findings(cfg))

    # 1. replay-divergence
    try:
        replayed = storage.replay(cfg.project_id)
        on_disk = storage.list_states(cfg.project_id)
        diverged = []
        for task_id, disk_state in on_disk.items():
            replayed_state = replayed.get(task_id)
            if (replayed_state is None
                    or _replayable_projection(replayed_state) != _replayable_projection(disk_state)):
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
        # CR-04b: ONE consistent backup of the whole store before repairing it,
        # replacing the per-statefile `.bak` copies the file backend needed.
        # A repair that rewrites every projection row is exactly when a
        # backup has to exist, and it has to be a point in time -- per-row
        # copies could not be, since a crash between two of them left a
        # backup that was half old and half new.
        storage.backup(project)
        for replayed_state in replayed.values():
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


# ---------------------------------------------------------------------------
# host-level checks (infra/agent-cli/README.md, infra/slices/README.md)

# Matches `--cgroup-parent=<name>` and the space-separated `--cgroup-parent
# <name>` form, anywhere inside a joined argv string (a gate's real docker
# invocation is typically nested inside `bash -c "..."`, so this deliberately
# scans the joined command text rather than positional argv parsing).
_CGROUP_PARENT_RE = re.compile(r'--cgroup-parent(?:=|\s+)([^\s\'"]+)')


def _cgroup_slices_in_argv(argv: list[str]) -> set[str]:
    """Every distinct systemd `.slice` unit name a gate's argv names via
    `--cgroup-parent`. Non-`.slice` values (unexpected, but tolerated) are
    ignored -- only slice units are what `nyxloom doctor` can verify via
    `systemctl show`."""
    joined = ' '.join(argv)
    return {name for name in _CGROUP_PARENT_RE.findall(joined) if name.endswith('.slice')}


def _slice_load_state(slice_name: str, timeout_s: int = 10) -> str:
    """The raw systemd `LoadState` for a slice unit, via `systemctl show`.

    Raises RuntimeError on ANY probe failure (missing binary mid-race,
    timeout, unparseable output) -- callers must turn that into a 'could not
    verify' finding rather than silently treating a failed probe as
    'exists'. A genuinely missing unit is NOT an error here: real systemd
    always answers with `LoadState=not-found` for an unknown unit name (exit
    0), so that case flows through normally and is judged by the caller.
    """
    try:
        proc = subprocess.run(
            ['systemctl', 'show', slice_name, '--property=LoadState'],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'systemctl show {slice_name} failed: {exc}') from exc
    for line in proc.stdout.splitlines():
        if line.startswith('LoadState='):
            return line.split('=', 1)[1].strip()
    raise RuntimeError(
        f'systemctl show {slice_name} produced no LoadState= line '
        f'(exit {proc.returncode}): {proc.stderr.strip()}'
    )


def doctor_host() -> list[DoctorFinding]:
    """Host-scoped checks with no single owning project (project=None on
    every finding). Two hazards, both "error path aliasing a benign result"
    shapes (canonical L18, one layer down in infrastructure):

    1. docker-transport: a lying attached-stream transport (canonical
       reference/LESSONS.md L18 defeat 4) makes every containerised gate
       verdict untrustworthy. `lying` -> critical. `unavailable` (no docker
       CLI/daemon reachable here) is explicitly NOT a fault -- this host may
       simply have no docker at all (e.g. the gate's own tester-unified
       container) -- so it produces no finding at all, never info/error/
       critical. `healthy` -> no finding.
    2. cgroup-slice-missing: every `--cgroup-parent=<slice>` named in any
       registered project's gate argv must exist as a real systemd unit, or
       the container it bounds runs UNBOUNDED (systemd silently fabricates
       an unlimited transient slice for an unknown name -- verified
       2026-07-27, infra/slices/README.md). Missing -> critical. A probe
       that itself fails (systemctl error/timeout) -> warning (never a
       silent skip). No systemd on this host at all -> a single info
       finding, and the per-slice probe is never attempted (a host with no
       systemd cannot honor slice limits regardless of what a project's
       gate argv asks for).
    """
    findings: list[DoctorFinding] = []

    # 1. docker transport
    probe = transport_check.probe_default()
    if probe.lying:
        findings.append(DoctorFinding(
            kind='docker-transport-lying',
            severity='critical',
            message=probe.detail,
            project=None,
            refs=['reference/LESSONS.md#L18'],
        ))
    # 'unavailable' is deliberately silent (see docstring above); 'healthy'
    # likewise produces no finding.

    # 2. cgroup slice existence, across every registered project's gates
    slice_owners: dict[str, list[str]] = {}
    try:
        registry = load_registry()
    except Exception:
        registry = {}
    for project_id, root in registry.items():
        try:
            cfg = ProjectConfig.load(root)
        except Exception:
            continue
        for gate_id, gate in cfg.gates.items():
            for slice_name in _cgroup_slices_in_argv(gate.argv):
                slice_owners.setdefault(slice_name, []).append(f'{project_id}:{gate_id}')

    if slice_owners:
        if shutil.which('systemctl') is None:
            findings.append(DoctorFinding(
                kind='cgroup-slice-unchecked',
                severity='info',
                message=(
                    'no systemctl on this host: cannot verify that the cgroup '
                    'slices configured gates dispatch into actually exist (a '
                    'host with no systemd cannot honor --cgroup-parent limits '
                    'at all)'
                ),
                project=None,
                refs=sorted(slice_owners),
            ))
        else:
            for slice_name in sorted(slice_owners):
                owners = slice_owners[slice_name]
                try:
                    load_state = _slice_load_state(slice_name)
                except RuntimeError as exc:
                    findings.append(DoctorFinding(
                        kind='cgroup-slice-unverified',
                        severity='warning',
                        message=f'could not verify cgroup slice {slice_name!r}: {exc}',
                        project=None,
                        refs=owners,
                    ))
                    continue
                if load_state != 'loaded':
                    findings.append(DoctorFinding(
                        kind='cgroup-slice-missing',
                        severity='critical',
                        message=(
                            f'cgroup slice {slice_name!r} does not exist '
                            f'(LoadState={load_state!r}): a container dispatched '
                            'with --cgroup-parent naming a missing slice runs '
                            'UNBOUNDED -- systemd silently fabricates a '
                            'transient slice with no limits (fail-open); this '
                            'is fail-closed reporting of that hazard'
                        ),
                        project=None,
                        refs=owners,
                    ))

    return findings
