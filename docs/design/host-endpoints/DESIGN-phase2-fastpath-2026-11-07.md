<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-07; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/host-endpoints-and-phase2-fastpath-DESIGN.md -->
<!-- relation: host endpoint design -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Host endpoint records and the phase-2 fast path (DESIGN, rev 1, 2026-11-07)

Author: Opus design agent (read-only; nothing changed, released or run on a host).
Verified against `origin/main` 73f0f840d (a `git archive` export). The local checkout
`<checkout>` is NOT origin/main (foreign commit 8b398ac56, pre-phase-1 content).
All `file:line` anchors below are origin/main unless a report path is given.

Binding inputs: operator rulings 2026-11-07 11:50Z (1 nothing hard-coded, 2 cgp VM first,
3 fast per-host path by reinstall, 4 fastest assay release host), plan
`docs/PLAN-2026-11-HOST-SERVICE-SOCKETS.md` (D1-D6, P-1..P-11), `reports/mdtd-DESIGN.md`
rev 4, `reports/sockets-phase1-RELEASE-PLAN.md`, `reports/div2-reinstall-RUNBOOK.md`.

Decisions taken here (each justified in its section):

| # | Decision |
|---|---|
| E-1 | Each service installer publishes one **endpoint fragment** `/etc/host-endpoints.d/<owner>.env`; a shared stdlib helper (`augeaswrap.endpoints`) renders ONE aggregate `/etc/host-endpoints.env` plus a managed block in `/etc/environment` and `/etc/profile.d/host-endpoints.sh`. Paths AND GIDs live in that one record. |
| E-2 | Consumers read **process environment only**, never the file and never a constant. Unset or empty = error naming the variable and its remedy. Explicit overrides (`--socket`, `RUN_GATE_HOST_DOCKER_GID`, `ASSAY_GATE_HOST_DOCKER_GID`) stay; defaults go. |
| E-3 | In-lane paths are **identity paths**: a level directory is bind-mounted read-only at the same path as on the host; the in-lane variable is assay's existing `ASSAY_RESOURCE_OBSERVER_SOCKET`, set by the mounter. `/run/cgprofile-observer` disappears. |
| E-4 | Fast path = **option C, invoked by the host agent after the reinstall** (no new debian-install-v2 stage now; INST-01 stays in the mdtd track). |
| E-5 | Assay release: `cmru release assay` stays on the controller; its two gate commands go through a new `assay/tools/release-gate.sh` that delegates to `remote-gate.py run <host>` when `ASSAY_RELEASE_GATE_HOST=<host>`. Credentials never leave the controller. |
| E-6 | Phase-1 releases (cgroup-profiler 2.2.0, topos 0.7.0, run-gate 25.3.0) are **never pinned anywhere**; a follow-up round "X-02" (augeaswrap 0.1.0, topos 0.8.0, cgroup-profiler 2.3.0, run-gate 25.4.0; assay 12.1.0 later; cli-extended is not released and stays 0.5.3) supersedes them. VM findings on 2.2.0 fold into 2.3.0. |

---

## 1. How host-setup and its consumers fit together, before and after (operator section)

### 1.1 Today (deployed on <host>, <host> and the controller; phase-1 releases published but not installed anywhere)

```
 mdt-cockpit/host-setup/install.sh   (root, on the host; one script owns everything below)
  |- dev-*.slice units, daemon.json "cgroup-parent", /run/docker-api socket
  |- mdt-buildkitd: socket chgrp docker            units/mdt-buildkitd.service.in:112
  |- /etc/environment "# BEGIN mdt-buildkit" block install.sh:527-560 (+ etc/profile.d/mdt-buildkit.sh)
  |- tmpfiles  /run/cgprofile 0770 root:docker      install.sh:1368-1371, units/mdt-cgprofile.conf
  |- observer  g cgprofile-observer, /run/cgprofile/observer 0751   install.sh:184-280
  |- tmpfiles  /run/topos 0750 root:docker + topos.service drop-in Group=docker   install.sh:1377-1386
  |- scripts/mdt-cgprofile-install.sh  -> /opt/cgprofile/<ver>, cgpd.service      install.sh:1476, :1578-1590
  |       cgpd 2.1.0 self-binds /run/cgprofile/ctl.sock (root:docker)
  |       and /run/cgprofile/observer/resource-observer.sock (root:cgprofile-observer)
  '- scripts/check.sh  checks those trees by literal path            check.sh:463-709

 consumers, each with its own literal path:
  devcontainer.json  bind /run/cgprofile, -v /run/topos, --group-add ${localEnv:DOCKER_GID}
                     (templates/devcontainer.json:96, :222, :242; DOCKER_GID exported by hand :219-220)
  start-cockpit-runner.sh  bind whole /run/cgprofile, --group-add docker   (:14, :19)
  run-gate           PROFILE_SOCKET_PATH_DEFAULT, observer constants (run_gate.py:505-513, :728)
  assay launchers    RESOURCE_OBSERVER_SOURCE=... (tester-unified-gate.sh:38-41, self-qualification-container.sh:25-42)
  cgpctl / topos     ctlwire.py:16, cgpctl.py:57-58, topos cli.py:46, status.py:22, mcp/server.py:39
```

| Thing | Owner today | Where |
|---|---|---|
| Disk, OS, docker install, `daemon.json` keys except `cgroup-parent` | debian-install-v2 | `installer.py:961-1073` (D-F1 split), `_stage1` `:4723` |
| Slices, BuildKit, docker API socket, `cgroup-parent` | host-setup | `install.sh` |
| `/run/cgprofile`, `/run/topos` trees, `cgprofile-observer` group | host-setup (tmpfiles/sysusers) | `install.sh:184-280`, `:1348-1386` |
| cgpd install/upgrade | host-setup (`mdt-cgprofile-install.sh`) | `install.sh:1578-1590` |
| topos daemon | nobody (host-setup "installs no topos service", `check.sh:583`) | |
| Docker GID for consumers | hand export `DOCKER_GID` | `templates/devcontainer.json:219-222` |
| Runner cockpit container | `scripts/netcup/runner/start-cockpit-runner.sh` | `:12-34` |
| ciu | no socket consumer (reads only `CGROUP_PARENT_*` slice names) | `git grep` of `ciu/src`: no hit |
| mdtd | design only | `reports/mdtd-DESIGN.md` rev 4 |

