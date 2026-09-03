---
schema_version: 1
id: nyxloom-P99-l10-per-project-thresholds
project: nyxloom
title: "L10 handoff-size thresholds: per-project [lint.l10] override in nyxloom.toml"
tier: implement-2
input_revision: "5200be23"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-3-l10-handoff-size-thresholds-are-hardcoded-constants-need-a-per.md
scope:
  touch:
    - "src/nyxloom/config.py"          # add L10Config dataclass (mirrors NotifyConfig's style); add ProjectConfig.l10 field; parse+validate [lint.l10] in ProjectConfig.load AND ASSIGN THE RESULT to the `return cls(...)` block (lines ~450-474, the same block that threads `pipeline=pipeline`) -- fail loudly, mirrors validate_pipeline's load-time-ValueError pattern
    - "src/nyxloom/lint.py"            # _check_l10 gains a cfg: ProjectConfig parameter, reads cfg.l10.warn_tokens/error_tokens instead of the hardcoded 10000/18000, preserving strict `>` (not `>=`) at both boundaries; update its call site (~line 206, already has cfg in scope from the L7/L9 calls immediately above it) and the L10 rule-catalogue comment (~line 68)
    - "tests/test_lint.py"             # TestL10Size gains 4 new tests (O1 real-load override-raises, O3 malformed incl. the warn==error equality case, O4 override-lowers, boundary-value cases per B2/B3); the two EXISTING tests (test_large_handoff_warning, test_huge_handoff_error) already cover the no-override/default-fallback oracle (O2) via the plain sample_project fixture -- do not duplicate that, just confirm it still passes once cfg is threaded through. Also: fix the stale "// L10 warning becomes error over 12k" comment on the demo-P21-huge.md parametrize row (~line 971, TestGoldenCorpus) to say 18k -- pre-existing staleness unrelated to this package's own logic but found while sweeping this same file, cheap to fix in the same commit
    - "src/nyxloom/schemas/nyxloom-config.schema.json"  # add a `lint` top-level property, additionalProperties:false, with a nested `l10` object (also additionalProperties:false, matching the notify/policy/backlog_entries precedent -- lint/l10 are both static known keys, not the dynamic-key `stage` case), warn_tokens/error_tokens as `{"type":"integer","exclusiveMinimum":0}`, NEITHER required (O1's own fixture is a partial override -- error_tokens only -- so marking either required would make nyxloom lint's own CFG1 reject a real, intended usage)
  forbid:
    - "docs/SPEC.md"      # its L10 row ("Handoff token size within project budget (warn, then block at 2x)") is already generic -- it describes a per-project budget, doesn't hardcode a number, and stays accurate whether that budget is the tool-wide default or an override. Confirmed by reading it; no edit needed, and there is no other number to update
    - "tests/fixtures/handoffs/demo-P21-huge.md"  # the committed oversized fixture used by test_lint.py's parametrized fixture sweep (~line 971) -- it tests the DEFAULT threshold path (no [lint.l10] in that fixture's project), which is unaffected by this change; do not resize it
