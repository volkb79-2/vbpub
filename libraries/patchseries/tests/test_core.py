from __future__ import annotations

from pathlib import Path

import pytest
from patchseries import (
    SeriesError,
    check,
    closure,
    delta,
    parse,
    render,
    series_digest,
)

VOCABULARY = {
    "capabilities": {"placement", "scope-control", "logging"},
    "roles": {"enable", "guard", "plumbing"},
    "risks": {"low", "medium", "high"},
}


def patch_text(
    change_id: str,
    *,
    subject: str | None = None,
    capability: str = "placement",
    role: str = "enable",
    risk: str = "medium",
    requires: str = "none",
    body: str = "-before\n+after\n",
    headers: str | None = None,
    filename: str = "pkg/file.go",
) -> str:
    subject = subject or f"Implement {change_id}"
    if headers is None:
        headers = (
            f"Change-Id: {change_id}\n"
            f"Capability: {capability}\n"
            f"Role: {role}\n"
            f"Risk: {risk}\n"
            f"Requires: {requires}\n"
        )
    return (
        "From 0123456789abcdef0123456789abcdef01234567 Mon Sep 17 00:00:00 2001\n"
        "From: Patch Author <author@example.test>\n"
        "Date: Tue, 1 Jan 2030 00:00:00 +0000\n"
        f"Subject: [PATCH] {subject}\n\n"
        f"{subject} body.\n\n"
        f"{headers}\n"
        "---\n"
        " pkg/file.go | 2 +-\n"
        " 1 file changed, 1 insertion(+), 1 deletion(-)\n"
        f"diff --git a/{filename} b/{filename}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{filename}\n"
        f"+++ b/{filename}\n"
        "@@ -1 +1 @@\n"
        f"{body}"
    )


