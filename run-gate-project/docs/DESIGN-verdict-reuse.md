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
| PASS authority | Only Run-Gate writes records, after a clean PASS. A consumer accepts only an exact identity-digest match. Worktree files and agent-writable records are never trusted. | Operator decision 1 |
| Tree scope | Hash the complete selected test tree. A narrower closure is allowed only after it is proven to contain every test, fixture, vendored source, and file read by the lane. The first cut uses the complete selection. | Design default for review decision 3; safer and simpler to audit |
| Coverage floor | A retained verdict may satisfy the whole-project floor only when it carries complete line and branch totals, no exclusions, and the required evidence artifacts. The floor remains an independent full-project check, never Assay's changed-line result. | Operator decision 2 |
| Release-prep paths | Projects explicitly list these paths. They are removed from the tree digest. A lane that reads one of them must run live when its content changed since the witnessed PASS; no read-set declaration means no reuse. | Operator decision 3, interpreted as a live check on changed prep input |
| Separate canary | Keep the coverage-floor canary as a separate live check in v1. Assay's import-break R3 result cannot stand in for the uncovered-line canary. | Design default for review decision 6; the checks reject different failures |
| Live inputs | `ref-probe`, `installed-wheel-live`, every R2 lane, and every lane that reads network, wall-clock, mutable refs, or current host facts are `never`. A config opt-in cannot override these hard vetoes. | Operator decision 1 and review correction |
| Resource warnings | A PASS with a resource-contamination or resource-warning marker is ineligible. | Design default for review decision 8 |
| Expiry | Records expire 30 days after `completed_at`; this is a bounded design default, not an operator-set retention period. An expired record is a miss. | Design default for review decision 7 |

The retained `4a103229d` Assay PASS is **not** a seed record: its history has a
6.3-second resource-contamination warning and the producer Run-Gate payload
identity is absent. It also lacks the complete runtime identity required here.
Run-Gate must not backfill it. Although `4a103229d` and `8cd845b57` have the
same Git tree, review found their effective `setuptools_scm` versions differ;
that makes their identity digests differ. The savings below are prospective
for a later exact repeat whose complete identity is unchanged.

## 1. Per-lane identity digest

### Canonical form

Before a reusable lane starts, Run-Gate builds a JSON object with schema
`run-gate-lane-identity/1`, serializes it as UTF-8 canonical JSON (sorted keys,
compact separators, no NaN), and computes:

```text
sha256("run-gate-lane-identity/1\0" || canonical_json(identity))
```

Arrays whose order is meaningful (argv, path lists, plugin load order) retain
that order. Sets (for example plugin distribution identities) are sorted by
their stable name. Unknown, unavailable, or malformed required input means no
identity and therefore no reuse. Run-Gate does not invent a placeholder for a
fact it could not read.

The identity object stores the exact argv as its canonical SHA-256 and the
exact effective environment as a canonical SHA-256 over sorted name/value
pairs. Their raw values stay in memory only; no argv/env secret is written to
evidence. Other groups use their named fields or component digest as shown in
the table. The record retains the complete non-secret `identity_components`
object so a reader can reject a missing component and recompute the top-level
identity digest.

The digest uses the exact selected lane tree as presented to the test process.
Hash a sorted manifest of every included path, its Git mode/type, and the
SHA-256 of its bytes (or symlink target/gitlink commit); include tests,
fixtures, generated vendored packages, and the full contents of included
libraries. Include the complete snapshot selection policy and omissions. The
manifest is content based, not mtime based. If Run-Gate cannot prove that the
hashed selection is the tree visible to the lane, or the lane can read
untracked/ignored or external files outside that selection, the lane is
`never` until those inputs are declared and captured.

The identity object contains exactly these input groups:

