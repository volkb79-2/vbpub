"""cmru-controller CLI entry point (spec §4 / §7).

Verbs:
  publish   — write desired state wave by wave, gate on wave barriers.
  approve   — approve production waves for a plan.
  hold      — pause a plan.
  status    — render catalog (registered/standby/assigned) + observed.
  rollback  — write a new desired generation with action=rollback.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


log = logging.getLogger("cmru.controller")


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


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
        dry_run=getattr(args, "dry_run", False),
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
            to_tag=getattr(args, "to_tag", None),
            generation=getattr(args, "generation", None),
        )
    except Exception as exc:
        print(f"[ERROR] Rollback failed: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cmru-controller",
        description="CMRU controller — assign desired state and orchestrate rollout waves",
    )
    parser.add_argument(
        "--landscape", default=None,
        help="Landscape name (can also be set in plan file)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--consul-addr", default=None,
        help="Consul HTTP address (default: $CONSUL_HTTP_ADDR or http://127.0.0.1:8500)",
    )
    parser.add_argument(
        "--token", default=None,
        help="Consul ACL token (prefer $CONSUL_HTTP_TOKEN in production)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show actions without writing to Consul",
    )

    sub = parser.add_subparsers(dest="verb", required=True)

    # publish
    p_pub = sub.add_parser("publish", help="Publish desired state from a plan file")
    p_pub.add_argument("--plan", required=True, metavar="PLAN_TOML",
                       help="Path to plan TOML file")
    p_pub.add_argument("--generation-base", dest="generation_base", type=int, default=1,
                       help="Base generation number (default: 1)")

    # approve
    p_approve = sub.add_parser("approve", help="Approve production waves for a plan")
    p_approve.add_argument("--plan", required=True, metavar="PLAN_ID",
                           help="Plan ID to approve")

    # hold
    p_hold = sub.add_parser("hold", help="Pause a plan")
    p_hold.add_argument("--plan", required=True, metavar="PLAN_ID")

    # status
    p_status = sub.add_parser("status", help="Show observed state for plan nodes")
    p_status.add_argument("--plan", default=None, metavar="PLAN_TOML_OR_ID")

    # rollback
    p_rollback = sub.add_parser("rollback", help="Write a new rollback desired generation")
    p_rollback.add_argument("--plan", required=True, metavar="PLAN_TOML")
    p_rollback.add_argument("--to", dest="to_tag", default=None,
                            help="Roll back to this release tag")
    p_rollback.add_argument("--generation", type=int, default=None,
                            help="Override rollback generation number")

    return parser


def main(argv=None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.log_level)

    dispatch = {
        "publish": cmd_publish,
        "approve": cmd_approve,
        "hold": cmd_hold,
        "status": cmd_status,
        "rollback": cmd_rollback,
    }
    fn = dispatch.get(args.verb)
    if fn is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(fn(args))


if __name__ == "__main__":
    main()
