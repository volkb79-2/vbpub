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
| `CONTROLLER_LOGS_DIR` | Gate-log search directory for `job-check.sh`; defaults to `$CONTROLLER_HOME/logs`. |
| `CONTROLLER_RUNS_DIR` | Optional run-record directory for Codex, `job-check.sh`, and `resume-usage-limited.sh`; defaults to `$CONTROLLER_HOME/runs`. Relative configured paths resolve under `CONTROLLER_HOME`; an environment override takes precedence over the local file. |
| `CONTROLLER_QUEUE` | Installed controller-side queue path; defaults to `$CONTROLLER_HOME/bin/host-sat-queue.sh`. |
| `CONTROLLER_GATE_LOCK` | Absolute path for the first gate slot; read it from host policy before running `gate-run.sh`. |
| `CONTROLLER_GATE2_LOCK` | Distinct absolute path for the second gate slot; both slot paths are required by `gate-run.sh`. |
| `CONTROLLER_REPO` | Checkout used for status and queue work; repo-local scripts derive it. Set it when the queue is installed under `CONTROLLER_HOME/bin`. |
| `CODEX_HOME_LUNA`, `CODEX_HOME_DEEPSEEK_VOLKER`, `CODEX_HOME_DEEPSEEK_GITHUB2`, `CODEX_HOME_REVIEW` | Explicit Codex config homes for Luna, the two pinned Open Design accounts, and review. |
| `CODEX_HOME_OVERRIDE` | Optional home override honoured only on the Luna route. |
| `CODEX_SQLITE_HOME` | Shared Codex session database path. |
| `CODEX_AUTO_CONTINUE` | Optional non-negative Codex resume count after a reasoning-only completed turn; defaults to `0` for Luna and `2` for DeepSeek. |
| `CONTROLLER_OD_STATE` | Optional absolute path for key-free Open Design account state; defaults to `$CONTROLLER_RUNS_DIR/opendesign-accounts.json`. |
| `CONTROLLER_OD_PREFER_EXPIRING` | Set to `1` to prefer the available Open Design account with the soonest recorded `window_end_epoch`; defaults to `0`. |
| `CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS` | Positive integer seconds to block an account when a 402 has no parseable reset time; defaults to `21600` (6 hours). |
| `PI_EMPTY_RETRY_LIMIT` | Non-negative continuation count for a pi run whose final assistant message is empty (no text, no tool call); defaults to `2`, `0` disables continuation. |
| `PI_CONTINUE_PROMPT_FILE` | Canonical continuation prompt; defaults to `scripts/controller/continue-empty-completion.md`. |
| `PI_REPORT` | Optional absolute expected report path for `pi-impl.sh`; equivalent to `--report`, unset skips the report check. |
| `PI_LIVE_INTERVAL_SECONDS` | Snapshot interval for a run's `.live` liveness file; defaults to `30`, must be a positive integer. |
| `PI_LIVE` | `0` starts no liveness sidecar at all (tests and inspection runs); defaults to `1`. |
| `JOB_STATUS_STALL_SECONDS` | `job-status.sh`: no pi-event age that reads as `STALLED`; defaults to `600`. |
| `JOB_STATUS_HUNG_TOOL_SECONDS` | `job-status.sh`: one tool call in flight longer than this reads as `HUNG-TOOL`; defaults to `1800`. |
| `RESUME_SKIP` | Space-separated run names excluded by `resume-usage-limited.sh`. |
| `RESUME_EXCLUDE_PREFIXES` | Space-separated run-name prefixes owned by another controller; defaults to `naf-`. |
| `RESUME_MAX_AGE_MINUTES` | Recent-run scan window for `resume-usage-limited.sh`; defaults to `180`. |
| `CMRU_VENV` | Selected project engine venv used by `release-from-wt.sh`. |
| `OPENCODE_COMMAND` | OpenCode wrapper executable; defaults to `opencode` on `PATH`. |
| `OPENCODE_PROVIDER` | Provider id used with the default DeepSeek model; defaults to `opendesign_github2`. |
| `OPENCODE_MODEL` | Full OpenCode provider/model identifier; overrides `OPENCODE_PROVIDER`. |
| `NETCUP_TOKEN_FILE` | Local provider token-file location for the configured `api_check` command. |
| `REMOTE_GATE_HOSTS` | Host table used by `remote-gate.py` when it is run directly; defaults to `hosts.local.json`. `remote-gate.sh` passes its own host table explicitly. |
| `CONTROLLER_REMOTE_GATE_HOSTS` | Required by `remote-gate.sh`: space-separated aliases that may receive a gate. The host table also names acceptance hosts, so the allow-list is local policy, never inferred from the table. |
| `CONTROLLER_REMOTE_HOSTS_FILE` | Host table for `remote-gate.sh`; defaults to `CONTROLLER_HOSTS_FILE`. A relative path resolves under `CONTROLLER_HOME`. |
| `CONTROLLER_REMOTE_API_CHECK` | Optional executable run as `<command> <alias>` instead of the host table's `api_check`; exit 0 means the provider-side identity matches. |
| `CONTROLLER_REMOTE_PYTHON` | Interpreter for the weight calculation and `remote-gate.py`; needs `tomllib` (Python 3.11+). Defaults to `python3`. |
| `CONTROLLER_REMOTE_GATE_PY`, `CONTROLLER_REMOTE_GATE_PY_REF` | Explicit `remote-gate.py` path, or the git ref (default `origin/main`) its `scripts/netcup/runner/remote-gate.py` is snapshotted from into `$CONTROLLER_HOME/tools/`. The runner never comes from the commit under test. |
| `CONTROLLER_REMOTE_SLOTS`, `CONTROLLER_REMOTE_BUDGET_MB` | Slot model: `slots` flock slots (1..8, default 4) of `budget / slots` MiB each (default budget 5120, the gate slice's MemoryHigh). Keep one value for every caller of one host. |
| `CONTROLLER_REMOTE_WEIGHTS_FILE` | Measured-peak table (default `$CONTROLLER_HOME/remote-gate-weights.tsv`, sample `remote-gate-weights.tsv.example`). |
| `CONTROLLER_REMOTE_WEIGHT_MB`, `CONTROLLER_REMOTE_SLOT_WAIT_S`, `CONTROLLER_REMOTE_WAIT_PSI`, `CONTROLLER_REMOTE_REPO_NAME`, `CONTROLLER_REMOTE_OUT` | Forced lane weight in MiB; seconds to wait for slots (default 7200, then exit 75); seconds the host job may wait for PSI (default 120); repository name on the host (default derived by `remote-gate.py`); result directory (default `$CONTROLLER_HOME/remote-gates`). |
| `CONTROLLER_REMOTE_DRYRUN`, `CONTROLLER_REMOTE_DRY_HOLD` | Non-empty `CONTROLLER_REMOTE_DRYRUN` stops after slot admission (nothing is sent to the host); `CONTROLLER_REMOTE_DRY_HOLD` seconds to keep the slots, for a contention check. |
| `CONTROLLER_GATE_RUN_SCRIPT`, `CONTROLLER_REMOTE_GATE_SCRIPT` | Test injection for `chain-run.sh`: the launcher used for plain lanes and for `remote:<lane>` lanes. Default: `gate-run.sh` / `remote-gate.sh` next to `chain-run.sh`. A chain's logs and marker go to `CONTROLLER_LOGS_DIR`. |
| `CTX_STATE_DIR` | Optional controller state directory for `hooks/ctx-hooks.py`; defaults to the hook input's own `cwd/.controller-state`. |
| `CTX_EXTRACT_CMD` | The extract command `hooks/ctx-hooks.py` runs; defaults to `nyxloom-harness` on `PATH`. |
| `CTX_READBACK_BUDGET_TOKENS` | Readback budget for a controller session; defaults to `30000` (a spawned session's controller passes a smaller flag or this variable). |
| `CTX_READBACK_TAIL_PROSE_TOKENS` | Recent-prose tail of the readback; defaults to `10000`. |
| `CTX_READBACK_GOAL_FILE` | Optional file pinning the readback's goal anchor instead of the session's first prompt. |
| `CTX_READBACK_GOAL_PROMPT` | `1` adds the session's first prompt as the goal anchor (`--goal-prompt`); implied by `CTX_SESSION_KIND=spawned`; a controller does not get it unless set. `CTX_READBACK_GOAL_FILE` wins. |
| `CTX_READBACK_REDACT_PATTERNS` | Regexes, one per line, forwarded to the extract as `--redact-pattern` (the flag `--redact-pattern` is repeatable too). A matching paragraph is replaced in the whole readback, tail included. The hook never logs, stores or prints a pattern (the manual fallback shows `<REGEX>`), but the extract's own placeholder names the pattern that matched, so use a pattern that matches the surrounding text, not the literal secret. An invalid regex makes the extract refuse, so the build fails closed. |
| `CTX_READBACK_TIMEOUT` | Seconds allowed for the readback build; defaults to `15` and is capped at the hard 20 s limit. |
| `CTX_HOOK_LOG` | Hook log path; defaults to `<state dir>/hooks/hook-log.jsonl`. |
| `CTX_SESSION_KIND` | `controller` or `spawned`; selects the pressure-note wording from 200k on (persist and continue vs stop at the boundary). Default: `spawned` inside an Agent-tool subagent (hook input has `agent_id`), otherwise `controller`. |

## `hosts.local.json`

The file follows the `remote-gate.py` private host-table shape: `hosts` maps
an accepted host alias to `ssh` (argv array), `api_check` (argv array), and
`guard` (remote shell condition). The queue additionally reads `queue_lanes`,
an array whose entries each contain exactly one `|` and non-empty project and
lane values. Host aliases must be accepted by the
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

A host that runs gates through `remote-gate.py` may also carry `runner_home`
(the runner account's home on the host), `cockpit` (the cockpit container name),
`image` (the default tester image) and `repos.<name>` (per-repository overrides
such as `run_gate_min`, `run_gate_bins`, `tester_image`); all four are optional
and validated by `remote-gate.py`, never by the shell kit. Keep one table:
`remote-gate.sh`, `hx.sh` and the queue read the same file.

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
