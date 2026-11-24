//! A value and its quality: the pair every hostobs tier, reply and segment carries.
//!
//! Plan v4 §C.2: *"Every value = (inc, metric, raw value, quality, interval). Zero is a
//! value; absence is never zero."* This module owns the second half of that sentence at
//! the single-value level: a [`Sample`] pairs a typed raw reading with a [`Quality`] code
//! and refuses, by construction, to let an absence look like a reading.
//!
//! The three raw value types are the ones the column codec stores (plan v4 §B.2):
//! `u64` for counters, `i64` for signed gauges, `f64` for derived and fractional values.
//!
//! # Contracts
//!
//! - A measured sample is the only sample whose reading is meaningful. [`Sample::measured`]
//!   is the only constructor that produces one, and [`Sample::missing`] refuses
//!   [`Quality::Ok`].
//! - A missing sample still needs a placeholder on the wire and on disk (the column
//!   stores eight bytes per row whatever the quality says). [`Sample::from_wire`]
//!   accepts such a row and the accessors keep treating it as an absence, so a consumer
//!   reading through this type can never see the placeholder.
//! - Accessors are typed strictly: [`Sample::u64`] answers only for a measured `u64`
//!   reading, never by converting. Casting `u64` to `f64` above 2<sup>53</sup> loses
//!   integers, which plan v4 §B.1 refuses; a caller that wants a float says so.

use crate::quality::Quality;

/// The typed raw reading of one value, matching the column codec's value types.
#[derive(Clone, Copy, Debug)]
pub enum Value {
    /// A raw `u64`: every counter in hostobs is stored this way (plan v4 §C.3).
    U64(u64),
    /// A raw `i64`, for signed gauges.
    I64(i64),
    /// A raw `f64`, for values that are not integers by nature.
    F64(f64),
}

impl Value {
    /// Values are compared **bit-exactly**: `NaN` equals itself and `-0.0` is distinct
    /// from `0.0`, because the codec round-trips bits, not numbers.
    #[must_use]
    pub fn same_bits(self, other: Value) -> bool {
        match (self, other) {
            (Value::U64(a), Value::U64(b)) => a == b,
            (Value::I64(a), Value::I64(b)) => a == b,
            (Value::F64(a), Value::F64(b)) => a.to_bits() == b.to_bits(),
            _ => false,
        }
    }

    /// The column value-type byte for this variant (plan v4 §B.2).
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

/// Why a sample could not be created as an absence.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SampleError {
    /// [`Quality::Ok`] names a measurement, so it cannot describe an absence.
    MissingCannotBeOk,
}

impl core::fmt::Display for SampleError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            SampleError::MissingCannotBeOk => {
                f.write_str("quality `ok` marks a measurement, not a missing reading")
            }
        }
    }
}

impl core::error::Error for SampleError {}

/// One value: a raw reading plus the quality that says whether to believe it.
#[derive(Clone, Copy, Debug)]
pub struct Sample {
    value: Value,
    quality: Quality,
}

impl Sample {
    /// A real measurement.
    #[must_use]
    pub const fn measured(value: Value) -> Sample {
        Sample {
            value,
            quality: Quality::Ok,
        }
    }

    /// An absence with its reason.
    ///
    /// # Errors
    ///
    /// [`SampleError::MissingCannotBeOk`] when `quality` is [`Quality::Ok`]: that code
    /// means the value *was* measured.
    pub const fn missing(quality: Quality) -> Result<Sample, SampleError> {
        if quality.is_measurement() {
            return Err(SampleError::MissingCannotBeOk);
        }
        Ok(Sample {
            value: Value::U64(0),
            quality,
        })
    }

    /// A row exactly as a column carries it: eight bytes of value and one quality byte.
    ///
    /// This is the decode path. A non-[`Quality::Ok`] quality makes the eight bytes a
    /// placeholder; the accessors below never return it.
    #[must_use]
    pub const fn from_wire(value: Value, quality: Quality) -> Sample {
        Sample { value, quality }
    }

    /// The collector-side name for [`Sample::from_wire`].
    ///
    /// A reader may already have a raw value and a quality byte; this constructor keeps
    /// those bytes without pretending the pair is a measurement when the quality says it
    /// is not.
    #[must_use]
    pub const fn from_raw(value: Value, quality: Quality) -> Sample {
        Sample::from_wire(value, quality)
    }

    /// The quality code of this value.
    #[must_use]
    pub const fn quality(&self) -> Quality {
        self.quality
    }

    /// The raw reading, or `None` when this value is not a measurement.
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

