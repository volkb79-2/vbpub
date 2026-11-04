import json

import pytest

from rigor_record import schema
from rigor_record.schema import SCHEMA_FILES, Validator, Violation, load_schema

COMMON = "rigor-common-1.json"


def check(value, node):
    return Validator(COMMON)._check(value, node, COMMON, "$")


def walk(node):
    """Yield every (keyword, value, container) of a schema document, descending through schema positions only."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value, node
            if key in ("properties", "$defs"):
                for sub in value.values():
                    yield from walk(sub)
            elif key in ("items", "propertyNames") or (key == "additionalProperties" and value is not False):
                yield from walk(value)
            elif key == "oneOf":
                for sub in value:
                    yield from walk(sub)


def test_every_packaged_schema_loads_and_uses_only_supported_keywords():
    supported = schema.CONSTRAINT_KEYWORDS | schema.ANNOTATION_KEYWORDS
    for name in SCHEMA_FILES:
        doc = load_schema(name)
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        used = {key for key, _, _ in walk(doc)}
        assert used <= supported, (name, used - supported)


def test_patterns_use_dollar_only_as_the_final_anchor():
    for name in SCHEMA_FILES:
        for key, value, _ in walk(load_schema(name)):
            if key == "pattern":
                assert "$" not in value[:-1], value
                assert value.startswith("^") and value.endswith("$"), value


def test_every_schema_file_is_listed():
    from importlib import resources

    on_disk = {p.name for p in resources.files("rigor_record").joinpath("schemas").iterdir() if p.name.endswith(".json")}
    assert on_disk == set(SCHEMA_FILES)


def test_unknown_schema_file_is_refused():
    with pytest.raises(KeyError):
        load_schema("nope.json")


def test_unknown_keyword_is_refused_not_ignored():
    with pytest.raises(ValueError, match="unsupported schema keyword"):
        check(1, {"multipleOf": 2})


def test_unknown_type_name_is_refused():
    with pytest.raises(ValueError, match="type name"):
        check(1, {"type": "decimal"})


def test_type_checks():
    cases = [
        ("null", None, True), ("null", 0, False),
        ("boolean", True, True), ("boolean", 1, False),
        ("integer", 3, True), ("integer", True, False), ("integer", 3.0, False),
        ("number", 3.5, True), ("number", 3, True), ("number", False, False), ("number", "3", False),
        ("string", "a", True), ("string", 1, False),
        ("array", [], True), ("array", {}, False),
        ("object", {}, True), ("object", [], False),
    ]
    for name, value, ok in cases:
        assert (check(value, {"type": name}) == []) is ok, (name, value)


def test_type_list_and_message():
    assert check(None, {"type": ["number", "null"]}) == []
    out = check("x", {"type": ["number", "null"]})
    assert out == [Violation("$", "expected type number|null, got str")]
    assert "got null" in check(None, {"type": "string"})[0].message
    assert "got boolean" in check(True, {"type": "string"})[0].message


def test_const_and_enum_are_type_strict():
    assert check(True, {"const": True}) == []
    assert check(1, {"const": True}) != []
    assert check(2, {"const": 2}) == []
    assert check(3, {"const": 2}) != []
    assert check("a", {"enum": ["a", "b"]}) == []
    assert check("c", {"enum": ["a", "b"]}) != []
    assert check(1, {"enum": [True]}) != []
    assert check(None, {"enum": [6, 7, None]}) == []


def test_string_keywords():
    node = {"type": "string", "minLength": 2, "maxLength": 3, "pattern": "^a+$"}
    assert check("aa", node) == []
    assert check("a", node)[0].message.startswith("shorter")
    assert check("aaaa", node)[0].message.startswith("longer")
    assert check("ab", node)[0].message.startswith("does not match")


def test_unanchored_pattern_searches():
    assert check("xxbxx", {"pattern": "b"}) == []
    assert check("xxxx", {"pattern": "b"}) != []


def test_dollar_anchor_does_not_accept_a_trailing_newline():
    node = {"type": "string", "pattern": "^abc$"}
    assert check("abc", node) == []
    assert check("abc\n", node) != []


def test_number_keywords():
    node = {"type": "number", "minimum": 1, "maximum": 3, "exclusiveMinimum": 0.5}
    assert check(2, node) == []
    assert check(0.5, node) != [] and "below minimum" in check(0.5, node)[0].message
    assert "above maximum" in check(4, node)[0].message
    assert "must be above" in check(0.5, {"exclusiveMinimum": 0.5})[0].message
    assert check(0.6, {"exclusiveMinimum": 0.5}) == []


def test_array_keywords_and_paths():
    node = {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "integer"}}
    assert check([1], node) == []
    assert "fewer" in check([], node)[0].message
    assert "more" in check([1, 2, 3], node)[0].message
    assert check([1, "x"], node) == [Violation("$[1]", "expected type integer, got str")]


def test_object_keywords_and_paths():
    node = {
        "type": "object", "required": ["a"], "additionalProperties": False,
        "properties": {"a": {"type": "integer"}},
    }
    assert check({"a": 1}, node) == []
    assert check({}, node) == [Violation("$", "missing required key 'a'")]
    assert check({"a": 1, "b": 2}, node) == [Violation("$.b", "unexpected key")]
    assert check({"a": "x"}, node) == [Violation("$.a", "expected type integer, got str")]
    open_node = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert check({"x": 1}, open_node) == []
    assert check({"x": "s"}, open_node) != []
    assert check({"x": "s"}, {"type": "object"}) == []  # no additionalProperties keyword: anything goes


def test_property_names():
    node = {"type": "object", "propertyNames": {"pattern": "^k[0-9]$"}, "additionalProperties": {"type": "integer"}}
    assert check({"k1": 1}, node) == []
    out = check({"bad": 1}, node)
    assert out[0].path == "$.bad" and out[0].message.startswith("key ")


def test_one_of_requires_exactly_one_match():
    node = {"oneOf": [{"type": "integer"}, {"type": "number", "minimum": 5}]}
    assert check("x", node)[0].message.endswith("matched 0")
    assert check(2, node) == []
    assert check(7, node)[0].message.endswith("matched 2")


def test_ref_resolution_local_and_cross_file():
    assert check("0" * 64, {"$ref": "#/$defs/sha256"}) == []
    assert check("zz", {"$ref": "#/$defs/sha256"}) != []
    out = Validator("rigor-record-1.json")._check("zz", {"$ref": "rigor-common-1.json#/$defs/sha256"}, "rigor-record-1.json", "$")
    assert out != []


def test_validate_with_pointer_and_root():
    v = Validator("rigor-fragment-1.json")
    assert v.validate({}, "/$defs/assay_lane") != []
    assert v.validate({}) != []  # root oneOf: no alternative matches


def test_violation_str():
    assert str(Violation("$.a", "bad")) == "$.a: bad"


def test_schema_documents_are_valid_json_with_urns():
    ids = {name: load_schema(name)["$id"] for name in SCHEMA_FILES}
    assert ids == {
        "rigor-common-1.json": "urn:vbpub:rigor-common:1",
        "rigor-fragment-1.json": "urn:vbpub:rigor-fragment:1",
        "rigor-record-1.json": "urn:vbpub:rigor-record:1",
        "rigor-waivers-1.json": "urn:vbpub:rigor-waivers:1",
    }
    json.dumps(load_schema(COMMON))


def test_one_of_reports_the_closest_alternative_only():
    obj = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"type": "integer"}}}
    other = {"type": "object", "additionalProperties": False, "required": ["b"], "properties": {"b": {}}}
    node = {"oneOf": [{"type": "null"}, obj]}
    # null-or-object reports the object's own violation
    assert check({"a": "x"}, node) == [Violation("$.a", "expected type integer, got str")]
    # two plausible alternatives: the one with strictly fewer violations is reported
    assert check({"a": "x"}, {"oneOf": [obj, other]}) == [Violation("$.a", "expected type integer, got str")]
    # a tie is ambiguous, so only the generic message appears
    tie = check({}, {"oneOf": [obj, other]})
    assert tie[0].message.endswith("matched 0") and len(tie) == 1
    # a valid value is untouched
    assert check(None, node) == [] and check({"a": 1}, node) == []
