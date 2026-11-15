# Using fleetd

## Install the CLI

The `fleet` command is registered with the shared `cli-extended` CLI library.
That library is a real wheel dependency of `fleetd`; it is never vendored and
never put on `sys.path`. `cli-extended` is published on GitHub Releases only
(the bare name is unclaimed on PyPI), so install both wheels from the same
directory:

```sh
python3 -m pip install --no-index --no-deps cli_extended-0.5.0-py3-none-any.whl
python3 -m pip install --no-index --no-deps fleetd-<version>-py3-none-any.whl
fleet --version
```

With a wheelhouse that holds both files, one command does the same work:

```sh
python3 -m pip install --no-index --find-links ./wheelhouse fleetd-<version>-py3-none-any.whl
```

The console script is `fleetd.entry:main`. It imports only the standard library
before it touches the registered CLI, so a host that lacks `cli-extended` gets
one actionable line and exit 3 instead of a `ModuleNotFoundError` traceback:

```sh
fleet --version
# fleet: ERROR cli-extended is not installed and the fleet CLI is registered with it; ...
```

Do not copy the sources onto a path, and do not treat a source checkout as an
install: the version comes from installed distribution metadata, so an
uninstalled checkout refuses to run instead of inventing a number. From a source
checkout, `PYTHONPATH=src python -m fleetd --version` works once the project is
installed (`pip install -e .`).

### What a managed host needs

