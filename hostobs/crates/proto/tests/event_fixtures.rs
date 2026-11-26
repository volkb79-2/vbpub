//! The event/1 golden-fixture test.
//!
//! The JSON descriptions define each expected event, and the committed `.bin` files are
//! canonical stored EVT frames. Valid frames must decode to their descriptions and be
//! reproduced byte for byte by the public encoder. Refusal frames must report the kind and
//! field named by their description; order refusals also pin the zero-based index.

// The fixture parser is shared by integration tests as source, not as a workspace member.
#[path = "../../../test-support/json.rs"]
mod json;

use hostobs_proto::error::ProtoError;
use hostobs_proto::event::{
    decode_event_frame, encode_event_frame, EventAttribute, EventContext, EventPayload,
};
use json::Json;

const FIXTURE_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/event");

fn fixture_names() -> Vec<String> {
    let mut entries: Vec<String> = std::fs::read_dir(FIXTURE_DIR)
        .unwrap_or_else(|error| panic!("read {FIXTURE_DIR}: {error}"))
        .map(|entry| {
            let entry =
                entry.unwrap_or_else(|error| panic!("read entry in {FIXTURE_DIR}: {error}"));
            let file_type = entry
                .file_type()
                .unwrap_or_else(|error| panic!("read file type in {FIXTURE_DIR}: {error}"));
            assert!(
                file_type.is_file(),
                "the event fixture directory contains only files"
            );
            entry.file_name().to_string_lossy().into_owned()
        })
        .collect();
    entries.sort();
    assert_eq!(
        entries.len(),
        14,
        "seven event fixtures have .bin and .json files"
    );
    assert!(
        entries
            .iter()
            .all(|name| { name.ends_with(".bin") || name.ends_with(".json") }),
        "the event fixture directory contains no other extension"
    );
    let mut names: Vec<String> = entries
        .iter()
        .filter_map(|name| name.strip_suffix(".json").map(str::to_owned))
        .collect();
    names.sort();
    assert_eq!(names.len(), 7, "the event corpus has exactly seven cases");
    for name in &names {
        assert!(
            entries.binary_search(&format!("{name}.bin")).is_ok(),
            "{name} has a JSON description and bytes"
        );
    }
    names
}

fn fixture_spec(name: &str) -> Json {
    let path = format!("{FIXTURE_DIR}/{name}.json");
    let bytes = std::fs::read(&path).unwrap_or_else(|error| panic!("read {path}: {error}"));
    json::parse(&bytes).unwrap_or_else(|error| panic!("parse {path}: {error:?}"))
}

fn fixture(name: &str) -> Vec<u8> {
    let path = format!("{FIXTURE_DIR}/{name}.bin");
    std::fs::read(&path).unwrap_or_else(|error| panic!("read {path}: {error}"))
}

fn event_from_spec(spec: &Json) -> EventPayload {
    let number = |key| {
        spec.field(key)
            .as_u64()
            .unwrap_or_else(|| panic!("{key} must be an unsigned integer"))
    };
    let text = |key| {
        spec.field(key)
            .as_str()
            .unwrap_or_else(|| panic!("{key} must be a string"))
            .to_string()
    };
    let optional_text = |key| match spec.field(key) {
        Json::Null => None,
        Json::String(value) => Some(value.clone()),
        _ => panic!("{key} must be a string or null"),
    };
    let ctx = spec
        .field("ctx")
        .as_array()
        .expect("ctx array")
        .iter()
        .map(|entry| EventContext {
            key: u8::try_from(entry.field("key").as_u64().expect("context key"))
                .expect("context key fits u8"),
            value: entry
                .field("value")
                .as_str()
                .expect("context value")
                .to_string(),
        })
        .collect();
    let attrs = spec
        .field("attrs")
        .as_array()
        .expect("attrs array")
        .iter()
        .map(|entry| EventAttribute {
            key: entry
                .field("key")
                .as_str()
                .expect("attribute key")
                .to_string(),
            value: entry
                .field("value")
                .as_str()
                .expect("attribute value")
                .to_string(),
        })
        .collect();
    let inferred = match spec.field("inferred") {
        Json::Bool(value) => *value,
        _ => panic!("inferred must be a boolean"),
    };
    EventPayload {
        t_mono_ns: i64::try_from(number("t_mono_ns")).expect("t_mono_ns fits i64"),
        span: number("span"),
        peer_dev: number("peer_dev"),
        peer_inode: number("peer_inode"),
        uid: u32::try_from(number("uid")).expect("uid fits u32"),
        peer_pid: u32::try_from(number("peer_pid")).expect("peer_pid fits u32"),
        kind: u8::try_from(number("kind")).expect("kind fits u8"),
        outcome: u8::try_from(number("outcome")).expect("outcome fits u8"),
        inferred,
        ctx,
        attrs,
        name: text("name"),
        key: text("key"),
        code: optional_text("code"),
        comm: optional_text("comm"),
    }
}

