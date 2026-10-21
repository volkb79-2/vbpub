# Changelog — cgroup-profiler

All notable changes to this project are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before
the release gate runs. Normative daemon behavior lives in `DESIGN.md` and
`docs/PROTOCOL.md`; entry-by-entry rationale lives in
`nyxloom-trove/backlog/` and git history.

## [Unreleased]

Release scope (CGP-110): the six blockers of the cgprofile-v1.0.0 Sol review.
**No R2 (mutation) pass for 1.1.0**: R2 is deferred by operator policy; the
registered `r0-r1` and `r3` lanes are the qualification. No R2 verdict may be
inferred from the r3 canaries.

- Add (Sol B5): `tools/observer-live-acceptance.py`, a read-only probe for the
  live contract-2 observer acceptance from a gated peer (contract-1 refusal,
  well-formed local maps, sibling-only versus enforced-at-ancestor deltas),
  with the operator procedure in `docs/CONSUMERS.md`. The live run itself is
  NOT part of this change and is required before CP-20 is closed.
- Fix (Sol B4): `cgprofile serve` now refuses to start (exit 2) when its own
  `cgprofile.slice` is unbounded (`memory.max` unlimited or unreadable), i.e.
  when the unit was never installed and systemd auto-created an implicit,
  limitless slice. The documentation that said the daemon "still serves"
  without the unit, and a `doctor` message the code never emitted, are
  corrected. A pre-creation Docker-host `LoadState=loaded` probe in CIU's
  preflight is a separate CIU change (filed in the CIU backlog).
- Fix (Sol B2): `stop` no longer claims completion while the sampling worker is
  still running. If the worker does not exit within 10 s the daemon answers the
  new error `stop-incomplete`, writes no `finished` manifest or summary, and
  keeps the session's placement and DAMON resources; a retry after the worker
  exits finalizes normally. Previously the join result was ignored, so a blocked
  sampler could append records to a finalized session.
- Fix (Sol B3): an unconfirmed DAMON teardown is no longer indistinguishable
  from "DAMON off". `stop` (and `summary.json`/`manifest.json`) now carry
  `damon_cleanup: {confirmed, slot, quarantined}` whenever a DAMON owner was
  closed, including after a runtime DAMON failure. The workload verdict and the
  `ok: true` stop are unchanged.
- Fix (Sol B1): placement now requires every selected, still-live process to
  be listed in its recorded origin unit's census immediately before transfer.
  A process that left the unit since capture is refused
  (`place-refused:identity-unavailable`) and no scope is created or attached,
  instead of being moved out of an unrelated unit and later restored to a stale
  origin.

<!-- cmru: release history -->

## [1.0.0] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=f4933a2d5a6d1f1ab460d5ff85caeed269af7a99 -->

### Added
- feat(assay,ciu,cmru,cgroup-profiler,nyxloom,run-gate): complete integrated release wave (1fa737782)
- feat(run-gate): make wheel SemVer the only supported interface (9a0e192ab)
- feat(cgroup-profiler): add contract-2 resource observer (eb3c77b35)
- feat(cmru): pass protected release source identity to artifact steps (b310458e9)
- feat: add contract-2 resource event attribution (90dc9078d)
- feat(rg55): adopt observer contract 2 and local events (2b1bd7f39)
- feat(cgprofile): expose local and hierarchical event evidence (135bcb4da)
- feat(rg-89): observe native mutation resource limits (219997c93)
- feat: add estate cli version compatibility (05f373a40)
- feat(skills): add canonical single-tool skills for ciu, run-gate, assay, cmru, cgprofile (33c0b0c27)
- feat(cgroup-profiler): build the r0-r1/r2/r3/gate lane matrix, restore 100% coverage (d5df216d1)
- feat(cgroup-profiler): log-tail phase source; fix lib/ silently excluded from git (712abdcac)
- feat(cgroup-profiler): container/cgroup profiler with effective limits, phases and reports (4af86d571)

