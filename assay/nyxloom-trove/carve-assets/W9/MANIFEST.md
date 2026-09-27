# W9 — v13 Topos qualification successors

Captured for the Wave C v13 verdict contract. W8 remains the frozen v12
generation; these two files are its P25 qualification successors. Both P25
lanes judge R0/R1 only and carry no mutation payload, so their complete
artifacts change only at `schema_version: 12 -> 13`. B106's native mutation
provenance is covered by the v13 mutation and selective-reuse tests in the
registered gate.

| file | evidence |
|---|---|
| `verdict.schema.v13.json` | byte copy of the shipped v13 schema |
| `expected/p25-pass-v13-template.json` | P25 whole-artifact PASS control |
| `expected/p25-missing-v13-template.json` | P25 whole-artifact `UNCOVERED_LINES` control |
| `test_acceptance_v13.py` | schema identity, byte-copy and template acceptance checks |

The W8 P25 templates remain unedited historical v12 evidence. The
`qualify_topos.py` gate harness compares live v13 artifacts against these W9
templates and separately compares Topos coverage against its locked hand
manifest.
