# Notes on extracting information of JSONL

## CODEX

### Example: with `jq`

```bash
jq -r 'select(.type=="event_msg" and .payload.item.type=="AgentMessage") | .payload.item.content[]? | select(.type=="Text") | .text'
    '/home/vscode/.codex/sessions/2026/09/13/rollout-2026-09-13T01-07-40-01a0984e-414f-7b32-80ee-932767086470.jsonl' | rg -n -C 8 -F -e
    'cmru-release-dirty-sync' -e '11ec4347' -e 'session-extract-follow-next' -e 'run-gate/coverage' | tail -220
```    

```text
...
444-- `cmru.release.log` confirmed the original run-gate failure plus the misleading dirty-rebase cleanup error.
445-- Added tests, SPEC/docs, README/CONSUMERS guidance, CHANGES, and backlog item KI-28.
446-
447-Validation:
--
6223-The scheduled observers remain active; no new gate observation is due yet.
...
```