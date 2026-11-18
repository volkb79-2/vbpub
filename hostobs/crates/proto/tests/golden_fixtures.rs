//! The cross-language golden-fixture test (plan v4 §B.5).
//!
//! Every `fixtures/wire/<case>.bin` is canonical bytes; `<case>.json` is the decoded typed
//! content produced by the **independent Python implementation**
//! (`python/reference/hostobs_wire_ref.py`, written from the wire specification). This test
//! decodes the committed bytes with the Rust codec and asserts the values the JSON
//! declares, and for a refusal case asserts the same refusal identity the Python side
//! asserts. The Python reference codec (`python/reference/`) asserts the same files; that
//! run is a manual check until the registered `conformance` lane exists (`HOB-4`).
//!
//! A fixture that the Rust encoder reproduces is a second check: for every valid case the
//! Rust *encoder* must emit the identical bytes, so neither direction can drift alone.

// The fixture parser is shared by the model and proto test crates as source, not as a
// workspace member: adding a member would change `Cargo.lock`, and the lane image caches
// exactly the committed lock (a new dependency edge needs an image rebuild).
#[path = "../../../test-support/json.rs"]
mod json;

use std::collections::BTreeMap;

use hostobs_proto::dict::{
    Aggregation, Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit,
    MetricValueType,
};
use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::{
    decode_block, decode_block_with, encode_block, Block, BlockHeaderFacts, Column, FrameType, Row,
    TimeBucket, TypedValue,
};
use hostobs_proto::quality::{Quality, ALL as ALL_QUALITIES};
use json::Json;

const FIXTURE_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/wire");

fn load(name: &str) -> (Json, Vec<u8>) {
    let spec_path = format!("{FIXTURE_DIR}/{name}.json");
    let bin_path = format!("{FIXTURE_DIR}/{name}.bin");
    let spec_text =
        std::fs::read(&spec_path).unwrap_or_else(|error| panic!("read {spec_path}: {error}"));
    let spec =
        json::parse(&spec_text).unwrap_or_else(|error| panic!("parse {spec_path}: {error:?}"));
    let bytes = std::fs::read(&bin_path).unwrap_or_else(|error| panic!("read {bin_path}: {error}"));
    (spec, bytes)
}

fn fixture_names() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(FIXTURE_DIR)
        .unwrap_or_else(|error| panic!("read {FIXTURE_DIR}: {error}"))
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.ends_with(".json") && name != "quality.json")
        .map(|name| name.trim_end_matches(".json").to_string())
        .collect();
    names.sort();
    assert!(!names.is_empty(), "the fixture directory is empty");
    names
}

fn facts(json: &Json) -> MetricFacts {
    MetricFacts {
        unit: match json.field("unit").as_str().expect("unit") {
            "none" => MetricUnit::None,
            "usec" => MetricUnit::Microseconds,
            "msec" => MetricUnit::Milliseconds,
            "nsec" => MetricUnit::Nanoseconds,
            "bytes" => MetricUnit::Bytes,
            "count" => MetricUnit::Count,
            "ratio" => MetricUnit::Ratio,
            "volts" => MetricUnit::Volts,
            "celsius" => MetricUnit::Celsius,
            other => panic!("unknown unit {other}"),
        },
        kind: match json.field("kind").as_str().expect("kind") {
            "gauge" => MetricKind::Gauge,
            "counter" => MetricKind::Counter,
            "state" => MetricKind::State,
            other => panic!("unknown kind {other}"),
        },
        scope: match json.field("scope").as_str().expect("scope") {
            "host" => MetricScope::Host,
            "cgroup" => MetricScope::Cgroup,
            "process" => MetricScope::Process,
            "daemon" => MetricScope::Daemon,
            other => panic!("unknown scope {other}"),
        },
        source: match json.field("source").as_str().expect("source") {
            "cgroupfs" => MetricSource::CgroupFs,
            "procfs" => MetricSource::ProcFs,
            "sysfs" => MetricSource::SysFs,
            "probe" => MetricSource::Probe,
            "derived" => MetricSource::Derived,
            other => panic!("unknown source {other}"),
        },
    }
}

