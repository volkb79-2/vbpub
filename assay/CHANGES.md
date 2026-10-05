# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. A marked `backfilled-after-release` entry was generated after its immutable tag already existed.

## [Unreleased]

### Added
- feat(assay): explicit project-level `[defaults].env_passthrough`, ordered
  union with lane lists, and exact-file `source_roots`; verdict schema v14
  records the effective allowlist (B140, B141)
- fix(assay): redact every effective passthrough value in verdict JSON and
  mask exact echoes before tail truncation in command tails, crash-resume
  records, failed probe diagnostics and Go helper refusals; record each
  present value's per-name SHA-256 fingerprint for cross-verdict comparison
  and mutation resume identity (B142)
- feat(assay): `assay plan` names its lane and reports why ingested R2
  candidate enumeration is unsupported; first-run budget sizing for Stryker
  is documented without inventing an estimate (B137)
- feat(assay): `assay analyze plan-estimate`; `assay plan` JSON `commit`/`tree`
  and a stderr hint; `candidate` progress `cpu_seconds`/`peak_rss_bytes`/
  `phase_seconds`/`startup_seconds` and state `resources` (with the
  `<events>.resources.json` sidecar); baseline `test` `setup_s`/`teardown_s`.
  All additive; no schema version changes (B111)
- feat(assay): `assay analyze campaign <lane>` (B108 phase 1), a read-only,
  deterministic campaign closeout: `--file`, `--expected-commit`, `--progress`,
  `--verdict`, `--command-exit`, `--state-dir`, `--request-base`, `--project`,
  `--project-jobs`, `--coverage`, `--log`, `--outcome`, `--path-prefix`,
  `--offset`, `--limit`, `--format`. Its JSON (schema
  `analysis-campaign.schema.json`) carries `errors[].source`,
  `complete_blockers`, `reclassified`, `state`, `adverse`, `unresolved`,
  `projection` and `campaign.selected_total`; exit 0/1/2/3 (3 is `incomplete`,
  unlike `analyze report`, where 3 is `running`). Additive judge surfaces it
  needs: the `candidates` progress key `judge_sha256`; the `assay plan` row keys
  `source_sha256` and `mutated_file_sha256`; the public `cli.plan_jobs` and
  `mutation.candidate_identity_fields`; and the public aliases replacing the
  private judge names the analysis package used to reach. No existing verdict,
  progress or plan key changes meaning; no schema version changes

### Fixed
- fix(assay): prevent cgroup process-limit or OOM events from becoming native
  R2 kills or survivors; sample candidate and visible-ancestor event counters,
  reject affected resume records, and advance the judge identity to `/6`,
  cold-starting the earlier `/4` and `/5` B145 evidence (B145)

### Changed
- refactor(assay): repeated judge rules now live once: `assay.records` (the
  frozen/keyword-only record decorators), `assay.guards` (strict-int, finite,
  positive, at-least, sha256-hex and aware-datetime predicates), producer-side
  claim/policy helpers in `verdict`, and same-side helpers in git, isolation,
  liveness_resources, runner, mutation, cli, evaluate and coverage; the raw
  verifier `assay.verify` keeps its own independent copies (A-182). Messages,
  exception types and reason codes are unchanged, and `assay.verdict.__all__`
  gains only `claim_for` and `claim_carries` (the runner uses both); mutation
  candidates in `src/assay` fall from 3733 to 3367. Assay-internal: no
  consumer-facing change (B129)
- chore(assay): assay's own two B105 self-qualification lanes now use the
  shallow snapshot (`snapshot_history = "shallow"`, the A-451 default) instead of
  full history, since no collected judge test reads history; a new test proves a
  shallow snapshot has no `HEAD~1`. Assay-internal: no consumer-facing change
  (B128)
- feat(assay): the registered gate has an explicit shared-host opt-in,
  `ASSAY_GATE_ALLOW_SHARED_HOST=1` (and `qualify_sql.py --allow-shared-host`),
  that runs alongside other projects' `run-gate-*` containers and prints
  `ASSAY_GATE_SHARED_HOST=<names>`. Two scopes: the gate
  (`ASSAY_GATE_ALLOW_SHARED_HOST=1`) still refuses any other `run-gate-assay-*`
  container, and the harness (`--allow-shared-host`) still refuses only another
  `run-gate-assay-sql-*` container; the default stays the exit-3 `host busy`
  refusal (CD50). Assay-internal: no consumer-facing change
- refactor(assay): judge tests (`tests/`) and tooling tests (`gate/tests/`, for
  the gate script, checker, wheel and zipapp, packaging and lane-config drift)
  are separate trees; `tester-unified` runs both, the B105 lanes collect
  `tests/` only without `--override-ini`, and the full `self-qualification`
  lane requires a same-commit `tester-unified` receipt
  (`assay/.assay/registered-gate/tester-unified.json`, written by the gate
  script after a green run); the gate exits 3 with
  `ASSAY_GATE_INCONCLUSIVE=host busy` when another `run-gate-*` container is
  running. Assay-internal: no consumer-facing change (B123, A-476)
- chore(assay): the registered gate retires the Topos and CMRU qualification
  harnesses and the historical schema phases; one refusal test per schema
  remains (A-475/A-477, B124/B125). Nothing in the product changes.
- refactor(assay): move `assay analyze` into a second top-level package,
  `assay_analysis`, shipped in the same wheel and zipapp; `assay analyze`
  behaviour, exit codes and schema paths are unchanged (a usage error for
  unrecognized arguments now prints the `assay analyze` usage line and prefix),
  the undocumented `import assay.analysis` is removed, an editable install
  made before this change must be re-run so `assay analyze` finds the new
  package, the package has its own R0+R1 lane, and the B105 report checker
  now names its scope (`src/assay` only; analysis out of scope by A-478)
  (B127)

### Fixed
- fix(assay): `plan-estimate` binds its baseline to both the plan's lane and
  commit, validates lane identity on every progress run header, and returns
  the lane in its JSON result (B136)
- fix(assay): liveness test leak; CONSUMERS 'upper bound' claim (the plan
  estimate is declaration-derived, not an upper bound); the B105 report checker
  refuses a partial, sharded or foreign R2 campaign against the plan (B111)
- fix(assay): require time-aligned resource evidence and a CPU-quiet window
  before classifying an R2 candidate as hung (B107/RW-57)
- fix(assay): restart the hung CPU window when an exiting descendant lowers
  the live process-tree CPU total, and reject cached hung traces that cannot
  prove their idle span or contain malformed counters (B107 final review)
- fix(assay): single-operator mutants of the Go, JavaScript, SQL and go.mod
  scanners, and of the git tree parser, now fail fast instead of spinning until
  the per-candidate budget; the `git.py` pipe-drain loop's exit test
  no longer offers a mutant that blocks forever (B113/A-466)

### Testing
- test(assay): characterization tests pin the accept/refuse verdict and exact
  message of every judge rule that B129 consolidated, a dataclass-contract test
  (resolved decorator objects plus a committed fixture), boundary-value tests
  for the shared guard predicates, and `tests/core/test_trust_boundary.py`,
  which keeps `assay.verify` free of `assay.guards`/`assay.records` and of the
  producer-only verdict helpers (B129)
- test(assay): G1–G5 snapshot invariant guards (B111)
- test(assay): deterministic mutant-guard tests, one per component, under a
  line-event budget (B113)

<!-- cmru: release history -->

## [7.2.0] - 2026-09-30
<!-- cmru: generated -->
<!-- cmru: source-end=5248b345d018d3917cde6c25b88a06149f705eec -->

### Added
- feat(assay): committed SQL witness (24 rows) and the three witness tests (B126, A-480) (ae5e97f8)
- feat(assay): campaign evidence modes, state reconciliation, rows, ETA, projection (B108 steps 3b-3f, code) (70ab9239)
- feat(assay): CD50 shared-host opt-in for the gate and the SQL harness (98195e04)
- feat(assay): campaign exit mapping and evidence_error documents (B108 step 3a) (c71bd31b)
- feat(assay): port campaign analysis draft (B108 phase 1) (e251f6ef)
- feat(assay): public plan_jobs, candidate identity fields and judge in the candidates event (B108 phase 1 J1-J5) (814494ac)
- feat(assay): B105 checker refuses partial or foreign R2 campaigns via --plan-json; driver plans first (W8-C, B111) (42405437)
- feat(assay): assay analyze plan-estimate and assay plan commit/tree/hint (W8-P+H, B111) (0484018c)
- feat(assay): per-candidate resource, phase and startup evidence (W8-R, B111) (73003664)
- feat(assay): self-contained SQL qualification on a digest-pinned PostgreSQL 18.6 (B126, A-480) (a0cca9db)
- feat(assay): forward baseline setup_s/teardown_s on test progress events, O9 (W8-S, B111) (9675b112)
- feat(assay): B105 self-qualification lanes use snapshot_history=shallow (B128 W7, commit SHALLOW) (a3a6d733)
- feat(assay): require_advance loop guards, git drain exit test without mutation sites (B113, A-466) (f13bf1d0)
- feat(assay): B105 scope check, analysis gate lane and package tests (B127, W2 steps 7-9, T3, T8-T10) (4f2fd892)
- feat(assay): move assay analyze into the assay_analysis package (B127, W2 steps 1-6, T1-T7) (1cd16baf)
- feat(assay): retire cross-project harnesses and historical schema phases from the gate (B124, B125, A-475, A-477) (bb696f53)
- feat(assay): retain B105 snapshot coverage arcs (8ed5c2b6)
- feat(assay): add full-source self-qualification gate (B105) (97031375)

### Fixed
- fix(assay): unknown judge cannot vouch for a resume claim (W9 V-1) (883a74de)
- fix(assay): W9R-1/W9R-2 — complete needs an event per pending candidate and a backed resume claim (e7127416)
- fix(assay): W5 review fixes W5C-1..W5C-6 (docker ps inconclusive, pins at run time, rm failure loud, lost docker env, CD50 scope docs) (e1cd59cf)
- fix(assay): --command-exit without --verdict is an evidence error (CD55 Q12, B108) (db6cecad)
- fix(assay): K18/K20 catch restrict_violation (CD53), re-pin probes digest (b45b8ae1)
- fix(assay): campaign error source names the refused input (CD51 Q1, B108 step 3a) (94e1f616)
- fix(assay): CD41 commit/tree check also runs when only expected_tree is given (B111, W8R-4) (1359762c)
- fix(assay): plan-estimate refuses a projection that overflows to infinity (B111, W8R-3) (3df040e7)
- fix(assay): a failing docker ps is inconclusive, exit 3, receipt untouched (B123, W4R-2) (353f8431)
- fix(assay): the tester container proves it judged the host-captured commit (B123, W4R-8) (210b45dd)
- fix(assay): a non-zero exit after launch removes the tester-unified receipt (B123, W4R-1) (3d51d412)
- fix(assay): re-pin liveness.py TYPE_CHECKING exclusion after B107 (55611561)
- fix(assay): add B107's liveness_resources.py to the B105 whole-source targets (ef9bb3bb)
- fix(assay): close B107 final review evidence gaps (76325aa5)
- fix(assay): require CPU-quiet post-finish grace (RW-57) (3c63ca34)
- fix(assay): make hung classification pressure-aware (B107) (193b8dd0)
- fix(assay): bound unfinished B105 qualification gate (b152ea3a)
- fix(assay): bind B105 evidence to exact runs (67ac2872)
- fix(assay): unify R2 classification and close B105 coverage gaps (62b41112)
- fix(assay): preflight B105 before full qualification (61bc5f43)
- fix(assay): qualify the isolated source snapshot (afd8c5bf)
- fix(assay): use tester interpreter for B105 lane (9f1d78bb)

