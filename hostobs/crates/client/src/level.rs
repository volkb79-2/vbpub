//! The access levels of a hostobs socket (WIRE.md §1.1, §4.3).

/// The level a socket serves: the verbs of WIRE.md §4.3 are served per level, and the server
/// answers a known verb at the wrong level with `forbidden`.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Level {
    /// Read-only telemetry (`current`, `subscribe`, `snapshot_at`, `series_batch`, ...).
    Observe,
    /// Telemetry plus the operator extras (`procs` with `cmdline`, `export`).
    Operator,
    /// Peer-bound annotations from instrumented processes (`annotate`).
    Observer,
    /// The control plane served by `act`.
    Control,
}

impl Level {
    /// The level's name as the `hello` request and reply spell it.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Level::Observe => "observe",
            Level::Operator => "operator",
            Level::Observer => "observer",
            Level::Control => "control",
        }
    }

    /// The level named `name`, or `None` for any other text.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Level> {
        const ALL: [Level; 4] = [
            Level::Observe,
            Level::Operator,
            Level::Observer,
            Level::Control,
        ];
        ALL.into_iter().find(|level| level.name() == name)
    }
}

#[cfg(test)]
mod tests {
    use super::Level;

    #[test]
    fn names_round_trip_and_unknown_text_is_none() {
        for level in [
            Level::Observe,
            Level::Operator,
            Level::Observer,
            Level::Control,
        ] {
            assert_eq!(Level::from_name(level.name()), Some(level));
        }
        assert_eq!(Level::from_name("root"), None);
        assert_eq!(Level::from_name(""), None);
    }
}
