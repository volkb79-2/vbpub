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
        "+    a - b",
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
        members = {m.diff_path: _diff() for m in mutants}
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
    _bind([_mutant(span=(2, 5, 2, 10), genre="UnaryOperator", name="x")])


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
        lambda: _bind([_fn_value()], {DIFF_PATH: _diff(rows=_rows_for(source))}, source=source),
        "is not in its function",
    )


def _rows_for(source: bytes) -> list[str]:
    lines = source.decode().split("\n")[:-1]
    return [f"@@ -1,{len(lines)} +1,{len(lines)} @@"] + [
        f"-{line}" if number == 2 else f" {line}"
        for number, line in enumerate(lines, 1)
    ] + ["+    a - b"]


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
    mutant = _mutant(genre="UnaryOperator", name="anything at all", replacement="")
    bound, _ = _bind([mutant])
    assert bound[0].operator == "cargo-mutants:UnaryOperator"
    assert bound[0].description == "UnaryOperator"
    # ... but the diff tier still binds them.
    _refused(
        lambda: _bind([mutant], {DIFF_PATH: _diff(file="other.rs")}), "does not start with"
    )


def test_an_unknown_genre_that_is_not_an_operator_name_refuses():
    _refused(lambda: _bind([_mutant(genre="Bad Genre", name="x")]), "is not an operator name")


# --- the per-mutant diff ---------------------------------------------------


def _with_rows(rows: list[str], **kwargs):
    return _bind([_mutant()], {DIFF_PATH: _diff(rows=rows, **kwargs)})


def test_the_diff_baseline_forms_bind():
    _with_rows(["@@ -1,3 +1,3 @@", " " + LINES[0], "-" + LINES[1], "+    a - b", " " + LINES[2]])
    # Counts default to 1, and several hunks are walked in turn.
    _with_rows(["@@ -2 +2 @@", "-" + LINES[1], "+    a - b"])
    _with_rows(
        ["@@ -1 +1 @@", " " + LINES[0], "@@ -2 +2 @@", "-" + LINES[1], "+    a - b"]
    )
    # An insertion-only hunk (no old lines) is legal alongside the removal.
    _with_rows(["@@ -2 +2 @@", "-" + LINES[1], "+    a - b", "@@ -3,0 +4 @@", "+// end"])


def test_a_no_newline_marker_and_a_missing_final_newline_are_accepted():
    rows = ["@@ -2 +2 @@", "-" + LINES[1], "\\ No newline at end of file", "+    a - b"]
    _with_rows(rows)
    raw = _diff(rows=rows).rstrip(b"\n")
    assert len(_bind([_mutant()], {DIFF_PATH: raw})[0]) == 1


def test_a_removed_line_that_is_not_the_committed_line_refuses():
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "-    a * b", "+    a - b"]),
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
        lambda: _with_rows(["@@ -1,3 +1,4 @@", " " + LINES[0], " " + LINES[1], "+    a - b", " " + LINES[2]]),
        "does not remove the lines of the mutation span",
    )
    # A multi-line span needs EVERY one of its lines removed.
    two = _mutant(span=(2, 7, 3, 2), genre="UnaryOperator", name="x")
    _refused(
        lambda: _bind([two], {DIFF_PATH: _diff(rows=["@@ -2 +2 @@", "-" + LINES[1], "+y"])}),
        "does not remove the lines of the mutation span",
    )


@pytest.mark.parametrize(
    "rows",
    [
        ["not a hunk header"],
        ["@@ -x +1 @@", "-a"],
        ["@@ -1,3 +1,3 @@ trailing", " a"],
        ["@@ -2 +2 @@", "-" + LINES[1], "+    a - b", "@@ malformed"],
        ["@@ -2 +2 @@", "-" + LINES[1], "+    a - b", ""],
    ],
)
def test_a_malformed_hunk_header_refuses(rows):
    _refused(lambda: _with_rows(rows), "malformed hunk header")


def test_a_truncated_hunk_refuses():
    _refused(lambda: _with_rows(["@@ -1,3 +1,3 @@", " " + LINES[0]]), "ends inside a hunk")


def test_a_hunk_with_more_added_lines_than_it_declares_refuses():
    _refused(
        lambda: _with_rows(["@@ -2 +2 @@", "+x", "+y", "-" + LINES[1]]), "ends inside a hunk"
    )


@pytest.mark.parametrize(
    "rows",
    [
        # an unknown tag
        ["@@ -2 +2 @@", "?" + LINES[1]],
        # an empty row where a body line is due
        ["@@ -2 +2 @@", ""],
        # old lines are used up while the new side still expects context
        ["@@ -2,1 +2,2 @@", "-" + LINES[1], "+    a - b", " " + LINES[2]],
        # a removal after the old side is used up
        ["@@ -2,1 +2,2 @@", "-" + LINES[1], "+    a - b", "-" + LINES[2]],
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
        "+    a - b",
        " }",
    ]
    bound, _ = _bind([_mutant()], {DIFF_PATH: _diff(rows=rows)}, source=source)
    assert source[bound[0].start_byte : bound[0].end_byte] == b"+"


def test_a_crlf_line_keeps_its_carriage_return_on_both_sides():
    source = b"fn add(a: i32, b: i32) -> i32 {\r\n    a + b\r\n}\r\n"
    rows = ["@@ -2 +2 @@", "-    a + b\r", "+    a - b\r"]
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
    members = {DIFF_PATH: _diff(), "diff/two.diff": _diff(plus="+++ replace + with * in add")}
    bound, _ = cmo._bind_mutants([_mutant(), other], _Dir(b"", members), ctx)
    assert len(bound) == 2 and ctx_repo.reads == [PurePosixPath("src/lib.rs")]


def test_the_aggregate_source_size_is_bounded(monkeypatch):
    monkeypatch.setattr(cmo, "MAX_MUTATION_REPORT_BYTES", len(SOURCE) + 5)
    second = b"fn b() {}\n" * 3
    files = {"src/lib.rs": SOURCE, "src/b.rs": second}
    mutants = [_mutant(), _mutant(file="src/b.rs", name="n", diff_path="diff/b.diff")]
    members = {DIFF_PATH: _diff(), "diff/b.diff": b""}
    _refused(lambda: _bind(mutants, members, files=files), "exceed")


def test_a_replacement_hash_and_description_preview():
    # UnaryOperator: its name is unmeasured, so the replacement is free here.
    long = _mutant(genre="UnaryOperator", name="n", replacement="x" * 100)
    (only,), _ = _bind([long])
    assert only.description == "UnaryOperator -> " + "x" * 60 + "..."
    # The cut never splits a UTF-8 sequence: 1 + 29 two-byte characters fit.
    multibyte = _mutant(genre="UnaryOperator", name="n", replacement="a" + "\u00e9" * 40)
    (only,), _ = _bind([multibyte])
    assert only.description == "UnaryOperator -> a" + "\u00e9" * 29 + "..."
    assert only.replacement_sha256 == hashlib.sha256(multibyte.replacement.encode()).hexdigest()
