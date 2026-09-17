# Reusable review evidence creation and consumption

This follow-up implements the workflow repeated during P4 and the RG49
addition to P5 as shipped `assay analyze` commands. It lives on
`review-evidence-tools-20260916`, based on main
`3693a9aea02aa746293ba28ed3b52f4bf6e6a014`. Initial implementation commit:
`fde9527ae053ab453bacbfd73c8342ec72512482`. JSON nesting, captured diagnostics
and the launcher marker boundary were repaired in
`b48e873d` and `d3bacd3166803f10200d53190c542aa351d21dcf`.
Receipt argv schema consistency and special-file refusal were completed in
`da0c3a584425d8cd2e0da78fc077a0f746d8152b`. Current main through
`1cdbde6de5d6f2541f33a021414cd99aa62589a3` was reconciled in
`5a21241267f3d0c8adfc4130d4cdb21ad185f32a` before final qualification.

The user authorized merging this follow-up to main. That authorization applies
to this tooling change. The RG49 candidate and its final REJECT review remain
frozen at `32a8f97c8ef6fe0c602288e112f2e77ece779d1e`; the outstanding B9 provider
resume-record crash is not repaired or silently closed by this change.
This follow-up performs no global dependency installs. A later read-only
inventory observed a concurrent CMRU change, disclosed below. No release, tag, push or publication
is part of this work.

## P4 and P5 inventory