| Input group | Required contents |
|---|---|
| Project identity and tree | `repo_id = SHA256(realpath(git rev-parse --path-format=absolute --git-common-dir))`; project path relative to repository root; selected-tree manifest digest; lane name and kind. Do not persist an origin URL or credentials. If Git common-dir cannot be resolved, miss. The identity digest does not require producer and consumer HEAD commit IDs to match, so a same-tree result can be considered, but all SCM-derived values below must still match. |
| Project and central configuration | SHA-256 of the exact selected `run-gate.toml` and resolved central config bytes, plus the merged effective lane policy. Include the project-declared release-prep path list and lane read declarations. |
| Assay | Assay source-tree digest (including vendored `cowtree` where packaged), installed/effective Assay version, schema digest/version, and exact effective `assay.toml` lane configuration. For same-repository consumers the source is the selected worktree's editable Assay source, as required by `assay/docs/INTERNAL-CONSUMERS.md`. |
| SCM-derived package versions | Exact effective versions for the project and every SCM-derived package used by the lane (including vendored libraries and wheel dependencies built from source), plus the `setuptools_scm` context that produced each value: selected tag, distance, commit, dirty state, config, and any pretend-version override. A missing or unresolved version is a miss. |
| Run-Gate payload | Installed Run-Gate SemVer and SHA-256 over the exact installed payload files that can affect execution (CLI module, admission/profile modules, schemas, and package data). Version alone is insufficient. |
| Lane command | Exact lane argv after Run-Gate substitutions and exact caller-selected lane arguments, including workers/jobs, comparison-base request, and other behavior-changing flags. Hash shell strings byte-for-byte. The resolved R1 base commit is a separate field. No unrecognized argv normalization is allowed. |
| Effective environment | Sorted name/value mapping of every variable delivered to the lane, including configured variables and all passthroughs after resolution, plus environment/image defaults that the lane can observe. Hash values in memory; persist only the resulting environment digest, never raw values or secrets. An unreadable passthrough is a miss. |
| Resources and isolation | Resolved CPUs, memory, swap, pids, I/O sync and CoW policy, worker/job limits, network/isolation settings, and all resource overrides. Include the resolved cgroup parent and host-provided resource facts if the lane is eligible to use them; in practice a result that depends on changing host facts is `never`. |
| Tester and runtime | Immutable tester image content digest actually used, not a tag; Python implementation/version/ABI; pytest and coverage versions; and every enabled pytest/plugin identity (distribution name/version and loaded entry point/module). Missing image or runtime identity is a miss. |
| R1 comparison | The resolved commit SHA used as the R1 comparison base. Use an explicit `not-applicable` value only for a lane whose declared rigor has no R1 judgment. A tag/ref string without its resolved commit is insufficient. |
| Build context | For any lane that builds a wheel: `SOURCE_DATE_EPOCH`, effective `setuptools_scm` context, and immutable builder image/toolchain digest. Use `not-applicable` only when the lane does not build a wheel. |

The selected-tree manifest includes test files, fixture contents, and included
vendored libraries rather than only production source. For CMRU, the source
closure includes `libraries/worktree`; Assay's package closure includes
`libraries/cowtree`; and any source-built cli-extended wheel is an input. A
released wheel dependency is identified by its exact installed wheel digest
and metadata. This prevents a project-subtree-only key from silently missing a
vendored or wheel-build input.

On the first live PASS, collect Python/pytest/coverage/plugin identities inside
the actual tester container and store them with the record. Before a hit,
resolve the configured image to its immutable local image ID and compare it to
the record; use the record's runtime identities only after the image digest,
selected sources, argv, and effective environment that determine them all
match. The live run also compares the started container's actual image ID to
the preflight ID before writing a PASS. If any runtime package can change from
a mutable mount, network install, or unbound path, that lane is `never`.

### Per-lane eligibility declaration

Add one closed-valued lane key:

```toml
verdict_reuse = "exact" # explicit opt-in
# or
verdict_reuse = "never" # explicit live lane
```

