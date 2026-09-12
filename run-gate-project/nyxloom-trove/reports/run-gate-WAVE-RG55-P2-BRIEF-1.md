# run-gate-WAVE-RG55-P2 — BRIEF-1 (successor continuation)

Checkpoint cut per E-008 (~55-58 tool calls used, at a coherent boundary:
green targeted tests + the designed-red selftest verdict + LOG/REPORT +
commit). Written for a FRESH successor implementer dispatched by the
controller — you have no memory of this session; everything you need is
either in this file, already committed in the worktree, or in the frozen
reference docs named below.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there —
**a real hazard hit this session**: it's easy to confuse this path with
the MAIN checkout `/workspaces/vbpub/run-gate-project/`, which looks
identical. Always double check the absolute path before every Edit/Write;
after any batch of edits, run `git -C /workspaces/vbpub status --porcelain
-- run-gate-project/` to confirm the MAIN checkout is still clean (it must
show nothing for this project — if it shows your edits, you hit the same
mistake: `git restore` the main-checkout copies immediately and redo the
edits at the worktree path).

C1 (RG-53) is DONE and committed — see the commit named in
`run-gate-WAVE-RG55-P2-LOG.md`'s "Commit 1" section (hash filled in by the
git command right after this file was written; `git -C
/workspaces/vbpub/.worktrees/rg55-run-gate-client log --oneline -3` will
show it at the top). `tools/coverage_gate.py` now reads `missing_branches`
and refuses a 0/0 diff (`--allow-empty-diff` escape). Full detail: `run-
gate-WAVE-RG55-P2-REPORT.md` (C1 section) and `-LOG.md`.

**Known, EXPECTED consequence you will see immediately**: running
`selftest` right now (before you touch `run-gate.py`) will `exit 2` with
`diff-coverage ERROR: 0 changed executable lines under 'run-gate.py'...`.
This is correct, not a regression to chase — the branch's diff against
`main` so far touches only `tools/`, `tests/`, and two doc files, none of
which is `run-gate.py`. It will resolve itself once C3 (which touches
`run-gate.py` heavily) is committed, since the judged diff accumulates
across every commit on this branch against the wave's `main` base, not
commit-to-commit. Do NOT add `--allow-empty-diff` to `run-gate.toml`'s
`selftest` argv to silence it — the handoff is explicit that argv gains
nothing.

## What to read (in this order, before touching code)

