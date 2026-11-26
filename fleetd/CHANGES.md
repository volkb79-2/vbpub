# Changelog

All notable changes to this project are recorded here.

## [Unreleased]

### Added
- Add the stdlib host admission book for capacity fit, durable charges, flock liveness, priority queueing, aged reservations, and lease accounting. This is library-only and is not yet wired to the host agent or callers.
- Collect, validate and consume `host-facts/2` with explicit null reasons, load, gate-slice, disk, guard and policy facts; `fleet facts validate` retains `/1` for one migration release.
- Document the new facts and optional host policy keys without changing planner placement decisions; close FD-049's guard fact reshape.

### Changed
- Assign prerequisite-missing exit code 79 in the fleetd runner contract; 73 is reserved by fleetd's runner and is not available for that result.
- `fleet facts collect` emits `host-facts/2` only.
- The planner reads `/2` `cpu.online` and `memory.total_bytes`, and refuses `/1`; legacy `/1` `cpu.for_lanes` and `memory.for_lanes_bytes` are therefore accepted only by `fleet facts validate` during the migration release and no longer affect placement.
- The collector reads the parent control-plane slice's `memory.min` again and reports it as `control_plane.memory_min_bytes`; the planner ignores it.

<!-- cmru: release history -->

## [0.1.0] - 2026-11-24
<!-- cmru: generated -->
<!-- cmru: source-end=71adc3ab141df54e7e88d6b6638c981e2d800908 -->

### Added
- feat(fleetd): the default R2 producers — scope, sizing, measurement, merge check (d8e9cb325)
- feat(fleetd): complete the cli-extended adoption (surface, skills, docs, release edge) (843628ed9)
- feat(fleetd): register the fleet CLI on cli-extended (dependency boundary + grammar) (db286757e)
- feat(fleetd): add named-job campaign client (61feb2224)
- feat(fleetd): add deterministic RAM-first planner (4dabc733e)
- feat(fleetd): collect rooted host facts (7c07ddd70)
- feat(fleetd): add strict versioned contracts (adee9c10c)
- feat(fleetd): scaffold the stdlib campaign planner project (0ac933d68)

### Fixed
- fix(fleetd): returned-duplicate stop only touches jobs that were lost (P-fd-01 r2 A) (37dda0ab6)
- fix(fleetd): end the unreachable episode on an ERROR answer and decide the error episode once per tick (WP2b B1, B2) (28c91bbda)
- fix(fleetd): validate the cost fields before dividing and cross-check them in the measurement validator (WP3b review 3, 4) (0eed38192)
- fix(fleetd): re-plan the placeable candidates and defer only the unplaceable ones (FD-017) (4157958d3)
- fix(fleetd): widen the first stall window of a host without a comparable speed reference (FD-025) (3fc04670b)
- fix(fleetd): age a host that stays in ERROR and warn at 1800 s, never lose or re-queue it (FD-026) (18c8b30dc)
- fix(fleetd): reserve a share of the rolling slice for the never-judged remainder and report every deferred changed file (FD-038) (a6beb0b5a)
- fix(fleetd): start the host-loss clock at the first failed probe, report a timeout as SLOW (FD-020) (cb7a00b33)
- fix(fleetd): size R2 shard budgets from the per-candidate mean and a measured tail margin (FD-031) (63568d5d3)
- fix(fleetd): settle a job for probing only from its EXIT row; tests no longer need the checkout's hx.sh (WP1b) (1243f4f71)
- fix(fleetd): defer new batch starts on a host above the PSI threshold (FD-022) (7411617f3)
- fix(fleetd): name the hx wrapper with --hx or FLEETD_HX; refuse cleanly when installed (FD-023) (3258259fa)
- fix(fleetd): the probe no longer grows with the batch count (FD-014) (729b3671e)
- fix(fleetd): review fixes for the cli-extended adoption (entry guard, skill accuracy, CHANGES honesty) (47403a8a0)
- fix(fleetd): derive --state-dir at run time instead of baking a home into the surface (f07f51d6d)
- fix(fleetd): run the campaign-contract lane in the review plugin's partial mode (6f893059d)
- fix(fleetd): never fail a batch that never started (aa1adb69c)
- fix(fleetd): preserve progress probes within argv budget (40b9ad5c7)
- fix(fleetd): make campaign supervision progress-aware (9197fbce0)
- fix(fleetd): restore start clocks on a refused start; test the lock under real contention (8f654f22b)
- fix(fleetd): never contact refused hosts, refuse unsynced checkouts, lock state (68b828e73)
- fix(fleetd): align campaigns with runner layout and restart recovery (1016c9651)
- fix(fleetd): require full job digests and close recovery edges (2663be716)
- fix(fleetd): harden campaign recovery and collection (fc97b40f2)
- fix(fleetd): complete superseded-job scheduling (c806981e3)
- fix(fleetd): correct campaign starts and pin execution output (8883089d2)
- fix(fleetd): apply the control-plane reserve default (41d78c66b)
- fix(fleetd): declare the monorepo's unsafe-symlink omissions in assay.toml (587d10c0e)
- fix(fleetd): clarify selected-run closeout and exercise commands (ee81ac47d)
- fix(fleetd): clarify manual plans and empty ETA reference (d0cb1744b)
- fix(fleetd): unpack role validation and keep config oracle current (5de289adf)
- fix(fleetd): trust host role and eligible ETA inputs (2a49f23ee)
- fix(fleetd): track ignored fixtures, refuse oversized input, correct demand docs (9abd51d66)
- fix(fleetd): correct planner demand parsing and oracles (ff9220c0a)
- fix(fleetd): preserve unavailable cgroup facts (58f524447)
- fix(fleetd): validate collector failure states (dbfa1bca2)
- fix(fleetd): correct contract edge-case oracles (5ed5798c9)

