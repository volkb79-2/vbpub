# hostobs/1 wire protocol (normative)

Status: normative for `proto` (P2), `python/hostobs_client` (P3), `client` and the probe's serving side
(P5). Plan: [`docs/design/hostobs/PLAN-v4-2026-11-08.md`](../../docs/design/hostobs/PLAN-v4-2026-11-08.md)
§A.2, §B, §C, §D. The BLOCK codec of §3 is the implemented one (`crates/proto/src/frame.rs`,
`dict.rs`, `quality.rs`, `error.rs`; fixtures `fixtures/wire/`); where this document and the
code disagree, §13 lists it. MUST/SHOULD/MAY are RFC 2119. All integers are little-endian.
Offsets are bytes from the start of the named structure. `align8(x)` = smallest multiple of 8 ≥ x.

## 1. Connection

1.1 **Transport.** `AF_UNIX` `SOCK_STREAM` sockets from systemd `.socket` units (plan §A.2): observe,
operator, observer (served by the probe) and control (served by act). Access is the socket's file
permission; issuer identity and peer binding come from `SO_PEERCRED`/`SO_PEERPIDFD` on the
**server** side only; a client never sends a uid, pid or cgroup and a request key naming one is
`bad-request`. Because the listening socket is held by systemd, a probe restart does not refuse new
connections; it closes every accepted connection (EOF or `ECONNRESET` for the client).

1.2 **Who speaks first.** The client. The server sends nothing before the client's first byte; a
connection over a cap is closed without a byte (§5.4 `refuse`), and one that sends nothing is closed
silently at its hello deadline (§5.1).

1.3 **Mode by first byte** (`crates/proto/src/frame.rs` `detect_mode`):

| First bytes from the client | Mode | Server action |
|---|---|---|
| `0x7B` (`{`) | JSON lines (§8) for the whole connection, observer and control only | read lines; at observe and operator write one line `{"ok":false,"corr":0,"error":{"code":"binary-required","message":"...","detail":{}}}\n`, close (D-W51) |
| `48 4F 42 01` (`HOB\x01`) `u16 major` `u16 minor` (8 B) | binary | §1.4 |
| 1-7 bytes whose first `min(have, 4)` match `HOB\x01` (bytes 5-8 are the version, any value) | undecided | wait until the hello deadline (§5.1), then close silently |
| any other byte, or a byte that diverges from `HOB\x01` | refused | write one line `{"ok":false,"corr":0,"error":{"code":"unknown-mode","message":"...","detail":{}}}\n`, close |

1.4 **Binary handshake.**
1. Client → `HOB\x01` + `u16 major = 1` + `u16 minor = C` (its highest minor).
2. Server → `HOB\x01` + `u16 major = S_major` + `u16 minor = N` (8 B). If `major == 1`, `N = min(C, S_minor)`
   is the **negotiated minor**: neither side sends a section, flag, frame type, verb key or reply key
   introduced after `N`. If the client's major is not one the server speaks, the server writes its
   prefix with its own highest major and minor, then closes; the client reports `unsupported-version`.
   This 8-byte prefix is major-independent forever.
3. Client → `REQ` `hello` (§4.4) as its first frame, `corr = 1`. Any other first verb: server sends
   `REP corr=<that corr> ok:false hello-required` and closes; `hello` with `corr ≠ 1` is `bad-frame`
   (REP `corr=0`), close. The whole handshake completes within the hello deadline (§5.1).
4. Server → `REP` for hello. Only now are other verbs accepted.

The prefix is 8 bytes; every later byte belongs to a 12-byte-header frame (§2).

1.5 **Half-close.** A client `shutdown(SHUT_WR)` means "no further requests": every request is already
answered (§5.1, no request waits); the server ends every live subscription with `END` reason
`client-eof` (§2.2) and closes once its queue is written. A server that closes for a code writes the `closing`
EVT under the conditions of §5.4 `Closing`; a client MUST NOT depend on receiving it.

## 2. Frames

2.1 **Header (12 B).**

| Off | Type | Field | Rule |
|---|---|---|---|
| 0 | u32 | `len` | whole frame incl. header; `12 ≤ len ≤ max_frame` (§5.1); a BLOCK's `len` is a multiple of 8 |
| 4 | u8 | `type` | 1 REQ, 2 REP, 3 EVT, 4 DICT, 5 BLOCK, 6 END; other → `bad-frame` |
| 5 | u8 | `flags` | bit0 `SECTIONED`; bits 1-7 MUST be 0 |
| 6 | u16 | reserved | MUST be 0 |
| 8 | u32 | `corr` | §2.3 |

`SECTIONED` MUST be set on BLOCK and MUST be clear on REQ, REP, EVT, END. A mismatch is `bad-frame`.

2.2 **Payloads.**

| Type | Direction | Payload |
|---|---|---|
| REQ | client → server | one UTF-8 JSON object `{"verb": str, "args": object}`, exactly those keys |
| REP | server → client | `{"ok": true, "result": object}` or the error envelope (§4.1) |
| EVT | server → client | `{"event": str, ...}`; events in 1.0: `closing` `{"event":"closing","reason": <§5.4 fatal code>}` |
| DICT | — | reserved; a 1.0 peer MUST NOT send it; receiving one is `bad-frame` (§13 D-W6) |
| BLOCK | server → client | the column block of §3 |
| END | server → client | `{"ok": true, "blocks": u32, "dropped": u32, "next": str\|null, "reason": str}` or `{"ok": false, "blocks": u32, "dropped": u32, "error": {...}}`, exactly these keys |

END keys: `blocks` = BLOCK frames of the stream sent to this client before the END (§5.3: a live
block counted when pinned, a query block when queued, so query blocks still queued ahead of the END
are included), all of which precede the END; `dropped` = blocks of the stream dropped after the last pinned one (always 0 for query
streams); `next` = continuation cursor (`series_batch` `page` only, else `null`); `reason` is one of
`complete` (`current`, `snapshot_at`, `export`, last `series_batch` page), `page` (`series_batch` with
`next`), `unsubscribed`, `client-eof`. `END ok:false` carries any non-fatal §10 code; in M2 the server
produces `internal` or `budget {"pool":"rows"}` there. A server shutdown ends no stream: it closes the
connection (§5.4).

A frame type sent in the wrong direction is `bad-frame`. JSON payloads carry no BOM and no trailing
bytes after the object; integers follow §8.3.

2.3 **`corr`.** The client allocates `corr` for each REQ: non-zero, unique among its unanswered requests
(final REP or END not yet read) and live subscriptions, and SHOULD increase by one per request. Every
REP, BLOCK and END answering a request carries its `corr`; every live BLOCK of a subscription carries
the `subscribe` request's `corr` until that subscription's END. `corr = 0` is reserved for
server-originated EVT and for the connection-fatal REP of a frame whose own `corr` cannot be trusted.
A REQ with `corr = 0` or the `corr` of a live subscription (the only `corr` the server holds, D-W49) is
`bad-frame` (REP `corr = 0`, close).

2.4 **Reply shapes.** A JSON verb answers with exactly one REP. A block verb answers
`REP ok:true` (stream header), then 0..n BLOCK, then exactly one END. A block verb that fails before
its first REP answers one `REP ok:false` and no END; one that fails after it ends with `END ok:false`.

## 3. Column block (implemented layout, fixed)

3.1 **Frame and directory** (`frame.rs` `encode_frame`/`decode_block`):

| Frame offset | Type | Field |
|---|---|---|
| 0 | 12 B | frame header, `type = 5`, `flags = 0x01` |
| 12 | u16 | `n_sec` |
| 14 | u16 | reserved, MUST be 0 (`section-reserved`) |
| 16 + 8·i | u16 `kind`, u8 `ver`, u8 `flags` (bit0 = required, bits 1-7 MUST be 0), u32 `len` | directory entry i |
| 16 + 8·n_sec | — | bodies, in directory order, contiguous, no padding between them |

Two decoder rules make the layout guarantees hold for **received** frames, not only for encoded ones:
- every directory entry's `len` is a multiple of 8, unknown optional sections included
  (`section-length`);
- directory entry 0 is the header (kind 1). It is checked after the rank rule of §3.2 (order in §3.10):
  an unknown kind at entry 0 is `section-order` at index 0; a known non-header kind there is already a
  rank violation at the header's index (the header has the lowest rank), or `missing-section` if the
  frame has no header, so every accepted frame starts with the header.
Hence **every body starts at a frame offset ≡ 0 (mod 8)**, a BLOCK's `len` is a multiple of 8, the
header body sits at `16 + 8·n_sec`, and the frame ends exactly after the last body (`trailing-bytes`
otherwise). Both decoders enforce the two rules (`HOB-51`, §13 V-11; fixtures
`unaligned-optional-section` and `optional-section-before-header`). Three more decoder rules:
the directory's `8·n_sec` bytes must be present before storage is reserved for them (`truncated`,
context "block directory"; `HOB-50`); the header and the subject directory entries MUST carry the
required flag (bit 0), and a clear flag is `unknown-section-flags` with `kind_code` the section's kind
and `flags` the entry's flag byte (fixtures `header-required-flag-clear`,
`subject-required-flag-clear`); only the first header section's body is decoded, so a second header is
`duplicate-section` whatever its body holds (fixture `duplicate-header-bad-second-body`). In
`fixtures/wire/live-block.bin` (344 B, 5
sections: header, dictionary, rows, 2 columns) the bodies start at offset 56 and `dropped` sits at 128.

3.2 **Sections** (version 1 each; a known kind with another version is refused even when optional,
`unsupported-section-version`; an unknown kind is skipped by `len` when optional and refused when
required, `unsupported-required-section`):

| kind | name | required | count | canonical rank |
|---|---|---|---|---|
| 1 | header | yes | exactly 1 | 0 |
| 5 | dictionary | no | ≤ 1 | 1 |
| 6 | subject (minor ≥ 1, §3.8) | yes when present | ≤ 1 | 2 |
| 2 | rows | no | ≤ 1 | 3 |
| 4 | time | no | ≤ 1 | 3 |
| 3 | columns | no | 0..n, one per metric (`conflicting-column`) | 4 |

Known sections MUST appear in non-decreasing rank (`section-order`); unknown kinds are exempt. Rows and
time are exclusive by presence (`rows-and-time`); `n_rows > 0` needs one of them (`no-rows-or-time`).

3.3 **Header body (kind 1, 80 B)**: `boot_id[16]`@0, u64 `epoch`@16, u64 `seq`@24, u64
`mono_start_ns`@32, u64 `mono_end_ns`@40, u64 `boot_ns`@48, i64 `real_ns`@56, u32 `dict_gen`@64,
u32 `n_rows`@68, u32 `dropped`@72, u32 `flags`@76 (MUST be 0, `unknown-section-flags`). The header is
the first body (§3.1), so `dropped` sits at frame offset `88 + 8·n_sec` and `corr` at 8: the two
per-client fields (§5.3). Live and `snapshot_at` blocks: `seq` is the probe tick number within the
epoch, `real_ns` the tick's (or bucket's) wall time; `dropped` counts this stream's blocks dropped for
this client since its previous pinned block (saturating, §5.3). History blocks: §4.9.

3.4 **Rows body (kind 2)**: `n_rows × u32 ent` @0 (dictionary index, MUST name an Entity), then
`n_rows × u32 t_off_us` @4·n_rows (offset from `mono_start_ns`). Length exactly `8·n_rows`.

3.5 **Column body (kind 3)**: u32 `metric`@0 (MUST name a Metric), u8 `type`@4 (1 u64, 2 i64, 3 f64),
u8 `kind`@5 (0 gauge, 1 counter, 2 state; MUST equal the dictionary's kind, `column-kind-mismatch`),
u16 reserved@6 = 0 (`column-reserved`), `n_rows × 8 B` values @8, `n_rows × u8` quality @`8 + 8·n_rows`,
zero pad (a non-zero pad byte is `non-zero-reserved`, `value` the first such byte; the same holds for
the pad of the time body and the dictionary body). Length exactly `align8(8 + 9·n_rows)`. A row whose
quality is not `ok` keeps its 8 bytes (any content, e.g. NaN bits) and they MUST NOT be read as a
measurement.

3.6 **Time body (kind 4, history)**: `n × i64 bucket_real_ns` @0, `n × u32 covered_ms` @8n,
`n × u16 samples` @12n, `n × u8 agg` @14n (0 raw, 1 last, 2 delta, 3 mean, 4 min, 5 max), zero pad;
`n = n_rows`. Length exactly **`align8(15·n)`** (`TIME_BUCKET_BYTES = 15`; five buckets are 80
bytes, fixture `history-five-buckets`, §13 V-1; layout version 1, no minor). The `agg` bytes are
judged (`unknown-aggregation`) before the pad (`non-zero-reserved`), so a body with both defects is
`unknown-aggregation` (fixture `time-bad-agg-and-pad`). Every bucket's `agg` MUST be permitted
by every column's metric mask (§3.7) — counters raw/last/delta, gauges last/mean/min/max, state last
(`aggregation-not-permitted`, `HOB-47`). The mask is the column's own kind's, so the check needs no
dictionary: it runs for an in-frame dictionary, a held one and none at all, and the encoders refuse
it too. Gauge min/max/mean are separate history blocks of the same
subject, one aggregation per block, packed by §4.9 (this is how `HOB-5` is served, no wire change).

3.7 **Dictionary body (kind 5)** (`dict.rs` `encode`/`decode`): u32 `generation`@0, u32 `count`@4
(≤ 65,536, `dictionary-too-large`), then `count` packed entries, then zero pad to 8. Entry: u8 `kind`
(0 entity, 1 metric, 2 label, 3 free), u8 reserved = 0, u16 `text_len` (≤ 256), kind fields
(entity: u64 `inc`; metric: u8 unit, u8 kind, u8 scope, u8 source, u16 mask which MUST equal the kind's
derived mask; label: u8 kind, u8 source; free: none), then `text_len` bytes of UTF-8 without bytes
< 0x20 or 0x7F. Entries are unaligned. Entry *i* is index *i*. Identity is the entity's `inc`, the
metric's name, the label's `(kind, source, text)`, the free entry's text; a repeated identity is
`duplicate-dictionary-entry` **whatever the other fields hold** (a metric name repeated with other
facts, an `inc` repeated with another label, an exact repeat; `HOB-52`, §13 V-12); Rust
`Dictionary::decode` maps the `conflicting-column` that `intern` reports for a metric name repeated
with other facts to `duplicate-dictionary-entry` at that entry's index (`intern` itself keeps
`conflicting-column` for a live table). Both decoders judge one entry in this order, the Rust cursor's:
`dictionary-entry-reserved`, `dictionary-entry-too-long`, `unknown-dictionary-kind`, the metric's
unit, kind (`unknown-column-kind`), scope and source codes (`dictionary-entry-unknown-code`), the mask
(`dictionary-entry-mask`), the text (`truncated` when the body ends first, `dictionary-entry-not-utf8`),
a repeated identity (`duplicate-dictionary-entry`), then control bytes in the text
(`dictionary-entry-control`, `index` and byte `offset`); the pad is `non-zero-reserved` and a longer body
`trailing-bytes`. A count above 65,536 is `dictionary-too-large` before any entry is read, and every
short read is `truncated` with context "dictionary section": no hostile body raises anything but a
typed refusal in either language (fixtures `dictionary-count-too-large`, `dictionary-entry-control`,
`dictionary-text-past-body`, `dictionary-invalid-utf8`, `dictionary-short-entry-header`,
`dictionary-nonzero-pad`). Code tables: `dict.rs` lines 48-155.

3.8 **Subject body (kind 6, ver 1, minor ≥ 1, required flag set)**: u32 `ent`@0 (dictionary index,
MUST name an Entity), u32 reserved@4 = 0. Allowed only with a time section (`subject-without-time`);
it names the one incarnation a history block describes. A minor-0 reader refuses it, which is correct:
a history block it cannot attribute must not be read. Both codecs implement it (`HOB-48`). The
directory's required flag is written by the encoders and enforced on decode: a clear flag is
`unknown-section-flags` (`kind_code` 6, `flags` the entry's flag byte, fixture
`subject-required-flag-clear`; the section version is judged first, fixture
`header-version-and-required-flag-clear`). The decoder takes no negotiated minor: a kind 6 section is
decoded whatever minor was negotiated, and the rule that a minor-0 peer is never sent one belongs to the
stream layer (`HOB-54`). A body that is not 8 bytes is `section-length`, a non-zero reserved
word `unknown-section-flags` (`flags` the word), a subject past the dictionary `dangling-dictionary-ref`,
one that is not an entity `row-reference-not-entity` (fixtures `subject-wrong-length`,
`subject-reserved-word`, `subject-dangling`, `subject-not-entity`). The encoder cannot check that the
subject names an entity when the block carries no dictionary, so only the decoder does.

3.9 **Quality** (`quality.rs`, `fixtures/wire/quality.json`): 0 ok, 1 absent, 2 unreadable, 3 stale,
4 reset, 5 not-sampled, 6 unsupported, 7 unknown-topology; any other byte is `unknown-quality`. Only 0
is a measurement. Codes are append-only and announced in `hello.quality_codes`.

3.10 **Decoder error kinds** are the `ProtoError::kind()` names (`ProtoError::kind`), e.g.
`truncated`, `oversized-frame`, `section-order`, `stale-dictionary-generation`,
`aggregation-not-permitted`, `subject-without-time` (46 kinds in all: `deadline` is the reader's
completion-age kind, `generation-regressed` the stream layer's, §6.2). They are local outcomes
(fixtures `expect_error.kind`), never sent on the wire; §5.2 maps the reader's ones to wire codes.
There are exactly two vocabularies: wire codes (§10, sent) and decoder kinds (local). Client APIs
raise one of them (P3 `ServerError(code)` / `WireError(kind)`, plus `ConnectionLost` for EOF or reset);
an unknown required section reaches the caller as `WireError("unsupported-required-section")`.

**Check precedence** (the first failing check names the error; fixtures pin it): frame header fixed
fields (`unknown-frame-type`, `unknown-frame-flags`, `non-zero-reserved`), then `len` bounds
(`oversized-frame`, `truncated` for `len < 12`), then `not-a-block`, then buffer length
(`truncated`, `trailing-bytes`), then `SECTIONED` (`missing-section`), then directory (`truncated`,
`section-reserved`, `section-length`, `section-order`), then per-section checks in directory order,
then cross-section checks. Within `section-order`, two checks in this order: (1) the rank check, one
pass in directory order, reports the first known entry whose rank is lower than the highest rank before
it (detail `index`, `kind_code` of that entry; unknown kinds are skipped); (2) only if (1) passed, an
unknown kind at entry 0 is reported (`index` 0, its `kind_code`). `fixtures/wire/section-order` (directory
kinds `[5, 1, 2, 3, 3]`, read from the `.bin`) therefore keeps its pinned `index: 1, kind_code: 1`.
`SECTIONED` on a frame type other than BLOCK is one of the fixed fields (`unknown-frame-flags`,
`bad-frame` on the wire), so it is judged before `not-a-block`. Both decoders follow this order
(`HOB-53`, §13 V-13).

**Fixed fields are read in wire order** (length word, type byte, flags byte, reserved word, `corr`) and
each is judged as soon as its bytes exist: a 5 to 11 byte buffer whose type byte is unknown is
`unknown-frame-type`, not `truncated`; a buffer too short for the next field is `truncated`, context
"frame header" (fixture `truncated-unknown-type`). A header with an unknown type, bad flags and a
reserved word names the type; bad flags and a reserved word name the flags.

**Pass structure** (one pass is finished before the next starts, and the first failure ends the decode):
1. frame header fixed fields, `len` bounds;
2. `not-a-block`, buffer length (`truncated`, `trailing-bytes`), `SECTIONED` (`missing-section`);
3. directory header (`truncated`, `section-reserved`), the directory's byte count (`truncated`, context
   "block directory", need `8·n_sec`, have the bytes left; an exact fit passes), then the entries;
