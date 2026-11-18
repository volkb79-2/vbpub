# assay Rust R-3a spikes (cargo-mutants)

Measured 2026-11-18 with cargo-mutants 27.1.0, cargo-nextest 0.9.151 and rustc
1.99.1 in `tester-unified-rust:local` on v1001 (`--network none`, 2-3 GiB,
2 CPUs, detached containers, named `assay-r3a-spikes*`). The subjects are
dependency-free synthetic crates, so every run needs no crate cache and takes
seconds. The exact scripts, subjects, diffs and raw reports are in
[rust-r3a/evidence](rust-r3a/evidence/); this document records what they show
and the bounded decisions B245 (R-3b) builds on. It closes B244.

R2 for Rust is adopted as a direction by the operator (T4, 2026-11-18); the
design's "not yet adopted" known issue (K15) stays true until B245 lands.

## What was run

| Spike | Subject | Evidence |
|---|---|---|
| S3a spans, genres, `FnValue` start | `subject/` (add, is_even, make, spin, pick) | `s3-list.json`, `spikes-s3-s4.log` |
| S3b zero-mutant `--in-diff` | comment-only diff | `spikes-s3-s4.log` |
| S3c/d `--in-diff` mismatch and match | `subject-diffs/v2.diff` | `spikes-s3-s4.log`, `indiff/` |
| S3e full run: caught, missed, unviable, timeout | `subject/` | `full/mutants.out/` |
| S3f span units with non-ASCII text, `--in-diff` granularity | `s2/subject/` | `s2/`, `spikes-s3-extra.log` |
| S3g `--test-workspace` in a real two-crate workspace | `workspace/ws/` | `workspace/out/` |
| S3h output reuse and reservation | `subject-v2` rerun | `spikes-s3-reuse.log` |
| S4 baseline failure | `subject-bf` variant | `basefail/mutants.out/` |

## Measured facts

**Span units (S3).** `span.start`/`span.end` are `{line, column}`, 1-based, with
the end exclusive. The column counts Unicode scalar values (characters), not
bytes: on `    let s = "héllo→"; s.len() + a` the `+` is column 31 (character
index) while its byte offset is 34. The conversion to a byte span against the
committed blob decodes the line as UTF-8 and counts characters. CRLF line ends
were not measured; the committed blobs of hostobs are LF.

**`FnValue` start (S3).** The mutant's own `span` starts at the first token of the
function BODY (`add`: line 9 column 5, with the signature on line 8), not at
the signature. `function.span` carries the whole function extent (line 7
column 1 to line 10 column 2). The `name` field is
`file:line:col: replace <what> with <replacement>[ in <function>]`; for
operator genres `<what>` is the original token (`replace == with !=`).

**`--in-diff` granularity (S3).** It is a superset filter at function level, not
line level: a diff touching only `b * 2` (line 7) of `multi` listed the mutants
on the unchanged line 6 (`a + 1`) and the `FnValue` mutant as well. So
`--in-diff` can only reduce cost; assay's own scope rule (mutant start line is
an added line) must do the judging, and design K8 holds (an `FnValue` mutant
whose body starts on an unchanged line is run but not judged).

**Zero-mutant `--in-diff` (S3).** A diff with no overlapping mutants prints
`INFO No mutants to filter`, exits 0 and creates NO output directory at all. An
empty diff prints `INFO Diff file is empty`, exits 0, and also creates nothing.
There is therefore no artifact that says "zero mutants"; see decision D3.

**`--in-diff` mismatch (S3).** A diff whose new side does not match the tree
exits 5 with `Diff content doesn't match source file` and writes no report.

**Exit codes (S3).** 0 all caught, 2 survivors, 3 timeouts, 4 baseline failed, 5
diff mismatch. With survivors AND a timeout in one run the exit is 3, so a
timeout masks the survivors in the exit code; the report is the only complete
source.

**Outcome shapes (S3, `full/mutants.out/outcomes.json`).** 25 mutants: 16
`CaughtMutant`, 7 `MissedMutant`, 1 `Unviable`, 1 `Timeout`, plus the
`Baseline` `Success` entry. Every caught mutant had a Test phase
`{"Failure": 100}`. `Unviable` has a Build phase `{"Failure": 101}` and NO Test
phase. `Timeout` has a Test phase `"Timeout"` after the declared 20 s.
`end_time` is set on a completed report; `diff_path` points at a per-mutant
`diff/*.diff` and `log_path` at `log/*.log`. `mutants.json` (listing) carries
the same mutants with an embedded `diff` string.

**Baseline failure (S4).** Exit 4. `outcomes.json` IS written and complete
(`end_time` set) but contains ONLY the `Baseline` outcome with summary
`Failure` and a Test phase `{"Failure": 100}`, and `total_mutants` is 0 (not
the 25 listed). Test status alone cannot separate a baseline failure from a
kill: both are `Failure(100)`; the scenario discriminates.

