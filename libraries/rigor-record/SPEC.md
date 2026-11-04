# rigor-record SPEC (RR-1, RR-2, RR-3, RR-1a/RR-2a)

> Records are keyed by commit (a release tag is a label on a commit); there is no `policy.toml`, no `rigor/releases/` and no `waive-r2`.

Normative for `urn:vbpub:rigor-fragment:1`, `urn:vbpub:rigor-record:1`, `urn:vbpub:rigor-waivers:1`, `urn:vbpub:rigor-index:1`, the distribution
object, the nearest-rank rule, the normalisation formula and the privacy rules. Design authority:
`RIGOR-EVIDENCE-DESIGN-MEMO-2026-11-05` (sections 2, 3, 4, 6, 7.1) with the operator amendments of that date. The
words MUST, MUST NOT and MAY are used in the RFC 2119 sense. Machine-checkable statements are enforced by
`src/rigor_record/` and the schemas in `src/rigor_record/schemas/`; the golden fixtures in `golden/` are normative
examples.

## 1. Versioning

1. Every schema carries its URN with an integer major. An additive optional key does not bump the major. A required
   key, a removed key, a new enum member or a change of meaning does: a hard cut.
2. A reader accepts major N and N-1 for one release line; a writer writes N only. (RR-1 defines major 1 only.)
3. Producers (assay, run-gate) MUST NOT import this wheel at run time. They vendor `schemas/` and `golden/`
   byte-identically into their tests and validate their own output against them.

## 2. Common conventions

- JSON, UTF-8. Committed documents use `canonical_json`: sorted keys, 2-space indent, ASCII escapes, one final newline.
- Every status key is always present. An unknown fact is a named status or an explicit `null`, never an absent key.
- Digests are lowercase hex SHA-256 (64) or git object ids (40). `image_id` is `sha256:` plus 64 hex.
- Timestamps are `YYYY-MM-DDTHH:MM:SSZ`; dates `YYYY-MM-DD`. Paths are project-relative POSIX (no leading `/`, no `..`
  segment, at most 256 characters).
- Every string reachable from a document is restricted by a schema pattern (privacy layer 1, section 8). The shared
  patterns live in `rigor-common-1.json`.

## 3. `rigor-fragment/1`

One producer's facts about one lane run, at most 256 KiB, kept outside the repository. `kind` selects the producer.
Both kinds carry `project`, `lane`, `judged_commit` and the `project_tree` of that commit
(`git rev-parse <commit>:<project-dir>`); a fragment is accepted for tag T only when its tree equals the tag's tree
(checked by RR-2).

### 3.1 `kind: "assay-lane"` (owner: assay)

`outcome` (`PASS | FAIL | ERROR`), `reason` (a `REASON_CODE`, null exactly when the outcome is `PASS`),
`verdict_sha256`, `config_sha256`, `verified` (const `true`: `assay verify` ran first), `judge`
(`version`, `artifact_sha256`, `judge_identity` = `assay-judge-identity/N`, `verdict_schema` = `urn:assay:schema:verdict:N`),
and three optional facts, each an object or `null`:

- `coverage`: `line_pct`, `branch_pct`, `floor{line,branch}`, `excluded_lines`.
- `canary`: `mechanism`, `targets[1..50]`, `outcome`.
- `r2`: `candidates{total,executed,resumed}`, `buckets{killed,survived,equivalent,hung,budget_exceeded,crashed}`,
  `score` (0..1 or null), `worker_counts`, `profile_status_counts{MEASURED,PARTIAL,NO_MEASUREMENT}`,
  `campaign{started_at,completed_at}|null`, `survivors{total,listed[],sidecar_sha256}`.

Semantic rules (validator): `executed <= total`; `resumed <= executed`; the bucket counts sum to `executed`; the sums of
`worker_counts` and of `profile_status_counts` do not exceed `executed`; `survivors.total == buckets.survived`;
`listed` holds `min(total, 500)` entries with unique `candidate_id`; `claims` (A-1a) is the verified claim set
(`{rigor, outcome}`), the only source of R1, R2 and R3, `inputs` the lane's declared out-of-project inputs and
`judged_paths` its judged source roots; `sidecar_sha256` is set exactly when
`total > 500`; the campaign does not end before it starts. An assay fragment carries NO resource numbers.

