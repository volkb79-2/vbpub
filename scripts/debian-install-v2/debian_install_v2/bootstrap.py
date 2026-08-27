from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .actions import HostActions
from .config import Config, ConfigError, load_config
from .installer import Installer
from .state import StateError, StateStore


PROG = "debian-install-v2"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG)
    parser.add_argument(
        "--action",
        choices=("install", "resume", "status", "verify", "disable-stage2", "show-plan"),
        required=True,
    )
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument("--config", metavar="FILE")
    config_group.add_argument("--config-json", metavar="JSON")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _stage2_config(state_dir: str) -> Config:
    manifest = StateStore(state_dir).load()
    saved = manifest.get("config")
    if not isinstance(saved, dict):
        raise StateError("state manifest does not contain a configuration object")
    allowed = {name for name in Config.__dataclass_fields__ if name in saved}
    return Config(**{name: saved[name] for name in allowed})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "resume":
            state_dir = os.environ.get("VBPUB_STATE_DIR", "")
            if not state_dir.startswith("/"):
                raise StateError("VBPUB_STATE_DIR must be set to an absolute path for resume")
            config = _stage2_config(state_dir)
        else:
            if not (args.config or args.config_json):
                raise ConfigError(f"--action {args.action} requires --config FILE or --config-json JSON")
            config = load_config(args.config, args.config_json)
    except (ConfigError, StateError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    actions = HostActions(dry_run=args.dry_run)
    installer = Installer(config, actions)
    try:
        if args.action == "status":
            print(json.dumps(installer.status(), indent=2, sort_keys=True))
            return 0
        if args.action == "verify":
            installer.verify()
            return 0
        if args.action == "disable-stage2":
            installer.disable_stage2()
            return 0
        if args.action == "show-plan":
            print(json.dumps(installer.show_plan(), indent=2, sort_keys=True))
            return 0
        if args.action == "install":
            installer.install()
        elif args.action == "resume":
            installer.resume()
    except Exception as exc:
        print(f"{PROG}: action failed: {exc}", file=sys.stderr)
        if actions.dry_run:
            print(json.dumps([action.__dict__ for action in actions.planned], indent=2), file=sys.stderr)
        return 1
    if args.dry_run and args.action == "install":
        print(json.dumps({
            "result": "planned",
            "actions": [action.__dict__ for action in actions.planned],
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
