# nyxloom-P100 — fix-verification round 2 (pinned O1 text, O6 execution environment)

**Repaired handoff:** frozen at `10944a67` (`input_revision: "8fcc207c"`). **Method:** re-read the
pinned replacement paragraph fresh against the actual current `reference/AUTHORING.md` text in this
worktree (not against memory of the earlier round), and re-checked O6's new wording for residual
ambiguity.

## 1. O1 — the paraphrase/polarity escape is closed; but the pinned text itself has a new factual gap

**Mechanism is closed.** Because Work item 1 now pins the exact replacement paragraph verbatim and
forbids rewording, there is no remaining wording freedom for an implementer to exploit — the earlier
finding (a regex that was both evadable and false-positive-prone) is moot once the text itself is no
longer discretionary. The five paraphrase constructions I built against the old regex, and the two
false-positive constructions, are all attacks on a *regex*; there is no regex left to attack. One
minor, non-blocking mechanical note: the observable literally says "a plain fixed-string `grep -F`,"
but the pinned paragraph spans multiple wrapped lines and AUTHORING.md's own line-wrap width may not
match the handoff's blockquote wrapping — a literal single-line shell `grep -F` would not match a
multi-line paragraph as written. This is ordinary implementation latitude (any competent
implementer reaches for a whitespace-normalized substring check, e.g. two lines of Python, not the
literal `grep` binary across newlines) and is a different kind of gap than the semantic
open-endedness that broke the original O1 — not blocking.

**The pinned text itself is not fully accurate — confirmed by reading BOTH texts side by side in
this worktree, not from memory:**

Current `reference/AUTHORING.md` (lines 88-92, read fresh just now):
> Only `implement-1` and `implement-2` are deployed today. Until the remaining bands exist,
> `contract_class` is an authoring/review classification recorded in the body, while frontmatter
> `tier` names a live route. Do not put a nonexistent tier in frontmatter. **Routing a `2a`-`2c`
> package through `implement-2` requires an explicit human/controller override and a
> frontier-capable route**; preferably carve it down first.

Work item 1's pinned replacement (handoff lines 266-277):
> ... Routing a `2a`-`2c` package through a live tier whose capability is below what the class
> needs **requires an explicit human/controller override**; preferably carve it down first.

The pinned replacement drops **"and a frontier-capable route"** without comment. The original states
TWO independent safeguards for the override case: (1) explicit sign-off, AND (2) the substitute
route actually has frontier capability. The replacement only carries (1) forward. This is not a
cosmetic casualty of removing the stale `implement-2` name — the two requirements are logically
independent (an operator could grant an override without verifying the replacement route's
capability), and the new phrasing ("a live tier whose capability is below what the class needs")
describes the *problem* being overridden, not a restatement of requirement (2). A reader of the new
paragraph alone would reasonably conclude that obtaining an override is sufficient, silently
dropping the capability-floor requirement the current doctrine states today.

Because Work item 1 mandates this text **verbatim, no rewording**, the implementer has no scope to
add the missing clause back even if they notice the gap — and no oracle checks the pinned text's own
completeness against the original's OTHER true content (O1 only checks that the pinned text is
present, not that it preserves everything worth preserving). This is a carve-level defect, not
something dispatch can self-correct.

## 2. O6 — now unambiguous and executable as described

The new observable/negative text explicitly states: run on the worktree's own filesystem, the
implementer's own shell, `python3 -m nyxloom.cli lint` or equivalent, **never** inside
`tester-unified` (stating the mechanical reason — the container doesn't mount
`$XDG_STATE_HOME` — which I independently confirmed in the previous round from this session's own
prior `run-gate.py` container argv), evidence captured verbatim in `nyxloom-P100-REPORT.md`, and the
negative explicitly forbids faking a routes.toml inside the container as a workaround. This fully
resolves the ambiguity flagged last round. `gate: tester-unified` remains on the oracle's frontmatter
field, but given the prose now unambiguously describes a host-side manual step whose evidence lives
in REPORT.md, this reads as the schema's required-gate-id bookkeeping (L2's "gate ids exist" check),
not a claim that O6 executes inside that gate's automated suite — not confusing in context anymore.
**Resolved.**

## 3. Nothing else shifted incorrectly

Diffed the full handoff between the two freezes: `scope.touch` gained `nyxloom-P100-LOG.md`/
`REPORT.md` (both `NEW`, both reasonable — REPORT.md is explicitly needed to hold O6's evidence);
O3/O4/O5's text is byte-identical to the already-verified prior round; `escalate_if`,
`Scope/forbid`, Work items 2-4 (aside from O6's environment clause folded into Work item 4's own
bullet, consistent with the oracle text), and the Implementation packet are otherwise unchanged
except the "degrees of freedom" paragraph, correctly updated to say the AUTHORING.md wording is no
longer free (consistent with Work item 1's new pinning). No unrelated drift found.

## Verdict: NOT READY

O6 is fully resolved and O1's mechanism (verbatim pinning) genuinely closes the paraphrase/polarity
problem from the previous round. But the specific text now pinned — which O1 will mechanically
enforce byte-for-byte once implemented — silently drops a real, currently-true safety requirement
("and a frontier-capable route") from the sentence it replaces, with no implementer discretion to
restore it. Recommend adding the missing clause back into the pinned paragraph (e.g., "...requires
an explicit human/controller override and a route whose actual capability meets what the class
needs; preferably carve it down first") before re-freezing.
