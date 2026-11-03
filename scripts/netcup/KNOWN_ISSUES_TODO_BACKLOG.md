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

