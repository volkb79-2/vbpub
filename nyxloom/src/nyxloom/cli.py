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
  render                      render.render_all(registry); prints www path.
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
  unpause <project> [task]    remove + PAUSE_CLEARED. (Project-level pause
                              writes the flag file; task-level also flows
                              into the statefile via the event projection.)
  leases                      leases.holder_info for every mutex declared
                              by any registered project (project + host).
  digest <project> [--since SEQ]   prints notify.digest.
  events <project> [--since SEQ] [--type T]   raw event lines (debug).
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
                              (F3) and guided questionnaire (F4) are
                              separate, NOT built here.

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


def cmd_render(args) -> int:
    """render"""
    from . import config, render

    registry = config.load_registry()
    www_path = render.render_all(registry)
    print(www_path)
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
    storage.append_and_apply(
        args.project,
        states,
        actor=actor,
        type=EventType.MERGE_RECORDED,
        payload={"merge_commit": commit},
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


def cmd_unpause(args) -> int:
    """unpause <project> [task]"""
    from . import paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project)

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))

    if hasattr(args, 'task') and args.task:
        # Task-level unpause
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
        # Project-level unpause
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
    """events <project> [--since SEQ] [--type T]"""
    from . import storage

    cfg = _cfg(args.project)

    since_seq = int(args.since) if hasattr(args, 'since') and args.since else 0
    filter_type = args.type if hasattr(args, 'type') and args.type else None

    for ev in storage.iter_events(args.project, since_seq):
        if filter_type is None or ev.type.value == filter_type:
            # Print event as JSON line
            import json
            print(json.dumps(ev.to_dict()))

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
    onboarding_scan's greenfield short-circuit) even if `--scan` was passed."""
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

    return 0


def cmd_version(args) -> int:
    """version"""
    from . import __version__
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
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

    # render
    render_parser = subparsers.add_parser("render")

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

    # unpause
    unpause_parser = subparsers.add_parser("unpause")
    unpause_parser.add_argument("project", help="Project ID")
    unpause_parser.add_argument("task", nargs="?", help="Task ID (optional)")

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
        elif args.cmd == "render":
            return cmd_render(args)
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
        elif args.cmd == "unpause":
            return cmd_unpause(args)
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
