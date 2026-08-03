// Package providerapi will implement provider protocol v1 — the seam
// between Wings' start-attempt transaction and srdm's generation
// resolution: prepare, commit, abort, release and reconcile, over a unix
// socket, with request-id idempotency, tombstoned leases and typed errors.
//
// Empty, and v2 only. It arrives after the Wings L1/L1b patches land
// (master plan Phase 5), at which point exposure: provider becomes the
// second driver behind the same seam and the cutover from host-bind is a
// config flip, not a migration — the store, the generations and the journal
// are untouched.
//
// The protocol freeze (fake provider, fake Wings driver, golden vectors,
// fault scripts) precedes any code here. That ordering is deliberate: it is
// what keeps the two implementations honest about a contract neither of
// them owns.
package providerapi
