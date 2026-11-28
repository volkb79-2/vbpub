# Using fleetd

## Install the CLI

The `fleet` command is registered with the shared `cli-extended` CLI library.
That library is a real wheel dependency of `fleetd`; it is never vendored and
never put on `sys.path`. `cli-extended` is published on GitHub Releases only
(the bare name is unclaimed on PyPI), so install both wheels from the same
directory:

A managed vbpub host installs it with the runner script, which verifies both
wheel hashes, installs into `/opt/fleetd/<version>/venv` and links
`/usr/local/bin/fleet`:

```sh
wheel_dir=/var/cache/host-wheels
fleetd_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/fleetd-<version>-py3-none-any.whl.sha256")
cli_extended_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/cli_extended-0.5.0-py3-none-any.whl.sha256")
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version <version> --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256" \
  --check
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version <version> --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256"
fleet --version
```

It never installs into the system Python, which Debian 13 refuses (PEP 668);
`--check` writes nothing and exits 1 when that exact wheel hash is not installed.

Without the runner scripts, install both wheels by hand into a venv of your own:

```sh
python3 -m venv ./venv
./venv/bin/python -m pip install --no-index --no-deps cli_extended-0.5.0-py3-none-any.whl
./venv/bin/python -m pip install --no-index --no-deps fleetd-<version>-py3-none-any.whl
./venv/bin/fleet --version
```

With a wheelhouse that holds both files, one command does the same work:

```sh
./venv/bin/python -m pip install --no-index --find-links ./wheelhouse fleetd-<version>-py3-none-any.whl
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

A host that runs only `fleet facts collect` needs the fleetd wheel and its
declared `cli-extended` dependency, plus nothing from this repository. The
separate `fleetd-agent` entry point is also in the wheel; its runtime modules
import only the Python standard library. `fleet facts collect` reads
`/etc/fleetd/facts.toml` and the host's own `/proc`, `/sys`, `systemctl`
service properties and Geekbench record; all of those are host-owned and
absolute (see [the facts config below](#collect-and-validate-host-facts)).

The campaign commands are the *controller's*: `fleet run`, `status`, `tick` and
`collect` launch jobs through the controller kit's identity-checked `hx`
wrapper, which must sit beside a repository checkout. A facts-only host needs
neither the wrapper nor a checkout (FD-023 tracks the installed-wheel path for
the wrapper).

### Host agent packages C3a, C3b-1 and C3b-2a

`fleetd-agent serve --socket-path /run/fleetd-agent/agent.sock` binds a local
Unix socket, while `fleetd-agent serve --listen-fd 3` accepts an inherited
listening descriptor. The direct path form requires an existing real runtime
directory and never creates or removes that directory. `fleetd-agent
forced-command --group GROUP` serves one framed request on the ssh forced
command's stdin/stdout. Package C3a supplies the protocol, admission wiring and
CLI.
C3b-1 adds the standard-library host executor and worker, guarded by the optional
`executor_enabled` key described below. When enabled, after scope start is
confirmed, the worker owns the admission flock in one transient systemd scope
per job. The root supervisor runs checkout, seed unpack, lane, collection and
other repository-content operations as the non-root `runner_user` required by
the enabled executor policy.
C3b-2a adds the legacy-job and Docker inspection providers. C3b-1 does not
install or enable host services; the systemd units, socket group and mounts
belong to C3c. Executor/reboot recovery remains follow-up work. Upload object
collection is available but disabled by default.

The optional closed-schema `upload_collection_enabled` key accepts only a TOML
boolean and defaults to false. Enable it only after reviewing host retention
behavior. Collection runs when a new put would exceed the store cap, preserves
objects named by durable job requests, and refuses the put without deleting
anything if the reclaimable set cannot make it fit. Re-uploads count toward the
cap and do not trigger collection. An uploaded object can be collected before a
later submit names it; a missing seed/dev/candidate object makes submit refuse
with its name, and the controller can upload it again. With collection
enabled, each `in.bundle` put (including a repeated put) records under the
upload-store lock, in private state outside `uploads/`, the same-group job ids
whose `request.json` is already published. A bundle is collectable only when
all group jobs have valid terminal book records and at least one terminal job
was published after the latest put. Missing, unreadable, malformed, or
non-private records keep the bundle and do not refuse collection; collection
removes a valid record. A bundle with no later submit or no usable record stays
until manual removal and counts against the cap. Over-cap refusal reports the
bytes held by unsubmitted bundles. A different same-group job submitted after
the put can make the bundle collectable once every group job is terminal.
Seed, dev, and candidate objects keep today's behavior: submit refuses a
missing object by name and the client re-uploads it. A missing bundle is not
detected by submit; a job whose checkout needs it fails with
`CHECKOUT_FAILURE`. `have` continues to list repo checkout refs and does not
report upload presence. A replay collected while its request body is streaming
is committed again or refused. Collection preflights both store-cap
and disk-floor deficits and deletes nothing if the reclaimable set is too
small. A floor-only breach refuses without collection, and the floor is
rechecked after collection. For a valid durable `request.json`, executor and
worker outputs beside it do not block collection; the request contents and
admission-book state determine which uploads remain protected. Seed/dev/
candidate collection before submit is by design (FD-076).

The optional closed-schema integer `lane_log_max_bytes` defaults to 268435456
(256 MiB) and accepts 1048576 through 4294967296. The worker counts merged
stdout and stderr and bounds `lane.log`; overflow stops the lane through the
normal stop path and binds terminal ERROR after stop proof. The summary scan is
bounded by the same value. No lane runtime limit is imposed. Arbitrary files
created in the checkout remain an open aggregate storage issue (FD-077).

The optional closed-schema key `executor_enabled` accepts only a TOML boolean.
If absent it is false; a present non-boolean refuses policy loading. While
false, `fleetd-agent` wires no executor, and submit, status and cancel preserve
the C3a no-executor refusal before any durable request record or admission
charge. Keep it false until FD-067 and FD-068 are closed and Run-Gate
revision 78 is released and deployed. `mode = "host"` also requires FD-069 to close;
`mode = "exec"` requires the live persistent-runner probe in FD-070. The
executor uses the host worker's `CGROUP_PARENT_DEV_GATES` value for prerequisite
containers and refuses unless it resolves to a loaded systemd slice. Deploy the
disabled binary with the existing policy unchanged; add `runner_user` only in
the later enablement change, together with `executor_enabled = true`.

`/etc/fleetd/agent.toml` is a closed host-owned config. It requires
`schema_version`, `idle_connection_s`, `query_timeout_s`, `legacy_jobs_dir`,
`lease_reconcile_interval_s`, `lease_bind_timeout_s`, `max_connections`, `identities`,
`repo_roots`, `requested_by_clients`, `extra_argv`, `dev_argv`, and
`env_values`; none has a consumer-invented fallback. All four time values are
positive. `runner_user` is required only when `executor_enabled = true`. If
present while disabled, its name is validated but it is never looked up. When
enabled it must resolve to a known non-root host account; a missing key,
unknown name or uid 0 refuses execution. The root
supervisor stays root and drops privileges for every step that touches checkout
content. `legacy_jobs_dir` is an absolute, fully-resolved path with no
default. It must point to the legacy runner user's real `$HOME/jobs`
directory, and the resolved directory must be checked before enabling the
agent. The provider opens every path component from `/` with descriptor-
relative `O_DIRECTORY | O_NOFOLLOW`; any symlink or non-directory component
refuses admission. It charges every entry without a regular `EXIT` marker,
including symlinks, non-directories, unreadable entries and odd names. IDs are
derived from each name, so inserting or removing other entries does not change
them. It lists the pinned directory twice, retains entries judged live in the
first pass, judges second-pass-only names by the same marker rule, then requires
the configured path to reopen to the same device and inode. These listings
are a point-in-time observation: `run-job` does not take `queue.lock`, a job
created after the final listing is not seen, and the jobs-directory owner is
trusted to write its own `EXIT` markers. Listing errors, a changed root, or
more than 65,536 entries refuse admission with 75. Completed directories
accumulate and must be pruned before the limit. A host reboot can leave a job
without `EXIT` charged until someone removes its directory (FD-064).
`lease_reconcile_interval_s` sets the exact-lease reconciliation
period; there is no default. `lease_bind_timeout_s` is also positive with no
default; it bounds the whole bind frame from grant on the monotonic clock, so
trickling bytes cannot extend it. It has no effect after bind. `max_connections`
is a positive integer with no default and bounds ordinary local connections.
`query_timeout_s` bounds capacity, legacy-job-list, object-inspector and
container-identity inspector calls. A lease name consisting of 12 through 63
lowercase hexadecimal characters is refused at request validation and
admission with `container_name must not be 12-63 lowercase hexadecimal
characters`; existing lease-state loading remains compatible. Production
Docker execution uses exactly
`/usr/bin/docker --host unix:///var/run/docker.sock inspect --type=container
<target>`, cwd `/`, and environment `PATH=/usr/bin:/bin`, `HOME=/nonexistent`,
`LC_ALL=C`; it is argv, never a shell or environment-selected Docker context.
Names must be exact, full IDs are 64 lowercase hex characters, and lowercase
hex targets of length 12 through 63 return `ERROR`. Each output stream is
drained up to 4 MiB with strict UTF-8 decoding. The outer bounded admission
call uses `query_timeout_s` to bound its answer. The provider's monotonic
deadline starts before process creation and bounds output collection and
direct-child reaping after creation returns. Process creation itself cannot be
cancelled; it can finish after the outer call refuses, in which case the
provider immediately attempts to signal the child process group with
`SIGKILL` and makes a bounded reap attempt before returning `ERROR` (FD-066).
On the normal result path, after both pipes reach EOF, the provider waits for
the leader's pidfd to become readable without reaping it. Cleanup runs in
`finally`: every child-created outcome, including `Exception` and
`BaseException` paths before EOF, attempts `SIGKILL` on the process group while
the leader is unreaped, then attempts the bounded reap even if signaling
raises. `ProcessLookupError` means the group is already empty; another signal
or reap failure returns `ERROR`. The pidfd, selector, stdout pipe, and stderr
pipe are each closed independently; a close failure is reported as `ERROR`
after the remaining closes are attempted. Any non-`Exception` interruption
from the body or cleanup is re-raised without wrapping; if several occur, the
first in occurrence order takes precedence over ordinary failures from the
body, signal, deadline calculation, reap, post-reap check, or closes. When no
interruption occurs, ordinary errors keep the existing precedence. A success
requires both pipes at EOF, exit observed before the deadline, and status from
the reap. Expiry or an output-cap breach also uses this cleanup path. The peer
table maps exact UID/primary-GID pairs to a principal and group;
forced-command entries map their trusted group argument to the same identity
shape. Request fields cannot choose either value. Loading the policy refuses
overlapping authenticated group prefixes.

Deploy the required key and provider binary together. Resolve and check the
legacy runner user's real `$HOME/jobs` directory before enabling the agent;
the configured `legacy_jobs_dir` must be that fully resolved absolute path.
Stop the agent, install the new binary, add the `legacy_jobs_dir` key, then
start the agent. For rollback, stop the agent, remove that key, reinstall the
old binary, then start it.

The agent sends and receives four-byte big-endian length-prefixed UTF-8 JSON
objects capped at 64 KiB. Each ordinary connection carries one request; the
client half-closes its write side before dispatch, and `put` does so after its
declared body. The server rejects trailing bytes or a second frame. `lease`
keeps its write side open through bind and the held connection. The idle
deadline applies while reading a request
and while a response write makes no progress; it is not a total transfer limit,
submit wait or run limit, and it does not apply to a lease after grant. An
excess ordinary connection receives exit 3; after a lease admission handle is
acquired, that held connection leaves the ordinary connection count. `probe`
includes `agent_version` and the verb
field capabilities. Clients refuse a major-version mismatch; a minor or patch
mismatch requires the host to advertise the requested verb and every field.
A failure uses the nested `error: {exit_code, reason}` object and has no
top-level `exit_code` or `reason`; success omits `error`. The client looks only
for `error`, so a status `exit_code` is returned as job data. A malformed
`error` maps to exit 70. See the [response schema](agent-response.schema.json).
A probe includes `lease` only when exact-object inspection, the legacy-job
list, and container-identity inspection are configured. C3b-2a wires these
providers from `legacy_jobs_dir` and `query_timeout_s`; the shipped C3a CLI has
none and refuses lease with exit 2.
A configured inspector reconciles pending and bound leases at startup and at
the configured interval. Pre-bind disconnect tombstones before checking the
exact name; bound leases are checked by immutable ID. Only a confirmed absence
releases a charge. The granted-but-never-bound live-connection case has no
deadline in §4.5; its held flock prevents pending reconciliation, so it remains
charged until disconnect and exact-name reconciliation confirms absence.
A missing or refusing local socket returns 3 with no fallback transport.
C3b-1 conditionally wires the executor when `executor_enabled = true`, while
C3b-2a wires the legacy-job and Docker providers. Keep the key false until
FD-067 and FD-068 are closed and Run-Gate revision 78 is released and deployed;
host-mode submissions additionally wait for FD-069. Exec-mode submissions
require the FD-070 live persistent-runner probe. With the key absent or false,
submit, status and cancel retain the C3a no-executor refusal before request
record or charge. C3b-1 does not install or enable host services; see the
executor section below.

Lane declarations and progress files are opened from trusted directory
descriptors with no-follow traversal of every component. Progress replies cap
each range at 8 MiB and return the matching `next_offset`; the client refuses a
larger response body. The host counts Assay candidates only when the opened
progress file is at most 8 MiB; larger files return `done: null` and a
non-empty `done_reason`, while smaller files omit `done_reason`. The scan is
bounded to the opened-size snapshot. Incremental counting remains FD-063.
Terminal status reads only `fleet-admission-terminal/1` and returns the
recorded completion time plus C3b-1's `verdict`, `exit_code`, `lane`, `api`,
`collected_at`, `result_sha256` and `result_size` when present. Older C2 records
remain readable; absent executor fields are unknown. Cancel of a terminal job
refuses with exit 2. For an executed lane, PASS requires agreement among the
request sha, host checkout line, Run-Gate event, exact-lane summary, process
status, and inspected container identity and checkout mount.
The worker observes and persists each `lane-started` container while Run-Gate is
running. A container gone at observation is an error; the short-lane race is
FD-067 and must be closed before enabling submissions. The worker confirms that
its scope contains only itself and that Docker has no running container with
this exact job label before it collects or releases the charge. The strict
persistent exec checkout-mount requirement still needs a real runner probe
(FD-070).

Cancel records intent only. The worker deregisters/thaws through the merged
thrashguard interface, sends TERM and waits for the lane process itself. When
guard presence is true but no callable `deregister` is injected, it keeps the
job in `stopping` with its charge and checkout. Exec-mode cancellation or an
end without the Run-Gate verdict proof cannot establish that the in-runner
process stopped, so it also remains charged and stopping (FD-068). A host-mode
lane shares the runner uid that owns the event file and can modify it (FD-069).

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
| AC-17 surface check | pass | `cli-extended surface check` exits 0 (manifest, spec region, 130 reviewed cases, no open finding) |
| AC-18 syntax complete | pass | `syntax_complete: true` in `docs/cli-surface.json` |
| AC-19 skills packaged | pass | `fleetd/skills/fleetd-campaigns` as package data, no checkout install tree (`tests/test_cli_skills.py`) |
| AC-20 doctor | **manual, deferred** | needs a doctor only when it has an environment to verify. `facts collect` already *is* the environment report for a host (it emits `host-facts/2` with a reason for every unavailable value), and the controller's environment is the runner checkout it already refuses to guess. A separate `fleet doctor` would duplicate both; revisit if a third environment appears. |
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
host-owned and must declare schema version 1, the control-plane slice, the
existing planner policy, and source paths. It may set
`control_plane.profile_reserve_bytes` to
the fixed fallback for that host profile; if omitted, fleetd uses the 768 MiB
compatibility profile constant. `--root DIR` maps both this config path and the
config's absolute source paths beneath `DIR`; tests can collect facts without
reading the calling machine. The Geekbench JSON path is host-owned config;
fleetd reads the record and never invokes Geekbench. It exports only the
matching model/count score fields. A missing, stale, or invalid record leaves
`speed` as `{"v": null, "reason": "..."}`. Collection emits
`host-facts/2`; measured and configured values use `{"v": value}` or
`{"v": null, "reason": "..."}`. `reason` appears if and only if `v` is
null. `schema`, `agent_version`, and `collected_utc` remain plain metadata.
`fleet facts validate` accepts `/1` for this migration release and `/2`, and
prints the schema it validated. The planner, campaign and CLI consumers read
`/2` only; a needed null value refuses with its recorded reason.

```toml
schema_version = 1

