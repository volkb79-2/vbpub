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
| v1.5 DAMON/snapshots/backend awareness | 70-80% | medium-low | Passive/control APIs and CLI paths exist with fixture tests. Full Textual confirmation modals, real-root acceptance, and ZRAM/swap-backend awareness are still missing. |
| v2 daemon/BPF/admin actions | 5-10% | low | Provider abstractions and safety patterns exist; daemon, BPF, admin actions, file inspection, GPU/ZFS plugins are not implemented. |

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

## Partially Implemented

- **System banner:** host verdict, pressure summary, and paddr heat exist.
  Per-device disk/network banner lines and CPU breakdown sparklines from spec
  §3.0 are not complete.
- **Compressed swap:** zswap host/cgroup metrics exist. ZRAM detection,
  per-device ZRAM host metrics, `/proc/swaps` backend classification, and
  mixed-backend wording are not implemented. See `docs/COMPRESSED-SWAP.md`.
- **Tree view:** full tree rendering exists; expand/collapse state does not.
- **Replay UI:** replay feeds the same UI and supports smoke testing, but
  transport controls and visible replay state are minimal.
- **Custom profiles:** named profile lists work; richer width-tier/user override
  behavior needs product hardening.
- **Diagnostics inputs:** pressure score works, but true `io_cap_saturation_pct`
  and attributable network loss/retransmit inputs are absent.
- **DAMON controls:** underlying APIs and CLI are tested; Textual typed-confirm
  modal flow is still a notice/planning surface.
- **Snapshots:** snapshot bundles are usable; live systemctl/docker refresh at
  snapshot time is not yet wired.
- **Acceptance evidence:** tests are strong, but `MEASUREMENTS.md` still needs
  live perf/RSS, packaging, DAMON, and future BPF measurements.

## Not Implemented

- Privileged daemon/read broker and `groop --attach`.
- Exact BPF per-cgroup network provider and BPF ownership lifecycle.
- Docker/systemd admin actions: update/start/stop/restart/kill.
- `systemctl set-property` governance actions.
- File/log/content browser behind `--inspect-files`.
- Web UI.
- GPU and ZFS optional plugins.
- CIU stack grouping/actions.
- paddr auto-start / persistent daemon-owned paddr mode.
- ZRAM/swap-backend-aware banner, registry metrics, fixtures, and tests.

## Acceptance Status

| Spec §9 item | Current status |
|---|---|
| 1. CPU performance | Not measured over required 5-minute steady-state run. |
| 2. Memory budget | Structural ring budget tested; live RSS measurement still needed. |
| 3. Counter reset handling | Covered by tests. |
| 4. Finding-D raw-write drift | Covered by tests; live destructive acceptance not run. |
| 5. Non-container visibility | Covered by fixtures and UI tests. |
| 6. Graceful degradation | Covered by focused tests; more host matrix evidence would help. |
| 7. Registry semantics | Covered by registry/model tests and branch-policy labels. |
| 8. Diagnostics | Covered by tests; missing richer inputs noted above. |
| 9. Network labels | Covered by provider tests. |
| 10. Record/replay fidelity | Model equality covered; byte-for-byte rendered table acceptance still needed. |
| 11. Packaging | Editable install tested; sdist/wheel/pipx still needed. |
| 12. v2 gating | Mostly out of scope; reserved-action UX still should be explicit. |
| 13. Unprivileged smoke | Basic non-root smoke was run in P7; formal repeat should be recorded. |
| 14. Measurement gates | `MEASUREMENTS.md` created, but BPF/DAMON overhead gates are not recorded. |

## Current Quality Gate

Most recent merged-main validation after P11:

```bash
PYTHONPATH=/tmp/groop-pytest:/home/vb/volkb79-2/vbpub/groop/src python3 -m pytest groop/tests -q
# 79 passed
```

Also validated: Python compile over `src/groop`, `--once --json`, and replay
UI smoke.
