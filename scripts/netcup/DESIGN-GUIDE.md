# Netcup SCP tool design guide

## Authentication is an explicit command

The SCP API needs a long-lived OAuth refresh token, but obtaining it is a
browser-mediated device-code flow. `login` performs that flow and writes the
token to `.env`. Afterward, in an interactive terminal, it performs one
read-only server inventory request and offers only SCP internal names matching
`v<digits>` for a shared local protected-server policy. It does not create an
SSH key. Non-interactive login still saves the token and leaves the policy
unchanged.

The interactive candidates are enriched with server detail and interface
records so the operator sees the server ID, addresses, and configured rDNS
before selecting a name. If the provider or resolver cannot supply optional
presentation data, the wizard says so and still permits selecting the verified
name/ID; it never treats missing addresses as evidence that a server has no
addresses.

The policy is intentionally local rather than a claimed provider-side lock:
the SCP API has no account/server “lock” operation, and the SCP website does
not read local configuration. API commands fetch server details immediately
before acting and refuse a matching name or recorded ID. Login persists both
the selected name and ID; the ID prevents a rename from becoming a fail-open.
Invalid policy syntax or an indeterminate target response fails closed. Any
automated UI/PWMCP path must call the same
`netcup_scp_client.assert_server_operation_allowed` guard before operating on
a server; direct manual website actions can bypass the local policy.
The repository currently has no managed SCP UI flow, so this shared guard is
presently enforced by API-backed commands only; the `NETCUP_SCP_UI_*` settings
are not consumed by a repository command.
Account-level user-ISO storage and firewall policy definition are not server
mutations; attaching media, assigning a policy, power operations, task
cancellation, and Debian image installation are guarded.

The interactive `wizard` is the API-backed gather-and-install path. It resolves
the target through the same picker as normal installation, writes a complete
`target-host.jsonc`, asks for final confirmation, and starts the image install.
`configure` is only a compatibility alias for that wizard; it is not a
server-dependent default-recipe mode. A bare invocation prints usage and does
not authenticate or touch SSH state.

The file-driven `install` path reads exactly `target-host.jsonc` by default, or
the file supplied with `--config`. It validates the complete target locally
before authentication and key work, then submits the saved payload and follows
the task by default. `--no-monitor` is the explicit task-creation-only mode.
`sshKeyIds` and `customScript` are optional in the file: the former means no
persistent account keys are injected, and the latter means no cloud-init hook
is sent.

The operating-system installer owns customScript generation. Netcup's wizard
can consume a plain command or a JSON bundle from that producer with
`--custom-script-file`; file-driven installs send the reviewed `customScript`
as-is. Netcup only expands the provider-neutral
`{{CONTROLLER_SSH_PUBKEY}}` marker and optionally waits for a declared
`completionMarker`. A plain image install can therefore complete without any
temporary controller key.

## Generated target and source selection

`target-host.jsonc` is an operator-local reviewed request, not a portable
repository configuration. It is ignored because image IDs, account-key
choices, and target identity are account-specific facts. `wizard` writes it;
`install` reads it. There is no hidden `default-recipe.jsonc` fallback.

The customScript source is intentionally outside `install-host.toml`. A
producer such as Debian v2 renders its URL, repository branch, strict JSON
configuration, credentials, and remote retention policy into a bundle. This
prevents the Netcup frontend from acquiring an operating-system-specific
environment-variable list or a second bootstrap template.

## Notification backend and Mattermost boundary

The Debian-v2 producer carries an explicit `NOTIFY_BACKEND` selector with
`telegram`, `mattermost`, and `none` values. Those values and credentials are
validated by Debian v2 and embedded in its generated JSON; Netcup does not
read or rewrite them.

Mattermost is intentionally integrated through its post-only incoming webhook,
not through a PAT or a REST client. The public consumer contract in
`mattermost-server/CONSUMER.md` gives the external hostname, producer
identity, channel binding, and secret-file location. A Netcup VM must use that
public URL. The remote bootstrap is a dependency-free `curl | python3 -`
entrypoint, so importing nyxloom's tightly coupled notification module would
be the wrong boundary. The notification path is best-effort: a webhook outage
must not fail disk partitioning or stage completion.

