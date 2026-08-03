# nyxloom core redesign implementation plan — amendment

Date: 2026-08-02
Parent: [`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02.md`](CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02.md)
Source assessment: [`DEEP-REVIEW-2026-08-02-AMENDMENT.md`](DEEP-REVIEW-2026-08-02-AMENDMENT.md)

Status: operator-approved; implementation in progress

## Implementation progress

This section is the live program ledger. Update it whenever program changes land
on `main`; architecture text below remains the contract. A package is `done`
only after implementation, an independent capable review-and-fix pass, and the
authoritative `tester-unified` gate. Commit IDs and gate evidence are recorded
here so external reviewers can distinguish planned work from shipped work.

Last updated: 2026-08-03

| Item | State | Evidence / notes |
| --- | --- | --- |
| Program preparation | in progress | `e9bf702f` adds a package-scoped exception to the obsolete frozen-file list; it does not generally unfreeze core files. |
| CR-00 | done | Sonnet implementation `4c995686`; independent Opus review-and-fix `8bdf283f`; authoritative `tester-unified` parallel suite completed at 100% with exit 0 and the coverage gate accepted 0 changed executable production lines (the package changes tests/docs only); merged to `main` as `5a9d441d`. |
| CR-15 | done | Opus implementation `b0bc7dfb`; independent Opus security review-and-fix `3aa1ea21` (closed the ntfy feedback mutation ingress and the credential-store/HTTP-framing races); coverage-rejection repair `ef6e1bc7`. The authoritative gate rejected the first attempt at 96.7% changed-line coverage (3 lines behind `pragma: no cover`, 12 unexecuted); both classes were answered by deleting genuinely unreachable code and testing the real failure modes, never by widening the gate. Final `tester-unified` run on `ef6e1bc7`: `diff-coverage OK: 360/360 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`. Merged to `main` as `7afc897e`. |
| CR-01 | done | Sonnet implementation (`6295095e`, rebased as `b9f98696`); independent Opus review-and-fix `03fcf0a5` (replaced a tautological interpreter-discrimination test that could not fail) and `a8953911`. The authoritative gate rejected the package at 178/190 changed executable lines (93.7%); every uncovered line was the unavailable-source half of a fact reader, unreachable from the real repo and therefore untested while the registry looked covered. Those oracles were written rather than excluded. Final `tester-unified` run on `a8953911`: `diff-coverage OK: 190/190 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`. Merged to `main` as `36f24685`. |
| CR-02a | done | Opus implementation `ae527004` (typed snapshot descriptors, the authoritative fan-in, `SNAPSHOT_UNAVAILABLE`/`SNAPSHOT_DEGRADED`, and the fault matrix driven through the real `run_pass`); independent Opus review-and-fix `1d5ac095`. The gate rejected the package at 433/445 (97.3%) plus one excluded line; the exclusion was a bare `...` line inside a docstring example, which coverage.py's textual exclusion pass matched, silently opting the whole docstring out. Review also deleted an unreachable second `permits_effects` guard (replaced by a structural oracle that fails if any acquisition below the guard is classified authoritative) and a `merge_audits` helper with no production caller. Final `tester-unified` run on `1d5ac095`: `diff-coverage OK: 443/443 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`. Merged to `main` as `104e9681`. |
| CR-02b | done | Opus implementation `1be7f7ea`. Closes CR-02's remaining acceptance. A closed four-class vocabulary (authority-bearing/fail-closed, process-boundary translation, advisory-degradation, cleanup/containment) declared per handler on its own `except` line; the fan-in is DERIVED structurally (a `builder` parameter or a snapshot type by name), so a new acquisition site joins the rule when it is written rather than when someone remembers to register it. All 11 fan-in handlers are classified; the remaining 100 across 16 modules are a per-module budget with an owning package, failing both over (new debt) and under (debt repaid without recording it). Per module rather than per handler so CR-05..07's move of `daemon.py` lowers a number instead of rewriting a registry. `tester-unified` on `1be7f7ea`: `diff-coverage OK: 88/88 changed executable lines covered (100.0%)`, `GATE_EXIT=0`. Merged to `main` as `a7fbbd1c`. |
| CR-03 | done | Opus, two reviewable commits as the readiness audit required: the versioned discriminated envelope `615f13a1`, then the decision-site migration `d7499b94`. The merge gate no longer reads authority from prose -- `_parse_review_verdict` (188 lines) and `_parse_self_review_verdict` (39) are deleted, replaced by one schema-validated record read at one derived path and compared against the identity the daemon dispatched. Two documents: an agent-authored judgement that cannot express evidence or commits, and the wrapper-assembled result that binds it to git truth. The gate caught two real defects the cockpit could not: a module-scope `jsonschema` import that killed every dispatched leg in the container (`9220341d`), and 37 untested assembly-seam lines (`384a57d0`). Test retirement classified per amendment 5.2: 8 structure mirrors of the deleted regex retired, 2 restated to keep the safety property without the mechanism, the rest migrated with assertions unchanged; the CR-00 corpus passes with only its fake CLI changed, so it now exercises the real contract end to end. Final `tester-unified` on `384a57d0`: `diff-coverage OK: 326/326 changed executable lines covered (100.0%)`, `GATE_EXIT=0`. Merged to `main` as `d8b78cbf`. |
| CR-04a | done | Opus `0a6b68b1`. The amendment's section 3.1 addition, landed first because it is the one item the amendment says must not be discovered during CR-05. `append_and_apply` took the projection it wrote back FROM ITS CALLER, so a write landing during a reconcile pass was overwritten by that pass's stale snapshot -- atomically, which is worse than a visible race. The store now reads committed state inside BEGIN IMMEDIATE, validates and applies against it, and refreshes the caller's map afterwards; the signature is unchanged so all 188 call sites get the fix and none can opt out. Two properties fall out and are pinned: validation reads committed state, and the store writes what the EVENT says and nothing else (a caller can no longer smuggle a hand-edited field through an unrelated append). `tester-unified` on `0a6b68b1`: `diff-coverage OK: 14/14 changed executable lines covered (100.0%)`, `GATE_EXIT=0`. Merged to `main` as `7f7f742b`. |
| CR-04b | done | Opus `4ce1bbdb` + `eae390fa`. Deletes the `NYXLOOM_STATE_BACKEND` selector and the file backend; extracts `projection.py` (the pure validation/projection), which is what removed the import cycle the contract names as part of the same item -- `storage_sqlite` imported the pure functions from `storage` while `storage` dispatched into it, a cycle that only held together because the dispatch was a function-local import inside a selector branch. Adds `backup`/`restore`/`export_jsonl`/`import_jsonl` (online-backup API, not a file copy: under WAL a copy can capture a database whose committed data still lives in a `-wal` sidecar it omits) and the store fault matrix. `doctor rebuild --write` now takes one consistent store backup instead of a per-statefile `.bak`. Found and fixed a projection/log divergence that predated the package: a re-asserted `TASK_BLOCKED` refreshed the blocker in memory but reported no affected task, so replay ended with the newer reason while the served projection kept the older one. Both program ratchets (exception census, ownership inventory) fired on this change and were answered rather than widened. `tester-unified` on `eae390fa`: `diff-coverage OK: 198/198 changed executable lines covered (100.0%)`, `GATE_EXIT=0`. Merged to `main` as `593360fc`. |
| CR-05a | done | Opus `a40c7dbf`. Split on the boundary the readiness audit already names: the registry, injected ports and the smaller route families first; attempt/review/carve/merge execution is CR-05b. `_execute` was a 1,090-line isinstance ladder that both DECIDED what an action meant and PERFORMED it; it is now a lookup. `effects.py` holds the ports (clock, processes, git, filesystem, background work, event log, journal), the per-action context and the registry — exactly one owner per action type, checked when the Daemon is CONSTRUCTED, so an action class added to the planner with no handler is a startup failure rather than a TICK_ERROR on the first pass that plans it. Section 3.3's acceptance is met structurally, not by assertion: no `effects*.py` module may import `daemon` or name `Daemon` (an AST test over the import graph), and all four background-work registries plus the provider backoff registry moved to the effector that owns them — the backoff registry is now injected into the input builder that reads it, so there is one writer and one reader holding the same instance instead of two reaching for an attribute on the shell. Two spec fields are load-bearing rather than documentation: `emits` is DERIVED by walking each handler's own call graph with `ast` (following `self.<name>` whether called or handed to the background port) and must match exactly — an over-broad declaration fails like a missing one; `idempotency_key` is CONSUMED — the registry resolves it before invoking and the two background families key their in-flight registries on `ctx.idempotency_key`, and it travels with the probe result so the drain clears exactly the entry the dispatcher created. The 12 unmoved families are registered as legacy handlers owned by CR-05b, held to `effects.LEGACY_HANDLER_BUDGET` in both directions (the `exception_census.py` ratchet shape, chosen for the same reason: a number that only goes down survives a package boundary). **Amendment 5.1 differential: zero delta.** `tests/effect_differential.py` drives `Daemon._execute` — the entry point identical before and after — over 15 scenarios, and `tests/fixtures/effect_transcripts_v1.json` was RECORDED against `180f3c80`, where `_execute` was still the ladder; every moved family reproduces its pre-package event sequence exactly, so 5.4's stop-loss does not fire. Test retirement (5.2): nothing retired, nothing skipped or xfailed; every touched test is an oracle whose OBSERVABLE moved and was restated with assertions unchanged, except one structure mirror of the deleted ladder (`test_execute_dispatches_verify_gate_action_via_the_isinstance_chain`), restated as a routing property. Both ratchets fired and were answered: `daemon.py`'s exception budget 35 → 33 (both handlers classified in their new module), and the ownership inventory gained rows for three source modules and two test surfaces. `tester-unified` on `a40c7dbf`: `diff-coverage OK: 464/464 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`; all three new modules at 100% with zero `pragma: no cover` exclusions. Denominator verified out of band before the green was accepted — an independent hunk-walk intersected with coverage.py's statement set counts the same 464 across the same four files, and 464 collides with no prior package's denominator. Merged to `main` as `f8704381`. |
| CR-05b | done | Opus `74be0d3a`. CR-05's remaining twelve families do not fit one package -- carve alone is ~713 executable lines, larger than anything this program has accepted -- so this takes the cut with NO cross-family coupling: review dispatch (`LaunchReview`, `LaunchGateDiagnosis`) plus the guarded merge (`AutoMergeTask`), which also completes the merge story CR-05a started with post-merge validation. `effects_dispatch.py` holds what the four agent-launching families share -- admission, pause mode, budget, gate hint, handoff frontmatter, mutex leases, approved scope amendments -- as plain FUNCTIONS over the effect context rather than a base class, deliberately: inheritance would put "may this launch happen at all" somewhere an effector could override, and that is the one decision an effector must not be able to soften. The pass's snapshot verdict now travels ON the context (the shell still records and clears it, and absence still means "no fan-in ran in this call stack", which is permitted and is NOT the same as clean -- both are tested). `effects_review.py` owns both reviewer dispatches and the committed-report readers, which are what is LEFT of prose after CR-03 removed prose from the merge decision; they live there so CR-05c's exit consumer and CR-05d's re-scope depend on one implementation instead of growing a second. `effects_merge.py`'s tests are almost all REFUSALS -- no recorded implementer branch, unresolvable repo root, refused scratch worktree, real conflict, a compare-and-swap the ref lost -- each asserting the task is left where an operator can rescue it. One diagnosability regression was caught while writing them and fixed rather than shipped: folding `worktree add` into a bool dropped git's own stderr from the escalation. **Amendment 5.1 differential: zero delta over 21 scenarios**, recorded against `cc5a33aa` where these were still ladder branches; the check FAILED first on `auto-merge-clean` and the diff was minted git object names rather than behaviour, so 40-hex names now normalize explicitly (full 40-hex only -- a shorter pattern would swallow ordinary hex content and hide a real payload change). Test retirement (5.2): nothing retired, skipped or xfailed; three censuses that scanned `daemon.py` BY NAME now glob the whole control-plane dispatch surface, because a moved call site silently dropping out of a census is the failure those tests exist to prevent one level up. `daemon.py` 8,515 -> 7,641 lines; exception budget 33 -> 31. `tester-unified` on `74be0d3a`: `diff-coverage OK: 441/441 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`; all six effector modules at 100% with zero exclusions; denominator recounted out of band (441 across six files, colliding with no prior package). Merged to `main` as `b27852e4`. |
| CR-05c | done | Opus `5a0b4a3f`. Implementer dispatch, resume, and the warm self-review leg. The receipt-exit consumer deliberately does NOT come with them: what an exit MEANS depends on the attempt's ROLE rather than on which effector dispatched it, and its CARVER branch delegates into carve -- so it lands after CR-05d as CR-05e rather than forcing either a ~950-line package or a second debt mechanism to paper over the dependency. `effects_attempt.py` owns the two worktree shapes (a re-dispatch after a rejected review CHECKS OUT the existing branch, which carries the prior attempt's commits; only a first dispatch branches from the default; a refused add RAISES rather than launching an agent into a directory that is not there), the stale-receipt ARCHIVE (a resumed attempt reuses its receipt path, and the leg being resumed already wrote one -- left in place the next pass reads RUNNING-with-a-receipt as "the wrapper died", emits a premature exit on the STALE receipt while the resumed session is live, and lets a SECOND implementer into the same worktree), and the resume ordinal derived from disk so a resume that landed without its event cannot overwrite the previous leg's log. Two failure shapes that no test had ever distinguished now are: a self-review with no warm session DEGRADES to AWAITING_REVIEW, an admission refusal emits nothing and leaves the task SELF_REVIEWING; the resume path's refusal had no oracle at all. The `build_dispatch` call-site census became an `ast` walk -- its regex balanced ONE level of nested parentheses, so a call site whose argument was itself a call with an argument did not match and it under-counted; it reported the shortfall rather than passing, which is the only reason it was caught, and a census that can miss for a reason unrelated to its property is not a census. **Differential: 26 scenarios, zero delta** against `6634bdbf`; it failed first on `resume-attempt` and the diff was an un-normalized STATE-root path, not behaviour (an attempt record carries paths under two temporary trees). Test retirement (5.2): nothing retired, skipped or xfailed. `daemon.py` 7,641 -> 7,438 lines. `tester-unified` on `5a0b4a3f`: `diff-coverage OK: 130/130 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`; all seven effector modules at 100% with zero exclusions; denominator recounted out of band. Merged to `main` as `2f9fb1f4`. |
| CR-05d | done | Opus `61f81fc7` + `96d8e153`. The three carver-session verbs, the snapshot reader whose `None` IS the feature-off gate, and the proposal validators the repair turn depends on. **Corrects this row's own previous entry**, which said the family was blocked on CR-06 because `_carver_session` and `_validated_carve_proposals` are read by `_build_input` as well as by the effects. That was wrong: a READER moves into an effector module as a function exactly as `scope_amendment_files` and `parse_reject_class` already had, and the SHELL may call an effector -- only the reverse is forbidden. `_carver_enablement_warned` moved off the shell with it, one more §3.3 shared attribute gone. The move was done as a MOVE, reviewable against the original: bodies transcribed with seven external references adapted, everything else still resolving because the method it names moved alongside; `_ensure_worktree` relocated from the attempt effector to `effects_dispatch` in the same commit, since carve needs it too and leaving it a method would have made one effector depend on another for a git call. **The gate rejected the first attempt at 413/415**: two delegates written alongside the eight real ones had no caller after the move. They were DELETED rather than tested -- an unused forwarding method is decoration, and covering it would have made the shell look more coupled to the carver than it is. The cockpit could not see this; both modules read 100% while those two lines were changed-and-unexercised. Two oracle defects surfaced and were fixed rather than worked around: the `emits` derivation counted any `EventType` MENTION as an emission (the validators READ `CARVER_PROPOSAL_RECORDED` to decide admission, so the launch verbs were being asked to declare an event they cannot produce -- it now counts only members reached inside an APPEND call), and a test asserted a branch existed by patching a class method AFTER the Daemon was built, which the registry's bound-method capture meant intercepted nothing. **Differential: 30 scenarios, zero delta** against `787b9f52`; the four new ones pin FEATURE-OFF as byte-identical for all three verbs. Test retirement (5.2): nothing retired, skipped or xfailed; one structure mirror of the deleted isinstance branch restated as a routing property. `daemon.py` 7,438 -> 6,611 lines; exception budget 31 -> 30, the last handler classified in its new module. `tester-unified` on `96d8e153`: `diff-coverage OK: 411/411 changed executable lines covered (100.0% >= 100.0% floor)`, `GATE_EXIT=0`; all eight effector modules at 100% with zero exclusions; denominator recounted out of band. Merged to `main` as `ca7b06c4`. |
| CR-05f | next | The carve-dispatch families: `CarveDispatch` and `AdmitCarveProposal`, plus the packet builders, the verdict-audit section, and `_execute_carve_via_session_resume` (the normalize branch that hands a carve to a warm session). ~335 executable lines by the same measurement method. Everything it needs now exists: `effects_carver` owns the sequence counter, the session snapshot and the validators; `effects_dispatch` owns admission and `ensure_worktree`. Budget 3 -> 1. |
| CR-05e | pending | `EmitAttemptExit` alone: the receipt-exit consumer, which routes by attempt ROLE into the implementer outcome table, the review verdict consumer, the self-review consumer, and carve's two. It lands last because it is the one handler that depends on every other family having moved. Budget 1 -> 0, at which point the readiness audit's final structural oracle applies: reject effector imports or effector-owned mutable state on `Daemon`, not merely a long `_execute`. |
| CR-06 through CR-14, CR-16 | pending | Dependency order in section 7 remains authoritative. |

