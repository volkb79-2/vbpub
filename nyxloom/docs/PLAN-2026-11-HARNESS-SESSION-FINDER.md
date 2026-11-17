# Plan: make `nyxloom-harness search` a usable session finder (P0 + P1 in detail)

Written 2026-11-17. Self-contained: a fresh agent can execute P0 and P1 from this file plus the
backlog entries it names. Backlog is the requirement record; this file is the intent, the
reasoning, the code map and the order of work.

Backlog entries: NL-79, NL-80, NL-81, NL-82 (new, 2026-11-17); NL-46..50, NL-62 (older, with
2026-11-17 notes). Read NL-79..82 first, then the notes on NL-50.

## 1. Intention

The operator asked "find the Claude session about `debian-rollback` so I can continue it".
Search returned ranked ids, dates and paths only. To tell the right session from the wrong ones
the answer had to be read out of the raw JSONL. The operator's verdict: a tool that needs that is
unusable.

Target behaviour: starting cold, `nyxloom-harness search <words>` shows, for each hit, enough
to decide (title, project, when, what it was about, how strongly it matches, how to resume),
ranks the topical session first, never returns the invoking session, and never fails because an
unrelated store is broken. Success test: the debian-rollback lookup is answered by reading the
first screen, without opening a transcript.

Non-goals for P0/P1: SQLite index, `show` verb, intent presets, TUI, subagent family rollup,
field-weighted ranking (all in NL-82/NL-81 later phases; see section 8).

## 2. Evidence the plan rests on (reproduce before changing code)

Store: `~/.claude/projects` (about 1,035 Claude files; 1,659 Codex files in `~/.codex/sessions`).

1. Unscoped `nyxloom-harness search debian-rollback --progress quiet` takes about 99 s and exits 1
   with stderr `error: could not search OpenCode store /home/vscode/.local/share/opencode/opencode.db: no such table: session`.
   stdout is empty. With `--client claude` the same query takes about 12 s and returns 1,196 rows. (NL-79)
2. `search zzzqqqnomatch --client claude --progress quiet` returns ONE row: the invoking session,
   score 16.079, exit 0. The query text is in its own transcript. (NL-80)
3. `search debian rollback --word-match all --client claude --progress quiet`: five sessions tie
   at 4.409. The real session `30cb3a5f-61a6-42f1-8f1c-07b095dc7506` (368 mentions of
   `debian-rollback`, title "Debian rollback snapshots with ZFS or Btrfs", custom title
   `vm-lab-pwmcp`, last activity 2026-11-13T05:55Z) ranks 5th under `--sort-by best` and about
   96th of 555 under `--sort-by date`. Over 500 of the rows are subagent files of controller
   session `c9129a68` (41 mentions). (NL-81)
4. Cause of 3: `_TERM_FREQUENCY_CAP = 64` (harness_search.py:49) clamps counts; saturated sessions
   tie; `--sort-by best` breaks ties by newest `last_activity`.
5. `extract-sessions claude --json` takes about 30 s for 1,266 sessions and labels every one
   "(interactive session)": it carries no title or cwd, so it is not a substitute.

Golden data for the tests (section 6): query `debian rollback` (all-words, claude) expects
`30cb3a5f-...` above `c9129a68-...` and above any session that only contains the query text.

## 3. Code map (nyxloom/src/nyxloom/)

- `harness_search.py` (1,935 lines): everything for search.
  - `SearchResult` dataclass (line 111), `_Document` (121), `_DiscoveredSession` (130).
  - Store discovery: `_store_sessions` (279), `_claude_sessions` (449), `_codex_sessions` (515),
    `_opencode_stores` (261), `_opencode_term_counts` (1644).
  - Counting: `_filtered_jsonl_counts` (682), `_ripgrep_raw_counts` (939), `_ripgrep_jsonl_counts`
    (1220), `_jsonl_content` (1492), `_add_text` (1425). `_active_query_terms` (1030) stops
    counting a term once it reaches the cap: the cap is also a scan early-exit. Removing it costs
    scan time (see section 4, P0-C budget).
  - `_documents` (1705) builds `_Document` per session; `search_sessions` (1831) scores and sorts
    (score loop 1907-1925, sort 1927-1935).
  - Metadata helpers to reuse for the card: `_claude_transcript_metadata` (483),
    `_codex_metadata` (560), `_jsonl_last_activity` (1572).
- `cli.py:~1960-2010`: the result table renderer (columns CLIENT, SESSION ID, SCORE, LAST
  ACTIVITY, MATCHED WORDS, SOURCE) and the `search` argument wiring. `cli_harness.py` is the entry
  module only.
- `session_extract/adapters/claude_code.py`: knows the Claude record vocabulary (`ai-title`,
  `custom-title`, `last-prompt`, ...). Reuse its readers instead of re-deriving the schema.
- Tests: `tests/test_harness_search.py` (2,436 lines). Follow its fixture style.
- Docs to keep true: `docs/CLI-REFERENCE.md`, `docs/cli-surface.json` (generated, check how it is
  regenerated), `CHANGES.md` entry.

