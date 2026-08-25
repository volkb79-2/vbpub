---
schema_version: 1
id: ciu-P25-v8prep7-env-required
project: ciu
component: composefile
title: "V8-PREP-7: optional env_required = [...] service declaration, checked collectively against the ACTUAL environment docker compose will see (post secret-materialization, pre-invocation) — not a new Jinja mechanism, {{ env.* }} already exists"
tier: implement-2
input_revision: "13c039ac"
source: {kind: research, ref: "V8-PREP-7 additive-safety research pass, controller session 2026-08-25, grounded in docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.20 and live source at src/ciu/composefile.py compose_process_env"}
stack: none
depends_on: [P24]
session: fresh
scope:
  touch:
    - "src/ciu/composefile.py"
    - "tests/tests/test_ciu_composefile.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P25-v8prep7-env-required-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/config_model.py"
    - "src/ciu/workspace_env.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-declaration
    observable: "New optional key `[<root>.<service>] env_required = [...]` — a list of non-empty strings, each matching `^[A-Za-z_][A-Za-z0-9_]*$` (a valid shell/env variable name), duplicates rejected. Shape-validated whenever present; absent -> zero behavior change."
    negative: "accepting a non-list, empty-string member, or invalid-charset member without a tagged error naming the service and the bad value"
    gate: "tester-unified"
  - id: O2-check-timing
    observable: "The presence check runs against the output of `compose_process_env(specs, materialized, base=profile_env)` (~composefile.py:1115-1157) — i.e. AFTER secret materialization and IMMEDIATELY BEFORE the compose invocation — not at config-render time. This ordering matters: a variable supplied via `expose_env` (~composefile.py:1146-1155, from materialized secrets) must NOT be flagged missing just because it isn't in the bare process environment before materialization. Checking at the wrong point (too early) is the exact false-failure this oracle exists to prevent."
    negative: "checking env_required against os.environ or the pre-materialization environment (produces false failures for expose_env-supplied names)"
    gate: "tester-unified"
  - id: O3-collective-error
    observable: "Missing variables across ALL declared services in one compose invocation are collected into ONE error naming every missing (service, variable) pair — mirror config_model.py's `expand_env_vars_or_fail` collective-error style referenced by this wave's other packages, do not raise on the first miss and stop."
    negative: "raising per-variable (stops after the first miss, hides the rest) instead of collecting all missing across all services first"
    gate: "tester-unified"
  - id: O4-no-new-jinja-mechanism
    observable: "This package adds NO new Jinja context variable and NO new template-facing mechanism — `{{ env.* }}` already receives the full process environment on every render path (config_model.py ~342, composefile.py ~251/~686, confirm these citations against your own commit before relying on them) and already works today. Confirm this via a test that a template referencing `{{ env.SOME_VAR }}` for a var supplied only via env_required's context (i.e. present in the real environment) renders correctly with NO change from this package's code — the ONLY new behavior is the missing-variable CHECK, not variable access."
    negative: "adding a second env-access mechanism alongside the existing {{ env.* }} (redundant, and risks the two disagreeing about what 'present' means)"
    gate: "tester-unified"
  - id: O5-docs
    observable: "docs/CONFIG.md documents `env_required`, its collective-error behavior, and the guaranteed-present machine-identity keys (workspace_env.py's REQUIRED_KEYS_CORE / GENERATED_IDENTITY_KEYS — cite their real names/line numbers, read-only, this file is forbidden for edits) a template can already rely on via `{{ env.* }}` without declaring them in env_required. docs/SPEC.md documents the check's timing (post-materialization, pre-invocation) explicitly, since getting this wrong is the main way a future implementer could regress it. CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md's V8-PREP-7 row -> updated with the additive subset shipped. Explicitly state in the LOG and in SPEC.md: this package does NOT touch `${VAR:-fallback}` handling in compose templates — withdrawing that is the SEPARATE, genuinely breaking QOL-10 item, deliberately out of scope here."
    negative: "conflating this package with QOL-10's fallback-withdrawal (a different, breaking item deferred to the real V8 gate)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "compose_process_env's actual call site/timing turns out to already run BEFORE secret materialization in some path this handoff didn't account for — BLOCKED naming the exact call order you find, do not guess at a hook point that would produce O2's exact false-failure bug this package exists to avoid"
mutexes: [merge-lane]
review_focus:
  - "the check genuinely runs post-materialization (construct a fixture where a required var comes ONLY from expose_env and confirm it's NOT falsely flagged missing)"
  - "no new Jinja/template mechanism was added — {{ env.* }} usage is provably unchanged"
  - "all missing variables across all services are named in one error, not just the first"
---

# ciu-P25 — V8-PREP-7: `env_required` declarations

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#V8-PREP-7` and `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.20.
2. `src/ciu/composefile.py`: `compose_process_env` (~1115-1157, especially the `expose_env` secret-value injection at ~1146-1155) — this is the EXACT environment your check must validate against, at the EXACT point it's called relative to the compose invocation (trace its caller to confirm ordering relative to secret materialization).
3. `src/ciu/config_model.py` ~342 and `src/ciu/composefile.py` ~251/~686 (read-only, config_model.py is forbidden for edits) — confirm `{{ env.* }}` already receives the full process environment on every render path, so O4's "no new mechanism" claim holds at your commit.
4. `src/ciu/config_model.py`'s `expand_env_vars_or_fail` (~123-184, forbidden file, read-only) — mirror its collective-error STYLE (not its code) for O3.
5. `src/ciu/workspace_env.py` `REQUIRED_KEYS_CORE`/`GENERATED_IDENTITY_KEYS` (read-only, forbidden file) — the machine-identity keys to document as already-available in O5.

## Work

1. `env_required` shape validation (O1).
2. Wire the presence check at the correct point, post-materialization (O2, O3).
3. Confirm/test no new Jinja mechanism was needed (O4).
4. Docs (O5).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P25-v8prep7-env-required-LOG.md`, commit, exit.