oracles:
  - id: O1
    observable: >-
      A REAL `ProjectConfig.load()` call (not `dataclasses.replace`) on a temp on-disk project
      whose `.nyxloom/project.toml` contains `[lint.l10]\nerror_tokens = 25000` (a PARTIAL
      override -- warn_tokens absent) produces a `cfg` with `cfg.l10.error_tokens == 25000` and
      `cfg.l10.warn_tokens == 10000` (the untouched default), and `lint.lint_file` on a handoff at
      the exact new boundary -- 25000 tokens (reuse/scale test_huge_handoff_error's body-sizing
      approach: `full_text` length // 4 == 25000) -- is L10 WARNING, not ERROR, while a handoff at
      25001 tokens IS L10 ERROR. This proves three things at once: the override reaches the
      instance `.load()` returns (not just an intermediate local), a partial override leaves the
      other field at its default, and the exact `>` (not `>=`) boundary survives
      parameterization.
    negative: >-
      The override reaching `L10Config(...)` but not the object `.load()` returns (B1) fails this
      oracle even if a DIRECT `dataclasses.replace`-built config would pass -- this oracle must go
      through `.load()`. A handoff at exactly 25000 tokens lint-ERRORing (an `>=` implementation)
      also fails this oracle.
    gate: tester-unified
  - id: O2
    observable: >-
      `test_large_handoff_warning` and `test_huge_handoff_error` (existing, unmodified except for
      whatever mechanical signature change `_check_l10` needs) still pass using the plain
      `sample_project` fixture (no `[lint.l10]` in its project.toml) -- proves the no-override
      fallback path is untouched (10000 warn / 18000 error, unchanged). Additionally, a new test
      confirms a handoff at exactly 10000 tokens is NOT flagged (neither warning nor error) and a
      handoff at exactly 18000 tokens is WARNING not ERROR -- pins today's strict-`>` boundary
      behavior (B2) as part of the frozen contract, not an implementation detail free to drift
      when the literals become variables.
    negative: >-
      Either existing test failing, or being deleted/rewritten to pass artificially (e.g.
      widening its body size to dodge a changed default), fails this oracle -- the DEFAULT numbers
      must not move. A `>=`-based reimplementation that flags exactly-10000 or exactly-18000 also
      fails this oracle even though it might still pass the two original (far-from-boundary)
      tests.
    gate: tester-unified
  - id: O3
    observable: >-
      `ProjectConfig.load` on a `nyxloom.toml` containing `[lint.l10]\nwarn_tokens = 20000\n
      error_tokens = 10000` (warn > error, malformed) raises `ValueError` at load time, before any
      handoff is linted. A second malformed case (either value `<= 0`, e.g. `error_tokens = -5`)
      also raises `ValueError` at load time. A THIRD case -- `warn_tokens == error_tokens` exactly
      (e.g. both `10000`) -- ALSO raises `ValueError`: the validation is `warn_tokens >=
      error_tokens`, not strict `>`, so equality is malformed too (this is a DIFFERENT boundary
      than O1/O2's `_check_l10` comparison, which stays strict `>` -- do not confuse the two;
      Work item 2 states this explicitly).
    negative: >-
      Silently swapping warn_tokens/error_tokens to make them consistent, silently ignoring the
      malformed table and falling back to defaults, or deferring the failure to lint-time instead
      of load-time all fail this oracle -- AGENTS.md 4.2a's fail-loudly analogue applies here. A
      validation using strict `warn_tokens > error_tokens` (permitting equality) fails the third
      case specifically.
    gate: tester-unified
  - id: O4
    observable: >-
      A real `ProjectConfig.load()` on a project whose `[lint.l10]` LOWERS both thresholds below
      the tool-wide defaults (`warn_tokens = 500`, `error_tokens = 1000`) lints a handoff of ~700
      tokens (far under the OLD 10000/18000 defaults, but between the new tighter numbers) as L10
      WARNING. This is NL-3's other stated direction ("a program that wants tighter handoffs") and
      must work identically to the raising case in O1 -- same code path, no special-cased
      direction.
    negative: >-
      An implementation that only threads the override through when it raises a threshold (e.g. a
      stray `max(cfg.l10.error_tokens, 18000)` guard, out of a mistaken belief the override can
      only raise never lower) passes O1 while failing this oracle -- both must be exercised, they
      are not redundant.
    gate: tester-unified
  - id: O5
    observable: >-
      `nyxloom lint`'s own config-schema check (CFG1 / `lint.lint_config`, whatever the actual
      entry point is named at implementation time -- see Context item 3) run against a
      `nyxloom.toml` declaring ONLY `[lint.l10]\nerror_tokens = 25000` (the same partial-override
      shape O1 uses) produces NO schema-validation finding. This is the schema-shape oracle Work
      item 6 needs: it proves `warn_tokens`/`error_tokens` were NOT marked `required` in the JSON
      schema (a partial override is legal) and that `lint`/`l10` were declared with
      `additionalProperties: false` without rejecting the legal partial shape.
    negative: >-
      A schema marking either key `required`, or a schema with a typo'd property name that
      silently falls through `additionalProperties: true` instead of being caught, fails this
      oracle. Never testing the schema against a real `[lint.l10]` table at all (only testing
      Work items 1-3's runtime behavior) does not close this gap even if O1-O4 all pass.
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
  - "the `return cls(...)` block in ProjectConfig.load (config.py ~450-474) does not accept a
    keyword matching the new field name without further edits elsewhere in that block -- verified
    clean at input_revision (the block already threads several parsed-and-validated locals the
    same way, e.g. pipeline=pipeline); if adding `l10=l10` there requires touching anything else
    in that constructor call, that is exactly the kind of coupling this carve should have named
    and didn't"
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
   — full entry. Its "Proposed contract" explicitly covers BOTH raising and
   lowering the ceiling (O1/O4 below); its "Oracles" section is the basis
   for this handoff. It also asks the carver to "check for [an existing
   per-project lint config precedent] before assuming none exists" —
   already checked at carve time: `src/nyxloom/schemas/nyxloom-config.schema.json`'s
   top-level `properties` (`project`, `gates`, `mutexes`, `policy`,
   `notify`, `refs`, `redact`, `stage`, `backlog_entries`) has no `lint`
   entry and no other per-project lint config exists anywhere in the
   codebase — `lint`/`l10` are new keys, not a rename or an extension of
   something that already exists.