### Fixed
- fix(cgroup-profiler): refuse pids_localevents in the R2 observer; clarify release qualification (bc48c179f)
- fix: bound memory for ephemeral gate lanes (731be2b4e)
- fix: close release-wave review findings (49e4be092)
- fix: bound Assay refusals and harden observer setup (ad173675f)
- fix(cgprofile): make host daemon lifecycle selectable in ciu (e3f13ada4)
- fix(cgprofile): preserve worktree git mounts in gate (1be40b03a)
- fix: bind image builds to exact source identity (a0ad6f40d)
- fix(cgprofile): refuse placement that empties origin scope (465f67d06)
- fix(rg89): serialize shared-leaf R2 and verify peer capability state (cb155eab1)
- fix(cgprofile): serialize R2 attribution and retry peer ESRCH snapshots (5bc86f977)
- fix(cgprofile): retry transient peer snapshot ESRCH (ace790e94)
- fix(cgprofile): consolidate DAMON guidance and harden NSpid guard (aa5edcde6)
- fix(cgprofile): align observer oracle and DAMON guidance (178552d0b)
- fix(cgprofile): close resource-observer mutation gaps (75d286dab)
- fix(cgprofile): fail closed on missing socket bind source (930f0bbab)
- fix(run-gate): support observer-backed command lanes (c9d488fc1)
- fix(cgprofile): wire observer into native R2 lane (cd0e947e5)
- fix(rg55): align daemon PID mode and preserve profile summaries (3c77a9314)
- fix(cgprofile): use host PID namespace for the daemon (50372b71f)
- fix(cgprofile): preserve placement recovery across failed start and retention (753ea04c5)
- fix(cgprofile): report cleanup quarantine truthfully (a8a53fb43)
- fix(cgprofile): tolerate unavailable optional DAMON lock (d8056454e)
- fix(cgprofile): serialize DAMON registry ownership safely (c5d89652f)
- fix(cgprofile): keep DAMON startup optional for one-shot runs (591c258d8)
- fix(cgprofile): reserve DAMON capacity and quarantine uncertain stops (c8f7c70e9)
- fix(cgprofile): skip unchanged token DAMON recommits (5c3b4f0d2)
- fix(cgprofile): isolate DAMON targets and degrade runtime failures (181201f5b)
- fix(cgprofile): start DAMON from configured sysfs inputs (c2db510fc)
- fix(cgprofile): preserve shared proc parser offsets (6733bef5e)
- fix(cgprofile): validate helper container id through shared utility (83ca2f5d1)
- fix(cgprofile): recover safely after leaf removal (4636a9c43)
- fix(cgprofile): pass explicit base through P1 acceptance gates (a7dc7b4c1)
- fix(cgprofile): close P1 proc-identity oracle gaps (18bf438ff)
- fix(cgprofile): verify scope retirement during restore and preserve socket refusals (d48d1ed8e)
- fix(cgprofile): publish only the versioned GHCR target (5d3c108e6)
- fix(cgprofile): publish only the versioned GHCR target (8723f3064)
- fix(cgprofile): release provisional DAMON owners and refuse session ID collisions (aa4d11734)
- fix(cgprofile): stream P1 summary accumulation (2e130da3e)
- fix(cgprofile): bind proc stat to requested pid (e8221c6bc)
- fix(cgprofile): place and recover lanes under delegated scopes (831a789e1)
- fix(cgprofile): reject malformed socket request shapes (753a57881)
- fix(cgprofile): distinguish unreadable cgroup tree from absent target (b88d4f07e)
- fix(cgprofile): accept completed capped helpers after update race (02c92591c)
- fix(cgprofile): preserve omitted ctl options and sync contract mirror (a1a82c991)
- fix(cgprofile): address P6 round-5 blockers (da0662877)
- fix(cgprofile): fail closed on PID migration write errors (bfe6a2e8a)
- fix(cgprofile): restore placed survivors across private PID namespaces (738bf1f5c)
- fix(cgprofile): refuse phantom system bus bind source (e102a62ff)
- fix(cgprofile): move private-namespace PIDs through host systemd (7eb544186)
- fix(cgprofile): refuse explicit empty build version (158488ccb)
- fix(cgprofile): derive daemon identity from release tag (dfef6bad8)
- fix(cgprofile): preserve host observation with private namespaces (d794c2362)
- fix(cgprofile): fail closed without probe parent (5b9d1bc10)
- fix(cgprofile): enforce placed gate container caps (3ce083491)
- fix(cgprofile): restore CMRU release identity (97c055927)
- fix(skills): repair defects found by independent review of new canonical skills (abcb6e274)
- fix(cgroup-profiler): a test's own "known broken" shebang path isn't universally broken (eda33a84a)
- fix(run-gate): each project's own assay.toml also needs the tester-venv python (943db7c82)
- fix(run-gate): estate-wide sweep of environment="host" lanes broken by RG-43 (f62642c69)
- fix(cgroup-profiler): caps.py limit-erasure bug, report_html XSS, unwired resample (95ace544b)
- fix(cgroup-profiler): place the helper outside the tier it profiles (77dff4428)