4. every entry's `len` multiple of 8 (`section-length`), the rank rule and then entry 0
   (`section-order`);
5. body bounds: bodies laid end to end (`truncated`, context "section body"), then `trailing-bytes`;
6. directory entries in order: flags (`unknown-section-flags` for a bit other than bit 0), unknown kind
   (`unsupported-required-section` or skipped), version (`unsupported-section-version`), the required
   flag of the header and the subject (`unknown-section-flags`), and the **first** header's body
   (`section-length`, `unknown-section-flags` for its flags word); no header is `missing-section`;
7. bodies in directory order: a repeated single section (`duplicate-section`) before its body, then the
   body's own refusals (rows, time, column, dictionary, subject), `conflicting-column`;
8. cross-section checks: `rows-and-time`, `no-rows-or-time`, `subject-without-time`;
9. with a dictionary in force (in the frame, else the held one): generation
   (`stale-dictionary-generation`), the subject (`dangling-dictionary-ref`,
   `row-reference-not-entity`), each row, each column (`dangling-dictionary-ref`,
   `column-reference-not-metric`, `column-kind-mismatch`);
10. every bucket against every column's mask (`aggregation-not-permitted`), with or without a
    dictionary.

**A bare block with nothing held** (no in-frame dictionary and `held = None`) is judged by the frame
rules (passes 1 to 8) and by pass 10, whose mask is the column's own kind's. It has no generation or
reference judgement: that belongs to the stream layer, which always has a held dictionary or knows it
has none (`HOB-54`, §6.1).

## 4. Requests and replies

4.1 **Error envelope**: `{"ok": false, "error": {"code": str, "message": str, "detail": object}}`;
`detail` is always present (`{}` when empty). In JSON mode both envelopes also carry `"corr"`.

4.2 **Common types.** `u64`/`i64`/`u32` = JSON integers in range; `name` = `^[a-z][a-z0-9_.-]{0,63}$`;
`cursor` = opaque string ≤ 256 B issued by the server, bound to `(boot_id, epoch, verb, args-hash)`.
`agg-name` = `raw|last|delta|mean|min|max` (the §3.6 codes 0-5). String bounds are on the decoded
UTF-8 length.
**Selection**: `{"entities": [sel, ...] (1..16), "metrics": [metric-name, ...] (1..64)}`, `sel` is
exactly one of `{"inc": u64}`, `{"path": str ≤ 512}`, `{"prefix": str ≤ 512}`, `{"all": true}`; an empty
path or prefix is refused, and paths contain no character below 0x20 or 0x7F (as dictionary text,
§3.7). `all` must have the JSON literal `true`. A repeated `sel` or metric name, compared after JSON
decoding, is `bad-request`; the selector object, its one member and each element's JSON type use the
schema paths named by design S1. **Canonical form** (the identity of "distinct selection", §5.1):
`sel`s sorted by (variant `inc` < `path` < `prefix` < `all`, then value), metric names sorted bytewise;
two selections are the same iff their canonical forms are equal (no resolution: `{"inc":5}` and the
`path` of incarnation 5 are distinct). A selection resolving to more than `limits.max_rows` entities is
`budget` `{"pool":"rows"}`. Every object below is **strict**: a missing required key, an unknown key or
a wrong type is `bad-request`. JSON text rules (both modes): UTF-8, no duplicate key at any depth,
nesting depth ≤ 8 (the envelope object is depth 1; the bound holds for requests and for the client's
checks of REP, END and EVT), exact integers (§8.3); a violation is `bad-request` (the server's JSON
layer enforces them; `proto` has no JSON parser). The JSON text excludes the JSON-mode line terminator
(§8.1): the caller strips the `\n` before any check, and a trailing `\n` or `\r` is a trailing byte.
*Implemented* (`crates/proto/src/envelope.rs`, P2c): `check_req`, `check_rep`, `check_end` and `check_evt` scan
a payload byte-wise and return byte ranges into it (no JSON dependency). They check UTF-8, one object with
nothing before the `{` and nothing after the `}` (whitespace between tokens is allowed), string escapes,
the number grammar and nesting depth ≤ 8 (the envelope object is depth 1), then the envelope's key set,
types and `ok` literal. Duplicate keys are detected at the envelope's own level and in the error object;
duplicates in `args`, `result` and `detail` are the server's JSON layer's job, as is every schema below the
envelope. A key spelled with escapes is an unknown key, and so is a ninth key (an envelope has at most five,
`MAX_MEMBERS` is 8); a `verb` containing a backslash is refused; a JSON-mode REQ with `corr` 0 is refused
(§2.3) while a REP may carry `corr` 0 (§8.2); the END counts are exact `u32`s (§8.3). An EVT needs a string
`event`, at most 8 keys, and `closing` carries exactly `{event, reason}`. A lone surrogate escape (an
escape whose four hex digits name a surrogate) passes the scan: the server's JSON layer owns it. A violation
of a request is `bad-request` on the wire (`detail.key` `""` for a text error, else the offending key); a
violation of a REP, END or EVT is a local `EnvelopeError` of the client (the server is the faulty peer).
**Worst-case size.** A JSON encoder may escape every non-ASCII character (`\uXXXX`, a surrogate pair
for 4-byte UTF-8), so a string costs at most 3× its decoded length plus 2 quotes; control characters
are excluded. Worst selection: 16 × (`{"prefix":` 10 + 512·3 + 2 + `}` 1 + `,` 1) = 24,800 B, metrics
64 × (64 + 3) = 4,288 B, keys and punctuation < 100 B, so ≤ 29,200 B; the other `series_batch` args
(two i64, u32s, 4 agg names, a cursor ≤ 256 B ASCII) and the envelope add < 600 B: every legal
observe/operator request is < 30 KiB ≤ `max_request_bytes` = 32 KiB.
**Request precedence** (P5b design R11, implemented by `classify` in `crates/serve/src/classify.rs`; the
first failing step answers, later steps are never inspected): (1) a frame that is not a REQ:
`bad-frame`, `corr` 0, fatal (binary only); (2) binary `corr` 0 or the `corr` of a live subscription
(§2.3): `bad-frame`, `corr` 0, fatal; (3) the envelope check of this section: `bad-request` on
the frame `corr` (JSON: the recovered `corr`, §8.1), `detail.key` as above; (4) binary before `hello`: a
verb other than `hello` is `hello-required` on its own `corr`, `hello` with a `corr` other than 1 is
`bad-frame` `corr` 0 (both fatal); (5) a verb not in the table or reserved (§4.13): `unknown-verb`; (6) a
verb the level does not know: `forbidden`; (7) `hello` when already answered, either mode: `bad-request`
`{"key":"verb"}`; (8) retired (JSON mode never reaches a level with BLK verbs, §1.3); (9) a verb known
but not served by this build: `unknown-verb`; (10) `too-many-requests` (non-fatal, `detail {}`) for a
one-block verb while the connection's Query pool is not empty, or a `series_batch` at the
per-connection history cap (§5.1 "No request waits"); the one other `too-many-requests` is judged at
step 12; (11) the argument schema: `bad-request` with the schema path (a `hello` with a JSON-mode
`proto.major` other than 1 is `unsupported-version`, fatal); (12) execution, including the §5.1
refusals judged after resolution (`budget {"pool":"query"}`; for `series_batch`, the first-block
`too-many-requests`), all before the REP. So duplicate keys inside the args of an unknown verb are never inspected, and `forbidden` is
judged from the full table (a verb the build does not serve yet still answers `forbidden` at a level
that does not know it). A `corr` is free again once its final REP or END is queued.
`unsupported-feature` follows (11). A `detail.key` echoes an unknown or duplicate key only if it is at
most 64 bytes of `[A-Za-z0-9_.-]`, else the path stops at the parent, and the `message` is fixed text,
never client text.
**Integers** (the digits-only rule of §8.3, enforced
below the envelope): only the digits `0-9`, at most 20 of them, no sign (so `-0` and a negative value
for an unsigned field are refused; a signed field refuses `-0` too), no fraction, no exponent, and the
value must fit the field's own type (`proto.minor` is a `u16`: 65,536 is `bad-request`).

4.3 **Verbs by level** (✓ = served; JSON = REP only; BLK = REP + BLOCK* + END; JSON mode exists only
at observer and control, which serve no BLK verb, §1.3):

P5b-2d serves `current`, `subscribe` and `unsubscribe` at observe and operator. The `hello.verbs`
array at those levels lists exactly `hello`, `status`, `current`, `subscribe`, `unsubscribe`;
observer remains `hello`.

| Verb | observe | operator | observer | control | Shape | Since |
|---|---|---|---|---|---|---|
| hello | ✓ | ✓ | ✓ | ✓ | JSON | M2 |
| status | ✓ | ✓ | | ✓ (relayed) | JSON | M2 |
| current | ✓ | ✓ | | | BLK | M2 |
| subscribe / unsubscribe | ✓ | ✓ | | | BLK / JSON | M2 |
| snapshot_at | ✓ | ✓ | | | BLK | M2 |
| series_batch | ✓ | ✓ | | | BLK | M2 |
| events, spans | ✓ | ✓ | | ✓ (relayed, `session.spans`) | JSON | M3 |
| procs | ✓ (no cmdline) | ✓ (+cmdline) | | | JSON | M2 |
| annotate | | | ✓ (peer-bound) | ✓ (any ctx) | JSON | M3 |
| export | | ✓ | | | BLK | M6 reserved |
| resource-limits, window-open, window-close | | | ✓ | | JSON | M5 reserved |
| version, host, session.start/stop/status/watch/list/spans, gc, reload | | | | ✓ | JSON (`watch`: REP + EVT* + END) | M6 reserved |

A known verb at the wrong level is `forbidden`; an unknown verb is `unknown-verb`.

4.4 **hello** — args `{"client": str ≤ 64, "proto": {"major": 1, "minor": u16}}` (binary: `proto` MUST
equal the client's own prefix, major 1 and its highest minor C, not the negotiated N, else `bad-request`
with `detail.key` `proto.major` or `proto.minor`; JSON: a `proto.major` other than 1 is
`unsupported-version`, fatal, the only wire place the code is reachable, and `proto.minor` is the client's
C). The result carries `proto.minor` N = min(C, the server's minor), in both modes; a second `hello` on a
connection, either mode, is `bad-request` `{"key":"verb"}` (JSON mode accepts `hello` only as the first
line). `client` is 0 to 64 decoded bytes without a control character (below 0x20 or 0x7F) and without a
lone surrogate; it is a free label, never an identity, and a request key naming a uid, pid or cgroup is
`bad-request`. Result:
`{"proto": {"major": 1, "minor": N}, "level": "observe|operator|observer|control", "server": str,
"boot_id": "<uuid text>", "epoch": u64, "features": [str], "verbs": [str], "quality_codes":
[{"code": u8, "name": str}], "limits": {"max_frame_bytes": u32, "max_request_bytes": u32,
"request_deadline_ms": u32, "hello_deadline_ms": u32, "idle_ms": u32, "stall_ms": u32,
"frame_completion_ms": u32, "max_outstanding": u32, "max_subscriptions": u32,
"client_queue_bytes": u32, "query_queue_bytes": u32, "control_queue_bytes": u32, "max_reply_bytes":
u32, "max_block_bytes": u32, "max_rows": u32, "max_block_buckets": u32,
"max_page_blocks": u32, "min_interval_ms": u32, "tick_ms": u32}}`, exactly these keys; values as
§5.1, plus defaults `max_rows` 1,024 (D-W52), `max_block_buckets` 4,096, `max_page_blocks` 64,
`min_interval_ms` 1,000, `tick_ms` 1,000 (levels that serve no block verb report the block keys as
0: `max_subscriptions`, `client_queue_bytes`, `query_queue_bytes`, `max_block_bytes`, `max_rows`,
`max_block_buckets`, `max_page_blocks`, `min_interval_ms` and `tick_ms`; `verbs` lists the verbs served
at the level now). `features` ⊆ {`peak_reset`, `placement`, `damon`, `enrich`}. A client MUST NOT assume a limit it
was not given.

4.5 **status** — args `{}`. Result `{"server": str, "boot_id": str, "epoch": u64, "started_real_ns": i64,
"uptime_ms": u64, "tick": {"seq": u64, "last_mono_ns": u64, "scan_ms": u32}, "clients": {"<level>":
u32}, "queues": {"queued_bytes": u64, "dropped_blocks": u64}, "tiers": [{"name": str, "step_ms": u32,
"keep_s": u32, "bytes": u64, "cap_bytes": u64}]}`. `clients` lists the served levels (observe, operator,
observer) with the connections of each that are not closed: a closed connection that waits for its final
bytes is not counted, but it holds its §5.1 connection-cap slot until the server releases it.
`queues.dropped_blocks` is the send hub's counted drop total since the server started (coalesces,
sheds, dropped incoming Live ticks and the unpinned Live tick removed by an END); the backend's
`StatusInfo::dropped_blocks` is ignored.
`tick.last_mono_ns` is absolute `CLOCK_MONOTONIC`, so a local client can compare it with its own
clock; a tick is stalled when `now − tick.last_mono_ns > 2 × tick_ms` or its `seq` is not advancing.

4.6 **current** — args `{"selection": Selection}`. REP result `{"stream": {"tick_ms": u32}}`, one
live-shaped BLOCK (dictionary-bearing, latest tick; a Query entry, §5.3, so never dropped), END
`reason: "complete"`, all queued in the turn that reads the request. Before the REP the server stops
backend resolution at `max_rows + 1` rows, judges the dictionary-bearing frame's size before encoding
and applies the §5.1 one-block refusals; a `current` never fails after its REP.

4.7 **subscribe** — args `{"selection": Selection, "interval_ms": u32}`. The interval is accepted
without rounding only when `tick_ms > 0`, it is a multiple of `tick_ms`, it is at least
`max(min_interval_ms, tick_ms)` and it is at most 60,000 ms; the REP echoes the exact requested value.
REP result `{"sub": <corr>, "interval_ms": u32}`; then one live BLOCK
per interval with this corr (Live entries, §5.3: a lagging client gets the newest tick and a
`dropped` count); the first BLOCK, the first this subscription receives at a generation it was not
last pushed (also when its interval skipped the tick that changed the generation) and the first after
a dropped or replaced dictionary-bearing block are dictionary-bearing (§5.3 form rule, §6.2). The REP
precedes all data; the first BLOCK comes from the next due sampler tick, never from `subscribe`. The caps of §5.1 apply in this
order: `max_subscriptions` per connection, 8 distinct selections per peer uid, 8 per probe; each is
`budget` with its `pool`. A subscription whose canonical selection is already live joins that
selection stream without creating one: a uid that already uses the selection joins with no selection
cap check; a uid that does not use it yet is checked against that uid's cap but not the probe cap.
The caps applied are the requesting connection's level, and a uid's selections are counted across
levels.
**unsubscribe** — args `{"sub": u32}`; REP on its own corr `{"ok": true, "result": {}}`, then END
`reason: "unsubscribed"` on the subscription's corr. Unknown `sub`: `not-found`.

The unsubscribe race follows the queue rules: an unpinned queued tick removed by `end_stream` is
counted in END `dropped`; a pinned tick is written before the END and counted in `blocks`. Clean EOF:
§1.5, §5.4 `Draining`.

**Live-block contents.** A live block has one row per published cgroup incarnation, labelled by the
cgroup's path relative to the configured root (`/` for the root and `/a.slice/b.scope` below it);
`path` and `prefix` match that label bytewise (exact or byte prefix), `inc` matches the incarnation,
and `all` matches every row. It contains only metrics registered in the model table, with that table's
facts; host readings from `/proc` and daemon `probe.*` metrics appear only on the root row and are
`absent` on every other row, while cgroup columns are dense and carry a quality code on every row
(a missing or failed reading has a non-measured placeholder, never a measured zero).
`probe.cgroups_skipped` counts cgroups and directory listings the walk left out in that tick
(a refused listing counts once).
Derived metrics are not produced. A selection resolving to no rows is successful with zero rows; only the seq-0
"no tick yet" state is `unavailable`, and an unregistered requested metric yields no column.

Note (non-normative) — the root row's `cpu.usage_usec` is tick-accounted and is not comparable
with its children's. The kernel computes the root cgroup's `cpu.stat` from tick-sampled `kcpustat`
(user+nice+system+irq+softirq+steal), while non-root cgroups use precise scheduler runtime, so on
one 60-tick run the root rose 24.22 s while its top-level children rose 29.11 s in total, and every
root value was a multiple of 4000 µs at `HZ=250`. Both readings are correct for their own cgroup.
A consumer must not derive a child's share of the root's CPU: such ratios can exceed 100 %. Nothing
above is normative — the wire contract says nothing about how a client uses two counters together,
and this note adds no field, no value and no rule to it.

4.8 **snapshot_at** — args `{"selection": Selection, "real_ns": i64, "tier": "auto|hot|warm|cold",
"provenance": bool}`. REP result `{"resolved": {"tier": str, "bucket_real_ns": i64, "step_ms": u32,
"covered_ms": u32}, "provenance": [object] (≤ 64 objects, each ≤ 1 KiB encoded) | null}`; one live-shaped BLOCK (rows + columns,
`t_off_us = 0`, header `real_ns = bucket_real_ns`); END `complete`; the one-block refusals of §5.1
apply. Outside retention: `not-found`.

4.9 **series_batch** — args `{"selection": Selection, "from_real_ns": i64, "to_real_ns": i64,
"step_ms": u32 (0 = coarsest tier covering the range), "aggs": [agg-name] (1..4), "cursor": str|null,
"max_blocks": u32 (1..max_page_blocks)}`; a repeated agg is `bad-request`. Requires negotiated minor
≥ 1 (kind 6); at minor 0 it is refused `unsupported-feature` `{"feature":"subject"}`. REP result
`{"tier": str, "step_ms": u32, "omitted": [metric-name]}`; then history BLOCKs (Query entries); END
reason `complete` or `page` with `next`. **Packing (deterministic):**
1. Subjects = the selection's resolved incarnations, ascending `inc`. For each subject, for each agg in
   request order: the columns are the selection's metrics, in canonical (bytewise) order, whose mask
   permits that agg; if none, no block. Metrics that no requested agg permits are listed in `omitted`.
2. Buckets: step boundaries `b` with `from_real_ns ≤ b < to_real_ns` (`b` a multiple of `step_ms`
   since the Unix epoch) for which the tier holds data of that subject; buckets without data are
   absent (a gap is visible in `bucket_real_ns`). A (subject, agg) whose buckets exceed
   `max_block_buckets`, or whose block would exceed `max_block_bytes`, is split into consecutive blocks
   in time order, each the largest prefix that fits both.
3. Block order = subject, then agg, then time. `max_blocks` counts BLOCK frames of this page; when it
   is reached, or the next block does not fit the Query pools (§5.1), and blocks remain, END is `page`
   with `next` naming the next (subject, agg, bucket). A page carries at least one block.
4. History header: `boot_id`/`epoch` of the serving probe, `seq = 0`, `mono_start_ns = mono_end_ns =
   boot_ns = 0`, `real_ns` = first `bucket_real_ns` of the block, `dict_gen` of the query stream,
   `n_rows` = buckets, `dropped = 0`.
A client continues with the same args and `cursor = next`; the cursor is bound to every arg except
`cursor` and `max_blocks` (a continuation may change its page size); a cursor from another epoch or
other args is `cursor-invalid`.