2. `src/nyxloom/lint.py` lines 60-75 (the L10 rule-catalogue comment),
   lines 190-210 (`_check_l10`'s call site, showing `cfg` already in scope
   from the L7/L9 calls immediately above it), lines 1078-1097
   (`_check_l10`'s current body — note the strict `>` on both branches,
   not `>=`), and lines 953-990 (`TestGoldenCorpus`'s parametrized fixture
   sweep in `tests/test_lint.py`, a SECOND, previously-unnamed consumer of
   L10 behavior via the `demo-P21-huge.md` fixture — read-only for this
   package, its stale "12k" comment is a one-line fix, not a behavior
   change).
3. `src/nyxloom/config.py`'s `NotifyConfig` dataclass (~line 87) — the
   style to mirror for the new `L10Config`. Then read `ProjectConfig.load`'s
   pipeline-validation block (search `validate_pipeline`) for the
   load-time-`ValueError` pattern this package's O3 must follow, AND read
   all the way to the `return cls(...)` call (~lines 450-474) — the same
   block that threads `pipeline=pipeline` after validating it. **This is
   the exact site B1 of the carve review named: parsing and validating
   `[lint.l10]` is not enough on its own — the parsed `L10Config` must be
   passed into this specific constructor call, or the returned
   `ProjectConfig` keeps the default forever regardless of what any
   project's `nyxloom.toml` says.**
4. `tests/conftest.py`'s `sample_project` fixture (~line 96) — builds a
   real `ProjectConfig.load(root)` from `SAMPLE_PROJECT_TOML`, itself
   built by writing a real `.nyxloom/project.toml` to a real temp git
   repo. **O1, O3, and O4 all require this REAL `.load()` path, not
   `dataclasses.replace`** — the carve review's B1 finding is exactly that
   a `dataclasses.replace`-based oracle cannot observe whether the parse
   result actually reaches `.load()`'s return value. Build additional
   temp projects the same way `sample_project` does (a fresh `tmp_path`,
   a written `project.toml`, `git init`+`add`+`commit`) rather than
   reusing the fixture instance directly, since each oracle needs a
   DIFFERENT `[lint.l10]` table on disk.
5. `tests/test_lint.py`'s `TestL10Size` class (~line 589) — the two
   existing tests already prove the current 10000/18000 defaults; do not
   duplicate their coverage, only add the override/boundary/malformed
   cases named in the oracles above.

## Implementation packet (normative)

**Tracer bullet (carver-run, 2026-09-03, at input_revision `5200be23`):**
confirmed today's `ProjectConfig.load()` has no `l10` attribute at all and
silently drops a `[lint.l10]` table if one is present in
`.nyxloom/project.toml` — built a real temp project (via the same
`git init`+`add`+`commit` shape `sample_project` uses) with
`[lint.l10]\nerror_tokens = 25000` declared, called `ProjectConfig.load(root)`,
and confirmed `hasattr(cfg, "l10")` is `False`. This is the exact
acceptance negative O1 must witness turn from failing (today) to passing
(post-implementation) — the negative is real, not hypothetical.

**Owned interfaces:**
- `L10Config` (new, `config.py`): `@dataclass` with `warn_tokens: int =
  10000`, `error_tokens: int = 18000`. No methods; pure data, mirroring
  `NotifyConfig`.
- `ProjectConfig.l10: L10Config` (new field, `default_factory=L10Config`)
  — every existing construction site (13 in `tests/`, 0 in `src/`, all
  keyword-argument) is unaffected by a defaulted field.
