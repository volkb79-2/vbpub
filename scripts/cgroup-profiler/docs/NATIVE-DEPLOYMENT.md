# cgprofile native deployment: wheel, unit, install, upgrade, migration

**Status.** Design for cgprofile 2.0.0. Replaces the ciu container deployment of
`cgprofile-host-daemon`. The command-line half is [`CGPCTL-SPEC.md`](CGPCTL-SPEC.md), the wire half is
[`PROTOCOL.md`](PROTOCOL.md) section 10. Backlog: CP-41 (wheel), CP-42 (unit), CP-43 (groups), CP-44
(retirement), CP-46 to CP-49 (the 2.0 packages). The host-side installer is mdt host-setup (mdt-cockpit
MDT-055, MDT-048); this file states what cgprofile provides and what host-setup must do with it.

Every number in the limits tables is a **proposal that needs measurement** (section 4.3) before it is
final. Nothing in this design requires a particular host; names such as `dev-gates.slice` are config
defaults, not assumptions.

## 1. Shape in one paragraph

One wheel, distribution `cgprofile`, installed by host-setup into a version-named directory under
`/opt/cgprofile`, run by a systemd service `cgpd.service` inside a bounded `cgprofile.slice`, listening on
`/run/cgprofile/ctl.sock` and `/run/cgprofile/observer/resource-observer.sock` (paths unchanged, so no
consumer bind mount changes). The client `cgpctl` comes from the same wheel and is installed in the
cockpit image. There is no container, no docker use by the daemon, and no helper image.

## 2. The wheel

| Item | Decision |
|---|---|
| Distribution name | `cgprofile` (normalizes to the release prefix, closing the CP-41 name mismatch; the old `cgroup-profiler` distribution is retired, never published as a wheel) |
| Version | scm-derived at build; wheel, installed metadata, `cgpctl --version`, `cgpd --version` and the release tag agree. No literal in `pyproject.toml`, no `CGPROFILE_VERSION` environment input |
| Python | >= 3.11, `tomllib` and `argparse` only on the daemon path |
| Runtime dependencies | `cli-extended>=0.5.1` (floor reason: CLI-EXT-30 collision filter). Nothing else. The daemon venv is stdlib plus cli-extended |
| Extra `report` | `pandas`, `plotly`, `matplotlib`, `scipy`, `ruptures` with `>=` floors and a reason per floor. Not installed by host-setup and not part of the cockpit image's default closure |
| Console scripts | `cgpctl = cgprofile.cli.cgpctl:main`, `cgpd = cgprofile.cli.cgpd:main` |
| Package | `src/cgprofile/` absorbing `lib/` and `cgprofile.py`: `collect` (the stdlib collector and samplers), `daemon` (serve, store, placement, liveness, access), `client` (ctl client, export, report glue), `report` (analysis and renderers, imports the extra lazily), `cli`, `_damon`, `skills/cgpctl/SKILL.md`, `data/` |
| Vendored DAMON | `cgprofile._damon`, a copy of `scripts/damon-analysis` module(s) the profiler uses, with a sync test that fails when the source and the copy differ (recorded decision Q7: vendor, do not publish damon-analysis as its own wheel). The relative-path import (`lib/damon.py`) is deleted |
| Package data | the unit templates for `cgpd unit print` (`data/cgprofile.slice.in`, `data/cgpd.service.in`), the packaged skill, nothing else. No checkout path appears anywhere in the built wheel (a test greps the wheel) |
| Release artifacts | `wheel` added to `cmru.toml` beside `oci-image` for the 2.0.x line: the wheel with a sha256 sidecar and `latest.json`, which the cockpit resolver reads as `cgprofile-latest`. The OCI image is not built for 2.0.0 unless the report tier needs it (decided in CP-44) |
| Install lanes | a clean-venv `pip install --no-index` of the built wheel runs `cgpctl --version`, `cgpctl doctor` (degraded), `cgpd --version`, `cgpd unit print`, `cgpd config check`; a metadata test pins the dependency closure |

