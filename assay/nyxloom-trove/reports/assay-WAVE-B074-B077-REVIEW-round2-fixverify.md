# assay wave B074 + B077 — round-2 fix verification

Reviewer: same fresh session that produced round 1 (no implementer context).
Repair range: `b3a33415..bdbb8e91` (`15258dfc` fix + `bdbb8e91` docs)
Gate-verified commit per the controller: `15258dfc` (PASS, exit 0).

## Verdict: **ACCEPT**

All three round-1 blockers are genuinely closed — verified by re-running my
own round-1 mutants and probes against the new range, not by reading the
implementer's account. Nothing regressed. **Merge it.**

One non-blocking note carries a decision ask for the controller (a *third*
declared-target test-path veto at R3, pre-existing and out of this wave's
remit); it does not hold the merge.

---

## Method

Every claim below is something I re-derived. I re-applied my round-1 mutants
verbatim in place, ran four fresh break-attempts against the new registry
guard, wrote three new end-to-end probes for the R2 ruling, and ran the full
suite plus the W7 acceptance suite. Total: **22 mutants / break-attempts, 22
caught.** The worktree is pristine after every mutation (`git status --short`
empty; verified between each case).

Host: load 4.78 with two sibling gates live (B078, nyxloom-p105) — I launched
no gate container of my own; everything nice'd serial.

---

## Blocker 1 — CLOSED. The mutants I ran in round 1 now go red.

Re-applied **byte-for-byte the same two mutations** that left all 4359 tests
green in round 1, plus the new third forwarding site, in every combination:

| mutant | site | round 1 | round 2 |
|---|---|---|---|
| M1 | `runner.py` `evaluate_r1` → `evaluate_targets` = `False` | (green) | **KILLED — 3 failed** |
| M2 | `runner.py` `_run_prepared_lane` → `JudgmentR1` = `False` | (green) | **KILLED — 2 failed** |
| M3 | `runner.py` `_run_prepared_lane` → `_mutation_targets_whole` = `False` (new site) | n/a | **KILLED — 1 failed** |
| M1+M2 | round-1's exact pair, verbatim | **4359 passed, GREEN** | **KILLED — 3 failed** |
| M1+M2+M3 | the whole feature deleted at the plumbing | n/a | **KILLED — 3 failed** |

Every kill comes from `tests/test_lane_allow_test_path_targets.py` — the new
lane-level file — not from a round-1 test picking it up incidentally:

```
FAILED test_a_lane_with_the_flag_judges_the_target_and_passes
FAILED test_the_verdict_records_the_effective_policy
FAILED test_an_r1_r2_lane_with_the_flag_resolves_the_target_at_BOTH_tiers
```

**The tests are the real thing, not a route-around.** They write an actual
`assay.toml` into a real git repo and drive `cli.main(["run", ..., "--verdict-json",
...])`, then read the verdict the run itself wrote
(`test_lane_allow_test_path_targets.py:148-160`). The positive and its
controlled negative differ by **exactly one line of TOML** (`_FLAG_LINE`,
line 131) — the strongest form of the controlled comparison B074's acceptance
box asks for.

**The root cause was fixed, not routed around.** `tests/conftest.py:743`
gains `allow_test_path_targets: bool | None = None`, threaded to the
`JudgeConfig` at line 787, and it is actually used
(`test_lane_allow_test_path_targets.py:367`). The docstring at
conftest.py:766-772 records *why* — "A helper that cannot express a field is
why nothing tests it" — which is the correct diagnosis of the round-1 gap.

---

## Blocker 2 + the decision ask — CLOSED, on all four sub-points

I verified (a)–(d) with my own probes, driving real `assay.toml` files with
`rigor = ["R0","R1","R2"]` through `cli.main()`.

**(a) R2 now resolves the target and reaches a real mutation judgment.**

