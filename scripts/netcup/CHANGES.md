# Changelog

All notable changes to scripts/netcup are recorded here.

## [Unreleased]

### Added
- NC-42: `run-r2-campaign.sh ... --instances K` supervises K shard instances
  of one lane at once (Run-Gate `--shard`, one child campaign each with its own
  lock, container, stall counter and evidence), then one `--merge-shards` run
  whose verdict is the fleet's result. `--shard-total`/`--shard-offset` split a
  campaign across hosts, `--merge-only N` merges copied shard stores,
  `--carry-over-main TEXT` restarts across a judge change, `status <base>`
  reports every instance, `stop <base>` stops a whole fleet, and a memory
  guard refuses an uncapped lane without an explicit per-instance estimate;
  zero, empty or repeated option values are refused by name; and these options
  need a host Run-Gate with RG-265/RG-266 (checked
  from its `--help` before any campaign state; Run-Gate checks Assay 12.2.0),
  while the plain single campaign keeps working on an older Run-Gate.
- NC-48: fleet memory admission sums expected worker peaks, scaling the lane
  ceiling by each instance's worker share rather than charging every shard the
  full ceiling. `R2_WORKER_MEM_MIB` supplies measured per-worker usage;
  `R2_OVERCOMMIT` and a bounded free-swap allowance implement the host's
  overcommit policy. The runner prints a CPU saturation hint for underused
  native R2 lanes and records KSM state and eligibility in preflight and
  `status`; it does not enable KSM or change Run-Gate's per-container ceiling.

<!-- cmru: release history -->

### Fixed (WP-T3 persistent R2 supervisor)
- Select the cockpit Run-Gate binary through `RUN_GATE_BIN`, preserve the shared
  source checkout link during T3 imports, and require clean persistent checkouts.
- Give each campaign supervision a unique `-s<N>` evidence directory, count
  completed candidate events for stall detection, and add a supported stop
  command with label cleanup plus per-attempt exact-name fallback for older
  Run-Gate versions. Fail loudly if neither route identifies a lane container.
- Write final exit 4 and a NOT_PASS budget summary when the supervisor reaches
  its attempt cap or stalls. Update the T3 launch, adoption, and initial
  capacity-planning guidance, including the Run-Gate version requirements and
  copied-supervisor dependencies.
