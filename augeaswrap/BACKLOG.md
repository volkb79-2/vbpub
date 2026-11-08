# Augeaswrap backlog

- HC-1b-u: unit files and drop-ins, exec checks, systemd validation, and
  directive intent/shadow checks against the frozen resource protocol.
- HC-2: JSON key updates and pending-action journal queries.
- HC-3: Augeas-backed in-place edits, format validators, and fstab helpers.
- HC-4: the `pam_env` overlay lens and its `augparse` fixture.
- Run a live trixie acceptance probe for `systemd-tmpfiles --dry-run --root`
  and capture `systemd-sysctl --cat-config` output before using those native
  validators as a host-specific compatibility claim; the registered gate
  exercises their argv through a fake command runner.

Known limits of the HC-1a' engine, kept simple on purpose (also listed in SPEC,
"Adding a resource kind"):

- Post-action order is request order. The caller lists dependencies in order;
  the engine does not impose a global phase order. Revisit an optional
  `PostAction.phase` only if a later kind needs an order the request cannot
  express.
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