4.10 **events / spans** — args `{"from_real_ns": i64, "to_real_ns": i64, "names": [name] (0..16, empty =
all), "ctx": {"run"|"lane"|"candidate"|"txn"|"session": str ≤ 128} (spans only, 0..5 keys), "limit":
u32 (1..1000), "cursor": str|null}`. Result `{"events"|"spans": [object], "next": cursor|null,
"displaced": u64}`; item keys are plan §C.4 (`kind, name, key, ctx, outcome, code, attrs, t_mono_ns,
real_ns, issuer, inferred`). A page holds at most `limit` items and ends earlier, with `next`, before
the item that would push the REP over `max_reply_bytes` (§5.1 reply sizes).

4.11 **procs** — args `{"subject": {"inc": u64}, "descendants": bool, "fields": [str], "limit": u32
(1..256), "cursor": str|null}`; `fields` ⊆ {`comm`, `ppid`, `state`, `cpu_usec`, `rss_bytes`,
`io_rbytes`, `io_wbytes`, `threads`, `cmdline`}; `cmdline` below operator level is `forbidden`. Result
`{"procs": [{"pid": u32, "start_ticks": u64, "inc": u64, <field>: value|null}], "coverage":
{"scanned": u32, "total": u32, "scan_age_ms": u32, "complete": bool}, "next": cursor|null}`.
`cmdline` is the first 4,096 B of `/proc/<pid>/cmdline` (NUL separators as spaces, cut at a UTF-8
boundary). A page holds at most `limit` items and ends earlier, with `next`, before the item that would
push the REP over `max_reply_bytes` (§5.1 reply sizes).

4.12 **annotate** — args `{"kind": "begin|end|point", "name": name, "key": str ≤ 64 | null, "ctx": {...}
(as 4.10), "outcome": str|null (end only), "code": str ≤ 64 | null, "attrs": {key: str} (≤ 8 pairs,
key a `name` ≤ 64 B, value ≤ 256 B)}`. Result `{"t_mono_ns": u64, "span": u64|null}`. Errors:
`span-unknown`, `rate-limited` (50/s per issuer), `budget` (64 open per issuer, 4,096 global).
Worst case at 3× per string (§4.2): name 64, key 192, ctx 5 × (13 + 384) = 1,985, outcome 24, code
192, attrs 8 × (64 + 768 + 8) = 6,720, envelope < 300: < 9,500 B ≤ 16 KiB (observer
`max_request_bytes`). **Issuer** (plan §C.4), fixed at accept: uid from `SO_PEERCRED` (the
credentials the kernel recorded at `connect()`), cgroup inc from the cgroup of the process behind
`SO_PEERPIDFD` read at accept (a pidfd cannot be confused by pid reuse); if the peer already exited,
the connection is closed (`refuse`). A child that inherits the connected fd annotates as the original
connector, by design. Observer level requires `SO_PEERPIDFD` (kernel ≥ 6.5; estate 7.2).

4.13 **M5/M6 reserved.** `resource-limits`, `window-open`, `window-close`, `export`, `version`, `host`,
`session.*`, `gc`, `reload` use the envelopes, strictness, errors and budgets of this document now;
their `args`/`result` keys are fixed by the M5/M6 packages (`resource-limits/1`, `window/1`, `summary/2`,
`spans/1`, `host/1`), which MUST add them here before code lands. Until then the server answers
`unknown-verb`. `session.watch` streams EVT frames with its corr and ends with END.

## 5. Budgets, streams and state machines

Every time below comes from an injected monotonic clock (`now_ns: u64`); no component reads a clock
itself, so every transition is a fake-clock unit test. A deadline fires when `now > deadline`
(strictly): at exactly `deadline` nothing happens.

5.1 **Bounds** (plan §B.4 where it agrees, §13 V-15/V-16 where not; config keys of `hostobsd.toml`,
values are the defaults; the ones a client needs are announced in `hello.limits`, §4.4):

| Bound (`hello.limits` key) | observe | operator | observer | control | On violation |
|---|---|---|---|---|---|
| connections per level | 16 | 8 | 128 | 64 | accept, close at once, no byte written (§5.4 `refuse`) |
| connections per peer uid per level | 16 | 4 | 64 | 32 | same |
| hello deadline, accept → hello complete (`hello_deadline_ms`) | 5 s | 5 s | 5 s | 5 s | §5.4 row `hello-timer` |
| request frame or line bytes (`max_request_bytes`) | 32 KiB | 32 KiB | 16 KiB | 1 MiB | `frame-too-large`, fatal |
| request deadline, first byte → complete (`request_deadline_ms`) | 5 s | 5 s | 5 s | 25 s | `deadline`, fatal |
| idle deadline (`idle_ms`) | 300 s | 300 s | 300 s | 300 s | `idle`, fatal |
| unread JSON replies the reply bound allows for (`max_outstanding`) | 4 | 4 | 4 | 4 | nothing is held (below); the control queue bounds them (`slow-client`) |
| live subscriptions per connection (`max_subscriptions`) | 4 | 4 | — | — | `budget` `{"pool":"subscriptions"}` |
| distinct live selections per peer uid / per probe | 8 / 8 | 8 / 8 | — | — | `budget` `{"pool":"uid-selections"}` / `{"pool":"selections"}` |
| Live pool, probe-wide: queued subscription BLOCK bytes per connection (`client_queue_bytes`) / global | 4 MiB / 32 MiB | same | — | — | §5.3 (oldest unpinned Live entry shed, else the incoming tick dropped) |
| Query pool, probe-wide: queued query BLOCK bytes per connection (`query_queue_bytes`) / global | 1 MiB / 4 MiB | same | — | — | the request is refused, nothing waits or is dropped (below) |
| `series_batch` streams with queued blocks per connection / per probe (probe-wide, not in `hello`) | 2 / 8 | same | — | — | `too-many-requests` / `budget` `{"pool":"query"}` |
| queued REP/EVT/END bytes per connection (`control_queue_bytes`) | 2 MiB | 2 MiB | 320 KiB | 2 MiB | `slow-client`, fatal (a frame into an empty control queue is always admitted) |
| largest JSON REP the server builds (`max_reply_bytes`) | 448 KiB | 448 KiB | 16 KiB | 448 KiB | never built (pages end early with `next`, reply sizes below) |
| frame completion: a pinned head, pin → completely written (`frame_completion_ms`) | 5 s | 5 s | 5 s | 5 s | `slow-client`, fatal |
| writer stall: queue non-empty, no byte written (`stall_ms`) | 5 s | 5 s | 5 s | 5 s | `slow-client`, fatal |
| largest BLOCK the server builds (`max_block_bytes`, probe-wide) | 1 MiB | 1 MiB | — | — | never built (§4.9 splits) |
| largest frame either side accepts (`max_frame_bytes`) | 16 MiB | 16 MiB | 16 MiB | 16 MiB | client reader: `oversized-frame` |

`max_request_bytes` is derived from the worst legal request (§4.2, §4.12 give the arithmetic), so a
request that obeys every field bound always fits. A **distinct selection** is the canonical
form of §4.2; a uid's selections are those its connections' live subscriptions use (a selection two
uids use counts for both), across levels. Joining an already-live selection adds no probe stream; a
uid joining a selection it did not already use is checked against the uid cap, while a stream creation
is checked against the uid and probe caps in that order, using the requesting level's values.

**Probe-wide keys** (D-W52). `tick_ms`, `max_rows`, `max_block_bytes`, `client_queue_bytes`,
`query_queue_bytes` and the history caps have one probe-wide value each; observe and operator announce
the same `hello` values (0 at levels without BLK verbs, §4.4). The global pools are Live ×8 and Query ×4 the
connection pools; `max_block_bytes` is clamped to `min(client_queue_bytes, query_queue_bytes)`, so a
block that passes its bound fits an empty connection pool. **Query RAM** (D-W52, ≤ 9 MiB including
queued Query bytes): the widest `max_rows` block (1,024 entities, 64 metrics, every text 256 B) is
16 + 67·8 directory + 80 header + dictionary (8 + 1,024·268 + 64·266 = 291,464) + rows 8·1,024 +
64 columns · align8(8 + 9·1,024) = 890,624 B < 1 MiB = `max_block_bytes`; one turn holds one resolution
(≤ 1,025 rows of the same data, ≤ 1 MiB) and one block (≤ 1 MiB): global Query 4 MiB + 2 MiB = 6 MiB.
No per-uid pools (D-W53): a uid is bounded by its connection and selection caps, and the Live victim
rule (§5.3) sheds the largest holder first.

**No request waits** (D-W49, D-W50). Every request is answered in the turn that reads it (REP, every
BLOCK and END queued) or refused before its REP (§2.4); no request record survives the turn, and a
connection's only request state is its live subscriptions. A Query block is queued only when it fits
the connection and global Query pools (`query_room`, §5.3). Refusals:
- one-block verbs (`current`, `snapshot_at`): `too-many-requests` while the connection's Query pool is
  not empty (unwritten query blocks; §4.2 step 10); else, after resolution and the size check,
  `budget {"pool":"query"}` when the global pool lacks room for the block;
- `series_batch` (applied when history is served): `too-many-requests` at 2 streams with queued blocks
  on the connection (step 10) or when its first block does not fit the connection pool; `budget`
  `{"pool":"query"}` at 8 per probe or when it does not fit the global pool; a page ends early (§4.9).
The client retries, after reading its queued blocks (`too-many-requests`) or later (`budget`); each
queued Query block is written within `frame_completion_ms` of its pin or its connection is closed.

