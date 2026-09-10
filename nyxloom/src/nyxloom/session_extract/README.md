# session_extract

> North star: a mechanical, cross-CLI, structured-Q&A-preserving,
> content-aware checkpoint extractor.

`nyxloom extract <session-log>` turns a coding-agent CLI's raw session
transcript into a compact, resumable brief — without an LLM round-trip.
It is a `/compact` alternative you can run *outside* the model: point it
at a session log, get back the real operator turns, the structured Q&A,
and the assistant's own checkpoint/summary prose, windowed to a word
budget, as delimited text (paste into a fresh session) or JSON (feed a
second-stage tool).

Every word in the north-star sentence is load-bearing and rules out a
simpler design:

- **mechanical** — no model call. Pure parsing + scoring + windowing, so
  it's fast, free, deterministic, and safe to run on secrets-bearing
  transcripts without sending them anywhere new.
- **cross-CLI** — one core selection/windowing algorithm, one adapter per
  session-log format. Claude Code was the first real consumer; Codex and
  opencode adapters exist so the core's assumptions get pressure-tested
  against genuinely different schemas, not just re-skinned for a second
  Claude-shaped format.
- **structured-Q&A-preserving** — `AskUserQuestion` (and its per-CLI
  equivalents) is the highest-signal content in a session: it's the
  moment an operator's actual decision got recorded verbatim. It is
  never trimmed by length rules and never silently dropped.
- **content-aware** — "checkpoint" is not "long message." See
  `classifier.py`: a scored set of structural/lexical signals (headers,
  closure language, direct-address openers, a "followed by a pause"
  lookahead), not a length threshold.

## Why this exists (design history)

