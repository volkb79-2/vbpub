//! Counter classification and rate derivation over the ACTUAL monotonic interval
//! (plan v4 §C.3).
//!
//! # Contracts
//!
//! - **Counters are stored raw `u64`; only this module turns two raw readings into a
//!   rate**, and the derived value is an `f64` (plan v4 §B.2: counters `u64`, derived
//!   values `f64`). The raw difference stays available as an integer.
//! - **The interval is the real one between the two readings**, from a monotonic clock
//!   supplied by the caller. No wall clock is ever used: a wall-clock `dt` goes
//!   backwards when NTP steps it. A missed sample does not shift its difference into the
//!   last nominal bucket — the divisor is the whole elapsed time, so a reading that
//!   arrives after a gap yields the average over that gap, and the number of missed
//!   nominal samples is *reported*, never guessed.
//! - **A zero-length interval yields no rate** ([`NoRate::ZeroInterval`]). There is no
//!   "instantaneous rate" of a point in time.
//! - **A backwards interval yields no rate** ([`NoRate::NegativeInterval`]). It is not
//!   clamped to a tiny positive number and the previous value is not carried over.
//! - **A counter that went backwards is classified, never smoothed**
//!   ([`NoRate::CounterReset`]): the current reading is the new baseline and no delta of
//!   either sign is reported. This is the sampler's rule (`topos/sampler/src/collect.rs`
//!   `Em::counter`: *"it is never turned into a spike"*).
//! - **Missing data propagates its own quality code** ([`NoRate::Missing`] carries the
//!   sample's code), and it never becomes a zero reading. SP-2's success criterion is
//!   exactly this: *no lifetime joins, no invented zeros or rates.*
//! - **No rate ever crosses a reset, a refusal or a gap.**
//! - **Wrap is not inferred.** A counter width is a kernel fact this crate does not read;
//!   without a declared width a backwards reading cannot be distinguished from a wrap, so
//!   it is classified as a reset. A declared-width wrap policy is backlog entry `HOB-1`;
//!   adding it later means adding an arm here, not changing this contract.
//!
//! The checks run in a fixed order and each one is documented at its arm: a missing
//! reading first (the pair is incomplete), then the interval (the pair has no usable
//! spacing), then the reset (the pair's order is impossible for a cumulative counter).

use core::fmt;

use crate::quality::Quality;
use crate::value::Sample;

/// A monotonic instant in nanoseconds (CLOCK_MONOTONIC, plan v4 §B.2 `mono_*_ns`).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct MonoTime(u64);

impl MonoTime {
    /// Wrap a monotonic reading taken from `clock_gettime(CLOCK_MONOTONIC)`.
    #[must_use]
    pub const fn from_nanos(nanos: u64) -> MonoTime {
        MonoTime(nanos)
    }

    /// The underlying nanoseconds.
    #[must_use]
    pub const fn nanos(self) -> u64 {
        self.0
    }

    /// The signed difference `self - earlier`; negative when `self` is earlier.
    #[must_use]
    pub const fn delta(self, earlier: MonoTime) -> i128 {
        self.0 as i128 - earlier.0 as i128
    }
}

impl fmt::Display for MonoTime {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}ns", self.0)
    }
}

/// The derived value of one counter over one real interval.
#[derive(Clone, Copy, Debug)]
pub struct Rate {
    delta: u64,
    elapsed_nanos: u64,
    missed_samples: u32,
}

impl Rate {
    /// The raw difference of the two readings (always non-negative).
    #[must_use]
    pub const fn delta(self) -> u64 {
        self.delta
    }

    /// The real interval the difference was measured over, in nanoseconds (never zero).
    #[must_use]
    pub const fn elapsed_nanos(self) -> u64 {
        self.elapsed_nanos
    }

    /// The difference per second.
    ///
    /// The elapsed time is non-zero by construction; `f64` loses integer precision above
    /// 2^53, which is why the raw difference is also available as [`Rate::delta`].
    #[must_use]
    pub fn per_second(self) -> f64 {
        self.delta as f64 * 1_000_000_000.0 / self.elapsed_nanos as f64
    }

    /// The part of the difference that one nominal sampling interval carries
    /// (`delta * nominal / elapsed`).
    ///
    /// `None` when the nominal interval is zero: a zero-sized bucket has no share.
    #[must_use]
    pub fn per_interval(self, nominal_interval_nanos: u64) -> Option<f64> {
        if nominal_interval_nanos == 0 {
            return None;
        }
        Some(self.delta as f64 * nominal_interval_nanos as f64 / self.elapsed_nanos as f64)
    }