fn boot_id(text: &str) -> [u8; 16] {
    let digits: Vec<u8> = text.bytes().filter(|byte| *byte != b'-').collect();
    assert_eq!(digits.len(), 32, "a fixture boot id is 16 hex bytes");
    let mut out = [0u8; 16];
    for (index, digit) in digits.chunks(2).enumerate() {
        let high = (digit[0] as char).to_digit(16).expect("hex") as u8;
        let low = (digit[1] as char).to_digit(16).expect("hex") as u8;
        out[index] = (high << 4) | low;
    }
    out
}

fn dictionary(json: &Json) -> Dictionary {
    let generation =
        u32::try_from(json.field("generation").as_u64().expect("generation")).expect("u32");
    let mut dictionary = Dictionary::new(generation);
    for entry in json.field("entries").as_array().expect("entries") {
        let text = entry.field("text").as_str().unwrap_or("").to_string();
        let built = match entry.field("kind").as_str().expect("kind") {
            "entity" => Entry::Entity {
                inc: entry.field("inc").as_u64().expect("inc"),
                label: text,
            },
            "metric" => Entry::Metric {
                facts: facts(entry.field("facts")),
                name: text,
            },
            "label" => Entry::Label {
                kind: u8::try_from(entry.field("label_kind").as_usize().expect("label_kind"))
                    .expect("u8"),
                source: u8::try_from(
                    entry
                        .field("label_source")
                        .as_usize()
                        .expect("label_source"),
                )
                .expect("u8"),
                text,
            },
            "free" => Entry::Free { text },
            other => panic!("unknown dictionary entry kind {other}"),
        };
        dictionary
            .intern(built)
            .expect("fixture dictionary entry interns");
    }
    dictionary
}

