# groop Release Readiness Ledger

This document is the canonical release-candidate readiness surface for v1/v1.5.
It maps `TUI-SPEC.md` §9 acceptance gates to evidence sources, lists exact
commands for rootless automated checks, provides a live-host evidence template
for missing manual measurements, and documents explicit non-claims.

**Evidence ledger:** `MEASUREMENTS.md` holds historical acceptance measurements
(CPU/RSS, test counts, fixture outputs) and templates for future measurements.
This document references that ledger rather than duplicating it.

**Status as of P39:** the v1/v1.5 codebase is a **feature-complete prototype**
with automated rootless evidence for every §9 gate except live 5-minute
Textual TUI CPU/RSS and live-root DAMON acceptance. The next step before a
release tag is manual live-host evidence capture using the templates below.

---

## Release-Cut Scope

### What can be claimed for v1 / v1.5

| Capability | Cut | Evidence |
|---|---|---|
| Cgroup v2 collector with reset-safe rates | v0 | Tests + `--once --json` |
| Metric registry with source labels | v0 | Tests + fixture JSON |
| Record/replay with headered JSONL | v1 | Tests + fixture round-trips |
| Network providers (host truth + netns) | v1 | Tests + `MEASUREMENTS.md` |
| Origin/drift detection | v1 | Tests + fixture raw-write simulation |
| Textual TUI with tree/container views | v1 | Fixture replay smoke |
| Diagnostics engine with pressure scores | v1 | Tests |
| Incident snapshots | v1.5 | Tests + CLI inspect |
| DAMON passive vaddr/paddr | v1.5 | Tests + fixture safety tests |
| ZRAM/swap-backend awareness | v1.5 | Tests + fixture rounds |
| Rootless acceptance smoke/steady/TUI harnesses | v1/v1.5 | P33, P35, P38 acceptance commands |
| Rootless CPU sparkline surface | v1 | P36 fixture tests |
| Rootless host network loss diagnostics | v1 | P37 fixture tests |
| Daemon read broker (spike) | v1.5/v2 | Tests + socket protocol |
| Daemon attach mode, default-socket, status | v1.5/v2 | Tests |
| BPF measurement gate + design | v2 | P17 fixture |
| BPF provider read side (userspace) | v2 | P18 fixture tests |
| Admin action gating skeleton | v2 | P21 fixture tests |
| Daemon deployment preflight + install plan | v1.5/v2 | Tests + P22/P25 |
| Swap/refault terminology aliases | v1.5 | P27 tests |
| I/O cap saturation metric | v1 | P28 tests |
| Inspect-files safety skeleton | v2 | P29 tests |
| Daemon client error guidance | v1.5/v2 | P31 tests |
| Daemon status command | v1.5/v2 | P32 tests |
| Host device banner | v1 | P34 tests |
| Snapshot progress UI | v1.5 | P26 tests |
| Replay timestamp jump | v1 | P24 tests |

### What is explicitly outside the release claim

- **Exact per-cgroup network loss attribution** without BPF (requires v2 BPF
  provider: P18/P17).
- **Live BPF lifecycle** (attach, pin, detach, kernel compilation) — the BPF
  gate and userspace read side exist as fixtures only.
- **Executable admin actions** — the preview/audit skeleton exists (P21),
  but no Docker/systemd commands are executed.
- **Web UI** — not implemented; deferred per spec.
- **GPU and ZFS plugins** — not implemented; deferred per spec.
- **Production daemon installation execution** — templates and install plans
  exist but are not executed automatically.
- **Persistent paddr DAMON** — daemon-owned paddr mode is not implemented.

---

## Spec §9 Acceptance Map

