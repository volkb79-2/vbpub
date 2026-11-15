"""The R2 default scope: changed lines since a release, and the rolling cursor."""

from __future__ import annotations

import json
import subprocess

import pytest

from fleetd.cli import main
from fleetd.contracts import ContractError
from fleetd.scope import (
    CURSOR_SCHEMA,
    DEFAULT_SCOPE_MODE,
    EXIT_NOTHING_TO_JUDGE,
    EXIT_OK,
    EXIT_STALE_CURSOR,
    ROLLING_MODE,
    SCOPE_MODES,
    SCOPE_SCHEMA,
    AddedLines,
    candidate_list_digest,
    cursor_document,
    load_scope_file,
    parse_added_lines,
    plan_candidates,
    read_cursor,
    release_diff,
    resolve_commit,
    rolling_slice,
    scope_document,
    select_changed_lines,
    write_cursor,
)

CID = "0" * 64


def _cid(value):
    return f"{value:064d}"


def _plan(*rows):
    return {
        "status": "ok",
        "lane": "r2",
        "candidate_count": len(rows),
        "candidates": [
            {"id": _cid(index), "path": path, "lineno": lineno, "operator": "python:compare-swap"}
            for index, (path, lineno) in enumerate(rows)
        ],
    }


def _git_repo(path):
    """A real repo with one tagged commit and one change -- no fake diff text."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    for argv, content in (
        (("add",), None),
    ):
        pass
    files = {"src/a.py": "one\ntwo\nthree\n", "src/b.py": "beta\n"}
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _commit_all(path, "initial")
    subprocess.run(["git", "-C", str(path), "tag", "tool-v1.0.0"], check=True)
    (path / "src" / "a.py").write_text("one\ntwo changed\nthree\nfour\n", encoding="utf-8")
    (path / "src" / "b.py").unlink()
    (path / "src" / "c.py").write_text("gamma\n", encoding="utf-8")
    _commit_all(path, "change")
    return path


def _commit_all(path, message):
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "user.name=Scope Test",
            "-c", "user.email=scope@example.invalid", "commit", "-qm", message,
        ],
        check=True,
    )


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------ added lines
def test_added_lines_state_machine_reads_per_file_added_lines():
    text = (
        "diff --git a/src/a.py b/src/a.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -2 +2,2 @@\n"
        "-two\n"
        "+two changed\n"
        "+three\n"
        "diff --git a/src/new.py b/src/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+alpha\n"
        "+beta\n"
        "diff --git a/old.py b/renamed.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to renamed.py\n"
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x\n"
        "-y\n"
    )
    parsed = parse_added_lines(text)
    assert parsed["src/a.py"] == AddedLines(frozenset({2, 3}), False)
    assert parsed["src/new.py"] == AddedLines(frozenset({1, 2}), False)
    assert parsed["renamed.py"] == AddedLines(frozenset(), True)
    assert "gone.py" not in parsed


def test_added_lines_never_reads_content_that_looks_like_a_header():
    # A genuinely added line whose own text begins "++" is the patch byte
    # sequence "+++ value": the header-sniffing parser assay replaced lost that
    # line and corrupted the numbers that followed. The state machine cannot.
    text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1,2 @@\n"
        "-old\n"
        "+old\n"
        "+++ x\n"
        "@@ -5 +5 @@\n"
        "+later\n"
    )
    parsed = parse_added_lines(text)
    assert parsed["a.py"] == AddedLines(frozenset({1, 2, 5}), False)


def test_added_lines_ignores_no_newline_markers_and_counts_hunk_lengths():
    # The marker arrives while the hunk still has body lines to consume: it is
    # a marker, so it must not be counted as a line on either side.
    text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
        " same\n"
        "@@ -10 +10 @@\n"
        "+tail\n"
    )
    parsed = parse_added_lines(text)
    assert parsed["a.py"] == AddedLines(frozenset({1, 10}), False)
    # The marker must not consume a hunk line: counting it would shift every
    # later added line of that hunk by one.
    text = (
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,2 +1,3 @@\n"
        "-old\n"
        "+first\n"
        "\\ No newline at end of file\n"
        "+second\n"
    )
    parsed = parse_added_lines(text)
    assert parsed["b.py"] == AddedLines(frozenset({1, 2}), False)


def test_added_lines_with_unknown_prefix_is_empty():
    assert parse_added_lines("") == {}
    assert parse_added_lines("random text\n") == {}


# ----------------------------------------------------------- git plumbing
def test_release_diff_reads_a_real_tagged_release_change(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    diff = release_diff(repo, "tool-v1.0.0", resolve_commit(repo))
    parsed = parse_added_lines(diff)
    # The changed file contributes its added lines; an unchanged file
    # contributes nothing at all; the new file is fully added.
    assert parsed["src/a.py"].lines == frozenset({2, 4})
    assert "src/c.py" in parsed
    assert parsed["src/c.py"].lines == frozenset({1})
    renamed = parse_added_lines(
        subprocess.run(
            ["git", "-C", str(repo), "diff", "--unified=0", "--no-color",
             "--no-ext-diff", "--find-renames", "tool-v1.0.0..HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout
    )
    assert "src/b.py" not in renamed
    assert "src/c.py" in renamed


def test_release_diff_refuses_bad_arguments(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    with pytest.raises(ContractError, match="--from-tag"):
        release_diff(repo, "", resolve_commit(repo))
    with pytest.raises(ContractError, match="--commit"):
        release_diff(repo, "tool-v1.0.0", "not-a-commit")
    with pytest.raises(ContractError, match="git rev-parse failed"):
        resolve_commit(tmp_path / "not-a-repo")


def test_git_start_failure_is_a_contract_error(monkeypatch, tmp_path):
    monkeypatch.setattr("fleetd.scope.subprocess.run", _raise_oserror)
    with pytest.raises(ContractError, match="cannot be started"):
        resolve_commit(tmp_path)


def _raise_oserror(*_args, **_kwargs):
    raise OSError("git unavailable")


# ----------------------------------------------------------- plan validation
def test_plan_candidates_validates_every_row():
    assert plan_candidates(_plan(("a.py", 1))) == (
        {"id": _cid(0), "path": "a.py", "lineno": 1},
    )
    cases = (
        ({"status": "ok", "candidates": [{"id": "short", "path": "a.py", "lineno": 1}]}, "64-character"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "", "lineno": 1}]}, "repo-relative path"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": 0}]}, "positive line"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": True}]}, "positive line"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": 1}] * 2}, "duplicate id"),
        ({"status": "ok", "candidates": "nope"}, "expected an array"),
        ({"status": "ok", "candidates": ["nope"]}, "expected an object"),
    )
    for document, message in cases:
        with pytest.raises(ContractError, match=message):
            plan_candidates(document)
    with pytest.raises(ContractError, match="expected a JSON object"):
        plan_candidates("nope")
    with pytest.raises(ContractError, match="has no candidate list"):
        plan_candidates({"status": "unsupported", "reason": "foreign report"})


def test_select_changed_lines_drops_untouched_files_and_lines():
    candidates = (
        {"id": _cid(0), "path": "src/a.py", "lineno": 2},
        {"id": _cid(1), "path": "src/a.py", "lineno": 9},
        {"id": _cid(2), "path": "src/untouched.py", "lineno": 1},
        {"id": _cid(3), "path": "src/renamed.py", "lineno": 7},
    )
    added = {
        "src/a.py": AddedLines(frozenset({2}), False),
        "src/renamed.py": AddedLines(frozenset(), True),
    }
    selected, counts = select_changed_lines(candidates, added)
    assert selected == [_cid(0), _cid(3)]
    assert counts == {"files_unchanged": 1, "lines_unchanged": 1}


# ------------------------------------------------------------- scope file
def test_scope_file_absent_means_the_changed_lines_default(tmp_path):
    declared = load_scope_file(tmp_path / "r2-scope.toml")
    assert declared["mode"] is None and declared["path"] is None


def test_scope_file_declares_an_opt_in_and_validates_every_field(tmp_path):
    path = _write(
        tmp_path / "r2-scope.toml",
        "schema_version = 1\n\n[r2]\nmode = 'rolling-whole-target'\n"
        "slice_size = 40\ncursor = 'evidence/r2-cursor.json'\nnote = 'first pass covers 40'\n",
    )
    declared = load_scope_file(path)
    assert declared["mode"] == ROLLING_MODE
    assert declared["slice_size"] == 40
    assert declared["cursor"] == "evidence/r2-cursor.json"
    assert declared["note"] == "first pass covers 40"
    assert declared["path"] == str(path)

    cases = (
        ("schema_version = 2\n[r2]\nmode = 'changed-lines'\n", "schema_version must be 1"),
        ("schema_version = 1\n[r2]\nmode = 'changed-lines'\nextra = 1\n", "unknown field"),
        ("schema_version = 1\n[r2]\nmode = 'everything'\n", "r2.mode must be one of"),
        ("schema_version = 1\n[r2]\nmode = 'rolling-whole-target'\n", "requires r2.slice_size"),
        ("schema_version = 1\n[r2]\nmode = 'rolling-whole-target'\nslice_size = 0\n", "at least 1"),
        ("schema_version = 1\n[r2]\nmode = 'rolling-whole-target'\nslice_size = 4\ncursor = ''\n", "non-empty path"),
        ("schema_version = 1\n[r2]\nmode = 'changed-lines'\nnote = '  '\n", "non-empty text"),
        ("schema_version = 1\n", "expected an \\[r2\\] table"),
        ("schema_version = 1\nsurplus = 1\n[r2]\nmode = 'changed-lines'\n", "unknown field"),
    )
    for text, message in cases:
        with pytest.raises(ContractError, match=message):
            load_scope_file(_write(tmp_path / "bad.toml", text))
    with pytest.raises(ContractError, match="not valid TOML"):
        load_scope_file(_write(tmp_path / "broken.toml", "[r2\n"))
    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(ContractError, match="cannot be read"):
        load_scope_file(binary)


# ------------------------------------------------------------------ cursor
def _cursor(**overrides):
    base = {
        "schema": CURSOR_SCHEMA,
        "mode": ROLLING_MODE,
        "lane": "r2",
        "slice_size": 2,
        "offset": 2,
        "pass_index": 0,
        "list_count": 4,
        "list_digest": candidate_list_digest([_cid(i) for i in range(4)]),
        "slice_digest": candidate_list_digest([_cid(0), _cid(1)]),
        "judged_commits": ["a" * 40],
    }
    base.update(overrides)
    return base


def test_cursor_round_trips_and_validates_its_whole_shape(tmp_path):
    path = tmp_path / "cursor.json"
    write_cursor(path, _cursor())
    assert read_cursor(path) == _cursor()
    assert json.loads(path.read_text(encoding="utf-8")) == _cursor()

    cases = (
        (_cursor(schema="other/1"), "expected schema"),
        (_cursor(mode="changed-lines"), f"expected {ROLLING_MODE}"),
        (_cursor(lane=""), "non-empty lane"),
        (_cursor(slice_size=0), "at least 1"),
        (_cursor(offset=-1), "non-negative integer"),
        (_cursor(pass_index=True), "non-negative integer"),
        (_cursor(list_count="4"), "non-negative integer"),
        (_cursor(judged_commits=["not-a-commit"]), "full Git object ids"),
        (_cursor(judged_commits="abc"), "full Git object ids"),
        (_cursor(list_digest="nope"), "lowercase SHA-256"),
        (_cursor(slice_digest="nope"), "lowercase SHA-256"),
        ({**_cursor(), "surplus": 1}, "unknown field"),
        ({key: value for key, value in _cursor().items() if key != "offset"}, "missing field"),
        (["not", "an", "object"], "expected schema"),
    )
    for document, message in cases:
        with pytest.raises(ContractError, match=message):
            write_cursor(path, document)
        with pytest.raises(ContractError, match=message):
            read_cursor(_write(tmp_path / "bad.json", json.dumps(document)))
    with pytest.raises(ContractError, match="cannot be read"):
        read_cursor(tmp_path / "absent.json")
    with pytest.raises(ContractError, match="not valid JSON"):
        read_cursor(_write(tmp_path / "broken.json", "{"))


def test_rolling_slice_walks_the_list_and_starts_a_new_pass(tmp_path):
    ids = [_cid(i) for i in range(5)]
    first = rolling_slice(ids, slice_size=2, cursor=None)
    assert first.selected == tuple(ids[:2])
    assert (first.offset, first.pass_index, first.stale_reason) == (2, 0, None)

    cursor = _cursor(
        list_count=5, list_digest=candidate_list_digest(ids), offset=2,
    )
    second = rolling_slice(ids, slice_size=2, cursor=cursor)
    assert second.selected == tuple(ids[2:4]) and second.offset == 4

    last = rolling_slice(ids, slice_size=2, cursor=_cursor(
        list_count=5, list_digest=candidate_list_digest(ids), offset=4,
    ))
    assert last.selected == (ids[4],) and last.offset == 5

    wrapped = rolling_slice(ids, slice_size=2, cursor=_cursor(
        list_count=5, list_digest=candidate_list_digest(ids), offset=5, pass_index=3,
    ))
    assert wrapped.selected == tuple(ids[:2])
    assert wrapped.offset == 2 and wrapped.pass_index == 4


def test_rolling_slice_refuses_every_stale_cursor_shape():
    ids = [_cid(i) for i in range(5)]
    digest = candidate_list_digest(ids)
    cases = (
        ((2, _cursor(list_count=5, list_digest="f" * 64)), "list changed since"),
        ((2, _cursor(list_count=4, list_digest=digest)), "was taken from a list of 4"),
        ((2, _cursor(list_count=6, list_digest=digest)), "was taken from a list of 6"),
        ((3, _cursor(list_count=5, list_digest=digest)), "slice size changed"),
        ((2, _cursor(list_count=5, list_digest=digest, offset=9)), "past the end"),
    )
    for (slice_size, cursor), message in cases:
        result = rolling_slice(ids, slice_size=slice_size, cursor=cursor)
        assert result.selected == ()
        assert message in result.stale_reason
    with pytest.raises(ContractError, match="--slice-size"):
        rolling_slice(ids, slice_size=0, cursor=None)
    with pytest.raises(ContractError, match="--slice-size"):
        rolling_slice(ids, slice_size=True, cursor=None)


def test_rolling_slice_rebaseline_accepts_a_changed_list():
    ids = [_cid(i) for i in range(3)]
    stale = _cursor(list_count=4, list_digest=candidate_list_digest([_cid(i) for i in range(4)])),
    result = rolling_slice(ids, slice_size=2, cursor=stale[0], rebaseline=True)
    assert result.selected == tuple(ids[:2])
    assert result.stale_reason is None


def test_cursor_document_records_the_advanced_slice():
    ids = [_cid(i) for i in range(3)]
    result = rolling_slice(ids, slice_size=2, cursor=None)
    document = cursor_document(
        lane="r2", slice_size=2, result=result, previous=None, commit="b" * 40
    )
    assert document["offset"] == 2 and document["list_count"] == 3
    assert document["judged_commits"] == ["b" * 40]
    assert document["slice_digest"] == candidate_list_digest(ids[:2])
    again = cursor_document(
        lane="r2", slice_size=2, result=result, previous=document, commit="b" * 40
    )
    assert again["judged_commits"] == ["b" * 40]


# ---------------------------------------------------------- scope document
def test_changed_lines_scope_selects_only_lines_added_since_the_tag():
    plan = _plan(("src/a.py", 2), ("src/a.py", 3), ("src/a.py", 1), ("src/b.py", 1))
    diff = (
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -2 +2,2 @@\n-old\n+two\n+three\n"
    )
    document = scope_document(
        mode=DEFAULT_SCOPE_MODE, mode_source="--mode", lane="r2",
        from_tag="tool-v1.0.0", commit="c" * 40, plan=plan, diff_text=diff,
    )
    assert document["status"] == "ok"
    assert document["candidate_ids"] == [_cid(0), _cid(1)]
    assert document["candidate_file_sha256"] == candidate_list_digest(
        [_cid(0), _cid(1)]
    )
    assert document["files_unchanged"] == 1
    assert document["lines_unchanged"] == 1
    assert document["slice"] is None and document["cursor"] is None
    assert document["schema"] == SCOPE_SCHEMA


def test_changed_lines_scope_with_nothing_to_judge_is_an_explicit_result():
    plan = _plan(("src/untouched.py", 1))
    document = scope_document(
        mode=DEFAULT_SCOPE_MODE, mode_source="default", lane="r2",
        from_tag="tool-v1.0.0", commit="c" * 40, plan=plan,
        diff_text="--- a/src/other.py\n+++ b/src/other.py\n@@ -1 +1 @@\n-a\n+b\n",
    )
    assert document["status"] == "nothing-to-judge"
    assert "no candidate starts on a line added since tool-v1.0.0" in document["reason"]
    assert document["candidate_ids"] == []


def test_scope_document_refuses_an_unknown_mode_and_a_rolling_scope_without_a_size():
    with pytest.raises(ContractError, match="scope mode"):
        scope_document(
            mode="everything", mode_source="--mode", lane="r2", from_tag="t",
            commit="c" * 40, plan=_plan(("a.py", 1)),
        )
    with pytest.raises(ContractError, match="--slice-size"):
        scope_document(
            mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
            commit="c" * 40, plan=_plan(("a.py", 1)), slice_size=None,
        )


def test_rolling_scope_records_a_stale_cursor_and_a_covered_list():
    plan = _plan(("a.py", 1), ("a.py", 2), ("a.py", 3))
    ids = [_cid(i) for i in range(3)]
    cursor = _cursor(list_digest=candidate_list_digest([_cid(i) for i in range(9)]), list_count=9)
    stale = scope_document(
        mode=ROLLING_MODE, mode_source="scope file x", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2, cursor=cursor,
    )
    assert stale["status"] == "stale-cursor"
    assert stale["cursor"] is None and stale["slice"]["stale_reason"]

    fresh = scope_document(
        mode=ROLLING_MODE, mode_source="scope file x", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2,
    )
    assert fresh["status"] == "ok" and fresh["cursor"]["offset"] == 2
    assert fresh["slice"]["rebaselined"] is False
    assert fresh["candidate_ids"] == ids[:2]
    empty = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan={"status": "ok", "candidates": []}, slice_size=2,
    )
    assert empty["status"] == "nothing-to-judge"


def test_rolling_scope_rebaseline_is_recorded_in_the_slice():
    plan = _plan(("a.py", 1), ("a.py", 2))
    document = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2,
        cursor=_cursor(list_count=9, list_digest="f" * 64), rebaseline=True,
    )
    assert document["status"] == "ok"
    assert document["slice"]["rebaselined"] is True


# ---------------------------------------------------------------------- CLI
def _scope_argv(tmp_path, plan_rows, *, diff_text=None, extra=()):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(*plan_rows)), encoding="utf-8")
    repo = _git_repo(tmp_path / "repo")
    return [
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--from-tag", "tool-v1.0.0", *extra,
    ], repo


def test_scope_cli_writes_the_candidate_file_and_a_changed_lines_document(tmp_path, capsys):
    paths, repo = _git_repo(tmp_path / "repo"), None
    plan_path = tmp_path / "plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(_plan(("src/a.py", 2), ("src/a.py", 3), ("src/a.py", 4), ("src/b.py", 1))),
        encoding="utf-8",
    )
    out = tmp_path / "ids.txt"
    status = main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(paths), "--from-tag", "tool-v1.0.0",
        "--out", str(out),
    ])
    assert status == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["mode"] == DEFAULT_SCOPE_MODE and document["mode_source"] == "default"
    assert document["candidate_ids"] == [_cid(0), _cid(2)]
    assert out.read_text(encoding="ascii") == "".join(f"{value}\n" for value in document["candidate_ids"])


def test_scope_cli_explicit_scope_file_overrides_the_auto_discovered_one(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("a.py", 1), ("a.py", 2))))
    explicit = _write(
        tmp_path / "explicit.toml",
        "schema_version = 1\n\n[r2]\nmode = 'rolling-whole-target'\nslice_size = 1\n",
    )
    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'changed-lines'\n")
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--scope-file", str(explicit),
    ]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["mode"] == ROLLING_MODE
    assert document["mode_source"] == f"scope file {explicit}"
    assert document["candidate_ids"] == [_cid(0)]
    # An explicitly named scope file may equally be absent: it just declares
    # nothing, and the changed-lines default still needs its release tag.
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--scope-file", str(tmp_path / "absent.toml"),
    ]) == 2
    assert "--from-tag" in capsys.readouterr().err


def test_scope_cli_defaults_to_the_changed_lines_mode_when_the_scope_file_says_so(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("src/a.py", 2))))
    # A scope file that DECLARES the default is still named as the source.
    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'changed-lines'\n")
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo), "--from-tag", "tool-v1.0.0",
    ]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "ok"
    assert document["mode"] == DEFAULT_SCOPE_MODE


def test_scope_cli_reports_nothing_to_judge_and_refuses_to_write_an_empty_file(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("src/untouched.py", 1))))
    out = tmp_path / "ids.txt"
    status = main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--from-tag", "tool-v1.0.0", "--out", str(out),
    ])
    assert status == 2
    assert "--out: refusing to write an empty candidate file" in capsys.readouterr().err
    assert not out.exists()
    status = main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo), "--from-tag", "tool-v1.0.0",
    ])
    assert status == EXIT_NOTHING_TO_JUDGE
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "nothing-to-judge"


def test_scope_cli_requires_the_release_tag_and_honours_a_scope_file(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("src/a.py", 2))))
    assert main(["scope", "r2", "--plan", str(plan_path), "--repo", str(repo)]) == 2
    assert "--from-tag" in capsys.readouterr().err

    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'changed-lines'\n")
    assert main(["scope", "r2", "--plan", str(plan_path), "--repo", str(repo), "--from-tag", "tool-v1.0.0"]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["mode_source"] == f"scope file {repo / 'r2-scope.toml'}"


def test_scope_cli_rolling_cursor_round_trip(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(
        tmp_path / "plan.json",
        json.dumps(_plan(("a.py", 1), ("a.py", 2), ("a.py", 3))),
    )
    cursor = tmp_path / "cursor.json"
    common = [
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "2",
    ]
    assert main([*common, "--write-cursor", str(cursor)]) == EXIT_OK
    first = json.loads(capsys.readouterr().out)
    assert first["mode_source"] == "--mode"
    assert first["cursor"]["offset"] == 2
    assert cursor.is_file()

    assert main([*common, "--cursor", str(cursor), "--write-cursor", str(cursor)]) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["candidate_ids"] == [_cid(2)]
    assert second["cursor"]["judged_commits"] == [second["commit"]]

    _write(cursor, json.dumps(_cursor(list_count=9, list_digest="f" * 64)))
    assert main([*common, "--cursor", str(cursor)]) == EXIT_STALE_CURSOR
    stale = json.loads(capsys.readouterr().out)
    assert stale["status"] == "stale-cursor"
    assert stale["cursor"] is None
    # A changed-lines scope publishes no cursor at all; --write-cursor refuses.
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--from-tag", "tool-v1.0.0", "--write-cursor", str(cursor),
    ]) == 2
    assert "--write-cursor" in capsys.readouterr().err


def test_scope_cli_rebaseline_extends_a_changed_list(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("a.py", 1), ("a.py", 2))))
    cursor = _write(tmp_path / "cursor.json", json.dumps(_cursor(list_count=9, list_digest="f" * 64)))
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "5", "--cursor", str(cursor),
        "--rebaseline", "--write-cursor", str(cursor),
    ]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["slice"]["rebaselined"] is True
    assert document["cursor"]["list_count"] == 2


def test_scope_cli_reads_the_cursor_from_the_scope_file(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(("a.py", 1), ("a.py", 2))), )
    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'rolling-whole-target'\n"
                    "slice_size = 1\ncursor = 'evidence/cursor.json'\n")
    (repo / "evidence").mkdir()
    _write(repo / "evidence" / "cursor.json", json.dumps(
        _cursor(slice_size=1, offset=1, list_count=2,
                list_digest=candidate_list_digest([_cid(0), _cid(1)]))
    ))
    assert main(["scope", "r2", "--plan", str(plan_path), "--repo", str(repo)]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["mode_source"] == f"scope file {repo / 'r2-scope.toml'}"
    assert document["candidate_ids"] == [_cid(1)]
    assert SCOPE_MODES == ("changed-lines", "rolling-whole-target")
