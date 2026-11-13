"""Command line for the fleet facts and planning tools."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .contracts import ContractError, dump_json, load_json, validate_host_facts
from .facts import DEFAULT_ROOT, collect_host_facts
from .guard import parse_guard_toml
from .plan import plan_campaign, read_lane_demand_table, render_commands


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
        print(dump_json(result), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ContractError, OSError, UnicodeError, OverflowError) as exc:
        # UnicodeError: a non-UTF-8 guard file. OverflowError: an integer too large
        # to convert for planner arithmetic. Both are bad input, not a crash.
        print(f"fleet: ERROR {exc}", file=sys.stderr)
        return 2