An omitted key means `never`. `exact` is an assertion by the lane owner that
all behavior-changing inputs are captured by the identity above. Run-Gate
hard-refuses reuse for R2, host/exec probes, live snapshot/ref checks, and
other known live-input lane kinds even if a config says `exact`. Every
network-, time-, host-, remote-tag-, or mutable-state-dependent lane must say
`never`; its config comment records why. No runtime heuristic infers that a
lane is deterministic.

| Lane or lane class | v1 decision | Reason |
|---|---|---|
| CMRU `ref-probe` | Never | It tests current protected-ref visibility and mutates the live gate snapshot. |
| CMRU `installed-wheel-live` | Never | It proves the current run's fresh snapshot manifest and live-snapshot boundary. |
| Any R2/mutation lane | Never | It resolves published tags and current remote baseline facts. |
| Any network/time/current-host lane | Never | Those facts are not frozen by a source tree digest. |
| CMRU `assay` (R0/R1/R3) | Exact, after all inputs are captured | Its own complete PASS record may be reused; it does not replace the separate coverage canary. |
| CMRU `coverage` | Exact, only with complete coverage evidence | Requires complete totals and retained artifact contract below. |
| CMRU `canary` (uncovered-line canary) | Never in v1 | It remains a separate release-time rejection check; Assay R3 does not substitute for it. |
| Composite release lane (`gate`, `gate-provisional`) | Never | The consumer decides leaf-lane order and must keep live lanes live. |

These CMRU examples are the initial rollout table, not a global assumption
that all Assay lanes are reusable. The `installed-wheel` build-smoke lane may
opt in only after its wheel build context is complete; the live variant above
never opts in.

## 2. Release-prep-only paths

Add project-scoped configuration to the selected `run-gate.toml`:

```toml
[project.verdict_reuse]
release_prep_only_paths = ["CHANGES.md"] # add exact version paths when used

[lanes.assay]
verdict_reuse = "exact"
release_prep_reads = ["CHANGES.md"]
```

Paths are exact, tracked, project-relative files. Reject absolute paths,
parent traversal, symlink escapes, globs, directories, and a path outside the
selected project. The path list and read declarations are configuration
inputs; the listed file contents are omitted from the selected-tree digest.

Store a separate SHA-256 witness for each declared prep-only path in the PASS
record. The witness is not an identity-digest component. During lookup, if a
lane declares that it reads a prep path and the current bytes differ from the
record's witness, refuse reuse and run that lane live. On a live PASS, write
the new witness. If a lane may read a prep path but has no complete
`release_prep_reads` declaration, treat it as `never`. This keeps a
`CHANGES.md`-only prepare edit from invalidating unrelated tests while still
rerunning tests that read it.

For the CMRU example, `cmru/tests/test_w3_prep_changelog_preflight.py` reads
`cmru/CHANGES.md`; therefore an edit to that path forces every lane containing
that check to run live. The later implementation may split out a registered
narrow changelog check, but the first cut does not guess which tests read which
files and does not skip that check.

## 3. Durable PASS record and trust boundary

### Record format and location

Write one immutable JSON record for each eligible PASS at:

```text
<RUN_GATE_EVIDENCE_DIR>/projects/<project-key>/lanes/<lane>/passes/<identity-sha256>/<run-id>.json
```

`RUN_GATE_EVIDENCE_DIR` already selects the evidence root, and Run-Gate
already groups lane logs under `lanes/<lane>/`. The record is separate from
`.run-gate/history.json` and from the per-run log. The project key is a stable
hash of the repository identity plus the project path relative to repository
root, so different projects cannot share a record by lane name alone.

Schema `run-gate-pass/1` requires at least:

```json
{
  "schema": "run-gate-pass/1",
  "lane": "coverage",
  "lane_kind": "command",
  "verdict": "PASS",
  "identity_sha256": "<64 lowercase hex>",
  "run_gate_version": "<installed SemVer>",
  "run_gate_payload_sha256": "<64 lowercase hex>",
  "tester_image_digest": "sha256:<64 lowercase hex>",
  "runtime_identity": {"python": {}, "pytest": {}, "coverage": {}, "plugins": []},
  "identity_components": {
    "repo_id": "<sha256>",
    "project_path": "cmru",
    "project_config_sha256": "<sha256>",
    "central_config_sha256": "<sha256-or-not-applicable>",
    "selected_tree_sha256": "<sha256>",
    "assay_source_sha256": "<sha256-or-not-applicable>",
    "assay_version": "<effective version-or-not-applicable>",
    "assay_schema_sha256": "<sha256-or-not-applicable>",
    "assay_config_sha256": "<sha256-or-not-applicable>",
    "effective_scm_sha256": "<sha256>",
    "argv_sha256": "<sha256>",
    "environment_sha256": "<sha256>",
    "resource_settings_sha256": "<sha256>",
    "r1_base_commit": "<full SHA-or-not-applicable>",
    "build_context_sha256": "<sha256-or-not-applicable>"
  },
  "producer_head": "<full Git commit SHA>",
  "producer_head_tree": "<full Git tree SHA>",
  "selected_tree_sha256": "<64 lowercase hex>",
  "completed_at": "<UTC RFC 3339>",
  "expires_at": "<UTC RFC 3339>",
  "resource_status": "clean",
  "release_prep_witnesses": {"CHANGES.md": "<sha256>"},
  "evidence": {},
  "record_sha256": "<sha256 of canonical record without this field>"
}
```

`evidence` contains hashes and paths for the exact Run-Gate/Assay closeout,
coverage totals, and required artifact copies; it never contains raw secrets.
The record binds the PASS to producer HEAD and tree even though the identity
digest is content-based and may be equal across commits. The record's
self-hash detects truncation or accidental edits; it is not an authenticity
signature.

For a coverage-bearing PASS, `evidence.coverage` records the sorted complete
source-file inventory, coverage format/version, `statements`, `covered_lines`,
`missing_lines`, `excluded_lines`, `branches`, `covered_branches`,
`missing_branches`, and `excluded_branches`, plus SHA-256 and retained paths
for each declared coverage/JUnit artifact. Unknown or omitted counters are
not zero; they make the proof incomplete.

### Who may write and what counts as durable

Only Run-Gate writes these records, after the lane has returned PASS and
Run-Gate has rechecked a clean tree, unchanged HEAD/tree, and no in-flight Git
operation both before and after the lane. Any failure to determine cleanliness
or identity blocks record creation. No consumer, agent, test, or lane command
may manufacture a PASS record. A failed, skipped, aborted, selective, or
infrastructure-error run writes no reusable PASS.

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

Run-Gate creates records atomically with restrictive permissions and no
symlink following. It never reads legacy history entries as records, never
backfills the retained `4a103` history, and never trusts a record copied into
the worktree. If a record already exists at a run-id path, it refuses the
write rather than overwriting it.

### Freshness and contamination

