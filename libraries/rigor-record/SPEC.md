# rigor-record SPEC (RR-1)

Normative for `urn:vbpub:rigor-fragment:1`, `urn:vbpub:rigor-record:1`, `urn:vbpub:rigor-waivers:1`, the distribution
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
`listed` holds `min(total, 500)` entries with unique `candidate_id`; `sidecar_sha256` is set exactly when
`total > 500`; the campaign does not end before it starts. An assay fragment carries NO resource numbers.

### 3.2 `kind: "run-gate-lane"` (owner: run-gate)

`run_gate{version,wheel_sha256}`, `environment`, `image{ref,image_id,labels}|null`, `config_sha256`, `io_sync`,
`exit`, `started_at`, `completed_at`, `duration_seconds`, `assay_fragment_sha256|null`, `machine_classes[]`,
`perf[]` (section 7). `completed_at` MUST NOT precede `started_at`.

## 4. `rigor-record/1`

One record per project per release, at most 64 KiB (canonical form). Top level: `schema`, `project`, `revision`,
`adopted_from` (tag or null), `release`, `toolsets{tN}`, `levels{R0,R1,R3,review,R2}`, `qualifications{name}`, `perf[]`,
`machine_classes[]`. `release.tag` ends in `-v<version>`; `state` is `current|withdrawn`, and `withdrawn`
(`{reason, replacement}`) is set exactly when the state is `withdrawn`. Every toolset id used by a level, a history
entry or a qualification MUST exist in `toolsets`.

### 4.1 Gate levels (R0, R1, R3, and each qualification)

`status` is one of `pass`, `pass-with-waivers`, `not-applicable`, `waived`, `unknown`. Keys: `toolset`,
`judged_commit`, `lanes[{lane,outcome,verdict_sha256|null,config_sha256,waiver?}]`, `waiver|null`; R1 adds `coverage`
and R3 adds `canaries[]`, and no other level may carry them.

| Status | Rule |
|---|---|
| `pass` | toolset, judged commit and at least one lane; every lane `PASS` with no waiver; no level waiver |
| `pass-with-waivers` | as above, but at least one lane failed, and exactly the failed lanes carry a `waiver{reason,plan_item,expires_before}` |
| `not-applicable`, `unknown` | no lanes, toolset, judged commit or waiver (a named gap, never rendered as a pass) |
| `waived` | `waiver{id,reason}` set |

R1 `pass` additionally needs `coverage` at or above its own floor (line and branch); R3 `pass` needs at least one
canary and every canary `PASS`.

### 4.2 Review

`accept` needs `range{from,to}`, `reviewer_role` and `document_sha256`; `missing` (adopted pre-record releases)
carries none of them. A reviewer is a role, never a person.

### 4.3 R2

Stored statuses: `pending`, `pass`, `pass-with-waivers`, `survivors`, `incomplete`, `error`, `partial`,
`not-applicable`, `waived`, `unknown`. `superseded` is derived at render time and MUST NOT be stored. Keys are always
all present: `toolset`, `judged_commit`, `deviations[]`, `scope`, `campaign`, `candidates`, `buckets`, `score`,
`worker_counts`, `survivors`, `lanes[]`, `waiver`, `history[<=5]`.

| Status | Rule |
|---|---|
| `pass`, `pass-with-waivers`, `survivors` | toolset, judged commit, scope, campaign (closeout exit 0 or 1), candidates (`executed == total`), buckets (sum to `executed`) and survivors present; `open + equivalent + accepted + resolved == buckets.survived` |
| `pass` | `open == 0` and `equivalent + accepted == 0` (survivors all `resolved` by a later campaign still count as `pass`) |
| `pass-with-waivers` | `open == 0` and `equivalent + accepted >= 1` |
| `survivors` | `open >= 1` |
| `incomplete` | campaign (closeout exit 3) and candidates present, `executed <= total` |
| `error` | campaign (closeout exit 2) and candidates present |
| `partial` | `lanes` has at least one `complete` and one `not-runnable` lane |
| `waived` | `waiver{id,reason}` set; no other status carries a level waiver |
| `pending`, `not-applicable`, `unknown` | no campaign data: campaign, candidates, buckets, survivors, score null; no workers, lanes or waiver |

`scope.kind` is `whole-source` (no base tag, anchored) or `changed-since` (names `base_tag`). Survivors: up to 200
survivors are all `listed` (counts per disposition equal the listed counts, `by_file` empty, no sidecar); above 200
`listed` is empty, `by_file` sums to the total and `sidecar_sha256` is set. A listed survivor has
`resolved_by` exactly when its disposition is `resolved`, and states a `reason` when `equivalent` or `accepted`.
`history` entries are earlier R2 entries and carry no history of their own. The transition table of memo 3.4 is
enforced by `sync`/`update` (RR-3), not by this document validator.

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
`candidate_id` (matched exactly), `disposition` (`equivalent | accepted`), `reason`, `reviewed_by` (a role).
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
| `rigor/releases/<tag>.json` (canonical form) | 64 KiB |
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

`golden/fragments/` (2 assay-lane, 2 run-gate-lane), `golden/records/` (one record per stored R2 status, an adopted
`unknown` record and a withdrawn record), `golden/waivers/waivers.toml`, `golden/percentile-vectors.json`. Every JSON
fixture is in canonical form and privacy-clean (tests enforce both).
