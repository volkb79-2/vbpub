# nyxloom 0.10.0 — hand-written release notes (draft)

Moved out of `CHANGES.md` `## [Unreleased]` on 2026-10-07 so `cmru release` can generate the full 0.10.0 section; cmru KI-30 refuses a non-empty hand-written `[Unreleased]`. After the release, fold this summary into the top of the generated `[0.10.0]` section and delete this file.

## SUCCESSOR package (nyxloom-successor-2026-10)

### Added
- feat(nyxloom): `nyxloom extract --successor-brief [--order TEXT|@FILE] [--brief-max-chars N]` -- one markdown document that primes a FRESH agent from a stopped subagent's transcript: header, the predecessor's original brief (verbatim, or a pointer + sha256 when longer than the cap), later turns, timeline, whole-session ledger, stop state, and the controller's order. Claude Code adapter only.
- feat(nyxloom): `--tool-calls none|intent|intent-or-call|call` and `--tool-errors show|hide` (default show: failed tool results are rendered, truncated, whatever the call mode). `--show-tool-calls` / `--show-tool-call-intent` stay as deprecated aliases with byte-identical output and a stderr note.
- feat(nyxloom): whole-session ledger with an external-effects bucket detected from Bash commands (`--effect-pattern`, repeatable; `--no-default-effect-patterns`), and `--stop-state` (cause from `.meta.json` `stoppedByUser`, in-flight call, last intent).
- feat(nyxloom): `.claude/skills/nyxloom-successor` skill; standing Bash-`description` line added to the nyxloom-dispatch implementer and reviewer checklists.
- feat(nyxloom): `nyxloom extract` options are grouped in `--help` (Source & range, Content selection, Rendering & compression, Derived sections, Output) and four named presets `--preset watch|successor|review|ledger` bundle them (exact expansions in `--help`, `docs/CLI-REFERENCE.md`, pinned by `tests/test_session_extract_presets.py`); an explicit option always wins. The `successor` preset includes `--stop-state`.
- feat(nyxloom): `--prose-only` (operator messages + assistant prose; interviews kept compactly as one question line plus the operator's answer), `--no-prose`, `--no-ledger`, `--no-stop-state`, and `--jsonl`, a STABLE VERSIONED line format `{"v": 1, "ts", "role", "text", "agent"?}` for the VS Code extension (keys pinned by tests; additions need a new `v`).
- feat(nyxloom): agent-control calls (Agent, SendMessage, TaskStop) appear in the `--ledger` external effects; a harness-version warning when a transcript's Claude Code version is not in `VERIFIED_HARNESS_VERSIONS`.
- test(nyxloom): real-corpus fixture `tests/fixtures/real_interview_2_1_289.jsonl` (verbatim records of a 2.1.289 transcript) and an opt-in whole-transcript smoke test (`NYXLOOM_REAL_TRANSCRIPT`).

### Changed
- fix(nyxloom): failed tool results are shown by default (also in `--json`); `--ledger` always appends the whole-session block; the default effect patterns detect MUTATING forms only (D2 narrowing; residuals NL-35).
- fix(nyxloom): a harness interrupt record (`[Request interrupted by user ...]`) is classified as a STOP marker, no longer rendered as `OPERATOR:`; `--task` banner now says the order is "supplied by the requester of this extract -- the controller or the operator".

### Documentation
- docs: E-020 (resume matrix: who stopped a subagent decides resumability; `claude attach/respawn` apply to `--bg` sessions, not Agent-tool subagents); E-015 corrected in place. Follow-ups NL-31..NL-34.

## Also in 0.10.0 (not in the generated subject list's words)
- `nyxloom/mattermost/tools/mm_reachability.py`: a dynamic, read-only Mattermost reachability check driven by the stack's ciu config (pings, webhooks, PATs, accounts; secrets redacted; `--post` opt-in).
- `[intake_bridge]` in `nyxloom-trove/nyxloom.toml` now targets the current Mattermost instance (`nyxloom-3oqua1`); NL-38 tracks deriving it.

Housekeeping still open: a stale `## [Unreleased] - UNRELEASED` draft from the 0.6 session_extract wave sits in `CHANGES.md` between `[0.6.0]` and `[0.5.1]`; its content shipped in 0.6.0, so it can be removed during the post-release curation.
