# nyxloom-P101 — adversarial CARVE REVIEW

> **FINAL VERDICT: ACCEPT** (round 3, carve frozen at `ac61f7d9`,
> `input_revision: a32c4bb6`). Rounds 1-3 are retained below unedited as the
> record: round 1 NOT READY (7 blockers) → round 2 REJECT (1 new blocker, R2-1)
> → round 3 **ACCEPT**. Every finding across all three rounds is closed and each
> closure was verified by my own execution, not by reading the diff.

---

## Round 1 (original)

**Reviewer:** fresh adversarial carve reviewer, no prior context on this package.
**Target:** `nyxloom-trove/handoffs/nyxloom-P101-retire-tier-band.md`
**Frozen at:** `a80264fb` (`input_revision: d50684d9`; the only diff between the
two commits is one line of the handoff itself — verified `git diff --stat`).
**Method:** blind pass against the live tree first (execution, not reading),
then adversarial attack, then reconciliation with NL-7 / the P100 reviews.

**VERDICT: NOT READY — 7 blockers.** The carve's central technical claim is
correct and I confirmed it independently by execution; the defects are in the
ORACLES and the implementation packet, which is where this package's value
lives. All seven are cheap to fix; none require re-carving.

---

## 0. What I verified independently, and where the carve is RIGHT

I ran the code before reading NL-7 or either P100 review. The carve survives on
its core claims:

| carve claim | my measurement | verdict |
|---|---|---|
| `_TIER_BAND` is a live ONE-WAY SUPPRESSOR, not dead code | executed `compute_review_depth_directive` with a 30-path scope + `["tests-pass","changed-line-coverage","mutation"]`: `implement-1`→`""`, `implement-2`→`""`, `implement-3`→fires, `luna-high`/`sonnet5-high`/`None`/`not-a-real-tier`→fires | **CONFIRMED** |
| tracked `routes.host.toml` declares `implement-1`/`implement-2` | `routes.host.toml:75,78` (+`implement-1-free:81`, `review-3:84`) | **CONFIRMED** |
| deployed file is different and pre-B16 | `paths.routes_path()` → `/home/vscode/.local/state/nyxloom/routes.toml`; tiers = `flash-high, flash-max, terra-med, luna-high, sonnet5-high, frontier-review, haiku-low, free-high`; no `implement-*` | **CONFIRMED** |
| "20+ handoffs declare `tier: implement-2`" | `archive/`: 22× `implement-2`, 1× `implement-1`, 3× `sonnet5-high`. `handoffs/`: 2× `luna-high` | **CONFIRMED** |
| NL-7's "lookup has returned `None` for every real handoff" is false | for 22 archived handoffs the lookup returns `2` | **CONFIRMED FALSE — carve is right** |
| threshold boundary: 5 → `""`, 6 → fires (rigorous gate) | executed n=4,5 → `""`; n=6,7 → fires | **CONFIRMED** |
| nyxloom's own gate declares no `mutation` | `nyxloom-trove/nyxloom.toml:91` `asserts = ["tests-pass","changed-line-coverage","canary-verified"]` | **CONFIRMED** (path is wrong — see N3) |
| exactly one production caller | `src/nyxloom/effects_review.py:434`, no try/except around it | **CONFIRMED** |
| 12 test call sites at the tabulated lines | all 12 line numbers verified exact | **CONFIRMED** |
| four `implement-3 does not exist` citations | `config.py:854`, `effects_carve.py:536`, `test_config.py:683`, `test_daemon.py:1202` — all four say exactly that at the named lines | **CONFIRMED** |
| option 2 has no data to read | tier tables in both `routes.host.toml` and the deployed file carry **only** `routes = [...]`; band lives in the tier name and in `#` comments | **CONFIRMED** |
| lint emits exactly four L13 warnings, no errors | reproduced verbatim, `EXIT=0` | **CONFIRMED**; the named-negative false-positive judgment is legitimate — two of the four (`config.py`, `effects_carve.py`) are in `forbid`, so "fixing" the warning would contradict `forbid` |
| the ownership-inventory rows stay inside tolerance | recomputed — conclusion holds, **reasoning is false** | see N2 |

The reverse-dependency sweep re-run with `git grep`, no pathspec: the
`_TIER_BAND` and `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD` tables are exactly right.
The `compute_review_depth_directive` table is right on all 12 call sites but is
not the complete grep it claims to be (N5).

---

## 1. Blocking ambiguities

### BLOCKER 1 — Work item 9 orders an edit to a file that is not in `scope.touch`

Handoff lines 464-469 (Work item 9): *"Verify `docs/plan-next-batches.md:298`.
… **If that line states the three-argument form as current fact, update it** to
the two-argument form."*

`docs/plan-next-batches.md` appears **nowhere** in `scope.touch` (frontmatter
lines 14-26) and nowhere in `forbid`. `escalate_if` line 150 then forces the
implementer to stop: *"any non-test file outside scope.touch needs an edit …
report BLOCKED naming the file and the symbol, do not widen scope
unilaterally."*

And the judgment call is genuinely 50/50. Read the real text
(`docs/plan-next-batches.md:293-307`): it sits under a heading
`## BATCH C … ✅ DONE (merge d64138a1, 2026-07-26)` — historical — but the
sentence itself is present tense and describes current behaviour:

> Instead `adapters.compute_review_depth_directive(tier, scope_touch, gate_asserts)` derives a
> review-depth directive from two ALREADY-EXISTING signals — the handoff's `Frontmatter.tier`
> band (`implement-1/2/3`, scope-size fallback >5 touched paths) …

A careful implementer reads "derives … from the handoff's `Frontmatter.tier`
band" as a current-fact claim, concludes it must be updated, finds the file out
of scope, and exits BLOCKED. That is a wasted dispatch caused entirely by the
carve.

**Prescription — pick one and close the degree of freedom:**
(a) add `"docs/plan-next-batches.md"` to `scope.touch` annotated
`# Work item 9 ONLY, line 298: the three-arg signature in BATCH C's DONE record`,
and pin the replacement sentence verbatim the way Work items 2/3 pin theirs; **or**
(b) rewrite Work item 9 to remove the edit branch entirely: *"leave it — it is a
`✅ DONE` batch record, historical by construction; record in the REPORT that
line 298 names the pre-P101 three-argument form and was deliberately left as a
historical record."* Then `docs/plan-next-batches.md` needs no scope entry.

