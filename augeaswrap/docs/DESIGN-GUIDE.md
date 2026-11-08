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

Present units and drop-ins require `intent = "authoritative"` or
`intent = "fallback"`. This records how the caller views the layer in the
systemd search order; directive-level enforcement remains the HC-1b-s job.
The registered `shadow_check(resource: Unit, root: Path) -> list[Problem]`
hook is called during planning and currently returns an empty list. Its small
signature lets the shared shadow reader connect without changing the resource
protocol.

The managed unit file and each existing directory in its path must be owned by
the expected root identity, regular directories/files, and not writable by
group or other. Live requests default to `root:root`; explicit non-root owners
are refused on the live host. Special mode bits are refused. Under a fake root,
the invoking uid/gid are used so an unprivileged test or image builder can
write its temporary tree. Requested unit text is valid UTF-8, contains no NUL,
and is at most 1 MiB in encoded bytes, including drop-ins whose verify step is
skipped.

Executable checks run against the selected root. The executable token must be
absolute; systemd specifiers and environment-variable references are not
expanded. The path is normalized and resolved inside the selected root before
the `/tmp`, `/var/tmp`, and `/home` refusal is applied. `..`, symlink
components, non-root-owned components, and group/other-writable components
are refused. This protects both the selected root boundary and the privilege
boundary of a service that runs as root.

Staged verification copies existing valid unit files and resolvable aliases
from the static `/usr/local/lib` and `/usr/lib` trees, the requested file,
same-unit drop-ins, and referenced dependency units from every selected search
directory. Sources are fingerprinted in the observation and checked again
while staging. `systemd-analyze verify` runs against that staged
root. A plan states `verify: skipped` with a reason when the analyzer is not
available or when a host-only drop-in has no base unit in the selected root;
in either case executable and configured preflight checks still run. A
drop-in with an available base unit is verified together with the staged base
and dependencies.

Runtime actions follow the shared rev3 phases: sysusers, tmpfiles,
`daemon-reload`, sysctl, enable/start/restart in declaration order, gpasswd,
then endpoint work. The engine deduplicates identical name/argv actions and
keeps declaration order within a phase. For whole-unit removal, stop and
disable are separate journaled pre-commit actions, interleaved with deleting
that unit's file; the later phases then reload systemd and reset its failed
state. Reverting a newly created whole unit derives stop/disable plus
`daemon-reload` from the absent pre-image and does not replay enable or runtime
actions against the removed unit. Reverting a restored present unit reconciles
its saved enablement and replays only applicable runtime actions.

`enable` is declared state. On the live host, planning reads
`systemctl is-enabled -- <name>` and schedules `enable` or `disable` whenever
the observed state differs, including when the file itself is unchanged. If
the state already matches, no systemctl action is declared. Fake roots have
no live systemd manager, so their preview can show enable on a changed unit
when `enable=true`, but cannot observe or reconcile a pre-existing manager
state. Successful transaction post-images are captured after actions and
verification, so later revert checks see the final manager state. Every unit
operand is preceded by `--` in systemctl argv. Preflight
commands have a 30-second timeout on the direct child; a command that spawns
descendants must manage their lifetime.

A socket restarts only when its own text changes because restarting it
unnecessarily can disrupt every listener path, while changing its client
service does not change the socket unit. The exec scanner examines physical
`Exec*=` lines; it does not join systemd backslash continuations or implement
the full systemd parser. Staged `systemd-analyze verify` remains the syntax
check when its stated inputs are available.
