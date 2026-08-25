---
schema_version: 1
id: ciu-P13-exit-on-doc-drift-and-env-scrub
project: ciu
component: docs+test-infra
title: "Repair doc/comment drift left by the exit_on migration (SPEC.md, DESIGN-NOTES.md, governance.py comment), fix a doubled-word operator message typo, normalize the [S10.7]->[S10.6] tag mismatch, add a CIU_*/identity env-scrubbing autouse test fixture, and land QOL-9's one missing README paragraph"
tier: implement-1
input_revision: "64d7e359d99377a58772367286c053b047980276"
source: {kind: review-finding, ref: "ciu-P12 adversarial review, findings 1-6 (residual, non-blocking) + docs/BACKLOG-2026-08-24.md#CIU-QOL-9"}
stack: none
depends_on: [ciu-P12-warn-policy-test-repair]
session: fresh
scope:
  touch:
    - "docs/SPEC.md"
    - "docs/DESIGN-NOTES.md"
    - "src/ciu/governance.py"
    - "src/ciu/deploy.py"
    - "src/ciu/warn_policy.py"
    - "tests/conftest.py"
    - "README.md"
    - "nyxloom-trove/reports/ciu-P13-exit-on-doc-drift-and-env-scrub-LOG.md"
  forbid:
    - "tests/tests/test_ciu_warn_policy.py"
    - "tests/tests/test_ciu_deploy_actions.py"
    - "docs/CONFIG.md"
    - "docs/CONSUMERS.md"
oracles:
  - id: O1-spec-drift-fixed
    observable: "docs/SPEC.md's S15.16 prose (currently ~lines 2176-2179) no longer claims the finding is 'by default also raised' and no longer names the withdrawn CIU_WARNINGS_AS_ERRORS=0 as the softening lever. It correctly states: warn_policy.warn_or_raise (S10.6) always prints [WARN]; by DEFAULT (exit_on=ERROR) it does NOT raise; ciu.exit_on=\"WARN\" makes it raise (fail-fast); \"NEVER\" suppresses even the WARN-level abort entirely for this and every other S10.6 site."
    negative: "prose that still describes 'default also raised' or names CIU_WARNINGS_AS_ERRORS anywhere in SPEC.md"
    gate: "tester-unified"
  - id: O2-design-notes-drift-fixed
    observable: "docs/DESIGN-NOTES.md's D6 section (currently ~lines 300-301) no longer names CIU_WARNINGS_AS_ERRORS as the S10.6 mechanism's env var; it names CIU_EXIT_ON and the closed WARN/ERROR/NEVER vocabulary, or is annotated as historical/superseded if the surrounding narrative reads better that way (your call — this is a design-notes journal, not a live contract; do not rewrite the surrounding survey reasoning, only the stale mechanism name)."
    negative: "CIU_WARNINGS_AS_ERRORS or a boolean framing left anywhere in the D6 section"
    gate: "tester-unified"
  - id: O3-governance-comment-fixed
    observable: "src/ciu/governance.py:155's comment listing ambient CIU_* toggles no longer includes CIU_WARNINGS_AS_ERRORS (withdrawn); replace it with CIU_EXIT_ON in that list (the current ambient-toggle example for this exact mechanism) so the comment's own point — 'usable directly, like every other ambient CIU_* toggle' — stays accurate."
    negative: "CIU_WARNINGS_AS_ERRORS left in this comment"
    gate: "tester-unified"
  - id: O4-typo-fixed
    observable: "The operator-facing [S15.16] message built in src/ciu/deploy.py (~lines 1144-1147) renders 'Set ciu.exit_on = \"WARN\" ...' exactly once, not the current doubled 'Set Set ciu.exit_on = ...'."
    negative: "any doubled word remaining in the rendered message; changing the message's other wording beyond removing the duplicate"
    gate: "tester-unified"
  - id: O5-tag-normalized
    observable: "src/ciu/warn_policy.py's _validate_exit_on raises with tag [S10.6] (not [S10.7] — docs/SPEC.md has no S10.7 heading; S10.6 is the actual documented heading for this entire mechanism, so the code's tag is corrected to match the doc, not the reverse). Both existing tests in tests/tests/test_ciu_warn_policy.py that assert on the tag text continue to pass unmodified — read them first (do not edit test files, they are scope.forbid; if changing the tag would require editing a forbidden test file, STOP per BLOCKED rule and report the exact assertion)."
    negative: "an invented new S10.7 doc heading instead of correcting the code tag; a forbidden test file edited"
    gate: "tester-unified"
  - id: O6-env-scrub-fixture
    observable: "tests/conftest.py exists (create it if absent) with an autouse, session- or function-scoped fixture that monkeypatch.delenv's (raising=False) the ambient identity/policy variables this devcontainer's shell carries from an unrelated checkout: REPO_ROOT, PHYSICAL_REPO_ROOT, REPO_NAME, INSTANCE_ID, DOCKER_NETWORK_INTERNAL, PUBLIC_FQDN, CIU_EXIT_ON. After adding it, `.venv/bin/python run-ciu-tests.py` run WITHOUT the manual `env -u ...` prefix (i.e. with those six variables still ambiently set in the shell) passes at 100% line+branch with zero failures — this is the actual proof the fixture works; run it both ways and report both outputs in the LOG."
    negative: "a fixture using os.environ manipulation directly instead of monkeypatch (leaks across test failures since monkeypatch auto-restores, plain os.environ mutation does not); a fixture that only clears SOME of the six; the manual env -u workaround still being required for a green run"
    gate: "tester-unified"
  - id: O7-readme-worktree-budget
    observable: "README.md's worktree section (currently lines ~11 and ~117-127 discuss worktree verbs/config) gains one paragraph naming ciu.worktree.max_concurrent_instances (default: unlimited/no cap), pointing a reader at docs/CONFIG.md's existing table entry and docs/CONSUMERS.md's existing worked example (both already document this — DO NOT duplicate their content here, just make it discoverable from the README the way every other worktree capability already is)."
    negative: "a new full explanation duplicating CONFIG.md/CONSUMERS.md instead of a short pointer; editing CONFIG.md or CONSUMERS.md (both are scope.forbid — they are already correct)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "fixing O5's tag requires editing a file in scope.forbid (a test asserting [S10.7] literally) — BLOCKED naming the exact file:line, do not edit the forbidden test"
  - "docs/CONFIG.md or docs/CONSUMERS.md turn out to be missing the max_concurrent_instances content this handoff assumes already exists — BLOCKED naming what's actually missing (re-verify docs/CONFIG.md line ~175 and docs/CONSUMERS.md line ~17 before starting; if absent, this handoff's premise is wrong)"
