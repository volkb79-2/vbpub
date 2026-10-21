# iobench

A cache-proof, time-resolved disk benchmark for vbpub hosts. It measures what a disk (especially a hoster's VM disk) *sustains*, not what its caches
absorb, and turns the measurements into **proposals with the rule that produced each**: the io.cost model and QoS, the writeback and queue
settings (`dirty_bytes`, `dirty_background_bytes`, `nr_requests`, `max_sectors_kb`), and the dev IO caps.

Python 3 standard library plus the `fio` binary. No other dependency in the measurement core. See [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) for why it works
this way, [docs/CONSUMERS.md](docs/CONSUMERS.md) for how to use it, and [docs/SPEC.md](docs/SPEC.md) for the normative requirements and the result schema.

## What it does

| Capability | One line | Spec |
|---|---|---|
| Calibration (once per host or storage class, 30-75 min) | working-set ladder to the cache cliff, steady tail, fresh vs rewritten blocks, cache model fit, ceiling, cost-shape grid, latency-vs-load curve | `IOB-CAL`, `IOB-SHP`, `IOB-LAT` |
| Matrix iteration (10-15 min) | reuses the calibration; short interference cells with a sync-latency probe under buffered writeback load, the read cost of small requests, a noise floor | `IOB-MAT`, `IOB-SWP`, `IOB-RDC` |
| Plausibility | refuses numbers that are a cache, a burst, a flat provider cap, or a too-short run; never keys on the `rotational` flag | `IOB-PLA` |
| Proposals | `proposals.writeback`, `proposals.caps`, `proposals.iocost_model`, `proposals.iocost_qos`, each value with `rule` and `inputs` | `IOB-DER` |
| Ceiling history | an append-only list of sustained plateaus (from the benchmark, a governor or a timer probe) from which the vrate bounds derive | `IOB-CEI` |
| Safety | refuses a target that is mounted, swap, signature-bearing or the root disk; never touches any cgroup `io.max`; restores every changed runtime knob on every exit | `IOB-TGT`, `IOB-SAF` |
| Read path | `iobench check` (is the result current and plausible for this disk) and `iobench show` (the proposals and evidence) | `IOB-CHK` |

## Quick use

```bash
# prepare a dedicated scratch partition once (destroys it; requires both guards)
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f...

# calibrate once per host / storage class
iobench calibrate --target /dev/vda7 --mount-dir /var/lib/iobench/work --profile standard

# iterate on settings (reuses the calibration)
iobench matrix --target /dev/vda7 --mount-dir /var/lib/iobench/work

# ask whether the result is usable, and read the proposals
iobench check --json; echo "exit=$?"
iobench show --proposals --json
```

## Layout

```
libraries/iobench/
  README.md  docs/{DESIGN-GUIDE,CONSUMERS,SPEC}.md  run-gate.toml
  src/iobench/   pure core (planning, reduction, derivation, schema) behind an injected Host; cli.py is the thin shell
  tests/         fake-fio and fake-sysfs scenarios, recorded-evidence replays, controlled-wrong plausibility cases
```

## Status

Specification phase (IOB-03 P1/P2). The estate decisions behind it are IOB-D-001..014 in the DESIGN-GUIDE. Nothing here is wired into
`mdt-cockpit` or `debian-install-v2` yet (migration phases M1-M4 in the DESIGN-GUIDE).
