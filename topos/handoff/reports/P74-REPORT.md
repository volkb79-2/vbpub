# P74 - GPU Host Provider — Implementation Report

## What Was Built

Host-level GPU metrics from the DRM sysfs tree (`/sys/class/drm/card*/device/`),
following the P71 ZFS ARC provider pattern exactly.

### Source changes

| File | Change |
|---|---|
| `src/topos/collect/host.py` | Added `_gpu_metrics()`, `_gpu_detail()`, `_read_sysfs_int()`, `import re`, `_CARD_RE`. Integrated into `collect_host()` and `collect_host_meta()`. |
| `src/topos/registry.py` | Added 4 new `MetricSpec` entries: `host_gpu_vram_total`, `host_gpu_vram_used`, `host_gpu_busy_pct`, `host_gpu_count`. |
| `src/topos/ui/banner.py` | Added `_gpu_line()` and wired into `render_banner()` after ARC line. |
| `docs/ARCHITECTURE.md` | Added DRM sysfs dataflow edge and updated module map description. |
| `docs/STATUS.md` | Changed "Not Implemented — GPU optional plugins" to track P74. |
| `docs/ROADMAP.md` | Marked P74 `:done:`. |
| `README.md` | Updated P74 row from Queued to Done with report link. |

### Test files

| File | Tests |
|---|---|
| `tests/test_gpu.py` | 15 tests covering all 8 acceptance oracles |
| `tests/fixtures/sysfs/drm/` | 3 fixture sets, 12 files (amdgpu, connectors, multi). The absent/empty/i915/malformed cases are constructed in-test, not from fixture files. |

### New metrics

| Metric | Unit | Kind | Aggregation | Sources |
|---|---|---|---|---|
| `host_gpu_vram_total` | bytes | gauge | sum across cards | `/sys/class/drm/card*/device/mem_info_vram_total` |
| `host_gpu_vram_used` | bytes | gauge | sum across cards | `/sys/class/drm/card*/device/mem_info_vram_used` |
| `host_gpu_busy_pct` | % | gauge | max across cards | `/sys/class/drm/card*/device/gpu_busy_percent` |
| `host_gpu_count` | count | gauge | total | `/sys/class/drm/card*` |

All metrics declare `locality="local"`, `branch_policy="n/a"`, `aggregatable=False`.

### Vendor matrix

| Driver | VRAM total | VRAM used | busy % | Source |
|---|---|---|---|---|
| amdgpu | `mem_info_vram_total` | `mem_info_vram_used` | `gpu_busy_percent` | DRM sysfs |
| i915/xe | absent | absent | absent | (no amdgpu files) |
| nvidia (proprietary) | absent | absent | absent | (no amdgpu files) |

### Banner format

- Single amdgpu: `GPU 4.2GiB/8.0GiB (busy 37%)`
- Multi amdgpu: `GPU 3.0GiB/8.0GiB (busy 90%) x2`
- No readable GPU (absent, i915, nvidia): no GPU segment at all
- Host_meta `["gpu"]["card0"]` carries per-card detail where available

## Deviations from Handoff

**Corrected at frontier review (pass #2) — the claim below originally read "None":**

1. **Contract 2 / oracle 2 (`host_gpu_count` on an absent GPU) was not met as
   written.** The implementation returned `MetricValue(0, "host")` for the count
   in every absent case, while contract 2 requires `v=None, src="unavail_kernel"`
   ("never 0"). The review fix splits the two absent cases, which the handoff had
   collapsed: no `/sys/class/drm` at all -> count is `unavail_kernel` (nothing was
   measured, so 0 would be a fabrication); a DRM tree that exists but holds no
   cards -> count is a real `0`. See `P74-REVIEW.md`.
2. **Fixture inventory was overstated** (claimed 7 sets / 15 files; 3 sets / 12
   files are tracked). The `malformed` fixture was never read by any test and was
   removed at review; the malformed cases are constructed in-test.

Everything else in the handoff (contracts 1, 3-8; oracles 1, 3-8) was met as
specified.

## Proposed Contract Changes

None. No frozen interfaces were touched. GPU metrics are additive host metrics
in the existing `host: dict[str, MetricValue]` pattern; `host_meta["gpu"]` follows
P23/P71 precedent.

## Test Evidence

Environment: Linux x86_64, Python 3.14, Debian 13, no discrete amdgpu GPU.

### GPU-focused tests (all green)
```bash
$ PYTHONPATH=src python3 -m pytest tests/test_gpu.py -q -W error -p no:schemathesis
...............                                                          [100%]
15 passed in 0.26s
```

### Full test suite (4 pre-existing failures unrelated to P74)
```bash
$ timeout 900 env PYTHONPATH=src python3 -m pytest tests -q -W error -p no:schemathesis
# 1132 passed, 2 skipped in 151.56s
# 4 pre-existing failures: 3x test_fieldlist_* (topos/src command not found),
#   1x test_pilot_snapshot_hotkey_writes_bundle (TUI env)
```

The 4 failures are environment-specific issues that exist on this host independently
of GPU changes. The baseline `main` suite (1101 passed) grew to 1132 due to our 15
new GPU tests plus carryover from earlier packages.

### --once --json absent-GPU validation (live on this GPU-less review host)
```bash
$ PYTHONPATH=src python3 -m topos.cli --once --json | python3 -c "
import sys,json
d=json.load(sys.stdin)
gpu=d['host']
print('count:', gpu['host_gpu_count'])
print('total:', gpu['host_gpu_vram_total'])
print('used:', gpu['host_gpu_vram_used'])
print('busy:', gpu['host_gpu_busy_pct'])
"
count: [1, 'host']
total: [null, 'unavail_kernel']
used: [null, 'unavail_kernel']
busy: [null, 'unavail_kernel']
```

Confirms the absent-path: this host has a card (count=1) but no amdgpu files →
all VRAM/busy metrics degrade to `unavail_kernel`. The banner correctly omits the
GPU segment on this host.

### Compile and whitespace
```bash
$ for f in src/topos/collect/host.py src/topos/registry.py src/topos/ui/banner.py tests/test_gpu.py; do PYTHONPATH=src python3 -m py_compile "$f" && echo "$f OK"; done
# All 4 OK

$ git diff --check   # clean
```

## Known Gaps / Open Items

- **Live amdgpu acceptance**: this host has no amdgpu GPU, so the present-path
  was validated only through fixture tests (oracle 1). A controller with an
  amdgpu host should run `topos --once --json` and verify the banner by eye.
- **No change to diagnostics or pressure score**: GPU pressure is not yet a
  diagnostic input. That would require a rule design and evidence, per handoff
  Out Of Scope.
- **No per-cgroup GPU attribution**: explicitly out of scope and documented as
  unavailable in the registry glossary. The kernel does not attribute GPU memory
  to cgroups via DRM sysfs.
- **CIU remains**: the Optional-plugins bucket still has CIU grouping/actions
  as the remaining item (noted in ROADMAP.md).