### Changed
- P-fd-02: remove fleet-owned merge and guard surfaces (e56e281b9)
- Separate returned duplicate pending state from loss requeue (0ae83d029)
- File remaining hostside review findings (ca3fea82c)
- Keep returned duplicate handling pending until stopped (cc4c87fa5)
- Exercise the full shipped probe answer and restore group checks (31c330658)
- Harden hostside readers against non-string candidate ids (815dd3b3e)
- fleetd: document shared host-side evidence (cc22a68e6)
- fleetd: share hostside probe and fix campaign evidence (6226a08a1)
- fleetd: ship probe logic as stdlib hostside module (cc554ca80)
- fleetd: every later rolling pass sweeps the whole target again; shared-budget limit documented (FD-033 review, FD-038) (f10b5d228)
- fleetd: account retired paths from the discarded cursor under --rebaseline (FD-033 review round) (55667b902)
- fleetd: key the rolling whole-target sweep by file content, so an edit resumes it (FD-033) (410d21255)
- fleetd: failed-collection tables keep the profile constant; installer refuses an empty --root (3235584f2)
- fleetd tests: pin a partial per-service reserve table, and the host install docs (463d36b34)
- fleetd: per-service control-plane reserve fallback with provenance (FD-007c) (6c8bc700d)
- fleetd: record the sizing estimator gap in FD-031; drop an unused import (076994537)
- fleetd: review fixes for the R2-default producers (4cf2b306b)
- fleetd: retire parent slice memory min probe (415166445)
- fleetd: isolate reserve placement capacity assertion (df73bef78)
- fleetd: align plan tests with reserve capacity (280916687)
- fleetd: align service fact validation expectations (5fcc5244f)
- fleetd: derive control-plane reserve from facts (e04ba2d86)
- review(fleetd backlog): FD-025 states the real threshold formula and fallback, FD-026 traced and confirmed with the fake transport (25082c722)
- backlog(fleetd): FD-026 wording (code path not yet traced) (725994f45)
- backlog(fleetd): FD-025 first stall window without a comparable speed reference, FD-026 host in ERROR has no loss timer (67bbbecad)
- Exercise campaign safety edge branches (8a75af3f6)
- Record three-host deferred retry coverage (eff20232a)
- Cover deferred retries across three hosts (bee29cf0c)
- Prove progress windows exclude earlier Assay events (614a913c2)
- Resolve Assay artifact lane when starting jobs (c4b8a1e23)
- Close reviewer coverage gaps (673c6b8e8)
- Cover remaining planner and contract branches (580fac053)
- Place planner candidate above shared cap (df24bd749)
- Use feasible planner test candidates (402566cf9)
- Correct planner batch expectations (44e4db903)
- fleetd: repair selected batch execution (677deaa21)

