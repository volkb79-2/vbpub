---
schema_version: 1
id: ciu-P23-v8prep5-one-shot-completion
project: ciu
component: provisioning+deploy_pkg
title: "V8-PREP-5: new :completed provisioning-ref terminal (exit-0-based, never consults Health), one_shot phase-entry key, a working meaning for slash-bearing stack-path selectors, and a lint_graph fix so stack-path deps actually enter cycle detection"
tier: implement-2
input_revision: "13c039ac"
source: {kind: research, ref: "V8-PREP-5 additive-safety research pass, controller session 2026-08-25, grounded in docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.18 and live source at src/ciu/provisioning.py"}
stack: none
depends_on: [P22]
session: fresh
scope:
  touch:
    - "src/ciu/provisioning.py"
    - "src/ciu/deploy_pkg/phases.py"
    - "src/ciu/config_model.py"
    - "tests/tests/test_ciu_provisioning.py"
    - "tests/tests/test_ciu_deploy_phases.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P23-v8prep5-one-shot-completion-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/composefile.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-completed-terminal
    observable: "Both provisioning-ref grammars (provisioning.py ~line 38 AND config_model.py ~line 701 — they MUST stay in sync, this codebase already keeps two copies) accept a new terminal `:completed` alongside the existing `:healthy`, additive (existing `:healthy` refs parse and behave EXACTLY as before). `_probe_stack` (provisioning.py ~379-433) resolves a `:completed` ref by: container exists (else not-satisfied, same as today's missing-container path), `State.Running == false`, `State.ExitCode == 0` -> satisfied; any other state (running, or exited non-zero) -> not satisfied. A `:completed` probe NEVER reads `Health` at all — this is the exact semantic gap fixed relative to `:healthy`'s exit-0-with-no-healthcheck special case at ~line 427-432."
    negative: "reusing the existing :healthy code path with a flag instead of a genuinely separate branch that never touches Health; a :completed probe that still checks Health.Status when present"
    gate: "tester-unified"
  - id: O2-deprecation-warn
    observable: "The existing exit-0-when-no-healthcheck special case at provisioning.py ~427-432 is left BEHAVING exactly as it does today (no behavior change — removing it is the breaking half V8 does later), but now ALSO emits a `[WARN]` naming the ref and suggesting `:completed` as the correct replacement for a one-shot service. This is the only change to that branch: an added warning, not an added abort."
    negative: "removing or gating the existing behavior behind a flag (that IS the breaking change this package must not make)"
    gate: "tester-unified"
  - id: O3-one-shot-key
    observable: "New optional phase-entry key `one_shot = true` (bool) on `deploy.phases.<key>.services[]`, validated by a new `phases.service_one_shot(service: dict) -> bool` mirroring `service_shipped`/`service_health_enabled`'s exact pattern (default False; non-bool present -> tagged [S7.2] ValueError naming the bad type) — same file, same style as P15's `service_health_timeout` if P15 has landed by the time you carve this (check; if it has, place this function adjacently and reuse whatever shared validation helper P15 may have introduced rather than duplicating it). This package does NOT wire one_shot into the deploy loop's post-up wait behavior (that's deploy.py, forbidden here, and a larger change than this package's scope) — it ships the DECLARATION + shape validation only, plus a ciu check-time cross-reference: if a stack declares `one_shot = true` in its OWN phase entry while ANOTHER stack's `requires` references it via `stack:<this>:healthy` (not `:completed`), warn suggesting `:completed` (mirrors O2's warning, reusing the same detection: is a one_shot-declared stack referenced via :healthy anywhere in the provisioning graph)."
    negative: "wiring one_shot into the actual health-gate polling loop in deploy.py (out of scope, forbidden file, and a materially bigger change belonging to a future package once a real consumer needs the polling behavior, not just the declaration)"
    gate: "tester-unified"
  - id: O4-path-selector
    observable: "When a provisioning ref's selector contains `/` (e.g. `stack:infra/db-init:completed`), resolve it as a repo-relative stack path against the known set of declared stack paths (deploy.phases.*.services[].path and profile stacks -- same enumeration O2-consistency-lint in ciu-P22 uses, if that package has landed; otherwise derive it directly) and use THAT entry's compose project for container-name resolution, instead of today's guaranteed-broken behavior (the raw selector containing a `/` gets passed straight into container_name and can never match a real container). A slash-FREE selector's resolution is BYTE-IDENTICAL to today (container_name(config, selector) unchanged) -- this is the regression bar. Since every slash-bearing selector is provably broken today (grep-confirm this yourself: no test exercises a passing slash-bearing selector), giving it a working meaning breaks no real behavior."
    negative: "changing the resolution behavior for a slash-free selector in any way; leaving the slash-bearing path silently broken while only adding :completed for slash-free selectors"
    gate: "tester-unified"
  - id: O5-lint-graph-fix
    observable: "`lint_graph` (provisioning.py ~115-146) is fixed to recognize a `stack:<path>:healthy|completed` ref whose selector matches a KNOWN stack-path key (same enumeration as O4) and add that edge to the cycle-detection graph -- today such refs are silently skipped (~145-146) because the graph is keyed on stack paths while the selector is compared as a bare service token, so stack-to-stack dependencies via a full path NEVER enter cycle detection. This is a 3-5 line, single-file, high-value fix per the research: verify it with a test fixture that constructs an actual cycle using two stacks referencing each other by full path via :completed or :healthy, and assert lint_graph now DETECTS it (today's behavior: silently passes a real cycle)."
    negative: "a test that only checks the fix doesn't crash, without actually constructing a real cycle and asserting it's now caught (this is the one oracle that proves the fix does anything)"
    gate: "tester-unified"
  - id: O6-docs
    observable: "docs/SPEC.md documents :completed (extending whatever section documents :healthy today), one_shot's shape and its ciu-check-time cross-reference warning, and the lint_graph fix's effect (name the exact section, state in the LOG). docs/CONFIG.md gets a worked one_shot + :completed example (e.g. a db-migration one-shot service another stack's requires references via :completed). CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md's V8-PREP-5 row -> updated with the additive subset shipped, and the false-positive risk this closes stated plainly."
    negative: "documenting one_shot as if it changes deploy-time polling behavior (it does not in this package)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the two provisioning-ref grammars (provisioning.py and config_model.py) have already drifted apart before this package touches them (i.e. adding :completed to one but the sync assumption is already broken) — BLOCKED naming the exact drift, do not silently fix a pre-existing drift as a side effect of this package"
