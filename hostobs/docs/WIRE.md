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

1.2 **Who speaks first.** The client. The server sends nothing before the client's first byte.

1.3 **Mode by first byte** (`crates/proto/src/frame.rs` `detect_mode`):

| First bytes from the client | Mode | Server action |
|---|---|---|
| `0x7B` (`{`) | JSON lines (§8) for the whole connection | read lines |
| `48 4F 42 01` (`HOB\x01`) `u16 major` `u16 minor` (8 B) | binary | §1.4 |
| a strict prefix of `HOB\x01` | undecided | wait; the 8 bytes MUST complete within 5 s, else close silently |
| any other byte, or a byte that diverges from `HOB\x01` | refused | write one line `{"ok":false,"corr":0,"error":{"code":"unknown-mode","message":"..."}}\n`, close |

1.4 **Binary handshake.**
1. Client → `HOB\x01` + `u16 major = 1` + `u16 minor = C` (its highest minor).
2. Server → `HOB\x01` + `u16 major = S_major` + `u16 minor = N` (8 B). If `major == 1`, `N = min(C, S_minor)`
   is the **negotiated minor**: neither side sends a section, flag, frame type, verb key or reply key
   introduced after `N`. If the client's major is not one the server speaks, the server writes its
   prefix with its own highest major and minor, then closes; the client reports `unsupported-version`.
   This 8-byte prefix is major-independent forever.
3. Client → `REQ` `hello` (§4.2) as its first frame, `corr = 1`. Any other first frame: server sends
   `REP corr=<that corr> ok:false hello-required` and closes.
4. Server → `REP` for hello. Only now are other verbs accepted.

The prefix is 8 bytes; every later byte belongs to a 12-byte-header frame (§2).

1.5 **Half-close.** A client `shutdown(SHUT_WR)` means "no further requests": the server finishes every
outstanding request (final `REP` or `END`), ends every live subscription with
`END {"ok":true,"reason":"client-eof",...}` and then closes. A server that closes for a reason
(§5.4) SHOULD first enqueue `EVT corr=0 {"event":"closing","reason":"<code>"}` when no frame is
partially written and the write does not block; a client MUST NOT depend on receiving it.

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
| EVT | server → client | `{"event": str, ...}`; events in 1.0: `closing` |
| DICT | — | reserved; a 1.0 peer MUST NOT send it; receiving one is `bad-frame` (§13 D-W6) |
| BLOCK | server → client | the column block of §3 |
| END | server → client | `{"ok": true, "blocks": u32, "next": str\|null, "reason": str}` or `{"ok": false, "blocks": u32, "error": {...}}` |

A frame type sent in the wrong direction is `bad-frame`. JSON payloads carry no BOM and no trailing
bytes after the object; integers follow §8.3.

2.3 **`corr`.** The client allocates `corr` for each REQ: non-zero, unique among its outstanding
requests and live subscriptions, and SHOULD increase by one per request. Every REP, BLOCK and END
answering a request carries its `corr`; every live BLOCK of a subscription carries the `subscribe`
request's `corr` until that subscription's END. `corr = 0` is reserved for server-originated EVT and
for the connection-fatal REP of a frame whose own `corr` cannot be trusted. A REQ with `corr = 0` or a
`corr` already in use is `bad-frame` (REP `corr = 0`, close).

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

Every body length is a multiple of 8, so **every body starts at a frame offset ≡ 0 (mod 8)**, and the
frame ends exactly after the last body (`trailing-bytes` otherwise). In `fixtures/wire/live-block.bin`
(344 B, 4 sections) the bodies start at offset 48.

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
always the first body, so `dropped` sits at frame offset `88 + 8·n_sec` and `corr` at 8: the two
per-client fields (§5.3). `seq` is the probe tick number within the epoch; `dropped` counts BLOCKs of
this stream dropped for this client since its previous delivered BLOCK of the stream (saturating).

3.4 **Rows body (kind 2)**: `n_rows × u32 ent` @0 (dictionary index, MUST name an Entity), then
`n_rows × u32 t_off_us` @4·n_rows (offset from `mono_start_ns`). Length exactly `8·n_rows`.

