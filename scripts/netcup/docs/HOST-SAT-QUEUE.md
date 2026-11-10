<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: host-sat-queue.README.md -->
<!-- relation: host saturation queue workflow -->

# host-sat-queue (r1002 + v1001 standing light-lane queue)

`host-sat-queue.sh start` runs one detached queue process per host (r1002, v1001). Each loop: fetch origin/main fresh,
probe the host (read only), and launch the next lane only if all of these hold, else log `skip+wait` and re-probe in 120 s:
memory PSI some avg10 <= 25, io PSI full avg10 <= 25, OOM-kill counter not rising, swap in use not growing (>=256 MB between
probes, absolute <= 4 GB), MemAvailable >= 2.5 GB, root disk >= 5 GB free, and load1 < 0.625*nproc (2.5 on 4 cores; this is the
headroom rule that keeps it out of the way of tnt R2 / cmru shards, so on v1001 with two shards it often waits).
Lanes run one at a time per host via `remote-gate run <host> <clean origin/main worktree> <lane> --detach` with run names
`sat-<lane>-<utc>-<host>` (the throwaway worktree is removed right after the bundle is shipped), polled every 60 s; an own run
over 60 min is cancelled (its own job only). Rotation (stateful, `logs/host-sat-queue/<host>.idx`): cli-extended r3, iobench
r0-r1 (v1001 only), pwmcp r1, topos topos-suite, cgroup-profiler focused, run-gate selftest, scripts/netcup suite,
debian-install-v2 r0-r1 and fake-integration, cli-extended r0-r1. (The bare-host `gate`/`r1-vm-real-commit` lanes are not
used: remote-gate refuses exec-mode/bare-host lanes and the VM lane waits for the v1001 reinstall.) 5 INFRA verdicts in a row
automatically disable that host's queue (`host-sat-queue.disabled.<host>`) and write the pause reason into the flag;
every INFRA result backs off 10 min.

Results: `logs/host-sat-results.tsv` (utc, host, lane, commit, run_name, verdict, seconds, log); verdict is PASS, FAIL,
INFRA:<remote-gate verdict> (PREREQ_MISSING, ERROR, PSI_REFUSED, NO_FINISH, LAUNCH_FAILED...) or `PAUSED:queue-auto` for
the synthetic automatic-pause event. Completion marker per run:
`logs/host-sat-queue/runs/<run_name>.done`. Run artifacts: `remote-gates/<run_name>/`. Queue log: `logs/host-sat-queue/<host>.log`.
If metadata creation or installation fails, the queue logs the failure and falls back to an empty pause flag so the
host stays paused; an empty flag then means the reason metadata is unavailable, not that an automatic pause is inactive.

Commands: `status`, `start [host]`, `stop [host]` (kills only the loop; an in-flight remote run finishes on its own),
`pause` (touch `host-sat-queue.disabled`) / `resume`, `probe <host>`. Per-host pause: `touch host-sat-queue.disabled.<host>`.
`status` prints the automatic-pause metadata (actor, host, UTC time, INFRA streak, last lane/result/run, log pointer and
fix hint); an empty legacy flag is shown as metadata absent. `resume` removes both global and per-host flags.
Identity: every launch runs `logs/host-sat-api-check.sh <host>` (Netcup API identity, cwd=/tmp so the repo-root `.env`
does not shadow the token) and remote-gate's own check; hosts file is the private `logs/host-sat-hosts.json` (r1002 image
override `tester-unified:r25-d38b3324fba5`). It never touches non-`sat-*` jobs or containers and never <protected-host>.
