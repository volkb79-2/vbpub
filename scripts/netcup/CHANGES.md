# Changelog

All notable changes to scripts/netcup are recorded here.

## [Unreleased]

### Added
- `runner/provision-gates-limits.sh` renders and applies the gates-tier memory policy of a pure test host (operator
  decision 2026-11-16) and `provision-runner.sh` runs it: drop-ins only, no unit-file edits. From the host's own
  `MemTotal`/`SwapTotal` it derives `MemoryMax = MemTotal - 1024 MiB floor - 256 MiB slack` and
  `MemorySwapMax = SwapTotal - 8192 MiB reserve`, sets `MemoryHigh=infinity` (no throttling mark), gives every
  transient `docker-<id>.scope` `OOMPolicy=kill` (`memory.oom.group=1` per container, deliberately not on the slice),
  and puts `OOMScoreAdjust=-900` on `ssh.service`, `systemd-logind.service`, `systemd-journald.service`,
  `docker.service` and `cgprofiled.service` (written in place through `/proc/<MainPID>/oom_score_adj`, so no service
  restarts and no ssh session is dropped). It refuses a host fact it cannot read, an override that does not protect a
  floor, a competing foreign drop-in, and a slice restart while the tier has tasks (a restart resets the
  `memory.events` counters remote-gate's `INCONCLUSIVE-OOM` reads); a second run with matching live values changes
  nothing (`NC-59`).
- `start-cockpit-runner.sh` starts the cockpit with `--oom-score-adj` (`COCKPIT_OOM_SCORE_ADJ`, default `-500`); the
  cockpit drives run-gate, so the global OOM killer must not prefer it (`NC-63`).
- `runner/remote-gate.py` admits a lane against LIVE memory instead of a static ceiling: `--mem-need-mb` (the lane's
  expected peak, the controller's weight), `--mem-floor-mb` (default 1024 MiB kept for the control plane) and
  `--wait-mem` (default 900 s). The expected peak is capped at `MemTotal - 2 x floor`, so one lane larger than RAM
  always runs alone and pages; one host-wide ticket (`~/rg/.mem-admit.lock`, held 20 s after a lane starts) keeps two
  callers from reading the same `MemAvailable`; unreadable meminfo is indeterminate and refuses. A refusal is exit 75
  with verdict `MEM_REFUSED` (never folded into `PSI_REFUSED`) (`NC-59`).
- `runner/README.md` carries the policy section (values, overrides, the ssh `-900` inheritance, the slice-restart and
  counter-reset rule, the foreign-drop-in refusal) and the live-admission section; `CONSUMERS.md` carries the
  convergence and verification steps; `DESIGN-GUIDE.md` carries the rationale and the rejected alternatives;
  `README.md` links all three.
- Tests: `tests/test_provision_gates_limits.py` (two RAM sizes, idempotence, idle-only restart, refusal codes, dry-run,
  in-place OOM scores, foreign drop-ins) and `tests/test_remote_gate_memory.py` (the job's own `mem_admit_ok` against
  meminfo fixtures, the included floor boundary, the larger-than-RAM cap, unreadable facts, the real-flock ticket, and
  the `MEM_REFUSED` verdict).

### Changed
- The no-host-setup path's `MemoryMax=6G` instruction is superseded: the memory values come from
  `provision-gates-limits.sh`, and the unit fragment carries only what makes the slice a loaded installed slice for
  `cgroup-parent.sh` (`NC-59`).
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
