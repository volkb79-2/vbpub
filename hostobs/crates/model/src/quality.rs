//! The closed quality vocabulary of plan v4 §C.2.
//!
//! Every value travelling through hostobs (live frame, history bucket, disk segment)
//! carries one of these codes next to the raw reading. The table is **append-only**:
//! stored segments keep the numbers, so a code never changes meaning. A zero is a
//! value and absence is never a zero; the code is what tells them apart.
//!
//! `Quality::Ok` is the only code for which a reading is a measurement. Consumers
//! read values through `Sample::measured_*` (see the crate docs), which return
//! `None` for every other code, so a placeholder can never be read as a reading.
//!
//! Codes not listed here are refused by [`Quality::from_code`]. A code this crate
//! does not know belongs to a future protocol revision; guessing its meaning was
//! how "unknown" would become "ok".

use core::fmt;

/// The model's quality vocabulary as used by collectors and wire clients.
///
/// This is an alias, not a second type: every consumer shares [`Quality`].
pub type QualityCode = Quality;

/// The committed vocabulary fixture shared by the Rust types and the Python decoder.
pub const VOCABULARY_FIXTURE: &str = include_str!("../../../fixtures/wire/quality.json");

/// Quality code of a value (plan v4 §C.2, append-only).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Quality {
    /// The value is a measurement.
    Ok = 0,
    /// The file or key is not present (for example a controller that is switched off).
    Absent = 1,
    /// Reading failed (permission, I/O error).
    Unreadable = 2,
    /// The reading is older than twice the sampling interval.
    Stale = 3,
    /// A counter went backwards: the reading is the new baseline and there is no rate.
    Reset = 4,
    /// The value was shed under load or decimated away.
    NotSampled = 5,
    /// The kernel does not provide this fact on this host.
    Unsupported = 6,
    /// The device topology behind the value is not understood.
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

/// A quality code this crate does not know.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum QualityError {
    /// The code is outside the closed vocabulary of plan v4 §C.2.
    UnknownCode {
        /// The offending stored byte.
        code: u8,
        /// The wire row whose quality column held the byte.
        row: usize,
    },
}

impl fmt::Display for QualityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            QualityError::UnknownCode { code, .. } => write!(f, "unknown quality code {code}"),
        }
    }
}

impl core::error::Error for QualityError {}

impl Quality {
    /// The measurement code, in the spelling collectors have historically used.
    pub const OK: Quality = Quality::Ok;
    /// The absent code, in the spelling collectors have historically used.
    pub const ABSENT: Quality = Quality::Absent;
    /// The unreadable code, in the spelling collectors have historically used.
    pub const UNREADABLE: Quality = Quality::Unreadable;
    /// The stale code, in the spelling collectors have historically used.
    pub const STALE: Quality = Quality::Stale;
    /// The reset code, in the spelling collectors have historically used.
    pub const RESET: Quality = Quality::Reset;
    /// The not-sampled code, in the spelling collectors have historically used.
    pub const NOT_SAMPLED: Quality = Quality::NotSampled;
    /// The unsupported code, in the spelling collectors have historically used.
    pub const UNSUPPORTED: Quality = Quality::Unsupported;
    /// The unknown-topology code, in the spelling collectors have historically used.
    pub const UNKNOWN_TOPOLOGY: Quality = Quality::UnknownTopology;

    /// The stored byte of this code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// The code's stable lower-case name (used by fixtures and operator output).
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

    /// Decode a stored code; every code outside the table is an error.
    #[allow(clippy::missing_const_for_fn)]
    pub fn from_code(code: u8, row: usize) -> Result<Quality, QualityError> {
        match code {
            0 => Ok(Quality::Ok),
            1 => Ok(Quality::Absent),
            2 => Ok(Quality::Unreadable),
            3 => Ok(Quality::Stale),
            4 => Ok(Quality::Reset),
            5 => Ok(Quality::NotSampled),
            6 => Ok(Quality::Unsupported),
            7 => Ok(Quality::UnknownTopology),
            other => Err(QualityError::UnknownCode { code: other, row }),
        }
    }

    /// Parse a code's name; unknown names are refused.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Quality> {
        ALL.into_iter().find(|quality| quality.name() == name)
    }

    /// True only for [`Quality::Ok`]: the reading is a measurement.
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

#[cfg(test)]
mod tests {
    use super::{Quality, QualityError, ALL, VOCABULARY_FIXTURE};

    #[test]
    fn every_code_round_trips_through_its_byte() {
        for (position, quality) in ALL.into_iter().enumerate() {
            assert_eq!(
                usize::from(quality.code()),
                position,
                "code order is the stored table order"
            );
            assert_eq!(Quality::from_code(quality.code(), 0), Ok(quality));
            assert_eq!(Quality::from_name(quality.name()), Some(quality));
        }
    }

    #[test]
    fn codes_outside_the_table_are_refused_in_both_directions() {
        for code in 8u8..=u8::MAX {
            assert_eq!(
                Quality::from_code(code, 0),
                Err(QualityError::UnknownCode { code, row: 0 })
            );
        }
        assert_eq!(Quality::from_name(""), None);
        assert_eq!(Quality::from_name("OK"), None, "names are lower case");
        assert_eq!(
            Quality::from_name("not_sampled"),
            None,
            "hyphen, not underscore"
        );
        assert_eq!(Quality::from_name("unknown"), None);
    }

    #[test]
    fn only_ok_is_a_measurement() {
        for quality in ALL {
            assert_eq!(
                quality.is_measurement(),
                quality == Quality::Ok,
                "{quality}"
            );
        }
    }

    #[test]
    fn error_text_names_the_offending_code() {
        assert_eq!(
            QualityError::UnknownCode { code: 42, row: 3 }.to_string(),
            "unknown quality code 42"
        );
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
        assert_eq!(ALL.len(), 8, "the vocabulary is closed at eight codes");
    }
}
