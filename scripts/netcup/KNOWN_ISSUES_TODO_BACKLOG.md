# netcup — known issues, TODO, and backlog

Created 2026-10-21 from the INT-03 review
(INT-03 review, N-01). IDs are
`NC-N`, allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`CLI-SPEC.md`](CLI-SPEC.md); rationale in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md). A FIXED entry means code, tests, and
docs landed together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| NC-01 | No test covers the 401 -> token refresh -> retried bodyless POST path; the retry must still carry `Content-Type: application/json` and no body | Minor | FIXED 2026-11-04 (tests only, fix-misc-1-20261104) |
| NC-02 | Runner provisioning scripts were host-specific task files with six first-run bugs; now generic under `runner/` | Minor | FIXED (this branch) |
| NC-03 | The install wizard connects with `StrictHostKeyChecking=no`; the host-key pin is trust-on-first-use | Minor | OPEN |
| NC-04 | A runner account must be uid 1003 (tester-unified lane uid); provisioning created uid 1000 | Major | FIXED (this branch) |
| NC-13 | Flexible installer-managed CoW and data storage replacing runner-side partition creation | Minor | SUPERSEDED by the single `partitioning` plan in div2-data-area-v2; live use OPEN |
| NC-12 | Test-host disk layout: 40 GiB root, swap in the middle, cow behind it; root filled to 91-97% and neither root nor cow could grow | Major | SUPERSEDED by the single `partitioning` plan in div2-data-area-v2; live reinstall of v1001/r1002 OPEN |
| NC-14 | Assay `self-qualification*` lanes need `/workspaces/<repo>` to be a bind mount of its own; the host cockpit mounts `~runner/checkouts` as `/workspaces`, so a remote-gate run ends `ERROR` ("not inside the declared /workspaces/vbpub bind") | Major | FIXED in repository 2026-11-10: launcher bind plus rendered-argv regression; live host acceptance remains separate |
| NC-15 | Host cockpits carry run-gate 24.3.0 (r1002 default) / 25.0.0 (v1001 default) while the controller runs 25.1.1; only the `gate-tools2502` venv (25.0.2) meets the vbpub floor | Minor | OPEN (host refresh needs the host board; remote-gate floors at 25.0.1 meanwhile) |
| NC-16 | Controller Ctrl-C can leave the read-only remote evidence collector running without the controller timeout | Minor | BY DESIGN |
| NC-17 | `update-host-pin.sh` counts the `#` banner line that newer `ssh-keyscan` prints to stdout, so it refuses a good scan | Minor | FIXED d951f417d |
| NC-18 | `runner/start-cockpit-runner.sh` makes the host-endpoint record a hard requirement: a runner cockpit cannot start on a host without mdt host-setup | Minor | FIXED d951f417d (host-setup mandatory) |
| NC-19 | `runner/provision-runner.sh` prints no line saying which partition it selected for the CoW volume | Nit | FIXED d951f417d |
| NC-20 | The runner README "Order" lists the cockpit-dependent steps before the host-setup step they need | Minor | FIXED: prior host-setup dependency in d951f417d; P1–P15 ordering aligned to the host plan 2026-11-10 |
| NC-21 | The cut-over recipe for a reinstalled host without mdt host-setup exists only in agent logs, not in the repo | Should-fix | DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; implementation and fresh-host acceptance OPEN |
| NC-23 | Host Run-Gate default and pin hold | Decision | DECIDED HP-D10 2026-11-10: target 25.4.1 on both test hosts after old campaign state collection; rollout and verification pending |
| NC-24 | The host-sat queue has a per-host pause flag (`host-sat-queue.disabled.<host>`) that no runner doc mentions | Minor | FIXED at `ac0a78dcd` (queue relocation and runner documentation) |
| NC-25 | Assay's `cgroup-parent.sh` refuses a host without mdt host-setup slice units; the minimal marker-unit workaround is not recorded | Should-fix | DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; automated provisioning and fresh-host acceptance OPEN |
| NC-26 | The cockpit container has Docker restart policy `no`; after a host reboot it stayed down and the queue exhausted its INFRA allowance | Should-fix | FIXED at `726550ed8` (launcher policy, argv regression test, and README); reboot acceptance OPEN |
| NC-27 | Remote-gate preflight can show a stopped cockpit as a stale run-gate, and the queue log keeps only the misleading last line | Should-fix | FIXED at `726550ed8` (ordered causal preflight messages, conditional run-gate hint, queue-tail regression tests) |
| NC-28 | Queue auto-pause leaves no durable who/why/when signal; controller status shows PAUSED without the reason | Should-fix | FIXED at `12ab909a1` (structured pause metadata and both status views) |
| NC-29 | A queue `stop` during an in-flight lane removes the loop pid file immediately but leaves that lane's `.current` marker stale, blocking or misreporting restart | Should-fix | OPEN |
| NC-30 | Hosts can pair incompatible cgprofile and Run-Gate versions without a documented compatibility matrix or a preflight that names the mismatch | Major | OPEN |
| NC-31 | Queue restart does not prove the old loop PID exited and the disabled queue logged that it did not launch a replacement | Should-fix | OPEN |
| NC-32 | T3 re-supervision collides with `run-job`'s existing-name refusal, and an INFRA retry reads the failed campaign's stale `final-exit` | Should-fix | OPEN |
| NC-33 | `sync-from-devcontainer` leaves the host build/setup symlink pointed at the imported release-tag checkout | Should-fix | OPEN |
| NC-34 | A passing CMRU command-evidence campaign exits 70 because the supervisor requires an Assay verdict | Should-fix | OPEN |
| NC-35 | Mixed endpoint-publisher rollback has no confirmed aggregate-cleanup argv | Minor | OPEN; live publisher and fragment inventory required before writing a safe command |
| NC-36 | The R2 supervisor relaunches every exit 4 and its stall guard counts baseline progress lines instead of completed candidates | Major | OPEN |
| NC-37 | A T3 campaign has no explicit, immutable comparison base or pre-baseline source-diff check; an empty source scope costs a baseline and ends NO_MUTANTS | Should-fix | OPEN |
| NC-38 | Persistent campaign checkouts and `.assay` state have no bounded retention or disk preflight on the small root volume | Should-fix | OPEN |
| NC-39 | Installed `run-job` and `lib.sh` differ from repository main with no provenance inventory or supported reconciliation path | Should-fix | OPEN |
| NC-40 | R2 supervisor counts re-reported kills as progress and misses a stalled resume | Major | OPEN |

---

## NC-01 — the 401 refresh-and-retry path of a bodyless POST is untested

**Status:** FIXED 2026-11-04 on `fix-misc-1-20261104`. Tests added in `tests/test_install_host.py`: `test_client_bodyless_post_retry_after_401_keeps_content_type_and_new_token` and `test_client_post_401_without_refresh_token_raises_without_retry`. The retry-only mutant (`json_body=data or {}` on the retried call) fails the first test and leaves the first-attempt test green. Filed 2026-10-21 from the INT-03 review. Severity Minor. Type: test gap; no production change was needed.

**Observed.** `NetcupSCPClient.post` retries once after a 401 when a refresh token is configured: it calls `refresh_access_token()` and then `_http_json` again. SCP validates `Content-Type` even for bodyless JSON actions (for example preparing a user-ISO upload), so the retried request must still carry `Content-Type: application/json` with no body. `_http_json` (`netcup_scp_client.py:490-497`) sets that header itself for a bodyless POST/PUT/PATCH, so current behavior is correct. The only test is `tests/test_install_host.py:682-693` `test_client_post_without_body_sets_json_content_type` (the first attempt only); nothing exercises the retry, so a regression that rebuilt the retry headers without the content type would pass the suite.

**Why netcup owns it.** The request construction is this client's, and a missing header on the retry would fail only against the live API.

**Required tests (two test cases, no production change): the retry, plus a 401 with no refresh token.** Stub the transport so the first POST to a bodyless endpoint returns 401 and the refresh succeeds. Assert that exactly two requests were made, and that the second carries `Content-Type: application/json`, has `data is None`, and uses the refreshed access token.

**Oracles.**
1. The two-request sequence above passes on current main.
2. Controlled wrong implementation, retry-only mutant: send `json_body={}` (or drop the content type) only on the retried `_http_json` call in `post`; the new retry test must fail while the existing first-attempt test still passes. Removing the bodyless-POST default in `_http_json` fails both tests, so it does not isolate the retry.
3. A 401 with no refresh token configured raises without a retry. No existing test covers this either, so it is the second new case, not a regression guard.

---

## NC-02 — runner provisioning scripts: first-run bugs, host-specific values

**Status:** FIXED on `fix-r1002-findings-20261104`. Found on a reinstalled runner host; the scripts lived in a task directory with the host name, server id and
paths hard-coded.

Bugs fixed in `runner/provision-runner.sh` and `runner/sync-from-devcontainer`:

1. A fresh installation has no `/etc/vbpub/server-id`; every identity check failed. The script now writes it from `RUNNER_SERVER_ID` after the hostname checks (the
   controller has already verified the provider identity).
