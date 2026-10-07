# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

<!-- Empty on purpose: KI-30 refuses a tagged release while this body is non-empty, and KI-23
     refuses one while a hand-authored `## [6.0.0] - UNRELEASED` heading exists, so this file
     carries no 6.0.0 draft. The operator upgrade guide is `docs/UPGRADING-6.0.md`; the
     pre-6.0 hand-written text is recoverable at `68a03b4fe^:cmru/CHANGES.md`.

     6.0.0 release notes (LANDPREP, 2026-10-06): `cmru release` generates the `## [6.0.0]`
     section below from the commit subjects since `cmru-v5.5.0`; a hand-written 6.0.0 section
     cannot coexist with it (the generator refuses to overwrite one). The pre-wave hand-written
     [Unreleased] bullets (versions/age-window, OCI rolling tags, secret-overlay and mutation
     hardening) describe work already shipped in 5.5.0 or covered by those commit subjects, so
     nothing from them is lost by leaving this body empty. The breaking changes (command
     grammar, exit codes, retired cmru-agent/cmru-controller, cli-extended as a wheel
     dependency) are in `docs/UPGRADING-6.0.md`.

     PROVISIONAL RELEASE: the R2 mutation campaign for 6.0.0 was postponed by operator
     decision 2026-10-06 and is tracked as KI-62 (`KNOWN_ISSUES_TODO_BACKLOG.md`); the
     release ran `gate-provisional`, whose evidence carries `.assay/mutation-postponed-cmru.json`.
     The release-notes commit subject (`docs(cmru): 6.0.0 release notes ...`) states this in the
     generated Documentation list. TODO(cmru-6.0 post-release): once KI-62 is closed, append a
     one-line pointer to the 6.0.x notes.

     HOTFIX (KI-63, 2026-10-06): the release-gate secret-overlay inventory no longer aborts on
     a directory owned by another uid that it cannot list (e.g. the live Mattermost postgres
     volume); it skips it with a stderr WARN. The release-notes generator picks this up from
     the `fix(cmru): ...` commit subject. -->

<!-- cmru: release history -->

## [6.0.0] - 2026-10-07
<!-- cmru: generated -->
<!-- cmru: source-end=e5fed335bb8984369204805f47f682a8416e5349 -->

**Summary (hand-written post-release curation).** cmru 6.0.0 is a breaking release built
around a redesigned command line (hard renames, a fifth exit code, `cmru-agent` and
`cmru-controller` retired). It adds the installer and host-enrollment mechanism (W1:
project-owned `get.py` extensions), makes `cli-extended` a real wheel dependency instead of a
vendored copy, gives `tester-gate` an `--init` PID 1 plus pids limits and hardened signals, and
hardens the release gate (secret-overlay inventory, hermetic tests, provisional gate with the
R2 mutation campaign postponed under KI-62). Upgrade steps and the old-to-new command table are
in [docs/UPGRADING-6.0.md](docs/UPGRADING-6.0.md); the list below is generated from commit
subjects only.

### Added
- feat(cmru): LANDPREP provisional gate: --postpone-mutation, gate-provisional lane, KI-62 (208c60ba5)
- feat(cmru): CMRU-FLOOR: cli-extended floor 0.3.0, remove KI-61 tolerance, surface sync (e09438f4a)
- feat(cmru): W3-ZERO build/release from zero releases, multi-level dependency graph + pyproject guard (2b842b3ca)
- feat(cmru): W3-PREP - retire origin-only release candidates (KI-35), env-leak fix, standards r5, 6.0.0 changes draft, sweep plan (bce3ab8af)
- feat(cmru): W2-PKG5 item 4 + T5: policy flip to "report", findings, exploration-verb tree-unchanged tests (1ea4b49c1)
- feat(cmru): W2-PKG5 item 3: surface lifecycle, reviewed catalog, S-CLI.9 generated region, pytest plugin (7e99b63eb)
- feat(cmru): W2-PKG5 items 1-2: skills verbs, SKILL rewrite, doctor (AC-19/AC-20) (e361069d1)
- feat(cmru): W2-INTEG cross-package seams (CMRU_INTERNAL_* env, run-step delegate removed, REFUSED alias dropped, SPEC S8, rename sweep, interactive extra, estate contract guard) (f20cfcf74)
- feat(cmru): W2-PKG1 status lists every selected project, transaction exception narrowing (906291cbe)
- feat(cmru): W2-PKG1 root CLI adoption and redesign (checkpoint) (e4b8d0017)
- feat(cmru): W2-PKG2 delegate registries on the shared factory, redesign B8-B12, exit-code taxonomy (d1318694b)
- feat(cmru): W2-PKG4 packaging: cli-extended as a real wheel dependency (fd2a60a79)
- feat(cmru): W2-PKG0 registry factory and library-selector target adapter (a7272ace5)
- feat(cmru): W1-INSTALLER finish: INS-18, rollback message, signature docs, authoring guide, plant table, report (c664861b3)
- feat(cmru,ciu): installer extensions mechanism; move get.py enroll into ciu (W1-CIU-ENROLL, O4) (3aada2d08)
- feat(cmru): W0-TESTER tests, tester-unified image hardening, docs, backlog (76432bb35)
- feat(cmru)!: retire cmru-agent/controller; CLI-04/05/14/18 + CLI-T1, doc drift (W0-RETIRE) (f6fef1d6b)
- feat(cmru): tester-gate hardening core (--init, pids limit, events wrapper, names, signals, DinD limits, digest pins) (577053efc)
- feat(cmru): authenticate transactional Git operations (7c2d7a8be)
- feat: resume mutations with additive test suites (2dca79d55)
- feat: adopt cli-extended and resolve CMRU CLI decisions (f6b577f41)

