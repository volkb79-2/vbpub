---
schema_version: 1
id: ciu-P18-config-check-hook-preflight
project: ciu
component: deploy
title: "Extend ciu check to walk the full config pipeline in-memory (V8 proposal §2.7 stages 1-6, 8-12; registry Pydantic stage 7 is a SEPARATE package, P19) with an optional hook validate_config(config, ctx) -> list[str] preflight contract, side-effect-free"
tier: implement-2
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-12", design_ref: "docs/CIU-V8-TESTING-GATE-PROPOSAL.md §2.7"}
stack: none
depends_on: [P17]
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/hooks_runner.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_deploy_actions.py"
    - "tests/tests/test_ciu_hooks_runner.py"
    - "docs/SPEC.md"
    - "docs/FEATURES.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P18-config-check-hook-preflight-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/composefile.py"
    - "src/ciu/config_model.py"
    - "src/ciu/provisioning.py"
    - "src/ciu/governance.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-side-effect-free
    observable: "ciu check (with the new stages active) creates NO hostdirs, materializes NO secrets to disk, invokes NO hook's run(), starts NO Docker container, and writes NO file anywhere (compose/overlay rendering happens in memory only). Verify this with a test that runs ciu check against a fixture stack in a tmp_path and asserts the tmp_path's filesystem tree is UNCHANGED before/after (a real regression-catching assertion, not a mocked-so-it-trivially-passes one)."
    negative: "reusing engine.main_execution(dry_run=True) as the implementation (verified at carve time: dry_run=True still creates hostdirs at STEP 8/17, still runs pre_secrets/pre_compose/post_compose hooks' run() for real, and only skips STEP 16's docker compose up -- this is NOT side-effect-free and is explicitly the exact problem this package exists to eliminate, per the backlog's own words: 'eliminates the need for consumers to run ciu up --dry-run, which still creates hostdirs and runs pre_secrets hooks')"
    gate: "tester-unified"
  - id: O2-stages
    observable: "action_check (deploy.py) is extended to run, for every selected+rendered stack, in-memory equivalents of these existing per-step validations (reuse the SAME functions main_execution already calls at each numbered step -- do not reimplement): stage 2 shape -> config_model.validate_stack_shape; stage 3 secrets grammar -> secret_directives.discover + find_misplaced (NOT secret_materialize -- no materialization); stage 4 provisioning -> already covered by action_check's existing lint_graph path, unchanged; stage 5 governance shape -> governance.resolve_stack_governance (shape/resolution only, no cgroup mutation); stage 6 configfile existence -> composefile's configfile-template-existence check (grep composefile.py for the function main_execution's relevant step calls; existence + schema-file-presence only, no rendering to disk); stage 10 compose render -> composefile.guard_config (S4.21, replaces secret values with SecretGuard sentinels BEFORE any render -- this is the existing mechanism that makes render safe without real secret values) + composefile.render_compose, in memory, not written to any file; stage 11 leak scan -> composefile.leak_scan against the in-memory guarded render; stage 12 consumption cross-check -> composefile.validate_consumption (same function engine.py's STEP 14 already calls). Registry validation (stage 7, Pydantic models) is EXPLICITLY OUT OF SCOPE for this package -- P19 adds it as a second pass over the same action_check extension point; leave a clearly marked insertion point/TODO-free stub comment naming P19, not a silent gap."
    negative: "reimplementing any of the named validations instead of importing/calling the existing function; silently skipping a stage instead of marking it explicitly deferred to P19; materializing real secret values anywhere in this path (the entire point of SecretGuard/S4.21 is that a template render never needs one)"
    gate: "tester-unified"
  - id: O3-hook-preflight
    observable: "hooks_runner.py's load_hook is refactored to share a private module-loading step (e.g. _load_hook_module(path) -> ModuleType, the import/exec_module machinery load_hook already has) so a hook file is imported EXACTLY ONCE per ciu check run, never twice (double-importing a hook module could double-execute module-level side effects, which the S9.2/[S9.1] hook contract does not forbid at import time -- import-time code is a hook author's business, but this package must not introduce a SECOND import of files that already import cleanly once). A new function (name your choice, e.g. load_hook_for_check(path) -> tuple[Callable, Callable | None]) returns (run, validate_config_or_None) from ONE module load, reusing load_hook's existing [S9.1]/[S9.2] error semantics for the 'file missing' / 'no run() or Hook class' cases (those still apply during ciu check -- stage 8 in the proposal's table). validate_config, when present, is called as validate_config(config, ctx) -> list[str] with the SAME merged (guarded) config and a HookContext instance as run() would receive during a real up, MINUS materialized secret file paths (ctx.secret_file callback: decide whether it should raise KeyError for every name unconditionally during check, since no file was ever written -- name this decision explicitly in your LOG, it's a real design choice, not a bug). validate_config MUST NOT be treated as raising exceptions to catch -- it returns list[str] (empty = OK); an exception escaping validate_config's own body is instead reported as a hook-preflight failure naming the exception (do not let one broken hook's validate_config crash the whole ciu check run for every other stack/hook)."
    negative: "importing the hook file twice (once for run, once for validate_config); treating validate_config's return value as a boolean instead of a list of error strings; one hook's validate_config exception aborting the ENTIRE ciu check invocation instead of being reported as that hook's own failure"
    gate: "tester-unified"
  - id: O4-cli-and-exit-codes
    observable: "ciu check keeps its EXISTING flags/exit-code contract exactly (S13.4: 0 clean / 1 live-probe failure / 2 graph-lint-or-config error) -- the new stages' failures map to exit 2 (they are all static/config-shape errors, same class as today's graph-lint failure), NEVER exit 1 (reserved for --live's live PROBE failures, unrelated to these new stages). --json output gains the new stage results in a versioned envelope alongside the existing graph-lint result (top-level schema_version, one entry per stage with pass/fail + findings -- match the proposal's prose-output stage list's granularity: render/shape/secrets/provisioning/governance/configfile/hooks-load/hooks-preflight/compose-render/leak-scan/consumption)."
    negative: "a new stage's failure returning exit 1 (collides with --live's meaning) or exit 0 (silently passing over a real config defect)"
    gate: "tester-unified"
  - id: O5-docs
    observable: "docs/SPEC.md documents the extended ciu check (new subsection referencing S13.4 and S9 for the validate_config hook contract addition) and the validate_config(config, ctx) -> list[str] optional hook entry point under S9. docs/CONSUMERS.md gets a worked example (a hook implementing validate_config, mirroring the proposal's db-core example). docs/FEATURES.md's ciu check row is updated. CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md CIU-QOL-12 row -> FIXED-partial (stage 7/registry explicitly deferred to P19, name it) with evidence."
    negative: "documenting ciu check as now fully implementing all 12 proposal stages when stage 7 is deliberately deferred -- say so plainly"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "a named existing function (composefile.guard_config, render_compose, leak_scan, validate_consumption, governance.resolve_stack_governance, or the configfile-existence check) has a signature that assumes real materialized secrets or real disk paths in a way that cannot be satisfied in-memory without a change to a FORBIDDEN file -- BLOCKED naming the exact incompatible assumption; do not work around it by materializing real secrets"
  - "a hook's validate_config genuinely needs a materialized secret's real path (not just to know it's declared) to do useful validation -- this is a real, product-relevant limitation the V8 proposal's own example (checking registry.postgresql.users keys exist, not secret file contents) does not hit; if you find a case that does, name it as a documented limitation in SPEC.md rather than materializing secrets to work around it, and note it in the LOG as a candidate follow-up, not a blocker"
