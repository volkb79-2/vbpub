# hostobs — known issues, TODO, and backlog

IDs are `HOB-<n>`, allocated sequentially in this file; no other allocator exists.
Uniqueness was checked against `origin/main` and every worktree in this repository
before the first entry was filed (the only other `HOB-` spellings are the
`HOB-WS`/`HOB-FRONT`/`HOB-LEVELS` work-package names in
`docs/design/telemetry-platform/DESIGN-v2-2026-11-07.md`, which carry no number).

Normative behavior lives in [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../docs/design/hostobs/PLAN-v4-2026-11-08.md).
A `FIXED` entry means code, tests, and docs landed together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| HOB-1 | Counter wrap is never inferred: every backwards raw counter is a reset | Minor | OPEN (by design, awaiting a declared-width policy) |
| HOB-2 | SP-4 measured bytes only: encode/decode CPU, allocations and maintenance cost unmeasured | Minor | OPEN |
| HOB-3 | `model` and `proto` duplicate the quality/metric vocabularies because the locked image cannot take the path dependency; a shared fixture, not a shared type, binds them | Major | OPEN (needs one image build on the registry-set identity, then workspace-only lock edits need no further rebuild) |
| HOB-4 | No registered `conformance` lane: the independent Python fixture check is not gate-enforced | Major | OPEN |
| HOB-5 | Section version 1 has no history min/max/mean columns; `TIME` carries one value per bucket plus the aggregation code | Minor | OPEN (decided: WIRE.md §3.6/§4.9 D-W10, no layout change; lands with P5b `series_batch`) |
| HOB-6 | `DICT` frame type is defined but consumed nowhere: the dictionary travels inside the block frame | Minor | CLOSED (by design: reserved and unsent, WIRE.md §2.2 D-W6) |
| HOB-7 | `IncarnationRegistry` grows without bound and `end_round` is O(all ids ever minted) | Major | OPEN (SP-3 bound) |
| HOB-8 | `FrameHeader::parse` does not bound its declared length; only `decode_block` does | Major | FIXED (P0 bug fold-in) |
| HOB-9 | `derive_rate` carries no boot/epoch/incarnation context, so "no rate across a boundary" is a caller contract only | Major | OPEN (store package) |
| HOB-10 | The metric code tables (`unit`, `scope`, `source`, `aggregation`, kind mask) are duplicated in `model` and `proto` and only the quality table is fixture-bound | Major | OPEN |
| HOB-11 | SP-4's sampler arm is a reproduction with synthetic delta magnitudes, and the example never runs in a lane, so its numbers can drift unnoticed | Minor | OPEN |
| HOB-12 | Rust R2 is still `cargo-mutants`-less: hand mutants are the substitute, and only the fix round's patches are replayable | Minor | OPEN (R-3b) |
| HOB-13 | Small code-hygiene items found at review (indexing, `row + 1`, relabel validation, error names, `O(n×m)` retirement scan, `Unit::Ratio` for load average) | Minor | PARTLY FIXED, rest OPEN |
| HOB-14 | `decode_block` accepted a block shape under any frame type, and `encode_block` would emit one | Major | FIXED (M1b) |
| HOB-15 | The section directory's canonical order was checked on encode but not on decode | Major | FIXED (M1b) |
| HOB-16 | A truncated section directory reached the Python decoder's `struct.unpack` and raised `struct.error` instead of a `WireError` | Minor | FIXED (M1b) |
| HOB-17 | `kernel` carries its own `Reading`/`QualityCode` because a path dependency to `model` rewrites the locked `Cargo.lock`; a shared fixture, not a type, binds them | Major | OPEN (needs one image build on the registry-set identity, then workspace-only lock edits need no further rebuild) |
| HOB-18 | Cgroup identity uses `st_dev` rather than `statx`'s mount id, and `starttime` ticks need a caller-supplied `CLK_TCK` | Minor | OPEN (deliberate; see the entry) |
| HOB-19 | No package converts `kernel::Reading` into `model::Sample`, so that boundary has no test | Minor | OPEN (probe package) |
| HOB-20 | The sampler treated an unknown device topology as a bottom device, double-counting stacked I/O; `kernel` refuses instead | Major | FIXED in `kernel`, sampler worktree unchanged |
| HOB-21 | `kernel`'s `TopologySource` has no `/sys/dev/block/<dev>/slaves` implementation; the caller supplies one | Minor | OPEN (probe package) |
| HOB-22 | The kernel readers are exercised against fixture trees only: no lane runs them against a live host, and the sampler's worker-pool scheduling is not ported | Minor | OPEN (deliberate) |
| HOB-23 | `parse_stat` read `minflt`/`majflt` from proc(5) fields 12/13 instead of 10/12, and the test helper and fixture carried the same wrong table | Major | FIXED (M1b fix round) |
| HOB-24 | `RealFs::read` refused a file by its *reported* length (a sysfs attribute reports one page) and `max_bytes + 1` overflowed at `usize::MAX` | Minor | FIXED (M1b fix round) |
| HOB-25 | `io.stat`'s iocost sums used `saturating_add` (silently clamping at `u64::MAX`) where the counters use `checked_add` | Minor | FIXED (M1b fix round) |
| HOB-26 | The SP-2 rate arithmetic is a test-local helper, not `model::derive_rate`, because the crates cannot depend on each other | Minor | OPEN (documented limit) |
| HOB-27 | `store` cannot depend on `proto`/`model`: a path or dev dependency rewrites the locked `Cargo.lock`, so the crate re-implements four frame-header checks and carries its own quality table | Major | OPEN (needs one image build on the registry-set identity, then workspace-only lock edits need no further rebuild) |
| HOB-28 | A segment's records are found by scanning every record body, not from a footer index; a range query re-reads each candidate file from the start | Minor | OPEN (M2 query surface) |
| HOB-29 | The `require_tmpfs` check reads `/proc/self/mounts` and matches path prefixes; it does not ask `statfs` what filesystem the directory is actually on | Major | OPEN |
| HOB-30 | No session spools: a crashed probe's rate/span continuation (`§C.4`'s spool) has no store at all | Major | OPEN (out of this package's scope) |
| HOB-31 | `StoreDir`'s symlink refusal is a `symlink_metadata` check followed by a separately-named open, so a swap between the two is not prevented (`O_NOFOLLOW`/`openat` need `unsafe` or a dependency) | Minor | OPEN (needs `HOB-27`'s image rebuild or an `unsafe` decision) |
| HOB-32 | No fault injection: a partial `write`, a failed `fsync` or `ENOSPC` mid-seal is exercised only as the typed `no-space` mapping, not as a real short write | Minor | OPEN |
| HOB-33 | The single writer is a design property (one `Store` owns the queue), not something a test can observe: no lane starts two writers on one directory | Minor | OPEN (daemon package) |
| HOB-34 | Retirement only knows the incarnations this store's own segments name, so a consumer holding an incarnation outside the store (a spool, a reply in flight) cannot prevent `retire` from freeing it | Minor | OPEN (documented limit) |
| HOB-35 | The events ring is RAM-only: its displacement is counted but not persisted, so a consumer that restarts knows how many events it lost, not which | Minor | OPEN |
| HOB-36 | Events are keyed by `(issuer, key)` strings rather than dictionary incarnations, so an event's cgroup identity is a label a caller supplies, not an identity the store can validate | Minor | OPEN (probe package) |
| HOB-37 | Span abandonment is a caller call (`abandon_issuer`); nothing in the store ties it to an incarnation disappearing, so `inferred: true` closes only happen when the caller loops over them | Minor | OPEN (probe package) |
| HOB-38 | A full writer queue always drops the oldest job; there is no blocking or sample-shedding policy, which is a product choice to revisit with the probe's own loop | Minor | OPEN (probe package) |
| HOB-39 | A range answer is the stored payload bytes (a whole `proto` BLOCK) plus the record's time and incarnations, not decoded `model` values with quality codes: the join needs `proto`/`model`, which `HOB-27` forbids | Major | OPEN (needs the lanes on the registry-set-identity image, as `HOB-27`) |
| HOB-40 | No focus tier is configured by `plan_v4`; the tier machinery accepts one, but the plan does not yet say what a focus tier keeps | Minor | OPEN (plan decision) |
| HOB-41 | No golden fixture has a non-zero record pad, so the padding rule is pinned by unit tests on each side, not by a cross-language file | Minor | OPEN |
| HOB-42 | A `.seg` name is trusted as sealed without a footer, and the header's `seq`/`start_real_ns` are never compared with the name | Minor | FIXED (P0 bug fold-in) |
| HOB-43 | Quarantined `.corrupt` files (corrupt, oversized) are kept for an operator but are never counted in `bytes`, never evicted and never bounded in number, so repeated damage can fill the directory beyond `cap_bytes` | Major | FIXED (P0 bug fold-in) |
| HOB-44 | CLOSED — `tester-unified-rust` carries no `pip`/`ensurepip`/`python3-venv`, so the original `kind = "assay"` `assay-r1` lane died at Run-Gate's editable install before judging. Fixed by re-declaring the lane as a `kind = "command"` lane that runs the judge from the worktree's own `assay/src` (`PYTHONPATH`, no install); the image is unchanged. Proven through Run-Gate: PASS with the measured totals in the lane log, and FAIL/UNCOVERED_LINES when one kernel test file is deleted | Major | CLOSED (2026-11-16) |
| HOB-45 | Rust R2/R3 remain unregistered for hostobs: no `assay-r2` (cargo-mutants ingestion) or `assay-r3` (canary) lane exists, so test quality rests on the hand-mutation campaigns | Major | OPEN (assay R-3b/R-4) |
| HOB-46 | A block without an in-frame dictionary could not be validated: `decode_block_with(bytes, held)` now judges a bare block's generation, references, column kinds and bucket aggregations against the held dictionary (the stream-state `generation-regressed` rule stays with HOB-54) | Major | FIXED (P2a1) |
| HOB-47 | A history bucket's aggregation is now checked against every column metric's permitted mask in both decoders (`aggregation-not-permitted`) | Minor | FIXED (P2a1) |
| HOB-48 | A history block names its subject in kind 6 `subject` (minor 1, `PROTO_MINOR = 1`, `subject-without-time`), and the time length is `align8(15·n)` (the P0 bug fold-in's half) | Major | FIXED (P2a1; time-length half P0) |
| HOB-49 | `hostobs_wire_ref.py` cites a non-existent `docs/WIRE-COLUMN-CODEC.md` instead of `docs/WIRE.md` | Minor | OPEN |
| HOB-50 | `SECTIONED` is refused on every frame type but BLOCK (`unknown-frame-flags` locally), and the directory byte count is proven present before the directory vector is reserved | Minor | FIXED (P2a1) |
| HOB-51 | Received frames are held to the alignment and header-first rules: every section `len` is a multiple of 8 (`section-length`) and an unknown kind at directory entry 0 is `section-order` at index 0, after the rank check | Minor | FIXED (P2a1) |
| HOB-52 | A repeated dictionary identity is `duplicate-dictionary-entry` in Rust whatever the other fields hold, as in the Python reference | Minor | FIXED (P2a1) |
| HOB-53 | The Python reference follows the WIRE.md §3.10 precedence: `not-a-block` before the buffer length and `SECTIONED` checks | Minor | FIXED (P2a1) |
| HOB-54 | The send hub, stream dictionaries, envelope checks and the Python/Rust clients do not exist yet; `FrameReader`, `ModeDetect` and `ConnTimers` exist | Major | OPEN (P2: `FrameReader`, `ModeDetect`, `ConnTimers` exist; `SendHub`, `StreamDicts`, `envelope.rs` do not) |
| HOB-55 | A look-alike name such as `hot-0-18446744073709551615.seg` (or `.seg.corrupt`) sets the tier's sequence water mark to `u64::MAX` before its owner is proven, so every `open_segment` fails with `LengthOverflow` until the file is removed | Minor | FIXED (P0b) |
| HOB-56 | `StoreStats::bytes` maintained by incremental arithmetic drifts from the tiers' own byte totals when a file vanishes or a new segment's header is written | Minor | FIXED (P0b) |
| HOB-57 | `docs/hand-mutants/m1d-mutants.py` had no per-mutant subprocess timeout, so a mutant that hangs the suite hangs the campaign | Minor | FIXED (P0b) |
| HOB-58 | The reports of an eviction that dropped an already-gone or unremovable entry reach only `TierScan::notes`, so the caller of `append`/`evict` never sees them and a rescan resets them | Minor | FIXED (P0b) |
| HOB-59 | `Tier::append` and `Store::evict` drop the partial `Evicted` (counts and notes) when an eviction fails after a partial pass | Minor | OPEN |
| HOB-60 | `Store::notes` visibility is decided by name, not by directory, and the store's eviction-notes ring may be redundant with the tiers' `scan.notes` | Minor | OPEN |

---

## HOB-23 — `minflt`/`majflt` read from the wrong proc(5) fields

**Where:** `crates/kernel/src/identity.rs` (`parse_stat`, `StatLine`), `crates/kernel/tests/fixtures/proc/1/stat`, `crates/kernel/tests/sp2_evidence.rs`.

The reader derived the index correctly (index `i` after `)` is field `i + 3`) and then assigned
`9 => minflt`, `10 => majflt`. Index 9 is field 12 (`majflt`) and index 10 is field 13
(`cmajflt`); the process's own minor faults are field 10 (index 7). A consumer therefore read
the process's major faults as its minor faults and its children's major faults as its major
faults — two wrong numbers that look like measurements. The ported sampler said so explicitly
(`topos/sampler/src/procs.rs:312`, "majflt = field 12"), and the wrong table was reproduced in
the unit-test helper's comments, the committed fixture and the SP-2 assertions, so the tests
pinned the defect rather than catching it.

The fix names fields, not indexes: `parse_stat` reads `10, 12, 14, 15, 20, 22, 24, 42` and
derives every index through `field_index(field)`/`STAT_FIELD_OFFSET` (proc(5) field 3 is index
0), fields 11 (`cminflt`) and 13 (`cmajflt`) are deliberately not read and the doc comments say
so, the fixture and the helper are generated from field numbers, and
`every_stat_field_this_reader_reads_arrives_at_its_proc5_field_number` builds a line where
field `n` carries the value `n`, so every assertion proves its own field. `statm`, `status`,
`loadavg`, `/proc/stat`, `meminfo`, `vmstat`, the PSI files, `io.stat`, `cpu.stat`,
`cpu.max`/`weight`, `memory.stat`/`events` and the limit files were re-audited the same way
(the result is in the fix-round report): the rest are `key value` or `key=value` files whose
field names travel with the value, so a position cannot slip silently, and their tests now use
distinct per-position values as well.

## HOB-24 — A read refused by reported length, and an overflow in the bound

**Where:** `crates/kernel/src/fs.rs` (`RealFs::read`).

The reader checked `metadata.len()` first and refused a file larger than the bound without
reading it. That is a *reported* length, not the fact: a sysfs attribute reports one page
(4096 bytes) whatever its content, so a 20-byte attribute under a 256-byte policy cap was
refused as too large. The same statement computed `max_bytes + 1` unsaturating, so a caller
passing `usize::MAX` (“no bound”) wrapped the read limit to 0 — an empty `Ok`, exactly the
wrong answer that looks like an empty file — or panicked in a debug build.

The fix applies the bound to the bytes actually read (`take(max_bytes + 1)`, then one byte
past the allowance is `TooLarge`) and uses `saturating_add`. `fs::tests::a_file_is_read_within_its_bound_and_a_larger_one_is_refused_unread`
now reads a fixture file with `usize::MAX` and asserts the whole content, and the hand-mutants
campaign replays the wrapping version (`D3`).

## HOB-25 — iocost sums clamped instead of refusing an overflow

**Where:** `crates/kernel/src/cgroup.rs` (`parse_io_stat`).

The device counters summed with `checked_add` and set `bad` on overflow, but the three iocost
sums used `saturating_add`, so an overflowing `cost.usage` was published as `u64::MAX` — a
number the kernel never wrote. Both now use `checked_add` and the same `unreadable` outcome;
`an_overflowing_io_sum_is_unreadable_instead_of_clamped` pins both paths (a counter overflow
and an iocost overflow) and the mutant `D2` replays the clamping.

## HOB-26 — SP-2's rate arithmetic is a test-local helper

**Where:** `crates/kernel/tests/sp2_evidence.rs` (`rate`), `docs/DESIGN-GUIDE.md`.

The SP-2 tests compute a rate as `delta / (mono_ns / 1e9)` in a local helper. They cannot call
`model::derive_rate`: `kernel` has no dependency on `model` (and cannot gain one while the lane
image's `Cargo.lock` is pinned, `HOB-17`), and a dev-dependency would rewrite the lock. So SP-2's
identity and clock claims are evidenced against the kernel's own types and hand-computed
arithmetic, while `model`'s classifier keeps its own `fixtures/rates/rates.json` cases; the two
sides are not joined by a test. The limit is stated in the SP-2 record rather than papered over.

**Exit criteria.** A package that owns both crates (the probe, or a reborn image with a new lock)
derives a rate from a `kernel::Reading` pair through `model::derive_rate` and asserts that the
hand-computed SP-2 values agree with it.

---

## HOB-14 — A block shape decoded under any frame type

**Where:** `crates/proto/src/frame.rs` (`encode_block`, `decode_block`), `python/reference/hostobs_wire_ref.py`.

`decode_block` read the section directory and the bodies without ever checking the frame type it
had just parsed, so a `REQ`, `DICT` or `END` frame carrying a block's sections decoded as a block.
`encode_block` had the mirror defect: it would write a block's sections under a caller-supplied
type. A consumer that dispatches on `FrameType` and then decodes anything with sections would
therefore read a control frame's bytes as measurements. Found by the luna cross-model spot review
(`reports/luna-hostobs-m1-spotreview-REPORT.md`, finding 1).

**Fix (M1b).** `ProtoError::NotABlock { frame_type }` (kind `not-a-block`) on the decode path and
`canonical_rank`-guarded refusal on the encode path; the Python decoder refuses the same case with the
same kind. Fixture `block-as-req` (a valid block with byte 4 patched to `REQ`) pins both, and a Rust
test patches byte 4 to each of the other four types.

## HOB-15 — Section order was enforced on encode only

**Where:** `crates/proto/src/frame.rs` (`check_canonical_order`), `python/reference/hostobs_wire_ref.py`.

Plan v4 §B.3 fixes the section order (header, dictionary, rows-or-time, columns) and the encoder
enforced it, but `decode_block` accepted any directory order: a sender that wrote dictionary after
rows produced a block whose re-encode differed from the bytes it came from, so the "decode then
re-encode is byte-identical" property the fixtures assert was one-sided. Found by the same luna review
(finding 2).

**Fix (M1b).** `ProtoError::SectionOrder { index, kind }` (kind `section-order`) with
`canonical_rank()`; the decoder checks the directory before reading any body and the Python decoder
matches. Fixture `section-order` reorders entries and bodies together, and a Rust test hand-builds a
swapped directory that no encoder would produce.

## HOB-16 — A truncated directory was a `struct.error` in the Python decoder

**Where:** `python/reference/hostobs_wire_ref.py` (`decode_block`).

The Python reference decoder unpacked each directory entry with `struct.unpack_from` after only
checking that the *count* fit, not that each entry did, so a frame whose declared section count
exceeded its bytes raised `struct.error` — a crash, not the `WireError` the Rust decoder returns
(`truncated`). Two decoders that disagree about the *kind* of a refusal are two decoders that do not
bind one contract. Found by the same luna review (finding 3).

**Fix (M1b).** The directory read is bounded before unpacking, with context `block directory`, and
the expected/observed lengths (`need`/`have`) travel in the error. Fixture `truncated-directory` (16
bytes declaring one section, no entry) pins it, and a Python test asserts the refusal for declared
counts 1..4 and that no `struct.error` escapes.

## HOB-17 — `kernel` carries its own value and quality types

**Where:** `crates/kernel/src/{value,quality}.rs`; `crates/kernel/Cargo.toml`.

Plan v4 §A.3 says the collectors produce `model` values. A path dependency
`hostobs-model = { path = "../model" }` — even as a *dev*-dependency — makes Cargo rewrite
`Cargo.lock` (the kernel package gains a `dependencies` entry), and the Rust lane image bakes the
whole-lock digest (`/usr/local/share/hostobs-Cargo.lock.sha256`, the identity of the image
currently deployed as `tester-unified-rust:local`), so `cargo --locked` then fails with
"cannot update the lock file". Hand-editing the lock is forbidden, and the deployed image needs a
rebuild. Measured in this package in a throwaway container: adding the dev-dependency rewrote the
lock; removing it restored the digest. The image source now bakes a registry-set digest instead
(`/usr/local/share/hostobs-Cargo.lock.registry-sha256`, which ignores workspace members), but no
image carrying it exists until a NEW tag is built on a host; until `run-gate.root.toml` points the
Rust lanes at that tag the paragraph above is still the truth for the lanes.

The crate is therefore dependency-free and carries a three-variant `Value` and a `QualityCode`
newtype. They are not parallel vocabularies by accident: `crates/kernel/tests/quality_table.rs`
asserts the table byte-for-byte against `fixtures/wire/quality.json`, the same fixture `model`,
`proto` and the Python decoder assert, so a renumbering fails in four places.

**Exit criteria.** The first lane run on the registry-set-identity image that can carry a new lock (a workspace-only lock edit needs no further rebuild): make `kernel` depend on
`model`, delete `crates/kernel/src/{value,quality}.rs`, convert `Reading` into `model::Sample` at
that boundary, and delete the local table assertion (the shared fixture stays).

## HOB-18 — `st_dev` where the plan says mount id, and a caller-supplied tick rate

**Where:** `crates/kernel/src/identity.rs` (`CgroupIdentity`, `ClockTicks`).

Plan v4 §C.1 keys a cgroup `(mount id, cgroup inode)`. `stx_mnt_id` is only reachable through
`statx`, i.e. a syscall, which without `unsafe` this crate cannot make (and `#![forbid(unsafe_code)]`
is in force). `Meta` therefore carries `st_dev`, the filesystem instance's id. For cgroupfs that
keeps the rule's purpose: a device id does not change while a cgroup lives, and it separates an inode
number reused on another filesystem. The residual difference is two *separate mounts* of the same
filesystem instance sharing `st_dev`; that matters only when two cgroupfs mounts are visible to one
probe at once, which no consumer does today.

`CLK_TCK` (`sysconf(_SC_CLK_TCK)`, normally 100) is the other host fact this crate does not invent:
`ClockTicks` carries it, the caller supplies it, and `ClockTicks::unknown()` makes every tick-to-ns
conversion a stated absence instead of a guess at 100.

**Exit criteria.** Either a `statx` wrapper confined to one small module under the plan's `unsafe`
allowlist (with the use-case that needs it: two cgroup mounts), or a package that supplies both
inputs from `/proc/self/mountinfo` and a read `CLK_TCK`, with a test that the mismatched-mount case is
distinguished.

## HOB-19 — No package converts a kernel `Reading` into a model `Sample`

**Where:** `crates/kernel/src/value.rs`, `crates/model/src/value.rs`.

The two types are structurally equivalent (a three-variant value plus a closed quality code) and no
code joins them yet: `bins/probe` is still the M0 scaffold. The conversion is therefore untested
rather than wrong, and HOB-17 records why a test cannot simply depend on both crate types today.

**Exit criteria.** The probe (or any package) performs the conversion, with a test asserting every
`Value` variant and every quality code round-trips, including that a `missing` reading never becomes
a measured `Sample`.

## HOB-20 — Unknown device topology counted as a bottom device in the sampler

**Where:** the sampler's `topos/sampler/src/collect.rs::is_bottom`
(`last.get(dev).copied().unwrap_or(true)`); `crates/kernel/src/cgroup.rs`.

A device the sampler had never classified (its `/sys/dev/block/<dev>/slaves` read failed, or the device
was new this tick) was treated as a **bottom** device, so a stacked device's counters were summed into
the cgroup's `io.*` aggregate and the aggregate double-counted. The plan's §A.5 fix makes "unknown"
a state. `kernel` implements it: the caller supplies a `TopologySource`, an `Unknown` answer makes the
whole aggregate `QualityCode::UNKNOWN_TOPOLOGY` with **no** values (never a partial total), and
`crates/kernel/tests/cgroup_fixtures.rs` pins bottom/stacked/unknown individually and mixed.

The sampler worktree is not edited by this package (it is a read-only behavioral source), so the bug
survives there; nothing consumes the sampler any more.

## HOB-21 — No `/sys` topology source

**Where:** `crates/kernel/src/cgroup.rs` (`TopologySource`, `UnknownTopology`, `FixedTopology`).

The trait is the seam, not the implementation: `UnknownTopology` and `FixedTopology` exist for tests
and for a caller without `/sys` access, and no reader in this crate reads
`/sys/dev/block/<dev>/slaves`. Until one does, every production `io.*` aggregate is
`unknown-topology` — an honest state, and the reason HOB-20's fix cannot silently regress.

**Exit criteria.** A `SysTopology` implementation that reads `slaves` (and `/sys/dev/block/<dev>/`
attributes for the device's own kind), with a fixture `/sys` tree and a test that a dm device and its
underlying disk are counted once.

## HOB-22 — The readers are exercised against fixture trees only

**Where:** `crates/kernel/tests/fixtures/` (cgroupfs and proc), `crates/kernel/src/cmdline.rs`.

Every reader runs against committed fixture trees, which is what makes the suite host-independent
(and is the rule this estate requires). What no lane does is run them against a **live** host, so a
kernel that formats a file differently from the fixture would be found only by a consumer. Plan §F.3
and SP-3 want a live acceptance probe with declared bounds; that is the probe package's job.

The sampler's scheduling around `/proc/<pid>/cmdline` (a three-worker pool, a 64-slot queue, one read
in flight per pid, a 2 s deadline) is deliberately **not** ported: `kernel` owns no threads, and a
caller that wants that shape implements it. The privacy contract *is* ported
(`cut_to_word` + `redact`, with the sampler's own test cases), so the part that can leak a password
is the part that came across.

---

## HOB-7 — The incarnation registry grows without bound

**Where:** `crates/model/src/identity.rs` (`IncarnationRegistry`).

`entries` is a `Vec<Option<Entry>>` indexed by id so `snapshot`/`is_active` are O(1), but a
retired slot stays as `None` for the life of the process and `end_round` walks every slot
ever created. Ids are never reused, which is the correctness property; the cost is size and
time. At the plan's 10 cgroup churn per second that is roughly 860k slots per day, and
`end_round` is O(all ids ever minted) once per scan round. Plan §A.4/SP-3 requires a bound
on every structure that grows with churn.

**Fix shape.** Key live entries by id in a `BTreeMap<Incarnation, Entry>` (or a `HashMap`
plus a dense "active id" list for iteration) so retirement actually frees the slot, and keep
a compact reverse index for `incarnation_of`. `end_round` then walks live entries only. The
observable behaviour must not change: ids stay monotonic, a retired key re-minted gets a
fresh id, two absent rounds retire, double `end_round` is a no-op — all covered by existing
tests.

## HOB-8 — `FrameHeader::parse` does not bound the declared length

**Where:** `crates/proto/src/frame.rs` (`FrameHeader::parse`).

`decode_block` refuses a frame whose declared length exceeds [`MAX_FRAME_BYTES`] *before* it
uses that number, and every later allocation is bounded by the verified frame. `parse` alone
does not: it reads the twelve header bytes and hands the caller the declared length
unchecked. The streaming reader of the next package starts with `parse` and must allocate
`len` bytes, so the safe path today is only the one `decode_block` happens to take.

**Fix shape.** Validate `FRAME_HEADER_LEN <= len <= MAX_FRAME_BYTES` inside `parse` (and keep
`decode_block`'s own check, so a future caller that bypasses `parse` is still bounded), plus
a test at the bound and one past it. No wire change.

**FIXED (P0 bug fold-in).** `parse` now refuses a declared length above `MAX_FRAME_BYTES`
(`oversized-frame`) and below `FRAME_HEADER_LEN` (`truncated`, context "frame length shorter
than the header"), after the fixed-field checks, so Rust and the Python reference name the same
first refusal. `decode_block` keeps its own check as a second bound; through `parse` that check
can no longer fire, so its multi-line `return Err(..)` is covered by no test (see `CHANGES.md`). Tests pin the
bound, one past it, `u32::MAX`, one below the header, zero and the refusal order, in both
languages, and two refusal fixtures (declared length 4, a header-only block) are decoded by
both. Stored hand-mutation patches, not yet run: `M1d-1`, `M1d-14`.

## HOB-9 — `derive_rate` has no boot/epoch/incarnation context

**Where:** `crates/model/src/rate.rs`.

Plan §C says no rate may cross a reset, a boot, an epoch change or an incarnation change.
Inside `derive_rate` the two readings are bare `(Sample, MonoTime)` pairs, so that rule is
the *caller's* contract: the function cannot see a boot id, an epoch or an `inc`. A caller
that pairs readings from two different incarnations gets a plausible, wrong rate.

Related and worth stating together: a sample stored with `Quality::Reset` keeps its raw
bytes, but `Sample::value()` returns `None` for it, so "the current reading is the new
baseline" only works if the caller keeps that reading as an `Ok` sample.

**Fix shape.** A `Baseline { boot, epoch, inc, sample, at }` guard in the store/probe
package (the next package owns the segment), checked before `derive_rate` is called, so a
cross-boundary pair is refused rather than averaged. Document the caller contract in
`docs/DESIGN-GUIDE.md` until the guard exists.

## HOB-10 — The metric code tables are duplicated and only quality is fixture-bound

**Where:** `crates/model/src/metric.rs` (`Unit`, `Scope`, `Source`, `Aggregation`,
`Metric::aggregation_mask`) and `crates/proto/src/dict.rs` (the same closed sets as wire
codes).

The quality vocabulary is bound across the two crates (and the Python decoder) by
`fixtures/wire/quality.json`. The metric tables are not: a unit code added in `proto` and not
in `model` (or a renamed scope) drifts silently, caught only if the corresponding crate's
own tests happen to name the value. This is the same root cause as HOB-3 (no dependency edge
possible under the locked image) but a different consequence: HOB-3 is about the quality
table, this is about every other code table.

**Fix shape.** A `fixtures/wire/metric-vocabulary.json` listing every `unit`/`scope`/
`source`/`aggregation` name and its wire code plus the per-kind mask, asserted by
`model`, `proto` and the Python decoder — the same three-way binding the quality fixture
already uses. Then a rename in one language is a red test in all three.

## HOB-11 — SP-4's baseline arm is a reproduction, and nothing runs the example

**Where:** `docs/SP-4-WIRE-SIZES.md`, `crates/proto/examples/sp4_wire_size.rs`.

Two caveats the document states, and one it should enforce:

- The sampler byte counts come from a faithful re-implementation of the sampler's varint
  encoding written inside the example, **not** from running the sampler. That is stated in
  the doc; it means the sampler arm is a reproduction, not a measurement of shipped code.
- The sampler's per-tick *delta* magnitudes are synthetic, so its varint size depends on
  invented values. The raw-counter line is the robust like-for-like comparison, and the D4
  verdict rests on that line. This is stated too.
- The example is compiled by `clippy` but **never executed in a lane**, so the published
  numbers can drift silently if either encoding changes.

**Fix shape.** Either a `selftest`-visible assertion on the deterministic column-side byte
counts at 100 and 1,000 cgroups (which is a pure function of the format), or a dedicated
step in a lane that runs the example and compares its output with a committed snapshot. The
CPU/allocation half stays in HOB-2.

## HOB-12 — Hand mutants are recorded as prose, not as replayable patches

**Where:** `docs/HAND-MUTANTS-M1.md`.

The M1 campaign (21 mutations, all killed) is recorded as a table of labels and verdicts
plus SHA-256 restore checks. That is honest evidence of *what* was done, but the exact
mutation text is not stored, so nothing can replay or re-verify it: a later reader has to
trust the prose, and a shifted line number makes a manual replay ambiguous. Rust R2
(`cargo-mutants`, R-3b) remains the real mechanism; this is its substitute until then.

**Progress in the fix round.** The fix round's 15 mutations (Rust and Python, both wiring
directions) are stored as a replayable script rather than prose — see
`docs/hand-mutants/fix-round-mutants.py` and the table in `docs/HAND-MUTANTS-M1.md`. The
M1 campaign's own patches are still prose; converting them is the remaining work.

## HOB-13 — Small code-hygiene items from review

**Status: partly fixed in the fix round.** Items and their state:

| Item | State |
|---|---|
| `&bytes[FRAME_HEADER_LEN..]` in `decode_block` (safe, but an index) | **FIXED** — replaced by `.get(..)` on the same call |
| `Column::sample` does `need: row + 1` (can overflow at `usize::MAX`) | **FIXED** — uses `checked_add` |
| `intern`'s relabel path skips `validate` | OPEN (the encoder still refuses an over-long label, so no bad bytes escape) |
| The relabel/duplicate refusal reused `ConflictingColumn { metric: index }` | **FIXED** — the wire path now has `DuplicateDictionaryEntry { index }`; the live-table relabel conflict keeps `ConflictingColumn` because it is a different condition |
| `retire_unreachable` does `reachable.contains` inside a loop (O(n×m)) | OPEN (bounded by 65,536 entries today) |
| `forbid(unsafe_code)` only via `[lints] workspace = true` | OPEN (belt-and-braces `#![forbid(unsafe_code)]` per crate) |
| `host.load1/5/15` declared with `Unit::Ratio` whose symbol is `%` | OPEN (a load average is not a percentage; needs a unit that says "count per core" without inventing one) |
| `sampler_baseline()` is a subset of the sampler's metrics | OPEN (M1 baseline; disk/procs/per-device lines are later packages) |
| `BootIdLength.len` counts bytes, not characters | OPEN (documented in the variant; the id is ASCII by definition) |
| `SampleRow.value` is a public field, so a placeholder can be read without `measured()` | OPEN (documented hazard; the golden fixture pins the absent case) |

---

## HOB-1 — Counter wrap is never inferred

**Where:** `crates/model/src/rate.rs` (`derive_rate`), crate docs decision 1.

**What.** A raw `u64` counter that decreased is classified `NoRate::CounterReset`
unconditionally. A genuine hardware/kernel wrap (for example a 32-bit device counter
crossing its maximum) is therefore reported as a reset: the current reading becomes the
baseline and no delta is produced.

**Why it is open rather than a bug.** Plan v4 §C.3 forbids invented readings. A wrap
cannot be distinguished from a reset without a *declared* counter width and a
plausibility bound (a decrease of at most `width - 1`, at most once per interval). Any
guess is a silently wrong rate, which is exactly what §C.3 and estate doctrine
("defaults and fallbacks are hazards") forbid. The classifier is the single place such a
policy would be added: one extra parameter per metric in the registry plus one branch
here, and the golden `fixtures/rates/rates.json` cases would grow a declared-width
section.

**Exit criteria.** A metric registry field (`counter_width`), a wrap branch with its own
`Quality`/`NoRate` outcome, fixture cases for "wrapped exactly once", "decreased by more
than the width", and "decreased twice in one interval", and a doc decision that replaces
crate docs decision 1.

## HOB-2 — SP-4 measured bytes only

**Where:** [`docs/SP-4-WIRE-SIZES.md`](docs/SP-4-WIRE-SIZES.md),
`crates/proto/examples/sp4_wire_size.rs`.

SP-4 settled **D4** (fixed-width typed columns) on wire size: 82.7/80.2 B per row
against the sampler TLV's 50.1/50.9 B (per-tick deltas) and 62.1/62.9 B (raw counters,
like for like). CPU time, allocation counts and the maintenance cost of owning a codec
were **not** measured: the Rust methodology defers benches with a regression gate to a
later package, and maintenance cost is a review judgement.

**Exit criteria.** A bench with a regression gate (not a wall-clock assertion; see the
estate rule that a test result may not depend on host load) for encode and decode on the
100- and 1,000-cgroup shapes, plus a written maintenance-cost judgement at review.

## HOB-3 — The two vocabularies are duplicated, not shared

**Where:** `crates/model/src/quality.rs`, `crates/proto/src/quality.rs`, and the
dictionary field codes in `crates/proto/src/dict.rs`.

`hostobs-model` holds the in-memory value vocabulary; `hostobs-proto` holds the same
closed table for wire codes. `hostobs-proto` deliberately does **not** depend on
`hostobs-model`: adding a path dependency changes `Cargo.lock`, and the Rust lane image
deployed as `tester-unified-rust:local` caches exactly the committed lock and bakes its
whole-file digest, so `cargo --locked` refuses to build (`cannot update
the lock file`). The two tables are therefore bound by *behavior*, not by a type: both
assert themselves against the committed fixture `fixtures/wire/quality.json`, and the
independent Python decoder asserts the same file. A divergence in either crate is a red
lane.

**Why it is filed.** The binding is real but expensive to keep: a new quality code must be
added in three places (two crates and the fixture), and a rename in one crate is caught
only if that crate's test runs. It is a configuration fact of the current image, not a
design preference.

**Exit criteria.** Decide the shared-type shape (a dependency edge, or a third
`-vocab` crate both depend on) once the Rust lanes run on an image built on the registry-set
identity (a workspace-only `Cargo.lock` edit then needs no rebuild), and record it as a decision in
`hostobs/docs/DESIGN-GUIDE.md`.

## HOB-4 — No registered `conformance` lane

**Where:** `run-gate.toml` declares `lint` and `selftest` only.

Plan v4 §B.5 and the Rust methodology both name a `conformance` lane for the Python
reference decoder. The Python side exists and passes (`python3 -m unittest discover -s
python/tests`, nine cases, standard library only), and the Rust side asserts the same
fixture files in `crates/proto/tests/golden_fixtures.rs`. What is missing is a *registered*
lane: the Python run has no declared environment, so it is not gate evidence and cannot
fail a merge by itself. The cross-language claim currently rests on two independent test
suites that happen to read the same files, not on one executed lane.

**Exit notes from this package.** The lane was not added here because the brief scoped
this slice to `model` and `proto` and any lane's environment/argv must be reviewed as
gate change. The work is small: `tester-unified` (not `-rust`) with
`python3 -m unittest discover -s python/tests`, no network, plus the fixture
regeneration check (`python3 python/reference/make_fixtures.py <tmpdir>` compared with
the committed bytes).

## HOB-5 — No history min/max/mean columns

**Where:** `crates/proto/src/frame.rs` `TIMESEC`.

Plan v4 §B.2 says history buckets that aggregate gauges carry `min/max/mean` columns
(sample-weighted). Version 1 of this codec carries one value per bucket, the covered
duration, the sample count and the aggregation code — enough to state "this bucket holds
the mean over 5 samples", not enough to carry the other two aggregates. This is a
deliberate M1 scope cut: the live path (which M1 needs) carries raw values and quality,
and history is an M2 query surface.

**Exit criteria.** Either three extra columns per aggregated gauge metric (a per-bucket
value triple) or a second bucket section kind, decided with M2's history query shape, and
a fixture case.

**Decided (WIRE.md §3.6, §4.9, D-W10).** Neither: min, max and mean of a gauge are separate
history blocks of the same subject, one aggregation per block, packed subject-major then by
the request's agg order; the existing bucket `agg` code says which. No layout change. Closes
when P5b's `series_batch` serves it and the P2 fixture `history-agg-not-permitted` plus a
three-agg packing test pass.

## HOB-6 — `DICT` frame type is defined and unused

**Where:** `crates/proto/src/frame.rs` `FrameType` (type code 4), crate docs decision 2.

`FrameType::Dict` (type code 4) is in the closed vocabulary, but nothing produces or consumes it: the
dictionary travels inside the block frame (section kind 5) so an exported segment is
self-contained and each fixture case is one file. A later package may split it into a
leading `DICT` frame without a wire change, which is why the type stays defined rather
than deleted.

**Exit criteria.** Either the probe emits a standalone `DICT` frame before a first block
of a generation (and the fixtures grow that case), or the type is retired with a note in
the crate docs.

**CLOSED (by design, WIRE.md §2.2, D-W6).** The type code stays reserved and is never sent in
`hostobs/1`; a peer that receives one treats it as `bad-frame`. The in-block dictionary
serves live, query and segment paths, so no producer needs it. P2's stream layer refuses it.

## HOB-27 — `store` cannot depend on `proto` or `model`

**Where:** `crates/store/Cargo.toml`, `crates/store/src/{format,quality}.rs`, `docs/SEGMENT-FORMAT.md`.

The documented option was a path or dev dependency on `hostobs-proto` (for the frame header) and
`hostobs-model` (for the quality vocabulary). It was tried and refused: a path dependency makes
`cargo --locked` want to rewrite `Cargo.lock`, and the Rust lane image bakes one lock's crate
closure, so the lane refuses before building (`HOB-17` has the same shape for `kernel`). The
store therefore carries its own four frame-header checks (`format::check_payload` plus the
header checks in `format`) and its own eight-entry quality table (`quality::QualityCode`), and
the two are bound to shared fixtures rather than to shared types: a header fact is asserted
against the real `fixtures/wire/*.bin` payloads in `crates/store/tests/golden_segment.rs`, and
the quality table against `fixtures/wire/quality.json` in `crates/store/tests/quality_table.rs`.

**Exit criteria.** The Rust lanes running on an image built on the registry-set identity (which
admits a lock whose only change is the workspace's own path dependencies), after which `store` calls `proto`'s `FrameHeader::parse` and `model`'s `Quality` and the two
duplicated modules are deleted (their tests move with them). Until then every change to the
frame header or the quality vocabulary has to be made in two places, and the fixture tests are
what force that.

## HOB-28 — A range query scans whole records, not a footer index

**Where:** `crates/store/src/segment.rs` (`scan_bytes`), `crates/store/src/tier.rs` (`range`).

A sealed segment's footer states its record count but carries no per-record index, so
`Tier::range` reads each candidate file from its header to its footer and filters by each
record's recorded `real_ns`. Whole-segment skipping by the ring's `first`/`last` timestamps
keeps an unrelated segment from being opened at all, and both a row bound and a byte bound stop
the walk, so a query's *answer* is bounded — its *work* is proportional to the records in the
window's segments. The footer has room for a small index (`docs/SEGMENT-FORMAT.md` reserves the
body for exactly this), so this is a deliberate M1 cut rather than a missing guard.

**Exit criteria.** A footer record index (or a per-record time summary) once M2's history query
shape is known, with the fixture generator and the Python reference extended together, and a
measurement showing the query cost before and after.

## HOB-29 — The tmpfs requirement is decided from the mount table, not from `statfs`

**Where:** `crates/store/src/tmpfs.rs` (`check_tmpfs`, `MountTable::covers`).

`require_tmpfs` for the hot and warm tiers reads `/proc/self/mounts` and asks whether the
longest component-wise matching mount is `tmpfs`. That is the kernel's own record of what an
administrator mounted, and it is readable without `unsafe`, which `statfs` is not. What it does
*not* catch is a directory reached through a bind mount into a tmpfs (the table shows the
source's mount, not the reachable path's), or a mount whose type a kernel reports under another
name (`ramfs`, an overlay on tmpfs). The reader also refuses to guess: a mount whose arc cannot
be found is `mount-not-found`, not "therefore not tmpfs".

**Exit criteria.** A `statfs`-based check (`f_type == TMPFS_MAGIC`) behind the same decision
point, chosen together with whatever `unsafe`/dependency policy `HOB-27`'s rebuild settles, and a
test tree with a bind-mounted directory.

## HOB-30 — No session spools

**Where:** the whole crate; plan v4 §C.4.

§C.4 wants a crashed probe's counters and open spans to continue across a restart: a session
spool that remembers the last raw reading per metric and the open spans per issuer, so a restart
neither invents a rate from a fresh baseline nor leaves spans unsealed. This package explicitly
does not build it (the brief puts session spools out of scope alongside the daemon, socket,
probe, act and installer). The store is the machinery the spool will use — a tier is a segment
directory with bounds, and spans are a RAM table with a documented bound — but the spool's own
policy (what to persist, when, and what a restart does with it) has no implementation and no
test.

**Exit criteria.** A spool crate or module with its own file format and bounds, a restart test
(record, kill the writer, reopen, continue) and a statement of the guaranteed window.

## HOB-31 — The symlink refusal is a check, not an atomic open

**Where:** `crates/store/src/fsio.rs` (`StoreDir::symlink_metadata`, every operation).

Every operation refuses a path whose `symlink_metadata` says it is a link, which is what plan
v4 §C.5 requires and what the brief's "never follow a symlink out of the store directory"
mutation pins. Between that `symlink_metadata` and the separately-named `open`/`create_new`, a
concurrent actor that can write the directory could swap a regular file for a link and win the
race. Closing it needs `O_NOFOLLOW` (an `unsafe` `libc` call) or `openat`/`fstatat` relative
opens (also `unsafe`, or a dependency), and both are blocked by the same constraints as
`HOB-27`; `fsio` records the limit in its module docs rather than implying an atomicity it does
not have. The threat model bounds it: the store directory is the probe's own (`/run/hostobs`,
`/var/lib/hostobs`), not a shared or attacker-writable path.

**Exit criteria.** `openat`-relative opens with `O_NOFOLLOW` behind one `fsio` function, chosen
with the same `unsafe`/dependency decision as `HOB-27`, plus a test that a link planted between
the check and the open is still refused.

## HOB-32 — No filesystem fault injection

**Where:** `crates/store/src/fsio.rs`, `crates/store/src/error.rs`.

`ENOSPC` is mapped to a typed `no-space` and counted, and that mapping is tested. What is not
tested is a *short write*, a failed `fsync`, a rename that succeeds on one machine and not on
another, or `ENOSPC` arriving in the middle of the seal's streamed copy. The seal is written so
that any of those leaves the open name intact and the final name absent (temp name, footer,
`fsync`, rename, `fsync` the directory, unlink the open name), but the *order's* correctness
rests on the sequence being what `docs/SEGMENT-FORMAT.md` says rather than on an injected
failure. A fake directory handle would need either a trait in `fsio` (which the write-boundary
lint makes affordable, since the only writer is one module) or a real filesystem that can be
made full.

**Exit criteria.** A `fsio` seam a test can fail at a named step, with tests that assert the
observable state after each failure (which names exist, and what the next open reports).

## HOB-33 — The single writer is a property, not an observable

**Where:** `crates/store/src/queue.rs` (`WriterQueue`), `crates/store/src/store.rs`.

Plan v4 §A.4 asks for a single writer; the implementation gets it structurally — one `Store`
owns the queue and `write_pending`/`write_one` take `&mut self`, so the type system refuses two
concurrent drains of one store. Nothing tests the property *across processes*, and no lane
starts two writers on one directory. Two probes (or a probe and a restarted probe whose
predecessor has not exited) on the same `hot`/`warm` directory would each hold a water mark and
each append to a `*.seg.open` name; the second would refuse at `StoreDir`'s `create_new`, which
is the intended outcome, but that outcome is not asserted anywhere.

**Exit criteria.** A documented directory-lock (a `*.lock` file created with `create_new`, held
for the store's lifetime, refused with a typed error) plus a test that a second `Store::open` on
a live directory is refused, and a statement of what a probe crash leaves behind.

## HOB-34 — Retirement cannot see incarnations held outside the store

**Where:** `crates/store/src/store.rs` (`retire`, `reachable`), `crates/store/src/dictionary.rs`.

`retire` computes reachability as the union of what this store's own history tiers and events
tier still name, then frees every ephemeral dictionary entry nobody names. A consumer that
holds an incarnation *outside* those segments — a session spool (`HOB-30`), a reply in flight
over the socket, a record already handed to a client but not yet written — has no way to keep
it alive. The store's reachability is correct for its own bytes and is not a global claim, which
is what the module docs say; the risk is a caller that assumes otherwise.

**Exit criteria.** `retire` takes a caller-supplied extra reachability set (or a registry
handle), with a test that an externally-held incarnation survives a retirement pass.

## HOB-35 — The events ring's displacement is counted but not persisted

**Where:** `crates/store/src/events.rs` (`EventRing`, `EventStore::stats`).

The RAM ring keeps the most recent events under a byte cap and reports how many it displaced
(`displaced`, `displaced_bytes`), which satisfies "a drop is counted". It does not say *which*
ones, and the counter does not survive a restart: after a reopen a consumer knows the ring is
at its cap, not what it lost before the restart. The persisted events tier is the durable
record, so nothing is lost from the store — but the RAM view's own loss history is only
in-process.

**Exit criteria.** Either a displacement journal (bounded, appended to the events tier) or a
consumer contract that states the count is per-process, plus a test that pins whichever is
decided for a restart.

## HOB-36 — Events are keyed by strings, not by validated incarnations

**Where:** `crates/store/src/events.rs` (`Event::new`, `SpanTable`).

An event's `issuer` is a caller-supplied string bounded to 128 bytes, and spans are keyed by
`(issuer, key)`. §C.4's issuer is a cgroup incarnation, and the store cannot validate one
(it must not depend on `model`'s registry, `HOB-27`), so an issuer string that names a
different incarnation than the caller meant — or that two issuers happen to share — merges
their spans. A consumer that derives the issuer string from the identity (`<boot>-<pid>-
<starttime>`, say) gets the right behaviour; the store cannot check that it did.

**Exit criteria.** An issuer identity the store can validate (a dictionary id, once the probe
owns the join between `model`'s incarnations and the store's dictionary), with a test that two
incarnations reusing one path never merge their spans.

## HOB-37 — Span abandonment is a caller's loop

**Where:** `crates/store/src/events.rs` (`SpanTable::abandon_issuer`), `crates/store/src/store.rs`.

`abandon_issuer` produces the inferred `abandoned` end events for one issuer's open spans, and
`Store` exposes the table, but nothing ties abandonment to an incarnation disappearing: the
probe's scan round has to notice the disappearance and call it. If it does not, spans stay open
until the per-issuer bound refuses new ones, which is a visible refusal rather than silent loss
— but the `inferred: true` record the plan requires then never exists.

**Exit criteria.** A round-taking entry point (`Store::end_round(disappeared: &[issuer])`) that
abandons automatically, with a test that a vanished issuer's spans are closed as inferred and
that a *live* issuer's are not (`HOB-7`'s one-absent-scan rule).

## HOB-38 — A full writer queue drops the oldest job, always

**Where:** `crates/store/src/queue.rs` (`WriterQueue::submit`).

When the queue is at `writer_queue_bytes`, `submit` drops the oldest job, returns it and counts
it; a job larger than the whole bound is refused. That is one policy, chosen because it keeps
the bound after every operation and because the newest record is the more useful one to keep.
Alternatives the plan leaves open — blocking the producer, dropping the *newest*, or shedding
at a lower rate (decimation with a `NOT_SAMPLED` count) — are not implemented, so a probe whose
writer falls behind always loses the oldest measurements rather than the newest.

**Exit criteria.** A policy code in the store's config (`drop_oldest | drop_newest | block`) with
a bounded block timeout and a decimation counter reported as `NOT_SAMPLED` values, decided with
the probe's own scheduling observation.

## HOB-39 — A range answer is payload bytes, not decoded `model` values

**Where:** `crates/store/src/segment.rs` (`ScannedRecord`), `crates/store/src/store.rs` (`StoreRange`).

The brief asks a range query to return "`model` values with quality codes intact". The store
returns the **payload** it was given — a complete `proto` BLOCK frame, byte-identical to what
the writer wrote — plus the record's own time and incarnation list. The quality bytes are
inside that payload, so nothing is lost, but a caller must decode it: the store has no
`build_dispatch`-shaped join to `model`, because it may not depend on `proto` or `model`
(`HOB-27`). `quality::Reading`/`QualityCode` exist as the store's own vocabulary (used by
nothing yet, asserted against `fixtures/wire/quality.json`), so the types are ready for that
join to land on top of them.

Doing the join inside the store would mean a decoder and a value model in this crate — the
duplication `HOB-27` already forces on four header facts, multiplied by the whole column codec,
which is the wrong trade while the lock is pinned.

**Exit criteria.** After the image switch that `HOB-27` needs (lanes on the registry-set-identity image), `Store::range` (or a layer above
it) returns `model` rows with their quality codes and the payload form becomes an internal
detail; `quality::Reading` is then replaced by `model`'s type.

## HOB-40 — No focus tier is configured by `plan_v4`

**Where:** `crates/store/src/store.rs` (`StoreConfig::plan_v4`), `crates/store/src/tier.rs`.

Plan v4 §A.4 and the brief name "focus tiers" among the per-tier directories. The machinery is
general — `TierConfig::new(name, kind, segment_s, keep_s, cap_bytes, require_tmpfs)` plus
`StoreDirs` accepts any tier set — but `plan_v4` configures exactly hot, warm, cold and events,
because the plan does not yet say which focus tiers exist, what they keep or where they live;
inventing that here would be a default standing in for a product decision. A caller that wants
one adds it to `StoreConfig::tiers` and points `dirs` at a directory (the `tmpfs` field covers
the shared hot/warm directory, so a focus tier outside it needs a `StoreDirs` extension).

**Exit criteria.** The plan states the focus tier's trigger (a queried cgroup, an operator flag,
a window), its `segment_s`/`keep_s`/`cap_bytes` and its directory; `plan_v4` configures it and a
test asserts a record for that cgroup lands there and is retained past `hot`'s window.

## HOB-41 — No golden fixture has a non-zero record pad

**Where:** `fixtures/segment/`, `python/reference/make_segment_fixtures.py`.

Every committed fixture's record bodies are multiples of 8 bytes, so every `pad` byte in the
corpus is `0`. The padding rule (`pad = (8 - body % 8) % 8`, zero bytes, inside the record's
total length) is therefore pinned only by the Rust unit test that walks body lengths 0–63 and by
the Python encoder's own round trip — the two implementations agree on what they each generate,
but no committed pair of files proves they agree with each other on a padded record. A real
cross-language case needs a body (an id table plus a payload) whose length is not a multiple of
8, which the shared `fixtures/wire/*.bin` payloads are not.

**Exit criteria.** One committed fixture with a non-zero `pad`, generated by the Python encoder
and decoded by `golden_segment.rs`, plus the Python decoder reading the Rust writer's padded
output.

## HOB-42 — A sealed name is trusted without its footer, and header identity is not checked against the name

**Where:** `crates/store/src/tier.rs` (`ring_segment`), `crates/store/src/segment.rs` (`scan`).

Two "sealed" semantics are weaker than the word suggests. (a) `ring_segment` never requires a
footer for a non-`.seg.open` name, so a `.seg` that lost its footer — or a truncated sealed file,
whose remainder is a torn tail — is served as a sealed segment rather than refused; the repair
path only covers the open name. (b) The header's `seq` and `start_real_ns` are never compared
with the numbers in the file's name, so a file renamed by hand keeps serving the identity of its
header. Neither is a safety defect (a reader still validates every byte it uses, and the store
only removes what a name parse plus a header kind check proves is its own), but "sealed" should
mean "the footer that closes these records is present and agrees", not "the name does not end in
`.seg.open`".

**Exit criteria.** A sealed name without a verifiable footer is reported and quarantined instead
of served, a name/header identity disagreement is a typed refusal, and both have a test and a
mutant.

**FIXED (P0 bug fold-in).** A non-`.seg.open` name without a verified footer (a lost footer, a
truncated sealed file) is quarantined whole as `<name>.corrupt` and reported in the tier scan
(`SealedWithoutFooter`); a header `seq` or `start_real_ns` that disagrees with the name is the
typed refusal `SegmentIdentity` and quarantines the file, for `.seg` and `.seg.open` alike. The
seal keeps the open name's start second so a step change cannot trip the new rule. The Python
segment reference refuses the same files (`check_segment_name`); no golden fixture is possible
because the name is not in the file's bytes. `docs/SEGMENT-FORMAT.md` states both rules.
A `.seg.open` name that carries a footer is refused too (`OpenWithFooter`). Stored hand-mutation
patches, not yet run: `M1d-6` … `M1d-9`, `M1d-17`, `M1d-19`.

## HOB-43 — Quarantined files sit outside the cap

**Where:** `crates/store/src/tier.rs` (`rescan`: corrupt and oversized branches), `crates/store/src/segment.rs` (`quarantine_name`).

A corrupt or oversized segment is renamed `<name>.corrupt` so an operator can inspect it. That
name does not parse as a segment, so the ring ignores it: it is not in `bytes`, `cap_bytes` and
`keep_s` never evict it, and nothing limits how many exist. One quarantined file is the right
answer; a source of repeated damage (a failing disk, a writer bug) turns "never silently dropped"
into unbounded disk use, the failure the cap exists to prevent. Found in the delta review of the
fix round; the first review round missed it too.

**Exit criteria.** Either quarantined bytes count against the tier's cap (oldest `.corrupt`
removed first, reported) or a hard count/bytes bound refuses further quarantine with a typed,
counted report; a test fills the bound; a mutant removes it.

