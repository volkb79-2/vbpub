# fleetd changes

## [Unreleased]

- Scaffold the standalone `fleetd` project and its R0/R1 gate.
- Add versioned host facts, work request, Assay merge manifest, and guard TOML
  contracts with strict validators and fixtures.
- Add a read-only, injectable-root host-facts collector with strict host-owned
  config, PSI/cgroup/KSM/control-plane facts, and privacy-filtered Geekbench
  records.
- Add a deterministic RAM-first plan library and CLI with physical candidate
  caps, explicit worker-wave budgets, per-host Geekbench ETA, memory-crash
  retries, and stable Run-Gate candidate-list commands.
- Read the host role from the existing host-setup source, ignore refused hosts
  when choosing the ETA reference, and reject demand units the R2 planner does
  not support.
- Report when no eligible host can provide an ETA reference, and document the
  host-role values in the `host-facts/1` specification.
- Make the manual command handoff explicit: copy only the matching host section
  to that host and run it there; the section creates its own ID files and
  fleetd has no transport.
- Record the omitted Run-Gate campaign-plan client under RG-276.
