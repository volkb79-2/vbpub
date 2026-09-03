# nyxloom-P100 — fix-verification pass against nyxloom-P100-CARVE-REVIEW.md

**Repaired handoff:** frozen at `bef27117` (`input_revision: "3fd4445b"`), after merging main
(`f94d4988`, bringing in P98/P99's real archival). **Method:** re-verified each finding against the
actual tree/environment, and ran the tightened O1 regex against crafted paraphrases and legitimate
corrections in real Python, not by inspection alone.

## 1. Sibling-handoff landmine — genuinely resolved

Confirmed directly: `nyxloom-trove/handoffs/` now contains only this package's own handoff
(`tier: luna-high`, a real key — verified present in `~/.local/state/nyxloom/routes.toml`'s
`[tiers.luna-high]`) plus two pre-existing non-handoff notes
(`CORE-REDESIGN-SESSION-HANDOFF-2026-08-0{3,4}.md`) that have no YAML frontmatter at all —
`frontmatter.parse_handoff` raises `HandoffParseError: ["missing leading '---'"]` on them, confirmed
by running it directly, so `lint_file` returns an L1 error before L14 (or any other rule) ever runs;
they can't produce a spurious L14 finding. `nyxloom-P98`/`nyxloom-P99`'s handoff+LOG+REPORT+review
files are genuinely present under `nyxloom-trove/archive/` (11 P98 files, 5 P99 files — checked via
`ls`, not just the commit message). O6 + its paired `escalate_if` bullet is a sound, sufficient
closure, not a one-off patch: L14 itself, once merged, is the *permanent* standing guard against any
future regression (every subsequent `nyxloom lint` run enforces it unconditionally); O6 only needs to
cover the narrower carve-to-dispatch concurrency window, which it does. **Resolved.**

One new, real ambiguity surfaced while checking this: **O6 lists `gate: tester-unified` like every
other oracle, but it cannot be meaningfully executed inside that container.** The `tester-unified`
docker invocation (confirmed from this session's own earlier `run-gate.py` argv for a sibling
package) mounts only the repo path, never the host's `$XDG_STATE_HOME`/`~/.local/state` — so
`paths.routes_path()` resolves to a file that does not exist inside the container, and L14 would
report a WARNING (routes.toml unavailable) for every file regardless of any handoff's real tier
value. Scope/forbid itself says the operator's live routing matrix is "not part of this repository"
— so O6, which is specifically about validating against that real file, structurally cannot run
inside the hermetic gate. Work item 4 correctly calls O6 "a one-time real-repo check, not a new
automated test," but nowhere does the handoff say it must be run by the implementer directly on the
host/worktree (outside `./run-gate.py`), which is the only place it can produce a real signal. This
is a host/container namespace confusion the repair didn't address — tagging O6 `gate:
tester-unified` alongside O1-O5 invites an implementer to assume it runs the same way they do.

## 2. NL-7 — real, substantive, correctly scoped

`nyxloom-trove/backlog/NL-7-adapters-py-s-tier-band-hardcodes-implement-n-keys-that-have-ne.md`
exists, is not a stub (91 lines: observed mechanism, reproduction, why-nyxloom-owns-it, two concrete
proposed-contract options, oracles, SPEC ownership, provenance citing this review by name). It
correctly does not touch `adapters.py` and is not referenced anywhere in P100's `scope.touch`.
**Resolved.**

## 3. O1's tightened regex — still evadable, and produces false positives (NOT resolved)

Ran the actual pattern (`(only|still).{0,20}(implement-1|implement-2).{0,40}(deployed|live|today)`,
case-insensitive) against crafted text in Python rather than reasoning abstractly:

**Evasion (false negative) — the paraphrase attack is not closed, only narrowed:**
```
no match: `implement-1` and `implement-2` remain the only bands live today.   # "only" AFTER the tier mention
no match: Only `implement-1` and `implement-2` remain routable right now.     # "routable" not in the word list
no match: Currently, `implement-1` and `implement-2` are the sole bands that actually route.
no match: Presently, `implement-1` and `implement-2` are the only active bands; the rest await B16.
no match: Still, `implement-1` and `implement-2` are, for the time being and until further
          routing work lands, deployed.                                       # gap > 40 chars
```
Every one of these asserts the exact same false claim O1 exists to eliminate. The regex is
order-dependent (`only`/`still` must precede the tier token) and window-bounded (40 chars to the
verb) — trivially defeated by reordering, a synonym outside the fixed word list, or padding the
gap. This is not a contrived adversarial case; a carver naturally writing "implement-1 and
implement-2 remain the only bands live today" while drafting a REPLACEMENT paragraph would produce
exactly this, in good faith, and O1 would still pass.

**False positive — legitimate, CORRECT prose can fail O1:**
```
FALSE POSITIVE (MATCH): the 2a-2e ladder still names `implement-2` as a planned band, not a live
                         key today.
FALSE POSITIVE (MATCH): It is still true that `implement-2` is not deployed today -- pick a real
                         routes.toml key instead.
```
Both sentences correctly state the TRUE fact (implement-2 is *not* live/deployed today) — exactly
the kind of accurate correction Work item 1 asks the implementer to write — yet the regex flags
them, because it only checks for the *tokens* `only`/`still` + tier + `deployed`/`live`/`today` in
proximity, not the polarity of the claim (it cannot distinguish "still true that X is NOT deployed"
from "X is still deployed"). An implementer hitting this would either get a confusing spurious O1
failure on correct work, or — worse — learn to route around the regex's specific shape rather than
write natural, accurate prose, which defeats the oracle's purpose either way.

**Conclusion: the "family of same-meaning claims" widening did not achieve its stated goal.** A
semantic check (or a human-read requirement, since O1 is inherently a prose-accuracy oracle) is
needed here; a fixed-window keyword regex cannot distinguish truth-value or absorb ordinary phrasing
variance in either direction. **Not resolved** — recommend replacing the regex with either (a) a
requirement that the replacement paragraph be reviewed for semantic accuracy rather than
grep-checked, or (b) a much simpler mechanical proxy that doesn't attempt polarity-sensitive natural
language matching (e.g., ban the token `implement-1` and `implement-2` from co-occurring with a
present-tense form of "to be" *and* a superlative/exclusivity word anywhere in the same paragraph,
still imperfect, or just accept this half of O1 needs a reviewer, not a regex).

## 4. O3's tightened fixture — genuinely closes the blocklist evasion

`sonnet5-hgih` (a transposition, never one of the three named historical values) is now a REQUIRED
fixture, and the negative explicitly names the blocklist shape (`tier in {"implement-2",
"sonnet-xhigh", "opus-xhigh"}`) alongside the allowlist shape. A hardcoded blocklist of exactly the
three historical strings has never seen `sonnet5-hgih` and would silently let it pass, directly
contradicting the now-required ERROR — closing the exact gap found in the first review round.
Combined with O5 (the valid set changes entirely mid-process), no static list or heuristic can
satisfy both. **Resolved.**

## 5. O4's malformed-routes.toml case — genuinely closes the narrow-except evasion

The new case (b) requires an actually-constructed on-disk malformed file (invalid TOML syntax, or a
`[tiers.x]` table missing its `routes` key), run through the real CLI subprocess, producing a
WARNING rather than a crash. Independently confirmed both failure modes are real and distinct from
`FileNotFoundError`: `tomllib.TOMLDecodeError` (invalid syntax) and `KeyError` (from
`Routes.load()`'s own `spec["routes"]` access in its dict comprehension, confirmed by reading the
function) — neither is an `OSError` subtype, so a narrow `except FileNotFoundError:` would let both
propagate uncaught exactly as the negative now describes. The fixture forces the implementer to
actually construct case (b) on disk rather than assume `Routes.load()`'s only failure mode is a
missing file. **Resolved.**

## Verdict: NOT READY

Findings 1, 2, 4, and 5 are genuinely resolved with real, verified evidence. Finding 3 (O1's
tightened regex) is not: it is both trivially evadable (five distinct working paraphrases, verified
by execution) and prone to false positives on legitimate accurate prose (two verified). A new,
related issue surfaced during re-verification of finding 1: O6's `gate: tester-unified` tag is
inconsistent with its own nature — it cannot produce a real signal inside the hermetic gate
container, which does not mount the host's routes.toml, and the handoff never says to run it
elsewhere. Recommend: replace O1's regex-based widening with a semantic/reviewer-checked
requirement (a fixed-window keyword regex cannot carry polarity), and clarify where/how O6 is meant
to execute (explicitly a host/worktree step, not `gate: tester-unified`) before re-freezing.