### BLOCKER 2 — controlled break B7 is a behavioural no-op; O2 has no valid mutation proof

Handoff line 452: `| B7 | delete the `if band < _HIGH_BAND and not shallow:
return ""` early return | O2 |`. The same break is O2's first negative example
(handoff lines 65-67).

**Measured: deleting that early return changes nothing on any input.** After
the Work item 3 edit the function is:

```python
band = _HIGH_BAND if len(scope_touch or []) > _HIGH_BAND_SCOPE_TOUCH_THRESHOLD else _LOW_BAND
shallow = bool(_RIGOROUS_ASSERTS - set(gate_asserts or []))
if band < _HIGH_BAND and not shallow:
    return ""                       # <-- B7 deletes this
reasons = []
if band >= _HIGH_BAND: reasons.append(...)
if shallow:            reasons.append(...)
return " ".join(reasons)
```

In exactly the case the early return covers (`band < _HIGH_BAND and not
shallow`), `reasons` is empty and `" ".join([])` is `""` — the identical value.
I enumerated the full input space (0/1/8 paths × rigorous/shallow/None asserts):
**0 differing cases.** The early return is a pure fast path and is provably
dead.

Consequences: (i) B7 cannot make O2 fail, so O2 — the carve's designated
load-bearing safety oracle, and the reason the carve says the package is more
than a comment fix — ships with **no mutation evidence at all**; (ii) O2's
frontmatter negative states a falsehood that will be transcribed into the
REPORT.

**Prescription:** replace B7 with a break that actually inverts the neutral
case, e.g. `else _LOW_BAND` → `else _HIGH_BAND` in the new ternary (band always
high → 1 path + rigorous gate returns the high-complexity reason → the
`_PRE_D1_SIMPLE_REVIEW_PROMPT` snapshot equality at
`tests/test_adapters.py:2127-2128` fails). Rewrite O2's negative to name that
break, and drop the early-return example. Optionally state in Work item 3 that
the early return is retained deliberately as a fast path even though it is now
redundant, so a code reviewer does not flag it as dead code (see N8).

### BLOCKER 3 — O5's negative rests on a false measured claim, and B6 does not isolate O5

Handoff lines 125-128: *"Measured at input_revision: `tests/test_effects_review.py`
contains ZERO references to `review_depth` (`grep -c` → 0), so **without this new
test the caller edit has no oracle at all and a stale kwarg would ship green**."*

The grep fact is true. The inference is **false**, and I proved it by execution
rather than argument. I simulated controlled break B6 (post-fix two-argument
`compute_review_depth_directive`, un-fixed caller still passing `tier=`) via a
pytest plugin that rebinds `adapters.compute_review_depth_directive` to a
two-argument function — no worktree mutation — and ran
`tests/test_effects_review.py`:

```
FAILED tests/test_effects_review.py::TestWaveLeaseUnion::
       test_a_wave_holds_the_union_of_its_members_leases_deduplicated
```

`launch_review` has **no `try`/`except` anywhere between lines 380 and 475**, so
the `TypeError` at line 434 propagates straight out of the handler and the
pre-existing wave-lease test at lines 256-313 goes red. A stale `tier=` kwarg
would NOT ship green.

