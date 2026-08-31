# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. A marked `backfilled-after-release` entry was generated after its immutable tag already existed.

## [Unreleased]
<!-- hand-written ahead of release; cmru's generator will produce the real dated entry for this range at release time -->

### Added
- feat(assay): ship `assay/helpers/go/stmtpos/`, a stdlib-only Go statement-position oracle adapted from `cmd/cover`'s own instrumenter, invoked with `go run` (B047 item 1, A-217)
- feat(assay): `go-cover` now keeps each record's whole block extent — columns included — as `FileCoverage.blocks`, instead of expanding it straight into line sets (A-239, A-390)
- feat(assay): `assay.statement_attribution.attribute_statements` — the pure, language-free join that turns block extents plus source-derived statement positions into statement-granular line sets, refusing loudly when the profile and the source are not the same revision (A-391)
- feat(assay): `LanguageAdapter.statement_blocks` — a new protocol hook (never an overload of `statement_spans`) through which an adapter supplies source-derived statement positions, plus the `requires_statement_attribution` declaration that gates it and the `HelperInvocation`/`StatementBlockReport` types it returns (A-397)
- feat(assay): the Go adapter declares `external_tools = ("go",)`. A Go lane in an environment with no Go toolchain now refuses `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` through the existing effective-PATH preflight, before anything runs, instead of judging a profile it cannot correct (B047 item 2, A-253)
- feat(assay): `assay.evaluate.resolve_coverage_keys` — the artifact-key-to-repository-path resolution, exposed rather than duplicated, so the file the statement-position oracle reads is the same file the evaluator judges (A-385/A-367)

### Fixed
- fix(assay): a Go R1 line claim is statement-granular. The shipped parser expanded a cover block's positional extent with `range(start, end + 1)`, attributing function signatures, closing braces, `case` labels and statement-continuation lines as executable code; it now reports only lines that begin a counted statement (F008-A3)
- fix(assay): an adapter whose coverage format is not statement-granular can no longer be handed an uncorrected profile by accident. `evaluate_coverage` and `evaluate_targets` refuse `ERROR`/`UNREADABLE_ARTIFACT` when the adapter declares `requires_statement_attribution` and the profile reports it never received any — a guard, because an uncorrected block profile is a wrong answer that looks exactly like a right one (A-392)

<!-- Post-release housekeeping, 2026-08-18: this block is CLEARED immediately
     after a release. cmru generates the dated entry below from the commit
     range, but it does NOT clear the hand-written block that fed it -- so
     leaving content here republishes shipped work as "unreleased". That is
     exactly how 2.0.0's entries survived into the 2.1.0 cycle, and it
     recurred on the 2.1.0 release itself. Until cmru clears this itself,
     clearing it is part of releasing. -->

<!-- cmru: release history -->

## [4.0.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=12234a9742066ed3b622af7946c74fe53e66899d -->

### Added
- feat(assay): B046 -- R2 by evidence ingestion, and javascript at R2 (d0aab6fd)
- feat(assay): B041(b) -- isolation.link_paths, with the teardown canary (9bb52280)
- feat(assay): B043 -- a lane-level cwd, honoured at every execution site (143e927e)
- feat(assay)!: verdict schema v8 -> v9 -- the producer cut (B045/B046/B043/B041(b)) (af14021f)
- feat(assay): B045 (2/2 non-schema) -- real branch arcs under a declared istanbul producer, and the type-only lexer (B038 a+b) (cc4e955f)
- feat(assay): B045 (1/2) -- the coverage PRODUCER as a declared, per-format, closed fact (fac1b73b)

### Fixed
- fix(assay): the raw verifier's three missing ORDER checks and its unclosed producer vocabulary; file B051 (4780c4ba)
- fix(assay): the report-schema major pin admits an unmeasured major; B037 docstring is stale (9848d5ca)
- fix(assay): BLOCKER -- cwd x link_paths composed into a snapshot escape (52b1f86b)

### Changed
- merge(assay): Wave B -- producer wave, verdict schema v8->v9 (B045/B046/B043/B041(b)) (5692ad37)
- backlog(assay): file B052 -- an ingested report's `source` is never checked against the snapshot's committed bytes (c1176bd0)

### Documentation
- docs(assay): Wave B controller log -- merged to main, starting release (12234a97)
- docs(assay): B037's ruling is made -- the second copy of the stale claim, in generate_mutation_sites' own docstring (7263716f)
- docs(assay): Wave B controller log -- fix-verification ACCEPT (d3c194be)
- docs(assay): Wave B controller log -- fix-verification round 2 interim (168b4445)
- docs(assay): Wave B controller log -- fix round 1 complete and green (bcc9335b)
- docs(assay): fix round 1 -- the rewritten gate record, and REPORT section 15 (05947625)
- docs(assay): Wave B controller log -- fix round 1 status, B051 bookkeeping gap (b62e9ec1)
- docs(assay): Wave B controller log -- review round 1 complete verdict (58bb314b)
- docs(assay): Wave B controller log -- review round 1, ACCEPT-conditional (85e90140)
- docs(assay): Wave B controller log -- implementation complete, gate green (a1d1dec3)
- docs(assay): the Wave B gate transcript (a4bf1bc3)
- docs(assay): the Wave B report (1a783f3e)
- docs(assay): Wave B controller log -- checkpoint 3, schema cut landed (624673db)
- docs(assay): Wave B checkpoint 3 -- continuation brief for the post-schema-cut half (f620c97b)
- docs(assay): Wave B controller log -- checkpoint 2, endorse required-fields fork (25d02d94)
- docs(assay): Wave B checkpoint 2 -- continuation brief + LOG entry for cc4e955f (b1a2f0e9)
- docs(assay): Wave B controller log -- checkpoint 1, dispatching generation 2 (a6e6ebe6)
- docs(assay): Wave B checkpoint 1 -- continuation brief (b85d3a6e)
- docs(assay): Wave B controller log -- report-vs-pause operating rule (c36a06a5)
- docs(assay): Wave A shipped status + operator ruling on B/C sequencing (76de935f)

### Testing
- test(assay): W5 -- the v9 frozen drift-guard generation, and the gate wiring that demotes W4 (1577fa45)
- test(assay): commit a REAL StrykerJS mutation-testing-report-schema artifact (B046 evidence) (384f3c0f)

## [3.2.0] - 2026-08-30
<!-- cmru: generated -->
<!-- cmru: source-end=71ddc7d952720ea2cf1d0e2dcb56ddf2489f01e5 -->

