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

## Layered resource shadowing

The same setting can be assigned from several drop-in directories, and the
winner depends on both basename sorting and the manager's merge rule. The
caller therefore chooses `intent = "authoritative"` or `"fallback"` for each
layered resource. Augeaswrap computes the effective sequence under the chosen
root and refuses a request when another assignment makes that intent false.
An equal value is a note unless an authoritative value is not last (or first,
for a first-definition format); that still means another file controls the
result. Equal fallback values remain notes even when the fallback would
otherwise win, because no effective value conflict exists.

`accept_shadow` is an exact pair of file and setting key. A broad exception
would continue accepting unrelated settings added to the same file, so one
accepted collision cannot silently authorize another. The complete layer
snapshot, including file bytes and drop-in directory membership, is part of
the plan precondition. A changed source or a new drop-in makes apply stale.

Unit directives can use the shared API documented in
[`SPEC.md`](../SPEC.md#shared-shadow-api-for-layered-kinds) when they add their
own kind: target-class order, effective files, normalized assignments and
findings are independent of a parser for any one format.

The request lists dependency order explicitly. A `sysusers` table must precede
an endpoint that uses `@gid:<group>`; that value resolves from the declared
fixed GID or an existing `/etc/group` entry. A group with an automatically
allocated GID cannot be guessed during planning, so that reference is refused.
An endpoint withdrawal precedes a later unit-removal resource when those
resources appear in that order. Membership changes, group creation or removal,
live sysctl application, and tmpfiles path changes are journaled as
irreversible effects; revert restores managed fragments and does not undo those
effects.
