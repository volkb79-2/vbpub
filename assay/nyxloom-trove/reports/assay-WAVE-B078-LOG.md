# assay wave B078 — checkpoint 1 implementer LOG (2026-09-08/09)

Branch: `feat/assay-b078-r0-structured-report-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b078-r0-structured-report`
Spec: `nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md` (SR-0..SR-6) +
`WAVE-PROMPT-2026-09-08-b078-r0-structured-report-checkpoint1.md`
Backlog: `nyxloom-trove/4-backlog.md` `## B078`

Gate: **`tester-unified` GREEN** on `c80d94d451b92154394ecb48b845b21e6e7478c5`
— `ASSAY_REGISTERED_GATE_COMPLETE=1`, `run-gate: lane 'tester-unified' exit 0`,
all 13 `ASSAY_GATE_PHASE=` markers present. Verdict read in a separate step
from the log itself, never a piped exit code.

## Commits, in order

| Hash | What |
|---|---|
| `60b83a0c` | `src/assay/result_reports/` — the reader registry, the format-agnostic completeness core, the `vitest-json` reader, `tests/test_result_reports.py` |
| `5b2cecea` | `[lanes.X.result_report]` — `ResultReportConfig`, loader, `Lane` field, `as_declared()` round-trip, `tests/test_config_result_report.py` |
| `6073749c` | The R0 wiring: reserve/arm/consume + the three-way branch in `runner.execute_plan`, both R0 call sites, `tests/test_runner_result_report.py`, `make_lane(result_report=...)` |
| `286320db` | `docs/DESIGN-GUIDE.md` §6, `docs/CONSUMERS.md` (worked pasteable lane), `CHANGES.md`, backlog acceptance boxes |
| `c80d94d4` | Two findings from the full local suite: the reader's `json.loads` now catches `RecursionError`; `test_self_hosting.py`'s pinned universal-PASS mutation repinned to the new FAIL-branch spelling |

## The two design questions the spec left open

### 1. Exact TOML key names — `[lanes.<name>.result_report]` with `format` and `path`

SR-2's illustrative shape, adopted verbatim. Three things settled it:

- **Nesting.** `result_report` as a sub-table of the lane matches
  `[lanes.X.isolation]` and `[lanes.X.judge.coverage]` exactly. SR-2 named
  `[lanes.X.pins.assay]` as the estate precedent; in-repo, `isolation` is the
  closer one (same file, same loader, same `as_declared()` contract), and both
  point the same way.
- **`format`, not `reporter`/`kind`.** `judge.coverage.format` and
  `judge.mutation.format` already mean "which document shape is this, closed
  against a shipped registry" (A-007: declared, never sniffed). Reusing the
  word means a consumer who has written one assay lane already knows what this
  key does. `reporter` would have named the *producer*, which is a different
  axis this design does not model (and `judge.coverage.producer` already owns
  that word for a real distinction — see B045).
