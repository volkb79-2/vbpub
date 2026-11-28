# Python client: `hostobs-mark`

The standard-library `hostobs-client` distribution provides `hostobs-mark`, a
command that sends one `annotate` line to the observer socket. Packaging details
are in [`PACKAGING.md`](PACKAGING.md).

**No release exists yet**, so there is no released wheel to install. When an
approved wheel is provided, set `HOSTOBS_CLIENT_WHEEL` to that wheel's full path
and install that file directly. On Debian 13, use a virtual environment because
the system Python is externally managed. The package has no runtime dependencies,
and `--no-index` avoids resolving the distribution name from a package index:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --no-deps "$HOSTOBS_CLIENT_WHEEL"
```

The command is installed at `.venv/bin/hostobs-mark` and is available as
`hostobs-mark` while the environment is active. Its target is
`hostobs_client.mark:main`.

**Current server and deployment status:** The probe serves `annotate` on the
observer socket. `deploy/` installs the observer socket
(`hostobs-observer.socket`), and connecting to it needs membership in the
`hostobs-observer` group. `hostobs-mark` exits 0 once the probe accepts the
annotation. When the annotation cannot be written (variable unset, socket
unreachable, error reply or bad reply), `hostobs-mark` prints a notice and exits
0; adding `--require` makes it exit 1.

Set the socket path and pass an annotation name accepted by WIRE §4.12:

```sh
HOSTOBS_OBSERVER_SOCKET=/run/hostobs/observer/hostobs.sock \
  hostobs-mark point hostobs.consumer-check --require
```

Usage errors exit 2 (WIRE §8.5 and `hostobs/python/hostobs_client/mark.py`).

# Using the hostobs Rust lanes

`model`, `proto`, `serve`, `kernel`, `store`, `client` and `bins/probe` are functional;
`eval`, `act` and `bins/act` are still the M0 scaffold. The registered default lanes are `lint`, `selftest`, `assay-r1`
and `conformance` (the Python tests). The estate wraps every lane run in the controller's gate wrappers, which take the
shared gate slots and the lane's lock (see `scripts/controller/README.md`, "Running the
registered gate" and "Run one lane on a remote gate worker"); a bare `run-gate <lane>` skips
that admission and `run-gate` refuses container lanes on the main checkout, so do not use it
as the way to run these.

The separately registered `assay-r2` command lane is hand-run only. `assay.toml` scopes its
changed-line mutation judge to six crate `src` roots; registration does not establish a passing
run (HOB-45/K15).

**Locally**, from the project directory `hostobs/` of a *linked worktree* (never the main
checkout):

```sh
cd <linked-worktree>/hostobs
. "$HOME/controller/controller.local.env"        # CONTROLLER_HOME's controller.local.env
scripts_dir=<repo>/scripts/controller
"$scripts_dir/gate-run.sh" lint
"$scripts_dir/gate-run.sh" selftest
"$scripts_dir/gate-run.sh" assay-r1
"$scripts_dir/gate-run.sh" conformance
```

**Remotely**, on a remote gate worker (needs `CONTROLLER_REMOTE_GATE_HOSTS=v1001`, or the
alias set the host table defines), one lane at a time, pinned to a commit or branch:

```sh
CONTROLLER_REMOTE_GATE_HOSTS=v1001 \
  scripts/controller/remote-gate.sh v1001 hostobs lint <commit-or-branch>