3.5 **Column body (kind 3)**: u32 `metric`@0 (MUST name a Metric), u8 `type`@4 (1 u64, 2 i64, 3 f64),
u8 `kind`@5 (0 gauge, 1 counter, 2 state; MUST equal the dictionary's kind, `column-kind-mismatch`),
u16 reserved@6 = 0 (`column-reserved`), `n_rows × 8 B` values @8, `n_rows × u8` quality @`8 + 8·n_rows`,
zero pad. Length exactly `align8(8 + 9·n_rows)`. A row whose quality is not `ok` keeps its 8 bytes
(any content, e.g. NaN bits) and they MUST NOT be read as a measurement.

3.6 **Time body (kind 4, history)**: `n × i64 bucket_real_ns` @0, `n × u32 covered_ms` @8n,
`n × u16 samples` @12n, `n × u8 agg` @14n (0 raw, 1 last, 2 delta, 3 mean, 4 min, 5 max), zero pad;
`n = n_rows`. Length exactly **`align8(15·n)`** (the implementation computes `align8(14·n)`: §13 V-1,
`HOB-48`). Every bucket's `agg` MUST be permitted by every column's metric mask (§3.7) — counters
raw/last/delta, gauges last/mean/min/max, state last (`aggregation-not-permitted`, `HOB-47`). Gauge
min/max/mean are sent as separate history blocks of the same subject, one aggregation per block
(closes `HOB-5` without a wire change).

3.7 **Dictionary body (kind 5)** (`dict.rs` `encode`/`decode`): u32 `generation`@0, u32 `count`@4
(≤ 65,536, `dictionary-too-large`), then `count` packed entries, then zero pad to 8. Entry: u8 `kind`
(0 entity, 1 metric, 2 label, 3 free), u8 reserved = 0, u16 `text_len` (≤ 256), kind fields
(entity: u64 `inc`; metric: u8 unit, u8 kind, u8 scope, u8 source, u16 mask which MUST equal the kind's
derived mask; label: u8 kind, u8 source; free: none), then `text_len` bytes of UTF-8 without bytes
< 0x20 or 0x7F. Entries are unaligned. Entry *i* is index *i*; a repeated identity (same inc, metric
name, label triple or free text) is `duplicate-dictionary-entry`. Code tables: `dict.rs` lines 48-155.

3.8 **Subject body (kind 6, ver 1, minor ≥ 1, required flag set)**: u32 `ent`@0 (dictionary index,
MUST name an Entity), u32 reserved@4 = 0. Allowed only with a time section (`subject-without-time`);
it names the one incarnation a history block describes. A minor-0 reader refuses it, which is correct:
a history block it cannot attribute must not be read. (`HOB-48` adds it to the codec.)

3.9 **Quality** (`quality.rs`, `fixtures/wire/quality.json`): 0 ok, 1 absent, 2 unreadable, 3 stale,
4 reset, 5 not-sampled, 6 unsupported, 7 unknown-topology; any other byte is `unknown-quality`. Only 0
is a measurement. Codes are append-only and announced in `hello.quality_codes`.

3.10 **Decoder error kinds** are the `ProtoError::kind()` names (`error.rs` lines 478-530), e.g.
`truncated`, `oversized-frame`, `section-order`, `stale-dictionary-generation`; P2 adds
`aggregation-not-permitted`, `subject-without-time`, `generation-regressed`. They are local outcomes
(fixtures `expect_error.kind`), never sent on the wire.

## 4. Requests and replies

4.1 **Error envelope**: `{"ok": false, "error": {"code": str, "message": str, "detail": object}}`;
`detail` is always present (`{}` when empty). In JSON mode both envelopes also carry `"corr"`.

4.2 **Common types.** `u64`/`i64`/`u32` = JSON integers in range; `name` = `^[a-z][a-z0-9_.-]{0,63}$`;
`cursor` = opaque string ≤ 256 B issued by the server, bound to `(boot_id, epoch, verb, args-hash)`.
**Selection**: `{"entities": [sel, ...] (1..64), "metrics": [metric-name, ...] (1..64)}`, `sel` is
exactly one of `{"inc": u64}`, `{"path": str ≤ 4096}`, `{"prefix": str ≤ 4096}`, `{"all": true}`.
A selection resolving to more than `limits.max_rows` entities is `budget`. Every object below is
**strict**: a missing required key, an unknown key or a wrong type is `bad-request`.

4.3 **Verbs by level** (✓ = served; JSON = REP only; BLK = REP + BLOCK* + END; JSON mode refuses
BLK verbs with `binary-required`, §8.4):

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

4.4 **hello** — args `{"client": str ≤ 64, "proto": {"major": 1, "minor": u16}}` (binary: MUST equal the
prefix, else `bad-request`). Result:
`{"proto": {"major": 1, "minor": N}, "level": "observe|operator|observer|control", "server": str,
"boot_id": "<uuid text>", "epoch": u64, "features": [str], "verbs": [str], "quality_codes":
[{"code": u8, "name": str}], "limits": {"max_frame_bytes": u32, "max_request_bytes": u32,
"request_deadline_ms": u32, "frame_completion_ms": u32, "max_outstanding": u32,
"max_subscriptions": u32, "client_queue_bytes": u32, "max_rows": u32, "max_page_blocks": u32,
"min_interval_ms": u32, "tick_ms": u32}}`. `features` ⊆ {`peak_reset`, `placement`, `damon`,
`enrich`}. A client MUST NOT assume a limit it was not given.

4.5 **status** — args `{}`. Result `{"server": str, "boot_id": str, "epoch": u64, "started_real_ns": i64,
"uptime_ms": u64, "tick": {"seq": u64, "last_mono_ns": u64, "scan_ms": u32}, "clients": {"<level>":
u32}, "queues": {"queued_bytes": u64, "dropped_blocks": u64}, "tiers": [{"name": str, "step_ms": u32,
"keep_s": u32, "bytes": u64, "cap_bytes": u64}]}`.

4.6 **current** — args `{"selection": Selection}`. REP result `{"stream": {"tick_ms": u32}}`, one live
BLOCK (dictionary-bearing, latest tick), END `reason: "complete"`.

4.7 **subscribe** — args `{"selection": Selection, "interval_ms": u32}` (multiple of `tick_ms`,
`min_interval_ms ≤ v ≤ 60000`). REP result `{"sub": <corr>, "interval_ms": u32}`; then one live BLOCK
per interval with this corr; the first BLOCK, and the first after any generation change or any drop
of a dictionary-bearing block, is dictionary-bearing (§6.2). More than `max_subscriptions` (4) live
per connection, or more than 8 distinct live selections per probe, is `budget`.
**unsubscribe** — args `{"sub": u32}`; REP on its own corr `{"ok": true, "result": {}}`, then END
`reason: "unsubscribed"` on the subscription's corr. Unknown `sub`: `not-found`.

4.8 **snapshot_at** — args `{"selection": Selection, "real_ns": i64, "tier": "auto|hot|warm|cold",
"provenance": bool}`. REP result `{"resolved": {"tier": str, "bucket_real_ns": i64, "step_ms": u32,
"covered_ms": u32}, "provenance": [object] | null}`; one live-shaped BLOCK (rows + columns,
`t_off_us = 0`, header `real_ns = bucket_real_ns`); END `complete`. Outside retention: `not-found`.

4.9 **series_batch** — args `{"selection": Selection, "from_real_ns": i64, "to_real_ns": i64,
"step_ms": u32 (0 = coarsest tier covering the range), "aggs": [agg-name] (1..4), "cursor": str|null,
"max_blocks": u32 (1..max_page_blocks)}`. REP result `{"tier": str, "step_ms": u32}`; then history
BLOCKs, each one subject (§3.8) and one aggregation; END `{"next": cursor|null, "reason":
"complete"|"page"}`. A client continues with the same args and `cursor = next`; a cursor from another
epoch or args is `cursor-invalid`.

4.10 **events / spans** — args `{"from_real_ns": i64, "to_real_ns": i64, "names": [name] (0..16, empty =
all), "ctx": {"run"|"lane"|"candidate"|"txn"|"session": str ≤ 128} (spans only, 0..5 keys), "limit":
u32 (1..1000), "cursor": str|null}`. Result `{"events"|"spans": [object], "next": cursor|null,
"displaced": u64}`; item keys are plan §C.4 (`kind, name, key, ctx, outcome, code, attrs, t_mono_ns,
real_ns, issuer, inferred`).

4.11 **procs** — args `{"subject": {"inc": u64}, "descendants": bool, "fields": [str], "limit": u32
(1..256), "cursor": str|null}`; `fields` ⊆ {`comm`, `ppid`, `state`, `cpu_usec`, `rss_bytes`,
`io_rbytes`, `io_wbytes`, `threads`, `cmdline`}; `cmdline` below operator level is `forbidden`. Result
`{"procs": [{"pid": u32, "start_ticks": u64, "inc": u64, <field>: value|null}], "coverage":
{"scanned": u32, "total": u32, "scan_age_ms": u32, "complete": bool}, "next": cursor|null}`.

4.12 **annotate** — args `{"kind": "begin|end|point", "name": name, "key": str ≤ 64 | null, "ctx": {...}
(as 4.10), "outcome": str|null (end only), "code": str ≤ 64 | null, "attrs": {str: str} (≤ 8 pairs,
≤ 256 B each)}`. Result `{"t_mono_ns": u64, "span": u64|null}`. Errors: `span-unknown`, `rate-limited`
(50/s per issuer), `budget` (64 open per issuer, 4,096 global).

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
| connections per peer uid per level | 8 | 4 | 64 | 32 | same |
| hello deadline, accept → hello complete (`hello_deadline_ms`) | 5 s | 5 s | 5 s | 5 s | §5.4 row `hello-timer` |
| request frame or line bytes (`max_request_bytes`) | 32 KiB | 32 KiB | 16 KiB | 1 MiB | `frame-too-large`, fatal |
| request deadline, first byte → complete (`request_deadline_ms`) | 5 s | 5 s | 5 s | 25 s | `deadline`, fatal |
| idle deadline (`idle_ms`) | 300 s | 300 s | 300 s | 300 s | `idle`, fatal |
| outstanding requests (`max_outstanding`) | 4 | 4 | 4 | 4 | `too-many-requests`, request refused, connection stays |
| live subscriptions per connection (`max_subscriptions`) | 4 | 4 | — | — | `budget` `{"pool":"subscriptions"}` |
| distinct live selections per peer uid / per probe | 4 / 8 | 4 / 8 | — | — | `budget` `{"pool":"uid-selections"}` / `{"pool":"selections"}` |
| queued BLOCK bytes per connection (`client_queue_bytes`) / global | 4 MiB / 32 MiB | same | — | — | §5.3 (live blocks dropped, query blocks wait) |
| queued REP/EVT/END bytes per connection (`control_queue_bytes`) | 2 MiB | 2 MiB | 2 MiB | 2 MiB | `slow-client`, fatal |
| writer stall: queue non-empty, no byte written (`stall_ms`) | 5 s | 5 s | 5 s | 5 s | `slow-client`, fatal |
| largest BLOCK the server builds (`max_block_bytes`) | 4 MiB | 4 MiB | — | — | never built (§4.9 splits) |
| largest frame either side accepts (`max_frame_bytes`) | 16 MiB | 16 MiB | 16 MiB | 16 MiB | client reader: `oversized-frame` |

`max_request_bytes` is derived from the worst legal request (§4.2, §4.12 give the arithmetic), so a
request that obeys every field bound always fits. An **outstanding request** is one whose final REP or
END is not yet enqueued; live subscriptions do not count. A **distinct selection** is the canonical
form of §4.2; a uid's selections are those its connections' live subscriptions use (a selection two
uids use counts for both). `max_block_bytes = client_queue_bytes`, so an empty queue can always take
the largest block (no query can wait forever on its own budget).

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

5.3 **Send hub** (`proto::stream::SendHub`, server side, one per serving process that owns every
connection's outgoing queue; P5b wraps it in one mutex). It owns the global budget, so the victim of a
global overflow is chosen with every queue in view.

Queue entries, in one FIFO per connection: `Control{bytes}` (REP/EVT/END, encoded by the caller),
`Live{stream, tick}` (a subscription tick, droppable), `Query{stream, bytes}` (a block of `current`,
`snapshot_at`, `series_batch` or `export`, never dropped). `stream` is the subscribing or requesting
`corr`. `tick` implements `TickForms`: `bare() -> Option<Arc<[u8]>>` (absent on the tick that changes the
generation) and `dict_form() -> Arc<[u8]>` (encoded at most once per tick, on first call). Per
connection: `block_bytes` (unsent bytes of Live and Query entries), `control_bytes`,
`last_progress_ns`, per stream `pending_drops: u32`, `needs_dict: bool`, `pinned_blocks: u32`; hub:
`global_block_bytes`.

**Pin.** `head(c)` returns the head entry as `[prefix, tail]`. The first `head` call for an entry
**pins** it: for a Live or Query block the hub copies the first `16 + 8·n_sec + 80` bytes (n_sec from
offset 12; §3.1 guarantees the header is the first body), writes `corr` @8 and `dropped =
pending_drops[stream]` (saturating) @`88 + 8·n_sec`, resets `pending_drops[stream] = 0` and increments
`pinned_blocks[stream]`. A pinned entry is never dropped or replaced: it is written completely or the
connection is closed. Stamping at pin, not at push, is what makes `dropped` exact: every drop happens
to an unpinned entry, so it is counted in the next pinned block of its stream or in that stream's END.

**Form.** A Live entry is pushed as `dict_form()` when `needs_dict[stream]` is set, when the stream has
no pinned block yet (first block), or when `bare()` is absent; otherwise as `bare()`. Pushing a
dictionary-bearing entry clears `needs_dict[stream]`; dropping or replacing a dictionary-bearing Live
entry sets it. The flag is sticky across an empty queue, so a later tick repairs it however long the
gap.

| Operation | Rule (in this order) |
|---|---|
| `push_control(c, f, now)` | `control_bytes + len > control_queue_bytes` ⇒ return `Close(slow-client)`; else append. Control bytes never count toward block budgets and are never dropped. |
| `push_live(c, s, tick, now)` | 1. **Coalesce**: if stream `s` has an unpinned Live entry, it is replaced in place by the new tick (`pending_drops[s] += 1`; if the old one was dictionary-bearing, `needs_dict[s] = true`); the form rule then picks the new entry's form. 2. **Per-connection fit**: while `block_bytes > client_queue_bytes`, drop the oldest unpinned Live entry of another stream of `c` (counted on its stream, sets its `needs_dict` if dictionary-bearing). 3. **Global fit**: while `global_block_bytes > 32 MiB`, drop the oldest unpinned Live entry of the connection with the largest `block_bytes` (ties: lowest connection id), excluding the entry just pushed. 4. If either budget is still exceeded, the new entry itself is dropped (counted, `needs_dict` set if it was dictionary-bearing) ⇒ `DroppedIncoming`; else `Queued`/`Coalesced`. |
| `push_query(c, s, block, now)` | Run steps 2-3 for a block of this length without inserting it; if both budgets then fit, append ⇒ `Queued`; else nothing is dropped beyond the Live entries already shed and the hub returns `Backpressure(block)`: the producer keeps the block and pauses that stream until `query_ready(c)` (§5.3 last row). Query blocks are never dropped. |
| `end_stream(c, s)` | Removes every unpinned Live entry of `s` (counted), returns `{blocks: pinned_blocks[s], dropped: pending_drops[s]}` for the END payload (§2.2) and forgets the stream; the caller then `push_control`s the END. |
| empty → non-empty (any push) | `last_progress_ns = now` |
| `wrote(c, n, now)` | `n > 0` ⇒ `last_progress_ns = now`; advance; a complete head is popped and its bytes leave both budgets. |
| `poll(now)` | for each connection with a non-empty queue and `now > last_progress_ns + stall_ms` ⇒ `Close(slow-client)`; returns the list. |
| `query_ready(c)` | true when an earlier `push_query` of `c` returned `Backpressure` and a pop or drop has freed bytes since; the producer retries. |
| `remove(c)` | drops everything of `c`, releases its bytes. |

Consequences, each a test (§11.4): a connection holds at most one unpinned Live entry per stream, so a
lagging subscriber sees the newest tick and a `dropped` count, never a stale backlog; a REP or END never
competes with blocks; a query never loses data, it waits; one slow group member cannot close another
(the global victim is whoever holds the most bytes, and only its Live entries are shed); a stalled
writer is closed after `stall_ms` from the last byte it accepted, never sooner after an idle period.

5.4 **Connection state machine** (server, P5b; `proto::stream::ConnTimers` holds the three deadlines
so the table is unit-testable without sockets):

| State | Event | Next / action |
|---|---|---|
| (accept) | level or peer-uid connection cap reached | `refuse`: close, no byte written, nothing read |
| (accept) | else | `Undecided{have: 0}`; `hello_deadline = now + hello_deadline_ms`; peer credentials read (§4.12) |
| `Undecided{have}` | byte `0x7B` at `have = 0` | `JsonNoHello` (§8) |
| `Undecided{have}` | byte equal to `HOB\x01`[have] (have < 4) or any byte (4 ≤ have < 8) | `have + 1`; at 8 ⇒ send the server prefix (§1.4) ⇒ `AwaitHello` (major mismatch ⇒ prefix, close) |
| `Undecided{have}` | any other byte | write the one `unknown-mode` line (§1.3), close |
| `Undecided`, `AwaitHello`, `JsonNoHello` | `now > hello_deadline` (`hello-timer`) | `Undecided`: close silently; `AwaitHello`: REP `corr=0` `deadline`; `JsonNoHello`: `deadline` line; then close |
| `AwaitHello` | complete REQ `hello`, `corr = 1`, valid | REP ⇒ `Ready` |
| `AwaitHello` | complete REQ other verb, or `hello` with `corr ≠ 1` | REP `corr=<its corr>` `hello-required` (corr ≠ 1: `bad-frame`, REP `corr=0`), close |
| `JsonNoHello` | first complete line | handled as a request ⇒ `Ready` (hello optional, §8.4) |
| `Ready` | complete request | dispatch; `max_outstanding` exceeded ⇒ REP `too-many-requests` |
| `Ready` | no outstanding request, no live subscription, reader at `Header{0}`, queue empty | arm `idle_deadline = now + idle_ms` (disarmed by any received byte or enqueued frame) |
| `Ready` | `now > idle_deadline` | `Closing(idle)` |
| `Ready` | reader `eof()` clean (`SHUT_WR`) | `Draining`: finish outstanding requests, `end_stream` every subscription with reason `client-eof`, close when the queue is empty |
| any | reader error, hub `Close(slow-client)`, server shutdown | `Closing(code)` |
| `Closing(code)` | — | `remove` the hub queue's unpinned entries; if no entry is partially written and the socket accepts it without blocking, write `EVT corr=0 {"event":"closing","reason":code}` (binary) or the error line (JSON); close. A client MUST NOT depend on receiving it. |

**Connection-fatal codes**: `unknown-mode`, `unsupported-version`, `hello-required`, `bad-frame`,
`frame-too-large`, `deadline`, `idle`, `slow-client`, `shutting-down`. Every other code answers one
request and keeps the connection. A refused connection (`refuse`) carries no code at all: the mode is
unknown before the first byte, so no reply could be framed.

## 6. Dictionaries, generations, epochs, reconnect

6.1 **Client side: a dictionary is held per stream** = `(connection, corr)`. A BLOCK without a
dictionary section is decoded against the stream's held dictionary and its rows/columns are checked
against it exactly as against an in-frame one (`HOB-46`).
6.2 **Server side: a generation counter belongs to the producer of the encoded bytes.** A live
selection stream (one per distinct selection, §4.2, shared by every subscriber, §5.3) owns one
dictionary and one counter: it starts at 1 when the selection stream is created and increases by one on
**any** change (append or retire); the tick that changes it carries the new generation complete (it
has no bare form). Every subscriber receives the same bytes, so the same `dict_gen`. A query stream
(private bytes) numbers its own from 1. Because a subscriber may join at generation 5, the client rule
is relative to what the stream holds, never absolute:
- the first BLOCK of every stream is dictionary-bearing (any generation ≥ 1);
- a later dictionary-bearing BLOCK's generation MUST exceed the held one (`generation-regressed`);
- a bare BLOCK's generation MUST equal the held one (`stale-dictionary-generation`); a bare BLOCK with
  nothing held is `stale-dictionary-generation` too.
The §5.3 form rule guarantees a conforming server never violates these: a dictionary-bearing entry is
pushed only for a first block, a generation change or after a dropped dictionary-bearing entry, and in
the last case the client never received that generation. At `u32::MAX` the selection stream ends every
subscription (`END ok:false`, `internal`).
6.3 Query replies carry their dictionary in their first BLOCK; a `series_batch` continuation (new
corr) is a new stream. Exported segments carry it in every record (plan §C.5).
6.4 **Epoch.** A probe process takes a new epoch at start (greater than every epoch it served under
the same `boot_id`, persisted beside the store) and keeps it until it exits; epoch and boot therefore
never change within a connection. On reconnect a client MUST drop every dictionary and live
subscription; if `hello.epoch` or `hello.boot_id` differs it MUST also drop cursors and open span keys
and report `session-lost`/`window-lost` for any session or window it held (plan §D.2); with the same
epoch its cursors stay valid.
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

7.3 **Receive path.** One reusable 12-byte header buffer; `recv_into` until 12 bytes; validate (§2.1,
§5.2 with client limits); allocate one `bytearray(len)`, copy the 12 header bytes in, `recv_into` the
rest through a `memoryview` slice until complete. No concatenation, no growing buffer, no per-frame
copy; the `bytearray` is owned by the decoded `Block` (reusing one body buffer would let the next read
overwrite views a caller still holds). Allocations per BLOCK frame: 1 `bytearray`, 1 root
`memoryview`, the `Block` object, a fixed number of header/directory tuples, and per *accessed*
section ≤ 3 views — independent of `n_rows`. `socket.settimeout` implements the client completion age.

7.4 **Decode.** All `struct` formats explicit `<`. One cached `struct.Struct('<' + 'HBBI'·n_sec)` per
`n_sec` reads the directory in one call; header body `<16sQQQQQqIIII` in one call; per column
`<IBBH` at body+0 in one call. On `sys.byteorder == 'little'` with `struct.calcsize` of `I`=4, `Q`=8:
values `mv[o+8:o+8+8n].cast('Q'|'q'|'d')`, rows `cast('I')`. Otherwise (big-endian) build
`array.array(typecode, bytes(slice))` and `.byteswap()` — one copy per accessed array, documented as the
only copying path. Quality is `mv[o+8+8n : o+8+9n]` (a `memoryview` of `B`). **No Python bytecode runs per
value during decode or validation**: quality validity is `bytes(q).translate(None, VALID_CODES)` must be
empty (C loop); row references use `max()` and a per-generation `bytes` kind map with `all(map(...))`;
a rows body byte-identical to the stream's previous validated one (C `==`) skips the reference check.
Dictionary decoding is a per-entry loop and runs once per generation; a dictionary body byte-identical
to a held one reuses it.

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

7.8 **Measurements P3 makes** (stdlib `timeit` + `tracemalloc`, SP-4 shapes: 8 columns, 100 and 1,000
cgroups, steady-state 8,272 / 80,240 B and dictionary-bearing 12,360 / 120,328 B frames from
`docs/SP-4-WIRE-SIZES.md`), published in `docs/SP-4-WIRE-SIZES.md` with the gate image named:

| Measurement | Acceptance (gated) | Reason |
|---|---|---|
| traced peak memory of `decode()` of a steady-state frame, minus the frame buffer | ≤ 16 KiB, and the 1,000-row figure ≤ the 100-row figure + 4 KiB | deterministic; proves no per-row objects |
| CPU ratio `t(1000)/t(100)`, steady state, eager validation, min of 7 × 200 interleaved | ≤ 3.0 | load cancels in a ratio; a per-value Python loop gives ≈ 10 |
| CPU absolute, steady state and dictionary-bearing | published, not gated (estate: no load-dependent verdict) | trend evidence; target ≤ 250 µs at 1,000 cgroups steady state |
| `measured_list()` of one 1,000-row column | published | materialisation is O(n) by definition |

## 8. JSON mode

8.1 **Framing.** Each request is one UTF-8 JSON object starting with `{` and ending with `\n` (0x0A);
the line, newline included, is bounded by `max_request_bytes` and the request deadline of the level
(`frame-too-large`/`deadline`, close). Empty lines are `bad-request`. Each reply is one line.
8.2 **Envelope.** Request `{"corr": u32 ≠ 0, "verb": str, "args": object}`; reply = §4.1 / §2.2 REP
objects plus `"corr"`. Server-originated lines use `corr: 0`.
8.3 **Integers.** All integers are exact JSON integers (never strings, never exponent or fraction form);
u64 up to 2^64−1 MUST round-trip; a fraction where an integer is required is `bad-request`. Floats
appear only where a schema names them; non-finite values are never sent (`null` + quality instead).
JavaScript consumers are unsupported (plan K-JSON).
8.4 **hello is optional** in JSON mode (without it the connection runs at minor 0). Block verbs are
refused with `binary-required`: JSON clients get telemetry through `hostobsctl --json`, which speaks
binary and prints JSON.
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
9.4 Minor 1 = kind 6 `subject` and the §3.6 time length fix. `PROTO_MINOR` becomes 1 with `HOB-48`.

## 10. Wire error codes (closed list, minor 0 + 1)

`unknown-mode`, `unsupported-version`, `hello-required`, `bad-frame`, `frame-too-large`, `deadline`,
`slow-client`, `shutting-down`, `busy`, `bad-request`, `unknown-verb`, `forbidden`, `binary-required`,
`too-many-requests`, `budget`, `rate-limited`, `not-found`, `cursor-invalid`, `span-unknown`,
`unsupported-feature`, `session-lost`, `window-lost`, `internal`. `detail` keys: `budget` →
`{"pool": str}`; `rate-limited` → `{"retry_after_ms": u32}`; `bad-request` → `{"key": str}`;
`unsupported-feature` → `{"feature": str}`.

## 11. Robustness and conformance

11.1 **Decoder rules for untrusted bytes** (both languages): check every length against the real
buffer before use; compute with checked arithmetic; allocate from a declared size only after the bytes
are proven present (directory capacity from `n_sec` included, `HOB-50`); no recursion; JSON payloads
are parsed only after the frame is complete and bounded; JSON nesting depth ≤ 8 for requests.

11.2 **Existing cases** (`fixtures/wire/*.{bin,json}`; Rust `crates/proto/tests/golden_fixtures.rs`,
Python `python/reference/hostobs_wire_ref.py`; both must reproduce `.json` or `expect_error`):
`live-block`, `history-buckets`, `empty-selection`, `all-quality-codes`, `u64-boundaries`,
`unicode-dictionary`, `unknown-optional-section` (decode ok); `bad-metric-mask`, `bad-quality-code`,
`block-as-req`, `column-kind-mismatch`, `column-references-entity`, `column-reserved`,
`dangling-dictionary-ref`, `duplicate-dictionary-entry`, `empty-rows-and-time`,
`future-section-version`, `generation-mismatch`, `no-rows-or-time`, `non-zero-reserved`,
`oversized-frame`, `required-section-refused`, `row-references-metric`, `section-order`,
`truncated-directory`, `truncated-frame` (named error). The P3 client must pass all of them too.

11.3 **New block cases** (P2 adds bytes from the Python reference encoder, Rust and Python agree):

| Case | Purpose | Expected |
|---|---|---|
| `history-four-buckets` | §3.6 length `align8(15·4) = 64` | ok |
| `history-subject` | kind 6 with time | ok |
| `subject-in-live-block` | kind 6 with rows | `subject-without-time` |
| `history-agg-not-permitted` | `mean` on a counter column | `aggregation-not-permitted` |
| `bare-block-held-dict` | no dictionary section, decoded with a held generation | ok |
| `bare-block-dangling` | bare block, row index past the held dictionary | `dangling-dictionary-ref` |
| `rep-sectioned-flag` | REP frame with `SECTIONED` | `bad-frame` (`unknown-frame-flags` locally) |

11.4 **Stream cases** (`fixtures/stream/<case>.json`: `{"case", "doc", "side": "client|server",
"max_frame", "completion_ms", "steps": [{"t_ms", "feed_hex"} | {"t_ms", "poll": true} | {"t_ms", "eof":
true}], "expect": [{"frame": {"type", "corr", "len"}} | {"error": kind} | {"closed": "clean"}]}`;
Rust and Python both replay them):

| Case | Expected |
|---|---|
| `dribbled-bytes` (one byte per step, within the age) | one frame |
| `split-every-offset` (frame cut at each offset 1..len−1) | one frame each |
| `two-frames-one-read` | two frames from one feed |
| `oversized-len` (`max_frame + 1`) | `oversized-frame`, poisoned, no allocation |
| `len-below-header` (`len = 11`) | `truncated` (header), poisoned |
| `completion-age` (half a frame, poll at +5001 ms) | `deadline` |
| `truncated-stream` (EOF mid-body) | `truncated` |
| `clean-eof` | `closed` |
| `corr-zero-req` (server side) | `bad-frame` |
| `epoch-change` (client: reconnect hello with a new epoch) | dictionaries, cursors and subscriptions dropped |
| `generation-rollover` (dict-bearing gen 2 after gen 1, then bare gen 1) | ok, ok, `stale-dictionary-generation` |
| `generation-regressed` (dict-bearing gen 1 after gen 2) | `generation-regressed` |
| `required-section-rejection` (reply BLOCK with unknown required kind) | caller error `unsupported-section`, connection closed |

Queue cases (`fixtures/stream/queue-*.json`, Rust only, fake clock): `queue-drop-oldest` (three blocks
over budget: oldest dropped, next head stamped `dropped = 1`), `queue-drop-dict-repair` (dropped
dict-bearing block, next bare block replaced), `queue-control-over-budget` (`slow-client`),
`queue-partial-never-dropped`, `queue-stall` (`slow-client` at +5001 ms), `queue-shared-block` (two
clients, one `Arc`, distinct `corr`/`dropped` prefixes).

## 12. Work breakdown

**P2 — `proto` stream layer and codec additions (L).** Modules `crates/proto/src/stream.rs` (`FrameReader`,
`SendQueue`), `crates/proto/src/envelope.rs` (REQ/REP/END/EVT JSON shape checks without a JSON
dependency: byte-level only — `proto` stays dependency-free; JSON parsing is the caller's),
`frame.rs` additions. Public API:
`FrameReader::new(max_frame: u32, completion_ns: u64) -> FrameReader`;
`FrameReader::feed(&mut self, bytes: &[u8], now_ns: u64) -> Result<Vec<RawFrame>, ProtoError>`;
`FrameReader::poll(&mut self, now_ns: u64) -> Result<(), ProtoError>`;
`FrameReader::eof(&mut self) -> Result<(), ProtoError>`;
`RawFrame { header: FrameHeader, bytes: Vec<u8> }`;
`SendQueue::new(per_client: usize, completion_ns: u64)`; `push_control(Arc<[u8]>)`,
`push_block(SharedBlock)`, `head(&mut self) -> Option<[&[u8]; 2]>`, `wrote(usize, now_ns)`,
`poll(now_ns) -> QueueState`; `SharedBlock { bytes: Arc<[u8]>, stream: u32, dict_bearing: bool,
dict_form: Option<Arc<[u8]>> }`;
`decode_block_with(bytes: &[u8], held: Option<&Dictionary>) -> Result<Block, ProtoError>`;
`SectionKind::Subject = 6`, `Block::subject: Option<u32>`. Fixes `HOB-8`, `HOB-46`, `HOB-47`, `HOB-48`,
`HOB-50`. Acceptance: every §11.3/§11.4 case in Rust; fake-clock unit tests for every row of §5.2 and
§5.3; Python reference encoder/decoder updated for kind 6 and the length fix.

**P3 — `python/hostobs_client` (L).** Stdlib only, zipimport-safe (no `__file__` reads, data via
`importlib.resources` only if needed). Modules `wire.py` (`decode_block(buf: bytearray, held:
Dictionary|None) -> Block`, `Dictionary`, `Column`, `WireError(kind, detail)`), `stream.py`
(`FrameReader` mirroring §5.2), `conn.py` (`Connection.open(path: str, level: str, *, client: str,
timeout_s: float) -> Connection`, `.request(verb: str, args: dict) -> dict`, `.blocks(verb, args) ->
Iterator[Block]`, `.subscribe(selection, interval_ms) -> Subscription`, `.close()`), `rates.py`
(`rate(prev, cur, nominal_interval_ns) -> dict`), `profiling.py` (`require_profiling(level: str,
required: bool) -> ProfilingState` with the five states of plan §D.2), `mark.py` (`hostobs-mark`).
Acceptance: all `fixtures/wire` and `fixtures/stream` client cases, `fixtures/rates/rates.json`, §7.8
gated thresholds, a zipimport smoke test, no import outside the stdlib (checked by a test).

**P5a — Rust `client` crate (M).** `Client::connect(path, Level, ClientInfo) -> Result<Client, ClientError>`,
`hello() -> &HelloResult`, `request(verb, serde-free JSON text) -> Reply`, `subscribe(Selection,
interval_ms) -> Subscription` (iterator of `Block`, held dictionary per stream), reconnect per §6.4.
**P5b — probe serving (L).** Accept loop per level from `LISTEN_FDS`, mode detection, handshake, verb
dispatch with strict arg checking, shared selection streams (one encoding per tick, dict-bearing form
on demand), `SendQueue` per client, the §5.1 table as config keys. Acceptance: an in-process
socketpair conformance test per row of §5.1 and per verb of §4.3 marked M2, against a fake clock.

## 13. Decisions taken, divergences, open questions

### Decisions taken

| ID | Decision | Reason |
|---|---|---|
| D-W1 | Server answers the binary prefix with its own 8-byte prefix carrying the negotiated minor; on a major mismatch it sends its prefix and closes | the prefix is the only structure that can survive a major change, so refusal must live there |
| D-W2 | `hello` is mandatory first in binary mode, optional in JSON mode | binary clients are libraries that need limits; shell one-liners (`hostobs-mark`) must stay one line |
| D-W3 | REQ payload `{"verb","args"}` with exactly those keys; JSON mode adds `corr` | one strict envelope; `corr` lives in the frame header in binary |
| D-W4 | Block verbs answer REP (stream header) + BLOCK* + END; JSON verbs one REP | a caller learns acceptance before data and the stream end is explicit, incl. mid-stream failure |
| D-W5 | Dictionary scope = one stream; any change (also an append) = a new complete generation inside the next BLOCK | no delta machinery; at 1,000 cgroups a dictionary-bearing tick is 1.5× a bare one (SP-4), bounded |
| D-W6 | `DICT` frame stays reserved and unsent in 1.0 | the in-block dictionary already serves live, query and segment paths (`HOB-6` resolved as "reserved") |
| D-W7 | Shared encoded blocks; per-client `corr` and `dropped` patched into a private prefix copy at head time | the header is always first, so both fields sit at fixed offsets; one encode per selection |
| D-W8 | `dropped` stamped when the frame becomes the head of the write, counting this stream's drops | drop-oldest can drop blocks queued before a block that was already enqueued |
| D-W9 | History blocks name their subject with new required section kind 6 (minor 1) | the implemented time block has no entity reference, so a multi-subject `series_batch` cannot be attributed |
| D-W10 | Gauge min/max/mean = separate history blocks, one aggregation each; agg must be permitted by every column's mask | no layout change for `HOB-5`; the mask already encodes what is permitted |
| D-W11 | `procs`, `events`, `spans` replies are JSON pages, not BLOCKs | per-process strings and the `(pid, start_ticks)` identity do not fit the u64 entity model; pages are ≤ 256/1,000 items |
| D-W12 | Error codes are kebab-case (`bad-request`, not `bad_request`) | every other code in plan and codec is kebab-case |
| D-W13 | JSON mode refuses block verbs (`binary-required`) | a second JSON rendering of blocks would be a second codec to keep conformant |
| D-W14 | Python reads each frame into its own exact-size `bytearray` (not one reusable body buffer) | decoded views stay valid as long as the caller holds the block; one allocation per frame is cheap at ≤ 120 KB/s |
| D-W15 | Python CPU gate is a scaling ratio (≤ 3.0) plus a deterministic `tracemalloc` bound; absolute times published only | estate rule: verdicts independent of host load; the ratio still catches a per-value loop |
| D-W16 | Max 4 subscriptions per connection; client-side reader limits 16 MiB / 30 s | bounds a client's share of the 8 distinct selections; client stall bound is generous because the probe is trusted |
| D-W17 | Section layouts are frozen at version 1; changes take new kind codes | the implemented decoder refuses a known kind with another version even when optional |

### Divergences (code or plan vs this document)

| ID | Where | Code / plan says | WIRE.md says | Work |
|---|---|---|---|---|
| V-1 | `frame.rs` `time_body_len` (line 741), `hostobs_wire_ref.py` `time_body_len` (line 380) | `align8(14·n)` | `align8(15·n)`; today only n ≤ 3 buckets round-trip (n = 4: encoder writes 64 B, decoder demands 56) | `HOB-48` |
| V-2 | `frame.rs` `decode_block` | references checked only against an in-frame dictionary | also against the held generation | `HOB-46` |
| V-3 | `frame.rs` `decode_block` | bucket `agg` not checked against the metric mask | `aggregation-not-permitted` | `HOB-47` |
| V-4 | plan §B.2 `TIME` | no subject; `series_batch` returns several entities | kind 6 `subject` | `HOB-48` |
| V-5 | `FrameHeader::parse` | no `len` bound; `SECTIONED` allowed on any type; directory capacity from `n_sec` before bytes are proven | §2.1, §11.1 | `HOB-8`, `HOB-50` |
| V-6 | plan §B.1 | `DICT` = dictionary delta or full generation | reserved, unsent | none (D-W6) |
| V-7 | plan §B.1 | procs page is a BLOCK | JSON page | none (D-W11) |
| V-8 | plan §B.3 | `bad_request` | `bad-request` | none (D-W12) |
| V-9 | plan §B.3 dictionary text | ≤ 256 B UTF-8 | also no bytes < 0x20 or 0x7F (as `dict.rs` `validate`) | none |
| V-10 | `hostobs_wire_ref.py` line 5 | cites `hostobs/docs/WIRE-COLUMN-CODEC.md` (does not exist) | this file | `HOB-49` |

### Open questions (recommendation first)

1. **Client-side completion age**: 30 s (recommended: a trusted probe under load must not be cut by a
   client) vs the server's 5 s.
2. **Who may read spans**: all `hostobs-observe` members (recommended, plan §I question 4) — WIRE.md
   serves `events`/`spans` at observe level on that assumption.