Two consequences:
1. **B6 proves nothing about O5.** It is caught by an unrelated pre-existing
   test, so witnessing its failure does not demonstrate O5 is non-hollow — which
   is the entire stated purpose of the Work-item-7 table ("the gate does not
   assert `mutation`, so this is the only proof these oracles are not hollow").
2. The justification for O5 existing at file level is wrong as written. O5 still
   earns its place — it pins the *value* end-to-end (that a 6-path
   `scope.touch` produces a `high-complexity` directive through the real
   caller), which nothing covers today — but that is a different argument.

**Prescription:** replace B6 with a break that isolates O5's genuinely new
property: in `src/nyxloom/effects_review.py:434-437`, pass
`scope_touch=[]` instead of `scope_touch=first_fm.scope.touch if first_fm is not
None else []`. That leaves the wave-lease test green (no `TypeError`) and fails
only O5. Rewrite O5's negative to say: *"the stale-kwarg case is already caught
by the pre-existing wave-lease test; what O5 uniquely pins is that the
frontmatter's `scope.touch` actually reaches the directive."* Keep the
`TypeError` observation as a secondary note, not as the load-bearing claim.

### BLOCKER 4 — Work item 8 hand-edits a GENERATED file, omits the required commands, and has no oracle

Handoff lines 454-462 (Work item 8): *"Update the status column for NL-7 in
`nyxloom-trove/backlog/INDEX.md` to match."*

`nyxloom-trove/backlog/INDEX.md:1` is:

```
<!-- GENERATED by `nyxloom backlog index` -- do not edit; regenerate. -->
```

and `src/nyxloom/backlog_entries.py:323` enforces it: *"BLG3: the committed
INDEX.md must byte-equal a fresh regeneration"*, message *"INDEX.md is stale
(regenerate: `nyxloom backlog index`)"*. A hand edit that happens not to
byte-match regeneration is a BLG3 lint error — and **nothing in the
`tester-unified` gate catches it**: `tests/test_backlog_entries.py` exercises
BLG3 against fixtures, not against the real trove. So a hand-edited or stale
INDEX ships green under O6.

The carve also omits the commands and one *required* argument. Verified against
the live CLI: `nyxloom backlog set-status` takes
`id {open,carved,fixed,withdrawn,obsolete}` with `--reason` **required for
`fixed`**. The implementer must invent the reason string.

**Prescription:** pin the mechanics in Work item 8:

```
python3 exec-nyxloom.py backlog set-status NL-7 fixed --reason "<pinned text>"
python3 exec-nyxloom.py backlog index
```

and add a REPORT evidence line (or, better, a seventh oracle) requiring
`python3 exec-nyxloom.py lint` over the trove to be BLG2/BLG3-clean after the
edit. Note that `set-status` may itself rewrite the entry's frontmatter/status
log, so Work item 8's prose rewrite must be sequenced around it.

### BLOCKER 5 — Work item 8's NL-7 correction is unpinned prose, and the carve's own summary of NL-7's error is imprecise

Work item 8 is the one item whose stated purpose is *"Leaving a fixed entry that
still misstates the mechanism would re-teach the wrong lesson to the next
reader"* (`scope.touch` annotation, handoff line 23). Yet it is the only
text-producing Work item with **no pinned replacement and no oracle** — while
Work items 2 and 3, whose texts are far less load-bearing, are both pinned
verbatim and (item 2) oracle-enforced.

That matters here because the carve's own summary table risks writing a NEW
falsehood. Handoff line 179 renders NL-7's first claim as *"`implement-N` never
a real tier | **Partly false.** `routes.host.toml` (the TRACKED matrix) has
declared …"*. But NL-7's actual words (`NL-7-*.md:27-29`) are *"have never been
real values in the **live** `routes.toml`"* — and the live/deployed file indeed
declares no `implement-*` tier, as the carve itself establishes 5 lines later.
On NL-7's own terms that half is **true**; NL-7's real error is the *inference*
(conflating "not a routable live tier" with "never appears as a handoff's
`tier:` field"), not the premise. An implementer paraphrasing the summary table
into NL-7 will write "NL-7 was wrong that `implement-N` was never a live
routes.toml tier", which is itself wrong.

**Prescription:** pin the replacement "Observed mechanism" paragraph verbatim,
exactly as Work items 2 and 3 are pinned, and correct the summary table's first
row to state NL-7's error precisely: *the premise about the live `routes.toml`
is correct; the inference that the lookup therefore returned `None` for every
real handoff is false, because handoffs declare `implement-2` from the TRACKED
matrix regardless of whether that tier is routable.*

### BLOCKER 6 — O3's observable overclaims by a wide margin, and Work item 3's docstring edits have no oracle at all

**(a) The overclaim.** O3's observable (handoff lines 74-79) says the test
*"proves no hardcoded tier-name-to-band table can be reintroduced"*, checking
for *"no `ast.Dict` node anywhere in the module whose keys include a string
literal matching `^(implement|review|carve)-\d+$`"*. I implemented that exact
check and ran it against nine candidate reintroductions:

| reintroduction shape | O3 result |
|---|---|
| `_TIER_BAND = {"implement-1": 1, ...}` (true positive) | CAUGHT |
| `{"implement" "-1": 1, "implement"+"-2": 2}` | CAUGHT |
| `dict(zip(("implement-1","implement-2","implement-3"),(1,2,3)))` | **EVADES** |
| `dict([("implement-1",1),("implement-2",2),("implement-3",3)])` | **EVADES** |
| `{f"implement-{i}": i for i in range(1,4)}` | **EVADES** |
| `if t=="implement-3": return 3 / elif …` | **EVADES** |
| `match t: case "implement-3": return 3` | **EVADES** |
| `int(t.rsplit("-",1)[1]) if t.startswith("implement-") else 1` | **EVADES** |
| `{"luna-high":1,"sonnet5-high":2,"frontier-review":3}` (deployed names) | **EVADES** |

Seven of nine evade. The carve's *negative* is narrowly accurate (renaming the
variable does not evade), but the observable's headline claim is false and will
be copied into the REPORT as evidence.

**(b) The missing oracle.** Work item 3 pins a replacement docstring paragraph
"EXACTLY" (handoff lines 376-383) and separately orders the line-209
parenthetical `(i.e. implement-1/implement-2, or the fallback's small-scope
case)` → `(a small scope_touch)`. **Nothing checks either.** O4 pins only the
Work-item-2 comment block. So an implementer can paraphrase or silently skip
both docstring edits and stay green — leaving `implement-1`/`implement-2` prose
inside the very function this package exists to de-tier, which is precisely the
"misleading comment" failure mode NL-7 and the P100 carve review both called
out.

**One prescription closes both.** Change O3's scan from `ast.Dict` keys to
**every `ast.Constant` string value in the module**, asserting none matches
`(implement|review|carve)-\d+`. Measured feasibility: today `adapters.py`
contains exactly two such constants — `_TIER_BAND` at line 181 and the
`compute_review_depth_directive` docstring (node at line 196, tier names at
lines 202 and 209) — and Work items 2 and 3 remove both, so the strengthened
check is **green on the correct fix and red on an incomplete one**. The pinned
Work-item-2 comment is a `#` comment and therefore invisible to the AST, so it
does not false-positive (this is exactly the property O3's rationale already
relies on). The strengthened check additionally catches `dict(zip)`, the
list-of-tuples form, the if/elif chain, the `match` statement, and the
`.startswith("implement-")` form. It still misses the f-string comprehension
and the deployed-names dict — so **narrow the observable's wording** to what it
actually proves: *"no tier-shaped string literal survives anywhere in
`adapters.py`'s AST."*

### BLOCKER 7 — Work item 4's per-line disposition table is incomplete, contradicting the 2d contract-class claim

The handoff declares *"Contract class: `2d` — every interface, exact
replacement text, and test disposition is fixed below"* (line 163). The
disposition table (lines 407-413) is not complete:

| real file:line | left undispositioned | consequence |
|---|---|---|
| `tests/test_adapters.py:2134` | `"""Oracle 3: LOW band (**implement-1**) but a shallow gate still fires` | the table's `2141, 2148` row says only "drop `tier=`; keep 1 path" — the tier name survives in the docstring |
| `tests/test_adapters.py:2159-2163` | `"""Oracle 4: an absent/unparseable tier falls back to a scope.touch-size proxy … isolating that the **FALLBACK** band alone …` | the carve says "say so in the docstring" but pins nothing; "fallback" is no longer true — it is now the only path |
| `tests/test_adapters.py:2158` | the test's NAME, `test_compute_review_depth_directive_scope_size_fallback` | not mentioned at all; rename or explicit keep is undecided |

**Prescription:** pin replacement docstring text for 2134 and 2159-2163 (or
state explicitly that they are left alone and why), and decide the 2158 rename
in the carve rather than leaving it to the implementer. Note that BLOCKER 6's
strengthened O3 does **not** cover these — its scan root is `adapters.py`, not
the test file — so they remain unenforced by design; pinning is the whole
mechanism here.

---

## 2. False-PASS attacks — one plausible wrong implementation per oracle

| oracle | wrong implementation that still PASSES | why it passes | fix |
|---|---|---|---|
| **O1** | Keep the tier-derived suppressor but rename the parameter (`handoff_tier`), or read the tier from a module-level global the caller sets. | `inspect.signature(...).parameters` has no key `tier`; 6 paths still fire and 5 paths still return `""` when the global is unset in the test. The suppressor is fully intact for real dispatches. | Assert the parameter tuple **equals exactly** `("scope_touch", "gate_asserts")`, not merely that `tier` is absent. |
| **O2** | Keep the tier suppressor and give it a `tier: str \| None = None` default. | O2's updated test (1 path, rigorous gate) returns `""` either way. Only O1's signature half catches it, and only if the parameter is spelled literally `tier`. | Same as O1: exact-parameter-tuple equality. |
| **O3** | `_BANDS = dict(zip(("implement-1","implement-2","implement-3"), (1,2,3)))` — **measured to evade**. | No `ast.Dict` node exists at all. | BLOCKER 6's prescription (scan all string `Constant`s). |
| **O4** | Paste the pinned block verbatim at the **end** of `adapters.py` (or inside an unrelated `"""` literal) while leaving the real band block above wrong, and delete the old false sentence. | The `in` containment test passes; both absence assertions pass. The block's `_LOW_BAND`/`_HIGH_BAND`/`_HIGH_BAND_SCOPE_TOUCH_THRESHOLD` lines merely re-bind the same values, so nothing breaks. | Anchor it: assert `src.count(PINNED) == 1` **and** `src.index(PINNED) < src.index("def compute_review_depth_directive")`. |
| **O5** | Wire the caller to a hardcoded list — `scope_touch=["a","b","c","d","e","f"]` — instead of `first_fm.scope.touch`. | The captured `review_depth` still contains `high-complexity`. The caller is wrong; the oracle is green. | Assert **both** directions in one test: `_FM` with 6 paths → contains `high-complexity`; `_FM` with 1 path → does not. This also supplies the isolating controlled break BLOCKER 3 asks for. |
| **O6** | Skip Work items 8 and 9 entirely; hand-edit `INDEX.md` into a byte-mismatched state. | The `tester-unified` gate runs pytest; nothing in it reads the trove backlog INDEX or `docs/plan-next-batches.md`. BLG3 only fires under `nyxloom lint`, which the gate does not run. | BLOCKER 4's prescription (pin the commands + require a lint-clean evidence line). |

---

## 3. Missing implementation-packet content — values the implementer must invent

1. `--reason` text for `backlog set-status NL-7 fixed` (**required** by the CLI). (BLOCKER 4)
2. The `backlog set-status` / `backlog index` invocations themselves. (BLOCKER 4)
3. The rewritten NL-7 "Observed mechanism" prose. (BLOCKER 5)
4. Replacement docstrings for `tests/test_adapters.py:2134` and `2159-2163`, and the 2158 rename decision. (BLOCKER 7)
5. `docs/plan-next-batches.md:298` — both the judgment and, if edited, the replacement sentence. (BLOCKER 1)
6. The six path strings for O1/O5 fixtures — genuinely free, and fine.
7. Whether the now-provably-dead early return is kept. Work item 3's edit map implies "kept" but never says so, and O2 silently depends on it existing. (N8)

---

## 4. Scope / dependency defects

- **Out of scope but required:** `docs/plan-next-batches.md` (BLOCKER 1). This is
  the classic package-killer the checklist warns about — an instruction whose
  execution path leads straight to `escalate_if` line 150.
- **`forbid` is correct and sufficient.** I confirmed the package does not need
  `src/nyxloom/lint.py` (this removes a tier *consumer*, not tier *validation*),
  `reference/AUTHORING.md` (its §3b anti-patterns are already copied verbatim
  into the handoff at lines 475-497, so no read is needed),
  `src/nyxloom/config.py` / `src/nyxloom/effects_carve.py` (O3 names them as
  permitted negatives; nothing reads or edits them), or `routes.host.toml` (no
  routing change).
- **Oracle satisfiability within `scope.touch`:** O1, O2, O3, O4 → `tests/test_adapters.py`
  + reads `src/nyxloom/adapters.py`, both in scope. O5 → `tests/test_effects_review.py`
  + `src/nyxloom/effects_review.py`, both in scope. O6 → the gate, with
  `tests/test_core_characterization.py` and the inventory doc listed VERIFY-ONLY.
  **All six are satisfiable within scope.** No oracle needs a forbidden file.
- **`escalate_if` coverage** is good and the E-008 checkpoint clause is present
  and correctly parameterised.
- No dependency defect: P100 (`480ef39f`) is merged, `depends_on: []` is right.

---

## 5. Corrected oracle / fixture matrix

| id | corrected observable | corrected negative | isolating controlled break |
|---|---|---|---|
| **O1** | unchanged 6-fires / 5-empty boundary, **plus** `tuple(inspect.signature(adapters.compute_review_depth_directive).parameters) == ("scope_touch", "gate_asserts")` | pre-fix fails on the exact-tuple check; `>=` fails the 5-path half | B1 (restore `_TIER_BAND` + `tier` param), B2 (`>` → `>=`) — both unchanged, both valid |
| **O2** | unchanged (snapshot equality vs the literal `_PRE_D1_SIMPLE_REVIEW_PROMPT` at `tests/test_adapters.py:1890`) | **replace** the early-return example — it is a no-op — with "flip the ternary's `else _LOW_BAND` to `else _HIGH_BAND`" | **B7′:** `else _LOW_BAND` → `else _HIGH_BAND` (old B7 is invalid) |
| **O3** | **widen** to: no `ast.Constant` string anywhere in `adapters.py`'s AST matches `(implement\|review\|carve)-\d+`; `hasattr(adapters,"_TIER_BAND")` is False. Narrow the claim to "no tier-shaped string literal survives in `adapters.py`" | add the measured evasion set as an explicit known-limits note (f-string comprehension, deployed-tier-name dict) | B3 unchanged, **plus** B3′: `_BANDS = dict(zip(("implement-1",...),(1,2,3)))` — must now fail |
| **O3b** *(new, folds in Work item 3)* | covered by the widened O3: the docstring's `implement-1`/`implement-2` prose must be gone | leaving the line-209 parenthetical unedited fails the widened scan | B3″: revert only the line-209 parenthetical |
| **O4** | add `src.count(PINNED) == 1` and `src.index(PINNED) < src.index("def compute_review_depth_directive")` | add: pasting the block anywhere else in the file fails | B4, B5 unchanged, **plus** B4′: move the block to end-of-file |
| **O5** | assert **both** directions through the real caller: `_FM.scope.touch` of 6 paths → captured `review_depth` contains `high-complexity`; 1 path → it does not | **drop** the "no oracle at all / would ship green" claim (measured false); the stale kwarg is already caught by `TestWaveLeaseUnion` | **B6′:** caller passes a hardcoded 6-element list instead of `first_fm.scope.touch` (old B6 is not isolating) |
| **O6** | unchanged, **plus** a REPORT evidence line: `nyxloom lint` clean over the trove (BLG2/BLG3) after Work item 8 | unchanged | n/a (gate-level) |

---

## 6. Non-blocking findings

- **N1 — Work-item cross-references are off by one in five places.** `scope.touch`
  line 20 says the controlled-break table is "Work item 6" (it is 7); line 23 and
  the `_TIER_BAND` table rows for `NL-7-*.md` and `INDEX.md` say "Work item 7"
  (it is 8); the `compute_review_depth_directive` table says
  `docs/plan-next-batches.md` is "Work item 8" (it is 9) and "handled by Work
  item 7" (it is 8). An implementer following "Work item 7" for the NL-7 edit
  lands on the controlled-break table.
- **N2 — the inventory arithmetic is right, its reasoning is false.** The claim
  *"This package only ever REMOVES lines from the two src files"* (`scope.touch`
  line 25) is wrong: the pinned Work-item-2 block is 23 lines replacing 13
  (`adapters.py:176-188`), so `adapters.py` **grows** by ~10 and nets ~1,205 vs
  1,197 today. Recomputed against
  `tests/test_core_characterization.py:735-736,806-815`
  (`SIZE_TOLERANCE_FRACTION = 0.10`, `SIZE_TOLERANCE_FLOOR = 40`,
  `allowed = max(40, int(actual * 0.10))`):
  `adapters.py` 1,161 vs ~1,205 → allowed 120, drift ~44 ✅;
  `effects_review.py` 597 vs 638 → allowed 63, drift 41 ✅;
  `tests/test_adapters.py` 2,619 vs ~2,775 (three new tests) → allowed ~277,
  drift ~156 ✅. `tests/test_effects_review.py` is recorded `new` and is skipped
  by the tolerance loop entirely (line 809-810). The conclusion holds
  comfortably; fix the sentence.
- **N3 — `nyxloom.toml` does not exist.** The real path is
  `nyxloom-trove/nyxloom.toml` (gate `asserts` at line 91). Named wrongly in
  "Context to read first" item 6 and twice in `scope.touch` annotations. The
  quoted `asserts` value is correct.
- **N4 — `escalate_if` line 151 misstates its own measurement.**
  `git grep '_TIER_BAND' -- src/ tests/` returns hits in `src/nyxloom/adapters.py`
  **only**; `tests/` has zero. Harmless (the allowlist is permissive, and
  `tests/test_adapters.py` will legitimately acquire the string via O4's pinned
  literal), but "measured at input_revision as exactly those two files" is false.
- **N5 — the sweep is not the complete grep it claims.** "Every hit is
  dispositioned; there are no untabulated greps behind this table" — the
  `compute_review_depth_directive` table omits `tests/test_adapters.py:2082,
  2106, 2133, 2158` (test names and docstrings). Two of those carry the tier
  prose BLOCKER 7 is about.
- **N6 — Phase-3: option-2 rejection reason 2 is contradicted by the estate's own
  record.** The carve says (handoff lines 243-246) *"The only cheap version of
  option 2 is the anti-pattern NL-7 itself forbids. Parsing `implement-(\d)` out
  of the tier name is a hardcoded tier-name string match wearing a regex
  costume."* But `src/nyxloom/config.py:842` declares
  `_IMPLEMENT_TIER_RE = re.compile(r"^implement-(\d+)$")` and
  `next_implement_tier` (843-880) does exactly that — derives a numeric band
  (`origin_band = int(match.group(1))`) from the tier NAME by regex, over the
  LIVE `routes.tiers` — and `nyxloom-P100-CODE-REVIEW.md:44-49` explicitly
  blessed it as *"genuinely different from `_TIER_BAND`'s hardcoded dict, not a
  second instance of NL-2's bug."* The carve cites `config.py:854` four times as
  corroborating evidence but never engages with it as an option-2 template.
  **The decision still stands** on reasons 1, 3 and 4 — decisively reason 3,
  which I verified: the deployed matrix has no `implement-*` tier at all, so a
  live-keys band lookup returns nothing for every real dispatch today. But
  reason 2 as written will be transcribed into NL-7 by Work item 8 and
  contradicts the codebase. Recommend amending it to name `next_implement_tier`
  and distinguish it (a presence/ordinal query over live keys that returns
  `None` when the tier is absent, vs. a hardcoded band table that fabricates a
  band for a tier that does not exist).
- **N7 — O1 overlaps the post-edit `test_compute_review_depth_directive_scope_size_fallback`**
  (which becomes 8-paths-fires / 1-path-empty under a rigorous gate). Harmless
  redundancy; the 5/6 boundary is genuinely new and worth having.
- **N8 — post-fix, the `if band < _HIGH_BAND and not shallow: return ""` early
  return is provably dead** (see BLOCKER 2's enumeration). The carve requires it
  to stay and O2 depends on it existing. Say so explicitly in Work item 3 so a
  downstream code reviewer does not flag it as unreachable-in-effect.

## 7. Matters of taste (no action needed)

- `input_revision: d50684d9` is the carve commit itself rather than the last
  source-bearing revision; HEAD `a80264fb` differs only in the handoff file, so
  every measured line number is stable. Harmless.
- The handoff's own `tier: luna-high` exists only in the **deployed** routes
  file, not the tracked one — consistent with P100's L14 (lint is clean), and a
  quiet illustration of the drift the carve documents.
- "Contract class 2d" is defensible once BLOCKERs 1, 5 and 7 are closed; as
  frozen, the packet is not yet the locked edit map that class asserts.

---

## Verdict

**NOT READY.** Disposition blockers 1-7 above, then re-freeze. My confidence in
the package's *thesis* is high — I confirmed the one-way suppressor by
execution, confirmed the tracked/deployed drift, confirmed NL-7's second claim
is false, and confirmed the option-2 rejection is materially correct. What is
not ready is the oracle set: **O2 has no valid mutation proof (its break is a
no-op), O5's justification is measurably false and its break is not isolating,
O3 is evaded by seven of nine plausible reintroductions, Work item 3's pinned
docstring has no oracle at all, and Work item 9 routes the implementer into a
BLOCKED exit.** Those are exactly the defects that let a package ship green and
wrong, which is what this carve was written to prevent.

Nothing here requires re-carving. Six of the seven are text edits to the
frontmatter and Work-item list; BLOCKER 6 is a five-line change to one oracle's
check that also closes BLOCKER 6(b).

---
---

# Round 2 — FIX VERIFICATION

**Re-frozen at:** `9f514289` (`input_revision: 49b98bf7`). Verified
`git diff --stat 49b98bf7 9f514289 -- nyxloom/src nyxloom/tests` is **empty** —
no source or test file moved under the repair, so every line number I validated
in round 1 still holds.
**Method:** I re-ran every probe myself from scratch. I took none of the
carver's measurements on trust, and I re-derived two of them independently.

**VERDICT: REJECT — one new blocker (R2-1), introduced by the BLOCKER 4 repair.**

All seven round-1 blockers are **genuinely repaired**, each verified by my own
execution, not by reading the diff. R2-1 is a one-line correction to an
evidence-recording instruction; it touches no oracle, no fixture and no edit
map. Once it is fixed I would ACCEPT without needing to re-verify anything
else in this file.

## R2-1 (NEW, BLOCKER) — Work item 8(c) / O6's second half pin a command that cannot produce the evidence they demand

O6's observable (line 179-182) requires *"a verbatim evidence line showing
`python3 exec-nyxloom.py lint` clean over the trove after Work item 8 —
specifically no BLG2/BLG3 finding"*, and Work item 8(c) (lines 622-625) orders
the implementer to *"Record in the REPORT the **verbatim output** of
`python3 exec-nyxloom.py lint` over the trove showing no BLG2/BLG3 finding."*

I ran it. Measured at `input_revision`:

```
$ python3 exec-nyxloom.py lint
EXIT=1
793 output lines
  398  - L13 warning        164  - L7 error
   92  - L14 error           56  - L1 error
   30  - L10 warning         23  - L11 error
   10  - L4 warning           5  - L12 error
    6  - L7 warning           2  - L10 error
→ 342 errors, 444 warnings, 0 BLG lines
```

Three problems, in increasing severity:

1. **It is not clean and never will be.** The nyxloom trove's
   `handoff_globs = ["nyxloom-trove/handoffs/*.md"]` sweeps in handoffs authored
   for other tools; `ciu-P04…P06` and a dozen `topos-P17x` files carry
   `L14 error tier 'implement-2' / 'luna-low' is not a key in the live
   routes.toml`, and the `CORE-REDESIGN-SESSION-HANDOFF-*` files carry
   `L1 error parse/schema error: missing leading '---'`. All 342 errors are
   pre-existing and none is this package's to fix.
2. **"Verbatim output" is 793 lines.** Pasting it into the REPORT buries the
   one fact it is supposed to establish.
3. **This is a BLOCKED trap of exactly the class BLOCKER 1 was repaired to
   remove — and it is worse, because the noise is thematically adjacent.** The
   implementer runs the pinned command, sees `EXIT=1` and 92 `L14 error tier
   'implement-2'` lines, and has every reason to believe this package caused
   them. O6's second half is a *named contract* ("lint clean over the trove"),
   and the BLOCKED rule (lines 744-749) says to STOP when a named contract
   cannot be met as specified. It literally cannot.

**What is NOT wrong here, so the fix stays small:** BLG findings really do
surface through this command — `lint.py:255-256` folds
`lint_backlog_entries(cfg)` (BLG2 + BLG3, `lint.py:461-470`) into the per-file
results. Zero BLG lines today means the INDEX is genuinely fresh, not that the
rule is unwired. The mechanism is sound; only the pinned instruction is wrong.

**Prescription — replace Work item 8(c) with a rule-scoped read, and state the
baseline so the noise cannot be mistaken for damage:**

> Run `python3 exec-nyxloom.py lint` and, **in a separate step** (LESSONS L4 —
> do not read a verdict off a pipe tail), read only the BLG findings from its
> output. Record in the REPORT the count and any BLG lines; the expected result
> is **zero BLG2/BLG3 findings**. NOTE: the bare command exits 1 and emits ~790
> findings (~342 errors) over the whole trove — pre-existing L1/L7/L10/L11/L12/
> L13/L14 findings on `ciu-*`, `topos-*` and `CORE-REDESIGN-*` handoffs that
> this package neither causes nor fixes. That is the baseline, not a failure of
> this package; do NOT report BLOCKED on it and do NOT paste the full output.

Adjust O6's observable to match ("no BLG2/BLG3 finding", dropping the word
"clean", which is false of this command's overall verdict).

## Per-blocker verification — all 7 CLOSED

| # | round-1 blocker | how I re-verified | result |
|---|---|---|---|
| 1 | WI9 → BLOCKED exit | `docs/plan-next-batches.md` is now `scope.touch` line 25. WI9 (lines 627-640) has no judgment branch: it pins one append line and forbids rewriting the record. Anchor uniqueness: `grep -c 'SELECTION by band'` → **1**, at line 309 | **CLOSED.** Anchor is unique and unambiguous |
| 2 | B7 no-op → O2 unproven | Executed B7′ (`else _LOW_BAND` → `else _HIGH_BAND`): the O2 case (1 path + rigorous) returns `'HIGH…'` where the fix returns `''`, so `assert directive == ""` (test_adapters.py:2115) and the snapshot equality (2127-2128) both fail | **CLOSED.** B7′ is isolating. The rejected-breaks note (lines 551-559) states the no-op correctly |
| 3 | O5 false claim / B6 non-isolating | Emulated B6′ (caller discards `first_fm.scope.touch`, passes a hardcoded 6-list) against a fixed caller: **`tests/test_effects_review.py` is entirely GREEN.** My first attempt showed a failure — that was my own harness passing `tier=` into a 2-arg fake (`TypeError … got an unexpected keyword argument 'tier'` at `effects_review.py:434`), not B6′. Corrected harness confirms isolation | **CLOSED.** B6′ isolates O5. O5's negative now correctly names `TestWaveLeaseUnion` as the pre-existing catcher |
| 4 | generated INDEX.md | CLI re-verified: `backlog set-status <id> {open,carved,fixed,withdrawn,obsolete}` with `--reason` *"required for fixed\|withdrawn\|obsolete"*; `backlog index` regenerates. Both pinned at lines 617-620 with the `--reason` string supplied. Prose→set-status→index ordering rationale is correct (`set-status` rewrites frontmatter) | **CLOSED for the INDEX mechanics.** The lint *evidence* half is R2-1 |
| 5 | unpinned NL-7 prose + imprecise summary | Heading `## Observed mechanism and reproduction` matches `NL-7-*.md:14` exactly. Pinned text (lines 571-611) re-checked against my own measurements: "22 archived handoffs declare `tier: implement-2`" ✓; pre-B16 deployed names ✓; gate asserts ✓. Summary-table row 1 (line 222) now says NL-7's live-routes premise is **TRUE on its own terms** and the error is the inference, with an explicit anti-paraphrase instruction at lines 229-231 | **CLOSED** |
| 6 | O3 evaded 7/9 + WI3 unoracled | Built the post-fix `adapters.py` in scratchpad and ran the widened scan: **correct fix → 0 hits (GREEN); line-209 parenthetical left unedited → RED** at the docstring node. So B3″ works and WI3's docstring edit is now genuinely oracled. O4's anchors also verified on the simulated fix: `count(PINNED)==1`, `index(PINNED) < index("def compute_review_depth_directive")`, old clause count 0, `per _TIER_BAND` absent | **CLOSED** |
| 7 | WI4 table incomplete | Second table (lines 493-499) re-checked against the real file: 2133-2134 quoted text matches byte-for-byte; 2158/2159-2163 quoted text matches; the 2158 rename is decided. `git grep scope_size_fallback` → only the definition and the handoff, so **the rename breaks no reference** (no `-k` selection in `run-gate.toml`) | **CLOSED** |

Also confirmed applied and correct: O1/O2 exact signature-tuple equality
`("scope_touch", "gate_asserts")` (lines 43-45, 47-52); O4's anchors (lines
126-128) with the paste-at-EOF evasion named in the negative (141-145); N1
renumbering; N2 inventory premise corrected to NET GROWS (line 26); N3
`nyxloom-trove/nyxloom.toml` fixed in all three places (20, 271, 337); N4
escalate_if corrected to one file (194); N5 the four non-call hits tabulated
(373); N6 option-2 reason 2 rewritten around `next_implement_tier` and demoted
to "WEAKEST of the four; do not lean on it" (294-307).

## Two corrections the carver made to MY round-1 findings — both accepted

1. **O3's constant count.** I wrote "exactly two such constants". The carver
   re-measured and stated **four matching `ast.Constant` nodes at two source
   locations** (three `_TIER_BAND` dict keys at line 181 + one docstring node
   reported at line 196 carrying the prose at 202 and 209). I re-ran my own
   scan: it returns exactly four rows — `181, 181, 181, 196`. **The carver is
   right and my round-1 phrasing was loose**; the handoff's wording (lines
   90-95) is the accurate one, and telling the implementer to re-run the scan
   before editing is the right instruction.
