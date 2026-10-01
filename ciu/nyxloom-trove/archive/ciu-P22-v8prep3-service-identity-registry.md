---
schema_version: 1
id: ciu-P22-v8prep3-service-identity-registry
project: ciu
component: config_model+deploy
title: "V8-PREP-3 narrowed: declaration-only [service.<stack>] registry (type/location/description) validated at global scope, plus a WARN-only ciu check lint cross-checking it against what's actually deployed — no per-service realness sub-tables"
tier: implement-2
input_revision: "13c039ac"
source: {kind: research, ref: "V8-PREP-3 additive-safety research pass, controller session 2026-08-25, grounded in docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.15/§3.1 (rev 1.4 two-level stack.service hierarchy, commit 4440c17e)"}
stack: none
depends_on: [P21]
session: fresh
scope:
  touch:
    - "src/ciu/config_model.py"
    - "src/ciu/deploy.py"
    - "tests/tests/test_ciu_config_model_service_registry.py"
    - "tests/tests/test_ciu_deploy_actions.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P22-v8prep3-service-identity-registry-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/composefile.py"
    - "src/ciu/provisioning.py"
    - "src/ciu/deploy_pkg/layouts.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-registry-shape
    observable: "A new optional global table `[service.<stack_name>]` accepts exactly: `type` (required, closed enum `CIU|COMPOSE|EXTERNAL|IN_PROCESS`), `location` (required for type CIU/COMPOSE, FORBIDDEN — a config error, not silently ignored — for EXTERNAL/IN_PROCESS; a repo-relative directory path), `description` (optional string). Any other key at `[service.<stack_name>]` scope, and any nested table under it, is REJECTED with a tagged error naming the stack name and the offending key (this deliberately reserves the per-service realness sub-table layer, §3.2, for the real V8 rewrite — do not accept and ignore it, REFUSE it, so V8 can define that layer freely later without a silent-acceptance migration trap). For type CIU, `location` must name a directory containing `ciu.defaults.toml.j2`; for COMPOSE, a directory containing `docker-compose.yml` (reuse config_constants.py's existing filename constants, do not hardcode the strings again). Absent `[service.*]` entirely is a no-op — zero behavior change."
    negative: "silently accepting or ignoring an unrecognized key or nested table (defeats the whole point of reserving the per-service layer for V8); a location check for EXTERNAL/IN_PROCESS types (they must FORBID location, not silently permit it)"
    gate: "tester-unified"
  - id: O2-consistency-lint
    observable: "ciu check (deploy.py's existing static-lint site, ~line 596-655) gains a WARN-only (never a refusal, never exit 2) cross-check: every `[service.<name>].location` that no currently-selected profile/phase actually deploys (compare against deploy.phases.*.services[].path and profile stacks) is named in a `[WARN]`; every deployed stack path that has NO corresponding `[service.*]` registry entry is ALSO named in a separate `[WARN]` (both directions — a registry entry nobody deploys, and a deployed stack nobody registered). This runs only when `[service.*]` is non-empty (registry-declaration-gated, matching O1's opt-in nature)."
    negative: "making either direction of the cross-check a hard failure/exit 2 (the registry is advisory groundwork, not an enforced join yet — per the deferred §1.16 mapping rule 1, an entry with no live consumer or a live stack with no registry entry are both legitimate transitional states, not defects)"
    gate: "tester-unified"
  - id: O3-tests
    observable: "Registry shape tests: valid CIU/COMPOSE/EXTERNAL/IN_PROCESS entries each pass; location required-but-missing for CIU/COMPOSE fails naming the stack; location present-but-forbidden for EXTERNAL/IN_PROCESS fails naming the stack; an extra/unknown key or nested table at stack scope is rejected; an unknown `type` value is rejected naming the closed vocabulary; absent `[service.*]` -> zero validation calls made (a spy/counter fixture proving the no-op, not just 'no exception'). Lint tests: a registry entry with no deploying profile -> WARN naming it, `ciu check` still exits per its existing (unchanged) success contract; a deployed stack with no registry entry -> separate WARN; both present and consistent -> no WARN; absent registry -> the lint code path is not entered at all."
    negative: "a lint test that only checks stdout contains a substring without checking BOTH the entry-not-deployed and deployed-not-registered directions independently"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/SPEC.md documents the `[service.<name>]` registry shape and its deliberately-reserved per-service sub-table layer (name the SPEC section, e.g. extending S3 or a new S3.x, your call, state which in the LOG). docs/CONFIG.md gets a worked example (one CIU stack, one EXTERNAL entry). CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md's V8-PREP-3 row corrected: the row currently says 'Global [service.*] registry eliminated' which is STALE/WRONG relative to proposal rev 1.4 (commit 4440c17e fixed the proposal to a two-level stack.service hierarchy that INTRODUCES this registry, it does not eliminate it) — fix this row's own description, not just its status, and note in the LOG that you corrected a stale backlog claim, grounding it against the actual proposal section."
    negative: "leaving the backlog row's incorrect 'eliminated' framing in place while just flipping its status"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "config_constants.py does not actually name the ciu.defaults.toml.j2 / docker-compose.yml filenames as reusable constants (contradicting this handoff's assumption) — BLOCKED naming what you find instead, do not hardcode a duplicate string without checking first"
mutexes: [merge-lane]
review_focus:
  - "the per-service realness sub-table layer is genuinely REJECTED (not silently accepted-and-ignored) when present — this is the one thing that would create real V8 migration debt if gotten wrong"
  - "the lint is WARN-only in both directions, never a refusal"
  - "the backlog row correction (O4) actually fixes the 'eliminated' claim, not just the status"
---

# ciu-P22 — V8-PREP-3: service identity registry (declaration + lint only)

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#V8-PREP-3` (already in context) — note its "eliminated" framing is STALE (O4 has you fix this).
2. `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.15 and §3.1 (search for these — §3.1 was amended in commit `4440c17e`, "fix V8 service model to two-level stack.service hierarchy"; read the CURRENT text, not any cached assumption about a flat model).
3. `src/ciu/config_model.py`: `render_global_chain` and `_validate_deploy_landscape_id` (same call-site pattern as P21's O1 — mirror it for this new registry validation, run it right alongside/after P21's `validate_user_tables` call).
4. `src/ciu/config_constants.py` — confirm the exact filename constants for a CIU stack's defaults file and a compose stack's compose file (do not hardcode duplicate string literals).
5. `src/ciu/deploy.py` ~line 596-655 (`action_check`'s existing static-lint site) — this is P18's insertion point too (a LATER package); confirm at your commit whether P18 has landed yet and if it changed this area's shape — if P18 has landed, add your lint alongside its stage-walking structure rather than as an unrelated bolt-on; if not, add it as a new step in the current `action_check` body.
6. `src/ciu/deploy_pkg/phases.py` (`build_selection`'s consumers) and `deploy.py`'s profile/stack enumeration — the sources of "what's actually deployed" your lint's second direction needs.

## Work

1. `[service.<name>]` shape validation (O1).
2. `ciu check` two-directional WARN-only lint (O2).
3. Tests (O3).
4. Docs, including the backlog row correction (O4).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P22-v8prep3-service-identity-registry-LOG.md`, commit, exit.