Program operating decisions:

- Keep the nyxloom daemon stopped through the core migration unless a later
  ledger entry records a deliberate compatibility decision.
- Preserve all live and nonterminal project tasks through backups and versioned
  upcasting. Greenfield architecture does not authorize a live-state reset.
- Free or otherwise untrusted routes remain disabled until CR-13a is gated.
- Implementation agents do not run the long gate. The controller runs it from
  each committed package branch, and no package merges on cockpit-only evidence.
- **A package branch is REBASED onto accepted `main`, never merged into, before
  the gate runs.** `coverage_gate` resolves its base by HEAD's parent count: a
  normal tip is diffed against `merge-base(main, HEAD)` — the package's own
  delta — but a MERGE commit is diffed against its FIRST parent, which for
  `git merge main` inside a package branch is the package tip, making the
  measured delta *what `main` brought in*. That is not a hypothetical: CR-01's
  first gate run reported `360/360 (100.0%)` and `GATE_EXIT=0` while measuring
  CR-15's 360 lines, and CR-01's own 190 lines were unmeasured. Rebased, the
  same tree failed at 178/190. Two independent checks caught it — the count was
  byte-identical to the previously accepted package's, and an out-of-band
  recount of the branch's changed executable lines disagreed with the gate — so
  the controller verifies the reported denominator against the package's own
  diff before accepting any green. Both are cheap; a package merged on the
  previous package's evidence is not.
