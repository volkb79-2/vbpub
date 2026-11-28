# assay — normative specification

Status: normative for assay at verdict schema v20, `assay-capabilities/2`,
lane schema 2. Where README.md, docs/CONSUMERS.md or docs/DESIGN-GUIDE.md
disagree with this document, this document (and the code it cites) wins.

## 0. How to read this document

- **Requirement ids** are `<CHAPTER>-<NNN>`, e.g. `VER-030`. The prefix names
  the chapter (`PUR`, `RIG`, `VER`, `JID`, `STO`, `LIV`, `TIM`, `CLI`, `CON`).
  Numbers start at 010 and step by 10, so a requirement can be inserted
  between two others (`VER-035`). An id is never renumbered or reused; a
  withdrawn requirement keeps its id with the text `WITHDRAWN (<date>, why)`.
- **MUST / MUST NOT / MAY** are normative. Each implemented requirement ends
  with its code citation `path:line`, relative to `assay/`.
- **PLANNED** marks behaviour that is not implemented. It cites its source,
  never code, and never shares a requirement with implemented behaviour.
- **Deprecation candidate:** / **Cleaner future design:** notes follow a
  requirement whose current behaviour is awkward. They are not requirements.
- Out of scope: the `assay analyze` sub-tool (`analysis/`), adapter-internal
  parsing rules, and the full field-by-field verdict shape (the schema file
  `src/assay/schemas/verdict.schema.json` is normative for shape).

## 1. What assay is for (PUR)

- **PUR-010** assay judges ONE lane of ONE commit and states the result as a
  verdict whose exit code alone is never wrong: a consumer that reads only the
  exit code MUST NOT be misled, except for the explicit dirty-tree override
  recorded under CON-075. `src/assay/errors.py:6`
- **PUR-020** assay measures; it does not choose. It runs exactly the command
  a lane declares, never discovers tests, never retries them except under
  RIG-365, and never turns an absent measurement into a pass.
  `src/assay/cli.py:360`, `src/assay/verdict.py:972`
- **PUR-030** Rigor is the strength of evidence: R0 the command succeeded,
  R1 the changed (or targeted) code was executed, R2 the tests kill
  mutations of it, R3 a known defect is caught. Each declared level is a
  separate claim (VER-040).

What would help more, or be cleaner (non-normative):

1. One time base for every bound and every recorded duration (TIM chapter):
   today wall time decides outcomes on a loaded host.
2. One capability surface: `assay capabilities` and the `capabilities` list in
   `assay lanes --json` are two unrelated mechanisms (CON-030, CON-040).
3. A lane-level vocabulary for "did not finish" that separates timeout,
   stall, cancellation and never-started, instead of one `BUDGET_EXCEEDED`
   bucket carrying all four (VER-090, LIV-050, LIV-070).
4. `assay verify` and the pilot mode reuse exit codes whose meaning differs
   from the verdict exit codes (CLI-040, CLI-050).

## 2. Rigor levels (RIG)

- **RIG-010** The rigor ladder is closed and ordered: `R0`, `R1`, `R2`, `R3`,
  `R4`. A lane declares an ordered, R0-led subsequence (non-contiguous allowed,
  e.g. `["R0","R2"]`). `src/assay/config.py:196`
- **RIG-020** Each declared level requires its own `judge` fields; an R0-only
  lane has no `[judge]` table. R1: `language`, `source_roots`, `fail_under`,
  `allow_excluded`, `coverage`, `base`; R2: `language`, `source_roots`,
  `mutation`, `base`; R3: `language`, `source_roots`, `canary`.
  `src/assay/config.py:309`
- **RIG-030** `R4` (red-first) is reserved: a lane declaring it is refused at
  load with `ERROR/BAD_LANE_CONFIG`, because its required field `red_first` is
  not a known judge field. The verdict schema already carries the `R4` wire
  shape and `RED_FIRST_UNPROVEN`. `src/assay/config.py:196`,
  `src/assay/config.py:328`, `src/assay/errors.py:103`
  Deprecation candidate: a reserved rung visible in the schema and reason
  vocabulary but unreachable; either ship its producer or drop it at v21.
- **RIG-040** A rigor level is reachable for a language only if the built-in
  registry lists it: Python `{R1,R2,R3}`, SQL `{R2}`, JavaScript `{R1,R2,R3}`,
  Go `{R1}`, Rust `{R1,R2}`. R0 needs no adapter and is reachable for any
  command. `src/assay/cli.py:1883`

### 2.1 R0 — the command

- **RIG-110** R0 runs the lane's effective argv once. Without `result_report`
  it PASSes iff the exit status is 0. With `result_report` the verified report
  decides both ways: zero failures PASSes over a non-zero exit, any failure
  FAILs over exit 0. A completed command that does not pass is
  `FAIL/COMMAND_FAILED`; a lane timeout is `BUDGET_EXCEEDED/LANE_TIMEOUT`;
  failure to execute, or descendants outliving the leader, is
  `ERROR/EXEC_FAILED`. `src/assay/runner.py:1977`, `src/assay/runner.py:2023`,
  `src/assay/runner.py:1842`, `src/assay/runner.py:1921`
  Cleaner future design: a report-decided PASS over a non-zero exit is
  visible only as present output tails; record the override as an explicit
  field.
- **RIG-120** An R0-only lane runs in the live checkout; any lane declaring a
  higher level runs in a committed-object snapshot.
  `src/assay/runner.py:8489`
