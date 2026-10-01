<!-- Verbatim round-1 pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against a3b68800. Dispositions: ../REVIEW-2026-09-28-round1.md -->

I reviewed all three briefs read-only against source at `a3b68800`. **None is ready to dispatch: P3c and P3d are NOT READY, P10a is READY-WITH-FIXES, and P10b is NOT READY.** Most fixes are wording changes to the briefs. About 45 of the file:line references are correct; the wrong ones are listed at the end.

## P3c: liveness lane keys

**Traceability (requirement → oracle → gap)**

| Requirement | Oracle | Gap |
|---|---|---|
| Grammar and refusals | new tests in `test_liveness_lane_keys.py` | a value that parses to `inf` is not refused |
| Echo in `assay lanes --json` | `test_cli_lanes_json.py` | only the spelling `"5s"` is tested |
| Monitor window and floor scaling | fake-clock unit test | the tick at which `hung` fires is not pinned |
| Calibration idle floor | unit test | nothing proves the runner passes the value in |
| Disclosure | real-run pins | both keys are 5s, so a swap is invisible |
| Shortened tests | the two real-child tests | wrong file after P1; the change adds host-speed dependence |

**Findings**
- **P3C-1 BLOCKING** (brief lines 89, 148, 286). The normative default `cpu_window_s: float = _HUNG_CPU_WINDOW_S` is fixed when the function is defined.
  - `test_liveness_runner_monitor.py:209` monkeypatches `_HUNG_CPU_WINDOW_S = 2.0` and pins `clock.t == 5.0` at :227.
  - With the specified signature the window stays 30 and the floor becomes 30 × (1/2) = 15, so that test goes red.
  - The brief says the existing monitor tests "stay green unchanged" and limits that file to "additive only".
  - Fix: use a `None` default resolved in `__init__`, plus a separate `_HUNG_CPU_GROWTH_FRACTION` constant. Alternatively, allow :209 to pass `cpu_window_s=2.0`.
- **P3C-2 BLOCKING** (lines 7, 47–50, 151, 286, 306). Plan §11.6 cuts v14 after P1, and P1 moves both tests to `tests/zz_slow/test_cli_run_real_campaigns.py` (P1:15, :138).
  - The Touch list, the anchors and the focused pytest command all still name `tests/test_cli_run.py`.
  - Fix: name the zz_slow file, find the tests by name, and add "P1 move missing" as a BLOCKED trigger.
- **P3C-3 MAJOR** (line 74). The claim that `parse_duration` "already refuses" non-finite values is false. A 400-digit seconds string parses to `inf` (I checked this).
  - An infinite window can never fill, so hung detection silently turns off while the disclosure says it is active.
  - `cpu_window_s: Infinity` also breaks the JSON and P3a's finite rule, and only after a whole campaign.
  - Fix: add an `isfinite` refusal and an explicit maximum, each with its own message substring.
- **P3C-4 MAJOR** (lines 151–156). Lowering the hang test's budget from 50 s to 20 s saves no time, because that test ends when `hung` fires, not at the budget.
  - It does shrink the tolerance for a slow baseline: `hung` needs `max(3×worst_gap, 5) < 20`, so the worst gap must stay under about 6.6 s instead of about 16 s.
  - `worst_gap` is measured wall-clock time, so "neither assertion depends on elapsed time" is false.
  - Calling a starvation flip a "TRUE red" contradicts the operator's contention-agnostic rule.
  - Fix: use about 25 s. It must stay under 30 s so that forgetting to pass the window still fails the test. Describe the real-child tests as smoke tests; the fake-clock unit test is the deterministic oracle.
- **P3C-5 MAJOR.** No oracle proves the runner passes `idle_floor_s` into calibration. If it forgets, the hang test still sees `hung` (at a 15 s bound, under the 20 s budget).
  - Fix: assert the plan-event relation `expect_next_event_within_s == max(3.0*worst_gap_s, idle_floor_s)`. It compares two disclosed values and involves no timing.
- **P3C-6 MAJOR.** Every fixture uses 5s/5s, so swapping window and idle floor anywhere passes all oracles. The tuple order of `liveness_windows` and where the new keys go in the `plan` event (top level or inside `liveness`) are both unstated.
  - Fix: use distinct values (window 6s, idle floor 5s) and pin both.
