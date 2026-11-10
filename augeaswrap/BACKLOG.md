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

- **AW-BL-22 (closed: native validator limit recorded).** On 2026-11-09, both
  Docker CE `5:29.9.3-1~debian.13~trixie` (Docker 29.9.3, build `70efc0e`)
  and Debian `docker.io 26.1.5+dfsg1-9+deb13u1` (Docker 26.1.5+dfsg1, build
  `411e817`) accepted a valid config and refused an unknown directive and
  malformed JSON using `dockerd --validate --config-file <staged-file>`;
  neither daemon was started. Both returned 0 / `configuration OK` for an
  empty file, duplicate JSON keys, invalid `bip` CIDR, unknown `log-opts`, and
  `hosts=["fd://"]` (the packaged unit-flag conflict appears only at daemon
  start). This confirms the native check is deliberately shallow; no second
  validator is being added. Full untrimmed outcomes:
  `/home/vscode/vbpub-release-program-20261027/reports/aw-bl-22-24-evidence/aw-bl-22-dockerd-validate-docker-ce-29.9.3.out`
  and `/home/vscode/vbpub-release-program-20261027/reports/aw-bl-22-24-evidence/aw-bl-22-dockerd-validate-docker.io-26.1.5.out`.
  Code commit: `c62372a0005295159308fffc9a44b1b17cfc9483`.
- **AW-BL-23 (closed with code change).** On systemd `257 (257.13-1~deb13u1)`,
  `systemctl show -p ActiveEnterTimestampMonotonic,ActiveState,LoadState
  <missing-unit>` exited 0 with stdout `LoadState=not-found`,
  `ActiveState=inactive`, `ActiveEnterTimestampMonotonic=0` and empty stderr.
  `transactions.py` now handles `LoadState=not-found` explicitly and keeps
  the older exact non-zero not-found diagnostic as a compatibility case.
  A distinct operational failure (systemd not running as PID 1 / unavailable
  system bus) exits 1 with its diagnostic and remains `journal.pending.query.failed`.
  Tests pin both response shapes and the `--` separator before the unit.
  Evidence: `/home/vscode/vbpub-release-program-20261027/reports/aw-bl-22-24-evidence/aw-bl-23-systemctl-show.out`.
  Code commit: `c62372a0005295159308fffc9a44b1b17cfc9483`.
- **AW-BL-24 (closed for the behaviors proven by the live smoke; fixed-branch
  live rerun owed).** The v1001 run used Debian 13.7, `augeas-tools`
  `1.14.1-1.1~deb13u1`, `augeas-lenses` `1.14.1-1.1~deb13u1`, `libaugeas0`
  `1.14.1-1.1~deb13u1`, and util-linux `2.41.5-0+deb13u1`. `-t Fstab` and
  `-t Fstab.lns` both work; loaded records are labelled `1`, `2`, `3`, while a
  newly inserted record is addressed as `01[1]` before save. `augtool print`
  quotes values; Fstab `name=value` options use an `opt` node plus `opt/value`.
  The implementation now parses those forms, bounds reconciliation, splits
  `name=value` on write, and prints lens errors after save. Real-tool tests
  cover append, selected-row edit with comment/unrelated-line byte preservation,
  unchanged input without `.augnew`, ordered/repeated options, and `=` options;
  the r0-r1 lane explicitly requires `augtool` and `augparse` and fails if
  either is absent.

  `findmnt --verify --tab-file <staged>` with util-linux 2.41.5 exited 1 for
  absent UUID/LABEL devices and missing target directories even with `nofail`;
  absent `/dev/...` sources and warning-only findings exited 0. Parse errors
  could terminate `findmnt` with signal 11 (shell exit 139; Python return code
  -11). Refusal text now includes stdout findings and stderr summaries. The
  live probe simulated device presence with a stat-only block node and
  udev-style symlinks, not a real filesystem.

  The live smoke proved the observations above, not acceptance of the fixed
  branch. The controller owes a final live rerun on this fixed branch on a
  host, covering the actual `augtool` edit plan/application and `findmnt`
  behavior. The corrected command spelling, tool versions, original staged
  bytes, and full raw outputs are in
  `/home/vscode/vbpub-release-program-20261027/reports/aw-bl-22-24-LIVE-REPORT.md`
  and `/home/vscode/vbpub-release-program-20261027/reports/aw-bl-22-24-evidence/`.
  Code commit: `c62372a0005295159308fffc9a44b1b17cfc9483`.

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

## R2 follow-ups (2026-11-10)

- **AW-BL-27 (closed with config and regression test).** The whole-target R1
  floor refused the docstring-only `src/augeaswrap/formats/__init__.py` target
  because the coverage artifact had no executable lines. Keep judge targets
  limited to package modules with executable AST statements, and require the
  target inventory test to reject both missing executable modules and
  non-executable targets. The R2 fix removes this initializer from the judge
  inventory and adds both checks.
