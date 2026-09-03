# nyxloom-P101 — adversarial CODE REVIEW

**Reviewer:** the same fresh adversarial reviewer who ran the three carve-review
rounds (`nyxloom-P101-CARVE-REVIEW.md`), now reviewing the implementation.
**Range reviewed:** `ac61f7d9..f2eb9eb1` (carve freeze → final commit).
**Method:** blind pass first — read the diff and re-ran every check myself,
including six live mutation probes against the shipped tests and an independent
re-run of the real `tester-unified` gate — before reading `LOG.md` / `REPORT.md`.

**VERDICT: ACCEPT — no blockers.** Five non-blocking findings in §6. Full
reasoning in §7. Independently gate-verified PASS on the final commit
`f2eb9eb1`, and all six of my live mutation probes were caught by the shipped
oracles.

---

## 1. Commit topology and gate provenance

```
f2eb9eb1  docs -- fill in the LOG/REPORT's own commit hash
ba2e09c2  docs -- Work item 10, real gate PASS verdict recorded
4241922e  docs -- LOG + REPORT through Work item 8, gate pending   <-- GATE JUDGED THIS
0b5eb9d4  docs -- close NL-7 with the corrected mechanism, regenerate INDEX.md
978f7a5c  fix  -- retire _TIER_BAND, scope.touch size is the sole band signal
a60f0ab3  docs -- carve review ACCEPT after 3 rounds
```

**The gate judged `4241922e`, not `ba2e09c2`.** I did not take this from the
REPORT — I read `run-gate`'s own state file, `.run-gate/history.json`:

```json
{"commit": "4241922e41e6332d3637f272ab4a08a8230db020", "dirty": false,
 "outcome": "pass", "exit_code": 0, "lane": "tester-unified", "revision": 33,
 "duration_seconds": 253.392, "started_at": "2026-09-03T07:57:12Z",
 "worktree": "/workspaces/vbpub/.worktrees/nyxloom-p101-tier-band"}
```

Correct lane, correct worktree, **clean tree** (`dirty: false`), exit 0.

**This does not weaken the verdict, because the code is frozen from that commit
onward:**

```
$ git diff --stat 4241922e f2eb9eb1 -- src tests
(empty)
$ git diff --stat 4241922e f2eb9eb1
 nyxloom-trove/reports/nyxloom-P101-LOG.md    | 70 ++++++++-
 nyxloom-trove/reports/nyxloom-P101-REPORT.md | 77 +++++++++-
```

Everything after the judged commit is LOG/REPORT prose. The gate verdict covers
100% of the shipped code. See finding N1 — the mis-stated commit came from the
dispatch relay, **not** from the implementer, whose LOG and REPORT both name
`4241922e` correctly and explain why.

## 2. Work items 1-10 against the diff

Every pinned block was checked **programmatically** — extracted from the handoff
by fence-parsing and compared byte-for-byte against the shipped file, never by eye:

| item | check | result |
|---|---|---|
| WI2 | pinned comment block in `adapters.py` | **byte-exact**, `count == 1`, and `index(PINNED) < index("def compute_review_depth_directive")` |
| WI3 | pinned signature, pinned docstring paragraph, pinned ternary | all three **byte-exact**; `(a small scope_touch)` present; `implement-1/implement-2` gone from the module |
| WI3 | early return kept as instructed | present, `adapters.py:236` |
| WI3 | `_LOW_BAND`/`_HIGH_BAND`/`_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`/`_RIGOROUS_ASSERTS` retained and still referenced | 2 / 9 / 3 / 2 references — no dead constant |
| WI4 | all 12 call sites drop `tier=` | `git grep compute_review_depth_directive -- src tests \| grep -c 'tier='` → **0** |
| WI4 | four non-call docstring/name hits | 2082/2106/2133-2134 updated; 2158 renamed to `..._scope_size_band`; 2159-2163 rewritten; third assertion block deleted |
| WI4 | three new tests added | O1 boundary+signature, O3 AST scan, O4 verbatim+anchor |
| WI5 | caller edit | exactly the one-line deletion, `first_fm` and both kwargs untouched |
| WI6 | O5 caller test | `TestReviewDepthReachesTheRealCaller`, both directions, real `launch_review` |
| WI7 | controlled breaks | re-run independently, §3 |
| WI8a | NL-7 pinned mechanism text | **byte-exact**, `count == 1` |
| WI8b | status via CLI | `status: fixed`, `closed_date: "2026-09-03"`, `closed_reason` = the pinned `--reason` string verbatim. `closed_date`/`closed_reason` are CLI-generated fields — direct evidence `set-status` was used, not a hand edit |
| WI8b | INDEX regenerated | `nyxloom lint` → **0 BLG findings**, i.e. BLG3 (`backlog_entries.py:323`, "must byte-equal a fresh regeneration") is satisfied. The diff shows the NL-7 row **moved** from the open group to the fixed group — a regeneration artefact, not the in-place `open`→`fixed` edit a hand change would produce |
| WI8c | BLG-findings read | evidenced in the REPORT, and independently reproduced by me |
| WI9 | `docs/plan-next-batches.md` note | **byte-exact**, `count == 1`, inserted after BATCH C's final line and before `## BATCH D`; the historical `✅ DONE` record is otherwise untouched |
| WI10 | gate + LOG + REPORT | §1, §5 |

**Files changed vs `scope.touch`:** exact match, with one addition —
`nyxloom-trove/reports/nyxloom-P101-CARVE-REVIEW.md`, committed in `a60f0ab3`.
That is my own carve-review artefact being entered into the pipeline record, not
implementer product. Correct hygiene; noted only for completeness.

**No coverage evasion:** `git diff ac61f7d9 f2eb9eb1 -- src tests | grep -c 'no cover'` → **0**.

**No weakened assertions:** I read every changed test hunk. The five tests that
swapped `tier="implement-3"` for a 6-path `scope_touch` keep every assertion they
had. This is safe for the two length-sensitive ones
(`..._worst_case_argv_degrades_never_raises`, `..._truncates_when_room_tight...`)
because band 3 via tier and band 3 via scope size produce a **byte-identical**
directive — the reason for firing is not encoded in the text.

## 3. Live evasion probes (the core of this review)

I flagged O2 and O5 as the fragile oracles during carve review. Claiming a test
exists is not evidence it bites, so I applied six real mutations to the working
tree, ran the named tests, and reverted each. Tree confirmed clean before and
after (`git status --porcelain` empty).

Baseline first: the five P101 tests plus `TestWaveLeaseUnion` → **7 passed**.

| probe | mutation | test that failed |
|---|---|---|
| **B7′ (O2)** | `else _LOW_BAND` → `else _HIGH_BAND` | `test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc` |
| **B2 (O1)** | `>` → `>=` on the threshold | `test_compute_review_depth_directive_signature_and_threshold_boundary` |
| **B1′ (O1)** | suppressor kept, param renamed `handoff_tier` with a default | `test_compute_review_depth_directive_signature_and_threshold_boundary` |
| **B3′ (O3)** | `_BANDS = dict(zip(("implement-1",…),(1,2,3)))` | `test_adapters_module_carries_no_tier_shaped_string_literal` |
| **B4′ (O4)** | pinned block moved verbatim to EOF | `test_adapters_carries_the_pinned_nyxloom_p101_band_comment_exactly` |
| **B6′ (O5)** | caller passes a hardcoded 6-list instead of `first_fm.scope.touch` | `TestReviewDepthReachesTheRealCaller::test_scope_touch_size_drives_review_depth_through_the_real_caller` |

**All six caught. Two results matter most, because they are exactly what I
demanded in carve review and could not confirm until now:**

- **O2 is genuinely protected.** In round 1 I proved the carve's original break
  (deleting the early return) was a behavioural no-op, leaving O2 with no
  mutation evidence at all. B7′ replaced it, and B7′ really does redden the
  snapshot oracle. O2 is no longer a hollow guard.