fn block(spec: &Json) -> Block {
    let frame = spec.field("frame");
    let header_json = frame.field("header");
    let header = BlockHeaderFacts {
        boot_id: boot_id(header_json.field("boot_id").as_str().expect("boot id")),
        epoch: header_json.field("epoch").as_u64().expect("epoch"),
        seq: header_json.field("seq").as_u64().expect("seq"),
        mono_start_ns: header_json
            .field("mono_start_ns")
            .as_u64()
            .expect("mono start"),
        mono_end_ns: header_json.field("mono_end_ns").as_u64().expect("mono end"),
        boot_ns: header_json.field("boot_ns").as_u64().expect("boot ns"),
        real_ns: i64::try_from(header_json.field("real_ns").as_i128().expect("real ns"))
            .expect("i64"),
        dict_gen: u32::try_from(header_json.field("dict_gen").as_usize().expect("dict gen"))
            .expect("u32"),
        n_rows: u32::try_from(header_json.field("n_rows").as_usize().expect("n rows"))
            .expect("u32"),
        dropped: u32::try_from(header_json.field("dropped").as_usize().expect("dropped"))
            .expect("u32"),
        flags: u32::try_from(header_json.field("flags").as_usize().expect("flags")).expect("u32"),
    };
    let rows = frame
        .field("rows")
        .as_array()
        .expect("rows")
        .iter()
        .map(|row| Row {
            ent: u32::try_from(row.field("ent").as_usize().expect("ent")).expect("u32"),
            t_off_us: u32::try_from(row.field("t_off_us").as_usize().expect("t_off")).expect("u32"),
        })
        .collect();
    let time = frame
        .field("time")
        .as_array()
        .expect("time")
        .iter()
        .map(|bucket| TimeBucket {
            real_ns: i64::try_from(bucket.field("real_ns").as_i128().expect("real ns"))
                .expect("i64"),
            covered_ms: u32::try_from(bucket.field("covered_ms").as_usize().expect("covered"))
                .expect("u32"),
            samples: u16::try_from(bucket.field("samples").as_usize().expect("samples"))
                .expect("u16"),
            agg: match bucket.field("agg").as_str().expect("agg") {
                "raw" => Aggregation::Raw,
                "last" => Aggregation::Last,
                "delta" => Aggregation::Delta,
                "mean" => Aggregation::Mean,
                "min" => Aggregation::Min,
                "max" => Aggregation::Max,
                other => panic!("unknown aggregation {other}"),
            },
        })
        .collect();
    let columns = frame
        .field("columns")
        .as_array()
        .expect("columns")
        .iter()
        .map(|column| {
            let quality: Vec<u8> = column
                .field("quality")
                .as_array()
                .expect("quality")
                .iter()
                .map(|name| quality_code(name.as_str().expect("quality name")))
                .collect();
            let value_type = match column.field("type").as_str().expect("type") {
                "u64" => MetricValueType::U64,
                "i64" => MetricValueType::I64,
                "f64" => MetricValueType::F64,
                other => panic!("unknown value type {other}"),
            };
            let values: Vec<u64> = match value_type {
                // An f64 row is its bit pattern, so a NaN and a -0.0 survive the fixture.
                MetricValueType::F64 => column
                    .field("value_bits")
                    .as_array()
                    .expect("value_bits")
                    .iter()
                    .map(|text| {
                        let text = text.as_str().expect("bits");
                        u64::from_str_radix(text.trim_start_matches("0x"), 16).expect("hex bits")
                    })
                    .collect(),
                // An i64 row is stored as its two's-complement bits, which is what a
                // little-endian `i64` write produces.
                MetricValueType::I64 => column
                    .field("values")
                    .as_array()
                    .expect("values")
                    .iter()
                    .map(|text| {
                        text.as_str()
                            .expect("value text")
                            .parse::<i64>()
                            .expect("i64 value text") as u64
                    })
                    .collect(),
                MetricValueType::U64 => column
                    .field("values")
                    .as_array()
                    .expect("values")
                    .iter()
                    .map(|text| {
                        text.as_str()
                            .expect("value text")
                            .parse::<u64>()
                            .expect("u64 value text")
                    })
                    .collect(),
            };
            let kind = match column.field("kind").as_str().expect("kind") {
                "gauge" => MetricKind::Gauge,
                "counter" => MetricKind::Counter,
                "state" => MetricKind::State,
                other => panic!("unknown column kind {other}"),
            };
            Column::new(
                u32::try_from(column.field("metric").as_usize().expect("metric")).expect("u32"),
                value_type,
                kind,
                values,
                quality,
            )
            .expect("fixture column")
        })
        .collect();
    let dictionary = frame.get("dictionary").map(dictionary);
    Block {
        frame_type: match frame.field("type").as_str().expect("frame type") {
            "block" => FrameType::Block,
            "dict" => FrameType::Dict,
            other => panic!("unknown frame type {other}"),
        },
        corr: u32::try_from(frame.field("corr").as_usize().expect("corr")).expect("u32"),
        header,
        rows,
        time,
        columns,
        dictionary,
        subject: frame
            .get("subject")
            .map(|value| u32::try_from(value.as_usize().expect("subject")).expect("u32")),
        skipped_sections: Vec::new(),
    }
}

fn quality_code(name: &str) -> u8 {
    ALL_QUALITIES
        .into_iter()
        .find(|quality| quality.name() == name)
        .unwrap_or_else(|| panic!("quality {name} is not in the closed vocabulary"))
        .code()
}