- **Bounded contract amendment (CR-02a):** `types.py` and
  `schemas/event.schema.json` carry the two new snapshot event types. CR-02's
  acceptance — "one actionable event" per authoritative fault — cannot be met
  without them, and no existing member has the right meaning (`TICK_ERROR` is a
  different fault class, and CR-16 claims its streaks). Granted as a
  package-scoped extension of the core-redesign exception, recorded here rather
  than inferred from it.
- Reviewers may improve and commit the implementation as they see fit while
  preserving the package contract; review is not limited to comments.

### Implementation-readiness audit decisions

An independent Opus/high read-only audit of the 2026-08-02 tree refined the
execution shape below. These are package boundaries and verification rules,
not changes to the product direction:

- **CR-01:** implement the standing product-truth check as an ordinary pytest
  test exercised by the existing `tester-unified` command; do not grow the
  already constrained gate argv. It covers explicit `[refs]` plus the contract
  files named by `AGENTS.md`, including interpreter and authoritative-gate
  claims.
- **CR-02a / CR-02b:** first introduce typed snapshot descriptors, the
  authoritative fan-in and the fault matrix; then land a complete advisory
  census and an AST allow-list oracle for broad exception handling. The audit
  counted 144 broad exception handlers across 15 production modules. In
  particular, `Daemon._build_input` currently converts a lint exception to an
  empty finding set and therefore to `lint_clean=True`; CR-02 owns that
  fail-open defect.
