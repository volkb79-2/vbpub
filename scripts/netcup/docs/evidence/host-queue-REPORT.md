<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-09; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/host-queue-REPORT.md -->
<!-- relation: host saturation queue evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# <host> host-sat queue stall: diagnosis and fix (2026-11-09)

## Root cause
<host> rebooted cleanly at 2026-11-09 02:02Z (03:02 CET), about one minute after the queue launched
`sat-r1-20261109T020057-<host>` (pwmcp:r1). That run never finished (INFRA:NO_FINISH after 4259 s).
The cockpit container `mdt-cockpit-runner` has restart policy `no`, so it stayed `Exited (143)` after the reboot
(created 2026-11-03, finished 02:02:10Z, OOMKilled=false). From 03:22Z every launch's preflight failed
(`docker exec ... true` failed, so no run-gate version could be read, giving rc=4 and the last-line
"a newer run-gate ..." hint). It was not a run-gate floor problem: the vbpub floor is still 25.0.1
(`REPO_DEFAULTS`, unchanged since 084c4af68) and the cockpit's `gate-tools2502` venv is 25.0.2. After 5 INFRA results the queue
created `host-sat-queue.disabled.<host>` (03:52Z). Cause of the reboot itself is not established (the previous boot's journal
ends in a normal user-session/shutdown sequence; no OOM was recorded; host-level docker/cgpd/buildkitd/cow sockets were all back).

## Before
- uptime 2:54 at 04:56Z; cockpit Exited (143); run-gate bins in cockpit: .local/bin 24.3.0, gate-tools 24.3.0, gate-tools25 25.0.0, gate-tools2502 25.0.2.
- image tester-unified:r25-d38b3324fba5 present; no sat-* containers; /run/{docker-api,cgprofile,mdt-buildkitd} sockets present.

## Change on <host> (identity-checked: API check + hostname/hostnamectl/server-id 799611 on every call)
`docker start mdt-cockpit-runner`   (no recreate, no install, no wheel/hash needed)

Revert: `docker stop mdt-cockpit-runner` on <host> (via the identity-checked wrapper)

## After / verification
- `docker exec -u vscode mdt-cockpit-runner true` OK; `.local/bin/run-gate` 24.3.0 (below floor), `gate-tools2502` 25.0.2 (>= 25.0.1) -> preflight logic passes (same commands as check_prereqs).
- Pause flag `host-sat-queue.disabled.<host>` removed at 04:57Z.
- Queue loop launched `sat-r0-r1-20261109T045822-<host>` (scripts/debian-install-v2:r0-r1, commit 49177026fe9d) at 04:58:23Z with no rc=4; still running 5 min later.

## Follow-ups (not done)
- The cockpit does not survive a host reboot (restart=no). Option: `docker update --restart unless-stopped mdt-cockpit-runner` or have provision-runner/start-cockpit-runner.sh set it. Not changed here (outside the minimal fix).
- The preflight prints only the last message line; with the cockpit down the hint "a newer run-gate" is misleading (the real first message is "cockpit container ... is not running"). Worth a remote-gate fix (scripts/netcup NC backlog).
- Check whether <host> has the same restart=no exposure.
- Wrapper used: <controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/hx-<host>.sh