This package grew out of watching an operator hand-build a resume
excerpt for a fresh session: manually re-reading a long Claude Code
transcript, cherry-picking their own prompts, the assistant's summaries,
and Q&A answers, and hand-inserting delimiters between them. That excerpt
worked well as a fresh-session seed. The question was whether the same
selection could be done mechanically, and the answer — validated against
that exact hand-built excerpt and its source session (see "Validation
against a real hand-curated excerpt" below) — is yes, with caveats that
shaped the design below.

### Why not just use each CLI's own `/compact`?

`/compact` (and Codex's `thread/compact/start`, and reasonix's
`compact_ratio` auto-compaction) is an LLM-driven summarization: it costs
a model call, its output is non-deterministic, and it discards the
original text — you get *a* summary, not a curated slice of the *actual*
transcript. Prior research on this exact boundary lives in
`nyxloom/docs/research-external-compaction.md` (per-tool compaction RPCs
as of 2026-07-24); this tool is deliberately the complementary mechanical
half — it never asks a model to compact anything, it decides what to
*keep* from what's already there.

### Why not just use length?

The operator's own instinct (episode 2 of the design conversation) was
"last 5 checkpoints, full comments in recent history, only long comments
further back." That is close, but two real gaps only showed up once the
tool's actual output was diffed against the operator's real hand-curated
excerpt, run against its own source session:

1. **A short, concrete finding survives; a short procedural aside
   doesn't** — the operator kept "Found it — a `pgrep` pattern bug" but
   dropped "Now let's fix that," despite near-identical length. This is
   `classifier.has_finding_signal()`: a finding-opener regex, a
   code-reference-in-backticks pattern, and a filename-mention pattern,
   checked independently of the length bars and _OR_'d into the keep
   decision at any distance from the newest checkpoint.
2. **"Recent history: keep everything" is too permissive** — running the
   tool with `--checkpoints 5` and `--checkpoints 10` against the real
   excerpt's span produced almost identical output, which meant the
   whole span held only ~5 real checkpoints and so "recent" (by the
   original design) covered nearly the *entire* transcript. Unconditional
   keep-everything in that window let through large amounts of low-value
   procedural narration the operator's own excerpt never kept. The fix
   was a second, lower length bar for the recent window
   (`recent_comment_chars`, default 40) instead of no bar at all — still
   far more lenient than the older window's `long_comment_chars` (180),
   but not unconditional. This is a deliberate, evidence-based deviation
   from the original "recent = keep everything" instruction; it has not
   yet been re-confirmed with the operator in a live conversation, only
   validated numerically against the one real excerpt available.

Both fixes are real bugs a naive length-only reading of the original
spec would have shipped with. A labeled corpus of more than one
hand-curated excerpt would let the thresholds be tuned properly instead
of eyeballed against a single data point — noted as a real limitation,
not hidden.

### Why a backward (newest-first) walk, not forward-scan-then-trim?

Per operator direction: walking from the newest event backward makes
"stop once the checkpoint target or word budget is satisfied" a natural
early exit, and guarantees the *oldest* material is what gets trimmed
first when the budget is tight — nothing already accepted is ever
un-accepted. See `select.py`'s module docstring for the full windowing
contract.

### Why ignore branching / sidechains?

Claude Code session logs are a tree (`parentUuid` links), and a node can
have more than one child. Before assuming a chronological linear walk was
safe, this was checked against real session data rather than assumed:
every branch point found in the sessions inspected was a **parallel
tool-call fan-out** (several `tool_use` blocks under one assistant turn,
each answered by its own `tool_result`), never a genuine
retry/edit-and-resubmit branch. A plain chronological (timestamp/seq)
walk is therefore sufficient — no parent-chain tracking needed. If a real
edit-retry branch ever surfaces in the wild, this assumption should be
revisited; the adapter deliberately keeps `NormalizedEvent.seq` as a
plain integer index rather than a graph position so that a future
tree-aware walk wouldn't require reshaping the core data model.

### Why is `compact_boundary` a hard stop, not ignored?

Content on the far side of a compaction boundary (automatic
`compact_boundary`/`isCompactSummary`, or an explicit `/compact`/`/clear`)
already got summarized away by the CLI itself once — re-extracting raw
prose from before that point would resurrect detail the operator (or the
CLI) already decided to compress. The walk keeps the boundary itself (as
a `LIFECYCLE_MARKER` note) and stops there; only content *newer* than the
boundary is eligible for selection.

### On "thinking" blocks

Left off by default (`include_thinking=False`). Whether extended
thinking content is worth surfacing is genuinely unclear — it can carry
reasoning not restated in the final answer, but it's also often noisy
scratch work. `--include-thinking` exists as a config knob precisely so
this can be A/B'd against real sessions rather than decided from
first principles.

## Cross-CLI adapter findings

Skimmed real session logs from Claude Code, Codex, and opencode before
finalizing the `SessionAdapter` protocol (`adapters/base.py`), to avoid
designing an interface that only fits Claude Code's shape. Findings, in
descending order of how much they constrained the interface:

- **Claude Code** (`adapters/claude_code.py`) — flat JSONL, one record
  per line, `type` discriminates `user`/`assistant`/`system`/housekeeping.
  `AskUserQuestion` batches are pre-rendered by the harness into a single
  `"The user answered: ..."` tool_result string covering every question
  in the batch — the adapter doesn't need to reconstruct pairing itself.
  Two real schema quirks found only by running against live files, not
  documentation: the first line of a real file is often a housekeeping
  `"mode"` record with no `sessionId`/`parentUuid` (sniff must scan
  several lines, not just line 1), and background Task-tool completions
  arrive as `<task-notification>` blocks inside an otherwise
  operator-turn-shaped record (`type: "user"`, plain string content, no
  `isMeta` flag) — real controller-injected noise that must be filtered
  by content, since the schema alone doesn't distinguish it from a real
  operator prompt.
- **Codex** (`adapters/codex.py`) — flat JSONL, but two layers: a clean
  `event_msg` layer (`user_message`/`agent_message`/`context_compacted`)
  that's almost a 1:1 fit for this tool's event model, and a raw
  `response_item` layer (the literal model-call transcript, including
  `function_call`/`function_call_output` and `reasoning` items) that's
  much harder to parse cleanly. The adapter deliberately uses only the
  `event_msg` layer. Two gaps, documented in the adapter's own docstring
  rather than papered over: no `AskUserQuestion`-equivalent structured
  Q&A signal was found in the samples inspected (Codex's interaction
  model may simply not have one), and `reasoning` items' `summary` array
  was empty with `encrypted_content` opaque in every sample checked —
  Codex is open-source, so it's plausible the real reasoning text is
  recoverable from a local build with the right flag/log level rather
  than genuinely inaccessible; this is exactly what the Codex-encryption
  research fork launched alongside this work was checking (see below).
  Also found a real schema-version gap purely by testing against files
  from two different CLI versions: the `ordinal` top-level field exists
  in 2026-08+ rollout files but is entirely absent in 2026-07 and earlier
  ones — `sniff()` does not require it.
- **opencode** (`adapters/opencode.py`) — the odd one out: a relational
  SQLite store (`session`/`message`/`part` tables), not flat JSONL. This
  is why `SessionAdapter.parse()` takes a path and a `config`, not a
  file handle — an adapter needs the freedom to open its source however
  its format requires. `session.parent_id` gives opencode *native*
  session forking (unlike Claude Code's flat/branching-by-tool-call
  model), and `session_context_epoch` /`session_input.delivery` look
  like they might map to this tool's compaction-boundary and
  operator-vs-injected distinctions respectively — but neither was
  confirmed against real compacted/injected data, so the adapter does
  not claim to detect lifecycle markers or operator/injected provenance
  for opencode yet. Documented as an open gap, not guessed at.

## Architecture

```
events.py       NormalizedEvent / EventKind — the shared data model every
                adapter must produce.
adapters/       One module per source CLI. Each exposes sniff(path),
                list_sessions(path), parse(path, session_id, config).
                adapters/__init__.py holds the registry + detect().
classifier.py   score_events(): checkpoint-likelihood scoring for
                ASSISTANT_TEXT events. has_finding_signal(): a separate,
                lighter heuristic for short-comment survival.
select.py       select(): the backward windowing walk (see its own
                module docstring for the full contract).
render.py       render_text()/render_json(): output formatting, plus the
                embedded end-of-session marker used for delta extraction.
config.py       ExtractConfig — every tunable knob, centralized.
__init__.py     extract() — orchestrates adapter → parse → score →
                select → render. read_since_marker() — delta-extraction
                support (see below).
```

## Delta extraction

Two ways to resume from a known point instead of re-walking a whole
session:

- `--since <marker>` — an opaque `event.marker` value (adapter-specific:
  a Claude Code `uuid`, a Codex event id, an opencode message id) from a
  prior run. Only events strictly after it are considered.
- `--since-file <path>` — point at a **prior run's saved output** (text
  or JSON) instead of hunting down or hand-copying a raw marker. Every
  render embeds the true end-of-session marker from the *full* parse
  (not just what survived selection) — a JSON `last_marker` field, or a
  text footer `<!-- nyxloom-extract: format=... marker=... -->`.
  `read_since_marker()` reads either back. This is also how the tool
  finds its own resume border for the "snapshot chain" pattern discussed
  in `nyxloom/docs/design-context-lifecycle.md` — an agent iteration
  writes a summary and exits, the *previous* iteration's saved extract
  output is passed as `--since-file` to the next `nyxloom extract` run,
  and only the new delta prose comes back, ready to hand to a freshly
  forked session alongside the prior snapshot.

