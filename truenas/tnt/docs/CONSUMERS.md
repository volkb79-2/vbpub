# Using tnt

This guide shows a pasteable profile and commands for a vanilla TrueNAS v27 host. For the feature scope and its safety
choices, see the [design guide](DESIGN.md#1-goals-and-non-goals). The quick overview is in the [README](../README.md).

## Configure a profile

Start from the committed example and protect the copy before entering credentials:

```sh
cp tnt.toml.example tnt.toml
chmod 600 tnt.toml
```

The selected config file must be owned by your user and have exact mode `0600`. Edit the copy to match this shape:

```toml
schema_version = 1
default = "lab"

[profiles.lab]
url = "wss://truenas.example.invalid/api/current"
username = "admin"
auth = "api_key"
api_key = "REPLACE_WITH_API_KEY"
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["~/.ssh/id_ed25519"]
ssh_known_hosts = "~/.ssh/known_hosts"
```

Set `auth = "password"` and a `password` field to use the pinned client's supported v27 password login. The other
closed `auth` value is `"api_key"`; its key may instead come from an `api_key_file`. The closed `tls` values are
`"verify"` and `"insecure"`; the latter also requires `--insecure` on every command. `ws://` URLs are refused.

## Run the core workflows

Every command accepts `--profile NAME`, `--config PATH`, and `--json` where it returns data. Add `--allow-write` to
apply a planned mutation. Destructive operations also need `--confirm` with the exact target shown in the plan.

```sh
tnt dataset list --profile lab --json
tnt dataset create --profile lab --name tank/home --type filesystem --allow-write
tnt snapshot list --profile lab tank/home --json
tnt container list --profile lab --json
tnt vm list --profile lab --json
tnt user list --profile lab --json
tnt tui --profile lab
```

Use `--file spec.json` for fields beyond a workflow's minimal create options. Passwords are entered through a no-echo
prompt or `--password-file`, never a command-line argument. User SSH keys are loaded from a public-key file or
validated public-key text; private keys are not accepted or printed.

