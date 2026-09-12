# run-gate-WAVE-RG55-P2 — BRIEF-7 (successor continuation)

T1-T5 (RW-24 fix, RG-58..RG-61 backlog, B3/M5 correction, SPEC R-43f/a,
RW-25 `jobs = 2`) are FULLY DONE and committed on `rg55-run-gate-client`.
Selftest was green (100%/100%) after every one of those commits. What's
left is entirely T6 (the final `assay-r2` mutation lane), its survivor
triage, T7's final gates, and the merge/release. This brief hands you
exactly that, plus a genuine unresolved anomaly (RW-56/RW-58) you do not
need to re-diagnose — only work around per the rule already issued.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Branch tip right
now (I switched back onto it to write this brief):

```
647a2cc6 docs(rg55-p2): record T6's first assay-r2 run -- BUDGET_EXCEEDED at 172/283 (RW-40)
186461de build(rg55-p2): RW-25 -- assay-r2 jobs=2 for the final close-out mutation run
69d46544 docs(rg55-p2): SPEC R-43f/R-43a -- profile_token is not recorded on the exec-lane path
82094849 docs(rg55-p2): B3/M5 correction -- M5 is an equivalent mutant, not a closed oracle gap (ACCEPT condition 2)
cc19e1f0 docs(rg55-p2): file review round-1 deferred residues as backlog RG-58..RG-61
```

