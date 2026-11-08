# cgprofile-P6-FOLLOWUPS — BRIEF for session 7 (successor)

Session 6 (fresh Opus, checkpoint clause HARD) shipped **C8 — CP-9,
placement** and cut at the post-C8-commit boundary WITHOUT starting C9, per
the dispatch's "cut before C9 rather than half-doing it". This is the
self-authored continuation brief + retention prompt.

## State

- Worktree `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`, branch
  `rg55-followups-cgprofile`. Do NOT create another.
- History: … `4fa725dc` (C7) → `7c34dcc2` (BRIEF-6) → **C8 commit** → this
  checkpoint-docs commit.
- Base is still assumed to be P1's tip (`16f3a29f` at dispatch, RW-35), NOT
  re-verified in any session. Check the controller log's dispatch table
  before assuming P1 merged; do not merge speculatively. The in-worktree
  contract mirror `docs/RG55-INTERFACE-CONTRACT.md` is still stale (no §8)
  and arrives with the `main` merge the controller announces — never
  hand-edit it.

## What C8 shipped (do not redo, do not re-litigate)

`lib/placement.py` (the D-25 write guard incl. RW-35(a)'s single non-leaf
write, `parse_request`, `LanePlacement`: apply / migrate / leaf_readings /
kill / release / block), `tests/test_serve_placement.py` (72 tests, 100%
line+branch on the new module), backlog row CP-9, and the wiring:
`lib/serve.py` (`_make_placement`, `_placement_block`, the migration on the
discovery tick, leaf readings into `LivenessSample`, `cgroup.kill` in
`_enforce_stall_kill`, `release` + `placement` in the Summary),
`lib/liveness.py` (`record_kill(via=)`), `cgprofile.py` (`--place`,
`--memory-high`, `--memory-max`, `--cpu-weight` + the wire keys),
`docs/PROTOCOL.md` (§3's placement table; every "lands with C8" hedge gone),
goldens `start-placed-v1.1.json`, `start-refused-v1.1.json` and four
regenerated ones. **Nine rulings are settled in the LOG's session-6
"Decision asks" — read them, do not re-open them.**

## Two things the successor must deal with FIRST

1. **The r0/r1 lane is order-dependent and RED on some draws — pre-existing,
   not C8's.** `TestPeerCredentials` running before
   `TestRealSubtreeEnforcement` makes both real-subtree tests report
   `state == "ok"`; reproduced at `7c34dcc2` (pre-C8). Full reproduction in
   the LOG ("Finding: the r0/r1 lane is order-dependent"). **File it as
   CP-10** (a C7 test-isolation defect) and fix it — the lane cannot be
   called green while its colour is a coin flip.
2. **C9 close-out**, as BRIEF-5/6 minus what C8 did: `docs/PROTOCOL.md` is
   DONE; README "Running the daemon" still needs the slice unit, socket,
   host prerequisite `mdt-cgprofile.conf`, watch policy and placement;
   ATTACH-GUIDE lane section; DESIGN.md D-27..D-30 summary; `CHANGES.md`
   `[Unreleased]` one line per CP id; backlog rows CP-2, CP-4..CP-9 → FIXED
   with commit hashes (**all still `status: open`**); `CGPROFILE_VERSION`
   `"1.0.0"` → `1.1.0` (grep for the string, not just that constant);
   `INDEX.md` regenerated with `nyxloom backlog index`.

## Also still pending

- **Live probes** — C6's 7-step plan, C7's watch probe, C8's placement probe
  (the exact `docker run` and the expectations are in the REPORT's "Live
  probes — still deferred"). Blocked all session by the two live estate
  mutation runs (RW-39/RW-42 forbid an image build or probe container;
  they relax only bare-host pytest lanes). Use `cgprofile-p6-probe` on
  `/tmp/cgprofile-p6`, removed in a `finally`; NEVER the singleton
  `cgprofile-host-daemon`.
- **r2 (LAST and once) and the r3 canary.**
- **`gc` does not reclaim a leaf orphaned by a daemon restart** (REPORT's
  "Deferred out of C8") — worth a row, deliberately not invented into §8.3.

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
head -2 /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'; docker ps --format '{{.Names}}' | grep run-gate-vbpub
python3 -m pytest tests/test_serve_socket_carrier.py::TestPeerCredentials \
    tests/test_serve_watch.py::TestRealSubtreeEnforcement -q -p no:randomly
```
That last command is the CP-10 reproduction (expect 2 failed, 9 passed).
Then `grep -rn "1\.0\.0" --include=*.py --include=*.md --include=*.toml .`
for the C9 version sweep.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the LOG's session-6 "Decision asks" (nine settled
rulings — the `bad-argument`-vs-`place-refused` boundary, the `null`-vs-block
union, `applied` holding only requested caps, the relative-path
`write-failed` code, the kernfs `rmdir` seam) and its order-dependence
finding with the reproduction commands; the REPORT's C8 oracle table, golden
table and mutation table; the HOST LOAD / RW-39 / RW-42 rules and the
`--base` gotcha (the r0/r1 lane REFUSES `--base`); the `cp -a`-to-scratch
mutation discipline (never `git checkout` on uncommitted work).
DROP: C1–C7's implementation blow-by-blow; C8's own tool-call narration;
the contract and design text, re-fetchable from `main` in one command each.