[paths]
geekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"
host_setup_env = "/etc/mdt/host-setup.env"
guard_config = "/etc/cgprofile/guard.toml"
guard_state = "/run/cgprofile/guard-state"
checkout_root = "/var/lib/fleetd"
docker_root = "/var/lib/docker"

[control_plane]
slice = "ctl.slice"
profile_reserve_bytes = 805306368

[gate_slice]
slice = "dev-gates.slice"

[policy]
overcommit = 1.5
oversubscribe_max = 1.25
psi_refuse_some_avg10 = 25
max_lane_containers = 8
host_reserve_bytes = 805306368
planning_cap = 5
max_footprint = "large"
max_planned_bytes = 7516192768
max_upload_bytes = 1073741824
max_upload_store_bytes = 10737418240

[policy.footprint_charges_bytes]
tiny = 67108864
small = 268435456
medium = 1073741824
large = 4294967296

[policy.priority_classes_by_principal]
worker = ["dev", "gate", "campaign"]

[policy.disk_floors]
checkout = 1073741824
docker = 2147483648

[policy.admission]
queue_path = "/run/fleetd/queue"
wait_s = 30
settle_s = 5
```

`overcommit`, `oversubscribe_max`, and the PSI refusal threshold are explicit
host policy inputs, so an incomplete file fails with a field-specific error.
The added policy settings in this example are optional facts: omitting one
does not supply a default and emits null with a `not-configured` reason. Install
a fleetd release that accepts `policy.max_upload_store_bytes` before adding
that key to the closed host config; configure it before enabling `put` on the
host. Until it is configured, `put` refuses with exit 2. The
`host_reserve_bytes` setting is emitted once as `memory.host_reserve_bytes`;
the other policy settings remain under `policy.*`. `planning_cap` is the
configured policy value; `cpu.planning_cap` is separately derived from the
gate-slice CPU limit. Checkout and Docker roots enable `disks[]` collection;
without either configured root, the disk list is null with a reason, and a
missing per-disk floor is null in that disk's row.

The optional admission policy keys `aging_s`, `legacy_charge_bytes`, and
`legacy_planned_cores` are accepted by the C3a source. They are positive
seconds, bytes, and cores respectively, and `/2` records their unit and
`/etc/fleetd/facts.toml` source. Omission produces a null fact with a reason;
it does not create an admission default. Add these keys to a host's config only
after installing a released fleetd version that accepts them, because the
config schema rejects unknown keys. In particular, do not copy the keys from
this unreleased worktree into a host config before that release is installed.

The host role is read from `host_setup_env`, which points to the existing
host-owned `HOST_ROLE` setting. `max_lane_containers`, `guard_state`, and
`ksm_optin_shim` may be omitted; their facts stay null with a reason. The
collector reads guard config plus lexically ordered `.toml` drop-ins for its
digest and reads runtime state from its configured source. It reports
`guard_present`, `guard_state`, `config_digest`, and `frozen_units` but does not
apply guard rules. Freeze events are not collected yet, so
`guard.frozen_units` is null with a reason. It reads cgroup v2 controllers,
configured slice presence and `cpu.weight`, PSI, load averages, swap counters,
and KSM sysfs values. It also reads
`MemoryMin` for the fixed, bounded `CONTROL_PLANE_SERVICE_UNITS` list in
`fleetd.contracts` with `systemctl show -p MemoryMin`. A fixture root does not
invoke systemctl; its service entries remain null with reasons. `0`,
`infinity`, unset, and failed queries are not reported values.
`memory.total_bytes` is raw `MemTotal`. Each listed unit's row also carries its systemd `load_state`
(`null` with a reason when it was not queried). Planning resolves one term per
listed unit: the reported positive value, that unit's documented per-service
default when the fact is missing or implausible, or `0` when a declared-optional
unit is absent (`LoadState` `not-found` or `masked`). `reserve_source` is
`service-memory-min` when every term was reported and `per-service-default`
otherwise, with `reserve_missing_services` naming the units that were not
reported. The per-profile fixed constant
(`reserve_source = "profile-constant"`) is used only when no per-service fact
exists at all (every row is a failed query). The collector
reports the parent slice's `memory.min` as `control_plane.memory_min_bytes`; the
planner ignores that field, in `/2` as in older `/1` facts. KSM
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
facts is a validated host-facts/2 record from fleet facts collect.
The planner reads `/2` `cpu.online` and `memory.total_bytes`, and refuses
`/1`; legacy `/1` `cpu.for_lanes` and `memory.for_lanes_bytes` are therefore
accepted only by `fleet facts validate` during the migration release and no
longer affect placement.
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
Run-Gate closeout below when that merged store is required. Assay owns the
manifest and strict store combination (B544/B565) and the baseline cache
(B542); Run-Gate's pass-through is RG-277. The restricted forced-command host
entry (CIU-218), daemon (FD-009/FD-011), and thrashguard job registration
(TG-1/FD-012) remain follow-up work.

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

The host arithmetic uses one term per service in the fixed list: the reported
positive `memory_min_bytes`, that service's documented default when it reports
none (or a value above its documented sane ceiling), or `0` when a
declared-optional unit is absent from the host. A missing mandatory fact is
therefore replaced by its documented floor, not by zero, and the reserve never
falls below the sum of what was actually reported. The fixed profile constant
(768 MiB when the host config has no override) applies only when no per-service
fact exists at all. An implausible report is refused with a reason instead of
being summed, and the per-service provenance appears as `reserve_services`.
The legacy parent `control_plane.memory_min_bytes` is accepted but ignored.
`P = memory.total_bytes - reserve_h`, O = other running charges + one
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

## Declare the campaign scope

The default R2 scope is the lines changed since the tool's previous release
tag: a candidate is in scope only when its start line is an added line of that
diff. Generate the id file Run-Gate consumes with `--only-candidates`:

```sh
assay plan r2 --json > plan.json
fleet scope r2 --plan plan.json --repo . --from-tag tool-v1.4.0 \
  --out .run-gate/r2-candidates.txt > scope.json