2. **B7's no-op enumeration.** The carver enumerated a wider input space
   (0/1/4/5/6/8/30 paths × rigorous/shallow/empty/None) than my original
   (0/1/8 × three assert shapes) and got the same result: 0 differing cases.
   Independently reproduced.

## The new L10 warning — I agree with accepting it, with one caveat

Reproduced: lint now emits **five** findings, `EXIT=0` —
`L10 warning handoff size 13796 tokens` plus the four known L13 named-negative
warnings. Thresholds confirmed at `src/nyxloom/lint.py:1089-1107`
(warn 10000 / error 18000), so this is a warning, not an error, and the handoff
is at ~77% of the error ceiling.

**Do not split the package and do not move the pinned blocks to a separate
file.** The size is a direct consequence of round 1's central remedy — pinning
verbatim text because paraphrase-tolerant instructions were the defect — and
moving the pinned blocks out would reintroduce exactly the indirection that
makes O4's verbatim containment and Work item 8's anti-paraphrase instruction
work. Splitting a package this small (one dict, one parameter, one caller line)
would also multiply the gate runs for no reduction in risk.

*Caveat, non-blocking:* the handoff states the size as `~13.8k tokens (exact
count drifts with any edit; treat as ~14k)`, which is right. But at 13,796 of an
18,000 error ceiling there is ~4.2k of headroom, and the implementer is not
asked to touch this file, so the margin is not at risk during implementation.
Worth a one-line note in the REPORT rather than any change here.

