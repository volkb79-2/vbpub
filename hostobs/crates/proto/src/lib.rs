//! The hostobs wire codec (milestone M1): frame header, section directory, fixed-width
//! typed little-endian columns, raw `u64` counters with a `u8` quality column, and bounded
//! per-segment dictionaries.
//!
//! This crate is **pure**: no I/O, no sockets, no clock, no threads, no `unsafe`, and no
//! dependencies. Bytes in, typed values out (and the exact inverse), with every failure a
//! typed [`ProtoError`](crate::error::ProtoError). The probe serves these bytes, the
//! store writes them to segments and the client decodes them; none of that belongs here.
//!
//! # Contracts
//!
//! - **Fixed-width typed little-endian columns** replace the sampler's varint TLV
//!   (plan v4 §B.1, decision D4). One column is one `memoryview` + `struct.unpack_from`
//!   on the Python side, and exact values on the Rust side: no `u64` counter is ever
//!   routed through `f64`, so integers above 2<sup>53</sup> survive.
//! - **Raw `u64` counters and a `u8` quality per value** (§C.2/§C.3). Zero is a value;
//!   absence is never zero, which is why the quality column is mandatory and why the
//!   `measured` accessors refuse to return a placeholder.
//! - **Self-contained dictionaries with bounded generations** (§B.3). Indices never
//!   change meaning within `(epoch, dict_gen)`; compaction is a new generation sent
//!   complete; a receiver refuses a block from a generation it was not sent.
//! - **Unknown future versions are refused**, never guessed: an unknown *required*
//!   section fails the block, a known section with a newer version fails even when
//!   optional, and an unknown quality code or column type is an error.
//! - **No panic on untrusted bytes** (this crate forbids `unsafe`; every length is
//!   checked before it is used, every arithmetic operation is checked). A decode returns
//!   either a complete validated value or one typed error — never a partial success and
//!   never a panic.
//!
//! # Golden fixtures (§B.5)
//!
//! `fixtures/wire/*.bin` are canonical bytes and `fixtures/wire/*.json` their decoded
//! typed values. The Rust tests consume them, and an **independent stdlib Python
//! decoder** (`python/reference/hostobs_wire_ref.py`) decodes the same bytes and compares
//! against the same JSON. The two implementations share the documents, not the code: the
//! Python one is written from the layout above, so a mistake in one is not copied into
//! the other. The Python run is a documented manual check, **not** a registered lane
//! (`hostobs/KNOWN_ISSUES_TODO_BACKLOG.md` `HOB-4`), so it is not gate evidence yet.
//!
//! # Decisions taken where plan v4 is open
//!
//! - **Section bodies are length-prefixed and eight-byte aligned**, and a reader requires
//!   a body's declared length to equal what its content needs. There are no inter-body
//!   pad bytes: `16 + 8 × n_sec` is divisible by eight, so alignment is a property of the
//!   layout rather than a rule to police.
//! - **The dictionary lives in the same frame** (`kind = 5`) instead of a separate `DICT`
//!   frame on the block path. §B.3 allows a full generation to precede the values that use
//!   it, and carrying it inside the block is what makes an exported segment self-contained
//!   (§C.5) and the fixture a single file per case. The `DICT` frame type stays defined and
//!   unconsumed; a later package may split it out without a wire change.
//! - **`kind` codes**: 1 header, 2 rows, 3 column, 4 time, 5 dictionary.
//! - **History blocks carry `n_rows` buckets** in the header, so every section keeps one
//!   authoritative row count and a disagreement is a typed refusal rather than a guess.

#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

pub mod dict;
pub mod error;
pub mod frame;
pub mod quality;
pub mod read;
pub mod write;
