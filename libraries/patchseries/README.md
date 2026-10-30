# patchseries

`patchseries` is the internal, stdlib-only library shared by consumers that
keep one Git patch per logical change. It parses DEP-3-style metadata, checks
required headers and dependency links, selects a change's `Requires` closure,
renders capability and role inventories, compares versions by `Change-Id`,
and computes a date-independent digest for an ordered series.

The package is embedded from this repository by its consumers. It is not a
separately published product. See the [design guide](DESIGN-GUIDE.md#header-contract)
for the metadata decisions and the [consumer guide](CONSUMERS.md#check-a-series)
for command and Python examples.

The source's registered `r0-r1` lane runs in `tester-unified` and requires
100% line and branch coverage.
