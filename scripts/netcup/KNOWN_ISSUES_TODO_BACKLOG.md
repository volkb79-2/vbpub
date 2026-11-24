<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
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
| NC-12 | Test-host disk layout: 40 GiB root, swap in the middle, cow behind it; root filled to 91-97% and neither root nor cow could grow | Major | SUPERSEDED by the single `partitioning` plan in div2-data-area-v2; live reinstall of <host>/<host> OPEN |
| NC-14 | Assay `self-qualification*` lanes need `/workspaces/<repo>` to be a bind mount of its own; the host cockpit mounts `~runner/checkouts` as `/workspaces`, so a remote-gate run ends `ERROR` ("not inside the declared <checkout> bind") | Major | FIXED in repository 2026-11-10: launcher bind plus rendered-argv regression; live host acceptance remains separate |
| NC-15 | Host cockpits carry run-gate 24.3.0 (<host> default) / 25.0.0 (<host> default) while the controller runs 25.1.1; only the `gate-tools2502` venv (25.0.2) meets the vbpub floor | Minor | OPEN (host refresh needs the host board; remote-gate floors at 25.0.1 meanwhile) |
| NC-16 | Controller Ctrl-C can leave the read-only remote evidence collector running without the controller timeout | Minor | BY DESIGN |
| NC-17 | `update-host-pin.sh` counts the `#` banner line that newer `ssh-keyscan` prints to stdout, so it refuses a good scan | Minor | FIXED d951f417d |
| NC-18 | `runner/start-cockpit-runner.sh` makes the host-endpoint record a hard requirement: a runner cockpit cannot start on a host without mdt host-setup | Minor | FIXED d951f417d (host-setup mandatory) |
| NC-19 | `runner/provision-runner.sh` prints no line saying which partition it selected for the CoW volume | Nit | FIXED d951f417d |
| NC-20 | The runner README "Order" lists the cockpit-dependent steps before the host-setup step they need | Minor | FIXED: prior host-setup dependency in d951f417d; P1–P15 ordering aligned to the host plan 2026-11-10 |
| NC-21 | The cut-over recipe for a reinstalled host without mdt host-setup exists only in agent logs, not in the repo | Should-fix | DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; implementation and fresh-host acceptance OPEN |
| NC-23 | Host Run-Gate default and pin hold | Decision | DECIDED HP-D10 2026-11-10: target 25.4.1 on both test hosts after old campaign state collection; rollout and verification pending |
| NC-24 | The host-sat queue has a per-host pause flag (`host-sat-queue.disabled.<host>`) that no runner doc mentions | Minor | FIXED at `ac0a78dcd` (queue relocation and runner documentation) |
| NC-25 | Assay's `cgroup-parent.sh` refuses a host without mdt host-setup slice units; the minimal marker-unit workaround is not recorded | Should-fix | DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; automated provisioning and fresh-host acceptance OPEN |
| NC-26 | The cockpit container has Docker restart policy `no`; after a host reboot it stayed down and the queue exhausted its INFRA allowance | Should-fix | PARTIAL at `726550ed8`: restart policy fixed; a fresh reboot showed Docker does not retry a failed create, so boot dependency ordering remains OPEN in mdt-cockpit MDT-095 |
| NC-27 | Remote-gate preflight can show a stopped cockpit as a stale run-gate, and the queue log keeps only the misleading last line | Should-fix | FIXED at `726550ed8` (ordered causal preflight messages, conditional run-gate hint, queue-tail regression tests) |
| NC-28 | Queue auto-pause leaves no durable who/why/when signal; controller status shows PAUSED without the reason | Should-fix | FIXED at `12ab909a1` (structured pause metadata and both status views) |
| NC-29 | A queue `stop` during an in-flight lane removes the loop pid file immediately but leaves that lane's `.current` marker stale, blocking or misreporting restart | Should-fix | OPEN |
| NC-30 | Hosts can pair incompatible cgprofile and Run-Gate versions without a documented compatibility matrix or a preflight that names the mismatch | Major | OPEN |
| NC-31 | Queue restart does not prove the old loop PID exited and the disabled queue logged that it did not launch a replacement | Should-fix | OPEN |
| NC-32 | T3 re-supervision collides with `run-job`'s existing-name refusal, and an INFRA retry reads the failed campaign's stale `final-exit` | Should-fix | FIXED in `t3q-supervisor-20261110` (2026-11-10) |
| NC-33 | `sync-from-devcontainer` leaves the host build/setup symlink pointed at the imported release-tag checkout | Should-fix | FIXED in `t3q-supervisor-20261110` (2026-11-10) |
| NC-34 | A passing CMRU command-evidence campaign exits 70 because the supervisor requires an Assay verdict | Should-fix | OPEN |
| NC-35 | Mixed endpoint-publisher rollback has no confirmed aggregate-cleanup argv | Minor | OPEN; live publisher and fragment inventory required before writing a safe command |
| NC-36 | The R2 supervisor relaunches every exit 4 and its stall guard counts baseline progress lines instead of completed candidates | Major | OPEN |
| NC-37 | A T3 campaign has no explicit, immutable comparison base or pre-baseline source-diff check; an empty source scope costs a baseline and ends NO_MUTANTS | Should-fix | OPEN |
| NC-38 | Persistent campaign checkouts and `.assay` state have no bounded retention or disk preflight on the small root volume | Should-fix | OPEN |
| NC-39 | Installed runner components, including the R2 supervisor, can differ from a selected repository commit with no provenance inventory or supported reconciliation path | Should-fix | OPEN |
| NC-40 | R2 supervisor counts re-reported kills as progress and misses a stalled resume | Major | OPEN |
| NC-41 | The R2 supervisor does not record the selected worker count and has no sustained under-utilisation advisory for a campaign consuming little of its CPU allocation | Should-fix | OPEN 2026-11-11 |
| NC-42 | The R2 supervisor runs one lane container per host, so a host with spare RAM cannot run several shard instances, split a campaign with another host or restart across a judge change | Enhancement | FIXED in `netcup-r2-instances-20261111` |
| NC-43 | The runner endpoint loader forbids `MDT_BUILDKIT_GID` from equalling the Docker GID, while host-setup always makes the BuildKit socket Docker-group accessible | Should-fix | FIXED 2026-11-24 (P-plat-19g) |
| NC-44 | The reinstall runbook does not explain the post-wipe root wrapper or link the guarded `pre-reinstall.py --yes` flow into the ordered runner bring-up | Minor | OPEN 2026-11-12 |
| NC-45 | The R2 supervisor hard-codes commit checkouts under /workspaces/<commit>, while the assay full-qualification driver accepts only the /workspaces/vbpub worktree family | Should-fix | OPEN 2026-11-12 |
| NC-46 | Bundle filenames become lane-container repository names, so exact-name selection depends on the uploaded bundle name | Minor | OPEN 2026-11-12 |
| NC-47 | Remote-gate's artifact filter discards the tester-unified receipt required by assay's full qualification | Should-fix | OPEN 2026-11-12 |
| NC-48 | R2 fleet memory admission multiplies the full lane ceiling per shard and hides CPU/KSM capacity signals | Should-fix | IMPLEMENTED unreleased |
| NC-49 | Runner guidance does not explain when `--instances` helps or its single-host cost | Enhancement | OPEN 2026-11-12 |
| NC-50 | `run-job` is installed under `~/bin` but bare-name examples fail in non-login runner shells | Should-fix | OPEN 2026-11-12 |
| NC-51 | Fleet merge launch lacks an incomplete-shard gate and status does not distinguish merging | Should-fix | OPEN 2026-11-12 |
| NC-52 | Campaign supervision logic (attempts, fleet, memory guard, merge) lives in the netcup runner scripts although it is provider-neutral; it should move out of the netcup script | Should-fix | OPEN 2026-11-12 (design) |
| NC-53 | the Run-Gate 25.6.0 image rollout did not synchronize runner scripts or verify the selected private Run-Gate profile | Should-fix | OPEN 2026-11-12 |
| NC-54 | Pre-reinstall helpers use a fixed release-program root; remote-gate's output root is configurable | Minor | OPEN 2026-11-13 |
| NC-55 | A deterministic budget-exceeded candidate is reported as stalled | Should-fix | OPEN 2026-11-13 |
| NC-56 | T3 bundle import can omit release tags needed to derive the required Assay version | Should-fix | OPEN 2026-11-14 |
| NC-57 | fleetd had no host-install script, and the docs prescribed a system-Python pip install that Debian 13 refuses | Should-fix | FIXED 2026-11-15 (install-fleetd.sh; live host acceptance OPEN) |
| NC-58 | `remote-gate.py` never emits the `run-gate.commit=` binding it depends on for evidence, so a non-container lane's binding exists only in an out-of-repo wrapper | Should-fix | OPEN 2026-11-16 |
| NC-59 | `provision-runner.sh` installs no slice limits, so the gates tier's CPU and memory policy (and the 2026-11-16 overcommit/paging decision) reaches a host only by hand-editing a drop-in | Should-fix | FIXED in repository 2026-11-16 (`provision-gates-limits.sh` + live-memory admission); live fresh-host acceptance NC-62 |
| NC-60 | Provisioning installs no pinned image set, so a host cannot run assay's SQL qualification until someone pulls the digest-pinned PostgreSQL reference by hand | Should-fix | OPEN 2026-11-16 |
| NC-61 | The runner guide's fresh-host replay is still unverified and its checked-in facts drift after a reinstall with no test or date binding them | Minor | OPEN 2026-11-16 |
| NC-62 | The rendered gates memory policy has no live fresh-host acceptance: it is proven only against fakes, while the measured policy it reproduces came from one hand-configured host (also carries the `--adopt` one-time step and the 6655-vs-6656 nominal-size finding of review round 1) | Should-fix | LIVE HALF CLOSED for v1001 2026-11-16 (adoption accepted; its run also LOWERED sshd's live `-1000` listener score to `-900`, now fixed as NC-64 — evidence below); r1002 OPEN (blocked on its iogovmon acceptance until 2026-11-17T14:38Z) |
| NC-63 | The `docker-.scope.d` `OOMPolicy=kill` drop-in covers every Docker container on the host, so an OOM in the cockpit or a buildkitd scope kills that container whole | Minor | OPEN 2026-11-16 (by design for lane scopes; restriction open) |
| NC-64 | `provision-gates-limits.sh` LOWERED sshd's own OOM protection: it wrote `-900` over the `-1000` live score sshd keeps on its listener, and would have replaced a packaged stronger value with a drop-in | Major | FIXED 2026-11-16 (never-reduce rule for the live score and the drop-in; sshd finding from the v1001 adoption; round-1 review fixed a swallowed `command not found` that refused MORE protective foreign files) |
| NC-65 | `provision-gates-limits.sh` writes the `dev-gates.slice` memory policy and `OOMPolicy=kill` unconditionally and lists ssh in the -900 set; it should skip the slice and scope halves when host-setup's `dev-gates.slice` fragment exists and drop ssh (sessions inherit it) | Should-fix | OPEN 2026-11-24 (from DESIGN-DEDICATED-DEV-MEMORY.md section 10; before host-setup lands on any runner host); addendum OPEN 2026-11-24: no re-render of `20-memory-policy.conf` at all on `HOST_ROLE=dedicated-dev` |
| NC-66 | remote-gate shipped the controller's local `main`; nothing created `origin/main` on the host | Should-fix | FIXED 2026-11-24 (P-plat-24; follow-ups NC-70) |
| NC-67 | The runner records no container `State.OOMKilled` in result.json | Should-fix | OPEN 2026-11-24 |
| NC-68 | HAZARD: `verdict_of()` in remote-gate.py is unanchored and takes the last match | Must-fix | OPEN 2026-11-24 |
| NC-69 | Concurrent remote-gate runs from one controller checkout share the temporary ref `refs/remote-gate/base`, so one run can ship another run's base and fail rc 72 | Should-fix | OPEN 2026-11-24 |
| NC-70 | `--base main~1` / `main^` (and a declared `judge.base = "main~1"`) still resolve against the stale local `main`; carries the plat-24 nits N2-N4 | Minor | OPEN 2026-11-24 |
| NC-71 | `remote-gate.py` status/logs/collect/cancel read the host table only from the default hosts file, so a gate launched through `scripts/controller/remote-gate.sh` cannot be cancelled without `--hosts-file` | Should-fix | OPEN 2026-11-24 |

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

**Status:** FIXED on `fix-<host>-findings-20261104`. Found on a reinstalled runner host; the scripts lived in a task directory with the host name, server id and
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

**Status:** FIXED on `fix-<host>-findings-20261104` (documented + provisioned). `tester-unified` runs lanes as uid 1003. A runner account at uid 1000 made every Run-Gate
`worktree` lane fail one of three ways (see Run-Gate RG-150): Git `dubious ownership` in the lane, `Permission denied` on `<project>/.run-gate`, or `MDT_COW_ROOT must be
owned by the non-root caller uid`. `provision-runner.sh` now creates the account with `RUNNER_UID` (default 1003) and refuses a different existing uid unless
`RUNNER_UID_ALLOW_CHANGE=1`; `start-cockpit-runner.sh` remaps the cockpit's `vscode` user to it. To migrate a live host: stop the cockpit and BuildKit containers, `groupmod -g`/`usermod -u`,
`chown -R -h` the runner home and the CoW root, restart, rerun `start-cockpit-runner.sh`.

<!-- Id note (2026-11-04): NC-02..NC-04 (runner provisioning, host-key pin, uid 1003) exist only on branch fix-<host>-findings-20261104. The
     ids below start at NC-05 to leave that gap; the controller resolves ordering and the status-table rows on merge. The runner README
     lives at scripts/netcup/runner/README.md on that branch. -->

## NC-05 — <host> migration of the cgprofile host daemon to the native service (host-setup, pinned wheel)

**Status:** OPEN. Filed 2026-11-04. Blocked on cgprofile CP-41/CP-42 and mdt-cockpit MDT-055.

**Problem.** On <host> the daemon is a ciu-managed container deployed from a repo checkout that carries two uncommitted hand edits
(`RUNBOOK.md:52-90`): the gates slice name and the slice unit, because the compose template authors both literally. The container also
restarts with dockerd.

**Decision record (operator, 2026-11-04).** The cgprofile daemon becomes a native systemd service from a released wheel, installed and pinned
by `mdt host-setup`; the ciu deployment is retired (CP-44). <host> migrates first, the shared host later in an idle window.

**Design.** Per-host steps in the RUNBOOK: record the current revision label; `ciu down` of the host profile; set `CGPROFILE_WHEEL`
(version plus sha256) in `/etc/mdt/host-setup.env`; run host-setup (installs the wheel into `/opt/cgprofile`, renders the unit and the
`cgprofile.slice`, restarts only on drift); verify `ctl.sock`, the observer socket, their groups (CP-43) and `version`; run one run-gate lane
with the observer and one `ctl` session; delete the hand edits from the checkout. Rollback: stop the unit, `ciu up --profile host`
from the pinned tag.

**Acceptance.** `systemctl is-active` and the pinned version match; the run-gate and assay probes are unchanged; no uncommitted edits
remain in the checkout; the RUNBOOK documents the sequence and rollback.

## NC-06 — interim: render the cgprofile gates slice for the ciu deployment from host config, no hand edits

**Status:** OPEN. Filed 2026-11-04. Interim (option C of the platform memo) until NC-05 lands; skip it if NC-05 is imminent.

**Problem.** The ciu deployment reads `[cgprofile.daemon] gates_slice` and the slice unit is installed by hand; <host> needs different
tier names and has local edits to the template.

**Design.** Render both the `gates_slice` value and the `cgprofile.slice` content from `/etc/mdt/host-setup.env` (the tier-name source of
truth) into the ciu override for the host, so the checkout stays clean. No new feature in the daemon.

**Acceptance.** `git status` of the <host> checkout is clean after an upgrade; the rendered values equal host-setup's tier names; a test of the
rendering with a fake env file.

## NC-07 — restricted `cockpit` ssh key with a forced command, plus a `docker context` recipe in the runner README

**Status:** OPEN. Filed 2026-11-04. Needs `fix-<host>-findings-20261104` merged first (the runner README exists only there). Docs companion:
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
- Runner README recipe: `docker context create <host> --docker host=ssh://runner@<host>`, then VS Code "Attach to Running Container" with the
  context; the shell gets the remapped uid of the runner user. Security note: `runner` is in the docker group, so it is host-root equivalent;
  its keys belong only to operators and the controller. Both mechanisms add no new exposure.
- The forced-command key is separate from the main key and is listed by fingerprint in the README so it can be revoked on its own.

**Acceptance.** A test of `provision-runner.sh` (fake `ssh`/`authorized_keys` paths) writes exactly the restricted line and never a bare key;
a live check: the forced key opens the shell and nothing else (a different command is ignored); the README recipe works from a laptop. After
the lane-uid rollout (run-gate RG-151..153) the "same uid everywhere" wording relaxes via mdt-cockpit MDT-059.

## NC-08 — a provider image install removes the server's snapshots; the listing is empty afterwards

**Status:** OPEN (filed 2026-11-04 from the <host> reinstall). Before `install-host.py wizard` (ServerImageSetupTask) `scp-api.py snapshots 804027` listed eight snapshots
(the server record said `snapshotCount` 1); after the task finished the listing said "no snapshots for this server", and `snapshots ... revert <name> --dry-run` answered
"existing: none"; `snapshotCount` stayed 1. No snapshot verb was called. An operator who keeps a TrueNAS/rollback snapshot on a host and then reinstalls it with the
wizard loses the rollback. **Fix.** `install-host.py` lists the target's snapshots in its preflight and refuses (or needs `--accept-snapshot-loss`) when any exist; the
README and DESIGN-GUIDE state that a provider reinstall does not preserve snapshots. **Acceptance.** Test with a fake client that returns one snapshot: the wizard refuses without the flag, passes with it.

## NC-09 — the cockpit container has no `run-gate` and no `assay` after `start-cockpit-runner.sh`

**Status:** FIXED on `ops-<host>-20261104` (`setup-cockpit-tools.sh`). `run-lane.sh` calls a controller-local `run-gate` path, but the image ships neither tool; on <host> they were
installed by hand. `setup-cockpit-tools.sh` creates `~/.venvs/gate-tools` in the cockpit with the pinned Run-Gate wheel (sha256 checked) and the synced checkout's Assay (editable).
Also added: `run-jobs-window.sh` and `run-jobs-compare.sh` (native-R2 `--jobs` comparisons on fresh state; a run-gate lane refuses `--jobs N` above the lane's `judge.mutation.jobs_max`, worktree 1, iobench 4).

## NC-10 — running the mdt host-setup wizard or `install.sh` as root from the runner checkout leaves root-owned `__pycache__` that breaks the next lane

**Status:** OPEN (docs). On <host>, `mdt-host-setup-wizard.py` and `install.sh` run as root from `~/src/vbpub-current/mdt-cockpit/host-setup` created root-owned `__pycache__` dirs; the next
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

**Status:** CODE DONE 2026-11-05 on `div2-disk-layout-20261105`; reinstall of <host> then <host> OPEN ([div2-reinstall-RUNBOOK.md](../debian-install-v2/docs/evidence/div2-reinstall-RUNBOOK.md)). Severity Major.

**Observed.** <host> and <host> (512 GiB virtual disks) were installed as ESP 243 MiB, `/boot` 977 MiB, 40 GiB ext4 root, four 4 GiB swap partitions, then a 454.8 GiB btrfs `/srv/cow`. Root reached 91-97% (docker 27 GB, runner checkouts, build cache) and could not grow because the swap partitions sit directly behind it. Cause: the predecessor installer required swap directly after root, and `provision-runner.sh` created CoW storage in the last free row. Both layout decisions are superseded by the single `partitioning` plan.

**Current behavior.** The `partitioning` plan defines root, data, the raw iobench reservation, reserve, and tail swap in physical order. The CoW partition is planned at install time and grows with its assigned size; the runner does not allocate it. Reinstall remains OPEN and must use the current plan contract documented in `scripts/debian-install-v2/README.md`.

## NC-14 — assay self-qualification lanes cannot run in the host cockpit

**Status:** FIXED in repository 2026-11-10. Severity Major. Live host acceptance was not run.

**Observed.** `remote-gate run <host> <worktree> self-qualification-provisional --project-dir assay` (run `assay-selfqual-cov-a`) ended `ERROR; exit_code 2` after 3 s with only `self-qualification-container: the selected worktree is not inside the declared <checkout> bind`. `assay/tools/self-qualification-container.sh` requires `findmnt --target <worktree>` to report TARGET `<checkout>`; in the host cockpit `/workspaces` is the mount (`~runner/checkouts`), so TARGET is `/workspaces`.

**Fix and evidence.** `start-cockpit-runner.sh` now adds the documented nested bind `--mount type=bind,source="$RUNNER_HOME/checkouts/vbpub",target=<checkout>,consistency=cached`. `tests/test_runner_scripts.py::test_cockpit_launcher_binds_vbpub_checkout_at_assay_workspace_path` renders the Docker argv with a fake Docker executable and asserts the exact source, target and consistency option. The remote-gate diagnostic remains useful if the bind is absent on a running host.

**Residual acceptance.** No host was accessed for this change. A live `self-qualification*` replay is not claimed here and remains part of the fresh-host acceptance.

## NC-15 — host cockpit run-gate is older than the controller's

**Status:** OPEN. Severity Minor.

**Observed.** `docker exec mdt-cockpit-runner <controller-local-run-gate-path> --version`: <host> 24.3.0, <host> 25.0.0; `~/.venvs/gate-tools2502/bin/run-gate` is 25.0.2 on both; the controller runs 25.1.1.

**Needed.** After the R2 campaigns on a host finish, `runner/setup-cockpit-run-gate.sh 25.1.1 <sha256>` (own venv, the default is left alone) and add that venv to the `run_gate_bins` of the vbpub and dstdns profiles. No host was changed by this entry.

## NC-16 — controller Ctrl-C can leave remote evidence collection running

Ctrl-C on the controller during collection can leave the remote `ssh`/collector
running after the controller exits; it is read-only, but the 300 s limit is
controller-side and no longer applies after interruption.

## NC-17 — `update-host-pin.sh` counts the `#` banner line of `ssh-keyscan`

**Status:** FIXED 2026-11-09 in `d951f417d`. `tests/test_update_host_pin.py` covers banner-plus-one-key and still-refused two-key scans.

**Priority:** minor. Source: [host-reinstall-EXEC-LOG.md](../debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md) (17:21Z) and [div2-reinstall-host-RESULT.md](../debian-install-v2/docs/evidence/div2-reinstall-host-RESULT.md).

**Finding.** `update-host-pin.sh:56-62` runs `ssh-keyscan -T 10 -t ed25519` and requires `grep -c '[^[:space:]]'` of the output to be 1. The controller's OpenSSH `ssh-keyscan` now prints a `# host:22 SSH-2.0-...` banner comment to stdout, so a correct single-key scan counts 2 lines and the script refuses ("expected exactly one scanned ed25519 host key"). The 2026-11-08 <host> reinstall ran the unmodified script with a PATH shim that drops `#` lines; the script's own rescan and fingerprint-equality check still applied.

**Fix.** Count only key lines: `grep -vc '^#'` (or `grep -c '^[^#[:space:]]'`), and use the same filtered output for the stored pin. Add a test in `tests/test_update_host_pin.py` with a fake `ssh-keyscan` that prints a banner line plus one key, and one that prints two keys.

**Close when.** The banner case passes, the two-key case still refuses, and a mutation of the filter fails a test.

## NC-18 — the cockpit start script cannot run without the host-endpoint record

**Status:** FIXED 2026-11-09 in `d951f417d` by choosing the reviewed mandatory-host-setup rule. The runner README, consumers guide, and design guide state that no raw-socket degraded mode is supported.

**Priority:** minor, decision. Source: [host-reinstall-EXEC-LOG.md](../debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md) (17:30Z), [div2-reinstall-host-RESULT.md](../debian-install-v2/docs/evidence/div2-reinstall-host-RESULT.md).

**Finding.** `runner/start-cockpit-runner.sh:7` calls `load_host_endpoints /etc/host-endpoints.env || exit $?` and needs the Docker-API, cgprofile, topos and BuildKit socket directories that mdt host-setup creates. On the freshly reinstalled <host> (host-setup deliberately not yet run) the pinned script refused, and the operator used a local, unreviewed variant that bound the raw docker socket with no cgprofile or BuildKit mounts. That variant is a deviation from the reviewed scripts.

**Decide.** Either state in the runner README that host-setup is mandatory before the cockpit (the current behavior, NC-20), or provide a reviewed degraded mode (an explicit flag, never a silent default) that names what is missing (no observer socket means run-gate lanes with `resource_observer = "cgprofile"` refuse; no BuildKit mount means no governed builds).

**Close when.** The decision is documented and, if a degraded mode is chosen, a test covers both the flag and the refusal text.

## NC-19 — `provision-runner.sh` does not say which CoW partition it selected

**Status:** FIXED 2026-11-09 in `d951f417d`. `tests/test_cow_select.py` covers the selected-device message and both reused and successfully formatted paths in `provision-runner.sh`.

**Priority:** nit. Source: [host-reinstall-EXEC-LOG.md](../debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md) (17:26-17:32Z).

**Finding.** `runner/provision-runner.sh:70-100` resolves the CoW volume by `PARTLABEL=MDT_COW` (`cow_select_device`) and reuses an already-mounted btrfs, but prints no line naming the selected device, the match rule, or "reused, no mkfs". On <host> the operator had to infer from the log that `/dev/vda4` was selected by PARTLABEL and that no mkfs or partition command ran.

**Fix.** Print one line after the selection: `selected MDT_COW by PARTLABEL: <device> (<fs type>, reused|formatted)`. Cover it in the provisioning test that already exercises `cow_select_device`.

**Close when.** The line appears in both the reuse and the format paths in a test.

## NC-20 — the runner README "Order" runs cockpit-dependent steps before host-setup

**Status:** FIXED. `d951f417d` moved host-setup ahead of the cockpit; the 2026-11-10 host-plan update orders all seven README steps across P1–P15 and places source sync before host-setup.

**Priority:** minor, docs. Source: [div2-reinstall-host-RESULT.md](../debian-install-v2/docs/evidence/div2-reinstall-host-RESULT.md) (runner provisioning section), [host-reinstall-EXEC-LOG.md](../debian-install-v2/docs/evidence/host-reinstall-EXEC-LOG.md).

**Finding.** The earlier order correction put host-setup before cockpit-dependent steps but still listed host-setup before `sync-from-devcontainer`, even though host-setup runs from the synced runner checkout. The host-plan dependency graph also distinguishes the required order: source sync, root-PATH Augeaswrap, host-setup, service checks and images, then cockpit and acceptance.

**Fix.** The seven numbered runner README steps now follow P1–P15: provider reinstall, host-key/layout check, runner provisioning, source sync, Augeaswrap then host-setup, services/checks/venvs/images, and cockpit/registration/acceptance. They state what works after provisioning alone and which host facts each later stage needs.

**Close when.** README "Order" names each dependency and matches the behavior observed on <host>.

## NC-21 — the cut-over recipe for a reinstalled host without mdt host-setup exists only in agent logs, not in the repo

**Priority:** should-fix (the working path is undocumented). Filed 2026-11-09, status OPEN. Source: [host-cutover-EXEC-LOG.md](docs/evidence/host-cutover-EXEC-LOG.md), [host-cockpit-EXEC-LOG.md](docs/evidence/host-cockpit-EXEC-LOG.md), and the controller continuity record (22:25Z and 23:35Z entries, retained outside this repository). Supersedes the decision half of NC-18 (it chooses the degraded path as a supported one) and extends NC-20.

**Status:** DOCUMENTED at `9541bbffe52cc71d30e725d63a6281e77da08d4f`; implementation and fresh-host acceptance OPEN. The documented prerequisites, commands, verification and rollback are in `runner/README.md` under "Host without mdt host-setup"; the Netcup README links to that section.

**Finding.** On 2026-11-08 <host> was moved to cgprofile 3.0.0 and a cgprofile-aware cockpit without mdt host-setup. The steps that worked are in no file under `scripts/netcup/`:
1. cgprofile 3.0.0 install on the host: `cgprofiled install` from the pinned wheels (cgprofile 3.0.0, cli-extended 0.5.3, augeaswrap), `--dry-run --json` first, then apply; it creates the control and observer sockets, the `cgprofile-control` and `cgprofile-observer` groups and publishes the six `CGPROFILE_*` keys to `/etc/host-endpoints.env` (`host-cutover-EXEC-LOG.md`, 21:33Z-21:34Z).
2. The cockpit start variant `start-cockpit-runner-cgprofile.sh` (kept at `<host>/runner-scripts-local/` and `<host>/cockpit-20261108/`, outside the repo): reads `CGPROFILE_*` from `/etc/host-endpoints.env` with a strict regex (nothing typed by hand), bind-mounts the control and observer socket directories read-only at identity paths, adds `--group-add <control gid>` (986), and sets a container-only `MDT_HOST_DOCKER_GID` (RG-252). Rollback script: `start-cockpit-runner-nohostsetup.sh`.
3. run-gate 25.4.0 installed by the existing `scripts/netcup/runner/setup-cockpit-run-gate.sh 25.4.0 <sha>` as the second venv `gate-tools2540` next to the default 25.0.2.
4. Minimal slice units where host-setup is absent: `dev-interactive.slice` as a limit-free marker unit, and `dev-gates.slice` with `CPUWeight=100`, `IOWeight=101` and `MemoryMax=6G`; size these from `MemTotal` as in NC-25.
`runner/start-cockpit-runner.sh` and `runner/README.md` know none of this (NC-18, NC-20).

**Fix.** Move the supported path into `scripts/netcup/runner/`: fold the cgprofile variant into `start-cockpit-runner.sh` (a mode chosen by the presence of the record, with a clear refusal naming the missing keys), add a `provision-cgprofile.sh` (or a README section) for steps 1 and 4 with the dry-run-first rule and the pinned wheel hashes taken from `runner.env`, and extend the README "Order" with a "host without mdt host-setup" path and its rollback. The named second Run-Gate venv already has its supported installer in `setup-cockpit-run-gate.sh`. Test the rendered argv of the start script offline (no Docker) for both modes.

**Close when.** A freshly reinstalled host reaches a working cgprofile-observed cockpit using only files in the repo and `runner.env`, the README documents the order and the rollback, and the rendered-argv tests pass.

## NC-22 — the cockpit image carries only the old `cgpctl` 2.1.0 client (no `cgprofilectl`)

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Found by the <host> cockpit agent: with cgprofile 3.0.0 on the host the cockpit image still holds only the 2.1.0 `cgpctl` client and has no `cgprofilectl`. Source: `HANDOFF-STATE-20261107.md` (22:25Z "Backlog to file"), [host-cockpit-EXEC-LOG.md](docs/evidence/host-cockpit-EXEC-LOG.md) (21:46Z, the 25.0.2 cockpit run-gate ignores `CGPROFILE_*`).

**Finding.** The estate image pipeline lists the wheel in `mdt-cockpit/pip/wheels.list` (entry `cgprofile`, clients `cgprofile` and `cgprofilectl` from 2.0.0 on); the image on <host> pre-dates the 3.0.0 wheel. Which pipeline produced the <host> cockpit image (mdt-cockpit image build versus the runner scripts `build-tester-*.sh` / `setup-cockpit-tools.sh`) is to be confirmed first; the rebuild with the 3.0.0 CLI is tracked as MDT-090 in `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md`, the runner side only has to use and check it.

**Fix.** Rebuild or retag the cockpit image with the 3.0.0 wheel (MDT-090), and have `setup-cockpit-tools.sh` verify after the build that `cgprofilectl --version` reports 3.0.x inside the container, failing with the image tag named.

**Close when.** `docker exec` into the <host> cockpit shows `cgprofilectl` 3.0.x and no stale `cgpctl`, and `setup-cockpit-tools.sh` checks it.

## NC-23 — Run-Gate host profile default and campaign-state pin hold

**Priority:** decision. Historical state recorded 2026-11-09: `gate-tools2540` (Run-Gate 25.4.0) was installed next to the default 25.0.2 on <host> and used for an observer-lane smoke and the Assay 12.1.0 release attempt; the queue still resolved 25.0.2. These are historical observations, not current host evidence.

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

**Finding.** The release attempt on <host> failed in seconds with `cgroup-parent: Docker-host unit "dev-interactive.slice" is not a loaded installed slice (FragmentPath=)`. `assay/tools/cgroup-parent.sh` requires, for each named slice: a loaded unit with a real `FragmentPath` (`:324-332`; a runtime-generated slice is refused); positive effective `CPUWeight` and `IOWeight` properties on the gates slice (`:354-355`); at least one non-default control; and, with `--memory-reserve-bytes`, a finite `memory.max` with the requested headroom (2 GiB for the B105 self-qualification lanes; `:394-402`). It does not test whether the weight properties were explicitly assigned in the unit file. mdt host-setup installs these units; <host> was reinstalled without it. Fixed by hand on 2026-11-08 with `/etc/systemd/system/dev-interactive.slice` as a limit-free marker unit and `/etc/systemd/system/dev-gates.slice` configured with `CPUWeight=100`, `IOWeight=101` and `MemoryMax=6G`. Revert: `rm` both files and `systemctl daemon-reload`; the before-file of `MemoryMax` is on <host> under `<home>/`.

**Fix.** Ship the two unit files as templates under `scripts/netcup/runner/` (sizes derived from `MemTotal`, never hard-coded: `MemoryMax = MemTotal - reserve`, reserve at least 2 GiB), install them from `provision-runner.sh` when host-setup is absent, and make the script refuse to overwrite a host-setup-owned unit. Record in the runner README why a marker unit satisfies the check and what it does not do (no governance). Consider whether assay should accept a documented "no-governance host" mode instead of units that mimic governance, as an assay backlog item.

**Close when.** A freshly reinstalled host without host-setup passes `cgroup-parent.sh --memory-reserve-bytes 2147483648` after `provision-runner.sh` alone, and the units are removed cleanly by a documented revert.

## NC-26 — cockpit restart policy `no` leaves remote gates down after a host reboot

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Source: [host-queue-REPORT.md](docs/evidence/host-queue-REPORT.md) (Root cause, Before, Change, Follow-ups);
the controller queue log (02:00-04:58Z entries, retained outside this repository);
`scripts/netcup/runner/start-cockpit-runner.sh` (`docker run` omits `--restart`), plus the pinned launch variants under the release-program artifacts
(`<host>/start-cockpit-runner-20261104.sh`, `<host>/runner-scripts*/start-cockpit-runner*.sh`) which also omit it.

**Status:** PARTIAL at `726550ed8`; reboot acceptance FAILED on 2026-11-12. `start-cockpit-runner.sh` now passes `--restart unless-stopped`, an argv-level fake-Docker
test covers every tracked `start-cockpit-runner*.sh`, and `runner/README.md` documents the reboot behavior plus the applied
`docker update --restart unless-stopped` workaround and its `--restart no` revert. The operator applied the live update
to both test hosts on 2026-11-09. The r1002 reinstall execution record 2026-11-12 then supplied the previously missing
reboot acceptance and disproved the sufficiency of the policy: Docker attempted the container once before
`mdt-buildkitd.service` recreated its bind source, runc failed the create, and Docker did not retry it (`RestartCount=0`).
A manual `docker start mdt-cockpit-runner` recovered it. Dependency ordering is owned by mdt-cockpit MDT-095.

**Finding.** The <host> host rebooted at 02:02Z on 2026-11-09 while a queue lane was running. Docker's default restart policy is `no`, and the cockpit launcher never
sets one, so `mdt-cockpit-runner` remained `Exited (143)`. The interrupted lane became `INFRA:NO_FINISH`; from 03:22Z every new launch failed preflight with rc 4,
and the fifth consecutive INFRA result at 03:52Z touched `host-sat-queue.disabled.<host>`. The operator restarted the cockpit and removed the flag at 04:57Z, after which
the queue launched a lane normally. The first failed queue preflight was recorded at 03:22Z, about 1 h 20 min after the 02:02Z reboot; the queue remained paused until the
operator recovery at 04:57Z.

**Fix.** Add `--restart unless-stopped` to the `docker run` invocation in every checked-in or pinned `start-cockpit-runner*.sh` variant retained for a host, replacing the
implicit `--restart no`. Add an offline test/check that each such launcher carries the policy. Document in `runner/README.md` that the cockpit returns after a host reboot
unless it was explicitly stopped, and how to inspect or revert the policy. The already-applied `docker update` is only the live workaround; the launchers remain the
durable fix.

**Close when.** A reboot of a test host brings `mdt-cockpit-runner` back by itself after every published socket mount
source exists, a queue lane starts without a manual `docker start`, the launcher policy check passes, and the runner
README documents the reboot behavior and its dependency on MDT-095's boot-order fix.

## NC-27 — a stopped cockpit is misreported as a stale run-gate, and the queue logs only the misleading hint

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Source: [host-queue-REPORT.md](docs/evidence/host-queue-REPORT.md) (Root cause, Follow-ups);
the checked-in `scripts/netcup/runner/remote-gate.py` (`check_prereqs`/`preflight`, especially
lines 638-711); the controller queue log (03:22-03:52Z launch failures, retained outside this repository).

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

**Priority:** should-fix. Filed 2026-11-09, status OPEN. Source: [host-queue-REPORT.md](docs/evidence/host-queue-REPORT.md) (Root cause, After);
the controller queue log (03:52-04:02Z entries, retained outside this repository);
the checked-in `scripts/netcup/runner/host-sat-queue.sh` (`record`, `status`, and auto-pause at lines 97-99, 177 and 191-203);
the checked-in `scripts/controller/ctl-status.sh` (host flags and recent results, lines 63-70).

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

**Close when.** The restart helper/procedure is documented and tested with fake queues, and a live <host>/<host> restart records the old PID exit, exact disabled-no-launch line, `.current` drain, and new loop startup without manual inference.

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

**Problem.** Test hosts can carry different cgprofile and Run-Gate versions. <host> was observed with cgprofile 2.0.0 and Run-Gate 25.0.2, a legacy pair that works with the old path/CLI. Run-Gate >=25.4 uses cgprofile 3.0.0's host socket layout and CLI. An incompatible pair can pass startup/preflight until an observer lane fails with an unrelated-looking endpoint, version, or missing-client error, and operators have no matrix saying which pairing is supported.

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
S16. Status FIXED in `t3q-supervisor-20261110` (2026-11-10); offline fake
coverage is in `tests/test_run_r2_campaign.py`.

**Finding.** The T3 design names a campaign job
`t3-<tool>-<tag>-<lane>`, but `runner/run-job` refuses an existing job directory
(`job already exists`, exit 3). A re-supervision that reuses that name therefore
never starts the new supervisor and the entry can cycle in `resume`. The same
collision affects an INFRA retry: `run-r2-campaign.sh` leaves
`campaigns/<name>/final-exit`, so collect observes the failed run's stale exit
immediately. A new supervisor also restarts its local attempt counter at 1 and
overwrites the previous `attempt-NN` logs.

**Fix.** Each supervisor start requires a fresh campaign name suffix,
`t3-<tool>-<tag>-<lane>-s<N>`, where `N` is the entry's supervisor-start count.
Use that same suffix for `run-job` and the campaign evidence directory. Keep
the unsuffixed campaign base stable for its lock and persistent checkout resume
state. Each start gets a new `campaigns/<base>-s<N>/` directory, so attempt
logs and terminal evidence remain intact without moving an older directory.
Do not delete the checkout; its `.assay` state is what makes the retry resume.

**Close when.** The supervisor requires a per-start suffix, two sequential fake
starts retain separate attempt logs and `final-exit` files, and the stop test
shows the stable campaign lock is released. Host rehearsal
`v1001-supervisor-rehearsal-REPORT.md` separately confirms resume across a
budget exit and supervisor death.

## NC-33 — T3 import repoints the host's shared checkout symlink

**Priority:** should-fix. Filed 2026-11-10 from `test-methodology-REVIEW4.md` §5
S17. Status FIXED in `t3q-supervisor-20261110` (2026-11-10); offline fake
coverage is in `tests/test_sync_from_devcontainer.py`.

**Finding.** `runner/sync-from-devcontainer` updates
`~/src/vbpub-current` to the imported `~/checkouts/<commit>` tree. Host build
and setup scripts obtain their source checkout through `source_repo()` in
`runner/lib.sh`, which reads that link. These scripts include tester image
builds, gate/cockpit tool setup and Assay venv setup. After a T3 import the
link therefore names a release-tag tree; a later rebuild or setup can silently
use an old commit.

**Fix.** T3 imports pass `--no-current-link` to import the commit checkout
without changing the shared `~/src/vbpub-current` source link. The default
import behavior remains available for non-T3 callers. Do not make the T3
checkout the new durable host source.

**Close when.** Fake `sync-from-devcontainer` tests prove an existing link is
unchanged, an absent link stays absent, the imported checkout remains usable
directly, and the default non-T3 import still updates the link.

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

**Observed again 2026-11-14 (Run-Gate native R2, not triggered).** The Run-Gate 25.6.0 native R2
(89 candidates, one attempt) ended by itself with lane verdict FAIL, so the supervisor never relaunched.
Had the lane returned exit 4 with `completed_candidates` unchanged (N->N, for example a candidate lost to the
lane budget with progress `budget_exceeded`), the same exit-4 relaunch rule would have started another
attempt over unresolved work. The reason-aware classification proposed below already covers this case; the
fixture list should include an unchanged-count exit 4 with a budget-exceeded candidate.

**Proposed minimal solution.** Read the current attempt's fresh verdict/progress terminal after each exit. Treat `BUDGET_EXCEEDED/LANE_TIMEOUT` or another explicitly resumable time boundary as the only exit-4 case eligible for relaunch. Stop with a durable reason and final exit when the terminal reason is `MUTANT_LIMIT_EXCEEDED`, `NO_MUTANTS`, or another non-resumable plan/config refusal. Never read a previous attempt's stale verdict; bind the reason artifact to the attempt/run identity. Replace the F8 line-count check with completed-candidate count for the current run: `mutation-state-<lane>/*.json` or current-run `event=candidate` records, excluding baseline/plan/candidate-header events. Preserve the resume state on a real budget stop.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`: exit classification, current-attempt verdict/progress selection, `final-exit`, and stall bookkeeping. `scripts/netcup/runner/lib.sh` for shared fresh-artifact helpers. Runner tests under `scripts/netcup/tests/`, with fake progress, verdict, and state files.

**Acceptance test idea.** A current-attempt `MUTANT_LIMIT_EXCEEDED` fixture stops after one attempt without a relaunch; a current-attempt `NO_MUTANTS` fixture likewise stops with the named reason. A `LANE_TIMEOUT` fixture resumes and the next attempt must add completed state before being considered progress. A baseline-only attempt that emits many plan/progress lines but zero candidate-state files is classified stalled. A stale verdict from an older attempt cannot drive the decision. Hand-removing reason classification or reverting to line count fails the focused oracle.

**Priority.** Major. The current behavior turns deterministic configuration failures into long false retry loops.

**Close when.** Fake-campaign tests prove reason-aware stop/relaunch classification, fresh-attempt artifact binding, and completed-candidate stall progress; a live campaign stop records the exact non-resumable reason and final exit without relaunching; and the runner documentation names which reasons are resumable.

**Confirmed again 2026-11-12.** The CIU R2 campaign report records attempts 3-12 each judging exactly 0
candidates with `pending_total=0`; every attempt nevertheless re-ran the baseline, and the stall detector
counted those test events as progress. The supervisor therefore used ten attempts and about 75 minutes after
all 627 candidates had already reached terminal state. The current-attempt outcome was
`BUDGET_EXCEEDED`, but that did not make any of the seven stored `budget_exceeded` candidates runnable and no
candidate completion was possible. The decision must therefore include a full terminal-condition fast stop
when both the current attempt's judged-candidate delta and the resumable pending count are zero, regardless
of the stored outcome label.

**Confirmed again 2026-11-14 (selected subset).** The PWMCP R9 campaign ran an `--only-candidates` set of 44
candidates. Attempt 1 ended exit 4 with `completed_candidates` 0->44, so the stall counter saw progress and the
supervisor launched attempt 2, which spent about eight minutes on a baseline with nothing left to judge. The
pre-launch check must therefore compare the pending count of the SELECTED set (zero here) before starting any
further attempt, not only the judged-candidate delta after a wasted one. The result stays a partial campaign,
not a whole-target completion.

## NC-37 — a T3 campaign has no explicit comparison base or pre-baseline source-diff check

**Priority:** should-fix. Filed 2026-11-11. Source: host-campaign controller session record 2026-11-11; related to Run-Gate RG-26 and RG-263.

**Problem.** The delegated mutation lane requires a comparison base, but the campaign entry has no explicit `base` field. Operators pass an ad hoc Run-Gate argument, so the candidate population can change without appearing in the campaign identity. A base that differs only in tests is especially expensive to discover after the fact: Run-Gate runs the baseline and assays the source diff, finds no source candidates, then ends `NO_MUTANTS`. Combining different bases under one campaign name can also mix incompatible progress/resume state.

**Evidence.** The host-campaign controller session record 2026-11-11 records CIU's `base_source = "request"` behavior: no `--base` refuses `NOT_RUN`. The previous release tag `ciu-v7.24.0` produced `NO_MUTANTS` for target `ciu-v7.24.1` because the release changed only tests, after about four minutes of baseline. The campaign instead ran with `--base ciu-v7.22.0`, producing 9-10 source files and 627 candidates. The two bases were selected through run-gate arguments, not a declared campaign fact.

**Scope after 2026-11-12.** The CIU campaign's ten zero-judged-candidate attempts did not arise from an
empty source diff: the campaign had already judged all 627 candidates, and attempts 3-12 resumed that terminal
record set. Their stop-and-progress behavior belongs to NC-36 and NC-40. This entry remains limited to
creating and freezing the comparison base and refusing an empty source scope before the first baseline.

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

## NC-39 — installed runner components, including the R2 supervisor, can differ from a selected repository commit with no provenance inventory or reconciliation path

**Priority:** should-fix. Filed 2026-11-11. Source: host-campaign controller session record 2026-11-11.

**Problem.** A runner host can execute installed campaign scripts that differ from the reviewed repository version, including an older `run-r2-campaign.sh` whose lifecycle differs from the merged supervisor. Those scripts worked in the observed campaign, but their provenance and behavioural delta were unknown, so routine results depended on unaudited host-local code. The observed old supervisor had no supported `stop` command or per-start suffix directories, leaving termination of the cockpit-side Run-Gate process as the only clean manual stop. There is no supported read-only inventory command that reports installed-vs-selected-repository identity for the complete runner set and no reconciliation path that preserves active campaign state.

**Evidence.** The host-campaign controller session record 2026-11-11 compared an installed runner with repository `main`: installed `run-job` sha256 began `6dc59f73` while main began `149c818c`; installed `lib.sh` began `66890af0` while main began `8435e07f`. The installed scripts worked but the difference was not investigated or reconciled. The later host-parallel R2 record found that both test hosts still ran the old supervisor, with no supported stop and no per-start suffix state; terminating the cockpit-side Run-Gate process was the only clean stop and preserved campaign state. Repository main now carries the merged supervisor with the supported lifecycle, making this a deployed-component reconciliation gap rather than a new supervisor design item.

**Confirmed again 2026-11-12.** A legacy supervisor had no active campaign
record, so the merged `stop <base>` refused at
`scripts/netcup/runner/run-r2-campaign.sh:205-220`. The supported fallback was
to terminate the cockpit-side `run-gate <lane>` client between attempts; the
legacy supervisor then exited on a status other than 4, released its lock and
left no supervisor or sampler process. Runner documentation must state this
boundary for the pre-NC-42 supervisor rather than imply that the new stop verb
can reconcile an unrecorded start.

**Documentation addendum.** The runner README must explain that `stop <base>`
addresses only NC-42 starts with a readable active record. For a pre-NC-42
start, terminate the cockpit-side `run-gate <lane>` client between attempts;
the legacy supervisor then exits on a status other than 4 and releases its
lock. Do not imply that the new stop verb can address an unrecorded start.

**Proposed minimal solution.** Add one supported, read-only provenance verb that takes the selected repository commit/source root and inventories every installed runner component, including the campaign supervisor and its helper scripts, with its resolved path, SHA-256, selected-repository SHA-256, and match/missing/extra state. Refuse an unrecognised selected source rather than compare against an implicit latest checkout. Provide a reconcile/update path that stages the reviewed files from the exact selected commit, verifies hashes, and refuses while a conflicting campaign or job is active; never silently overwrite live scripts or infer equivalence from matching versions. Require reconciliation before the next campaign on a host whose supervisor lacks the selected commit's supported lifecycle. The report may say reconciliation is deferred, but it must identify every differing file and the exact installed and selected hashes.

**Where.** `scripts/netcup/runner/run-job`, `scripts/netcup/runner/lib.sh`, `scripts/netcup/runner/run-r2-campaign.sh`, the runner install/update scripts and README, plus offline tests using fake installed/repository trees.

**Acceptance test idea.** A fake host with one matching and three differing scripts, including a supervisor without a supported stop or per-start directory, prints all paths and full hashes and exits non-zero while drift exists. Reconciliation from a selected commit updates exactly those files, records before/after hashes, and preserves unrelated/extraneous files for an explicit decision. An active campaign causes reconciliation to refuse without writing. A changed but unreported script is never classified as current. The selected supervisor is proven by its `stop` usage and per-start state behavior, not by a version string alone.

The acceptance oracle also includes a docs-contract test for the pre-NC-42
stop boundary and the between-attempt fallback.

**Priority.** Should-fix. Campaign evidence must come from code whose exact deployed identity is knowable.

**Close when.** The provenance command reports exact installed/repository hashes for every runner component, including the supervisor; a supported reconcile operation can update from a selected commit only after active-work checks and hash verification; the reconciled supervisor exposes its supported stop and per-start lifecycle; the runner README documents the pre-NC-42 stop boundary and fallback; and fake-tree plus docs oracles pass on a clean final commit.

## NC-40 — R2 supervisor counts re-reported kills as progress and misses a stalled resume

**Priority:** major. Filed 2026-11-11. Distinct from NC-36: NC-36 covers baseline progress and exit-4 classification; this entry covers reused candidate events emitted again after resume.

**Finding.** The main supervisor counts every line in its progress files (`runner/run-r2-campaign.sh:56-69`), while the pending T3 supervisor's candidate counter counts every `event=candidate` row without filtering `reused`. Assay emits a candidate progress row with `reused: true` for each previously killed candidate on resume (`cmru/tools/mutation_campaign.py:1257-1266`). Re-reported kills therefore raise the supervisor's progress count even when no new candidate has completed. When the campaign repeatedly reruns earlier survivors and timeouts (KI-152), this can keep the stall check from detecting the lack of forward progress until the attempt cap.

**Proposed minimal solution.** Count only newly completed candidate outcomes as progress. Ignore replayed `reused: true` rows, baseline events, and other progress records that do not advance the set of completed candidate indexes.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh` progress accounting and its offline supervisor tests.

**Acceptance test idea.** Feed the stall check repeated `candidate` records with `reused: true` and no new candidate result; assert the progress counter does not advance and the configured stall response occurs. Then append a newly completed candidate result and assert it advances progress and resets the stall count. Include baseline events to preserve NC-36's separate guard.

**Confirmed again 2026-11-12.** The CIU R2 campaign report records 6,344 baseline `test` events in each of
attempts 3-12. Those events advanced the current line-counting signal while each attempt judged zero
candidates. The real-signal threshold is a newly completed, non-reused candidate outcome;
`pending_total=0` plus a zero judged-candidate delta must stop the supervisor immediately.

**Close when.** Replayed kills, baseline/test events, and progress-file writes do not reset the stall counter;
a new non-reused completed candidate does; a current attempt with a zero judged-candidate delta and no
resumable pending candidate stops without relaunch; and focused tests prove all three behaviors rather than
repeated evidence rows.

## NC-41 — the R2 supervisor does not record the selected worker count and has no sustained under-utilisation advisory for a campaign consuming little of its CPU allocation

**Priority:** should-fix. Filed 2026-11-11. Source: host-parallel R2 controller record 2026-11-11; related to Run-Gate RG-264 and the tag-time identity contract.

**Problem.** The R2 supervisor records memory, CPU PSI and memory PSI samples, but it does not log the selected worker count as a campaign fact and cannot warn when a multi-worker campaign leaves most of its allocated CPU capacity idle for a sustained period. An operator therefore has to infer N from attempt output and watch CPU by hand. In the measured campaigns, one parallel lane used roughly three of four allocated CPUs while another used under half of its four-core envelope because per-candidate snapshot and orchestration work dominated. Those two situations need different operator action: keep the first, and investigate scheduling or resource choice for the second. The sampler has no common vocabulary for that distinction.

**Evidence.** The host-parallel R2 controller record measured a CIU run at three workers using about 73 percent of four cores, while a PWMCP run at three workers used about 43 percent and was limited by non-CPU work. The attempt logs contain Run-Gate's `R2 jobs:` disclosure, but the supervisor's `samples.csv` and cycle summary do not retain N or a durable under-utilisation advisory. A mid-campaign attempt to raise N also invalidated the complete finished-candidate state, which is why the advisory must point to tag-time policy rather than recommend a live edit.

**Proposed minimal solution.** At campaign start, extract the selected N and its declared CPU/memory ceilings from the attempt's Run-Gate disclosure, validate those fields, append them to campaign metadata and `attempts.log`, and fail visibly if they are absent from a native R2 run. In the sampler, derive CPU busy from the lane container or lane cgroup's cumulative CPU-use delta and its applied CPU quota over the same interval; do not infer host cores or use host load. After a bounded sustained window, emit one advisory when busy utilization stays below 50 percent of the allocated capacity. The advisory may recommend investigating worker/per-candidate overhead or choosing a new tag-time configuration; it must not suggest editing a running campaign. Keep PSI and memory samples as the capacity evidence.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`: attempt-log/Run-Gate disclosure parsing, campaign metadata, sampler loop, warning state machine and cycle summary; `scripts/netcup/runner/README.md`: the worker-count fields and advisory semantics; runner tests under `scripts/netcup/tests/` with fake cgroup CPU counters, quotas, clocks and attempt logs. RG-264 owns the cross-project tag-time documentation and Run-Gate preflight wording.

**Acceptance test idea.** A fake native R2 attempt log with N=3 and CPU/memory ceilings causes the supervisor metadata and attempt log to record those exact values. A fake sampler clock below 50 percent allocated-CPU busy for the sustained window emits one advisory naming N, the window and the tag-time contract; a later recovery clears the condition and a second sustained interval may emit one new advisory, never one per sample. A busy run emits none. Missing N in a native R2 attempt log fails visibly. Changing the cgroup counter source, hardcoding host cores, or warning from a single sample fails the focused test.

**Priority.** Should-fix. Without this signal, an expensive campaign can silently waste most of its host allocation and operators are tempted to make the one change that invalidates all completed evidence.

**Close when.** Native R2 campaign metadata and logs record the validated worker count and resource ceilings, fake-clock/cgroup tests prove the sustained under-utilisation and recovery behavior, the advisory names the tag-time configuration boundary rather than a live override, and the runner documentation matches the implementation on a clean final commit.

## NC-42 — one lane container per host: no shard instances, cross-host split or carry-over restart

**Status:** FIXED in `netcup-r2-instances-20261111` (2026-11-11; merge pending review). Needs Run-Gate RG-265 and Assay 12.2.0 (B529-B531) on the host.

**Problem.** Operator 2026-11-11: "if we have enough RAM we can place 5 containers. io phase will allow another cpu bound process to run during that time." The supervisor ran exactly one lane container; `--jobs` inside it is bounded by that container's caps, a second campaign of the same lane was refused by the lock, and a restart after an upgrade re-executed every candidate.

**Fix.** `--instances K [--shard-total T --shard-offset O]`, `--merge-only T`, `--carry-over-main TEXT`, `status <base>`, fleet-aware `stop <base>`, a `MemAvailable` guard (`R2_INSTANCE_MEM_MIB`, `R2_HOST_RESERVE_MIB`), staggered starts (`R2_INSTANCE_STAGGER_S`), per-instance progress counting and verdict lookup (`R2_INSTANCE_ARTIFACT`). Children are ordinary supervisor starts, so the existing relaunch, stall, stop and summary rules apply to each.

**Independent review (luna, FIX-FIRST on `8ec050337`), fixed:** option presence is tracked apart from values (zero, empty, malformed or repeated values refused by name); the memory guard has no 2048 MiB default (an uncapped lane needs `R2_INSTANCE_MEM_MIB`) and bounds its inputs to 7 digits; an empty `--carry-over-main` is refused; the functional floor probes the host Run-Gate's `--help` for RG-265 before any state (older 25.0.2/25.4.0 refused for fleet/carry options, legacy path unchanged); shard artifacts follow Run-Gate's `<assay_lane>.<lane>.shard<K>of<N>` naming; README/CONSUMERS/DESIGN-GUIDE synchronized; the label-only stop path has its own test.

**Named limits.** The memory guard is an admission check at start, not a runtime limit (each container keeps its own cap). Copying remote shard stores for a cross-host merge is a manual step. A fleet's children share the exit-4 relaunch policy of NC-36.

**Tests.** `tests/test_run_r2_campaign.py` (NC-42 section).

## NC-43 — the runner endpoint loader rejects the BuildKit/Docker GID identity that host-setup produces

**Status:** FIXED 2026-11-24 (package P-plat-19g; seen again on r1002 2026-11-24). `load_host_endpoints` accepts
`MDT_BUILDKIT_GID == MDT_HOST_DOCKER_GID` (docker-group membership is already root-equivalent, so a separate
BuildKit group adds no isolation); cgprofile/topos must still differ from docker and pairwise. Filed 2026-11-12,
should-fix, fresh-host launch blocker. Source: r1002 reinstall execution record 2026-11-12.

**Problem.** `scripts/netcup/runner/lib.sh` loads the published host endpoints and requires each level GID
(`CGPROFILE_CONTROL_GID`, `TOPOS_OBSERVE_GID`, `MDT_BUILDKIT_GID`) to differ from `MDT_HOST_DOCKER_GID`,
then requires the level GIDs to differ pairwise. mdt-cockpit's `mdt-buildkitd.service` deliberately changes
the BuildKit socket to the host's `docker` group and publishes that group as `MDT_BUILDKIT_GID`. On a real
host these contracts cannot both hold. The checked-in `start-cockpit-runner.sh` consequently refused to
start; a host-local patch exempted only the BuildKit-versus-Docker comparison while preserving the other
checks.

**Evidence.** The r1002 reinstall execution record 2026-11-12 records the refusal
`MDT_BUILDKIT_GID must differ from MDT_HOST_DOCKER_GID` immediately after host-setup, cgprofile and topos
installation had published one coherent endpoint record. The BuildKit socket was owned by the Docker group
and mode `0660`, which is the producer's stated access contract. The same record documents the minimal
host-local loader patch and successful launcher start. This is a consumer/producer contract mismatch, not an
invalid host endpoint record.

**Minimal solution.** Treat Docker-group access as the BuildKit endpoint's intentional exception: continue
requiring `CGPROFILE_CONTROL_GID` and `TOPOS_OBSERVE_GID` to differ from the Docker GID, and keep pairwise
distinctness where it protects separate authority levels. Permit `MDT_BUILDKIT_GID ==
MDT_HOST_DOCKER_GID` only when the declared BuildKit socket directory and socket contract passes the
existing ownership/mode checks. Do not weaken the local Docker group/GID cross-check or invent a new
BuildKit group in host-setup. Link the producer contract in mdt-cockpit rather than duplicating it there.

**Where.** `scripts/netcup/runner/lib.sh` (`load_host_endpoints`), the rendered cockpit launcher behavior in
`scripts/netcup/runner/start-cockpit-runner.sh`, and `scripts/netcup/tests/test_runner_host_endpoints.py`.
The producer is `mdt-cockpit/host-setup/units/mdt-buildkitd.service.in` and its endpoint publisher.

**Acceptance test idea.** An endpoint fixture with `MDT_BUILDKIT_GID == MDT_HOST_DOCKER_GID`, distinct
cgprofile/topos GIDs and the matching local Docker group loads successfully. Equality for either
cgprofile/topos GID still fails by name; pairwise collisions among the remaining authority levels still fail;
a mismatched local Docker group still fails; and the fake-Docker launcher acceptance shows the resulting
`--group-add` set and BuildKit mount. Reintroducing the blanket BuildKit inequality fails the new equality
oracle.

**Priority.** Should-fix. The checked-in fresh-host launch path is unusable until this mismatch is resolved.

**Close when.** The supported endpoint loader accepts host-setup's BuildKit/Docker group identity, preserves
the other authority-separation checks, renders and starts the cockpit against such a record without a
host-local patch, and the endpoint plus launcher tests pass on a clean final commit.

## NC-44 — the ordered reinstall runbook omits the post-wipe root wrapper and the guarded --yes flow

**Status:** OPEN (filed 2026-11-12); priority minor, documentation ambiguity in a destructive workflow. Source:
r1002 reinstall execution record 2026-11-12.

**Problem.** `scripts/netcup/CONSUMERS.md` documents the guarded `pre-reinstall.py` dry-run and `--yes`
flow, including the exact reviewed bundle hash, commit and SSH-key identity. The runner README's ordered
reinstall sequence starts at the provider reinstall but does not link that flow or explain the wrapper switch
required after a wipe. A runner-only wrapper depends on the runner account and its server-id file; neither
exists until runner provisioning. The real reinstall therefore required a separately authorized root
wrapper for post-key-pin and provisioning checks. Leaving that fact only in an execution record makes the
next operator likely to stop at a correct refusal or reconstruct an unreviewed root path.

**Evidence.** The r1002 reinstall execution record 2026-11-12 records the guarded dry-run, the detached `--yes`
submission with its reviewed bundle SHA-256, and the post-wipe deviation to a root wrapper because no runner
account or server-id existed yet. The same record accepts an absent server-id only during bootstrap while
retaining the hostname and static-hostname identity checks and the pinned host key. The current netcup
consumer guide already contains the `--yes` invocation; the missing piece is its placement and linkage in
the runner's ordered runbook, plus the root-wrapper boundary.

**Minimal solution.** In `scripts/netcup/runner/README.md`, make P1 link directly to the guarded reinstall
consumer section and name the order: reviewed dry-run, record the bundle hash, then the foreground `--yes`
submission with the exact commit/hash/key identity. Between P1 and P3, document the root wrapper as the only
path while the runner identity is absent, including its hostname/static-hostname guard, pinned host key,
bootstrap treatment of a missing server-id, and the transition back to the runner wrapper after
provisioning. Do not copy machine-specific addresses, controller paths or account IDs into the repository.

**Where.** `scripts/netcup/runner/README.md` P1-P3; link to
`scripts/netcup/CONSUMERS.md#guarded-data-area-v2-reinstall` and the existing wrapper verification rather
than restating safety mechanics. Any documentation-contract test that checks the ordered reinstall
vocabulary.

**Acceptance test idea.** The runbook contains both the guarded dry-run and `--yes` invocations or direct
links, names the bundle-hash/commit/key preconditions, and states which wrapper is valid before and after
runner provisioning. A docs check fails if the root-wrapper boundary or the `--yes` sequence disappears. The
text contains no host address, controller filesystem path or account key identifier.

**Priority.** Minor. The destructive flow is already guarded; the gap is operator reachability and ordering.

**Close when.** The ordered runner reinstall documentation names both wrappers and their switch point, links
the guarded dry-run/`--yes` consumer flow, explains the bootstrap identity exception, and the documentation
contract passes on a clean final commit.

## NC-45 — the R2 supervisor's checkout layout is outside the assay full-qualification driver's accepted workspace family

**Status:** OPEN (filed 2026-11-12); no longer blocks B535's native
`assay-r2` lane; remains applicable to command-backed qualification.
Source: 2026-11-12 host R2 restart record; cross-reference assay B541.

**Problem.** `run-r2-campaign.sh` requires the persistent checkout at
`$RUNNER_HOME/checkouts/$commit` and validates only that directory and its
project subdirectory (`scripts/netcup/runner/run-r2-campaign.sh:377-391`).
The synchronized runner checkout is exposed in the cockpit as
`/workspaces/<commit>`. Assay's full-qualification driver accepts only
`/workspaces/vbpub` and nested vbpub worktrees
(`assay/tools/self-qualification-container.sh:104-107`), so command-backed
`self-qualification` exits before preflight from the supervisor checkout.
Assay's B535 R2 follow-up now uses native `kind = "assay"` lane `assay-r2`,
which bypasses this driver and therefore removes this layout as a blocker for
that R2-only campaign. The driver itself remains unchanged.

**Minimal solution.** Add an explicit, validated checkout-directory option to
the supervisor for lanes that declare a narrower workspace requirement, or
document a supported layout-neutral handoff. The option must resolve an
existing non-symlink directory and preserve the full commit, clean-tree,
project-directory and exact-container-label checks. Do not derive a location
from lane output, and do not make arbitrary runner paths acceptable by default.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`,
`scripts/netcup/runner/README.md`, and `scripts/netcup/tests/test_run_r2_campaign.py`.
Assay B541 owns acceptance of the selected layout inside the driver.

**Acceptance test idea.** A fake nested-vbpub checkout accepted explicitly by
the option reaches the supervisor launch with the same clean-tree and commit
checks; the default `/workspaces/<commit>` path remains unchanged; relative,
symlink, missing, dirty and wrong-commit targets refuse by name. A control run
without the option reproduces the current assay refusal.

**Close when.** If the runner continues to support command-backed Assay
qualification, its documented handoff lets the B105 driver accept the
supervisor checkout while preserving full-commit, clean-tree, project-directory
and container-identity checks; positive and refusal oracles pass on a clean
final commit. B535's native R2 campaign is covered by `assay-r2` and is not a
close condition for this command-driver issue.

## NC-46 — a bundle filename becomes the lane-container repository name

**Status:** OPEN (filed 2026-11-12); priority minor for operator exact-name
selection. Source: 2026-11-12 host R2 restart record.

**Problem.** `sync-from-devcontainer` clones directly from the uploaded bundle,
so the clone's `remote.origin.url` remains the bundle path
(`scripts/netcup/runner/sync-from-devcontainer:31-33`).
`run-r2-campaign.sh` derives the container repository part from that URL tail,
removing only `.git`
(`scripts/netcup/runner/run-r2-campaign.sh:393-425`), so a filename such as
`release.bundle` survives into `release.bundle-run-gate-...`. Operators
selecting or guarding a lane by
exact container name therefore become coupled to an incidental upload name.

**Minimal solution.** Set the imported checkout's `origin` to a stable,
declared repository identity, or add an explicit validated repository-name
input to the sync/campaign contract. Derive the displayed repository count from
the selected repository contract rather than inventing one from an uploaded
filename. Remove only known transport suffixes and still name the actual
observed name in refusals.

**Where.** `scripts/netcup/runner/sync-from-devcontainer`,
`scripts/netcup/runner/run-r2-campaign.sh`, `scripts/netcup/runner/README.md`,
and focused runner tests.

**Acceptance test idea.** Imports from two differently named bundles of the
same repository produce the same deterministic repository container part.
Explicit names are validated, absent names have one documented supported
derivation, and a stale label-based stop still reports the exact name it
searched.

**Close when.** Bundle names no longer appear as repository identities in
new lane-container names, both bundle-name fixtures produce the declared name,
and exact-name discovery/stop documentation and tests agree on the same
derivation.

## NC-47 — remote-gate collection discards the registered tester-unified receipt

**Status:** OPEN (filed 2026-11-12); priority should-fix for reusable release
qualification evidence. Source: 2026-11-12 host R2 restart record;
cross-reference assay B123 and B535.

**Problem.** Assay's full qualification requires a four-key receipt at
`.assay/registered-gate/tester-unified.json`
(`assay/tools/b105_report_check.py:1081-1128`), but the self-qualification lane's
declared artifacts do not include that path (`assay/run-gate.toml:138-145`).
Remote-gate archives only files under `.assay/` or `.run-gate/` whose names
match verdict, progress, coverage, log or XML globs
(`scripts/netcup/runner/remote-gate.py:599-601`); it then collects that tarball
unchanged (`scripts/netcup/runner/remote-gate.py:887-904`). A release's passing
tester-unified run therefore leaves a receipt on the runner but drops it from
the collected evidence, preventing the tag's throwaway checkout from feeding
the later full qualification.

**Minimal solution.** Archive every artifact explicitly declared by the
selected lane, with safe path validation, or declare the receipt as a
self-qualification artifact and include it in the bounded collection filter.
The copy must preserve bytes and path identity, and its collection must be
reported even when empty. Do not widen the archive to every file under
`.assay/`.

**Where.** `scripts/netcup/runner/remote-gate.py`,
`scripts/netcup/runner/tests/test_remote_gate.py`, and the artifact declaration
in `assay/run-gate.toml`; B123 owns receipt validation.

**Acceptance test idea.** A fake completed run containing
`.assay/registered-gate/tester-unified.json` collects the exact bytes at the
declared path. Missing, oversized, symlinked or traversal-shaped paths fail or
are reported by name; unrelated `.assay` files remain excluded; and an archive
without the receipt cannot be mistaken for a complete qualification input.

**Close when.** A remote tester-unified run from a release checkout yields its
same-commit receipt in collected artifacts, B535 can consume it without a
runner-side side channel, and focused exact-path and exclusion tests pass on a
clean final commit.

**Second observed instance (2026-11-16).** The same filter discards a lane's
OTHER declared artifacts, not only the receipt: assay's
`rust-r1-qualification` declares `artifacts = [".assay/rust-r1-real"]`
(`assay/run-gate.toml:136`) and PASSED in about 4 s on a remote host with an
EMPTY collected `artifacts/` directory, so the lane's real cargo/lcov evidence
could not be inspected afterwards (pre-release gate report of 2026-11-16,
item F5). No new id is filed for it: the fix above — archive exactly the
artifacts a lane declares, with safe path validation and a reported outcome
even when the archive is empty — covers this case, and this entry now carries
it as a second reproduction. The acceptance test for it is the same test with
a declared-directory fixture (a directory holding two files survives with its
path identity preserved) alongside the receipt-path case above.

## NC-48 — R2 fleet admission multiplies the full lane ceiling and hides host capacity signals

**Status:** IMPLEMENTED unreleased in `r2-guard-20261112`; suite and final
commit evidence recorded in `r2-guard-REPORT.md`.

**Problem.** The fleet guard charged every shard the lane's full
`resources.memory`, although that ceiling is sized for all workers in one
container. A PWMCP R2 lane declared 3 GiB and ran three workers at about 430
MiB and 104% CPU in an observed sample. Two instances then required
`2 x 3072 + 1024 = 7168 MiB`, so the guard refused against 6454 MiB available.
This made spare CPU unusable and treated a container ceiling as expected RAM
demand. The runner also printed no CPU hint for an underused single-worker
native R2 lane and did not expose whether KSM was active or useful to its
workloads.

**Design.** Keep the configured Run-Gate memory as the per-container hard
ceiling; Run-Gate has no per-invocation memory override. For `jobs_max = J` and
`j` workers per instance, disclose a scaled ceiling
`max(128 MiB, ceil(lane_cap x j / J))`. The expected peak defaults to
`ceil(lane_cap / J)` per worker and sums across the workers and instances.
`R2_WORKER_MEM_MIB` overrides that expected per-worker peak, while the existing
`R2_INSTANCE_MEM_MIB` remains an explicit measured per-instance peak. Refuse
an uncapped lane unless the per-instance override is supplied.

When a lane declares `jobs = "auto"` and a fleet start omits `--jobs`, pass
`--jobs max(1, ceil(lane_jobs_max / K))` to each shard, where K is the instance
count. An explicit `--jobs` overrides this default.

Expected peaks must fit `MemAvailable - R2_HOST_RESERVE_MIB +
R2_EXPECTED_PEAK_CREDIT_MIB`. The reserve defaults to 1024 MiB and the explicit
expected-peak credit defaults to 0 MiB. Swap and KSM state add no automatic
credit; the credit variable is an operator opt-in for a measured value and
affects only the expected-peak budget. Separately, compare the sum of full
declared container caps (`K x lane_cap`) with `MemAvailable x R2_OVERCOMMIT`.
`R2_OVERCOMMIT` accepts 1.0..4.0, defaults to 2.0, and applies only to this
declared-cap comparison. Print the arithmetic for both budgets and the
explicit expected-peak credit.
When a native R2 lane's worker count is below `nproc`, print the advised
`--instances floor(nproc / workers)` when the workers are single-threaded; do
not change the command automatically. Print and save KSM `run`, `pages_sharing`
and `pages_shared`, with lane eligibility marked unverified unless workload
processes opt in through `MADV_MERGEABLE` or `PR_SET_MEMORY_MERGE`. Do not
enable KSM.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`, its runner tests and
README, plus the Netcup README, DESIGN-GUIDE, CONSUMERS and CHANGES.

**Acceptance and mutation oracles.** The registered `scripts/netcup suite`
must prove that three one-worker shards of a 3 GiB / three-worker lane are
admitted when their expected peaks fit the available-memory budget; a measured
worker override is summed per worker; explicit expected-peak credit affects
only that budget, while swap and KSM state alone do not admit over-budget
peaks; and overcommit applies only to the sum of declared caps. The suite must
also prove the default per-instance jobs formula, refusal of an uncapped lane
without `R2_INSTANCE_MEM_MIB` even when `R2_WORKER_MEM_MIB` is set, the CPU hint
without rewriting args, and fake KSM sysfs values in preflight and status.
Mutants that multiply the full ceiling, apply swap/KSM or cap overcommit to
expected peaks, skip the declared-cap comparison, suppress the CPU hint, or
omit KSM status must fail their corresponding tests.

**Close when.** The registered suite passes on the final committed head, the
merge-tree check against `origin/main` succeeds, and the report records the
gate and mutation evidence.

## NC-49 — runner guidance does not explain when `--instances` helps or its single-host cost

**Status:** OPEN (filed 2026-11-12); priority enhancement. Source:
[`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md) sections 0.5–0.7 and 10.2–10.3.
Related: NC-48, implemented unreleased in `r2-guard-20261112`; it owns
per-instance memory admission and the CPU hint. This item adds measured
operator guidance and cross-references NC-48 without restating either behavior.

**Problem.** The runner guides explain how to start shards and check the
memory guard, but do not tell an operator when extra instances are useful.
The measured ciu pilots used about 310 MiB more across three one-worker
instances than one three-worker container, while throughput stayed within
candidate-mix variation. Four one-worker instances reached 29–40% CPU PSI on a
four-core runner for an effectively unchanged 1.75/min versus 1.7/min at three
instances. Operators can read `--instances` as a way to add CPU capacity on
one host when its actual use is to shard work across hosts or to run a lane
whose worker limit prevents sharing one container.

**Evidence.** The report records 1 x 3 at 1.3–2.4 candidates/min, 3 x 1 at
1.7/min and 4 x 1 at 1.75/min, with 29–40% CPU PSI in the four-instance pilot
([`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md):14-19, 145-154). The runner README
documents the shard mechanics and guard but not this trade-off
(`scripts/netcup/runner/README.md:134-164`). NC-48 owns the admission estimate
and CPU hint; this item adds measured operator guidance only.

**Minimal solution.** Add the measured use cases and limits to the runner
README and CONSUMERS guide: use instances to split a campaign across hosts or
to honor a lane's `jobs_max = 1` sharding constraint; on a four-core host, do not expect more
throughput from `--instances K --jobs 1` than from sharing up to three workers
in one container. State the observed per-instance memory overhead as a
measurement, not a universal constant. Link the rationale from the DESIGN-GUIDE
and cross-reference NC-48 for the guard and CPU advisory rather than restating
its design.

**Also observed 2026-11-14 (Run-Gate 25.6.0 native R2).** The `assay-r2` lane has `jobs_max = 2`; `--jobs 2`
filled about 200% of a four-CPU host and left two CPUs idle (the runner printed the `--instances` hint), while
`--instances 2` would have doubled the lane's configured 2 GiB memory demand. The guidance added here should
state this `jobs_max` versus `--instances` trade-off for a low-`jobs_max` lane next to the `jobs_max = 1`
example.

**Where.** `scripts/netcup/runner/README.md`,
`scripts/netcup/CONSUMERS.md`, the relevant DESIGN-GUIDE section, and the
runner docs-contract test.

**Acceptance test idea.** A docs contract pins both supported use cases, the
four-core measurement as an example rather than a machine limit, and the link
to NC-48. It rejects wording that promises single-host throughput gains or
duplicates NC-48's memory guard design.

**Close when.** README, DESIGN-GUIDE and CONSUMERS state the same use cases
and trade-offs; CONSUMERS contains a pasteable example for a cross-host split
and a `jobs_max = 1` lane; the docs contract passes in the registered suite;
and NC-48 remains the sole owner of the memory-admission and CPU-hint behavior.

## NC-50 — `run-job` is installed under `~/bin` but bare-name examples fail in non-login runner shells

**Status:** OPEN (filed 2026-11-12); priority should-fix for reproducible
runner commands. Source: [`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md) sections
6.1 and 10.8.

**Problem.** Runner provisioning installs `run-job` at `~/bin/run-job` and
copies it into `~/runner-scripts`, while several runner examples invoke
`run-job` by bare name. The non-login
`bash -c` wrapper used for runner commands does not include `~/bin` in its
PATH, so the bare command fails with `command not found` before the campaign
starts. This makes a documented command depend on an interactive login PATH.

**Evidence.** Provisioning installs the executable at
`$RUNNER_HOME/bin/run-job` and copies it to `$RUNNER_HOME/runner-scripts`
(`scripts/netcup/runner/provision-runner.sh:142-149`),
and the runner README uses the unqualified name in its command examples
(`scripts/netcup/runner/README.md:105, 129, 216, 582`). The report
records that the non-login wrapper succeeds with `~/bin/run-job`
([`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md):87-92, 177).