2. `uv python install` failed when run from the root job's cwd (`/root` is unreadable to `runner`; uv searches the cwd's parents for `uv.toml`): `cd` to the runner home first.
3. `~/.local` and `~/.local/share/uv` were created root-owned by `install -d` parents; chown `~/.local` back to the runner.
4. `sync-from-devcontainer` ran `git bundle verify` outside a repository: it uses a private empty bare repository (`~/src/.verify.git`).
5. Checkouts were created with umask 077 and unreadable to the tester uid: `chmod -R go+rX` after the checkout.
6. The scripts hard-coded the host: values now come from the gitignored `runner/runner.env` (`runner.env.example` documents each variable).

Move of the reviewed scripts (provision-runner, setup-assay-venvs, setup-gate-tools, build-tester-base, build-tester-unified, start-cockpit-runner,
run-cowtree-assay-smokes) into `scripts/netcup/runner/` with a README. Test: `scripts/netcup/tests/test_runner_scripts.py` (shell syntax, no host-specific literal).

## NC-03 — the install wizard uses `StrictHostKeyChecking=no`

**Status:** OPEN. The wizard's post-install SSH steps do not verify the new host key, so the pin written afterwards is trust-on-first-use from `ssh-keyscan`. The
operator cross-checks it against the host's own key file over a key-authenticated session (README "Runner" section). **Fix.** After the install, fetch the host key
from the provider console or the installer's own custom-script output (it can print `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) and pin that.

## NC-04 — the runner account must be uid 1003

**Status:** FIXED on `fix-r1002-findings-20261104` (documented + provisioned). `tester-unified` runs lanes as uid 1003. A runner account at uid 1000 made every Run-Gate
`worktree` lane fail one of three ways (see Run-Gate RG-150): Git `dubious ownership` in the lane, `Permission denied` on `<project>/.run-gate`, or `MDT_COW_ROOT must be
owned by the non-root caller uid`. `provision-runner.sh` now creates the account with `RUNNER_UID` (default 1003) and refuses a different existing uid unless
`RUNNER_UID_ALLOW_CHANGE=1`; `start-cockpit-runner.sh` remaps the cockpit's `vscode` user to it. To migrate a live host: stop the cockpit and BuildKit containers, `groupmod -g`/`usermod -u`,
`chown -R -h` the runner home and the CoW root, restart, rerun `start-cockpit-runner.sh`.

<!-- Id note (2026-11-04): NC-02..NC-04 (runner provisioning, host-key pin, uid 1003) exist only on branch fix-r1002-findings-20261104. The
     ids below start at NC-05 to leave that gap; the controller resolves ordering and the status-table rows on merge. The runner README
     lives at scripts/netcup/runner/README.md on that branch. -->

## NC-05 — r1002 migration of the cgprofile host daemon to the native service (host-setup, pinned wheel)

**Status:** OPEN. Filed 2026-11-04. Blocked on cgprofile CP-41/CP-42 and mdt-cockpit MDT-055.

**Problem.** On r1002 the daemon is a ciu-managed container deployed from a repo checkout that carries two uncommitted hand edits
(`RUNBOOK.md:52-90`): the gates slice name and the slice unit, because the compose template authors both literally. The container also
restarts with dockerd.

**Decision record (operator, 2026-11-04).** The cgprofile daemon becomes a native systemd service from a released wheel, installed and pinned
by `mdt host-setup`; the ciu deployment is retired (CP-44). r1002 migrates first, the shared host later in an idle window.

**Design.** Per-host steps in the RUNBOOK: record the current revision label; `ciu down` of the host profile; set `CGPROFILE_WHEEL`
(version plus sha256) in `/etc/mdt/host-setup.env`; run host-setup (installs the wheel into `/opt/cgprofile`, renders the unit and the
`cgprofile.slice`, restarts only on drift); verify `ctl.sock`, the observer socket, their groups (CP-43) and `version`; run one run-gate lane
with the observer and one `ctl` session; delete the hand edits from the checkout. Rollback: stop the unit, `ciu up --profile host`
from the pinned tag.

**Acceptance.** `systemctl is-active` and the pinned version match; the run-gate and assay probes are unchanged; no uncommitted edits
remain in the checkout; the RUNBOOK documents the sequence and rollback.

## NC-06 — interim: render the cgprofile gates slice for the ciu deployment from host config, no hand edits

**Status:** OPEN. Filed 2026-11-04. Interim (option C of the platform memo) until NC-05 lands; skip it if NC-05 is imminent.

**Problem.** The ciu deployment reads `[cgprofile.daemon] gates_slice` and the slice unit is installed by hand; r1002 needs different
tier names and has local edits to the template.

**Design.** Render both the `gates_slice` value and the `cgprofile.slice` content from `/etc/mdt/host-setup.env` (the tier-name source of
truth) into the ciu override for the host, so the checkout stays clean. No new feature in the daemon.

**Acceptance.** `git status` of the r1002 checkout is clean after an upgrade; the rendered values equal host-setup's tier names; a test of the
rendering with a fake env file.

## NC-07 — restricted `cockpit` ssh key with a forced command, plus a `docker context` recipe in the runner README

**Status:** OPEN. Filed 2026-11-04. Needs `fix-r1002-findings-20261104` merged first (the runner README exists only there). Docs companion:
mdt-cockpit MDT-058.

**Problem.** Entering the runner container takes ssh to the host as `runner` and then `docker exec -u vscode ...` (`RUNBOOK.md:7-10,130`); there is
no one-step shell and no IDE attach. The images have no sshd and no supervisor.

**Decision record (operator, 2026-11-04).** No sshd in containers (a supervisor, persistent host keys, a published port and a second auth
surface for no isolation gain: the container already holds the Docker socket). Instead: a docker context over ssh for the IDE (VS Code
"Attach to Running Container") and a restricted ssh key with a forced command into the container shell.

**Design.**

- `provision-runner.sh` (optionally, off by default) installs a second authorized key for the `runner` account with
  `command="docker exec -it -u vscode -w /workspaces mdt-cockpit-runner zsh -l",restrict` (plus `pty`), `no-port-forwarding`,
  `no-agent-forwarding`, `no-X11-forwarding`, so that key can open only that shell.
- Runner README recipe: `docker context create r1002 --docker host=ssh://runner@<host>`, then VS Code "Attach to Running Container" with the
  context; the shell gets the remapped uid of the runner user. Security note: `runner` is in the docker group, so it is host-root equivalent;
  its keys belong only to operators and the controller. Both mechanisms add no new exposure.
- The forced-command key is separate from the main key and is listed by fingerprint in the README so it can be revoked on its own.

**Acceptance.** A test of `provision-runner.sh` (fake `ssh`/`authorized_keys` paths) writes exactly the restricted line and never a bare key;
a live check: the forced key opens the shell and nothing else (a different command is ignored); the README recipe works from a laptop. After
the lane-uid rollout (run-gate RG-151..153) the "same uid everywhere" wording relaxes via mdt-cockpit MDT-059.

## NC-08 — a provider image install removes the server's snapshots; the listing is empty afterwards

**Status:** OPEN (filed 2026-11-04 from the v1001 reinstall). Before `install-host.py wizard` (ServerImageSetupTask) `scp-api.py snapshots 804027` listed eight snapshots
(the server record said `snapshotCount` 1); after the task finished the listing said "no snapshots for this server", and `snapshots ... revert <name> --dry-run` answered
"existing: none"; `snapshotCount` stayed 1. No snapshot verb was called. An operator who keeps a TrueNAS/rollback snapshot on a host and then reinstalls it with the
wizard loses the rollback. **Fix.** `install-host.py` lists the target's snapshots in its preflight and refuses (or needs `--accept-snapshot-loss`) when any exist; the
README and DESIGN-GUIDE state that a provider reinstall does not preserve snapshots. **Acceptance.** Test with a fake client that returns one snapshot: the wizard refuses without the flag, passes with it.

## NC-09 — the cockpit container has no `run-gate` and no `assay` after `start-cockpit-runner.sh`

