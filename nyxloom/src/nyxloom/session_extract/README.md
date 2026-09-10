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
that exact hand-built excerpt and its source session (see "Why not just
use length?" below) — is yes, with caveats that shaped the design below.

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
further back." That is close, but real gaps only showed up once the
tool's actual output was diffed against the operator's real hand-curated
excerpt, run against its own source session — twice, at two levels of
rigor:

1. **A short, concrete finding survives; a short procedural aside
   doesn't** — the operator kept "Found it — a `pgrep` pattern bug" but
   dropped "Now let's fix that," despite near-identical length. This is
   `classifier.has_finding_signal()`: a finding-opener regex, a
   code-reference-in-backticks pattern, and a filename-mention pattern,
   checked independently of the length bar and _OR_'d into the keep
   decision at any distance from the newest checkpoint.
2. **"Recent history: keep everything" is too permissive, and so is
   "recent history: a lower bar."** A first pass compared `--checkpoints 5`
   vs `--checkpoints 10` output and, seeing them nearly identical, tried a
   second, lower length bar for content near the newest checkpoints instead
   of no bar at all. That was still wrong: a full, line-by-line replay of
   **every** ASSISTANT_TEXT event in the real excerpt's span against the
   excerpt (not a sampled before/after diff) found roughly 40 short
   (40-150 char), no-finding-signal procedural lines ("Now the
   `log-opts` cleanup gap...", "Real bug confirmed and it's a quick fix.
   Let me apply it...") that the operator dropped even in the very LAST
   turns of the span, immediately next to the newest checkpoint — no case
   anywhere of recency alone rescuing a short procedural line. The
   recency-based leniency was removed rather than re-tuned a second time:
   `long_comment_chars` (180) now applies uniformly across the whole
   walked span, with `has_finding_signal()` as the only length-independent
   escape hatch, at any distance from the newest checkpoint.

Both fixes are real bugs a naive length-only reading of the original
spec would have shipped with. A labeled corpus of more than one
hand-curated excerpt would let the threshold be tuned properly instead
of eyeballed against a single data point — noted as a real limitation,
not hidden.

### Why default toward dropping marginal content? (append-only / cache-stable extraction)

A rendered extraction's whole reason to exist is to become the start of a
fresh session's prompt, which the inference provider then prefix-caches.
Once a later `--since`/`--since-file` run has extended that session
further and the cache has built up on top of it, retroactively deciding
"actually, drop that one-liner from three checkpoints back" would edit the
middle of an already-cached prefix and tear the cache for everything built
on top of it — an expensive, one-way mistake, not a free do-over. Two
consequences follow, and both are enforced in `select.py`/`config.py`:

1. Selection for a span must never depend on anything that happens in a
   LATER span. A tempting-looking idea — "drop this because a later
   checkpoint restates the same numbers" (real example: an intermediate
   "299 passed, 93% coverage" line the operator dropped because the next
   checkpoint restated the final numbers) — was considered and rejected for
   exactly this reason: it would require re-visiting an earlier span's
   decision once something later makes it redundant, which is precisely
   the kind of retroactive edit this section rules out.
2. When a threshold is ambiguous, default toward DROPPING marginal content
   rather than keeping it. Dropping loses nothing permanently (the raw
   session log is always still there to re-read — see "Lossless dump"
   below); keeping something marginal bakes it into a prefix that becomes
   expensive to ever revise. This is the operator's own stated reasoning
   for removing the recency leniency above, independent of the excerpt
   evidence: even if a human curator's *own* in-the-moment judgment about
   what to keep is admittedly "a very fast intuitive decision," not a
   precisely-reproducible policy, the asymmetric cost of being wrong in
   each direction still favors the strict default.

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
designing an interface that only fits Claude Code's shape. All three
adapters have since been run end-to-end against real local session data
(hundreds of Codex rollout files spanning cli_version 0.142.2-0.151.0, a
72-session opencode.db, and multiple real Claude Code project logs), not
just synthetic fixtures — one of those real runs is what found the
schema-migration bug below. Findings, in descending order of how much they
constrained the interface or turned up a real bug:

- **Codex** (`adapters/codex.py`) — **real, load-bearing finding: Codex's
  `event_msg` schema was restructured entirely around cli_version 0.147.0
  (2026-08-09).** The adapter's first version read only the OLD flat shape
  (`payload.type` directly naming `user_message`/`agent_message`/
  `context_compacted`) — verified correct against files up to 0.145.0, but
  every real local file from 0.147.0 onward (i.e. the last month of local
  Codex history at the time this was found) uses a completely different
  NEW shape: every event arrives as `payload.type == "item_completed"`
  wrapping a typed `payload.item` (`"UserMessage"`, `"AgentMessage"`,
  `"Reasoning"`, tool/machine item types to skip), and the compaction
  boundary moved to a top-level `type: "compacted"` record carrying
  Codex's own real compaction-summary text. Running the adapter against a
  real recent file silently produced **zero events** before this was
  caught — a stark demonstration of why "verified against real session
  logs" has to mean *many* real logs spanning time, not a handful from one
  period, especially against a fast-moving external CLI this tool doesn't
  control the versioning of. Both generations are now handled; see the
  adapter's own module docstring for the full shape of each.
  **A second, related correction from the same wider sampling:** the
  earlier documented finding that Codex's chain-of-thought is opaque
  (`encrypted_content` in the old `response_item.reasoning` layer) turned
  out to be generation-specific, not universal — the NEW schema's
  `"Reasoning"` item carries a `raw_content` list of **plain-text**
  reasoning strings, not encrypted at all. THINKING is now emitted from
  real Codex sessions (new generation, `--include-thinking`) where it
  previously never could be. Whether the OLD layer's `encrypted_content`
  is genuinely unrecoverable, or just a different code path, is a
  narrower open question left to the Codex-encryption research thread
  (see "Known limitations" below).
  Remaining honest gap: no `AskUserQuestion`-equivalent structured Q&A
  signal was found in either generation, including a
  `"CollabAgentToolCall"` item that looked promising but turned out (its
  `"tool": "spawn_agent"` field, checked directly) to be a sub-agent
  spawn, not a question/answer mechanism.
- **Claude Code** (`adapters/claude_code.py`) — flat JSONL, one record
  per line, `type` discriminates `user`/`assistant`/`system`/housekeeping.
  `AskUserQuestion` batches are pre-rendered by the harness into a single
  `"The user answered: ..."` tool_result string covering every question
  in the batch — the adapter doesn't need to reconstruct pairing itself.
  Real schema quirks found only by running against live files, not
  documentation, several caught only by a later adversarial review's own
  reproductions rather than the initial design pass: the first line of a
  real file is often a housekeeping `"mode"` record with no
  `sessionId`/`parentUuid` (sniff must scan several lines, not just line
  1); background Task-tool completions arrive as `<task-notification>`
  blocks that can appear ANYWHERE in an otherwise operator-turn-shaped
  record, not only as the whole message, and must be stripped wherever
  they occur rather than only when the whole message starts with one (the
  provenance-smuggling risk: controller-injected content rendered as
  trustworthy operator intent); `<command-args>`'s inner content is the
  operator's own typed argument text and must be unwrapped, never
  discarded along with the surrounding tag noise; `<command-name>`
  detection must be anchored to the start of the message, since an
  unanchored search matches this tool's own docs/tests merely *mentioning*
  the literal tag string mid-sentence; and a `--since`/`--until` marker
  generated from a uuid-less record must be resolved via the exact same
  `f"line{i}"` fallback used to generate it, or it can never be resolved
  again.
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
  for opencode yet. Documented as an open gap, not guessed at. A real
  72-session local opencode.db run produced a coherent, substantive
  brief with no crashes or garbage output, giving reasonable confidence
  in what IS implemented even though these gaps remain.

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
lossless.py     dump_claude_code(): the independent ground-truth dumper --
                see "Lossless dump" below.
ledger.py       build_ledger(): E-012's mechanical files-touched/commits/
                branches/test-results ledger, aggregated per prompt
                boundary. Opt-in via `extract --ledger` (Claude Code only,
                text mode only today).
debug_diff.py   render_debug(): `nyxloom extract-debug`'s colored diff
                between lossless.py's own dump and a given extract() run --
                see its own module docstring for the full color-scheme
                rationale (white/grey/cyan/green).
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

`--until <marker>` is the symmetric bound: stop at a given marker
(inclusive) instead of walking to the end of the log. Its main use isn't
everyday resumption but pinning a run to a fixed historical span — e.g.
reproducing a run against a fixed hand-curated reference, which is exactly
what made the exhaustive real-excerpt replay above reproducible.

**Fixed bug, load-bearing for the whole chained-snapshot pattern above**: a
record without a real id (a uuid-less Claude Code record, an ordinal-less
pre-2026-08 Codex rollout line) falls back to a positional marker. Both
adapters used to compute that fallback position by re-`enumerate()`-ing the
record list — but they did this *after* slicing off everything at or before
`--since`, so the fallback restarted at 0 on every hop instead of counting
from the true start of the file. A marker minted from an already-sliced
parse therefore lived in a different index space than the same marker
resolved against a fresh, unsliced re-parse on the *next* run — so chaining
a second `--since` hop off the first hop's own output marker could resolve
to the wrong record and silently re-emit content a prior snapshot had
already captured, tearing exactly the cache-stability invariant this tool
exists to preserve (see "append-only / cache-stable extraction" above).
Caught by a fresh adversarial review with an empirical two-hop repro, not
by the test suite (the existing marker tests each only exercised a single
hop off a fresh full parse — the one case where the two index spaces still
happen to coincide). Fixed by tagging every record with its absolute,
pre-slice position once, up front, and using that same tag as the fallback
both when resolving a marker and when minting one — never a position
re-numbered after slicing. Regression tests
(`test_chained_since_on_uuid_less_records_never_reindexes_from_zero`,
`test_chained_since_on_ordinal_less_rollout_never_reindexes_from_zero`)
chain two real hops and assert the second returns nothing, not a replay.

## Lossless dump

`nyxloom extract --lossless` (Claude Code, Codex, and opencode today,
`lossless.py`) bypasses classification/windowing entirely: it keeps every text/thinking
content block verbatim and drops only `tool_use`/`tool_result` blocks and
non-conversational bookkeeping records, with no `isMeta`/task-notification/
AskUserQuestion-pairing logic at all. It's deliberately NOT the smart
adapter's code path — a "dumb," independent implementation, so it forms a
trustworthy superset rather than inheriting the real extractor's own blind
spots. Two things it's for:

1. **A ground-truth baseline for judging and tuning the real classifier.**
   `select()`'s job is to pick a small, curated subset of a session's
   prose; judging whether it picked well requires reading everything it
   *could* have picked from. Comparing the real extraction against this
   dump (not the raw JSONL, which is mostly machine noise) rather than
   against memory or a sample is what surfaced the two-tier-window bug and
   several of the adversarial-review findings above — it made a full,
   line-by-line replay of a real span actually tractable to read end to
   end, rather than eyeballing a diff.
2. **Raw material for a future hybrid** (see "Future" below): an
   agent-authored condensation of segments the mechanical path structurally
   can't recover.

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

## Future: hybrid agent-authored condensation for tool-only history

This tool's whole mechanism assumes a checkpoint already exists as prose
the assistant wrote in-line, and just needs to be located and filtered —
which is exactly why it can be free, instant, and deterministic. That's
also its hard limit: it structurally cannot recover information that only
ever existed in raw tool output the assistant never restated in its own
words.

Direct inspection of what Claude Code's own built-in auto-compaction does
(reading the raw session log across its own `compact_boundary`, not just
the resulting summary) makes the shape of the gap concrete. Its
`compactMetadata` shows a hybrid: a short verbatim tail of the most recent
messages survives completely unsummarized — **including raw tool_result
content**, e.g. a 43,250-character file dump kept byte-for-byte in one real
case — while everything older than that tail is sent to the model and
compressed into prose. That older-segment summarization is a genuine
generative act (the model reads arbitrary command output, diffs, and
review reports, and synthesizes a narrative connecting them), not an
extraction — which is precisely why it costs a real API round-trip
(measured directly: ~240 seconds of model latency for one compaction in
this tool's own development session) instead of being free like this tool.

A future design point, not yet implemented: on a genuine cold restart with
no prefix-cache reuse worth preserving, hand an agent the `--lossless`
dump for the segment being retired and ask it to describe, in its own
words, what should be remembered from it — especially anything that exists
ONLY in tool output — then prepend that agent-authored condensation before
this tool's own mechanically-extracted, unsummarized prose for whatever
span IS still worth keeping verbatim. This is a targeted, operator/tool-
triggered version of what Claude Code's built-in compaction does
automatically and opaquely: pay the model-call cost only for the segment
that genuinely needs synthesis, and get the mechanical, free, deterministic
treatment for everything else. See "Multi-tier strategy at scale" above
for how this could fit the hard-reset-past-N-boundaries case specifically.

## Known limitations / open work

- `long_comment_chars=180` and `checkpoint_score_threshold=3.0` are tuned
  against one real hand-curated excerpt, now via a full replay rather than
  a sampled diff (see "Why not just use length?" above), but still one
  data point. A small labeled corpus would let this be done properly.
- opencode: no lifecycle-marker detection, no QA_PAIR equivalent, no
  operator-vs-injected distinction — `session_context_epoch` and
  `session_input.delivery` are plausible hooks for these but unverified
  against real compacted/injected data. Also unaddressed (adversarial
  review, low priority): the SQLite connection URI is built by naive
  string interpolation (a store path containing `?`/`#`/`%` would break
  it), and part-fetching is one query per message (N+1; fine at real-world
  session scale seen so far, won't scale indefinitely).
- Codex: no `AskUserQuestion`-equivalent found in either schema generation
  (see "Cross-CLI adapter findings" above). Whether the OLD generation's
  `response_item.reasoning.encrypted_content` is genuinely unrecoverable,
  or just a different code path from the NEW generation's plain-text
  `raw_content`, is still an open question — not currently under active
  investigation; fold findings back into `adapters/codex.py`'s documented
  gaps if and when that thread resumes. `--since` chaining across the
  0.145.0→0.147.0 schema boundary, or across the ordinal-present/absent
  boundary, is not guaranteed to resolve (the marker scheme is
  generation-internal, not a stable cross-version id) — narrower than it
  sounds: chaining *within* one generation, including across records that
  individually lack `ordinal`, is fixed and covered by regression tests
  (see "Delta extraction" above); only a hop that crosses the schema-version
  boundary mid-chain is the still-open gap.
- `--lossless` and `session-stats` now both support Claude Code, Codex, and
  opencode (2026-09-10) -- see `stats.py`/`lossless.py`'s own module
  docstrings for the real per-format usage-ledger shape and gaps each
  found (incl. a correction to `adapters/codex.py`'s own claim about
  `compacted.payload.message` always carrying real compaction summary
  text -- it's empty for ~91% of real compactions). Unlike
  `dump_claude_code`, neither `dump_codex` nor `dump_opencode` has been
  checked against a real hand-curated reference yet -- validated against
  real local session files at the shape/field level, not against a
  human's own "what should have survived" judgment.
- No config-file loading yet — `ExtractConfig` is centralized (single
  source of truth for every knob) but only constructible from Python or
  the CLI flags in `cli.py`'s `extract` subparser today.
- No operator-visible signal when an adapter silently yields zero (or
  suspiciously few) events for a file it claims to recognize — exactly the
  failure mode the Codex schema migration produced, caught only by manually
  diffing extraction output against a real file rather than any automated
  check. `codex.py`'s `_SKIPPED_ITEM_TYPES` is documentation of what's
  known-noise, not an enforced allowlist the parser checks against — an
  unrecognized future `item.type` falls through the same silent `elif`
  chain as a deliberately-skipped one, with no distinction visible from the
  output. Worth a "did this file produce a plausible amount of content"
  sanity check someday; not built.
- `read_since_marker()`'s "last footer match wins" fix (for a file whose
  kept text happens to quote an earlier footer verbatim) is a heuristic,
  not a structural guarantee — it has no way to distinguish a genuine
  trailing footer from prose that coincidentally follows it and looks like
  one. Narrow enough not to have a known real trigger.
- `lossless.py`'s `--since`/`--until` compare raw `uuid` only, with no
  `f"line{i}"`-style fallback for a uuid-less record — unlike the real
  adapter, it cannot resume from or bound to such a record. Acceptable for
  its current use (a manual debugging/ground-truth tool, not part of the
  automated chained-snapshot pipeline), but worth knowing if that changes.

## Open design questions (raised 2026-09-10, not yet decided)

Surfaced building a cost/timeline analysis tool (V9 in
`nyxloom/docs/design-context-lifecycle-experiments.md`) on top of this package,
validated against a real dstdns session (`8ebff140-...`, E-009 in that file).
Parked here rather than acted on unilaterally:

- **Should `LIFECYCLE_MARKER` stay a hard stop?** — **RESOLVED 2026-09-10**:
  the hard-stop-at-0 behavior is now a configurable knob,
  `max_lifecycle_markers` (`config.py`, `--max-lifecycle-markers` on the
  CLI: `0` keeps today's hard stop, `N` walks past N markers, `-1` ignores
  them entirely). The *annotation* half is also shipped, in a different
  shape than first proposed: rather than a literal
  `---restarted-after-lossy-compaction---` string, `select.py` attaches
  `walk_stopped_because` (why the walk stopped short of the real session
  start — a LIFECYCLE_MARKER's own kept text already self-explains that
  case) and `gap_after` (how much raw content, including tool activity,
  sits between two kept events that aren't actually time-adjacent) via
  `NormalizedEvent.meta`; `render.py` surfaces both as bracketed text-mode
  notes and typed JSON fields. See `design-context-lifecycle-experiments.md`'s
  `E-011` for the full design + two rounds of real-data-driven refinement
  (`vbpub@7c152e13`).
- **Named "compression profiles"** — **SHIPPED 2026-09-10**: `config.py`'s
  `PROFILES` dict (`tight`/`default`/`manual_fresh`), wired into the CLI as
  `nyxloom extract --profile <name>`, with `--max-words` staying an
  independent, always-overridable axis rather than baked into a profile's
  identity. Still open: surfacing profiles as parallel columns in
  `session-stats`' timeline view (`_simulate_profile` already computes the
  per-profile running word count needed for this; nothing renders it as a
  table yet).
- **CLI-version-aware schema-drift detection** — every adapter's docstring
  already states its verified `cli_version`/`version` range (see
  `adapters/codex.py`'s 0.147.0 schema-break finding). Nothing today checks
  that range against a given session file's own reported version at parse
  time; an unverified newer version is silently parsed with whatever the
  adapter currently assumes, the same way the 0.147.0 break went unnoticed
  until manually caught against real files.
- **Prompting extension for the agents being extracted from** (not a code
  change here) — steer checkpoint-writing to front-load anything
  "memorable" from tool output into the prose, since mechanical extraction
  structurally cannot recover raw tool output that was never restated in
  words. Extends `design-context-lifecycle.md` §3's existing "summaries are
  indexes, not archives" mitigation to the no-agent-authored-summary case.

See `design-context-lifecycle-experiments.md`'s `E-009` for the full
discussion and the real per-CLI usage-field inventory (`usage`/
`token_count`/`tokens` — every adapter's source format already carries a
complete per-call token/cost ledger, unused by this package today), and
`E-011` for a 2026-09-10 follow-up: a qualitative read of real v1/v5/v6a
extractions against the "seed a fresh session" use case, the annotation/
omission-marker proposal above, and a proposed north-star refinement —
`select.py`'s core walk stays strictly mechanical/deterministic always
(this package's zero-model-calls guarantee is not just a cost property, it
is what keeps the append-only/cache-stability principle above true at
all), with an explicit opt-in "compression-assist" tier layered on top
that may only touch content the mechanical walk already decided was
marginal, and may only *add* a clearly-tagged synthesized sentence, never
edit or replace verbatim kept text.

`E-012`/`E-013`/`E-014` (same file, all 2026-09-10) cover three more SHIPPED
pieces: the real-data inventory of what's mechanically recoverable from
tool_use/tool_result records nyxloom otherwise drops (`E-012`, feeding
`ledger.py`'s `--ledger` flag), a second round of real-data-driven fixes to
`session-stats`' condensed view (`E-013` addendum — a real compaction is
now its own `kind="compaction"` row with visible post-compaction recovery
work, not a suppressed block hidden behind a divider; the elapsed-time
column now measures each row's own call latency, not a gap to the previous
row), and `extract-debug`'s colored lossless-vs-kept diff (`E-014`,
`debug_diff.py` — including a real design correction: it diffs
`lossless.py`'s own dump against `extract()`'s own render as TEXT, not by
marker, since `lossless.py` deliberately shares no marker space with the
adapters).
