# assay Rust R-3a spikes (cargo-mutants)

Measured 2026-11-18 with cargo-mutants 27.1.0, cargo-nextest 0.9.151 and rustc
1.99.1 in `tester-unified-rust:local` on v1001 (`--network none`, 2-3 GiB,
2 CPUs, detached containers named `assay-r3a-spikes*`). The subjects are
dependency-free synthetic crates plus one two-crate workspace, so no crate cache
is needed and a run takes seconds. Scripts, subjects, diffs and raw reports are
in [rust-r3a/evidence](rust-r3a/evidence/) (see its README for the
script-to-log map). This document records what they show and the bounded
decisions B245 (R-3b) builds on. It closes B244 as MEASUREMENT; the decisions
marked OPEN below are B245's carve inputs, not settled.

The operator named R-3 (cargo-mutants, R2 at T3) as the next Rust track on
2026-11-18, which is a direction, not a K15 closure: design K15 ("Rust R2 is
not adopted yet") stays true until B245 lands.

## Measured facts

Every statement here rests on a retained file; "(log)" means the line is in the
named log, "(report)" that it is in a retained `outcomes.json`/`mutants.json`.

**Span units.** `span.start`/`span.end` are `{line, column}`, 1-based, end
exclusive, columns counted in Unicode scalar values (characters), not bytes.
On `    let s = "héllo→"; s.len() + a` the `+` is column 31; its byte column would
be 34 (`s2/s2-uni-list.json`). CRLF sources were not measured.

**Item and body starts.** `function.span` covers the whole item including its
doc comment and attributes: `add` starts at 7:1 (the `///` line) and ends 10:2,
while `pub fn add` is on line 8; `multi` (no doc comment) starts at 5:1 on `pub`
(`s2/s2-multi-indiff.json`). The `FnValue` mutant's own `span` starts at the
first token of the BODY (`add`: 9:5). `name` is
`file:line:col: replace <what> with <replacement>[ in <function>]`; for the
operator genres `<what>` is the original token, for `MatchArmGuard` it is the
guard text (`replace match guard v > 10 with true in pick`, span text `v > 10`).

**`--in-diff` is a function-level superset.** A diff touching only `b * 2`
(line 7) listed the mutants on the unchanged line 6 (`a + 1`) and the `FnValue`
mutant too (`s2/s2-multi-indiff.json`, `spikes-s3-extra.log`). It can only
reduce cost; assay's own scope rule must do the judging (design K8 holds). A
real `git diff` (with `index` and hunk-context headers) is accepted, and so is
the design's flag set `--jobs 2 --timeout-multiplier 10 --minimum-test-timeout
120 --cargo-arg=--locked` (`respike/spikes5b.log`, `respike/real-git.diff`).
Non-`.rs` files, renames and deletions in the diff were not tried.

**No artifact when nothing runs.** Real (not `--list`) runs with a comment-only
diff, with an empty diff, and with a diff whose new side mismatches the tree
leave NO `--output` directory at all (`respike/spikes5.log` steps 1-3: `ls`
reports `No such file or directory` for each). The first two print `INFO No
mutants to filter` / `INFO Diff file is empty` and exit 0; the mismatch run
prints `Diff content doesn't match source file` and exits 5.

**Exit codes.** 0 all caught, 2 survivors, 3 timeouts, 4 baseline failed, 5 diff
mismatch (log). A run with survivors AND a timeout exits 3: the exit code
masks the survivors and the report is the only complete source. Exits 1 and 70
are upstream-documented; exit 1 was seen only as a usage error.

**Outcome shapes** (`full/mutants.out/outcomes.json`). 25 mutants: 16
`CaughtMutant`, 7 `MissedMutant`, 1 `Unviable`, 1 `Timeout`, plus the
`Baseline` `Success` entry; `end_time` is set. Every caught mutant has Test
`{"Failure": 100}`. `Unviable` has Build `{"Failure": 101}` and no Test phase.
`Timeout` has Test `"Timeout"` after the declared 20 s. `diff_path` points at a
per-mutant `diff/*.diff` (minus-lines are the original source),
`log_path` at `log/*.log`; `mutants.json` also carries an embedded `diff` string.

**Baseline failure.** Exit 4, `outcomes.json` written and complete (`end_time`
set), containing ONLY the `Baseline` outcome with summary `Failure` and Test
`{"Failure": 100}`; `total_mutants` is 0 although 25 were listed
(`basefail/mutants.out/outcomes.json`). Test status alone cannot tell a
baseline failure from a kill; the scenario decides.

**`--test-workspace` default is per-package, and a test-less crate "dies
caught".** In a two-crate workspace whose only test lives in crate `b`, mutants
of crate `a` are tested with `--package=a@0.1.0` when the flag is UNSET and
when it is `=false`; nextest finds no tests, exits 4, and cargo-mutants reports
`CaughtMutant` with Test `{"Failure": 4}`. With `=true` the argv is
`--workspace` and the same mutants are `CaughtMutant` with `{"Failure": 100}`
(`respike/w-*/mutants.out/outcomes.json`, `respike/spikes5.log` step 6). So the
false kill is the DEFAULT behaviour for a crate whose tests live elsewhere, and
the design rule "only `Failure(100)` is a kill, any other Test status is
`crashed`" is what keeps it from scoring. The baseline itself ran both
packages and passed. With `--package a` filtering (`workspace/out/spikes4.log`,
the reports of that run were not retained) the baseline fails with exit 4
because `a` has no tests, so no mutant runs at all.

**Output reuse and rotation.** `--output DIR` creates `DIR/mutants.out`; the
source tree gets no file (the run happens in a `/tmp/cargo-mutants-*.tmp`
copy). A second run into the same `--output` rotates the first result to
`mutants.out.old` (`spikes-s3-reuse.log`). A PRE-CREATED EMPTY `mutants.out`
is also rotated to `mutants.out.old`, and a fresh `mutants.out` is made
(`respike/spikes5.log` step 4); a pre-created empty `--output` directory is
simply used (step 5). `--output` pointing at an existing file fails with
`Not a directory` (`spikes-s3-reuse.log`; the `exit=0` lines there are the
exit status of a `tail` pipeline, not the tool).

**Environment variables.** cargo-mutants reads `CARGO_MUTANTS_JOBS`,
`CARGO_MUTANTS_MINIMUM_TEST_TIMEOUT`, `CARGO_MUTANTS_OUTPUT` and
`CARGO_MUTANTS_TRACE_LEVEL`. `--in-diff` has NO environment spelling
(`respike/spikes5b.log`; `CARGO_MUTANTS_IN_DIFF` filters nothing).

## What the code says (verified 2026-11-18 against `main` 8688e28da)

- `runner._reserve_result_report` reserves a PROJECT-RELATIVE lexical path with
  `safeio.reserve_output`, which holds an fd on the final parent directory and
  rejects absolute, empty, `.` and `..` spellings. `config.ResultReportConfig.path`
  is relative to the directory the lane's command runs in. The direct R0 path
  keeps `create_missing_parents=False` (a missing parent returns no reservation,
  and the exit-code rule decides); the snapshot path creates the parent itself.
- Lane commands are an exact argv list run through `Popen(list(argv))`: no
  shell and no placeholder substitution in assay (`runner.py:419`).
- No `ASSAY_BASE`-like or `ASSAY_CHANGED_DIFF` variable exists in `src`.
- The verdict schema is v19 (`VERDICT_SCHEMA_VERSION`); the verifier refuses the
  previous version with no compatibility shim. The `mutation_operator` pattern
  exists in TWO places: `INGESTED_OPERATOR_NAMESPACES`/`INGESTED_OPERATOR_RE`
  in `vocabulary.py` and a literal `^(?:stryker):[A-Za-z0-9]+$` in
  `schemas/verdict.schema.json` (kept in step by a test);
  `mutation_report_json.py:96` hardcodes `INGESTED_OPERATOR_NAMESPACES[0]`.
- `judgment.r1.coverage_producer` already exists (B045, a free string, schema
  v19). B550 is a missing `COVERAGE_PRODUCERS_BY_FORMAT` entry for lcov and its
  own close condition allows a docs-only fix; it does not need a schema bump.
- `mutation.INGESTED_STATUS_BUCKETS` has no `crashed` bucket and is keyed on one
  upstream status string.
- The existing ingested path ends a zero-candidate report in INCONCLUSIVE with
  `NO_MUTANTS` (`verdict.py`), never PASS.

## Decisions for B245 (OPEN ones need the carve review)

**D1 Span and content binding (DECIDED).** Convert `(line, character column)` to
a byte offset on the committed blob (decode as UTF-8, count characters, end
exclusive). A span outside the blob refuses the report (`UNREADABLE_ARTIFACT`).
Binding per genre: operator genres (`BinaryOperator`, `UnaryOperator`) require
the blob text at `span` to equal the original token in `name`; `MatchArmGuard`
requires `name` to contain the blob text at `span`; `FnValue` requires
`function_name` to appear as `fn <name>` inside the blob text of
`function.span` (which starts at the item's doc comment or attribute, not at
`fn`). In addition the per-mutant `diff/*.diff` is the content tier the design
already named: its minus-lines must equal the committed blob at those lines,
because `outcomes.json` alone does not carry the original text and `FnValue`
(10 of 25 mutants) cannot otherwise be bound to the body.

**D2 Directory-artifact reservation (OPEN, new capability, blocks the L size
estimate).** The existing reservation cannot be used as is. It holds the parent
directory fd, and cargo-mutants renames ANY existing `mutants.out` (even an
empty one that assay's `create_missing_parents` just made) to `mutants.out.old`
before writing, so the held fd would end up on `.old`, find no
`outcomes.json`, and every honest run would fall to the exit-code rule. Also
the report path must be project-relative inside the lane's cwd or snapshot
(`--output <relative dir>`), not "outside the snapshot". Required shape: a
reservation for a directory artifact that (a) checks before the command that
neither `<out>/mutants.out` nor `<out>/mutants.out.old` exists and does not
create `mutants.out`, (b) after the command opens `<out>/mutants.out/outcomes.json`
and the `diff/` files through a `O_NOFOLLOW` chain, (c) requires `.old` still
absent and the report's `start_time` and `end_time` inside the command's observed
wall window. New code in `safeio`/`runner`, not a lane-level trick.

**D3 Zero mutants (DECIDED 2026-11-18, left to the assay owner by the operator,
adopting the recommendation below).** Missing output directory after exit 0
is the only signal of a zero-mutant `--in-diff` run and cannot be told apart
from a wrapper that never ran the tool, so assay must not accept it as
evidence of coverage. Rule: the reader returns no report, and the lane
ends INCONCLUSIVE/`NO_MUTANTS` (the existing ingested terminal), never PASS and
never ERROR. For R2 at T3 (advisory, async) an inconclusive "nothing to mutate"
on a docs-only diff is the honest answer. The rejected alternative is a
textual rule keyed on the declared argv (`--list --in-diff X` exits 0 with no
report and would satisfy it).

**D4 Result-report completeness (DECIDED with a caveat).** Complete means
`end_time` set and a `Baseline` outcome present. `Baseline` summary other than
`Success` is a FAIL: the report has `total_mutants` 0, which the existing
`verify_complete` shape (requires a positive total) cannot express, so the
FAIL comes from the exit code 4 path with the reader refusing the report with
an explicit reason; B245 must keep that reason distinguishable in the verdict.
Exits 2 and 3 do not decide R0 once a complete report exists; exits 5, 1 and
70 leave no report and stay exit-code FAILs.

**D5 Status map (DECIDED, needs a normaliser).** `CaughtMutant` with Test
`{"Failure": 100}` is `killed`; `CaughtMutant` with any other Test status
(`{"Failure": 4}` measured, `Signalled`, `Other`) is `crashed`; `MissedMutant`
is `survived`; `Unviable` is `discarded` (Build failure, no Test phase);
`Timeout` is `budget_exceeded`; an unknown summary, phase or status key refuses
the report. `INGESTED_STATUS_BUCKETS` has no `crashed` entry and keys on one
string, so B245 needs a normalising layer (summary + Test status to the assay
status) in the parser, then maps that to the existing buckets; the bucket
table gains a `crashed` row or the parser discards with a reason.

**D6 Schema (DECIDED 2026-11-18: the operator approved a v20 verdict schema for
anything B245 needs).** Adding the `cargo-mutants` operator namespace changes the
`mutation_operator` pattern in `schemas/verdict.schema.json` and
`vocabulary.py`: a v19 to v20 hard cut and a major assay release, together
with the test that keeps the two copies in step. B550 is independent: it needs
only a vocabulary entry, so it is NOT coupled to this cut. Namespace members:
`FnValue`, `BinaryOperator`, `UnaryOperator`, `MatchArm`, `MatchArmGuard`,
`StructField` (design list); observed here: `FnValue`, `BinaryOperator`,
`MatchArmGuard`. B245 pays the cut itself and may put any other wire field it
needs (a directory-artifact digest, a diff-scope record) into the same v20; no
second cut is planned for Rust R2.

**D7 Diff handoff (OPEN, replaces `ASSAY_CHANGED_DIFF`).** The lane receives its
arguments as a fixed argv with no shell and no substitution, and cargo-mutants
has no environment spelling for `--in-diff`, so an exported variable cannot
reach it. Recommended: an assay-owned argv placeholder (for example
`{changed_diff}`) accepted only in ingested changed-lines lanes, validated at
lane load, substituted by the runner with the path to
`git diff <resolved base>..<commit>` written outside the snapshot, in git
format with `a/`/`b/` prefixes (measured accepted). The rejected alternative is
a `bash -c` wrapper (hand-written fail-open territory, per the design).

**D8 Flags (DECIDED).** The lane uses `--test-tool nextest --jobs N
--timeout-multiplier 10 --minimum-test-timeout 120 --cargo-arg=--locked
--cargo-arg=--offline --in-diff <placeholder> --output <relative dir>` and an
EXPLICIT `--test-workspace` choice. Because the unset default is per-package
and turns a crate with no tests of its own into `crashed`, the lane
documentation must say which choice a workspace lane makes and why; the
hostobs lane must be measured with the real crate set before the choice is
fixed.

## Not measured (stays open)

- CRLF sources and `\r` in spans.
- Non-`.rs` files, renames and deletions in the diff; `--in-diff` with several
  crates in one diff.
- `--shard k/n` with `--in-diff` (assay refuses shards on ingested lanes; needed
  for the R-6 advisory lanes only).
- hostobs-scale cost: the design's 5-20 minute estimate for a diff-scoped run is
  unverified; the synthetic subjects prove mechanics (25 mutants in 27 s, 20 s
  of which was the timeout mutant).
- A real `Signalled` or `Other` Test status; no spike produced one, so B245's
  fixtures for them are synthetic and must be labelled so.
- Zero mutants after `exclude_re` filtering in a full (non-diff) run.
- `--output` on a read-only or shared filesystem.
