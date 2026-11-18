"""KI-161 / KI-177: ``versions resolve`` and ``versions init`` edit only what they own.

The [versions] writers change the values and tables they own and leave every other byte of
``cmru.toml`` identical (comments above, between and below the version tables, blank-line
runs, multi-line string values, key order). When a target cannot be edited as text they
refuse and name the edit to make by hand; they never rewrite the whole region.
"""
from __future__ import annotations

import json
import tomllib
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cmru import versions
from tests.test_versions import _estate, _result

RELEASED = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _cmru_text(versions_block: str, tail: str = "") -> str:
    """A loadable project config: marker comments, a multi-line value with blank lines."""
    return f'''schema_version = 1
# hand-written comment before everything

[runtime]
kind = "none"

[project]
id = "demo"
description = """first line


third line, two blank lines above
[not.a.header]
"""
prefix = "demo-v"
artifacts = ["wheel"]
template_revision = 4

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

{versions_block}{tail}
[steps.run-tests]
quiet = true
commands = [{{ label = "test", argv = ["true"], cwd = "." }}]

[steps.build]
quiet = true
commands = [{{ label = "build", argv = ["true"], cwd = "." }}]

[steps.push]
quiet = true
commands = [{{ label = "push", argv = ["true"], cwd = "." }}]
'''


VERSIONS_BLOCK = '''# comment above the versions region
[versions]
age_window_days = 14   # the window

# marker above a target, between two versions tables
[versions.targets."pypi.requests"]
mode = "single"
constraint = ">=2.31.0,<3.0.0"

# comment on the source table
[versions.targets."pypi.requests".pypi]
name = "requests"
registry = "https://pypi.org"

[versions.targets."pypi.requests".resolved]
# comment inside the owned table
version = "2.31.0"  # trailing comment on an owned value
resolved_at = "2026-08-01T00:00:00Z"
age_cutoff = "2026-07-18T00:00:00Z"
provenance = "old override"

[versions.targets."pypi.requests".resolved.sources.pypi]
version = "2.31.0"
released_at = "2026-07-30T00:00:00Z"
age_source = "fixture"

# note about the second target
[versions.targets."pypi.flask"]
mode = "single"
constraint = ">=3.0.0"

[versions.targets."pypi.flask".pypi]
name = "flask"
registry = "https://pypi.org"
'''
COMMENT_BEFORE_OTHER_TABLE = "\n# comment directly above a non-versions table that follows the region"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cmru.toml"
    path.write_bytes(text.encode("utf-8"))
    return path


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def test_resolve_changes_only_owned_values_and_keeps_every_other_byte(tmp_path):
    original = _cmru_text(VERSIONS_BLOCK, COMMENT_BEFORE_OTHER_TABLE)
    path = _write(tmp_path, original)
    results = {
        "pypi.requests": _result("pypi.requests", "pypi", "2.32.0", RELEASED),
        "pypi.flask": _result("pypi.flask", "pypi", "3.1.0", RELEASED),
    }

    versions._apply_resolved_state(path, results)

    expected = original
    expected = _replace_once(expected, 'version = "2.31.0"  # trailing comment on an owned value',
                             'version = "2.32.0"  # trailing comment on an owned value')
    expected = _replace_once(expected, 'resolved_at = "2026-08-01T00:00:00Z"', 'resolved_at = "2026-09-24T00:00:00Z"')
    expected = _replace_once(expected, 'age_cutoff = "2026-07-18T00:00:00Z"', 'age_cutoff = "2026-09-10T00:00:00Z"')
    expected = _replace_once(expected, 'provenance = "old override"\n', "")
    expected = _replace_once(
        expected,
        'version = "2.31.0"\nreleased_at = "2026-07-30T00:00:00Z"\nage_source = "fixture"\n',
        'version = "2.32.0"\nreleased_at = "2026-09-01T00:00:00Z"\nage_source = "fixture"\n',
    )
    flask_anchor = '[versions.targets."pypi.flask".pypi]\nname = "flask"\nregistry = "https://pypi.org"\n'
    expected = _replace_once(expected, flask_anchor, flask_anchor + (
        '\n[versions.targets."pypi.flask".resolved]\n'
        'version = "3.1.0"\nresolved_at = "2026-09-24T00:00:00Z"\nage_cutoff = "2026-09-10T00:00:00Z"\n'
        '\n[versions.targets."pypi.flask".resolved.sources.pypi]\n'
        'version = "3.1.0"\nreleased_at = "2026-09-01T00:00:00Z"\nage_source = "fixture"\n'
    ))
    assert path.read_text(encoding="utf-8") == expected
    parsed = tomllib.loads(expected)
    assert parsed["project"]["description"].count("\n\n\n") == 1
    assert parsed["versions"]["targets"]["pypi.flask"]["resolved"]["version"] == "3.1.0"


