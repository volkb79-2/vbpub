//! The kernel crate's quality table is the *shared* wire table, byte for byte.
//!
//! `hostobs-kernel` does not depend on `hostobs-model` (a path dependency rewrites
//! `Cargo.lock`, which the Rust lane image pins — `HOB-17`), so the two crates are bound by
//! this fixture instead of by a type. `model`, `proto`, the independent Python decoder and
//! this crate all read `hostobs/fixtures/wire/quality.json`; a renumbered or renamed code
//! is a red test in every one of them, which is the only thing that makes four separate
//! tables one table.

#[path = "../../../test-support/json.rs"]
mod json;

use hostobs_kernel::quality::QualityCode;

#[test]
fn the_kernel_table_is_the_shared_wire_table() {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../fixtures/wire/quality.json"
    ))
    .expect("quality fixture is committed");
    let spec = json::parse(text.as_bytes()).expect("quality fixture parses");
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
    }
    // The one code the M1 fix added is present and is not a measurement.
    assert_eq!(QualityCode::UNKNOWN_TOPOLOGY.code(), 7);
    assert!(!QualityCode::UNKNOWN_TOPOLOGY.is_measurement());
}