## 4. P0: three independent bugfixes (parallelizable; each a small package)

### P0-A NL-79: one broken store must not kill the search
- Probe every selected store BEFORE scanning (OpenCode: open the db read-only, check the
  `session` table exists; Claude/Codex: root exists and is readable). The probe costs
  milliseconds; the failure currently appears only after the 99 s scan.
- Per-client isolation: a store that fails the probe, or fails mid-scan, becomes a warning line
  `skipped <client>: <reason>` on stderr and the other clients' hits are printed. An explicit
  `--client opencode` against a broken store remains a hard error (the existing
  `require_compatible_roots=client is not None` behaviour is the model).
- Exit codes: 0 complete; a distinct non-zero code for partial (a store skipped); error code only
  when nothing could be searched. Pick the numbers from the repo's closed exit-code table
  (CLI-REFERENCE.md) rather than inventing; document the new one.
- Default client set: stores that exist and probe valid.
- Tests: tmp store with an empty sqlite file (no `session` table) plus valid Claude and Codex
  fixtures. Assert: hits printed, warning present, partial exit code, `--client opencode` still
  errors, and total scan does not start before the probe fails.

### P0-B NL-80: the search must not find itself
- Drop records that are the search invocation: a tool call whose command text contains
  `nyxloom-harness search`, and its result. Do it at the record level in the counting path, not
  by filtering rows afterwards, otherwise the counts stay polluted.
- Exclude the current session by default; `--include-current` opts back in. OPEN QUESTION that
  the implementer must settle by measuring, not assuming: how to identify the current session.
  Check what environment variables the Claude Code and Codex harnesses export to child processes
  (inspect `env` inside a session) and use an explicit session-id variable if one exists. Only if
  none exists, fall back to "the transcript modified in the last N seconds that contains this
  command line", and say in the docs that it is a heuristic.
- Footer on every run: `N sessions matched` (including `0`), number of rows hidden, stores
  searched/skipped. The `zzzqqqnomatch` case must print `0 sessions matched` and exit 0.
- Tests: the zzz query returns nothing with the footer; `--include-current` brings the session
  back; a transcript fixture holding a real mention plus a search invocation counts only the
  real mention.

### P0-C NL-81 (minimum viable): ranking that does not saturate
Scope for P0 is the smallest change that fixes the observed mis-ranking. Field weights, family
rollup and `--explain` stay in later phases.
- Count distinct RECORDS containing each term, not token occurrences. A pasted log is one record.
- Replace the hard clamp with a saturating weight that includes length normalisation: BM25 form
  `tf / (tf + k1 * (1 - b + b * len / avglen))`, with `len` = the session's record count (a
  `rg -c ''` on the file is enough). Starting values k1=1.2, b=0.75; keep them as named constants.
- The early-exit cap stays as a cost bound but is raised to a record-count ceiling large enough
  that real sessions do not hit it (start at 2,000 records per term) and is documented as a bound,
  not a ranking input. Scan-time budget: claude-only query must stay at or under 25 s on this host's
  store (baseline 12 s). If the budget cannot be met, report the measurement and stop; do not
  silently keep the old behaviour.
- Tie-break order: score rounded to 3 decimals, then matched-term count, then recency. Recency
  is a tie-break only.
- Tests (golden set, mean reciprocal rank in the fixture): (1) `debian rollback` fixture: the
  discussion session outranks a newer session with one pasted log containing both words; (2) a
  session with 10 operator-turn matches outranks one with 500 matches in a single record; (3)
  controlled wrong implementation (cap kept, recency primary) must fail test 1.
- Real-store acceptance: after the change, `30cb3a5f` is rank 1 or 2 for `debian rollback`
  (rank 1 once P0-B excludes the invoking session). Record the measured ranks in the PR/report.

## 5. P1: the session card (up-front context)

Goal: each result row carries what is needed to decide without opening a transcript. This is the
contract noted on NL-50.

Columns/fields per session: client; short id plus full id; TITLE (custom title if present, else
ai-title, else `-`); project/cwd; first and last activity and duration; operator and assistant
turn counts; FIRST OPERATOR PROMPT (first non-synthetic user message, one line, ~120 chars,
skipping compaction-continuation summaries); best matching snippet with its role (operator/
assistant/tool); match breadth (operator turns matched / total); subagent count; flag
`current` (from P0-B) ; RESUME command.

Design decisions (made here; flag any the operator overrides):
- Compute cards LAZILY for the top K rows after ranking (default `--limit 10`), never for all
  matches. The scan must not slow down. Counts use `rg -c`; title, first prompt and cwd come from a
  bounded read of the file head (about 1 MB); last assistant text and the latest title record from a
  bounded read of the tail. A 106 MB controller transcript must not be parsed whole.
  Title records can sit anywhere and repeat; last one wins, so verify the tail read finds it, and
  fall back to a targeted `rg` for `"type":"custom-title"` / `"type":"ai-title"` if the tail does not.
- Compaction-continuation user messages start with "This session is being continued from a
  previous conversation"; they are synthetic and must be skipped when picking the first prompt.