def test_resolve_with_unchanged_values_leaves_the_file_untouched(tmp_path):
    original = _cmru_text(VERSIONS_BLOCK, COMMENT_BEFORE_OTHER_TABLE)
    path = _write(tmp_path, original)
    results = {"pypi.requests": _result("pypi.requests", "pypi", "2.32.0", RELEASED)}
    versions._apply_resolved_state(path, results)
    first = path.read_bytes()
    inode = path.stat().st_ino
    versions._apply_resolved_state(path, results)
    assert path.read_bytes() == first and path.stat().st_ino == inode


def _advisory_block() -> str:
    parts = ["[versions]\nage_window_days = 14\n"]
    for index in range(8):
        parts.append(f'''
# ADVISORY: not consumed by the build (MDT-013), target {index}.
# Second advisory line {index}.
[versions.targets."oci.t{index}"]
mode = "single"
constraint = "*"

[versions.targets."oci.t{index}".oci]
image = "ghcr.io/acme/t{index}"
tag = "{{version}}"

[versions.targets."oci.t{index}".resolved]
version = "1.0.0"
resolved_at = "2026-08-01T00:00:00Z"
age_cutoff = "2026-07-18T00:00:00Z"

[versions.targets."oci.t{index}".resolved.sources]

[versions.targets."oci.t{index}".resolved.sources.oci]
version = "1.0.0"
released_at = "2026-07-01T00:00:00Z"
age_source = "oci-registry-last-modified"
tag = "1.0.0"
''')
    return "".join(parts)


def test_mdt_cockpit_shape_keeps_all_eight_advisory_comments(tmp_path):
    original = _cmru_text(_advisory_block())
    path = _write(tmp_path, original)
    results = {
        f"oci.t{index}": _result(f"oci.t{index}", "oci", "1.1.0", RELEASED, tag="1.1.0")
        for index in range(8)
    }
    versions._apply_resolved_state(path, results)
    rewritten = path.read_text(encoding="utf-8")

    def kept(text: str, prefix: str) -> list[str]:
        return [line for line in text.splitlines() if line.lstrip().startswith(prefix)]

    assert len([line for line in kept(rewritten, "#") if "ADVISORY" in line]) == 8
    assert kept(rewritten, "#") == kept(original, "#")
    assert kept(rewritten, "[") == kept(original, "[")
    assert [line for line in rewritten.splitlines() if not line.strip()] == [
        line for line in original.splitlines() if not line.strip()
    ]
    changed = {
        line.split(" = ")[0]
        for line in set(rewritten.splitlines()) ^ set(original.splitlines())
    }
    assert changed == {"version", "resolved_at", "age_cutoff", "released_at", "age_source", "tag"}
    assert tomllib.loads(rewritten)["versions"]["targets"]["oci.t7"]["resolved"]["version"] == "1.1.0"


def test_versions_init_adds_only_new_tables_and_keeps_comments(tmp_path):
    text = _cmru_text(VERSIONS_BLOCK.split("# note about the second target")[0], COMMENT_BEFORE_OTHER_TABLE)
    root_config, project_root = _estate(tmp_path)
    project_path = project_root / "cmru.toml"
    project_path.write_bytes(text.encode("utf-8"))
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\nflask>=3.0.0\n", encoding="utf-8")
    forge = versions.load_forge_config(root_config)

    summary = versions._versions_init(forge, ["demo"], dry_run=False)

    assert any("added pypi.flask" in line for line in summary)
    anchor = 'age_source = "fixture"\n'
    assert text.count(anchor) == 1
    expected = text.replace(anchor, anchor + (
        '\n[versions.targets."pypi.flask"]\nmode = "single"\nconstraint = ">=3.0.0"\n'
        '\n[versions.targets."pypi.flask".pypi]\nname = "flask"\nregistry = "https://pypi.org"\n'
    ))
    assert project_path.read_text(encoding="utf-8") == expected


def test_versions_init_dry_run_and_no_new_targets_write_nothing(tmp_path):
    text = _cmru_text(VERSIONS_BLOCK, COMMENT_BEFORE_OTHER_TABLE)
    root_config, project_root = _estate(tmp_path)
    project_path = project_root / "cmru.toml"
    project_path.write_bytes(text.encode("utf-8"))
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\nflask>=3.0.0\n", encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    versions._versions_init(forge, ["demo"], dry_run=True)
    versions._versions_init(forge, ["demo"], dry_run=False)
    assert project_path.read_bytes() == text.encode("utf-8")


