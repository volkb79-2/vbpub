# Changelog

## [Unreleased]

### Added and fixed (P2a1: wire codec and Python reference)

- **`HOB-48`: the `subject` section (kind 6) and `PROTO_MINOR = 1`.** A history block names the one
  incarnation it describes (`u32 ent | u32 0`, canonical rank between the dictionary and the
  rows/time sections); `Block::subject`, `encode_subject_body` and the Python reference carry it.
  It needs the time section (`subject-without-time`, decoder and encoder), must name an entity
  (`row-reference-not-entity`) and has a zero reserved word (`unknown-section-flags`).
- **`HOB-46`: `decode_block_with(bytes, held)`.** A block without an in-frame dictionary is judged
  against the held one (`stale-dictionary-generation`, reference, kind and mask checks); `decode_block` is
  `held = None` and behaves as before. Fixtures carry the held dictionary as a top-level `"held"` key.
- **`HOB-47`: `aggregation-not-permitted`.** Every history bucket's aggregation must be in every
  column metric's mask, in both decoders. `history-buckets` and `history-five-buckets` carry `last`
  buckets (their counter and gauge columns permit no other); the 15-byte length pin is unchanged.
- **`HOB-50`/`HOB-51`: received frames.** `SECTIONED` on a non-BLOCK frame is `unknown-frame-flags`
  (`block-as-req` has the flag clear), every section length is a multiple of 8
  (`section-length`), an unknown kind at directory entry 0 is `section-order` at index 0 (after the
  rank check), and the directory bytes are proven present before the vector is reserved.
- **Both decoders give the same error for the same bytes.** Only the first header body is decoded (a
  second header is `duplicate-section`); a non-zero pad byte in a column, time or dictionary body is
  `non-zero-reserved`, and the time body's `agg` bytes are judged before its pad; the Python
  `decode_dictionary` refuses exactly what Rust `Dictionary::decode` refuses, with the same kinds, and
  raises nothing but `WireError`; the fixed header fields are read and judged in wire order (a short
  buffer with an unknown type byte is `unknown-frame-type`); the header and the subject must carry the
  directory required flag, judged after the section version; the aggregation mask needs no dictionary
  (`held = None` included) and `encode_block` refuses `aggregation-not-permitted`; the column scan uses
  a set.
- **`HOB-52`/`HOB-53`: one outcome per defect.** A metric name repeated with other facts is
  `duplicate-dictionary-entry` in Rust, judged before the text's control bytes; the Python reference
  judges `not-a-block` before the buffer length. There are two new `ProtoError` variants (44 in all).
- **Fixtures.** `fixtures/wire` holds 70 cases (11 valid, 59 refusals): 29 existing cases and 41 new ones.
  Not part of this change: the stream layer (`HOB-54`), the `generation-regressed` rule, and `HOB-49`.

### Fixed (P0b store follow-ups)

- **`HOB-55`: a look-alike name with a huge sequence cannot poison the tier.** The store never
  mints a segment sequence above `SEQ_NAME_MAX = u32::MAX`, so a name above it
  (`hot-0-18446744073709551615.seg`, also `.seg.open`, `.seg.corrupt`, `.seg.open.corrupt`) is not a
  segment name: the Rust and Python parsers refuse it, and the scan notes it and never counts,
  adopts or deletes it or lets it raise the water mark. An unproven name never raises the mark
  above the bound minus one; a proven one (an adopted segment whose header agrees with its name)
  is adopted at the bound itself, and the sequence after the bound is the typed `LengthOverflow`.
- **`HOB-56`: `StoreStats::bytes` is the tiers' own total.** It is recomputed from the history
  tiers and the events store after every write, seal and eviction pass (error paths too), with no
  incremental add/subtract arithmetic, so a vanished file, a new segment's header and a seal's
  footer are always in it.
- **`HOB-58`: eviction reports reach the caller.** `Evicted::notes` carries the reports of an
  entry dropped because its file was already gone or could not be removed (a quarantined file that
  cannot be removed stays on disk, uncounted, and the next restart adopts it again). A tier keeps
  at most `MAX_EVICTION_NOTES` (256) of them in `scan.notes` after its rescan notes, and the store
  at most 256 in its ring, the oldest dropped first; `Store::notes()` shows them once and hides
  only one tier's "names another tier" note about another tier's file in a shared directory, so a
  note about a tier's own garbage name stays visible.
