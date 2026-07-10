# P45 - Bounded Inspect-Files Content Reads

## Goal

Turn the P29 preview-only inspection catalog into a disabled-by-default,
bounded read-only content surface without creating an arbitrary root-file-read
primitive.

## Workflow

- Branch: `feat/groop-p45-inspect-files-bounded-content`
- Worktree: `.worktrees/-groop-p45-inspect-files-bounded-content`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P45-LOG.md` current
- Finish with `groop/handoff/reports/P45-REPORT.md` and focused commits

## Requirements

- Add a read API and `groop inspect-files read` CLI path gated by both
  `--inspect-files` and `--admin`; planning remains non-reading.
- Support bounded snapshots of allowlisted regular files from P29's resolved
  catalog. Start with Docker JSON logs and cgroup files. Journal follow and
  arbitrary volume/overlay traversal remain separate work.
- Resolve every candidate from catalog/entity metadata, then confine its real
  path to the allowlisted root. Reject user-supplied absolute paths, `..`,
  symlink escapes, devices, FIFOs, sockets, directories, and non-regular files.
- Use no shell and no mutation. Prefer direct descriptor reads with no-follow
  semantics; do not call `cat`, `tail`, Docker, or systemd to read content.
- Bound bytes, lines, time/work, and rendered output. Expose truncation and
  unavailable/error state explicitly; decode hostile bytes safely.
- Do not let Docker names masquerade as container directory IDs. Production
  Docker log reads require a validated full container ID or trusted resolved
  metadata; fixture seams may provide alternate roots.
- JSON output must be deterministic and must not echo content on denied/error
  paths. Text output must distinguish preview, content, truncation, and denial.
- Add structural safety tests proving no subprocess, no writes, no arbitrary
  path escape, and no special-file blocking, plus CLI and fixture content tests.
- Update README, ROADMAP, STATUS, OPERATIONS, INSPECT-FILES,
  RELEASE-READINESS, and MEASUREMENTS with sensitivity and non-claim notes.

## Acceptance

- Default/ungated paths read zero bytes and return a clear refusal.
- Allowed fixture Docker/cgroup files return bounded deterministic content.
- Traversal, symlink, special-file, oversized, and invalid-ID cases fail safely.
- Focused tests, full suite, CLI smoke, and full-source `py_compile` pass.

## Out Of Scope

- Arbitrary filesystem browsing.
- Follow/stream mode, journal execution, volume trees, or overlay traversal.
- File writes, deletion, editing, chmod/chown, or command execution.
- Daemon transport/API exposure.

