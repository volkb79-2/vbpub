"""Declarative command registries for Nyxloom's installed CLIs.

The cli-extended registries own parsing, help, and dispatch. Domain handlers
remain in :mod:`nyxloom.cli`; this module only declares their public grammar
and applies the invocation boundary around them.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    PromptCancelled,
    VerbGroup,
    VerbSpec,
)

from . import __version__, backlog_entries, cli, findings


def _identity(command: str) -> CliIdentity:
    return CliIdentity(
        name="NYXLOOM",
        version=__version__,
        long_name="files-first control plane for role-separated AI-assisted development",
        command=command,
    )


def _traceback_option() -> OptionSpec:
    return OptionSpec(
        ("--traceback",),
        "show a traceback for an unexpected command failure",
        group="DEBUGGING",
        parser_kwargs={"action": "store_true"},
    )


def _finding_field(value: str) -> str:
    """Validate finding KEY=VALUE syntax during parsing, before any dispatch."""
    key, separator, _field_value = value.partition("=")
    if not separator or not key:
        raise argparse.ArgumentTypeError(
            "--field must be KEY=VALUE with a non-empty KEY"
        )
    return value


def _arg(
    name: str,
    description: str,
    *,
    metavar: str | None = None,
    **parser_kwargs: Any,
) -> ArgumentSpec:
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=parser_kwargs)


def _opt(
    flags: str | Sequence[str], description: str, **parser_kwargs: Any
) -> OptionSpec:
    if isinstance(flags, str):
        flags = (flags,)
    if "default" not in parser_kwargs:
        action = parser_kwargs.get("action", "store")
        if action == "store_true":
            parser_kwargs["default"] = False
        elif action == "store_false":
            parser_kwargs["default"] = True
        elif not parser_kwargs.get("required", False):
            parser_kwargs["default"] = None
    return OptionSpec(tuple(flags), description, parser_kwargs=parser_kwargs)


def _invoke(
    handler: Callable[[Any], int],
    *,
    fields: Mapping[str, Any] | None = None,
    bootstrap: bool = False,
    validate: Callable[[Any], None] | None = None,
) -> Callable[[Any, Any], int]:
    """Adapt an existing Nyxloom handler to cli-extended's runtime boundary."""

    def run(args: Any, runtime: Any) -> int:
        args.runtime = runtime
        for name, value in (fields or {}).items():
            setattr(args, name, value)
        if validate is not None:
            validate(args)
        cli._bootstrap_logging(args, persist=bootstrap)
        try:
            return handler(args)
        except (CliFailure, PromptCancelled, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if bool(getattr(args, "traceback", False)):
                raise
            print(f"error: {exc}", file=runtime.output.stderr)
            return 1

    return run


def _verb(
    name: str,
    description: str,
    handler: Callable[[Any], int] | None = None,
    *,
    arguments: Sequence[ArgumentSpec] = (),
    options: Sequence[OptionSpec] = (),
    fields: Mapping[str, Any] | None = None,
    group: str = VerbGroup.EXPLORATION.value,
    mutating: bool = False,
    interactive: bool = False,
    expensive: bool = False,
    include_json: bool = False,
    include_progress: bool = False,
    bootstrap: bool = False,
    validate: Callable[[Any], None] | None = None,
    delegate: Any = None,
    examples: Sequence[str] = (),
) -> VerbSpec:
    callback = None
    if handler is not None:
        callback = _invoke(
            handler,
            fields=fields,
            bootstrap=bootstrap,
            validate=validate,
        )
    return VerbSpec(
        name=name,
        description=description,
        group=group,
        arguments=tuple(arguments),
        options=tuple(options),
        handler=callback,
        delegate=delegate,
        mutating=mutating,
        interactive=interactive,
        expensive=expensive,
        include_json=include_json,
        include_progress=include_progress,
        confirmation_required=False if mutating else None,
        examples=tuple(examples),
    )


def _registry(
    prog: str,
    description: str,
    *,
    getting_started: Sequence[str] = (),
    bootstrap: bool = False,
) -> CliRegistry:
    return CliRegistry(
        _identity(prog.split()[0]),
        prog=prog,
        description=description,
        getting_started=getting_started,
        global_options=(_traceback_option(),),
        logging_logger="nyxloom" if bootstrap else "nyxloom.cli",
    )


def _leaf(
    prog: str,
    name: str,
    description: str,
    handler: Callable[[Any], int],
    *,
    arguments: Sequence[ArgumentSpec] = (),
    options: Sequence[OptionSpec] = (),
    fields: Mapping[str, Any] | None = None,
    bootstrap: bool = False,
    mutating: bool = False,
    interactive: bool = False,
    expensive: bool = False,
    include_json: bool = False,
    validate: Callable[[Any], None] | None = None,
    examples: Sequence[str] = (),
) -> VerbSpec:
    del prog
    return _verb(
        name,
        description,
        handler,
        arguments=arguments,
        options=options,
        fields=fields,
        bootstrap=bootstrap,
        mutating=mutating,
        interactive=interactive,
        expensive=expensive,
        include_json=include_json,
        validate=validate,
        examples=examples,
    )


def _group(
    prog: str,
    name: str,
    description: str,
    children: Sequence[VerbSpec],
) -> VerbSpec:
    nested = _registry(f"{prog} {name}", description)
    for child in children:
        nested.register(child)
    return _verb(name, description, delegate=nested.build())


def _doctor_guard(args: Any) -> None:
    liveness = getattr(args, "liveness", False)
    rebuild = getattr(args, "rebuild", False)
    write = getattr(args, "write", False)
    if liveness and (rebuild or write):
        raise CliFailure(
            "--liveness cannot be combined with --rebuild or --write",
            exit_code=2,
            show_help=True,
        )
    if write and not rebuild:
        raise CliFailure(
            "--write requires --rebuild",
            exit_code=2,
            show_help=True,
        )


def _backlog_new_guard(args: Any) -> None:
    if not args.title and not getattr(args, "interactive", False):
        raise CliFailure(
            "TITLE is required unless --interactive is used",
            exit_code=2,
            show_help=True,
        )


def _resync_guard(args: Any) -> None:
    if getattr(args, "apply_content_merges", False) and not getattr(args, "apply", False):
        raise CliFailure(
            "--apply-content-merges requires --apply",
            exit_code=2,
            show_help=True,
        )


def _capability_refresh_guard(args: Any) -> None:
    if getattr(args, "dry_run", False) and getattr(args, "emit_findings", False):
        raise CliFailure(
            "--emit-findings cannot be combined with --dry-run",
            exit_code=2,
            show_help=True,
        )


def _extract_guard(args: Any) -> None:
    """Reject incoherent extraction syntax before reading a session source."""
    message = cli._validate_render_and_follow_flags(args)
    if message is not None:
        raise CliFailure(message, exit_code=2, show_help=True)
    if getattr(args, "since", None) and getattr(args, "since_file", None):
        raise CliFailure(
            "--since and --since-file are mutually exclusive",
            exit_code=2,
            show_help=True,
        )
    if getattr(args, "task", None) is not None and getattr(args, "task_file", None) is not None:
        raise CliFailure(
            "--task and --task-file are mutually exclusive",
            exit_code=2,
            show_help=True,
        )
    if getattr(args, "follow", False) and getattr(args, "strip_stale_wakeups", False):
        raise CliFailure(
            "extract --follow cannot be combined with --strip-stale-wakeups: "
            "the trailing-run transform requires a fixed, complete span",
            exit_code=2,
            show_help=True,
        )
    if getattr(args, "json", False):
        if getattr(args, "ledger", False):
            raise CliFailure(
                "--ledger has no JSON equivalent yet -- text mode only",
                exit_code=2,
                show_help=True,
            )
        text_only = (
            "--blank-lines" if getattr(args, "blank_lines", None) is not None else None,
            "--gap-marker" if getattr(args, "gap_marker", None) is not None else None,
            "--min-gap-records" if getattr(args, "min_gap_records", None) is not None else None,
            "--ledger" if getattr(args, "ledger", False) else None,
            "--show-gap-source" if getattr(args, "show_gap_source", False) else None,
            "--task" if getattr(args, "task", None) is not None else None,
            "--task-file" if getattr(args, "task_file", None) is not None else None,
            "--render-markdown" if getattr(args, "render_markdown", False) else None,
            "--highlight" if getattr(args, "highlight", False) else None,
            "--show-timestamps" if getattr(args, "show_timestamps", None) is not None else None,
            "--timestamp-format" if getattr(args, "timestamp_format", None) is not None else None,
            "--extract-metadata" if getattr(args, "extract_metadata", None) is not None else None,
            "--color/--no-color" if getattr(args, "color", None) is not None else None,
        )
        requested = [flag for flag in text_only if flag is not None]
        if requested:
            raise CliFailure(
                f"{', '.join(requested)} only affect text-mode rendering and cannot be combined with --json",
                exit_code=2,
                show_help=True,
            )


def _extract_report_guard(args: Any) -> None:
    if getattr(args, "detailed", False) and getattr(args, "report_type", None) not in (None, "csv"):
        raise CliFailure(
            "--detailed is a legacy alias for --type csv and cannot be combined with another --type",
            exit_code=2,
            show_help=True,
        )
    if getattr(args, "report_type", None) == "csv" and getattr(args, "json", False):
        raise CliFailure(
            "--type csv selects CSV output and cannot be combined with --json",
            exit_code=2,
            show_help=True,
        )


def _options_project_id() -> OptionSpec:
    return _opt("--project-id", "Registered project id", metavar="PROJECT_ID")


def _local_backlog_group() -> VerbSpec:
    prog = "nyxloom"
    common_project = _options_project_id()
    new_options = (
        common_project,
        _opt("--type", "Entry type", choices=("feature", "bugfix"), default=None),
        _opt("--severity", "Entry severity", choices=("low", "medium", "high"), default=None),
        _opt("--priority", "Entry priority", type=int, default=None),
        _opt("--component", "Owning component", default=None),
        _opt("--context-estimate", "Estimated context size", choices=("small", "medium", "large"), default=None),
        _opt("--folds-into", "Related managed entry", default=None),
        _opt("--provenance", "Where this entry came from", default=None),
        _opt("--filed-by", "Filer's name", dest="filed_by", default=None),
        _opt("--spec-owner", "Owning spec's name", dest="spec_owner", default=None),
        _opt("--body-from", "Read the entry body from this file", dest="body_from", metavar="FILE", default=None),
        _opt("--interactive", "Prompt for entry metadata", action="store_true"),
    )
    children = [
        _leaf(
            prog,
            "new",
            "Create a managed backlog entry.",
            cli.cmd_backlog_new,
            arguments=(_arg("title", "One-line entry title", nargs="?"),),
            options=new_options,
            fields={"backlog_cmd": "new"},
            mutating=True,
            interactive=True,
            validate=_backlog_new_guard,
            examples=("nyxloom backlog new 'Add cache metrics'", "nyxloom backlog new --interactive"),
        ),
        _leaf(
            prog,
            "edit",
            "Interactively edit a managed entry's authorable metadata.",
            cli.cmd_backlog_edit,
            arguments=(_arg("entry_id", "Managed entry id", metavar="ENTRY_ID"),),
            options=(common_project,),
            fields={"backlog_cmd": "edit"},
            mutating=True,
            interactive=True,
        ),
        _leaf(prog, "promote", "Promote an inbox item into the managed backlog.", cli.cmd_backlog_promote,
              arguments=(_arg("inbox_id", "Inbox item id", metavar="INBOX_ID"),), options=(common_project,),
              fields={"backlog_cmd": "promote"}, mutating=True),
        _leaf(prog, "note", "Append a dated update to an entry.", cli.cmd_backlog_note,
              arguments=(_arg("entry_id", "Managed entry id", metavar="ENTRY_ID"), _arg("text", "Note text")),
              options=(common_project,), fields={"backlog_cmd": "note"}, mutating=True),
        _leaf(prog, "set-status", "Apply a typed status transition.", cli.cmd_backlog_set_status,
              arguments=(_arg("entry_id", "Managed entry id", metavar="ENTRY_ID"),
                         _arg("status", "New status", choices=("open", "carved", "fixed", "withdrawn", "obsolete"))),
              options=(common_project, _opt("--reason", "Transition reason", default=None)),
              fields={"backlog_cmd": "set-status"}, mutating=True),
        _leaf(prog, "list", "List managed backlog entries without writing files.", cli.cmd_backlog_list,
              options=(common_project, _opt("--status", "Filter by status", metavar="STATUS",
                                            choices=backlog_entries.STATUSES, default=None)),
              fields={"backlog_cmd": "list"}),
        _leaf(prog, "show", "Show one managed backlog entry.", cli.cmd_backlog_show,
              arguments=(_arg("entry_id", "Managed entry id", metavar="ENTRY_ID"),), options=(common_project,),
              fields={"backlog_cmd": "show"}),
        _leaf(prog, "index", "Regenerate the managed backlog index.", cli.cmd_backlog_index,
              options=(common_project,), fields={"backlog_cmd": "index"}, mutating=True),
    ]
    return _group(prog, "backlog", "Manage project-local managed backlog entries.", children)


def primary_cli():
    """Build the local project-authoring CLI."""
    registry = _registry(
        "nyxloom",
        "Local Nyxloom project authoring. No registry or daemon is required.",
        getting_started=("nyxloom lint", "nyxloom backlog list", "nyxloom --help"),
    )
    registry.register(_leaf("nyxloom", "lint", "Lint the current project's configured handoffs, or explicit handoff paths.",
                            cli.cmd_lint_local,
                            arguments=(_arg("path", "Handoff Markdown paths; omit to discover this project", metavar="HANDOFF_FILE", nargs="*"),),
                            examples=("nyxloom lint", "nyxloom lint nyxloom-trove/handoffs/example.md")))
    registry.register(_leaf("nyxloom", "init", "Scaffold a Nyxloom trove in a project folder.", cli.cmd_init,
                            arguments=(_arg("project_folder", "Target project folder", metavar="PROJECT_FOLDER"),),
                            mutating=True))
    registry.register(_leaf("nyxloom", "onboard", "Run the local project onboarding flow.", cli.cmd_onboard,
                            arguments=(_arg("project_folder", "Target project folder", metavar="PROJECT_FOLDER"),),
                            options=(
                                _opt("--maturity", "Project maturity", choices=("empty", "partial", "mature"), default="empty"),
                                _opt("--docs", "Whether project docs exist", choices=("present", "absent"), default="absent"),
                                _opt("--mode", "Onboarding mode", choices=("derive-from-code", "code-good-docs-absent", "greenfield-define-it"), default="greenfield-define-it"),
                                _opt("--scan-path", "Path for a later AI scan; repeatable", action="append", dest="scan_paths", metavar="SCAN_PATH", default=None),
                                _opt("--scan", "Run the read-only assessment scan", action="store_true"),
                                _opt("--questionnaire", "Run the guided questionnaire", action="store_true"),
                                _opt("--check-gate", "Check whether the project declares a gate", action="store_true", dest="check_gate"),
                                _opt("--scaffold-gate", "Write a reviewable gate skeleton when none is declared", action="store_true", dest="scaffold_gate"),
                            ), mutating=True))
    registry.register(_local_backlog_group())
    return registry.build()


def harness_cli():
    """Build the host-independent AI-harness session CLI."""
    registry = _registry(
        "nyxloom-harness",
        "Inspect and extract AI-harness session data. No Nyxloom host state is initialized.",
        getting_started=("nyxloom-harness extract-sessions codex", "nyxloom-harness extract SESSION_ID"),
    )
    extraction_path = _arg("path", "Session file, store, directory, or supported session id", metavar="SESSION_LOG")
    common_follow = (
        _opt(("--follow", "-f"), "Follow new session records", action="store_true"),
        _opt("--interval", "Follow polling interval in seconds", type=float, default=None),
        _opt("--bell", "Ring when attention is needed", action="store_true"),
        _opt("--on-attention", "Attention notification behavior", default=None),
        _opt("--notify-project", "Nyxloom project for attention notifications", default=None),
        _opt("--attention-min-chars", "Minimum attention message size", type=int, default=None),
    )
    registry.register(_leaf("nyxloom-harness", "extract", """Create a structured, resumable session extract. The default operator-review profile selects the newest epoch and applies its checkpoint and word limits; --profile all includes ordinary prose across the available epochs. Explicit selection controls override profile defaults.

With --follow, Nyxloom prints a one-shot prefix and then reads only appended payload plus bounded prefix/tail fingerprints for rewrite detection. Unchanged polls read no content; there is no whole-file rescan. Follow-only controls require --follow, fixed-span and task-banner controls cannot be combined with it, and JSON output is unsupported while following.""", cli.cmd_extract,
        arguments=(extraction_path,),
        options=(
            _opt("--opencode-session", "Select an OpenCode session id", default=None),
            _opt("--format", "Session source format", choices=("claude-code", "codex", "opencode", "reasonix"), default=None),
            _opt("--json", "Emit structured JSON output", action="store_true"),
            _opt("--profile", "Extraction profile", choices=("all", "operator-review"), default=None),
            _opt(("--answer-length", "--long-threshold"), "Long answer threshold", type=int, default=None),
            _opt("--include-thinking", "Include model thinking", action="store_true"),
            _opt("--show-api-errors", "Show API error records", action="store_true"),
            _opt("--show-compaction-content", "Show compaction content", action="store_true"),
            _opt("--show-tool-calls", "Show tool call details", action="store_true"),
            _opt("--show-tool-call-intent", "Show tool call intent", action="store_true"),
            _opt(("--max-checkpoints", "--checkpoints"), "Maximum checkpoints", type=int, default=None),
            _opt("--max-words", "Maximum output words", type=int, default=None),
            _opt(("--max-compactions", "--max-lifecycle-markers"), "Maximum lifecycle markers", type=int, default=None),
            _opt("--max-time-minutes", "Maximum source time span", type=int, default=None),
            _opt("--epochs", "Select epochs: N, A:B, or all", default=None),
            _opt("--since", "Start after this source marker", default=None),
            _opt("--since-file", "Read the last marker from a prior extract", default=None),
            _opt("--until", "Stop at this source marker", default=None),
            _opt("--ledger", "Include source event ledger", action="store_true"),
            _opt(("--blank-lines", "--insert-blank-lines"), "Blank lines between extract sections", type=int, default=None),
            _opt("--gap-marker", "Gap marker style", choices=("full", "inline", "inline2", "inline-short", "none"), default=None),
            _opt("--min-gap-records", "Minimum records represented by a gap marker", type=int, default=None),
            _opt("--show-gap-source", "Show source range for gaps", action="store_true"),
            _opt("--render-markdown", "Render Markdown", action="store_true"),
            _opt("--highlight", "Highlight terminal output", action="store_true"),
            _opt("--show-timestamps", "Timestamp placement", choices=("pre", "post", "both", "none"), default=None),
            _opt("--timestamp-format", "Timestamp display format", default=None),
            _opt("--extract-metadata", "Metadata placement", choices=("pre", "post", "both"), default=None),
            _opt("--strip-stale-wakeups", "Remove stale wakeup events", action="store_true"),
            _opt("--redact-pattern", "Additional redaction regular expression; repeatable", action="append", default=None),
            _opt("--task", "Task context text", default=None),
            _opt("--task-file", "Read task context from a file", default=None),
            *common_follow,
        ), fields={"cmd": "extract"}, validate=_extract_guard))
    lossless_options = (
        _opt("--opencode-session", "Select an OpenCode session id", default=None),
        _opt("--format", "Session source format", choices=("claude-code", "codex", "opencode", "reasonix"), default=None),
        _opt("--since", "Start after this source marker", default=None),
        _opt("--since-file", "Read the last marker from a prior extract", default=None),
        _opt("--until", "Stop at this source marker", default=None),
        _opt("--highlight", "Highlight terminal output", action="store_true"),
        *common_follow,
    )
    registry.register(_leaf("nyxloom-harness", "extract-lossless", """Print recovered source prose and available thinking without selection or windowing. Output remains verbatim; --redact-pattern is not accepted.

With --follow, Nyxloom prints a one-shot prefix and then reads only appended payload plus bounded prefix/tail fingerprints for rewrite detection. Unchanged polls read no content; there is no whole-file rescan. Follow-only controls require --follow, and fixed-span controls cannot be combined with it.""", cli.cmd_extract_lossless,
        arguments=(extraction_path,), options=lossless_options, fields={"cmd": "extract-lossless"}, validate=_extract_guard))
    registry.register(_leaf("nyxloom-harness", "extract-debug", "Inspect extraction classification and window decisions.", cli.cmd_extract_debug,
        arguments=(extraction_path,), fields={"cmd": "extract-debug"}, options=(
            _opt("--opencode-session", "Select an OpenCode session id", default=None),
            _opt("--format", "Session source format", choices=("claude-code", "codex", "opencode", "reasonix"), default=None),
            _opt("--profile", "Extraction profile", choices=("all", "operator-review"), default=None),
            _opt(("--max-checkpoints", "--checkpoints"), "Maximum checkpoints", type=int, default=None),
            _opt(("--answer-length", "--long-threshold"), "Long answer threshold", type=int, default=None),
            _opt("--max-words", "Maximum output words", type=int, default=None),
            _opt("--include-thinking", "Include model thinking", action="store_true"),
            _opt(("--max-compactions", "--max-lifecycle-markers"), "Maximum lifecycle markers", type=int, default=None),
            _opt("--max-time-minutes", "Maximum source time span", type=int, default=None),
            _opt("--epochs", "Select epochs: N, A:B, or all", default=None),
            _opt("--show-api-errors", "Show API error records", action="store_true"),
            _opt("--show-compaction-content", "Show compaction content", action="store_true"),
            _opt("--show-tool-calls", "Show tool call details", action="store_true"),
            _opt("--show-tool-call-intent", "Show tool call intent", action="store_true"),
            _opt("--since", "Start after this source marker", default=None),
            _opt("--since-file", "Read the last marker from a prior extract", default=None),
            _opt("--until", "Stop at this source marker", default=None),
            _opt("--min-gap-records", "Minimum records represented by a gap marker", type=int, default=None),
            _opt("--gap-marker", "Gap marker style", choices=("full", "inline", "inline2", "inline-short", "none"), default=None),
            _opt(("--blank-lines", "--insert-blank-lines"), "Blank lines between extract sections", type=int, default=None),
            _opt("--show-timestamps", "Timestamp placement", choices=("pre", "post", "both", "none"), default=None),
            _opt("--timestamp-format", "Timestamp display format", default=None),
            _opt("--extract-metadata", "Metadata placement", choices=("pre", "post", "both"), default=None),
            _opt("--render-markdown", "Render Markdown", action="store_true"),
            _opt("--highlight", "Highlight terminal output", action="store_true"),
            _opt("--strip-stale-wakeups", "Remove stale wakeup events", action="store_true"),
            _opt("--redact-pattern", "Additional redaction regular expression; repeatable", action="append", default=None),
            _opt("--ledger", "Include source event ledger", action="store_true"),
        ), validate=_extract_guard))
    registry.register(_leaf("nyxloom-harness", "extract-report", "Generate a session cost and timeline report. report-sheet is the default; report-detailed prints a readable row per API call; csv is the spreadsheet format. --detailed remains an alias for --type csv.", cli.cmd_extract_report,
        arguments=(extraction_path,), fields={"cmd": "extract-report"}, options=(
            _opt("--opencode-session", "Select an OpenCode session id", default=None),
            _opt("--format", "Session source format", choices=("claude-code", "codex", "opencode"), default=None),
            _opt("--type", "Report format", dest="report_type", choices=("report-sheet", "report-detailed", "csv"), default=None),
            _opt("--detailed", "Include detailed report fields", action="store_true"),
            _opt("--json", "Emit structured JSON output", action="store_true"),
        ), validate=_extract_report_guard))
    registry.register(_leaf("nyxloom-harness", "extract-sessions", "Discover session families in a harness store or directory.", cli.cmd_extract_sessions,
        arguments=(_arg("path", "Harness name or session path", metavar="TOOL_OR_SESSIONS_PATH"),), fields={"cmd": "extract-sessions"}, options=(
            _opt("--format", "Session source format", choices=("claude-code", "codex", "opencode"), default=None),
            _opt("--recurse", "Recurse into nested session folders; an omitted value means true", nargs="?", const="true", choices=("true", "false"), default="true"),
            _opt("--json", "Emit structured JSON output", action="store_true"),
        )))
    return registry.build()


def _ctl_project_group() -> VerbSpec:
    return _group("nyxloomctl", "project", "Manage the host project registry.", (
        _leaf("nyxloomctl", "add", "Register a project with the local host.", cli.cmd_project_add,
              arguments=(_arg("id", "Short registry id", metavar="PROJECT_ID"), _arg("root", "Project root", metavar="PROJECT_ROOT")),
              fields={"project_cmd": "add"}, bootstrap=True, mutating=True),
        _leaf("nyxloomctl", "list", "List locally registered projects.", cli.cmd_project_list,
              fields={"project_cmd": "list"}, bootstrap=True),
    ))


def _ctl_group(name: str, description: str, children: Sequence[VerbSpec]) -> VerbSpec:
    return _group("nyxloomctl", name, description, children)


def operator_cli():
    """Build the local host-control and operator CLI."""
    registry = _registry(
        "nyxloomctl",
        "Local Nyxloom host administration and operator workflows. No HTTP client is used.",
        getting_started=("nyxloomctl doctor", "nyxloomctl status", "nyxloomctl project list"),
    )
    registry.register(_ctl_project_group())
    registry.register(_leaf("nyxloomctl", "lint", "Lint handoffs for all host-registered projects.", cli.cmd_lint_registered,
                            bootstrap=True, examples=("nyxloomctl lint",)))
    registry.register(_leaf("nyxloomctl", "doctor", "Check registered project and host health.", cli.cmd_doctor,
        bootstrap=True, validate=_doctor_guard, options=(
            _opt("--project-id", "Registered project id", metavar="PROJECT_ID", default=None),
            _opt("--rebuild", "Show state replay differences", action="store_true"),
            _opt("--write", "Write replayed state files", action="store_true"),
            _opt("--liveness", "Run the fast service liveness checks", action="store_true"),
        )))
    registry.register(_leaf("nyxloomctl", "status", "Show current task states for registered projects.", cli.cmd_status,
        bootstrap=True, options=(_opt("--project-id", "Registered project id", metavar="PROJECT_ID", default=None),)))
    registry.register(_leaf("nyxloomctl", "resync", "Compare recorded task state with project and Git evidence.", cli.cmd_resync,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"),), bootstrap=True,
        validate=_resync_guard, mutating=True, options=(
            _opt("--apply", "Apply high-confidence state transitions", action="store_true"),
            _opt("--apply-content-merges", "Also apply lower-confidence content-merge evidence", action="store_true"),
        )))
    registry.register(_leaf("nyxloomctl", "render", "Regenerate the host dashboard files.", cli.cmd_render, bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "migrate-store", "Migrate a project's event store to SQLite.", cli.cmd_migrate_store,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"),), bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "daemon", "Run the daemon in the foreground.", cli.cmd_daemon,
        bootstrap=False, mutating=True, expensive=True))
    registry.register(_ctl_group("auth", "Manage local HTTP operator credentials.", (
        _leaf("nyxloomctl", "show", "Show the current operator identity and credential.", cli.cmd_auth,
              fields={"auth_cmd": "show"}, bootstrap=True),
        _leaf("nyxloomctl", "bootstrap", "Create the initial local operator credential.", cli.cmd_auth,
              fields={"auth_cmd": "bootstrap"}, bootstrap=True, mutating=True,
              options=(_opt("--operator", "Operator id", default=None),)),
        _leaf("nyxloomctl", "rotate", "Rotate the local operator credential.", cli.cmd_auth,
              fields={"auth_cmd": "rotate"}, bootstrap=True, mutating=True,
              options=(_opt("--operator", "Operator id", default=None), _opt("--force", "Recover a rejected credential store", action="store_true"))),
    )))
    registry.register(_leaf("nyxloomctl", "tick", "Run one daemon work pass.", cli.cmd_tick,
        bootstrap=True, mutating=True, options=(_opt("--project-id", "Restrict work to a registered project", default=None),)))
    registry.register(_leaf("nyxloomctl", "decide", "Record an explicit decision response.", cli.cmd_decide,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("decision_id", "Decision id", metavar="DECISION_ID")),
        options=(_opt("--choose", "Chosen decision", required=True), _opt("--note", "Optional decision note", default=None)), bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "discuss", "Show the command to discuss a pending decision.", cli.cmd_discuss,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("decision_id", "Decision id", metavar="DECISION_ID")), bootstrap=True))
    registry.register(_leaf("nyxloomctl", "intake", "Advance one local project intake conversation.", cli.cmd_intake,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("intake_id", "Intake conversation id", metavar="INTAKE_ID"), _arg("message", "Message to the intake agent")), bootstrap=True, mutating=True, expensive=True))
    registry.register(_ctl_group("intake-bridge", "Poll the configured intake bridge once.", (
        _leaf("nyxloomctl", "poll", "Poll one bridge batch and process at most one intake turn.", cli.cmd_intake_bridge_poll,
              arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"),),
              options=(_opt("--transport", "Bridge transport", choices=("mmctl", "rest"), default=None),),
              bootstrap=True, mutating=True, expensive=True),
    )))
    registry.register(_leaf("nyxloomctl", "reject", "Return a merge-ready task to rework.", cli.cmd_reject,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("task", "Task id", metavar="TASK_ID")),
        options=(_opt("--note", "Optional rejection note", default=None),), bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "merge", "Record a manually completed merge.", cli.cmd_merge,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("task", "Task id", metavar="TASK_ID")),
        options=(_opt("--commit", "Merge commit SHA", default=None), _opt("--force", "Override the pre-merge gate refusal", action="store_true")),
        bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "pause", "Pause a project or a task.", cli.cmd_pause,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("task", "Task id; omit for project pause", metavar="TASK_ID", nargs="?")), bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "resume", "Resume a paused project or task.", cli.cmd_resume,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"), _arg("task", "Task id; omit for project resume", metavar="TASK_ID", nargs="?")),
        options=(_opt("--force", "Resume despite a failed or drifting project scan", action="store_true"),), bootstrap=True, mutating=True))
    registry.register(_leaf("nyxloomctl", "leases", "Show host mutex lease holders.", cli.cmd_leases, bootstrap=True))
    registry.register(_leaf("nyxloomctl", "digest", "Show project notifications after an optional cursor.", cli.cmd_digest,
        arguments=(_arg("project_id", "Registered project id", metavar="PROJECT_ID"),),
        options=(_opt("--since", "Only notifications after this sequence", metavar="SEQ", type=int, default=None),), bootstrap=True))
    registry.register(_leaf("nyxloomctl", "events", "Dump or follow a registered project's event stream.", cli.cmd_events,
        arguments=(_arg("project_id", "Project id or event-store key", metavar="PROJECT_ID"),), bootstrap=True,
        options=(_opt("--since", "Only events after this sequence", metavar="SEQ", type=int, default=None),
                _opt("--type", "Filter by event type", metavar="EVENT_TYPE", default=None),
                _opt("--tail", "Follow appended events", action="store_true"),
                _opt("--json", "Explicit JSONL output", action="store_true"))))
    registry.register(_ctl_group("free-models", "Discover and refresh free model routing data.", (
        _leaf("nyxloomctl", "list", "Discover free models across enabled sources.", cli.cmd_free_models_list,
              fields={"free_models_cmd": "list"}, bootstrap=True, expensive=True,
              options=(_opt("--source", "Restrict discovery to one source", default=None),)),
        _leaf("nyxloomctl", "refresh", "Refresh the managed free model routes block.", cli.cmd_free_models_refresh,
              fields={"free_models_cmd": "refresh"}, bootstrap=True, mutating=True, expensive=True,
              options=(_opt("--source", "Restrict discovery to one source", default=None), _opt("--dry-run", "Compute changes without writing", action="store_true"))),
    )))
    registry.register(_ctl_group("capability-map", "Refresh the local model capability catalog.", (
        _leaf("nyxloomctl", "refresh", "Rebuild the capability catalog from benchmark sources.", cli.cmd_capability_map_refresh,
              fields={"capability_map_cmd": "refresh"}, bootstrap=True, mutating=True, expensive=True,
              validate=_capability_refresh_guard,
              options=(_opt("--dry-run", "Compute without writing", action="store_true"),
                       _opt("--emit-findings", "Record crossover findings under a registered project", metavar="PROJECT_ID", default=None))),
    )))
    registry.register(_ctl_group("route", "Validate configured routes and provider reachability.", (
        _leaf("nyxloomctl", "doctor", "Validate routes and optionally probe providers.", cli.cmd_route_doctor,
              fields={"route_cmd": "doctor"}, bootstrap=True, expensive=True,
              options=(_opt("--no-probe", "Validate schema only; do not contact providers", action="store_true"),)),
    )))
    registry.register(_ctl_group("finding", "Record or list host findings.", (
        _leaf("nyxloomctl", "record", "Record a finding for a registered project.", cli.cmd_finding_record,
              fields={"finding_cmd": "record"}, bootstrap=True, mutating=True,
              options=(_opt("--project-id", "Registered project id", required=True),
                       _opt("--kind", "Finding kind", choices=tuple(findings.FINDING_KINDS), required=True),
                       _opt("--title", "One-line finding title", required=True), _opt("--body", "Finding body", default=""),
                       _opt("--field", "Typed field KEY=VALUE; repeatable", type=_finding_field,
                            action="append", default=[]),
                       _opt("--task-id", "Associated task id", default=None),
                       _opt("--severity", "Severity label", choices=findings.SEVERITIES, default="info"))),
        _leaf("nyxloomctl", "list", "List recorded findings.", cli.cmd_finding_list,
              fields={"finding_cmd": "list"}, bootstrap=True,
              options=(_opt("--project-id", "Project key or id", default=None),
                       _opt("--kind", "Finding kind", choices=tuple(findings.FINDING_KINDS), default=None))),
    )))
    return registry.build()