## Non-blocking findings, round 2

- **R2-N1 — BATCH C block bounds are off by two.** `scope.touch` line 25 and
  Work item 9 both say "lines 293-307"; the block actually runs **293-309**
  (heading at 293, final line `SELECTION by band …` at 309). Harmless — Work
  item 9 anchors on unique text, not on a line number — but the annotation
  should say 293-309.
- **R2-N2 — the two pinned test-docstring replacements span line breaks in the
  source.** The strings Work item 4's second table quotes
  (`"""Oracle 4: an absent/unparseable tier falls back to a scope.touch-size` +
  `proxy.` at 2159-2160; `isolating that the FALLBACK band` + `alone (not gate
  rigor) drives the difference` at 2162-2163) are rendered as flowing sentences
  in the handoff, so the implementer must re-wrap the replacements to the file's
  ~75-column style. No oracle reads test docstrings (O3's scan root is
  `adapters.py`), so nothing breaks either way; it is a free choice and should
  be stated as one.
- **R2-N3** — round-1 §7 taste items (input_revision pointing at a carve commit;
  `tier: luna-high` living only in the deployed file; O1/fallback-test overlap)
  remain correctly dispositioned as non-blocking. No action.

## Round 2 verdict

**REJECT**, on **R2-1 alone**. The repair round is otherwise excellent: every
one of the seven blockers is closed by a change I verified by execution, two of
my own findings were independently re-measured and one of them corrected, and
the rejected-breaks section now preserves the traps so they cannot be
re-derived. R2-1 is a single instruction that pins an unsatisfiable command;
rewrite Work item 8(c) and O6's second half per the prescription above and the
package is ready to dispatch. **I do not need to see the rest of the file
again — fix R2-1 and treat this as ACCEPT.**

