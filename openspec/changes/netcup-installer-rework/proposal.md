# Proposal: Rework the Netcup installer lifecycle

Status: proposed  
Date: 2026-09-21  
Scope: `scripts/netcup/` and the controller-key retention contract in
`scripts/debian-install-v2/`

## Summary

Make `install-host.py` a deliberate installation workflow rather than an
implicit collection of API, key-generation, and bootstrap side effects.

The installer will authenticate and resolve its target before doing any SSH
work, offer an interactive server picker when no target is configured, inject
the operator-selected persistent Netcup account keys, and separately create or
reuse a host-specific temporary controller key for monitoring. The controller
key will never be registered as a Netcup account key merely because the
account has no other keys.

`scp-api.py` remains the user-facing API CLI. `install-host.py` will use the
same shared API client/configuration library directly; it will not invoke the
CLI as a subprocess. `install-host.toml` will contain installer settings only,
and one shared API configuration will be used by all Netcup tools.

## Problems addressed

- A bare `install-host.py` invocation currently generates an
  `unknown-host` SSH key before discovering that the target variable is
  missing.
- `--poweroff` duplicates an operation already provided by `scp-api.py`.
- Payload, attach-only, poweroff, and normal modes are not mutually validated.
- The temporary controller key can be registered as a persistent Netcup
  account key, conflating two different access roles.
- The normal key prompt selects only one account key even though the API
  accepts multiple `sshKeyIds`.
- The normal and manual custom-script paths assemble equivalent bootstrap
  commands independently.
- API URLs are duplicated in `install-host.toml`, `scp-api.toml`, and
  `monitor-task.toml`.
- The v2 bootstrap always removes the controller key after success and offers
  no explicit host-retention policy.

## Goals

1. Make every mode validate its inputs and target before local key generation
   or remote mutation.
2. Prompt for a target server from the authenticated API when normal
   interactive mode has no configured target.
3. Support selecting multiple existing Netcup account SSH keys.
4. Reuse an existing host-targeted local controller key; generate one only
   when no suitable key exists.
5. Keep controller-key retention independent on the host and locally.
6. Generate the normal API payload and the manual customScript from one shared
   builder.
7. Establish one API configuration source and one shared API client boundary.
8. Remove installer-owned power control and direct operators to `scp-api.py`.
9. Update tests, README, DESIGN-GUIDE, and consumer-facing examples together
   with the behavior.

## Non-goals

- Do not make the Netcup provider offer a server lock; the existing local
  protected-server denylist remains the safety control.
- Do not register the temporary controller key as a persistent account key.
- Do not make `install-host.py` shell out to `scp-api.py`.
- Do not make the manual customScript wizard a second installer implementation.
