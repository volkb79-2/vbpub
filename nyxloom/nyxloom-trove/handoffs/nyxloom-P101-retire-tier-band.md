---
schema_version: 1
id: nyxloom-P101-retire-tier-band
project: nyxloom
title: "Retire adapters.py's _TIER_BAND one-way suppressor; scope size becomes the sole band signal"
tier: luna-high
input_revision: "d50684d9"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-7-adapters-py-s-tier-band-hardcodes-implement-n-keys-that-have-ne.md
scope:
  touch:
    - "src/nyxloom/adapters.py"          # the fix. Three edits, all inside lines ~176-224: (1) delete the `_TIER_BAND` dict (line 181) and replace the comment block above it (lines 176-187) with Work item 2's pinned verbatim text; (2) drop the `tier` parameter from `compute_review_depth_directive`'s signature (line 194) and rewrite its docstring's band paragraph per Work item 3's pinned text; (3) collapse the two-step band computation (lines 219-223) to the unconditional scope-size ternary. `_LOW_BAND`, `_HIGH_BAND`, `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD` and `_RIGOROUS_ASSERTS` all STAY (all four remain used -- see Work item 3's note on why the numeric band is not collapsed to a bool)
    - "src/nyxloom/effects_review.py"    # the sole production caller, `ReviewEffector.launch_review` line ~434: drop the `tier=first_fm.tier if first_fm is not None else None,` kwarg. `first_fm` itself STAYS (still read for `scope_touch=` on the next line and by `incapable_escalation_note` below), so this is a one-line deletion, not a restructure
    - "tests/test_adapters.py"           # the four existing band tests at lines 2082-2185 all pass `tier=` positionally-or-by-keyword and MUST be updated (they will TypeError otherwise); plus five call sites at lines 2188/2234/2266/2291/2441 that pass `tier="implement-3"` purely to manufacture a non-empty directive for an unrelated oracle -- those must switch to a large `scope_touch` instead. New tests for O1/O3/O4 land here too. See Work item 4 for the exact per-line disposition table
    - "tests/test_effects_review.py"     # NEW test for O5 (the caller). There is currently ZERO coverage of `review_depth` in this file (measured: `grep -c review_depth tests/test_effects_review.py` -> 0), which is why the original defect survived -- mirror the existing full-path harness `test_a_wave_holds_the_union_of_its_members_leases_deduplicated` (lines 256-313), which already drives `launch_review` all the way through line 434
    - "nyxloom-trove/reports/nyxloom-P101-LOG.md"      # NEW: per-commit LOG, estate standard contract
    - "nyxloom-trove/reports/nyxloom-P101-REPORT.md"   # NEW: per-oracle evidence + the MUTATION-CHECKED controlled-break table (Work item 6). The gate does NOT assert `mutation` (measured: nyxloom.toml [gates.tester-unified] asserts = tests-pass, changed-line-coverage, canary-verified), so the controlled breaks are run BY HAND and their output recorded here -- this file is where that proof lives
    - "nyxloom-trove/reports/nyxloom-P101-BRIEF.md"    # authorised checkpoint artefact (E-008 clause in escalate_if); expected unused, this is a small package
    - "nyxloom-trove/reports/nyxloom-P101-COMPACT.md"  # authorised checkpoint artefact (the self-authored retention prompt); expected unused
    - "nyxloom-trove/backlog/NL-7-adapters-py-s-tier-band-hardcodes-implement-n-keys-that-have-ne.md"  # status open -> fixed, AND correct the entry's own factually wrong premise (Work item 7): NL-7 says the keys "have never been real routes.toml values" and the lookup "has returned None for every real handoff", both of which this carve measured FALSE. Leaving a fixed entry that still misstates the mechanism would re-teach the wrong lesson to the next reader
    - "nyxloom-trove/backlog/INDEX.md"   # the generated status column for NL-7 must match the entry's new status
    - "nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md"  # VERIFY-ONLY, no edit expected. This doc is mechanically gated (tests/test_core_characterization.py::test_inventory_sizes_are_within_the_declared_tolerance) and both P98 and P100 were bitten by it. Measured at input_revision: adapters.py row records 1,161 vs actual 1,197 (tolerance max(40, int(1197*0.10)) = 119, drift 36 -- inside); effects_review.py records 597 vs actual 639 (tolerance 63, drift 42 -- inside); tests/test_adapters.py records 2,619 vs actual 2,715 (tolerance 271, drift 96 -- inside). This package only ever REMOVES lines from the two src files, which moves each row's drift by single digits and cannot cross its tolerance. Listed so that if the gate nonetheless reports drift the implementer re-measures and updates rather than reporting BLOCKED on an unlisted path
    - "tests/test_core_characterization.py"  # VERIFY-ONLY, no edit expected: it is the enforcer of the row above. Listed because O6 requires confirming it collects and passes, not because it changes
  forbid:
    - "src/nyxloom/lint.py"              # nyxloom-P100's L14 shipped 2026-09-03 (merged 480ef39f) after four adversarial repair rounds and an independent code-review ACCEPT. This package does not touch tier VALIDATION at all -- it removes a tier CONSUMER. Do not edit
    - "reference/AUTHORING.md"           # likewise nyxloom-P100's shipped prose (the contract-class/tier table at lines 80-98). Its `implement-1`..`implement-5` column is explicitly labelled PLANNED tiers, which is correct and is NOT what this package is fixing. Do not edit
    - "src/nyxloom/config.py"            # `Routes`/`Routes.load()`/`for_tier` are untouched infrastructure. This package removes a consumer that never called them; it does not change routing
    - "src/nyxloom/effects_carve.py"     # already carries a correct `implement-3 does not exist today` note (line 536); it is corroborating evidence for this carve, not a target
    - "routes.host.toml"                 # no routing change. The deployed/tracked tier drift this carve documents is a REAL open hazard (routes.host.toml's own "SYNC HAZARD" block) but it is a product decision with four other consumers, not this package's to resolve