---
---

# Round 3 — FINAL FIX VERIFICATION

**Re-frozen at:** `ac61f7d9` (`input_revision: a32c4bb6`).

**VERDICT: ACCEPT.** R2-1 is closed. The carve is ready to dispatch.

## Scope of the repair, verified rather than assumed

- `git diff --stat 9f514289 ac61f7d9` → **one file, +38/−14**, the handoff alone.
- `git diff --stat a32c4bb6 ac61f7d9` → the handoff's own `input_revision` line
  only. A clean freeze.
- `git diff --stat 49b98bf7 ac61f7d9 -- src tests` → **empty**. No source or test
  file has moved since the round-2 base, so **every line number I validated in
  rounds 1 and 2 still holds** and none of my earlier verification needs redoing.

I read the full diff rather than trusting the summary; it touches exactly the
four areas claimed and nothing else.

## R2-1 — CLOSED

| requirement | state at `ac61f7d9` |
|---|---|
| Drop the false "clean" contract | O6's observable now reads *"record the **BLG findings only** … the expected result is ZERO BLG2/BLG3 findings"*. `grep -n 'lint.clean\|clean over the trove'` over the whole handoff → **no hits**. The retracted contract is fully gone, not merely softened |
| Read in a separate step (L4) | Present in both O6 and Work item 8(c) |
| Don't paste the full output | Stated in both places |
| State the baseline so the noise cannot be mistaken for damage | Work item 8(c) carries it verbatim: exit **1**, ~790 findings (~342 errors / ~444 warnings), and the per-rule histogram **164 L7 / 92 L14 / 56 L1 / 23 L11 / 5 L12 / 2 L10, zero BLG** — which matches my own measurement exactly, rule for rule. Named causes (`handoff_globs` sweeping `ciu-P04..P06`, a dozen `topos-P17x`, `CORE-REDESIGN-SESSION-HANDOFF-*`) are correct |
| Defuse the BLOCKED trap | *"A nonzero exit code from this command is the baseline, not a failure signal"*, plus an explicit note that `implement-2` in those L14 errors is thematically adjacent **and not the implementer's doing** — the precise misreading I flagged |

