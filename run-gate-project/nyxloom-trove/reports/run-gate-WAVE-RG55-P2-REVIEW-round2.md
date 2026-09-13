# run-gate-WAVE-RG55-P2 — adversarial review, round 2 (fix verification)

**Verdict: ACCEPT** — all five round-1 blockers are fixed and independently
re-verified by re-running my own round-1 probes verbatim; RW-21 and RW-23(a–g)
are correctly applied; the tip's own gates are green on a clean tree. Two
follow-ups below are ACCEPT conditions on the RECORDS, not on the code, and
neither blocks the merge.

**Reviewer:** same session as round 1 (fresh Opus xhigh, never a fork).
**Tip verified:** `rg55-run-gate-client` @ `a7a84e09`
(fix `5f91f308`, coverage follow-up `8faaf969`, survivor tests `a0edc4ae`,
records `a7a84e09`, after a clean `merge main` `a55e4d3e`).
**Base:** `main` @ `3b75e1df` (= the merge-base; the branch is fully up to date
with main, so the review diff is exactly `3b75e1df...a7a84e09`).
**Worktree state:** CLEAN this round — so, unlike round 1, `assay-r1` could
actually run at the tip.

**Host rule honoured.** pytest serial under `nice -n 19 ionice -c 3`; the live
probe held one `tester-unified:local` container, capped at creation via
`resources.cpus = "3"` and removed in a shell `finally`; PSI checked before each
probe (memory full avg10 ≤ 0.14% throughout); at no point more than one gate
container estate-wide. `cgprofile-host-daemon` is still stopped — the daemon-path
probe stays skipped (P3 owns it). **RW-22 honoured:** `.assay/` untouched, the
`62d9a66a` r2 process never signalled and still alive at the end of this round;
I ran no `assay-r2`.

---

## Blocker verification

### B1 — profiling raises, kills the verdict, leaks the container → **FIXED**

Re-ran the round-1 injection harness **verbatim** (same script, same probe
project, same real `tester-unified:local` lane printing `LANE-RAN-OK`):

| injected condition | round 1 | round 2 |
|---|---|---|
| (control) | returned 0, container removed | returned 0, container removed |
| exception in `ProfilerClient._ctl` | `RuntimeError` escaped, **container leaked** | **returned 0**, container removed, record `profile_error='profiling crashed unexpectedly: planted exception inside ProfilerClient'` |
| exception in `BasicSampler._read_container` | `RuntimeError` escaped, **container leaked** | **returned 0**, container removed, record names the crash |
| `start` reply without `session` | `KeyError: 'session'`, **container leaked** | **returned 0**, container removed |
| `stop` reply without `summary` | `KeyError: 'summary'`, **container leaked** | **returned 0**, container removed |

In every row `LANE-RAN-OK` was printed, `.run-gate/inflight/` held only its own
`inflight.lock` (the record itself cleared), and `docker ps -a` showed no
`run-gate-vbpub-quick-*` survivor.

The **non-planted** routes, re-tested at the real-subprocess level through a
fake `docker` shim (not a monkeypatch — these go through the shipped `_ctl` and
`_read_container`):

```
\xff on daemon STDERR       -> version parsed OK, no raise          (errors="replace", run-gate.py:1057)
\xff on daemon STDOUT       -> (None, "... produced unparsable stdout ...")
start missing 'session'     -> (None, "... missing the required 'session' key (contract Sec 1.4 minor-version skew?)")
stop  missing 'summary'     -> (None, "... missing the required 'summary' key ...")
\xff from the lane cgroup   -> memory_current None, no raise        (errors="replace", run-gate.py:1435)
```

Structural fixes read and confirmed: `errors="replace"` on both
`subprocess.run(..., text=True)` calls (`run-gate.py:1057`, `:1435`); `_ctl`'s
`except Exception` broadening (`:1064`); the per-verb `_REQUIRED_KEY` validation
(`:1042`, `:1093-1097`); `.get()` at both former bare subscripts (`:1682`,
`:1726`); and the `try/except Exception` wrappers around the profiling block of
**both** `finally`s and both `start_lane_profiling` call sites, with
`docker rm -f` / `proc.terminate()` / `clear_inflight_record` now structurally
unreachable-by-exception.