oracles:
  - id: O1
    observable: >-
      In `tests/test_adapters.py`, a new test proves the suppression is gone AND pins the
      threshold boundary. With a RIGOROUS `gate_asserts` (`["tests-pass",
      "changed-line-coverage", "mutation"]`, so gate rigor contributes nothing and the band is
      isolated as the only variable): `compute_review_depth_directive(scope_touch=[6 distinct
      paths], gate_asserts=RIGOROUS)` returns a non-empty directive containing the substring
      `high-complexity`; the same call with exactly 5 paths returns `""`. Additionally
      `inspect.signature(adapters.compute_review_depth_directive).parameters` does NOT contain
      the key `tier` -- a structural check on the real signature object, so no caller can pass a
      tier value that suppresses the band by any spelling.
    negative: >-
      The pre-fix code fails this: with `_TIER_BAND` restored and a `tier` parameter present, the
      6-path call is still reachable (tier defaults to nothing) but the `tier` key IS in the
      signature, so the `inspect.signature` half fails. The boundary half independently catches
      the off-by-one: an implementation using `>=` instead of `>` against
      `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD` returns a non-empty directive for the 5-path case and
      fails. Asserting only "6 paths -> non-empty" without the 5-path case and without the
      signature check would be a hollow pass -- the pre-fix code satisfies "6 paths -> non-empty"
      already when tier is None, which is exactly how this defect hid for five weeks.
    gate: tester-unified
  - id: O2
    observable: >-
      The load-bearing neutral-case safety property is PRESERVED byte-for-byte. The existing test
      `test_review_depth_absent_prompt_is_byte_identical_to_pre_batchc`
      (tests/test_adapters.py:2103) still passes after being updated to drop its `tier=` argument:
      a small `scope_touch` plus a rigorous gate returns `""`, and `build_dispatch`'s
      REVIEW_INDEPENDENT prompt for that `""` is still equal to the module-level
      `_PRE_D1_SIMPLE_REVIEW_PROMPT` snapshot, with `"Review depth:"` absent from both the
      default-omitted and explicit-empty forms.
    negative: >-
      An implementation that makes the directive fire unconditionally (e.g. by deleting the
      `if band < _HIGH_BAND and not shallow: return ""` early return along with the tier lookup,
      or by flipping the default band to `_HIGH_BAND`) breaks the snapshot equality and fails.
      This oracle exists specifically because the fix REMOVES a branch, and the cheapest wrong way
      to remove it is to remove the neutral case with it. The comparison must stay against the
      literal `_PRE_D1_SIMPLE_REVIEW_PROMPT` snapshot constant, not against a freshly recomputed
      `build_dispatch(...)` call -- a self-comparison would pass for any prompt whatsoever.
    gate: tester-unified
  - id: O3
    observable: >-
      A new AST-based test in `tests/test_adapters.py` proves no hardcoded tier-name-to-band table
      can be reintroduced. It parses `src/nyxloom/adapters.py` with `ast.parse` (scan root bounded
      to that ONE file) and asserts there is no `ast.Dict` node anywhere in the module whose keys
      include a string literal matching `^(implement|review|carve)-\d+$`. Separately,
      `hasattr(adapters, "_TIER_BAND")` is False.
    negative: >-
      Re-adding `_TIER_BAND = {"implement-1": 1, ...}`, or the same table under any other NAME, or
      nested inside a function rather than at module level, all fail the AST check -- the check
      keys off the literal SHAPE (tier-shaped string keys in a dict), not off the identifier, so
      renaming the variable does not evade it. This is deliberately an AST check and NOT a
      `grep` for the strings `implement-1`/`implement-2`/`implement-3`: Work item 2's pinned
      replacement comment legitimately NAMES those three values while explaining what was removed
      and why, so a textual ban on the names would either fail on the correct fix or force the
      comment to be vague about the exact thing it must be concrete about. Bounded to
      `adapters.py` alone by design: `src/nyxloom/config.py:854`, `src/nyxloom/effects_carve.py:536`,
      `tests/test_config.py:683` and `tests/test_daemon.py:1202` all reference `implement-3`
      correctly and legitimately (each says it does NOT exist) and are named here as explicit
      permitted negatives, not oversights.
    gate: tester-unified
  - id: O4
    observable: >-
      A new test in `tests/test_adapters.py` reads `src/nyxloom/adapters.py` as text and asserts
      that Work item 2's replacement comment block is present VERBATIM as one exact fixed
      substring (the full block, newlines and leading `# ` included -- a plain `in` containment
      test on the file text, no regex). Completeness is checked INDEPENDENTLY of presence: the
      same test asserts the count of the old false clause
      `"mechanical/cheap vs. hard" banding routes.host.toml already uses for` is 0 in that file,
      and that the substring `per _TIER_BAND` is likewise absent.
    negative: >-
      Any deviation from the pinned block -- a dropped sentence, a reordered clause, a
      same-meaning paraphrase, a rewrapped line -- fails the verbatim containment. That is the
      point of pinning required text rather than trying to regex-ban a family of wrong wordings:
      nyxloom-P100's own carve-review rounds 2-3 established that banning-bad-wording regexes are
      both evadable by real paraphrases and prone to false positives on correct prose, because a
      keyword window cannot carry semantic polarity. The two absence assertions are NOT a
      substitute for the verbatim check and the verbatim check is not a substitute for them: an
      implementer could add the new block while leaving the old false sentence in place directly
      above it, which the presence half alone would happily pass.
    gate: tester-unified
  - id: O5
    observable: >-
      A new test in `tests/test_effects_review.py`, mirroring the existing full-path harness
      `test_a_wave_holds_the_union_of_its_members_leases_deduplicated` (lines 256-313), drives the
      REAL `ReviewEffector.launch_review` through line 434 with a frontmatter stub whose
      `scope.touch` holds 6 paths, monkeypatching `effects_review.adapters.build_dispatch` with a
      kwarg-capturing fake, and asserts the captured `review_depth` kwarg contains
      `high-complexity`.
    negative: >-
      If the caller still passes the removed `tier=` kwarg, this test fails with `TypeError:
      compute_review_depth_directive() got an unexpected keyword argument 'tier'` -- which is the
      whole reason this oracle exists at file level rather than being folded into O1. Measured at
      input_revision: `tests/test_effects_review.py` contains ZERO references to `review_depth`
      (`grep -c` -> 0), so without this new test the caller edit has no oracle at all and a stale
      kwarg would ship green. A test that calls `compute_review_depth_directive` directly instead
      of going through `launch_review` does NOT satisfy this oracle -- it would not touch the
      caller.
    gate: tester-unified
  - id: O6
    observable: >-
      The whole `tester-unified` gate is GREEN, run as the single command in "Gate argv
      (verbatim)" below, with its verdict read in a SEPARATE step from the run (never a piped
      tail). This specifically includes `tests/test_core_characterization.py` collecting and
      passing -- notably `test_inventory_sizes_are_within_the_declared_tolerance` and
      `test_inventory_paths_all_exist` -- which is what proves the ownership-inventory
      reverse-dependency really was benign rather than merely assumed to be.
    negative: >-
      A green `pytest tests/test_adapters.py` in the devcontainer cockpit does NOT satisfy this
      oracle (cockpit doctrine: the pins differ, and "green in the cockpit venv" is not a ship
      signal). A gate run whose verdict is read from a piped tail does not satisfy it either
      (LESSONS L4). If the inventory tolerance test fails, the arithmetic recorded in this
      package's `scope.touch` annotation was wrong and the row must be re-measured with real
      `wc -l` -- not hardcoded to a guessed number.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any non-test file outside scope.touch needs an edit to keep the gate green (a reverse-dependency this carve's tabulated sweep missed) -- report BLOCKED naming the file and the symbol, do not widen scope unilaterally"
  - "`git grep -n '_TIER_BAND' -- src/ tests/` at dispatch time returns a hit in any file other than src/nyxloom/adapters.py and tests/test_adapters.py -- measured at input_revision as exactly those two files plus docs/backlog/report prose; a third code consumer appearing between carve and dispatch means the reverse-dependency table below is stale and the caller analysis must be redone, not patched around"
  - "`compute_review_depth_directive` has acquired a second production caller besides src/nyxloom/effects_review.py:434 between carve and dispatch (measured at input_revision: exactly one). A second caller means the signature change is no longer a one-line edit and O5's single-caller premise is false -- report BLOCKED rather than guessing at the new caller's intent"
  - "`tests/test_effects_review.py`'s harness at lines 256-313 no longer reaches `launch_review`'s dispatch branch (e.g. it was refactored to refuse earlier), making O5's mirror target unusable -- report BLOCKED rather than inventing a new end-to-end fixture from scratch, which is a bigger package than this one"
  - "the ownership-inventory tolerance test fails after the edits despite the arithmetic in scope.touch -- re-measure with real `wc -l` and update the row (that path IS in scope.touch); escalate only if a row OTHER than adapters.py/effects_review.py/tests/test_adapters.py drifts"
  - "E-008 checkpoint clause: arm at ~120k context or ~60 tool calls (whichever first), cut at the next coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster end; never on a red gate), repeat every ~40-55 calls, stop when <~40 calls remain. At the cut: write a continuation brief to nyxloom-trove/reports/nyxloom-P101-BRIEF.md plus a self-authored /compact-style retention prompt to nyxloom-trove/reports/nyxloom-P101-COMPACT.md (both authorised touches), commit, and return -- do not resume or fork past the cut yourself. Unlikely to be needed: this is a small package."
---

# nyxloom-P101 — Retire `_TIER_BAND`, make scope size the sole band signal

**Branch:** `nyxloom-p101-tier-band`
**Worktree:** `/workspaces/vbpub/.worktrees/nyxloom-p101-tier-band` (the nyxloom
package is at `<worktree>/nyxloom`)
**Contract class:** `2d` — every interface, exact replacement text, and test
disposition is fixed below; the remaining work is ordinary code construction
against a locked edit map plus writing the specified tests.

## Why this package exists — and where the backlog entry is WRONG

Read this section before `NL-7` itself. This carve measured NL-7's central
factual claim and found it **false**, which changes both the fix and its
justification.

NL-7 says the keys `implement-1`/`implement-2`/`implement-3` "have never been
real values in the live `routes.toml`" and that `_TIER_BAND.get(tier or "")`
"has returned `None` for every real handoff". Measured at `input_revision`:

| claim | measured reality |
|---|---|
| `implement-N` never a real tier | **Partly false.** `routes.host.toml` (the TRACKED matrix) has declared `[tiers.implement-1]` and `[tiers.implement-2]` since the B16 rename on 2026-07-23 — three days BEFORE D-BATCHC shipped `_TIER_BAND` on 2026-07-26. Only `implement-3` is fictional. |
| lookup returns `None` for every handoff | **False.** 20+ handoffs in `nyxloom-trove/archive/` declare `tier: implement-2`, including `nyxloom-P98` and `nyxloom-P99`, both shipped 2026-09-03. For every one of those the lookup returns `2`, not `None`. |

There is a second, separate axis NL-7 does not mention at all: `Routes.load()`
reads `paths.routes_path()` — the **deployed** `$XDG_STATE_HOME/nyxloom/routes.toml`,
not the tracked `routes.host.toml`. The deployed file on this host still carries
the PRE-B16 tier names (measured: `flash-high`, `flash-max`, `terra-med`,
`luna-high`, `sonnet5-high`, `frontier-review`, `haiku-low`, `free-high`) and
declares no `implement-*` tier at all. `routes.host.toml`'s own "SYNC HAZARD"
block documents this drift. It is a real open hazard with four other consumers
and it is explicitly **not** this package's to resolve.

### What the defect actually is

`_TIER_BAND` is not dead code. It is a **live one-way suppressor**. Probed at
`input_revision` with a 30-path `scope_touch` and a rigorous gate:

| `tier` passed in | band | directive |
|---|---|---|
| `implement-1` | 1 | **`""` — suppressed** |
| `implement-2` | 2 | **`""` — suppressed** |
| `implement-3` | 3 | fires |
| `luna-high` / `sonnet5-high` / `None` | `None` → scope proxy | fires |

`implement-3` is the only key mapping to `_HIGH_BAND`, and it exists in neither
the deployed nor the tracked routes file — so the tier path can never *raise*
the band. `implement-1` and `implement-2` — the tiers nyxloom's own handoffs
actually declare — resolve below `_HIGH_BAND` and short-circuit the scope-size
branch, so the tier path can only ever *lower* it. A 30-file `implement-2`
handoff gets no high-complexity directive; the same handoff with its tier field
absent does. That inversion is the bug.

The codebase already knows `implement-3` is fictional in four other places —
`src/nyxloom/config.py:854`, `src/nyxloom/effects_carve.py:536`,
`tests/test_config.py:683`, `tests/test_daemon.py:1202`. Only `adapters.py`
still believes otherwise.

### Honest bound on the blast radius

For **nyxloom's own project** the practical effect is narrower than the table
above suggests, and this must not be overstated in the REPORT. Measured:
`nyxloom.toml`'s `[gates.tester-unified]` declares
`asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]` — no
`mutation`. `_RIGOROUS_ASSERTS` requires both `changed-line-coverage` AND
`mutation`, so nyxloom's own gate always reads as shallow, the shallow-gate
reason always fires, and the directive is never empty in practice here. For
nyxloom the bug therefore omits the *high-complexity reason* from an otherwise
non-empty directive. For any project whose gate does declare `mutation`, the
suppression is total. Both are real defects; only the second is total.

### Why option 1, not option 2

NL-7 offers two options. This carve chooses **option 1** (retire the
tier-derived band; scope size becomes the sole signal), for reasons that hold
independently of the estate's standing "nyxloom is offline, don't build
plumbing nobody consumes" mandate:

1. **Option 2 has no data to read.** Neither the deployed `routes.toml` nor the
   tracked `routes.host.toml` carries any per-tier band, complexity, or
   capability fact. Band is encoded only in the tier *name* and in a human
   comment (`# was haiku-low + flash-high (band 1: mechanical/cheap)`). A
   routes-backed mapping would therefore require **inventing new routes.toml
   schema** and editing both the tracked and deployed matrices — a far larger,
   riskier package touching the live routing config.
2. **The only cheap version of option 2 is the anti-pattern NL-7 itself
   forbids.** Parsing `implement-(\d)` out of the tier name is a hardcoded
   tier-name string match wearing a regex costume; NL-7's own second oracle
   rules it out, and it is precisely NL-2/L14's root cause one level down.
3. **Option 2 cannot work on the deployed file today anyway.** The deployed
   matrix has no `implement-*` tier, so a live-data band lookup would return
   nothing for every real dispatch — trading a wrong signal for an absent one.
4. **Option 1 restores intended behavior rather than merely deleting code.**
   D-BATCHC's own comment says a handoff spanning many files "is no longer a
   small/cheap change". Option 1 is what makes that fire. This is a behavior
   FIX, not just an honesty fix — which is why it carries real oracles (O1, O5)
   and not only a comment check (O4).

Option 2 stays available: the pinned comment in Work item 2 names it explicitly
as the route to restoring a tier-derived band, and Work item 7 records it on the
NL-7 entry rather than closing the idea off.

## Context to read first

Read in this order; nothing else is needed.

1. `nyxloom-trove/backlog/NL-7-adapters-py-s-tier-band-hardcodes-implement-n-keys-that-have-ne.md`
   — the origin entry. **Read the "Why this package exists" section above
   first**; NL-7's mechanism paragraph is partly wrong and Work item 7 corrects it.
2. `src/nyxloom/adapters.py` lines **176-245** — the whole band block, the
   function, and the reason-assembly below it. Not just the two lines NL-7 names.
3. `src/nyxloom/effects_review.py` lines **425-440** — the sole production
   caller, inside `ReviewEffector.launch_review`.
4. `tests/test_adapters.py` lines **2075-2200** — the four existing band oracles
   (O1-O4 of D-BATCHC's own set) and their docstrings, which state the
   properties that must survive.
5. `tests/test_effects_review.py` lines **256-313** — the harness to mirror for
   O5, plus the `_routes()` helper at line 316 it depends on.
6. `nyxloom.toml` `[gates.tester-unified]` (lines ~69-92) — the gate's real
   `asserts` list, which the "honest bound" paragraph above depends on.

## Reverse-dependency sweep (tabulated, `git grep`, all tracked file types)

Run over the whole worktree with no pathspec restriction, so `.py`, `.toml`,
`.j2`, `.ts`, `.js`, `.sh`, `.yml` and `.md` are all in scope. Every hit is
dispositioned; there are no untabulated greps behind this table.

### `_TIER_BAND`

| file | line | disposition |
|---|---|---|
| `src/nyxloom/adapters.py` | 181 | **DELETE** (Work item 2) |
| `src/nyxloom/adapters.py` | 202 | **REWRITE** — docstring reference (Work item 3) |
| `src/nyxloom/adapters.py` | 219 | **DELETE** — the lookup itself (Work item 3) |
| `nyxloom-trove/backlog/NL-7-*.md` | 5, 19, 27, 29, 43, 51, 55, 63 | **EDIT** (Work item 7) — prose |
| `nyxloom-trove/backlog/INDEX.md` | 9 | **EDIT** (Work item 7) — generated status row |
| `nyxloom-trove/handoffs/nyxloom-P100-*.md` | 261 | leave — a shipped package's frozen text |
| `nyxloom-trove/reports/nyxloom-P100-CARVE-REVIEW.md` | 46, 50, 57, 136, 146, 213 | leave — historical review record |
| `nyxloom-trove/reports/nyxloom-P100-CODE-REVIEW.md` | 41, 48 | leave — historical review record |
| `nyxloom-trove/reports/nyxloom-P100-LOG.md` | 29 | leave — historical |
| `nyxloom-trove/reports/nyxloom-P100-REPORT.md` | 63 | leave — historical |

**Zero** `.toml`, `.j2`, `.ts`, `.js`, `.sh` or `.yml` hits. No re-export: the
symbol is module-private and absent from any `__init__.py` (verified by the
same sweep — no `common/`-style flat shim layer exists in this package).

### `compute_review_depth_directive`

| file | line | disposition |
|---|---|---|
| `src/nyxloom/adapters.py` | 194 | **EDIT** — signature (Work item 3) |
| `src/nyxloom/adapters.py` | 319, 638 | leave — comments naming the helper, still accurate |
| `src/nyxloom/effects_review.py` | 434 | **EDIT** — the ONE production caller (Work item 5) |
| `tests/test_adapters.py` | 2087, 2112, 2141, 2148, 2167, 2173, 2178, 2188, 2234, 2266, 2291, 2441 | **EDIT** — 12 call sites, dispositioned individually in Work item 4 |
| `docs/plan-next-batches.md` | 298 | **VERIFY** — Work item 8; it names the three-arg form |
| `nyxloom-trove/backlog/NL-7-*.md`, `nyxloom-trove/reports/nyxloom-P100-CARVE-REVIEW.md` | various | prose, handled by Work item 7 / left historical |

### `_HIGH_BAND_SCOPE_TOUCH_THRESHOLD`

| file | line | disposition |
|---|---|---|
| `src/nyxloom/adapters.py` | 188 | **KEEP** (value unchanged); comment above it rewritten |
| `src/nyxloom/adapters.py` | 204, 222 | **REWRITE** / **KEEP** — docstring ref; the comparison survives as the sole band branch |
| `tests/test_adapters.py` | 2160 | **EDIT** — docstring of the fallback test (Work item 4) |

`_LOW_BAND` (182, 221, 223) and `_HIGH_BAND` (183, 221, 229, 233) stay; all
remain referenced after the edit.

## Implementation packet (normative)

### Work

1. **Read the six context files above.** Do not start editing from NL-7's
   description alone — it is partly wrong and the corrected mechanism is in
   "Why this package exists".

2. **Replace `src/nyxloom/adapters.py` lines 176-188** (the comment block,
   `_TIER_BAND`, and the threshold comment) with EXACTLY this text. O4 pins it
   verbatim; do not rewrap, reorder, or paraphrase:

```python
# D-BATCHC (2026-07-26, plan-factory-hardening.md; Batch C -- modulate
# review depth by complexity band + declared gate rigor).
#
# nyxloom-P101 (NL-7): scope size is the SOLE band signal. `tier` used to
# feed a hardcoded `_TIER_BAND = {"implement-1": 1, "implement-2": 2,
# "implement-3": 3}` table, which was wrong in both directions. The only
# key mapping to _HIGH_BAND was `implement-3`, which is not a tier in the
# deployed routes.toml or in the tracked routes.host.toml and never has
# been (config.py and effects_carve.py already record this) -- so the tier
# path could never RAISE the band. Meanwhile `implement-1` and
# `implement-2`, the tiers nyxloom's own handoffs really do declare,
# resolved to 1 and 2, both below _HIGH_BAND, and short-circuited the
# scope-size branch below -- so the tier path could only ever LOWER it,
# suppressing the high-complexity reason for exactly the large-scope
# handoffs that named a real tier. It was a one-way suppressor, never a
# trigger. Restoring a tier-derived band requires a real per-tier
# complexity fact in routes.toml (NL-7 option 2), not another hardcoded
# tier-name table.
_LOW_BAND = 1
_HIGH_BAND = 3
# >N touched paths in scope.touch is treated as band 3 -- a handoff
# spanning that many files is no longer a small/cheap change.
_HIGH_BAND_SCOPE_TOUCH_THRESHOLD = 5
```

   `_RIGOROUS_ASSERTS` and its two comment lines (189-191) are unchanged and
   stay immediately below.

3. **Change the signature and band computation.** Line 194-195 becomes:

```python
def compute_review_depth_directive(scope_touch: list[str] | None,
                                   gate_asserts: list[str] | None) -> str:
```

   Replace the docstring's band paragraph (lines 202-207) with EXACTLY:

```
    Band comes from the size of `scope_touch` alone (see
    _HIGH_BAND_SCOPE_TOUCH_THRESHOLD and the nyxloom-P101 note above it);
    there is deliberately no tier-derived band and no `tier` parameter. Gate
    rigor comes from `gate_asserts` (config.GateDef.asserts; None/[] is the
    shallowest case -- no gate, or a gate declaring nothing, both read as
    maximally shallow, never crash).
```

   In the paragraph below it, replace the parenthetical
   `(i.e. implement-1/implement-2, or the fallback's small-scope case)` with
   `(a small scope_touch)`. Leave the rest of that paragraph alone.

   Replace lines 219-223 with the unconditional ternary:

```python
    band = (_HIGH_BAND
            if len(scope_touch or []) > _HIGH_BAND_SCOPE_TOUCH_THRESHOLD
            else _LOW_BAND)
```

   **Degree of freedom explicitly CLOSED:** do not collapse the numeric band to
   a boolean, even though `band` can now only hold `_LOW_BAND` or `_HIGH_BAND`.
   The numeric vocabulary is what D-BATCHC, `plan-factory-hardening.md` and the
   surrounding comparisons (`band < _HIGH_BAND`, `band >= _HIGH_BAND`) use, and
   NL-7 option 2 would restore multi-valued bands. Collapsing it is a
   gratuitous diff that enlarges the review surface for no behavioral gain.

4. **Update `tests/test_adapters.py`.** Every call site must drop `tier=`.
   Disposition per site:

   | line | current | required change |
   |---|---|---|
   | 2087 | `tier="implement-3", scope_touch=["a.py"]` | drop `tier=`; `scope_touch` becomes 6 paths so the high band still fires. Docstring: "a high band (implement-3)" → "a high band (a large scope_touch)" |
   | 2112 | `tier="implement-1", scope_touch=["a.py"]` | drop `tier=`; keep the 1-path scope (still low band). Docstring: "low band (implement-1)" → "low band (a small scope_touch)" |
   | 2141, 2148 | `tier="implement-1", scope_touch=["a.py"]` | drop `tier=`; keep 1 path — this test proves the shallow gate fires ALONE with a low band, which is exactly preserved |
   | 2167, 2173, 2178 | `tier=None` / `tier="not-a-real-tier"` | drop `tier=`. The 2178 case (unrecognized tier falls back the same way) no longer has a distinct meaning — **delete that third assertion block** and say so in the docstring; the concept it tested is now the only path |
   | 2188, 2234, 2266, 2291, 2441 | `tier="implement-3"` used only to manufacture a non-empty directive for an unrelated oracle | drop `tier=`; pass 6 paths in `scope_touch` instead. These tests are about role scoping / argv degradation / `review_focus` coexistence and must keep asserting exactly what they assert today |

   Then ADD three new tests: O1's suppression-and-boundary test (6-path fires,
   5-path empty, `inspect.signature` has no `tier`), O3's AST test, and O4's
   verbatim-comment test.

5. **Update the caller.** In `src/nyxloom/effects_review.py` at ~line 434,
   delete the line `tier=first_fm.tier if first_fm is not None else None,`.
   Keep `first_fm` and both remaining kwargs exactly as they are. The call
   becomes:

```python
        review_depth = adapters.compute_review_depth_directive(
            scope_touch=first_fm.scope.touch if first_fm is not None else [],
            gate_asserts=review_gate.asserts if review_gate is not None else [],
        )
```

6. **Add O5's caller test** to `tests/test_effects_review.py`, mirroring
   `test_a_wave_holds_the_union_of_its_members_leases_deduplicated`
   (lines 256-313). Copy that harness; give the `_FM` stub a `scope.touch` of 6
   paths instead of `[]`; replace the `build_dispatch` monkeypatch with a
   kwarg-capturing fake; assert the captured `review_depth` contains
   `high-complexity`.

7. **Run the MUTATION-CHECKED controlled breaks by hand** and record each in
   `nyxloom-trove/reports/nyxloom-P101-REPORT.md` with the real failure output.
   The gate does not assert `mutation`, so this is the only proof these oracles
   are not hollow. Each break is applied, the named test run, the failure
   witnessed, then the break reverted:

   | break | change | must fail |
   |---|---|---|
   | B1 | restore `_TIER_BAND` + the `tier` param + the `.get` short-circuit | O1 (signature half) |
   | B2 | `>` → `>=` in the threshold comparison | O1 (5-path boundary half) |
   | B3 | re-add the dict under a different name, e.g. `_BANDS` | O3 |
   | B4 | reword one sentence of the pinned comment | O4 (verbatim half) |
   | B5 | leave the old false "mechanical/cheap vs. hard" sentence in place above the new block | O4 (absence half) |
   | B6 | re-add `tier=...` to the `effects_review.py` call | O5 (TypeError) |
   | B7 | delete the `if band < _HIGH_BAND and not shallow: return ""` early return | O2 |

8. **Correct and close NL-7.** In
   `nyxloom-trove/backlog/NL-7-*.md`: set `status: fixed`; rewrite the
   "Observed mechanism" section so it states the measured truth (only
   `implement-3` is fictional; `implement-1`/`implement-2` are real tracked
   tiers; the lookup was a one-way suppressor, not always-`None`; the
   deployed-vs-tracked drift is a separate open hazard); record that option 1
   was chosen and why, keeping option 2 named as the route to a real
   tier-derived band. Update the status column for NL-7 in
   `nyxloom-trove/backlog/INDEX.md` to match.

9. **Verify `docs/plan-next-batches.md:298`.** It names
   `adapters.compute_review_depth_directive(tier, scope_touch, gate_asserts)`.
   If that line states the three-argument form as current fact, update it to
   the two-argument form. If it is clearly historical/planning narrative
   (a "Batch C will..." sentence), leave it and say so in the REPORT. Read the
   surrounding paragraph before deciding; do not edit blind.

10. **Run the gate** (argv below) and read the verdict in a separate step.
    Write `nyxloom-trove/reports/nyxloom-P101-LOG.md` per commit and
    `nyxloom-trove/reports/nyxloom-P101-REPORT.md` with per-oracle evidence.

### Test anti-patterns — these apply to every test this package adds

Copied from `reference/AUTHORING.md` §3b because an implementation agent has no
access to the incident history behind them:

- **No wall-clock dependence.** No `time.sleep` + assert, no
  `time.monotonic()` deadlines, no asserting on elapsed time or iteration
  counts. Wait on a real synchronization point or remove the wait. A timeout is
  legal only as a generous failsafe that can never decide pass/fail. (L20)
- **No order/worker/sibling dependence.** Do not mutate process-global state
  (`os.environ`, module attributes, logging config, singletons) without
  restoring it — under `pytest-xdist` the damage lands on whichever test shares
  the worker. Do not `monkeypatch.setattr` an object that synthesizes
  attributes via `__getattr__`; patch the owning namespace. (PL7 §5, L19)
- **No hollow tests.** No body that is `pass` or that only asserts nothing
  raised; no asserting call counts or private attributes in place of the
  behavioral contract; never weaken or delete an assertion to get past a
  failure. Every oracle here pins a negative for exactly this reason.
- **No coverage evasion.** No `no cover` pragma on a changed line — the gate
  rejects them and matches the literal token anywhere on a line, including in a
  comment that merely describes the rule. (L11, GA2b)
- **Network, clock and filesystem are inputs — control them.** No real network,
  no `datetime.now()` the assertion depends on. `tmp_path`/`tmp_state` per test.

O3 and O4 both read `src/nyxloom/adapters.py` from disk. Resolve that path from
`Path(__file__).resolve().parents[1] / "src" / "nyxloom" / "adapters.py"`, the
same way `tests/test_core_characterization.py` derives `REPO_ROOT` — never from
a hardcoded absolute path and never from the current working directory.

## Environment setup

Mode-B, this worktree only (`/workspaces/vbpub/.worktrees/nyxloom-p101-tier-band`,
branch `nyxloom-p101-tier-band`). No package image tag needed; no stack
bring-up. The gate runs `tester-unified` via `./run-gate.py`.

**Host discipline (shared machine, 8 cores, also runs a production game
server):** run `docker ps` BEFORE launching the gate and do not stack a second
gate container if one is already running from another package's work. Serial
pytest only — no `-n auto`. No builds concurrent with the suite.

## Gate argv (verbatim)

```
cd /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band/nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-p101-tier-band tester-unified
```

Read the verdict in a SEPARATE step from the run — never from a piped tail
(LESSONS L4). "Green in the devcontainer cockpit venv" is not a ship signal
(cockpit doctrine): the pins differ, and only this command's verdict counts.

## Scope / forbid

`scope.touch` and `forbid` in the frontmatter are authoritative and each entry
is annotated with why. In particular: **nyxloom-P100's shipped work
(`src/nyxloom/lint.py`'s L14, `reference/AUTHORING.md`'s contract-class table)
is forbidden.** P100 merged as `480ef39f` after four adversarial repair rounds
and an independent code-review ACCEPT. Its `implement-1`..`implement-5` column
is explicitly labelled as PLANNED tiers and is correct as written; it is not
what this package fixes. If you believe you have found a real defect in P100's
shipped work, that is a NEW finding — report it as such under the BLOCKED rule
rather than editing those files.

Per the estate's standing design mandate, `nyxloom` is offline/unused: do not
design for external consumers or migrations of `_TIER_BAND`. The sweep above
confirms it has none.

### Expected lint output

`python3 exec-nyxloom.py lint nyxloom-trove/handoffs/nyxloom-P101-retire-tier-band.md`
emits exactly four warnings and no errors:

```
- L13 warning oracle 'O3' references path 'src/nyxloom/config.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'src/nyxloom/effects_carve.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'tests/test_config.py' not covered by scope.touch
- L13 warning oracle 'O3' references path 'tests/test_daemon.py' not covered by scope.touch
```

All four are the known **named-negative** false-positive class. O3 cites those
files as explicitly *permitted* negatives — the places where `implement-3` is
mentioned correctly and which O3's scan root deliberately excludes — not as
paths any oracle reads or edits. Two of them (`config.py`, `effects_carve.py`)
are in `forbid`, so adding them to `scope.touch` would directly contradict it.
Do not "fix" these warnings by widening scope. If lint emits anything else,
that is a real finding.

## BLOCKED rule

BLOCKED: if a named contract cannot be met as specified, or scope requires a
forbidden file, STOP — write `BLOCKED: <reason>` to
`nyxloom-trove/reports/nyxloom-P101-LOG.md`, commit, and exit. Do NOT improvise
a workaround. A BLOCKED exit is a success mode: it is a cheap, clean signal the
controller re-routes on, whereas a silently improvised workaround is the
expensive failure.
