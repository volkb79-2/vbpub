# The hostobs segment format (version 1)

Normative description of what `crates/store` writes and reads. Two independent implementations
follow this document: the Rust reader/writer in `crates/store/src/` and the Python reference in
`python/reference/hostobs_segment_ref.py`. Neither is written from the other, and the committed
fixtures under `hostobs/fixtures/segment/` are the contract between them:

* `python/reference/make_segment_fixtures.py` generates `<case>.seg` from a description using the
  **Python** encoder (the independent one);
* `crates/store/tests/golden_segment.rs` decodes those bytes with the Rust reader and re-derives
  the same facts, and re-encodes them field by field with the Rust writer;
* `python/tests/test_reference.py` decodes them and re-encodes them byte-identically.

A layout change that only one side makes turns one of those red. Do not edit a `.seg` by hand:
change `make_segment_fixtures.py` and regenerate.

## Why an envelope at all

`proto` already has a frame: a wire frame is validated by its own lengths, types and section
directory. A *file* is different. A crash, a full disk, or a `SIGKILL` between two writes can leave
a record half-written, and every frame after it is still perfectly readable — which is exactly the
shape that makes a naive reader serve a short-but-valid block as data. Plan v4 §C.5 therefore asks
for a checksum **per block**, and this envelope supplies it: each record carries its own total
length (so a reader knows precisely how many bytes are missing), its own body checksum (so it knows
whether the bytes it found are the bytes that were written), and its own sequence number (so a
gap or a reordering is detected rather than absorbed).

The store never looks *inside* a payload. It checks the four frame-header facts of plan v4 §B.1
(type, declared length, sectioned flag, zero reserved field) and hands the bytes to `proto` when a
consumer reads them back. That split is why `store` needs no dependency on `proto` (see
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md) `HOB-27`).

## File naming

    <tier>-<start_s>-<seq>.seg          sealed segment
    <tier>-<start_s>-<seq>.seg.open     open segment (being appended to)
    <tier>-<start_s>-<seq>.seg.corrupt  a quarantined segment (never read as data)
    <tier>-<start_s>-<seq>.seg.tmp      the transient name of an in-progress seal

* `<tier>` is the tier's configured name, which may contain `-`, `_` and `.` (focus tiers are named
  after their subject), but never starts with `.`; it is written verbatim and parsed from the
  **right**, so a tier called `lane-7` is not mistaken for a field.
* `<start_s>` is the first second of the segment's own step window, aligned to the tier's
  `segment_s`: a record at 119 s belongs to the segment aligned at 60 s when `segment_s = 60`.
* `<seq>` is a per-tier high-water mark, monotone for the life of the tier directory. It is **not**
  reused after an eviction or a quarantine: a name that came back would give a new segment the
  identity of a file an operator may still hold.

Only a name that parses *and* whose first 64 bytes are this store's header for this tier is a
segment the store may delete. Anything else in the directory — a foreign file, a symbolic link, a
directory, an interrupted `.tmp` — is reported (`TierScan::notes`) and left untouched.

## Layout

Little-endian, every field fixed width. Numbers in brackets are byte offsets.

```
SEGMENT := HEADER RECORD* FOOTER?

HEADER (64)
  0   magic7 "HOBSEG\x01"        7   u8  0                     8   u16 version = 1
 10   u16 header_len = 64       12   u64 seq                  20   u64 epoch
 28   u32 dict_generation       32   u8  kind                 33   u8  payload
 34   u16 0                     36   i64 start_real_ns        44   u32 max_payload
 48   u32 max_incarnations      52   u32 0                    56   u32 header_crc
 60   u32 0

RECORD (48 + body + pad),  pad = (8 - body % 8) % 8, zero bytes
  body := id_table[8 * n_incarnations] || payload
  0   magic4 "HOBR"              4   u32 record_len           8   u64 seq
 16   u8  kind                  17   u8  flags               18   u16 0
 20   u32 body_len              24   i64 real_ns             32   u16 n_incarnations
 34   u16 0                     36   u32 body_crc            40   u8  pad
 41   u8[3] 0                   44   u32 header_crc

FOOTER (64)
  0   magic4 "HOBF"              4   u32 footer_len = 64      8   u64 seq
 16   u64 record_count          24   u64 end_offset          32   u32 dict_generation
 36   u32 0                     40   i64 first_real_ns        48   i64 last_real_ns
 56   u32 footer_crc             60   u32 0
```

`header_crc` covers the 56 bytes before it (the header) or the 44 bytes before it (the record);
`body_crc` covers the record's whole body, **the id table and the payload together**;
`footer_crc` covers the 56 bytes before it. A checksum that covered the field it lives in could
never be checked.

### The id table lives inside the record

The store must answer "which incarnations can any stored block still reference?" from the files
alone, without decoding a payload — that answer is what drives dictionary retirement (plan v4
§A.4, "inactive incs retire with the segments that reference them"), so it has to survive a restart
and cannot live only in RAM. Eight bytes per id is the cost of that property, and it is why a
record's length is `48 + 8·n + payload + pad` rather than `48 + payload`.

Retirement itself is by **reachability**, never by time: an id is retired only when no segment in
any tier still names it. A segment reaching its `keep_s` or byte-cap bound leaves whole, so its
ids become unreachable at exactly the moment its bytes do.