*Observation, not a defect:* when `_ctl` itself is bypassed (only possible in a
test harness like mine), a daemon-path `stop` returning no `summary` yields
`resources: null` with `profile_error: null` — "no error, no data". In
production `_ctl`'s own validation fires first and the reason is named, so this
is unreachable; worth one line in the record if anyone revisits
`finish_lane_profiling`'s daemon branch.

### B2 — `meta.expected` violated frozen contract §2.2 → **FIXED**

`profile_meta` now calls `footprint_manifest_lane_expected`. Driven directly:

```
manifest present, lane known : {"memory_peak_median_bytes": 746586112, "hot_set_p90_bytes": 190000000,
                                "cpu_cores_avg": 1.3, "duration_median_s": 316.2}
manifest present, lane absent: None
no manifest                  : None
partial lane entry           : {"memory_peak_median_bytes": 1, "hot_set_p90_bytes": null,
                                "cpu_cores_avg": null, "duration_median_s": null}
KEY SET MATCHES CONTRACT §2.2: True
```

Exactly the four contract keys, `null` for the whole object when unknown,
per-field `null` for a partial manifest entry. `SPEC.md` R-44d rewritten to
describe the object and to say what it used to emit. The P3 integration break is
gone.

### B3 — two mutation survivors → **one genuinely closed, one proven EQUIVALENT**

Both mutants replanted and re-run against the **full** `tests/` suite in a
scratch copy (control: `3 failed, 1066 passed` — the 3 are the same copy
artifacts as round 1: `TestPointerLinkageEstate::test_cmru_toml_id_matches_orchestration_key`
and the two `TestRG55FixtureByteIdentity` tests, which need paths the copy omits).

- **M2** (`_nearest_rank_value` treating `None` as `0`): **KILLED** —
  `6 failed, 1063 passed`, the three new failures being
  `TestNearestRankValue::test_none_values_are_excluded_not_zeroed`,
  `::test_all_none_returns_none`, `::test_mixed_none_matches_readable_only_ranking`.
  Contract §1.7 now has a real oracle. ✓

- **M5** (removing `max(0, peak_bytes - baseline_bytes)`): **still survives**
  (`3 failed, 1066 passed` — byte-identical to the control). I then proved *why*,
  and it is **not** a test gap: in the only scope where that expression runs
  (`container-shared` — RW-21 makes scope `container` return `None`
  unconditionally), `peak_bytes = _max_or_none(mem_current)` is the maximum over
  a list whose **first element is the baseline**, so `peak_bytes >= baseline_bytes`
  holds by construction. Exhaustive check over every readable/`None` combination
  of 1–4 samples: **340 combinations exercised, 0 where `peak - baseline < 0`.**
  `max(0, …)` is dead defensive code and M5 is an **equivalent mutant** — no
  test can kill it.

  The new `test_peak_over_baseline_bytes_is_floored_at_zero_container_shared`
  does not close it, and its own body says so: it sets baseline 900 MiB /
  later sample 700 MiB, then comments *"baseline == peak here (the max IS the
  baseline sample), so this alone would not distinguish 'floored' from 'never
  negative in the first place'"* before asserting `== 0` anyway. The LOG's claim
  that B3 is closed by "a direct call proving the `max(0, ...)` floor with a
  shrinking baseline" is therefore inaccurate, and the premise in the test's
  comment ("a sibling process frees memory between `docker exec` and the first
  sample") cannot produce a negative either — the baseline *is* the first sample,
  so anything before it is already inside it.

  **Substantively B3 is closed** (no behaviour at risk in either case), which is
  why this does not hold the merge. See ACCEPT condition 2.

### B4 — the red-first proof was hollow → **FIXED, and now genuinely red-first**

The assertion was raised from `>= 2` to `>= 3` (`tests/test_run_gate.py:14188`,
with the docstring corrected from "at most 1" to "exactly 2"). I re-ran my own
revert experiment, this time as a *faithful* minimal revert — only the tick loop
replaced by a single blocking `proc.wait()`, leaving `proc` and the S10 tail
intact:

```
TestExecLaneProfilingWiring against the reverted implementation:
E       assert 2 >= 3
FAILED  ...::test_exec_lane_samples_at_least_twice_during_a_slow_command
1 failed, 7 passed
(control, real implementation: 8 passed)
```

Exactly 2 samples — the number I predicted in round 1 — and the test is now the
only thing in the class that discriminates. The oracle is real.

### B5 — records asserted a gate sweep that never happened → **FIXED**

Both claims corrected in place with explicit **B5 correction** callouts that
name the evidence rather than quietly rewriting history:
- `…P2-LOG.md:1280-1293` — the "Final-commit gate sweep" heading no longer says
  "against `ac885ed4`"; the callout states the store's actual last `selftest`
  (`e14615b3`, dirty, 09:15:00Z, before `ac885ed4` existed), explains *why* only
  `latest` ever advances under `--allow-dirty`, and credits the round-1 reviewer
  for independently re-verifying the number.
- `…P2-REPORT.md:858-870` — the `assay-r2` section no longer says "dispatched
  last"; the callout gives the real `ps` start time (09:18:38, before r1/r3/doctor
  and all nine probe runs), states plainly that it ran concurrently with the final
  sweep, and distinguishes the sequencing violation from resource contention.

