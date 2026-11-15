//! The wire quality vocabulary (plan v4 §C.2) as the codec sees it: one `u8` per value
//! row, in a column of its own.
//!
//! `hostobs-model` holds the same closed table for values in memory. The two are the same
//! vocabulary by construction: **both crates assert their table against the shared fixture
//! `fixtures/wire/quality.json`**, which the independent Python decoder also checks, so a
//! code added to one side without the other turns the lanes red. (A path dependency
//! between the two crates is not available in this package: it would change `Cargo.lock`,
//! and the lane image caches exactly the committed lock. The fixture is the cheaper,
//! stronger contract — it binds a third implementation as well.)
//!
//! Codes outside the table are refused: an unknown code is a future protocol revision,
//! and guessing it would turn "unknown" into "ok".

use core::fmt;

use crate::error::ProtoError;

/// Quality code of a value (append-only; the stored byte is the discriminant).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Quality {
    /// The value is a measurement.
    Ok = 0,
    /// The file or key is not present.
    Absent = 1,
    /// Reading failed (permission, I/O error).
    Unreadable = 2,
    /// The reading is older than twice the sampling interval.
    Stale = 3,
    /// A counter went backwards; the reading is the new baseline.
    Reset = 4,
    /// Shed under load or decimated away.
    NotSampled = 5,
    /// The kernel does not provide this fact here.
    Unsupported = 6,
    /// The topology behind the value is not understood.
    UnknownTopology = 7,
}

/// The complete quality vocabulary, in stored-code order.
pub const ALL: [Quality; 8] = [
    Quality::Ok,
    Quality::Absent,
    Quality::Unreadable,
    Quality::Stale,
    Quality::Reset,
    Quality::NotSampled,
    Quality::Unsupported,
    Quality::UnknownTopology,
];

impl Quality {
    /// The stored byte.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// The stable lower-case name used by fixtures and operator output.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Quality::Ok => "ok",
            Quality::Absent => "absent",
            Quality::Unreadable => "unreadable",
            Quality::Stale => "stale",
            Quality::Reset => "reset",
            Quality::NotSampled => "not-sampled",
            Quality::Unsupported => "unsupported",
            Quality::UnknownTopology => "unknown-topology",
        }
    }

    /// Decode a stored code; the refusal names the row so a corrupt column locates itself.
    ///
    /// # Errors
    ///
    /// [`ProtoError::UnknownQuality`].
    pub fn from_code(code: u8, row: usize) -> Result<Quality, ProtoError> {
        ALL.get(code as usize)
            .copied()
            .filter(|quality| quality.code() == code)
            .ok_or(ProtoError::UnknownQuality { row, code })
    }

    /// True only for [`Quality::Ok`]: the eight value bytes are a measurement.
    #[must_use]
    pub const fn is_measurement(self) -> bool {
        matches!(self, Quality::Ok)
    }
}

impl fmt::Display for Quality {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// The committed vocabulary fixture both crates and the Python decoder agree on.
pub const VOCABULARY_FIXTURE: &str = include_str!("../../../fixtures/wire/quality.json");

#[cfg(test)]
mod tests {
    use super::{Quality, ALL, VOCABULARY_FIXTURE};

    #[test]
    fn codes_round_trip_and_every_outside_code_is_refused() {
        for (position, quality) in ALL.into_iter().enumerate() {
            assert_eq!(usize::from(quality.code()), position);
            assert_eq!(Quality::from_code(quality.code(), 0), Ok(quality));
        }
        for code in 8u8..=u8::MAX {
            assert_eq!(
                Quality::from_code(code, 3),
                Err(crate::error::ProtoError::UnknownQuality { row: 3, code })
            );
        }
        assert_eq!(
            Quality::Ok.code(),
            0,
            "code 0 is the measurement and never an absence"
        );
        assert!(Quality::Ok.is_measurement());
        for quality in ALL {
            if quality != Quality::Ok {
                assert!(!quality.is_measurement(), "{quality}");
            }
        }
    }

    #[test]
    fn the_table_is_the_shared_fixture_the_python_decoder_also_checks() {
        assert!(VOCABULARY_FIXTURE.contains("\"case\": \"quality-vocabulary\""));
        let missing: Vec<String> = ALL
            .iter()
            .flat_map(|quality| {
                [
                    format!("\"code\": {},", quality.code()),
                    format!("\"name\": \"{}\"", quality.name()),
                ]
            })
            .filter(|needle| !VOCABULARY_FIXTURE.contains(needle.as_str()))
            .collect();
        assert!(
            missing.is_empty(),
            "the shared fixture is missing {missing:?}"
        );
        for quality in ALL {
            assert_eq!(quality.to_string(), quality.name());
        }
        assert_eq!(ALL.len(), 8, "the vocabulary is closed at eight codes");
    }
}
