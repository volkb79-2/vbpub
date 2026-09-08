---
kind: backlog-entry
schema_version: 1
id: NL-16
title: "long-term: split the nyxloom wheel into a lean CLI/dev-tooling package and a separate full-product (daemon) release"
status: open
type: "feature"
severity: "low"
component: "release"
provenance: "operator request, nyxloom-P104 cmru-adoption, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

nyxloom-P104 (2026-09-08) adopted cmru release orchestration and shipped
`nyxloom-v0.4.0` as a single wheel containing the ENTIRE `nyxloom` package
-- CLI (`cli.py`, the lint/backlog/registry/onboarding surface actually
used today), daemon (`daemon.py`, reconcile/effects/stages, currently
offline with no running consumer), and everything the daemon needs
(routing, workflow_ir/compile, gap-engine, etc.).

`nyxloom/build-push.py` ALSO exists (currently disabled for release, see
NL-15) to package the daemon as OCI images (`nyxloomd` + `nyxloom-agent-cli`)
for deployment -- a second, heavier distribution shape for the SAME
codebase, aimed at actually running the control-plane daemon rather than
just using its CLI as a dev tool.

This means today there is exactly ONE Python distribution artifact
(`nyxloom-*.whl`) serving two very different consumers:
1. A lightweight dev-tool consumer (mdt, or any human/CI running `nyxloom
   lint`/`nyxloom backlog ...`) that never touches the daemon at all.
2. A full-product consumer (someone actually deploying `nyxloomd` and
   letting it run the autonomous carve/review/merge pipeline) that needs
   everything.

Operator, while unwinding a botched first release (2026-09-08), asked to
"consider we need in the long run separate release for the client CLI
wheel and nyxloom as product with the daemon (maybe plus bundled CLI)."

## Why nyxloom owns it

This is nyxloom's own packaging/distribution boundary
(`pyproject.toml`, `cli.py` vs `daemon.py`/`effects_*.py`/`reconcile.py`
module split, `cmru.toml`'s release contract).

## Proposed contract

Not designed here -- this entry exists to TRACK the decision, not make
it. Sketch of the shape a future design pass should evaluate, based on
the operator's own framing:

1. **A lean `nyxloom-cli` (or similarly named) wheel** -- the actual
   dev-tooling surface (lint, backlog, registry, doctor, onboard, route
   doctor, free-models, capability-map) with minimal dependencies. This
   is what mdt and any CI/human dev-tool consumer actually wants; it is
   ALSO the thing this session's own carve/review/merge pipeline has
   used constantly, and it doesn't need daemon.py/effects_*.py/reconcile.py
   at all.
2. **A `nyxloom` (product) wheel or OCI image** carrying the full daemon
   -- the resident control-plane product, which per the CLI's own
   description IS meant to eventually run autonomously. Whether this
   bundles the CLI too (so a deployed daemon environment also has `nyxloom
   lint` available) or depends on the CLI wheel as a dependency is exactly
   the open design question the operator flagged ("maybe plus bundled
   CLI").
3. Given nyxloom is currently OFFLINE (no forcing function to actually
   deploy the daemon product today -- same reasoning NL-15 uses to defer
   OCI publishing), this split is speculative work with no current
   consumer forcing it. Consistent with this session's own standing
   discipline (do not build ahead of a forcing function), do NOT attempt
   this split until either (a) something actually needs to deploy
   nyxloomd, or (b) the single combined wheel is found to cause a real
   problem for the CLI-only consumer (e.g. dependency bloat, a daemon-only
   dependency breaking the CLI install).

## Oracles

Not applicable yet -- this is a design-tracking entry, not an
implementation-ready contract. Whoever picks this up should re-derive
oracles once the actual split shape is chosen.

## SPEC ownership

`pyproject.toml`, `cmru.toml`, the `cli.py`/`daemon.py` module boundary,
`docs/ARCHITECTURE.md`.

## Provenance

Operator request during nyxloom-P104's cmru-adoption work, 2026-09-08,
while unwinding a mistaken nyxloom-v1.0.0 release (a separate incident --
see nyxloom-trove/reports/nyxloom-CMRU-ADOPTION-CODE-REVIEW.md and the
git history around commits 928b7fc2..9ec8d935 for that thread).