- **`HOB-57`: the M1d hand-mutation script has a per-run timeout.** `--timeout-s` (default 1800 s
  for a Rust suite, 300 s for Python); a timeout is `KILLED-TIMEOUT`, counted apart (and
  inconclusive under host load), and the container is removed by its exact name
  `hostobs-mut-<pid>-<n>` on a timeout or any exception. 53 stored patches (`M1d-20` to `M1d-22`
  added, `M1d-18` rewritten); the campaign has not been run.

### Fixed (P0 bug fold-in)

- **`HOB-8`: `FrameHeader::parse` bounds the declared length.** It refuses a length above
  `MAX_FRAME_BYTES` (`oversized-frame`) or below `FRAME_HEADER_LEN` (`truncated`), after the
  fixed-field checks, in Rust and in the Python reference, so a streaming reader may allocate
  `len` straight after `parse`. `decode_block` keeps its own check; through `parse` it can no
  longer fire, which leaves its multi-line `return Err(ProtoError::OversizedFrame { .. })`
  uncovered (documented in `docs/HAND-MUTANTS-M1D.md`). No
  wire change. The Python reference also stops leaking a `struct.error` for a 12 to 15 byte
  block frame.
- **`HOB-43`: quarantined files are inside the cap.** A `<segment>.corrupt` file whose name
  parses to the tier and whose header is the tier's corpus is adopted: it counts in the byte
  total, `keep_s` applies to it by its name's `(start_s, seq)`, and under `cap_bytes` it is
  evicted before any live segment, oldest first. Evictions are reported
  (`Evicted::quarantined`) and counted (`StoreStats::evicted_quarantined`); no new config key.
  Look-alikes are reported and never counted, evicted or removed.
- **`HOB-42`: a sealed name must be sealed, and a name must match its header.** A non-`.seg.open`
  file without a verified footer, or whose header `seq`/`start_real_ns` disagrees with its
  name, is quarantined and reported (`SealedWithoutFooter`, `SegmentIdentity`), never served.
  The seal keeps the open name's start second. The Python segment reference refuses the same
  files; `docs/SEGMENT-FORMAT.md` states both rules.
- **`HOB-48` (length half only): the time section is 15 bytes per bucket, not 14.** A bucket is
  `i64 real_ns` + `u32 covered_ms` + `u16 samples` + `u8 agg`. The encoder wrote that, but
  `time_body_len` (Rust `frame.rs`, Python `hostobs_wire_ref.py`) computed `align8(14 * n)`,
  which agrees only up to three buckets: for `n = 4` the encoder wrote 64 bytes and the decoder
  demanded 56. Both sides now share one `TIME_BUCKET_BYTES = 15`. Tests pin the exact body length
  and the frame round trip for 0 to 9 buckets in both languages, and a new golden fixture
  `history-five-buckets` is decoded by both (with the two HOB-8 refusal fixtures
  `frame-length-below-header` and `block-without-directory`, `fixtures/wire` grows from 26 to
  29 cases: 8 valid, 21 refusals; the other 26 regenerate byte-identically). **No wire version
  bump is needed**: no
  hostobs release or tag exists, no store segment fixture carries a history block, and the only
  history fixture before this had three buckets, so no byte stream with `n >= 4` has ever been
  written by a shipped build. The `subject` section (kind 6) and the rest of `HOB-48` are not
  part of this change.

### Added

