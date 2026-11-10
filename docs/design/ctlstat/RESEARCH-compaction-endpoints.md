<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ctlstat-RESEARCH-compaction-endpoints.md -->
<!-- relation: ctlstat compaction research -->

**Status: COMPLETE**

# ctlstat research: compaction endpoints (official vs harness vs mechanical)

Date 2026-11-09. Read-only research; no estate files edited. Web content treated as untrusted data. Claims carry a URL or a file path; "UNVERIFIED" marks places where the docs are silent or only third parties speak. Local measurements were made read-only on `~/.claude/projects`, `~/.codex*/sessions`, and one `nyxloom-harness extract` run (output kept in the session scratchpad only).

## Summary (10 lines)

1. Anthropic offers three readable-summary compaction surfaces: Claude Code `/compact` and auto-compact, the Messages API `compaction` block, and `compact_20260112` threshold compaction. In all three a Claude model writes the summary as plain text, and the caller can steer it with instructions.
2. The API block is signed and tamper-evident (content or signature edits give 400 `compaction_content_mismatch`). Your own `instructions` (up to 16,384 chars) replace the default prompt entirely.
3. OpenAI's `/responses/compact` returns an encrypted, opaque `compaction` item that is "not intended to be human-interpretable". The docs describe no caller steering of the summary, and no pricing or cache statement.
4. Codex has two paths. On providers without remote compaction support it writes a readable summary using a fixed prompt, overridable via `compact_prompt`. On OpenAI providers it uses the opaque remote item. In 420 local Codex compaction records, 375 are opaque (median 43k chars of ciphertext) and 45 are readable.
5. pi, opencode and reasonix write LLM summaries (all `/compact [focus]`-style or API-triggered). pi is the most inspectable: a `CompactionEntry` stores the summary text, and a `session_before_compact` hook can supply a different summary. Per its docs opencode has no instruction channel and reasonix's summary storage is undocumented (UNVERIFIED).
6. Measured locally (96 Claude Code compactions): median pre-compaction context about 490-530k tokens, post about 18k, summary about 18-20k chars, 65-79 s. nyxloom measured the call at $0.54 with a warm cache and $2.14 with a cold one, for the same kind of work.
7. A purely mechanical extract (`nyxloom extract`) cannot replace the LLM summary: it recovers nothing that existed only in tool output, cannot de-duplicate superseded facts (append-only rule), and was larger than the official summary (72.8 KB for `operator-review` on a 40 MB session, versus a 20 KB official summary). There is NO fact-recall evidence in the E-series; V2/V5.9 were never run.
8. It does complement the LLM summary well: ledger (files, commits, effects, test results), verbatim operator turns, Q&A, stop state and pointers are free, deterministic, auditable and harness-neutral. That is the right "skeleton".
9. Recommendation: hybrid. A mechanical skeleton (ctlstat state plus extract ledger) is written before and re-injected after compaction; the prose layer (decisions, rationale, next steps) is written by an LLM on the warm resumed session; a mechanical lint checks the prose against the skeleton. Opaque endpoints are allowed only as a fallback.
10. Cheap evaluation: replay the existing 96 + 420 real compaction points, compare official summary, skeleton and hybrid on mechanically derived entity recall, re-orientation tool calls, bytes and dollars (section 5). Open questions are in section 6.

## 1. Comparison per vendor and harness

Legend: R = readable text, O = opaque. "Steer" = caller can influence what is kept. "External" = can be triggered by a process other than the live session.

### 1.1 Vendor-official compaction