**FIXED (P0 bug fold-in), by the first alternative.** Quarantined files count in the tier's
byte total, `keep_s` applies to them by the `(start_s, seq)` their names keep, and under
`cap_bytes` they are evicted first, oldest first, before any live segment. Every such removal
is reported (`Evicted::quarantined`) and counted (`StoreStats::evicted_quarantined`). Only a
`.corrupt` file whose name parses to this tier and whose header is this tier's corpus is
adopted; any other look-alike is reported as a note, never counted, evicted or removed, so
those bytes stay outside the cap. A quarantined file deleted by hand, or one that cannot be
removed, leaves the list and the total without failing the pass, and a quarantined name keeps its
sequence number. Tests fill the bound; stored hand-mutation patches, not yet run: `M1d-2` … `M1d-5`,
`M1d-10`, `M1d-12` … `M1d-16`, `M1d-18`.

## HOB-44 — CLOSED: the lane needed no `pip`, because it needed no install

**Closed 2026-11-16** by re-declaring the lane (commit `09771f638`) and proving it
through Run-Gate in this branch after `09771f638` (the re-declaration). Original text kept
below, then the resolution.

**Where:** `tester-unified-rust/Dockerfile` (the `apt-get install` line);
`hostobs/run-gate.toml` `[lanes.assay-r1]`; `run-gate-project/run_gate.py`
`assay_source_setup`.