def test_init_creates_the_versions_region_at_the_end_of_a_file_without_one(tmp_path):
    text = _cmru_text("")
    root_config, project_root = _estate(tmp_path)
    project_path = project_root / "cmru.toml"
    project_path.write_bytes(text.encode("utf-8"))
    (project_root / "requirements.in").write_text("flask>=3.0.0\n", encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    versions._versions_init(forge, ["demo"], dry_run=False)
    written = project_path.read_text(encoding="utf-8")
    assert written.startswith(text)
    assert written[len(text):] == (
        '\n[versions.targets."pypi.flask"]\nmode = "single"\nconstraint = ">=3.0.0"\n'
        '\n[versions.targets."pypi.flask".pypi]\nname = "flask"\nregistry = "https://pypi.org"\n'
    )


@pytest.mark.parametrize(
    ("original", "expected_tail"),
    [
        ("", '[versions.targets.a.pypi]\nname = "a"\n'),
        ("x = 1", '\n[versions.targets.a.pypi]\nname = "a"\n'),
        ("x = 1\n", '\n[versions.targets.a.pypi]\nname = "a"\n'),
        ("x = 1\n\n", '[versions.targets.a.pypi]\nname = "a"\n'),
    ],
)
def test_new_versions_region_is_appended_with_one_separating_blank_line(tmp_path, original, expected_tail):
    path = tmp_path / "cmru.toml"
    path.write_bytes(original.encode("utf-8"))
    versions._update_versions_file(path, lambda _old: {"targets": {"a": {"pypi": {"name": "a"}}}})
    written = path.read_text(encoding="utf-8")
    separator = "" if original.endswith("\n") or not original else "\n"
    assert written == original + separator + expected_tail


def test_crlf_files_keep_their_line_endings(tmp_path):
    original = _cmru_text(VERSIONS_BLOCK, COMMENT_BEFORE_OTHER_TABLE).replace("\n", "\r\n")
    path = _write(tmp_path, original)
    versions._apply_resolved_state(path, {
        "pypi.requests": _result("pypi.requests", "pypi", "2.32.0", RELEASED),
        "pypi.flask": _result("pypi.flask", "pypi", "3.1.0", RELEASED),
    })
    written = path.read_bytes().decode("utf-8")
    assert "\n" not in written.replace("\r\n", "")
    assert written.count("\r\n") > original.count("\r\n")
    assert '\r\n[versions.targets."pypi.flask".resolved]\r\nversion = "3.1.0"\r\n' in written
    assert 'version = "2.32.0"  # trailing comment on an owned value\r\n' in written


_SCANNER_TEXT = '''a = "x # not a comment \\" still"   # real comment
b = 'lit # not a comment'
c = """ml basic "quoted" \\""" escaped
[not.a.header] in a string
"""   # closing comment
d = \'\'\'ml literal
[nope]
\'\'\'
e = [
  "[x]",
  [1, 2],
]
f = { k = "]", j = [1] }
g = """"""
h = """a""""
"quoted key" = 1
dotted.key = 2
[ spaced . "q.t" ]
k = 1
[[arr]]
z = 1
# comment only

[after]
'''


def test_toml_statement_scanner_understands_strings_arrays_and_headers():
    assert tomllib.loads(_SCANNER_TEXT)["h"] == 'a"'
    lines = versions._split_lines(_SCANNER_TEXT)
    statements = versions._toml_statements(lines)
    shape = [(s.kind, s.key or s.path, s.first, s.last) for s in statements if s.kind != "other"]
    assert shape == [
        ("key", "a", 0, 0), ("key", "b", 1, 1), ("key", "c", 2, 4), ("key", "d", 5, 7),
        ("key", "e", 8, 11), ("key", "f", 12, 12), ("key", "g", 13, 13), ("key", "h", 14, 14),
        ("key", "quoted key", 15, 15),
        ("table", ("spaced", "q.t"), 17, 17), ("key", "k", 18, 18),
        ("array-table", (), 19, 19), ("key", "z", 20, 20), ("table", ("after",), 23, 23),
    ]
    dotted = [s for s in statements if s.first == 16]
    assert dotted and dotted[0].kind == "other"
    first = statements[0]
    assert lines[0][first.value_span[0]:first.value_span[1]] == '"x # not a comment \\" still"'


def test_split_lines_keeps_every_byte_and_only_breaks_on_newline():
    text = "a = 1\r\nb = ' '\nc"
    lines = versions._split_lines(text)
    assert lines == ["a = 1\r\n", "b = ' '\n", "c"]
    assert "".join(lines) == text
    assert versions._split_lines("") == []
    assert versions._split_lines("x\n") == ["x\n"]


def test_unterminated_trailing_value_does_not_loop():
    statements = versions._toml_statements(["a = [\n", "1,\n"])
    assert [(s.kind, s.first, s.last) for s in statements] == [("key", 0, 1)]


# ---- KI-176: every stale `*` npm target is reported in one run -------------------------


def _npm_declaration(name: str) -> dict:
    return {"npm": {"name": name, "registry": "https://registry.npmjs.org"}}


def _stale_npm_project(tmp_path: Path) -> tuple[Path, dict, dict]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "package.json").write_text(json.dumps({
        "dependencies": {"alpha": "1.0.0", "unrelated": "9.9.9"},
        "optionalDependencies": {"beta": "2.0.0"},
        "overrides": {"alpha": "1.0.0", "gamma": "0.1.0"},
    }), encoding="utf-8")
    results = {
        "npm.alpha": _result("npm.alpha", "npm", "1.1.0", RELEASED),
        "npm.beta": _result("npm.beta", "npm", "2.1.0", RELEASED),
    }
    declarations = {"npm.alpha": _npm_declaration("alpha"), "npm.beta": _npm_declaration("beta")}
    return project_root, results, declarations


