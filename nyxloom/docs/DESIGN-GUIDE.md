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
## Top-level version compatibility

Nyxloom keeps the `version` command for its normal command grouping and adds
`nyxloom --version` as an identity probe before command dispatch. Both use
`nyxloom.__version__`; the legacy command prints the bare metadata value,
while the top-level flag prints `nyxloom <version>` and exits 0.
The early probe avoids importing command-specific state merely to identify the
installed CLI and gives estate automation the same top-level contract as the
other first-party tools.
At every nested parser depth, help, usage, and configuration diagnostics begin
with `NYXLOOM <version> — operator CLI` as line 1 before argparse usage text;
normal command output is unchanged.

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