`assay-r1` is a `kind = "assay"` lane, so Run-Gate installs the selected worktree's own
`assay/` package into the lane before invoking the judge
(`"$ASSAY_PYTHON" -I -m pip install ... --editable <worktree>/assay`).
`tester-unified-rust` installs `python3` (Debian 13, CPython 3.13) but NOT `pip`,
`ensurepip` or `python3-venv`: measured in the image, `/usr/bin/python3 -m pip --version`
-> `No module named pip`, `python3 -m venv` -> `ensurepip is not available`, and no
`pip*` file exists under `/usr`, `/opt` or `/root`. The lane therefore stops with

```
run-gate: installing assay from the selected worktree source
/usr/bin/python3: No module named pip
run-gate: editable assay install failed
run-gate: lane 'assay-r1' verdict ERROR; exit_code 2
```

at head `c14d2cc30` (log retained in the program's `logs/pi-hostobs-assay-r1-assay-r1.log`),
before the judge runs and before any verdict artifact exists. Nothing about the lane's
declaration is wrong: `assay lanes --json` in that image parses `[lanes.assay-r1]` cleanly
(inventory `rigor: [R0, R1]`, `language: rust`, `external_tools: []`).

**Smallest image change.** One `apt-get install` addition in the image's toolchain stage
(and the same for the final stage only if pip is wanted there too):

```dockerfile
apt-get install -y --no-install-recommends python3-pip python3-venv
```

Measured with `apt-get install -s` in the image (Debian 13.7): 7 new packages,
`python3-pip` 25.1.1+dfsg-1, `python3-wheel`, `python3-packaging`, `python3-pip-whl`,
`python3-setuptools-whl`, `python3.13-venv`, `python3-venv`. No new base image, no
network at run time, no change to the crate cache or the lock. The editable install needs
`--no-build-isolation` (already passed by Run-Gate) and assay's zero runtime dependencies,
so `python3-pip` alone is sufficient; `python3-venv` is included only because
`ensurepip` is not packaged separately here.

**Exit criteria.** `run-gate assay-r1` in `tester-unified-rust` produces
`.assay/verdict-assay-r1.json` with an R1 claim whose `covered`/`executable` match the
measured artifact, the lane PASSES at head with the floor in `assay.toml`, and the same lane
FAILS when a test file is deleted (the negative run recorded in
`docs/CONSUMERS.md` §"Proof it is a real judge").

**Resolved: the declaration was the defect, not the image.** `assay-r1` is now a
`kind = "command"` lane ([`run-gate.toml`](run-gate.toml): `kind`, `argv`, `exit_map`).
assay is stdlib-only, so the judge runs straight from the mounted worktree's source —
`PYTHONPATH={worktree}/assay/src` plus `assay/analysis/src` for the `assay analyze`
post-checks — exactly how assay is consumed inside this monorepo, and nothing is installed.
The Rust image is unchanged and stays offline and lock-bound; the image change described
below is NOT needed and should not be applied for this lane.

**Evidence, all through Run-Gate (no throwaway copies).**

| run | head | verdict |
|---|---|---|
| `run-gate assay-r1` | fix round | `PASS`, `.assay/verdict-assay-r1.json` written, R0 PASS + R1 PASS |
| the same lane, one kernel test file deleted
  and committed in a temporary worktree | fix round + that deletion | `FAIL`, R1 `UNCOVERED_LINES` |

The PASS log binds the artifact to the head (`assay analyze verdict --expected-commit`)
and prints the measured totals from it: `lines 15100/15723 = 96.0377% over 33 targets;
branches 0/0 (unavailable)`, 24 files carrying `missing_lines` findings. The negative log
carries `lines 14777/15723 = 93.9833%` and `R1 FAIL UNCOVERED_LINES` while nextest is still
322/322 green, so the failure is the coverage claim and not the test command. Logs:
`logs/pi-hostobs-assay-r1-fix-assay-r1.log` and `…-fix-assay-r1-negative.log` in the release
program's `logs/`. The registered lane path is therefore PROVEN end to end (declaration →
Run-Gate → judge → verdict artifact → gate verdict), which the earlier copy-based probes
could not show.