### Documentation
- docs(fleetd): P-fd-02 r1 fix-forward: drop the remaining merge-check prose (ec9a5596a)
- docs: review r1 fixes for P-plat-00/03 (house rules consistency, interim exit 3, FD-041/043) (4dfd1fee0)
- docs(backlog): file CTL-37..54, MDT-106/107, FD-039..046 (P-plat-00) (696310699)
- docs(fleetd): document the WP2b episode rules and known limits, pin the returned-after-loss keys (51da9596b)
- docs(fleetd): document the WP2 supervision rulings and close FD-017, FD-020, FD-025, FD-026 (aadd58721)
- docs(fleetd): reword the sweep remainder order, state the 75 percent deferral and the uncapped margin (WP3b review 1, 2, 5, 6, 7, 8) (4c57ae8f0)
- docs(fleetd): mark FD-031, FD-035 and FD-038 done in the backlog with their commits (6e16775c0)
- docs(fleetd): document the mean-based budget, the sweep reserve and the FD-035 by-design limit (FD-031, FD-038, FD-035) (e5e97ac78)
- docs(fleetd): record the probe, hx wrapper and PSI start gate (FD-014, FD-022, FD-023) (c53d8f942)
- docs(fleetd): close five campaign review findings (a39dbdf19)
- docs(fleetd): record series-review findings FD-015..FD-024 and reserve decision (9b2130e34)
- docs(fleetd): state the probe size limit and file FD-014 (e9f5b7e3c)
- docs(fleetd): record campaign fix round and deferred state lock (04aa1c6ad)
- docs(fleetd): document campaign workflow and MVP-C backlog (ffd55cd4c)
- docs(fleetd): correct closeout paths and record host slice gap (114122d02)
- docs(fleetd): name public campaign schemas (36c839076)

### Testing
- test(fleetd): pin the boundaries and guards the WP2 hand-mutation review found unpinned (436153e70)
- test(fleetd): pin help completeness, the catalog check, and the domain precedence (4db3f2dd4)
- test(fleetd): characterize the fleet CLI before the cli-extended migration (feb6dc1d3)
- test(fleetd): pin the planner's not-reported rule without relying on the validator (be0e1681f)
- test(fleetd): pin the per-job candidate filter in refresh and collect (8b1d5aa24)
- test(fleetd): drop trailing blank line (09781ccfa)
- test(fleetd): cover restart and runner refusal branches (4fad6c582)
- test(fleetd): pin stall/loss constants and close surviving mutants (9e9e9c83f)
- test(fleetd): cover plans without execution context (5133b523f)
- test(fleetd): register campaign contract lane (f679b20aa)
- test(fleetd): cover runner state and deterministic collection edges (373a80870)
- test(fleetd): derive planner budgets from inputs and pin determinism, batch size and CLI overflow (78d25f281)
- test(fleetd): include SPEC in public doc checks (ca59ced65)
- test(fleetd): cover incomparable timing fallbacks (15faba780)
- test(fleetd): expect unavailable eligible ETA reference (eb0d776ea)
- test(fleetd): assert eligible ETA reference shape (6413b81bb)
- test(fleetd): pin large-candidate physical routing (92251270c)

<!-- cmru: pending release notes begin -->
### Fixed

- Host-side progress and verdict readers now skip non-string candidate ids
  instead of raising a `TypeError` while testing set membership. Their outcome
  bucket list is shared through `hostside.OUTCOME_BUCKETS`.
- Host-side probe logic is now the stdlib-only `fleetd.hostside` module shipped
  as source and imported by the controller. `FakeTransport` materializes a
  temporary host home and calls the same probe, lane resolution, progress
  filtering and verdict reader (FD-039; installed `fleet host ...` verbs remain
  P-fd-05). The probe now returns the observer's `profile.metrics.memory_peak_bytes`,
  and collection merges that measured value into a matching earlier record
  instead of dropping it (FD-040).