    /// How many nominal intervals the real interval spans beyond one: 0 when the readings
    /// are one interval apart, and a larger number when a sample was missed.
    #[must_use]
    pub const fn missed_samples(self) -> u32 {
        self.missed_samples
    }
}

/// Why a rate could not be derived.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NoRate {
    /// One of the readings is not a measurement; the payload is that reading's own
    /// quality code, so a consumer sees the actual reason (`absent`, `unreadable`, …).
    Missing(Quality),
    /// A decrease of a cumulative counter: the current reading is the new baseline.
    CounterReset,
    /// The two readings were taken at the same instant.
    ZeroInterval,
    /// The later reading is earlier than the earlier one.
    NegativeInterval,
    /// A reading is a measurement but not a `u64` counter (a caller defect).
    WrongValueType,
}

impl NoRate {
    /// The quality code a consumer stores for this outcome.
    ///
    /// A missing sample keeps its own code; a reset is [`Quality::Reset`]; a zero or
    /// reversed interval is [`Quality::NotSampled`] and [`Quality::Stale`] (the sample
    /// the pair would have been was not taken, or is out of order); a value of the wrong
    /// type is [`Quality::Unsupported`] — the caller asked for a rate of something that
    /// is not a counter, which is visible in the quality column rather than a panic.
    #[must_use]
    pub const fn quality(self) -> Quality {
        match self {
            NoRate::Missing(quality) => quality,
            NoRate::CounterReset => Quality::Reset,
            NoRate::ZeroInterval => Quality::NotSampled,
            NoRate::NegativeInterval => Quality::Stale,
            NoRate::WrongValueType => Quality::Unsupported,
        }
    }
}

impl fmt::Display for NoRate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NoRate::Missing(quality) => write!(f, "reading missing: {quality}"),
            NoRate::CounterReset => f.write_str("counter reset"),
            NoRate::ZeroInterval => f.write_str("zero interval"),
            NoRate::NegativeInterval => f.write_str("interval went backwards"),
            NoRate::WrongValueType => f.write_str("value is not a counter"),
        }
    }
}

/// Which of the two readings was unusable.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Reading {
    /// The earlier reading of the pair.
    Previous,
    /// The later reading of the pair.
    Current,
}

/// The outcome of classifying one pair of readings.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Derivation {
    /// A rate over the real interval.
    Rate(Rate),
    /// No rate, with the reason and which reading caused it (`None` for an interval
    /// problem, where both readings are present but their spacing is unusable).
    NoRate {
        /// Why.
        reason: NoRate,
        /// Which reading was unusable.
        reading: Option<Reading>,
    },
}

impl Derivation {
    /// The rate, if there is one.
    #[must_use]
    pub fn rate(&self) -> Option<Rate> {
        match self {
            Derivation::Rate(rate) => Some(*rate),
            Derivation::NoRate { .. } => None,
        }
    }

    /// True when this outcome carries a real rate.
    #[must_use]
    pub fn is_rate(&self) -> bool {
        matches!(self, Derivation::Rate(_))
    }

    /// The reason, when there is no rate.
    #[must_use]
    pub fn reason(&self) -> Option<NoRate> {
        match self {
            Derivation::Rate(_) => None,
            Derivation::NoRate { reason, .. } => Some(*reason),
        }
    }

    /// The quality code to store for this outcome: [`Quality::Ok`] for a rate.
    #[must_use]
    pub fn quality(&self) -> Quality {
        match self {
            Derivation::Rate(_) => Quality::Ok,
            Derivation::NoRate { reason, .. } => reason.quality(),
        }
    }
}

impl PartialEq for Rate {
    fn eq(&self, other: &Rate) -> bool {
        self.delta == other.delta
            && self.elapsed_nanos == other.elapsed_nanos
            && self.missed_samples == other.missed_samples
    }
}

