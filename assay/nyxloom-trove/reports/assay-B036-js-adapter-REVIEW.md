# assay B036 — JavaScript/TypeScript adapter (R1) — ADVERSARIAL REVIEW

**Reviewer:** independent, no implementation context.
**Branch:** `feature/assay-b036-js-adapter` @ `371a4f7b`, base `62fe368f`.
**Verdict: ACCEPT-conditional** (three conditions, §Conditions).

Everything below was re-derived here. Where the report pasted a transcript, the
transcript was re-run rather than read. This devcontainer HAS a Node toolchain
(`node v26.5.1`, `npm 11.17.0`, registry reachable), so the central empirical
claim was checked against real `vitest`, not against the committed fixture.

---

## What independently verified TRUE

| claim | how it was checked here |
|---|---|
| the real registered gate is green | Ran `./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b036-js-adapter tester-unified` on a clean tree at HEAD `371a4f7b`. Exit code and markers read in a SEPARATE step (L4): `GATE_EXIT=0`, all **10** `ASSAY_GATE_PHASE=` markers in order, terminated by `ASSAY_REGISTERED_GATE_COMPLETE=1`. The wheel built from the exact OID (`assay-2.4.3.dev22+g371a4f7b`) — a genuine self-hosted run, not the report's older `8aa62c62` transcript |
| 3454 passed / 11 skipped | `python3 -m pytest tests/ -q` → `3454 passed, 11 skipped, 1 warning in 343.84s`, exit 0. Exact match |
| 153 new tests / 8 modules | `--collect-only -q` over the 8 modules → `153 tests collected`. Exact match |
| **the fixtures are real, unedited tool output** | Copied `tests/fixtures/coverage/probe-js` out of the repo, `npm install` from its own pins (resolved `vite 7.3.6`, `vitest 3.2.4` — matching PROVENANCE), ran both providers. After stripping ONLY the absolute path prefix and each record's own `path` field, **both committed artifacts are deeply identical to my regeneration** — same keys, same `statementMap`, `s`, `fnMap`, `f`, `branchMap`, `b`, `all`, record for record. This is the A-334 bar met properly |
| every PROVENANCE fact | Re-derived from **my own** output, not the committed file: v8 has zero multi-line extents anywhere; istanbul carries `format.ts` `[13,15]/[24,32]/[33,37]/[34,36]` and `roles.ts` `[7,11]`; `branchy.ts` istanbul `[2,4]`=1 with `[3,3]`=0; branchMaps 6 arcs/2 covered (istanbul, typed `if`/`if`/`cond-expr`) vs 4/1 (v8, all typed `"branch"`, one location each, one spanning `[1,10]`, one starting at the closing brace on line 7); every istanbul `end.column` is `null`; `types.d.ts` absent from both; `typesonly.ts` v8-only with `"statementMap": {}`; `orphan.ts` `"all": true`; no `skip` marker in either; all three test-naming conventions absent from both |
| innermost-wins on the real adversarial case | Parsed my own istanbul artifact with the shipped parser: `branchy.ts` → `executed={2,4,5,6,7}`, `missing={3,8,9}`. Line 3 is missing, not falsely covered. `_paint`'s sort key `(start-end, count)` (`coverage_istanbul_json.py:262`) paints widest-first then narrower over the top, ties by ascending count so MAX survives — correct on inspection and on the real data. `test_three_levels_of_nesting_resolve_innermost_first` re-derived by hand: correct |
| both canary transcripts | Applied `inject_uncovered_line` to the real `roles.ts` and ran `vitest run --coverage`: got the report's **exact** map `{7:1, 8:1, 9:1, 10:1, 11:1, 17:1, 18:2, 19:1, 20:1, 23:1, 24:0, 25:0, 26:0}`, suite still `4 passed / 6 tests`. Applied `inject_import_break` and got a real `FAIL src/break.test.ts … Error: assay-canary-import-break` at `roles_break.ts:23:7`. Also confirmed the uncovered canary yields count-0 lines under the **istanbul** provider too (`[24,24,0]`, `[25,25,0]`), which the report did not check |
| `normalize_coverage_key` no-op is correct, not a shortcut | Mutation-tested. Replacing `evaluate._to_repo_relative_key`'s absolute branch (`evaluate.py:617`) with `return key` kills **6** tests, incl. both CLI lane tests and `test_a_fully_covered_change_passes`. Replacing `JavaScriptAdapter.normalize_coverage_key` with `key.lstrip("/")` kills **6**. The `$PWD/src/app.ts` key in `test_cli_run_javascript.py:48` is the real snapshot-relative shape, and the tests are non-vacuous |
| `branches = None` is really what ships, and fails honestly | `coverage_istanbul_json.py:247` unconditional; `derive_branch_capability` → `"unavailable"` on both real artifacts; `runner.py:924` renders `NO_MEASUREMENT`/`BRANCH_UNAVAILABLE` for `require_branch = true`. No silent default to either producer's shape |
| scope boundary held | Diff touches **no** `adapters/base.py`, `evaluate.py`, `registry.py`, `coverage_parsers/model.py`, or `schemas/`. Schema `language` is `{"type":"string","minLength":1}` (`$defs/judgment_resolved/properties/language`) — open string, no bump needed, as claimed. `generate_mutation_sites` is unconditional `"UNSUPPORTED"` (`javascript.py:347`), `external_tools = ()`, no `subprocess` import, no partial R2 groundwork |
| no sniff collision | All 12 artifacts in `tests/fixtures/coverage/` plus my 2 regenerated ones sniff to exactly one `FORMAT_REGISTRY` key each |
| decision-id numbering | `main`@`62fe368f` ends at A-326. `feature/assay-b018-b019-b035-v8-synergy` has consumed **A-327..A-334**. This branch starts at A-340. No collision; gap A-335..A-339. The header note (`decisions.md`, "The id numbering deliberately skips A-327..A-339") is present and its reasoning is sound |
| B038/B039 filed honestly | B038 owns both the branch-arc refusal and the type-only-module gap, with the acceptance criteria stating the open question rather than hiding it. B039's claim verified at source: `coverage_parsers/go_cover.py:91` does have the identical unbounded `range(start, end + 1)` with no ceiling |

