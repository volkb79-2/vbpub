# hostobs design decisions

This guide records *why* hostobs is built the way it is, including the alternatives
that were rejected, so a later agent does not re-argue a settled question. Normative
behavior lives in [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../../docs/design/hostobs/PLAN-v4-2026-11-08.md);
adoption steps live in [`CONSUMERS.md`](CONSUMERS.md); open items live in
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md).

## Package boundaries

`proto` and `model` are pure libraries: no I/O, no clock reads, no threads, no `unsafe`,
no dependencies. The probe reads the host and hands facts to them; the store writes bytes
they produce; the client decodes bytes they describe. Purity is what makes the property
tests deterministic and what keeps a wire bug from hiding inside a filesystem call.

The workspace denies unsafe Rust by default. Only `kernel` and `act` declare the planned
unsafe-code allowance. Cargo rejects combining a workspace-lint inheritance stanza with a
package-level override, so those two manifests declare their allowance directly while the
other seven inherit the workspace lint. The allowance is nominally there for `kernel`
(`statx`, a `procfs` read) but **nothing uses it**: the crate carries its own
`#![forbid(unsafe_code)]`, which a package lint override cannot raise, and every host read
is `File::open`, `read_dir` and `symlink_metadata`. The allowance stays declared because
`tools/check-lints.py` pins the workspace's expected override table, and because the first
thing that genuinely needs a syscall (a `statx` mount id, `HOB-18`) should be a one-line
manifest change reviewed on its own, not a crate-wide relaxation.

## Wire: why fixed-width typed columns (D4)

The topos sampler stores varint TLV values. `proto` stores fixed-width typed little-endian
columns instead: one `u32` metric index, one `u8` type, one `u8` kind, a `u16` reserved
field, then `n` eight-byte values and `n` quality bytes, padded to eight.

The measured trade is in [`SP-4-WIRE-SIZES.md`](SP-4-WIRE-SIZES.md). Like for like — both
formats carrying raw absolute counters, which is the whole point of §C.3 — the columns
cost 82.7 B per row against 62.1 B at 100 cgroups and 80.2 B against 62.9 B at 1,000: a
ratio of about 1.3×. That difference is the mandatory `u8` quality column and the row
offset column, both of which the sampler cannot express at all. The rejected alternative
was FlatBuffers: it needs a `flatc` toolchain, a runtime dependency and an image rebuild,
and it still has no zero-cost place to put "absent, not zero". Varints were rejected
because a per-value parse loop is the log2-of-magnitude cost the plan wants to stop paying
and because a varint `u64` above 2^53 invites an `f64` round trip.

## Wire: layout choices the plan left open

- **Section bodies are length-prefixed and eight-byte aligned**, and a reader requires a
  body's declared length to equal what its content needs. There are no inter-body pad
  bytes: `16 + 8 × n_sec` is divisible by eight, so alignment is a property of the layout
  rather than a rule to police at every body.
- **The dictionary travels inside the block frame** (section kind 5) rather than as a
  separate `DICT` frame on the block path. §B.3 allows a complete generation to precede
  the values that use it; carrying it inside the block is what makes an exported segment
  self-contained (§C.5) and a fixture a single file per case. The `DICT` frame type stays
  defined and unconsumed (`HOB-6`), so a later package can split it out without a wire
  change.
- **An unknown *optional* section is skipped by its declared length and reported** in
  `Block::skipped_sections`; an unknown *required* section fails the block. A **known**
  section carrying any version this codec does not implement — older or newer — is
  refused even when optional: decoding an older body as version 1 would report values the
  sender never wrote.
- **The header's row count is the single authoritative count** for rows, columns and
  history buckets. A section that disagrees is a typed refusal, never a truncation or a
  guess.
- **Oversize is refused before allocation.** A frame declaring more than 16 MiB is
  rejected without using that number as an allocation size, and exactly 16 MiB is legal.

## Data model: why identities are incarnations