**Source.** Filed 2026-11-15 by the hostobs assay-r1 adoption; measured at head
`c14d2cc30` in `tester-unified-rust:local`. Closed 2026-11-16; assay B549 records the
same finding from the judge's side.

## HOB-45 — Rust R2 and R3 are unregistered for hostobs

**Where:** `hostobs/run-gate.toml` and `hostobs/assay.toml` (no `assay-r2`/`assay-r3`
lane); assay `nyxloom-trove/4-backlog.md` B245 (R-3b, cargo-mutants ingestion) and B246
(R-4, Rust canaries).

With `assay-r1` registered the workspace has an assay-judged R1 line verdict. The
remaining two tiers do not exist for Rust in assay at all: R2 needs
the `cargo-mutants-outcomes` report parser and the `ASSAY_CHANGED_DIFF` handoff (B245), and
R3 needs the rustc `UNCOVERED_LINES` injector plus a receipt-writing test-break canary
(B246). hostobs therefore has no mutation or canary lane, and the test-quality claim is the
three hand-mutation campaigns in `docs/HAND-MUTANTS-M1*.md` (eighty-two stored, replayable
mutants). This is recorded once, as a known issue, not as a per-release waiver.

**Exit criteria.** An `assay-r2` lane (diff-scoped cargo-mutants, T3, `--base <previous
release tag>`) and an `assay-r3` lane (test-break canary with a receipt) exist here, pass
their registered gates in `tester-unified-rust`, and the hand-mutation scripts are retired or
reduced to the survivors those lanes cannot express.