```

The lane's own exit code is returned (2 refusal, 3 identity or transport failure, 75 slots or
PSI busy, 76 `INCONCLUSIVE-OOM`, none of which is a lane verdict). The run log is written to
`$CONTROLLER_LOGS_DIR/<run>.log` with the binding line `run-gate.commit=<sha>`.

**The Rust lanes need the `tester-unified-rust` image on the executing host** (the local
Docker daemon, or the remote worker). If it is missing the lane stops with `verdict ERROR;
docker run failed (exit 125)` and `Unable to find image 'tester-unified-rust:rs-20261124b'`
(the image named by the `tester-unified-rust` environment in `run-gate.root.toml`); that is
an environment fault, not a verdict on the code.

The registered Rust lanes (`lint`, `selftest`, `assay-r1` and hand-run `assay-r2`) use
`tester-unified-rust` with Docker networking disabled. The image contains the
stable Rust toolchain, the cargo tools, Python for the cache/floor helpers, and the crate
archive closure for this exact `Cargo.lock`. Do not run Cargo from the cockpit or use a
network fallback. The lane image (`tester-unified-rust:rs-20261124b`) has a Cargo wrapper that refuses before
invoking Cargo if the mounted lock's registry-set digest, crate set, lock checksums, or cached
archive bytes disagree with the image (the registry set is the downloaded packages plus the
lock version; workspace members' own entries are not part of it). An image built before this
identity checks the sha256 of the whole `Cargo.lock` file instead; no lane runs on one (which
tag a host carries is a fact of that host, not recorded here).

## What `assay-r1` judges, and what it says

`assay-r1` is a `kind = "command"` lane: it runs the judge straight from this worktree's
own source (`PYTHONPATH={worktree}/assay/src`, plus `assay/analysis/src` for the
post-checks) and the judge runs the lane declared in [`assay.toml`](../assay.toml)
(`[lanes.assay-r1]`). `assay` is stdlib-only, so nothing is installed and the Rust image
needs no `pip` — that is the whole reason the lane has this shape instead of
`kind = "assay"`, which would have Run-Gate `pip install` the worktree first and fail
here (see [`HOB-44`](#hob-44-closed-the-lanes-own-declaration-shape) below). Its
own argv is the same offline/locked cargo-llvm-cov + nextest + doctest + crate-floor
command `selftest` runs, so the two cannot disagree about what was executed; what changes
is who renders the coverage verdict. The artifacts are `lcov.info` and `cov-summary.json` in
this directory (git-ignored), and the verdict is
`hostobs/.assay/verdict-assay-r1.json`.

The verdict's R1 claim carries measured totals, not a restated floor. The current declaration
in `assay.toml` judges 61 `src` files across the six functional crates and `bins/probe`. The
example below is a historical verdict from head `3b46df06`, when the declaration had 33 files;
it shows the shape, not the current file count or a current-head result:

```json
{"covered": 15100, "executable": 15723, "considered": 33,
 "branch_capability": "unavailable", "branches_total": 0,
 "missing_lines": {"hostobs/crates/kernel/src/cgroup.rs": [270, 319, ...], ...}}
```

Read it as: at that historical head, 33 declared `src` files of `model`, `proto`, `kernel` and `store` were judged,
15100 of their 15723 executable lines ran (96.04 %), the floor in the lane file is 95.0 %,
and every uncovered executable line is listed by file as a finding (`missing_lines`).
`branch_capability: "unavailable"` and `branches_total: 0` are the honest answer for stable
Rust (cargo-llvm-cov writes no `BRDA:` arcs), not a zero-of-zero pass: the crate's R1 claim
is line-only, and the lane declares `require_branch = false` rather than pretending.

Fields to look at, in order: `outcome` (`PASS`/`FAIL`/`NO_MEASUREMENT`/`ERROR`),
`claims[].coverage.missing_lines`, `claims[].coverage.covered/executable`, and
`commit` (must be the head you meant to judge). `assay verify` and
`assay analyze report` read the same file independently.

### Floors and where they come from

The floor is `judge.fail_under = 95.0`, one full point BELOW the measured 96.04 % (15100
/ 15723 executable lines on this workspace at head `3b46df06`). Per crate at that head:
`model` 98.09 % (2004/2043), `proto` 99.71 % (3454/3464), `kernel` 93.52 % (2627/2809),
`store` 94.71 % (7015/7407). The derivation, the one-point margin and the rule that a
floor is never lowered live in [`assay.toml`](../assay.toml)'s header. The per-crate line and
region floors in [`coverage-floors.toml`](../coverage-floors.toml) stay in the lane's argv and
remain the tighter bound; Assay's line claim does not replace them, and region coverage is
not part of Assay's R1 claim.

### Proof it is a real judge, not a renamed floor

At head `3b46df06`, Run-Gate proved the judge in this workspace's own lane (no throwaway
copies): the same 33 targets returned:

| run | nextest | assay R1 |
|---|---|---|
| `run-gate assay-r1` at head `3b46df06` | 333 tests pass | `PASS`, 15100/15723 = 96.0377 % |
| that historical lane, one kernel test file deleted and committed in a temporary worktree | 322 tests pass (all green) | `FAIL/UNCOVERED_LINES`, 14777/15723 = 93.9833 % |

So the verdict is the measured number and the failure is the coverage claim itself; the
test command stays green in the negative case, which is exactly the difference between a
coverage verdict and a test result. The negative lane logged both its claims and its
totals (`assay-r1 claim: R1 FAIL UNCOVERED_LINES`, `lines 14777/15723 = 93.9833%`), which
is why the lane captures the judge's exit status instead of aborting on it. Earlier, the
same deletion was also measured in throwaway copies with the lane's crate-floor step
neutralized (`true` in place of `tools/crate-floors.py`), so nothing but the Assay R1
claim could have failed it; re-running that step over a negative copy confirms it refuses
the deletion independently (`ERROR: crate crates/kernel line coverage is 81.24%
(2364/2910); floor is 92.00%`). In that historical registered lane no neutralization was
needed: R0 FAILed first (`COMMAND_FAILED`) and R1 FAILed on `UNCOVERED_LINES`, both in one log.

### `HOB-44` (closed): the lane's own declaration shape

`tester-unified-rust` carries `python3` but no `pip`, no `ensurepip` and no
`python3-venv`. Under the original `kind = "assay"` declaration that was fatal: Run-Gate
installed `assay` from the bind-mounted worktree before the judge ran, so the lane stopped
at `run-gate: editable assay install failed` / `/usr/bin/python3: No module named pip` and
reported `ERROR` with no verdict artifact. The fix is a declaration change, not an image
change: the
lane is now a **command lane** that runs `python3 -m assay.cli run assay-r1` with
`PYTHONPATH` pointing at the worktree's own `assay/src` — which is exactly how assay is
consumed from inside this monorepo — so there is nothing to install. The image is unchanged
and should stay that way (it is offline and lock-bound by design). `HOB-44` is CLOSED; assay
B549 records the same finding from the judge's side.

### What is not covered

- **Branches** — unavailable on stable Rust (see above). A branch number here would be invented.
- **Mutation (R2)** — a hand-run `assay-r2` lane is registered. `assay.toml` judges changed
  lines in `crates/model/src`, `proto/src`, `serve/src`, `kernel/src`, `store/src` and
  `client/src` by ingesting cargo-mutants' outcomes; `run-gate.toml` declares the command lane
  and does not put it in a default sequence. The first hostobs-sized campaign is tracked by K15,
  so lane registration is not a pass. The hand-mutation campaigns in
  [`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md), [`HAND-MUTANTS-M1B.md`](HAND-MUTANTS-M1B.md) and
  [`HAND-MUTANTS-M1C.md`](HAND-MUTANTS-M1C.md) remain the test-quality evidence;
  [`HAND-MUTANTS-M1D.md`](HAND-MUTANTS-M1D.md) holds the stored patches of the P0 bug fold-in,
  whose campaign ran on 2026-11-18 (54 of 54 patches killed on `6fa50236f`).
