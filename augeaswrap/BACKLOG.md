# Augeaswrap backlog

- HC-1b-u: unit files and drop-ins, exec checks, systemd validation, and
  directive intent/shadow checks against the frozen resource protocol.
- HC-1b-s: sysctl, sysusers/groups, tmpfiles, endpoint adapter, and effective
  order/shadow checks against the frozen resource protocol.
- HC-2: JSON key updates and pending-action journal queries.
- HC-3: Augeas-backed in-place edits, format validators, and fstab helpers.
- HC-4: the `pam_env` overlay lens and its `augparse` fixture.
- The HC-1b-s review measured the staged tmpfiles operand and the
  `/usr/lib/systemd/systemd-sysctl` location with systemd 257.13. Registered
  gates still use fake command runners; the remaining real-tool acceptance
  work is tracked in AW-BL-10 and must run through a project container or VM.

Known limits of the HC-1a' engine, kept simple on purpose (also listed in SPEC,
"Adding a resource kind"):

- Post-actions follow rev3 phase order: sysusers, tmpfiles, daemon-reload,
  sysctl, runtime, gpasswd, then endpoint; declaration order is retained within
  each phase. An address-bound `before_commit` action runs immediately before
  its resource commit.
- A journal is bound to the exact `--root` string it was written under; an
  installer that applies under `/target` cannot list or revert from `/` later.
  By design; record the root as informational only if an installer needs it.
- `mode` may carry setuid, setgid or sticky bits, and neither a request nor an
  observed file has a size cap. Both are the caller's responsibility.
- No lock timeout: a second `apply` waits for the first. Acceptable for a
  host-level writer lock; add `--lock-timeout` if a caller needs it.
- TOML array tables group resources by kind. The loader and serializer refuse
  interleaved kind tables rather than silently changing request order; keep
  repeated tables of each kind contiguous or use the ordered Python API.
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
- Unit directive shadowing uses the shared layer observer and effective-order
  API. The kind builds one `TargetClass` per unit and includes its observed
  layer files in the stale-plan fingerprint; post-commit verification compares
  managed unit state without treating validation-only layer files as part of
  the committed image.

## HC-1b-u follow-ups (2026-11-08)

- **AW-BL-11 (acceptance, R2/VM): real systemd smoke.** The fake-analyzer unit
  tests assert that staged `Exec*=` paths from both the requested resource and
  existing source units are empty mode-0755 files. The local scratch-root test
  exercises an Environment-only drop-in over a vendor unit with an ExecStart;
  the R2/VM lane must also run real `systemd-analyze
  --root=<scratch-root> verify` for that drop-in case and the broader staged
  tree, plus an `is-enabled` probe for a not-yet-created unit with the expected
  empty-output/non-zero form. Never point the analyzer at the
  cockpit's live root. This acceptance smoke was documented but not run in the
  HC-1b-u fix round.
- **AW-BL-12 (compatibility): full systemd directive parsing.** Exec scanning is
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
  in-process post-action instead of a child process. Decision: keep the current
  optional hooks and argv post-action contract for this wave; changing that
  protocol needs a separate design pass. The fixed-GID collision is now refused
  in Python and covered by a regression test; native acceptance is AW-BL-10.
  The delegated kind-name validation is present and tested. Source:
  `/home/vscode/vbpub-release-program-20261027/reports/hc-1b-s-REVIEW.md`,
  `reports/hc-1a2-REVIEW2.md`.
- **AW-BL-05 (design): a `[[group]]` removal makes the whole transaction
  non-revertible.** The engine rejects revert for all changed resources in that
  transaction, including file and endpoint siblings. This is now stated in
  SPEC and CONSUMERS; consider per-resource revert support or a separate group
  removal transaction. Close when the transaction contract is changed or the
  current boundary is accepted as permanent.
- **AW-BL-06 (accuracy): a foreign sysusers `m` membership line can be reported
  as a shadowing group definition.** This can create a false `shadow.*` finding
  when the membership has no effect on which `g` declaration wins. The current
  parser intentionally treats the line as an implicit group. Revisit the
  analyzer's sysusers-specific distinction and add a fixture. Close when a
  membership-only line no longer shadows a group definition without losing
  systemd's implicit-group behavior.
- **AW-BL-07 (metadata): existing sysctl, sysusers and tmpfiles fragments are
  rewritten as mode 0644 on change.** Revert restores the recorded pre-image
  mode, but an ordinary update does not preserve the installed mode. Decide
  whether updates preserve existing metadata, then test the chosen behavior.
- **AW-BL-08 (diagnostics): malformed foreign sysusers fragments refuse with a
  generic `resource.plan.failed` instead of a structured problem naming the
  source file and line.** Add source attribution while retaining fail-closed
  behavior. Close when a malformed vendor fragment produces a stable named
  problem.
- **AW-BL-09 (native order): `systemd-sysctl --cat-config` parsing accepts
  `/lib/sysctl.d` headers that Python does not list.** On trixie `/lib` points
  into `/usr/lib`; verify whether systemd prints both spellings before deciding
  how to canonicalize the native header sequence. Cover the observed behavior
  in the registered native acceptance lane.
- **AW-BL-10 (acceptance): add a project container or VM probe for native
  `systemd-sysctl --cat-config` ordering, staged `systemd-tmpfiles` operand
  resolution, `systemd-sysusers` diagnostics and fixed-GID collisions, and
  `gpasswd` argv acceptance (`-a/-d <user> -- <group>`). The real-tool proof
  belongs in this native container or VM lane and must never run on the cockpit
  host. Close when the lane records its systemd version and demonstrates the
  staged bytes, GID result, and accepted membership argv.
