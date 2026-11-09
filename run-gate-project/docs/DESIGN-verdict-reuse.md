# Design: exact per-lane PASS verdict reuse

**Status: DESIGN.** This document specifies a later implementation; this change adds no executable reuse behavior.

## Goal and decisions

Run-Gate may avoid repeating a deterministic lane only when a trusted PASS
record proves the exact lane identity. The release gate opts into lookup per
lane. A miss always runs the lane normally and is disclosed; a hit is also
disclosed. Reuse never turns a partial, contaminated, or ambiguous run into a
PASS.

The first cut is a SHA-256 keyed lookup in Run-Gate's existing evidence
location. It adds no cache service, background worker, parallel lane runner,
prebuilt Assay image, or history-based heuristic. `.run-gate/history.json`
remains telemetry, not a verdict authority.

| Decision | Rule | Source |
|---|---|---|
| PASS authority | Run-Gate stages a non-reusable candidate after its clean PASS checks. Run-Gate alone promotes it, after independently revalidating the candidate identity, immutable snapshot, visible refs, and lane outputs with the same validator CMRU uses. A candidate nonce selects a candidate; it is not an authority token. There is no consumer receipt or caller-written success assertion. | Operator decision 1 and review correction B1 |
| Tree scope | Hash the complete selected test tree. A narrower closure is allowed only after it is proven to contain every test, fixture, vendored source, and file read by the lane. The first cut uses the complete selection. | Design default for review decision 3; safer and simpler to audit |
| Coverage floor | A retained verdict may satisfy the whole-project floor only when it carries complete line and branch totals, no exclusions, and the required evidence artifacts. The floor remains an independent full-project check, never Assay's changed-line result. | Operator decision 2 |
| Release-prep paths | Projects explicitly list these paths. Their bytes are removed from the tree digest only for lanes with a proven empty read set. Every lane with a non-empty `release_prep_reads` list is `never` in v1, even when the listed bytes are unchanged; an unknown or incomplete read set is also `never`. | Operator decision 3 |
| Separate canary | Keep the coverage-floor canary as a separate live check in v1. Assay's import-break R3 result cannot stand in for the uncovered-line canary. | Design default for review decision 6; the checks reject different failures |
| Live inputs | `ref-probe`, `installed-wheel-live`, every R2 lane, and every lane that reads network, wall-clock, mutable refs, or current host facts are `never`. A config opt-in cannot override these hard vetoes. | Operator decision 1 and review correction |
| Resource warnings | A PASS with a resource-contamination or resource-warning marker is ineligible. | Design default for review decision 8 |
| Expiry | Records expire 30 days after `completed_at`; this is a bounded design default, not an operator-set retention period. An expired record is a miss. | Design default for review decision 7 |

### B1 promotion choice

The promotion path must not accept a caller-authored statement that CMRU
finalization succeeded. The three options are:

| Option | Rule | Decision |
|---|---|---|
| (a) Run-Gate validates snapshot/output state | Extend CMRU's read-only snapshot/output validator once to check the complete visible-ref digest and lane-output contract; Run-Gate promotion and CMRU `finish_lane` call that implementation. CMRU may request promotion with the public candidate nonce, but supplies no result or digest assertions. | **Choose.** This keeps Run-Gate as the sole PASS authority and adds no receipt format, secret, or signing path. |
| (b) Receipt carries a value from validation | The caller supplies a validation value that Run-Gate then recomputes from the tree and refs. | Lose: once Run-Gate recomputes the value, the receipt adds no authority or information; without recomputation, the value is forgeable. |
| (c) Run-Gate MACs a receipt | Run-Gate keeps a secret and verifies a MAC on the consumer receipt. | Lose: secret custody, handoff, and protection from the lane caller add machinery when Run-Gate can check the underlying state directly. |

No trust question remains open for this design: option (a) implements the
operator's existing rule that only Run-Gate on a clean tree writes PASS.

The retained `4a103229d` Assay PASS is **not** a seed record: its history has a
6.3-second resource-contamination warning and the producer Run-Gate payload
identity is absent. It also lacks the complete runtime identity required here.
Run-Gate must not backfill it. Although `4a103229d` and `8cd845b57` have the
same Git tree, review found their effective `setuptools_scm` versions differ;
that makes their identity digests differ. The savings below are prospective
for a later exact repeat whose complete identity is unchanged and whose
selected lane has a proven empty release-prep read set.

## 1. Per-lane identity digest

### Canonical form

Before a reusable lane starts, Run-Gate builds a JSON object with schema
`identity_components/1`, serializes it as UTF-8 canonical JSON (sorted keys,
compact separators, no NaN), and computes:

```text
sha256("run-gate-lane-identity/1\0" || canonical_json(identity_components))
```

Arrays whose order is meaningful (argv, path lists, plugin load order) retain
that order. Sets (for example plugin distribution identities) are sorted by
their stable name. Unknown, unavailable, or malformed required input means no
identity and therefore no reuse. Run-Gate does not invent a placeholder for a
fact it could not read.

The identity object stores the exact argv as its canonical SHA-256 and the
exact effective environment as a canonical SHA-256 over sorted name/value
pairs. Their raw values stay in memory only; no argv/env secret is written to
evidence. `identity_components/1` is a closed schema: every key and nested key
shown under `identity_components` in the record example is required, and any
missing or unknown key rejects the record. Each group digest is computed from
that group's canonical value; the whole object is then hashed by the formula
above. For an applicability-qualified group, use the explicit
`not-applicable` sentinel only when the lane truly has no such input; its group
digest hashes the canonical JSON string `"not-applicable"`. Unknown or
unavailable inputs still fail closed. A reader can therefore validate the
closed field set and recompute the complete top-level identity digest without
trusting fields stored elsewhere in the record.

