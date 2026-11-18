# hostobs probe serving (P5b): design, resolutions and package split

Status: design reviewed 2026-11-18 by an independent Opus reviewer (verdict GO-WITH-CHANGES; its
findings are folded in below as rules R1..R28). Author: session vbpub-cb. Normative protocol:
`WIRE.md` (sections quoted as §n). This file says HOW the probe's serving side is built, cut into
packages, and how it RESOLVES the places where WIRE.md is silent or contradicts itself. A rule here
that needs a WIRE.md edit lists that edit in §6; the package that implements the rule makes the edit.

## 1. Shape: a sans-IO core, injected backend, thin drivers

`bins/probe/src/` library modules (no new crate; `main.rs` stays a thin entry until P5b-4; the
probe serves levels observe, operator and observer; `control` belongs to `act`: `Server::accept(Control)`
refuses uncounted. A serving core inside `bins/probe` is not reusable by `bins/act`: backlog item for
a later move into a crate).

- **`Conn`**: one per accepted connection, a pure state machine. No sockets, threads or clock reads,
  no allocation by an untrusted length.
- **`Server`**: owns ONE `SendHub` (§5.3), the conns keyed by `ConnId`, the per-level and per-uid
  counters, and the backend (`B: Backend`, methods `&self` in P5b-1). A single-threaded `poll(2)`
  driver needs no mutex (WIRE §5.3's "one mutex" is not needed; recorded as a deviation).
- **`Backend`** (trait): P5b-1 needs only `identity() -> Identity {server, boot_id, epoch, features}`
  and `status() -> StatusInfo`. `Server` fills `clients` per level and `queues.queued_bytes` (from
  `hub.global_pools()`); `queues.dropped_blocks` is 0 in P5b-1 (a hub counter arrives in P5b-2).
  `peer_identity`/issuer is NOT in P5b-1 (§4.12 is P5b-3). Test backends in `#[cfg(test)]`.
- **API** (all deterministic under a fake clock): `Server::accept(level, peer: Option<Peer>, now) ->
  Accepted(ConnId) | Refused` (`Peer{uid,pid}` captured ONCE and stored in the `Conn`; every counter
  decrement and hub `add` uses the stored uid, never a re-read; `None` = refuse uncounted),
  `on_bytes(id, bytes, now)`, `on_eof(id, now)`, `poll(now)`, `output(id, now) -> Option<[&[u8]; 2]>`
  (the hub's `head`, which PINS: never call it to test emptiness; use `hub.queued(c) == Pools::default()`),
  `wrote(id, n, now)`, `take_final(id) -> Option<Vec<u8>>`, `is_closed(id)`, `finish(id)`, `shutdown(now)`.
- **`finish(id)` is the single close path** (R22): it decrements the level and uid counters, deletes a
  uid entry at 0, calls `hub.remove`, drops the conn; idempotent; used by EVERY close (silent,
  unknown-mode, major mismatch, Closing, Draining done, driver I/O error, shutdown). `refuse` never
  increments. Accept when `count < cap`. Test: after each close path counters, `uid_pools` and
  `global_pools` are back to zero.
- **Levels** are a closed enum; the verb table of §4.3 is `const` data, one row per verb with its
  level mask, shape (JSON/BLK), `served` flag (P5b-1: hello, status) and reserved flag (§4.13).

## 2. Package cut (R-cut)

Blind-compilation risk is minimised by splitting the original P5b-1:

- **P5b-1a, pure functions, no connection state** (about two review rounds; its API is the fixed input
  of 1b): `Limits` (the §5.1 defaults per level, doc comments carry the `hostobsd.toml` key names; no
  TOML parsing), `Level`, the verb table, the **args reader** (R4), `hello`/`status` argument schemas,
  the **precedence function** `classify(...) -> Dispatch | Refusal{code, detail}` (R11), the encoders
  (`control_frame(type, corr, payload)`, the §4.1 error envelope, the `closing` EVT, the JSON line;
  message/`detail` rules R14, reply cap R25), and the JSON **`LineReader`** (R13) as a §5.2-style
  table with row tests. Each piece gets deterministic hostile-run tests (the style of the client's
  2,000 + 2,000 runs: closed set of outcomes, no panic, bounded buffers).
- **P5b-1b, the state machine**: `Conn`/`Server`, every §5.4 row, the final-frame `Closing` (R1, R2),
  counters (R22), the timer order (R20), `hello`/`status` end to end, binary and JSON mode, and the
  reachable §11.4 connection cases (R21). About two to three rounds.
- Later: P5b-2 (live streams), P5b-3 (history verbs, issuer), P5b-4 (drivers, acceptance; Opus review
  of the final series), see WIRE.md §12 P5b and the controller's approval of 2026-11-18.

## 3. Resolutions (the source of truth where WIRE.md is silent or inconsistent)

**R1 Final frame, not a hub frame (WIRE §5.2 vs §5.4 `Closing`).** A connection-fatal outcome yields
exactly ONE final frame held OUTSIDE the hub: in binary mode the REP the table names when the outcome
answers a request or a reader error (`hello-required`, `bad-frame`, `frame-too-large`, `deadline`), the
`EVT closing {event,reason}` for `idle`, `slow-client`, `shutting-down` and `unsupported-version` /
`unknown-mode` as specified; in JSON mode the error line. Algorithm at `Closing(code)`: `drain_unpinned(c)`;
if `head_partial(c)` is false then `final = Some(bytes)` else `None`; then the conn is closed (`is_closed`)
and the driver takes `take_final` and makes ONE non-blocking write, abandoning a short write; `finish` removes it.
**R2 Prefix guarantee.** The conn counts the bytes reported through `wrote`. When it is 0 at `Closing`
and the server prefix was queued (the `conn-prefix-surplus` shape: prefix + a bad REQ in one read), the
final bytes are the 8 prefix bytes followed by the final frame; 1..7 written means `head_partial` is
already true and nothing more is written. A major-version mismatch: the final bytes are the prefix alone.
**R3 Unreachable in P5b-1:** `too-many-requests` and "corr already in use" need a request that stays
outstanding; `hello`/`status` are synchronous, so outstanding is 0 whenever the next request arrives.
Both arrive in P5b-2 with the first block verb; their position in the precedence list (R11) is pinned now.
**R4 Args reader.** Do NOT use serde for requests (it accepts duplicate keys in map-typed objects,
recursion limit 128 not 8, and `detail.key` would be scraped from error text), although `bins/probe/Cargo.toml`
depends on `serde_json`. Build on `proto::envelope`: `check_req` already enforces UTF-8, grammar, depth <= 8,
no BOM and no trailing bytes; the args layer adds `members_up_to(slice, 16)` per object (duplicates per
level; cap 16 members per object and 64 elements per array: the largest request object has 7), the strict
schema, string unescaping that REFUSES surrogates (lone or paired-wrongly: `bad-request`; a valid pair is
decoded), decoded-length bounds and exact integers. `client::json::unescape` must NOT be lifted: it maps a
lone surrogate to U+FFFD, which §4.2 forbids server-side.
**R5 Backend sources.** `hello` needs `server`, `boot_id`, `epoch`, `features` from `Backend::identity()`;
see §1.
**R6 Invalid hello in `AwaitHello`.** Bad args: REP `corr=1` `bad-request`, state stays `AwaitHello`
(non-fatal), hello timer keeps running. A REQ failing `check_req` in `AwaitHello`: `bad-request` on its own
`corr`, stays.
**R7 `hello.proto`** must equal the CLIENT's own prefix (major 1, minor C), not the negotiated N. The
result carries N = min(C, server minor). (The Rust client sends `PROTO_MINOR` in `hello` and checks
`result.proto.minor == N`.)
**R8 JSON-mode `hello`:** negotiation N = min(C, S_minor); a major other than 1 is `unsupported-version`
(fatal; the only wire place the code is reachable); `hello` is accepted only as the first line; a second
`hello` in `Ready`, either mode, is `bad-request` with `detail.key` `"verb"`.
**R9 `AwaitHello` row pinned:** `corr == 0` -> `bad-frame` `corr=0` (§2.3 first); verb other than `hello`
with any `corr != 0` -> `hello-required` on that `corr`; `hello` with `corr != 1` -> `bad-frame` `corr=0`.
**R10 EOF before `Ready`:** `Undecided`, `JsonNoHello` without a line, `AwaitHello`: silent close (no
request owed); EOF mid-line in JSON mode: close without reply (mirrors §5.2 `truncated`); EOF in `Ready`:
`Draining`; bytes after EOF: `trailing-bytes`, close without reply.
**R11 Request check precedence (§4.2/§4.3/§10):** (1) frame type != REQ: `bad-frame`, fatal; (2) `corr == 0`
(binary), or (P5b-2) `corr` in use: `bad-frame`, fatal; (3) `check_req`: `bad-request`; (4) `AwaitHello` gate;
(5) verb not in the table, reserved (§4.13), or known-but-not-served in this package: `unknown-verb`;
(6) level: `forbidden`; (7) JSON mode and a block verb: `binary-required`; (8) (P5b-2) `max_outstanding`:
`too-many-requests`; (9) args schema: `bad-request`; (10) (P5b-3) verb-level feature `unsupported-feature`;
(11) execute. So duplicate keys inside an UNKNOWN verb's args are never inspected (state it in §4.2).
Known M2 verbs not served yet answer `unknown-verb` and are absent from `hello.verbs`; `forbidden` and
`binary-required` still come from the full table and stay testable. Never `internal` for them.
**R12 JSON-mode `corr`:** echoed only if `members()` succeeds and `corr` is one exact u32 != 0, else 0;
a JSON-mode `corr` of 0 is `bad-request` `{"key":"corr"}`, non-fatal; binary `corr` 0 is `bad-frame`, fatal.
**R13 JSON `LineReader`:** a line including its `\n` is at most `max_request_bytes`; at that many bytes
buffered without a `\n` the reader refuses with `frame-too-large` (fatal); `\r\n` reaches `check_req` with a
trailing `\r` = `bad-request` (non-fatal); the request-deadline age is stamped at each line's first byte;
linear time: keep a scanned offset (no rescan per feed), slice complete lines straight out of the fed
`bytes`, buffer only the incomplete tail, compact at most once per feed; capacity <= min(`max_request_bytes`,
received + step); an empty line is `bad-request`.
**R14 Bounded echo.** `message` is a `&'static str`, never client text. `detail.key` is built from schema
names; an unknown or duplicate key is included only if it is <= 64 bytes of `[A-Za-z0-9_.-]` with no
backslash, otherwise the path stops at the parent. `EnvelopeError` -> `detail.key`: `Json` -> `""`;
`MissingKey`/`WrongType(n)` -> `n`; `DuplicateKey`/`UnknownKey` -> the bounded rule.
**R15 Escaped keys.** Duplicate detection compares raw bytes; to stay exact, any key containing a backslash
is refused as unknown before it is compared (already the envelope rule; make it the args rule).
**R16 Integers (§8.3).** Digits only: a leading `-` on an unsigned field is `bad-request`, `-0` is refused
for signed fields too, `1.0`, `1e2`, a value beyond the field's own type (`proto.minor` is u16: 65536 is
refused) are `bad-request`; checked arithmetic, stop after 20 digits.
**R17 `hello.client`:** 0..64 decoded bytes, no control characters (< 0x20, 0x7F), lone surrogates refused
(matches the Rust client's own rule).
**R18 Block keys of `hello.limits`** are 0 at levels that serve no block verb: `max_subscriptions`,
`client_queue_bytes`, `query_queue_bytes`, `max_block_bytes`, `max_rows`, `max_block_buckets`,
`max_page_blocks`, `min_interval_ms`, `tick_ms`; `hello.verbs` = the verbs served at the level now.
**R19 Quadratic CPU:** duplicate detection is O(k^2) per object, hence the caps in R4; line reading R13;
no `Vec::drain` per line.
**R20 Timer order**, one deterministic order per conn per `poll`: (1) `ConnTimers` Hello, (2)
`FrameReader::poll` / line-reader age (`deadline`), (3) `ConnTimers` Idle, (4) hub `poll` `slow-client`;
the first fatal outcome wins and the rest are ignored; conns are processed in `ConnId` order and closed
conns are `finish`ed (`hub.poll` keeps reporting until removal). Hello and Idle are exclusive (Idle arms
only in `Ready`), Idle and stall are exclusive (Idle needs an empty queue); the only tie is Hello vs stall
on the unread prefix, settled by the order. The idle condition is recomputed at the end of every `Server`
entry point (`accept`, `on_bytes`, `wrote`, `poll`). Within `on_bytes`: `on_byte`, feed, dispatch each
frame (`on_enqueue` per push), stop at the first fatal outcome, then apply `Feed.error`; frames left in a
`Feed` after a fatal outcome are DISCARDED, not dispatched. `Feed`'s frame vector is bounded only by the
driver's read size: P5b-4 fixes one read size (<= 64 KiB).
**R21 §11.4 cases reachable in P5b-1:** all connection cases except `conn-reply-page-truncated` (uses
`events`, M3: retarget to `procs` in P5b-3), `conn-not-idle-with-subscription`, `conn-dribbling-reader`
and the END half of `conn-half-close` (P5b-2). "In-process socketpair" becomes the sans-IO core under
test; the socketpair runs move to P5b-4 (deviation note in §11.4).
**R23 Peer:** `client` is a free label, never identity; a request key naming a uid, pid or cgroup is
`bad-request` through the strict schemas (test it); `pid` is unused in P5b-1.
**R24 Mode detection** uses `stream::ModeDetect`, NOT `frame::detect_mode` (one-shot, reports a partial
magic as `truncated`).
**R25 Reply cap:** one `control_frame(type, corr, payload) -> Vec<u8>` helper (`encode_frame` needs
sections); a REP longer than `max_reply_bytes` is replaced by an `internal` error (bounds a misbehaving
backend, e.g. more than 8 tiers).
**R26 Gates:** each new src file is an `assay.toml` `judge.targets` entry as well as under `source_roots`
(`bins/probe/src` joins `source_roots`); `coverage-floors.toml` `[crates."bins/probe"]` is the bootstrap
90/85 and becomes a measured behavioural floor; `lib.rs`/`main.rs` are judged only if they carry executable
lines (module declarations only: not judged).
**R27 Hub:** the conn methods take `&mut SendHub` and `&dyn Backend` as disjoint field borrows of `Server`.
**R28 Control queue memory** (`control_queue_bytes` 2 MiB with no per-uid/global cap: 64 observer conns
from one uid hold 128 MiB, 128 conns of the level 256 MiB): DECISION PENDING with the controller before
`Limits` defaults freeze; candidates: observer `control_queue_bytes` ~ 320 KiB (`max_reply_bytes` ~ 16 KiB
by the §5.1 formula) or a per-uid/global control pool. Until decided, `Limits` carries the WIRE defaults
and the choice is a one-line change.

## 4. P5b-1a test list (minimum)

Args reader / schemas: escaped key spellings and duplicates of them; surrogate pair valid, high + non-low,
low first, high at end; `\u0000` and controls in `client`; decoded vs escaped length at 64/65; 16/17
members, 64/65 elements; duplicate at depth 2 (`{"proto":{...},"proto":...}`); `true`/`null`/string where
an integer is expected; `-0`, `1.0`, `1e2`, 2^64, i64 edges, u16 65535/65536; a 30 KB key with `detail`
<= 2 KiB; keys naming uid/pid/cgroup; depth 8/9. LineReader: CRLF, empty line, a line of exactly
`max_request_bytes` and one byte more, 10k lines in one feed and 1-byte dribbles (linear-time asserts via
operation counters or capacity bounds, not wall time), EOF mid-line. Precedence: one test per step of R11
and per tie between steps. Encoders: every §10 code reachable, `max_reply_bytes` fallback, `detail` bounds.
Hostile runs: 2,000 + 2,000 deterministic runs with a closed outcome set and no panic.

## 5. P5b-1b test list (minimum)

`conn_row_<n>_*` one per §5.4 row; the reachable §11.4 cases; one per §5.1 connection-level row
(caps per level/uid, hello deadline, request size, request deadline, idle, control queue, stall, frame
completion, max_reply_bytes); R1/R2 (prefix + bad REQ in one read; final frame held back when a frame is
half written; major mismatch prefix alone); R6..R10 rows; counters back to zero after every close path;
timer order ties (R20); byte-by-byte delivery of prefix, hello, status in both modes; frames after a fatal
outcome discarded; no unbounded buffer growth (`buffered_capacity` bounds) under hostile bytes.

## 6. WIRE.md edits that belong to these packages

§4.2 request precedence (R11) and the digits-only integer rule (R16); §4.4 `hello.proto` = client prefix
(R7) and JSON-mode negotiation (R8), the block-key list (R18); §5.2/§5.4 final-frame rule and the new rows
(R1, R2, R6, R9, R10) with a decision D-W28; §8.1 line rules (R13), JSON `corr` recovery (R12); §11.4
`events` -> `procs` retarget and socketpair -> P5b-4 (R21); §5.3 "no mutex" note (R27); §5.1 control-queue
memory per R28 once decided.
