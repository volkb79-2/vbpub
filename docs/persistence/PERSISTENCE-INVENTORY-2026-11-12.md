<!-- origin: 2026-11-12 -->
<!-- status: inventory -->
<!-- copied from the controller program dir 2026-11-12 -->

# Persistence inventory — 2026-11-12

Source: the controller program directory; classification basis: its 2026-11-12 purge manifest and the tracked destinations and proofs named in this inventory. This file classifies every class C and D top-level entry and depth-two sub-item recorded in the manifest. Same-fate leaves are grouped by a literal filename pattern; large snapshot trees are classified as a whole because their contents are host exports or generated run state.

The manifest records class C as 30 top-level entries, 8.4 GiB and 71,570 regular files; class D as 6 entries, 98 MiB and 4,015 regular files; class B as 29 entries, 4.9 GiB and 27,815 regular files. Directory totals overlap their children. The operator removed the five host-backup paths listed below; this task removed nothing.

## Keep / purge summary

| Scope | Purge status | Proof or reason |
|---|---|---|
| Class A: `astra-scratch/`, `cgprofile/`, `dl/` | PURGE-SAFE | Current `ls -A` output is empty for all three directories. |
| PERSIST rows | PURGE-SAFE AFTER THIS COMMIT | Each useful source document has a scrubbed repository copy and an owning index entry. |
| EPHEMERAL rows | PURGE-SAFE | One-off logs, caches, abandoned attempts, and operational snapshots; see the row-level reasons below. |
| ALREADY-IN-REPO rows | PURGE-SAFE | Hashes, tracked IDs/headings, and evidence indexes prove the repo copy or superseding record. |
| PRIVATE-LOCAL | KEEP | Never copy; names only. |
| CONTROLLER-CONTINUITY | KEEP | Handoffs, queues, session state, and controller notes stay outside the repo. |
| ARCHIVE-OUTSIDE | KEEP | Large or host-specific evidence remains in the program directory; no bulk archive copy. |
| OPERATOR-DECISION / KEEP-OUTSIDE | KEEP | Requires an owner decision or separate persistence review. |
| Class B: 29 entries / 4.9 GiB | HOLD; NOT PURGE-SAFE | See the Class B citation ledger, the wave 3 status table, and final purge-readiness table below. |
| DELETED-BY-OPERATOR-DECISION | ALREADY DELETED | Five named directories are absent from the current filesystem listing. |

## Current source metadata and operator deletions

A metadata-only recheck at 2026-11-12 found `r1002/` at 103 MiB / 2,159 files and `v1001/` at 906 MiB / 18,862 files. The operator's 2026-11-12 decision deleted the five backup paths listed below; metadata confirms they are absent. Current kept items include credential and SSH names, runner scripts, runbooks, `r1002/r2-closeouts-20261105/`, and both `v1001/r2-handover-*/` trees. The current `v1001/` tree has six `.claude/` directories; the manifest snapshot listed 55. No source contents were read for this metadata check.

## Class C entries

