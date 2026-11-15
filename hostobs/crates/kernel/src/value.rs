//! A raw reading and its quality: the outcome every collector returns.
//!
//! Plan v4 §C.2: *"Every value = (inc, metric, raw value, quality, interval). Zero is a
//! value; absence is never zero."* This module owns the single-value half of that: a
//! [`Reading`] is a typed raw value plus one closed quality code, and it cannot be built
//! as "a measurement with no value" or "an absence that looks measured".
//!
//! # Contracts
//!
//! - [`Reading::measured`] is the only constructor of a value whose quality is
//!   [`QualityCode::OK`]; [`Reading::missing`] refuses that code. A collector that cannot
//!   read a fact therefore *cannot* return a zero with quality `ok`.
//! - Accessors are typed strictly ([`Reading::u64`], [`Reading::i64`], [`Reading::f64`]):
//!   each answers only for its own value type and only for a measurement, so a `u64` is
//!   never silently routed through `f64` (integers above 2^53 would lose precision, §B.1).
//! - Equality is bit-exact, because the value travelling to the store and the wire is bits
//!   (a `-0.0` and a `0.0` differ; a NaN equals itself).

use core::fmt;

use crate::quality::{QualityCode, QualityError};

/// The typed raw value of one reading (the three wire value types of plan v4 §B.2).
#[derive(Clone, Copy, Debug)]
pub enum Value {
    /// A raw `u64` (every counter, §C.3).
    U64(u64),
    /// A raw `i64` (a negative gauge or a delta).
    I64(i64),
    /// A raw `f64` (a fraction, a ratio, a derived rate).
    F64(f64),
}

impl Value {
    /// Type-and-bits equality.
    #[must_use]
    pub fn same_bits(self, other: Value) -> bool {
        match (self, other) {
            (Value::U64(left), Value::U64(right)) => left == right,
            (Value::I64(left), Value::I64(right)) => left == right,
            (Value::F64(left), Value::F64(right)) => left.to_bits() == right.to_bits(),
            _ => false,
        }
    }

    /// The stored quality code a value type maps onto the wire's type code.
    #[must_use]
    pub const fn type_code(self) -> u8 {
        match self {
            Value::U64(_) => 1,
            Value::I64(_) => 2,
            Value::F64(_) => 3,
        }
    }
}

impl PartialEq for Value {
    fn eq(&self, other: &Value) -> bool {
        self.same_bits(*other)
    }
}

/// Why an absence could not be constructed.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReadingError {
    /// [`QualityCode::OK`] names a measurement, so it cannot describe an absence.
    MissingCannotBeOk,
    /// The code is outside the closed vocabulary.
    UnknownCode(QualityError),
}

impl fmt::Display for ReadingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ReadingError::MissingCannotBeOk => {
                f.write_str("quality `ok` marks a measurement, not a missing reading")
            }
            ReadingError::UnknownCode(error) => write!(f, "{error}"),
        }
    }
}

impl core::error::Error for ReadingError {}

/// One value: a raw reading plus the quality that says whether to believe it.
#[derive(Clone, Copy, Debug)]
pub struct Reading {
    value: Value,
    quality: QualityCode,
}

impl Reading {
    /// A real measurement.
    #[must_use]
    pub const fn measured(value: Value) -> Reading {
        Reading {
            value,
            quality: QualityCode::OK,
        }
    }

    /// An absence with its reason.
    ///
    /// # Errors
    ///
    /// [`ReadingError::MissingCannotBeOk`] for [`QualityCode::OK`]: that code means the
    /// value *was* measured.
    pub const fn missing(quality: QualityCode) -> Result<Reading, ReadingError> {
        if quality.is_measurement() {
            return Err(ReadingError::MissingCannotBeOk);
        }
        Ok(Reading {
            value: Value::U64(0),
            quality,
        })
    }

    /// A row exactly as a tier or a column carries it: a value and a quality byte.
    #[must_use]
    pub const fn from_raw(value: Value, quality: QualityCode) -> Reading {
        Reading { value, quality }
    }

    /// The quality code.
    #[must_use]
    pub const fn quality(&self) -> QualityCode {
        self.quality
    }

    /// The raw value, or `None` when this reading is not a measurement.
    #[must_use]
    pub const fn value(&self) -> Option<Value> {
        if self.quality.is_measurement() {
            Some(self.value)
        } else {
            None
        }
    }