mutexes: [merge-lane]
review_focus:
  - "the O5 lint_graph fix actually detects a real constructed cycle in its test, not just 'doesn't crash'"
  - "a :completed probe genuinely never reads Health under any code path"
  - "slash-free selector resolution is byte-identical to pre-package behavior (diff the exact container_name call before/after)"
---

# ciu-P23 — V8-PREP-5: one-shot completion semantics

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#V8-PREP-5` and `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.18.
2. `src/ciu/provisioning.py` in full — `_probe_stack` (~379-433, especially the exit-0-when-no-healthcheck branch ~420-432), the ref grammar (~line 38), `container_name`'s caller chain, and `lint_graph` (~115-146, especially the silent-skip at ~145-146).
3. `src/ciu/config_model.py` ~line 701 — the SECOND copy of the ref grammar; confirm it is kept in sync with provisioning.py's and update both.
4. `src/ciu/deploy_pkg/phases.py` — `service_shipped`/`service_health_enabled` (and `service_health_timeout` if ciu-P15 has landed by your commit — check `ls nyxloom-trove/reports/ciu-P15*-LOG.md`) for the exact validation-accessor pattern to mirror for `service_one_shot`.
5. `src/ciu/deploy.py` ~line 138-151 (`container_name`) — READ-ONLY (forbidden file), understand it to know what O4's path-selector resolution must ultimately feed into without editing this file.

## Work

1. `:completed` terminal in both grammars + `_probe_stack` branch (O1).
2. Deprecation `[WARN]` on the existing exit-0-no-healthcheck path (O2).
3. `one_shot` phase-entry key + shape validation + ciu-check cross-reference warning (O3).
4. Working resolution for slash-bearing selectors (O4).
5. `lint_graph` fix (O5).
6. Docs (O6).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P23-v8prep5-one-shot-completion-LOG.md`, commit, exit.