The digest uses the exact selected lane tree as presented to the test process.
Hash a sorted manifest of every included path, its Git mode/type, and the
SHA-256 of its bytes (or symlink target/gitlink commit); include tests,
fixtures, generated vendored packages, and the full contents of included
libraries. Include the complete snapshot selection policy and omissions. The
manifest is content based, not mtime based. Pre-run and post-run cleanliness
checks alone do not prove that a lane saw one tree: a file could change and be
restored between those checks. For v1, lookup and candidate creation require an
immutable CMRU gate-snapshot view. The selected source paths exposed to the lane
must be read-only for the full run, and the backing snapshot must be
unmodifiable by other processes for that interval; a read-only bind mount over
a writable host path is insufficient. The lane's visible Git refs must also be
frozen for that interval. Outputs go to separate writable paths. A CMRU
snapshot qualifies only when these properties are enforced, not merely because
`prepare_lane` and `finish_lane` bracket the invocation. Bind its stable
snapshot-manifest digest and digest of all Git refs visible to the lane into
the identity, verify both again before staging, and re-verify them at
promotion. Direct Run-Gate invocations on mutable worktrees remain live-only.
If the lane can read untracked/ignored or external files outside the immutable
selection or frozen ref view, it is `never` until those inputs are declared and
captured.

The identity object contains exactly these input groups:

| Input group | Required contents |
|---|---|
| Project identity and tree | `repo_id = SHA256(realpath(git rev-parse --path-format=absolute --git-common-dir))`; project path relative to repository root; selected-tree manifest digest; lane name and kind. Do not persist an origin URL or credentials. If Git common-dir cannot be resolved, miss. The selected-tree digest is content-based and does not itself require producer and consumer HEAD commit IDs to match. The snapshot-manifest, visible-ref, and SCM-derived components must also match, so a changed commit/ref may still produce a miss. |
| Project and central configuration | SHA-256 of the exact selected `run-gate.toml` and resolved central config bytes, plus the merged effective lane policy. Include the project-declared release-prep path list and lane read declarations. |
| Assay | Assay source-tree digest (including vendored `cowtree` where packaged), installed/effective Assay version, schema digest/version, and exact effective `assay.toml` lane configuration. For same-repository consumers the source is the selected worktree's editable Assay source, as required by `assay/docs/INTERNAL-CONSUMERS.md`. |
| SCM-derived package versions | Exact effective versions for the project and every SCM-derived package used by the lane (including vendored libraries and wheel dependencies built from source), plus the `setuptools_scm` context that produced each value: selected tag, distance, commit, dirty state, config, and any pretend-version override. A missing or unresolved version is a miss. |
| Run-Gate payload | Installed Run-Gate SemVer and SHA-256 over the exact installed payload files that can affect execution (CLI module, admission/profile modules, schemas, and package data). Version alone is insufficient. |
| Lane command | Exact lane argv after Run-Gate substitutions and exact caller-selected lane arguments, including workers/jobs, comparison-base request, and other behavior-changing flags. Hash shell strings byte-for-byte. The resolved R1 base commit is a separate field. No unrecognized argv normalization is allowed. |
| Effective environment | Sorted name/value mapping of every variable delivered to the lane, including configured variables and all passthroughs after resolution, plus environment/image defaults that the lane can observe. Hash values in memory; persist only the resulting environment digest, never raw values or secrets. An unreadable passthrough is a miss. |
| Resources and isolation | Resolved CPUs, memory, swap, pids, I/O sync and CoW policy, worker/job limits, network/isolation settings, and all resource overrides. Include the resolved cgroup parent and host-provided resource facts if the lane is eligible to use them; in practice a result that depends on changing host facts is `never`. |
| Snapshot and visible refs | Digest of the stable CMRU snapshot-manifest identity fields, plus SHA-256 of the complete sorted ref/object-id listing visible from the lane's Git common-dir. The view must freeze that ref set during the lane. Exclude volatile manifest fields such as refresh time and per-lane result codes, but include the source commit/tree, snapshot identity/path, selection policy, protected-ref scope, protected-ref digest, and anchor refs. Re-read both before staging and at promotion; an absent or changed value is a miss. |
| Tester and runtime | Immutable tester image content digest actually used, not a tag; Python implementation/version/ABI; pytest and coverage versions; and every enabled pytest/plugin identity (distribution name/version and loaded entry point/module). Missing image or runtime identity is a miss. |
| Vendored and wheel dependencies | Digest of a sorted manifest of every vendored source package and installed wheel dependency, including exact wheel bytes and metadata for released wheels. Source-built wheels are also bound by their source closure and build context. |
| R1 comparison | The resolved commit SHA used as the R1 comparison base. Use an explicit `not-applicable` value only for a lane whose declared rigor has no R1 judgment. A tag/ref string without its resolved commit is insufficient. |
| Build context | For any lane that builds a wheel: `SOURCE_DATE_EPOCH`, effective `setuptools_scm` context, and immutable builder image/toolchain digest. Use `not-applicable` only when the lane does not build a wheel. |

The selected-tree manifest includes test files, fixture contents, and included
vendored libraries rather than only production source. For CMRU, the source
closure includes `libraries/worktree`; Assay's package closure includes
`libraries/cowtree`; and any source-built cli-extended wheel is an input. A
released wheel dependency is identified by its exact installed wheel digest
and metadata. This prevents a project-subtree-only key from silently missing a
vendored or wheel-build input.

