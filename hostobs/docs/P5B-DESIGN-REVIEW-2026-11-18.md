# P5b-1 design review (independent Opus reviewer, 2026-11-18)

Verdict: **GO-WITH-CHANGES**. Read-only review of the first draft of `P5B-SERVER-DESIGN.md` against
`WIRE.md`, `crates/proto/src/{stream,envelope,frame,hub}.rs`, `crates/client/src/{json,hello}.rs`,
`bins/probe/Cargo.toml`, `assay.toml`, `coverage-floors.toml`, `tools/check-lints.py`. Nothing was
compiled. The findings are numbered as in the review; `P5B-SERVER-DESIGN.md` §3 carries the resolutions
as rules R1..R28 (the finding number and the rule number are the same, except where the table says so).
Recorded verbatim in substance, condensed in wording.

## Blockers
1. **Fatal REP vs `Closing` (WIRE §5.2 mapping, §5.4 rows `AwaitHello`/`any`/`Closing`, §1.4.3).** §5.2
   says a reader error produces a REP `corr=0` `frame-too-large`/`bad-frame`/`deadline`; `AwaitHello`
   produces a REP `hello-required`. The `Closing(code)` row drains the hub (`drain_unpinned`), writes the
   `closing` EVT directly and removes the conn: a fatal REP pushed through the hub is still unpinned and is
   deleted, so the client would see `EVT closing`, never the REP. The first draft's "queue it as the last
   frame" also departs from WIRE (a pinned head with no bytes written is discarded). Resolution (R1): one
   final frame held outside the hub; REP for outcomes that answer a request or a reader error, `EVT closing`
   for `idle`/`slow-client`/`shutting-down`, the error line in JSON mode; `take_final()` + `is_closed()`.
2. **The server prefix can be dropped by `Closing` (§1.4, §5.4).** Prefix + `status` REQ in one read:
   the prefix is queued, `status` gives `hello-required`, `Closing` drains the unwritten prefix, and the
   client then reads the REP header as the prefix (`unknown-mode`). Resolution (R2): count bytes through
   `wrote`; at 0, the final bytes are prefix + final frame; 1..7 means `head_partial` is already true; a
   major mismatch sends the prefix alone.
3. **`too-many-requests` and "corr in use" are unreachable in P5b-1 (§5.1, §2.3).** `hello`/`status` are
   synchronous, outstanding is always 0. They arrive in P5b-2 with the first block verb (R3).
4. **`bins/probe` already depends on `serde_json`, `serde`, `toml`** while the draft said "no JSON
   dependency". Do not use serde for requests: it accepts duplicate keys in map-typed objects, its
   recursion limit is 128 not 8, `detail.key` would be scraped from error text. Build on
   `proto::envelope` (`check_req`, `members_up_to`, caps, strict schema, surrogate-refusing unescape,
   exact integers). `client::json::unescape` must not be lifted: it maps a lone surrogate to U+FFFD (R4).
5. **`hello`/`status` sources missing from `Backend`.** Define `Backend::identity()`; `Server` fills
   `clients` and `queues.queued_bytes`; `queues.dropped_blocks` is 0 until a hub counter exists (P5b-2);
   drop `peer_identity` from P5b-1 (R5).

## Major (WIRE gaps; resolutions)
6. Invalid `hello` in `AwaitHello`: `bad-request` on `corr=1`, stays `AwaitHello`, timer keeps running (R6).
7. `hello.proto` must equal the client's own prefix (major 1, minor C); the result carries N (R7).
8. JSON-mode `hello`: N = min(C, S_minor); major != 1 is `unsupported-version` (fatal); only as first
   line; a second `hello` is `bad-request` `detail.key` `"verb"` (R8).
9. `AwaitHello` row pinned: `corr 0` -> `bad-frame` `corr=0`; other verb -> `hello-required` on its
   `corr`; `hello` with `corr != 1` -> `bad-frame` `corr=0` (R9).
10. EOF before `Ready`: silent close; EOF mid-line in JSON: close without reply; EOF in `Ready`:
    `Draining`; bytes after EOF: `trailing-bytes`, close (R10).
11. Request check precedence list (11 steps) (R11).
12. JSON-mode `corr` recovery and the binary/JSON asymmetry for `corr 0` (R12).
13. JSON line reader edge cases (bound, CRLF, age stamp, empty line, linear time) (R13).
14. Unbounded echo of client text: `message` is `&'static str`; `detail.key` bounded (R14).
15. Keys spelled with escapes: refuse any key with a backslash as unknown before comparing (R15).
16. `-0` and integer form: digits only, range per field type, <= 20 digits (R16).
17. `hello.client`: 0..64 decoded bytes, no controls, no lone surrogates (R17).
18. Block keys of `hello.limits` are 0 where no block verb is served; explicit list (R18).
19. Quadratic CPU on hostile input: duplicate detection O(k^2) (cap 16 members/64 elements), line
    rescans, `Vec::drain` per line (R19).