- `_check_l10`'s new signature: `_check_l10(findings, path, full_text,
  cfg) -> None` (was `(findings, path, full_text)`).

**Construction/validation flow (the decision table B4/B5 asked for):**

| `[lint.l10]` state | Example | `ProjectConfig.load()` outcome |
|---|---|---|
| Absent (no `[lint]` or no `[lint.l10]`) | — | `l10 = L10Config()` (10000/18000 defaults) |
| Valid, full | `warn_tokens=5000, error_tokens=9000` | `l10 = L10Config(warn_tokens=5000, error_tokens=9000)` |
| Valid, partial | `error_tokens=25000` only | `l10 = L10Config(error_tokens=25000)` — `warn_tokens` stays the dataclass default `10000` |
| Malformed: `warn_tokens >= error_tokens` | `warn=20000, error=10000` OR `warn=10000, error=10000` | `ValueError` raised at load time, config never returned |
| Malformed: either value `<= 0` | `error_tokens=-5` | `ValueError` raised at load time, config never returned |

**Bounds:** token counts are `len(full_text) // 4` (unchanged, `lint.py`'s
existing estimate — this package does not change how tokens are counted,
only what they're compared against). No upper bound on a project's
declared `error_tokens`/`warn_tokens` beyond `warn_tokens < error_tokens`
and both `> 0` — a project raising its ceiling to an arbitrarily large
number is a legitimate use case NL-3 names, not a value to cap.

**Prepared proof / traceability:** O1 → the load-and-assign wiring +
partial-override default-fill + strict-`>` boundary, all three at once.
O2 → the untouched fallback path, including its own boundary. O3 → the
`>=`-validation fail-loudly path, including the equality case. O4 →
symmetry (lowering works exactly like raising). O5 → the schema shape
permits the exact partial-override table O1/O4 rely on.

**Degrees of freedom left to the implementer** (everything else is
fixed): the exact `ValueError` message text (must name the actual bad
values, per Work item 2, but exact wording is free); whether
`data.get("lint", {}).get("l10", {})` is read inline in `load()` or via a
small private helper function (either is fine, no test distinguishes
them); the exact schema `$comment`/`description` text for the new `lint`/
`l10` properties (must exist per the schema convention, wording is free).

## Work

1. **Add `L10Config` to `config.py`.** A small `@dataclass` mirroring
   `NotifyConfig`'s style: `warn_tokens: int = 10000` and
   `error_tokens: int = 18000` (the tool-wide defaults, unchanged from
   today's hardcoded values in `lint.py`). Add
   `l10: L10Config = field(default_factory=L10Config)` to `ProjectConfig`.
2. **Parse, validate, AND ASSIGN `[lint.l10]` at load time.** In
   `ProjectConfig.load`, read `data.get("lint", {}).get("l10", {})`,
   construct `l10 = L10Config(**that_dict)`, and validate immediately: if
   `warn_tokens >= error_tokens` (note: `>=`, so exact equality is ALSO
   malformed — a different boundary than `_check_l10`'s own strict `>`,
   see Work item 3), or either value is `<= 0`, raise `ValueError` with a
   message naming the actual values and why they're invalid — mirroring
   `validate_pipeline`'s load-time-failure pattern (Context item 3). Do
   not silently correct, swap, or ignore a malformed table. Absence of
   `[lint]` or `[lint.l10]` entirely is not malformed — it is the normal
   case, and must fall back to `L10Config()`'s defaults. **Then pass
   `l10=l10` into the `return cls(...)` constructor call** (~lines
   450-474 — the same block that threads `pipeline=pipeline`). This last
   step is the one the first carve draft never named explicitly and is
   not optional: without it, every other part of this Work item can be
   implemented correctly and the feature still does nothing in
   production (see the Implementation packet's tracer bullet and O1's
   negative).
3. **Thread `cfg` into `_check_l10`, preserving the strict `>` boundary.**
   Change its signature to `_check_l10(findings, path, full_text, cfg)`,
   use `cfg.l10.warn_tokens` and `cfg.l10.error_tokens` in place of the
   current hardcoded `10000` and `18000` — **keep the comparisons strict
   `>` on both branches, exactly as today** (a handoff at exactly
   `warn_tokens` or exactly `error_tokens` tokens is NOT flagged/escalated
   at that tier; this is today's real behavior and is part of the frozen
   contract, not an incidental detail free to drift when the literals
   become variables — O2 pins this explicitly). Update its call site
   (~line 206) to pass `cfg` — `cfg` is already in scope there. Update
   the `message` string's rule text if it hardcodes either number (check
   `_check_l10`'s current body for a literal `10000`/`18000` in the
   message text, not just the comparison).