You do not need to re-read what C1 already used (RG-53's own scope,
`tools/coverage_gate.py`, `tests/test_coverage_gate.py`, the backlog
RG-53/54 entries, `CHANGES.md`'s format) — that ground is covered. You DO
need, fresh:

1. `/workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-HANDOFF.md`
   (the original handoff — full read list is authoritative; this BRIEF only
   flags what THIS session skipped, not a replacement for it).
2. `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` — already
   read once this session (all 7 sections), you should too; it is FROZEN,
   do not treat anything in it as negotiable. Pay special attention to §3
   (Summary schema) and §7 (computation rules) before writing
   `ResourceAccumulator`.
3. `run-gate-project/nyxloom-trove/fixtures/rg55/README.md` — already read
   once this session; the by-hand arithmetic is what your tests must
   reproduce byte-for-byte. Note the N=5 nearest-rank gotcha
   (`p90_bytes == peak_bytes` for `container-shared` scope is NOT a bug).
4. **NOT read this session, read it now**: `SPEC.md` `R-29`, `R-36` (ALL
   sub-clauses), `R-39`, `R-40`, `R-41`, the rest of `R-42` (this session
   only read the intro paragraph), and the two documented drifts (the
   stall_timeout "assay lanes only" text after RG-41, the R-08 duplicated
   paragraph — grep for the literal duplicated block, it repeats
   "advisory only), `memory`..." verbatim once).
5. **NOT read this session**: `run_gate.py` lines 152-437 (validators,
   `LANE_KEYS`, `_check_keys`, `[history]`, `load_config` — you need this
   before C3's `[profile]`/`[footprint]` config additions), 640-780 (slice
   resolution), 900-1250 (history store — needed before C4), 1550-1800
   (`duration_stats` et al — needed before C4's `series_stats`
   generalization), 3500-3720 (`build_assay_inner`, `ProgressWatch` —
   needed before C2/C3), 3908-4110 (`LogStreamWatch`), 4117-4300
   (`await_container` — the exact hook points C3 needs), 4331-4560
   (`follow_container`, `promote_follower`, `resolve_inflight` — the
   re-attach path C3 must cover), 4613-4760 (`run_container_lane`),
   4823-4930 (`run_exec_lane`, `run_bare_host_lane` — `run_exec_lane` is
   the one you REWRITE from blocking `subprocess.run` to `Popen`+wait-loop),
   4960-5110 (`usage()`), 5290-5442 (`main`).
6. **NOT read this session**: `tests/test_run_gate.py` targeted ranges
   (fake_docker/fake_docker_executing/ambient_cgroup around 90-240,
   `_fake_cgroupfs` around 3100-3200, the History test classes around
   6740-6900, the RG-35 `fake_docker_stateful` tests, the RG-41
   `LogStreamWatch` tests) — you extend `fake_docker` for the `cgprofile
   ctl` branch and extend `_fake_cgroupfs` for the basic path; read the
   existing shape before extending it.
7. **NOT read this session**: `cmru/run-gate.toml`, `cmru/assay.toml`,
   `cmru/tools/assay/` (the pinned pyz+sha256 to vendor), `cmru/tools/
   coverage_canary.py` AND `scripts/cgroup-profiler/tools/canary-run.sh`
   (two R3 canary shapes — pick one, say why in the REPORT),
   `assay/README.md` (rigor ladder, `base_source = "request"`, judge keys),
   `assay/docs/CONSUMERS.md` (the key that scopes judged source to
   `run-gate.py` only, excluding `tests/`/`tools/`).
8. **NOT read this session**: `scripts/cgroup-profiler/lib/util.py` lines
   70-170 (`read_int`, `read_kv`, `read_pressure`) — you port these
   verbatim (attributed in a comment) for the basic-path sampler.

## Next deliverable: C2 (assay lanes, D-11), then C3

Go in handoff order — C2 before C3, because C2 gives you the assay-r1/r3
gate lanes the handoff wants exercised "once before return", and C3 is
large enough to deserve its own checkpoint cycle rather than being rushed
in behind C2. Full C2/C3 specs are in the handoff §2 — do not re-derive
them here, this BRIEF is a pointer, not a substitute.

One thing worth flagging before you start C2: `cmru/tools/assay/` holds
the pinned `.pyz` + `.sha256` to copy verbatim (verify the sha256 after
copying — do not trust the copy silently). Check `.gitignore` for
`.assay/` and `*.pyz` patterns BEFORE you `git add` — the handoff calls
this out explicitly (a negation may be needed, as cmru did) and it is easy
to add files that then silently fail to `git ls-files`.

## Process notes for you specifically

- Edit tool only, never sed/python rewrite scripts (this session's one
  process error was a WRONG PATH, not a wrong tool — the Edit tool itself
  behaved correctly once pointed at the right file).
- `git -C /workspaces/vbpub/.worktrees/rg55-run-gate-client commit -F
  <msgfile> --only -- <paths>`; never `cd /workspaces/vbpub` first; never a
  bare `git stash`. Both trailers on every commit (see this session's
  commits for the exact form).
- HOST LOAD rules bind you exactly as they bound this session: serial
  `nice -n 19 ionice -c 3` pytest, whole suite at most once per checkpoint
  and once before return, gate containers capped at 2 estate-wide with
  `docker update --cpus=3` right after launch.
- Checkpoint (E-008) binds you too: ARM at ~120k context or ~60 tool
  calls, CUT at the next coherent boundary, write `-BRIEF-2.md` +
  update `-LOG.md`/`-REPORT.md`, commit, return.

## Retention prompt (paste this into your own `/compact` if you need to
compact mid-C3, so the controller's next successor after YOU inherits the
right things)

```
KEEP: the RG55 interface contract's §3 (Summary schema)/§7 (computation
rules) exact field names and formulas; the fixtures/rg55/README.md derived
numbers for whichever golden(s) you are mid-implementing against; the
exact file:line seams in run-gate.py you have already located for
await_container/run_exec_lane/follow_container/promote_follower; which of
C2/C3/C4/C5/C6/C7/C8 are done vs in-progress vs not-started, with commit
hashes for each done one; any decision asks already raised and their
resolution (or their still-open status); the HOST LOAD container-count
state (how many gate containers are currently live, whether the daemon
container exists here — it does NOT in this package, P1 owns it).
DROP: the full text of the interface contract/fixtures README (they are
frozen files on disk, re-readable in one call); the full read-list
rationale from earlier BRIEFs once you've actually done those reads;
resolved sub-threads about which of two R3 canary shapes to pick, once
picked and recorded in the REPORT.
```