Phase 1 (merged, released, not deployed) already moved the service side: `cgpd install`
(`scripts/cgroup-profiler/src/cgprofile/cli/installer.py:1192`) and `toposd install`
(`topos/src/topos/service/install.py:1070`) own their groups (sysusers), socket units, level
directories and install record `/etc/<svc>/install.toml` (`installer.py:73-74`,
`service/install.py:76-77`), and refuse old host-setup leftovers (P-11, `installer.py:199-345`).
What phase 1 did NOT fix: every consumer still carries the new path as a literal.

### 1.2 After (this design; <host> first, then <host>, both by reinstall)

```
 debian-install-v2 (unchanged): partitions (data-area-v2), OS, docker, daemon.json keys
        |  host agent, after the reinstall
 provision-runner.sh: runner user (uid 1003), /srv/cow
        |
 host-setup install.sh (phase-2-min)          root, once per install/upgrade
  |- slices, daemon.json cgroup-parent, /run/docker-api, mdt-buildkitd (socket group mdt-buildkit)
  |- cgpd install --add-user ...   -> groups cgprofile-control / cgprofile-observer, socket units,
  |                                   /run/cgprofile/{control,observer,state}
  |                                   publishes /etc/host-endpoints.d/cgprofile.env
  |- toposd install --add-user ... -> group topos-observe, socket unit, /run/topos/{observe,state}
  |                                   publishes /etc/host-endpoints.d/topos.env
  |- publishes /etc/host-endpoints.d/mdt-host-setup.env (docker GID, BuildKit, docker API)
  |      via `augeaswrap endpoints publish`; every publish re-renders, under one lock (augeaswrap.endpoints):
  |        /etc/host-endpoints.env                 the ONE host facts file (paths + GIDs)
  |        /etc/environment "# BEGIN host-endpoints" block   (pam_env: ssh, sudo, su, cron)
  |        /etc/profile.d/host-endpoints.sh        (sh/bash login shells: sources the file)
  '- check.sh = cgpd doctor + toposd doctor + augeaswrap endpoints check + cross-service GID rule

 consumers: environment only, fail naming the variable
  start-cockpit-runner.sh  sources /etc/host-endpoints.env -> identity ro binds of the level dirs,
                           numeric --group-add per level, -e for every endpoint variable
  run-gate (in the cockpit) CGPROFILE_CONTROL_SOCKET, CGPROFILE_OBSERVER_SOCKET, MDT_HOST_DOCKER_GID
     '- observer lane: ro bind of dirname(CGPROFILE_OBSERVER_SOCKET) at the same path,
        --group-add <socket gid>, -e ASSAY_RESOURCE_OBSERVER_SOCKET=<same path>
  assay launchers           CGPROFILE_OBSERVER_SOCKET, MDT_HOST_DOCKER_GID
  cgpctl / topos clients    CGPROFILE_CONTROL_SOCKET / TOPOS_OBSERVE_SOCKET (or --socket)
  devcontainers (deferred, 5.4)  ${localEnv:VAR} from pam_env; initializeCommand verifies
```

| Concern | Owner after | Record / place |
|---|---|---|
| Level groups (`cgprofile-control`, `cgprofile-observer`, `topos-observe`) | each service's sysusers file, via its install verb | `/etc/sysusers.d/{cgprofile,topos}.conf` |
| Memberships | install verb `--add-user LEVEL=USER`, values from host-setup.env | `/etc/<svc>/install.toml` |
| `/run/<svc>/<level>/`, `/run/<svc>/state/` | systemd socket units / `RuntimeDirectory=` of the service | unit files from `unit print` |
| Socket path defaults | the service's own config module, nowhere else | `daemon_config.py:34-35`; `topos/service/config.py:15-16` |
| Endpoint + GID facts of a service | that service's install verb | `/etc/host-endpoints.d/<svc>.env` |
| Docker GID, BuildKit and docker-API facts | host-setup | `/etc/host-endpoints.d/mdt-host-setup.env` |
| Aggregate, `/etc/environment` block, profile.d | `augeaswrap.endpoints` (called by every publisher) | `/etc/host-endpoints.env` |
| BuildKit socket group | host-setup (`g mdt-buildkit`, P-5a) | `units/mdt-buildkitd.service.in:112` |
| Calling the installers with host values | host-setup | `install.sh` new `install_host_services` |
| Calling host-setup on a fresh host | host agent now; debian-install-v2 INST-01 later (mdtd track) | runbook 4.4 |

Who reads what, from where:

| Consumer | Runs in | Reads (env) | Delivered by |
|---|---|---|---|
| `cgpctl` | host shell, cockpit, devcontainer | `CGPROFILE_CONTROL_SOCKET` (+ `CGPROFILE_OBSERVER_SOCKET` for `doctor`) | pam_env / profile.d; container `-e` |
| `topos --attach`, `topos mcp`, `topos daemon status` | same | `TOPOS_OBSERVE_SOCKET` | same |
| run-gate lane launcher | cockpit / devcontainer | `CGPROFILE_CONTROL_SOCKET` (profiled or observer lane), `CGPROFILE_OBSERVER_SOCKET` (observer lane), `MDT_HOST_DOCKER_GID` | container `-e` from the record |
| assay judge in a lane | lane container | `ASSAY_RESOURCE_OBSERVER_SOCKET` | run-gate `-e`; lane commands via `required-env:` |
| assay registered launchers | cockpit (bare-host lanes) | `CGPROFILE_OBSERVER_SOCKET`, `MDT_HOST_DOCKER_GID` | container env |
| runner start script | host, root | `/etc/host-endpoints.env` (sourced) | the record file itself |
| devcontainer (deferred) | VS Code server / devcontainer CLI on the host | `${localEnv:...}` | pam_env block |

### 1.3 Lifecycle

| Event | What happens | Consumer effect |
|---|---|---|
| install (fresh) | verb creates groups, units, dirs; adds users; starts; publishes its fragment; helper re-renders the aggregate | new login sessions and newly created containers see the variables; running sessions do not (log in again; recreate the cockpit) |
| upgrade (same paths) | verb re-runs; fragment re-published with re-resolved GIDs; unchanged text = no rewrite | none |
| restart service | socket units keep the inode (`systemctl restart cgpd` leaves sockets) | none; directory binds keep working |
| restart socket unit | service restarts via `Requires=`; new inode in the same directory | directory binds see it |
| uninstall | verb withdraws its fragment FIRST, re-renders, then removes units/dirs | new sessions lose the variables and fail naming them; running containers hold a dead mount until recreated |
| doctor | `cgpd doctor` / `toposd doctor` new check `endpoint-record`; `cgpctl doctor` new check `endpoint-env`; host-setup `check.sh` runs all plus `augeaswrap endpoints check` | read-only |

### 1.4 Failure modes