A record is fresh only when all of the following hold: current UTC time is
before `expires_at`; its schema, Run-Gate version, and Run-Gate payload digest
match exactly; its self-hash verifies; the record is in the protected evidence
root; the tree and prep-path witness checks pass; and every identity component
is present and valid. There is no schema migration or compatibility fallback.
The 30-day expiry is fixed at record creation and is not extended by a cache
hit.

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
checks every time.

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
| Worktree dirty, HEAD/tree changed, or Git operation active | Refuse reuse; do not write a PASS record. Run live only if the lane's ordinary preflight permits it. |
| Record expired, self-hash invalid, or selected-tree/prep witness mismatch | Reject; run live. |
| Evidence root is agent/worktree writable, temporary, or unverifiable | Refuse reuse and record writes; run live. |
| PASS is contaminated or has a resource warning | Reject as a source record; run live. |
| Lane is declared `never`, includes R2, is a sequence, or reads live external state | Refuse reuse before record lookup; run live. |
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
| Required identity components | Independently change project/central config, Assay source/schema/config, SCM version/context, Run-Gate payload, argv, effective env/passthrough, resources, image digest, Python/pytest/coverage/plugin identity, and resolved R1 base; every change misses. Unknown component refuses identity. | Drop each component once in turn; the matching invalidation test must kill the mutant. |
| Release-prep exclusions | Declared path bytes do not alter `identity_sha256`; undeclared file changes do. A lane with a declared prep read gets a live miss when its witness changes and may hit when unchanged. Invalid paths refuse config. | Hash an excluded path or ignore a changed read witness; the exclusion/live-reader test must fail. |
| Lane eligibility | Omitted policy defaults to never; only `exact` opts in; R2/live/host vetoes remain never. | Change a prohibited lane from never to exact or bypass the hard veto; the non-reuse test must fail. |
| Clean-tree PASS writer | Clean PASS with stable HEAD/tree writes one record; dirty start/end, changed HEAD/tree, in-flight Git op, FAIL, selective run, abort, or unknown cleanliness writes none. | Remove one pre/post clean or head/tree check; the corresponding refusal test must fail. |
| Protected evidence and record integrity | Fake roots distinguish protected external store from worktree/agent-writable/default `/tmp`; tamper, truncate, self-hash mismatch, symlink, or overwrite is rejected. | Remove owner/mode/root validation or self-hash validation; its test must fail. |
| Freshness and version binding | Injected clock checks just-before/at-expiry; version/payload/schema mismatch and missing fields miss. | Remove expiry or version comparison; deterministic boundary test must fail. |
| Resource contamination | A PASS with any warning/contamination flag produces no reusable record and cannot be read as a hit. | Force contaminated status to clean; contamination test must fail. |
| Surfacing and CMRU request | Mock `run-gate` receives the opt-in on each CMRU leaf call; only `exact` lane declarations can hit, and hit output names record and digest in the inherited release-gate log. Misses say they ran live. | Drop the CMRU flag or suppress either evidence field; consumer test must fail. |
| Complete coverage floor | Complete line+branch totals, zero exclusions, all files and artifact hashes can hit; missing totals/branches/exclusions/artifacts or changed inventory must miss. | Remove one completeness condition at a time; corresponding fixture test must fail. |
| No cross-lane substitution | Same source with different lane name/argv is a miss; an Assay PASS cannot satisfy `coverage` or `canary`. | Remove lane/argv from identity or allow an Assay record as coverage; cross-lane test must fail. |

Every future implementation guard above gets one manual hand mutation and its
test must kill it. Any survivor gets a new deterministic test before the gate
can be considered complete. This design-only change adds no executable guards,
so it has no implementation mutants to run.

## 7. Rollout order and measured savings

All current timing figures come from the investigation and are bounded as
observed: total `gate-provisional` was **2,989.8 s**; per-lane intervals are
inferred from container starts and child-log completion times.

| Step | Scope | Expected saving on an exact repeat |
|---|---|---:|
| 1 — smallest | Add canonical digest, protected immutable PASS record, expiry, refusal rules, and one exact-lane lookup. Seed one clean exact `assay` PASS; do not backfill history. | The first run saves **0 s** because it seeds the record. A later exact `assay` hit can save about **1,249 s** (**41.8%** of 2,989.8 s). The historical 1,236.912 s Assay run is consistent with that estimate but is not reusable because of its warning and missing producer identity. |
| 2 | Have CMRU request exact reuse for eligible `assay` and `coverage` lanes; keep `ref-probe`, `installed-wheel-live`, R2, and the independent canary live. Require complete coverage evidence before a coverage hit. | A retained exact `coverage` hit adds about **751 s**; together the two eligible hits save about **2,000 s / 66.9%**. The **741.17 s** pytest number is inside the inferred coverage interval, not additive. |
| 3 | Declare project release-prep paths and their lane readers; keep changed-path readers live. Add a narrow changelog check only if later evidence shows it safely replaces those live reads. | No separately measured saving. A changed `CHANGES.md` must not be called a hit merely because its bytes are omitted from the tree digest. |

