# nyxloom-P100 — pre-dispatch adversarial handoff review

**Reviewed:** `nyxloom-trove/handoffs/nyxloom-P100-tier-routes-toml-validation.md`, frozen at
`725a772a` (`input_revision: "a10c7978"`, its own first commit). **Backlog basis:** NL-2.
**Method:** every factual/sweep claim re-derived independently from the live tree/environment, not
trusted from the handoff's prose.

## Verdict up front: NOT READY

The doc-fix + L14 mechanism (Work items 1-4) is itself reasonably well specified. The reason for
NOT READY is that the carve's sweep is verifiably incomplete in exactly the way NL-2 warns about,
with a concrete, currently-live consequence: **this repo's own three open handoffs — including
nyxloom-P98, nyxloom-P99, and this handoff itself, nyxloom-P100 — all declare `tier: implement-2`**,
the precise invalid value L14 exists to catch, and the carve makes no provision for them.

## Independent verification of the carve's factual claims

- **`Routes.load()` has no caching, confirmed by reading the function** (`src/nyxloom/config.py:690-712`):
  a plain `@classmethod` that does `p.read_text(...)` + `tomllib.loads(...)` fresh on every call —
  no `@lru_cache`, no module/class-level cache attribute. O5's premise holds.
- **`fm.tier` is confirmed as an existing, already-used attribute**: `reconcile.py:1072`
  (`_check_healthy_route`) and `rules_dispatch.py:109` both do
  `inp.routes.for_tier(fm.tier)` verbatim. `types.py:494` confirms `Frontmatter.tier: str` is a
  required (no-default) field.
- **The live `routes.toml` keys are confirmed accurate**: read `~/.local/state/nyxloom/routes.toml`
  directly — `grep -n "^\[tiers\."` returns exactly `flash-high, flash-max, terra-med, luna-high,
  sonnet5-high, frontier-review, haiku-low, free-high`, matching the handoff's list exactly. Its own
  comment confirms the B16 framing verbatim: "Destined for the `implement-2` tier once B16 ...
  lands." Worth noting for the record: the file's `revision = "2026-07-23"` and mtime are unchanged
  since before NL-2 was even filed (2026-08-25) — the "current as of now" claim is true, but
  trivially so (nothing has touched the file since), not because anyone re-verified it today.
- **AUTHORING.md's OTHER `implement-N` mentions**: confirmed the ladder table (lines 80-86) and the
  2a-2e subsection headers (95-134) are the only other hits, and they describe the **planned**
  contract-class-to-tier mapping, never claiming these are live `routes.toml` keys today — the
  carve's claim that only lines ~88-93 and ~390 need editing is correct.