### Changed
- chore: integrate contract-2 release wave (336336d81)
- chore(cgprofile): pin cockpit daemon release image (9c152110d)
- review(cgprofile): record R2 cap audit (06e7804ea)
- Add oracles for RG-89 input-boundary survivors (42a76af89)
- Retry peer identity scans on procfs disappearance (fa5e348a8)
- Remove dead resource observer test fixture assignment (60303e0db)
- Strengthen resource observer survivor oracles (75d9c4f0e)
- Merge main into rg89-resource-observer (3cd1b1444)
- cgprofile: record P1 round 8 fix verification (eb909889f)
- Merge branch 'main' into rg55-damon-start-commit-fix-main-20261005 (f0cf27517)
- Merge main into cli-extended-unified before release (ce1696562)
- harden(cgprofile): validate proc PID inputs (63e4fe919)
- refactor(cgprofile): share proc and safety helpers (cb22e88ec)
- cgprofile: model busctl subprocess output semantics (c3a0d7865)
- cgprofile: assert rejected slice-name boundaries (4728f2c0f)
- backlog(cgprofile): CP-15, CP-16 reviewed against SPEC-V8 draft.11: count mode is 8.0, byte budget v8.1 (dstdns D-666) (0c4f72e9e)
- backlog(cgprofile): CP-15/CP-16 scoped to v8.1 admission (dstdns D-658) (c8def3121)
- Merge branch 'main' into rg55-p1-final-20261001 (55d44fcd4)
- backlog(cgprofile): CP-16 combined per-sample hot+warm Summary series for ciu v8's warm-set charge (dstdns D-658) (e7a5ed5c0)
- backlog(cgprofile): CP-15 note -- optional after D-655 (Docker-object reservations) (4a46ee439)
- backlog(cgprofile): CP-15 reservation verb for consumer-decided admission (ciu v8 V8-31) (9b4bf835e)
- refactor(cgprofile): remove inert P1 mutation boundaries (4d097c97e)
- Merge branch 'main' into rg55-p1-final-20261001 (e0ce67dc0)
- revert: drop duplicate P1 oracle tests (1a098eb51)
- merge(cgprofile): reconcile P6 with current main (3f8d119ba)
- Refresh P1 checkpoint for latest Nyxloom main update (1063fc5f5)
- Refresh P1 checkpoint for latest main (bd915d7f9)
- Refresh P1 reconciliation and review checkpoint (a617f8763)
- Clarify P1 review artifact gate sequence (6aa6427f4)
- Record current P1 base and gate deferral (bd4cb3453)
- Refresh P1 round-five review checkpoint (27fd457d3)
- Record P1 mutation survivor oracle backport (902a6f5f6)
- Add direct oracles for proc identity survivor gaps (01912a917)
- Clarify exact percentile state memory growth (032b707a3)
- Document exact percentile state growth (c003cd3c2)
- Record focused P6 blocker regressions (28345cecb)
- Point P6 preflight evidence at RW-398 (298e4157e)
- Normalize parallel RG-55 controller rulings (6e8178e75)
- Record RG-55 host preflight for P6 (cd24d73c9)
- Record RG-55 host unit preflight evidence (fb1f1d154)
- perf(cgprofile): share online DAMON rank trees (c81b28372)
- review(cgprofile): record P1 round4 rejection and evidence holds (1a9451553)
- review(cgprofile): fix socket test synchronization and state daemon authority (0c361de93)
- Merge branch 'rg55-p1-release-review-20260930' into rg55-followups-cgprofile-final (80e6d8d2b)
- review(cgprofile): record round-6 host-unit block and safe bus fixes (bfcf69368)
- cgprofile: record P6 final review round 4 rejection (ca8fccc7c)
- cgprofile: fail closed on unreachable watch kills and repair status timestamp (9c0a6e39a)
- cgprofile: record P6 final review and live-probe block (5e37a7a62)
- cgprofile: describe stream identity and private PID namespace accurately (5ef643605)
- cgprofile: align design and release guide with private namespaces (7bcdc37d9)
- cgprofile: reject watch EOF without a terminal end (d206877ad)
- cgprofile: count only complete progress events and cover leaf-creation race (d858852b3)
- cgprofile: bind placement writes to exact token leaf and guard progress streams (fd77a4c44)
- Merge branch 'main' into rg55-followups-cgprofile-final (41c6fba6d)
- Cover helper PID identity resolution (d20153732)
- Preserve helper PID target identity (a2d79242f)
- Clarify shared cgroup profiling attribution (f76f1f015)
- Include runtime project metadata in cgprofile image (e93a40fcc)
- rg55: cap P1 container lanes before execution (285ffa8cb)
- merge: reconcile P6 with private-namespace P1 fixes (d14b90707)
- cgprofile: constrain profiler PID attribution to target cgroup (bc5566b23)
- cgprofile: preserve private-namespace host PID attribution (0f478314e)
- merge cgprofile release and gate infrastructure (35d3732fe)
- merge: reconcile P6 with current main (ae923b88b)
- review(cgprofile): record reconciled P6 controller pass (4392bece3)
- Merge current main for P6 source-backed assay (cc9d13b60)
- review(cgprofile): record controller P6 conditional review (13e394a01)
- Merge current P1 daemon fixes into P6 follow-ups (bb1042a67)
- review(cgprofile): record controller P1 adversarial review round 2 (3455999ae)
- chore(cgprofile): reconcile P1 daemon with current main (ced406593)
- backlog(CP-4): cmd_targets hardcodes repo_dir==out_dir, Docker refuses the duplicate mount (c01451d75)
- chore: land run-gate root and dev-gates migration (41c1cafb3)
- chore(cgprofile): materialize P6 final tree linearly on current main (c362c8dff)
- chore(cgprofile): materialize P1 final tree linearly on current main (9b70a46e9)
- plan+log(rg55): P6 review handoff; RW-45 -- builds under PSI, frozen-golden version string, CP-10 root cause (7d7298522)
- contract+log(rg55): RW-43 -- P4 round 1 rulings (rusage via os.wait4, exec inflight stamp), contract §4.3a bare-host lanes, round file committed (2ec076e18)
- plan(rg55): P6 handoff (cgprofile follow-ups: CP-2 socket, CP-4..CP-7, cgprofile.slice, CP-8 watch, CP-9 placement) + RW-35 (7c8e55738)
- contract(rg55): v1.1 amendment (RW-34) -- two carriers, watch streaming, placement, liveness policy, gates slice (6b719d223)
- contract(rg55): RW-21 scope-container absolute counters + RW-11 file list -- goldens regenerated (3b75e1df1)
- plan(rg55): adversarial review handoffs for P1 (daemon) and P2 (run-gate client) (ead72a735)
- plan(rg55): P1/P2 implementer handoffs (dispatched 2026-09-12 in parallel per RW-1) (6f827c296)
- plan(rg55): P0 -- interface contract frozen, golden fixtures, RG-56/RG-57 + CP-1..3 filed, SPEC-V8 D.6, controller log (63b928da0)

