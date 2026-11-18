"""KI-161 / KI-177: edge shapes of the surgical [versions] writer, including every refusal."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from cmru import versions

_HEAD = '[versions.targets."a"]\nmode = "single"\nconstraint = "*"  # drop with the key\n\n# about pypi\n[versions.targets."a".pypi]\nname = "a"\n\n'
_RESOLVED_ROWS = (
    'version = "1"\nresolved_at = "2026-09-01T00:00:00Z"\nage_cutoff = "2026-08-18T00:00:00Z"\n'
)
_SOURCE_ROWS = 'version = "1"\nreleased_at = "2026-08-01T00:00:00Z"\nage_source = "f"\n'
_RESOLVED = (
    f'[versions.targets."a".resolved]\n{_RESOLVED_ROWS}\n'
    f'[versions.targets."a".resolved.sources.pypi]\n{_SOURCE_ROWS}'
)
_INLINE_SOURCES = '{ pypi = { version = "1", released_at = "2026-08-01T00:00:00Z", age_source = "f" } }'


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cmru.toml"
    path.write_bytes(text.encode("utf-8"))
    return path


def _bump_version(old):
    old["targets"]["a"]["resolved"]["version"] = "2"
    return old


def _refusal(tmp_path, text, mutate) -> str:
    path = _write(tmp_path, text)
    with pytest.raises(versions.VersionsError) as failure:
        versions._update_versions_file(path, mutate)
    assert path.read_bytes() == text.encode("utf-8")
    return str(failure.value)


def test_missing_trailing_newline_and_edits_at_the_last_line(tmp_path):
    original = _HEAD + _RESOLVED.rstrip("\n")
    path = _write(tmp_path, original)

    def mutate(old):
        old["targets"]["a"]["resolved"]["sources"]["pypi"]["age_source"] = "g"
        old["targets"]["a"]["resolved"]["sources"]["pypi"]["tag"] = "t"
        return old

    versions._update_versions_file(path, mutate)
    assert path.read_text(encoding="utf-8") == original.replace('age_source = "f"', 'age_source = "g"\ntag = "t"') + "\n"


def test_a_key_is_added_after_the_last_entry_of_an_existing_table(tmp_path):
    original = '[[arr]]\nz = 1\n\n' + _HEAD + _RESOLVED
    path = _write(tmp_path, original)

    def add_provenance(old):
        old["targets"]["a"]["resolved"]["provenance"] = "pinned"
        return old

    versions._update_versions_file(path, add_provenance)
    assert path.read_text(encoding="utf-8") == original.replace(
        'age_cutoff = "2026-08-18T00:00:00Z"\n', 'age_cutoff = "2026-08-18T00:00:00Z"\nprovenance = "pinned"\n',
    )


def test_a_table_added_next_to_an_inline_definition_is_refused(tmp_path):
    text = '[versions.targets]\na = { mode = "single" }\n'

    def add_resolved(old):
        old["targets"]["a"]["resolved"] = {
            "version": "1", "resolved_at": "2026-09-01T00:00:00Z", "age_cutoff": "2026-08-18T00:00:00Z",
            "sources": {"pypi": {"version": "1", "released_at": "2026-08-01T00:00:00Z", "age_source": "f"}},
        }
        return old

    assert "would not parse" in _refusal(tmp_path, text, add_resolved)


def test_an_inline_table_is_refused_with_the_edit_to_make_by_hand(tmp_path):
    text = (
        '[versions.targets."a"]\nmode = "single"\nresolved = { version = "1", '
        f'resolved_at = "2026-09-01T00:00:00Z", age_cutoff = "2026-08-18T00:00:00Z", sources = {_INLINE_SOURCES} }}\n'
        '[versions.targets."a".pypi]\nname = "a"\n'
    )
    message = _refusal(tmp_path, text, _bump_version)
    assert "[versions.targets.a.resolved]" in message and 'change version to "2"' in message
    assert "rewriting" in message and "no file was changed" in message


def test_dotted_keys_are_refused(tmp_path):
    text = (
        '[versions.targets."a"]\nmode = "single"\nresolved.version = "1"\n'
        'resolved.resolved_at = "2026-09-01T00:00:00Z"\nresolved.age_cutoff = "2026-08-18T00:00:00Z"\n'
        f"resolved.sources = {_INLINE_SOURCES}\n"
        '[versions.targets."a".pypi]\nname = "a"\n'
    )
    assert "[versions.targets.a.resolved]" in _refusal(tmp_path, text, _bump_version)


def test_a_multiline_value_that_would_be_replaced_is_refused(tmp_path):
    text = _HEAD + _RESOLVED.replace('version = "1"\nresolved_at', 'version = """\n1"""\nresolved_at', 1)
    assert tomllib.loads(text)["versions"]["targets"]["a"]["resolved"]["version"] == "1"
    message = _refusal(tmp_path, text, _bump_version)
    assert "plain single-line `key = value` entry" in message and "[versions.targets.a.resolved]" in message


def test_a_value_that_changes_between_table_and_scalar_is_refused(tmp_path):
    text = _HEAD + _RESOLVED
    path = _write(tmp_path, text)
    document = versions._load_document(path)
    changed = {"targets": {"a": {"resolved": "scalar"}}}
    with pytest.raises(versions.VersionsError, match="between a table and a value"):
        versions._edit_versions_text(path, text, document, changed)


def test_adding_a_key_to_a_table_without_a_header_is_refused(tmp_path):
    message = _refusal(
        tmp_path, '[versions.targets."a".pypi]\nname = "a"\n',
        lambda old: {**old, "age_window_days": 21},
    )
    assert "age_window_days" in message and "[versions]" in message


def test_deleting_a_table_written_without_a_header_is_refused(tmp_path):
    text = (
        '[versions.targets."a"]\nmode = "single"\nresolved = { version = "1", '
        f'resolved_at = "2026-09-01T00:00:00Z", age_cutoff = "2026-08-18T00:00:00Z", sources = {_INLINE_SOURCES} }}\n'
    )

    def drop_resolved(old):
        del old["targets"]["a"]["resolved"]
        return old

    assert "delete the table [versions.targets.a.resolved]" in _refusal(tmp_path, text, drop_resolved)


def test_deleting_a_table_and_a_key_removes_only_those_lines(tmp_path):
    npm_block = f'[versions.targets."a".resolved.sources.npm]\n{_SOURCE_ROWS}'
    original = (
        '# keep me\n' + _HEAD + _RESOLVED.replace('age_cutoff = "2026-08-18T00:00:00Z"\n', 'age_cutoff = "2026-08-18T00:00:00Z"\n'
        'provenance = "x"\n') + f"\n{npm_block}\n# after\n[other]\nx = 1\n"
    )
    path = _write(tmp_path, original)

    def mutate(old):
        del old["targets"]["a"]["constraint"]
        del old["targets"]["a"]["resolved"]["provenance"]
        del old["targets"]["a"]["resolved"]["sources"]["npm"]
        return old

    versions._update_versions_file(path, mutate)
    expected = original.replace('constraint = "*"  # drop with the key\n', "").replace('provenance = "x"\n', "")
    expected = expected.replace(npm_block, "")
    assert path.read_text(encoding="utf-8") == expected


def test_the_result_is_verified_and_a_wrong_edit_is_refused_not_written(tmp_path, monkeypatch):
    text = _HEAD + _RESOLVED
    path = _write(tmp_path, text)
    monkeypatch.setattr(versions, "_apply_edit", lambda _path, lines, _op: [*lines, "broken = [\n"])
    with pytest.raises(versions.VersionsError, match="would not parse"):
        versions._update_versions_file(path, _bump_version)
    monkeypatch.setattr(versions, "_apply_edit", lambda _path, lines, _op: lines)
    with pytest.raises(versions.VersionsError, match="would not match the requested values"):
        versions._update_versions_file(path, _bump_version)
    assert path.read_bytes() == text.encode("utf-8")


def test_header_spellings_and_empty_tables_are_understood(tmp_path):
    original = (
        '[ versions . targets . "a" ]  # spaced header\nmode = "single"\n'
        "[versions.targets.a.pypi]\nname = 'a'\n"
        '[versions.targets."a".resolved.sources]\n'
    )
    path = _write(tmp_path, original)

    def add_resolved(old):
        old["targets"]["a"]["resolved"] = {
            "version": "1", "resolved_at": "2026-09-01T00:00:00Z", "age_cutoff": "2026-08-18T00:00:00Z",
            "sources": {"pypi": {"version": "1", "released_at": "2026-08-01T00:00:00Z", "age_source": "f"}},
        }
        return old

    versions._update_versions_file(path, add_resolved)
    written = path.read_text(encoding="utf-8")
    assert written.startswith(original)
    assert tomllib.loads(written)["versions"]["targets"]["a"]["resolved"]["sources"]["pypi"]["version"] == "1"


def test_an_empty_header_for_a_table_to_be_added_is_refused_not_duplicated(tmp_path):
    original = _HEAD + '[versions.targets."a".resolved]\n'
    path = _write(tmp_path, original)

    def add_resolved(old):
        old["targets"]["a"]["resolved"] = {
            "version": "1", "resolved_at": "2026-09-01T00:00:00Z", "age_cutoff": "2026-08-18T00:00:00Z",
            "sources": {"pypi": {"version": "1", "released_at": "2026-08-01T00:00:00Z", "age_source": "f"}},
        }
        return old

    with pytest.raises(versions.VersionsError, match="would not parse"):
        versions._update_versions_file(path, add_resolved)
    assert path.read_text(encoding="utf-8") == original
