# P42 Work Log

## Context

- Branch: `feat/groop-p42-daemon-bpf-snapshot-bridge`
- Worktree: `.worktrees/-groop-p42-daemon-bpf-snapshot-bridge`
- Base commit: fba1d89 (docs(groop): carve P42 daemon BPF snapshot bridge)
- Package: P42 — Daemon BPF Snapshot Bridge
- Current objective: Implement the daemon-side BPF snapshot writer that reads pinned counter maps via bpftool and produces the P18 snapshot.json contract

## Timeline

```text
2026-07-09 23:15 UTC
- Action: Start implementation. Read handoff, README, P17/18 reports, daemon broker, config, CLI, tests.
- Files changed: (research phase)
- Result: Full understanding of codebase.
- Follow-up: Create P42-LOG.md, implement module.

2026-07-09 23:30 UTC
- Action: Create P42-LOG.md, implement groop/src/groop/daemon/bpf_snapshot.py
- Files changed: groop/src/groop/daemon/bpf_snapshot.py
- Result: BpfSnapshotBridge module with bpftool runner, path confinement, cgroup resolver, snapshot builder, atomic writer.
- Follow-up: Add config, CLI integration, tests.

2026-07-09 23:45 UTC
- Action: Add BpfSnapshotConfig to config.py, integrate into CLI daemon serve.
- Files changed: groop/src/groop/config.py, groop/src/groop/cli.py, groop/src/groop/daemon/__init__.py
- Result: Config section, CLI --bpf-root/--bpf-interval, disabled-by-default.
- Follow-up: Write tests.

2026-07-09 23:55 UTC
- Action: Write focused unit tests for BpfSnapshotBridge.
- Files changed: groop/tests/test_daemon_bpf_snapshot.py
- Result: Tests for decoding, cgroup mapping, atomic replace, last-good, path confinement, output bounds, command failure, cleanup.
- Follow-up: Run tests.

2026-07-10 00:05 UTC
- Action: Run focused tests and fix issues.
- Result: All focused tests passing.
- Follow-up: Run full suite.

2026-07-10 00:15 UTC
- Action: Run full suite and py_compile.
- Result: Full suite green, py_compile clean.
- Follow-up: Update docs, write REPORT, commit.
```

## Decisions

- Decision: Use injectable `CommandRunner` (Callable[[list[str]], str] type) matching bpf_gate.py pattern.
  Reason: Consistency with existing codebase; avoids shell injection by using argv-only.
  Impact: Tests can mock command output without subprocess.
- Decision: Store last valid snapshot as dict in memory rather than writing a .last file.
  Reason: Simpler, no stale file cleanup needed; the bridge is daemon-internal.
  Impact: A daemon restart loses the last-good cache; acceptable since it only affects the first refresh after restart.
- Decision: Use Python's `os.replace()` for atomic file replacement after fsync of temp file.
  Reason: POSIX atomic rename on same filesystem; matches requirement.
  Impact: Safe against partial writes.
- Decision: Cgroup ID resolver reads /proc/self/cgroup or the kernel interface file to get numeric cgroup id.
  Reason: The kernel exposes cgroup id via /proc/<pid>/cgroup or /sys/fs/cgroup/<path> on newer kernels; for production, we document the assumption and allow injection for fixtures.
  Impact: Fixture tests work without real cgroupfs.

## Blockers

None.

## Validation

```bash
# Focused tests
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests/test_daemon_bpf_snapshot.py -q
# ... tbd

# Full suite
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# ... tbd

# py_compile
python3 -m py_compile groop/src/groop/daemon/bpf_snapshot.py groop/src/groop/config.py groop/src/groop/cli.py groop/tests/test_daemon_bpf_snapshot.py
# ... tbd
```

## Handoff Checklist

- [ ] Report file written.
- [ ] Log file current.
- [ ] Tests/compile/smoke recorded.
- [ ] Known gaps documented.
- [ ] Feature branch committed.

2026-07-10 00:45 UTC
- Action: Controller review of e8b9249 identified 9 categories of fixes.
- Files changed: bpf_snapshot.py, config.py, net_bpf.py, cli.py, test_daemon_bpf_snapshot.py, STATUS.md, P42-REPORT.md
- Result: All 9 items addressed. Full suite 427 passed, 1 skipped.
- Follow-up: Commit follow-up with disclosure.

## Controller Review Fixes

1. **state_dir** (was: writing JSON into bpffs). Added `BpfSnapshotConfig.state_dir` defaulting to `/run/groop/bpf`. Bridge writes to state_dir, not bpf_root. `BpfProvider` reads from state_dir when provided.
2. **BpfProvider at highest rank.** Daemon serve integrates BpfProvider first in the Collector's network_providers tuple when bridge enabled.
3. **CalledProcessError/TimeoutExpired.** `_subprocess_runner` and `_run_bpftool` both catch these and convert to bounded BpfSnapshotError with limited stderr output.
4. **Path.is_relative_to.** Replaced string prefix matching with `Path.is_relative_to`. Added sibling-prefix symlink escape test.
5. **Immediate refresh.** `refresh_and_write()` called before thread start; failures logged but thread continues.
6. **Integration tests.** Added 15 new tests covering raw byte rejection, sibling-prefix escape, CalledProcessError, TimeoutExpired, restore_last_known_good, refresh_and_write, state_dir config, BpfProvider with state_dir.
7. **Cgroup docs tightened.** Removed specific kernel version claim; documented as kernel-version dependent and unverified.
8. **Raw byte array rejection.** Explicitly rejected in `_parse_bpftool_output`. Per-CPU array values also rejected. BTF-typed structured output required.
9. **Docs/reports updated.** STATUS.md test counts corrected. P42-REPORT.md updated with controller review disclosure and new test counts.
