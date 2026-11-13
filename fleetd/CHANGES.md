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
- Make the manual command handoff explicit: generated candidate files must be
  staged with the matching host section because fleetd has no transport.
- Record the omitted Run-Gate campaign-plan client under RG-276.