def test_all_stale_star_npm_targets_are_reported_with_the_exact_package_json_edit(tmp_path):
    project_root, results, declarations = _stale_npm_project(tmp_path)
    with pytest.raises(versions.VersionsError) as failure:
        versions._validate_manifest_target_compatibility(project_root, results, declarations)
    message = str(failure.value)
    assert message.count("manifest requires") == 2
    assert "npm.alpha" in message and "npm.beta" in message
    assert "alpha@1.1.0" in message and "beta@2.1.0" in message
    assert 'set "dependencies"."alpha" from "1.0.0" to "1.1.0"' in message
    assert 'set "overrides"."alpha" from "1.0.0" to "1.1.0"' in message
    assert 'set "optionalDependencies"."beta" from "2.0.0" to "2.1.0"' in message
    assert "unrelated" not in message and "gamma" not in message
    assert "hand-owned" in message and "nothing was written" in message

    document = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    document["dependencies"]["alpha"] = "1.1.0"
    document["overrides"]["alpha"] = "1.1.0"
    document["optionalDependencies"]["beta"] = "2.1.0"
    (project_root / "package.json").write_text(json.dumps(document), encoding="utf-8")
    versions._validate_manifest_target_compatibility(project_root, results, declarations)


def test_a_single_mismatch_and_non_npm_targets_use_the_same_aggregate_report(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "requirements.in").write_text("requests>=2.0.0,<3.0.0\n", encoding="utf-8")
    (project_root / "package.json").write_text(json.dumps({"dependencies": {"alpha": "1.0.0"}}), encoding="utf-8")
    results = {
        "pypi.requests": _result("pypi.requests", "pypi", "1.0.0", RELEASED),
        "npm.alpha": _result("npm.alpha", "npm", "1.1.0", RELEASED),
    }
    declarations = {
        "pypi.requests": {"pypi": {"name": "requests", "registry": "https://pypi.org"}},
        "npm.alpha": _npm_declaration("alpha"),
    }
    with pytest.raises(versions.VersionsError) as failure:
        versions._validate_manifest_target_compatibility(project_root, results, declarations)
    message = str(failure.value)
    assert message.count("manifest requires") == 2
    assert 'set "dependencies"."alpha" from "1.0.0" to "1.1.0"' in message
    assert "update the manifest entry for requests" in message


def test_resolve_stops_before_writing_when_manifest_pins_are_stale(tmp_path, monkeypatch):
    local_versions = '''[versions]
age_window_days = 14

[versions.targets."npm.alpha"]
mode = "single"
constraint = "*"

[versions.targets."npm.alpha".npm]
name = "alpha"
registry = "https://registry.npmjs.org"

[versions.targets."npm.beta"]
mode = "single"
constraint = "*"

[versions.targets."npm.beta".npm]
name = "beta"
registry = "https://registry.npmjs.org"
'''
    root_config, project_root = _estate(tmp_path, project_versions=local_versions)
    (project_root / "package.json").write_text(json.dumps({
        "dependencies": {"alpha": "1.0.0", "beta": "2.0.0"},
    }), encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    results = {
        "npm.alpha": _result("npm.alpha", "npm", "1.1.0", RELEASED),
        "npm.beta": _result("npm.beta", "npm", "2.1.0", RELEASED),
    }
    monkeypatch.setattr(
        versions, "_resolve_targets",
        lambda targets, **_kwargs: {key: results[key] for key in targets if key in results},
    )
    context = types.SimpleNamespace(config_kind="project", config_path=project_root / "cmru.toml")
    before = (project_root / "cmru.toml").read_bytes()
    package_before = (project_root / "package.json").read_bytes()
    for dry_run in (False, True):
        with pytest.raises(versions.VersionsError) as failure:
            versions._run_resolve(forge, context, ["demo"], dry_run=dry_run)
        assert "npm.alpha" in str(failure.value) and "npm.beta" in str(failure.value)
    assert (project_root / "cmru.toml").read_bytes() == before
    assert (project_root / "package.json").read_bytes() == package_before