**Reply sizes.** `max_reply_bytes = (control_queue_bytes − 256 KiB) / max_outstanding` = 448 KiB, so
`max_outstanding` unread replies of that size plus 256 KiB of END/EVT frames always fit one control queue; a
client that keeps sending requests without reading their replies is `slow-client`. The formula runs
both ways, `control_queue_bytes = max_outstanding * max_reply_bytes + 256 KiB`, and the observer level
uses it to derive its queue from its reply bound (design R28): the observer serves only `hello` and
`annotate` (`status` is forbidden there, §4.3), every observer reply and error envelope is below 3 KiB
(fixed-shape replies below), and the observer `hello` reply too (≤ 2 verbs, ≤ 8 quality codes, 19 limit
keys), so `max_reply_bytes` is 16 KiB and `control_queue_bytes` is 4 * 16 KiB + 256 KiB = 320 KiB (128 observer
connections hold at most 40 MiB of control queue, not 256 MiB). The one hub carries the widest
`control_queue_bytes` of the served levels, so the server enforces a smaller level cap itself after
each read (a connection whose queued control bytes exceed its own level's cap is `slow-client`; a
frame is at most `max_reply_bytes`, so this equals the hub's rule). The cap measures the binary REP payload
alone, and the JSON-mode reply line without its `\n` and including its `,"corr":N` member. A paged verb
(`events`, `spans`, `procs`) ends its page before the item that would push the REP over
`max_reply_bytes` and sets `next`; every page carries at least one item, because the worst item is far
below the bound. Worst cases at 3× per client-supplied string (§4.2) and 6× per server-read string
(`comm`, `cmdline`: a control character is escaped as `\u00XX`):
- `events`/`spans` item (keys of §4.10): name 192, key 192, ctx 1,985, outcome 24, code 192, attrs
  6,720 (the §4.12 bounds), two i64 + issuer + `kind` + `inferred` + 11 keys < 400: < 9,800 B, so a
  page holds ≥ 46 items; a full 1,000-item page fits when items average ≤ 450 B (typical events have
  no attrs: ~250 B).
- `procs` item: `cmdline` ≤ 4,096 B decoded (§4.11) × 6 = 24,576, `comm` 15 × 6 = 90, eight integer
  fields and `pid`/`start_ticks`/`inc` with keys < 500: < 25,200 B, so a page holds ≥ 18 items; without
  `cmdline` < 600 B, so a full 256-item page (< 154 KB) always fits.
- fixed-shape replies: `hello` < 4 KiB (≤ 32 verbs, ≤ 8 quality codes, 19 limit keys), `status` <
  2 KiB (≤ 8 tiers), `series_batch` REP ≤ 64 omitted names × 67 + 100 < 4.5 KB, `snapshot_at` with
  `provenance` ≤ 64 objects × 1 KiB < 66 KiB, every other REP and every error envelope (message ≤ 256 B,
  detail < 2 KiB) < 3 KiB; END < 1 KiB (a cursor is ≤ 256 B).
EVT streams (`session.watch`, M5/M6, §4.13) MUST define their own bound before they land; in M2 the only
EVT is `closing`.

5.2 **Reader** (`proto::stream::FrameReader`, both sides and both languages; P3's `stream.py` is the
same machine, §7.3). `feed(bytes, now)` returns the frames it completed **and** at most one error:
frames completed before the error in the same call are returned with it, never lost.

| State | Input | Transition / output |
|---|---|---|
| `Header{have}` | bytes | append to the 12-byte header buffer; `have = 0 → 1` sets `started = now`; at 12 B validate (§2.1 fixed fields, then `12 ≤ len ≤ max_frame`) → error ⇒ `Poisoned(e)`; `len = 12` ⇒ emit, `Header{0}`; else `Body{hdr, have: 12, started}` |
| `Body` | bytes | append up to `len`; complete ⇒ emit, `Header{0}`; surplus bytes start the next frame in the same call (with `started = now`) |
| `Header{have > 0}` or `Body` | `poll(now)`, `now > started + completion` | `Poisoned(deadline)` |
| `Header{0}` | `poll(now)` | nothing (an idle connection is §5.4's business, not the reader's) |
| `Header{0}` | `eof()` | `Closed` (clean) |
| `Header{have > 0}` or `Body` | `eof()` | `Poisoned(truncated)` |
| `Poisoned(e)` | `feed`, `poll`, `eof` | stays; returns `e` (no frames) |
| `Closed` | `feed` | `Poisoned(trailing-bytes)` |

Buffering: the Rust reader grows its body buffer with received bytes (capacity ≤ `min(len, received +
64 KiB)`), never by `len` alone. Parameters: server `max_frame = max_request_bytes`, `completion =
request_deadline_ms` of the level; client `max_frame = 16 MiB`, `completion = 30 s` (D-W16).

Wire mapping on the server (a reader error on a request): `oversized-frame` → REP `corr=0`
`frame-too-large`; any other header error (`unknown-frame-type`, `unknown-frame-flags`,
`non-zero-reserved`, `truncated` for `len < 12`) → REP `corr=0` `bad-frame`; `deadline` → REP `corr=0`
`deadline`; `truncated` at EOF and `trailing-bytes` after a clean EOF → close without a reply (the peer
is gone). All of them are fatal (§5.4).

**Implemented (`crates/proto/src/stream.rs`).** `FrameReader` is the table above, one unit test per
row (`header_row_*`, `body_row_*`, `poll_row_*`, `eof_row_*`, `poisoned_row_*`, `closed_row_*`), and
the 12 reader cases of §11.4 replay against it. Precisions the table leaves open: an empty `feed` is
not an event (it stamps nothing; a poisoned reader still returns its error); `feed` never checks the
age, only `poll` does, so a frame that completes late inside one `feed` is delivered; a second `eof`
after a clean one stays `Ok`; the effective maximum is `min(max_frame, 16 MiB)` because
`FrameHeader::parse` already refuses a longer `len`; the fixed header fields are judged before `len`
(the first refusal of the codec and of the Python reference); a `len` above the codec's own 16 MiB
limit is `oversized-frame` naming the reader's `max_frame` as the bound; the age error is
`ProtoError::Deadline` (`deadline`), and `truncated` at EOF names its context (`frame header at end
of stream`, `frame body at end of stream`). `buffered_len()` and `buffered_capacity()` expose the
buffer for the allocation tests; a poisoned reader holds no buffer. The 64 KiB growth step costs up
to `len / 64 KiB` reallocations for the largest frame (256 at 16 MiB); this is accepted, because the
capacity bound forbids geometric growth.

5.3 **Send hub** (`hostobs_serve::hub::SendHub`, `crates/serve`, server side, one per serving
process that owns every connection's outgoing queue). The serving process's driver is a
single-threaded `poll(2)` loop that
owns the hub and passes `&mut SendHub` to the connection methods; no mutex exists. The hub owns the
global pools, so the victim of an overflow is chosen with every queue in view.

Queue entries, in one FIFO per connection: `Control{bytes}` (REP/EVT/END, encoded by the caller),
`Live{stream, tick}` (a subscription tick, droppable), `Query{stream, bytes}` (a block of `current`,
`snapshot_at`, `series_batch` or `export`, never dropped). `stream` is the subscribing or requesting
`corr`. `tick` implements `TickForms`: `generation() -> u32` (the selection stream's `dict_gen` this
tick encodes, §6.2), `bare() -> Option<Arc<[u8]>>` (absent on the tick that changes the generation) and
`dict_form() -> Arc<[u8]>` (encoded at most once per tick, on first call). Three pools, each counted per
connection and globally against the §5.1 caps: **Live** (unsent bytes of Live entries), **Query**
(unsent bytes of Query entries), **control** (unsent bytes of Control entries, per connection only).
Per connection also `last_progress_ns` and, for a pinned head, `pinned_ns`; per stream
`pending_drops: u32`, `needs_dict: bool`, `last_pushed_gen: u32` (0 = nothing pushed yet), `blocks: u32`.

**Pin.** `head(c, now)` returns the head entry as `[prefix, tail]`. The first `head` call for an entry
**pins** it and sets `pinned_ns = now`; for a Live or Query block the hub also copies the first `16 +
8·n_sec + 80` bytes (n_sec from offset 12; §3.1 guarantees the header is the first body), writes
`corr` @8 and the stream's `dropped` count @`88 + 8·n_sec`: a Live pin writes
`pending_drops[stream]` (saturating), resets it to 0 and increments `blocks[stream]`; a Query pin writes
0 and leaves `pending_drops[stream]` untouched, so an old Query block cannot consume drops of a new Live
stream that reused its `corr`. A pinned entry is never
dropped or replaced: it is written completely within `frame_completion_ms` of its pin or the connection
is closed (`poll`). Stamping at pin, not at push, is what makes `dropped` exact: every drop happens to
an unpinned entry, so it is counted in the next pinned block of its stream or in that stream's END.
`blocks[stream]` counts a Live entry at pin and a Query entry when it is queued (it is never dropped), so
at `end_stream` it includes Query blocks still waiting ahead of the END.

**Drop total and Query room.** `SendHub::dropped_blocks()` returns every counted drop since `new`:
each increment of any stream's `pending_drops`, while `drain_unpinned` and `remove` count none.
`SendHub::query_room(c, len)` is true exactly when `push_query(c, _, block of len, _)` would queue now:
the connection and global Query pools both fit; an unknown connection is true (its push is a no-op
`Queued`). The server calls it before the REP of every block verb (§5.1 "No request waits").

**Form.** A Live entry is pushed as `dict_form()` when `needs_dict[s]` is set, when `bare()` is absent,
or when `tick.generation() ≠ last_pushed_gen[s]`; otherwise as `bare()`. Every queued Live entry (also
a coalesced one) sets `last_pushed_gen[s] = tick.generation()`; a dictionary-bearing one clears
`needs_dict[s]`; dropping or replacing a dictionary-bearing Live entry sets it. Because
`last_pushed_gen` starts at 0 and generations at 1, the first block is dictionary-bearing; because the
comparison is per stream, a subscriber whose `interval_ms` skipped the tick that changed the generation
(the stream joined an existing selection stream, §4.7) still gets the dictionary form on its next
push. The flag is sticky across an empty queue, so a later tick repairs it however long the gap.

**Fit.** Pushing `L` bytes into a pool checks, in the order connection, global: while `pool + L >
cap`, shed one **victim**: the oldest unpinned Live entry of the victim connection, counted on its
stream (`pending_drops += 1`, `needs_dict` set if dictionary-bearing). The victim connection is `c`
itself for the connection pool, and for the global pool the connection with the largest Live bytes
**among those holding an unpinned Live entry** (ties: lowest connection id). If a pool has no victim left, the push fails. Only the Live pool sheds; the Query and
control pools never shed anything.

| Operation | Rule (in this order) |
|---|---|
| `push_control(c, f, now)` | control pool of `c` empty ⇒ append, whatever the length (a frame is ≤ `max_frame_bytes` by construction; every legal REP is ≤ `max_reply_bytes` anyway, §5.1); else `control + len > control_queue_bytes` ⇒ return `Close(slow-client)`; else append. Never counts toward a block pool, never dropped. |
| `push_live(c, s, tick, now)` | 1. **Coalesce**: if stream `s` has an unpinned Live entry, it is replaced in place by the new tick (`pending_drops[s] += 1`; if the old one was dictionary-bearing, `needs_dict[s] = true`); its bytes leave the pools. 2. The form rule picks the form, `L` = its length. 3. **Fit** `L` into the Live pool (the entry being pushed is not yet inserted, so it is never its own victim). 4. Fit failed ⇒ the new entry is dropped (`pending_drops[s] += 1`, `needs_dict[s] = true` if its form was dictionary-bearing; entries already shed stay shed) ⇒ `DroppedIncoming`; else insert (in place when coalesced) ⇒ `Queued`/`Coalesced`. |
| `push_query(c, s, block, now)` | Query pool of `c` and global both fit `len` ⇒ append, `blocks[s] += 1` ⇒ `Queued`; else `Backpressure(block)`, nothing queued (the server checks `query_room` first, so it never sees this outcome; nothing waits, D-W49). Nothing is shed: a query never displaces a Live tick and a tick never displaces a query block. |
| `end_stream(c, s)` | Removes every unpinned Live entry of `s` (counted), returns `{blocks: blocks[s], dropped: pending_drops[s]}` for the END payload (§2.2) and forgets the stream; queued Query entries of `s` stay (already counted, they precede the END); the caller then `push_control`s the END. |
| empty → non-empty (any push) | `last_progress_ns = now` |
| `head(c, now)` | the head entry, pinning it on the first call (above). |
| `head_partial(c)` | true when the head is pinned and some but not all of its bytes are written. |
| `wrote(c, n, now)` | `n > 0` ⇒ `last_progress_ns = now`; advance; a complete head is popped and its bytes leave its pools. |
| `poll(now)` | for each connection: a pinned head not completely written and `now > pinned_ns + frame_completion_ms`, or a non-empty queue and `now > last_progress_ns + stall_ms` ⇒ `Close(slow-client)`; returns the list. The first rule bounds a dribbling reader (1 byte per second never trips the second); the second bounds a socket that accepts nothing while no head is pinned, so the outcome does not depend on when the writer calls `head`. |
| `drain_unpinned(c)` | removes every unpinned entry of `c` (all kinds), releases their bytes; a pinned head stays. |
| `remove(c)` | drops everything of `c`, releases its bytes, forgets `c`. |

Consequences, each a test (§11.4): a connection holds at most one unpinned Live entry per stream, so a
lagging subscriber sees the newest tick and a `dropped` count, never a stale backlog; a bare block
always carries the generation its stream was last pushed; a REP or END never competes with blocks; a
query never loses data (it is queued whole or refused) and never costs another client a tick; one slow
group member cannot close another (the victim is whoever holds the most sheddable bytes, and only Live
entries are shed); each frame a client is sent must complete within `frame_completion_ms`, so a
dribbling reader cannot hold a pool; a stalled writer is closed after `stall_ms` from the last byte it
accepted, never sooner after an idle period.

**Implemented (`crates/serve/src/hub.rs`; moved from `proto` by P-12).** One unit test per operation row and per consequence
above (`control_row_*`, `live_row_*`, `fit_*`, `query_row_*`, `end_stream_row_*`, `head_row_*`,
`poll_row_*`, `drain_unpinned_row_*`, `remove_row_*`), plus a pool-exactness checker that recomputes
every pool from the queues after each operation of a pseudo-random run. Precisions the tables leave
open: bytes stay in a pool until the entry is popped, shed, coalesced, drained or removed, so a partly
written head still counts; `head` returns the **unwritten rest** of the entry as `[prefix part,
tail part]` (a control frame has an empty prefix; after `wrote(n)` the prefix part shrinks first), and
`wrote` clamps to the entry's length, ignores a call without a pinned head and pops a pinned
zero-length entry on any call; a control frame is pinned like a block (`pinned_ns` and the frame-completion rule
apply to it) but has nothing to stamp; "empty → non-empty" is judged before the push touches the
queue, so a coalesce or a shed that empties the queue and then inserts never restarts
`last_progress_ns` (a stalled socket with one tick per second is still closed); a
coalesced tick keeps its queue position (the replaced entry's sequence number) and, when it then does
not fit, counts two drops (the replaced and the incoming tick); the victim of the global pool
may be the pushing connection itself (it holds the most sheddable bytes); `drain_unpinned` and
`remove` do not count the removed ticks (the connection is closing), but `drain_unpinned` of a
dictionary-bearing tick sets `needs_dict` for its stream; `end_stream` of a stream with no
state (never pushed, or already ended) returns zeros, and a Query block of an ended stream that is
pinned later is stamped `dropped = 0` with its own `corr`; operations on an unknown connection are no-ops (`push_control` `Ok`,
`push_live` `DroppedIncoming`, `push_query` `Queued`, `head` `None`, pools zero); `poll` is `&self`
and keeps reporting a connection until the caller `remove`s it; `ConnId` starts at 1, a wrap skips 0
and an id still in use; a stamped field that lies outside a block shorter than its header is not
written (no panic). A tick's generation MUST be at least 1 (`last_pushed_gen` 0 means nothing
pushed, and the first push of a stream is the dictionary form whatever generation it carries).
The hub lives in `hostobs_serve::hub` (`crates/serve`, moved from `proto` by P-12), not in
`proto`, so `proto::stream` stays the reader and timers only.

**Mode detection** (`proto::stream::ModeDetect`, server, P2; drives §5.4's `Undecided` rows):
`feed(bytes) -> Detect` with `Detect::Need` (consumed everything, fewer than 8 prefix bytes so far),
`Detect::Json { at }` (byte `0x7B` at prefix offset 0), `Detect::Binary { prefix: [u8; 8], at }` (8
bytes seen; the caller applies §1.4 to the prefix fields), `Detect::Unknown`. `at` is the index in
`bytes` of the first byte not consumed by the detector (`{` itself for JSON: it is the line's first
byte); the caller passes `&bytes[at..]` to the `FrameReader` (binary) or the JSON line reader in the
same call, so surplus bytes after the prefix are never lost. A detector that returned anything but
`Need` is finished; further `feed` calls are a caller bug and return `Detect::Unknown` in every build.

**Implemented.** The detector decides at the earliest byte: `{` at prefix offset 0 is `Json { at }`
(`at` is that byte's index, not consumed); a byte among the first four that differs from `HOB\x01`
(a `{` after the first byte included) is `Unknown`; bytes 5 to 8 may be any value; the eighth byte
gives `Binary { prefix, at: index + 1 }`. An empty `feed` is `Need`. After a decision, `feed`
returns `Unknown` in every build. Tests: `detect_row_*`.

5.4 **Connection state machine** (server, P5b; `proto::stream::ConnTimers` holds the hello and idle
deadlines, the request deadline is the reader's completion age (§5.2), so the table is unit-testable
without sockets):

| State | Event | Next / action |
|---|---|---|
| (accept) | level or peer-uid connection cap reached | `refuse`: close, no byte written, nothing read |
| (accept) | else | `Undecided{have: 0}`; `hello_deadline = now + hello_deadline_ms`; peer credentials read (§4.12) |
| `Undecided{have}` | byte `0x7B` at `have = 0` | observer, control: `JsonNoHello` (§8); observe, operator: write the one `binary-required` line (§1.3), close |
| `Undecided{have}` | byte equal to `HOB\x01`[have] (have < 4) or any byte (4 ≤ have < 8) | `have + 1`; at 8 ⇒ send the server prefix (§1.4) ⇒ `AwaitHello` (major mismatch ⇒ prefix, close) |
| `Undecided{have}` | any other byte | write the one `unknown-mode` line (§1.3), close |
| `Undecided`, `AwaitHello`, `JsonNoHello` | `now > hello_deadline` (`hello-timer`) | `Undecided`: close silently; `AwaitHello`: REP `corr=0` `deadline`; `JsonNoHello`: `deadline` line; then close |
| `AwaitHello` | complete REQ `hello`, `corr = 1`, valid | REP ⇒ `Ready` |
| `AwaitHello` | complete REQ with `corr = 0` (any verb, even a malformed payload: judged BEFORE `check_req`, §4.2 step 2) | REP `corr=0` `bad-frame`, close |
| `AwaitHello` | complete REQ with `corr ≠ 0` that passes `check_req` and is not a valid first `hello`: a verb other than `hello`, or `hello` with `corr ≠ 1` | other verb: REP `corr=<its corr>` `hello-required`; `hello` with `corr ≠ 1`: REP `corr=0` `bad-frame`; then close |
| `AwaitHello` | `corr ≠ 0` and `check_req` fails (§4.2), or a `hello` with `corr = 1` whose args are invalid | non-fatal `bad-request` REP on its own `corr` (D-W28, §4.2 first): the state stays `AwaitHello` and the hello timer keeps running |
| `JsonNoHello` | first complete line | handled as a request ⇒ `Ready` (hello optional, §8.4) |
| `Ready` | complete request | dispatch (§4.2 precedence); answered or refused in this turn (§5.1 "No request waits") |
| `Ready` | no live subscription, reader at `Header{0}`, queue empty | arm `idle_deadline = now + idle_ms` (disarmed by any received byte or enqueued frame) |
| `Ready` | `now > idle_deadline` | `Closing(idle)` |
| `Ready` | reader `eof()` clean (`SHUT_WR`) | `Draining`: `end_stream` every subscription with reason `client-eof`, close when the queue is empty |
| any | reader error, hub `Close(slow-client)`, server shutdown | `Closing(code)` |
| `Closing(code)` | — | `drain_unpinned(c)`; if `head_partial(c)` is false the connection holds ONE final frame outside the hub (D-W28): the REP (or error line) of the outcome for a request or reader error, `EVT corr=0 {"event":"closing","reason":code}` (binary) or the error line (JSON) for `idle`, `slow-client`, `shutting-down`; the driver makes one non-blocking write of it and abandons a short write; `remove(c)`; close. When no byte was written yet and the server prefix is queued, the final bytes start with the 8 prefix bytes (prefix guarantee); a major mismatch writes the prefix alone. The driver never writes the hub head after the close (a head pinned with 0 bytes written is not partial and stays in the hub; the final bytes already repeat the prefix). A client MUST NOT depend on receiving it. |
| `Undecided` or `AwaitHello` or `JsonNoHello` or `Ready` | stream end (EOF) other than the clean one of the `Ready` row above: before `Ready` (also mid-line or mid-frame), or in `Ready` inside a frame or line, or bytes after the end | close without a reply (no request is owed; design R10) |

**Driver cadence (P5b-2d).** A P5b-2 driver calls `poll(now)` first and then `on_tick(now)` once per
sampler tick in the same loop iteration (no retry pass, D-W49). `on_tick` asks the registry for at most
one shared block per due selection stream and delivers it to each subscriber after the `subscribe` REP.

**Implemented (`ConnTimers`).** `ConnTimers::new(hello_ns, idle_ns)` holds the level's two
durations; nothing is armed until `on_accept(now)` (a refused connection never calls it).
`on_hello_complete()` disarms the hello deadline (the first complete line in JSON mode counts),
`on_byte()` and `on_enqueue()` disarm idle, `on_idle_condition(true, now)` arms idle at `now +
idle_ns` only when it is not armed (an armed deadline is kept, never pushed out) and
`on_idle_condition(false, now)` disarms it; deadlines saturate at `u64::MAX`. `expired(now)` is
strict (`now > deadline`) and reports `Timer::Hello` before `Timer::Idle`. Tests: `timers_row_*`,
one per row above.

**Connection-fatal codes**: `unknown-mode`, `binary-required`, `unsupported-version`, `hello-required`,
`bad-frame`, `frame-too-large`, `deadline`, `idle`, `slow-client`, `shutting-down`. Every other code answers one
request and keeps the connection. A refused connection (`refuse`) carries no code at all: the mode is
unknown before the first byte, so no reply could be framed.

## 6. Dictionaries, generations, epochs, reconnect

6.1 **Client side: a dictionary is held per stream** = `(connection, corr)`. A BLOCK without a
dictionary section is decoded against the stream's held dictionary and its rows/columns are checked
against it exactly as against an in-frame one (`HOB-46`; `decode_block_with(bytes, held)` and Python
`decode_frame(data, held)`). The codec judges only what it is given: with `held = None` and no
in-frame dictionary it makes no generation or reference check (§3.10), and the stream layer is what
supplies the held dictionary, treats "nothing held" as `stale-dictionary-generation` and enforces the
rules below (`HOB-54`).
6.2 **Server side: a generation counter belongs to the producer of the encoded bytes.** A live
selection stream (one per distinct selection, §4.2, shared by every subscriber, §5.3) owns one
dictionary and one counter. The counter is 0 before the stream's first built tick; the first change
sets it to 1 and each later change adds one. The stream is destroyed with its last subscriber, so a
recreated stream starts again at 0 and its first built tick is generation 1. The tick that changes the
counter carries the new generation complete (it has no bare form), and a change when the counter is
already `u32::MAX` ends every subscription with `internal`. Every subscriber receives the same bytes,
so the same `dict_gen`. A query stream
(private bytes) numbers its own from 1. Because a subscriber may join at generation 5, the client rule
is relative to what the stream holds, never absolute:
- the first BLOCK of every stream is dictionary-bearing (any generation ≥ 1);
- a later dictionary-bearing BLOCK's generation MUST exceed the held one (`generation-regressed`);
- a bare BLOCK's generation MUST equal the held one (`stale-dictionary-generation`); a bare BLOCK with
  nothing held is `stale-dictionary-generation` too.
The §5.3 form rule guarantees a conforming server never violates these: a bare entry is pushed only
when the stream's last pushed entry carried the same generation and no dictionary-bearing entry of the
stream was dropped or replaced since, so the client holds exactly that generation; a
dictionary-bearing entry is pushed for a first block, for any generation the stream was not last pushed
(including a change tick the subscriber's interval skipped) and after a dropped or replaced
dictionary-bearing entry, and in each case its generation exceeds what the client holds. At `u32::MAX` the selection stream ends every
subscription (`END ok:false`, `internal`).
*Implemented* (`crates/proto/src/stream_dicts.rs`, P2c): `StreamDicts` holds one dictionary per `corr`.
`held(corr)` is what to pass to `decode_block_with`; `accept(corr, &block)` applies the rules above to a
decoded block and stores a dictionary-bearing block's dictionary; `close(corr)` forgets one stream (at its
END, at an unsubscribe, after any decoder error); `reset()` forgets all; `on_hello(boot_id, epoch)` drops
every dictionary and returns `Session::{First, Same, Changed}` against the previous hello, so the caller
decides about cursors and sessions (§6.4). A stream that holds nothing holds generation 0 and generations
start at 1, so a first dictionary-bearing block at generation 0, and any later one at an equal or lower
generation, is `generation-regressed`. `accept` forgets the stream's dictionary on any error, and the caller
MUST `close(corr)` after any decoder error and then close the connection (§6.5). `accept` does not compare
`block.corr` with its argument (the caller routes by corr). There is no cap on the number of streams: the
caller's bound is its own unanswered requests (§2.3) plus `max_subscriptions` (§5.1). `generation-rollover` has no client-side
rule beyond `generation-regressed`: the server ends the subscriptions at `u32::MAX`.
6.3 Query replies carry their dictionary in their first BLOCK; a `series_batch` continuation (new
corr) is a new stream. Exported segments carry it in every record (plan §C.5).
6.4 **Epoch.** A probe process takes a new epoch at start, the `CLOCK_BOOTTIME` nanoseconds at process
start, so greater than every earlier epoch of the same boot; nothing is persisted. It keeps that
epoch until it exits; epoch and boot therefore never change within a connection. On reconnect a
client MUST drop every dictionary and live subscription; if `hello.epoch` or `hello.boot_id` differs
it MUST also drop cursors and open span keys and report `session-lost`/`window-lost` for any session
or window it held (plan §D.2); with the same epoch its cursors stay valid.
6.5 A client that sees any decoder error on a BLOCK MUST close the connection and reconnect (the
server is the only producer, so the stream is no longer trustworthy).

## 7. The efficient Python path (normative for `python/hostobs_client`)

7.1 **Wire properties that exist for this path**: fixed-width columns (one cast, no per-value parse);
8-byte body alignment relative to the frame start (§3.1); values before quality inside a column so the
value array starts at body offset 8; one column per metric (a metric is one slice); full independent
live snapshots (a client may skip or drop any block); raw u64 counters (exact Python ints).

7.2 **Alignment.** Every body starts at frame offset ≡ 0 (mod 8); column values at body+8 (8-aligned);
rows `ent` at body+0 and `t_off_us` at body+4n (4-aligned); time `real_ns`@0 (8), `covered_ms`@8n (4),
`samples`@12n (2), `agg`@14n (1). Alignment does **not** hold for: the 8-byte prefix and 12-byte frame
header; frame starts in the byte stream (REP/EVT/END lengths are arbitrary); dictionary entries
(packed). A client therefore reads every frame into its own buffer at offset 0. CPython `memoryview`
item access copies via `memcpy`, so a cast view is correct even unaligned; the guarantee is what
makes it fast and makes third-party zero-copy views (e.g. `numpy.frombuffer`) aligned.

7.3 **Receive path.** `stream.py` `FrameReader` is the §5.2 machine (same states, same `feed`/`poll`/
`eof` results, same `fixtures/stream` cases); `conn.py` drives it from the socket with an injected
clock (`clock: Callable[[], int]`, default `time.monotonic_ns`). Buffering differs from Rust in one
documented way: after the 12 header bytes validate against the **client** limit (`len ≤ 16 MiB`), the
reader allocates one `bytearray(len)`, copies the header in and `recv_into`s the rest through a
`memoryview` slice. This allocates by `len` before the bytes arrive, which §11.1 forbids for servers;
a client accepts it (zero-fill ≤ 16 MiB) because its only peer is the probe it chose by socket path
(D-W14). No concatenation, no growing buffer, no per-frame copy; the `bytearray` is owned by the
decoded `Block` (a reused body buffer would let the next read overwrite views a caller still holds).
**Completion age:** before every `recv_into` the reader sets `sock.settimeout(max(0.001, (deadline −
clock()) / 1e9))` with `deadline = started + 30 s`; a timeout, or `clock() > deadline` after a recv,
is `poll(clock())` ⇒ `deadline`. A per-recv timeout alone would not bound the frame's age. Allocations
per BLOCK frame: 1 `bytearray`, 1 root `memoryview`, the `Block` object, a fixed number of header and
directory tuples, and per *accessed* section ≤ 3 views — independent of `n_rows`.

7.4 **Decode.** All `struct` formats explicit `<`. One cached `struct.Struct('<' + 'HBBI'·n_sec)` per
`n_sec` reads the directory in one call; header body `<16sQQQQQqIIII` in one call; per column
`<IBBH` at body+0 in one call. On `sys.byteorder == 'little'` with `struct.calcsize` of `I`=4, `Q`=8:
values `mv[o+8:o+8+8n].cast('Q'|'q'|'d')`, rows `cast('I')`. Otherwise (big-endian) build
`array.array(typecode, bytes(slice))` and `.byteswap()` — one copy per accessed array, documented as the
only copying path. Quality is `mv[o+8+8n : o+8+9n]` (a `memoryview` of `B`). **No Python bytecode runs per
value during decode or validation** (C-speed O(n) work remains and is expected): quality validity is
`bytes(q).translate(None, VALID_CODES)` must be empty (one transient n-byte copy; measured faster than
`max(q)`, 79-85 µs vs 178-183 µs per 1,000-row frame); row references are `max(ents) < len(dict)` and
`not any(map(kinds.__getitem__, ents))` with a per-generation `bytes` kind map (0 = entity); the
stream keeps a `memoryview` of the previous validated rows body (no copy) and a byte-identical rows
body (C `==`) skips both. **Retention:** that view pins the previous frame's whole `bytearray`, so a
stream retains at most one previous frame, and only one of `len ≤ 1 MiB` (SP-4 frames are ≤ 120 KB); a
larger frame is not retained and its successor validates in full. The view is released when the next
frame of the stream validates, when the stream ends and on any decoder error, so a client holds ≤ 1 MiB
of retained frames per live stream (≤ 4 MiB per connection at `max_subscriptions` 4). A digest instead
of the view was rejected: it puts a hash of every rows body on the measured path and turns the skip
into "probably equal". Dictionary decoding is a per-entry loop and runs once per generation; a
dictionary body byte-identical to a held one reuses it.

7.5 **Integers.** u64 counters are read only through `cast('Q')`, `array('Q')` or `struct` `Q`, and
stay Python `int`. No API converts a counter to `float` except `rate()`, which computes the integer
delta first and divides last.

7.6 **API surface** (P3): `Block.header` (named tuple), `Block.rows.ents()`/`.t_off_us()` (u32 views),
`Block.column(metric_name) -> Column`, `Block.columns()` (iterator, lazy), `Column.raw_values` (view;
the name says it may hold placeholders), `Column.quality` (u8 view of codes), `Column.measured(i) ->
int|float|None` (`None` unless quality 0), `Column.measured_list()` (lazy materialisation, `None` for
non-ok), `Column.quality_name(i)`, `rate(prev, cur, nominal_interval_ns)` matching `model::derive_rate`
on `fixtures/rates/rates.json` (Python reference `python/reference/hostobs_rate_ref.py`).

7.7 **No optional wire additions are needed for this path.** A fixed-offset routing prefix is already
there (`corr`@8 in the frame header, header body at `16 + 8·n_sec`); a per-metric column index would
save the 8-12 `<IBBH` calls of an SP-4 frame (≈ 3 µs) out of a decode dominated by object creation. The
one addition 1.1 makes, kind 6 `subject` (§3.8), is a correctness fix, not a speed one.

7.8 **Measurements P3 makes** (stdlib only; SP-4 shapes: 8 columns, 100 and 1,000 cgroups,
steady-state 8,272 / 80,240 B and dictionary-bearing 12,360 / 120,328 B frames from
`docs/SP-4-WIRE-SIZES.md`; every measured decode alternates **two distinct rows bodies** so the `==`
skip never fires and row validation runs), published in `docs/SP-4-WIRE-SIZES.md` with the gate
image and `sys.version` named. **Python version rule:** the absolute memory bound below was measured on
**CPython 3.14.10** (the patch level) and is enforced only where the interpreter is exactly that
version; elsewhere that test is skipped and the growth and line-count gates, which do not depend on the
version, still run. The registered `conformance` lane runs the CPython of the `tester-unified-rust`
image (3.13.5), so it enforces the growth and line-count gates and not the absolute bound. The
absolute bound is re-measured and re-set in this table in the same change that moves the lane's image
to any other CPython version, **a patch-level change included** (3.14.10 → 3.14.11 too), and the
growth and line-count gates are re-run in that change.

Model measurements behind the thresholds (this design's decoder of §7.4 written stdlib-only as
`bench2.py`; the `bytes()` row is the earlier `bench.py`, which copied the previous rows body; both are
P1 design scratch, not checked in: P3 re-measures with its real decoder and publishes the numbers in
`docs/SP-4-WIRE-SIZES.md`; `nice -n 19 python3 -I`, CPython 3.14.10, devcontainer, 2026-11-18; times are min of 7 × 200 decodes,
two runs; peaks are `tracemalloc` peaks of one decode with the frame allocated before `start()`;
lines = `sys.settrace` `line` events in all frames during one decode):

| Path (model) | t(100) µs | t(1000) µs | ratio | peak 100 / 1000 B | lines 100 / 1000 |
|---|---|---|---|---|---|
| repeated rows body (`==` skip) | 11.3-11.8 | 33.5-33.8 | 2.85-2.97 | 6,131 / 8,219 | 141 / 141 |
| **distinct rows bodies (the gated path)** | 14.1-14.8 | 79.3-84.7 | 5.64-5.74 | 6,131 / 8,219 | 141 / 141 |
| distinct, quality via `max(q)` | 23.2-25.4 | 178-183 | 7.21-7.69 | 6,009 / 6,297 | 141 / 141 |
| defect: per-value Python loop on quality | 25.5-28.5 | 202-212 | 7.45-7.93 | 6,009 / 6,297 | 1,733 / 16,133 |
| defect: `list()` of every column | 19.8-20.5 | 242-251 | 11.81-12.65 | 7,177 / 38,313 | 149 / 149 |
| (`bench.py`: distinct, copying the previous rows body with `bytes()`) | — | — | — | 6,964 / 16,252 | — |

The CPU ratio cannot gate: the correct path (5.6-5.7) and a per-value loop (7.5-7.9) are too close for
a load-independent threshold, and a legitimate C-speed path (`max(q)`) already sits at 7.2-7.7.
Bytecode counts and memory separate them deterministically:

| Measurement (distinct rows bodies, eager validation, steady state) | Acceptance (gated) | Catches |
|---|---|---|
| `sys.settrace` `line` events during `decode()` + validation | equal at 100 and 1,000 rows | any per-value Python loop (model: 141 = 141; a loop: 1,733 vs 16,133) |
| `tracemalloc` peak of `decode()` (frame allocated before `start()`) | 1,000-row peak − 100-row peak ≤ 4 KiB (model 2,088 B) | per-row objects or copies (`list()`: 31,136 B; a rows copy: 9,288 B) |
| same, absolute 1,000-row peak, CPython 3.14.10 only | ≤ 16 KiB (model 8,219 B) | fixed-cost bloat |
| CPU absolute and ratio, steady state and dictionary-bearing | published, not gated (estate: no load-dependent verdict) | trend evidence; target ≤ 250 µs at 1,000 cgroups |
| `measured_list()` of one 1,000-row column | published | materialisation is O(n) by definition |

## 8. JSON mode

8.1 **Framing.** Each request is one UTF-8 JSON object starting with `{` and ending with `\n` (0x0A);
the line, newline included, is bounded by `max_request_bytes` and the request deadline of the level
(`frame-too-large`/`deadline`, close); the line reader is bounded like §5.2 (no allocation beyond the
bytes received, capacity ≤ `max_request_bytes`). Empty lines and invalid JSON text (§4.2) are
`bad-request` with `corr: 0` when no `corr` could be read; the connection stays. Each reply is one
line. **Line rules** (P5b design R13, `crates/serve/src/lines.rs`): a line including its `\n` is at most
`max_request_bytes`; when that many bytes are buffered without a `\n` the reader refuses with
`frame-too-large` (fatal); a `\r\n` ending reaches the envelope check with a trailing `\r`, which is a
trailing byte and so `bad-request` (non-fatal); an empty line is `bad-request`; the request-deadline
age is stamped at each line's first byte; reading is linear (a scanned offset, no rescan per feed,
complete lines sliced out of the received bytes, only the incomplete tail buffered) and the buffer
capacity never exceeds `min(max_request_bytes, bytes received + 4,096)`. End of stream inside a line
closes without a reply. **`corr` recovery** (R12): the reply to a defective line carries the line's
`corr` only if the members of the line read (no duplicate key, at most 8 keys) and `corr` is one exact
`u32` other than 0, else `corr` 0; a `corr` of 0 is `bad-request` `{"key":"corr"}`, not fatal (a binary
`corr` 0 is `bad-frame`, fatal).
8.2 **Envelope.** Request `{"corr": u32 ≠ 0, "verb": str, "args": object}`; reply = §4.1 / §2.2 REP
objects plus `"corr"`. Server-originated lines use `corr: 0`.
8.3 **Integers.** All integers are exact JSON integers (never strings, never exponent or fraction form);
u64 up to 2^64−1 MUST round-trip; a fraction where an integer is required is `bad-request`. Floats
appear only where a schema names them; non-finite values are never sent (`null` + quality instead).
JavaScript consumers are unsupported (plan K-JSON).
8.4 **Levels and hello.** JSON mode is served at observer and control only (at observe and operator a
first `{` gets one `binary-required` line and a close, §1.3, D-W51); JSON telemetry comes from
`hostobsctl --json`, which speaks binary. `hello` is optional (without it the connection runs at minor 0).
8.5 **`hostobs-mark`** writes one `annotate` line to `HOSTOBS_OBSERVER_SOCKET`, `shutdown(SHUT_WR)`,
reads one reply line, exits 0 (`--require`: non-zero on any failure; otherwise a stderr notice and 0).

## 9. Versioning

9.1 `hostobs/1` = major 1. A minor is additive: new optional sections, new required sections only
inside replies to requests that asked for them under the negotiated minor, new verbs, new optional
request keys, new reply keys, new quality codes, new error codes. Nothing of minor *m* is sent to a
peer that negotiated `< m`.
9.2 A section's layout is frozen at version 1; a change gets a **new kind code** (a known kind with an
unknown version is refused, `frame.rs` `decode_block`). Unknown optional sections are skipped by
`len`; unknown required ones are refused, surfaced to the client caller as `unsupported-section`.
9.3 A major change changes the frame grammar; only the 8-byte prefix survives it.
9.4 Minor 1 = kind 6 `subject` (and `series_batch`, which needs it); `PROTO_MINOR` is 1 (`HOB-48`). The
§3.6 time length is not a minor: it is layout version 1 (§13 V-1).

## 10. Wire error codes (closed list, minor 0 + 1)

`unknown-mode`, `binary-required`, `unsupported-version`, `hello-required`, `bad-frame`,
`frame-too-large`, `deadline`, `idle`, `slow-client`, `shutting-down` (the §5.4 fatal set),
`bad-request`, `unknown-verb`, `forbidden`, `too-many-requests`, `budget`, `rate-limited`, `not-found`,
`cursor-invalid`, `span-unknown`, `unsupported-feature`, `session-lost`, `window-lost`, `internal`.
`detail` keys: `budget` → `{"pool": "subscriptions"|"uid-selections"|"selections"|"rows"|"query"|"spans"}`
(`spans` is reserved until P5b-3);
`rate-limited` → `{"retry_after_ms": u32}`; `bad-request` → `{"key": str}` (dotted path of the
offending key, `""` for a JSON text error); `unsupported-feature` → `{"feature": str}`; every other
code `{}`. Mapping of local errors: §5.2 (reader), §4.2 (JSON text). There is no `busy`: a
connection over a cap is refused without a byte (§5.4).

## 11. Robustness and conformance

11.1 **Decoder rules for untrusted bytes** (both languages): check every length against the real
buffer before use; compute with checked arithmetic; allocate from a declared size only after the bytes
are proven present (directory capacity from `n_sec` included, `HOB-50`; the one exception is the
Python client's frame buffer, §7.3); no recursion; JSON payloads
are parsed only after the frame is complete and bounded; JSON nesting depth ≤ 8 for requests.

11.2 **Existing cases** (`fixtures/wire/*.{bin,json}`; Rust `crates/proto/tests/golden_fixtures.rs`,
Python `python/reference/hostobs_wire_ref.py`; both must reproduce `.json` or `expect_error`):
`live-block`, `history-buckets`, `empty-selection`, `all-quality-codes`, `u64-boundaries`,
`unicode-dictionary`, `unknown-optional-section` (decode ok); `bad-metric-mask`, `bad-quality-code`,
`block-as-req`, `block-without-directory`, `column-kind-mismatch`, `column-references-entity`,
`column-reserved`, `dangling-dictionary-ref`, `duplicate-dictionary-entry`, `empty-rows-and-time`,
`frame-length-below-header`, `future-section-version`, `generation-mismatch`, `no-rows-or-time`,
`non-zero-reserved`, `oversized-frame`, `required-section-refused`, `row-references-metric`,
`section-order`, `truncated-directory`, `truncated-frame` (named error). The P3 client must pass all of
them too.

11.3 **New block cases** (bytes from the Python reference encoder, Rust and Python agree;
`fixtures/wire` holds 70 cases: 11 valid, 59 refusals, the lists of §11.2 and below).
`history-buckets` and `history-five-buckets` carry `last` buckets only, because their counter and gauge
columns together permit no other aggregation (§3.6); `block-as-req` has `SECTIONED` clear (§3.10). A
case with a top-level `"held": {"generation", "entries"}` key is decoded against that held dictionary
(`decode_block_with(bytes, Some(held))`, Python `decode_frame(data, held)`), in the same entry form as
`frame.dictionary`; every other case is decoded with `held = None`.

| Case | Purpose | Expected |
|---|---|---|
| `history-five-buckets` | §3.6 length `align8(15·5) = 80` | ok |
| `history-gauge-aggs` | gauge-only history block, `mean`, `min`, `max` and `last` buckets, no subject | ok |
| `duplicate-header-bad-second-body` | a second header whose flags word is 1 | `duplicate-section` (kind 1) |
| `column-nonzero-pad`, `time-nonzero-pad`, `dictionary-nonzero-pad` | pad byte 1 | `non-zero-reserved` (value 1) |
| `time-bad-agg-and-pad` | agg byte 9 and a pad byte 1 | `unknown-aggregation` (code 9) |
| `dictionary-count-too-large` | count 65,537 | `dictionary-too-large` |
| `dictionary-entry-control` | text byte 0x01 | `dictionary-entry-control` (index 0, offset 0) |
| `dictionary-text-past-body` | text length 200 past the body | `truncated` (dictionary section) |
| `dictionary-invalid-utf8` | text byte 0xFF | `dictionary-entry-not-utf8` |
| `dictionary-short-entry-header` | count 5, four entries, body ends in the fifth header | `truncated` (dictionary section) |
| `truncated-unknown-type` | 5 bytes, type byte 9 | `unknown-frame-type` |
| `header-required-flag-clear`, `subject-required-flag-clear` | entry flags 0 | `unknown-section-flags` (kind 1, kind 6) |
| `unknown-section-flags-directory` | a column entry with flags 2 | `unknown-section-flags` (kind 3) |
| `unknown-section-flags-header` | header flags word 1 | `unknown-section-flags` (kind 1, flags 1) |
| `section-reserved` | directory reserved word 1 | `section-reserved` |
| `missing-header` | no header in the directory | `missing-section` |
| `duplicate-section` | two dictionary sections | `duplicate-section` (kind 5) |
| `conflicting-column` | metric 2 twice | `conflicting-column` |
| `subject-wrong-length` | subject body 16 bytes | `section-length` (declared 16, expected 8) |
| `subject-reserved-word` | subject reserved word 1 | `unknown-section-flags` (kind 6, flags 1) |
| `subject-dangling`, `subject-not-entity` | subject 99, subject 1 (a metric) | `dangling-dictionary-ref`, `row-reference-not-entity` |
| `subject-stale-generation-held` | bare history block, `dict_gen` 2, `held` generation 3 | `stale-dictionary-generation` |
| `aggregation-not-permitted-held` | bare block, `raw` over a gauge, `held` generation 2 | `aggregation-not-permitted` |
| `history-gauge-raw`, `history-state-delta` | `raw` over a gauge, `delta` over a state | `aggregation-not-permitted` |
| `duplicate-entity-other-label-control-byte` | `inc` repeated with a label that starts with 0x01 | `duplicate-dictionary-entry` (judged before the control byte) |
| `header-version-and-required-flag-clear` | header entry with version 2 and the required flag clear | `unsupported-section-version` (version before the flag) |
| `history-subject` | kind 6 with time, counter column with `raw`, `last`, `delta` buckets | ok |
| `unaligned-optional-section` | unknown optional kind, `len = 5`, last | `section-length` |
| `optional-section-before-header` | unknown optional kind, `len = 8`, directory entry 0 | `section-order` |
| `duplicate-metric-other-facts` | metric name repeated with another unit | `duplicate-dictionary-entry` |
| `duplicate-entity-other-label` | `inc` repeated with another label | `duplicate-dictionary-entry` |
| `rep-not-sectioned-truncated` | REP type, buffer shorter than `len` | `not-a-block` (precedence, §3.10) |
| `subject-in-live-block` | kind 6 with rows | `subject-without-time` |
| `history-agg-not-permitted` | `mean` on a counter column | `aggregation-not-permitted` |
| `bare-block-held-dict` | no dictionary section, `held` generation 1 | ok |
| `bare-block-dangling` | bare block, `held` with 4 entries, row index 99 | `dangling-dictionary-ref` |
| `rep-sectioned-flag` | REP frame with `SECTIONED` | `bad-frame` (`unknown-frame-flags` locally) |

11.4 **Stream cases** (`fixtures/stream/<case>.json`: `{"case", "doc", "side": "client|server",
"max_frame", "completion_ms", "steps": [{"t_ms", "feed_hex"} | {"t_ms", "poll": true} | {"t_ms", "eof":
true}], "expect": [[<outputs of step 0>], [<outputs of step 1>], ...]` with outputs `{"frame": {"type",
"corr", "len"}}`, `{"error": kind}`, `{"closed": "clean"}`; one list per step, in order; Rust and
Python both replay them). A client case (`"side": "client"`) has the two steps `{"t_ms", "feed_hex"}`,
one complete BLOCK frame of stream corr 5 (decode with `decode_block_with(bytes, held(5))`, then
`accept(5, block)`; on any error `close(5)`), and `{"t_ms", "hello": {"boot_id", "epoch"}}` (`on_hello`),
with outputs `{"ok": generation}` (the block's header `dict_gen`), `{"error": kind}` and `{"session":
"first|same|changed"}`; one output per step. Reader cases (`fixtures/stream/reader-*.json`, one per §5.2 row; written by
`python/reference/make_stream_fixtures.py` from the §5.2 rules, not by running a reader; Rust
`crates/proto/tests/stream_fixtures.rs`; `python/tests/test_stream_fixtures.py` checks the files against
the generator and replays them with an independent reader):

| Case | Expected |
|---|---|
| `reader-dribbled-bytes` (one byte per step, within the age) | one frame at the last step |
| `reader-split-every-offset` (frame cut at each offset 1..len−1) | one frame each |
| `reader-two-frames-one-feed` | two frames from one feed |
| `reader-frame-then-bad-header` (one feed: a good frame, then a header with type 9; then a second feed) | `[frame, error unknown-frame-type]` in the first step, the same error and no frame in the second |
| `reader-oversized-len` (`max_frame + 1`) | `oversized-frame`, poisoned, buffer capacity 12 (the fixture format has no capacity key: the unit tests `an_oversized_len_never_allocates_beyond_the_header_buffer` and `body_row_buffer_grows_with_received_bytes_never_with_len` pin it) |
| `reader-len-below-header` (`len = 11`) | `truncated`, poisoned |
| `reader-completion-age` (half a frame at t=0, poll at `completion_ms`, then `+1`) | nothing, then `deadline` |
| `reader-age-from-first-byte` (idle 60 s at `Header{0}`, then a frame dribbled within the age) | one frame (idle time does not count) |
| `reader-truncated-stream` (EOF mid-body) | `truncated` |
| `reader-clean-eof` | `closed` |
| `reader-poisoned-stays` (feed after an error) | the same error, no frame |
| `reader-feed-after-eof` | `trailing-bytes` |

Client stream cases (`fixtures/stream/client-*.json`, both languages; steps feed BLOCK frames to a
stream state with `decode_block_with`; written by `python/reference/make_client_stream_fixtures.py`
from the §6 rules, not by running a client; Rust `crates/proto/tests/client_stream_fixtures.rs`;
`python/tests/test_client_stream_fixtures.py` checks the files against the generator and replays them
with an independent client): `client-first-block-gen-5` (first block dictionary-bearing at
generation 5: ok, then a bare 5: ok), `client-first-block-bare` (`stale-dictionary-generation`, twice),
`client-generation-step` (dict gen 2, bare 2, dict gen 3, bare 2: ok, ok, ok, `stale-dictionary-generation`),
`client-generation-regressed` (dict gen 2 then dict gen 2: `generation-regressed`, the stream is closed so
a bare 2 is stale, a fresh dict gen 4 is ok, dict gen 3 is regressed),
`client-required-section` (unknown required kind in a dictionary-bearing block: `unsupported-required-section`,
stream closed, so the bare block after it is stale), `client-epoch-change` (hello `first`, a dictionary,
hello with a new epoch `changed`: the bare block finds nothing held; a dictionary, hello with the same
epoch `same`: dictionaries are dropped here too; cursors and subscriptions are the caller's, §6.4).

Hub cases (`fixtures/stream/hub-*.json`, 19 files written by `python/reference/make_hub_fixtures.py`,
replayed by `crates/serve/tests/hub_fixtures.rs` with a fake clock, `t_ms * 1_000_000` as `now_ns`;
a Python test pins the committed bytes to the generator). Format: `{"case", "doc", "limits", "conns",
"steps", "expect"}`; `limits` holds the seven `QueueLimits` fields (scaled: Live 1000/8000 per
connection/global, Query 1000/4000, control 500, stall and frame completion 5000 ms),
`conns` the number of connections (index 0 first, added at t = 0). Every block is a
2-section BLOCK of the stated length (at least 112; `dropped` at offset 104, tail from 112
filled with `tag`, default 160). A step is `{"t_ms", "op", ...}` with `op` one of `push_live {c, s,
gen, bare|null, dict, tag?, share?}` (`share` names one tick object used by several pushes),
`push_query {c, s, len}`, `push_control {c, len}`, `query_room {c, len}`, `end_stream {c, s}`,
`head {c, block}`, `head_partial {c}`, `wrote {c, n}`, `poll`, `drain_unpinned {c}`, `remove {c}`,
each with an optional `watch` list of `c<i>` and `g`. `expect` has one list per step: the
operation's output (`queued|coalesced|dropped-incoming`, `queued|backpressure`, `ok|close`,
`{"counts": {"blocks", "dropped"}}`, `{"head": {"len": remaining, "corr", "dropped", "tail_tag"}}`
or `null`, `{"room": bool}`, `{"partial": bool}`, `{"closed": [conn indexes]}`), then
`{"pools": {key: [live, query, control]}}` when the step watches. The replay also asserts at most
one `dict_form` call per push:

| Case | Expected |
|---|---|
| `hub-pin-stamps` | push A, B (two streams), `head` pins A with `dropped = 0`; drop of a later A-stream tick is stamped on the next pinned A block |
| `hub-coalesce` | two ticks of one stream, no `head` between: one entry, stamped `dropped = 1` |
| `hub-pinned-never-dropped` | over budget with the pinned head the oldest: the next oldest unpinned is dropped |
| `hub-coalesce-dict-form` | an unpinned dictionary-bearing tick replaced by a bare tick: the replacement is the dictionary form |
| `hub-needs-dict-sticky` | incoming dictionary-bearing tick dropped over budget, queue drains, 60 s later a tick arrives: dictionary form |
| `hub-repair-over-budget` | the dictionary form itself does not fit: dropped, `needs_dict` stays set, counted |
| `hub-per-connection-shed` | over `client_queue_bytes`: oldest unpinned Live entry of another stream dropped |
| `hub-global-largest` | three connections, global over: the largest connection's oldest unpinned Live entry dropped, the pushing one untouched |
| `hub-victim-skips-pinned` | global over, the largest connection holds only a pinned head and Query bytes: the next largest connection with an unpinned Live entry is shed; with no such connection the push is `DroppedIncoming` (counted, `needs_dict` if dictionary-bearing) |
| `hub-interval-skips-generation` | stream pushed generation 1 (pinned and written); the change tick to 2 is not pushed to it (interval); the next tick, generation 2 with a bare form, is pushed as the dictionary form; a following generation-2 tick is bare |
| `hub-query-backpressure` | `query_room` false and `push_query` `Backpressure` over the connection and over the global Query pool, nothing queued or dropped; after `wrote` frees Query bytes `query_room` is true and a new push is `Queued` |
| `hub-query-own-pool` | Live pool full of other connections' unpinned ticks: `push_query` sheds none of them; Query pool full: `push_live` unaffected |
| `hub-query-pin-after-corr-reuse` | after a Query stream's END, a new Live stream reuses its `corr`: the old Query pin stamps `dropped = 0` without consuming the Live stream's pending drop, and the next Live pin stamps 1 |
| `hub-control-cap` | a frame larger than `control_queue_bytes` into an empty control queue: admitted; a second control push while it is unsent: `Close(slow-client)`; blocks never block a control push |
| `hub-frame-completion` | pinned head, `wrote(1)` at 4 s: `head_partial`, no stall close, no close at `pinned_ns + frame_completion_ms`, `slow-client` at `+1` |
| `hub-stall` | non-empty, no `wrote`, no `head`: no close at `+stall_ms`, `slow-client` at `+stall_ms + 1` |
| `hub-stall-after-idle` | queue empty for 60 s, then a push and `poll` at `+1`: no close |
| `hub-shared-block` | two connections, one `Arc`, distinct `corr`/`dropped` prefixes, identical tails |
| `hub-end-stream` | `end_stream` of a live stream removes the unpinned tick, returns `blocks`/`dropped`; of a query stream with one pinned and two queued unpinned Query blocks returns `blocks = 3` and leaves both queued |

Connection cases (P5b, Rust only, a fake clock, one per §5.4 row; in P5b-1 and P5b-2 the case runs against the sans-IO core (`Conn` and `Server` fed bytes in memory, design R21), and the socketpair runs of the same cases move to P5b-4 with the drivers):
`conn-refuse-level-cap`, `conn-refuse-uid-cap` (no byte written), `conn-silent-hello-timeout`
(nothing sent, closed at `+5001` ms, no byte written), `conn-prefix-partial` (`HOB\x01` + 2 bytes, then
the timer: silent close), `conn-unknown-mode`, `conn-json-first-line` (observer),
`conn-json-block-level` (`{` at observe: one `binary-required` line, close), `conn-hello-required`,
`conn-hello-corr-not-1` (`bad-frame`), `conn-corr-zero-req` (`bad-frame`), `conn-idle` (`idle` at
`+idle_ms + 1`), `conn-not-idle-with-subscription`, `conn-half-close` (END `client-eof`, then close),
`conn-request-too-large` (`frame-too-large`), `conn-request-deadline` (`deadline`),
`conn-prefix-surplus` (the 8-byte prefix and a complete `hello` REQ in one write: hello answered),
`conn-dribbling-reader` (a subscriber reading one byte per 4 s: `slow-client` at its pinned head's
`pinned_ns + frame_completion_ms + 1`; a second connection's ticks are never shed meanwhile),
`conn-reply-page-truncated` (retargeted to `procs` in P5b-3, because `events` is M3: `procs` at its
page limit with a `cmdline` of 4,096 B per item: REP ≤ `max_reply_bytes`, fewer items, `next` set,
connection stays; the original `events` form, `limit` 1,000 with 8 attrs of 256 B each, returns with M3), `conn-closing-evt` (`Closing` with an
unwritten head: EVT `closing` written; with a partially written head: no EVT, closed).

P5b-2c and P5b-2d make `conn-not-idle-with-subscription`, `conn-dribbling-reader`, the END half
of `conn-half-close`, `conn-corr-in-use-sub` and `conn-too-many-requests` reachable against the
in-memory `Conn`/`Server`; R21's P5b-1 exclusion is superseded for those cases. Under D-W49
`conn-too-many-requests` is a `current` while the connection's Query pool holds a block (refused, the
connection stays), `conn-current-budget-query` a `current` against a full global Query pool (`budget`
before any REP), and `conn-corr-in-use-pending` is retired. `conn-reply-page-truncated` remains
P5b-3, and the socketpair form remains P5b-4.

## 12. Work breakdown

**P2 — `proto` stream layer and codec additions (L).** Modules `crates/proto/src/stream.rs` (`FrameReader`,
`ModeDetect`, `ConnTimers`, `StreamDicts`), `crates/serve/src/hub.rs` (`SendHub`, moved from `proto`
by P-12), `crates/proto/src/envelope.rs` (REQ/REP/END/EVT JSON shape checks without a JSON
dependency: byte-level only — `proto` stays dependency-free; JSON parsing is the caller's),
`frame.rs` additions. Public API:
`FrameReader::new(max_frame: u32, completion_ns: u64) -> FrameReader`;
`FrameReader::feed(&mut self, bytes: &[u8], now_ns: u64) -> Feed`, `Feed { frames: Vec<RawFrame>,
error: Option<ProtoError> }`; `FrameReader::poll(&mut self, now_ns: u64) -> Result<(), ProtoError>`;
`FrameReader::eof(&mut self) -> Result<(), ProtoError>`; `RawFrame { header: FrameHeader, bytes:
Vec<u8> }`;
`ModeDetect::new() -> ModeDetect`; `ModeDetect::feed(&mut self, bytes: &[u8]) -> Detect`, `enum Detect
{ Need, Json { at: usize }, Binary { prefix: [u8; 8], at: usize }, Unknown }` (§5.3 mode detection);
`QueueLimits { client_queue_bytes: usize, global_live_bytes: usize, query_queue_bytes: usize,
global_query_bytes: usize, control_queue_bytes: usize, stall_ns: u64, frame_completion_ns: u64 }`;
`SendHub::new(QueueLimits) -> SendHub`; `add(&mut self, now_ns: u64) -> ConnId` (`ConnId(u32)`,
increasing; ties in §5.3 go to the lowest);
`remove(ConnId)`; `drain_unpinned(ConnId)`;
`push_control(ConnId, Arc<[u8]>, now_ns) -> Result<(), Close>`;
`push_live(ConnId, stream: u32, &dyn TickForms, now_ns) -> LiveOutcome {Queued, Coalesced,
DroppedIncoming}`; `push_query(ConnId, stream: u32, Arc<[u8]>, now_ns) -> QueryOutcome {Queued,
Backpressure(Arc<[u8]>)}`; `query_room(ConnId, len: usize) -> bool`; `end_stream(ConnId, stream:
u32) -> StreamCounts {blocks: u32, dropped: u32}`; `head(ConnId, now_ns) -> Option<[&[u8]; 2]>`
(pins); `head_partial(ConnId) -> bool`; `wrote(ConnId, n: usize, now_ns)`; `poll(now_ns) ->
Vec<(ConnId, Close)>`; `queued(ConnId) -> Pools { live: usize, query: usize, control: usize }`,
`global_pools() -> Pools` (`control` 0); `trait TickForms { fn
generation(&self) -> u32; fn bare(&self) -> Option<Arc<[u8]>>; fn dict_form(&self) -> Arc<[u8]>; }`;
`enum Close { SlowClient }`;
`ConnTimers { hello_deadline_ns: Option<u64>, idle_deadline_ns: Option<u64> }` with
`ConnTimers::new(hello_ns: u64, idle_ns: u64)`, `on_accept(now_ns)`, `on_hello_complete()`, `on_byte()` and `on_enqueue()` (both disarm idle),
`on_idle_condition(idle: bool, now_ns)` (arms at `now + idle_ms` when it turns true), `expired(now_ns) ->
Option<Timer>`, `enum Timer { Hello, Idle }` (the request deadline is `FrameReader`'s completion age);
`decode_block_with(bytes: &[u8], held: Option<&Dictionary>) -> Result<Block, ProtoError>`;
`StreamDicts` (client side, §6.2: `accept(&mut self, corr, &Block)`);
`SectionKind::Subject = 6`, `Block::subject: Option<u32>`; decoder rules of §3.1, §3.7, §3.10.
Fixes `HOB-46`, `HOB-47`, `HOB-48` (subject half; the length half landed in P0), `HOB-50`,
`HOB-51`..`HOB-53` (§13 V-11..V-13) and the P2 part of `HOB-54` (stream layer). Acceptance: every §11.3 case and the reader, client and hub cases of §11.4 in Rust, one test
per row of §5.2, §5.3 (operations and mode detection) and the `ConnTimers` rows of §5.4; Python reference encoder/decoder updated for
kind 6, §3.1, §3.7 and §3.10.

**P3 — `python/hostobs_client` (L).** Stdlib only, zipimport-safe (no `__file__` reads, data via
`importlib.resources` only if needed). Modules `wire.py` (`decode_block(buf: bytearray, held:
Dictionary|None) -> Block`, `Dictionary`, `Column`, `WireError(kind, detail)`, `ServerError(code,
message, detail)`, `ConnectionLost`), `stream.py` (`FrameReader(max_frame: int, completion_ns: int)`
with `.feed(data: bytes, now_ns: int) -> tuple[list[RawFrame], WireError|None]`, `.poll(now_ns) ->
WireError|None`, `.eof() -> WireError|None`: the §5.2 machine; `StreamDicts` for §6.2), `conn.py`
(`Connection.open(path: str, level: str, *, client: str, timeout_s: float, clock: Callable[[], int] =
time.monotonic_ns) -> Connection`, `.request(verb: str, args: dict) -> dict`, `.blocks(verb, args) ->
Iterator[Block]`, `.subscribe(selection, interval_ms) -> Subscription`, `.close()`; the §7.3 receive
loop), `rates.py`
(`rate(prev, cur, nominal_interval_ns) -> dict`), `profiling.py` (`require_profiling(level: str,
required: bool) -> ProfilingState` with the five states of plan §D.2), `mark.py` (`hostobs-mark`).
Acceptance: all `fixtures/wire` cases and the reader and client cases of `fixtures/stream`,
`fixtures/rates/rates.json`, the §7.8 gated thresholds on the pinned CPython, a zipimport smoke test,
no import outside the stdlib (checked by a test). Closes the P3 part of `HOB-54`.

*P3a implemented* (`python/hostobs_client/{__init__,wire,stream,rates}.py`; `conn.py`, `profiling.py`
and `mark.py` are P3b). `wire.py` is the §7.4 decoder; `stream.py` holds `FrameReader`, `RawFrame`,
`StreamDicts` and `Session` (`"first"|"same"|"changed"`); `rates.py` holds `rate()` and `Reading`. Tests:
`python/tests/test_client_{wire,stream,rates,stdlib_only,budgets}.py`. Decisions and deviations:
- **Reader API.** Besides `feed`/`poll`/`eof`, the reader exposes `fill() -> memoryview` (the writable
  slice of the buffer being filled: the rest of the 12-byte header, then the rest of the one
  `bytearray(len)`) and `advance(n, now_ns)` (accounts for `n` bytes written there). P3b's `conn.py`
  calls `sock.recv_into(reader.fill())` then `advance`; `feed` is the same pair over a `bytes` argument.
  `fill()` never spans two frames, so one `recv_into` completes at most one frame. `RawFrame` is
  `(type, corr, length, data)` with `data` the owned `bytearray`.
- **Errors.** `StreamDicts.accept` raises `WireError` (the Python style) where Rust returns `Err`; the
  reader returns its error in the `feed`/`poll`/`eof` result as §12 specifies. `WireError.detail` holds the
  fixtures' `expect_error` fields; an oversized length names the reader's `max_frame` as `max`.
- **Retained rows.** The skip compares and retains only the `ent` half of the rows body (the only part
  validation reads), so a changed `t_off_us` does not defeat it; the retained view lives on the
  `Dictionary` it was validated against (`Dictionary._rows_ok`), so it can never be applied to another
  dictionary, and `StreamDicts.close/reset` and any decoder error drop it. A bare block with no rows
  section clears it.
- **Buffer ownership.** A buffer passed to `decode_block` is owned by the returned `Block` (and, for the
  entity half of its rows, by the held dictionary) and MUST never be mutated or reused afterwards. As a
  defence the skip never applies when the retained view and the rows being decoded are views of the same
  underlying object (`previous.obj is ents_mv.obj`), so a buffer overwritten in place is validated in full.
- **Gate image and the absolute memory bound.** The registered `conformance` lane (`HOB-4`) runs
  `python/tests` on `tester-unified-rust`, whose `python3` is Debian's apt package: CPython 3.13.5 on
  Debian 13 (measured by running `python3` in the `rs-20261118` image). The absolute 1,000-row peak bound
  (16 KiB) was measured on CPython 3.14.10 only (3,395 B there) and is skipped on every other version, so
  the lane enforces the growth and line-count gates and no absolute bound; no 3.14.10 number is claimed
  for 3.13.5. Per the §7.8 rule the absolute bound is re-measured and re-set in the change that moves the
  lane's image (or CPython) to a version the bound was not measured on.
- **Dictionary reuse.** A dictionary section byte-identical to the held dictionary's body returns the held
  object (`Block.dictionary is held`); `Block.dictionary` is the carried dictionary (`None` for a bare
  block) and `Block.dictionary_in_force` the one the block was judged against.
- **Byte order.** `wire._NATIVE_VIEWS` (`sys.byteorder == "little"` at import) selects casts; the copy path
  is `array.array` plus `byteswap()` when `sys.byteorder == "big"`, and both are tested by patching
  `_NATIVE_VIEWS` and `sys.byteorder`.
- **`rate()`** returns a dict for both outcomes (`ok` true with `delta`, `denominator`, `per_second`,
  `missed`; `ok` false with `reason`, `reading`, `quality`); `per_second` is `delta * 10**9 / elapsed` over
  Python ints (exact until the one final division); an unknown quality or a non-`u64` value raises
  `ValueError`.
- **Line counts.** The §7.8 model counted 141 `line` events; this decoder counts 510 at both 100 and 1,000
  rows (one function walks the passes of §3.10); the gate is the equality, not the number.

*P3b implemented* (`python/hostobs_client/{conn,envelope,profiling,mark}.py`; `__init__.py` exports them;
P3a files `wire.py`, `stream.py`, `rates.py` are unchanged). Tests: `python/tests/test_client_{conn,path,
zipimport}.py` with the scripted fake probe `fake_probe.py`. Decisions and ambiguities:
- **`envelope.py`** (not named in the P3 module list) ports the byte rules of `crates/proto/src/envelope.rs` for
  REP, END and EVT: one object with `{` first and `}` last, nesting at most 8 (a C-speed bracket count with
  strings removed, before parsing), the envelope's own keys compared as written (an escaped spelling is an
  unknown key), the `ok` literal, exact `u32` counts, `closing` with exactly `event` and `reason`. Beyond Rust
  it enforces §4.2 below the envelope with the `json` module and hooks: no duplicate key at any depth, no
  `NaN`/`Infinity`, no non-finite float, no `-0`. A violation is a local `EnvelopeError` (a `WireError` subclass
  with kinds `json-text`, `duplicate-key`, `unknown-key`, `missing-key`, `wrong-type`); the connection closes.
  `ProtocolError` (also a `WireError` subclass) names what is not a decoder error: `unexpected-frame`,
  `unknown-corr`, `bad-reply`, `end-counts`, `hello-mismatch`, `queue-overflow`, `corr-exhausted`.
- **Handshake.** The prefix (`HOB\x01`, major 1, minor 1) is sent alone, the 8-byte server prefix is read with a
  `recv_into` that never over-reads, then the `hello` REQ goes out with `corr` 1. A closed socket before the
  prefix is `ConnectionLost`; a wrong magic is `unknown-mode`; another major is `unsupported-version`; a server
  minor above the client's, a `proto` that disagrees with the prefix, another `level` than requested and a missing or
  non-`u32` limit are `ProtocolError` `hello-mismatch`. Unknown extra `hello` keys are ignored (a later minor
  adds reply keys, §9.1). After the hello the reader is replaced by one limited to
  `min(16 MiB, limits.max_frame_bytes)`; the completion age stays 30 s.
- **Receive loop.** One `recv_into(reader.fill())` per step; the socket timeout is `max(0.001, (deadline - now))`
  with `deadline = frame_started + 30 s` while a frame is partial and the caller's operation deadline
  (`now + timeout_s`) otherwise; a `TimeoutError` or a late recv runs `poll(clock())`. An operation timeout
  closes the connection (a late reply could not be matched) and raises `OperationTimeout`. EOF inside a frame is
  `ConnectionLost` caused by the `truncated` `WireError`; a `closing` EVT is remembered in `closing_reason`.
  `connect()` itself is bounded by the OS through the socket timeout, not by the injected clock.
- **Streams.** `request()` waits for its REP. `blocks()` awaits the REP at call time and returns an iterator
  (`.result`, `.end`, `.received`, `.close()`); `subscribe()` returns a `Subscription` (`.dropped` sums the block
  header and END counters, `.client_dropped` counts blocks this client overwrote, `.close()` sends `unsubscribe`
  and waits for the END). Frames of other streams that arrive while waiting are decoded in arrival order and
  queued (a subscription keeps its newest 8, a query stream up to 1,024, else `queue-overflow`). The END must
  count exactly the BLOCK frames received; a query END reports `dropped` 0; the reason is `complete`/`page` for a
  query (`page` iff `next`) and `unsubscribed`/`client-eof` for a subscription. A subscription wait is bounded by
  `timeout_s + interval_ms`. Closing an unfinished block stream drops its later blocks undecoded.
- **Reconnect (§6.4).** `Connection.reconnect()` closes the connection (every dictionary and stream is dropped)
  and opens a new one on the same path with the same `StreamDicts`; `.session` is `first`, `same` or `changed`
  from `on_hello`. The caller drops cursors, open span keys, sessions and windows when it is `changed`.
- **`profiling.py`.** `require_profiling(level, required, *, features=(), ...)` returns a `ProfilingState`
  (`name`, `required`, `detail`, `notice`, `action`, `connection`). Plan D.2 leaves open how a usable socket is
  named and where `absent` and `lost` come from; decided: the sixth value `available` (`action` `run`, open
  connection), `absent(required)` for the consumer's own `except ImportError`, `lost(required, reason)` after
  start with `loss_reason()` mapping `session-lost`/`window-lost` and `session == "changed"`, `unconfigured` =
  `HOSTOBS_<LEVEL>_SOCKET` unset or empty, `refused` = another level, a missing `features` entry (the caller names
  the capabilities, default none, so `peak_reset` is not assumed), `unsupported-version`, or a server `forbidden`/
  `unsupported-feature`/`unknown-verb`; every other failure is `unreachable`. Actions: unset `required` →
  `run-with-notice` (`lost`: `record-profile-error`); `required` → `refuse` (`lost`: `evidence-invalid`, outcome
  `EVIDENCE_INVALID`).
- **`mark.py`.** `hostobs-mark` is JSON mode (§8.5): one `annotate` line with `corr` 1 to
  `HOSTOBS_OBSERVER_SOCKET`, `shutdown(SHUT_WR)`, one reply line (bounded 64 KiB, deadline from the injected
  clock), exit 0; unavailable → one stderr notice and exit 0, `--require` → exit 1, usage error → exit 2. All
  `annotate` args keys are sent (`key`, `outcome`, `code` null when unset); `ctx` is read from
  `HOSTOBS_CTX_{RUN,LANE,CANDIDATE,TXN,SESSION}` (the five keys of §4.10; the plan names `HOSTOBS_CTX_*` without
  the list). Run as `python3 -m hostobs_client.mark begin|end|point NAME [--key K] [--outcome O] [--code C]
  [--attr K=V] [--require] [--timeout S]`; the console script `hostobs-mark = hostobs_client.mark:main` is the
  wheel's (M3) packaging entry.
- **Linear time.** The text checks never run a quadratic scan on hostile input: the payload is parsed first (the C
  decoder is linear, refuses an unterminated string and stops a depth bomb at the recursion limit) and the
  bracket-depth count (strings removed by regex) runs only on text that parsed. The top-level key scan uses an
  unrolled pattern. Tests assert a wall-time cap on 20,000-repeat payloads and, structurally, that the depth count
  is never called on text that did not parse.
- **Queue policy is a client decision beyond this specification.** A subscription keeps its newest 8 undelivered
  blocks (`client_dropped` counts the overwritten ones) and a query stream up to 1,024 blocks and 64 MiB of frames
  per connection (`QUERY_BYTES`), then `queue-overflow`. The bounds are count- and frame-byte-bounded, the oldest
  block of a subscription is the one shed (stale-ordered), and a subscription's bytes are not bounded (8 frames of at
  most 16 MiB). The receive loop returns as soon as the awaited stream has a block, so `client_dropped` only grows
  while another call (a request, another stream) is pumping.
- **Validation.** `Connection.open` validates `level`, `client` and `timeout_s` (a finite number in `(0, 1 year]`;
  `inf`, `nan`, `bool`, strings are `ValueError`) before creating a socket; a connect failure is the system's
  `OSError`, unchanged (`require_profiling` maps it to `unreachable` with its errno), everything after the connect is
  `ConnectionLost`/`OperationTimeout`/`ServerError`/`WireError`. `hello` fields are exact integers (`proto`, `epoch`
  in `0..2**64-1`, every limit a `u32`), and a hello whose `max_request_bytes` cannot carry the client's own hello or
  whose `max_frame_bytes` is below 12 is `hello-mismatch`. `hostobs-mark --timeout` outside `(0, 1 year]` is a usage
  error (exit 2). Args nested deeper than the interpreter's recursion limit are a `ValueError` like any other deep args.
- **Not covered here:** a run against a live probe (P5b); the Python tests run in the registered `conformance` lane (`HOB-4`).

**P5a — Rust `client` crate (M).** `Client::connect(path, Level, ClientInfo) -> Result<Client, ClientError>`,
`hello() -> &HelloResult`, `request(verb, serde-free JSON text) -> Reply`, `subscribe(Selection,
interval_ms) -> Subscription` (iterator of `Block`, held dictionary per stream), reconnect per §6.4.

*P5a implemented* (`crates/client/src/{client,error,hello,json,level,streams,transport}.rs`; tests
`crates/client/tests/client_scripted.rs` plus unit tests per module). The behavioural twin is the Python client of
*P3b*; the decisions of that note (handshake order, `corr` from 1 with exhaustion, REP matching by `corr`, unknown
`corr` and unexpected frames as protocol errors, errors close the connection, exact `hello` validation including
`max_request_bytes` fitting the client's own hello and `max_frame_bytes >= 12`, `StreamDicts::on_hello` at every
hello, the END counts and reason checks) hold. API:
- `Client::connect(path, Level, ClientInfo)` and `Client::from_transport(Box<dyn Transport>, Box<dyn Clock>, Level,
  ClientInfo, Option<Redial>)`; `ClientInfo { name, timeout }` (`timeout` in `(0, 1 year]`, default 10 s; the name 1-64
  bytes without control characters; a violation is `ClientError::Usage` before any socket exists). `Transport` is
  `read(buf, timeout)`, `write_all(data, timeout)` and `close()`, implemented for `UnixStream`; `Clock::now_ns()` has the
  `Instant`-based `MonotonicClock`.
- `hello() -> &HelloResult { minor, level, server, boot_id, epoch, features, verbs, limits: Limits }`; `session()` is
  `Session::{First, Same, Changed}`; `closing_reason()` is the last `closing` EVT's reason.
- `request(verb, args_json) -> Reply { text, result_json() }`: `verb` is 1-64 bytes of `[a-z0-9_.-]` and `args_json` one
  JSON object; the payload goes through `check_req` (depth 8, UTF-8, only `verb` and `args`) before sending, a refusal
  is `Usage("bad-verb" | "bad-args" | "request-too-large")` and sends nothing; an error reply is
  `ClientError::Server { code, message, detail_json }`, and a connection-fatal code (§5.4) closes the connection.
- `blocks(verb, args_json) -> BlockStream` and `subscribe(selection_json, interval_ms) -> Subscription`, both
  `Iterator<Item = Result<Block, ClientError>>`; `selection_json` must be exactly one JSON object (so it cannot add a
  key to `args`, `Usage("bad-selection")`). `Subscription::dropped()` sums the header and END `dropped` counters.
- `reconnect() -> Result<Session, ClientError>` closes, redials, redoes the handshake and drops every dictionary;
  `close()` is idempotent and `Drop` closes.
- `ClientError`: `Io(kind)`, `Wire(ProtoError)`, `Envelope(EnvelopeError)`, `Server { .. }`, `Timeout`,
  `ConnectionLost`, `Protocol(ProtocolKind)`, `Usage(&'static str)`; `kind()` is the wire kind, the §10 code or the
  stable local name. Reset-like I/O kinds are `ConnectionLost`, `WouldBlock`/`TimedOut` on a write are `Timeout`.
Differences from the Python client:
- **One stream at a time.** `blocks()` and `subscribe()` return streams that borrow the `Client` mutably, so no other call
  can interleave and the client keeps **no queue** of undelivered blocks: the kernel socket buffer and the server's §5.3
  hub are the queues, and there is no `client_dropped` counter and no `queue-overflow`. A second concurrent subscription
  needs a second `Client`. The Python client's newest-8 / 1,024-block / 64 MiB queue policy has no Rust counterpart.
- **Stream end and reconnect.** Dropping or `close()`-ing an unfinished stream reads and discards the rest (blocks are
  counted, not decoded; a subscription sends `unsubscribe` first and tolerates `not-found`). A lost connection yields one
  `Err(ConnectionLost)` and then `None`; because streams borrow the client, none can outlive a `reconnect()`, so the
  caller resubscribes.
- **EOF.** Clean EOF and EOF inside a frame are both `ConnectionLost` (the Python client also reports the `truncated`
  cause); `Wire(Truncated)` is never returned by the client itself.
- **Timeouts.** `connect()` itself is not bounded by the injected clock (the OS bounds it); every later wait is: operation
  deadline `now + timeout` (strict `>`), the completion age of a partial frame 30 s from its first byte, transport timeout
  `max(1 ms, remaining)`, a subscription wait adds the interval, and a timeout closes the connection.
- **Hello arrays.** `features` and `verbs` must be arrays of strings (Python only requires a list); `quality_codes` must
  be an array. The `hello` result (9 keys) and its `limits` object (19 keys, at most 25) are read with
  `proto::envelope::members_up_to(bytes, 64)`: the 8-key cap of `members` is the envelope's, and the bound of 64 keeps
  the quadratic duplicate-key check finite. Every other object the client reads by key (error object, END, EVT,
  `subscribe` result, selection check) has at most 5 keys; `status`, `procs` and other results are returned as text and
  never parsed by the client.
- **Frames after the hello.** After the hello the reader's limit is `min(16 MiB, hello.max_frame_bytes)`. Frames the
  pre-hello reader completed in the same read are re-checked against it (`oversized-frame`); a frame only partly
  buffered at that moment keeps the 16 MiB bound of the old reader (parity with Python).
- **Not `Send`, one stream per client.** `Transport` and `Clock` have no `Send` bound, so a `Client` stays on its thread;
  while a stream borrows the client no other call can be made.
- **Waits.** Waiting for a frame to start is bounded by the timeout; a partly received frame only by its 30 s completion age
  (§7.3), so a server that chains frames can exceed the timeout, as in Python.
- **END errors.** An `END ok:false` adds its `dropped` to `Subscription::dropped()`; a connection-fatal code in an
  `END ok:false` closes the connection (Python closes only on REP errors).
- **Corr exhaustion** (`ProtocolKind::CorrExhausted`) is reached after 2^32 - 1 requests; a unit test sets the counter near
  `u32::MAX`.
- The `proto` pieces reused: `FrameReader` (client limit `min(16 MiB, hello.max_frame_bytes)` once the hello is in and no
  partial frame is buffered), `StreamDicts`, `decode_block_with`, `check_rep`/`check_end`/`check_evt`/`check_req` and
  `members`.
**P5b — probe serving (L).** Accept loop per level from `LISTEN_FDS`, mode detection, handshake, verb
dispatch with strict arg checking (§4.2 JSON text rules, canonical selections), shared selection
streams (one bare encoding per tick, the dictionary form lazily, one generation counter per selection,
§6.2), one `SendHub` for all connections, `ConnTimers` per connection, issuer derivation (§4.12),
`series_batch` packing (§4.9), the §5.1 table as config keys. Acceptance: the connection cases of
§11.4 and a socketpair conformance test per row of §5.1 and per verb of §4.3 marked M2, against a fake
clock; the §5.1 reply-size bounds for every paged verb. Closes `HOB-54`.

## 13. Decisions taken, divergences, open questions

### Decisions taken (D-W31..D-W48 are held by the parked P5b-3 branch)

| ID | Decision | Reason |
|---|---|---|
| D-W1 | Server answers the binary prefix with its own 8-byte prefix carrying the negotiated minor; on a major mismatch it sends its prefix and closes | the prefix is the only structure that can survive a major change, so refusal must live there |
| D-W2 | `hello` is mandatory first in binary mode, optional in JSON mode | binary clients are libraries that need limits; shell one-liners (`hostobs-mark`) must stay one line |
| D-W3 | REQ payload `{"verb","args"}` with exactly those keys; JSON mode adds `corr` | one strict envelope; `corr` lives in the frame header in binary |
| D-W4 | Block verbs answer REP (stream header) + BLOCK* + END; JSON verbs one REP | a caller learns acceptance before data and the stream end is explicit, incl. mid-stream failure |
| D-W5 | A client holds a dictionary per stream `(connection, corr)`; the server numbers generations per producer of the bytes (one counter per live selection stream, shared by its subscribers; one per query stream); any change (also an append) = a new complete generation inside the next BLOCK; the first block of every stream is dictionary-bearing at whatever generation is current | no delta machinery (a dictionary-bearing tick at 1,000 cgroups is 1.5× a bare one, SP-4); shared encoded bytes force a shared `dict_gen`, so the client rule is relative ("exceeds held"), never "starts at 1" |
| D-W6 | `DICT` frame stays reserved and unsent in 1.0 | the in-block dictionary already serves live, query and segment paths (`HOB-6` resolved as "reserved") |
| D-W7 | Shared encoded blocks; per-client `corr` and `dropped` patched into a private prefix copy when the entry is pinned (first `head()`) | §3.1's decoder rule makes the header the first body, so both fields sit at fixed offsets; one encode per selection and form |
| D-W8 | `dropped` stamped at pin; pinned entries are never dropped; at most one unpinned Live entry per stream (a newer tick replaces it, counted); a dropped or replaced dictionary-bearing entry sets a sticky `needs_dict` | every drop happens to an unpinned entry, so every drop is counted exactly once (next pinned block or END); coalescing makes the dictionary repair local to the entry that replaces it |
| D-W9 | History blocks name their subject with new required section kind 6 (minor 1) | the implemented time block has no entity reference, so a multi-subject `series_batch` cannot be attributed |
| D-W10 | Gauge min/max/mean = separate history blocks, one aggregation each; agg must be permitted by every column's mask | no layout change for `HOB-5`; the mask already encodes what is permitted |
| D-W11 | `procs`, `events`, `spans` replies are JSON pages, not BLOCKs | per-process strings and the `(pid, start_ticks)` identity do not fit the u64 entity model; pages are ≤ 256/1,000 items |
| D-W12 | Error codes are kebab-case (`bad-request`, not `bad_request`) | every other code in plan and codec is kebab-case |
| D-W13 | JSON mode carries no block verb; amended by D-W51: it is not served at the block levels at all | a second JSON rendering of blocks would be a second codec to keep conformant |
| D-W14 | Python reads each frame into its own exact-size `bytearray(len)` after the header validates against the client limit (16 MiB), the one exception to "never allocate by `len`"; the §5.2 machine is otherwise shared, with an injected clock and a deadline-based `settimeout` | decoded views stay valid as long as the caller holds the block; the only peer is the probe chosen by socket path; one allocation per frame is cheap at ≤ 120 KB/s |
| D-W15 | Python gates are deterministic: equal `sys.settrace` line counts at 100 and 1,000 rows, and `tracemalloc` peak growth ≤ 4 KiB plus an absolute ≤ 16 KiB enforced on CPython 3.14.10 only (the registered `conformance` lane runs 3.13.5 and skips it; re-measured on any version change, patch level included); CPU ratio and absolute times are published only | measured (§7.8): the ratio of the correct path (5.6-5.7) and of a per-value loop (7.5-7.9) are too close to gate; line counts (141 = 141 vs 1,733 / 16,133) and memory growth (2,088 B vs 31,136 B for `list()`) separate them without load dependence |
| D-W16 | Client-side reader limits 16 MiB / 30 s; server limits per level as §5.1 | the probe must not be cut by its own client under host load; the probe's stall bound (5 s) protects the probe from clients, the reverse protection only needs to bound a dead probe |
| D-W17 | Section layouts are frozen at version 1; changes take new kind codes | the implemented decoder refuses a known kind with another version even when optional |
| D-W18 | One `SendHub` owns every connection's queue and three pools: Live (droppable, 4 MiB / connection, 32 MiB global), Query (never dropped; a block that does not fit is refused, D-W49; 1 / 4 MiB, D-W52) and control (2 MiB / connection, a frame into an empty control queue always admitted); a pool overflow sheds the oldest unpinned Live entry of the largest connection that has one, else the incoming tick is dropped; queries never shed ticks. Amended by D-W53: no per-uid pools | a REP/END can no longer close a lagging client; history and snapshot replies never lose data and a history loop cannot churn other clients' ticks; one slow group member cannot close another (finding 4, round 2 D) |
| D-W19 | Over-cap connections are refused by close without a byte; `busy` is not a code | the mode is unknown before the first byte, so no reply can be framed (finding 8) |
| D-W20 | Hello deadline 5 s from accept, idle deadline 300 s, per-uid connection caps (half the level cap at operator, observer and control; equal to it at observe) and selection caps (8 of 8, equal to the level's); amended by D-W53 | a silent or idle connection cannot hold a slot (finding 9); the per-uid caps must not bind below the level caps where telemetry clients share service uids (D-W53) |
| D-W21 | Selection bounds 16 entities, paths ≤ 512 B without control characters; observe/operator requests ≤ 32 KiB, observer ≤ 16 KiB; annotate attr key ≤ 64 B, value ≤ 256 B | the worst legal request, at 3× JSON escaping, fits with margin (§4.2, §4.12 arithmetic), so a legal request is never `frame-too-large` (finding 10) |
| D-W22 | Received-frame rules: every section `len` ≡ 0 (mod 8) (`section-length`), directory entry 0 is the header (`section-order`, checked after the rank check, §3.10, so the pinned `section-order` fixture is unchanged) | the alignment, the header offset and the `dropped` offset are then true for received frames, not only for encoded ones (finding 12) |
| D-W23 | A repeated dictionary identity is `duplicate-dictionary-entry` whatever the other fields | one outcome per defect in both languages; the Python reference already does it (finding 22) |
| D-W24 | `series_batch` packing is subject-major, then agg in request order, then time; metrics no agg permits are listed in `omitted`; history headers carry `seq` and mono fields 0 | one byte sequence per answer, so fixtures and cursors are deterministic (finding 11) |
| D-W25 | Every pinned head must be completely written within `frame_completion_ms` (5 s) of its pin; the writer stall rule stays for a queue whose head is not pinned; (per-uid pools: retired by D-W53) | a reader that accepts a byte every few seconds never trips a byte-progress rule, and with 8 connections per uid could otherwise hold the whole global pool (round 2 B) |
| D-W26 | Every JSON REP is ≤ `max_reply_bytes` = (`control_queue_bytes` − 256 KiB) / `max_outstanding` = 448 KiB; paged verbs end the page early with `next`; `cmdline` ≤ 4,096 B, `provenance` ≤ 64 objects | a legal reply can never overflow a healthy client's control queue, even with `max_outstanding` such replies unread (§5.1 reply sizes; round 2 C) |
| D-W27 | The hub keeps `last_pushed_gen` per stream; a Live tick whose generation differs from it is pushed in the dictionary form | a subscription whose interval skips the tick that changed the shared generation would otherwise get a bare block for a generation it never received (round 2 A) |
| D-W28 | A connection-fatal outcome yields exactly one final frame held outside the hub (`Conn::take_final`), written once by the driver and abandoned on a short write, only when no head is partly written; if nothing was written and the server prefix is queued it is prefixed by the prefix; a malformed REQ in `AwaitHello` is a non-fatal `bad-request` on its own `corr` | §5.2 and §5.4 disagreed (hub frame vs direct write; `hello-required` vs `bad-request`); a bad request in the same read as the prefix must not leave the client without a handshake (P5b design R1, R2, R6) |
| D-W29 | `subscribe` performs no backend resolution or block encoding; its first BLOCK comes from the next due sampler tick, creator and joiner alike | encoding at subscribe made subscribe/unsubscribe churn a CPU amplifier; the cost is at most one `tick_ms` of latency (P5b-2 S9/S10) |
| D-W30 | **Retired, see D-W49.** Was: a backpressured `current` holds its request and selection, never its block bytes, and re-resolves the latest tick when Query room appears | no request waits any more, so none is held or re-resolved |
| D-W49 | No request waits: every request is answered (REP, every BLOCK, END queued) or refused before its REP in the server turn that reads it; no request record survives the turn; a connection's only request state is its live subscriptions, so "in use" (§2.3) and the idle and Draining conditions (§5.4) count subscriptions only; there is no retry pass. Scope: the observe and operator request verbs of this document; later stream verbs (control relays, `session.watch`, `export`) state their own rule when they are specified | the pending machinery (request records, retry pass, re-resolution, uid rotation, exactly-once cleanup on every close path) was the complexity centre and held most P5b-3 defects, for a local daemon with ≤ 16 clients; a client can retry, and a cursor already is a backpressure mechanism (review F3) |
| D-W50 | Refusals (§5.1): a one-block verb (`current`, `snapshot_at`) is `too-many-requests` while the connection's Query pool is not empty (step 10), else `budget {"pool":"query"}` when the global pool has no room for its block; `series_batch` is `too-many-requests` at 2 streams with queued blocks per connection (or when its first block does not fit the connection pool) and `budget {"pool":"query"}` at 8 per probe (or when its first block does not fit the global pool), and ends its page early with END `page` + `next` when the next block does not fit; `max_outstanding` stays in `hello` as the reply-bound factor only | a refusal before the REP keeps §2.4's shapes (no `END ok:false` for a query that never started); `too-many-requests` names the connection's own unwritten query blocks (the pool empties as bytes reach the socket), `budget` the shared pool; every page carries a block, so each continuation progresses |
| D-W51 | JSON mode is served at observer and control only; at observe and operator a `{` first byte is answered with one `binary-required` line (`corr` 0) and the connection closes, at mode detection (§1.3); `binary-required` becomes connection-fatal and §4.2 step 8 is retired | the JSON consumers (`hostobs-mark`, run-gate control, assay observer) live at observer and control, and JSON mode at block levels could only do `hello`, `status`, `unsubscribe` (review F11); refusing at the first byte needs no line reader or parse there and reuses the `unknown-mode` one-line path; `hello` is optional in JSON mode, so it cannot be the point, and waiting for the first line would parse a request only to refuse it; `binary-required` was otherwise unreachable (BLK verbs are `forbidden` at JSON levels) and tells the client what to do |
| D-W52 | `tick_ms`, `max_rows`, `max_block_bytes`, `client_queue_bytes`, `query_queue_bytes` and the history caps are probe-wide keys (the `hello` ones announced equally at observe and operator); defaults `max_rows` 1,024, `query_queue_bytes` 1 MiB (global Query 4 MiB), `max_block_bytes` 1 MiB, history concurrency 2 per connection / 8 per probe (applied when history is served); Live defaults unchanged | the per-level values were min/max-unified across observe and operator, so a per-level setting could not take effect (review F7); sized to the workload (≤ 500 cgroups, 5k PIDs, ≤ 16 telemetry clients): 1,024 rows is 2× the cgroup count, not 8×; worst-case query RAM including queued Query bytes is 6 MiB ≤ 9 MiB, and the widest 1,024-row block (890,624 B) still fits `max_block_bytes` (§5.1 "Query RAM") |
| D-W53 | No per-uid Live or Query pools; the per-uid connection and selection caps stay (D-W20) but never bind below the level caps at observe: per-uid connections 16 = the observe level cap, per-uid selections 8 = the level's selection cap (operator's per-uid connection cap 4 of 8 stays: operator clients are interactive tools, not shared-uid telemetry) | the Live victim rule already sheds the largest holder, so a uid's many connections shed themselves first; a per-uid Query pool equal to one connection's (×1) would serialize every client of one service uid, the expected case for ≤ 16 estate clients under a few uids, and leave the global pool unreachable; for the same reason the per-uid caps must not refuse those clients before the level is full; global caps keep the memory bound unchanged. Residual: any few slowly reading connections, of one uid or several, can keep the global Query pool full; other clients get `budget {"pool":"query"}` and retry. Each pinned block must complete within `frame_completion_ms`, so the hold lasts only while those clients keep reading |
| D-W54 | A process epoch is the `CLOCK_BOOTTIME` nanosecond value at process start; nothing is persisted | monotonic within a boot, so greater than every earlier epoch of that boot; BOOTTIME so it matches the header's `boot_ns` timeline |
| D-W55 | A live block has one row per published cgroup incarnation and the live-block contents paragraph's dense, quality-bearing columns; only registered non-derived metrics are published, and host and daemon metrics are root-row only | absence is a quality, never zero; a bounded tick makes a cut walk visible and gives `current {"all":true}` a truthful `max_rows + 1` refusal |

### Divergences (code or plan vs this document)

| ID | Where | Code / plan says | WIRE.md says | Work |
|---|---|---|---|---|
| V-1 | `frame.rs` `time_body_len` (line 741 on this document's base; post-P0 line 768), `hostobs_wire_ref.py` `time_body_len` (line 380 on the base; post-P0 line 384) | `align8(14·n)` | `align8(15·n)` | fixed by the P0 bug fold-in (merged to main; commit `e13027cfe`: `TIME_BUCKET_BYTES = 15`, fixture `history-five-buckets`, no minor bump) |
| V-2 | `frame.rs` `decode_block` | references checked only against an in-frame dictionary | also against the held generation | fixed by P2a1 (`HOB-46`, `decode_block_with`) |
| V-3 | `frame.rs` `decode_block` | bucket `agg` not checked against the metric mask | `aggregation-not-permitted` | fixed by P2a1 (`HOB-47`) |
| V-4 | plan §B.2 `TIME` | no subject; `series_batch` returns several entities | kind 6 `subject` | fixed by P2a1 (`HOB-48`) |
| V-5 | `FrameHeader::parse`, `decode_block` | no `len` bound; `SECTIONED` allowed on any type; directory capacity from `n_sec` before bytes are proven | §2.1, §11.1 | `len` bound: P0 (`HOB-8`, commit `6724fe8be`); the rest fixed by P2a1 (`HOB-50`; `block-as-req` regenerated with the flag cleared) |
| V-6 | plan §B.1 | `DICT` = dictionary delta or full generation | reserved, unsent | none (D-W6) |
| V-7 | plan §B.1 | procs page is a BLOCK | JSON page | none (D-W11) |
| V-8 | plan §B.3 | `bad_request` | `bad-request` | none (D-W12) |
| V-9 | plan §B.3 dictionary text | ≤ 256 B UTF-8 | also no bytes < 0x20 or 0x7F (as `dict.rs` `validate`) | none |
| V-10 | `hostobs_wire_ref.py` line 5 | cites `hostobs/docs/WIRE-COLUMN-CODEC.md` (does not exist) | this file | `HOB-49` |
| V-11 | `frame.rs` `decode_block` 1086-1126, `hostobs_wire_ref.py` `decode_frame` 509-535 on the P2a1 base | any body length; an unknown section may precede the header (measured on the Python reference: an optional `len = 5` section decodes, frame `len` 357; placed first, the header body starts at 69) | §3.1 decoder rules | fixed by P2a1 (`HOB-51`, fixtures §11.3) |
| V-12 | `dict.rs` `decode` (891 → `intern` 596) on the P2a1 base | metric name repeated with other facts → `conflicting-column` | `duplicate-dictionary-entry` (Python reference already, measured) | fixed by P2a1 (`HOB-52`) |
| V-13 | `hostobs_wire_ref.py` `decode_frame` 484-493 on the P2a1 base | buffer length and `SECTIONED` checked before `not-a-block` | §3.10 precedence (Rust order) | fixed by P2a1 (`HOB-53`) |
| V-14 | plan §B.3 | `(epoch, dict_gen)` probe-global | generation per selection stream / query stream; client rule relative to held | none (D-W5) |
| V-15 | plan §B.4 | full queue drops the oldest unsent BLOCK, counted in "the next `HDR.dropped`"; `DICT/REP/EVT` over budget close | at most one unpinned tick per stream, drop-oldest across streams (victim: the largest connection with an unpinned Live entry, else the incoming tick), stamp at pin; the plan's separate query budget is kept as its own Query pool (1 MiB / connection, 4 MiB global, sizes are WIRE.md's) whose blocks are never dropped and never shed a tick; control frames on their own budget | none (D-W8, D-W18, D-W53) |
| V-16 | plan §B.4 | observe/observer requests 4,096 B; one 5 s "frame completion age" | 32 KiB / 16 KiB (D-W21); the 5 s frame completion age is **retained** on the send side (`frame_completion_ms`, pin → completely written; it is what bounds a dribbling reader, D-W25), plus server request deadline 5 s / 25 s (control, plan's RG55 value), writer stall 5 s for an unpinned head, client reader 30 s (D-W16) | none |
| V-17 | plan §B.2 | gauges carry min/max/mean columns | one aggregation per block, separate blocks per agg | none (D-W10) |
| V-18 | plan §B.1 | JSON mode for "ctl, shell, launchers, debug" (all verbs) | JSON mode refuses block verbs; `hostobsctl --json` renders them | none (D-W13) |
| V-19 | plan §A.2 | verbs listed per level only | `status`, `events`, `spans` also at control (relayed), `unsubscribe` at observe/operator | none (§4.3) |
| V-20 | plan §B.4 | clients per level only | per-uid connection and selection caps, hello and idle deadlines, refuse without a byte | none (D-W19, D-W20) |

### Open questions (both DECIDED)

1. **Client-side completion age: DECIDED, 30 s** (D-W16; the server keeps its 5 s). Reason: a
   trusted probe under host load must not be cut by its own client, and the probe's stall bound
   (5 s) already protects the probe.
2. **Who may read spans: DECIDED, every `hostobs-observe` member** (plan §I question 4). Reason:
   this is the plan's recommendation, and WIRE.md serves `events`/`spans` at observe level on
   that basis.

### Review round 1 (independent review of rev 1 at `7f1f17888`, verdict FIX)

| # | Finding (severity) | Disposition | Where fixed |
|---|---|---|---|
| 1 | per-stream generations contradict shared blocks (Blocker) | fixed: counter per selection stream, client rule relative, first block dictionary-bearing at any generation | §6.2, D-W5, V-14, `client-first-block-gen-5` |
| 2 | `dropped` lost when the stamped head is dropped | fixed: stamp at pin, pinned entries never dropped | §5.3 Pin, D-W8, `hub-pin-stamps`, `hub-pinned-never-dropped` |
| 3 | `last_progress` undefined after idle | fixed: set on empty → non-empty and on every `wrote(n > 0)` | §5.3, `hub-stall-after-idle` |
| 4 | REP/END close a lagging client; global cap has no owner | fixed: control frames on their own budget; `SendHub` owns the global budget, victim = largest queue's oldest unpinned Live entry (refined in round 2 D: pools, victim must hold an unpinned Live entry) | §5.3, D-W18, `hub-control-cap`, `hub-global-largest` |
| 5 | query blocks droppable; blocks above 4 MiB unqueueable | fixed: Query entries are backpressure; `max_block_bytes = client_queue_bytes` | §5.1, §5.3, §4.9, `hub-query-backpressure` |
| 6 | dictionary repair undefined (no next block, cascade) | fixed: coalescing + sticky `needs_dict`; a repair that does not fit is dropped and stays flagged | §5.3 Form, `hub-needs-dict-sticky`, `hub-repair-over-budget` |
| 7 | `feed` cannot return a frame and an error | fixed: `Feed { frames, error }` | §5.2, §12, `reader-frame-then-bad-header` |
| 8 | `EVT closing busy` before the mode is known | fixed: refuse = close without a byte; `busy` removed | §1.2, §5.4, §10, D-W19 |
| 9 | no pre-hello/idle deadlines, no per-uid caps; 4-7 byte prefixes | fixed | §1.3, §5.1, §5.4, D-W20, `conn-*` |
| 10 | request limits unsatisfiable | fixed: bounds lowered, caps raised, arithmetic shown; attrs key/value defined | §4.2, §4.12, §5.1, D-W21 |
| 11 | history packing underspecified | fixed: deterministic packing, `omitted`, header values, minor-0 refusal | §4.9, D-W24 |
| 12 | alignment guarantees not enforced on decode | verified (Python reference run; Rust source read) and fixed by two decoder rules | §3.1, D-W22, V-11, two §11.3 fixtures |
| 13 | time-length fix coupled to minor 1 | fixed: P0 bug fix, no minor; `HOB-48` keeps only the subject half | §3.6, §9.4, V-1, §11.3, backlog `HOB-48` |
| 14 | §7.3 allocation and timeout contradict §5.2 | fixed: one machine; allocation exception stated; deadline-based `settimeout` | §7.3, §11.1, D-W14 |
| 15 | CPU gate does not do what it says | fixed with measurements: ratio published only; line-count gate added | §7.8, D-W15 |
| 16 | `tracemalloc` bound Python-version dependent | fixed: measured, CPython 3.14 pinned, re-derivation rule | §7.8, D-W15 |
| 17 | wrong cross-reference | fixed (§13 open question 1 via D-W16) | §5.2 |
| 18 | per-connection cap does not bound a share; "distinct" undefined | fixed: per-uid selection cap; canonical form | §4.2, §4.7, §5.1 |
| 19 | unlisted divergences | fixed | V-14..V-20 |
| 20 | error-vocabulary gaps, precedence | fixed: reader mapping, JSON text rules, two vocabularies, precedence | §3.10, §4.2, §5.2, §8.1, §10, V-13 |
| 21 | protocol details open | fixed: hello `corr`, END keys and reasons, `agg-name`, cursor binding, defaults, epoch, issuer, minor-0 `series_batch` | §1.4, §2.2, §4.2, §4.4, §4.9, §4.12, §6.4 |
| 22 | `conflicting-column` vs `duplicate-dictionary-entry` | verified (Python: all three repeats `duplicate-dictionary-entry`; Rust: metric with other facts `conflicting-column`); improved on the review's "list both": one outcome | §3.7, D-W23, V-12, two §11.3 fixtures |
| 23 | `HOB-5`/`HOB-6` rows stale | fixed | backlog |
| — | (found while verifying) `live-block.bin` layout figures | corrected: 5 sections, bodies at 56 | §3.1 |

### Review round 2 (independent re-review of rev 2 at `319f5d48b`, verdict FIX)

| # | Finding (severity) | Disposition | Where fixed |
|---|---|---|---|
| A | a subscriber whose interval skips the generation-changing tick gets a bare block it cannot decode (major) | fixed: `TickForms::generation()`, per-stream `last_pushed_gen`; any generation the stream was not last pushed goes out in the dictionary form | §4.7, §5.3 Form, §6.2, §12, D-W27, `hub-interval-skips-generation` |
| B | a dribbling reader holds the global budget; the plan's frame completion age was dropped (major) | fixed: a pinned head must be written within `frame_completion_ms` (5 s) of its pin; the stall rule stays, because it alone covers a head that is not pinned; per-uid pools = a quarter of the global ones (per-uid pools retired by D-W53) | §5.1, §5.3 `poll`, §4.4, §12, D-W25, V-16, `hub-frame-completion`, `hub-uid-cap`, `conn-dribbling-reader` |
| C | a legal reply can exceed the control queue and close a healthy client (major) | fixed: a frame into an empty control queue is always admitted; every JSON REP ≤ `max_reply_bytes` = 448 KiB, sized so `max_outstanding` replies plus END/EVT slack fit; pages end early with `next`; per-verb worst cases; `cmdline` ≤ 4,096 B, `provenance` ≤ 64 objects | §5.1 reply sizes, §4.4, §4.8, §4.10, §4.11, D-W26, `hub-control-cap`, `conn-reply-page-truncated` |
| D | the global victim may hold no sheddable entry; queries shed other clients' ticks (major) | fixed: the victim is the largest connection that holds an unpinned Live entry, none ⇒ the incoming tick is dropped (`DroppedIncoming`); Query blocks have their own pool (4 MiB / connection, 4 MiB / uid, 16 MiB global) and only ever wait (`Backpressure`) (the uid pool retired by D-W53, the wait by D-W49) | §5.1, §5.3 Fit, D-W18, V-15, `hub-victim-skips-pinned`, `hub-query-own-pool` |
| E | END `blocks` misses queued Query blocks (minor) | fixed: `blocks` counts a Query entry when it is queued (a Live one at pin) | §5.3 Pin, `end_stream`, `hub-end-stream` |
| F | entry-0 rule vs the pinned `section-order` fixture (major) | fixed: rank check first, entry-0 rule only for an unknown kind at entry 0; fixture directory read from the `.bin` (`[5, 1, 2, 3, 3]`), pinned `index: 1, kind_code: 1` stays valid, no fixture changes | §3.1, §3.10, D-W22 |
| G | Closing row and §12 API disagree; prefix machine unnamed (minor) | fixed: `head_partial`, `drain_unpinned`, `query_ready(c, stream)` (retired by D-W49), `enum Timer { Hello, Idle }` (the request deadline is the reader's completion age, no duplicate timer), `ModeDetect` with `at` handing surplus bytes to the reader | §5.3, §5.4, §12, `conn-prefix-surplus`, `conn-closing-evt` |
| H | previous-body view pins a whole frame; table provenance; CPython patch level (minor) | fixed: retention stated and bounded (one previous frame of ≤ 1 MiB per stream; a digest rejected, it adds a hash to the measured path and makes the skip probabilistic); table attributed to `bench2.py`, the `bytes()` row to `bench.py`; pinned CPython 3.14.10, re-measure on any version change, patch level included | §7.4, §7.8, D-W15 |
| I | V-11..V-13 and the stream machines have no backlog id (minor) | fixed: `HOB-51`..`HOB-53` (V-11..V-13), `HOB-54` (stream layer, P2/P3/P5b exit criteria) | backlog, V-11..V-13, §12, CHANGES.md |

Case counts (§11.4): 12 reader, 6 client, 19 hub, 22 connection.
