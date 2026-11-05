# topos — host pressure inspector and cgroup forensics TUI

Implementation home for the tool specified in `TUI-SPEC.md` (the **spec**, the
target design). topos is a read-only sysadmin console: one enriched screen showing
per-CPU, memory pressure, the full cgroup tree with its configured limits, consumers
and blocked processes, with honest source and degradation labels. Admin actions are
gated and stay in the CLI for now. The web UI is parked (D-023).

Today it is a usable cgroup-tree TUI plus a CLI over recordings, an optional
root daemon with a Unix socket, MCP and HTTP read frontends, and gated Docker and
systemd actions. `docs/STATUS.md` says what works and what is not connected; the
plan to reach the spec is `docs/PLAN-2026-11-CONSOLE.md`.

Read `CONTRACTS.md` before writing code; it defines the interfaces. Its `§`
references to the old spec point to `nyxloom-trove/archive/TUI-SPEC-2026-07.md`.
Stack: Python; Textual allowed only under `src/topos/ui/`. A native sampler owning
collection is planned pending a proof of concept (D-026).

## Canonical documents

- `TUI-SPEC.md` — target design (2026-11). The 2026-07 spec is
  `nyxloom-trove/archive/TUI-SPEC-2026-07.md`.
- `CONTRACTS.md` — frozen developer contracts for model, registry, providers,
  config, recording, and degradation behavior.
- `docs/STATUS.md` — current implementation state versus the spec.
- `docs/PLAN-2026-11-CONSOLE.md` — plan from the current state to the spec.
- `docs/ROADMAP.md` — pointer to the roadmap; the live one is
  `nyxloom-trove/3-roadmap.md` (backlog: `nyxloom-trove/4-backlog.md`).
- `nyxloom-trove/decisions.md` — product and architecture decisions (D-001 on);
  implementation agents must not silently reinterpret them.
- `nyxloom-trove/1-north-star.md`, `nyxloom-trove/2-product-definition.md` —
  intent and product definition.
- `nyxloom-trove/archive/BRANCH-DISPOSITION-2026-07.md` — reviewed historical
  branches and merge/reject decisions.
- `docs/OPERATOR-QUESTIONS.md` — real investigation coverage and the named
  scenario acceptance model behind the aspirational "95%" target.
- `docs/ARCHITECTURE.md` — current dataflow and module map.
- `docs/LIFECYCLE-ADAPTERS.md` — owner-chain safety contract and future
  systemd/Compose/CIU/Wings/Podman/Kubernetes integration posture.
- `docs/OPERATIONS.md` — runbook for using topos safely today.
- `docs/COMPRESSED-SWAP.md` — zswap/zram/disk/mixed backend policy and metric
  semantics.
- `docs/BPF-NETWORK-ACCOUNTING.md` — v2 exact network accounting design and
  measurement-gate constraints.
- `MEASUREMENTS.md` — acceptance and overhead evidence ledger. BPF defaults,
  DAMON default increases, and release claims should be blocked on this file.
- `docs/RELEASE-READINESS.md` — release-candidate readiness surface: gates,
  evidence sources, live-host templates, and explicit non-claims.
- `docs/DAEMON.md`, `docs/INSPECT-FILES.md`, `docs/WEB-UI-SCOPING.md` (parked),
  `docs/BACKLOG.md` (older narrative ledger).
- `pwmcp-instance/README.md` — pinned browser-test service, CIU lifecycle and
  network/security constraints for future web acceptance.
- `nyxloom-trove/` — handoffs, reports and decisions; finished ones are in
  `nyxloom-trove/archive/` and `nyxloom-trove/reports/`.

## Quickstart

```bash
pip install -e topos/
pip install -e './topos[mcp]'
claude mcp add topos -- topos mcp serve
topos --once --json
topos --version
topos
topos --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step
topos snapshot inspect /path/to/topos-incident-*.tar
topos report topos/tests/fixtures/frames/gstammtisch-once.jsonl
topos report topos/tests/fixtures/frames/gstammtisch-once.jsonl --json --window last:60s --group-by slice
topos report recording.jsonl --json --window auto
topos query recording.jsonl --shape summary --metric ram --metric psi_mem_some_avg10 --sort ram:p95:desc
topos query recording.jsonl --shape raw --metric io_r_bps --window last:60s --json
topos squeeze --target /sys/fs/cgroup/system.slice/app.service --admin --confirm SQUEEZE
topos action preview --kind docker-kill --target my-container --admin --signal TERM --json
topos action preview --kind docker-update --target my-container --admin --memory 512M --json
```

