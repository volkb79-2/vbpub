//! The committed segment fixtures decode, in Rust, into exactly the facts the independent
//! Python reference says they hold (plan v4 §C.5's M1 acceptance).
//!
//! `hostobs/fixtures/segment/<case>.seg` is a whole segment file, and `<case>.json` is its
//! decoded description. The bytes were produced by
//! `python/reference/hostobs_segment_ref.py`, which is written from
//! `docs/SEGMENT-FORMAT.md` and not from this crate, so a layout change that only one side
//! makes turns this test red. `crates/store`'s writer is checked against the same document by
//! `the_writer_reproduces_the_portable_fixture` below, which writes a segment from the same
//! description and compares the bytes field by field rather than by whole-file equality
//! (a writer names files and pads for its own tier, and both are allowed to differ).
//!
//! The Python side asserts the same files from the other direction:
//! `python/tests/test_reference.py` decodes every `<case>.seg` and re-encodes it.

#[path = "../../../test-support/json.rs"]
mod json;

use hostobs_store::format::{SegmentHeader, TierKind, HEADER_LEN, RECORD_HEADER_LEN};
use hostobs_store::segment::{scan_bytes, TailState};

fn fixtures() -> Vec<(String, json::Json, Vec<u8>)> {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/segment");
    let mut cases = Vec::new();
    let entries = std::fs::read_dir(&dir).expect("the fixture directory is committed");
    let mut names: Vec<String> = Vec::new();
    for entry in entries {
        let entry = entry.expect("a fixture directory entry");
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.ends_with(".seg") {
            names.push(name.trim_end_matches(".seg").to_string());
        }
    }
    names.sort();
    assert!(
        names.len() >= 5,
        "the fixture set covers the shapes: {names:?}"
    );
    for name in names {
        let spec = std::fs::read_to_string(dir.join(format!("{name}.json")))
            .expect("a fixture description beside its bytes");
        let spec = json::parse(spec.as_bytes()).expect("the description parses");
        let bytes = std::fs::read(dir.join(format!("{name}.seg"))).expect("the fixture bytes");
        cases.push((name, spec, bytes));
    }
    cases
}

/// The fixture's integer as an `i64`, so a negative time is read as a negative time.
fn int(value: &json::Json) -> i64 {
    i64::try_from(value.as_i128().expect("an integer"))
        .expect("a fixture only carries i64-range values")
}