fn refusal_of(error: &ProtoError) -> BTreeMap<&'static str, String> {
    let mut map = BTreeMap::new();
    map.insert("kind", error.kind().to_string());
    match error {
        ProtoError::UnknownQuality { row, code } => {
            map.insert("row", row.to_string());
            map.insert("code", code.to_string());
        }
        ProtoError::DanglingDictionaryRef { index, len } => {
            map.insert("index", index.to_string());
            map.insert("entries", len.to_string());
        }
        ProtoError::StaleDictionaryGeneration { needed, held } => {
            map.insert("needed", needed.to_string());
            map.insert("held", held.to_string());
        }
        ProtoError::UnsupportedSectionVersion {
            kind,
            version,
            supported,
        } => {
            map.insert("kind_code", kind.to_string());
            map.insert("version", version.to_string());
            map.insert("supported", supported.to_string());
        }
        ProtoError::OversizedFrame { len, max } => {
            map.insert("len", len.to_string());
            map.insert("max", max.to_string());
        }
        ProtoError::Truncated { context, .. } => {
            map.insert("context", (*context).to_string());
        }
        ProtoError::NonZeroReserved(value) => {
            map.insert("value", value.to_string());
        }
        ProtoError::ColumnReserved { metric, pad } => {
            map.insert("metric", metric.to_string());
            map.insert("pad", pad.to_string());
        }
        ProtoError::UnsupportedRequiredSection { kind } => {
            map.insert("kind_code", kind.to_string());
        }
        ProtoError::DuplicateDictionaryEntry { index } => {
            map.insert("index", index.to_string());
        }
        ProtoError::DictionaryEntryMask {
            index,
            mask,
            expected,
        } => {
            map.insert("index", index.to_string());
            map.insert("mask", mask.to_string());
            map.insert("expected", expected.to_string());
        }
        ProtoError::RowsAndTime { rows, buckets } => {
            map.insert("rows", rows.to_string());
            map.insert("buckets", buckets.to_string());
        }
        ProtoError::NoRowsOrTime { n_rows } => {
            map.insert("n_rows", n_rows.to_string());
        }
        ProtoError::RowReferenceNotEntity { index } => {
            map.insert("index", index.to_string());
        }
        ProtoError::ColumnReferenceNotMetric { index } => {
            map.insert("index", index.to_string());
        }
        ProtoError::ColumnKindMismatch {
            index,
            declared,
            dictionary,
        } => {
            map.insert("index", index.to_string());
            map.insert("declared", declared.to_string());
            map.insert("dictionary", dictionary.to_string());
        }
        ProtoError::NotABlock { frame_type } => {
            map.insert("frame_type", frame_type.to_string());
        }
        ProtoError::SectionOrder { index, kind } => {
            map.insert("index", index.to_string());
            map.insert("kind_code", kind.to_string());
        }
        ProtoError::SectionLength {
            kind,
            declared,
            expected,
        } => {
            map.insert("section", kind.to_string());
            map.insert("declared", declared.to_string());
            map.insert("expected", expected.to_string());
        }
        ProtoError::UnknownFrameFlags(flags) => {
            map.insert("flags", flags.to_string());
        }
        ProtoError::AggregationNotPermitted { metric, agg } => {
            map.insert("metric", metric.to_string());
            map.insert("agg", agg.to_string());
        }
        ProtoError::SubjectWithoutTime { subject } => {
            map.insert("subject", subject.to_string());
        }
        _ => {}
    }
    map
}

fn declared_refusal(spec: &Json) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    if let Json::Object(fields) = spec.field("expect_error") {
        for (key, value) in fields {
            let rendered = match value {
                Json::Number(number) => number.to_string(),
                Json::String(text) => text.clone(),
                other => {
                    panic!("an expected refusal field must be a number or a string, got {other:?}")
                }
            };
            map.insert(key.clone(), rendered);
        }
    }
    map
}

