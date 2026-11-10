<!-- origin: 2026-11-01; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/WORKTREE-INTEGRATION-PLAN-2026-11-01.md -->
<!-- relation: worktree integration plan -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Worktree integration plan: the 14 class-B items (2026-11-01)

Base for every comparison: `origin/main` = local `main` = `5823d31f1`. Every claim below was checked
read-only with `git log/cherry/diff/merge-tree` against that commit plus the current backlogs. No test,
gate, Docker or ref change was made. Operator decisions already on record and applied here: 2026-10-21
"all as recommended" (RG-56 file-and-drop, assay-b099 drop; cmru-inst62 and requirements-prefix left to
the operator), 2026-10-29 (whole-source cmru R2 for KI-79/KI-99 deferred).

Verdicts: **4 INTEGRATE-NOW**, **4 INTEGRATE-AFTER-DECISION**, **1 RE-CARVE**, **5 DROP**.
Questions: `WORKTREE-OPERATOR-INTERVIEW-2026-11-01.md` (Q1-Q6). Briefs: `codex/integrate-*-brief.md`.

## Per-item table

| # | Item (tool) | Worktree / tip | Verdict | Effort | Registered gates before merge | Brief / question |
|---|---|---|---|---|---|---|
| 1 | Nyxloom `child.pid` atomic write + SIGTERM test ordering | `.claude/worktrees/wave-nyxloom-20261027` @ `d5dfd5bd5` | INTEGRATE-NOW | S | nyxloom `tester-unified` | `integrate-nyxloom-childpid-brief.md` |
| 2 | CMRU mutation-lane timeout (KI-99 support) | `.worktrees/cmru-r2-timeout-20261029` @ `d45d4b19f` (+ untracked `r2-campaign-project/`) | INTEGRATE-NOW (commit); fixture not ported | S | cmru `assay` | `integrate-cmru-mutation-timeout-brief.md` |
| 3 | MDT runtime bind-dir identity | `.worktrees/mdt-runtime-dir-identity-20261014` @ `98ab21084` | INTEGRATE-NOW (remnant: the `/run/docker-api` render-test block only, into `mdt-cockpit`) | S | mdt-cockpit `smoke` | `integrate-mdt-bindmount-identity-brief.md` |
| 4 | CIU-135 overlay intent | `.worktrees/ciu-overlay-intent-20261013` @ `e52abd9d9` | INTEGRATE-NOW (one review report); code already landed | S | none (trove doc); light review | `integrate-ciu-p53-review05-brief.md` |
| 5 | CIU-134 root retry | `.worktrees/ciu-134-root-retry-20261014` @ `13119ce84` (= `park/ciu-134-root-escalation-20261014`, also on origin) | RE-CARVE | L | ciu `ciu`, libraries/worktree `worktree` | Q3 |
| 6 | PWMCP GlyphGrid fonts + speed | `.claude/worktrees/agent-ac07d8c08375985b5` (branch `integration-fonts-speed`) @ `f851cf741` | INTEGRATE-AFTER-DECISION | M | pwmcp `r1` | Q2 |
| 7 | PWMCP console corpus / OCR phase 2-3 prep | `.claude/worktrees/wave-pwmcp-20261027` @ `ac15a7df1` | INTEGRATE-AFTER-DECISION | M | tester-unified rebuild, then pwmcp `r1` + cmru `assay` | Q1 |
| 8 | CMRU installer alternative (INST62) | `.worktrees/cmru-inst62` @ `18d50209d` | INTEGRATE-AFTER-DECISION (executes as a re-carve) | S/M to L | cmru `gate-provisional`; ciu `enroll` + tls-edge/pwmcp gates if `get.py` re-renders | Q4 |
| 9 | Requirements-file naming | `.worktrees/requirements-prefix-20261014` @ `d4c43b3d5` | INTEGRATE-AFTER-DECISION (redo from main) | S per tool | debian-install-v2 `r0-r1`; assay `self-qualification` (with the next assay release) | Q5 |
| 10 | Assay B103 interruption boundary | `.worktrees/assay-b099-p35-repair` @ `6d34f0d7b` | DROP (archive tag) | archive S | none | Q6 (confirm) |
| 11 | I/O bench series prototype | `.worktrees/io-bench-01-20261020` @ `e6a9a44fe` | DROP (superseded) | archive S | none | none |
| 12 | RG-120 current-source rejudge | `.worktrees/r2-rejudge-source-20261029` (detached) @ `4bff48afc` | DROP branch; finish RG-120 by a targeted rejudge run | run S, wall hours | run-gate `assay-r2` (rejudge) | none (default) |
| 13 | RG-56 admission design | `.worktrees/rg56-admission` @ `92a2c0971` | DROP (archive tag) | archive S | none | Q6 (confirm) |
| 14 | RG-56 admission implementation | `.worktrees/rg56-admission-implementation` @ `84341e758` | DROP (archive tag) | archive S | none | Q6 (confirm) |