### Fixed
- fix(cmru): installed-wheel smoke removes the egg-info/build its in-tree wheel build created (5f6d855ba)
- fix(cmru): set the fake gates cgroup parent inside the env-restore fixture (15cbfba33)
- fix(cmru): hermetic release-gate tests; changelog finds its generated section by heading line (496d2f38e)
- fix(cmru): release-gate overlay inventory also skips vanished paths (KI-63) (bd9304a43)
- fix(cmru): release-gate overlay inventory skips foreign-owned unreadable directories (KI-63) (2e02c95c4)
- fix(cmru): W2-PKG5 round 1 - cover the multi-family preflight refusal, drop the dead ReleaseLockHeld branch in abandon (ReleaseLockHeld is now a CliFailure) (7e12b7780)
- fix(cmru): W2-PKG5 review round 1 - retained-record validators raise UnsafeRecord, findings re-synced, REPORT fix-round section (4335f5751)
- fix(cmru): W2-PKG5 review round 1 - sweep of user-reachable refusals into the domain-error family, T5 runs the validators and non-empty tool-deps/versions, SKILL/SPEC wording, staleness guards, DNS sandbox, doctor timeout pin (6dd3a5627)
- fix(cmru): W2-PKG5 review round 1 B1 - one domain-error family (CmruError), exit codes per taxonomy, StepFailed; cli-extended backlog CLI-EXT-27..29 (2245651f2)
- fix(cmru): restore cmru.runner.run_step (MDT build-push.py consumes it); estate import guard (2bb245f64)
- fix(cmru): W2-INTEG review round 1 (C1-C7, run_step removal) (508bc83d6)
- fix(cmru): W2-PKG1 review round 1: forward parsed globals to transaction children, fail closed on empty mode values, pin run --step (6aa9964e1)
- fix(cmru): W2-PKG2 review round 1 (resolve installer loader, HTTPException, template render catch, get-py no-installer exit 2, tests) (7a54514ce)
- fix(cmru): W2-PKG4 review fix round 1 (bootstrap dist-info, https redirects, pinned image, skill symlink) (eedf2f164)
- fix(cmru,ciu): review fixes for W1-CIU-ENROLL (checker gaps, hard-fail drift guard, CIU-131) (2ac2fe2ea)
- fix(cmru): review fixes - file: strategy resume, doc corrections, survivor tests, backlog KI-55..58 (6288ecbe6)
- fix(cmru): W0-GATE review fixes - deterministic gate-report tests, sample/template handler argv, git probe env, mutant-killing tests, CIU-129/NL-31 filed (35b7f2968)
- fix(cmru): review fixes for W0-RETIRE (warn once, estate config, docs) (531e48659)
- fix(cmru): status never configures release logging; candidate trailer; promotion/recovery docs (CLI-01, REL-08, REL-04/05 docs) (53584bb0a)
- fix(cmru): W0-GATE - skip the tls-edge test when the gate fixture lacks that tree (14466be48)
- fix(cmru): W0-GATE - canary fixture closure (tls-edge) and self-contained bootstrap test (31f6be6cf)
- fix(cmru): W0-GATE - gate diagnosability, bound handler launcher, git scope, bootstrap (14e5f013f)
- fix(cmru): merge-promote on rejected fast-forward, tag rollback after failed build, resilient local-main sync (REL-04, REL-05, REL-06, REL-13) (f1228d288)
- fix(cmru): legacy abandon ignores tags below the base; one commit-id pattern (REL-03, REL-14) (2b7ea2ae5)
- fix(cmru): regenerate stale generated changelog section; refuse non-empty [Unreleased] (REL-02, REL-10, KI-30) (68a03b4fe)
- fix(cmru): internal-handoff guards exit 1 instead of escaping RuntimeError (REL-01, KI-54) (06791b345)
- fix(cmru): KI-53 nits - forward only SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*, spy mount_root (a536dc9fd)
- fix(cmru): KI-53 wheel-build mounts the worktree root and forwards build env (890fda002)
- fix(cmru): reject malformed release snapshot output (fccd44be3)
- fix(cmru): handle multi-family preflight errors (536b83dca)
- fix(cmru): make run-gate mode explicit (759444fc1)
- fix(cmru): classify resolved config aliases consistently (5b162b385)
- fix(cmru): harden config aliases and tag retries (036d27da9)
- fix(cmru): guard retained release retries (c2102f828)
- fix(cmru): pin preflighted family release snapshots (3d584ae9f)
- fix(cmru): preflight release policy from origin snapshots (95ee25d73)
- fix(cmru): preflight all families before release dispatch (042639460)
- fix(cmru): preflight local tag inspection support (ea4c2ec37)
- fix(cmru): require explicit local tag lookup status (f96245fc1)
- fix(cmru): complete retained release branch coverage (55dcc5e14)
- fix(cmru): preflight retained publication targets (05095c5f5)
- fix(cmru): preserve tag lookup diagnostics (e30172722)
- fix(cmru): confirm ambiguous local tag absence (c17970954)
- fix(cmru): use explicit ref existence status (5131f7e67)
- fix(cmru): distinguish absent tags from ref lookup errors (f9a87d54d)
- fix(cmru): verify host slice and tag ref records (aa5c87a69)
- fix(cmru): reject unverified cleanup inventories (a26c0bd87)
- fix(cmru): harden retained release paths (e6f63ca33)
- fix(cmru): close release review findings (849f29f78)
- fix(cmru): refresh legacy worktree before resuming (0eaf74d8a)
- fix(cmru): select published ancestor baseline across merges (ddbb11171)
- fix(cmru): protect release gate credentials and baselines (5f5f19d08)
- fix(cmru): strip publisher credentials from abandon probes (59618778e)
- fix(cmru): correct generated askpass quoting (04c5f4901)
- fix(cmru): authenticate Git and freeze cleanup plans (aba42a3a9)
- fix(cmru): preserve tester CPU parse diagnostics (489ce8849)
- fix(cmru): enforce effective tester CPU ceiling (356d259f2)
- fix(cmru): bind sibling sources in release subprocesses (ec95f86b1)
- fix(cmru): reject empty artifact coordinates (231c91d5e)
- fix: tighten CLI review semantics and tester-gate previews (44411a624)
- fix: resume mutations across unrelated monorepo commits (a6c4274be)
- fix: anchor CMRU Assay coverage to release tag (98f98d380)
- fix: pass PATH through CMRU Assay lane (9c71d58e4)
- fix: include CLI dependencies in mutation fixtures (9c78d26e8)
- fix: make empty CMRU runs true no-ops (a6ed9457c)

### Changed
- Merge cmru-relgate-fix: hermetic release-gate tests, changelog finds its own generated section, wheel smoke cleans its build artifacts (e5fed335b)
- Merge nyxloom-successor-2026-10: extract presets (watch/successor/review/ledger), option groups, intent pairing, stop state, effects (9d162f711)
- chore(cmru): LANDPREP image/lane revert - REQUIRES RETAG of tester-unified:cmru6-integ to tester-unified:local (e9fd37c53)
- Merge cmru-w3-prep: KI-35 sweep plan + origin-only abandon, env-leak fixture, standards r5, UPGRADING-6.0, compat audit (8e04e5545)
- chore(cmru): gate lanes on tester-unified:cmru6-integ (temporary), exact module-entry dispatch assertion (5150de172)
- merge(cmru-w2-pkg1): root cmru CLI on cmru_registry, D2 metadata version, redesign B1-7 (run --step, status --json, release --ahead-check-ref, explicit publish/cleanup modes, abandon BRANCH|PATH), exit 4, globals forwarded from the parsed namespace; reviewed ACCEPT after REJECT round (d8ac6b66d)
- merge(cmru-w2-pkg2): nine delegate registries on cmru_registry, redesign B8-12 (resolve --repo/--prefix, --bake-target, forward-*-slice, init prompt driver), exit 3/4, narrowed exceptions; reviewed ACCEPT (5baa68888)
- W1-INSTALLER round2: symlink-clause tests, manifest key grammar, install_dir shape, bundle-manifest errors, M49b, docs (abb6840cd)
- W1-INSTALLER round1: close coverage gaps (bundle_files, variant pass-through), skip tls-edge script test when absent (d1aed127f)
- W1-INSTALLER round1: bundle-manifest verb (--name), SPEC inventory, REPORT section (9c519aded)
- W1-INSTALLER round1: strengthen survivor tests (M05 M24 M59, setuid observable) (056219e0b)
- W1-INSTALLER round1: tests, docs (CONSUMERS authoring guide, SPEC S6), INS-18 leftovers (b25d57d09)
- W1-INSTALLER round1: template/producer fixes (WIP, tests next) (7193017cf)
- wip(cmru-w1-installer): generic get.py installer rewrite (fail-closed, per-release dirs, rollback, offline hash-locked wheels, signature policy); checkpoint (c15d5d873)
- Merge branch 'cmru-wave-2026-10' into cmru-w1-enroll (b311cd3b4)
- Merge cmru-wave-2026-10 into cmru-w0-tester; W0-TESTER review fixes (e582c7461)
- Merge cmru-wave-2026-10 (b9ed0cc01) into cmru-w0-rel (c542a082b)
- merge(cmru-w0-gate): BG-03 no-maxfail + named failures; BG-04/REL-07 bound cmru handler; REL-11, BG-09/10/11 (b9ed0cc01)
- x (26ada0687)
- refactor(cmru): split release/status branch into _release_launcher/_release_child/_status (REL-12) (58bc7c0c3)
- Merge main into cli-extended-unified before release (ce1696562)
- Merge CMRU snapshot safety fixes (d4b17b93b)
- Merge reviewed CMRU release fixes (2ca59036d)
- Follow config symlinks in release snapshots (a54350344)
- Verify release snapshots and follow moved configs (8d64c8fa9)
- Fix CMRU release snapshot policy checks (071046398)
- Preserve adopted metadata in resume migration test (4a1c3e83a)
- Exercise malformed local tag output in regression test (cf2d35378)
- Validate build output ID before path inspection (c8542a5c9)
- Assert resume scope refusal through CLI contract (6369a8457)
- Fix release cleanup and resume edge cases (54f297dac)
- backlog(ciu,cmru): file the shipped CIU-93/KI-24 enrollment defects from v8 round 4 — CIU-122, CIU-123, KI-49, KI-50 (dstdns D-658) (6ccfffb98)
- Merge branch 'cmru-cli-decisions' into integration/cmru-local-cleanup-20260930 (7e639e1dc)
- merge: nyxloom testability doctrine + cmru KI-35 (docs/testability-cleanup-20260928) (5248b345d)
- merge: sync CMRU CLI work with current main (b14643995)
- Document CMRU worktree library adoption (c64a1c1d7)
- Document CMRU module and consumer interfaces (f78c392c3)
- Strengthen CLI semantic boundary coverage (a3620b149)
- Adopt cli-extended and audit CMRU CLI (83ab24cc6)
- Record canonical CMRU CLI semantic audit (0a93ae4b6)
- Resolve CLI source roots for scaffold validation (4de03ca51)
- Include sibling imports in CMRU CLI probes (481c7ebac)
- Add sibling CLI source roots to CMRU gate (131453a9e)
- Adopt cli-extended across CMRU command surfaces (c54e90581)

