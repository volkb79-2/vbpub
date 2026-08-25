---
schema_version: 1
id: ciu-P32-repo-root-precedence-and-docs
project: ciu
component: dev+docs
title: "dev.resolve_repo_root() checks ambient $REPO_ROOT BEFORE --define-root, contradicting its own documented contract (SPEC.md S1.1, CONFIG.md, CIU.md all say --define-root wins first) and reproducing the CIU-41 masked-default hazard for the resolver that decides WHICH REPO ciu dev/worktree verbs operate on -- live-reproduced by the operator standing in vbpub, no --define-root given, getting dstdns's worktree list back because an ambient REPO_ROOT from a sourced sibling ciu.env silently outranked deriving from cwd; fix the precedence to match the documented contract PLUS close the masked-default gap the documented contract itself still has (apply the same S2.7 refined-precedence pattern already used for REPO_NAME/INSTANCE_ID/DOCKER_NETWORK_INTERNAL/PUBLIC_FQDN), and add explicit usage()/docs guidance"
tier: implement-3
input_revision: "e61df823"
source: {kind: operator-report, ref: "operator live reproduction 2026-08-25 (ciu worktree list in /workspaces/vbpub returned dstdns's worktrees until an explicit `ciu env generate` + source); independently corroborated by the worktree-identity-wave retrospective review's HIGH finding #2 (dev.py:40-44, ambient REPO_ROOT overrides explicit --define-root, contradicting CIU-29 req 8's own oracle)"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/dev.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_dev.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/CIU.md"
    - "docs/DESIGN-GUIDE.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P32-repo-root-precedence-and-docs-LOG.md"
  forbid:
    - "src/ciu/deploy.py"
    - "src/ciu/engine.py"
    - "src/ciu/worktree.py"
    - "src/ciu/workspace_env.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-precedence-corrected
    observable: "`dev.resolve_repo_root(define_root, start_dir)` implements, in order: (1) `define_root` when given -- returned immediately, no consistency check, explicit always wins; (2) else walk up from `start_dir` looking for `ciu.global.defaults.toml.j2`; if found, that is the DERIVED root; (3) if a DERIVED root was found AND ambient `$REPO_ROOT` is also set: equal -> silent, use it; UNEQUAL -> REFUSE with a tagged `[S1.1]` ValueError naming both the derived root and the ignored ambient value, and the three remedies (unset REPO_ROOT, pass --define-root explicitly, or cd to the intended repo) -- do NOT silently prefer either value on a real disagreement, this is a WHICH-REPO-AM-I-OPERATING-ON decision feeding destructive verbs (worktree rm, branches -y, clean), not a value written to a generated file; (4) if walk-up finds NOTHING (no ciu.global.defaults.toml.j2 anywhere up from start_dir): fall back to ambient `$REPO_ROOT` if set (nothing better to derive), else fall back to `start_dir` (today's ultimate fallback, unchanged)."
    negative: "ambient REPO_ROOT silently winning over a successful walk-up derivation when they disagree (the exact defect reproduced live); define_root failing to win outright when both it and a conflicting ambient REPO_ROOT are present; a refusal firing when ambient REPO_ROOT is simply ABSENT (that must stay silent -- only a genuine disagreement refuses)"
    gate: "tester-unified"
  - id: O2-live-reproduction-closed
    observable: "A test reproduces the operator's EXACT scenario: cwd inside a real ciu-managed repo tree (containing ciu.global.defaults.toml.j2), no --define-root given, ambient $REPO_ROOT set to a DIFFERENT, also-real-looking path -- `resolve_repo_root` now REFUSES naming both paths, instead of silently returning the ambient (wrong) one. A second test proves the common, non-contaminated case is unaffected: cwd inside a repo, no ambient REPO_ROOT set at all -> derives from cwd exactly as before, no refusal, no behavior change from today."
    negative: "a test that only checks the refusal message text without confirming NO caller proceeds to operate on either path when it fires"
    gate: "tester-unified"
  - id: O3-callers-propagate-refusal-cleanly
    observable: "Every one of dev.resolve_repo_root's ~8 existing call sites in cli.py (grep `from .dev import resolve_repo_root` and `resolve_repo_root(` to enumerate them all -- do not assume the count from this handoff, re-verify) lets the new ValueError propagate to a clean `[ERROR] ...` message + non-zero exit, matching this codebase's existing top-level error-handling convention (find and mirror it) -- no caller catches and silently swallows or downgrades it to a warning."
    negative: "any call site catching the new ValueError and falling back to a value anyway (that would defeat the whole point of refusing)"
    gate: "tester-unified"
  - id: O4-docs-corrected-and-hazard-named
    observable: "docs/SPEC.md S1.1, docs/CONFIG.md's REPO_ROOT precedence row, and docs/CIU.md's REPO_ROOT precedence row are ALL corrected to state the new, ACTUAL precedence (define_root always wins; ambient REPO_ROOT only adopted when consistent with a derived root or when nothing can be derived; a real disagreement refuses naming both values) -- replacing whatever they currently say (verify what they currently say first; if they already claim define_root wins first, as this handoff suspects from a preliminary grep, that means the CODE was violating its OWN documented contract -- say so explicitly in the LOG, it's a notable finding in its own right). docs/DESIGN-GUIDE.md gains a short section (near the existing worktree-identity-guard section at DESIGN-GUIDE.md:213-222, which already explains a related ambient-REPO_ROOT hazard for a DIFFERENT check -- cross-reference it, don't duplicate its reasoning) explaining WHY this refusal exists: a login shell sourcing a sibling checkout's `ciu.env` (the documented convenience pattern this codebase's own CIU-41 finding already named) silently carries that OTHER checkout's REPO_ROOT into every derived shell, and `ciu dev`/`ciu worktree *` used to trust that ambient value over deriving from where the operator is actually standing."
    negative: "leaving stale precedence prose that still describes the buggy (pre-fix) order after the code changes"
    gate: "tester-unified"
  - id: O5-usage-text
    observable: "`cli.py`'s `_USAGE` module docstring (or the per-verb help for `dev`/`worktree`, whichever this codebase's existing convention favors for this kind of cross-cutting environment-resolution note -- check both and pick the one a user actually reads before hitting this) gains a concise note on REPO_ROOT resolution order and the sourced-sibling-ciu.env hazard, so a user reading `ciu --help`/`ciu dev --help`/`ciu worktree --help` can find this WITHOUT having to hit the refusal first."
    negative: "documentation only in SPEC.md/CONFIG.md (which a user consults as reference, not proactively) with nothing in the CLI's own --help output"
    gate: "tester-unified"
  - id: O6-followup-filed-not-silently-expanded
    observable: "A NEW backlog entry (or an amendment naming this specific gap) records that at least ~8 OTHER call sites in cli.py resolve `repo_root` via a bare `Path(os.environ.get('REPO_ROOT', Path.cwd()))` with NO define_root consideration and NO walk-up at all (grep `os.environ.get(\"REPO_ROOT\", Path.cwd())` in cli.py yourself to get the current exact count/verbs -- name them) -- these are NOT touched by this package (different resolution strategy entirely, touching more verbs and closer to deploy.py's own separate resolver would be needed for full unification, too large for this package) but must not be left undiscovered for a future reader; state plainly that this package closes the gap for `ciu dev`/`ciu worktree *` specifically, not for every ciu verb."
    negative: "silently widening this package's scope to touch all 8+ other call sites (real scope creep into deploy.py-adjacent territory); OR failing to file the gap at all, leaving it to be rediscovered from scratch later"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "fixing O3 (clean refusal propagation) requires changing error-handling in a forbidden file (deploy.py/engine.py) -- BLOCKED naming the exact call site, do not touch the forbidden file"
  - "the walk-up-finds-nothing fallback-to-ambient case (O1 step 4) turns out to be reachable in a way that reintroduces the SAME hazard this package closes -- BLOCKED naming the exact scenario, do not ship a fallback that recreates the defect"
mutexes: [merge-lane]
review_focus:
  - "reproduce the operator's EXACT live scenario as your own test, not a synthetic simplification -- two real-looking repo paths, one ambient env var, standing in the correct one with no --define-root"
  - "confirm the refusal fires ONLY on genuine disagreement, never on ambient-REPO_ROOT-simply-absent (that must stay exactly as fast/silent as today for the common case)"
  - "confirm every one of dev.resolve_repo_root's real call sites in cli.py actually surfaces the refusal cleanly, don't just check the function in isolation"
  - "confirm the docs-vs-code contract violation finding (if real) is stated plainly in the LOG as its own notable fact, not buried"
---

# ciu-P32 — `dev.resolve_repo_root` precedence fix + docs/usage() clarification

## Context to read first

1. `src/ciu/dev.py:32-51` (`resolve_repo_root`, whole function) — the buggy
   precedence: `env_root = os.environ.get("REPO_ROOT"); if env_root: return ...`
   checked BEFORE `define_root`.
2. `docs/SPEC.md` — search `S1.1` — the NORMATIVE precedence this function is
   supposed to implement (preliminary read suggests it already says
   `--define-root → REPO_ROOT env → walk-up`, i.e. define_root FIRST — confirm this
   yourself and record in the LOG whether the code has been violating its own
   documented contract).
3. `docs/CONFIG.md` — search for the `REPO_ROOT` precedence row (around line 659 per
   a preliminary grep) and `docs/CIU.md` (around line 531) — same claim, two more
   places to correct if the code changes.
4. `docs/DESIGN-GUIDE.md:200-230` (approximate — search for the existing
   worktree-identity-guard / ambient-REPO_ROOT section) — read this FIRST as your
   tone/placement precedent; it already explains a RELATED but DIFFERENT ambient-
   REPO_ROOT hazard (a different check, in `worktree.py`, forbidden for this
   package) — cross-reference it, do not duplicate its reasoning verbatim.
5. `src/ciu/workspace_env.py` — search for `S2.7`/`refined precedence` — the
   ALREADY-SHIPPED pattern for CIU-41/CIU-47 (derived-wins-unless-ambient-agrees,
   warn on mismatch) that this package's REFUSAL variant is modeled on (read-only,
   `workspace_env.py` is a forbidden file — you are borrowing the PATTERN, not the
   code).
6. `src/ciu/cli.py` — every call site of `from .dev import resolve_repo_root` /
   `resolve_repo_root(` (grep to enumerate all of them, re-verify the count) — these
   are the callers whose error-propagation you must confirm in O3. ALSO grep
   `os.environ.get("REPO_ROOT", Path.cwd())` for the SEPARATE bare-fallback call
   sites (O6 — do not touch these, just name them in the follow-up).
7. `tests/tests/test_ciu_dev.py` (or wherever `resolve_repo_root` is currently
   tested — grep to find it) — the existing fixture style to mirror.

## Work

1. Fix the precedence in `dev.resolve_repo_root` per O1.
2. Add the two tests per O2 (live-scenario reproduction + unaffected common case).
3. Verify/fix clean refusal propagation across all real call sites (O3).
4. Correct SPEC.md/CONFIG.md/CIU.md precedence documentation + add the
   DESIGN-GUIDE hazard section (O4).
5. Add the `_USAGE`/per-verb help text (O5).
6. File the O6 follow-up naming the other bare-fallback call sites, without
   touching them.

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P32-repo-root-precedence-and-docs-LOG.md`, commit, exit.