On a live run, collect Python/pytest/coverage/plugin identities inside the
actual tester container. A reusable lane must use a fresh container whose
runtime is determined by its immutable image and captured inputs. Image
identity alone cannot validate a persistent runtime: its writable layer may
change Python, pytest, coverage, or a plugin while the configured image ID
stays fixed. Therefore every `exec`, `host`, and `bare-host` environment is
`never` for v1. This is simpler and safer than probing mutable runtime state on
every lookup; changing that policy requires exact runtime identities to be
re-collected and compared before every hit. The live run also compares the
started container's actual image ID to the preflight ID before staging a
candidate.

### Per-lane eligibility declaration

Add one closed-valued lane key:

```toml
verdict_reuse = "exact" # explicit opt-in
# or
verdict_reuse = "never" # explicit live lane
```

An omitted key means `never`. `exact` is an assertion by the lane owner that
all behavior-changing inputs are captured by the identity above. Run-Gate
hard-refuses reuse for R2, every `exec`, `host`, and `bare-host` environment,
live snapshot/ref checks, and other known live-input lane kinds even if a
config says `exact`. Every
network-, time-, host-, remote-tag-, or mutable-state-dependent lane must say
`never`; its config comment records why. No runtime heuristic infers that a
lane is deterministic.

| Lane or lane class | v1 decision | Reason |
|---|---|---|
| CMRU `ref-probe` | Never | It tests current protected-ref visibility and mutates the live gate snapshot. |
| CMRU `installed-wheel-live` | Never | It proves the current run's fresh snapshot manifest and live-snapshot boundary. |
| Any R2/mutation lane | Never | It resolves published tags and current remote baseline facts. |
| Any `exec`, `host`, or `bare-host` environment | Never in v1 | Its runtime can change while a configured image ID stays fixed; persistent runtime identities are not re-probed on lookup. |
| Any network/time/current-host lane | Never | Those facts are not frozen by a source tree digest. |
| CMRU `assay` (R0/R1/R3) | Never for the current full-suite selection | `cmru/tests/test_w3_prep_changelog_preflight.py` reads `cmru/CHANGES.md`; a later read-free selection may opt in only after that test remains live elsewhere. |
| CMRU `coverage` | Never for the current full-suite selection | Its `pytest tests` command includes the prep reader; complete coverage evidence does not override the live-reader rule. A later read-free selection must still satisfy the complete artifact contract below. |
| CMRU `canary` (uncovered-line canary) | Never in v1 | It remains a separate release-time rejection check; Assay R3 does not substitute for it. |
| Composite release lane (`gate`, `gate-provisional`) | Never | The consumer decides leaf-lane order and must keep live lanes live. |

These CMRU decisions describe the current full-suite selections, not a global
assumption that all Assay lanes are reusable. The `installed-wheel` build-smoke
lane may opt in only after its wheel build context is complete; the live
variant above never opts in. Any future exact CMRU selection must prove its
prep read set is empty and keep the known reader in a live lane.

## 2. Release-prep-only paths

Add project-scoped configuration to the selected `run-gate.toml`:

```toml
[project.verdict_reuse]
release_prep_only_paths = ["CHANGES.md"] # add exact version paths when used

[lanes.assay]
verdict_reuse = "never"
release_prep_reads = ["CHANGES.md"]
```

Paths are exact, tracked, project-relative files. Reject absolute paths,
parent traversal, symlink escapes, globs, directories, and a path outside the
selected project. The path list and read declarations are configuration
inputs. For a lane with an empty, complete `release_prep_reads` list, omit the
listed file bytes from that lane's selected-tree digest. A lane with any
declared prep read is `never` in v1, regardless of whether those bytes changed.
If the read set is unknown or incomplete, it is also `never`. There are no
per-record prep-path witnesses and no unchanged-witness hit path.

For the CMRU example, `cmru/tests/test_w3_prep_changelog_preflight.py` reads
`cmru/CHANGES.md`; any lane whose selection contains that test declares the
read and remains live even when `CHANGES.md` is unchanged. A future design may
split out a registered lane whose proven read set is empty, but v1 does not
guess which tests read which files or skip a known reader.

## 3. Staged candidate, durable PASS record, and trust boundary

### Record format and location

After a live PASS and Run-Gate's own checks, write a non-reusable staged
candidate at:

```text
<RUN_GATE_EVIDENCE_DIR>/projects/<project-key>/lanes/<lane>/candidates/<nonce>.json
```

The candidate is not under `passes/` and lookup never scans it. After
Run-Gate's independent post-lane validation, it promotes the candidate
atomically to the
immutable PASS-record path:

```text
<RUN_GATE_EVIDENCE_DIR>/projects/<project-key>/lanes/<lane>/passes/<identity-sha256>/<run-id>.json
```

`RUN_GATE_EVIDENCE_DIR` already selects the evidence root, and Run-Gate
already groups lane logs under `lanes/<lane>/`. The record is separate from
`.run-gate/history.json` and from the per-run log. The project key is a stable
hash of the repository identity plus the project path relative to repository
root, so different projects cannot share a record by lane name alone.

Schema `run-gate-pass/1` has this closed `identity_components/1` object. The
same object is carried inside the staged candidate; only its enclosing record
schema and state change at promotion.

