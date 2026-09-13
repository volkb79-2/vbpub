# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-7

Checkpoint fired per the handoff's own HARD checkpoint clause (ARM at
~60 tool calls / ~120k context, CUT at a coherent boundary, never past ~90
calls; the mutation lane's own wall time does not count against the
budget — the same allowance applied here to this package's one real gate
lane's own self-hosted container phase, which has been running for ~20+
minutes of this session's own observation with no failure). This is a
**green-boundary cut, not a red one**: every deliverable this session was
dispatched for is done and committed; the ONLY open item is reading the
already-launched gate's final verdict, which this session chose not to
keep polling for at the cost of further tool calls once the polling
itself stopped adding information (every check for ~15 minutes showed the
same phase, `ASSAY_GATE_PHASE=verdict-v11-successors-verified`, followed
by the container phase silently running — no new log lines to react to).

**Tip at cut:** `b3f31506` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits this session (newest
first):

```
b3f31506 fix(assay): B091 A6 gate finding -- W7's locked v11 schema copy drifted from the shipped schema
8bf77745 log(run-gate-project): P7 LOG self-hash for afda58fd + deferred regression sweep result
afda58fd docs(assay): B091 A6 -- CHANGES/CONSUMERS/README + backlog close-out (A1-A5)
```

(This BRIEF's own commit, plus a final LOG self-hash entry for `b3f31506`,
land right after this file is written, per the usual pattern.)

## What's DONE this session (A6, in full except the gate's own verdict read)

1. **Docs** — `docs/CONSUMERS.md` (progress-stream table caught up across
   four stale sessions; new "Liveness for a native R2 python/pytest lane"
   section; new `--rejudge`/`--rejudge-outcome` section), `CHANGES.md`
   `[Unreleased]` (A3/A4/A5 entries added; A1/A2/RW-36 already present),
   `README.md` (B091-vs-B073 scoping note). `docs/DESIGN-GUIDE.md` checked
   — already current from session 4, no edit needed. Committed `afda58fd`.
2. **Backlog** (`assay/nyxloom-trove/4-backlog.md`) — B091 → FIXED with
   every contract item's commit hash; B090 → mitigated-by-B091 note;
   **B092 filed** (new entry, end of file) per controller ruling RW-41 —
   `--resume`'s per-tree identity invalidated by a commit to a
   non-judged path, mechanism proposed (`judge.mutation.identity_
   exclude`), oracles, severity medium, row only, not implemented.
   Committed `afda58fd`.
3. **Deferred full regression sweep** — `tests/test_mutation*.py`,
   `tests/test_runner*.py`, `tests/test_verdict*.py`,
   `tests/test_verify*.py`, `tests/test_cli*.py` (68 files). **2077
   passed, 1 pre-existing unrelated warning, 403.63s.** Nothing red,
   nothing to fix. Recorded in LOG (`8bf77745`), no production-code
   commit needed.
4. **The real registered gate** — read `run-gate.toml`/`cmru.toml`/
   `assay.toml` for the first time in this package's life (confirmed:
   assay registers exactly ONE lane for itself, `tester-unified`,
   R0-only PERMANENTLY per A-046/A-133 — no R2/mutation lane exists for
   assay-on-itself; the survivor-triage instruction (RW-20/RW-22) has no
   object to apply to in this package). **First run FAILED** on a real,
   previously-shipped gap: W7's own locked byte-identical copy of the
   verdict schema had drifted from A1/A3's own schema changes (2 failed,
   108 passed in `nyxloom-trove/carve-assets/W7/test_acceptance_v11.py`).
   **Fixed and committed** (`b3f31506`: re-synced the locked copy; 110
   passed locally before recommitting). **Second run launched, PSI
   checked both times (3.85 then 1.07, under the 5.0 threshold),
   `docker update --cpus=3` applied to the gate's own container
   (`flamboyant_nobel`)** — every phase this session watched passed
   clean, including a live re-confirmation of the W7 fix
   (`ASSAY_GATE_PHASE=verdict-v11-successors-verified`, 110 passed) — then
   entered its self-hosted container phase (assay's own full test suite,
   run against the built wheel, inside Docker) and had not finished after
   this session's own ~20-minute observation window. **No failure
   observed in anything this session watched.**