- **P3C-7 MAJOR** (Gate section). There is no `self-qualification-preflight` run, although plan §10.3 requires one for any `src/assay` change.
- **P3C-8 MINOR.**
  - The keys are accepted on lanes where liveness can never switch on (the RW-36 test at config.py:3172). That is dead config and should be refused.
  - The fallback path ignores the idle floor while still disclosing it; say whether the disclosed value is declared or applied.
  - Nothing enforces "B105 lanes keep the defaults"; add a pin.
  - Point the README edit at README:654, not :364, which is the grace text that must stay. Change "time never classifies" to "time never produces killed/survived".
  - `w*(1/30)` and `w/30` differ in the last bit for some windows (e.g. 5.75); pin one formula.
  - No tracer-bullet record from the carver.

**Combined fixtures**
1. Liveness auto, a `/bin/sh` argv on a python lane, and both keys set: disclosure must be null. This path is not tested today.
2. Window 6s with idle floor 5s: catches a swapped disclosure or wrong threading.
3. The monkeypatched :209 test run under the new signature.
4. Keys declared with the plugin inactive (fallback path).

**Wrong implementations that pass the proposed tests**
- Refusal oracle: a minimum check that lets `inf` through.
- Echo oracle: re-formatting to `"5s"` instead of echoing verbatim.
- Monitor oracle: an off-by-one window of 6 s, because the firing tick is not pinned.
- Calibration oracle: the runner never threads the idle floor.
- Disclosure oracle: a cpu/idle swap.
- Shortened-test oracle: the idle floor never reaches calibration.

**Verdict: NOT READY.** A refusal/bound (P3C-3), an interface that contradicts a stated oracle (P3C-1), and the scope/location (P3C-2) are left for the implementer to invent.

## P3d: gate `--cold-witness` and source-bound checker

**Traceability.** C3 is the only check that is truly bound to the committed source (C5 is its independent re-derivation). C1, C2, C4, C6 and C7 check constants. C8 and C10–C12 check that producer-written files agree with each other; they are not source proof.

**Findings**
- **P3D-1 BLOCKING** (lines 61, 141). The brief conflicts with the §11.6 merge order (P6 → v14 → P7b → P10b).
  - P6 merges first and already wires `--campaign-deadline "$deadline"` for both lanes inside a `timeout` wrapper (P6:147–155). P3d adds a `# B110-P6:` TODO with a different variable name and pins that marker.
  - The pin "`--cold-witness` appears only in the `self-qualification)` arm" goes red once P7b adds its `b110-pilot` and `b110-screen` arms (P7:268, :291), which also pass `--cold-witness`.
  - Fix: state that P6 is in the base, keep its wiring, drop the P6 TODO, and scope the pin to the body of `run_and_verify_lane`.
- **P3D-2 MAJOR.** The pytest config file is not source-bound. B110's acceptance requires the checker to bind "cwd/config", but `config_sha256` is never compared with the sha256 of `git show <commit>:assay/pyproject.toml`. This matters more once P1 moves `pythonpath` into pyproject. Add a check (C13), and label C8 and C10–C12 as consistency checks.
- **P3D-3 MAJOR.** The valid-report builder leaves out P0's `--plan-json`, which P0 W5 makes mandatory for R2 reports. The positive test would be refused.
- **P3D-4 MAJOR** (line 293). "The pilot will exercise these flags end to end" is false. The pilot arm (P7:255–277) never calls `run_and_verify_lane` or the checker. The first real exercise would be the 6–8 h qualifying run.
  - The text pins still pass if `"${lane_flags[@]}"` is never expanded into the command.
  - Fix: pin that expansion, and add a stub-binary test that runs only `run_and_verify_lane` with a fake `assay_bin` and records its arguments. This needs no docker.
- **P3D-5 MAJOR.** The C3 oracle cannot tell `git show` apart from reading the worktree file, because HEAD equals the worktree in CI.
  - Add a fixture: a repo whose committed `assay.toml` is A, whose working copy is B, and whose report matches B. It must be refused.