```json
{
  "schema": "run-gate-pass/1",
  "lane": "coverage",
  "lane_kind": "command",
  "verdict": "PASS",
  "identity_sha256": "<64 lowercase hex>",
  "run_gate_version": "<installed SemVer>",
  "run_gate_payload_sha256": "<64 lowercase hex>",
  "identity_components": {
    "schema": "identity_components/1",
    "project": {"repo_id": "<sha256>", "path": "cmru"},
    "project_identity_sha256": "<sha256>",
    "lane": {"name": "coverage", "kind": "command"},
    "lane_sha256": "<sha256>",
    "selected_tree_manifest_sha256": "<sha256>",
    "snapshot_manifest_sha256": "<sha256>",
    "visible_refs_sha256": "<sha256>",
    "project_config_sha256": "<sha256>",
    "central_config_sha256": "<sha256>",
    "effective_lane_policy_sha256": "<sha256>",
    "assay": {
      "source_sha256": "<sha256-or-not-applicable>",
      "version": "<version-or-not-applicable>",
      "schema_version": "<version-or-not-applicable>",
      "schema_sha256": "<sha256-or-not-applicable>",
      "config_sha256": "<sha256-or-not-applicable>"
    },
    "assay_sha256": "<sha256>",
    "scm_context_sha256": "<sha256>",
    "run_gate": {"version": "<installed SemVer>", "payload_sha256": "<sha256>"},
    "run_gate_identity_sha256": "<sha256>",
    "argv_sha256": "<sha256>",
    "environment_sha256": "<sha256>",
    "resource_isolation_sha256": "<sha256>",
    "tester_image_digest": "sha256:<64 lowercase hex>",
    "runtime_identity": {
      "python": {"implementation": "CPython", "version": "<version>", "abi": "<abi>"},
      "pytest": {"version": "<version>"},
      "coverage": {"version": "<version>"},
      "plugins": [{
        "distribution": "<name>",
        "version": "<version>",
        "entry_point_or_module": "<name>"
      }]
    },
    "runtime_identity_sha256": "<sha256>",
    "r1_base_commit": "<full SHA-or-not-applicable>",
    "vendor_wheel_dependencies_sha256": "<sha256>",
    "build_context_sha256": "<sha256>",
    "release_prep_policy_sha256": "<sha256>"
  },
  "producer_head": "<full Git commit SHA>",
  "producer_head_tree": "<full Git tree SHA>",
  "selected_tree_sha256": "<64 lowercase hex>",
  "completed_at": "<UTC RFC 3339>",
  "expires_at": "<UTC RFC 3339>",
  "resource_status": "clean",
  "evidence": {},
  "record_sha256": "<sha256 of canonical record without this field>"
}
```

That exact field set is normative; no additional or missing fields are
accepted. The `project_identity_sha256` and `lane_sha256` fields hash their
adjacent objects. `assay_sha256`, `run_gate_identity_sha256`, and
`runtime_identity_sha256` hash their corresponding objects; the other named
group fields hash the canonical value defined for that input group above.
`tester_image_digest` is the actual immutable image digest. `r1_base_commit`
is a full commit SHA or the explicit `not-applicable` value. The top-level
lane, lane kind, Run-Gate version, and payload fields must match the values
inside `identity_components`; top-level `selected_tree_sha256` must equal
`identity_components.selected_tree_manifest_sha256`. The candidate schema is
`run-gate-candidate/1`, adds its one-use random `nonce`,
`candidate_state: "staged"`, and `candidate_sha256`, and otherwise carries the
same identity, verdict, and evidence fields. At promotion Run-Gate validates
the candidate self-hash, removes candidate-only fields, changes the record
schema to `run-gate-pass/1`, and computes a new `record_sha256`. A candidate
never counts as a PASS record.

There is no `release_prep_witnesses` field. The closed
`release_prep_policy_sha256` binds the project path list and the lane read
declaration. A record is eligible only when the lane's declared read set is
empty; a reader lane is refused before lookup, so no witness can make it
reusable.

`evidence` contains hashes and paths for the exact Run-Gate/Assay closeout,
coverage totals, and required artifact copies; it never contains raw secrets.
The record binds the PASS to producer HEAD and tree even though the identity
digest is content-based and may be equal across commits. The record's
self-hash detects truncation or accidental edits; it is not an authenticity
signature. Neither `identity_sha256`, `candidate_sha256`, nor
`record_sha256` authenticates a caller's claim that CMRU accepted the lane;
they are consistency checks only. Promotion authority comes from Run-Gate's
own snapshot/ref/output validation.

For a coverage-bearing PASS, `evidence.coverage` records the sorted complete
source-file inventory, coverage format/version, `statements`, `covered_lines`,
`missing_lines`, `excluded_lines`, `branches`, `covered_branches`,
`missing_branches`, and `excluded_branches`, plus SHA-256 and retained paths
for each declared coverage/JUnit artifact. Unknown or omitted counters are
not zero; they make the proof incomplete.

### Who may write and what counts as durable

Only Run-Gate writes or promotes records. After a live PASS it may stage a
candidate only when the immutable lane view, complete identity, resource
status, clean-tree state, and ordinary Run-Gate checks are valid. The candidate
remains non-reusable until Run-Gate's promotion operation independently
revalidates it.

Run-Gate's promotion operation and CMRU's `finish_lane` call one shared
read-only snapshot/output validator. Extend the current validator once to
check the complete visible-ref digest and the lane-output contract; this is
not two validators that can drift. Run-Gate re-reads the candidate identity,
immutable snapshot manifest, complete visible-ref listing, clean HEAD/tree
state, and lane outputs from their source. Only then does Run-Gate atomically
promote the candidate. CMRU may request this check after its post-lane
identity recheck, `finish_lane`, and manifest update, but its report is not
authority. The request supplies only the candidate nonce to select a staged
candidate. It does not accept `consumer`, `result`, digest fields, or JSON via
`--receipt-stdin`; a nonce is public and cannot substitute for Run-Gate's own
validation.

