# Notes on extracting information of JSONL

## Presets (`nyxloom-harness extract --preset NAME`)

The readback preset is the compaction recovery document. Its layout, in
document order: an OPT-IN goal anchor (`--goal-prompt`, the session's first
genuine user prompt, or `--goal-file`; harness framing and slash-command
wrappers excluded; included once per read-back and never truncated; use it
for a spawned or targeted session, a controller session omits it because its
anchor is its plan file), the most recent user/assistant prose from before
the `--since` cut (`--tail-prose-tokens 10000`; prose between the anchor and
the tail is dropped on purpose), then the delta after the previous read-back's
marker. The declared budget (`--budget-tokens`, 30000 for a controller session)
covers the whole document; truncation order is tail prose first (it also yields
to the delta's protected operator/interview material, which is never dropped),
then the delta's other classes, and the goal anchor never shrinks. The tail honours
`--redact-pattern` exactly like the delta. The document ends with a `[readback marker: ...]`
line: pass that marker as the next run's `--since` (or use `--since-file`).
Agent communication and the protected operator/interview material keep the
behaviour the preset already had.

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