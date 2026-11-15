"""Command line for the fleet facts and planning tools."""

from __future__ import annotations

import argparse
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Sequence

from . import __version__
from .contracts import (
    ContractError,
    dump_json,
    load_json,
    parse_memory_bytes,
    validate_host_facts,
)
from .facts import DEFAULT_ROOT, collect_host_facts
from .guard import parse_guard_toml
from .measurement import load_measurement
from .merge import load_shard_identity, validate_shard_set
from .plan import plan_campaign, read_lane_demand_table, render_commands
from .scope import (
    DEFAULT_SCOPE_MODE,
    EXIT_NOTHING_TO_JUDGE,
    EXIT_OK,
    EXIT_STALE_CURSOR,
    ROLLING_MODE,
    SCOPE_MODES,
    load_scope_file,
    read_cursor,
    refuse_unpinned_checkout,
    release_diff,
    resolve_commit,
    scope_document,
    write_cursor,
)
from .sizing import derive_sizing, sizing_input_from_plan
from .campaign import CampaignManager
from .transport import HxTransport, TransportError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description="Read host facts and create deterministic campaign plans")
    parser.add_argument("--version", action="version", version=f"fleet {__version__}")
    commands = parser.add_subparsers(dest="group", required=True)

    facts = commands.add_parser("facts", help="collect or validate host facts")
    facts_commands = facts.add_subparsers(dest="action", required=True)
    facts_collect = facts_commands.add_parser("collect", help="collect host-facts/1 from /proc, /sys, and control-plane service properties")
    facts_collect.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    facts_collect.set_defaults(handler=_collect_facts)
    facts_validate = facts_commands.add_parser("validate", help="validate a host-facts/1 JSON file")
    facts_validate.add_argument("file", type=Path)
    facts_validate.set_defaults(handler=_validate_facts)

    guard = commands.add_parser("guard", help="validate host-owned guard rules")
    guard_commands = guard.add_subparsers(dest="action", required=True)
    guard_validate = guard_commands.add_parser("validate", help="parse and validate one guard TOML file")
    guard_validate.add_argument("file", type=Path)
    guard_validate.set_defaults(handler=_validate_guard)

    plan = commands.add_parser("plan", help="create a deterministic RAM-first campaign plan")
    plan.add_argument("lane", help="Run-Gate lane name")
    plan.add_argument("--candidates", required=True, type=Path, help="candidate-table/1 JSON file")
    plan.add_argument("--hosts", required=True, type=Path, help="fleet-host-set/1 JSON file")
    plan.add_argument("--demand", required=True, type=Path, help="TOML file containing [lanes.<lane>.demand] (not yet accepted inside run-gate.toml; see RG-276)")
    plan.add_argument("--commands", action="store_true", help="print Run-Gate --only-candidates commands instead of plan JSON")
    plan.add_argument("--batch-multiplier", type=float, default=4.0, help="candidate count per batch as a multiple of host worker slots (default: 4)")
    plan.set_defaults(handler=_plan_campaign)

    scope = commands.add_parser(
        "scope",
        help="produce the R2 candidate-id scope (changed lines since a release by default)",
        description=(
            "Build the candidate-id set an R2 campaign judges. The default "
            "scope is the lines changed since a release tag; the opt-in rolling "
            "baseline walks the whole-target list from a persisted cursor."
        ),
    )
    scope.add_argument("lane", help="Run-Gate lane whose Assay candidates form the whole-target list")
    scope.add_argument("--plan", required=True, type=Path, help="output of `assay plan <lane> --json`")
    scope.add_argument("--repo", type=Path, default=Path("."), help="the Git checkout the plan describes")
    scope.add_argument("--from-tag", default=None, help="previous release tag (required by the changed-lines scope)")
    scope.add_argument("--commit", default=None, help="commit to judge (default: HEAD of --repo)")
    scope.add_argument("--mode", choices=SCOPE_MODES, default=None, help=f"scope mode (default: {DEFAULT_SCOPE_MODE})")
    scope.add_argument("--scope-file", type=Path, default=None, help="project r2 scope file (default: <repo>/r2-scope.toml when present)")
    scope.add_argument("--slice-size", type=int, default=None, help="rolling-whole-target slice size")
    scope.add_argument("--cursor", type=Path, default=None, help="rolling cursor file to resume from")
    scope.add_argument("--write-cursor", type=Path, default=None, help="write the advanced rolling cursor here")
    scope.add_argument("--rebaseline", action="store_true", help="accept a stale rolling cursor and extend its list, recording a new pass")
    scope.add_argument("--out", type=Path, default=None, help="write the candidate-id file here (refused for an empty scope)")
    scope.set_defaults(handler=_scope_campaign)

    sizing = commands.add_parser(
        "sizing",
        help="derive shards, instances per host and budgets from measured units",
    )
    sizing.add_argument("plan", type=Path, help="fleet-plan/1 JSON")
    sizing.add_argument("--measurement", type=Path, default=None, help="fleet-r2-measurement/1 from a finished campaign")
    sizing.add_argument("--per-candidate-seconds", type=float, default=None, help="declared per-candidate wall seconds")
    sizing.add_argument("--per-candidate-memory", type=str, default=None, help="declared per-candidate memory charge")
    sizing.add_argument("--margin", type=float, default=1.10, help="budget margin over the estimate (default: 1.10)")
    sizing.add_argument("--minimum-budget", type=str, default="15m", help="shortest per-instance budget (default: 15m)")
    sizing.add_argument("--startup-allowance", type=str, default="15m", help="outer-bound allowance beside each instance budget (default: 15m)")
    sizing.set_defaults(handler=_sizing_campaign)

    merge = commands.add_parser(
        "merge-check",
        help="refuse a shard merge whose shards judged different commits, images or plans",
    )
    merge.add_argument("identity", type=Path, nargs="+", help="fleet-shard-identity/1 files, one per shard")
    merge.add_argument("--require-complete", action="store_true", help="refuse a shard set with a missing index")
    merge.set_defaults(handler=_merge_check)

    run = commands.add_parser("run", help="start or re-adopt the named jobs in a fleet plan")
    run.add_argument("plan", type=Path)
    run.add_argument("--transport", choices=("hx",), default="hx")
    run.add_argument("--campaign", required=True)
    run.add_argument("--state-dir", type=Path, default=_default_state_dir())
    run.set_defaults(handler=_run_campaign)

    for name, help_text in (
        ("status", "probe campaign progress and ETA"),
        ("tick", "perform one campaign scheduling step"),
        (
            "collect", "fetch artifacts and write combined campaign results",
        ),
        (
            "measurement", "write the per-candidate measurement evidence that sizes the next campaign",
        ),
    ):
        campaign = commands.add_parser(name, help=help_text)
        campaign.add_argument("campaign")
        campaign.add_argument("--transport", choices=("hx",), default="hx")
        campaign.add_argument("--state-dir", type=Path, default=_default_state_dir())
        campaign.set_defaults(handler={
            "status": _status_campaign,
            "tick": _tick_campaign,
            "collect": _collect_campaign,
            "measurement": _measurement_campaign,
        }[name])
    return parser


