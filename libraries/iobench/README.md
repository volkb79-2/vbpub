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
| Calibration (once per host or storage class; `quick` 12, `standard` 60, `thorough` 75 min) | working-set ladder to the cache cliff, two independent cache estimators, fresh vs rewritten blocks, ceiling, cost-shape grid, latency-vs-load curve | `IOB-CAL`, `IOB-CCH`, `IOB-SHP`, `IOB-LAT` |
| Matrix iteration (6 cells: typical 11, worst case 15 min; one knob A/B typical 4 min) | reuses the calibration; interference cells with a sync-latency probe under buffered writeback load, the read cost of small requests, a noise floor | `IOB-MAT`, `IOB-SWP`, `IOB-RDC` |
| Proof model | every proposed value is `proven`, `unproven` (with reasons) or `absent`; `check` exit 0 only if every requested proposal is proven, exit 7 if valid but some are not | `IOB-STA`, `IOB-CHK` |
| Plausibility | rejects numbers that are a cache, a burst, a flat provider cap, a moved ceiling or a too-short run; never keys on the `rotational` flag | `IOB-PLA`, `IOB-REF` |
| Proposals | `proposals.writeback`, `proposals.caps`, `proposals.iocost_model`, `proposals.iocost_qos`, each value with `rule`, `inputs`, `status` | `IOB-DER` |
| Ceiling history | an append-only list of sustained plateaus from which the vrate bounds derive | `IOB-CEI` |
| Safety | refuses a target that is mounted, swap, signature-bearing or the root disk, writes nothing without its marker; never touches any cgroup `io.max`; restores every changed runtime knob on every exit; never creates or mounts a filesystem | `IOB-TGT`, `IOB-SAF` |

## Quick use

```bash
# prepare a dedicated scratch partition once (guards: --i-own-this-device plus matching size and PARTUUID)
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 7b2d0c1e-07

# calibrate once per host / storage class (a live host needs the three guard flags)
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40

# ask whether the result is usable, and read the proposals
iobench check --json; echo "exit=$?"
iobench show --proposals --json
```

## Layout

```
libraries/iobench/
  README.md  docs/{DESIGN-GUIDE,CONSUMERS,SPEC}.md  run-gate.toml  assay.toml
  src/iobench/   pure core (planning, reduction, derivation, schema) behind an injected Host; cli.py is the thin shell
  tests/         fake-fio and fake-sysfs scenarios, recorded-evidence replays, controlled-wrong plausibility cases
```

## Status

Specification phase (IOB-03 P1/P2, revision 2 after review). The decisions behind it are IOB-D-001..014 and the rulings R-1..R-10 in the DESIGN-GUIDE. Nothing here is wired into
`mdt-cockpit` or `debian-install-v2` yet (migration phases M1-M4 in the DESIGN-GUIDE).