run-gate r2 --only-candidates .run-gate/r2-candidates.txt
```

`fleet scope` exits `0` when it produced a scope, `4` when the release changed
no mutation target line (`nothing-to-judge`, and `--out` refuses to write an
empty file), and `3` when a rolling cursor no longer describes the list. Each
of those prints its `fleet-r2-scope/1` document on stdout, so redirect it to
keep the record; a bad argument or a refused `--out`/`--write-cursor` exits `2`
with the reason on stderr and writes nothing.

To run a rolling whole-target baseline instead — every release judging the
next slice of the whole list — commit this file beside the project's
`run-gate.toml`. Without it a project keeps the default:

```toml
schema_version = 1

[r2]
mode = "rolling-whole-target"
slice_size = 40
reserve_percent = 25
cursor = ".assay/r2-scope-cursor.json"
note = "each release judges the next 40 candidates"
```

Then pass the cursor through. The cursor is keyed by **file content**, not by
candidate id: each marked path records the git blob id of the content it was
judged at plus the candidate keys judged there, so an edit to another file
never invalidates the sweep. A release that edits a target file judges that
file **whole** (its candidates are due again), and a file whose content did not
move keeps its marks:

```sh
fleet scope r2 --plan plan.json --repo . --from-tag tool-v1.4.0 \
  --cursor .assay/r2-scope-cursor.json \
  --write-cursor .assay/r2-scope-cursor.json \
  --out .run-gate/r2-candidates.txt > scope.json