| Failure | Detected by | Message / remedy |
|---|---|---|
| Variable unset in a consumer (old login session, desktop VS Code, a script with `env -i`) | the consumer, before any connect | "`CGPROFILE_CONTROL_SOCKET` is unset: log in again, or `set -a; . /etc/host-endpoints.env`, or run `cgpd install`" |
| Record stale vs live (GID re-created, path edited) | `endpoint-record` doctor; run-gate compares the daemon-advertised observer path with the variable (`run_gate.py:8918`) | re-run the install verb |
| Record present, daemon down | client cause `socket-missing` (`ctlwire.py:29`) | `systemctl status cgpd` |
| Two owners publish one key | helper at preflight and at publish, exit 2 | names both fragments |
| `/etc/environment` block damaged | helper (same rules as `install.sh:527-560`) | repair by hand, then re-run |
| Level group = docker GID / GID 0 / another level's GID | install verbs (P-5), host-setup cross-service rule, run-gate/assay preflights | refused, no override |
| Consumer inside a container trusts its own group table | prevented: GIDs come only from the record (numeric) | |

---

## 2. Endpoint record mechanism (ruling 1)

### 2.1 Files

```
/etc/host-endpoints.d/                  0755 root:root
/etc/host-endpoints.d/<owner>.env       0644 root:root   one per publisher (cgprofile, topos, mdt-host-setup; mdtd later)
/etc/host-endpoints.d/.lock             0600 root:root   flock for publish/withdraw/render
/etc/host-endpoints.env                 0644 root:root   aggregate, rendered, never hand-edited
/etc/environment                        managed block between
                                        "# BEGIN host-endpoints (generated by augeaswrap.endpoints; do not edit)"
                                        and "# END host-endpoints"
/etc/profile.d/host-endpoints.sh        0644: [ -r /etc/host-endpoints.env ] && { set -a; . /etc/host-endpoints.env; set +a; }
```

