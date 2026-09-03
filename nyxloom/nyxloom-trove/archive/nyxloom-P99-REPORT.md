# nyxloom-P99 — REPORT (L10 per-project threshold override, NL-3)

Handoff: `nyxloom-trove/handoffs/nyxloom-P99-l10-per-project-thresholds.md`
(input_revision `e3baff00`, frozen at `9ceb6eb9`). Branch:
`feat/nyxloom-P99-l10-per-project-thresholds`. Worktree:
`/workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom`.

Code/schema/test commits (in order; see `nyxloom-P99-LOG.md` for one
entry per commit): `e3428c11`, `d6cf8334`, `e7e3c888`, `6739f8c1`,
`9657bc7a`. Plus one doc commit (`79725379`, an interim LOG.md snapshot
needed to clear the worktree for the second gate attempt — see LOG) and
this REPORT's own commit (made after this file was written, per the
self-hash rule — see LOG's final entry for its hash).

## Gate (tester-unified) — GREEN

```
cd /workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom && \
  ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified
```

Two runs:

1. **At commit `6739f8c1` (before the reorder fix): FAIL.** Verdict:
   `tester-unified: FAIL/UNCOVERED_LINES (exit 1)`. R1 (changed-line
   coverage, `fail_under=100.0`) reported `config.py` lines 483-487
   (the non-positive-value branch's body) uncovered: with
   `[lint.l10] error_tokens = -5` (warn_tokens defaulting to 10000), the
   `warn_tokens >= error_tokens` check fired first (10000 >= -5), so the
   dedicated non-positive check was structurally unreachable by any of
   the three O3 fixtures under the original check order.
2. **At commit `79725379` (after commit `9657bc7a` reordered the two
   `[lint.l10]` validation checks, non-positive first): PASS.** Read in a
   separate step from launching it (never through a piped
   tail/grep that could mask a non-zero exit code):

   ```
   $ tail -5 gate-run3.log
   tester-unified: PASS (exit 0)
     commit: 797253797d9984eb63d756b6e5c4c9ff2d45d6ea
     argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
   run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom/.assay/verdict-tester-unified.json
   run-gate: lane 'tester-unified' exit 0
   ```

   `.assay/verdict-tester-unified.json`: `"outcome": "PASS"`,
   `"exit_code": 0`, `"commit": "797253797d9984eb63d756b6e5c4c9ff2d45d6ea"`
   (= `79725379`, HEAD at gate time). Claims:
   - R0 (`tests-pass`): `status: PASS`.
   - R1 (`changed-line-coverage`, `mode: changed_lines`,
     `fail_under: 100.0`): `status: PASS`, `"pct": 100.0`,
     `"covered": 25`, `"executable": 25`, `"missing_lines": {}` — the
     483-487 gap from run 1 is gone.

   Gate containers were capped `docker update --cpus=3` immediately after
   launch both times (host-shared-with-production-game-server rule) and
   were removed automatically by run-gate.py on completion (confirmed no
   stray `run-gate-vbpub-tester-unified-*` container survives either run).

## Per-oracle evidence

All five oracles were independently re-run as their own pytest node
(`-o addopts=""` to defeat the repo's default `-q`, so per-test PASS/FAIL
is visible), in addition to passing as part of the full gate run above.

### O1 — partial override reaches `.load()`, partial-default fill, new strict-`>` boundary

```
$ python3 -m pytest tests/test_lint.py::TestL10Size::test_o1_partial_override_reaches_load_and_pins_new_boundary -v -o addopts=""
tests/test_lint.py::TestL10Size::test_o1_partial_override_reaches_load_and_pins_new_boundary PASSED
```

The test builds a real on-disk project (git init+add+commit) with
`.nyxloom/project.toml` containing `[lint.l10]\nerror_tokens = 25000`
(warn_tokens absent), calls the REAL `ProjectConfig.load(root)` (never
`dataclasses.replace`), and asserts `cfg.l10.error_tokens == 25000` and
`cfg.l10.warn_tokens == 10000`; then lints a handoff sized to exactly
25000 tokens (WARNING, not ERROR) and one at 25001 tokens (ERROR).

Independently re-verified outside pytest, directly against the final code:

```
O1: cfg.l10.error_tokens = 25000 (expect 25000)
O1: cfg.l10.warn_tokens  = 10000 (expect 10000, untouched default)
```

Negative check: the wrong implementation the carve review named (parse +
validate `[lint.l10]` but never assign `l10=l10` into `cls(...)`'s
return) was the actual state of the code before commit `e3428c11`'s own
tracer-bullet re-run (`hasattr(cfg, "l10")` was `False`) — see LOG entry
for commit `e3428c11`.

### O2 — no-override fallback untouched, default boundary pinned

```
$ python3 -m pytest tests/test_lint.py::TestL10Size::test_large_handoff_warning tests/test_lint.py::TestL10Size::test_huge_handoff_error tests/test_lint.py::TestL10Size::test_default_thresholds_boundary_values -v -o addopts=""
tests/test_lint.py::TestL10Size::test_large_handoff_warning PASSED
tests/test_lint.py::TestL10Size::test_huge_handoff_error PASSED
tests/test_lint.py::TestL10Size::test_default_thresholds_boundary_values PASSED
```

The two pre-existing tests are unmodified in substance (only the
mechanical `_check_l10(..., cfg)` signature change touches their call
path) and still pass against the plain `sample_project` fixture (no
`[lint.l10]`). The new boundary test confirms a handoff at exactly 10000
tokens produces no L10 finding at all, and one at exactly 18000 tokens is
WARNING, not ERROR — pinning the strict-`>` default boundary.

### O3 — malformed `[lint.l10]` fails loudly at load time, all three cases

```
$ python3 -m pytest tests/test_lint.py::TestL10Size::test_o3_malformed_warn_greater_than_error_raises tests/test_lint.py::TestL10Size::test_o3_malformed_warn_equals_error_raises tests/test_lint.py::TestL10Size::test_o3_malformed_non_positive_raises -v -o addopts=""
tests/test_lint.py::TestL10Size::test_o3_malformed_warn_greater_than_error_raises PASSED
tests/test_lint.py::TestL10Size::test_o3_malformed_warn_equals_error_raises PASSED
tests/test_lint.py::TestL10Size::test_o3_malformed_non_positive_raises PASSED
```

Each case builds a real on-disk project with a malformed `[lint.l10]`
table and asserts `pytest.raises(ValueError)` around `ProjectConfig.load`.
The equality case (`warn_tokens == error_tokens == 10000`) — the finding
that failed the first carve draft — is included and independently
re-verified outside pytest:

```
O3 equality case: ValueError raised as expected: [lint.l10]: warn_tokens (10000) must be strictly less than error_tokens (10000)
```

The non-positive case required a production-code fix (commit `9657bc7a`,
see LOG) to actually exercise its own distinct branch rather than being
silently caught by the ordering check first — see the Gate section above
for how this was caught (R1/UNCOVERED_LINES on the first gate run) and
fixed.

### O4 — override direction is symmetric (lowering works identically to raising)

```
$ python3 -m pytest tests/test_lint.py::TestL10Size::test_o4_lowered_thresholds_apply_symmetrically -v -o addopts=""
tests/test_lint.py::TestL10Size::test_o4_lowered_thresholds_apply_symmetrically PASSED
```

`[lint.l10] warn_tokens = 500, error_tokens = 1000` (both below the
tool-wide defaults) loads correctly (`cfg.l10.warn_tokens == 500`,
`cfg.l10.error_tokens == 1000`) and a ~700-token handoff (far under the
OLD 10000/18000 defaults, between the NEW tighter numbers) lints as L10
WARNING, not clean and not ERROR. This defeats a `max(cfg.l10.error_tokens,
18000)`-style "raise-only" clamp, which the O1 fixture alone cannot rule
out.

### O5 — schema shape permits the exact partial-override table O1/O4 rely on

```
$ python3 -m pytest tests/test_lint.py::TestL10Size::test_o5_schema_accepts_partial_l10_override -v -o addopts=""
tests/test_lint.py::TestL10Size::test_o5_schema_accepts_partial_l10_override PASSED
```

Runs `lint.lint_config(cfg)` (the real CFG1 entry point, `lint.py:326`,
independently confirmed by the fix-verification round and re-confirmed
here by reading `lint.py`) against a project declaring
`[lint.l10]\nerror_tokens = 25000` and asserts no `CFG1` finding.
Confirmed independently via `python3 -m nyxloom.cli lint` against
nyxloom's own `nyxloom-trove/nyxloom.toml` (no `[lint.l10]` there, so this
is a baseline-cleanliness check, not O5 itself): zero CFG1 findings,
confirming the new schema addition introduces no regression on the
project's own existing config.