#[test]
fn every_fixture_is_decoded_by_the_rust_codec() {
    let names = fixture_names();
    assert!(
        names.len() >= 26,
        "the fixture set is the §B.5 case list: {}",
        names.len()
    );
    let mut valid = 0;
    let mut refused = 0;
    for name in names {
        let (spec, bytes) = load(&name);
        let expected = declared_refusal(&spec);
        // A case with a top-level `held` key is decoded against that held dictionary generation.
        let held = spec.get("held").map(dictionary);
        if expected.is_empty() {
            let decoded = decode_block_with(&bytes, held.as_ref())
                .unwrap_or_else(|error| panic!("{name}: decode: {error}"));
            let expected_block = block(&spec);
            assert_eq!(decoded.header, expected_block.header, "{name}: header");
            assert_eq!(decoded.rows, expected_block.rows, "{name}: rows");
            assert_eq!(decoded.time, expected_block.time, "{name}: time buckets");
            assert_eq!(decoded.columns, expected_block.columns, "{name}: columns");
            assert_eq!(
                decoded.dictionary, expected_block.dictionary,
                "{name}: dictionary"
            );
            assert_eq!(decoded.corr, expected_block.corr, "{name}: correlation id");
            assert_eq!(decoded.subject, expected_block.subject, "{name}: subject");
            assert_eq!(
                decoded.frame_type, expected_block.frame_type,
                "{name}: frame type"
            );
            let skipped: Vec<u16> = spec
                .field("frame")
                .field("skipped")
                .as_array()
                .expect("skipped")
                .iter()
                .map(|value| u16::try_from(value.as_usize().expect("skipped code")).expect("u16"))
                .collect();
            assert_eq!(
                decoded.skipped_sections, skipped,
                "{name}: skipped sections"
            );
            // And the Rust encoder reproduces the committed bytes exactly — except for a
            // fixture that carries a section kind this codec does not know: it can skip such
            // a section on read but cannot re-emit a body it never modelled.
            if decoded.skipped_sections.is_empty() {
                let encoded = encode_block(&decoded)
                    .unwrap_or_else(|error| panic!("{name}: re-encode: {error}"));
                assert_eq!(
                    encoded, bytes,
                    "{name}: the Rust encoder must emit the committed bytes"
                );
            } else {
                let encoded = encode_block(&decoded)
                    .unwrap_or_else(|error| panic!("{name}: re-encode: {error}"));
                assert!(
                    encoded.len() < bytes.len(),
                    "{name}: re-encoding without the unknown section must be shorter"
                );
            }
            valid += 1;
        } else {
            let error = decode_block_with(&bytes, held.as_ref())
                .expect_err(&format!("{name}: this fixture must be refused"));
            let actual = refusal_of(&error);
            for (key, value) in &expected {
                assert_eq!(
                    actual.get(key.as_str()),
                    Some(value),
                    "{name}: refusal field {key} (whole refusal: {error:?})"
                );
            }
            refused += 1;
        }
    }
    assert_eq!(
        valid, 11,
        "the valid fixture count is part of the case list"
    );
    assert_eq!(
        refused, 30,
        "the refusal fixture count is part of the case list"
    );
}

#[test]
fn a_fixture_whose_value_bytes_are_read_differently_is_caught() {
    // A guard for the guard: if a counter value silently became an f64 read, the live
    // fixture's 2^64-1 row would not compare equal, so this test would fail. Pin it
    // explicitly once so the assertion's value is visible.
    let (_, bytes) = load("live-block");
    let decoded = decode_block(&bytes).expect("live block decodes");
    let counter = decoded.columns.first().expect("counter column");
    assert_eq!(
        counter.sample(2).expect("row").value,
        TypedValue::U64(u64::MAX)
    );
    let gauge = decoded.columns.get(1).expect("gauge column");
    assert_eq!(gauge.sample(2).expect("row").quality, Quality::Absent);
    assert_eq!(
        gauge
            .sample(2)
            .expect("row")
            .value
            .measured(Quality::Absent),
        None,
        "a placeholder is never a reading"
    );
}

#[test]
fn the_quality_vocabulary_fixture_names_every_code() {
    let text =
        std::fs::read_to_string(format!("{FIXTURE_DIR}/quality.json")).expect("quality fixture");
    let spec = json::parse(text.as_bytes()).expect("quality fixture parses");
    let codes = spec.field("codes").as_array().expect("codes");
    assert_eq!(codes.len(), ALL_QUALITIES.len());
    for (position, quality) in ALL_QUALITIES.into_iter().enumerate() {
        let entry = codes.get(position).expect("code row");
        assert_eq!(
            entry.field("code").as_usize(),
            Some(usize::from(quality.code()))
        );
        assert_eq!(entry.field("name").as_str(), Some(quality.name()));
        assert_eq!(
            entry.get("measurement"),
            Some(&Json::Bool(quality.is_measurement())),
            "{}",
            quality.name()
        );
    }
}
