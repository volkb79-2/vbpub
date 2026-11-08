# Augeaswrap backlog

- HC-1b-s: sysctl, sysusers/groups, tmpfiles, endpoint adapter, and effective
  order/shadow checks against the frozen resource protocol.
- HC-2: JSON key updates and pending-action journal queries.
- HC-3: Augeas-backed in-place edits, format validators, and fstab helpers.
- HC-4: the `pam_env` overlay lens and its `augparse` fixture.

Known limits of the HC-1a' engine, kept simple on purpose (also listed in SPEC,
"Adding a resource kind"):

- A journal is bound to the exact `--root` string it was written under; an
  installer that applies under `/target` cannot list or revert from `/` later.
  By design; record the root as informational only if an installer needs it.
- `mode` may carry setuid, setgid or sticky bits, and neither a request nor an
  observed file has a size cap. Both are the caller's responsibility.
- No lock timeout: a second `apply` waits for the first. Acceptable for a
  host-level writer lock; add `--lock-timeout` if a caller needs it.
- Directories created for a new file are not journaled and revert leaves them;
  a revert counts as a later transaction, so earlier ones touching the same
  address cannot then be reverted.
- A hard kill (SIGKILL, power loss) between commits leaves the journal at
  `applying`, which `revert` refuses; only `KeyboardInterrupt`, `SystemExit` and
  `SIGTERM` are journaled as `interrupted`.
- A drop-in whose base unit is missing from all selected-root systemd search
  directories skips staged `systemd-analyze verify`; the plan states the reason
  and exec/preflight checks still run. A missing `systemd-analyze` binary also
  skips only syntax verification and is stated in the plan. See SPEC for the
  staged search tree and dependency closure.
- Unit directive shadowing is not implemented in this wave.
  `augeaswrap.unit.shadow_check` is a documented no-op seam for the shared
  HC-1b-s effective-order implementation.

## HC-1b-u follow-ups (2026-11-08)

- **AW-BL-05 (acceptance, R2/VM): real systemd smoke.** The fake-analyzer unit
  test asserts that staged `Exec*=` paths are empty mode-0755 files, but it
  does not exercise systemd's real parser. On the R2/VM lane, run
  `systemd-analyze --root=<scratch-root> verify` against a disposable staged
  tree with an Exec target stub, and confirm an `is-enabled` probe for a
  not-yet-created unit returns the expected empty-output/non-zero form. Never
  point the analyzer at the cockpit's live root. This acceptance smoke was
  documented but not run in the HC-1b-u fix round.
- **AW-BL-06 (compatibility): full systemd directive parsing.** Exec scanning is
  physical-line based and does not join backslash continuations or model reset
  directives. The limitation is documented in SPEC; add parser-compatibility
  fixtures if the scanner's contract is expanded beyond physical lines.

## HC-1a' second review follow-ups (2026-11-08)

Source: `/home/vscode/vbpub-release-program-20261027/reports/hc-1a2-REVIEW2.md`
(verdict MERGE; items R2-R4 are not defects on the evidence path; R1, the kind-name
validation in `register_kind`, is in the HC-1b-s brief and is not filed here). IDs
are `AW-BL-NN`, allocated in this file.

- **AW-BL-01 (minor, documentation): a stuck `applying` journal also blocks the
  revert of earlier transactions on the same addresses.** After a hard kill the
  journal stays `applying`; `revert` refuses it, and `_later_transaction` treats
  `applying` as touching its addresses, so earlier transactions on those
  addresses cannot be reverted either, until the stuck one is pruned (50
  transactions). SPEC and this file say only that revert refuses the `applying`
  journal. Add that sentence to the SPEC section on revert and to the hard-kill
  limit above; the pre-images stay on disk for a manual restore. Close when both
  texts carry it.
- **AW-BL-02 (nit): window in the interrupt handler.** `fail()` indexes
  `steps[step_index]` (`transactions.py:381`, `:392`); an interrupt that lands
  after the last `finish_step()` has set `step_index` to `len(steps)` and before
  the journal status becomes `done` would raise `IndexError` inside the
  `except BaseException` block, masking the interruption and leaving `applying`
  (the same effect as a hard kill, a few bytecodes wide, reasoned but not
  reproduced). Clamp the index (`min(step_index, len(steps) - 1)`) and cover it
  with a test that interrupts after the last step. Close when the test passes.
- **AW-BL-03 (nit): `_prepare_revert` indexes `resource.observe(...)[address]`
  unguarded** (`transactions.py:670`). A restore resource that breaks the
  contract (observe without the address) surfaces as a raw `KeyError`. Wrap it
  in the same `ValueError` to `revert.preimage.invalid` path used for the other
  invalid pre-image cases; only a kind author can trigger it. Close when a test
  with a contract-violating fake kind gets the named refusal.
- **AW-BL-04 (design, from the HC-1b-s review, not blocking): two preferences for a
  later design pass.** The reviewer prefers making the revert image projection hooks
  mandatory for every revertible kind, and running the endpoint publication as an
  in-process post-action instead of a child process. Also open: add an acceptance
  check for native fixed-GID collision handling (a fixed-GID `sysusers` resource
  against an existing group) to the HC-1b-s branch backlog, which currently carries
  only the live-trixie probe item; and the hc-1a2 review-2 R1 item (kind-name
  validation in `register_kind`) is delegated to the HC-1b-s brief, so re-check it
  there. Source: `reports/hc-1b-s-REPORT.md`, `reports/hc-1a2-REVIEW2.md`. Close when each is
  decided and recorded in SPEC, or struck out with a reason.
