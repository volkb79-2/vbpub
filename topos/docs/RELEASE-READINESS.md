# topos Release Readiness

This is the canonical checklist for a v1/v1.5 release claim. `TUI-SPEC.md`
defines acceptance; this document maps those criteria to runnable checks and
remaining gates. `MEASUREMENTS.md` is the evidence ledger: paste dated command
output there rather than copying historical measurements into this file.

Current conclusion: the v1/v1.5 core is a feature-complete prototype with
strong fixture and rootless automation, but it is not production-certified.
Strict spec acceptance still needs the live and packaging evidence marked
`Partial` below.

## Candidate Claim

The current candidate may claim these implemented, fixture-tested surfaces:

| Cut | Claimable surface | Qualification |
|---|---|---|
| v0 | Cgroup v2 collection, registry/source labels, reset-safe rates, `--once --json` | Rootless fixture tests and acceptance smoke exist. |
| v1 | Read-only Textual tree/container UI, diagnostics, record/replay, profiles, snapshots, host/netns network labels, CPU trends, host device/loss summaries | Final performance and live non-root acceptance remain release gates. |
| v1.5 | Passive DAMON observation, explicitly controlled DAMON APIs/UI, incident workflows, compressed-swap awareness, daemon read-client foundations | Controlled DAMON and deployed non-root daemon claims require the applicable live-host templates below. |

The candidate must not be described as production-certified until every
unconditional blocker in this document has dated passing evidence in
`MEASUREMENTS.md`. A narrower release may omit a conditional capability from
its claim instead of running that capability's live gate.

## Explicit Non-Claims

- Exact per-cgroup network loss without a live BPF provider.
- Live BPF attach, pin, snapshot-writer, and detach lifecycle.
- Executable Docker/systemd admin actions beyond the validated P46
  start/stop/restart kernel and P49 memory.high set-property (update, kill,
  raw subprocess).
- Guided `memory.high` squeeze beyond the P56 single-target CLI (no TUI
  integration, no daemon-side/remote squeeze, no automatic two-run stratification
  mode — the two-run pattern is documented as operator guidance only).
- Automated production daemon installation or service mutation.
- Persistent daemon-owned paddr DAMON — enabled by explicit `[damon] paddr_enabled = true`; disabled by default (P44 adds the lifecycle, but the default is unchanged).
- Inspect-files subprocess execution is limited to the bounded journald
  snapshot (fixed absolute ``/usr/bin/journalctl`` argv). No follow/stream
  mode, no arbitrary subprocess.
- Web UI.
- GPU and ZFS plugins.

## Spec Section 9 Evidence Map

`Pass` means the current evidence satisfies the criterion. `Partial` means the
implementation exists but the exact spec acceptance has not been recorded.
`Conditional` means current defaults comply, but changing the default activates
the named measurement gate.