mutexes: [merge-lane]
review_focus:
  - "the O1 filesystem-unchanged test is real (asserts an actual tmp_path tree snapshot, not a mock call count) -- this is THE oracle that proves the package's entire reason for existing"
  - "no hook file is imported twice by the same ciu check run (would double-execute module-level code)"
  - "one hook's validate_config exception does not abort every other stack's check in the same invocation"
  - "exit code discipline: no new stage failure ever returns 1 (that's reserved for --live)"
---

# ciu-P18 — `ciu check` full config validation + hook preflight (CIU-QOL-12)

## Context to read first
1. `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §2.7 (search `### 2.7`, ~line
   648-735) — READ IN FULL. This is the design source: the 12-stage table,
   the `validate_config(config, ctx) -> list[str]` contract, and the prose
   output shape. This handoff routes stage 7 (registry Pydantic) to a
   SEPARATE package (P19) because it is a large, independent unit of work
   (5 new model classes + a new optional dependency) that would make this
   package too big per AUTHORING.md's "keep it small" rule — everything else
   (stages 1-6, 8-12) is yours.
2. `docs/BACKLOG-2026-08-24.md#CIU-QOL-12` — the backlog framing (already in
   your context via `source`), consistent with the proposal.
3. `src/ciu/engine.py` `main_execution` (~1140-1600+) — READ IN FULL, WITH
   ITS STEP COMMENTS (`# ---- Step N/17: ... ----`). This is your map: each
   proposal stage corresponds to one or more of these existing steps' inner
   logic. **Critical finding from this wave's carve**:
   `main_execution(dry_run=True)` is NOT side-effect-free — it still creates
   hostdirs (Step 8), still runs real hook `run()` calls (pre_secrets/
   pre_compose/post_compose), and only skips Step 16 (`docker compose up`).
   Do NOT build this package by calling `main_execution(dry_run=True)` — that
   reproduces the exact defect QOL-12 exists to fix. Instead, call the
   INDIVIDUAL validation/render functions each step already delegates to,
   stopping before Step 6 (reset), Step 8 (hostdirs), and any secret
   materialization / hook `run()` invocation.
