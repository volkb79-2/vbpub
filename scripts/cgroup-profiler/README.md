# cgroup-profiler

Profile what a container or cgroup actually does over time — CPU, memory, IO,
swap, pressure — resolve what its **effective** limits are (the whole ancestor
chain, not just the leaf), timestamp phase boundaries so activity can be
correlated with the numbers, and produce an interactive report at the end.

It also watches *neighbours*. The question that produced this tool was not
"how much memory does my gate use" but **"when my gate runs, does the
production game server lose its anonymous pages?"** — so profiling a subject
while observing a victim is a first-class mode, not a workaround.

```bash
pip install cgprofile           # the wheel provides cgpctl (client) and cgpd (daemon)
cgpctl --version                # cgpctl <version>
cgpctl doctor                   # what can I reach?

cgpctl run --phase gate -- ./gate.sh                   # profile one command locally (direct mode)
cgpctl start --target slice:dev-gates.slice            # or ask the host daemon to profile a target
```

`cgpctl --version` is the documented operator identity probe. It prints the
installed wheel version (derived from the `cgprofile-vX.Y.Z` release tag; an
untagged build is `0.0.0-dev`). `cgpctl` and `cgpd` are built on cli-extended:
help, usage and error output at every depth begin with the matching identity
headline, and the reviewed command surface is [`docs/CLI-SPEC.md`](docs/CLI-SPEC.md).
The 1.x commands map to `cgpctl` verbs in the
[migration table](docs/CONSUMERS.md#migration-table-1x-command-to-cgpctl-verb).

## Product overview

### 1. What it is

Two programs from one wheel. `cgpd` is the always-on host daemon, a native
systemd service that samples cgroups, owns DAMON, stores sessions, and answers
a Unix-socket protocol. `cgpctl` is the client: it talks to the daemon, renders
reports, and also profiles one command locally without any daemon. There is no
container, no Docker use by the daemon, and no helper image. Normative
documents: [`docs/CGPCTL-SPEC.md`](docs/CGPCTL-SPEC.md) (command line),
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) (wire), [`docs/NATIVE-DEPLOYMENT.md`](docs/NATIVE-DEPLOYMENT.md)
(install), [`DESIGN.md`](DESIGN.md) (architecture).

### 2. Commands

`cgpctl [--socket PATH] [--json] <verb>`; the full client table, flags, JSON
shapes and exit codes are in [`docs/CGPCTL-SPEC.md`](docs/CGPCTL-SPEC.md)
section 2.

| verb | what |
|---|---|
| `status` | daemon and client versions, contracts, uptime, slots and settings in effect |
| `host` | host facts: load, memory, pressure, observed slices, gates slice and DAMON |
| `sessions` | one row per session; `--state live\|done\|all`, `--mine` |
| `show` | live counters of a live session, or the summary of a finished one |
| `start` | start a daemon session on a container, cgroup or slice; `--target`, `--scope`, `--place`, `--on-stall`, `--dry-run` |
| `stop` | stop and finalize a session; prints the summary |
| `watch` | stream readings until the session ends; `--watch-interval` |
| `report` | export a stored session and render it locally; `--out`, `--html-only`, `--md-only`, `--run-dir` |
| `prune` | apply retention now; `--keep`, `--keep-days`, `--dry-run`, `--yes` |
| `run` | profile one command locally, direct mode only; `--phase`, `--out-dir` |
| `mark` | append a phase mark to the current run; stdlib-only, callable from inside a gate |
| `doctor` | diagnose client access, daemon, observer endpoint and runtime; `--check` |
| `config` | `config show`, `config set KEY VALUE [--persist]`, `config reset` (uid 0 for writes) |
| `skills` | install, check, list or remove the packaged agent skill |

`cgpd [--config PATH] <verb>` is root-only for service changes and read-only
for inspection ([`docs/CGPCTL-SPEC.md`](docs/CGPCTL-SPEC.md) section 3):

| verb | what |
|---|---|
| `run` | explicit service entrypoint: serve systemd-passed sockets; `--self-bind` is for tests and development |
| `install` | fetch pinned wheels, render config and systemd resources, drain before restart; repeatable `--add-user LEVEL=USER`, `--enable`, `--no-enable`, `--allow-downgrade` |
| `uninstall` | stop and remove the service; `--remove-groups` and `--purge-data` are opt-in |
| `doctor` | check config, units, groups, socket nodes, state, service, access and foreign rules |
| `unit` | `unit print` renders the service, slice, two socket units and sysusers file; `unit check` reports drift |
| `config` | `config check` validates the config file without starting anything; `cgpd -t` is the same exit-code check for service preflight |
| `reload` | signal the running daemon to reload; `--reset-overrides` also drops runtime overrides |