| §9 Item | Area | Evidence Source | Passing? | Remaining |
|---|---|---|---|---|
| 1. CPU performance (<5% one core, 5 min) | Performance | `MEASUREMENTS.md` (P12/P35 bounded evidence) | Partial | **5-minute live Textual TUI run needed** |
| 2. Memory budget (<~60 MB at 40 entities) | Performance | `MEASUREMENTS.md` (P12/P35/P38 RSS evidence) | Partial | **Live Textual TUI RSS measurement needed** |
| 3. Counter reset handling | Correctness | `groop/tests/test_collector.py` | Yes | — |
| 4. Finding-D raw-write drift | Correctness | `groop/tests/test_drift.py` | Yes | Live destructive acceptance not run |
| 5. Non-container visibility | Correctness | `groop/tests/test_ui_tree.py`, fixtures | Yes | — |
| 6. Graceful degradation | Stability | `groop/tests/test_collector.py`, focused tests | Yes | — |
| 7. Registry semantics | Correctness | `groop/tests/test_model.py`, `test_registry.py` | Yes | — |
| 8. Diagnostics | Correctness | `groop/tests/test_diag.py` | Yes | Per-cgroup loss attribution is v2 |
| 9. Network labels | Correctness | `groop/tests/test_network_providers.py` | Yes | — |
| 10. Record/replay fidelity | Correctness | Model equality tests + fixture round-trips | Partial | Byte-for-byte rendered table acceptance not run |
| 11. Packaging | Operations | `MEASUREMENTS.md` (P12 sdist/wheel) | Yes | pipx-specific install optional |
| 12. v2 gating | Safety | `groop/tests/test_admin_actions.py`, P13 TUI tests | Yes | — |
| 13. Unprivileged smoke | Operations | `python -m groop.acceptance smoke` / `steady` / `tui-smoke` | Yes | Fresh live-host results → `MEASUREMENTS.md` |
| 14. Measurement gates | Governance | `MEASUREMENTS.md` (P17 BPF gate) | Yes | DAMON overhead gate not recorded |

---

## Rootless Automated Check Commands

These commands produce deterministic evidence without root privileges. Run them
from the repository root (or with `PYTHONPATH=groop/src`) in the same Python
environment where groop is installed or importable.

### Full test suite

```bash
python3 -m pytest groop/tests -q --tb=short
```

Expected: all tests pass. (Current baseline after P38: `382 passed in 41.48s`.)

### Python compile check

```bash
python3 -m py_compile groop/src/groop/acceptance.py
python3 -m py_compile groop/tests/test_acceptance.py
```

Expected: exit code 0, no output. If no Python files were changed by the
current package, this can be skipped.

### Acceptance smoke harness (P33)

```bash
# Fixture-based (deterministic, no /sys/fs/cgroup dependency):
python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json
```

Expected: exit 0, `"ok": true`, entity count ≥ 1, replay frame count ≥ 1.

### Acceptance steady harness (P35)

```bash
# Fixture-based, 2 samples, zero interval (fast):
python3 -m groop.acceptance steady \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --samples 2 --interval-s 0 --json
```

Expected: exit 0, `"ok": true`, `samples_completed` >= 2, CPU and RSS
measurements present.

### TUI smoke evidence (P38)

```bash
# Fixture replay (deterministic, standard fixture):
python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json

# Custom profile smoke:
python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --profile minimal --json
```

Expected: exit 0, `"ok": true`, `smoke_line` shows `frames=N view=tree
profile=auto` (or whatever `--profile` was given).

### Replay UI smoke (manual TUI check)

```bash
groop --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --step --ui-smoke
```

Expected: exits silently after rendering one frame via the Textual smoke path.

### Packaging smoke

```bash
python3 -m build groop/
python3 -m pip install ./groop/dist/groop-*.whl --force-reinstall
groop --version
```

Expected: wheel/sdist build, clean install, `groop --version` returns
`groop 0.1.0` (or current version).

---

## Live-Host Evidence Template

The following measurements require a real host with live cgroups, `/proc`,
and optionally root (for DAMON). Paste results into `MEASUREMENTS.md` before
a release tag.

### 1. 5-Minute Textual TUI CPU/RSS (spec §9 items 1–2)

**Purpose:** Confirm the running TUI stays under `<5%` of one CPU core and
`<~60 MB` RSS at ~40 entities with default history settings.

**Setup:**

1. Ensure groop is installed or on `PYTHONPATH`.
2. Open the TUI in one terminal:
   ```bash
   groop --record /tmp/groop-live.jsonl
   ```
3. Keep it running for 5 minutes with real cgroup data.

**Measurements:**

```bash
# CPU (5-minute window, 5-second samples):
pidstat -p "$(pgrep -f 'groop$')" 5 60
# or with child threads:
pidstat -p "$(pgrep -f 'groop$')" -d 5 60

# RSS snapshot:
ps -o pid,rss,cmd -p "$(pgrep -f 'groop$')"

# Total wall time and frame count from recording:
wc -l /tmp/groop-live.jsonl
```

**Record in `MEASUREMENTS.md`:**

- Host description:
- Kernel version:
- CPU count:
- Entity count (from banner or `--once --json`):
- groop version:
- History settings (full_resolution_seconds, entity_grace_seconds):
- Pass/fail against `<5%` CPU:
- Pass/fail against `<~60 MB` RSS:

### 2. Live DAMON Vaddr/Paddr Acceptance (spec §9 if claiming controlled DAMON)

**Purpose:** Verify controlled DAMON sessions start, produce hot/warm/cold
columns in the TUI, and stop cleanly without leaving foreign kdamond slots
modified.

See `MEASUREMENTS.md` "DAMON Gate" section for the full measurement plan.
Run on a deliberate test host (not a production game server).

**Minimal checklist:**

- [ ] `sudo groop damon vaddr start --pid <PID> --confirm START` succeeds.
- [ ] TUI drill-down for the target entity shows hot/warm/cold columns after
      two aggregation windows (~4s with defaults).
- [ ] `sudo groop damon stop --all-mine` stops only groop-owned sessions.
- [ ] Manual `damo status` shows foreign kdamond slots untouched.
- [ ] TUI DAMON start modal shows planned sysfs writes; exact `START` required.

### 3. Daemon Status (spec §9 if claiming non-root daemon mode)

**Purpose:** Verify that `groop daemon status` and `groop daemon current`
work against a deployed root daemon.

**Setup:** Deploy the daemon using `groop daemon install-plan` guidance,
then run the packaged systemd/tmpfiles templates.

**Checklist:**

- [ ] `groop daemon status` exit 0, reports deployment + protocol OK.
- [ ] `groop daemon status --json` produces parseable JSON.
- [ ] `groop daemon current --pretty-json` returns a valid frame.
- [ ] `groop --attach` launches the TUI consuming daemon frames.
- [ ] `groop --attach --once --json` returns a frame without opening the TUI.
- [ ] Non-root user can run all the above (group-readable socket).

---

## Release Blocker Checklist

Before tagging a release, the following evidence must be present in
`MEASUREMENTS.md`. If any item is missing or fails, the release is blocked.

- [ ] **Full test suite:** `python3 -m pytest groop/tests -q` exit 0.
- [ ] **Python compile:** `py_compile` clean on all touched Python files.
- [ ] **Acceptance smoke (P33):** fixture-based `python -m groop.acceptance smoke`
      exit 0 with measurements recorded.
- [ ] **Acceptance steady (P35):** fixture-based `python -m groop.acceptance steady`
      exit 0 with CPU/RSS samples recorded.
- [ ] **TUI smoke (P38):** fixture-based `python -m groop.acceptance tui-smoke`
      exit 0 with child CPU/RSS recorded.
- [ ] **Packaging:** sdist/wheel build, fresh install, `groop --version`.
- [ ] **5-minute live TUI CPU/RSS:** `<5%` CPU, `<~60 MB` RSS at ~40 entities
      (paste results into `MEASUREMENTS.md`).
- [ ] **Live DAMON acceptance:** if claiming controlled DAMON, run the full
      DAMON measurement plan (paste results into `MEASUREMENTS.md`).
- [ ] **BPF gate:** if BPF is enabled by default, the seven-step BPF overhead
      gate must be recorded in `MEASUREMENTS.md`. Currently BPF is **disabled
      by default** — this gate is only required when BPF is turned on.
- [ ] **DAMON gate:** DAMON is **not enabled by default** — raised defaults
      require this gate. Currently DAMON starts are explicit (CLI `--confirm`
      or TUI typed confirmation).

---

## Non-Claims Detail

| Non-Claim | Why | Covered By |
|---|---|---|
| Exact per-cgroup network loss | Requires kernel BPF `cgroup_skb` with daemon-owned lifecycle | P17 (gate), P18 (read side), v2 roadmap |
| Live BPF lifecycle | No `bpftool`, no root, no BPF C source in repo | P17 (blocker documented) |
| Executable admin actions | Preview/audit skeleton exists, no execution | P21 (skeleton) |
| Web UI | Spec defers to post-v2 | Not implemented |
| GPU plugins | Optional, no provider interface for GPU | Not implemented |
| ZFS plugins | Optional, no provider interface for ZFS | Not implemented |
| Production daemon install | Templates and install plan exist; no automated execution | P22/P25 (plan only) |
| Persistent paddr DAMON | Daemon-owned mode not implemented | P11 (CLI-only start) |

---

## Document History

| Date | Change |
|---|---|
| 2026-07-10 | Initial P39 release readiness ledger created. Maps §9 gates, documents automated commands, provides live-host templates, records non-claims. |
