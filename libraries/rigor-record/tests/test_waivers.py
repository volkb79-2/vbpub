import pytest

from conftest import GOLDEN, has
from rigor_record import is_expired, load_waivers, validate_waivers
from rigor_record.validate import _semver_key

TEXT = (GOLDEN / "waivers" / "waivers.toml").read_text()


def test_golden_waivers_load_clean():
    doc, violations = load_waivers(TEXT)
    assert violations == []
    assert doc["lane"][0]["lane"] == "worker_io_dns_operations"
    assert [s["disposition"] for s in doc["survivor"]] == ["equivalent-reviewed", "accepted"]


def test_schema_only_document_is_valid():
    assert load_waivers('schema = "urn:vbpub:rigor-waivers:1"\n')[1] == []


def test_toml_syntax_error_is_a_violation_not_an_exception():
    doc, violations = load_waivers("schema = [")
    assert doc is None and violations[0].message.startswith("not valid TOML")


def replace(old, new):
    assert old in TEXT
    return load_waivers(TEXT.replace(old, new))[1]


def test_schema_violations():
    assert has(replace('urn:vbpub:rigor-waivers:1', 'urn:vbpub:rigor-waivers:2'), "$.schema", "must equal")
    assert has(replace('level = "R1"', 'level = "R2"'), "$.lane[0].level", "must be one of")
    assert has(replace('expires_before = "1.6.0"', 'expires_before = "soon"'), "$.lane[0].expires_before", "pattern")
    assert has(replace('disposition = "equivalent-reviewed"','disposition = "open"'), "$.survivor[0].disposition", "must be one of")
    assert has(replace('reviewed_by = "independent-review"\n\n[[survivor]]', 'reviewed_by = "Jane Doe"\n\n[[survivor]]'),
               "$.survivor[0].reviewed_by", "pattern")
    assert has(replace('plan_item = "P-xyz"\n', 'plan_item = "P-xyz"\nowner = "x"\n'), "$.lane[0].owner", "unexpected key")
    assert has(load_waivers('[[lane]]\nlane = "a"\n')[1], "$", "missing required key 'schema'")


def test_duplicates_are_refused():
    dup_lane = TEXT + '\n[[lane]]\nlane = "slow-io"\nlevel = "R0"\nreason = "again"\nexpires_before = "3.0.0"\n'
    assert has(load_waivers(dup_lane)[1], "$.lane[2]", "duplicate")
    same_lane_other_level = TEXT + '\n[[lane]]\nlane = "slow-io"\nlevel = "R1"\nreason = "other level"\nexpires_before = "3.0.0"\n'
    assert load_waivers(same_lane_other_level)[1] == []
    dup_id = TEXT + '\n[[survivor]]\ncandidate_id = "cand-0001"\ndisposition = "accepted"\nreason = "again"\nreviewed_by = "r"\n'
    assert has(load_waivers(dup_id)[1], "$.survivor[2].candidate_id", "duplicate")


def test_privacy_applies_to_waiver_text():
    out = load_waivers(TEXT, hosts=["P-xyz"])[1]
    assert has(out, "$.lane[0].reason", "privacy rule host")
    assert has(out, "$.lane[0].plan_item", "privacy rule host")
    # the validator entry point takes the same lists
    doc, _ = load_waivers(TEXT)
    assert has(validate_waivers(doc, users=["independent-review"]), "$.survivor[0].reviewed_by", "privacy rule user")
    assert validate_waivers(doc) == []


@pytest.mark.parametrize(
    "limit,version,expected",
    [
        ("1.6.0", "1.5.9", False),
        ("1.6.0", "1.6.0", True),  # equal refuses
        ("1.6.0", "1.6.1", True),
        ("1.6.0", "2.0.0", True),
        ("1.6.0", "1.10.0", True),  # numeric, not lexical
        ("1.10.0", "1.9.0", False),
        ("1.6.0", "1.6.0-rc1", False),  # a pre-release is before its release
        ("1.6.0-rc1", "1.6.0", True),
        ("1.6.0-rc1", "1.6.0-rc2", True),
        ("1.6.0-rc2", "1.6.0-rc1", False),
    ],
)
def test_expiry(limit, version, expected):
    assert is_expired(limit, version) is expected


@pytest.mark.parametrize("bad", ["1.6", "v1.6.0", "", "1.6.0.1"])
def test_expiry_rejects_non_semver(bad):
    with pytest.raises(ValueError, match="semantic version"):
        _semver_key(bad)
    with pytest.raises(ValueError):
        is_expired("1.0.0", bad)