- **`lint_file`'s call sequence and `_check_l13`'s signature/style are confirmed** as described
  (`lint.py:218` calls `_check_l13(findings, path, fm)` — no `cfg` needed — immediately establishing
  the precedent the handoff's proposed `_check_l14(findings, path, fm)` signature follows).

## A second copy of the bad pattern the carve's sweep missed (real, in production code)

The coordinator asked whether any other lint rule/schema field/fixture already hardcodes tier
names. None inside `lint.py`/`schemas/` do — but **`src/nyxloom/adapters.py:181`** does:

```python
_TIER_BAND = {"implement-1": 1, "implement-2": 2, "implement-3": 3}
```

consumed by `compute_review_depth_directive` (line ~192), which reads the SAME `fm.tier` field L14
validates. Since no live handoff has ever used `implement-N` values, `_TIER_BAND.get(tier or "")`
has been returning `None` for every real handoff since D-BATCHC shipped (2026-07-26), silently
falling back to the scope-size proxy every single time — not a crash, but the exact same false
premise NL-2 diagnoses, now embedded in a second, unrelated piece of production logic, undetected
because the fallback happens to be a reasonable substitute. This is not P100's job to fix (different
bug, different owner, `adapters.py` is not in `scope.touch` and shouldn't be), but the carve's
implicit claim of understanding "the" tier-confusion problem is incomplete, and `adapters.py`'s own
comment ("`implement-1`..`implement-3` per `_TIER_BAND`") could itself mislead a future reader into
re-committing the exact error NL-2 describes. **Recommend filing this as its own backlog entry
before or alongside dispatch, not silently leaving it found-and-unrecorded.**

## Blocking finding: the package's own sibling handoffs already violate the rule it introduces

`nyxloom-trove/nyxloom.toml` declares `handoff_globs = ["nyxloom-trove/handoffs/*.md"]` — nyxloom
lints its own trove. All three currently-open real handoffs there declare `tier: implement-2`:
`nyxloom-P98-retire-toolkit-gate-verify.md:6`, `nyxloom-P99-l10-per-project-thresholds.md:6`, and
**`nyxloom-P100-tier-routes-toml-validation.md:6` — this handoff's own frontmatter.** Once L14 ships
(ERROR severity when `routes.toml` loads but the tier doesn't resolve — confirmed it will, since the
live file loads fine), linting any of these three real files will produce a genuine L14 ERROR. This
is not cosmetic: `daemon.py:1452` does `lint_clean[fm_id] = not lint.has_blocking(f)`, and
`reconcile.py`'s own module contract (item 1) transitions `CARVED -> QUEUED` only when
`lint_clean[id]` is `True`; `effects_carver.py:592` uses the identical `has_blocking` check to gate
carve-proposal admission. **The mechanism this package is adding will mechanically un-queue (or
refuse to queue) its own sibling packages and itself**, the moment someone or the daemon next lints
them against the live `routes.toml` — with zero remediation Work item, zero decision entry, and zero
`escalate_if` trigger naming this. (I did not find live daemon state files for P98/P99/P100 under
`~/.local/state/nyxloom/projects/nyxloom/state/`, so this may not be actively mid-flight through the
daemon's own tick loop right now — but the code path is real and unconditional, and the frontmatter
values are real and currently committed, so the exposure is structural, not hypothetical.) This is
squarely a scope/dependency defect AUTHORING.md's own escalate_if doctrine anticipates ("any touched
non-test file outside this list needs an edit to keep the gate green") — except here the affected
files are entirely *un-named*, so an implementer has no scope authorization to fix them and no
trigger telling them to look. Either P100 needs a Work item to correct its own and its siblings'
`tier` values as part of landing L14, or the coordinator needs an explicit decision to accept the
transitional breakage (and sequence P98/P99's merge/archival before L14 ships).

## 1. Blocking ambiguities

- **The sibling-handoff landmine above** — no decision owner named, no remediation path.
- **O1 is defeatable by exact-string paraphrase** (see false-PASS attacks below) — the corrected
  paragraph's accuracy is checked only by a literal `grep -c` of one exact sentence, not a semantic
  check that the *replacement* prose is itself non-stale.
- **O4 only tests the "file does not exist" case**, never a present-but-malformed `routes.toml`
  (bad TOML syntax, or a `[tiers.x]` entry missing its `routes` key, which raises `KeyError` inside
  `Routes.load()`'s own comprehension) — Work item 3 says "catch broadly," but nothing enforces that
  instruction was actually followed rather than a narrow `except FileNotFoundError`.

## 2. False-PASS attacks (one per oracle)

- **O1**: reword the banned sentence instead of deleting it — e.g. "`implement-1` and `implement-2`
  remain the only bands live today" (word order/tense changed, same false claim). `grep -c` for the
  EXACT original phrase reports `0`; the worked example's placeholder is fixed correctly; the
  contract_class/tier distinction is stated. O1 passes in full while the doctrine is still wrong.
  A second variant: replace the deleted sentence with a NEW hardcoded list of "current" tier keys in
  prose (not in the YAML block, which O1's second check does cover) — e.g. "a live key such as
  `sonnet5-high`, `flash-high`, `terra-med`..." — passes both greps, states the distinction, and
  still re-embeds exactly the staleness-prone pattern NL-2 exists to remove, one sentence away from
  the one O1 actually inspects.
- **O2**: a `_check_l14` that is a complete no-op stub (never appends any finding, for any input)
  passes O2 in isolation (no finding for the valid-tier case, via both call paths, trivially) — O3
  is what actually catches this, not O2. Naming it because AUTHORING's protocol asks per-oracle.
- **O3**: implement the check as a hardcoded **blocklist** of exactly the three cited historical
  values (`if fm.tier in {"implement-2", "sonnet-xhigh", "opus-xhigh"}: error`) rather than reading
  `Routes.load().tiers` as an allowlist. Passes O3's three named fixtures exactly (and even
  produces a plausible nearest-match string if hardcoded alongside). This is the same disease O3's
  own negative anticipates, but shaped as a blocklist rather than the hardcoded-allowlist the
  negative's prose literally describes — worth tightening the wording. (O5 does catch this variant,
  see the corrected matrix below — it isn't a full escape, just an O3-local one.)
- **O4**: catch only `FileNotFoundError` (matching O4's literal "does not exist" scenario) and let
  any other `Routes.load()` exception (malformed TOML, a tier entry missing `routes`) propagate
  uncaught out of `lint_file`, crashing the WHOLE lint run for that file, all rules included. Passes
  O4 exactly as tested; violates Work item 3's "catch broadly" instruction and the evident intent
  ("an environment that hasn't run onboarding yet must still be able to use `nyxloom lint`") for the
  untested malformed-but-present case.
- **O5**: none found — this oracle is well-constructed. Any cache, wherever added (`Routes.load()`
  itself, a memoized `_check_l14`, or a `lint_file`-level cache), is exposed because O5 asserts a
  DIFFERENT observable result from the SAME nominal input (path + tier string) after an external
  file mutation, which no plausible cache shape can satisfy by accident.

## 3. Missing implementation-packet content

- No remediation plan (Work item, decision, or explicit deferral) for the sibling handoffs
  (P98/P99/P100 itself) that will trip L14 immediately.
- No fixture/oracle for a present-but-malformed `routes.toml` (only "missing" is tested) — the
  Implementation packet's decision table lists only "Missing / unparseable" as one row, but no
  oracle actually constructs the "unparseable" half of that row.
- No acknowledgment of `adapters.py`'s `_TIER_BAND`, even as an out-of-scope note or a
  cross-reference backlog filing — a reader of this handoff would have no way to know a second,
  live instance of the same root cause exists elsewhere in the codebase.

## 4. Scope/dependency defects

- The sibling-handoff landmine (above) is the primary one: `scope.touch` has no authorization to
  touch `nyxloom-trove/handoffs/nyxloom-P98-*.md` / `-P99-*.md` / this file, yet landing this
  package's Work items unconditionally breaks their lint-clean status the next time they're linted
  against the live matrix.
- `adapters.py`'s `_TIER_BAND` is correctly out of `scope.touch` (a different bug), but its
  existence should have been surfaced by a "no other tier consumer" sweep the handoff's Context
  list implies was done (Context item 3 only names `reconcile.py:1072`/`rules_dispatch.py:109` as
  "already used" call sites — it did not check for OTHER consumers of `fm.tier` besides the routing
  path, which is exactly where `adapters.py`'s copy lives).

## 5. Pairwise input matrix and combined-axis fixtures

Axes: `routes.toml state` × `tier value class` × `call path` × `repeated execution`.

| routes.toml | tier class | lint_file() | real CLI | same-process re-lint |
|---|---|---|---|---|
| valid, declares X | X (valid) | no finding (O2) | no finding (O2) | — |
| valid, declares X | close-but-wrong (typo) | ERROR + suggestion (O3-adjacent, untested exact case) | untested | — |
| valid, declares X | one of 3 historical bad values | ERROR + suggestion (O3) | untested for these 3 | — |
| missing | any | WARNING, L1-L13 unaffected (O4) | untested via real CLI | — |
| present, malformed | any | **untested anywhere** | untested | — |
| valid, declares X then Y | X then Y | — | — | O5 (same path only) |

**Three combined-axis fixtures likely to break a convenient implementation** (at least one not named
by the handoff's own tests, per AUTHORING's requirement):

1. **Malformed-but-present `routes.toml` (a `[tiers.x]` entry with no `routes` key, or invalid TOML
   syntax) linted through the REAL `nyxloom lint <path>` CLI subprocess**, not `lint_file()` directly
   — combines the untested "unparseable" row with the untested "real CLI" path for exactly the
   failure case Work item 3's "catch broadly" instruction is supposed to prevent. Distinguishes a
   narrow `except FileNotFoundError` from the intended broad catch, and distinguishes an
   in-process-swallowed traceback from one that actually kills a subprocess's exit code.
2. **A near-miss tier value NOT in the three named historical fixtures** (e.g. `tier: sonnet5-hgih`,
   a transposition, or `tier: flash-hi`) against the O2 routes.toml — this is the fixture that
   distinguishes a real `Routes.load().tiers`-driven allowlist from a hardcoded blocklist of exactly
   the three cited values (§2's O3 attack): the blocklist implementation silently PASSES this value
   (never seen before, not in its hardcoded set), while a real implementation correctly ERRORs and
   suggests `sonnet5-high`. Not named anywhere in the handoff's own O3/Work item 4 fixture list.
3. **Two DIFFERENT handoff files sharing the identical `tier` string, linted in the same process
   before and after a routes.toml mutation that invalidates that string** (as opposed to O5's design,
   which re-lints the SAME file/path twice) — this is the fixture that would catch a cache
   accidentally keyed on the tier STRING alone rather than on path or freshness, a caching shape O5's
   own construction (same path both times) does not by itself rule out.

## 6. Corrected oracle/fixture matrix (deltas from the frozen handoff)

- O1: add a third check — the replacement paragraph must not introduce a NEW hardcoded list of
  "current" tier names in prose (only the existing "live `routes.toml`" indirection language is
  allowed); tighten the observable to a semantic check or at least widen the banned-phrase grep to a
  regex tolerant of minor rewording, not one exact sentence.
- O3: reword the negative to explicitly rule out a hardcoded **blocklist** in addition to the named
  hardcoded **allowlist**, and add fixture #2 above (a near-miss not in the three historical values)
  as a required case, not an optional one O5 merely happens to backstop.
- O4: add a present-but-malformed `routes.toml` fixture (fixture #1 above) alongside the
  missing-file case, run through the real CLI, and require the SAME graceful WARNING behavior.
- New O6 (or a Work item, not an oracle — this is a product/sequencing decision, not implementer
  discretion): P98/P99/this handoff's own `tier: implement-2` must be corrected to a real live key,
  or the coordinator must record an explicit decision accepting/deferring the transitional
  lint-blocking exposure and naming who fixes it and when.

## READY or NOT READY

**NOT READY.** The mechanical L14 design (Work items 2-4) is close to solid — O5 in particular is a
well-built anti-caching oracle — but: (1) the package will mechanically invalidate its own and two
sibling in-flight handoffs the moment it ships, with no Work item, decision, or escalate_if
acknowledging this; (2) O1 is defeatable by a same-meaning paraphrase of the one sentence it greps
for, and by re-introducing a hardcoded "current tier" list in prose outside the YAML block it
checks; (3) the carve's sweep for other `tier`-hardcoding consumers stopped at `lint.py`/the routing
path and missed a live, second instance in `adapters.py`; (4) O4 never exercises the
malformed-(not-missing)-routes.toml half of its own decision table. Recommend: add the sibling-
handoff remediation (or an explicit deferral decision), tighten O1 and O3/O4 per §6, and file
`adapters.py`'s `_TIER_BAND` as its own backlog entry before re-freezing.
