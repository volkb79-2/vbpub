# Changelog

All notable changes to scripts/netcup are recorded here.

## [Unreleased]

<!-- cmru: release history -->

### Fixed (WP-T3 persistent R2 supervisor)
- Select the cockpit Run-Gate binary through `RUN_GATE_BIN`, preserve the shared
  source checkout link during T3 imports, and require clean persistent checkouts.
- Give each campaign supervision a unique `-s<N>` evidence directory, count
  completed candidate events for stall detection, and add a supported stop
  command with exact Run-Gate container-label cleanup.
- Write final exit 4 and a NOT_PASS budget summary when the supervisor reaches
  its attempt cap or stalls. Update the T3 launch, adoption, and initial
  capacity-planning guidance.
