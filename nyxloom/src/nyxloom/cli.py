"""Operator CLI. PACKAGE P10.

Thin argparse wiring over the modules; every state-changing verb appends an
audited event (actor OPERATOR with $USER). Output is plain aligned text
tables (no rich/click deps). Exit codes: 0 ok, 1 findings/failures, 2 usage.

INTERFACE CONTRACT (frozen) — subcommands:

  project add <id> <root>     config.register_project + paths.ensure_layout
                              + PROJECT_REGISTERED event.
  project list                registry table.
  lint [path ...]             no args -> lint_project for every registered
                              project; exit 1 if any has_blocking. Prints
                              'PATH:LINE RULE SEVERITY MESSAGE' lines.
  doctor [--project X] [--rebuild [--write]]
                              findings table; exit 1 on any severity in
                              {critical, error}. --rebuild prints diffs.
  status [--project X]        per task: id, state, since, attempt route,
                              cost, notes. Reads statefiles only.
  resync <project> [--apply] [--apply-content-merges]
                              PACKAGE RP01 2026-07-21 + RP02 (docs/plan-
                              state-integrity.md Part B): ground-truth
                              re-baseline. Reads statefiles + trove handoff
                              presence + git merge facts (branch --merged
                              AND a content-check fallback for a squash/
                              CAS/deleted-branch merge), plans via
                              resync.resync_plan, prints a table (task,
                              believed, ground-truth, proposed action,
                              evidence). Without --apply: PURE READ-ONLY,
                              unchanged RP01 dry-run (no writes, no
                              events). With --apply (RP02): emits the
                              audited transitions via
                              resync.resync_apply/storage.append_and_apply
                              (actor RESYNC) for every ACTION_ADVANCE row
                              backed by a high-confidence `git branch
                              --merged` hit; a row backed ONLY by the
                              content-check channel is left flagged unless
                              --apply-content-merges is ALSO passed (lower-
                              confidence evidence -- see resync.py's
                              module docstring). ACTION_NEEDS_OPERATOR rows
                              are NEVER auto-applied. Idempotent: a second
                              --apply performs no further writes. Allowed
                              on a PAUSED project (operator verb, not
                              daemon dispatch -- resync a project before
                              resuming it is the whole point).
  render                      render.render_all(registry); prints www path.
  migrate-store <project>     PACKAGE SP02 2026-07-21 (docs/plan-state-
                              integrity.md Part A.3): imports a project's
                              file-backend events.jsonl into the SQLite
                              backend (storage_sqlite), verifies ZERO
                              divergence against the on-disk statefiles,
                              then retires the source (events.jsonl ->
                              events.jsonl.pre-sqlite, kept as a backup,
                              never deleted). Idempotent. See
                              migrate_store.migrate for the full
                              contract; only ever run against a live
                              project at the SP03 cutover (not here).
  daemon [--foreground]       Daemon(registry).run() (foreground only in
                              the pilot; systemd/tmux owns daemonization).
  tick [--project X]          daemon.run_once — one pass, prints action
                              count. THE debug/fallback mode.
  decide <project> <D-id> --choose TEXT [--note TEXT]
                              decisions.decide(authority=$USER) +
                              DECISION_RESOLVED event (decision_id set).
  discuss <project> <D-id>    prints decisions.discuss command string.
  intake <project> <intake_id> <message>
                              P29 2026-07-16: feature-intake chat verb,
                              parallel to `discuss` -- advances one
                              intake_chat.advance_intake turn (launches on
                              the first call for a given intake_id, resumes
                              on subsequent calls) and prints the agent's
                              reply. The programmatic entry point P30's UI
                              calls.
  reject <project> <task> [--note TEXT]
                              P17 2026-07-15: merge-gate rejection.
                              MERGE_READY -> REVIEW_REJECTED via
                              TASK_TRANSITIONED (actor OPERATOR $USER); the
                              task not being MERGE_READY -> error, no event
                              written. Lets a merge authority (human or a
                              future auto-gate) that rejects AT the gate
                              route the task back to rework (re-enters
                              QUEUED the normal REVIEW_REJECTED way) without
                              a SUPERSEDE + statefile reset.
  merge <project> <task> [--commit SHA]
                              P17 2026-07-15: records a manual merge (SPEC
                              §7: auto-merge disabled). MERGE_READY ->
                              MERGED + MERGE_RECORDED{merge_commit}; commit
                              defaults to `git rev-parse HEAD` of the
                              project root (the REAL merge commit) rather
                              than a hand-padded placeholder. Prints the
                              recorded commit.
  pause <project> [task]      touch pause flag + PAUSE_SET event;
  resume <project> [task]     remove + PAUSE_CLEARED. (Project-level pause
                              writes the flag file; task-level also flows
                              into the statefile via the event projection.)
  leases                      leases.holder_info for every mutex declared
                              by any registered project (project + host).
  digest <project> [--since SEQ]   prints notify.digest.
  events <project> [--since SEQ] [--type T] [--tail] [--json]
                              PACKAGE SP04 2026-07-21 (docs/plan-state-
                              integrity.md A.3): the greppability bridge --
                              dumps the event store as JSONL to stdout via
                              storage.iter_events, which is backend-agnostic
                              (file or SQLite, per NYXLOOM_STATE_BACKEND),
                              restoring `| jq` / `| lnav` over the event log
                              regardless of backend. --since/--type filter
                              (--type unchanged from the original P10 debug
                              verb); --json is an explicit alias for the
                              already-JSONL default output (no other output
                              mode exists); --tail polls for new appends
                              after the initial dump and follows them
                              (KeyboardInterrupt during the poll -> clean
                              exit 0). Reads only -- never writes an event
                              or a statefile. An unknown/never-written
                              project is not an error: iter_events yields
                              nothing for it, so nothing is printed and the
                              exit code is 0.
  version                     nyxloom.__version__.
  init <project_folder>       PACKAGE P23. Scaffold nyxloom-trove/ into
                              <project_folder> from this package's bundled
                              templates (STANDARD.md + AUTHORING.md copied
                              verbatim, a fresh nyxloom.toml with [project]
                              id = basename(<project_folder>)). Refuses
                              (exit 1) if <project_folder>/nyxloom-trove/
                              already exists -- never overwrites. Missing
                              <project_folder> -> exit 2 (argparse usage).
                              PACKAGE F2: the scaffold itself now lives in
                              onboarding.scaffold_trove; this verb is a
                              thin wrapper around it.
  onboard <project_folder> [--maturity empty|partial|mature]
          [--docs present|absent] [--mode derive-from-code|
          code-good-docs-absent|greenfield-define-it]
          [--scan-path PATH ...]
                              PACKAGE F2 2026-07-17: the non-AI onboarding
                              wizard + spine instantiation (docs/nyxloom-
                              operating-model.md §2, onboarding.py). Ensures
                              a trove exists (reusing the `init` scaffold
                              above if none does -- never duplicates it),
                              then instantiates any MISSING direction-spine
                              doc (1-north-star.md .. 4-backlog.md) with
                              minimal-valid frontmatter, wires any MISSING
                              nyxloom.toml spine key, and records the wizard
                              answers to
                              <trove>/onboarding-answers.json. Idempotent:
                              an already-present spine doc / already-set
                              config key is left untouched. Deterministic,
                              scriptable, no AI/LLM invoked -- the AI scan
                              (F3, `--scan`) and guided questionnaire (F4b,
                              `--questionnaire`) are separate flags, below.
                              `--questionnaire` (PACKAGE F4b 2026-07-17):
                              requires a STORED assessment (`--scan` this
                              call or a prior one) -- errors clearly (exit
                              1, no dispatch) without one. Dispatches the
                              guided one-shot questionnaire agent, drafts
                              the direction spine via F4a's spine_writer,
                              self-lints it, and restores the prior spine
                              content on any failure (see
                              onboarding_questionnaire.py).

main(argv=None) -> int. Import module functions lazily inside handlers so
`nyxloom version` works even if an optional module is broken; handlers
catch NyxloomError-family exceptions and print 'error: ...' to stderr
(exit 1), never tracebacks (tracebacks only with --debug global flag).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap_logging() -> None:
    """PACKAGE P05c: every OTHER module this CLI dispatches into
    (config/lint/decisions/backlog_items/frontmatter/render/...) now carries
    `log.debug`/`log.info`/`log.warning` calls. structlog's OWN default (when
    `log.configure()` has never been called by anyone in the process) is an
    unfiltered PrintLogger straight to stdout -- see structlog's docs on its
    pre-configure default. A short-lived `nyxloom` CLI invocation (unlike the
    persistent daemon, which wires this in `Daemon.run()` via P02's
    `resolve_level()`) never otherwise calls `log.configure()`, so without
    this bootstrap the FIRST log call any dispatched command reaches would
    print a raw structlog line into the middle of this CLI's stdout/stderr --
    corrupting the exact `doctor`/`status` output contract this package's
    own byte-unchanged oracle guards (see docs/plan-logging.md P05c). Kept
    intentionally minimal: `console=False` so nothing but a JSONL file is
    ever written (the CLI's own stdout/stderr stays exactly the print
    statements below, untouched); the level honors NYXLOOM_LOG_LEVEL (the
    same env D-L3/resolve_level's layer 2 the daemon honors) so `nyxloom
    doctor`/`lint`/etc. share the daemon's one nyxloom.jsonl stream at a
    consistent verbosity, falling back to INFO on an unset/invalid value.
    Never imports daemon.py (that would defeat `nyxloom version`'s
    resilience to a broken optional module -- see this module's own
    docstring on lazy imports)."""
    from . import log as log_module, paths

    level = os.environ.get("NYXLOOM_LOG_LEVEL", "info")
    try:
        log_module.configure(level=level, log_dir=paths.logs_dir(), console=False)
    except ValueError:
        log_module.configure(level=log_module.INFO, log_dir=paths.logs_dir(), console=False)
    except OSError:  # pragma: no cover -- defensive; needs an unwritable state dir to trigger
        # Defensive (mirrors this module's own "nyxloom version works even
        # if an optional module is broken" resilience intent, above): a
        # state dir that cannot be created/written (read-only HOME, full
        # disk) must never take the whole CLI down over a diagnostics
        # side-channel. structlog's own pre-configure default (unfiltered
        # PrintLogger to stdout) is worse than no file handler at all here,
        # so fall back to a level-gated, handler-less config: WARNING+ still
        # risks stdlib's `lastResort` stderr fallback in this one degraded
        # case, but DEBUG/INFO (this package's own added calls) stay silent.
        log_module.configure(level=log_module.INFO, log_dir=None, console=False)


def _cfg(project: str):
    """Load ProjectConfig for a project ID. Raise if not found."""
    from . import config
    registry = config.load_registry()
    if project not in registry:
        raise RuntimeError(f"unknown project: {project}")
    return config.ProjectConfig.load(registry[project])


def _format_table(rows: list[dict], columns: list[str]) -> str:
    """Format a list of dicts as aligned columns."""
    if not rows:
        return ""
    # Determine column widths
    widths = {}
    for col in columns:
        widths[col] = len(col)
    for row in rows:
        for col in columns:
            val = str(row.get(col, ""))
            widths[col] = max(widths[col], len(val))

    lines = []
    # Header
    header_parts = []
    for col in columns:
        header_parts.append(col.ljust(widths[col]))
    lines.append("  ".join(header_parts))

    # Rows
    for row in rows:
        row_parts = []
        for col in columns:
            val = str(row.get(col, ""))
            row_parts.append(val.ljust(widths[col]))
        lines.append("  ".join(row_parts))

    return "\n".join(lines)


def cmd_project_add(args) -> int:
    """project add <id> <root>"""
    from . import config, paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = config.ProjectConfig.load(Path(args.root))
    config.register_project(args.id, Path(args.root))
    paths.ensure_layout(args.id)

    # Append PROJECT_REGISTERED event
    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
    storage.append_event(
        args.id,
        actor=actor,
        type=EventType.PROJECT_REGISTERED,
        payload={},
    )
    return 0


def cmd_project_list(args) -> int:
    """project list"""
    from . import config

    registry = config.load_registry()
    rows = []
    for pid, root in sorted(registry.items()):
        rows.append({"id": pid, "root": str(root)})

    if rows:
        print(_format_table(rows, ["id", "root"]))
    return 0


def cmd_lint(args) -> int:
    """lint [path ...]"""
    from . import config, lint

    registry = config.load_registry()
    all_findings = {}

    if args.path:
        # Lint specific paths - each against ITS OWN owning project's config
        for path_str in args.path:
            path = Path(path_str)
            cfg = lint.resolve_project_for_path(path, registry)
            if cfg is None:
                all_findings[path_str] = [lint.unresolved_project_finding(path)]
                continue
            findings = lint.lint_file(path, cfg)
            if findings:
                all_findings[path_str] = findings
    else:
        # Lint all registered projects
        for pid, root in registry.items():
            try:
                cfg = config.ProjectConfig.load(root)
                findings_dict = lint.lint_project(cfg)
                all_findings.update(findings_dict)
            except Exception:
                pass

    # Print findings
    has_error = False
    for relpath in sorted(all_findings.keys()):
        for finding in all_findings[relpath]:
            line = finding.line if finding.line is not None else "-"
            print(f"{relpath}:{line} {finding.rule} {finding.severity} {finding.message}")
            if finding.severity == "error":
                has_error = True

    if not all_findings:
        print("clean")

    return 1 if has_error else 0


def cmd_doctor(args) -> int:
    """doctor [--project X] [--rebuild [--write]]"""
    from . import config, doctor, storage

    registry = config.load_registry()
    all_findings = []

    if args.project:
        projects = [args.project] if args.project in registry else []
    else:
        projects = list(registry.keys())

    http_entries = []
    for pid in projects:
        root = registry[pid]
        cfg = config.ProjectConfig.load(root)
        http_entries.append((cfg.policy.http_port, cfg.policy.http_bind))
        findings = doctor.doctor_project(cfg)
        all_findings.extend(findings)

    # If rebuild mode, show diffs
    if args.rebuild:
        for pid in projects:
            replayed, diffs = doctor.rebuild(pid, write=False)
            if diffs:
                print(f"Project {pid} diffs:")
                for diff in diffs[:50]:  # Cap at 50 diffs per oracle
                    print(f"  {diff}")

        if args.write:
            for pid in projects:
                replayed, diffs = doctor.rebuild(pid, write=True)

    # Print findings as table
    rows = []
    for finding in all_findings:
        rows.append({
            "kind": finding.kind,
            "severity": finding.severity,
            "message": finding.message,
            "project": finding.project or "",
            "refs": ", ".join(finding.refs) if finding.refs else "",
        })

    if rows:
        print(_format_table(rows, ["kind", "severity", "message", "project", "refs"]))

    # Dashboard URL. The daemon serves the read-only HTTP/SSE surface at the
    # (port, bind) of the registered project with the lowest policy.http_port
    # (see daemon.py Daemon._chosen_http). P38 2026-07-16: on a private ciu
    # bridge network (docs/runtime-process-model.md §3) the bind is 0.0.0.0,
    # reachable from any co-networked container (e.g. the devcontainer) via
    # the "nyxloomd" alias every nyxloomd compose sets, in ADDITION to the
    # loopback address on the daemon host itself. A loopback bind (127.0.0.1,
    # the default) is reachable only on the daemon host.
    if http_entries:
        port, bind = min(http_entries, key=lambda pb: pb[0])
        if bind in ("0.0.0.0", "::"):
            print(f"\ndashboard: http://127.0.0.1:{port}  (on the daemon host) "
                  f"or http://nyxloomd:{port}  (bridge alias, from a co-networked "
                  "container e.g. the devcontainer) -- read-only")
        else:
            print(f"\ndashboard: http://{bind}:{port}  (read-only; loopback on the daemon host)")

    has_critical_or_error = any(f.severity in ("critical", "error") for f in all_findings)
    return 1 if has_critical_or_error else 0


def cmd_status(args) -> int:
    """status [--project X]"""
    from . import config, storage

    registry = config.load_registry()

    projects = []
    if args.project:
        if args.project in registry:
            projects = [args.project]
    else:
        projects = list(registry.keys())

    rows = []
    for pid in projects:
        states = storage.list_states(pid)
        for task_id, tsf in states.items():
            # Get newest attempt route
            route_id = ""
            if tsf.attempts:
                latest = tsf.attempts[-1]
                route_id = latest.route.route_id if latest.route else ""

            # Calculate cost
            cost_str = ""
            if tsf.attempts:
                total_cost = 0
                basis_list = []
                for att in tsf.attempts:
                    if att.usage:
                        if att.usage.cost is not None:
                            total_cost += att.usage.cost
                        basis_list.append(att.usage.basis.value if att.usage.basis else "unknown")

                if total_cost > 0:
                    basis_mix = "/".join(sorted(set(basis_list))) if basis_list else "unknown"
                    cost_str = f"{total_cost:.2f} ({basis_mix})"

            rows.append({
                "task_id": task_id,
                "state": tsf.state.value,
                "since": tsf.since.isoformat() if tsf.since else "",
                "route": route_id,
                "cost": cost_str,
                "notes": tsf.notes or "",
            })

    if rows:
        print(_format_table(rows, ["task_id", "state", "since", "route", "cost", "notes"]))
    return 0


def cmd_resync(args) -> int:
    """resync <project> [--apply] [--apply-content-merges]

    PACKAGE RP01 2026-07-21 + RP02: ground-truth re-baseline (docs/plan-
    state-integrity.md Part B.4). Gathers the three B.1 ground-truth
    sources (statefile belief via storage.list_states, handoff presence
    via resync.gather_handoff_presence, git merge facts via
    resync.gather_git_facts), plans via the pure resync.resync_plan, and
    prints the plan as a table -- unchanged RP01 behavior, always printed
    first (an --apply run must still show the plan it is about to act on).

    Without --apply: dry-run only, no writes, no events (RP01, unchanged).

    With --apply (RP02): hands the SAME plan to resync.resync_apply, which
    emits the audited transitions for every ACTION_ADVANCE row -- gated by
    the merge-evidence confidence split (see resync.py's module docstring
    and resync_apply's own docstring for the full SAFETY contract):
    a `git branch --merged`-backed row auto-applies; a row backed ONLY by
    the content-check channel applies only when --apply-content-merges is
    ALSO passed. ACTION_NEEDS_OPERATOR rows are never auto-applied (only
    reported). Prints an "applied N; skipped M" summary line plus one line
    per considered row. Deliberately does NOT check the project's pause
    flag -- resync is an operator verb, not daemon dispatch, and remaining
    resyncable while paused is the entire point (B.4's pre-resume use
    case).
    """
    from . import storage
    from .resync import (
        ACTION_NONE, gather_git_facts, gather_handoff_presence, resync_apply,
        resync_plan,
    )

    cfg = _cfg(args.project)
    states = storage.list_states(args.project)
    frontmatters = gather_handoff_presence(cfg, states)
    git_facts = gather_git_facts(str(cfg.root), cfg.default_branch, states)
    plan = resync_plan(states, frontmatters, git_facts)

    rows = [
        {
            "task_id": p.task_id,
            "believed": p.believed_state.value,
            "ground_truth": p.ground_truth,
            "proposed_action": p.proposed_action,
            "evidence": p.evidence,
        }
        for p in plan
    ]

    if rows:
        print(_format_table(
            rows, ["task_id", "believed", "ground_truth", "proposed_action", "evidence"]
        ))
    else:
        print("no tasks")

    if not getattr(args, "apply", False):
        return 0

    allow_content_merge = getattr(args, "apply_content_merges", False)
    results = resync_apply(
        args.project, states, plan, allow_content_merge=allow_content_merge,
    )

    applied = [r for r in results if r.applied]
    skipped = [r for r in results if not r.applied]
    considered = [p for p in plan if p.proposed_action != ACTION_NONE]
    print(f"\napplied {len(applied)}/{len(considered)} transition(s); "
          f"{len(skipped)} skipped")
    for r in applied:
        print(f"  applied  {r.task_id}: {r.reason}")
    for r in skipped:
        print(f"  skipped  {r.task_id}: {r.reason}")

    return 0


def cmd_render(args) -> int:
    """render"""
    from . import config, render

    registry = config.load_registry()
    www_path = render.render_all(registry)
    print(www_path)
    return 0


def cmd_migrate_store(args) -> int:
    """migrate-store <project>

    PACKAGE SP02 2026-07-21 (docs/plan-state-integrity.md Part A.3): thin
    wrapper -- all logic lives in migrate_store.migrate (import/verify/
    rename). Prints the resulting status + counts; a MigrationError
    (corrupt source line, divergence, or an inconsistent partial-import
    state) is caught here and reported the same way other verbs report
    domain errors: 'error: ...' to stderr, exit 1.
    """
    from .migrate_store import MigrationError, migrate

    try:
        result = migrate(args.project)
    except MigrationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result.status == "migrated":
        print(
            f"migrated: {result.imported_count} event(s) imported, "
            f"{len(result.task_ids)} task(s) verified zero-divergence"
        )
    elif result.status == "already-migrated":
        print("already-migrated: events.jsonl.pre-sqlite backup already present, nothing to do")
    else:
        print("nothing-to-migrate: no events.jsonl source found")
    return 0


def cmd_daemon(args) -> int:
    """daemon [--foreground]"""
    from . import config, daemon as daemon_mod

    registry = config.load_registry()
    d = daemon_mod.Daemon(registry)
    d.run()
    return 0


def cmd_tick(args) -> int:
    """tick [--project X]"""
    from . import daemon as daemon_mod

    action_count = daemon_mod.run_once(args.project if hasattr(args, 'project') else None)
    print(action_count)
    return 0


def cmd_decide(args) -> int:
    """decide <project> <D-id> --choose TEXT [--note TEXT]"""
    from . import config, decisions, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project)

    try:
        note = getattr(args, 'note', '') or ''
        decisions.decide(cfg, args.decision_id, args.choose, note,
                        os.environ.get("USER", "operator"))

        # Append DECISION_RESOLVED event
        actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
        storage.append_event(
            args.project,
            actor=actor,
            type=EventType.DECISION_RESOLVED,
            decision_id=args.decision_id,
            payload={},
        )
        return 0
    except decisions.DecisionError as e:
        if getattr(args, 'debug', False):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_discuss(args) -> int:
    """discuss <project> <D-id>"""
    from . import decisions

    cfg = _cfg(args.project)

    try:
        cmd_str = decisions.discuss(cfg, args.decision_id)
        print(cmd_str)
        return 0
    except decisions.DecisionError as e:
        if getattr(args, 'debug', False):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_intake(args) -> int:
    """intake <project> <intake_id> <message>"""
    from . import intake_chat

    cfg = _cfg(args.project)
    reply = intake_chat.advance_intake(cfg, args.project, args.intake_id, args.message)
    print(reply)
    return 0


def cmd_reject(args) -> int:
    """reject <project> <task> [--note TEXT]"""
    from . import storage
    from .types import (
        Actor, ActorKind, EventType, TaskState, TransitionError,
        check_task_transition,
    )

    _cfg(args.project)  # raises if the project isn't registered

    states = storage.list_states(args.project)
    tsf = states.get(args.task)
    if tsf is None:
        print(f"error: unknown task: {args.task}", file=sys.stderr)
        return 1

    from_state = tsf.state
    try:
        check_task_transition(from_state, TaskState.REVIEW_REJECTED)
    except TransitionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    note = getattr(args, "note", None) or "merge-gate rejection"
    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
    storage.append_and_apply(
        args.project,
        states,
        actor=actor,
        type=EventType.TASK_TRANSITIONED,
        payload={"from": from_state.value, "to": TaskState.REVIEW_REJECTED.value, "notes": note},
        task_id=args.task,
    )
    return 0


def cmd_merge(args) -> int:
    """merge <project> <task> [--commit SHA]"""
    import subprocess

    from . import storage
    from .types import (
        Actor, ActorKind, EventType, TaskState, TransitionError,
        check_task_transition,
    )

    cfg = _cfg(args.project)

    states = storage.list_states(args.project)
    tsf = states.get(args.task)
    if tsf is None:
        print(f"error: unknown task: {args.task}", file=sys.stderr)
        return 1

    from_state = tsf.state
    try:
        check_task_transition(from_state, TaskState.MERGED)
    except TransitionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    commit = getattr(args, "commit", None)
    if not commit:
        result = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"error: git rev-parse HEAD failed: {result.stderr.strip()}", file=sys.stderr)
            return 1
        commit = result.stdout.strip()

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
    storage.append_and_apply(
        args.project,
        states,
        actor=actor,
        type=EventType.TASK_TRANSITIONED,
        payload={"from": from_state.value, "to": TaskState.MERGED.value, "notes": None},
        task_id=args.task,
    )
    # P64 2026-07-20 (A12, D-061): carry a REAL progress_units (files the merge
    # changed) so the ratchet sees genuine progress -- parity with the daemon
    # auto-merge path (_merge_progress_units). Same git command; best-effort.
    _changed: list[str] = []
    try:
        _root = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True).stdout.strip() or str(cfg.root)
        _d = subprocess.run(
            ["git", "-C", _root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
            capture_output=True, text=True)
        if _d.returncode == 0:
            _changed = [ln.strip() for ln in _d.stdout.splitlines() if ln.strip()]
    except OSError:
        pass
    storage.append_and_apply(
        args.project,
        states,
        actor=actor,
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": commit, "progress_units": _changed, "source_kind": "review"},
        task_id=args.task,
    )

    # Best-effort: the merge is already durably recorded above, so a backlog
    # that cannot be read/written must warn, not sink the whole command.
    from . import backlog_items
    try:
        backlog_items.tick_merged(backlog_items.resolve_path(cfg), args.task, commit)
    except (OSError, UnicodeDecodeError) as e:
        print(f"warning: backlog auto-tick skipped: {e}", file=sys.stderr)

    print(commit)
    return 0


def cmd_pause(args) -> int:
    """pause <project> [task]"""
    from . import paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project)

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))

    if hasattr(args, 'task') and args.task:
        # Task-level pause
        flag_path = paths.pause_flag(args.project, args.task)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()

        # Load statefile to set paused=True
        states = storage.list_states(args.project)

        storage.append_and_apply(
            args.project,
            states,
            actor=actor,
            type=EventType.PAUSE_SET,
            task_id=args.task,
            payload={},
        )
    else:
        # Project-level pause
        flag_path = paths.pause_flag(args.project)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()

        states = storage.list_states(args.project)

        storage.append_event(
            args.project,
            actor=actor,
            type=EventType.PAUSE_SET,
            payload={},
        )

    return 0


def cmd_resume(args) -> int:
    """resume <project> [task]"""
    from . import paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project)

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))

    if hasattr(args, 'task') and args.task:
        # Task-level resume
        flag_path = paths.pause_flag(args.project, args.task)
        flag_path.unlink(missing_ok=True)

        states = storage.list_states(args.project)

        storage.append_and_apply(
            args.project,
            states,
            actor=actor,
            type=EventType.PAUSE_CLEARED,
            task_id=args.task,
            payload={},
        )
    else:
        # Project-level resume
        flag_path = paths.pause_flag(args.project)
        flag_path.unlink(missing_ok=True)

        states = storage.list_states(args.project)

        storage.append_event(
            args.project,
            actor=actor,
            type=EventType.PAUSE_CLEARED,
            payload={},
        )

    return 0


def cmd_leases(args) -> int:
    """leases"""
    from . import config, leases

    registry = config.load_registry()

    rows = []
    seen = set()

    # Collect all mutex names from all projects
    for pid, root in registry.items():
        try:
            cfg = config.ProjectConfig.load(root)
            for mutex_name, mutex_def in cfg.mutexes.items():
                lease_name = mutex_def.lease_name(pid)
                if lease_name not in seen:
                    seen.add(lease_name)
                    info_list = leases.holder_info(lease_name, mutex_def.capacity)
                    for info in info_list:
                        row = {
                            "name": lease_name,
                            "slot": info.get("slot", ""),
                            "held": "True" if info.get("held") else "False",
                            "owner": info.get("owner", ""),
                            "since": info.get("since", ""),
                        }
                        rows.append(row)
        except Exception:
            pass

    if rows:
        print(_format_table(rows, ["name", "slot", "held", "owner", "since"]))
    return 0


def cmd_digest(args) -> int:
    """digest <project> [--since SEQ]"""
    from . import notify

    cfg = _cfg(args.project)
    since_seq = int(args.since) if hasattr(args, 'since') and args.since else 0

    digest_text = notify.digest(cfg, args.project, since_seq)
    print(digest_text)
    return 0


def cmd_events(args) -> int:
    """events <project> [--since SEQ] [--type T] [--tail] [--json]

    PACKAGE SP04 2026-07-21 (docs/plan-state-integrity.md A.3 -- the
    greppability bridge). Dumps the event store as JSONL to stdout via
    storage.iter_events, which is backend-agnostic (file or SQLite, per
    NYXLOOM_STATE_BACKEND) -- so `nyxloom events P | jq` / `| lnav` works
    unchanged regardless of which backend is selected. Each printed line is
    `Event.to_dict()` JSON-encoded, the exact shape storage.py's file
    backend writes to events.jsonl, so a dump round-trips to the same
    records `iter_events` yields.

    Deliberately does NOT resolve the project through `_cfg`/the registry:
    storage.iter_events works directly off the project id string on both
    backends and simply yields nothing for a project with no events.jsonl /
    no state.db row, so an unknown or never-written project is not an
    error here -- it prints nothing and exits 0 (this is a read-only
    debug/grep tool, not a mutation path that needs config validation).

    --since SEQ   only sequence > SEQ (passed straight through to
                  iter_events(project, since=SEQ)).
    --type T      filter to one event type value (unchanged P10 behavior).
    --json        explicit alias for the (already-JSONL) default output --
                  accepted so a script can be unambiguous about the format
                  it depends on; there is no other output mode to select.
    --tail        after the initial dump, poll for new appends and emit
                  them as they arrive, tracking the highest sequence seen
                  so each poll only re-queries iter_events for the delta.
                  Interruptible: a KeyboardInterrupt (Ctrl-C) during the
                  poll is caught and the command exits 0 cleanly.

    Reads only -- iter_events is a pure SELECT/scan on both backends; this
    command never calls append_event/append_and_apply/save_state.
    """
    import json as json_lib
    import time

    from . import storage

    since_seq = int(args.since) if hasattr(args, 'since') and args.since else 0
    filter_type = args.type if hasattr(args, 'type') and args.type else None

    def _dump_since(last_seq: int) -> int:
        for ev in storage.iter_events(args.project, last_seq):
            if filter_type is None or ev.type.value == filter_type:
                print(json_lib.dumps(ev.to_dict()))
            last_seq = ev.sequence
        return last_seq

    last_seq = _dump_since(since_seq)

    if getattr(args, "tail", False):
        try:
            while True:
                time.sleep(1.0)
                last_seq = _dump_since(last_seq)
        except KeyboardInterrupt:
            pass

    return 0


def cmd_init(args) -> int:
    """init <project_folder>

    PACKAGE F2: the scaffold itself moved to onboarding.scaffold_trove (so
    `onboard` can reuse it without duplicating it); this is now a thin
    wrapper preserving the original P23 CLI contract (same messages/exit
    codes)."""
    from . import onboarding

    project_folder = Path(args.project_folder)
    try:
        trove_dir = onboarding.scaffold_trove(project_folder)
    except onboarding.OnboardingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(str(trove_dir))
    return 0


def cmd_onboard(args) -> int:
    """onboard <project_folder> [--maturity ...] [--docs ...] [--mode ...]
    [--scan-path PATH ...]

    PACKAGE F2 2026-07-17: NON-AI, deterministic onboarding wizard + spine
    instantiation (docs/nyxloom-operating-model.md §2). Ensures the project
    has a trove -- reusing `init`'s own scaffold (cmd_init above) if none
    exists yet, never duplicating it -- then hands off to
    onboarding.run_wizard to instantiate any missing spine doc, wire any
    missing nyxloom.toml spine key, and record the wizard answers. F4 (the
    guided questionnaire) is NOT built here -- it later consumes the
    recorded answers file (and, when `--scan` was passed, the assessment
    below).

    PACKAGE F3 2026-07-17: `--scan` is the follow-on AI step, kept strictly
    AFTER `run_wizard` returns (F2's non-AI wizard core stays pure -- see
    onboarding.py's own module docstring) -- it dispatches
    onboarding_scan.run_assessment_scan with the very answers just recorded.
    Skipped automatically for maturity=empty (nothing to scan; see
    onboarding_scan's greenfield short-circuit) even if `--scan` was passed.

    PACKAGE F4b 2026-07-17: `--questionnaire` is the follow-on guided
    one-shot draft, kept strictly AFTER `run_wizard` (and, in the same
    invocation, after `--scan` if both are passed together). It requires a
    STORED assessment (`onboarding-assessment.json`, written by a prior or
    this-same-call `--scan`) -- with none stored, prints a clear error and
    returns 1 WITHOUT dispatching. Otherwise dispatches
    onboarding_questionnaire.run_questionnaire, which proposes + drafts the
    direction spine (north-star/product-definition/roadmap/backlog) via
    F4a's spine_writer, self-lints the result, and restores the prior spine
    content on any failure (see onboarding_questionnaire's module
    docstring)."""
    from . import onboarding

    project_folder = Path(args.project_folder)
    trove_dir = project_folder / "nyxloom-trove"
    if not trove_dir.exists():
        rc = cmd_init(argparse.Namespace(project_folder=str(project_folder)))
        if rc != 0:
            return rc

    scan_paths = list(args.scan_paths) if getattr(args, "scan_paths", None) else ["."]
    try:
        answers = onboarding.WizardAnswers(
            maturity=args.maturity,
            docs_present=(args.docs == "present"),
            mode=args.mode,
            scan_paths=scan_paths,
        )
        result = onboarding.run_wizard(project_folder, answers)
    except onboarding.OnboardingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result.created_docs:
        print("created:")
        for rel in result.created_docs:
            print(f"  {rel}")
    if result.skipped_docs:
        print("already present (untouched):")
        for rel in result.skipped_docs:
            print(f"  {rel}")
    if result.wired_keys:
        print("wired nyxloom.toml keys: " + ", ".join(result.wired_keys))
    print(f"answers recorded: {result.answers_path}")

    if getattr(args, "scan", False):
        from . import onboarding_scan

        try:
            assessment = onboarding_scan.run_assessment_scan(project_folder, answers)
        except onboarding_scan.AssessmentScanError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        if assessment.skipped:
            print(f"scan skipped: {assessment.skip_reason}")
        else:
            print(f"assessment recorded: {onboarding_scan.assessment_path(trove_dir)}")
            print(f"  maturity: {assessment.maturity}")
            print(f"  gaps: {len(assessment.gaps)}")

    if getattr(args, "questionnaire", False):
        from . import onboarding_questionnaire, onboarding_scan

        if not onboarding_scan.assessment_path(trove_dir).exists():
            print(
                "error: no assessment recorded for this project -- run "
                "`onboard --scan` first",
                file=sys.stderr,
            )
            return 1

        try:
            q_result = onboarding_questionnaire.run_questionnaire(project_folder)
        except onboarding_questionnaire.QuestionnaireError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        print(
            f"spine drafted: {q_result.feature_count} features, "
            f"{q_result.milestone_count} milestones, "
            f"{q_result.backlog_count} backlog items -> "
            + ", ".join(str(p) for p in q_result.drafted_paths)
        )

    return 0


def cmd_version(args) -> int:
    """version"""
    from . import __version__
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    _bootstrap_logging()
    parser = argparse.ArgumentParser(prog="nyxloom", add_help=False, exit_on_error=False)
    parser.add_argument("--debug", action="store_true", help="Show tracebacks")

    subparsers = parser.add_subparsers(dest="cmd", help="Command")

    # project
    project_parser = subparsers.add_parser("project")
    project_subs = project_parser.add_subparsers(dest="project_cmd")

    add_parser = project_subs.add_parser("add")
    add_parser.add_argument("id", help="Project ID")
    add_parser.add_argument("root", help="Project root path")

    list_parser = project_subs.add_parser("list")

    # lint
    lint_parser = subparsers.add_parser("lint")
    lint_parser.add_argument("path", nargs="*", help="Handoff file paths (optional)")

    # doctor
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--project", help="Project ID (optional)")
    doctor_parser.add_argument("--rebuild", action="store_true", help="Rebuild mode")
    doctor_parser.add_argument("--write", action="store_true", help="Write changes")

    # status
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--project", help="Project ID (optional)")

    # resync
    resync_parser = subparsers.add_parser("resync")
    resync_parser.add_argument("project", help="Project ID")
    resync_parser.add_argument("--apply", action="store_true",
                                help="PACKAGE RP02: emit the audited re-baseline "
                                     "transitions (default: dry-run plan only)")
    resync_parser.add_argument("--apply-content-merges", action="store_true",
                                help="PACKAGE RP02 SAFETY opt-in: also apply "
                                     "ACTION_ADVANCE rows whose ONLY merge evidence "
                                     "is the (lower-confidence) content-check channel "
                                     "-- requires --apply")

    # render
    render_parser = subparsers.add_parser("render")

    # migrate-store
    migrate_store_parser = subparsers.add_parser("migrate-store")
    migrate_store_parser.add_argument("project", help="Project ID")

    # daemon
    daemon_parser = subparsers.add_parser("daemon")
    daemon_parser.add_argument("--foreground", action="store_true", help="Foreground mode")

    # tick
    tick_parser = subparsers.add_parser("tick")
    tick_parser.add_argument("--project", help="Project ID (optional)")

    # decide
    decide_parser = subparsers.add_parser("decide")
    decide_parser.add_argument("project", help="Project ID")
    decide_parser.add_argument("decision_id", help="Decision ID")
    decide_parser.add_argument("--choose", required=True, help="Choice")
    decide_parser.add_argument("--note", help="Note (optional)")

    # discuss
    discuss_parser = subparsers.add_parser("discuss")
    discuss_parser.add_argument("project", help="Project ID")
    discuss_parser.add_argument("decision_id", help="Decision ID")

    # intake
    intake_parser = subparsers.add_parser("intake")
    intake_parser.add_argument("project", help="Project ID")
    intake_parser.add_argument("intake_id", help="Intake ID")
    intake_parser.add_argument("message", help="Message to the intake agent")

    # reject
    reject_parser = subparsers.add_parser("reject")
    reject_parser.add_argument("project", help="Project ID")
    reject_parser.add_argument("task", help="Task ID")
    reject_parser.add_argument("--note", help="Rejection reason (optional)")

    # merge
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("project", help="Project ID")
    merge_parser.add_argument("task", help="Task ID")
    merge_parser.add_argument("--commit", help="Merge commit SHA (optional; default: git rev-parse HEAD)")

    # pause
    pause_parser = subparsers.add_parser("pause")
    pause_parser.add_argument("project", help="Project ID")
    pause_parser.add_argument("task", nargs="?", help="Task ID (optional)")

    # resume
    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("project", help="Project ID")
    resume_parser.add_argument("task", nargs="?", help="Task ID (optional)")

    # leases
    leases_parser = subparsers.add_parser("leases")

    # digest
    digest_parser = subparsers.add_parser("digest")
    digest_parser.add_argument("project", help="Project ID")
    digest_parser.add_argument("--since", help="Since sequence (optional)")

    # events
    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("project", help="Project ID")
    events_parser.add_argument("--since", help="Since sequence (optional)")
    events_parser.add_argument("--type", help="Event type (optional)")
    events_parser.add_argument("--tail", action="store_true",
                                help="Follow new events as they are appended (Ctrl-C to stop)")
    events_parser.add_argument("--json", action="store_true",
                                help="Explicit JSONL output (default; no other output mode exists)")

    # version
    version_parser = subparsers.add_parser("version")

    # init
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("project_folder", help="Target project folder to scaffold a trove into")

    # onboard (PACKAGE F2). Choices are hardcoded literals here (not
    # imported from onboarding.py) so parser construction -- which runs for
    # EVERY subcommand, including `version` -- never depends on importing an
    # optional module (mirrors main()'s "lazy import inside handlers"
    # design intent above); onboarding.WizardAnswers re-validates these same
    # choice sets at construction time regardless.
    onboard_parser = subparsers.add_parser("onboard")
    onboard_parser.add_argument("project_folder", help="Target project folder (trove scaffolded here if absent)")
    onboard_parser.add_argument("--maturity", choices=["empty", "partial", "mature"],
                                 default="empty", help="Project maturity (default: empty)")
    onboard_parser.add_argument("--docs", choices=["present", "absent"],
                                 default="absent", help="Whether the project already has real docs (default: absent)")
    onboard_parser.add_argument("--mode", choices=["derive-from-code", "code-good-docs-absent", "greenfield-define-it"],
                                 default="greenfield-define-it", help="Onboarding mode (default: greenfield-define-it)")
    onboard_parser.add_argument("--scan-path", action="append", dest="scan_paths", metavar="PATH",
                                 help="Path (repeatable) for the later AI scan (F3) to read; default: ['.']")
    onboard_parser.add_argument("--scan", action="store_true",
                                 help="PACKAGE F3: after the non-AI wizard, dispatch the read-only "
                                      "assessment scan agent (skipped automatically for --maturity empty)")
    onboard_parser.add_argument("--questionnaire", action="store_true",
                                 help="PACKAGE F4b: dispatch the guided one-shot questionnaire agent "
                                      "to draft the direction spine from a STORED assessment (run "
                                      "--scan first, or pass both --scan --questionnaire together)")

    try:
        args = parser.parse_args(argv)
    except (SystemExit, argparse.ArgumentError) as e:
        parser.print_help(sys.stderr)
        if isinstance(e, SystemExit):
            return e.code or 2
        return 2

    # Route to handler
    try:
        if args.cmd == "project":
            if args.project_cmd == "add":
                return cmd_project_add(args)
            elif args.project_cmd == "list":
                return cmd_project_list(args)
            else:
                parser.print_help(sys.stderr)
                return 2
        elif args.cmd == "lint":
            return cmd_lint(args)
        elif args.cmd == "doctor":
            return cmd_doctor(args)
        elif args.cmd == "status":
            return cmd_status(args)
        elif args.cmd == "resync":
            return cmd_resync(args)
        elif args.cmd == "render":
            return cmd_render(args)
        elif args.cmd == "migrate-store":
            return cmd_migrate_store(args)
        elif args.cmd == "daemon":
            return cmd_daemon(args)
        elif args.cmd == "tick":
            return cmd_tick(args)
        elif args.cmd == "decide":
            return cmd_decide(args)
        elif args.cmd == "discuss":
            return cmd_discuss(args)
        elif args.cmd == "intake":
            return cmd_intake(args)
        elif args.cmd == "reject":
            return cmd_reject(args)
        elif args.cmd == "merge":
            return cmd_merge(args)
        elif args.cmd == "pause":
            return cmd_pause(args)
        elif args.cmd == "resume":
            return cmd_resume(args)
        elif args.cmd == "leases":
            return cmd_leases(args)
        elif args.cmd == "digest":
            return cmd_digest(args)
        elif args.cmd == "events":
            return cmd_events(args)
        elif args.cmd == "version":
            return cmd_version(args)
        elif args.cmd == "init":
            return cmd_init(args)
        elif args.cmd == "onboard":
            return cmd_onboard(args)
        else:
            parser.print_help(sys.stderr)
            return 2
    except Exception as e:
        if args.debug:
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