### Documentation
- docs(cmru): CMRU-LANDPREP report (122ca13ca)
- docs(cmru): CMRU-FLOOR C1 - cli-extended floor text 0.2.0 -> 0.3.0 (UPGRADING, tester-unified README, findings remedy, SPEC) (6bc3f860c)
- docs(cmru): 6.0.0 release notes: R2 mutation postponed (KI-62), provisional release, see UPGRADING-6.0 (58247078b)
- docs(cmru): CMRU-FLOOR report (d243dd770)
- docs(cmru): move 6.0 narrative to UPGRADING-6.0.md, unblock release changelog preflight (525b43376)
- docs(cmru): W3-ZERO report gate results (c690eca9a)
- docs(cmru): W3-PREP report - gate verdicts and plants (1be285b09)
- docs(cmru): W2-PKG5 REPORT - fix round 1 lane verdicts (4789f962a)
- docs(cmru): W2-PKG5 REPORT (06d296a41)
- docs(cmru): W2-INTEG REPORT round-2 lane verdict (947e588d7)
- docs(cmru): W2-INTEG REPORT round-1 lane verdicts (308be74b8)
- docs(cmru): W2-INTEG REPORT part C (dcc4d19ae)
- docs(cmru): W2-PKG1 REPORT review fix round 1 (ae4959bbf)
- docs(cmru): W2-PKG2 REPORT review fix round 1, D-A handoff (3d0220fcd)
- docs(cmru): W2-PKG1 REPORT (plant table, coverage, handoff to W2-INTEG); drop continuation (ee8228814)
- docs(cmru): W2-PKG2 REPORT (supersedes the continuation), handoff to W2-INTEG (d446e68dd)
- docs(cmru): W2-PKG4 review fix round 1 report (8d4ca29fd)
- docs(cmru): W2-PKG4 report (825d6660c)
- docs(cmru): W1-INSTALLER round 2 report (3a8064552)
- docs(cmru): W1-INSTALLER round 1 report results (1054fde5a)
- docs(cmru): W1-INSTALLER report gate results (2fc5e7dca)
- docs(cmru): W1-CIU-ENROLL report, release-gate env fix (87e54f967)
- docs(cmru): W1-CIU-ENROLL report (46a29514e)
- docs(cmru): program plan — Wave 0 merged, Wave 1 sequential order, CLI-01 under W0-REL (fa402bd39)
- docs(cmru): file KI-59 (file: resume bump-commit subject match too loose) (53687365e)
- docs(cmru): W0-REL report - review fixes, merge, gate verdicts (3da4aa7e0)
- docs(cmru): W0-GATE report addendum (de87545f3)
- docs(cmru): W0-RETIRE report, review fixes (44c583c74)
- docs(cmru): W0-REL report - gate verdicts, coverage findings, deviations (4ba5c90fb)
- docs(cmru): W0-TESTER report gate verdicts (50de2b7d0)
- docs(cmru): W0-REL report; remove the continuation file (4fdc3ae90)
- docs(cmru): W0-RETIRE report (final) (c4d17b36b)
- docs(cmru): W0-GATE report (b60dbe1c4)
- docs(cmru): W0-RETIRE report (draft) (059241d83)
- docs(cmru): program 2026-10 plan (fix-up, retirement, adoption, 6.0.0) (baf0ea295)
- docs: discard contaminated Assay R2 outcomes (6882167d0)
- docs(cmru): file KI-54 (pre-existing red handoff dry-run test on main) (40f044ad8)
- docs(cmru): KI-53 report (4455cb6c3)
- docs(backlogs): file host zombie incident — cmru KI-52, assay B145, run-gate RG-83 (2d1db004f)
- docs(backlogs): W10 review nits (assay A-005 offline install, cmru KI-51 install order) (0c4b265e5)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(ciu): SPEC-V8 draft.10 — T4-07 trust root in the controller's wheel; one grouped [admission] switch, default off; the ticket count mode and published count subset move into 8.0; enrollment proposal rev 4 (dstdns D-661) (1fe6e7564)
- docs(ciu): coverage memo §8 round-3 note; correct line cites in CIU-122/KI-49 (dstdns D-658) (fe0fb5364)
- docs(cmru): clarify tagged head baseline exclusion (2f3b64a62)
- docs(cmru): reconcile FEAT-03 release status (4efd5f334)
- docs: add tester-gate contract anchor (898447dc5)
- docs: apply fresh review to the testability doctrine and cmru KI-35 (851d1648e)
- docs: add mutation-testability design guidance; file cmru KI-35 (703505aa5)
- docs: preserve mutation timeout contract wording (3f391d6ea)
- docs: specify nested CLI group help behavior (7358159d2)