### Changed
- merge: Wave A W9 campaign analysis (B108 phase 1, assay analyze campaign) into landing (2ab0e737)
- merge: Wave A W10 DRY consolidation (B129; records/guards; 3733→3367 candidates) into landing (82ae03d2)
- refactor(assay): W10 step 5 — same-side cluster helpers E1-E3, E5-E10 (B129 I4) (44755782)
- refactor(assay): W10 step 4 — policy-iff-attempted and claim lookup helpers (B129 I3) (7e715276)
- refactor(assay): W10 step 3 part 3 — verdict and verify private twins (B129 I2) (8e23eddc)
- merge: Wave A W5 self-contained SQL qualification + CD50 shared-host opt-in into landing (2b12781f)
- refactor(assay): W10 step 3 part 2 — config, mutation, runner, isolation, liveness, liveness_resources, adjudication (B129 I2) (8f48a2f9)
- refactor(assay): W10 step 3 part 1 — guards.py, test_guards.py, first eight producer files (B129 I2) (6f70871e)
- refactor(assay): W10 CD57 — rename local record loop variables to record_entry (pyflakes clean) (6de38226)
- refactor(assay): W10 step 2 — @record/@positional_record replace 81 dataclass decorators (B129 I1) (c6809420)
- merge: W9 judge seams J1-J5 (814494ac) as W10 base (CD52) (e32b09d6)
- merge: Wave A W8 measurement hygiene + plan-estimate (P0, B111) into landing (5a695516)
- Merge assay-b110-landing (W6 and latest decisions) into wave-a-w7-shallow (08d059ad)
- Merge assay-b110-landing (W6 and latest decisions) into wave-a-w5-sql (c8e6b783)
- Merge W6 (B113): P2 loop guards (75ceb9e9)
- Merge W2 (B127): assay analyze as its own package in the same distribution (42dd7285)
- Merge W1 (B124/B125): retire cross-project harnesses and historical schema phases (83f099e5)
- Merge W3 (B130): component boundaries report, import contracts, component test layout (eb0acea6)
- Merge assay-b110-landing (latest Wave A decisions) into wave-a-w6-guards (20846716)
- Merge assay-b110-landing (latest Wave A decisions) into wave-a-w2-analysis (4c53f28c)
- Merge assay-b110-landing (latest Wave A decisions) into wave-a-w1-retire (762b500d)
- merge: main (B107 load-independent tests) into assay-b110-landing (1e3c8a49)
- merge: main (B107 pressure-aware liveness) into assay-b110-landing (7d7b0fb9)
- review(assay): accept B107 current-main candidate (415686f5)

