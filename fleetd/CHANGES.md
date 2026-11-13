# Changelog

All notable changes to this project are recorded here.

## [Unreleased]

### Added

- Scaffold the standalone `fleetd` project and its R0/R1 gate.
- Add versioned host facts, work request, Assay merge manifest, and guard TOML
  contracts with strict validators and fixtures.
- Add a read-only, injectable-root host-facts collector with strict host-owned
  config, PSI/cgroup/KSM/control-plane facts, and privacy-filtered Geekbench
  records.
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
- Apply the design's clamped control-plane reserve when `ctl.slice` or its
  `memory.min` is absent or below the default, and report reserve bytes/source.
- Add tests for the five assignment heuristics, reserve clamp boundaries, and a
  deterministic CLI golden plan for two 4-core, 8-GiB host profiles and 230
  mixed-RAM candidates.
- Record follow-up items FD-005 through FD-007 for host-factor learning,
  selected-run capacity, and the future measured-p95 reserve.
- Correct Assay's tracked fixture-symlink omissions for the fleetd judge lane.
- Record MVP-C follow-ups for strict Assay merge, Run-Gate option forwarding,
  restricted host enrollment, baseline reuse, the always-on daemon, and
  campaign-aware guard enforcement in fleetd and their owning project
  backlogs.