### Testing
- test(cmru): LANDPREP skip the ciu enroll render test while the extensions shim is active (959bd4019)
- test(cmru): CMRU-FLOOR: interactive extra floor assertion to 0.3.0 (cdf54a91d)
- test(cmru): skip estate-wide W3-ZERO tests in the isolated canary tree (aad6e70ac)
- test(cmru): W2-PKG5 doctor error-path coverage; black-box probe uses the suite interpreter path, not the image's installed cmru (e2387889b)
- test(cmru): W2-INTEG estate guards skip dangling symlinks in the canary sparse snapshot (review B2) (74505c9d0)
- test(cmru): estate guard minimum contract count applies to a full checkout only (2f03c240d)
- test(cmru): estate guard requires a sibling's contract only when it exists on disk (sparse canary snapshot) (dc71f9dd6)
- test(cmru): estate-scan sanity check tolerates the canary lane's sparse snapshot (fae4cf516)
- test(cmru): remaining resolve fakes supply the forge config (review B1 fallout) (a22ca4daa)
- test(cmru): resolve fakes load the forge config the way the real path does (e54742120)
- test(cmru): CLI-19 asymmetric option-value case (5f4869dfb)
- test(cmru): stop the registry test leaking the time-prefix env into later tests (d92c96ce1)
- test(cmru): the handler entry assertion follows the child's own distribution view (b3a929329)
- test(cmru): source-module handler entry is exit 3 without an installed distribution (D2) (b5abb94d7)
- test(cmru): cover the legacy parse_target_names None branch (W2-PKG0 coverage gate) (630669a9e)
- test(cmru): cover the unrenderable-value branch of _py_literal (60a18093e)
- test(cmru): cover extension scope analysis branches (9f777fccb)
- test(cmru): restore standards DinD tests lost in merge, cover blank events lines, widen canary skip guard (457e2628f)
- test(cmru): close coverage to 100% (REL-05/08/12 branches, seven arcs already missing on the integration branch) (969e9ee59)
- test(cmru): end-to-end suite sets its own git identity for in-process promotion (found by the coverage lane) (bd91df968)
- test(cmru): skip repository-root image/config tests in the isolated canary tree (aad0ecda4)
- test(cmru): cover signal-handler scoping and shared .cmru directory; docker standards pass case (f3ba542bf)
- test(cmru): keep the non-agent tests from the retired dedicated files (68884bb5c)
- test(cmru): real-git end-to-end release suite (REL-15, m24; plant/revert for REL-04/05/06/13) (9eaafed42)
- test(cmru): select explicit targets in handoff tests (0f96d124f)
- test(cmru): initialize unpromoted candidate fixture repo (5a8b636f5)
- test(cmru): initialize promotion failure fixture repo (ca663b6dd)
- test(cmru): initialize resume source repository (9aded85d2)
- test(cmru): isolate resume policy ordering (beb122cae)
- test(cmru): complete candidate tag fixture (e91b01fdc)
- test(cmru): avoid recreating candidate repo root (afefb38fa)
- test(cmru): initialize snapshot config fixture repo (d6dc05e74)
- test(cmru): unmask origin tag policy checks (119572c90)
- test(cmru): complete origin policy fixture (8dc9bf0c5)
- test(cmru): complete family snapshot config fixtures (7b30e65f7)
- test(cmru): stabilize merge tag tie timestamps (ee1ca832e)
- test(cmru): restore snapshot handoff environment (99956795f)
- test(cmru): stub snapshot handoff marker cleanup (d07fb4e7c)
- test(cmru): allow snapshot fetch before git preflight (2345fab36)
- test(cmru): match local tag listing row order (b4cda901c)
- test(cmru): stub parent release preflight (e0429b17b)
- test(cmru): normalize doc assertion whitespace (2aec743a9)
- test(cmru): stub cleanup in synthetic release (08e926129)
- test(cmru): scope release policy stub to synthetic resumes (f913fba7f)
- test(cmru): stub sidecar cleanup in fake release cases (48899a6ec)
- test(cmru): stub release refusal marker cleanup (18d11ff6d)
- test(cmru): assert full origin tag advertisement (d45ad82a2)
- test(cmru): use canonical release tag in refusal case (0e1986bcb)
- test(cmru): assert release diagnostics on stderr (945d4f1c5)
- test(cmru): assert release refusal on stderr (8d882526c)
- test(cmru): assert publish race diagnostic on stderr (3ea2baf20)
- test(cmru): cover concurrent release identity changes (406030bfc)
- test(cmru): supply abandon tag inspection facts (2ef45bb06)
- test(cmru): intercept leased abandon deletion (82edf2aef)
- test(cmru): intercept leased candidate deletion (5b52ace34)
- test(cmru): model local tag presence checks (8cd2452b8)
- test(cmru): avoid pinning retained scan count (0f7c5e39f)
- test(cmru): update abandon remote push fixture (280601f3d)
- test(cmru): isolate release snapshot writer (28e1e700b)
- test(cmru): stub origin tags in release harnesses (eb759fcfa)
- test(cmru): declare unmanaged release assets (203a1e944)
- test(cmru): stub origin tags in launcher failure (463f77471)
- test(cmru): include cleanup release asset inventory (2d69cf933)
- test(cmru): include release tag format preflight (f742cbacd)
- test(cmru): model tag preflight in push warning (663778e83)
- test(cmru): include strict tag ref checks (7a0e6d545)
- test(cmru): assert ambiguous package 404 wording (8d96f9a2b)
- test(cmru): fail closed on malformed tag refs (05cbc37d4)
- test(cmru): match malformed ref diagnostic (a42623899)
- test(cmru): allow safe recheck short circuit (7649f9d9a)
- test(cmru): match cleanup preview version (ad2762e38)
- test(cmru): request captured cleanup output fixture (c7689c79a)
- test(cmru): expect local tag outcome recheck (c8760ec14)
- test(cmru): accept cleanup dry-run keyword (3997195b7)
- test(cmru): isolate abandon remote boundary fixtures (efa7d4e94)
- test(cmru): assert legacy workspace record purpose (5311ba1c8)
- test(cmru): supply release github config fixtures (a9bf5b58d)
- test(cmru): remove secret backup ordering assumption (1ffb26925)
- test(cmru): align release gate loader mock (0a7f20469)
- test(cmru): checkpoint legacy release resume fixture (cae5f9e3a)
- test(cmru): create candidate source root fixtures (d5adfcd15)
- test(cmru): initialize release tag fixture head (6a02f2d60)
- test(cmru): inspect hook directory within git call (9c381ccee)
- test(cmru): check release changes in a git repo (e88a5df83)
- test(cmru): include token in release config fixture (3eeb95316)
- test(cmru): match Assay first-parent diagnostic (fcaaac728)
- test(cmru): prepare origin facts before moving local tag (14f650fdc)
- test(cmru): pass explicit argv to baseline checker (4bbdb5e26)
- test(cmru): assert cleanup plan is applied (caece5b17)
- test(cmru): provide saved scope to resume fixture (05e0549e9)
- test(cmru): match tag push transport invocation (308987ba4)
- test(cmru): isolate malformed scope remote guard (c1a62ee02)
- test(cmru): validate legacy transaction child fixture (d537f6feb)
- test(cmru): account for cleanup plan dispatch (5177e6a49)
- test(cmru): assert cleanup preview identity handoff (ca5126809)
- test(cmru): match successful tag deletion calls (ea8dc1e36)
- test(cmru): align abandon fixtures with remote preflight (7b799b776)
- test(cmru): align cleanup fake with clean-step contract (700f0f936)
- test(cmru): cover overflowing tester CPU exponents (53757b2a2)
- test(cmru): use valid tester CPU resolver values (4324e57c1)
- test(cmru): cover malformed build coordinates (cf37ed112)
- test(cmru): model existing Git common directory (fad90a5a9)
- test(cmru): close CLI mutation survivors (c83db8862)
- test: cover tester gate cgroup refusal (e26f35c5e)
- test: supply registered agent dry-run default (1588856cc)
- test: cover remaining observable CLI semantics (046bf12c9)
- test: cover CLI semantic boundaries for mutation resume (07b38d541)
- test: cover cli-extended mutation fixture copy (7774d1561)
- test: include estate manifests in CMRU fixture (4e4d9e6b9)
- test: harden CLI semantic checks and transaction context (9b2273d58)
- test: cover CMRU CLI branch alternatives (635decab3)

## [5.5.0] - 2026-09-26
<!-- cmru: generated -->
<!-- cmru: source-end=456164d528b11adea23b8812094bb510d4d5cfb4 -->

### Added
- feat(cmru): adopt age-windowed version tracking estate-wide (910b151d)
- feat(cmru): add age-windowed version resolution (dddf988e)

