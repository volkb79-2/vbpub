"""Command line for the fleet facts and planning tools."""

from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path
from typing import Sequence

from . import __version__
from .contracts import ContractError, dump_json, load_json, validate_host_facts
from .facts import DEFAULT_ROOT, collect_host_facts
from .guard import parse_guard_toml
from .plan import plan_campaign, read_lane_demand_table, render_commands
from .campaign import CampaignManager
from .transport import HxTransport, TransportError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description="Read host facts and create deterministic campaign plans")
    parser.add_argument("--version", action="version", version=f"fleet {__version__}")
    commands = parser.add_subparsers(dest="group", required=True)

    facts = commands.add_parser("facts", help="collect or validate host facts")
    facts_commands = facts.add_subparsers(dest="action", required=True)
    facts_collect = facts_commands.add_parser("collect", help="collect host-facts/1 from /proc and /sys")
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

    run = commands.add_parser("run", help="start or re-adopt the named jobs in a fleet plan")
    run.add_argument("plan", type=Path)
    run.add_argument("--transport", choices=("hx",), default="hx")
    run.add_argument("--campaign", required=True)
    run.add_argument("--state-dir", type=Path, default=_default_state_dir())
    run.set_defaults(handler=_run_campaign)

    for name, help_text in (
        ("status", "probe campaign progress and ETA"),
        ("tick", "perform one campaign scheduling step"),
        ("collect", "fetch artifacts and write combined campaign results"),
    ):
        campaign = commands.add_parser(name, help=help_text)
        campaign.add_argument("campaign")
        campaign.add_argument("--transport", choices=("hx",), default="hx")
        campaign.add_argument("--state-dir", type=Path, default=_default_state_dir())
        campaign.set_defaults(handler={"status": _status_campaign, "tick": _tick_campaign, "collect": _collect_campaign}[name])
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