### 3.2 `kind: "run-gate-lane"` (owner: run-gate)

`run_gate{version,wheel_sha256}`, `environment`, `image{ref,image_id,labels}|null`, `config_sha256`, `io_sync`,
`exit`, `started_at`, `completed_at`, `duration_seconds`, `assay_fragment_sha256|null`, `machine_classes[]`,
`perf[]` (section 7). `completed_at` MUST NOT precede `started_at`.

## 4. `rigor-record/1`

One record per project per COMMIT, at most 64 KiB (canonical form). A result describes the commit it was judged on; a
release tag is only a label on that commit. Top level: `schema`, `project`, `commit`, `revision`, `labels[<=4]`,
`inventory{sequence,members,r2_lanes}`, `toolsets{tN}`, `results[]`, `review`, `qualifications{name}`, `perf[]`,
`machine_classes[]`. Every toolset id used by a result, a history entry or a qualification MUST exist in `toolsets`.

- **Label** (`kind: "release"`): `tag` (ends in `-v<version>`), `version`, `published_at`, `artifacts[{name,sha256}]`,
  `state` (`current|withdrawn`) and `withdrawn{reason,replacement}`, set exactly when the state is `withdrawn`. A commit
  without a label is judged but unreleased (for example a per-merge R2 campaign).
- **Inventory**: the release sequence lane (`sequence`) and its member lanes (`members`) as of this commit's release, and
  `r2_lanes`, the R2 lanes of the project (never members of the sequence). An unlabelled commit has `sequence: null`.
- **Results** are of two kinds. A *lane result* is `{lane, kind, judged_by, levels, toolset, targets,
  verdict_sha256, config_sha256}` plus the level data below; a *sequence result* is `{lane, kind: "sequence", members,
  outcome, toolset}` and records what the release sequence ran.
- **Targets** (the identity of what a result judged): a map from project-relative path to git object id at the judged
  commit: the project directory's tree plus every declared out-of-project input git resolves. A declared input that git
  cannot resolve (an untracked link path, a misspelling) has no object id: it is NEVER dropped, it is listed in
  `unresolved_inputs[<=64]`, and such a result is valid only at the commit it was judged on (section 4.4).
- **Toolsets** are stored once and referenced by id (`assay`, `run_gate`, `tester_image` components).

### 4.1 Levels held by a lane result

`levels` maps `R0`, `R1`, `R3` to `pass|fail` and `R2` to a stored R2 status (4.3). A lane held R0 when its run-gate
sequence member ran. Assay lanes hold the levels assay verified as claims: R1 and R3 take their OWN claim's outcome
(`PASS` is `pass`, `FAIL` or `ERROR` is `fail`), not the lane's overall status. A failed or errored claim whose section is
null (assay emits that for `ERROR`) means the level is not held; it never blocks assembly by itself. A command lane
(`kind: "command"`) is judged by run-gate, holds R0 only, carries the note `declared, not judged` and never feeds R1 or R3
even when its argv runs `assay run`. A failed level is stored only together with its `waiver{reason,plan_item,
expires_before}`, and a waiver needs a failed level. `coverage` is present exactly when R1 is held, `canary` exactly when
R3 is held; R1 `pass` needs coverage at or above its floor and R3 `pass` a passing canary.

### 4.2 Review

`accept` needs `range{from,to}`, `reviewer_role` and `document_sha256`; `missing` (an unlabelled commit, or an adopted
pre-record release) carries none of them. A reviewer is a role, never a person.

### 4.3 R2

An R2 result is the lane result of an R2 lane at the commit it judged, with `levels{R0: pass, R2: <status>}`, `scope`,
`campaign{started_at,completed_at,attempts,closeout_exit}`, `candidates`, `buckets`, `score`, `worker_counts`,
`deviations[]`, `attributed_from` (the judged commit when the result is stored at a published stand-in commit, else
null), `history[<=5]` and, for completed campaigns, `survivors`. Stored R2 statuses: `pass`, `pass-with-waivers`,
`survivors`, `incomplete`, `error`. `pending`, `running`, `superseded` and `not-judged` are derived (4.4), never stored.

