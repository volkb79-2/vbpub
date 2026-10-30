# patchseries

`patchseries` is the internal, stdlib-only library shared by consumers that
keep one Git patch per logical change. It parses text, rename-only, and
mode-only Git patches with DEP-3-style metadata; checks required headers,
dependency links, and application order; selects a change's `Requires`
closure; renders capability and role inventories; compares versions by
`Change-Id`; and computes a date-independent digest from ordered patch bytes
and the pinned upstream commit. Binary patches are refused with a specific
error.
The digest inputs are explained in the [identity design](DESIGN-GUIDE.md#identity-across-versions).

The package is embedded from this repository by its consumers. It is not a
separately published product. See the [design guide](DESIGN-GUIDE.md#header-contract)
for the metadata decisions and the [consumer guide](CONSUMERS.md#check-a-series)
for command and Python examples.

The source's registered `r0-r1` lane runs in `tester-unified` and requires
100% line and branch coverage. See the [design guide](DESIGN-GUIDE.md#patch-shape-and-application-order)
for parser and dependency rules, and the [consumer guide](CONSUMERS.md#series-digest)
for the digest API.
