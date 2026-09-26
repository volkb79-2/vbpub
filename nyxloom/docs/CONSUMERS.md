# CONSUMERS.md — adopting nyxloom's managed backlog entries

Worked recipes for the per-entry backlog (`nyxloom-trove/backlog/`, one file
per issue). Design authority (WHY it exists, rejected alternatives):
[`backlog-entries-spec.md`](backlog-entries-spec.md).

## Adopt in a project (paste-able)

Add to `nyxloom-trove/nyxloom.toml`:

```toml
[backlog_entries]
dir       = "nyxloom-trove/backlog"   # optional; this is the default
id_prefix = "CIU"                     # your project's issue sequence
```

Then:

### Check the installed CLI

    nyxloom --version

This prints `nyxloom <version>` on stdout and exits 0 without accessing the
project registry.

```bash
nyxloom lint                 # BLG2/BLG3 now active (silent before adoption)
nyxloom backlog new "clean leaves instance-scoped networks" \
    --type bugfix --severity medium --provenance "consumer P111 F4"
nyxloom backlog index        # regenerate backlog/INDEX.md (lint enforces freshness)
```

Commit `backlog/` including `INDEX.md` — it is generated, and `nyxloom lint`
fails when it is stale, so never hand-edit it.

Omitting the `[backlog_entries]` table entirely = feature unused; every
backlog-entry lint rule stays silent.

## Extract a session log

`extract` and `extract-lossless` consume session logs from Claude Code, Codex,
Reasonix, and opencode. `extract-sessions` discovers Claude Code, Codex, and
opencode families; `extract-report` supports those same three formats. These
verbs are separate from nyxloom's registered-project commands:
`SESSION_LOG` means a session file/store or bare session ID, not a registered
project ID.

Start with a normal compact brief:

```bash
nyxloom extract /path/to/session.jsonl
```

The default `operator-review` profile selects the newest epoch reported by
the source adapter, keeps up to five assistant checkpoints, and stops at
10,000 words. Checkpoints are classifier-scored prose anchors, not safe
compaction boundaries. Claude Code exposes `/clear` epochs; Codex begins a
new rollout after `/clear`, while OpenCode and Reasonix currently expose a
single epoch. To prepare a broad fresh-agent resume with all prose across
available epochs, use `all`:

```bash
nyxloom extract /path/to/session.jsonl --profile all > full-prose.md
```

The `all` profile removes checkpoint, word, time, and compaction stops. It
includes short operator, Q&A, and assistant prose. API transport errors,
thinking content, tool calls, and compaction prompts/summaries remain
controlled by separate options. A detailed Claude Code or Codex inspection
can include short tool labels and any explicit description/intent field the
source provides, without printing tool
inputs or results:

```bash
nyxloom extract /path/to/session.jsonl --profile all \
  --show-tool-calls --show-tool-call-intent > full-with-tool-labels.md
```

Codex interactive prompts and replies remain readable in the result. Every
question and its options appear at the prompt's source position, including an
unanswered prompt or one Codex did not copy into assistant prose. Structured
answers appear with the question they answer, even after other session
activity. For example:

```text
INTERVIEW: Which implementation approach should we use?
- Keep the current worktree
- Create a new worktree

OPERATOR: Keep the current worktree

INTERVIEW: What should change if the current branch is stale?

OPERATOR: Reconcile it with main, preserving the current configuration.
```

