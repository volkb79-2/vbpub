//! The store's own copy of the closed quality vocabulary (plan v4 §C.2).
//!
//! Every value a reader serves is either a measurement **or** a typed refusal, and the refusal
//! carries the same `u8` code the wire column, the `model` crate and the store use. Codes are
//! append-only and shared, so the *number* is the contract: this module asserts its table
//! against `fixtures/wire/quality.json`, which `model`, `proto`, `kernel` and the independent
//! Python decoder assert too. A renumbering or a dropped code is a red test in all of them.
//!
//! # Why the store does not use `hostobs_model::quality::Quality`
//!
//! `hostobs-store` has no dependency on `hostobs-model` or `hostobs-proto`: a path dependency
//! changes `Cargo.lock`, and the Rust lane image caches exactly the committed lock, so
//! `cargo --locked` refuses to build until the image is rebuilt from the new lock (`HOB-27`,
//! the same constraint that keeps `kernel` apart from `model` in `HOB-17`). The crates are
//! bound by the shared fixture instead of by a type, and [`QualityCode`] is the thin newtype
//! that lets the store state the code without inventing a parallel vocabulary.
//!
//! The store never *derives* a value: it returns raw `u64`/`i64` readings with their quality
//! byte (§C.3), and a non-measurement is never presented as a value
//! ([`Reading::measured`]).

use core::fmt;

/// The eight quality codes of plan v4 §C.2, in their stored order.
pub const ALL: [QualityCode; 8] = [
    QualityCode::OK,
    QualityCode::ABSENT,
    QualityCode::UNREADABLE,
    QualityCode::STALE,
    QualityCode::RESET,
    QualityCode::NOT_SAMPLED,
    QualityCode::UNSUPPORTED,
    QualityCode::UNKNOWN_TOPOLOGY,
];

/// The stored byte of one closed quality code (plan v4 §C.2, append-only).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct QualityCode(u8);

impl QualityCode {
    /// The value is a measurement.
    pub const OK: QualityCode = QualityCode(0);
    /// The file or key is not present (a controller that is switched off, for example).
    pub const ABSENT: QualityCode = QualityCode(1);
    /// The reading could not be turned into a value (permission, I/O or parse failure).
    pub const UNREADABLE: QualityCode = QualityCode(2);
    /// The reading is older than its allowed age.
    pub const STALE: QualityCode = QualityCode(3);
    /// A counter went backwards: the reading is the new baseline and there is no rate.
    pub const RESET: QualityCode = QualityCode(4);
    /// The value was not sampled: shed, decimated, or refused by a reader bound.
    pub const NOT_SAMPLED: QualityCode = QualityCode(5);
    /// The kernel does not provide this fact on this host.
    pub const UNSUPPORTED: QualityCode = QualityCode(6);
    /// The topology behind the value is not understood, so no aggregate is reported.
    pub const UNKNOWN_TOPOLOGY: QualityCode = QualityCode(7);

    /// The code for a stored byte; a code outside the table is refused.
    ///
    /// # Errors
    ///
    /// [`crate::error::StoreError::QualityCode`] for anything outside `0..=7`.
    pub const fn from_code(code: u8) -> Result<QualityCode, crate::error::StoreError> {
        if code <= 7 {
            Ok(QualityCode(code))
        } else {
            Err(crate::error::StoreError::QualityCode { found: code })
        }
    }

    /// The stored byte.
    #[must_use]
    pub const fn code(self) -> u8 {
        self.0
    }

    /// The stable lower-case name used by fixtures and operator output.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self.0 {
            0 => "ok",
            1 => "absent",
            2 => "unreadable",
            3 => "stale",
            4 => "reset",
            5 => "not-sampled",
            6 => "unsupported",
            7 => "unknown-topology",
            _ => "outside-the-vocabulary",
        }
    }

    /// True only for [`QualityCode::OK`]: the reading is a measurement.
    #[must_use]
    pub const fn is_measurement(self) -> bool {
        self.0 == 0
    }
}

impl fmt::Display for QualityCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// One stored reading: a raw value and the quality code it was stored with.
///
/// The pair is the unit §C.2 asks for: a value is never served without the code that says
/// whether it *is* a value, and a counter's `u64` is kept exact (no `f64` conversion, which
/// loses integers above 2^53).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Reading {
    /// The raw stored value (a counter's absolute value, a gauge's reading, a state's code).
    pub value: u64,
    /// The quality it was stored with.
    pub quality: QualityCode,
}

impl Reading {
    /// A reading with its quality.
    #[must_use]
    pub const fn new(value: u64, quality: QualityCode) -> Reading {
        Reading { value, quality }
    }

    /// A reading of `value` that is a measurement.
    #[must_use]
    pub const fn measured_value(value: u64) -> Reading {
        Reading {
            value,
            quality: QualityCode::OK,
        }
    }

    /// A reading whose value is unknown, for the reason `quality`.
    ///
    /// The value is stored as zero, and [`Reading::measured`] is what a consumer must call
    /// before using it: there is no way to read the placeholder as data.
    #[must_use]
    pub const fn missing(quality: QualityCode) -> Reading {
        Reading { value: 0, quality }
    }

    /// The value, or `None` when the quality says this reading is not a measurement.
    #[must_use]
    pub const fn measured(self) -> Option<u64> {
        if self.quality.is_measurement() {
            Some(self.value)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{QualityCode, Reading, ALL};
    use crate::error::StoreError;

    #[test]
    fn codes_round_trip_and_every_outside_code_is_refused() {
        for (position, quality) in ALL.into_iter().enumerate() {
            assert_eq!(usize::from(quality.code()), position);
            assert_eq!(QualityCode::from_code(quality.code()), Ok(quality));
            assert_eq!(quality.to_string(), quality.name());
        }
        for code in 8u8..=u8::MAX {
            assert_eq!(
                QualityCode::from_code(code),
                Err(StoreError::QualityCode { found: code })
            );
        }
        assert!(QualityCode::OK.is_measurement());
        for quality in ALL {
            if quality != QualityCode::OK {
                assert!(!quality.is_measurement(), "{quality}");
            }
        }
        assert_eq!(QualityCode::UNKNOWN_TOPOLOGY.code(), 7);
        assert_eq!(ALL.len(), 8, "the vocabulary is closed at eight codes");
    }

    #[test]
    fn a_reading_is_a_value_only_when_its_quality_says_so() {
        let measured = Reading::measured_value(u64::MAX);
        assert_eq!(measured.measured(), Some(u64::MAX), "a u64 stays exact");
        assert_eq!(measured.quality, QualityCode::OK);
        for quality in ALL.into_iter().skip(1) {
            let missing = Reading::missing(quality);
            assert_eq!(missing.measured(), None, "{quality}");
            assert_eq!(missing.value, 0);
        }
        assert_eq!(
            Reading::new(42, QualityCode::STALE).measured(),
            None,
            "a stale value is not served as a measurement"
        );
    }
}