| Program-dir entry / sub-item | Size | Disposition | Destination or reason | Proof |
|---|---:|---|---|---|
| `ap9/` (REPORT.md, five *.run.log files, five *.verdict.json files) | 64 KiB / 11 files | EPHEMERAL | One-off AP-9 consumer smoke outputs; not product docs or cited repo evidence. | REPORT says it ran narrowed consumers in a cockpit venv, not a registered gate; `git grep` found no AP-9 report citation. |
| `archives/codex-transcripts-202610.tar.zst` | 24,554,980 bytes | CONTROLLER-CONTINUITY | Agent/session transcript archive stays outside the repo. | Filename and archive metadata only; contents not unpacked. |
| `archives/wt-20261104/*.ignored.txt` | 384 KiB / 98 files | EPHEMERAL | Worktree ignore-list snapshots from the 2026-11-04 cleanup; no product documentation. | Names and sizes; none is a Git bundle. |
| `assay/check_gap.py`, `assay/r1-gaps-a6f579f12.json` | 16,548 bytes / 2 files | EPHEMERAL | Coverage-gap checker and measured gap output are run tooling/state. | Names, sizes and the current Assay release record. |
| `assay/gap-agent-brief.md` | 2,095 bytes | CONTROLLER-CONTINUITY | Agent assignment and worktree instructions stay outside the repo. | Name and size; not copied. |
| `assay/RELEASE-NOTES-INPUT-10.0.0.md` | 4,079 bytes | EPHEMERAL | One-time release-route/notification/R2-waiver input for an already shipped release; the file itself is not in the repo. | Filename and its use as a release-notes input; no product source depends on retaining this scratch copy. |
| `ASSAY-DESIGN-OPUS-2026-10-30.md` | 40 KiB / 1 file | PERSIST | Reconciled into [Assay's design](../../assay/docs/ASSAY-DESIGN-OPUS-2026-10-30.md). | Source SHA-256 prefix `d699f3d88129`; pre-wave repo copy `3e23cc3b1650`. Diff showed the source's complete provisional-run path had been shortened during scrubbing; the repo copy now retains it as `<run-gate-state>/…`. |
| `backlog-staging/assay-4-backlog-APPEND.md` | 13,323 bytes | ALREADY-IN-REPO | B206, B207 and B208 are in `assay/nyxloom-trove/4-backlog.md`. | Source SHA-256 prefix `1ba6029bf0eb`; current tracked backlog contains all three IDs. |
| `backlog-staging/cgprofile-backlog/CP-39-*.md` | 8,412 bytes | ALREADY-IN-REPO | `cgprofile/nyxloom-trove/backlog/CP-39-observer-contract-3-measurement-windows-process-tree-and-cgroup.md`. | Source SHA prefix `536841687040`; tracked counterpart SHA prefix `a8ac74c2a2c7`; current index and CHANGES reference CP-39. |
| `backlog-staging/cgprofile-backlog/CP-40-*.md` | 2,824 bytes | ALREADY-IN-REPO | `cgprofile/nyxloom-trove/backlog/CP-40-ancestor-local-memory-events-in-summary-and-window-context.md`. | Source SHA prefix `47591670d6f5`; tracked counterpart SHA prefix `fb5614494e84`; current index and CHANGES reference CP-40. |
| `backlog-staging/DESIGN-RESOURCE-PROFILES-2026-11-03.md` | 67,452 bytes | ALREADY-IN-REPO | Superseded/normalized copy is `docs/design/RESOURCE-PROFILES.md`. | Source SHA prefix `3152d6fb1900`, repo SHA prefix `26ba6803c5f7`; both have the same 939-line heading structure. Repo design contains the current decisions and path/host scrub. |
| `backlog-staging/iobench-IOB-R2-01-REPLACEMENT.md` | 7,600 bytes | ALREADY-IN-REPO | Merged into `libraries/iobench/BACKLOG.md` under IOB-R2-01. | Source SHA prefix `c207f06d71a8`; current backlog includes the IOB-R2-01 section and follow-up results. |
| `backlog-staging/mdt-cockpit-MDT-033-REPLACEMENT.md` | 11,678 bytes | ALREADY-IN-REPO | Superseded by current MDT-033 in `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md`. | Source SHA prefix `5b75f6c9e315`; tracked backlog has the current MDT-033 heading and sub-items a–g. |
| `backlog-staging/run-gate-KNOWN_ISSUES-APPEND.md` | 14,423 bytes | ALREADY-IN-REPO | RG-144 through RG-148 are in `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`. | Source SHA prefix `ae13daf41d73`; tracked backlog contains the five IDs. |
| `backlog-staging/REVIEW-NOTES.md` | 7,618 bytes | CONTROLLER-CONTINUITY | Coordinator staging notes and resolved decisions remain outside. | Name and size; the resulting design and backlog entries are tracked. |
| `backlog-staging/tools/sched-sim.py`, `sim-budget.md`, `sim-cli-extended.md`, `sim-iobench.md` | 16,322 bytes / 4 files | ALREADY-IN-REPO | Exact copies are in `docs/design/resource-profiles-sim/`. | SHA-256 is identical for all four source/destination pairs: `d2d527b2987a`, `344fbeaea603`, `a6e4eb9a983d`, `af0d349479a8`. |
| `COW-PLAN-OPUS-2026-10-30.md` | 48 KiB / 1 file | PERSIST | Reconciled into [Cowtree's plan](../../libraries/cowtree/docs/COW-PLAN-OPUS-2026-10-30.md). | Source SHA prefix `28101f93db75`; pre-wave repo copy `5730c57767b1`. Diff showed public host label `r1002` had been replaced by `<host>`; the public label is restored. Absolute local paths remain placeholders. |
| `devcontainer.json.local.bak`, `devcontainer.json.operator-20261103` | — | PRIVATE-LOCAL | Operator-local configuration backups; never copy. | Names only; contents not read. |
| `fable-sessions.md` | 4 KiB / 1 file | CONTROLLER-CONTINUITY | Session register stays outside the repo. | Controller session register; name and size only. |
| `handoff-assay.md`, `handoff-cgprofile-1.1.0-release.md`, `handoff-ciu.md`, `handoff-cmru-post-7.0.0-review.md`, `handoff-iogov-final-review.md`, `handoff-nyxloom.md`, `handoff-run-gate-deep-review.md`, `handoff-tester-rebuild-20261028.md`, `handoff-tester-unified-rebuild.md`, `handoff-truenas-v1001.md` | 84 KiB / 10 files | CONTROLLER-CONTINUITY | Handoff material stays outside. | Names and manifest sizes only; matches the explicit handoff exclusion. |
| `iogov-install-record.md` | 4 KiB / 1 file | ALREADY-IN-REPO | `iogovmon/docs/evidence/INSTALL-RECORD-2026-10-27.md`. | Source SHA prefix `bdb7bb6ce95c`, destination `e2564e6ac6c2`; diff is provenance/path normalization (`/root` became `<home>`). |
| `LANES-AUDIT-2026-10-29.md` | 32 KiB / 1 file | PERSIST | Reconciled into [the lane-audit design index](../design/lane-audit/LANES-AUDIT-2026-10-29.md). | Source SHA prefix `9a4b2e360d65`; pre-wave repo copy `83b28229fb73`. Diff replaced the controller-home mention with `<controller-home>`; the complete body is retained. |
| `nyxloom-usage-analysis-plan.md` | 32 KiB / 1 file | ALREADY-IN-REPO | `nyxloom/docs/plan-usage-analysis-2026-10-28.md`. | Source SHA prefix `db44648b7661`, destination `8b1403597cc9`; diff is provenance and scratch-path normalization. |
| `r1002/ib-vq/`, `r1002/ib-vq-rerun/` | Manifest: 1.2 MiB / 198 files; 3.8 MiB / 382 files | ARCHIVE-OUTSIDE | Host-specific benchmark/test evidence remains in the controller program directory, outside Git. | Manifest depth-two inventory; current parent count is lower; no raw evidence copied. |
| `r1002/preserve-20261112/`, `r1002/pre-reinstall-20261112/`, `v1001/export-20261105/`, `v1001/images-20261108/`, `v1001/pre-reinstall-20261108/` | — | DELETED-BY-OPERATOR-DECISION | Operator decision dated 2026-11-12: “Delete them.” | Metadata-only `ls -ld` reports each path absent. |
| Seven git bundles named in the 2026-11-12 operator deletion decision | — | DELETED-BY-OPERATOR-DECISION | The decision identifies seven deleted bundle files. The decision summary did not enumerate their paths; current bundle survivors are listed under `releases-archive/`. | Name-only filesystem enumeration; no bundle contents read. |
| `r1002/reinstall-20261112/`, `reinstall-20261112-dry1/`, `reinstall-20261112-dry2/`, `stale-20261112/` | Manifest: 52 KiB / 6 files; 24 KiB / 4; 24 KiB / 4; 48 KiB / 9 | ARCHIVE-OUTSIDE | Remaining host installation snapshots and dry-run outputs stay in the program directory. | Manifest names/counts; current directory metadata confirms these paths remain. |
| `r1002/r2-closeouts-20261105/` | 24 MiB | ARCHIVE-OUTSIDE | Raw campaign archive stays in the program directory; the retained s1 closeout summary is `cmru/docs/evidence/r2-cmru-r2c-s1.md`. | Current metadata size; `closeout.tgz` was excluded from Git. |
| `r1002/runner-20261112/vbpub-r1002-src-20261112.bundle` | 77,569,459 bytes | ARCHIVE-OUTSIDE | This runner bundle still exists (it was not among the operator-deleted backups); it is too large for Git. | `ls -l` lists the path and size. Bundle contents were not read in this round. |
| `r1002/runner-scripts-local-20261112/` and host-named install/runner scripts | 28 KiB / 3 files; direct root has 21 `.sh` and 5 `.py` files | OPERATOR-DECISION | Some names resemble install helpers, but their relation to host-local configuration and generic install docs cannot be established from metadata. Keep outside pending operator classification. | Names/size/mode only; no scripts copied. |
| `r1002/RUNBOOK.md` | — | PRIVATE-LOCAL | Host-specific runbook excluded; no content copied. | Name only. A redacted pattern-count scan read it in wave 2 and emitted no source lines or values (12 address-like and 19 private-name matches); this deviation is recorded for the operator. |
| `r1002/root-password`, `runner.env`, `ssh_known_hosts*`, `ssh-tail-*`, `task-monitor-*`, `api-env.sh`, `store-root-password.py`, `extract-root-password.py`, `ssh-askpass-root.sh`, host deployment JSON/configs | — | PRIVATE-LOCAL | Credentials, SSH material and host configuration remain outside. | Names only; contents not read or copied. |
| `r1002/*.log`, `*.out`, `*.err`, `*.EXIT`, `*.pid`, benchmark output | Included in 2,260 files / 4.7 GiB | EPHEMERAL | Reinstall, runner and benchmark outputs. | Filename patterns and manifest counts; no raw run output copied. |
| `r1002/CONTINUATION-BRIEF-1.md`, `JOB-QUEUE-2026-11-04.md`, `R2-BOARD-2026-11-05.md` | 2,155; 4,317/7,413; 15,626 bytes | CONTROLLER-CONTINUITY | Active/previous queue and campaign-board state stays outside. | Names and sizes only; not copied. |
| `removal-log.md` | 16 KiB / 1 file | CONTROLLER-CONTINUITY | One-time controller cleanup record; its disposition records no product decision beyond the tracked cleanup guidance and history. | The resulting cleanup guidance and decisions are tracked; this controller log is not their sole record. |
| `TRUENAS-MANIFEST-OPUS-2026-10-30.md` | 28 KiB / 1 file | ALREADY-IN-REPO | `scripts/truenas/docs/TRUENAS-MANIFEST-2026-10-30.md`. | Source SHA prefix `9ad2595f8fb5`, destination `9789e90a55c5`; diff is provenance and worktree-path normalization. |
| `truenas-tui-research.md` | 20 KiB / 1 file | ALREADY-IN-REPO | `scripts/truenas/docs/RESEARCH-tui-2026-10-28.md`. | Source SHA prefix `986267bc4957`, destination `523973353afb`; diff replaces the host endpoint with `<host>` and adds provenance. |
| `v1001/r2-handover-20261108/`, `r2-handover-s4-20261108/` | 458 MiB / 9,373 files; 447 MiB / 9,164 files | ARCHIVE-OUTSIDE | Host handover and test-state snapshots stay outside; nested session/private paths are not copied. | Manifest counts; no nested contents read. |
| `v1001/ciu-remote-test/`, `cockpit-20261108/`, `ib-vq/`, `iobench-diag-20261108/`, `reinstall-20261108-swap64/`, `reinstall-20261108-swap64-dry1/`, `reinstall-20261108-swap64-dry2/`, `stale-20261107/`, `superseded-prep-20261108/` | 42, 9, 158, 2, 6, 4, 4, 8, 5 files respectively | ARCHIVE-OUTSIDE | Test, install and stale host snapshots; no repository copy. | Manifest depth-two listing; names and sizes only. `pre-reinstall-20261108/` is listed as deleted above. |
| `v1001/runner-scripts/`, `runner-scripts-host-snapshot-20261108/`, `runner-scripts-local/` | 84 KiB / 18 files; 120 KiB / 25; 24 KiB / 3 | OPERATOR-DECISION | Host runner scripts may overlap generic install/setup workflows; not proven safe or generic from metadata. | Directory names/counts only; files not read or copied. |
| `v1001/CONTINUATION-BRIEF-1.md`, `JOB-QUEUE-2026-11-04.md` | 2,155 and 4,317 bytes | CONTROLLER-CONTINUITY | Handover and queue documents are continuity. | Names and sizes only; no source copy. |
| `v1001/FINDING-SNAPSHOTS-GONE-20261104.md` | 1,364 bytes | EPHEMERAL | One-time snapshot-loss run note; the underlying cleanup lesson is in `scripts/netcup/CONSUMERS.md` and its backlog. | Tracked Netcup guidance covers the operational lesson. |
| `v1001/RUNBOOK.md` | — | PRIVATE-LOCAL | Host-specific runbook; leave outside. | Name only; contents not read. |
| `v1001/runner.env`, `api-env.sh`, `ssh_known_hosts-v1001-*`, nested `*.env`, `*.pem`, `.local`, `.claude/` | — | PRIVATE-LOCAL | Host credentials, SSH, local config and session names stay outside; do not read or copy contents. | Names only; current scan found six `.claude/` directories; the manifest snapshot listed 55. |
| `worktree-audit/` (audit/inventory TSVs, merged/unmerged snapshots, scripts and log) | 372 KiB / 11 files | EPHEMERAL | Point-in-time cleanup audit; current Git worktree state supersedes it. | Filenames and manifest counts; no product doc destination. |
| `worktree-inventory-20261027.json`, `worktree-inventory-20261027.md` | 192 KiB and 108 KiB | EPHEMERAL | Point-in-time operational snapshots superseded by the live Git worktree state and CIU worktree records. | Filenames and dates identify snapshots; current repository lifecycle commands provide live state. |

## Class D entries

| Program-dir entry / sub-item | Size | Disposition | Destination or reason | Proof |
|---|---:|---|---|---|
| `ciu-sim/tester_env_plugin.py`, `ciu-sim/__pycache__/tester_env_plugin.cpython-314-pytest-9.1.1.pyc` | 5,229 and 9,813 bytes | EPHEMERAL | Test probe and compiled bytecode; no docs destination. | Names/sizes; source code is not a documented product interface. |
| `pwmcp/test_glyphgrid_generator_identity.py` | 3,293 bytes | PERSIST | Persisted as [`pwmcp/tests/test_glyphgrid_generator_identity.py`](../../pwmcp/tests/test_glyphgrid_generator_identity.py); the fast/reference APIs and imagegen matrix are present in tracked pwmcp. | Source SHA-256 prefix `612f0a579b03`; obsolete branch preamble removed; the test passes in the pwmcp r1 lane and fails under a scratch mutation of the generator identity check. |
| `pwmcp/tester-unified-console-corpus.patch.txt` | 1,657 bytes | EPHEMERAL | Superseded experiment patch; R4's local-directory corpus plan is tracked. | Filename and current R4 plan; no source patch copied. |
| `shard-move-s13/final/` | 8.6 MiB / 6 files | ALREADY-IN-REPO | Final campaign index and raw files are in `cmru/docs/evidence/r2-shard-s13.md` and its indexed directory. | Three raw files match byte-for-byte; the tracked index gives hashes for all three scrubbed JSON/JSONL destinations. |
| `shard-move-s13/try1-baseline-flake/`, `try2-guard-stopped-2129Z/`, root attempts/monitor/controller scripts, `v1001-campaign-copy/` | 2.8 MiB / 6; 52 KiB / 3; remaining run files; 88 KiB / 3 | EPHEMERAL | Failed/paused attempts and controller output; final campaign evidence is retained and indexed. | Attempt names identify baseline-flake, guard-stopped, and duplicate campaign-copy runs. |
| `shard-move-s4/final/` | 16 MiB / 5 files | ALREADY-IN-REPO | Final campaign index and raw files are in `cmru/docs/evidence/r2-shard-s4.md` and its indexed directory. | Three raw files match byte-for-byte; the tracked index gives hashes for both scrubbed JSON/JSONL destinations. |
| `shard-move-s4/attempt2-guard-stopped/`, `run2-aborted-follow/`, root attempts/monitor/controller scripts, `v1001-campaign-copy/` | 20 KiB / 4; 28 KiB / 7; remaining run files; 324 KiB / 8 | EPHEMERAL | Aborted/paused attempts and duplicate campaign copies. | Attempt names identify guard-stopped, aborted-follow, and duplicate campaign-copy runs. |
| `spike-augeas/` (b1150, dl, linklib, pyaug, rl, root, root-b1150, root.orig, root-py, root-v1141, scripts, src, v1141, v1150) | 60 MiB / 3,899 files | OPERATOR-DECISION | Guess: preserved Augeas 1.14/1.15 build and compatibility spike, but no durable owner/persistence mapping was established. Keep until the operator decides. | Manifest depth-two inventory and names only; tree not copied. |
| `successor/` (cgp-fs, div2-v2-fix5, ren-a, x02-e0-fix, x02-e3-fix extraction/prose/successor outputs) | 88 KiB / 25 files | OPERATOR-DECISION | Guess: generated follow-on analysis for five separate tasks; prose may contain useful findings, but it is not linked to a tracked project record. | Names/sizes only; content not copied. |

## Class B citation ledger

Class B is 29 top-level entries, 4.9 GiB and 27,815 regular files in the manifest. The table below is the wave 2 ledger. Wave 3 re-scanned tracked files for cited program-directory paths and source basenames, excluding this inventory and provenance headers. The wave 2 `reports/` table listed 53 small cited files; its original scan found 70 tracked citer-file/source pairs (19 now point to repository copies and 51 still cite outside sources). Three additional citations to the Nyxloom report were found outside that table and are persisted. The copied reports exposed four more pairs to four of the 38 remaining baseline files, plus three pairs to three additional report files. The wave 3 first slice therefore leaves 41 small cited files and 58 source/citer pairs pending, documented below. It persisted 16 reports and added two raw-evidence summaries. Class B remains on hold; this is not purge authorization.

| B top-level entry | Manifest size / files | Result | Cited source path(s), tracked citers, or reason |
|---|---:|---|---|
| `ciu/` | 24 KiB / 4 | UNCITED (0) | No exact controller path or source sub-item citation found; generic project-name matches excluded. Candidate for operator review. |
| `codex/` | 859 MiB / 3,458 | KEEP-CITED (8 tracked files) | `codex/runs/` citers: `docs/design/ctlstat/REQUIREMENTS.md`, `nyxloom/nyxloom-trove/backlog/INDEX.md`, `nyxloom/nyxloom-trove/backlog/NL-56-mechanical-codex-stop-state-and-stall-detection-replace-control.md`, `nyxloom/nyxloom-trove/backlog/NL-62-record-level-search-across-session-evidence.md`, `nyxloom/nyxloom-trove/backlog/NL-63-resolve-project-and-parent-session-side-files.md`, `scripts/controller/tests/test-job-check.sh`. Controller-path citers: `scripts/debian-rollback/TODO.md`, `scripts/debian-rollback/docs/WORK-PACKAGES.md`. Keep live `.wt` markers and unmerged worktree state. |
| `controller/` | 11 MiB / 525 | KEEP-CITED (1) | `controller/ib-vq-rerun/` is cited by `libraries/iobench/BACKLOG.md`. |
| `dstdns-r2/` | 1.3 GiB / 4,194 | KEEP-OUTSIDE-WITH-SUMMARY (partial) | `dstdns-r2/work/r2lanes.txt`: `cmru/docs/evidence/r2-board-20261105b-REPORT.md`. Other path citers: `docs/design/dstdns/R2-PILOT-2026-11-04.md`, `docs/design/host-service-sockets/RELEASE-PLAN-phase1-2026-11-07.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md`, `scripts/debian-install-v2/docs/evidence/div2-reinstall-RUNBOOK.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md`. A one-page summary and rewrites remain for these six citer files. |
| `evidence/` | 405 MiB / 5,367 | KEEP-OUTSIDE-WITH-SUMMARY (partial) | IOBench raw evidence is summarized at `libraries/iobench/docs/reports/IOBENCH-R2-R1002-EVIDENCE-SUMMARY-2026-11-13.md`; Assay jobs-pilot raw evidence is summarized at `assay/docs/evidence/JOBS-PILOT3-EVIDENCE-SUMMARY-2026-11-13.md`, and both tracked citations now point to those summaries. TrueNAS live evidence remains outside; its existing index records the retained/omitted files, but a wave 3 one-page summary and citation rewrite are still needed. The 22 `evidence/truenas-live-20261029/` citers are `scripts/truenas/boot-pool-patch/TODO.md`, `scripts/truenas/boot-pool-patch/docs/DESIGN-GUIDE.md`, `scripts/truenas/boot-pool-patch/v28-nightly-20261027/README.md`, `scripts/truenas/docs/evidence/truenas-live-20261029.md`, and `scripts/truenas/docs/evidence/truenas-live-20261029/{unit-green-v28-external-lane.log,unit-red-v28-external-lane.log,v27-after-ui-inspect.log,v27-patch-apply.log,v27-path-a-api-rerun.log,v27-path-a-api.log,v27-path-a-ui.log,v27-path-b-api-rerun.log,v27-path-b-api.log,v27-path-b-partition.log,v27-path-b-provision-idempotence.log,v27-path-b-ui-dom-capture.log,v27-path-b-ui-fixed.log,v27-path-b-ui-nav-fixed.log,v27-path-b-ui.log,v27-stock-inspect-final.log,v27-stock-inspect-r3.log,v28-target-source-archive.sha256}`. `evidence/truenas-live-20261030/`: `scripts/truenas/docs/UPDATE-AND-DRIFT.md` and `scripts/truenas/docs/evidence/truenas-live-20261030.md`. `evidence/truenas-live-20261031/`: `scripts/truenas/docs/evidence/truenas-live-20261031.md`, `scripts/truenas/docs/evidence/truenas-live-20261031/{blocked-local-tcg-20261030.md,blocked-step2-chroot-dns-20261031.md,step1-20g.log}`, and `scripts/truenas/golden-iso/docs/CONSUMERS.md`. |
| `gate2.lock` | 0 bytes / 1 | KEEP-CITED (4) | Four users: `scripts/controller/controller.local.env.example`, `scripts/controller/gate-run.sh`, `scripts/controller/tests/mutate-sources.py`, `scripts/controller/tests/test-hardening-tools.sh`. |
| `gate.lock` | 0 bytes / 1 | KEEP-CITED (30) | Thirty users: `ciu/docs/plan-release-2026-11-02.md`, `cmru/tools/run_release_gate.py`, `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W2-INTEG-REPORT.md`, `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W2-PKG4-REPORT.md`, `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W3-PREP-REPORT.md`, `cmru/nyxloom-trove/reports/PROGRAM-2026-10-W3-ZERO-REPORT.md`, `cmru/nyxloom-trove/reports/cmru-CREDPASS-2026-10-REPORT.md`, `cmru/nyxloom-trove/reports/cmru-release-readiness-claude-opus-5-high-2026-10-13.md`, `libraries/cli-extended/docs/CX-BACKLOG-2026-10-REPORT.md`, `libraries/cli-extended/docs/program-reports/CLIX-04-REPORT.md`, `libraries/cli-extended/docs/program-reports/W10-REPORT.md`, `libraries/cli-extended/docs/program-reports/W9a-REPORT.md`, `libraries/worktree/docs/INTEGRATION-PLAN-2026-11-01.md`, `mdt-cockpit/docs/RELEASE-PLAN-2026-11-03.md`, `nyxloom/nyxloom-trove/reports/MM-MOVE-REPORT.md`, `nyxloom/nyxloom-trove/reports/nyxloom-SUCCESSOR-2026-10-REPORT.md`, `nyxloom/nyxloom-trove/reports/nyxloom-usage-iteration-4-REPORT.md`, `pwmcp/docs/PWMCP-02-REPORT.md`, `pwmcp/docs/RELEASE-PLAN-R4-2026-11-02.md`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`, `run-gate-project/nyxloom-trove/reports/RG-SMALL-REPORT.md`, `run-gate-project/nyxloom-trove/reports/run-gate-RG-NARROW-2026-10-REPORT.md`, `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`, `scripts/controller/controller.local.env.example`, `scripts/controller/gate-run.sh`, `scripts/controller/rules.md`, `scripts/controller/tests/test-hardening-tools.sh`, `scripts/debian-install-v2/LT-MEM-REPORT.md`, `scripts/mounttrace/docs/evidence/mt-clix-REPORT.md`, `voice-screen-share/mattermost-server/docs/MM-RENAME-REPORT.md`. |
| `HANDOFF-STATE-20261105.md` | 16 KiB / 1 | KEEP-CITED (1) | `docs/design/dstdns/DEVELOPMENT-PLAN-2026-11-04.md` cites it; continuity stays outside. |
| `HANDOFF-STATE-20261107.md` | 296 KiB / 1 | KEEP-CITED (7) | Seven users: `augeaswrap/BACKLOG.md`, `docs/design/telemetry-platform/DESIGN-v2-2026-11-07.md`, `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`, `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `run-gate-project/docs/evidence/host-selftest-stall-REPORT.md`, `scripts/netcup/KNOWN_ISSUES_TODO_BACKLOG.md`. |
| `HANDOFF-STATE.md` | 68 KiB / 1 | KEEP-CITED (1) | `assay/docs/ASSAY-OPUS-REVIEW-AND-PLAN-2026-10-31.md` cites it; continuity stays outside. |
| `host-sat-queue.disabled.r1002` | 0 bytes / 1 | KEEP-CITED (18) | Eighteen tracked citers: `augeaswrap/docs/evidence/aw-bl-22-24-LIVE-REPORT.md`, `cmru/docs/evidence/shard-move-s4-REPORT.md`, `docs/design/ctlstat/REQUIREMENTS.md`, `scripts/controller/ctl-status.sh`, `scripts/controller/tests/mutate-sources.py`, `scripts/controller/tests/test-ctl-status.sh`, `scripts/debian-install-v2/docs/evidence/div2-reinstall-host-RESULT.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md`, `scripts/mounttrace/docs/evidence/mt-close-vm-LIVE-REPORT.md`, `scripts/mounttrace/docs/evidence/mt11-rerun-LIVE-REPORT.md`, `scripts/netcup/KNOWN_ISSUES_TODO_BACKLOG.md`, `scripts/netcup/docs/HOST-SAT-QUEUE.md`, `scripts/netcup/docs/evidence/host-cockpit-EXEC-LOG.md`, `scripts/netcup/docs/evidence/host-queue-REPORT.md`, `scripts/netcup/runner/README.md`, `scripts/netcup/runner/host-sat-queue.sh`, `scripts/netcup/runner/tests/test-queue.sh`. |
| `host-sat-queue.disabled.v1001` | 0 bytes / 1 | KEEP-CITED (18) | The same eighteen tracked files listed for the r1002 flag cite the paired per-host pause-flag pattern. The flag files themselves are runtime controller state. |
| `host-sat-queue.README.md` | 4 KiB / 1 | PERSISTED-CITATION (1) | Persisted as `scripts/netcup/docs/HOST-SAT-QUEUE.md`; its source header is the sole citer. This individual source is purgeable. |
| `host-sat-queue.README.md.nc28-backup-20261109T190502Z` | 4 KiB / 1 | UNCITED (0) | No exact backup-name citation found. Candidate for operator review. |
| `host-sat-queue.sh` | 16 KiB / 1 | KEEP-CITED (21) | Twenty-one users: `scripts/netcup/runner/README.md`, `scripts/netcup/docs/HOST-SAT-QUEUE.md`, `scripts/netcup/CONSUMERS.md`, `scripts/netcup/DESIGN-GUIDE.md`, `scripts/netcup/KNOWN_ISSUES_TODO_BACKLOG.md`, `scripts/controller/README.md`, `scripts/controller/controller.local.env.example`, `scripts/controller/docs/CONFIGURATION.md`, `scripts/controller/docs/CONSUMERS.md`, `scripts/controller/tests/mutate-sources.py`, `scripts/controller/tests/run-suite.sh`, `scripts/controller/tests/test-mutations.sh`, `scripts/netcup/runner/remote-gate.py`, `scripts/netcup/runner/tests/test-queue.sh`, `scripts/netcup/tests/test_runner_scripts.py`, `docs/design/ctlstat/REQUIREMENTS.md`, `run-gate-project/docs/DESIGN-compute-hosts-and-campaigns-2026-11.md`, `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `run-gate-project/docs/evidence/host-selftest-stall-REPORT.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md`, `scripts/netcup/docs/evidence/host-cockpit-EXEC-LOG.md`. A tracked equivalent exists; this program-directory source remains a separate item. |
| `host-sat-queue.sh.nc28-backup-20261109T190502Z` | 12 KiB / 1 | UNCITED (0) | No exact backup-name citation found. Candidate for operator review. |
| `ib-vq-scripts/` | 88 KiB / 16 | UNCITED (0) | No exact source path citation found; the separate `controller/ib-vq-rerun/` item is cited under `controller/`. Content looks unique (benchmark driver scripts): operator review before any purge. |
| `iobench` | 8 KiB / 1 | UNCITED (0) | No exact controller path citation found; generic project-name matches excluded. Candidate for operator review. |
| `logs/` | 31 MiB / 3,589 | RUNTIME-DEPENDENCY (CTL-02; configuration-driven) | `ctl-status.sh` reads `$CONTROLLER_HOME/logs/host-sat-queue/` and `$CONTROLLER_HOME/logs/host-sat-results.tsv`; queue pause flags also live under the configured home. It works with another controller home when the needed state is moved there. The old queue history is not migrated by changing the variable; retain or copy the needed state before retiring the current home. The tracked citations are the same 9 results-ledger users and 13 host-saturation users listed in the wave 2 row above. Sockets and Nyxloom gate-log citations in the persisted reports were rewritten to those reports' evidence summaries. |
| `pilot/` | 620 KiB / 24 | UNCITED (0) | No exact controller path or source sub-item citation found. Content looks unique (pilot run material): operator review before any purge. |
| `R2-PORTFOLIO-PLAN.md` | 16 KiB / 1 | PERSISTED-CITATION (2) | Persisted as `docs/design/r2-portfolio/R2-PORTFOLIO-PLAN-2026-10-29.md`; both tracked citations point to the repository copy. This individual source is purgeable. |
| `r2-session/` | 2.2 GiB / 9 | KEEP-CITED (2) | Two users: `run-gate-project/docs/evidence/fix-r2-lanes-REPORT.md` and `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md`. |
| `releases-archive/` | 14 MiB / 97 | KEEP-CITED (1) | `scripts/controller/README.md` cites the archive. `branch-bundles/` contains nine bundles whose heads are not in `origin/main` per the operator-supplied state; contents were not opened. |
| `remote-gates/` | 119 MiB / 9,176 | KEEP-CITED (6) | Six users: `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md`, `scripts/netcup/docs/HOST-SAT-QUEUE.md`, `scripts/netcup/runner/README.md`, `scripts/netcup/runner/host-sat-queue.sh`, and `docs/reports/assay-tmpfs-CONTINUATION.md` (unfinished run output; final verdict pending). |
| `reports/` | 20 MiB / 1,177 | PERSIST-PARTIAL (41 cited files / 58 outstanding citer-file/source pairs) | Wave 3 persisted 15 of the 53 files listed below and the additional `nyx-extract-readback-REPORT.md`; secondary citations in copied reports add three pending report files. See the wave 3 matrix and secondary-source table for exact remaining sources/citers. Earlier KSM, IOBench and Assay tmpfs report copies remain persisted. |
| `reviews/` | 2.6 MiB / 147 | PERSISTED-CITATION (1); top-level KEEP | `reviews/pwmcp2.out` is now copied to `pwmcp/docs/reports/pwmcp2-review-2026-10-30.md` and the six PWMCP backlog references point there. The other 146 entries have not been reviewed for purge. |
| `v1001.lock` | 0 bytes / 1 | RUNTIME-DEPENDENCY (TN-17; fixed path) | `scripts/truenas/boot-pool-patch/acceptance/accept.py` uses the fixed `v1001.lock` location under the release-program root and has no alternate-home override. Keep the lock location until the code is made configurable or the exact limitation is accepted. |
| `vm-cgp-accept-20261107/` | 192 KiB / 17 | UNCITED (0) | No exact controller path or source sub-item citation found. Candidate for operator review. |
| `WAVE-BRIEF.md` | 8 KiB / 1 | UNCITED (0) | No exact tracked citation found. It is controller continuity and should remain outside pending operator review. |

`releases-archive/branch-bundles/` currently lists these nine names: `cowtree-gate-scratch-20261105.bundle`, `test-policy-doc-20261109.bundle`, `mdt-admission-cap-gap-20261105.bundle`, `tnt-r2-pilot-20261105.bundle`, `rigor-rr3-20261105.bundle`, `rigor-rr2-20261105.bundle`, `cmru-r2-shards-20261105.bundle`, `tnt-r2-20261105.bundle`, and `ref-hc-1a-wip-20261108.bundle`. The additional `r1002/runner-20261112/vbpub-r1002-src-20261112.bundle` also appears in `ls`. No bundle contents were read. The seven bundle pathnames in the operator deletion decision were not available for matching; the runner bundle remains ARCHIVE-OUTSIDE because it still exists.

### Reports: wave 2 source inventory

The bounded fix-2 slice persisted the small KSM report to
`mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md` and the
IOBench R2 report to `libraries/iobench/docs/reports/IOBENCH-R2-R1002-2026-11-02.md`.
The five KSM backlog citers and the IOBench backlog now point to those repo
copies; their external source paths are no longer needed. The 53 rows below
record the wave 2 source/citer baseline. Wave 3 copied 15 rows from this list;
the exact destination and remaining source matrix follows the table. The
original sizes are metadata only.

| Source basename | Source size | Tracked citer file(s) |
|---|---:|---|
| `AGENT-RULES.md` | 4,750 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md` |
| `DSTDNS-R2-PILOT-CONTINUATION-BRIEF-2026-11-04.md` | 3,165 B | `docs/design/dstdns/R2-PILOT-2026-11-04.md` |
| `FLUXER-VERIFICATION-2026-11-04.md` | 8,640 B | `voice-screen-share/fluxer-server/README.md` |
| `R1002-REINSTALL-2026-11-04.md` | 10,277 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `ad2-measurements-REPORT.md` | 8,657 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md` |
| `assay-jobs-audit-REPORT.md` | 15,064 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `libraries/cli-extended/assay.toml` |
| `assay-jobs-audit-REVIEW.md` | 6,502 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md` |
| `assay-rust-docs-REPORT.md` | 5,192 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `assay-tmpfs-CONTINUATION.md` | 5,801 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md` |
| `augeas-extension-FEASIBILITY.md` | 23,458 B | `docs/design/hostcfg/DESIGN-AUGEAS-AND-BOUNDARIES-2026-11-07.md` |
| `cgpd-rollout-REPORT.md` | 6,347 B | `cgprofile/nyxloom-trove/backlog/CP-57-cgpctl-doctor-caller-group-fails-for-root-so-check-sh-and-the-root-install-run-always-report-a-failure.md`, `cgprofile/nyxloom-trove/backlog/INDEX.md` |
| `ciu-survivors2-REPORT.md` | 7,458 B | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `di2-ksm-sleep-REVIEW.md` | 4,487 B | `scripts/debian-install-v2/TODO.md` |
| `div2-operator-scripts-v2.patch` | 47,109 B | `scripts/debian-install-v2/docs/evidence/div2-reinstall-RUNBOOK.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md` |
| `div2-reinstall-v1001-RESULT.md` | 9,746 B | `libraries/iobench/BACKLOG.md` |
| `dstdns-cp0-CONTINUATION.md` | 5,337 B | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-cp0-REPORT.md` | 7,099 B | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-e2e-REPORT.md` | 6,536 B | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-p4-REVIEW.md` | 10,833 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md` |
| `dstdns-parallelism-REPORT.md` | 2,560 B | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `libraries/rigor-record/docs/DESIGN-MEMO-2026-11-05.md` |
| `fix-r2-lanes-CONTINUATION.md` | 1,507 B | `run-gate-project/docs/evidence/fix-r2-lanes-REPORT.md` |
| `flex-r2-REPORT.md` | 38,728 B | `assay/nyxloom-trove/4-backlog.md` |
| `gate-reuse-INVESTIGATION.md` | 20,260 B | `docs/design/test-policy/REQUIREMENTS-2026-11-09.md`, `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `gate-reuse-REVIEW.md` | 12,963 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `hc4-REPORT.md` | 9,634 B | `nyxloom/tests/fixtures/stall/clean.exec.jsonl` |
| `host-compute-REPORT.md` | 2,484 B | `libraries/rigor-record/docs/DESIGN-MEMO-2026-11-05.md` |
| `host-parallel-r2-REPORT.md` | 13,180 B | `assay/nyxloom-trove/4-backlog.md` |
| `host-pipeline-PLAN.md` | 64,951 B | `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `hosts-cow-ksmd-REPORT.md` | 32,958 B | `assay/nyxloom-trove/4-backlog.md` |
| `ib-vq-REPORT.md` | 27,757 B | `libraries/iobench/BACKLOG.md` |
| `ib-vq-controller-REPORT.md` | 11,168 B | `libraries/iobench/BACKLOG.md` |
| `ib-vq-rerun-REPORT.md` | 8,323 B | `libraries/iobench/BACKLOG.md` |
| `iobench-iogovmon-DESIGN-2026-11.md` | 28,149 B | `scripts/debian-install-v2/TODO.md` |
| `mdt-dedicated-mem-REPORT.md` | 12,017 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md` |
| `mt11-iobench-LIVE-REPORT.md` | 37,691 B | `libraries/iobench/docs/SPEC.md` |
| `nyx-3-REPORT.md` | 8,420 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `pytest92-audit-REVIEW2.md` | 10,762 B | `topos/nyxloom-trove/4-backlog.md` |
| `reinstall-READINESS-20261105.md` | 15,713 B | `docs/design/mdtd/DESIGN-rev4-2026-11-07.md` |
| `rg-102-C0-SPIKE.md` | 30,835 B | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `rg-154-REPORT.md` | 3,456 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `rg-flake-REVIEW.md` | 6,644 B | `nyxloom/tests/fixtures/stall/review-rg-flake.rollout.jsonl` |
| `rigor-chain-REVIEW.md` | 13,344 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `rigor-merge-prep-REPORT.md` | 6,540 B | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `rigor-rr2a-CONTINUATION.md` | 45,867 B | `libraries/rigor-record/CHANGES.md` |
| `rust-r0-REPORT.md` | 11,264 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `session-audit-3.md` | 45,059 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `sockets-phase1-GATES-REPORT.md` | 11,010 B | `docs/design/host-service-sockets/RELEASE-PLAN-phase1-2026-11-07.md` |
| `swap-bench-window.draft.sh` | 16,779 B | `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md` |
| `test-methodology-REPORT.md` | 21,360 B | `docs/design/test-policy/REQUIREMENTS-2026-11-09.md` |
| `test-methodology-REVIEW.md` | 31,555 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `test-methodology-REVIEW2.md` | 35,192 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `test-methodology-REVIEW3.md` | 26,618 B | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `v1001-reinstall-EXEC-LOG.md` | 13,779 B | `libraries/iobench/BACKLOG.md` |

### Tracked TrueNAS evidence omission facts

The tracked `scripts/truenas/docs/evidence/truenas-live-20261029.md` index accounts for 300 source files, 257 retained raw files and 43 deliberate omissions. The omissions are: `live-env.sh` (credential-bearing environment file); `v1001-update-to-v28-r2.py` (one-off helper); `v27-login-page.aria.yml` (generated selector snapshot); 18 extracted middleware files under `v27-nightly-20261027-dryrun/`; 19 extracted upstream files under `v27-nightly-20261027-stock-sources/`; and `v28-dataset-query-utils-20261027.py`, `v28-partition-shim-targets-20261027.tar.gz`, and `v28-targets-20261027.tar.gz` (three duplicated snapshots). The dry-run and upstream source member lists are preserved in the source inventory detail below. The environment file was not read or copied.

Dry-run members (18): `middlewared/async_validators.py`, `middlewared/plugins/container/container.py`, `middlewared/plugins/container/dataset.py`, `middlewared/plugins/container/image.py`, `middlewared/plugins/container/lifecycle.py`, `middlewared/plugins/container/utils.py`, `middlewared/plugins/disk_/availability.py`, `middlewared/plugins/pool_/dataset.py`, `middlewared/plugins/pool_/dataset_info.py`, `middlewared/plugins/pool_/dataset_query_utils.py`, `middlewared/plugins/pool_/export.py`, `middlewared/plugins/pool_/import_pool.py`, `middlewared/plugins/pool_/pool.py`, `middlewared/plugins/vm/info.py`, `middlewared/plugins/vm/vm_devices.py`, `middlewared/plugins/zfs/utils.py`, `middlewared/utils/libvirt/storage_devices.py`, `truenas_pylibvirt/domain/vm/xml.py`.

Upstream-source members (19): `middlewared/async_validators.py`, `middlewared/plugins/boot.py`, `middlewared/plugins/container/container.py`, `middlewared/plugins/container/dataset.py`, `middlewared/plugins/container/image.py`, `middlewared/plugins/container/lifecycle.py`, `middlewared/plugins/container/utils.py`, `middlewared/plugins/disk_/availability.py`, `middlewared/plugins/pool_/dataset.py`, `middlewared/plugins/pool_/dataset_info.py`, `middlewared/plugins/pool_/dataset_query_utils.py`, `middlewared/plugins/pool_/export.py`, `middlewared/plugins/pool_/import_pool.py`, `middlewared/plugins/pool_/pool.py`, `middlewared/plugins/vm/info.py`, `middlewared/plugins/vm/vm_devices.py`, `middlewared/plugins/zfs/utils.py`, `middlewared/utils/libvirt/storage_devices.py`, `truenas_pylibvirt/domain/vm/xml.py`.

## Archive summaries for the operator

The current metadata totals are 103 MiB / 2,159 files under `r1002/` and 906 MiB / 18,862 under `v1001/`. The five operator-deleted backup directories are absent. `r1002/r2-closeouts-20261105/` is 24 MiB; `r1002/runner-scripts-local-20261112/` is 28 KiB; `v1001/r2-handover-20261108/` and `v1001/r2-handover-s4-20261108/` are 458 MiB and 447 MiB. The runner bundle `r1002/runner-20261112/vbpub-r1002-src-20261112.bundle` still appears in `ls` at 77,569,459 bytes; it is not one of the deleted backups. The operator decision reports seven bundle deletions but its path list is unavailable in the material reviewed here; the current name-only bundle listing is recorded in the Class B section. No bundle contents were read. Private files and runbooks are excluded from size details.

The s13 final Assay campaign planned and completed 277 candidates: 274 killed and three survived, with no crashes, hangs or budget exceedances. The evidence is marked incomplete because coverage was not reverified. Three of the six source final files match their tracked raw counterparts byte-for-byte; the three remaining JSON/JSONL files are indexed in their scrubbed repo form. Intermediate baseline-flake and guard-stopped runs remain ephemeral.

The s4 final campaign planned 305 candidates, completed 304, killed 283, recorded 21 survivors and one budget exceedance, with one candidate pending. It is marked incomplete because coverage was not reverified and the lane timed out or did not start. Three of the five source final files match their tracked raw counterparts byte-for-byte; two are indexed in their scrubbed repo form. Aborted, paused and duplicate host-copy runs remain ephemeral.

The 60 MiB Augeas spike has versioned source/build/install trees and generated build output, not a project design record. The 88 KiB successor directory has extracted/prose output under five task prefixes, including empty extraction/error files. Their owners must decide whether any still-unrecorded reasoning is worth moving into a project-owned design before the program directory is purged.

## Cross-wave references

The destination paths, source hash prefixes, tracked backlog identifiers, and linked evidence indexes above provide the repository-side proof needed for each disposition. The reconciled Assay, Cowtree and lane-audit documents already appear in their project docs indexes; this inventory is linked from the root README.

## Wave 3 citation disposition — 2026-11-13

This is a documentation-only first slice. The 15 report sources listed below
were copied, scrubbed, indexed under the root cross-project reports directory,
and their citations now point to repository paths. The separately cited
Nyxloom readback report was copied to its project trove. PWMCP's cited review
output was copied to its project docs/reports directory. The large IOBench and
Assay jobs-pilot evidence sets now have one-page summaries; their citing
sentences point to those summaries. No source was deleted.

The wave 2 table has 53 source rows. Its original scan found 70 tracked
citer-file/source pairs, excluding this inventory and source provenance
headers. Nineteen pairs now point to repository copies; 51 baseline pairs
across 38 remaining source files still need a separate scrub, copy, citation
rewrite, project index update and registered docs-sync gate. The copied
reports add four pairs to four of those files and three pairs to three
additional report files, leaving 58 pairs across 41 pending files. The
remaining matrix and secondary-source table below record the exact final-head
citers. Three tracked citations to `nyx-extract-readback-REPORT.md` were
outside the original table and now point to its repository copy.
Across the baseline, Nyxloom and secondary report sources, the final tracked
set has 80 citer-file/source pairs: 22 now point to repository sources or
summaries and 58 remain pending.

### Report sources persisted in wave 3

| Source | Repository copy | Rewritten citer file(s) |
|---|---|---|
| `AGENT-RULES.md` | `docs/reports/AGENT-RULES.md` | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `DSTDNS-R2-PILOT-CONTINUATION-BRIEF-2026-11-04.md` | `docs/reports/DSTDNS-R2-PILOT-CONTINUATION-BRIEF-2026-11-04.md` | `docs/design/dstdns/R2-PILOT-2026-11-04.md` |
| `ad2-measurements-REPORT.md` | `docs/reports/ad2-measurements-REPORT.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md`, `docs/design/telemetry-platform/DESIGN-v2-2026-11-07.md` |
| `assay-jobs-audit-REVIEW.md` | `docs/reports/assay-jobs-audit-REVIEW.md` | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md` |
| `assay-tmpfs-CONTINUATION.md` | `docs/reports/assay-tmpfs-CONTINUATION.md` | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md` |
| `augeas-extension-FEASIBILITY.md` | `docs/reports/augeas-extension-FEASIBILITY.md` | `docs/design/host-setup/DESIGN-rev3-2026-11-08.md`, `docs/design/hostcfg/DESIGN-AUGEAS-AND-BOUNDARIES-2026-11-07.md` |
| `dstdns-p4-REVIEW.md` | `docs/reports/dstdns-p4-REVIEW.md` | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `mdt-dedicated-mem-REPORT.md` | `docs/reports/mdt-dedicated-mem-REPORT.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md` |
| `nyx-3-REPORT.md` | `docs/reports/nyx-3-REPORT.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `reinstall-READINESS-20261105.md` | `docs/reports/reinstall-READINESS-20261105.md` | `docs/design/mdtd/DESIGN-rev4-2026-11-07.md` |
| `rg-154-REPORT.md` | `docs/reports/rg-154-REPORT.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `rigor-chain-REVIEW.md` | `docs/reports/rigor-chain-REVIEW.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `rigor-merge-prep-REPORT.md` | `docs/reports/rigor-merge-prep-REPORT.md` | `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md` |
| `sockets-phase1-GATES-REPORT.md` | `docs/reports/sockets-phase1-GATES-REPORT.md` | `docs/design/host-service-sockets/RELEASE-PLAN-phase1-2026-11-07.md` |
| `test-methodology-REPORT.md` | `docs/reports/test-methodology-REPORT.md` | `docs/design/test-policy/REQUIREMENTS-2026-11-09.md` |
| `nyx-extract-readback-REPORT.md` (additional source) | `nyxloom/nyxloom-trove/reports/nyx-extract-readback-REPORT.md` | `nyxloom/nyxloom-trove/backlog/NL-57-session-extract-since-iso-time-and-relative-duration.md`, `nyxloom/nyxloom-trove/backlog/NL-58-session-extract-include-agent-communications-optionally.md`, `nyxloom/nyxloom-trove/backlog/NL-59-session-extract-readback-preset-with-token-budget.md` |

The previously persisted `assay-tmpfs-REPORT.md` citations now point to
`assay/docs/evidence/assay-tmpfs-REPORT.md`; line references account for that
copy's existing provenance header. The prior KSM and IOBench report copies
remain indexed in their owning projects.

### Remaining report sources — PERSIST pending scrub and project gates

These 38 source files are all under 65 KiB in the wave 2 metadata. They remain
outside Git until copied and scrubbed. Their current tracked citer files are
listed to make the remaining work exact.

| Source basename | Current citer-file count | Tracked citer file(s) |
|---|---:|---|
| `FLUXER-VERIFICATION-2026-11-04.md` | 1 | `voice-screen-share/fluxer-server/README.md` |
| `R1002-REINSTALL-2026-11-04.md` | 1 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `assay-jobs-audit-REPORT.md` | 4 | `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `docs/design/parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md`, `libraries/cli-extended/assay.toml`, `pwmcp/assay.toml` |
| `assay-rust-docs-REPORT.md` | 2 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `docs/reports/test-methodology-REPORT.md` |
| `cgpd-rollout-REPORT.md` | 2 | `cgprofile/nyxloom-trove/backlog/CP-57-cgpctl-doctor-caller-group-fails-for-root-so-check-sh-and-the-root-install-run-always-report-a-failure.md`, `cgprofile/nyxloom-trove/backlog/INDEX.md` |
| `ciu-survivors2-REPORT.md` | 1 | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `di2-ksm-sleep-REVIEW.md` | 1 | `scripts/debian-install-v2/TODO.md` |
| `div2-operator-scripts-v2.patch` | 3 | `scripts/debian-install-v2/docs/evidence/div2-reinstall-RUNBOOK.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md`, `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md` |
| `div2-reinstall-v1001-RESULT.md` | 1 | `libraries/iobench/BACKLOG.md` |
| `dstdns-cp0-CONTINUATION.md` | 1 | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-cp0-REPORT.md` | 1 | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-e2e-REPORT.md` | 1 | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `dstdns-parallelism-REPORT.md` | 3 | `docs/design/dstdns/DEVELOPMENT-PLAN-2026-11-04.md`, `docs/design/parallelisation/DESIGN-MEMO-2026-11-05.md`, `libraries/rigor-record/docs/DESIGN-MEMO-2026-11-05.md` |
| `fix-r2-lanes-CONTINUATION.md` | 1 | `run-gate-project/docs/evidence/fix-r2-lanes-REPORT.md` |
| `flex-r2-REPORT.md` | 1 | `assay/nyxloom-trove/4-backlog.md` |
| `gate-reuse-INVESTIGATION.md` | 2 | `docs/design/test-policy/REQUIREMENTS-2026-11-09.md`, `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `gate-reuse-REVIEW.md` | 1 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `hc4-REPORT.md` | 1 | `nyxloom/tests/fixtures/stall/clean.exec.jsonl` |
| `host-compute-REPORT.md` | 1 | `libraries/rigor-record/docs/DESIGN-MEMO-2026-11-05.md` |
| `host-parallel-r2-REPORT.md` | 1 | `assay/nyxloom-trove/4-backlog.md` |
| `host-pipeline-PLAN.md` | 1 | `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `hosts-cow-ksmd-REPORT.md` | 1 | `assay/nyxloom-trove/4-backlog.md` |
| `ib-vq-REPORT.md` | 2 | `libraries/iobench/BACKLOG.md`, `libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `ib-vq-controller-REPORT.md` | 2 | `libraries/iobench/BACKLOG.md`, `libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `ib-vq-rerun-REPORT.md` | 2 | `libraries/iobench/BACKLOG.md`, `libraries/iobench/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `iobench-iogovmon-DESIGN-2026-11.md` | 1 | `scripts/debian-install-v2/TODO.md` |
| `mt11-iobench-LIVE-REPORT.md` | 1 | `libraries/iobench/docs/SPEC.md` |
| `pytest92-audit-REVIEW2.md` | 1 | `topos/nyxloom-trove/4-backlog.md` |
| `rg-102-C0-SPIKE.md` | 1 | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` |
| `rg-flake-REVIEW.md` | 1 | `nyxloom/tests/fixtures/stall/review-rg-flake.rollout.jsonl` |
| `rigor-rr2a-CONTINUATION.md` | 2 | `libraries/rigor-record/CHANGES.md`, `docs/reports/rigor-merge-prep-REPORT.md` |
| `rust-r0-REPORT.md` | 1 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `session-audit-3.md` | 1 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `swap-bench-window.draft.sh` | 2 | `scripts/debian-install-v2/docs/evidence/host-reinstall-PREP-REPORT.md`, `scripts/debian-install-v2/docs/evidence/swap-bench-window-REVIEW3.md` |
| `test-methodology-REVIEW.md` | 1 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` |
| `test-methodology-REVIEW2.md` | 2 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `docs/reports/test-methodology-REPORT.md` |
| `test-methodology-REVIEW3.md` | 2 | `run-gate-project/docs/DESIGN-lane-rework-2026-11.md`, `docs/reports/test-methodology-REPORT.md` |
| `v1001-reinstall-EXEC-LOG.md` | 1 | `libraries/iobench/BACKLOG.md` |

### Additional small report sources discovered in copied reports

These three report basenames were not in the wave 2 source table. They are
small text reports and remain PERSIST-pending; their sources must stay outside
until copied with the same scrub/header/index process and each citation is
rewritten to the repository copy.

| Source basename | Source size | Tracked citer file(s) |
|---|---:|---|
| `ASSAY-JOBS-PILOT-2026-11-02.md` | 8,071 B | `assay/docs/evidence/ASSAY-JOBS-PILOT-ANALYSIS-2026-11-02.md` |
| `assay-RUST-ADAPTER-DESIGN.md` | 35,204 B | `docs/reports/test-methodology-REPORT.md` |
| `hostobs-DECISIONS-20261107.md` | 7,224 B | `docs/reports/test-methodology-REPORT.md` |

### Other wave 3 cited items

| Program-dir item | Disposition and tracked citer |
|---|---|
| `reviews/pwmcp2.out` (27,593 B) | PERSISTED as `pwmcp/docs/reports/pwmcp2-review-2026-10-30.md`; all six citations in `pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md` now point there. |
| `evidence/iobench-r2-r1002/` (133 files / 31,058,728 B) | KEEP-OUTSIDE-WITH-SUMMARY at `libraries/iobench/docs/reports/IOBENCH-R2-R1002-EVIDENCE-SUMMARY-2026-11-13.md`; `libraries/iobench/BACKLOG.md` cites the summary, not the raw tree. |
| `evidence/jobs-pilot3/` (1,179 files / 23,655,197 B) | KEEP-OUTSIDE-WITH-SUMMARY at `assay/docs/evidence/JOBS-PILOT3-EVIDENCE-SUMMARY-2026-11-13.md`; the Assay analysis cites the summary, not the raw tree. |
| `evidence/truenas-live-20261029/`, `evidence/truenas-live-20261030/`, `evidence/truenas-live-20261031/` | KEEP-OUTSIDE-WITH-SUMMARY pending a one-page summary and rewrite of the exact external-path citations. The 2026-10-29 index already records 300 source files, 257 retained raw files and 43 deliberate omissions; no wave 3 summary was added. |
| `controller/ib-vq-rerun/` | KEEP-OUTSIDE-WITH-SUMMARY pending an owning-project summary and rewrite of `libraries/iobench/BACKLOG.md`. |
| `r2-session/` | KEEP-OUTSIDE-WITH-SUMMARY pending summaries for its two tracked citing reports and a citation rewrite; raw campaign/session state remains outside. |
| `dstdns-r2/` | KEEP-OUTSIDE-WITH-SUMMARY pending a one-page summary and path rewrite for the six tracked citation files in the wave 2 row above. |
| `logs/host-sat-*` and queue state | RUNTIME-DEPENDENCY under `CONTROLLER_HOME`; CTL-02 records that the code works with another home when required state is migrated. |
| pre-reinstall and remote-gate paths | RUNTIME-DEPENDENCY limitations are recorded in NC-54: pre-reinstall helpers use a fixed root; remote-gate output has an environment/argument override. |
| TrueNAS acceptance lock | RUNTIME-DEPENDENCY limitation is recorded in TN-17: `accept.py` uses a fixed `v1001.lock` location with no alternate-home setting. |

The new raw-evidence summaries state what was captured, when, the source
revision and tool configuration, key results and a regeneration path. The
IOBench summary records the historical lane/tool versions and cap; the
Assay summary records the arms, candidate comparison, raw file count and the
limitation that the original pilot launcher/config is still in the external
evidence set.

## Purge readiness

This table supersedes the wave 2 Result column for the wave 3 final head. A
top-level entry marked KEEP can contain individual files whose citations are
now fully persisted. “Purgeable now” is a source classification only; this
task deleted nothing and does not authorize deleting program-directory data.

| Class B top-level entry | Wave 3 citation/persistence disposition | Purge readiness |
|---|---|---|
| `ciu/` | UNCITED unique content; operator review remains. | Keep — not classified for purge. |
| `codex/` | KEEP — controller run state, live worktree markers and unmerged state are still cited. | Keep — continuity/runtime state. |
| `controller/` | KEEP-OUTSIDE-WITH-SUMMARY — the cited `ib-vq-rerun/` raw evidence has no summary yet. | Keep — summary and citation rewrite remain. |
| `dstdns-r2/` | KEEP-OUTSIDE-WITH-SUMMARY — large raw evidence has no wave 3 summary. | Keep — cited source files remain. |
| `evidence/` | IOBench and Assay jobs-pilot have summaries and rewritten citations; three TrueNAS evidence sets remain pending summary/path rewrites. | Keep — TrueNAS source evidence remains. |
| `gate2.lock` | RUNTIME-DEPENDENCY — controller gate serialization state under the configured controller home. | Keep — needed while controller gates use this home. |
| `gate.lock` | RUNTIME-DEPENDENCY — controller gate serialization state under the configured controller home. | Keep — needed while controller gates use this home. |
| `HANDOFF-STATE-20261105.md` | KEEP — cited controller continuity. | Keep — continuity source remains outside. |
| `HANDOFF-STATE-20261107.md` | KEEP — cited controller continuity. | Keep — continuity source remains outside. |
| `HANDOFF-STATE.md` | KEEP — cited controller continuity. | Keep — continuity source remains outside. |
| `host-sat-queue.disabled.r1002` | RUNTIME-DEPENDENCY — per-host pause state is read under `CONTROLLER_HOME`; CTL-02 records migration limits. | Keep — preserve current queue state. |
| `host-sat-queue.disabled.v1001` | RUNTIME-DEPENDENCY — per-host pause state is read under `CONTROLLER_HOME`; CTL-02 records migration limits. | Keep — preserve current queue state. |
| `host-sat-queue.README.md` | PERSISTED to `scripts/netcup/docs/HOST-SAT-QUEUE.md`; source header was its only citer. | Purgeable now — source copy is in the owning project. |
| `host-sat-queue.README.md.nc28-backup-20261109T190502Z` | UNCITED backup; no source copy or owner review. | Keep — operator classification required. |
| `host-sat-queue.sh` | KEEP — SHA-256 differs from the tracked runner script; NC-53 still tracks deployed-script synchronization and profile verification. | Keep — distinct source/runtime version has not been reconciled. |
| `host-sat-queue.sh.nc28-backup-20261109T190502Z` | UNCITED backup; no source copy or owner review. | Keep — operator classification required. |
| `ib-vq-scripts/` | UNCITED unique benchmark-driver scripts. | Keep — operator review required. |
| `iobench` | UNCITED unique source item. | Keep — operator review required. |
| `logs/` | RUNTIME-DEPENDENCY under `CONTROLLER_HOME`; status works from another home if queue logs/markers/flags are migrated. | Keep — current status history/state is not migrated automatically. |
| `pilot/` | UNCITED unique pilot material. | Keep — operator review required. |
| `R2-PORTFOLIO-PLAN.md` | PERSISTED as `docs/design/r2-portfolio/R2-PORTFOLIO-PLAN-2026-10-29.md`; the citing design now points there. | Purgeable now — repo copy and citations are in place. |
| `r2-session/` | KEEP-OUTSIDE-WITH-SUMMARY — two tracked citing reports still need summaries and rewrites. | Keep — raw session evidence remains cited. |
| `releases-archive/` | KEEP — nine branch bundles have heads not in origin/main; they are not persisted as Git history. | Keep — unique Git bundle history remains. |
| `remote-gates/` | RUNTIME-DEPENDENCY/output archive — remote-gate can override its default output root, but several raw logs are still cited and unsummarized. | Keep — raw remote-gate evidence and outputs remain. |
| `reports/` | PERSIST-PARTIAL — 16 source reports persisted in this slice; 41 small cited files and 58 citer-file/source pairs remain. Previously copied KSM, IOBench and Assay tmpfs reports remain linked. | Keep — remaining source files are cited. |
| `reviews/` | `pwmcp2.out` is PERSISTED; the rest of the 147-entry tree was not reviewed. | Keep — other review artifacts remain unclassified. |
| `v1001.lock` | RUNTIME-DEPENDENCY — fixed path with no override; TN-17 records the limitation. | Keep — acceptance script still requires this lock location. |
| `vm-cgp-accept-20261107/` | UNCITED unique acceptance material. | Keep — operator review required. |
| `WAVE-BRIEF.md` | UNCITED controller continuity brief. | Keep — controller continuity remains outside. |