## Distribution and daemon deployment

Topos is distributed as a Python wheel.  The daemon is deliberately a root
systemd service, not a container image: it observes the host cgroup hierarchy,
Docker metadata, optional BPF state, and optional DAMON sysfs control.  Putting
those powers in a privileged container would add an image boundary without
meaningfully reducing host authority.  The installed `topos.service`,
`topos.tmpfiles`, and `topos.slice` templates instead create a group-readable
Unix socket for clients and bound the daemon to 256 MiB `MemoryHigh`, 512 MiB
`MemoryMax`, 1 GiB swap, 512 tasks, and low CPU weight.

Without a daemon, the client still works in direct mode: `topos --once`, the
live TUI, record/replay, report, query, and rootless readable metrics collect
locally. `--attach`, `topos daemon current/status/health`, and the MCP frontend
need the daemon socket and fail with a typed daemon-unavailable error rather
than silently switching data sources. Privileged DAMON/actions remain gated by
root/admin confirmation in either mode.

Use `--config PATH` to point at an alternate TOML config, `--profile NAME` to
override the active UI column profile for one run, and `--record FILE` to record
the live TUI stream to JSONL while you inspect it. For unattended (headless)
recording without the TUI, use `--record FILE --headless [--interval N]
[--duration S | --frames K]`. Headless mode drives the existing collector loop
without importing `textual`, so it works even when the UI dependencies are not
installed.

Use `--entities GLOB` (repeatable) to collect only entities whose `EntityKey`
matches a glob pattern, `--slice NAME` to include an entity subtree, and
`--container NAME_OR_PREFIX` (repeatable) to include entities matching a docker
container name or prefix. Use `--metrics compact` to keep only the memory-gauge,
PSI, and refault-rate metric families. Use `--metrics FIELD_OR_FAMILY,...` to
select an explicit comma-separated subset of individual metric names and/or
metric families (registry-validated). These flags apply to `--once`, the live
TUI, and `--record` (both TUI-driven and headless P53). They are rejected with
`--replay` and `--attach`.

Use `--container NAME_OR_PREFIX` on `topos inspect-files plan/read --target` or
`topos action preview/execute --target` to resolve a Docker container name or
prefix to its cgroup path automatically instead of manually specifying
`--target`.

For AI CLI access to a running local daemon, install `topos[mcp]` and register
the stdio frontend with `claude mcp add topos -- topos mcp serve`. The four
read-only tools have stated item limits and an enforced 4 MiB aggregate result
cap; use `topos mcp serve --redact-above operational` when sensitive metric
values should be replaced by typed redaction markers.

Use `topos report FILE` to compute a steady-state profile from a P2-format
recording (JSONL or JSONL.zst). Per-entity p50/p95/max for key memory/PSI
gauges are computed, with derived rates from embedded raw counters when the
recorded live rate is ``None``. Use ``--window last:Ns`` to restrict to the
last N seconds of a recording, or ``--window auto`` to choose the longest
stable trailing window (the busiest entity's primary gauge has population
CoV <= 0.05; ``ram`` and three frames by default). ``--group-by slice`` rolls
entities up under their owning ``*.slice`` ancestor. This is the steady-state
profile input for the gstammtisch stack measurement program
(``scripts/gstammtisch-guide/plan-stack-resource-tuning.md`` PKG-3).

By default `topos report` and `topos query` print a deterministic, pipe-safe
ASCII table (P65) — no color, no paging, fixed column widths, safe to diff or
pipe. Pass `--json` for the machine-readable contract instead; `--json` and
the explicit `--table` form are mutually exclusive (requesting both exits 2).
The table renderer only ever formats the same already-computed figures the
JSON would show — it never recomputes, rounds a raw float, or reads a file
itself. Six typed value states get distinct, unambiguous ASCII spellings
(``missing``, ``redacted``, ``warming``, ``stale``, ``permission-denied``,
``truncated``); a real `0` always renders as `0`, never blank or a dash. A
`--projection hierarchy` query indents children under their parent in
ancestry/sibling order; `--projection flat` (the default) is always labelled
`flat` in the table header, since it is a global rank, not a tree.

