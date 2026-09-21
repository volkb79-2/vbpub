# Specification: Netcup installer rework

## Target resolution

### Requirement: authenticate before installer side effects

The normal installer MUST obtain a valid API client before generating,
reading, or deleting a controller SSH key. Missing or invalid refresh-token
configuration MUST fail with a remedy referring to `./scp-api.py login`.

### Requirement: interactive target picker

When normal interactive mode has no payload target, explicit server ID, or
`NETCUP_SCP_API_SERVER_NAME`, it MUST list account servers and prompt for one.
The list MUST include enough identity to avoid ambiguity: server name, ID,
hostname, and available IP addresses.

Non-interactive mode MUST refuse to continue without an unambiguous target.

### Requirement: protected target ordering

The installer MUST verify the selected target against the local protected
server policy before controller-key generation and before any mutating API
request.

## Account SSH keys

### Requirement: persistent-key selection

The installer MUST treat Netcup account SSH keys as persistent operator access
keys. It MUST allow selecting zero, one, or multiple existing account key IDs
and MUST pass the selected IDs as `sshKeyIds`.

### Requirement: no implicit account-key creation

The installer MUST NOT register the temporary controller key as a Netcup
account key and MUST NOT create an account key merely because the account has
no existing keys. Account-key management remains an explicit API operation.

## Controller SSH key

### Requirement: host-specific reuse

The installer MUST search for a valid existing local controller key targeted at
the selected host before generating one. A valid candidate MUST be preferred
over generating a new key. Existing dated installer-key filenames MUST remain
discoverable.

### Requirement: explicit identity paths

When the operator supplies an explicit controller identity path, a missing or
invalid path MUST be an error; the installer MUST NOT silently substitute or
generate another key.

### Requirement: separate retention controls

The installer MUST expose independent host and local retention controls. It
MUST accept exactly these successful-install outcomes:

- host remove / local retain (default);
- host remove / local remove;
- host retain / local retain.

Host retain / local remove MUST be rejected before installation.

On failed installation, both controller-key forms MUST be retained for
diagnosis. Local removal MUST occur only after successful stage2 completion is
observed.

## Bootstrap/customScript

### Requirement: one builder

The normal API payload and manual `build-customscript` output MUST be produced
by one shared builder. They MUST use the same bootstrap source, repository
branch, notification validation, controller-key expansion, and retention
semantics.

### Requirement: normal payload

The normal installer MUST include a generated `customScript` that invokes the
configured `debian-install-v2` bootstrap. It MUST not rely on a manually
maintained second command template.

### Requirement: manual mode boundary

`build-customscript` MUST perform no Netcup API calls. It MAY generate a local
controller key only after the operator explicitly requests controller access
and supplies/chooses a target label. It MUST disclose that local retention
cannot be automated without an observing monitor process.

## API/configuration boundary

### Requirement: shared API configuration

All Netcup frontends MUST use one validated API configuration source. API base
and Keycloak URLs MUST NOT be duplicated in installer, explorer, and monitor
configuration files.

### Requirement: shared library, not CLI subprocesses

`install-host.py` MUST use the shared Python API library directly. It MUST NOT
invoke `scp-api.py` through a shell or subprocess to perform API operations.

### Requirement: power operations

`install-host.py` MUST NOT expose a poweroff mode. Power control MUST be
performed through `scp-api.py power ...`, including the existing protected
server policy.

## Validation

### Requirement: fail clearly on malformed input

Payloads and CLI combinations MUST reject malformed top-level JSON/JSONC,
invalid types (including booleans where integer IDs are required), non-positive
IDs, invalid key-ID lists, and conflicting modes without a traceback or SSH
side effect.
