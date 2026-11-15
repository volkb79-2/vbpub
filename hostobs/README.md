# hostobs

The Rust host-observability daemon planned in
[`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md)
as a probe plus an actuator. The workspace has the planned `proto`, `model`, `kernel`,
`store`, `eval`, `act`, and `client` library boundaries plus the `probe` and `act`
binary packages.

**What is implemented today.** Two of the seven library crates are functional:

- **`model`** — the data model: incarnation identity (`(mount id, cgroup inode)` with
  boot/epoch/process keys), the closed eight-code quality vocabulary, good/bad-quality
  values, the metric registry with unit/kind/scope/source/aggregation, and counter
  classification plus rate derivation over the *actual* monotonic interval.
- **`proto`** — the wire codec: frame header, section directory, fixed-width typed
  little-endian columns (raw `u64` counters, a `u8` quality per value), history buckets
  and bounded per-segment dictionaries. No I/O, no clock, no `unsafe`, no dependencies.

Both crates are pure: facts in, typed answers out, and every refusal is a typed error
rather than a panic or an invented zero. The codec's refusals cover the frame's own shape
too: a dictionary body that repeats an identity, a metric mask that is not the mask its kind
derives, a row naming a metric, a column naming an incarnation, a column whose kind
contradicts the dictionary, and a non-zero row count with neither a rows nor a time section
are all refused with a named error, in both the Rust and the Python decoder. The commitments they implement — identity by
incarnation and never by cgroup path, absence never written as zero, counters stored raw
with rates derived from the real interval, unknown future versions refused — are stated
in each crate's module docs and in
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md).

The `kernel` collectors and `store` segments are the next package and are still the M0
scaffold. The proven topos sampler
(`topos-sampler-poc-20261105`, `topos/sampler/`) remains their behavioral source; no code
was moved from it in this package.

Golden fixtures under `fixtures/` are shared by two independent implementations: the Rust
tests in `crates/model/tests` and `crates/proto/tests`, and a standard-library-only Python
reference codec in `python/reference/` that decodes and re-encodes the same bytes
(`python3 -m unittest discover -s python/tests`). SP-4's wire-size measurement and the
numbers that confirm decision D4 are in [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md).

The test-quality claim for these crates is a hand mutation campaign (Rust R2 is not adopted
yet), recorded in [`docs/HAND-MUTANTS-M1.md`](docs/HAND-MUTANTS-M1.md): twenty-one
mutations, all killed.

Open items found while building this package are in
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md) (`HOB-1` … `HOB-6`).

## Gates

Use the registered `lint` and `selftest` lanes from this directory:

```sh
run-gate lint
run-gate selftest
```

Cargo is offline and locked, and each Cargo invocation checks the mounted `Cargo.lock`
against the image's lock-specific cache manifest first. See
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the lane commands, the Python conformance
run and the "changing a dependency" path, and
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the boundary decisions.
