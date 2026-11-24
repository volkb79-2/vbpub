//! `hostobs-kernel`: read-only collectors that turn kernel facts into typed readings.
//!
//! Milestone M1, part 2 (plan v4 §A.3 `kernel`, §C.1–§C.3, §F.3 SP-2). The crate reads
//! cgroupfs and `/proc`/`/sys` files through a root-anchored, bounded, **read-only**
//! [`Fs`](crate::fs::Fs) abstraction and returns [`Reading`](crate::value::Reading) values,
//! each carrying one quality code from the closed vocabulary. It is the port of the
//! proven topos sampler collectors (tag `archive/topos-sampler-poc-20261105`, commit
//! `193bf601f`, path `topos/sampler/`) with the behaviours plan v4 requires to be fixed:
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
//!   [`QualityCode::UnknownTopology`](crate::quality::QualityCode::UnknownTopology) on the aggregate and no totals at all.
//! - **No path-keyed identity.** A cgroup is `(mount id, inode)` and a process is
//!   `(boot id, pid, starttime ticks)`; a path is a label. Nothing in this crate derives
//!   an identity from a path, and a test pins the recycled-inode case.
//! - **Read-only, bounded, and one audited `unsafe` item.** The [`Fs`](crate::fs::Fs) trait
//!   has no write operation, every read takes a byte bound and every listing an entry
//!   bound. `activation::adopt_raw` is the crate's only `unsafe` item: it claims a
//!   systemd-owned fd after the checks in [`activation`]. The crate carries the
//!   `unsafe_code` deny lint, and the sole allowance is on that function.
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
//! # Shared model vocabulary
//!
//! [`Reading`](crate::value::Reading) is an alias of `hostobs_model::Sample`, and
//! [`QualityCode`](crate::quality::QualityCode) is the model's `Quality` type. Kernel
//! collectors, rate derivation and the wire codec therefore agree by type rather than by
//! copied tables or a boundary conversion.

#![deny(unsafe_code)]
#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

pub mod activation;
pub mod cgroup;
pub mod clock;
pub mod cmdline;
pub mod fs;
pub mod identity;
pub mod parse;
pub mod procfs;

/// The model-owned quality vocabulary used by kernel readings.
pub mod quality {
    pub use hostobs_model::quality::Quality as QualityCode;
}

/// The model-owned value vocabulary returned by kernel readers.
pub mod value {
    pub use hostobs_model::value::{Sample as Reading, Value};
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
