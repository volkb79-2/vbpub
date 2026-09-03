---
schema_version: 1
id: nyxloom-P99-l10-per-project-thresholds
project: nyxloom
title: "L10 handoff-size thresholds: per-project [lint.l10] override in nyxloom.toml"
tier: implement-2
input_revision: "f84953a7"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-3-l10-handoff-size-thresholds-are-hardcoded-constants-need-a-per.md
scope:
  touch:
    - "src/nyxloom/config.py"          # add L10Config dataclass (mirrors NotifyConfig's style); add ProjectConfig.l10 field; parse+validate [lint.l10] in ProjectConfig.load (fail loudly, mirrors validate_pipeline's load-time-ValueError pattern)
    - "src/nyxloom/lint.py"            # _check_l10 gains a cfg: ProjectConfig parameter, reads cfg.l10.warn_tokens/error_tokens instead of the hardcoded 10000/18000; update its call site (~line 206, already has cfg in scope from the L7/L9 calls immediately above it) and the L10 rule-catalogue comment (~line 68)
    - "tests/test_lint.py"             # TestL10Size gains 2 new tests: an override-raises-the-ceiling case and a malformed-config-fails-loudly case; the two EXISTING tests (test_large_handoff_warning, test_huge_handoff_error) already cover the no-override/default-fallback oracle via the plain sample_project fixture -- do not duplicate that coverage, just confirm it still passes once cfg is threaded through
    - "src/nyxloom/schemas/nyxloom-config.schema.json"  # add a `lint` top-level property with a nested `l10` object (warn_tokens/error_tokens as positive integers) for structural consistency with the other declared sections (notify, redact, stage, backlog_entries) -- top-level additionalProperties is already true so this is NOT required for the feature to work, only for schema-driven tooling/autocomplete parity
  forbid:
    - "docs/SPEC.md"      # its L10 row ("Handoff token size within project budget (warn, then block at 2x)") is already generic -- it describes a per-project budget, doesn't hardcode a number, and stays accurate whether that budget is the tool-wide default or an override. Confirmed by reading it; no edit needed, and there is no other number to update
    - "tests/fixtures/handoffs/demo-P21-huge.md"  # the committed oversized fixture used by test_lint.py's parametrized fixture sweep (~line 971) -- it tests the DEFAULT threshold path (no [lint.l10] in that fixture's project), which is unaffected by this change; do not resize it