### Documentation
- docs(assay): W9 LOG — V-1 fix, READY-FOR-GATE 883a74de (code commit) (ab65c996)
- docs(assay): W9 LOG — review fixes (W9R), READY-FOR-GATE d2e4b0b3 (da68919b)
- docs(assay): CD60 — W9 review rulings (90459553)
- docs(assay): W10 review fixes (W10R-1 CHANGES __all__ wording, W10R-2 CD56 fail_under ruling) (ac52eba1)
- docs(assay): W9 checkpoint 11 (QUESTIONS 24-25, controller's gate run, READY-FOR-GATE 14bc8588) (827b51c5)
- docs(assay): W10 traceability table, finish results, gate-run notes, READY-FOR-GATE f5498920 (488c3809)
- docs(assay): analyze campaign in README, DESIGN-GUIDE, CONSUMERS and CHANGES; B108 boxes 1, 2, 4 (B108 step 4, W9) (33076c79)
- docs(assay): W10 steps 7-8 — moved-path comments, B129 docs, assay.toml targets, final inventory (CD43, CD48) (f5498920)
- docs(assay): W10 step 5 LOG and checkpoint 10 continuation (step 6 next) (6ee92c4e)
- docs(assay): W10 checkpoint 9 continuation (step 4 done; step 5 next) (ee52f10a)
- docs(assay): W10 LOG step 3 parts 2-3, O1 and O3 results (304e9c2f)
- docs(assay): W10 checkpoint 8 continuation (step 3 done; O1 break done; step 4 next) (b4588822)
- docs(assay): W9 checkpoint 8 (CD58 deletions logged, state/verdict tables, continuation brief) (592a039b)
- docs(assay): W5-LOG review fixes (W5C) and READY-FOR-GATE e1cd59cf (fe857a7e)
- docs(assay): W10 checkpoint 7 continuation (step 3 part 2 done; verdict.py and verify.py remain) (1a48ffcf)
- docs(assay): CD59 — W5 code review rulings (f56e744a)
- docs(assay): W10 checkpoint 6 continuation (CD57 and step 3 part 1 done) (8e12475e)
- docs(assay): CD58 — W9 Q22 accepted, Q23 redundant raises deleted not pragma'd (302abf88)
- docs(assay): W9 checkpoint 7 (O15, O25, Q18 done; step 3a table in progress; continuation brief) (163db49f)
- docs(assay): CD57 — rename shadowing loop variables for pyflakes (a706c68d)
- docs(assay): W10 checkpoint 5 continuation (steps 1-2 finished) (46de81b6)
- docs(assay): W5 Work 0 re-run, Work 5 results, final log and continuation (READY-FOR-GATE ae5e97f8) (429ff988)
- docs(assay): CD56 — W10 drops E4; deliberate transcriptions stay (741fc4c1)
- docs(assay): W10 checkpoint 4 continuation (step 1 finished) (3800afa9)
- docs(assay): W9 checkpoint 5 (oracles O1-O14a, O16b, O22-O26 partly done; continuation brief) (0d3683c6)
- docs(assay): W10 checkpoint 2 continuation brief and step-1 LOG (c26c2623)
- docs(assay): CD55 answers W9 questions 9-18 (81237fc9)
- docs(assay): W9 checkpoint 4 after the campaign core (continuation brief, LOG questions 9-18) (db340684)
- docs(assay): CD53 (K18/K20 restrict_violation) and CD54 (CD50 env hygiene) (74b20e7e)
- docs(assay): W5 Work 0 result: K18/K20 probes fail unmutated on PostgreSQL 18.6 (blocked, QUESTION 1) (b36e31e5)
- docs(assay): W10 checkpoint 1 continuation brief (0331d74f)
- docs(assay): W10 step 0 base inventory LOG (B129) (4a448cba)
- docs(assay): CD52 — W10 early base, gate ownership, FORCE_COLOR test pin (be7f02e6)
- docs(assay): Wave A stage-2 integration — CD49, handoff rows, 2026-09-30 operator rules (ce86fed4)
- docs(assay): CD51 answers W9 questions 1-8 (075023d7)
- docs(assay): W9 checkpoint 3 after exit mapping (continuation brief) (c5d980a6)
- docs(assay): W9 checkpoint 2 after the port (continuation brief) (500112f7)
- docs(assay): W9 checkpoint 1 after judge step (continuation brief) (a91d463d)
- docs(assay): W8 review recorded (40317e29)
- docs(assay): W8 review fixes logged, READY-FOR-GATE 7e83bcab (B111) (b6096460)
- docs(assay): state the TreeSample lower-bound semantics and which candidates carry phase_seconds (B111, W8R-5) (30a38b93)
- docs(assay): W7 review recorded (8b5ff9ac)
- docs(assay): W7 log READY-FOR-GATE after review fixes (B128) (baf24995)
- docs(assay): record the step-0 root-layout directory follow-up (B128, W7R-4) (9e1a932f)
- docs(assay): flag the W7 full-run figures as approximate (B128, W7R-3) (988feed6)
- docs(assay): W7 log carries the counts-capable drift proof and local result (B128, W7R-1) (1d777195)
- docs(assay): W8 LOG complete, READY-FOR-GATE 2d3a0e58 (B111) (cd110f2c)
- docs(assay): W8 docs sync, measured plan estimate, resource evidence, R2 plan check (B111) (f5fb9098)
- docs(assay): correct what the shallow seed holds for the B105 lanes (B128, W7R-2) (952a5329)
- docs(assay): W8 checkpoint 2 continuation after P+H, C, G (B111) (8c7abdbd)
- docs(assay): W8 checkpoint LOG and continuation after step R (B111) (d461e91b)
- docs(assay): W5 log and continuation checkpoint (host busy; container steps pending) (c45d0e1b)
- docs(assay): SQL qualification design, out-of-scope list, decision notes and backlog (B126, B132, A-480) (d2143b06)
- docs(assay): W7 log and READY-FOR-GATE (B128) (bdd9cde7)
- docs(assay): B105 lanes use the shallow snapshot; CHANGES and backlog B128 (W7) (157421f2)
- docs(assay): W4 log, review fixes and READY-FOR-GATE (B123) (21a7b1f0)
- docs(assay): W4 report lists the out-of-scope files naming moved test paths (B123, W4R-6) (f9ba8212)
- docs(assay): S1 and CD32 operational rules in README, CONSUMERS, DESIGN-GUIDE (B123, W4R-5) (fb784413)
- docs(assay): W4 review recorded; CD48; B135 (witness ignores pytest.toml) (fae0727c)
- docs(assay): CD47 W4 P5 stand-in for go_stmtpos is two judge files (2bdd7ef0)
- docs(assay): W4 log and report, READY-FOR-GATE (B123) (d9f1a990)
- docs(assay): W4 two test trees and S1 in README, DESIGN-GUIDE, CONSUMERS, CHANGES, decisions, backlog; moved-test path prose (B123, CD43) (da355733)
- docs(assay): handoff timestamp fix (068c7729)
- docs(assay): W6 review-fix log and READY-FOR-GATE (B113) (7a02c24d)
- docs(assay): correct the O4 missing anchor in the W6 log (B113, W6R-5) (fa59dad7)
- docs(assay): scope the drain 'cannot block' claim to its exit test (B113, W6R-4) (12690707)
- docs(assay): Wave A handoff: W2 merged, W6 in fix, W4 dispatched (957252e6)
- docs(assay): B134 host-dependent git-marker test; W6 review recorded; W5 new ids from B135 (11ace522)
- docs(assay): W4 brief: nyxloom.toml pythonpath comment (W2 D10) (00b42101)
- docs(assay): W2 review-fix log, oracle results and READY-FOR-GATE (B127) (f0492d23)
- docs(assay): correct verify_scope wording, assay.toml pythonpath comment and B127 status (B127, W2R-6) (141ac746)
- docs(assay): tell pre-B127 editable installs to reinstall (B127, W2R-5) (617d1055)
- docs(assay): W2 review recorded; CD46, CD31 amended (package-based fixture loader) (eaa9fccb)
- docs(assay): W6 READY-FOR-GATE marker (4979f6d0)
- docs(assay): W6 loop guards docs and log (B113) (8036bd8d)
- docs(assay): Wave A handoff: stage gates; W3 and W1 merged (40ecb20f)
- docs(assay): W1 review recorded (e920e0db)
- docs(assay): W1 review-fix log, READY-FOR-GATE (B124/B125) (f98f58e0)
- docs(assay): REPORT anchor-shift note and review-fix records (B124/B125, W1R-7) (9b567d0e)
- docs(assay): B124 status notes the open dstdns-checkout item (B124/B125, W1R-6) (0c306c03)
- docs(assay): correct the REPORT's tests/qualification classification (B124/B125, W1R-5) (15fa9940)
- docs(assay): retire the stale A-468 --ignore in the B105 criterion (B124/B125, W1R-4) (a7775c49)
- docs(assay): Wave A handoff: W1/W2 done and in review, W6 dispatched (17c46012)
- docs(assay): W2 final log, oracle table and READY-FOR-GATE (B127) (e81dce9c)
- docs(assay): W1 docs, decisions, backlog, log and report (B124, B125) (fb4b0cc7)
- docs(assay): document the assay_analysis package boundary (B127, W2 step 11) (d816dbf4)
- docs(assay): W2 checkpoint log and continuation brief (B127) (5e763cb2)
- docs(assay): Wave A handoff: W3 fixed and queued, W1/W2 dispatched (bd08657b)
- docs(assay): W3 review-fix log (B130) (7a9c8948)
- docs(assay): fix W3 durations.py component attribution on post-move junit (B130, W3R-5) (b4139c18)
- docs(assay): W3 review recorded; CD43 moved-path prose, CD44 controller owns gates, CD45 tests/core ignore trap (1fc887a3)
- docs(assay): Wave A handoff: controller owns package gates; W3 status (e54deb16)
- docs(assay): W3 log, oracle results; gate not run (host busy) (ea990a53)
- docs(assay): W8/W9 pre-dispatch review applied; CD39-CD42, CD38 amended (189ec19a)
- docs(assay): W5 brief round-2 review applied; CD21 exit-3 rule (9e34f7f9)
- docs(assay): Wave A handoff status (03cd67f3)
- docs(assay): Wave A W8/W9 self-contained briefs (CD26); CD34-CD38 (ec427945)
- docs(assay): Wave A W5 brief fixed per review; CD21 amended, CD32 gate-entry host check, CD33 (43abf9e8)
- docs(assay): W3 Part 1 component boundaries report (B130) (be0357de)
- docs(assay): Wave A controller handoff (living file) (76ad4c1c)
- docs(assay): apply Wave A pre-dispatch review to W1-W4, W10 and rebase notes; CD31 (3daf62a7)
- docs(assay): Wave A pre-dispatch review round 1 and carver decisions CD25-CD30 (f27fbdea)
- docs(assay): Wave A briefs W1-W5, W10, P0/P2 and P8 rebase notes, carver decisions CD1-CD24 (be803c3a)
- docs(assay): preserve the uncommitted B108 campaign-analysis draft for W9 (c40ec3b8)
- docs(assay): Wave A -- B130 moves to stage 0; implementers are Sonnet only (2160de12)
- docs(assay): record Wave A decisions A-475..A-480, backlog B123..B130, plan (5bbd916e)
- docs(assay): record B107 load-independent final review (ad32d14e)
- docs(assay): file B122 pluggable per-candidate scratch provider (CoW requirements) (02e46b1a)
- docs(assay): apply round-2 verification of the B110 reuse/testability report (a41cf441)
- docs(assay): apply fresh review of the B110 reuse/testability report (29b791bd)
- docs(assay): add structural research R9-R11 and Part C to the B110 reuse review (84e4618f)
- docs(assay): add B110 reuse and testability review (non-binding) (95622e3a)
- docs(assay): apply round-3 (final) pre-dispatch review to the B110 plan (5eb5768f)
- docs(assay): apply round-2 pre-dispatch review to the B110 plan (210d6130)
- docs(assay): apply round-1 pre-dispatch review to the B110 plan (3ee09b61)
- docs(assay): carve B110 runtime plan, split into B111-B121 (a3b68800)
- docs(assay): record B105 runtime ceiling and proof plan (db85f747)
- docs(assay): record B105 gate results and capacity bounds (509feb89)
- docs(assay): record stopped B105 runtime attempt (be2d2dff)
- docs(assay): record B105 gate launcher refusal (94b547e5)
- docs(assay): file B106 impact-aware reuse follow-up (f8ad7b3b)
- docs(assay): file campaign analysis backlog item (8cdaed3c)
- docs(assay): capture prior B105 gate failure (13477882)
- docs(assay): record final B105 candidate plan (25c4f4ea)
- docs(assay): record B105 whole-source preflight (5b851bc4)
- docs(assay): record B105 mutation plan (e138f0c3)
- docs(assay): qualify B107 cross-run comparison (659a92a5)
- docs(assay): track pressure-ambiguous candidate liveness (4241cdc1)

### Testing
- test(assay): W9R-3..W9R-6 pins and W9R-7 CONSUMERS correction (d2e4b0b3)
- test(assay): pin keyword-before-punctuation case in _preceding_word (W5b) (e1b0c76e)
- test(assay): owed controlled breaks for campaign pins; two weak pins strengthened; CD58 deletes the unobservable in-selection filter; complete raise-to-case map (B108 step 3a, W9) (14bc8588)
- test(assay): pin drop-not-null with no preceding word (sql.py:157 coverage) (f37d9595)
- test(assay): campaign.py to 100% line+branch over table rows and pins; CD58 deletes the candidate_id None branch; controlled breaks owed (B108 step 3a, W9) (b9a2225d)
- test(assay): W10 step 6 — trust-boundary contract for verify vs guards/records (B129 O5) (2918cb89)
- test(assay): --coverage block over a real R1 run; owed controlled breaks recorded; CD58 deletes 13 coverage-block raises and the redundant commit check (B108 step 3a, W9) (190d91de)
- test(assay): state-dir and verdict refusal tables; CD58 deletes 10 verifier-dominated verdict-shape raises and 5 reader-dominated progress raises (B108 step 3a, W9) (2181a9e8)
- test(assay): campaign refusal table, field helpers, verdict bytes, lane-file binding rows (B108 step 3a, W9) (78aab8ff)
- test(assay): campaign refusal-reachability table, progress half (B108 step 3a, W9) (9b9e35fc)
- test(assay): campaign oracle O15 on a real R2 rejudge run; fix effective judge.mode default (B108, W9) (001a3b95)
- test(assay): campaign oracles O17-O19 on real R1 evidence; fix two coverage-path crashes (B108, W9) (c7084318)
- test(assay): W10 step 1 finished: verify int sites and I4 E1-E10 pins (48c300f4)
- test(assay): W10 step 1 verify strict-int/text pins; checkpoint 3 continuation (fa8a0725)
- test(assay): campaign oracles O4, O5, O8, O9, O14a, O22, O23 (B108, W9) (39cb2707)
- test(assay): W10 step 1 I3 verify-side raw policy-presence pins (93afecbd)
- test(assay): W10 step 1 I3 producer-side policy-present-iff-attempted pins (6abbd8c4)
- test(assay): campaign oracles O1-O3, O6, O7, O10, O12, O13, O16b, O24, O26 (B108, W9) (4699cb44)
- test(assay): W10 CD52 _no_ambient_color autouse fixture in the three conftests (c35a4254)
- test(assay): characterize runner, mutation and liveness scalar guards (B129 W10 step 1) (18c2dfaf)
- test(assay): drop ASSAY_GATE_ALLOW_SHARED_HOST from gate-test subprocess envs (CD54) (e4b4769d)
- test(assay): move W10 characterization files into their layout folders (2320fcbf)
- test(assay): characterize adapter and parser strict-int guards (B129 W10 step 1) (44c67854)
- test(assay): characterize verdict policy and execution guards (B129 W10 step 1) (dd2e17ec)
- test(assay): characterize attestation, provenance, shard and runner guards (B129 W10 step 1) (76907bc8)
- test(assay): characterize config scalar guards before guard helpers (B129 W10 step 1) (cc7e6760)
- test(assay): characterize isolation, mutation and liveness scalar guards (B129 W10 step 1) (8e016971)
- test(assay): characterize verdict wire validators before guard helpers (B129 W10 step 1) (08b3f996)
- test(assay): dataclass contract test and fixture, resolved-decorator detection (B129 W10 step 1) (0ec04d82)
- test(assay): O7 diagnostics-never-fail tests cover normal, hung and timeout rows (B111, W8R-7) (7e83bcab)
- test(assay): plan-estimate fixture has jobs 3 so dividing by jobs is caught (B111, W8R-6) (80926dbb)
- test(assay): O8 resume must reuse both records with none rejected (B111, W8R-2) (5835f56a)
- test(assay): pin the startup_seconds differences by value (B111, W8R-1) (602ff951)
- test(assay): analysis package file set names plan_estimate.py (W8, B111) (2d3a0e58)
- test(assay): snapshot invariant guards G1-G5 (W8-G, B111, A-472) (7f6b7cf7)
- test(assay): scope liveness plugin unit tests off the outer stream, O10 (W8-L, B111) (eee1de96)
- test(assay): negative proof that a shallow snapshot hides HEAD~1 (B128 W7, commit FULL) (c1c0e817)
- test(assay): allow root *_support.py helpers in the layout check (CD23 amended; W4+W6 integration) (a4a0010a)
- test(assay): allow root *_support.py helpers in the layout check (CD23 amended; W4+W6 integration) (2faa6426)
- test(assay): the S1 refusal test asserts the receipt is the cause (B123, W4R-7) (5aaa9e4c)
- test(assay): gate/tests imports W2's one judge-conftest loader, with a one-loader test (B123, W4R-4) (91979dda)
- test(assay): the pytest config pin also covers pytest.toml and native tool.pytest keys (B123, W4R-3) (737b7dd3)
- test(assay): the shipped-tree lint test links gate/tests into its scratch clone (B123, W4) (730f2db7)
- test(assay): split judge tests from tooling tests; same-commit tester-unified receipt binds self-qualification (B123, W4) (8a8dc69e)
- test(assay): positive control for the drain exit-test site check (B113, W6R-3) (8bccafdf)
- test(assay): reach all 8 guarded lines with ordinary input and assert results (B113, W6R-2) (094667f6)
- test(assay): O4b refuses a wait before the group kill and always cleans up its child (B113, W6R-1) (f0e2258d)
- test(assay): pin the no-analysis-tree lint refusal to its own message (B127, W2R-7) (194cf74a)
- test(assay): run the zipapp analyze test without site-packages (B127, W2R-2) (6d3cf1bd)
- test(assay): pin the analysis to judge module dependency set (B127, W2R-8) (1b1c66b2)
- test(assay): key analysis sources by path and pin the packages under analysis/src (B127, W2R-3) (34651582)
- test(assay): make analysis/tests a package so its conftest never rebinds the judge's (B127, W2R-1) (c84bdd7d)
- test(assay): exact, distinct shard schema refusal rows (B124/B125, W1R-3) (241e32f6)
- test(assay): refuse a non-current hung-evidence schema version (B124/B125, W1R-2) (f299aef8)
- test(assay): freeze the shipped verdict schema bytes per version (B124/B125, W1R-1) (2e0265f8)
- test(assay): W6 red - scanner progress guard tests, drain and exclusion structure tests (B113) (06e0a790)
- test(assay): guard cross-folder test-module imports (B130, W3R-4) (5a072d0b)
- test(assay): forbid *_test.py files that escape the layout rule (B130, W3R-3) (175cf326)
- test(assay): guard adapter and parser modules against silent core classification (B130, W3R-2) (c2da2f2a)
- test(assay): commit W3's import-contract test that the root .gitignore hid (B130, W3R-1) (b751a37f)
- test(assay): W3 component import contracts and component-organized judge-test layout (B130) (f2c249fe)
- test(assay): remove timing-dependent liveness assertions (35adca38)
- test(assay): reject inflated cached idle spans (801fa033)
- test(assay): preserve deterministic B107 liveness coverage (afa0ef40)
- test(assay): close B105 source coverage gaps (f9d8910f)
- test(assay): close B105 source coverage gaps (35eede15)

## [7.1.1] - 2026-09-26
<!-- cmru: generated -->
<!-- cmru: source-end=94009fbba09c798d8759dce5c4039db2ddb2b196 -->

### Documentation
- docs(assay): correct Wave C closeout date (4b3ff6e2)
- docs(assay): close Wave C release records (35540ab6)

## [7.1.0] - 2026-09-25
<!-- cmru: generated -->
<!-- cmru: source-end=e5e9b95c5ac8be3452c93f1066f9436347f862fd -->

### Added
- feat(assay): add provenance-safe selective mutation reuse (d43167c6)
- feat(assay): add bounded live gate report (0624ab95)

### Fixed
- fix(assay): preserve legacy Topos release smoke (7d45deaa)
- fix(assay): report progress for nested gate runs (11ff984f)
- fix(assay): name shard-scoped rejudge refusal (045dac4c)

### Changed
- merge: current main into B106 P5 worktree (bf2e5ab2)
- perf(assay): bound mutation liveness monitor state (09d1f38d)
- merge: current main into Wave C P2 worktree (f7895bd2)
- Merge branch 'main' into assay-b080-js-default-arg (c5a13a4a)
- Preserve Wave C refusal evidence and doc anchors (a0daf185)
- Fix Wave C refusal diagnostics and classification (e7c5dbe4)
- Merge current main into Wave C P1 (40bd4906)
- Fix statement-less JavaScript default-arg coverage (86c16f85)
- Record Wave C P0 tester-unified gate evidence (24fad0c2)

### Documentation
- docs(assay): record Wave C P5 gate pass (b5014bc3)
- docs(assay): record stale-base P5 gate stop (a0e2fe23)
- docs(assay): record B106 v13 reuse contract (3f117b27)
- docs(assay): record Wave C P4 gate result (dc7c5cbe)
- docs(assay): carve Wave C P4 report command (63d618ac)
- docs(assay): record Wave C P3 gate evidence (5c83bf92)
- docs(assay): record P2 final gate evidence (587c4a57)
- docs(assay): tighten B106 verdict and replay contract (247cb72a)
- docs(assay): approve B106 provenance-safe reuse as P5 (8d79a48a)
- docs(assay): reconcile Wave C gate evidence (b42156a4)
- docs(assay): record Wave C P1 review and gate (e6c7685a)
- docs(assay): file selective mutation rerun use case (0777a571)
- docs(assay): record Wave C P1 review (b925d203)
- docs(assay): correct resolved B028 cross-reference (c0ce884f)
- docs(assay): carve B080 default-arg classification (b13707ad)
- docs(assay): correct Wave C review chronology (26ec6b39)
- docs(assay): close v7 release records and log Wave C findings (36723684)
- docs(assay): Wave C handoff -- fix-verification corrections (9dcb7a3e)
- docs(assay): Wave C handoff for a third-party controller (B080, refusals, liveness, B100) (ab66c149)
- docs(assay): close B101 release records (a3e0dcce)

### Testing
- test(assay): repair v13 self-hosted gate fixtures (c28fbbb9)
- test(assay): preserve verdict successors through v13 (222c8cb8)
- test(assay): align historical gate check with schema v13 (8aa1d61d)
- test(assay): preserve qualification diagnostic stub (f21f9629)
- test(assay): align rejudge message assertions (9595548e)
- test(assay): guard all Wave C documentation anchors (5ba8e0a5)
- test(assay): close Wave C P1 adversarial case (e29c1fb0)

### Added (hand-written release detail)
- feat(assay): add provenance-safe selective native R2 reruns using current
  pytest kill-witness replay, with bounded v13 candidate inventory and a
  full-suite fallback for uncertain cases (B106)

### Fixed (hand-written release detail)
- fix(assay): classify istanbul `default-arg` signature lines from the
  enclosing function's call count (`fnMap`/`f`) when an arm of that same
  branch is attributed to the node's physical line, preserving its branch arcs
  even when the default is unused (B080; resolves withdrawn duplicate B089).
  Judged files now produce coverage numbers instead of a contradictory-record
  refusal; bystanders keep their arcs without a contradiction diagnostic.
  Previously-PASS numbers are unchanged. Multiline defaults whose arms all
  start on other lines retain their unclassified node-line gap (A-459); the
  broader function-call alternative would change some prior 0/0 PASS counts
  to 1/1. Previously-refused whole-target lanes can now count those signatures:
  the measured consumer artifact gains
  six executable lines (ChartCard 54→55, DataTable 105→108, StatCard 1→2,
  StatTile 37→38). Required missing, malformed, or ambiguous function metadata
  refuses `UNREADABLE_ARTIFACT`. B054's braceless-`if` disposition and all
  `FileCoverage` invariants are unchanged.
- fix(assay): replace Git's unreachable `safe.directory` remedy on dubious-
  ownership failures, and classify unknown/stale `--rejudge` ids as
  `BAD_LANE_CONFIG` while preserving corrupt-store errors (B081/B094)

### Documentation (hand-written release detail)
- docs(assay): explain Git ownership refusals, rejudge input classification,
  and the consumer remedies (B081/B094)
- docs(assay): document selective R2 reuse, v12 cold starts, and supported
  JavaScript R2 ingestion (B106)

### Testing (hand-written release detail)
- test(assay): drive unknown, stale, valid, and corrupt `--rejudge` state
  through the CLI and `assay verify`; guard the attestation-timeout
  infrastructure forward (B094/B025)
- test(assay): verify B106 identity inventories and current pytest witness
  replay, including fallback to a full run (B106)

## [7.0.0] - 2026-09-23
<!-- cmru: generated -->
<!-- cmru: source-end=e434b2938551ad6edfb49d35da88ce5eac996822 -->

### Added
- feat(assay)!: record v12 isolation provenance (452cf976)
- feat(assay): use shallow snapshot seeds by default (5bf832a4)

### Fixed
- fix(assay): advance dstdns witness to verdict v12 (c183a371)
- fix(assay): avoid pyflakes field shadowing (4ed15f31)
- fix(assay): refresh dstdns qualification witness (c6a97f1e)
- fix(assay): consume the pre-snapshot resolved base inside P22 snapshots (port of assay-b096 84baffb4; B101 P1) (36f8551c)

### Changed
- backlog(assay): operator triage 2026-09-23 and the B101 wave plan (96ac8197)
- Merge assay B101 P1: consume the pre-snapshot resolved base inside P22 snapshots (e785b955)
- backlog(assay): audit — complete B001-B087 status normalization, finish report (83446781)
- backlog(assay): audit — complete frontmatter index, normalize B088-B103 status (5689b85d)
- backlog(assay): B101 design interview decided; file B102 dirty-tree override (ffa1264a)
- Share native Git worktree inventory across CIU and CMRU (b9cad87c)
- Merge main into workspace instance feature (e2a4a68d)
- backlog(assay): B101 -- note that a design interview is needed before implementation (d05939b9)
- backlog(assay): B101 -- snapshot_selection=repository's max_total_object_bytes measures full git-history closure, unconfigurable, 74-lane blast radius on dstdns (0361b211)
- Merge branch 'main' into feat/ciu-cmru-workspace-instance (19828b67)
- harden workspace lifecycle and gate oracles (8d634211)
- reap gate descendants before PID exhaustion (b552e0e2)
- Retry transient Assay Git child launch failures (d14c9ddb)
- Bound Assay Git index preload resources (294aaf94)

### Documentation
- docs(assay): close provisional gate evidence (e434b293)
- docs(assay): record B101 wave gate evidence (171c9045)
- docs(assay): fix B101 wave handoff per independent review (4 blocking, 12 major) (a02dcb32)
- docs(assay): B101 isolation wave handoff for a third-party controller (7a3c0571)
- docs(assay): fix independent-reviewer findings in backlog audit (B047, B089/B080, B092, frontmatter titles, ID-collision mechanism, dates, B104) (a127067e)
- docs(assay): B101 P1 review nits -- stale re-resolution prose, R3 test scope (260055ee)
- docs(assay): preserve assay-b096 review records and gate-repair briefs (6bd0ed51)
- docs(assay): fix B096-B099 release date/version citations in audit (080bdb2c)
- docs(assay): correct assay-b096 WIP finding using the B101 triage (baaf780c)
- docs(assay): B101 shallow-seed explanation and assay-b096 triage (f5e702bc)
- docs(assay): audit checkpoint — continuation brief for B001-B087 (390e651b)
- docs(assay): audit — WIP-branch findings and ID collisions (5aeb0f84)
- docs(assay): begin backlog audit report (93d64b29)
- docs(backlog): B089 -- two more reproductions of the istanbul branch-arc contradiction (e7fbb50f)

### Testing
- test: close CMRU mutation survivors and Git spawn retries (67c45c8d)

### Added (hand-written release detail)
- feat(assay): make P22 seeds shallow by default, add an explicit full-history
  lane opt-in, project-level snapshot limits, and a judged-tree blob ceiling
  (B101)
- feat(assay): record snapshot dirty-path provenance, support declared
  `dirty_ignore` globs and the snapshot-only `--allow-dirty` override, and
  refuse release receipts for overridden verdicts (B102)
- feat(assay): keep higher-rigor liveness side files outside the checkout and
  retain their bounded evidence in the verdict (B093)
- feat(assay): distinguish ingested compile and runtime discards with the
  `discard_reason` vocabulary (B079)

### Documentation (hand-written release detail)
- docs(assay): document shallow source/seed distinctions, snapshot limits,
  Go lane-file cleanliness, source unshallowing, measured Go image support,
  and the current dstdns assay pin (B082-B084/B101)
- docs(assay): document verdict schema v12, dirty-tree provenance, liveness
  cleanup, and ingested discard reasons (B079/B093/B102)

### Fixed (hand-written release detail)
- fix(assay): refresh the W3 dstdns SQL witness for the shipped v11 liveness
  fields and normalize its per-run derived candidate budget (B104)
- fix(assay): base checks inside P22 snapshots and `assay plan` diffs on the
  base resolved before snapshot creation (B101 P1, port of `assay-b096`
  `84baffb4`); `judgment.resolved.base`, `BASE_IS_HEAD`, and the merge-HEAD
  first-parent rule retain their existing behavior
- fix(assay): include the scenario artifact and bounded assay/pytest output
  when a P25 qualification scenario's terminal mismatches; other mismatch and
  missing-artifact errors keep their prior evidence behavior

### Testing (hand-written release detail)
- test(assay): cover history-cut snapshots with real Git for R1, a merge HEAD's
  first-parent rule, R2 without R1, and the R3 canary control; the transformed
  R3 half remains outside this fixture's scope

## [6.5.0] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=55473fd56a77ba8f4a209f64bdd207ac82366641 -->

### Added
- feat: add estate cli version compatibility (05f373a4)

### Fixed
- fix: preserve nested gate stack governance (eb9a0a59)

### Changed
- Merge branch 'feat/estate-cli-version-20260919' (0795ebb9)
- cli: universalize vbpub parser diagnostics (aa0e69fa)
- chore: land run-gate root and dev-gates migration (41c1cafb)

### Documentation
- docs(assay): add bounded gate report backlog item (e225d705)

## [6.4.0] - 2026-09-17
<!-- cmru: generated -->
<!-- cmru: source-end=8512408c83c37759bb317d5023c94e554f708013 -->

### Added
- feat(skills): add canonical single-tool skills for ciu, run-gate, assay, cmru, cgprofile (33c0b0c2)
- feat(assay): consume captured gate failures without replaying tests (d3bacd31)
- feat(assay): share review evidence capture and analysis (fde9527a)

### Fixed
- fix(skills): repair defects found by independent review of new canonical skills (abcb6e27)
- fix(assay): preserve verdicts for resume decoder resource errors (a84581fc)
- fix(assay): read ignore provenance as bounded NUL-delimited Git records (0320a160)
- fix(assay): decode quoted Git ignore origins with shared path parser (3a7f6f3e)
- fix(assay): keep receipt argv schema consistent and refuse special files (da0c3a58)
- fix(assay): refuse deeply nested analysis JSON and expose captured failures (b48e873d)
- fix(assay): refuse non-object mutation resume records (12e061b1)

### Changed
- Merge accepted Assay RG-49 B9 provider repair (fb24a852)

### Documentation
- docs(assay): record completed review evidence tooling qualification (0ae3c56c)
- docs(assay): record evidence extraction and blocked final gate admission (9cb98651)

### Testing
- test(assay): retire guarded resume decoder exemption (e6ac4730)
- test(assay): invoke shipped CLI and use valid capture metadata (f55bfba8)
- test(assay): qualify failure and refusal oracles across gate interpreters (25269e30)

### Release detail (hand-written)
- `assay analyze` records commands with before/after Git identities and actual
  job exits; collects and checks portable artifact archives; inspects verdicts
  and appended mutation progress; and produces receipts from these facts and
  `tester-unified/run` evidence. JSON is the default output; verdict inspection
  also offers a concise text summary. No runtime dependencies are added.

### Fixed
- fix(assay): reject non-object JSON mutation resume records as structured
  `ERROR`/`UNREADABLE_ARTIFACT` instead of allowing a `TypeError` traceback
  and a missing verdict (B099, RG-49 review B9)
- fix(assay): preserve that structured refusal when bounded JSON exceeds
  the decoder's integer-conversion or nesting limits (RG-49 review B11)

## [6.3.1] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=f07cc6c55dc784b7b03141b58a331f4dd300947c -->

### Testing
- test(debian-install-v2): harden isolated VM lane (d472d58d)

## [6.3.0] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=51d6dec32d02189eaf098b1380d264e2d120b363 -->

### Added
- feat(assay): filter native R2 resume identity (176a06a5)

### Fixed
- fix(assay): implement B097 per-process liveness identities (1daf6e62)
- fix(assay): B096 derive rejudge outcome help from vocabulary (6f76e471)

### Changed
- refactor(gates): consume assay from selected worktree source (cd4d19b0)
- review(assay): accept RG-55 merged sidecar (e8d7a79a)

### Documentation
- docs(assay): qualify source lane environment (4609bc34)
- docs(assay): record B097 review verification status (0303a24d)
- docs(assay): record B097 review fix verification (aa075851)
- docs(assay): fix B097 brief EOF whitespace (23b75167)
- docs(assay): freeze B097 adversarial review handoff (b83b9941)
- docs(assay): add B096 implementation and review handoffs (ee41553d)
- docs(assay): record B096 verification (de1ef799)
- docs(assay): record B092 B098 adversarial review (49a0e51c)

### Testing
- test(assay): fix B097 regression import and record validation (a8ac0d5c)

### Added
- feat(assay): B092 native-R2 `judge.mutation.identity_exclude` filters
  report-only paths from the tree-content half of `judge_sha256` while
  preserving the omitted-key legacy identity.

### Fixed
- fix(assay): B098 documents every excluded mutation bucket, including
  `crashed`, while preserving the `killed / (killed + survived)` arithmetic.
- fix(assay): B096 derives `--rejudge-outcome` help from the canonical
  `MUTATION_BUCKETS` vocabulary while retaining the CLI-only `error` alias.
- fix(assay): B097 stamps liveness records and parses xdist timelines by
  producer pid, preserving legacy records and owner-only session finishes.

### Documentation
- docs(assay): sync README, DESIGN-GUIDE, and CONSUMERS with B092 adoption
  semantics and B098's canonical mutation-score vocabulary.
- docs(assay): document B096's canonical rejudge bucket source and alias.

### Testing
- test(assay): cover B092 normalization, malformed declarations, native-only
  scope, legacy digest compatibility, filtered-tree directions, and B098's
  canonical bucket enumeration.
- test(assay): prove rejudge help follows a changed owner vocabulary.

## [6.2.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=b54aa1f23ac9f91ddacf66aec02b59cc394603ef -->

### Added
- feat(assay): B091 A5 -- --rejudge/--rejudge-outcome (c15f6040)
- feat(assay): B091 A4 -- progress stream gains test events, slowest_test_s/expect_next_event_within_s, tests_completed (5baf2670)
- feat(assay): B091 A3 -- active LivenessRunner monitoring loop, hung bucket (44dd12ca)
- feat(assay): B091 A2 -- materialized pytest liveness plugin + os._exit candidate wrapper (D-23/RW-33) (f4fa1788)
- feat(assay): B091 A1 -- budget_per_candidate = "auto" default (D-23) (de32bb91)

### Fixed
- fix(assay): guard xdist session-finish liveness grace (cd1f84fe)
- fix(assay): B091 P7 round-1 S-item fold-in (S2, S4, S7, S8, S9, S10) (ef247935)
- fix(assay): B091 P7 round-1 B2 -- calibrate the idle bound on observed gaps (07e121d9)
- fix(assay): B091 P7 round-1 B3 -- tests_completed reads the RESOLVED run cwd (5c1b9ef8)
- fix(assay): B091 P7 round-1 B1 -- os._exit sentinel prevents false SURVIVOR (802f0855)
- fix(assay/tests): B091 P7 gate finding -- test_standalone.py's real-wheel expected-artifact drift (3 R2 tests never exercised by the deferred sweep) (ee24ced6)
- fix(assay): B091 P7 gate finding -- pyflakes unused imports + RecursionError guard on liveness.py's two untrusted JSON parse sites (95d02f50)
- fix(assay): B091 A6 gate finding -- W7's locked v11 schema copy drifted from the shipped schema (b3f31506)
- fix(assay): B091 A3 remainder -- real e2e liveness fixture tests + a real plugin JSON bug found by them (99463ae5)
- fix(assay): B091 RW-36 -- liveness via argv_appended + judge.mutation.liveness gate (e27b107b)

### Documentation
- docs(assay): B091 P7 round-1 B4 + B5 -- disclose the v11 same-number break, restructure CHANGES (4ef3985f)
- docs(assay): B091 A6 -- CHANGES/CONSUMERS/README + backlog close-out (A1-A5) (afda58fd)

### Testing
- test(assay): B091 A3 -- >=3 planted-mutant table for the monitoring loop (BRIEF-3/4's own ask) (d1540eda)
- test(assay): fix a stray leftover assertion in the A1 diagnostics=None regression test (f649a249)

## [6.1.2] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=ebc26e83da5d2d55b619890eca7a33e60b8e81db -->

### Fixed
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 6.1.0 -> 6.1.1 (assay-v6.1.1) (dd8b07f1)

### Changed
- design(rg55): liveness/placement/admission design of record (D-17..D-26) + RW-29 -- P7 assay B091, P8 mdt slices handoffs, SPEC-V8 D.7 (11ac5d67)
- backlog(assay): B090 -- budget_per_candidate has no default; a hung mutant blocks the whole R2 run (seen live in the RG-55 wave) (ce27b14a)

### Documentation
- docs(assay): B089 -- istanbul branch-arc self-contradiction on some .tsx files (2659af3a)

## [6.1.1] - 2026-09-11
<!-- cmru: generated -->
<!-- cmru: source-end=53d841a84ae16545025921ff126ade7cc167dbe8 -->

### Fixed (detail)

- **`--resume` replayed a stale `survived` verdict after a test-only fix
  (B088).** A persisted candidate record's identity was computed from the
  mutant alone — path, source bytes, byte span, replacement, operator — and
  from nothing that JUDGES the mutant. A mutant's source bytes are the same
  bytes whether the suite about to run against them just gained the assertion
  that kills them or not, so adding a test (zero bytes of the mutated source
  touched) left the candidate id bit-identical and `--resume` replayed the old
  `survived` instead of re-executing: a real fix landing, and a mutation gate
  staying red — or, worse, a later run staying green — on a cached verdict the
  current suite never produced. Measured twice in one week, in two
  repositories (dstdns `worker-execution-admission-r2-flips`, 2026-09-09; this
  repository's own nyxloom `session-extract` lane, 2026-09-10), each time
  fixed only by deleting the state directory by hand.

  A record now also carries `judge_sha256`: a digest of the **content of the
  judged commit's tree** (every leaf's path, mode and Git object id, plus the
  declared `unsafe_symlink_omissions` — so a changed `conftest.py`, fixture or
  helper counts, and a touched-but-unchanged file does not) together with the
  **resolved argv, the lane's declared `env` by value, the NAMES of whatever
  else the resolved environment carried, cwd, the project prefix, the declared
  `link_paths`, and assay's own version**. Passthrough and `infrastructure`
  values are folded by name and never by value: they are per-invocation by
  design (a worktree's own host path, a per-instance DSN, `TERM`), and folding
  them by value would make resume impossible across exactly the
  ephemeral-checkout case `--state-dir` was built for. A mismatch — and the
  absence of the field, which is what a pre-B088 record looks like — is a cache
  miss: the candidate is silently re-executed, never a lane failure, following
  B021's own disposition for the other field not folded into the candidate id.
  The check runs *after* every identity-vs-filename check, so a routine test
  edit can never launder a hand-edited state file into a silent rerun.
  `MUTATION_STATE_SCHEMA_VERSION` is deliberately **not** bumped: the field is
  additive with a safe absence, and that one constant is also the shard-summary
  document's version, which `merge_mutation_shards` refuses outright on any
  other value — assay's own version is folded into `judge_sha256` instead, so
  an upgrade that changes how a result is classified still invalidates records.

  Resume is now per-**tree**, not per-commit: identical trees at different
  commits still resume each other, and a commit that touched any file in the
  judged tree re-executes every candidate. The uses `--state-dir` exists for —
  several worktrees of one commit, budget-capped retries, `--shard` fan-out —
  judge the same tree with the same command and keep resuming, including when
  a per-instance passthrough value differs between the runs.

- **`--resume` said nothing when it resumed nothing (B088, round-1 review).**
  The `resume` progress event fired only on a successful resume, so a store
  whose every record was refused emitted no event at all and was
  indistinguishable from an empty one — a permanently cold cache with no
  symptom anywhere in the progress stream or the verdict. The event now also
  fires when records were REFUSED, and carries `rejected_total` alongside
  `resumed_total`.

- **`budget = "unbounded"` was voidable by declaring R3.** Both arms of the
  refusal were conditioned on the *absence* of R3, so an R0/R1+R3 lane — and
  an ingested-R2+R3 lane, whose entire R2 evidence is that one command — was
  admitted and ran its own evidence-producing command with no bound at all,
  which is the exact state the refusal's message calls impossible. The
  predicate is now about the lane's own top-level command and is independent
  of which other tiers are declared. Found by adversarial review.

- **The `--state-dir` containment check failed open across a symlink.** The
  "inside the judged tree" test compared a lexically-normalised destination
  against a fully-resolved project root; when the two disagreed the path was
  silently classified "outside the tree" and resume records were written into
  the real work tree, leaving it dirty. Both namespaces are now checked, in
  both directions, and any of them saying "inside" refuses. Found by
  adversarial review.

### Fixed
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 6.0.0 -> 6.1.0 (assay-v6.1.0) (ec868f14)

### Changed
- mutation: fold the round-1 review's findings into B088's judge identity (fd50183e)
- backlog(assay): B088 -- FIXED, with three residuals recorded (dd62d88b)
- mutation: resume identity folds in the judging suite (B088) (fd08df8f)
- README: document the judge.base origin-drift pitfall (B019 follow-up) (734a6763)
- backlog(assay): B088 -- second independent hit in vbpub nyxloom session-extract lane (e5455b2b)
- backlog(assay): B086+B087 -- Go mutation (R2) and JS canary (R3) registration; B073 corroborated live (0865b2e6)
- backlog(assay): B080 addendum -- predicted latent tripwire fired live (dstdns D-433) (568d866b)

### Documentation
- docs(assay): B088 -- --resume's candidate identity omits the judging test suite, replaying a stale verdict after a test-only fix (2abcb2a3)
- docs(assay): canonical rigor-level explanation + language support matrix (509c3b89)

## [6.1.0] - 2026-09-09
<!-- cmru: generated -->
<!-- cmru: source-end=e1ab92ddc3006e6383a299620c7b44d06c3c9d22 -->

### Added
- feat(assay): B074 reaches R2's declared-target gate, and gains the lane-level tests that defend it (15258dfc)
- feat(assay): B074 -- judge.allow_test_path_targets lets a DECLARED whole-target entry name library code under tests/ (991ede05)
- feat(assay): B078 -- the [lanes.X.result_report] opt-in declaration (5b2cecea)
- feat(assay): B078 -- result-report readers and the completeness core (60b83a0c)

### Fixed
- fix(assay): B078 -- exclude canary halves, sweep every execution site (blockers 2, 3) (f535f04e)
- fix(assay): B078 -- wire the R0 command of every R1+ lane (review blocker 1) (388f23a2)
- fix(assay): move the locked v11 schema asset with the shipped one (B074) (427157c1)
- fix(assay): B078 -- guard the vitest reader's parse, repin the mutation target (c80d94d4)
- fix(assay): B077 -- name the symlink instead of passing through git's raw pathspec fatal (dc944932)
- fix(cmru,ciu,nyxloom): re-pin assay gate zipapps 5.2.0 -> 6.0.0 (assay-v6.0.0) (ec0bc47f)
- fix(assay): B078 -- R0 judges a verified-complete report, not the exit code (6073749c)

### Changed
- Merge assay B074+B077 -- whole-target test-path opt-out (extended to R2), named symlink refusal (e4573cf3)
- Merge assay B078 checkpoint 1 -- R0 judges a verified-complete test report, not the exit code (8998cfb2)
- backlog(assay): B081-B084 -- dubious-ownership GIT_FAILED proposes an impossible fix, plus three Go-consumer documentation gaps (wings-cgroups-filed) (57d52972)

### Documentation
- docs(assay): tick B074+B077 -- fix-verification ACCEPT (0 blockers), close-out gate GREEN (72b44944)
- docs(assay/backlog): B085 -- judge.canary.target's own test-path veto is untouched by B074's opt-out (c3d982cf)
- docs(assay): B074+B077 round-2 fix verification -- ACCEPT (337b5c7f)
- docs(assay): tick B078 Checkpoint 1 -- fix-verification ACCEPT, close-out gate GREEN (c997d4f3)
- docs(assay): B074+B077 fix round landed + gate re-verified, fix-verification dispatched (bbfdcee9)
- docs(assay): B074+B077 LOG/REPORT revised for the fix round -- gate GREEN at 15258dfc (bdbb8e91)
- docs(assay): B078 fix-verification ACCEPT-conditional, one-line test close-out dispatched (c475cfa3)
- docs(assay): B078 -- fix-verification round 2 (ACCEPT-conditional, 1 test-only condition) (ad90bdc4)
- docs(assay): B078 fix round landed + gate re-verified, fix-verification dispatched (e281a7fe)
- docs(assay): B078 -- LOG/REPORT for the round-1 repair (8735b68d)
- docs(assay): correct 5 stale B074 comments to B075 (RecursionError sweep) (30858058)
- docs(assay): B074+B077 round-1 review ACCEPT-conditional -- extend flag to R2, fix dispatched (d8aaf08b)
- docs(assay): B074+B077 round-1 adversarial review -- ACCEPT-conditional, 3 blockers (12321837)
- docs(assay): B078 -- the verdict never carried returncode (blocker 4, OBS 4/5) (92803df3)
- docs(assay): B078 round-1 review REJECT -- real repro path unwired, rulings made, fix dispatched (0a029f31)
- docs(assay): B078 checkpoint 1 -- adversarial review round 1 (REJECT, 4 blockers) (27b66c25)
- docs(assay): B078 checkpoint 1 landed + gate re-verified, review dispatched (a39f43c3)
- docs(assay): B078 checkpoint 1 -- implementer LOG and acceptance REPORT (b03b0afc)
- docs(assay): B074+B077 implementation landed + gate re-verified, review dispatched (935af5e9)
- docs(assay): B074+B077 wave LOG and REPORT -- gate GREEN at 427157c1 (b3a33415)
- docs(assay): B078 -- document result_report in DESIGN-GUIDE 6 and CONSUMERS (286320db)
- docs(assay): B080 -- default-arg branch on the function-signature line contradicts FileCoverage's executed|missing invariant for a fully- executed file; found via dstdns D-422/D-423 (P176/P177 this session) (865cf70d)

### Testing
- test(assay): B078 -- make the canary-exclusion test actually able to fail (0f18cad7)

## [6.0.0] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=55881596de882df322c078a3db59980b6ee1e90e -->

### Added
- feat(assay): W7 -- the frozen v11 generation, and the gate's own demotion of W6 (B070) (621ae8cc)
- feat(assay): verify gains the FOURTH re-derivation over judgment.r2.discarded (B070) (00cca2f3)
- feat(assay): ingest RECORDS the discarded mutants it used to drop (B070) (0a67eae9)
- feat(assay)!: verdict schema v10 -> v11 -- judgment.r2.discarded becomes a listed, verified field (B070) (4fc13ca2)

### Fixed
- fix(assay): the model re-narrows the sentinel disposition, and carries BLOCKER 1's own half (review BLOCKER 3) (1be233d2)
- fix(assay): the candidate ceiling becomes producer-aware -- B070 was refusing the honest high-discard report (review BLOCKER 1) (504b1453)
- fix(cmru,ciu,nyxloom): re-pin stale assay gate zipapps to 5.2.0, file KI-27 (583faad7)

### Changed
- Merge assay B070 -- verdict schema v10 -> v11, judgment.r2.discarded becomes a listed, verified field (55881596)

### Documentation
- docs(assay): B070 fix-verification round 2 -- ACCEPT, all three blockers closed (2c2342be)
- docs(assay): B070 fix round landed + gate re-verified, fix-verification dispatched (cb3886f5)
- docs(assay): B070 fix-round LOG + REPORT -- gate GREEN at 05df0450 (f23d6420)
- docs(assay): the ingested size bound, the honest limit of "refused by name", and B078 (review BLOCKER 1 + OBS 1/2/3) (05df0450)
- docs(assay): B070 round-1 review logged, 3 blockers ruled, fix dispatched (42d8d756)
- docs(assay): B070 review round 1 -- BLOCKER 3 confirmed on the full suite (d7e53e53)
- docs(assay): Wave 3 (B070) adversarial review round 1 -- ACCEPT-conditional, 3 blockers (b46d0467)
- docs(assay): Wave 3 (B070) LOG + REPORT -- gate GREEN at 6797d4fa (efd3920a)
- docs(assay): the v10 -> v11 migration notes, written not implied (B070) (6797d4fa)
- docs(assay,run-gate): design R0 structured-report tiebreak, file B078, move RG-45's disposition (10a413c1)
- docs(assay): Wave 2 post-release checklist completed + Wave 3 (B070) dispatched (eb93f0b3)
- docs(assay): Wave 3 prompt -- B070 v11 schema cut, list-the-discarded-mutants shape (361cf628)

### Testing
- test(assay): the latent-lie fix and the sentinel disposition now have tests that kill their mutants (review BLOCKERS 2+3) (697088c3)
- test(assay): the local suite for the v11 discarded-mutants cut (B070) (e6920f60)
- test(assay): a SECOND real StrykerJS artifact, with 40 genuine CompileErrors (B070) (aebf7fda)

## [5.2.0] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=763c14b4ee3ae3d5666c3b6fa7d21a0dfd90187e -->

### Added
- feat(assay): --state-dir -- resume state that outlives its worktree (B066) (243de634)
- feat(assay): the progress stream reaches every tier, ticks, and carries time (B064/B065) (940b5ba2)
- feat(assay): budget = "unbounded", admissible only where every unit is bounded (B067) (7f2ba056)

### Fixed
- fix(assay): round-1 review -- B1 blocker, SF-1/5/6, six nits (B067/B064/B065/B066) (b5532895)

### Documentation
- docs(assay/backlog): B077 -- symlink-into-judged-tree destinations fail closed with a raw git passthrough, not a named refusal (6ea26609)
- docs(assay): fix-verification round 2 of the progress/resume wave -- ACCEPT (078e3703)
- docs(assay): LOG/REPORT for round-1 fixes -- gate GREEN from scratch at b5532895 (e1db9655)
- docs(assay): adversarial review round 1 of the progress/resume wave -- NOT ACCEPT (842921ef)
- docs(assay): Wave 2 controller log -- PR-R3, all items landed gate GREEN, review dispatched (9ffd56c7)
- docs(assay): progress/resume wave LOG + REPORT -- all four items, gate GREEN (48561aba)
- docs(assay): Wave 2 controller log -- PR-R2, checkpoint verified, fresh successor dispatched (845f1502)
- docs(assay): E-008 checkpoint after B067 -- continuation brief, gate GREEN at 7f2ba056 (8bc6b9ad)
- docs(assay): Wave 2 (progress/resume) dispatched -- wave prompt + controller log (651b9ffa)

## [5.1.0] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=309bedca062a76a08ae5d626f6604597a6ca58bd -->

### Added
- feat(assay): a crashed mutation candidate's state record keeps the output that explains it (B071) (b12ec9f2)

### Fixed
- fix(assay): redo B072's sweep properly -- it missed 4 sites, 3 of them crashing (review blocker) (93e6f7fc)
- fix(assay): verify_text catches RecursionError too -- the third and last site of one gap (B074) (767393d1)
- fix(assay): the suite skips, honestly, when there is no parent repository to read (B063) (e426c29f)
- fix(assay): sweep tests/ pyflakes-clean and widen the gate's lint phase to cover it (B062) (c2d89888)
- fix(assay): parse_attestation catches RecursionError too (B072); sweep finds a third instance, filed B074 (9cd5ef28)
- fix(assay): a severed linked worktree is NAMED, not passed through as git's fatal (B068) (96973575)
- fix(run-gate): estate-wide sweep of environment="host" lanes broken by RG-43 (f62642c6)

### Changed
- merge(assay): B068 + quick-wins -- linked-worktree gap named, 3 RecursionError sites closed, tests/ pyflakes-clean, honest skips outside a repo, crash diagnostics kept (B068/B072/B062/B063/B071/B074) (309bedca)
- Merge remote-tracking branch 'origin/main' (cafbb8a5)
- Merge remote-tracking branch 'origin/main' into debian-install-update (2161b7d3)
- Merge remote-tracking branch 'origin/main' into debian-install-update (6aa14599)
- chore(assay): sync worktree to origin/main (assay-v2.4.2) (276c1912)

### Documentation
- docs(assay): fix-verification -- ACCEPT, the review blocker is discharged (1a494e83)
- docs(assay): gate re-run GREEN at 001a1f24 after the review-blocker fix (12b4a3ed)
- docs(assay): record the retracted sweep, item 7, and the re-measured B063 numbers (001a1f24)
- docs(assay): adversarial review round 1 -- ACCEPT-conditional, one blocker on B072's sweep record (7ec114c6)
- docs(assay): Wave (B068+quickwins) controller log -- QW-R2/QW-R3, review dispatched (83d5ebda)
- docs(assay): add B074 as wave item 6; gate re-run GREEN at 767393d1 (7bcf089a)
- docs(assay): B068 quick-wins wave -- implementer LOG and REPORT; gate GREEN at b12ec9f2 (95177803)
- docs(backlog): B074 — a whole-target judge cannot grade deployed library code under a tests/ segment (5f17c60b)
- docs(assay): Wave (B068+quickwins) dispatched -- wave prompt + controller log (a78d0280)
- docs(assay/backlog): B073 -- per-language live test progress adapter, filed only (faaa49c2)
- docs(assay/backlog): B072 -- attestation.py has the identical uncaught-RecursionError gap adjudication.py had before f0126b35 (3dd08b12)
- docs(assay/backlog): B071 -- R2 mutation candidate stdout/stderr computed then discarded (9862f96e)
- docs: estate-wide assay live-install policy for in-repo consumers; fix debian-install-v2 README link (44664150)

## [5.0.0] - 2026-09-03
<!-- cmru: generated -->
<!-- cmru: source-end=d761838df269c6ce8d8bb2bb9d1ee12c963e3065 -->

### Added
- feat(assay): B004, the whole carve -- adjudicated image provenance (A-442) (d9fc22eb)
- feat(assay): a canary declares SEVERAL probes, in order, with a stated aggregation (B007, A-440) (d30b313b)
- feat(assay): a refusal's own sentence reaches the wire -- claim.detail's producers (B053, A-439) (d0e212e2)
- feat(assay): non-repudiation tier three -- an ingested report's source must be the commit's own bytes (B052, A-438) (83c31f18)
- feat(assay): judgment.r2.fail_under becomes a floor that is TAKEN (B050, A-436) (962211cd)
- feat(assay)!: verdict schema v9 -> v10 -- the integrity cut (B050/B053/B004/B007/F015) (b2fd09f3)
- feat(assay): the gate's own assay run carries --resume --progress (A-429); phase-2 design rows A-427/A-428; file B064 (f254b702)

### Fixed
- fix(assay): adjudication.py's json.loads catches RecursionError too (R-2 round-2 finding) (f0126b35)
- fix(assay): verify.py checks the budget_exhausted bookkeeping member (R-2 SF-1) (78a786fc)
- fix(assay): the last silent terminal in runner.py announces (DA-R15/SF-6, A-426) (b69a9248)
- fix(assay): bound the LANE_TIMEOUT commit-label read by a documented grace (DA-R13, A-425) (ba2f1133)
- fix(assay): R-1 round 1's fix package -- both blockers and all five should-fixes (A-418..A-424) (8895ffbf)
- fix(assay): the registered gate lints its own source, from its own hash-bound closure (B024, A-417) (7c9e8dd1)
- fix(assay): the canary side-run resolves infrastructure; B029's premise corrected by measurement (B029, A-416) (81228b25)
- fix(assay): a lane that runs out of time writes its verdict, and a timeout is never called GIT_FAILED (B028, A-415) (dd8f4d2c)
- fix(assay): the last six silent refusals speak, and a superseded one stops (B053, A-414) (21bdf19d)
- fix(assay): the release builder writes nothing outside --outdir; three rulings recorded (B060, B056, B055, B009) (c80b3452)
- fix(assay): a self-contradictory istanbul branchMap is one file's defect, not the verdict's (B054, A-410) (c37ca3fb)
- fix(assay): every refusal says WHY, once, through one emitter (B053 a+b, A-409) (440d5da9)
- fix(assay): a replaced output directory is named, not read as EMPTY_COVERAGE (B049, A-408) (3b2b8e62)

### Changed
- merge(assay): Wave D -- verdict schema v9 -> v10, image-provenance adjudicator (B004); 5.0.0 (d761838d)
- backlog(assay): B068 -- R0/R1 lane GIT_FAILED in a linked-worktree container whose main .git is not mounted (dstdns-filed); wave plan carries it (f6d3a858)
- backlog(run-gate,assay): the progress/re-attach/unbounded-budget pattern as estate default -- RG-35..RG-37, B065..B067 (b57b2d12)
- backlog(assay): B063 -- three test modules git -C PROJECT_ROOT.parent, so the suite cannot run from a copy (e3ae8ada)
- backlog(assay): B024's gate wiring is blocked -- measured, nothing landed (DA-D15) (93188912)

### Documentation
- docs(assay): Wave D R-2 round 2 -- ACCEPT, RecursionError blocker found+fixed (f0126b35) (cfc2ec2c)
- docs(assay): generation 13 verified -- B004 complete, Wave D implementation DONE, DA-R34, R-2 round 2 dispatched (23fe2c98)
- docs(assay): Wave D generation 13 -- B004 complete, gate GREEN on `d9fc22eb` (0c6863ff)
- docs(assay): correct DA-R33 routing -- SF-1 landed by R-2 itself (78a786fc), N-1 append-only via A-443, real generation 13 now messaged (b8ea728a)
- docs(assay): R-2 round 1 ACCEPT-conditional -- DA-R32 (SF-1) / DA-R33 (N-1), routed into generation 13 (4969bbc6)
- docs(assay): Wave D R-2 round 1 review -- ACCEPT-conditional, SF-1 filed (2128e464)
- docs(assay): generation 12 returned -- B007 + migration notes gate-green, DA-R30/DA-R31 ruled (2cadd57a)
- docs(assay): Wave D generation 12 checkpoint -- B007 + migration notes gate-green, BRIEF-12 (a4528144)
- docs(assay): the v9 -> v10 migration notes, and the anchors that now resolve (A-441) (fd489620)
- docs(assay): Wave D generation 11 checkpointed at 97907425 -- B053 verified green; DA-R28 reorders B007 -> migration notes -> B004; generation 12 dispatched (aa3b7b8e)
- docs(assay): Wave D generation 11 checkpoint -- B053 producers gate-green, BRIEF-11 (97907425)
- docs(assay,run-gate): limits reset -- three implementers dispatched in parallel (assay gen 11, run-gate fix successor, E-5 Buildkite seams) (c4cdd5e9)
- docs(assay): Wave D generation 10 checkpointed at 9de276bd -- B051 + B052 verified gate-green; no successor dispatched (session limit) (8e0425f1)
- docs(assay): Wave D generation 10 checkpoint -- B051 + B052 gate-green, BRIEF-10 (9de276bd)
- docs(run-gate): LANE-AUTHORING.md + REMOTE-LANES-BUILDKITE.md -- sibling guides to CONSUMERS; E-5 (remote/async lanes) recorded in the post-v10 wave plan (780f9a98)
- docs(assay): judgment.r2.discarded is DECLARED, NOT VERIFIED (B051, A-437) (5b2730b6)
- docs(assay): Wave D generation 9 returned -- B069 + B050 verified green, B051 blocked; DA-R25..R27; generation 10 dispatched (850a45fe)
- docs(assay): Wave D generation 9 checkpoint -- B069 + B050 gate-green, B051 BLOCKED, BRIEF-9 (1b127356)
- docs(assay): Wave D generation 8 returned -- the v10 cut b2fd09f3 verified green; DA-R22..R24; generation 9 dispatched (9d664b3a)
- docs(assay): Wave D generation 8 checkpoint -- the v10 cut is green, BRIEF-8 (af1df91d)
- docs(run-gate,assay): run-gate resumable-gate wave dispatched (E-1: RG-35/36/32/34 -> 23.4.0); operator rulings D1-D7 accepted, F015 leaves Wave D (DA-R21) (dc6e88c4)
- docs(run-gate,assay): RG-37/RG-38 id swap -- resume-state durability is RG-38, RG-37 is the ciu v8 session's container-derivation row (05e123d3)
- docs(assay,run-gate): wave plan after v10 -- the progress/re-attach/unbounded-budget pattern folded in, sequencing E-1..E-4, decisions D1..D7 (8f2f939f)
- docs(assay): A-434 -- DA-R18 amends A-433, RED_FIRST_UNPROVEN is a judged FAIL (4538bd66)
- docs(assay): Wave D controller log -- generation 7 verified (design complete, B007 measured), DA-R18..DA-R20, generation 8 dispatched for the v10 cut (48c48599)
- docs(assay): Wave D generation 7 checkpoint -- A-430..A-433, B007 measured, BRIEF-7 (0016d6cf)
- docs(assay): the last three phase-2 design rows -- B004, B007 (measured), F015/R4 (A-430..A-433) (26b38cc4)
- docs(assay): Wave D controller log -- generation 6 verified (gate-green on bfb55e3f), DA-R16 F015 = R4, DA-R17 B007 measured first, generation 7 dispatched (6917423d)
- docs(assay): Wave D generation 6 checkpoint -- A-425/A-426/A-429 gate-green on bfb55e3f, BRIEF-6 (ed287d73)
- docs(assay): Wave D controller log -- operator directive on resume/progress handled estate-wide, two items routed to the branch (13df60ec)
- docs(assay): Wave D controller log -- R-1 round 2 ACCEPT-conditional on e3ae8ada, DA-R15 (SF-6 rides with A-425) (72eabede)
- docs(assay): Wave D controller log -- generation 5 verified (fix package gate-green on e3ae8ada), DA-R13/DA-R14, R-1 round 2, generation 6 (4f2ca8e7)
- docs(assay): Wave D generation 5 checkpoint -- R-1's fix package landed and gate-green on e3ae8ada, BRIEF-5 (fb8d03f5)
- docs(assay): R-1 round 1's FINAL report, verbatim (839 lines, superseding the 756-line interim) (e44c1056)
- docs(assay): Wave D controller log -- generation 4 verified (B024 gate-green on 7c9e8dd1), DA-R12, generation 5 dispatched (c35baa9e)
- docs(assay): Wave D generation 4 checkpoint -- B024 landed and gate-green on 7c9e8dd1, R-1 round 1 filed, ciu assets re-captured, BRIEF-4 (efbab2bb)
- docs(assay): Wave D controller log -- R-1 round 1 NOT ACCEPT, rulings DA-R8..DA-R11, fix package routed (c5692c4c)
- docs(assay): Wave D controller log -- second escalation, paused probes killed to stop swap-out (c23a4558)
- docs(assay): Wave D controller log -- load incident escalation, reviewer probes serialized (2268cae4)
- docs(assay): Wave D -- host-load incident recorded, serial-test/one-gate rule added to the wave prompt (92af352f)
- docs(assay): Wave D controller log -- phase 1 complete (gate PASS on 93188912), DA-R7 ruled (B024 lint closure), R-1 and generation 4 dispatched (72bc041f)
- docs(assay): Wave D generation 3 checkpoint -- phase 1 complete, BRIEF-3, green gate on 93188912 (b90ca598)
- docs(assay): Wave D controller log -- generation 2 verified (7/10 phase-1 items, gate PASS on c80b3452), DA-R3..DA-R6 ruled, generation 3 dispatched (ba741c3b)
- docs(assay): Wave D generation 2 checkpoint -- BRIEF-2 and the green gate on c80b3452 (10d9390d)
- docs(assay): Wave D controller log -- generation 1 verified (B049/A-408, gate PASS on 299d18a0), DA-R1/DA-R2 ruled, generation 2 dispatched (602e1930)
- docs(assay): record the green registered gate on 299d18a0 (36ac802c)
- docs(assay): Wave D generation 1 checkpoint -- BRIEF-1 and the gate log (299d18a0)
- docs(assay): Wave D dispatched -- wave prompt (16 rulings, 3 phases, one v10 cut) and controller log (a4a865da)
- docs(assay): Wave C closed -- controller log final entry, DA-R3 note on B053, srdm P14 note (0556d309)

### Testing
- test(assay): a local tripwire for the gate harnesses' contract pins (B069, A-435) (61b8d836)
- test(assay): the gate's assay stubs read --verdict-json from argv, not $5 (A-429 follow-up) (bfb55e3f)

## [4.1.0] - 2026-09-02
<!-- cmru: generated -->
<!-- cmru: source-end=1e80268181516b9a6ad83a63b8e71a5f9908c3c5 -->

### Added
- feat(assay): derive a Go lane's module path from its own go.mod (B057, A-404) (4b5e7707)
- feat(assay): helpers[] gets its first producer, and its real-toolchain proof (B047 item 5) (77d9d6b9)
- feat(assay): register the Go adapter at R1, and open the go-cover producer vocabulary (367bbdf5)
- feat(assay): wire the Go statement-attribution chain end to end (c85c703a)
- feat(assay): declare the Go helper as package data, and retract my own blocker claim (428f69e2)
- feat(assay): keep Go block extents and correct them (A-239 items 1+3) (fe9aaf7c)
- feat(assay): the Go statement-position oracle (B047 item 1, A-217) (271af037)

### Fixed
- fix(assay): A-407 -- drop an orphaned helper where the payload went (0fb6fb94)
- fix(assay): BLOCKER 1 (A-405) and DA-R1 (A-406), with should-fixes 4, 5, 6, 7 (4c3e83f4)
- fix(assay): should-fix 1 -- restore a CLI test of the unknown-language branch (bdbb2557)
- fix(assay): should-fix 2 -- three go.mod divergences from the real parser (7cda9d11)
- fix(assay): BLOCKER 2 -- registry.py's two stale docstring facts (4c876306)
- fix(assay): B061 -- the statement join kept only the LAST record for a repeated block, so covered code reported as uncovered (875382d2)
- fix(assay): F008-A4 -- the Go coverage fixtures are real toolchain output, and their expectations are the oracle's (394c6cc2)
- fix(assay): reach the Go oracle from the shipped zipapp (A-403) (8d7f8740)

### Changed
- merge(assay): Wave C -- Go at R1, statement-granular, through the shipped zipapp (1e802681)
- chore(assay): renumber Wave C backlog ids B053-B058 -> B055-B060 (main's B053/B054 landed first) (e7eb5241)
- backlog(assay): file B053 (ERROR verdict messages never surfaced) + B054 (never-executed file's istanbul quirk refuses the whole verdict) (a050a467)

### Documentation
- docs(assay): Wave C review round 3 -- reviewer's report, verbatim (5091a413)
- docs(assay): Wave C controller log -- generation 8 verified (gate run 12 PASS on 99d2a443), round 3 sent to the reviewer (7288b3dc)
- docs(assay): correct this generation's own claim about its mutation probes (4889b742)
- docs(assay): Wave C generation 8 -- gate run 12 PASS on 99d2a443 (e1d8128f)
- docs(assay): Wave C generation 8 -- REPORT sections 50-53 (494c8ee2)
- docs(assay): SF-R2-3 -- CONSUMERS' Go refusals say what a consumer sees (74c64858)
- docs(assay): Wave C review round 2 -- reviewer's report, verbatim (71a59967)
- docs(assay): Wave C controller log -- review round 2 NOT ACCEPT (one pre-existing blocker, fix proven), DA-R3 ruled, final fix round dispatched (12e028c7)
- docs(assay): Wave C controller log -- fix generation verified (gate run 11), reviewer resumed for round 2 (d71e0a0e)
- docs(assay): Wave C generation 7 -- gate run 11 PASS on 4c3e83f4 (1d464fc4)
- docs(assay): Wave C review round 1 -- reviewer's report, verbatim (210812f6)
- docs(assay): Wave C controller log -- review round 1 ACCEPT-conditional, DA-R1/DA-R2 ruled, fix generation dispatched (5b6f77cc)
- docs(assay): Wave C controller log -- generation 6 verified, scope complete (F008 shipped, M6 done), reviewer dispatched (a74bc6f6)
- docs(assay): Wave C generation 6 -- BRIEF-7, gate run 10 PASS on 3355d238 (d938ab8c)
- docs(assay): F008-A5 -- the srdm qualification ran; F008 is shipped, M6 is done (3355d238)
- docs(assay): Wave C controller log -- generation 5 verified (A-404 landed, F008-A3 proven), DA-9 lane shape, generation 6 dispatched (53eba55b)
- docs(assay): Wave C generation 5 -- gate run 9 verdict for dd1e2c46 (86b4efae)
- docs(assay): fold in main's B053, tick F008-A3, and BRIEF-6 (dd1e2c46)
- docs(assay,srdm): B053 corroboration note, Wave C id-collision ruling, srdm pointer corrected (173eda68)
- docs(assay): Wave C generation 5 -- A-404's record, B057 closed, B058 filed (1885d64e)
- docs(assay): Wave C controller log -- generation 4 checkpoint verified, DA-8 ruled (derive the Go module path from go.mod) (3a95459e)
- docs(assay): Wave C generation 4 checkpoint -- BRIEF-5, and the gate verdict for 9714361c (524dd16c)
- docs(assay): file B057 and decision ask DA-8 -- a CLI Go lane cannot resolve its own coverage keys (854d20c3)
- docs(assay): reword F008-A5, and record the in-image harness ruling (A-401, A-402) (2f0cd223)
- docs(assay,srdm): Wave C controller log -- 2026-09-01 assessment, DA-4..DA-7, generation 4 rulings (237b9585)
- docs(assay): Wave C checkpoint 4 -- gate verdict for 91b05186 (4cff97bc)
- docs(assay): Wave C controller log -- session checkpoint, gate green on 91b05186 (ce08d077)
- docs(assay): Wave C controller log -- goal cleared, closing out the last in-flight gate (4ad164da)
- docs(assay): Wave C generation 3 continuation brief (BRIEF-4) (91b05186)
- docs(assay): Wave C controller log -- checkpoint 3, gate green, DA-3 resolved (a259bc98)
- docs(assay): Wave C checkpoint 3 -- gate verdict for c85c703a (4a326ddc)
- docs(assay): Wave C controller log -- generation 2 implementer dispatched (055115ba)
- docs(assay): Wave C controller log -- checkpoint 2, dispatching fresh successor (ddbcf9af)
- docs(assay): Wave C checkpoint 2 -- gate verdict for 428f69e2 (335636b4)
- docs(assay): Wave C controller log -- gate PASS, self-correcting the packaging call (a80832c0)
- docs(assay): Wave C controller log -- checkpoint 1, both decision asks resolved (8fd9dd68)
- docs(assay): Wave C checkpoint 1 -- LOG/REPORT/BRIEF, and file B053 (4408622b)
- docs(assay): Wave C controller log -- created, implementer dispatched (205b0cd2)
- docs(assay): Wave C dispatch plan -- the P27 re-carve, scoped to F008-A3/A4/A5 (25b1f7fb)
- docs(assay): Wave B controller log -- Wave C pre-dispatch research dispatched (106aea3a)
- docs(assay): Wave B controller log -- release published, deployed, dstdns notified (97d4b409)

### Testing
- test(assay): A-407's control -- a judged R1 lane KEEPS its helper (99d2a443)
- test(assay): SF-R2-1/SF-R2-2 -- kill the two surviving A-405 mutants (ba09eb61)
- test(assay): the Go qualification driver resolves judge provenance, as cmd_run does (9714361c)

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