```
===== FLAG SET, target tests/_harness/lib.py: exit=1
  R0: PASS
  R1: PASS          r1.allow_test_path_targets: True
  R2: FAIL MUTANTS_SURVIVED
  r2: {"jobs":1,"kill_attribution":"unattributed","max_mutants":4,
       "mode":"whole_target","operators":["python:bool-const-flip"],
       "producer":"native","targets":["tests/_harness/lib.py"]}
```

A real graded R2 outcome with the declared target in `judgment.r2.targets` —
not `BAD_LANE_CONFIG`. This is exactly what the round-1 probe could not
produce, and exactly what the ruling asked for.

**(b) Without the flag, BOTH tiers refuse, each naming the target and the
remedy.** The R1 refusal does **not** short-circuit R2 — both claims are
present and both messages are remedy-naming:

```
R1: judge.targets entry 'tests/_harness/lib.py' is a test path per the
    adapter's own convention. If this file is library code that merely LIVES
    under a test directory ... declare judge.allow_test_path_targets = true ...
R2: mutation target 'tests/_harness/lib.py' is a test path per the adapter's
    own convention. If this file is library code that merely LIVES under a
    test directory, declare judge.allow_test_path_targets = true on this lane
    -- it relaxes this gate and R1's twin of it together, so one declaration
    covers both tiers
```

The round-1 Blocker-2 defect — an R2 message that flatly contradicted a flag
the operator had just set — is gone. I also probed the filename half at R2:
`tests/_harness/test_lib.py` **with** the flag still refuses at both tiers,
each with a tier-appropriate closing sentence ("grading a test file's own
coverage is the vacuity whole-target mode exists to close" at R1; "mutating a
test file to see whether the suite notices grades the suite against itself"
at R2).

**(c) The sweep-side sites are untouched, and the relaxation did not widen
past the declared-target gates.** I enumerated every `is_test_path` call site
in `src/`:

| site | kind | flag reaches it? |
|---|---|---|
| `evaluate.py:428` | sweep (changed-line coverage) | no — unchanged, takes no such parameter |
| `mutation.py:478` | sweep (changed-line R2) | no — `mutation.py` is byte-untouched across the *whole* wave (`git diff 9c2c435f..bdbb8e91` empty) |
| `evaluate.py:1110` | declared target, R1 | yes (round 1) |
| `runner.py:2995` | declared target, R2 | yes (this round) |
| `canary.py:477` | declared target, R3 | no — see the note below |
| `evaluate.py:996` | `_is_test_filename` itself | n/a |

Exactly the three declared-target gates the ruling scoped, no wider.

**(d) `_is_test_filename` is genuinely IMPORTED, not copy-pasted.** Checked
directly: `src/assay/runner.py:117-127` imports it from `.evaluate`, and the
whole `src/` tree contains exactly **one** `def _is_test_filename`
(`evaluate.py:966`). Two call sites, `evaluate.py:1124` and `runner.py:3012`,
share that one definition. The two tiers structurally cannot drift.

**And the new R2 gate logic is itself mutation-tested** — three more mutants I
wrote, all KILLED: R2 ignoring the flag (`if not allow_… ` → `if True`),
R2 dropping the filename half, and R2 using the full path instead of the
basename. `test_the_r2_gate_itself_refuses_a_test_path_target_without_the_flag`
catches all three.

**The re-argued load-time rule is sound.** `config.py:2043-2051` now refuses
an R2-only lane declaring the flag because *R1 is the tier that records the
policy into the verdict*, not because it is "the only reader" (which is no
longer true). That is the correct reason and it survives the change; the
message names it and tells the operator to declare R1 alongside R2.

---

## Blocker 3 — CLOSED. The completeness guard is load-bearing; I broke it four ways.

I did not read the parametrization and assume. Four independent break
attempts, **all four turn the suite red**:

