"""Domain handlers used by Nyxloom's installed command registries.

The public grammar, generated help, and dispatch live in :mod:`cli_registry`.
Handlers keep their lazy imports and domain-specific safeguards here.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import argparse


def _bootstrap_logging(args=None, *, persist: bool = True) -> None:
    """Configure Nyxloom diagnostics only after a command has parsed.

    Local authoring and harness commands configure a quiet in-memory logging
    path so structlog cannot corrupt stdout, but they do not create host state.
    Operator commands retain the shared host JSONL log. ``--log-level`` wins
    over the environment default; ``--quiet`` and ``--debug`` follow the
    cli-extended meanings.
    """
    from . import log as log_module, paths

    explicit = getattr(args, "log_level", None) if args is not None else None
    if explicit is not None:
        level = explicit
    elif args is not None and bool(getattr(args, "quiet", False)):
        level = "warning"
    elif args is not None and (
        bool(getattr(args, "debug", False))
        or bool(getattr(args, "debug_raw", False))
    ):
        level = "debug"
    else:
        level = os.environ.get("NYXLOOM_LOG_LEVEL", "info")

    log_dir = paths.logs_dir() if persist else None
    try:
        log_module.configure(level=level, log_dir=log_dir, console=False)
    except ValueError:
        log_module.configure(level=log_module.INFO, log_dir=log_dir, console=False)
    except OSError:
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


def _print_lint_results(all_findings: dict) -> int:
    has_error = False
    for relpath in sorted(all_findings):
        for finding in all_findings[relpath]:
            line = finding.line if finding.line is not None else "-"
            print(f"{relpath}:{line} {finding.rule} {finding.severity} {finding.message}")
            if finding.severity == "error":
                has_error = True
    if not all_findings:
        print("clean")
    return 1 if has_error else 0


def cmd_lint_local(args) -> int:
    """Lint explicit handoff paths or the project containing the current cwd."""
    from . import lint

    all_findings = {}
    paths_to_check = list(args.path or [])
    if paths_to_check:
        for path_str in paths_to_check:
            path = Path(path_str)
            cfg = lint.resolve_project_for_path(path, {})
            if cfg is None:
                all_findings[path_str] = [lint.unresolved_project_finding(path)]
                continue
            findings = lint.lint_file(path, cfg)
            if findings:
                all_findings[path_str] = findings
    else:
        cfg = lint.resolve_project_for_path(Path.cwd(), {})
        if cfg is None:
            path = Path.cwd()
            all_findings[str(path)] = [lint.unresolved_project_finding(path)]
        else:
            all_findings = lint.lint_project(cfg)
    return _print_lint_results(all_findings)


def cmd_lint_registered(args) -> int:
    """Lint every project registered in the local host registry."""
    from . import config, lint

    all_findings = {}
    for _project_id, root in config.load_registry().items():
        try:
            cfg = config.ProjectConfig.load(root)
            all_findings.update(lint.lint_project(cfg))
        except Exception as exc:
            key = str(root / "nyxloom-trove" / "nyxloom.toml")
            all_findings[key] = [lint.LintFinding(
                rule="L0",
                severity="error",
                message=("project config could not be loaded: "
                         f"{type(exc).__name__}: {str(exc)[:300]}"),
                path=key,
            )]
    return _print_lint_results(all_findings)


def cmd_doctor(args) -> int:
    """doctor [--project-id PROJECT_ID] [--rebuild [--write]] [--liveness]"""
    from . import config, doctor, storage

    registry = config.load_registry()

    project_id = getattr(args, "project_id", None)
    if project_id:
        if project_id not in registry:
            raise ValueError(f"unknown registered project id: {project_id}")
        projects = [project_id]
    else:
        projects = list(registry.keys())

    # CR-16 (RISK-007): the fast liveness-only path -- deadman, TICK_ERROR
    # streak, and notify-transport reachability ONLY. Deliberately skips the
    # other 11 checks, the host-scoped docker/cgroup checks, and rebuild:
    # this is the path a container healthcheck runs on a tight interval
    # (nyxloomd/docker-compose.yml), so it pays for exactly what it needs.
    # It is still a plain CLI invocation -- no Daemon constructed, no HTTP
    # server started -- which is what makes its own non-zero exit code an
    # alarm path independent of the daemon being alive at all.
    if getattr(args, "liveness", False):
        live_findings = []
        # One shared transport-probe cache for the whole invocation: every
        # registered project resolves the same NYXLOOM_NTFY_URL by default, so
        # without it an N-project sweep makes N identical outbound requests and
        # can serialise N x the probe timeout into the healthcheck's own
        # wall-clock budget. See doctor._transport_finding.
        probe_cache: dict = {}
        for pid in projects:
            cfg = config.ProjectConfig.load(registry[pid])
            live_findings.extend(doctor.liveness_findings(cfg, probe_cache=probe_cache))
        rows = [{
            "kind": f.kind, "severity": f.severity, "message": f.message,
            "project": f.project or "", "refs": ", ".join(f.refs) if f.refs else "",
        } for f in live_findings]
        if rows:
            print(_format_table(rows, ["kind", "severity", "message", "project", "refs"]))
        return 1 if any(f.severity in ("critical", "error") for f in live_findings) else 0

    all_findings = []
    http_entries = []
    for pid in projects:
        root = registry[pid]
        cfg = config.ProjectConfig.load(root)
        http_entries.append((cfg.policy.http_port, cfg.policy.http_bind))
        findings = doctor.doctor_project(cfg)
        all_findings.extend(findings)

    # Host-scoped checks (docker-transport lying, missing cgroup slices) --
    # not owned by any single project, so run once regardless of --project-id
    # and folded into the SAME findings table + exit-code decision below.
    all_findings.extend(doctor.doctor_host())

    # If rebuild mode, show diffs
    if getattr(args, "rebuild", False):
        for pid in projects:
            replayed, diffs = doctor.rebuild(pid, write=False)
            if diffs:
                print(f"Project {pid} diffs:")
                for diff in diffs[:50]:  # Cap at 50 diffs per oracle
                    print(f"  {diff}")

        if getattr(args, "write", False):
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
    """status [--project-id PROJECT_ID]"""
    from . import config, storage

    registry = config.load_registry()

    projects = []
    if args.project_id:
        if args.project_id not in registry:
            raise ValueError(f"unknown registered project id: {args.project_id}")
        projects = [args.project_id]
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

    cfg = _cfg(args.project_id)
    states = storage.list_states(args.project_id)
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
        args.project_id, states, plan, allow_content_merge=allow_content_merge,
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


def _resolve_since_marker(
    args, detected_format: str | None = None
) -> tuple[str | None, int | None]:
    """Resolve --since/--since-file (shared by `extract` and
    `extract-lossless`) into an opaque marker. Returns (marker, None) on
    success, or (None, exit_code) after printing an error -- --since-file's
    embedded (format, marker) is cross-checked against --format/the
    auto-detected one, since a marker minted by one adapter is meaningless
    fed into another. ``detected_format`` is supplied by the caller after it
    has resolved the session path; keeping that fact explicit prevents the
    auto-detected branch from accidentally checking only explicit formats.
    """
    from pathlib import Path

    from .session_extract import read_since_marker

    since_marker = args.since
    if args.since_file:
        since_format, since_marker = read_since_marker(Path(args.since_file))
        expected_format = args.format or detected_format
        if expected_format and expected_format != since_format:
            if args.format:
                detail = f"--format={args.format!r} was requested"
            else:
                detail = f"the session log was auto-detected as {expected_format!r}"
            print(f"error: --since-file was produced by the {since_format!r} adapter, "
                  f"but {detail}", file=sys.stderr)
            return None, 1
    return since_marker, None


def _resolve_session_log(args) -> tuple[Path, str | None] | None:
    """The shared SESSION_LOG positional resolution every extract-* verb
    runs first: a path is taken as-is, a bare session id is located on disk
    (session_extract/locate.py). Returns (path, session_id) -- session_id
    being the opencode session the ref itself named, else whatever
    --opencode-session passed -- or None after printing the error, which the
    caller turns into exit 1.
    """
    from .session_extract.locate import LocateError, resolve_session_ref

    try:
        ref = resolve_session_ref(args.path, Path.cwd())
    except LocateError as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    return ref.path, ref.session_id or getattr(args, "opencode_session", None)


def _block_render_for(args):
    """Build the prose renderer from independent render and color settings.

    Markdown source is highlighted for terminal output by default; pipes stay
    plain unless --color is explicit. --render-markdown consumes markdown
    syntax while honoring the same color decision.
    """
    use_color = _color_enabled(args)
    if getattr(args, "render_markdown", False):
        from .session_extract.render_markdown import render_markdown

        return lambda text: render_markdown(text, color=use_color)
    if getattr(args, "highlight", False) or use_color:
        from .session_extract.highlight import highlight_markdown

        return lambda text: highlight_markdown(text, color=use_color)
    return None


def _color_enabled(args) -> bool:
    explicit = getattr(args, "color", None)
    if explicit is not None:
        return explicit
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _source_metadata(path: Path, fmt: str | None = None) -> dict[str, str]:
    source_path = path
    if fmt == "opencode" and path.is_dir():
        from .session_extract.adapters import opencode

        database = opencode._db_path(path)
        if database is not None:
            source_path = database
    stat = source_path.stat()
    birth = getattr(stat, "st_birthtime", None)
    created = (
        datetime.fromtimestamp(birth, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if birth is not None else "unavailable"
    )
    return {
        "name": source_path.name,
        "path": str(source_path.absolute()),
        "bytes": str(stat.st_size),
        "created": created,
    }


def _extract_config_from_args(args, *, since_marker=None, json_output: bool | None = None):
    from .session_extract import ExtractConfig
    from .session_extract.config import DEFAULT_PROFILE, PROFILES

    profile_name = getattr(args, "profile", None) or DEFAULT_PROFILE
    base = PROFILES[profile_name]
    get = lambda name, default=None: getattr(args, name, default)
    max_checkpoints = get("max_checkpoints")
    if max_checkpoints is None:
        max_checkpoints = get("checkpoints")
    answer_length = get("answer_length")
    if answer_length is None:
        answer_length = get("long_threshold")
    max_compactions = get("max_compactions")
    if max_compactions is None:
        max_compactions = get("max_lifecycle_markers")
    blank_lines = get("blank_lines")
    if blank_lines is None:
        blank_lines = get("insert_blank_lines")
    as_json = get("json", False) if json_output is None else json_output
    return ExtractConfig(
        max_checkpoints=max_checkpoints if max_checkpoints is not None else base.max_checkpoints,
        long_comment_chars=answer_length if answer_length is not None else base.long_comment_chars,
        max_words=get("max_words") if get("max_words") is not None else base.max_words,
        include_thinking=get("include_thinking", False),
        since_marker=since_marker,
        until_marker=get("until"),
        max_compactions=max_compactions if max_compactions is not None else base.max_compactions,
        max_time_minutes=(
            get("max_time_minutes") if get("max_time_minutes") is not None else base.max_time_minutes
        ),
        epochs=get("epochs") if get("epochs") is not None else base.epochs,
        output_format="json" if as_json else "text",
        hide_api_errors=not get("show_api_errors", False),
        min_gap_to_annotate=(
            get("min_gap_records") if get("min_gap_records") is not None else base.min_gap_to_annotate
        ),
        gap_note_show_marker=get("show_gap_source", False),
        insert_blank_lines=blank_lines if blank_lines is not None else base.insert_blank_lines,
        gap_marker_mode=get("gap_marker") if get("gap_marker") is not None else base.gap_marker_mode,
        strip_stale_wakeups=get("strip_stale_wakeups", False),
        redact_patterns=tuple(get("redact_pattern") or ()),
        hide_compaction_content=not get("show_compaction_content", False),
        show_tool_calls=get("show_tool_calls", False),
        show_tool_call_intent=get("show_tool_call_intent", False),
        tool_calls=get("tool_calls") or "none",
        tool_errors=get("tool_errors") or "show",
        strip_cd_prefix=bool(get("strip_cd_prefix", False)),
        edit_calls=get("edit_calls") or "show",
        read_calls=get("read_calls") or "show",
        effect_calls=get("effect_calls") or "mode",
        timestamps=get("timestamps") or "all",
        timestamp_gap_minutes=(
            get("timestamp_gap_minutes") if get("timestamp_gap_minutes") is not None else 5
        ),
        show_timestamps=get("show_timestamps") or "pre",
        timestamp_format=get("timestamp_format") or "[%H:%M:%S]",
        extract_metadata=get("extract_metadata") or "both",
    )


#: --follow-only flags, by the attribute argparse stores them under, with the
#: value that means "not passed". Each errors without --follow rather than
#: silently doing nothing -- the trap this package keeps choosing to error on.
_FOLLOW_ONLY_FLAGS = (
    ("interval", None, "--interval"),
    ("bell", False, "--bell"),
    ("on_attention", None, "--on-attention"),
    ("notify_project", None, "--notify-project"),
    ("attention_min_chars", None, "--attention-min-chars"),
)


def _validate_render_and_follow_flags(args) -> str | None:
    """Every cross-flag rule shared by extract and extract-lossless; returns
    the error message, or None when the combination is coherent."""
    if getattr(args, "render_markdown", False) and getattr(args, "highlight", False):
        return ("--render-markdown and --highlight are opposite goals (render markdown vs. "
                "colorize it while keeping every markup character) -- pick one")
    if getattr(args, "show_tool_call_intent", False) and not getattr(args, "show_tool_calls", False):
        return "--show-tool-call-intent requires --show-tool-calls"

    if not getattr(args, "follow", False):
        for attr, unset, flag in _FOLLOW_ONLY_FLAGS:
            if getattr(args, attr, unset) != unset:
                return f"{flag} only has an effect with --follow"
        return None

    if args.interval is not None and args.interval <= 0:
        # A zero/negative interval is a busy loop, which on a shared host is a
        # real cost, not just a pointless setting.
        return "--interval must be greater than 0 (a zero interval is a busy loop)"
    if getattr(args, "json", False):
        return "--follow streams text as the session grows -- it has no JSON document to emit"
    if args.until:
        return ("--until pins the far end of a FIXED span; --follow has no end to pin -- "
                "they are contradictory")
    if getattr(args, "epochs", None) is not None:
        return "--epochs selects a fixed session span and cannot be combined with --follow"
    if getattr(args, "max_time_minutes", None) not in (None, -1):
        return "--max-time-minutes is a fixed-span stop condition and cannot be combined with --follow"
    if getattr(args, "extract_metadata", None) == "pre":
        return ("--extract-metadata pre cannot keep the cursor current in a growing --follow stream; "
                "use post or both")
    if getattr(args, "task", None) is not None or getattr(args, "task_file", None) is not None:
        return ("--task/--task-file append a banner AFTER the finished brief, which --follow "
                "never reaches -- they are contradictory")
    return None


def _resolve_effect_patterns(args) -> tuple[tuple[str, ...], bool] | None:
    """(effect patterns, built-in scp-upload rule on) from `--effect-pattern` /
    `--no-default-effect-patterns`; None (after printing the error) on an
    invalid regex."""
    import re as re_mod

    from .session_extract.shellcmd import DEFAULT_EFFECT_PATTERNS

    user = tuple(getattr(args, "effect_pattern", None) or ())
    for pattern in user:
        try:
            re_mod.compile(pattern)
        except re_mod.error as e:
            print(f"error: --effect-pattern {pattern!r}: invalid regex: {e}", file=sys.stderr)
            return None
    defaults = not getattr(args, "no_default_effect_patterns", False)
    return ((DEFAULT_EFFECT_PATTERNS if defaults else ()) + user), defaults


def _follow_anchor(path: Path, fmt: str, session_id: str | None):
    """Where phase 2 picks up, captured BEFORE phase 1 parses anything: the
    start of the file's current final (possibly partial) line, or opencode's
    newest (time_created, id) row plus a bounded recent-row part-fingerprint
    view.

    Deliberately measured BEFORE rather than after. A record appended while
    phase 1 is parsing then appears twice (once in the one-shot brief, once
    live); measured after, such a record would be skipped by BOTH and lost
    from the stream for good. A duplicate is visible and harmless; silent
    loss is neither. The window is one parse long either way. For JSONL,
    backing up to the current line's start closes the race where the raw
    file-size anchor lands in the middle of a record and the remaining suffix
    cannot be parsed.
    """
    if fmt != "opencode":
        try:
            size = path.stat().st_size
            if size == 0:
                return 0
            with path.open("rb") as handle:
                position = size
                while position:
                    start = max(0, position - 8192)
                    handle.seek(start)
                    chunk = handle.read(position - start)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        return start + newline + 1
                    position = start
            return 0
        except OSError:
            return 0

    import sqlite3

    from .session_extract.adapters import opencode as opencode_adapter

    db = opencode_adapter._db_path(path)
    if db is None:
        return None
    from .session_extract.follow import OPENCODE_TRACKED_ROWS, OpencodeAnchor

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT time_created, id, data FROM message WHERE session_id = ? "
            "ORDER BY time_created DESC, id DESC LIMIT ?",
            (session_id, OPENCODE_TRACKED_ROWS),
        ).fetchall()
        if not rows:
            return OpencodeAnchor((-1, ""))
        tracked = []
        for row in reversed(rows):
            parts = conn.execute(
                "SELECT id, time_updated, data FROM part "
                "WHERE message_id = ? ORDER BY id ASC",
                (row[1],),
            ).fetchall()
            fingerprint = (row[2], tuple(parts))
            tracked.append(((row[0], row[1]), fingerprint))
        newest = rows[0]
        return OpencodeAnchor(
            (newest[0], newest[1]),
            tracked[-1][1],
            tuple(tracked),
        )
    finally:
        conn.close()


def _run_follow(args, path: Path, fmt: str, config, session_id: str | None, anchor,
                 block_render, lossless_mode: bool, source_metadata=None, watch=None) -> int:
    """Phase 2: hand off to session_extract/follow.py and tail until Ctrl-C."""
    from .session_extract import follow as follow_mod
    from .session_extract.adapters import claude_code as claude_code_adapter

    notify_config = None
    if args.notify_project:
        try:
            notify_config = _cfg(args.notify_project).notify
        except RuntimeError as e:
            print(f"error: --notify-project: {e}", file=sys.stderr)
            return 1

    follow_config = follow_mod.FollowConfig(
        interval=args.interval if args.interval is not None else follow_mod.DEFAULT_INTERVAL_S,
        bell=args.bell,
        on_attention=args.on_attention,
        notify=notify_config,
        attention_min_chars=args.attention_min_chars,
    )

    if fmt == "opencode":
        from .session_extract.adapters import opencode as opencode_adapter

        db = opencode_adapter._db_path(path)
        if isinstance(anchor, follow_mod.OpencodeAnchor):
            cursor = anchor.cursor
            anchor_fingerprint = anchor.fingerprint
            tracked_fingerprints = anchor.tracked
        else:
            cursor = anchor
            anchor_fingerprint = None
            tracked_fingerprints = ()
        source = follow_mod.OpencodeSource(
            db, session_id, config, lossless_mode, cursor=cursor,
            anchor_fingerprint=anchor_fingerprint,
            tracked_fingerprints=tracked_fingerprints,
        )
    else:
        source = follow_mod.JsonlSource(
            path, fmt, anchor, config, lossless_mode,
            has_primary_thread=(
                claude_code_adapter.has_primary_thread(path) if fmt == "claude-code" else True
            ),
        )

    follower = follow_mod.Follower(
        source, harness=fmt, session_path=str(path), config=config,
        follow_config=follow_config, out=sys.stdout, lossless_mode=lossless_mode,
        block_render=block_render,
        insert_blank_lines=config.insert_blank_lines,
        source_metadata=source_metadata, watch=watch,
    )
    return follower.run_forever()


# The four `--preset` bundles (watch, successor, review, ledger) live in
# session_extract/presets.py; `--successor-brief` implies `successor`.


def cmd_extract(args) -> int:
    """extract <session-ref> [--format FMT] [--json] [--profile NAME]
    [--max-checkpoints N] [--answer-length N] [--max-words N]
    [--max-compactions N] [--max-time-minutes N] [--epochs N|A:B|all]
    [--since MARKER | --since-file PATH] [--until MARKER] [--ledger]
    [--tool-calls none|intent|intent-or-call|call] [--tool-errors show|hide]
    [--stop-state | --no-stop-state] [--no-ledger] [--prose-only | --no-prose] [--jsonl]
    [--preset watch|successor|review|ledger]
    [--successor-brief [--order TEXT|@FILE] [--brief-max-chars N]]
    [--strip-cd-prefix | --no-strip-cd-prefix] [--path-aliases SPEC]
    [--edit-calls show|collapse|omit] [--read-calls show|collapse]
    [--effect-calls always|mode] [--timestamps all|gaps|none]
    [--timestamp-gap-minutes N]
    [--effect-pattern REGEX] [--no-default-effect-patterns]
    [--show-tool-calls] [--show-tool-call-intent] [--gap-marker MODE]
    [--blank-lines N] [--show-timestamps MODE] [--timestamp-format FMT]
    [--extract-metadata MODE] [--render-markdown | --highlight] [--color | --no-color]
    [--strip-stale-wakeups] [--redact-pattern REGEX] [--task TEXT | --task-file PATH]

    Mechanical (no LLM roundtrip) session-log extraction -- see
    session_extract/__init__.py's module docstring for the full contract.
    Auto-detects the source CLI's format from the path (a Claude Code, Codex,
    or Reasonix JSONL file, or an opencode SQLite store); --format overrides
    when detection is ambiguous or wrong. Prints the resumable brief to
    stdout: delimited text by default, or --json for a second-stage tool.
    For a raw, unclassified verbatim dump instead of this command's
    classification/windowing, see `extract-lossless` -- a separate verb
    (split off 2026-09-11, operator direction) because none of this
    command's selection-aggressiveness flags below ever applied there.

    --profile selects an extraction use case. operator-review is the default
    concise review of recent work; all removes word/checkpoint/time/compaction
    stops and keeps ordinary prompt, Q&A, and assistant prose across all
    epochs. API errors, thinking, tool calls, and compaction internals remain
    controlled by separate options. Explicit values override
    profile values. Checkpoints are classifier-scored assistant messages, not
    semantic boundaries. /clear starts a new epoch; outside the all profile,
    only the newest epoch is selected. Use --epochs N, A:B, or all to choose.

    Targeting a specific agent's own conversation (e.g. a dispatched Claude
    Code Agent-tool subagent, or an opencode session forked from a parent)
    needs no special flag -- point path (and --opencode-session, for a
    store that holds more than one) directly at THAT agent's own
    file/session. Each
    adapter handles its own format's targeting quirks internally (e.g.
    claude_code.py auto-detects a subagent's dedicated transcript file and
    treats its content as primary rather than filtering it out as noise --
    see that adapter's module docstring); this command's own surface never
    needs adapter-specific vocabulary for it.

    --since-file reads the last nyxloom marker from a PRIOR run's saved
    output (text or JSON) and uses it as the lower cursor. Save the first
    snapshot with `nyxloom extract log.jsonl > snapshot.md`; after the log
    grows, run `nyxloom extract log.jsonl --since-file snapshot.md > delta.md`.
    This emits only events after the marker; it does not merge the old
    snapshot into delta.md. Keep both files, or concatenate them yourself
    when a cumulative transcript is wanted. The cursor is the source end
    marker, even when some source records were intentionally filtered. The
    source format is checked because markers are adapter-specific.

    --until bounds the walk's far (older) end at a given marker (inclusive)
    -- symmetric with --since, mainly for pinning a run to a fixed
    historical span (e.g. reproducing a run against a fixed hand-curated
    reference) rather than everyday resumption.

    --ledger appends a mechanically-extracted `[files read: ...] [files
    edited: ...] [commits created: ...] [branches involved: ...] [tests:
    ...]` line after each kept boundary's own text (E-012, session_extract/
    ledger.py) -- zero LLM calls, same guarantee as the rest of this
    package. Claude Code only today (errors on any other format); --json
    errors too (no JSON equivalent yet). It also appends a WHOLE-SESSION
    ledger (files, commits, branches, tests and an `external effects` bucket
    of Bash commands: git push/merge/tag/rebase, ssh, mutating curl, netcup
    snapshot/install verbs, docker rm/stop/run, systemctl, apt -- extend or
    replace with --effect-pattern / --no-default-effect-patterns) before the
    closing cursor comment, so a single-brief agent is no longer empty.

    --tool-calls MODE (Claude Code) renders tool calls: `none` (default),
    `intent` (the call's own description/intent field only), `intent-or-call`
    (intent, else the one-line truncated call), `call` (the one-line
    truncated call); results are never shown. --show-tool-calls and
    --show-tool-call-intent are DEPRECATED aliases that keep their exact
    prior output (name labels, optionally with intent) and cannot be mixed
    with --tool-calls. --tool-errors show|hide (default show) renders FAILED
    tool results truncated, independent of --tool-calls; the harness's
    synthetic stop/denial records render as `[STOP: ...]`, never as OPERATOR.

    --stop-state appends the cause (the sibling .meta.json `stoppedByUser`
    plus the transcript tail), the last assistant text and the in-flight call.
    Every option belongs to one help group: Source & range, Content
    selection, Rendering & compression, Derived sections, Output.
    --preset NAME is one of four named bundles of options (watch, successor,
    review, ledger; session_extract/presets.py, every expansion shown in
    --help); explicit options override it (--no-ledger, --no-stop-state,
    --no-strip-cd-prefix and --prose-only/--no-prose cancel boolean
    members). --prose-only keeps only operator messages and assistant prose
    (timestamped, coloured per --color/--no-color; --jsonl emits
    {v, ts, role, text, agent?} lines, v=1; works with --follow). --no-prose drops
    all events and keeps only the derived sections. --successor-brief emits ONE markdown document for priming a
    fresh agent (original brief verbatim or path+sha256, the extract with the
    successor preset applied, whole-session ledger, stop state, then --order
    TEXT|@FILE) and implies --preset successor.

    --blank-lines/--gap-marker/--min-gap-records/--show-gap-source control
    ONLY the text-mode rendering of
    the "---" block separator and the gap_after annotation (select.py's own
    "N raw records were dropped here" fact) -- all four error combined with
    --json rather than silently having no effect, since JSON output already
    reports the true gap count as a typed field regardless. Defaults use
    inline gaps and zero blank lines; see each flag's own --help text for the
    full value space (config.py's
    insert_blank_lines/gap_marker_mode comments have the complete picture).

    --render-markdown (2026-09-12) is for READING the brief rather than
    piping it: each kept block's prose goes through `rich`'s markdown
    renderer, so headers/bold/code fences render the way the CLI that wrote
    them showed you live. Rendering CONSUMES the markup characters, which is
    exactly wrong when the point is to copy real markdown back out of the
    terminal -- `--highlight` covers that case instead (pygments, every
    character left in place). The two are mutually exclusive. Only kept
    blocks' own prose is affected: the `---` separators, the bracketed gap/
    stop-reason notes, the ledger line and positioned `<!-- nyxloom-extract:
    ... -->` cursor comments are never passed through a renderer (they are
    machine-read by --since-file and must stay byte-exact).
    --color/--no-color controls ANSI color independently from rendering. On
    a terminal, --highlight colors markdown source by default; piped output is
    plain unless --color is explicit. --render-markdown consumes markup;
    --highlight preserves every markup character while adding color.

    --strip-stale-wakeups/--redact-pattern/--task/--task-file (2026-09-11,
    operator direction, "handoff to a fresh agent" -- see
    session_extract/mangle.py) exist for `nyxloom extract ... | claude`:
    handing the rendered brief straight to a new agent as its whole prompt.
    The first two are heuristic, opt-in event-level transforms (work in
    both --json and text mode); --task/--task-file append a clearly
    delimited new-instruction banner AFTER the rendered brief and are
    text-mode only (error combined with --json). See each flag's own
    --help text.
    """
    from pathlib import Path

    from .session_extract import extract
    from .session_extract.events import EventKind

    resolved = _resolve_session_log(args)
    if resolved is None:
        return 1
    path, session_id = resolved

    from .session_extract import presets as presets_mod

    successor = bool(getattr(args, "successor_brief", False))
    preset = presets_mod.preset_name(args)
    # A preset (watch/successor/review/ledger; --successor-brief implies
    # successor) only turns options on; an explicit option always wins
    # (presets.resolve). The expansions are shown in --help and the docs.
    for attr, value in presets_mod.resolve(args).items():
        setattr(args, attr, value)
    if getattr(args, "no_ledger", False):
        args.ledger = False
    if getattr(args, "no_stop_state", False):
        args.stop_state = False
    prose_only = bool(getattr(args, "prose_only", False))
    no_prose = bool(getattr(args, "no_prose", False))
    if getattr(args, "show_tool_calls", False) or getattr(args, "show_tool_call_intent", False):
        print("nyxloom extract: --show-tool-calls/--show-tool-call-intent are deprecated aliases "
              "(label / label+intent rendering); use --tool-calls "
              "none|intent|intent-or-call|call", file=sys.stderr)

    detected_format = None
    if args.format is None and (args.since_file or args.follow):
        from .session_extract.adapters import detect

        detected_format = detect(path).name

    since_marker, err = _resolve_since_marker(args, detected_format)
    if err is not None:
        return err

    if args.redact_pattern:
        import re as re_mod
        for pattern in args.redact_pattern:
            try:
                re_mod.compile(pattern)
            except re_mod.error as e:
                print(f"error: --redact-pattern {pattern!r}: invalid regex: {e}", file=sys.stderr)
                return 1

    config = _extract_config_from_args(args, since_marker=since_marker)
    resolved = _resolve_effect_patterns(args)
    if resolved is None:
        return 1
    effect_patterns, scp_uploads = resolved
    try:
        from dataclasses import replace as dc_replace

        from .session_extract.compress import resolve_aliases

        config = dc_replace(
            config, effect_patterns=effect_patterns, effect_scp_uploads=scp_uploads,
            path_aliases=resolve_aliases(getattr(args, "path_aliases", None), path),
        )
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    follow_fmt = None
    anchor = None
    if args.follow:
        follow_fmt = args.format or detected_format
        follow_session = session_id
        if follow_fmt == "opencode" and follow_session is None:
            from .session_extract.adapters import opencode as opencode_adapter

            sessions = opencode_adapter.list_sessions(path)
            if len(sessions) == 1:
                follow_session = sessions[0]
        # Before phase 1 parses anything -- see _follow_anchor on why.
        anchor = _follow_anchor(path, follow_fmt, follow_session)

    result = extract(path, config, fmt=args.format, session_id=session_id)
    if config.show_tool_calls and result.format not in ("claude-code", "codex"):
        print(f"error: --show-tool-calls is not supported for {result.format!r}; supported formats: claude-code, codex",
              file=sys.stderr)
        return 1

    for flag, wanted in ((f"--preset {preset}", preset is not None
                          and presets_mod.PRESETS[preset].claude_only and not successor),
                         ("--successor-brief", successor),
                         ("--stop-state", getattr(args, "stop_state", False))):
        if wanted and result.format != "claude-code":
            print(f"error: {flag} does not support {result.format!r} yet -- Claude Code "
                  f"transcripts only (see session_extract/stopstate.py)", file=sys.stderr)
            return 1
    if config.tool_calls != "none" and result.format != "claude-code":
        print(f"error: --tool-calls is not supported for {result.format!r} yet; supported "
              f"format: claude-code", file=sys.stderr)
        return 1
    if result.format != "claude-code":
        for attr, flag in (("tool_errors", "--tool-errors"), ("path_aliases", "--path-aliases"),
                           ("edit_calls", "--edit-calls"), ("read_calls", "--read-calls"),
                           ("effect_calls", "--effect-calls")):
            if getattr(args, attr, None) is not None:
                print(f"error: {flag} is not supported for {result.format!r} yet; supported "
                      f"format: claude-code", file=sys.stderr)
                return 1
        if getattr(args, "strip_cd_prefix", False):
            print(f"error: --strip-cd-prefix is not supported for {result.format!r} yet; "
                  f"supported format: claude-code", file=sys.stderr)
            return 1

    trailer_blocks: list[str] = []
    session_ledger = None
    if args.ledger:
        if result.format != "claude-code":
            print(f"error: --ledger does not support {result.format!r} yet -- see "
                  f"session_extract/ledger.py's module docstring", file=sys.stderr)
            return 1
        from .session_extract import ledger as ledger_mod

        # An interrupt STOP marker is a LIFECYCLE_MARKER but not a boundary.
        boundary_markers = {
            ev.marker for ev in result.events
            if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR, EventKind.LIFECYCLE_MARKER)
            and ev.meta.get("boundary_type") != "interrupt"
        }
        ledgers = ledger_mod.build_ledger(
            path, result.format, boundary_markers, effect_patterns=effect_patterns,
            scp_uploads=scp_uploads, aliases=config.path_aliases,
        )
        session_ledger = ledger_mod.session_ledger(ledgers)
        if not successor:
            result._ledger = ledgers
            trailer_blocks.append(session_ledger.render_session())

    stop_state = None
    if getattr(args, "stop_state", False) or successor:
        from .session_extract.stopstate import build_stop_state

        stop_state = build_stop_state(path)
        if not successor:
            trailer_blocks.append(stop_state.render())

    brief = None
    if successor:
        from .session_extract.successor import first_user_record

        brief = first_user_record(path)
        if brief is None:
            print(f"error: --successor-brief: {path} has no user record to take the original "
                  f"brief from", file=sys.stderr)
            return 1
        # The brief is its own section; drop its event from the extract.
        brief_uuid = brief[0]
        result.events = [
            ev for ev in result.events
            if not (ev.kind is EventKind.OPERATOR_TEXT and brief_uuid and ev.marker == brief_uuid)
        ]
    result._trailer_blocks = trailer_blocks

    block_render = _block_render_for(args)
    result._block_render = block_render
    result._source_metadata = _source_metadata(path, result.format)

    if result.stale_wakeups_stripped:
        print(f"nyxloom extract: stripped {result.stale_wakeups_stripped} stale-wakeup "
              f"checkpoint(s) from the tail (--strip-stale-wakeups)", file=sys.stderr)
    if result.redacted_paragraphs:
        print(f"nyxloom extract: redacted {result.redacted_paragraphs} paragraph(s) matching "
              f"--redact-pattern", file=sys.stderr)

    watch = None
    if prose_only:
        from .session_extract import watch as watch_mod

        jsonl = bool(getattr(args, "jsonl", False))
        explicit_render = bool(getattr(args, "render_markdown", False) or getattr(args, "highlight", False))
        watch = watch_mod.WatchFormatter(
            jsonl=jsonl, color=_color_enabled(args) and not jsonl, agent=watch_mod.agent_id(path),
            timestamps=config.timestamps, show_timestamps=config.show_timestamps,
            timestamp_format=config.timestamp_format, gap_minutes=config.timestamp_gap_minutes,
            block_render=block_render if explicit_render else None,
        )
        rendered = watch.format_all(result.events)
    else:
        if no_prose:
            # --no-prose: only the derived sections (ledger, stop state); the
            # ledger and stop state were already built from the full parse.
            result.events = []
        rendered = result.render()
    harness_warning = None
    if result.format == "claude-code":
        from .session_extract.harness import version_warning

        harness_warning = version_warning(path)
        if harness_warning and (args.json or args.follow or prose_only):
            print(f"nyxloom extract: {harness_warning}", file=sys.stderr)
        elif harness_warning and not successor:
            rendered = harness_warning + "\n" + rendered
    if successor:
        from .session_extract import successor as successor_mod

        order = None
        if getattr(args, "order", None) is not None:
            try:
                order = successor_mod.read_order(args.order)
            except OSError as e:
                print(f"error: --order: {e}", file=sys.stderr)
                return 1
        max_chars = getattr(args, "brief_max_chars", None)
        rendered = successor_mod.assemble(
            path, brief[1], rendered, session_ledger, stop_state, order,
            brief_max_chars=max_chars if max_chars is not None else successor_mod.DEFAULT_BRIEF_MAX_CHARS,
            brief_line=brief[2], harness_warning=harness_warning,
        )
    task_text = None
    if args.task is not None:
        task_text = args.task
    elif args.task_file is not None:
        task_text = Path(args.task_file).read_text(encoding="utf-8")
    if task_text is not None:
        rendered += (
            "\n════ TASK FOR THIS SESSION (supplied by the requester of this extract -- the "
            "controller or the operator -- not part of the session above) ════\n"
            f"{task_text.strip()}\n"
            "════════════════════════════════════════════════════════════════════════════════"
            "════\n"
        )
    # end="" only when following: the brief already ends in a newline, and
    # print's own would put three blank lines between it and the first live
    # block. Left exactly as it was for every non-follow run.
    print(rendered, end="" if args.follow or prose_only else "\n")
    if args.follow:
        return _run_follow(
            args, path, follow_fmt, config, result.session_id, anchor, block_render,
            lossless_mode=False, source_metadata=result._source_metadata, watch=watch,
        )
    return 0


def cmd_extract_lossless(args) -> int:
    """extract-lossless <path> [--opencode-session ID] [--format FMT]
    [--since MARKER | --since-file PATH] [--until MARKER]

    A "dumb", independent lossless-prose dump -- deliberately NOT `extract`'s
    classification/windowing path. Keeps every text/thinking content block
    verbatim, dropping only tool_use/tool_result blocks and harness
    bookkeeping records -- see session_extract/lossless.py's module
    docstring for the two use cases this exists for (a ground-truth
    superset for judging what `extract` selects, and raw material for a
    future hybrid condensation design). Supports Claude Code, Codex, Reasonix,
    and opencode (an opencode store holding more than one session needs
    --opencode-session). --since/--since-file/--until work exactly as they do for
    `extract` (same opaque per-adapter marker, same cross-check against
    --format).

    Was `extract --lossless` until 2026-09-11 (operator direction): split
    into its own verb because NONE of extract's selection-aggressiveness
    flags (--profile/--max-checkpoints/--answer-length/--max-words/
    --include-thinking/--max-compactions/--json/--ledger/
    --show-api-errors) ever applied in lossless mode -- sharing one verb
    made every one of them a silently-ignored trap. See `extract-debug` to
    compare this dump against what `extract` would actually keep from it.
    """
    from .session_extract import lossless
    from .session_extract.adapters import detect

    if getattr(args, "redact_pattern", None):
        print("error: extract-lossless is a verbatim dump and does not support "
              "--redact-pattern; use extract for redacted output", file=sys.stderr)
        return 1

    resolved = _resolve_session_log(args)
    if resolved is None:
        return 1
    path, session_id = resolved

    fmt = args.format or detect(path).name
    since_marker, err = _resolve_since_marker(args, fmt)
    if err is not None:
        return err
    block_render = _block_render_for(args)

    def _emit(text: str) -> None:
        # The lossless dumpers render each prose block themselves, leaving
        # the machine-readable marker footer byte-exact for --since-file.
        print(text)

    anchor = None
    follow_session = session_id
    if args.follow:
        if fmt == "opencode" and follow_session is None:
            from .session_extract.adapters import opencode as opencode_adapter

            sessions = opencode_adapter.list_sessions(path)
            if len(sessions) == 1:
                follow_session = sessions[0]
        # Before phase 1 reads anything -- see _follow_anchor on why.
        anchor = _follow_anchor(path, fmt, follow_session)

    if fmt == "claude-code":
        _emit(lossless.dump_claude_code(
            path, since_marker=since_marker, until_marker=args.until,
            block_render=block_render,
        ))
    elif fmt == "codex":
        _emit(lossless.dump_codex(
            path, since_marker=since_marker, until_marker=args.until,
            block_render=block_render,
        ))
    elif fmt == "reasonix":
        _emit(lossless.dump_reasonix(
            path, since_marker=since_marker, until_marker=args.until,
            block_render=block_render,
        ))
    elif fmt == "opencode":
        from .session_extract.adapters import opencode as opencode_adapter

        resolved_session = session_id
        if resolved_session is None:
            sessions = opencode_adapter.list_sessions(path)
            if len(sessions) == 1:
                resolved_session = sessions[0]
            elif not sessions:
                raise ValueError(f"{path}: no opencode sessions found")
            else:
                raise ValueError(
                    f"{path} holds {len(sessions)} opencode sessions; pass "
                    f"--opencode-session (e.g. {sessions[0]!r})"
                )
        _emit(lossless.dump_opencode(
            path, resolved_session, since_marker=since_marker, until_marker=args.until,
            block_render=block_render,
        ))
        follow_session = resolved_session
    else:
        print(f"error: extract-lossless does not support {fmt!r} -- see "
              f"session_extract/lossless.py's module docstring for what's implemented",
              file=sys.stderr)
        return 1
    if args.follow:
        from .session_extract import ExtractConfig

        return _run_follow(
            args, path, fmt, ExtractConfig(), follow_session, anchor, block_render,
            lossless_mode=True,
        )
    return 0


def cmd_extract_debug(args) -> int:
    """extract-debug mirrors extract's selection and rendering options.

    A colored diff between `extract-lossless`'s full lossless base and what
    `extract` -- called with these SAME flags -- would actually keep: white
    = kept verbatim, grey = dropped (wrapped in a cyan `>>> ... <<<` gap
    note), cyan = a nyxloom-authored note (gap/stop-reason) with no
    lossless counterpart, green = an E-012 ledger line. See
    session_extract/debug_diff.py's module docstring for the full color-
    scheme rationale. Content selection, bounds, timestamps, gap markers,
    metadata, tool-call visibility, and markdown rendering use extract's
    shared configuration. The diff's own annotation colors are controlled
    by --color/--no-color and stdout/NO_COLOR policy.
    """
    from .session_extract import extract, lossless
    from .session_extract.adapters import detect
    from .session_extract.debug_diff import render_debug
    from .session_extract.events import EventKind

    resolved = _resolve_session_log(args)
    if resolved is None:
        return 1
    path, session_id = resolved
    fmt = args.format or detect(path).name

    since_marker, err = _resolve_since_marker(args, fmt)
    if err is not None:
        return err
    config = _extract_config_from_args(args, since_marker=since_marker, json_output=False)
    if config.show_tool_calls and fmt not in ("claude-code", "codex"):
        print(f"error: --show-tool-calls is not supported for {fmt!r}; supported formats: claude-code, codex",
              file=sys.stderr)
        return 1

    if fmt == "claude-code":
        lossless_text = lossless.dump_claude_code(
            path, since_marker=since_marker, until_marker=args.until,
            show_tool_calls=config.show_tool_calls,
            show_tool_call_intent=config.show_tool_call_intent,
        )
    elif fmt == "codex":
        lossless_text = lossless.dump_codex(
            path, since_marker=since_marker, until_marker=args.until,
            show_tool_calls=config.show_tool_calls,
            show_tool_call_intent=config.show_tool_call_intent,
        )
    elif fmt == "reasonix":
        lossless_text = lossless.dump_reasonix(path, since_marker=since_marker, until_marker=args.until)
    elif fmt == "opencode":
        from .session_extract.adapters import opencode as opencode_adapter

        resolved_session = session_id
        if resolved_session is None:
            sessions = opencode_adapter.list_sessions(path)
            if len(sessions) == 1:
                resolved_session = sessions[0]
            elif not sessions:
                print(f"error: {path}: no opencode sessions found", file=sys.stderr)
                return 1
            else:
                print(f"error: {path} holds {len(sessions)} opencode sessions; pass "
                      f"--opencode-session (e.g. {sessions[0]!r})", file=sys.stderr)
                return 1
        lossless_text = lossless.dump_opencode(
            path, resolved_session, since_marker=since_marker, until_marker=args.until,
        )
    else:
        print(f"error: extract-debug does not support {fmt!r} -- see session_extract/lossless.py's "
              f"module docstring for what's implemented", file=sys.stderr)
        return 1

    result = extract(path, config, fmt=fmt, session_id=session_id)
    result._source_metadata = _source_metadata(path, result.format)
    result._block_render = _block_render_for(args)
    if args.ledger:
        if result.format != "claude-code":
            print(f"error: --ledger does not support {result.format!r} yet", file=sys.stderr)
            return 1
        from .session_extract import ledger as ledger_mod

        boundary_markers = {
            ev.marker for ev in result.events
            if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR, EventKind.LIFECYCLE_MARKER)
        }
        result._ledger = ledger_mod.build_ledger(path, result.format, boundary_markers)
    use_color = _color_enabled(args)
    print(render_debug(
        lossless_text, result.render(), use_color, config=config, fmt=fmt,
        all_events=result.all_events, block_render=result._block_render,
    ))
    return 0


def cmd_extract_report(args) -> int:
    """extract-report <path> [--type report-sheet|report-detailed|csv] [--json]

    Cost/timeline analysis on top of session_extract -- V9 in
    nyxloom/docs/design-context-lifecycle-experiments.md. Renamed from
    `session-stats` 2026-09-11 (operator direction -- consistent extract-*
    verb family); behavior is unchanged. Supports Claude Code, Codex, and
    opencode today (see session_extract/stats.py's module docstring for the
    real per-format usage-ledger shape each reads, and its honest gaps).
    --opencode-session disambiguates an opencode store holding more than one
    session (Claude Code/Codex are one-file-one-session, so it's unused there). To
    find out WHICH sessions/sub-agents exist before targeting one, see
    `extract-sessions` -- a different question (discovery vs. reporting on
    a session you've already identified). report-sheet is the condensed
    operator overview. report-detailed shows one human-readable row per API
    call. csv writes the detailed per-call data for spreadsheet/import use.
    --json emits the selected report shape as JSON.
    """
    from .session_extract import stats

    resolved = _resolve_session_log(args)
    if resolved is None:
        return 1
    path, session_id = resolved

    try:
        rows = stats.build_call_rows(path, fmt=args.format, session_id=session_id)
    except (NotImplementedError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    report_type = args.report_type or ("csv" if args.detailed else "report-sheet")
    if report_type == "report-sheet":
        blocks = stats.build_blocks(rows)
        if args.json:
            import dataclasses
            import json as jsonlib

            print(jsonlib.dumps([dataclasses.asdict(b) for b in blocks], indent=2))
        else:
            print(stats.render_condensed(blocks), end="")
        return 0
    if args.json:
        import dataclasses
        import json as jsonlib

        print(jsonlib.dumps([dataclasses.asdict(r) for r in rows], indent=2))
    elif report_type == "csv":
        print(stats.render_detailed_csv(rows), end="")
    else:
        print(stats.render_detailed_text(rows), end="")
    return 0


def cmd_extract_sessions(args) -> int:
    """extract-sessions <claude|codex|opencode|path> [--recurse [true|false]] [--json]

    Discovery, not reporting: lists which sessions/sub-agents EXIST and how
    they relate (parent, depth, a human label), for when you don't yet know
    what to point `extract`/`extract-lossless`/`extract-report` at -- see
    session_extract/sessions.py's module docstring. The gap this closes was
    found while redesigning Claude Code sub-agent targeting (E-015 in
    nyxloom/docs/design-context-lifecycle-experiments.md); it turned out
    identical in shape across all three adapters once checked against real
    data. path may be a top-level session file OR one specific sub-agent's
    own file for Claude Code/Codex (the whole family is shown either way,
    rooted at the top-level session); for opencode it's the whole SQLite
    store (every root session and its descendants -- there's no single-
    file scoping concept there). --json dumps the flat node list instead
    of the indented tree.
    """
    from .session_extract import sessions

    hint = args.path.lower()
    inferred = {"claude": "claude-code", "codex": "codex", "opencode": "opencode"}.get(hint)
    if inferred is not None:
        if args.format is not None and args.format != inferred:
            print(f"error: {args.path!r} selects {inferred!r}, conflicting with --format={args.format!r}",
                  file=sys.stderr)
            return 1
        args.format = inferred
        if inferred == "claude-code":
            root = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).expanduser()
            path = root / "projects"
        elif inferred == "codex":
            root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
            path = root / "sessions"
        else:
            if os.environ.get("OPENCODE_DB"):
                path = Path(os.environ["OPENCODE_DB"]).expanduser()
            else:
                data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))).expanduser()
                path = data_home / "opencode" / "opencode.db"
    else:
        resolved = _resolve_session_log(args)
        if resolved is None:
            return 1
        path, _session_id = resolved

    try:
        nodes = sessions.list_agents(path, fmt=args.format, recurse=args.recurse == "true")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        import dataclasses
        import json as jsonlib

        print(jsonlib.dumps([dataclasses.asdict(n) for n in nodes], indent=2))
    else:
        print(sessions.render_tree(nodes), end="")
    return 0


def cmd_search(args) -> int:
    """Search discovered harness transcripts and print ranked session ids."""
    from .harness_search import search_sessions

    query = " ".join(args.words)
    runtime = getattr(args, "runtime", None)
    fast = getattr(args, "fast", False)
    progress = runtime.progress() if runtime is not None else None
    if progress is None:
        results = search_sessions(
            query,
            client=args.client,
            sort_by=args.sort_by,
            source_roots=args.source_root,
            word_match=args.word_match,
            term_match=args.term_match,
            fast=fast,
        )
    else:
        with progress:
            progress.update("Preparing local session search")
            results = search_sessions(
                query,
                client=args.client,
                sort_by=args.sort_by,
                source_roots=args.source_root,
                word_match=args.word_match,
                term_match=args.term_match,
                fast=fast,
                progress=lambda message, current, total: progress.update(
                    message, current=current, total=total,
                ),
            )
            progress.finish("Session search complete")
    if not results:
        print("No matching sessions.")
        return 0

    rows = [
        {
            "CLIENT": result.client,
            "SESSION ID": result.session_id,
            "SCORE": f"{result.score:.3f}",
            ("FILE MTIME (UTC)" if fast else "LAST ACTIVITY"): result.last_activity or "—",
            "MATCHED WORDS": ", ".join(result.matched_terms),
            "SOURCE": result.source,
        }
        for result in results
    ]
    print(_format_table(
        rows,
        [
            "CLIENT", "SESSION ID", "SCORE",
            "FILE MTIME (UTC)" if fast else "LAST ACTIVITY",
            "MATCHED WORDS", "SOURCE",
        ],
    ))
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
        result = migrate(args.project_id)
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
    """tick [--project-id PROJECT_ID]"""
    from . import daemon as daemon_mod

    action_count = daemon_mod.run_once(args.project_id)
    print(action_count)
    return 0


def cmd_decide(args) -> int:
    """decide <project> <D-id> --choose TEXT [--note TEXT]"""
    from . import config, decisions, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project_id)

    try:
        note = getattr(args, 'note', '') or ''
        decisions.decide(cfg, args.decision_id, args.choose, note,
                        os.environ.get("USER", "operator"))

        # Append DECISION_RESOLVED event
        actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
        storage.append_event(
            args.project_id,
            actor=actor,
            type=EventType.DECISION_RESOLVED,
            decision_id=args.decision_id,
            payload={},
        )
        return 0
    except decisions.DecisionError as e:
        if getattr(args, 'traceback', False):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_discuss(args) -> int:
    """discuss <project> <D-id>"""
    from . import decisions

    cfg = _cfg(args.project_id)

    try:
        cmd_str = decisions.discuss(cfg, args.decision_id)
        print(cmd_str)
        return 0
    except decisions.DecisionError as e:
        if getattr(args, 'traceback', False):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


def cmd_intake(args) -> int:
    """intake <project> <intake_id> <message>"""
    from . import intake_chat

    cfg = _cfg(args.project_id)
    reply = intake_chat.advance_intake(cfg, args.project_id, args.intake_id, args.message)
    print(reply)
    return 0


def cmd_intake_bridge_poll(args) -> int:
    """intake-bridge poll <project> [--transport mmctl|rest]

    B9 2026-09-09 (nyxloom-P109): ONE poll of the Mattermost intake channel,
    then exit. A separate verb GROUP rather than a sub-verb of `intake`
    because `intake <project> <intake_id> <message>` is a frozen positional
    contract (P29) that cannot grow a sub-parser without breaking it; the
    hyphenated-group shape matches `free-models`/`capability-map`/`route`.
    """
    from . import intake_bridge

    cfg = _cfg(args.project_id)
    if args.transport:
        # An explicit override still goes through resolve_reader's
        # is_configured check -- naming a transport whose config is absent
        # must report 'unconfigured', not fall through to the other one.
        from dataclasses import replace as _replace
        cfg = _replace(cfg, intake_bridge=_replace(cfg.intake_bridge,
                                                   transport=args.transport))
    result = intake_bridge.poll_once(cfg, args.project_id)
    print(f"transport={result.transport or '-'} status={result.status} "
          f"fetched={result.fetched} ingested={result.ingested} "
          f"intake={result.intake_id or '-'} reply_posted={result.reply_posted} "
          f"cursor_ms={result.cursor_ms}")
    if result.detail:
        print(result.detail)
    return 1 if result.status == "refused" else 0


def cmd_reject(args) -> int:
    """reject <project> <task> [--note TEXT]"""
    from . import storage
    from .types import (
        Actor, ActorKind, EventType, TaskState, TransitionError,
        check_task_transition,
    )

    _cfg(args.project_id)  # raises if the project isn't registered

    states = storage.list_states(args.project_id)
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
        args.project_id,
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

    cfg = _cfg(args.project_id)

    states = storage.list_states(args.project_id)
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

    # F (factory-hardening): gate the commit BEFORE recording the merge, so a
    # manual merge-record cannot enshrine a tree that fails the project's own
    # gate. The daemon auto-merge path is already gated pre-publish by
    # D-CORRECT-1; this closes the OPERATOR path (canonical reference/LESSONS.md
    # L4). `--force` bypasses it (an explicit operator override); a project that
    # declares no gate is recorded as before. Nothing has been written yet, so a
    # gate failure simply returns 1 without touching state.
    if not getattr(args, "force", False):
        from . import gate_runner
        _gate = gate_runner.select_verification_gate(cfg)
        if _gate is not None:
            _res = gate_runner.run_gate_at_commit(cfg, _gate, commit, phase="pre-merge")
            if _res.exit_code != 0:
                print(f"error: pre-merge gate {_gate.gate_id} failed "
                      f"(exit {_res.exit_code}) for {commit[:12]}; merge NOT recorded. "
                      f"Fix and re-run, or pass --force to override.", file=sys.stderr)
                return 1

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))
    storage.append_and_apply(
        args.project_id,
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
    from . import merge_digest
    try:
        _carver_digest = merge_digest.carver_digest_payload(
            cfg, states, args.task, commit, source_kind="review",
        )
    except Exception:
        log = __import__("logging").getLogger("cli")
        log.warning("carver_digest build failed", exc_info=True)
        _carver_digest = None
    payload: dict[str, object] = {
        "merge_commit": commit,
        "progress_units": _changed,
        "source_kind": "review",
    }
    if _carver_digest is not None:
        payload["carver_digest"] = _carver_digest
    storage.append_and_apply(
        args.project_id,
        states,
        actor=actor,
        type=EventType.MERGE_RECORDED,
        payload=payload,
        task_id=args.task,
    )

    # Best-effort: the merge is already durably recorded above, so a backlog
    # that cannot be read/written must warn, not sink the whole command.
    from . import backlog_entries, backlog_items
    try:
        backlog_items.tick_merged(backlog_items.resolve_path(cfg), args.task, commit)
        backlog_entries.tick_merged_entries(cfg, args.task, commit)
    except (OSError, UnicodeDecodeError) as e:
        print(f"warning: backlog auto-tick skipped: {e}", file=sys.stderr)

    print(commit)
    return 0


def cmd_pause(args) -> int:
    """pause <project> [task]"""
    from . import paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project_id)

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))

    if hasattr(args, 'task') and args.task:
        # Task-level pause
        flag_path = paths.pause_flag(args.project_id, args.task)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()

        # Load statefile to set paused=True
        states = storage.list_states(args.project_id)

        storage.append_and_apply(
            args.project_id,
            states,
            actor=actor,
            type=EventType.PAUSE_SET,
            task_id=args.task,
            payload={},
        )
    else:
        # Project-level pause
        flag_path = paths.pause_flag(args.project_id)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()

        states = storage.list_states(args.project_id)

        storage.append_event(
            args.project_id,
            actor=actor,
            type=EventType.PAUSE_SET,
            payload={},
        )

    return 0


def _pre_resume_drift_scan(project: str, cfg) -> list | None:
    """PACKAGE RP03: the pre-resume safety check, wired to the SAME
    ground-truth planner `resync` uses (`resync.resync_plan`, fed by its own
    `gather_handoff_presence` / `gather_git_facts` I/O boundaries) -- this
    is deliberately NOT a second drift-detection implementation, just a
    programmatic (non-printing) dry-run of RP01's existing one.

    Returns the list of `ProposedTransition` rows whose `proposed_action !=
    ACTION_NONE` (a possibly-EMPTY list -- "ran clean, no drift"), or `None`
    if the scan itself could not complete (a storage/git failure while
    gathering ground truth). `None` vs `[]` is the load-bearing distinction
    per the RP03 hard constraint: a scan that could not run must never be
    read as "no drift found" by its caller -- that would manufacture false
    confidence in the riskiest operation in the system. `cmd_resume` treats
    both `None` and a non-empty list as "refuse by default", but reports
    each with a DIFFERENT message (see there) so an operator (and a test)
    can tell "verified clean" apart from "could not verify" apart from
    "verified drifted"."""
    from . import storage
    from .resync import (
        ACTION_NONE, gather_git_facts, gather_handoff_presence, resync_plan,
    )

    try:
        states = storage.list_states(project)
        frontmatters = gather_handoff_presence(cfg, states)
        git_facts = gather_git_facts(str(cfg.root), cfg.default_branch, states)
        plan = resync_plan(states, frontmatters, git_facts)
    except Exception:
        return None

    return [p for p in plan if p.proposed_action != ACTION_NONE]


def cmd_resume(args) -> int:
    """resume <project> [task] [--force]

    PACKAGE RP03 2026-07-27 (a pre-resume safety guard): resuming a
    project is the single highest-consequence operator action in the
    system -- the daemon immediately starts dispatching agents, spending
    budget, and merging branches against whatever state it finds. A
    project can sit paused for days, ample time for that state to drift
    from git reality (a branch deleted, a worktree removed, an attempt
    still recorded RUNNING whose process is long dead). Project-level
    resume (no task given) therefore dry-runs the EXISTING resync planner
    first (`_pre_resume_drift_scan` above -- reused, not reimplemented)
    and REFUSES to resume when it reports drift, printing which task(s)
    drifted and the exact repair command. `--force` overrides the refusal
    and records that it did.

    ATOMICITY: the scan runs and this function can return BEFORE either
    the pause-flag unlink or the PAUSE_CLEARED append -- both happen
    strictly AFTER a refusal would already have returned, so a refused
    resume changes NOTHING (re-running without --force refuses
    identically; see test_resume_guard.py's atomicity oracle).

    DEGRADE SAFETY: `_pre_resume_drift_scan` returns `None` (never `[]`)
    when the scan itself fails -- this function refuses on `None` exactly
    like it refuses on a non-empty drift list (never treats "could not
    verify" as "verified clean").

    TASK-LEVEL RESUME IS DELIBERATELY NOT GUARDED. Two reasons: (1) blast
    radius -- project-level resume flips the daemon from fully inert to
    fully active across EVERY task at once (the "riskiest action"
    scenario above); task-level resume only un-pauses the one task the
    operator explicitly named, so the worst case is bounded to that one
    task's next reconcile tick, not a project-wide dispatch storm. (2) the
    reused planner's drift signal is a broader question than "is this
    one task safe to resume" -- e.g. an operator-created task whose
    handoff was never filed under the trove's active globs reads as an
    "orphan" (NEEDS_OPERATOR) under `resync_plan` even though nothing
    about resuming IT specifically is unsafe; scoping the SAME planner to
    one task would false-block legitimate single-task operator actions
    on exactly that shape of task (see the pre-existing `test_resume_task`
    in test_cli.py, whose fixture is precisely this shape, and RP03's own
    `test_resume_task_level_skips_the_drift_guard`).
    """
    from . import paths, storage
    from .types import Actor, ActorKind, EventType

    cfg = _cfg(args.project_id)

    actor = Actor(kind=ActorKind.OPERATOR, id=os.environ.get("USER", "operator"))

    if hasattr(args, 'task') and args.task:
        # Task-level resume -- deliberately unguarded, see docstring above.
        flag_path = paths.pause_flag(args.project_id, args.task)
        flag_path.unlink(missing_ok=True)

        states = storage.list_states(args.project_id)

        storage.append_and_apply(
            args.project_id,
            states,
            actor=actor,
            type=EventType.PAUSE_CLEARED,
            task_id=args.task,
            payload={},
        )
        return 0

    # Project-level resume: RP03 pre-resume drift guard.
    force = getattr(args, "force", False)
    drift = _pre_resume_drift_scan(args.project_id, cfg)

    payload: dict = {}
    if drift is None:
        # The scan itself could not complete -- NEVER read as "no drift".
        if not force:
            print(
                f"error: refusing to resume '{args.project_id}' -- could not "
                f"verify its state first (the pre-resume drift scan "
                f"itself failed). Inspect manually: nyxloomctl resync "
                f"{args.project_id}; pass --force to resume without a "
                f"completed scan once you've confirmed it's safe.",
                file=sys.stderr,
            )
            return 1
        print(
            f"--force: resuming '{args.project_id}' WITHOUT a completed "
            f"pre-resume drift scan (the scan itself failed) -- operator "
            f"override recorded.",
            file=sys.stderr,
        )
        payload = {"forced": True, "drift_scan": "failed"}
    elif drift:
        summary = "; ".join(
            f"{p.task_id} ({p.proposed_action}: {p.evidence})" for p in drift
        )
        if not force:
            print(
                f"error: refusing to resume '{args.project_id}' -- drift "
                f"detected in {len(drift)} task(s): {summary}",
                file=sys.stderr,
            )
            print(
                f"inspect: nyxloomctl resync {args.project_id}   "
                f"repair: nyxloomctl resync {args.project_id} --apply",
                file=sys.stderr,
            )
            return 1
        print(
            f"--force: resuming '{args.project_id}' despite detected drift "
            f"in {len(drift)} task(s): {summary}",
            file=sys.stderr,
        )
        payload = {"forced": True, "drift_tasks": [p.task_id for p in drift]}
    # else: drift == [] -- verified clean, resume proceeds silently exactly
    # as before RP03 (payload stays {}, nothing printed).

    flag_path = paths.pause_flag(args.project_id)
    flag_path.unlink(missing_ok=True)

    states = storage.list_states(args.project_id)

    storage.append_event(
        args.project_id,
        actor=actor,
        type=EventType.PAUSE_CLEARED,
        payload=payload,
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

    cfg = _cfg(args.project_id)
    since_seq = int(args.since) if hasattr(args, 'since') and args.since else 0

    digest_text = notify.digest(cfg, args.project_id, since_seq)
    print(digest_text)
    return 0


def cmd_events(args) -> int:
    """events <project> [--since SEQ] [--type T] [--tail] [--json]

    PACKAGE SP04 2026-07-21 (docs/plan-state-integrity.md A.3 -- the
    greppability bridge). Dumps the event store as JSONL to stdout via
    storage.iter_events, which is backend-agnostic (file or SQLite, per
    the SQLite store) -- so `nyxloomctl events P | jq` / `| lnav` works
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
        for ev in storage.iter_events(args.project_id, last_seq):
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
    docstring).

    PACKAGE GA3 v1 2026-07-25: `--check-gate` is a fourth, opt-in follow-on,
    kept strictly AFTER `run_wizard` returns (the trove -- and therefore a
    loadable `ProjectConfig` -- is guaranteed to exist by then). It is a
    cheap, deterministic, AI-free config read (onboarding_gate.assess_gate),
    NOT a real gate invocation: it only reports whether the project declares
    a usable verification gate and prints the corresponding offer/
    recommendation (docs/plan-gate-adoption.md §GA3). v1 deliberately does
    not scaffold a gate (no Dockerfile/`[gates.*]`-writing/container build --
    that is a v2 follow-up); omitting the flag leaves `onboard`'s output
    byte-identical to before this package."""
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

    if getattr(args, "check_gate", False):
        from . import onboarding_gate

        offer = onboarding_gate.assess_gate(project_folder)
        if offer.has_gate:
            print(f"gate check: declared gate '{offer.gate_id}'")
        else:
            print("gate check: NO GATE declared")
        print(f"  {offer.recommendation}")

    if getattr(args, "scaffold_gate", False):
        from . import gate_scaffold, onboarding_gate

        offer = onboarding_gate.assess_gate(project_folder)
        scaffold_result = gate_scaffold.scaffold_gate(project_folder, offer)
        if scaffold_result.scaffolded:
            print(f"gate scaffolded: '{scaffold_result.gate_id}'")
            print(f"  Dockerfile: {scaffold_result.dockerfile_path}")
            print(f"  config:     {scaffold_result.config_path}")
            print(
                "  review the `# nyxloom-scaffold: adjust` markers, then adopt "
                "run-gate+assay (run-gate-project/CONSUMERS.md, "
                "assay/docs/CONSUMERS.md) to confirm it actually rejects broken code"
            )
        else:
            print(f"gate scaffold skipped: {scaffold_result.skipped_reason}")

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


def cmd_free_models_list(args) -> int:
    """free-models list [--source NAME]

    D-R12: discover currently-free models across every ENABLED source
    (free_models.discover_all) and print them, read-only -- no routes.toml
    write. `--source NAME` restricts discovery to one source (matches a
    `[free_models.sources.<name>]`/DEFAULT_SOURCES name)."""
    from . import free_models

    cfg = free_models.FreeModelsConfig.load()
    models = free_models.discover_all(cfg, only=getattr(args, "source", None))

    rows = [{
        "source": m.source,
        "id": m.id,
        "context_length": str(m.context_length) if m.context_length is not None else "",
        "privacy": m.privacy,
        "free": "True" if m.free else "False",
    } for m in models]

    if rows:
        print(_format_table(rows, ["source", "id", "context_length", "privacy", "free"]))
    else:
        print("no free models discovered")
    return 0


def cmd_free_models_refresh(args) -> int:
    """free-models refresh [--dry-run] [--source NAME]

    D-R12: discover + write. Aggregates every discovered FREE model into a
    regenerated `[tiers.free-high]` + `[routes.auto-*]` managed block in
    routes.toml (free_models.refresh) -- never touches other tiers or
    hand-authored routes. `--dry-run` computes and prints the identical
    plan without writing."""
    from . import free_models

    cfg = free_models.FreeModelsConfig.load()
    result = free_models.refresh(
        cfg,
        only=getattr(args, "source", None),
        dry_run=getattr(args, "dry_run", False),
    )

    print(f"discovered {len(result.discovered)} free model(s)")
    for rid in result.route_ids:
        print(f"  {rid}")
    if result.written:
        print(f"wrote {result.path}")
    else:
        print(f"dry-run: {result.path} not written")
    return 0


def cmd_finding_record(args) -> int:
    """finding record --project-id PROJECT_ID --kind KIND --title TITLE [--body BODY]
    [--field KEY=VALUE ...] [--task-id ID] [--severity S]"""
    from . import findings
    fields = {}
    for item in args.field:
        if "=" not in item:
            print(f"error: --field must be KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        fields[key] = value
    ev = findings.record_finding(
        args.project_id, args.kind, title=args.title, body=args.body,
        fields=fields, task_id=args.task_id, severity=args.severity)
    print(f"recorded F-{args.project_id}-{ev.sequence} ({args.kind})")
    return 0


def cmd_finding_list(args) -> int:
    """finding list [--project-id PROJECT_ID] [--kind KIND]"""
    from . import findings
    from .config import load_registry
    if args.project_id:
        projects = [args.project_id]
    else:
        projects = sorted(load_registry().keys())
    rows = []
    for project in projects:
        for f in findings.load_findings(project):
            if args.kind and f.kind != args.kind:
                continue
            rows.append({
                "id": f.finding_id, "project": f.project, "kind": f.kind,
                "severity": f.severity, "push": "yes" if f.pushable else "no",
                "title": f.title,
            })
    if rows:
        print(_format_table(rows, ["id", "project", "kind", "severity", "push", "title"]))
    else:
        print("no findings")
    return 0


def _resolve_backlog_project(args):
    """--project-id via the registry, else walk up from cwd to the nearest
    nyxloom-trove/nyxloom.toml or .nyxloom/project.toml. Returns
    ProjectConfig or (None, message)."""
    from .config import ProjectConfig, load_registry
    if getattr(args, "project_id", None):
        registry = load_registry()
        if args.project_id not in registry:
            return None, f"unknown project {args.project_id!r} (not registered)"
        return ProjectConfig.load(registry[args.project_id]), ""
    root = Path.cwd().resolve()
    while root != root.parent:
        if ((root / "nyxloom-trove" / "nyxloom.toml").exists()
                or (root / ".nyxloom" / "project.toml").exists()):
            return ProjectConfig.load(root), ""
        root = root.parent
    return None, ("no project config found between cwd and / "
                  "(pass --project-id or run inside the project checkout)")


def cmd_backlog_new(args) -> int:
    """backlog new <title> [typed fields] [--body-from FILE]"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if cfg.backlog_id_prefix is None:
        print("error: project has no [backlog_entries] table in nyxloom.toml",
              file=sys.stderr)
        return 1
    if getattr(args, "interactive", False):
        if args.body_from:
            try:
                body = Path(args.body_from).read_text(encoding="utf-8")
            except OSError as e:
                print(f"error: --body-from: {e}", file=sys.stderr)
                return 1
        else:
            body = None
        from . import backlog_wizard
        path = backlog_wizard.create(
            cfg,
            args.runtime,
            getattr(args, "title", None),
            {
                "type": args.type,
                "severity": args.severity,
                "priority": args.priority,
                "component": args.component,
                "context_estimate": args.context_estimate,
                "folds_into": args.folds_into,
                "provenance": args.provenance,
                "filed_by": args.filed_by,
                "spec_owner": args.spec_owner,
            },
            body=body,
        )
        print(path)
        return 0
    body = None
    if args.body_from:
        try:
            body = Path(args.body_from).read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: --body-from: {e}", file=sys.stderr)
            return 1
    path = backlog_entries.create_entry(
        cfg, args.title,
        type=args.type, severity=args.severity,
        priority=args.priority, component=args.component,
        context_estimate=args.context_estimate,
        folds_into=args.folds_into,
        provenance=args.provenance, filed_by=args.filed_by,
        spec_owner=args.spec_owner, body=body,
    )
    print(path)
    return 0


def cmd_backlog_edit(args) -> int:
    """Interactively edit authorable metadata on one managed entry."""
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if cfg.backlog_id_prefix is None:
        print("error: project has no [backlog_entries] table in nyxloom.toml",
              file=sys.stderr)
        return 1
    from . import backlog_wizard
    path = backlog_wizard.edit(cfg, args.runtime, args.entry_id)
    print(path)
    return 0


def cmd_backlog_promote(args) -> int:
    """backlog promote <inbox-id>"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        path = backlog_entries.promote(cfg, args.inbox_id)
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_backlog_note(args) -> int:
    """backlog note <id> <text>"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        path = backlog_entries.note(cfg, args.entry_id, args.text)
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_backlog_set_status(args) -> int:
    """backlog set-status <id> <status> [--reason R]"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        path = backlog_entries.set_status(cfg, args.entry_id, args.status,
                                          reason=args.reason)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_backlog_list(args) -> int:
    """backlog list [--status S] -- the generated INDEX (optionally filtered)"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if backlog_entries.resolve_dir(cfg) is None:
        print("error: project has no [backlog_entries] table in nyxloom.toml",
              file=sys.stderr)
        return 1
    lines = backlog_entries.render_index(backlog_entries.load_entries(cfg)).splitlines()
    if args.status:
        # header = banner, blank, title, blank, column row, separator (0-5)
        kept = [ln for ln in lines
                if ln.startswith("|") and not ln.startswith(("| ID", "|---"))
                and f"| {args.status} " in ln]
        lines = lines[:6] + kept
    print("\n".join(lines))
    return 0


def cmd_backlog_show(args) -> int:
    """backlog show <id>"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        e = backlog_entries._find(cfg, args.entry_id)
    except KeyError as ex:
        print(f"error: {ex.args[0]}", file=sys.stderr)
        return 1
    print(e.path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_backlog_index(args) -> int:
    """backlog index -- regenerate INDEX.md"""
    from . import backlog_entries
    cfg, err = _resolve_backlog_project(args)
    if cfg is None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if cfg.backlog_id_prefix is None:
        print("error: project has no [backlog_entries] table in nyxloom.toml",
              file=sys.stderr)
        return 1
    print(backlog_entries.write_index(cfg))
    return 0


def cmd_capability_map_refresh(args) -> int:
    """capability-map refresh [--dry-run]

    Fetch every enabled benchmark source, assemble the capability catalog
    (+ operator-surfaced unrated models), and write it to routes.toml's
    capability-map managed block. --dry-run computes + prints without writing."""
    from . import capability_map, paths
    from .types import utc_now

    cfg = capability_map.CapabilityMapConfig.load()
    dry = getattr(args, "dry_run", False)
    store_path = None if dry else paths.routes_path().parent / "benchmark-store.toml"
    now = None if dry else utc_now().isoformat()
    catalog, errors = capability_map.refresh_catalog(
        cfg, store_path=store_path, now=now)
    if not dry:
        capability_map.write_capability_catalog(paths.routes_path(), catalog)
    print(f"assembled {len(catalog)} capability record(s)")
    for name, err in sorted(errors.items()):
        print(f"  source {name} error: {err}")
    print(f"dry-run: {paths.routes_path()} not written" if dry
          else f"wrote {paths.routes_path()} and {store_path}")

    emit_project = getattr(args, "emit_findings", None)
    if not dry and emit_project:
        from . import findings
        from .config import load_registry
        if emit_project not in load_registry():
            print(f"error: --emit-findings project {emit_project!r} is not registered",
                  file=sys.stderr)
            return 2
        seen = {(f.fields.get("cheap_model"), f.fields.get("ref_model"),
                 f.fields.get("metric"))
                for f in findings.load_findings(emit_project)
                if f.kind == "cost_crossover"}
        for cc in capability_map.detect_cost_crossovers(catalog):
            key = (cc["cheap_model"], cc["ref_model"], cc["metric"])
            if key in seen:
                continue
            findings.record_finding(
                emit_project, "cost_crossover",
                title=f"{cc['cheap_model']} ~= {cc['ref_model']} on {cc['metric']}",
                fields=cc, severity="note")
            print(f"  finding: cost_crossover {cc['cheap_model']} vs "
                  f"{cc['ref_model']} on {cc['metric']}")
    return 0


def cmd_route_doctor(args) -> int:
    """route doctor [--no-probe]

    BACKLOG B1: schema-validate every routes.toml route/tier
    (route_doctor.check_schema) and, unless --no-probe, live-probe each
    route (route_doctor.check_live -> adapters.probe -- the SAME function
    daemon.py's _provider_ok memoizes; read-only, never dispatches a task).
    Prints a per-route OK/problem summary plus the findings table; exits 1
    on any critical/error finding (mirrors cmd_doctor's exit-code rule
    exactly)."""
    from . import route_doctor

    live_probe = not getattr(args, "no_probe", False)
    routes, findings = route_doctor.doctor_routes(live_probe=live_probe)

    if routes is not None:
        problem_ids: set[str] = set()
        for f in findings:
            problem_ids.update(rid for rid in f.refs if rid in routes.routes)
        rows = [{
            "route": route_id,
            "cli": route.cli,
            "model": route.model,
            "status": "problem" if route_id in problem_ids else "OK",
        } for route_id, route in sorted(routes.routes.items())]
        if rows:
            print(_format_table(rows, ["route", "cli", "model", "status"]))

    if findings:
        if routes is not None:
            print()
        print(_format_table([{
            "kind": f.kind,
            "severity": f.severity,
            "message": f.message,
            "refs": ", ".join(f.refs),
        } for f in findings], ["kind", "severity", "message", "refs"]))

    has_critical_or_error = any(f.severity in ("critical", "error") for f in findings)
    return 1 if has_critical_or_error else 0


def _print_operator_credential(record) -> None:
    """THE intentional secret-retrieval surface. Printed to stdout only -- the
    value is never passed to log.*, never written to an event payload, and
    never rendered into a dashboard page (render.py's dashboard prompts for it
    at runtime instead). It does land in this terminal's scrollback, so prefer
    piping it where it is needed over re-running this by hand."""
    print(f"operator: {record.operator_id}")
    print(f"generation: {record.generation}")
    print(f"credential: {record.credential}")
    print(f"header: Authorization: Bearer {record.credential}")


def cmd_auth(args) -> int:
    """Bootstrap, show, or atomically rotate the HTTP operator credential.

    A store this loader refuses (wrong mode, foreign owner, truncated write)
    makes the daemon refuse every mutation, so `show`/`rotate` report the
    reason on stderr and exit 1 rather than raising -- and `rotate --force`
    is the documented way back, since an unreadable store cannot carry its
    identity or generation forward.

    Refusals and rotations are auditable with
    `nyxloomctl events _nyxloom-control`."""
    from . import control_auth, paths, storage
    from .types import EventType

    store = control_auth.CredentialStore(paths.daemon_dir())
    try:
        if args.auth_cmd == "bootstrap":
            _print_operator_credential(store.ensure(args.operator))
            return 0
        if args.auth_cmd == "show":
            _print_operator_credential(store.load())
            return 0
        # `main` dispatches here ONLY for show/bootstrap/rotate (anything else
        # prints usage and exits 2 there), so rotate is the remaining case. An
        # `else: return 2` stood here and was deleted rather than tested: it was
        # unreachable through every entry point, and an unreachable line is a
        # line no test can honestly cover.
        record = store.rotate(args.operator, force=args.force)
    except control_auth.CredentialStoreError as exc:
        print(f"error: {exc} ({store.path})", file=sys.stderr)
        if args.auth_cmd == "rotate":
            print("hint: `nyxloomctl auth rotate --force` replaces an unreadable "
                  "store with a fresh credential", file=sys.stderr)
        return 1

    # Print BEFORE auditing: the credential has already been replaced on disk,
    # so an unavailable event store must not cost the operator the only copy
    # of the value that now works.
    _print_operator_credential(record)
    try:
        storage.append_event(
            control_auth.CONTROL_LEDGER_PROJECT, actor=record.actor,
            type=EventType.CONTROL_CREDENTIAL_ROTATED,
            payload={"generation": record.generation, "forced": bool(args.force)},
        )
    except Exception as exc:
        print(f"warning: rotation succeeded but could not be audited: {exc!r}",
              file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the local authoring CLI through its cli-extended registry."""
    from .cli_registry import primary_cli

    return primary_cli().run(argv=argv, interactive_extra="nyxloom[interactive]")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
