# Changelog — run-gate

All notable changes to `run-gate.py` and its estate adoption are recorded
here. The in-file `__revision__` is the drift marker: estate sweeps compare
it, and any consumer holding a COPY (not a symlink) must re-copy when it
moves. Normative behavior lives in SPEC.md; entry-by-entry rationale lives in
KNOWN_ISSUES_TODO_BACKLOG.md and git history.

## [Unreleased]
<!-- hand-written ahead of release; cmru's generator will produce the real dated entry for this range at release time -->

### Fixed
- **RG-39 (rev 35, SPEC `R-41`) — exec-mode lanes now serialize internally on
  the resolved container name, closing the need for every caller's own
  `flock`.** A second lock (`/tmp/run-gate-exec-<container>.lock`, RG-20's
  `O_NOFOLLOW`+`0600` discipline via a new shared `_open_lockfile()` helper)
  is acquired strictly AFTER RG-20's shared-infra locks and released from the
  same `finally` — a fixed global order that rules out an ABBA deadlock
  between the two lock kinds. A second lane racing the SAME container WAITS
  (blocking `LOCK_EX`, never fails); different containers never meet;
  `--dry-run` plans the lock but never blocks. The caller-side `flock` of
  dstdns `GUIDE.md` §1 stays valid as an outer lock and becomes optional for
  correctness, not required.

<!-- Post-release housekeeping (assay CHANGES.md precedent): this block is
     CLEARED immediately after a release. cmru generates the dated entry
     below from the commit range but does NOT clear this hand-written block
     itself — leaving content here would republish shipped work as
     "unreleased" on the next cycle. Recurred on run-gate-project as of
     2026-09-01 (rev 24/25/27/28/29/30 content had survived three releases,
     23.1.0/23.2.0/23.2.1; then again the SAME cycle, since cutting
     23.2.2 to ship RG-31's own fix — confirming this really is a manual
     per-release step, not something cmru will ever do for you); cleared
     both times. -->

<!-- cmru: release history -->

## [23.4.0] - 2026-09-03
<!-- cmru: generated -->
<!-- cmru: source-end=b7b5f38dd7655cc44049d1bb3667ad705c1cd4f0 -->

### Added
- feat(run-gate): bk-lane.sh BK_QUEUE overrides the pipeline queue per build (E5-R6) (17077426)
- feat(run-gate): bk-lane.sh trigger + collector (REMOTE-LANES seam 4) (8fcf3dd9)
- feat(run-gate): Buildkite pipeline generator (REMOTE-LANES seam 2) (c07ae6af)
- feat(run-gate): RG-36 -- liveness judged from the progress file, with an optional stall_timeout (rev 34, R-40) (10aa59e2)
- feat(run-gate): RG-34 -- doctor names an unprefixed script path in a container command lane (rev 34, R-30b) (1e41069f)

### Fixed
- fix(run-gate): RW-37 follow-up -- restore line 1259's coverage after the fix stole it (d274bc73)
- fix(run-gate): RW-37 -- an absent schema key in an inflight record is corrupt, not a mismatch fall-through (0f866432)
- fix(run-gate): review round 2 nits N-a and N-b -- both one-liners, both taken (713887fc)
- fix(run-gate): RW-25 -- an inflight record of another schema is REFUSED, not disclosed-and-overwritten (3af55353)
- fix(run-gate): RW-28 -- a follower that outlives its owner is promoted, not left with an orphan (G2) (7fd45793)
- fix(run-gate): RW-29 -- the owner's identity is namespace-safe, and unknown means ALIVE (G3) (d16d9380)
- fix(run-gate): RW-27 -- silence is measured from the FILE, not from this client (G1) (43d66ba8)
- fix(run-gate): close collect's two uncontracted exits; round-2 nits (E5-R17/R18/R19) (c2105cf7)
- fix(run-gate): bound artifact downloads by stall, API calls by total time (E5-R16) (ef8396df)
- fix(run-gate): bound every request; point §3 at the LANE-AUTHORING rule (E5-R14/R15) (d58f122b)
- fix(run-gate): review round 1 — traversal, swallowed --dry-run, unbounded poll, exit codes (a777f109)
- fix(run-gate): RW-19 -- S7 + N1..N5 + hollow tests 2-6, and one real flake in the wave's own fixture (d7e280ce)
- fix(run-gate): RW-18 -- the dry run names the real outcome, and every path discloses the lane's bounds (S5+S6, R-39b/d) (ffc64903)
- fix(run-gate): RW-17 -- a COLLECTED run's duration is the container's own FinishedAt - StartedAt (S4, R-39c) (86f7f7b4)
- fix(run-gate): RW-20 -- a live owner whose container is already gone is re-polled before the refusal (R-39e) (cb6104a4)
- fix(run-gate): RW-15 -- drop `--since`; plain `docker logs -f` already replays from the first line (S1, R-39b) (a8dc5ebc)
- fix(run-gate): RW-16 -- "gone" is only "No such object", and the name is not the identity (S2+S3, R-39b) (3f157a3e)
- fix(run-gate): RW-13 -- a misplaced LANE key says "move it", and RG-32's impact is the parsed 13 (B1, R-08a) (074ae074)
- fix(run-gate): RW-14 -- a live owner's container is FOLLOWED, never hijacked (B2, R-39e) (73f2bb14)
- fix(run-gate)!: RG-32 -- `pins.*.budget` refused at load; pin tables validate their keys (rev 34, R-08a) (8db781e6)
- fix(run-gate): RG-35 -- a lane's container is found again after its client dies (rev 34, R-39) (6fe633f5)

