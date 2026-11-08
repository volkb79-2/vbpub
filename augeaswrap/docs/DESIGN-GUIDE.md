# Augeaswrap design guide

## Publish endpoint facts at the owning service

Host endpoint paths and GIDs change with service configuration and host
installation state. The service installer already resolves those facts, so it
publishes one fragment under `/etc/host-endpoints.d/<owner>.env`; consumers read
the resulting process environment and never recreate the service's defaults.
The shared helper sorts fragments, rejects duplicate keys, and regenerates the
aggregate and environment block under one `flock`. This keeps multiple service
installers from overwriting each other's environment data.

The aggregate is a simple `KEY=VALUE` file because pam_env, POSIX shells,
systemd `EnvironmentFile=`, and Docker env files can all consume the same
restricted alphabet. Atomic replacement protects each generated file from a
partial write. The managed block preserves unmanaged `/etc/environment` bytes,
and refuses an assignment outside the block when it would duplicate a managed
key. A publish may skip a damaged fragment owned by another service so it can
preserve the remaining valid owners; it emits a warning because that owner's
variables will be absent from the regenerated aggregate until the owner repairs
its fragment. Symlink refusal prevents a managed target from redirecting writes
to an unrelated file. The API and operational examples are in the
[consumer guide](CONSUMERS.md#host-endpoint-records); the normative contract is
in [`SPEC.md`](../SPEC.md#endpoint-records).

## Guarded applier

The applier keeps transaction journals and exact pre-images so a caller can
inspect a partial apply and choose an explicit revert. It does not keep an
owner registry or infer who should win when two callers name the same file.
That avoids turning a simple file applier into a hidden package manager: a
caller states the desired present or absent value, and the journal records
what the transaction changed. A revert checks that no later transaction
changed the same address before restoring the pre-image. It then reruns the
recorded post-actions for the restored addresses so dependent services can
reload the restored configuration. Transaction IDs preserve allocation order
using retained transaction directories, so the revert check does not mistake
two transactions from the same second for unrelated history.

Resource kinds share one frozen protocol so independent kinds can be built
without engine-specific hooks. `observe` supplies the complete precondition,
`diff` describes the requested transition, `stage` validates a private image,
`commit` writes it, `post_actions` declares argv-only follow-up commands, and
`verify` compares the host with the staged image. The protocol and context
contract are defined in the [specification](../SPEC.md#guarded-applier-protocol-hc-1a).
Each JSON invocation emits one document so automation can consume the reviewed
plan and its outcome as a single record.

## Systemd units

A unit is one whole file, or one drop-in on somebody else's unit. The address
is the file path, so pre-images and stale-revert checks use the same exact-file
model as `file`. The kind does not claim ownership of a service or infer which
drop-ins belong to it. A caller requests each file it wants and uses
`state="absent"` explicitly to remove it.

Validation runs on the staged image, before the managed tree changes.
`systemd-analyze verify` sees the unit and copied local drop-ins under a staged
root; executable checks then inspect the selected managed root. The executable
policy is deliberately narrower than systemd's loader: root-owned regular
executables, and no writable component on the path. That closes the common
privilege-escalation route where a service unit runs a script from a writable
directory. Optional preflight argv remains the service-specific configuration
check.

Runtime actions are separated from file commits. The engine deduplicates
`daemon-reload` across all changed units, then enables, reloads or restarts
according to the explicit request. `pending` exists because an installer often
must update a unit without interrupting a live service; the journal retains
the exact argv for a later operator action. A socket restarts only when its own
text changes because restarting it unnecessarily can disrupt every listener
path, while changing its client service does not change the socket unit.

The one verification limitation is a host-only drop-in: a distro unit whose
base fragment is outside `/etc/systemd/system` is not copied into the staged
root, so syntax verification is skipped for that drop-in. Exec and preflight
checks still apply. Reconstructing the complete systemd search path would make
the observation much larger and risk validating a tree that differs from the
host; the limitation is explicit instead.

Directive-level shadow checks are not part of this kind yet.
`augeaswrap.unit.shadow_check` is the no-op seam for the shared HC-1b-s
effective-order implementation.