### Fixed
- fix(cmru): include estate manifests in gate fixtures (3b0254ff)
- fix(cmru): split release R2 from main-based assay lane (3e94c6d4)
- fix(cmru): resolve child worktree project configs correctly (0e96f960)
- fix(cmru): map credential policy fields correctly (2bcd3ad9)
- fix(cmru): reject empty registry URL fragments (441696f7)
- fix(cmru): close version resolver review gaps (1427dbf6)
- fix(cmru): exclude strict Go pseudo-version bounds (417e5c46)
- fix(cmru): timebox and resume mutation campaigns (57ba11d3)
- fix(cmru): handle Go pseudo-versions and workspace rollback (c3b3a692)
- fix(cmru): prepare external versions during dry-run (3629bf12)

### Changed
- merge(cmru): FEAT-03 age-windowed version resolution (325736a1)
- Merge CIU CMRU shared workspace instance (bf02c233)
- doc(cmru): add backlog KI-29 — Release abandonment needs a first-class (239ee8c5)
- test qualify shared worktree and deterministic properties (b6ce9752)
- test CMRU workspace listing mutation survivors (0da8f158)
- fix shared CIU and CMRU workspace ownership review (84b7919d)
- Share native Git worktree inventory across CIU and CMRU (b9cad87c)
- Adapt dry-run test to project scope preflight (61ff5e9d)
- bound mutation candidates and document gate liveness (343d17e7)
- harden workspace lifecycle and gate oracles (8d634211)
- Merge branch 'main' into feat/ciu-cmru-workspace-instance (15e1a346)
- cover transaction failure branches (4115f9e7)
- ci: raise serialized tester gate memory ceiling (e518a9fd)
- Implement CIU CMRU workspace instance plan (d1eb9877)

### Documentation
- docs(cmru): specify registry URL and redirect limits (e97803f8)
- docs(cmru): record FEAT-03 gate result (9a7ef680)
- docs(assay): close v7 release records and log Wave C findings (36723684)

### Testing
- test(cmru): cover rolling age cutoff and evidence boundaries (bbb9205d)
- test(cmru): normalize documented gate contract whitespace (7041dc84)
- test(cmru): make mutation witnesses deterministic (b399fa95)
- test(cmru): cover empty root version resolution (e4f34c0c)
- test(cmru): close mutation review survivors (b10bc5b0)
- test(cmru): cover registry edge paths (e5b9e3de)
- test(cmru): keep npm target declared in malformed lock case (beaeaf0f)
- test(cmru): align resolver diagnostic assertion (e66d661c)
- test(cmru): accept missing-host URL diagnostic (d323659f)
- test(cmru): model killed mutation exit code (5951dc85)
- test(cmru): cover mutation resume timeout policy (b27c252a)
- test(cmru): close Go age evidence mutation gaps (7b76df47)
- test(cmru): cover Go workspace detection errors (cb2a32ab)
- test(cmru): exercise remaining Go failure branches (ccc83d2a)
- test(cmru): cover Go proxy missing metadata paths (9dd4b535)
- test(cmru): exercise strict versions config loader (687cce59)
- test(cmru): cover version config dispatch edges (94d07303)
- test: preserve CMRU documentation link fixtures (5e8eb9b5)
- test: include shared worktree library in CMRU fixtures (f5b32813)
- test: isolate CMRU runtime-kind validation (6d8a5e06)
- test: close final CMRU mutation witnesses (7b7b51a6)
- test: close CMRU mutation survivors and Git spawn retries (67c45c8d)
- test(cmru): assert dry-run consumes prepared version (521c6d65)

## [5.4.1] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=39c2a8f94c42965ad3bd07570580a2811ab34426 -->

### Fixed
- fix(cmru): resolve central config for project runner steps (06f1d19c)

### Testing
- test(cmru): cover central runner project mismatch (39c2a8f9)
- test(cmru): pin runner context in local config contract (1ebacd54)

## [5.4.0] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=240caa0f7776390a902f58e7c1c32ee69e176902 -->

### Added
- feat: add estate cli version compatibility (05f373a4)

### Changed
- Merge branch 'feat/estate-cli-version-20260919' (0795ebb9)
- cmru: retain declared release gate evidence (1e149e49)

## [5.3.1] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=32950335ea21e1b22f9dea04920cfb1a6a69246d -->

### Changed
- cmru: promote release candidates after publication (1a7b5941)
- merge: adopt contextual cmru configuration (f4151963)
- cmru: complete contextual config migration fixes (3af75077)
- cmru: adopt contextual config and positional targets (16961d15)
- chore: land run-gate root and dev-gates migration (41c1cafb)

### Documentation
- docs: record cmru and isolated gate lane plans (6091cfef)

### Testing
- test: declare gates tier in cmru config fixtures (8c56f6b3)

## [5.3.0] - 2026-09-17
<!-- cmru: generated -->
<!-- cmru: source-end=32511a4273f266bb202030da18056b461f79c2e6 -->

### Added
- feat(skills): add canonical single-tool skills for ciu, run-gate, assay, cmru, cgprofile (33c0b0c2)

### Fixed
- fix(skills): repair defects found by independent review of new canonical skills (abcb6e27)

## [5.2.2] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=2bc07098c881d10ecbb6cdc522d3c20a39a9abdb -->

### Fixed
- fix(cmru): preserve ignored caller content during cleanup (6b476268)
- fix(cmru): preserve dirty caller main during release cleanup (d775bb40)

### Changed
- refactor(gates): consume assay from selected worktree source (cd4d19b0)
- review(cmru): final dirty-sync adversarial acceptance (b1150e6d)

### Documentation
- docs(cmru): clarify external assay refresh scope (f1d54948)
- docs(gates): close stale assay pin guidance (a8f04686)
- docs(cmru): record release sync final review (443e4d75)
- docs(cmru): record cleanup fix verification (f6972c98)
- docs(cmru): record dirty-main cleanup review (48e99c7f)

### Testing
- test(cmru): collect interrupted rebase coverage (700ef5f8)
- test(cmru): isolate interrupted rebase fixture (2eff6bdd)
- test(cmru): replace rejected release sync survivor oracles (e224579c)
- test(cmru): close release sync mutation survivors (08692bf2)
- test(cmru): cover release sync defensive outcomes (164b114c)

### Fixed
- fix(cmru): refuse cleanup rebase from dirty caller `main` including ignored content, preserve its files/ref, and report the per-call cleanup reason on every release outcome

## [5.2.1] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=188a67259c4394c67214613a1ba9067562b79b11 -->

### Fixed
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 6.1.0 -> 6.1.1 (assay-v6.1.1) (dd8b07f1)
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 6.0.0 -> 6.1.0 (assay-v6.1.0) (ec868f14)

## [5.2.0] - 2026-09-09
<!-- cmru: generated -->
<!-- cmru: source-end=617d387ed5b6481cd1d2d85561269dc78d40c8d1 -->

### Added
- feat(cmru): retain release logs+artifacts by DEFAULT, --discard-*-on-release opts out (f3713a8b)

### Fixed
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 5.2.0 -> 6.0.0 (assay-v6.0.0) (ec0bc47f)
- fix(cmru): backlog sweep -- KI-19/20/21/23/25, all verified live (8e3d1ae7)
- fix(cmru,ciu,nyxloom): re-pin stale assay gate zipapps to 5.2.0, file KI-27 (583faad7)

### Documentation
- docs(cmru backlog): KI-26 -- get-py cannot render any project's get.py from an installed cmru (61c842d7)

### Testing
- test(cmru): kill the workspace_purpose two-part-branch survivor (617d387e)

## [5.1.0] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=34d0717ae7f4039111b8aade8f132f16ea2a5a53 -->

### Added
- feat(cmru): get.py enroll -- bare-host enrollment for every rendered installer (KI-24) (745046b5)