- **RIG-130** Before the command, a snapshot lane refuses
  `NO_MEASUREMENT/DIRTY_TREE` when the loaded `assay.toml` is uncommitted, or
  when an uncommitted path matches no `isolation.dirty_ignore` pattern and
  `--allow-dirty` is absent. An R0-only lane refuses any uncommitted path in
  the live tree; `--allow-dirty` does not apply to it. After the command (live
  tree for R0, the snapshot otherwise, each mutant's snapshot for native R2) a
  dirty tree is `NO_MEASUREMENT/DIRTY_TREE` and a clean tree whose HEAD moved
  is `NO_MEASUREMENT/HEAD_CHANGED`; dirt takes precedence. Dirt or HEAD drift
  left by a mutant refuses the lane and is never bucketed `crashed`.
  `src/assay/runner.py:3661`, `src/assay/runner.py:3681`,
  `src/assay/runner.py:4267`, `src/assay/runner.py:8557`,
  `src/assay/runner.py:8840`, `src/assay/mutation.py:2092`,
  `src/assay/mutation.py:4795`
- **RIG-135** With `--allow-dirty`, admitted paths are recorded in
  `worktree_integrity.overridden_dirty_paths` and `dirty_ignore` matches in
  `ignored_dirty_paths`. `src/assay/cli.py:434`, `src/assay/runner.py:3693`

### 2.2 R1 — coverage

- **RIG-210** Mode `changed_lines` (default when `judge.mode` is absent)
  judges executable lines changed since the resolved base; `whole_target`
  judges every executable line of each declared target.
  `src/assay/config.py:366`, `src/assay/evaluate.py:457`,
  `src/assay/evaluate.py:1167`
- **RIG-220** `pct = 100 * (covered lines + covered branch arcs) /
  (executable lines + branch arcs)`; a zero denominator is 100.
  `src/assay/evaluate.py:682`, `src/assay/evaluate.py:1324`
  Cleaner future design: a changed-lines run with zero changed executable
  lines PASSes at 100 %; a distinct "nothing to judge" disposition would be
  more honest.
- **RIG-230** Outcome precedence (changed_lines): unclassified lines →
  `FAIL/UNCLASSIFIED_LINES`; disallowed excluded lines → `FAIL/EXCLUDED_LINES`;
  `pct < fail_under` → `FAIL/UNCOVERED_BRANCHES` when no line is missing and an
  arc is uncovered, else `FAIL/UNCOVERED_LINES`; otherwise PASS. whole_target
  has the same order without the unclassified step.
  `src/assay/evaluate.py:688`, `src/assay/evaluate.py:449`,
  `src/assay/evaluate.py:1330`
- **RIG-240** Measurability guards come first, each `NO_MEASUREMENT`: an
  artifact with zero measured files → `EMPTY_COVERAGE`; `require_branch = true`
  over an artifact without branch data → `BRANCH_UNAVAILABLE`; a whole_target
  target absent or with zero executable lines → `TARGET_NOT_MEASURED`;
  base equals HEAD → `BASE_IS_HEAD`. `src/assay/coverage.py:335`,
  `src/assay/runner.py:2918`, `src/assay/runner.py:2929`,
  `src/assay/evaluate.py:1297`,
  `src/assay/measurability.py:102`
- **RIG-250** Coverage formats are closed: `coverage-py-json`, `lcov`,
  `cobertura`, `go-cover`, `coverage-istanbul-json`; the artifact is sniffed
  against the declared format before parsing. `coverage-istanbul-json`
  requires `producer`. `src/assay/coverage.py:105`,
  `src/assay/vocabulary.py:226`, `src/assay/vocabulary.py:277`
- **RIG-260** Per language: Go statement lines are re-derived from source
  (`go` must be on PATH, else `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL` before the
  command); Go and stable-Rust artifacts carry no branch data.
  `src/assay/vocabulary.py:311`, `src/assay/cli.py:1754`

### 2.3 R2 — mutation

- **RIG-310** R2 has two producers. **native** (Python, SQL): assay generates
  candidates from the closed per-language operator catalogue and runs each.
  **ingested** (JavaScript via `mutation-report-json`, Rust via
  `cargo-mutants-outcomes`): an external tool runs the mutants and assay
  scores its completed report. `src/assay/verdict.py:3299`,
  `src/assay/vocabulary.py:154`, `src/assay/mutation_parsers/__init__.py:102`
- **RIG-320** A native lane for a language without a native engine
  (JavaScript, Rust) is refused at load (foreign/unknown operator); Go is not
  registered at R2 at all. `src/assay/cli.py:1833`, `src/assay/cli.py:1869`, `src/assay/cli.py:1897`
- **RIG-330** The R2 claim is derived in this order: baseline (unmutated)
  non-PASS → the baseline's own outcome; no engine →
  `INCONCLUSIVE/MUTATION_UNSUPPORTED`; zero attempted but candidates beyond
  discards → `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`; zero attempted →
  `INCONCLUSIVE/NO_MUTANTS`; any `crashed` → `ERROR/EXEC_FAILED`; any
  `budget_exceeded` → `BUDGET_EXCEEDED/LANE_TIMEOUT`; any `hung` →
  `BUDGET_EXCEEDED/CANDIDATE_HUNG`; `survived` and score below `fail_under` →
  `FAIL/MUTANTS_SURVIVED`; only `equivalent` (no killed, no survived) →
  `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT`; else PASS.
  `src/assay/mutation.py:5592`
- **RIG-340** The score is `100 * killed / (killed + survived)`; 0/0 is 0.
  `crashed`, `budget_exceeded`, `hung` and `equivalent` never enter it.
  `src/assay/mutation.py:5567`
- **RIG-350** The floor `fail_under` applies to ingested lanes only and is
  recorded in `judgment.r2.fail_under`; a native lane's floor is fixed at 100
  (any survivor FAILs). `src/assay/mutation.py:5592`
- **RIG-360** Native candidate classification uses one termination table:
  passed → `survived`, failed → `killed`, timed out → `budget_exceeded`,
  hung → `hung`, everything else (not run, killed externally, self-signalled,
  leaked process tree, resource-limit event, unobserved, inconclusive) →
  `crashed`. With a declared equivalence artifact, a byte-equal artifact is
  `equivalent` and a missing one `crashed`. A passed run is `survived`
  regardless of resource observation; a failed run is `killed` only with clear
  observation or a trusted failed-call witness, else `unobserved` (`crashed`).
  A resource-limit event outranks both. A record's `forced_bucket` (only
  `crashed`) overrides a judgeable termination. On a lane declaring
  `judge.mutation.kill_signal_artifact`, a `killed` run without a kill signal
  is `crashed`. `src/assay/termination.py:49`, `src/assay/termination.py:64`,
  `src/assay/termination.py:88`, `src/assay/termination.py:97`,
  `src/assay/termination.py:109`, `src/assay/mutation_store.py:4580`,
  `src/assay/mutation_store.py:1784`, `src/assay/mutation_store.py:4629`
