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
