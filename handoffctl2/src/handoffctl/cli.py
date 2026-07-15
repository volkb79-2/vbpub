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
  version                     handoffctl.__version__.

main(argv=None) -> int. Import module functions lazily inside handlers so
`handoffctl version` works even if an optional module is broken; handlers
catch HandoffctlError-family exceptions and print 'error: ...' to stderr
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
        # Lint specific paths - call lint_file directly
        for path_str in args.path:
            path = Path(path_str)
            # Use the first available project config (they're all the same for lint purposes)
            if registry:
                root = next(iter(registry.values()))
                cfg = config.ProjectConfig.load(root)
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

    for pid in projects:
        root = registry[pid]
        cfg = config.ProjectConfig.load(root)
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


def cmd_version(args) -> int:
    """version"""
    from . import __version__
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handoffctl", add_help=False, exit_on_error=False)
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
