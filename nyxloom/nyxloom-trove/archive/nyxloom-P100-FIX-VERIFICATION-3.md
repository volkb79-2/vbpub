# nyxloom-P100 — fix-verification round 3 (restored clause, final fresh read)

**Repaired handoff:** frozen at `95472822` (`input_revision: "1183d702"`). **Method:** re-read the
pinned replacement paragraph fresh, sentence by sentence, against the current
`reference/AUTHORING.md` text in this worktree (not the diff, not memory of the prior round), the
same way the dropped clause was originally found.

## Diff confirms scope of the change

```
git diff 10944a67..95472822 -- nyxloom-trove/handoffs/nyxloom-P100-tier-routes-toml-validation.md
```
shows exactly two hunks: the `input_revision` bump, and the restoration of "and a frontier-capable
route" into the pinned paragraph's closing sentence. Nothing else in the handoff moved.

## Sentence-by-sentence comparison, fresh

Current `reference/AUTHORING.md` (lines 86-93, read directly, not from memory):
> Only `implement-1` and `implement-2` are deployed today. Until the remaining bands exist,
> `contract_class` is an authoring/review classification recorded in the body, while frontmatter
> `tier` names a live route. Do not put a nonexistent tier in frontmatter. Routing a `2a`-`2c`
> package through `implement-2` requires an explicit human/controller override and a
> frontier-capable route; preferably carve it down first.

Pinned replacement (handoff, current freeze), broken into the same four content units:

1. **False "deployed today" claim** → replaced with "The table above names the PLANNED tier each
   contract class is intended to route through once the remaining implementer bands exist — it is
   not itself a claim about what `routes.toml` declares today." Correct: removes the false claim,
   states the true relationship (planned mapping, not a current-key claim).
2. **"contract_class is a body classification / tier names a live route"** → "`contract_class`
   (2a-2e) is an authoring/review classification recorded in the body; frontmatter `tier` is a
   different thing entirely: it must always be a literal key that exists in the CURRENT live
   `routes.toml`, chosen for the capability the assigned contract class needs, regardless of what
   name a future band will eventually carry." Both facts carried forward and strictly extended (adds
   the capability-matching guidance the original only implied). The original's "Until the remaining
   bands exist" qualifier is dropped, but re-examined fresh: that qualifier was attached to a
   temporary-sounding framing of a fact that isn't actually temporary (contract_class and tier are
   different vocabularies regardless of how many routing bands exist) — removing it is a genuine
   clarification, not a lost safeguard, unlike the frontier-capable-route clause. No actionable
   requirement is attached to that qualifier that isn't still true after removing it.
3. **"Do not put a nonexistent tier in frontmatter."** → identical, verbatim. Unchanged.
4. **The override/capability sentence** → "Routing a `2a`-`2c` package through a live tier whose
   capability is below what the class needs requires an explicit human/controller override **and a
   frontier-capable route**; preferably carve it down first." Now carries BOTH original safeguards
   forward: (a) explicit human/controller override, (b) a frontier-capable route. Confirmed by
   direct textual comparison — the restored clause is byte-identical in meaning and near-identical
   in wording to the original's "and a frontier-capable route; preferably carve it down first."

No other clause, qualifier, or requirement from the original four sentences is missing from the
replacement. The one structural change (dropping "Until the remaining bands exist" as a standalone
temporal qualifier) does not remove an actionable requirement — it removes a confusing frame around
a permanently-true fact, re-examined specifically for this reason on this pass.

## Verdict: READY

Both fix-verification rounds' findings are resolved: O6 (previous round) is unambiguous and
executable as described; O1's pinned text (this round) is a complete, accurate carry-forward of the
original doctrine's two safeguards, with the false claim correctly removed and no other content
silently dropped. No new issues found on this fresh pass.