## Per-item detail: (a) value, (b) landed since, (c) conflicts, (d) verdict, (e) effort, (f) risk

### 1. Nyxloom `child.pid` / SIGTERM ordering
- (a) `wrapper_main` writes `child.pid` with a plain `write_text`; readers (`effects_lifecycle.py:133-138`, the daemon, tests) test existence and then `int()`-parse, so they can read an empty file. The test failure was `test_sigterm_detached_real`'s `ValueError`, and the production reader has the same race. The commit writes through `child.pid.tmp` plus `os.replace`, and two in-process SIGTERM tests now wait for the child's `starting` line instead of `sleep(0.3)`. It is a real race fix that also removes two timing flakes from the tester-unified lane.
- (b) Main has not changed `wrapper.py` or `test_wrapper.py` since the branch base `0204aa8a3`. Nyxloom 0.11.1 and 0.12.0 shipped without this fix, and nothing supersedes it.
- (c) The in-memory merge is clean (2 files).
- (d) INTEGRATE-NOW. (e) S.
- (f) Low. The brief adds a reader audit (a stray `child.pid.tmp` after a crash) and a deterministic test of the invariant. Gate: nyxloom `tester-unified` (R0+R1, whole-source 100% branch coverage). It folds into the next nyxloom patch release.

### 2. CMRU mutation-lane timeout (KI-99 support)
- (a) The registered `mutation` lane (`cmru/run-gate.toml:214`, part of `gate`) caps the known-good control and each candidate at 120 s. The full cmru suite takes about 439 s in tester-unified, so the control cannot finish and the lane cannot produce R2 evidence. The commit sets 600 s and updates README, CONSUMERS, CONTRIBUTING, DESIGN-GUIDE, SPEC and `tests/test_docs_config_examples.py`. It also adds `tools/test_mutation_campaign.py::test_registered_mutation_timeout_covers_known_good_suite`. The untracked `r2-campaign-project/` (`check_baseline.py`, an ad-hoc `r2-debt` lane) was the harness for the 2026-10-29 diff-scoped run, and its results are already in `cmru-R2-diff-triage-20261029.md`.
- (b) Main still says 120 s in the lane and in all five docs; none of the 8 files has changed since the branch base.
- (c) The in-memory merge is clean.
- (d) INTEGRATE-NOW for `d45d4b19f`. The fixture is not ported: it is an unregistered ad-hoc lane, and the whole-source run it would feed stays deferred (operator, 2026-10-29). (e) S.
- (f) Low. The timeout is part of the campaign resume key, so any existing `.assay/mutation-cmru.json` restarts fresh (intended; no cmru campaign is running). Gate: cmru `assay`. `tools/test_mutation_campaign.py` runs only in the `mutation` lane prelude, so the focused run is the evidence until the next real `gate` run.