/// Derive the rate of a counter between two readings taken at two monotonic instants.
///
/// `previous` is the earlier reading at `previous_at`, `current` the later one at
/// `current_at`. `nominal_interval_nanos` is the sampling interval the caller declared;
/// it only *reports* the number of missed samples and never divides a difference
/// computed from a different interval.
///
/// # Contract (plan v4 §C.3)
///
/// The divisor is the **actual** interval `current_at - previous_at`. A missed sample
/// widens the interval and yields the average over the whole gap. `dt = 0` yields no
/// rate, `dt < 0` yields no rate, a decrease of the raw counter yields
/// [`NoRate::CounterReset`] (the current reading is the new baseline), and a missing
/// sample yields [`NoRate::Missing`] carrying its own quality code.
#[must_use]
pub fn derive_rate(
    previous: Sample,
    previous_at: MonoTime,
    current: Sample,
    current_at: MonoTime,
    nominal_interval_nanos: u64,
) -> Derivation {
    let Some(raw_previous) = counter_reading(previous) else {
        return missing_rate(previous, Reading::Previous);
    };
    let Some(raw_current) = counter_reading(current) else {
        return missing_rate(current, Reading::Current);
    };
    if current_at < previous_at {
        return Derivation::NoRate {
            reason: NoRate::NegativeInterval,
            reading: None,
        };
    }
    if current_at == previous_at {
        return Derivation::NoRate {
            reason: NoRate::ZeroInterval,
            reading: None,
        };
    }
    // Both readings are u64 nanoseconds and the later one is not earlier, so this
    // subtraction is exactly the interval and cannot underflow.
    let elapsed_nanos = current_at.nanos() - previous_at.nanos();
    if raw_current < raw_previous {
        return Derivation::NoRate {
            reason: NoRate::CounterReset,
            reading: Some(Reading::Current),
        };
    }
    let delta = raw_current - raw_previous;
    Derivation::Rate(Rate {
        delta,
        elapsed_nanos,
        missed_samples: missed_samples(elapsed_nanos, nominal_interval_nanos),
    })
}

/// A `u64` counter reading, or `None` when the sample is not one.
const fn counter_reading(sample: Sample) -> Option<u64> {
    if sample.quality().is_measurement() {
        sample.u64()
    } else {
        None
    }
}

fn missing_rate(sample: Sample, reading: Reading) -> Derivation {
    let reason = if sample.quality().is_measurement() {
        NoRate::WrongValueType
    } else {
        NoRate::Missing(sample.quality())
    };
    Derivation::NoRate {
        reason,
        reading: Some(reading),
    }
}

/// How many nominal intervals the real interval spans beyond one
/// (`floor(elapsed / nominal) - 1`), saturated at `u32::MAX`. A nominal interval of 0
/// cannot report gaps and returns 0.
#[must_use]
pub fn missed_samples(elapsed_nanos: u64, nominal_interval_nanos: u64) -> u32 {
    if nominal_interval_nanos == 0 {
        return 0;
    }
    let intervals = elapsed_nanos / nominal_interval_nanos;
    u32::try_from(intervals.saturating_sub(1)).unwrap_or(u32::MAX)
}

#[cfg(test)]
mod tests {
    use super::{derive_rate, missed_samples, Derivation, MonoTime, NoRate, Rate, Reading};
    use crate::quality::{Quality, ALL};
    use crate::value::{Sample, Value};

    const S: u64 = 1_000_000_000;
    const NOMINAL: u64 = S;

    fn reading(value: u64) -> Sample {
        Sample::measured(Value::U64(value))
    }

    fn at(nanos: u64) -> MonoTime {
        MonoTime::from_nanos(nanos)
    }

    fn rate_of(previous: u64, current: u64, dt: u64) -> Rate {
        derive_rate(
            reading(previous),
            at(10 * S),
            reading(current),
            at(10 * S + dt),
            NOMINAL,
        )
        .rate()
        .expect("a rate")
    }

    #[test]
    fn a_one_second_interval_over_the_real_interval_is_a_per_second_delta() {
        let rate = rate_of(1000, 1500, S);
        assert_eq!(rate.delta(), 500);
        assert_eq!(rate.elapsed_nanos(), S);
        assert_eq!(rate.per_second(), 500.0);
        assert_eq!(rate.missed_samples(), 0);
        assert_eq!(rate.per_interval(S), Some(500.0));
        assert_eq!(rate.per_interval(0), None);
        assert_eq!(rate, rate_of(1000, 1500, S));
    }

    #[test]
    fn a_missed_sample_widens_the_interval_instead_of_inflating_one_bucket() {
        let rate = rate_of(0, 3000, 3 * S);
        assert_eq!(rate.delta(), 3000);
        assert_eq!(rate.elapsed_nanos(), 3 * S);
        assert_eq!(rate.per_second(), 1000.0, "the average over the real gap");
        assert_eq!(rate.missed_samples(), 2, "two nominal samples were skipped");
        assert_eq!(
            rate.per_interval(S),
            Some(1000.0),
            "one nominal bucket's share"
        );
    }

    #[test]
    fn a_sub_nominal_interval_does_not_report_missed_samples_and_keeps_its_own_divisor() {
        let rate = rate_of(0, 100, S / 4);
        assert_eq!(rate.elapsed_nanos(), S / 4);
        assert_eq!(rate.per_second(), 400.0, "the actual dt is the divisor");
        assert_eq!(
            rate.missed_samples(),
            0,
            "a shorter interval is not a missed sample"
        );
        assert_eq!(rate.per_interval(S), Some(400.0));
    }