```

The scope's `slice` object is the accounting: `changed_files`/`changed_candidates`
(candidates due again because their file content moved), `never_judged_candidates`,
`pending_candidates` (not yet judged in this content), `judged_candidates`,
`retired` (paths whose candidates left the plan, with their reason), and
`new_pass` (a finished sweep restarting). Nothing is dropped silently. The
changed candidates and the sweep share `slice_size`, and `reserve_percent` of
it (default 25, set in `[r2]`, `0` restores the changed-first slice) is
reserved for the non-changed remainder (never-judged and the pending
candidates of a half-judged file, in path order), so a target edited every
release still sweeps (FD-038). The slice also reports `reserve_percent`,
`reserved_candidates`, `deferred_changed_candidates` and `deferred_changed_files`
(each changed file whose candidates did not all fit, with `candidates` and
`deferred_candidates`); a deferred file keeps its mark and continues next
release. Changing the reserve changes what the next release judges.

`--mode changed-lines|rolling-whole-target` overrides the declared mode. A
cursor written by an older producer (`fleet-r2-scope-cursor/1`) is refused with
its migration; `--rebaseline` is that migration: it discards the marks and
starts one new pass from the current content. Reach for it when you *intend*
to restart the sweep, not to make a changed list fit.

An empty rolling scope is `nothing-to-judge` with its reason (exit 4): either
the lane plans no candidate at all, or — the one normal case — the whole target
is empty because nothing was planned. A finished sweep is never reported as
empty: it starts a recorded new pass.

## Size the campaign from measured units

Sizing needs the per-worker unit in the demand file. Keep
`efficient_upper_bound` at or below the native lane's
`judge.mutation.jobs_max`:

```toml
schema_version = 1

