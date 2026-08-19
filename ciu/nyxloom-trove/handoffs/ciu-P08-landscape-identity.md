---
schema_version: 1
id: ciu-P08-landscape-identity
project: ciu
component: config
title: "Adopt [deploy] landscape_id as a documented, validated first-class identity key"
tier: implement-2
input_revision: "0b920f806b4aedcc12014ebb028b917858450de0"
source: {kind: backlog, ref: "KNOWN_ISSUES_TODO_BACKLOG.md#CIU-36"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/config_model.py"
    - "tests/tests/test_ciu_config_model_landscape.py"
    - "docs/CONFIG.md"
    - "docs/SPEC.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P08-landscape-identity-LOG.md"
  forbid:
    - "src/ciu/workspace_env.py"
    - "src/ciu/worktree.py"
    - "src/ciu/composefile.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-key-validated
    observable: "render_global_chain (src/ciu/config_model.py:401-478) validates, after the final merge: if [deploy].landscape_id is present it MUST match ^[a-z][a-z0-9-]{0,62}$ (a DNS-label-safe slug); a violating value fails the render with a tagged error naming the key and the pattern. Absence is legal (the key is consumer-opt-in). A passing and a failing template fixture prove both directions."
    negative: "validation that only warns; validation running per-directory instead of on the final merged value; a regex accepting uppercase or leading digit/dash"
    gate: "tester-unified"
  - id: O2-template-reach
    observable: "A test renders a stack TOML template referencing {{ deploy.landscape_id }} through render_toml_template and receives the declared value — proving the existing context plumbing needs no change and pinning it against regression."
    negative: "the test bypassing _make_render_context"
    gate: "tester-unified"
  - id: O3-docs
    observable: "docs/CONFIG.md's [deploy] subtable table documents landscape_id (purpose: shared-landscape identity for consumer KV roots / mesh ACL tags; format; opt-in), and explicitly warns about the EXISTING configfile-context name `instance_id` (composefile.py render_configfiles context) being a per-service replica index, NOT the workspace INSTANCE_ID and NOT landscape-scoped. docs/SPEC.md gains the normative clause with an S-number following the local numbering convention. CHANGES.md entry; KNOWN_ISSUES CIU-36 row → FIXED with evidence."
    negative: "docs added without the instance_id disambiguation warning"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "The reserved-roots frozenset (config_model.py:55-68) or validate_stack_shape rejects the key in a way that cannot be fixed inside config_model.py alone — BLOCKED naming the constraint"
mutexes: [merge-lane]
review_focus:
  - "validation placement: final merged config, once, not per chain directory"
  - "no scope creep into ciu.env / worktree carry (explicitly deferred — see body)"
---

# ciu-P08 — `[deploy] landscape_id`

## Context to read first
1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-36` — the filed ask (consumer: dstdns renders its Consul
   KV root `dstdns/<landscape_id>/…` from it).
2. `src/ciu/config_model.py:325-338` (`_make_render_context`) and `:401-478`
   (`render_global_chain`) — the value already reaches templates via the merged dict; this
   package only reserves, validates, and documents it.
3. `src/ciu/composefile.py:569-576` — the configfile context's per-replica `instance_id`
   (the naming hazard O3 documents).

## Dispatch contract
Smallest-possible adoption: **validate + document, change no plumbing.** The consumer declares
`[deploy] landscape_id = "prod-eu"` in its own `ciu.global.toml(.j2)`; templates read
`{{ deploy.landscape_id }}`. Deliberately DEFERRED (do not build): emitting `LANDSCAPE_ID` into
`ciu.env` (chicken-egg with env-generate ordering) and worktree env carry (worktree instances
re-render the global chain from their own tree and inherit the value for free). Out-of-scope /
forbid: everything in scope.forbid — in particular `workspace_env.py` and `worktree.py` stay
untouched; sibling repos are read-only context.

## Work
1. Validation in `render_global_chain` after the final merge; tagged error per house style.
2. The three tests (O1 both directions, O2 reach).
3. Docs per O3; mark CIU-36 FIXED with evidence in the tracker and LOG.

## Environment setup
Implement in the dispatched worktree at `../.worktrees/<branch>/ciu` (trove `worktree_root`).
Standard ciu gate: `cd ciu && export PYTHONPATH=src && python run-ciu-tests.py` inside
tester-unified (100% line+branch holds — no pragma, no hollow tests, no live Docker).

## BLOCKED rule
If an oracle is impossible within scope.touch, write `BLOCKED: <mechanical reason>` to the LOG,
commit, exit. Forbidden workarounds: widening scope, warn-instead-of-fail, validating per
directory.
