# Host access runbook (template)

Copy this page next to the controller's private host notes and replace every
`<PLACEHOLDER>`. Never commit the filled copy: it names a real server. The
shape is the one the controller kit already enforces: `hx.sh`, `remote-gate.sh`
and the queue read the same host table and run the same two checks on every
call, the provider identity check on the controller side and the guard on the
host side.

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `<ALIAS>` | the host alias used in the host table and in `CONTROLLER_REMOTE_GATE_HOSTS` |
| `<SERVER_ID>` | the provider's numeric server id of THIS host (one id, never a list) |
| `<SERVER_NAME>`, `<FQDN>`, `<SHORT_NAME>`, `<SERVER_IP>` | provider-side name, canonical hostname, static hostname, address |
| `<KEY_FILE>` | private key of the `runner` (or `root`) login; mode 600, outside every checkout |
| `<PIN_FILE>` | pinned `known_hosts` file holding only this host's key |
| `<IDENTITY_CHECK>` | the executable the host table's `api_check` runs (see below) |

## 1. Controller-side identity check (before every write or lifecycle command)

Ask the provider API about **only** `<SERVER_ID>` and assert, from the answer,
the server id, name, hostname, address and the running state. Any mismatch is a
non-zero exit and nothing else happens. `<IDENTITY_CHECK> <ALIAS>` is that
command; it reads the provider credential from the provider's own credential
file, never from the command line, and prints no secret. Register it as
`api_check` in the host table (an absolute path: it also runs from a temporary
worktree).

## 2. Pinned host key, strict checking

Record the host key once, AFTER the identity check passed, from the host's own
key (for example `ssh-keyscan` against `<SERVER_IP>`), compare its fingerprint
with the provider console or the host's own `/etc/ssh/ssh_host_ed25519_key.pub`,
and store it in `<PIN_FILE>`. Every ssh call then carries
`-o UserKnownHostsFile=<PIN_FILE> -o StrictHostKeyChecking=yes -o BatchMode=yes`
and `-i <KEY_FILE>`. A wizard or first-connect tool that connects with
`StrictHostKeyChecking=no` is only acceptable before the pin exists; switch to
strict immediately afterwards. After a reinstall the host key changes: re-pin
into a NEW file, never edit the old pin to make a failing check pass.

## 3. On-host asserts (the host table's `guard`)

Every remote command is wrapped so that it first asserts the machine is the
intended one and fails with `GUARD-FAIL` (exit 98) otherwise:

```sh
test "$(hostname -f)" = <FQDN> && test "$(hostnamectl --static)" = <SHORT_NAME> && test "$(cat /etc/vbpub/server-id)" = <SERVER_ID>
```

Writes repeat the hostname assert after the command as well. `hx.sh <ALIAS> '<command>'`
does identity check, guard wrapping and the strict ssh call in one step; use it
instead of a hand-written ssh line.

## 4. Checked upload (identity check, then the write)

Repeat the controller-side identity check immediately before an upload, then
stream the file so the on-host asserts run before anything is written:

```bash
# Description: upload one file to <ALIAS> after the identity check and the on-host asserts.
set -euo pipefail
<IDENTITY_CHECK> <ALIAS>
cat "$source" | ssh -i <KEY_FILE> -o UserKnownHostsFile=<PIN_FILE> -o StrictHostKeyChecking=yes -o BatchMode=yes \
  runner@<SERVER_IP> \
  'set -e; test "$(hostname -f)" = <FQDN>; test "$(cat /etc/vbpub/server-id)" = <SERVER_ID>; umask 077; cat > "<DESTINATION>"; test "$(hostname -f)" = <FQDN>'
```

## 5. Host table entries (two sample aliases)

Local file `hosts.local.json` (see `hosts.local.json.example` for the committed
placeholder file; the field names are identical). `ssh` and `api_check` are argv
arrays, `guard` is a remote shell condition, `queue_lanes` entries are
`project|lane` pairs. Gate hosts also appear in `CONTROLLER_REMOTE_GATE_HOSTS`;
an acceptance host stays out of it so no gate lands there.

```json
{
  "hosts": {
    "gate-host-a": {
      "ssh": ["ssh", "-i", "<KEY_FILE_A>", "-o", "UserKnownHostsFile=<PIN_FILE_A>",
              "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "runner@<FQDN_A>"],
      "api_check": ["<IDENTITY_CHECK_A>", "gate-host-a"],
      "guard": "test \"$(hostname -f)\" = <FQDN_A> && test \"$(cat /etc/vbpub/server-id)\" = <SERVER_ID_A>",
      "queue_lanes": ["scripts/netcup|suite"]
    },
    "acceptance-host-b": {
      "ssh": ["ssh", "-i", "<KEY_FILE_B>", "-o", "UserKnownHostsFile=<PIN_FILE_B>",
              "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "runner@<FQDN_B>"],
      "api_check": ["<IDENTITY_CHECK_B>", "acceptance-host-b"],
      "guard": "test \"$(hostname -f)\" = <FQDN_B> && test \"$(cat /etc/vbpub/server-id)\" = <SERVER_ID_B>",
      "queue_lanes": []
    }
  }
}
```

Check the result with `ctl-doctor.sh`: it verifies the schema, that every key
file exists with mode 600, that every pin file exists, that the identity command
is executable and that `CONTROLLER_REMOTE_GATE_HOSTS` names only aliases of this
table. It never prints a key, a server id or an address.
