# Reusable review evidence creation and consumption

This follow-up implements the workflow repeated during P4 and the RG49
addition to P5 as shipped `assay analyze` commands. It lives on
`review-evidence-tools-20260916`, based on main
`3693a9aea02aa746293ba28ed3b52f4bf6e6a014`. Initial implementation commit:
`fde9527ae053ab453bacbfd73c8342ec72512482`. JSON nesting, captured diagnostics
and the launcher marker boundary were repaired in
`b48e873d` and `d3bacd3166803f10200d53190c542aa351d21dcf`.
Gate interpreter/fixture oracles were corrected in `25269e30` and
`f55bfba8d5f6d8ada3e1690e712644d475c82d75`.
Receipt argv schema consistency and special-file refusal were completed in
`da0c3a584425d8cd2e0da78fc077a0f746d8152b`. Current main through
`1cdbde6de5d6f2541f33a021414cd99aa62589a3` was reconciled in
`5a21241267f3d0c8adfc4130d4cdb21ad185f32a` before final qualification. The final Git ignore-provenance repair is
`0320a16021470c72e7c425c9f2754761bb26a10a`; it replaces display parsing with
bounded NUL-delimited Git input/output.

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
are updated. The Assay gate keeps its existing success phases. Its failure path now inspects the captured verdict instead of rerunning the failed suite.

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
dropped-byte counts without inventing absent capture fields. Assay's
gate consumes that view rather than replaying the whole failed suite. Red gate
status is preserved whether inspection succeeds or fails. Dedicated behavioral
oracles assert no replay and no success phase marker. Recording and receipt
inspection also preserve legitimate empty argv arguments. The packaged receipt
schema permits empty arguments after the nonempty executable. Special-file
inputs such as FIFOs refuse before opening; bounded CLI probes guard against
waiting indefinitely for a writer.

An attempted focused gate was deferred by the launcher's two-container estate
capacity limit. Existing foreign tester containers were left alone. The initial queue supervisor was `/tmp/assay-analysis-qualification-da0c3a58.py`; final qualification uses `/tmp/assay-analysis-qualification-0320a160.py`. Preserved admission streams record deferrals and the pressure/capacity state that allowed each launch.

## Final qualification

Gated source HEAD: `0320a16021470c72e7c425c9f2754761bb26a10a`.
The report update is administrative; the production blobs are compared with
this source before merging. No successful gate is attributed to an untested
administrative or merge HEAD.

The user identified the two earlier occupied tester containers as remnants
and explicitly authorized always starting an owned tester as needed. They
were absent when this resumed qualification launched; no foreign container
was stopped or removed by this follow-up. Earlier admission deferrals are
superseded, not misreported as failed product tests.

The first resumed attempt combined tests from two projects, causing two
conftest import errors (exit 2). Separate pytest processes fixed the invocation.
A subsequent focused run found 12 failed/161 passed in 13.08s; another found
5 failed/169 passed in 21.94s. These were new test-oracle defects: fixture
capture fields already existed, the receipt helper requires explicit input
lists, a source pytest subprocess did not inherit its parent's import path,
Assay uses its console script rather than `python -m assay`, and Python 3.14
parsed deeply nested arrays without exhausting its stack. The final tests
exercise the shipped console script, preserve actual paired capture fields,
assert refusal without assuming a particular decoder depth, and separately
verify local RecursionError translation and the permanent AST sweep.

Final focused command: one canonical offline tester container, with two
sequential pytest processes. Assay subset **187 passed in 17.82s**; launcher
subset **10 passed in 0.75s**. Launcher exit, Docker wait and terminal marker
were all **0**. Exact argv and admission state are in `final-0320a160/commands.json`.

The f55bfba8 full run was deliberately interrupted after a targeted probe
found quoted Git ignore origins were treated as literal paths. Docker wait
137 and transport 125 reflect this controlled interruption, not a functional
test result. The partial progress stream is retained. Repair 3a7f6f3e initially reused the shared C-style path decoder. The next
probe identified colon/numeric delimiter ambiguity as well. Its just-started
full run was interrupted before qualification, recorded as Docker 137 and
transport 125. Final repair 0320a160 reads Git’s NUL-delimited machine format
with bounded regular-file stdin through the existing sanitized process runner.
Six real Git repositories prove receipt output works with control characters,
quotes, backslashes and colon/numeric source names, combined with colon-bearing
ignore patterns. No display spelling or ambiguous delimiter is guessed.

