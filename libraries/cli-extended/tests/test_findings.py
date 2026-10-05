from __future__ import annotations

import pytest

from cli_extended.findings import (
    Finding,
    FindingsError,
    FindingsFile,
    load_review_findings,
)

HEADER = 'schema_version = 1\ncli_id = "demo"\n'
OPEN = (
    '[[findings]]\nid = "{id}"\nstatus = "open"\nseverity = "{sev}"\n'
    'category = "help"\nsummary = "S {id}"\nremedy = "R {id}"\n'
)


def _write(tmp_path, text):
    path = tmp_path / "findings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_o3_valid_file_loads_every_field(tmp_path):
    path = _write(
        tmp_path,
        HEADER
        + OPEN.format(id="a.1", sev="major")
        + 'route = "route:entrypoint:demo/go"\n'
        + '[[findings]]\nid = "w"\nstatus = "wontfix"\nseverity = "note"\n'
        'category = "adoption"\nsummary = "s"\nrationale = "because"\n'
        + '[[findings]]\nid = "f"\nstatus = "fixed"\nseverity = "minor"\n'
        'category = "grammar"\nsummary = "s2"\n',
    )
    loaded = load_review_findings(path)
    assert loaded.cli_id == "demo"
    assert loaded.findings == (
        Finding("a.1", "open", "major", "help", "S a.1", "R a.1", "", "route:entrypoint:demo/go"),
        Finding("w", "wontfix", "note", "adoption", "s", "", "because", ""),
        Finding("f", "fixed", "minor", "grammar", "s2", "", "", ""),
    )


def test_o3_a_file_without_findings_is_valid(tmp_path):
    loaded = load_review_findings(_write(tmp_path, HEADER))
    assert loaded == FindingsFile("demo", ())
    assert loaded.open_findings() == ()


def test_open_findings_sort_by_severity_then_id_and_skip_closed(tmp_path):
    body = HEADER
    for identifier, severity in (("z", "blocker"), ("a", "note"), ("b", "major"), ("a2", "blocker"), ("m", "minor")):
        body += OPEN.format(id=identifier, sev=severity)
    body += '[[findings]]\nid = "closed"\nstatus = "fixed"\nseverity = "blocker"\ncategory = "help"\nsummary = "x"\n'
    ordered = [item.id for item in load_review_findings(_write(tmp_path, body)).open_findings()]
    assert ordered == ["a2", "z", "b", "m", "a"]


GOOD = OPEN.format(id="x", sev="major")


@pytest.mark.parametrize(
    "text, message",
    (
        ("schema_version = [", "is not valid TOML"),
        (HEADER + "extra = 1\n", "unknown top-level key 'extra'"),
        ('schema_version = 2\ncli_id = "d"\n', "must be the integer 1, got 2"),
        ('schema_version = true\ncli_id = "d"\n', "got True"),
        ("schema_version = 1\n", "missing required key 'cli_id'"),
        ('schema_version = 1\ncli_id = ""\n', "cli_id must be a non-empty string"),
        (HEADER + "findings = 3\n", "findings in .* must be an array of tables"),
        (HEADER + "findings = [1]\n", r"findings\[0\] must be a table"),
        (HEADER + GOOD + "bogus = 1\n", r"unknown key 'bogus' in findings\[0\]"),
        (HEADER + GOOD.replace('id = "x"\n', ""), "missing required key 'id'"),
        (HEADER + GOOD.replace('"x"', '"-x"', 1), "must match"),
        (HEADER + GOOD.replace('"x"', '"a b!"', 1), "must match"),
        (HEADER + GOOD.replace('"x"', '"ab!"', 1), "must match"),
        (HEADER + GOOD.replace('"open"', '"closed"'), "status must be one of open, fixed, wontfix, got 'closed'"),
        (HEADER + GOOD.replace('"major"', '"huge"'), "severity must be one of blocker, major, minor, note"),
        (HEADER + GOOD.replace('"help"', '"style"'), "category must be one of grammar, help"),
        (HEADER + GOOD.replace('summary = "S x"', 'summary = ""'), "summary must be a non-empty string"),
        (HEADER + GOOD.replace('remedy = "R x"\n', ""), "findings\\[0\\] is missing required key 'remedy'"),
        (HEADER + GOOD + "route = 3\n", "route must be a non-empty string"),
        (
            HEADER + '[[findings]]\nid = "x"\nstatus = "wontfix"\nseverity = "note"\n'
            'category = "help"\nsummary = "s"\n',
            "missing required key 'rationale'",
        ),
        (HEADER + GOOD + GOOD, "duplicate finding id 'x'"),
    ),
)
def test_o3_invalid_findings_files_are_refused(tmp_path, text, message):
    with pytest.raises(FindingsError, match=message):
        load_review_findings(_write(tmp_path, text))


def test_findings_error_is_a_value_error():
    assert issubclass(FindingsError, ValueError)


@pytest.mark.parametrize("key", ("id", "status", "severity", "category", "summary"))
def test_w8c_every_required_finding_key_is_refused_when_missing(tmp_path, key):
    lines = [line for line in GOOD.splitlines(keepends=True) if not line.startswith(f"{key} = ")]
    text = HEADER + "".join(lines)
    with pytest.raises(FindingsError) as caught:
        load_review_findings(_write(tmp_path, text))
    assert str(caught.value) == f"findings[0] is missing required key {key!r}"


def test_w8c_remedy_and_rationale_are_required_only_for_their_status(tmp_path):
    fixed = (
        '[[findings]]\nid = "f"\nstatus = "fixed"\nseverity = "note"\n'
        'category = "help"\nsummary = "s"\n'
    )
    wontfix = (
        '[[findings]]\nid = "w"\nstatus = "wontfix"\nseverity = "note"\n'
        'category = "help"\nsummary = "s"\nrationale = "because"\n'
    )
    loaded = load_review_findings(_write(tmp_path, HEADER + GOOD + fixed + wontfix))
    by_id = {item.id: item for item in loaded.findings}
    assert (by_id["x"].remedy, by_id["x"].rationale, by_id["x"].route) == ("R x", "", "")
    assert (by_id["f"].remedy, by_id["f"].rationale) == ("", "")
    assert (by_id["w"].remedy, by_id["w"].rationale) == ("", "because")
    open_without_remedy = HEADER + GOOD.replace('remedy = "R x"\n', "")
    with pytest.raises(FindingsError, match="missing required key 'remedy'"):
        load_review_findings(_write(tmp_path, open_without_remedy))
    wontfix_without = HEADER + wontfix.replace('rationale = "because"\n', "")
    with pytest.raises(FindingsError, match="missing required key 'rationale'"):
        load_review_findings(_write(tmp_path, wontfix_without))


def test_w8c_an_optional_route_may_be_omitted_and_is_kept_when_given(tmp_path):
    loaded = load_review_findings(_write(tmp_path, HEADER + GOOD + GOOD.replace('"x"', '"y"') + 'route = "route:r"\n'))
    assert [item.route for item in loaded.findings] == ["", "route:r"]
