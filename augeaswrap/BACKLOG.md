# Augeaswrap backlog

- HC-1b-u: unit files and drop-ins, exec checks, systemd validation, and
  directive intent/shadow checks against the frozen resource protocol.
- HC-1b-s: sysctl, sysusers/groups, tmpfiles, endpoint adapter, and effective
  order/shadow checks against the frozen resource protocol.
- HC-2: JSON key updates and pending-action journal queries.
- HC-3: Augeas-backed in-place edits, format validators, and fstab helpers.
- HC-4: the `pam_env` overlay lens and its `augparse` fixture. Implemented
  2026-11-09 with `/etc/security/pam_env.conf` `DEFAULT`/`OVERRIDE` records,
  simple `/etc/environment` records, pure helpers and hostile-input guards.
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

## Release and consumer follow-ups (2026-11-09)

IDs here start at AW-BL-20 because the in-flight `hc-1b-u-20261108` branch
defines AW-BL-05..12 against main's AW-BL-05..10 and its reconcile job will
renumber; AW-BL-13..19 are left free for that.

- **AW-BL-20 (release/docs): the released `augeaswrap-v0.1.0` wheel ships only
  the `endpoints` group, but `README.md`, `SPEC.md` and `docs/CONSUMERS.md` on
  main describe `plan`, `apply`, `revert` and `journal`.** Verified on
  2026-11-09: `git ls-tree -r augeaswrap-v0.1.0 -- augeaswrap/src` lists
  `__init__.py`, `__main__.py`, `_fs.py`, `cli.py`, `endpoints.py` and
  `lenses/README.md` only, and `cli.py` there is a thin wrapper around
  `endpoints.build_cli`; the tagged README says the public command is
  `augeaswrap endpoints` with `publish`, `withdraw`, `check`, `show`. Main is 24
  commits past the tag under `augeaswrap/` (the guarded applier, the `file`,
  `check`, `sysctl`, `sysusers`, `group`, `tmpfiles` and `endpoint` resources;
  `CHANGES.md` `[Unreleased]`). An agent installing the wheel on v1001
  (2026-11-08) could use only `augeaswrap endpoints check`. Fix, in order:
  (1) until the next release, mark the applier chapters in README, SPEC and
  CONSUMERS as "unreleased: not in `augeaswrap-v0.1.0`" (a docs test that the
  marker exists while `[Unreleased]` lists the applier); (2) after HC-1b-s and
  the HC-1b-u reconcile merge and the review is closed, release the next
  version (0.2.0: new CLI verbs) with `cmru release augeaswrap`, then remove
  the marker and raise the floor in dependents that need the verbs (cgprofile
  declares `augeaswrap>=0.1.0`, `cgprofile/pyproject.toml:31`). Close when the
  installed wheel's `augeaswrap --help` lists `plan`, `apply`, `revert` and
  `journal`, or the docs say plainly which version provides them.
  Source: `HANDOFF-STATE-20261107.md` (22:25Z, "released augeaswrap 0.1.0 wheel
  lacks plan/apply/revert/journal verbs"); `reports/v1001-cutover-EXEC-LOG.md`.
- **AW-BL-21 (design): `cgprofiled install` publishes the endpoint record
  through `augeaswrap.endpoints` directly, so the publication is not
  journaled and cannot be reverted by `augeaswrap revert`.**
  `cgprofile/src/cgprofile/cli/installer.py:967-977` defines
  `_endpoints_api`, which imports `augeaswrap.endpoints` (the 0.1.0 API);
  `installer.py:1522` calls `publish(...)` and writes
  `/etc/host-endpoints.d/cgprofile.env`; the journaled path is the `endpoint`
  resource kind of the applier (AW-BL-20, unreleased). Uninstall calls
  `withdraw("cgprofile", ...)` (`installer.py:1709`), the only undo. Decide: (a) keep it by design and say
  so in `SPEC.md` ("the `endpoints` verbs are unjournaled; the `endpoint`
  resource is the journaled form"), or (b) move installers to the `endpoint`
  resource once released, so a failed install can be reverted as one
  transaction. Prefer (a) now and revisit with AW-BL-20's release. Close when
  the decision is recorded in SPEC and in `cgprofile/docs/CONSUMERS.md`.
  Source: `HANDOFF-STATE-20261107.md` (22:25Z, "cgprofiled install is not
  journaled").
## HC-2 follow-ups (2026-11-09)

- **AW-BL-22 (acceptance): installed dockerd compatibility.** `json_key`
  validates the canonical staged file through the command-runner seam, and
  R0/R1/R3 use only a fake runner. In a project container or VM, record the
  installed Docker version and prove that its `dockerd --validate
  --config-file <staged-file>` accepts a valid fixture and refuses an invalid
  one. Do not start the daemon or run the probe in the cockpit. Close when the
  tool version and both outcomes are recorded.
- **AW-BL-23 (acceptance): live systemd pending-query semantics.** `journal
  --pending` is covered with exact argv and deterministic command-runner output.
  In a project container or VM, record the systemd version and the installed
  `systemctl show -p ActiveEnterTimestampMonotonic,ActiveState,LoadState <unit>`
  behavior for active, inactive, and missing units. For a missing unit, record
  whether the command succeeds with `LoadState=not-found` and
  `ActiveState=inactive`, or exits non-zero with its exact not-found diagnostic;
  confirm other operational failures remain distinguishable. Do not run the
  probe in the cockpit.

- **AW-BL-24 (acceptance, live/VM smoke):** close the real-tool checks owed by
  HC-3 before the first consumer adopts `edit`. In a disposable Debian trixie
  VM with the recorded `augeas-tools` version, verify whether `-t` accepts
  `Fstab` and/or `Fstab.lns`, and pin the supported spelling in
  `AUGTOOL_TRANSFORM_LENS_SPELLING`; verify that a new Fstab record uses label
  `01` and that ordered/repeated `opt` fields survive `--new`. Run
  `findmnt --verify --tab-file <staged>` for valid syntax with referenced
  devices present and absent; record which absent-device findings refuse the
  plan. Confirm an unchanged tree produces no `.augnew`, and a selected record
  edit preserves comments and unrelated lines. The HC-3 implementation uses a
  fake runner only; this smoke has not been run. Keep it inside the disposable
  VM/native lane, never on the cockpit host. Close with argv, tool version,
  staged bytes, validator output, and the passing live/VM gate evidence.

## HC-4 follow-ups (2026-11-09)

- **AW-BL-25 (compatibility): `pam_env` file variants.** The HC-4 lens supports
  simple `/etc/environment` `NAME=VALUE` records and
  `/etc/security/pam_env.conf` `DEFAULT=`/`OVERRIDE=` records, including
  unquoted equals-bearing and empty values. It deliberately does not yet parse
  the `export NAME=VALUE` prefix accepted by Linux-PAM's environment reader or
  a backslash-joined logical continuation in `pam_env.conf`. Add unambiguous
  branches and augparse fixtures for both forms, then extend the Python helper
  tests for their quoting and normalization behavior. Close when both variants
  parse, round-trip, and can be updated through `Edit`. Separately, helper
  arguments to `pam_env_item` are raw Linux-PAM expressions: `\$`, `\@`, and
  `\\` are preserved and unsupported backslash sequences are refused.
- **AW-BL-26 (acceptance, live/VM smoke): actual `pam_env` application.**
  `tools/lens-check.sh` proves the overlay with the tester image's real
  `augparse` 1.14.1, and unit tests prove the request and staged-script
  contracts. In a disposable Debian trixie VM, record the installed
  `libpam-modules`/Linux-PAM version and use a non-cockpit login-session probe
  to show that generated `/etc/security/pam_env.conf` and `/etc/environment`
  records reach the session environment. Include quoted whitespace, an
  ordinary comma, a `DEFAULT`/`OVERRIDE` pair, and the unsupported variants
  from AW-BL-25 as expected negatives. Never point the probe at the cockpit
  host. Close with tool versions, input bytes, observed environment values,
  expected refusals, and passing VM/native evidence.
