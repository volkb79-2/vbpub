# Release-wave review: HEAD `ad679a15a` plus 36 uncommitted files

This was read-only: no repository files changed, no tests, gates or containers. The plan file `/home/vscode/.claude/plans/you-are-performing-a-functional-fern.md` holds a condensed copy of this review. Every gate status comes from backlog text. "Proven" means read in code, config or a tag; "inference" is marked.

**Verdict: not releasable yet.** There are three blockers: nothing is gated at the exact tip, CMRU will refuse to tag two of the packages, and the Assay release would be mislabelled as a minor version. CMRU 6.2's restored full gate also brings in a mutation engine that can report false kills. B110 cannot reach GO at one worker; that matters only if qualification is claimed.

## Findings

### BLOCKER

**B1. Nothing in this candidate is gated at its exact tip.**
- 36 files are uncommitted, so no gate can have covered them.
- The backlog marks B151–B161 "exact-tip gate pending", B132's 70-case PostgreSQL hazard gate pending (its own entry requires it before merge or release), and RG-84's registered selftest pending.
- **Action:** commit per package, rebuild tester-unified, then run each package's registered gates (list in the release plan below).

**B2. `cmru release` will refuse Run-Gate 23.11 and cgprofile 1.0.0.** (Proven.)
- `cmru/src/cmru/changelog.py:341-348` refuses a tagged release while a plain `## [Unreleased]` section has a non-empty body (CMRU KI-30). The default changelog is `CHANGES.md` (`cmru/src/cmru/config.py:1109`).
- `run-gate-project/CHANGES.md:9-31` and `scripts/cgroup-profiler/CHANGES.md:9-…` both carry hand-written bullets. At `run-gate-v23.10.0` that body was only a comment.
- **Oracle:** each project's release preflight passes only after its body is emptied and the content folded into commits or docs.

**B3. The v16 verdict hard cut would ship as a minor release (9.1.0).** (Proven.)
- `assay/src/assay/verdict.py:401` sets the verdict schema to 16; it is 15 at `assay-v9.0.0`. `assay/src/assay/verify.py:3596` rejects any other version, and `docs/CONSUMERS.md:3913` calls v16 a hard cut.
- 9.0.0 shipped the v15 cut with a "BREAKING CHANGE" section and a major bump.
- Assay uses conventional bumps (`assay/cmru.toml:18-19`), and none of the 70 commits since `assay-v9.0.0` carries a breaking marker. CMRU would therefore compute 9.1.0.
- **Failure:** consumers adopt it as a compatible minor, then every retained v15 verdict stops verifying and v15 reuse cold-starts. Publication cannot be undone.
- **Action, pick one:**
  - release as 10.0.0 with a BREAKING note;
  - drop the v16 schema constant and keep `jobs = 1` purely as loader/CLI policy. I recommend this one.

### HIGH

**H1. CMRU 6.2's restored full gate runs a mutation engine with false-kill paths.** (Proven.)
- The release step now runs `./run-gate.py gate` (`cmru/cmru.toml:49`). Its mutation lane (`cmru/run-gate.toml:92-121`) uses CMRU's own loop, `cmru/tools/mutation_campaign.py:127-135`:
  - a timeout counts as **killed**;
  - exit code 1 counts as killed;
  - there is no cgroup resource-event guard and no cold witness.
- That is exactly the B145 false-kill class that Assay closed. Run-Gate's container gives this lane `--init` but no event check.
- **Oracles:**
  - a mutant whose test fails with EAGAIN under a low PID limit must not be counted as killed;
  - a hanging mutant must not be counted as killed.
- **Fix:** move the lane onto Assay native R2 with the observer, or at minimum treat timeouts as not killed and read the container's `pids.events` and `memory.events` around each candidate (`tester_gate.py:164-169` already has that wrapper). Otherwise state explicitly that CMRU's mutation evidence is not B145-guarded.

**H2. Shared-ancestor and memory-`max` events turn ordinary pressure into lane ERROR.** This is an availability risk, not a false-PASS risk. (Proven code; frequency is inference.)
- `assay/src/assay/resource_limits.py:330-349`: for every one of the five counters, including memory `max`, an ancestor-local delta in the candidate's window with zero candidate delta raises a lane-level observation error. A candidate's own delta makes it `crashed`.
- Kernel semantics: `max` counts every charge that hits a limit and triggers reclaim. Page cache filling a capped container, a sibling gate under the shared `dev-gates.slice` memory limit, or cockpit work under `dev.slice` can all produce these events.
- **Verification:**
  - run a native R2 lane with a memory cap while (a) its own page cache fills the cap, and (b) a sibling fills the gates slice to its limit;
  - count `crashed` candidates and observation errors;
  - record a policy decision before claiming estate-wide R2 can run.

