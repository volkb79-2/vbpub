//! The stream-layer golden test (WIRE.md §11.4 reader cases, §5.2).
//!
//! Every `fixtures/stream/reader-*.json` is replayed against `FrameReader` with a fake
//! clock (`t_ms` becomes `now_ns`): each step's outputs (frames by type, corr and len, then
//! at most one error by its `kind` name, or `closed`) must equal the fixture's hand-derived
//! expectation exactly. The fixtures are written by `python/reference/make_stream_fixtures.py`
//! from the rules of §5.2, not by running a reader, so this test judges the Rust machine
//! against arithmetic. A second test feeds the concatenated valid frames of `fixtures/wire`
//! through a fixed pseudo-random splitter: split invariance over real frames.

// The fixture parser is shared with `golden_fixtures.rs` as source (see that file).
#[path = "../../../test-support/json.rs"]
mod json;

use std::path::{Path, PathBuf};

use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::FrameHeader;
use hostobs_proto::stream::{FrameReader, RawFrame};
use json::Json;

const STREAM_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/stream");
const WIRE_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/wire");
const NS_PER_MS: u64 = 1_000_000;

const READER_CASES: [&str; 12] = [
    "reader-age-from-first-byte",
    "reader-clean-eof",
    "reader-completion-age",
    "reader-dribbled-bytes",
    "reader-feed-after-eof",
    "reader-frame-then-bad-header",
    "reader-len-below-header",
    "reader-oversized-len",
    "reader-poisoned-stays",
    "reader-split-every-offset",
    "reader-truncated-stream",
    "reader-two-frames-one-feed",
];

fn read_file(path: &str) -> Vec<u8> {
    std::fs::read(path).unwrap_or_else(|error| panic!("read {path}: {error}"))
}

fn parse(label: &str, bytes: &[u8]) -> Json {
    json::parse(bytes).unwrap_or_else(|error| panic!("parse {label}: {error:?}"))
}

fn is_reader_fixture(path: &Path) -> bool {
    let Some(name) = path.file_name() else {
        return false;
    };
    let name = name.to_string_lossy();
    name.starts_with("reader-") && name.ends_with(".json")
}

fn reader_fixture_paths() -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(STREAM_DIR)
        .unwrap_or_else(|error| panic!("read {STREAM_DIR}: {error}"))
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| is_reader_fixture(path))
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

fn frame_text(name: &str, corr: u64, len: u64) -> String {
    format!("frame:{name}:{corr}:{len}")
}

fn error_text(error: &ProtoError) -> String {
    format!("error:{}", error.kind())
}

fn render_raw(raw: &RawFrame) -> String {
    let header = raw.header;
    let name = header.frame_type.name();
    frame_text(name, u64::from(header.corr), u64::from(header.len))
}

fn render_expected(output: &Json) -> String {
    if let Some(frame) = output.get("frame") {
        let name = frame.field("type").as_str().expect("frame type");
        let corr = frame.field("corr").as_u64().expect("frame corr");
        let len = frame.field("len").as_u64().expect("frame len");
        frame_text(name, corr, len)
    } else if let Some(kind) = output.get("error") {
        format!("error:{}", kind.as_str().expect("error kind"))
    } else if let Some(closed) = output.get("closed") {
        format!("closed:{}", closed.as_str().expect("closed text"))
    } else {
        panic!("unknown output {output:?}")
    }
}

fn replay(name: &str, spec: &Json) {
    let max_frame = spec.field("max_frame").as_u64().expect("max");
    let max_frame = u32::try_from(max_frame).expect("max_frame fits u32");
    let completion_ms = spec.field("completion_ms").as_u64().expect("ms");
    let steps = spec.field("steps").as_array().expect("steps");
    let expect = spec.field("expect").as_array().expect("expect");
    assert_eq!(steps.len(), expect.len(), "{name}: step lists");
    let mut reader = FrameReader::new(max_frame, completion_ms * NS_PER_MS);
    for (index, step) in steps.iter().enumerate() {
        let now = step.field("t_ms").as_u64().expect("t_ms") * NS_PER_MS;
        let actual: Vec<String> = if let Some(hex) = step.get("feed_hex") {
            let bytes = hex_decode(hex.as_str().expect("feed hex"));
            let feed = reader.feed(&bytes, now);
            let mut out: Vec<String> = feed.frames.iter().map(render_raw).collect();
            if let Some(error) = &feed.error {
                out.push(error_text(error));
            }
            out
        } else if step.get("poll").is_some() {
            match reader.poll(now) {
                Ok(()) => Vec::new(),
                Err(error) => vec![error_text(&error)],
            }
        } else if step.get("eof").is_some() {
            match reader.eof() {
                Ok(()) => vec!["closed:clean".to_string()],
                Err(error) => vec![error_text(&error)],
            }
        } else {
            panic!("{name}: step {index} is not feed, poll or eof");
        };
        let outputs = expect.get(index).expect("expect list");
        let outputs = outputs.as_array().expect("outputs");
        let wanted: Vec<String> = outputs.iter().map(render_expected).collect();
        assert_eq!(actual, wanted, "{name}: step {index}");
    }
}