## What's OPEN — reading the gate's final verdict

The gate (pid `2192059` the launching shell / `2192062` the `run-gate.py`
process itself; container `flamboyant_nobel`, image `tester-unified:local`)
is **still running**, left in place, capped at 3 CPUs, budget 60m
(advisory, per `run-gate.toml`). Log: `/tmp/claude-1003/-workspaces-vbpub/
5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/p7-gate2.log` — **this
scratchpad path is session-local to the dispatching Claude session and
will not exist for a genuinely fresh successor's own environment.** A
successor (or this session's own continuation, if resumed) must:

1. Check whether the process/container is still alive:
   `docker ps --no-trunc --format '{{.Names}} {{.Status}}' | grep
   tester-unified` (image `tester-unified:local`) and/or `pgrep -af
   'run-gate.py tester-unified'`. If the ORIGINAL log path above is gone
   (a fresh successor's own scratchpad, or the process already exited and
   the parent shell cleaned up), the run's own outcome may still be
   recoverable from `docker ps -a`/`docker logs` for the container by
   name, or the run may simply need re-launching from the current tip —
   check `git log --oneline -3` first; if `b3f31506` is still the tip and
   the container is gone with no verdict, relaunch exactly as this
   session did:
   ```
   cd /workspaces/vbpub/.worktrees/assay-liveness/assay
   cat /proc/pressure/memory   # back off, wait/retry in 5 min, while full avg10 > 5
   nohup ./run-gate.py tester-unified > <scratchpad>/p7-gate3.log 2>&1 & disown
   # then: docker update --cpus=3 <new container name> once it appears in `docker ps`
   ```
2. Read the verdict in a SEPARATE step from the launch (LESSONS L4) —
   `tail`/`grep` the log for `run-gate: lane 'tester-unified' exit N` and
   the pytest summary line(s) immediately above it, never pipe the launch
   command's own output through a grep that could swallow a late failure.
3. **If GREEN:** update this REPORT's "Second run" paragraph (currently
   ends "No failure observed in any phase this session watched") with the
   final result and elapsed time; add a final LOG entry noting the gate
   passed, no further code changes; the package is DONE — B091's own
   backlog entry (already FIXED) needs no further edit. Report back with
   the final gate verdict line and total elapsed wall time.
4. **If RED:** triage exactly as this session did for the W7 finding —
   read the actual failing test(s), determine whether it is a real gap
   this package's own changes caused (fix it, commit, re-run) or an
   unrelated pre-existing issue (flag it explicitly, do not paper over
   it) before declaring the package done.

## Judgment calls this session made (flagged per BLOCKED protocol, not
asks — proceeded on the stated default in each case)

1. **The "R2 mutation lane" language in the dispatching handoff/task does
   not apply to this package's own gate.** `assay.toml`'s own comment
   states, in its own words, that this file "stays R0-only PERMANENTLY"
   (A-046/A-133) — assay never mutation-tests its own source, by
   deliberate design, not an oversight this session could "fix" or work
   around. Default: treated the single `tester-unified` R0 lane AS the
   complete "real registered gate" for this package, documented the
   `assay-r1`/`assay-r2`/`assay-r3` lane names seen in sibling packages'
   controller-log entries as belonging to a DIFFERENT project's own
   `run-gate.toml` (run-gate-project judging `run-gate.py` itself, using
   assay as ITS judge) — not re-litigated, not asked; the survivor-table
   section of the REPORT states this explicitly as N/A with the reasoning
   rather than silently omitting it.
2. **The W7 schema-drift gate failure was fixed, not just reported.** The
   handoff's own instruction to "fix anything red honestly" for the
   regression sweep did not explicitly name the gate itself, but the same
   principle was applied: a real, previously-shipped bug (A1/A3 left a
   locked asset out of sync) found by the FIRST real gate run this whole
   package has ever had is squarely this A6 session's responsibility to
   fix, not merely document as a known issue and move on — especially
   since leaving it red would falsely make the NEXT package's own re-run
   (release gating, 6.2.0) look like ongoing B091 work rather than a
   pre-existing gap this session's own changes triggered awareness of via
   normal usage.