- **Dead second bound in `decode_block`.** Its own `declared > MAX_FRAME_BYTES` check cannot
  fire any more, because `FrameHeader::parse` refuses the same length first. It is kept as a
  second bound whose multi-line `return Err(..)` is uncovered and the coverage floor may count; see
  [`HAND-MUTANTS-M1D.md`](HAND-MUTANTS-M1D.md), "Known limit".
- **Canaries (R3)** — needs a receipt-writing test-break canary (assay B246 / R-4); not registered.
- **Inline `#[cfg(test)] mod tests` blocks inside a judged file** are measured (LCOV has no
  per-region exclusion, assay K5). They are NOT a small effect: at historical head `3b46df06`,
  counting from each file's `#[cfg(test)]` line to its end, roughly a third of the four crates'
  source lines (about 10.5k of 28.7k) were inline test code, whose executable lines sat in the
  15723 denominator and were covered whenever the tests ran. The 96.04 % therefore overstated
  product-code coverage for that historical 33-file set, and
  a drop of the 629-line headroom lands almost entirely on product code, so 95.0 % is looser
  on product code than it reads (the sampled uncovered line `store/src/tier.rs:1553` is a test
  `assert!` message argument). The per-crate floors in the lane's argv carry the same bias.
- **Five of the six judged `crates/*/src/lib.rs` files** (`model`, `proto`, `serve`, `store`,
  `client`) are module
  declarations and `//!` documentation only, so cargo-llvm-cov emits no LCOV record for them and
  they are not declared targets; Assay refuses a declared target it cannot measure rather than
  excusing it. `crates/kernel/src/lib.rs` does carry `Bounds` (7 executable lines, all covered),
  so it is one of the current 61 targets.

## The independent Python cross-check run

The fixtures in `fixtures/` are the contract between two implementations written from the
same specification. Regenerate and check them with:

```sh
python3 python/reference/make_fixtures.py /tmp/fixtures-expected     # wire cases
python3 python/reference/make_segment_fixtures.py /tmp/segment-expected  # segment files
diff -r fixtures/wire /tmp/fixtures-expected                         # committed bytes are current
diff -r fixtures/segment /tmp/segment-expected                       # ditto, whole files
python3 -I -m unittest discover -s python/tests -v                   # decoders, rate reference, hostobs_client
```