#[test]
fn every_reader_fixture_replays_exactly() {
    let paths = reader_fixture_paths();
    assert_eq!(paths.len(), 12, "reader cases: {paths:?}");
    for case in READER_CASES {
        let path = format!("{STREAM_DIR}/{case}.json");
        assert!(paths.contains(&PathBuf::from(&path)), "missing {case}");
        let spec = parse(case, &read_file(&path));
        assert_eq!(spec.field("case").as_str(), Some(case));
        replay(case, &spec);
    }
}

// ---- split invariance over the valid frames of fixtures/wire ------------------------------

fn valid_wire_frames() -> Vec<Vec<u8>> {
    let mut names: Vec<String> = std::fs::read_dir(WIRE_DIR)
        .unwrap_or_else(|error| panic!("read {WIRE_DIR}: {error}"))
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.ends_with(".json") && name != "quality.json")
        .map(|name| name.trim_end_matches(".json").to_string())
        .collect();
    names.sort();
    let mut frames = Vec::new();
    for name in names {
        let spec = parse(&name, &read_file(&format!("{WIRE_DIR}/{name}.json")));
        if spec.get("expect_error").is_some() {
            continue;
        }
        frames.push(read_file(&format!("{WIRE_DIR}/{name}.bin")));
    }
    frames
}

/// A fixed xorshift64 generator: the splitter is deterministic, so a failure reproduces.
struct XorShift(u64);

impl XorShift {
    fn step(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
}

fn feed_in_parts(parts: &[&[u8]]) -> Vec<Vec<u8>> {
    let mut reader = FrameReader::new(16 * 1024 * 1024, 30_000 * NS_PER_MS);
    let mut frames = Vec::new();
    for part in parts {
        let feed = reader.feed(part, 0);
        assert_eq!(feed.error, None);
        frames.extend(feed.frames.into_iter().map(|raw| raw.bytes));
    }
    assert_eq!(reader.buffered_len(), 0, "frame boundary");
    frames
}

#[test]
fn the_valid_wire_frames_split_every_way_yield_the_same_frames() {
    let wire = valid_wire_frames();
    assert_eq!(wire.len(), 11, "§11.3 holds 11 valid cases");
    for (index, bytes) in wire.iter().enumerate() {
        let header = FrameHeader::parse(bytes).expect("valid header");
        let declared = usize::try_from(header.len).expect("len");
        assert_eq!(declared, bytes.len(), "case {index}");
    }
    let stream: Vec<u8> = wire.concat();

    // Whole stream in one feed is the reference.
    let reference = feed_in_parts(&[&stream]);
    assert_eq!(reference, wire);

    // Every single cut point.
    for cut in 0..=stream.len() {
        let (head, tail) = stream.split_at(cut);
        assert_eq!(feed_in_parts(&[head, tail]), wire, "cut {cut}");
    }

    // One byte at a time.
    let singles: Vec<&[u8]> = stream.chunks(1).collect();
    assert_eq!(feed_in_parts(&singles), wire, "dribbled");

    // Fixed pseudo-random splitters of 1..=300 byte chunks.
    let mut rng = XorShift(0x9E37_79B9_7F4A_7C15);
    for round in 0..300 {
        let mut parts: Vec<&[u8]> = Vec::new();
        let mut rest: &[u8] = &stream;
        while !rest.is_empty() {
            let size = usize::try_from(rng.step() % 300).expect("small") + 1;
            let (part, tail) = rest.split_at(size.min(rest.len()));
            parts.push(part);
            rest = tail;
        }
        assert_eq!(feed_in_parts(&parts), wire, "round {round}");
    }
}
