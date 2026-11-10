# Controller configuration

The committed `*.example` files contain field names and placeholders only.
Copy them to `$CONTROLLER_HOME` and add local paths and host facts there. The
controller scripts derive runtime files from `CONTROLLER_HOME`, defaulting to
`$HOME/controller`.

## `controller.local.env`

This is shell syntax, loaded by `scripts/controller/lib.sh`. Set the selected
Codex profile directories, shared Codex session database, controller checkout,
OpenCode command if needed, chosen CMRU engine venv, and provider token-file
location here. Store secret values in the provider's supported credential
store; this file should contain paths and settings, not token contents.

Useful settings:

| Name | Purpose |
| --- | --- |
| `CONTROLLER_HOME` | Local state root; defaults to `$HOME/controller`. |
| `CONTROLLER_QUEUE` | Installed controller-side queue path; defaults to `$CONTROLLER_HOME/bin/host-sat-queue.sh`. |
| `CONTROLLER_GATE_LOCK` | Required shared gate-lock path; read it from host policy before running `run-gate`. |
| `CONTROLLER_REPO` | Checkout used for status and queue work; repo-local scripts derive it. Set it when the queue is installed under `CONTROLLER_HOME/bin`. |
| `CODEX_HOME_LUNA`, `CODEX_HOME_DEEPSEEK`, `CODEX_HOME_REVIEW` | Explicit Codex config homes for each route. |
| `CODEX_SQLITE_HOME` | Shared Codex session database path. |
| `CMRU_VENV` | Selected project engine venv used by `release-from-wt.sh`. |
| `OPENCODE_COMMAND` | OpenCode wrapper executable; defaults to `opencode` on `PATH`. |
| `NETCUP_TOKEN_FILE` | Local provider token-file location for the configured `api_check` command. |
| `REMOTE_GATE_HOSTS` | Host table used by `remote-gate.py`; defaults to `hosts.local.json`. |

## `hosts.local.json`

The file follows the `remote-gate.py` private host-table shape: `hosts` maps
an accepted host alias to `ssh` (argv array), `api_check` (argv array), and
`guard` (remote shell condition). The queue additionally reads `queue_lanes`,
an array of `project|lane` strings. Host aliases must be accepted by the
installed `remote-gate.py`; its allowed-alias set is authoritative.

Example shape (replace every placeholder locally):

```json
{
  "hosts": {
    "accepted-host-alias": {
      "ssh": [
        "ssh", "-i", "/local/path/to/key",
        "-o", "UserKnownHostsFile=/local/path/to/known_hosts",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes", "runner@host.example.invalid"
      ],
      "api_check": ["/local/path/to/provider-check", "accepted-host-alias"],
      "guard": "test \"$(hostname -f)\" = host.example.invalid && test \"$(cat /etc/vbpub/server-id)\" = REPLACE_ME",
      "queue_lanes": ["scripts/netcup|suite"]
    }
  }
}
```

`api_check` must perform the provider-side identity check before remote work.
Use an absolute command path so it works from the controller home and from a
temporary worktree when `remote-gate.py` repeats the check.
`hx.sh` runs it on every invocation; the queue runs it before starting a lane.
The remote `guard` must check the host's canonical hostname and server identity.
Every host command runs through that guard. Do not put private key contents,
provider token contents, or real server addresses in the example file.

The queue's resource limits are environment overrides for operator policy,
not host identity data. They are optional; defaults are documented in the
runner README and can be changed through the local environment file when the
operator's policy changes.