`cgpctl mark` runs inside commands that `cgpctl run` wraps. It is reached through the venv's `cgpctl`
script, whose shebang is that venv's interpreter, so cli-extended is importable there; `mark` imports
nothing else beyond the standard library (a test pins it). This closes CP-17's open question about the bare
`python3` collector.

## 3. On the host

### 3.1 Layout

```
/opt/cgprofile/
  2.0.0/                 # one directory per installed version, immutable once complete
    venv/                # python venv; bin/cgpd, bin/cgpctl; installed from the pinned wheel only
    MANIFEST             # wheel file name, sha256, install timestamp, installer id
  2.0.1/
  current -> 2.0.1       # the single mutable pointer, replaced atomically (symlink + rename)
/etc/cgprofile/cgpd.toml       # root 0644, rendered by host-setup (or hand-written off mdt)
/etc/systemd/system/cgprofile.slice   # rendered by `cgpd unit print`, installed by host-setup
/etc/systemd/system/cgpd.service
/var/lib/cgprofile/            # StateDirectory=cgprofile; sessions/ below it
/run/cgprofile/                # created by the host's tmpfiles rule, never by the unit
```

`cgpd.service` runs `/opt/cgprofile/current/venv/bin/cgpd run`. Because the unit names the `current`
symlink and not a version, a version change is "install the new directory, flip the symlink, restart"; the
unit file itself changes only when the template changes (drift is detected by `cgpd unit check`). The
client on a host that wants one is `/opt/cgprofile/current/venv/bin/cgpctl`; host-setup may link it into a
system `PATH` directory. Keep the previous version directory for rollback (section 6.4); host-setup prunes
to the two newest.

### 3.2 Install and pin

Host-setup variables (names are proposals owned by MDT-055):

| Variable | Meaning |
|---|---|
| `CGPROFILE_ENABLE` | `0` / `1`; when `1` the install below runs, when `0` an existing install is left alone and reported by `check.sh` |
| `CGPROFILE_WHEEL` | `<version>@sha256:<hex>`; refused as empty while enabled. The wheel is fetched from the release (or a local path for air-gapped hosts) and its sha256 verified before anything is unpacked |
| `CGPROFILE_DRAIN_TIMEOUT` | drain wait, default `10m` (section 5) |
| `CGPROFILE_ALLOW_UIDS`, `CGPROFILE_GATES_SLICE`, `CGPROFILE_OBSERVE_SLICES`, ... | rendered into `/etc/cgprofile/cgpd.toml` keys of the same names (lower case); the host-setup wizard keeps `host-setup.env` the single operator source on mdt hosts |

The install is idempotent: create `/opt/cgprofile/<ver>`, build the venv from the verified wheel with
`pip install --no-index` plus the cli-extended wheel pinned by the same host-setup mechanism (no network
at service start), write `MANIFEST`, run `cgpd --version` from the new venv and compare with the pin, then
render the config and unit files and flip `current`. Nothing runs from a checkout and nothing is pip-
installed into a system interpreter.

### 3.3 Groups and directories

Unchanged by this design and owned by CP-43 / MDT-055: `/run/cgprofile` is `0770 root:<ctl group>`
(`mdt-docker`, gid 2751, on mdt hosts; `docker` until the regroup lands; any group off mdt, which the
daemon adopts by `stat`), `/run/cgprofile/observer` is `0751 root:cgprofile-observer` with a dynamic gid
that must differ from both docker-class gids. The unit never uses `RuntimeDirectory=` (that would
recreate the mode and group).

### 3.4 Docker

The daemon does not use docker and the unit does not need the docker socket. Docker appears only as an
optional client-side convenience: `cgpctl start --target container:NAME` calls `docker inspect` when the
`docker` CLI and socket are reachable and otherwise accepts only `containerid:`, `cgroup:` and `slice:`.

### 3.5 One daemon per socket

`cgpd run` connects to `ctl_socket` before binding it. If a daemon answers `version` there, it exits 2
with `another daemon already serves <path>` and the answering daemon's version and pid (from
`SO_PEERCRED`). A stale socket file that nobody answers on is replaced as before (CP-34 keeps the
no-steal rule for non-socket files and for sockets that still answer). This makes a forgotten container
or a second unit an explicit failure and guards the container-to-native cutover (section 8). The
observer socket gets the same check.

