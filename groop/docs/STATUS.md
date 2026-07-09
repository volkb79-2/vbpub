# groop Implementation Status

This file describes the current implementation state against `TUI-SPEC.md`.
It is intentionally separate from the spec: the spec says where the product is
going; this file says what is true in the code today.

## Summary

Current state: **v0 complete, v1 mostly implemented, v1.5 mostly implemented,
v2 not started beyond architectural seams.**

Approximate status:

| Release cut | Feature implementation | Release confidence | Notes |
|---|---:|---:|---|
| v0 collector proof | 100% | high | Collector/model/registry/`--once --json` are implemented and tested. |
| v1 read-only TUI | 80-85% | medium | Core daily triage works. Remaining gaps are release evidence, UI polish, richer host banner/device surfaces, and some acceptance criteria. |
| v1.5 DAMON/snapshots/backend awareness | 85-90% | medium | Passive/control APIs, CLI paths, TUI typed-confirmation modals, snapshots, and ZRAM/swap-backend awareness exist with fixture tests. Real-root acceptance still needs a deliberate test host. |
| v2 daemon/BPF/admin actions | 10-15% | low | Provider abstractions, safety patterns, and a read-only Unix-socket daemon spike exist; attach mode, BPF, admin actions, file inspection, GPU/ZFS plugins are not implemented. |

These percentages are engineering estimates, not release tags. The strongest
claim the repo can currently make is: **feature-complete prototype for v1/v1.5
core workflows, not yet production-certified.**

## Implemented

- Cgroup v2 collector with reset-safe rates and graceful unavailable/unlimited
  source labels.
- Canonical frame model and registry-backed metric validation.
- Docker metadata join for `docker-<64hex>.scope` entities.
- Host facts: meminfo, load, uptime, PSI, zswap fallback/debugfs support, and
  legacy disk-swap estimate.
- Process drill-down from `cgroup.procs` and `/proc/<pid>`.
- Record/replay using headered JSONL, plus optional zstd when available.
- Fixed-capacity numeric history ring.
- Network provider abstraction with host truth and netns approximation.
- Origin/drift detection for systemd-managed values versus live cgroup files.
- Pressure score and rule-based findings.
- Textual TUI with tree/container views, profiles, sorting, filtering, banner,
  entity drill-down, glossary, snapshot hotkey, and host-memory screen.
- Passive DAMON vaddr attribution and paddr host-session detection.
- Controlled DAMON vaddr and paddr start APIs/CLI with root guards, typed
  confirmation, ownership markers, and audit logs.
- Incident snapshots with bounded frame capture, raw cgroup copies, provider
  status, manifest hashes, redaction, and `groop snapshot inspect`.
- Read-only Unix-socket daemon broker spike with current/stream protocol and
  socket tests.

## Partially Implemented

- **System banner:** host verdict, pressure summary, and paddr heat exist.
  Per-device disk/network banner lines and CPU breakdown sparklines from spec
  §3.0 are not complete.
- **Compressed swap:** zswap host/cgroup metrics, host ZRAM totals,
  `/proc/swaps` backend classification, and mixed-backend banner wording exist.
  Per-device ZRAM drill-down is not yet rendered. See
  `docs/COMPRESSED-SWAP.md`.
- **Tree view:** full tree rendering and expand/collapse state exist.
- **Replay UI:** replay feeds the same UI with mode/status, pause/resume,
  stepping, speed controls, and smoke testing. Timestamp jump remains a future
  improvement.
- **Custom profiles:** named profile lists work and unsupported configured
  columns are surfaced as ignored metadata.
- **Diagnostics inputs:** pressure score works, but true `io_cap_saturation_pct`
  and attributable network loss/retransmit inputs are absent.
- **DAMON controls:** underlying APIs, CLI, and TUI typed-confirm modals are
  fixture-tested. Live-root acceptance still needs a deliberate test host.
- **Snapshots:** snapshot bundles include bounded frame history, cgroup files,
  provider status, fresh systemctl/docker metadata where available, redaction,
  CLI inspect, and hash verification. A progress UI remains future polish.
- **Acceptance evidence:** P12 records tests, packaging, fixture JSON, replay
  smoke, wheel install, version, and bounded once/json CPU/RSS. `MEASUREMENTS.md`
  still needs a full 5-minute live perf/RSS run, DAMON, and future BPF
  measurements.

## Not Implemented

- Production daemon packaging and `groop --attach`.
- Exact BPF per-cgroup network provider and BPF ownership lifecycle.
- Docker/systemd admin actions: update/start/stop/restart/kill.
- `systemctl set-property` governance actions.
- File/log/content browser behind `--inspect-files`.
- Web UI.
- GPU and ZFS optional plugins.
- CIU stack grouping/actions.
- paddr auto-start / persistent daemon-owned paddr mode.
- Per-device ZRAM drill-down.

## Acceptance Status

| Spec §9 item | Current status |
|---|---|
| 1. CPU performance | Bounded once/json CPU smoke recorded in P12; required 5-minute steady-state TUI run still needed. |
| 2. Memory budget | Bounded once/json max RSS recorded in P12; live TUI RSS measurement still needed. |
| 3. Counter reset handling | Covered by tests. |
| 4. Finding-D raw-write drift | Covered by tests; live destructive acceptance not run. |
| 5. Non-container visibility | Covered by fixtures and UI tests. |
| 6. Graceful degradation | Covered by focused tests; more host matrix evidence would help. |
| 7. Registry semantics | Covered by registry/model tests and branch-policy labels. |
| 8. Diagnostics | Covered by tests; missing richer inputs noted above. |
| 9. Network labels | Covered by provider tests. |
| 10. Record/replay fidelity | Model equality covered; byte-for-byte rendered table acceptance still needed. |
| 11. Packaging | P12 built sdist/wheel and verified fresh wheel install; pipx-specific install still optional evidence. |
| 12. v2 gating | Mostly out of scope; reserved-action UX still should be explicit. |
| 13. Unprivileged smoke | Basic non-root smoke was run in P7; formal repeat should be recorded. |
| 14. Measurement gates | `MEASUREMENTS.md` created, but BPF/DAMON overhead gates are not recorded. |

## Current Quality Gate

Most recent package validation from P16:

```bash
/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 96 passed in 15.30s
```

Also validated: Python compile over `src/groop`, `--once --json`, replay UI
smoke. P12 separately validated build, wheel install, and `groop --version`.
