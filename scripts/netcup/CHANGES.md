# Changelog

All notable changes to scripts/netcup are recorded here.

## [Unreleased]

### Added
- `runner/install-fleetd.sh` installs the fleetd and cli-extended wheels a
  managed host needs for `fleet facts collect` and `fleet guard validate`. It
  verifies both SHA-256 inputs before writing, installs offline into
  `/opt/fleetd/<version>/venv` (`--no-index --no-deps`), links
  `/usr/local/bin/fleet`, is idempotent for the same hashes and reinstalls when a
  hash changes, and never uses the system Python, which Debian 13 refuses
  (PEP 668). `--check` verifies the inputs and reports whether that exact wheel
  hash is already installed without writing, exiting 1 when it is not. It
  refuses a symlinked install path, an incomplete existing venv and a
  non-symlink file at the command path, and it touches nothing outside its
  prefix. `runner/README.md` and `CONSUMERS.md` carry the host sequence;
  `tests/test_install_fleetd.py` covers every refusal in a temp prefix only
  (`NC-57`).
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
  full ceiling. For `jobs = "auto"`, omitted `--jobs` is split across the
  instance count and passed through to Run-Gate. Expected peaks fit
  `MemAvailable - reserve` plus only the explicit measured
  `R2_EXPECTED_PEAK_CREDIT_MIB` (default 0); swap and KSM state add no automatic
  credit. `R2_OVERCOMMIT` (default 2.0) applies only to the sum of full
  declared container caps against `MemAvailable`. The runner prints a CPU
  saturation hint for underused native R2 lanes and records the memory
  arithmetic plus KSM state and eligibility in preflight and `status`; missing
  KSM sysfs is reported as unavailable. It does not enable KSM or change
  Run-Gate's per-container cap.

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