If the lane fails, is skipped, aborted, selective, or has an infrastructure
error, Run-Gate does not stage a candidate. If CMRU finalization rejects the
lane state, a later promotion attempt with the candidate nonce still fails
Run-Gate's independent validation and leaves the candidate staged. A forged
success receipt cannot promote it because the receipt interface does not
exist. A candidate nonce is one-use; replay after promotion cannot create or
overwrite another PASS record. A nonce by itself proves nothing about another
invocation: Run-Gate still checks the selected candidate against the current
identity, snapshot, refs, and outputs. Receipt-shaped fields from another
invocation are never read.

The evidence root must be persistent across release-gate invocations, outside
the judged worktree, and protected so the trusted Run-Gate writer is the only
identity allowed to modify records. A 0700 directory alone is not an authority
boundary when the caller/agent owns it. If the configured root is temporary,
inside the worktree, writable by the agent/worktree, or cannot be checked,
Run-Gate refuses all reuse and record writes and runs the requested lane live.
The current default is `/tmp/run-gate`; it does not by itself prove
persistence or exclusive-writer trust. The deployment must point the existing
`RUN_GATE_EVIDENCE_DIR` at the protected persistent evidence root before reuse
can yield a hit.

Run-Gate creates candidates and promoted records atomically with restrictive
permissions and no symlink following. It never reads legacy history entries
as records, never backfills the retained `4a103` history, and never trusts a
record copied into the worktree. A candidate nonce is single-use; if a
candidate or run-id path already exists, Run-Gate refuses to overwrite it.

### Freshness and contamination

A record is fresh only when all of the following hold: current UTC time is
before `expires_at`; its schema, Run-Gate version, and Run-Gate payload digest
match exactly; its self-hash verifies; the record is in the protected evidence
root; the lane's complete prep read set is empty; and every identity component
is present and valid. There is no schema migration or compatibility fallback.
The 30-day expiry is fixed at record creation and is not extended by a cache
hit.

When an identity directory has multiple records, lookup scans them newest
first by valid RFC 3339 `completed_at`, with ascending run-id as the stable
tie-break. A file with an invalid/missing timestamp sorts after timestamped
files by path. Each record is validated in that order; a malformed, expired,
or otherwise invalid record is skipped with its rejection reason disclosed,
and scanning continues. The first valid record wins. If none validate, the
lane reports a miss and runs live.

Any Run-Gate contamination flag, resource warning, unexplained memory stall,
OOM/pressure warning, incomplete resource observation, or failed post-run
clean-tree check makes a PASS ineligible. A later clean live PASS may create a
new record. Historical warning-bearing PASSes are never promoted.

## 4. Consumer request, surfaced verdict, and coverage floor

Add a distinct Run-Gate CLI opt-in, `--reuse-verdict`, which means “look for a
trusted prior PASS for this lane before executing it.” It is not the existing
Assay R2 `--reuse-from PATH`, which reuses proven mutation candidates. Direct
`run-gate <lane>` stays live by default. `cmru/tools/run_release_gate.py`
adds `--reuse-verdict` to its leaf-lane requests. Run-Gate honors it only for a
lane configured `verdict_reuse = "exact"`; `never` lanes are refused before
lookup and run live. This keeps the eligibility policy in the selected
Run-Gate configuration rather than duplicating lane names in CMRU. CMRU keeps
its current serial lane order and continues to run `ref-probe` and other live
checks every time. V1 lookup and record creation are available only through a
CMRU invocation bound to an immutable gate-snapshot view. A direct
`run-gate --reuse-verdict <lane>` invocation without that snapshot binding
reports a miss and runs live; this avoids a second snapshot/finalization path.

For a live CMRU PASS, Run-Gate returns the staged candidate nonce. CMRU then
performs its post-lane Run-Gate identity recheck, `finish_lane`, and snapshot
manifest update. It may request `run-gate finalize-candidate <nonce>` after
those steps, but sends no receipt. Run-Gate checks the candidate self-hash and
recomputes the snapshot, visible-ref, clean-tree, and output validation itself
using the same validator CMRU calls. A forged receipt-shaped input, failed
validation, changed identity/tree/ref state, or failed promotion leaves the
candidate staged and invisible to lookup. Run-Gate alone performs promotion.

On a hit, Run-Gate emits a machine-readable result and a human line such as:

```text
run-gate: REUSED PASS from <record-path>, digest sha256:<identity-digest>
```

It records `reused_from`, the record SHA-256, and the identity digest in the
current invocation result/history. The `run-gate` lane log is retained in its
evidence directory and the CMRU helper inherits that line into the aggregate
release-gate log; this is the reuse evidence beside CMRU's per-lane exit code.
A miss is explicit (`reuse miss: <reason>; running live`) and produces
ordinary lane evidence. A digest mismatch is a refusal to reuse, not a reason
to skip the test or to manufacture a failure; the lane runs live. If the
requested lane itself refuses dirty trees, its existing clean-tree refusal
still applies.

Coverage is reusable only from a trusted PASS record with complete whole-project
coverage evidence for the exact requested lane identity. Require all configured
source files, all statements and branches accounted for, all line and branch
totals covered to the configured floor, zero excluded files/lines, and
hash-verified required artifacts (`coverage.json` and JUnit where declared).
Missing totals, a changed source-file inventory, an absent/corrupt artifact,
line-only totals, or Assay's changed-line-only PASS is a cache miss. In v1,
Run-Gate does not map an `assay` record onto the separate `coverage` lane:
the exact argv and lane identity differ. The separate coverage floor remains a
real check, and the separate uncovered-line canary remains live. A prior
eligible `coverage`-lane record can satisfy that same lane on an exact digest
match; it cannot stand in for the canary.