| break attempt | result |
|---|---|
| register a genuine **fifth adapter** (a `rust` adapter with its own `is_test_path`) in `cli._built_in_registry()`, with no `_SPLIT_CASES` entry | **RED** — `test_every_registered_adapter_has_a_split_case` |
| drop `"javascript"` from `_SPLIT_CASES` while it stays registered | **RED** — same guard, other direction |
| weaken python's `directory_case` to a filename positive (`conftest.py`) | **RED** — `…splits_directory_from_filename_per_adapter[python]` |
| replace Go's explicit `None` with a directory positive that isn't one | **RED** — `…[go]` |

The set assertion is `set(_SPLIT_CASES) == set(_registered_languages())`, so
it catches drift in *both* directions, and the per-case adapters are pulled
from the registry rather than locally constructed, so the tests exercise the
object a real lane resolves. Go's absent directory branch is asserted
positively (`not adapter.is_test_path("tests/helper.go")`), not skipped.

---

## The other items

**N1 — DESIGN-GUIDE.** Done, and done well: a full section at
`docs/DESIGN-GUIDE.md:1326-1375` ("`allow_test_path_targets` overrides a claim
about LAYOUT, never about a filename (B074)") sitting beside `require_branch`'s
and `base_source`'s, structured as the three properties that make it a narrow
override. It states the R2 ruling's reasoning correctly.

**N2 — schema description.** Reworded, and — the part that mattered — moved in
**both** copies in the same commit. `diff src/assay/schemas/verdict.schema.json
nyxloom-trove/carve-assets/W7/verdict.schema.v11.json` → **byte-identical**,
and the B070-era guard
`test_shipped_schema_is_byte_identical_to_the_locked_v11_asset` passes. The
new text drops the misleading "did this lane grade…?" framing for "the
EFFECTIVE … POLICY … a lane that sets the flag and happens to name no test
path still records true, because the field answers what policy judged, not
whether the relaxation was exercised" — which is what the field actually is.
The hard cut is untouched: all **34** frozen v6–v10 templates still rejected
under v11 (re-run, 34 passed).

**N3 — sound, and it does not overstate.** The implementer folded it despite
my having marked it controller-owned; the change itself is correct.
`verdict.py:2065-2081` now gives both arguments and labels the *second* as
load-bearing: a pre-B074 loader refuses `allow_test_path_targets` as a surplus
judge key, so no older assay can emit it under any configuration. I verified
the premise — `_KNOWN_JUDGE_FIELDS` gates it and the surplus check refuses
unknown judge keys — and the claim is stated as absolute only where it is
absolute. The weaker first argument is kept and correctly labelled as also
true but weaker. No overstatement.

**LOG accuracy.** The revised account is honest rather than re-asserted. It
names the wrong call in the implementer's own voice — "**`runner._mutation_targets_whole`
was a third site, and I got its disposition wrong** … the ruling is right and
my call was wrong" — gives the three reasons it was wrong (the gate's own
docstring calls itself "R1's own rule, one tier down"; no safety asymmetry
because mutation runs in an ephemeral snapshot; the motivating consumer left
coverage-gradeable but never mutation-gradeable), and records that the
withdrawn position was carried out of `CONSUMERS.md`, `config.py`,
`evaluate.py` and `CHANGES.md` rather than left contradicting the code. The
mutant table it reports (3/2/1 failures for M1/M2/M3) matches what I measured
independently. I found nothing in it that is not true.

**CONSUMERS.md / CHANGES.md.** Row 4 of the "does not do" table is rewritten
from "It does not reach **R2**" (now false, and it used to tell consumers to
"declare such a lane R1-only, or move the file") to "It does not relax
**anything at R2 that R1 keeps strict**", with the one-declaration-two-tiers
reasoning. CHANGES.md matches. The CONSUMERS example still loads through the
real `load_lane_file` (`test_docs_examples_and_vocabulary.py`).

