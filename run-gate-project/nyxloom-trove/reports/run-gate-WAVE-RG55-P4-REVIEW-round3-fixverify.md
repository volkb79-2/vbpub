# Verdict: ACCEPT

**Package:** RG-55 P4 (`RG-57` through `RG-61`)  
**Review:** fresh Luna xhigh round-3 fix verification  
**Tip reviewed:** `28bc3feb179142aaf28b6885f983b94539e85859`  
**P2 base:** `186461de5ef5e58031c10a65c3ebf1087dfae76f`  
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`

The two blockers from the prior round-3 rejection are fixed and verified. No
product file was modified, no merge/release was performed, and the existing
untracked rejection report was preserved.

## B1 — synchronous exec-client spawn failure clears the record: PASS

The repair moves `subprocess.Popen(argv)` inside the `try` beginning at
`run-gate-project/run-gate.py:7715`, keeps a nullable `proc`, and places
`clear_inflight_record()` in the nested `finally` at `:7768-7773`. A
synchronous `Popen` exception remains unhandled as the lane-execution failure,
but the record cleanup runs. The already-started-child path still reaches the
same cleanup only when Python reaches its `finally`; an externally killed
client never reaches it and leaves the record for reconciliation.

The new focused test passed:

```text
PYTHONDONTWRITEBYTECODE=1 nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py -q -p no:randomly -p no:cacheprovider -k 'test_popen_failure_clears_record_and_reports_lane_failure'
1 passed, 1061 deselected in 0.64s
```

An independent live probe exercised the real `run_exec_lane` boundary with a
real `Popen` call made to a deliberately absent executable. It observed:

```text
real Popen exception: FileNotFoundError [Errno 2] No such file or directory: '/definitely/missing/docker'
record existed at real Popen: True
record runner at real Popen: exec
record exists after run_exec_lane: False
```

The existing real client-death test also passed independently:

```text
PYTHONDONTWRITEBYTECODE=1 nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py -q -p no:randomly -p no:cacheprovider -k 'test_killed_client_leaves_the_record_on_disk'
1 passed, 1061 deselected in 1.03s
```

The broader focused serial set covering both changed cleanup behavior and the
bare-host profiling wiring passed `24 passed, 1038 deselected in 4.11s`.

## B2 — current normative prose uses wait4 and states limits: PASS

Current `SPEC.md:1648-1659` now says the bare-host fallback uses
`os.wait4()` on the lane's own child, converts `ru_maxrss * 1024` from Linux
KiB to bytes, and has no cgroup/pressure/DAMON/events data. Its detailed
`R-43i` text at `SPEC.md:1766-1832` states the own-child guarantee, CPU
accounting, `scope: null`, null discipline for unavailable fields, and the
fork/COW floor caveat.

Current `CONSUMERS.md:57-77` gives the same adoption-facing contract: own-child
`os.wait4()`, `ru_maxrss * 1024`, and no cgroup/pressure/DAMON/events data when
the daemon is unavailable.

The current prose/test sweep was:

```text
rg -n -i 'getrusage|RUSAGE_CHILDREN' run-gate-project/run-gate.py run-gate-project/tests --glob '*.py'
```

The only five matches are explicitly historical explanations: the module
header's “FIRST cut”, `finish_bare_host_profiling`'s “earlier ... shape”, the
run-lane comment's “rather than” explanation, the stdlib-allowlist test's
“originally used ... replaced”, and the arithmetic test's explanation of the
rewrite. The public-document sweep likewise found the only SPEC occurrence in
the explicitly rejected-algorithm explanation; `CHANGES.md` occurrences are
changelog history. No current overview, consumer guidance, or relevant test
docstring presents `getrusage(RUSAGE_CHILDREN)` as the shipped algorithm.

## Ranked findings

1. **None.** The requested blockers are closed, and the repair-range audit
   found no false-green or cleanup regression requiring a new finding.

## Commands and environment

- Checkout identity: `git rev-parse HEAD` returned exactly
  `28bc3feb179142aaf28b6885f983b94539e85859`; `git status` showed only the
  pre-existing untracked round-3 rejection report before this report was
  created.
- Repair diff: `git diff --stat 068c1dd0f80b55553291982cb87b70f56eb5f936
  28bc3feb179142aaf28b6885f983b94539e85859` reported six changed product/docs
  files, including the exec cleanup, tests, SPEC, and CONSUMERS changes.
- Fresh PSI checks were performed before the probes/tests. The initial host
  was heavily loaded (`full avg10=30.72`, later `32.63`), so no registered
  gate was attempted. Before the final real-Popen probe the fresh reading was
  `full avg10=3.66`; all test/probe commands used `nice -n 19 ionice -c 3`.
- `git diff --check 068c1dd0f80b55553291982cb87b70f56eb5f936
  28bc3feb179142aaf28b6885f983b94539e85859`: exit 0.
- No registered gate and no mutation campaign was launched, as instructed.
  Existing host containers/processes were observed read-only and not stopped
  or modified.

The only deliberate worktree write from this review is this fix-verification
report; the prior untracked rejection report remains untouched.
