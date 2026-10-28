# Nyxloom design guide

The README describes the shipped surface; [`CONSUMERS.md`](CONSUMERS.md) shows
how to adopt it. This document records the reasoning behind the release-facing
test contract.

## Why the broad CLI lane measures branch coverage

Nyxloom's primary lane covers the complete CLI package in `tester-unified` and
is judged by Assay. Line coverage alone can execute a decision while leaving
one outcome untested, so the lane now passes `--cov-branch` and requires the
same 100% whole-source floor for both lines and branches. The narrower
`session-extract` lane already carried branch, mutation, and canary evidence;
the broad lane now has a consistent minimum reach contract.

Hypothesis remains appropriate for pure CLI and configuration invariants where
input families are broad. Schemathesis is outside the CLI-only lane because
Nyxloom does not expose an owned HTTP/OpenAPI contract there.

## The cli-extended wheel ownership boundary

Nyxloom 0.10.0 included `cli_extended/` files in its wheel while the separately
installed `cli-extended` distribution installed those same paths. Pip records
each wheel's file list independently; it does not track shared-file ownership.
On upgrade, pip removed Nyxloom 0.10.0's files and kept the `cli-extended`
distribution metadata because its version still satisfied the dependency. The
metadata then described a package whose import files had been deleted.

Nyxloom 0.10.1 removed the duplicate files from its wheel, which is the correct
steady-state ownership model. An environment that already has 0.10.0 must
reinstall the trusted `cli-extended` wheel after upgrading Nyxloom once. Later
upgrades from a clean 0.10.1 install do not need this repair. The
[consumer guide](CONSUMERS.md#upgrade-from-nyxloom-0100) gives the ordered
commands.

## CLI identity, help, and bootstrap

Each human command has one cli-extended registry that owns its parser, help,
usage, and dispatch. `nyxloom`, `nyxloom-harness`, and `nyxloomctl` report the
same Nyxloom wheel version with their own executable names. Bare invocation
prints complete help and exits 0, matching the shared CLI contract. `--help`,
`help`, `help <verb>`, command help, and version paths do not create host log
files; `-h` is intentionally absent. Command-local errors include the relevant
generated help, so examples and accepted syntax share one declaration source.

The parser finishes before logging setup. Local authoring and harness commands
use an in-memory diagnostic path; `nyxloomctl` uses the host log for actual
operator work. This keeps `nyxloom lint`, session extraction, and display paths
usable from a project checkout without creating unrelated host state.
`--debug` and `--verbose` select debug verbosity, while `--log-level` accepts
`error`, `warn`, `info`, or `debug`; `NYXLOOM_LOG_LEVEL` supplies the default.
`--traceback` controls unexpected-exception tracebacks separately.

The large `nyxloom-harness extract` option set is grouped by the operator's
task: session source, window selection, included content, output, redaction
and task context, and live following. This uses cli-extended's native option
groups, so parsing and grouped help still come from the same declaration.

## CLI grammar and closed values

Parser declarations derive closed values from their owning sources: backlog
statuses come from the managed-entry model, finding kinds and severities come
from the finding registry, and notification/event sequence cursors are parsed
as integers. Finding `--field` values are checked for `KEY=VALUE` syntax with
a non-empty key before dispatch. This keeps generated help, accepted syntax,
and domain validation aligned; a typo or malformed value gets a usage error
with command help instead of looking like an empty result or reaching a
partially prepared handler. The [CLI reference](CLI-REFERENCE.md#option-declaration-matrix)
lists each option's effective omission, type, choices, placement, and effects.

## CLI boundaries and safety choices

The user-facing command surfaces follow who is working and which files the
command targets:

- `nyxloom` edits one project's trove and offers the read-only `usage report`
  query over local Claude Code transcripts. Both work without project
  registration or a daemon.
- `nyxloom-harness` searches, discovers, and extracts session files and
  stores. It does not load the Nyxloom project registry or initialize host
  state.
- `nyxloomctl` handles local host operations such as registry, workflow state,
  routes/models, credentials, all-project lint, and local `daemon` control.
  It is not a remote API client. The dashboard remains the HTTP/SSE client.
- `nyxloomd` is a service-manager executable that starts the existing daemon
  lifecycle in the current container; it is not another interactive CLI.
  Its only arguments are side-effect-free `--help` and `--version`; unknown
  options fail before registry or daemon initialization, so a diagnostic typo
  cannot accidentally start service work.

The split keeps common skill entrypoints dependable when the daemon is down,
while making host-wide actions visibly distinct from project-local authoring.
The exact command ownership and old-to-new command map are in the
[CLI reference](CLI-REFERENCE.md#current-command-and-option-contract).

`nyxloom lint` checks the current checkout, discovered from the working
directory or explicit file paths. `nyxloomctl lint` preserves the registered
project scan. A project author therefore does not need to register the project
with a local daemon host just to validate a handoff.

## Local usage report

`nyxloom usage report` is a read-only analytics query over Claude Code's local
project transcripts. It is a separate workflow from `nyxloom-harness
extract-report`: that command summarizes one selected source file, while this
command selects a UTC time window across session files and attributes requests
to sessions and nested subagents. D-022 deliberately selects the additive
`nyxloom usage report` spelling. Its handler calls the pure
`session_extract.usage` query service; it does not initialize project or daemon
state and does not add a persistent store.

File-local stream blocks are grouped by request ID, falling back to message ID
only when request ID is absent. The loader verifies model, effort, usage and
other request metadata before choosing the final stream row. A conflict
excludes the group and appears in `conflicts` and coverage warnings. Request
IDs are never global: copied prefixes are merged across files only when
session ID, message ID, normalized UTC timestamp, model and the complete usage
tuple all match. The call retains opaque source IDs, line provenance, stream
block count, all distinct request-ID aliases, copied-source count and whether
any copy came from a fork.

Parent edges use the shared resolver also used by `list_agents`: a consistent
`parentAgentId` wins, then a unique `toolUseId` owner. Missing and ambiguous
edges remain in `lineage_edges` and in an explicit unattributed tree branch;
`spawnDepth` describes a node but never invents its parent. `isFork` and
sidechain calls are retained.

The frozen JSON `usage-report-v1` contract is the renderer source. Its top-level
`calls` array is the exact deduplicated ledger, including every token class,
model, effort, speed, service tier, inference geo, context size, timestamp,
opaque provenance and stable call ID. `nodes` reference those call IDs and
carry exact self and subtree totals. `lineage_edges`, `events`, conflicts,
coverage warnings, labels and null extension slots for future prices, anchors
and chart data are also part of the versioned shape. CSV is intentionally a
flat call ledger: one report metadata row followed by one row per call; it does
not substitute overlapping subtree rows for calls. Table output shows the
hierarchy and self/subtree totals.

Coverage diagnostics retain both lost-data warnings and successful merge
counters. `partial` is true when malformed, unavailable, incomplete, or
unattributed source data prevents a complete report. Ordinary stream-block and
copied-prefix deduplication remain visible diagnostics but do not make an exact
merged call ledger partial by themselves.

The v1 event-kind vocabulary is `compaction`, `api_rejection`, and
`unexplained_drop`. A compaction event comes only from a structural
`system.compact_boundary` record; a `user.isCompactSummary` record is paired by
`parentUuid`, with only its timestamp retained. An API rejection comes from an
`isApiErrorMessage` record; `limit_kind` is `rate_limit` only for the explicit
`rate_limit` error code or HTTP status 429, and other rejection classes remain
unknown. A context-size drop of at least 30% between consecutive calls is an
`unexplained_drop` unless a structural compaction boundary lies between them
by timestamp and source-line tie order. It never becomes a confirmed
compaction by heuristic.

Node kinds are `main`, `subagent`, `fork`, `unattributed`, and `other`.
Lineage statuses distinguish `resolved_parent_agent_id`,
`resolved_tool_use_id`, `resolved_tool_use_id_parent_metadata_conflict`,
`unresolved_ambiguous_tool_use`, `unresolved_missing_lineage_metadata`,
`unresolved_missing_tool_use_owner`, `unresolved_missing_parent_source`,
`unresolved_parent_conflict`, `unresolved_unknown_parent`, and
`unresolved_cycle`; roots use `root` and the grouping node uses
`unresolved_group`. Conflict kinds are `duplicate_conflict` and
`cross_file_conflict`. Conflicts exclude all candidate calls and include
opaque source/line provenance.

The loader parses each transcript once, only through its initial byte-size
snapshot, and keeps usage metadata rather than transcript records. It never
uses file modification time to decide which requests fall in a time window.
Source and project paths are represented by opaque identifiers. Path-bearing
free-text labels and path-valued identity metadata are omitted. Message,
thinking, tool input and tool results are not fields in the report. Optional
title/topic labels come from title and agent metadata; `--no-labels` removes
them before serialization.

U1–U2 intentionally stop at raw token totals. “Raw token units” sum input,
cache-read, cache-write 5-minute, cache-write 1-hour, unclassified cache-write,
and output tokens. Thinking tokens are reported as a subset of output and are
not counted twice. Until the verified weight table and cost-state comparison
land in U3, USD and weighted units stay null, `--sort cost` means raw token
units, and `--min-share` folds rows by their share of those raw units. This
avoids making an unverified price or limit formula look authoritative.

The interval is half-open `[since, until)` in UTC. Relative `--since` values
are measured backwards from `--until` when supplied, otherwise from the single
invocation-time sample. Each API call is timed by its own timezone-aware
record timestamp; missing or invalid timestamps are excluded and counted as a
coverage warning. Output timestamps are normalized to UTC. Nodes expose
`clipped_before` and `clipped_after` when calls from their source lie outside
the selected interval. Project selection uses the recorded `cwd` (exact
normalized path or an unambiguous basename) and includes the matching session
tree. The report discloses scanned files, malformed or truncated lines,
missing fields, conflicts, lineage gaps, and concurrent changes.

Wall duration is the first-to-last call span. Active span adds only the spans
within activity bursts; a gap strictly greater than 3,600 seconds starts a
new burst. Both values and every tie use deterministic timestamp, session,
opaque source, line and call-ID ordering. IDs use truncated SHA-256 over
canonical source and record identity; JSON keys are sorted and share fractions
are rounded to eight decimal places. Tests inject the clock and compare the
complete versioned output. The invocation samples `now` once and reuses it for
window resolution and `generated_at`.

The hierarchy is shown directly in the table: session rows contain their own
calls, child-agent rows, and subtree totals. `--depth` limits visible levels;
`--min-share` folds small child trees and session roots into explicit “other”
rows; `--flat` sorts visible nodes globally. The query and output contract are
documented in the [consumer guide](CONSUMERS.md#report-claude-code-usage) and
[canonical CLI reference](CLI-REFERENCE.md#current-command-and-option-contract).

Mutations keep Nyxloom's existing safeguards as consent: explicit verbs,
state-transition validation, role checks, `--apply` plus confidence flags,
and store protections. The registry accurately marks mutating commands but
disables cli-extended's generic confirmation requirement; adding a second
prompt would make automation less clear without improving those domain checks.
Options that were accepted but ignored now fail with a clear usage error, and
read-only paths such as `backlog list` do not write indexes.

## Interactive backlog authoring

`nyxloom backlog new TITLE` remains scriptable. When users need to create or
edit a structured entry by hand, `new --interactive [TITLE]` and `edit
ENTRY_ID` use cli-extended's optional Questionary prompt API. The optional
`nyxloom[interactive]` extra keeps the core wheel free of UI dependencies;
ordinary help, lint, harness work, and noninteractive backlog commands work
without it.

The prompt layer owns TTY checks, cancellation, and typed choices. Nyxloom
owns metadata meaning, validates the full frontmatter candidate with its
shipped schema, and performs the file write only after validation. Edit keeps
metadata outside the form, including transition-owned state, and copies the
Markdown body bytes unchanged. Creation retains the body template and
`--body-from`. This reduces hand-written prompt boilerplate without moving
backlog policy into a generic wizard library.

## Session extraction selection and boundaries

`extract` has two use-case profiles. `operator-review` is the default: it
selects the newest `/clear` epoch, then walks backward until it reaches any of
five classifier-detected assistant checkpoints or the 10,000-word cap. A
checkpoint is a scored assistant prose message that helps anchor a review; it
is not a semantic boundary and does not certify that older context can be
discarded safely. The classifier looks for report-like structure and whether
the assistant turn was followed by an operator pause. A message's line count
or line breaks alone are not checkpoint evidence: single-line status updates
can matter, and multiline text can still be incidental.

`all` relaxes the stop conditions: it has no checkpoint, word, time, or
compaction limit and selects all epochs the adapter can identify. It emits
ordinary operator, Q&A, and assistant prose, including short messages. API
transport errors remain hidden unless `--show-api-errors` is used; tool calls,
thinking, and compaction summaries or prompt payloads remain controlled by
their separate options. This gives a broad fresh-agent resume without mixing
compaction internals into the prose transcript.

The backward walk crosses real compaction boundaries by default. An optional
`--max-compactions N` stop limits how many automatic compaction boundaries it
crosses; it does not count an explicit `/compact` command or a compaction
summary echo. `/clear` is different: where the adapter exposes it, it starts
a new epoch because the next context is empty, so the default operator review
never concatenates pre-clear history. Claude Code transcript `/clear` records
are tagged explicitly. Codex starts a new rollout/session ID after `/clear`,
so one Codex file cannot span that reset. OpenCode and Reasonix currently
expose no verified clear marker and therefore appear as one epoch each.
`--epochs N`, `A:B`, and `all` select one epoch, an inclusive range, or all
epochs the adapter reports. Epoch selection is independent from checkpoint
and budget stops.

### Codex question replies

Codex records each interactive question in a
`response_item.function_call` named `request_user_input_async`. Nyxloom renders
every item from that call at its source position as `INTERVIEW:` prose, with
the question title/text and offered choices. This is the authoritative prompt
record: it remains visible when no assistant-prose copy exists. If Codex also
writes an identical `AgentMessage` copy, nyxloom deduplicates it. When a call is
before a `--since` boundary but its prose copy is after it, the copy becomes
the marked prompt for that delta. Live follow starts from a one-shot prefix
that already rendered calls through its anchor, so a later prose copy is
suppressed without repeating the prefix prompt.

The submitted answer arrives later as a `UserMessage` containing a
`send_user_message_question_reply` JSON envelope. Each reply row carries its
question text, answer, and `questionItemId`; nyxloom uses that ID to recover the
matching choices without depending on adjacency or recency. Exact choice text
and arbitrary free text are both retained verbatim as `OPERATOR:` prose. A
multi-question reply is split into one marked question/answer pair per row.
Malformed envelopes remain visible as the original operator text rather than
being partially decoded. If the user answers through ordinary chat instead,
that text remains operator prose; nyxloom does not infer a question link where
the source record has no question ID.

### Claude Code question replies

Claude Code records each `AskUserQuestion` UI request as an assistant
`tool_use`. Nyxloom emits every question and its offered choices at that
source position as `INTERVIEW:` prose, including its header, option
descriptions, and multi-select behavior, whether or not the request has a
matching answer record or tool-use ID. When a recognized result arrives, its
position carries a second marked question/answer block so the operator's choice
or free text remains attached to the question it answers. This keeps a pending
prompt visible in an extract and keeps an answer understandable when an extract
starts after the prompt.

The usual tool result flattens rows as `"question"="answer"`. A rejected
question batch can instead contain a `Questions asked:` section with per-row
`Answer:` or `(No answer provided)` entries. Nyxloom uses the original tool
request to match those rows and renders both answered and explicitly
unanswered rows with `INTERVIEW:` and `OPERATOR:` labels. If the rows do not
match the original prompt batch, the source text stays intact; partial parsing
could otherwise drop an answer or attach it to the wrong question.

Profiles carry use-case policy for selection and gap reporting. Explicit
selection or gap flags override the corresponding profile values. Timestamp
placement, metadata placement, tool-call visibility, Markdown rendering,
syntax highlighting, and ANSI color remain separately controlled. Rendering
consumes Markdown characters for reading; `--highlight` colors source while
preserving them. ANSI color follows stdout's TTY state and `NO_COLOR` by
default, `--color` forces ANSI, and `--no-color` disables it without changing
the selected rendering mode.

Snapshot cursors are source markers rather than timestamps. `--since-file`
reads the marker embedded in a previous text or JSON extraction and resumes
after it. `--since` accepts the marker value directly, and `--until` pins an
inclusive upper marker for reproducible historical spans. The marker is the
end of the source parse, even if selection omitted records, so snapshot chains
do not repeat material already considered. A live `--follow` stream appends a
post cursor after each emitted event; pre-only metadata is refused there
because that cursor could not track the growing stream. Codex records with an
ordinal use it directly. In older ordinal-less rollouts, legacy `event_msg`
cursors keep their numeric form, while surfaced `response_item` prompts use a
namespaced `response_item-<position>` marker.

An opencode session ID can be resolved directly when it is unique across the
known stores. `--opencode-session` still selects a particular row when the
operator starts from an explicit multi-session database path. Session
discovery uses environment-aware defaults (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`,
and `OPENCODE_DB`/XDG); directory discovery recurses by default so nested
Codex rollouts are found.

Report output types make the audience explicit. `report-sheet` is the compact
operator overview, `report-detailed` is a readable row per API call, and `csv`
is the stable-column form for spreadsheets or scripts. The legacy
`--detailed` flag remains an alias for CSV.

## Local session search

`nyxloom-harness search` answers “which local session should I inspect?” by
searching the text-bearing records in the existing Claude Code, Codex, and
OpenCode stores. It stays local and prints identifiers, source paths, matched
query words, dates, and scores; it does not print transcript excerpts or send
session content to a service. Matching is case-insensitive and punctuation
splits words, so `cli-extended` searches for both `cli` and `extended`.

The default is an **any-word** search with exact token matches.
`--word-match all` requires every query token; `--term-match prefix` lets a
query token match the beginning of a longer token (`qcow` matches `qcow2`). These are
separate choices because “which sessions mention any of these clues?” and
“which sessions mention every clue?” are different operator questions.
Repeated `--client` values form the selected harness list. Repeated
`--source-root` values form the locations searched by each selected client.
Codex and Claude Code use directory roots; OpenCode uses a database file or a
directory containing `opencode.db`. Roots with a different path type are
ignored for clients not explicitly selected. If `--client` names a format,
the roots must include a compatible path for every selected client. With no
source roots, each adapter uses its existing configured/default store
discovery.

Search does not build or maintain an index. It retains counts only for the
requested words, and uses ripgrep's native parallel scanner, when installed,
to stream only candidate JSONL records into Python. The Python side checks
each record against the normal text-field and token rules; Unicode case-fold
expansions and JSON-escaped query letters are included in the candidate
filter. After 512 candidate records, the guarded `nyxloom-harness` entrypoint
uses one bounded pool of up to four processes for JSON decoding and text
counting in batches; each submitted batch is capped at 128 records and 1 MiB
of path and record bytes, and larger individual records are counted inline.
At most two batches per worker can be pending; shorter searches avoid worker
startup. Embedded calls from other Python entrypoints stay inline so worker
startup cannot re-import the caller's module. Without ripgrep, fixed-size reads
and a raw-line filter provide the same search semantics while keeping memory
bounded. OpenCode rows use a raw candidate check before JSON decoding. Exact
activity timestamps are read from the end only for transcripts that match.
Progress uses cli-extended's existing renderer on stderr with the standard
`--progress` modes; the search command does not add a second progress API.

`--fast` is an opt-in approximate path for raw JSONL stores. It requires
ripgrep, ASCII query words, and an explicit `--client codex` and/or
`--client claude`; it refuses OpenCode and refuses the all-client default
instead of silently omitting a client. Ripgrep emits only complete matching
tokens with `--only-matching`; Nyxloom counts those occurrences directly and
does not parse candidate transcript records for matching or read matched-file
tails. The 64-hit cap,
any/all filter, exact/prefix matching, and relevance ranking still apply. File
modification time supplies the date sort and is labeled `FILE MTIME (UTC)`;
it is a filesystem proxy for activity, not the transcript's recorded last
activity.

Raw mode searches serialized JSON, including keys and metadata, so a match may
come from text the regular search deliberately excludes. Escaped query letters
and Unicode case-fold expansions may be missed. Those tradeoffs are why raw
mode is opt-in. Session discovery still reads the small identifying metadata
needed to map source paths to session IDs and deduplicate aliases; the match
counting pass itself uses only ripgrep's returned tokens. For the precise
text-field behavior, use the default search path.

Codex homes can be aliases of one another (for example, `~/.codex2/sessions`
symlinked to `~/.codex/sessions`). Search and bare-ID resolution compare
physical file/directory identities so aliases and directory-link cycles do
not create duplicate work or false ambiguity. The resolver applies the same
deduplication to Claude project candidates and OpenCode database aliases,
where duplicate physical candidates would otherwise look ambiguous. A
single-root `extract-sessions` walk does not union the default Codex homes, so
it does not need this particular root-alias check; file aliases inside one
selected tree remain a possible separate cleanup if that layout appears.

The default `best` order favors sessions matching more distinct query words,
then weights rare terms more heavily and modestly rewards repeats. Counts
saturate at 64 occurrences of each query word per session: matching presence
and matched-word output stay exact, while further repetitions no longer raise
that word's relevance score. `date` puts newer recorded activity first.
Results include the exact source path so a same-ID collision across local
clients remains distinguishable. The
[consumer recipe](CONSUMERS.md#search-local-session-history) shows the
copyable command and output fields.