**Regression.** Full run of `tests/` **plus** the W7 acceptance suite:
**4477 passed, 20 skipped** (4367 in `tests/` — matching the reported count —
plus 110 W7). And my complete round-1 10-mutant battery re-run against the
repaired tree: **10/10 still KILLED**, including both B077 mutants (the cli
symlink-guard removal and the probe-all-components variant), so B077 did not
regress under B074's churn.

---

## Non-blocking note + decision ask (does NOT hold the merge)

**There is a THIRD declared-target test-path veto the flag does not reach:
`judge.canary.target` at `canary.py:477` (R3).**

I found this while enumerating call sites for point (c), not in either
round's scope. It is pre-existing — the canary veto predates B074 — and
nothing in the shipped docs says anything false about it (`config.py`'s
"nothing else reads it" is a true statement about the *flag*). So this is a
note, not a blocker.

But it is reachable. I confirmed the combination loads:

```
rigor = ["R0", "R1", "R3"], mode = "whole_target"
targets = ["tests/_harness/lib.py"], allow_test_path_targets = true
[lanes.unit.judge.canary] target = "tests/_harness/lib.py"
→ LOADS OK. canary.target = tests/_harness/lib.py | flag = True
```

so such a lane gets R1 relaxed and R3 refused by
`judge.canary.target {target!r} is a test path … a canary transforms real
source, never a test file` — a message that names neither the flag nor a
remedy. That is round-1 Blocker 2's shape, one tier over.

**Why I am not raising it as a blocker**, and why I am not deciding it: the
argument that forced the R2 ruling was that R1 and R2 resolve *the same
declared `judge.targets` list*, so a flag reaching one and not the other made
one declaration mean two things. That does not apply here — `judge.canary.target`
is a **different declaration key** with its own semantics (the canary
deliberately breaks a file to prove the suite notices), so "should the same
flag govern it" is a genuine product question, not an internal inconsistency.
Recommend filing it as a backlog entry rather than expanding this wave.

**Two smaller observations, neither worth a fix now:**

* `test_evaluate_whole_target_allow_test_path.py`'s `directory_case is None`
  branch asserts a hardcoded `not adapter.is_test_path("tests/helper.go")`. For
  Go that is exactly right. For a *future* adapter that also declares `None`,
  that assertion is vacuous (a `.go` path is trivially not its test path). A
  language-appropriate probe path in the table would close it; the
  completeness guard already catches the case that matters.
* `judgment.r1.allow_test_path_targets` is recorded from the lane's declared
  policy, which is now correct and correctly documented in four places — but
  it is worth remembering that on an R1+R2 lane it is the *only* place the
  policy appears: `judgment.r2` carries no such field. The docs say so
  ("R1 is the tier that writes that field"); no action.

---

## Summary

| round-1 item | status | how I verified it |
|---|---|---|
| Blocker 1 (HARD) — plumbing untested | **CLOSED** | round-1's exact mutants re-applied in place; all now red, killed by the new lane-level file; `make_r1_judge` root cause fixed |
| Blocker 2 (MEDIUM) — R2 message / no oracle | **CLOSED, and superseded by the ruling** | own end-to-end probes: R2 resolves with the flag and reaches a real mutation judgment; both tiers refuse without it, both naming the remedy; `_is_test_filename` confirmed imported, one definition in the tree; 3 new R2-gate mutants killed |
| Blocker 3 (LOW) — hand-copied adapter table | **CLOSED** | 4 break attempts incl. registering a real fifth adapter; all four red |
| decision ask — should R2 honour the flag | **RULED and implemented correctly** | scope confirmed to exactly the three declared-target gates; both sweep sites byte-untouched |
| N1 / N2 / N3 | **all done** | DESIGN-GUIDE section present; schema reworded in both copies, byte-identity guard and 34-template hard cut both green; N3 sound and not overstated |

22 mutants and break-attempts, 22 caught. 4477 passed, 20 skipped. Nothing
regressed. **ACCEPT — merge it.**
