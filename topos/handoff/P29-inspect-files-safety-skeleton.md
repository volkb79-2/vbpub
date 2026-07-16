# P29 - Inspect-Files Safety Skeleton

**Cut:** v2 foundation. **Depends:** P21. Branch:
`feat/topos-p29-inspect-files-safety`. Follow `topos/README.md` workflow
protocol exactly.

## Goal

Start the v2 file/log inspection feature safely by adding a read-only planning
layer and CLI surface that is disabled unless explicitly requested. This package
must not browse arbitrary host files or print sensitive file contents. It should
establish the safety contract that later TUI/daemon file inspection can reuse.

## Required Context

- `topos/README.md` workflow protocol.
- `topos/TUI-SPEC.md` §4.8 "File/log/content inspection (`--inspect-files`,
  v2)" and related privacy notes.
- `topos/handoff/P21-admin-action-gating-skeleton.md` and
  `topos/handoff/reports/P21-REPORT.md` for no-execution/gating patterns.
- `topos/src/topos/cli.py`, `topos/src/topos/actions/*`,
  `topos/src/topos/collect/dockerjoin.py`.
- `topos/tests/test_actions.py` and CLI tests/smokes if present.
- `topos/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Add a small read-only inspection planning module, for example
   `topos/inspect_files/plan.py`.
2. Add an explicit CLI command such as:

```bash
topos inspect-files plan --target ENTITY_OR_CONTAINER --kind docker-logs --admin
```

   The exact subcommand shape may differ if it fits the existing CLI better,
   but it must be explicit and discoverable.
3. Gate the command:
   - without `--inspect-files`, return a disabled result/message;
   - without `--admin`, return a disabled result/message;
   - with both flags, produce a **plan only**. Do not read log contents, do not
     tail files, do not run Docker/systemd/journalctl.
4. Support a small allowlist of plan kinds:
   - `docker-json-log`: plan the expected Docker json-file log path for a
     container id/name target;
   - `systemd-journal`: plan a `journalctl` query argv for a systemd unit;
   - optionally `cgroup-files`: list a fixed set of known cgroup filenames
     relevant to topos snapshots.
5. Plans must be structured dataclasses with JSON conversion and text rendering.
   Use argv lists for command previews, never shell strings.
6. Path safety:
   - normalize path previews;
   - reject absolute path targets supplied directly by users unless they are
     derived from the allowlisted kind;
   - do not follow symlinks or open files in this package.
7. Add tests proving:
   - disabled without `--inspect-files`;
   - disabled without `--admin`;
   - enabled plans are deterministic JSON/text;
   - plan argv is a list, not a shell string;
   - no subprocess execution or file content reads are performed;
   - unsafe direct paths are rejected or never accepted by the parser.
8. Update docs after implementation:
   - `README.md` P29 row should become Done;
   - `docs/STATUS.md` should move file inspection from "not implemented" to
     "safety skeleton only";
   - `docs/ROADMAP.md` should add/mark P29 and leave content browsing/TUI
     integration as future work;
   - add `docs/INSPECT-FILES.md` if useful for the safety contract.

## Scope - Out

- No actual file content reads.
- No log tail/follow.
- No daemon integration.
- No TUI screen.
- No Docker/systemd subprocess calls.
- No host mutation, root operations, or writes outside tests/reports.
- No changes outside `topos/**`.

## Design Notes

- This should mirror the admin action safety posture: deterministic previews,
  explicit flags, JSON/text output, no execution.
- Keep it DRY and small. A catalog enum plus planner functions is enough.
- Prefer "plan" language in user-facing text so operators cannot mistake this
  for live inspection.
- If adding CLI JSON output, keep it stable and test exact keys.

## Acceptance

- Full suite passes:

```bash
python3 -m pytest topos/tests -q
```

- Compile check passes for changed Python files.
- Focused tests cover gating, JSON/text rendering, path/argv safety, and
  no-execution/no-content-read guarantees.
- `topos/handoff/reports/P29-LOG.md` and
  `topos/handoff/reports/P29-REPORT.md` are written and current.

## Handoff Requirements

- Keep `topos/handoff/reports/P29-LOG.md` current using
  `topos/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P29-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract-change proposals.
- Commit the feature branch with a focused message.
