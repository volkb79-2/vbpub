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

The three user CLIs follow who is working and which files the command targets:

- `nyxloom` edits one project's trove: `init`, `onboard`, local `lint`, and
  `backlog`. It can work on an unregistered checkout without a daemon.
- `nyxloom-harness` reads session files and stores for `extract*` workflows.
  It does not load the Nyxloom project registry or initialize host state.
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