**Status:** FIXED on `ops-v1001-20261104` (`setup-cockpit-tools.sh`). `run-lane.sh` calls a controller-local `run-gate` path, but the image ships neither tool; on r1002 they were
installed by hand. `setup-cockpit-tools.sh` creates `~/.venvs/gate-tools` in the cockpit with the pinned Run-Gate wheel (sha256 checked) and the synced checkout's Assay (editable).
Also added: `run-jobs-window.sh` and `run-jobs-compare.sh` (native-R2 `--jobs` comparisons on fresh state; a run-gate lane refuses `--jobs N` above the lane's `judge.mutation.jobs_max`, worktree 1, iobench 4).

## NC-10 — running the mdt host-setup wizard or `install.sh` as root from the runner checkout leaves root-owned `__pycache__` that breaks the next lane

**Status:** OPEN (docs). On v1001, `mdt-host-setup-wizard.py` and `install.sh` run as root from `~/src/vbpub-current/mdt-cockpit/host-setup` created root-owned `__pycache__` dirs; the next
`run-gate smoke` (tester uid 1003) failed with `[Errno 13] Permission denied: './host-setup/__pycache__/mdt-host-setup-wizard...pyc'`. **Fix.** The runner README order step 5 runs host-setup
with `PYTHONDONTWRITEBYTECODE=1` (or from a throw-away copy), or `provision`-time `chown -R` after it. **Acceptance.** README states it; the smoke lane passes after a host-setup run in the documented form.

## NC-11 — `scp-api.py` on a `.env` with the legacy `NETCUP_SCP_API_PROTECTED_*` keys exits 2 before any command; token lookup depends on the working directory

**Status:** OPEN (observed 2026-11-04). The cockpit's `.env` carried the old protected-server key names; every verb stopped with `invalid protected-server denylist: legacy ... setting(s) are no longer read`.
The two keys were renamed in place (values unchanged: the single protected server stays protected). Separately, running the same absolute command from another repository checkout's `scripts/truenas` cwd
answered `missing NETCUP_SCP_API_REFRESH_TOKEN`, so `.env` is found relative to the cwd rather than to the script. **Fix.** Load `.env` from the script directory (documented), and print the rename recipe in the legacy-key error.

## NC-13 — the runner previously created the cow partition after install

**Status:** SUPERSEDED by the single whole-disk `partitioning` plan in `div2-data-area-v2`; no host reinstalled, live use OPEN with the NC-12 reinstall. Severity Minor (operator request).

**Observed.** The previous runner created its CoW partition after installation, which made partition numbering depend on the install layout. The operator wanted data storage laid out during install, with either a partition or LVM volumes.

**Current behavior.** `partitioning.data` declares the CoW partition or LVM volume, and `provision-runner.sh` resolves the planned partition by GPT PARTLABEL `MDT_COW`. The runner has no partition creation path. `install-host.py` passes the generated customScript through unchanged.

**Open.** Not run on a real host; ZFS is not managed (spare LV is left unformatted). Growing an existing data partition/LV in place is manual.

## NC-12 — test hosts were installed with swap in the middle of the disk and a 40 GiB root

**Status:** CODE DONE 2026-11-05 on `div2-disk-layout-20261105`; reinstall of v1001 then r1002 OPEN (`reports/div2-reinstall-RUNBOOK.md` in the release-program directory). Severity Major.

**Observed.** r1002 and v1001 (512 GiB virtual disks) were installed as ESP 243 MiB, `/boot` 977 MiB, 40 GiB ext4 root, four 4 GiB swap partitions, then a 454.8 GiB btrfs `/srv/cow`. Root reached 91-97% (docker 27 GB, runner checkouts, build cache) and could not grow because the swap partitions sit directly behind it. Cause: the predecessor installer required swap directly after root, and `provision-runner.sh` created CoW storage in the last free row. Both layout decisions are superseded by the single `partitioning` plan.

**Current behavior.** The `partitioning` plan defines root, data, the raw iobench reservation, reserve, and tail swap in physical order. The CoW partition is planned at install time and grows with its assigned size; the runner does not allocate it. Reinstall remains OPEN and must use the current plan contract documented in `scripts/debian-install-v2/README.md`.

## NC-14 — assay self-qualification lanes cannot run in the host cockpit

**Status:** FIXED in repository 2026-11-10. Severity Major. Live host acceptance was not run.

**Observed.** `remote-gate run r1002 <worktree> self-qualification-provisional --project-dir assay` (run `assay-selfqual-cov-a`) ended `ERROR; exit_code 2` after 3 s with only `self-qualification-container: the selected worktree is not inside the declared /workspaces/vbpub bind`. `assay/tools/self-qualification-container.sh` requires `findmnt --target <worktree>` to report TARGET `/workspaces/vbpub`; in the host cockpit `/workspaces` is the mount (`~runner/checkouts`), so TARGET is `/workspaces`.

**Fix and evidence.** `start-cockpit-runner.sh` now adds the documented nested bind `--mount type=bind,source="$RUNNER_HOME/checkouts/vbpub",target=/workspaces/vbpub,consistency=cached`. `tests/test_runner_scripts.py::test_cockpit_launcher_binds_vbpub_checkout_at_assay_workspace_path` renders the Docker argv with a fake Docker executable and asserts the exact source, target and consistency option. The remote-gate diagnostic remains useful if the bind is absent on a running host.

**Residual acceptance.** No host was accessed for this change. A live `self-qualification*` replay is not claimed here and remains part of the fresh-host acceptance.

## NC-15 — host cockpit run-gate is older than the controller's

**Status:** OPEN. Severity Minor.

**Observed.** `docker exec mdt-cockpit-runner <controller-local-run-gate-path> --version`: r1002 24.3.0, v1001 25.0.0; `~/.venvs/gate-tools2502/bin/run-gate` is 25.0.2 on both; the controller runs 25.1.1.

**Needed.** After the R2 campaigns on a host finish, `runner/setup-cockpit-run-gate.sh 25.1.1 <sha256>` (own venv, the default is left alone) and add that venv to the `run_gate_bins` of the vbpub and dstdns profiles. No host was changed by this entry.

## NC-16 — controller Ctrl-C can leave remote evidence collection running

Ctrl-C on the controller during collection can leave the remote `ssh`/collector
running after the controller exits; it is read-only, but the 300 s limit is
controller-side and no longer applies after interruption.

## NC-17 — `update-host-pin.sh` counts the `#` banner line of `ssh-keyscan`

**Status:** FIXED 2026-11-09 in `d951f417d`. `tests/test_update_host_pin.py` covers banner-plus-one-key and still-refused two-key scans.

**Priority:** minor. Source: `reports/v1001-reinstall-EXEC-LOG.md` (17:21Z) and `reports/div2-reinstall-v1001-RESULT.md`.

**Finding.** `update-host-pin.sh:56-62` runs `ssh-keyscan -T 10 -t ed25519` and requires `grep -c '[^[:space:]]'` of the output to be 1. The controller's OpenSSH `ssh-keyscan` now prints a `# host:22 SSH-2.0-...` banner comment to stdout, so a correct single-key scan counts 2 lines and the script refuses ("expected exactly one scanned ed25519 host key"). The 2026-11-08 v1001 reinstall ran the unmodified script with a PATH shim that drops `#` lines; the script's own rescan and fingerprint-equality check still applied.

**Fix.** Count only key lines: `grep -vc '^#'` (or `grep -c '^[^#[:space:]]'`), and use the same filtered output for the stored pin. Add a test in `tests/test_update_host_pin.py` with a fake `ssh-keyscan` that prints a banner line plus one key, and one that prints two keys.

**Close when.** The banner case passes, the two-key case still refuses, and a mutation of the filter fails a test.

## NC-18 — the cockpit start script cannot run without the host-endpoint record

**Status:** FIXED 2026-11-09 in `d951f417d` by choosing the reviewed mandatory-host-setup rule. The runner README, consumers guide, and design guide state that no raw-socket degraded mode is supported.

**Priority:** minor, decision. Source: `reports/v1001-reinstall-EXEC-LOG.md` (17:30Z), `reports/div2-reinstall-v1001-RESULT.md`.

**Finding.** `runner/start-cockpit-runner.sh:7` calls `load_host_endpoints /etc/host-endpoints.env || exit $?` and needs the Docker-API, cgprofile, topos and BuildKit socket directories that mdt host-setup creates. On the freshly reinstalled v1001 (host-setup deliberately not yet run) the pinned script refused, and the operator used a local, unreviewed variant that bound the raw docker socket with no cgprofile or BuildKit mounts. That variant is a deviation from the reviewed scripts.

**Decide.** Either state in the runner README that host-setup is mandatory before the cockpit (the current behavior, NC-20), or provide a reviewed degraded mode (an explicit flag, never a silent default) that names what is missing (no observer socket means run-gate lanes with `resource_observer = "cgprofile"` refuse; no BuildKit mount means no governed builds).

**Close when.** The decision is documented and, if a degraded mode is chosen, a test covers both the flag and the refusal text.

## NC-19 — `provision-runner.sh` does not say which CoW partition it selected

**Status:** FIXED 2026-11-09 in `d951f417d`. `tests/test_cow_select.py` covers the selected-device message and both reused and successfully formatted paths in `provision-runner.sh`.

**Priority:** nit. Source: `reports/v1001-reinstall-EXEC-LOG.md` (17:26-17:32Z).

**Finding.** `runner/provision-runner.sh:70-100` resolves the CoW volume by `PARTLABEL=MDT_COW` (`cow_select_device`) and reuses an already-mounted btrfs, but prints no line naming the selected device, the match rule, or "reused, no mkfs". On v1001 the operator had to infer from the log that `/dev/vda4` was selected by PARTLABEL and that no mkfs or partition command ran.

**Fix.** Print one line after the selection: `selected MDT_COW by PARTLABEL: <device> (<fs type>, reused|formatted)`. Cover it in the provisioning test that already exercises `cow_select_device`.

**Close when.** The line appears in both the reuse and the format paths in a test.

## NC-20 — the runner README "Order" runs cockpit-dependent steps before host-setup

**Status:** FIXED. `d951f417d` moved host-setup ahead of the cockpit; the 2026-11-10 host-plan update orders all seven README steps across P1–P15 and places source sync before host-setup.

**Priority:** minor, docs. Source: `reports/div2-reinstall-v1001-RESULT.md` (runner provisioning section), `reports/v1001-reinstall-EXEC-LOG.md`.

**Finding.** The earlier order correction put host-setup before cockpit-dependent steps but still listed host-setup before `sync-from-devcontainer`, even though host-setup runs from the synced runner checkout. The host-plan dependency graph also distinguishes the required order: source sync, root-PATH Augeaswrap, host-setup, service checks and images, then cockpit and acceptance.

**Fix.** The seven numbered runner README steps now follow P1–P15: provider reinstall, host-key/layout check, runner provisioning, source sync, Augeaswrap then host-setup, services/checks/venvs/images, and cockpit/registration/acceptance. They state what works after provisioning alone and which host facts each later stage needs.

**Close when.** README "Order" names each dependency and matches the behavior observed on v1001.

## NC-21 — the cut-over recipe for a reinstalled host without mdt host-setup exists only in agent logs, not in the repo

**Priority:** should-fix (the working path is undocumented). Filed 2026-11-09, status OPEN. Source: cutover and cockpit execution logs and handoff notes retained as scratch evidence. Supersedes the decision half of NC-18 (it chooses the degraded path as a supported one) and extends NC-20.

**Status:** DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; implementation and fresh-host acceptance OPEN. The documented prerequisites, commands, verification and rollback are in `runner/README.md` under "Host without mdt host-setup"; the Netcup README links to that section.

**Finding.** On 2026-11-08 v1001 was moved to cgprofile 3.0.0 and a cgprofile-aware cockpit without mdt host-setup. The steps that worked are in no file under `scripts/netcup/`:
1. cgprofile 3.0.0 install on the host: `cgprofiled install` from the pinned wheels (cgprofile 3.0.0, cli-extended 0.5.3, augeaswrap), `--dry-run --json` first, then apply; it creates the control and observer sockets, the `cgprofile-control` and `cgprofile-observer` groups and publishes the six `CGPROFILE_*` keys to `/etc/host-endpoints.env` (`v1001-cutover-EXEC-LOG.md`, 21:33Z-21:34Z).
2. The cockpit start variant `start-cockpit-runner-cgprofile.sh` (kept at `v1001/runner-scripts-local/` and `v1001/cockpit-20261108/`, outside the repo): reads `CGPROFILE_*` from `/etc/host-endpoints.env` with a strict regex (nothing typed by hand), bind-mounts the control and observer socket directories read-only at identity paths, adds `--group-add <control gid>` (986), and sets a container-only `MDT_HOST_DOCKER_GID` (RG-252). Rollback script: `start-cockpit-runner-nohostsetup.sh`.
3. run-gate 25.4.0 installed by the existing `scripts/netcup/runner/setup-cockpit-run-gate.sh 25.4.0 <sha>` as the second venv `gate-tools2540` next to the default 25.0.2.
4. Minimal slice units where host-setup is absent: `dev-interactive.slice` as a limit-free marker unit, and `dev-gates.slice` with `CPUWeight=100`, `IOWeight=101` and `MemoryMax=6G`; size these from `MemTotal` as in NC-25.
`runner/start-cockpit-runner.sh` and `runner/README.md` know none of this (NC-18, NC-20).

**Fix.** Move the supported path into `scripts/netcup/runner/`: fold the cgprofile variant into `start-cockpit-runner.sh` (a mode chosen by the presence of the record, with a clear refusal naming the missing keys), add a `provision-cgprofile.sh` (or a README section) for steps 1 and 4 with the dry-run-first rule and the pinned wheel hashes taken from `runner.env`, and extend the README "Order" with a "host without mdt host-setup" path and its rollback. The named second Run-Gate venv already has its supported installer in `setup-cockpit-run-gate.sh`. Test the rendered argv of the start script offline (no Docker) for both modes.

**Close when.** A freshly reinstalled host reaches a working cgprofile-observed cockpit using only files in the repo and `runner.env`, the README documents the order and the rollback, and the rendered-argv tests pass.

## NC-22 — the cockpit image carries only the old `cgpctl` 2.1.0 client (no `cgprofilectl`)

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Found by the v1001 cockpit agent: with cgprofile 3.0.0 on the host the cockpit image still holds only the 2.1.0 `cgpctl` client and has no `cgprofilectl`. Source: `HANDOFF-STATE-20261107.md` (22:25Z "Backlog to file"), `reports/v1001-cockpit-EXEC-LOG.md` (21:46Z, the 25.0.2 cockpit run-gate ignores `CGPROFILE_*`).

**Finding.** The estate image pipeline lists the wheel in `mdt-cockpit/pip/wheels.list` (entry `cgprofile`, clients `cgprofile` and `cgprofilectl` from 2.0.0 on); the image on v1001 pre-dates the 3.0.0 wheel. Which pipeline produced the v1001 cockpit image (mdt-cockpit image build versus the runner scripts `build-tester-*.sh` / `setup-cockpit-tools.sh`) is to be confirmed first; the rebuild with the 3.0.0 CLI is tracked as MDT-090 in `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md`, the runner side only has to use and check it.

**Fix.** Rebuild or retag the cockpit image with the 3.0.0 wheel (MDT-090), and have `setup-cockpit-tools.sh` verify after the build that `cgprofilectl --version` reports 3.0.x inside the container, failing with the image tag named.

**Close when.** `docker exec` into the v1001 cockpit shows `cgprofilectl` 3.0.x and no stale `cgpctl`, and `setup-cockpit-tools.sh` checks it.

## NC-23 — Run-Gate host profile default and campaign-state pin hold

**Priority:** decision. Historical state recorded 2026-11-09: `gate-tools2540` (Run-Gate 25.4.0) was installed next to the default 25.0.2 on v1001 and used for an observer-lane smoke and the Assay 12.1.0 release attempt; the queue still resolved 25.0.2. These are historical observations, not current host evidence.

**Decision HP-D10 (operator accepted 2026-11-10).** Both test hosts target Run-Gate 25.4.1 as the default only after old campaign and resume state has been collected. Until collection is complete, preserve the existing pins and venvs so campaigns can resume; retain prior venvs for rollback. This branch records the policy only: no host hosts-file was edited and neither live host version nor profile was checked.

**Close when.** Old campaign state is collected, both host profiles resolve Run-Gate 25.4.1 by default, the live version is verified on each host, and the prior venvs remain available for rollback.

## NC-24 — the host-sat queue has a per-host pause flag (`host-sat-queue.disabled.<host>`) that no runner doc mentions

**Priority:** minor, docs. Filed 2026-11-09.

**Status:** FIXED at `ac0a78dcd`. The queue was relocated to
`scripts/netcup/runner/host-sat-queue.sh`, and the global/per-host flags, loop
and drain behavior, operator commands, INFRA back-off, and auto-pause are
documented in `scripts/netcup/runner/README.md` under "Host-sat queue pause
flags". The controller install and update sequence is in
`scripts/controller/README.md`.

## NC-25 — assay's `cgroup-parent.sh` refuses a host without mdt host-setup slice units; the minimal marker-unit workaround is not recorded

**Priority:** should-fix (blocks any assay release or self-qualification lane on such a host). Filed 2026-11-09, status OPEN; referenced from RG-252 in `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`. Source: `HANDOFF-STATE-20261107.md` (23:20Z attempt a, 23:35Z slice fix), assay 12.1.0 release attempt a.

**Status:** DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; automated provisioning and fresh-host acceptance OPEN. The minimal marker-unit bodies, installation, verification, rationale and rollback are in `runner/README.md` under "Install the two slice marker units"; details absent from the source trail are marked `to be confirmed`.

**Finding.** The release attempt on v1001 failed in seconds with `cgroup-parent: Docker-host unit "dev-interactive.slice" is not a loaded installed slice (FragmentPath=)`. `assay/tools/cgroup-parent.sh` requires, for each named slice: a loaded unit with a real `FragmentPath` (`:324-332`; a runtime-generated slice is refused); positive effective `CPUWeight` and `IOWeight` properties on the gates slice (`:354-355`); at least one non-default control; and, with `--memory-reserve-bytes`, a finite `memory.max` with the requested headroom (2 GiB for the B105 self-qualification lanes; `:394-402`). It does not test whether the weight properties were explicitly assigned in the unit file. mdt host-setup installs these units; v1001 was reinstalled without it. Fixed by hand on 2026-11-08 with `/etc/systemd/system/dev-interactive.slice` as a limit-free marker unit and `/etc/systemd/system/dev-gates.slice` configured with `CPUWeight=100`, `IOWeight=101` and `MemoryMax=6G`. Revert: `rm` both files and `systemctl daemon-reload`; the before-file of `MemoryMax` is on v1001 under `/root/slicefix-20261108/`.

**Fix.** Ship the two unit files as templates under `scripts/netcup/runner/` (sizes derived from `MemTotal`, never hard-coded: `MemoryMax = MemTotal - reserve`, reserve at least 2 GiB), install them from `provision-runner.sh` when host-setup is absent, and make the script refuse to overwrite a host-setup-owned unit. Record in the runner README why a marker unit satisfies the check and what it does not do (no governance). Consider whether assay should accept a documented "no-governance host" mode instead of units that mimic governance, as an assay backlog item.

**Close when.** A freshly reinstalled host without host-setup passes `cgroup-parent.sh --memory-reserve-bytes 2147483648` after `provision-runner.sh` alone, and the units are removed cleanly by a documented revert.

## NC-26 — cockpit restart policy `no` leaves remote gates down after a host reboot

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Source: the r1002 queue incident report and host log retained as scratch evidence;
`scripts/netcup/runner/start-cockpit-runner.sh` (`docker run` omits `--restart`), plus the pinned launch variants under the release-program artifacts
(`r1002/start-cockpit-runner-20261104.sh`, `v1001/runner-scripts*/start-cockpit-runner*.sh`) which also omit it.

**Status:** FIXED at `726550ed8`. `start-cockpit-runner.sh` now passes `--restart unless-stopped`, an argv-level fake-Docker
test covers every tracked `start-cockpit-runner*.sh`, and `runner/README.md` documents the reboot behavior plus the applied
`docker update --restart unless-stopped` workaround and its `--restart no` revert. The operator applied the live update
to both test hosts on 2026-11-09. The full host-reboot acceptance is still OPEN; no host access was used for this fix.

**Finding.** The r1002 host rebooted at 02:02Z on 2026-11-09 while a queue lane was running. Docker's default restart policy is `no`, and the cockpit launcher never
sets one, so `mdt-cockpit-runner` remained `Exited (143)`. The interrupted lane became `INFRA:NO_FINISH`; from 03:22Z every new launch failed preflight with rc 4,
and the fifth consecutive INFRA result at 03:52Z touched `host-sat-queue.disabled.r1002`. The operator restarted the cockpit and removed the flag at 04:57Z, after which
the queue launched a lane normally. The first failed queue preflight was recorded at 03:22Z, about 1 h 20 min after the 02:02Z reboot; the queue remained paused until the
operator recovery at 04:57Z.

**Fix.** Add `--restart unless-stopped` to the `docker run` invocation in every checked-in or pinned `start-cockpit-runner*.sh` variant retained for a host, replacing the
implicit `--restart no`. Add an offline test/check that each such launcher carries the policy. Document in `runner/README.md` that the cockpit returns after a host reboot
unless it was explicitly stopped, and how to inspect or revert the policy. The already-applied `docker update` is only the live workaround; the launchers remain the
durable fix.

**Close when.** A reboot of a test host brings `mdt-cockpit-runner` back by itself, a queue lane starts without a manual `docker start`, the launcher policy check passes, and
the runner README documents the reboot behavior.

## NC-27 — a stopped cockpit is misreported as a stale run-gate, and the queue logs only the misleading hint

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Source: the r1002 queue incident report and runtime log retained as scratch evidence; `scripts/netcup/runner/remote-gate.py` (`check_prereqs`/`preflight`, especially lines 638-711).

**Status:** FIXED at `726550ed8`. A failed cockpit exec now stops run-gate probing, preserving the cockpit message as the
first prerequisite without a fabricated `no run-gate` consequence. The newer-run-gate hint is emitted only when the
first unmet prerequisite is the run-gate floor, and the error's final line repeats the first prerequisite so the
queue's `tail -1` excerpt remains causal. Regression tests cover the stopped-cockpit and old-run-gate cases.

**Finding.** `check_prereqs` records `cockpit container ... is not running`, but continues trying to obtain every configured run-gate version through `docker exec`. Those
attempts necessarily fail, so it also appends `no run-gate ... (tried: ... unusable)`. `preflight` then adds the `a newer run-gate` hint whenever any missing message
starts with `no run-gate`, even though the first and causal problem is that the cockpit is down. `host-sat-queue.sh` reports only `tail -1` of the launch error, so the
queue log shows the run-gate hint and omits `cockpit container ... is not running`.

Existing preflight coverage is in `scripts/netcup/tests/test_remote_gate_repos.py`:
`test_preflight_script_reports_every_unmet_prerequisite` pins the raw script's cockpit/image/run-gate order, and
`test_missing_prerequisites_stop_before_any_upload_with_a_clear_error` pins the upload boundary. Neither test covers the misleading hint when the cockpit is down, nor the
queue-facing excerpt. That is the test gap covered by this entry.

**Fix.** Preserve `PREREQ` messages in check order and track whether the cockpit was actually executable and the run-gate list actually read. When it was not, do not emit the
`no run-gate` consequence or the newer-run-gate hint; report the cockpit message first. When the cockpit is up but every candidate is below the floor, emit the run-gate
message and hint. Make the queue's launch-failure excerpt select the first prerequisite/causal message rather than `tail -1`. Extend
`test_remote_gate_repos.py` with a stopped-cockpit case and an old-run-gate case, asserting order and hint suppression/presence, plus a small excerpt-selection test for
the queue-facing first-line behavior.

**Close when.** A stopped cockpit yields `cockpit container ... is not running` as the first prerequisite and the only run-gate-related repair hint, no `no run-gate`
consequence is fabricated, the queue log records that first prerequisite even when independent checks also fail, and the added tests pass.

## NC-28 — queue self-pause has no controller-visible who/why/when

**Priority:** should-fix. Filed 2026-11-09.

**Status:** FIXED at `12ab909a1`. Automatic per-host pauses atomically record
the actor, UTC time, INFRA streak, latest lane/result/run/log, and recovery
instruction in the flag; the results TSV receives a matching `PAUSED` row.
Both `host-sat-queue.sh status` and `ctl-status.sh` display structured pause
metadata and identify legacy empty flags without inventing details.

**Evidence:** `scripts/netcup/runner/host-sat-queue.sh` (`write_auto_pause`,
`status`), `scripts/controller/ctl-status.sh`,
`scripts/netcup/runner/tests/test-queue.sh`,
`scripts/controller/tests/test-ctl-status.sh`, and the queue/status sections
of `scripts/netcup/runner/README.md` and `scripts/controller/README.md`.

**Fix.** Use the smallest coherent signal set for automatic per-host pauses: have the automatic pause atomically write a flag body with `at` (UTC), `by` (`queue-auto`),
and `why` (the consecutive-INFRA count, first prerequisite/reason, and latest run/log reference); record one `PAUSED` row in `logs/host-sat-results.tsv` using the existing
TSV shape with the same who/why/when text in its fields; and make both `host-sat-queue.sh status` and `ctl-status.sh` display the recorded pause summary. For legacy empty
flags, both views must say the metadata is absent rather than invent it. This entry covers the queue's automatic per-host pause only; operator-created and global flags
are outside its scope.

**Close when.** An automatic pause atomically records actor `queue-auto`, UTC time, and reason in the host flag and as a corresponding `PAUSED` row in the results TSV.
Both `host-sat-queue.sh status` and `ctl-status.sh` show that actor, reason, and time, and one regression check exercises both views for a structured automatic pause and
an empty legacy flag, asserting the latter reports absent metadata instead of inventing it. Discovering a stopped-cockpit auto-pause must not require reading the
per-host log.

## NC-31 — queue restart does not prove the old loop PID exited and the disabled queue logged that it did not launch a replacement

**Priority:** should-fix. Filed 2026-11-10, status OPEN. Source: the 2026-11-10 session audit; the host-queue lifecycle lesson; NC-29 and NC-24.

**Problem.** A safe restart has two facts that the current controller-local procedure does not require together: the exact old queue-loop PID exited, and the disabled queue has emitted the named no-launch evidence (`disabled flag present: not launching`) before a replacement loop starts. A stale `.current` marker made the ordering ambiguous in the observed restart, while a pid-only or log-file-tail check can pass after another loop has already written to the same log. NC-29 owns the stale `.current` drain/removal contract; this item owns the restart proof boundary.

**Evidence.** The 2026-11-10 session audit records the handoff rule but found no tested controller-local procedure. The host-sat queue logs historically append, so reading a matching line without knowing which PID/attempt emitted it is not proof for this restart.

**Minimal solution.** Add one supported restart procedure/helper for the host-sat queue:

1. Set the per-host disabled flag before stopping anything.
2. Stop the named loop and wait for the exact recorded PID to exit, bounded with a refusal timeout that identifies the still-running process and its log/subtree.
3. Verify the log for that stopped loop contains the exact `disabled flag present: not launching` line after or at the attempted launch boundary.
4. Wait for NC-29's exact `.current` drain condition.
5. Start exactly one replacement, record its PID and configuration, and verify its startup line.

A mismatch between the recorded PID and the line's emitting attempt, a missing no-launch line, a surviving child, or a stale `.current` marker refuses with the unresolved state; it never proceeds on pid-file absence alone.

**Where.**

- `scripts/netcup/runner/README.md`: queue restart sequence and evidence contract.
- The external `host-sat-queue.sh`/controller restart helper, carried with the supported queue relocation tracked by NC-24.
- An offline fake queue/log fixture under `scripts/netcup/tests/` until the queue itself is relocated.

**Acceptance idea.** With a fake queue loop, two distinct run directories/logs and a recorded PID, the helper passes only when the old PID has exited and the disabled attempt logged exactly `disabled flag present: not launching` before the replacement's first line. Reusing the old PID line, omitting the disabled evidence, starting before PID exit, or leaving `.current` present must each refuse. Hand-removing the PID-exit wait or the line-to-attempt comparison must fail its focused case.

**Priority.** Should-fix. This closes the remaining ambiguity in a procedure that already caused a dual-queue outage.

**Close when.** The restart helper/procedure is documented and tested with fake queues, and a live r1002/v1001 restart records the old PID exit, exact disabled-no-launch line, `.current` drain, and new loop startup without manual inference.

## NC-29 — queue `stop` leaves the in-flight lane's `.current` marker stale and can hold both host queues down

**Priority:** should-fix. Filed 2026-11-10, status OPEN. Source: controller session record 2026-11-10; `scripts/netcup/runner/README.md` "Host-sat queue pause flags"; the pending external host-sat queue relocation tracked by NC-24.

**Problem.** The queue loop's TERM trap removes its own pid file immediately and exits. It does not wait for or clear the in-flight lane's `<host>.current` marker, so `stop` can leave a marker naming a lane that has not finished. A subsequent start treats that marker as outstanding work (or reports the wrong current lane), and an operator following an incomplete stop/start sequence can take both queues down while diagnosing it. The README already tells installation/restart procedures to pause by flag and wait for `.current` to clear, but the queue verb itself does not enforce that safe sequence.

**Evidence.** The controller session record 2026-11-10 records a premature stop on 2026-11-09 that left both host queues down for 18 minutes and left the in-flight lane's `.current` marker stale. The pause path itself was designed not to cancel an in-flight lane; `stop` is a different, less safe lifecycle action.

**Proposed minimal solution.** Make the supported lifecycle drain by default:

1. Document and implement `stop`/restart as: set the per-host pause flag, wait until the current lane finishes and `.current` is absent, then terminate the idle loop.
2. A dirty or stale `.current` marker causes `status` to name it and `start` to report/refuse it rather than pretending the host is idle. Do not add forced-cancel or a new status taxonomy in this fix; either would be a separate item with its own demonstrated requirement.

If the external queue script remains outside the repository, put the exact safe sequence in `runner/README.md` now and carry the implementation with its supported relocation (NC-24).

**Where.**

- `scripts/netcup/runner/README.md`: "Host-sat queue pause flags" and install/restart procedure.
- The host-sat queue TERM trap, `stop`, `status`, and `.current` lifecycle implementation (currently external; eventual supported home NC-24).
- `scripts/netcup/runner/remote-gate.py`: the job/lane completion marker contract.
- Queue lifecycle tests under `scripts/netcup/tests/` when the script is relocated; an offline shell fixture until then.

**Acceptance test idea.** With a fake long-running lane, `stop` sets the pause flag and leaves the loop alive until the lane removes `.current`, then exits cleanly. A forged stale marker causes `status` to name it and `start` to report/refuse rather than launch a duplicate. Hand-removing the drain wait or stale-marker refusal must fail the focused lifecycle test.

**Close when.** The default `stop` drains the in-flight lane before the loop exits, a stale `.current` marker makes `status` and `start` report or refuse it, the README carries the exact safe sequence, and the focused lifecycle checks pass on a clean final commit. If the external queue implementation remains unavailable, a recorded decision to document the drain-before-stop procedure as a known manual constraint closes this item only with the exact sequence, stale-marker recovery, and follow-up owner at NC-24.

**Priority.** Should-fix. The observed 18-minute dual-queue outage makes this a cheap operational reliability fix.

## NC-30 — cgprofile and Run-Gate version compatibility is undocumented and preflight does not name an incompatible pair

**Priority:** major. Filed 2026-11-10, status OPEN; evidence extended 2026-11-11 from the host campaigns. Source: controller session records 2026-11-10 and 2026-11-11; related to NC-15, NC-21, and NC-23.

**Problem.** Test hosts can carry different cgprofile and Run-Gate versions. r1002 was observed with cgprofile 2.0.0 and Run-Gate 25.0.2, a legacy pair that works with the old path/CLI. Run-Gate >=25.4 uses cgprofile 3.0.0's host socket layout and CLI. An incompatible pair can pass startup/preflight until an observer lane fails with an unrelated-looking endpoint, version, or missing-client error, and operators have no matrix saying which pairing is supported.

**Evidence.** The controller session record 2026-11-10 records one test host with cgprofile 2.0.0 plus Run-Gate 25.0.2, and states that Run-Gate >=25.4 requires the 3.0.0 socket layout. The host-campaign controller session record 2026-11-11 then found live skew across two test hosts: one had cgprofile 2.0.0 and only a working Run-Gate up to 25.0.2, while the other had cgprofile 3.0.0 and Run-Gate 25.4.0. The 25.4.0 rule from the earlier host rehearsal did not transfer to the 2.0.0 host, and the campaign had to use temporary host-local `run-lane.sh` copies pointing at different versioned binaries. The existing records NC-15 (run-gate version skew), NC-21 (3.0.0 cutover recipe), and NC-23 (25.0.2 pin hold) describe the individual host transitions but not the compatibility relation between the two products.

**Proposed minimal solution.** Add one compatibility matrix to `scripts/netcup/runner/README.md` and the remote-gate preflight contract. Record only actually supported/tested pairs as supported:

| cgprofile | Run-Gate | Status |
|---|---|---|
| 2.0.0 | 25.0.2 | supported legacy pair, including the 2.x client/socket contract |
| 3.0.x | >=25.4.0 | supported current pair and required 3.0 socket layout |

Rows outside these pairs are unsupported until tested; do not infer compatibility from semver. The remote-gate `check_prereqs` preflight reads the selected Run-Gate version and cgprofile version/endpoint contract, then refuses with both exact versions, the supported upgrade direction, and the host-specific runbook section. It must not suggest downgrading Run-Gate when the host has already cut over to 3.0, nor upgrading cgprofile when the installed Run-Gate predates the 3.0 contract.

Implement the host-campaign F2 requirement as a first-class per-host `run_gate_bin` selector. The source is the host profile's environment plus the private hosts-file `run_gate_bins`; `run-lane.sh` selects the exact versioned binary from that source and never infers the version from the container's default `PATH`. A copied script with one hard-coded binary path is a temporary diagnostic workaround, not the durable contract. Record the selected path/version in campaign metadata and verify it against the cgprofile pair before launch.

Document the observed campaign pairs as host inventory examples, with the exact change needed to reach a supported pair and the rollback. The cockpit verifier must check the selected run-gate binary-specific version rather than assume the default `run-gate` on `PATH`, and the remote-gate preflight must read that same selected binary.

**Where.**

- `scripts/netcup/runner/README.md`: compatibility matrix and per-host transition notes.
- `scripts/netcup/runner/remote-gate.py`: `check_prereqs`/`preflight`.
- `scripts/netcup/tests/test_remote_gate_repos.py` and the runner-script tests.
- NC-15/NC-21/NC-23 remain the owners of the individual host upgrades and pin decisions.

**Acceptance test idea.** Fixture a 2.0.0/25.0.2 pair and a 3.0.x/25.4.0 pair and assert both pass with their expected contract. Fixture two hosts whose `run_gate_bins` resolve different versioned paths and assert each `run-lane.sh` invocation records and executes its own selected binary rather than a shared default. Assert 2.0.0 with 25.4.0 refuses before upload/run, naming both versions and the 3.0 cutover remedy; assert 3.0.x with a pre-25.4 binary refuses with the opposite correctly directed remedy. Hand-removing the version comparison, replacing the selector with one hard-coded path, or reversing a remedy must fail the relevant case.

**Close when.** The supported-pair, both mismatched-pair, selected-binary, and correctly directed remedy cases pass on a clean final commit; the compatibility matrix and per-host transitions are documented; and the preflight names the observed pair before upload/run. A recorded operator decision to retain manual version management also closes the item when the matrix records only tested pairs, unsupported pairs are explicit, and the per-host runbook/recovery procedure is complete.

**Priority.** Major. Version skew is already live across test hosts, and a preflight naming the exact incompatible pair prevents observer-lane failures from being diagnosed as product defects.

## NC-32 — T3 re-supervision and INFRA retry reuse existing job and campaign names

**Priority:** should-fix. Filed 2026-11-10 from `test-methodology-REVIEW4.md` §5
S16. Status OPEN.

**Finding.** The T3 design names a campaign job
`t3-<tool>-<tag>-<lane>`, but `runner/run-job` refuses an existing job directory
(`job already exists`, exit 3). A re-supervision that reuses that name therefore
never starts the new supervisor and the entry can cycle in `resume`. The same
collision affects an INFRA retry: `run-r2-campaign.sh` leaves
`campaigns/<name>/final-exit`, so collect observes the failed run's stale exit
immediately. A new supervisor also restarts its local attempt counter at 1 and
overwrites the previous `attempt-NN` logs.

**Fix.** Give each supervisor start a fresh job name,
`t3-<tool>-<tag>-<lane>-s<N>`, where `N` is the entry's supervisor-start count.
Keep the campaign name `t3-<tool>-<tag>-<lane>` stable so the per-campaign lock
and persistent checkout resume state carry across starts. Before launching a
recreated INFRA-retry entry, move the old `campaigns/<name>/` aside to
`campaigns/<name>.prev-<utc>`. Record attempt logs per supervisor start (or
preserve the old start directory another explicit way) rather than overwriting
them. Do not delete the checkout; its `.assay` state is what makes the retry
resume.

**Close when.** Killing a supervisor and re-claiming the entry on the same host
starts a new job, acquires the same campaign lock, and continues from the last
candidate. An INFRA retry moves the failed campaign directory aside, does not
observe its old `final-exit`, and retains the prior start's attempt evidence.
Offline fake-command tests cover both paths.

## NC-33 — T3 import repoints the host's shared checkout symlink

**Priority:** should-fix. Filed 2026-11-10 from `test-methodology-REVIEW4.md` §5
S17. Status OPEN.

**Finding.** `runner/sync-from-devcontainer` updates
`~/src/vbpub-current` to the imported `~/checkouts/<commit>` tree. Host build
and setup scripts obtain their source checkout through `source_repo()` in
`runner/lib.sh`, which reads that link. These scripts include tester image
builds, gate/cockpit tool setup and Assay venv setup. After a T3 import the
link therefore names a release-tag tree; a later rebuild or setup can silently
use an old commit.

**Fix.** Preserve the link's previous target and restore it after the import
when it was a symlink. If it did not exist, remove the temporary link after the
import. Do not make the T3 checkout the new durable host source. Any
build/setup invocation that deliberately needs a release tree must pass an
explicit expected commit rather than relying on the shared link.

**Close when.** A fake `sync-from-devcontainer` test proves an existing link is
byte-for-byte restored, an absent link stays absent, the imported checkout
remains usable directly, and no tracked build/setup script depends on
`vbpub-current` changing as a side effect of T3 sync.

## NC-34 — a passing CMRU campaign has no fresh Assay verdict and exits 70

**Priority:** should-fix. Filed 2026-11-10 from `test-methodology-REVIEW4.md` §5
S18. Status OPEN.

**Finding.** `cmru/tools/mutation_campaign.py` is a consumer-side evidence
runner, not an Assay rigor claim. It writes `.assay/mutation-cmru.json`.
`run-r2-campaign.sh` nevertheless requires a fresh Assay
`.assay/verdict-<assay_lane>.json`; a CMRU campaign with `final-exit` 0 but no
Assay verdict exits 70. The ledger reads `final-exit`, so the row is still
PASS, but the job status looks like an infrastructure failure and the evidence
path is undocumented.

**Fix.** Define both evidence modes at the T3 campaign launch. Assay-verdict
lanes keep the existing fresh-verdict requirement. Command-evidence lanes such
as CMRU name the fresh evidence artifact that closes their campaign; the
supervisor copies it and exits 0 when `final-exit` is 0 and that artifact was
written during the current supervision. If the declared evidence is absent or
stale, keep the loud non-success exit. The ledger continues to derive verdict
from `final-exit`, while the run directory carries the evidence artifact.

**Close when.** A fake CMRU-shaped campaign writes a fresh
`.assay/mutation-cmru.json` and exits 0 with `final-exit` 0; an absent or
pre-supervision artifact still fails visibly; an Assay lane without a fresh
verdict still exits 70. The T3 docs name `final-exit` as the ledger source.

## NC-35 — mixed endpoint-publisher rollback can delete a shared aggregate

**Priority:** minor, rollback documentation. Filed 2026-11-10. Source: `runner/README.md` rollback steps and the endpoint publishers in host-setup, cgprofile and topos.

**Finding.** The observed rollback removed `/etc/host-endpoints.env` and `/etc/profile.d/host-endpoints.sh` after cgprofile was the only endpoint publisher. The checked-in cgprofile uninstall removes its own fragment, while topos and host-setup publish separate fragments. On a mixed-publisher host, deleting the aggregate and profile loader can hide still-owned endpoints; removing the fragment directory can also fail or discard another publisher's state. Repository code describes possible publishers but cannot establish which fragments and services exist on a particular host.

**Needed.** Before writing a mixed-host cleanup command, collect a read-only inventory on that host of the endpoint fragments, their owning services and install state, the aggregate and profile-loader file types/owners, and each publisher's supported uninstall behavior. Derive an argv that removes only the intended publisher's artifacts and leaves other endpoint entries available. Until that host evidence exists, use only the sole-publisher rollback documented for the observed host; do not run it on a mixed record.

**Close when.** The live inventory is recorded, the exact cleanup argv is documented, the targeted publisher is removed, remaining publishers regenerate their endpoint entries, and the host checker confirms the surviving endpoints.

## NC-36 — the R2 supervisor relaunches every exit 4 and cannot distinguish a plan refusal from resumable budget exhaustion

**Priority:** major. Filed 2026-11-11. Source: host-campaign and host-rehearsal controller session records 2026-11-10/11; related to NC-32 and NC-34.

**Problem.** `runner/run-r2-campaign.sh` classifies exit 4 as a time-budget boundary and relaunches the campaign. Assay also uses exit 4 for `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`, a deterministic plan/configuration refusal that no relaunch can fix. The F8 stall guard counts lines in `progress-*.jsonl`; a resumed baseline writes many events at once, so a run that completes zero candidates can still look active. A `NO_MUTANTS` result likewise represents an empty scope or planning problem, not work to resume. The supervisor can therefore burn the full attempt allowance on evidence that should stop immediately.

**Evidence.** The host-campaign controller session record 2026-11-11 records the exact pwmcp release tag repeatedly exiting 4 with `MUTANT_LIMIT_EXCEEDED`; the report notes the supervisor would relaunch 12 times although a baseline emits enough progress events to satisfy the current F8 check. It also records `NO_MUTANTS` after a roughly four-minute baseline. The host-rehearsal controller session record 2026-11-10 independently records F8 counting thousands of progress lines across baseline/restart and identifies `mutation-state-r2/*.json` or `event=candidate` records as the meaningful progress unit. NC-32 owns stale campaign/job names and stale `final-exit`; this item owns semantic reason classification and the true-progress guard.

**Proposed minimal solution.** Read the current attempt's fresh verdict/progress terminal after each exit. Treat `BUDGET_EXCEEDED/LANE_TIMEOUT` or another explicitly resumable time boundary as the only exit-4 case eligible for relaunch. Stop with a durable reason and final exit when the terminal reason is `MUTANT_LIMIT_EXCEEDED`, `NO_MUTANTS`, or another non-resumable plan/config refusal. Never read a previous attempt's stale verdict; bind the reason artifact to the attempt/run identity. Replace the F8 line-count check with completed-candidate count for the current run: `mutation-state-<lane>/*.json` or current-run `event=candidate` records, excluding baseline/plan/candidate-header events. Preserve the resume state on a real budget stop.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`: exit classification, current-attempt verdict/progress selection, `final-exit`, and stall bookkeeping. `scripts/netcup/runner/lib.sh` for shared fresh-artifact helpers. Runner tests under `scripts/netcup/tests/`, with fake progress, verdict, and state files.

**Acceptance test idea.** A current-attempt `MUTANT_LIMIT_EXCEEDED` fixture stops after one attempt without a relaunch; a current-attempt `NO_MUTANTS` fixture likewise stops with the named reason. A `LANE_TIMEOUT` fixture resumes and the next attempt must add completed state before being considered progress. A baseline-only attempt that emits many plan/progress lines but zero candidate-state files is classified stalled. A stale verdict from an older attempt cannot drive the decision. Hand-removing reason classification or reverting to line count fails the focused oracle.

**Priority.** Major. The current behavior turns deterministic configuration failures into long false retry loops.

**Close when.** Fake-campaign tests prove reason-aware stop/relaunch classification, fresh-attempt artifact binding, and completed-candidate stall progress; a live campaign stop records the exact non-resumable reason and final exit without relaunching; and the runner documentation names which reasons are resumable.

## NC-37 — a T3 campaign has no explicit comparison base or pre-baseline source-diff check

**Priority:** should-fix. Filed 2026-11-11. Source: host-campaign controller session record 2026-11-11; related to Run-Gate RG-26 and RG-263.

**Problem.** The delegated mutation lane requires a comparison base, but the campaign entry has no explicit `base` field. Operators pass an ad hoc Run-Gate argument, so the candidate population can change without appearing in the campaign identity. A base that differs only in tests is especially expensive to discover after the fact: Run-Gate runs the baseline and assays the source diff, finds no source candidates, then ends `NO_MUTANTS`. Combining different bases under one campaign name can also mix incompatible progress/resume state.

**Evidence.** The host-campaign controller session record 2026-11-11 records CIU's `base_source = "request"` behavior: no `--base` refuses `NOT_RUN`. The previous release tag `ciu-v7.24.0` produced `NO_MUTANTS` for target `ciu-v7.24.1` because the release changed only tests, after about four minutes of baseline. The campaign instead ran with `--base ciu-v7.22.0`, producing 9-10 source files and 627 candidates. The two bases were selected through run-gate arguments, not a declared campaign fact.

**Proposed minimal solution.** Add `base` to the campaign entry as a required immutable field when the selected lane's `base_source = "request"`. Resolve and record the base ref and commit at campaign creation. Before importing/materializing or starting Run-Gate's baseline, run a read-only `git diff --stat <base> <tag> -- <source paths>` for the declared lane source scope; refuse early and visibly when the source diff is empty, when the base/tag cannot be resolved, or when an existing campaign name has any other base. Treat an empty source diff as a planning refusal with a path to choose a new campaign name, not as `NO_MUTANTS` evidence. Do not silently fall back to a previous campaign's base or to `HEAD`.

**Where.** The T3 campaign entry schema/reader and `scripts/netcup/runner/run-r2-campaign.sh`; campaign metadata and resume identity; runner README; fake-repository tests under `scripts/netcup/tests/`. Run-Gate RG-263 owns the generic lane-side preflight if the supervisor cannot reliably derive source scope.

**Acceptance test idea.** A campaign entry without `base` refuses before any import or baseline when its lane delegates the base. A fake repository where the base-to-tag diff is empty over the declared source path is refused in seconds with zero Run-Gate invocation and an explicit "no source changes" reason. A non-empty source diff proceeds and records the resolved base commit. A resumed campaign whose requested base differs from its recorded base refuses rather than mixing state. Removing the diff precheck makes the empty-diff oracle fail.

**Priority.** Should-fix. The defect turns campaign planning mistakes into repeated multi-minute baselines and ambiguous no-candidate results.

**Close when.** Campaign creation requires and freezes a base for delegating lanes; source-diff emptiness, unresolved refs, and base changes are refused before baseline; the chosen base and exact diff scope appear in campaign evidence; and offline fake-repository tests cover pass, empty-diff, and mismatched-resume cases.

## NC-38 — persistent campaign checkouts and `.assay` state have no bounded retention or disk preflight

**Priority:** should-fix. Filed 2026-11-11. Source: host-campaign controller session record 2026-11-11; the retention policy named by Run-Gate LR-KI-12.

**Problem.** Persistent per-campaign checkouts and resumable `.assay` state accumulate until an operator cleans them. On a host with a 40 GB root volume this can exhaust free space and block new imports or gates before the campaign starts. The external T3 checkout design references LR-KI-12 retention, but the runner workflow has no supported bounded-retention or disk-admission procedure, and there is no rule that separates active/resumable state from completed state that may be archived or removed.

**Evidence.** The host-campaign controller session record 2026-11-11 records a 40 GB root volume at roughly 84% used with 26 old campaign checkouts, leaving about 6.2 GB free; moving about 5 GB to the CoW archive recovered space. Each campaign adds about 230 MB plus Assay state. LR-KI-12 records that persistent per-commit checkouts and their `.assay` resume state are retained until a later cleanup feature deletes them after their last entry is done. This is a new runner retention/disk-admission requirement, not a duplicate of NC-12's disk-layout reinstall.

**Proposed minimal solution.** Before importing a campaign, read free space and compare it with the declared bounded campaign requirement plus a host reserve; refuse with exact observed/required values when admission would cross that reserve. Retention is exact and state-aware: keep active campaigns, the latest completed campaign needed for evidence/resume, and explicitly archived campaigns; collect completed campaign evidence first, then remove only the exact checkout/state directories registered for that campaign. Never use age, globs, or broad directory deletion. Never discard resumable `.assay` state before its campaign is completed, superseded, or explicitly abandoned. Record a retention inventory with campaign id, state class, size, and last evidence reference.

**Where.** `scripts/netcup/runner/` campaign import/collection/retention scripts and README; the durable campaign metadata reader; runner tests with fake filesystems and declared capacities. LR-KI-12 remains the upstream retention policy reference.

**Acceptance test idea.** A fake host below the reserve refuses a new import before materialization and names current free, required, and reserve bytes. Active and latest-state fixtures are preserved; a collected completed campaign is removed only when its exact registered paths match and its evidence reference exists. A broad cleanup argument, a glob, or removal of uncollected `.assay` state is refused. Removing the reserve check or the state-class protection fails its focused test.

**Priority.** Should-fix. Root exhaustion is already observable and can stop every future campaign on the host.

**Close when.** Campaign import has a fail-closed disk admission check, completed evidence is collected before exact-path cleanup, active/latest/resumable state is protected, and fake-volume plus collection-order tests pass on a clean final commit; the runner documentation states the retention and exact-consent contract.

## NC-39 — installed `run-job` and `lib.sh` differ from repository main with no provenance inventory or reconciliation path

**Priority:** should-fix. Filed 2026-11-11. Source: host-campaign controller session record 2026-11-11.

**Problem.** A runner host can execute installed campaign scripts that differ from the reviewed repository version. Those scripts worked in the observed campaign, but their provenance and behavioural delta were unknown, so routine results depended on unaudited host-local code. There is no supported read-only inventory command that reports installed-vs-selected-repository identity and no reconciliation path that preserves active campaign state.

**Evidence.** The host-campaign controller session record 2026-11-11 compared an installed runner with repository `main`: installed `run-job` sha256 began `6dc59f73` while main began `149c818c`; installed `lib.sh` began `66890af0` while main began `8435e07f`. The installed scripts worked but the difference was not investigated or reconciled.

**Proposed minimal solution.** Add one supported, read-only provenance verb that takes the selected repository commit/source root and inventories every installed runner component with its resolved path, SHA-256, selected-repository SHA-256, and match/missing/extra state. Refuse an unrecognised selected source rather than compare against an implicit latest checkout. Provide a reconcile/update path that stages the reviewed files from the exact selected commit, verifies hashes, and refuses while a conflicting campaign or job is active; never silently overwrite live scripts or infer equivalence from matching versions. The report may say reconciliation is deferred, but it must identify every differing file and the exact installed and selected hashes.

**Where.** `scripts/netcup/runner/run-job`, `scripts/netcup/runner/lib.sh`, the runner install/update scripts and README, plus offline tests using fake installed/repository trees.

**Acceptance test idea.** A fake host with one matching and two differing scripts prints all paths and full hashes and exits non-zero while drift exists. Reconciliation from a selected commit updates exactly those files, records before/after hashes, and preserves unrelated/extraneous files for an explicit decision. An active campaign causes reconciliation to refuse without writing. A changed but unreported script is never classified as current.

**Priority.** Should-fix. Campaign evidence must come from code whose exact deployed identity is knowable.

**Close when.** The provenance command reports exact installed/repository hashes for every runner component, a supported reconcile operation can update from a selected commit only after active-work checks and hash verification, and fake-tree pass/drift/conflict oracles pass on a clean final commit.

## NC-40 — R2 supervisor counts re-reported kills as progress and misses a stalled resume

**Priority:** major. Filed 2026-11-11. Distinct from NC-36: NC-36 covers baseline progress and exit-4 classification; this entry covers reused candidate events emitted again after resume.

**Finding.** The main supervisor counts every line in its progress files (`runner/run-r2-campaign.sh:56-69`), while the pending T3 supervisor's candidate counter counts every `event=candidate` row without filtering `reused`. Assay emits a candidate progress row with `reused: true` for each previously killed candidate on resume (`cmru/tools/mutation_campaign.py:1257-1266`). Re-reported kills therefore raise the supervisor's progress count even when no new candidate has completed. When the campaign repeatedly reruns earlier survivors and timeouts (KI-152), this can keep the stall check from detecting the lack of forward progress until the attempt cap.

**Proposed minimal solution.** Count only newly completed candidate outcomes as progress. Ignore replayed `reused: true` rows, baseline events, and other progress records that do not advance the set of completed candidate indexes.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh` progress accounting and its offline supervisor tests.

**Acceptance test idea.** Feed the stall check repeated `candidate` records with `reused: true` and no new candidate result; assert the progress counter does not advance and the configured stall response occurs. Then append a newly completed candidate result and assert it advances progress and resets the stall count. Include baseline events to preserve NC-36's separate guard.

**Close when.** Replayed kills and baseline events do not reset the stall counter, a new completed candidate does, and focused tests prove the supervisor stops or resumes according to real candidate progress rather than repeated evidence rows.
