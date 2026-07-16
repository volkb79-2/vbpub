# P65 — Human-readable query/report rendering — REVIEW

**Task:** `topos-P65-report-human-readable-render`
**Branch:** `feat/topos-P65-report-human-readable-render`
**Reviewer role:** independent frontier reviewer (merge gate)
**Verdict:** ✅ APPROVED (one small defect fixed in-place)

## What was reviewed

Diff vs `main` (merge-base `f43a2f7`):

```
 topos/README.md            |  37 +++--
 topos/src/topos/cli.py     |  67 +++++++--
 topos/src/topos/render.py  | 340 +++++++++++++++++++++++++++++++++++++++++++++
 topos/tests/test_render.py | 331 +++++++++++++++++++++++++++++++++++++++++++
 topos/tests/test_report.py |  24 +++-
```

`render.py` is a pure formatter over the canonical JSONable dicts
(`Result.to_jsonable()` / `report_to_jsonable(...)`). The CLI now emits an
ASCII table by default and keeps `--json` for the machine contract, with
`--json`/`--table` mutually exclusive.

## Gate — re-run, not trusted from any report

Declared gate (`topos[dev]` venv, `pytest topos/tests`) re-run by the reviewer:

- Pre-fix: **1644 passed**, 0 skipped (192.83s).
- Post-fix: **1645 passed**, 0 skipped (188.84s) — includes one regression test I added.
- `git diff --check`: clean. `py_compile` of `render.py`/`cli.py`: clean.

## Oracle-by-oracle adversarial verification

- **O1 (verbatim values):** metric values are rounded to 6 decimals *upstream*
  (`semantics._round`, `_ROUND_DIGITS = 6`; engine `round(subtree, 6)`), and
  `_format_number` formats with `:.6f` + trailing-zero trim, so the displayed
  figure equals the JSON figure. Cross-checked `query`/`report` table output
  against `--json` on the `gstammtisch-once` fixture: values match. ✅
- **O2 (distinct typed spellings, zero is `0`):** `missing`/`redacted`/
  `warming`/`permission-denied`/`unsupported`/`unlimited` map from `src`;
  `stale`/`truncated` at the header. Zero renders `0` (verified: `0`, `0.2`,
  `0.1`). **One defect found and fixed — see below.** ✅ after fix.
- **O3 (hierarchy vs flat):** header always prints `projection: <flat|hierarchy>`;
  hierarchy indents children by `depth`, preserving engine ancestry/sibling
  order; flat/global-rank is explicitly labelled `flat`. ✅
- **O4 (deterministic ASCII, no ANSI/trailing WS):** widths computed from
  content, `.rstrip()` per line, trailing blank lines dropped; suite asserts
  ASCII-only, no `\x1b`, no trailing whitespace, idempotent render. ✅
- **O5 (`--json` byte-compatible):** JSON paths call the unchanged
  `format_result` / `format_report`; a test asserts CLI `--json` == direct
  `format_result`. ✅
- **O6 (both formats → exit 2):** `add_mutually_exclusive_group()`; verified
  real subprocess exit 2 for both `query` and `report`. ✅
- **O7 (P61 exit code format-independent):** exit logic runs after the print,
  off `assertion_results`; verified breach → exit 1 in both text and JSON. ✅
- **O8 (renderer purity):** no collection/aggregation/rounding-from-raw/file I/O;
  test greps the module for `open(`/`Path(`/readers, and asserts input is not
  mutated. ✅

## Defect found and fixed (small)

**`render._format_src_value` mislabelled kernel-unsupported cells under
`--visibility available`.** The `hidden` branch returned `permission-denied`
for *any* `src` starting with `unavail`, so a `unavail_kernel` value (whose
documented spelling is `unsupported`) rendered as `permission-denied` — the
same spelling as `unavail_perm`. This:

- contradicted the module's own docstring (`permission-denied ⟺ unavail_perm`),
- collapsed two distinct typed states into one spelling (O2 negative), and
- made the same entity read `unsupported` under `--visibility all` but
  `permission-denied` under `--visibility available`.

Confirmed by running the CLI both ways on the fixture. The `else "hidden"`
fallback was dead (a hidden cell always has an `unavail*` src).

**Fix:** dropped the `hidden` special-case; a suppressed cell now classifies by
its true `src` exactly as under `--visibility all` (`unavail_kernel` →
`unsupported`, `unavail_perm` → `permission-denied`). Removed the now-unused
`hidden=` argument at the call site. Added
`test_unsupported_and_permission_denied_stay_distinct_under_available` to lock
the behaviour. All existing tests still pass.

## Non-blocking observations (not fixed)

1. **`test_all_six_spellings_are_pairwise_distinct`** is tautological — it
   asserts a literal set has 6 members and never touches the renderer. Harmless
   but exercises nothing; the real coverage lives in the other O2 tests.
2. **Header/raw timestamps** go through `:.6f`, which would round a `ts` with
   >6 fractional digits. O1/O8 are scoped to *metric values* (all pre-rounded),
   and fixture timestamps are integral, so no divergence observed; noted for
   completeness.
3. **`--pretty` is silently ignored** in the default (text) mode; only meaningful
   with `--json`. Cosmetic.
4. **README ROADMAP row references `handoff/reports/P65-REPORT.md`**, which the
   implementer did not create (the diff adds no LOG/REPORT). Per the reviewer
   role contract I do not author the implementer's LOG/REPORT, so this is left
   as an implementer/controller deliverable gap. It does not affect any oracle
   or gate.

## Conclusion

All eight acceptance oracles hold; the declared gate is green with zero skips.
The single real defect (visibility-dependent state mislabel) was small and
self-contained and has been fixed on the feat branch with a regression test.
**APPROVED.**
