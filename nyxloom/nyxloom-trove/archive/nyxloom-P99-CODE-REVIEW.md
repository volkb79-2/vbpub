# nyxloom-P99 — code review (diff vs. frozen handoff)

Handoff: `nyxloom-trove/handoffs/nyxloom-P99-l10-per-project-thresholds.md`,
frozen at `9ceb6eb9` (input_revision `e3baff00`). Reviewed diff:
`git diff 9ceb6eb9..HEAD` at branch tip `276ab254`. Prior rounds: carve
review (`nyxloom-P99-CARVE-REVIEW.md`, NOT READY) and fix-verification
(`nyxloom-P99-FIX-VERIFICATION.md`, READY). This is the code-review round:
the actual diff against the frozen contract, not the handoff's text.

**Verdict: ACCEPT.**

---

## Blind-phase method (before reading LOG.md/REPORT.md)

1. Read `git diff 9ceb6eb9..HEAD -- .` in full (`config.py`, `lint.py`,
   the schema, `test_lint.py`).
2. Rebuilt every fixture for O1, O3, O4, O5 **myself**, from scratch, with
   my own script — real `git init`+`add`+`commit` temp projects and a real
   `ProjectConfig.load(root)` call, never the implementer's test helpers
   or `dataclasses.replace` — and confirmed each oracle's claimed behavior
   independently.
3. Planted three targeted mutation-evasion probes directly against the
   shipped code (apply mutation → run tests → `git checkout --` to
   revert), specifically re-testing the exact wrong implementations named
   in the carve review and fix-verification rounds.
4. Re-ran the real gate myself (`./run-gate.py --worktree
   /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified`), capped the
   container at 3 CPUs immediately after launch (host-shared-with-
   production rule), and read the verdict in a separate step from
   launching it.
5. Only then read `nyxloom-P99-LOG.md`/`REPORT.md` and reconciled.

## 1. Diff vs. Work items — traceability

