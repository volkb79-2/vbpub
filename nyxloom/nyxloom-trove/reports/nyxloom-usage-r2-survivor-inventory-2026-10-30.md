# Session-extract R2 survivor inventory

Date: 2026-10-30
Campaign source commit: `a43c0fcf97d8ef14b592b8eb0c6b377ab0e1251c`
Run-Gate campaign log ID: `d1b8b0eadd04fb3c014f386fd8d3b1ed`
Verdict: **BUDGET_EXCEEDED** (`LANE_TIMEOUT`, exit 4).

## Scope and evidence

This report inventories the terminal R2 candidates for the exact-tree `session-extract` campaign. The full run planned 1,203 candidates and ended with 889 killed, 312 survived, and 2 `budget_exceeded`; there were no pending candidates. The final verdict remains budget-exceeded, not a green R2 result. The operator accepted an R2 release waiver; survivor triage is deferred, and this report does not add or run killing tests.

The inventory was reconstructed from `nyxloom/.assay/verdict-session-extract.json`, the candidate events in `nyxloom/.assay/progress-session-extract.jsonl` for the 1,203-candidate exact-tree run on 2026-10-30, and candidate records in the Run-Gate Assay state directory. The final exact-tree Run-Gate retry resumed 1,196 candidates, rejudged the 2 timeout candidates, rejected 5 stale records, and again produced 1,203 terminal outcomes. All 1,203 candidate IDs selected from the progress evidence matched state records with the same outcome bucket (0 mismatches). The sidecar records source hashes and the selection window.

The machine-readable sidecar is [`nyxloom-usage-r2-survivor-inventory-2026-10-30.json`](nyxloom-usage-r2-survivor-inventory-2026-10-30.json). It contains all 312 survivor IDs and both `budget_exceeded` IDs, with path, line, operator, mutation description, group, and outcome.

## Counts

| Planned | Killed | Survived | Budget-exceeded | Other terminal | Pending |
|---:|---:|---:|---:|---:|---:|
| 1,203 | 889 | 312 | 2 | 0 | 0 |

Survivor operator counts: `python:compare-swap` 119; `python:bool-const-flip` 99; `python:boolop-swap` 90; `python:falsy-swap` 4. The two budget-exceeded candidates are listed separately below and in the sidecar.

## Grouped inventory

Classifications are initial source-level triage aids, not equivalence rulings. A group can contain distinct cases; review the candidate IDs and exact mutation before deciding whether an additional behavioral test is needed. Line ranges refer to the campaign source tree.