- **CR-03:** use two reviewable commits in one gated package: first the
  versioned envelope plus generated enum/schema consistency checks, then the
  replacement of regex authority at the exact decision sites. Extend the
  existing receipt/gate-result records where possible rather than creating a
  parallel evidence model.
- **CR-04:** perform the backend removal in two internally verified steps:
  make SQLite unconditional while ignoring the selector, then delete the
  selector, file backend and obsolete tests. The source census found 17
  transactional append/project call sites and 13 direct append bypasses after
  excluding definitions; the test migration spans 119 call sites in 19 files.
  Persist storage/projection versions in a dedicated metadata table rather
  than reusing the event-envelope `SCHEMA_VERSION`. Rollback means restoring a
  verified pre-write backup and replaying with the old projector; it does not
  promise that the old binary can read a newly projected database.
- **CR-05a / CR-05b:** first add the handler registry, injected ports and the
  smaller route families; then move attempt, review and carve execution. The
  final structural oracle rejects effector imports or effector-owned mutable
  state on `Daemon`, not merely a long `_execute` method.
- **CR-06:** decompose against the numbered reconcile-contract rules already
  documented in the source and reuse `PlanResult.trace`, `ReconcileTrace` and
  `TraceNote` as the explanation model.
- **CR-07a / CR-07b:** first land schema, parser, typed IR, validator, digest,
  negative corpus and shadow compilation; then migrate lifecycle nodes. Keep
  read compatibility for the removed `DRAFT` value through enum `_missing_`,
  not as an executable workflow state.
- **Differential verification:** there is no production old/new runtime flag.
  Tests retain a frozen legacy reconcile implementation and compare observable
  plans; CR-05 compares injected-port transcripts. Legacy test machinery is
  retired when its replacement package has passed its retirement obligation.
- **Migration safety:** preserve queued and other nonterminal tasks across the
  redesign. "Drain" means no attempt may be mid-effect during a schema write;
  it does not mean finishing or deleting the queue. Inventory the live state
  root before CR-04, then create and verify the rollback backup immediately
  before CR-07 first writes an upcast event shape.
- **Merge order:** land CR-15 before CR-03, CR-05 and CR-07 because it already
  threads actor identity through daemon handlers those packages will move.

## What this document is

The parent plan is a good plan: the layering is right, the compile-time rejection conditions
are genuinely sharp, and the acceptance criteria mostly observe behaviour rather than
structure. **It stands except where amended here.**

This amendment changes four things and adds four:

- **corrects** §3's verified starting point, where two facts about the deployed system are
  wrong in ways that change package scope;
- **re-sequences** containment, which currently lands after the packages that increase the
  exposure it exists to contain;
