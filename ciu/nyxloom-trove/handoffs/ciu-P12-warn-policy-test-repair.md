---
schema_version: 1
id: ciu-P12-warn-policy-test-repair
project: ciu
component: warn_policy
title: "Repair gate: test_ciu_warn_policy.py and two governance mem_min tests still assert the pre-exit_on (CIU_WARNINGS_AS_ERRORS boolean) contract that commit 51d5d4f7 replaced; delete the now-dead warnings_as_errors_enabled() shim"
tier: implement-1
input_revision: "370ea814"
source: {kind: gate-regression, ref: "baseline gate run on main@370ea814, controller session 2026-08-25 — 8+2 failures, coverage 99.77%"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "tests/tests/test_ciu_warn_policy.py"
    - "tests/tests/test_ciu_deploy_actions.py"
    - "src/ciu/warn_policy.py"
  forbid:
    - "src/ciu/deploy.py"
    - "docs/SPEC.md"
    - "docs/DESIGN-NOTES.md"
    - "CHANGES.md"
oracles:
  - id: O1-warn-policy-tests-match-exit-on
    observable: "test_ciu_warn_policy.py tests ONLY the symbols that actually exist in src/ciu/warn_policy.py today: EXIT_ON_ENV_VAR, EXIT_ON_VALUES, DEFAULT_EXIT_ON, _resolve_exit_on, _validate_exit_on, should_exit_on, warn_or_raise. Cover: should_exit_on() truth table for all 3x2 (threshold, severity) combinations; _resolve_exit_on()'s precedence (config wins over env wins over default); _validate_exit_on() rejects an invalid value naming the source and vocabulary (both the config-sourced and env-sourced call paths); warn_or_raise() prints '[<SEVERITY>] <message>' and raises ValueError(message) (exact text, no tag) exactly when should_exit_on(severity, config=config) is True, and does not raise otherwise."
    negative: "a test still importing/asserting warn_policy.WARNINGS_AS_ERRORS_ENV_VAR or warn_policy.warnings_as_errors_enabled (both must no longer exist after this package); a test that passes without exercising _validate_exit_on's raise path from both the config and env sources"
    gate: "tester-unified"
  - id: O2-dead-shim-removed
    observable: "warnings_as_errors_enabled() (src/ciu/warn_policy.py:90-98) is deleted. `grep -rn warnings_as_errors_enabled src/ tests/` returns zero matches repo-wide after this package (it has exactly one real caller today: the stale test file being rewritten under O1 — confirmed via `grep -rn warnings_as_errors_enabled src/ tests/ docs/` before this package, which found only the definition and the stale test)."
    negative: "the function kept 'just in case'; a re-export or alias replacing it"
    gate: "tester-unified"
  - id: O3-governance-mem-min-tests-match-shipped-decision
    observable: "The two tests in test_ciu_deploy_actions.py at (pre-edit) lines 1108-1154 are rewritten to match the ALREADY-SHIPPED, ALREADY-DOCUMENTED decision at src/ciu/deploy.py:1127-1150 (read the comment at :1128-1134 — it is the normative record, do not re-litigate it): (a) a mem_min-inadequate finding under the DEFAULT config (no ciu.exit_on set, so exit_on resolves to ERROR) prints '[WARN] [S15.16] ...' and does NOT raise; (b) the same finding with config={'ciu': {'exit_on': 'WARN'}} DOES raise ValueError containing '[S15.16]' and the slice name. Neither test may reference CIU_WARNINGS_AS_ERRORS or any os-environ monkeypatch for this policy — exit_on's config-first resolution path is what deploy.py actually calls (config=config is already threaded to warn_or_raise at deploy.py:1150), so drive it through the config parameter, not the environment."
    negative: "a test that reintroduces the CIU_WARNINGS_AS_ERRORS env var as its lever; a test that asserts the OLD default (raises with no config at all) — that assertion is checking a behavior the codebase intentionally no longer has"
    gate: "tester-unified"
  - id: O4-full-gate-green
    observable: "`.venv/bin/python run-ciu-tests.py` run with the ambient identity env vars unset (see Environment setup) exits 0 with 100% line+branch coverage and zero failures — the SAME command the wave's baseline run used, now clean."
    negative: "any remaining failure or any coverage regression below 100%"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "any OTHER call site of warn_policy.warnings_as_errors_enabled or warn_policy.WARNINGS_AS_ERRORS_ENV_VAR turns up outside the two files named in scope.touch — BLOCKED naming the file:line (this would mean the fix is not mechanical/self-contained after all)"
  - "the governance mem_min production code at deploy.py:1127-1150 appears to need a CHANGE (not just its tests) to make O3 pass — that is out of scope (forbid: src/ciu/deploy.py); BLOCKED naming the mismatch, do not edit deploy.py"
mutexes: [merge-lane]
review_focus:
  - "O3's two rewritten tests must actually exercise deploy.governance_slice_preflight end-to-end (not just warn_policy in isolation) — confirm they still monkeypatch check_slice_unit/check_slice_memory_min exactly as the originals did, changing only the exit_on lever and expected outcome"
  - "no `# pragma: no cover` introduced anywhere; the 10 previously-uncovered statements in warn_policy.py (47->52, 50, 54, 60-66, 83, 85, 98, 116 per the baseline coverage report) must all be covered by O1's rewritten suite once the dead function at 90-98 is deleted (deleting it removes some of those line numbers from existence entirely; the rest — should_exit_on's branches, _validate_exit_on's raise, warn_or_raise's raise/no-raise split — must be genuinely exercised, not incidentally hit)"
---

# ciu-P12 — repair the exit_on migration's stale tests (gate is currently red on main)

## Why this exists

The controller ran CIU's real gate command (`run-ciu-tests.py`) fresh from `main@370ea814`
before starting this implementation wave, per the estate rule that new work never lands on
top of an already-red gate. It is red: **8 failures in `tests/tests/test_ciu_warn_policy.py`**
(`AttributeError: module 'ciu.warn_policy' has no attribute 'WARNINGS_AS_ERRORS_ENV_VAR'`) and
**2 failures in `tests/tests/test_ciu_deploy_actions.py`** (the two `governance_slice_preflight`
mem_min tests, "DID NOT RAISE ValueError"), plus coverage sitting at 99.77-99.84% instead of
100%. Root cause for all ten: commit `51d5d4f7` ("implement exit_on + single-stack validation")
rewrote `src/ciu/warn_policy.py` from a boolean `CIU_WARNINGS_AS_ERRORS` env-var model to the
closed-vocabulary `ciu.exit_on` model (`WARN`/`ERROR`/`NEVER`, default `ERROR`) and updated the
ONE real call site (`deploy.py:1135-1150`), but never updated the tests that assert the OLD
contract. This is pure test-suite repair matching an already-shipped, already-documented
behavior change — **no production behavior changes in this package** except deleting one
now-dead compatibility shim.

**This package must land and gate green BEFORE any other package in this wave starts** — every
later package's coverage/oracle evidence is unreliable while the baseline itself is red.

## Context to read first

1. `src/ciu/warn_policy.py` (whole file, 117 lines) — the CURRENT, correct contract. Read the
   module docstring (:1-27) for the vocabulary and resolution order, then `should_exit_on`
   (:69-87), `warn_or_raise` (:101-116), and the dead shim `warnings_as_errors_enabled`
   (:90-98) you are about to delete.
2. `tests/tests/test_ciu_warn_policy.py` (whole file, 68 lines) — every test in it references
   `warn_policy.WARNINGS_AS_ERRORS_ENV_VAR`, which does not exist. This entire file gets
   rewritten against the real module.
3. `src/ciu/deploy.py:1095-1155` — `governance_slice_preflight`'s mem_min branch. Read the
   comment at `:1128-1134` in full — it is the operator's own record of the intended default
   behavior change (mem_min-inadequate is now a non-fatal WARN by default; `ciu.exit_on =
   "WARN"` restores fail-fast). Do not second-guess this decision; the two tests are wrong,
   not the code.
4. `tests/tests/test_ciu_deploy_actions.py:1108-1179` — the three governance mem_min tests
   (`..._raises_when_mem_min_inadequate`, `..._logs_only_when_warnings_opted_out`, and
   `..._passes_when_mem_min_adequate` immediately after — read all three so the rewritten pair
   stays internally consistent with the still-passing third one; note `_governance_selection_rendered_mem_min`
   and `_plain_config()` helpers already in this file, reuse them unchanged).
5. `docs/SPEC.md` — search for `S10.6` and `S10.7` for the normative exit_on contract text (read
   only, this package does not touch SPEC.md — the shipped contract is already documented there).

## Work

1. **Rewrite `tests/tests/test_ciu_warn_policy.py`** against the real module per oracle O1.
   Structure it like the existing file (two test classes are fine, or flatten — your choice,
   this is a degree of freedom). Must cover, at minimum: `should_exit_on` for all combinations
   of threshold (`WARN`/`ERROR`/`NEVER`) x severity (`WARN`/`ERROR`); `_resolve_exit_on`'s
   three-tier precedence (config dict with `ciu.exit_on` set wins even when `CIU_EXIT_ON` env is
   also set to something different; env wins when config has no `ciu` table or no `exit_on` key;
   default `ERROR` when neither is set); `_validate_exit_on` raising `ValueError` with the
   `[S10.7]` tag and the vocabulary listed, from BOTH an invalid config value and an invalid env
   value; `warn_or_raise` printing `[<SEVERITY>] <message>` and raising exactly when
   `should_exit_on` says so (use `capsys` as the existing file did).
2. **Delete `warnings_as_errors_enabled`** from `src/ciu/warn_policy.py:90-98` (the whole
   function and its docstring). Per O2, confirm via grep that nothing else in `src/` or `tests/`
   references it before deleting, and that nothing references it afterward.
3. **Rewrite the two failing tests in `tests/tests/test_ciu_deploy_actions.py`** (currently
   `test_governance_slice_preflight_raises_when_mem_min_inadequate` and
   `test_governance_slice_preflight_mem_min_inadequate_logs_only_when_warnings_opted_out`,
   lines 1108-1154) per oracle O3. Keep the existing monkeypatches of `check_slice_unit` /
   `check_slice_memory_min` exactly as-is; change only how each test drives the exit_on lever
   (via the `config` parameter passed to `deploy.governance_slice_preflight`, not an env var) and
   the expected raise/no-raise outcome. Rename the tests if the old names now describe the wrong
   behavior (e.g. the "logs only when opted out" name described the OLD opt-out shape — pick
   names that describe the NEW default/override shape; this is a degree of freedom, keep them
   descriptive).
4. Run the local iteration signal (see Environment setup) until green at 100% line+branch, then
   stop — do not touch any file outside `scope.touch`.

## Environment setup

This package is dispatched into `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
(already created, venv already provisioned at `.venv/`). **Critical:** this host's shell carries
ambient `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `REPO_NAME`, `INSTANCE_ID`, `DOCKER_NETWORK_INTERNAL`,
and `PUBLIC_FQDN` values from an unrelated dstdns checkout sourced into this devcontainer's login
shell (the exact CIU-41/CIU-47 contamination species, now observed leaking into CIU's OWN test
run rather than a consumer's). These do not affect this package's three files, but they WILL
make unrelated tests in `test_ciu_engine_*.py` fail with `"--define-root ... does not match
REPO_ROOT (/workspaces/dstdns)"` if left set — that is a pre-existing environmental artifact of
this shell, not something to fix, and not something you introduced. Always run the iteration
signal with these six unset:

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN \
  .venv/bin/python run-ciu-tests.py
```

A green run here is the iteration signal only (per this project's evidence ladder — see
`nyxloom-trove/ciu-config-wave-BRIEF-2026-08-19.md` "Environment / gate"); the controller runs
the real `tester-unified`-based gate at checkpoint review.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a file outside
`scope.touch`, STOP: write `BLOCKED: <mechanical reason>` to
`nyxloom-trove/reports/ciu-P12-warn-policy-test-repair-LOG.md`, commit, and exit. Do not edit
`src/ciu/deploy.py` to make a test pass — if O3 seems to require it, that is a BLOCKED trigger,
not a green light.
