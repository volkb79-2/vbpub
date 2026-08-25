"""CIU's shipped hook template library (ciu-P20, CIU-QOL-13).

Each sibling module here is a copyable STARTING POINT, not a package a
consumer imports directly: `ciu init --hooks NAME` (`scaffold.py`) copies one
module's file verbatim into a target stack directory (`<stack_dir>/hooks/
<name>.py`), prefixed with a one-line stamp comment naming the template and
the `template_revision` it was copied at (see docs/SPEC.md S19.1 for the
exact format). The copy is then loaded and run like any other hook (S9) —
nothing about the S9.1/S9.2/S9.4/S9.5 contract changes because a hook file
happened to originate from this library.

Every module here exposes:
  - `template_revision: int` — starts at 1, incremented on every behavioral
    change to that module's `run`/`validate_config` bodies (see each
    module's own docstring for the exact rule).
  - `run(config, ctx) -> dict` — the ordinary S9.1 hook entry point.
  - optionally `validate_config(config, ctx) -> list[str]` — the S9.5
    preflight contract.

This directory intentionally ships ONE reference template
(`post_compose_db.py`). See `nyxloom-trove/reports/
ciu-P20-hook-template-library-LOG.md` for why the backlog's other named
templates (Vault/Consul/Redis/Authentik/Tailscale) are not shipped here yet.
"""
from __future__ import annotations