**`--test-workspace` (S3g).** In a two-crate workspace where crate `a` has no
tests of its own (its only test is in `b`), the default full-workspace run
tests each mutant of `a` with `--workspace` and all 5 are caught with Test
`{"Failure": 100}`. With `--test-workspace=false` the same 5 mutants are tested
with `--package=a@0.1.0` only: nextest finds no tests, exits 4, and
cargo-mutants reports them `CaughtMutant` with Test `{"Failure": 4}`. That is
a FALSE kill from cargo-mutants' point of view, and it is exactly what the
design's rule "`Failure(100)` is a kill, any other Test status is `crashed`"
absorbs. The design claim that `--test-workspace=false` "can only add
survivors" is therefore corrected: it can also turn test-less crates into
`crashed`, which is a loud verdict but a different one. Combined with
`--package a`, `--test-workspace=true` is ignored (the argv stays
`--package=a@0.1.0`); measure the real flag set on hostobs before fixing the
lane command.

**Output reuse (S3h).** `--output DIR` creates `DIR/mutants.out`; the source tree
gets no file and the run happens in a `/tmp/cargo-mutants-*.tmp` copy, so the
dirty-tree check has nothing to see. A second run into the same `--output`
rotates the first result to `mutants.out.old` and writes a fresh
`mutants.out`. `--output` pointing at an existing file fails with `Not a
directory`.

## Decisions for B245 (bounded, from the measurements)

**D1 Units.** Convert spans with `(line, character column) -> byte offset` on the
committed blob, end exclusive. A span outside the blob, or whose text at the
span does not equal the original token named in `name` for the operator
genres, refuses the report (`UNREADABLE_ARTIFACT`). Content binding does not
need the `diff/` files: `name`, `span`, `replacement` and the committed blob
suffice for `BinaryOperator`/`UnaryOperator`; `FnValue` binds `function_name`
to the `fn` token at `function.span.start`.

**D2 Reservation target.** Reserve the single file
`<output>/mutants.out/outcomes.json`, with the lane's `--output` directory
outside the snapshot. A pre-existing `mutants.out` or `mutants.out.old` is a
refusal, so a stale report cannot be accepted. The reader needs no other file.

**D3 Zero mutants.** Absence of the output directory after exit 0 is the only
signal of a zero-mutant `--in-diff` run, and it is indistinguishable from "the
tool did not run". OPEN, recommended: the reader accepts the absent report
only when the lane declaration includes `--in-diff "$ASSAY_CHANGED_DIFF"`
(checked at lane load), the run's R0 exit is 0 and assay's own changed-line
scope for the lane is non-empty; the verdict records the vacuous outcome as
data (zero candidates), never as a pass of unmeasured code. Any other absence
is `UNREADABLE_ARTIFACT`. This choice needs the reviewer's explicit sign-off.

**D4 Result report completeness (B078 seam).** Complete means `end_time` set and a
`Baseline` outcome present. A `Baseline` summary other than `Success` is a
lane FAIL (`total_mutants` is then 0). Exit 2 and 3 do not decide R0 once a
complete report exists; exit 5, 1 and 70 leave no report and stay an exit-code
FAIL.

**D5 Status map.** As the design table, with measured grounding: `CaughtMutant` + Test
`{"Failure": 100}` only is `killed`; any other Test status (`{"Failure": 4}` as
measured, `Signalled`, `Other`) is `crashed`; `Unviable` is `discarded`
(compile error, Build `Failure(101)`, no Test phase); `Timeout` is
`budget_exceeded`; unknown summary or phase key refuses the report. Crashed
mutants in a test-less crate are the loud signal for `--test-workspace=false`.

**D6 Schema.** `INGESTED_OPERATOR_NAMESPACES` is the single owner of the
`mutation_operator` pattern branch; adding `cargo-mutants` widens the verdict
schema, which is a hard cut (the verdict is at v19 and "no dual-version
verifier" applies). B550 (`judgment.r1.coverage_producer`) also needs a schema
field. Plan ONE v20 cut that carries both, with a major assay release. The
operator values are `cargo-mutants:FnValue`, `:BinaryOperator`, `:UnaryOperator`,
`:MatchArm`, `:MatchArmGuard`, `:StructField`; the observed set in these
spikes is `FnValue`, `BinaryOperator`, `MatchArmGuard`.

**D7 `ASSAY_CHANGED_DIFF`.** Confirmed absent today; no `ASSAY_BASE`-like variable
exists in `runner.py`. It is exported only to ingested changed-lines lanes,
as a path outside the snapshot to the diff between the resolved base and the
candidate commit, in git format with `a/` and `b/` prefixes (the format the
spikes' diffs used and cargo-mutants accepted).

**D8 Flags to qualify, not assume.** `--test-tool nextest`, `--jobs`, `--timeout`
(or `--timeout-multiplier` with `--minimum-test-timeout`), `--output`,
`--cargo-arg=--offline`, `--in-diff`. `--test-workspace` stays an explicit
per-lane choice with the false-kill behaviour above documented in the lane.

## Not measured (stays open)

- CRLF sources and `\r` in spans.
- `--shard k/n` interaction with `--in-diff` (assay refuses shards on ingested
  lanes, so not needed for R2; needed for the R-6 advisory lanes).
- hostobs-scale cost: the design's 5-20 minute estimate for a diff-scoped run
  is unverified; the synthetic subject only proves mechanics (25 mutants in
  27 s, of which 20 s was the timeout mutant).
- A real `Signalled` or `Other` Test status; no spike produced one. The B245
  fixtures for these are synthetic and must be labelled so.
- `--output` on a read-only or shared filesystem.