20. Control-queue memory per uid (§5.1): 64 observer connections of one uid can hold 128 MiB (256 MiB for
    the level), recycled every `stall_ms`, while observer replies are <= 3 KiB. Needs a WIRE decision
    (R28). DECIDED by the controller 2026-11-18: observer-level `control_queue_bytes` ~320 KiB derived by
    the §5.1 formula from the observer `max_reply_bytes` (~16 KiB), no hub change; the 2 MiB default stays
    only for levels whose replies can be larger; the derivation (not the number) is written into WIRE §5.1
    and the `Limits` docs; a test pins observer `control_queue_bytes` <= the formula's bound.
21. §11.4 cases P5b-1 cannot reach: `conn-reply-page-truncated` (uses `events`, M3: retarget to `procs`),
    `conn-not-idle-with-subscription`, `conn-dribbling-reader`, END half of `conn-half-close` (P5b-2);
    "in-process socketpair" becomes the sans-IO core, socketpair runs move to P5b-4 (R21).

## Minor
22. `Server::output(id)` -> `output(id, now) -> Option<[&[u8]; 2]>` (the hub's `head` pins).
23. Do not call `head()` to test emptiness; use `hub.queued(c) == Pools::default()`.
24. Use `stream::ModeDetect`, not `frame::detect_mode` (one-shot, reports `truncated` for a partial magic).
25. One `control_frame(type, corr, payload)` helper (`encode_frame` needs sections); a REP over
    `max_reply_bytes` becomes `internal`.
26. The probe never serves `control` (act does): `accept(Control)` refuses uncounted; a core inside
    `bins/probe` is not reusable by `bins/act`: backlog item.
27. WIRE §5.3 "one mutex" is not needed by a single-threaded `poll(2)` driver: deviation note.
28. Gates: `assay.toml` `judge.targets` lists each new file as well as `source_roots`;
    `[crates."bins/probe"]` floor 90/85 is bootstrap and becomes a measured floor; module-only
    `lib.rs`/`main.rs` are not judged.

## Answers to the six questions (condensed)
1. The sans-IO `Conn`/`Server` cut is right; `Server` owns the backend; use the final-frame model.
2. Known-but-unserved M2 verbs answer `unknown-verb` and are absent from `hello.verbs` (§4.13 precedent);
   never `internal`; `forbidden`/`binary-required` still come from the full table.
3. Missing hostile cases: escaped key spellings and duplicates, surrogate shapes, `\u0000`/controls in
   `client`, 64/65 decoded vs escaped, 16/17 members, 64/65 elements, nested duplicate keys, wrong types
   for integers, 30 KB key (`detail` <= 2 KiB), CRLF/empty/exact-limit lines, 10k lines in one feed and
   1-byte dribbles (linear time), prefix + REQ + garbage in one feed, REP/EVT/END/BLOCK/DICT sent to the server.
4. `Peer{uid,pid}` captured once at accept and stored; counters and hub `add` use the stored uid; `client`
   is a label, never identity; `accept(level, Option<Peer>)`, `None` refuses uncounted.
5. One deterministic order per conn per `poll`: Hello, request deadline/line age, Idle, hub stall; first
   fatal outcome wins; conns in `ConnId` order (R20).
6. Resource holes: line reader, control-queue memory per uid, unbounded `detail` echo, frames left in a
   `Feed` after a fatal outcome must be discarded, `Feed`'s frame vector bounded only by the driver's read
   size (<= 64 KiB, P5b-4); counters: one idempotent `finish(id)` is the only close path (R22).

## Recommended package cut (adopted)
P5b-1a pure functions (limits, verb table, args reader, classify, encoders, JSON line reader); P5b-1b
`Conn`/`Server` state machine. WIRE.md edits go with the package that implements the rule (the controller
approved: §5.x edits with 1b).

## Not verified by the reviewer
Nothing compiled or run; serde_json behaviours are from knowledge of the crate; `check-lints.py` read
only up to its token list; `hub.rs` internals read through WIRE §5.3 and the public signatures;
Python `conn.py` handshake handling of a fatal REP vs `EVT closing` checked only partly.