### Fixed
- fix(cmru): KI-24 review fix -- warn on silent multi-key coexistence, file KI-25 (ed511ddb)
- fix(run-gate): estate-wide sweep of environment="host" lanes broken by RG-43 (f62642c6)
- fix(cmru): run-gate.toml's own assay pin was missed by tool-deps --refresh (6121eec9)
- fix(cmru,run-gate): RG-29 -- cmru/run-gate.toml's assay pin still named the vanished 2.2.0 sidecar (0ad5372d)
- fix(run-gate): RG-1 conjunction overrides forwarded + override-reachability guard (d0401ed0)

### Changed
- chore(cmru): tool-deps --refresh assay -- 5.0.0 -> 5.1.0, including run-gate.toml's own pin (a4addfc5)
- chore(cmru): tool-deps --refresh assay -- 4.1.0 -> 5.0.0 (bc4fb12a)
- backlog(cmru): file KI-23 -- generate_release_changelog never detects a hand-authored `- UNRELEASED` draft section, duplicates instead of folding (8d7aa615)
- chore(cmru): re-pin the judge 2.3.0 -> 4.1.0 (run-gate rev 33 passes --resume/--progress, floor 2.4.1) (b36c6925)
- backlog(cmru): file KI-21, KI-22 -- worktrees crash on a slash-less release branch; a build failure leaves an orphaned tag (8e200938)
- backlog(cmru): file KI-20 -- status/release ahead-of-origin check reads shared local main, no --ref override (f6b386f2)
- chore(consumers): repin assay-v2.3.0 (841d89c8)
- Merge branch 'main' into run-gate-rg-sweep (72cc1f47)
- run-gate RG-20: resource-aware admission — slice RAM budget + shared-infra locks (5b7535bc)
- run-gate RG-10: declared artifacts + evidence-path disclosure on every lane exit (6f859529)
- chore(deps): repin consumers to released assay 2.2.0 (42b6a0de)

### Documentation
- docs(cmru): KI-24 implementation REPORT -- per-oracle evidence + gate verdict (35d312c3)
- docs(cmru): plan KI-24 -- get.py's enroll subcommand (dc86a828)
- docs(ciu,cmru): host enrollment rev 2 for both lines -- ciu host enroll (two steps, no token/callback), v8 SPEC-V8 draft.7 S7.2.4 + V8-29, v7 SPEC.md S14.7 backport, cmru KI-24 get.py enroll subcommand, proposal rev 3.4, round-4 review prompt (b19880bc)
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)

## [5.0.0] - 2026-08-23
<!-- cmru: generated -->
<!-- cmru: source-end=6711d260974f859dacd947db6cffbdc0511f19da -->

### Added
- feat(cmru): cmru init guided scaffolding + wheel-shipped templates (2f2cd234)
- feat(cmru)!: tester-gate cgroup-parent is DECLARED-CONFIG — supersedes the fallback tier (223e0bb0)
- feat(cmru): declared cgroup-parent fallback tier for bare-host gate runs (b76bb2d3)

### Fixed
- fix(cmru): adversarial-review round — forward-var floor, io-preflight wiring tests, explicit-empty convention, probe honesty (98a27f42)

### Changed
- backlog: file run-gate adversarial-review findings — RG-1..14 (new backlog), cmru KI-19 (mutation skip emits no evidence), assay B011 (stale cross-tool wiring example) (75593bcc)

### Testing
- test(cmru): pin the write-loop/validate mkdir contract site-scoped (6711d260)
- test(cmru): kill the 13 mutation survivors from the release-gate campaign (e8edb08b)
- test(cmru): restore 100% branch coverage over the tester-gate/init wave (b2470223)
- test(cmru): xdist-safe tester-gate main tests — declared cgroup-parent model, unscoped-launch contract, forward var via CMRU_TESTER_CGROUP_FORWARD_VAR (9ea87a32)

<!-- cleared 2026-09-09: this was a stale, never-renamed `[Unreleased]` draft
     block sitting between [5.0.0] and [4.1.1] -- everything it described
     (get.py enroll/KI-24, cmru init scaffolding, tester-gate cgroup-parent
     DECLARED-CONFIG) is already captured in the real [5.0.0]/[5.1.0]
     sections above. Found stale while auditing for unreleased ciu/cmru
     work on 2026-09-09; this is the exact failure mode this file's own
     process note (top of file) describes -- removed rather than folded
     since the detail duplicated what [5.0.0]/[5.1.0] already say. -->

## [4.1.1] - 2026-08-22
<!-- cmru: generated -->
<!-- cmru: source-end=a1f7288b68e2281b9e0d58dcb7681ac7b98d2324 -->

### Changed
- run-gate(cmru): mutation lane skips with notice when the source diff vs the verified-ancestor release tag is empty — a src-unchanged release is ungatable otherwise (d9d9f541)
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- cmru(FEAT-03): DECIDED contract — cmru versions {refresh,check}, cmru.orchestration.toml schema (window+pins+resolved state), python+npm+go, per-project cmru.toml overrides, explicit-flag-only build integration (8819ce67)
- cmru(FEAT-03): proposed — central version determination as a dated constraints artifact (uv --exclude-newer age window, explicit refresh, cmru-distributed, ciu-independent) (fbead753)

## [4.1.0] - 2026-08-19
<!-- cmru: generated -->
<!-- cmru: source-end=0b920f806b4aedcc12014ebb028b917858450de0 -->

### Added
- feat(cmru): ciu-align KI-16 naming (flat 1:1) and fix KI-17 gate env preflight (2df06459)
- feat(cmru): S15 declared tool dependencies, and three checks that are not one (9c056def)
- feat(cmru): KI-13 unchanged reasons, KI-14 dry-run parity, KI-15 backup cleanup (8248fcc2)
- feat(cmru): KI-12 release-plan integrity, KI-16 transaction naming (cab5da5d)
- feat(cmru): add pinned Assay gate lane (97ecfb97)

### Fixed
- fix(cmru): mutation gate diffs previous release tag, not origin/main (KI-18) (0b920f80)
- fix(cmru): kill the last two mutants -- one weak assertion, one equivalent (d3b7212f)
- fix(cmru): S15 -- a 404 is not a bootstrap, and four smaller conflations (4be6adea)
- fix(cmru): keep Assay rigor bounded by its snapshot contract (4b8009d5)
- fix(cmru): make promotion retry bound explicit (a230070b)
- fix(cmru): reject disappeared retained artifacts (61f1b570)
- fix(cmru): preserve race-safe retention finalizer (7932471d)
- fix(cmru): restore retained outputs across filesystems (ba28e795)
- fix(cmru): rollback retained outputs after move failure (3fd573c0)
- fix(cmru): preflight release retention atomically (356bb9f8)
- fix(cmru): report missing wheel metadata (10b21e46)
- fix(cmru): fail closed on malformed Consul read values (033a5667)
- fix(cmru): reject invalid desired signatures (9a0f7269)
- fix(cmru): declare Assay test import environment (2acb96e9)
- fix(cmru): reject ambiguous release artifacts (d0cb9248)
- fix(cmru): reject unknown version bump levels (3fea6521)