---

## Blockers

None. The parser and adapter code is correct: it faithfully reports what the
artifact says, and the gate is green at HEAD.

---

## Major

### M1. The shipped docs recommend the provider that produces FALSE GREENS, and this was never measured

`@vitest/coverage-v8` — the provider README.md:173 presents as the default
(`provider: 'v8',  // or 'istanbul' — both emit this format`) and
CONSUMERS.md:524 pastes into its worked config, and which CONSUMERS.md:613
actively tells consumers to *prefer* — reports provably-never-executed lines as
**executed**, and an `assay` R1 lane therefore PASSes on them.

Isolated and reproduced from scratch (9 lines of TypeScript, `vitest 3.2.4`):

```ts
// m3.ts
export function k(v: number): number {   // 1
  if (v === 0) return 0                  // 2   <- k(0) returns HERE
  const a =                              // 3
    v > 3                                // 4
      ? 10                               // 5
      : 20                               // 6
  const b = a + 2                        // 7   <- never runs
  return b                               // 8   <- never runs
}                                        // 9
```
The only test is `expect(k(0)).toBe(0)`.

```
v8 m3.ts line->count : {1:1, 2:1, 3:0, 4:0, 5:0, 6:0, 7:1, 8:1, 9:1}
istanbul m3.ts       : [(2,2,1),(4,4,0),(7,7,0),(8,8,0)]   (correct)
```

Driven through the shipped `evaluate_coverage` with changed lines `{7, 8}` at
`fail_under = 100.0`:

```
v8       : outcome=PASS  pct=100.0  covered=2  executable=2  missing={}
istanbul : outcome=FAIL  pct=0.0    covered=0  executable=2  missing={'src/m3.ts': [7, 8]}
```

The trigger is a **multi-line conditional (ternary) expression**: every line
after it in the same block is falsely reported executed. (A multi-line binary
expression does not trigger it — checked.) `coverage.experimentalAstAwareRemapping=true`
does **not** fix it — checked; lines 7/8 stay at count 1.

This is not hypothetical and not confined to my probe: **the committed v8
fixture already contains an instance of it.** In
`coverage-istanbul-json.vitest-v8.json`, `src/format.ts` lines **17 and 18**
carry count 1, although the only test is `relativeTime('')`, which returns at
line 9 — the multi-line ternary at `[12,15]` is the same trigger. The
end-to-end module offers `frozenset(range(1, 41))` over `format.ts`
(`test_evaluate_javascript_end_to_end.py:127-131`) and asserts nothing that
would notice.