There is no `libraries/mattermost-client` extraction in this change. A Python
import package would need an underscore name such as `mattermost_client`; a
hyphen is suitable for a distribution/project name, not for an import. An
extraction becomes worthwhile only if another dependency-free consumer needs a
stable, tested webhook contract.

## SSH identity timing

The normal install path creates a per-host, per-date identity only after it
knows which host it is targeting and only when the selected customScript needs
one. `login`, `wizard`, and `configure` must not create one merely because the
frontend was invoked. The generated controller public key is passed through
the opaque hook; the hook's own producer decides whether the remote key is
removed. The operator's persistent access key remains independent.

The Netcup account-level `sshKeyIds` choice is separate from that local
controller identity. Existing account keys are shown during the interactive
key step and the operator may select zero, one, or several; `--ssh-key-id ID`
pins existing keys for direct payload runs. The installer never registers the
temporary controller key as an account key and never creates account keys as a
side effect of an install.

The local controller key is retained by default so it can be reused for a
later installation. A producer may declare a generic completion marker; only
after that marker is observed can `--local-controller-key remove` delete the
local key. Without the marker, the key remains for diagnosis and reuse. The
remote key policy is not a Netcup concern.

`netcup.toml` is the one API endpoint configuration source. `install-host.toml`
contains installer/SSH policy only, and `monitor-task.toml` contains
monitor polling only. Frontends import `netcup_scp_client`; they do not shell
out to `scp-api.py` or duplicate token/configuration setup.

## OpenAPI reference and SCP Screen