Read the P4 round-7 review at
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round7.md`.
Its reviewed product commit was
`b2eb633e7ed2574d4752e6b3e406446bf03a8321`.
P4 created repository-owned `tester-unified/run`, with README, design and
consumer documentation and behavioral tests. It already handles workspace
namespace mounts, full run-user identity, host-backed temporary storage,
placement, launch admission, CPU acceptance, detach/wait/log transport and
exact owned cleanup. Its preserved run directories contain `launch.txt`,
`container.inspect.json`, `docker-wait.exit`, `container.log` and
`launch-memory-pressure.txt` under the P4 worktree's
`.assay/tester-unified-runs/`. Reuse this launcher and consume its records.

The inspected temporary P4 supervisors `/tmp/rg55-p4-deferred-r2.sh` and
`/tmp/rg55-p4-r2.sh` contain incident-specific worktrees, hashes, waits and
candidate switching. They are historical records, not reusable generic
analysis code. No separate generic Python collector/receipt helper was found
in the bounded P4 locations inspected; this is not a claim to have searched
all private conversations or every unrelated temporary tree.

P5's `/tmp/rg49-collect-evidence.py` and `/tmp/rg49-final-receipt.py` repeated
copying, hashing, Git binding, job-marker extraction, verdict loading and
progress segmentation. Their fixed findings, oracle counts, hashes, provider
reproduction and runtime disclosures remain in the frozen review record.
Reusable operations move into Assay without those candidate-specific facts.

## Implementation and contracts

- `record`: one explicit command, actual subprocess exit, merged raw log,
  before/after Git identities and cleanliness. It preserves binary output,
  signal statuses, dirty final state and diagnostic partial files.
- `receipt`: current expected clean HEAD/tree, named P5 prefix records or
  P4 launcher records, validated verdicts, segmented progress and fingerprints.
- `collect --receipt`: one file graph, including all fingerprinted inputs;
  changed bytes refuse. Explicit supplementary artifacts can be named too.
- `check`: portable archive SHA256/size and exact file membership, without
  reopening old source paths. Existing outputs never overwrite.
- `verdict`: the existing Assay verifier plus the expected commit; JSON or
  concise text, with recorded outcomes, line/branch counts and mutant buckets.
- `progress`: every matching appended run remains separate, with line
  references, event counts and explicit resume/candidate/terminal payloads.
- `launcher`: inspect historical P4 transport records without rebinding an
  old product HEAD to today's administrative checkout.

All commands ship through the existing wheel/zipapp CLI and share the same
stdlib implementation. Internal vbpub consumers normally install the selected
worktree's source at lane time; Assay's own gate builds and installs an
exact-OID wheel in temporary container environments. The wheel is not the
only CLI distribution, and the image does not bake this new source change.

Reuse Assay's sanitized, bounded Git boundary and dirty-path policy. Personal
excludes cannot hide dirty sources or authorize telemetry output; Git
environment/local worktree redirects cannot select a different checkout.
Duplicate JSON keys, non-JSON numeric literals, stale/partial records,
traversal/collisions, missing named artifacts and inconsistent wait/job exits
refuse. Archives and receipts have their own packaged v1 JSON Schemas;
verdict v11 and lane v2 remain unchanged. README, DESIGN-GUIDE, CONSUMERS,
changelog and decision A-448 ship together; tests parse their command examples,
cover the complete subcommand vocabulary, and check the new anchors.

`tester-unified/run --network none` enables reuse for the offline exact-OID
self-hosting phases. The launcher verifies and records Docker's accepted
network mode. Its only explicit network value is `none`; omission preserves
existing Docker selection. All three launcher documents and behavioral tests
are updated. The Assay gate driver and its phase sequence remain unchanged.

## Validation records

Authoritative container records are preserved in this follow-up worktree's
`.assay/evidence-analysis/`. The full gate uses the committed exact-OID wheel,
not a source/PYTHONPATH shortcut. It is Assay's existing gate inner phase
sequence launched by the canonical P4 launcher with an offline container,
uid 1003, configured host cgroup, launch/update/inspect 3-CPU cap, low priority
and PSI admission. This replaces the legacy outer transport for this run;
it is not falsely reported as the unchanged registered outer argv.

Focused tests exposed and repaired command-separator integration, unsupported
Git literal-pathspec handling for check-ignore, and an omitted stdlib import.
One development attempt changed the running launcher shell while it was
waiting; its wrapper failed. Recovered Docker wait/log records independently
show the real job failed (10 failed, 160 passed). It was not counted as PASS.
Each authoritative run uses a committed source identity. The first full run
was frozen at fde9527a; the repaired source is frozen again before requalification.
All admission refusals above memory PSI `full avg10=5` are infrastructure
deferrals, never functional PASS/FAIL results.

The first full gate at `fde9527a` failed: 1 failed, 4818 passed, 13 skipped
in 635.43s. The one failure was the permanent untrusted-JSON AST sweep:
`analysis._json` needed a local `RecursionError` guard. The CLI boundary already
caught that exception; the shared parser now translates it locally into a
clear refusal, with deep-nesting regression tests across five analysis inputs.
The failure was already available in verdict `result_stdout_tail`; only the
extra diagnostic replay was terminated after the completed failure was known.
No green result is claimed. That terminated replay also exposed a launcher
transport bug: progress dots without a newline joined the job marker. The
launcher now starts the marker on a new line. Behavioral tests execute its real
job wrapper with zero/nonzero jobs and unterminated stdout.

`verdict --format text` now exposes recorded failure tails and existing
nonzero dropped-byte counts without inventing absent capture fields. Assay's
gate consumes that view rather than replaying the whole failed suite. Red gate
status is preserved whether inspection succeeds or fails. Dedicated behavioral
oracles assert no replay and no success phase marker. Recording and receipt
inspection also preserve legitimate empty argv arguments. The packaged receipt
schema permits empty arguments after the nonempty executable. Special-file
inputs such as FIFOs refuse before opening; bounded CLI probes guard against
waiting indefinitely for a writer.

An attempted focused gate was deferred by the launcher's two-container estate
capacity limit. Existing foreign tester containers were left alone. Final
validation outcomes below are filled only after execution. The queue supervisor
is `/tmp/assay-analysis-qualification-da0c3a58.py`; its preserved admission
stream records every deferral and the pressure/capacity state that allows launch.

## Current status: BLOCKED before final qualification and merge

Repaired production source HEAD: `da0c3a584425d8cd2e0da78fc077a0f746d8152b`.
Main was clean at `1cdbde6de5d6f2541f33a021414cd99aa62589a3` when checked.
No main merge of this follow-up has occurred. The permanent focused/full gates
must pass at the repaired source before a merge is accepted.

Both estate tester slots are occupied by foreign containers
`vbpub-mdt-release-20260917` and `vbpub-mdt-release-20260917-v2`.
Read-only `docker top ... -eo pid,comm,etime` showed only docker-init and
sleep in both; they were left alone. The canonical launcher refused the first
attempt with exit 2 and `2 'tester-unified:local' containers are already
running; estate cap is 2`. A subsequent supervisor queued the correct commands
while recording capacity/PSI every 30 seconds. It was stopped while still
waiting; there is no unattended gate scheduled to launch after this report.
The owner must release a test slot or identify an approved route. No capacity
bypass or cockpit test result is offered as qualification.

### Completed exact-OID wheel gate at the initial implementation

```bash
TESTER_UNIFIED_RUN_NAME=assay-analysis-full-fde9527a-r2 \
  nice -n 19 ionice -c 3 tester-unified/run --network none \
  --workdir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/assay \
  --evidence-dir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/full -- \
  nice -n 19 ionice -c 3 bash \
  /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/assay/tools/tester-unified-gate.sh \
  --inner /workspaces/vbpub/.worktrees/review-evidence-tools-20260916
