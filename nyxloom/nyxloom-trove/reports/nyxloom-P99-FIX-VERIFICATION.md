# nyxloom-P99 — fix-verification pass

Handoff under review: `nyxloom-trove/handoffs/nyxloom-P99-l10-per-project-thresholds.md`
Repaired at commit `e3baff00`, re-frozen at `9ceb6eb9` (input_revision `e3baff00`,
its own prior commit — two-step freeze confirmed via `git log`).
Prior review: `nyxloom-trove/reports/nyxloom-P99-CARVE-REVIEW.md` (verdict: NOT READY).

This is a fix-verification pass, not a fresh review: each prior finding is
checked for actual closure against the new oracle/work-item text, with
independent re-execution where the claim is checkable (tracer bullet,
schema entry point, baseline cleanliness). No new findings are raised
except where the repair itself introduces a defect.

**Verdict: READY**, with one residual non-blocking observation (§7).

---

## 1. B1 (load-wiring gap) — RESOLVED, confirmed

O1's observable now requires "A REAL `ProjectConfig.load()` call (not
`dataclasses.replace`)" producing a `cfg` with `cfg.l10.error_tokens ==
25000` and `cfg.l10.warn_tokens == 10000`, plus the downstream
`lint.lint_file` boundary check. Work item 2 now explicitly instructs
"Then pass `l10=l10` into the `return cls(...)` constructor call" and
names the exact site (`config.py` ~450-474). Independently re-verified:

- `hasattr(cfg, "l10")` is `False` on unmodified `e3baff00` even when a
  real on-disk `.nyxloom/project.toml` declares `[lint.l10]\nerror_tokens
  = 25000` (re-ran the carver's tracer bullet myself: built a temp git
  repo via the same `git init`+`add`+`commit` shape `sample_project` uses,
  called `ProjectConfig.load(root)`, confirmed `hasattr(cfg, "l10")`
  is `False` and no exception is raised — today's code silently drops the
  table). This confirms the negative is real, not hypothetical.
- The specific wrong implementation named in my original §2 (parse +
  validate `[lint.l10]` but never assign `l10=l10` into the `cls(...)`
  return) would now produce a `cfg.l10` stuck at the default
  `L10Config()` (10000/18000) when `.load()` is called — O1's assertion
  `cfg.l10.error_tokens == 25000` fails immediately, and the follow-on
  `lint.lint_file` check at the 25000-token boundary would also
  misclassify (still comparing against 18000). **This wrong
  implementation now fails O1 as written.** B1 closed.

## 2. B2/B3 (boundary semantics) — RESOLVED, confirmed

- **B2** (`_check_l10`'s own `>` boundary): O2 now pins "a handoff at
  exactly 10000 tokens is NOT flagged (neither warning nor error) and a
  handoff at exactly 18000 tokens is WARNING not ERROR." This matches
  today's actual code (`if tokens > 18000: error elif tokens > 10000:
  warning` — verified again at `lint.py:1084/1091`, unchanged by the
  repair). A `>=`-based reimplementation flags exactly-10000 as warning
  (fails "NOT flagged") and exactly-18000 as error (fails "WARNING not
  ERROR"). Unambiguous, and it distinguishes `>` from `>=` in both
  directions independent of the two original far-from-boundary tests.
- **B3** (`[lint.l10]` validation's `>=` boundary): O3 adds a third case,
  `warn_tokens == error_tokens` (both `10000`), explicitly stated to also
  raise `ValueError` since the rule is `>=`, not `>`. A validator using
  strict `warn_tokens > error_tokens` (permitting equality) would not
  raise for this case and fails O3's third case specifically, while still
  passing the original `warn=20000/error=10000` case. This is a clean,
  distinguishing test for the exact ambiguity B3 named.

Both boundaries are now stated unambiguously and in the correct,
non-conflated direction (`_check_l10` stays strict `>`; `[lint.l10]`
validation is `>=`) — Work item 2/3's prose calls out explicitly that
these are two *different* boundaries, forestalling the conflation risk.

## 3. B4 (schema) — RESOLVED, confirmed; O5's entry-point hedge checked and found harmless

Work item 6 now gives a literal, complete JSON fragment: `lint` and `l10`
both `additionalProperties: false`, `warn_tokens`/`error_tokens` as
`{"type": "integer", "exclusiveMinimum": 0}`, neither `required`, with the
rationale (O1/O4's partial-override fixture) stated inline. This is a
concrete owned-interface spec, not a decision left to the implementer.

On the flagged uncertainty ("I couldn't pin the exact function name for
the schema-validation check... flagged as a degree of freedom") — I
independently resolved this rather than treating it as still-open:
`lint.lint_config(cfg: ProjectConfig) -> list[LintFinding]`
(`lint.py:326`) is the real, single, already-existing entry point. It:
re-locates the project's raw `nyxloom.toml`/`project.toml` from
`cfg.root`, re-reads it with `tomllib` (independent of `.load()`'s already
-parsed dataclasses), and validates the raw dict against
`nyxloom-config.schema.json` via `jsonschema.Draft202012Validator`,
appending `CFG1` findings. It is wired into the real `nyxloom lint`
pipeline (`lint_project` calls it at `lint.py:241`) — confirmed by
running `python3 -m nyxloom.cli lint` and by reading `lint_project`'s
body directly. So O5's hedge ("whatever the actual entry point is named
at implementation time") is unnecessary — `lint_config` is not something
implementation will *name*, it already exists unchanged by this package —
but the hedge is harmless: an implementer or reviewer finds it with one
grep for `CFG1` in `lint.py`, and it does not admit more than one
plausible candidate.

I also independently verified O5 is actually testable as written: ran
`lint.lint_config()` against the unmodified `sample_project` baseline
toml (no `[lint.l10]` at all) and got zero findings today — so the base
fixture O1/O5 build on is schema-clean, meaning any CFG1 finding that
appears once `[lint.l10]` is added can only come from the new section
itself, not from pre-existing noise. O5's unqualified "produces NO
schema-validation finding" is therefore safe to assert literally, not
ambiguous about which finding it means.

**B4 closed**, entry-point hedge confirmed harmless (not a hidden invented
decision), no re-derivation needed.

## 4. B5 (Implementation packet) — RESOLVED for tier `implement-2`/class `2d`

The new `## Implementation packet (normative)` section supplies, against
AUTHORING.md's packet checklist:

- **Tracer bullet** — carver-run, dated, with a concrete negative
  witnessed (`hasattr(cfg, "l10")` is `False` today) — independently
  reproduced above, confirmed real.
- **Owned interfaces** — `L10Config`'s exact fields/defaults,
  `ProjectConfig.l10`'s field declaration, `_check_l10`'s exact new
  signature.
- **Construction/validation flow** — a 5-row decision table (absent /
  valid-full / valid-partial / two malformed shapes) mapping state to
  `ProjectConfig.load()` outcome.
- **Bounds** — token-counting bound stated as unchanged; explicit
  statement that there is no upper bound on the override values beyond
  ordering + positivity, with the reasoning (raising is a named use case,
  not a value to cap).
- **Traceability** — each of O1-O5 mapped to what it proves.
- **Degrees of freedom** — three named, all incapable of changing
  externally visible behavior (error message wording, inline-vs-helper
  parsing, schema description text).

"Topology and namespaces" (packet item 3) is genuinely inapplicable here
(no repo/project, host/container, or producer/consumer boundary in this
change) and its omission is correctly licensed by AUTHORING's "omit an
item only when genuinely irrelevant." Nothing required for a `2d`/
`implement-2` package is missing. **B5 closed.**

## 5. §4 narrowing (lowering direction) — RESOLVED, confirmed non-redundant with O1