## 4. The systemd units

`cgpd unit print` renders both units from the wheel's templates; `cgpd unit check` compares installed
files. Host-setup supplies only the install root and the wheel venv path; no checkout path, user name or
host name is read.

### 4.1 `cgprofile.slice`

Top level, a sibling of the host's workload slices, never nested (the single unhyphenated token keeps it
at cgroup root; a hyphenated name would nest under an auto-created parent with `MemoryMin=0`).

| Setting | Proposed value | Why |
|---|---|---|
| `MemoryMin` | 128M | protected reservation; at or below the daemon's measured resident set |
| `MemoryHigh` | 768M | soft throttle for the daemon plus any transient helper |
| `MemoryMax` | 1G | hard ceiling; 1.1.0's refusal to start on an unbounded slice stays and now has a real partner |
| `CPUWeight` | 100 | |
| `IOWeight` | 50 | the watcher must not compete with the workloads it measures |
| `ManagedOOM*` | unset on purpose | an oomd policy that could kill the singleton watcher contradicts the slice's purpose |

### 4.2 `cgpd.service`

| Setting | Proposed value | Notes |
|---|---|---|
| `Type` | `simple` | `cgpd run` binds its sockets before serving; readiness is "socket answers `version`" (a host-setup check, no `sd_notify` dependency) |
| `ExecStart` | `/opt/cgprofile/current/venv/bin/cgpd run` | `--config` only when host-setup places the file elsewhere |
| `ExecReload` | `/bin/kill -HUP $MAINPID` | CGPCTL-SPEC.md section 3.3 |
| `Slice` | `cgprofile.slice` | |
| `User` | root | placement, DAMON sysfs, `cgroup.kill` and `busctl` need it |
| `MemoryHigh` / `MemoryMax` | 512M / 768M | below the slice cap, so the slice never becomes the first limit hit |
| `MemorySwapMax` | 0 | a sampler that swaps stops observing the pressure it exists to see |
| `CPUQuota` | 50% | |
| `TasksMax` | 256 | measure first (section 4.3); handler threads are bounded in code too (CP-33) |
| `IOWeight` | 50 | |
| `LimitNOFILE` | 4096 | |
| `OOMScoreAdjust` | **see 4.4; unset in the first release** | |
| `Restart` | `on-failure`, `RestartSec=2s`, `RestartSteps=5`, `RestartMaxDelaySec=60`, `StartLimitIntervalSec=300`, `StartLimitBurst=5` | a crash loop backs off and then stops; consumers see a missing socket, which CP-31 keeps distinct from a refusing one |
| `TimeoutStopSec` | 20s | covers the 4 s shutdown drain plus joins |
| `KillMode` | `mixed` | SIGTERM to the main process first so the drain runs |
| `PrivateNetwork` | yes | the container had `network_mode: none`; the D-Bus system socket is a filesystem socket and stays reachable |
| `PrivateTmp`, `ProtectHome` | yes | |
| `ProtectSystem` | `strict` with `ReadWritePaths=/run/cgprofile /var/lib/cgprofile /sys/fs/cgroup /sys/kernel/mm/damon` | the DAMON sysfs path is added to the plan's list; confirm by test |
| `StateDirectory` | `cgprofile` | |
| `CapabilityBoundingSet` | starts from what placement via `busctl`, cgroupfs writes, DAMON sysfs writes and `/proc/<pid>` reads of other uids need | shrunk by test; every removed capability is proven by a run, not guessed |
| `NoNewPrivileges`, `RestrictAddressFamilies=AF_UNIX`, `LockPersonality`, `RestrictRealtime` | yes | |

No `Environment=` lines: the daemon reads configuration from the file only, and a stale
`CGPROFILE_*` variable is refused (CGPCTL-SPEC.md section 3.4).

### 4.3 Measurements that gate the numbers

