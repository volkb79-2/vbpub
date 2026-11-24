//! `hostobs-kernel`: read-only collectors that turn kernel facts into typed readings.
//!
//! Milestone M1, part 2 (plan v4 §A.3 `kernel`, §C.1–§C.3, §F.3 SP-2). The crate reads
//! cgroupfs and `/proc`/`/sys` files through a root-anchored, bounded, **read-only**
//! [`Fs`](crate::fs::Fs) abstraction and returns [`Reading`](crate::value::Reading) values, each carrying one quality code from
//! the closed vocabulary. It is the port of the proven topos sampler collectors
//! (`topos-sampler-poc-20261105`, `topos/sampler/src/{collect,procs,util,fsafe,cmdline}`
//! `.rs`) with the behaviours plan v4 requires to be fixed:
//!
//! - **A parse failure is a typed error carrying its quality, never a zero.** The
//!   sampler wrote `0.0` with a state code inside the same value; here a failure produces
//!   [`KernelError`](crate::cgroup::KernelError) and the metric is published as a [`Reading::missing`](crate::value::Reading::missing) with the code
//!   the failure maps to, so a consumer can never read a placeholder as a measurement.
//! - **Unknown device topology is a state, not "bottom device"** (§A.5, the M1 fix). The
//!   sampler treated a device it had never classified as a bottom device
//!   (`collect.rs::is_bottom`, `last.get(dev).copied().unwrap_or(true)`), so a failed
//!   topology read silently counted a stacked device's I/O into a cgroup aggregate — and
//!   the aggregate then double-counted. Here an unknown topology reports
//!   [`QualityCode::UNKNOWN_TOPOLOGY`](crate::quality::QualityCode::UNKNOWN_TOPOLOGY) on the aggregate and no totals at all.
//! - **No path-keyed identity.** A cgroup is `(mount id, inode)` and a process is
//!   `(boot id, pid, starttime ticks)`; a path is a label. Nothing in this crate derives
//!   an identity from a path, and a test pins the recycled-inode case.
//! - **Read-only, bounded, and free of `unsafe`.** The [`Fs`](crate::fs::Fs) trait has no write
//!   operation, every read takes a byte bound and every listing an entry bound, and the
//!   crate is `#![forbid(unsafe_code)]` (the plan allows `unsafe` here, but nothing
//!   needs it: `std::time::Instant`/`SystemTime` are the clocks and `File::open` the read
//!   path, so the allowance stays unused and the lint stays in force).
//! - **An injectable clock** ([`Clock`](crate::clock::Clock), [`ManualClock`](crate::clock::ManualClock)) so no test depends on host
//!   speed, load or the wall clock.
//!
//! # What this crate does not do
//!
//! It does not own a cgroup tree walk, a subject schedule, a dictionary, a store or a
//! socket: those are `bins/probe`, `store` and the client's package. It also does not
//! decide *when* a fact is stale (the consumer's interval is a policy input), and it does
//! not derive rates (`model` owns that arithmetic).
//!
//! # Why there is a local [`Reading`](crate::value::Reading) instead of `hostobs_model::Sample`
//!
//! A path dependency on `hostobs-model` changes `Cargo.lock`, and the Rust lane image
//! caches exactly the committed lock (the image's baked lock digest is the authority), so
//! `cargo --locked` refuses to build until the image is rebuilt from the new lock
//! (`HOB-3`/`HOB-10` already record this for `model`/`proto`). The crate is therefore
//! dependency-free: it produces the same three raw value types and the same closed
//! quality bytes, asserted against `fixtures/wire/quality.json`, and the probe package
//! converts `Reading` into the model's `Sample` at the boundary. `HOB-17` is the
//! decision point (delete this type at the first image rebuild); `HOB-19` records that
//! the conversion has no test yet because no package performs it.

#![forbid(unsafe_code)]
#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

pub mod cgroup;
pub mod clock;
pub mod cmdline;
pub mod fs;
pub mod identity;
pub mod parse;
pub mod procfs;
pub mod value;

/// The model-owned quality vocabulary used by kernel readings.
pub mod quality {
    pub use hostobs_model::quality::{Quality as QualityCode, QualityError};
}

/// Every bound a reader is given.
///
/// A kernel pseudo-file is a few hundred bytes; a directory of a cgroup is a handful of
/// names. [`Bounds::POLICY`] is the policy value used by the probe (the sampler's growth
/// limit `SLURP_MAX = 64 MiB` is far above any single file it reads, and its 4,096-entry
/// directory and 4,096-task budgets are the numbers the plan's SP-3 bound starts from).
/// A test passes its own tiny bounds to force a refusal, which is why nothing in a reader
/// has a literal bound inside it.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Bounds {
    /// Largest file a reader will accept, in bytes.
    pub max_file_bytes: usize,
    /// Largest number of lines (or key/value pairs) a reader will parse.
    pub max_lines: usize,
    /// Largest number of directory entries a listing will accept.
    pub max_entries: usize,
}

impl Bounds {
    /// The probe's policy bounds.
    pub const POLICY: Bounds = Bounds {
        max_file_bytes: 256 * 1024,
        max_lines: 4096,
        max_entries: 4096,
    };

    /// Explicit bounds (a test's or a consumer's own choice).
    #[must_use]
    pub const fn new(max_file_bytes: usize, max_lines: usize, max_entries: usize) -> Bounds {
        Bounds {
            max_file_bytes,
            max_lines,
            max_entries,
        }
    }
}
