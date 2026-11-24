# Design: Netcup installer lifecycle

## 1. Modes and boundaries

`install-host.py` exposes three workflow verbs and one compatibility alias:

- `wizard`: resolve a target, gather and validate an installation plan, write
  a reviewed `target-host.jsonc`, confirm, submit the image install, and
  optionally monitor the provider task/customScript contract.
- `install`: read and strictly validate a target config (default
  `target-host.jsonc`, override with `--config`), submit exactly that image
  payload, and monitor the resulting task by default.
- `attach`: SSH-attach to an already installing/installed host and follow its
  provider customScript log. This path requires an SSH host, makes no Netcup
  API calls, and never creates an identity key; it uses normal SSH identity
  discovery unless an existing key is explicitly selected.

`configure` is a compatibility alias for `wizard`, not a separate
server-dependent recipe command. The frontend has no customScript builder: a
producer such as Debian v2 emits a JSON bundle, and
`wizard --custom-script-file` consumes it.

A bare invocation prints usage and performs no authentication or SSH work.

`scp-api.py` owns account/server API exploration and general reversible API
operations, including power control. `install-host.py` may import shared API
library functions, but it does not call the CLI executable.

Invalid combinations are rejected by each verb's parser before authentication
or key work. SSH attachment is a distinct `attach` verb, not an installer mode
flag, so install/configuration options are not accepted there. `--poweroff` is
removed from `install-host.py`; operators use `scp-api.py power off SERVER_ID`.

## 2. Normal installation sequence

The sequence is intentionally ordered so every failure before the SSH stage
is side-effect free with respect to local key material:

1. Parse arguments and reject conflicting modes/options.
2. Load and validate local configuration, including the protected-server
   policy and the shared API configuration.
3. Obtain an authenticated API client. If the refresh token is absent, fail
   with the exact remedy: run `./scp-api.py login`.
4. Resolve the target:
   - an install config supplies its own `serverId`/hostname;
   - an explicit `--server-id` or configured
     `NETCUP_SCP_API_SERVER_NAME` is used when supplied;
   - otherwise, in an interactive terminal, list all account servers with
     name, ID, hostname, and addresses and prompt for one;
   - non-interactive mode without a target fails clearly.
5. Fetch server details and verify the local protected-server denylist before
   generating a controller key or preparing any mutating request.
6. Resolve current server details, a compatible image flavour, disk, and the
   installer recipe. Display the choices before key work.
7. List existing Netcup account SSH keys and allow a multiple-selection prompt
   (including an explicit no-account-key choice). `--ssh-key-id` remains a
   repeatable non-interactive override. Selected IDs become `sshKeyIds` in the
   install payload; no account-key POST is performed by this installer.
8. Resolve the controller key. Search for an existing host-targeted key first;
   generate a new key only when no suitable key exists and the plan needs
   controller access.
9. If the wizard was given a customScript bundle, pass its opaque command
   through. File-driven `install` sends the config's customScript as-is,
   including no customScript for a plain image install.
10. Print a redacted, complete summary and write/review the JSONC payload as
    appropriate.
11. Ask for final confirmation, then POST the image installation.
12. If monitoring is enabled and the consumed bundle declares a completion
    marker, wait for that marker before applying local-key removal.

The account-key list and target/server details are read-only API operations.
The image POST is the first server mutation in the normal installer.

## 3. SSH key roles

### Persistent account keys

The operator’s existing keys stored in Netcup are selected by their account
key IDs and sent as `sshKeyIds`. Multiple IDs are supported. These keys are
not generated, deleted, or modified by `install-host.py`.

If no account key is selected, `sshKeyIds` is omitted. The installation can
still be monitored through the controller key.

### Temporary controller key

The controller private key remains local to the checkout. Its public key is
passed as `CONTROLLER_SSH_PUBKEY` in the opaque customScript. The customScript
producer decides how that key is installed and removed; operator keys are
outside this frontend's contract.

The controller key is not uploaded to the Netcup account and is not placed in
`sshKeyIds`.

### Reuse

The default identity template becomes host-oriented rather than run-oriented.
For a selected hostname such as `r1002.vxxu.de`, the resolver searches the
configured local SSH directory for private keys whose filenames contain the
host-specific label (`r1002`) and match the installer-key naming convention.
Existing dated keys from the previous template remain eligible. The newest
valid host-specific key is preferred; a new key is generated only when no
valid candidate exists.

An explicitly supplied identity path is authoritative: if it does not exist
or is not readable as a private key, the command fails instead of silently
generating a different key.

## 4. Controller-key retention

The local controller key is retained by default so it can be reused for a
later installation. A producer may declare a generic completion marker; only
after that marker is observed can `--local-controller-key remove` delete the
local key. Without the marker, the key remains for diagnosis and reuse. The
remote key policy is not a Netcup concern.

## 5. CustomScript producer/consumer boundary

The producer owns source URLs, strict JSON configuration, credentials, remote
retention, and shell quoting. Debian v2's `build-customscript` action emits a
bundle containing `config`, `customScript`, and `completionMarker`. Netcup
consumes that bundle and only expands the provider-neutral controller-key
marker. No Netcup module imports Debian-v2 code or duplicates its environment
variable mapping.

## 6. API configuration and client boundary

Introduce one shared API configuration file at
`scripts/netcup/netcup.toml`, containing `[api].base_url` and
`[api].keycloak_url`. The shared client loads and validates it once. Remove the
duplicated `[api]` tables from `install-host.toml`, `scp-api.toml`, and
`monitor-task.toml`; `scp-api.toml` is renamed to `netcup.toml`, while
`monitor-task.toml` retains only monitor-specific settings.

The shared client should expose construction/authentication and the common
response/error helpers. `scp-api.py`, `install-host.py`, and `monitor-task.py`
remain thin frontends around that library. No frontend copies API URL setup,
refresh-token handling, or server-target validation logic.

## 7. Validation and failure behavior

- Missing API credentials fail before key generation.
- Missing/invalid target selection fails before key generation.
- A protected target fails before key generation and before any account-key
  mutation.
- Missing payload, malformed JSON/JSONC, a non-object top level, invalid
  booleans-as-integers, non-positive IDs, and invalid key-ID lists produce
  structured errors rather than tracebacks.
- A normal interactive invocation without a configured target is a wizard,
  not an immediate environment-variable error.
- The error text names `NETCUP_SCP_API_SERVER_NAME`, not the nonexistent
  `$SERVER_NAME` variable.

## 8. Documentation contract

Update all three user-facing responsibilities in the same change:

- `scripts/netcup/README.md`: feature surface and quickstarts;
- `scripts/netcup/DESIGN-GUIDE.md`: rationale and lifecycle boundaries;
- adopter examples/help output: target picker, multi-key selection, reusable
  controller keys, retention outcomes, and the shared customScript behavior.

The v2 README/config documentation must also explain the host-retention input
and the failure-safe retention rule. Every new closed value (`remove`,
`retain`) must appear in the help and docs.
