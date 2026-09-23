# cgprofile-P1-DAEMON — adversarial review handoff (RG-55 wave, package P1)

**Reviewer:** FRESH session (Opus, xhigh), never a fork of the implementer or
the controller. **Your job is to BREAK this before merge.** 3-round cap;
fix-verification rounds resume YOUR session (the controller messages you the
repair commit). Records: `scripts/cgroup-profiler/nyxloom-trove/reports/
cgprofile-P1-DAEMON-REVIEW-round<n>.md`.

Branch `rg55-profiler-daemon`, worktree `/workspaces/vbpub/.worktrees/rg55-profiler-daemon`,
project dir `scripts/cgroup-profiler/`. Base: `main` at the P0 freeze
(`63b928da`). The tip hash is in the dispatch message. Review the FULL diff
`main...<tip>` — every file, every type.

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: the plan of record
(`run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
§2 D-1..D-16, §3, §4, §5-P1, §10), the contract
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §1–§7) and
`fixtures/rg55/README.md`, the controller log's Rulings section
(`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
— RW-3, RW-7, RW-9, RW-11, RW-13..RW-16, RW-19, RW-21, RW-23,
RW-47, and RW-48 bind this package), the
implementer handoff (`cgprofile-P1-DAEMON-HANDOFF.md`, what was asked), then
the diff itself — `lib/summary.py`, `lib/subtree.py`, `lib/damon.py`,
`lib/serve.py`, `lib/store.py` changes, `cgprofile.py`, the shim, the
Dockerfile + build scripts, the ciu templates, `cmru.toml`, the root
`cmru.orchestration.toml`, `pyproject.toml`, every test file, docs. Form
your own view of correctness, safety and contract conformance BEFORE reading
the implementer's narrative. Run your OWN sweeps (the handoff biases
coverage, not conclusions).

## Phase 2 — RECONCILE against the implementer's claims

Read `cgprofile-P1-DAEMON-LOG.md`, `-REPORT.md`, every `-BRIEF-n.md`; check
each claim against what you found; list claims you could not verify.

## Attack surface (minimum; add your own)

1. **D-15 daemon safety.** Every write the daemon can issue: is there ANY
   path outside `WRITABLE_ROOTS` (sessions dir, `/sys/kernel/mm/damon/admin`)?
   Grep for `open(..., "w")`, `write_text`, `os.replace`, `shutil`, `subprocess`
   in `lib/serve.py`, `lib/damon.py`, `lib/store.py`, `cgprofile.py`. Is
   `--cap`/`TempCaps` reachable from `serve` or `ctl` by any argv? Does the
   compose template really give `network_mode: none`, no docker socket, the
   interactive `cgroup_parent` AUTHORED (not governance-injected), `pid:
   host`, `cgroupns: host`, `privileged: true`, `mem_limit`? Does SIGTERM
   restore `nr_kdamonds` and never tear down a foreign kdamond? Does a crash
   mid-session leak a kdamond (plant an exception in the sampler thread and
   watch)?
2. **Contract conformance.** Every `ctl` verb's response vs contract §2 and
   the goldens; exit codes 0/2/3; `contract: 1` on every response incl.
   errors; ONE JSON document on stdout (plant a stray print and watch the
   test catch it); `stop` within 30 s after a LONG session (inject a clock:
   1 h of 1 s samples — is the summary incremental or recomputed?);
   idempotent `start` by (container id, token) → `reused: true`; idempotent
   `stop` → `already_stopped: true`; `too-many-sessions`; `target-not-found`.
3. **§7 arithmetic.** Nearest-rank (N=5 gotcha), `source` per scope
   (`memory.peak` vs `sampled-max`), baseline subtraction floored at 0,
   `cores_max` per interval pair, PSI deltas /1e6, `limit_drift` counting,
   null discipline (unreadable at ONE end → null, never a delta against
   nothing; RW-7 last-SUCCESSFUL read). Plant 6+ mutants of your own in
   `lib/summary.py` (swap max/min, drop a /1e6, off-by-one in rank, wrong
   floor) — every one must be caught by the suite; record which test caught
   each.