### 3. MDT runtime bind-dir identity
- (a) Devcontainers bind-mount `/run/docker-api` and `/run/mdt-buildkitd` as directories. If the host replaces such a directory, running containers keep pointing at the deleted inode. The branch adds `RuntimeDirectoryPreserve=yes`, a unit comment, render-test assertions and docs.
- (b) `modern-debian-tools-python-debug/` is the deprecated tree (main `6b68faa64`: "mdt-cockpit carries the change"). `mdt-cockpit/` already has `RuntimeDirectoryPreserve=yes`, the rewritten `docker-api-socket.conf` comment, the DESIGN-GUIDE "Directory bind-mount identity" section (`docs/DESIGN-GUIDE.md:631`), the CONSUMERS `//deleted` note (`docs/CONSUMERS.md:487`) and the host-setup README note (`host-setup/README.md:786`). The only thing still missing is the `/run/docker-api` render-test block in `mdt-cockpit/host-setup/tests/test-render.sh`.
- (c) Nothing is merged from the branch. The block is added by hand to `mdt-cockpit` only; no file under `host-setup/units/` and nothing in the deprecated tree changes.
- (d) INTEGRATE-NOW (remnant). (e) S.
- (f) Low. Gate: mdt-cockpit `smoke` (runs `mdt-cockpit/host-setup/tests/test-render.sh`, which only renders into a temp dir). The gate log must show the new `ok: Docker API socket restart …` line.

### 4. CIU-135 overlay intent
- (a) A WIP snapshot of the P53 review-05 fixes.
- (b) Landed: `ciu/src` in this snapshot is byte-identical to `f472c62ed` on main, and the backlog marks CIU-135 FIXED (7.17 line, repaired in 7.18). Main's tests are newer. The one unlanded file is `ciu/nyxloom-trove/reports/ciu-P53-REVIEW-05.md` (sha256 `d4cc08c6…8638a9`). Main's `ciu-P53-LOG.md:130` says that report is retained, so the reference is currently dangling.
- (c) None (new file, not ignored).
- (d) INTEGRATE-NOW for the report only; the code part is dropped as landed. (e) S.
- (f) None. Docs-only trove evidence: light review, no gate run needed.

### 5. CIU-134 root retry
- (a) A consented, permission-specific retry of `ciu worktree rm` through the S6.5 root helper, plus a per-phase report. This has high safety value: there are three reproductions, and the failure is systematic for lane-running worktrees (root-owned `.coverage`, `__pycache__`, `.ciu/ksm/*.so`).
- (b) The same implementation landed as `63a696707`, with fix `07090690e` (accept the CIU record that Git already deleted when the shared ledger proves the target). Both were reverted in 7.18 (`a6b4cb524`). Backlog CIU-134 (OPEN, `ciu/KNOWN_ISSUES_TODO_BACKLOG.md:5051`) carries all 8 adversarial findings, with the untracked review's content folded in. The branch's CIU-143 entry is on main as CIU-154. Three more open items sit on the same `rm` path: CIU-151 (no CIU root in an old checkout), CIU-153 (deletes unmerged branches) and CIU-154 (prunes ignored resumable state).
- (c) Not mergeable: the branch is 1063 commits behind, and `worktree.py` plus `libraries/worktree/core.py` were reshaped by schema 5/6.
- (d) RE-CARVE from current main. Reference implementation: park ref `13119ce84` plus `07090690e`. Scope is Q3. (e) L.
- (f) High: a privileged helper deletes host paths, and there is DooD path-namespace translation. Gates: ciu `ciu` and libraries/worktree `worktree`. It needs an adversarial review against the 8 findings, then a ciu release.

### 6. PWMCP GlyphGrid fonts + speed
- (a) Adds:
  - console-extra and ambiguous font atlases (console-setup Terminus/Fixed/VGA, kernel TER16x32), built from a sha256 manifest;
  - a NOTICE with per-font licence evidence;
  - `read_image` about 5x faster with bit-identical output (the reference path is kept);
  - `GlyphReader` for frame sequences;
  - the integer-scale hazard guard (class `z`, `trust_scale`).

  It went through four review rounds in-branch and was "reviewed" per HANDOFF-STATE.
