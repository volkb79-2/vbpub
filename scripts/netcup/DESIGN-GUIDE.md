# Netcup SCP tool design guide

## Authentication is an explicit command

The SCP API needs a long-lived OAuth refresh token, but obtaining it is a
browser-mediated device-code flow. `login` performs that flow and writes the
token to `.env`; it does not create an SSH key or contact a server. This keeps
authentication usable before a target host has been selected.

`configure` is also local recipe setup after one read-only server/image lookup.
It likewise does not generate an SSH identity. The normal install path is the
only path that needs a per-host controller key for SSH monitoring.

## Generated recipe and source selection

`default-recipe.jsonc` is an operator-local cache, not a portable repository
configuration. It is ignored because image IDs and account-level choices are
facts resolved by `configure`, not estate-wide constants.

The customScript keeps the bootstrap location as a placeholder until the
controller sends the API request. The configured repository branch is passed
both to the raw bootstrap URL and to `bootstrap-remote.py`; otherwise a
feature-branch wrapper could silently fetch `main`'s installer subtree.

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
account-level `GET /imageflavours` or `GET /isoimages`. `scp-api.py` therefore
lists `/servers` first and queries each server when a read-only command has no
ID. It adds the source server to those rows so identical image names from two
VMs cannot be mistaken for one result. An explicit ID remains available for a
focused query, and filters are applied locally to the returned fields.

Actions that change state cannot safely fan out. ISO detach, rescue-system
deactivation, snapshot creation, and snapshot dry-run therefore refuse without
an explicit server ID. This keeps the convenient account-wide default limited
to inspection while retaining the API's server boundary for mutations.

The same boundary applies to ISO attachment and firewall assignment: both
require an explicit server, and firewall `set` requires an interface MAC plus
the complete replacement policy assignment. The CLI deliberately does not
pretend that guest-agent status is installer health, and it does not turn
firewall policy/rule creation into an unreviewed convenience command.

## Test boundary

The Debian installer’s ordinary tests run in `tester-unified`. Real loop/swap
commit tests remain in `debian-install-v2`'s explicit QEMU/TCG VM lane. The
Netcup merge must not resurrect the historical privileged-container harness,
because Docker shares the host kernel and cannot isolate host-global swap.