    /// The placeholder bytes of an absence (zero), for a caller that must write a column
    /// row anyway. `None` for a measurement, which has a real reading.
    #[must_use]
    pub const fn placeholder(&self) -> Option<Value> {
        if self.quality.is_measurement() {
            None
        } else {
            Some(self.value)
        }
    }
}

impl PartialEq for Sample {
    fn eq(&self, other: &Sample) -> bool {
        self.quality == other.quality && self.value.same_bits(other.value)
    }
}

#[cfg(test)]
mod tests {
    use super::{Sample, SampleError, Value};
    use crate::quality::{Quality, ALL};

    #[test]
    fn a_measurement_reads_back_and_a_missing_sample_does_not() {
        let measured = Sample::measured(Value::U64(7));
        assert_eq!(measured.quality(), Quality::Ok);
        assert_eq!(measured.u64(), Some(7));
        assert_eq!(measured.f64(), None, "a u64 is not silently cast to f64");
        assert_eq!(measured.i64(), None);
        assert_eq!(measured.placeholder(), None);

        let absent = Sample::missing(Quality::Absent).expect("absent is a valid absence");
        assert_eq!(absent.quality(), Quality::Absent);
        assert_eq!(
            absent.u64(),
            None,
            "the placeholder zero is never a reading"
        );
        assert_eq!(absent.f64(), None);
        assert_eq!(absent.value(), None);
        assert_eq!(absent.placeholder(), Some(Value::U64(0)));
    }

    #[test]
    fn every_non_ok_code_is_an_acceptable_absence_and_ok_is_not() {
        assert_eq!(
            Sample::missing(Quality::Ok),
            Err(SampleError::MissingCannotBeOk)
        );
        for quality in ALL {
            if quality == Quality::Ok {
                continue;
            }
            let sample = Sample::missing(quality).expect("non-ok codes are absences");
            assert_eq!(sample.quality(), quality);
            assert_eq!(sample.value(), None, "{quality}");
        }
        assert!(SampleError::MissingCannotBeOk.to_string().contains("ok"));
    }

    #[test]
    fn typed_accessors_never_convert_between_value_types() {
        let negative = Sample::measured(Value::I64(-3));
        assert_eq!(negative.i64(), Some(-3));
        assert_eq!(negative.u64(), None);
        assert_eq!(negative.f64(), None);

        let real = Sample::measured(Value::F64(0.25));
        assert_eq!(real.f64(), Some(0.25));
        assert_eq!(real.u64(), None);

        let big = Sample::measured(Value::U64(9_007_199_254_740_993));
        assert_eq!(big.u64(), Some(9_007_199_254_740_993));
        assert_eq!(big.f64(), None, "2^53+1 is not representable as f64");
    }

    #[test]
    fn a_wire_row_with_a_bad_quality_is_an_absence_holding_its_bytes() {
        let row = Sample::from_wire(Value::F64(12.5), Quality::Unreadable);
        assert_eq!(row.quality(), Quality::Unreadable);
        assert_eq!(row.f64(), None);
        assert_eq!(
            row.placeholder(),
            Some(Value::F64(12.5)),
            "the bytes travel, unread"
        );

        let good = Sample::from_wire(Value::U64(u64::MAX), Quality::Ok);
        assert_eq!(good.u64(), Some(u64::MAX));
    }

    #[test]
    fn a_measured_value_returns_its_reading_and_i64_values_compare() {
        let measured = Sample::measured(Value::U64(7));
        assert_eq!(measured.value(), Some(Value::U64(7)));
        assert_eq!(
            Sample::measured(Value::I64(-2)),
            Sample::measured(Value::I64(-2))
        );
        assert_ne!(
            Sample::measured(Value::I64(-2)),
            Sample::measured(Value::I64(2))
        );
    }

    #[test]
    fn equality_is_bit_exact_for_floats_and_type_sensitive() {
        assert_eq!(
            Sample::measured(Value::F64(f64::NAN)),
            Sample::measured(Value::F64(f64::NAN))
        );
        assert_ne!(
            Sample::measured(Value::F64(0.0)),
            Sample::measured(Value::F64(-0.0))
        );
        assert_ne!(
            Sample::measured(Value::U64(1)),
            Sample::measured(Value::I64(1))
        );
        assert_ne!(
            Sample::from_wire(Value::U64(5), Quality::Stale),
            Sample::from_wire(Value::U64(5), Quality::Ok),
            "the same bytes with a different quality are different values"
        );
        assert_eq!(Value::U64(1).type_code(), 1);
        assert_eq!(Value::I64(1).type_code(), 2);
        assert_eq!(Value::F64(1.0).type_code(), 3);
        assert_eq!(
            Sample::from_raw(Value::U64(5), Quality::Stale),
            Sample::from_wire(Value::U64(5), Quality::Stale)
        );
    }
}
