//! The injected backend of the serving core (design R5 and section 1; WIRE.md sections 4.4 and
//! 4.5).
//!
//! A [`Conn`](crate::conn::Conn) never reads the sampler, the store or a clock: everything the
//! two P5b-1 verbs need from the probe comes through [`Backend`]. `hello` needs the server's
//! identity ([`Identity`]); `status` needs the facts only the backend knows ([`StatusInfo`]:
//! start time, uptime, the tick and the tier list).
//!
//! # Who fills what in `status` (WIRE.md section 4.5)
//!
//! - `queues.queued_bytes` is read by the connection from the send hub it is handed
//!   (`global_pools()`: Live plus Query bytes; the control pool exists per connection only).
//! - `clients` (connections per level) and `queues.dropped_blocks` are the server layer's: the
//!   server wraps its backend in an adapter whose [`Backend::status`] returns the counters in
//!   [`StatusInfo::clients`] and [`StatusInfo::dropped_blocks`]. A plain backend leaves them
//!   empty and 0 (`dropped_blocks` stays 0 until the hub counts drops, P5b-2).

use crate::limits::Level;

/// What `hello` announces about the server (WIRE.md section 4.4).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Identity {
    /// `server`: the server's name and version text.
    pub server: String,
    /// `boot_id`: the UUID text of this boot.
    pub boot_id: String,
    /// `epoch`.
    pub epoch: u64,
    /// `features`, a subset of `peak_reset`, `placement`, `damon` and `enrich`.
    pub features: Vec<String>,
}

/// The `tick` member of `status`.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct TickInfo {
    /// The latest tick's sequence number.
    pub seq: u64,
    /// The latest tick's monotonic time.
    pub last_mono_ns: u64,
    /// How long the latest scan took.
    pub scan_ms: u32,
}

/// One entry of the `tiers` list of `status`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TierInfo {
    /// The tier name (`hot`, `warm`, `cold`).
    pub name: String,
    /// The bucket width.
    pub step_ms: u32,
    /// The retention.
    pub keep_s: u32,
    /// The bytes held now.
    pub bytes: u64,
    /// The byte cap.
    pub cap_bytes: u64,
}

/// What `status` reports (WIRE.md section 4.5), minus what the connection reads itself.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct StatusInfo {
    /// `started_real_ns`: the wall-clock start of this boot.
    pub started_real_ns: i64,
    /// `uptime_ms`.
    pub uptime_ms: u64,
    /// `tick`.
    pub tick: TickInfo,
    /// `clients`: connections per level, in the order given (the server layer fills it).
    pub clients: Vec<(Level, u32)>,
    /// `queues.dropped_blocks` (the server layer fills it; 0 in P5b-1).
    pub dropped_blocks: u64,
    /// `tiers`, at most 8 (a longer list overflows `max_reply_bytes` and answers `internal`).
    pub tiers: Vec<TierInfo>,
}

/// The probe's side of the serving core: identity and status, both read-only.
pub trait Backend {
    /// The facts `hello` announces.
    fn identity(&self) -> Identity;
    /// The facts `status` reports.
    fn status(&self) -> StatusInfo;
}

/// A backend with fixed answers, for tests and the P5b-4 bring-up.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FixedBackend {
    /// The answer of [`Backend::identity`].
    pub identity: Identity,
    /// The answer of [`Backend::status`].
    pub status: StatusInfo,
}

impl FixedBackend {
    /// A backend named `hostobs-probe`, epoch 1, no features, no tiers, all counters 0.
    #[must_use]
    pub fn new() -> FixedBackend {
        FixedBackend {
            identity: Identity {
                server: String::from("hostobs-probe"),
                boot_id: String::from("00000000-0000-4000-8000-000000000001"),
                epoch: 1,
                features: Vec::new(),
            },
            status: StatusInfo::default(),
        }
    }
}

impl Default for FixedBackend {
    fn default() -> FixedBackend {
        FixedBackend::new()
    }
}

impl Backend for FixedBackend {
    fn identity(&self) -> Identity {
        self.identity.clone()
    }

    fn status(&self) -> StatusInfo {
        self.status.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::{Backend, FixedBackend};

    #[test]
    fn fixed_backend_answers_what_it_holds() {
        let mut backend = FixedBackend::new();
        assert_eq!(backend.identity().server, "hostobs-probe");
        assert_eq!(backend.identity().epoch, 1);
        assert!(backend.status().tiers.is_empty());
        backend.identity.epoch = 7;
        backend.status.uptime_ms = 9;
        assert_eq!(backend.identity().epoch, 7);
        assert_eq!(backend.status().uptime_ms, 9);
        assert_eq!(FixedBackend::default(), FixedBackend::new());
    }
}