Why this is Major rather than Minor: the report's central evidentiary claim is
that both providers were measured. They were measured for **shape** (extent
geometry, branchMap typing, key spelling) — never for **line-level
correctness**, which is the one property R1 rests on. That is exactly the
A-334 failure mode this review was asked to look for, on the axis that matters
most. And the change then ships guidance steering the first real consumer
(`webapp-ui-react`) onto the unsound provider.

The defect belongs to the producer, not to this parser — which is why this is
a docs/decision correction plus a filing, not a code rejection. `istanbul` has
no observed false green anywhere in my measurements: it only ever *omits*
lines (see M2), which removes them from the denominator rather than claiming
they ran.

### M2. A-342's "leaves no line of a measured file unattributed" is false under `@vitest/coverage-istanbul`, and the test that pins it cannot fail

Measured on the committed istanbul artifact: **23 non-comment lines across 6
files** are in neither `executed` nor `missing`. Specifically every function
declaration line (`branchy.ts:1`, `hinted.ts:1`, `orphan.ts:1`, `roles.ts:17`,
`format.ts:8`, `Badge.tsx:8`), every function-level closing brace
(`branchy.ts:10`, `hinted.ts:7`, `orphan.ts:3`, `roles.ts:20`,
`format.ts:39`), and `format.ts:12` (`const date =` — a genuinely executable
line whose istanbul statement starts at 13). Under v8 only 13 lines are
unattributed and all are genuinely non-code.

Because `requires_span_attribution = False`, those lines take `evaluate.py`'s
**rule 4** — silently dropped from both numerator and denominator. Measured
end-to-end: a diff touching only `src/roles.ts:17` gives
`executable=0, covered=0 → PASS` under istanbul, versus `executable=1,
covered=1` under v8. That is the "srdm silent-excuse direction" that
`coverage_istanbul_json.py:60-64` explicitly says option (b) was rejected for.

The change contradicts itself about this. Honest, in two places:
- `coverage_istanbul_json.py:76-79` — "…and — under `@vitest/coverage-istanbul`
  — a function's own signature line — stays unclassified and falls to rule 4";
- `test_coverage_istanbul_real_fixtures.py:245` — "…and the closing braces the
  istanbul provider leaves untracked".

Overstated, in two places:
- `decisions.md` A-342 — "…and leaves no line of a measured file unattributed";
- report §3.3, same sentence.