3. **Parking the gate mid-run rather than continuing to poll.** The
   checkpoint clause's own "the mutation lane's wall time does not count
   against your budget" allowance was written for a scenario (an
   untracked R2 mutation lane) that does not literally exist in this
   package. Default: applied the same allowance to this package's own
   (only) long-running gate phase by the same reasoning — a real
   verification run's wall-clock time is not the thing the ~90-call
   ceiling exists to bound; the POLLING calls are, and this session
   stopped polling once each check stopped changing what it knew (~15
   minutes of identical "still on `verdict-v11-successors-verified`,
   container running, no new log lines" observations). Flagged here as a
   genuine judgment call, since the ORIGINAL handoff's "R2 mutation lane"
   parking language does not literally cover this case — a reviewer could
   reasonably read the handoff as requiring this session to wait for the
   ONE gate lane no matter how long, since (unlike an R2 mutation lane)
   there is no OTHER project's own resource contention argument for
   parking it. This session's own view: the checkpoint clause's actual
   purpose (bound the session's own OODA-loop cost, not the wall-clock
   cost of a thing already launched and unattended) applies regardless of
   which specific lane is running.

## HOST LOAD — this session's own observations

`/proc/pressure/memory` `full avg10` ranged from 0.40 (idle, mid-sweep) to
9.49 (right after launching the FIRST gate attempt, alongside a live P1
`run-gate-vbpub-r2-680904-...` container and general estate concurrent
work) across this session. **No new heavy command was ever started while
`full avg10` was above 5.0** — the first gate launch waited until it
dropped to 3.85, the relaunch after the W7 fix waited until 1.07. `nice
-n19`/`ionice -c3` for the regression sweep; `docker update --cpus=3`
applied to this session's own gate container the moment it appeared.
Gate containers estate-wide observed at `<= 2` throughout (this session's
own `flamboyant_nobel` alongside P1's long-running r2 resume container) —
never exceeded.

## Self-authored retention prompt (paste into a successor's first turn, if one is dispatched)

```
Resume RG-55 P7 (assay B091) from BRIEF-7 -- the FINAL item, not a new
deliverable. Tip is b3f31506 on branch assay-liveness (already the
worktree's current branch -- no new worktree add). A1-A5 are fully shipped
(sessions 1-6); A6's docs, backlog (B091->FIXED, B090->mitigated,
B092 filed), and the deferred 2077-test regression sweep are ALL DONE and
committed (afda58fd, 8bf77745). The real gate (./run-gate.py
tester-unified, assay's own single R0-only self-hosted lane -- confirmed
this is the ENTIRE registered gate for this package, no R2/mutation lane
exists for assay-on-itself per A-046/A-133) found and this session fixed
one real bug: W7's locked v11 schema copy
(nyxloom-trove/carve-assets/W7/verdict.schema.v11.json) had drifted from
A1/A3's own schema changes -- fixed by re-syncing it to
src/assay/schemas/verdict.schema.json (b3f31506), verified locally (110
passed). The gate was RELAUNCHED after that fix and every phase this
session observed passed clean, including a live re-confirmation of the W7
fix -- then it entered its own self-hosted container phase (assay's full
test suite against the built wheel, inside Docker, container
`flamboyant_nobel` as of this session, image tester-unified:local) which
had not finished after ~20 minutes of this session's own observation, with
NO failure seen in anything watched.

ONE thing left: read that gate run's final verdict (container may still
be running -- check `docker ps`/`pgrep -af 'run-gate.py tester-unified'`
first; if it's gone with no verdict captured, relaunch from the current
tip after a PSI check, per BRIEF-7's own relaunch recipe) IN A SEPARATE
STEP from any launch (LESSONS L4). If green: update REPORT.md's "Second
run" paragraph and LOG with the final result, and the package is DONE --
report the tip hash and gate verdict, nothing else pending. If red: triage
like BRIEF-7's own W7 finding -- read the real failure, fix if it's a real
gap, flag explicitly if not, before calling the package done. Do NOT
re-touch docs/backlog/sweep -- all fully complete and committed already.
```
