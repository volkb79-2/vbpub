# run-gate-WAVE-RG55-P2 — BRIEF-4 (successor continuation)

Checkpoint cut at the EXACT boundary the dispatch prompt itself names as
the sanctioned mid-C3 cut point: "config + client + accumulator + sampler
with their tests, before the wiring." That boundary is now REAL, tested,
committed code (not a draft) — commit `4d684920`. This brief tells you
what exists, where it lives, and exactly what remains to finish C3, then
C4–C8.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there
(BRIEF-1/2/3's double-checkout hazard warning still applies — verify with
`git -C /workspaces/vbpub status --porcelain -- run-gate-project/` after
every commit).

Git log at hand-off:
```
4d684920 feat(rg55-p2): C3 (partial) -- profiling config + client + accumulator + sampler
62f8a18f docs(rg55-p2): checkpoint after C2 fully done + verified -- LOG, REPORT, BRIEF-3
45f2aa5a fix(rg55-p2): close tools/coverage_gate.py's own diff-coverage gaps found by assay-r1
```

**C1, C1-rework, C2 are ALL DONE** (unchanged from BRIEF-3 — nothing this
session touched them). **C3 is now DONE for its non-wiring half**:

- Config: top-level `[profile]`/`[footprint]` (whole-table shadowing,
  unknown-key refusal), per-lane `profile` key, `resolve_profile_settings`.
- `ProfilerClient` class: `version`/`host`/`status`/`start`/`stop`, JSON-
  first exit-code-second design, never raises.
- `ResourceAccumulator` class: contract §7 arithmetic, PROVEN byte-exact
  against `summary-basic-v1.json` and the README's hand-derived numbers.
- `BasicSampler` class: the docker-exec plumbing + direct host-PSI reads.
- `generate_profile_token()`.
- `tests/fixtures/rg55/` vendored (185 files, byte-identity tested).
- 76 new tests; whole suite 898 passed, 3 skipped; `selftest` diff-coverage
  **100.0% lines (336/336), 100.0% branches (128/128)**, exit 0.
- `assay-r1` PASS (100.0%, 613/613 lines, 158/158 branches) against commit
  `4d684920`; `assay-r3` PASS (canary correctly rejects a broken
  `duration_stats`). `assay-r2` deliberately NOT run (handoff's own rule:
  once, at the very end of the WHOLE package — C4–C8 still remain).

Full detail: REPORT's new "C3 (partial)" section; LOG's "Session 4"
orientation + "Commit 5" sections.

## What NOT to re-read

Everything BRIEF-1/2/3 already marked read/skipped stays that way. Do NOT
re-read: the interface contract, `fixtures/rg55/README.md`, the golden
fixture files themselves, `scripts/cgroup-profiler/lib/util.py`'s parsers
— all already fully absorbed into the shipped code and its docstrings
(each function's docstring names its contract section and, where ported,
its source). If you need the exact arithmetic again, read
`run-gate.py`'s `ResourceAccumulator.finish()` directly — it IS the
contract §7 spec now, verified against the fixtures, not a draft of it.

**Do NOT re-derive the config/parser/client/accumulator/sampler design.**
It exists, is tested, and matches the golden fixtures. If something looks
wrong, that is a real bug to fix in place, not a reason to rewrite from
BRIEF-3's draft (which is now stale — the shipped code differs in two
ways BRIEF-3 did not anticipate, both load-bearing, read the next section).

## Two things BRIEF-3's draft got wrong or incomplete (fixed in the shipped code)

1. **`PROFILE_BASIC_FILES` has 12 entries, not 10.** Contract §4.3's
   literal basic-path file list omits `memory.max`/`memory.high`, but §7's
   `events.limit_drift` rule and the golden `summary-basic-v1.json`
   fixture both require them (a real contract drift, flagged in the
   REPORT's "Decision asks" and "Contract drift" sections — not resolved
   by a controller ruling yet, proceeded per RW-9). If a controller ruling
   arrives correcting the CONTRACT text instead (i.e. saying the two files
   should NOT be read), you would need to also change how `limit_drift`/
   `memory_high_breach` are computed for the basic path (they would become
   permanently `null` or `0` — re-read contract §7 at that point, do not
   guess).
2. **`_profile_parse_raw_limit` is a new parser**, not in BRIEF-3's draft.
   `_profile_parse_int` folds `"max"` to `None`, which loses the
   distinction `limit_drift` needs (a `"max"` → integer transition IS a
   drift event; `_profile_parse_int` would see both as the same `None`
   and never detect it). Used only for `memory.max`/`memory.high`.