mutexes: [merge-lane]
review_focus:
  - "the env-scrub fixture (O6) must not mask a REAL future test failure that depends on one of these vars being deliberately set BY a test via its own monkeypatch.setenv — confirm the fixture runs BEFORE each test body (function-scoped autouse, or session-scoped clearing only the initial ambient value) and that per-test monkeypatch.setenv calls still work normally afterward (monkeypatch fixtures compose; a function-scoped delenv fixture and a test's own setenv in the same test both apply, in fixture-then-test-body order)"
  - "confirm O5's retag doesn't change any OTHER visible string a consumer might have started grepping for since 51d5d4f7 shipped (unlikely this fast, but check CHANGES.md's own text for [S10.7] mentions before assuming it's purely internal)"
---

# ciu-P13 — exit_on migration doc/comment drift + test-env hygiene + QOL-9

## Context to read first

1. `nyxloom-trove/reports/ciu-P12-warn-policy-test-repair-LOG.md` — the prior package; findings
   1-6 in its accompanying review are what this package repairs (this handoff's oracles ARE
   those findings, restated as a contract).
2. `docs/SPEC.md:1086-1108` (the CURRENT, correct S10.6 section — use this as the template for
   how S15.16's cross-reference at `:2170-2190` should read) and `docs/SPEC.md:2170-2190` (the
   stale cross-reference to fix).
3. `docs/DESIGN-NOTES.md:295-323` (the whole D6 section) — read the surrounding survey table in
   full before touching the one stale row; this is historical design reasoning, not a live spec,
   so the fix is narrow (the mechanism NAME, not the reasoning).
4. `src/ciu/governance.py:150-165` — the comment block listing ambient `CIU_*` toggles.
5. `src/ciu/deploy.py:1127-1150` — the `[S15.16]` message string with the doubled "Set Set".
6. `src/ciu/warn_policy.py:59-66` (`_validate_exit_on`) and `tests/tests/test_ciu_warn_policy.py`
   (READ-ONLY, it's `scope.forbid`) — confirm exactly what string the two raise-path tests assert
   before retagging, so you know whether they check `"[S10.7]"` literally or a looser match.
7. `docs/CONFIG.md` (search `max_concurrent_instances`) and `docs/CONSUMERS.md` (search the same)
   — confirm both already document this (per the wave's own grounding pass); `README.md`'s
   worktree section (~lines 11, 117-127) — confirm it currently does NOT mention the cap.
8. `tests/` directory root — confirm whether `tests/conftest.py` currently exists at all (it did
   not as of the wave's grounding pass) before deciding create-vs-edit for O6.

## Work

1. Fix `docs/SPEC.md`'s S15.16 cross-reference per O1.
2. Fix `docs/DESIGN-NOTES.md`'s D6 row per O2.
3. Fix `src/ciu/governance.py`'s comment per O3.
4. Fix the doubled "Set Set" in `src/ciu/deploy.py` per O4.
5. Retag `[S10.7]` -> `[S10.6]` in `src/ciu/warn_policy.py` per O5 (read the forbidden test file
   first to confirm this doesn't break it; if it does, BLOCKED — do not edit the test).
6. Add the env-scrubbing autouse fixture to `tests/conftest.py` per O6. Prove it works by running
   the gate BOTH with and without the manual `env -u ...` prefix and pasting both outputs in the
   LOG.
7. Add the one README paragraph per O7.
8. Update `docs/BACKLOG-2026-08-24.md`'s QOL-9 row to ✅ IMPLEMENTED with a one-line evidence
   pointer (this file is not in `scope.touch` above — add it; it was an oversight, treat it as
   included).

## Environment setup

Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu` (existing branch
`feat/ciu-qol-v8prep-wave`, existing venv at `.venv/`). Iteration signal, BEFORE O6 lands (use
the manual unset, matching prior packages):

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN \
  .venv/bin/python run-ciu-tests.py
```

AFTER O6 lands, additionally prove the fixture works by running the SAME command with none of
those variables unset (i.e. drop the whole `env -u ...` prefix) and confirming it is now ALSO
green. Paste both runs' tail output in the LOG.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a file outside `scope.touch`,
STOP: write `BLOCKED: <mechanical reason>` to
`nyxloom-trove/reports/ciu-P13-exit-on-doc-drift-and-env-scrub-LOG.md`, commit, and exit.