### Documentation
- docs(cgprofile): preserve Sol stop design review (290459db4)
- docs(cgprofile): preserve Opus stop handoff review v2 (b4ef3b2d6)
- docs(cgprofile): preserve sampler handoff review (8dca2089a)
- docs(cgprofile): preserve Opus release review follow-up (a7c512a5f)
- docs(cgprofile): preserve independent release reviews (eae8d0fc1)
- docs(cgprofile): record live placement acceptance (8f5e5eae2)
- docs(cgprofile): document OCI release notes (9943239c2)
- docs(cgprofile): name installed Run-Gate wheel consumer (e5d01bef1)
- docs(rg55): align consumer identity with Run-Gate SemVer (39754e0b0)
- docs(cgprofile): align Run-Gate wheel identity contract (9ba133fe3)
- docs(cgprofile): close CP-22 after gate proof (31f09341b)
- docs(cgprofile): keep consumer identity wording aligned with oracle (9688cd435)
- docs(cgprofile): clarify R2 cap sentinel plan (34417c42e)
- docs(cgprofile): record P1 round-9 adversarial review (3d6df01ff)
- docs(cgprofile): explain DAMON exclusivity and test EOF edge (b5804ee6d)
- docs(cgprofile): fix DAMON_STAT troubleshooting anchors (868ff7c64)
- docs(cgprofile): document DAMON_STAT availability boundary (a56fe0c3a)
- docs(rg55): record bind and observer trust boundaries (ae11d4240)
- docs(cgprofile): correct host PID namespace claims (e89aeff89)
- docs(cgprofile): correct daemon namespace description (b9d41a141)
- docs(cgprofile): record P1 final merge review acceptance (056ffbec5)
- docs(rg55): record P1 fix verification and campaign evidence (7da5f0eb5)
- docs(cgprofile): record P1 survivor review (ca352d8cd)
- docs(rg55): record P1 round-5 provisional acceptance (5becf9893)
- docs(cgprofile): record latest-main reconciliation (e2e077fbd)
- docs(cgprofile): record current-main reconciliation (385526194)
- docs(cgprofile): record DAMON repair gate history (e764f582d)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(cgprofile): link DAMON startup semantics to kernel source (a3735a42e)
- docs(rg55): disposition P1 supplemental Sol review (8bd5d0a8e)
- docs(rg55): record P1 supplemental Sol acceptance (bf7dc95fb)
- docs(cgprofile): preserve P1 base-propagation review finding (8e6b2475d)
- docs(cgprofile): add P1 supplemental review packet (d7078fe51)
- docs(cgprofile): clarify P1 supplemental review scope (9d66a9434)
- docs(cgprofile): checkpoint P1 main reconciliation (d97ecae44)
- docs(cgprofile): identify DAMON stop evidence field (de06f750d)
- docs(cgprofile): align full gate advisory window (2f3689ef3)
- docs(cgprofile): clarify P6 campaign estimate (13fae541e)
- docs(cgprofile): clarify DAMON capability vs readiness (3f1cd1910)
- docs(cgprofile): record P6 review and gate receipts (0bc3f3ddb)
- docs(cgprofile): record P6 survivor oracle review (faed6fe25)
- docs(cgprofile): record round-7 P6 fix verification (2c4c5ad74)
- docs(rg55): record P6 exact-gate failure and repair (175ed5d84)
- docs(rg55): record P6 socket EOF oracle (95fcbe9d0)
- docs(cgprofile): record P6 focused oracle correction (123e182ef)
- docs(cgprofile): record P1 final Sol review (415ce74aa)
- docs(cgprofile): document pinned GHCR deployment (e32cb6179)
- docs(cgprofile): record P1 adversarial review round 5 (c2716520e)
- docs(cgprofile): record P6 blocker regression rerun (dae606f06)
- docs(rg55): checkpoint current P1 review blockers (db044d4d3)
- docs(rg55): refresh P1 review checkpoint and attached-tree policy (148481e4a)
- docs(cgprofile): record P6 round-seven blocker repairs (415bbc798)
- docs(cgprofile): add round 7 live carrier evidence (d1b2963ef)
- docs(cgprofile): record P6 final adversarial review rejection (b526eb5c1)
- docs(cgprofile): record round-6 exact-tree short gates (a98b443f2)
- docs(cgprofile): refresh P6 review contract (477a8e7a8)
- docs(cgprofile): record P6 main reconciliation and host preflight (2d555fe12)
- docs(cgprofile): record P6 exact-tip short-gate results (0eb2686c9)
- docs(cgprofile): record P6 exact-tip coverage failure (3ec4cba0a)
- docs(cgprofile): checkpoint P6 after B107 merge (cc25b517d)
- docs(cgprofile): bound P6 brief inventory in review handoff (14fae51e3)
- docs(cgprofile): checkpoint P6 survivor restoration work (a65d2f37a)
- docs(cgprofile): record P6 replacement R2 outcome (d612dbf41)
- docs(cgprofile): document system bus placement prerequisite (6d1ed8a96)
- docs(cgprofile): prepare P6 provisional review (6859bc3e9)
- docs(cgprofile): clarify P1 evidence limits (7e39857d8)
- docs(cgprofile): record P1 Sol review round three (b0544d50a)
- docs(rg55): reconcile P1 review state to current main (3f3777662)
- docs(rg55): refresh P6 review safety contract (1300fca3a)
- docs(rg55): record fresh P1 short gates (b871cd644)
- docs(rg55): record P1 coverage and CIU findings (43daf53e0)
- docs(cgprofile): clarify helper PID identity (8df969674)
- docs(rg55): refresh P1 reviewer packet rulings (ec2882090)
- docs(rg55): record P1 terminal mutation evidence (d94c58b9e)
- docs(rg55): record P1 main reconciliation (1908316b2)
- docs(rg55): refresh private-daemon review policy (0daa66e26)
- docs(cgprofile): record terminal P1 R2 rerun (199850969)
- docs(cgprofile): close P6 reconciliation report checkpoint (8e52ea083)
- docs(cgprofile): align adopter examples with 1.1.0 (54e0a3645)
- docs(cgprofile): record current R2 survivor disposition (9471a9af7)
- docs(cgprofile): disambiguate post-wave backlog identity (32ec2b3d6)