- A host that returns after loss keeps its returned-duplicate fact pending while
  the retry is unfinished or a stop fails; a later successful refresh stops the
  source duplicate once. A duplicate may finish too, and collection still
  chooses one owner (WP2c/L2).
- Campaign supervision (WP2). The host-loss clock starts at the first failed
  contact the controller observed instead of the last successful contact, so one
  failed probe after a long gap no longer marks a healthy host `LOST`; a probe
  timeout (rc 124) is reported `SLOW` (`HostSlow`, a `HostUnavailable` subclass)
  and counts toward the same 1800 s clock (FD-020). A host that stays in `ERROR`
  reports `error_since`, `error_seconds` and a `warning` from 1800 s in `status`
  and the campaign state; it is never declared lost or re-queued automatically
  (FD-026). A host without a comparable Geekbench reference gets its first stall
  window widened to 3 x the unscaled reference until it has observed a finished
  candidate (`NONCOMPARABLE_FIRST_WINDOW_FACTOR`, FD-025). `replan_pending`
  now returns `(batches, unplaceable)`: one candidate that does not fit the
  re-queue target no longer drops the placeable others, and the unplaceable ids
  wait in `deferred_requeues` with the planner's reason until a larger host is
  online, else `collect` reports them missing with that reason (FD-017).
- Campaign supervision follow-up (WP2b). An `ERROR` answer (a garbled probe, a
  digest mismatch) now ends an open unreachable episode like a good probe, so a
  later failed probe starts a fresh loss clock instead of declaring the host
  `LOST` at once and re-queueing work that still runs there (FD-020). The error
  clock is decided once per command from the host's final status, so an error
  raised after a good probe or a refused start no longer restarts it and the
  1800 s warning fires once (FD-026). A batch that exits on an `ERROR` host is
  deferred with an ERROR reason instead of "unreachable".
