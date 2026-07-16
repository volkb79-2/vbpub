# P42 Work Log

## Context

- Branch: `feat/topos-p42-daemon-bpf-snapshot-bridge`
- Worktree: `.worktrees/-topos-p42-daemon-bpf-snapshot-bridge`
- Base commit: fba1d89 (docs(topos): carve P42 daemon BPF snapshot bridge)
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
- Action: Create P42-LOG.md, implement topos/src/topos/daemon/bpf_snapshot.py
- Files changed: topos/src/topos/daemon/bpf_snapshot.py
- Result: BpfSnapshotBridge module with bpftool runner, path confinement, cgroup resolver, snapshot builder, atomic writer.
- Follow-up: Add config, CLI integration, tests.

2026-07-09 23:45 UTC
- Action: Add BpfSnapshotConfig to config.py, integrate into CLI daemon serve.
- Files changed: topos/src/topos/config.py, topos/src/topos/cli.py, topos/src/topos/daemon/__init__.py
- Result: Config section, CLI --bpf-root/--bpf-interval, disabled-by-default.
- Follow-up: Write tests.

2026-07-09 23:55 UTC
- Action: Write focused unit tests for BpfSnapshotBridge.
- Files changed: topos/tests/test_daemon_bpf_snapshot.py
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
- Decision: Keep the atomically replaced snapshot as the on-disk last-good file
  and restore its validated minimum schema on daemon restart.
  Reason: A transient first refresh must not discard usable prior telemetry.
  Impact: No parallel `.last` file or cleanup path is required.
- Decision: Use Python's `os.replace()` for atomic file replacement after fsync of temp file.
  Reason: POSIX atomic rename on same filesystem; matches requirement.
  Impact: Safe against partial writes.
- Decision: Resolve IDs from directory inodes under the daemon's configured
  cgroup-v2 root, with an injectable resolver and an explicit unverified kernel
  identity boundary.
  Reason: The snapshot needs a deterministic userspace ID-to-path mapping.
  Impact: Live identity still requires validation on the privileged test host.

## Blockers

None.

## Validation

```bash
# Focused tests
PYTHONPATH=topos/src /home/vscode/.venv/bin/python -m pytest topos/tests/test_daemon_bpf_snapshot.py -q
# 48 passed in 0.35s

# Full suite
PYTHONPATH=topos/src /home/vscode/.venv/bin/python -m pytest topos/tests -q
# 431 passed, 1 skipped, 1 warning in 47.90s

# py_compile
python3 -m py_compile topos/src/topos/daemon/bpf_snapshot.py topos/src/topos/config.py topos/src/topos/cli.py topos/tests/test_daemon_bpf_snapshot.py
# clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.

2026-07-10 00:45 UTC
- Action: Controller review of e8b9249 identified 9 categories of fixes.
- Files changed: bpf_snapshot.py, config.py, net_bpf.py, cli.py, test_daemon_bpf_snapshot.py, STATUS.md, P42-REPORT.md
- Result: Agent correction committed as 2d7c86b; further controller review found
  configured-cgroup-root, schema-validation, required-counter, no-op-test, and
  stale-evidence gaps.
- Follow-up: Commit follow-up with disclosure.

## Controller Review Fixes

1. **state_dir** (was: writing JSON into bpffs). Added `BpfSnapshotConfig.state_dir` defaulting to `/run/topos/bpf`. Bridge writes to state_dir, not bpf_root. `BpfProvider` reads from state_dir when provided.
2. **BpfProvider at highest rank.** Daemon serve integrates BpfProvider first in the Collector's network_providers tuple when bridge enabled.
3. **CalledProcessError/TimeoutExpired.** `_subprocess_runner` and `_run_bpftool` both catch these and convert to bounded BpfSnapshotError with limited stderr output.
4. **Path.is_relative_to.** Replaced string prefix matching with `Path.is_relative_to`. Added sibling-prefix symlink escape test.
5. **Immediate refresh.** `refresh_and_write()` called before thread start; failures logged but thread continues.
6. **Integration tests.** Added 15 new tests covering raw byte rejection, sibling-prefix escape, CalledProcessError, TimeoutExpired, restore_last_known_good, refresh_and_write, state_dir config, BpfProvider with state_dir.
7. **Cgroup docs tightened.** Removed specific kernel version claim; documented as kernel-version dependent and unverified.
8. **Raw byte array rejection.** Explicitly rejected in `_parse_bpftool_output`. Per-CPU array values also rejected. BTF-typed structured output required.
9. **Docs/reports updated.** STATUS.md test counts corrected. P42-REPORT.md updated with controller review disclosure and new test counts.

2026-07-10 controller final review
- Action: Bound the default cgroup resolver to the daemon's configured root,
  validate restored snapshot shape and resolver output, reject missing counters,
  remove the no-op test, and align all evidence.
- Result: 48 focused tests passed, including enabled daemon wiring and bounded
  shutdown; full suite 431 passed, 1 skipped; py_compile clean.
- Acceptance regression: 40 passed in 6.99s; TUI smoke exit 0 with `ok=true`,
  one frame, tree view, and auto profile.
- Follow-up: Commit the controller correction and merge after main-state check.

2026-07-10 post-merge validation
- Merge: `8e48498` (`Merge topos P42 daemon BPF snapshot bridge`).
- Focused: 48 passed in 0.29s.
- Full suite: 431 passed, 1 skipped in 47.40s.
- Acceptance: 40 passed in 7.41s.
- TUI smoke: exit 0, `ok=true`, one frame, tree view, auto profile.
- Full-source `py_compile`: clean.