- (b) Main's `pwmcp/tools/glyphgrid` is unchanged since the base merge `adab40335`; nothing supersedes this work.
- (c) The in-memory merge is clean. The only overlap with the sibling unmerged imagegen branch (`worktree-agent-ab4156ad5c7c6cb52`) is `GLYPHGRID-FINDINGS.md`, which is trivial.
- (d) INTEGRATE-AFTER-DECISION. The decision (Q2) is whether to commit 538 KB plus 79 KB of bitmap tables derived from third-party fonts. (e) M (review and merge; the code is small, most lines are JSON).
- (f) Low code risk: `tools/glyphgrid` is not a judged target, and the tests run in R0 with no timing assertions. Gate: pwmcp `r1`.

### 7. PWMCP console corpus / OCR phase 2-3 prep
- (a) Adds:
  - `tools/corpus/console_corpus.py`: deterministic tar, sha256 pin, verified idempotent fetch, refusal paths;
  - `tests/corpus/console/CORPUS-ASSETS.json` (the pin);
  - 15 tests (synthetic corpus plus a fake downloader);
  - `docs/DESIGN-OCR-PHASE2-3.md` (the next-stream design);
  - README release-model corrections;
  - `test_ocr_stack.py`: the OCR image tag is derived from `pwmcp.contract.json`.

  It also carries a 1.64.0-r4 coordinate bump and a "PWMCP-12: r4 without R2" entry.
- (b) Main has nothing equivalent; the series index is already on main. Main's PWMCP-12..17 are different issues, so the IDs collide. The branch's PWMCP-12 is obsolete because a whole-source PWMCP R2 is running. The release coordinate is still r3, and the bump belongs to the release step.
- (c) The only conflict is `KNOWN_ISSUES_TODO_BACKLOG.md` (the ID collision). Drop those hunks and the coordinate bump.
- (d) INTEGRATE-AFTER-DECISION (Q1). The two real-corpus tests FAIL by policy when the frames are absent, and gate lanes have no network. The order is therefore: publish the data release, then bake it into tester-unified (patch: `pwmcp/tester-unified-console-corpus.patch.txt` in the program dir), then merge. (e) M.
- (f) Medium, because of the shared tester-unified image rebuild: every tool's gate uses it, and `cmru/tests/test_tester_unified_image.py` asserts the Dockerfile. Rebuild with a rollback tag and no other gate running. Gates: cmru `assay` (Dockerfile assertions) and pwmcp `r1` on the rebuilt image. Do not add `tools/corpus/console_corpus.py` to the judged targets in this integration; that keeps the running PWMCP R2 evidence aligned.

### 8. CMRU installer alternative (INST62)
- (a) Adds `cmru installer render|check` (byte check with a 40-line capped diff; `get-py` becomes a deprecated alias). It also adds `[project.installer] stable_dir` sync, `stub_launchers`, installer self-copy and typed wizard questions. These serve stack consumers whose ciu `INSTANCE_ID` derives from the physical path. The branch calls this KI-69, which collides with main's different KI-69.
- (b) Main still has `get-py` only. The template has evolved since (KI-89 asset name, R6-03, C-03a) and nothing supersedes INST62. Main's `[project.installer]` users are ciu, tls-edge and pwmcp.
- (c) Hard as a merge. It touches `get.py.tmpl` and the rendered `ciu/get.py` and `tls-edge/get.py` (721 lines each), and the CLI surface needs regeneration. Its deprecation text says "removal in cmru 7.0", which is already past.
- (d) INTEGRATE-AFTER-DECISION (Q4), executed as a re-carve on cmru 7.x with the branch as reference. (e) S/M for `render|check` only; L for all five features.
- (f) Medium: a template change re-renders committed installers, which pulls in ciu `enroll`, tls-edge and pwmcp gates and releases.