- **RIG-365** After the sweep, unless a campaign-deadline mask fired, each
  candidate executed in this invocation with bucket `crashed`,
  `budget_exceeded` or `hung` is re-run once, serially, in plan order, while
  the remaining lane deadline is at least `budget_per_candidate` (no such stop
  when it is `none`). The retry's bucket is final and persisted; resumed
  records are never retried. `src/assay/mutation.py:4807`,
  `src/assay/mutation.py:4817`
- **RIG-370** A native R2 run whose lane process cannot observe its cgroup v2
  ancestry is refused before the baseline: `NO_MEASUREMENT/
  CGROUP_OBSERVATION_UNAVAILABLE`. `src/assay/mutation.py:1715`

### 2.4 R3 — canary

- **RIG-410** An R3 claim runs 1–8 declared canary targets, each a known-good
  control plus a transformed run; aggregation is `any` (first PASS
  short-circuits) or `all`. `src/assay/verdict.py:869`,
  `src/assay/verdict.py:878`, `src/assay/canary.py:1279`
- **RIG-420** Per target: broken control, malformed mechanism or no-op
  transform → `INCONCLUSIVE/CANARY_INCONCLUSIVE`; transformed run PASSes or
  fails with a reason other than the expected one → `FAIL/CANARY_SURVIVED`;
  else PASS. `src/assay/canary.py:1236`
- **RIG-430** R3 is registered for Python and JavaScript only; Go R3 and Rust
  R3 are refused by the registry. `src/assay/cli.py:1883`

## 3. Verdict contract (VER)

- **VER-010** Outcomes are closed, each with a frozen exit code: `PASS` 0,
  `FAIL` 1, `ERROR` 2, `NO_MEASUREMENT` 3, `BUDGET_EXCEEDED` 4,
  `INCONCLUSIVE` 5. Every non-zero outcome is not-a-pass; whether it blocks
  is the consumer's application of `enforcement` (VER-100).
  `src/assay/errors.py:41`, `src/assay/errors.py:58`
- **VER-020** Reason codes are closed and partitioned by outcome; `PASS`
  carries none (the key is omitted, never null), every other outcome carries
  exactly one from its own set. `src/assay/errors.py:70`,
  `src/assay/errors.py:225`, `src/assay/verdict.py:997`

| Outcome | Reason codes |
|---|---|
| FAIL (measured, code judged bad) | UNCOVERED_LINES, UNCOVERED_BRANCHES, EXCLUDED_LINES, UNCLASSIFIED_LINES, MUTANTS_SURVIVED, CANARY_SURVIVED, COMMAND_FAILED, RED_FIRST_UNPROVEN |
| ERROR (tool or input broken) | GIT_FAILED, UNREADABLE_ARTIFACT, FORMAT_MISMATCH, BAD_LANE_CONFIG, EXEC_FAILED, OUTPUT_WRITE_FAILED, MUTATION_DISCOVERY_FAILED |
| NO_MEASUREMENT (subject unknown) | BRANCH_UNAVAILABLE, TARGET_NOT_MEASURED, DIRTY_TREE, HEAD_CHANGED, BASE_IS_HEAD, EMPTY_COVERAGE, MISSING_ATTESTATION, STALE_ATTESTATION, MISSING_EXTERNAL_TOOL, PROVENANCE_UNVERIFIED, CGROUP_OBSERVATION_UNAVAILABLE |
| BUDGET_EXCEEDED (did not finish) | LANE_TIMEOUT, MUTANT_LIMIT_EXCEEDED, SNAPSHOT_LIMIT_EXCEEDED, CANDIDATE_HUNG |
| INCONCLUSIVE (nothing proven) | NO_MUTANTS, MUTATION_UNSUPPORTED, ALL_MUTANTS_EQUIVALENT, CANARY_INCONCLUSIVE |

- **VER-030** The verdict outcome is the rollup of all claim and evidence
  statuses with precedence `ERROR > NO_MEASUREMENT > BUDGET_EXCEEDED > FAIL >
  INCONCLUSIVE`; PASS only when every status is PASS; an empty set is never a
  PASS. The artifact's `exit_code` equals the outcome's code.
  `src/assay/verdict.py:805`, `src/assay/verdict.py:972`,
  `src/assay/verdict.py:5939`, `src/assay/verdict.py:6840`
- **VER-040** `claims` holds exactly one computed claim per declared rigor
  level, no more, no fewer. `src/assay/verdict.py:5882`
- **VER-050** Externally sourced evidence (`attested`, `adjudicated`) lives in
  `declared_evidence`/`evidence`, keyed by `(source, key)`, and the two arrays
  cover the same identities exactly; it is not rigor. `src/assay/verdict.py:777`,
  `src/assay/verdict.py:5908`
- **VER-060** Absent means unknowable, empty means known-empty. The
  lane-resolved field group (`declared_rigor`, `declared_evidence`, `argv_*`,
  `env_*`, `scope`, `enforcement`) is all present or all absent; a refusal
  before the lane resolved carries none of them. `src/assay/verdict.py:819`,
  `src/assay/verdict.py:5818`
- **VER-070** A claim carrying `MUTANTS_SURVIVED`, `NO_MUTANTS` or
  `ALL_MUTANTS_EQUIVALENT` MUST carry the mutation payload it was read from.
  `src/assay/verdict.py:794`, `src/assay/verdict.py:4907`
- **VER-080** Mutation buckets are closed, in wire order: `killed`,
  `survived`, `crashed`, `budget_exceeded`, `equivalent`, `hung`. `hung` is
  native-only; ingested reports never produce it. `src/assay/verdict.py:1058`
- **VER-090** Candidates excluded by `--only-candidates`/
  `--exclude-candidates` and not covered by a resumed or carried record are
  recorded as `budget_exceeded` (never started) and the claim carries a
  `PARTIAL:` detail; the claim cannot PASS. `src/assay/mutation.py:1749`,
  `src/assay/mutation.py:5781`
  Cleaner future design: "never started" deserves its own bucket instead of
  sharing `budget_exceeded` with genuine timeouts.
- **VER-100** `enforcement` (`gate`|`advisory`) and `scope` are recorded
  verbatim; assay's exit code does not depend on them. A consumer applies
  `enforcement`. `src/assay/config.py:197`, `src/assay/cli.py:3653`
  Deprecation candidate: an assay field with no assay behaviour; move it to
  the gate's own configuration.
