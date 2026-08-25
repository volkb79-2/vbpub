---
schema_version: 1
id: ciu-P29-hotfix-layout-mutex-bypass
project: ciu
component: cli
title: "HOTFIX (jumps the queue ahead of P20-P27): ciu up --layout's mutual-exclusion guard is a denylist of exact flag spellings that argparse's own abbreviation matching (allow_abbrev=True) walks straight through, silently overriding a layout's per-host bundles with one CLI --profile on every host in the plan; separately, --layout=NAME (equals form) misses the layout dispatch entirely and falls through to the wrong code path with a confusing error"
tier: implement-2
input_revision: "d8b627cb"
source: {kind: retrospective-review, ref: "ciu-retrospective-review-findings.md, config-wave review F-1 (BLOCKING) and F-2 (BLOCKING)"}
stack: none
depends_on: [ciu-P28-hotfix-worktree-branches-prune-safety]
session: fresh
scope:
  touch:
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_cli_layouts.py"
    - "docs/SPEC.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P29-hotfix-layout-mutex-bypass-LOG.md"
  forbid:
    - "src/ciu/deploy_pkg/layouts.py"
    - "src/ciu/deploy.py"
    - "src/ciu/engine.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-abbreviation-proof
    observable: "`cli.py`'s `_LAYOUT_FORBIDDEN` mutual-exclusion check (~lines 1135-1152) catches an ABBREVIATED spelling of any forbidden flag (e.g. `--prof core`, `--pro=core`, `--hos edge-a`, `--th`, `--boot`, `--roll`) exactly as it would catch the full spelling — not by enumerating every possible abbreviation length, but by making the check itself abbreviation-aware: either (a) register the six forbidden long options on a local argparse parser with the SAME `allow_abbrev` semantics the remote parser uses, so argparse itself resolves the abbreviation before your check runs (preferred — reuses argparse's own resolution instead of re-implementing prefix matching), or (b) construct the remote argv from RESOLVED, NAMED values only (profile list, host, dir, etc.) rather than forwarding `remaining` through at all, so there is nothing left in the forwarded argv for an abbreviation to hide in. State which approach you took and why in the LOG. Verify with the EXACT reproduction from the review: a 3-host layout, invoked with `--layout NAME --prof=core` (or any abbreviated form of any of the six forbidden flags), must be REFUSED with the `[S7.5c]` tag, and — critically — must NEVER reach the point of constructing or sending a push command to ANY host."
    negative: "a fix that only lengthens the literal-string denylist with a few more spellings (does not close the general abbreviation class); a fix that resolves abbreviations locally but still forwards the (now-resolved) flag into `remaining` for the remote command, so the remote parser still receives and half-applies it"
    gate: "tester-unified"
  - id: O2-equals-form-dispatch
    observable: "The verb-dispatch check that decides whether `ciu up` routes to the layout path (currently a plain `if \"--layout\" in rest:` membership test) recognizes `--layout=NAME` (equals form) exactly as it recognizes `--layout NAME` (space form) — mirror the SAME prefix-aware predicate you use for O1's forbidden-flag check (e.g. `a == \"--layout\" or a.startswith(\"--layout=\")`), applied at the DISPATCH decision point, not just inside the already-dispatched layout branch. Extend the identical fix to `--host=`/`--dir=` equals forms on `up`/`down`/`render` if those verbs have the same plain-membership dispatch pattern (grep for it; if they don't, don't touch them — this package's scope is the layout/host/dir dispatch bug class, not an unrelated refactor)."
    negative: "fixing only --layout= and leaving an identical --host=/--dir= dispatch bug unfixed if the same plain-membership pattern exists there (grep first, then decide, don't assume without checking)"
    gate: "tester-unified"
  - id: O3-tests
    observable: "New tests reproducing the exact review scenarios: (a) `ciu up --layout prod --prof=core` (and at least 2 other abbreviated forms of different forbidden flags) is refused with `[S7.5c]`, exit 2, and asserted to never construct/invoke any push command (mock the transport and assert zero calls); (b) `ciu up --layout=prod` (equals form, no other flags) resolves and dispatches through the SAME layout path as `--layout prod` (space form) — assert both produce identical resolved Layout objects / identical push sequences; (c) if O2 extends to --host=/--dir=, one test each confirming the equals form now dispatches correctly."
    negative: "a test that only checks the error MESSAGE text without confirming zero push commands were issued (the whole point is no side effect happened, not just that a nice error printed)"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/SPEC.md's S7.5c section is corrected if it previously implied the mutual-exclusion check was complete (name the exact prior claim if any). CHANGES.md gets an Unreleased/Fixed entry naming this as a hotfix for already-released behavior (layouts shipped in ciu-P10, v6.3.0 per the wave history) — state plainly that a layout deployment using an abbreviated companion flag could previously silently deploy the wrong profile to every host, so operators relying on `--layout` should be aware prior versions had this gap. `KNOWN_ISSUES_TODO_BACKLOG.md` gets a new entry or an amendment to the CIU-34 (layouts) row noting this hotfix."
    negative: "downplaying this in CHANGES.md as a minor fix when it's a silent-wrong-deploy class of defect"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the local layout parser cannot be made abbreviation-aware without also changing the REMOTE deploy.py parser's own allow_abbrev behavior in a way that would touch a forbidden file (deploy.py) — BLOCKED naming the exact coupling; approach (b) in O1 (construct remote argv from resolved values only) is the documented fallback specifically because it avoids this coupling, prefer it if approach (a) hits this wall"