- **B6′ isolates O5.** Both `TestReviewDepthReachesTheRealCaller` **and**
  `TestWaveLeaseUnion` were in the same pytest invocation; only the former
  failed. This is the property I required in round 2 — the original B6
  (re-adding `tier=`) reddens the pre-existing wave-lease test via `TypeError`
  and therefore proves nothing about O5. The shipped test earns its place: it
  pins the end-to-end value binding, and the 1-path direction is what makes a
  hardcoded stand-in fail.

**O3's widened scan works and is honest.** B3′ is the `dict(zip(...))` form I
measured evading an `ast.Dict`-keys check; the shipped test catches it. The
test's own docstring states the two remaining limits (an f-string comprehension,
a dict keyed on deployed tier names) rather than papering over them, and the
regex is an unanchored `.search` over every string `Constant` — which is what
makes it also cover the docstring, not just the dict. Zero hits on the fixed
file.

## 4. Behavioural verification

Re-ran the shipped function directly:

- 5 paths + rigorous gate → `""`; 6 paths + rigorous gate → fires with
  `high-complexity`. Boundary is `>`, as specified.
- `tuple(inspect.signature(...).parameters) == ("scope_touch", "gate_asserts")`.
- Ownership inventory recomputed by hand against real `wc -l`:

  | file | recorded | actual | allowed | drift | |
  |---|---|---|---|---|---|
  | `adapters.py` | 1,161 | 1,204 | 120 | 43 | INSIDE |
  | `effects_review.py` | 597 | 638 | 63 | 41 | INSIDE |
  | `tests/test_adapters.py` | 2,619 | 2,826 | 282 | 207 | INSIDE |

  `tests/test_effects_review.py` is recorded `new` and skipped by the tolerance
  loop (`test_core_characterization.py:809-810`). The carve projected ~2,775 for
  `test_adapters.py` and it landed at 2,826 — still comfortably inside, and the
  carve's `escalate_if` correctly told the implementer to re-measure rather than
  guess.

## 5. Independent gate re-run

Because the commit relayed to me was wrong (§1, N1), I did not stop at reading
`history.json` — I re-ran the real gate myself, using the handoff's own
**"Gate argv (verbatim)"** including the `--worktree` flag, on the final commit
`f2eb9eb1` with a clean tree. Host discipline observed: `docker ps` first (no
other gate container), load 6.5/8 at launch, `docker update --cpus=3` applied to
the container within seconds.

```
$ ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band tester-unified
run-gate: rev 33 | lane tester-unified | slice dev-background.slice
assay-4.0.0.pyz: OK
tester-unified: PASS (exit 0)
  commit: f2eb9eb1aec472aa7fee9b964e4ff8d04954ff81
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: lane 'tester-unified' exit 0
```

**Verdict read in a SEPARATE step** from the run (LESSONS L4 — never a piped
tail), from two independent artefacts:

`.assay/verdict-tester-unified.json`:
```
outcome PASS | exit 0 | commit f2eb9eb1
  R0 (tests-pass)              PASS  verified_by_assay=True
  R1 (changed-line-coverage)   PASS  verified_by_assay=True
     pct 100.0, considered 1, executable 4, covered 4,
     missing_lines {}, files_missing_coverage []
```

`.run-gate/history.json` latest: `commit f2eb9eb1, dirty False, outcome pass,
exit_code 0, duration 247.556s`.

**This is strictly stronger evidence than the implementer's run**: it judges the
actual final commit `f2eb9eb1` rather than `4241922e`, closing the provenance
gap in §1 entirely. The lane, the pins (assay 4.0.0 sha256-verified), the
container and the asserts are the real registered ones — not a cockpit `pytest`
substitute.

It also settles N2: the handoff's pinned argv **including `--worktree` works
exactly as written**. The container command resolved to
`cd /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom` with no
double-appended path.

## 6. Findings — all NON-BLOCKING