| Aspect | Claude Code `/compact` and auto-compact | Anthropic Messages API (`compaction` block, on demand) | Anthropic threshold compaction (`compact_20260112`) | OpenAI Responses `/responses/compact` and server-side `compact_threshold` | Codex CLI |
|---|---|---|---|---|---|
| Who writes it | A Claude model, the session model; since v2.1.198 the summarizer inherits the session's extended-thinking setting ([context-window](https://code.claude.com/docs/en/context-window)) | A Claude model, same model, `system`, `tools` and thinking settings as the request ([compaction-on-demand](https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand)) | A Claude model, inside the request that crosses the trigger ([compaction-threshold](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold)) | OpenAI model; the output is a "compacted window" ([guide](https://developers.openai.com/api/docs/guides/compaction)) | Remote path: OpenAI `/responses/compact`. Local path: the session model, using a prompt ([compact.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs), `tasks/compact.rs` selects by `provider.capabilities().remote_compaction`) |
| Readable or opaque | R. Stored in the transcript as a user line with `isCompactSummary` after a `system/compact_boundary` line (nyxloom `design-context-lifecycle.md` section 1; I confirmed 97 summaries locally) | R plus a `signature`; `content` is plain text | R; `content` is plain text; null if no summary | O: "encrypted compaction item ... opaque and not intended to be human-interpretable"; `encrypted_content` maxLength 104,857,600 ([API ref](https://developers.openai.com/api/reference/resources/responses/methods/compact)) | Remote: O. Local: R, summary text prefixed with `SUMMARY_PREFIX` plus up to 20,000 tokens of recent user messages (`COMPACT_USER_MESSAGE_MAX_TOKENS`) |
| Size (measured or documented) | Local, 96 compactions: median 18-20k chars summary (max 36k); context about 490-530k to about 18k tokens; first post-resume call about 59-62k tokens incl. fixed prefix (nyxloom V1 add. 4) | `max_tokens` caps the call; 4,096 shown in docs; docs give no summary size guarantee | Not stated | Not stated; the docs say only "fewer tokens". Local Codex ciphertext: median 42,916 chars (n=375) | Local: 45 of 420 records carry readable `message`; the rest are the opaque form. Median whole record 160k chars (it also holds retained messages) |
| Steer | Yes: `/compact <instructions>`, CLAUDE.md "Compact Instructions", `/rewind` "Summarize from here" ([how-claude-code-works](https://code.claude.com/docs/en/how-claude-code-works.md), [context-window](https://code.claude.com/docs/en/context-window)). nyxloom measured that designed instructions steer the summary (design-context-lifecycle.md section 1) | Yes: `compaction.instructions`, non-blank, up to 16,384 chars, REPLACES the default prompt | Yes: `instructions` replaces the default prompt; `pause_after_compaction` lets you reinsert recent turns | Not documented ("The page does not say" per my fetch). The request has an `instructions` field, but the reference describes it as a system/developer message, not a summary prompt (UNVERIFIED whether it steers) | Local path: yes, `compact_prompt` / `experimental_compact_prompt_file`. Default prompt text is "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM..." ([prompt.md](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/compact/prompt.md)). Remote path: no known steering (UNVERIFIED) |
| External trigger | Yes, headless: `claude -p --resume <sid> "/compact <text>"` (nyxloom F1, measured on interactive-origin sessions); Agent SDK sends `/compact` and reports `compact_boundary` with `pre_tokens` and `trigger` ([slash-commands](https://code.claude.com/docs/en/agent-sdk/slash-commands.md)). A cron or ScheduleWakeup prompt `/compact` is NOT expanded (V5.1, NO) | Yes, you send the request | Only inside your own request | Yes, stateless HTTP endpoint | app-server `thread/compact/start` (research-external-compaction.md); `codex exec resume` has no compact flag |
| Audit and storage | Summary text in the JSONL; nyxloom reads it. Hook `PostCompact` receives `compact_summary` (ctlstat REQUIREMENTS 12.1) | Keep the block byte-exact; edits are rejected. The text can be read, logged and stored. Summarized images, documents and fetched URLs are gone | Same block | Cannot be read or verified. Factory: "You cannot read the compressed output to verify what was preserved" ([Factory](https://factory.com/news/evaluating-compression)) | Remote: cannot audit. Local: yes |
| Reuse on another model or harness | The text, yes (plain text; it is what `nyxloom extract` shows). The wrapper phrase "This session is being continued..." is Claude Code-specific | Text is readable and could be pasted as an ordinary message; sending the block to a different model is UNVERIFIED (docs silent in what I read) | As API block | Not addressed by the docs (UNVERIFIED). The blob is server-decryptable only (nyxloom README, `adapters/codex.py` on `encrypted_content`; openai/codex issue 25290 cited there) | Remote item: no. Local summary text: yes |
| Cost | A summarization call over the whole transcript. nyxloom: $1.68-1.85 on Opus over about 235k (V1 add. 4), about 8-13% of the work turn it protects. Cache dependent: $0.54 with a warm cache versus $2.14 cold, same work (V1 add. 5) | "billed and rate-limited like any other request"; reported in `usage.iterations`; re-sending the block adds no cost | Same, plus an extra sampling step; top-level usage excludes it | Pricing "Not covered" in the guide (UNVERIFIED) | Not documented (UNVERIFIED) |
| Cache behaviour | After compaction the cache is cold but small (V1 add. 3: 44k creation + 16k read). Compaction leaves background commands and subagents running | `cache_control` allowed on the block; keep the system-prompt breakpoint separate so only the summary is re-written | Same | Not covered in the guide; "do not manually prune" with `previous_response_id` | Not documented |
| Mechanical option offered by the vendor | `Context editing` clears old tool results and thinking "by rule instead of summarizing" ([overview](https://platform.claude.com/docs/en/build-with-claude/compaction)) | same | same | none that I found | `cold_resume_prune`-style pruning exists in reasonix, not Codex |

Conflict to resolve before design: nyxloom `design-controller-checkpoint-reset.md` F7 says `PreCompact` cannot steer a compaction (receives `custom_instructions`, only decision control is `block`, citing hooks.md), while ctlstat REQUIREMENTS 12.1 says PreCompact stdout "is used as instructions for the compaction summary". Treat as UNVERIFIED and test (cheap, throwaway session).

Other facts: Claude Code re-injects project CLAUDE.md, auto memory, skill descriptions, up to 5 recently read files (5,000 tokens each) and invoked skill bodies after compaction; hook-added context is "summarized with the rest of the conversation"; SessionStart hooks matching the `compact` source run and their output is added to the compacted context ([context-window](https://code.claude.com/docs/en/context-window)). That last item is a deterministic re-injection channel for a skeleton.

What the Claude Code summary looks like (local, 97 summaries): numbered sections "Primary Request and Intent", "Key Technical Concepts", "Current Work", "Pending Tasks", "Optional Next Step" in 82 of 97, plus "Errors and fixes" (81), "Files and Code Sections" (68), "All user messages" (67). So the official summary is already partly mechanical (verbatim user messages and file lists) and partly synthesized.

### 1.2 Other harnesses

| Aspect | pi | opencode | reasonix | (for reference) Claude Code / Codex |
|---|---|---|---|---|
| Source | [pi compaction.md](https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@0.81.1/docs/compaction.md) (official package docs) | [opencode v2 compaction](https://opencode.ai/v2/docs/compaction) | [GUIDE.md](https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/GUIDE.md); nyxloom [research-external-compaction.md](/workspaces/vbpub/nyxloom/docs/research-external-compaction.md) (v1.17.12; local binary is now v2.58.0, so that doc may be stale) | see 1.1 |
| Who writes it | An LLM summarizes the older messages; previous summary passed in as iterative context. Which model: not stated (UNVERIFIED) | The session model by default; `agents.compaction.model` can name another | A "summary model" (not specified; UNVERIFIED) | |
| Format | Fixed Markdown sections: Goal, Constraints and Preferences, Progress (Done, In Progress, Blocked), Key Decisions, Next Steps, Critical Context, plus `<read-files>` and `<modified-files>` blocks. File tracking is MECHANICAL (from tool calls, cumulative). Tool results are truncated to 2000 chars before the summarizer sees them | Fixed structure: objective, decisions, active work, blockers, next moves, relevant files | Not documented (UNVERIFIED) | |
| Readable and stored | Yes: `CompactionEntry` in session JSONL with `summary`, `firstKeptEntryId`, `tokensBefore`, optional `usage` and `details` | Docs silent on display; "earlier messages stay stored" | Docs mention "persisted compaction-summary counts"; storage of the text undocumented (UNVERIFIED) | |
| Steer | `/compact [instructions]`; extension hook `session_before_compact` can cancel or SUPPLY its own compaction object (summary, `firstKeptEntryId`, `tokensBefore`) | No instruction channel documented. Third-party write-ups of pre-v2 code mention an `experimental.session.compacting` plugin hook; I could not find it in the current plugin docs (UNVERIFIED, second-hand) | `/compact [focus]` only | |
| Triggers and tail | Auto at `contextTokens > contextWindow - reserveTokens` (16,384 default); keeps `keepRecentTokens` 20,000 verbatim; split-turn handling | `buffer` 15% (min 16k) kept free; keeps `compaction.keep.tokens` 15,000 verbatim; experimental `background_compaction` | `compact_ratio` 0.80 is "the sole auto trigger" (GUIDE); local config also had `soft_compact_ratio`, `tool_result_snip_ratio` (mechanical snipping), `compact_force_ratio`, `cold_resume_prune` (research doc; not in GUIDE) | |
| External trigger | Not documented as an external call; `/compact` interactive, hook or SDK (UNVERIFIED for headless) | Yes: `POST /api/session/<id>/compact` with optional idempotency `id`; async, follow `session.compaction.*` events; works with `compaction.auto=false` | No documented external RPC; research doc found `POST /compact` strings in a v1.17.12 binary (version-pinned, experimental) | |
| Cost visibility | Summary `usage` stored, so "session totals include summarization work" | Not stated | Not stated | |
| Takeaway for ctlstat | Best-documented adapter: summary text and tail boundary are in the log, so before/after fill and summary size are mechanically readable | Trigger is clean; summary content not steerable per docs, so use the successor path for designed compaction | Weakest: use resume + `/compact [focus]` interactive, or successor | |

### 1.3 Independent evidence on quality

- Factory (self-interested, their own method wins) probe-based evaluation on 36k+ real messages: overall scores Factory 3.70, Anthropic SDK 3.44, OpenAI `/responses/compact` 3.35 (0-5); artifact trail was the weakest dimension for everyone (2.19-2.45); compression 98.6-99.3%. Reported gaps are small, no variance or significance tests, authors are a vendor ([Factory](https://factory.com/news/evaluating-compression)). Use as direction only.
- JetBrains/TUM, "The Complexity Trap" (SWE-agent, SWE-bench Verified, 5 model configs): simple observation masking halves cost versus the raw agent and matches or slightly beats LLM summarization on solve rate; a hybrid cuts a further 7% versus masking and 11% versus summarization ([arXiv 2508.21433v3](https://arxiv.org/abs/2508.21433v3)). Scope caveat: SWE-agent style episodes, not multi-day controllers.

## 2. Mechanical (extract-based) versus LLM-written compaction

### 2.1 What `nyxloom extract` actually produces

Source: `/workspaces/vbpub/nyxloom/src/nyxloom/session_extract/README.md`.

- Deterministic, no model call, 4 adapters (Claude Code, Codex, Reasonix, opencode) for `extract`/`extract-lossless`. Real prose, operator turns, structured Q&A, an optional ledger (files touched, commits, branches, test results, external effects), stop state, and `--successor-brief` (Claude Code only; other adapters NL-31).
- Selection is a backward windowed walk with checkpoint scoring. The append-only / cache-stability rule means a span's selection never depends on later spans, so it cannot drop earlier content because a later turn made it redundant (README, "Why default toward dropping marginal content").
- Known hard limit, stated by the tool's own docs: it "structurally cannot recover information that only ever existed in raw tool output the assistant never restated in its own words"; mitigation is to prompt agents to narrate (README, "Future: hybrid agent-authored condensation").
- Relation to the harness summary: README "Future" already proposes the hybrid ("pay the model-call cost only for the segment that genuinely needs synthesis"), and E-011 proposes an opt-in `--llm-assist` tier that may only add tagged sentences, never rewrite kept text.

### 2.2 Measured evidence in the nyxloom E-series (and its gaps)

| Evidence | What it shows | What it does not show |
|---|---|---|
| E-011 (2026-09-10) | Qualitative read of `v1` (default, 2,515 words), `v6a` (7,512 words), `v5` (lossless, 11,420 words) of one real dstdns session. `v1` stops at the last real compaction and loses standing hazards from earlier the same day; `v6a` recovers them but carries audit-trail prose; `v5` is mostly filler. | No recall measurement; n=1 session; judgment by the author. |
| E-012 | Reading `v6a`: prose narration restated tool outcomes well for a disciplined narrator ("P102 implementer finished - 4 commits ... `8da835aa`->`f1b22d1a`"). Identified stranded items: AskUserQuestion rejected options, file-touch ledger, command ledger, artifact mentions, subagent index, test results; shipped as `ledger.py`. | "A terse agent leaves nothing for ANY mechanical extractor to recover." One session. |
| E-016 finding 2 | `nyxloom extract` right after a harness auto-compaction returned about 10 lines; the compact summary had already replaced the history. Extract only adds value BEFORE the harness threshold. | Single observation. |
| E-020 and the SUCCESSOR report | `--successor-brief` on a real lane: 14,174 bytes / 1,573 words / 151 lines, about 2.4% of the raw file; every claim is "the predecessor's own, not verified". | No resumption-success test of a successor primed this way against an LLM-compacted continuation. |
| V1 add. 3-5 (LLM compaction) | Three chained designed compactions on a worker: 3.9x-4.3x context reduction, stable ~59-62k floor, zero re-orientation reads before first productive edit, correct citation of a prior decision (`D-157 DA-1`) after a summary of a 167-turn segment. Cost $1.68-2.14 per compact, warm/cold gap about 4x. | Not graded against a facts checklist (V2, V5.9 were pre-registered but not run). |
| V2 / V2c / V5.9 (designed-vs-auto A/B, tiered brief quality, chained-summary fidelity) | Pre-registered in `design-context-lifecycle.md` section 5 and `design-controller-checkpoint-reset.md` section 4. | None of them has results in the experiments file (my grep found no result entries). So there is NO measured fact-recall comparison of mechanical versus LLM compaction anywhere in nyxloom. |
| E-010 (mitmproxy trace of the real compaction request) | Pre-registered only; "not run" per the HOWTO. | n/a |

### 2.3 New datum from this research (size only, not quality)

On `~/.claude/projects/-workspaces-vbpub/384f276e-...jsonl` (40.7 MB, 10 compactions), read-only: `nyxloom-harness extract --profile operator-review` took 1 s and produced 72,830 bytes (11,114 words, 614 lines; stopped by `--max-words`); `--profile all` produced 685,968 bytes (107,340 words); `--profile all --ledger --tool-calls intent-or-call` produced 1,035,683 bytes. For comparison, the median official Claude Code summary is about 20 KB. So the default extract is about 3.6x the size of an official summary and not a "state document": it is a recent-history readout. The 2.4% successor brief is the closest to a compaction-sized artifact. No quality conclusion can be drawn from this.

### 2.4 What a mechanical compaction loses

1. Facts that exist only in tool output (test failures' causes, probe results, file contents seen), unless narrated.
2. Rationale and rejected alternatives that the assistant did not write down. E-012 item 1: AskUserQuestion rejected options were dropped until fixed.
3. Supersession: a mechanical walk cannot know that a later turn replaced an earlier fact (an intermediate "299 passed" is kept or dropped by shape, not by relevance). This is by design (cache-stable append-only rule).
4. Synthesis of "what next": no judgment about the next three actions or standing hazards, which E-011 found to be the key difference between `v1` and `v6a`.
5. Size control: it is a window, not a summary; budget is enforced by words, so detail near the cut is arbitrary.

### 2.5 What an LLM compaction loses or risks

1. Opacity (OpenAI remote): cannot be audited, steered, or read by a different harness.
2. Artifact trail: Factory's weakest dimension for all three vendors; file paths and endpoints are dropped as "low-entropy" (OpenAI) or partially (Anthropic).
3. Hallucinated or carried-forward-unverified state: nyxloom V1 add. 4 names it ("a claim carried across a compaction is a claim that has not been re-measured") and mitigates it by instructing re-verification of UNVERIFIED-tagged claims.
4. Drift under repeated compaction (summary of summary). nyxloom saw no degradation across 3 chains by the output-size/floor metrics, but fidelity was not graded.
5. Latency and cost: 65-79 s median, $0.5-2.1 per compact, strongly cache dependent.
6. Attention: Claude Code's own docs say "detailed instructions from early in the conversation may be lost".

### 2.6 Can mechanical replace the LLM one?

For a worker whose durable truth is in files and git (the nyxloom doctrine: "summaries are indexes, not archives"), a mechanical skeleton plus the agent's own checkpoint prose is probably sufficient, and E-012 supports this for one disciplined narrator. For a controller, the state that matters (plan, ledger, queue state, pending operator decisions) is exactly what ctlstat already computes mechanically, so the mechanical share is even larger. The residual that only an LLM can write is small (decisions with rationale, rejected options, hazards, next steps), about 1-2k tokens. Replacement is not supported by evidence; complement is.

## 3. Hybrid proposal: mechanical skeleton + LLM prose

```
 compaction request (ctlstat/ctlctl, at a semantic boundary, session idle)
   |
   |-- L0 SKELETON (deterministic, written BEFORE compaction, stored as a file)
   |     header: harness, session id, model, cursor marker, fill before, boundary type
   |     state pointers: plan.toml / findings ledger / handoff file paths + sha256
   |     ctlstat snapshot (attention list, running jobs with pids/session ids, queue state)
   |     git: branch, HEAD, dirty files, unpushed commits
   |     session ledger: files touched, commits, branches, test results, external effects
   |     verbatim: original brief (or pointer+sha), last N operator turns, open/answered Q&A,
   |               last K assistant checkpoints, stop state
   |     gap markers + raw-log cursor wherever content was omitted
   |
   |-- L1 PROSE (LLM, same warm session, resumed with a compaction prompt)
   |     prompt: "Do not restate L0. Write only: decisions + why + rejected options,
   |              open questions, hazards/lessons, next 3 actions with expected completion signal.
   |              Cite L0 ids. Mark anything not verified as UNVERIFIED. <= 1500 words."
   |
   |-- L2 TAIL (harness-native): last 15-20k tokens verbatim (pi, opencode do this; Claude keeps preservedMessages)
   |
   |-- LINT (deterministic): every path / hash / id in L1 must exist in L0 or git; flag unknowns;
   |         every L0 pending-decision item must be mentioned or listed as dropped
   v
 compacted context = L0 + L1 (+ L2), injected per harness
```

Injection per harness (what each can accept):

| Harness | How L0 and L1 get in | Constraint |
|---|---|---|
| Claude Code | `claude -p --resume <sid> "/compact <L1 prompt>"` for L1; L0 re-injected after compaction through a SessionStart hook with the `compact` source (docs say its output is added to the compacted context) or the first resume prompt | Instructions text is only a request; the model may paraphrase, so L0 should not travel inside the prompt |
| Codex | Local-path providers: `compact_prompt` for L1; OpenAI provider uses the opaque remote item, so the hybrid should use the successor path (fresh session seeded with L0+L1) or force local compaction via the provider `remote_compaction` setting (config schema: "Remote context-compaction protocol; omission preserves provider defaults"; effect on a given route UNVERIFIED) | Do not rely on steering the remote item |
| pi | `session_before_compact` hook returns our `compaction` object (summary = L0+L1) | Best fit: the external endpoint plugs in directly |
| opencode | Trigger `POST /api/session/<id>/compact`, no instruction channel: use a successor session seeded with L0+L1 | Designed compaction not available per docs |
| reasonix | Resume + `/compact [focus]` with the L1 prompt, or successor | Summary storage undocumented, so verifying L1 is harder (UNVERIFIED) |
| Anthropic API | Compaction block must be sent byte-exact: send L0 as an ordinary message after the block | Block cannot be edited |

Warm-cache note: the L1 call should happen on the live resumed session so the transcript prefix is read from cache; nyxloom measured $0.54 warm versus $2.14 cold for the same compact (V1 add. 5), and the 1 h TTL means the compaction must be acted on promptly after a checkpoint (the doc's own "a CHECKPOINT sitting in the queue is actively decaying").

## 4. Recommendation

1. Do not replace LLM compaction with `nyxloom extract`. There is no evidence for it, and the structural limits (tool-only facts, no supersession, size) are real. Treat official compaction as an endpoint of a harness-neutral service, not as the service.
2. Define three compaction endpoint kinds in ctlstat/ctlctl and give each harness a capability row (readable, steerable, external trigger, hook for supplying a summary):
   - E1 in-harness: resume + compaction prompt (Claude Code, Codex local path, reasonix focus, pi `/compact`, opencode POST).
   - E2 successor: fresh session seeded with skeleton + prose (universal; the only option for user-stopped Claude subagents, E-020; the fallback for opaque Codex remote and for opencode designed compaction).
   - E3 supplied summary: harness accepts an externally written summary (pi `session_before_compact` hook).
3. Always produce the mechanical skeleton (L0) as the write barrier before any compaction, and keep it as the audit reference afterward. It is cheap, deterministic and needs no LLM; ctlstat already derives most of it. Reuse `nyxloom extract`'s ledger, stop-state, Q&A and adapters for it rather than its prose walk; fill NL-31 (non-Claude successor-brief) as the cross-harness piece.
4. Use the LLM only for the narrow prose layer (L1, <= 1.5k words), on the warm resumed session. Run the mechanical lint against L0 and git. Show the controller the diff (what L1 dropped versus L0) so the decision stays with it, in line with ctlstat's "tool proposes with reasons, controller decides".
5. Opaque endpoints (Codex remote, API `/responses/compact`): allowed as a fallback for workers, not for the controller or any session whose state is not entirely in files. If used, run the L0 re-injection after and record that the content could not be verified.
6. Run extract-based pieces BEFORE the harness threshold (E-016); a skeleton built from an already compacted transcript is near-empty.
7. Do not commit to the `--llm-assist` idea of E-011 yet; the hybrid above is the same idea with the split on a different line (mechanical state + LLM judgment) and can be tested first.

## 5. Evaluation method (cheap, uses data that already exists)

Corpus (free): 96 Claude Code compaction points (67 auto, 29 manual) and 420 Codex compaction records are in the local logs; the official summary at each point already exists. Cut points can be reproduced by truncating a read-only copy of the transcript at the `compact_boundary` and running experiments on throwaway copies (R12.9 approved; do not touch estate files; an `exp-compact-e2` scratchpad project already exists in `~/.claude/projects`, so check who owns it before reusing names).

Arms per cut point (aim for 10-15 cuts, mix of controller and worker sessions):

| Arm | Content | Cost |
|---|---|---|
| A0 | Official summary already in the log | 0 |
| A1 | L0 skeleton only (nyxloom ledger + ctlstat-like state; build from the transcript prefix) | 0 (seconds) |
| A2 | `nyxloom extract --successor-brief`-style brief | 0 |
| A3 | L0 + L1 written by a cheap model from the lossless dump | cents |
| A4 | L0 + L1 written by a warm resumed session with the designed prompt | $0.5-2 each |
| A5 | Opaque Codex item (only measurable by behaviour) | $ |

Metrics:

1. Size: tokens/bytes kept per arm and compaction ratio (official: about 490k to about 5k tokens of summary).
2. Entity recall (mechanical, no judge): extract entities (file paths, commit hashes, backlog and finding ids, function and branch names, numbers) from the post-cut segment's first N=50 tool calls and from the assistant's own references; recall = share present in the arm's context, plus share recoverable by a pointer in the arm. Also precision/hallucination = share of entities in L1 that exist nowhere in L0 or git (lint rate).
3. Probe QA à la Factory: per cut, 8-12 probes (recall, artifact, continuation, decision), gold answers derived from the transcript, answered by a fixed cheap model given only the arm's context; exact-match where possible, blind LLM judge from a different model family otherwise.
4. Resumption success: on 3-5 cuts only, resume a throwaway session with the arm and count re-orientation calls before the first productive edit (nyxloom F5 metric, readable with `jsonl-metrics.py`), re-reads of pre-cut files, and whether the next committed outcome matches the original. Expensive; do last.
5. Determinism and stability: re-run A1 twice (must be byte-identical); chain A4 three times and check entity recall decay.
6. Cost and latency: $ and seconds of the compaction call (`usage`, `total_cost_usd`), and first-resumed-turn cache creation; do warm versus cold (gap above and below 1 h).
7. Decision-loss specific check: pull every `AskUserQuestion` / operator decision in the pre-cut segment and check presence in each arm.

Decision rule: adopt the hybrid if A4 beats A0 on entity recall and decision presence by a clear margin at <= A0 size, and the lint finds no unknown ids; adopt mechanical-only (A1/A2) for workers if its entity recall and re-orientation count are within an agreed tolerance of A0.

## 6. Open questions

1. PreCompact: does its stdout steer the summary or not (F7 versus REQUIREMENTS 12.1)? Test on a throwaway session.
2. Does a compaction call on a resumed session read the transcript prefix from cache (design intent, V1 add. 5 numbers imply yes within the TTL), and does `/compact` on a session not changed since the last turn keep the same prefix bytes?
3. Can an Anthropic compaction block be sent to a different model, and can its text be re-used as a plain message without the signature? (Docs silent in what I read.)
4. OpenAI `/responses/compact`: is the `instructions` field used by the compaction model? Pricing and cache behaviour? Does the item work across models (the docs show the same model in both calls only)?
5. For Codex routes through a gateway (luna via ChatGPT account, deepseek via Open Design), which path runs: `RemoteCompactionSupport::V2` or local? Check a real rollout for a `compaction` item versus a readable `message`.
6. Does pi's `session_before_compact` hook work headless (print mode / RPC) so it can be our external endpoint? Which model does pi use by default for the summary?
7. opencode: any hook or config to replace the compaction prompt in v2 (the `experimental.session.compacting` hook is only second-hand)? Does `POST .../compact` accept more than `id`?
8. reasonix v2.58.0: where is the compaction summary stored and in what form; any external trigger? The nyxloom research (v1.17.12) is probably stale.
9. Controller survival (R12.5): do detached children survive terminating the controller session, and does a `claude --bg` host change the answer? (REQUIREMENTS 12.1 marks UNVERIFIED.)
10. How large should L0 be for a controller, and which ctlstat fields are in it by default (plan, findings, queue, host state)? Does the injected L0 count against the intended context limit (R12.4)?
11. Who owns the quality bar: is a human-visible diff (L0 versus L1) enough, or do we need a second reviewer model? (Doctrine: one Fable review per tool, rest Sonnet or Codex.)
12. Should the SessionStart(compact) hook replace L0 injection entirely for Claude, making the prompt-only part just L1?

## 7. Sources

Web (fetched 2026-11-09; treated as data):
- Anthropic compaction overview: https://platform.claude.com/docs/en/build-with-claude/compaction
- Anthropic on-demand compaction (beta `compact-2026-09-04`): https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand
- Anthropic threshold compaction (`compact_20260112`, beta header shown there as `compact-2026-01-12`): https://platform.claude.com/docs/en/build-with-claude/compaction-threshold
- Claude Code context window and what survives compaction: https://code.claude.com/docs/en/context-window
- Claude Code "When context fills up": https://code.claude.com/docs/en/how-claude-code-works.md
- Claude Code auto-compact window: https://code.claude.com/docs/en/model-config.md
- Agent SDK `/compact` and `compact_boundary`: https://code.claude.com/docs/en/agent-sdk/slash-commands.md
- OpenAI compaction guide: https://developers.openai.com/api/docs/guides/compaction
- OpenAI `/responses/compact` reference: https://developers.openai.com/api/reference/resources/responses/methods/compact (saved copy parsed locally: params, `CompactedResponse`, `Compaction object {encrypted_content, type, id}`)
- OpenAI conversation-state guide (short pointer only): https://developers.openai.com/api/docs/guides/conversation-state
- Codex source: https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs , `.../core/src/tasks/compact.rs` , `.../core/src/compact_remote_v2.rs` , `.../prompts/templates/compact/prompt.md` , `.../prompts/templates/compact/summary_prefix.md` , `.../core/config.schema.json` (keys `compact_prompt`, `model_auto_compact_token_limit`, `remote_compaction`)
- opencode v2 compaction: https://opencode.ai/v2/docs/compaction ; plugin docs checked, no compaction hook found: https://opencode.ai/docs/plugins/
- pi compaction: https://cdn.jsdelivr.net/npm/@earendil-works/pi-coding-agent@0.81.1/docs/compaction.md
- reasonix: https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/GUIDE.md ; https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/SESSION_MEMORY_RETRIEVAL.md (silent on compaction)
- Factory evaluation (vendor, self-reported): https://factory.com/news/evaluating-compression
- JetBrains/TUM "The Complexity Trap": https://arxiv.org/abs/2508.21433v3

Local files (read-only):
- <controller-program-dir>/reports/ctlstat-REQUIREMENTS.md (sections 10-12)
- /workspaces/vbpub/nyxloom/docs/design-context-lifecycle.md ; design-context-lifecycle-experiments.md (E-010, E-011, E-012, E-016, E-020, V1 addenda 3-5) ; design-controller-checkpoint-reset.md (F1-F12, section 4) ; research-external-compaction.md ; mitmproxy-compaction-trace-howto.md (not run) ; "Discusson on claude caching and compaction context trimming.txt"
- /workspaces/vbpub/nyxloom/src/nyxloom/session_extract/README.md ; /workspaces/vbpub/nyxloom/nyxloom-trove/4-backlog-inbox.md (B46) ; /workspaces/vbpub/nyxloom/nyxloom-trove/reports/nyxloom-SUCCESSOR-2026-10-REPORT.md
- Local measurement scripts and outputs (scratchpad only, not in the estate): `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/{scan_cc.py,scan_cc2.py,scan_cx.py,ex-operator-review.txt,ex-all.txt}`. Results: 96 Claude Code compactions (67 auto, 29 manual): auto pre median 492,618 tokens, post 17,786, summary 20,392 chars, 78.8 s; manual pre 531,382, post 18,109, 18,166 chars, 64.1 s. 420 Codex `compacted` records: 375 with an encrypted `compaction` item (median 42,916 chars), 45 with non-empty readable `message`.

## Appendix: UNVERIFIED list

- PreCompact stdout steering (conflicting local docs).
- OpenAI `/responses/compact` `instructions` effect, pricing, cache, cross-model use.
- Anthropic compaction block cross-model use.
- Codex remote versus local selection for specific gateway routes.
- opencode compaction plugin hook and `compact` body beyond `id`.
- reasonix summary model, format, storage, external trigger (v2.58.0).
- pi headless/external trigger and default summary model.
- Summarizer prefix reads from cache on a warm resumed session (consistent with V1 add. 5, not measured directly for the summary call).
