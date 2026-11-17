# Host cutover 2026-11: legacy cgprofile 1.1.0 to the modern host stack

Purpose: move the controller host from the legacy cgprofile container and legacy host-setup files to the
published endpoint record, so a new mdt-cockpit devcontainer (started from the dstdns repo) receives
`CGPROFILE_*`, `MDT_*` and BuildKit facts without bridges. Owner of the decisions: the operator
(2026-11-17). Executor: the controller session, after one explicit "go".

## Status

Phases 0 to 3 were executed on the controller host on 2026-11-17 (about 15 minutes, 00:08 to 00:14 CET). Outcome:
cgprofile 3.0.0 runs natively (`cgprofiled.service` plus the two socket units, groups `cgprofile-control` 989 and
`cgprofile-observer` 990, `vb` a member of both), `/etc/host-endpoints.env` carries the cgprofile and mdt-host-setup
fragments, `augeaswrap endpoints check` is clean, every `cgprofiled doctor` and `cgprofilectl doctor` check is OK
(one WARN: the optional `report` extra lacks matplotlib), and all 14 production containers plus the docker,
containerd, wings and soulmask units show unchanged start times and restart counts. Records: `/var/backups/mdt-cutover-20261117/`
on the host (removed files, `containers-before.txt` / `containers-after.txt`, `units-before.txt`).
Open: Phase 4 (the operator restarts the VS Code Remote-SSH server and logs in again, then starts the rebuilt dstdns
devcontainer; verify a run-gate lane there with no manual export, which closes MDT-086) and, later, the topos install.

## Decisions (operator, 2026-11-17)

| Topic | Decision |
|---|---|
| Execution | Controller executes end to end after the operator has read these steps and said "go". It stops on any failed check. |
| Production | The game server, wings, traefik, site and every non-cgprofile container are NOT stopped, restarted or re-placed. No host reboot. |
| host-setup scope | Minimal: remove the legacy files and publish the endpoint record. `install.sh` is NOT re-run, so no governance unit, slice, io.cost or Docker daemon setting changes. |
| topos | Deferred until hostobs. No `toposd install`; `TOPOS_*` stays unset and the bootstrap warns by name. |
| Purpose of the deploy | Only host pieces the new mdt-cockpit build needs, so host and image match. |

## Host facts (read-only survey 2026-11-17, host `gstammtisch`, Debian 13, kernel 7.2.12)

- Running legacy daemon: container `cgprofile-host-daemon` (image `ghcr.io/volkb79-2/cgprofile:1.1.0`,
  `restart=unless-stopped`, privileged, host PID, compose project `cgprofile-host-cgroup-profiler` from
  `scripts/cgroup-profiler/ciu.compose.yml`). Serves `/run/cgprofile/ctl.sock` (root:docker 0660) and
  `/run/cgprofile/observer/resource-observer.sock` (root:cgprofile-observer 0660). Session history is in the
  Docker volume `cgprofile-host-cgroup-profiler_cgprofile-sessions`.
- No `/opt/cgprofile`, `/etc/cgprofile`, `cgprofiled.service`, topos, augeaswrap, or host endpoint files.
- Nothing on the host references the old socket (no unit, cron or script); DAMON has 0 kdamonds running.
- Legacy host-setup files present: `/etc/tmpfiles.d/mdt-cgprofile.conf`,
  `/etc/tmpfiles.d/mdt-cgprofile-observer.conf`, `/etc/sysusers.d/mdt-cgprofile.conf`,
  `/etc/profile.d/mdt-buildkit.sh`, and one `# BEGIN mdt-buildkit` block in `/etc/environment`.
  `/etc/systemd/system/cgprofile.slice` is an old copy to be replaced by the installer's own.