A host that runs only `fleet facts collect` or `fleet guard validate` needs
these two wheels and nothing from this repository. `fleet facts collect` reads
`/etc/fleetd/facts.toml` and the host's own `/proc`, `/sys`, `systemctl`
service properties and Geekbench record; all of those are host-owned and
absolute (see [the facts config below](#collect-and-validate-host-facts)).

The campaign commands are the *controller's*: `fleet run`, `status`, `tick` and
`collect` launch jobs through the controller kit's identity-checked `hx`
wrapper, which must sit beside a repository checkout. A facts-only host needs
neither the wrapper nor a checkout (FD-023 tracks the installed-wheel path for
the wrapper).

### Agent skills

The packaged skill source is `fleetd/skills/fleetd-campaigns/SKILL.md` inside
the wheel. The library verbs install a stamped copy plus an integrity sidecar,
so a modified copy is detected rather than silently overwritten:

```sh
fleet skills install                 # default: ~/.agents/skills and ~/.claude/skills
fleet skills install --harness claude
fleet skills install --dest /some/dir
fleet skills check                  # exit 1 unless every copy is current
fleet skills list --json
fleet skills uninstall --dry-run
```

## Adoption checklist status

`cli-extended audit` rows for this CLI, as of the registration commit. The
audit is mechanical; the two `manual` rows are judgements recorded here.

| Checklist row | Status | Evidence |
|---|---|---|
| AC-01 version source | pass | `CliIdentity.resolve(distribution="fleetd")`, no literal (`tests/test_cli_entry.py`) |
| AC-02 identity fields | pass | `FLEET` / `fleet` / long name in `fleetd.cli.identity` |
| AC-03 registered grammar | pass | every verb, argument and option is a `VerbSpec`/`ArgumentSpec`/`OptionSpec` |
| AC-04 synopsis overrides | pass | none; the synopsis is derived |
| AC-05 shadowed controls | pass | no consumer option reuses `--json`, `--dry-run`, `--traceback`, ... |
| AC-06 declared constraints | pass | `--candidates/--hosts/--demand` and `--campaign` are `required` in the specs |
| AC-07 configure callbacks | pass | none |
| AC-08 hidden options | pass | none |
| AC-09 SelectorList | not used | no comma- or space-separated name list exists in this CLI |
| AC-10 exception policy | pass | `unexpected_exceptions="report"` on every registry |
| AC-11 domain failures | pass | one mapping in `fleetd.cli._domain` → `CliFailure(exit_code=2)` (`tests/test_cli_boundary.py`) |
| AC-12 mutation safety | **manual, accepted** | `run`/`tick` are mutating without `--yes` or `--dry-run` because they are idempotent supervisors: `run` re-adopts jobs by deterministic name, `tick` is one scheduling step whose only state is the campaign's own versioned file, and an accidental repeat changes nothing a first call would not have changed. A confirmation prompt would break the controller loop that calls `tick` unattended. Destructive acts (`skills install` overwriting a modified copy) do require `--overwrite-modified`, a library-owned guard. |
| AC-13 shared prompts | pass | no interactive question is asked by this CLI |
| AC-14 output and `--json` | pass | `runtime.output`; every read verb declares `include_json` |
| AC-15 progress/colour | pass | no long operation renders progress; the library colour policy applies |
| AC-16 surface configured | pass | `[tool.cli-extended.clis]` in `pyproject.toml` |
| AC-17 surface check | pass | `cli-extended surface check` exits 0 (manifest, spec region, 94 reviewed cases, no open finding) |
| AC-18 syntax complete | pass | `syntax_complete: true` in `docs/cli-surface.json` |
| AC-19 skills packaged | pass | `fleetd/skills/fleetd-campaigns` as package data, no checkout install tree (`tests/test_cli_skills.py`) |
| AC-20 doctor | **manual, deferred** | needs a doctor only when it has an environment to verify. `facts collect` already *is* the environment report for a host (it emits `host-facts/1` with a reason for every unavailable leaf), and the controller's environment is the runner checkout it already refuses to guess. A separate `fleet doctor` would duplicate both; revisit if a third environment appears. |
| AC-21 contract tests | pass | `tests/test_cli_characterization.py` pins the pre-migration contract; `tests/test_cli_review_cases.py` replays the reviewed catalog |
| AC-22 pytest plugin | pass | `pytest_plugins = ["cli_extended.pytest_plugin"]` in `tests/conftest.py`. A focused run — a single test file, or the `campaign-contract` lane, which collects only `tests/test_campaign.py` — passes `--cli-case-partial`; `r0-r1` collects the whole suite and stays strict. |
| AC-23 invoker helpers | not applicable | fleetd's tests do not wrap `subprocess` for the CLI; they call `main()` and, where a real process matters, the library's helpers |
| AC-24 dependency declared | pass | `dependencies = ["cli-extended>=0.5.0"]`, no `package-dir` mapping |
| AC-25 no path hacks | pass | no `sys.path`/`PYTHONPATH` insertion in project sources |

## Collect and validate host facts

The facts command is read-only. A fixture root reads only the supplied files
and does not query the calling machine's systemd:

```sh
fleet facts collect --root ./host-root > host-facts.json
fleet facts validate host-facts.json
```

On a host, `fleet facts collect` requires `/etc/fleetd/facts.toml`. The file is
host-owned and must declare schema version 1, the control-plane slice, policy
limits, and source paths. It may set `control_plane.profile_reserve_bytes` to
the fixed fallback for that host profile; if omitted, fleetd uses the 768 MiB
compatibility profile constant. `--root DIR` maps both this config path and the
config's absolute source paths beneath `DIR`; tests can collect facts without
reading the calling machine. The Geekbench JSON path is host-owned config;
fleetd reads the record and never invokes Geekbench. It exports only the
matching model/count score fields. A missing, stale, or invalid record leaves
`speed` as `null` with a reason. The JSON schema version is `host-facts/1`.

```toml
schema_version = 1

[paths]
geekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"
host_setup_env = "/etc/mdt/host-setup.env"
guard_config = "/etc/cgprofile/guard.toml"

[control_plane]
slice = "ctl.slice"
profile_reserve_bytes = 805306368

[policy]
overcommit = 1.5
oversubscribe_max = 1.25
psi_refuse_some_avg10 = 25
max_lane_containers = 8
```

`overcommit`, `oversubscribe_max`, and the PSI refusal threshold are explicit
host policy inputs, so an incomplete file fails with a field-specific error.
The host role is read from `host_setup_env`, which points to the existing
host-owned `HOST_ROLE` setting. `max_lane_containers`, `guard_state`, and
`ksm_optin_shim` may be omitted; their facts stay `null` with a reason. The
collector reads guard config plus lexically ordered `.toml` drop-ins for its
digest and effective `enabled` value. It reports but does not apply guard
rules. It reads cgroup v2 controllers, configured slice presence and
`cpu.weight`, PSI avg10, swap counters, and KSM sysfs values. It also reads
`MemoryMin` for the fixed, bounded `CONTROL_PLANE_SERVICE_UNITS` list in
`fleetd.contracts` with `systemctl show -p MemoryMin`. A fixture root does not
invoke systemctl; its service entries remain null with reasons. `0`,
`infinity`, unset, and failed queries are not reported values. `memory.for_lanes`
is raw `MemTotal`. Planning sums the service values only if every listed unit
reports a positive value; otherwise it uses the per-profile fixed constant and
reports `reserve_source = "profile-constant"` plus `reserve_missing_services`.
When every unit reports, `reserve_source = "service-memory-min"` and the
reserve is their sum. The collector does not read the parent slice's
`memory.min`; older host facts may carry `control_plane.memory_min_bytes`, which
the contract accepts for compatibility and the planner ignores. KSM
swappiness-argument support is unknown because the collector makes no write
probe.

## Plan a candidate set

The collector records a reason when the host role source or
`max_lane_containers` is unavailable. Planning requires both values, so
collection can succeed while the planner refuses an incomplete host
configuration. Unknown role strings are also rejected; only `dedicated-dev`
and `shared-prod` are accepted.

The candidate table identifies candidates and their source files. Candidate
IDs must be the lowercase 64-character SHA-256 digests emitted by native
Assay R2. Each candidate may carry its own measured RAM, elapsed seconds,
measurement host, and retry evidence. Measurements are optional; absent
values fall through to same-file and lane medians, then the lane demand
declaration.

The demand file may declare the Assay baseline wall time in seconds. Fleetd
adds this value to the first stall window; if it is absent, fleetd uses
completed baseline measurements from the same host.

```toml
schema_version = 1

[lanes.r2.demand]
unit = "candidate"
instance_memory = "250m"
unit_memory = "120m"
unit_memory_high = "600m"
unit_seconds_ref = 95
efficient_upper_bound = 2
baseline_seconds = 400
```

The input envelopes are closed and versioned. Candidate files use
candidate-table/1; host files use fleet-host-set/1. The host envelope
contains { "id", "facts", "other_running" } per host, with optional f_h.
facts is a validated host-facts/1 record from fleet facts collect.
other_running is always explicit (use [] when empty) and each row gives the
job id, expected memory charge, worker count, and container count. Every
running name present in facts.jobs must be represented. f_h defaults to 1.0
and must be in [0.5, 2.0]. The files
[candidate-table.valid.json](../tests/fixtures/candidate-table.valid.json)
and [fleet-host-set.valid.json](../tests/fixtures/fleet-host-set.valid.json)
show the exact input shapes.

The planner refuses a host whose role is shared-prod or unknown, as well as
hosts with unknown physical lane memory, CPU, or PSI admission facts. Missing
or partial service values, including an absent control-plane slice, do not
refuse the host: the host profile's fixed reserve is applied, and the plan
lists each service without a positive value. The default profile constant is
768 MiB, the former lower bound and above the measured 450–500 MB near-idle hot
RSS on the two 8 GiB hosts. A host profile can provide its own fixed value;
reserve selection does not use `MemTotal`, `MemAvailable`, or measured RSS.
Calibration of profile constants from measured p95 under load remains open as
[FD-007b](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-007b-calibrate-profile-reserve-from-measured-p95-under-load).

```sh
fleet plan r2 \
  --candidates candidates.json \
  --hosts hosts.json \
  --demand lane-demand.toml
```

Normal output is stable JSON with host capacities, assigned candidates,
batches, and ETA. Add `--commands` to print self-contained host sections. Each
section writes one batch's candidate IDs beneath `.assay/fleetd-plan/` and
then invokes the existing Run-Gate selector:

```sh
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand lane-demand.toml --commands
```

The selected candidate files are consumed by Run-Gate as
`--only-candidates FILE`. A section has one command per batch, with
`--jobs` no greater than the planned worker slots. Batches run serially because
selected-only invocations in one checkout share the lane's in-flight record
and Assay state store. A retry command uses `--jobs 1`. Run only the matching
`# host <id>` section from that host's lane checkout; the output has no
transport and running every section in one shell sends them all to that one
machine. `fleet plan` itself writes no files; the copied section creates the
candidate files beneath `.assay/fleetd-plan/`.

Assay reports candidates left out of an `--only-candidates` selection as never
started, so the claim is `PARTIAL` and Run-Gate exits 4 (`BUDGET_EXCEEDED`).
The generated script accepts exit 0 or 4 so the next batch runs, and stops on
any other result. Exit 4 cannot tell a normal partial selection from a batch
that really exhausted the lane budget, so the script does not prove the
selected candidates finished; the whole-lane closeout below executes any that
did not. Exit 2 (for example a crashed candidate) and exit 1 stop the section.
An unplaceable candidate or a host refused by the PSI
admission threshold is reported in the plan with a reason, never silently
dropped. Each batch keeps the design's wave accounting in JSON, while one
selected-only invocation pays the baseline once for that batch.

The planned `--jobs` value is bounded by the lane's efficient upper bound.
Run-Gate can refuse `--jobs > 1` when the lane does not use a supported cheap
clone workspace. Fleetd's host-specific `B_h` is not passed through the
current selected-candidate interface; the command does not enforce that
per-host budget. Runtime admission is still provided by the host and lane's
existing Run-Gate, Assay, and cgroup limits (FD-003). The future Run-Gate
campaign client must pass the host budget into Assay admission or establish an
equivalent enforced bound before treating the planner's wave proof as an
execution guarantee.

## Run and collect a lite campaign

For automated dispatch, generate the plan from a clean Git project. The plan
then includes the full commit and project path that the runner must have in
its persistent checkout. Host IDs in `hosts.json` are local aliases already
configured for the hx wrapper; the repository contains no alias table or host
address. Prepare the runner checkouts and scripts through the existing runner
workflow before dispatch.

```sh
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand lane-demand.toml > plan.json
fleet run plan.json --transport hx --campaign r2-week46
fleet status r2-week46
fleet tick r2-week46
fleet collect r2-week46
```

`fleet run` starts the first available batch on each planned host and returns.
It can be invoked again after a controller restart; the same named jobs are
probed and re-adopted. The plan's host slot limit is respected. `fleet status`
refreshes progress, candidate rate, and the ETA blended from planner and
observed rates. `fleet tick` makes one scheduling step; the caller decides
when to call it again. Fleetd does not run a daemon or a hidden polling loop.

If a host cannot be reached, stall time pauses. More than 30 minutes without
contact marks it `LOST`; the next `tick` re-plans unfinished IDs from both
running and never-started queued batches on the reachable planned host with
the largest alone-capacity. Planned idle hosts are probed and may receive that
work. A batch becomes `STALLED` after the larger of the 15-minute floor,
three times its predicted p95 candidate time on that host, and three times
that host's observed p95 candidate time. The first window also includes the
declared `baseline_seconds` or the host's measured baseline. A batch that
exits, goes missing, or stalls without finishing any candidate becomes
`FAILED`; status shows the reason and collect marks its candidates missing
with the exit code when one is available. A batch that made progress can be
re-queued without an attempt cap.
Matching job names are idempotent. If the plan's sorted candidate list
changes, the digest and job name change; the old job is stopped before
replacement work starts.

The runner stores each candidate list below the selected project in its
persistent checkout. The first detached command writes the full list digest
before starting `run-r2-campaign.sh`; a probe can recover the digest from the
uploaded ID file during that startup window. Each runner job records the
append-only progress file's start and end byte offsets. Probe matches the byte
range and the job's candidate IDs, then uses that job's verdict for candidates
resumed by Assay. This prevents an older campaign's records on the same commit
from counting as new progress. Probe sends file paths instead of large
candidate arrays, resolves the Assay lane from the checkout's `run-gate.toml`,
and reads that lane's `progress-<assay-lane>.jsonl` in the project `.assay`
directory. This is the plain-run layout and does not depend on an instance
`progress-file` pointer.

`fleet collect` writes one combined `fleet-campaign-result/1` JSON file and a
Markdown report under the state directory. Each candidate has one owner, the
earliest finished record; additional host or batch copies are counted as
duplicates and ignored. The report includes totals, per-host wall time and
rate, failed batches, and missing artifacts/candidates. A verdict entry whose
process-tree evidence says `not-started` stays `missing` with a distinct
reason; ordinary `budget_exceeded` and `hung` outcomes remain timeouts. A
transport or contract error marks only that host `ERROR`, and the next tick
continues supervising other hosts. The state directory defaults to
`$XDG_STATE_HOME/fleetd/campaigns` or
`~/.local/state/fleetd/campaigns`; if you pass `--state-dir` to `run`, repeat
it on every later command. Keep this local JSON state outside Git. State
schema 2 records monotonic and UTC timestamps plus the controller boot ID.
Same-boot deadlines use monotonic time; after a reboot they use UTC elapsed
time. A backward UTC correction is clamped to zero and delays detection. A
forward UTC correction counts as downtime and can advance detection after a
reboot.

The campaign client passes its generated `--jobs` and `--only-candidates`
arguments only; arbitrary Run-Gate options such as `--base` and
`--carry-over-main` are outside this MVP. Project paths follow the runner's
relative-path rule and cannot contain `..` within a component. hx timeouts and
unreachable results pause stall time; identity-check and guard refusals (exit
97/98) remain errors instead of being treated as host loss.

MVP-B-lite collects candidate verdicts but does not combine remote Assay state
stores into a strict whole-lane verdict. Use the manual state-copy and
Run-Gate closeout below when that merged store is required. `--merge-manifest`
support, baseline reuse, the restricted forced-command host entry, daemon, and
active guard are tracked for MVP-C (Assay B544, B542; Run-Gate RG-277; CIU-218;
fleetd FD-008/FD-009; cgprofile CP-64).

After all host sections finish, copy each host's whole same-judge Assay state
store to its own directory beneath the final checkout's ignored
`.run-gate/collected/`. The store to copy is the directory each batch run
prints as `run-gate: state directory: <path>`. By default that is
`<repo>/.run-gate/assay-state/<project>/`, where `<repo>` is the checkout that
owns the shared `.git` and `<project>` is the planned lane's project directory
relative to it (a linked worktree inside `<repo>` adds its own path prefix, and
an environment with a declared `state_root` moves the store there), so copy
the printed path rather than reconstructing it. Copy that directory as a
whole, for example to `<repo>/.run-gate/collected/host-small/`. Then run a
whole-lane closeout without `--only-candidates` from the lane's project
directory; Run-Gate merges the copied records and executes any remaining
candidates before writing the complete verdict:

```sh
run-gate r2 \
  --merge-state "<repo>/.run-gate/collected/host-small" \
  --merge-state "<repo>/.run-gate/collected/host-large"
```

Replace `<repo>` and the example host names with your checkout and the hosts
in the plan. A relative `--merge-state` path resolves from the current
directory (the project directory), not from `<repo>`. The source paths must be
directories beneath the checkout or judged worktree. `--batch-multiplier N`
changes the batch target from its default of 4 × worker slots.

Run every host section with the same lane commit and tester image. Run-Gate's
`--merge-state` imports only records for the current judge identity; records
from a different commit or image are ignored and those candidates run again at
closeout. Compare the closeout's candidate counts with the planned list before
using its elapsed time as the campaign ETA.

The host arithmetic uses the sum of all positive service `memory_min_bytes`
values when the complete fixed service list is present. If a service is
missing, zero, or `infinity`, it uses that host's fixed profile constant
(768 MiB when the host config has no override); partial sums are never used.
The legacy parent `control_plane.memory_min_bytes` is accepted but ignored.
`P = memory.for_lanes_bytes - reserve_h`, O = other running charges + one
campaign instance's `instance_memory`, and B = P × overcommit - O. Each host
plan and campaign status record `reserve_bytes`, `reserve_source`, and
`reserve_missing_services` so the input to P is visible. Worker slots are the
CPU-based count after other workers, bounded by the lane's
`efficient_upper_bound`. The manual planner reserves one campaign slot per
host; parallel slots need the future supervisor (FD-006). Shared placement
also applies the physical candidate cap: a candidate never exceeds P - O.
Expected sums in each wave, including an alone or retry wave, must fit B. Overcommit increases
only that sum budget; it never increases a candidate's physical cap.

RAM class is low when a candidate's expected charge is no greater than the
smallest host average budget b = B / W; all other candidates are high. The
planner places constrained and longer candidates first, honors each host's
mix limit where possible, and flags the assignment when no eligible host can
keep the mean within b. It builds batches around 4 × W, interleaving high and
low candidates. A candidate with memory-crash evidence is retried once on the
host with the largest physical alone cap, only if its charge also fits that
host's sum budget.

Elapsed measurements are normalized with the source host's single-core
score. Time is compared only when Geekbench major, exact version, and
baseline score agree. Host ETA applies baseline / single_core × f_h; multi-core
scores are not used. Missing or incomparable timing leaves placement intact
and reports ETA as unavailable with a reason.

The lane demand is a `[lanes.<lane>.demand]` table. Released Run-Gate (25.6.0)
refuses `demand` as an unknown lane key, so do not add it to a project's
`run-gate.toml` yet; keep it in a separate TOML file that you pass to
`--demand`. Run-Gate will accept the table in its own file when RG-275 lands
(see [RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented)).
The example below is such a standalone file:

```toml
schema_version = 1

[lanes.r2.demand]
unit = "candidate"
instance_memory = "250m"
unit_memory = "120m"
unit_memory_high = "600m"
unit_seconds_ref = 95
efficient_upper_bound = 3
```

Keep `efficient_upper_bound` at or below the native R2 lane's
`judge.mutation.jobs_max`. The standalone planner validates and uses its demand
file but cannot derive that authoritative lane value yet; the follow-on Run-Gate
client will derive or refuse a mismatch (see [FD-002](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-002-demand-upper-bound-is-not-joined-to-assay-inventory)).

The schema accepts `candidate`, `worker`, and `run`, but this R2 candidate
planner currently requires `unit = "candidate"`; it refuses the other valid
schema units until their placement meaning is defined. Memory accepts positive
integer bytes with `B`, `k`, `m`, `g`, `kib`, `mib`, or `gib` suffixes.
See the [specification](SPEC.md) and [design choices](DESIGN-GUIDE.md).

## Validate guard rules

```sh
fleet guard validate /etc/cgprofile/guard.toml
```

This validates one rule file. It does not load drop-ins, signal cgprofiled,
freeze cgroups, or reclaim memory.
