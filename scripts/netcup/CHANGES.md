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
  guard refuses a fleet that does not fit `MemAvailable` (exit 75).

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