| Item | Criterion | State | Current evidence | Missing evidence |
|---:|---|---|---|---|
| 1 | <5% of one core for 5 minutes at 30 cgroups | Partial | P35 collector loop and P38 one-frame TUI measurements | Five-minute live Textual run on comparable hardware |
| 2 | RSS budget at 40 entities | Partial | P35/P38 bounded RSS measurements | Live Textual RSS with entity count and history settings |
| 3 | Counter reset handling | Pass | Collector/rate tests | None |
| 4 | Raw-write Finding-D drift and reversion | Partial | Drift fixtures/tests | Controlled live raw write plus daemon-reload reversion |
| 5 | Non-container visibility | Pass | Tree/model fixtures and UI tests | None |
| 6 | Graceful degradation matrix | Pass | Collector/provider/DAMON degradation tests | Optional broader host matrix |
| 7 | Registry/branch semantics | Pass | Registry/model/aggregation tests | None |
| 8 | Pressure sorting and finding explanations | Pass | Diagnostics and UI tests | Exact per-cgroup network loss remains a non-claim |
| 9 | Host/netns network labels | Pass | Network provider and UI tests | None |
| 10 | Byte-identical formatted replay cells | Pass | P41 `test_rendered_fidelity.py`: three annotated ticks written by `RecordWriter`, returned by `ReplayDriver.play(step=True)`, and compared through the production formatted-row snapshot at fixed width/profile/sort/filter; JSONL plus conditional compressed JSONL | None |
| 11 | Local pipx install and no-config defaults | Pass | Post-P40 controller evidence in `MEASUREMENTS.md`: isolated build, pipx install, version, and empty-directory replay smoke. P43 changes published dependency from `textual>=0.58,<1` to `textual>=8.2.8`, verified by source metadata, wheel METADATA, packaging-metadata regression tests, and clean resolver installation. | None |
| 12 | v2 action/inspection gating | Partial | Disabled hotkeys; admin preview/audit; P45 gated bounded Docker/cgroup content reads; P46 production-root execution kernel with strict start/stop/restart validation, fixed absolute argv, mandatory fixed-path audit, bounded runner, and injected test fixtures | Remaining v2 actions and inspect-files journal/follow execution are non-claims |
| 13 | Live docker-group non-root smoke | Partial | P33/P35/P38 rootless fixture harnesses | Live non-root tree, Docker JOIN, populated metrics, and disabled mutations |
| 14 | BPF/DAMON default measurement gates | Conditional | BPF and active DAMON remain disabled by default | Run the relevant overhead plan before changing either default |
| 15 | MCP live-daemon acceptance | Pass | P75 `mcp-smoke` leg: rootless live daemon + MCP server over real stdio client, 6 checks (hello, tool discovery, tool calls, response cap, daemon loss, invalid selector), max response size recorded | None |

## Rootless Automated Checks

Run from the repository root. The explicit `PYTHONPATH` ensures the checkout,
not an unrelated installed copy, is tested.

### Tests and compilation

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests -q