## 5. Refusals and failure behavior

| Condition | Required behavior |
|---|---|
| Identity digest differs | Reject the record; print miss reason; run live. |
| Missing, malformed, or unknown identity field | Reject the record and do not create a digest from defaults; print miss reason; run live. |
| Record written by another Run-Gate version or payload | Reject; run live. No legacy fallback. |
| Mutable direct worktree or missing CMRU snapshot binding | Refuse lookup and candidate creation; run live. |
| Worktree dirty, HEAD/tree changed, or Git operation active | Refuse reuse; do not stage or promote a candidate. Run live only if the lane's ordinary preflight permits it. |
| Promotion request contains a forged/malformed receipt or success assertion | No receipt input is supported; reject it. The nonce alone does not authorize promotion, and Run-Gate's own validation must pass. |
| CMRU finalization rejects lane output, or Run-Gate's independent validation fails | Leave the candidate staged and non-reusable; a later lookup misses unless another independently validated PASS exists. |
| Candidate nonce was consumed already or its candidate identity/snapshot does not match current state | Refuse promotion without overwriting records; the other candidate remains unaffected. |
| Snapshot manifest or visible refs changed before promotion | Refuse promotion; leave the candidate non-reusable and report the mismatch. |
| Persistent `exec`, `host`, or `bare-host` runtime | Refuse lookup and candidate creation in v1; run live. |
| Record expired, self-hash invalid, or lane has a non-empty/unknown release-prep read set | Reject; run live, even if prep-path bytes are unchanged. |
| Evidence root is agent/worktree writable, temporary, or unverifiable | Refuse reuse and record writes; run live. |
| PASS is contaminated or has a resource warning | Reject as a source record; run live. |
| Lane is declared `never`, includes R2, is a sequence, reads any release-prep-only path, or reads live external state | Refuse reuse before record lookup; run live. |
| Coverage totals/artifacts incomplete | Refuse coverage reuse; run the coverage lane live. |

If evidence storage itself cannot be safely read, Run-Gate reports the error
and falls back to a live lane only when the lane can run without the store. It
never treats unreadable state as an empty PASS or a PASS.

## 6. Test and mutation plan for implementation

All tests run through Run-Gate's registered `selftest`/Assay lanes in
`tester-unified`; never from the cockpit. Tests use temporary Git roots,
fixture images, fake clocks, and injected Docker/runtime probes. They assert
outputs and records, not elapsed time, host pressure, sleeps, or load-sensitive
thresholds.

| Guard / behavior | Deterministic test oracle | Hand mutation to apply once |
|---|---|---|
| Canonical selected-tree digest | Same files with different enumeration order/mtime produce the same digest; changing any included test, fixture, vendored file, mode, or symlink target changes it. | Remove included file bytes or file mode from the manifest; digest sensitivity test must fail. |
| Closed `identity_components/1` schema | Round-trip the exact closed field set; changing image, runtime, or plugin values changes its group digest and top-level identity; missing or unknown fields are rejected. Independently vary project/central config, Assay source/schema/config, SCM version/context, Run-Gate payload, argv, effective env/passthrough, resources, vendor/wheel dependencies, build context, and resolved R1 base; every change misses. | Remove `tester_image_digest`, a runtime/plugin field, or a required group digest from validation; the matching schema/identity test must fail. |
| Empty release-prep read set | For an `exact` lane with a complete empty read set, changing only a declared prep-only file leaves `identity_sha256` unchanged and a previously valid PASS can hit. Invalid paths still refuse config. | Mark every lane with any project prep-only path `never`; the empty-reader hit test must fail. |
| Non-empty release-prep read set | A lane with any `release_prep_reads` entry runs live whether the listed bytes changed or not; an unknown/incomplete read set also runs live. Invalid paths refuse config. | Allow a non-empty prep reader to hit when unchanged; the live-reader test must fail. |
| Lane eligibility | Omitted policy defaults to never; only `exact` opts in; R2/live/host vetoes remain never. | Change a prohibited lane from never to exact or bypass the hard veto; the non-reuse test must fail. |
| Immutable tree and refs | The lane reads the immutable selected tree; snapshot-manifest and complete visible-ref digests are in identity and rechecked at promotion. Deterministically change a tracked file after identity collection, restore it before the mutable worktree's post-check, and assert the mutable direct invocation creates no candidate or PASS record. Also assert an eligible immutable-snapshot lane sees fixed bytes and refuses promotion if its manifest/ref digest changes. | Remove the immutable-view eligibility requirement or skip manifest/ref re-verification at promotion; the race or promotion test must fail. |
| Run-Gate self-validates before promotion | A valid staged candidate whose immutable snapshot, visible refs, clean tree, and output contract pass promotes and then hits. Conversely, simulate `finish_lane` rejecting invalid output, attempt nonce-only promotion with the candidate's public fields and a forged JSON success receipt, and assert the removed `--receipt-stdin` interface is refused, no PASS record is written, and lookup misses. | Accept a caller's success string or skip Run-Gate's output/snapshot validator; the valid/forged/rejected-output tests must fail. |
| One-use promotion | A successful promotion consumes the candidate. Replaying the old `run-gate-consumer-finalization/1` receipt-shaped payload through `--receipt-stdin` is refused; on a fresh target candidate the store remains empty and lookup misses. A nonce-only replay cannot create or overwrite a second PASS record. | Accept an already-consumed candidate or permit record overwrite; the replay tests must fail. |
| Cross-invocation promotion binding | Submit invocation A's receipt-shaped JSON to invocation B's promotion request, with B using a different tree/ref identity. The removed receipt interface is refused, B's candidate remains staged, and lookup misses. | Accept cross-invocation receipt fields or skip candidate identity/snapshot/ref comparison; the test must fail. |
| Persistent runtime eligibility | A persistent runner keeps the same image ID while its installed pytest or plugin changes; lookup is vetoed and the lane runs live. All `exec`, `host`, and `bare-host` environments are `never` in v1. | Remove the persistent-environment veto; the same-image/different-runtime test must fail. |
| Clean-tree candidate eligibility | A clean PASS with stable HEAD/tree and no Git operation may stage a candidate; dirty start/end, changed HEAD/tree, in-flight Git operation, FAIL, selective run, abort, or unknown cleanliness stages none. | Remove one pre/post clean or head/tree check; the corresponding refusal test must fail. |
| Protected evidence and record integrity | Fake roots distinguish protected external store from worktree/agent-writable/default `/tmp`; tamper, truncate, self-hash mismatch, symlink, or overwrite is rejected. | Remove owner/mode/root validation or self-hash validation; its test must fail. |
| Freshness and version binding | Injected clock checks just-before/at-expiry; version/payload/schema mismatch and missing fields miss. | Remove expiry or version comparison; deterministic boundary test must fail. |
| Multiple record selection | Records are scanned by descending valid `completed_at`, then ascending run-id; malformed records are skipped with a reason and later valid records remain eligible. | Stop at the first malformed/expired file or change ordering; the scanner test must fail. |
| Resource contamination | A PASS with any warning/contamination flag produces no reusable record and cannot be read as a hit. | Force contaminated status to clean; contamination test must fail. |
| Surfacing and CMRU request | Mock `run-gate` receives the opt-in on each CMRU leaf call; only `exact` lane declarations can hit, and hit output names record and digest in the inherited release-gate log. Misses say they ran live. | Drop the CMRU flag or suppress either evidence field; consumer test must fail. |
| Complete coverage floor | Complete line+branch totals, zero exclusions, all files and artifact hashes can hit; missing totals/branches/exclusions/artifacts or changed inventory must miss. | Remove one completeness condition at a time; corresponding fixture test must fail. |
| No cross-lane substitution | Same source with different lane name/argv is a miss; an Assay PASS cannot satisfy `coverage` or `canary`. | Remove lane/argv from identity or allow an Assay record as coverage; cross-lane test must fail. |