- **fixes two dependency inversions** that would force rework (CR-04 carrying CR-07's schema;
  CR-01's acceptance invalidated by CR-04);
- **adds two packages** (CR-15 control-plane authentication, CR-16 liveness and channel
  health) and splits one (CR-13);
- **adds four missing program mechanisms**: differential verification, test retirement, a
  labour model, and a stop-loss.

Operator decision #13 — core redesign before new behaviour — is **held as written**. The
amendments below are consequences of holding it, not attempts to reopen it.

## 1. Corrections to §3, "Verified starting point"

### 1.1 SQLite is already the sole live store — CR-04 is code deletion, not a cutover

§3 states that `storage.py` selects between backends via `NYXLOOM_STATE_BACKEND`. True of the
source. But `nyxloomd/docker-compose.yml:83` sets that variable to `"sqlite"` and records the
cutover as **live since 2026-07-21**, with `events.jsonl` retired to `.pre-sqlite` and backups
under `~/.local/state/nyxloom-backups/`. The live state directory confirms it: every
registered project has a `state.db`.

Consequences:

- **§7.3 is rewritten.** It currently requires a "destructive live cutover" runbook that
  "stops nyxloomd, identifies the exact state volume/project DBs, creates and verifies
  backups/exports, initializes the new schema, replays the characterization corpus, and
  documents rollback". Most of that describes an event that has happened. What remains is
  genuinely required and should be kept: **back up and verify before the schema-version and
  upcaster machinery first writes a new event shape.** The rest is ceremony that will make
  the package look riskier than it is and invite it to be deferred.
- **CR-04's scope narrows** to: delete the file backend, the selector and the dual-backend
  tests; extract pure validation/projection; add schema and projection version tables plus
  upcasters; implement backup, restore, JSONL export **and a tested re-import**; prove the
  fault behaviours. That is a bounded refactor.
- **CR-04's scope also widens by one item** — see §3.1 below, the projection API.

### 1.2 The deployed trust boundary is not what the plan assumes

§3 does not describe the runtime the packages will execute in. Three facts change package
scope and priority:

- `NYXLOOM_HTTP_BIND: "0.0.0.0"`. The mutating control plane — including
  `POST /api/decision/reply`, which is how a human answers an escalation — is
  **unauthenticated** and reachable from any container on `nyxloomd-net`.
- `wrapper.wrapper_main` handed every agent CLI `os.environ.copy()`: the daemon's full
  environment, including secrets `nyxloomd/secrets.env.example` documents as daemon-only.
  (Amended by a stopgap strip; see the review amendment.)
- The daemon container mounts `/var/run/docker.sock` (host-root-equivalent), the operator's
  home directory, and every registered project repository. Agent CLIs are its direct children
  in that namespace.

`routes.host.toml` already defines `[tiers.implement-1-free]` with four free OpenRouter
routes. The guard currently applied to them is a sentence in the prompt.

### 1.3 The system is not running

`nyxloom-prod-nyxloomd` has been `Exited (143)` for ten days and the notification channel has
been crash-looping. This is good news for the program — there is no live traffic to protect
during CR-04 through CR-07 — and bad news for the premise that anyone would notice if the
redesign broke something. See CR-16.

## 2. Re-sequencing: containment precedes the cheap-route expansion

The parent puts DR-14 / CR-13 (runtime sandbox) at position 14 of 15, dependent on CR-05,
CR-08 and CR-10. The program's stated purpose is to make cheap and free routes the normal
path (CR-09, CR-10). **Containment therefore lands after the packages that multiply the
exposure it exists to contain**, in a runtime where an agent already shares the daemon's
namespace, docker socket, operator home and control-plane network.

Split CR-13:

| Package | Scope | New position |
| --- | --- | --- |
| **CR-13a — execution containment** | Agent CLIs run in a per-use container (D-R7): no docker socket, no operator home, only the declared repository/worktree mounted, and **per-route secret injection** rather than environment inheritance. Failure to establish containment prevents launch. | **Before CR-09.** Depends on CR-05 only. |
| **CR-13b — resource and permission policy** | Full per-task/role permission, mount, network, secret, CPU, memory, process and wall-time policy as selector constraints and handler inputs; recorded containment identity; resource kill classified separately from capability. | Phase D, as originally planned. Depends on CR-08, CR-10, CR-13a. |

CR-13a's acceptance: a free-tier attempt cannot read the operator home, cannot reach the
docker socket, receives only the secrets its route declares, and cannot open a connection to
the control-plane port. A route configured to require containment that is unavailable does
not launch.

**Standing rule until CR-13a lands:** free and otherwise untrusted routes stay disabled. The
plan should say this explicitly rather than leaving it as an inference; `implement-1-free` is
opt-in today, and "opt-in" is not a control once the cost optimiser is choosing routes.

## 3. Dependency and acceptance corrections

### 3.1 CR-04 must not carry CR-07's schema — and must change the projection API

CR-04 work item 3 reads: "Introduce lifecycle/node, workflow/prompt digests, route
explanations, and result evidence in versioned tables/events." The lifecycle/node model is
*designed in CR-07*, three packages later. CR-04 would therefore speculate on a schema that
does not exist, and CR-07 would migrate it anyway.

Amendment: CR-04 delivers the store mechanics, versioning and **upcasters**; CR-07 lands the
lifecycle/node schema through an upcaster. That is precisely what the upcaster machinery is
for, and it converts a guess into a designed migration.

CR-04 gains one item instead, from RISK-008: **`storage.append_and_apply` must stop taking a
caller-owned `states` dict.** Today the reconcile loop and the HTTP handler threads each pass
their own in-memory projection, and a UI write during a pass is overwritten by the pass's
stale snapshot. The plan's §4.9 promise — "event append and projection update in one
transaction", "one logical daemon writer" — is unreachable while the projection is computed
from a caller's snapshot: such a transaction is atomic and wrong. The store must derive the
projection from committed state inside the transaction. Small signature, wide blast radius;
it must land with CR-04, not be discovered during CR-05.

Added CR-04 acceptance:

- A projection update concurrent with a reconcile pass is never lost, and no caller can
  submit a projection derived from a snapshot older than the committed head.
- A JSONL export re-imported into an empty store replays to a byte-identical projection.

### 3.2 CR-01's acceptance is invalidated by CR-04 — make document truth a standing gate

CR-01 acceptance says "every declared current fact about store, merge mode, trove, daemon,
and milestone agrees with machine configuration". CR-01 runs first; CR-04 then deletes the
file backend and makes `README.md`'s headline — "files are the database" — false again. The
cleanup would be stale within its own program.

Amendment: CR-01 delivers the contradiction check as a **lint rule that runs in the gate on
every package**, over a small set of machine-known facts (state backend, trove path, merge
mode, daemon mode, active milestones, containment requirement). Any package that changes one
of those facts fails its own gate until the declared documents agree. CR-01's one-time
archive work is then just the first thing that rule certifies.

CR-01 also gains the identity item explicitly: the product's self-description must be
rewritten when CR-04 lands, and the rewrite is CR-04's obligation under the new rule.

The rule must cover the **contract files**, not only `[refs]` documents. `nyxloom-trove/
STANDING.md` — inherited by every handoff — was found pinning a stale date, naming a
nonexistent interpreter, and declaring a cockpit `pytest` invocation "the only accepted
evidence" in direct contradiction of `nyxloom.toml`'s `[gates.tester-unified]` and of this
plan's own §3. That is the same defect class with a far worse blast radius: a stale reference
wastes a model's context, while a stale standing contract lowers the evidence bar on every
package the factory produces. The declared interpreter and the declared gate are
machine-checkable; check them.

### 3.3 CR-05 must assign state ownership, not only move code

`Daemon` is a single class with **155 methods** over shared mutable instance state
(`_gate_verify_running`, `_post_merge_gate_running`, `_httpd`, `registry`, thread handles).
"Move effect code from `Daemon` without policy changes" can be satisfied by six modules that
all reach back into one god object, which passes the acceptance criteria and delivers nothing.

Added CR-05 acceptance: each effector module owns its own state explicitly through injected
ports; no effector holds a reference to the `Daemon` instance; and background-work registries
(gate verify, post-merge gate) belong to the effector that owns the work, not to the shell.

## 4. Added packages

### CR-15 — control-plane authentication and operator identity

Covers: RISK-005. Depends on: nothing. Position: **first, alongside CR-00.**

This is small, independent of the redesign, and gates the invariant the whole product rests
on. It should not wait behind fourteen packages.

Work:

1. Require an operator credential on every mutating endpoint; issue and rotate it through the
   daemon's own state directory, not a config file in the repository.
2. Bind the credential to a named operator identity and put that identity in the `Actor` of
   every resulting event. Today every UI write is attributed to the literal string `"ui"`,
   which is an interface name, not an identity — so the audit trail cannot answer "who
   answered this decision".
3. Refuse unauthenticated mutation with an audited event, not a silent 403.
4. Keep the read surface separable, so the dashboard can stay open on a trusted network while
   mutation requires a credential.
5. Delete or correct every code comment asserting a security posture the deployment does not
   have. (Four such "loopback-only" claims in `daemon.py` — the module docstring, the
   `_CONFIG_POST_PATHS` comment, and two handler docstrings — were corrected already. The
   rule going forward is that an assertion about the trust boundary must be testable or
   absent.)

Acceptance:

- An unauthenticated `POST /api/decision/reply` produces zero decision state change and one
  audited refusal event, and cannot distinguish a valid decision id from an invalid one.
- Every `CONFIG_CHANGED`, decision reply and intake event carries a resolvable operator
  identity.
- Credential rotation invalidates the prior credential within one reconcile pass.
- A cross-site browser request is refused before any lookup. *(The CSRF half of this — a
  `Content-Type` requirement and same-origin `Origin` check — is already implemented with
  regression tests; CR-15 adds authentication on top, and must not regress it.)*

### CR-16 — liveness, channel health, and silent-failure detection

Covers: RISK-007. Depends on: CR-04 (event/store), CR-02 (authoritative/advisory model).
Position: **before CR-09**, i.e. before the system is trusted to run cheap work unattended.

Evidence for the package: the daemon has been stopped for ten days and the notification
channel crash-looping, and nothing reported either. `watchdog.detect_runaways` covers
`notification-storm`, `reconcile-thrash` and `attempt-loop` — all three detect *too much
activity*. Nothing detects absence.

Work:

1. A deadman: a durable heartbeat per project, and an alarm when no reconcile pass has
   completed within a configured multiple of the interval.
2. Transport health as a first-class probe, with an escape path that does not depend on the
   transport being probed. An alarm channel that cannot report its own failure is not an alarm
   channel.
3. `TICK_ERROR` streaks as a watchdog pattern, so a daemon that is up, healthy by its TCP
   check, and failing every pass is detected rather than rendered red on a dashboard nobody
   is watching.
4. A container healthcheck that reflects *reconciliation* liveness, not just that a socket is
   listening. The current check is `bash /dev/tcp` against the HTTP port, which a wedged
   daemon passes indefinitely.

Acceptance:

- Killing the daemon mid-pass produces an operator-visible alarm within a bounded interval,
  through a path that does not require the daemon to be alive.
- Breaking the notification transport produces an operator-visible alarm through a different
  path.
- A daemon whose every pass raises is reported as failing, not as healthy.
- The alarm path is itself covered by the fault-injection matrix in CR-02.

## 5. Added program mechanisms

### 5.1 Differential verification for CR-05, CR-06 and CR-07

CR-00's characterization corpus is the only safety net the parent proposes for a rewrite of a
1,160-line planner and a 1,060-line executor. It is a good net and it is not sufficient: it
proves the new code reproduces the cases someone thought to write down.

The parent review lists "shadow planner and two-phase workflow rollout" among out-of-the-box
opportunities. It belongs in the program as a **required acceptance mechanism**, not an idea:

- CR-06 and CR-07 run the old and new planner over identical snapshots — the CR-00 fixtures
  *and* the historical event log of the self-host project — and diff the planned action
  sequences. Any difference is either explained in the package report or is a defect.
- CR-05 does the same for effects at the boundary: identical action in, identical typed
  result and event sequence out.

This costs little (both engines are pure at the planning layer, which is the parent's own
design achievement) and converts "the tests we wrote still pass" into "the behaviour we did
not think to write down is unchanged".

### 5.2 Test retirement policy

`test_daemon.py` is over 7,000 lines and mirrors implementation structure. A rewrite of
`daemon.py` invalidates most of it. Nothing in the parent plan owns that, so every core
package will fight false reds from tests asserting the shape of code being deliberately
replaced — and the cheapest way through that pressure is to weaken tests, which is the exact
failure this project's doctrine exists to prevent.

Obligation added to CR-05, CR-06 and CR-07: every test the package touches is classified as

- **behaviour oracle** — asserts an observable artifact, event, state or exit code. Kept, and
  migrated to the new structure.
- **structure mirror** — asserts internal call shape, private method presence, or module
  layout. Deleted with the code it mirrors, and the package report names it.

A package may not leave a test disabled, skipped, or `xfail`ed to make its own gate pass. The
strict `xfail` the parent notes for the orphan `DRAFT` state is resolved under CR-07, which
removes workflow-specific members from the state enum: either `DRAFT` becomes a real node or
it leaves the domain.

### 5.3 Labour model and self-host constraints

The parent plan does not say who implements it. This matters, because nyxloom's operating
model is cheap implementers behind a frontier review gate, and CR-05 through CR-07 are
frontier-grade architecture work on the two largest files in the tree — while autonomous
carving is frozen for the duration.

Stated explicitly:

- CR-00, CR-05, CR-06, CR-07 and CR-13a are **operator-carved and frontier-implemented**.
  They are not band-1 or band-3 work, and attempting them as cheap handoffs will produce the
  hollow-improvisation failure `LESSONS.md` already documents.
- CR-01, CR-12, CR-14, CR-15 and CR-16 are bounded enough to be carved as ordinary handoffs
  under the existing review gate.
- The daemon manages other projects (`dstdns`, `naf`, `topos`, `ciu`). It is currently
  stopped, which is the cheapest possible moment for this program. If it is restarted for
  those projects during the redesign, each core package needs a live-compatibility statement;
  if it stays stopped, the plan should say so, because "self-hosting" is one of the north
  star's five strategic guarantees and suspending it is a real cost to acknowledge.

### 5.4 Program stop-loss and de-scope ladder

Under decision #13 the program delivers no operator-visible value until CR-09 — the tenth
package. That ordering is defensible, and it is only defensible with explicit criteria for
stopping.

Declared before starting:

| Trigger | Response |
| --- | --- |
| CR-00 cannot produce a corpus that fails when a transition, artifact binding, or gate verdict is deliberately corrupted | Stop. The characterization net does not exist, and CR-05..07 have no safety net. Re-scope CR-00 before proceeding. |
| CR-05 or CR-06 exceeds its budget by more than 100%, or its differential diff cannot be driven to explained-or-zero | Stop and re-scope. Do not proceed to CR-07 with an unexplained behavioural delta. |
| CR-07's compiler cannot express the current flow without a per-node escape hatch into imperative code | Stop. That is the signal that the workflow language is the wrong abstraction; fall back to CR-05/CR-06's decomposition plus a hand-written flow, which already delivers most of the maintainability gain. |
| Two consecutive core packages land with the old engine still live beside the new | Stop. The parent's §5 explicitly warns against maintaining two control engines; this is the measurable form of that warning. |

De-scope ladder, in the order things should be dropped if the program must shrink: CR-10
(cost optimiser — CR-08's selector with configured priors is already useful), then CR-12
(criterion evidence), then CR-07 (the compiler — CR-05 and CR-06 deliver most of the
maintainability benefit without it). **CR-13a, CR-15 and CR-16 are not de-scopable**; they are
safety, not architecture.

## 6. Amendment to CR-09: decline validity and cost-amplification guards

The parent's §4.6 makes an explicit capability decline promote immediately and exclude the
route for that task fingerprint. The parent review's own checklist asks whether that creates a
cost-amplification path; neither document answers it. A route that declines liberally — from
miscalibration, not malice — turns band 1 into a toll booth: every task pays a cheap call and
lands at band 2 anyway, which is strictly worse than having no band 1.

Added to CR-09's work and acceptance matrix:

1. **Decline validity.** A decline is honoured only when it names a mechanical unmet condition
   from a closed vocabulary *and* is corroborated by a fact the wrapper establishes
   independently (empty diff, no tool invocations, measured context over the packet envelope).
   An uncorroborated decline is retained as evidence and treated as an ordinary failure.
   *Acceptance:* a fake route that declines with no corroborating evidence does not promote
   the task, and the decline is visible in the trace.
2. **Per-route decline rate as a routing input.** Excluding the route for one task is not
   enough. A route whose decline rate for a task archetype crosses a threshold stops being
   selected for that archetype. *Acceptance:* a fake route declining every band-1 task of an
   archetype is removed from selection for it within a bounded number of attempts, and the
   removal is an operator-visible, operator-reversible observation.
3. **Per-task decline cap** terminating in a typed human decision. *Acceptance:* no task can
   traverse the full ladder on repeat; the cap produces one actionable wait, not a loop.

## 7. Revised package table

Changes from the parent's §5 are marked.

| Package | Covers | Depends on | Position |
| --- | --- | --- | --- |
| CR-00 | prerequisite | — | 1 |
| **CR-15** *(new)* | RISK-005 | — | 1 (parallel with CR-00) |
| CR-01 | DR-04 | CR-00 | 2 — *acceptance now a standing lint gate* |
| CR-02 | DR-03 | CR-00 | 2 — *scope widened to all of `src/`* |
| CR-03 | DR-13 | CR-00 | 3 |
| CR-04 | DR-09 | CR-00, CR-03 | 4 — *scope narrowed (no cutover, no CR-07 schema), widened (projection API)* |
| CR-05 | DR-06 | CR-03, CR-04 | 5 — *state ownership acceptance added* |
| CR-06 | DR-07 | CR-05 | 6 — *differential verification required* |
| CR-07 | DR-08 | CR-03, CR-05, CR-06 | 7 — *differential verification required; lands lifecycle/node via upcaster* |
| CR-08 | DR-05, DR-10 | CR-04, CR-07 | 8 |
| **CR-13a** *(split, moved)* | DR-14 (containment) | CR-05 | 9 — *was position 14* |
| **CR-16** *(new)* | RISK-007 | CR-02, CR-04 | 9 |
| CR-09 | DR-01 | CR-03, CR-07, CR-08, CR-13a | 10 — *decline guards added* |
| CR-10 | DR-11 | CR-08, CR-09 | 11 |
| CR-11 | DR-02 | CR-05, CR-06, CR-07 | 11 |
| CR-12 | DR-12 | CR-04, CR-07 | 12 |
| **CR-13b** *(split)* | DR-14 (resource policy) | CR-08, CR-10, CR-13a | 13 |
| CR-14 | DR-15 | CR-04, CR-07..CR-13 | 14 |

## 8. Metrics ownership

§8 lists eleven required metrics as ship criteria, and no package owns emitting them. A ship
criterion nobody implements is discovered at the ship gate. Assigned:

| Metric group | Owner |
| --- | --- |
| Store transaction, checkpoint, backup, replay health | CR-04 |
| Planner/handler errors, fail-closed admissions | CR-05, CR-06 |
| Route selection explanations, filtered reasons | CR-08 |
| Declines, promotions, same-band changes, diagnoses, escalations, false-promotion audit rate | CR-09 |
| Expected and actual cost per accepted change; first-pass acceptance; minimum successful band by archetype; execution-packet size and cache reuse | CR-10 |
| Review escape/rejection rate by band, risk and gate rigor | CR-10 |
| Human interruptions, time-to-answer, duplicate suppression | CR-14 |
| Causal completeness (percentage of insights with resolvable job evidence) | CR-14 |
| Daemon liveness, transport health, silent-failure detection | CR-16 |

## 9. Answers to the parent's external-review checklist

The parent's §9 poses ten questions "reviewers should explicitly answer". Answering the ones
this pass can:

- **Does lifecycle-plus-node preserve every safety property of `TASK_TRANSITIONS`?** Not
  automatically. The safety properties currently enforced by the transition graph are of two
  kinds: lifecycle legality (terminal tasks cannot re-enter; attempts cannot regress) and
  workflow ordering (review precedes merge). The first must stay in the kernel; the second
  moves into the compiled plan and is only as strong as compile-time rejection condition 6.
  CR-07 needs an explicit inventory mapping each current transition rule to *kernel* or
  *compiler*, with a negative test per rule. Without that inventory the migration is a
  best-effort translation.
- **Can any agent result or configurable guard indirectly authorize merge?** Not under
  conditions 6 and 7 as written, *provided* guards remain registered pure predicates over a
  typed snapshot and cannot read agent-authored text. That constraint should be stated in the
  handler contract, not left implicit: a guard named `reviewer_said_ok` reading a report body
  would satisfy every listed condition and defeat the boundary.
- **Does immediate promotion create a cost-amplification path?** Yes, as written. Mitigated by
  §6 above.
- **Is direct SQLite-only cutover operationally safe?** The question is largely moot — it
  happened on 2026-07-21. What remains safety-relevant is backup-and-verify before the first
  new event shape is written, and a *tested* export/re-import.
- **Are CR-05 through CR-07 sequenced tightly enough to avoid two control engines?** Only with
  the stop-loss in §5.4. "Deliberately serial" is a statement of intent; the trigger makes it
  enforceable.
- **Which package still has scope too broad for a bounded independently reviewable handoff?**
  CR-07 and CR-10. CR-07 bundles schema, parser, IR, validator, digest, state-model
  replacement, manifest authoring and diagram generation; it should be split at minimum into
  compiler-and-IR versus lifecycle-migration. CR-10 bundles archetype features, statistics,
  the cost objective and execution packets; the packet compiler is separable and independently
  valuable.

Remaining checklist questions (workflow language minimality, decline mechanicality for small
models, `review-N` eligibility versus dominance, cost objective as constraint) are product
judgements the operator has already decided and this pass does not contest.

## Change log

| # | Change | Reason | Parent section |
| --- | --- | --- | --- |
| 1 | Corrected §3: SQLite has been the sole live store since 2026-07-21 | The plan schedules a cutover that already happened; CR-04 is code deletion, and the inherited error is itself an instance of the doc-truth risk | §3, §7.3 |
| 2 | Rewrote §7.3 from "destructive live cutover runbook" to "back up and verify before the first new event shape" | Ceremony proportional to a migration that is not happening makes the package look riskier than it is and invites deferral | §7.3 |
| 3 | Added §3's missing trust-boundary facts (unauthenticated 0.0.0.0 control plane; agents inheriting the daemon environment, docker socket and operator home) | Three of the four new risks are invisible from `src/` alone, and they change package priority | §3 |
| 4 | Split CR-13 and moved CR-13a (execution containment) before CR-09 | Containment currently lands after the packages that multiply the exposure; the cost program's whole purpose is routing more work to untrusted routes | §5, CR-13 |
| 5 | Added the standing rule that free/untrusted routes stay disabled until CR-13a | "Opt-in" stops being a control once a cost optimiser is choosing routes | §5, §7 |
| 6 | Moved CR-07's lifecycle/node schema out of CR-04 into CR-07, via upcaster | CR-04 would otherwise speculate on a model designed three packages later, and CR-07 would migrate it anyway | CR-04, CR-07 |
| 7 | Added the `append_and_apply` projection-API change to CR-04 | The §4.9 atomicity promise is unreachable while the projection comes from a caller's snapshot; two concurrent writers lose updates today | CR-04, §4.9 |
| 8 | Made CR-01's contradiction check a standing lint gate rather than a one-time cleanup | CR-04 invalidates CR-01's acceptance within the same program; only a continuous check survives | CR-01 |
| 8b | Extended CR-01's contradiction check to the contract files (`STANDING.md`, `DOCTRINE.md`), not only `[refs]` | `STANDING.md` declared cockpit pytest "the only accepted evidence" against `nyxloom.toml`; a stale standing contract lowers the evidence bar on every package, invisibly | CR-01 |
| 9 | Added state-ownership acceptance to CR-05 | `Daemon` is one class with 155 methods over shared mutable state; "move the code" is satisfiable by six modules reaching into one god object | CR-05 |
| 10 | Added CR-15 — control-plane authentication and operator identity | The "human owns direction" invariant is enforced by network topology asserted in a comment; every UI write is attributed to `"ui"`, not an identity | New |
| 11 | Added CR-16 — liveness, channel health, silent-failure detection | The daemon has been down 10 days and the alarm channel crash-looping with nothing reported; the watchdog detects only excess activity | New |
| 12 | Made differential/shadow verification a required acceptance for CR-05..CR-07 | The characterization corpus proves only the cases someone wrote down; both planners are pure, so the diff is cheap | §6, CR-05..07 |
| 13 | Added a test-retirement policy with a behaviour-oracle / structure-mirror classification | A 7,000-line implementation-mirroring suite is unowned during a rewrite of what it mirrors; unowned, it becomes pressure to weaken tests | §6 |
| 14 | Added a labour model and self-host constraints | The plan does not say who implements it, while the operating model is cheap implementers and carving is frozen; CR-05..07 are not cheap-handoff work | §7 |
| 15 | Added a stop-loss and de-scope ladder | Under decision #13 nothing is operator-visible until package ten; that is defensible only with declared exit criteria | §7 |
| 16 | Added decline-validity, per-route decline-rate, and per-task cap guards to CR-09 | The parent's own checklist asks whether immediate promotion amplifies cost and does not answer it; without a floor, band 1 is a toll booth | §4.6, CR-09 |
| 17 | Assigned every §8 metric to an owning package | A ship criterion nobody implements is discovered at the ship gate | §8 |
| 18 | Answered six of the ten external-review checklist questions, and named CR-07/CR-10 as still too broad to carve | The checklist asks reviewers to answer explicitly; leaving them open leaves the plan unreviewed on its own terms | §9 |