### 9. Requirements-file naming
- (a) An AGENTS.md rule (`requirements-<use-case>.txt`) and renames of the assay build/lint requirements, the P24 carve asset and the debian-install-v2 wizard requirements, with their references.
- (b) Not landed. Main has since added more references: `debian-install-v2` `bootstrap.py`/`wizard.py` and `assay/tools/tester-unified-gate.sh`. The recorded gate block (observer socket rejected, gate-slice capacity not derivable) was environmental, not a code defect.
- (c) 25 paths, 1231 commits behind. Redoing it mechanically from main is cheaper than merging. Historical reports, logs and transcripts must not be rewritten. tester-unified's `<temp-path>` is an image-internal temp name and out of scope.
- (d) INTEGRATE-AFTER-DECISION (Q5). (e) S per tool.
- (f) Low code risk. The cost is the assay `self-qualification` gate (exclusive host), so the assay part rides the next assay release after the active assay wave merges.

### 10. Assay B103 interruption boundary
- (a) Design-only handoffs: assay P35, run-gate P36, cgprofile P07. The last independent review is NOT READY (F-1/5/7/8). The later "closed" claim was never re-reviewed.
- (b) Main has the B103 stub (`assay/nyxloom-trove/4-backlog.md:10669`) pointing at these commits; A-449/A-450 are reserved for it. Later assay work partially overlaps: interrupted candidates are left unclassified, and host-busy runs are classified inconclusive. Whether that fully covers B103 has not been verified.
- (c) ID collisions (B099, A-448), and the branch is 4048 commits behind.
- (d) DROP (operator 2026-10-21; Q6 confirms). Create `archive/assay-b099-p35-repair` and point the B103 stub at the tag. Re-carve from main when B103 is scheduled.
- (e) Archive S. (f) None.

### 11. I/O bench series prototype
- (a) A budget-aware fio series reducer plus `io_probe` timers and service.
- (b) Superseded. `libraries/iobench` (0.1.0) implements the series reduction (SPEC IOB-SER-01..05) with the review fixes (knee detection, NaN/duplicate timestamps). The long-term probe is D-011, assigned to iogov (iobench DESIGN-GUIDE:303). The 2026-10-21 "keep as reference" purpose is now served by the archive tag.
- (d) DROP (`archive/io-bench-01-20261020`). (e) Archive S.
- (f) The prototype probe randwrites a raw partition with no device guard (review P-01), so it must never be deployed.

### 12. RG-120 current-source rejudge
- (a) Five WIP commits. The net diff against their base is one line: `PYTEST_ADDOPTS` added to `env_passthrough` of the run-gate `r1` assay lane.
- (b) The pilot outcome and the oracle tests (`f84b1a9b4`) are on main and recorded in RG-120. RG-121 is closed. What remains is three candidate verdicts: survivors `3c1b59b5b700` and `ec9c79b40e7e`, and the crash `2e82d927b973`.
- (d) DROP the branch: an ambient passthrough into a judged lane is a "defaults are hazards" shape. Close RG-120 with a targeted rejudge of those three candidates from current main (Assay `--resume --rejudge`, or a fresh targeted run if the pilot state is not resumable). That needs no code; it is a controller operation when an R2 slot is free. (e) Run S, wall time hours.
- (f) Low. Lane: run-gate `assay-r2`.