The question and offered choices come from Codex's UI request record. The
structured answer is linked using that record's question ID; free text is not
rejected for failing to match an option. An ordinary chat response remains
operator prose without an inferred question link. See the
[design rationale](DESIGN-GUIDE.md#codex-question-replies).

For a specific span, `--epochs` selects a `/clear` epoch or inclusive range;
`--max-compactions` and `--max-time-minutes` add backward-walk stops. A
checkpoint count is an extraction budget, not a semantic compaction point.

```bash
nyxloom extract /path/to/session.jsonl --epochs 2 --max-compactions 1
nyxloom extract /path/to/session.jsonl --epochs 1:2 --max-time-minutes 180
```

If you only have the session ID, pass it directly. Resolution succeeds only
when nyxloom finds exactly one match; otherwise the error lists what must be
disambiguated. To select an adapter explicitly, `--format` accepts
`claude-code`, `codex`, `opencode`, or `reasonix`; omit it for content-based
detection:

```bash
nyxloom extract 019f0890-43a2-75c2-9143-3f8d10ad4484
nyxloom extract ses_04bd4e9b4ffeBJm48T6v130DS6
```

Codex stores rollouts under `CODEX_HOME` (`~/.codex` by default). Bare UUID
lookup also checks local `~/.codex*` profiles, so a UUID copied between
profiles fails closed instead of selecting the default profile's transcript.
Use the full rollout path to disambiguate the one you want:

```bash
codex_home="${CODEX_HOME:-$HOME/.codex}"
session_id=019f0890-43a2-75c2-9143-3f8d10ad4484
session_file=$(find "$codex_home/sessions" -type f \
  -name "rollout-*-$session_id.jsonl" -print -quit)
test -n "$session_file" || {
  printf 'Codex rollout not found for %s\n' "$session_id" >&2
  exit 1
}
nyxloom extract "$session_file"
```

Codex does not reserve IDs separately for each `CODEX_HOME`. New thread IDs
are UUIDv7 values, and the same identity is repeated in the rollout filename
and its first `session_meta` record. A pre-existing filename is opened for
append, so do not symlink live `sessions` directories between independently
authenticated profiles. To identify a live instance, inspect its process
argument (`codex resume <SESSION_ID>` when resumed) and its `CODEX_HOME`, then
corroborate the currently active ID from the first metadata record of the
rollout path held open by `/proc/<pid>/fd`; the command-line ID can be only the
launch target if the interactive process later switches to a new thread.
If the UUID is present in multiple homes, use the full path; renaming the file
alone does not rewrite its embedded identity.

For an opencode database containing more than one session, use the store path
and select the row explicitly:

```bash
nyxloom extract /path/to/opencode.db --opencode-session ses_04bd4e9b4ffeBJm48T6v130DS6
```

If only the opencode ID is available, pass it as the positional session
reference; nyxloom searches its configured/default stores and resolves it when
there is exactly one match. The explicit flag remains useful with a database
path containing multiple sessions.

Choose the output mode for the job at hand. `--render-markdown` is for
reading and consumes Markdown markers. `--highlight` colors Markdown source
while retaining every `#`, `**`, backtick, and dash. A terminal gets
highlighting by default unless `NO_COLOR` is set; `--color` forces ANSI for
piped output, and `--no-color` disables ANSI without changing the render mode:

```bash
nyxloom extract /path/to/session.jsonl --render-markdown --no-color
nyxloom extract-lossless /path/to/session.jsonl --highlight --no-color
```

The text brief defaults to `[HH:mm:ss]` timestamps before each event, with
source metadata and the machine-readable cursor marker at both ends. Source
creation time is reported as unavailable when the filesystem exposes no
creation timestamp. Change timestamp placement/format and metadata placement
independently:

`--show-timestamps` accepts `pre` (default, before the prose), `post` (after
the prose), `both`, or `none`. `--extract-metadata` accepts `pre`, `post`, or
`both` (default). `--gap-marker` accepts `full`, `inline` (default), `inline2`,
`inline-short`, or `none`; `--blank-lines` defaults to `0`.

```bash
nyxloom extract /path/to/session.jsonl --show-timestamps both \
  --timestamp-format '%Y-%m-%d %H:%M' --extract-metadata pre
nyxloom extract /path/to/session.jsonl --show-timestamps none --extract-metadata post
```

`--follow` uses the same timestamp format and appends an updated post cursor
comment after each emitted event so the saved stream can still be passed to
`--since-file`. With a live stream, choose `post` or `both`; `pre` alone is
refused because its cursor would go stale as the file grows.

The cursor comment includes a source marker, for example
`<!-- nyxloom-extract: format=codex marker=166 -->`. Copy the value after
`marker=` for a raw cursor range. `--until` includes its marker. For normal
snapshot chaining, save the prior output and let `--since-file` read it.

Treat the marker as opaque: ordinal-less Codex rollouts use a namespaced
`response_item-<position>` cursor for surfaced UI prompts, while their legacy
event cursors retain numeric values.

```bash
nyxloom extract /path/to/session.jsonl > snapshot.md
# After more activity is written to the same source log:
nyxloom extract /path/to/session.jsonl --since-file snapshot.md > delta.md
# Or pin an exact historical span using marker values from that source:
nyxloom extract /path/to/session.jsonl --since 166 --until 240 > span.md
# Older ordinal-less Codex rollouts can use a UI-prompt cursor directly:
nyxloom extract /path/to/rollout.jsonl --since response_item-23 > delta.md
```

`extract-debug` takes the same profile and selection/rendering flags as
`extract`, then compares that result with the lossless source view. Use it
when a specific message appears inside an inline gap:

```bash
nyxloom extract-debug /path/to/session.jsonl --profile all --no-color | less
```

`extract-report` has three text shapes: `report-sheet` (default) is the
compact overview, `report-detailed` is a readable one-row-per-call table, and
`csv` is for spreadsheet/script ingestion. JSON is available for the two
report shapes. The old `--detailed` spelling selects CSV.

```bash
nyxloom extract-report /path/to/session.jsonl --type report-detailed
nyxloom extract-report /path/to/session.jsonl --type csv > calls.csv
```

Discover sessions under tool-specific environment roots. Directory scanning
recurses by default (`--recurse true` is explicit); `--recurse false` limits
it to direct children.

```bash
nyxloom extract-sessions codex
CODEX_HOME="$HOME/.codex2" nyxloom extract-sessions codex
nyxloom extract-sessions "$HOME/.codex2/sessions" --recurse false
nyxloom extract-sessions opencode
```

Follow a live session after the initial one-shot result. The two verbs keep
their own semantics: `extract` applies its normal selection rules to new
records, while `extract-lossless` prints every new prose/thinking block.

```bash
nyxloom extract-lossless /path/to/session.jsonl --follow --highlight --bell
```

File-backed follow is incremental for large logs: an advancing file reads its
appended payload plus only bounded prefix/tail fingerprints as needed for
rewrite detection. Unchanged polls read no content, and no poll performs a
whole-file rescan.

The incremental tailer treats a path that is absent before its first open as
startup readiness: a later poll can open it after the producer creates it.
Once the stream is active, a disappeared file or a metadata (`stat()`) I/O
error is a terminal failure. The CLI reports `error: followed session file
disappeared while following: PATH` for disappearance, or an `error: cannot
stat followed session file while following PATH: ...` diagnostic for another
metadata failure, and exits 1; consumers must not interpret either case as
“no new content”.

For live redaction, use `extract`: its `--redact-pattern` applies to both the
initial brief and newly streamed phase-two output.

```bash
nyxloom extract /path/to/session.jsonl --follow --redact-pattern 'API_KEY=[^ ]+'
```

`--strip-stale-wakeups` is only a finished-span transform. The exact
combination `extract --follow --strip-stale-wakeups` is rejected before the
initial extraction because a trailing run cannot be finalized while the
session is still growing. `extract-lossless` remains verbatim and rejects
`--redact-pattern`; choose `extract` when live redaction is required.

To run an operator hook when the stream needs attention, use the typed
environment variables supplied by nyxloom. The hook receives one of
`interview_pending`, `checkpoint_detected`, or `long_block` in
`NYXLOOM_ATTENTION_REASON`, plus the harness, session path, and a short
excerpt:

```bash
nyxloom extract /path/to/session.jsonl --follow --attention-min-chars 4000 \
  --on-attention 'printf "%s: %s\n" "$NYXLOOM_ATTENTION_REASON" "$NYXLOOM_ATTENTION_EXCERPT" >&2'
```

`--notify-project PROJECT_ID` can additionally use the `[notify]` channel of
an already registered project. It is opt-in; run `nyxloom project list` to
choose the project ID. The notification includes the flagged excerpt, unlike
the ordinary fixed-template nyxloom notifications.

## File a follow-up on an existing entry

Second reproductions, priority bumps, new evidence — `note`, never a new
entry and never a hand-edit:

```bash
nyxloom backlog note CIU-43 "second repro on 6.3.0: volumes leak too"
```

This appends a dated paragraph under `## Updates` in
`CIU-43-clean-leaves-networks.md` and refreshes the index.

## Close an entry

```bash
nyxloom backlog set-status CIU-23 withdrawn --reason "consumer premise disproved"
nyxloom backlog set-status CIU-36 fixed --reason "shipped in ciu-P08"
```

Terminal statuses (`fixed|withdrawn|obsolete`) require `--reason`; the verb
stamps `closed_date`/`closed_reason` in the entry's frontmatter. `merged` is
never hand-set — the merge flow auto-ticks the entry linked by
`carved_handoff`.

## Promote an idea from the inbox

Quick idea in `4-backlog-inbox.md` (spine) or `backlog.md` (plain) outgrew
the inbox? Promote it — the entry is created, the item is removed from the
inbox, and the index is regenerated in one step:

```bash
nyxloom backlog promote B7
```

## Carve from an entry

Set `carved_handoff` in the entry's frontmatter (or let your carve flow do
it); when that handoff merges, the merge auto-tick sets `status=merged` +
`merge_commit`. A `carved` status with a `carved_handoff` link is all the
auto-tick needs.

When recording CLI diagnostics, preserve the first line at every verb depth:
`NYXLOOM <version> — operator CLI`, using the package metadata version exposed
by `nyxloom.__version__`. `nyxloom --version` is exactly one identity line;
normal command output is unchanged.

The broad `tester-unified` lane is Assay-judged with 100% whole-source line and
branch coverage. The `session-extract` lane additionally carries its declared
mutation and canary checks. Invoke both through the project's `./run-gate.py`
entrypoint; local cockpit test results do not certify a release.