| Status | Rule |
|---|---|
| `pass`, `pass-with-waivers`, `survivors` | closeout exit 0 or 1, `executed == total`, buckets sum to `executed`, survivor block present; `open + equivalent + accepted + resolved == buckets.survived` |
| `pass` | `open == 0` and `equivalent_reviewed + accepted == 0` |
| `pass-with-waivers` | `open == 0` and `equivalent_reviewed + accepted >= 1` |
| `survivors` | `open >= 1` |
| `incomplete` | closeout exit 3, no survivor block, `executed <= total` |
| `error` | closeout exit 2, no survivor block |

The close-out exit is read from the assay fragment, never supplied by the caller: 2 when assay judged the lane `ERROR`, 3
when the campaign has no completion time or did not execute every candidate, else 1 with survivors and 0 without.
`scope.kind` is `whole-source` or `changed-since`. Survivors: up to 200 are all `listed`; above 200 `listed` is empty,
`by_file` sums to the total and `sidecar_sha256` is set. A listed survivor has `resolved_by` exactly when its disposition
is `resolved`, and a `reason` when `equivalent-reviewed` or `accepted`. The transition table (section 17) is enforced by
`update`, not by the document validator.

### 4.4 Validity and the level summary (`rigor_record.derive`)

A stored result is never copied to another commit. A result is VALID AT a commit D exactly when every `targets` entry has
the same git object id at D; a result with `unresolved_inputs` is valid only at the commit that carries it. The level
summary of a commit reads only valid results and only the commit's OWN release sequence:

- `R0`: the commit's own sequence result names the member lanes; every member needs a valid R0 result; the value is the
  worst status of the members (a non-`PASS` sequence without a failed member reads `fail`). A commit without a release
  record of its own, or an empty sequence, is `not-judged`. Another record's sequence (for example a newer release that
  added a lane) never decides it.
- `R1`, `R3`: the worst status of the newest valid assay result per lane that holds the level, else `not-judged`.
- `R2`: `k/n <worst>` over the n R2 lanes of the commit's inventory (k with a valid result); with k = 0 `pending` (or
  `running` when a notes provider reports a campaign) on the project's latest current label, `superseded` on an older
  label and `not-judged` otherwise.
- `review`: the record's review status, `missing` without a record.

## 5. Distribution object and nearest-rank percentiles

```json
{"unit": "s", "n": 557, "n_excluded": {"contaminated": 3, "no_measurement": 0},
 "p50": 41.2, "p90": 77.9, "p98": 118.0, "p99": 131.4, "max": 402.7, "source": "assay-clock"}
```

1. **Rule (nearest rank).** For an ascending sample `x[1..n]` and an integer percent `p`, the value is
   `x[ceil(p * n / 100)]`, computed with integer arithmetic `(p*n + 99) // 100`. It is always a measured sample;
   nothing is interpolated.
2. **Minimum n.** `p50` needs `n >= 1`, `p90` needs `n >= 10`, `p98` needs `n >= 50`, `p99` needs `n >= 100`. Below
   that the field is `null`, because the value would equal the maximum and pretend to be a tail estimate. `max` and `n`
   are always present; `max` is `null` only when `n == 0`.
3. Percentiles are non-decreasing up to `max`. They are recomputed from raw subjects for every export; stored
   percentiles are never combined.
4. `unit` is `s | s_ref | bytes | ops | ratio`. `n_excluded` counts subjects removed before the sample (contaminated,
   no measurement); they are not in `n`. `source` names where the numbers came from (`assay-clock`, `memory.peak`,
   `sampled-sum-rss`, ...).
5. A metric that cannot be produced is `{"unavailable": true, "reason": "REASON_CODE"}`, never a distribution of zeros.
6. Metric names fix the unit: `*_bytes` is `bytes`, `*_ops` is `ops`, `*_ref` is `s_ref`, `cpu_per_wall` and
   `steal_fraction` are `ratio`, every other name (`wall_seconds`, `cpu_seconds`, `phase_seconds.*`) is `s`. IO is
   reported as counts, never as a rate. Memory is never normalised.

