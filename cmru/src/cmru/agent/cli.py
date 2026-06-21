"""cmru-agent CLI entry point (spec §4).

Verbs:
  enroll  — register the node with the backend; persist node_id + identity.
  run     — long-running reconcile loop (the daemon).
  once    — single reconcile pass then exit (for tests / cron fallback).
  status  — print current observed state + last applied generation.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional


log = logging.getLogger("cmru.agent")


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


def _build_backend(args):
    """Build a ConsulBackend from CLI args / environment."""
    from cmru.agent.consul_backend import ConsulBackend
    consul_addr = (
        getattr(args, "consul_addr", None)
        or os.environ.get("CONSUL_HTTP_ADDR", "http://127.0.0.1:8500")
    )
    # Token: prefer env; --token arg is for testing only (not for prod secrets)
    token = os.environ.get("CONSUL_HTTP_TOKEN", "") or getattr(args, "token", None)
    # NEVER log the token value
    return ConsulBackend(consul_addr=consul_addr, token=token or None)


def _load_identity(scope: str):
    """Load persisted NodeIdentity from state dir; exit 2 if missing."""
    from cmru.agent.state import read_node_id, read_identity
    node_id = read_node_id(scope)
    if not node_id:
        print(
            f"[ERROR] No node_id found in state dir ({scope} scope). "
            "Run 'cmru-agent enroll' first.",
            file=sys.stderr,
        )
        sys.exit(2)
    identity_data = read_identity(scope)
    return node_id, identity_data


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def cmd_enroll(args) -> int:
    """Enroll this node with the Consul backend."""
    from cmru.agent.backend import EnrollmentSeed
    from cmru.agent.consul_backend import ConsulBackend
    from cmru.agent.state import (
        ensure_state_dir, write_node_id, write_identity, write_observed
    )
    from cmru.agent.protocol import ObservedState

    # Resolve seed from args / env — NEVER commit tokens
    node_id = args.node_id or os.environ.get("CMRU_NODE_ID", "")
    landscape = args.landscape or os.environ.get("CMRU_LANDSCAPE", "")
    consul_token = os.environ.get("CMRU_CONSUL_TOKEN", "") or getattr(args, "token", "")
    minisign_pubkey = (
        getattr(args, "minisign_pubkey", None)
        or os.environ.get("CMRU_MINISIGN_PUBKEY", "")
    )

    if not node_id:
        print("[ERROR] --node-id / CMRU_NODE_ID required", file=sys.stderr)
        return 2
    if not landscape:
        print("[ERROR] --landscape / CMRU_LANDSCAPE required", file=sys.stderr)
        return 2

    seed = EnrollmentSeed(
        node_id=node_id,
        landscape=landscape,
        consul_token=consul_token,
        minisign_pubkey=minisign_pubkey,
    )

    backend = _build_backend(args)
    # Use provisioning token for enrollment
    backend._token = consul_token or backend._token  # type: ignore[attr-defined]

    try:
        identity = backend.enroll(seed)
    except Exception as exc:
        print(f"[ERROR] Enrollment failed: {exc}", file=sys.stderr)
        return 1

    scope = args.scope
    ensure_state_dir(scope)
    write_node_id(identity.node_id, scope)
    write_identity({
        "node_id": identity.node_id,
        "landscape": identity.landscape,
        "token_path": identity.token_path,
        "public_key": identity.public_key,
    }, scope)

    print(f"[INFO] Enrolled: node_id={identity.node_id} landscape={identity.landscape}")
    return 0


def cmd_run(args) -> int:
    """Long-running reconcile loop."""
    from cmru.agent.reconciler import Reconciler

    node_id, identity_data = _load_identity(args.scope)
    landscape = (identity_data or {}).get("landscape", "") or os.environ.get("CMRU_LANDSCAPE", "")
    pubkey = (identity_data or {}).get("public_key", "")

    if not landscape:
        print("[ERROR] landscape not found in identity — re-enroll or set CMRU_LANDSCAPE",
              file=sys.stderr)
        return 2

    backend = _build_backend(args)
    release_root = Path(args.release_root) if getattr(args, "release_root", None) else None

    reconciler = Reconciler(
        backend=backend,
        node_id=node_id,
        landscape=landscape,
        scope=args.scope,
        release_root=release_root,
        minisign_pubkey=pubkey,
    )
    reconciler.run()
    return 0


def cmd_once(args) -> int:
    """Single reconcile pass then exit."""
    from cmru.agent.reconciler import Reconciler

    node_id, identity_data = _load_identity(args.scope)
    landscape = (identity_data or {}).get("landscape", "") or os.environ.get("CMRU_LANDSCAPE", "")
    pubkey = (identity_data or {}).get("public_key", "")

    if not landscape:
        print("[ERROR] landscape not found in identity — re-enroll or set CMRU_LANDSCAPE",
              file=sys.stderr)
        return 2

    backend = _build_backend(args)
    release_root = Path(args.release_root) if getattr(args, "release_root", None) else None

    reconciler = Reconciler(
        backend=backend,
        node_id=node_id,
        landscape=landscape,
        scope=args.scope,
        release_root=release_root,
        minisign_pubkey=pubkey,
        max_iterations=1,
    )
    applied = reconciler.once()
    print(f"[INFO] once: {'change applied' if applied else 'no change'}")
    return 0


def cmd_status(args) -> int:
    """Print current observed state + last applied generation."""
    from cmru.agent.state import read_node_id, read_observed, read_current_generation

    scope = args.scope
    node_id = read_node_id(scope)
    observed = read_observed(scope)
    generation = read_current_generation(scope)

    print(f"node_id:            {node_id or '(not enrolled)'}")
    print(f"current_generation: {generation if generation is not None else '(none)'}")
    if observed:
        print(f"health:             {observed.health}")
        print(f"applied_generation: {observed.applied_generation}")
        print(f"adapter_phase:      {observed.adapter_phase}")
        print(f"release_digest:     {observed.release_digest}")
        if observed.error_class:
            print(f"error_class:        {observed.error_class}")
        if observed.started_at:
            print(f"started_at:         {observed.started_at}")
        if observed.finished_at:
            print(f"finished_at:        {observed.finished_at}")
    else:
        print("observed:           (none)")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cmru-agent",
        description="CMRU reconciler agent — converges host to declared desired state",
    )
    parser.add_argument(
        "--scope", choices=["system", "user"], default="user",
        help="State directory scope (default: user)",
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

    sub = parser.add_subparsers(dest="verb", required=True)

    # enroll
    p_enroll = sub.add_parser("enroll", help="Register this node with the backend")
    p_enroll.add_argument("--node-id", dest="node_id", default=None)
    p_enroll.add_argument("--landscape", default=None)
    p_enroll.add_argument("--minisign-pubkey", dest="minisign_pubkey", default=None)

    # run
    p_run = sub.add_parser("run", help="Long-running reconcile loop (daemon)")
    p_run.add_argument("--release-root", dest="release_root", default=None)

    # once
    p_once = sub.add_parser("once", help="Single reconcile pass then exit")
    p_once.add_argument("--release-root", dest="release_root", default=None)

    # status
    sub.add_parser("status", help="Print current observed state + last applied generation")

    return parser


def main(argv=None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.log_level)

    dispatch = {
        "enroll": cmd_enroll,
        "run": cmd_run,
        "once": cmd_once,
        "status": cmd_status,
    }
    fn = dispatch.get(args.verb)
    if fn is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(fn(args))


if __name__ == "__main__":
    main()
