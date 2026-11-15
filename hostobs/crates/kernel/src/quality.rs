//! The quality codes a kernel reading carries, in the closed vocabulary of plan v4 §C.2.
//!
//! Every value a reader here produces is either a measurement or a typed refusal, and a
//! refusal carries the same `u8` code the wire column, the model and the store use. Codes
//! are append-only and shared, so the number is the contract: this crate asserts its table
//! against `fixtures/wire/quality.json`, which `model`, `proto` and the independent Python
//! decoder assert too. A renumbering or a dropped code is a red test in all four places.
//!
//! # Why this is not `hostobs_model::quality::Quality`
//!
//! `hostobs-kernel` has no dependency on `hostobs-model`. A path dependency changes
//! `Cargo.lock`, and the Rust lane image caches exactly the committed lock, so `cargo
//! --locked` refuses to build until the image is rebuilt from the new lock (the same
//! constraint that keeps `proto` and `model` apart, `HOB-3`/`HOB-10`). The two crates are
//! therefore bound by the shared fixture instead of by a type, and the thin
//! [`QualityCode`] newtype is what lets a reader state the code without inventing a
//! parallel vocabulary. `HOB-17` records the decision point: the first image rebuild
//! replaces this newtype with `hostobs_model::quality::Quality` and deletes this module.

use core::fmt;

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
    /// [`QualityError::UnknownCode`] for anything outside `0..=7`.
    pub const fn from_code(code: u8) -> Result<QualityCode, QualityError> {
        if code <= 7 {
            Ok(QualityCode(code))
        } else {
            Err(QualityError::UnknownCode(code))
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

/// A stored code this crate does not know.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum QualityError {
    /// The code is outside the closed vocabulary.
    UnknownCode(u8),
}

impl fmt::Display for QualityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            QualityError::UnknownCode(code) => write!(f, "unknown quality code {code}"),
        }
    }
}

impl core::error::Error for QualityError {}

#[cfg(test)]
mod tests {
    use super::{QualityCode, QualityError};

    // The shared fixture table is asserted by the integration test
    // `crates/kernel/tests/quality_table.rs`: an in-crate `#[path]` to
    // `hostobs/test-support/json.rs` would resolve through the non-existent
    // `crates/kernel/src/quality/` directory and fail, and a fixture-driven assertion
    // belongs in a test that is not inside the library.

    #[test]
    fn outside_codes_are_refused_in_both_directions() {
        for code in 8u8..=u8::MAX {
            assert_eq!(
                QualityCode::from_code(code),
                Err(QualityError::UnknownCode(code))
            );
        }
        assert_eq!(
            QualityError::UnknownCode(9).to_string(),
            "unknown quality code 9"
        );
        for code in [
            QualityCode::OK,
            QualityCode::ABSENT,
            QualityCode::UNKNOWN_TOPOLOGY,
        ] {
            assert!(!code.name().is_empty());
            assert_eq!(code.to_string(), code.name());
        }
    }

    #[test]
    fn only_ok_marks_a_measurement() {
        for code in 0u8..=7 {
            let parsed = QualityCode::from_code(code).expect("in the vocabulary");
            assert_eq!(parsed.is_measurement(), code == 0, "{}", parsed.name());
        }
    }

    #[test]
    fn the_eight_names_are_the_ones_the_wire_uses() {
        let names: Vec<&str> = (0u8..=7)
            .map(|code| {
                QualityCode::from_code(code)
                    .expect("in the vocabulary")
                    .name()
            })
            .collect();
        assert_eq!(
            names,
            vec![
                "ok",
                "absent",
                "unreadable",
                "stale",
                "reset",
                "not-sampled",
                "unsupported",
                "unknown-topology",
            ]
        );
    }
}
