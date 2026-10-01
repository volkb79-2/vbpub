# cgprofile-P6-FOLLOWUPS — BRIEF for session 9 (or the controller directly)

Session 8 (fresh Sonnet) ran EVERY probe in the REPORT's probe plan
(a)-(f) against the real daemon, found and root-cause-fixed one real bug
(CP-12) along the way, re-verified the fix live, and re-ran the registered
r0-r1 lane to 100% line+branch. Only r2 is still blocked — both estate
mutation slots (RW-42, cap 2) were occupied for this session's ENTIRE
runtime. This is a routine checkpoint cut, not a distress signal: check
the one blocking condition below and, if it has cleared, go straight to
"Exact next command".

## State

- Worktree `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another.
- Tip: `9ebb1ecd`. History since BRIEF-8's `241122b6`: `8067cc03` (CP-12
  fix: `_finalize_after_kill`), `9ebb1ecd` (backlog hash fill-in).
- Working tree clean. No leftover `cgprofile-p6-*` containers/volumes (all
  torn down in `finally`-style cleanup at the end of every probe run —
  verified with `docker ps -a` / `docker volume ls` before this cut).

## What session 8 did (do not redo, do not re-litigate)

**Live probes (a)-(f), all run against `cgprofile:local` built from
`241122b6`, using session 8's OWN `cgprofile-p6-probe` instance on a
scratch mount — never the singleton `cgprofile-host-daemon`.** Full
transcripts are in the REPORT's new "Live probes (session 8)" section.

- **(a) every verb, both carriers, diffed** — `version`/`host`/`gc`/
  `status` (no session) and `start`/`status`/`stop`/`report` (a real
  session, created via exec, manipulated via socket — genuine cross-
  carrier interop, not just two same-carrier calls). Every diff was a
  genuinely time-varying field (timestamps, PSI, memory samples, elapsed
  seconds); zero structural differences. No bug.
- **(b) peer-refused** — restarted the probe with
  `CGPROFILE_ALLOW_UIDS=0`; a throwaway container's uid-1000 process got
  `{"ok": false, "error": {"code": "peer-refused", ...}}` over the socket
  while `docker exec` (uid 0) kept working. `version`'s own
  `transports.socket.allow_uids` correctly reported `[0]`. No bug.
- **(c) placed exec-mode probe** — this host has NO `dev-gates.slice`
  (mdt host-setup not installed here yet; confirmed independently via a
  throwaway `--cgroupns=host` container listing `/sys/fs/cgroup/dev.slice/`
  before touching the daemon at all). `ctl start --place ...` correctly
  answered `place-refused:no-gates-slice`, `leaf: null`, `applied: {}`,
  and the session started anyway — exactly the documented fallback. No
  bug.
- **(d) watch probe, `--on-stall kill`** — **found and fixed CP-12**: an
  enforced kill (a REAL tagged pid, confirmed dead via `docker exec
  <target> ps`) never finalized the session; the `watch` stream kept
  sending `reading` lines for 2+ minutes with no `end`. Root-caused,
  fixed (`8067cc03`, `lib/serve.py`'s new `_finalize_after_kill`), new
  regression coverage added, re-verified live against a rebuilt image —
  the fixed daemon's `end` line now lands at the SAME timestamp as the
  `killed` verdict. Full root-cause narrative, fix description and proof
  are in backlog `CP-12` and the commit message; do not re-derive them.
- **(e) `ctl host --json`** — `gates_slice.present: false` (consistent
  with (c)); `daemon_slice` block present and sane. No bug.
- **(f) `ctl version --json`** — `transports` block matches §8.6 exactly
  in both the default and `ALLOW_UIDS=0` configurations. No bug.

**Registered r0-r1 lane, run TWICE** (once before CP-12, to establish the
100%-minus-one-branch baseline that pointed straight at the fix's own
defensive guard; once after adding the idempotency test): `tools/gate.sh
{worktree} coverage`, verdict read directly from the tool's own coverage
table (never a pipe tail) — **1336 passed, 100% line AND branch on every
one of the 23 modules**, including the newly-added
`lib/serve.py::_finalize_after_kill`.

**CP-12 filed and closed** in the same session
(`nyxloom-trove/backlog/CP-12-...md`), commit hash filled in.

## What is still open, and why

**r2 — blocked the entire session, still blocked at cut time.** Checked
repeatedly (session start, before/after each probe cluster, at the cut):
`docker ps` still shows `run-gate-vbpub-r2-680904-1789228700` (P1's
mutation container, `.worktrees/rg55-profiler-daemon`) AND `pgrep -af
'run-gate.py --base main assay-r2'` still shows a live bare-host run (pid
1499375, `.worktrees/rg55-run-gate-client`, i.e. P2). RW-42's cap is 2
estate-wide; both slots were already spoken for before this session even
started and never freed. This is an estate-wide condition outside this
package — it may already be gone when you read this.

## Exact next command

```
docker ps --no-trunc --format '{{.Names}}' | grep -E 'run-gate-vbpub-r2|assay-r2'
pgrep -af 'run-gate.py --base main assay-r2'
cat /proc/pressure/memory   # want full avg10 < 5
```
If BOTH the docker container and the pgrep line for OTHER projects' r2
runs are gone (a container/process for THIS package's own r2, once
launched, obviously does not count against itself), AND `full avg10 < 5`:

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
nohup nice -n 19 ionice -c 3 python3 run-gate.py --base rg55-profiler-daemon r2 \
  > <scratchpad>/p6-r2.log 2>&1 &
disown
# tracked watcher, cheap:
until ! kill -0 <pid> 2>/dev/null; do sleep 60; done
```
(`--base rg55-profiler-daemon` per BRIEF-8/the dispatch; switch to `--base
main` once the controller announces P1's merge. `run-gate.py` here is the
project's own symlink to `run-gate-project/run-gate.py` — already resolved,
no separate cd needed.) Right after launch: find the container name via
`docker ps --format '{{.Names}}' | grep run-gate-vbpub-r2` and `docker
update --cpus=3 <that name>`.

When the verdict lands, read it in a SEPARATE step (never a pipe tail —
`.assay/verdict-r2.json` / the tool's own printed summary), then triage
every survivor per RW-20/RW-22 (a killing test or a written
equivalent-mutant justification in the REPORT). Note RW-41's per-tree
resume rule: a records-only commit (even this BRIEF, if committed after
r2 starts) invalidates a resume — land r2 BEFORE any further doc commits
if at all avoidable, or accept a from-scratch re-run.

If either condition is not met: re-check periodically, or write a fresh
BRIEF-10 with "still blocked" and the two slot-holders' identities rather
than forcing r2 against the HOST LOAD rule.

## Self-authored retention prompt (paste into the successor's context, if dispatched)

KEEP: this BRIEF in full; backlog `CP-12`'s root-cause/fix/proof sections
(do not re-derive — read them, cite them); the REPORT's "Live probes
(session 8)" transcripts for (a)-(f); the r0-r1 100%-coverage result;
the exact r2 command above.
DROP: session 8's own investigation narrative for CP-12 (the sibling-
container bind-mount-path detour, the two failing-test root-causes) —
the backlog entry and the commit message are the distilled versions of
record; this BRIEF's own tool-call sequence.