mapfile -d '' pyfiles < <(find topos/src/topos topos/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
```

Both commands must exit zero. Record the interpreter/dependency environment
with the result.

### Acceptance smoke (P33)

```bash
PYTHONPATH=topos/src python3 -m topos.acceptance smoke \
  --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json
```

Require exit zero, `ok: true`, at least one entity, a serialization round-trip,
source labels, and at least one replay frame.

### Collector steady harness (P35)

```bash
PYTHONPATH=topos/src python3 -m topos.acceptance steady \
  --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch \
  --samples 5 --interval-s 0 --json
```

Require exit zero, `ok: true`, five completed samples, stable nonzero entity
counts, and CPU/RSS fields. This is collector evidence, not the five-minute TUI
gate.

### TUI smoke harness (P38)

```bash
PYTHONPATH=topos/src python3 -m topos.acceptance tui-smoke \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json
```

Require exit zero, `ok: true`, `frames: 1`, `view: tree`, and `profile: auto`.

### Direct replay UI smoke

```bash
PYTHONPATH=topos/src python3 topos/src/topos/cli.py \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl \
  --step --ui-smoke
```

Require exit zero and `ui smoke ok` output. After installation, the equivalent
operator command is `topos --replay ... --step --ui-smoke`.

### Packaging and pipx

```bash
python3 -m build topos/
pipx install --force ./topos/dist/topos-*.whl
topos --version
topos --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl \
  --step --ui-smoke
```

Record the artifact names and pipx version. Run from a directory without a
topos config file as well, proving documented defaults load without error.
This pipx check is required by spec item 11; a normal venv install is useful
but is not a substitute.

### MCP acceptance (P75)

```bash
PYTHONPATH=topos/src python3 -m topos.acceptance mcp-smoke --json --pretty
```

Require exit zero, `extra_installed: true`, and all six MCP checks passing
(hello, tool_discovery, tool_calls, response_cap, daemon_loss, invalid_selector).
The response cap check records the largest observed response size. When the
`topos[mcp]` extra is absent the leg reports a distinguishable skip (exit 0,
`extra_installed: false`, `checks: []`).

## Live-Host Evidence Templates

Paste completed templates and raw command output into `MEASUREMENTS.md`.

### Five-Minute Textual CPU/RSS

Target: spec items 1 and 2 on hardware comparable to the documented 8-vCPU
host, with at least 30 discovered cgroups and an RSS observation at 40 entities.

```bash
# Terminal 1
topos --record /tmp/topos-live.jsonl

# Terminal 2: select the exact TUI process, then sample CPU and RSS for 5 min.
pid="$(pgrep -n -f '(^|/)(python[^ ]* -m )?topos( |$)')"
test -n "$pid"
pidstat -u -r -p "$pid" 5 60 | tee /tmp/topos-pidstat.txt
ps -o pid,rss,etimes,cmd -p "$pid" | tee /tmp/topos-rss.txt
```

Record:

- Date, host, kernel, CPU count, storage type:
- topos version/commit and exact command:
- Entity count and Docker JOIN count:
- History configuration:
- Five-minute average CPU as percent of one core:
- Peak/ending RSS and entity count at observation:
- Pass/fail against `<5%` CPU and the applicable spec memory budget:

### Controlled DAMON (only if claimed)

Use a deliberate non-production test host. Preserve foreign kdamond slots and
record sysfs state before and after.

- [ ] From entity drill-down, press `d`; verify the vaddr plan and type exact
      `START` confirmation.
- [ ] After at least two aggregation windows, verify vaddr hot/warm/cold data.
- [ ] From host-memory, press `p`; verify the paddr plan and type `START`, or
      run `sudo topos damon paddr start --confirm START`.
- [ ] Verify paddr heat/status appears.
- [ ] Run `sudo topos damon stop --all-mine`.
- [ ] Prove only topos-owned sessions stopped and foreign slots were unchanged.
- [ ] Complete the DAMON overhead plan in `MEASUREMENTS.md` before raising or
      enabling defaults.

### Deployed Non-Root Daemon (only if claimed)

After an operator deliberately applies the packaged templates/install plan:

- [ ] `topos daemon status` exits zero and reports deployment/protocol OK.
- [ ] `topos daemon status --json` is parseable.
- [ ] `topos daemon current --pretty-json` returns a valid frame.
- [ ] `topos --attach --once --json` returns a frame as the non-root user.
- [ ] `topos --attach` opens the TUI as the non-root user.
- [ ] Socket ownership/mode matches the documented group-readable policy.
- [ ] P52 envelope `hello` negotiates protocol version and capabilities.
- [ ] P52 envelope `current`/`history`/`entity`/`health` return typed
      responses with sensitivity metadata; peer credentials are observed.

### Live Non-Root Acceptance

Run as a docker-group user with no sudo and `permission_mode = "auto"`:

- [ ] Startup shows no password prompt or crash.
- [ ] Full cgroup tree and memory/CPU/IO/PSI values are populated.
- [ ] Running Docker containers are JOINed.
- [ ] DAMON and every mutating/v2 action are hidden or disabled with the
      documented root/admin guidance.

## Release Blockers

Before tagging a production-certified v1/v1.5 release, require dated passing
evidence in `MEASUREMENTS.md` for:

- [ ] Full suite and full-source `py_compile` from the candidate commit.
- [ ] P33 smoke, P35 steady, P38 TUI smoke, and direct replay UI smoke.
- [ ] Five-minute live TUI CPU and RSS budgets (spec items 1-2).
- [ ] Controlled live Finding-D raw-write/reversion (item 4).
- [ ] Rendered record/replay cell fidelity (item 10).
- [ ] Live docker-group non-root acceptance (item 13).
- [ ] Live DAMON and daemon evidence only for capabilities included in the
      release claim.
- [ ] BPF or DAMON overhead gates before changing their disabled-by-default
      posture.

Any missing unconditional item blocks a production-certified claim. It does
not block publishing an explicitly labeled prototype/pre-release whose release
notes repeat the unresolved gates and non-claims above.

## History

| Date | Change |
|---|---|
| 2026-07-10 | P39 created the canonical readiness map and live evidence templates. |
| 2026-07-10 | P45 adds bounded inspect-files content reads: `topos inspect-files read` with confined no-follow opens, bounded bytes/lines, safe decoding, deterministic JSON/text output, and structural safety tests. |