- **P3D-6 MINOR.**
  - Recompute `duplicates` from the sidecar instead of trusting the report, and bound the sidecar's size.
  - A `KeyError` or `TypeError` escapes the caught exception tuple (b105_report_check.py:139) and exits 1 instead of 2.
  - The independence test should ban every `assay.*` import and `importlib`, not only `assay.r2_command`; also list the 8 argv cases.
  - Delete any stale sidecar before the run.
  - Make C9 source-bound (the committed lane's `env` and `env_passthrough`).
  - Note that `"${arr[@]}"` under `set -u` needs bash 4.4 or later.
  - State the refusal order when both `--plan-json` and `--r2-manifest` are wrong.

**Combined fixtures**
1. A duplicated sidecar line with a matching digest and `duplicates: 0`.
2. Matching argv but a mismatched `config_sha256`.
3. A dirty worktree `assay.toml`.
4. Two cold kills where only the second has a wrong `failed_call_index`.
5. A report with no `judgment` key at all.

**Wrong implementations that pass**
- Valid-report oracle: accepts `--r2-manifest` without reading it.
- C3: reads the worktree file.
- C11: checks only the first cold kill.
- Independence: `from assay import r2_command`.
- Gate wiring: `lane_flags` defined but never expanded.

**Verdict: NOT READY** because of the merge-order and pin conflict (P3D-1) and the missing refusals and proof sources (P3D-2 to P3D-4).

## P10: equivalence ledger

**Can a wrong or stale ledger entry yield a false PASS?**
- A wrongly ledgered entry that the suite cannot kill does pass. That is the operator's accepted trade: human review, and a same-commit survival that is consistency evidence, not proof.
- A forged receipt with copied identity fields passes the qualifying run's binding, and no downstream check re-validates it (P10-7).
- Beyond that, the binding (commit, tree, lane, version, ledger sha, candidate set, judge identity) is sound.
- `judge_mutation` and `mutation_pct` behave correctly: ledgered candidates stay out of the score, and all-ledgered gives `ALL_MUTANTS_EQUIVALENT`.
- "Ledger declared but no audit, so candidates execute normally" is PASS-safe; only its resume/import interplay is undefined (P10-5).

**Findings**
- **P10-1 BLOCKING.** OC12 is marked "chosen (b), resolved", but it is not wired in anywhere.
  - The receipt schema has no `runtime_fingerprint_sha256`.
  - "All cold-only inputs None" sets the runtime fingerprint to None, per P3b:208's signature.
  - The audit runs without `--cold-witness`, and under A6 the manifest-only plugin that captures the fingerprint is injected only in cold mode.
  - A non-cold qualifying run has `r2_command: null`, so there is nothing to compare against.
  - P3a:271 still says `coverage_baseline` has 5 keys.
- **P10-2 MAJOR.** The resolution algorithm is unstated.
  - The refusal words (unresolved, ambiguous, fingerprint-mismatch) and the checker's "anchor appears in the file" imply: exact anchor match, then fingerprint equality, then class uniqueness.
  - The brief says only "never by ordinal alone". Which anchor goes on the wire after drift is undefined.
  - The uniqueness domain is inconsistent: ordinals are counted over the file's sites, eligibility over the plan. Add this as an open choice, OC13.
- **P10-3 MAJOR.** Fingerprints depend on the Python version.
  - `ast.dump` output changed in 3.13 (`show_empty` now defaults to False), and `requires-python` is ≥3.11, so Invariant 4 ("pure function of the source") is false.
  - Add OC14 (a canonical serializer, or a pinned interpreter) and a probe E5 that diffs fingerprints across interpreters.
- **P10-4 MAJOR.** Probe E2 misses the dangerous seam.
  - A new top-level function's sites get their own qualname, not `<module>`, so no ordinals shift.
  - Re-aim it: (a) a module-level `0 == 0` inserted before existing module-level sites; (b) a duplicate of an eligible statement, which must give `ambiguous`; (c) a different-shape statement with the same scope, operator and change inserted before it, which must be refused and never re-targeted.
  - Add (c) as a P10b oracle; O3 covers only (b).
- **P10-5 MAJOR.** Resume and import interplay is undefined.
  - Plan §5 puts the ledger sha in the judge identity "when declared", which includes screens; P10 says only in the qualifying run.
  - Records of `survived` from an unaudited run, or D7 imports of screen records for ledger candidates, meet ledger placement in an unspecified order.
- **P10-6 MAJOR.** Missing terminal states and refusals:
  - the audit when interrupted, over budget, or when R0/R1 fails;
  - an empty ledger (the wire requires at least 1 entry);
  - `--shard` with a ledger: the rule `len(equivalent) == entry_count` fails per shard;
  - static binding mismatches, which should refuse the whole lane before the baselines rather than after them;
  - plan-event values other than `declared-unaudited`.
- **P10-7 MAJOR.** The checker extension never re-validates the receipt, although P3d's TODO says "and the audit receipt is bound".
  - It also does not check the ledger `path` against the lane key in the committed `assay.toml`.
  - The receipt should also bind the wheel digest, as P6's deadline does.
- **P10-8 MAJOR.** The gate-mode step creates a dependency cycle.
  - P10b lives on v14, v14 merges before P7b, and the gate mode needs P7b. The "second commit" therefore has no valid branch; split it into a separate P10c off the integration line.
  - How bash decides that the lane "declares a ledger" is unspecified.
  - The fixed receipt path combined with `_write_new` (never overwrite) breaks every re-run; include the commit in the path.
- **P10-9 MAJOR.** SQL naming conflict.
  - P3a's rule X8 ("native lane with equivalents requires a ledger") would reject SQL native equivalents. `r2_inconclusive_all_mutants_equivalent.json` uses `equivalence_artifact`, which also breaks P10 Invariant 5.
  - Separately, P10b step 6 ("implement V1–V6 if P3a didn't") contradicts its own BLOCKED trigger for the same case.
- **P10-10 MINOR.**
  - The anchor regex `$` lets a trailing newline through; use `fullmatch`.
  - `match_case.guard`, `TryStar` and `type_params` have no fingerprint subject.
  - OC3's deciding evidence measures stability only, never staleness of the `reason`.
  - B109 requires a Luna-model design review, but P10a routes to a fresh Opus review instead.
  - The focused test command omits `test_b105_report_check.py` and the judge-identity tests.
  - The number of ledger entries, and so audit runtime, is unbounded.
  - OC4, OC7 and OC10 need operator ratification before P10b is dispatched.

**Combined fixtures**
1. An unaudited run, then an audited `--resume` in the same state directory.
2. An ordinal shift where the new ordinal-0 site is a different statement.
3. A non-cold qualifying run with a ledger and an accepted receipt.
4. A ledger authored on Python 3.12 and gated on 3.14.
5. A forged receipt.
6. A SQL lane with equivalents under X8.

**Wrong implementations that pass**
- O1: the ordinal folded into the fingerprint.
- O2: role suffixes only for `@dataclass`.
- O3: uniqueness computed over the plan after truncation.
- O6: the audit runs in cold mode.
- O8: comparisons against the receipt's own fields rather than recomputed values.
- O10: no invocation count, so candidates are placed without being executed.
- O12: the checker hashes the worktree ledger file.

**Verdicts**
- **P10a: READY-WITH-FIXES.** Add OC13 and OC14, the real OC12 wiring, the re-aimed E2, probe E5, and the audit terminal-state choice.
- **P10b: NOT READY.** It is gated on P10a anyway, and it also has the concrete defects P10-1 and P10-5 to P10-9.

## Wrong or stale anchors
- P3c `_monitor` "1455–1540": the def is at `liveness.py:1441`.
- P3c README `:364`: this is the post-finish grace text, which must stay 30 s. The lane-key text belongs at `README.md:654–660`.
- P3c `tests/test_cli_run.py:463–676` and the pins at 529 and 645: after P1 these live in `tests/zz_slow/test_cli_run_real_campaigns.py`.
- P3c "exact liveness dict pins ~443/~577": 443 belongs to a different test with liveness inactive, and the busy-loop test has no exact dict pin.
- P3d `run_and_verify_lane` "150–213": it is at 156–214; `assay verify` is at 195, not ~193.
- P10 python.py operator catalogues "440–478": they start at 434.
- P10's "budget_exceeded_mask 3009–3035" names the bucket assembly; the mask is built at 2890–2993.
- P3a:271 "coverage_baseline has 5 keys" is stale; after OC12 it has 6.

## Other checks
- Every other reference I sampled was accurate: `config.py` 384/438–450/567/641/2946–2952/3082–3106/3144/3172/3219/3279, `runner.py` 3796/4420/4964/5036, `mutation.py` 320/1023/1702/3601/3626/3768, `verify.py:1283`, `verdict.py:5038`, `cli.py` 331/1571/1824, the checker 14–107 and 110–147, `test_b105_report_check.py` 23/34/78/94, `test_self_lane.py` 128–135/156–198, `run-gate.toml` 33–52, `decisions.md` 941/943, and the CONSUMERS/DESIGN-GUIDE docs anchors.
- §3b is pasted byte-identical to AUTHORING.md in all three briefs.
- All three have a BLOCKED rule and the host-load rule.