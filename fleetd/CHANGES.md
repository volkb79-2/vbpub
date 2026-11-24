# Changelog

All notable changes to this project are recorded here.

## [Unreleased]

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
  gone; runtime `state` remains the owner-emitted value from the configured
  state source. A historical rules-invalid state remains accepted for schema
  compatibility but can no longer be produced by fleetd.
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
- Add versioned host facts, work request, Assay merge manifest, and guard TOML
  contracts with strict validators and fixtures.
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
- Add `fleet merge-check`, which refuses shard sets whose lane, commit, tester
  image, plan digest or shard count differ, refuses a duplicated shard index,
  and reports a missing index as incomplete and refuses it (`--require-complete` is accepted and changes nothing).
- Accept an optional `execution.image_digest` in plans so a shard can state the
  tester image it judged under; a campaign without one runs but cannot be
  merged.
- Add `docs/R2-DEFAULT.md` and consumer, README, SPEC and design-guide sections
  for the four commands, plus a docs test that every closed value a consumer
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
