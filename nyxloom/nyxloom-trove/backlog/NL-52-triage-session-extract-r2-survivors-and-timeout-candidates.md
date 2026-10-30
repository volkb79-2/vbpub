---
kind: backlog-entry
schema_version: 1
id: NL-52
title: "Triage session-extract R2 survivors and timeout candidates"
status: open
type: "bugfix"
severity: "medium"
component: "session-extract"
context_estimate: "large"
provenance: "Independent review batch 9, 2026-10-30; grouped inventory in nyxloom-trove/reports/nyxloom-usage-r2-survivor-inventory-2026-10-30.md"
filed_by: "Codex GPT-6 Luna"
priority: 2
filed_date: "2026-10-30"
---

## Observed mechanism and reproduction

The exact-tree `session-extract` R2 campaign at `a43c0fcf97d8ef14b592b8eb0c6b377ab0e1251c` ended with 312 survivors and two `budget_exceeded` candidates. The registered lane verdict is `BUDGET_EXCEEDED/LANE_TIMEOUT`; R2 remains waived for the release and is not green. The complete grouped inventory and candidate IDs are in `nyxloom-trove/reports/nyxloom-usage-r2-survivor-inventory-2026-10-30.md` and its adjacent JSON sidecar.

## Why Nyxloom owns it

Every candidate mutates Nyxloom session-extract source, and future dispositions require behavior tests against its parser, adapters, usage model, and renderers.

## Proposed contract

Triage all 312 survivor IDs and the two timeout IDs from the sidecar. Determine whether each survivor is equivalent, exposes a test gap, or changes error-path behavior. Add focused behavioral tests only in that later triage task. Keep timeout candidates separate until a budget decision and rejudge provide a terminal disposition.

### Deferred groups