**Minimal solution.** Use `~/bin/run-job` in runner instructions that may run
under a non-login shell, including the command snippets in README and
CONSUMERS. If the runner PATH is changed instead, make it part of the
provisioned non-login environment and keep the absolute installed location
explicit in diagnostics.

**Where.** `scripts/netcup/runner/README.md`,
`scripts/netcup/CONSUMERS.md`, runner provisioning only if PATH is changed,
and a docs or script regression check for the non-login invocation.

**Acceptance test idea.** A clean environment with no login profile runs the
documented `~/bin/run-job` form successfully. A bare-name invocation with
`~/bin` absent reproduces the named failure; a test must not pass because the
test process inherited the cockpit's interactive PATH.

**Close when.** Every documented non-login runner command resolves the
provisioned `run-job` executable deterministically, the README/CONSUMERS
examples match that contract, and the focused fake-PATH check plus the
registered suite pass.

## NC-51 — a fleet launches a merge without a visible incomplete-shard gate, and status says generic `running`

**Status:** OPEN (filed 2026-11-12); priority should-fix for fleet-result
integrity. Source: [`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md) section 10.9.
Related: NC-42, which introduced fleet starts, child status and strict merges.

**Problem.** When all three child campaigns ended with `final_exit=1`, the
supervisor still launched the merge run without first recording whether each
shard was complete. A fleet with an active merge is reported as `running`, so
its status line does not distinguish mutation work from merge work or show
that child failures preceded the merge. A strict merge may legitimately
combine complete non-passing shards; it must not run when a child left an
incomplete shard, and the status must name that state.

**Evidence.** The campaign report records three children at `final_exit=1`
followed by a running `merge-s1` ([`KSM evidence report`](../../mdt-cockpit/docs/evidence/hosts-ksm-instances-REPORT-2026-11-12.md):178).
`run_fleet()` collects every child exit before its merge branch, selected by
`shard_offset == 0 && instances == shard_total`
(`scripts/netcup/runner/run-r2-campaign.sh`, `run_fleet()`); NC-51 needs to
check shard completeness at that point, independent of exit code.
`status_campaign()` labels a directory without `final-exit` as `running`
(`scripts/netcup/runner/run-r2-campaign.sh`, `status_campaign()`). NC-42 documents the
strict merge and the per-run status lines, but not the incomplete-child gate
or a distinct merging state (`scripts/netcup/runner/README.md:134-164`).

**Minimal solution.** Before launching the strict merge, determine whether
every required shard is complete for the current judge and plan. Keep merging
complete shards even when their final verdict is non-passing. If any required
shard is incomplete, skip the merge and write a durable `merge-skipped` reason
with the missing or incomplete shard; report the active merge phase as
`merging`, not generic `running`.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`, fleet status and
summary generation, runner tests using fake child exits and shard manifests,
and the runner README/CONSUMERS/DESIGN-GUIDE.

**Acceptance test idea.** Complete shards with a nonzero, non-passing child
verdict still launch a strict merge and preserve the whole-lane result.
An incomplete shard prevents any merge invocation, writes `merge-skipped`
with the exact shard reason, and appears that way in status. A currently
active strict merge is reported as `merging`. Mutating the completeness check
to use exit codes alone either drops a complete non-pass shard or merges an
incomplete one, and the focused tests catch both.

**Close when.** A fleet merges every complete shard regardless of whether its
verdict passes, skips merge on an incomplete shard with a durable reason,
distinguishes the merge phase in status, and the registered suite plus
user-facing docs pass on the same final commit.

## NC-52 — supervision logic should move out of the netcup script

**Status:** OPEN (filed 2026-11-12, design only, rev 5); Should-fix. Design
record:
[`run-gate-project/docs/DESIGN-compute-hosts-and-campaigns-2026-11.md`](../../run-gate-project/docs/DESIGN-compute-hosts-and-campaigns-2026-11.md)
(Run-Gate RG-275). Cross-references: NC-42, NC-48, NC-45.

**Problem.** Host installation is provider-specific: `install-host.py`, the
SCP API client, debian-install-v2 and `pre-reinstall*.py`. Two runtime pieces
are provider-coupled as well:

- the per-host `api_check` (a provider API identity check, required by
  `runner/remote-gate.py:196-198` and called by `host-sat-queue.sh`);
- the hard `ALLOWED_HOSTS` allowlist (`remote-gate.py:30`).

The rest is generic SSH + Docker + Run-Gate code that happens to sit under
`scripts/netcup/runner/`:

- `runner/run-r2-campaign.sh` (the attempt loop, stall counter, fleet of shard
  children, MemAvailable guard, merge and stop/status);
- `remote-gate.py`'s transport;
- `host-sat-queue.sh`.

In particular, the supervisor's fleet and memory-guard decisions (instances,
per-instance memory, overcommit; NC-48 is the interim per-host fix) belong to
the central scheduler service that knows every host. A per-host bash script
configured by environment variables cannot make them.