And the test the report offers as the pin (§5, "zero unclassified lines across
all five measured files") is **structurally incapable of failing**:
`test_evaluate_javascript_end_to_end.py:121
test_no_line_of_a_measured_file_is_ever_unclassified` asserts
`result.unclassified_lines == {}`, but `evaluate.py:426` only ever populates
that mapping inside `if unattributed and adapter.requires_span_attribution:`,
and this adapter declares `False`. The assertion holds for **every** artifact,
including a deliberately broken one. Only its `considered == 5` is live.

Neither is a false green, so this ranks below M1 — but it is a decision record
asserting a measured fact that its own parser docstring refutes, backed by a
vacuous test, in a project whose standard is "measured, not assumed".

---

## Minor

- **The canary verification is a report transcript, not committed evidence.**
  All 11 tests in `test_adapters_javascript_canary_injection.py` are string
  assertions; nothing runs `vitest` as a subprocess. I reproduced both
  transcripts exactly, so A-345's claim is **true** — but the evidence pattern
  the change used so well for coverage (commit the real artifact) was not
  applied here, and A-345 reads as though it were. Latent rather than live,
  since `javascript` is not registered at R3. Either commit the post-injection
  coverage artifact as a fixture, or say plainly in A-345 that the
  verification is a one-off transcript.
- **`MAX_CLASSIFIED_LINES = 2_000_000` is bounded but very loose.**
  `coverage_istanbul_json.py:133`. Its own paired must-succeed control
  (`test_an_extent_just_inside_the_bound_still_parses`) drives peak RSS to
  **~352 MB** (measured) from a ~100-byte artifact, on every suite run. The
  docstring itself notes "the largest witnessed here is four figures"; a
  ceiling 1-2 orders of magnitude lower preserves the identical safety
  argument at a tenth of the memory. The bound is on line COUNT, not on
  memory, and the amplification factor is what makes that gap matter.
- **No direct pin on `normalize_coverage_key` being a no-op.** Python has
  `test_adapters_python_normalize_coverage_key.py`; javascript has none. An
  *inert* wrong strip (`key.removeprefix("/build/agent/7/")`) survives the
  entire suite — only a strip that hits real keys is caught. One assertion in
  `test_the_javascript_adapter_declares_the_expected_protocol_surface` closes it.
- **`cli.py`'s R2-refusal docstring names the wrong layer for the shape its own
  test exercises.** `cli.py:261-271` says a `javascript` R2 lane "is refused by
  `assay.registry.get_adapter`". A lane declaring `judge.mutation.operators` —
  which is what `test_cli_run_javascript.py:202` writes — is refused earlier,
  at config load, by `config.py:1773`'s foreign-operator guard, because no
  `javascript:` operator exists in `MUTATION_OPERATORS_BY_LANGUAGE`. Both
  refusals are honest and both name `javascript` (verified there is no
  `KeyError` path: `config.py:1765` uses `.get(language or "", ())`), so this
  is wording only.

---

## Nitpicks

- `test_all_four_adapters_coexist_in_one_registry_each_independently_addressable`
  (`test_adapters_javascript_registration.py:130`) registers **three**
  adapters — `GoAdapter` is absent — while its name and docstring say
  "registering every real adapter together" / "now at four languages". The
  report §5 repeats the claim.
- `decisions.md`'s gap note says the synergy wave "had already consumed
  A-327..A-333"; it is now at **A-334**. The note anticipated this ("with room
  to add more before it merges") and the A-335..A-339 gap still absorbs it, so
  the reasoning stands — the number is just stale.
- No `package-lock.json` is committed with `probe-js`, so transitive deps are
  unpinned. Not a live problem (I regenerated byte-identical output today) and
  the artifacts themselves are committed and marked un-editable, but a lockfile
  would make the "produced outside this repository" recipe exactly reproducible.
- PROVENANCE's istanbul `branchMap` description omits that an `if`'s else-arm
  location is `{"start": {}, "end": {}}` (empty objects) in real output —
  irrelevant to this parser, relevant to whoever picks up B038.
- The self-flagged over-approximation ("a lone closing brace in a
  never-executed statement counts as an uncovered changed line",
  `coverage_istanbul_json.py:71-79`) **is defensible** — I tried to construct a
  misleading case and could not. A brace only ever inherits the status of the
  statement it is structurally part of, and innermost-wins already rescues the
  nested case (`branchy.ts:4` is correctly `executed`, not missing). It fails
  toward scrutiny, as claimed.

---

## Conditions for ACCEPT

1. **Flip the documented provider default to `@vitest/coverage-istanbul`**
   (README.md:173, docs/CONSUMERS.md:524, and the "prefer the v8 provider"
   sentence at CONSUMERS.md:613, which currently points at the unsound one),
   with M1's measured reason stated. File the v8 mis-attribution as its own
   backlog item (or fold into B038 — same root cause: the producer is an
   undeclared fact and the two disagree). Add the `m3.ts` repro or the
   `format.ts:17-18` instance as the witness.
2. **Correct A-342's "leaves no line of a measured file unattributed"** in
   `decisions.md` and report §3.3 to match what `coverage_istanbul_json.py:76-79`
   and `test_coverage_istanbul_real_fixtures.py:245` already say, and add one
   line to CONSUMERS.md's "Three things that behave differently" telling
   consumers that under istanbul a function signature line or a lone closing
   brace falls to rule 4 and leaves the denominator.
3. **Replace or re-point
   `test_evaluate_javascript_end_to_end.py:121`** so it can fail — e.g. assert
   that `result.executable` equals the number of offered lines actually in
   `executed | missing` — and parametrize the end-to-end module over **both**
   committed artifacts rather than v8 only. As written it pins nothing.

None of the three requires touching the parser, the adapter, the protocol, the
core, or the schema. The engineering in this change is genuinely good — the
fixtures are real (I proved it the expensive way), the resolution rule is
correct on real adversarial data, the refusals are typed and non-vacuous, and
the scope boundary held exactly as claimed. What it missed is that it measured
the two producers' *shapes* and never their *accuracy*, and then recommended
the inaccurate one.

---
---

# ROUND 2 — re-verification of `3a677f95` (6 commits on `371a4f7b`)

**Verdict: ACCEPT.** All three round-1 conditions met, both Majors closed, all
five Minors and all Nitpicks fixed, two of them beyond what was asked. Nothing
new found. Everything below was re-run here, not read off the report.

## Independently re-verified

| | |
|---|---|
| **gate** | `./run-gate.py --worktree … tester-unified` on a clean tree at HEAD `3a677f95`. Exit read in a SEPARATE step: `GATE_EXIT=0`, **10** phase markers in order, `ASSAY_REGISTERED_GATE_COMPLETE=1`, wheel `assay-2.4.3.dev28+g3a677f95` built from the exact OID (one commit past the report's `dev27`, which is the report's own transcript commit — expected) |
| **suite** | `pytest tests/ -q` → `3489 passed, 11 skipped`, exit 0. 3454 + 35 = 3489; nothing regressed |
| **the four defect artifacts are real** | `npm ci` from **the committed lockfiles** into two clean trees (`vitest 3.2.4`, `vitest 4.1.11`), ran both providers in each. All four committed artifacts are **byte-identical to my own runs** (modulo the `path` field) |
| **the defect, against ground truth** | Computed dead lines from `shapes.ts` structurally (every line below each `if (v === 0) return 0` guard), then checked each artifact. v8 falsely reports executed: **Vitest 3** → `[10,11,16,17,18]`, **Vitest 4** → `[10,11,17,18]`. Exactly `V8_FALSE_GREENS`. istanbul: **zero** false greens in both. My round-1 numbers matched once braces are excluded, which their `NEVER_EXECUTED` correctly does |
| **one-line ternary — my round-1 characterisation was too narrow** | Confirmed. `ternaryOneLine` (`const a = v > 3 ? 10 : 20`) triggers it in both majors. The implementer's correction is right and my review was wrong on this point |
| **no version to pin past** | Confirmed on both currently-released majors |
| **only ternaries trigger it** | Confirmed: `binaryMultiLine`, `callMultiLine`, `objectLiteralMultiLine` are all correct under the same provider in the same artifact. So sound and unsound records are not structurally distinguishable |
| **the version-drift argument against shape-sniffing** | This is the load-bearing claim behind "option (b) is unavailable", and it verifies **stronger than argued**: Vitest 3's v8 provider emits **0** multi-line extents; Vitest 4's emits **4** — and they are the *identical* extent tuples istanbul emits, `[(7,9),(24,25),(32,35),(42,45)]`. Under Vitest 4 there is literally no extent-geometry discriminator between the two providers. Any shape heuristic written today would be both forbidden by A-007 and factually broken |
| **PASS/FAIL through the shipped code** | Drove all four artifacts through `evaluate_coverage` at `fail_under=100.0` on provably-dead lines: v8 → **PASS 100.0%** (3 of 4 cases; Vitest 4 partially recovers `ternaryOneLine` to 66.7% FAIL but still PASSes 100% on `ternaryMultiLine`), istanbul → **FAIL 0.0%** in all four |
| **`format.ts:17-18` instance pinned** | Present as its own test |

## Round-1 conditions

**Condition 1 (M1) — MET, and exceeded.** Ruling (a) is the right call and my
own measurement supports it more strongly than the report argues (see the
version-drift row). README/CONSUMERS now say `provider: 'istanbul'` only, with
a 9-line repro, the four measured facts, and — the sentence that matters —
"**assay cannot detect this for you, and does not pretend to.**" That is the
honest shape. A-346 records the ruling *and* the process lesson ("a shape match
is not a correctness check"), which is the more transferable finding. B040
filed, and honestly marks the upstream report as *not yet done* rather than
claiming it.

**Condition 2 (M2) — MET.** A-342 corrected in place, A-326 style: the
withdrawn phrase left visible next to its measured refutation, with the true
narrower guarantee stated ("every line any statement extent covers is
classified"). Same correction applied to `adapters/javascript.py`'s docstring
and report §3.3. CONSUMERS gained a fourth "behaves differently" item spelling
out the practical consequence (a signature-only diff can report
`executable = 0` and PASS) — and offers `judge.mode = "whole_target"` as a real
mitigation, which I had not thought to suggest.

**Condition 3 (M2 test) — MET.** The vacuous pin is gone. I mutation-tested the
replacements: reducing `_paint` to `istanbul-lib-coverage`'s start-line-only
behaviour now kills **5** tests including both new ones
(`test_the_unattributed_line_set_is_exactly_what_was_measured[istanbul]`,
`test_extent_expansion_classifies_strictly_more_than_start_lines_alone`). The
old test survived that mutation. `UNATTRIBUTED`'s literal sets match my
independent round-1 measurement exactly, file for file and line for line
(13 v8 / 23 istanbul). The e2e module is parametrized over both artifacts. The
denominator-consistency test correctly does **not** die under the mutation —
and its own docstring says up front that it does not pin the parser, which is
the right way to ship a test like that.

## Round-1 Minors / Nitpicks

All fixed. Verified directly: canary evidence moved to committed fixtures —
`roles.uncovered-line-injected.ts` is byte-for-byte what
`inject_uncovered_line` produces (checked), and both provider artifacts show
the canary body as missing, matching my round-1 live `vitest` run exactly
(`{23:1, 24:0, 25:0, 26:0}` under v8). `MAX_CLASSIFIED_LINES` tests now
monkeypatch the ceiling: peak RSS **352 MB → 77 MB** (measured), pinned from
both sides. `test_all_four_adapters_coexist…` now actually registers
`GoAdapter`, with the correction noted in its own docstring. `cli.py`'s
two-layer refusal documented in the order a real lane hits them — and their
finding is sharper than mine: a config-valid `javascript` R2 lane is **not
constructible at all** today. Lockfiles committed. A-342's stale synergy-wave
id corrected.

## The two questions flagged for me

**1. Should assay refuse v8-produced artifacts pending B038/B040? — No, and
this is not a close call.** Refusing requires identifying, and identifying
requires either a *declared* producer (which does not exist yet, and is exactly
B040(b)/B038's scope) or shape-sniffing. Sniffing is forbidden by A-007 *and* —
per my own measurement above — factually impossible under Vitest 4, where the
two providers emit identical extent geometry. A heuristic would therefore be
wrong, silent, and worse than the warning: it would manufacture a false sense
of protection while refusing honest nyc/Jest artifacts, which share istanbul's
correct instrumenter. Building an opt-in `judge.coverage.producer` key inside
B036 would also widen the config surface for one language mid-change. The
documentary mitigation plus B040 is the correct disposition.

**2. Three tests asserting an upstream bug persists — the tradeoff is right,
with one wording correction.** Pinning a defect as a witness is sound: it is
the stated reason for a shipped ruling, so it should break when the reason
does. But the tests read **committed artifacts**, not a live `npx vitest`, so
they cannot fail on their own when upstream fixes the provider — they change
only when someone regenerates the fixtures. Freezing them is right (the suite
must stay Node-free, DESIGN-GUIDE §10), so the design is correct; it is the
*framing* that overreaches. The module docstring and B040's acceptance both say
the tests "are written to FAIL if it is fixed", which reads as an automatic
signal it is not. B040's checkbox already owns the manual re-check, so the
maintenance cost is real but small and correctly homed. Worth one wording pass
("if you regenerate these fixtures against a newer provider and this fails,
upstream fixed it — revisit A-346"), not worth another round.

## Remaining — non-blocking, for whenever

- The wording nit in question 2 above.
- The four defect artifacts carry an absolute key under a
  `/tmp/claude-…/scratchpad/defect/` path from the producing session. Harmless
  — it is genuine tool output and the tests rebase the prefix — and arguably
  good evidence that it was not hand-authored. Just untidy in a committed
  fixture.

## Verdict

**ACCEPT.** Merge, release and deploy per standing authorization.

The round-2 work is better than the fix it was asked for. It reproduced M1
independently before touching anything, found it worse than I had reported
(one-line ternaries, both majors), built a probe whose ground truth needs no
coverage tool at all, committed four real artifacts plus lockfiles, and then
declined to build the heuristic that would have looked like a stronger fix and
been a silent lie. The A-346 process lesson — *a shape match is not a
correctness check; A-334's "check against a real thing" has to mean the thing
the verdict actually claims* — is worth upstreaming to nyxloom `LESSONS.md`
beyond this repo.