### 13-14. RG-56 admission design and implementation
- (a) The design branch has five reports; its last review is BLOCKED on policy/contract decisions. The implementation branch has seven commits of pure admission arithmetic in `run-gate.py` plus tests.
- (b) RG-56 stays OPEN as a future wave (`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md:4509`). Since then, main shipped the RG-80 Docker-ticket count admission (`run_gate_admission.py`) and the R-45 bounded headroom wait. Main renamed `run-gate.py` to `run_gate.py`. The backlog cites "the separate corrected RG-56 design-only handoff", which exists only on the design branch.
- (c) The implementation targets the pre-rename file and is 3993 commits behind. The design docs are additive, but they are blocked.
- (d) DROP both (operator 2026-10-21 file-and-drop; Q6 confirms). Create `archive/rg56-admission` and `archive/rg56-admission-implementation`, and point RG-56's handoff citation at them.
- (e) Archive S; a future RG-56 wave is L. (f) None now.

## Recommended order (one luna stream per tool)

1. **Now, in parallel (each brief is in `codex/`):**
   - NYX: `child.pid`.
   - CMRU: mutation timeout.
   - MDT: bind-mount docs and test.
   - CIU: REVIEW-05 report.

   Each runs the luna implementer, then the controller runs the registered gate (one gate container at a time, verdict read in a separate step), then an independent review per AGENTS.md "Reviews", then `--no-ff` merge and push, then worktree removal. Lowest risk first: CIU doc, MDT, NYX, CMRU.
2. **Archive batch (controller):** tags for items 10, 11, 12, 13, 14. Docs-only pointer edits: B103 stub and the RG-56 handoff citation. Do the B103 edit only after the active assay wave (`assay-wp8`) merges, to avoid a backlog conflict.
3. **After the interview:**
   - PWMCP stream: fonts-speed (Q2), then the other pwmcp-chain branches outside this plan (imagegen, canvas-capture, vmscreen), then the corpus (Q1: publish, tester-unified bake, merge). Release pwmcp r4 after the running PWMCP R2 completes.
4. **After the interview, in parallel with the PWMCP stream:**
   - CIU: the worktree-rm lifecycle package (Q3, L).
   - CMRU: INST62 per Q4.
   - Requirements naming per Q5: debian-install-v2 first, assay with its next release.
5. **Controller operation when an R2 slot frees:** the RG-120 targeted rejudge.

## Dependencies and serialization

- **Host:**
  - One gate container at a time (production game servers share the host).
  - The running PWMCP and IOBench R2 campaigns hold the `r2b` and `r2c` locks. Gates and the RG-120 rejudge queue behind them under `gate.lock`.
  - Integration work itself is focused pytest only.
- **tester-unified image (item 7):** shared by every tool's gate.
  - The rebuild needs an idle host and a rollback tag.
  - Run cmru `assay` (its `test_tester_unified_image.py` checks the Dockerfile) before rebuilding.
  - Publish the corpus data release first; the fetch runs at image build time.
- **ciu (items 4, 5):**
  - All `worktree.py` and `libraries/worktree` work goes in one stream: CIU-134/151/153/154 in one package, nothing else in parallel on those files.
  - The REVIEW-05 doc does not conflict and can land first.
- **run-gate (items 12-14):** no code integration. RG-120 is a campaign run; RG-56 is archive plus a backlog pointer.
- **assay (items 9, 10):**
  - `assay-wp8` is active, so assay backlog and gate-script edits wait for it.
  - The requirements rename touches `assay/tools/tester-unified-gate.sh` and `gate/distribution`, which needs the assay `self-qualification` gate (exclusive host).
- **cmru (items 2, 8):**
  - The timeout lands first (S).
  - A re-render of the INST62 template ripples into ciu, tls-edge and pwmcp, so schedule it after the CIU stream's release, never concurrently.
- **pwmcp (items 6, 7):**
  - Fonts-speed and the corpus touch disjoint files; land fonts-speed first.
  - Main's session-cleanliness guard (PWMCP-13..16) applies to the new tests: they must not write inside the repository.
- **Releases:**
  - nyxloom: next patch.
  - cmru: next patch.
  - mdt: next source release.
  - The ciu doc needs no release.
  - Every release runs through cmru with its tool's provisional or full gate, as recorded in each backlog.
