---
schema_version: 1
id: assay-P04-b100-report
project: assay
title: "One bounded snapshot report for gate verdict, progress, and log evidence"
tier: sonnet5-high
input_revision: "8911e636ad09071868c813347bf7a4d0a00049bb"
source: {kind: product-goal, ref: "nyxloom-trove/4-backlog.md#b100"}
stack: none
depends_on: []
session: fresh
scope:
  touch: ["src/assay/analysis.py", "src/assay/schemas/analysis-report.schema.json", "tests/test_analysis.py", "README.md", "docs/DESIGN-GUIDE.md", "docs/CONSUMERS.md", "docs/INTERNAL-CONSUMERS.md", "nyxloom-trove/4-backlog.md", "nyxloom-trove/reports/assay-WAVE-C-CONTROLLER-LOG.md"]
  forbid: ["src/assay/verify.py", "src/assay/schemas/verdict.schema.json", "src/assay/config.py", "src/assay/errors.py", "src/assay/cli.py"]
oracles:
  - id: O1
    observable: "A schema-valid PASS verdict at the required commit reports pass and command exit 0; a schema-valid non-PASS verdict reports fail and exit 1, preserving its recorded outcome, exit code, and reason code"
    negative: "A file path, log line, or progress marker alone certifies pass/fail, or a valid adverse verdict is hidden"
    gate: tester-unified
  - id: O2
    observable: "A current nonterminal progress run with a partial final JSONL record reports running and exit 3; a stale/no-terminal run or a terminal progress event without a valid verdict reports evidence_error and exit 2"
    negative: "A malformed complete record is ignored, a torn final record destroys valid preceding progress, or stale progress is called running"
    gate: tester-unified
  - id: O3
    observable: "Missing, malformed, unreadable, wrong-lane, or wrong-commit supplied verdict/progress evidence reports evidence_error and exit 2 with the actual commit and bounded diagnostic when available"
    negative: "Expected-commit mismatch, absent evidence, or malformed JSON falls through to running or pass"
    gate: tester-unified
  - id: O4
    observable: "Multiple named lanes are reported in deterministic order, with exact explicit artifact paths and hashes, progress counts, latest event/phase, and evidence-artifact path references; mixed statuses use the frozen command exit precedence"
    negative: "A lane is silently omitted, a path is invented, or one lane's status is attributed to another"
    gate: tester-unified
  - id: O5
    observable: "Text and JSON output both stay within the error-count/line-size bounds for a log larger than the diagnostic window, report its full path and full-file SHA-256, and expose truncation without using log content to decide status"
    negative: "The command dumps an entire log, loses the full-log lookup path, or treats a child line such as 'PASS'/'ERROR' as a verdict"
    gate: tester-unified
  - id: O6
    observable: "The ten B100 acceptance cases each assert JSON shape, text shape, and exact process exit code; report execution makes no filesystem writes; a control proves existing analyze verdict/progress/receipt behavior is unchanged"
    negative: "The report writes into the judged tree or changes an existing analyze subcommand's result, text, or exit mapping"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the command cannot be added without changing existing analyze verdict/progress/receipt behavior"
  - "the required contract appears to need a verdict schema, lane schema, or ReasonCode change"
  - "one of the ten B100 cases requires status inference from a path or arbitrary child output"
mutexes: [merge-lane]
---

# Wave C P4 — B100 `assay analyze report`

## Dispatch contract

- Contract class: **2c — bounded integration**. The public CLI, status rules,
  freshness boundary, JSON shape, exit mapping, and docs are fixed below; the
  implementer chooses only private parsing and rendering details.
- The Wave C prompt requests Opus for P4. Opus is not among the models
  available in this session, so the controller assigns the strongest listed
  model, GPT-6-Astra xhigh, as a fresh implementer, then a separate fresh
  GPT-6-Astra xhigh adversarial reviewer.
- P3 prerequisite: merged and gated at `09d1f38d`; current package base is
  `8911e636ad09071868c813347bf7a4d0a00049bb`.
- This is a stdout-only diagnostic snapshot. It does not write a report file,
  verdict, progress stream, or other artifact.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-wave-c-p4-b100-report` on
branch `assay-wave-c-p4-b100-report`, created by `ciu worktree` from the exact
P3-merged `main` SHA above. The package's unique CIU identity is
`assay-wave-c-p4-b100-report`.

## Context to read first

1. This handoff in full and `nyxloom-trove/4-backlog.md` §B100.
2. `src/assay/analysis.py`: `_json`, `_digest`, `_read`, `inspect_verdict`,
   `inspect_progress`, `build_analyze_parser`, and `cmd_analyze`.
3. `tests/test_analysis.py`: `cli`, `repository`, `verdict`, existing
   `inspect_progress` and `inspect_verdict` tests, and the analysis-doc tests.
4. `src/assay/mutation.py::ProgressStream` and `runner.py::PROGRESS_HEARTBEAT_DEFAULT_SECONDS`;
   `docs/CONSUMERS.md`'s B064 progress event table.
5. The three public adopter documents' `Review evidence analysis` sections
   and the existing `docs/INTERNAL-CONSUMERS.md` adoption guidance.

## CLI and serialized contract

Add only `assay analyze report` in `analysis.py`:

```text
assay analyze report --expected-commit SHA
  [--verdict LANE FILE]... [--progress LANE FILE]... [--log LANE FILE]...
  [--format json|text] [--max-errors N]
