# Augeaswrap backlog

- HC-1b-s: sysctl, sysusers/groups, tmpfiles, endpoint adapter, and effective
  order/shadow checks against the frozen resource protocol.
- HC-2: JSON key updates and pending-action journal queries.
- HC-3: Augeas-backed in-place edits, format validators, and fstab helpers.
- HC-4: the `pam_env` overlay lens and its `augparse` fixture.

Known limits of the HC-1a' engine, kept simple on purpose (also listed in SPEC,
"Adding a resource kind"):

- Post-action order is request order. An optional `PostAction.phase` for the
  design's fixed order (sysusers, tmpfiles, daemon-reload, sysctl, enable,
  gpasswd, endpoint) is decided when HC-1b freezes its resources.
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
- A drop-in for a host-only distro unit (base fragment under
  `/usr/lib/systemd/system` or another lower-priority search directory) skips
  staged `systemd-analyze verify`; exec and preflight checks still run. This is
  the documented limitation in SPEC, not a claim of full host-only validation.
- Unit directive shadowing is not implemented in this wave.
  `augeaswrap.unit.shadow_check` is a documented no-op seam for the shared
  HC-1b-s effective-order implementation.