def write_series(root: Path, entries: list[tuple[str, str]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for filename, content in entries:
        (root / filename).write_text(content, encoding="utf-8")
    return root


def sample_series(tmp_path: Path) -> Path:
    return write_series(
        tmp_path / "series",
        [
            ("0002-child.patch", patch_text("child", requires="base")),
            ("0001-base.patch", patch_text("base")),
        ],
    )


def test_parse_orders_patches_and_reads_metadata_and_diffstat(tmp_path: Path) -> None:
    root = sample_series(tmp_path)

    changes = parse(root)

    assert [item.change_id for item in changes] == ["base", "child"]
    assert changes[0].subject == "Implement base"
    assert changes[0].capability == "placement"
    assert changes[0].role == "enable"
    assert changes[0].risk == "medium"
    assert changes[0].requires == ()
    assert changes[0].files == ("pkg/file.go",)
    assert (changes[0].insertions, changes[0].deletions) == (1, 1)
    assert changes[1].requires == ("base",)


def test_parse_ignores_non_patch_files_and_accepts_empty_series(tmp_path: Path) -> None:
    root = write_series(tmp_path / "empty", [("notes.txt", "not a patch")])

    assert parse(root) == []


def test_parse_requires_a_series_directory(tmp_path: Path) -> None:
    with pytest.raises(SeriesError, match="not a directory"):
        parse(tmp_path / "missing")


def test_parse_rejects_patch_without_subject_or_diff(tmp_path: Path) -> None:
    root = write_series(tmp_path / "bad", [("0001-bad.patch", "not a patch")])

    with pytest.raises(SeriesError, match="Subject"):
        parse(root)

    root = write_series(
        tmp_path / "no-diff",
        [("0001-no-diff.patch", "Subject: plain subject\n\n---\nbody only\n")],
    )
    with pytest.raises(SeriesError, match="no supported file-change metadata"):
        parse(root)

    root = write_series(
        tmp_path / "no-separator",
        [("0001-no-separator.patch", "Subject: Present subject\nno separator\n")],
    )
    with pytest.raises(SeriesError, match="no supported file-change metadata"):
        parse(root)


def test_parse_handles_plain_subject_new_files_repeated_hunks_and_leading_plus(
    tmp_path: Path,
) -> None:
    patch = (
        "Subject: Plain subject\n\n"
        "Change-Id: plain\nCapability: placement\nRole: enable\n"
        "Risk: low\nRequires: none\n\n---\n"
        "diff --git a/src/new.go b/src/new.go\n"
        "--- /dev/null\n+++ b/src/new.go\n@@ -0,0 +1 @@\n+++literal\n"
        "diff --git a/src/new.go b/src/new.go\n"
        "--- a/src/new.go\n+++ b/src/new.go\n@@ -1 +1 @@\n---literal\n"
    )
    root = write_series(tmp_path / "plain", [("0001-plain.patch", patch)])

    change = parse(root)[0]

    assert change.subject == "Plain subject"
    assert change.files == ("src/new.go",)
    assert (change.insertions, change.deletions) == (1, 1)

    fallback = patch_text("fallback", filename="plain-path")
    fallback = fallback.replace("a/plain-path", "plain-path").replace(
        "b/plain-path", "plain-path"
    )
    fallback_root = write_series(
        tmp_path / "fallback", [("0001-fallback.patch", fallback)]
    )
    assert parse(fallback_root)[0].files == ("plain-path",)


def test_parse_tracks_deleted_files_and_refuses_a_diff_without_a_real_path(
    tmp_path: Path,
) -> None:
    deleted = patch_text("deleted")
    deleted = deleted.replace("+++ b/pkg/file.go", "+++ /dev/null")
    deleted_root = write_series(tmp_path / "deleted", [("0001-deleted.patch", deleted)])
    assert parse(deleted_root)[0].files == ("pkg/file.go",)

    no_file = patch_text("no-file")
    no_file = no_file.replace("--- a/pkg/file.go", "--- /dev/null")
    no_file = no_file.replace("+++ b/pkg/file.go", "+++ /dev/null")
    no_file_root = write_series(tmp_path / "no-file", [("0001-no-file.patch", no_file)])
    with pytest.raises(SeriesError, match="no supported file-change metadata"):
        parse(no_file_root)


def test_parse_accepts_rename_only_and_mode_only_format_patches(
    tmp_path: Path,
) -> None:
    headers = (
        "Change-Id: change\nCapability: placement\nRole: enable\n"
        "Risk: low\nRequires: none\n\n---\n"
    )
    rename = (
        "Subject: Rename file\n\n"
        + headers
        + "diff --git a/old/path b/new/path\n"
        + "similarity index 100%\n"
        + "rename from old/path\n"
        + "rename to new/path\n"
    )
    mode = (
        "Subject: Make script executable\n\n"
        + headers
        + "diff --git a/bin/tool b/bin/tool\n"
        + "old mode 100644\n"
        + "new mode 100755\n"
    )
    root = write_series(
        tmp_path / "metadata-only",
        [("0001-rename.patch", rename), ("0002-mode.patch", mode)],
    )

    changes = parse(root)

    assert changes[0].files == ("new/path",)
    assert changes[1].files == ("bin/tool",)
    assert (changes[0].insertions, changes[0].deletions) == (0, 0)
    assert (changes[1].insertions, changes[1].deletions) == (0, 0)

    rename_with_hunks = rename + (
        "--- a/old/path\n"
        "+++ b/new/path\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
    )
    with_hunks = write_series(
        tmp_path / "rename-with-hunks",
        [("0001-rename.patch", rename_with_hunks)],
    )
    assert parse(with_hunks)[0].files == ("new/path",)

    multiple_files = rename + (
        "diff --git a/second/file b/second/file\n"
        "--- a/second/file\n"
        "+++ b/second/file\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
    )
    multiple_root = write_series(
        tmp_path / "rename-and-text",
        [("0001-multiple.patch", multiple_files)],
    )
    assert parse(multiple_root)[0].files == ("new/path", "second/file")

    spaced_rename = rename.replace(
        "rename from old/path\nrename to new/path\n",
        "rename from old path\nrename to new path\n",
    )
    spaced_root = write_series(
        tmp_path / "spaced-rename", [("0001-spaced.patch", spaced_rename)]
    )
    assert parse(spaced_root)[0].files == ("new path",)

    prefixed_rename = rename.replace(
        "rename from old/path\nrename to new/path\n",
        "rename from a/old/path\nrename to a/new/path\n",
    )
    prefixed_root = write_series(
        tmp_path / "prefixed-rename", [("0001-prefixed.patch", prefixed_rename)]
    )
    assert parse(prefixed_root)[0].files == ("a/new/path",)


def test_parse_rejects_binary_patch_with_specific_error(tmp_path: Path) -> None:
    binary = (
        "Subject: Binary asset\n\n"
        "Change-Id: binary\nCapability: placement\nRole: enable\n"
        "Risk: low\nRequires: none\n\n---\n"
        "diff --git a/image.bin b/image.bin\n"
        "index 1111111..2222222 100644\n"
        "GIT binary patch\n"
        "literal 3\nabc\n"
    )
    root = write_series(tmp_path / "binary", [("0001-binary.patch", binary)])

    with pytest.raises(SeriesError, match="binary patches are unsupported"):
        parse(root)

    binary_files = binary.replace(
        "GIT binary patch\nliteral 3\nabc\n",
        "Binary files a/image.bin and b/image.bin differ\n",
    )
    root = write_series(
        tmp_path / "binary-files", [("0001-binary.patch", binary_files)]
    )
    with pytest.raises(SeriesError, match="binary patches are unsupported"):
        parse(root)


def test_parse_reports_malformed_diff_and_rename_metadata(tmp_path: Path) -> None:
    malformed_header = patch_text("malformed-header").replace(
        "diff --git a/pkg/file.go b/pkg/file.go",
        'diff --git "unterminated b/pkg/file.go',
    )
    root = write_series(
        tmp_path / "malformed-header",
        [("0001-malformed.patch", malformed_header)],
    )
    with pytest.raises(SeriesError, match="invalid diff header"):
        parse(root)

    short_header = patch_text("short-header").replace(
        "diff --git a/pkg/file.go b/pkg/file.go", "diff --git "
    ).replace("--- a/pkg/file.go\n+++ b/pkg/file.go\n", "")
    root = write_series(
        tmp_path / "short-header", [("0001-short.patch", short_header)]
    )
    with pytest.raises(SeriesError, match="no supported file-change metadata"):
        parse(root)

    malformed_rename = (
        "Subject: Bad rename metadata\n\n"
        "Change-Id: rename\nCapability: placement\nRole: enable\n"
        "Risk: low\nRequires: none\n\n---\n"
        "diff --git a/old b/new\n"
        'rename from "unterminated\n'
        "rename to new\n"
    )
    root = write_series(
        tmp_path / "malformed-rename",
        [("0001-malformed-rename.patch", malformed_rename)],
    )
    with pytest.raises(SeriesError, match="invalid rename metadata"):
        parse(root)


def test_parse_rejects_invalid_utf8_and_empty_requires_items(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8"
    invalid_utf8.mkdir()
    (invalid_utf8 / "0001-invalid.patch").write_bytes(b"\xff")
    with pytest.raises(SeriesError, match="not UTF-8"):
        parse(invalid_utf8)

    root = write_series(
        tmp_path / "empty-requires",
        [("0001-empty.patch", patch_text("empty", requires="base,"))],
    )
    with pytest.raises(SeriesError, match="empty Requires"):
        parse(root)


def test_parse_marks_missing_requires_header_as_missing(tmp_path: Path) -> None:
    root = write_series(
        tmp_path / "no-requires",
        [
            (
                "0001-no-requires.patch",
                patch_text(
                    "no-requires",
                    headers=(
                        "Change-Id: no-requires\nCapability: placement\n"
                        "Role: enable\nRisk: low\n"
                    ),
                ),
            )
        ],
    )
    change = parse(root)

    assert change[0].requires is None
    with pytest.raises(SeriesError, match="Requires"):
        check(change, VOCABULARY)
    assert [item.change_id for item in closure(change, ["no-requires"])] == [
        "no-requires"
    ]


def test_parse_rejects_duplicate_single_value_trailer(tmp_path: Path) -> None:
    headers = (
        "Change-Id: one\nChange-Id: two\nCapability: placement\n"
        "Role: enable\nRisk: low\nRequires: none\n"
    )
    root = write_series(
        tmp_path / "duplicate",
        [("0001-duplicate.patch", patch_text("one", headers=headers))],
    )

    with pytest.raises(SeriesError, match="duplicate Change-Id"):
        parse(root)


def test_check_accepts_valid_series_and_empty_requires(tmp_path: Path) -> None:
    changes = parse(sample_series(tmp_path))

    assert check(changes, VOCABULARY) is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("Change-Id", "bad id", "invalid Change-Id"),
        ("Capability", "unknown", "unknown Capability"),
        ("Role", "unknown", "unknown Role"),
        ("Risk", "unknown", "unknown Risk"),
        ("Requires", "absent", "unresolved Requires"),
    ],
)
def test_check_rejects_invalid_values(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    values = {
        "Change-Id": "valid",
        "Capability": "placement",
        "Role": "enable",
        "Risk": "low",
        "Requires": "none",
    }
    values[field] = value
    headers = "".join(f"{key}: {item}\n" for key, item in values.items())
    root = write_series(
        tmp_path / "invalid",
        [("0001-invalid.patch", patch_text("valid", headers=headers))],
    )

    with pytest.raises(SeriesError, match=message):
        check(parse(root), VOCABULARY)


def test_check_requires_all_trailers_and_unique_ids(tmp_path: Path) -> None:
    missing = write_series(
        tmp_path / "missing",
        [
            (
                "0001-missing.patch",
                patch_text("missing", headers="Change-Id: missing\n"),
            )
        ],
    )
    with pytest.raises(SeriesError, match="missing required headers"):
        check(parse(missing), VOCABULARY)

    duplicate = write_series(
        tmp_path / "duplicate-ids",
        [
            ("0001-first.patch", patch_text("same")),
            ("0002-second.patch", patch_text("same")),
        ],
    )
    with pytest.raises(SeriesError, match="duplicate Change-Id"):
        check(parse(duplicate), VOCABULARY)


def test_check_rejects_duplicate_requires_and_dependency_cycles(tmp_path: Path) -> None:
    duplicated = write_series(
        tmp_path / "duplicate-requires",
        [
            ("0001-base.patch", patch_text("base")),
            (
                "0002-child.patch",
                patch_text("child", requires="base, base"),
            ),
        ],
    )
    with pytest.raises(SeriesError, match="duplicate Requires"):
        check(parse(duplicated), VOCABULARY)

    cyclic = write_series(
        tmp_path / "cycle",
        [
            ("0001-first.patch", patch_text("first", requires="second")),
            ("0002-second.patch", patch_text("second", requires="first")),
        ],
    )
    with pytest.raises(SeriesError, match="dependency cycle"):
        check(parse(cyclic), VOCABULARY)


def test_check_rejects_requirement_that_sorts_after_its_dependent(
    tmp_path: Path,
) -> None:
    out_of_order = parse(
        write_series(
            tmp_path / "out-of-order",
            [
                ("0001-child.patch", patch_text("child", requires="base")),
                ("0002-base.patch", patch_text("base")),
            ],
        )
    )

    with pytest.raises(SeriesError, match="required Change-Id 'base'.*before"):
        check(out_of_order, VOCABULARY)


def test_check_rejects_missing_vocabulary_categories(tmp_path: Path) -> None:
    changes = parse(sample_series(tmp_path))

    with pytest.raises(SeriesError, match="vocabulary.*roles"):
        check(changes, {"capabilities": {"placement"}, "risks": {"medium"}})


def test_check_accepts_empty_series() -> None:
    assert check([], VOCABULARY) is None


def test_closure_is_transitive_ordered_and_rejects_unknown_selection(
    tmp_path: Path,
) -> None:
    root = write_series(
        tmp_path / "closure",
        [
            ("0001-base.patch", patch_text("base")),
            ("0002-middle.patch", patch_text("middle", requires="base")),
            ("0003-leaf.patch", patch_text("leaf", requires="middle")),
        ],
    )
    changes = parse(root)

    assert [item.change_id for item in closure(changes, ["leaf", "base"])] == [
        "base",
        "middle",
        "leaf",
    ]
    assert closure(changes, []) == []
    with pytest.raises(SeriesError, match="unknown selected Change-Id"):
        closure(changes, ["missing"])
    assert [item.change_id for item in closure(changes, ["base"])] == ["base"]

    out_of_order = parse(
        write_series(
            tmp_path / "out-of-order-closure",
            [
                ("0001-child.patch", patch_text("child", requires="base")),
                ("0002-base.patch", patch_text("base")),
            ],
        )
    )
    assert [
        item.change_id for item in closure(out_of_order, ["child"])
    ] == ["base", "child"]

    multiple_requirements = parse(
        write_series(
            tmp_path / "multiple-requirements",
            [
                ("0001-first.patch", patch_text("first")),
                ("0002-second.patch", patch_text("second")),
                (
                    "0003-child.patch",
                    patch_text("child", requires="first, second"),
                ),
            ],
        )
    )
    assert [
        item.change_id
        for item in closure(multiple_requirements, ["child"])
    ] == ["first", "second", "child"]

    duplicate = parse(
        write_series(
            tmp_path / "duplicate-closure",
            [
                ("0001-one.patch", patch_text("same")),
                ("0002-two.patch", patch_text("same")),
            ],
        )
    )
    with pytest.raises(SeriesError, match="duplicate Change-Id"):
        closure(duplicate, ["same"])

    missing_id = parse(
        write_series(
            tmp_path / "missing-closure-id",
            [
                (
                    "0001-no-id.patch",
                    patch_text(
                        "no-id",
                        headers=(
                            "Capability: placement\nRole: enable\nRisk: low\n"
                            "Requires: none\n"
                        ),
                    ),
                )
            ],
        )
    )
    with pytest.raises(SeriesError, match="has no Change-Id"):
        closure(missing_id, ["no-id"])

    dangling = parse(
        write_series(
            tmp_path / "dangling-closure",
            [("0001-leaf.patch", patch_text("leaf", requires="missing"))],
        )
    )
    with pytest.raises(SeriesError, match="unknown selected Change-Id or dependency"):
        closure(dangling, ["leaf"])

    cyclic = parse(
        write_series(
            tmp_path / "cyclic-closure",
            [
                ("0001-first.patch", patch_text("first", requires="second")),
                ("0002-second.patch", patch_text("second", requires="first")),
            ],
        )
    )
    with pytest.raises(SeriesError, match="dependency cycle in selected closure"):
        closure(cyclic, ["first"])


def test_render_outputs_capability_and_role_tables_with_columns(
    tmp_path: Path,
) -> None:
    first = parse(sample_series(tmp_path))
    second_root = write_series(
        tmp_path / "second",
        [
            (
                "0001-logging.patch",
                patch_text("logging", capability="logging", role="guard"),
            )
        ],
    )

    markdown = render({"v1.13.3": first, "v1.14.0": parse(second_root)})

    assert "## Capabilities" in markdown
    assert "## Roles" in markdown
    assert "| Capability | v1.13.3 | v1.14.0 |" in markdown
    assert "| placement | 2 changes (+2/-2) | — |" in markdown
    assert "| logging | — | 1 change (+1/-1) |" in markdown
    assert "| guard | — | 1 change (+1/-1) |" in markdown


def test_render_rejects_duplicate_column_labels() -> None:
    with pytest.raises(SeriesError, match="duplicate column"):
        render([("same", []), ("same", [])])

    with pytest.raises(SeriesError, match="must not be empty"):
        render([("", [])])


def test_render_ignores_missing_metadata_and_handles_no_columns(tmp_path: Path) -> None:
    root = write_series(
        tmp_path / "missing-metadata",
        [("0001-missing.patch", patch_text("missing", headers=""))],
    )

    rendered = render({"empty": parse(root)})

    assert "## Capabilities" in rendered
    assert "## Roles" in rendered
    assert (
        render({})
        == "## Capabilities\n\n| Capability |\n| --- |\n\n## Roles\n\n| Role |\n| --- |\n"
    )


def test_render_escapes_vocabulary_labels(tmp_path: Path) -> None:
    root = write_series(
        tmp_path / "pipe",
        [("0001-pipe.patch", patch_text("pipe", capability="a|b"))],
    )

    assert "a\\|b" in render({"v": parse(root)})


def test_delta_classifies_added_removed_changed_and_unchanged(
    tmp_path: Path,
) -> None:
    old = parse(
        write_series(
            tmp_path / "old",
            [
                ("0001-same.patch", patch_text("same")),
                ("0002-changed.patch", patch_text("changed")),
                ("0003-removed.patch", patch_text("removed")),
            ],
        )
    )
    new = parse(
        write_series(
            tmp_path / "new",
            [
                ("0001-same.patch", patch_text("same")),
                (
                    "0002-changed.patch",
                    patch_text("changed", body="-before\n+after2\n"),
                ),
                ("0003-added.patch", patch_text("added")),
            ],
        )
    )

    changes = {item.change_id: item.status for item in delta(old, new)}

    assert changes == {
        "added": "added",
        "changed": "changed",
        "removed": "removed",
        "same": "unchanged",
    }


def test_delta_rejects_duplicate_change_ids(tmp_path: Path) -> None:
    duplicate = parse(
        write_series(
            tmp_path / "duplicate",
            [
                ("0001-one.patch", patch_text("same")),
                ("0002-two.patch", patch_text("same")),
            ],
        )
    )

    with pytest.raises(SeriesError, match="duplicate Change-Id"):
        delta(duplicate, [])

    missing_id = parse(
        write_series(
            tmp_path / "missing-id",
            [
                (
                    "0001-missing.patch",
                    patch_text(
                        "placeholder",
                        headers=(
                            "Capability: placement\nRole: enable\nRisk: low\n"
                            "Requires: none\n"
                        ),
                    ),
                )
            ],
        )
    )
    with pytest.raises(SeriesError, match="no Change-Id"):
        delta(missing_id, [])


def test_series_digest_uses_ordered_patch_bytes_and_upstream_only(
    tmp_path: Path,
) -> None:
    first = parse(sample_series(tmp_path))
    reversed_changes = list(reversed(first))
    baseline = series_digest(first, "a" * 40)

    assert baseline == series_digest(first, "a" * 40)
    assert baseline != series_digest(reversed_changes, "a" * 40)
    assert baseline != series_digest(first, "b" * 40)
    assert len(baseline) == 64

    renamed = write_series(
        tmp_path / "renamed-series",
        [
            ("renamed-child.patch", (first[1].path).read_text(encoding="utf-8")),
            ("renamed-base.patch", (first[0].path).read_text(encoding="utf-8")),
        ],
    )
    assert baseline == series_digest(parse(renamed), "a" * 40)

    changed = write_series(
        tmp_path / "changed-series",
        [
            ("0001-base.patch", (first[0].path).read_text(encoding="utf-8") + "\n"),
            ("0002-child.patch", (first[1].path).read_text(encoding="utf-8")),
        ],
    )
    assert baseline != series_digest(parse(changed), "a" * 40)
    with pytest.raises(SeriesError, match="upstream commit"):
        series_digest(first, "not-a-commit")


def test_load_vocabulary_refuses_io_toml_schema_and_item_errors(tmp_path: Path) -> None:
    from patchseries import load_vocabulary

    with pytest.raises(SeriesError, match="cannot read vocabulary"):
        load_vocabulary(tmp_path / "missing.toml")

    invalid_toml = tmp_path / "invalid.toml"
    invalid_toml.write_text("not = [", encoding="utf-8")
    with pytest.raises(SeriesError, match="cannot read vocabulary"):
        load_vocabulary(invalid_toml)

    invalid_schema = tmp_path / "missing-schema.toml"
    invalid_schema.write_text(
        'capabilities = ["placement"]\nroles = ["enable"]\nrisks = ["low"]\n',
        encoding="utf-8",
    )
    with pytest.raises(SeriesError, match="schema_version"):
        load_vocabulary(invalid_schema)

    non_string = tmp_path / "non-string.toml"
    non_string.write_text(
        'schema_version = 1\ncapabilities = [1]\nroles = ["enable"]\nrisks = ["low"]\n',
        encoding="utf-8",
    )
    with pytest.raises(SeriesError, match="array of strings"):
        load_vocabulary(non_string)