After installation, run the service CLI from the active versioned venv at
`/opt/cgprofile/current/venv/bin/cgpd`. The installer links the client command
`cgpctl` into `/usr/local/bin` but does not add a global `cgpd` symlink.

The daemon reads `/etc/cgprofile/cgpd.toml`, plus uid-0 runtime overrides.
A missing file means all defaults. Unknown keys are refused. Socket settings
are fixed and require a service restart:

| key | default | change applies |
|---|---|---|
| `sessions_dir` | `/var/lib/cgprofile/sessions` | restart |
| `sockets.control` | `path=/run/cgprofile/control/cgprofile.sock; group=cgprofile-control; mode=0660` | restart |
| `sockets.observer` | `path=/run/cgprofile/observer/cgprofile.sock; group=cgprofile-observer; mode=0660` | restart |
| `damon_lock` | `/run/cgprofile/state/damon.lock` | restart |
| `proc_root` | `/proc` | restart |
| `busctl` | `busctl` | restart |
| `export_max_bytes` | `268435456` | reload |
| `max_exports` | `4` | reload |
| `max_watchers` | `32` | reload |
| `interval` | `1.0` | reload |
| `damon_default` | `"on"` | reload |
| `keep_sessions` | `200` | reload |
| `keep_days` | `14` | reload |
| `max_sessions` | `16` | reload |
| `observe_slices` | `[]` | reload |
| `gates_slice` | `"dev-gates.slice"` | reload |
| `allow_uids` | `[]` | reload |

### 3. Sockets