**Source.** Filed 2026-11-15 by the hostobs assay-r1 adoption; assay A-501 (Rust R2 not
adopted is a known issue), `assay/docs/RUST-METHODOLOGY.md`.

## HOB-46 — A bare block cannot be validated against the held dictionary

**Where:** `crates/proto/src/frame.rs` `decode_block` (reference checks run only under
`if let Some(table) = &dictionary`).

A live stream carries its dictionary only when the generation changes (`docs/WIRE.md` §6.2), so
most live blocks are bare. For those, `decode_block` checks neither the generation nor the
row/column references: a dangling index or a row naming a metric is accepted, and the caller has no
API to supply the held generation.

**Exit criteria.** `decode_block_with(bytes, held: Option<&Dictionary>)` applies the in-frame checks
against the held generation (`stale-dictionary-generation` for a mismatch, `generation-regressed`
for a dictionary-bearing block that does not exceed it), plus the `bare-block-held-dict`,
`bare-block-dangling`, `generation-rollover` and `generation-regressed` cases of WIRE.md §11.3/§11.4
in Rust and Python.

**FIXED (P2a1).** `decode_block_with(bytes, held)` exists in Rust (`decode_block` is
`decode_block_with(bytes, None)`) and `decode_frame(data, held)` in the Python reference. A block
with an in-frame dictionary is judged against it and `held` is not consulted. A block without one
and with a held dictionary must declare the held generation (`stale-dictionary-generation`), and
its rows, columns, subject, column kinds and bucket aggregations are checked against the held
dictionary. With `held = None` no reference check is possible and none is made, exactly as before.
The fixtures `bare-block-held-dict` and `bare-block-dangling` carry the held dictionary as a
top-level `"held": {"generation", "entries"}` key. The `generation-regressed` and
`generation-rollover` rules need the per-stream held state and stay with the stream layer
(`HOB-54`); they are not part of this fix.

