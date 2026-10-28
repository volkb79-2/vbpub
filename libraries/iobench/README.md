# iobench

A cache-proof, time-resolved disk benchmark for vbpub hosts. It measures what a disk (especially a hoster's VM disk) *sustains*, not what its caches
absorb, and turns the measurements into **proposals with the rule that produced each and a proof status** (`proven`, `unproven` with reasons, or `absent`): the io.cost model and QoS,
the writeback and queue settings (`dirty_bytes`, `dirty_background_bytes`, `nr_requests`, `max_sectors_kb`), and the dev IO caps.

The measurement core is Python 3 standard library plus the `fio` binary. The command line adopts `cli-extended` (so the CLI needs that library on `PYTHONPATH`; the core does not).
See [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) for why it works this way, [docs/CONSUMERS.md](docs/CONSUMERS.md) for how to use it, and [docs/SPEC.md](docs/SPEC.md) for the normative
requirements and the result schema.

## What it does

| Capability | One line | Spec |
|---|---|---|
| Calibration (once per host or storage class; `quick` 12, `standard` 60, `thorough` 75 min) | cold-region probe (primary, direct) cross-checked by a latency-mode split, working-set ladder, time-axis proof for writes, fresh vs rewritten blocks, ceiling, cost-shape grid, latency-vs-load curve | `IOB-CAL`, `IOB-CCH`, `IOB-SHP`, `IOB-LAT` |
| Matrix iteration (6 cells: typical 11, worst case 15 min; one knob A/B typical 4 min) | reuses the calibration; interference cells with a sync-latency probe under buffered writeback load, the read cost of small requests, a noise floor | `IOB-MAT`, `IOB-SWP`, `IOB-RDC` |
| Proof model | every proposed value is `proven`, `unproven` (with reasons) or `absent`; `check` exit 0 only if every requested proposal is proven, exit 7 if valid but some are not | `IOB-STA`, `IOB-CHK` |
| Plausibility | rejects numbers that are a cache, a burst, a flat provider cap, a moved ceiling or a too-short run; never keys on the `rotational` flag | `IOB-PLA`, `IOB-REF` |
| Proposals | `proposals.writeback`, `proposals.caps`, `proposals.iocost_model`, `proposals.iocost_qos`, each value with `rule`, `inputs`, `status` | `IOB-DER` |
| Ceiling history | an append-only list of sustained plateaus from which the vrate bounds derive | `IOB-CEI` |
| Safety | targets are a dedicated device **or a preallocated file** on an existing filesystem; refuses a target that is mounted, swap, signature-bearing or the root disk, writes nothing without its marker; the Host itself binds every writer and discard to the authorised extent (a plain `run`/`spawn` can only create `iobench-*` load files, never reach a target); never touches any cgroup `io.max`; restores every changed runtime knob on every exit; never creates or mounts a filesystem | `IOB-TGT`, `IOB-SAF`, `IOB-ARC-05` |

## Quick use

```bash
# prepare a dedicated scratch partition once (guards: --i-own-this-device plus matching size and PARTUUID)
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007

# calibrate once per host / storage class (a live host needs the three guard flags)
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40

# ask whether the result is usable, and read the proposals
iobench check --json; echo "exit=$?"
iobench show --proposals --json
```

## Layout

```
libraries/iobench/
  README.md  docs/{DESIGN-GUIDE,CONSUMERS,SPEC,CLI-SPEC}.md  docs/{cli-surface.json,cli-review.toml,cli-review-findings.toml}
  pyproject.toml  cmru.toml  run-gate.toml  assay.toml  .coveragerc
  src/iobench/   pure core (planning, reduction, derivation, schema) behind an injected Host; cli.py is the thin shell
  tests/         fake-fio and fake-sysfs scenarios, recorded-evidence replays, controlled-wrong plausibility cases
```

## Status

Implemented (IOB-03 P3, reviewed through wave 2026-10-27): the measurement core, safety, proposals, the `iobench` CLI over `cli-extended`, and the replay fixtures, all tested on a fake host (`FakeHost` / `FakeFio`). Not yet done: the live
acceptance run on the designated test host (IOB-ACC-01, IOB-03 P4; `RealHost` has never run against a real device, fio or systemd) and the first cmru release (IOB-REL-01; wheel plus sha256). The CLI review cases in `docs/cli-review.toml` record real invocations and
the outcomes observed on the fake host; an independent semantic review still has to confirm their rationales. The decisions behind it are IOB-D-001..014 and the rulings
R-1..R-10 in the DESIGN-GUIDE. Nothing here is wired into `mdt-cockpit` or `debian-install-v2` yet (migration phases M1-M4 in the DESIGN-GUIDE).