4. **Update the L10 rule-catalogue comment** (~line 68) to note the
   thresholds are per-project-configurable via `[lint.l10]`, defaulting to
   10000/18000.
5. **Tests.** In `TestL10Size`, add (all via a REAL `ProjectConfig.load()`
   on a freshly-built temp project — see Context item 4 — never
   `dataclasses.replace` for any of these):
   - **O1**: a partial override (`error_tokens=25000` only) proving (a)
     the override reaches the loaded config, (b) `warn_tokens` stays at
     its default `10000`, and (c) the exact new boundary is strict `>`
     (25000 tokens → WARNING, 25001 → ERROR).
   - **O2's boundary addendum**: exactly-10000-token and exactly-18000-token
     handoffs against the plain `sample_project`/default config, proving
     neither is over-flagged.
   - **O3**: three malformed cases — `warn > error`, `warn == error`
     (the equality boundary — do not skip this one, it's the finding that
     failed the first carve draft), and a non-positive value — each
     via `pytest.raises(ValueError)` around `ProjectConfig.load`.
   - **O4**: a full override LOWERING both thresholds
     (`warn_tokens=500, error_tokens=1000`) proving a ~700-token handoff
     (far under the OLD defaults, between the NEW ones) is WARNING.
   - **O5**: run the schema/config validation check (find the actual
     entry point — likely `lint.lint_config` or similar; grep for what
     validates a project's `nyxloom.toml` against
     `nyxloom-config.schema.json`) against a project declaring the same
     partial `[lint.l10]\nerror_tokens=25000` table O1 uses, asserting no
     schema-validation finding is produced.
   - Fix the stale `# L10 warning becomes error over 12k` comment on the
     `demo-P21-huge.md` parametrize row (~line 971) to say `18k` — found
     during this package's own sweep of `test_lint.py`, unrelated to this
     package's logic but cheap to fix in the same commit.
   - Confirm the two existing tests (`test_large_handoff_warning`,
     `test_huge_handoff_error`) still pass unmodified in substance (only
     the mechanical `_check_l10` signature change may touch their call
     path).
6. **Schema.** Add a `lint` object to
   `src/nyxloom/schemas/nyxloom-config.schema.json`'s top-level
   `properties`: `"lint": {"type": "object", "additionalProperties":
   false, "properties": {"l10": {"type": "object", "additionalProperties":
   false, "properties": {"warn_tokens": {"type": "integer",
   "exclusiveMinimum": 0}, "error_tokens": {"type": "integer",
   "exclusiveMinimum": 0}}}}}`. **Neither `warn_tokens` nor
   `error_tokens` is `required`** — O1's own fixture is a partial override
   (`error_tokens` only), and marking either required would make
   `nyxloom lint`'s own schema check reject that legal, intended shape
   (this is exactly what B4 of the carve review flagged: don't mirror
   `backlog_entries`' `required` field without checking why it's required
   there — it's a semantic dependency specific to that section, not a
   style default to copy). `additionalProperties: false` at both the
   `lint` and `l10` levels mirrors `notify`/`policy`/`backlog_entries`'
   precedent (closed, static-key sections) rather than `stage`'s (open,
   because its keys are dynamic stage names — inapplicable here).

## Scope / forbid

- **`docs/SPEC.md`'s L10 row** — already generic ("Handoff token size
  within project budget (warn, then block at 2×)"), names no specific
  number, and stays accurate regardless of whether the budget is the
  tool-wide default or a per-project override. Read and confirmed; no
  edit needed.
- **`tests/fixtures/handoffs/demo-P21-huge.md`** — the committed
  oversized fixture consumed by `test_lint.py`'s parametrized fixture
  sweep. It exercises the DEFAULT threshold path (its project has no
  `[lint.l10]`), unaffected by this change. Do not resize or touch it —
  only the ADJACENT stale comment (Work item 5) needs a one-line fix.

## Environment setup

Mode-B, this worktree only (`/workspaces/vbpub/.worktrees/nyxloom-nl3`,
branch `feat/nyxloom-P99-l10-per-project-thresholds`). No package image tag
needed. Gate runs via `./run-gate.py --worktree {worktree} tester-unified`.

## Gate argv (verbatim)

```
cd /workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified
```