- **VER-110** Passthrough environment values never appear in a verdict: the
  name is kept with the marker `<passthrough>` plus a SHA-256 fingerprint.
  `src/assay/verdict.py:837`
- **VER-120** The verdict is written only when `--verdict-json` is given,
  exactly once: atomically to PATH, or to stdout for `-`; a run that cannot write it is
  `ERROR/OUTPUT_WRITE_FAILED`. `src/assay/cli.py:678`, `src/assay/cli.py:3636`,
  `src/assay/errors.py:115`
- **VER-130** `assay verify` re-derives what JSON Schema cannot: outcome
  agrees with rollup, `argv_effective == argv_declared + argv_appended`,
  claims cover declared rigor, and R1/R2/R3 status from payload plus recorded
  policy. It never re-runs a lane. `src/assay/verify.py:1`,
  `src/assay/verify.py:3636`, `src/assay/verify.py:4152`

## 4. Judge identity (JID)

- **JID-010** A candidate id is the SHA-256 identity of `(path,
  source_sha256, start_byte, end_byte, mutated_file_sha256, operator)`; it
  names the state record file and is independent of the judging suite.
  `src/assay/mutation_store.py:495`, `src/assay/mutation_store.py:509`,
  `src/assay/mutation_store.py:542`
- **JID-020** The judge identity (`judge_sha256`, label
  `assay-judge-identity/15`) folds: the projected tree digest; assay's own
  version; the effective argv; declared env names and values; names of every
  other effective env entry, plus the value fingerprint of present
  passthrough names; declared cwd and project prefix; `link_paths`; the
  cold-witness flag; the R2 transform and collection digests; the R2 hook and
  runtime fingerprints; the coverage hook and coverage runtime fingerprints;
  the equivalence ledger digest; and candidate fail-fast when
  on. `src/assay/mutation.py:322`, `src/assay/mutation.py:1500`
- **JID-030** The tree digest projects `assay.toml` and the sibling
  `run-gate.toml` before hashing: resource and scheduling keys are removed
  (lane `budget`; mutation `jobs`, `jobs_max`, `max_mutants`, `shard_index`,
  `shard_count`, schedule factors and predictions; isolation `verify_clone`,
  `materialize`, `tmpfs_*`; canary `budget_per_attempt`; project
  `[isolation] limits`; run-gate `profile`/`footprint`/`resources` tables and
  lane resource keys). Comment and formatting edits of those two files do not
  move the identity. `src/assay/_judge_projection.py:65`,
  `src/assay/_judge_projection.py:93`
- **JID-040** Deliberately outside the identity: the removed resource keys
  (they form `resource_sha256`, JID-050); the content of linked directories;
  infrastructure fact values (names only). `src/assay/mutation.py:1571`,
  `src/assay/_judge_projection.py:44`
- **JID-050** `resource_sha256` digests the removed resource values plus the
  run's worker count. A resumed record whose bucket is `crashed`, `hung` or
  `budget_exceeded` re-executes when it differs; every other bucket
  (`killed`, `survived`, `equivalent`) is reused. `src/assay/mutation_store.py:598`, `src/assay/mutation_store.py:4267`
- **JID-060** `judge.mutation.budget_per_candidate` IS inside the identity
  (it is not a projected key), so changing it re-executes every candidate.
  `src/assay/_judge_projection.py:20`
  Cleaner future design: a wall bound in the identity contradicts JID-050's
  resource split; once LIV-110 ships it belongs with the resource keys.

## 5. Resume, store, carry-over and shards (STO)

This chapter applies to native R2 lanes. `--carry-over`, `--carry-over-reason`,
`--carry-forward`, `--merge-state`, `--merge-shard-store`,
`--only-candidates`, `--exclude-candidates` and `--merge-manifest` are refused
with `ERROR/BAD_LANE_CONFIG` on any other lane. `src/assay/cli.py:2394`,
`src/assay/cli.py:2473`. (Unverified: what `--resume`, `--shard`,
`--state-dir` and `--rejudge*` do on a non-R2 lane.)

- **STO-010** The store is `<project>/.assay/mutation-state/` unless
  `--state-dir` is given; a state dir inside the judged tree that git can see
  is refused before any work. Each record is `<candidate-id>.json`, at most
  1 MiB, record schema 1. `src/assay/mutation_store.py:570`,
  `src/assay/mutation_store.py:100`, `src/assay/mutation_store.py:114`,
  `src/assay/cli.py:723`
- **STO-020** The flat name holds the record of the judge that wrote it last;
  before a different judge replaces it, the old file is hard-linked to
  `judges/<judge>/<id>.json`, so no generation is overwritten.
  `src/assay/mutation_store.py:580`
- **STO-030** With `--resume`, a record is reused iff it is well formed, its
  effective judge equals this run's judge, and (when bound) its campaign
  deadline digest matches; a record of this judge under `judges/` is
  promoted back to the flat name. A foreign-judge or judge-less record is
  rejected and the candidate re-executes (counted separately from a cold
  miss); a malformed record is `ERROR/UNREADABLE_ARTIFACT`.
  `src/assay/mutation_store.py:1974`, `src/assay/mutation_store.py:2392`,
  `src/assay/mutation_store.py:4163`, `src/assay/mutation_store.py:162`
- **STO-040** `--rejudge ID[,ID]` and `--rejudge-outcome BUCKET[,BUCKET]`
  (`error` aliases `crashed`) drop matching records before resume; both
  require `--resume`; an unknown id or bucket is `ERROR/BAD_LANE_CONFIG`
  before any work. `src/assay/runner.py:8440`, `src/assay/runner.py:8458`,
  `src/assay/mutation_store.py:199`
- **STO-050** Combine policies, in fixed per-candidate precedence:
  `same-judge` (`--resume`, `--merge-state`), `strict-shard`
  (`--merge-shard-store`), `owner-manifest` (`--merge-manifest`),
  `import-unverified` (`--carry-over`), `carry-forward` (`--carry-forward`);
  `--reuse-from` is a `hint`. `src/assay/mutation_store.py:82`,
  `src/assay/mutation_store.py:89`
- **STO-060** `--carry-over DIR` (repeatable, requires `--resume` and
  `--carry-over-reason` of at most 1024 bytes) imports `killed`/`survived`
  records of any judge, keeping the executing judge and adding a
  `carry_over` provenance block; other buckets re-execute. It is an operator
  assertion, not evidence. `src/assay/mutation_store.py:593`,
  `src/assay/mutation_store.py:3773`, `src/assay/mutation_store.py:639`
  Cleaner future design: carried records are invisible in the verdict
  (provenance lives only in the store and progress stream); the verdict
  should count them.
