# srdm backlog

Un-carved ideas. Nothing here is committed to.

- **A Go changed-line coverage gate.** D-007 declines to claim a floor the
  gate does not enforce. `go test -coverprofile` plus a changed-line
  evaluator would let `[gates.unit]` honestly assert
  `changed-line-coverage`.
- **A durability oracle.** D-008: process kill proves ordering, not
  durability. `dm-flakey` or a VM snapshot in the privileged harness would
  close it.
- **A YAML front-end for profile documents** (D-005), once the profile
  engine lands and a parser dependency is worth its weight.
- **`srdm store gc`** — retention (D-002), once there is something that
  pins a release.
- **Manifest streaming.** `BuildManifest` holds every entry in memory. Fine
  for a ~2.4 GB game tree; worth revisiting if a profile ever covers a tree
  with millions of entries.
- **Parallel hashing.** The manifest walk is single-threaded. Measure before
  optimizing: on the case-study node the store lives on the same device the
  io.max caps govern, so more concurrency may buy nothing.
