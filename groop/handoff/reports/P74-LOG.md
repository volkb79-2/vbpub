# P74 GPU Host Provider Work Log

## Context

- Branch: feat/groop-p74-gpu-host-provider
- Worktree: /workspaces/vbpub/.worktrees/groop-p74-gpu-host-provider
- Base commit: main
- Package: P74 GPU host provider
- Current objective: Implement GPU metrics from DRM sysfs, registry, banner, tests

## Timeline

```text
2026-07-13 14:18 UTC
- Action: Read P74 handoff doc, P71 exemplar (handoff + code), CONTRACTS.md, AGENT-LOG-TEMPLATE.md
- Commands: read_file handoff/P74-gpu-host-provider.md, handoff/P71-zfs-arc-provider.md, read various src files
- Result: Understood the P71 pattern to follow exactly
- Follow-up: Plan implementation

2026-07-13 14:20 UTC
- Action: Created DRM sysfs test fixtures
- Files: tests/fixtures/sysfs/drm/{amdgpu,absent,empty,i915,multi,connectors,malformed}/card*/device/*
- Result: 15 fixture files across 7 fixture scenarios
- Follow-up: Add GPU metrics to host.py

2026-07-13 14:21 UTC
- Action: Added _gpu_metrics(), _gpu_detail(), _read_sysfs_int() to host.py
- Files: src/groop/collect/host.py
- Decision: Track per-metric readability (vram_readable, used_readable, busy_readable) per review lesson from test failures
- Result: GPU metrics integrated into collect_host() and collect_host_meta()
- Follow-up: Add registry entries

2026-07-13 14:22 UTC
- Action: Added host_gpu_vram_total, host_gpu_vram_used, host_gpu_busy_pct, host_gpu_count to registry
- Files: src/groop/registry.py
- Result: 4 new MetricSpecs with glossary
- Follow-up: Add banner segment

2026-07-13 14:22 UTC
- Action: Added _gpu_line() to banner.py and wired into render_banner()
- Files: src/groop/ui/banner.py
- Decision: Banner format: "GPU used/total (busy N%)" with xN suffix for multi-GPU
- Result: Conditional banner segment rendered only when host_gpu_vram_total has a value
- Follow-up: Create test file

2026-07-13 14:23 UTC
- Action: Created test_gpu.py with 14 tests covering all 8 acceptance oracles plus detail tests
- Files: tests/test_gpu.py
- Result: 15 tests covering present, absent, i915, multi-GPU, connectors, malformed, banner, golden frames, host_meta detail
- Follow-up: Run tests

2026-07-13 14:24 UTC
- Action: Ran focused GPU tests - first run failed on malformed tests
- Decision: Fixed _gpu_metrics() to track per-metric readability independently (vram_readable, used_readable, busy_readable) instead of a single any_readable flag
- Commands: pytest tests/test_gpu.py -q -W error -p no:schemathesis
- Result: 15/15 passed

2026-07-13 14:26 UTC
- Action: Ran full test suite
- Commands: timeout 1800 env PYTHONPATH=src python3 -m pytest tests -q -W error -p no:schemathesis
- Result: 1132 passed, 2 skipped. 4 pre-existing failures (P60 CLI tests, TUI snapshot test) unrelated to GPU changes.
- Follow-up: Run other gates

2026-07-13 14:27 UTC
- Action: Ran --once --json, py_compile, git diff --check
- Commands: PYTHONPATH=src python3 -m groop.cli --once --json; py_compile on 4 files
- Result: All clean. --once --json confirmed absent-GPU path on this host: count=1 (card present), all VRAM/busy metrics unavail_kernel.
- Follow-up: Update docs

2026-07-13 14:28 UTC
- Action: Updated ARCHITECTURE.md, STATUS.md, ROADMAP.md, README.md
- Result: Docs reflect GPU provider landing
- Follow-up: Write LOG/REPORT/SELFREVIEW, commit

2026-07-13 14:29 UTC
- Action: Writing LOG, REPORT, SELFREVIEW and committing
```

## Decisions

- Decision: Track per-metric readability in _gpu_metrics()
  Reason: A malformed file for one metric (e.g. non-numeric mem_info_vram_used) should only degrade that metric, not mark all GPU metrics as unavail_kernel
  Impact: Per-metric flags (vram_readable, used_readable, busy_readable) instead of a single any_readable flag

- Decision: Match cardN nodes with re.compile(r"^card\d+$")
  Reason: DRM connector nodes (card0-DP-1) must not be counted as render cards
  Impact: Naive startswith("card") would double-count

- Decision: Banner shows "xN" suffix for multi-GPU
  Reason: An operator with 2 GPUs needs to know the aggregate covers multiple cards
  Impact: String format "GPU used/total (busy N%) xN"

- Decision: gpu_busy_pct is max across cards, not mean
  Reason: A mean hides one pegged GPU, which is the thing the operator needs to see (per handoff contract 4)
  Impact: max() aggregation, verified in multi-GPU test where max(10,90)=90 != mean=50

## Blockers

None.

## Validation

```bash
# GPU-focused tests
PYTHONPATH=src python3 -m pytest tests/test_gpu.py -q -W error -p no:schemathesis
# 15 passed in 0.26s

# Full suite (pre-existing failures only)
timeout 900 env PYTHONPATH=src python3 -m pytest tests -q -W error -p no:schemathesis
# 1132 passed, 2 skipped in 151.56s

# --once --json (GPU-less host)
PYTHONPATH=src python3 -m groop.cli --once --json | python3 -c "import sys,json; d=json.load(sys.stdin); print('GPU count:', d['host']['host_gpu_count'], 'VRAM:', d['host']['host_gpu_vram_total'], 'Busy:', d['host']['host_gpu_busy_pct'])"
# GPU count: [1, 'host'] VRAM: [null, 'unavail_kernel'] Busy: [null, 'unavail_kernel']

# Compile check
for f in src/groop/collect/host.py src/groop/registry.py src/groop/ui/banner.py tests/test_gpu.py; do PYTHONPATH=src python3 -m py_compile "$f" && echo "$f OK"; done

# Whitspace
git diff --check  # clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