**Minimal solution.** Follow the design's migration (§5).
`run-r2-campaign.sh` becomes a thin wrapper around
`run-gate campaign supervise` with the same arguments and the same lock,
active-record and campaign-directory layout, so legacy and new supervisors
exclude each other on one host. The fleet options print the equivalent
`run-gate campaign submit` to the central scheduler service (design WP4). After
one release the wrapper is deprecated.

The fleet and memory-guard decisions move to the service. The service enrolls
its own per-host keys with `ciu host enroll` (run as its user). Existing
controller keys are never copied: they are revoked once remote-gate and the
queue submit through the service. The provider identity check stays a
provisioning step, and the allowlist is replaced by explicit enrollment plus a
refusal of `HOST_ROLE=shared-prod`. Once the service dispatches campaigns
(design WP5, WP12), the generic runner scripts move to a provider-neutral
home, and netcup keeps installation only. Active campaigns finish under the
script that started them; runner scripts are not replaced on a host while its
campaign is active.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh`, `runner/README.md`,
`scripts/netcup/tests/test_run_r2_campaign.py` (whose cases become Run-Gate
parity tests), and `scripts/netcup/README.md`.

**Close when.** The wrapper delegates to `run-gate campaign supervise`, the
parity tests pass in Run-Gate, a lock-interop test proves that legacy and new
supervisors refuse each other's campaign base, and the netcup suite passes on
the final head.

## NC-53 — Run-Gate rollout leaves runner scripts and selected profile unverified

**Status:** OPEN (filed 2026-11-12); Should-fix. The 2026-11-12 Run-Gate
25.6.0 image rollout rebuilt and deployed tester-base/tester-unified, but its
report records that runner scripts on the compute hosts still came from an
older repository revision and that one private `run_gate_bins` profile may
still select Run-Gate 25.4.0. Related: NC-39 tracks general runner-component
provenance and reconciliation; NC-23 tracks earlier per-host Run-Gate pin
decisions.

**Problem.** Updating the tester images and Run-Gate venv does not update the
separately installed runner scripts or prove which Run-Gate binary the
controller profile selects. After an image/runtime rollout, older
`run-r2-campaign.sh` and `build-tester-base.sh` copies can remain active, and
a private profile can continue selecting an older versioned venv. A campaign
can therefore combine the new image with stale orchestration code or an old
Run-Gate selection.

**Evidence.** The 2026-11-12 image rollout report records that the host runner
scripts were older than the current repository versions and that a private
`run_gate_bins` entry may still pin 25.4.0. The runner guide says the runner
directory is uploaded together (`scripts/netcup/runner/README.md:8-11`) and
documents the checkout synchronization flow
(`scripts/netcup/runner/README.md:283`). The controller profile parser accepts
an explicit `run_gate_bins` list and passes it to the remote lane
(`scripts/netcup/runner/remote-gate.py:86,134-138,682`); the guide documents
that profile setting (`scripts/netcup/runner/README.md:600-601`).

**Minimal solution.** Make the rollout procedure synchronize the runner
scripts from the selected repository revision after the image/runtime update,
while respecting the rule that active campaigns finish under their starting
script. Record the deployed source revision or hashes. As part of the same
rollout check, inspect the active controller profile's `run_gate_bins` and
confirm it selects the intended 25.6.0 Run-Gate binary. Keep general component
provenance under NC-39 and the earlier pin policy under NC-23.

**Where.** Runner rollout documentation and synchronization/check tooling;
controller profile validation for `run_gate_bins`; focused runner tests and
the registered Netcup suite.

**Acceptance test idea.** A fixture with an older deployed runner script is
detected until synchronization records the selected source revision and the
deployed scripts match it. A profile fixture selecting 25.4.0 is reported as
stale for a 25.6.0 rollout, while a profile resolving to the intended 25.6.0
binary passes. Verification must not replace runner scripts during an active
campaign.

**Close when.** The rollout procedure synchronizes and records the runner
script revision after image/runtime changes without interrupting an active
campaign; the registered suite detects stale scripts and a 25.4.0 profile and
accepts scripts plus a profile verified for 25.6.0; and the rollout check
records successful verification of the deployed scripts and selected
`run_gate_bins` on each compute host.

## NC-54 — release-program paths in reinstall and remote-gate helpers

**Status:** OPEN (filed 2026-11-13); documents the exact purge dependency.

**Limitation.** `pre-reinstall.py` and `pre-reinstall-export.py` set a fixed
release-program root and read `dstdns-r2/env.sh` beneath it. They cannot use a
different program root today. `runner/remote-gate.py` has a default output
directory under the release-program root, but supports `REMOTE_GATE_OUT` and
`--out-dir`; callers can use it without that default path by setting an
override. The fixed reinstall helpers must be made configuration-driven or
retained with their required helper before the old program directory can be
purged.

**Where.** `scripts/netcup/pre-reinstall.py`,
`scripts/netcup/pre-reinstall-export.py`, and
`scripts/netcup/runner/remote-gate.py`.

**Close when.** Both reinstall helpers accept a controller-home setting and a
fake-root check proves that they work from an alternate home; the remote-gate
override remains documented and its default is no longer required.

## NC-55 — deterministic budget-exceeded candidate is reported as stalled

**Status:** OPEN (filed 2026-11-13).

The campaign supervisor stops with exit 4 and reports “stalled” when the only
remaining candidate has a deterministic `budget_exceeded` result. That
candidate is a completed judgment, not evidence that candidate progress has
stalled; the supervisor loses the distinction at the end of the campaign.

**Where.** `scripts/netcup/runner/run-r2-campaign.sh` and its supervisor-state
and exit-status tests.

**Acceptance.** A deterministic fixture with one remaining `budget_exceeded`
candidate finishes with that outcome reported and does not take the stalled
exit-4 path. A genuinely non-progressing campaign still reports stalled with
exit 4, and the saved candidate evidence is retained in both cases.

## NC-56 — T3 bundle import can omit release tags needed to derive the required Assay version

**Status:** OPEN (filed 2026-11-14); first observed on both test-host campaign launches.

**Problem.** `sync-from-devcontainer` clones a bundle with `--single-branch`. If the bundle contains only the branch and a small subset of tag refs, the imported checkout lacks the `assay-v*` tags used by setuptools-scm. The CMRU R2 launch required Assay 12.2.0 or newer but derived `0.1.1.dev12052` from the tag-incomplete checkout and refused in four seconds on both hosts. The version guard was correct and remained enabled; the wasted launch came from the sync recipe not carrying all tags.

**Needed.** Bundle the reviewed branch together with all release tags, and fetch `refs/tags/*` from that bundle into the imported checkout. Add a preflight with a clear missing-tags diagnosis before a campaign is launched. Do not weaken the Assay version floor.

**Where.** `scripts/netcup/runner/sync-from-devcontainer`, its focused tests, and the P4 import instructions in `scripts/netcup/runner/README.md`. The Run-Gate project also documents this import at `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` §3.6; keep that consumer guidance synchronized when this entry is implemented. This issue is filed here because the netcup project owns the sync script.

**Close when.** A branch-plus-all-tags bundle imports with the required release tags present, an incomplete bundle is diagnosed before the lane starts, and the runner README and the referenced Run-Gate consumer instructions describe the same tag-complete recipe.

## NC-57 — fleetd had no host-install script, and the docs prescribed a system-Python pip install

**Status:** FIXED (filed and implemented 2026-11-15, `pi-fleetd-followups`); live
host acceptance remains a host-board step (no host was touched).

**Problem.** `fleetd` made its wheel depend on `cli-extended`, so a managed host
needs two wheels. The docs described that install but no runner script existed,
and both `fleetd/README.md` and `fleetd/docs/SPEC.md` told an operator to run
`python3 -m pip install` — which Debian 13 refuses for the system Python
(PEP 668). There was also no idempotent way to tell whether a host already had
the exact reviewed wheel.

**Fix.** `runner/install-fleetd.sh` verifies both wheel SHA-256 inputs before
writing, installs offline (`--no-index --no-deps`) into
`/opt/fleetd/<version>/venv`, links `/usr/local/bin/fleet`, is idempotent for
the same hashes and reinstalls when a hash changes (`--check` verifies the
inputs and reports current/not-current with exit 0/1 without writing). It
refuses a symlinked install path, an incomplete existing venv, and a
non-symlink file at the command path; it never uses the system Python and
touches nothing outside its prefix. It mirrors the accepted
`install-augeaswrap.sh` shape, including `lib.sh` + `identity_check`.

**Where.** `scripts/netcup/runner/install-fleetd.sh`,
`scripts/netcup/tests/test_install_fleetd.py` (hermetic: fake wheels, fake venv,
temp prefix only), `scripts/netcup/runner/README.md`, `scripts/netcup/CONSUMERS.md`,
and the three fleetd documents that carry the install steps.

## NC-58 — remote-gate never emits the `run-gate.commit=` line that its own evidence binding relies on

**Status:** OPEN (filed 2026-11-16); Should-fix. Source: the remote gate
worker report of 2026-11-16 (`sonnet-v1001-gateworker-REPORT.md`, section
"Findings for tool backlogs (NOT fixed locally)"; the pre-release gate
report of the same day names the same symptom in its counter row).

**Problem.** The only mechanical binding between a collected run and the
commit it judged is the `run-gate.commit=<full sha>` label run-gate puts on
the container it starts. `remote-gate.py` neither writes that line into
`result.json` nor into a collected file: `collect()` builds
`{"lane", "verdict", "exit_code", "reason"}` and nothing else
(`scripts/netcup/runner/remote-gate.py:915-918`). A reader of a collected run
therefore has the commit only when the lane's own log happens to carry it,
which depends on the lane shape:

- a container lane leaves the docker-argv label line in the lane log, so the
binding is recoverable;
- a native/assay lane that renders no such argv line, or a lane whose log the
collection truncated to a tail with no arguments of its own,
leaves no binding at all.

The practical consequence was measured live: the out-of-repo controller
wrapper had to re-derive the binding by relocating (copying aside) the host
log so it could extract the `checkout ... at <sha>` line, and then emitted its
own `remote-gate: bound run-gate.commit=<sha>` line — i.e. the evidence
authority lived in a tool outside this repository and would silently disappear
with it. The host already knows the answer — the job script asserts `git
rev-parse HEAD == SHA` and prints `checkout <dir> at <sha>`
(`remote-gate.py:553-556`) — so this is a disclosure gap, not a missing fact.

**Minimal solution.** Emit the binding where the fact is already certain: the
job script writes the resolved `HEAD` into a small committed-to-run file (or
`remote-gate.py` renders `SHA` into the run directory from `meta.json`, which
already carries `sha`), and `collect()` adds `commit` to `result.json` plus one
`run-gate.commit=<full sha>` line to the collected log. Keep it a
DISCLOSURE of the host-asserted value, never a claim: the line must be written
only after the host's HEAD assertion passed, and it must name the assertion it
rests on (for example `run-gate.commit=<sha> (host HEAD asserted)`), so a
missing assertion cannot masquerade as a verified binding. Absent, wrong-shape
or contradictory values must be reported by name instead of omitted.

**Where.** `scripts/netcup/runner/remote-gate.py` (`render_job`, `collect`),
`scripts/netcup/runner/tests/test_remote_gate.py`, and the runner guide's
remote-gate section (`scripts/netcup/runner/README.md`) plus
`scripts/netcup/CONSUMERS.md` where a collected run is described.

**Acceptance test idea.** A fake host job that asserts HEAD writes the binding
into `result.json` and into the collected log; a fake job whose assertion is
absent writes NO binding and says so; a job whose HEAD assertion failed still
produces a refused run rather than a binding. A lane log with no docker-argv
line at all still collects the same binding, and two runs of the same lane at
two different commits never share a value.

**Priority.** Should-fix. Release and merge evidence is bound to a commit by
this line; a run whose commit can only be recovered from a host log outside
the repository is not reviewable evidence (see the false-certification rule on
narrower checks).

**Close when.** A collected run of a non-container lane names its
host-asserted commit in `result.json` and in the collected log, the value has
its assertion disclosed beside it, and the fake-host cases above pass on a
clean final commit.

## NC-59 — provisioning installs no slice limits, so the gates tier's resource policy is hand-maintained per host

**Status:** FIXED in repository 2026-11-16 (`provision-gates-limits.sh`
reproduces the measured policy from the host's own facts, `provision-runner.sh`
runs it, and `remote-gate.py` admits lanes against live memory); live fresh-host
acceptance is NC-62. Source: the remote gate worker report of 2026-11-16
(`sonnet-v1001-gateworker-REPORT.md`, "Findings for tool backlogs", the
provisioning row) and the 2026-11-16 host slice-limit decision recorded by the
controller. Related: NC-25 (the marker-unit template install and its
2 GiB-reserve derivation) and NC-30 (cgprofile/Run-Gate compatibility).

**Problem.** `provision-runner.sh` prepares the runner account, the CoW
volume and the local job helpers, and it does not touch the gates tier at all
(`scripts/netcup/runner/provision-runner.sh`, 165 lines, contains no
`systemctl`, no unit fragment and no `dev-gates.slice` reference). A host
reinstalled without mdt host-setup therefore gets whatever slice state the
installer left, and the whole gates budget arrives by hand: on the observed
host the missing CPU/memory controls were added as a local
`/etc/systemd/system/dev-gates.slice.d/10-remote-gate-limits.conf` drop-in
(`CPUQuota=300%`, `MemoryHigh=5G`, `MemoryMax=6G`, `TasksMax=8192`). Nothing in
the repository records that drop-in, so a second host, or the same host after
another reinstall, reproduces the same manual step and silently loses it.

The values also encode a policy that has since been decided the other way. The
2026-11-16 decision for the gates tier is: no `MemoryHigh`; `MemoryMax = RAM
minus the control-plane floors minus a small slack`; deliberate swap
overcommit with paging by design; `memory.oom.group` on the slice and a
negative `OOMScoreAdjust` for the control-plane units so any kill stays inside
the slice; admission then budgets against live memory pressure rather than a
static high-water mark. The hand drop-in (a fixed `MemoryHigh=5G` with a hard
`MemoryMax=6G`) is the previous shape, so the one host that has limits has
them in the form the decision retires, and no scripts document the transition.

**Minimal solution.** Give provisioning an explicit, idempotent slice step for
a host without host-setup, in the NC-25 shape: ship the unit template under
`scripts/netcup/runner/`, DERIVE the values from the host's own `MemTotal`
(never a hard-coded `6G`), refuse to overwrite a unit a host-setup owns, and
render the current policy (no `MemoryHigh`; `MemoryMax` from RAM minus the
control-plane floors, with the floors read from the host rather than assumed;
`memory.oom.group`; the CPU quota as a host-owned input with its source named).
Record in the runner guide which numbers are derived, which are host policy
the provisioning does not invent, and how an already-provisioned host converges
(single documented re-run, no hand editing).

**Where.** `scripts/netcup/runner/provision-runner.sh` and a new unit template
beside it; `scripts/netcup/runner/README.md` (the marker-unit and slice sections);
`scripts/netcup/tests/test_runner_scripts.py` if the existing argv/literal
checks can carry the rendering cases.

**Acceptance test idea.** A hermetic render test: two fake host facts (a small
and a large `MemTotal`) produce two different `MemoryMax` values and no
`MemoryHigh`; a pre-existing host-setup-owned fragment makes the step refuse by
name; a host whose control-plane floors are unreadable refuses rather than
defaulting to a number. A live acceptance on a freshly reinstalled host without
host-setup shows the rendered slice properties from `systemctl show` and the
same values derived twice (idempotence).

**Priority.** Should-fix. Until this lands, every reinstall of a
host-setup-less host costs a hand step whose absence is invisible until a lane
thrashes or is killed, and the estate keeps two contradictory gates-tier
policies.

**Implementation (2026-11-16).** The step is now a script, not a note:

- `runner/provision-gates-limits.sh` renders drop-ins only (no unit file is ever
  edited) and `provision-runner.sh` runs it as part of P3. From the host's own
  `MemTotal`/`SwapTotal` it derives `MemoryMax = MemTotal - 1024 MiB control-plane
  floor - 256 MiB slack` and `MemorySwapMax = SwapTotal - 8192 MiB host reserve`,
  renders `MemoryHigh=infinity` (the fragment's own mark is overridden), gives every
  transient `docker-<id>.scope` `OOMPolicy=kill` (per-container
  `memory.oom.group=1`, deliberately not on the slice), and writes
  `OOMScoreAdjust=-900` for `ssh.service`, `systemd-logind.service`,
  `systemd-journald.service`, `docker.service` and `cgprofiled.service`.
- The four refusals the item asked for exist and are tested: unreadable host facts
  (exit 3, by name — no invented number, and a host whose swap is not above its
  reserve refuses instead of pretending paging is by design), an override that does
  not leave `MemoryMax` below `MemTotal` (exit 3), a foreign drop-in that sets one of
  the owned memory keys (exit 5, naming the file; CPU-only drop-ins are tolerated so
  the CPU quota stays host-owned), and a slice restart while the tier has tasks
  (exit 4). The restart happens only when the live cgroup values disagree, because
  `daemon-reload` alone does not re-apply limits to an active slice and a restart
  resets the `memory.events` counters remote-gate's `INCONCLUSIVE-OOM` reads.
- Convergence without a restart: the live OOM scores are written through
  `/proc/<MainPID>/oom_score_adj`, so ssh, logind, journald and docker keep running;
  a second run with matching values rewrites nothing and reloads nothing.
- The ceiling is derived from the host's NOMINAL size: each total is rounded UP to
  `GATES_NOMINAL_STEP_MIB` (default 1024 MiB) before the reserve is subtracted, so
  an 8 GiB host whose kernel reports 7935 MiB derives `6656M` and `57344M`
  (`NC-62` records how the raw-kB form came out one MiB low). The rule is exercised
  on 4, 8 and 16 GiB fixtures, with the step and the reserve as killed mutants.
- A host an operator configured by hand is migrated once and explicitly:
  `--adopt` (or `GATES_MEMORY_ADOPT=1` through provisioning) recognises a file only
  at this tool's own path, only when every section and directive belongs to the
  rendered policy and the value is the one this host derives; its content is copied
  to `<file>.retired-<UTC stamp>` and never deleted. Without the flag it is refused
  with a message naming `--adopt`, so a re-run on the measured host cannot silently
  change the policy.
- A loaded-but-INACTIVE slice (a fresh host) is a no-op (`exit 0`): no live limit to
  compare, no counter to reset; an unreadable process list is never an idle slice
  (`exit 3`), because the only thing it gates is the restart that resets counters.
- Live admission replaced the static high-water budget: `remote-gate.py` takes
  `--mem-need-mb` (the lane's expected peak, passed by the controller's weight),
  `--mem-floor-mb` (default 1024) and `--wait-mem` (default 900), caps the expected
  peak at `MemTotal - 2 x floor` so one oversized lane always runs alone and pages,
  serialises callers through one host-wide ticket, and refuses (exit 75, verdict
  `MEM_REFUSED`) when meminfo cannot be read. The default `--mem-need-mb 0` declares
  no peak and keeps the previous behaviour exactly (no ticket, no floor, no wait),
  because the controller wrapper does not pass a peak yet; the verdict is decided
  from the job log's refusal LINE, so an admitted lane that failed later is not
  mislabelled.
- The cockpit launcher passes `--oom-score-adj` (`COCKPIT_OOM_SCORE_ADJ`, default
  `-500`), which was the one control-plane member the measurement found unprotected
  (NC-63 tracks the breadth of the docker-scope drop-in).
- Tests: `tests/test_provision_gates_limits.py` (three RAM sizes including the
  measured v1001 fixture, idempotence, idle-only restart, an unreadable process list,
  every refusal code, dry-run, in-place scores, foreign/agreeing drop-ins, the
  `--adopt` migration, the provisioning exit contract) and
  `tests/test_remote_gate_memory.py` (the job's own `mem_admit_ok` against meminfo
  fixtures, the inclusive floor boundary, the larger-than-RAM cap, unreadable facts,
  the real `flock` ticket, the no-peak default, the `MEM_REFUSED` verdict and its two
  negative cases).

**Close when.** A freshly reinstalled host without host-setup has the gates
slice properties from provisioning alone, derived from that host's own facts and
in the decided policy shape, and a second run changes nothing. Repository side is
done; the live half is NC-62.

## NC-60 — provisioning installs no pinned image set, so an SQL-qualification lane cannot run on a fresh host

**Status:** OPEN (filed 2026-11-16); Should-fix. Source: the pre-release gate
report of 2026-11-16 (`sonnet-v1001-prerelease-gates-REPORT.md`, finding F1 and
the host-change section) and the remote gate worker report of the same day.

**Problem.** Assay's SQL qualification checks the pinned reference with
`docker image inspect` and REFUSES to pull it (the SQL runner then uses
`--pull=never --network none`), so the image must already be on the host. The
reference is immutable and lives in `assay/gate/python/qualify_sql.py`:
`postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2`.
On the observed host it was absent, and the full `tester-unified` lane ended
`ERROR; exit_code 2` with `ASSAY_SQL_INCONCLUSIVE=image absent: docker pull ...`
after 19m42s; the pull was done by hand on the host and the re-run passed in
23m10s. Nothing in provisioning, in the runner guide's ordered bring-up, or in
the remote-gate host profile's `required_images`
(`scripts/netcup/runner/remote-gate.py:86,139-142`; the profile is a
out-of-repo hosts file) names that reference, so the same 20-minute waste recurs
on every fresh or reinstalled host, and the failure is only discovered at the
end of a full lane.

The related measurement in the same report is worth keeping: the controller's
own `postgres:18-alpine` tag resolved to a DIFFERENT digest
(`10a5c69a...`), so an operator who pulls by tag will appear to have the image
present while `docker image inspect` on the pinned digest still fails.

**Minimal solution.** Provisioning installs the digest-pinned image set the
repository's own lanes need — at minimum this exact reference, taken from the
one source of truth rather than retyped — either by pulling it or by loading a
saved archive, and verifying it with `docker image inspect` on the immutable
reference afterwards. The set must be DERIVED from the repository (one
constant, or a generated list per project) and never a hand-maintained list in
a private hosts file; a missing image then fails at provisioning time with the
pinned reference in the message, not at the end of a 20-minute lane.

**Where.** `scripts/netcup/runner/provision-runner.sh` (or a small helper it
calls), `scripts/netcup/runner/README.md` step 2 (which today documents the
pull as an operator action), `scripts/netcup/tests/test_runner_scripts.py`, and
`assay/gate/python/qualify_sql.py` as the constant's owner (read only; no
change there).

**Acceptance test idea.** A fake `docker` records the exact `pull`/`load` argv
and the following `image inspect` argv, and the test asserts the reference
string equals the constant assay uses (a single-source assertion, so a digest
bump in assay cannot leave provisioning behind). A `inspect` that fails after
the pull refuses the provisioning step by name. A host already holding the
reference performs no pull.

**Priority.** Should-fix. The failure is environmental and loud but late, it
costs a full lane, and the fix is one derived constant.

**Close when.** Provisioning a fresh host ends with the pinned reference
inspectable, the runner guide's step no longer instructs a manual pull, and a
digest bump in assay breaks the provisioning test rather than a lane.

## NC-61 — the runner guide's fresh-host replay is unverified and its checked-in facts are not bound to a host state

**Status:** OPEN (filed 2026-11-16); Minor, documentation-integrity. Source: the
remote gate worker report of 2026-11-16
(`sonnet-v1001-gateworker-REPORT.md`, "What already existed" and the RUNBOOK row)
and the same day's pre-release gate report.

**Problem.** Two separate staleness facts, both already observed:

1. **The guide's own replay is not closed.** After the ordered bring-up the
guide states that a complete fresh-host replay using only checked-in files has
not been performed, and it carries five `to be confirmed` markers — the exact
unit-file wording and installation transcript, the portable `MemoryMax`
derivation, two standalone invocation results, and the Run-Gate wheel hash
(`scripts/netcup/runner/README.md:405,437,494,624,765`). A guide whose
replay was never completed is the shape a reader trusts and cannot execute.
2. **The host-side runbook is not bound to the host state.** The host runbook
(kept outside the repository) is updated in sections as facts change — on the
observed host the 2026-11-08 reinstall had already invalidated its pin-file
name, disk layout, swap size and cgprofile deployment, and the correction
landed only as an appended section rather than a re-verified document. Because
nothing in the repository binds the runbook to a measured host state, the same
drift is undetectable from the repository side.

**Minimal solution.** Close the guide's replay or state per step what is still
unverified and why (a single verified-fresh-host date in the guide, with the
steps that were actually run and the ones that were not); and add a short
"host fact re-verification" checklist to the bring-up whose items are
mechanical reads (release/pin identity, disk layout, swap size, installed
daemon version/contracts, image ids) so a reinstall forces those lines to be
re-measured rather than remembered. The checklist must not encode a host's own
values, only what to READ, so no host-specific fact enters the repository.

**Where.** `scripts/netcup/runner/README.md` (bring-up order, the `to be
confirmed` markers, and a new re-verification checklist),
`scripts/netcup/CONSUMERS.md` if it repeats the order, and
`scripts/netcup/tests/test_runner_scripts.py` for a check that the guide's
markers are either resolved or explicitly listed as unverified.

**Acceptance test idea.** A docs test fails when a `to be confirmed` marker
appears without an entry in the guide's unverified list, and when the guide
claims a completed fresh-host replay without a recorded date and the steps it
covered. The checklist items are read-only commands whose output a reader
compares, and the test asserts none of them carries a host-specific value.

**Priority.** Minor; it costs a reader's trust and one reinstall's diagnostics,
not a lane. Raise it if a second host is ever brought up from this guide.

**Close when.** The guide either records a completed fresh-host replay with its
date and covered steps or lists what remains unverified, the five markers are
resolved or listed, and the re-verification checklist exists with no
host-specific value in it.

## NC-62 — the rendered gates memory policy has no live fresh-host acceptance

**Status:** LIVE HALF CLOSED for v1001 on 2026-11-16 (the host-configured
adoption half); r1002 and any future fresh host remain OPEN. Should-fix for the
second and every later host. Source: this repository's NC-59 implementation
(`provision-gates-limits.sh`, 2026-11-16) and the measured policy report the tool
reproduces (`sonnet-v1001-slicelimits-REPORT.md`).

**Acceptance recorded on v1001, 2026-11-16** (host agent run from a `git archive`
of `origin/main` `f5209b7cd`, script sha256 identical on host and controller; no
other host touched):

| Check | Observed |
| --- | --- |
| Derived policy (dry run) | `MemoryHigh=infinity MemoryMax=6656M MemorySwapMax=57344M OOMScoreAdjust=-900` from `MemTotal` 7935 MiB (nominal 8192) / `SwapTotal` 65535 MiB (nominal 65536) |
| Live values equalled the hand policy | `systemctl show`: MemoryMax 6979321856 (6656 MiB), MemorySwapMax 60129542144 (57344 MiB, the hand file's `56G`), MemoryHigh infinity; cgroup files the same; CPUQuota/TasksMax untouched; the hand `10-remote-gate-limits.conf` still tolerated |
| Plain `--dry-run` on the unmarked hand host | rc 5, refused with a message naming `--adopt` |
| `--adopt --dry-run` | rc 0, listed all seven recognised hand files, the `daemon-reload`, and “live limits already match”; sha256 of every drop-in and of the slice `memory.events` unchanged by either dry run |
| `--adopt` | rc 0; seven files retired to `<file>.retired-20261116T133214Z` (content kept, never deleted), owned files carry the marker, `daemon-reload` only, `ssh` MainPID score set in place, NO slice restart |
| Idempotence | re-run without `--adopt`: rc 0, `unchanged` for all seven, live limits already match, sha256 of the whole `*.d` tree identical |
| No slice restart | `memory.events` (`low 0 high 0 max 16162 oom 0 oom_kill 0 oom_group_kill 0`) and the slice `InvocationID`/`ActiveEnter` identical before the adopt, after it and after a real lane |
| `memory.oom.group` probe | a container started with `--cgroup-parent dev-gates.slice` read `memory.oom.group=1`, `OOMPolicy=kill`, removed by exact name (no container left) |
| Real lane afterwards | `lint` lane PASS in 54 s, `run-gate.commit=f5209b7cd9a805cc96a1d9cbfaaf037b258ef7b9`, no `rg-rgw*` container and no `.worktrees` entry left |

The run also FOUND three repository defects, all closed in this package: the
script lowered sshd's live listener score (`NC-64`), the guide's probe needed an
`alpine` image the host did not have (it now uses `tester-base:local` with
`--pull=never`), and the fragment/foreign-file messages printed or stopped
in the wrong place (both now list or name exactly what differs).

**Remainder (OPEN):** r1002 carries the same rendered policy, and it is the host
with the closest thing to a fresh acceptance: it is BLOCKED on its iogovmon
write-mode acceptance window, which must not be disturbed by a host change
before **2026-11-17T14:38Z** (the +48 h check of the 2026-11-15 installation). A
slice restart or a provisioner run during that window is exactly the
host-exclusive operation the window forbids, so the r1002 adoption waits for it.
The genuinely fresh half (a host that has never had the policy applied by hand)
is also still unproven: v1001 was ADOPTED, not freshly provisioned.

**Problem.** The policy values were MEASURED by hand on one host (`MemoryMax=6656M`
on an 8 GiB box, `MemorySwapMax=56G`, `OOMPolicy=kill` on docker scopes,
`OOMScoreAdjust=-900` on five units) and the repository now derives the same shape
from `MemTotal`/`SwapTotal`. Everything about the derivation is proven against
fakes: three RAM sizes produce three different ceilings, the live restart is refused
while the tier has tasks, an unreadable fact refuses by name, a foreign drop-in is
refused, a hand-configured host is adopted only on request, and a second run changes
nothing. What is NOT proven is that the rendered drop-ins and the one idle restart
produce the measured cgroup state on a real reinstalled host — the fake
`systemctl`/`cgroupfs` cannot show that systemd accepts the drop-in syntax in
`/etc/systemd/system/docker-.scope.d/`, that a prefix drop-in really reaches a
transient `docker-<id>.scope`, or that a slice restart applies `MemorySwapMax` on
that kernel. In other words: "the tool renders the policy" is certified; "a fresh
host comes up with the policy" is not.

**Measured-host adoption step (review round 1, 2026-11-16).** On the already
hand-configured host the tool must be a NO-OP after one explicit adoption, not a
one-MiB change that restarts the slice and resets `memory.events`. The one-time step
is `sudo ~/runner-scripts/provision-gates-limits.sh --adopt --dry-run` (review the
list), then `--adopt`, then a plain run that reports every drop-in unchanged and
runs neither `daemon-reload` nor `restart`. The hand content is kept as
`20-memory-policy.conf.retired-<stamp>`. The same finding that round produced: the
raw-kB derivation rendered `6655M` (and `57343M` of swap) for this host instead of
the measured `6656M`/`57344M`, because `MemTotal` reads 7935 MiB for an 8 GiB box —
the nominal-size rule (`GATES_NOMINAL_STEP_MIB`) now reproduces both measured
values exactly, and the acceptance run must show those exact numbers.

**Minimal solution.** One acceptance run on a freshly reinstalled host, no manual
edits, comparing the rendered values against the live ones and against the
measured reference: `systemctl show <slice> -p MemoryHigh,MemoryMax,MemorySwapMax`,
`cat /sys/fs/cgroup/.../memory.{high,max,swap.max}`, `ps -eo pid,comm,oom_score_adj`
for the five units, a started lane container's `memory.oom.group` reading `1` (probe
started with `--cgroup-parent <slice>`, read from the host's own cgroup files, never
through a `--cgroupns=host` container), and one second run of the script that changes
nothing (`systemctl` argv shows no `daemon-reload` and no `restart`). On the host that
was configured by hand, the same run verifies the adoption step above (one `--adopt`,
then a no-op). Record the host's `MemTotal`/`SwapTotal` and the derived numbers in
the acceptance record (not in the repository: they are host facts).

**Where.** No code expected; `scripts/netcup/runner/README.md` (the acceptance
subsection next to the policy section, marked with the run's date) and this file.

**Acceptance test idea.** A live host run whose evidence lists each of the four
checks above with its observed value, plus the second run's no-op transcript. A
mismatch is a repository defect (the derivation) or a host defect (unit not
loaded), and the record must say which.

**Priority.** Should-fix: until it runs, the estate has one hand-configured host
and one host that has never had the policy applied by the tool.

**Close when.** A freshly reinstalled host, provisioned by the repository files
alone, shows the measured policy shape in the live cgroup, a lane container with
`memory.oom.group=1`, `-900` on the five units, and a second run that changes
nothing; the evidence is linked from the runner guide.

## NC-63 — the docker-scope `OOMPolicy=kill` drop-in covers every container on the host, not only lane containers

**Status:** OPEN (filed 2026-11-16); Minor, by design for lane scopes; the
restriction is optional. Source: the measured policy report
(`sonnet-v1001-slicelimits-REPORT.md`, residual hazard 1, and this repository's
`provision-gates-limits.sh`).

**Problem.** `docker-.scope.d/20-oom-group.conf` is a PREFIX drop-in: it applies to
every transient `docker-<id>.scope` systemd creates for a container, so the cockpit,
a buildkitd scope and any long-running stack container of another project on the
same host all get `memory.oom.group=1`. That is exactly what lane containers need
(one OOM kills the lane's own process tree instead of an arbitrary process of it),
but for a container that is not disposable it converts a single process's OOM into
the death of the whole container. The measured host had a deliberate single-purpose
role, so the hazard was accepted there; a host that also runs a long-lived stack
inherits it silently.

**Minimal solution.** Either scope the group to lane containers only (a dedicated
scope prefix for lanes, if Docker's cgroup naming allows it, or an explicit
`--oom-kill-disable`/per-container setting where Run-Gate starts the container), or
state the hazard in the runner guide next to the drop-in (which it now does) and
refuse to apply the prefix drop-in when the host table declares a long-running
stack. Whichever is chosen, the per-container behaviour must stay: an OOM ends one
lane, never a sibling lane and never a whole tier.

**Where.** `scripts/netcup/runner/provision-gates-limits.sh`,
`scripts/netcup/runner/README.md` (policy section), and the tests in
`tests/test_provision_gates_limits.py`.

**Acceptance test idea.** A fake systemd root in which a forbidden/allowed
container scope set is applied: the drop-in is not installed (or says so) when a
long-running stack is declared, and a lane container still reads
`memory.oom.group=1`. Plus one live check on a host with a stack running that the
stack's container is not group-killed.

**Priority.** Minor on the current pure test hosts, higher as soon as a host runs
both lanes and a stack.

**Close when.** Either the drop-in is scoped to lane containers, or the runner
guide names the hazard and the provisioning refuses it where it would be unsafe.


## NC-64 — `provision-gates-limits.sh` LOWERED sshd's own OOM protection

**Status:** FIXED 2026-11-16 (live rule + persistent rule, tests and mutants in
the same change). Filed from the v1001 adoption report
(`sonnet-v1001-adopt-REPORT.md`, finding 1), which also had to restore the value
by hand. Severity Major: the tool silently weakened a host service's own
protection, and every later run repeated it.

**Problem.** `oom_score_adj` is a RANKING, not a magnitude: a LOWER value is MORE
protective (`-1000` strongest, `+1000` weakest). sshd sets its own listener to
`-1000` at runtime (it protects itself), while the unit carries the policy's
`-900` for the sessions it starts. The tool's live fix-up compared the current
value only for EQUALITY with the target, so `-1000` read as "not converged" and
was overwritten with `-900` — a protection downgrade on a running host service,
on the very host the policy exists to protect, and one a re-run would repeat. The
persistent half had the same defect in a different form: the tool rendered
`30-oom-protect.conf` with `OOMScoreAdjust=-900` for all five protected units
unconditionally. A drop-in is merged AFTER the unit file, so on a unit whose own
file sets a stronger value (`containerd.service` ships `-999`; a packaged
`ssh.service` may set `-1000`) the drop-in would REPLACE it with the weaker
`-900` at that unit's next start — invisible to `--dry-run`, to a diff and to
every cgroup read, because the drop-in file is the tool's own and the unit file
was never consulted.

**Minimal solution (as implemented).** One comparison in both halves, and the
rule is stated where an operator reads it:

- live: write `/proc/<MainPID>/oom_score_adj` only when the current value is
  HIGHER (less protective) than `-900`; an equal-or-lower value is left alone and
  reported ("already at least as protected"). An UNREADABLE value is exit 8, not
  `0`: the read is what gates the write.
- persistent: render the drop-in only for a unit whose OWN unit file sets nothing
  or something weaker. The own value is read from `systemctl show -p FragmentPath`
  and parsed as INI — never `systemctl show -p OOMScoreAdjust`, which already
  includes a drop-in this tool rendered on an earlier run and would make a second
  run believe the unit protects itself. A unit already at least as protected is
  reported and gets no drop-in (and, if this tool had rendered one earlier, that
  owned file is RETIRED to `<file>.retired-<UTC stamp>`, because it would otherwise
  keep winning; another owner's file that would lower the value is refused with
  exit 5, and one that is equal-or-more protective there is KEPT and reported). An
  unreadable or unusable own value is exit 3.
- the EFFECTIVE score (`systemctl show -p OOMScoreAdjust`) is read for one
  decision only: when the policy would be rendered, a vendor drop-in this tool
  cannot see (a packaged one under `/usr/lib/systemd/system/<unit>.d/`) may already
  protect the unit more strongly. An equal-or-more protective effective value means
  nothing is rendered (and the message names the directory to inspect); an empty or
  unusable one is exit 3, never an assumed `0`.
- a foreign drop-in that sets a MORE protective value is now kept and reported
  instead of refused as a contradiction (`more_protective` is used for the same
  comparison there).
- review round 1 found the blocking half of this rule itself broken: the helper that
  reads another owner's drop-in was DEFINED BELOW ITS FIRST CALL, so bash answered
  `command not found`, a `|| true` swallowed it, and every equal-or-more protective
  file at this tool's own path was REFUSED (exit 5) as unreadable. Every helper now
  lives above the main flow, the decision is recorded before any mutation
  (`plan_retire`/`plan_refusal`/`apply_retires`), and a test asserts that no function
  is used before its definition anywhere in the file.

No special case names sshd: the rule is about the VALUE and applies to every
protected unit, so `containerd`-style packaged scores and future units are covered
without a list to maintain.

**Tests.** `tests/test_provision_gates_limits.py`: `-1000`, `-999`, `-900`, `-899`,
`-500` and `0` live scores (only the less-protective values are written, and the
exact `current -> target` transition is in the output); an EQUAL value with a
read-only score file proves by consequence that no write is even attempted; the
`oom_score_number`/`usable_oom_score` boundaries (`-1000`/`1000` accepted,
`-1001`/`1001` refused) and the base-10 rule (`010`, `0900` decimal;
`0000000900` refused before arithmetic); unreadable live values (empty, garbage,
`+900`, `-2000`, `1001`) are exit 8 and leave the file byte for byte; a unit whose
own file sets `-1000`/`-999` gets no drop-in; an own file with nothing, `0` or
`-500` still gets the policy drop-in (unless a vendor drop-in the tool cannot see
already protects more, which is reported instead); an unreadable own file is exit
3 and writes nothing; another owner's drop-in at this tool's own path is judged by
its VALUE (equal-or-more protective kept and reported, weaker refused, unreadable
exit 3); a previously rendered owned drop-in is RETIRED (announced in `--dry-run`
without writing, and only after every pre-flight passed) and the adoption of a hand
`-900` file over a unit's own `-1000` retires it and renders nothing. Mutants that
were killed: a function defined below its first call (12 failures), flipping
`more_protective` to `<` (the equal-value tests fail, including the read-only-score
one), taking the effective `OOMScoreAdjust` instead of the fragment (the
earlier-drop-in case), dropping the readability gates, a plain `rm` instead of a
retirement, and a rival-file listing that exits at the first file.

**Where.** `scripts/netcup/runner/provision-gates-limits.sh`
(`oom_score_number`, `more_protective`, `usable_oom_score`, `own_oom_score`,
`effective_oom_score`, `apply_live_score`, `plan_retire`/`apply_retires`),
`runner/README.md` ("protection is never reduced"), `CONSUMERS.md`,
`DESIGN-GUIDE.md` (the rejected alternatives), `CHANGES.md`.

**Close when.** (met) the live and persistent comparisons are one rule, the
readability of both inputs is a hard error, and the runner guide names the
directional meaning of `oom_score_adj` so nobody reads `-1000 -> -900` as
"converged".

## NC-65 — `provision-gates-limits.sh` must yield to a host-setup `dev-gates.slice` fragment and stop listing ssh in the -900 set

**Status:** OPEN (filed 2026-11-24); Should-fix. Source: `mdt-cockpit/host-setup/docs/DESIGN-DEDICATED-DEV-MEMORY.md`
section 10 ("Cross-tool interplay"), whose placeholder `NC-nn` this entry resolves.

`runner/provision-gates-limits.sh` writes `dev-gates.slice.d/20-memory-policy.conf` (MemoryMax 6656M), `OOMPolicy=kill`
on docker-.scope and `OOMScoreAdjust=-900` on ssh.service. Follow-up: skip the slice and scope halves when host-setup's
`dev-gates.slice` fragment exists, and drop ssh from the -900 list (sessions inherit it). Sequence this item before
host-setup lands on any runner host; `check.sh` WARNs meanwhile (design section 7).

**Addendum OPEN 2026-11-24 (main ruling 12:20Z).** `provision-gates-limits.sh` must not re-render `dev-gates.slice.d/20-memory-policy.conf`
at all on a `HOST_ROLE=dedicated-dev` host (the role host-setup governs; `mdt-cockpit/host-setup/host-setup.env.example:448-449`). The
script writes it unconditionally (`slice_dropin`, `runner/provision-gates-limits.sh:478`, rendered at `:752`), so the next runner
provisioning brings the file back and with it `check.sh`'s tier `memory.max` FAIL: the drop-in's `MemoryMax=6656M` no longer
matches the dedicated-dev derivation (`mdt-cockpit/host-setup/scripts/check.sh:345-355`). Separately, `check.sh:417` WARNs on the file
itself, names the placeholder `NC-nn` (now NC-65), and says it becomes a FAIL once this follow-up lands. Fix: detect the role (the host-setup env's `HOST_ROLE`, or host-setup's own `dev-gates.slice` fragment) and, on dedicated-dev, skip
the slice file, retire an existing `20-memory-policy.conf` this script owns (marker line) through the existing `retire_paths` mechanism, and report that host-setup owns the policy.
This is the same change as the slice half above; the role test is its trigger. Close when a test renders on a dedicated-dev fixture and
no `20-memory-policy.conf` exists afterwards, and a shared host still gets it.

## NC-66 — remote-gate shipped the controller's local `main`; nothing created `origin/main` on the host

**Status:** FIXED by P-plat-24 (2026-11-24). Source: vbpub-rollback run `rgw-r2-20261124T144053-v1001`.

`runner/remote-gate.py` bundled `refs/heads/main` of the controller checkout and the job only set the host's local `main`. A lane with
`base = "origin/main"` failed in 4 s with GIT_FAILED (`git rev-parse origin/main^{commit}`: "Needed a single revision"), and `base = "main"`
lanes judged against a local main that was 36 commits behind origin/main. Fix: `git fetch -q origin main` in the controller checkout, bundle
`refs/remotes/origin/main`, create both `refs/remotes/origin/main` and `refs/heads/main` on the host; a failed fetch warns and uses the
existing origin/main; an assay lane whose declared `judge.base` does not resolve locally is refused with exit 2 (a host-side check of every
declared ref would need the base passed into the job; not done, so a declared base that names a branch other than main, or a non-release tag
(only HEAD, origin/main (set on the host as both `origin/main` and `main`), the `--base` commit and release tags `refs/tags/*-v[0-9]*` travel in the bundle; `remote-gate.py:422`, `:457`), passes the local check and still fails on the host).
`--base main` / `origin/main` now resolve to the fetched origin/main (not the stale local branch). A host checkout cloned from a seed starts with the
seed's own `main` and `origin/main`; the job overwrites them, and deletes both when there is no origin/main to set, so nothing stale survives (r1 review F1-F5).
**Where.** `runner/remote-gate.py` (`local_state`, `lane_checks`, `build_bundle`, job script), `CONSUMERS.md`, `CHANGES.md`.

## NC-67 — the runner records no container `State.OOMKilled` in result.json

**Status:** OPEN (filed 2026-11-24); Should-fix.

`job.sh` only inspects containers for cleanup, so an OOM kill inside a lane container leaves no field in `result.json`;
`scripts/controller/remote-gate.sh` can classify an OOM only from the slice delta. Proposal: the runner writes an `oom_killed` field
(from `docker inspect --format '{{.State.OOMKilled}}'` before the container is removed) that remote-gate.sh uses besides the slice delta.

## NC-68 — HAZARD: `verdict_of()` in remote-gate.py is unanchored and takes the last match

**Status:** OPEN (filed 2026-11-24); Must-fix (a lane can fake its own verdict).

`verdict_of()` (`runner/remote-gate.py`, `VERDICT_RE`) matches `lane '?X'? verdict Y` anywhere in the text and takes the last match, so a
lane's free output (test stderr quoting "lane X verdict Y") can fake the `result.json` verdict. Proposal: anchor it as
`scripts/controller/remote-gate.sh` does since P-plat-23 (q246): line start, exact lane.

## NC-69 — concurrent remote-gate runs from one controller checkout share the temporary ref `refs/remote-gate/base`

**Status:** OPEN (filed 2026-11-24); Should-fix. Pre-existing (not introduced by P-plat-24). Source: `.controller-state/platform/reviews/plat-24-r2-opus.md`, N5.

`runner/remote-gate.py` names the temporary bundle head for the `--base` commit with the constant `BASE_REF = "refs/remote-gate/base"` (`:433`;
used at `:458-470`, cleanup `:469-470`). Linked worktrees share every ref outside `refs/worktree/*`, so every run from one controller repository writes the same
ref: run A sets it to X, run B sets it to Y, A's `bundle create` ships Y, and A's host lacks X and ends rc 72 "base ... missing on the host"
(`JOB_REASON_RE`, `:1150`). Also A's `finally` deletes the ref while B still needs it. With `--base main` both runs now usually map to the same fetched
sha, so P-plat-24 did not make it worse, but any two runs with different bases collide. Fix: a per-run ref name, `refs/remote-gate/base-<run name>`,
used for the bundle head, the host-side `git fetch` of it (`:664-665` job script) and the cleanup. Close when a test creates two runs with different bases
and asserts each bundle carries its own base.

## NC-70 — `--base main~1` / `main^` still resolve against the stale local `main`; plat-24 nits N2-N4

**Status:** OPEN (filed 2026-11-24); Minor. Source: `.controller-state/platform/reviews/plat-24-r2-opus.md`, N1-N4 (N5 is NC-69, N6 was corrected in NC-66).

- **N1 (follow-up).** `MAIN_NAMES` (`runner/remote-gate.py:300`, checked at `:328`) is an exact-match tuple, so only `main`, `origin/main`,
  `refs/heads/main` and `refs/remotes/origin/main` map to the fetched origin/main. `--base main~1`, `main^` or `main@{1}` fall through to
  `rev-parse --verify` (`:331-336`) in the controller checkout and resolve against local `refs/heads/main`, which was 36 commits behind in the
  incident: the gate judges a base 37 commits back and nothing warns. The same holds for a declared `judge.base = "main~1"` (`:386-390`):
  the local check resolves it against the local branch, possibly absent (a false refusal), while the host resolves it against origin/main.
  Fix: when `base` matches `^(refs/heads/)?main([~^@].*)?$`, rewrite the `main` prefix to `refs/remotes/origin/main` before the `rev-parse`, for both
  `--base` and the declared check; or refuse it with rc 2. One parametrize case.
- **N2.** The 60 s fetch bound is untested (`FETCH_TIMEOUT`, `:301`, used `:320`): `Fake.run` in `tests/test_remote_gate.py` drops `timeout`
  (`:40-42`), so reverting to the 300 s default passes every test. Record `timeout` in `Fake.calls` (or a one-off recording runner) and assert
  `== rg.FETCH_TIMEOUT` in `tests/test_remote_gate_seams.py` (`:42-52`).
- **N3.** A seeded clone keeps a dangling `refs/remotes/origin/HEAD` after the job deletes `origin/main` (`:693-695` job script): lane
  stderr gets `warning: ignoring broken ref` and `origin` alone no longer resolves. Add
  `git -C "$CKDIR" symbolic-ref -d refs/remotes/origin/HEAD 2>/dev/null || true` to the same else branch.
- **N4.** The declared-base parse runs on every assay lane run, not only with `--base` (the `if base_given` guard moved inward, `:377-380`).
  An `assay.toml` whose `lanes` or `lanes.<x>.judge` is not a table (`judge = "x"`) raises an AttributeError traceback (rc 1) instead of
  "no opinion". Guard both with `isinstance(..., dict)` as the neighbouring `assay_cfg` / `imp` parse does.
- Optional: rename `test_local_state_records_the_local_main_sha` (`tests/test_remote_gate_seams.py:26`), which now describes origin/main.

## NC-71 — `remote-gate.py` status/logs/collect/cancel read the host table only from the default hosts file

**Status:** OPEN (filed 2026-11-24); Should-fix. Source: main controller request 2026-11-24 (platform STATE.md, Queued): a losing duplicate gate could not be cancelled.

The `--hosts-file` option defaults to `$REMOTE_GATE_HOSTS` or `~/.config/remote-gate/hosts.json` (`runner/remote-gate.py:38`, `:1323`) and the
verbs `status`, `logs`, `collect` and `cancel` load it at `:1363`. Launches through `scripts/controller/remote-gate.sh` resolve their own table
(`CONTROLLER_REMOTE_HOSTS_FILE` or `CONTROLLER_HOSTS_FILE`, `remote-gate.sh:102-104`) and pass it as `--hosts-file` (`:259`). A `remote-gate.py cancel <id>`
without `--hosts-file` therefore fails to find the host, so a duplicate or losing gate cannot be cancelled without pid-signalling the process,
which this estate forbids. Fix: these verbs resolve the host table the way launch does (the same CONTROLLER_* variables), or `remote-gate.sh`
grows `status`/`logs`/`collect`/`cancel` verbs that pass the table and the out-dir. Workaround: pass the global options before the verb,
`remote-gate.py --hosts-file <table> --out-dir <dir> cancel <id>`, or export `REMOTE_GATE_HOSTS`. Close when a test launches with a non-default table
and cancels the run without naming the table again.
