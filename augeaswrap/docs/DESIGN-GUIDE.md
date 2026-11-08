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
