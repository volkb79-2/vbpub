"""B245 U3b: binding cargo-mutants mutants to the committed source bytes.

Two layers. The real-case tests run :func:`parse_directory` over every case in
``tests/fixtures/rust/cargo-mutants`` (REAL tool output, except
``synthetic-signalled`` and ``synthetic-other``, which are edited from ``full``
by ``make-synthetic.py`` and are used only for the ``Crashed`` mapping). The
refusal tests hand-build one small source, one mutant and one diff so each
bad input differs from the good one in exactly one place.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path, PurePosixPath

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation_parsers import ParseContext, cargo_mutants_outcomes as cmo

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rust" / "cargo-mutants"
TOP = Path("/repo")
#: case -> (repo prefix of the run directory, total mutants, bound count).
REAL_CASES = {
    "full": ("", 25),
    "crashed-f4": ("", 8),
    "indiff": ("", 9),
    "methods": ("", 11),
    "paths": ("", 10),
    "subdir": ("sub/crate1", 9),
    "unicode": ("", 4),
    "w-true": ("", 8),
    "rotation": ("", 9),
    "basefail-test": ("", 0),
    "basefail-build": ("", 0),
}
SYNTHETIC = ("synthetic-signalled", "synthetic-other")


class _Repo:
    """A fake ``ctx.repository`` over a dict of repo-relative paths."""

    def __init__(self, files: dict[str, bytes]):
        self.files = {PurePosixPath(path): data for path, data in files.items()}
        self.reads: list[PurePosixPath] = []

    def read_regular_file(self, path: PurePosixPath, *, timeout: float) -> bytes:
        self.reads.append(path)
        return self.files[path]


class _Dir:
    def __init__(self, report: bytes, members: dict[str, bytes]):
        self.report = report
        self.members = members


def _case(case: str, prefix: str) -> tuple[_Dir, ParseContext, _Repo]:
    root = FIXTURES / case
    subject = root / "subject"
    base = PurePosixPath(prefix) if prefix else PurePosixPath()
    files = {
        str(base / path.relative_to(subject).as_posix()): path.read_bytes()
        for path in sorted(subject.rglob("*"))
        if path.is_file() and path.suffix in (".rs", ".toml")
    }
    out = root / "mutants.out"
    members = {
        f"diff/{path.name}": path.read_bytes()
        for path in sorted((out / "diff").glob("*.diff"))
    } if (out / "diff").is_dir() else {}
    repo = _Repo(files)
    ctx = ParseContext(
        run_cwd=TOP / prefix if prefix else TOP,
        repo_top=TOP,
        repository=repo,  # type: ignore[arg-type]
        read_timeout=1.0,
    )
    return _Dir((out / "outcomes.json").read_bytes(), members), ctx, repo


def _refused(call, match: str) -> AssayError:
    with pytest.raises(AssayError, match=match) as caught:
        call()
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    return caught.value


# --- real cases ------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(REAL_CASES))
def test_every_real_case_binds_and_the_span_bytes_are_the_committed_text(case):
    prefix, count = REAL_CASES[case]
    artifact, ctx, repo = _case(case, prefix)
    report = cmo.parse_directory(artifact, ctx)
    assert report.project_root is None
    assert len(report.mutants) == count
    # Every blob is read once, at the run directory's prefix in the repository.
    assert len(repo.reads) == len(set(repo.reads)) == len(report.sources)
    for read in repo.reads:
        assert str(read).startswith(prefix)
    for mutant in report.mutants:
        assert mutant.operator.startswith("cargo-mutants:")
        text = report.sources[mutant.path].encode("utf-8")[
            mutant.start_byte : mutant.end_byte
        ]
        assert text and mutant.lineno >= 1


def test_the_full_case_names_the_token_of_every_operator_mutant():
    artifact, ctx, _ = _case("full", "")
    report = cmo.parse_directory(artifact, ctx)
    normalised = cmo.normalise_outcomes(cmo.load_document(artifact.report))
    assert len(normalised) == len(report.mutants)
    checked = 0
    for mutant, ingested in zip(normalised, report.mutants):
        if mutant.genre not in ("BinaryOperator", "MatchArmGuard"):
            continue
        token = report.sources[ingested.path].encode("utf-8")[
            ingested.start_byte : ingested.end_byte
        ].decode("utf-8")
        head = "match guard " if mutant.genre == "MatchArmGuard" else ""
        assert f": replace {head}{token} with " in mutant.name
        checked += 1
    assert checked >= 5


def test_the_full_case_counts_and_genres():
    artifact, ctx, _ = _case("full", "")
    report = cmo.parse_directory(artifact, ctx)
    assert {m.operator for m in report.mutants} >= {
        "cargo-mutants:FnValue",
        "cargo-mutants:BinaryOperator",
        "cargo-mutants:MatchArmGuard",
    }


def test_unicode_columns_are_characters_and_offsets_are_bytes():
    artifact, ctx, _ = _case("unicode", "")
    report = cmo.parse_directory(artifact, ctx)
    source = report.sources["src/lib.rs"]
    binary = [m for m in report.mutants if m.operator.endswith("BinaryOperator")]
    assert len(binary) == 2
    for mutant in binary:
        assert source.encode()[mutant.start_byte : mutant.end_byte] == b"+"
        # column 31 counts "é" and "→" as one each; their bytes are more.
        assert mutant.start_byte == source.encode().index(b"+ a")
        # The two non-ASCII characters add 1 + 2 bytes before the operator.
        assert mutant.start_byte == source.index("+ a") + 3
    fn_value = [m for m in report.mutants if m.operator.endswith("FnValue")]
    assert source.encode()[fn_value[0].start_byte : fn_value[0].end_byte].decode() == (
        'let s = "héllo→"; s.len() + a'
    )


def test_methods_bind_qualified_function_names_to_the_last_segment():
    artifact, ctx, _ = _case("methods", "")
    names = {
        m.function_name
        for m in cmo.normalise_outcomes(cmo.load_document(artifact.report))
    }
    assert {"S::bump", "<impl T for S>::triple", "pick"} <= names
    assert len(cmo.parse_directory(artifact, ctx).mutants) == 11


def test_subdir_reads_the_blob_at_the_run_directory_prefix():
    artifact, ctx, repo = _case("subdir", "sub/crate1")
    cmo.parse_directory(artifact, ctx)
    assert repo.reads == [PurePosixPath("sub/crate1/src/lib.rs")]


def test_a_run_directory_equal_to_the_repository_top_has_no_prefix():
    artifact, ctx, repo = _case("full", "")
    cmo.parse_directory(artifact, ctx)
    assert repo.reads == [PurePosixPath("src/lib.rs")]


@pytest.mark.parametrize("case", SYNTHETIC)
def test_synthetic_cases_bind_and_their_edited_mutants_are_crashed(case):
    # SYNTHETIC (edited from `full`, not tool output): the mapping only.
    artifact, ctx, _ = _case(case, "")
    report = cmo.parse_directory(artifact, ctx)
    assert len(report.mutants) == 25
    assert sum(1 for m in report.mutants if m.status == "Crashed") == 2


def test_the_run_directory_must_be_inside_the_repository():
    artifact, ctx, _ = _case("full", "")
    outside = dataclasses.replace(ctx, run_cwd=Path("/elsewhere"))
    _refused(lambda: cmo.parse_directory(artifact, outside), "not inside the repository")


# --- hand-built cases ------------------------------------------------------

LINES = ["fn add(a: i32, b: i32) -> i32 {", "    a + b", "}"]
SOURCE = ("\n".join(LINES) + "\n").encode()
DIFF_PATH = "diff/src__lib.rs_line_2_col_7.diff"
#: What cargo-mutants 27.x appends to every replacement in the diff (measured
#: on the real fixtures); spelled out here so the tests do not borrow it from
#: the code under test.
MARK = " /* ~ changed by cargo-mutants ~ */"
#: The one `+` row of the baseline mutant: `    a ` + `-` + marker + ` b`.
GOOD = "    a -" + MARK + " b"


def _plus_row(mutant: cmo.NormalisedMutant, source: bytes = SOURCE) -> str:
    """The `+` row cargo-mutants writes: the first span line up to the start
    column, the replacement, the marker, the last span line from the end column
    (character columns, one row even for a multi-line span)."""
    lines = source.decode().split("\n")
    span = mutant.span
    return (
        lines[span[0] - 1][: span[1] - 1]
        + mutant.replacement
        + MARK
        + lines[span[2] - 1][span[3] - 1 :]
    )


def _diff_for(
    mutant: cmo.NormalisedMutant, source: bytes = SOURCE, *, plus: str | None = None
) -> bytes:
    """The exact diff cargo-mutants writes for ``mutant`` over ``source``: a
    hunk of the span lines plus one context line on each side."""
    span = mutant.span
    try:
        real = source.decode().split("\n")[:-1]
        added = _plus_row(mutant, source)
    except (IndexError, UnicodeDecodeError):
        return b""  # a test that refuses before the diff is ever read
    first, last = max(1, span[0] - 1), min(len(real), span[2] + 1)
    old = last - first + 1
    new = old - (span[2] - span[0] + 1) + 1
    rows = [f"@@ -{first},{old} +{first},{new} @@"]
    for number in range(first, last + 1):
        removed = span[0] <= number <= span[2]
        rows.append(("-" if removed else " ") + real[number - 1])
        if number == span[2]:
            rows.append("+" + added)
    title = mutant.name[len(f"{mutant.file}:{span[0]}:{span[1]}: ") :]
    return "\n".join(
        [f"--- {mutant.file}", plus if plus is not None else f"+++ {title}", *rows, ""]
    ).encode()


def _blob(source: bytes = SOURCE) -> cmo._Blob:
    repo = _Repo({"src/lib.rs": source})
    ctx = ParseContext(run_cwd=TOP, repo_top=TOP, repository=repo, read_timeout=1.0)  # type: ignore[arg-type]
    return cmo._load_blob("src/lib.rs", ctx, PurePosixPath(), [0])


def _diff(
    *,
    file: str = "src/lib.rs",
    plus: str = "+++ replace + with - in add",
    rows: list[str] | None = None,
) -> bytes:
    rows = rows if rows is not None else [
        "@@ -1,3 +1,3 @@",
        " " + LINES[0],
        "-" + LINES[1],
        "+" + GOOD,
        " " + LINES[2],
    ]
    return ("\n".join([f"--- {file}", plus, *rows]) + "\n").encode()


def _mutant(**changes) -> cmo.NormalisedMutant:
    base = dict(
        name="src/lib.rs:2:7: replace + with - in add",
        package="p",
        file="src/lib.rs",
        function_name="add",
        function_span=(1, 1, 3, 2),
        span=(2, 7, 2, 8),
        replacement="-",
        genre="BinaryOperator",
        status="Survived",
        diff_path=DIFF_PATH,
    )
    base.update(changes)
    return cmo.NormalisedMutant(**base)


def _bind(
    mutants,
    members=None,
    *,
    source: bytes = SOURCE,
    files: dict[str, bytes] | None = None,
    ctx: ParseContext | None = None,
):
    if members is None:
        members = {m.diff_path: _diff_for(m, source) for m in mutants}
    repo = _Repo(files if files is not None else {"src/lib.rs": source})
    context = ctx or ParseContext(
        run_cwd=TOP, repo_top=TOP, repository=repo, read_timeout=1.0  # type: ignore[arg-type]
    )
    return cmo._bind_mutants(mutants, _Dir(b"", members), context)


def test_the_hand_built_baseline_binds():
    bound, sources = _bind([_mutant()])
    (only,) = bound
    assert (only.path, only.lineno, only.start_byte, only.end_byte) == (
        "src/lib.rs", 2, SOURCE.index(b"+"), SOURCE.index(b"+") + 1,
    )
    assert only.operator == "cargo-mutants:BinaryOperator"
    assert only.description == "BinaryOperator -> -"
    assert sources == {"src/lib.rs": SOURCE.decode()}


@pytest.mark.parametrize(
    "span, match",
    [
        ((9, 7, 9, 8), "past the end of the source"),
        ((2, 7, 2, 99), "past the end of that line"),
        ((2, 99, 2, 100), "past the end of that line"),
        ((2, 7, 2, 7), "empty or reversed"),
        ((2, 8, 2, 7), "empty or reversed"),
        ((3, 1, 2, 7), "empty or reversed"),
    ],
)
def test_a_span_outside_the_source_or_empty_or_reversed_refuses(span, match):
    _refused(lambda: _bind([_mutant(span=span)]), match)


def test_the_last_column_of_a_line_is_one_past_its_text():
    # `    a + b` is 9 characters: column 10 is the exclusive end.
    _bind([_mutant(span=(2, 5, 2, 10), genre="UnaryOperator", name="src/lib.rs:2:5: x")])


@pytest.mark.parametrize(
    "name",
    [
        "src/lib.rs:2:7: replace - with - in add",  # wrong token
        "src/lib.rs:2:7: replace + with * in add",  # replacement differs
        "src/lib.rs:2:7: replace + with - in sub",  # function differs
        "src/lib.rs:2:7: replace + with -",  # function missing
        "src/lib.rs:2:9: replace + with - in add",  # position differs
        "other.rs:2:7: replace + with - in add",  # file differs
        "replace + with - in add",  # no prefix
        "",  # shorter than prefix + suffix
    ],
)
def test_an_operator_name_that_does_not_carry_the_committed_token_refuses(name):
    _refused(lambda: _bind([_mutant(name=name)]), "not the token its name carries")


def test_an_operator_without_a_function_binds_when_the_name_has_no_in_clause():
    bound, _ = _bind(
        [
            _mutant(
                name="src/lib.rs:2:7: replace + with -",
                function_name=None,
                function_span=None,
            )
        ]
    )
    assert len(bound) == 1


def test_a_match_arm_guard_binds_by_token_text():
    guard = _mutant(
        genre="MatchArmGuard",
        name="src/lib.rs:2:7: replace match guard + with true in add",
        replacement="true",
    )
    assert len(_bind([guard])[0]) == 1
    bad = dataclasses.replace(
        guard, name="src/lib.rs:2:7: replace match guard - with true in add"
    )
    _refused(lambda: _bind([bad]), "not the token its name carries")


def test_a_guard_name_with_the_operator_head_refuses():
    # The head belongs to the genre: a guard named like an operator is wrong.
    bad = _mutant(genre="MatchArmGuard")
    _refused(lambda: _bind([bad]), "not the token its name carries")


def _fn_value(**changes) -> cmo.NormalisedMutant:
    return _mutant(
        genre="FnValue",
        name="src/lib.rs:2:5: replace add -> i32 with 0",
        span=(2, 5, 2, 10),
        replacement="0",
        **changes,
    )


@pytest.mark.parametrize("function", ["add", "S::add", "<impl T for S>::add"])
def test_fn_value_binds_by_the_last_segment(function):
    assert len(_bind([_fn_value(function_name=function)])[0]) == 1


@pytest.mark.parametrize("function", ["sub", "ad", "S::ad", "dd"])
def test_fn_value_without_that_fn_in_its_span_refuses(function):
    _refused(lambda: _bind([_fn_value(function_name=function)]), "is not in its function")


def test_fn_value_needs_a_word_boundary_before_fn():
    source = b"xfn add() {\n    a + b\n}\n"
    _refused(
        lambda: _bind([_fn_value()], source=source),
        "is not in its function",
    )


def test_fn_value_with_the_mutation_span_outside_the_function_span_refuses():
    _refused(
        lambda: _bind([_fn_value(function_span=(1, 1, 1, 10))]), "is not in its function"
    )


def test_fn_value_function_span_is_itself_checked():
    _refused(
        lambda: _bind([_fn_value(function_span=(1, 1, 9, 2))]), "past the end of the source"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"function_name": None, "function_span": None},
        {"function_span": None},
        {"function_name": None},
    ],
)
def test_fn_value_without_a_function_refuses(changes):
    _refused(lambda: _bind([_fn_value(**changes)]), "names no function")


def test_unary_operator_and_unmeasured_genres_are_bound_by_diff_and_span_only():
    # The name shape of these genres is unmeasured, so no token check applies.
    mutant = _mutant(genre="UnaryOperator", name="src/lib.rs:2:7: anything at all", replacement="")
    bound, _ = _bind([mutant])
    assert bound[0].operator == "cargo-mutants:UnaryOperator"
    assert bound[0].description == "UnaryOperator"
    # ... but the diff tier still binds them.
    _refused(
        lambda: _bind([mutant], {DIFF_PATH: _diff(file="other.rs")}), "does not start with"
    )


def test_an_unknown_genre_that_is_not_an_operator_name_refuses():
    _refused(lambda: _bind([_mutant(genre="Bad Genre", name="src/lib.rs:2:7: x")]), "is not an operator name")


# --- the per-mutant diff ---------------------------------------------------


def _with_rows(rows: list[str], **kwargs):
    return _bind([_mutant()], {DIFF_PATH: _diff(rows=rows, **kwargs)})


def test_the_diff_baseline_forms_bind():
    _with_rows(["@@ -1,3 +1,3 @@", " " + LINES[0], "-" + LINES[1], "+" + GOOD, " " + LINES[2]])
    # Counts default to 1, and several hunks are walked in turn.
    _with_rows(["@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD])
    _with_rows(
        ["@@ -1 +1 @@", " " + LINES[0], "@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD]
    )


def test_a_no_newline_marker_and_a_missing_final_newline_are_accepted():
    rows = ["@@ -2 +2 @@", "-" + LINES[1], "\\ No newline at end of file", "+" + GOOD]
    _with_rows(rows)
    raw = _diff(rows=rows).rstrip(b"\n")
    assert len(_bind([_mutant()], {DIFF_PATH: raw})[0]) == 1


def test_a_removed_line_that_is_not_the_committed_line_refuses():
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "-    a * b", "+" + GOOD]),
        "is not the committed source line",
    )


def test_a_context_line_that_is_not_the_committed_line_refuses():
    _refused(
        lambda: _with_rows(["@@ -1,2 +1,2 @@", " fn other() {", "-" + LINES[1], "+x"]),
        "is not the committed source line",
    )


@pytest.mark.parametrize("old_start", [0, 9])
def test_a_hunk_outside_the_source_refuses(old_start):
    _refused(
        lambda: _with_rows([f"@@ -{old_start} +2 @@", "-" + LINES[1], "+x"]),
        "is not the committed source line",
    )


def test_a_diff_for_another_file_refuses():
    _refused(
        lambda: _bind([_mutant()], {DIFF_PATH: _diff(file="src/other.rs")}),
        "does not start with '--- src/lib.rs'",
    )


@pytest.mark.parametrize("plus", ["++ replace", "--- x", ""])
def test_a_diff_without_a_plus_line_refuses(plus):
    _refused(
        lambda: _bind([_mutant()], {DIFF_PATH: _diff(plus=plus)}),
        "does not start with",
    )


def test_a_diff_shorter_than_its_two_header_lines_refuses():
    _refused(
        lambda: _bind([_mutant()], {DIFF_PATH: b"--- src/lib.rs\n"}), "does not start with"
    )


def test_a_diff_that_does_not_remove_the_span_lines_refuses():
    _refused(
        lambda: _with_rows(["@@ -1 +1 @@", " " + LINES[0]]),
        "does not remove exactly the lines of the mutation span",
    )
    # A multi-line span needs EVERY one of its lines removed.
    two = _mutant(span=(2, 7, 3, 2), genre="UnaryOperator", name="src/lib.rs:2:7: x")
    _refused(
        lambda: _bind([two], {DIFF_PATH: _diff(plus="+++ x", rows=["@@ -2 +2 @@", "-" + LINES[1], "+y"])}),
        "does not remove exactly the lines of the mutation span",
    )


@pytest.mark.parametrize(
    "rows",
    [
        ["not a hunk header"],
        ["@@ -x +1 @@", "-a"],
        ["@@ -1,3 +1,3 @@ trailing", " a"],
        ["@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD, "@@ malformed"],
        ["@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD, ""],
    ],
)
def test_a_malformed_hunk_header_refuses(rows):
    _refused(lambda: _with_rows(rows), "malformed hunk header")


def test_a_truncated_hunk_refuses():
    _refused(lambda: _with_rows(["@@ -1,3 +1,3 @@", " " + LINES[0]]), "ends inside a hunk")


def test_a_hunk_with_more_added_lines_than_it_declares_refuses():
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "+x", "+y", "-" + LINES[1]]),
        "does not directly follow a removed line",
    )


@pytest.mark.parametrize(
    "rows",
    [
        # an unknown tag
        ["@@ -2 +2 @@", "?" + LINES[1]],
        # an empty row where a body line is due
        ["@@ -2 +2 @@", ""],
        # old lines are used up while the new side still expects context
        ["@@ -2,1 +2,2 @@", "-" + LINES[1], "+" + GOOD, " " + LINES[2]],
        # a removal after the old side is used up
        ["@@ -2,1 +2,2 @@", "-" + LINES[1], "+" + GOOD, "-" + LINES[2]],
    ],
)
def test_a_malformed_hunk_line_refuses(rows):
    _refused(lambda: _with_rows(rows), "malformed hunk line")


def test_a_non_utf8_diff_refuses():
    _refused(lambda: _bind([_mutant()], {DIFF_PATH: b"--- src/lib.rs\n\xff\n"}), "is not UTF-8")


def test_a_non_utf8_source_refuses():
    _refused(lambda: _bind([_mutant()], source=b"fn \xff() {}\n"), "is not UTF-8")


def test_lines_are_split_on_newline_only():
    # A vertical tab and U+2028 inside a line do not start a new line.
    source = ("fn add(a: i32, b: i32) -> i32 {\x0b\u2028\n    a + b\n}\n").encode()
    rows = [
        "@@ -1,3 +1,3 @@",
        " fn add(a: i32, b: i32) -> i32 {\x0b\u2028",
        "-    a + b",
        "+" + GOOD,
        " }",
    ]
    bound, _ = _bind([_mutant()], {DIFF_PATH: _diff(rows=rows)}, source=source)
    assert source[bound[0].start_byte : bound[0].end_byte] == b"+"


def test_a_crlf_line_keeps_its_carriage_return_on_both_sides():
    source = b"fn add(a: i32, b: i32) -> i32 {\r\n    a + b\r\n}\r\n"
    # The `\r` is part of the line text (cargo-mutants splits on `\n`; the CRLF
    # shape is NOT measured against the real tool), so the added row keeps it.
    rows = ["@@ -2 +2 @@", "-    a + b\r", "+" + GOOD + "\r"]
    bound, _ = _bind([_mutant()], {DIFF_PATH: _diff(rows=rows)}, source=source)
    assert source[bound[0].start_byte : bound[0].end_byte] == b"+"


# --- members, names, files, sizes -----------------------------------------


def test_a_missing_and_an_extra_member_refuse():
    _refused(lambda: _bind([_mutant()], {}), "missing \\['diff/src__lib.rs_line_2_col_7.diff'\\]")
    _refused(
        lambda: _bind([_mutant()], {DIFF_PATH: _diff(), "diff/stray.diff": b""}),
        "extra \\['diff/stray.diff'\\]",
    )


def test_two_mutants_naming_one_diff_member_refuse():
    # load_document would normally refuse this earlier; the binder checks too.
    twin = _mutant(name="src/lib.rs:2:7: replace + with * in add", replacement="*")
    _refused(lambda: _bind([_mutant(), twin]), "one diff member for two mutants")


@pytest.mark.parametrize(
    "diff_path",
    [
        "src__lib.rs.diff",
        "diff/a",
        "diff/..",
        "diff/../x.diff",
        "diff/a/b.diff",
        "diff//x.diff",
        "diff/a\\b.diff",
        "diff/a\0b.diff",
        "/diff/x.diff",
        "/abs/x.diff",
        "diff/\ud800.diff",
        7,
        None,
    ],
)
def test_a_diff_path_outside_the_member_grammar_refuses(diff_path):
    _refused(
        lambda: _bind([_mutant(diff_path=diff_path)], {}),
        "diff_path",
    )


@pytest.mark.parametrize(
    "file",
    ["../x.rs", "src/../x.rs", "/etc/x.rs", "src/./lib.rs", "src//lib.rs", "a\0b.rs", ""],
)
def test_a_mutant_file_that_is_not_a_normal_relative_path_refuses(file):
    _refused(
        lambda: _bind([_mutant(file=file)], files={}),
        "is not a normalised relative path",
    )


def test_a_file_the_report_names_twice_is_read_once_and_counted_once(monkeypatch):
    other = _mutant(
        name="src/lib.rs:2:7: replace + with * in add",
        replacement="*",
        diff_path="diff/two.diff",
    )
    monkeypatch.setattr(cmo, "MAX_MUTATION_REPORT_BYTES", len(SOURCE))
    ctx_repo = _Repo({"src/lib.rs": SOURCE})
    ctx = ParseContext(run_cwd=TOP, repo_top=TOP, repository=ctx_repo, read_timeout=1.0)  # type: ignore[arg-type]
    members = {DIFF_PATH: _diff(), "diff/two.diff": _diff_for(other)}
    bound, _ = cmo._bind_mutants([_mutant(), other], _Dir(b"", members), ctx)
    assert len(bound) == 2 and ctx_repo.reads == [PurePosixPath("src/lib.rs")]


def test_the_aggregate_source_size_is_bounded(monkeypatch):
    monkeypatch.setattr(cmo, "MAX_MUTATION_REPORT_BYTES", len(SOURCE) + 5)
    second = b"fn b() {}\n" * 3
    files = {"src/lib.rs": SOURCE, "src/b.rs": second}
    mutants = [_mutant(), _mutant(file="src/b.rs", name="n", diff_path="diff/b.diff")]
    # (the second mutant is refused while loading its file, before any diff)
    members = {DIFF_PATH: _diff(), "diff/b.diff": b""}
    _refused(lambda: _bind(mutants, members, files=files), "exceed")


def test_a_replacement_hash_and_description_preview():
    # UnaryOperator: its name is unmeasured, so the replacement is free here.
    long = _mutant(genre="UnaryOperator", name="src/lib.rs:2:7: n", replacement="x" * 100)
    (only,), _ = _bind([long])
    assert only.description == "UnaryOperator -> " + "x" * 60 + "..."
    # The cut never splits a UTF-8 sequence: 1 + 29 two-byte characters fit.
    multibyte = _mutant(genre="UnaryOperator", name="src/lib.rs:2:7: n", replacement="a" + "\u00e9" * 40)
    (only,), _ = _bind([multibyte])
    assert only.description == "UnaryOperator -> a" + "\u00e9" * 29 + "..."
    assert only.replacement_sha256 == hashlib.sha256(multibyte.replacement.encode()).hexdigest()


# --- the diff is tied to the replacement, the name and the removed lines ----


def _unary(**changes) -> cmo.NormalisedMutant:
    """A mutant whose name carries no token, so only the diff tier binds it."""
    return _mutant(genre="UnaryOperator", name="src/lib.rs:2:7: neg", **changes)


def test_the_exact_diff_binds_for_a_single_and_a_multi_line_span():
    assert len(_bind([_unary()])[0]) == 1
    # Two lines: the prefix of the first, the replacement, the marker and the
    # suffix of the last, as ONE added row.
    two = _mutant(
        genre="UnaryOperator",
        name="src/lib.rs:2:5: neg",
        span=(2, 5, 3, 2),
        replacement="0",
    )
    assert _plus_row(two) == "    0" + MARK
    assert len(_bind([two])[0]) == 1
    # ... and a one-row diff that keeps the last line's suffix.
    wide = dataclasses.replace(two, span=(1, 4, 2, 6), name="src/lib.rs:1:4: neg")
    assert _plus_row(wide) == "fn 0" + MARK + " + b"
    assert len(_bind([wide])[0]) == 1


def test_every_real_multi_line_span_is_bound():
    for case in ("full", "methods"):
        artifact, ctx, _ = _case(case, "")
        multi = [
            m
            for m in cmo.normalise_outcomes(cmo.load_document(artifact.report))
            if m.span[0] != m.span[2]
        ]
        assert multi and all(m.genre == "FnValue" for m in multi)
        assert len(cmo.parse_directory(artifact, ctx).mutants) == REAL_CASES[case][1]


@pytest.mark.parametrize("replacement", ["*", "/", "%", "fake1", "fake2"])
def test_one_site_cannot_be_bound_as_several_mutants_with_one_copied_diff(replacement):
    # The genuine mutant replaces `+` with `-`; the copy claims another
    # replacement but ships the very same diff.
    fake = _mutant(
        replacement=replacement,
        name=f"src/lib.rs:2:7: replace + with {replacement} in add",
        diff_path="diff/fake.diff",
    )
    _refused(lambda: _bind([fake], {"diff/fake.diff": _diff()}), "'\\+\\+\\+' text")
    # Also with the '+++' text rewritten to the fake's name: the added row
    # still shows `-`.
    renamed = _diff(plus=f"+++ replace + with {replacement} in add")
    _refused(
        lambda: _bind([fake], {"diff/fake.diff": renamed}),
        "added line is not the replacement",
    )
    genuine = _mutant()
    members = {DIFF_PATH: _diff(), "diff/fake.diff": _diff()}
    _refused(lambda: _bind([genuine, fake], members), "'\\+\\+\\+' text")


def test_a_swapped_replacement_with_an_unchanged_diff_refuses():
    swapped = _unary(replacement="*")
    _refused(
        lambda: _bind([swapped], {DIFF_PATH: _diff(plus="+++ neg")}),
        "added line is not the replacement",
    )


@pytest.mark.parametrize(
    "added",
    [
        "    unrelated();",
        GOOD + " ",  # one trailing byte more
        "    a -" + MARK,  # the suffix of the last line is missing
        "    a - b",  # the marker is missing
        " a -" + MARK + " b",  # the prefix is changed
        "",
    ],
)
def test_an_added_row_that_is_not_the_spliced_replacement_refuses(added):
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "-" + LINES[1], "+" + added]),
        "added line is not the replacement",
    )


@pytest.mark.parametrize("plus", ["+++ whatever", "+++ ", "+++ replace + with - in add "])
def test_an_arbitrary_plus_plus_plus_text_refuses(plus):
    _refused(
        lambda: _bind([_mutant()], {DIFF_PATH: _diff(plus=plus)}),
        "'\\+\\+\\+' text is not the mutant name",
    )


@pytest.mark.parametrize(
    "rows",
    [
        # another line is rewritten in the same hunk (the closing brace)
        ["@@ -2,2 +2,2 @@", "-" + LINES[1], "+" + GOOD, "-" + LINES[2], "+} // x"],
        ["@@ -1,2 +1,2 @@", "-" + LINES[0], "+fn x() {", "-" + LINES[1], "+" + GOOD],
        # ... or in a hunk of its own
        ["@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD, "@@ -3 +3 @@", "-}", "+}}"],
    ],
)
def test_a_diff_that_rewrites_other_lines_too_refuses(rows):
    _refused(lambda: _with_rows(rows), "does not remove exactly the lines of the mutation span")


def test_a_second_added_row_or_a_row_not_after_a_removal_refuses():
    # Two removed lines, two added rows: the span's lines are all removed but
    # the replacement is not ONE spliced row.
    two = _mutant(
        genre="UnaryOperator", name="src/lib.rs:2:5: neg", span=(2, 5, 3, 2), replacement="0"
    )
    rows = ["@@ -2,2 +2,2 @@", "-" + LINES[1], "+    0", "-" + LINES[2], "+" + MARK.lstrip()]
    _refused(
        lambda: _bind([two], {DIFF_PATH: _diff(plus="+++ neg", rows=rows)}),
        "added line is not the replacement",
    )
    # An insertion beside the real change (an added row after context only).
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "-" + LINES[1], "+" + GOOD, "@@ -3,0 +4 @@", "+// end"]),
        "does not directly follow a removed line",
    )
    _refused(
        lambda: _with_rows(["@@ -1,3 +1,4 @@", " " + LINES[0], "+x", "-" + LINES[1], " " + LINES[2]]),
        "does not directly follow a removed line",
    )


@pytest.mark.parametrize("span", [(1, 1, 10**7, 1), (3, 1, 2, 1), (2, 1, 1, 1)])
def test_a_hostile_or_reversed_span_is_refused_without_building_a_range(span):
    mutant = _mutant(
        span=span, genre="UnaryOperator", name=f"src/lib.rs:{span[0]}:{span[1]}: x"
    )
    _refused(
        lambda: cmo._check_diff(mutant, _diff(plus="+++ x"), _blob()),
        "does not remove exactly the lines of the mutation span",
    )


# --- the last legal column ---------------------------------------------------


def test_the_end_exclusive_column_is_allowed_and_one_more_is_not():
    blob = _blob()  # line 2 is `    a + b`, nine characters
    assert cmo._byte_offset(blob, 2, 10, "x") == blob.line_starts[1] + 9
    _refused(lambda: cmo._byte_offset(blob, 2, 11, "x"), "past the end of that line")
    # The empty last line (after the final newline) has only column 1.
    assert cmo._byte_offset(blob, 4, 1, "x") == len(SOURCE)
    _refused(lambda: cmo._byte_offset(blob, 4, 2, "x"), "past the end of that line")


def test_the_end_exclusive_column_of_a_crlf_line_counts_its_carriage_return():
    # `\r` stays part of the line text (cargo-mutants splits on `\n`; the CRLF
    # shape is not measured against the real tool), so the line is ten
    # characters and column 11 is its exclusive end.
    blob = _blob(b"fn add(a: i32, b: i32) -> i32 {\r\n    a + b\r\n}\r\n")
    assert blob.lines[1] == "    a + b\r"
    assert cmo._byte_offset(blob, 2, 11, "x") == blob.line_starts[1] + 10
    _refused(lambda: cmo._byte_offset(blob, 2, 12, "x"), "past the end of that line")