## Full test-file run

```
$ python3 -m pytest tests/test_lint.py -q
84 passed, 1 warning in 1.86s
```

(77 pre-existing + 7 new `TestL10Size` tests = 84; the golden-corpus stale
"12k" -> "18k" comment fix does not change any assertion, confirmed by
`TestGoldenCorpus::test_golden_corpus[demo-P21-huge.md-L10-False]` still
passing individually.)

## Files touched in `config.py` / `lint.py` / the schema

- **`src/nyxloom/config.py`**:
  - New `L10Config` dataclass (mirrors `NotifyConfig`'s style):
    `warn_tokens: int = 10000`, `error_tokens: int = 18000`.
  - New `ProjectConfig.l10: L10Config = field(default_factory=L10Config)`
    field.
  - `ProjectConfig.load`: reads `data.get("lint", {}).get("l10", {})`,
    constructs `L10Config(**l10_data)`, validates (non-positive check
    first, then ordering check — see the reorder-fix commit), raises
    `ValueError` naming the actual bad values on either failure, and
    passes `l10=l10` into the `return cls(...)` block.
- **`src/nyxloom/lint.py`**:
  - `_check_l10`'s signature gained a `cfg: ProjectConfig` parameter;
    comparisons now read `cfg.l10.error_tokens`/`cfg.l10.warn_tokens`
    instead of the hardcoded `18000`/`10000`, strict `>` preserved on
    both branches.
  - Call site (`lint_file`) updated to pass `cfg` through (already in
    scope from the immediately-preceding L7/L9 calls — no signature
    change needed anywhere else).
  - L10 rule-catalogue module-docstring comment (~line 68) updated to
    document the per-project override and its defaults.
- **`src/nyxloom/schemas/nyxloom-config.schema.json`**: new top-level
  `lint` object (`additionalProperties: false`), nested `l10` object
  (`additionalProperties: false`), `warn_tokens`/`error_tokens` as
  `{"type": "integer", "exclusiveMinimum": 0}`, neither `required`.
- **`tests/test_lint.py`**: 2 new module-level helpers
  (`_l10_project_root`, `_handoff_text_at_token_count`), 7 new
  `TestL10Size` tests, 1 stale-comment fix (`TestGoldenCorpus`,
  "12k" -> "18k").

No file outside this list was touched. `docs/SPEC.md` and
`tests/fixtures/handoffs/demo-P21-huge.md` (forbid list) were confirmed
untouched (`git diff --stat` against `b42bd8a3` shows exactly the four
files above, plus this package's own LOG/REPORT).

## Orientation telemetry

- Read, in order: the handoff (frontmatter + body), the NL-3 backlog
  entry, `nyxloom-P99-CARVE-REVIEW.md` (verdict: NOT READY, B1-B5 +
  §4 findings), `nyxloom-P99-FIX-VERIFICATION.md` (verdict: READY, all
  prior findings confirmed resolved, one non-blocking residual on O5's
  one-sidedness).
- Independently reproduced the packet's tracer bullet BEFORE editing
  anything: built a real on-disk project (git init+add+commit) with
  `[lint.l10]\nerror_tokens = 25000`, confirmed `hasattr(cfg, "l10")` is
  `False` on unmodified code — the acceptance negative is real.
- Independent sweep for anything the two review rounds might have
  missed (per the dispatch prompt's step 4): re-confirmed `cfg` is in
  scope at `_check_l10`'s call site, all 13 `ProjectConfig(...)`
  construction sites in `tests/` are keyword-only (unaffected by the new
  defaulted field), the `return cls(...)` block accepts a new keyword
  cleanly, and `docs/SPEC.md`'s L10 row needs no edit. No new gap beyond
  the fix-verification round's own documented, accepted-as-non-blocking
  O5 one-sidedness (malformed/unknown-key rejection not tested) was
  found — that gap was left as-is per the handoff's own explicit
  instruction not to close it.
- One real gap WAS found during implementation, not by inspection but by
  the gate itself: the non-positive validation branch was dead code
  under the original check order (see Gate section and LOG). This was
  not anticipated by either review round or by my own pre-edit sweep —
  found only because the gate's R1 (changed-line coverage) lane caught
  it, which is exactly what that lane is for. Fixed in commit `9657bc7a`
  with no test changes and no behavior change (same fixtures still raise
  `ValueError`; only which branch each one hits internally changed).
- Total tool-call footprint for this package: well under the ~60-call /
  ~120k-context checkpoint threshold (E-008) — no BRIEF/COMPACT files
  were needed.

## Status

All 6 Work items complete. All 5 oracles (O1-O5) independently verified
via their own real `ProjectConfig.load()` on a freshly-built on-disk
project (never `dataclasses.replace`), both individually and as part of
the full `tester-unified` gate, which is GREEN (`exit 0`, R0 PASS, R1
PASS at 100% changed-line coverage). Not merging — leaving for review per
instructions.