| Work item | Diff location | Match |
|---|---|---|
| 1. `L10Config` dataclass + `ProjectConfig.l10` field | `config.py` new dataclass (`warn_tokens: int = 10000`, `error_tokens: int = 18000`) + `l10: L10Config = field(default_factory=L10Config)` | Exact |
| 2. Parse/validate/**assign** `[lint.l10]` at load time | `config.py` `ProjectConfig.load`: `l10_data = data.get("lint", {}).get("l10", {})`, `l10 = L10Config(**l10_data)`, two `ValueError` branches, then `l10=l10` threaded into `return cls(...)` | Exact — the exact B1 site is touched |
| 3. Thread `cfg` into `_check_l10`, strict `>` preserved | `lint.py` signature now `(findings, path, full_text, cfg)`; both comparisons read `cfg.l10.error_tokens`/`cfg.l10.warn_tokens`; call site (`lint_file`) updated | Exact |
| 4. Rule-catalogue comment update | `lint.py:68-71` docstring rewritten to reference `cfg.l10.*` and defaults | Exact |
| 5. Tests + stale-comment fix | `test_lint.py`: 2 helpers + 7 new `TestL10Size` tests (O1, O2 addendum, O3×3, O4, O5) + `demo-P21-huge.md` comment "12k"→"18k" | Exact |
| 6. Schema | `nyxloom-config.schema.json`: `lint`/`l10` both `additionalProperties: false`, neither key `required`, `exclusiveMinimum: 0` | Exact, byte-for-byte matches Work item 6's literal JSON |

No file outside `scope.touch` was modified. `docs/SPEC.md` and
`tests/fixtures/handoffs/demo-P21-huge.md` (forbid list) — confirmed
untouched by `git diff 9ceb6eb9..HEAD -- docs/SPEC.md
tests/fixtures/handoffs/demo-P21-huge.md` (empty). The frozen handoff file
itself is untouched (empty diff) — frontmatter and body remain in
agreement with what was reviewed and re-frozen. The 13 pre-existing
`ProjectConfig(...)` construction sites (10 files under `tests/`, 0 under
`src/`) are untouched by this diff; the full-suite gate (which exercises
all of them via `pytest tests -n auto`) passed, so nothing broke.

## 2. Independent oracle re-execution (not the implementer's tests)

All five built and run from a standalone script, real `ProjectConfig.load()`
on a fresh on-disk temp project each time:

- **O1**: `[lint.l10]\nerror_tokens = 25000` (partial) →
  `cfg.l10.error_tokens == 25000`, `cfg.l10.warn_tokens == 10000` confirmed;
  a handoff at exactly 25000 tokens (independently constructed, not reusing
  the implementer's `_handoff_text_at_token_count` helper) is WARNING only,
  25001 tokens is ERROR. **Confirmed.**
- **O3**, all three cases (`warn=20000/error=10000`, `warn=error=10000`,
  `error=-5`) each independently raise `ValueError`. **Confirmed.**
- **O4**: `warn_tokens=500, error_tokens=1000` loads correctly and a
  700-token handoff is WARNING. **Confirmed.**
- **O5**: `lint.lint_config(cfg)` on the same partial-override project
  produces zero `CFG1` findings. **Confirmed**, and I independently
  verified `lint.lint_config` really is the live CFG1 entry point wired
  into `lint_project` (`lint.py:241`), not a dead/parallel function.
- **O2** (boundary addendum): exactly-10000-token handoff against the
  unmodified default config produces no L10 finding at all; exactly-18000
  is WARNING not ERROR. **Confirmed**, plus the two pre-existing tests
  (`test_large_handoff_warning`, `test_huge_handoff_error`) are absent
  from the diff entirely — genuinely unmodified.

## 3. Evasion probes (mutation testing against the shipped code)

Three targeted mutations, each reverted with `git checkout --` after
observing the result:

1. **`_check_l10`'s `>` → `>=` on both branches** (the B2 evasion): failed
   `test_o1_partial_override_reaches_load_and_pins_new_boundary` and
   `test_default_thresholds_boundary_values`. Caught.
2. **Ordering validator `>=` → `>` (permit equality)** (the B3 evasion):
   failed `test_o3_malformed_warn_equals_error_raises` with `DID NOT RAISE
   ValueError`. Caught.
3. **Drop `l10=l10` from `return cls(...)`** (the exact B1 evasion named
   in the original carve review — the single most important thing this
   round had to verify): failed `test_o1_partial_override_reaches_load_and_pins_new_boundary`
   (`cfg.l10.error_tokens` read back as `10000`, not `25000`) and
   `test_o4_lowered_thresholds_apply_symmetrically`. **Caught by two
   independent oracles.** This is direct proof the wiring gap the whole
   repair cycle was about is actually closed in the shipped code, not
   merely claimed.

No hollow tests found: every assertion checks a behavioral outcome
(`cfg.l10.*` values, actual `LintFinding` severities, `ValueError`
presence/absence) — none asserts a call count, private attribute, or log
string.

## 4. The reordering fix — independently verified correct and complete

The coordinator's specific ask: plant a case where BOTH values are
non-positive AND `warn >= error`, and confirm the error message names the
right cause; confirm both fields are checked independently.

Ran five additional probes directly against `ProjectConfig.load`:

| Fixture | Result |
|---|---|
| `warn=-5, error=-5` (both negative, equal) | `ValueError: ... must both be > 0` — non-positive cause correctly reported, not the ordering message |
| `warn=-1, error=-100` (both negative, warn > error) | same — non-positive cause reported |
| `warn=-5, error=18000` (only warn non-positive, ordering otherwise fine) | raises — non-positive check catches it independently of `error_tokens` |
| `warn=0, error=18000` | raises — zero correctly treated as non-positive (`<= 0`, not `< 0`) |
| `warn=5000, error=0` | raises — `error_tokens` checked independently of `warn_tokens` |

`config.py`'s check is a single `if l10.warn_tokens <= 0 or
l10.error_tokens <= 0: raise ...` followed by a separate `if
l10.warn_tokens >= l10.error_tokens: raise ...` — both fields are checked
by one boolean `or` (independent, complete), and the non-positive check
runs first so it can never be shadowed by the ordering check, closing the
exact dead-code path the gate's R1 lane caught during implementation
(`config.py` lines 483-487 at commit `6739f8c1`, confirmed via `git show
--stat 9657bc7a` — only `config.py` changed, no test changes, matching
the LOG's claim). I also confirmed there is no case where the reorder
makes the *ordering* branch newly unreachable: `warn=10, error=5` (both
positive, pure ordering violation) still raises via the ordering branch.

## 5. Gate — re-run independently, verdict read in a separate step

```
$ ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified
```

Run against branch tip `276ab254` (the final HEAD, including LOG/REPORT
and the freeze commit — a superset of what the implementer's own last
gate run at `79725379` covered).

Container capped `docker update --cpus=3` immediately after launch per
the host-shared-with-production-game-server rule. Read
`.assay/verdict-tester-unified.json` in a separate step (not through a
piped tail):

- `outcome: "PASS"`, `exit_code: 0`, `commit:
  276ab2541bd0d0a983ee907ee1c46b97320a9696` (= HEAD).
- R0 (`tests-pass`): `PASS`.
- R1 (`changed-line-coverage`, `fail_under: 100.0`, `mode: changed_lines`):
  `PASS`, `pct: 100.0`, `covered: 25`, `executable: 25`,
  `missing_lines: {}`.

**Gate green, independently confirmed, at the actual final tip.**

## 6. Reconciliation against LOG.md / REPORT.md

Read only after the blind pass above. No discrepancy found between my
independent findings and the implementer's claims:

- The gate-1-FAIL → reorder-fix → gate-3(and my own gate-4)-PASS
  narrative matches what `git show --stat` on the named commits actually
  shows.
- REPORT.md's per-oracle pytest node evidence matches what I got building
  the fixtures myself from scratch, independently.
- REPORT.md's file-touch itemization matches `git diff b42bd8a3..HEAD
  --stat` exactly (6 files: 4 implementation + LOG + REPORT).
- REPORT.md's own orientation telemetry names the fix-verification
  round's residual, accepted-as-non-blocking O5 one-sidedness (unknown-key
  rejection untested) and correctly does not claim to have closed it —
  consistent with the fix-verification report's own disposition of that
  item. I independently re-confirmed the production safety net for that
  gap still holds: constructing `ProjectConfig.load()` against a
  `[lint.l10]` table with a misspelled key (`warn_token`) raises a
  `TypeError` from `L10Config(**l10_data)` (uncaught, fails loudly),
  identical to how `Policy(**policy_data)` and `NotifyConfig(**notify_data)`
  already behave elsewhere in this same file — not a new inconsistency,
  not a regression, still non-blocking.

## 7. Verdict

**ACCEPT.** No blockers. Every Work item lands exactly as specified, all
five oracles are independently reproducible from a from-scratch script
(not the implementer's fixtures), three targeted mutation probes
(including the exact wiring-omission evasion the whole carve-review/
fix-verification cycle was about) are caught by name, the mid-
implementation reorder fix is independently verified correct and complete
against five additional non-positive/ordering combinations I planted
myself, the forbid list is intact, no file outside scope was touched, and
the gate is green at the true final commit, read in a separate step. The
one carried-forward residual (O5's unknown-key direction untested) is
unchanged from the fix-verification round's own non-blocking disposition
and does not affect this verdict.