    #[test]
    fn a_zero_interval_yields_no_rate_at_all() {
        assert_eq!(
            derive_rate(reading(5), at(7), reading(9), at(7), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::ZeroInterval,
                reading: None
            }
        );
        assert_eq!(NoRate::ZeroInterval.quality(), Quality::NotSampled);
    }

    #[test]
    fn a_backwards_interval_is_refused_not_clamped() {
        assert_eq!(
            derive_rate(reading(5), at(10), reading(9), at(9), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::NegativeInterval,
                reading: None
            }
        );
        assert_eq!(
            derive_rate(reading(5), at(u64::MAX), reading(9), at(0), NOMINAL).rate(),
            None,
            "a huge backwards step is still a backwards step"
        );
        assert_eq!(NoRate::NegativeInterval.quality(), Quality::Stale);
    }

    #[test]
    fn a_counter_that_went_backwards_is_a_reset_and_not_a_negative_delta() {
        let outcome = derive_rate(reading(100), at(10), reading(7), at(11), NOMINAL);
        assert_eq!(
            outcome,
            Derivation::NoRate {
                reason: NoRate::CounterReset,
                reading: Some(Reading::Current)
            }
        );
        assert!(outcome.rate().is_none());
        assert_eq!(outcome.reason(), Some(NoRate::CounterReset));
        assert_eq!(outcome.quality(), Quality::Reset);
        assert_eq!(NoRate::CounterReset.to_string(), "counter reset");
    }

    #[test]
    fn an_equal_reading_is_a_real_zero_delta_and_not_an_absence() {
        let rate = rate_of(42, 42, S);
        assert_eq!(rate.delta(), 0);
        assert_eq!(
            rate.per_second(),
            0.0,
            "a zero is a measurement (plan §C.2)"
        );
        assert!(derive_rate(reading(42), at(1), reading(42), at(2), NOMINAL).is_rate());
        assert_eq!(
            Derivation::NoRate {
                reason: NoRate::CounterReset,
                reading: None
            }
            .quality(),
            Quality::Reset
        );
        assert_eq!(Derivation::Rate(rate).quality(), Quality::Ok);
    }

    #[test]
    fn the_counter_boundaries_classify_exactly() {
        // No decrease is a rate, including the u64 extremes.
        assert_eq!(rate_of(0, u64::MAX, S).delta(), u64::MAX);
        assert_eq!(rate_of(u64::MAX, u64::MAX, S).delta(), 0);
        assert_eq!(rate_of(0, 0, S).delta(), 0);
        // The smallest possible decrease is a reset, never a wrapping delta of ~2^64.
        assert_eq!(
            derive_rate(reading(1), at(1), reading(0), at(1 + S), NOMINAL).reason(),
            Some(NoRate::CounterReset)
        );
        // Two below the top is a real delta of exactly two.
        assert_eq!(rate_of(u64::MAX - 2, u64::MAX, S).delta(), 2);
        // From zero to the top is the largest representable delta.
        assert_eq!(rate_of(0, u64::MAX, S).per_second(), u64::MAX as f64);
        assert_eq!(
            derive_rate(reading(u64::MAX), at(1), reading(0), at(1 + S), NOMINAL).reason(),
            Some(NoRate::CounterReset)
        );
    }

    #[test]
    fn missing_readings_propagate_their_own_quality_code() {
        for quality in ALL {
            if quality == Quality::Ok {
                continue;
            }
            let missing = Sample::missing(quality).expect("absence");
            let previous_gap = derive_rate(missing, at(10), reading(5), at(11), NOMINAL);
            assert_eq!(
                previous_gap,
                Derivation::NoRate {
                    reason: NoRate::Missing(quality),
                    reading: Some(Reading::Previous)
                }
            );
            let current_gap = derive_rate(reading(5), at(10), missing, at(11), NOMINAL);
            assert_eq!(
                current_gap,
                Derivation::NoRate {
                    reason: NoRate::Missing(quality),
                    reading: Some(Reading::Current)
                }
            );
            assert!(!previous_gap.is_rate() && !current_gap.is_rate());
            assert_eq!(
                previous_gap.quality(),
                quality,
                "the code travels unchanged"
            );
            assert_eq!(NoRate::Missing(quality).quality(), quality);
        }
    }