The same run covers the Python client package `python/hostobs_client` (`wire`, `stream`, `rates`):
`test_client_wire.py` (the 70 wire fixtures and a differential fuzz against the reference),
`test_client_stream.py` (the 12 reader and 6 client stream fixtures), `test_client_rates.py`,
`test_client_stdlib_only.py` and `test_client_budgets.py` (the gated §7.8 line-count and memory
bounds; the absolute memory bound runs on CPython 3.14.10 only). `python3 -I python/bench_decode.py`
prints the ungated CPU timings.

The connection layer (`conn`, `envelope`, `profiling`, `mark`) is tested without a live probe:
`test_client_conn.py` steps a scripted fake probe (`fake_probe.py`, one `socketpair`, the fake clock jumps
instead of sleeping), `test_client_path.py` covers `Connection.open`, `reconnect`, `require_profiling` and
`hostobs-mark` over real `AF_UNIX` paths (the only server thread of the suite), and `test_client_zipimport.py`
builds a zip of the package in a temporary directory and runs `python3 -I` against it. Run one file with
`python3 -I -m unittest discover -s python/tests -p 'test_client_conn.py' -v` (one Python process at a time on a shared host).
A consumer calls `require_profiling("observer", required)` once at start and shows `state.notice` verbatim;
`python3 -m hostobs_client.mark point NAME` writes one annotation (exit 0 when hostobs is unavailable, `--require` makes that 1).

`python/tests` needs only the standard library and never imports the Rust code. The
regeneration must reproduce the committed bytes exactly: the Python encoders are
independent of the Rust ones, so a difference is a real divergence rather than a cache
artefact. Do not edit a `.bin` or a `.seg` by hand — change the case description in the
matching `make_*.py` and regenerate. The segment layout they implement is specified in
[`SEGMENT-FORMAT.md`](SEGMENT-FORMAT.md), and the Rust side asserts the same files in
`crates/store/tests/golden_segment.rs`.

**This run is the registered `conformance` lane** (`HOB-4`): `run-gate.toml` runs
`python3 -B -I -W error::ResourceWarning -m unittest discover -s python/tests` on
`tester-unified-rust` with `network = "none"`, so a failure fails the gate. That image's `python3` is
Debian's apt package (CPython 3.13.5 on Debian 13, no pip), not the CPython 3.14.10 the WIRE.md §7.8
absolute memory bound was measured on: that one test is skipped there (its reason names the pinned
version), while the growth and line-count gates and every other test run. The lane is not judged by
Assay. Run it the same way as any lane (`scripts/controller/gate-run.sh conformance` in a linked
worktree, or `remote-gate.sh v1001 hostobs conformance <commit>`).

## Working with the model in a probe or test

Identity is an incarnation, never a path:

```rust
use hostobs_model::identity::{CgroupKey, IncarnationRegistry};

let key = CgroupKey::new(31, 0x1234)?;        // (mount id, cgroup inode); zeros are refused
let mut registry = IncarnationRegistry::new();
registry.begin_round();
let inc = registry.observe(key, Some("/system.slice/unit.scope"))?;
registry.end_round();                          // one absent scan is not deletion
```

Rates come from two readings and two monotonic instants; the interval is the real one:

```rust
use hostobs_model::rate::{derive_rate, MonoTime};
use hostobs_model::value::{Sample, Value};

let outcome = derive_rate(
    Sample::measured(Value::U64(1_000)),
    MonoTime::from_nanos(1_000_000_000),
    Sample::measured(Value::U64(3_000)),
    MonoTime::from_nanos(3_000_000_000),
    1_000_000_000,                             // nominal interval: reports gaps only
);
assert_eq!(outcome.rate().expect("a rate").per_second(), 1_000.0);
```

A missing reading is built with `Sample::missing(quality)?`, which refuses `Quality::Ok`;
read a value with the `measured_*` accessors and it returns `None` unless the sample is
real.

## Encoding and decoding a block