`--since-file`'s embedded format is cross-checked against `--format` (or
the auto-detected adapter) before running — a marker from one adapter is
meaningless fed to another, and this fails loudly instead of silently
returning nothing or garbage.

## Relationship to `jsonl-metrics.py` and the context-lifecycle experiments

`dstdns/nyxloom-trove/orientation/jsonl-metrics.py` and the E-001..E-008
experiment log in `nyxloom/docs/design-context-lifecycle-experiments.md`
are a **different deliverable**: content-based "coherent boundary"
detection (`gate_green`/`gate_red`/`gate_unknown`, commit,
`edit_cluster_end`, `log_report_write`) for *pipeline-agent* transcripts,
producing aggregate statistics — never raw prose. This tool never
produces stats; it only ever produces prose (or a structured event list
that renders to prose). The two are complementary: `jsonl-metrics.py`-style
boundary detection could plausibly inform a *future* checkpoint signal
here, but no code is shared today.

`nyxloom/docs/design-context-lifecycle.md` documents two related
patterns this tool is meant to serve as connective tissue for: (a)
checkpoint → designed-compact → resume, and (b) the snapshot chain
(fork → work → iteration-summary → re-fork) described above under
"Delta extraction."

## Multi-tier strategy at scale (not yet built)

For very long-running agents, mechanical extraction of the *whole*
history stops being the right move well before it stops being possible.
The intended (not yet implemented) shape, per operator direction:

1. **Normal operation**: the agent still does its own task-aware
   anticipatory pruning ("what future work does NOT need") as it works —
   this tool doesn't replace that judgment, it operates on top of
   whatever the agent already chose to say.
2. **Semantic-boundary checkpoints**: the agent is instructed to write a
   summary and exit at a semantic boundary once context crosses a
   threshold (see `vbpub/CLAUDE.md`'s "~120k context or ~60 tool calls"
   rule as the existing estate-wide version of this trigger). This
   tool's checkpoint scoring is designed to recognize exactly that kind
   of self-authored summary.
3. **Hard reset past ~10 boundaries**: at some point a fresh session
   without a prefix-cache hit is the right call, and boundaries older
   than roughly the last 10 are deliberately discarded rather than
   endlessly re-extracted. The agent can still be instructed to emit
   forward-oriented hints in its final checkpoint before a hard reset,
   which then survive into the next fork's seed even though the detailed
   history behind them doesn't.

`max_checkpoints` and the hard word budget are today's version of (3) at
single-run scale; a real multi-tier implementation (deciding *when* to
fall back to hard-reset-plus-hints vs. a normal `--since-file` delta) is
future work, not yet built.

## Known limitations / open work

- Thresholds (`recent_comment_chars=40`, `long_comment_chars=180`,
  `checkpoint_score_threshold=3.0`) are tuned against exactly one real
  hand-curated excerpt. A small labeled corpus would let this be done
  properly.
- opencode: no lifecycle-marker detection, no QA_PAIR equivalent, no
  operator-vs-injected distinction — `session_context_epoch` and
  `session_input.delivery` are plausible hooks for these but unverified
  against real compacted/injected data.
- Codex: no QA_PAIR equivalent found in the samples inspected;
  `reasoning` content was opaque (`encrypted_content`) in every sample —
  under active investigation given Codex is open-source (see the
  research fork referenced above; fold its findings back into
  `adapters/codex.py`'s documented gaps once it returns).
- No config-file loading yet — `ExtractConfig` is centralized (single
  source of truth for every knob) but only constructible from Python or
  the CLI flags in `cli.py`'s `extract` subparser today.