The canonical human API documentation is Netcup's
[SCP REST API page](https://www.netcup.com/en/helpcenter/documentation/servercontrolpanel/api).
The SCP publishes its OpenAPI document directly at
`https://www.servercontrolpanel.de/scp-core/api/v1/openapi`; the local
`netcup-scp-openapi.json` is a checked-in reference snapshot, not a runtime
dependency. `update-scp-openapi.py` validates the response shape and atomically
replaces that snapshot so a network or schema error cannot truncate the prior
copy.

The published API schema includes `/api/v1/servers/{serverId}/rescuesystem`:
`GET` reports state, `POST` activates the provider-managed rescue boot, and
`DELETE` deactivates it. This boots Netcup's minimal Grml rescue Linux outside
the installed OS, for network diagnosis, backup, and system repair. It is a
recovery environment, not a serial console or an ISO installation signal.
The status schema includes an `active` flag and optional temporary rescue
password; the CLI's normal output redacts that secret. Netcup requires the
server to be off before activation and notes that the provider firewall is
disabled while rescue mode is active.

Netcup documents its SCP “Screen” as browser VNC. That is a separate browser
surface from the REST API, whose published schema has no screen, VNC, or serial
console operation. A manual PWMCP check confirmed screen access and keyboard
delivery after clicking the console canvas; it did not validate screen-text
recognition. Credentials belong in the local mode-0600 `.env`; the URL is
public, and `netcup.toml` remains limited to API endpoint settings. The
browser may also require an interactive second factor. See the live check
below for the observed console and automation limit.

## Shared CLI boundary

All four Netcup entrypoints—`scp-api.py`, `install-host.py`,
`monitor-task.py`, and `install-restore-iso.py`—use `cli-extended` for identity/version, grouped help,
registry-backed verb parsing, common output/diagnostic behavior, and clean
cancellation. Install the package in the same Python environment that runs
the scripts; they do not add a repository-root import-path fallback. Their
shared family version comes from `scripts/netcup/VERSION`, not the helper's
package version.

Each executable registers its verbs, summaries, full descriptions, groups,
arguments, options, and examples once. `summary_description` keeps the
top-level catalog concise while command help retains detailed behavior. The
top-level common output options can show the union supported by the CLI;
command-specific help is authoritative, and an unsupported mode is rejected
with that verb's help. Domain handlers still own provider semantics, API and
config validation, redaction inputs, result schemas, and confirmation timing.

Bare invocation, help, and version discovery construct the interface only;
they do not load `.env`, API settings, or credentials, create SSH identities,
or contact Netcup. Runtime configuration is loaded only after dispatch. The
`install-host.py attach` verb is a separate exception to the install/API
workflow: it loads local SSH settings and follows the provider customScript
log over SSH, but makes no Netcup API calls and never creates an identity key.
It uses normal SSH key/agent discovery unless an existing identity is
explicitly selected.

## Generic installer ISO handoff

The SCP API exposes the provider VM's state and ISO attachment, but not guest
console text or a portable installation-complete event. Guest-side
`CDROMEJECT` does not remove the provider attachment, and a normal reboot leaves
the VM running and can boot the same attached installer again. The generic
install verb therefore uses a configured shutdown handoff: the installer is
expected to power off after success. This is a workflow signal, not independent
proof that the guest installer succeeded; the generic workflow does not read
its logs.

The workflow records the uploaded ISO hash, saved provider boot order and each
completed mutation. It observes the guest reach `RUNNING`, then waits until
Netcup reports it off. While the guest is off it detaches the exact ISO this
workflow uploaded, restores the saved boot order, and powers on the server
from disk. It marks the install complete after Netcup reports `RUNNING` with
`configChanged=false`. A caller can optionally add an HTTP readiness probe,
but that is not part of the generic completion contract. On timeout, the ISO
remains attached and the checkpoint can resume; the tool does not guess that
the installer completed.

The provider polling first observes `RUNNING` with `configChanged=false`, then
waits for `OFF`, `STOPPED`, `SHUTDOWN`, or `SHUTOFF`. If the guest blocks at an
end screen asking for a key before poweroff, it remains running and the wait
times out. A PWMCP session can view the SCP Screen and send input after the
operator clicks the console canvas, after which the normal provider handoff
can resume. If the installer automatically reboots, the provider normally
continues reporting `RUNNING`, so the workflow cannot recognize that reboot as
completion. If a brief reboot appears as an `OFF` poll, the API provides no
reason code and the current check could mistake it for the handoff. Reboot-only
installers need a guest success signal or a system-specific readiness check.
The TrueNAS unattended image supports the handoff by selecting
`--post-install-action poweroff`; its GRUB entry also uses `noeject` to avoid
the live-boot media-removal prompt that would otherwise stop before shutdown.

#### Live SCP Screen check (2026-10-19)

On Netcup server 804027 (`v1001.vxxu.de`), PWMCP authenticated to the SCP and
opened the Screen popup at
`https://www.servercontrolpanel.de/scp-ui/servers/804027/screen?popup=1`. The
console showed TrueNAS `Console setup` and the guest's HTTP and HTTPS
management URLs; this is the installed, ready-to-use system screen, not an
installer finish prompt. Clicking the canvas and sending `0` without Enter
placed the character at the console prompt; Backspace removed it, and the
menu was restored by reopening the Screen. No menu option was submitted. A
read-only provider check afterward still reported `RUNNING`,
`configChanged=false`, no ISO attached, and boot order `HDD,NETWORK,CDROM`.

The console is rendered as a canvas. PWMCP's page snapshot exposes the browser
controls but not the console text, so automatic detection needs OCR or a
screen-specific image recognizer. The manual check verified screen access and
keyboard delivery; it did not observe an installer reboot or a finish prompt.
No PWMCP screen observer is part of the generic installer yet. A future
observer must treat an unrecognized screen as indeterminate and keep waiting;
provider `OFF` alone has no reason code that proves installer success.

## Restore ISO lifecycle

A restore ISO takes long enough that a single command cannot safely infer when
the destructive guest write has finished. `start` uploads and boots the ISO,
then records the saved provider boot order and returns control to the operator.
The guest reports target discovery, write progress, read-back verification,
and completion through Mattermost. The operator explicitly asserts completion
before `finish` can remove the provider media.

The finish stage calls SCP to detach the virtual media, restores the boot order
captured before attachment, waits for each provider task, cold-cycles the
server, and verifies both SSH and `boot-pool` health. Guest `CDROMEJECT` only
requests an eject from the virtual drive; it does not remove SCP's media
attachment. The state file is private and records each completed provider
stage so an interrupted CLI invocation can resume without guessing which
mutation succeeded. A read-only `status` command exposes that checkpoint.

`finish` requires the operator to assert the guest's completion notification
and disk read-back verification. When that evidence is unavailable, `recover
--try-disk-boot` performs the same provider detach, boot-order restoration,
cold cycle, and SSH/pool health check without recording restore completion.
Its terminal checkpoint is `recovered`, and `restore_certified=false`; this
distinguishes a healthy boot from proof that the ISO completed its write and
verification steps. A recovery attempt can resume from its persisted provider
stage after interruption.

This boundary keeps Netcup credentials and provider mutations on the controller.
The ISO only restores the disk, reports progress to Mattermost, and requests a
reboot; it does not try to call the SCP API or grow the ZFS partition.

### Task monitor behavior

The task monitor exposes `show TASK_UUID` to fetch one snapshot and
`watch TASK_UUID` to poll until a terminal provider state. JSON is a
presentation choice on `show`, not a separate operation; cancellation remains
under `scp-api.py tasks cancel`, the API-owning mutation path. The `--poll`
interval belongs only to `watch` and defaults from `monitor-task.toml`.

The monitor validates task UUIDs and rejects non-object API responses, while
treating missing optional task fields as unknown rather than crashing.
`cli-extended` owns the invocation shell; the Netcup handler owns response
field interpretation, request semantics, and terminal-state meaning.
`--debug-raw` is deliberately conspicuous because both JSON output and shared
client diagnostics can expose secret-bearing task data.

### Why monitor-task has a generated semantic catalog

The registered parser is the grammar source; a TOML catalog records the
product decisions that argparse cannot express by itself. The canonical
[`CLI specifications`](CLI-SPEC.md) (one per CLI) embed the generated inventory
and accepted/refused invocation table. Its JSON manifest makes parser changes
reviewable; synchronization cannot rewrite the human-owned decision catalog
or the surrounding spec text.

Netcup is the first real adopter of the library's surface workflow. Its command
is a hyphenated script with sibling imports, so the path-based factory loads
`monitor-task.py:build_cli` directly. The stable generated module name avoids
checkout-root churn in callable labels, and the loader keeps the script
directory importable without a consumer shim. The workflow links behavior
tests for important combinations: `show --poll` and `watch --json` refuse,
`show --json --debug-raw` deliberately exposes raw data with a warning, and
repeated `watch --poll` uses the last value. Product tests assert the resulting
status and effects; collection markers only prove the test exists under the
expected case ID.

The installer also declares `--monitor` and `--no-monitor` as a shared parser
mutex. Argparse rejects the invalid combination before `.env` or runtime
settings load, so the product no longer needs a later duplicate check.

## Account-wide API exploration

The SCP API makes most inventory endpoints server-scoped: there is no
account-level endpoint for image flavours or bootable ISO images. `scp-api.py` therefore
lists `/servers` first and queries each server when a read-only command has no
ID. It adds the source server to those rows so identical image names from two
VMs cannot be mistaken for one result. An explicit ID remains available for a
focused query, and filters are applied locally to the returned fields.

`status` is the deliberately compact account-wide view. It queries each
server's detail record and normalizes provider fields into one table: vname,
hostname (or nickname), run state, architecture, CPU count, RAM and disk GiB,
an SSH-connectivity/authentication result, and a final multiline column
containing configured/resolver-derived reverse-DNS entries. The address list
is deliberately sourced only from the detail record's `ipv4Addresses` and
`ipv6Addresses`; nested live interface data is not merged because it can add
link-local addresses, prefixes, or rDNS map keys that are not host addresses
for this compact view.

Status uses a bounded four-worker pool and retains input order in the table.
The local SSH settings and key scan are shared across the invocation. Each
server gets one 2-second SSH service preflight per address until one responds,
before key authentication is attempted; this avoids multiplying a closed-port
timeout by the number of keys. Matching server-name/hostname/nickname filenames are attempted first,
but all keys are still tested so every successful key can be reported. Reverse
DNS calls run concurrently and are intentionally not memoized across duplicate
addresses: each displayed address remains an independent live lookup. A
reachable SSH service for which every tested key fails public-key
authentication is reported as `no keys match`. An SSH-level/session refusal is
reported as `rejected`, while transport failure is reported as `no answer`.

Actions that change state cannot safely fan out. ISO detach, rescue-system
deactivation, snapshot creation, deletion, and restoration therefore require an
explicit server ID. Snapshot restore additionally requires a listed unique
snapshot name, checks for active provider tasks, supports a read-only dry run,
and asks for confirmation before submitting the asynchronous disk replacement.
This keeps the convenient account-wide default limited to inspection while
retaining the API's server boundary for mutations.

The same boundary applies to ISO attachment and firewall assignment: both
require an explicit server. Firewall `set` requires the complete replacement
policy assignment, but its MAC is optional when live server details prove
there is exactly one interface; multiple interfaces require an explicit MAC.
The CLI deliberately does not pretend that guest-agent status is installer
health. Firewall policy create/PUT and user-ISO upload are supported, but both
validate locally and remain confirmation-gated because they carry larger
lockout/storage failure surfaces than a read/list wrapper. Presigned ISO
uploads bypass the authenticated API request helper so the SCP bearer token is
never sent to object storage.

ISO attachment is provider state. A guest `CDROMEJECT` ioctl can make the
virtual drive report an eject request, but it does not call SCP or remove the
provider's media attachment. A TrueNAS 27 live check returned success from the
ioctl while `iso-attached` still reported the ISO attached. Use the confirmed
`iso-attached SERVER_ID detach` action for provider detachment, then inspect
boot order and `configChanged` before rebooting.

Firewall policy input is the API's `FirewallPolicySave` request shape, supplied
as strict inline JSON or a JSON file. The validator rejects unknown/read-only
fields, missing rule enums, malformed addresses, duplicate addresses, and
invalid ports before contacting SCP. The API remains authoritative for
provider-side semantic constraints and rule application order.

The policy examples cover the recurring operator cases: public services with
restricted administrator ports, WireGuard handshakes with public SSH blocked,
and an egress allow-list followed by protocol drops. A WireGuard `wg0` inside
the guest is not a distinct SCP interface, so the provider firewall cannot by
itself express “allow this post-decryption traffic only on wg0”; the guest
firewall must enforce that half.

Power operations share one `power` verb (`on`, `off`, `cycle`, `reset`) because
they are one API operation family: a PATCH of server state with an optional
`stateOption`. The sub-action names make the destructive distinction visible
in help while avoiding four unrelated top-level commands.

The account-level ISO collection uses the singular `user-iso` verb even though
the default action lists multiple objects; its explicit `upload` action makes
the state-changing path visible in both the command and help output. Help keeps
actions in their own group, separates list/write/confirmation options, and
shows an executable example for each action family. The public spelling is
`--help` (there is no short `-h` alias), so generated usage cannot hide the
documented interface behind argparse's shorthand.

### Why the case tests replay the catalog

Each CLI has its own spec, catalog, manifest and findings file because `sync`
supports one marked region per spec file; `CLI-SPEC.md` is only an index. The
case tests (`tests/test_cli_cases_install_host.py`,
`tests/test_cli_cases_scp_api.py`) read the catalog row and replay the row's own
invocation through the real `main()` with a fake client, then compare exit
status, output and the exact API calls with what the row claims. That keeps the
documented invocation and the executed one identical; a hand-written argv in a
test could drift from the row. The library controls (`--json`, `--debug-raw`,
`--yes`, `--dry-run`) are each one parametrised test across routes, because
every route shares the same contract, and each proves a contrast against the
same argv without the control. The rejected alternative was one bespoke test
per row, which for scp-api would be about 150 near-identical functions.

Review findings were fixed where the fix was local: `watch` now exits 1 when
the task ends in ERROR, CANCELED or ROLLBACK (0 only for FINISHED), each
`--filter` help line states its semantics (server-side API query on `tasks` and
`firewall-policies`, client-side case-insensitive text match on `imageflavours`
and `iso-bootable`), the install-host dry-run plan prints the SSH target and key
choice a live run would use, and the purely structural `attach-iso` and `power`
`configure` callbacks became declarative specs. The remaining scp-api
`configure` callbacks stay hand-written until the library can declare optional
action positionals and rules conditioned on a positional's value (cli-extended
CLI-EXT-17); `power` keeping its action-first argument order is recorded as a
`wontfix` compatibility finding.

A reviewed row must never claim more than its linked tests prove. The case
replay therefore checks each row's `effects` against what a replay can observe
(plan lines, API calls, files written, followers started); where an effect is
not observable under `--dry-run`, the row says so and names the live-run test
that covers it (for example the controller-key retention and monitor-argument
tests).

## Test boundary

The Debian installer’s ordinary tests run in `tester-unified`. Real loop/swap
commit tests remain in `debian-install-v2`'s explicit QEMU/TCG VM lane. The
Netcup merge must not resurrect the historical privileged-container harness,
because Docker shares the host kernel and cannot isolate host-global swap.