```rust
use hostobs_proto::dict::{Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit, MetricValueType};
use hostobs_proto::frame::{encode_block, decode_block, Block, BlockHeaderFacts, Column, FrameType, Row};
use hostobs_proto::quality::Quality;

let mut dictionary = Dictionary::new(0);
dictionary.intern(Entry::Entity { inc: 11, label: "/a".into() })?;
dictionary.intern(Entry::Metric {
    facts: MetricFacts {
        unit: MetricUnit::Microseconds,
        kind: MetricKind::Counter,
        scope: MetricScope::Cgroup,
        source: MetricSource::CgroupFs,
    },
    name: "cpu.usage_usec".into(),
})?;

let block = Block {
    frame_type: FrameType::Block,
    corr: 7,
    header: BlockHeaderFacts { /* boot_id, epoch, seq, monotonic bounds, real_ns, dict_gen, n_rows, dropped, flags */ },
    rows: vec![Row { ent: 0, t_off_us: 0 }],
    time: Vec::new(),
    columns: vec![Column::new(1, MetricValueType::U64, MetricKind::Counter, vec![1234], vec![Quality::Ok.code()])?],
    dictionary: Some(dictionary),
    skipped_sections: Vec::new(),
};

let bytes = encode_block(&block)?;            // canonical bytes
let decoded = decode_block(&bytes)?;          // the same typed block, or one typed error
```

`decode_block` returns either a complete validated `Block` or one `ProtoError`; it never
panics on untrusted bytes, never returns a partial result, and reports an unknown optional
section in `skipped_sections` while refusing an unknown required one.

The refusals a producer will meet while assembling blocks, with their `kind()` names:

| Condition | Refusal |
|---|---|
| the dictionary body repeats one identity (an `inc`, a metric name, a label, free text) | `duplicate-dictionary-entry` |
| a metric entry's aggregation mask is not the mask its kind derives | `dictionary-entry-mask` |
| a row names a dictionary entry that is not an incarnation | `row-reference-not-entity` |
| a column names a dictionary entry that is not a metric | `column-reference-not-metric` |
| a column's kind contradicts the dictionary's metric facts | `column-kind-mismatch` |
| a non-zero row count with neither a rows nor a time section | `no-rows-or-time` |
| both a rows and a time section, even when both are empty | `rows-and-time` |

A dictionary body is reproduced position for position: `Dictionary::decode` refuses a
repeated identity instead of merging it, because merging would shift every later index away
from the sender's numbering. Use `Dictionary::encode_with` to write an exact list of entries
when a test needs to express a wire list a live table would never build.

## Writing and reading history through the store

A store is configured, opened, fed from the writer queue, and queried by time. The layout it
writes is specified in [`SEGMENT-FORMAT.md`](SEGMENT-FORMAT.md); nothing below writes a file
directly, because `fsio` is the only module that may.

```rust
use hostobs_store::queue::Job;
use hostobs_store::store::{Store, StoreConfig};

// plan v4's hot/warm/cold/events set, with every bound set explicitly (no defaults).
let config = StoreConfig::plan_v4(Path::new("/run/hostobs"), 7, 1)?; // epoch, dict generation
let mut store = Store::open(config)?;         // rescans the directories and repairs tails

// Submit is bounded. An oversize job is refused as `job-too-large`; a full queue drops the
// oldest job and *says* which bytes it lost.
let report = store.submit(Job::new(payload, real_ns, vec![incarnation]))?;
if report.dropped_anything() {
    // lost records are a report, never silence
}

store.write_pending(now_ns, 64)?;   // bounded drain; a write failure is counted, not fatal
store.evict(now_ns)?;               // keep_s first, then cap_bytes, whole segments oldest first
store.seal_all()?;                   // temp name -> write -> fsync -> rename -> fsync dir

// A range query is bounded in records and bytes across the whole store, and answers with the
// stored payload byte for byte. Decoding it is the caller's step: the store carries no column
// codec (`HOB-39`), only the quality vocabulary.
let answer = store.range(from_ns, to_ns, 4096, 1 << 20)?;
for routed in &answer.records {      // `routed.tier` says which tier it came from
    // routed.record.payload, routed.record.real_ns, routed.record.incarnations
}
```

Every refusal a caller will meet, with its `kind()` name:

| Condition | Refusal |
|---|---|
| a tier directory the config requires to be tmpfs is not (`require_tmpfs`) | `not-tmpfs` |
| the mount table cannot be read while such a tier is required | `mount-table-unreadable` |
| a write fails with `ENOSPC` | `no-space` |
| a job is larger than the whole queue bound | `job-too-large` |
| a segment's header declares a payload its tier kind does not carry | `tier-kind-mismatch` |
| a record's envelope kind is not the segment's corpus | `record-corpus` |
| a record's payload is not the frame type its corpus requires | `payload-frame-type` |
| the dictionary is at `dict_active_max` | `dictionary-full` |
| a segment still names an incarnation the retirement pass was about to free, a queued job's payload names it, or a minted record has not yet reached a segment | (not retired; `retired` count) |
| a range asks for zero rows or zero bytes | `query-limit` |
| a tier directory the config requires to be tmpfs is spelled with `..`, is relative, or crosses a link | `not-tmpfs` (the check resolves first; the mount table lists what the kernel resolved) |