**Two stale cross-references the carver found that my prescription implied but
did not name:** `scope.touch` lines 20 and 24 both still said *"Work item 8's
lint-clean evidence line"*, which would have re-asserted the retracted contract
from the frontmatter — where it would outrank the body. Both now read *"Work
item 8(c)'s BLG-findings read"*. That is a real catch and the right instinct:
a retraction in the body is worthless while the frontmatter still asserts the
old contract.

## R2-N1, R2-N2 — CLOSED

- **R2-N1:** `293-307` → `293-309` in both `scope.touch` line 25 and Work item 9.
- **R2-N2:** Work item 4 now states that re-wrapping the two pinned **test**
  docstrings is a FREE CHOICE because no oracle reads them, and contrasts that
  explicitly with `adapters.py`'s byte-exact block — *"this is the one place
  where 'pinned' means the sentence, not the bytes."* That is a better fix than
  I asked for: it removes the ambiguity in the one direction that mattered
  (an implementer over-reading "pinned" as byte-exact where O4 does not check).

## Independent re-measurement

```
$ python3 exec-nyxloom.py lint nyxloom-trove/handoffs/nyxloom-P101-retire-tier-band.md
- L10 warning handoff size 14204 tokens
- L13 warning oracle 'O3' references path 'src/nyxloom/config.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'src/nyxloom/effects_carve.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'tests/test_config.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'tests/test_daemon.py' not covered by scope.touch
EXIT=0
```

Five warnings, no errors — matching the handoff's own "Expected lint output"
block, which now documents ~14.2k. L10 headroom: **14,204 of an 18,000 error
ceiling = 3,796 spare**, and the implementer never edits this file, so the
margin is not at risk during implementation. Fence nesting re-checked
independently: 20 three-backtick and 2 four-backtick fences, both balanced.

## Final verdict

**ACCEPT.** Ready to dispatch at `ac61f7d9`.

Across three rounds this carve absorbed 7 blockers, 1 follow-on blocker, and 11
non-blocking findings without a single unresolved disposition. Two of my own
round-1 claims were independently re-measured and corrected by the carver
(O3's constant count, which I had phrased loosely, and B7's enumeration, which
was reproduced on a wider input space) — I verified both corrections and they
were right. The oracle set now has isolating mutation evidence for every oracle
it claims to protect, and the two instructions that could have routed an
implementer into a spurious BLOCKED exit are both closed with the baseline
stated in terms.