### Testing
- test(cgprofile): cover observer host permission failures (2dfa8204a)
- test(cgprofile): cover observer refusal and cleanup branches (c9f769691)
- test(cgprofile): cover release identity refusal paths (e00aac9d8)
- test(cgprofile): raise R2 cap for current candidate plan (27ddf251a)
- test(cgprofile): cover unterminated observer overflow (cedfc4598)
- test(cgprofile): normalize deployment contract wording checks (6aa0a8b5a)
- test(cgprofile): match the documented helper cgroup wording (25deff6ce)
- test(cgprofile): synchronize socket readiness without clients (0afa24a6e)
- test(cgprofile): hold token process through PID scan (b2a270ee9)
- test(cgprofile): wait for socket listener readiness (e520b855d)
- test(cgprofile): synchronize token process readiness (e7a6b7594)
- test(cgprofile): wait for listening socket before client connect (1653143f7)
- test(cgprofile): close P1 R2 survivor oracles (c24b0d2b8)
- test(cgprofile): cover DAMON quarantine and restore failures (b9ea60801)
- test(cgprofile): align pool counter fixtures with reconciliation (b30eca821)
- test(cgprofile): exercise scope recovery after leaf removal (b6b2dc659)
- test(cgprofile): close P1 survivor oracles (f13f76db9)
- test(cgprofile): strengthen P1 mutation survivor oracles (99db6d034)
- test(cgprofile): cover P1 R2 summary and proc boundaries (38e383499)
- test(cgprofile): budget the full P6 mutation inventory (87064fb26)
- test(cgprofile): reject empty systemd array replies (58eeac96c)
- test(cgprofile): exercise captured systemd unit output (df4dd1fc5)
- test(cgprofile): cover placement retirement proof paths (45f575bd0)
- test(cgprofile): remove obsolete PID-addressability oracle (de7245aa2)
- test(cgprofile): exercise unterminated requests over real socket (9651d9152)
- test(cgprofile): assert delegated moves use systemd (67f45c28b)
- test(cgprofile): restore P6 mutation survivor oracles (a1d6d545d)
- test(cgprofile): replace probabilistic run ID oracle (7d7171b60)
- test(cgprofile): distinguish proc stat PID guard from helper refusal (d11adc412)
- test: cover malformed proc stat delimiter (80263df66)
- test: cover late placement identity recovery paths (f6a366160)
- test: cover placement identity and retired-scope failures (ae0a17a93)
- test: include placement defaults in ctl request fixture (5b62ef028)
- test(cgroup-profiler): cover orphan placement recovery paths (bb106d724)
- test(cgroup-profiler): cover placement ownership and recovery paths (1e0d5a2cb)
- test(cgprofile): align D-31 fixtures and documentation oracles (d5b524eff)
- test(cgprofile): describe shared peak oracle accurately (3a8327afc)
- test(cgprofile): distinguish persistent helper from completed client (07bc03477)
- test(cgprofile): cover partial request timeout in round-6 gate (e48d1d3f7)
- test(cgprofile): cover incomplete kill without logger (f48726036)
- test(cgprofile): close P6 coverage gaps (d5fd17eb5)
- test(cgprofile): assert PID migration refusal diagnostics (e7bca65e4)
- test(cgprofile): close P6 mutation oracle gaps (6540f8776)
- test(cgprofile): cover silent cgroup kill failure (b3df5602a)
- test(cgprofile): cover placement refusal cleanup branches (93264604a)
- test(cgprofile): cover namespace placement failure paths (b282fe79e)
- test(cgprofile): close P6 mutation survivor oracles (2f3fd2c90)
- test(cgprofile): close P1 mutation survivors (3e6e657e0)
- test(cgprofile): align private-proc placement oracles (aae66356b)
- test(cgprofile): cover CLI usage headline (a2c2501fe)
- test(debian-install-v2): harden isolated VM lane (d472d58d8)