| Group | File and line ranges | Candidates | Operator counts | Classification and note |
|---|---|---:|---|---|
| G01 | `nyxloom/src/nyxloom/session_extract/adapters/claude_code.py`:116, 150, 156, 160, 165, 888, 906, 916, 918 | 11 survived | bool-const-flip=2; boolop-swap=7; compare-swap=2 | **test_gap** — Adapter defaults, identity ownership and edit/tool-input branches need distinguishing behavior cases; inspect the ownership conflict cases separately. |
| G02 | `nyxloom/src/nyxloom/session_extract/compress.py`:49 | 1 survived | compare-swap=1 | **test_gap** — Transcript-boundary matching is not distinguished by current coverage. |
| G03 | `nyxloom/src/nyxloom/session_extract/config.py`:235, 245 | 2 survived | bool-const-flip=2 | **test_gap** — Boolean configuration defaults change opt-in extraction behavior when callers omit the field. |
| G04 | `nyxloom/src/nyxloom/session_extract/ledger.py`:217, 353 | 2 survived | bool-const-flip=2 | **test_gap** — Ledger policy defaults need direct contract assertions for both enabled and disabled cases. |
| G05 | `nyxloom/src/nyxloom/session_extract/presets.py`:35, 57-58, 75, 90-91, 98-99, 139, 143, 145 | 10 survived, 1 budget-exceeded | bool-const-flip=10; budget: bool-const-flip=1 | **test_gap** — Preset defaults and cancellation/selection predicates need cases that distinguish each option combination. |
| G06 | `nyxloom/src/nyxloom/session_extract/render.py`:206 | 1 survived | boolop-swap=1 | **test_gap** — Timestamp and ledger-boundary rendering choices need explicit transition cases. |
| G07 | `nyxloom/src/nyxloom/session_extract/shellcmd.py`:156, 178, 218, 233, 273 | 6 survived | bool-const-flip=1; boolop-swap=1; compare-swap=4 | **error_path** — Nested command and token parsing limits include refusal and malformed-input paths. |
| G08 | `nyxloom/src/nyxloom/session_extract/stats.py`:205 | 1 survived | compare-swap=1 | **error_path** — Integer-versus-boolean token validation needs malformed numeric cases. |
| G09 | `nyxloom/src/nyxloom/session_extract/stopstate.py`:80, 109 | 2 survived | bool-const-flip=1; boolop-swap=1 | **error_path** — Synthetic interrupt and tool-result handling are recovery-state branches. |
| G10 | `nyxloom/src/nyxloom/session_extract/toolresult.py`:178 | 1 survived | bool-const-flip=1 | **test_gap** — Tool-result summarization and SCP effect defaults lack distinguishing assertions. |
| G11 | `nyxloom/src/nyxloom/session_extract/usage/anchors.py`:38, 68, 77, 80, 203, 208 | 8 survived | bool-const-flip=2; boolop-swap=3; compare-swap=3 | **error_path** — Timestamp, range, source validation and anchor-store conflict/write checks are refusal paths. |
| G12 | `nyxloom/src/nyxloom/session_extract/usage/calibration.py`:22, 67, 74, 89, 94, 110, 125, 128, 155, 195 | 12 survived | bool-const-flip=2; boolop-swap=3; compare-swap=7 | **test_gap** — Reset-window fitting, monotonicity, residual limits and estimate disclosures need boundary cases; invalid input branches may be error paths. |
| G13 | `nyxloom/src/nyxloom/session_extract/usage/charts.py`:41, 58, 65, 70, 108, 129, 148, 178, 223, 269, 272, 290, 304, 310, 325, 373, 468-469, 751, 856, 859, 864-865, 870, 872-873, 887, 889, 899, 949, 966, 1066-1067 | 48 survived | bool-const-flip=11; boolop-swap=13; compare-swap=23; falsy-swap=1 | **test_gap** — Joined chart ordering, grouping, point limits and anchor-fit clipping need distinguishing cases; malformed-fit guards are error paths. |
| G14 | `nyxloom/src/nyxloom/session_extract/usage/html.py`:18, 21 | 2 survived | bool-const-flip=2 | **likely_equivalent** — The ensure_ascii and sort_keys mutations change JSON byte formatting, not the parsed payload; verify no byte-for-byte output contract depends on them. |
| G15 | `nyxloom/src/nyxloom/session_extract/usage/html.py`:19 | 1 survived | bool-const-flip=1 | **error_path** — The allow_nan mutation changes rejection of non-finite JSON values. |
| G16 | `nyxloom/src/nyxloom/session_extract/usage/loader.py`:35, 44, 47, 84, 86, 95, 102, 108, 230, 254, 273, 281, 294, 303, 305, 313, 335, 338, 374, 397, 428, 662, 696, 751, 780, 802, 852-853, 856, 874, 886, 889, 898, 901, 955, 978, 1012, 1034, 1045-1046, 1049, 1136, 1161, 1174, 1185, 1209, 1213, 1216 | 53 survived, 1 budget-exceeded | bool-const-flip=11; boolop-swap=22; compare-swap=20; budget: compare-swap=1 | **error_path** — The group spans malformed metadata, source validation, file races, lineage fallbacks and timestamp checks; candidate-level review should separate error paths from ordinary attribution gaps. |
| G17 | `nyxloom/src/nyxloom/session_extract/usage/model.py`:10, 18, 49-50, 52, 74, 102, 116, 128, 180, 216, 235-236, 245, 253, 284, 296 | 21 survived | bool-const-flip=20; compare-swap=1 | **test_gap** — Model defaults and temporal gap handling need direct tests of omitted flags and boundary behavior. |
| G18 | `nyxloom/src/nyxloom/session_extract/usage/pricing.py`:30, 39, 47, 60, 266, 295, 300, 303, 305, 415, 426, 448-449, 464, 470, 481, 492, 535, 565, 569, 585-586, 592, 606, 620, 625-626, 628, 637, 641-642, 660, 670, 680, 682 | 41 survived | bool-const-flip=21; boolop-swap=7; compare-swap=12; falsy-swap=1 | **test_gap** — Price selection, prompt thresholds, reconciliation and fit-rank limits need explicit boundary cases. |
| G19 | `nyxloom/src/nyxloom/session_extract/usage/query.py`:41, 69, 73-74, 88, 101, 103, 138, 143, 213, 237, 239-240, 243, 245, 247, 249, 268, 276, 324, 374, 562, 605, 614, 617, 652 | 37 survived | bool-const-flip=2; boolop-swap=10; compare-swap=23; falsy-swap=2 | **test_gap** — Query window, sorting, burst, folding and generated-time boundaries need distinguishing cases. |
| G20 | `nyxloom/src/nyxloom/session_extract/usage/render.py`:26, 38-39, 51, 60, 132, 153, 155, 158, 179, 187, 200, 254, 278-280, 298, 329, 341-342, 345-349, 351, 354-357, 364, 367, 376-377 | 41 survived | bool-const-flip=5; boolop-swap=21; compare-swap=15 | **test_gap** — Renderer traversal, folded ownership, exact-ledger totals and CSV value mapping need output assertions. |
| G21 | `nyxloom/src/nyxloom/session_extract/usage/windows.py`:33-34, 40, 125, 155, 166, 209-210 | 10 survived | bool-const-flip=2; boolop-swap=1; compare-swap=7 | **test_gap** — Window aggregation and reset-boundary inclusivity need cases on both sides of each endpoint. |
| G22 | `nyxloom/src/nyxloom/session_extract/watch.py`:148 | 1 survived | bool-const-flip=1 | **likely_equivalent** — The ensure_ascii mutation changes escape spelling while preserving parsed JSON; confirm consumers have no byte-level dependency. |