**Critical: the 281/283-judged mutation record set is keyed to commit
`186461de` (RW-25's own commit), NOT to `647a2cc6`.** assay 6.1.1's B088
fix keys resume identity on the whole judged tree, and `647a2cc6` (a
docs-only LOG commit) already invalidated the cache once (see "Run
history" below). **You must `git switch --detach 186461de` before
launching any further `assay-r2` pass — do not commit anything while
detached.** When you finally get a passing verdict (or accept run 2's
evidence per RW-58, below), `git switch rg55-run-gate-client` to come back
before doing any triage commits.

## Run history (all four attempts, `.assay/verdict-r2.json` + `.assay/progress-r2.jsonl`, all against commit `186461de`)

1. **First run** (started `12:28:58Z`, ended `16:28:52Z`): `BUDGET_EXCEEDED`/
   `LANE_TIMEOUT`, exit 4. `candidate_total: 283`, `killed: 157, survived:
   15, budget_exceeded: 111` (172/283 judged). Recorded in LOG already
   (RW-40 read).
2. **Second run's resume saga** (RW-41): the first `--resume` relaunch (at
   `647a2cc6`, a LOG-only commit) showed `rejected_total: 174,
   resumed_total: 0` — the tree-identity change invalidated everything;
   killed on the controller's own instruction rather than let it
   re-execute blind. Switched to `git switch --detach 186461de` (the tree
   the 174 records were actually judged on) and relaunched; that resume
   showed `rejected_total: 18, resumed_total: 156` (the 18 being exactly
   the candidates whose state got overwritten by the killed bad-resume
   attempt — confirms the mechanism precisely). That run then hit
   `BUDGET_EXCEEDED` again at `started 17:02:14Z, ended 21:00:56Z`
   (~3h58m): `killed: 263, survived: 18, budget_exceeded: 2` — **281/283
   judged, only 2 candidates never reached.**
3. **Third run** (relaunch #2 at the same detached `186461de`, per RW-41's
   pre-authorized "relaunch once more if budget trips again"): started
   `21:02:03Z`, ended `21:05:04Z` (~3 min) — **`FAIL`/`COMMAND_FAILED`,
   exit 1**, and only during the **R0 baseline** (`python3 -m pytest tests
   -q --cov=. --cov-branch --cov-report=json:coverage.json`, returncode 1
   — genuine test failures, not a crash). No candidate events at all this
   run. Diagnosed live with the controller (RW-56): a by-hand run of the
   exact baseline command reproduced 3 failures, all in
   `tests/test_run_gate.py::TestExecModeMutex` — `IsADirectoryError:
   '/tmp/run-gate-exec-myproj-dev1-<pid>-runner.lock'` at lines 3949/3968,
   and a "DID NOT RAISE" at line 3997 because `main()` hit the
   unusable-lock infra path before the planted `RuntimeError`. Root cause:
   `TestExecModeMutex::test_unusable_lock_path_is_infra_failure_not_traceback`
   (line 4036) does `self._lock_path().mkdir()` (line 4053) and never
   cleans it up; since the lock name is pid-scoped, every later `os.open`
   of that path in the SAME pytest process fails once that test has run.
   `pytest-randomly` is installed and active (tracked as **RG-62** in P4's
   backlog), so whether the planting test runs before its three siblings
   is a coin flip per process — runs 1 and 2 happened to pass, this one
   didn't. **This is an order-dependent pre-existing test defect, not a
   regression from T1-T5 and not host contention** — P4's branch already
   fixes this family (RW-46a: isolated lock dir + cleanup). A fix commit
   in *this* tree would invalidate the 281 judged records (RW-41 B088
   semantics again), so the tree stays at `186461de` — the remedy is
   procedural (below), not a code fix here.
4. **Fourth run** (relaunch #3, this time with `PYTEST_ADDOPTS="-p
   no:randomly"` exported into the launching shell before `nohup
   ./run-gate.py --base main assay-r2 …`, per RW-56's prescribed remedy):
   started `21:21:54Z`, ended `21:24:32Z` (~166s) — **`FAIL`/
   `COMMAND_FAILED`, exit 1 again**, same baseline command, same
   ~2.5-minute-then-fail shape. **RW-58 finding: `PYTEST_ADDOPTS` works
   correctly by hand (see below) but assay 6.1.1 evidently does NOT
   propagate it into the baseline/candidate subprocess environment** — an
   unconfirmed mechanism, not yet root-caused. **You do not need to
   re-diagnose this** — RW-58 already issued the working procedure (see
   "Remaining sequence" below). If you want to chase it anyway: check
   assay 6.1.1's runner for how it constructs the subprocess `env=` for
   the baseline/candidate commands (whether it passes `os.environ` through
   or builds a filtered/explicit env dict that drops `PYTEST_ADDOPTS`).

## By-hand baseline results (both bare, niced, from `/workspaces/vbpub/.worktrees/rg55-run-gate-client/run-gate-project`, tree detached at `186461de`)

- **Without the fix** (plain `python3 -m pytest tests -q -p no:cacheprovider
  -rfE --no-header`, log `p2-baseline-byhand.log`): 3 failures, all
  `TestExecModeMutex`, as described above (RW-56's diagnosis).
- **With `PYTEST_ADDOPTS="-p no:randomly"` exported** (log
  `p2-baseline-byhand2.log`): **1087 passed, 3 skipped, 153.9s — clean.**
  Confirms the remedy works when applied directly to the pytest
  invocation; it is assay's own subprocess launch that isn't inheriting
  the variable, per RW-58.
- Additionally verified (controller RW-56 step 1): three consecutive
  by-hand runs of just `PYTEST_ADDOPTS="-p no:randomly" python3 -m pytest
  tests/test_run_gate.py -k TestExecModeMutex -q -p no:cacheprovider` —
  **all three green** (6 passed each), proving the ordering fix is
  deterministic, not itself flaky.

## RW-58 ruling (already in the controller log — the procedure you follow)

Relaunch the `assay-r2` pass on the tree detached at `186461de` **up to
three more times**. The first `PASS` is the evidence — use it. **If all
three fail again in the baseline** (same `COMMAND_FAILED` shape), **run
2's verdict (281/283 judged: killed 263, survived 18, budget_exceeded 2)
is accepted as the mutation evidence with a disclosure**, and 23.7.0 is
released on that basis. Either way, when you get to the REPORT's mutation
section, disclose: (a) if a pass eventually went green, whether
`PYTEST_ADDOPTS` was somehow honored or the failure simply didn't recur
(host-load loop the dice differently each time, since the defect is a
random-seed coin flip); (b) if run 2 is accepted instead, say so plainly
and name RG-62/RW-46a as the reason a clean R0 rerun could not be
obtained this session.

Command for each relaunch attempt (tree must be clean and detached at
`186461de` each time — `git status --short` empty, `git rev-parse HEAD`
== `186461de5ef5e58031c10a65c3ebf1087dfae76f`):

```
nohup nice -n 19 ionice -c 3 ./run-gate.py --base main assay-r2 \
  > <scratchpad>/t11-assay-r2-final<n>.log 2>&1 < /dev/null & disown
```

(Exporting `PYTEST_ADDOPTS` first is harmless but per RW-58 does not
change the outcome — do it anyway in case assay's env handling is
non-deterministic across runs, but do not rely on it and do not spend
time re-diagnosing why it doesn't propagate unless you have host budget
to spare.) Arm an untracked watcher (`until ! kill -0 <pid>; do sleep 15;
done`), read `.assay/verdict-r2.json` in a separate step each time. Check
`docker ps` and `/proc/pressure/{memory,cpu}` before each relaunch out of
general host discipline, but per the controller's own RW-56 ruling this
specific failure is NOT contention-gated — only wait for memory PSI `full
avg10` < 5 before relaunching, nothing else blocks you.

## The 18 survivors (verified list) + candidate 105 (RW-50)

I extracted this list from `/workspaces/vbpub/.run-gate/assay-state/
.worktrees/rg55-run-gate-client/run-gate-project/*.json`, filtered to only
the files whose `source_sha256` matches the CURRENT `run-gate.py` at
`186461de` (`6a6e2707edc714bcc4acbc580f8299a591b36d71c321473d576b7f57a21f4d69`)
— the state directory has accumulated cross-epoch cruft from earlier
sessions (files with `candidate_total: 256` and other stale epochs
coexist with the current 283-candidate set; naive dedup across
`.assay/progress-r2.jsonl`'s raw events produces 24-28 "survivors"
because per-file candidate batches reuse small index/total pairs across
different runs — do not trust a raw progress-log scan without the
source_sha256 filter). Filtering this way reproduces the official
bucket totals for `survived` (18) and `budget_exceeded` (2) exactly; the
`killed` count under the filter (240) undercounts the official 263
because some killed candidates' on-disk state files were later
overwritten by other epochs — this doesn't matter for triage purposes,
only the survivor identities do, and those check out exactly.

**None of these 18 have been triaged yet — this is fully open work for
you.** All are in `run-gate.py`.

| line | operator | mutation | candidate_id (short) | context |
|---|---|---|---|---|
| 1056 | bool-const-flip | True->False | `606523f23d04` | `proc = subprocess.run(argv, capture_output=True, text=True,` |
| 1190 | compare-swap | Gt->GtE | `6438f5979484` | `if best is None or rate > best:` |
| 1207 | compare-swap | IsNot->Is | `990b3c74e762` | `max_changed = (prev["memory_max"] is not None` |
| 1233 | boolop-swap | Or->And | `33470544a50b` | `if self._host_first is None or self._host_last is None:` |
| 1234 | falsy-swap | None->[] | `964387b0e06c` | `return None` (guard body of the line-1233 check) |
| 1275 | boolop-swap | And->Or | `bd70ff1869e0` | `if peak_bytes is not None and baseline_bytes is not None else None)` |
| 1434 | bool-const-flip | True->False | `f98db8b1f61b` | `proc = subprocess.run(argv, capture_output=True, text=True,` |
| 1473 | boolop-swap | And->Or | `f37226dd3d60` | `if mem_text is None and cpu_text is None and loadavg1 is None:` |
| 1623 | boolop-swap | Or->And | `0bca723d3c52` | `if snap is None or snap.get("full_avg10") is None or snap.get("full_avg60") is None:` |
| 1686 | boolop-swap | And->Or | `a066912273cf` | `and not isinstance(baseline, bool) else "?")` |
| 1759 | bool-const-flip | True->False | `868e9cecd0c9` | `file=sys.stderr, flush=True)` |
| 1764 | bool-const-flip | True->False | `7d2564045f5f` | `print(state["session_line"], flush=True)` |
| 1775 | bool-const-flip | True->False | `dd276c970fce` | `"token, no daemon call, no sampler", flush=True)` |
| 1779 | bool-const-flip | True->False | `2e6ab876459c` | `f"token env {PROFILE_TOKEN_ENV}", flush=True)` |
| 1833 | bool-const-flip | True->False | `8886647c0ae0` | `f"{manifest_seg}", flush=True)` |
| 3442 | boolop-swap | Or->And | `38ec187fa90d` | `f"{', '.join(sorted(lanes)) or '(none)'} (config: {cfg_path}"` |
| 5160 | boolop-swap | And->Or | `70c6d1068ef7` | `if manifest.get("lanes") and not drifted:` |
| 5249 | boolop-swap | Or->And | `18edcb28ad24` | `s_mem_p = (s.get("pressure") or {}).get(` |

Several of these (1759-1833, the `flush=True` cluster) look like classic
low-value `bool-const-flip` mutants on a keyword argument whose only
effect is output buffering timing — plausible equivalent-mutant
candidates (RW-20/RW-22 house style: a written justification in the
REPORT, no test needed) rather than genuine oracle gaps, but verify each
one actually can't be observed (e.g. via a test that checks stdout
ordering/atomicity) before writing that off. The 1190/1207/1233/1234/
1275/1473/1623/1434/1056/1686/3442/5160/5249 cluster looks like real
`None`-guard and comparison-boundary logic in the profiler/footprint code
paths (`ProfilerClient`/sampling helpers per the earlier file map) that
likely DOES need a killing test — do not wave these through as equivalent
without checking the surrounding function's actual behavior under the
mutant.

**Candidate 105 (RW-50):** the controller SIGKILLed this candidate's
pytest process at `20:22Z` after ~30 min CPU-bound (no
`budget_per_candidate` on this tree, so only the 4h lane budget would
have ended it otherwise). I confirmed its final state file
(`170ed994eb5686626a0c9950c5a6e9d7f4c09f6fd54408499e22877ca066185d.json`):
`path: tools/coverage_gate.py`, `line 128`, `Eq->NotEq`,
`source_sha256` matches the current tree, **`outcome_bucket: "killed"`**,
recorded at `elapsed_seconds: 1844.634` (~30.7 min). This is the expected
reading per RW-50: the SIGKILL produced a signal-death test-process
failure, which assay's judge correctly classified as "the mutant was
killed" (a never-terminating-looking mutant is still detected by any
sane judge). **Add the LOG line "20:22Z controller terminated candidate
105's pytest (RW-50); classified `killed`" when you write the LOG entry
for this lane.**

## Exact remaining sequence

1. Confirm `git -C .../run-gate-project status --short` empty, then
   `git switch --detach 186461de`.
2. Relaunch `assay-r2` (command above), untracked watcher, read the
   verdict in a separate step. Repeat up to 3 times total per RW-58. Stop
   at the first `PASS`, or after 3 `FAIL`s (accept run 2's evidence).
3. `git switch rg55-run-gate-client` (back onto the branch, now at
   `647a2cc6` unless you've made no other commits since this brief).
4. Triage all 18 survivors above (killing test or written equivalent-
   mutant justification per RW-20/RW-22) + candidate 105's note. Commit
   the triage.
5. Write the REPORT's mutation section with the FULL disclosure: run
   history (all 4 attempts + whichever of the 3 RW-58 relaunches you
   used), the RG-62/RW-46a order-dependent-test-defect finding, the
   `PYTEST_ADDOPTS` non-propagation finding (RW-58), and — if run 2 was
   accepted instead of a clean pass — say so explicitly and why.
6. T7 final gates on the final tip, each verdict read in a separate step:
   `selftest --allow-dirty` (must stay 100%/100%), `assay-r1`,
   `assay-r3 --allow-dirty`, `doctor`. Tree clean afterward.
7. Merge `rg55-run-gate-client` into `main` with `--no-ff`.
8. `cmru release --project run-gate-project --set-version 23.7.0`.
9. `pip install --upgrade` the built wheel into `/home/vscode/.venv`;
   verify `run-gate --help` reports rev 41.
10. Clear the `[Unreleased]` section of `CHANGES.md` (fold into the new
    23.7.0 entry per this repo's release convention).
11. Write the closing BRIEF/return with: release commit hash, wheel path,
    installed version, and anything left for P3's close-out (the live
    daemon probes were noted as still-open in an earlier BRIEF — check
    REPORT §4.3 status before claiming it's done).

## What NOT to re-do

- T1-T5 are done and gate-verified; do not re-touch `series_stats`,
  `_lane_stats`, the backlog rows RG-58..RG-61, the B3/M5 correction, SPEC
  R-43f/R-43a, or `assay.toml`'s `jobs = 2` — all committed, all correct.
- Do not re-diagnose the RW-56/RW-58 findings — they're confirmed with
  hard evidence (by-hand reproduction, 3x determinism check, state-file
  cross-check). Chasing assay's env-propagation bug further is optional
  and out of scope unless you have host budget to spare after everything
  else is done.
- Do not commit anything while detached at `186461de` — the whole point
  is preserving the 281/283 judged record set until you either get a
  clean pass or the controller accepts run 2's evidence.
- Do not touch `scripts/cgroup-profiler/`, `ciu/`, or `/workspaces/dstdns`.
- No push/merge to a shared remote beyond the local `main` merge described
  above; no release beyond 23.7.0 unless instructed.

## Retention prompt (paste this to compact/resume with)

```
KEEP: T1-T5 are DONE on rg55-run-gate-client (tip 647a2cc6 pre-triage).
The 281/283 mutation record set is keyed to commit 186461de (B088 tree-
identity resume) -- detach there for every further assay-r2 attempt, do
NOT commit while detached. RW-58: relaunch up to 3x total at 186461de;
first PASS is the evidence; 3 FAILs -> accept run 2 (killed 263, survived
18, budget_exceeded 2) with a disclosure. RW-56 root cause (already
proven, do not re-diagnose): TestExecModeMutex's lock-planting test
(line 4036/4053) leaks a pid-scoped directory that breaks 3 sibling tests
when pytest-randomly orders it first (RG-62/RW-46a, P4's branch already
fixes it) -- assay's baseline subprocess doesn't inherit PYTEST_ADDOPTS
(RW-58, unconfirmed mechanism, not your job to fix). The verified 18
survivor candidate_ids + line numbers are in BRIEF-7's table -- use the
source_sha256 filter method described there if you need to re-derive
anything, never a raw progress-log scan. Candidate 105 = killed (SIGKILL
at 20:22Z, RW-50) -- state file confirmed, just needs a LOG line.
DROP: the full run-1/run-2 resume-cache forensics narrative (already
distilled into BRIEF-7's "Run history" section -- cite it, don't re-derive
it); the raw progress-r2.jsonl exploration detail; the container/PSI
host-load checks from before RW-56 ruled out contention for this specific
failure (still do routine host checks, just don't re-litigate whether
THIS failure was contention -- it wasn't).
NEXT: git switch --detach 186461de, relaunch assay-r2, then follow
BRIEF-7's "Exact remaining sequence" steps 2-11 in order.
```