The separate CMRU canary remains about **919 s** live in this first cut. Do not
claim the unsupported **2,919 s / 97.6%** saving: that would drop a distinct
coverage-floor rejection check. The remaining observed time is about 2 s for
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
| `run-gate-project/run_gate.py:5184-5210` (`head_commit`, `head_tree`, `worktree_is_dirty`); `:5213-5371` (`start_run_record`, `finish_run_record`); `:6278-6350` (`_record_invocation`, `record_invocation`, `flush_run_record`) | Add identity collection, pre/post clean and unchanged-tree validation, trusted PASS-record reader/writer, and invocation reuse evidence. Do not upgrade `_apply_record` history into verdict authority. |
| `run-gate-project/run_gate.py:18494-18516` (CLI arguments) and lane dispatch near `:18988-19075` | Add `--reuse-verdict` separately from Assay's existing `--reuse-from`; resolve the policy before lookup and report hit, miss, or `never` in the lane result. |
| `run-gate-project/run_gate.py:543-544`, `:7819-7827`, `:7877-7889` (`evidence_dir`, `lane_evidence_directory`, `allocate_lane_log`) | Reuse the existing evidence-root setting and lane layout; require a protected persistent root for PASS records. Current `/tmp/run-gate` default is insufficient evidence of durability/trust. |
| `run-gate-project/run_gate.py:1930-2130` (resource resolution); `:2560-2644` (environment resolution); `:2646-2675` (image selection/actual image inspection); `:7707-7755` (lane argv/artifact disclosure); `:9524-9600` (Assay source identity); `:10140-10240` (comparison-base resolution) | Collect the effective digest inputs at their existing resolution points; fail closed when a required runtime or base identity is unavailable. |
| `run-gate-project/run_gate_admission.py:191-215`, `:979-1010`, `:1142-1191` | No verdict-cache changes. This module owns Docker-backed admission tickets and release, not lane PASS history; do not add cache policy here. |
| `cmru/tools/run_release_gate.py:1233-1266` (`_invoke_lane`); `:1269-1323` (`_run_snapshot_lane`); `:1514-1555` (`_run_locked_gate`) | Append the reuse request to each leaf invocation, preserve serial order, and let Run-Gate's selected lane policy retain live checks; the inherited lane output becomes release-gate log evidence. |
| `cmru/src/cmru/gate_snapshot.py:668-695` (`open_gate_snapshot`); `:1129-1159` (`prepare_lane`, `finish_lane`) | No behavior change is required: CMRU already opens a stable snapshot for the source HEAD and resets/cleans it before each lane. Bind Run-Gate's lookup to the selected tree and never use the mutable snapshot manifest as PASS authority. |
| `run-gate-project/run-gate.toml:59-73`, `:99-176`; `run-gate-project/assay.toml:38-86` | Add parser/selftest coverage for config defaults and effective digest collection; the existing selftest is the project's registered R0 lane. |
| `cmru/run-gate.toml:99-158`, `:244-305`, `:330-350`; `cmru/assay.toml:29-80` | Declare exact/never policy per CMRU leaf lane, prep-path readers, coverage artifacts/totals, and the retained live canary/R2 probes. |
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

Not verified in the current checkout: a protected, durable Run-Gate evidence
root owned by a trusted writer identity is not provisioned by these files.
That deployment trust boundary is a prerequisite, not something a JSON
self-hash can create. The investigation also did not retain the exact Assay
baseline/control phase split or bootstrap-engine build duration; no estimate
for those is claimed here.
