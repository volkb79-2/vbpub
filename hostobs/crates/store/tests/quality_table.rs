//! The store's quality table is the *shared* wire table, byte for byte.
//!
//! `hostobs-store` does not depend on `hostobs-model` or `hostobs-proto` (a path dependency
//! rewrites `Cargo.lock`, which the Rust lane image pins — `HOB-27`), so the crates are bound by
//! `hostobs/fixtures/wire/quality.json` instead of by a type. `model`, `proto`, `kernel`, the
//! independent Python decoder and this crate all read the same fixture; a renumbered or renamed
//! code is a red test in every one of them, which is the only thing that makes five separate
//! tables one table.
//!
//! This assertion lives in an integration test rather than inside the library because an
//! in-crate `#[path]` to `hostobs/test-support/json.rs` would resolve through the non-existent
//! `crates/store/src/quality/` directory (the same reason `kernel`'s table test lives here).

#[path = "../../../test-support/json.rs"]
mod json;

use hostobs_store::quality::{QualityCode, ALL};

#[test]
fn the_store_table_is_the_shared_wire_table() {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../fixtures/wire/quality.json"
    ))
    .expect("quality fixture is committed");
    let spec = json::parse(text.as_bytes()).expect("quality fixture parses");
    assert_eq!(
        spec.field("case").as_str(),
        Some("quality-vocabulary"),
        "the fixture names itself"
    );
    let codes = spec.field("codes").as_array().expect("codes");
    assert_eq!(codes.len(), 8, "eight closed codes");
    for (position, entry) in codes.iter().enumerate() {
        let code = u8::try_from(entry.field("code").as_u64().expect("code")).expect("u8");
        assert_eq!(usize::from(code), position, "codes are stored in order");
        let parsed = QualityCode::from_code(code).expect("in the vocabulary");
        assert_eq!(parsed.name(), entry.field("name").as_str().expect("name"));
        assert_eq!(
            parsed.is_measurement(),
            entry.get("measurement") == Some(&json::Json::Bool(true)),
            "{}",
            parsed.name()
        );
        assert_eq!(
            parsed, ALL[position],
            "the store's own table lists the code in the fixture's order"
        );
    }
    // A code past the table is refused by the store, not silently stored.
    for code in 8u8..=u8::MAX {
        assert!(QualityCode::from_code(code).is_err(), "code {code}");
    }
    assert_eq!(QualityCode::UNKNOWN_TOPOLOGY.code(), 7);
    assert!(!QualityCode::UNKNOWN_TOPOLOGY.is_measurement());
}
