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

The `extract` family consumes a raw session-log file written by Claude Code,
Codex, or opencode. It is separate from nyxloom's registered-project
commands: `SESSION_LOG` means that file (or a bare session ID), not a
registered project ID.

Start with a normal compact brief:

```bash
nyxloom extract /path/to/session.jsonl
```

If you only have the session ID, pass it directly. Resolution succeeds only
when nyxloom finds exactly one match; otherwise the error lists what must be
disambiguated:

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

Choose the output mode for the job at hand. `--render-markdown` is for
reading; `--highlight` is for copying markdown source while retaining every
`#`, `**`, backtick, and dash:

```bash
nyxloom extract /path/to/session.jsonl --render-markdown --no-color
nyxloom extract-lossless /path/to/session.jsonl --highlight --no-color
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