### Added
- feat(assay): B044 -- `assay lanes --json`, a machine-readable lane inventory (04ad5688)

### Fixed
- fix(assay): list JavaScript R1 in `assay run`'s own help/docstring (64e9382a)
- fix(assay): B039/B047-4 -- one shared classified-line ceiling for every expanding coverage parser (1eeab9db)

### Changed
- merge(assay): Wave A -- JS consumer wave (B044/B042/B041(a,c)/B039/B047-4/B048) (71ddc7d9)
- backlog(assay): Wave A -- file B049, record A-347..A-350, tick acceptance boxes (917c1e92)
- backlog(run-gate,ciu,assay): RG-25/RG-26 -- backport ciu CIU-72 (b)/(c) to the current gate; CIU-73 needs no code (b2884e76)
- backlog(assay,ciu): 3.1.0 design review -- file B041-B048, resolve B037, rule the v9 producer wave; CIU-72/73 (8b196f0b)

### Documentation
- docs(assay): commit the confirming registered-gate transcript (fix commits) (70bd6775)
- docs(assay): Wave A REPORT/LOG -- record review round 1 verdict and fixes (cfe512a8)
- docs(assay): Wave A review round 1 -- fix 3 blockers + 6 should-fixes (efb825bc)
- docs(assay): the gate transcript, and the REPORT's final gate section (225dd6ad)
- docs(assay): commit the qualification transcript (renamed past *.log gitignore) (e9424676)
- docs(assay): Wave A REPORT, and LOG entries for the housekeeping commits (8353ff25)
- docs(assay): fix a dangling "see the qualification harness below" reference (4a70e09e)
- docs(assay): Wave A commit LOG (4a4056b6)
- docs(assay): B041(a)/B042/B044/B048/B049 -- README and CONSUMERS updates (5bd20c71)
- docs(assay): PROVENANCE entries for the c8 and vite-plugin-istanbul artifacts (28b39344)