The four failures that must never be silent have their own answers, and a test each:

| failure | what a caller sees |
|---|---|
| disk fill | `no-space` from the write, counted in `StoreStats::write_failures`; nothing is lost silently because the job is still queued |
| RAM growth | a full queue drops the **oldest** job and returns it; the dictionary and the query limits are refusals |
| torn file | `torn_repaired` (truncated back to the last good record) or `quarantined` (renamed `.corrupt`), both counted and both reported by `notes()`. A tail of pure zeros is the unwritten part of a crash and is truncated, not quarantined; a tail of any other bytes is corrupt. |
| a file bigger than the read bound | quarantined `.corrupt` and counted in `TierScan::oversized`; never skipped, because a file outside the ring is outside the byte total and eviction would never reach it |
| a segment whose header cannot be read (EACCES, EIO, a race) | counted in `TierScan::unreadable`, reported in `notes()`, left in place; the rest of the tier still opens |
| a seal that fails before its rename | the open handle and the ring entry come back, no `.tmp` is left behind, and the next write goes to the same open segment |
| silent loss | `Submit::dropped_jobs`/`dropped_records`/`dropped_bytes` and the matching `StoreStats` counters; `StoreStats::lossless()` is true only when every one is zero |

A store only ever deletes a file that is its own by name **and** header kind. A file that
looks like a segment but holds another corpus (or a link, or a subdirectory) is reported in
`notes()` and left in place — which is what lets hot and warm share a directory. The one file
it removes without reading it is its own stale `<n>.seg.open` twin beside an already-sealed
`<n>.seg`: the crash window between the seal's rename and its unlink. Removing the twin is
safe only because the name parse and the sealed file's header both prove it is the store's,
and keeping it would count its bytes twice and reopen a file whose records already left.

## Changing a Rust dependency

Adding, removing or changing a registry or git package (name, version, checksum, source)
or the top-level lock `version` requires rebuilding `tester-unified-rust` from the reviewed
lock before the lanes can pass. An edit that only touches workspace members (a path
dependency between workspace crates rewrites their `dependencies` arrays and adds no
downloaded crate) does not change the image identity and needs no rebuild. This initial
image build is a local bootstrap; CMRU-owned version resolution and image refresh belong to
the later R-5 work package. Do not edit `Cargo.lock` by hand to satisfy a lane: the baked
registry-set digest is the authority, and a mismatch is a signal to rebuild the image, not
to hand-edit the lock.

Which image runs what: the Rust lanes run on `tester-unified-rust:rs-20261124b`, which carries
the registry-set identity, so a workspace-only lock edit (a path dependency between members)
needs no image rebuild. An image with the older whole-lock identity rejects ANY lock byte
change. Which tag a host carries is a fact of that host and is not recorded in this repository.

## Select a published cgroup

A minor-2 `current` request can select a cgroup by its `stat(2)` identity and read its CPU usage and
CPU limit readings:

```json
{
  "verb": "current",
  "args": {
    "selection": {
      "entities": [{"cgroup_id": {"dev": 2049, "inode": 5159}}],
      "metrics": ["cpu.usage_usec", "limit.cpu.max.period", "limit.cpu.max.quota"]
    }
  }
}
```

Call `stat(2)` on the cgroup directory and use `st_dev` for `dev` and `st_ino` for `inode`; `dev` is
not a mount ID. The selector resolves only against the tick served for that request. It is not a
durable record key, and hostobs does not guarantee that the pair never names a later cgroup,
including after a reboot or on another host. Keep the reply's `inc` together with `boot_id` and
`epoch` when recording identity, then compare those fields. `limit.cpu.max.quota` and
`limit.cpu.max.period` are gauge values in microseconds: quota is `unsupported` for `max` and for an
absent `cpu.max`; period reports `P` for a readable `Q P` or `max P` line and is `absent` when the
file is missing.

Send `cgroup_id` only when the negotiated minor is 2 or higher. A pre-minor-2 server refuses the
member as `bad-request` with `detail.key` `selection.entities.cgroup_id`; a minor-2 server whose
negotiated minor is below 2 refuses it as `unsupported-feature` with `detail.feature`
`cgroup_id`. A container has to receive the host key from its launcher; no launcher passes one
yet (HOB-105). This document makes no claim about a container-local `stat`.

