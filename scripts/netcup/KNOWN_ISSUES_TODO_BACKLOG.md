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
| NC-01 | No test covers the 401 -> token refresh -> retried bodyless POST path; the retry must still carry `Content-Type: application/json` and no body | Minor | OPEN |
| NC-02 | Runner provisioning scripts were host-specific task files with six first-run bugs; now generic under `runner/` | Minor | FIXED (this branch) |
| NC-03 | The install wizard connects with `StrictHostKeyChecking=no`; the host-key pin is trust-on-first-use | Minor | OPEN |
| NC-04 | A runner account must be uid 1003 (tester-unified lane uid); provisioning created uid 1000 | Major | FIXED (this branch) |

---

## NC-01 — the 401 refresh-and-retry path of a bodyless POST is untested

**Status:** OPEN. Filed 2026-10-21 from the INT-03 review. Severity Minor. Type: test gap; no production change is expected.

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

**Status:** FIXED on `ops-v1001-20261104` (`setup-cockpit-tools.sh`). `run-lane.sh` calls `/home/vscode/.local/bin/run-gate`, but the image ships neither tool; on r1002 they were
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