    /// The reading as a `u64`, only for a measured `u64`.
    #[must_use]
    pub const fn u64(&self) -> Option<u64> {
        match self.value {
            Value::U64(raw) if self.quality.is_measurement() => Some(raw),
            Value::U64(_) | Value::I64(_) | Value::F64(_) => None,
        }
    }

    /// The reading as an `i64`, only for a measured `i64`.
    #[must_use]
    pub const fn i64(&self) -> Option<i64> {
        match self.value {
            Value::I64(raw) if self.quality.is_measurement() => Some(raw),
            Value::U64(_) | Value::I64(_) | Value::F64(_) => None,
        }
    }

    /// The reading as an `f64`, only for a measured `f64` (never converted).
    #[must_use]
    pub const fn f64(&self) -> Option<f64> {
        match self.value {
            Value::F64(raw) if self.quality.is_measurement() => Some(raw),
            Value::U64(_) | Value::I64(_) | Value::F64(_) => None,
        }
    }

    /// The placeholder bytes of an absence (for a caller that must write a row anyway).
    #[must_use]
    pub const fn placeholder(&self) -> Option<Value> {
        if self.quality.is_measurement() {
            None
        } else {
            Some(self.value)
        }
    }
}

impl PartialEq for Reading {
    fn eq(&self, other: &Reading) -> bool {
        self.quality == other.quality && self.value.same_bits(other.value)
    }
}

#[cfg(test)]
mod tests {
    use super::{Reading, ReadingError, Value};
    use crate::quality::QualityCode;

    #[test]
    fn a_measurement_reads_back_and_an_absence_never_does() {
        let measured = Reading::measured(Value::U64(7));
        assert_eq!(measured.quality(), QualityCode::OK);
        assert_eq!(measured.u64(), Some(7));
        assert_eq!(measured.f64(), None, "a u64 is never cast to f64");
        assert_eq!(measured.placeholder(), None);

        let absent = Reading::missing(QualityCode::ABSENT).expect("absent is a valid absence");
        assert_eq!(absent.quality(), QualityCode::ABSENT);
        assert_eq!(absent.value(), None);
        assert_eq!(
            absent.u64(),
            None,
            "the placeholder zero is never a reading"
        );
        assert_eq!(absent.placeholder(), Some(Value::U64(0)));
    }

    #[test]
    fn ok_cannot_describe_an_absence() {
        assert_eq!(
            Reading::missing(QualityCode::OK),
            Err(ReadingError::MissingCannotBeOk)
        );
        for code in 1u8..=7 {
            let quality = QualityCode::from_code(code).expect("in the vocabulary");
            let reading = Reading::missing(quality).expect("a non-ok code is an absence");
            assert_eq!(reading.quality(), quality);
            assert_eq!(reading.value(), None);
        }
        assert!(ReadingError::MissingCannotBeOk
            .to_string()
            .contains("not a missing reading"));
        assert_eq!(
            ReadingError::UnknownCode(crate::quality::QualityError::UnknownCode(9)).to_string(),
            "unknown quality code 9"
        );
    }

    #[test]
    fn typed_accessors_never_convert_between_value_types() {
        let negative = Reading::measured(Value::I64(-3));
        assert_eq!(negative.i64(), Some(-3));
        assert_eq!(negative.u64(), None);
        assert_eq!(negative.f64(), None);

        let real = Reading::measured(Value::F64(0.25));
        assert_eq!(real.f64(), Some(0.25));
        assert_eq!(real.u64(), None);

        let big = Reading::measured(Value::U64(9_007_199_254_740_993));
        assert_eq!(
            big.f64(),
            None,
            "2^53+1 is not representable as f64 and is never converted silently"
        );
        assert_eq!(big.u64(), Some(9_007_199_254_740_993));
    }

    #[test]
    fn equality_is_bit_exact_and_type_sensitive() {
        assert_eq!(
            Reading::measured(Value::F64(f64::NAN)),
            Reading::measured(Value::F64(f64::NAN))
        );
        assert_ne!(
            Reading::measured(Value::F64(0.0)),
            Reading::measured(Value::F64(-0.0))
        );
        assert_ne!(
            Reading::measured(Value::U64(1)),
            Reading::measured(Value::I64(1))
        );
        assert_ne!(
            Reading::from_raw(Value::U64(5), QualityCode::STALE),
            Reading::from_raw(Value::U64(5), QualityCode::OK)
        );
        assert_eq!(Value::U64(1).type_code(), 1);
        assert_eq!(Value::I64(1).type_code(), 2);
        assert_eq!(Value::F64(1.0).type_code(), 3);
    }
}