- Size R2 shard budgets from the per-candidate mean and a measured tail margin
  (FD-031). `fleet-r2-measurement/1` stays `/1` and its `summary` gains
  additive optional fields `cost_candidates`, `cost_seconds_sum`,
  `cost_seconds_mean`, `cost_seconds_stdev` and `cost_seconds_max` over killed,
  survived, `budget_exceeded` and `hung` candidates (the last two at their
  bound); the median fields are unchanged and a document written before them
  still loads and sizes from its median. `fleet sizing --measurement` now uses
  the mean and derives each shard's margin as `max(1.10, 1 + 1.645 x cv /
  sqrt(n))`; `--margin` (no longer defaulting to 1.10 on the command line) is
  the explicit override. Sizing documents gain `seconds_statistic`,
  `margin_source`, `dispersion` and a per-shard `margin` (additive to
  `fleet-r2-sizing/1`). The budget is an estimate: a remainder continues in a
  later instance. Effect: a measured heavy-tailed lane now gets a larger budget
  than before.
- Reserve a share of the rolling slice for the never-judged remainder (FD-038).
  `[r2] reserve_percent` in `r2-scope.toml` (integer 0 to 100, default **25**,
  rounded up, capped at `slice_size - 1`) keeps that share of every slice for
  non-changed candidates (never-judged and the pending candidates of a
  half-judged file, in path order), so a target edited every release by more
  than `slice_size` candidates still sweeps; a reserve the remainder cannot fill
  returns to the changed files, `0` restores the changed-first slice. The
  scope document's `slice` gains `reserve_percent`, `reserved_candidates`,
  `deferred_changed_candidates` and `deferred_changed_files` (every changed file
  that did not fit). The cursor document is unchanged, so every
  `fleet-r2-scope-cursor/2` file still loads. RISK: a heavy-churn release now
  judges fewer changed candidates and more sweep candidates than before.
- Probe follow-ups to FD-014/FD-022/FD-023 (WP1b). A job is now settled for
  probing only when a probe row reported it `EXIT` with its progress window
  closed (`probe_settled`), so a stall stop in the gap between the launcher's
  end-offset write and EXIT no longer leaves a job without its verdict records;
  states written before the flag keep the earlier rule. A host that becomes
  unreachable or errors drops its stored PSI reading. The tests no longer depend
  on the checkout's `scripts/controller/hx.sh`.
- The probe no longer grows with the batch count (FD-014). It sends one
  compressed job-name table per (campaign, host, commit, project, lane) group
  with an ID-file path template, keeps a single copy of the payload, and skips
  jobs that ended with a closed progress window; a 150-batch host probes with
  about 6 KB instead of failing at the 128 KiB argument cap near 100 batches.
- Name the hx wrapper with `--hx PATH` or `FLEETD_HX`; an installed fleetd with
  neither exits 3 with one actionable line instead of "could not launch hx
  transport" (FD-023). Precedence: `--hx`, then `FLEETD_HX`, then the source
  checkout default; `PATH` is never searched.
- Defer new batch starts on a host whose memory or IO PSI `some avg10` is above
  the plan's `psi_refuse_some_avg10`, whose PSI cannot be read, or whose plan
  records no threshold (FD-022). `status` names the reason in `start_deferred`;
  running batches are never stopped. Plan host rows carry
  `psi_refuse_some_avg10` (additive to `fleet-plan/1`; a campaign started from an
  older plan needs a re-plan before it starts new batches).

### Changed

- `host-facts/1.guard.enabled` is now a plain read of the merged top-level
  `enabled`: a boolean is reported as itself, while a missing or non-boolean
  value is `null` with a reason naming what was found. Rule validation is
  gone; runtime `state` is, as before, the owner-emitted string from the
  configured state source, and rule validation never fed it.
- Rewrite the rolling whole-target sweep so an edit no longer restarts it
  (FD-033). The `fleet-r2-scope-cursor/1` cursor resumed at an offset into a
  candidate-id list whose ids fold in the source-file hash, so nearly every
  release that changed a target file went `stale-cursor` and `--rebaseline`
  restarted at slice 0: rolling was a sample, not a sweep. The cursor is now
  `fleet-r2-scope-cursor/2`, keyed by **file content** (the git blob id at the
  judged commit) and by a location-stable candidate key
  `(path, operator, ordinal)`, never by an Assay candidate id; a path whose
  content changed is due again and judged whole, every other path keeps its
  marks, a plan shape that no longer matches a mark is reported as `changed`,
  and a path that left the plan is `retired` with its reason. The slice is a
  prefix of a deterministic order (changed files first, least recently judged
  first, then never/not-yet-judged candidates in path/line order), `slice_size`
  is a budget that re-slices the same sweep, a finished sweep starts a recorded
  new pass (`new_pass`) instead of an empty `nothing-to-judge`, the scope document
  carries the accounting and the cursor a whole-document `state_digest`, and a
  `fleet-r2-scope-cursor/1` cursor is refused with its migration
  (`--rebaseline`, reported as `slice.legacy_cursor`). `--rebaseline` is now
  rare instead of per-release. Rolling mode re-judges a changed file whole (a
  superset of the changed-lines scope); the remaining per-file cost is FD-037.
- Replace the all-or-nothing control-plane reserve with a per-service fallback
  (FD-007c). Each listed service contributes its reported `MemoryMin`, its
  documented default when the fact is missing or implausible, or `0` when a
  declared-optional unit is absent from that host; the fixed profile constant
  remains only for the case where no per-service fact exists at all. Plans and
  campaign status expose `reserve_services` provenance (`reported`, `default`,
  `absent-optional`) next to `reserve_bytes`, `reserve_source`,
  `reserve_missing_services` and `reserve_reason`. `host-facts/1` carries each
  unit's `load_state` (nullable with a reason) and
  `control_plane.service_memory_min_reason` when a facts root queries no unit.
  A reported value above 4 x its documented default, or a total above twice the
  documented floors, refuses the host with a reason instead of a clamped sum.
  The per-service table equals the one `scripts/debian-install-v2` writes, and
  `tests/test_control_plane_memory_table.py` parses the installer's source and
  its documented table so drift fails.
- Point the host install at the runner's `install-fleetd.sh` (`/opt/fleetd/
  <version>/venv`, hash-verified wheels, `--check`) instead of `pip install`
  into the system Python, which Debian 13 refuses (PEP 668; FD-034).
- Migrate the `fleet` CLI from a hand-built `argparse` parser to a registered
  `cli-extended` CLI. Every subcommand, flag spelling, default, output document
  and success or domain-error exit status is unchanged; the shared library now
  owns help, the identity banner, the output controls and the exception
  boundary. What the library changes, on purpose: the error line on stderr is
  `[ERROR] <message>` (was `fleet: ERROR <message>`) and a parse error is one
  such line without the argparse usage block (exit 2 as before); `-h` is gone
  (use `--help` or `fleet help [verb]`); option names must be spelled in full
  (argparse prefix abbreviations such as `--cand` are refused); a group without a
  verb (`fleet`, `fleet facts`) prints its help and exits 0 instead of 2; and
  every verb gains `--quiet`, `--debug` and the other shared controls (and
  `--json` where it emits a document).
  Callers that matched the old stderr prefix or relied on `-h` must be updated.
  The pre-migration
  contract is pinned by `tests/test_cli_characterization.py`, written before the
  change and green on both implementations.
- Declare `cli-extended>=0.5.0` as a real wheel dependency (unified-adoption
  CX-D1). It is never vendored and never put on `sys.path`; 0.5.0 is the
  revision the registered gate image installs, so a managed host and the gate
  run the same contract. `cmru`'s release graph gains the matching
  `fleetd -> cli-extended` edge, which moves fleetd from L0 to L1.
- The console script is now `fleetd.entry:main`: it imports only the standard
  library first, so a host without the declared dependency gets one actionable
  install line and exit 3 instead of a `ModuleNotFoundError` traceback. Any
  other missing module still raises. `python -m fleetd` keeps working.
- The package version comes from `CliIdentity.resolve(distribution="fleetd")`
  instead of a literal. An uninstalled source checkout refuses (exit 3) rather
  than inventing a version.

### Removed

- Remove `fleet merge-check`, the fleet-owned `fleet-shard-identity/1` and
  `fleet-shard-identity-set/1` documents, `assay-merge-manifest/1` validation,
  `fleet guard validate`, and the guard rule schema. Assay owns the manifest
  and the `owner-manifest`/`strict-shard` policies (B544/B565); thrashguard
  owns guard rules (TG-1).

### Added

- Surface lifecycle for the registered CLI: `docs/cli-surface.json`, the
  generated region in `docs/SPEC.md`, the reviewed catalog
  `docs/cli-review.toml` (94 characterized cases), `docs/cli-review-findings.toml`,
  and the `cli_extended.pytest_plugin` link between a catalog row and its test.
  `tools/gen-cli-review-catalog.py` regenerates the catalog from the exported
  surface.
- `fleet skills install|check|list|uninstall` from the library, backed by the
  packaged skill source `fleetd/skills/fleetd-campaigns/SKILL.md` (package data,
  one source of truth inside the wheel).
- Exit-code and entry-point boundary tests (`tests/test_cli_boundary.py`,
  `tests/test_cli_entry.py`) and packaging/skill tests
  (`tests/test_cli_skills.py`).
- Scaffold the standalone `fleetd` project and its R0/R1 gate.
- Add versioned host facts and work request contracts with strict validators
  and fixtures.
- Add a read-only, injectable-root host-facts collector with strict host-owned
  config, PSI/cgroup/KSM/control-plane facts, and privacy-filtered Geekbench
  records.
- Report bounded per-service control-plane `MemoryMin` facts and each host
  profile's fixed fallback reserve.
- Add a deterministic RAM-first plan library and CLI with physical candidate
  caps, explicit worker-wave budgets, per-host Geekbench ETA, memory-crash
  retries, and stable Run-Gate candidate-list commands.
- Add a transport protocol, in-memory fake hosts, and an hx-backed transport
  that delegates remote commands to the controller kit's identity-checked
  wrapper.
- Add runner command builders for digest-named idempotent jobs, progress
  probes, candidate-list transfer, and stop handling.
- Add `fleet run`, `status`, `tick`, and `collect` with atomic versioned local
  state, restart recovery, stall/loss handling, planner-based re-queues, and
  one-owner combined JSON and Markdown results.
- Add real-runner contract checks that execute generated start, probe, and stop
  scripts with the checked-in `run-job` and `run-r2-campaign.sh` under a
  temporary HOME, while stubbing only the lane command.

- Add `fleet scope`, the committed R2 candidate-scope producer: the default
  `changed-lines` mode selects candidates on lines added since the previous
  release tag (`git diff -U0`, renamed files judged whole), an empty scope is
  an explicit `nothing-to-judge` result (exit 4) that refuses to write an id
  file, and the opt-in `rolling-whole-target` mode walks a persisted sweep
  cursor keyed by file content (see the FD-033 entry above; a cursor for
  another lane or an older schema is refused, exit 3 / with its migration).
- Add a committed `r2-scope.toml` (`schema_version = 1`, `[r2]` with `mode`,
  optional `slice_size`, `cursor`, `note`) so a project declares its scope
  instead of passing a hand-prepared id file; no file keeps the default.
- Add `fleet sizing`, which derives instances per host, shard count, each
  shard's budget and the outer bound from the plan's host budgets, a per-worker
  unit and a fixed per-instance charge, and records whether the numbers are
  `measured` or `declared`.
- Accept optional `worker_memory`, `worker_cpu` and `instance_cpu` in the lane
  demand table, refusing a worker charge above `unit_memory`; sizing names the
  missing keys instead of guessing a unit.
- Add `fleet measurement`, which writes `fleet-r2-measurement/1` per-candidate
  elapsed seconds and observer memory peaks plus medians over killed and
  survived candidates; a missing number stays `null`, never zero, and the
  collector now carries the cgprofile `resource-profile/1` peak per candidate.
- Accept an optional `execution.image_digest` in plans so a shard can state the
  tester image it judged under, as measurement evidence.
- Add `docs/R2-DEFAULT.md` and consumer, README, SPEC and design-guide sections
  for the three commands, plus a docs test that every closed value a consumer
  types (`changed-lines`, `rolling-whole-target`, the four document schemas and
  the scope exit statuses) appears, every TOML example parses with the shipped
  loader, and every cross-document anchor resolves.
- File FD-029 (no workflow wires the producers), FD-030 (Run-Gate refuses the
  per-worker demand keys), FD-031 (budgets from medians, not a measured tail)
  and FD-033 (the rolling cursor goes stale when a target file changes).
- `fleet scope` refuses a checkout that is not exactly the named commit
  (different HEAD, or uncommitted changes to tracked files): FD-032 is done.
- The diff reader no longer drops or shifts files for a form feed, U+2028 or lone
  carriage return inside a source line, a path containing a space, or a changed
  file that is not UTF-8.

### Changed

- Read the host role from the existing host-setup source, ignore refused hosts
  when choosing the ETA reference, and reject demand units the R2 planner does
  not support.
- Report when no eligible host can provide an ETA reference, and document the
  host-role values in the `host-facts/1` specification.
- Make the manual command handoff explicit: copy only the matching host section
  to that host and run it there; the section creates its own ID files and
  fleetd has no transport.
- Record the omitted Run-Gate campaign-plan client under RG-276.
- Emit one serial `--only-candidates` Run-Gate call per host batch, with
  explicit jobs and `BUDGET_EXCEEDED` exit handling; reserve campaign-slot
  concurrency for the future supervisor.
- Balance placements by normalized reference work per worker when Geekbench
  scores cannot be compared, and retain a reason when a candidate's source
  host is absent from the current host set.
- Validate every candidate ID as Assay's lowercase 64-digit SHA-256 digest.
- Document that the manual command path cannot pass fleetd's per-host RAM
  budget into Assay, and track the enforcement contract for the Run-Gate
  campaign client.
- Execute generated batch scripts against a fake Run-Gate to pin status-4
  continuation, failure stopping, and candidate-file creation.
- Clarify that selected-run closeout copies the state path printed by Run-Gate
  and requires the same lane commit and tester image on every host.
- File FD-004 for the missing host-owned `dev-gates.slice` memory ceiling fact.
- Replace the rejected RAM-proportional control-plane reserve with the sum of
  all six positive per-service `MemoryMin` facts, or the host profile's fixed
  constant when any service is missing. Stop collecting parent-slice
  `memory.min`; accept it only as a legacy input field and ignore it. Expose
  reserve source and missing units in plans and campaign status.
- Test complete and partial reports, zero/infinity/unset parsing, profile
  fallback, RAM-independent reserve values, capacity and refusal changes, and
  reserve-driven lost-host placement. Retain the deterministic CLI golden plan
  for two 4-core, 8-GiB host profiles and 230 mixed-RAM candidates.
- Record FD-005 through FD-007 for host-factor learning, selected-run capacity,
  and reserve calibration; the rejected formula is replaced and p95-under-load
  profile calibration remains open as FD-007b.
- Correct Assay's tracked fixture-symlink omissions for the fleetd judge lane.
- Record MVP-C follow-ups for strict Assay merge, Run-Gate option forwarding,
  restricted host enrollment, baseline reuse, the always-on daemon, and
  campaign-aware guard enforcement in fleetd and their owning project
  backlogs.
- Fix the project-scoped ID path and plain-run Assay progress discovery against
  the runner layout. Probes carry ID-file paths rather than candidate arrays,
  and recover a missing startup digest from the uploaded ID file.
- Requeue queued as well as running batches from lost hosts, preserve one
  requeue for a stalled batch, and probe idle planned hosts as requeue targets.
- Persist controller boot IDs with monotonic/UTC deadline records so restart
  recovery re-bases deadlines; bump campaign state to schema 2. Backward UTC
  steps delay detection after reboot; forward steps count as downtime and can
  advance detection. Document runner option limits, project path rules,
  host-clock ownership skew, and fail-closed hx identity/guard refusals.
- File FD-013 for serializing concurrent state updates from overlapping
  `fleet run`, `status`, `tick`, or `collect` invocations.
- Never contact a host the planner refused when it owns no campaign job; the
  idle-host probe had reached refused (for example shared-prod) hosts on every
  command.
- Refuse to start a batch when the host's persistent checkout is not synced,
  before the ID-file upload would create `checkouts/<commit>/` as a plain
  directory that the supervisor refuses and the checkout sync cannot replace.
- Compute the probe's progress path lexically below the remote home, so a
  symlinked `checkouts` directory no longer fails every probe; report `null`
  instead of failing when no home-relative path exists.
- Restore a batch's start and stall clocks when its start is refused or the
  host is unreachable, so a start retried after more than 15 minutes is no
  longer stalled and stopped on the next tick; repeated refusals record one
  warning instead of one per tick.
- Close FD-013: hold a non-blocking per-campaign lock for each command; an
  overlapping command fails with "busy" and changes nothing.
- Pin per-host stall observations, repeated host loss, exit de-duplication
  against work in flight elsewhere, and the start script's ID-file digest check
  with tests (each closes a surviving hand mutant).
- File FD-015 to FD-024 from the Opus series review and record the operator's
  rejection of the RAM-proportional control-plane reserve under FD-007.
- Set stall windows from per-candidate host predictions and host-local observations, with an optional declared or observed baseline allowance for the first progress window.
- Fail zero-progress EXIT, missing-job, and stalled batches visibly with their failure reason and exit code when available; batches that made progress retain unlimited re-queue behavior.
- Record progress byte bounds in each runner job and combine batch candidate IDs with those bounds; use the batch verdict for resumed outcomes so stale append-only records cannot count as new progress.
- Keep Assay verdict entries marked process-tree `not-started` candidates as missing with a distinct reason, and include failed batches and reasons in collection.
- Continue supervising other hosts when a host probe or operation returns a transport or contract error; report the affected host as `ERROR`.
- Review of fleetd-fix4: a batch that never started (a QUEUED batch of a lost
  host, or a start interrupted while STARTING) is no longer failed as
  zero-progress when its host returns or the job is absent; it is re-queued or
  retried with fresh clocks, so a single-host campaign keeps its unstarted work.
<!-- cmru: pending release notes end -->