oracles:
  - id: O1
    observable: >-
      A `ProjectConfig` built from a `nyxloom.toml` containing `[lint.l10]\nerror_tokens = 25000`
      lints a handoff at ~20000 tokens (reuse test_huge_handoff_error's exact 80000-char body) as
      L10 WARNING, not ERROR -- proves the override is actually READ and applied, not merely
      parsed and ignored. Implemented as a new test in TestL10Size using
      `dataclasses.replace(sample_project, l10=config.L10Config(error_tokens=25000))` (no new
      git-repo fixture needed -- ProjectConfig is a plain dataclass).
    negative: >-
      The same 20000-token body still lint-ERRORing under the raised ceiling means the override
      was parsed into L10Config but never reached _check_l10 -- fails this oracle.
    gate: tester-unified
  - id: O2
    observable: >-
      `test_large_handoff_warning` and `test_huge_handoff_error` (existing, unmodified except for
      whatever mechanical signature change _check_l10 needs) still pass using the plain
      `sample_project` fixture (no `[lint.l10]` in its project.toml) -- proves the no-override
      fallback path is untouched (10000 warn / 18000 error, unchanged).
    negative: >-
      Either existing test failing, or being deleted/rewritten to pass artificially (e.g.
      widening its body size to dodge a changed default), fails this oracle -- the DEFAULT
      numbers must not move.
    gate: tester-unified
  - id: O3
    observable: >-
      `ProjectConfig.load` on a `nyxloom.toml` containing `[lint.l10]\nwarn_tokens = 20000\n
      error_tokens = 10000` (warn >= error, malformed) raises `ValueError` at load time, before
      any handoff is linted. A second malformed case (either value <= 0, e.g.
      `error_tokens = -5`) also raises `ValueError` at load time.
    negative: >-
      Silently swapping warn_tokens/error_tokens to make them consistent, silently ignoring the
      malformed table and falling back to defaults, or deferring the failure to lint-time instead
      of load-time all fail this oracle -- AGENTS.md 4.2a's fail-loudly analogue applies here.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any touched non-test file outside this list needs an edit to keep the gate green (a
    reverse-dependency this carve's sweep missed)"
  - "_check_l10's call site does not already have cfg in scope (verified true at input_revision --
    the L7 and L9 checks immediately above it both already receive and use cfg); if that has
    changed, threading cfg through requires touching the calling function's own signature, which
    is outside this package's scope.touch"
  - "any OTHER call site of ProjectConfig(...) construction (not sample_project, not
    dataclasses.replace) needs a new required l10 argument -- l10 must have a default_factory so
    existing callers are unaffected; if any construction site breaks, the field was declared
    without a default, which is a carve defect"
  - "E-008 checkpoint clause: arm at ~120k context or ~60 tool calls (whichever first), cut at
    the next coherent boundary (green gate > commit > LOG/REPORT write), repeat every ~40-55
    calls, stop when <~40 calls remain. At the cut: continuation brief to
    nyxloom-trove/reports/nyxloom-P99-BRIEF.md + self-authored retention prompt to
    nyxloom-trove/reports/nyxloom-P99-COMPACT.md (both authorised touches), commit, return --
    do not resume/fork past the cut yourself. (Unlikely to be needed -- this is a small package.)"
---

# nyxloom-P99 — L10 per-project threshold override

## BLOCKED protocol

If any contract item below cannot be met exactly as specified, or an
`escalate_if` condition fires, stop and report **BLOCKED: <reason>** rather
than improvising a substitute. Do not silently narrow, widen, or reinterpret
a contract item.

## Context to read first

1. `nyxloom-trove/backlog/NL-3-l10-handoff-size-thresholds-are-hardcoded-constants-need-a-per.md`
   — full entry. Its "Proposed contract" and "Oracles" sections are the
   basis for this handoff; read it before Work item 1 so you understand
   *why* `[lint.l10]` is nested under `[lint]` rather than flat.
2. `src/nyxloom/lint.py` lines 60-75 (the L10 rule-catalogue comment) and
   lines 190-210 (`_check_l10`'s call site, showing `cfg` already in scope
   from the L7/L9 calls immediately above it) and lines 1078-1097
   (`_check_l10`'s current body).
3. `src/nyxloom/config.py`'s `NotifyConfig` dataclass (~line 87) — the
   style to mirror for the new `L10Config`. Also read
   `ProjectConfig.load`'s pipeline-validation block (search
   `validate_pipeline`) — the "config load fails loudly rather than the
   daemon planning an invalid flow" pattern this package's O3 must follow
   for malformed `[lint.l10]` values.
4. `tests/conftest.py`'s `sample_project` fixture (~line 96) — builds a
   real `ProjectConfig.load(root)` from `SAMPLE_PROJECT_TOML`. The new O1
   test does NOT need a new git-repo fixture: `ProjectConfig` is a plain
   dataclass, so `dataclasses.replace(sample_project, l10=L10Config(...))`
   is sufficient and much cheaper than writing a second on-disk project.
5. `tests/test_lint.py`'s `TestL10Size` class (~line 589) — the two
   existing tests already prove the current 10000/18000 defaults; do not
   duplicate their coverage, only add the override and malformed-config
   cases.

## Work