Fragment (and aggregate body) format: `KEY=VALUE` lines, header comments
`# host-endpoints v1`, `# owner=<owner>`, `# writer=<verb> <version>`. Key
`^[A-Z][A-Z0-9_]{0,63}$`; value `^[A-Za-z0-9_./:@+-]{1,4096}$` (no whitespace, quote, `$`, `\`,
`#`). With that alphabet one line is valid unchanged for pam_env, POSIX `sh`, systemd
`EnvironmentFile=` and `docker --env-file`, so no consumer needs a parser. JSON/TOML rejected:
every bash launcher would need python/jq, and pam_env cannot read them.

Why a directory of fragments rather than `/etc/<svc>/endpoints.env`: one glob builds the
aggregate, and the file name is the ownership marker (a key may appear in exactly one fragment).
Not world-secret: paths and GIDs only, so 0644. Name chosen to avoid the existing, unrelated
`/var/lib/mdt/host-facts` (CPU benchmark data, `mdt-host-cpu-benchmark.py:39`).

### 2.2 Names (owner = the only place with a default)

| Variable | Publisher | Value source | Used by |
|---|---|---|---|
| `CGPROFILE_CONTROL_SOCKET` | `cgpd install` | `[sockets.control].path` (default `daemon_config.py:34`) | cgpctl, run-gate, runner |
| `CGPROFILE_CONTROL_SOCKET_DIR` | `cgpd install` | dirname of the above | static configs (runner bind, devcontainer.json) |
| `CGPROFILE_CONTROL_GID` | `cgpd install` | `getent group <control group>` after sysusers | runner / devcontainer `--group-add` |
| `CGPROFILE_OBSERVER_SOCKET`, `_SOCKET_DIR`, `_GID` | `cgpd install` | `[sockets.observer]` (default `daemon_config.py:35`) | run-gate, assay launchers, runner (dir bind only, no group) |
| `TOPOS_OBSERVE_SOCKET`, `_SOCKET_DIR`, `_GID` | `toposd install` | `[sockets.observe]` (default `topos/service/config.py:16`) | topos clients, runner, devcontainer |
| `MDT_HOST_DOCKER_GID` | host-setup | `getent group docker` | run-gate (`run_gate.py:532-533`), assay (`resource-observer-gid.sh`), runner, devcontainer |
| `MDT_BUILDKIT_SOCKET`, `_SOCKET_DIR`, `_GID` | host-setup | its own unit template value; `getent group mdt-buildkit` | runner (`BUILDKIT_HOST=unix://$MDT_BUILDKIT_SOCKET`), devcontainer |
| `MDT_DOCKER_API_SOCKET`, `_SOCKET_DIR` | host-setup | `units/docker-api-socket.conf` value | runner (`DOCKER_HOST`), devcontainer |
| `ASSAY_RESOURCE_OBSERVER_SOCKET` (in-lane only) | the mounter (run-gate, assay launchers) | = `CGPROFILE_OBSERVER_SOCKET` (identity path) | assay judge (`resource_limits.py:1078`, `runner.py:1465`, `cli.py:3716`), lane commands via `required-env:` |

Rules: programs use `*_SOCKET` and derive the directory; `*_SOCKET_DIR` exists only for static
files that cannot compute a dirname (devcontainer.json, docker flags in bash); doctors check
`dirname(*_SOCKET) == *_SOCKET_DIR`. Removed with no fallback: `CGPCTL_SOCKET`
(`ctlwire.py:17`), the `DOCKER_GID` export (templates switch to `MDT_HOST_DOCKER_GID` in 5.4),
`ASSAY_GATE_RESOURCE_OBSERVER_SOURCE` (`self-qualification-container.sh:29-31`), run-gate
`[profile] socket_path` (becomes a named removed key next to `PROFILE_REMOVED_KEYS`,
`run_gate.py:722-727`).

Facts are not entitlements: the record carries every GID unconditionally; membership is
checked by doctors and (later) initializeCommand. This supersedes plan 5.3.8's literal
`/etc/environment` lines plus `/etc/profile.d/mdt-host-gids.sh`, and mdtd-DESIGN 3.3's
`/etc/profile.d/mdt-host-groups.sh` "export only if the session holds it" (mdtd S1-09 publishes
`MDTD_*` through the same helper). The plan's inert `nogroup` values are not written into the
record; for devcontainers (5.4, deferred) an OPTIONAL level uses `${localEnv:VAR:65534}` (the
mdtd-DESIGN 3.3 form) and initializeCommand warns, a REQUIRED level has no default.

### 2.3 How it reaches each place

- (a) **Host shells**: pam_env reads the `/etc/environment` block for ssh (including a Remote-SSH
  VS Code server after it is restarted), sudo (sudo merges the PAM environment), su and cron;
  `/etc/profile.d/host-endpoints.sh` covers login shells that skip pam_env. A systemd unit that
  needs a value declares `EnvironmentFile=/etc/host-endpoints.env`.
- (b) **Devcontainers** (deferred to 5.4, specified now so names do not change): `mounts`
  `source=${localEnv:CGPROFILE_CONTROL_SOCKET_DIR},target=${localEnv:CGPROFILE_CONTROL_SOCKET_DIR},type=bind,readonly`;
  `runArgs --group-add ${localEnv:CGPROFILE_CONTROL_GID}`; `containerEnv`
  `CGPROFILE_CONTROL_SOCKET: ${localEnv:CGPROFILE_CONTROL_SOCKET}` and likewise for
  topos/BuildKit/docker GID. `initializeCommand` cannot change localEnv; it re-reads the same
  variables from its own `os.environ` and refuses, naming the variable, when one is missing or
  differs from `/etc/host-endpoints.env` (it may read the file because it is the verifier, not a
  consumer).
- (c) **run-gate lanes**: run-gate (stdlib-only, no cli-extended import) reads the two socket
  variables lazily: `CGPROFILE_CONTROL_SOCKET` is required when profiling is enabled or the lane
  is an observer lane (the observer version preflight speaks to the control socket,
  `run_gate.py:8900-8922`), `CGPROFILE_OBSERVER_SOCKET` only for observer lanes. Into the lane it
  passes exactly the facts of the levels the lane is granted (`-e ASSAY_RESOURCE_OBSERVER_SOCKET`).
  A lane command that needs a fact declares it with the existing host-owned mechanism, as RG-157
  does for TMPDIR (`run-gate-project/assay.toml:55-56`, resolved by assay `runner.py:950-990`,
  which refuses a missing or empty value naming the fact). Assay's own lanes switch from
  `env_passthrough = [..., "ASSAY_RESOURCE_OBSERVER_SOCKET"]` (silently drops a missing value,
  `assay/assay.toml:58, :97, :220, :335, :452, :501`) to
  `[lanes.<lane>.infrastructure] ASSAY_RESOURCE_OBSERVER_SOCKET = "required-env:ASSAY_RESOURCE_OBSERVER_SOCKET"`.
- (d) **Assay launchers**: `RESOURCE_OBSERVER_SOURCE="$CGPROFILE_OBSERVER_SOCKET"` after a
  `[[ -n ${CGPROFILE_OBSERVER_SOCKET:-} ]] || die 'CGPROFILE_OBSERVER_SOCKET is unset; ...'`;
  the target directory is the source directory (identity). The path allowlist
  `self-qualification-container.sh:38-41` goes; the ownership guard
  `resource-observer-gid.sh:5-55` (root:root dir, root-owned socket, non-root non-docker group)
  remains the safety check. Docker GID order stays `ASSAY_GATE_HOST_DOCKER_GID` then
  `MDT_HOST_DOCKER_GID` (`resource-observer-gid.sh:62-76`).
- (e) **In-lane paths**: identity (E-3). The lane sees
  `/run/cgprofile/observer/cgprofile.sock` read-only; nothing in the lane hard-codes it.

### 2.4 The shared helper (module `augeaswrap.endpoints`; cli-extended is not changed and stays 0.5.3)

Stdlib only (P-7 zipimport rule), importable from the wheel file:
`publish(owner, entries, *, writer, root="/")`, `withdraw(owner, *, root="/")`,
`plan(owner, entries, *, root="/") -> problems` (preflight: collisions, alphabet, block
structure), `verify(owner, expected, *, root="/") -> problems` (doctor),
`check(*, root="/") -> problems` (whole host: aggregate == fragments, block == aggregate,
profile.d present, no key outside the block duplicates one inside, no symlinks, modes),
`require_env(name, *, remedy, environ=os.environ) -> str` (client helper raising `CliFailure`
naming the variable). CLI for bash callers: `augeaswrap endpoints
publish --owner O --writer W --entry K=V ... | withdraw --owner O | check [--json] | show`
(`python3 -m augeaswrap endpoints ...` from a wheel).
Atomic writes (temp + fsync + rename), one flock, refuses symlinked targets. The block logic
ports host-setup's structure rules (`install.sh:527-560`); host-setup's own `mdt-buildkit` block
stays as is.

### 2.5 Code that must change (X-02 round; file:line on origin/main)

cgroup-profiler (`scripts/cgroup-profiler`):
- `src/cgprofile/cli/installer.py`: `_preflight` (`:850`) adds `augeaswrap.endpoints.plan`; dry run
  (`:1198-1206`) reports the fragment; publish after apply and readiness (`:1302-1303`), before
  the install record (`:1324`); `uninstall` (`:1450`) withdraws first; `InstallPaths` runtime
  dirs `:93-106` derive from the configured socket paths instead of literals; `:1187` likewise.
  Old-layout literals in the P-11 refusal (`:214`, `:261-264`, `:339`) stay: they describe what
  to refuse.
- `src/cgprofile/cli/service_doctor.py:545-555`: add `endpoint-record`.
- `src/cgprofile/ctlwire.py:16-17, :28, :48-57`: delete `DEFAULT_SOCKET`; `SOCKET_ENV =
  "CGPROFILE_CONTROL_SOCKET"`; unset = `WireFailure(exit 2, code "endpoint-unset")` naming it;
  the `socket-not-mounted` remedy names `CGPROFILE_CONTROL_SOCKET_DIR`, not a path.
- `src/cgprofile/cli/cgpctl.py:57-58, :645-656, :830`: observer path from
  `CGPROFILE_OBSERVER_SOCKET`, cross-checked with the daemon advertisement; new doctor check
  `endpoint-env` next to `:592`, `:602`; help text without a path.
- Owner-side de-duplication (one default, `daemon_config.py:34-35, :75`): `serve.py:84-85`
  (uses `:548`, `:590`, `:708`, `:3335`) and `damon.py:63` import it.
- `tools/observer-live-acceptance.py:31` reads the variable; `tools/vm-socket-acceptance.sh`
  gains the endpoint cases (3.1).
- `pyproject.toml:31` floors `cli-extended>=0.5.3` (existing cli-extended API only) and `augeaswrap>=0.1.0` (`augeaswrap.endpoints`, first release carrying it). Docs: `docs/CONSUMERS.md`,
  `docs/NATIVE-DEPLOYMENT.md`, `docs/CGPCTL-SPEC.md`, `docs/CLI-SPEC.md`, `docs/cli-surface.json`,
  `skills/cgpctl/SKILL.md`, `ATTACH-GUIDE.md`, CHANGES. Delete the unused 1.x container files
  `ciu.defaults.toml.j2`, `ciu.compose.yml.j2` (plan 5.1.10) if `git grep` finds no user.

topos:
- `src/topos/service/install.py`: publish after the record write (`:1172-1176`), withdraw at
  `uninstall` (`:1369`); `:105`, `:216` derive from `service/config.py` defaults.
- `src/topos/service/doctor.py:432-442`: add `endpoint-record`.
- Clients: `src/topos/cli.py:46-47, :59-62, :122-192` (five `--socket` defaults), `:297`;
  `src/topos/daemon/status.py:22, :151, :184`; `src/topos/mcp/server.py:35-39, :466`: one
  resolver (`--socket`/`--attach PATH`, else `TOPOS_OBSERVE_SOCKET` via `require_env`).
- `pyproject.toml:13` floors `cli-extended>=0.5.3` (existing cli-extended API only) and `augeaswrap>=0.1.0` (`augeaswrap.endpoints`); new `tools/vm-socket-acceptance.sh`
  mirroring cgprofile's; docs `docs/DAEMON.md`, `docs/OPERATIONS.md`, CHANGES.

run-gate-project (`run_gate.py`):
- Delete `:505-513` constants and `:728` `PROFILE_SOCKET_PATH_DEFAULT`; add
  `control_socket_from_env()` / `observer_socket_from_env()` (fail_infra naming the variable;
  `RUN_GATE_PROFILE=off` named as the explicit opt-out for profiling). Call sites `:1725`,
  `:1739`, `:3817`, `:4025`, `:4310`, `:6079`, `:8838`, `:15511`, `:15867`, `:16075`, `:16090`.
- `resource_observer_docker_flags` (`:8813-8896`): bind source = target =
  dirname(observer socket); `-e ASSAY_RESOURCE_OBSERVER_SOCKET=<observer socket>`.
- `:8918` compares the advertised path with the variable; `:17796` help text.
- `[profile] socket_path` -> removed-key refusal; fixture
  `tests/fixtures/rg55-p5-placement-live/run-gate.toml:7`. Docs CONSUMERS/SPEC/README,
  DESIGN-GUIDE (0755 vs 0750 nit from the integration review), CHANGES.

assay (`assay/`):
- `tools/tester-unified-gate.sh:38-41` and `tools/self-qualification-container.sh:25-42, :483,
  :554, :613, :625`: env source, identity target, no override, no allowlist.
- `assay.toml` six passthrough sites -> `required-env` facts (2.3 c).
- New `tools/release-gate.sh`; `cmru.toml:29-35` uses it (section 5).
- `tools/self-qualification-gate.sh:821` window (section 5, operator go).
- Docs `docs/CONSUMERS.md:538`, README, CHANGES. `src/assay` needs no change (already env-only,
  no `/run/cgprofile` literal), so the wheel is layout-agnostic.

Netcup runner (`scripts/netcup/runner/`), section 4.5. mdt-cockpit host-setup, section 4.3.
Devcontainer templates (`templates/devcontainer.json:96, :222, :242`,
`templates/host-folder/devcontainer.json:111, :246, :266`, `.devcontainer/devcontainer.json:96,
:230, :250`, `templates/initialize_container_environment.py:487-548` / call `:785`,
`scripts/test_devcontainer_audit.py:158-161`): deferred (4.6).

---

## 3. What must be VM- or host-verified before anything consumes the releases (ruling 2)

Nothing consumes 2.2.0/0.7.0/25.3.0 (E-6). The X-02 versions are consumed only after:

| Step | Where | What | Blocks |
|---|---|---|---|
| V-1 (running now) | disposable guest, `scripts/cgroup-profiler/tools/vm-socket-acceptance.sh` clean-guest + old-layout-guest on 2.2.0 | plan 7 phase-1 list for cgpd | its findings go into WP-E1 |
| V-2 | one disposable guest, branch wheels of augeaswrap + cgprofile + topos (cli-extended unchanged; before merge M1) | both service scripts; plus: both fragments, aggregate, `/etc/environment` block; key collision refused; fresh `ssh tester@guest env` shows the variables, `sudo env` keeps them, `env -i` consumer fails naming the variable; uninstall withdraws; run-gate 25.4.0 preflight probe without docker (`python3 -c` calling `preflight_control_socket_access` and `resource_observer_docker_flags` with a fake ephemeral lane) and `source assay/tools/resource-observer-gid.sh; assay_resource_observer_gid "$CGPROFILE_OBSERVER_SOCKET"` as a member and a non-member | merge M1, X-02 releases |
| V-3 | mdt-cockpit `vm-system` lane (`host-setup/tests/run-vm-acceptance.sh`) on a fresh guest, X-02 wheels from a directory source | new case: install.sh with `CGPROFILE_ENABLE=1 TOPOS_ENABLE=1` reaches green `check.sh` through the doctors; three fragments; BuildKit socket group `mdt-buildkit`; refusal while seeded old files exist | merge M2, <host> host services |
| H-1 | <host> after its cutover (real host, nothing else running) | doctors green; cockpit `cgpctl doctor`, `topos --once --attach`; one profiled non-observer lane via `run-lane.sh` (admission/session visible in `cgpctl sessions`); assay `tester-unified` via remote-gate (also the assay-release rehearsal); one observer R2 lane started and stopped after its baseline | R2 campaigns, assay release, <host> |

run-gate's observer lane and assay's launchers need docker plus the multi-GB tester image, which
the TCG guest does not carry; their pure-preflight logic is in V-2, their real run is H-1.

---

## 4. Fast path to the two hosts (ruling 3)

### 4.1 Options

| | A: DIV2 `host_services` stage calls the verbs | B: full plan 5.3+5.4 first | C: minimal host-setup subset, host-setup calls the verbs |
|---|---|---|---|
| Works without host-setup changes? | No: the runner needs slices, BuildKit, docker API socket from host-setup, and today's host-setup installs `mdt-topos.conf` and `mdt-cgprofile*.conf` unconditionally (`install.sh:1348-1386`), which P-11 refuses (`installer.py:237-270`) | yes, but rewrites host-setup, wizard, check.sh, 3 templates, initializeCommand | yes |
| New code on the critical path | DIV2 stage + VM case + host-setup edits anyway | largest | host-setup subset + runner script |
| Touches the data-area-v2 branch in review | yes | no | no |
| Reused later | INST-01 (mdtd) | everything | INST-01 calls the same host-setup |

**Pick C, invoked by the host agent after the reinstall.** A is a strict superset of C's
host-setup work (the stage would have to call a host-setup that no longer installs the old
files) and adds a DIV2 change, a DIV2 VM case and a DIV2 review on top of the data-area-v2 fix
round still in flight. B delays both hosts by the template/initializeCommand work that neither
netcup host needs: the hosts run the runner cockpit from a script (`start-cockpit-runner.sh`),
not devcontainer.json. mdtd-DESIGN 6 already reinstalls both hosts again for mdtd S1+S2, and
INST-01 there will call this same host-setup, so nothing here is throwaway.

### 4.2 Long-running R2 campaigns: stop, hand over, resume

Inventory first (identity check, read-only): every `~runner/jobs/*/` without `EXIT`, every
`~runner/campaigns/*/` without `final-exit`, `docker ps --filter label=run-gate.lane`.

- **<host> (cut over first)**: mark it DRAINING on the R2 board and in the remote-gate host
  profile (no new jobs). For each running cmru shard: let it finish if its own ETA is under 3 h;
  otherwise, with the operator's go, stop it with the board recipe
  (`reports/r2-board-20261105b-REPORT.md:112-115`: kill the job pid, the
  `run-r2-campaign-r25d38.sh <campaign>` pids, `docker stop` the exact lane container, then any
  surviving in-cockpit `run-gate` pid). Export to the controller with a sha256 manifest:
  `~runner/campaigns/<name>/`, `~runner/checkouts/<sha>/` including `.run-gate/assay-state/` and
  `<project>/.assay/`. Resume on <host>, which has the same pins (`gate-tools2502`, image
  `tester-unified:r25-d38b3324fba5`): copy both trees to the same paths, then re-run the same
  campaign name with the same wrapper (`run-r2-campaign-r25d38.sh`; resume is automatic,
  `run-r2-campaign.sh:6`). The first attempt log must show judged candidates being skipped, not a
  new baseline; if it re-baselines or refuses on identity, keep it as a fresh run (shards are
  small). Queued, unstarted shards go to <host> or, after H-1, run fresh on <host> on the new pins.
- **<host> (second)**: the qualifying tnt R2 (`tnt-r2-v042`, ETA about 2026-11-09 11:30Z per the
  handoff) runs to completion; <host> is reinstalled after it, its closeout exported. No resume
  across pins is attempted (a campaign started on 25.0.2/r25-d38 does not resume on 25.4.0).
  Any cmru shards still on <host> then move to <host> as fresh runs.

### 4.3 host-setup phase-2-min (mdt-cockpit; WP-H1a, WP-H1b)

H1a (install path):
- Delete `units/mdt-topos.conf`, `units/mdt-topos-service.conf`, `units/mdt-cgprofile.conf`,
  `units/mdt-cgprofile-observer.conf`, `units/mdt-cgprofile.sysusers`,
  `scripts/mdt-cgprofile-install.sh`.
- `install.sh`: delete `provision_cgprofile_observer` (`:184-280`) and its calls (`:388`, `:413`,
  `:1108`, `:1371`), `--observer-only` (`:14`, `:73`, `:123-127`, `:324`, `:366-390`), the
  tmpfiles/drop-in installs (`:1348-1386`), the installer copy (`:1476`). Replace
  `--cgprofile-only` (`:392-416`) with `--host-services-only` and the service step
  (`:1578-1590`) with `install_host_services`: fetch each pinned wheel with sha256 into
  `/var/cache/mdt/host-services/`, then
  `PYTHONPATH=<cgprofile.whl>:<cli_extended.whl> python3 -m cgprofile.cli.cgpd install --yes
  --wheel ... --cli-extended-wheel ... [--augeaswrap-wheel <whl>@sha256:<h>] --wheel-source <dir> --control-group ... --observer-group
  ... --add-user control=U ... --enable`, the same for `topos.service.cli` `toposd install`;
  exit 3 (drain) keeps today's pending handling.
- Refuse (before any change) while any old file of plan 5.3.3 exists, printing the manual
  removal (fresh hosts never hit it; it protects the controller host).
- Keys in `host-setup.env.example` (`:899-947`): `CGPROFILE_CONTROL_GROUP`,
  `CGPROFILE_OBSERVER_GROUP`, `CGPROFILE_CONTROL_USERS`, `CGPROFILE_OBSERVER_USERS`,
  `TOPOS_ENABLE`, `TOPOS_WHEEL`, `TOPOS_WHEEL_SOURCE`, `TOPOS_OBSERVE_GROUP`,
  `TOPOS_OBSERVE_USERS`, `CLI_EXTENDED_WHEEL` (replaces `CGPROFILE_CLI_EXTENDED_WHEEL`),
  `MDT_BUILDKIT_GROUP`, `MDT_BUILDKIT_USERS`. `*_USERS` must be set explicitly when the role is
  enabled (empty = nobody); no `SUDO_USER` default (defaults are hazards in a non-interactive
  run). The wizard only renames/removes keys; prompts for the new keys are WP-H3 (deferred).
- Publish `mdt-host-setup` fragment (`MDT_HOST_DOCKER_GID`, `MDT_BUILDKIT_*`,
  `MDT_DOCKER_API_*`) through `augeaswrap endpoints publish` (H1g'). `mdt-cockpit/host-setup` keeps its name.
- After both installs: cross-service rule (all `*_GID` distinct, none 0 or the docker GID).
- Tests: `tests/test_cgprofile_install.py` wiring cases, `test_install_runtime_blocks.py`,
  `tests/test-render.sh`.

H1b (check and BuildKit):
- `scripts/check.sh:463-709` replaced by `cgpd doctor --json`, `toposd doctor --json`,
  `augeaswrap endpoints check --json` and the cross-service GID rule, rendered as check lines.
- BuildKit (P-5a, operator confirms the name): sysusers `g mdt-buildkit`; `gpasswd -a` for
  `MDT_BUILDKIT_USERS`; `units/mdt-buildkitd.service.in:112` chgrps to `@MDT_BUILDKIT_GROUP@`
  and verifies against its GID; reword the final error; `check.sh` checks it;
  `tests/test-render.sh` pins move. Restart through the existing decision helper.
- README sections ("Provision the cgprofile observer directory", "Install, pin and upgrade the
  cgprofile service"), CHANGES, and the V-3 case in `tests/run-vm-acceptance.sh`.

Delivery: host-setup runs from `~runner/checkouts/<M2 merge sha>/mdt-cockpit/host-setup` (synced
by `sync-from-devcontainer`); no mdt-cockpit release is needed for the fast path and none may be
cut before 5.4 (it would rebuild the cockpit image from `-latest` wheels,
`mdt-cockpit/pip/wheels.list:18-20`).

### 4.4 <host> sequence (host agent; operator go at steps 1 and 3)

1. Campaign handover (4.2). Export per runbook 4.2 (`div2-reinstall-RUNBOOK.md:235-247`).
2. Preconditions: div2-v2 fix4 verified and merged, `r1-vm-real-commit` lane run
   (runbook 3.1), the 20261107 customscript JSON exists (not yet in
   `<host>/`; only ...1104/...1105 are there).
3. Reinstall (runbook 4.5, `install-host.py wizard`). This step does not wait for X-02: it can
   run in parallel with the E track.
4. Post-install checks (runbook 5.1-5.4), host key pin update, `provision-runner.sh` (runbook 5.5).
5. When X-02 is released and M2 merged: write the reviewed per-host
   `/etc/mdt/host-setup.env` (pins with sha256, `CGPROFILE_ENABLE=1`, `TOPOS_ENABLE=1`,
   `CGPROFILE_CONTROL_USERS="runner <operator login>"`, `CGPROFILE_OBSERVER_USERS=""`,
   `TOPOS_OBSERVE_USERS="runner <operator login>"`, `MDT_BUILDKIT_USERS` same), run
   `install.sh` non-interactively, then `check.sh` green.
6. `cat /etc/host-endpoints.env`; a fresh ssh login as runner shows the variables;
   `getent group cgprofile-control cgprofile-observer topos-observe mdt-buildkit docker`.
7. `setup-cockpit-run-gate.sh 25.4.0 <sha>` (new `~/.venvs/gate-tools2540`), build
   `tester-unified:x02-<date>` with `build-tester-unified.sh` (side tag), `run-gate admission set
   --max-concurrent N --unreadable-policy refuse`, start the runner with the new
   `start-cockpit-runner.sh` (4.5), update the controller's gitignored remote-gate hosts file
   (<host> profile: new run-gate path, image tag).
8. H-1 (section 3). Then <host> ACTIVE on the board; assay release (section 5); queued R2 work.

<host>: the same 1-8 after the tnt R2 closeout, with the operator's go.

### 4.5 Runner cockpit container (plan 5.7, WP-R1)

`start-cockpit-runner.sh`: `load_host_endpoints` in `lib.sh` sources `/etc/host-endpoints.env`
and requires every variable it uses (die naming the first missing one); refuse when any of
`CGPROFILE_CONTROL_GID`, `TOPOS_OBSERVE_GID`, `MDT_BUILDKIT_GID` equals `MDT_HOST_DOCKER_GID`, is
0, or equals another. Replace `:14` with numeric `--group-add` for the docker, control, topos and
BuildKit GIDs (never the observer GID; lanes get it from run-gate). Replace `:18-20` with
read-only identity binds of `MDT_DOCKER_API_SOCKET_DIR`, `CGPROFILE_CONTROL_SOCKET_DIR`,
`CGPROFILE_OBSERVER_SOCKET_DIR` (run-gate's preflight `lstat`s it, `run_gate.py:8840-8850`),
`TOPOS_OBSERVE_SOCKET_DIR`, `MDT_BUILDKIT_SOCKET_DIR` (connect works on a read-only bind; Docker
applies `--group-add` to `docker exec -u vscode` processes, which is how the docker group reaches
lanes today). Replace `:25-29` literals with `-e` for every endpoint variable,
`MDT_HOST_DOCKER_GID`, `DOCKER_HOST=unix://$MDT_DOCKER_API_SOCKET`,
`BUILDKIT_HOST=unix://$MDT_BUILDKIT_SOCKET`. `runner.env.example:19` pins `RUN_GATE_VERSION`
25.4.0. `remote-gate.py` needs no change: its job inherits the cockpit environment
(`remote-gate.py:589`).

### 4.6 Deferred (not on the fast path)

5.4 templates + vbpub `.devcontainer` + `initialize_container_environment.py` (spec in 2.3 b);
the host-setup wizard prompts (H3); DIV2 INST-01 (mdtd track, unchanged, now calls the C
host-setup); slice names (`CGROUP_PARENT_DEV_*`) into the record (phase 2.x); topos-sampler
drop-in (topos-b9, waits on the TUI decisions); the controller host (ignored per ruling; keeps
run-gate 25.2.1, old layout, `tester-unified:local`). The plan's cutover steps 1-3 (drain, stop,
manual removal) are void on reinstalled hosts.

---

## 5. Assay release host (ruling 4)

How the gate runs today: `cmru release` prepares the candidate commit, pushes it as the backup
branch, then runs `[steps.run-tests]` in the candidate worktree with `CMRU_RELEASE_GATE_COMMIT`
exported (`cmru/src/cmru/cli.py:3961-3970`, `:5250-5272`, `:3534-3560`); the step inherits the
caller's environment apart from protected names (`:335-420`). Assay's steps are
`bash tools/tester-unified-gate.sh ..` and `run-gate self-qualification-provisional`
(`assay/cmru.toml:29-35`), both bare-host lanes needing the new-layout observer
(`assay/run-gate.toml:34-37, :95-98`). cmru has no remote gate feature (`delegated.py` is
signing tools only); `--skip-gates` (`cli.py:9010`) would publish "gates skipped" in the Release
body and bind nothing to the candidate.

Recommendation: delegate only the two gate commands to <host>, keep the whole transaction,
tagging, building and publishing (and every credential) on the controller.

- New `assay/tools/release-gate.sh <lane>` (WP-E4): requires `ASSAY_RELEASE_GATE_HOST`
  (`local` | `<host>` | `<host>`; unset = error naming it). `local` execs today's command.
  Otherwise it runs `python3 ../scripts/netcup/runner/remote-gate.py run "$host" "$(git rev-parse
  --show-toplevel)" <lane> --project-dir assay --repo vbpub --name
  "assay-rel-${CMRU_RELEASE_GATE_COMMIT:0:12}-<lane>-<utc>"` attached (remote-gate ships a git
  bundle of HEAD, `remote-gate.py:727-790`; the host never sees a credential), then refuses
  unless the run's `meta.json` `sha` equals `CMRU_RELEASE_GATE_COMMIT` and `result.json` verdict
  is PASS; exits with the lane's code. `self-qualification-provisional` needs no receipt from
  the tester-unified run (`self-qualification-gate.sh:594-606`: full lane only), so the two
  separate throwaway checkouts are fine.
- `assay/cmru.toml:29-35`: both commands become `bash tools/release-gate.sh tester-unified` and
  `bash tools/release-gate.sh self-qualification-provisional`.
- **Blocker found**: the provisional lane's R2-postponement window is `>= 10.0.0, < 12.1.0`
  (`self-qualification-gate.sh:821`), and the next assay release is 12.1.0 (feat commits, no
  breaking marker since `assay-v12.0.0`). It refuses. Extending the window to `< 12.2.0`
  (and fixing the stale `cmru.toml:31-33` comment) is an R2 waiver: **operator go**.
- Steps: (1) WP-E4 merged; (2) H-1 green on <host>; (3) dry run `cmru release assay --dry-run`
  on the controller; (4) `export ASSAY_RELEASE_GATE_HOST=<host>; release-when-quiet.sh <log>
  assay --ahead-check-ref origin/main`; (5) `gh release view assay-v12.1.0`; (6) the wheel is
  layout-agnostic (`src/assay` reads only the environment), so the controller may keep 12.0.0
  and dstdns may take 12.1.0 when it wants; still no `.assay-inbox` drop until dstdns asks.
- remote-gate `auto` must not pick <host> for assay while <host> is old-layout: name the host.

---

## 6. Prioritised work packages

Implementer: Codex `gpt-6-luna` max via `codex-impl.sh`; reviewer: an independent Sonnet
read-only agent per WP (verification review after each fix round); host/VM steps: Sonnet host
agent or the controller. Merges: M1 = one integration branch with per-tool commits for
E0-E4 + R1, one `--no-ff`; M2 = H1a + H1b + D1. Sizes: S < 300 changed lines, M < 900, L more.

| WP | Content | Size | Depends | Gate | Verify | Operator go |
|---|---|---|---|---|---|---|
| E0 | `augeaswrap.endpoints` module + CLI + tests + README/SPEC (2.4; work package AW-0) | M | - | augeaswrap gate | V-2 | no |
| E3 | run-gate env-only endpoints, identity observer mount, removed `socket_path` (2.5) | M | names only (parallel with E0) | `selftest` | V-2 probe, H-1 | no |
| E4 | assay launchers env-only + identity, `required-env` facts, `release-gate.sh`, cmru.toml steps (2.5, 5) | M | names only | focused `pytest` of launcher tests on the controller; registered gate only at H-1 | V-2 probe, H-1 | no (window: see E4w) |
| R1 | runner `start-cockpit-runner.sh` + `lib.sh` + `runner.env.example` + README (4.5) | S | names only | netcup `suite` lane | H-1 | no |
| E1 | cgroup-profiler publish/withdraw/doctor, env-only `cgpctl`/`ctlwire`, owner de-dup, VM script cases, V-1 fixes (2.5) | M | E0 API | `r0-r1`, `r3` | V-2 | no |
| E2 | topos publish/withdraw/doctor, env-only clients, VM script (2.5); coordinate topos-b9 | M | E0 API | remote-gate `topos-suite` | V-2 | no |
| V-2 | VM run on branch wheels (3) | S (ops) | E0-E3 reviewed | - | - | no |
| M1 + REL-X02 | merge; release (cli-extended: none, stays 0.5.3) augeaswrap 0.1.0 -> cgroup-profiler 2.3.0 -> run-gate-project 25.4.0; topos 0.8.0 after augeaswrap when it uses endpoints (orchestration order, `--ahead-check-ref origin/main`); then side image `tester-unified:x02-<date>` for ciu releases only; no venv/promote on the controller | ops | V-2 green | each project's release gate | - | no (pin hold continues) |
| H1a | host-setup install path (4.3) | M/L | E0 CLI, E1/E2 verb flags | mdt-cockpit registered gate | V-3 | no |
| H1b | check.sh via doctors, BuildKit group (4.3) | M | H1a | mdt-cockpit registered gate | V-3 | **yes: `mdt-buildkit` name and BuildKit access change (P-5a)** |
| D1 | amend plan 5.3.8/5.4.1/5.5.2/5.6/5.7 and note mdtd 3.3 supersession, pointing here | S | - | doc review | - | no |
| V-3 + M2 | VM vm-system case, then merge | ops | H1a, H1b, REL-X02 | - | - | no |
| CUT-<host> | 4.2 handover, 4.4 steps 1-8 | ops | div2 fix4 merge (step 3); M2 (step 5) | - | H-1 | **yes: stopping shards; the reinstall** |
| E4w | assay provisional window `< 12.2.0` | S | - | assay tests | - | **yes (R2 waiver)** |
| REL-assay | section 5 steps | ops | E4, E4w, H-1 | delegated gates on <host> | - | inform (ruling 4) |
| CUT-<host> | 4.4 after the tnt R2 closeout | ops | CUT-<host> green, tnt R2 final-exit | - | H-1 repeated | **yes** |
| later | 5.4 templates + initializeCommand, H3 wizard, mdt-cockpit release, INST-01 | - | - | - | V + H | per mdtd plan |

Critical path: E0 -> E1/E2 -> V-2 -> M1 -> REL-X02 -> H1a/H1b -> V-3 -> M2 -> <host> step 5 ->
H-1 -> REL-assay. E3, E4, R1, D1 and the <host> reinstall itself (steps 1-4) run beside it.
At most two L tracks at once (H1a is the only L-ish one).

---

## 7. Risks and what is inferred, not verified

- Docker applying `--group-add` to `docker exec -u` processes, sudo merging pam_env, and connect
  on a read-only bind are inferred from documented behaviour and today's working docker-group
  setup; V-2 (sudo, ssh) and H-1 (exec, ro bind) confirm them.
- Assay infrastructure fact name equal to its source variable
  (`ASSAY_RESOURCE_OBSERVER_SOCKET = "required-env:ASSAY_RESOURCE_OBSERVER_SOCKET"`) and its
  interplay with the assay process's own `os.environ` read (`runner.py:1465`) were read, not run;
  E4 acceptance must show a named refusal when the variable is missing.
- R2 resume across hosts with identical pins was never tried; 4.2 accepts a fresh restart.
- Next assay version 12.1.0 is inferred from conventional commits; confirm with
  `cmru release assay --dry-run` before asking for the window.
- `cmru release` running `remote-gate.py` from inside its transaction child (ssh keys, hosts
  file under `~/.config/remote-gate/`) is unexercised; the dry run in section 5 step 3 and a
  `ASSAY_RELEASE_GATE_HOST=<host> bash tools/release-gate.sh tester-unified` run on main HEAD
  (H-1) prove it before the real release.
- Host facts (running campaigns, ETAs, disk) come from the 2026-11-07 handoff and the R2 board
  report; re-read them on the host before acting.
