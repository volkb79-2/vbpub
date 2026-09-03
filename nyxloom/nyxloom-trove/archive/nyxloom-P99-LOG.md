# nyxloom-P99 — LOG (L10 per-project threshold override)

Handoff: `nyxloom-trove/handoffs/nyxloom-P99-l10-per-project-thresholds.md`
(input_revision `e3baff00`, frozen at `9ceb6eb9`). Branch:
`feat/nyxloom-P99-l10-per-project-thresholds`. Worktree:
`/workspaces/vbpub/.worktrees/nyxloom-nl3/nyxloom`.

Baseline confirmed before any edit (tracer bullet, mirroring the handoff's
own carver-run probe): built a real on-disk project (git init+add+commit)
with `[lint.l10]\nerror_tokens = 25000` in `.nyxloom/project.toml`, called
`ProjectConfig.load(root)`, confirmed `hasattr(cfg, "l10")` is `False` and
no exception is raised — the negative O1 must witness turn from failing
(today) to passing is real, not hypothetical.

One entry per commit, in order, each naming its own hash (self-hash rule).

## Commit `e3428c11` — fix(nyxloom): P99 -- add L10Config + parse/validate/assign [lint.l10] at load time

Work items 1-2. Added `L10Config` dataclass (`config.py`, mirrors
`NotifyConfig`'s style: `warn_tokens: int = 10000`, `error_tokens: int =
18000`) and `ProjectConfig.l10: L10Config = field(default_factory=L10Config)`.
In `ProjectConfig.load`, added parse+validate logic right before the
existing `carve_cfg`/`return cls(...)` block: reads
`data.get("lint", {}).get("l10", {})`, constructs `L10Config(**l10_data)`,
raises `ValueError` if `warn_tokens >= error_tokens` (equality included) or
either value `<= 0`, logging via `log.warning` first (mirrors
`validate_pipeline`'s fail-loudly pattern). Threaded `l10=l10` into the
`return cls(...)` call — the exact step the carve review's B1 finding
named as the one a first draft could skip while still looking complete.
Re-ran the tracer bullet immediately after this commit: `cfg.l10` now
correctly reads `L10Config(warn_tokens=10000, error_tokens=25000)` for the
same fixture that previously produced `hasattr(cfg, "l10") == False`.

## Commit `d6cf8334` — fix(nyxloom): P99 -- thread cfg into _check_l10, drop hardcoded L10 thresholds

Work items 3-4. `_check_l10`'s signature gained a `cfg: ProjectConfig`
parameter; its two comparisons (`tokens > 18000` / `tokens > 10000`) now
read `cfg.l10.error_tokens` / `cfg.l10.warn_tokens`, keeping strict `>` on
both branches unchanged. Updated the call site (`lint_file`, cfg already
in scope from the immediately-preceding L7/L9 calls) to pass `cfg`
through. Updated the L10 rule-catalogue module-docstring comment to
document the per-project `[lint.l10]` override and its 10000/18000
defaults. Ran `tests/test_lint.py` (pre-existing tests only, new ones not
yet added) — all green.

## Commit `e7e3c888` — fix(nyxloom): P99 -- schema shape for [lint.l10] (Work item 6)

Work item 6. Added a top-level `lint` object to
`src/nyxloom/schemas/nyxloom-config.schema.json`'s `properties`, nested
`l10` object, both `additionalProperties: false` (mirrors `notify`/
`policy`/`backlog_entries`'s closed, static-key precedent, not `stage`'s
open dynamic-key one). `warn_tokens`/`error_tokens` are
`{"type": "integer", "exclusiveMinimum": 0}`; neither is `required`
(O1/O4's own fixtures are partial/asymmetric overrides). Validated the
resulting file is well-formed JSON (`python3 -c "import json; json.load(...)"`).

## Commit `6739f8c1` — fix(nyxloom): P99 -- TestL10Size O1/O3/O4/O5 oracles + stale-comment fix

Work item 5. Added two module-level test helpers
(`_l10_project_root`, `_handoff_text_at_token_count`) and 7 new tests to
`TestL10Size`, all via a REAL `ProjectConfig.load()` on a freshly-built
on-disk project (git init+add+commit, mirroring `conftest.sample_project`'s
own shape) — never `dataclasses.replace`:

- `test_o1_partial_override_reaches_load_and_pins_new_boundary` (O1)
- `test_default_thresholds_boundary_values` (O2 boundary addendum)
- `test_o3_malformed_warn_greater_than_error_raises` (O3, case 1)
- `test_o3_malformed_warn_equals_error_raises` (O3, case 2 — the equality
  boundary that failed the first carve draft)
- `test_o3_malformed_non_positive_raises` (O3, case 3)
- `test_o4_lowered_thresholds_apply_symmetrically` (O4)
- `test_o5_schema_accepts_partial_l10_override` (O5)

Also fixed the stale `# L10 warning becomes error over 12k` comment on
`TestGoldenCorpus`'s `demo-P21-huge.md` parametrize row to say `18k`
(pre-existing staleness found while sweeping this file, unrelated to this
package's own logic, per Work item 5's own instruction to fix it in the
same commit). `demo-P21-huge.md` itself was NOT touched (forbid list).
Ran `pytest tests/test_lint.py -q`: all 84 tests pass (was 77 before this
commit's 7 additions).

## Gate run 1 (FAIL) — R1/UNCOVERED_LINES

`./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3 tester-unified`
at commit `6739f8c1` FAILed: assay's R1 (changed-line coverage) reported
`config.py` lines 483-487 uncovered. Diagnosis: with `[lint.l10]
error_tokens = -5` (warn_tokens defaulting to 10000), the
`warn_tokens >= error_tokens` check (10000 >= -5) fired FIRST, so the
non-positive check's own body was never reached by any of the three O3
malformed-fixture tests — dead code under the original check order.

## Commit `9657bc7a` — fix(nyxloom): P99 -- reorder [lint.l10] validation checks for full branch coverage

Reordered `ProjectConfig.load`'s two `[lint.l10]` validation checks:
non-positive now checked BEFORE ordering. A negative/zero value is always
caught by its own branch first; the ordering check is only reached once
both values are already known positive. No test changes needed — all
three existing malformed-fixture tests (warn>error, warn==error,
non-positive) each now exercise a distinct branch. Behavior (which
fixtures raise `ValueError`, and their messages) is unchanged. Local
`pytest --cov=src/nyxloom --cov-report=term-missing` re-check confirms
lines 474-491 (the whole `[lint.l10]` validation block) are no longer in
the missing-lines list.

## Gate run 2 (refused, dirty tree — no container launched)

Re-running `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3
tester-unified` immediately after commit `9657bc7a` refused: "refusing to
judge a dirty tree ... 1 uncommitted change(s) (first: '?? nyxloom/nyxloom-
trove/reports/nyxloom-P99-LOG.md')". No container was started (exit before
the docker argv line). Fixed by committing this LOG.md (below) before
re-running.

## Commit `79725379` — docs(nyxloom): P99 -- LOG.md through the gate-1-fail / reorder-fix / gate-2 cycle

Committed this file's own then-current content (documenting gate run 1's
FAIL and the reorder fix) so the worktree would be clean for the next
gate attempt.

## Gate run 3 (PASS)

`./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl3
tester-unified` at commit `79725379` (the LOG.md commit, HEAD at gate
time): **`tester-unified: PASS (exit 0)`**. Verdict JSON
(`.assay/verdict-tester-unified.json`): `"outcome": "PASS"`,
`"exit_code": 0`; claims R0 (`tests-pass`) `status: PASS`, R1
(`changed-line-coverage`, `fail_under: 100.0`, `mode: changed_lines`)
`status: PASS` with `"pct": 100.0`, `"missing_lines": {}` — the
`config.py` 483-487 gap from run 1 is gone. Full evidence + per-oracle
mapping in `nyxloom-P99-REPORT.md`.

## Commit `fb5e79dc` — docs(nyxloom): P99 -- REPORT.md with full oracle evidence + gate verdict

Written after gate run 3's PASS was confirmed. Contains per-oracle
evidence (exact pytest node + PASSED output for each of O1-O5, plus two
independent out-of-pytest re-verifications: O1's `.load()` field values
and O3's equality-case `ValueError` message), the gate-1-FAIL ->
reorder-fix -> gate-3-PASS narrative, an itemization of every touched
symbol in `config.py`/`lint.py`/the schema, and orientation telemetry.
This is the final commit of the package -- all 6 Work items and all 5
oracles are independently verified; not merging, per instructions.