Before any value above is final, a scratch host (not a production-shared one) records, for 1, 8 and 24
concurrent sessions: resident set of the daemon, thread count, CPU, open files, and the slice's
`memory.peak`; plus a deliberate runaway in a test build (a memory balloon, a fork bomb) proving the
service limits contain it, the host and gates slice are unaffected, the service restarts with backoff and
stops after the start-limit burst. The table goes into this file's appendix when measured. The
`cgroup.kill` rule is unchanged and gets a test: no write to any cgroup other than an exact gates-slice
container leaf with `populated 1` is possible from the unit's writable paths plus the code guard.

### 4.4 The OOMScoreAdjust conflict

Two statements in the estate disagree and this design does not paper over it:

* `infra/cgprofile.slice` (and its D-29 rationale) says the daemon "must never itself be the thing that
  gets OOM-killed mid-run", which argues for a **negative or zero** adjust and no oomd policy.
* CP-42's proposal sets `OOMScoreAdjust=` to a moderate **positive** value (+200), so that under a
  host-wide OOM the watcher is expendable before production workloads and gate lanes.

Both are defensible: a killed watcher loses live session data and stalls lanes (run-gate sees a missing
socket), while a killed production workload is the harm the whole slice exists to avoid. The per-service
`MemoryMax` (768M) below the slice cap already bounds the daemon, so a host-wide OOM is not caused by it,
and a positive adjust only decides who dies when something else exhausts the host. **Decision for the
first 2.0.x release: leave `OOMScoreAdjust` unset (0)**, document that the daemon is expendable only in
proportion to its small resident set, and revisit with the section 4.3 measurements and one observed
host-wide OOM event. The value is a template parameter (`oom_score_adjust`, default unset) so changing it
is a template edit, not a code change.

## 5. Upgrade: drain before restart

A restart finalizes live sessions as `daemon-stopped`, which wastes a run-gate lane's profile. Host-setup
therefore restarts only on drift, and only after draining:

1. Compute drift: wanted version differs from `current`, or the rendered unit or config digest differs
   from the installed one. No drift, no restart (a config-only change is a `systemctl reload`, never a
   restart, unless a fixed key changed).
2. Install the new version directory first (no effect on the running daemon), then wait up to
   `CGPROFILE_DRAIN_TIMEOUT` (default 10 minutes), polling `cgpctl sessions --state live --json` as
   uid 0 every 10 seconds for an empty list. New sessions started during the wait extend nothing: the wait
   has one fixed deadline.
3. Empty before the deadline: flip `current`, `daemon-reload` if the unit changed, restart.
4. Not empty at the deadline: **refuse**. Host-setup leaves the old daemon and the old `current` in
   place, prints the live session ids with owner uid and age, exits non-zero, and records the pending
   version. `--force-restart` (an explicit option, never an environment default) proceeds anyway and
   states in its output which sessions it aborted.
5. After the restart, host-setup polls until `version` answers (bounded, 30 s), then asserts
   `cgpctl status --json` reports the pinned version and `config.last_reload_error` null.

The drain wait is read-only toward cgprofile: it never stops a session, and run-gate lanes are not
interrupted by it.

## 6. Install, upgrade, rollback via mdt host-setup

### 6.1 Fresh install

`CGPROFILE_ENABLE=1` plus a valid `CGPROFILE_WHEEL`: groups and tmpfiles (CP-43), the version directory
(section 3.2), config and unit render, `systemctl enable --now cgprofile.slice cgpd.service`, then the
`check.sh` assertions of section 6.3.

### 6.2 Upgrade

Section 5, with the new `CGPROFILE_WHEEL` value. Host-setup keeps the two newest version directories.

### 6.3 `check.sh`

Replaces the directory-and-group-only check with `cgpctl doctor --json` run as root (against the local
socket), asserting: no `fail` check, `daemon.version` equals the pinned version, the group and mode of
both socket directories, `cgprofile.slice` bounded, and `systemd-analyze verify` clean on both units.
Host-setup's `test-render.sh` pins the rendered config and the unit golden.

### 6.4 Rollback