### Changed
- merge(run-gate): resumable-gate wave (E-1) -- 23.4.0, rev 34 (2dfc45df)
- merge(run-gate): E-5 Buildkite seams 2+4 -- pipeline generator from --list, bk-lane.sh trigger/collector, 72 network-free tests, REMOTE-LANES manual updated (c79d374b)
- backlog(run-gate,assay): the progress/re-attach/unbounded-budget pattern as estate default -- RG-35..RG-37, B065..B067 (b57b2d12)

### Documentation
- docs(run-gate): renumber the wave's own RG-39/RG-40 -- real collision with main's RG-39 (f239001a)
- docs(run-gate): RW-38 -- RW-37's fix shipped an incomplete diff, caught by re-verifying the gate myself (5857045c)
- docs(run-gate): RG-39 exec-mutex review round 1 ACCEPT -- merge-ready, waiting on the wave to land first (62cd09ad)
- docs(run-gate): RG-39 exec-mutex implementer verified, gate GREEN -- merge sequencing decided, review round dispatched (1f601fdd)
- docs(run-gate): new backlog RG-39 (exec mutex) dispatched -- separate branch, plus a real ID-collision note for the wave's own merge (118f79a0)
- docs(ciu): v8 design set rev 3.2 / SPEC-V8 draft.5 -- round-2 delta audit folded (T2-01..T2-10 + every incomplete/broke audit row), canonical lock keys + ciu lease, [ciu] inherit, ciu8 decision recorded; demo ciu.toml files re-included in git (v7 **/ciu.toml rule hid them); RG-39 annotated as buildable; nyxloom NL-6 filed (ccbc02bf)
- docs(run-gate): round 3 NOT ACCEPT -- RW-37, RW-35 was wrong, fix-and-reverify (5c07b5c5)
- docs(run-gate): resumable-gate wave -- review round 3 (verification-only): NOT ACCEPT, RW-35's ruling not implemented (a5b2834a)
- docs(run-gate): correct RW-32 -- already implemented at 7fd45793, no addendum (5c2a77a0)
- docs(run-gate): fix package 2 landed at 661fde05, gate GREEN -- RW-32..RW-36 ruled (718070ee)
- docs(run-gate): fix round 2 -- gate verdicts and what was deliberately not done (661fde05)
- docs(run-gate): RW-30, RW-31, RW-26 -- a dated measurement with its command, the N5 path, and RG-34 closed (2cb91b4e)
- docs(run-gate): RG-39 -- no internal mutual exclusion around the container exec (1f312ab1)
- docs(run-gate): E-5 merged (c79d374b); run-gate review round 2 NOT ACCEPT -- RW-27..RW-31 ruled, fresh fix implementer for package 2, round 3 verification-only (7fb0adde)
- docs(run-gate): adversarial review round 2 -- NOT ACCEPT, 1 new blocker (4a7a490b)
- docs(run-gate): E-5 review round 2 ACCEPT -- E5-R17..R19 (fold SF1 + nits before the merge); LANE-AUTHORING §5 quotes the generator's exact glob (477f80f9)
- docs(run-gate): E-5 Buildkite seams 2+4 -- adversarial review round 2, ACCEPT (10dced36)
- docs(run-gate): fix successor returned green at 21e6bbea, RW-23..RW-26, reviewer round 2 dispatched; E-5 E5-R16 landed at ef8396df, round 2 dispatched (740f43b4)
- docs(run-gate): E-5 controller log -- E5-R14/R15 landed at d58f122b; E5-R16 bounds downloads by stall (59a2120f)
- docs(run-gate): fix round 1 part 2 -- LOG + REPORT, the live two-client probe, RW-20..RW-22 as ruled (21e6bbea)
- docs(run-gate): E-5 controller log -- round-1 fix package at 6dceaaf0 (65 tests); E5-R13..R15; curl timeouts follow-up then round 2 (4f23919d)
- docs(run-gate): REMOTE-LANES §3/§4/§6 corrected after review round 1 (6dceaaf0)
- docs(run-gate): E-5 review round 1 NOT ACCEPT -- E5-R7..R12 ruled; LANE-AUTHORING §5: remote-capable lanes keep artifacts under .assay/ (3f148522)
- docs(run-gate): E-5 Buildkite seams 2+4 -- adversarial review round 1, NOT ACCEPT (31efccca)
- docs(run-gate): E-5 controller log -- BK_QUEUE follow-up landed at 17077426, reviewer round 1 dispatched (7eb3beea)
- docs(run-gate): E-5 controller log -- Buildkite seams 2 and 4 landed on feature/run-gate-buildkite-seams; E5-R1..R6; BK_QUEUE follow-up then review (1dfc07ae)
- docs(run-gate): REMOTE-LANES §3/§4/§6 point at the real Buildkite tools (81ff037f)
- docs(assay,run-gate): limits reset -- three implementers dispatched in parallel (assay gen 11, run-gate fix successor, E-5 Buildkite seams) (c4cdd5e9)
- docs(run-gate): controller log -- the fix implementer's selftest verdict is quoted, not read; successor re-gates first (b30a900d)
- docs(run-gate): resumable-gate wave -- fix implementer checkpointed at e87007cc (RW-13/14/16 landed, gate green); RW-20..RW-22; no successor dispatched (session limit) (d615cc2e)
- docs(run-gate): fix round 1 -- LOG entry and continuation BRIEF 1 (E-008 cut) (e87007cc)
- docs(run-gate): LANE-AUTHORING.md + REMOTE-LANES-BUILDKITE.md -- sibling guides to CONSUMERS; E-5 (remote/async lanes) recorded in the post-v10 wave plan (780f9a98)
- docs(run-gate): resumable-gate wave -- reviewer round 1 NOT ACCEPT (2 blockers); RW-13..RW-19 ruled, fresh fix implementer dispatched (bdad6768)
- docs(run-gate): adversarial review round 1 -- NOT ACCEPT, 2 blockers (be7d94b3)
- docs(run-gate): resumable-gate wave -- follow-up package verified at 73e6b061, reviewer round 1 dispatched (70f676c4)
- docs(run-gate): RG-40 filed (RW-9) and --fresh's conjunction shape documented (RW-10) (73e6b061)
- docs(run-gate): resumable-gate wave -- implementer returned gate-green; controller log with RW-9..RW-12 (061fd861)
- docs(run-gate): wave records for rev 34 + RG-39 (coverage_gate dirty-tree line numbers) (d4e8e137)
- docs(run-gate): RG-34 addendum — second independent repro, broader scope (ff922ddd)
- docs(run-gate,assay): run-gate resumable-gate wave dispatched (E-1: RG-35/36/32/34 -> 23.4.0); operator rulings D1-D7 accepted, F015 leaves Wave D (DA-R21) (dc6e88c4)
- docs(run-gate,assay): RG-37/RG-38 id swap -- resume-state durability is RG-38, RG-37 is the ciu v8 session's container-derivation row (05e123d3)