Use ``--assert GROUP:METRIC:STAT<=VALUE`` (or ``>=``) repeatably to add
threshold gating: exit code 1 when a bound is breached (genuine gate
failure), exit code 2 on malformed specs. Absent groups/metrics and null
STATs are breaches. Assertion outcomes appear under a top-level
``"assertions"`` key in the JSON output.

::

    # Assert that root entity RAM max <= 4 GB
    topos report recording.jsonl --json --assert ':ram:max<=4294967296'
    echo $?   # 0 when satisfied, 1 when breached

    # Multiple asserts ANDed together
    topos report recording.jsonl --json \
      --assert 'system.slice:ram:max<=8589934592' \
      --assert 'system.slice:psi_mem_some_avg10:p95<=5.0'

Useful feature hotkeys in the TUI:

- `F5` / `t` cycles tree -> container -> ciu-grouped view. The ciu-grouped view
  groups containers by ciu stack and phase (numeric: `phase_2` before
  `phase_10`); entities whose stack membership was *inferred* rather than
  label-confirmed are marked `(inferred)`, and a group holding both tiers
  renders as `(mixed)`.
- `p` cycles column profiles.
- `F6` / `s` cycles sort.
- `/` filters rows.
- `Enter` opens entity drill-down.
- `x` writes an incident snapshot for the selected row.
- `m` opens host-memory / paddr DAMON status.
- `F1` / `?` opens generated registry help.

## Gate environment

Parts of the suite are only *reachable* when an optional extra is installed:
the ``zstandard`` decompression oracles, and the 16 ``mcp`` frontend tests that
sit behind a module-level ``importorskip``. Run the gate from the declared
``[dev]`` extra, which pins every extra the suite needs:

```bash
# From the repo root:
pip install -e 'topos[dev]'
```

That installs ``topos``, its runtime dependency ``textual``, the ``zstandard``
and ``mcp`` extras, and ``pytest``.

If a required extra is missing, a session-level gate **fails the run** (exit 1)
and names every test that skipped:

```
!!  GATE FAILED: missing test extra(s): zstandard
!!  6 test(s) skipped -- this run is NOT a gate.
!!  Install with: pip install -e 'topos[dev]'
!!  SKIPPED: tests/test_report.py::... -- zstandard not installed
```

This is why the gate exists: a skip reads as a pass at a glance. P79 shipped a
real bug because it was validated in a venv without ``zstandard``, so every
oracle that would have caught it skipped and the suite was green. The gate is
keyed on the **extras**, not on test names — a name-matching gate only covers
the tests someone remembered to name.

Neither extra becomes a hard runtime dependency: ``pip install topos`` without
extras still imports and runs ``topos report`` on a plain ``.jsonl`` file.

## Work process

Work packages, handoff authoring and review follow nyxloom doctrine in
`nyxloom/reference/` (at the vbpub root). The 2026-07 package tables and workflow
text are kept as history in `nyxloom-trove/archive/README-packages-2026-07.md`.

## Reference deployment

gstammtisch (Debian 13, cgroup v2, zswap, Pterodactyl/Wings game server).
Degradation on other hosts must be graceful; there is no distro matrix yet.

## Testing

`run-gate` is the canonical test entrypoint — `run-gate --list`
discovers the declared lanes; definitions live in `run-gate.toml`.
See [`../run-gate-project/CONSUMERS.md`](../run-gate-project/CONSUMERS.md).

### CLI diagnostics

The `topos` parser families, including operational subcommands and the
acceptance harness, prefix help, usage, missing-argument, unknown-argument, and
configuration diagnostics at every depth with `TOPOS <version> — host telemetry
and resource control` as line 1. The top-level `topos --version` command prints
exactly one `topos <version>` line on stdout, exits 0, and writes nothing to
stderr. Normal command output is unchanged.
The documented `python -m topos.acceptance` entrypoint is also versioned: its
top-level `--version` prints the same one-line `topos <version>` identity and
its nested parser diagnostics use the same TOPOS headline.