def _validate_facts(args: argparse.Namespace) -> int:
    validate_host_facts(load_json(args.file))
    print("valid host-facts/1")
    return 0


def _collect_facts(args: argparse.Namespace) -> int:
    print(dump_json(collect_host_facts(args.root)), end="")
    return 0


def _validate_guard(args: argparse.Namespace) -> int:
    parse_guard_toml(args.file.read_text(encoding="utf-8"))
    print("valid guard.toml")
    return 0


def _plan_campaign(args: argparse.Namespace) -> int:
    result = plan_campaign(
        args.lane,
        load_json(args.candidates),
        load_json(args.hosts),
        read_lane_demand_table(args.demand, args.lane),
        batch_multiplier=args.batch_multiplier,
    )
    if args.commands:
        print(render_commands(result), end="")
    else:
        execution = _execution_context()
        if execution is not None:
            result["execution"] = execution
        print(dump_json(result), end="")
    return 0


def _default_state_dir() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "fleetd" / "campaigns"


def _execution_context() -> dict[str, str] | None:
    """Pin a runnable plan only when the current project is a clean Git tree."""
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True)
        prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], check=False, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], check=False, capture_output=True, text=True)
    except OSError:
        return None
    if any(result.returncode != 0 for result in (root, commit, prefix, status)) or status.stdout.strip():
        return None
    repository = Path(root.stdout.strip())
    project = prefix.stdout.strip().rstrip("/") or "."
    project_path = repository if project == "." else repository / project
    if not (project_path / "run-gate.toml").is_file() and not (project_path / "run-gate.root.toml").is_file():
        return None
    return {"schema": "fleet-plan-execution/1", "commit": commit.stdout.strip(), "project": project}


def _manager(args: argparse.Namespace) -> CampaignManager:
    if args.transport != "hx":
        raise ContractError("transport: only the identity-checked hx wrapper is supported")
    return CampaignManager(args.state_dir, HxTransport())


def _run_campaign(args: argparse.Namespace) -> int:
    plan = load_json(args.plan)
    if not isinstance(plan, dict):
        raise ContractError("plan file: expected a JSON object")
    print(dump_json(_manager(args).run(args.campaign, plan)), end="")
    return 0


def _status_campaign(args: argparse.Namespace) -> int:
    print(dump_json(_manager(args).status(args.campaign)), end="")
    return 0


def _tick_campaign(args: argparse.Namespace) -> int:
    print(dump_json(_manager(args).tick(args.campaign)), end="")
    return 0