### Changed
- chore(cmru): refresh assay tool-dependency pin 1.0.0 -> 2.1.0 (S15 freshness) (02836036)
- backlog(cmru): KI-12..KI-16 shipped; KI-17 stays open (5c671413)
- merge(cmru): KI-12..KI-17 and S15 tool dependencies (712ca915)
- review(cmru): adversarial review of S15 tool dependencies (e2c8a137)
- review(cmru): adversarial review of KI-12/KI-16 -- the fourth tag state (390e0508)
- backlog(cmru): correct KI-12(b) -- the two-state rule was wrong (1d4a1991)
- backlog(cmru): KI-12..KI-16 -- five findings from assay's 2.1.0 release (db09ada9)
- Merge commit '525af743' (6a69ac16)
- Merge commit 'a230070b8b9d553882af1d8c567513855e459a8a' (d5e1858b)
- Merge commit 'e08f93c1' (8446bb8f)
- Merge commit '978d4a41b0f3344f43e88e4b00c4df2c4cb88bcd' (85545d90)
- Merge commit '8244d51c71c9ff2cae7d86cb1818f334fd1a7018' (03cdde4b)
- Merge commit '0b9264e52fa8b297f0ec20463c7c47f184d27f9d' (6454b93c)
- Merge commit '5ae43f04' (4868229f)
- Merge commit '4e72041a0c0878463dc40849513daa01eaaf58b0' (9dac40dd)
- Merge commit 'c65501d8' (58352b7b)
- Merge commit '8a1f9b908c0319b76b44e43a64f82dc9e78c9b69' (24d8cace)
- Merge commit '9cf15031' (aeb70972)
- Merge commit '57cbd7ba' (0aea8060)
- Merge commit '786bf1bd' (60da769d)
- Merge commit '61f1b570' (5fbf3dff)
- Merge commit 'b38c7944' (b82972c5)
- Merge commit '7932471d' (10f5fed5)
- Merge commit 'e0ed459f' (99a81532)
- Merge commit '722715d8e52b2e719e090598efd6305431df358b' (d7110190)
- Merge commit 'c0170dd8' (7ca011bb)
- Merge commit '94f95530' (e82066b1)
- Merge commit '8a84b7d64f0a780d61ef08627427a1a7b37bbf6b' (6074ad2a)
- Merge commit 'b28b39b1' (defb62a5)
- Merge commit '4cd619d5' (60b7898d)
- Merge commit 'a4a38f0b4e47a8d892b18672135218a63986b2bf' (c66b67a0)
- refactor: require supported tomllib runtime (a4a38f0b)
- Merge commit 'ba28e795' (80aa8deb)
- Merge commit 'e3d3a33c' (faa8c85d)
- Merge commit '1231349719d9e0007dc38ca9446e8793ac5817c8' (f867e516)
- Merge commit '80fbe7e1' (9d7456a9)
- merge(cmru): review final CLI refusal dispatch (cdbae62f)
- merge(cmru): review CLI refusal safety coverage (0803964e)
- merge(cmru): review runtime residual safety coverage (e57f4a12)
- merge(cmru): review final residual contracts (b35e1d83)
- merge(cmru): review final config transaction policy coverage (4b23557c)
- merge(cmru): review high impact release coverage (841bb303)
- merge(cmru): review residual config transaction coverage (50901a43)
- merge(cmru): review final operational residuals (68ebe752)
- merge(cmru): review top-level cleanup dispatch coverage (b95f28e9)
- merge(cmru): review high-risk retention coverage (505b3c8c)
- merge(cmru): fail closed on malformed wheel metadata (0799eaf4)
- merge(cmru): review CLI cleanup coverage (f7b04b52)
- merge(cmru): review residual operational coverage (61636df0)
- merge(cmru): review CLI operation safeguards (57ca2f11)
- merge(cmru): review transaction retention coverage (0dc0b0c5)
- merge(cmru): review config version coverage (7f0c92f2)
- style(cmru): remove test trailing blank (9266e9b6)
- merge(cmru): review config transaction safety coverage (7f9b3c47)
- merge(cmru): review version runner coverage (02419602)
- merge(cmru): review runtime contract coverage (c883968f)
- merge(cmru): review CLI dispatch protocol coverage (8479dbb8)
- merge(cmru): review runtime boundary coverage (f2c625b2)
- merge(cmru): review smaller boundary coverage (b249f6c8)
- merge(cmru): review operational refusal coverage (fed4fdfb)
- merge(cmru): review CLI dispatch coverage (24d4b6c3)
- merge(cmru): review high residual boundary coverage (a63274c5)
- merge(cmru): make abstract contracts explicit (759eaca4)
- merge(cmru): review remaining runtime coverage (582f59ed)
- merge(cmru): review remaining release transaction coverage (f65c15cd)
- merge(cmru): review 100 CLI config coverage (66e20000)
- merge(cmru): review 100 runtime coverage (02c78165)
- merge(cmru): review exhaustive release agent coverage (bba48724)
- merge(cmru): review exhaustive config runtime coverage (df57ca9b)
- merge(cmru): review final runtime release coverage (b7b20a1f)
- merge(cmru): review final build release coverage (7df31477)
- merge(cmru): review final non CLI coverage (0089914d)
- merge(cmru): review final release transaction coverage (869a5f85)
- style(cmru): remove test eof whitespace (6ca7caf2)
- merge(cmru): review remaining release core coverage (8651ad13)
- merge(cmru): review CLI release orchestration coverage (3c9cd635)
- merge(cmru): review CLI cleanup coverage (f9c7209a)
- merge(cmru): review deep runtime coverage (cabd06c7)
- merge(cmru): review deep release transaction coverage (9abc1f26)
- merge(cmru): review execution boundary coverage (0144698a)
- merge(cmru): review artifact release coverage (6ae4ffd7)
- merge(cmru): review agent controller coverage (22787d06)
- merge(cmru): review release boundary coverage (2642d4de)

### Documentation
- docs(cmru): scope CONTRIBUTING to cmru, point elsewhere for the general (7c8a7265)
- docs(cmru): CONTRIBUTING -- what actually bites you, and KI-17 (a8e66f04)