### Testing
- test(run-gate): RW-29 -- cover the kernel that will not name the namespace (f4a46459)
- test(run-gate): cover the owner-liveness and follow-path edges (diff-coverage floor) (450b3e22)

## [23.3.0] - 2026-09-02
<!-- cmru: generated -->
<!-- cmru: source-end=b36c6925d1d8ff8bf6fd4b74de8a5bd9f3855dbe -->

### Fixed
- fix(run-gate): RG-33 -- every assay lane runs with --resume and --progress, judge floor refused by name (rev 33, R-38) (0a4862db)
  - Hand-authored detail (folded in post-release, the standing rule): every
    `kind = "assay"` lane is now invoked with `--resume --progress
    .assay/progress-<assay_lane>.jsonl`, unconditionally, on every runner.
    Measured cause: dstdns's `sql-mutation` lane re-tested the first of four
    target files from mutant #1 on three budget-capped retries because the
    argv never carried `--resume`. Both flags are no-ops on a lane without R2
    (assay's own contract), so R0/R1 lanes are unchanged; an R2 lane resumes
    from `.assay/mutation-state/` on retry and streams progress beside its
    verdict under the git-ignored `.assay/`.
  - **Consumer note (breaking for very old pins):** the flags need a judge
    that knows them — assay **>= 2.4.1**. A pin declaring an older `version`
    is refused at argv construction by name (lane, pin, version, floor,
    remedy); a pin without a declared version reaches the judge and fails
    loudly there. Re-pin before adopting rev 33 (in this estate only `cmru`
    was below the floor, at 2.3.0; re-pinned to 4.1.0 in `b36c6925`).

### Documentation
- docs(run-gate): RG-34 — schema lane argv doesn't template {worktree} into its own script path (eeda67ce)
- docs(run-gate): RG-33 — assay mutation lanes never pass --resume, retries restart from scratch (a04c95c2)
- docs(run-gate): RG-32 — pins.assay.budget is silently inert, real value lives in the consumer's assay.toml (8be4c6b9)

## [23.2.2] - 2026-09-01
<!-- cmru: generated -->
<!-- cmru: source-end=fad40555fb0f8125315f3811a8dcd95bea6db9c3 -->

### Fixed
- fix(run-gate): RG-31 -- assay_toolchain_findings routes --worktree through resolve_worktree_scope (rev 32) (0efd062e)

## [23.2.1] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=fe09688572dc7d744ba81b6b471eb4908599ffa6 -->

### Fixed
- fix(run-gate): RG-30 -- doctor/--check-env honor --worktree (rev 31) (89ca96ba)

### Changed
- backlog(run-gate): file RG-31 -- assay_toolchain_findings bypasses RG-30's validated worktree resolution (da535655)

### Documentation
- docs(run-gate): RG-30 FIXED + run-gate-P04 LOG/REPORT (60512539)

## [23.2.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=7c47a70710eac58697641d9ee2444a1ea0db8af3 -->

### Added
- feat(run-gate): RG-27 -- lane invocation history + the `history` query verb (rev 30) (1687b60d)

### Fixed
- fix(run-gate): RG-27 round-2 review -- B1 history read scope, B2 at-most-once flush, S1/S2 (0e6d0ea4)
- fix(run-gate): RG-27 -- record inline in main(), and cover the wiring in-process (afcdb39f)

### Changed
- backlog(run-gate): file RG-30 -- doctor/--check-env ignore --worktree, same pattern RG-27 just closed for history (5df35ce4)

### Documentation
- docs(run-gate): RG-27 -- P03 LOG/REPORT carry the real round-2 gate verdict (56d98572)
- docs(run-gate): RG-27 FIXED + run-gate-P03 LOG/REPORT (dbaccfe1)

### Testing
- test(run-gate): RG-27 -- in-process cover for the B1/S1 main() dispatch branches (dc3b1490)

## [23.1.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=1f47601c1b69a3503c4d94caca3ca90c373f8e0b -->

### Added
- feat(run-gate): RG-26 -- --base REF reaches a delegating assay lane as --request-base (7b30bc49)
- feat(run-gate): RG-25 -- doctor/--check-env preflight assay-lane toolchain fitness (9a403da3)
- feat(run-gate): RG-21 -- doctor names the linked-worktree host-lane git view (9adf11fc)

### Fixed
- fix(cmru,run-gate): RG-29 -- cmru/run-gate.toml's assay pin still named the vanished 2.2.0 sidecar (0ad5372d)
- fix(run-gate): P02 review round -- batch the fitness probe (B2), tell the truth about what dry-run and doctor start (B1/B3) (2f266885)
- fix(run-gate): RG-23 -- declare the env-forward breaking change and widen the drift sweep (c55f5748)
- fix(run-gate): RG-24 -- exec-mode container names resolve from the judged worktree (bd1a3f85)

### Changed
- backlog(ciu,run-gate): file CIU-75 -- backport v8 F2 identity source (breaking, ciu 7.6); retriage CIU-55 -> RG-27 -- gate invocation history + query verb (a78a0046)
- backlog(run-gate,ciu,assay): RG-25/RG-26 -- backport ciu CIU-72 (b)/(c) to the current gate; CIU-73 needs no code (b2884e76)
- backlog(ciu,run-gate): file CIU-71 -- build-context project-directory gap; RG-24 -- exec-mode container resolution is repo-scoped not worktree-scoped (92ae1917)

### Documentation
- docs(run-gate): usage() names the doctor/--check-env checks this bundle added (08783d09)
- docs(run-gate): P02 bundle LOG + REPORT (RG-21/23/24/25/26) (e8a6a34b)
- docs(assay): file the 2.1.0->2.3.0 review-gap audit and its backlog (B030-B034, RG-23) (142143a4)

## [23.0.0] - 2026-08-24
<!-- cmru: generated -->
<!-- cmru: source-end=f8178d9b0b821405f4f0fb8831d710056352193f -->

### Added
- feat(run-gate): adopt estate release orchestration with a diff-coverage floor (b6ec5d6a)

### Fixed
- fix(run-gate): resolve adversarial-review findings on the release-adoption program (db173082)
- fix(run-gate): run the selftest lane in host mode, not tester-unified (ca023b78)
- fix(run-gate): scope the selftest diff-coverage floor to run-gate.py alone (1c1d2fda)
- fix(run-gate): RG-22 — safe.directory global-config write survives pre-existing entries (9ad6388f)

### Changed
- backlog(run-gate): RG-22 — safe.directory overwrite fails when global config has multiple entries (2174d22e)

## [22] - 2026-08-24 — RG-sweep program complete

Backlog entries RG-1..RG-20 implemented (RG-18 excepted — dstdns-side scope,
see its backlog body), adversarially reviewed by two independent fresh
reviewers (findings fixed in rev 21–22 commits), then verified end-to-end.
Merged to main as `vbpub@91df32ee` (--no-ff, no conflicts).

### Rev map (one backlog item each unless noted)
- rev 4 RG-15 — assay lanes execute in the selected worktree
- rev 5 RG-11 — reserved exit codes: 2 config/refusal, 3 infrastructure
- rev 6 RG-4 — pins.version actually checked (whole-token grammar)
- rev 7 RG-16 — central configs may define shared lanes
- rev 8 RG-3 — dual-mount degeneracy fix
- rev 9 RG-5 — `{worktree}` substitution hardened (quoting/injection)
- rev 10 RG-6 — exec-mode refusal names the project-agnostic remedy
- rev 11 RG-17/RG-19 — required_env preflight + forwarding log + --check-env
- rev 12 RG-1 — conjunction override guard (`--worktree`/`--allow-dirty`)
- rev 13 RG-12 — failing-container evidence preserved (mode 0600)
- rev 14 RG-10 — artifacts key + unconditional verdict/evidence disclosure
- rev 15 RG-2 — `validate-pointers` verb + estate linkage meta-tests
- rev 16 RG-8 — `--dry-run` plan rehearsal on all three runners
- rev 17 RG-20 — resource-aware admission (slice budget from cgroupfs,
  shared-infra locks, lane `resources` key)
- rev 18 RG-9 — `doctor` preflight verb
- rev 19 RG-14 — wheel as second artifact (version derived from __revision__)
- rev 20 RG-13 — docs sweep + estate adoption retro ×9 projects
- rev 21–22 — adversarial-review hardening (SPEC header Rev 5 lists the
  rule-level deltas R-02..R-32)

### Verification ledger (2026-08-24, phase B/C)
- Suite: `pytest tests -q` → **205 passed** on the branch pre-merge and again
  on shared main post-merge.
- `--list`: all 9 consuming projects, rc=0.
- Every declared lane executed end-to-end (16 total): mdt/smoke, plesk/smoke,
  topos/py-compile, topos/topos-suite (2922 passed, diff-coverage 100% floor),
  pwmcp/tests (8 passed), nyxloom/tester-unified (diff-coverage 100% floor),
  srdm/unit, srdm/e2e (full systemd suite), ciu/ciu, cmru/{assay, coverage
  (1675 passed, 100%), canary, mutation, gate}.
- Assay judgment read SEPARATELY from each verdict artifact (LESSONS L4):
  assay claims R0–R3 PASS; ciu claims R0–R1 PASS (its declared rigor);
  cmru claim R0 PASS.
- cmru/gate conjunction rc=0 with every sub-invocation carrying explicit
  `--worktree {worktree}` — RG-1's guard exercised against a real daemon.
- cmru/mutation took its honest self-skip path (no changed src since
  cmru-v5.0.0), proving the KI-18 base-resolution contract.
- srdm/coverage **fails from a linked worktree** (covergate git plumbing hits
  the main checkout's gitdir, which gate.sh's single-subtree mount does not
  carry) and **passes on main** — environment topology, not a run-gate defect;
  run-gate's own exit-status passthrough and clean-tree refusal behaved
  correctly throughout. Filed as RG-21 with candidate directions.
- Merge held one foreign uncommitted edit (ciu/docs CIU-V7 proposal notes)
  via tagged stash round-trip; restored untouched afterwards.

### Re-copy obligation (drift marker = 22)
All nine in-repo consumers invoke `../run-gate-project/run-gate.py` through a
SYMLINK — zero re-copies owed inside vbpub. Any out-of-repo COPY of an older
revision (none known today) must re-copy before its next gate run.
