# Changelog — Augeaswrap

## [Unreleased]

### Fixed
- fix(augeaswrap): exclude non-executable modules from whole-target judge coverage
- fix(augeaswrap): cover transaction fingerprints and unit parser/action edge cases

<!-- cmru: release history -->

## [0.2.0] - 2026-11-09
<!-- cmru: generated -->
<!-- cmru: source-end=9e3877cb5b0a631b537decf97691fa7d25f8aae4 -->

### Added
- feat(augeaswrap): add pam_env overlay lens (6fdfce7eb)
- feat(augeaswrap): add HC-3 edit kind and fstab helpers (494f162ec)
- feat(augeaswrap): add JSON key and pending journal (1d6cbf78d)
- feat(augeaswrap): add layered host resource kinds (cf6412bff)
- feat(augeaswrap): add systemd unit resource (d83f0b948)
- feat(augeaswrap): kind registry and generic revert hooks (file and check become registry entries) (c54c3224c)
- feat(augeaswrap): add guarded transaction engine (67780c11e)
- feat(augeaswrap): freeze guarded resource protocol (eb9d3e77a)

### Fixed
- fix(augeaswrap): align pam_env escapes and accepted records (49a33f682)
- fix(augeaswrap): match live Augeas and systemd behavior (c62372a00)
- fix(augeaswrap): refuse invalid internal edit states (48d2749e6)
- fix(augeaswrap): resolve HC-3 review blockers (0e34b2089)
- fix(augeaswrap): resolve HC-2 review blockers (ae7f41fa4)
- fix(augeaswrap): reconcile layered resources and HC-1b follow-ups (ce636309e)
- fix(augeaswrap): stage source Exec targets for unit verification (9166d2319)
- fix(augeaswrap): correct sysusers membership actions (eb1ce1ac4)
- fix(augeaswrap): address HC-1b-u re-review findings (bb671d9e1)
- fix(augeaswrap): address HC-1b-s blockers (db086a683)
- fix(augeaswrap): route drop-in runtime actions once (e6b3dede4)
- fix(augeaswrap): close remaining unit branch gaps (72ac45971)
- fix(augeaswrap): harden systemd unit transactions (048b2087d)
- fix(augeaswrap): close layered review edge cases (b876a36dd)
- fix(augeaswrap): preserve opaque unit preimages (0e5d5b222)
- fix(augeaswrap): close engine review follow-ups (044412898)
- fix(augeaswrap): import plan for apply(Request|mapping|path); test every apply input type and gate undefined names (5755bee02)
- fix(augeaswrap): import the revert precondition check, repair four failing tests (e3114733b)
- fix(augeaswrap): keep the frozen /etc/environment marker text (AW-0 ruling) (56a7be29d)

### Changed
- Fix fstab option comma injection (9026eb8e1)
- Fix docs sync assertion for action status (3b34207d4)
- Fix reviewed sysusers and endpoint edge cases (94e624591)
- Strengthen sysctl native order fixture (db161c5f2)
- Finish resource coverage assertions (8c32c2e09)
- Close layered resource coverage gaps (fc6a7f12e)
- Fix layered shadow checks and revert post-images (76c852dbf)

### Documentation
- docs(augeaswrap): record live smoke outcomes (fe3a68a2b)
- docs(augeaswrap): cite the endpoint publish call accurately (c930c9354)
- docs(augeaswrap): file AW-BL-20, AW-BL-21 (released wheel ships endpoints only vs docs, unjournaled cgprofiled endpoint publish) (a1bd3d794)
- docs(backlog): correct stale line cites, blank line, AW-BL-04 open items (review findings 1-7) (1bf488a12)
- docs(augeaswrap): document unit safety and reconciliation (96f4efd77)
- docs(augeaswrap): file AW-BL-01..04 (HC-1a' review 2 R2-R4, HC-1b-s design preferences) (aa4eacf5c)
- docs(augeaswrap): document systemd unit behavior (8719680d9)
- docs(augeaswrap): additive SPEC addendum for the kind registry and the revert contract (ba6a9d462)

### Testing
- test(augeaswrap): match pam_env edit trailing newline (a4af8418b)
- test(augeaswrap): preserve pam_env fixture separator (0f8d4ccae)
- test(augeaswrap): sync docs checks with live behavior (bdf9a14c6)
- test(augeaswrap): cover empty systemd state properties (6b993eeb0)
- test(augeaswrap): prove HC-2 guard directions (36194a5e6)
- test(augeaswrap): finish HC-2 coverage branches (43fdbae2d)
- test(augeaswrap): cover HC-2 refusal branches (6399cfa99)
- test(augeaswrap): cover post-write path revalidation (e58756aa6)
- test(augeaswrap): cover remaining safety branches (96dc1e203)
- test(augeaswrap): cover unit safety branches (5882ec403)
- test(augeaswrap): kill the review survivors; journal interrupts, wedged dirs; docs, CHANGES, BACKLOG (6bcb57ae7)
- test(augeaswrap): cover the guarded engine to 100% line and branch; drop dead defensive code (de64f12a3)

<!-- cmru: pending release notes begin -->
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
  byte-and-metadata revert. Add pure fstab mount and swap record helpers. In
  response to v1001 live smoke findings, handle numeric Fstab labels and quoted
  values, encode `name=value` options as an `opt/value` child, capture
  post-save lens diagnostics, bound record reconciliation, and preserve
  findmnt's stdout findings on refusal. Real `augtool` fixture tests now run in
  r0-r1, whose tool preflight requires `augeas-tools`. AW-BL-24's fixed-branch
  host rerun remains owed. `journal --pending` recognizes systemd 257.13's
  successful `LoadState=not-found` response while preserving operational
  failures. Docker `--validate` limits found on versions 29.9.3 and 26.1.5 are
  documented; no second validator was added. See `BACKLOG.md` AW-BL-22..24.
  Implementation commit: `c62372a0005295159308fffc9a44b1b17cfc9483`.
- Add the original `pam_env.aug` overlay lens and its `augparse` fixture for
  `/etc/security/pam_env.conf` `DEFAULT`/`OVERRIDE` records and simple
  `/etc/environment` records, including unquoted equals-bearing and empty
  values. Add pure environment and pam_env helpers with quote-aware values,
  Linux-PAM escape validation for raw `pam_env.conf` expressions, hostile-input
  guards, hand-built mapping validation, duplicate-name refusal, and fresh
  per-item staged observations. Record observation decodes Augeas' quoted
  leaf output and recognizes the numeric sequence labels real `augtool` prints
  when appending or replacing a row. The `export NAME=VALUE` and
  logical-continuation compatibility gaps are tracked as AW-BL-25; the live
  libpam acceptance smoke is AW-BL-26.
<!-- cmru: pending release notes end -->

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
