//! `hostobs-store`: the on-disk history and events store (milestone M1, part 3).
//!
//! The store is the first hostobs crate that **writes**, so its shape is decided by the four
//! failures that matter, each of which is a typed refusal or a counter rather than a silent
//! outcome (plan v4 §A.4, §C.5):
//!
//! | failure | what the store does |
//! |---|---|
//! | disk fill | every tier is bounded by `keep_s` **and** `cap_bytes`; a full disk is [`StoreError::NoSpace`] and a counted write error, never a swallowed one |
//! | RAM growth | the writer queue is bounded in bytes; the active dictionary is bounded by `dict_active_max`; a query is bounded by its record limit; a reader never allocates more than the file it was given |
//! | torn files after a crash | every record carries its own checksum and its own length, so an open segment's torn tail is *detected*, truncated to the last good record, and reported ([`TierScan::torn`]) |
//! | silent data loss | a dropped job is counted ([`QueueStats::dropped`]), a corrupt segment is quarantined by name and reported, and nothing is ever deleted unless the store itself created it and it matches its own naming and header |
//!
//! # Components
//!
//! * [`mod@format`] — the byte-level segment format (header, records, footer, naming), checked
//!   before anything is allocated. `proto` owns the payload codec; this module checks only
//!   the four frame-header facts that decide whether bytes may be stored at all.
//! * [`fsio`] — **the only write path**: one directory handle, single-component names, no
//!   symbolic link ever followed, atomic seal by temp-name + fsync + rename.
//! * [`crc`] — CRC-32, so a record's checksum needs no dependency.
//! * [`dictionary`] — the active incarnation index: bounded, epoch-scoped, never reusing an
//!   id, retiring an incarnation only when no stored segment can still reference it.
//! * [`segment`] — one segment file: open/append/seal, and the reader that returns the
//!   blocks a segment holds with their quality codes intact.
//! * [`tier`] — one directory of segments with its configured step, `keep_s`, `cap_bytes`
//!   and ring semantics (whole-segment eviction, oldest first).
//! * [`queue`] — the bounded writer queue with counted drops and one writer.
//! * [`tmpfs`] — the mount table reader that decides whether a tier really is on tmpfs.
//! * [`events`] — the events/spans store: the same segment machinery and its own cap.
//! * [`store`] — the [`Store`](crate::store::Store) that ties the tiers, the dictionary and the
//!   queue together.
//!
//! # Decisions taken where the plan is open
//!
//! * The store uses `proto`'s frame vocabulary and declares `model` for the HOB-39 decoded-row
//!   join; it has no local frame, quality or value type.
//! * The store keeps **raw `u64` counters** and **quality per value** (§C.3/§C.2): it never
//!   converts a value, never derives a rate and never rounds a reading.
//! * A record's payload is a `proto` BLOCK frame, so a segment is a sequence of column blocks
//!   with a per-segment dictionary generation (§B.3). The generation belongs to the segment
//!   and is checked on every append, so a block can never be stored against names it was
//!   not encoded with.
//!
//! [`StoreError::NoSpace`]: crate::error::StoreError::NoSpace
//! [`QueueStats::dropped`]: crate::queue::QueueStats::dropped
//! [`TierScan::torn`]: crate::tier::TierScan::torn_repaired

#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

pub mod crc;
pub mod dictionary;
pub mod error;
pub mod events;
pub mod format;
pub mod fsio;
pub mod queue;
pub mod segment;
pub mod store;
pub mod tier;
pub mod tmpfs;