Full gate uses `tester-unified/run --network none` to launch Assay's exact-OID
inner driver. Accepted uid 1003, configured host cgroup `dev-background.slice`,
NanoCpus 3000000000, network none. Launch/update/inspect CPU acceptance and
host/container low priority were preserved; active pytest/Assay processes
were nice 19 and ionice idle. This is the unchanged inner phase sequence with
the canonical launcher transport; it is not claimed to be the registered
legacy outer argv. No cockpit pytest result is a ship signal.

Exact wheel version: `6.3.2.dev23+g0320a160`; 694518 bytes; SHA256
`9e9543b6152bddba0453e578c5d14d87f8e2b975bc1ab8f5293075af82c958fd`.
Runtime package path was inside the private run-venv and its metadata version
matched `assay.__version__`. Release-builder zipapp: 2251260 bytes; SHA256
`660246d5e66b967d6ef8525b1d4df1c55572603492e79d69126685df3f64d9e8`.
Five changed package source/schema files match both distributions byte-for-byte.

Full gate: **PASS**. Exact launch interval: 2026-09-17T05:37:50.398030+00:00 to 2026-09-17T05:52:34.588992+00:00; 884.190962s. Launcher exit **0**, Docker wait **0**, unique last-line `TESTER_UNIFIED_JOB_EXIT=0`. The final supervisor exited **0** after cleanup. Every required success phase was observed:

| phase | exact result |
|---|---|
| wheel build/install and purity | PASS, exact version/hash above |
| attestation hardening | 25 passed, 16 deselected, 1.73s |
| v5 acceptance | 13 passed, 31 deselected, 29.67s |
| lane-v2 successors | 17 passed, 0.32s |
| v6–v10 hard cuts | all 34 frozen templates passed |
| v11 successors | 110 passed, 0.53s |
| self-hosted suite | actual command exit 0; verified verdict PASS/exit 0 at 0320a160 |
| Topos qualification | `ASSAY_GATE_PHASE=topos-qualified` |
| CMRU B006(a) qualification | `ASSAY_B006A_CMRU_QUALIFIED=1`, phase marker; R0/R1/R2/R3 claims PASS |
| independent self-hosting witness | 7 passed, 13.59s |
| hash-pinned pyflakes closure | `ASSAY_GATE_PHASE=pyflakes-clean` |

The self-hosted command was `python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=`. Its command interval was 05:38:43.261792–05:46:49.839274 UTC; progress `command_finished` records actual returncode 0 and elapsed_s 486.654. Successful verdicts omit output tails, so the surviving artifact does not supply a numerical pytest count for this phase. No count is invented. Assay's own declaration remains **R0/tests-pass**; consumer qualification claims do not upgrade it to coverage or mutation certification of Assay's own diff.

The live canonical launcher probe also passed its expected adverse job: stdout `no-final-newline` without a newline, job exit **7**. Raw log was exactly `no-final-newline\nTESTER_UNIFIED_JOB_EXIT=7\n`; Docker wait and launcher exit were both **7**. Accepted uid 1003, parent read from `$CGROUP_PARENT_DEV_BACKGROUND`, NanoCpus 3000000000 and network none. This expected nonzero result proves preservation of a failed job, not a green product gate. The probe observer exited 0 after verifying every fact.

A prior bounded container host-unit query returned `LoadState=loaded` and `FragmentPath=/etc/systemd/system/dev-background.slice`; the selected parent was read from the cockpit environment. It used only a read-only host system-bus socket bind, no host namespace or privileged escape. Foreign jobs were left alone.

Exact full-gate argv (environment `TESTER_UNIFIED_RUN_NAME=assay-analysis-full-0320a160`):

```bash
nice -n 19 ionice -c 3 /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/tester-unified/run --network none --workdir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/assay --evidence-dir /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160 -- nice -n 19 ionice -c 3 bash /workspaces/vbpub/.worktrees/review-evidence-tools-20260916/assay/tools/tester-unified-gate.sh --inner /workspaces/vbpub/.worktrees/review-evidence-tools-20260916
```

