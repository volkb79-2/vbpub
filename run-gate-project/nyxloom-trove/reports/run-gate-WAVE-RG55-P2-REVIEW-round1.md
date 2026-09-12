# run-gate-WAVE-RG55-P2 — adversarial review, round 1

**Verdict: REJECT** (5 blockers; 14 non-blocking findings; 6 decision asks)

**Reviewer:** fresh Opus xhigh session, never a fork of the implementer or the
controller. Blind-first: plan of record → contract §1–§7 → `fixtures/rg55/README.md`
→ controller log rulings → SPEC on `main` and as changed → the full diff, all
before opening `-LOG.md`/`-REPORT.md`/`-BRIEF-n.md`.

**Tip reviewed:** `rg55-run-gate-client` @ `62d9a66a`, base `main` @ `63b928da`,
full diff `main...62d9a66a` (211 files, 26 non-fixture).
**Worktree state at review time:** 2 uncommitted files (the implementer's
in-flight RW-20 survivor triage: `…P2-LOG.md`, `tests/test_run_gate.py` +3 tests).
Everything below refers to the **committed tip** unless stated.
`ac885ed4..62d9a66a` is LOG/REPORT only (verified) — so gate results at
`ac885ed4` transfer to the tip.

**Host rule honoured.** pytest serial under `nice -n 19 ionice -c 3` throughout;
never more than 2 `tester-unified:local` containers estate-wide (P1's r2 lane held
one the whole time); my own containers `docker update --cpus=3` after launch (or
`resources.cpus` at creation) and removed in a `finally`; PSI checked before each
probe (memory full avg10 stayed ≤ 0.7%). `cgprofile-host-daemon` exists but is
**stopped** — the optional daemon-path probe was **skipped** per the dispatch;
P3 integration covers it. `.assay/` was never edited and the r2 lane was never
touched (still alive, 1h12m+ elapsed, at my last check).