4. **Subtree resolver.** environ read of a vanished/zombie pid; pid reuse
   across discovery ticks; a descendant that re-parents to PID 1 (daemonized
   worker) — is it lost? (disclosed via `targets_seen` is acceptable, silent
   is not); `/proc/<pid>/task/*/children` absent → ppid map path exercised.
5. **DAMON pool.** Two concurrent sessions live (indices 0 and 1); stopping
   0 while 1 lives writes NO `nr_kdamonds`; reuse of 0; shrink at the end;
   `status: "unavailable"` with reason when sysfs is read-only or the module
   is absent; RW-15 no-token recommit; the RW-14 real report (`ctl report`
   renders the existing interactive HTML, not a stub — open the file).
6. **Registry/retention.** Restart with live sessions → `aborted:
   daemon-restarted` with partial summary; retention never removes a live or
   in-flight session; `--keep-days` vs `--keep-sessions` interplay.
7. **Image and stack.** No network fetch at runtime (grep the Dockerfile and
   scripts for pip/curl/wget at run time); `cgprofile` wrapper routes verbs
   correctly; OCI labels; `build-push.py --push` refuses without a version;
   ciu: render from the standalone root, confirm the authored `cgroup_parent`
   survives governance, the refusal when `$CGROUP_PARENT_DEV_INTERACTIVE` is
   unset (unset it and render), the singleton name, restart policy; cmru:
   `cmru status --project cgroup-profiler` clean; the release gate lanes.
8. **Hollow tests.** For every new test file: mutate the subject, watch the
   test. Coverage 100% line+branch is necessary, not sufficient. The
   contract-fixture identity test really compares bytes against
   `run-gate-project/nyxloom-trove/fixtures/rg55/`.
9. **Rulings honored.** RW-3 one-liner present; RW-13/RW-15/RW-16 as ruled;
   RW-19/RW-21/RW-23/RW-47/RW-48 recorded and reflected in code/tests;
   CP-4..CP-7 entries real and honest. Read `.assay/verdict-r2.json`
   separately and reconcile every survivor with REPORT's concrete
   disposition; in particular, independently attack the focused oracle for
   `lib/summary.py:214` and the four claimed equivalents at
   `lib/serve.py:626,704` and `lib/summary.py:173,177`.
10. **Docs.** README "Running the daemon", ATTACH-GUIDE lane section,
    DESIGN.md tier story — accurate to the code, not aspirational.

## Live probes (you run them yourself; host rule below)

- `python3 build-push.py --build` (or confirm `cgprofile:local` is current
  for the tip: compare the image's revision label to the tip).
- `ciu up` from the project dir (or the documented fallback); `docker inspect
  cgprofile-host-daemon` shows the interactive slice, privileged, pid host,
  cgroupns host, network none; `ctl version --json` verbatim.
- Ephemeral probe with DAMON on (100 MiB held 12 s in a throwaway
  `cmru-enroll-fixture:local` container under `dev-background.slice`, token
  set) → summary sanity (peak ≥ 100 MiB, `source: memory.peak`, cpu > 0,
  damon on with hot bytes or `unavailable:<reason>`); `ctl report` on it →
  open the HTML.
- Shared probe (sleep container + `docker exec -e RUN_GATE_PROFILE_SESSION=…
  … 80 MiB`) → `targets_seen ≥ 1`, `peak_over_baseline ≥ 70 MiB`,
  `source: sampled-max`.
- Two sessions concurrently (both probes at once) → two kdamond indices,
  independent summaries.
- `finally`: remove probes; `ciu down` the daemon (leave the host as you
  found it).

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write the round file, then return the verdict line first in your message.

## HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal. pytest
SERIAL only, `nice -n 19 ionice -c 3`; targeted files while iterating, the
whole suite at most once. ≤ 2 gate containers estate-wide (`docker ps` for
`tester-unified:local` first; a P2 package may hold one); `docker update
--cpus=3` after launch; remove in a `finally`. Only the daemon may run with
`--cgroupns=host --pid=host`. Never touch `run-gate-project/`, `ciu/src/`,
`/workspaces/dstdns`. Edit tool only if you must write (round files); no
commits to the branch — repairs are the implementer's.
