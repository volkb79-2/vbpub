# External compaction for long-running carver sessions

Research date: 2026-07-24. Local versions inspected: reasonix v1.17.12, Codex CLI 0.145.0, Claude Code 2.1.218.

## Executive finding

There are three different mechanisms, and they should not be conflated:

1. An automatic compaction policy decides that the context is too large.
2. A compaction operation is an external event that a daemon can request now.
3. A durable memory/ground-truth store is reread after compaction.

Codex app-server has a first-class external request for (2): thread/compact/start. Claude Code has a headless equivalent by resuming a session and sending /compact <retention instructions> as a one-turn prompt; its Agent SDK exposes the same operation programmatically and reports a compact_boundary. Local reasonix has automatic compaction settings and an interactive /compact, but its visible CLI/ACP surface does not expose a documented external compaction RPC. Its Memory-v5 compact command is a configuration setter, not an immediate compaction request.

For nyxloom, the reliable design is: keep a bounded daemon-controlled cycle, persist the authoritative spine before compaction, request compaction through the strongest protocol available, wait for the completion/boundary event, reread the spine, and then continue. Compaction summaries are working context, not the source of truth.

## Per-tool findings

| Tool | External compaction mechanism available today | What the daemon can do | Durable-memory implication |
|---|---|---|---|
| **reasonix v1.17.12** | Automatic compaction is controlled by compact_ratio and compact_force_ratio; interactive /compact exists. The visible CLI has no standalone compact subcommand. The installed binary contains POST /compact and POST /summarize strings for its server, but that HTTP surface is not documented by the local help and should be treated as version-pinned/experimental. | A daemon can set policy in ~/.reasonix/config.toml, then continue with reasonix run -c or reasonix run --resume PATH. It cannot safely force the currently running CLI process through a documented CLI flag. An adapter may use the pinned reasonix serve endpoint only after a contract test; otherwise use bounded runs and let the next run compact. | --resume reopens the saved session and keeps whatever transcript/compaction artifacts reasonix persisted. It is not a guarantee of lossless memory. cold_resume_prune=true deliberately elides stale tool results when reopening beyond the provider cache window. Keep the authoritative state outside the transcript. |
| **Codex CLI 0.145.0** | codex exec resume [SESSION_ID] [PROMPT] and codex resume provide continuation, but codex exec --help exposes no compaction command. Codex app-server has the explicit JSON-RPC method thread/compact/start. | Run an app-server-owned session, call thread/compact/start with the thread ID, wait for contextCompaction item completion, then start the next turn. Do not concurrently control the same thread with a second codex exec process. | The compacted summary remains in the thread, but it is generated working context. Configure a retention prompt and maintain an external spine because details can be omitted or drift. |
| **Claude Code** | /compact [instructions] is an in-session command. Headless CLI can resume and send it as the next prompt. The Agent SDK can resume a session and send /compact with max_turns=1/maxTurns: 1; the SDK emits a compact_boundary system message. | Prefer Agent SDK for a daemon; CLI fallback is claude -p --resume SESSION --max-turns 1 "/compact RETENTION...". Wait for the boundary/result before sending the next work prompt. | Claude’s compacted summary replaces older messages. Root instructions and reinjected memory persist, while some path-scoped context must be reread. Treat the spine as authoritative. |

