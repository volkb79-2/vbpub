# Specification: Netcup installer rework

## Target resolution

### Requirement: authenticate before installer side effects

The normal installer MUST obtain a valid API client before generating,
reading, or deleting a controller SSH key. Missing or invalid refresh-token
configuration MUST fail with a remedy referring to `./scp-api.py login`.

### Requirement: explicit installer modes

`install-host.py` with no arguments MUST print usage and perform no API or SSH
work. `wizard` MUST expose the API-backed gather-and-install flow.
`configure` MUST be a compatibility alias for `wizard`. `install` MUST read
`target-host.jsonc` by default, or the file supplied with `--config` (with
`--payload` retained as a deprecated alias), and MUST not gather missing
values interactively.

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

### Requirement: safe local retention

The installer MUST retain the local controller key by default so it can be
reused. If `--local-controller-key remove` is requested, removal MUST occur
only after the consumed customScript's declared generic completion marker is
observed. Without that marker, or on failed installation, the key MUST be
retained for diagnosis.

## Bootstrap/customScript

### Requirement: producer-owned customScript

`install-host.py` MUST NOT build a Debian-specific customScript. A producer
such as Debian v2 MUST own its bootstrap source, repository branch, strict JSON
configuration, notification validation, controller-key configuration, and
remote retention semantics. The producer's bundle MUST contain a
`customScript` string and MAY contain a `completionMarker`.

### Requirement: normal payload

The wizard MUST consume a customScript command or producer JSON bundle when
given with `--custom-script-file`; without one it MUST not invent a hook.
The file-driven `install` command MUST treat `customScript` as optional and
send exactly the value in the selected file. A config without a customScript
MUST not cause a hidden operating-system-specific default or controller-key
generation. The installer MAY expand only the provider-neutral
`{{CONTROLLER_SSH_PUBKEY}}` marker.

### Requirement: complete file-driven config

`install` MUST validate its JSON/JSONC config before authentication or key
work. It MUST require a positive `serverId` (or non-empty resolvable
`hostname`), positive `imageFlavourId`, and non-empty `diskName`.
`sshKeyIds` and `customScript` MAY be omitted intentionally.

### Requirement: install monitoring

`install` MUST monitor the created task by default. `--no-monitor` MUST be the
explicit task-creation-only escape hatch.

### Requirement: external builder boundary

The Debian-v2 `build-customscript` action MUST perform no Netcup API calls and
MUST emit validated JSON plus the cloud-init command. Netcup MUST consume that
bundle without importing Debian-v2 code or duplicating its configuration
translation.

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
