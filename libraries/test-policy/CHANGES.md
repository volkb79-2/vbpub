# Changelog

## [Unreleased]

- New library `libraries/test-policy`: the `estate_test_policy` pytest plugin (fixed sleeps, sub-120 s timeouts,
  wall-time assertions, thread/fd/child leaks; allowlist only through `estate_allow` with reason and backlog id)
  and the `flakehunt` stress-repeat runner (slow-cpu, cpu-contended, slow-disk, mem-pressure, combo; container
  and inner modes; dry run). Ledger of known flakes with root causes in `docs/FLAKE-LEDGER.md`. No release
  contract yet (TP-01).