```

- Require a full lowercase 40- or 64-hex expected commit and at least one
  explicit input. Use existing `--expected-commit`, `--format`, and named
  two-argument option conventions from `assay analyze`/`receipt`.
- `--max-errors` defaults to 5 and accepts only integers 0–10. Report no more
  than that many diagnostic records per lane, cap each displayed record at 512
  characters, and expose a truncation/count field.
- The JSON object has `schema_version: 1`, the expected commit, the command's
  exit code, and a lexically sorted non-empty `lanes` array. Each lane records
  its name, `status` (`running`, `pass`, `fail`, `evidence_error`), expected
  and actual commit, verdict outcome/exit/reason code when available, latest
  progress event and phase, progress event counts and available candidate
  counts, bounded error records, explicit input paths plus byte counts and
  full-file SHA-256 values, and artifact paths referenced by the verified
  verdict. Referenced evidence paths are displayed only; never open them
  implicitly. Publish `schemas/analysis-report.schema.json` and validate real
  command output against it.
- `--format text` prints one compact status line per lane followed by the same
  bounded details. It never prints a full log.
- Command exit mapping is fixed: 0 if every lane is `pass`; 1 if any lane is
  `fail` and none is `evidence_error`; 2 if any lane is `evidence_error`; 3
  when every lane is `running`. Mixed-state precedence is
  `evidence_error > fail > running > pass`.

## Evidence and status rules

1. Parse verdict JSON with the existing duplicate-key/non-finite protections,
   validate it with `verify_text`, require the embedded lane to equal the
   command's `LANE`, and compare its commit with `--expected-commit`. Classify
   `PASS` with exit 0 as `pass`; every other verifier-valid terminal outcome
   is `fail`. Preserve `inspect_verdict`'s separate dirty-override refusal:
   the new reader must show a verifier-valid verdict containing
   `overridden_dirty_paths`, not reuse that receipt-specific refusal.
2. Parse progress as JSONL in a streaming, bounded-memory reader. Require a
   complete run at the expected commit, select the last run header in the file,
   and reject a later run for a different commit. Count only that run. Ignore
   one malformed unterminated final record as a torn append; refuse malformed
   newline-terminated records. Report the latest event and its `phase`, plus
   candidate counts present in the stream. Never change existing
   `inspect_progress` semantics.
3. `running` is available only when no verdict was supplied, progress has no
   terminal `verdict_written`/`end` event, and the latest event's parseable
   `emitted_at` is no more than **120 seconds old**. This is twice the shipped
   default heartbeat period (`PROGRESS_HEARTBEAT_DEFAULT_SECONDS == 60`). A
   stale stream, absent timestamp, terminal progress with no valid verdict,
   missing run, or no verdict/progress facts yields `evidence_error`, never
   `pass`. If a valid terminal verdict exists, it determines status; a stale
   but otherwise valid optional progress input is shown as stale and does not
   overturn that verdict.
4. Hash the complete bytes of every supplied regular-file input. For a log,
   retain only its final 64 KiB for diagnostics, cap each record as above, and
   mark diagnostics truncated when bytes were omitted. The full log's resolved
   path, complete byte size, and complete SHA-256 remain in JSON. Select only
   bounded diagnostic lines containing `error`, `failed`, `exception`, or
   `traceback` (case-insensitive), and expose them as **log diagnostics**.
   `ASSAY_GATE_PHASE=` lines may supply a displayed latest phase. Logs never
   set or repair a status, outcome, exit code, reason code, expected commit,
   or actual commit.
5. Any explicitly supplied unreadable or non-regular log, or unreadable,
   malformed, wrong-lane, or wrong-commit verdict/progress input yields
   `evidence_error`; include its explicit path and a bounded error record.
   A log's contents may add diagnostics only. A log larger than the 64 KiB
   diagnostic window is normal: hash the full file, scan only the bounded
   tail, and mark diagnostics truncated without changing a verdict-derived
   status. No path's mere existence is evidence of success.

## Oracles

Implement all ten B100 cases (PASS, valid fail, running with a partial final
line, stale/no-terminal, missing path, malformed JSON, commit mismatch,
multiple lanes, bounded error selection, and over-limit log). For every case,
assert both JSON and text output plus exact process exit. Also prove that
`ERROR`/`PASS` child log lines cannot change a valid verdict status, output is
bounded and sorted, the report creates no files in a temporary judged Git
tree, and existing `analyze verdict`, `progress`, and `receipt` behavior is
unchanged. Add an over-limit log containing a valid terminal PASS verdict;
it stays pass, while diagnostics are truncated, full path and SHA-256 remain,
and output length respects the fixed bounds.

Update in this package: README `assay analyze` feature list, DESIGN-GUIDE
design/rationale, CONSUMERS pasteable long-gate example and exit vocabulary,
`docs/INTERNAL-CONSUMERS.md` internal gate workflow, the B100 backlog status,
and the controller log. Add report examples to the shipped-parser docs oracle
and verify all relevant cross-document anchors. Do not edit root `AGENTS.md`;
put the suggested controller wording there in the controller log for the
operator.

## BLOCKED rule

**BLOCKED:** If a listed oracle cannot be met without editing a forbidden file or changing
an existing subcommand, stop before making that change. Commit a short
`reports/assay-WAVE-C-P4-B100-BLOCKED.md` with the exact failing case, the
observed call path, and the smallest required contract change; leave the
worktree otherwise reviewable. Do not invent a reason code, lane key, or
verdict field to get around the stop. Do not edit the repository-root
`AGENTS.md`; leave its suggested adoption wording in the controller log.
