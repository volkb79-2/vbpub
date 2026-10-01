---
kind: backlog-entry
schema_version: 1
id: NL-25
title: "gate assert vocabulary lacks a truthful coverage-floor value for whole-project gates"
status: open
type: "bugfix"
severity: "medium"
provenance: "vbpub RG-55 P6 doc-lint finding, 2026-09-30"
priority: 2
filed_date: "2026-09-30"
---

## Observed mechanism and reproduction

`nyxloom lint` rejects `coverage-floor` in `[gates.coverage].asserts` because
the closed vocabulary currently accepts only `tests-pass`,
`changed-line-coverage`, `mutation`, `canary-verified`, and `assay-verdict`.
The cgroup-profiler consumer declares `asserts = ["tests-pass",
"coverage-floor"]` for its registered full-project 100% line-and-branch
coverage gate, so the project cannot truthfully declare the evidence its gate
actually supplies. Reproduction in `scripts/cgroup-profiler/`:

```text
nyxloom lint
nyxloom.toml: gates.coverage.asserts.1: 'coverage-floor' is not one of
['tests-pass', 'changed-line-coverage', 'mutation', 'canary-verified',
'assay-verdict']
```

Calling this `changed-line-coverage` is not a valid workaround: that value
means changed-line coverage, while this gate measures the whole configured
target and checks both line and branch floors. Silently accepting an
unsupported value or mapping it to another assertion would misstate the
consumer's evidence.

## Why nyxloom owns it

Nyxloom owns the schema, config model, validation, and presentation of the
closed `[gates.*].asserts` vocabulary. The consumer's gate is functioning; the
declared-evidence interface is too narrow.

## Proposed contract

Add a distinct, documented `coverage-floor` assertion for a gate that enforces
its configured coverage floor. Keep it semantically distinct from
`changed-line-coverage`; do not imply that every such floor is whole-project
coverage, or that it includes branches, unless the consumer's gate contract
says so. Validation, config comments, schema, docs, CLI/rendered summaries,
and rigor-routing consumers must preserve the declared assertion without
rewriting it or silently treating it as a different assertion. If any
downstream policy needs finer distinctions (changed lines vs whole target,
line vs branch), model those explicitly rather than overloading this value.

## Oracles

- A config declaring `coverage-floor` passes schema validation and `nyxloom
  lint`; an unknown assertion still fails with the offending path and value.
- The shipped config model, documentation, CLI/rendered gate summary, and any
  review-depth/routing logic all recognize `coverage-floor` as its own value.
- `changed-line-coverage` retains its existing meaning and behavior; tests
  prove it is not substituted for or aliased to `coverage-floor`.
- A real consumer config with a full-project line-and-branch floor lints
  without changing that project's gate configuration or weakening its gate.

## SPEC ownership

`src/nyxloom/schemas/nyxloom-config.schema.json`, the gate-assert model and
consumers under `src/nyxloom/`, `reference/STANDARD.md`, and the user-facing
configuration/gate documentation. Update all mirrors and tests in the same
change.

## Updates
