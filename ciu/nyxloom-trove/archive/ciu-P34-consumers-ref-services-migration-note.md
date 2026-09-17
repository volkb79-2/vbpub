---
schema_version: 1
id: ciu-P34-consumers-ref-services-migration-note
project: ciu
component: docs
title: "CONSUMERS.md gains a new numbered section (#16, following the existing 1-15 sequence) showing how to MIGRATE a hand-maintained internal_host override (dstdns's dstdns-mstest pattern, named in CIU-49's own filed text) to the native S16.1a --shared-infra-ref-services mechanism CIU-52 shipped -- a real adoption-ease gap: CONFIG.md documents the mechanism for a NEW adopter (worked example at CONFIG.md:899-954) but nothing frames it as a migration for an EXISTING hand-rolled override, which is the exact shape of the friction dstdns reported"
tier: implement-1
input_revision: "9b731c78"
source: {kind: operator-report, ref: "dstdns adoption-ease suggestion relayed 2026-08-25: 'a small package replacing dstdns-mstest's hand-rolled internal_host override with CIU-52's native shared_infra.ref_services' -- operator confirmed 'yes' to carving this"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P34-consumers-ref-services-migration-note-LOG.md"
  forbid:
    - "src/ciu/*.py"
    - "tests/tests/*.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/DESIGN-GUIDE.md"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-new-section-shows-a-real-before-after
    observable: "CONSUMERS.md gains one new numbered section (use the next free number after the existing highest, `## 15. Adopt a shipped hook template...` -- verify it's still 15 and this becomes 16, don't assume). It shows: (a) a BEFORE snippet -- a hand-maintained, hand-typed [topology.services.<alias>] internal_host value pointing at a reference instance's container (the exact CIU-49-filed dstdns-mstest pattern: `internal_host = \"dstdns-mstest-f2d1cb-vault\"  # instance config: scoped (GUIDE 3.6)`, quoted verbatim from KNOWN_ISSUES_TODO_BACKLOG.md's CIU-49 entry or CONFIG.md wherever it's already quoted -- re-find the exact text, do not paraphrase it from memory); (b) the AFTER: the equivalent `--shared-infra-ref-services vault` invocation plus the resulting CIU-derived [topology.services.vault] block, cross-referencing CONFIG.md's existing worked example (`#### --shared-infra-ref-services -- addressing the reference's services [S16.1a]`, around CONFIG.md:942) rather than duplicating its full mechanism explanation; (c) an explicit callout of what the migration BUYS the consumer: the hand-typed value goes stale if the reference instance is ever re-created under a new identity (a new INSTANCE_ID/network), while `ref_services` re-derives and re-authenticates it at every add and every join -- name this concretely, not abstractly."
    negative: "a section that only shows the AFTER (the CONFIG.md content already covers that) without ever showing what a hand-rolled BEFORE looks like -- that is the exact gap this package exists to close, not a restatement of what already exists"
    gate: "tester-unified"
  - id: O2-worked-example-is-executed-not-just-prose
    observable: "The AFTER example's CLI invocation and resulting TOML block are PROVEN accurate by actually running them against a real fixture (a throwaway two-instance shared-infra setup, mirroring however CIU-52's own tests construct one -- grep `test_ciu_worktree_shared_infra.py` for the fixture pattern and reuse it, do not hand-construct a fresh one) as part of writing the LOG, with the real command output and real resulting overlay content pasted into the LOG as evidence -- not necessarily reproduced verbatim in CONSUMERS.md itself (docs should stay readable), but the LOG must show the docs example was checked against real CIU behavior, not typed from memory."
    negative: "a CONSUMERS.md worked example that doesn't match the actual current CLI flag name, TOML table name, or derived value shape (a `[ciu.instance.shared_infra.ref_services.<alias>]` typo, a stale CLI flag spelling) -- this is exactly the class of error only running the real thing catches, per this session's own reviewer's standing lesson (P20: 'where no oracle exists -- templates, docs, help text -- execute the artifact')"
    gate: "tester-unified"
  - id: O3-gate-stays-green
    observable: "The full test suite (`.venv/bin/python run-ciu-tests.py`) is still 100% line+branch coverage and fully green after this change -- a docs-only change should not need any source or test file edits at all; if it does, that means the worked example needed a NEW test fixture, which is scope creep beyond this package (escalate rather than add production test files under `scope.forbid`)."
    negative: "adding new test files or touching src/ to make an example \"work\" -- if the example doesn't already work against existing mechanisms and fixtures, that's a signal the docs claim is wrong, not that new code is needed"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the CONFIG.md worked example this section is meant to cross-reference has drifted from CIU-52's actual shipped CLI flag/TOML shape since P31 landed -- BLOCKED naming the exact discrepancy, do not silently write CONSUMERS.md to match either the stale doc or invent a third version"
mutexes: [merge-lane]
review_focus:
  - "confirm the BEFORE snippet is the ACTUAL dstdns-mstest text this backlog already quotes, not a paraphrase -- a fabricated 'realistic-looking' example would undercut the whole point of grounding this in a real reported pain point"
  - "confirm the worked example was actually executed against a real fixture, not just written to look plausible -- ask to see the LOG's pasted real output"
  - "confirm this section does not duplicate CONFIG.md's mechanism explanation -- it should cross-reference, and add ONLY the before/after migration framing CONFIG.md doesn't have"
---

# ciu-P34 — CONSUMERS.md: migrating a hand-rolled `internal_host` override to `ref_services`

## Context to read first

1. `KNOWN_ISSUES_TODO_BACKLOG.md` — search `CIU-49` — the filed text quoting
   dstdns's exact hand-maintained override
   (`internal_host = "dstdns-mstest-f2d1cb-vault"  # instance config: scoped
   (GUIDE 3.6)`) and its disposition (ciu ships no default of its own to
   change; dstdns's own override remains dstdns's own follow-up to remove).
   This package does NOT touch dstdns or remove anything there — it documents
   the CIU-side migration path so dstdns (or any future consumer in the same
   position) can do that removal themselves, correctly, from ciu's own docs.
2. `docs/CONFIG.md` around line 899-954 — the EXISTING S16.1a/CIU-52 worked
   example and mechanism explanation (`--shared-infra-ref-services`,
   `[ciu.instance.shared_infra.ref_services.<alias>]`,
   `[topology.services.<alias>]`). This is the reference material; this
   package cross-references it, does not duplicate it.
3. `docs/CONSUMERS.md` — read section `## 15. Adopt a shipped hook template
   instead of hand-writing one (ciu init --hooks, S19.1)` (ciu-P20) as your
   tone/structure precedent: problem framing, a real command, real output,
   a caveat callout box. Your new section follows the same shape, numbered
   after it.
4. `tests/tests/test_ciu_worktree_shared_infra.py` — find how CIU-52's own
   tests construct a two-instance shared-infra fixture (reference + joiner).
   Reuse the SAME construction pattern (conceptually, in your own throwaway
   manual reproduction for the LOG — you are not adding a test file here,
   `scope.forbid` excludes `tests/tests/*.py`) rather than inventing a new one.

## Definition of done

- CONSUMERS.md section 16 exists, shows a REAL before/after (quoted, not
  paraphrased, hand-rolled override → `--shared-infra-ref-services`
  invocation → resulting native block), and names concretely what staleness
  risk the migration closes.
- The LOG documents that the AFTER example was actually run against a real
  fixture, with real output pasted in as evidence.
- `CHANGES.md` gets a short entry (docs-only, no `!` marker — nothing behavioral
  changes).
- Gate stays green with ZERO source or test file changes. If achieving that
  turns out to be impossible, STOP and report why rather than reaching into
  `scope.forbid`.