## HOB-47 — History aggregation is not checked against the metric mask

**Where:** `crates/proto/src/frame.rs` `decode_block`, `python/reference/hostobs_wire_ref.py`.

The dictionary's metric mask is derived and verified, but no decoder checks that a bucket's `agg`
is in the mask of every column of the block, so `mean` over a counter decodes as valid.

**Exit criteria.** Both decoders refuse with `aggregation-not-permitted` (WIRE.md §3.6) and the
`history-agg-not-permitted` fixture pins it.

**FIXED (P2a1).** After the reference checks, both decoders refuse a history block with an
`agg` outside the permitted mask of any of its columns as `aggregation-not-permitted` (the error
names the metric's dictionary index and the aggregation code); counters permit raw, last and
delta, gauges last, mean, min and max, states last. The mask is the column's own kind's, so the check
runs for an in-frame dictionary, a held one and none at all, and `encode_block` refuses it too. The
fixtures `history-agg-not-permitted` (`mean` over a counter), `history-gauge-raw`, `history-state-delta`,
`aggregation-not-permitted-held` and `history-gauge-aggs` (every gauge aggregation) pin it, and `history-buckets`, `history-five-buckets`
were regenerated with `last` buckets, because their mixed counter and gauge columns permit no other
aggregation (the n-bucket length pin is unchanged). Rust tests cover all six aggregations against
a counter, a gauge and a state column.

## HOB-48 — A history block cannot name its subject

**Where:** `crates/proto/src/frame.rs` `SectionKind`, `decode_block`; `python/reference/hostobs_wire_ref.py`.

A history block has no entity reference, so a multi-subject `series_batch` reply cannot say which
incarnation each block describes. (The time section's length half of this entry, `align8(14·n)`
instead of `align8(15·n)`, is a bug fix without a version change and lands in the P0 bug fold-in:
`TIME_BUCKET_BYTES = 15`, fixture `history-five-buckets`; WIRE.md §3.6, §13 V-1.)

**Exit criteria (P2).** The `subject` section (kind 6, version 1, required, `u32 ent | u32 0`, time
blocks only, `subject-without-time` otherwise) per WIRE.md §3.8; `PROTO_MINOR = 1`; the
`history-subject` and `subject-in-live-block` fixtures in both languages.

**FIXED (P2a1; the time-length half landed in the P0 bug fold-in).** `SectionKind::Subject`
(kind 6, version 1, directory required flag written and enforced on decode (a clear flag on the
subject or the header is `unknown-section-flags`), body
`u32 ent | u32 0`, canonical rank between the dictionary and the rows/time sections),
`Block::subject`, `encode_subject_body` and `PROTO_MINOR = 1` exist in Rust, and the Python
reference does the same. A non-zero reserved word is `unknown-section-flags` (section 6), a subject
that is not an entity is `row-reference-not-entity` (a dangling one `dangling-dictionary-ref`), and
a subject without a time section is `subject-without-time` in both the decoder and the encoder.
The fixtures `history-subject` and `subject-in-live-block` are decoded by both languages.

## HOB-49 — The Python reference cites a missing specification file

**Where:** `python/reference/hostobs_wire_ref.py` line 5.

The docstring names `hostobs/docs/WIRE-COLUMN-CODEC.md`, which does not exist; the normative
specification is `docs/WIRE.md`.

**Exit criteria.** The docstring points at `docs/WIRE.md` (§3 for the block layout).

## HOB-50 — Frame header flag/type consistency and directory allocation

**Where:** `crates/proto/src/frame.rs` `FrameHeader::parse`, `decode_block` (`Vec::with_capacity(n_sec)`).

`parse` accepts `SECTIONED` on REQ/REP/EVT/END, which WIRE.md §2.1 makes `bad-frame`. `decode_block`
reserves a directory vector of `n_sec` entries (up to 65,535) before it has proven that
`4 + 8·n_sec` bytes exist, so a 16-byte frame causes an allocation sized by an untrusted field
(bounded, but against WIRE.md §11.1).

**Exit criteria.** `parse` (or the stream reader) enforces the flag/type rule with the
`rep-sectioned-flag` case; the directory length is checked against the buffer before the vector is
reserved, with a unit test for `n_sec = 65535` in a short frame.

**FIXED (P2a1).** `FrameHeader::parse` refuses `SECTIONED` on every frame type but BLOCK as
`unknown-frame-flags` (`bad-frame` on the wire), with the same fixed-field precedence as before
(so it is judged before `not-a-block` and the length). `decode_block_with` proves that
`8·n_sec` directory bytes are present (`truncated`, context "block directory") before it reserves
the directory vector. The `rep-sectioned-flag` fixture pins the flag rule in both languages, the
`block-as-req` fixture was regenerated with the flag cleared (only byte 5 changed), and a Rust
unit test pins `n_sec = 65535` in a 16-byte frame.

## HOB-51 — Received frames are not held to the alignment and header-first rules

**Where:** `crates/proto/src/frame.rs` `decode_block` (post-P0 lines 1115-1154),
`python/reference/hostobs_wire_ref.py` `decode_frame` (post-P0 lines 523-531). WIRE.md §13 V-11.

Both decoders take a section body of any length and let an unknown section precede the header, so
the "every body starts at an offset ≡ 0 (mod 8)" and "the header body sits at `16 + 8·n_sec`"
guarantees of WIRE.md §3.1 hold only for frames this codebase encoded. Measured on the Python
reference: an optional `len = 5` section decodes (frame `len` 357); placed first, the header body
starts at 69.

**Exit criteria (P2).** Both decoders refuse a section `len` not ≡ 0 (mod 8) with `section-length`
and an unknown kind at directory entry 0 with `section-order`, checked after the rank check (WIRE.md
§3.10); the `unaligned-optional-section` and `optional-section-before-header` fixtures pin them in both
languages; the existing `section-order` fixture keeps its pinned `index: 1, kind_code: 1`.

**FIXED (P2a1).** Both decoders refuse a directory entry whose `len` is not a multiple of 8,
unknown optional kinds included, as `section-length`; the check runs after the directory is read
and before the order check. After the rank check an unknown kind at directory entry 0 is
`section-order` at index 0 (a known non-header kind there is already a rank violation or a missing
header). The `unaligned-optional-section` and `optional-section-before-header` fixtures pin both in
Rust and Python, and the `section-order` fixture still reports `index: 1, kind_code: 1`.

## HOB-52 — A metric repeated with other facts is `conflicting-column` in Rust

**Where:** `crates/proto/src/dict.rs` `decode` (line 891 → `intern` line 596). WIRE.md §13 V-12.

A dictionary that repeats a metric name with other facts is `conflicting-column` in Rust and
`duplicate-dictionary-entry` in the Python reference (measured), so one defect has two outcomes.

**Exit criteria (P2).** Every repeated dictionary identity is `duplicate-dictionary-entry` in both
languages whatever the other fields (WIRE.md §3.7, D-W23); the `duplicate-metric-other-facts` and
`duplicate-entity-other-label` fixtures pin it.

**FIXED (P2a1).** `Dictionary::decode` turns the `conflicting-column` that `intern` reports for a
metric name repeated with other facts into `duplicate-dictionary-entry` at that entry's index, so
all three repeats (metric with other facts, `inc` with another label, exact repeat) have one
outcome in Rust and in the Python reference. `intern` itself still reports `conflicting-column` for
a live table. The `duplicate-metric-other-facts` and `duplicate-entity-other-label` fixtures pin it
in both languages.

## HOB-53 — The Python reference checks buffer length before `not-a-block`

**Where:** `python/reference/hostobs_wire_ref.py` `decode_frame` (post-P0 lines 495-504). WIRE.md §13 V-13.

The reference checks buffer length and `SECTIONED` before the frame type, so a truncated REP is
`truncated` in Python and `not-a-block` in Rust.

**Exit criteria (P2).** The reference follows the WIRE.md §3.10 check precedence (Rust order); the
`rep-not-sectioned-truncated` fixture pins `not-a-block` in both languages.

**FIXED (P2a1).** `decode_frame` in the Python reference now checks the frame type
(`not-a-block`) before the buffer length (`truncated`, `trailing-bytes`) and before `SECTIONED`
(`missing-section`), the order of Rust `decode_block` and of WIRE.md §3.10. The
`rep-not-sectioned-truncated` fixture (a REP of declared length 32 holding 24 bytes, flag clear)
pins `not-a-block` in both languages.

## HOB-54 — The send hub, stream dictionaries, envelope checks and clients do not exist yet

**Where:** `crates/proto/src/stream.rs` (new), `python/hostobs_client/stream.py` (new), the P5b serving
loop. WIRE.md §5.2-§5.4, §12.

`proto` decodes single frames only. The frame reader (WIRE.md §5.2), mode detection and send hub
(§5.3: pin-time `dropped` stamping, coalescing, the `last_pushed_gen` form rule, Live/Query/control
pools with per-uid caps, frame completion and stall closes) and the connection timers (§5.4) are
specified with fake-clock tables but have no code, so no stream property of `hostobs/1` is tested.

**What exists (P2a2).** `crates/proto/src/stream.rs` holds `FrameReader` (the §5.2 machine, one unit
test per row), `ModeDetect` (the §5.3 mode-detection rows) and `ConnTimers` (one test per §5.4 timer
row); `ProtoError::Deadline` is the reader's completion-age error. The 12 reader cases are
`fixtures/stream/reader-*.json` (generator `python/reference/make_stream_fixtures.py`, replayed by
`crates/proto/tests/stream_fixtures.rs`). **P2b:** `crates/proto/src/hub.rs` holds `SendHub` (one unit
test per §5.3 row and consequence) and the 19 `fixtures/stream/hub-*.json` files (generator
`python/reference/make_hub_fixtures.py`, replayed by `crates/proto/tests/hub_fixtures.rs`). Still open: `StreamDicts` and the 6 client cases, `envelope.rs`, the Python `stream.py`, and P5b's 18 connection cases.

**Exit criteria.** P2: the §12 `FrameReader`, `ModeDetect`, `SendHub` and `ConnTimers` APIs in Rust
with the 12 reader and 19 hub cases of WIRE.md §11.4 and one test per §5.2/§5.3 row; P3: the Python
`FrameReader` passes the same 12 reader cases and the 6 client cases; P5b: the 18 connection cases
of §11.4 against a fake clock.

## HOB-55 — A look-alike name with a huge sequence poisons the tier's water mark

**Where:** `crates/store/src/format.rs` (`SegmentFileName::parse`, `SEQ_NAME_MAX`),
`crates/store/src/tier.rs` (`rescan`, `open_segment`), `python/reference/hostobs_segment_ref.py`.

`rescan` records `highest_seen` for every name of the tier before it proves the file is the
store's own, so `hot-0-18446744073709551615.seg` (or the same with `.seg.corrupt`) raises the
sequence water mark to `u64::MAX` and every later `open_segment` fails with `LengthOverflow`
until someone removes the file; a look-alike at `u32::MAX` would do the same one step later.

**Exit criteria.** The name parsers refuse a sequence above a bound the store can never mint, so
every caller agrees; such a name is a look-alike (a note, never counted, adopted or deleted, and
never raises the water mark). An unproven name never raises the mark above the bound minus one; a
proven one (an adopted segment) is adopted at the bound, and the sequence after the bound is the
typed overflow error. All four spellings, a garbage name at the bound, a valid segment at the
bound, and minting the bound from a tier whose scan saw the bound minus one are tested; the Python
reference mirrors the bound; mutants remove each guard.

**FIXED (P0b).** `SEQ_NAME_MAX = u32::MAX` (136 years at one segment a second). `parse` and
`parse_quarantined` (and the Python `parse_segment_name`/`parse_quarantine_name`) refuse a larger
sequence in all four spellings; `rescan` lets a name reserve at most `SEQ_NAME_MAX - 1` until an
adopted live segment proves it (then its full sequence); `open_segment` returns `LengthOverflow`
once the next sequence would exceed the bound; `docs/SEGMENT-FORMAT.md` states the rule. Known
limit: a genuinely quarantined segment at exactly `SEQ_NAME_MAX` reserves only `SEQ_NAME_MAX - 1`,
so a mint at the bound with the same `start_s` could later be refused its quarantine rename because
`<name>.seg.open.corrupt` exists; reaching it needs 2^32 segments. Stored
hand-mutation patches `M1d-17` and `M1d-20`, not yet run.

## HOB-56 — `StoreStats::bytes` drifts from the tiers' own totals

**Where:** `crates/store/src/store.rs` (`write_one`, `record_event`, `evict`, `seal_all`,
`recount`).

Maintaining `stats.bytes` by arithmetic (add `bytes_written`, subtract `Evicted::bytes`) disagrees
with the tiers' own totals: an entry dropped because its file was already gone leaves `Tier::bytes`
but not `Evicted::bytes`, and a new segment's header (and a seal's footer) is in the tier total but
not in `bytes_written`.

