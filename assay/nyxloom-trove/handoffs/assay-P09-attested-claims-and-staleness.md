---
schema_version: 1
id: assay-P09-attested-claims-and-staleness
project: assay
title: "Tier 3 attested claims: schema, commit binding, staleness detection -- and the Tier 2 slot"
tier: sonnet5-high
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P07-runner-cli-verdict-emission]
session: fresh
scope:
  touch:
    - "src/assay/attest.py"
    - "src/assay/schemas/attestation.schema.json"
    - "src/assay/config.py"
    - "src/assay/verdict.py"
    - "tests/**"
  forbid:
    - "src/assay/evaluate.py"
    - "src/assay/canary.py"
oracles:
  - id: O1
    observable: "an attestation naming a commit other than the commit under test is REFUSED; a lane declaring `attested = [\"adversarial-review\"]` with no attestation present cannot render PASS"
    negative: "a verdict shows R0+R1+R2 green and reads as 'this change is fine' while the only method that could have caught the defect never ran"
    gate: tester-unified
  - id: O2
    observable: "an attestation whose reviewed paths CHANGED between the attested commit and HEAD is marked STALE and does not satisfy its claim; one whose paths are byte-identical satisfies it"
    negative: "a review of an earlier revision is credited against later code -- assay cannot judge a review, but it must be able to prove one is stale"
    gate: tester-unified
  - id: O3
    observable: "every attested claim in the verdict carries `verified_by_assay: false` and its `producer`; there is no code path by which an attestation becomes a computed claim"
    negative: "attested evidence is laundered as verified, which is worse than omitting it because it is now on the record"
    gate: tester-unified
  - id: O4
    observable: "a malformed attestation (missing producer, missing commit, unknown kind, bad shape) yields exit 2 / ERROR / UNREADABLE_ARTIFACT rather than being ignored"
    negative: "an unparseable attestation is silently skipped, so a broken producer reads as an absent requirement"
    gate: tester-unified
  - id: O5
    observable: "`[lanes.X.adjudicated]` parses and validates with zero integrations shipped, and a lane declaring an unknown adjudicator fails at load with BAD_LANE_CONFIG"
    negative: "the schema has no room for Tier 2, so adding the first scanner integration forces a schema version bump across every already-adopted consumer"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "staleness detection requires assay to interpret the CONTENT of an attestation rather than its declared path list -- that crosses into judging, which is forbidden"
mutexes: []
---

# P09 — attested claims and the adjudicated slot

The claim to attack: **can assay require evidence it cannot produce, without
pretending to have verified it?**

## Context to read first

1. `docs/DESIGN-GUIDE.md` §3 — the three tiers, and specifically why Tier 3
   exists: ignoring non-mechanical evidence is the 0/0-is-100% bug at the level
   of the evidence table.
2. `reference/TESTING-METHODOLOGY.md` "Do tests test the right thing?" — the
   adversarial-review row and the line *"a runner cannot infer intent"*.
   Also the retroactive-work rule (commit-addressed queue; pending/passed/
   failed/inconclusive), which is the model an async producer follows.

## Work

1. `schemas/attestation.schema.json` — kind, commit, producer, reviewed_paths,
   findings, timestamp. Shipped as data so producers can validate without
   importing assay.
2. `attest.py` — load, validate, bind to commit, diff `reviewed_paths` between
   the attested commit and HEAD for staleness, fold into `claims[]`.
3. `config.py` — the `attested` list and the `[…adjudicated]` table.
4. `verdict.py` — claim entries carry `source: computed|adjudicated|attested`
   and `verified_by_assay`.

## Explicitly deferred

Any Tier 2 integration (A-034, A-O10): the slot must parse, nothing invokes a
scanner yet. Claim-level `enforcement` and the `PENDING` status for async
producers are noted in A-O08/A-O09; check the schema has room, do not build them.

## The line

assay reads an attestation's declared metadata. It never reads, ranks, or acts
on its findings' content. If this package starts parsing prose, the boundary was
drawn wrong.