    #[test]
    fn a_non_counter_sample_cannot_be_differenced() {
        let float = Sample::measured(Value::F64(1.5));
        assert_eq!(
            derive_rate(float, at(10), reading(3), at(11), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::WrongValueType,
                reading: Some(Reading::Previous)
            }
        );
        let signed = Sample::measured(Value::I64(-1));
        assert_eq!(
            derive_rate(reading(3), at(10), signed, at(11), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::WrongValueType,
                reading: Some(Reading::Current)
            }
        );
        assert_eq!(NoRate::WrongValueType.quality(), Quality::Unsupported);
    }

    #[test]
    fn an_absence_of_a_non_counter_type_is_still_an_absence() {
        let row = Sample::from_wire(Value::F64(9.0), Quality::Stale);
        assert_eq!(
            derive_rate(row, at(10), reading(3), at(11), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::Missing(Quality::Stale),
                reading: Some(Reading::Previous)
            }
        );
        assert_eq!(
            derive_rate(reading(3), at(10), row, at(11), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::Missing(Quality::Stale),
                reading: Some(Reading::Current)
            }
        );
    }

    #[test]
    fn the_reading_order_of_checks_is_missing_then_interval_then_reset() {
        // Both a missing current reading and a backwards interval: the reading wins, so a
        // consumer learns that a sample is absent rather than that clocks disagree.
        assert_eq!(
            derive_rate(
                reading(5),
                at(10),
                Sample::missing(Quality::Unreadable).expect("absence"),
                at(9),
                NOMINAL
            ),
            Derivation::NoRate {
                reason: NoRate::Missing(Quality::Unreadable),
                reading: Some(Reading::Current)
            }
        );
        // Both a backwards interval and a counter decrease: the interval wins.
        assert_eq!(
            derive_rate(reading(5), at(10), reading(3), at(9), NOMINAL),
            Derivation::NoRate {
                reason: NoRate::NegativeInterval,
                reading: None
            }
        );
    }

    #[test]
    fn missed_sample_counting_is_floor_division_and_saturates() {
        assert_eq!(missed_samples(0, S), 0);
        assert_eq!(missed_samples(S / 2, S), 0);
        assert_eq!(missed_samples(S, S), 0);
        assert_eq!(
            missed_samples(S + 1, S),
            0,
            "a part of a second interval is one interval"
        );
        assert_eq!(missed_samples(2 * S, S), 1);
        assert_eq!(missed_samples(10 * S, S), 9);
        assert_eq!(
            missed_samples(S, 0),
            0,
            "no nominal interval, no gap report"
        );
        assert_eq!(
            missed_samples(u64::MAX, 1),
            u32::MAX,
            "saturated, never wrapped"
        );
    }

    #[test]
    fn every_surface_of_a_derivation_is_reachable_and_typed() {
        // A rate has no reason, and a refusal is never a rate.
        let rate = rate_of(1, 2, S);
        let outcome = Derivation::Rate(rate);
        assert_eq!(outcome.reason(), None);
        assert_eq!(
            MonoTime::from_nanos(42).nanos(),
            42,
            "the monotonic instant reads back"
        );
        let refused = Derivation::NoRate {
            reason: NoRate::CounterReset,
            reading: None,
        };
        assert!(!refused.is_rate());

        // The widest possible interval is representable and still derived exactly.
        let widest = derive_rate(
            reading(0),
            at(0),
            reading(u64::MAX >> 1),
            at(u64::MAX),
            NOMINAL,
        )
        .rate()
        .expect("a rate over the widest interval");
        assert_eq!(widest.elapsed_nanos(), u64::MAX);
        assert!(widest.missed_samples() > 0);
    }

    #[test]
    fn every_rate_is_derived_from_the_actual_nanoseconds() {
        let rate = rate_of(1, 2, 3 * S);
        assert!((rate.per_second() - 0.333_333_333).abs() < 1e-6);
        let huge = rate_of(0, u64::MAX, 60 * 60 * S);
        assert!((huge.per_second() - (u64::MAX as f64 / 3600.0)).abs() < 1_000_000.0);
        assert_eq!(MonoTime::from_nanos(5).delta(MonoTime::from_nanos(9)), -4);
        assert_eq!(MonoTime::from_nanos(5).to_string(), "5ns");
        assert_eq!(
            NoRate::Missing(Quality::Absent).to_string(),
            "reading missing: absent"
        );
        assert_eq!(NoRate::WrongValueType.to_string(), "value is not a counter");
        assert_eq!(
            NoRate::NegativeInterval.to_string(),
            "interval went backwards"
        );
        assert_eq!(NoRate::ZeroInterval.to_string(), "zero interval");
    }
}
