"""cmru-agent CLI entry point (spec §4).

Verbs:
  enroll  — register the node with the backend; persist node_id + identity.
  run     — long-running reconcile loop (the daemon).
  once    — single reconcile pass then exit (for tests / cron fallback).
  status  — print current observed state + last applied generation.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from cli_extended import CliRegistry, OptionSpec, VerbGroup, VerbSpec

from cmru.cli_support import cmru_identity


log = logging.getLogger("cmru.agent")


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

def _build_cli():
    identity = cmru_identity(
        command="cmru-agent",
        long_name="CMRU reconciler agent — converges host to declared desired state",
    )
    global_options = (
        OptionSpec(
            ("--scope",), "state directory scope", group="AGENT", metavar="SCOPE",
            parser_kwargs={"choices": ("system", "user"), "default": "user"},
        ),
        OptionSpec(
            ("--consul-addr",),
            "Consul HTTP address (default: $CONSUL_HTTP_ADDR or http://127.0.0.1:8500)",
            group="BACKEND", metavar="URL",
        ),
        OptionSpec(
            ("--token",), "Consul ACL token (prefer $CONSUL_HTTP_TOKEN in production)",
            group="BACKEND", metavar="TOKEN",
        ),
    )
    registry = CliRegistry(
        identity,
        prog="cmru-agent",
        description="CMRU agent command line.",
        global_options=global_options,
        logging_logger="cmru.agent",
    )
    common = {"include_json": False, "include_progress": False}
    registry.register(VerbSpec(
        "enroll", description="Register this node with the backend.",
        group=VerbGroup.AUTHENTICATION.value,
        mutating=True, include_confirmation=False,
        options=(
            OptionSpec(("--node-id",), "node identity (or CMRU_NODE_ID)", metavar="ID", parser_kwargs={"default": None}),
            OptionSpec(("--landscape",), "landscape name (or CMRU_LANDSCAPE)", metavar="NAME", parser_kwargs={"default": None}),
            OptionSpec(("--minisign-pubkey",), "installer verification public key", metavar="KEY", parser_kwargs={"default": None}),
        ), handler=lambda args, _runtime: cmd_enroll(args), **common,
    ))
    for name, description, handler in (
        ("run", "Run the long-lived reconcile loop.", cmd_run),
        ("once", "Run one reconciliation pass and exit.", cmd_once),
    ):
        registry.register(VerbSpec(
            name, description=description, group=VerbGroup.MODIFICATION.value,
            mutating=True, include_confirmation=False,
            options=(OptionSpec(("--release-root",), "override the release root", metavar="DIR", parser_kwargs={"default": None}),),
            handler=lambda args, _runtime, fn=handler: fn(args), **common,
        ))
    registry.register(VerbSpec(
        "status", description="Print current observed state and last applied generation.",
        group=VerbGroup.EXPLORATION.value,
        handler=lambda args, _runtime: cmd_status(args), **common,
    ))
    return registry.build()


def _build_parser():
    """Expose the generated parser for contract tests and embedders."""
    return _build_cli().parser


def main(argv=None) -> int:
    return _build_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