1. **Add `L10Config` to `config.py`.** A small `@dataclass` mirroring
   `NotifyConfig`'s style: `warn_tokens: int = 10000` and
   `error_tokens: int = 18000` (the tool-wide defaults, unchanged from
   today's hardcoded values in `lint.py`). Add
   `l10: L10Config = field(default_factory=L10Config)` to `ProjectConfig`.
2. **Parse and validate `[lint.l10]` at load time.** In
   `ProjectConfig.load`, read `data.get("lint", {}).get("l10", {})`,
   construct `L10Config(**that_dict)`, and validate immediately: if
   `warn_tokens >= error_tokens`, or either value is `<= 0`, raise
   `ValueError` with a message naming the actual values and why they're
   invalid — mirroring `validate_pipeline`'s load-time-failure pattern
   (Context item 3). Do not silently correct, swap, or ignore a malformed
   table. Absence of `[lint]` or `[lint.l10]` entirely is not
   malformed — it is the normal case, and must fall back to
   `L10Config()`'s defaults.
3. **Thread `cfg` into `_check_l10`.** Change its signature to
   `_check_l10(findings, path, full_text, cfg)`, use `cfg.l10.warn_tokens`
   and `cfg.l10.error_tokens` in place of the current hardcoded `10000`
   and `18000`, and update its call site (~line 206) to pass `cfg` — `cfg`
   is already in scope there (the L7 and L9 checks immediately above
   already receive it). Update the `message` string's rule text if it
   hardcodes either number (check `_check_l10`'s current body for a
   literal `10000`/`18000` in the message text, not just the comparison).
4. **Update the L10 rule-catalogue comment** (~line 68) to note the
   thresholds are per-project-configurable via `[lint.l10]`, defaulting to
   10000/18000.
5. **Tests.** In `TestL10Size`: add a test proving the override is read
   (O1 — reuse `test_huge_handoff_error`'s exact 80000-char/~20000-token
   body against a `ProjectConfig` built via `dataclasses.replace(
   sample_project, l10=config.L10Config(error_tokens=25000))`, assert the
   result is now a WARNING not an ERROR) and a test proving malformed
   config fails loudly at load time (O3 — two cases: `warn_tokens >=
   error_tokens`, and a non-positive value; both via `ProjectConfig.load`
   on a temp project.toml with an added `[lint.l10]` table, asserting
   `pytest.raises(ValueError)`). Confirm the two existing tests
   (`test_large_handoff_warning`, `test_huge_handoff_error`) still pass
   unmodified in substance (only the mechanical `_check_l10` signature
   change may touch their call, if `lint.lint_file` doesn't already
   isolate that) — this is O2.
6. **Schema.** Add a `lint` object to
   `src/nyxloom/schemas/nyxloom-config.schema.json`'s top-level
   `properties`, with a nested `l10` object declaring `warn_tokens`/
   `error_tokens` as `{"type": "integer", "exclusiveMinimum": 0}`. This is
   not required for the feature to function (top-level
   `additionalProperties` is already `true`), but matches the existing
   convention of declaring every known top-level TOML section (`notify`,
   `redact`, `stage`, `backlog_entries` are all declared this way) for
   schema-driven tooling parity.

## Scope / forbid

- **`docs/SPEC.md`'s L10 row** — already generic ("Handoff token size
  within project budget (warn, then block at 2×)"), names no specific
  number, and stays accurate regardless of whether the budget is the
  tool-wide default or a per-project override. Read and confirmed; no
  edit needed.
- **`tests/fixtures/handoffs/demo-P21-huge.md`** — the committed
  oversized fixture consumed by `test_lint.py`'s parametrized fixture
  sweep. It exercises the DEFAULT threshold path (its project has no
  `[lint.l10]`), unaffected by this change. Do not resize or touch it.

## Environment setup

Mode-B, this worktree only (`/workspaces/vbpub/.worktrees/nyxloom-nl3`,
branch `feat/nyxloom-P99-l10-per-project-thresholds`). No package image tag
needed. Gate runs via `./run-gate.py --worktree {worktree} tester-unified`.

## Gate argv (verbatim)

```
cd /workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified
```
