"""Command line for the fleet facts and planning tools."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .contracts import ContractError, dump_json, load_json, validate_assay_merge_manifest, validate_host_facts
from .guard import parse_guard_toml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description="Read host facts and create deterministic campaign plans")
    parser.add_argument("--version", action="version", version=f"fleet {__version__}")
    commands = parser.add_subparsers(dest="group", required=True)

    facts = commands.add_parser("facts", help="collect or validate host facts")
    facts_commands = facts.add_subparsers(dest="action", required=True)
    facts_validate = facts_commands.add_parser("validate", help="validate a host-facts/1 JSON file")
    facts_validate.add_argument("file", type=Path)
    facts_validate.set_defaults(handler=_validate_facts)

    guard = commands.add_parser("guard", help="validate host-owned guard rules")
    guard_commands = guard.add_subparsers(dest="action", required=True)
    guard_validate = guard_commands.add_parser("validate", help="parse and validate one guard TOML file")
    guard_validate.add_argument("file", type=Path)
    guard_validate.set_defaults(handler=_validate_guard)
    return parser


def _validate_facts(args: argparse.Namespace) -> int:
    validate_host_facts(load_json(args.file))
    print("valid host-facts/1")
    return 0


def _validate_guard(args: argparse.Namespace) -> int:
    parse_guard_toml(args.file.read_text(encoding="utf-8"))
    print("valid guard.toml")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ContractError, OSError) as exc:
        print(f"fleet: ERROR {exc}", file=sys.stderr)
        return 2