```

Admission full avg10: 2.12. Accepted uid 1003, NanoCpus 3000000000,
configured background cgroup and network none. Exact-OID wheel version:
`6.3.1.dev32+gfde9527a`; 694007 bytes; SHA256
`05d061ba3ab0851ea08edff61ef23e9c9cbb8d342b4b700a4f76eae0c3192f8e`.
Attestation: 25 passed, 16 deselected. Verdict-v5 acceptance: 13 passed,
31 deselected. Lane-schema successors: 17 passed. All 34 frozen v6–v10
hard-cut probes passed. Verdict-v11 successors: 110 passed.
Self-hosted suite: **1 failed, 4818 passed, 13 skipped, 635.43s**.
Recorded verdict: **FAIL/COMMAND_FAILED, exit 1**. Docker wait: **1**.
Launcher transport: **125** because the terminated diagnostic replay's
unterminated progress output joined the marker. No Topos, CMRU, independent
witness or pyflakes success is claimed from this failed run.

The last narrow boundary run before these later repairs recorded
**120 passed in 5.86s** in tester-unified. It is historical development
validation, not a green gate for da0c3a58.

### Actual P4/P5 wheel and zipapp replay

The installed fde9527a wheel ran six commands against the genuine preserved
P4 and frozen P5 inputs: launcher, receipt (five jobs/three verdicts/R2 progress),
collect, check, verdict-text and progress. **All six exited 0.**
The resulting portable bundle has **31 files**. Manifest SHA256:
`13164b4231af8230962534b464c817dbb99fdc055de2aeee331f1cd15bf74ba8`.
R2 progress retained the one expected-commit run and its actual facts:
resumed=0, rejudged=0, rejected=2; total=2, pending=2; killed=2; terminal PASS.
These facts do not close P5's B9. The same wheel was packaged as the existing
standalone zipapp distribution; SHA256
`61316c3fb13a7005bbb0d04ce8a4685c0bee7c495576011a685243615359a47d`.
Under `python -I`, P4 launcher and P5 archive check exited **0**, and an
intentionally wrong expected commit exited **1**, as required.
Exact argument vectors, stdout/stderr and actual exits are preserved in the
linked command records. Requalification of the repaired wheel/zipapp is pending.

### Pending repaired-source commands

These were queued but **not executed** because capacity stayed full:

```bash
TESTER_UNIFIED_RUN_NAME=assay-analysis-focused-da0c3a58 \
  nice -n 19 ionice -c 3 tester-unified/run --network none \
  --workdir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/assay \
  --evidence-dir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-da0c3a58 -- \
  nice -n 19 ionice -c 3 python -m pytest -q \
  tests/test_analysis.py tests/test_gate_failure_diagnostics.py \
  tests/test_untrusted_json_parse_sweep.py tests/test_git_boundary.py \
  tests/test_git_hostile_boundary.py tests/test_git_dirty_paths.py \
  tests/test_docs_examples_and_vocabulary.py \
  ../run-gate-project/tests/test_tester_unified_launcher.py
```

The subsequent full command is the exact-OID gate above with the run name
`assay-analysis-full-da0c3a58` and evidence root `final-da0c3a58`.
Full gate, updated wheel/zipapp replay and a live no-final-newline acceptance
probe remain required. `git diff --check` passed for the committed repairs.

### Installed tools and frozen-review preservation

The baseline and current inventory both contain 59 entries. At the earlier
snapshot all entries matched. The later check found one concurrent difference:
CMRU changed from `2.0.1` to editable
`5.2.3.dev14+g1cdbde6d` at `/workspaces/vbpub/cmru`.
This follow-up issued no global install or repoint operation and did not undo
another session's change. The observed full inventory is preserved; identical
installed-tool state across the entire shared session cannot be certified.
Assay remains editable from the frozen RG49 worktree. That candidate remained
clean at `32a8f97c8ef6fe0c602288e112f2e77ece779d1e` when checked.

## Evidence links

- [Failed full gate verdict](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/full/failed-fde9527a.verdict.json)
- [Failed full gate transport](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/full/assay-analysis-full-fde9527a-r2/container.log)
- [P4/P5 wheel replay exact commands](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/replay/commands.json)
- [Zipapp replay exact commands](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/replay/zipapp-commands.json)
- [Final-source admission deferrals](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-da0c3a58/admission.jsonl)
- [Current installed-tool inventory](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/installed-tools-current.json)

## Limits

These commands inspect recorded facts; they do not issue ACCEPT or REJECT.
A valid ERROR verdict or nonzero recorded job exit remains adverse data even
when inspection itself exits 0. No recorded terminal progress event means no
recorded terminal result. Malformed/truncated JSON refuses.

Matching endpoint Git snapshots cannot prove continuous historical cleanliness.
Fingerprints are byte integrity, not producer authentication or signatures.
Launcher inspect is explicitly pre-wait, not final/OOM inspect; analysis does
not query host systemd or certify loaded placement. A complete reproducible
environment/image identity still requires separately preserved evidence.
The frozen P5 report's runtime-route disclosure remains unchanged: actual
machine-verifiable Sol model/effort/route metadata was unavailable and review
proceeded under the user's override.