def _collect_campaign(args: argparse.Namespace) -> int:
    result, _report, json_path, report_path = _manager(args).collect(args.campaign)
    print(dump_json({
        "schema": "fleet-campaign-collection/1",
        "campaign": args.campaign,
        "result": str(json_path),
        "report": str(report_path),
        "totals": result["totals"],
    }), end="")
    return 0


def _scope_campaign(args: argparse.Namespace) -> int:
    repo = Path(os.path.abspath(os.path.expanduser(str(args.repo))))
    commit = args.commit or resolve_commit(repo)
    refuse_unpinned_checkout(repo, commit)
    scope_file = args.scope_file
    if scope_file is None:
        default = repo / "r2-scope.toml"
        scope_file = default if default.is_file() else None
    declared = load_scope_file(scope_file) if scope_file is not None else {
        "mode": None, "slice_size": None, "cursor": None, "note": None, "path": None,
    }
    if args.mode is not None:
        mode, mode_source = args.mode, "--mode"
    elif declared["mode"] is not None:
        mode, mode_source = declared["mode"], f"scope file {declared['path']}"
    else:
        mode, mode_source = DEFAULT_SCOPE_MODE, "default"
    if mode == DEFAULT_SCOPE_MODE and not args.from_tag:
        raise ContractError(
            "--from-tag: the changed-lines scope needs the previous release tag; "
            "there is nothing to compare against without it"
        )
    slice_size = args.slice_size if args.slice_size is not None else declared["slice_size"]
    cursor_path = args.cursor
    if cursor_path is None and declared["cursor"] is not None:
        cursor_path = (repo if Path(declared["cursor"]).is_absolute() else repo / declared["cursor"])
    cursor = read_cursor(cursor_path) if cursor_path is not None else None
    plan = load_json(args.plan)
    diff_text = release_diff(repo, args.from_tag, commit) if mode == DEFAULT_SCOPE_MODE else ""
    document = scope_document(
        mode=mode,
        mode_source=mode_source,
        lane=args.lane,
        from_tag=args.from_tag or "",
        commit=commit,
        plan=plan,
        diff_text=diff_text,
        slice_size=slice_size,
        cursor=cursor,
        rebaseline=args.rebaseline,
    )
    if args.write_cursor is not None:
        if document["cursor"] is None:
            raise ContractError(
                "--write-cursor: only a rolling scope publishes an advanced cursor; "
                f"this scope is {document['status']}"
            )
        write_cursor(args.write_cursor, document["cursor"])
    if args.out is not None:
        if document["status"] != "ok":
            raise ContractError(
                f"--out: refusing to write an empty candidate file; the scope is "
                f"{document['status']}: {document['reason']}"
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            "".join(f"{candidate_id}\n" for candidate_id in document["candidate_ids"]),
            encoding="ascii",
        )
    print(dump_json(document), end="")
    if document["status"] == "stale-cursor":
        return EXIT_STALE_CURSOR
    if document["status"] == "nothing-to-judge":
        return EXIT_NOTHING_TO_JUDGE
    return EXIT_OK


def _sizing_campaign(args: argparse.Namespace) -> int:
    plan = load_json(args.plan)
    measurement = load_measurement(args.measurement) if args.measurement is not None else None
    sizing = sizing_input_from_plan(
        plan,
        measurement=measurement,
        per_candidate_seconds=args.per_candidate_seconds,
        per_candidate_memory_bytes=(
            parse_memory_bytes(args.per_candidate_memory, "--per-candidate-memory")
            if args.per_candidate_memory is not None else None
        ),
    )
    limit = None
    allowance = None
    for value, name in (
        (args.minimum_budget, "--minimum-budget"),
        (args.startup_allowance, "--startup-allowance"),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+[smh]", value):
            raise ContractError(f"{name}: expected a duration like 15m or 1h")
        seconds = int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]
        if name == "--minimum-budget":
            limit = seconds
        else:
            allowance = seconds
    document = derive_sizing(
        sizing,
        margin=args.margin,
        minimum_budget_seconds=limit,
        startup_allowance_seconds=allowance,
    )
    print(dump_json(document), end="")
    return 0


def _merge_check(args: argparse.Namespace) -> int:
    identities = [load_shard_identity(path) for path in args.identity]
    document = validate_shard_set(identities, require_complete=args.require_complete)
    print(dump_json(document), end="")
    return 0 if document["status"] == "ok" else EXIT_STALE_CURSOR


def _measurement_campaign(args: argparse.Namespace) -> int:
    print(dump_json(_manager(args).measurement(args.campaign)), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ContractError, OSError, UnicodeError, OverflowError, TransportError) as exc:
        # UnicodeError: a non-UTF-8 guard file. OverflowError: an integer too large
        # to convert for planner arithmetic. Both are bad input, not a crash.
        print(f"fleet: ERROR {exc}", file=sys.stderr)
        return 2