- Groups: `docker` 994, `cgprofile-observer` 990 (kept, GID unchanged), no `cgprofile-control`.
- Host checkout used by the cockpit: `/home/vb/volkb79-2/vbpub` (`HOSTSETUP=$CHECKOUT/mdt-cockpit/host-setup`).
- host-setup `install.sh` has no endpoint-only mode; its endpoint publication is the function
  `publish_host_setup_endpoints` in `host-setup/scripts/mdt-host-endpoints.sh`, which the minimal path calls
  directly. Its dry-run fragment today: `BUILDX_BUILDER=mdt-managed`, `BUILDKIT_HOST`, `MDT_HOST_DOCKER_GID=994`,
  `MDT_BUILDKIT_SOCKET(_DIR)`, `MDT_BUILDKIT_GID=994`, `MDT_DOCKER_API_SOCKET(_DIR)`.

## Artifacts (verify the sha256 before use; values read from the GitHub release metadata)

| Artifact | Release | sha256 |
|---|---|---|
| `cgprofile-3.0.0-py3-none-any.whl` | `cgprofile-v3.0.0` (2026-11-08) | `4dd5f1855be5a3265a994c187d4046d2e6e7cebd7b335b303112d524eee45711` |
| `augeaswrap-0.2.0-py3-none-any.whl` | `augeaswrap-v0.2.0` (2026-11-09) | `fdc5d33ad958fd5f992a61f2c48e34f2e56ad7892013ed75b282a61133418b3f` |
| `cli_extended-0.5.3-py3-none-any.whl` | `cli-extended-v0.5.3` (2026-11-07) | `5a88979592ec5d3c7bebae57787d1674d88d72413c764f2dcde82f672fd267d4` |

cli-extended 0.5.3 is the floor both cgprofile 3.0.0 and augeaswrap require. 0.6.0 exists (2026-11-09) but is
under the 14-day vetting buffer and not needed.

## Phase 0: preflight (read-only; stop on any surprise)

1. Snapshot every container's `StartedAt` and `RestartCount` plus `systemctl is-active` for `docker`, `wings.slice`
   units and `soulmask*`; this is the before/after proof that production is untouched.
2. Confirm no live cgprofile session: the legacy session directory shows only finished sessions, and no running
   gate or build container exists besides production. If one runs, wait or ask.
3. `python3-venv` is installed (3.13.5): yes. Disk and network: `curl` to github.com works from the host.
4. Back up everything that will be removed to `/var/backups/mdt-cutover-20261117/` (root 0700), keeping paths.

## Phase 1: stage, with no host behaviour change

1. `/var/tmp/mdt-cutover-20261117/` (root 0700): download the three wheels from
   `https://github.com/volkb79/vbpub/releases/download/<tag>/<file>`, verify each sha256 against the table. Abort on mismatch.
2. Put the `augeaswrap` command on the host PATH. No documented host install exists, so use the pattern cgprofile
   uses for its own venvs: `python3 -m venv /opt/augeaswrap/0.2.0/venv`, `pip install --no-index --no-deps` of the
   augeaswrap and cli-extended wheels, then `ln -s /opt/augeaswrap/0.2.0/venv/bin/augeaswrap /usr/local/bin/augeaswrap`.
   Check `augeaswrap --version` prints 0.2.0. (Backlog: file the missing host-install doc against augeaswrap.)

## Phase 2: the cut (one sitting; keep the sequence, no reboot in between)

The old tmpfiles/sysusers rules are what recreate `/run/cgprofile` at boot. Between removal and `cgprofiled install`
a host reboot would leave no runtime directories, so nothing here may restart the host.

1. Remove the legacy daemon by exact name: `docker stop cgprofile-host-daemon`, `docker rm cgprofile-host-daemon`.
   Do not remove its volume (archive of past sessions). No other container is named or touched.
2. Remove the old socket nodes and the old runtime directory: `rm -f /run/cgprofile/ctl.sock
   /run/cgprofile/observer/resource-observer.sock /run/cgprofile/damon.lock` (the legacy daemon leaves
   `damon.lock` behind), then `rmdir /run/cgprofile/observer /run/cgprofile` (the installer refuses an old
   owner/mode and an old-layout node).
3. Service files (from the host-setup refusal text): remove the three tmpfiles/sysusers files above and the old
   `/etc/systemd/system/cgprofile.slice`; `systemctl daemon-reload`. Keep the `cgprofile-observer` group and its GID.
