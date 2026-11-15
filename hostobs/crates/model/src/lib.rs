//! hostobs data model (milestone M1): identity, good/bad-quality values, the metric
//! registry, counter classification and rate derivation.
//!
//! This crate is **pure**: no I/O, no clock reads, no threads, no `unsafe`, no
//! dependencies. It receives facts (monotonic timestamps, cgroup inodes, raw counter
//! readings) and turns them into typed answers. Everything it cannot know is a typed
//! refusal, never an invented zero.
//!
//! # Contracts (plan v4 §C, `docs/design/hostobs/PLAN-v4-2026-11-08.md`)
//!
//! - **Identity is an incarnation (`inc`), never a cgroup path** (§C.1). An `inc` is
//!   minted once per `(mount_id, cgroup inode)`; the path is a label that may change.
//!   A new inode at the same path is a new `inc`. Missing on one scan is not deletion;
//!   two consecutive absent scans retire the incarnation, and a retired key that
//!   reappears is minted again with a fresh id. Process identity is
//!   `(boot_id, pid, start_ticks)` and never a bare pid.
//! - **Every value carries a quality code** (§C.2). Zero is a value; absence is never
//!   zero. `Sample::missing` cannot carry `Quality::Ok` and the `measured_*`
//!   accessors return `None` unless the sample is a real measurement, so a consumer
//!   cannot read a placeholder as a reading.
//! - **Counters stay raw `u64`; rates are derived over the actual monotonic
//!   interval** (§C.3). A zero-length or reversed interval is refused at construction
//!   (no rate at all), a decrease of the raw counter is `NoRate::CounterReset`
//!   (the reading is the new baseline and no delta is invented), and a rate is
//!   computed from the delta and the *whole* interval between the two readings, so
//!   missed samples widen the interval instead of inflating the last nominal bucket.
//! - **Metrics are registered with unit, kind, scope, source and permitted
//!   aggregation**, and a registry refuses a definition whose aggregation does not
//!   match its kind (§A.3/B.3).
//!
//! # Decisions taken where plan v4 is open
//!
//! These are recorded in `hostobs/docs/DESIGN-GUIDE.md` and, where a follow-up is
//! needed, in `hostobs/KNOWN_ISSUES_TODO_BACKLOG.md`.
//!
//! 1. A backwards raw counter is **always** classified as a reset. Wrapping is not
//!    inferred: without a declared counter width and a plausibility policy a wrap
//!    delta would be an invented reading, which §C.3 forbids. The design keeps the
//!    classifier as the single place a declared-width wrap would be added.
//! 2. The ratings of the closed quality vocabulary map onto a no-rate outcome as:
//!    no baseline or zero-length interval → `not-sampled`; reversed interval →
//!    `stale`; counter reset → `reset`; a missing sample propagates its own code;
//!    a value of the wrong kind → `unsupported` (a caller defect, visible in the
//!    quality column instead of a panic).
//! 3. `Sample::missing` is a fallible constructor (`Option`): an absence cannot
//!    carry `Quality::Ok` and a measurement cannot be silently missing.

#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

pub mod identity;
pub mod metric;
pub mod quality;
pub mod rate;
pub mod value;
