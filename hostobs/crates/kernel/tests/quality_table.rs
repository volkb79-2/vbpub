//! Kernel readings carry the model's quality type, and the model table is the shared
//! wire table byte for byte. The independent Python decoder checks the same fixture.

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
        let parsed = QualityCode::from_code(code, position).expect("in the vocabulary");
        assert_eq!(parsed.name(), entry.field("name").as_str().expect("name"));
        assert_eq!(
            parsed.is_measurement(),
            entry.get("measurement") == Some(&json::Json::Bool(true)),
            "{}",
            parsed.name()
        );
    }
    // The one code the M1 fix added is present and is not a measurement.
    assert_eq!(QualityCode::UnknownTopology.code(), 7);
    assert!(!QualityCode::UnknownTopology.is_measurement());
}