<!-- cmru: pending release notes begin -->
### Added
- First SemVer release of the `cgprofile` CLI and host daemon, including the
  separate read-only contract-2 observer for native Assay R2 lanes. The
  observer reports event counters for the candidate cgroup and its enforcing
  ancestors without exposing the daemon control socket.

### Changed
- Documentation: identify the installed Run-Gate SemVer wheel as the supported
  consumer of the CGProfile interface; copied `run-gate.py` scripts are retired.
- Gate R3 now declares memory and swap ceilings, so Run-Gate admits it against
  the gates-slice budget and Docker enforces its per-container limit.
- The native R2 resource observer refuses a host cgroup2 mount using
  `pids_localevents`: its fork-failure counter records the cgroup where the
  fork was attempted, so it cannot identify which ancestor's `pids.max`
  rejected it. Successful responses always report
  `pids_events_scope = "hierarchical"`; `memory_localevents` remains supported.
- Documentation: the native-R2 lane example declares the required
  `resources.memory` and `resources.pids`.
- Release qualification: CMRU's release gate runs `run-gate r0-r1` and
  `run-gate r3` only. The complete Run-Gate `gate` lane additionally runs the
  24-hour `r2` mutation campaign. The initial 1.0.0 release has no completed
  full-source R2 verdict; the long R2 campaign was deferred under the
  operator's release policy.

### Documentation
- The README, design guide and consumer guide now describe the GitHub Release body CMRU
  publishes for each release (`cmru handler oci-image-release`, the last command of the push
  step, after the image push): its pull instruction and tagged notes, the
  `cmru release --dry-run` preview, and what CMRU verifies before and after writing it.

### Migration
- Native R2 consumers that use CGProfile resource observation must move to
  observer contract 2 and the observer-only endpoint
  `/run/cgprofile/observer/resource-observer.sock`. Contract-1 resource
  observer requests are refused; the separate daemon control protocol remains
  contract 1 on `/run/cgprofile/ctl.sock`.
- Host observer setup must provision the `cgprofile-observer` group and
  `/run/cgprofile/observer` as `0751 root:cgprofile-observer`. On vbpub hosts,
  apply MDT's observer-only host setup before using the endpoint. This is
  required only for lanes configured to use the native R2 observer.
<!-- cmru: pending release notes end -->
