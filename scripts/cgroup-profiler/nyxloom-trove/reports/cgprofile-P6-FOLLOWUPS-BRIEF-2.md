# cgprofile-P6-FOLLOWUPS — BRIEF for session 2 (successor)

Session 1 (fresh Sonnet, checkpoint clause HARD) shipped C1 and C2, cut
clean at the post-C2-commit boundary per the checkpoint clause (~68 tool
calls, past the ~60 ARM threshold). This is the self-authored continuation
brief + retention prompt the controller dispatches a fresh successor with.

## State

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`.
- Tip: `16b01c1c` (`376bb9cb` C1 CP-4, then `16b01c1c` C2 CP-5).
- Base was `16f3a29f` (P1's tip at dispatch, RW-35) — check with the
  controller whether P1 has since merged into `main`; if so, `git merge
  main` (or `rg55-profiler-daemon` if not yet merged) before continuing,
  per the handoff's "the controller tells you when" instruction — do NOT
  merge speculatively without that signal.
- Full per-deliverable evidence for C1/C2: `cgprofile-P6-FOLLOWUPS-REPORT.md`
  (this session). Full orientation + implementation narrative:
  `cgprofile-P6-FOLLOWUPS-LOG.md` (this session, two dated entries).

## What remains (handoff order, C3 onward)

- **C3 — CP-7** (manifest `limits` table): resolve effective limits with
  `lib.limits.effective()` for the session's cgroup AT START (read-only,
  once) and store in the manifest; `analyze.py` proposal checks then work
  on daemon reports; one test with an oversubscribed fixture. NOTE: C2
  already added a `limits_mod.effective()` call inside `_create_session_locked`
  (`initial_effective_limits`, stored on `sess.last_effective_limits` for
  CP-5's drift detection) — C3 can likely reuse that same value for the
  manifest table rather than resolving it twice; check
  `_manifest_for`/`rundir.write_manifest` call sites in `lib/serve.py`
  before adding a second `effective()` call.
- **C4 — CP-6** (DAMON series in report): `lib/analyze.py` gains a
  `damon.jsonl` reader; `report_html` charts hot/warm/cold/idle bytes;
  absent file -> no figure, no error.
- **C5** — `infra/cgprofile.slice` + `infra/README.md` + `ctl host` §8.5
  (`gates_slice`, `daemon_slice`); ciu compose template authors
  `cgroup_parent: cgprofile.slice`. Goldens `fixtures/rg55/host-v1.1.json`.
- **C6 — CP-2** socket carrier (D-30, §8.1, §8.6) — the largest remaining
  deliverable: host-visible `/run/cgprofile/ctl.sock`, peer creds
  (`SO_PEERCRED`, `CGPROFILE_ALLOW_UIDS`), `ctl` request-shape alignment,
  `docs/PROTOCOL.md` (new), per-verb goldens
  `fixtures/rg55/socket/<verb>-{request,response}.json`, a both-carriers
  parity test.
- **C7 — CP-8** watch role (D-27, §8.2, §8.4) — file the CP-8 backlog row
  FIRST (same shape as CP-4/CP-5's rows, provenance RW-30/RW-34). State
  machine, `--progress-stream`/`--idle-bound`/`--ceiling`/`--on-stall`,
  PSI-paused idle clock, `ctl watch` streaming on both carriers.
- **C8 — CP-9** placement (D-20, D-25, §8.3) — file the CP-9 backlog row
  FIRST. `--place` + caps, the ONE non-leaf whitelist write (`+memory +cpu
  +pids` to the gates slice's `cgroup.subtree_control`, `+` only, per
  RW-35(a)), leaf lifecycle, D-15 write-guard whitelist extension + a test
  proving the refusal of any other path.
- **C9** — close-out: `docs/PROTOCOL.md` complete, README/ATTACH-GUIDE/
  DESIGN.md updates, `CHANGES.md [Unreleased]`, backlog rows CP-2/CP-4..
  CP-9 -> FIXED with commit hashes (CP-4 = `376bb9cb`, CP-5 = `16b01c1c`),
  version string bump `1.0.0` -> `1.1.0` (grep — at minimum
  `lib/serve.py`'s `CGPROFILE_VERSION` constant, confirmed NOT yet bumped
  this session), contract mirror byte-identical, `INDEX.md` regenerated.

## Orientation already banked (do NOT re-read from scratch)

Read in full this session and summarized in the LOG: controller log
RW-30, RW-31, RW-34, RW-35 (the LOG quotes them in full); contract
`RG55-INTERFACE-CONTRACT.md` §8 in full (all of §8.1-§8.9, from `main`).
A successor can start from the LOG's summaries and the quoted rulings
rather than re-reading the controller log/contract from zero — but DO
re-read the design doc `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
(§2 D-17..D-26, §3, §4, A1 D-27..D-29, A2 D-30) — this session only saw it
second-hand via the ruling summaries, never read it directly, and C5-C8
depend on it directly (D-20, D-25, D-27..D-30 are all placement/watch/
slice design decisions this session did not verify against the primary
source).

Also NOT read this session, needed before C5-C8: backlog rows CP-2, CP-6,
CP-7 (CP-4/CP-5 were read in full); P1's `-REPORT.md`/`-LOG.md` beyond the
dispatch-table row (what already exists: `lib/damon.py` KdamondPool,
`lib/subtree.py` token resolver — C8's placement needs the subtree
resolver's pid-discovery shape, C7's watch needs it too for
`--on-stall kill`'s "SIGKILL every pid of the token subtree" path);
`README.md` "Running the daemon"; `docs/ATTACH-GUIDE.md` beyond one grep
hit; `lib/damon.py`, `lib/subtree.py`, `lib/summary.py` (beyond what C2's
grep touched).

## Exact next command

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
cat /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
docker ps --no-trunc --format '{{.Names}}\t{{.Status}}' | grep -c run-gate-vbpub-r2
```
Re-check HOST LOAD first (both named mutation runs may have exited by
now — if BOTH are gone, the r0/r1 lane + r3 + eventually r2 become
available per the handoff; if either is still alive, stay in targeted-
pytest-only mode as session 1 did). Then read the design doc named above
in full, then start C3 (CP-7) — it is the smallest remaining deliverable
and can reuse C2's `initial_effective_limits` value, making it a natural,
fast next commit before tackling C4-C8's larger surface.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the REPORT's C1/C2 evidence tables (tip hashes
`376bb9cb`/`16b01c1c`, test-pass counts, the CP-5 red-first bug-catch
narrative); the "orientation already banked" list above (rulings/contract
§8 are done, don't redo); the exact HOST LOAD recheck commands above.
DROP: the C1/C2 implementation blow-by-blow in the LOG (already shipped
and committed — read it only if debugging a regression in `new_run_id` or
`_on_session_sample`'s event wiring specifically, not for general
context); this session's own tool-call-by-tool-call narration.
