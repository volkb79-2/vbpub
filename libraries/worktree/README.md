# Shared worktree library

`worktree` is the internal, neutral workspace-instance substrate embedded by
CIU and CMRU. It centralizes Git-family discovery, path identity, durable
records, leases, and safe lifecycle cleanup; product adapters retain their own
root and release policy.

Read the [specification](SPEC.md) for the contract, the
[design guide](DESIGN-GUIDE.md) for the ownership decisions, and
[consumer guide](CONSUMERS.md) for adoption and qualification. The source has
its own tester-unified R0-R3 lane, but it is not a separately released package.
The retained integration plan is
[INTEGRATION-PLAN-2026-11-01.md](docs/INTEGRATION-PLAN-2026-11-01.md).

Removal accepts an explicit branch-deletion policy, an optional expected tip
for compare-and-delete, and phase callbacks. CIU uses these mechanics to keep
unmerged refs and to report which cleanup and Git phases completed; the neutral
library does not decide whether a product considers a branch merged or safe.
See the [removal API example](CONSUMERS.md#removal-options-and-phase-events).

Adapters may pass a `checkout_recovery` callback to handle a Git removal that
failed with an explicit permission denial. The library never calls it for
other errors. After it returns success, the library rechecks the captured
checkout inode, linked-worktree admin identity, and registration before it
finishes removal. CIU uses this seam only after exact-branch consent and invokes
its S6.5 helper on verified failed descendants. `finish_workspace_removal`
supports a later invocation when Git already dropped the registration; it
requires a durable adapter checkpoint and the captured admin identity.
`remove_workspace` also accepts an optional `residual_checkout_guard`, called
immediately before its unprivileged Git retry or recursive removal of an
unregistered leftover checkout. Adapters can revalidate checkpointed data
there without adding product policy to the neutral library.