#[test]
fn every_segment_fixture_decodes_to_the_description_beside_it() {
    for (name, spec, bytes) in fixtures() {
        let scan = scan_bytes(&name, &bytes, 4096).expect("the fixture decodes");
        assert_eq!(
            scan.tail,
            TailState::Complete,
            "{name}: a committed fixture must not have a damaged tail"
        );
        let header = spec.field("header");
        assert_eq!(
            scan.header,
            SegmentHeader {
                seq: header.field("seq").as_u64().expect("seq"),
                epoch: header.field("epoch").as_u64().expect("epoch"),
                dict_generation: u32::try_from(
                    header
                        .field("dict_generation")
                        .as_u64()
                        .expect("generation")
                )
                .expect("u32"),
                kind: match header.field("kind").as_str().expect("kind") {
                    "history" => TierKind::History,
                    "events" => TierKind::Events,
                    other => panic!("{name}: unknown fixture corpus {other}"),
                },
                payload: match header.field("payload").as_str().expect("payload") {
                    "block" => hostobs_store::format::PAYLOAD_BLOCK,
                    "evt" => hostobs_store::format::PAYLOAD_EVT,
                    other => panic!("{name}: unknown fixture payload {other}"),
                },
                start_real_ns: int(header.field("start_real_ns")),
                max_payload: u32::try_from(
                    header.field("max_payload").as_u64().expect("max payload")
                )
                .expect("u32"),
                max_incarnations: u32::try_from(
                    header
                        .field("max_incarnations")
                        .as_u64()
                        .expect("max incarnations")
                )
                .expect("u32"),
            },
            "{name}: the header the reference wrote is the header this crate reads"
        );
        let records = spec.field("records").as_array().expect("records");
        assert_eq!(
            scan.records.len(),
            records.len(),
            "{name}: the record count is the description's"
        );
        assert_eq!(scan.incarnations.len(), {
            let mut ids = std::collections::BTreeSet::new();
            for record in records {
                for inc in record
                    .field("incarnations")
                    .as_array()
                    .expect("incarnations")
                {
                    ids.insert(inc.as_u64().expect("id"));
                }
            }
            ids.len()
        });
        for (index, (expected, actual)) in records.iter().zip(scan.records.iter()).enumerate() {
            assert_eq!(actual.seq, u64::try_from(index).expect("index"), "{name}");
            assert_eq!(
                actual.real_ns,
                int(expected.field("real_ns")),
                "{name}: record {index} real time"
            );
            let ids: Vec<u64> = expected
                .field("incarnations")
                .as_array()
                .expect("incarnations")
                .iter()
                .map(|id| id.as_u64().expect("id"))
                .collect();
            assert_eq!(actual.incarnations, ids, "{name}: record {index} id table");
            let payload = expected.field("payload_hex").as_str().expect("payload hex");
            assert_eq!(
                actual.payload.len(),
                payload.len() / 2,
                "{name}: record {index} payload length"
            );
            // The payload is compared through its own frame header facts, because `proto`
            // owns the section layout and this crate only decides whether bytes may be stored.
            assert_eq!(
                hostobs_store::format::check_payload(
                    &actual.payload,
                    scan.header.kind,
                    scan.header.max_payload
                )
                .expect("the stored payload is the frame its corpus requires")
                .len as usize,
                actual.payload.len()
            );
        }
        let sealed = spec.get("sealed") == Some(&json::Json::Bool(true));
        assert_eq!(scan.is_sealed(), sealed, "{name}: sealed-ness");
        if let Some(footer) = spec.get("footer").filter(|_| sealed) {
            let parsed = scan.footer.expect("a sealed fixture has a footer");
            assert_eq!(
                parsed.record_count,
                u64::try_from(records.len()).expect("count"),
                "{name}: the footer counts what is there"
            );
            assert_eq!(
                parsed.seq, scan.header.seq,
                "{name}: the footer names the same segment as the header"
            );
            assert_eq!(
                parsed.dict_generation, scan.header.dict_generation,
                "{name}: the footer names the same dictionary generation"
            );
            assert_eq!(
                parsed.end_offset,
                footer.field("end_offset").as_u64().expect("end offset"),
                "{name}: the footer says where the records end"
            );
            assert_eq!(parsed.first_real_ns, int(footer.field("first_real_ns")));
            assert_eq!(parsed.last_real_ns, int(footer.field("last_real_ns")));
        } else {
            assert!(
                scan.footer.is_none(),
                "{name}: an open fixture has no footer"
            );
        }
        assert_eq!(
            scan.good_end, scan.file_len,
            "{name}: a complete segment's good end is its whole file"
        );
        assert!(!scan.record_bound_reached);
    }
}

#[test]
fn a_fixture_segment_is_reproduced_record_for_record_by_this_crates_writer() {
    // The writer is exercised on the description of a real fixture: the same header, the same
    // records, the same payloads. The comparison is on the format, not on whole-file equality,
    // because a store names files for its tier and may pad for its own alignment.
    for (name, _spec, bytes) in fixtures() {
        let scan = scan_bytes(&name, &bytes, 4096).expect("the fixture decodes");
        // A conforming writer must reproduce this file's length exactly: the envelope has no
        // free choice in it, and a padding or length change would silently move every offset.
        let expected_len = bytes.len();
        let mut written = Vec::new();
        written.extend_from_slice(
            &scan
                .header
                .encode()
                .expect("the reference header re-encodes"),
        );
        for record in &scan.records {
            written.extend_from_slice(
                &hostobs_store::format::RecordHeader::encode_record(
                    record.seq,
                    scan.header.kind.record_kind(),
                    record.real_ns,
                    &record.incarnations,
                    &record.payload,
                    scan.header.max_payload,
                    scan.header.max_incarnations,
                )
                .expect("the reference record re-encodes"),
            );
        }
        let rescan = scan_bytes(&name, &written, 4096).expect("the rewritten segment decodes");
        assert_eq!(rescan.header, scan.header, "{name}: header");
        assert_eq!(rescan.records, scan.records, "{name}: records");
        assert_eq!(
            rescan.tail,
            TailState::Complete,
            "{name}: no tear in a rewritten segment"
        );
        if scan.is_sealed() {
            let footer = scan.footer.expect("sealed");
            written.extend_from_slice(&footer.encode());
            let rescan = scan_bytes(&name, &written, 4096).expect("the sealed rewrite decodes");
            assert_eq!(rescan.footer, scan.footer, "{name}: footer");
            assert_eq!(written.len(), expected_len, "{name}: sealed length");
        } else {
            assert_eq!(
                written.len(),
                expected_len,
                "{name}: open length is the header plus its records"
            );
        }
        assert!(written.len() >= HEADER_LEN);
        assert_eq!(HEADER_LEN, 64);
        assert_eq!(RECORD_HEADER_LEN, 48);
    }
}