## Status: the live slice

The hostobs live slice is on `main`. It is **not** a versioned release: no tag, package or
installer. Use it from a checkout (`PYTHONPATH=hostobs/python`; the units in `hostobs/deploy`
are installed by hand).

**Available:** the observe and operator sockets; observer `hello` and `annotate`; the observe and
operator verbs `hello`, `status`, `current`, `subscribe`, `unsubscribe` and `events`; fixed-width BLOCK
frames with a quality byte per value; the Rust client (`hostobs/crates/client`) and the stdlib-only
Python `hostobs_client`; the `cgroup_id` selector and the `limit.cpu.max.*` metrics (wire minor 2).
`deploy/` installs the observer socket.

`hostobs-operator` is reserved for reads with command-line details (`procs`, not served yet);
today it returns the same data as `hostobs-observe`.

**Not available yet:** `spans` (it answers `unknown-verb` at every probe level);
the control level and sessions; the install CLI. Version 0.1.0 follows after M3, once the release
packages are in.

Limits:

1. **Performance is not established.** The only probe CPU figure is a debug build under QEMU
   emulation (27.3 CPU-s in 59 s, median 434 ms per 1 s tick). No release build was measured on
   native hardware. The VM result is functional only.
2. **What the demo exercised.** Only `cpu.usage_usec`, `mem.current` and `pids.current`. It did
   not exercise PSI, `io.stat`, `memory.stat`, the host `/proc` metrics, `probe.*`, the observer
   socket, the `cgroup_id` selector, the `limit.cpu.max.*` metrics, a reboot, or a non-root
   operator-group member. The observer socket did not exist in the recorded run.
3. **The root cgroup is not a share denominator.** The root's `cpu.usage_usec` is tick-accounted
   and is not comparable with child cgroups; do not compute shares against `/`. On the root,
   `mem.current` and `pids.current` are always `absent` (expected — see the table below).
4. **`current` before the probe's first tick** answers `internal` (HOB-94): retry, or use
   `subscribe`.

For the live-demo verdict, each `closed` line must keep the exact
`closed<TAB>blocks=<n><TAB>reason=<reason>` form, and `<n>` must be an integer at least as large
as the requested tick count (N observe ticks, one operator tick). The count can be higher because
`unsubscribe` counts blocks that arrive while it waits for its reply
(`hostobs/crates/client/src/client.rs:550-558`).

### Expected `absent` cells in the recorded Debian 13 guest

`absent` means the file is not there (WIRE's quality vocabulary: only `0` is a measurement, and
`absent` is "the file or key is not present"). Permission-denied and I/O read failures are
`unreadable`, never `absent`. For cgroup-v2 controller files, a controller must be enabled in the parent
cgroup's `cgroup.subtree_control` for its files to be exposed in a child; other delegated trees
can therefore have additional `absent` cells. The kernel reader maps a missing controller file
to `absent` and permission/I/O failures to `unreadable`; bounded-overflow cases can be
`not-sampled` (`crates/kernel/src/cgroup.rs`, `Absence` and `read_file_error`, and
`crates/kernel/src/fs.rs`, `FsError::quality`). The table below records one guest, not every host.

| cgroup | metric | quality | why |
|---|---|---|---|
| `/` (the true cgroup v2 root) | `mem.current`, `pids.current` | `absent` | both files are registered `CFTYPE_NOT_ON_ROOT` in the kernel, so the root has no such file; `cpu.stat` (hence `cpu.usage_usec`) and `*.pressure` exist on every cgroup, root included |
| `/system.slice/systemd-udevd.service/udev` | `mem.current`, `pids.current` | `absent` | systemd 257 runs udevd under `Delegate=` with `DelegateSubgroup=udev`, and nothing writes `memory`/`pids` into that parent's `cgroup.subtree_control`; the leaf therefore has only core files, while its parent `systemd-udevd.service` still shows `pids.current=1` (hierarchical) |
| every other entity in this transcript | all three | `ok` | the files exist and are read |

The host `/proc` metrics this slice publishes only appear on the root row (WIRE §4.7). **The model
table has no host-wide memory metric — there is no `/proc/meminfo` metric** — so a consumer that
needs host memory cannot get it from a hostobs row today; that is a new-metric item for the
consumer's own backlog, not something to derive from the root's `mem.current`.

In part (a)'s container run (`.controller-state/hostobs/evidence/live-container-20261124.txt`) the root row is `ok` for all
three metrics, and that is consistent: `/sys/fs/cgroup` there is the container's
cgroup-namespace root, a non-root cgroup of the real hierarchy, so `memory.current` exists.

