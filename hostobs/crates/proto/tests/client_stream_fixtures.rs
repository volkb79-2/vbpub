//! The client stream golden test (WIRE.md §11.4 client cases, §6).
//!
//! Every `fixtures/stream/client-*.json` is replayed against `StreamDicts`: a `feed_hex` step is
//! one BLOCK frame of stream corr 5, decoded with `decode_block_with(bytes, held(5))` and then
//! judged by `accept`; a decoder or stream error closes the stream (§6.5); a `hello` step calls
//! `on_hello` (§6.4). Each step's single output (`ok:<generation>`, `error:<kind>` or
//! `session:<first|same|changed>`) must equal the fixture's hand-derived expectation. The
//! fixtures are written by `python/reference/make_client_stream_fixtures.py` from the rules of
//! §6, not by running a client, so this test judges the Rust state against arithmetic. The
//! fixture's `t_ms` only orders the steps: the dictionary state has no clock.

// The fixture parser is shared with `golden_fixtures.rs` as source (see that file).
#[path = "../../../test-support/json.rs"]
mod json;

use std::path::{Path, PathBuf};

use hostobs_proto::frame::decode_block_with;
use hostobs_proto::stream_dicts::{Session, StreamDicts};
use json::Json;

const STREAM_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/stream");
const CORR: u32 = 5;

const CLIENT_CASES: [&str; 6] = [
    "client-epoch-change",
    "client-first-block-bare",
    "client-first-block-gen-5",
    "client-generation-regressed",
    "client-generation-step",
    "client-required-section",
];

fn read_file(path: &str) -> Vec<u8> {
    std::fs::read(path).unwrap_or_else(|error| panic!("read {path}: {error}"))
}

fn parse(label: &str, bytes: &[u8]) -> Json {
    json::parse(bytes).unwrap_or_else(|error| panic!("parse {label}: {error:?}"))
}

fn is_client_fixture(path: &Path) -> bool {
    let Some(name) = path.file_name() else {
        return false;
    };
    let name = name.to_string_lossy();
    name.starts_with("client-") && name.ends_with(".json")
}

fn client_fixture_paths() -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(STREAM_DIR)
        .unwrap_or_else(|error| panic!("read {STREAM_DIR}: {error}"))
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| is_client_fixture(path))
        .collect();
    paths.sort();
    paths
}

fn hex_decode(text: &str) -> Vec<u8> {
    assert_eq!(text.len() % 2, 0, "hex text has an even length");
    let mut out = Vec::new();
    for pair in text.as_bytes().chunks(2) {
        let digits = std::str::from_utf8(pair).expect("hex digits are ascii");
        out.push(u8::from_str_radix(digits, 16).expect("hex byte"));
    }
    out
}

fn session_name(session: Session) -> &'static str {
    match session {
        Session::First => "first",
        Session::Same => "same",
        Session::Changed => "changed",
    }
}

/// Decode one BLOCK frame against what stream `CORR` holds, judge it and close the stream on
/// any error, as a client must (§6.5).
fn feed(dicts: &mut StreamDicts, bytes: &[u8]) -> String {
    let decoded = decode_block_with(bytes, dicts.held(CORR));
    let block = match decoded {
        Ok(block) => block,
        Err(error) => {
            dicts.close(CORR);
            return format!("error:{}", error.kind());
        }
    };
    match dicts.accept(CORR, &block) {
        Ok(()) => format!("ok:{}", block.header.dict_gen),
        Err(error) => {
            dicts.close(CORR);
            format!("error:{}", error.kind())
        }
    }
}

fn render_expected(output: &Json) -> String {
    if let Some(generation) = output.get("ok") {
        format!("ok:{}", generation.as_u64().expect("generation"))
    } else if let Some(kind) = output.get("error") {
        format!("error:{}", kind.as_str().expect("error kind"))
    } else if let Some(session) = output.get("session") {
        format!("session:{}", session.as_str().expect("session"))
    } else {
        panic!("unknown output {output:?}")
    }
}

fn replay(name: &str, spec: &Json) {
    let steps = spec.field("steps").as_array().expect("steps");
    let expect = spec.field("expect").as_array().expect("expect");
    assert_eq!(steps.len(), expect.len(), "{name}: step lists");
    let mut dicts = StreamDicts::new();
    for (index, step) in steps.iter().enumerate() {
        let actual = if let Some(hex) = step.get("feed_hex") {
            feed(&mut dicts, &hex_decode(hex.as_str().expect("feed hex")))
        } else if let Some(hello) = step.get("hello") {
            let boot_id = hello.field("boot_id").as_str().expect("boot_id");
            let epoch = hello.field("epoch").as_u64().expect("epoch");
            let session = dicts.on_hello(boot_id, epoch);
            format!("session:{}", session_name(session))
        } else {
            panic!("{name}: step {index} is not feed or hello");
        };
        let outputs = expect.get(index).expect("expect list");
        let outputs = outputs.as_array().expect("outputs");
        let wanted: Vec<String> = outputs.iter().map(render_expected).collect();
        assert_eq!(vec![actual], wanted, "{name}: step {index}");
    }
}

#[test]
fn every_client_fixture_replays_exactly() {
    let paths = client_fixture_paths();
    assert_eq!(paths.len(), 6, "client cases: {paths:?}");
    for case in CLIENT_CASES {
        let path = format!("{STREAM_DIR}/{case}.json");
        assert!(paths.contains(&PathBuf::from(&path)), "missing {case}");
        let spec = parse(case, &read_file(&path));
        assert_eq!(spec.field("case").as_str(), Some(case));
        replay(case, &spec);
    }
}