Sources: [reasonix guide](https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/GUIDE.md), [reasonix ACP specification](https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/ACP.md), [Codex app-server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md), [Codex config schema](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json), [Claude CLI usage](https://code.claude.com/docs/en/cli-usage), [Claude commands](https://code.claude.com/docs/en/commands), [Claude context window](https://code.claude.com/docs/en/context-window), and [Claude Agent SDK slash commands](https://code.claude.com/docs/en/agent-sdk/slash-commands).

## 1. reasonix details

### Local configuration and commands

The inspected ~/.reasonix/config.toml has:

~~~toml
[agent]
memory_compiler = { enabled = true, verbosity = "observe" }
soft_compact_ratio = 0.5
tool_result_snip_ratio = 0.6
compact_ratio = 0.8
compact_force_ratio = 0.9
cold_resume_prune = true
~~~

The comments say that compact_ratio attempts compaction when the prompt reaches that fraction of the provider context window, while compact_force_ratio is the high-water/forced threshold. tool_result_snip_ratio shortens stale tool output before summary compaction. cold_resume_prune removes stale tool results when reopening a session after the provider cache window.

reasonix run --help confirms:

~~~text
reasonix run -c|--continue
reasonix run --resume PATH
reasonix run --copy
~~~

--resume PATH takes precedence over --continue; --copy duplicates the session when continuing/resuming. These are session selection/continuation controls, not compaction triggers.

### What memory-v5 compact does

In an isolated temporary REASONIX_HOME, the command:

~~~text
reasonix config memory-v5 compact
~~~

changed the user config to:

~~~toml
memory_compiler = { enabled = true, verbosity = "compact" }
~~~

and reasonix config memory-v5 status then reported enabled/compact. No session path, live process, or prompt was accepted. Therefore this command changes the Memory-v5 policy for a later/new configuration load; it does not compact an active transcript immediately. A daemon must not treat a config flip as an in-flight event. Restarting or starting the next run -c is the safe boundary at which to assume the new config is loaded.

The installed v1.17.12 binary contains /compact, CmdCompact, and CompactNow, so interactive reasonix can compact locally. However, reasonix --help, reasonix run --help, and the local ACP handshake do not expose a documented external compact method. The local ACP capabilities advertised loadSession, resume, and sessionSteer was absent. Current upstream reasonix documents a steering extension only when the agent advertises it; do not assume it is available in v1.17.12. See [reasonix ACP steering](https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/ACP.md).

The installed binary also contains server route strings including POST /compact. This is evidence that a local server integration may exist in this build, not a stable public contract. If nyxloom uses it, pin the reasonix version and add an integration test that starts reasonix serve, creates/resumes a session, posts the request, verifies the compaction result, and checks that a subsequent prompt uses the compacted session. Do not make the daemon depend on an unverified string found in a binary.

There is an additional release caveat: current reasonix main-v2 documentation says the old Memory-v5 execution compiler was removed from current releases, while v1.17.x was an earlier release family. This makes the local config behavior especially version-sensitive. See [reasonix session/memory retrieval](https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/SESSION_MEMORY_RETRIEVAL.md).

### Resume and memory

Resume preserves the saved session representation, including any summary that reasonix has already written, but it does not create an independent, lossless memory. Compaction can omit raw messages; cold-resume pruning can intentionally omit old tool output. A successful --resume therefore means “continue from persisted session state,” not “all facts are recoverable from the session.”

### Recommended reasonix mode for a daemon

Until a documented, tested external compaction endpoint is available, use bounded daemon-controlled cycles:

~~~bash
# The daemon writes/validates the spine first, then:
reasonix run -c -dir "$WORKTREE" "$NEXT_CARVE_PACKET"
~~~

For a known session file:

~~~bash
reasonix run --resume "$SESSION_FILE" -dir "$WORKTREE" "$NEXT_CARVE_PACKET"
~~~

Keep the automatic policy enabled and tune compact_ratio, compact_force_ratio, tool_result_snip_ratio, and cold_resume_prune in the user config. If the daemon must interrupt a still-running turn at an exact threshold, use a tested reasonix serve/HTTP adapter or add a supported ACP compact method; changing memory-v5 is not enough.

## 2. Codex details

The local ~/.codex/config.toml contains model, reasoning, approval, sandbox, and trusted-project settings, but no compaction settings. Local codex exec --help has resume, --last, JSON output, and prompt/stdin controls; it has no compact flag.

The current Codex app-server protocol documents:

~~~json
{"method":"thread/compact/start","id":25,"params":{"threadId":"thr_blah"}}
~~~

The request returns immediately with {}. Compaction progress is reported through normal item notifications, including an item whose type is contextCompaction; the daemon should wait for its completion before starting more work. The same app-server supports thread/start and thread/resume.

Start the daemon-owned protocol process with:

~~~bash
codex app-server --stdio
~~~

For a socket/websocket deployment, use the installed app-server's --listen URL form instead; keep one app-server owner for the thread.

The daemon sequence should be:

~~~text
1. Start one Codex app-server process, normally with --stdio or a daemon-owned Unix/socket listener.
2. thread/resume the carver's thread ID.
3. Persist/validate the external spine.
4. thread/compact/start { threadId }.
5. Wait for contextCompaction item completion and the turn/operation completion notification.
6. Start the next turn with the next carve packet.
~~~

For policy-driven automatic compaction, the public config schema exposes model_auto_compact_token_limit, model_auto_compact_token_limit_scope, compact_prompt, and experimental_compact_prompt_file. The daemon can set these in the config or at launch with a -c override, for example:

~~~bash
codex app-server -c model_auto_compact_token_limit=120000
~~~

Use the app-server RPC for an externally timed event; use compact_prompt for the retention policy that automatic/manual compaction should follow. Some Codex versions/issues report auto-compaction not firing until a turn boundary and remote compaction timeouts in long-lived sessions ([headless auto-compaction issue](https://github.com/openai/codex/issues/16033), [remote compaction timeout issue](https://github.com/openai/codex/issues/18829)). The daemon should therefore monitor notifications, set a timeout, and recover from a failed compaction by rereading the spine and resuming/forking a fresh thread.

If app-server is unavailable, the fallback is:

~~~bash
codex exec resume "$SESSION_ID" "$NEXT_CARVE_PACKET"
~~~

This resumes and continues, but does not give the daemon a reliable “compact now” operation. Do not depend on a synthetic /compact prompt in Codex; prompt-guided/manual compaction behavior has varied across releases ([Codex compaction discussion](https://github.com/openai/codex/discussions/5799)).

## 3. Claude Code details

Claude’s documented command is /compact [instructions]: it summarizes the conversation so far and accepts focus/retention instructions. Commands sent while Claude is responding are queued and run after the current turn. The context-window documentation says compaction replaces older conversation messages with a structured summary; persistent root instructions and reinjected auto-memory remain, while some path-scoped context may need to be reread.

### Headless CLI equivalent

The daemon can resume a session, give compaction one turn, and request machine-readable events:

~~~bash
claude -p --resume "$SESSION_ID" \
  --output-format stream-json \
  --max-turns 1 \
  "/compact $RETENTION_INSTRUCTIONS"
~~~

After observing successful completion, resume the same session with the next carve packet:

~~~bash
claude -p --resume "$SESSION_ID" \
  --output-format stream-json \
  "$NEXT_CARVE_PACKET"
~~~

--continue can be used instead of --resume SESSION_ID when the daemon owns the most recent session. --fork-session is for branching and should not be used when the objective is to compact the same session.

### Agent SDK equivalent

The Agent SDK is the better programmatic interface: resume by session ID, send /compact with maxTurns: 1 (TypeScript) or max_turns=1 (Python), and consume the compact_boundary system message. The boundary includes metadata such as pre-compaction token count and trigger. The SDK documentation explicitly supports slash commands through the SDK; the low-level Anthropic API alone does not provide Claude Code’s session compaction operation. See [SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions) and [SDK slash commands](https://code.claude.com/docs/en/agent-sdk/slash-commands).

## Recommended nyxloom daemon mechanism

Use a provider adapter with one common lifecycle:

~~~text
daemon threshold
    -> stop issuing new work
    -> write/validate spine revision N
    -> external compact request
    -> wait for provider completion/boundary
    -> reread spine revision N
    -> send next carve packet
~~~

Provider-specific implementation:

* **Codex:** own the session through app-server. Call thread/compact/start with the thread ID, wait for contextCompaction completion, then issue the next turn. Configure compact_prompt with the retention template below. This is the recommended production path because the external trigger is a documented protocol method.
* **Claude Code:** use Agent SDK resume plus /compact and max_turns=1, watching compact_boundary; use the CLI command above if an SDK integration is not practical.
* **reasonix:** use bounded reasonix run -c/--resume cycles with automatic compaction policy. If “compact exactly now” is a hard requirement, use a version-pinned reasonix serve adapter only after an endpoint contract test; otherwise the daemon should not claim it can externally force the local v1.17.12 CLI. memory-v5 compact can be applied before a new run, but is not the event.

The daemon should not run two controllers against the same session. A single owner must serialize resume, compact, and continue operations. Record session/thread ID, compaction request ID, start/end time, pre/post token telemetry, and the spine revision used for retention.

## Retention template for a spine/roadmap/backlog carver

Pass a compact version of this as the provider’s compaction prompt or /compact argument:

~~~text
COMPACTION RETENTION CONTRACT — NYXLOOM CARVER

Always KEEP:
- The objective, scope, explicit non-goals, and the current carver phase.
- The authoritative files and their paths: spine, roadmap, backlog, handoff,
  decisions, gate/test instructions, and any state file. These files are ground
  truth and must be reread after compaction before making decisions.
- Current task ID, status, acceptance criteria, dependencies, locks, and stop/escalate
  conditions.
- Decisions already made, their rationale, unresolved decisions, and constraints from
  the user or project doctrine.
- Files changed, whether changes are uncommitted/committed, relevant branch/worktree,
  and the exact last gate command/result.
- Reproducible blockers and errors: command, target, essential output, and next action.
- The next one to three concrete actions and the expected completion signal.
- The spine revision/checksum and this rule: if the summary conflicts with the spine,
  filesystem, or gate output, trust the durable artifact and report the conflict.

It is safe to DROP:
- Repeated file contents, full diffs already present on disk, and verbose tool output.
- Raw internal reasoning, discarded hypotheses, duplicated prompts, and routine logs.
- Completed-step narration once its durable outcome/path/test is recorded.
- Stale token/cache metadata and provider implementation details.
- Speculative branches that were not selected, unless their rejection is a recorded
  decision needed to prevent repeating the work.

Do not invent progress, test results, file contents, or decisions. After compaction,
reread the authoritative files and inspect current filesystem/git state before acting.
~~~

The template deliberately retains references and outcomes rather than copying large artifacts into the context. The compacted context should contain enough to resume safely, while the spine contains enough to reconstruct anything that was dropped.

## Durable-ground-truth mitigation

Put the truth outside the model transcript, preferably in small structured files in the carver worktree or daemon state directory:

* SPINE.md: objective, invariants, current phase, decisions, blockers, and next action.
* ROADMAP.md: ordered milestones with stable IDs and status.
* BACKLOG.md: actionable items, owner, dependencies, and acceptance criteria.
* DECISIONS.md: append-only decision records, including rationale and rejected alternatives.
* CARVER_STATE.json: session/thread ID, spine revision, last compaction, gate result, and timestamps.

Write these atomically before requesting compaction. Include a monotonic revision and, if practical, a checksum. On every resumed cycle, the first instruction should require rereading them and reconciling the summary against current git status, the diff, and the gate. Never store an important fact only in the conversation.

This makes compaction safely lossy: a dropped fact is recoverable by rereading the spine or inspecting the worktree. It also bounds summary drift, because the agent repeatedly re-anchors to stable IDs and current artifacts instead of recursively trusting the previous summary.

Remaining failure modes to monitor are: a fact omitted from both the summary and the spine; a stale spine that was not updated before compaction; summary drift across repeated compactions; a compaction request queued behind a still-running turn; provider timeout; and a daemon accidentally resuming a fork/copy rather than the canonical session. Use pre-compaction write barriers, serialized session ownership, completion/boundary checks, post-compaction rereads, and a fresh-session recovery path when validation fails.
