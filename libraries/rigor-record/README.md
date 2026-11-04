# rigor-record

Stdlib-only interchange layer for the vbpub **rigor-evidence record** (design memo
`RIGOR-EVIDENCE-DESIGN-MEMO-2026-11-05`, package RR-1). It owns:

| Piece | Where |
|---|---|
| Normative specification | [SPEC.md](SPEC.md) |
| JSON Schemas (`rigor-fragment/1`, `rigor-record/1`, `rigor-waivers/1`, shared definitions) | `src/rigor_record/schemas/` |
| Schema and semantic validator | `rigor_record.validate`, `rigor_record.schema` |
| Distribution object, nearest-rank percentiles, normalisation | `rigor_record.stats` |
| Privacy lint | `rigor_record.privacy` |
| Golden fixtures (fragments, one record per R2 status, waivers, percentile vectors) | `golden/` |

Producers (assay, run-gate) do not import this wheel at run time. They vendor the schemas and the
golden fixtures byte-identically into their tests and validate their own output against them.

```python
from rigor_record import validate_fragment, validate_record, lint, distribution

violations = validate_fragment(json.loads(text))      # [] when valid
findings = lint(json.loads(text))                      # [] when privacy-clean
dist = distribution([3.0, 1.0, 2.0], unit="s", source="assay-clock")
```

No CLI exists yet (backlog RRK-01). Gate: `run-gate r0-r1` from the project's worktree (100 % line and
branch coverage).