O4 exercises a **different** code path risk than O1: O1 only ever raises
`error_tokens` above the default (25000 > 18000, `warn_tokens` untouched
at its default 10000), so O1 alone cannot distinguish a correct
implementation from one that defensively clamps the override with
something like `max(cfg.l10.error_tokens, 18000)` (a plausible "I'll only
trust the override if it's more permissive" wrong implementation, matching
the exact shape guessed at in my original review's fixture #3). O4's
`warn_tokens=500, error_tokens=1000` — both *below* the tool-wide
defaults — would be silently clamped back up to 18000/10000-ish under
such an implementation, misclassifying a 700-token handoff as clean
instead of WARNING. O1 passes under that clamp; O4 fails. Confirmed
genuinely distinct, not redundant, and it restores NL-3's full proposed
contract (raise *and* lower).

## 6. Re-run false-PASS attacks from the original §2 against the new oracle text

- **Original O1 attack (load-wiring omission)**: now caught by the
  rewritten O1 itself (§1 above) — no longer a false PASS.
- **Original O2 attack (`>` → `>=` drift in `_check_l10`)**: now caught by
  O2's new boundary addendum (§2 above) — no longer a false PASS.
- **Original O3 attack (`>=` → `>` drift in the validator, permitting
  `warn == error`)**: now caught by O3's new third case (§2 above) — no
  longer a false PASS.
- **New attack surface checked, found closed**: a schema author who marks
  `warn_tokens`/`error_tokens` `required` (breaking O1's own partial-
  override fixture) is caught by O5 directly, since O5 reuses that exact
  partial shape and asserts zero findings.
- **New attack surface checked, found open but non-blocking** (§7 below):
  O5 is one-sided — it proves a *legal* partial override isn't rejected,
  but no oracle proves an actually-invalid key (e.g. a typo'd
  `warn_token`) or a stray unknown key under `[lint.l10]` *is* flagged by
  CFG1. A schema that ends up with `additionalProperties` left open
  (default `true`) at the `l10` level — contrary to Work item 6's literal
  text, but never exercised by any oracle in the negative direction —
  would still pass O5 and every other oracle.

## 7. Residual observation (non-blocking)

O5 tests only the "valid partial override is accepted" direction, not the
"invalid/unknown key under `[lint.l10]` is rejected by CFG1" direction,
even though Work item 6 explicitly specifies `additionalProperties: false`
at both levels and O5's own *negative* prose names exactly this failure
mode in words ("a schema with a typo'd property name that silently falls
through `additionalProperties: true`... fails this oracle") without an
observable that would actually exercise it. This is a real gap relative
to AUTHORING's "one valid + at least two invalid examples" packet
guidance, but I am not treating it as disqualifying, because:

1. The schema shape itself is not left for the implementer to invent —
   Work item 6 pins it in literal JSON, so nothing externally visible is
   unspecified; this is a test-coverage gap, not a contract gap.
2. A structural safety net already exists independent of the schema: an
   unexpected key reaching `L10Config(**that_dict)` in `config.py` raises
   a raw `TypeError` from the dataclass constructor itself (verified by
   inspection — no `**kwargs` catch-all exists on `L10Config`), so a
   misspelled key still fails loudly in production even if
   `additionalProperties` were accidentally left open; the schema
   enforcement is corroborating/cosmetic (nicer `nyxloom lint` messaging),
   exactly as the original handoff's Work item 6 framed it before the
   repair ("NOT required for the feature to function").
3. It is a one-line addition for a future micro-repair (one more O5-style
   assertion with an unknown-key fixture) if the operator wants it closed
   before dispatch; it does not warrant blocking this package again.

## 8. Verdict

**READY.** All six items (B1-B5, §4) are confirmed resolved against the
actual repaired oracle/work-item text, with independent re-execution of
the tracer bullet, the schema baseline-cleanliness check, and the
`lint_config` entry-point claim (all confirmed true, not just asserted).
The one residual gap (§7) is a minor, contained test-coverage asymmetry
in a work item the handoff itself already scoped as non-functionally-
required, with an independent structural safety net already in place — it
does not meet AUTHORING's disqualifying bar ("any externally visible
decision... remains for the implementer to invent"), since the schema
shape is fully pinned in Work item 6's literal text. Clear to dispatch.