- **G01** `src/nyxloom/session_extract/adapters/claude_code.py:116, 150, 156, 160, 165, 888, 906, 916, 918` — 11 survived; `test_gap`. Adapter defaults, identity ownership and edit/tool-input branches need distinguishing behavior cases; inspect the ownership conflict cases separately.
- **G02** `src/nyxloom/session_extract/compress.py:49` — 1 survived; `test_gap`. Transcript-boundary matching is not distinguished by current coverage.
- **G03** `src/nyxloom/session_extract/config.py:235, 245` — 2 survived; `test_gap`. Boolean configuration defaults change opt-in extraction behavior when callers omit the field.
- **G04** `src/nyxloom/session_extract/ledger.py:217, 353` — 2 survived; `test_gap`. Ledger policy defaults need direct contract assertions for both enabled and disabled cases.
- **G05** `src/nyxloom/session_extract/presets.py:35, 57-58, 75, 90-91, 98-99, 139, 143, 145` — 10 survived, 1 budget-exceeded; `test_gap`. Preset defaults and cancellation/selection predicates need cases that distinguish each option combination.
- **G06** `src/nyxloom/session_extract/render.py:206` — 1 survived; `test_gap`. Timestamp and ledger-boundary rendering choices need explicit transition cases.
- **G07** `src/nyxloom/session_extract/shellcmd.py:156, 178, 218, 233, 273` — 6 survived; `error_path`. Nested command and token parsing limits include refusal and malformed-input paths.
- **G08** `src/nyxloom/session_extract/stats.py:205` — 1 survived; `error_path`. Integer-versus-boolean token validation needs malformed numeric cases.
- **G09** `src/nyxloom/session_extract/stopstate.py:80, 109` — 2 survived; `error_path`. Synthetic interrupt and tool-result handling are recovery-state branches.
- **G10** `src/nyxloom/session_extract/toolresult.py:178` — 1 survived; `test_gap`. Tool-result summarization and SCP effect defaults lack distinguishing assertions.
- **G11** `src/nyxloom/session_extract/usage/anchors.py:38, 68, 77, 80, 203, 208` — 8 survived; `error_path`. Timestamp, range, source validation and anchor-store conflict/write checks are refusal paths.
- **G12** `src/nyxloom/session_extract/usage/calibration.py:22, 67, 74, 89, 94, 110, 125, 128, 155, 195` — 12 survived; `test_gap`. Reset-window fitting, monotonicity, residual limits and estimate disclosures need boundary cases; invalid input branches may be error paths.
- **G13** `src/nyxloom/session_extract/usage/charts.py:41, 58, 65, 70, 108, 129, 148, 178, 223, 269, 272, 290, 304, 310, 325, 373, 468-469, 751, 856, 859, 864-865, 870, 872-873, 887, 889, 899, 949, 966, 1066-1067` — 48 survived; `test_gap`. Joined chart ordering, grouping, point limits and anchor-fit clipping need distinguishing cases; malformed-fit guards are error paths.
- **G14** `src/nyxloom/session_extract/usage/html.py:18, 21` — 2 survived; `likely_equivalent`. The ensure_ascii and sort_keys mutations change JSON byte formatting, not the parsed payload; verify no byte-for-byte output contract depends on them.
- **G15** `src/nyxloom/session_extract/usage/html.py:19` — 1 survived; `error_path`. The allow_nan mutation changes rejection of non-finite JSON values.
- **G16** `src/nyxloom/session_extract/usage/loader.py:35, 44, 47, 84, 86, 95, 102, 108, 230, 254, 273, 281, 294, 303, 305, 313, 335, 338, 374, 397, 428, 662, 696, 751, 780, 802, 852-853, 856, 874, 886, 889, 898, 901, 955, 978, 1012, 1034, 1045-1046, 1049, 1136, 1161, 1174, 1185, 1209, 1213, 1216` — 53 survived, 1 budget-exceeded; `error_path`. The group spans malformed metadata, source validation, file races, lineage fallbacks and timestamp checks; candidate-level review should separate error paths from ordinary attribution gaps.
- **G17** `src/nyxloom/session_extract/usage/model.py:10, 18, 49-50, 52, 74, 102, 116, 128, 180, 216, 235-236, 245, 253, 284, 296` — 21 survived; `test_gap`. Model defaults and temporal gap handling need direct tests of omitted flags and boundary behavior.
- **G18** `src/nyxloom/session_extract/usage/pricing.py:30, 39, 47, 60, 266, 295, 300, 303, 305, 415, 426, 448-449, 464, 470, 481, 492, 535, 565, 569, 585-586, 592, 606, 620, 625-626, 628, 637, 641-642, 660, 670, 680, 682` — 41 survived; `test_gap`. Price selection, prompt thresholds, reconciliation and fit-rank limits need explicit boundary cases.
- **G19** `src/nyxloom/session_extract/usage/query.py:41, 69, 73-74, 88, 101, 103, 138, 143, 213, 237, 239-240, 243, 245, 247, 249, 268, 276, 324, 374, 562, 605, 614, 617, 652` — 37 survived; `test_gap`. Query window, sorting, burst, folding and generated-time boundaries need distinguishing cases.
- **G20** `src/nyxloom/session_extract/usage/render.py:26, 38-39, 51, 60, 132, 153, 155, 158, 179, 187, 200, 254, 278-280, 298, 329, 341-342, 345-349, 351, 354-357, 364, 367, 376-377` — 41 survived; `test_gap`. Renderer traversal, folded ownership, exact-ledger totals and CSV value mapping need output assertions.
- **G21** `src/nyxloom/session_extract/usage/windows.py:33-34, 40, 125, 155, 166, 209-210` — 10 survived; `test_gap`. Window aggregation and reset-boundary inclusivity need cases on both sides of each endpoint.
- **G22** `src/nyxloom/session_extract/watch.py:148` — 1 survived; `likely_equivalent`. The ensure_ascii mutation changes escape spelling while preserving parsed JSON; confirm consumers have no byte-level dependency.

## Oracles

- Every one of the 312 survivor IDs and both timeout IDs has exactly one later disposition linked to its campaign group.
- Each `test_gap` or `error_path` disposition names a behavior case that distinguishes the surviving mutation from the baseline, with the legitimate state constructed for any refusal.
- Each `likely_equivalent` disposition records why the mutated syntax preserves the observable contract, including byte-level expectations where relevant.
- Timeout candidates remain `budget_exceeded` until a separately authorized rejudge finishes; the existing R2 verdict is never described as green.

## SPEC ownership

`src/nyxloom/session_extract/**`, the owning tests under `tests/`, and the inventory report/JSON sidecar.

## Updates

2026-10-30: filed from independent review batch 9. No R2 killing tests were added or run in this fix wave.