- **`path`, not `artifact`.** `artifact` is taken, and taken for something
  with a different grammar: every `*_artifact` field in this file is
  **project-root**-relative and containment-checked by resolving it against
  that root (`_validate_artifact_path`). The result report is relative to the
  directory the command **runs in** (the lane's `cwd` when declared), because
  that is what the runner's own `--outputFile` resolves against. Two different
  roots must not share one word — A-271's "two path grammars, not one,
  deliberately" is the same argument one field over.

Both keys are **required**; there is no defaulting. A format with no path
names nothing to read and a path with no format cannot be parsed, so neither
has a meaning alone, and this module's own rule ("a default is legitimate only
when it is a policy choice correct in the absence of information") admits
neither.

`format` is closed against `assay.result_reports.RESULT_REPORT_FORMATS`, which
is **derived from the reader registry** rather than hand-listed beside it — a
hand-listed copy is how a format becomes declarable before a reader exists,
and `tests/test_config_result_report.py::test_the_config_vocabulary_is_the_reader_registry`
asserts the two are literally the same object.

`LANE_SCHEMA_VERSION` **stays 2**. A-432's own precedent, in this file's own
words: an additive optional lane field does not bump it, and a lane that omits
the table carries `None` and emits no key.

### 2. Where the reader module lives — a new `src/assay/result_reports/` package

The migration surface offered three: mirror `adapters/`, a new
`report_adapters/`, or a `format` dispatch inside `runner.py`. All three were
rejected in favour of a fourth that already exists in this codebase twice.

- **Not `adapters/`.** That package is the LANGUAGE registry: keyed by
  language, every member implementing the whole `LanguageAdapter` protocol
  (mutant generation, canary injection, coverage-key normalisation, statement
  spans). A test-report reader shares neither the key space (a *format*, and
  one format spans languages — Jest and vitest write the same document) nor
  the protocol. Putting it there means either widening `LanguageAdapter` with
  methods its five real members cannot answer, or keeping an unrelated kind of
  object in a namespace whose name promises the first kind.
- **Not `report_adapters/`.** It would be a second convention for a thing this
  repo already has a convention for.
- **Not a dispatch inside `runner.py`.** `runner.py` is 5,500 lines and is the
  one module that must stay runner-agnostic (`_command_heartbeat`'s own
  docstring says so about a much smaller intrusion). Per-format parsing there
  is exactly what `coverage_parsers/` was extracted out of.
- **Chosen: `result_reports/`,** built to look identical to
  `coverage_parsers/` and `mutation_parsers/` — sibling packages, one module
  per artifact FORMAT, a `model.py` of normalized types, a tiny uniform
  protocol (`FORMAT` + `read(raw) -> ReportSummary`), a registry in
  `__init__.py`, and a strict DAG (no sibling imports, nothing imports back
  into `runner`).

The split inside the package is what makes Checkpoints 2 and 3 small: a reader
turns one format's bytes into a `ReportSummary` or raises `ReportUnusable`;
`model.verify_complete` applies SR-2's bar to every format identically. Adding
`pytest-json-report` is a module plus one line in `_READERS`. The core is
tested against a synthetic non-vitest summary
(`tests/test_result_reports.py::test_the_core_refuses_every_incomplete_shape`)
precisely so "a core that can only be exercised through one format" — which
would not be a core — fails visibly.

## Decisions taken inside the ruled constraints

**No `Claim.detail`.** SR-6 left this to me and suggested the free-text route
to avoid A-050's closed-enum stop-and-ask. It cannot be taken:
`Claim._check_detail` **forbids `detail` on a PASS claim** ("a PASS carries no
detail"), and the PASS direction — a verified-complete report overriding a
non-zero exit — is precisely the case worth annotating. A FAIL-only detail
would be an asymmetric note about the uninteresting half. The provenance goes
where it can go instead, unforced: a report-driven PASS keeps the **real
non-zero `returncode`** and the **bounded stdout/stderr tails** on the
artifact, so the disagreement it resolved is visible without any schema
surface at all. `Verdict.result_stdout_tail`'s own docstring already allows a
PASS to carry them.

**No new `ReasonCode`, no `Outcome`/`EXIT_CODES` change, no `run-gate.py`
change, no retry, no CPU-quota primitive, no dstdns-side argv edit, no
Checkpoint 2/3 reader.** All as ruled.

## The one place I did not follow the design document

The design and the wave prompt both say "R0 wiring in `execute_command`",
citing `runner.py:1140-1142`. Those lines are `execute_command`'s **docstring**
describing A-073; the rule itself is implemented one function below, in
`execute_plan` (`runner.py:1066-1088` pre-change).

That distinction is load-bearing, not pedantic: **the shipped R0-only path
does not go through `execute_command` at all.** `run_lane`'s direct branch
(`runner.py:~5299`) calls `execute_plan` itself, and that is the path
dstdns's `ui_unit` lane — RG-45's own confirmed live reproduction, an R0-only
`kind = "assay"` lane — actually runs on. Wiring only `execute_command` would
have shipped a feature that passes every unit test and does nothing for the
case it was built for.

So the branch lives in `execute_plan`, behind a keyword-only
`result_report=None` parameter, and exactly two callers pass it:
`execute_command` (the design's named site, and the public API) and
`run_lane`'s direct R0-only path. `execute_plan`'s other callers — every R2
candidate re-execution, R3's canary control/transform halves, the
`environment_command` probe — keep the default and are unchanged **by
construction**, which is a stronger form of SR-1's promise than a rule each
call site has to remember.
`tests/test_runner_result_report.py::test_run_lane_direct_r0_path_applies_the_tiebreak`
covers the shipped path end to end, with an adjacent must-fail control.

Flagged here rather than silently absorbed, per the wave prompt's own
instruction. It is a correction to the design document's site reference, not a
change to anything the design decided.

## Two things the full local suite found (neither predicted)

1. **`test_untrusted_json_parse_sweep.py`** — the new reader's `json.loads`
   was unguarded. A result report is an untrusted third-party artifact, and a
   pathologically nested one blows CPython's stack inside the decoder. Here it
   is worse than a crash-vs-refusal question: an uncaught `RecursionError`
   escapes R0's terminal mapping entirely, where every other malformed shape
   is a quiet fallback. Fixed with the one-line widening the sweep asks for,
   plus a test that actually feeds it 200,000 nested arrays.
2. **`test_self_hosting.py`** — A-131's pinned universal-PASS mutation
   targeted the two literal field lines of R0's FAIL constructor, which this
   wave turned into a conditional pair. Repinned to the new spelling (same
   branch, same two fields, same mutation), and `runner.py`'s explanatory
   comment moved *above* the call so the pinned lines stay adjacent and the
   pin stays a surgical one-line edit. A comment sitting between them would
   have made the pin brittle against reflow.

Both are recorded because they are the kind of thing a "the feature works"
summary would have omitted.

## Known limitation, stated rather than discovered later

Assay does **not** create the report's parent directory.
`safeio.reserve_output`'s `create_missing_parents` is contractually reserved
for callers that own an ephemeral assay-managed snapshot (B006(b)), and R0
runs in the consumer's live tree. A lane declaring
`path = ".assay/report.json"` in a tree with no `.assay/` therefore falls back
to A-073 — safe, but silent. Mitigated by documentation (CONSUMERS.md
recommends a project-root path, which always works) and asserted as a stated
fact by
`tests/test_runner_result_report.py::test_a_report_in_a_directory_that_does_not_exist_yet_falls_back`.
If a future consumer needs a subdirectory path, the honest fix is a widened
`safeio` contract, not a quiet `True` at this call site.