- **N1 — the gate commit was mis-stated in the dispatch relay, not by the
  implementer.** The instruction I received said "implementer reports gate judged
  commit `ba2e09c2`". The real judged commit is `4241922e`. Crucially, the
  implementer's own artefacts are **correct**: `LOG.md` says *"The commit judged
  is `4241922e` — the LOG+REPORT commit made just before the gate was
  launched"*, and `REPORT.md` repeats it with the same explanation. So this is a
  relay error to correct in the ledger, not an implementer defect, and it is
  harmless because the code diff from `4241922e` to `f2eb9eb1` is empty.
  *Prescription:* record `4241922e` as the gate-judged commit in the merge ledger.
- **N2 — the implementer deviated from the handoff's pinned "Gate argv
  (verbatim)" and its stated reason is wrong.** They ran
  `python3 run-gate.py tester-unified` without `--worktree`, reasoning that
  *"passing it here would double-append the path and break the docker mount"*.
  My own run with the flag, exactly as the handoff pins it, shows the container
  argv resolving correctly — `cd /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom`,
  no double-append. The premise is false. The outcome was nonetheless correct
  (`history.json` confirms the right lane and worktree), and the deviation is
  disclosed openly in the REPORT's Gate run section. *Prescription:* none for
  this package; do not propagate the "double-append" claim into future handoffs.
- **N3 — the REPORT's closing "Final commit range: `ac61f7d9..ba2e09c2`" omits
  `f2eb9eb1`.** Inherent to a report that records its own hash; `f2eb9eb1` exists
  only to fill that hash in. Cosmetic.
- **N4 — `nyxloom-trove/reports/nyxloom-P101-CARVE-REVIEW.md` is not in
  `scope.touch`** but was committed in `a60f0ab3`. It is the review record
  entering the pipeline, which is right; noting it only so the scope audit is
  complete.
- **N5 — O3's guard is deliberately narrower than "no tier mapping can return".**
  The shipped test proves *no tier-shaped string literal survives in
  `adapters.py`*. A computed-key comprehension or a dict keyed on deployed tier
  names still evades it. This is correctly scoped and correctly documented in the
  test's own docstring — recorded here so a future reader does not over-trust it.

## 7. Verdict

**ACCEPT — merge it.** No blockers. The five findings in §6 are all
non-blocking; none requires a change to this branch, and N1/N2 are corrections
to the surrounding record rather than to the code.

What I verified myself rather than accepting on report:

- Every pinned block — WI2's comment, WI3's signature/docstring/ternary, WI8a's
  NL-7 mechanism, WI9's supersession note — is **byte-exact**, extracted from
  the handoff programmatically and compared, each appearing exactly once.
- All 12 call sites drop `tier=`; zero residual `tier=` kwargs anywhere in
  `src/` or `tests/`; the early return kept as instructed; all four constants
  still referenced; no `no cover` pragma; no assertion weakened.
- **Six live mutation probes, all caught, tree clean before and after.** The two
  oracles I identified as fragile in carve review are now genuinely load-bearing:
  B7′ reddens O2 (replacing the no-op break I disproved in round 1), and B6′
  reddens O5 **alone**, with `TestWaveLeaseUnion` staying green in the same run —
  the isolation property I required in round 2.
- NL-7 closed through the CLI (`closed_date`/`closed_reason` are CLI-generated),
  `INDEX.md` regenerated not hand-edited (BLG3 clean, **0 BLG findings**,
  reproduced independently), inventory tolerances recomputed by hand — all three
  rows inside.
- **The real gate re-run by me on the final commit `f2eb9eb1`: PASS, exit 0,
  clean tree, R0 and R1 both green, 100% changed-line coverage**, verdict read
  in a separate step from two independent artefacts.

The LOG and REPORT reconcile with my blind pass on every point I checked,
including the eleven controlled breaks (my six overlap theirs and agree on every
result) and the lint baseline (my 793 lines + their appended `EXIT=` line = their
794 — reconciled exactly, not a discrepancy). Where the implementer deviated
(N2) they disclosed it openly, and where the dispatch relay was wrong about the
gate commit (N1) their own artefacts were right.

This package did what a carve-reviewed package is supposed to do: the defect it
fixes is real and was measured, the oracles bite under mutation rather than
merely existing, and the removal of a branch did not take the neutral case with
it.
