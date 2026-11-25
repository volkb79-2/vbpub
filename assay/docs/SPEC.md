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
  exit code MUST NOT be misled. `src/assay/errors.py:6`
- **PUR-020** assay measures; it does not choose. It runs exactly the command
  a lane declares, never discovers or retries tests on its own (outside the
  named R2 mechanics), and never turns an absent measurement into a pass.
  `src/assay/verdict.py:21`, `src/assay/verdict.py:972`
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

- **RIG-110** R0 runs the lane's effective argv once. It PASSes iff the exit
  status is 0, or, when the lane declares `result_report`, iff the verified
  report counts zero failures (a non-zero exit then still PASSes). Otherwise
  `FAIL/COMMAND_FAILED`. `src/assay/runner.py:1977`, `src/assay/runner.py:2023`
  Cleaner future design: a report-decided PASS over a non-zero exit is
  visible only as present output tails; record the override as an explicit
  field.
- **RIG-120** An R0-only lane runs in the live checkout; any lane declaring a
  higher level runs in a committed-object snapshot.
  `src/assay/runner.py:8489`
- **RIG-130** After the command, a dirty snapshot is `NO_MEASUREMENT/DIRTY_TREE`;
  a clean tree whose HEAD moved is `NO_MEASUREMENT/HEAD_CHANGED`; dirt takes
  precedence. `src/assay/runner.py:4267`

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
  `src/assay/runner.py:2918`, `src/assay/evaluate.py:1297`,
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
  `equivalent` and a missing one `crashed`. `src/assay/termination.py:49`,
  `src/assay/termination.py:64`, `src/assay/mutation_store.py:4580`
- **RIG-370** A native R2 run whose lane process cannot observe its cgroup v2
  ancestry is refused before the baseline: `NO_MEASUREMENT/
  CGROUP_OBSERVATION_UNAVAILABLE`. `src/assay/errors.py:162`

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
  `INCONCLUSIVE` 5. Every non-zero outcome blocks.
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
  atomically, exactly once; a run that cannot write it is
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
  cold-witness flag; the R2 transform, collection, hook and runtime
  fingerprints; the equivalence ledger digest; and candidate fail-fast when
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
  `budget_exceeded` re-executes when it differs; `killed`/`survived` are
  reused. `src/assay/mutation_store.py:598`, `src/assay/mutation_store.py:4267`
- **JID-060** `judge.mutation.budget_per_candidate` IS inside the identity
  (it is not a projected key), so changing it re-executes every candidate.
  `src/assay/_judge_projection.py:20`
  Cleaner future design: a wall bound in the identity contradicts JID-050's
  resource split; once LIV-110 ships it belongs with the resource keys.