Plan v4 §C.1: a cgroup's identity is an incarnation id minted for `(mount id, cgroup
inode)`. The path is a *label*. The reasons, in the order they matter:

1. A path can be re-used by a different cgroup (a re-created container), so a path key
   silently joins two different lifetimes and produces a rate across a boundary.
2. A path can be renamed while the cgroup lives on, so a path key silently splits one
   lifetime into two.
3. Matching by inode survives the rename and separates the re-creation.

The registry therefore retires after **two consecutive absent scans** (missing one scan is
not deletion), a rename keeps its id with an updated label, and a retired key that
reappears is minted again with a fresh id and never reused (`Incarnation::UNASSIGNED` is
0 and ids start at 1). Process identity is `(boot_id, pid, start_ticks)` for the same
reason: a bare pid is recycled.

## Data model: why quality is a mandatory column

§C.2: zero is a value; absence is never zero. The sampler stores a value or a state code
per value, but nothing that says "absent" without writing a zero, so a consumer reading a
missing file as `0` is indistinguishable from a real zero. `proto` therefore writes a
`u8` quality for every value and never omits a row: a missing sample keeps its eight
bytes and the quality code says not to read them. `model::Sample::missing` refuses
`Quality::Ok`, so an absence cannot masquerade as a measurement even in memory.

## Data model: rates over the actual interval

§C.3 and SP-2: counters stay raw `u64` on the wire and on disk. `model::derive_rate`
divides the delta by the **actual** monotonic interval between the two readings, so a
missed sample widens the interval and yields the average over the whole gap instead of
inflating one nominal bucket. `dt = 0` and `dt < 0` produce no rate at all (the second is
refused, not clamped). A decreased raw counter is `NoRate::CounterReset`: the current
reading is the new baseline and no delta of either sign is invented.

**Counter wrap is deliberately not inferred** (`HOB-1`). Without a declared counter width
and a plausibility policy, a wrap and a reset are indistinguishable, and guessing one is
an invented rate — exactly what §C.3 forbids. The classifier is the single place a
declared-width policy would be added.

The no-rate outcome maps onto the closed quality vocabulary as: no baseline or zero-length
interval → `not-sampled`; reversed interval → `stale`; reset → `reset`; a missing sample
propagates its own code; a value of the wrong type → `unsupported` (a caller defect
visible in the quality column instead of a panic).

## Dictionary decoding: the wire list is the sender's numbering

`Dictionary::intern` de-duplicates and re-labels, which is correct for a *live* table
(plan v4 §C.1: an incarnation's label is not its identity, so a rename keeps the entry and
its index) and wrong for a *wire* body. `decode` therefore never trusts `intern`'s index: it
checks that the entry it just interned came back at the position the sender wrote it and
refuses with `duplicate-dictionary-entry` otherwise. Deduplicating instead would shorten the
table and shift every later index, which is precisely the meaning change §B.3 forbids, and
would make the two decoders disagree with no fixture to catch it.

The aggregation mask is treated the same way: it is **derived** from the metric kind, so the
decoder requires equality, not "no extra bits". A strict subset used to decode and then be
re-widened by `encode`, which silently discarded the sender's restriction; a subset is now
`dictionary-entry-mask`.

A reference is also checked for **kind**, not only for range: a row must name an
incarnation, a column a metric, and a column's declared arithmetic kind must agree with the
dictionary's facts. Range alone would let a consumer print a metric name as a cgroup, or
aggregate a gauge by counter rules, while every individual field looked valid.

Finally, the block's shape rules are about **presence**, not about counts: rows and time
sections are mutually exclusive even when both are empty, and a non-zero row count with
neither section is `no-rows-or-time` on both the encode and the decode path. There is no
"rowless host-scope block" in this codec: values that carry neither an entity nor a bucket
cannot be addressed, so inventing a layout for them would be a new mode with no specification
behind it. If a later package needs host-scope values that are not per-incarnation, that is
a deliberate schema addition (a new section kind with its own fixture), not a relaxation of
this rule.

## Cross-language conformance without a shared type

`fixtures/wire/*.bin` are canonical bytes and `*.json` their decoded typed values.
`fixtures/rates/rates.json` pins the arithmetic of the rate rules with the computation
written out in each case's `doc`. The contract binds **three** implementations: the Rust
encoder/decoder, the Rust rate derivation, and a standard-library-only Python reference
codec written from the layout, not from the Rust code.

`hostobs-proto` does **not** depend on `hostobs-model`. A path dependency changes
`Cargo.lock`, and the Rust lane image caches exactly the committed lock, so `cargo
--locked` refuses to build ("cannot update the lock file"). Rather than hand-edit the
lock (forbidden: the image's baked digest is the authority), the two crates are bound by
behavior: both assert their vocabulary against `fixtures/wire/quality.json`, and the
Python decoder asserts the same file. The cost of this choice is recorded as `HOB-3`
together with the decision point that would remove it (the next image rebuild).

The same constraint is why the tiny test-only JSON reader lives in `test-support/json.rs`
(outside the workspace members) instead of pulling `serde_json` in as a dev-dependency.

## Collecting the host: what was ported from the sampler, and what was changed

The `kernel` crate is the port of the topos sampler's collectors
(`topos-sampler-poc-20261105`, `topos/sampler/src/{collect,procs,util,fsafe,cmdline}.rs`),
under four decisions.

**One `Fs` trait, one root, no ambient filesystem.** Every reader takes a [`Fs`] and a root;
`RealFs` is the only implementation that touches the host and it offers exactly `read`,
`read_prefix`, `list` and `meta` — no write, create, remove, rename or spawn operation
exists to misuse, and `tools/check-lints.py` additionally refuses the write tokens in this
crate's sources. The sampler read paths directly, which is why its fixture tests had to
build real trees and why a path typo was a runtime surprise. Here the whole cgroupfs and
`/proc` fixture trees are committed files, so the tests are host-independent by
construction (this estate's rule that no test result may depend on host load).

**Bounds are arguments, not literals.** Every read takes a byte bound, every listing an
entry bound and every parse a line bound; `Bounds::POLICY` is the probe's policy value and
a test passes a tiny value to force the refusal. The sampler had `SLURP_MAX = 64 MiB`
inline and truncated a too-long directory listing; a truncated listing is a *wrong* answer
(the tasks past the bound look absent), so here an over-long listing is `not-sampled`, and
a file past its byte bound is refused rather than read into memory.

**A parse failure is a code, never a zero.** The sampler stored `0.0` with a state code
beside it, so a consumer that read only the value got a fabricated measurement; several
collectors also skipped a line they could not parse, which made the metric silently
disappear. Here each metric keeps its name and carries a quality code: an unparseable
value is `unreadable` for that metric only, one bad field of `io.stat` makes the whole
counter sum `unreadable` (a partial total is a wrong number that looks right), and a
missing optional file is `absent` or `unsupported` according to a code the caller declares
(`Absence::Controller` versus `Absence::Feature`). Nothing in a reader writes a zero for a
failure.

**Unknown device topology is a state, not a bottom device.** The sampler asked each device
whether its `/sys/dev/block/<dev>/slaves` listing was non-empty and reused the last answer
on failure, treating an unclassified device as bottom (`last.get(dev).copied().unwrap_or(true)`);
a failed read therefore summed a stacked device's counters into the cgroup aggregate, and
the aggregate double-counted. `cgroup::TopologySource` makes the answer an explicit
three-valued input and an `Unknown` device makes the aggregate
`QualityCode::UNKNOWN_TOPOLOGY` with **no** values at all. `HOB-20` records the fix, `HOB-21`
the still-missing `/sys` implementation (so every production `io.*` aggregate is
`unknown-topology` until the probe supplies one — an honest state rather than the bug
again).

**No path-keyed identity.** The cgroup key is `(device, inode)` and the process key is
`(boot id, pid, starttime ticks)` (`identity.rs`); a path is a label. `st_dev` stands in for
`statx`'s mount id because `unsafe` is forbidden here (`HOB-18`), and `CLK_TCK` is a
caller-supplied fact (`ClockTicks`) rather than an assumed 100.

**The sampler's scheduling was deliberately not ported.** Reading `/proc/<pid>/cmdline`
takes the target's `mmap` lock, so for a task stuck in `D` state the read blocks; the sampler
hid that behind a three-worker pool, a 64-slot queue, one in-flight read per pid and a 2 s
deadline. `kernel` owns no threads: it exposes a bounded, blocking read plus the privacy
contract (`cut_to_word` + `redact`, with the sampler's own test cases), and the caller that
wants a pool implements one. What *was* ported is the part that can leak a password: a long
line is cut at a word boundary so redaction never sees half a secret, and a vanished
process is a typed `Missing` rather than an empty string.

**Clock injection instead of the host clock.** `ManualClock` makes every assertion about a
wall jump, a monotonic interval or a reboot deterministic; `SystemClock` is the only
non-test clock and reads `Instant`/`SystemTime` without `unsafe`.

**A field is named by the kernel's own field number, and the index is derived.** `/proc`
files are positional: `/proc/<pid>/stat` has no names at all, and `statm`, `loadavg` and
the `cpu` rows of `/proc/stat` are the same shape. An index written beside a field number
is a second copy of the same fact, and the two copies can disagree — which is exactly what
happened: `parse_stat` declared `(9, field 12)` for `minflt` and then matched `9 => minflt`,
so it read field 12 (`majflt`) and field 13 (`cmajflt`), the process's *children's* faults
(`HOB-23`). The helper, the fixture and the SP-2 assertions had been written from the same
wrong table, so nothing went red. The rule the fix applies: a reader declares **field
numbers** and derives an index through one `STAT_FIELD_OFFSET` (field 3 is index 0 after
`comm`), the fixture and the test helper are generated from field numbers too, and one test
gives field `n` the value `n` so every assertion proves its own field (a slip of one is a
different number). Keyed files (`cpu.stat`, `memory.*`, `statm`'s sibling `status`, the
PSI files, the limits) cannot slip this way because the name travels with the value; their
tests now use distinct per-position values anyway.

**A read bound applies to bytes read, never to a reported length.** Refusing by `metadata.len()`
looks stricter and is wrong in both directions: a sysfs attribute reports one page (4096)
whatever its content, so a 20-byte attribute under a 256-byte policy cap was refused, while
a `/proc` pseudo-file reports 0 and still answers with text. The bound is therefore a
`take(max_bytes + 1)` on the read, with `saturating_add` so `usize::MAX` ("no bound") means
the whole file rather than a wrapped limit of 0 (`HOB-24`). A sum has the same rule: a total
that overflows is `unreadable`, never clamped at `u64::MAX` — `saturating_add` is for a
budget, `checked_add` for a reading (`HOB-25`).

**What SP-2 does *not* join (`HOB-26`).** The SP-2 tests compute a rate in a local helper
rather than through `model::derive_rate`, because `kernel` cannot depend on `model` while the
lane image pins a `Cargo.lock` that any path or dev dependency rewrites. The clock and
identity half of SP-2 is evidenced here against hand-computed arithmetic, and the classifier
half lives in `model` with `fixtures/rates/rates.json`; the two are joined by hand-checked
numbers and the shared quality fixture, not by a call. Stating that is part of the evidence,
not a footnote to it.

## Storing the history: one write module, four bounds, and a named tail

The `store` crate is where plan v4 §A.3/§A.4/§C.5 stop being intentions. Five decisions
carry the shape of it.

**One module performs every filesystem mutation.** `crates/store/src/fsio.rs` is the only
place in this crate (and, by extension, in the non-`act` part of the workspace) that opens
anything for writing, creates, truncates, renames, removes or lists a directory. Every other
module holds a `StoreDir` and can only call it. That is not a convention anyone has to
remember: `tools/check-lints.py` refuses the write tokens (`.write(true)`, `O_WRONLY`,
`O_RDWR`, `create_dir(`, `create_dir_all(`, `remove_dir(`, `remove_dir_all(`,
`Command::new`) in every Rust source outside `crates/act` and this one file, **tests
included**, which is why the test temp-directory helper is `fsio::testdir::TempDir` and
nothing else. `tools/check-lints-mutation.py` (a `lint` lane step) proves the guard bites: it
plants a `.write(true)` in `queue.rs`, a `create_dir(` in `tier.rs`, a `Command::new` in
`bins/probe`, a whole new `.rs` file containing a write, and a `#[mutants::skip]` inside
`fsio.rs`, and asserts each is reported — then asserts the baseline and `fsio.rs` are not.
A lint that cannot go red on a planted write is a lint that certifies nothing (plan v4 §A.3
asks for exactly this mutation check).

**Four failures, four typed answers, and a counter for each.** Plan v4 §C.5 names the four
ways a store can fail quietly, and none of them is allowed to be quiet here.

| failure | answer |
|---|---|
| disk fill | `StoreError::NoSpace` from `ENOSPC`, counted as a write failure; a submit that cannot be queued is a *reported* drop |
| RAM growth | `writer_queue_bytes`, `dict_active_max`, the query's own row/byte limits and the events ring's byte cap — all refusals, never a growing collection |
| torn file | the tail is *detected* at open: `TailState::Incomplete` is truncated back to the last good record and counted (`torn_repaired`), `TailState::Corrupt` is renamed `<name>.corrupt` and counted (`quarantined`) |
| silent loss | a drop is returned by `submit` and accumulated in `QueueStats`/`StoreStats`; loss and ``lossless()`` are opposites by construction, so an uncounted drop has no representation |

A corollary that cost a fix round: a segment may only be removed when it is *this* store's
file by both name **and** header kind. A right-looking name holding another corpus's
segment, a symbolic link, a subdirectory, and an unrelated file are all reported as notes and
left untouched (`tier::tests::a_segment_of_another_corpus_in_this_directory_is_reported_and_never_touched`).
A store that deleted a file it merely recognized would be one `rename` away from destroying a
boundary's worth of a neighbouring tier.

**A segment's payload kind is its corpus, not a parameter.** `TierKind` owns
`payload_kind()`, `record_kind()` and `frame_type()`; the header refuses to encode a payload
that disagrees with its kind (`TierKindMismatch`), a record's own envelope kind is checked
against the segment it lives in (`RecordCorpus`), and the reader refuses either mismatch
rather than decoding one corpus's bytes with the other's rules. `store` cannot call
`hostobs_proto` to do this (`HOB-27`: a path or dev dependency rewrites the locked
`Cargo.lock`, which the lane image bakes), so it carries its own four frame-header checks and
its own eight-entry quality table, the latter asserted against `fixtures/wire/quality.json`
by `crates/store/tests/quality_table.rs` — the same device `kernel` uses under `HOB-17`.

**Eviction is by whole segment, oldest first, and `next_seq` is a water mark.** A tier's
ring is ordered by `(start_s, seq)` and evicts from the front while the segment's entire span
is older than `keep_s`, then while the total bytes exceed `cap_bytes` — `keep_s` first, the
byte cap second, whichever bites first. Sequences only move forward: `seq_water` remembers
the highest sequence the directory has ever shown, so a quarantined or evicted `hot-0-0.seg`
is never reused and a restarted store cannot silently inherit a name whose bytes it did not
write. Re-opening a tier rescans the directory and raises the water mark from what is there.
The cap is strict: exactly at the cap keeps every segment (the bound is `<=`), one byte over
evicts, and a cap below a single segment's size drops the open one too — it is still the
oldest.

**A torn tail is a state, not an exception.** The reader returns `TailState`
(`Complete`/`Incomplete{offset}`/`Corrupt{offset, reason}`) with the good end, the footer
facts and the last good record index, so the caller decides: truncate and continue (an open
segment after a crash) or quarantine (bytes that are not a record at all). The seal is atomic
in the only order that survives a crash — write the whole segment to a temporary name, append
the footer, `fsync`, rename onto the final name, `fsync` the directory, unlink the open
name — and `docs/SEGMENT-FORMAT.md` specifies that order because the seal's correctness is a
property of the *sequence*, not of any single call. A test kills each step of it
(`M1c-5`: "the seal copies onto the final name, with no temp name and no rename" survives no
test).

**Reads are bounded and never panic.** `Tier::range` and `Store::range` skip whole segments
on their recorded `first`/`last` timestamps before reading any record, take a row bound and a
byte bound, and stop at either; a corrupt segment inside the window is a typed decoder error for
that segment, not an abort of the query. Nothing in the crate indexes a slice without a
checked length (`crc::checked_region_len` is the only arithmetic that can wrap, and it refuses)
and nothing panics on fixture-shaped corruption — the golden fixture suite decodes each file
and the Python reference decodes the same bytes independently.

## Gates and coverage

`lint` runs formatting, Clippy with `-D warnings`, rustdoc with `-D warnings`, cargo-deny
and the source lints; `selftest` runs nextest, doctests, llvm-cov and the per-crate
line/region floors from `coverage-floors.toml`. Both are offline and locked, in the
`tester-unified-rust` image, with Docker networking disabled.

`model`, `proto`, `kernel` and now `store` carry behavioral floors (97% line/96% region,
98%/96%, 92%/93% and 94%/93%): the first two were raised from the M0 bootstrap 90%/85% after
their own tests reached 98.06%/97.90% and 99.45%/97.88%, `kernel` after its own tests reached
92.19% line/93.50% region, and `store` after its own tests reached 94.87% line/93.90% region —
a crate whose floor starts at the bootstrap value is treated as scaffold, and one with real
tests raises its own floor instead of hiding code. The other five packages keep the bootstrap
floor because they are still the compilable scaffold; their identity test is not a
product-coverage claim. Floors are never lowered and code is never excluded from coverage.

Rust R2 (`cargo-mutants` judged by assay) is not adopted yet (R-3b in
[`assay/docs/RUST-METHODOLOGY.md`](../../assay/docs/RUST-METHODOLOGY.md)), so the test-quality
claim for this package rests on hand mutation campaigns: one mutation at a time, in the lane
image, each restored and digest-verified. The M1 campaign (twenty-one mutations, all killed,
the two first-pass survivors converted into tests) is in
[`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md); the M1 part-2 campaign (thirty-one mutations
covering the proto/Python refusals, the kernel collectors, the clocks, and the fix round's
`stat` field mapping, iocost overflow and unbounded read — all killed) is
[`HAND-MUTANTS-M1B.md`](HAND-MUTANTS-M1B.md), and the M1 part-3 campaign (fifteen mutations
over the store's bounds, eviction order, torn-tail handling, seal order, retirement, drop
accounting, foreign-file refusal and symlink refusal — all killed, one of them only after a
test that pinned the ring's oldest-first order) is [`HAND-MUTANTS-M1C.md`](HAND-MUTANTS-M1C.md).
A mutation that does not compile is reported
as `NOT-BUILT` rather than counted as a kill, because it proves nothing about the tests; a
patch whose stored fragment no longer matches the source is `NOT APPLIED`, which is also
not a survivor.

`cargo deny` keeps duplicate-version and external wildcard checks strict. Its single
`winnow@0.7.15` skip is tied to the imported sampler lock: `toml 0.9.8` and its
`toml_parser 1.1.5` resolve `winnow` 0.7.15 and 1.0.4, so the skip names only the older
exact version and becomes an unused-entry warning when that edge disappears. Workspace
packages are explicitly unpublished; their license metadata is deferred until a
publication decision, while registry dependencies remain license-checked.

Dependencies remain outside Git: the sampler's measured dependency floors are caret
constraints in the workspace manifest, `Cargo.lock` pins the resolved graph, and the
sibling tester image caches its `.crate` archives. Every lane is offline and locked, with
a precheck tying the mounted lock bytes and each registry archive to the image build; a
lane refuses before invoking Cargo when any of them disagree.