## Boundaries

These four rules say where hostobs ends and its neighbours begin; later packages and other tools
follow them.

1. **cgprofile is frozen and shares no code.** Until hostobs 1.0 runs on both hosts with one R2
   campaign each, cgprofile 3.x takes fixes and the M4 `mark` forwarder only, and no code is
   shared between the two (plan v4 §G, §D.1; cgprofile backlog item CP-66). New collector and
   observer features are built in hostobs.
2. **A span is one JSON line on the observer socket.** The line is a JSON-mode request
   (WIRE §8.1, §8.2) with verb `annotate` and the args of WIRE §4.12 (typed span fields in plan v4
   §C.4). WIRE §4.12 fixes its args and result, and the probe serves `annotate` on the observer
   socket. The future control client sends
   the same line to act on the control socket. The intended integration keeps the judges, assay and
   run-gate dependency-free; they write that line themselves. `hostobs_client` (WIRE §7) and
   `hostobs-mark` (WIRE §8.5) serve importers such as cmru, iobench and `cgprofile mark`:
   programs and scripts that would rather call a helper than speak the wire.

   The server takes `t_mono_ns` from the current turn's monotonic clock. It derives `real_ns` from
   the startup wall-clock and monotonic-clock pair, so a wall-clock step after startup does not move
   event time. Peer `uid` comes from `SO_PEERCRED`; pid, comm and cgroup are advisory labels. Every
   observer member may emit any grammar-valid event name, so a reader trusts `thrashguard.*` only
   when `issuer` is the expected uid. All root emitters share one uid rate bucket across connections.

   A span belongs to its connection. It may stay quiet longer than `idle_ms`, and it ends when its
   connection closes; until then it holds one connection slot. The observer caps remain 128 open
   connections total and 64 per peer uid. A one-shot `hostobs-mark begin` connection closes when
   the command exits, so its open span is recorded as `abandoned`. A normal connection close also
   writes inferred abandoned ends. `SIGTERM` keeps its default action under HOB-92: a stopped probe
   writes no inferred ends, and open spans remain open just as they do after a crash. Only the
   internal `Server::shutdown` stop path closes connections and records their inferred ends.

   Appends run synchronously on the server thread and do not fsync for an ordinary record. Sealing
   a segment (and any eviction during that append) can fsync and stall the turn. All clients' replies
   and subscription ticks wait for it. A stall shorter than a tick interval delays the tick; a longer
   stall lets the sampler publish several ticks while the server is blocked, then the next
   `begin_turn` reads the newest published tick and delivers only that one. A new epoch or bucket
   seal that keeps failing (for example, a full disk) makes every append return `internal` until an
   append succeeds again. Appends are unsynced and can be lost on a host crash.

   A probe restart empties the events ring, spans and idempotency cache. For an idempotent point,
   readers may dedupe by `(name, boot_id, seq)` using the `attrs` carried by that point; generic
   event items do not carry that identity. `annotate_rate_per_s` and `annotate_burst` are
   required `[limits]` keys; installed configurations must add both.

   **Reading the event ring.** `events` is a process-local RAM window. Treat any request with
   `from_real_ns < started_real_ns` as incomplete; `displaced` resets to zero at restart, so it
   cannot identify events lost before that process started. A page set is complete only under the
   WIRE §4.10 completeness rule; restart the read when a gap appears. Order generic items by
   `(t_mono_ns, ordinal)`. A repeated item across continuation pages is overlap, not a duplicate
   event; a displaced window means restart the read and accept the reported gap. Size `events_ring_bytes` for the consumer's expected lane
   length: at the roughly 9.5 KB worst item, a 4 MiB ring holds about 440 events, and one observer
   member emitting at the 64-event burst plus 50/s rate can fill it in about 8 seconds. `attrs`
   are not filterable in M3; readers match them client-side.
3. **act never writes `io.cost`, `io.weight` or sysctl.** `hostobs-act` serves only the verbs
   of plan v4 §A.2, and iogovmon owns `io.cost.*`, `io.weight` and the sysctls (plan v4 §A.1,
   "Never does" column for `hostobs-act`).
4. **iogovmon reads hostobs only after M3, and only to do less.** It reads through the library
   once the M3 release exists, and hostobs data may only make iogovmon less active (iogovmon's
   asymmetric trust rule H2, `docs/design/iogovmon/DESIGN-rev9-2026-11-06.md`; plan v4 §D.1, §G).