**Exit criteria.** The tiers' own totals are the single source: `stats.bytes` is recomputed from
them after every operation that can change one and the incremental arithmetic is gone; tests
compare it with the sum of the tiers' `bytes()` and with the summed `file_len` of their segments
and quarantined files after a hand-deleted quarantined file, a hand-deleted live segment, a write
that opens a segment, a seal and an eviction; a mutant restores the arithmetic.

**FIXED (P0b).** `refresh_bytes` sums the history tiers and the events store and runs after every
write, seal and eviction pass, on the error path as well; `StoreStats::bytes` documents that scope.
Stored hand-mutation patches `M1d-18` (rewritten) and `M1d-21`, not yet run.

## HOB-57 — The M1d hand-mutation script has no per-mutant timeout

**Where:** `docs/hand-mutants/m1d-mutants.py`, `docs/HAND-MUTANTS-M1D.md`.

A mutant that makes the suite hang (a loop that no longer ends) hangs the whole campaign when the
script runs each suite with no subprocess timeout, and the container stays running.

**Exit criteria.** `--timeout-s` bounds every run (defaults 1800 s for a Rust suite, 300 s for a
Python one; the baseline uses the same limits); a timeout is classified `KILLED-TIMEOUT` (a hang is
a detected mutant), counted apart in the summary, and the container of a timed-out Rust run is
removed by its exact name; the patch count and the `--dry-run` result stay consistent (0 stale).

