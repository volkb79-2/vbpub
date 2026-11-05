# topos console plan (2026-11-05)

From the deep review and operator interview of 2026-11-05. Target design:
`TUI-SPEC.md`. Decisions: `nyxloom-trove/decisions.md` D-020 onward. Current state:
`docs/STATUS.md`.

No milestone or finish line is defined yet. The operator will set it after using
the PoCs.

## Streams

### Stream 0: correctness (in review, branch `topos-20261105_035951-deep-review-20261105`)

About 60 fixes from the deep review. Gate and adversarial review before merge.

### Stream A: data layer

1. **Rust sampler PoC** (`poc/rust-sampler/`). Measure against lean Python at 1 Hz
   on r1002 and v1001, including the process scan. Rust owns collection if it
   wins by enough to justify the toolchain.
2. Sampler proper: reads, rates, hot/warm/cold tiers, hard events, socket, slow
   enrichment on its own cadence, `topos --once` through it.
3. Read path: `topos query`, TUI backfill and MCP read the tiers. Today persistent
   history is write-only and `topos query` reads recordings only.
4. Unit with the self-protection envelope; install flow, and the wizard question
   for `kernel.task_delayacct`.
5. `mdt-cockpit` mounts `/run/topos`; zero-argument source selection; visible
   fallback; a clear message when a container sees only its own namespace. No daemon
   installs on r1002 or v1001 without coordinating with the agent that controls them
   (D-034). The unit's `RuntimeDirectory=` removes the directory on stop, which would
   strand directory mounts, so the unit must keep it.
6. Build and release: Rust image pinned in the build, a `cmru` step for a static
   musl binary, a run-gate lane. Containers only; no toolchain in the cockpit.

### Stream B: the screen

1. **Line-rendered table replaces `DataTable`**, with priority-based column
   dropping (in progress).
2. **ratatui PoC of the main screen** (`poc/ratatui-tui/`) in parallel, to compare
   by hand and decide the engine.
3. **Drawing PoC** (`tools/poc/terminal_graphics_poc.py`) in VS Code, tmux, PuTTY,
   Ubuntu and CachyOS default terminals, Termix.
4. Meters: per-CPU, stacked memory, zswap, PSI trend, per-device await, io.cost
   vrate, KSM. Needs Stream A's data, or a local reader until then.
5. Process rows: wire the bounded process model into the tree. The code in
   `src/topos/procs/` exists and has no caller; Stream A's sampler replaces its
   reads.
6. The single view (no lenses), planned for 260 columns, in both Textual and
   ratatui, with column order and priority in config, and the side pane.
7. Config in the row and pane: set vs effective using the real kernel algorithm
   (A-08), origin, drift (A-13), inconsistency warnings. Validate on real kernels in
   a VM lane.
8. Freeze and scrub.

### Track C: eBPF block latency (independent)

Cgroup-attributed IO latency from block tracepoints: per-cgroup "response time"
the kernel does not otherwise provide. Optional provider with a measured overhead
gate. BTF is present and `bpftrace` and `biolatency-bpfcc` are installed on the
controller; v1001 has BTF.

### Later

Gated TUI actions (after the open safety items below), remote hosts, TUI-SPEC
parked web UI.

## Open decisions (operator)

| Item | Waits on |
|---|---|
| TUI engine: Textual line widget or ratatui | ratatui PoC vs the swapped Python table |
| Sampler language final go | Sampler PoC numbers |
| Final column list | Using the PoCs |
| Drawing: which techniques | Drawing PoC in real terminals |
| Milestone definition | The above |
| Control submenu design for gated actions (D-033), including whether power and rescue units stay refused | Later; no work until designed |

## Known gaps carried from the review

- **Safety:** squeeze has no owner or protected-service gate and its audit is not
  durable or fail-closed (C-05, C-06); hidden `--allow-non-root-fixture` flag
  (C-16); DAMON start in `paddr.py` writes sysfs before its marker, with no pid cap
  or fixed state dir (C-11).
- **Semantics:** effective protection is a clamp, not the kernel algorithm (A-08);
  drift on user-manager units and `[not set]` weights (A-13); `child_sum` double
  counts network (A-03); paddr start probably monitors nothing (A-11).
- **Persistence, to be replaced by the tiers:** crash loses the active segment and
  reports clean (B-03); health set once at startup (B-08); unreadable segments
  skipped, sticky `evicted` flag (B-09); persistence runs in the sampling thread
  (B-07).
- **Tests and gate:** gate lane and `cmru` release step run different checks, and
  topos still carries its own `tools/coverage_gate.py` instead of assay (D-13); no
  real-kernel lane (D-16, `TP-VMLANE`); `acceptance.py` runs in no lane (D-20).
- **Code shape:** `cli.py` split (D-19), drill-down reads `/proc` on the event loop
  and goes stale (D-07), two sparkline implementations (D-11).
- **Tool finding filed elsewhere:** `ciu worktree create` fails on `DOCKER_GID` in
  the cockpit (known as CIU-160, CIU-161); passing `DOCKER_GID` by hand worked.

## Evidence

Measured on 2026-11-05.

| Measurement | Result |
|---|---|
| Sweep as shipped, r1002, 77 cgroups | 564 ms, 55 spawns |
| Sweep with the docker and unit caches | 51 ms, 1 spawn |
| Read 9 files per cgroup, C | 5 ms, 1.2 MB RSS |
| Read 9 files per cgroup, Python | 8-10 ms, 10 MB RSS (29 MB with topos loaded) |
| Frame CPU, 79 rows at 170x48, Textual `DataTable` | 624 ms, 59 MiB |
| Same, custom Textual line widget, styled cells | 9.5 ms, 39 MiB |
| Same, ratatui 0.29, unstyled cells | 0.39 ms, about 12 MB |
| One recorded frame, 77 cgroups | 417 KB JSON |

The frame benchmarks measure the table only with no terminal writes, so treat them
as ratios. The controller was under load during the Textual runs.

Prior-art sources: <https://github.com/facebookincubator/below>,
<https://www.atoptool.nl/>, <https://github.com/aristocratos/btop>,
<https://github.com/VHSgunzo/zswapmon>. Terminal support:
<https://pypi.org/project/textual-image/>, <https://docs.rs/ratatui-image>,
<https://github.com/BourgeoisBear/rasterm>. PuTTY and Termix graphics support could
not be verified; assume text only.