## Config layer — what already exists, ready to consume

`resolve_profile_settings(lane, cfg, cfg_path, central, central_path) ->
{"enabled": bool, "daemon": str, "interval": str, "damon": bool, "source":
str}` — call this ONCE per lane invocation (mirrors how `resolve_environment`
is called) to get the fully-resolved profiling policy. `settings["enabled"]
is False` is your "disabled" branch (contract: `resources: null,
profile_error: "disabled"`, no token, no daemon call, no sampler).
`settings["daemon"]`/`settings["damon"]` feed `ProfilerClient.start()`'s
`damon=` kwarg and the daemon container name. `settings["interval"]` is a
budget-grammar string (`budget_seconds()` converts it) — NOT currently
passed anywhere; contract's `--interval` flag on `ctl start` wants a
FLOAT seconds value, so you'll need `float(budget_seconds(settings["interval"]))`
or similar at the call site.

`PROFILE_SAMPLE_SECONDS = 5` (module constant) is the BASIC-path tick
interval — unrelated to `settings["interval"]`, which is the DAEMON's own
internal sampling interval (contract's `--interval` argument, default
"1s"). Do not conflate the two.

## Remaining C3 work (the actual wiring), current line numbers

All line numbers below are CURRENT (post-commit `4d684920`) — re-verify
with `grep -n` before editing regardless, per the standing house rule.