### Testing
- test(assay): B044 golden coverage for env_required/environment_command/infrastructure_facts (15e24ffa)
- test(assay): B041(c) -- real Vitest qualification through the real assay CLI (0ea21a05)
- test(assay): B048 -- a real `vite-plugin-istanbul` artifact proves original src/*.ts(x) keys (0fbe1261)
- test(assay): B042 item 2 -- measure `c8`'s v8-to-istanbul remapper against the same defect probe (5e347d04)

## [3.1.0] - 2026-08-30
<!-- cmru: generated -->
<!-- cmru: source-end=c0b0e18225935138628099d44f23e93d5f5e7f49 -->

### Added
- feat(assay): a JavaScript/TypeScript adapter, registered at R1 (B036) (26c92be9)
- feat(assay): coverage-istanbul-json, a fifth coverage format (B036) (d019b624)

### Changed
- merge(assay): bring feature/assay-b036-js-adapter up to date with main (B018/B019/B035 v8 + B037 ruling) before landing (408c3d57)
- backlog(assay): rule B037's native-vs-evidence-ingestion fork -- Stryker Mutator (b0bef83a)

### Documentation
- docs(assay): B036/A-346 -- fix "written to FAIL if fixed" overreach (round-2 nit) (e580de02)
- docs(assay): B036 report -- round-2 registered gate transcript, exit 0 (B036) (3a677f95)
- docs(assay): B036 report -- round-2 response, and commit the review it answers (B036) (d8b54de8)
- docs(assay): rule @vitest/coverage-v8 unsafe for judged lanes (A-346, B040) (0e2f111b)
- docs(assay): correct three overstated claims in the JS adapter's own source (B036) (14b5c47e)
- docs(assay): correct the phase-marker count in the B036 report (371a4f7b)
- docs(assay): record the registered gate transcript for B036 (95a83968)
- docs(assay): B036 implementation report (8aa62c62)
- docs(assay): document the javascript language and istanbul format (B036) (53dca7d5)

### Testing
- test(assay): pin the v8 provider defect and replace the vacuous span pin (B036) (c115a107)
- test(assay): real fixtures for the v8 provider defect and the canary (B036) (6aa8e08b)
- test(assay): real vitest coverage fixtures for both providers (B036) (e2395b66)

## [3.0.0] - 2026-08-30
<!-- cmru: generated -->
<!-- cmru: source-end=b6aca39de509ab188148c149d352a74401d09fa0 -->

### Added
- feat(assay)!: drop the withdrawn operator spellings at the v8 cut (A-331) (74c89475)
- feat(assay): judge provenance, request-supplied base, r2 judging scope (B018/B019/B035) (86ceb527)

### Fixed
- fix(assay): round-2 review remediation -- retract two false root causes, fix the diagnostic properly (A-334) (652962af)
- fix(assay): round-1 review remediation -- M1/M2/M3 + m1..m8, N1/N3/N6 (A-332/A-333) (0fad7842)
- fix(assay): name the dirtying paths when the self-hosted lane goes DIRTY_TREE (28d6e41d)

### Changed
- merge(assay): B018/B019/B035 -- judge provenance, request-supplied base, r2 judging scope (v7->v8) (b6aca39d)
- backlog(assay): B036/B037 -- JS/TS language adapter for dstdns's React UI (62fe368f)

### Documentation
- docs(assay): round-3 review polish, release housekeeping, migration notes (B018/B019/B035) (feff8948)
- docs(assay): make the report's gate invariant self-checking, not self-dating (ea6c2daa)
- docs(assay): report section 9 -- round-2 outcome and the retractions (ba62cd54)
- docs(assay): correct the changed-file count (91 -> 92) (c27273af)
- docs(assay): point the report's gate invariant at the remediation commit (e8990bf5)
- docs(assay): report section 8 -- round-1 review outcome and the re-run gate (15dea56d)
- docs(assay): state the gate coverage as an invariant, not a hash that goes stale (6b40e3ad)
- docs(assay): B017 recurrence 3 resolved upstream; record the second green gate (9c39e271)
- docs(assay): state which commit the gate judged, and prove the gap is prose (0a315100)
- docs(assay): final gate transcript at 745ac377 and complete the commit table (7515c57d)
- docs(assay): wave report for B018/B019/B035 with the real gate transcript (ef3a6930)
- docs(assay): B017 -- third recurrence, first inside vbpub's own worktree (97a82a1f)
- docs(assay): record A-327..A-331 and document the v8 contract (B018/B019/B035) (b72a3c5b)
- docs(assay): B017 -- second recurrence of the untracked CIU render-input class (45755014)

### Testing
- test(assay): prove the zipapp provenance branch against a real .pyz (B018) (745ac377)
- test(assay): cover B018 provenance, B019 base delegation, and the v8 vocabularies (f7450b0a)

## [2.4.2] - 2026-08-26
<!-- cmru: generated -->
<!-- cmru: source-end=fd6cbb5fafcec4255f2d9b74d098fc559ab9957d -->

### Fixed
- fix(assay): B033/B034 round-2 review fixes (blocker + findings 2, 3, 5) (a667862c)
- fix(assay): B033 whole-target scope + B034 operator withdrawal (A-325, A-326) (6e0dca84)

### Documentation
- docs(assay): record the second round-2 gate run, at the actual branch tip (5fe87fa2)
- docs(assay): round-2 notes in the LOG and REPORT, with the re-run gate (a72f0d72)
- docs(assay): state B033(a)'s verifier-weakening cost outright (round-2 finding 4) (6eb0f925)
- docs(assay): complete the B033/B034 LOG (entries for the three doc commits) (f2283598)
- docs(assay): fill in the B033/B034 registered-gate transcript (green at 40127f76) (b3648130)
- docs(assay): say in the SQL consumer section how a whole-target SQL lane differs (40127f76)
- docs(assay): B033/B034 LOG, and correct README's "which question R1 asks" (949cac2f)
- docs(assay): record A-325/A-326, correct B015, file B035, update consumer docs (1fedb8fa)

## [2.4.1] - 2026-08-26
<!-- cmru: generated -->
<!-- cmru: source-end=d0d2f25bb1978b729cceba76e59d7052021e691d -->

### Fixed
- fix(assay): round-2 review fixes -- honest probe-timeout bound, honest --progress refusal (B031/B032) (3f47d5fa)
- fix(assay): guard A-320's candidate_ids producer against an empty shard (d58265bc)
- fix(assay): B031/B032 -- opt-in progress artifact, registered verify fields, honest probe refusals (A-320..A-323) (ae09425d)
- fix(assay): B030 -- assay plan discovers against the real project root (A-319) (6a0f9a04)

### Documentation
- docs(assay): fill in the B030-B032 round-2 gate transcript (registered gate green) (775d44cb)
- docs(assay): B030-B032 round-2 LOG and REPORT (235a6f2e)
- docs(assay): round-2 review bookkeeping -- tick B030/B031/B032 acceptance boxes, restate A-320's no-bump argument, CHANGES.md (D1) (7cf34c2f)
- docs(assay): fill in the B030-B032 gate transcript (registered gate green) (fcdfde92)
- docs(assay): B030-B032 remediation LOG and REPORT (0e6cab39)
- docs(assay): file the 2.1.0->2.3.0 review-gap audit and its backlog (B030-B034, RG-23) (142143a4)

## [2.4.0] - 2026-08-25
<!-- cmru: generated -->
<!-- cmru: source-end=f0f063e642abb88dc7d349b93d373029f42f25c1 -->

### Added
- feat(assay): inject declared infrastructure facts before lane execution (11b20645)
- feat(assay): add mutation resume, deterministic sharding, merge proof (7a4f6333)

### Fixed
- fix(assay): assay.verify never learned base_resolution/env_effective_incomplete (f0f063e6)
- fix(assay): re-witness the W2 locked v7 schema drift-guard (c31ffd12)
- fix(assay): stabilization wave GO fold-ins — positive probe test + docstring fix (21205b78)
- fix(assay): stabilization wave round 2 — close review findings on e2169d46 (b97f3aaf)
- fix(assay): stabilization wave — B008, B021, B022, B024, B025, B026, B027 (e2169d46)
- fix(assay): close N-6 — cli.py's refuse_lane sites also needed infrastructure forwarding (869235a8)
- fix(assay): round-3 remediation — close N-1..N-4 and a merge-proof gap from round-2 review (45ea7d0b)
- fix(assay): remediate B012/B013/B016/B017 defects found by independent adversarial review (7941fdcb)
- fix(assay): prove every snapshot manifest leaf before yield (00da6510)
- fix(assay): honor standard excludes without masking tracked dirt (18debcae)

### Changed
- backlog(assay): B008 second reproduction — also collapses R2 mutation lanes (9328f69f)
- backlog(assay): B017 new reproduction — dirty_paths() false-positive on every ciu worktree (2669ef9d)
- backlog(assay): B017 — dirty_paths uses narrow exclude flag, forcing brittle consumer patterns (6d1eea84)

### Documentation
- docs(backlog): B027 — mutant-timeout crash in execute_plan's _bounded_tail (0ab258f5)
- docs(assay): resolve the new B017 worktree-dirty reproduction — no assay bug (6c28153c)
- docs(assay): fix A-298 round-label slip, note the untested LANE_TIMEOUT forward (6e8a51fb)
- docs(assay): file CIU V8 preparation backlog B018-B020 (4644a464)
- docs(assay): file B016 — snapshot omits source files when __pycache__ exists (acc82d86)

## [2.3.0] - 2026-08-24
<!-- cmru: generated -->
<!-- cmru: source-end=ab87caade1dfb8ebfbe1002db493b41d9a51f555 -->

### Added
- feat(assay): W2 verdict schema v7 successors (f3ce3d0a)
- feat(assay): B015 semantic Python mutation operators (126ef577)
- feat(assay): B014 bounded command output tails (37462618)

### Fixed
- fix(assay): align P25 oracle tests with v7 sentinels (ab87caad)
- fix(assay): normalize P25 runtime identities to literal sentinels (7a926ebe)
- fix(assay): use v7 P25 template in normalization negatives (d6708836)
- fix(assay): pin P25 qualification to the v7 contract (f8178d9b)
- fix(assay): omit runtime tails in P25 v7 templates (9c7bfa88)
- fix(assay): require judgment delta in source-root decoy oracle (9e94a0f8)
- fix(assay): treat judgment-only decoy delta as no root discrimination (50d34711)
- fix(assay): ignore runtime tails in source-root decoy discrimination (291f81d3)
- fix(assay): normalize B014 diagnostic tails in P25 comparisons (615a924e)
- fix(assay): point P25 Topos qualification at v7 templates (3d53847d)
- fix(assay): distinguish captured timeout tails from no-process timeouts (ebdd8f6c)
- fix(assay): keep pre-command budget fixture tail-free (46f1368d)
- fix(assay): align runner fixtures and SQL witness with v7 (b6d9615c)
- fix(assay): W2 gate and v7 test migrations (6b777274)
- fix(assay): import sys for W1 hard-cut gate probe (56d6c2c5)

### Changed
- chore(assay): name differing fields in template qualification (ae54e8cf)
- chore(assay): drop dead runtime-field guard in decoy oracle (893af414)
- Merge branch 'feature/assay-B015-semantic-python-operators': B015 semantic Python mutation operators (6324548d)
- chore(assay): gate v6 locked successors for the v7 hard cut (ee6d9cb1)
- Merge branch 'main' into run-gate-rg-sweep (72cc1f47)
- run-gate RG-2: validate-pointers verb + estate pointer↔lane linkage tests (7e5612c1)

### Documentation
- docs(assay): B013 update — schema-wrapper lanes with sibling runners hit same isolation wall (7c56fa8c)
- docs(assay): clarify B015 is independent of candidate budgets (5cc36a26)
- docs(assay): file B015 UUID/enum operator gap (4819cf8b)
- docs(assay): reconcile shipped M4/M5 product statuses (d2769483)
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)

### Testing
- test(assay): make correct-root decoy control fail loudly (ef3eabfe)
- test(assay): relax decoy oracle message after tail normalization (823a1741)
- test(assay): update wrong-root decoy oracle for B014 normalization (13247f7b)

## [2.2.0] - 2026-08-24
<!-- cmru: generated -->
<!-- cmru: source-end=f64307a9bd02e6ae2d9918bb54fa4fad35c7b0a5 -->

### Added
- feat(assay): B010/B012 preflight and mutation observability with review fixes (8a2a4731)
- feat(gates): whole-target SQL mutation targets and declared env forwarding (ba8908d6)

### Changed
- backlog: file run-gate adversarial-review findings — RG-1..14 (new backlog), cmru KI-19 (mutation skip emits no evidence), assay B011 (stale cross-tool wiring example) (75593bcc)
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- backlog(ciu,assay): CIU-41..43 + assay B010 — four upstream findings from dstdns P111's Mode-B live pass (7f64090c)
- run-gate-project: README (design authority) + CONSUMERS (adoption guide) + HANDOFF-P01 (build + ciu first adoption) — estate D-110/D-111+amendment (647364ab)
- backlog: CIU-40 + assay B009 refined per D-111 (run-gate.py + gates.toml, one parser, orchestration/judgment split) (910d8b8e)
- backlog: assay B009 (assay.toml role docs + image-baked distribution) + ciu CIU-40 (run-gate.sh + de-vendor) per estate D-110 (e9bd9b27)
- backlog(assay): B008 — R1 base resolves to first-parent on merge-commit HEADs, silently narrowing the changed-line floor (measured, ciu gate) (f0d6f858)
- ciu+assay: sync main to the worktree branch's resolved config-wave docs (backlog with CIU-39 renumber, brief rev 2, re-frozen handoffs); assay provenance refs CIU-28 -> CIU-39 (d3f80b9c)
- assay: wave 3 complete -- W3-RESUME is the standing successor brief (e0462ebe)
- backlog(assay): correct B001 and B004's frontmatter rows (3bf9e571)

### Documentation
- docs(assay): document and disposition B010/B011/B012 (f64307a9)
- docs(backlog): file B014 bounded subprocess output capture on failure (93f0eae1)
- docs(backlog): file B013 SQL infrastructure injection requirement (c057199c)
- docs(backlog): file B012 mutation execution requirements (ab4a75d7)
- docs(estate+assay): two general hazards, and why the rigor levels differ (4ec6437a)
- docs(assay): consumer practices, and reconcile B002/B003 as COMPLETE (273ba944)

### Testing
- test(assay): self-hosting cgroup-wiring meta-test reads the SSOT run-gate.toml lane, not the trove pointer argv (91959b3a)
- test(assay): gate-pointer meta-test asserts the run-gate SSOT chain — trove pointer → host lane → self-hosting driver; driver safety assertions unchanged (e1c8cfd2)

## [2.1.0] - 2026-08-18
<!-- cmru: generated -->
<!-- cmru: source-end=52534ef7e78d5c113c7873db5e8dc8f2940542d6 -->

### Added
- feat(assay): P34 W9 -- real-PostgreSQL qualification at a pinned dstdns revision (746a24b5)
- feat(assay): P34 W5+W6 -- classification, artifact plumbing, CLI wiring (2c1a57cc)
- feat(assay): P34 W3+W4 -- the external-tool preflight and the config surface (67e396bf)
- feat(assay): P34 W1+W2 -- the DDL lexer and the SQL adapter (fbb5e15b)

### Fixed
- fix(assay): wave 1's release embargo could not survive its own success (A-278) (9bd0cf72)

### Changed
- merge(assay): wave 3 -- P34/B001 the source-oriented SQL/DDL adapter (W0-W8) (ccf9ca55)
- evidence(assay): freeze the A-279 ordering pair; rule A-287, A-288 (545d5213)
- decide(assay): A-279..A-283 -- the P34 carve corrections, ruled (9a5b68d5)
- review(assay): W3 -- adversarial review of the P34 carve (5 blocking) (d7a78a60)
- carve(assay): W3 -- P34/B001 source-oriented SQL/DDL adapter (cdd16adc)
- assay: wave 2 complete -- nothing to release, and why that is the right call (f6c7196b)
- disposition(assay): B004 deferred (A-275/A-276); A-270 finds its first defect (A-277) (a86d70b5)
- review(assay): B004 carve -- READY WITH CORRECTIONS, defer implementation (1237a39f)
- carve(assay): B004 provenance-verified -- and it is blocked twice (5a14d70c)
- backlog(assay): B007 has no v7 partner, so wave 2 and 3 go first (ca63c8cc)
- assay: wave 1 complete -- assay-v2.0.0 released, .pyz verified, dstdns notified (611279c2)

### Documentation
- docs(assay): P34 W7+W8 -- the sixth derived vocabulary and the SQL documentation (7d4ad61d)

## [2.0.0] - 2026-08-17
<!-- cmru: generated -->
<!-- cmru: source-end=5460d9371cb11b4b80dbe8b0ea920c72b29cda83 -->

### Added
- feat(assay): B006(b) -- create the coverage artifact's missing parent inside the snapshot only (7e869e71)
- feat(assay): B006(a) WI-3 -- coverage-artifact/omission collision and the embargo (7d2da7f3)
- feat(assay): B006(a) WI-2 -- P22 exact unsafe-symlink omissions (57d620d7)
- feat(assay): lane schema v2 -- IsolationConfig and the R0/R1+ isolation conditional (c56a13ea)

### Fixed
- fix(assay): finish the P25 harness's v6 migration and split its v1/v2 lanes (3da074ec)
- fix(assay): withdraw coverage.py branch-summary cross-check (A-272) (4894bae6)
- fix(assay): R1 never worked for a nested project -- reconcile the key spaces (d547c75a)
- fix(assay): W1-WI5 report -- correct §7 with the real O7 command output (bb5153c6)

### Changed
- merge(assay): wave 1 -- B005 whole-target judge, B006 monorepo snapshots, v6 (e7e2c616)
- assay: record the gate green on the branch, and CMRU's real R1/R2/R3 claims (000ae29a)
- assay: re-witness P25's two v6 templates from a real run (A-274) (d355a434)
- assay: rule A-274 -- the migration needed a fifth bucket, RE-WITNESS (b0aa10fb)
- assay: rule A-273 -- correct A-263's percentage claim, do not chase the number (dd03dea6)
- assay: rule A-272 -- the branch-summary cross-check refuses real coverage.py (662f288b)
- assay: record the three release blockers the acceptance test found (2cffc884)
- assay(B006a): WI-5 CMRU qualification harness -- lands the proof, finds a real R1 defect (cd83ae8d)
- backlog(assay): add B007's frontmatter row to match its body (ee88ca23)
- assay: wave-1 item 5 -- README/DESIGN-GUIDE/CONSUMERS for B005+B006, and the three A-270 checks (f0e391cd)
- assay: discharge the B005 end-to-end proof through the real CLI (3328a01f)
- assay: bring the wave state current, and sequence B007 after the release (29ce78b0)
- backlog(assay): B007 -- multi-target R3 canary, assessed and deferred to v7 (b869db79)
- assay: rule A-271 -- the two path grammars differ on purpose (9cb0e310)
- assay: verify the v6 cut, and close section 5's self-contradiction (3149d822)
- fixup(assay wave-1): classify the v6 successor suite in the migration script (507ca1c7)
- assay: wave-1 branch coverage, whole-target judge, verdict schema v6 (71d98965)
- assay: record wave state -- B006 is built, the v6 cut is dispatched (dae5b7c7)
- assay+estate: rule A-270 -- user-facing docs merge with the change (8269fe5d)
- assay: widen the documentation work item -- the plan missed both user docs (66382ab5)
- assay: controller's independent verification of WI-3 (678f93fc)
- assay: sweep the wave contract for surviving withdrawn-design instructions (1b563dea)
- assay: correct the one B006(b) sentence that still named the withdrawn scope (80de2a6c)
- assay: verify WI-2 against the real substrate, and remove one dead branch (88f24a85)
- assay: controller's independent verification of WI-1 (f76a3f32)
- assay: measure the B006(a) carve's open M20 — CMRU in tester-unified (75d50a7e)
- assay: rule A-269 and kill the superseded B006(a) design in place (c74dce86)
- assay: B006(a) is unblocked — record the recarve as the live state (5d6d525c)
- assay: fold review round 1 into the B006(a) carve, body first (3d745924)
- assay: independent review of the B006(a) recarve — READY WITH CORRECTIONS (c313589b)
- assay: recarve B006(a) as unsafe-symlink omission, not a project boundary (d3173e61)
- assay: stop B006(a) at the review budget, correct two stale decision rows (c3b00729)
- assay: fold round 2-of-3's nine blocking findings into section 1 (2f9495b5)
- assay: dispatch review round 2-of-3 on the revised section 1 (18b08fc5)
- assay: make notifying dstdns part of the release step, not an afterthought (465393d3)
- assay: take round 3's eight blocking findings as decisions, and widen the wave (d57cb2f1)
- assay: WI-3 verified, and B006(a) stopped at the 3-round review cap (7835ed8c)
- coverage(parsers): wire branch arcs into all four formats (wave-1 S3.1a/S3.3) (bd99bb7a)
- coverage(model): add BranchCoverage and FileCoverage.branches (wave-1 S3.1/S3.2) (759bea03)
- assay: add the wave-1 resume point (172f1550)
- assay: stop claiming a sandbox the substrate cannot deliver (A-267) (0a96dc7e)
- assay: adapt wave 1 to main's rewritten B006 -- project-scoped snapshots (A-266) (571cf2b5)
- merge(assay): take main's rewritten B006 -- project-scoped snapshots supersede the allowlist (0791d9c4)
- decisions(wave1): record A-257..A-265 (branch coverage, whole-target judge, verdict v6) (6bd75c0c)
- assay: rewrite the wave-1 carve against an independent review's 11 findings (59af6b4b)
- assay: carve spec addendum -- the carver's own corrections (77c40ee7)
- assay: stop the coverage fixtures from joining the project's own suite (af918715)
- assay: pin the two branch-arc spellings that reject a reasonable parser (39fa7af2)
- assay: carve wave 1 -- branch coverage, whole-target judge, verdict v6 (4286e501)
- backlog: B005 whole-module/per-callable coverage judge; B006 snapshot substrate papercuts (b5d0c894)

### Documentation
- docs(assay): complete WI-1 audit log with real post-commit numbers (9b02e5e8)
- docs(assay): classify scoped snapshot as capability (010d1813)
- docs(assay): specify safe monorepo snapshot scope (c7bc9b59)

### Testing
- test(assay): witness O5, the dstdns nginx-symlink incident shape (364415ad)

## [1.0.0] - 2026-08-16
<!-- cmru: generated -->
<!-- cmru: source-end=389c288d19fb32db023a016eb084422140fcf488 -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)

### Changed
- cmru: centralize estate release policy (2281181a)

## [0.1.0] - 2026-08-12
<!-- cmru: generated -->
<!-- cmru: source-end=ab2c130b7e536402d68211f560a423862bc217ae -->
<!-- cmru: backfilled-after-release tag=assay-v0.1.0 -->

### Added
- feat(assay): P25 real Python-project qualification over a disposable Topos tree (2607cb7d)
- feat(assay): P24 versioned wheel contract over the locked five-wheel closure (c16a7436)
- feat(assay): P23 exact reexecution integration over P22 snapshots (8268467f)
- feat(assay): P22 committed-object snapshot substrate (487deaf1)
- feat(assay): P21 verdict v4 evidence contract (5bf87de2)
- feat(assay): P19 -- isolated R3 CLI pipeline (710b2f3e)
- feat(assay): P18 -- Python R2 CLI pipeline (7fc7e7a3)
- feat(assay): P17 -- Python R1 CLI pipeline, real assay run R0+R1 (e5b81d4c)
- feat(assay): P16 -- schema v3, judgment binding, assay verify rederives R1/R2/R3 (bba57771)
- feat(assay): P15 -- measurement input integrity (266f3764)
- feat(assay): P14 self-hosted conformance -- assay verify + self-hosting (A-128..A-133) (461ed28f)
- feat(assay): P13 -- standalone wheel proof (b9073b62)
- feat(assay): P12 -- baseline-gated, isolated, jobs-bounded mutation execution (8848ac09)
- feat(assay): P11 -- valid mutant construction (102d8559)
- feat(assay): P10 -- attested evidence staleness, never verified (2b13ecef)
- feat(assay): P09 -- cause-sensitive canary proves the gate rejects for cause (08048d56)
- feat(assay): P08 -- Go adapter boundary proof (5c08706e)
- feat(assay): P07 -- statement-span attribution (A-100/A-101) (7aa474f7)
- feat(assay): P06 -- Python LanguageAdapter, the union of dstdns/topos/nyxloom (5a04508a)
- feat(assay): P05 -- language-free evaluation core (four-way union, adapter protocol, registry, R1 runner integration) (ff83f9ce)
- feat(assay): P04 -- runner, CLI run subcommand, and R0 verdict emission (352cab50)
- feat(assay): P03 -- coverage formats registry (coverage.py JSON, lcov, Cobertura, Go coverprofile) (972e52c4)
- feat(assay): P02 -- changed-line extraction and measurability guards (128ae1b7)
- feat(assay): P01b -- the verdict model and a schema that REJECTS (d0ff79ce)
- feat(assay): P01a — skeleton and the assay.toml loader that refuses to invent (c85d610d)

### Fixed
- fix(assay): attach test closure before P26 gate phase (18b93242)
- fix(assay): P22 scope correction — revert two forbidden test paths (93c0a30e)
- fix(assay): close P20 exclude and stderr bounds (dfcfe0ee)
- fix(assay): P19 controller review -- A-149..A-151 repaired (6e65c59a)
- fix(assay): P18 controller review -- 4 defects repaired (A-145..A-148) (9750b54e)
- fix(assay): P18 self-review -- operator-filter oracle, misleading param name (7d2df553)
- fix(assay): P17 controller review -- 3 defects repaired (A-139..A-141) (d9839e81)
- fix(assay): P17 self-review -- stale docstrings, a non-discriminating test, one more terminal shape (3acab968)
- fix(assay): P16 controller repairs -- five defects in code that was never written (50110247)
- fix(assay): P15 controller repairs -- three input boundaries decided by  default, not by assay (A-134) (9ae961b2)
- fix(assay): P07 -- wire unclassified_lines through runner.evaluate_r1 (9b9d38e8)
- fix(assay): P04 -- relocate the R0-only rigor gate out of cli.py (fd7ae88e)
- fix(assay): rename P01a/P01b -> P00/P01 -- letter-suffixed ids fail nyxloom's schema (c1bb518d)
- fix(assay): apply A-071 to the four handoffs it governs -- it was ruled, never landed (3b419090)
- fix(assay): the schema's timestamp anchor meant two different things (59fc9132)
- fix(assay): refuse judge config for an undeclared rigor level (A-062) (0e1dc7dd)

### Changed
- assay: ship env_required for dstdns's real-lane isolation (A-254/A-255/A-256, B004) (e414f475)
- assay: P34's scope updated -- both decisional gates cleared (72a870a3)
- assay: close the hollow-PASS/FAIL schema gap (A-251/A-252), and rule the external-tool preflight to P34 (A-253) (6750e7c1)
- assay: P34 pre-carve SCOPE (not a carve, not dispatchable) (18a8547f)
- assay: land ship -- cmru adoption + reproducible zipapp (A-249/A-250) (0239513a)
- assay: resequence -- ship (cmru + zipapp) moves ahead of P34 (A-248) (1b369e23)
- assay: persist Fable round-4 review (ciu-synergy check, A-237/A-240/A-241 doctrine recommendation) — last Fable round (1082f4eb)
- assay: draft the nyxloom spine documents (1-north-star, 2-product-definition, 3-roadmap) (95196be0)
- assay: scope cmru adoption + a parallel zipapp, and stop short of landing (A-247/B002/B003) (d5d9865a)
- assay: decision-record hygiene sweep (A-246) (c1b26f6b)
- assay: record A-245 and mark A-241 FIXED (00049d5f)
- assay: close A-241's real half in verify.py, and correct its example (a7c16d0c)
- assay: persist Fable round-3 review (fidelity check, A-240/A-241 re-verify, Open-section audit, full validity audit, two follow-ups) (59a94473)
- assay: land the accepted Fable rulings (A-239 - A-244) (7fcf1020)
- assay: append Fable's open-decisions discussion to the v3 review report (ad83e084)
- assay: act on the Fable full-codebase review (A-234 - A-238) (35a6e4f3)
- assay: persist the post-P33 Fable full-codebase review report (3c8dc988)
- assay: add top-level README (737ef7da)
- assay: P33 -- reinstall the repaired locked v5 schema (9afea879)
- assay: merge main to pick up P33's carve-asset repair (62305df3) (a9166feb)
- assay: post-implementation carve-asset repair for P33 (5 fixes) (62305df3)
- assay: P33 review -- isolate invariant 1's four clauses (work item 3) (6ce66717)
- assay: P33 -- verdict schema v5 (language-qualified mutation, judgment.resolved) (f13e78a2)
- assay: fix the three gate-wiring oracle bugs round 6 found (e82da152)
- assay: commit P33's round-6 mandatory adversarial carve review (NOT READY) (c2d8659b)
- assay: record round 5 and A-232 at the resume point (6a7f9764)
- assay: record P33 round 5 -- anchor, hashes, and the pasted-output record (081d945b)
- assay: re-carve P33 round 5 -- run every claim, classify every red (cb5ceeab)
- assay: commit P33's round-5 mandatory adversarial carve review (NOT READY) (27e3a998)
- assay: reconcile P33 counts, anchor and round-4 record (7bf86042)
- assay: re-carve P33 round 4 -- two independent reviews, verification closed (51668c0d)
- assay: commit P33's round-4 mandatory adversarial carve review (NOT READY) (f9363543)
- assay: record P33 round 3 -- sweep v2, CA8 taken, anchor refreshed (8617051d)
- assay: re-carve P33 round 3 -- close the sweep own gaps and pin it (c22c6073)
- assay: commit P33's round-3 mandatory adversarial carve review (NOT READY) (8877910b)
- assay: record P33 round 2 and the inventory at the resume point (61de2912)
- assay: re-carve P33 after round-2 NOT READY -- close the class by inventory (fba0b88d)
- assay: commit P33's round-2 mandatory adversarial carve review (NOT READY) (50558d4c)
- assay: disambiguate the P27/P33 boundary in the resume marker (147592e0)
- assay: discharge P33 re-carve residuals and re-anchor the handoff (2e42d7f4)
- assay: re-carve P33 after NOT READY -- answer all 17 review defects (7a774d57)
- assay: commit P33's mandatory pre-dispatch adversarial carve review (NOT READY) (b22ebd56)
- assay: point the resume marker at the carved P33 (495b71f3)
- assay: carve P33 -- verdict schema v5, ready for carve review (b6f0b3bf)
- assay: design schema v5 and resequence the wave SQL-first (b03555d7)
- assay: point the resume marker at the A-O19 ruling and the B001 resequence (5a7af3f6)
- assay: rule A-O19 as option 2 and repair the P27 carve after review (a22842c2)
- assay: record P27's blocked carve at the documented resume point (bf7be597)
- assay: P27 JIT carve -- BLOCKED on the Go block-to-line grammar (A-O19) (239f6671)
- assay: make the wave controller run block OID-self-consistent (016863a4)
- assay: re-platform the wave controller for a Claude-only carve loop (ac9919ba)
- assay: schedule SQL adapter design checkpoint (9b167ba2)
- Merge current main into P26 gate-repair branch (d3f91a5c)
- review(assay): close P26's mutation deadline gap, surrogate escape, and dot-component spelling (50726383)
- assay: implement P26 attested evidence CLI hardening (06e44b4e)
- backlog(assay): B001 — SQL/DDL adapter for R2/R3 on PostgreSQL schema (f05c9942)
- assay: JIT-freeze P26 attestation hardening (d610dbb4)
- review(assay): witness the P25 single-witness guard that A-208 relies on (1a43c5a1)
- Merge current main into P25 review branch (145c7059)
- handoff(assay): ratify P25 budget scope and numeric witness (8164fca1)
- review(assay): pin the copied Topos witness to its hand manifest and unbreak the lane budget (c5633491)
- handoff(assay): JIT-freeze P25 Topos qualification (f311dc3d)
- review(assay): refuse an undecodable METADATA member instead of crashing (d7869110)
- carve(assay): freeze P24 distribution contract (c7ff15a1)
- carve(assay): correct P23 locked adapter fixture (7c52ecc2)
- review(assay): P23 phase-2 reconciliation, target-selection repair, audit closure (ff2617fe)
- review(assay): P23 blind-phase-1 combined-axis attacks and two bounded repairs (d85125dc)
- carve(assay): freeze P23 exact reexecution (0d46e954)
- review(assay): P22 phase-2 reconciliation, cleanup-contract repair, dead-code removal (cf49ec85)
- review(assay): P22 blind-phase-1 combined-axis attacks and four bounded repairs (b96cba90)
- carve(assay): freeze P22 snapshot substrate (cffec359)
- review(assay): repair P21 verifier terminal and vocabulary audit (bbf5cc46)
- merge(assay): apply P21 A-183 correction (76ecc814)
- carve(assay): resolve P21 unsupported mutation seam (ddad505b)
- blocked(assay): P21 mutation seam needs forbidden go.py (71b1b961)
- carve(assay): freeze P21 verdict v4 contract (20beeda1)
- Merge branch 'main' into feat/assay-P20-repository-artifact-boundary-integrity (ff7b09e7)
- Merge branch 'main' into feat/assay-P20-repository-artifact-boundary-integrity (a0a034d4)
- carve(assay): close P20 routed boundary gaps (7beb56ab)
- review(assay): P20 reviewer repairs — commit identity, bounded reads, gated hostile-Git (6251adc8)
- implement(assay): P20 repository/artifact boundary integrity (30188240)
- carve(assay): freeze P20 adversarial contract (b674d3e3)
- merge(assay): P15 -- measurement input integrity (v1.1 series opens) (326507f1)
- carve(assay): P25 -- real Vitest coverage is parsed without losing or inventing judgment (bcf9afb9)
- carve(assay): P24 -- a real Go pipeline catches each canary for its intended cause (6f116df8)
- carve(assay): P23 -- Go changed-line mutants are valid single-site programs judged by real go test (ae665a6c)
- carve(assay): P22 -- a real Go toolchain produces an R1 verdict through the installed CLI (1756be25)
- carve(assay): P21 -- every consumable assay wheel has a stable non-placeholder identity (701c1f3b)
- carve(assay): P20 -- declared attested evidence is bounded, contained, and path-current (75a5f143)
- carve(assay): P19 -- assay run proves a declared canary in an isolated real pipeline (94867b23)
- carve(assay): P18 -- assay run constructs and executes the declared changed-line mutants (2bff5d16)
- carve(assay): P17 -- assay run executes a declared Python R1 lane end to end (8e6090bb)
- carve(assay): P16 -- schema v3, assay verify actually rederives R1/R2/R3 status (14577e3f)
- carve(assay): P15 -- measurement input integrity (v1.1, sol) (48771e48)
- rule(assay): P14 readiness findings -- A-128 through A-133, land before dispatch (56c821c2)
- rule(assay): P13 readiness findings -- A-123 through A-127, land before dispatch (a42fe02a)
- rule(assay): P12 readiness findings -- A-116 through A-122, land before dispatch (f828d14e)
- rule(assay): P11 readiness findings -- A-112/A-113/A-114/A-115, land before dispatch (887bae41)
- rule(assay): P10 readiness findings -- A-110/A-111, land before dispatch (aa5b28c7)
- rule(assay): P09 readiness findings -- A-105..A-109, land before dispatch (23122f9b)
- rule(assay): P08 readiness findings -- A-102/A-103/A-104, land before dispatch (c6bb7aa6)
- rule(assay): P07 readiness findings -- A-100/A-101, land before dispatch (90f9de44)
- rule(assay): P06 readiness findings -- A-098/A-099, land before dispatch (05ab843e)
- rule(assay): P05 readiness findings -- A-096/A-097, land before dispatch (0958efdf)
- rule(assay): P04 readiness findings -- A-094/A-095, land before dispatch (bfc467b8)
- rule(assay): P03 readiness findings -- A-092/A-093, land before dispatch (e97d6e6f)
- merge(assay): P02 -- changed-line extraction and measurability guards (89a489a0)
- rule(assay): P02 readiness findings -- A-090/A-091, land before dispatch (04e72c9a)
- Make the Go no-code proof toolchain-independent (e86c81c8)
- Close missing-tool and mutation-command contracts (88798f03)
- Withdraw defective handoffs and reissue the assay series (98901252)
- Repair verdict and execution contracts before recarving (9bd7d206)
- merge(assay): P01b -- the verdict model and a schema that REJECTS (caf4fc78)
- chore(assay): close A-O01/A-O02 and record the implementation loop (37a710cd)

### Documentation
- docs(assay): correct the reviewer module's own header after phase 2 (fb768163)
- docs(assay): record the P22 scope correction in the package LOG (f77fdafd)
- docs(assay): promote P21 missing-tool fixture (d82e9c02)
- docs(assay): promote P21 Go ordering contract (58f3fb40)
- docs(assay): normalize P22 report whitespace (f5fdaaa2)
- docs(nyxloom): make frozen wave pilot executable (8aad3dc3)
- docs(assay): normalize adversarial review markdown (b91ef9af)
- docs(assay): recarve pre-adoption wave P20-P32 (257f2e7e)
- docs(nyxloom): define frozen fork workflow and contract ladder (2f2167f5)
- docs(nyxloom): make complex handoffs solution-bearing (f521bfaa)
- docs(assay): review P15-P19 and recarve successor wave (ebbe208c)
- docs(assay): P19 gate reverified on main after merge (1d31eae1)
- docs(assay): record P19 gate counts measured inside tester-unified (2215fdfc)
- docs(assay): P19 LOG, rulings A-149..A-152, A-O18, STATE.md, P24 amended (67533325)
- docs(assay): P19 successor-brief -- implementer notes carried into P24 (6747386a)
- docs(assay): A-O17 -- one AssayError still escapes evaluate_r1, assigned to P22 (91d29390)
- docs(assay): P18 LOG, rulings A-145..A-148, STATE.md (c5697871)
- docs(assay): P18 successor-brief -- implementer notes carried into P19/P23 (3a902c4b)
- docs(assay): record P17 gate counts measured inside tester-unified (c26acd00)
- docs(assay): P17 rulings -- A-139..A-144, A-128 closed, four handoffs amended (1232793d)
- docs(assay): P17 successor-brief -- implementer notes carried into P18/P19 (1e66c09c)
- docs(assay): P16 rulings -- A-136/A-137/A-138, A-O16, and five handoffs amended (7b376fbb)
- docs(assay): STATE.md -- P15 merged and reviewed; A-O15 (attestation path transport) (279f7026)
- docs(assay): P15 rulings -- A-134 (input boundaries), A-135 (contradictory fixtures) (a8357405)
- docs(assay): STATE.md -- P15-P25 carved and landed, P26/P27 blocked on usage cap (75c845a2)
- docs(assay): persist sol's post-series adversarial review; STATE.md update (9782ddf9)
- docs(assay): STATE.md final update -- P00-P14 series complete (e1851eec)
- docs(assay): P14 LOG + final-state BRIEF -- series close (c89a80e6)
- docs(assay): propagate P13's landing -- P14 citations, STATE.md resume (b1a49d65)
- docs(assay): P13 LOG + successor BRIEF for P14 (8ceca57c)
- docs(assay): propagate P12's landing -- STATE.md resume, no handoff changes needed (652d1d00)
- docs(assay): propagate P11's landing -- P12 Mutant/generate_mutants citation, STATE.md resume (6db09cba)
- docs(assay): P11 LOG + successor BRIEF for P12 (53e3ddbf)
- docs(assay): propagate P10's landing -- P12 assemble_verdict citation, STATE.md resume (9e8cf78a)
- docs(assay): P10 LOG + successor BRIEF (81b429aa)
- docs(assay): propagate P09's landing -- P11 stale-citation watch, STATE.md resume (c0d341fd)
- docs(assay): P09 LOG + successor BRIEF (39bcd7ba)
- docs(assay): correct STATE.md's own "protocol frozen for good" overreach (29760a03)
- docs(assay): propagate P08's landing -- STATE.md resume (2990f2a6)
- docs(assay): P08 LOG -- record the actual commit hash (302d9b87)
- docs(assay): propagate P07's landing -- P08 O2 tension, STATE.md resume+trim (c1439965)
- docs(assay): P07 LOG and successor brief (6c9885b6)
- docs(assay): propagate P06's landing -- P07 vocabulary gap, STATE.md resume (0975214a)
- docs(assay): P06 LOG and successor BRIEF for P07 (bd0b33b5)
- docs(assay): propagate P05's landing -- BRIEF pointers, STATE.md resume (2c7b4725)
- docs(assay): P05 LOG -- fill in implementation commit hash (af0b0e06)
- docs(assay): propagate P04's landing -- A-O14, STATE.md resume (fb7b702b)
- docs(assay): P04 LOG -- record the implementation commit hash (8fe0bc30)
- docs(assay): propagate P03's landing -- STATE.md resume (6d64a8b2)
- docs(assay): record P03's own commit hash in its LOG (5a3e8ad9)
- docs(assay): propagate P02's landing -- P10 git.run trap, STATE.md resume (ec770651)
- docs(assay): P02 LOG and successor brief (c6c95c90)
- docs(assay): persist session state; correct two WORKFLOW claims that measurement disproved (faf502ed)
- docs(assay): trim the P01b brief under the 500-word limit (c68ead5d)
- docs(assay): trim the P01b brief under the 500-word limit (4579e4ce)
- docs(assay): trim the P01b brief under the 500-word limit (4c2c5759)
- docs(assay): P01b LOG and successor brief (13b736a0)
- docs(nyxloom,assay): handoff review belongs to the reviewer, not the implementer (bf604d8d)
- docs(assay,nyxloom): measurement protocol; reconcile P01a's brief instead of annotating it (902ea7d7)
- docs(assay,nyxloom): ratify P01a's rulings, pay forward its debts, backlog the brief (659e02d6)
- docs(assay): P01a successor brief (c497111c)
- docs(assay): P01a LOG -- gate output, per-oracle evidence, self-review (9c03e0a5)
- docs(assay): close 13 spec defects the P01 pre-flight found; split P01 (61052ae4)
- docs(assay): scope the standalone testing/rigor library -- design only, no source (a0c9e515)

### Testing
- test(assay): bind gate receipts to checked driver (cfd340cc)
- test(assay): P10 -- pin A-110's remap independently of the outer catch (911af565)
- test(assay): close the last untested rejection paths in the loader (c9119092)