**H3. B110 GO cannot be met at one worker, and the plan still assumes three.** (Proven documents; arithmetic is inference from the measured analysis.)
- `assay-B110-PLAN-2026-09-28.md`:
  - line 125 (decision D10) allows `--pilot-jobs` from 1 to 8;
  - lines 394-395 run `--pilot-jobs 3` on a 3-worker queue;
  - line 416 divides by "effective workers";
  - line 485 says to commit the pilot-measured `jobs` value.
- `b110/P7-pilot-tooling.md:752,771` match. The CLI refuses anything but 1 (`cli.py:738-747`).
- At one worker, 3,760 candidates within ≤ 5 h leave about 3.8 s per candidate. The analysis measured 9.4–13.4 s of fixed cost per kill. The new P8 projection is pinned to the lane's `jobs = 1` and will show this NO-GO honestly.
- Not a release blocker if no qualification is claimed.
- **Action:** record a D-decision and amend §7/§8/§9.3. Treat the pilot as a measurement that sizes the NO-GO branches (B121 isolation units, B119 remote capacity, per-candidate cgroups).

### MEDIUM

**M1. Backlog statuses misstate what ships.**
- B114 says IN PROGRESS, B118 "pending", and B115 "pending release on the local v9.0.0 candidate". But 9.0.0 already shipped "integrate B114, B118 pilot, and B131" (`assay/CHANGES.md`, 9.0.0 section).
- KI-69 says "open" (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1796`). CP-20 says "carved" and CP-21 "open" (`scripts/cgroup-profiler/nyxloom-trove/backlog/INDEX.md:15-16`). B108 is OPEN while A-490 extends it.
- The release framing is off too: the next Assay release is B132/B133, B151–B161 and P8 v2, not B114/B118/B131.

**M2. Four native R2 lanes still have no observer.**
- ciu (`ciu/run-gate.toml:14` → `ciu/assay.toml:22`), `libraries/worktree`, mdt `host_setup` (`modern-debian-tools-python-debug/run-gate.toml:38`), and pwmcp `r2` (`pwmcp/run-gate.toml:19`).
- They refuse before R0 under B148. This is not a regression, since B148 is already on `main`, but it contradicts the wave's "native R2 observer configs" completeness.
- **Action:** wire them, or mark them not runnable.

**M3. The new observer advertisement isn't used by Run-Gate.**
- `run-gate.py:6958-6999` only checks the socket's file type and ownership. A contract-1 daemon, or one reporting `listening: false`, passes Run-Gate's preflight and only fails inside Assay. That is still fail-closed (before R0), but after the container has started.
- The contract-1 retirement flag day (in-repo consumers pick up Assay from source at merge) is still undecided.
- **Action:** Run-Gate preflight reads `version.resource_observer` and requires contract 2 with `listening: true`; record the deployment order.

**M4. The P8 pilot tests are synthetic and thin.**
- Only 2 new tests, both with hand-built selection documents. The accepted key sets are duplicated in `campaign.py:893-907` and in the two selectors. Drift would fail closed, but nothing would catch it.
- **Add:** a golden fed by real selector output, plus negatives for:
  - foreign plan bytes;
  - out-of-order IDs;
  - an unknown field;
  - selection combined with a shard;
  - selection combined with a verdict;
  - native pilot projection with jobs ≠ 1;
  - an earlier run with a different digest.

**M5. Carried forward, unchanged:**
- the cgprofile push builds a second, ungated image;
- the tester-unified image is unlabelled;
- CMRU `tester_gate` reads only leaf events (`tester_gate.py:814-817`), unlike Assay B145.

### LOW

- **L1.** README and the P8 brief amendment say "`assay plan --json`". `assay plan` has no `--json` flag; it always prints JSON, and `--json` belongs to `assay lanes` (`cli.py:252`).
- **L2.** `_parse_bytes` (`placement.py:622-630`) now refuses integral floats such as `1.0` on control contract 1. That matches PROTOCOL's `integer`, and Run-Gate sends ints (`run-gate.py:4508-4532`). Third-party JSON clients sending `1e9` now get a bad-argument error; worth one line in PROTOCOL/CHANGES.
- **L3.** `version.resource_observer.listening` only means the socket bound (`serve.py:791`). An observer bind failure is just logged and the daemon keeps running (`serve.py:2420-2425`).

### Resolved since the two earlier reports
- **All 8 cgprofile survivors are addressed:**
  - 495 removed;
  - both 639 mutants by the split condition plus test 2065;
  - 678 simplified;
  - 627 made integer-only, with a `1.0` refusal test;
  - 709 by a new symlink test;
  - 501 by test 2046.
  - **Correction:** 377 was already covered by `test_serve.py:139` (2026-10-12); my follow-up report was wrong there.
- `gate.sh` now passes `--init --pids-limit=8192` to both of its containers, and the test asserts it.
- The daemon advertises the observer contract in `version`, and the CLI validates it without breaking older daemons.
- **P8 now analyses both pilots and binds them correctly** (details in §2 below). Native pilot projections must use the lane's `jobs`; non-pilot projections still accept more than 1.

## Answers by review level

### 1. Product and design contract
- **Profiling versus observation.** R2 needs only *event observation* (candidate and ancestor `pids.events`/`memory.events`), not cgprofile sessions or metrics.
- **Can R2 run without observation?** Refusing before R0 (B148) is correct for qualifying runs. Without ancestor counters, an ancestor-enforced fork or memory refusal is indistinguishable from a test failure, which means a false kill and possibly a false PASS. A cold witness narrows this but cannot remove it: a refusal inside a test's call phase is still a call-phase failure. A non-qualifying "unattributed" mode for development feedback is a reasonable backlog item, not this release.
- **What "candidate" means (proven).** The "candidate" cgroup is the whole tester container (Assay, its shims, the init process). With `jobs = 1` the window is one candidate's `execute_plan`, so this is acceptable but misnamed. Ancestor-*local* events mean a limit enforced at a shared parent (`rg-<token>`, `dev-gates.slice`, `dev.slice`) that siblings also charge.
- **Can sibling activity be misclassified? Not into a mutant outcome.** An ancestor event with no candidate delta aborts the lane; a candidate delta gives `crashed`. Both roll up to ERROR, never PASS. Sibling activity can, however, cause false lane ERRORs (H2).
- **Metrics are an infrastructure veto, not correctness evidence (proven).** `_classified_bucket` only ever downgrades. That asymmetry is correct.
- **Residual gap (inference):** only cgroup limits are guarded. ENOSPC, RLIMIT and file-descriptor exhaustion can still produce kills that are not real.

### 2. Assay B110/B131 path
- **Usefulness and labelling.** The pilot is useful as a measurement and cannot be mistaken for qualification. Output is `qualifying: false`, `incomplete`, has no verdict and exits 3, and the schema enforces this (`analysis-campaign.schema.json`, the new conditional blocks).
- **Binding is sufficient and compatible (proven):**
  - raw plan bytes match the selector's `plan_sha256`;
  - plan rows equal the current planner's rows;
  - selected IDs are unique, in-plan and in plan order;
  - `selection_sha256` equals `plan_sha256(selected)`, which is exactly what the runner records (`mutation.py:3744-3768`);
  - each run's progress digest must match;
  - the latest run's candidates must be a subset of the selection;
  - key sets match both selectors exactly (`b110_pilot_select.py:909-925`, `analysis_r2_pilot_select.py:365-382`);
  - `--cold-witness` does not change plan rows.
- **One-worker limit:** see H3.
- **B110 P0–P11 prerequisites:**
  - **Done:** B111 and B113. B114 and B118 shipped in 9.0.0, but their backlog statuses are stale (M1). B115 says "pending release", but I could not find it in the 9.0.0 notes, so its shipped status is unresolved.
  - **Open or contradicting B110's acceptance list:** B108 phase 1 (P8) is listed as merged, but B108 is OPEN; B112 (P1) is partial.
  - **Gated on a later decision:** B116 (P5) is conditional on measured need; B119 (P9), B120 (P10) and B121 (P11) are open; B121 is dispatched only after a NO-GO.

### 3. cgroup-profiler and Run-Gate
- **Correct:**
  - the contract-2 interface and the observer version advertisement;
  - stale-socket handling;
  - the survivor repairs;
  - `gate.sh` init and PID cap;
  - all wired lanes run Assay from source through the symlinked `run-gate.py`, so the `resource_observer` key is understood.
- **No false refusal from the integer tightening:** Run-Gate sends ints.
- **Gaps:**
  - M2 (unwired lanes);
  - M3 (version advertisement unused, flag day undecided);
  - B2 (changelog refusal);
  - the exact-tree contract-2 R2 must be re-run only if contract-2 R2 qualification is claimed. 1,012 candidates were never accounted, and v16 cold-starts reuse.
- **New operator burden:** observer-wired lanes now fail as infrastructure on any host without the daemon socket. This is fail-closed and acceptable, but should be documented.

### 4. Release and adoption
- **Assay:** the version decision is essential (B3).
- **Both Run-Gate and cgprofile:** the changelog fix is essential (B2).
- **CMRU 6.2's full-gate change** is sound in intent, but it makes H1 release-relevant and adds a gate of about 14 h.
- **Safe to defer:**
  - M5 (CP-21 still builds from the exact source, so identity holds);
  - L1–L3;
  - the four unwired lanes, if recorded.

### 5. Backlog review (not blockers in this candidate)
**Language-support parity is a real gap on current `main` (`aa41988`, the review base):**
- Only Python has cause-sensitive kill proof (cold witness).
- SQL native R2 classifies kills by exit status plus the equivalence artifact. It is subject to B148, yet the database container's resource events are unobserved (inference).
- JS R2 is ingestion-only. It trusts the producer's "Killed" with no environment attestation, though Timeout is correctly mapped to `budget_exceeded` (`mutation.py:5012-5032`).
- Go has no R2 or R3.
- The README matrix (`assay/README.md:402-408`) does not show this difference in kill-evidence strength.

**Other priorities:**
- **Assay:** H2 probe and policy; B120 equivalence ledger; B110 decision.
- **Run-Gate:** observer contract preflight; pending RG-84 and RG-89 acceptance.
- **cgprofile:** push the gated image; observer liveness reporting.
- **CMRU:** H1; leaf-only `tester_gate` events.

## Minimal release and gate plan
1. Decide the Assay version: no v16 schema, or 10.0.0 with a BREAKING note.
2. Empty the Run-Gate and cgprofile `[Unreleased]` bodies.
3. Commit per package. Fix the M1 statuses and the L1 wording. Amend the B110 plan and record the D-decision.
4. Rebuild tester-unified and record its image ID.
5. Run the registered gates on the exact tip:
   - Assay `tester-unified` plus the B132 PostgreSQL hazard set;
   - Run-Gate selftest;
   - cgprofile `r0-r1` and `r3`.
6. Release cgprofile 1.0.0 (`--set-version 1.0.0`). Pin it in `ciu.toml.j2`, run `ciu up`, and verify `version.resource_observer = {contract: 2, listening: true}`. Announce the observer flag day.
7. Release Run-Gate 23.11, then Assay. Install the wheel in the cockpit venv, verify it, and notify dstdns with the sha256.
8. Release CMRU 6.2 last, after fixing H1 or explicitly documenting it, with its full `gate`.

## Three most valuable next backlog chunks
1. **One attributable cgroup per candidate (observer contract 3).** The daemon creates a candidate leaf, places the PID, and returns local counters. This lifts B157, makes B110 feasible, and removes the sibling aborts behind H2. Decide the memory-`max` policy from the probe first.
2. **One R2 engine, one false-kill policy.** Retire CMRU's bespoke mutation loop onto Assay native R2 with the observer. Add a kill-evidence-strength column to the language matrix (Python, SQL, JS ingest, Go).
3. **Close B110 honestly.** Run the pilot as a measurement and analyse it with P8 v2. Record the NO-GO, choose between B121, B119 and chunk 1, and land B120, which any consumer with equivalent survivors needs.

The session is kept for follow-up questions.
CLAUDE_EXIT=0

## Review session record

- Reviewer: Claude Opus 5.5, high
- Persistent session: `28227ce4-cbdc-471a-8b69-a430af63fb67`
- Session log: `/home/vscode/.claude/projects/-workspaces-vbpub--worktrees-cgprofile-cp20-contract2-20261012/28227ce4-cbdc-471a-8b69-a430af63fb67.jsonl`
- Reviewed candidate HEAD: `ad679a15afd449a6752d42782edaa7a5e0c8461c`
- Review output was read-only; HEAD and worktree status were verified unchanged afterward.