## Budget-exceeded candidates

- `ebe086057a6b38a6b83928ff396dc3be88027987aa753db107639031109d932c` — `nyxloom/src/nyxloom/session_extract/presets.py:91`, `python:bool-const-flip` `True->False`. Run-Gate/Assay ended it as `budget_exceeded` at the configured per-candidate limit; it is not counted as a survivor or kill.
- `558916d65c155a2df85965527ee1c3c8bfacdc3cb051e3b674372bc6c7ca9708` — `nyxloom/src/nyxloom/session_extract/usage/loader.py:397`, `python:compare-swap` `NotEq->Eq`. Run-Gate/Assay ended it as `budget_exceeded` at the configured per-candidate limit; it is not counted as a survivor or kill.

## Deferred triage contract

- Start from the sidecar candidate IDs; every candidate must receive a disposition in a later triage record.
- For `test_gap` and `error_path` groups, identify the behavior and legitimate state that current tests do not distinguish before adding focused tests.
- Treat `likely_equivalent` as a hypothesis only. Record the operator-level equivalence rationale and check any byte-level or ordering contract before closing it.
- Rejudge the two timeout candidates under a separately authorized budget decision; do not relabel timeout as survive, kill, or equivalent.
- Keep the original R2 verdict as `BUDGET_EXCEEDED` unless a later complete campaign proves otherwise.

No tests were added or run for this inventory task, and no R2 candidate was killed by this change.