fn expected_error_for(name: &str) -> Option<&'static str> {
    match name {
        "event-minimal" | "event-full" | "event-point-ctx" => None,
        "event-unknown-code" => Some("event-unknown-code"),
        "event-overflow" => Some("event-field-bound"),
        "event-unsorted-attrs" => Some("event-order"),
        "event-control-text" => Some("event-text"),
        other => panic!("unrecognized event fixture case {other}"),
    }
}

fn assert_refusal_detail(spec: &Json, error: &ProtoError, name: &str) {
    let expected_field = spec
        .field("field")
        .as_str()
        .unwrap_or_else(|| panic!("{name}: expect_error.field must be a string"));
    match error {
        ProtoError::EventUnknownCode { field, .. }
        | ProtoError::EventFieldBound { field }
        | ProtoError::EventText { field } => {
            assert_eq!(
                *field,
                expected_field,
                "{name}: refusal field matches description"
            );
            assert!(
                spec.get("index").is_none(),
                "{name}: only order refusals carry an index"
            );
        }
        ProtoError::EventOrder { field, index } => {
            assert_eq!(
                *field,
                expected_field,
                "{name}: refusal field matches description"
            );
            let described_index = spec
                .field("index")
                .as_u64()
                .and_then(|value| usize::try_from(value).ok())
                .unwrap_or_else(|| panic!("{name}: expect_error.index must be an integer"));
            assert_eq!(
                *index, described_index,
                "{name}: refusal index matches description"
            );
        }
        other => panic!("{name}: unexpected event fixture refusal {other:?}"),
    }
}

#[test]
fn every_event_fixture_matches_its_description_or_refusal() {
    for name in fixture_names() {
        let spec = fixture_spec(&name);
        assert_eq!(
            spec.field("case").as_str(),
            Some(name.as_str()),
            "description case matches its filename"
        );
        let event = event_from_spec(spec.field("event"));
        let bytes = fixture(&name);

        match expected_error_for(&name) {
            None => {
                assert!(
                    spec.get("expect_error").is_none(),
                    "{name} is a valid case without an expect_error"
                );
                let decoded = decode_event_frame(&bytes)
                    .unwrap_or_else(|error| panic!("{name}: decode event frame: {error}"));
                assert_eq!(
                    decoded, event,
                    "{name}: decoded value matches its description"
                );
                let encoded = encode_event_frame(&event)
                    .unwrap_or_else(|error| panic!("{name}: encode event frame: {error}"));
                assert_eq!(
                    encoded, bytes,
                    "{name}: encoded frame matches committed bytes"
                );
            }
            Some(expected) => {
                let described = spec
                    .field("expect_error")
                    .field("kind")
                    .as_str()
                    .unwrap_or_else(|| panic!("{name}: expect_error.kind must be a string"));
                assert_eq!(described, expected, "{name}: description error kind");
                let error = decode_event_frame(&bytes)
                    .expect_err("refusal fixture must be rejected by the public decoder");
                assert_eq!(
                    error.kind(),
                    described,
                    "{name}: error kind matches description"
                );
                assert_eq!(
                    error.kind(),
                    expected,
                    "{name}: error kind matches case table"
                );
                assert_refusal_detail(spec.field("expect_error"), &error, &name);
            }
        }
    }
}