Every future implementation guard above gets one manual hand mutation and its
test must kill it. Any survivor gets a new deterministic test before the gate
can be considered complete. This design-only change adds no executable runtime
guards; its review-contract mutations and their scope are recorded in the
round's external report.

## 7. Rollout order and measured savings

All current timing figures come from the investigation and are bounded as
observed: total `gate-provisional` was **2,989.8 s**; per-lane intervals are
inferred from container starts and child-log completion times. They are gross
upper bounds, not v1 savings: the current CMRU `assay` and `coverage` full-suite
selections both include the test that reads `cmru/CHANGES.md`, so both remain
live until a later design creates a complete read-free selection. Under the
current CMRU lane layout, v1 saves **0 s** from verdict reuse.

| Step | Scope | Expected saving on an exact repeat |
|---|---|---:|
| 1 — smallest | Add canonical digest, protected immutable PASS record, expiry, refusal rules, Run-Gate-owned promotion after its own snapshot/ref/output validation, and one exact-lane lookup. Do not seed a CMRU lane whose complete test selection reads a prep-only path. Do not backfill history or add a consumer receipt API. | **0 s** for the current CMRU lane layout. The measured Assay interval of about **1,249 s** is a gross upper bound only after a future read-free split; the historical 1,236.912 s Assay run is not reusable because of its warning and missing producer identity. |
| 2 | Have CMRU request exact reuse only for a future complete read-free lane selection; keep the current full-suite `assay` and `coverage`, `ref-probe`, `installed-wheel-live`, R2, and the independent canary live. Require complete coverage evidence before any coverage hit. | **0 s** until the later split. If it preserves the same work and evidence contracts, the historical coverage interval of about **751 s** and Assay interval of about **1,249 s** bound the potential; the **741.17 s** pytest number is inside the inferred coverage interval, not additive. |
| 3 | In a later design, isolate known prep readers in a dedicated live lane and prove that each exactable selection has an empty prep read set. | No separately measured saving. A lane that reads `CHANGES.md` is never a hit in v1 merely because its bytes are omitted from the tree digest. |

The separate CMRU canary remains about **919 s** live in this first cut. The
unsupported **2,919 s / 97.6%** figure is not a v1 saving: it drops a distinct
coverage-floor rejection check and assumes current prep-reading lanes can be
reused. The remaining observed time is about 2 s for
`ref-probe`, 10.5 s for `installed-wheel-live`, and a ~58.3 s inferred
preparation/wrapper residual. The four unmodified full-suite controls were
Assay R1 baseline, Assay R3 control, coverage, and canary control; the two
canaries also run transformed commands, so the gate issued six pytest
invocations in total. The measured 741.17 s coverage pytest is already inside
the inferred ~751 s lane interval.

Do not put the prebuilt Assay image proposal back into the order: same-repo
consumers install Assay editable from the selected worktree at lane time.
Engine-wheel caching has no supported duration estimate; the observed 5.5 s
target wheel build is not the missing bootstrap-engine build interval.
Parallel-lane work and library test splitting have no measured saving and are
deferred.

## 8. Implementation file/function map

Line references below describe the current checkout and are orientation for a
future implementation; no code changes are part of this design.

