<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/rigor-chain-REVIEW.md -->

# Rigor-evidence chain: adversarial review (2026-11-05)

Scope: branches rigor-{rr1,rr2,rr3,rr2a,a1,a1a,rgd,hb1,hb23,cp1,c13,c2}-20261105 against main 68890590a. Design authority: memo Amendment 2 and the boundaries review. Read-only; static reading plus three tiny local probes (a pathspec check in scratch, and two python probes of `derive` over the golden project). No gates, containers or heavy runs were run, so reported gate results are the packages' own claims.

## Throwaway merge result (main + all, review-tmp-rigor, now removed)

Order: rr2a (contains rr1, rr2, rr3), a1a (contains a1; shares the rr1 commit, merged without conflict), rgd, hb1, hb23, cp1, c13, c2.
- One conflict, resolved trivially: `docs/BOOTSTRAP-FROM-ZERO.md` (c13 commit 879f520ae sets "25.0.1"; main already says "25.0.2"). Correct resolution is main's side, since c13's commit is a stale duplicate of a fix main already carries. Drop that commit from c13/c2 before merging.
- No other textual conflicts. Every CHANGES.md has exactly one `## [Unreleased]` after the merge.
- Backlog id clashes: **assay B219 collides** with `backlog-materialize-20261105` and `fix-assay-mat-20261105`, which both also define B219..B223 for other items. B224 is free everywhere. RG-161, MDT-066..068, CP-58/59 and cmru KI-118/120 (status edits only) have no clashes on any other branch. The existing duplicates RG-55/RG-102/B105/B110 are on main already.
- The merged tree was not run, so cross-branch behaviour is unverified (cp1's golden copies into run-gate fixtures, for example).

## Findings, severity ranked

### HIGH

**H1. rigor-record cannot consume the inventory that cmru's record step hands it (rr2a x c13).**
- `libraries/rigor-record/src/rigor_record/assemble.py:84-102` (`_artifacts`) demands `kind == "cmru-local-build"`, `publication == "eligible"`, `source_commit`, `project` and `artifacts[].files[].path/sha256`.
- `cmru/src/cmru/record_step.py` (`write_inventory`) writes `kind: "cmru-release-inventory"`. It has no `publication` key, and the files carry `path`, `sha256` and `bytes`. It is exported to the record step as `CMRU_BUILD_INVENTORY`.
- Failure: the documented wiring `rigor-record assemble ... --artifacts "$CMRU_BUILD_INVENTORY"` is refused with "artifacts: not a cmru build inventory" on the first real release. The goldens and tests only use the `cmru-local-build` build.json, so the two packages were never run against each other.
- Fix: accept the release-inventory kind in `_artifacts` (or have cmru hand over the build manifest) and add one cross-package fixture test.

**H2. `derive._r0` uses the newest record's release sequence for every commit.**
- `derive.py:_r0`: `sequences[-1]` comes from any record in `storage_order`, not the record of `commit`.
- Probed on the golden project: with an older release 1111 summarised as R0 `pass`, adding one new member lane to the newest release sequence flips release 1111 to `not-judged` in the RIGOR.md history table and the index.
- Failure scenario: v2 adds a lane to the release sequence, and every earlier release row in `RIGOR.md` silently turns "R0 not-judged".
- Fix: use the sequence of the commit's own record. For an unlabelled commit, use the newest sequence valid at that commit, or `not-judged`.

**H3. Declared out-of-project inputs are silently dropped from commit identity.**
- `assemble._targets` (`assemble.py:237-245`) and `update._targets` (same function) `continue` on `GitError`.
- assay's own contract (B224 decision 2) says `inputs` are `link_paths`, whose content is untracked.
- Result: the judged-target identity (design Amendment C, "valid where every target is identical") never includes those inputs. A result stays "valid" at a commit where the linked input differs, and a misspelled input is never noticed.
- The test pins the opposite contract: `tests/test_cli.py:42-43` creates and commits a `node_modules/` tree so the golden assay input resolves to a tracked tree. Production inputs are untracked, so this test hides the gap.
- Fix: record untracked inputs explicitly (named, with no object id), or refuse when a declared input has no identity. Replace the test with an untracked input.

### MEDIUM

**M1. `plan_r2` accepts an assay fragment without its run-gate half (RRK-11).**
- `update.py:plan_r2`: it only requires `g.assay`; `g.gate` may be None. `_toolset_of` then yields no `run_gate` or `tester_image`, `perf` stays empty, and no environment or machine identity is stored.
- The result is a pass/survivors R2 with no reproducible tool identity, which contradicts the design rule that each result carries its toolset.
- The author left this open knowingly; it should be a refusal until RG-a lands, not a silent acceptance. No test refuses it.

**M2. `closeout_exit` is a caller-supplied number, not read from the fragment (`update._status_from`).**
- `--closeout-exit 3` on a campaign that actually completed stores `incomplete`, and the validator does not check `executed < total` for incomplete.
- `--closeout-exit 2` stores `error` on a completed run. A later "re-run" is then allowed by `TRANSITIONS`, though the survivors may already be final.
- The validator catches only the inverse case (exit 0/1 with `executed != total`).

**M3. R1/R3 level status is the lane's overall status, not the claim's outcome (`assemble._result`).**
- `_claims()` returns `{level: outcome}`, but `_result` only tests membership (`level in claims`) and stores the lane-wide status.
- assay's CONSUMERS text says a consumer derives levels "only from claims with outcome PASS".
- A claim with outcome ERROR and a null section (which assay does emit, B224 decision 1) makes `_result` raise "assay claims R1 but the fragment carries no coverage". This blocks a waived failing lane (for example R0 fails, later claims ERROR) even though a waiver covers it.
- Fix: map each claim outcome to its own level status. Treat a null section with an ERROR claim as "level not held", not a refusal.

**M4. `sync._fold` drops existing labels and skips the privacy lint.**
- It is built from the released record, so a commit that already carries another label (a different tag on the same commit) loses it.
- `plan_sync` calls `validate_record(folded)` without `hosts` and `users`, so the denylist lint is skipped for the folded record.
- Probability is low (two tags on one commit), but the data loss is silent.

**M5. `gitfacts.published` treats any local tag containing the commit as "published".**
- `git tag --contains <c>` is non-empty for a local, unpushed tag or one on a side branch.
- The completion-only/attribution rule ("refused unless attributed to a published commit") can then be satisfied by a local tag.
- Also, `--attribute-to` is passed unvalidated to `git merge-base` (option injection for a leading `-`; low).

**M6. rr2a branch history contains 29 `wip(...)` commits, "red tree" by design.**
- Steps 4a to 17 are tagged "(red tree)", and the tip is the first green one.
- Merging the branch with `--no-ff` makes `git bisect` unusable across rigor-record, and the commits carry a misleading `chore`/`wip` provenance. Squash on merge.

**M7. rigor-record SPEC.md is normatively stale.**
- Sections 13-17 still specify `policy.toml`, `rigor/releases/<tag>.json` and `--waive-r2` (lines 212, 234-252, 278, 310-311, 334, 360, 390), under a pointer to §18. The golden descriptions at 234-236 are wrong too.
- The CHANGES `[Unreleased]` entries for RR-2 and RR-3 still describe the retired `rigor-policy-1` schema.
- This conflicts with the "no policy.toml" decision and sets up a wrong-contract test later. The author flagged it (the full rewrite is still open). It should be fixed before the library is released.

**M8. cmru C-2 attaches the rigor asset after the release is published (`release.py:_attach_exact_assets`).**
- The sequence is delete the same-named asset, then upload, and the delete can leave a release with no record if the upload fails.
- If GitHub "immutable releases" is enabled for the repo, deleting or adding assets after publication may be refused, or it contradicts "the release-time record is the immutable asset" that `sync` trusts.
- Needs a real-repo check. The `wheel-publish` path uploads in the same call as the release, which is better; the body-only and OCI paths are the concern.
- The `.sha256` sidecar is from the same uploader, so it protects integrity in transit only, not authenticity.

**M9. run-gate `--list --json` triggers docker probes.**
- It forces `[assay.import]` resolution (`assay_import_resolution_required`) and runs `assay lanes --json` per environment and judge identity (`lane_rigor_facts`).
- A listing is a plausible cron or consumer call. It can start containers per environment, and it exits 0 with `rigor: null` when a probe fails.
- Nothing in rigor-record consumes `run-gate-list/1` yet, and the consumer must treat null as "unknown", not "none". That cannot be shown until the consumer exists.

**M10. rgd selftest gate is red.**
- The report claims lane verdict FAIL on three runs (two different flaky tests, RG-160 family and a 1 s timing test), with diff-coverage run by hand.
- Not a defect of the feature, but a merge needs one clean `selftest` lane PASS and the coverage step run by the gate.

### LOW / notes

- **L1. Steal 10% exclusion exists nowhere in code.**
  - `grep` finds no steal-fraction computation or subject exclusion in run-gate, cgpd or rigor-record. cgpd only exposes `steal_jiffies`/`steal_seconds`, and rigor-record validates a `steal_fraction` metric name and `n_excluded`.
  - It is blocked on the unimplemented run-gate fragment writer (RG-a, `--rigor-out`), and the threshold is still an operator question. `classify_contamination` in run_gate.py is memory-stall only. Nothing is wrong yet, but the rule is currently unenforceable.
- **L2. Producer-side schema is mostly absent.**
  - The assay half is byte-identical between assay and rigor-record. Checked: goldens `cmp`-equal, and the `$defs` for assay_lane/coverage/canary/survivor/assay_r2 are equal.
  - The run-gate-lane and run-gate-composite shapes are rigor-record's own provisional copies, since RG-a has not landed. The composite fragment is invented by rr2a. Producer/consumer compatibility for run-gate is therefore unverified, not verified.
- **L3. Command lanes never feed R1/R3: correctly implemented in three places.**
  - Enforced in `validate._lane_result`, `derive._newest_per_lane_level` and `_result`. No `policy.toml` code remains in src. `waive-r2` is gone (argparse exit 2 is tested). Commits are completion-only. Labels live inside commit-keyed records.
  - Gap: there is no golden for a waived failed gate lane (R0/R1/R3 stored as `fail` plus a waiver). It renders as `fail (1 waived lane)`, while the RIGOR.md legend (`render.py:261`) says "pass-with-waivers: a failed lane or survivor is waived", which is only true for R2.
- **L4. `_resolve_older` resolves older survivors only for labelled commits.**
  - An unlabelled per-merge R2 result (the Amendment C use case) resolves nothing. Open survivors of older releases stay open until a labelled fix is processed.
- **L5. assay `_progress` reports only the newest run's start and end for a resumed campaign.**
  - The result is a wall-time understatement. It is not a double count: one `resume` event is emitted per run.
- **L6. hb1/hb23/cp1:**
  - The GB7 stdout and result URL parse are an assumed shape (fixture `gb7-stdout-ASSUMED-SHAPE.txt`). The first real run must replace it.
  - The baseline constant `{6: 2500, 7: 2500}` is duplicated in `cpu_benchmark.py` and cgpd `machine.py`, so it can drift.
  - The cgpd scores table ships empty (CP-59), so unknown models give `speed: null`.
  - The installer step is default-on and advisory: it runs a free Geekbench run that uploads the result to Primate Labs on every install. `_safe_extract` validates symlinks relative to the member's parent only (hardlink semantics differ) and then calls `extractall`.
  - mdt host-setup passes `--max-psi 5` (consistent with the memo). Only x86_64 is pinned (MDT-068).
- **L7. cmru C-1/C-3 look correct.**
  - `:(exclude,glob)<dir>` was checked in a scratch repo: it excludes the directory's contents, so the `rigor/` and `RIGOR.md` change exclusions work. The record step runs before `push`, and its outputs are limited to two files and bounded by size. A failure rolls the unpublished tag back.
  - `version.exclude_paths` refuses an exclude that covers an explicit watch path. The 64 KiB record cap in cmru matches the rigor-record cap.

## Verdicts

| Branch | Verdict | Condition |
|---|---|---|
| rigor-rr1, rr2, rr3 | do not merge separately | superseded ancestors of rr2a (policy.toml/tag-keyed layout) |
| rigor-rr2a | FIX FIRST | H1, H2, H3, M1-M3, M6 (squash), M7 |
| rigor-a1 | MERGE with a1a | |
| rigor-a1a | MERGE after renumbering B219 | clashes with backlog-materialize/fix-assay-mat (A-1 id), then merge a1 and a1a together |
| rigor-rgd | FIX FIRST (small) | M10 clean selftest PASS; M9 decision on docker probes |
| rigor-hb1 | MERGE | |
| rigor-hb23 | MERGE | L6 notes tracked (MDT-066..068) |
| rigor-cp1 | MERGE | |
| rigor-c13 | MERGE | drop the stale BOOTSTRAP commit 879f520ae (conflicts with main) |
| rigor-c2 | FIX FIRST (small) | M8 check against GitHub immutable-releases semantics; replace-asset failure window |