| socket | owner and access | speaks |
|---|---|---|
| `/run/cgprofile/control/cgprofile.sock` | parent `0755 root:root`; socket mode `0660`, group `root:cgprofile-control` | control protocol contracts 1 and 2 ([`docs/PROTOCOL.md`](docs/PROTOCOL.md)) |
| `/run/cgprofile/observer/cgprofile.sock` | parent `0755 root:root`; socket mode `0660`, group `root:cgprofile-observer` | observer contracts 2 and 3: resource counters and measurement windows ([`docs/PROTOCOL.md`](docs/PROTOCOL.md#8-native-r2-resource-observer-rg-89)) |

Each systemd socket unit has one `ListenStream` and passes one named fd to
`cgpd.service`. Systemd owns the socket nodes across daemon restarts. The
Both level groups must be dedicated: neither can be named `docker`, resolve to
GID 0 or the docker GID, or share a GID with the other cgprofile level. The
installer and config checker refuse these values from flags, saved records,
existing config, and wizard answers, with no override. Cross-service group
uniqueness belongs to host-setup. Repeatable `cgpd install --add-user
LEVEL=USER` flags add existing users with `gpasswd -a`; the installer records
and keeps additions, and uninstall never removes memberships. `allow_uids` can
further narrow control access. See the [access-group design](docs/DESIGN-GUIDE.md#dedicated-groups-for-each-socket-level)
and [install examples](docs/CONSUMERS.md#install-and-run-the-native-service).

The control socket is one carrier: there is no `exec` carrier. A version reply
looks like this (the `cgprofile` field is the installed version):

```json
{
  "ok": true,
  "contract": 1,
  "cgprofile": "2.2.0",
  "daemon": {
    "name": "cgprofile-host-daemon",
    "started_at": "2026-09-12T10:15:00Z",
    "damon": "available",
    "damon_default": "on",
    "sessions_live": 0,
    "max_sessions": 16
  },
  "resource_observer": {
    "contract": 2,
    "contracts": [2, 3],
    "path": "/run/cgprofile/observer/cgprofile.sock",
    "listening": true
  }
}
```

`damon: "available"` is a capability probe, not proof that the kernel accepted
a monitoring context; see the
[design guide](docs/DESIGN-GUIDE.md#damon-availability-is-not-session-readiness)
and the [troubleshooting note](docs/CONSUMERS.md#host-damon_stat-exclusivity).

The config and client defaults use only these new paths. Install refuses old
config keys, foreign host-setup rules, mismatched runtime directories, old
mdt installer manifests, and incomplete install markers. It prints the manual
removal list; it never migrates or repairs old state in place. The exact
reasoning is in the
[systemd socket activation design](docs/DESIGN-GUIDE.md#systemd-owns-the-service-sockets).

### 4. Consumers

* **Run-Gate** starts, watches and stops sessions over the control socket and
  mounts only the observer level directory into a native-R2 tester container.
  Integrations use the installed Run-Gate wheel and its SemVer identity;
  copied `run-gate.py` scripts are not a supported consumer. See the [rationale](docs/DESIGN-GUIDE.md#run-gate-is-an-installed-semver-consumer)
  and [adoption steps](docs/CONSUMERS.md#attach-a-run-gate-lane).
* **Assay** reads event counters through the observer socket
  ([adoption](docs/CONSUMERS.md#native-r2-resource-observer)).
* **mdt host-setup** becomes a consumer in phase 2. Phase-1 releases are not
  deployed to cockpit hosts; keep `CGPROFILE_WHEEL` below `2.2.0` until the
  host-setup update and per-host cutover are complete. See
  [`docs/NATIVE-DEPLOYMENT.md`](docs/NATIVE-DEPLOYMENT.md).
* **Devcontainers and cockpits** mount level directories after cutover; they
  do not mount the whole runtime root. See [`ATTACH-GUIDE.md`](ATTACH-GUIDE.md).

### 5. Access

The control and observer socket groups are separate. The daemon identifies
control peers by `SO_PEERCRED`; `allow_uids` narrows control access further
(uid 0 is always allowed). Observer access is read-only and does not grant
control access. Sessions are owned by the uid that started them; only uid 0
can stop or export another uid's session. See
[`docs/PROTOCOL.md`](docs/PROTOCOL.md#4-authorisation-on-the-socket-81).

### 6. Protection

`cgpd.service` runs inside a memory-bounded `cgprofile.slice`, and refuses to
start when its cgroup is not under that slice
([`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md#the-daemon-proves-the-slice-it-is-in-not-a-slice-by-name)).
Optional placement puts a lane into a delegated scope beneath the gates slice;
`cgroup.kill` is written only for an exact container cgroup proven beneath
that slice, or an exact placed leaf. The daemon never signals a numeric PID.
An upgrade drains live sessions before restarting the service
([`docs/NATIVE-DEPLOYMENT.md`](docs/NATIVE-DEPLOYMENT.md)).

### 7. Install

Bootstrap `cgpd install` from the cgprofile and cli-extended wheels, each
pinned by version and SHA-256. The installer creates an immutable versioned
venv, validates the config and rendered units, atomically updates `current`,
and waits for the selected version to answer. It supports a TTY wizard and
explicit flags for unattended installation. Install and uninstall steps,
manual cleanup for an old host-setup install, doctor checks and VM acceptance
are in [`docs/NATIVE-DEPLOYMENT.md`](docs/NATIVE-DEPLOYMENT.md). The design
choices are in the [socket activation guide](docs/DESIGN-GUIDE.md#systemd-owns-the-service-sockets).
Release identity (wheel version, tag, checksum) is described in the
[release identity design](docs/DESIGN-GUIDE.md#release-identity).

A CMRU release also publishes a **GitHub Release body** for `cgprofile-v<version>`: it
carries that version's dated section of `CHANGES.md`, including any pending
`[Unreleased]` and migration notes, which CMRU folds into the tagged candidate. From 2.0.0 the
release is the wheel only (the OCI image is no longer published): the body is written by
`cmru handler wheel-publish`, the only command of the push step, which uploads the wheel,
its sha256 sidecar and `latest.json`. `cmru release --dry-run` previews that exact body, and
CMRU refuses a second release-body handler before any release side effect. See the
[design](docs/DESIGN-GUIDE.md#the-github-release-body-for-the-published-wheel) and the
[pasteable preview](docs/CONSUMERS.md#preview-the-github-release-body).

## Where to read next

- **[`ATTACH-GUIDE.md`](ATTACH-GUIDE.md)** — how to profile a gate in any repo.
  Start there if you want to use it.
- **[`DESIGN.md`](DESIGN.md)** — architecture and module contracts. Start there
  if you want to change it.
- **[`docs/CONSUMERS.md`](docs/CONSUMERS.md)** — pasteable probes and adoption
  steps for the daemon and Run-Gate.
- **[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md#daemon-safety-and-placement)**
  — why the always-on daemon owns only its measured state and how its contract
  stays fail-closed.

Native R2 takes its changed-lines base from the gate request. Supply the
recorded comparison commit with `--base` for `r2` and the full `gate`, and
inspect the Assay plan before a long run. Native R2 runs one mutant at a time
because its resource event counters belong to the shared tester cgroup; an
incomplete 24-hour window resumes on the same exact tree. See the
[design rationale](docs/DESIGN-GUIDE.md#mutation-comparison-base) and the
[exact commands](docs/CONSUMERS.md#run-the-p1-review-gates). CMRU's release gate
runs only `r0-r1` and `r3`; the complete Run-Gate `gate` lane also includes the
24-hour `r2` campaign. There is no completed full-source R2 verdict for the
current tree. See
[release qualification](docs/DESIGN-GUIDE.md#release-qualification-and-the-full-r2-gate).

## What you get

| file | |
|---|---|
| `report.html` | interactive: zoom, pan, hover crosshair across every series, phase bands, event markers, resolution selector. One self-contained file, no server, no network. |
| `report.md` + `charts/` | static twin for pasting into a ledger or a PR |
| `samples.jsonl` | raw samples, for your own analysis |
| `events.jsonl` | what the profiler flagged, with the numbers behind each one |
| `manifest.json` | targets, host facts, and the effective-limit snapshot |

A daemon session stores `samples.jsonl.gz`, `events.jsonl`, `host.jsonl`,
`manifest.json`, `summary.json`, and `damon.jsonl` only when DAMON samples were
persisted; `cgpctl report ID` renders them locally from an `export`, so the
daemon process never imports the report libraries.

## The parts worth knowing about

**Effective limits, not declared ones.** A container's `memory.max` is rarely
the number that binds it. The profiler walks the ancestor chain and reports
which cgroup actually imposes each ceiling. It also reports `memory.min` and
`memory.low` twice — once as they hold *without* `memory_recursiveprot`, once
as they would hold with it — because a mount missing that flag silently
discards protection an ancestor declared, and nothing else on the host tells
you.

**Adaptive sampling.** 250 ms while anything is moving, backing off to 2 s when
quiet, snapping back to hot on any event. Designed to be run on a host that is
already short of memory: the collector is standard-library only, holds no
series in memory, and does all analysis after the run.

**Phases three ways.** Explicit marks (`cgpctl mark "restoring db"`, callable
from inside a gate container), wrapper-derived boundaries for gates that know
nothing about the tool, and changepoint detection over the series so unlabelled
regime shifts still get timestamped.

**Targets resolve on the client.** `cgpctl start --target container:NAME`
resolves a name to a full container ID with `docker inspect` when the Docker
CLI is reachable; otherwise use `containerid:`, `cgroup:` or `slice:`. `self`
means the container that invoked the CLI. `pid:N` carries the caller's
namespace identity and process start time, and the daemon accepts exactly one
live match or refuses; it never guesses from a numeric PID
([design](docs/DESIGN-GUIDE.md#private-namespaces-and-host-views)).

**Nothing at run time.** The report dependencies live in the optional `report`
extra of the wheel. No code path installs, downloads, or fetches while
profiling — that would add exactly the load the tool exists to measure.

**DAMON is optional evidence.** Setup, collection or cleanup failure disables
only the DAMON series; ordinary profiling and the wrapped command continue. The
one-shot collector and the daemon pool coordinate through a host-shared advisory
lock, which does not serialize unrelated privileged DAMON tools
([design](docs/DESIGN-GUIDE.md#damon-availability-is-not-session-readiness)).

## Relationship to the neighbours

- `scripts/damon-analysis/` — DAMON working-set analysis. The wheel vendors the
  `SysfsInterface` and `Classifier` it uses to add a hot/warm/cold breakdown
  alongside the counters.
- `mdt-cockpit/host-setup/` — owns the host's dev-tier
  slice governance (`dev-interactive`, `dev-background`, `dev-gates`,
  `dev-buildkitd`). This tool measures those tiers; it does not manage them.
  Proposals in the report are phrased as changes to *that* configuration.

## Requirements

Linux with cgroup v2 and Python 3.11+. The daemon needs root and systemd; the
client needs only the socket's group, or nothing for `run`, `mark` and
`report --run-dir`.