[lanes.r2.demand]
unit = "candidate"
instance_memory = "250m"
unit_memory = "120m"
unit_memory_high = "600m"
unit_seconds_ref = 95
efficient_upper_bound = 3
baseline_seconds = 400
worker_memory = "120m"
worker_cpu = 1
instance_cpu = 1
```

```sh
fleet sizing plan.json
fleet sizing plan.json --measurement r2-week46.measurement.json
```

The first call sizes from the lane's declared estimate and reports
`"basis": "declared"`; the second uses a finished campaign's own medians and
reports `"basis": "measured"`. Each shard row states its `candidate_count`,
`jobs`, `estimate_seconds`, `margin`, `budget_seconds` and `outer_budget_seconds`,
so a lane's `budget` and the Run-Gate outer bound come from one derivation. A
measured budget uses the per-candidate mean and a margin derived from the
measured spread (`seconds_statistic`, `margin_source` and `dispersion` say so);
`--margin` overrides that margin. The budget is an estimate: a shard that
reaches it stops, and the remainder continues in a later instance. Override
policy numbers with `--margin`, `--minimum-budget`, `--startup-allowance`,
`--per-candidate-seconds` and `--per-candidate-memory`.

## Record what a candidate cost

```sh
fleet collect r2-week46
fleet measurement r2-week46
```

`fleet measurement` writes `<campaign>.measurement.json` beside the campaign
result in the same state directory. Each planned candidate carries its
`outcome_bucket`, `elapsed_seconds` and `memory_peak_bytes`; the `summary`
keeps medians over `killed` and `survived` candidates and the additive
per-candidate sum fields (`cost_candidates`, `cost_seconds_sum`,
`cost_seconds_mean`, `cost_seconds_stdev`, `cost_seconds_max`) over every
candidate that occupied a worker, `budget_exceeded` and `hung` ones at their
bound. A candidate the cgprofile
observer could not measure keeps `null`, never `0`, and the summary reports
`memory_measured_candidates`, so you can see how much of the number is
observed. The probe and collector preserve this peak from the real progress
record through duplicate ownership. Pass that file to
`fleet sizing --measurement` next time.

Strict shard merging belongs to assay: use `--merge-shard-store` (driven by
run-gate's `--merge-shards N`) with B565's `strict-shard` policy. Fleetd copies shard stores but does not compare
identities or synthesize a merge document. Guard rule validation belongs to
thrashguard (TG-1); fleetd only reports `guard_present`, the configuration
digest and owner-emitted runtime state in `host-facts/2`.