1. **Token generation + append** (contract §4.1, obligation 1):
   `generate_profile_token()` exists. Append
   `["-e", f"{PROFILE_TOKEN_ENV}={token}"]` to argv in BOTH:
   - `run_container_lane` (line 5390's `for key in env.get("forward_env",
     []):` loop — add the token flag right after this loop, before line
     5399's `argv += [env["image"], "bash", "-c", inner]`).
   - `run_exec_lane` (line 5588's `for key in (CGROUP_ENV_VAR,
     *env.get("forward_env", [])):` loop — add after, before line 5592's
     `argv += [name, "bash", "-c", inner]`).
   Record `profile_token` in the inflight record
   (`write_inflight_record(...)` call at line 5430 in `run_container_lane`
   — add a `profile_token` key to that dict) and `profile_session` after a
   successful `ProfilerClient.start()` call (a second write to the
   inflight record, or fold `start()` into `run_container_lane` before
   `await_container` is invoked at line ~5433's return statement — your
   call how to sequence it, per BRIEF-3's own note, still unresolved).

2. **`await_container`'s polling-granularity decision** (line 4858,
   `finally:` block with `docker rm -f` at line 4974) — STILL UNMADE.
   BRIEF-3 named two shapes (shrink the shared poll interval vs. a
   separate background-thread sampler mirroring `LogStreamWatch` at line
   4629) and leaned toward the thread (lower regression risk against this
   function's heavily-adversarially-reviewed stall/re-attach logic,
   `__revision__ = 40`'s own changelog names at least 5 rounds of review
   on this exact function). Read the FULL body of `await_container`
   (4858–4990-ish) before deciding — do not assume BRIEF-3's line-range
   estimate is still exactly right, the function itself is unchanged but
   surrounding code shifted. Whichever shape you pick: the `ctl stop` (or
   `BasicSampler.finish()`) call goes in the `finally:` block at line 4973,
   BEFORE line 4974's `docker rm -f` — non-negotiable per contract §4.2.

3. **`run_exec_lane`'s rewrite** (line 5544, currently ends in a blocking
   `subprocess.run` — find the exact call via `grep -n "subprocess.run"
   run-gate.py` scoped to this function's line range): rewrite to `Popen`
   + `proc.wait(timeout=PROFILE_SAMPLE_SECONDS)` loop, sampling on each
   wake via `BasicSampler` with `--scope container-shared` and baseline
   subtraction (the scope choice alone handles baseline subtraction — see
   `ResourceAccumulator`'s own docstring). stdout/stderr stay inherited;
   exit code unchanged; the R-41 exec mutex (`_open_lockfile`/
   `acquire_shared_locks`, unrelated to this) stays untouched. THIS IS THE
   SIMPLER of the two rewrites (no stall/re-attach/follower machinery) —
   BRIEF-3's own recommendation to do this one FIRST as the red-first
   proof (a test asserting ≥2 samples during a 12s fake exec must fail
   against the current blocking code, pass after) still stands and was
   NOT done this session.

4. **`run_bare_host_lane`** (line 5618): unchanged except the host-pressure
   disclosure line at start and `resources: null, profile_error:
   "bare-host lanes are not profiled (RG-57)"` in its own history record
   construction (find where it builds/returns its result — this function
   is short, read it fresh).

5. **`follow_container`/`resolve_inflight`** (lines 5072/5120): adopt
   `profile_session` from the inflight record on re-attach; a
   collected-after-exit container → `resources: null, profile_error:
   "collected after exit"`.

6. **Disclosure lines** (contract §4.6, exact shapes — quoted verbatim in
   the interface contract and the handoff §2's C3 bullet list): host PSI
   line, profile-session line, footprint line (C4/C5 territory for the
   full footprint line, but the profile-session line belongs here), and
   the WARNING line for any `ProfilerClient` failure reason.

7. **`--dry-run`**: print the profile plan (daemon name, scope, damon,
   token env var name) and start nothing — find the `dry_run` branches in
   `run_container_lane`/`run_exec_lane` (each has one already, for the
   docker argv itself) and add a profile-plan line alongside.

8. **Ctrl-C handling**: bounded, at-most-once `ctl stop` attempt on
   interrupt, mirroring R-36h's "staked-claim" pattern already used
   elsewhere in this file for the history-flush-at-most-once guarantee
   (`grep -n "staked"` or similar to find the existing pattern to mirror —
   this session did not locate the exact site, budget time to find it
   fresh).

## Test debt for the wiring (none written yet)

The handoff's test spec (§3) for the WIRED behavior is entirely
unaddressed: the red-first `run_exec_lane` sampling test, the stall-path
argv-order assertion (`stop` before `rm -f`), Ctrl-C-during-stop, the
re-attach-adopts-session test, `--dry-run` text assertions,
`disabled`/`profile = false` → no-token-no-call assertions. Extend
`fake_docker_executing` with the SAME `CGPROFILE_SHIM_CASE` branch
`fake_docker` already has (they currently diverge — `fake_docker_executing`
overwrites the shim script entirely and does NOT yet include the
cgprofile branch) when a test needs both a real lane exec AND a profiler
ctl call answered in the same invocation.

## Process notes (unchanged from BRIEF-1/2/3, restated)

- Edit tool only; `git -C <worktree> commit -F <msgfile> --only --
  <paths>`; both trailers — match whatever YOUR session's own current
  attribution instruction says (this session's differed from BRIEF-3's
  quoted text — always use the reminder present in YOUR OWN dispatch, not
  a prior brief's copy).
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from
  `<worktree>/run-gate-project`, verdict read in a SEPARATE step. Targeted
  `pytest tests/test_run_gate.py -k <cluster>` while iterating (whole
  suite ~114s).
- `assay-r1`/`assay-r3` need a CLEAN tree (commit first) — a dirty-tree
  attempt correctly refuses with `NO_MEASUREMENT/DIRTY_TREE`, not a bug.
- HOST LOAD (handoff §6): 8 cores shared with a production game server;
  serial pytest only; at most 2 gate containers estate-wide (`docker ps
  --format '{{.Image}}'` for `tester-unified:local` first); `docker update
  --cpus=3` right after launching any container for the live acceptance
  probes (handoff §4, still fully unstarted — needs C3's wiring half).
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary, write `-BRIEF-5.md`, update LOG/REPORT, commit,
  return.

## Retention prompt (paste into your own `/compact` if you need to compact mid-session)

```
KEEP: C3's non-wiring half is DONE and committed (4d684920) — config,
ProfilerClient, ResourceAccumulator (proven byte-exact against golden
fixtures), BasicSampler, generate_profile_token(), 76 tests, 100%
line+branch diff-coverage, assay-r1/r3 PASS. The two things BRIEF-3's
draft got wrong (PROFILE_BASIC_FILES has 12 files not 10; a new
_profile_parse_raw_limit parser exists). The contract drift decision ask
(memory.max/memory.high) and its resolution (read them anyway, fixtures
are the tie-breaker). The await_container polling-granularity decision
(STILL UNMADE — BRIEF-3's lean toward a background thread). Current line
numbers for the 8 remaining wiring items above, once you've re-verified
them. Any new decision asks and their resolution. HOST LOAD container-
count state. Commit hashes for every prior commit (C1/C1-rework/C2/C3-
partial), all done, don't re-verify unless something looks broken.
DROP: the full config/parser/client/accumulator/sampler source code
listings (re-readable in one call from run-gate.py directly — it IS the
spec now); the byte-exact verification narrative (already proven, keep
only "matches the golden fixtures, commit 4d684920" as the pointer).
```