`golden/percentile-vectors.json` holds hand-computed vectors (n = 0, 1, 2, 9, 10, 11, 49, 50, 99, 100, 101, 557,
duplicates, floats). Every implementation of the rule MUST reproduce them.

## 6. Machine-speed normalisation (Geekbench, keyed by MAJOR)

1. **Baselines are per Geekbench major version.** Geekbench 6: 2500, the score of the Intel Core i7-12700 reference.
   Geekbench 7: 2500, the score of the AMD Ryzen 7 7700 reference. The two numbers are equal only by the choice of
   calibration; scores of different majors are NOT comparable. A tool MUST NOT assume a baseline for a major that is
   not listed here: the listed majors are `6` and `7`, and any other major is refused.
2. **Default for new measurements: the newest major (7).** Measurements already taken with major 6 stay valid and keep
   their major.
3. **Formula.** `x_ref = x * single_core / baseline(major)`, for `cpu_seconds` and `wall_seconds`. `single_core` is the
   single-core score of the host, measured with that same major. The unit reads "seconds on the Geekbench <major>
   baseline core" (`s_ref`).
4. **Majors are never mixed.** A normaliser takes the major with the score; a set of subjects normalised together must
   share one major (`common_major` refuses a mixed or empty set). In a record, a perf entry names its
   `normalisation.geekbench_major`, and a machine class it references whose speed has a different major is a violation.
   Nothing converts a score between majors.
5. **Machine class** `{id, cpu_model, cpus_available, hypervisor, speed}`. `speed` is either
   `{basis, geekbench_major, geekbench_version, single_core, multi_core, cpus_at_measurement, measured_on}` or
   `{"unavailable": true, "reason": ...}`. `geekbench_major` MUST equal the major of `geekbench_version`. `basis` is
   `geekbench-measured` (all of multi_core, cpus_at_measurement and measured_on present) or `geekbench-table` (the
   published-table fallback: `multi_core` null, because a published multi-core value describes the whole chip). The
   published-score table is keyed by major as well. `multi_core` is host context only.
6. **Normalisation record** `{basis, geekbench_major, divisor, reference_score, reason}`: either a basis
   (`geekbench-measured | geekbench-table`) with a major, divisor `single_core`, `reference_score == baseline(major)` and
   no reason; or basis `none` with no major, divisor `none`, no reference score and a reason code. Without a basis every
   `*_ref` metric MUST use the unavailable form. There is no silent default score.
7. Never in a record: the Geekbench result or claim link, a host name, a host identity.

## 7. Performance entries

`perf[]` entry: `lane`, `level`, `subject` (`r2-candidate | xdist-worker | lane-run`), `window` (`this-campaign`,
`this-run`, or `{runs<=200, first_commit, last_commit}`), `machine_classes{mN: count}` (every id declared in the
document), `status_counts{MEASURED,PARTIAL,NO_MEASUREMENT}`, `normalisation`, `metrics{name: distribution|unavailable}`,
`host_psi_some_avg{cpu,memory,io}` (percent or null).

## 8. Waivers: `rigor-waivers/1`

TOML (`<project>/rigor/waivers.toml`), validated in its parsed form. `schema` is required. `[[lane]]`:
`lane`, `level` (R0|R1|R3), `reason`, optional `plan_item`, `expires_before` (SemVer). `[[survivor]]`:
`candidate_id` (matched exactly), `disposition` (`equivalent-reviewed | accepted`), `reason`, `reviewed_by` (a role).
A `(lane, level)` pair and a `candidate_id` appear at most once. **Expiry:** a lane waiver with `expires_before = X`
refuses at version X and at every later version; a pre-release sorts before its release (`is_expired`).

## 9. Privacy (vbpub is public)

Never in a fragment, record, view, release body or git note line: host names, FQDNs, IP addresses; user names and uids;
container, worktree, campaign or supervisor names; absolute paths; environment values; tokens or anything
token-shaped; review prose; `browser.geekbench.com` (any `geekbench.com` host).

Enforced twice:

1. **Schema patterns** restrict each string (enums, hex digests, SemVer, tag and project patterns, project-relative
   paths, lane and test node ids, the CPU model `^[A-Za-z0-9 ()@.,_+-]{1,96}$`, short reasons).