- Codex: title absent in most rollouts; use `_codex_metadata` (cwd, first user message). OpenCode:
  take title/directory from the db if the columns exist, else show `-`. Do not invent values.
- Resume line: Claude `claude --resume <session-id>` (must be run from the project's cwd; print
  the `cd <cwd> &&` prefix); Codex per `codex resume --help` (VERIFY the exact syntax, do not
  guess); subagent rows print the PARENT session id and the note `subagent of <parent>`, because
  an agent id is not resumable.
- Output: default becomes the card list (top K, then footer from P0-B). `--table` keeps the legacy
  columns plus a TITLE column; `--json` emits the full structured cards. Check
  `docs/CONSUMERS.md` and repo grep for scripts parsing the old table before changing the default;
  if a consumer exists, keep the table default and put cards behind `--cards` instead, and say so
  in the report.
- Acceptance (fixture store with a Claude transcript carrying title records, a compaction
  summary, a first prompt, a subagent file, and a Codex rollout): the card shows the right
  title and first prompt, skips the compaction summary, shows the parent id for the subagent
  row, and the resume line is correct. Live acceptance: `search debian rollback --client claude`
  shows `30cb3a5f` with title "Debian rollback snapshots with ZFS or Btrfs" and a `claude --resume`
  line, and the operator can pick it from the screen.

## 6. Order, packaging, roles

1. Order: P0-A, P0-B, P0-C in parallel (separate worktrees; P0-B and P0-C both touch the counting
   path, so merge P0-B first and rebase P0-C on it; P0-A touches discovery and exit codes only).
   P1 after P0-B and P0-C are merged (needs the footer, current-session flag and the new count
   path). Release P0 as soon as it is merged; P1 as a second release.
2. Process (estate rules; read the named files, do not restate): all work via `ciu worktree`
   (memory `nyxloom-worktree-cli-rule`); edits via Edit/apply_patch, never sed or scripted
   rewrites; Codex luna implementers with intent and reasoning in the brief, a persistent
   Sonnet reviewer per package, Opus final review per series (memory
   `feedback-work-process-2026-11-13`); every merged change gets an independent adversarial review
   (no size exception); gates are the project's registered gate, read in a SEPARATE step from the
   run (LESSONS L4); host-load and devcontainer-memory limits apply (AGENTS.md; memories
   `host-shared-with-production-load-rule`, `feedback-devcontainer-8g-cap-oom-concurrency`): at most
   the number of concurrent heavy jobs they allow.
3. Real-store measurements (the 12 s / 99 s numbers) are taken with `--progress quiet` and stdout
   and stderr captured separately; take the exit code in the same command. Run them under the host
   load rules, one at a time.
4. Commit/trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`; stage only relevant
   paths and commit with `git commit --only -- <paths>`. Backlog state changes only through
   `nyxloom backlog set-status`; regenerate `INDEX.md` with `nyxloom backlog index`. Close
   NL-79/80 when P0 ships; NL-81 stays open until the field weights land; NL-82 and NL-50 stay open.
5. Each package updates `CHANGES.md`, `docs/CLI-REFERENCE.md` and the cli-surface file in the same
   change.

## 7. ETA (estimate, unmeasured; assumes dispatch starts promptly, hosts free)

Calibration: the pi-adapter package (NL-73..78) took about 6 h wall time from first implementation
commit (05:13) to its last fix commit (11:23) on 2026-11-16, with two review/fix rounds. P0 packages
are smaller than that one; P1 is comparable.

- P0 (three packages, parallel): about 4-8 h wall time to merged, including review rounds and the
  gate; the largest unknown is P0-C's scan-time budget on the real store.
- P1: about 6-10 h wall time after P0-B/P0-C merge (card extraction for three clients plus the
  output-contract decision).
- Together: roughly 1-1.5 working days to both merged, plus release time per tool release process.
  The estimate widens if the gate queue is busy, the consumer check finds a table parser, or the
  current-session detection (P0-B) has no env variable to rely on.

## 8. Later phases (not in this plan; specs live in NL-81/NL-82)

P2 result handles and `show` verb; P3 SQLite FTS5 index (also fixes the 12-99 s scans, NL-49),
field-weighted bm25, subagent family rollup (NL-48), intent presets (`--as resume|decision|
incident|touched|usage`), time scoping (NL-46), phrase search (NL-47); P4 optional TUI `browse`.
Re-plan after P1 ships; the P1 card data model should be the one `show` and the index reuse, so
keep it a plain dataclass with a `to_json()` and no rendering inside it.

## 9. Verification checklist for the final report

- [ ] NL-79 repro (empty `opencode.db`) prints hits + warning + partial exit code, fast.
- [ ] `zzzqqqnomatch` prints `0 sessions matched`, exit 0.
- [ ] `debian rollback` ranks `30cb3a5f` first on the real store; measured ranks recorded.
- [ ] Claude-only scan time recorded before/after (budget 25 s).
- [ ] Card for `30cb3a5f` shows title, project, first prompt, resume line; no transcript opened.
- [ ] Tests, docs, CHANGES, cli-surface updated; independent review per package; gate verdict read
      in a separate step.