Both are honest, specific, and match the evidence I collected in round 1.

---

## RW-21 (contract §7 amendment) — correctly applied, verified four ways

**Code.** `ResourceAccumulator.finish` branches on `absolute = (scope ==
"container")` for `cpu.seconds`/`throttled_seconds`/`nr_throttled`
(`run-gate.py:1298-1313`), every `pressure.*` field (`:1324-1329`), every
`faults.*` field (`:1339-1343`), and `events.oom_kill`/`memory_high_breach`
(`:1351-1355`); `peak_over_baseline_bytes` is unconditionally `None` there
(`:1266-1271`). `limit_drift`, `cores_max`, `memory.peak_bytes` and every
`host.*` field are untouched in both scopes — exactly the contract's own
exception list. RW-7 is applied through a new `_last_successful` helper at every
"last read" site, including `pids.peak` in **both** scopes.

**Goldens — reproduced by my own independent recompute**, not by trusting the
suite: I re-read `tests/fixtures/rg55/frames/0..4` with the shipped parsers, fed
them into `ResourceAccumulator` at 1 s spacing, and compared to the vendored
goldens. `summary-basic-v1.json` (container-shared) and
`summary-basic-container-v1.json` (container) both reproduce **field-for-field**
(the only difference was a leading `/` on the `cgroup` string I passed in by
hand). The two daemon-side goldens were checked for RW-21 consistency instead
(run-gate copies them verbatim from `ctl stop`):

```
summary-container-v1  scope=container        peak_over_baseline=None  mem_full_stall=5.3  pgmajfault=1032
summary-v1            scope=container-shared peak_over_baseline=209715200 mem_full_stall=4.8 pgmajfault=32
```

`diff -rq` confirms the vendored fixture tree is byte-identical to
`nyxloom-trove/fixtures/rg55/` (README aside). The amendment is proven to
*change numbers*, not relabel them, by
`test_container_scope_absolute_counters_differ_from_delta` (5.3 vs 4.8, 1032 vs
32, 11 vs 1, with `limit_drift` and `cores_max` asserted identical).

**Live.** The ephemeral basic-path probe, with the lane self-reporting its own
cgroup truth from inside the container:

| | round 1 (delta rule) | round 2 (RW-21) | container's own truth |
|---|---|---|---|
| `memory.peak_bytes` | 137256960 | **137277440** | `memory.peak` = 137277440 ✓ exact |
| `cpu.seconds` | 0.093 (**3.3× low**) | **0.292** | `usage_usec` = 321422 → 0.321 |
| `peak_over_baseline_bytes` | 1585152 (meaningless) | **null** ✓ |  |
| `cores_avg` | 0.007 | **0.022** | 0.321 / 13.0 = 0.025 |

The residual 0.292 vs 0.321 is the CPU the container spent *after* run-gate's
last successful read (the lane's own trailing `echo`/`grep`/`cat`), which is
correct by construction. The 3.3× understatement that motivated D2 is gone.

---

## RW-23 items (a–g) — all applied, each re-tested

| | ruling | evidence |
|---|---|---|
| a | RW-7 applies to all "last read" fields | `_last_successful` (`run-gate.py:1003`) used for `memory.peak_bytes`, `pids.peak`, and every RW-21 absolute |
| b | bare-host `assay-r2` `stall_timeout` removed | `run-gate.toml` has **no** `stall_timeout` anywhere; `:98-109` explains why, and gets the RG-43 host/bare-host direction right |
| c | `RUN_GATE_PROFILE=""` = absent | `run-gate.py:665-672` (`if ambient == "": ambient = None`) |
| d | token redacted in the header line | **live**: `-e 'RUN_GATE_PROFILE_SESSION=<redacted>'` in the real `docker argv:` line; `redact_forwarded_values` now always includes `PROFILE_TOKEN_ENV` (`:3479`) |
| e | basic path never fabricates `target.cgroup` | **live**: record shows `target.cgroup: None`; `cgroup = None` at both call sites (`:6762`, `:6965`) with the measurement in the comment |
| f | canary copies without `.assay/`/`.run-gate/`/`coverage.json` | `tools/canary-run.sh:67-69` |
| g | `coverage_gate` record-lookup false green fixed if small, else RG-58 | **fixed** (~15 lines): `_rel_to_source` now checks the LEADING boundary too. My round-1 reproduction re-run: the case that reported `diff-coverage OK: 2/2 … 100.0%` now correctly reports `FAIL: 2/4 (50.0%); branches 1/2 taken … subject.py: [8(branch), 10]`, exit 1, while `run-gate-project-other/mod.py` still does not match prefix `run-gate-project`. No RG-58 needed. |

RW-23's final clause — *"all listed doc drift fixed in the round"* — is the one
part only **partially** met; see the residues section.

---

## Gates on the tip (clean tree; every verdict read in a separate step)

```
selftest   : 1083 passed, 3 skipped;  diff-coverage OK 873/873 lines (100.0%),
             branches 334/334 taken;  exit 0
assay-r1   : r1: PASS (exit 0)          (--base main, clean tree, no --allow-dirty)
assay-r3   : canary: 2 rejected, 0 survived; exit 0
             (median-not-mean, median-not-mean-series-stats — S9b closed:
              `series_stats` now has a canary of its own)
```

This is the first time `assay-r1` has run at a tip of this branch without
`--allow-dirty`, and the first time any gate has run at the tip under review.

---

## S11 / S13 — the judgement the controller asked for

**S11 (fractional byte medians) → BACKLOG, not a round-3 fix.** I confirmed it
still reaches a *tracked* file: two PASS entries with peaks 100 and 101 produce
`"memory_peak_bytes": {"median": 100.5, "max": 101}` in
`build_footprint_manifest`'s output, and the implementer's own committed probe
manifest already carries `"median": 907264.0`. Contract §7 says "bytes are never
rounded". The implementer's deferral rationale is right: the fix needs a product
decision (even-count byte median → floor, nearest, or report the pair), which is
a numbered ruling, not a unilateral edit in a fix round. **File it as a backlog
row** so it outlives the wave.

**S13 (exec lanes record no token/session) → SPLIT.** The *code* half — an exec
lane writes no inflight record at all, so it has no recovery path if its client
dies mid-run — is a new code path and correctly deferred: **backlog**. The *doc*
half is different: `SPEC.md:1624-1629` (R-43f) still reads "`profile_token`
(recorded on `docker run`/**`exec`**, before any daemon call)", i.e. a normative
rule asserting a field the code never writes on that path. That is a two-word
correction to a rule, and SPEC is normative here. **Recommend fixing the R-43f /
R-43a wording** — in the merge commit or a trivial follow-up — rather than
carrying a false normative statement; it does not justify a round 3 on its own.

---

## Pre-adjudicated residues — assessed, none hides a real defect

- **S1 / D5** (should a bare-host lane declaring `stall_timeout` be refused or
  warned at load?) — the concrete hazard is gone: the misleading key and its
  comment are removed from `assay-r2` (RW-23b) and the new comment states the
  inert-vs-live rule correctly. What remains is a genuine open product question,
  correctly deferred. No hidden defect.
- **S6** (the live-run daemon-absent warning names "unparsable stdout" instead of
  "the daemon container is not running") — still exactly as in round 1; I saw the
  same text on every probe this round. `doctor`'s text is good. Cosmetic, but it
  is the message every consumer will see until P3 starts the daemon, so it is
  worth a backlog row rather than silence. No hidden defect.
- **S11 / S13** — above.
- **S14 remainder** — I spot-checked the highest-value items and they are indeed
  unfixed: `SPEC.md` still says the doctor summary counts "all four" statuses
  while the code emits five (`INFO`); `doctor`'s "profiler" check is still
  cross-referenced to **`R-44`** at `SPEC.md:555` and `:1644` (R-44 is entirely
  the footprint manifest — the C7 deliverable still has no rule id); R-35a still
  carries the "scores `0/0` as 100% — a silent false green" sentence that RW-5
  made false; the `host/exec` ↔ `bare-host/exec` inversion survives in `SPEC.md`
  (×2), `README.md` and `usage()`; `CONSUMERS.md` still has no `[profile]` block.
  None of these hides a behavioural defect — but the inversion is the same wrong
  statement that produced S1 in the first place, and a rule cross-reference
  pointing at the wrong rule is the kind of drift D-12 put on this wave's
  agenda. Deferred is acceptable; **undocumented** is not (condition 1).

---

## ACCEPT conditions (records only — do not hold the merge)

1. **File the deferred residues as backlog rows** before the wave closes. Today
   S1/D5, S6, S11, S13's code half and the S14 doc-drift list exist only inside
   `…P2-LOG.md` / `…P2-REPORT.md`; no `RG-58`+ row was created (the backlog's
   highest id is still RG-57). A wave report is not a backlog, and this estate's
   own rule is that a finding about a tool lands in that tool's backlog.
2. **Correct the B3/M5 claim, and pre-write its r2 justification.** The
   `max(0, …)` survivor is an *equivalent mutant* — proven here over 340
   sample combinations — not a closed oracle gap, and the test that claims to
   close it asserts a tautology its own comment acknowledges. Since RW-22 sends
   the final `assay-r2` at the ACCEPTed tip, that run will report this survivor
   again; it deserves the correct justification ("unreachable by construction:
   `peak_bytes` is a max over a list whose first element is the baseline"), or
   the dead `max(0, …)` simply deleted so no mutant exists.

---

## Claims I still cannot verify

1. **The `assay-r2` verdict.** Per RW-22 the lane on `62d9a66a` continues to its
   own verdict and the final r2 runs once on this ACCEPTed tip; I ran none and
   touched nothing under `.assay/`. The r2 process was alive at the start and at
   the end of this round.
2. **The daemon path end to end.** `cgprofile-host-daemon` is still stopped;
   everything daemon-shaped here remains fake-client / golden-shape evidence.
   P3 owns the real integration probe.
3. **P1-side agreement on the new `meta.expected` object** — P1 is a separate
   branch; I verified the shape against the frozen contract text only.

## Cleanup

Probe project and all probe containers removed; the main checkout carries no
artifacts of mine beyond this round file; the P2 worktree is clean (`git status`
empty); zero `tester-unified:local` gate containers left running; the r2 process
untouched and alive.