- **`docs/WIRE.md`: the normative `hostobs/1` specification** — connection and handshake,
  frame types and `corr`, the implemented block layout with offsets and the received-frame
  alignment rules, every M2-M3 verb with strict keys and worst-case request sizes, closed error
  codes, fake-clock state machines for the reader, the send hub (pin-time `dropped` stamping,
  per-stream coalescing, query backpressure, a global budget owner) and the connection timers
  (hello and idle; the request deadline is the reader's completion age), per-selection dictionary generations, deterministic `series_batch`
  packing, the efficient Python access path with deterministic gates set from measurements on
  CPython 3.14, versioning, conformance cases and the P2/P3/P5 work breakdown. Files
  `HOB-46`..`HOB-50` (bare-block validation, aggregation mask, the history subject section, a
  stale spec pointer, header and directory checks); `HOB-48` keeps only the subject half, the
  time-length half is the P0 bug fold-in's. `HOB-5` is decided (separate per-aggregation history
  blocks) and `HOB-6` closed by design (`DICT` reserved). Rev 3 (second review): the send hub
  pushes the dictionary form whenever a tick's generation differs from the stream's last pushed
  one, closes a connection whose pinned frame is not written within 5 s, caps every uid at a
  quarter of the Live and of a new separate Query pool, sheds only connections that hold an
  unpinned tick, admits any frame into an empty control queue and bounds every JSON reply to
  448 KiB with paging; `section-order` checks rank before the header-first rule; the Python path
  retains at most one previous frame of ≤ 1 MiB per stream and pins CPython 3.14.10. Files
  `HOB-51`..`HOB-54` (received-frame alignment rules, one outcome for a repeated dictionary
  identity, the reference's check precedence, the stream layer machines).
- **Hand-mutation record for the P0 fold-in** (`docs/HAND-MUTANTS-M1D.md`,
  `docs/hand-mutants/m1d-mutants.py`): thirty-five stored patches in the method of
  `HAND-MUTANTS-M1C.md`. The campaign has not been run yet; the doc says so.
- **Gate invocations in `docs/CONSUMERS.md` and `README.md`** now state the estate wrappers
  (`scripts/controller/gate-run.sh <lane>` from a linked worktree's project directory;
  `scripts/controller/remote-gate.sh v1001 hostobs <lane> <commit-or-branch>` with
  `CONTROLLER_REMOTE_GATE_HOSTS=v1001`) instead of a bare `run-gate`, and that the Rust lanes
  need the `tester-unified-rust` image on the executing host.

- **`assay-r1`: the coverage verdict is assay's.** A new `assay-r1` lane in
  `run-gate.toml` binds to `assay.toml [lanes.assay-r1]`, which declares a Rust
  `whole_target` R1 judge over the 33 `src/*.rs` files of `model`, `proto`, `kernel` and
  `store` at a 95.0% line floor (measured 96.0377%, 15100/15723 executable lines, floored one
  point down; derivation in the lane file). The lane keeps the exact workspace command
  `selftest` runs — offline, locked, nextest, doctests and the per-crate line/region floors
  from `coverage-floors.toml` — so what was executed cannot drift; assay now renders the
  coverage claim, prints the measured totals in the verdict, lists every uncovered
  executable line as a finding, and reports branch capability as `unavailable` (stable
  `cargo-llvm-cov` emits no `BRDA` arcs; assay B523) rather than as zero-of-zero. R2 (mutation)
  and R3 (canary) stay unregistered for Rust (`HOB-45`). Proven a real judge, not a
  relabelled floor: deleting one kernel test file leaves nextest green but drops the R1
  claim to 93.9833% and FAILs it. The lane is a **command lane**: assay is stdlib-only, so it
  runs from the mounted worktree's own source (`PYTHONPATH={worktree}/assay/src`) with no
  install, which is why it needs no `pip` in the Rust gate image (`HOB-44` was the
  `kind = "assay"` declaration's install step, and is closed by this re-declaration; the
  image is unchanged). Proven through Run-Gate in this branch: the lane log carries
  `verdict PASS` and the measured totals, and a lane-run negative (one kernel test file
  deleted and committed in a temporary worktree) logs `FAIL/UNCOVERED_LINES`. `selftest`
  stays as the enforced baseline.
- **`store` crate (M1 part 3):** the on-disk history and events store. A segment is a
  sequence of `proto` payloads inside a checksummed record envelope (64-byte header, records
  carrying their own total length, sequence number, body CRC and incarnation list, a 64-byte
  footer on a sealed file), named `<tier>-<start_s>-<seq>.seg` (`*.seg.open` while being
  written). Tier directories are bounded by `keep_s` **and** `cap_bytes` (whichever bites
  first) and evict whole segments oldest first by `(start_s, seq)`; the active dictionary is
  RAM-bounded by `dict_active_max`, ids are monotone and never reused, and retirement frees an
  incarnation only when no stored segment still names it; the writer queue is byte-bounded
  with an oversize job refused (`job-too-large`) and every drop **counted**; a crash's torn
  tail is detected at open (`complete | incomplete | corrupt`) and either truncated back to
  its last good record or quarantined as `<name>.corrupt`; a seal is atomic in one order
  (temp name, footer, `fsync`, rename, `fsync` directory, unlink the open name); range reads
  are bounded in records and bytes and return payloads byte-identical to what was written
  (typed decoder errors, never a panic); and the events store has the same machinery plus the
  plan's RAM ring, open-span table with `(issuer, key)` LIFO closes and inferred `abandoned`
  ends, under its own cap. The byte layout is specified in
  [`docs/SEGMENT-FORMAT.md`](docs/SEGMENT-FORMAT.md).
- **The write boundary is a lint, and the lint has a mutation check.** Every filesystem
  mutation in the `store` crate lives in `crates/store/src/fsio.rs` (tests included, which is
  why the test temp-directory helper is `fsio::testdir::TempDir`);
  `tools/check-lints.py` refuses the write tokens anywhere else, and the new
  `tools/check-lints-mutation.py` (a `lint` lane step) plants five violations — including a
  whole new source file containing a write and a `#[mutants::skip]` inside the allowed file —
  and asserts each is reported, then asserts the baseline and `fsio.rs` are not. Plan v4 §A.3.
- **Segment fixtures and an independent reference decoder:** `fixtures/segment/` holds five
  whole segment files (open and sealed history, a sealed segment with no records, the events
  corpus, and a later epoch/dictionary generation) generated by
  `python/reference/make_segment_fixtures.py` from the format document.
  `python/reference/hostobs_segment_ref.py` decodes and re-encodes them with nothing but the
  standard library, and `crates/store/tests/golden_segment.rs` decodes them and reproduces
  them record for record with the Rust writer. Twenty Python tests pass under `python3 -I`.
- **Hand-mutation record for M1 part 3** (`docs/HAND-MUTANTS-M1C.md`,
  `docs/hand-mutants/m1c-mutants.py`): fifteen stored patches over the brief's nine failure
  modes (cap off-by-one, `keep_s` ignored, eviction order from both sides, torn tail accepted
  in the reader and in the tier's rescan, seal without a rename, retirement ignoring
  reachability, an uncounted drop, a foreign file deleted, a foreign-corpus segment adopted,
  a symlink followed, and three in the Python reference). All fifteen killed; the ring-order
  patch survived the first pass and was killed by a test that pins `oldest()`/`newest()` by
  name rather than the outcome of one eviction.
- **Backlog** grows from `HOB-26` to `HOB-40`: the `store`'s dependency limit on
  `proto`/`model` (and with it a range answer that is stored payload rather than decoded
  `model` rows, and a `plan_v4` with no focus tier), the O(records) range scan, the
  mount-table tmpfs check, the absent session spools, the non-atomic symlink refusal, the
  absent fault injection, the single writer as a property rather than an observable,
  retirement's blindness to externally-held incarnations, the in-process events displacement
  count, string-keyed event issuers, caller-driven span abandonment and the drop-oldest
  queue policy.
- **`kernel` crate (M1 part 2):** the read-only collectors — cgroupfs (`cpu.stat`,
  `cpu.max`/`weight`, `memory.current`/`.stat`/`.events`/`.max` and the other limits,
  `io.stat` with iocost, `pids.current`/`.max`, `memory.swap.current`, the three PSI files)
  and `/proc` (`stat`, `meminfo`, `vmstat`, `loadavg`, one process's
  `stat`/`statm`/`status`/`cmdline`, the boot id, the host `/proc/pressure/*` files), plus
  the identity inputs (`CgroupIdentity` from `(device, inode)`, `ProcessIdentity` from
  `(boot id, pid, starttime ticks)`) and an injectable clock (`Clock`, `ManualClock`,
  `SystemClock`, `Timeline`, `interval_between`). Every file access goes through one
  root-anchored, read-only `Fs` trait (`RealFs`, and `MemFs` for fixtures) with a byte
  bound, a line bound and an entry bound; every parse failure is a typed error carrying a
  quality code, never a zero. `#![forbid(unsafe_code)]`, no I/O beyond `File::open`/`read_dir`/
  `symlink_metadata`, no threads, no writes (`HOB-22`).
- **Committed kernel fixtures:** `crates/kernel/tests/fixtures/cgroupfs/` (the sampler's own
  cgroupfs tree, `gstammtisch`'s contents and `transient-footgun`) and
  `crates/kernel/tests/fixtures/proc/` (a hand-built `/proc` tree whose `stat` fields follow
  the kernel's own numbering). The readers are driven by those trees only, never by a live
  host, so the suite cannot depend on host load or version.
- **SP-2 evidence as tests** (`crates/kernel/tests/sp2_evidence.rs`): incarnation reuse (a
  cgroup recreated at one path, a recycled pid after exit, a reboot repeating pid and start
  tick), wall-clock jumps versus a monotonic interval (with the wall-based rate computed and
  shown negative), a pair from two boots/epochs refused rather than subtracted, a
  zero-length interval refusing a rate, and late discovery (an age since discovery and none
  since boot). Every expectation is hand-computed.
- **Hand-mutation record for M1 part 2** (`docs/HAND-MUTANTS-M1B.md`,
  `docs/hand-mutants/m1b-mutants.py`): thirty-one stored patches over the proto/Python
  refusals, the kernel collectors, the parse/value/identity guards, the clocks and the fix
  round's stat mapping. All killed, each restore SHA-256-verified; a patch that does not
  compile is reported as `NOT-BUILT` instead of counted as a kill, and a patch whose stored
  fragment no longer matches the source is `NOT APPLIED` rather than a survivor.
- **Backlog** grows from `HOB-13` to `HOB-26`: the three luna findings as fixed entries, the
  kernel's own value/quality types and their consequences, `st_dev` versus a `statx` mount
  id, the missing `/sys` topology source, the fixture-only reader evidence, and (from the
  fix round) the `stat` field mapping, the reported-length read, the iocost overflow rule and
  the SP-2 rate-helper limit.

### Fixed

- **A block shape is refused under any other frame type** (`not-a-block`, `HOB-14`).
  `decode_block` read the directory and bodies of whatever frame it was handed, so a `REQ`,
  `DICT` or `END` frame carrying block sections decoded as a block; `encode_block` had the
  same hole on the write side. Both now refuse, the Python decoder refuses with the same
  kind, and the `block-as-req` fixture pins it.
- **The canonical section order is enforced on decode** (`section-order`, `HOB-15`). The
  encoder checked it and the decoder did not, so a reordered directory decoded and then
  re-encoded to different bytes. `check_canonical_order` now runs before any body is read, a
  `direction`-free `canonical_rank` table is shared with the encoder, and the
  `section-order` fixture moves entries and bodies together.
- **A truncated section directory is a `WireError`, not a `struct.error`** (`HOB-16`). The
  Python decoder unpacked each entry after checking only the count, so a frame declaring
  more sections than it carried raised `struct.error`. The read is now bounded with context
  `block directory` and `need`/`have`; the `truncated-directory` fixture pins it and a
  Python test asserts the refusal for declared counts 1..4.
- **A `/proc/<pid>/stat` field is named by its proc(5) number, not by a hand-written index**
  (`HOB-23`). `parse_stat` read `minflt` from field 12 (which is `majflt`) and `majflt` from
  field 13 (`cmajflt`), and the unit-test helper, the committed fixture and the SP-2
  assertions carried the same wrong table, so the tests pinned the defect. The reader now
  declares field numbers 10/12/14/15/20/22/24/42 and derives every index through one
  `STAT_FIELD_OFFSET`, children's faults (11/13) are deliberately unread, the fixture and
  helper are generated from field numbers, and a new test gives field `n` the value `n` so
  every assertion proves its own field.
- **A read is bounded by the bytes it actually reads** (`HOB-24`). `RealFs::read` refused a
  file by its *reported* length (a sysfs attribute reports one page whatever its size) and
  computed `max_bytes + 1` unsaturating, so `usize::MAX` (“no bound”) wrapped the limit to 0
  and returned an empty file. It now applies the bound to the bytes read and saturates the
  `+ 1`.
- **An overflowing iocost sum is unreadable, not clamped** (`HOB-25`). `io.stat`'s counter
  sums used `checked_add` while its iocost sums used `saturating_add`, publishing
  `u64::MAX` as a measured cost. Both refuse now.
- **An unknown device topology is a state, not a bottom device** (`HOB-20`). The sampler
  treated an unclassified device as bottom
  (`collect.rs::is_bottom`, `last.get(dev).copied().unwrap_or(true)`), so a failed `/sys`
  read summed a stacked device's counters into the cgroup aggregate. `cgroup::TopologySource`
  makes the answer explicit and an `Unknown` device makes every `io.*` aggregate
  `unknown-topology` with no values at all.
- **An unparsable field no longer becomes a zero or a vanished metric.** A `cpu.stat`,
  `memory.stat`, `memory.events`, PSI, `statm` or `status` field that does not parse is
  published under its own name with the `unreadable` code; one bad `io.stat` counter makes
  the whole sum unavailable (a partial total would be a wrong number that looks right); a
  `max` limit is a `unsupported` state with the kernel's own text kept, not `-1`.

### Changed

- **`tools/verify-crate-cache.py`: the image lock identity is the registry-set digest.** One
  canonicalisation function (the lock `version`, then one sorted line per package that has a
  `source`: `name version checksum`, plus the source text when it is not crates.io) replaces
  the whole-file sha256, so a path dependency between workspace crates (which only rewrites
  workspace members' `dependencies` arrays) no longer needs an image rebuild; a changed,
  added or removed registry/git package, checksum, source or lock version still does, and
  non-crates.io sources are still refused. New mode `--print-registry-digest --lock PATH`;
  the wrapper flag `--baked-lock-sha256` became `--baked-registry-sha256`. This is source
  only: it takes effect for the lanes when a new image tag is built on a host and
  `run-gate.root.toml` is switched to it (HOB-3/17/27/39 stay OPEN until then).
- `docs/HAND-MUTANTS-M1B.md` and `docs/hand-mutants/m1b-mutants.py`: twenty-six patches
  became thirty-one (the fix round adds the `stat` mapping's three patches, the iocost
  overflow and the unbounded read).
- `coverage-floors.toml`: `crates/kernel` raised from the M0 bootstrap 90%/85% to 92% line /
  93% region after its own tests reached 92.19% line / 93.50% region. No floor was lowered
  and no code is excluded from coverage. The fix round adds tests without changing the
  floor: a raised floor is re-measured, never lowered to fit.
- `coverage-floors.toml`: `crates/store` raised from the M0 bootstrap 90%/85% to 94% line /
  93% region after its own tests reached 94.87% line / 93.90% region (108 unit tests plus the
  segment-fixture and quality-table integration tests). Five packages carry the bootstrap
  floor now, because the remaining crates are still the compilable scaffold.
- `docs/CONSUMERS.md` gains a worked `Store` adoption section (configure, open, submit,
  `write_pending`, `evict`, `seal_all`, `range`) with the refusal names a caller meets, and
  `docs/DESIGN-GUIDE.md` gains the five decisions behind the `store`'s shape (one write
  module, four bounds with a typed answer each, corpus-bound payload kinds, whole-segment
  eviction oldest-first with a sequence water mark, and a torn tail as a state). `README.md`
  describes the crate and points at the format document.
- `docs/SEGMENT-FORMAT.md` is the new normative document for the byte layout; both
  implementations are written from it rather than from each other.
- The `proto` refusal vocabulary grows from 40 to 42 names (`not-a-block`, `section-order`);
  the count test, the display test, the fixture comparison in both languages and the crate
  docs were updated together. `fixtures/wire` grows from 23 to 26 cases (7 valid, 19 refusals).

## [M1]

### Added

- **`model` crate (M1):** incarnation identity (`BootId`, `Epoch`, `CgroupKey` keyed by
  `(mount id, inode)`, `ProcessKey`, `IncarnationRegistry` with two-absent-scan retirement
  and rename-preserving relabel), the closed eight-code quality vocabulary, good/bad-quality
  values (`Sample::missing` refuses `Quality::Ok`), the metric registry with
  unit/kind/scope/source/aggregation (aggregation derived from kind), and counter
  classification plus rate derivation over the actual monotonic interval (`dt = 0`,
  `dt < 0`, reset, missing and wrong-type outcomes as typed refusals).
- **`proto` crate (M1):** the wire codec — frame header, section directory, fixed-width
  typed little-endian columns (raw `u64` counters and a mandatory `u8` quality per value),
  live rows, history buckets, and bounded per-segment dictionaries with generation
  retirement. Every decode failure is one typed `ProtoError`; nothing panics, nothing
  returns a partial block, and unknown or future section versions are refused.
- **Shared golden fixtures:** `fixtures/wire/` (26 cases: u64 boundaries, `-0.0`/NaN
  placeholder, Unicode dictionary, every quality code, empty selection, unknown optional
  section, and nineteen refusal cases) and `fixtures/rates/rates.json` (18 hand-computed
  classifier and rate cases). Bind the Rust tests and an independent stdlib-only Python
  reference codec (`python/reference/`, `python/tests/`) to one contract.
- **SP-4 measurement** (`crates/proto/examples/sp4_wire_size.rs`, deterministic,
  criterion-free) with published numbers in `docs/SP-4-WIRE-SIZES.md`: at 100/1,000
  cgroups, 82.7/80.2 B per row for the column codec against the sampler TLV's 50.1/50.9 B
  (per-tick deltas) and 62.1/62.9 B (raw counters). Confirms decision D4.
- **`KNOWN_ISSUES_TODO_BACKLOG.md`** with the first six `HOB-` entries (counter wrap
  policy, unmeasured CPU/allocations, the duplicated-vocabulary consequence of the locked
  image, the missing registered `conformance` lane, history min/max/mean, and the unused
  `DICT` frame type).
- **Hand-mutation record** `docs/HAND-MUTANTS-M1.md`: twenty-one mutations across the
  classifier boundaries and codec bounds, all killed in the lane image, each restored and
  SHA-256-verified; the two first-pass survivors became tests.
- **Docs:** `README.md`, `docs/DESIGN-GUIDE.md` and `docs/CONSUMERS.md` rewritten for the
  functional crates (contracts, rejected alternatives, paste-ready examples).

### Fixed

- **Dictionary decode no longer de-duplicates the wire list** (`duplicate-dictionary-entry`).
  `intern` merges a repeated identity (correct for a live table, wrong on the wire), so a
  body repeating an incarnation, metric name, label or free text came back with fewer
  entries and every later index shifted. The decoder now refuses when the entry it interned
  is not at the position the sender wrote; the Python reference decoder refuses the same
  input with the same kind name, and `duplicate-dictionary-entry` is a committed fixture.
- **A metric aggregation mask must equal the mask its kind derives**
  (`dictionary-entry-mask`). Previously only extra bits were refused, so a strict subset
  decoded and was silently widened on re-encode. Both languages now refuse a subset or an
  extra bit, pinned by the `bad-metric-mask` fixture.
- **Dictionary references are checked for kind, not only for range.** A row must name an
  incarnation (`row-reference-not-entity`), a column a metric
  (`column-reference-not-metric`), and a column's declared kind must agree with the
  dictionary's metric facts (`column-kind-mismatch`); fixtures pin all three in both
  languages.
- **Values without an address are refused** (`no-rows-or-time`): a non-zero row count
  requires a rows or a time section, on the encoder and the decoder side. Rows and buckets
  are now exclusive **by presence**, so a block carrying both sections is refused even
  when both are empty (`n_rows == 0`), which the count-based check missed.
- `Column::sample` computes its reported need with `checked_add` (a caller-supplied
  `usize::MAX` row was a debug-build overflow), and `decode_block` takes the directory slice
  with `.get(..)` instead of an index.
- The refusal vocabulary grows from 34 to 40 names; the count test, the display test, the
  fixture comparison in both languages and the crate docs were updated together.
- `Dictionary::encode_with` writes an exact entry list (tests and fixtures need to express
  wire lists a live table would never build); `Dictionary::encode` is unchanged.
- Fix-round hand-mutation campaign stored as a replayable script
  (`docs/hand-mutants/fix-round-mutants.py`, 16 patches, all killed) and the backlog grew
  to `HOB-13`.

### Changed

- `coverage-floors.toml`: `crates/model` raised to 97% line / 96% region and
  `crates/proto` to 98% line / 96% region (measured 98.06%/97.90% and 99.43%/97.69%).
  No floor was lowered and no code is excluded from coverage.

## [M0]

### Added

- Added the minimal hostobs Cargo workspace skeleton for the planned sampler crate split.
- Added offline Cargo settings and a lock-bound launcher that checks the baked crate cache before invoking Cargo with `--locked`.
- Added zero-retry nextest configuration, cargo-deny policy, source lint tooling, and per-crate coverage-floor checks at 90% line and 85% region.
- Added the `lint` and `selftest` Run-Gate lanes using the `tester-unified-rust` image; both run without network access.