**Two side effects I must disclose** (both from the mandated `assay-r1` run):
`.assay/verdict-r1.json` in the P2 worktree was **overwritten** by my
`assay-r1 --allow-dirty` run (it now holds a `NO_MEASUREMENT/DIRTY_TREE` verdict,
not the implementer's earlier PASS), and one content-addressed file was added to
`/workspaces/vbpub/.run-gate/assay-state/…/run-gate-project/`. Both are additive /
regenerable and neither can affect the running r2 lane (verified: r2 still
running; the state dir is content-addressed, append-only).

---

## BLOCKERS

### B1 — Profiling raises, destroys the lane's verdict, and leaks the container (R-04, R-36h, contract §1.3)

The package's central safety promise is false. `ProfilerClient`'s own docstring
(`run-gate.py:986-987`) says "**NEVER** raises, **NEVER** itself changes a lane's
verdict (R-04/R-36h)", and `tests/test_run_gate.py:12435-12436` calls the
caller's "verdict-unchanged behavior … a wiring-level guarantee". **No test
proves it, and it does not hold.** Measured, real docker, an ephemeral
`tester-unified:local` lane that printed `LANE-RAN-OK` and exited 0:

| injected condition | result | container afterwards |
|---|---|---|
| (control) | `main()` returned 0, footprint line printed | removed |
| exception planted in `ProfilerClient._ctl` | `RuntimeError` escapes `main()`, traceback | **LEAKED** |
| exception planted in `BasicSampler._read_container` | `RuntimeError` escapes `main()`, traceback | **LEAKED** |
| daemon answers `start` with `{"ok":true,"contract":1}` (no `session`) | `KeyError: 'session'` | **LEAKED** |
| daemon answers `stop` with `{"ok":true,"contract":1}` (no `summary`) | `KeyError: 'summary'` — **after the lane already passed** | **LEAKED** |

Routes, with file:line:

1. **Bare subscripts on daemon data.** `run-gate.py:1564` `state["session"] =
   start_doc["session"]`; `run-gate.py:1605` `return {"resources":
   stop_doc["summary"], …}`. `_ctl` validates only `contract` and `ok`
   (`run-gate.py:1026-1034`) — a response that is well-formed but missing a key
   (a P1 bug, or minor-version skew *inside* contract major 1, which §1.4
   explicitly permits) crashes the gate.
2. **`UnicodeDecodeError` is not caught.** `run-gate.py:1010`
   (`ProfilerClient._ctl`) and `run-gate.py:1330` (`BasicSampler._read_container`)
   both call `subprocess.run(..., capture_output=True, text=True)`, whose decode
   is **strict**; their `except` clauses cover only `TimeoutExpired`/`OSError`.
   Contract §1.2 declares the daemon's **stderr** to be "free text for
   diagnostics" — no encoding guarantee. Demonstrated:
   `subprocess.run(["sh","-c","printf '{\"ok\":true}'; printf '\\377' >&2"],
   capture_output=True, text=True)` → `UnicodeDecodeError` (a `ValueError`).
   **This exact class already bit this file**: `docker logs` carries
   `errors="replace"` with a long comment at `run-gate.py:5859-5873` and three
   tests pin it (`tests/test_run_gate.py:5381`, `:10578-10592`, `:10847-10851`).
   The new code does not carry the guard.
3. **The blast radius is the `finally`.** `await_container`'s cleanup block calls
   `finish_lane_profiling` → `print_profile_warning` → `print_footprint_line` →
   the `run_record` writes at `run-gate.py:5969-5977` with **no guard**, and
   `docker rm -f` (`:5978`), `proc.terminate()` (`:5979`) and
   `clear_inflight_record` (`:5998`) all come *after*. An exception there
   pre-empts every cleanup step the function exists to guarantee — hence the
   leaked containers above. `run_exec_lane`'s `finally` (`:6793-6803`) has the
   same shape.

**Prescription (all five parts):**
(a) `errors="replace"` on both `subprocess.run(..., text=True)` calls
(`:1010`, `:1330`), matching `:5871-5873`.
(b) `_ctl` catches `Exception`, not just `(TimeoutExpired, OSError)`.
(c) `_ctl` validates the required key per verb (`start`→`session`,
`stop`→`summary`) and degrades to `(None, reason)`; every remaining daemon-data
access becomes `.get()`.
(d) Wrap `start_lane_profiling`, `tick_lane_profiling`, and the whole profiling
block of **both** `finally`s in `try/except Exception` that records
`profile_error` and prints the one warning — the cleanup steps after it must be
structurally unreachable-by-exception.
(e) Tests: plant an exception in `ProfilerClient` and in `BasicSampler`, assert
the lane's exit code is the lane's **and** that `docker rm -f` ran and the
inflight record was cleared.

### B2 — `meta.expected` violates the FROZEN contract §2.2 (P3 integration break)

`profile_meta` (`run-gate.py:1467-1468`) sets `expected` to
`footprint_manifest_lane_peak_median(...)`, which returns **a bare int or `None`**
(`run-gate.py:2361-2374`). Contract §2.2 (`RG55-INTERFACE-CONTRACT.md:76`) requires
`null` **or** `{"memory_peak_median_bytes": int|null, "hot_set_p90_bytes":
int|null, "cpu_cores_avg": float|null, "duration_median_s": float|null}`.

No ruling amends §2.2 (RW-1..RW-20 touch §4.3 only, via RW-11). Worse,
`SPEC.md:1700-1704` (R-44d) documents the scalar **as if it were the contract**,
citing "contract Sec 2.2" — the SPEC text conceals the divergence rather than
flagging it, and the REPORT's decision-asks never raise it. P1's daemon will be
handed `"expected": 746586112` where the frozen contract promises an object.

**Prescription:** emit the four-key object from the manifest lane entry
(`memory_peak_bytes.median`, `hot_set_bytes.p90_median`, `cpu_cores.avg_median`,
`duration_s.median`) — every field already exists in the manifest — **or** obtain
a numbered controller ruling amending §2.2 and correct R-44d. A package's SPEC
may not silently redefine a frozen interface.

### B3 — Contract-mandated behaviour with no oracle: 2 of 10 planted mutants survive the WHOLE suite

Method: `run-gate-project` tar-copied to scratch, one mutant at a time, run against
**all** of `tests/` (control: `3 failed, 1025 passed` — the 3 are copy artifacts:
`TestPointerLinkageEstate::test_cmru_toml_id_matches_orchestration_key` and the two
`TestRG55FixtureByteIdentity` tests need paths I excluded from the copy).

Killed (8): rank `ceil`→`floor`; `_profile_parse_raw_limit` folding `"max"`→`None`;
scope-`container` peak formula swapped to the sampled max; `limit_drift` `or`→`and`;
`_cores_max` fixed `dt=1.0`; `_profile_parse_kv` field-count guard; pressure
`/1e6`→`/1e3`; `pids.peak` last→first. Catchers were mostly
`TestResourceAccumulatorGoldenFixtures::test_container_{shared_,}scope_*`,
`TestProfileParsers::*`, `TestBasicSamplerFinalSample::*`.

**Survivors (full suite, `1025 passed, 0 failed` each):**

- **`_nearest_rank_value` (`run-gate.py:977`)** — replacing
  `sorted(v for v in values if v is not None)` with
  `sorted((v if v is not None else 0) for v in values)` survives. This is exactly
  contract §1.7 ("absent is never zero") and exactly what the function's own
  docstring (`:970-976`) claims to guarantee. Consequence: a run with any failed
  `memory.current` read reports 0-contaminated `p90_bytes`/`median_bytes`.
- **`ResourceAccumulator.finish` (`run-gate.py:1199-1201`)** — removing the
  `max(0, …)` floor on `peak_over_baseline_bytes` survives. Contract §7 says
  "floored at 0". Consequence: a `container-shared` lane whose baseline shrinks
  writes a **negative byte count** into history and into the tracked manifest.

**Prescription:** one direct-call test each. These are precisely the class the
in-flight `assay-r2` lane will surface; closing them now is cheaper than another
RW-20 resume.

### B4 — The named RED-FIRST proof for the `run_exec_lane` rewrite is hollow (proven by experiment)

`TestExecLaneProfilingWiring`'s docstring (`tests/test_run_gate.py:13593-13605`)
argues that the pre-wiring blocking `subprocess.run(argv)` shape means "only the
pre-loop baseline sample from `start_lane_profiling` could ever exist, **i.e. at
most 1**", so `>= 2 samples` is what a red run would have failed. The argument is
wrong: `finish_lane_profiling` → `sample_final()` (`run-gate.py:1610`) supplies a
**second** sample in the `finally` regardless of the loop.

I reverted only the Popen loop (`run-gate.py:6783-6792`) back to
`code = subprocess.run(argv).returncode`, leaving all other C3 wiring intact, and
re-ran the class: **`4 passed`.** The test does not distinguish the rewrite from
the implementation it replaced. The handoff named `run_exec_lane`'s blocking form
as *the* controlled-wrong implementation for this package; that oracle does not
exist.

(The rewrite itself works — my live exec probe took **4** samples in a 12 s exec.
It is the assertion that is non-discriminating.)

**Prescription:** assert `>= 3` samples (or, better, assert the tick count / that
`tick_lane_profiling` was called at least once mid-run) and correct the docstring's
"at most 1" claim to "exactly 2". Re-run the revert to confirm red.

### B5 — Records assert a gate sweep that never happened

`…P2-LOG.md:1267-1272` and `…P2-REPORT.md:783-784` report the final
`selftest` sweep "against `ac885ed4`". The lane history store
(`run-gate-project/.run-gate/history.json`) says the **last and only** recorded
`selftest` is `commit e14615b3…` (C7), `dirty: true`, `started_at
2026-09-12T09:15:00Z` — and `ac885ed4` was committed at 09:17:37. No selftest ever
ran at `ac885ed4` or at the tip. Similarly `…P2-LOG.md:1070` / `REPORT:845` say
`assay-r2` was "dispatched last"; `ps` shows it started **09:18:38**, i.e. before
`assay-r1` (09:19:18), `assay-r3` (09:22:01), `doctor` and all nine live probe
runs — concurrently with the entire final sweep, contrary to the handoff's
one-gate-at-a-time rule.

**Substance is fine — I re-verified it myself** (selftest green at the tip, see
Live probes). So the repair is a **records correction**, not a re-run. It blocks
merge because this estate's merge pipeline consumes these records as the gate
evidence, and "shipped = verified" cannot rest on a claim the store contradicts.

**Prescription:** correct both records to name `e14615b3`/dirty for the selftest
that actually ran and the real r2 dispatch ordering; state the tip's own gate
state (selftest green at `62d9a66a`+triage — this review's run; `assay-r1`/`r3`
green at `ac885ed4`, which is LOG/REPORT-identical to the tip).

---

## NON-BLOCKING FINDINGS

**S1 — `assay-r2`'s `stall_timeout = "20m"` does not exist.** `run_bare_host_lane`
(`run-gate.py:6808-6846`) is a plain `subprocess.run` with **no** `ProgressWatch`,
no `LogStreamWatch`, no timer of any kind; `stall_timeout` is accepted and silently
inert there. Yet `run-gate.toml:96-101` tells the operator "a stuck-but-still-
running mutation sweep is stopped after 20 minutes with no progress line" — on the
lane RW-19/RW-20 sized at 9h+. Related doc inversion in four places: after RG-43's
host/bare-host swap the inert case is **`bare-host`**, and `host` is a real
container lane where `stall_timeout` is live, but `SPEC.md:172`, `SPEC.md:1479-1482`,
`README.md:47` and `usage()` (`run-gate.py:7007`) all say "host/exec". See D6.

**S2 — the profile token is printed verbatim in the `docker argv:` line.**
Measured on both paths, live and `--dry-run`:
`… -e RUN_GATE_PROFILE_SESSION=ff32eb7b44fbe6181a062ace3dd5cb45 …`.
`redact_forwarded_values` (`run-gate.py:3323`) masks only `forward_env` keys.
Attack-surface item 4 asks that the token not appear in logs beyond the record.
Impact is low (a per-invocation nonce, also visible via `docker inspect`), but it
makes the argv disclosure line non-deterministic for every consumer that diffs
gate output. **Fix:** add `PROFILE_TOKEN_ENV` to the redaction key set.

**S3 — `RUN_GATE_PROFILE=""` bricks every invocation.** `run-gate.py:664-667`
refuses any value not in `("on","off")`, and `os.environ.get` returns `""` for an
exported-but-empty variable → `run-gate: RUN_GATE_PROFILE must be 'on' or 'off'
(got '')`, **exit 2**, measured. Every other env knob in this file treats empty as
absent — `run-gate.py:6460` even carries the comment "*empty string counts as
ABSENT — matches log_forwarded_env*". `export RUN_GATE_PROFILE=${SOMETHING}` in a
CI wrapper takes the whole gate down. **Fix:** `if ambient == "": ambient = None`.

**S4 — `target.cgroup` is fabricated and wrong under the systemd cgroup driver.**
`run-gate.py:6579` and `:6768` build `f"/{slice_name}/docker-{id}.scope"`. Both live
probes recorded `/dev-background.slice/docker-….scope`; the real path is
`/dev.slice/dev-background.slice/docker-….scope` (wave plan §1;
`fixtures/rg55/README.md`). A guessed-wrong value is worse than the `null` §1.7
prescribes for a fact this process cannot read (it is in a private cgroup
namespace — which this same package's `cgroup_namespace_is_private` exists to
name). The daemon path gets the real path from `ctl start`.

**S5 — on the ephemeral scope, `baseline_bytes` / `peak_over_baseline_bytes` /
`cpu.seconds` / `cores_avg` are race artifacts (measured).** A lane allocating
120 MiB, self-reporting its own cgroup from inside:

| quantity | container's own truth | run-gate recorded | |
|---|---|---|---|
| `memory.peak` | 137256960 | `peak_bytes` 137256960 | exact ✓ |
| `cpu.stat usage_usec` (lifetime) | 310371 (0.310 s) | `cpu.seconds` 0.093 | **3.3× understated** |
| baseline of a fresh container | ~0 | `baseline_bytes` 135671808 | → `peak_over_baseline_bytes` **1585152 (1.5 MiB)** for a 120 MiB lane |

`s_0` is taken after `docker run -d` + the daemon probe + one `docker exec`, by
which time the container has already allocated. `memory_peak_over_baseline_bytes`
and `cpu_cores_avg` are **two of the five history series and two manifest fields**,
and they feed RG-56. This follows contract §7 literally, so it is a contract
question, not an implementation bug — see **D2**.

**S6 — the daemon-absent warning (today's default state) names the wrong cause.**
Measured: `run-gate: WARNING profiling: \`cgprofile ctl version\` produced
unparsable stdout (exit 1); stderr: Error response from daemon: container
a261796c… is not running — basic in-lane sampling only`. The wave plan §3.4's
intended text ("*WARNING profiler daemon not running — basic in-lane sampling only
(start it: cd scripts/cgroup-profiler && ciu up)*") exists only in `doctor`
(`run-gate.py:5040-5043`). **Fix:** when `docker exec` fails with "No such
container" / "is not running", say so and give the remedy.

**S7 — RW-7 divergence between the two implementations.** RW-7 pinned "the last
read" (contract §7) as the last **successful** read, skipping trailing nulls, on
the daemon side. run-gate takes `samples[-1]["memory_peak"]` (`run-gate.py:1194`)
and `samples[-1].get("pids_peak")` (`:1289`) unconditionally. §7's header requires
both implementations to agree. Narrow (only a *partial* final read triggers it —
`sample_final` already drops total failures), but real. See **D1**.

**S8 — pre-existing false-green in the diff judge, now carrying branch data too.**
`evaluate` builds `cov_by_norm` by `_rel_to_source(k, prefix)` over every coverage
record (`tools/coverage_gate.py`, the dict comprehension above `total_changed_exec`),
and `_rel_to_source` matches the prefix as a **substring**. Reproduced end to end:
with `--source subject.py` and `tests/test_subject.py` in the coverage JSON, both
normalise to `subject.py`, the test file's record wins (last-wins), and a diff with
a genuinely uncovered branch reported `diff-coverage OK: 2/2 changed executable
lines covered (100.0%)`. `run-gate-project` itself is safe (`run-gate.py` vs
`test_run_gate.py` differ by hyphen/underscore), but this judge is copied into
consumers, and RG-53 now reads `missing_branches` through the same collapsed map.
**Fix:** normalise on a path-boundary basis, or refuse a key collision loudly.

**S9 — `tools/canary-run.sh` hazards.** (a) Its tar exclude list
(`canary-run.sh:60-64`) does **not** exclude `.assay/`, so every canary copies the
live mutation lane's state, including a `progress-r2.jsonl` being appended to right
now; `tar` exits 1 on "file changed as we read it" and the script runs under
`set -euo pipefail`. (b) After C4 its find-string occurs **twice** in `run-gate.py`
(`duration_stats` `:2947`, `series_stats` `:2968`) and `text.replace(find, replace,
1)` silently targets whichever is first — correct today, position-dependent, and
`series_stats`' own median is never canaried. **Fix:** add `--exclude=.assay`; make
the canary target unambiguous (anchor on the enclosing `def`), or add a second
canary for `series_stats`.

**S10 — Ctrl-C on an exec lane leaks a `docker exec` client process.** Measured
(token-matched): after `kill -INT`, one `/usr/bin/docker exec … RUN_GATE_PROFILE_
SESSION=<tip token> …` survives; the base's `subprocess.run` killed and reaped it.
The inner container process survives on **both** tip and base, so lane-orphaning is
*not* a regression — only client-process hygiene is. **Positive result in the same
probe:** contract §4.2's Ctrl-C obligation IS met — the `finally` ran,
`sample_final()` was taken and the footprint line printed before the interrupt
propagated. **Fix:** `proc.kill(); proc.wait()` in the `finally`.

**S11 — byte medians can be fractional in a tracked file.** `series_stats`
(`run-gate.py:2967-2969`) averages and `round(..., 3)`s on an even count, so
`memory_peak_bytes.median` can be e.g. `100.5` in history and in
`run-gate.footprint.json`. Contract §7: "bytes are never rounded".

**S12 — `resolve_profile_settings` loses the source of a lane-TABLE override**
(`run-gate.py:692-696`): a `[lanes.<n>.profile]` table changing `enabled`/`damon`
leaves `source` pointing at the config file, so `--dry-run`/`doctor` mis-attribute
it (the bool `profile = false` branch does set it). Also a needless f-string at
`:691`.

**S13 — `profile_token` is never recorded for exec lanes, and SPEC says it is.**
`write_inflight_record` is called only from `run_container_lane`
(`run-gate.py:6558`, `:6591`); `run_exec_lane` writes no inflight record at all.
`SPEC.md:1624-1629` (R-43f) and `:1573-1580` (R-43a) both claim the token is
recorded on `docker run`/**`exec`**. R-43f also omits `profile_daemon`, a third
field the code writes (`run-gate.py:6556`) and both recovery branches require
(`:6070`, `:6348`).

**S14 — documentation and records drift (consolidated; each item has file:line in
the two sweeps I ran).** Highest-value:
- `SPEC.md:555` and `:1644` cross-reference **R-44** for `doctor`'s "profiler"
  check; R-44a–e (`:1656-1721`) are entirely the footprint manifest, and R-30
  (Doctor, `:578-601`) is untouched — the whole C7 deliverable is unspecified.
- R-30 still says the summary counts "**all four**" statuses (`SPEC.md:588-589`);
  the code adds a fifth, `INFO` (`run-gate.py:4960`, summary at `:5087-5091`).
- The RG-53 SPEC amendment promised at `KNOWN_ISSUES_TODO_BACKLOG.md:4166-4168`
  never landed (R-33 untouched), and `SPEC.md:755-757` (R-35a) still states the
  now-false "*scores `0/0` as 100% — a silent false green*".
- `[profile]` and `[footprint]` are documented **nowhere** outside SPEC —
  `CONSUMERS.md` (the adoption contract, which carries `[history]`) has no
  `[profile]` block at all. `RUN_GATE_PROFILE` is documented as `off`-only there;
  `on`-over-lane-opt-out and the by-name refusal appear only in SPEC/`usage()`.
- `CONSUMERS.md:952-959`'s `footprint --write` transcript is fabricated from the
  contract's golden fixture and shows an impossible run: all five lanes of this
  project are `bare-host` → never profiled → `--write` **refuses**, which I
  confirmed live. Its column layout also does not match `print_footprint_report`.
- `CHANGES.md` states no `__revision__ 40 → 41` drift marker (every prior entry
  does; `CHANGES.md:146`), `CHANGES.md:10` still reads "Verified empty as of
  2026-09-11's release" above 103 new lines, and the RG-55 bullet reports
  "84.51 MiB over a **90.14 MiB baseline**" — 90.14 MiB was the *peak*; the
  baseline was 5.62 MiB.
- `usage()` omits `RUN_GATE_PROC_ROOT` while listing its sibling
  `RUN_GATE_CGROUPFS_ROOT` (`run-gate.py:7036`); `run-gate.py:6464` still refers to
  "the test-only kill switch" RW-17 deleted; `SPEC.md:177-178` points the lane
  `profile` key at R-43g (it is R-43h; README gets it right);
  `SPEC.md:48-51`'s "**both** had shipped in code" names three keys, and the
  `resources`/`cpus` half did **not** ship before.
- Backlog RG-53's evidence says "`tests/test_coverage_gate.py` grew from 20 to 23
  tests"; the tip has **27** (the four RW-10 tests from `45f2aa5a` are missing from
  the evidence). Backlog FIXED entries cite **no commit hashes** at all. LOG test
  counts "76 new tests" (`:727`) and "45 new tests" (`:913`) are **64** and **38**.

---

## DECISION ASKS FOR THE CONTROLLER

- **D1 — Does RW-7 bind P2?** It was not in P2's ruling list, but contract §7's
  header says both implementations MUST agree, and today they do not (S7).
- **D2 — Amend contract §7 for scope `container`?** Measured evidence (S5) shows
  `baseline_bytes` and `cpu.seconds` as start-race artifacts on the ephemeral path
  (CPU understated 3.3×, over-baseline 1.5 MiB for a 120 MiB lane). The exact
  values exist for this scope exactly as `memory.peak` already does: `baseline = 0`
  and `cpu.seconds` = the **last** cumulative `usage_usec`. This affects two of the
  five history series, two manifest fields, and RG-56's future admission input —
  and it affects **both** packages.
- **D3 — `meta.expected` (B2):** amend §2.2 by numbered ruling, or require the
  four-key object. Either way `SPEC.md` R-44d must stop describing a scalar as the
  contract.
- **D4 — RW-11 is still only a ruling.** The contract file and its mirror say ten
  basic-path files; the tip's SPEC/README/code say twelve. Confirm P3 owns the
  lockstep amendment (the tip flags this correctly in
  `PROFILE_BASIC_FILES`' comment).
- **D5 — bare-host `stall_timeout` (S1):** refuse it at load, warn once, or accept
  it as documented-inert? It is currently silently inert on a 9h lane whose config
  comment promises otherwise.
- **D6 — the implementer's carried-forward asks.** The assay-r3 canary reading
  (one selector vs the full r1 argv): I verified the canary is genuinely
  discriminating (see Live probes), so the reading holds operationally.
  `footprint --write` refusing a LANE filter is an applied reading, not contract
  text (`REPORT:684-688`) — confirm. `branches_total/missed` scoping is settled by
  RW-6. Handoff §3's "Ctrl-C during `stop`" test was **silently dropped** (no
  `KeyboardInterrupt` test exists anywhere in the RG-55 classes; the string
  "Ctrl-C" appears in neither the LOG nor the REPORT) — I verified the *behaviour*
  live and it is correct, but the oracle is missing.

---

## CLAIMS I COULD NOT VERIFY

1. **The `assay-r2` verdict and the survivor triage.** The lane is still running
   (1h12m+ at my last check; RW-20 resumes until a final verdict). Nothing in the
   records. This is the package's largest outstanding deliverable.
2. **`assay-r1` AT THE TIP.** Refused: `assay: NO_MEASUREMENT/DIRTY_TREE: 2
   uncommitted file(s)` (exit 3) — the implementer's in-flight triage. Mitigated:
   `ac885ed4..62d9a66a` is LOG/REPORT-only (verified), and the store corroborates
   the implementer's r1 PASS at `ac885ed4` (exit 0, clean tree, 09:19:18Z).
3. **The daemon path end to end.** `cgprofile-host-daemon` is stopped; the
   daemon-path probe was skipped per the dispatch. Every daemon-shaped result here
   is fake-client or record-shape evidence. P3 owns the real one.
4. **Whether P1's daemon tolerates a scalar `meta.expected`** (B2) — P1 is a
   separate branch and I did not read it.
5. **Per-commit selftest figures in the LOG** (827/830/834/… passed): every
   selftest ran `--allow-dirty` → history-ineligible → only `latest` survives. They
   are internally consistent with the static test-count deltas, which is
   corroboration, not verification.
6. **README's "still ~47% total" coverage figure** — pre-existing text, not
   re-measured.

---

## LIVE PROBE RESULTS (numbers)

**1. Ephemeral basic-path probe** (throwaway project symlinking the tip's
`run-gate.py`; a `tester-unified:local` command lane holding ~120 MiB for 12 s; no
daemon; `resources.cpus = "3"`):
```
peak_bytes 135372800 (129.1 MiB)   source "memory.peak"   method "basic"
scope "container"   samples 3   interval_seconds 5.16   duration 13.0
baseline_bytes 131846144   peak_over_baseline_bytes 3526656
cpu {seconds 0.098, cores_avg 0.008, cores_max 0.01, throttled 0.0, nr_throttled 0}
host.start/end memory+cpu PSI and loadavg1 all populated; host.slice null
store schema 2; profile_error null; profile_ref null
run-gate: footprint hold: peak 129 MiB (+3 MiB over baseline), p90 129 MiB,
  0.01 cores avg, 0.0 s stalled on memory (full); history median peak - (0 runs)
```
Criteria met: `memory.peak_bytes` ≥ 100 MiB ✓, `source: memory.peak` ✓,
`method: basic` ✓, host PSI filled ✓, footprint line exactly the contract §4.6
shape ✓. The `--cpus 3` argv (RG-48) appears at the right position ✓.
Second run with the payload self-reporting its own cgroup gave the S5 table above
(`memory.peak` matched to the byte; CPU 3.3× understated).

**2. Exec container-shared probe** (a `tester-unified:local` runner pre-warmed to
210 MiB, declared as an `mode = "exec"` environment; lane allocates 80 MiB for 12 s):
```
scope "container-shared"   method "basic"   samples 4   interval 4.118   duration 12.0
baseline_bytes 220323840 (210.1 MiB)   peak_bytes 307597312   source "sampled-max"
peak_over_baseline_bytes 87273472 (83.2 MiB)   p90 307597312   cpu.seconds 0.279
run-gate: docker exec … -e RUN_GATE_PROFILE_SESSION=<token> rg55-review-exec-runner …
```
Criteria met: `peak_over_baseline_bytes` ≥ 70 MiB ✓, `source: sampled-max` ✓,
≥ 2 samples ✓ (4). Token present on `docker exec` in the `forward_env` position ✓,
and no `--cpus` on the exec argv ✓ with the naming-only WARNING emitted ✓.

**3. R-04/R-36h exception injection** — the B1 table above. 4/4 injected
conditions escaped `main()` and leaked the container.

**4. RW-12 tick shape** — `await_container` (`run-gate.py:5892-5906`): tick is
`PROFILE_SAMPLE_SECONDS` only while `basic_active`, and the stall/log-stream poll
is gated on `now - last_poll_at >= PROGRESS_POLL_SECONDS` (monotonic, injectable),
so RG-36/RG-41 stall semantics are structurally unchanged. No thread introduced.
Code-verified; matches RW-12 exactly.

**5. RW-5 / RG-53** (synthetic git repos, same coverage.py 7.15.3):
```
base == HEAD          -> diff-coverage SKIPPED: 0 changed executable lines under
                         'mod.py' between 007eede119 and HEAD (HEAD is on the base)   exit 0
--refuse-empty-diff   -> diff-coverage ERROR: ... naming all three routes          exit 2
changed line, arm untaken -> diff-coverage FAIL: 2/4 (50.0%); branches 1/2 taken
                             mod.py: [8(branch), 10]                              exit 1
"ran, one arm untaken"    -> FAIL: 6/9 (66.7%); branches 2/4 taken
                             mod.py: [8(branch), 10, 15(branch)]                  exit 1
```
**Evasion probe on the real subject:** planted `_rg55_evasion_probe` in
`run-gate.py` with one never-taken else-arm plus a test covering only the true arm,
full suite + `--cov-branch`, then the judge:
`diff-coverage FAIL: 4/5 changed executable lines covered (80.0% < 100.0% floor);
branches 1/2 taken. Uncovered changed lines: run-gate.py: [245(branch)]` — **exit 1.
The judge is real.**

**6. `selftest` on the tip** (once, `--allow-dirty`, verdict read in a separate step):
```
1041 passed, 3 skipped, 2 warnings in 157.39s
diff-coverage OK: 789/789 changed executable lines covered (100.0% ≥ 100.0% floor);
  branches 306/306 taken
run-gate: lane 'selftest' exit 0
```

**7. `assay-r3`** (`--allow-dirty`): `canary: 1 rejected, 0 survived`, **exit 0**.
**Discrimination check (RW-10's other half):** I neutered the canary's replacement
to an identity substitution and re-ran — `canary: 0 rejected, 1 SURVIVED -- the gate
is not discriminating`, **exit 1**. The canary is genuinely discriminating. ✓

**8. `assay-r1 --base main`**: `NO_MEASUREMENT/DIRTY_TREE`, exit 3 (see
Unverified 2).

**9. `history` / `history --json`**: `stats.passes` carries exactly the five new
series plus the three duration keys; no `_`-prefixed private keys leak. ✓

**10. `footprint`** (against a store I synthesised from two real measured records —
3 eligible PASS entries at distinct commits + 1 fail; labelled as synthetic
because the main checkout is dirty and no real run is history-eligible there):
```
  LANE     RUNS  PEAK(med/max)        +BASE   HOT p90  CORES  STALL  DURATION
  quick       3  131 MiB/134 MiB      7 MiB        -    0.01   0.8s     14.0s
manifest: runs 3, completed_runs 4 (the fail counted), scope/method from the newest
profiled entry, last_commit/last_at from the newest completed entry,
top-level keys sorted ✓, indent 2 ✓, trailing newline ✓, schema 1 / revision 41 ✓
footprint --write + LANE filter  -> refused, exit 2 ✓
footprint --write, nothing eligible (the real bare-host store) -> refused, exit 2,
  nothing written ✓
disclosure tail with a manifest present:
  "... history median peak 131 MiB (3 runs) | manifest 131 MiB"  ✓ exact §4.6 shape
```

**11. `doctor`**: 12 checks. `footprint drift` OK within tolerance; forced to 3×
→ `[WARN] footprint drift 'quick': live history median peak 131 MiB vs manifest
393 MiB (67% > 25% tolerance)`; staleness OK → forced old →
`[WARN] ... distilled 254 day(s) ago (> 30 day threshold, [footprint] in ...)`;
`[OK] profile config: enabled=True daemon='cgprofile-host-daemon' interval=1s
damon=True (source: default)`; `[WARN] profiler daemon: 'cgprofile-host-daemon'
not running — ... ('cd scripts/cgroup-profiler && ciu up' starts it ...)`.
INFO class emitted when no manifest exists. ✓

**12. RW-17 (`RUN_GATE_PROFILE`)**:
```
=off  (dry)  -> "profiling disabled ($RUN_GATE_PROFILE=off) — no token, no daemon call, no sampler"
lane profile=false -> "profiling disabled (lane 'profile = false') — ..."
=on over lane profile=false -> token present in argv, plan printed  ✓ (overrides the lane)
=1    -> "RUN_GATE_PROFILE must be 'on' or 'off' (got '1')"   exit 2 ✓
=""   -> "RUN_GATE_PROFILE must be 'on' or 'off' (got '')"    exit 2  ← S3
doctor with =off -> "[WARN] profile config: ... (source: $RUN_GATE_PROFILE=off);
                     $RUN_GATE_PROFILE='off' override active"
live =off run -> {'resources': None, 'profile_error': 'disabled (RUN_GATE_PROFILE=off)',
                  'profile_ref': None}   ✓
```

**13. Ctrl-C on an exec lane**: the `finally` ran (footprint line printed,
`sample_final()` taken) ✓; one stray `docker exec` client survived (S10); the inner
container process survived on **both** tip and base (not a regression).

**14. Vendored judge**: `sha256sum -c` OK, and byte-identical to
`cmru/tools/assay/assay-6.1.1.pyz`
(`3798a5f74908ec6c60d9089dd1a5015151314a07ce2f84379d117327fd519f6f`). ✓

**Cleanup**: every probe container and the throwaway project removed; the main
checkout carries none of my artifacts; the P2 worktree still shows exactly the
implementer's own 2 modified files; one `tester-unified:local` gate container
(P1's) remains; the r2 lane is alive.
