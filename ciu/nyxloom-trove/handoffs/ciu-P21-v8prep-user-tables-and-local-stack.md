---
schema_version: 1
id: ciu-P21-v8prep-user-tables-and-local-stack
project: ciu
component: config_model
title: "V8-PREP-1 (ciu.user_tables declaration-gated global namespace check) + V8-PREP-4 (local_stack recognized as a preferred stack root key) — two small, independent, additive V8 groundwork items bundled for efficiency"
tier: implement-2
input_revision: "13c039ac"
source: {kind: research, ref: "V8-PREP-1/4 additive-safety research pass, controller session 2026-08-25, grounded in docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.14/§1.16"}
stack: none
depends_on: [P14]
session: fresh
scope:
  touch:
    - "src/ciu/config_model.py"
    - "tests/tests/test_ciu_config_model_user_tables.py"
    - "tests/tests/test_ciu_config_model_local_stack.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P21-v8prep-user-tables-and-local-stack-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/composefile.py"
    - "src/ciu/provisioning.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-user-tables-declaration
    observable: "A new frozenset `RESERVED_GLOBAL_TABLES` in config_model.py — DISTINCT from the existing `RESERVED_GLOBAL_NAMESPACES` (~line 57-70, which governs STACK root-key collisions, S3.7, a different question) — names the top-level tables CIU itself actually reads at GLOBAL scope today (grep render_global_chain and its callers to enumerate them; at minimum `deploy`, `ciu`; confirm/extend by reading config_model.py in full before finalizing the set — do not guess). An optional global key `ciu.user_tables = [...]` (list of strings, each matching `^[A-Za-z0-9_-]+$`, no duplicates, no member already in `RESERVED_GLOBAL_TABLES`) is validated by a new `validate_user_tables(merged: dict) -> None`, called from `render_global_chain` immediately beside the existing `_validate_deploy_landscape_id` call (~line 509-513) — i.e. once, on the FINAL merged config including the worktree overlay, for the same reason documented at that call site. When `ciu.user_tables` is ABSENT, the function is a no-op — zero behavior change for every config that doesn't opt in. When present, every top-level key in the merged config that is neither in `RESERVED_GLOBAL_TABLES` nor listed in `ciu.user_tables` is a single collective ValueError naming every offending key (mirror `expand_env_vars_or_fail`'s collective-error style, ~line 176-182)."
    negative: "using RESERVED_GLOBAL_NAMESPACES as the allowlist denominator instead of a new RESERVED_GLOBAL_TABLES (conflates 'forbidden stack root key' with 'CIU reads this globally' — two different sets); inventing an `[app]` table (the backlog title mentions it but proposal §1.14 and the current repo have no such table anywhere — ship only `ciu.user_tables`); a validation that runs even when ciu.user_tables is absent"
    gate: "tester-unified"
  - id: O2-local-stack-root-key
    observable: "`validate_stack_shape` (~line 633-688) accepts `local_stack` as a recognized, preferred stack root key name, returned exactly like any other valid root key — the existing S3.5 'exactly one non-state top-level key' invariant is UNCHANGED (local_stack is still exactly one key). A stack opts in purely by naming its own root table `[local_stack]` instead of a directory-derived name; stacks that don't rename are byte-for-byte unaffected — their existing root key still hits the current S3.7 collision check unchanged. `local_stack` is NOT added to `RESERVED_GLOBAL_NAMESPACES` (that set means 'forbidden as a stack root key' — the exact opposite of what this oracle ships) and NOT added to O1's `RESERVED_GLOBAL_TABLES` either (it's a stack-scope concept, not a global one)."
    negative: "adding local_stack to RESERVED_GLOBAL_NAMESPACES or RESERVED_GLOBAL_TABLES (inverts the intent — would make it FORBIDDEN); implementing per-service [local_stack.<svc>] wiring or hook relocation (explicitly deferred — no defined ordering/precedence semantics exist yet for per-service hooks; this package ships ONLY root-key recognition)"
    gate: "tester-unified"
  - id: O3-tests
    observable: "test_ciu_config_model_user_tables.py: absent ciu.user_tables -> any top-level keys pass unchanged (regression bar); present with a valid declaration -> unlisted key collectively named in one ValueError; a declared member colliding with RESERVED_GLOBAL_TABLES -> config error naming the collision; malformed declaration (non-list, non-string member, bad-charset member, duplicate) -> tagged error. test_ciu_config_model_local_stack.py: a stack with root key `local_stack` -> validate_stack_shape returns 'local_stack' successfully, downstream consumers (secrets discovery, hooks, configfile, governance) work unchanged because they take root_key as a parameter (verify at least secret_directives.discover accepts it in a fixture, per the handoff's own grounding that every reader is parameterized on root_key); a stack with a conventional directory-derived root key is completely unaffected (no regression)."
    negative: "a happy-path-only test suite with no negative/malformed case per oracle; asserting only 'no exception' without checking the actual returned root_key or the exact error message content"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/SPEC.md documents `ciu.user_tables` (new S3-adjacent clause, cite the exact section you land it in and why in the LOG) and `local_stack` as a recognized root key name (extend the existing S3.5/S3.7 prose). docs/CONFIG.md gets one worked example of `ciu.user_tables` and a note that a stack MAY name its root table `local_stack`. CHANGES.md Unreleased entry for both. docs/BACKLOG-2026-08-24.md's V8-PREP-1 and V8-PREP-4 rows -> updated to reflect the additive subset shipped (state plainly that this is the additive groundwork, not the full V8 breaking form: V8-PREP-1's eventual breaking step is defaulting `ciu.user_tables` to empty; V8-PREP-4's is making `local_stack` the ONLY accepted root key and relocating hooks)."
    negative: "documenting either item as if the full V8 breaking behavior already shipped"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "grepping config_model.py/render_global_chain's actual global reads shows RESERVED_GLOBAL_TABLES needs more members than {deploy, ciu} to be honest — this is not a blocker, extend the set and note the discrepancy in the LOG"
  - "a downstream reader of root_key (secrets, hooks, configfile, governance) turns out to special-case a specific root-key STRING rather than treating it as an opaque parameter, in a way that would make 'local_stack' behave differently than any other name — BLOCKED naming the exact special-case, this would mean O2's premise (every reader is parameterized) is wrong for that one reader"
