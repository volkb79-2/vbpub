//! Wire helpers for the closed quality vocabulary owned by [`hostobs_model`].
//!
//! A quality is one `u8` per value row. The type and table live in `model`; this
//! module only re-exports them and adds the row-aware decoder error the wire codec
//! needs. The shared fixture still binds both Rust decoder paths and the independent
//! Python decoder.

pub use hostobs_model::quality::{Quality, ALL, VOCABULARY_FIXTURE};

use crate::error::ProtoError;
use hostobs_model::quality::QualityError;

/// Decode a quality byte, naming the row that held an unknown code.
///
/// # Errors
///
/// [`ProtoError::UnknownQuality`] for a byte outside the closed model vocabulary.
pub fn from_code(code: u8, row: usize) -> Result<Quality, ProtoError> {
    Quality::from_code(code, row).map_err(|error| match error {
        QualityError::UnknownCode { code, .. } => ProtoError::UnknownQuality { row, code },
    })
}

#[cfg(test)]
mod tests {
    use super::{from_code, Quality, ALL, VOCABULARY_FIXTURE};

    #[test]
    fn codes_round_trip_and_every_outside_code_is_refused() {
        for (position, quality) in ALL.into_iter().enumerate() {
            assert_eq!(usize::from(quality.code()), position);
            assert_eq!(from_code(quality.code(), 0), Ok(quality));
        }
        for code in 8u8..=u8::MAX {
            assert_eq!(
                from_code(code, 3),
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
    fn the_reexport_is_the_fixture_bound_model_table() {
        assert!(VOCABULARY_FIXTURE.contains("\"case\": \"quality-vocabulary\""));
        for quality in ALL {
            assert_eq!(quality.to_string(), quality.name());
        }
        assert_eq!(ALL.len(), 8, "the vocabulary is closed at eight codes");
    }
}