### kind, payload, epoch and dict_generation

| byte | value | meaning |
|---|---|---|
| `kind` | 1 | `history`: sampled column blocks (hot, warm, cold, focus tiers) |
| | 2 | `events`: typed events and spans |
| `payload` | 1 | the payload is a `proto` BLOCK frame (frame type 5) |
| | 2 | the payload is a `proto` EVT frame (frame type 3) |

`kind` and `payload` must agree, in both directions, and are checked on encode *and* on decode: a
history segment can never declare an events payload, and a record's kind must be the one its
segment's corpus carries. A mismatch is `tier-kind-mismatch` (header) or `record-corpus` (record),
never a silent reinterpretation.

`epoch` and `dict_generation` are **per-segment** facts, not global ones. A segment written against
dictionary generation 2 is readable after a rollover to generation 3, and a consumer that decodes
a payload it found in that segment knows which generation its indices belong to. The footer
repeats the generation so a sealed segment's self-description is closed.

## Tail states: how a crash is represented

Reading a segment yields a *state*, not just facts:

| state | meaning | what the store does |
|---|---|---|
| `complete` | the bytes end exactly after the last record (or after a verified footer) | serve it |
| `incomplete` | a record starts and the file ends before its declared length | truncate back to the last good record, report it, then append behind the repaired tail |
| `corrupt` | the bytes at an offset are not a valid record: magic, header CRC, body CRC, sequence, padding or payload facts | quarantine the whole file as `.corrupt`, report it, keep the file for an operator |

An incomplete tail is a *repair*, not a loss: nothing that was completed is discarded. A corrupt
segment is quarantined rather than deleted, because its bytes are the only evidence of what went
wrong, and a truncated one would hide the corrupt records that precede the damage.

Nothing in a reader panics on what it finds. Every refusal is a typed error with a stable machine
name (`StoreError::kind`), and the Python reference uses the same names, so a disagreement between
the implementations is a named difference rather than "it failed".

## Seal: atomic, by temp name

Sealing a segment must never leave a file whose footer lies about it, and must never make a
half-copied file look sealed. The order is fixed:

1. append the last buffered records and fsync the open file;
2. copy the open file into `<sealed>.tmp` in 64 KiB chunks (bounded by the tier's `segment_bytes`,
   so a corrupt or foreign file is refused rather than duplicated into a trusted name);
3. append the 64-byte footer to `<sealed>.tmp` and fsync it;
4. `rename` `<sealed>.tmp` → `<sealed>` (a rename that would overwrite an existing name is refused);
5. fsync the **directory**, then unlink the `.seg.open` name.

A crash before step 4 leaves either the open file with its records (repaired and continued at the
next open) or a `.tmp` orphan, which is reported and left alone. A crash after step 4 leaves a
complete sealed segment and possibly a stale `.seg.open`; the scan prefers the sealed name it
verified. The `.tmp` name exists because a partially written copy of a file under its *final* name
would be a segment whose header and footer disagree.

## Eviction: whole segments, oldest first

Every tier is bounded by `keep_s` **and** `cap_bytes`, whichever binds first. The unit of eviction
is the whole segment: cutting inside one would leave a file whose footer no longer describes it.
Ordering is by the name's own `start_s`, then its `seq` — never by insertion order, so a clock step
cannot make a newer segment look older.

`keep_s` compares a segment's **end** to the window: a segment whose end is exactly `now - keep_s`
is still inside it. `cap_bytes` is strict, and if the only remaining segment is the open one and it
alone exceeds the cap, it goes too — with its handle closed first, so nothing is ever appended
behind a name that no longer exists.

## Bounds and where they come from

| fact | source | bound |
|---|---|---|
| one record's payload | `proto` §B.1 | the tier's `max_payload` (1 MiB default, 16 MiB is the frame maximum) |
| ids in one record | the tier's `max_incarnations` | 1024 default |
| one segment file | `TierConfig::segment_bytes` | `max_payload × 64`, at least 64 KiB |
| a scan of one file | the reader's `max_file_bytes` | 16 MiB default; a file larger is `file-too-large` |
| records one scan walks | `segment::SCAN_RECORD_BOUND` | 2^20 |
| names one directory is scanned for | `tier::MAX_SEGMENTS` | 4096 |
| a range answer | the caller's `max_records` and `max_bytes` | refused when `max_records` is zero |

A reader never allocates more than the file it was given, a tier's memory is proportional to its
number of segments (never to their bytes), and a query's memory is the caller's two bounds.

## Fixtures

| case | shape it pins |
|---|---|
| `open-history` | two block records, no footer, two incarnations |
| `sealed-history` | three records and the footer that closes them, two payload shapes |
| `sealed-empty` | a sealed segment with no records: count 0, offsets at the header |
| `open-events` | the events corpus: one EVT record, `kind = events` |
| `open-second-generation` | a later epoch and dictionary generation, four ids |

Regenerate with `python3 python/reference/make_segment_fixtures.py` from `hostobs/`.

## Version byte

Byte 8 of the header is the version (`u16`), and byte 7 is reserved zero so a later format can
widen the magic without moving any other field. Version 1 is the only version that exists; a
reader refuses any other with `header-version` rather than guessing. A change that cannot be
expressed as an additive section in the payload is a new version, and both implementations change
in the same commit.
