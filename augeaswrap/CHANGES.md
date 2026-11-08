# Changelog — Augeaswrap

## [Unreleased]

- Harden the HC-1b-u `unit` resource: enforce normalized selected-root ExecStart
  checks, safe ownership and modes through the managed directory chain, reject
  leading-dash names and NUL/oversize unit text, stage systemd search paths and
  existing dependency units, and report every verify skip in the plan. Require
  `intent`, wire the documented shadow-check hook, reconcile declared
  enablement, run post-actions in rev3 phases, and journal absent-unit
  stop/disable/delete steps separately. Revert of a new whole unit now derives
  actions from its absent pre-image.
- Add the guarded applier core with strict TOML requests, `file` and `check`
  resources, plan/apply/revert/journal commands, pre-image journals, stale-plan
  checks, and explicit revert.
- Move the shared resource protocol and value types into `augeaswrap`; resource
  work for HC-1b-u/-s builds against that frozen contract.
- Tighten protocol input checks for NUL-bearing writers and non-string change
  actions without changing resource method signatures or dataclass fields.
- Keep the standalone endpoint publisher, its paths and the frozen
  `/etc/environment` marker text unchanged.
- Emit one JSON document per CLI invocation, with plan and result together for
  apply and revert.
- Mark an unobservable failed-commit post-image in the journal and refuse an
  unsafe explicit revert.
- Preserve transaction allocation order for same-second and clock-rollback
  cases; bind each journal to its selected root.
- Record address-bound post-actions and replay recorded actions during revert.
- Add `augeaswrap.kinds`, a registry of resource kinds: a kind registers one
  `KindSpec` (TOML parse, TOML serialize, optional plan rendering, and
  `RevertHooks(snapshot, restore)` or an explicit `revert=None`). The loader,
  `Request.to_toml()`, plan rendering, pre-image storage and revert dispatch
  through it; `file` and `check` are registry entries. A plan prints
  `revert: unsupported` (and `plan --json` gives `"revertible": false`) before
  apply for a kind without hooks. SPEC, "Adding a resource kind", is the contract.
- Fix `apply()` with a `Request`, mapping or path (a missing `plan` import raised
  `NameError`); the test suite now fails on any undefined global name.
- Add the initial `unit` resource for whole systemd unit files and drop-ins;
  the HC-1b-u fix round above defines its current safety, staged verification,
  enablement, ordering, and revert behavior.
- Add generic `PostAction.pending`: a pending action is recorded with status
  `pending` in the journal and exposed in `Result.pending` instead of executed;
  revert replays it as a pending declaration.
- Journal an apply stopped by `KeyboardInterrupt`, `SystemExit` or `SIGTERM` as
  `interrupted` (revertible like a failed apply); `journal list` skips, with a
  warning, a transaction directory that has no journal and it no longer blocks
  reverting earlier transactions.
- An exec `check` is fingerprinted by argv and exit code only, so a check with
  varying output no longer makes every apply stale; validators, exec checks and
  post-actions run with stdin closed; `atomic_write` sets the owner before the
  mode; generated TOML escapes DEL; `apply` refuses a hand-built `Plan` whose
  changes do not match its request; `var` and `var/lib` keep the default mode
  when the state directory is created on a fresh root.
- Removed from the released 0.1.0 surface (no aliases): `augeaswrap.validate_owner`
  (now `augeaswrap.endpoints.validate_owner`) and `augeaswrap.endpoints.build_cli`,
  `build_surface_cli` and `main` (now `augeaswrap.cli`).

<!-- cmru: release history -->

## [0.1.0] - 2026-11-08
<!-- cmru: generated -->
<!-- cmru: source-end=f454c38d8dd64f67eb9caac925961451819ba2c7 -->

### Added
- feat(augeaswrap): add standalone endpoint publisher (8754cacb0)

### Fixed
- fix(augeaswrap): show surface-drift findings in test_docs, trim copied cmru/assay comments, note frozen marker text in SPEC (53e1c55f9)

### Changed
- Merge origin/main into aw-0-20261108 (cgprofile rename, cgprofiled writer text ported to augeaswrap) (0518a3bb8)

<!-- cmru: pending release notes begin -->
- Add the standalone `augeaswrap` project and move the host endpoint record
  API and CLI from cli-extended. The endpoint paths and formats are unchanged.
<!-- cmru: pending release notes end -->