4. `src/ciu/composefile.py` — `SecretGuard` (S4.21, ~line 90-105): the
   EXISTING mechanism that lets compose/hook contexts render against a
   guard sentinel instead of a real secret value (a guard object raises if
   accidentally stringified — see line ~96). `guard_config` and
   `render_compose` (S4.21, referenced at engine.py Step 13) are what
   `main_execution` itself uses for the REAL render — reuse them here for
   the in-memory validation render (stage 10). `leak_scan` (S4.22) and
   `validate_consumption` (S4.20, called at engine.py ~Step 14, line
   ~1500-1511) are the stage 11/12 functions.
5. `src/ciu/hooks_runner.py` — `load_hook` (~111-165) in full. Note it
   imports the module and returns ONLY the `run` callable — you need BOTH
   `run` (to confirm it exists, stage 8) and `validate_config` (stage 9,
   optional) from ONE import. Refactor to share the module-loading step
   without double-importing (see oracle O3 for the exact contract).
6. `src/ciu/deploy.py` `action_check` (~1688-1770) — today's implementation
   (provisioning graph lint only). This is what you extend; preserve its
   existing behavior and exit-code contract for the graph-lint part
   unchanged, adding the new stages alongside it.
7. `docs/SPEC.md` S9 (Hooks, ~line 995-1034) and S13.4 (`ciu check`'s
   existing exit-code contract, search `S13.4`) — the two normative sections
   you extend.

## Implementation packet (normative)

### Owned interfaces
- `hooks_runner.py`: `_load_hook_module(path: Path) -> ModuleType` (private,
  extracted from `load_hook`'s existing body). `load_hook_for_check(path:
  Path) -> tuple[Callable, Callable | None]` — `(run, validate_config)`,
  `validate_config` is `None` when the module doesn't define it. Same
  `[S9.1]`/`[S9.2]` errors as `load_hook` for the missing-file/no-run cases.
- `deploy.py`: extend `action_check`'s signature only if needed (prefer
  keeping it stable) to walk stages 2,3,5,6,8,9,10,11,12 in-memory per
  selected+rendered stack, aggregating findings into the existing return/
  exit-code contract (O4).

### Decision table (stage -> reused function -> failure exit code)
| stage | function reused | exit on failure |
|---|---|---|
| 2 shape | `config_model.validate_stack_shape` | 2 |
| 3 secrets grammar | `secret_directives.discover` + `find_misplaced` | 2 |
| 4 provisioning | existing `lint_graph` path (unchanged) | 2 |
| 5 governance shape | `governance.resolve_stack_governance` | 2 |
| 6 configfile existence | composefile's existence/schema-presence check (locate exact function/step) | 2 |
| 8 hooks load | `load_hook_for_check` per declared hook path | 2 |
| 9 hooks validate_config | call `validate_config(guarded_config, ctx)` when present; aggregate `list[str]` | 2 |
| 10 compose render | `composefile.guard_config` + `render_compose`, in memory | 2 |
| 11 leak scan | `composefile.leak_scan` on the in-memory render | 2 |
| 12 consumption | `composefile.validate_consumption` | 2 (WARN today per engine.py — decide and document whether `ciu check` treats this as a hard failure or keeps it a warning; engine.py's real pipeline only WARNS at Step 14, so matching that precedent — warning, not exit 2 — is defensible; state your choice and why in the LOG) |
| --live (existing) | unchanged | 1 |

### Degrees of freedom
Exact internal function/helper names in `deploy.py`; whether stages run in
one big function or several small ones; whether `--json`'s per-stage
envelope nests stages under a list or a dict keyed by stage name (pick one,
document it). NOT a degree of freedom: side-effect-freedom (O1), the
exit-code reservation of `1` for `--live` only (O4), and single-import hook
loading (O3).

## Work
1. `hooks_runner.py`: `_load_hook_module` + `load_hook_for_check` (O3).
2. `deploy.py`: extend `action_check` per the packet, explicitly marking the
   stage-7/registry insertion point for P19 (O2).
3. `cli.py`: wire any new flags `action_check` needs (likely none — reuse
   existing `ciu check [--profile NAME] [--live] [--json]`).
4. Tests: the O1 filesystem-unchanged proof (required, not optional); one
   fixture per stage's failure mode; the hook-preflight double-import guard;
   one hook's `validate_config` exception not aborting the run.
5. Docs per O5.
6. LOG at `nyxloom-trove/reports/ciu-P18-config-check-hook-preflight-LOG.md`,
   with an explicit "Design decisions made" section naming: the
   `ctx.secret_file` behavior during check (escalate_if #2's territory if it
   bites), and the consumption-cross-check exit-code choice.

## Environment setup
Same worktree/venv as prior packages:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/python run-ciu-tests.py`

## BLOCKED rule
Per `escalate_if` above. Forbidden workaround: calling
`main_execution(dry_run=True)` or otherwise letting any real hostdir
creation, secret materialization, or hook `run()` execution happen during
`ciu check`.
