"""cmru-controller CLI entry point (spec §4 / §7).

Verbs:
  publish   — write desired state wave by wave, gate on wave barriers.
  approve   — approve production waves for a plan.
  hold      — pause a plan.
  status    — render catalog (registered/standby/assigned) + observed.
  rollback  — write a new desired generation with action=rollback.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from cli_extended import CliRegistry, OptionSpec, VerbGroup, VerbSpec

from cmru.cli_support import cmru_identity


log = logging.getLogger("cmru.controller")


def _positive_generation(value: str) -> int:
    """Argparse type for a generation coordinate that cannot be zero or negative."""
    parsed = int(value)
    if parsed < 1:
        raise ValueError("generation must be a positive integer")
    return parsed


def _build_backend(args):
    from cmru.agent.consul_backend import ConsulBackend
    consul_addr = (
        getattr(args, "consul_addr", None)
        or os.environ.get("CONSUL_HTTP_ADDR", "http://127.0.0.1:8500")
    )
    token = os.environ.get("CONSUL_HTTP_TOKEN", "") or getattr(args, "token", None)
    return ConsulBackend(consul_addr=consul_addr, token=token or None)


def _build_engine(args, landscape: str):
    from cmru.controller.rollout import RolloutEngine
    backend = _build_backend(args)
    return RolloutEngine(
        backend=backend,
        landscape=landscape,
        generation_base=getattr(args, "generation_base", 1),
        dry_run=args.dry_run,
    )


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def cmd_publish(args) -> int:
    """Parse plan file and publish to Consul KV wave by wave."""
    from cmru.controller.planner import load_plan
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"[ERROR] Plan file not found: {plan_path}", file=sys.stderr)
        return 2

    try:
        plan = load_plan(plan_path)
    except (ValueError, Exception) as exc:
        print(f"[ERROR] Failed to load plan: {exc}", file=sys.stderr)
        return 2

    landscape = args.landscape or plan.landscape
    if not landscape:
        print("[ERROR] --landscape required (or set in plan)", file=sys.stderr)
        return 2

    engine = _build_engine(args, landscape)
    try:
        engine.publish(plan)
    except Exception as exc:
        print(f"[ERROR] Publish failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_approve(args) -> int:
    """Approve production waves for a plan."""
    plan_id = args.plan
    if not plan_id:
        print("[ERROR] --plan required", file=sys.stderr)
        return 2
    landscape = args.landscape or ""
    engine = _build_engine(args, landscape)
    try:
        engine.approve(plan_id)
    except Exception as exc:
        print(f"[ERROR] Approve failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_hold(args) -> int:
    """Hold (pause) a plan."""
    plan_id = args.plan
    if not plan_id:
        print("[ERROR] --plan required", file=sys.stderr)
        return 2
    landscape = args.landscape or ""
    engine = _build_engine(args, landscape)
    try:
        engine.hold(plan_id)
    except Exception as exc:
        print(f"[ERROR] Hold failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_status(args) -> int:
    """Render observed state for all registered nodes in a plan."""
    from cmru.controller.planner import load_plan
    plan_path = Path(args.plan) if args.plan else None
    landscape = args.landscape or ""

    if plan_path and plan_path.exists():
        try:
            plan = load_plan(plan_path)
            landscape = landscape or plan.landscape
        except Exception as exc:
            print(f"[ERROR] Failed to load plan: {exc}", file=sys.stderr)
            return 2

        engine = _build_engine(args, landscape)
        try:
            result = engine.status(plan)
        except Exception as exc:
            print(f"[ERROR] Status failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
    else:
        if not landscape:
            print("[ERROR] --landscape required when --plan is not a file path", file=sys.stderr)
            return 2
        backend = _build_backend(args)
        # List registered cmru-agent services
        try:
            status, body, _ = backend._get("/v1/catalog/service/cmru-agent")
        except Exception as exc:
            print(f"[ERROR] Consul unavailable: {exc}", file=sys.stderr)
            return 1
        if status == 200:
            try:
                services = json.loads(body)
                print(f"Registered cmru-agent nodes ({len(services)}):")
                for svc in services:
                    print(f"  {svc.get('Node', '?')} ({svc.get('ServiceTags', [])})")
            except json.JSONDecodeError:
                print(f"[WARN] Could not parse service catalog: {body[:200]}")
        else:
            print(f"[WARN] Consul returned HTTP {status}")
    return 0


def cmd_rollback(args) -> int:
    """Write a new desired generation with action=rollback."""
    from cmru.controller.planner import load_plan
    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"[ERROR] Plan file not found: {plan_path}", file=sys.stderr)
        return 2

    try:
        plan = load_plan(plan_path)
    except Exception as exc:
        print(f"[ERROR] Failed to load plan: {exc}", file=sys.stderr)
        return 2

    landscape = args.landscape or plan.landscape
    engine = _build_engine(args, landscape)
    try:
        engine.rollback(
            plan,
            generation=getattr(args, "generation", None),
        )
    except Exception as exc:
        print(f"[ERROR] Rollback failed: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_cli():
    identity = cmru_identity(
        command="cmru-controller",
        long_name="CMRU controller — assign desired state and orchestrate rollout waves",
    )
    global_options = (
        OptionSpec(("--landscape",), "landscape name (can also be set in plan file)", metavar="NAME"),
        OptionSpec(
            ("--consul-addr",),
            "Consul HTTP address (default: $CONSUL_HTTP_ADDR or http://127.0.0.1:8500)",
            metavar="URL",
        ),
        OptionSpec(
            ("--token",), "Consul ACL token (prefer $CONSUL_HTTP_TOKEN in production)",
            metavar="TOKEN",
        ),
    )
    registry = CliRegistry(
        identity,
        prog="cmru-controller",
        description="CMRU controller command line.",
        global_options=global_options,
        logging_logger="cmru.controller",
    )
    common = {"include_json": False, "include_progress": False}
    specs = (
        ("publish", "Publish desired state from a plan file.", cmd_publish, (
            OptionSpec(("--plan",), "path to plan TOML file", metavar="PLAN_TOML", parser_kwargs={"required": True}),
            OptionSpec(("--generation-base",), "positive base generation number (default: 1)", metavar="N", parser_kwargs={"type": _positive_generation, "default": 1}),
        )),
        ("approve", "Approve production waves for a plan.", cmd_approve, (
            OptionSpec(("--plan",), "plan ID to approve", metavar="PLAN_ID", parser_kwargs={"required": True}),
        )),
        ("hold", "Pause a plan.", cmd_hold, (
            OptionSpec(("--plan",), "plan ID to pause", metavar="PLAN_ID", parser_kwargs={"required": True}),
        )),
        ("status", "Show observed state for plan nodes.", cmd_status, (
            OptionSpec(("--plan",), "plan TOML path or plan ID", metavar="PLAN_TOML_OR_ID", parser_kwargs={"default": None}),
        )),
        ("rollback", "Write a new rollback desired generation.", cmd_rollback, (
            OptionSpec(("--plan",), "path to plan TOML file", metavar="PLAN_TOML", parser_kwargs={"required": True}),
            OptionSpec(("--generation",), "positive rollback generation number", metavar="N", parser_kwargs={"type": _positive_generation, "default": None}),
        )),
    )
    for name, description, handler, options in specs:
        if name != "status":
            options = options + (OptionSpec(
                ("--dry-run",), "show planned Consul changes without writing them",
                parser_kwargs={"action": "store_true", "default": False},
            ),)
        registry.register(VerbSpec(
            name, description=description,
            group=VerbGroup.EXPLORATION.value if name == "status" else VerbGroup.MODIFICATION.value,
            mutating=name != "status", include_confirmation=False,
            options=options,
            handler=lambda args, _runtime, fn=handler: fn(args), **common,
        ))
    return registry.build()


def _build_parser():
    """Expose the generated parser for contract tests and embedders."""
    return _build_cli().parser


def main(argv=None) -> int:
    return _build_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit("Use the installed 'cmru-controller' command; its module alias is not supported.")