| File / current location | Change in implementation |
|---|---|
| `run-gate-project/run_gate.py:5184-5210` (`head_commit`, `worktree_is_dirty`); `:5374-5385` (`head_tree`); `:5213-5371` (`start_run_record`, `finish_run_record`); `:6278-6350` (`_record_invocation`, `record_invocation`, `flush_run_record`) | Add identity collection, immutable snapshot input, staged candidate/promotion protocol, trusted PASS-record reader/writer, and invocation reuse evidence. The promotion path calls the shared read-only CMRU snapshot/output validator and rechecks cleanliness. Pre/post clean and unchanged-tree checks remain necessary but are not proof that a mutable lane view stayed fixed. Do not upgrade `_apply_record` history into verdict authority. |
| `run-gate-project/run_gate.py:18494-18516` (CLI arguments) and lane dispatch near `:18988-19075` | Add `--reuse-verdict` separately from Assay's existing `--reuse-from`, plus a candidate-nonce-only promotion request; resolve policy before lookup and report hit, miss, or `never` in the lane result. No receipt JSON is accepted. |
| `run-gate-project/run_gate.py:543-544`, `:7819-7827`, `:7877-7889` (`evidence_dir`, `lane_evidence_directory`, `allocate_lane_log`) | Reuse the existing evidence-root setting and lane layout; require a protected persistent root for PASS records. Current `/tmp/run-gate` default is insufficient evidence of durability/trust. |
| `run-gate-project/run_gate.py:1930-2130` (resource resolution); `:2560-2644` (environment resolution); `:2646-2675` (image selection/actual image inspection); `:7707-7755` (lane argv/artifact disclosure); `:9524-9600` (Assay source identity); `:10140-10240` (comparison-base resolution) | Collect the effective digest inputs at their existing resolution points; fail closed when a required runtime or base identity is unavailable. |
| `run-gate-project/run_gate_admission.py:191-215`, `:979-1010`, `:1142-1191` | No verdict-cache changes. This module owns Docker-backed admission tickets and release, not lane PASS history; do not add cache policy here. |
| `cmru/tools/run_release_gate.py:1233-1266` (`_invoke_lane`); `:1269-1323` (`_run_snapshot_lane`); `:1514-1555` (`_run_locked_gate`) | Append the reuse request to each leaf invocation; after the post-lane identity recheck, `finish_lane`, and manifest update, request promotion by candidate nonce only. Run-Gate independently validates before promotion. Preserve serial order; the inherited lane output remains release-gate evidence. |
| `cmru/src/cmru/gate_snapshot.py:492-559` (protected and anchor ref digests); `:668-695` (`open_gate_snapshot`); `:1065-1159` (`validate_lane_outputs`, `prepare_lane`, `finish_lane`); `:1200-1225` (snapshot manifest) | Extend and share the read-only `validate_lane_outputs` implementation with Run-Gate's promotion path. Provide an immutable source view for eligible lanes, a stable manifest identity projection, and the complete visible-ref digest; recheck them before promotion. Current pre/post validation alone does not prevent a transient change-and-restore race. |
| `run-gate-project/run-gate.toml:59-73`, `:99-176`; `run-gate-project/assay.toml:38-86` | Add parser/selftest coverage for config defaults and effective digest collection; the existing selftest is the project's registered R0 lane. |
| `cmru/run-gate.toml:99-158`, `:244-305`, `:330-350`; `cmru/assay.toml:29-80` | Declare exact/never policy per CMRU leaf lane, make every non-empty prep-path reader `never`, declare coverage artifacts/totals, and retain live canary/R2 probes. |
| `run-gate-project/tests/test_run_gate.py` and CMRU release-gate tests | Implement the deterministic guard tests and hand mutations from section 6 using fake roots and injected external probes; assert CMRU appends `--reuse-verdict` without changing its serial lane order. |
| `run-gate-project/README.md`, `run-gate-project/docs/DESIGN-GUIDE.md`, `run-gate-project/CONSUMERS.md`; `cmru/README.md`, `cmru/docs/DESIGN-GUIDE.md`, `cmru/docs/CONSUMERS.md` | When the capability ships, update WHAT/WHY/HOW in the same implementation change: feature surface, rationale and rejected alternatives, and a pasteable consumer example. |
| `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` | Track this design as RG-253 until implementation and required docs land. |

## Verification boundary

Verified in the current checkout: Run-Gate's history captures commit, lane,
config hashes, selected resource/image data and outcome, but de-duplicates on
commit + I/O sync + worker count and writes best-effort telemetry; its evidence
directory currently defaults to `/tmp/run-gate`; admission is a separate
Docker ticket mechanism; CMRU's release helper invokes lanes serially and
prepares/resets the gate snapshot before each lane; and the CMRU config keeps
the live probes, coverage lane, mutation lane, and canary separate.

Not verified in the current checkout: CMRU's snapshot is an ordinary writable
linked worktree. `prepare_lane` and `finish_lane` validate and clean it, but do
not prove that no tracked byte changed and was restored while the lane read it.
The linked worktree also shares Git refs with its common-dir; existing checks
cover protected refs and release anchors, not a frozen copy of every ref
visible to the lane. V1 reuse stays disabled until both source bytes and the
visible ref view are immutable for the whole lane. The current snapshot
manifest also has volatile refresh/result fields; the implementation must hash
the stable identity projection and separately hash every Git ref visible to
the lane. A protected, durable Run-Gate evidence root owned by a trusted writer
identity is also not provisioned by these files. Those trust boundaries are
prerequisites, not properties a JSON self-hash can create. The investigation
also did not retain the exact Assay baseline/control phase split or
bootstrap-engine build duration; no estimate for those is claimed here.