### Testing
- test(cmru): gate complete mutation and coverage evidence (c499f4fb)
- test(cmru): enforce Assay coverage mutation and canary gate (0fd3dd36)
- test: cover agent status optional dispatch branches (e11d4322)
- test(cmru): cover existing unknown worktree branch (525af743)
- test(cmru): close final CLI branch alternatives (005c2455)
- test(cmru): cover remaining CLI branch alternatives (e08f93c1)
- test(cmru): cover final non-cli branch contracts (978d4a41)
- test: close non-cli release and rollout branches (8244d51c)
- test(cmru): cover final CLI branch alternatives (e19776db)
- test(cmru): cover CLI branch alternatives (cae9788e)
- test(cmru): assert handler release contracts (0b9264e5)
- test(cmru): cover CLI defensive boundaries and module guard (cdfe164e)
- test(cmru): cover runtime branch alternatives (ff5b18c2)
- test(cmru): cover empty child release result (2151b243)
- test(cmru): cover child release completion paths (5ae43f04)
- test: close final dependency and standards branches (4e72041a)
- test(cmru): cover release launcher exception boundary (32f6f026)
- test(cmru): cover release behind-main path (c65501d8)
- test: close dependency and version branch paths (fbf10c8b)
- test: isolate output presentation environment state (8a1f9b90)
- test(cmru): cover behind-main build warning (489f1dd5)
- test: cover core parser and public guard paths (7e5c3bf2)
- test(cmru): cover release revert outcomes (9cf15031)
- test(cmru): close small operational module gaps (57cbd7ba)
- test(cmru): cover release output retention dispatch (fdd45772)
- test(cmru): cover no-tag release refusal (786bf1bd)
- test(cmru): cover sequential no-build release paths (b38c7944)
- test(cmru): cover publish dispatch and pagination (e0ed459f)
- test(cmru): cover public module entrypoints (7cd74084)
- test: correct atomic cleanup and cli guard witnesses (722715d8)
- test: cover small non-cli operational paths (5ed92884)
- test(cmru): cover transaction collision and cleanup races (1091a122)
- test(cmru): close final runtime residuals (c0170dd8)
- test(cmru): cover CLI pagination and asset dispatch (94f95530)
- test: cover resolver and agent operational edges (8a84b7d6)
- test(cmru): cover remaining CLI no-op guards (b28b39b1)
- test(cmru): cover runtime and operational residuals (4cd619d5)
- test: cover remaining config validation branches (de948030)
- test(cmru): cover resumed release cleanup path (e3d3a33c)
- test: cover config and changelog validation paths (12313497)
- test(cmru): cover isolated build failure boundaries (31574cba)
- test(cmru): cover CLI control-flow clusters (f9f745fb)
- test(cmru): cover current CLI semantic gaps (b3022be4)
- test(cmru): cover release REST contracts (80fbe7e1)
- test(cmru): cover delegated CLI dispatch (dc8ada6d)
- test(cmru): cover final CLI refusal and dry-run paths (894b206e)
- test(cmru): cover small final operational contracts (bfa0717d)
- test(cmru): assert cleanup refusal messages (5d11fc13)
- test(cmru): cover final dispatch and variant contracts (dd1cabad)
- test(cmru): cover CLI refusal safety contracts (ed39e7da)
- test(cmru): narrow release retention collision oracle (f98843d0)
- test(cmru): cover runtime residual safety contracts (cad03088)
- test(cmru): cover final residual contracts (436fb20f)
- test(cmru): cover release and cleanup dispatch paths (659bfced)
- test(cmru): cover final config transaction policies (cd65b55f)
- test(cmru): cover high impact release residuals (dab68f1a)
- test(cmru): cover semantic build and worktree dispatch (bfa3e4ac)
- test(cmru): cover residual config transaction version paths (d466e0f0)
- test(cmru): cover final operational residuals (c0db4dd5)
- test(cmru): cover release dispatch boundaries (bc4778a6)
- test(cmru): witness cleanup credential refusal boundary (18f95e8b)
- test(cmru): make retention rollback oracle explicit (67003d13)
- test(cmru): cover top-level cleanup and transaction dispatch (eaa7f2f8)
- test(cmru): cover bundle changelog residuals (aacf4a85)
- test(cmru): cover high-risk residual contracts (6632377a)
- test(cmru): cover cleanup source operations (c9953ed8)
- test(cmru): cover final config version transaction contracts (d7e85079)
- test(cmru): cover operational residual outcomes (bc6fc42c)
- test(cmru): name mount mapping witness accurately (8cd33f54)
- test(cmru): witness untagged mutation refusal (d01a0745)
- test(cmru): cover residual operational refusals (35dcf4d8)
- test(cmru): cover residual config version contracts (53a4ec8f)
- test(cmru): cover cli operation safeguards (6371ce62)
- test(cmru): name retention witness accurately (528e3f85)
- test(cmru): cover transaction retention boundaries (deecfc98)
- test(cmru): cover config version orchestration (f5646af9)
- test(cmru): close cli controller refusal gaps (782ee401)
- test(cmru): strengthen config policy witnesses (0fa5dafe)
- test(cmru): cover controller and cleanup protocols (5228cd85)
- test(cmru): cover config transaction safety edges (cb92cbeb)
- test(cmru): cover version and runner contracts (2d3171f7)
- test(cmru): cover remaining runtime contracts (48339384)
- test(cmru): cover dispatch protocol modes (f6bf8105)
- test(cmru): cover agent controller boundaries (404dad37)
- test(cmru): cover remaining runtime boundaries (0b6d6632)
- test(cmru): cover smaller boundary modules (030ddc2a)
- test(cmru): witness ghcr cleanup refusals (8ff58a43)
- test(cmru): cover orchestration cleanup flows (c331f18e)
- test(cmru): tighten wheel metadata oracle (44ddbd2d)
- test(cmru): harden cli dispatch contracts (5460ba20)
- test(cmru): cover operational refusal paths (9341491d)
- test(cmru): authenticate transaction digests (8dd7b326)
- test(cmru): cover high residual runtime boundaries (acc6e778)
- test(cmru): execute abstract agent contracts (8051f6f7)
- test(cmru): assert sync refusal atomically (736623f5)
- test(cmru): cover remaining runtime boundaries (325d0fcb)
- test(cmru): cover remaining release transaction paths (90a3e797)
- test(cmru): cover remaining CLI helper branches (df07d219)
- test(cmru): deepen CLI and config contract coverage (c87887a8)
- test(cmru): deepen runtime boundary coverage (ecf05690)
- test(cmru): complete release transaction controller coverage (a1903e44)
- test(cmru): exhaust release transaction boundaries (961b4412)
- test(cmru): exhaust runtime build boundaries (3351fdad)
- test(cmru): cover exhaustive CLI dispatch branches (2d97e4dc)
- test(cmru): tighten malformed wheel oracle (3c5e6c83)
- test(cmru): use hermetic Python executable (92a29024)
- test(cmru): cover build and release boundary families (62785351)
- test(cmru): cover runtime release failure pairs (87870139)
- test(cmru): cover final CLI release dispatch paths (0155e4e9)
- test(cmru): cover final release transaction boundaries (de30963f)
- test(cmru): complete noncli boundary witnesses (a981cab8)
- test(cmru): cover final CLI config boundaries (1b0bbe96)
- test(cmru): isolate release asset fixture (ec54c816)
- test(cmru): remove ambient PATH assumption (bc37168a)
- test(cmru): deepen cleanup retention coverage (5f788053)
- test(cmru): cover cli release orchestration flows (a6192665)
- test(cmru): cover remaining release core boundaries (378e43f4)
- test(cmru): deepen runtime and handler boundaries (484b4cdb)
- test(cmru): deepen transaction lifecycle coverage (84cf0bd1)
- test(cmru): deepen config and controller CLI coverage (63e05f0d)
- test(cmru): cover execution boundary contracts (389069b4)
- test(cmru): cover core orchestration boundaries (dc0cd6d4)
- test(cmru): cover artifact and release boundaries (44b3085a)
- test(cmru): witness invalid consul base64 refusal (7c38798a)
- test(cmru): harden agent and controller behavioural coverage (fbb7d181)
- test(cmru): cover release boundary failures (747a11d2)
- test(cmru): cover CLI runner and gate boundaries (e09b6df6)

## [4.0.1] - 2026-08-16
<!-- cmru: generated -->
<!-- cmru: source-end=e126a1f73e239dd2dc33a51ca79cd5fb0c19cb01 -->

### Changed
- cmru: centralize estate release policy (2281181a)

## [4.0.0] - 2026-08-12
<!-- cmru: generated -->
<!-- cmru: source-end=9e98628c934f62fc08d1d89cfa9776b1f36a1fe0 -->

### Added
- feat(cmru)!: retain successful local build outputs (128a3da5)

### Documentation
- docs(cmru): record transaction self-binding defect (e320003f)

## [3.0.0] - 2026-08-12
<!-- cmru: generated -->
<!-- cmru: source-end=b5bd5497d2ba8a6322d3c595252de1a631432381 -->

### Added
- feat(cmru)!: consolidate strict release maintenance (b5bd5497)

## [2.0.1] - 2026-08-12
<!-- cmru: generated -->
<!-- cmru: source-end=8e6359e5c4519feee99a662d7c1e0efa804f7812 -->

### Fixed
- fix(cmru): make the estate shim select its explicit config (8e6359e5)

## [2.0.0] - 2026-08-12
<!-- cmru: generated -->
<!-- cmru: source-end=6abbc2e8933cd90007f8fc8cf41a5c23d02d432b -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
- feat(cmru)!: enforce strict release framework (8dd0e416)
- feat(cmru): generate release history by default (df049c27)
- feat(cmru): generate marked project release history (fe40d0a1)
