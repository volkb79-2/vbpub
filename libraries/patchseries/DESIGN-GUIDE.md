# patchseries design guide

## Header contract

Each `.patch` is one logical change exported with `git format-patch`. Put all
five required trailers in the commit message before the format-patch `---`
separator:

- `Change-Id`: stable identifier for the same logical change across series
  versions. It is unique within one series.
- `Capability`: one value from the consumer's closed capability vocabulary.
- `Role`: one value from the consumer's closed role vocabulary.
- `Risk`: one value from the consumer's closed risk vocabulary.
- `Requires`: comma-separated `Change-Id` values, or `none` when independent.

`patchseries check` verifies that each trailer is present, every vocabulary
value is known, every requirement resolves, identifiers are unique, and the
dependency graph has no cycles. `delta` compares by `Change-Id`, so keeping an
identifier stable across versions is the consumer's authorship rule; the tool
can detect duplicates and report identity continuity, but cannot infer intent.

## Identity across versions

Patch order is the natural filename order used by the series. A selection is
expanded through `Requires` and returned in that original order. `delta`
reports additions, removals and changed patch bytes by stable `Change-Id`;
`git range-diff` remains the human review tool for the underlying code change.

The series digest uses length-prefixed ordered patch filenames and bytes, the
full upstream commit SHA, and the `patchseries` package version. Build dates
are intentionally excluded so two builds from the same inputs have the same
series identity.

## Diffstat and rendering

The parser derives file paths and insertion/deletion counts from unified diff
hunks. `render` produces separate capability and role tables with one column
per supplied series. This is an inventory, not a judgement that a capability
label or risk assessment is correct; reviewers own those meanings.

## Runtime boundary

The implementation uses only the Python standard library. The package is
embedded from this repository by consumers rather than published separately.
Consumer vocabularies use TOML schema version `1`; the loader refuses an
unversioned or unsupported document.
