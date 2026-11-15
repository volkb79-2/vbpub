//! The hand-computed rate oracles of plan v4 §F.3 SP-2, replayed by the model crate.
//!
//! `fixtures/rates/rates.json` was written by hand (`python/reference/make_rate_fixtures.py`
//! spells out the arithmetic in each case's `doc`) and is asserted here **and** by the
//! independent Python reference (`python/reference/hostobs_rate_ref.py`). Rust and Python
//! agreeing with arithmetic, rather than with each other's code, is what SP-2 asks for.
//!
//! The comparison is exact: a rate either has the hand-computed `delta`, denominator and
//! `per_second`, or it is refused with the same reason and the same offending reading.

#[path = "../../../test-support/json.rs"]
mod json;

use hostobs_model::quality::Quality;
use hostobs_model::rate::{derive_rate, Derivation, MonoTime, NoRate, Reading};
use hostobs_model::value::{Sample, Value};
use json::Json;

const FIXTURE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../fixtures/rates/rates.json"
);

fn load() -> Json {
    let text =
        std::fs::read_to_string(FIXTURE).unwrap_or_else(|error| panic!("read {FIXTURE}: {error}"));
    json::parse(text.as_bytes()).unwrap_or_else(|error| panic!("parse {FIXTURE}: {error:?}"))
}

fn quality(name: &str) -> Quality {
    Quality::from_name(name)
        .unwrap_or_else(|| panic!("quality {name} is not in the closed vocabulary"))
}

fn sample(spec: &Json) -> (Sample, MonoTime) {
    let quality = quality(spec.field("quality").as_str().expect("quality"));
    let mono = MonoTime::from_nanos(spec.field("mono_ns").as_u64().expect("mono_ns"));
    let kind = spec.field("type").as_str().unwrap_or("u64");
    let value = match kind {
        "u64" => spec
            .get("value")
            .map(|value| Value::U64(value.as_u64().expect("u64 value")))
            .unwrap_or(Value::U64(0)),
        "i64" => spec
            .get("value")
            .map(|value| {
                Value::I64(i64::try_from(value.as_i128().expect("i64 value")).expect("i64 value"))
            })
            .unwrap_or(Value::I64(0)),
        "f64" => Value::F64(1.5),
        other => panic!("unknown sample type {other}"),
    };
    if quality.is_measurement() {
        (Sample::measured(value), mono)
    } else {
        (Sample::missing(quality).expect("absence"), mono)
    }
}

fn reason_name(reason: NoRate) -> &'static str {
    match reason {
        NoRate::Missing(_) => "missing",
        NoRate::CounterReset => "counter-reset",
        NoRate::ZeroInterval => "zero-interval",
        NoRate::NegativeInterval => "negative-interval",
        NoRate::WrongValueType => "wrong-value-type",
    }
}

fn reading_name(reading: Option<Reading>) -> Option<&'static str> {
    match reading {
        Some(Reading::Previous) => Some("previous"),
        Some(Reading::Current) => Some("current"),
        None => None,
    }
}

#[test]
fn every_hand_computed_case_is_reproduced_exactly() {
    let document = load();
    let cases = document.field("cases").as_array().expect("cases");
    assert!(
        cases.len() >= 18,
        "the oracle set keeps its size: {}",
        cases.len()
    );
    let mut rates = 0;
    let mut refusals = 0;
    for case in cases {
        let name = case.field("case").as_str().expect("case name");
        let nominal = case
            .field("nominal_interval_ns")
            .as_u64()
            .expect("nominal interval");
        let (previous, previous_at) = sample(case.field("previous"));
        let (current, current_at) = sample(case.field("current"));
        let outcome = derive_rate(previous, previous_at, current, current_at, nominal);
        if let Some(expected) = case.get("expect") {
            let rate = outcome
                .rate()
                .unwrap_or_else(|| panic!("{name}: expected a rate, got {outcome:?}"));
            assert_eq!(
                u128::from(rate.delta()),
                expected.field("delta").as_i128().expect("delta") as u128,
                "{name}: delta"
            );
            assert_eq!(
                rate.elapsed_nanos(),
                expected.field("denominator").as_u64().expect("denominator"),
                "{name}: denominator"
            );
            if let Some(per_second) = expected.get("per_second") {
                let expected_rate = per_second.as_i128().expect("per_second") as f64;
                assert!(
                    (rate.per_second() - expected_rate).abs()
                        <= 1e-9 * expected_rate.abs().max(1.0),
                    "{name}: per_second {} != {expected_rate}",
                    rate.per_second()
                );
            }
            assert_eq!(
                u64::from(rate.missed_samples()),
                expected.field("missed").as_u64().expect("missed"),
                "{name}: missed"
            );
            rates += 1;
        } else {
            let expected = case.field("expect_refusal");
            let reason = outcome
                .reason()
                .unwrap_or_else(|| panic!("{name}: expected a refusal, got {outcome:?}"));
            assert_eq!(
                reason_name(reason),
                expected.field("reason").as_str().expect("reason"),
                "{name}: refusal reason"
            );
            let expected_reading = match expected.get("reading") {
                Some(Json::String(text)) => Some(text.as_str()),
                Some(Json::Null) | None => None,
                other => panic!("{name}: unexpected reading field {other:?}"),
            };
            let actual_reading = match &outcome {
                Derivation::NoRate { reading, .. } => reading_name(*reading),
                Derivation::Rate(_) => None,
            };
            assert_eq!(
                actual_reading, expected_reading,
                "{name}: offending reading"
            );
            // The quality a consumer would store is the vocabulary code of the reason.
            if let NoRate::Missing(code) = reason {
                assert_eq!(
                    outcome.quality(),
                    code,
                    "{name}: a missing reading keeps its own code, not a generic one"
                );
            }
            refusals += 1;
        }
    }
    assert_eq!(rates, 8, "the oracle set has eight rates");
    assert_eq!(refusals, 10, "the oracle set has ten refusals");
}

#[test]
fn a_case_that_would_pass_under_a_different_rule_is_caught() {
    // Guard the guard: if the three-second gap were divided by the nominal interval
    // instead of the actual one, this case would report 3000/s rather than 1000/s.
    let document = load();
    let cases = document.field("cases").as_array().expect("cases");
    let gap = cases
        .iter()
        .find(|case| case.field("case").as_str() == Some("three-second-gap-is-an-average"))
        .expect("the gap case exists");
    let (previous, previous_at) = sample(gap.field("previous"));
    let (current, current_at) = sample(gap.field("current"));
    let nominal = gap.field("nominal_interval_ns").as_u64().expect("nominal");
    let rate = derive_rate(previous, previous_at, current, current_at, nominal)
        .rate()
        .expect("a rate");
    assert_eq!(rate.per_second(), 1000.0);
    assert_ne!(
        rate.per_second(),
        3000.0,
        "the delta is not charged to one nominal bucket"
    );
    assert_eq!(
        rate.per_interval(nominal),
        Some(1000.0),
        "one nominal bucket carries its own share, not the whole gap"
    );
}