- **STO-070** `--carry-forward DIR` matches earlier-judge records by exact
  id, then function locus, then line window; survivors are carried
  pessimistically, kills re-run in full. It requires `--resume` and excludes
  `--carry-over` and `--carry-over-reason`. `src/assay/cli.py:2420`, `src/assay/mutation_store.py:3317`,
  `src/assay/cli.py:2401`
- **STO-080** `--shard K/N` (`1 <= N <= 10000`, `0 <= K < N`) selects the
  candidates whose `blake2b-32(id) mod N == K`, deterministic and
  independent of plan order; the shard's store gets a `shard.manifest`
  (`assay-shard-store/1`: index, count, judge, plan digest, total).
  `src/assay/mutation_store.py:2609`, `src/assay/mutation_store.py:583`,
  `src/assay/verdict.py:1042`
- **STO-090** Strict merge: `--merge-shard-store` (with `--resume`, without
  `--shard`/`--only-candidates`/`--exclude-candidates`) takes exactly one
  store per shard `0..N-1`. Each MUST carry a manifest of this judge and of
  the current plan of its shard, a resumable record for every candidate of
  its shard and no other record. Any absent, duplicate, foreign-judge,
  changed-plan, incomplete or polluted store refuses the run with
  `ERROR/BAD_LANE_CONFIG` before any candidate runs; nothing is executed in
  place of a lost shard. `src/assay/mutation_store.py:3618`,
  `src/assay/mutation_store.py:215`, `src/assay/cli.py:2428`
- **STO-100** `--merge-state DIR` is the permissive merge: it copies records
  of this run's judge only and ignores others. `src/assay/cli.py:517`
- **STO-110** `--merge-manifest FILE` (`assay-merge-manifest/1`) requires
  every current candidate to be owned exactly once; it excludes `--shard`,
  candidate selections, `--candidates-file` and `--merge-shard-store`.
  `src/assay/mutation_store.py:70`, `src/assay/cli.py:2476`
- **STO-120** `plan_sha256` hashes candidate ids in full discovery order,
  before shard, selection or resume filtering. `src/assay/mutation_store.py:514`

## 6. Liveness, hangs and timeouts (LIV)

### 6.1 Implemented

- **LIV-010** Every lane declares `budget`: a duration (`90s`, `5m`, `1h30m`;
  a bare number is refused) or the literal `unbounded`. Expiry of the lane
  budget is `BUDGET_EXCEEDED/LANE_TIMEOUT`. `src/assay/config.py:380`,
  `src/assay/config.py:401`, `src/assay/runner.py:274`,
  `src/assay/runner.py:307`
- **LIV-020** `unbounded` is admitted only for a native R2 lane whose
  candidates are bounded; `budget_per_candidate = "none"` with `unbounded`
  is refused at load; R0/R1 (with or without R3) and ingested R2 lanes cannot
  be unbounded; an unbounded lane declaring R3 MUST declare
  `judge.canary.budget_per_attempt`. The native R2 baseline then runs with no
  timeout; its watcher is the caller. `src/assay/config.py:2621`,
  `src/assay/config.py:2696`, `src/assay/config.py:2746`
- **LIV-030** `judge.mutation.budget_per_candidate`: a duration, `auto`
  (also the meaning of an omitted key) = `max(3 x baseline_wall,
  baseline_wall + 60 s)` computed after the baseline ran, or `none` (no
  per-candidate bound, WARN at run time). Expiry → bucket `budget_exceeded`.
  `src/assay/config.py:412`, `src/assay/mutation.py:2136`,
  `src/assay/mutation.py:2636`, `src/assay/runner.py:6011`
- **LIV-040** Liveness applies only to native R2 Python candidates, never to
  the baseline or R1. `judge.mutation.liveness` is `auto` (on iff the
  declared argv invokes pytest), `true` (refused at load for non-pytest argv)
  or `false`. When on, a pytest plugin is appended to `argv_appended` and
  candidates run under the liveness monitor. `src/assay/liveness.py:1`,
  `src/assay/liveness.py:243`, `src/assay/liveness.py:518`
- **LIV-045** `judge.mutation.liveness_cpu_window` and
  `judge.mutation.liveness_idle_floor` are durations in [5 s, 1 h]; declaring
  either is refused at load when `liveness = false` or when liveness can never
  activate (not Python, or argv does not invoke pytest).
  `src/assay/config.py:484`, `src/assay/config.py:3787`,
  `src/assay/config.py:3800`, `src/assay/config.py:3810`
- **LIV-050** A candidate is `hung` (`CANDIDATE_HUNG`) iff, over a window with
  complete, pressure-free resource evidence: no new plugin event and no
  stdout/stderr growth for the calibrated bound AND tree CPU grew less than
  `W/30` in the trailing `W` (`W` = `liveness_cpu_window`, default 30 s); or
  `session_finish` was seen and the process lives 30 s later with the same
  quiet conditions. All three intervals run on a pressure-adjusted clock
  (TIM-140). `src/assay/liveness.py:672`, `src/assay/liveness.py:1547`,
  `src/assay/liveness.py:1649`, `src/assay/liveness.py:2073`,
  `src/assay/liveness.py:1979`
- **LIV-060** Calibrated idle bounds: `max(3 x worst baseline event gap, F)`
  after the first event and `max(3 x leading gap, F)` before it (`F` =
  `liveness_idle_floor`, default 15 s); without baseline events,
  `max(60 s, baseline_s / 4)` for both (`F` does not apply).
  `src/assay/liveness.py:1250`, `src/assay/liveness.py:1297`,
  `src/assay/liveness.py:1303`,
  `src/assay/liveness.py:1311`, `src/assay/liveness.py:816`
- **LIV-070** A candidate that keeps completing plugin events can exceed
  `budget_per_candidate` while the liveness watch is armed; that budget is
  advisory. The CPU-without-progress and per-test CPU bounds can still end it
  as `hung`. If the watch is not armed, the per-candidate budget binds as
  `budget_exceeded`; `none` leaves the lane deadline as the bound.
  `src/assay/liveness.py:670`, `src/assay/liveness.py:2167`,
  `src/assay/runner.py:1842`