Exact focused gate argv and all probe argv/results are preserved in the linked command records below. Temporary installs were confined to container build/run/lint/qualification environments. Gate and probe containers completed and the launcher cleaned its own containers and temporary storage.


## Actual P4/P5 qualification

The gated wheel and derived zipapp executed 12 probes, all with their required
actual exit status. Wheel launcher, P5 receipt, collect, archive check,
verdict-text, progress and captured-failure view exited **0**. A real failed job
recording exited **7**; receipt inspection exited **0** while preserving
`job_exit=7`. Isolated zipapp P4 launcher and P5 archive check exited **0**;
a deliberately wrong expected commit exited **1**. The captured-failure view
showed the earlier real assertion without replaying its suite.

The new P5 archive has **31 artifacts**. Manifest SHA256:
`88212b23497761de9a4782d465f19bbe54cd6f72a205894fa07c7606e3d2277f`.
Expected-commit progress has one matching run: resumed=0, rejudged=0, rejected=2;
total=2, pending=2; killed=2; terminal PASS. Old-commit events are excluded and
same-commit retry runs remain distinct. These historical facts do not close B9.
The original fde9527a wheel/zipapp replay is retained as development evidence;
its successful replay did not turn its failed self-hosting gate green.

## Installed tools and frozen candidate

This follow-up issued no global dependency install or repoint. Both inventory
snapshots have 59 entries. One observed concurrent change was CMRU from 2.0.1
to editable `5.2.3.dev14+g1cdbde6d` at `/workspaces/vbpub/cmru`. The shared
inventory therefore cannot be certified identical to the original baseline.
The follow-up leaves the observed installed tools as found. Temporary gate
build/run/lint environments are allowed and isolated from installed tools.
Assay's global editable source remains the frozen RG49 candidate.

The RG49 candidate remains clean at
`32a8f97c8ef6fe0c602288e112f2e77ece779d1e`. Its [final review](/workspaces/vbpub/.worktrees/rg49-assay-state/run-gate-project/nyxloom-trove/reports/run-gate-RG49-SOL-FINAL-REVIEW.md) remains **REJECT** with B9 outstanding. Pass this report and exact frozen HEAD to the implementer/controller; ordinary-state green gates do not close B9. The original review also discloses that machine-verifiable Sol xhigh route metadata was unavailable; the operator explicitly overrode the initial identity block. This tooling follow-up neither changes nor merges that candidate.

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

## Evidence locations

The retained evidence is local to the private follow-up worktree and is ignored
by Git. The tracked report does not imply these large development artifacts
ship in the main checkout:

- [Exact P4/P5 wheel and zipapp probe commands/results](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/replay-0320a160/commands.json)
- [Final focused/full commands and transport exits](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/commands.json)
- [Self-hosted verdict](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/verdict.json)
- [Self-hosted progress](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/progress-tester-unified.jsonl)
- [Tested production blob identities](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/gated-production-blobs.json)
- [Host unit query](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/host-unit.json)

## Final tooling verdict and integration

**ACCEPT** for the reusable evidence tooling at tested source `0320a16021470c72e7c425c9f2754761bb26a10a`. No remaining tooling blocker was identified. The registered gate outer transport was replaced as disclosed; the complete required inner phase sequence passed. This is not a claim of independently verified Sol xhigh runtime metadata.

Main advanced after qualification from `1cdbde6de5d6f2541f33a021414cd99aa62589a3` to `f96a333820b89683fdc012c27ab772b9caf5ab4e` through a concurrent MDT BuildKit cache fix. Its five changed files are under `modern-debian-tools-python-debug/` and do not overlap the tested Assay/launcher/test surfaces. Integration preserves that change. The subsequent report-only commit and authorized no-fast-forward integration are bound in the ignored post-merge receipt and final response; no fresh test result is attributed to their administrative OIDs. Production blobs are required to equal the tested source. The frozen P5 candidate remains REJECT/B9.

[Live transport command and result](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/live-marker.json); [raw full transport log](/workspaces/vbpub/.worktrees/review-evidence-tools-20260916/.assay/evidence-analysis/final-0320a160/full.transport.log).