mutexes: [merge-lane]
review_focus:
  - "RESERVED_GLOBAL_TABLES vs RESERVED_GLOBAL_NAMESPACES are genuinely two different frozensets serving two different checks, not one renamed"
  - "a config that declares NEITHER ciu.user_tables NOR a local_stack root table sees zero behavior change (both are purely additive/opt-in)"
  - "no per-service [local_stack.<svc>] machinery or hook relocation crept in beyond root-key recognition"
---

# ciu-P21 — V8-PREP-1 (`ciu.user_tables`) + V8-PREP-4 (`local_stack` root key)

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#V8-PREP-1` and `#V8-PREP-4` (already in your context via `source`).
2. `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.14 and §1.16 (search for these markers) — the V8 target shapes. Note §1.14's own example lists `build` as a USER table, not a CIU-reserved one — do not add `build` to `RESERVED_GLOBAL_TABLES`.
3. `src/ciu/config_model.py`:
   - `RESERVED_GLOBAL_NAMESPACES` (~line 57-70) and its ONLY consumer `validate_stack_shape` (~line 633-688, specifically the S3.7 collision check ~line 682) — READ CAREFULLY, this is the set you must NOT reuse as O1's allowlist.
   - `render_global_chain` (~line 411-518) and `_validate_deploy_landscape_id` (~line 528-552) — the exact call-site pattern and "final merged config including overlay" precedent you mirror for `validate_user_tables`.
   - `expand_env_vars_or_fail` (~line 123-184, collective-error style at ~176-182) — mirror this error-aggregation style.
   - `validate_stack_shape` again for O2 — confirm exactly how it currently determines the "one non-reserved top-level key" and where the minimal change to also ACCEPT `local_stack` by name goes.
4. Grep every caller that receives `root_key` as a parameter after `validate_stack_shape` returns it (secret_directives.discover, composefile's configfile iteration, engine.py's hooks lookup, governance) — confirm none of them special-case the STRING value, only use it as an opaque dict key. This is the load-bearing assumption behind O2 being additive.

## Work

1. `RESERVED_GLOBAL_TABLES` + `validate_user_tables` + wiring into `render_global_chain` (O1).
2. `local_stack` recognition in `validate_stack_shape` (O2).
3. Tests per O3.
4. Docs per O4.

## Environment setup

Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`, branch `feat/ciu-qol-v8prep-wave`, venv at `.venv/`, `tests/conftest.py` scrubs ambient identity env vars.

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

If a named contract cannot be met, or scope requires a forbidden file, STOP: write `BLOCKED: <reason>` to `nyxloom-trove/reports/ciu-P21-v8prep-user-tables-and-local-stack-LOG.md`, commit, exit.