2. **Lint** (`rigor_record.privacy`) scans every string and key. Rules: `local-path` (`/home/`, `/workspaces/`,
   `/tmp/`, `/run/`), `abs-path`, `home-tilde`, `windows-path`, `url` (`scheme://`), `at-sign`, `token-prefix`
   (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`, `glpat-`, `xox?-`, `sk-`, `AKIA`, JWT), `token-like` (32 or
   more of `[A-Za-z0-9+/_-]` with a lowercase letter, an uppercase letter and a digit; lowercase hex digests do not
   match), `secret-word` (`bearer|token|secret|password|apikey` followed by `=` or `:`), `env-assignment` (`NAME=value`),
   `uid` (`uid|gid` with a number), `ipv4`, `ipv6`, `geekbench`, `control-char`, and the operator's local lists
   `host` (substring) and `user` (whole word), supplied to the validator and never committed.
   **`@` exception:** `@` is allowed only inside a `cpu_model` value and only as ` @ ` (the CPU model string
   `Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz`).
   A finding names the JSON path and the rule and never echoes the matched text; a key that fails the lint is
   reported as `<redacted-key>`. A four-part dotted number (for example a four-part version) is an `ipv4` hit.

Every `validate_*` function includes the lint: a privacy hit is a violation. Notes (GN-1) reuse `lint`.

## 10. Size limits

| Item | Limit |
|---|---|
| `rigor/commits/<aa>/<commit>.json` (canonical form) | 64 KiB |
| fragment | 256 KiB |
| `release-body.md` | 8 KiB |
| generated `RIGOR.md` | 128 KiB |

`check_size(kind, data)` returns a violation over the limit; `validate_record` applies the record limit itself.

## 11. Validator dialect

The schemas are valid JSON Schema 2020-12. The packaged validator implements exactly: `type`, `enum`, `const`
(type-strict), `pattern` (search; a trailing `$` matches only at the very end), `minLength`, `maxLength`, `minimum`,
`maximum`, `exclusiveMinimum`, `properties`, `required`, `additionalProperties`, `propertyNames`, `items`, `minItems`,
`maxItems`, `oneOf` and `$ref` (local or `file.json#/pointer`). A schema keyword outside this set raises, so a
constraint is never silently ignored (a test keeps the packaged schemas inside the set). For `oneOf` with no match the
violations of the single closest alternative are reported. Semantic rules (sections 3 to 7) run only after the schema
passes.

## 12. Fixtures

`golden/fragments/` (assay-lane and run-gate-lane fragments, a command lane, a composite), `golden/records/` (one
commit-keyed record per stored R2 status and a withdrawn record), `golden/waivers/waivers.toml`,
`golden/percentile-vectors.json`, `golden/machine/` (cgprofile's own machine/1 objects). Every JSON fixture is in
canonical form and privacy-clean (tests enforce both).
`golden/assemble/` holds a complete `assemble` input set (the fragments including the `run-gate-composite`, `review.md`,
a cmru `build.json`, `waivers.toml`) and its expected output (`record.json`, `release-body.md`); `golden/project/` holds
a project tree (`rigor/commits/<aa>/<commit>.json`, `rigor/index.json`, `RIGOR.md`, `rigor/waivers.toml`) that `check`
accepts and `render --kind views` regenerates byte for byte. The cmru `build.json` is the one fixture that is not
privacy-linted: it is a local input and never published. The inventory of cmru's record step
(`cmru-release-inventory`) is built in the tests from `build.json` with the producer's exact keys.

## 13. `assemble` (RR-2, RR-2a)

`assemble` turns a gate's fragments into the release-time record of the tagged COMMIT (revision 1, one `release` label,
R0 and the assay-claimed R1/R3 decided, the review decided) and the release-body appendix. It is a pure function of its
inputs plus the git facts (the tag's commit, the project tree and the tree of every declared input at a commit); the
time of publication is an input (`--published-at`, default now at the command line only). Inputs: the project, tag,
commit and project directory; the fragments (every entry of the fragments directory must be a regular `.json` file);
the review document; a cmru inventory (`--artifacts`); optionally `waivers.toml`; `--r2-lane L` (repeatable) for the R2
lanes of the project. There is no `policy.toml`: the release sequence is the one `run-gate-composite` fragment.

1. **Grouping.** A lane is one `assay-lane` fragment and/or one `run-gate-lane` fragment. An assay fragment whose
   sha256 equals a run-gate fragment's `assay_fragment_sha256` belongs to that run-gate lane; a reference to a fragment
   that is not in the set refuses. Two fragments of one kind in a lane refuse.
2. **Sequence.** Exactly one `run-gate-composite` fragment names the member lanes of the release sequence. Every member
   needs a fragment and every fragment's lane must be a member; a member that is also an `--r2-lane`, and any fragment
   carrying R2 evidence, refuses (R2 enters through `update`). The composite must be for the project and commit.
3. **Binding (refusals).** The tag resolves to the commit. Every fragment is for the project and its `project_tree`
   equals the project tree of the tag's commit and of its own `judged_commit` (checked in git); the refusal names both.
4. **Review (refusal).** The review document must carry exactly one line
   `<!-- rigor-review: verdict=ACCEPT from=<tag|none> to=<40hex> reviewer=<role> -->` with `to` equal to the
   candidate commit. Prose is hashed (`document_sha256`), never read further or copied.
5. **Artifacts.** `--artifacts` is a cmru inventory of one of two kinds, and the consumer accepts both: the local
   `build.json` (`kind: cmru-local-build`, `publication: eligible`) and the inventory cmru's record step passes as
   `CMRU_BUILD_INVENTORY` (`kind: cmru-release-inventory`, no `publication` key, a `tag` equal to the release tag). Both
   must name the project and `source_commit`; the files' `sha256` and file names become the label's artifacts.
6. **Waivers (refusal).** A lane waiver whose `expires_before` is not after the release version refuses, used or not
   (the same rule as the pre-gate `check --waivers`). A failed level is allowed only when `waivers.toml` has a `[[lane]]`
   entry for its lane; the waiver is then stored on the result (a failed lane is never silently `pass`).
7. **Lane outcome and levels.** R0 is `fail` when the assay fragment's `outcome` is not `PASS` or the run-gate `exit` is
   not 0, else `pass`. R1 and R3 are held per CLAIM: the status of the claim's own outcome (4.1), with the fragment's
   `coverage`/`canary` section. A `PASS` claim without its section refuses; a failed or errored claim without it is a
   level not held, not a refusal. Command lanes hold R0 only.
8. **Targets.** The project tree plus every declared assay `input` git resolves at the commit; an input git cannot
   resolve is listed in `unresolved_inputs` (never dropped; 4).
9. **Toolsets and machine classes.** One toolset per distinct component combination, `t1`..; the composite's run-gate
   identity gets one too. Machine classes merge by `(cpu_model, cpus_available, hypervisor)` and are renumbered `m1`..;
   the same class with two different speeds refuses; perf entries are remapped and their counts added.
10. **Output.** `record.json` (canonical, at most 64 KiB) and `release-body.md` (at most 8 KiB, line-by-line privacy
    lint). The body has no heading: cmru's body handler appends it under `## Rigor at publication`. It names the
    project's `RIGOR.md` as a path (a URL would be a privacy hit). Every refusal lists all causes found at that stage.

## 14. `render` and `check` (RR-2, RR-2a)

`render` is a set of pure, byte-deterministic functions (no clock, environment or locale):

- **record**: `canonical_json` of a valid record (an invalid record is refused, never normalised).
- **body**: the 5-row table (R0, R1 with line/branch percent, R3, review, "R2 pending: runs after release on the
  latest release only"), the tools line and the `<project-dir>/RIGOR.md on main` line.
- **`rigor/index.json`** (`urn:vbpub:rigor-index:1`): `schema`, `project`, `latest` (newest current release label),
  `stable` (newest current release whose `promotion` qualification passed, else null), `adopted_from`, `labels` (tag to
  commit) and `commits`, newest first: every labelled commit plus the newest 200 unlabelled ones, each with `commit`,
  `labels`, `judged_at`, `levels{R0,R1,R3,review,R2}` (the derived level summary, 4.4), `r2_scope`, `anchored`.
- **`RIGOR.md`**: header, current guarantees, the table of the newest 25 commits (older ones only counted), the
  latest-release detail (lanes, up to 20 open survivors, waivers in force, deviations, per-lane performance as raw and
  Geekbench-normalised tables, machine classes) and a legend. Over 128 KiB the table loses rows; if it still does not
  fit, rendering refuses. Command lanes are listed as `declared, not judged`.

`check` is offline. For a project directory it validates every `rigor/commits/<aa>/<commit>.json` (valid, file name
equals the record's commit, canonical form so a hand edit is detected) and `rigor/waivers.toml`, and confirms
`rigor/index.json` and `RIGOR.md` equal their regeneration against the repository's object ids and that the index is
schema-valid. It fails on invalid or hand-edited files, never on lag; the published-tags-without-record listing
(`check --published`) stays open (RRK-07). `check --waivers --release-version V` is the pre-gate preflight: the waivers are
valid and none is expired at `V`. `check --commits RANGE --repo R` is the online commit check of section 17.

## 15. Command line

`rigor-record` (cli-extended): `assemble`, `render --kind record|body|views`, `check`, `sync`, `update`. Exit codes: 0
success, 1 a refusal or failed check (every cause printed), 2 usage. The operator's local denylists (host substrings,
user words) come from `--privacy-config FILE` or `$RIGOR_RECORD_PRIVACY_CONFIG` (TOML `hosts = [...]`, `users = [...]`),
never committed. `queue`, `base`, `adopt` and `note` arrive in RR-4 and GN-1.

## 16. The write procedure: `sync` and `update` (RR-3, RR-2a)

Every write to `main` (`sync`, `update`) runs one procedure (`rigor_record.store.write_through`, memo 5.4):

1. take the single-writer `flock` (`--lock-file`, default `~/.local/state/rigor-record/<repo>.lock`);
2. `git fetch <remote> <branch>` into `refs/remotes/<remote>/<branch>`; create a detached linked worktree there (the
   checkout's own working tree and index are never touched);
3. load and validate the project's records in that worktree; a commit already carrying the same
   `Rigor-Record: <project>@<commit> rev=<n> fragments=<key>` trailer for the record makes the whole call a no-op
   (idempotency);
4. compute the change (a pure function of the records and the inputs, section 17), write the changed
   `rigor/commits/<aa>/<commit>.json` files canonically (`<aa>` the first two hex digits) and regenerate
   `rigor/index.json` and `RIGOR.md` from all records;
5. `git add` then `git commit --only -- <project>/rigor <project>/RIGOR.md`; the message is
   `chore(rigor): <tag-or-commit-prefix> <level> <status>`, then a blank line and one trailer per changed record,
   `Rigor-Record: <project>@<commit> rev=<n> fragments=<sha256-12>`;
6. without `--push` the commit stays in the throw-away worktree (a dry run is reported and discarded); with `--push`,
   `git push <remote> HEAD:refs/heads/<branch>`.

A rejected push is a retry only when the remote branch moved: the attempt is discarded and the procedure restarts at
step 2 from the new tip (so views are recomputed, never merged), at most 5 times with a growing jittered pause. A push
that fails while the remote did not move is an error. Nothing is merged, rebased or force-pushed, and the worktree is
always removed. The fragment key is the first 12 hex of the sha256 of the sorted fragment digests (a qualification adds
its name; `withdraw` hashes its inputs; `sync` uses the asset digest).

`sync --project P --tag T --project-dir D` downloads `T-rigor.json` and `T-rigor.json.sha256` of the GitHub Release
(`gh release download`, behind the `ReleaseSource` seam), checks the digest file against the asset bytes, validates the
record (schema, semantics, privacy, size), requires revision 1, the requested tag as its only label and the project,
that the tag still resolves to the record's commit and that every recorded target still has its object id at that
commit, then commits the record. When the commit already has a record (earlier R2 evidence) the release record is FOLDED
in: the existing labels are kept, the existing results, qualifications and perf entries are added (toolset and machine
class ids remapped), a lane with a result in both refuses, and the folded record runs the full validation including the
privacy lint with the operator's denylists. A tag that already is a label of the record is a no-op.

## 17. `update` and the R2 transition table (RR-3, RR-2a)

Exactly one operation per call; every one honours `--expect-revision N` (compare-and-swap against the revision read
inside the lock) and bumps the revision of each record it changes. Records are keyed by commit; `--tag T` or
`--commit C` only names the record of a qualification, reconcile or withdraw, and re-verifies that a labelled tag still
resolves to the record's commit (a moved tag refuses).

- **R2 from fragments** (`--fragments DIR --attempts N [--closeout-exit E] [--base B] [--attribute-to C]`): every R2 lane
  needs BOTH halves, the assay fragment (with an R2 claim, campaign data and a passing R0 claim) and its run-gate
  fragment (tool identity, machine class, perf); an assay-only fragment refuses (RRK-11). The commit comes from the
  fragments: the result is stored in the record of its judged commit, which must be published (reachable from
  `refs/remotes/origin/main` or from a tag `origin` reports); a record is created for an unlabelled commit. With
  `--attribute-to C` (a published commit) the result is stored at `C` when every target is identical there, and the
  result lists `attributed_from`; declared inputs without a git identity cannot be shown identical and refuse. `--lane L`
  pins the one lane the fragments carry. The status is read from the fragment (4.3); `--closeout-exit` is only a
  cross-check and a differing value refuses, so a completed campaign can never be stored as `incomplete` or `error`.
  `--base B` must name a recorded clean commit or release label; `scope.anchored` is inherited from it (no `--base` is a
  whole-source, anchored campaign). Survivors are all open and listed up to 200; 201 to 500 collapse to per-file counts
  with the assay fragment's digest as the list digest; more refuse (RRK-09). Toolset, machine classes and R2 performance
  entries are merged into the record (a rerun replaces the lane's earlier R2 performance entries).
- **Allowed stored R2 transitions** (`rigor_record.update.TRANSITIONS`): `incomplete` and `error` to
  `pass | survivors | incomplete | error` (the earlier entry moves into `history`, newest 5 kept); `survivors` to
  `pass-with-waivers | survivors | pass` (reconcile and `resolved_by` only). A fragment for a lane whose R2 is final
  (`pass`, `pass-with-waivers`, `survivors`) is refused; a later campaign marks `resolved_by` instead. No `running`
  status is ever committed. There is no operator waiver verb: R2 waivers live only in `rigor/waivers.toml` and apply
  through `--reconcile`.
- **`resolved_by`**: when a completed campaign (`pass` or `survivors`) is stored at commit C', every older record of the
  project whose R2 is `survivors`, and whose open survivors C''s campaign covers (whole-source, or its base older than
  that record), gets each open survivor absent from C''s list marked `resolved` with `resolved_by: C'`; if none stays
  open its status becomes `pass-with-waivers` (something is waived) or `pass`. All changed records go in one commit.
  A campaign collapsed to per-file counts, or an older record that is, cannot be compared and is skipped. Only labelled
  commits are resolved (RRK-17 family).
- **`--qualification NAME`** with `--fragments`: a gate-level entry (`results[]`, judged commit) under
  `qualifications[NAME]` of the labelled record, written once; a failed lane refuses; every target must match the commit;
  `--stable` additionally requires the release to be the index's `stable` afterwards.
- **`--reconcile`**: the `[[survivor]]` rulings of `rigor/waivers.toml` (`disposition = "equivalent-reviewed"|"accepted"`)
  are applied to the open survivors by exact `candidate_id`; `survivors` becomes `pass-with-waivers` when none stays
  open. Nothing to apply is a no-op; collapsed survivors refuse.
- **`--withdraw T --reason R --replacement T2`**: sets the label's `state = withdrawn`; the replacement must be a current
  label of a record, newer. Assets and tags are untouched; `cmru release-mark` does the GitHub side.

`check --commits RANGE --repo R` lists commits of the range that touch `rigor/commits`, `rigor/index.json` or `RIGOR.md`
without the `chore(rigor): ` subject prefix or a valid `Rigor-Record:` trailer (hand edits of `waivers.toml` go through
normal packages and are not flagged).