mutexes: [merge-lane]
review_focus:
  - "reproduce the review's exact attack (3-host prod layout, --prof=core abbreviation) end-to-end against the FIXED code and confirm zero hosts receive the wrong profile — this was a silent-wrong-deploy in production, verify the fix closes it completely, not just for the one example flag"
  - "confirm the fix didn't just add --profile's abbreviations to a longer denylist while leaving --host/--dir/--thin/--bootstrap/--rollback's abbreviations still open — test at least one abbreviation of EACH of the six forbidden flags"
  - "confirm --layout=NAME truly reaches the same code path as --layout NAME, not a parallel reimplementation that could drift"
---

# ciu-P29 — hotfix: `ciu up --layout` mutual-exclusion bypass

**This package jumps the dispatch queue ahead of ciu-P20 through ciu-P27**, same
operator priority decision as ciu-P28 (see that handoff's opening note). This one
targets a SEPARATE, silent-wrong-production-deploy defect: an abbreviated CLI flag
bypasses `--layout`'s mutual-exclusion guard, silently overriding a carefully-planned
per-host bundle deployment with one CLI `--profile` value on EVERY host in the layout.

## Context to read first

1. `src/ciu/cli.py`'s `up --layout` block in full (search for `_LAYOUT_FORBIDDEN` —
   read the surrounding ~50 lines including the comment naming the ORIGINAL
   checkpoint-C fix this bug sits right next to: an empty `bundles=[]` list being
   silently accepted, and the `--profile=core` equals-form check that fix already
   added — your job is closing the SAME bug class one level further, not
   re-deriving that fix).
2. `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` is NOT relevant here — this is a hotfix to
   shipped v7.x behavior, not V8 groundwork; do not conflate the two.
3. The remote parser this pushes into: find where `deploy.py`'s argument parser is
   constructed (read-only — `deploy.py` is `scope.forbid`) to confirm its
   `allow_abbrev` default (Python's `argparse.ArgumentParser` defaults to
   `allow_abbrev=True` unless explicitly disabled — confirm whether this one
   disables it; if it already does, the vulnerability is narrower than described
   and you should say so precisely in the LOG rather than assume the review's
   framing is exactly right).
4. `tests/tests/test_ciu_cli_layouts.py` — the existing test for the checkpoint-C
   equals-form fix (search for `--profile=` or similar) — mirror its fixture style.

## Work

1. Fix O1 (abbreviation-proof mutual exclusion).
2. Fix O2 (equals-form dispatch).
3. Tests per O3.
4. Docs per O4.

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P29-hotfix-layout-mutex-bypass-LOG.md`, commit, exit.
