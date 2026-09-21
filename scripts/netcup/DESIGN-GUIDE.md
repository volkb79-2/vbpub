# Netcup SCP tool design guide

## Authentication is an explicit command

The SCP API needs a long-lived OAuth refresh token, but obtaining it is a
browser-mediated device-code flow. `login` performs that flow and writes the
token to `.env`. Afterward, in an interactive terminal, it performs one
read-only server inventory request and offers only SCP internal names matching
`v<digits>` for a local protected-server denylist. It does not create an SSH
key. Non-interactive login still saves the token and leaves the denylist
unchanged.

The interactive candidates are enriched with server detail and interface
records so the operator sees the server ID, addresses, and configured rDNS
before selecting a name. If the provider or resolver cannot supply optional
presentation data, the wizard says so and still permits selecting the verified
name/ID; it never treats missing addresses as evidence that a server has no
addresses.

The denylist is intentionally local rather than a claimed provider-side lock:
the SCP API has no account/server “lock” operation. Mutating server commands
fetch server details immediately before acting and refuse a matching name or
recorded ID. Login persists both the selected name and ID; the ID prevents a
rename from becoming a fail-open. Invalid policy syntax or an indeterminate
target response fails closed. Account-level user-ISO storage and firewall
policy definition are not server mutations; attaching media, assigning a
policy, power operations, task cancellation, and Debian image installation
are guarded.

`configure` is also local recipe setup after one read-only server/image lookup.
It likewise does not generate an SSH identity. The normal install path is the
only API-install path that needs a per-host controller key for SSH monitoring;
the optional `build-customscript` wizard can also create one after the
operator explicitly asks to include it in a manually pasted script.

## Generated recipe and source selection

`default-recipe.jsonc` is an operator-local cache, not a portable repository
configuration. It is ignored because image IDs and account-level choices are
facts resolved by `configure`, not estate-wide constants.

The customScript keeps the bootstrap location as a placeholder until the
controller sends the API request. The configured repository branch is passed
both to the raw bootstrap URL and to `bootstrap-remote.py`; otherwise a
feature-branch wrapper could silently fetch `main`'s installer subtree.

## Notification backend and Mattermost boundary

The installer carries an explicit `NOTIFY_BACKEND` selector with
`telegram`, `mattermost`, and `none` values. The selector prevents a stale
credential in `.env` from silently choosing a different service, while the
Telegram default keeps existing v2 recipes compatible. Telegram credentials
must be supplied as a pair; Mattermost requires an HTTPS incoming-webhook URL
and rejects Telegram credentials in the same request.

Mattermost is intentionally integrated through its post-only incoming webhook,
not through a PAT or a REST client. The public consumer contract in
`nyxloom/mattermost/CONSUMER.md` gives the external hostname, producer
identity, channel binding, and secret-file location. A Netcup VM must use that
public URL. The remote bootstrap is a dependency-free `curl | python3 -`
entrypoint, so importing nyxloom's tightly coupled notification module would
be the wrong boundary. The small payload translation is kept local and the
notification path is best-effort: a webhook outage must not fail disk
partitioning or stage completion.

There is no `libraries/mattermost-client` extraction in this change. A Python
import package would need an underscore name such as `mattermost_client`; a
hyphen is suitable for a distribution/project name, not for an import. An
extraction becomes worthwhile only if another dependency-free consumer needs a
stable, tested webhook contract.

## SSH identity timing

The normal install path creates a per-host, per-date identity only after it
knows which host it is targeting. `login`, `configure`, and `build-customscript`
must not create one merely because the script was invoked. The generated
controller public key is passed through the bootstrap and removed by stage2;
the operator's persistent access key remains independent.

The Netcup account-level `sshKeyIds` choice is separate from that local
controller identity. Existing account keys are shown during the interactive
key step and the first is the default; selecting one prevents a new account
key from being registered. `--ssh-key-id ID` pins an existing key for direct
payload runs. A new account key is created only when no account key exists or
the operator explicitly selects the create-new choice.

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
reachable SSH service with no successful key is reported as `no keys match`,
while transport failure is reported as `SSH not open/responding`.

Actions that change state cannot safely fan out. ISO detach, rescue-system
deactivation, snapshot creation, and snapshot dry-run therefore refuse without
an explicit server ID. This keeps the convenient account-wide default limited
to inspection while retaining the API's server boundary for mutations.

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

## Test boundary

The Debian installer’s ordinary tests run in `tester-unified`. Real loop/swap
commit tests remain in `debian-install-v2`'s explicit QEMU/TCG VM lane. The
Netcup merge must not resurrect the historical privileged-container harness,
because Docker shares the host kernel and cannot isolate host-global swap.
