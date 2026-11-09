# Changelog — Augeaswrap

## [Unreleased]

- Add the `json_key` resource for duplicate-safe nested JSON updates, canonical
  staged output, optional dockerd validation, exact-byte revert, and declared
  restart actions. Add `augeaswrap journal --pending`, which derives unresolved
  unit restarts from retained journals and systemd's monotonic active-enter
  timestamp.
- Harden the HC-1b-u `unit` resource: enforce normalized selected-root ExecStart
  checks, safe ownership and modes through the managed directory chain, reject
  leading-dash names and NUL/oversize unit text, stage systemd search paths and
  existing dependency units, stub staged Exec targets, and report every verify
  skip in the plan. Require `intent`, wire the documented shadow-check hook,
  reconcile declared enablement, run post-actions in rev3 phases, and journal
  absent-unit stop/disable/delete steps separately. Revert of a new whole unit
  now derives actions from its absent pre-image.
- Require an explicit `PostAction.phase` for unrecognized third-party argv
  forms; custom action callers need to provide a phase. This is a breaking
  validation requirement.
- Resolve live `systemd-sysctl` through its absolute systemd path first and
  report missing tools as `sysctl.native.failed`; refuse leading-dash sysctl
  keys. Validate tmpfiles against its staged file operand. Validate sysusers
  names and GIDs in Python, reject fixed GID collisions before endpoint
  resolution, and refuse native dry-run diagnostics even on exit 0. Guard GID 0
  group removal, use `gpasswd -a/-d <user> -- <group>`, and accept member
  account names with periods or a trailing `$`.
- Pin sysctl layer precedence and exact shadow acceptance, the last matching
  own assignment, leading-dash sysusers rejection, revert mode and owner
  restoration, and symlinked resource targets. Membership-only sysusers
  requests no longer run the native tool on an empty fragment.
- Refuse sysusers membership changes for groups removed in the same request,
  preserve endpoint restore pre-images when a revert commit fails, and clarify
  fake-root endpoint action reporting in the user-facing docs.
- Keep revert post-images in each restore resource's observation shape after
  post-actions, avoid redundant re-observation, and report same-basename masks
  even when the `/etc` file wins. Native sysctl order checks now reject
  unmodeled systemd headers and ignore annotated content comments.
- Recheck layered intent during verification, model `/dev/null` masks and
  `/usr/local/lib` systemd layers, stage tmpfiles account records, and defer
  live endpoint publishing until sysusers actions finish. TOML loaders and
  serializers now refuse interleaved resource kinds that would lose order;
  endpoint revert restores prior fragment bytes without replaying publish.
  Sysusers shadow checks include implicit groups, and sysctl native headers
  accept systemd's annotated `/etc/sysctl.conf` header.
- Add `sysctl`, `sysusers`, `group`, `tmpfiles` and `endpoint` resources to the
  guarded applier. Layered resources require caller intent and check effective
  setting/file order; exact `accept_shadow` pairs become plan notes. Journal
  non-file effects in `irreversible_effects`, resolve `@gid:<group>` from
  earlier fixed-GID sysusers resources or existing groups, and restore managed
  fragment bytes on revert.
- Validate registered kind names before using them as TOML table names, journal
  finalization safely after the last step, and refuse a restore resource that
  omits its address with a clear error. Document the revert effect of a
  hard-killed `applying` transaction.
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
- Add the Augeas-backed `edit` resource with private staged augtool execution,
  guarded duplicate matches, native and argv validators, atomic commit and exact
  byte-and-metadata revert. Add pure fstab mount and swap record helpers. The
  real Augeas 1.14.1 and live `findmnt` smoke remains tracked as AW-BL-24.
- Add the original `pam_env.aug` overlay lens and its `augparse` fixture for
  `/etc/security/pam_env.conf` `DEFAULT`/`OVERRIDE` records and simple
  `/etc/environment` records. Add pure environment and pam_env helpers with
  quote-aware values, hostile-input guards, hand-built mapping validation,
  duplicate-name refusal, and fresh per-item staged observations. Record
  observation now decodes Augeas' quoted leaf output and recognizes the
  numeric sequence labels real `augtool` prints when appending or replacing a
  row. The `export NAME=VALUE` and logical-continuation compatibility gaps are
  tracked as AW-BL-25; the live libpam acceptance smoke is AW-BL-26.

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
