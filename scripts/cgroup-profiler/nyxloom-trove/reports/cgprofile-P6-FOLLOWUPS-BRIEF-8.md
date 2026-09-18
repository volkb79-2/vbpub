# cgprofile-P6-FOLLOWUPS — BRIEF for session 8 (successor, IF one is dispatched)

Session 7 (fresh Sonnet, checkpoint clause HARD) completed EVERY scoped
deliverable except r2 and the live probes, both blocked by real,
still-live estate mutation runs at cut time — not by anything a successor
needs to re-derive. This is a routine checkpoint cut at a clean boundary,
not a distress signal: read this brief, check the two blocking conditions
below, and if they have cleared, go straight to "Exact next command".

## State

- Worktree `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another.
- Tip: `d6c2c63d`. History since BRIEF-7's `e4be5111`: `b50163e9` (CP-10
  fix), `478f1443` (C9 close-out), `e17cdf9a` (CHANGES.md self-hash
  fill-in), `d6c2c63d` (this session's LOG/REPORT).
- Working tree clean.

## What session 7 shipped (do not redo, do not re-litigate)

1. **CP-10, root-caused and fixed** (`b50163e9`) — and the root cause is
   NOT what session 6 found. `TestRealSubtreeEnforcement`'s two
   `on_stall` tests were never actually order-dependent on
   `TestPeerCredentials`; they track this HOST's real memory PSI at run
   time, because the test passed `proc_root="/proc"` (needed for real
   subprocess pid resolution) and `lib/serve.py` had one `proc_root` knob
   feeding BOTH pid resolution AND the host-PSI read that drives §8.4's
   pause condition. Fixed with a new `host_proc_root` seam (defaults to
   `proc_root`, every other caller/test byte-identical). Proven: both
   class orders, 3 `pytest-randomly` seeds, real host `full avg10` <1 to
   ~24 across the runs, all green. Backlog CP-10 filed with the corrected
   root cause and set `fixed`; CP-11 (orphaned placement leaf on daemon
   restart) filed `open`, not implemented, per the dispatch.
2. **C9 close-out, complete** (`478f1443`, `e17cdf9a`): `docs/PROTOCOL.md`
   was already done (no change); README/ATTACH-GUIDE.md/DESIGN.md all
   gained their missing sections (slice unit + watch + placement in
   README; a new consumer-facing §9 in ATTACH-GUIDE.md; a D-27..D-30
   summary in DESIGN.md §4.15a); `CHANGES.md` created (did not exist for
   this project) with `[Unreleased]` carrying one line per CP id;
   `CGPROFILE_VERSION`/Dockerfile ARG/7 golden JSON files/one test
   literal bumped `1.0.0` -> `1.1.0` (full sweep table in the REPORT);
   backlog CP-2/CP-4..CP-9 -> `fixed` with commit hashes, `INDEX.md`
   regenerated. `pyproject.toml`'s `version = "0.1.0"` deliberately left
   alone (a separate, cmru-SCM-managed field P1 never touched).
3. **A decision ask, logged, not re-opened:** bumping the 7 in-project
   goldens broke `tests/test_summary.py::TestFixtureIdentity`'s
   byte-identity check against the FROZEN cross-package copy at
   `run-gate-project/nyxloom-trove/fixtures/rg55/` (contract §6). Session
   7 synced that copy too (same 4 files, same field, mechanical) rather
   than leave the gate red. **A reviewer should attack this first** — see
   the REPORT's "what a reviewer should attack first" for the full
   framing; do not silently revert it without reading that first.
4. **Gates, both GREEN, verdicts read from the tool's own output** (never
   a pipe exit code): **r0-r1** (bare) — `1335 passed`, 100% line+branch
   across all 23 modules, `run-gate: lane 'r0-r1' exit 0`. **r3 canary** —
   7/7 rejected as required, `run-gate: lane 'r3' exit 0`. Neither needed
   a repair.

## What is still open, and why

**r2 and the live probes — blocked, not skipped.** Checked twice this
session (start and just before the cut): `docker ps` still shows
`run-gate-vbpub-r2-680904-1789228700` (P1's mutation container) AND
`pgrep -af 'run-gate.py --base main assay-r2'` still shows a live
bare-host run (pid 1499375). The dispatch's own condition for r2/an image
build/probes — "if both are gone, build `cgprofile:local` once and run
the REPORT's probe plan" — was never met. This is an estate-wide
condition outside this package; it may already be gone when you read
this.

**CP-11** (`gc`/`_recover_orphans` does not reclaim a leaf orphaned by a
daemon restart) — filed, `open`, deliberately NOT implemented (not
spec'd by §8.3, CP-1's retention work is the natural owner per the row's
own text). Not part of this dispatch's scope; do not implement it
speculatively.

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
head -2 /proc/pressure/memory
docker ps --format '{{.Names}}' | grep run-gate-vbpub-r2
pgrep -af 'run-gate.py --base main assay-r2'
```
If BOTH the docker container and the pgrep line are gone, AND
`full avg10 < 5`: build `cgprofile:local` once
(`python3 build-push.py --build`), run `./run-gate.py r2` (last and once,
launched untracked per the checkpoint clause's own instruction — 600s
budget per candidate is already set), then run the REPORT's probe plan
(C6's 7-step diff, C7's watch probe, C8's placement probe — exact `docker
run` argv and expectations are in the REPORT's "Live probes — still
deferred" section, unchanged) with your OWN `cgprofile-p6-probe` on
`/tmp/cgprofile-p6`, removed in a `finally` — NEVER the singleton
`cgprofile-host-daemon`. Record r2's survivor table (RW-20/RW-22: a
killing test or a written equivalent-mutant justification per survivor)
and the probe transcripts in the REPORT, then this package is ready for
review — flag readiness to the controller rather than starting a review
round yourself.

If either condition is not met: re-check periodically or return
"still blocked" rather than force a build/r2/probe against the HOST LOAD
rule.

## Self-authored retention prompt (paste into the successor's context, if dispatched)

KEEP: this BRIEF in full; the REPORT's CP-10 root-cause table, docs
disposition table, version sweep table, gate verdict lines, and "what a
reviewer should attack first" (especially the frozen-copy decision ask);
the exact `docker run` argv and probe expectations already written in the
REPORT (C6/C7/C8 probe sections) — do not re-derive them.
DROP: C1-C8's implementation blow-by-blow; CP-10's investigative dead
ends (the mis-ordered trials) — the corrected root cause and its proof
are what matter now; session 7's own tool-call narration.