- **LIV-075** The resource trace is compacted at its sample cap without
  disabling hang decisions. An `unknown` resource interval resets the trace
  and the idle clock; a later complete interval starts a new trace.
  `src/assay/liveness.py:1808`, `src/assay/liveness.py:1852`,
  `src/assay/liveness.py:1998`
- **LIV-080** A stored `hung` record is reusable with either validated
  pressure-adjusted idle evidence (`idle-hang` or `session-finish-hang`) or
  validated CPU-bound evidence (`cpu-without-progress` or
  `per-test-cpu-bound`). `src/assay/mutation_store.py:1630`,
  `src/assay/mutation_store.py:1841`
- **LIV-090** SIGTERM/SIGINT/SIGHUP during `run` kill the live process
  groups and write an incomplete verdict (`BUDGET_EXCEEDED/LANE_TIMEOUT`).
  `src/assay/cli.py:801`, `src/assay/runner.py:274`
  Cleaner future design: a cancellation is not a timeout; it needs its own
  reason.
- **LIV-100** `--progress PATH` appends an NDJSON stream flushed per event
  for every rigor tier; `--progress-heartbeat` ticks `command_running`
  (default 60 s, floor 5 s). assay emits progress; stall detection of a
  whole lane belongs to the caller. `src/assay/cli.py:688`,
  `src/assay/cli.py:707`, `src/assay/runner.py:1178`,
  `src/assay/runner.py:1179`

### 6.2 Implemented liveness bounds

- **LIV-110** The CPU-without-progress bound is
  `max(3 x baseline_wall, baseline_wall + 60 s)` process-tree CPU seconds
  since spawn or the last completed plugin event. Completed events are `test`
  (call) and `phase` (setup or teardown) reports. `test_start`, `test_finish`,
  `session_start`, `session_finish`, and output growth do not count as
  completed events. The bound ignores pressure, and its CPU series does not
  decrease when a child exits. A hit is `hung` with decision
  `cpu-without-progress`; its evidence records measured and required CPU,
  completed-event count, and the resource snapshot. `src/assay/liveness.py:883`,
  `src/assay/liveness.py:896`, `src/assay/liveness.py:946`,
  `src/assay/liveness.py:2478`, `src/assay/liveness.py:2702`,
  `src/assay/liveness.py:2755`, `src/assay/liveness.py:2774`
- **LIV-120** The per-test CPU bound applies only to one in-flight pytest test,
  outside xdist and without overlapping test starts. Its window opens at
  `test_start`, covers setup, call and teardown, and closes only at the matching
  `test_finish`; each start, including a rerun of the same node id, opens a
  fresh window. The bound is `max(10 x baseline setup+call+teardown seconds,
  60 CPU seconds)`. A node id absent from the baseline has no per-test bound;
  `session_finish` closes any open window. `src/assay/liveness.py:911`,
  `src/assay/liveness.py:924`, `src/assay/liveness.py:1650`,
  `src/assay/liveness.py:1714`, `src/assay/liveness.py:1724`,
  `src/assay/liveness.py:1725`, `src/assay/liveness.py:2667`,
  `src/assay/liveness.py:2783`
- **LIV-130** When liveness is injected and the baseline produced plugin
  events, the progress watch is armed. While the candidate CPU has been read
  and its event stream remains intact, `budget_per_candidate` is advisory and
  emits one warning when first exceeded. If the watch cannot arm, or the event
  stream breaks at runtime, that budget binds as `budget_exceeded`; a broken
  stream also disarms the event-derived CPU bounds and no-progress wall stop.
  The `plan` progress record states the watch regime. `src/assay/runner.py:6067`,
  `src/assay/runner.py:6082`, `src/assay/mutation.py:2528`,
  `src/assay/mutation.py:2965`, `src/assay/liveness.py:1621`,
  `src/assay/liveness.py:2842`, `src/assay/liveness.py:2901`
- **LIV-140** The resource trace is capped by compacting eligible adjacent
  samples; it does not truncate evidence or disable hang decisions. The first
  two samples remain unmerged, and an unknown resource interval starts a new
  trace. `src/assay/liveness.py:1808`, `src/assay/liveness.py:1852`,
  `src/assay/liveness.py:2627`
- **LIV-150** Every liveness stop currently classified as a hang is `hung`,
  never `killed`. CPU-bound and per-test stops carry their CPU evidence;
  idle and session-finish stops require their pressure-adjusted trace.
  `src/assay/liveness.py:668`, `src/assay/liveness.py:2079`,
  `src/assay/mutation_store.py:1630`
- **LIV-155** PLANNED (operator decision 2026-11-26; `docs/VERDICT-V21-DELTA.md`
  §8.2, §8.8). In the next verdict-schema cut, only a load-independent
  CPU-bound timeout counts as detected and carries a per-candidate
  `timeout_kind`. Wall-budget stops are not detections. Idle-hang and
  session-finish-hang remain non-detections while their windows are wall-based.
- **LIV-160** PLANNED (design §3.3, later release, not scheduled): a lane-level
  `STALLED` outcome with its own exit code, and `CANDIDATE_HUNG` mapped to a
  lane-level `INCONCLUSIVE`. Changes a closed vocabulary.

## 7. Time semantics (TIM)

### 7.1 Target (operator decision 2026-11-26)

- **TIM-010** PLANNED. A verdict MUST NOT depend on wall time.
- **TIM-015** Native R2 already consumes cgprofile: each candidate carries a
  `resource-profile/1`, the verdict's `judgment.r2.profile` names observer
  `cgprofile`, and hidden-ancestor counters come from cgprofile's observer
  socket. Nothing in `src/assay` reads hostobs or thrashguard.
  `src/assay/cli.py:5103`, `src/assay/verdict.py:564`,
  `src/assay/resource_limits.py:1070`, `src/assay/liveness.py:1869`
- **TIM-020** PLANNED. assay additionally obtains thrashguard freeze events
  via hostobs and recalculates effective CPU time from cgprofile data and
  freeze intervals.
