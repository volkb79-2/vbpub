# Shared worktree library

`worktree` is the internal, neutral workspace-instance substrate embedded by
CIU and CMRU. It centralizes Git-family discovery, path identity, durable
records, leases, and safe lifecycle cleanup; product adapters retain their own
root and release policy.

Read the [specification](SPEC.md) for the contract, the
[design guide](DESIGN-GUIDE.md) for the ownership decisions, and
[consumer guide](CONSUMERS.md) for adoption and qualification. The source has
its own tester-unified R0-R3 lane, but it is not a separately released package.

Removal accepts an explicit branch-deletion policy, an optional expected tip
for compare-and-delete, and phase callbacks. CIU uses these mechanics to keep
unmerged refs and to report which cleanup and Git phases completed; the neutral
library does not decide whether a product considers a branch merged or safe.
See the [removal API example](CONSUMERS.md#removal-options-and-phase-events).
