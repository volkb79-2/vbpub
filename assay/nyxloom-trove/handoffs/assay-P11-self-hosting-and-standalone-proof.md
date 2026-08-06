---
schema_version: 1
id: assay-P11-self-hosting-and-standalone-proof
project: assay
title: "assay gates itself without circularity, and proves the standalone claim inside its own isolated gate"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P10-mutation-changed-lines]
session: fresh
scope:
  touch:
    - "assay.toml"
    - "nyxloom-trove/nyxloom.toml"
    - "tests/**"
    - "docs/BOOTSTRAP.md"
    - "README.md"
  forbid:
    - "src/assay/**"
oracles:
  - id: O1
    observable: "a test inside the gated suite creates a venv containing ONLY assay (no coverage.py, no pytest-cov, no nyxloom, no ciu), and `assay run --lane <fixture-lane>` inside it renders the expected verdict"
    negative: "assay imports a project it claims independence from, so it is a sub-module wearing a library's name (P90 O1)"
    gate: tester-unified
  - id: O2
    observable: "the fixture suite's expected-verdict assertions are made by pytest and fail if assay is stubbed to return PASS unconditionally -- at least ten distinct assertions break"
    negative: "assay validates its own verdicts, so a universal-PASS bug passes its own gate; the circularity is unbroken"
    gate: tester-unified
  - id: O3
    observable: "`assay.toml` declares exactly ONE gated lane, `tester-unified`; there is no lane whose argv runs bare in the cockpit"
    negative: "a cockpit-runnable lane manufactures the 'green in the interactive cockpit' pathway the estate explicitly rejects as a ship signal"
    gate: tester-unified
  - id: O4
    observable: "`assay verify` against assay's own lane reports KILLED for both canary mechanisms, and its help text plus docs/BOOTSTRAP.md state that this layer is NOT independent of assay"
    negative: "the canary is presented as the proof of assay's correctness, when it uses assay to check assay"
    gate: tester-unified
  - id: O5
    observable: "docs/BOOTSTRAP.md's recipe, followed verbatim on a clean checkout, works: install, `pytest` (proves assay is right, no assay-as-gate involved), then `assay run` (proves the change is covered)"
    negative: "the bootstrap requires a gate that requires a working assay, so a clean checkout cannot be validated at all"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "declaring the tester-unified lane requires rebuilding the shared gate image (A-O02) -- stop and report; a rebuild re-risks ciu/cmru/topos/nyxloom"
  - "the scratch venv cannot be built offline, which would mean a runtime dependency crept in against A-005"
mutexes: []
---

# P11 — self-hosting and the standalone proof

The claim to attack: **is the standalone claim real, and is the self-gating
non-circular?**

## Context to read first

1. `docs/DESIGN-GUIDE.md` §9 — the circularity argument and the two-step
   bootstrap.
2. `reference/TESTING-METHODOLOGY.md` — *"greens from the interactive cockpit are
   explicitly not a ship signal"*, which is why O3 exists.
3. `nyxloom-trove/decisions.md` A-005, A-040, A-041.

## Work

1. `assay.toml` for assay — one lane, `tester-unified`, `rigor = ["R0","R1","R2","R3"]`,
   `fail_under = 100.0`, `allow_excluded = false`, `allow_argv_append = false`.
   assay holds itself to the strictest form of every option it offers.
2. The scratch-venv test (O1) inside the gated suite — this is P90's O1
   discharged mechanically, with no cockpit-green pathway.
3. `docs/BOOTSTRAP.md` — the two ordered steps, and an explicit statement that
   bare `pytest` is a developer convenience and **not** evidence.
4. `README.md` — what assay is, what it is not (point at DESIGN-GUIDE §7), and
   the three-channel contract.

## The ordering that removes the circularity

```
pip install -e .[test]
pytest                      # proves assay is RIGHT   -- independent of assay
assay run --lane tester-unified   # proves this CHANGE is covered -- uses assay
```

They answer different questions, which is why neither depends on the other's
conclusion. assay's own lane argv is `pytest`, so the gate transitively re-runs
its own independent oracle every time.