- **TIM-030** PLANNED (operator decision 2026-11-26 02:2xZ). The assay
  summary records effective CPU seconds (TIM-020) as raw, un-normalized CPU
  seconds, together with the processor model. When the host has a Geekbench
  record of major 7 (the unit's major), the summary also records reference
  CPU seconds, `ref_cpu_s = effective_cpu_s * single_core / baseline_score`,
  with the record's `tool_major`, `single_core` and `baseline_score`. Both
  inputs come from the host record; assay holds no score constant. Factor
  1.0 is that major's own reference machine (Geekbench 7: 2500 single-core,
  AMD Ryzen 7 7700); the former 1500-point unit is retired. Values are never
  compared across majors: a record of another major counts as no record.
  Without a record only raw CPU seconds and the processor model are
  recorded, so a score can be applied later. cgprofile's score table
  (`cgprofile/docs/SCORES-TABLE.md`, backlog CP-59) is not an input here;
  normalized values it states are to follow this unit. Reference: Primate Labs,
  `geekbench7-cpu-workloads.pdf`.

Also decided (operator 2026-11-26 01:1xZ, Q3): bounds (LIV-030,
LIV-060) use normalized time when it is available, otherwise baseline-relative
CPU time on the same host. Open: how effective CPU time is recalculated
(TIM-020), including how freeze intervals are subtracted; how a bound is
derived from normalized time, and its margins (`ref_cpu_s` is an estimate for
a test suite, since the score averages Geekbench's own workload mix); which
summary fields carry raw and normalized time; schema impact.

### 7.2 Places where wall time can affect a verdict today

- **TIM-110** Lane budget: monotonic wall deadline → `LANE_TIMEOUT`.
  `src/assay/runner.py:307`, `src/assay/runner.py:8034`
- **TIM-120** Campaign deadline: a persisted UTC expiry bounds the lane
  deadline; candidates left at expiry are recorded never started
  (`budget_exceeded`). `src/assay/runner.py:282`, `src/assay/mutation.py:4891`
- **TIM-130** Auto per-candidate budget derives from the baseline's wall
  duration (the difference of its recorded realtime `started`/`ended`
  timestamps) and is enforced as monotonic wall elapsed time.
  `src/assay/mutation.py:2121`, `src/assay/mutation.py:2636`,
  `src/assay/liveness.py:2097`
- **TIM-140** Liveness idle bounds, the CPU window and the session-finish
  grace are wall intervals on a pressure-adjusted clock that stops during
  non-clear resource intervals (which also reset the CPU window); the
  per-candidate budget is raw monotonic wall time.
  `src/assay/liveness.py:1979`, `src/assay/liveness.py:1985`,
  `src/assay/liveness.py:2051`, `src/assay/liveness.py:2076`,
  `src/assay/liveness.py:2097`
- **TIM-150** Canary `budget_per_attempt` is a wall bound per attempt; an
  exhausted lane deadline marks later targets `budget_exhausted`.
  `src/assay/canary.py:737`, `src/assay/verdict.py:887`
- **TIM-160** Git and snapshot calls run against the remaining lane deadline;
  the commit-label read after expiry has a 2 s wall grace.
  `src/assay/runner.py:4267`, `src/assay/cli.py:180`
- **TIM-170** The serial tail retry (RIG-365) opens only without a
  campaign-deadline mask and stops when the remaining wall deadline drops
  below `budget_per_candidate`. `src/assay/mutation.py:4807`,
  `src/assay/mutation.py:4817`
- **TIM-180** The cgroup observer request times out after 5 s and its
  preflight join after 6 s; a slow observer makes observation unavailable
  (RIG-370 before the baseline, `unobserved`/`crashed` per candidate).
  `src/assay/resource_limits.py:61`, `src/assay/resource_limits.py:1126`,
  `src/assay/resource_limits.py:1423`
- **TIM-190** A candidate process tree that does not empty within its
  cleanup deadline is `ERROR/EXEC_FAILED`. `src/assay/process_tree.py:632`,
  `src/assay/runner.py:1895`
- **TIM-200** The liveness trace cap (LIV-075) is a sample count at 1 s polls,
  so about 1024 s of eligible time without progress disables `hung`.
  `src/assay/liveness.py:676`, `src/assay/liveness.py:2020`

## 8. CLI and exit codes (CLI)

- **CLI-010** Commands: `run`, `plan`, `lanes`, `capabilities`, `verify`,
  `campaign init`, `analyze` (forwarded verbatim to the `assay_analysis`
  package; its import failure is `ERROR/EXEC_FAILED`).
  `src/assay/cli.py:304`, `src/assay/cli.py:887`, `src/assay/cli.py:186`
- **CLI-020** `run LANE [-- ARGV...]`: exit code = the verdict's outcome code
  (VER-010), whether or not `--verdict-json` is given; a refusal before a
  verdict exits with its own outcome code. Text after the first literal `--`
  is appended argv, accepted only when the lane sets
  `allow_argv_append`. `src/assay/cli.py:3653`, `src/assay/cli.py:923`,
  `src/assay/cli.py:927`
- **CLI-030** Usage errors (argparse) exit 2 after printing the headline and
  usage; a SIGCHLD-handling failure at startup exits 2.
  `src/assay/cli.py:135`, `src/assay/cli.py:874`
  Cleaner future design: exit 2 also means `ERROR`; a usage error writes no
  verdict, so a caller cannot tell them apart by exit code alone.
- **CLI-040** Pilot mode (`run --candidates-file`, requires `--state-dir`,
  writes no verdict) exits 6 when the selection completed, else the
  verdict's code, with a non-completed PASS reported as 2.
  `src/assay/cli.py:370`, `src/assay/cli.py:1704`
  Cleaner future design: 6 is outside the verdict range (schema
  `exit_code` maximum 5); a separate pilot command would avoid sharing
  the code space.
- **CLI-050** `verify PATH|-` exits 0 for a valid v20 verdict (stderr notes
  overridden dirty paths) and 1 for an unreadable or invalid one; every
  schema version other than 20 is invalid. Exit 0 means a valid verdict of
  ANY outcome; a consumer MUST read `outcome`/`exit_code` from the document.
  `src/assay/verify.py:4199`,
  `src/assay/verify.py:4082`
  Deprecation candidate: exit 1 reads as `FAIL` to a verdict-aware caller.
- **CLI-060** `plan LANE` discovers candidates and estimates runtime without
  running the lane command; `--list-pending` (with `--state-dir`, optional
  `--judge`) prints pending ids for `run --only-candidates`; refusals exit
  with their outcome code. `src/assay/cli.py:589`, `src/assay/cli.py:621`,
  `src/assay/cli.py:4625`
- **CLI-070** `lanes [--json]` loads and validates `assay.toml` (searched
  upward from the cwd unless `--file`), runs nothing, exits 0, or 2 with no
  JSON on stdout when the file fails to load. `src/assay/cli.py:307`,
  `src/assay/cli.py:5135`, `src/assay/config.py:1524`
- **CLI-080** `capabilities [--json]` prints CON-030's document and reads no
  project file; exits 0, or 2 (`ERROR/EXEC_FAILED`) when the `assay_analysis`
  package cannot be imported. `src/assay/cli.py:340`, `src/assay/cli.py:221`,
  `src/assay/cli.py:186`
- **CLI-090** `campaign init --campaign NAME --lane L... (--hours H |
  --expires-at ISO8601Z)` creates a deadline bound to the current commit and
  full lane plans; `run --campaign-deadline PATH` binds a run and its
  records to it. `src/assay/cli.py:766`, `src/assay/cli.py:742`,
  `src/assay/cli.py:2150`
- **CLI-100** `--require-judge-provenance` refuses before any work unless
  the installed build artifact (wheel or zipapp) is identified; without it an
  unidentifiable install runs and emits no `judge_provenance`.
  `src/assay/cli.py:752`, `src/assay/verdict.py:742`
- **CLI-110** `--request-base REF` is required by a lane with
  `judge.base_source = "request"` and refused on every other lane.
  `src/assay/cli.py:263`, `src/assay/config.py:358`
- **CLI-120** Native R2 execution flags: `--jobs N` (worker count, default 1,
  at most `judge.mutation.jobs_max`; the worker count enters
  `resource_sha256`, JID-050); `--memory-budget-bytes` or
  `ASSAY_R2_MEMORY_BUDGET_BYTES` (admission: a candidate starts only while the
  predicted peak RSS of running candidates plus its own fits, one always
  runs; a malformed value is `BAD_LANE_CONFIG`); `--operators` (operator
  subset); `--cold-witness` and `--r2-manifest PATH` (Python cold kill
  witnesses; the manifest requires `--cold-witness`); `--pilot-jobs` (pilot
  only, must be 1). `campaign init` also takes `--state-dir`,
  `--wheel-sha256` and `--out`. `src/assay/cli.py:386`,
  `src/assay/cli.py:393`, `src/assay/cli.py:5117`, `src/assay/cli.py:443`,
  `src/assay/cli.py:416`, `src/assay/cli.py:424`, `src/assay/cli.py:380`,
  `src/assay/cli.py:788`, `src/assay/_judge_projection.py:28`

## 9. Consumer contract (CON)

- **CON-010** `assay.toml` (lane schema 2) is a hard cut: every lane declares
  `scope`, `rigor`, `enforcement`, `argv`, `env`, `env_passthrough`,
  `budget`, `allow_argv_append`; unknown keys and missing required keys are
  `ERROR/BAD_LANE_CONFIG` naming file, lane and field; no default is
  invented. R1+ lanes MUST declare `isolation.snapshot_selection`; R0-only
  lanes MUST NOT. `src/assay/config.py:174`, `src/assay/config.py:200`,
  `src/assay/config.py:241`, `src/assay/errors.py:318`
- **CON-020** A consumer MAY rely on: the exit code (VER-010); the closed
  outcome, reason-code, rigor and bucket vocabularies (VER-020, RIG-010,
  VER-080); one claim per declared level (VER-040); absent-vs-empty
  (VER-060). It MUST NOT rely on: unspecified field order, prose `detail`
  text, stderr text, or progress-stream fields beyond their documented
  names. `src/assay/errors.py:58`, `src/assay/verdict.py:1058`
- **CON-030** `assay capabilities` prints one closed object, schema
  `assay-capabilities/2`, keys `schema`, `assay_version`,
  `verdict_schema_version`, `commands` (sorted long options of `run`, `plan`,
  `verify`, `lanes`, `analyze campaign`, from the live parsers, `--help`
  included),
  `mutation_store_policies` (store flag → policy), `combine_policies` (the
  STO-050 order). A new, dropped or re-meant key requires a new `schema`
  value; consumers MUST refuse an unknown `schema`. `src/assay/cli.py:182`,
  `src/assay/cli.py:221`
- **CON-040** `assay lanes --json` prints `inventory_schema` 1 with
  `assay_version`, a `capabilities` flag list (`r2-memory-budget`,
  `r2-schedule-tem`, `r2-resource-profile`, `verdict-v20`) and one entry per
  lane (rigor vs `rigor_reachable`, coverage/mutation/canary shape,
  `base_source`, `external_tools`, `cwd`, `link_paths`,
  `snapshot_selection`, `resource_observation`). Adding a key does not bump
  `inventory_schema`; a changed meaning does. `src/assay/cli.py:5095`,
  `src/assay/cli.py:5109`, `src/assay/cli.py:5135`
  Deprecation candidate: two capability lists with different schemas and
  rules; fold the flag list into CON-030 at its next schema bump.
- **CON-050** Verdict schema versioning: exactly one schema is active per
  build; producers emit v20; `assay verify` refuses every other version;
  the schema is hand-written and shipped as package data.
  `src/assay/verdict.py:443`, `src/assay/verdict.py:773`,
  `src/assay/schemas/verdict.schema.json:21`
- **CON-060** `--reuse-from VERDICT` reuses pytest kill witnesses only from a
  verified complete native v20 verdict; v12-v15 start cold, v16-v19 are
  refused; it excludes `--shard`. `src/assay/cli.py:446`
- **CON-070** Progress and verdict files MUST be outside the judged tree (or
  ignored); a file inside the tree that git sees and that no
  `isolation.dirty_ignore` pattern matches makes the next run
  `NO_MEASUREMENT/DIRTY_TREE` (RIG-130). `src/assay/cli.py:688`,
  `src/assay/cli.py:723`
- **CON-075** A release consumer MUST refuse, by default, a verdict whose
  `worktree_integrity.overridden_dirty_paths` is non-empty, whatever its exit
  code; `assay verify` accepts it (exit 0) and says so on stderr.
  `src/assay/verify.py:4216`
