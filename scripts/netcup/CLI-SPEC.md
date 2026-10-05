# Netcup CLI specifications

The Netcup scripts are three separate executables that share one configuration
(`netcup.toml`), one client module (`netcup_scp_client.py`) and one version
(`VERSION`). Each has its own canonical grammar and semantic specification,
because the `cli-extended` generator owns exactly one marked region per spec
file. [`cli-extended.toml`](cli-extended.toml) declares all three.

| CLI | Specification | Review catalog | Findings | Generated manifest |
| --- | --- | --- | --- | --- |
| `install-host` | [`CLI-SPEC-install-host.md`](CLI-SPEC-install-host.md) | [`cli-review-install-host.toml`](cli-review-install-host.toml) | [`cli-review-findings-install-host.toml`](cli-review-findings-install-host.toml) | [`cli-surface-install-host.json`](cli-surface-install-host.json) |
| `monitor-task` | [`CLI-SPEC-monitor-task.md`](CLI-SPEC-monitor-task.md) | [`cli-review-monitor-task.toml`](cli-review-monitor-task.toml) | [`cli-review-findings-monitor-task.toml`](cli-review-findings-monitor-task.toml) | [`cli-surface-monitor-task.json`](cli-surface-monitor-task.json) |
| `scp-api` | [`CLI-SPEC-scp-api.md`](CLI-SPEC-scp-api.md) | [`cli-review-scp-api.toml`](cli-review-scp-api.toml) | [`cli-review-findings-scp-api.toml`](cli-review-findings-scp-api.toml) | [`cli-surface-scp-api.json`](cli-surface-scp-api.json) |

Maintenance loop (from this directory; see the
[`cli-extended` consumer guide](../../libraries/cli-extended/docs/CONSUMERS.md#the-review-loop)):

```bash
cli-extended surface sync --cli install-host   # likewise monitor-task, scp-api
cli-extended surface check --cli install-host
cli-extended audit --cli install-host
```