**FIXED (P0b).** Each Rust run names its container `hostobs-mut-<pid>-<n>`, removes a stale
container of that exact name first, and `docker rm -f` takes that exact name on a timeout or any
other exception (a failed removal is reported); the method section of `docs/HAND-MUTANTS-M1D.md`
documents the classification and that a timeout under host load is inconclusive. The stored patch
count is 53 (`--dry-run`: 0 stale).

## HOB-58 — Eviction reports never reach the caller

**Where:** `crates/store/src/tier.rs` (`remove_quarantined`, `remove_segment`, `Evicted`),
`crates/store/src/store.rs` (`Store::notes`).

`remove_quarantined` and `remove_segment` reported an entry dropped because its file was already
gone, or could not be removed, only in `scan.notes`: the caller of `append` or `evict` never sees
it, a rescan resets those notes, and nothing bounds them.

**Exit criteria.** `Evicted` carries the reports; the tier fills them in addition to `scan.notes`,
capped at `MAX_EVICTION_NOTES` with the oldest dropped; the store copies them into its own ring
with the same cap so `Store::notes()` shows each once; `Store::notes()` hides only one tier's note
about another tier's file in a shared directory, never a tier's note about its own name; the known
limit (a file that could not be removed stays on disk, uncounted, and is re-adopted at the next
restart) is documented; tests cover both paths, the cap and the filter; mutants remove each piece.

**FIXED (P0b).** `Evicted::notes`, filled by `Tier::report` (the scan notes' reports capped, the
rescan's own notes never dropped for them); the store keeps at most 256 in a ring and
`Store::notes()` appends them once after its filter. Tests cover an already-gone quarantined file,
an already-gone live segment, an unremovable quarantined file (a link in its place), the write
path, the cap, garbage under a tier's own live and quarantine names, and the shared-directory
case; stored hand-mutation patches `M1d-22`, not yet run.

## HOB-59 — A failing eviction drops the partial `Evicted`

**Where:** `crates/store/src/tier.rs` (`Tier::append`, `Tier::evict`),
`crates/store/src/store.rs` (`Store::write_one`, `Store::evict`, `evict_each`).

`Tier::evict` returns `Err` from `remove_segment` when a live segment cannot be removed, after
earlier entries of the same pass were already removed or reported. The `Evicted` of that partial
pass (its counts, bytes and notes) is lost, so `StoreStats::evicted_*` and `Store::notes()` miss
what the pass did before the refusal, and `Tier::append` returns the error although its record is
on disk. `stats.bytes` itself stays true (it is recomputed from the tiers), as do the tiers'
own `scan.notes`.

**Exit criteria.** The partial result of a failed pass reaches the caller: `Evicted` is returned
with the error (or the counters and notes are applied before the error is propagated), the store
counts the partial pass, and a test makes a removal fail after a successful one and checks the
counts and the notes.

## HOB-60 — `Store::notes` visibility is by name, and the notes ring may be redundant

**Where:** `crates/store/src/store.rs` (`is_visible`, `Store::notes`, `keep_notes`).

`Store::notes` hides a scan note whose name parses to a tier of the store other than the tier whose
scan made it. That decision is by name, not by directory: a foreign `warm-0-3.seg` in a directory
warm does not scan is hidden although no tier owns it. The store's eviction-notes ring is
redundant with the tiers' `scan.notes` for every case reachable through the store's API (the store
has no call that rescans a tier), so the claim that the ring outlives a rescan is not tested and is
not made; a mutant that leaves the ring out (`M1d-22`) is possibly equivalent for the same reason.

**Exit criteria.** `is_visible` hides a cross-tier note only when the producer's directory is also
the directory of the tier the name belongs to (shared directory), and a test plants a foreign
`warm-0-3.seg` in cold's directory and sees it; either a store call rescans a tier and a test shows
the ring keeping a report across it, or the ring is removed and `Store::notes` reads `scan.notes`
alone.