4. Last, immediately before publishing (new login sessions lose the BuildKit variables until step 6): remove
   `/etc/profile.d/mdt-buildkit.sh` and only the `# BEGIN mdt-buildkit ... # END mdt-buildkit` block of
   `/etc/environment`, preserving every other line.
5. `refuse_legacy_host_setup_files /` (sourced from `mdt-host-endpoints.sh`) must now print nothing.
6. Publish host-setup's fragment: `publish_host_setup_endpoints "$HOSTSETUP" publish /`. This writes
   `/etc/host-endpoints.d/mdt-host-setup.env` and renders `/etc/host-endpoints.env`, the managed `pam_env` block in
   `/etc/environment` and `/etc/profile.d/host-endpoints.sh`.
7. `cgprofiled install` from the staged wheels with the three pins, `--control-group cgprofile-control`,
   `--observer-group cgprofile-observer`, `--add-user control=vb`, `--add-user observer=vb`, `--gates-slice
   dev-gates.slice`, `--enable --yes`. Run it first with `--dry-run` and read the plan (units, groups, fragment
   `cgprofile.env`). It drains live sessions first; there are none. The drain is not used as a stop mechanism.

## Phase 3: verify

- `/opt/cgprofile/current/venv/bin/cgprofiled doctor --json`, `cgprofilectl doctor --json`, `augeaswrap endpoints check --json`.
- `/etc/host-endpoints.env` carries the `CGPROFILE_CONTROL_*`, `CGPROFILE_OBSERVER_*`, `MDT_HOST_DOCKER_GID`,
  `MDT_BUILDKIT_*`, `MDT_DOCKER_API_*` and BuildKit keys; the sockets exist with group `cgprofile-control` /
  `cgprofile-observer`; the control and observer GIDs differ from each other and from docker 994.
- A fresh login on the host (`ssh localhost env`) shows the keys through `pam_env`.
- The Phase-0 snapshot compares equal: every production container's `StartedAt` and `RestartCount` unchanged;
  `docker.service`, wings and soulmask units untouched. If anything differs, stop and report.

## Phase 4: consumers (after the cut; no further host change)

- This controller devcontainer binds the old `/run/cgprofile`; after the cut its run-gate cannot reach the new
  sockets until it is rebuilt. Finish or park in-flight gates before the cut.
- The operator starts the rebuilt dstdns devcontainer. Expected bootstrap output: a real
  `/etc/host-endpoints.env`, no "delivering the facts the live sockets show" bridge warning, only the
  `TOPOS_*` absence warning (topos deferred). Then verify a run-gate lane inside it with no manual export (closes MDT-086).
- Restart long-lived sessions (VS Code Remote-SSH server, systemd units) so they read the new record.

## Rollback

There is no in-place rollback: old and new clients and daemons do not interoperate. To go back: `cgprofiled uninstall
--yes`, restore the backed-up files from `/var/backups/mdt-cutover-20261117/`, `systemctl daemon-reload`, recreate the
container from `scripts/cgroup-profiler/ciu.compose.yml` (image `cgprofile:1.1.0` stays in the local Docker store; the
session volume is kept). Production is not involved either way.

## Not part of this cutover

- topos install (waits for hostobs); host reboot; any `install.sh` governance re-run (host policy dated Oct 23 to
  Nov 3 stays as is).
- VM storage: the workspace (`/workspaces/vbpub`, `/workspaces/dstdns`) stays on ext4; only selected folders get CoW
  through the VM session's central path config (its design, tracked in MDT-044 and docs/BTRFS-COW.md). The CoW mount
  and `MDT_COW_ROOT` are active in the templates, the live `.devcontainer` and dstdns (operator, 2026-11-17); the
  host area `/srv/mdt-cockpit-cow/vb` exists. The host has no `/dev/kvm`, so VM guests run under TCG.
- Image packages for the next build, all in `apt/packages.list`: `btrfs-progs`, `eatmydata`, `libeatmydata1`,
  `xorriso`, `mtools`. `swtpm` later; vmscreen and a vm-lab wheel are requested for a later image.