Rollback is an upgrade to the previous pinned version: point `CGPROFILE_WHEEL` at it, run the same drain
and restart path. The previous directory is still present, so no download is needed and `current` flips
back atomically. Config keys the old version does not know would be refused by the old `cgpd`, so each
release documents the keys it adds and host-setup renders only the keys the pinned version supports (a
version-to-keys table in the installer, tested). Rollback past 2.0.0 into the 1.x container is the section 8 rollback.

## 7. What the helper and probe images become

Dropped, with nothing replacing them in the product:

| 1.x artifact | 2.0 |
|---|---|
| Privileged helper container (`CGPROFILE_HELPER_IMAGE`, `CGPROFILE_HELPER_CGROUP_PARENT`, `CGPROFILE_IN_HELPER`, `_collect`) | removed. A caller that cannot see host cgroups starts a daemon session with `cgpctl start` |
| Placement probe container in `tester-unified:local` with the hard-coded `--user 1003:1003` | removed; placement is verified by the daemon on the host |
| `docker logs -f` based `--log-tail` / `--log-match` | removed with the docker dependency |
| `Dockerfile`, `docker-bake.hcl`, `ciu.compose.yml.j2`, the OCI image | retired by CP-44 after both hosts migrate |
| `cgprofile` bash shim, `setup.sh` venv, `venv/` | removed; the wheel's scripts replace them |

## 8. Migration from the ciu container deployment (CP-44)

Window: one release cycle after 2.0.0, dated in the 2.0.0 release notes, never open-ended. No dual
ownership beyond it; the single-daemon guard (section 3.5) turns any overlap into an explicit refusal.

Per host (a test host first, then the shared host in an idle window; the load rules of the host apply):

1. Announce on the host; confirm no live sessions (`cgpctl sessions --state live`, or the 1.x `ctl status`).
2. `ciu down` the `cgprofile` host profile (stops `cgprofile-host-daemon`; its `cgprofile-sessions` named
   volume stays on disk).
3. Install through host-setup (section 6.1). Stored sessions in the old volume are **dropped by default**:
   retention is 14 days and the data are profiling artefacts, not records. An operator who needs them
   copies the volume's `sessions/` into `/var/lib/cgprofile/sessions` before the first start (a session
   without `owner_uid` becomes uid-0-only for `stop` and `export`).
4. Verify: both sockets, groups and modes, `cgpctl status` shows the pinned version, one run-gate lane and
   one `cgpctl start` / `stop` / `report` session succeed.
5. Rollback inside the window: `systemctl disable --now cgpd.service`, then `ciu up --profile host` at the
   last 1.2.x image. The ciu compose and its hand-installed `cgprofile.slice` stay in the tree until the
   window closes, which is why 1.2.x remains deployable.

After the window (CP-44): delete `ciu.compose.yml.j2`, the host profile of `ciu.toml.j2`, the daemon
sections of `ciu.defaults.toml.j2`, `ciu.global*.toml*`, the image build, `infra/cgprofile.slice` and the
hand-install steps of `infra/README.md`, the "Deploy a published daemon image with CIU" chapter of
CONSUMERS.md, and the RUNBOOK hand edits; add a grep test pinning the deletions. Release as 2.0.1 (2.1.0
if anything is added).

## 9. Tests this design requires

* Golden `cgpd unit print` output; `systemd-analyze verify` on Debian 13 (a documented skip where systemd
  is unavailable); no checkout path in either unit or in the wheel.
* Clean-venv wheel install lane and the dependency-closure metadata test (section 2).
* `cgpd run` preflight tests (each refusal), reload tests (valid, invalid, fixed key), single-daemon test,
  group-adoption tests for gids 2751, 999 and 0 and an arbitrary group off mdt.
* Fake-root host-setup install, upgrade, drain-refusal, `--force-restart` and rollback tests (in the
  host-setup repo); native tests run against a scratch socket directory or in a VM harness, never against
  the live daemon.
* The release canary (R3) rewritten for a native daemon; R2 waived for the release wave and recorded.

## 10. Safety rules for the implementing packages

No package starts, stops or reconfigures the live `cgprofile-host-daemon`. Native tests use a scratch
socket directory or a VM. Only the ops package touches a host, and it follows the host's load rule.
