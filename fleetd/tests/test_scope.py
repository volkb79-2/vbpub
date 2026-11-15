"""The R2 default scope: changed lines since a release, and the rolling sweep."""

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
    LEGACY_CURSOR_SCHEMAS,
    ROLLING_MODE,
    SCOPE_MODES,
    SCOPE_SCHEMA,
    AddedLines,
    PlannedCandidate,
    assign_site_keys,
    candidate_key,
    candidate_list_digest,
    cursor_document,
    cursor_state_digest,
    load_cursor,
    load_scope_file,
    parse_added_lines,
    plan_candidates,
    planned_file_blobs,
    read_cursor,
    refuse_unpinned_checkout,
    release_diff,
    resolve_commit,
    rolling_slice,
    scope_document,
    select_changed_lines,
    tree_blobs,
    validate_cursor,
    write_cursor,
)

CID = "0" * 64


def _cid(value):
    return f"{value:064d}"


def _rows(*pairs):
    """Plan rows in the shape ``assay plan --json`` emits (id, path, lineno, operator)."""
    return [
        {"id": _cid(index), "path": path, "lineno": lineno, "operator": operator}
        for index, (path, lineno, operator) in enumerate(pairs)
    ]


def _plan(rows):
    return {"status": "ok", "lane": "r2", "candidate_count": len(rows), "candidates": rows}


def _git_repo(path):
    """A real repo with one tagged commit and one change -- no fake diff text."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
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


def test_tree_blobs_reads_every_file_of_a_commit(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    blobs = tree_blobs(repo, resolve_commit(repo))
    assert sorted(blobs) == ["src/a.py", "src/c.py"]
    # The blob id is the content's own object id, not the commit's.
    blob = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD:src/a.py"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert blobs["src/a.py"] == blob
    with pytest.raises(ContractError, match="full lowercase Git object id"):
        tree_blobs(repo, "HEAD")


def test_tree_blobs_keeps_a_path_with_a_tab_and_newline_parseable(tmp_path):
    repo = tmp_path / "odd"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "."], check=True)
    for name in ("plain.py", "with\ttab.py", "with\nnewline.py"):
        (repo / name).write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo, "odd names")
    blobs = tree_blobs(repo, resolve_commit(repo))
    # Every path round-trips byte-exactly through the -z record: the path is
    # everything after the FIRST tab, so neither a tab nor a newline in a name
    # can be confused with a record separator.
    assert sorted(blobs) == sorted(["plain.py", "with\ttab.py", "with\nnewline.py"])
    assert "with\ttab.py" in blobs and "with\nnewline.py" in blobs


def test_tree_blobs_skips_a_gitlink_and_refuses_an_unreadable_record(monkeypatch, tmp_path):
    repo = tmp_path / "links"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "."], check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo, "one file")
    real = subprocess.run
    payload = b"160000 commit " + b"c" * 40 + b"\tsub\0"
    payload += b"100644 blob " + b"d" * 40 + b"\ta.py\0"

    class _Done:
        returncode = 0
        stdout = payload
        stderr = b""

    commit = resolve_commit(repo)
    monkeypatch.setattr("fleetd.scope.subprocess.run", lambda *a, **k: _Done())
    assert tree_blobs(repo, commit) == {"a.py": "d" * 40}
    _Done.stdout = b"nonsense-without-tab\0"
    with pytest.raises(ContractError, match="unreadable record"):
        tree_blobs(repo, commit)
    monkeypatch.setattr("fleetd.scope.subprocess.run", real)


def test_planned_file_blobs_refuses_a_planned_path_the_commit_lacks(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    commit = resolve_commit(repo)
    assert planned_file_blobs(repo, commit, ["src/a.py", "src/c.py", "src/a.py"]) == {
        "src/a.py": tree_blobs(repo, commit)["src/a.py"],
        "src/c.py": tree_blobs(repo, commit)["src/c.py"],
    }
    with pytest.raises(ContractError, match="not files in commit"):
        planned_file_blobs(repo, commit, ["src/a.py", "src/absent.py"])


# --------------------------------------------------------- site identities
def test_site_keys_are_content_stable_and_id_independent():
    rows = _rows(
        ("src/a.py", 9, "python:compare-swap"),
        ("src/a.py", 2, "python:compare-swap"),
        ("src/a.py", 2, "python:constant"),
        ("src/b.py", 1, "python:compare-swap"),
    )
    by_path = assign_site_keys(plan_candidates(_plan(rows), require_site_identity=True))
    assert sorted(by_path) == ["src/a.py", "src/b.py"]
    # Ordinals count within one (path, operator) group in line order, so the
    # key of a site depends on its file content and operator alone.
    a_swap = [row.key for row in by_path["src/a.py"] if row.operator == "python:compare-swap"]
    assert a_swap == [candidate_key("src/a.py", "python:compare-swap", 0),
                      candidate_key("src/a.py", "python:compare-swap", 1)]
    assert by_path["src/a.py"][0].lineno == 2 and by_path["src/a.py"][1].lineno == 2
    assert by_path["src/b.py"][0].key == candidate_key("src/b.py", "python:compare-swap", 0)


def test_site_keys_do_not_change_when_only_the_candidate_ids_do():
    # Assay ids fold in the file's SHA-256; the sweep key must not. Two plans
    # of the same content with different ids therefore key identically.
    first = assign_site_keys(plan_candidates(_plan(_rows(("src/a.py", 3, "python:compare-swap")))))
    second = assign_site_keys(plan_candidates(_plan(
        [{"id": "f" * 64, "path": "src/a.py", "lineno": 3, "operator": "python:compare-swap"}]
    )))
    assert first["src/a.py"][0].id != second["src/a.py"][0].id
    assert first["src/a.py"][0].key == second["src/a.py"][0].key == candidate_key(
        "src/a.py", "python:compare-swap", 0
    )


def test_candidate_key_refuses_unorderable_input():
    for args, message in (
        (("", "op", 0), "repo-relative path"),
        (("a.py", "", 0), "mutation operator"),
        (("a.py", "op", -1), "site ordinal"),
        (("a.py", "op", True), "site ordinal"),
    ):
        with pytest.raises(ContractError, match=message):
            candidate_key(*args)


# ----------------------------------------------------------- plan validation
def test_plan_candidates_validates_every_row():
    assert plan_candidates(_plan(_rows(("a.py", 1, "python:compare-swap")))) == (
        PlannedCandidate(_cid(0), "a.py", 1, "python:compare-swap"),
    )
    # The changed-lines scope does not need the operator and accepts a plan
    # that omits it; the rolling sweep refuses one.
    assert plan_candidates({"status": "ok", "candidates": [
        {"id": _cid(0), "path": "a.py", "lineno": 1},
    ]}) == (PlannedCandidate(_cid(0), "a.py", 1, ""),)
    with pytest.raises(ContractError, match="keys each candidate by its mutation operator"):
        plan_candidates({"status": "ok", "candidates": [
            {"id": _cid(0), "path": "a.py", "lineno": 1},
        ]}, require_site_identity=True)
    cases = (
        ({"status": "ok", "candidates": [{"id": "short", "path": "a.py", "lineno": 1}]}, "64-character"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "", "lineno": 1}]}, "repo-relative path"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": 0}]}, "positive line"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": True}]}, "positive line"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": 1}] * 2}, "duplicate id"),
        ({"status": "ok", "candidates": [{"id": _cid(0), "path": "a.py", "lineno": 1, "operator": ""}]}, "non-empty operator"),
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
        PlannedCandidate(_cid(0), "src/a.py", 2),
        PlannedCandidate(_cid(1), "src/a.py", 9),
        PlannedCandidate(_cid(2), "src/untouched.py", 1),
        PlannedCandidate(_cid(3), "src/renamed.py", 7),
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
A = "a" * 40
B = "b" * 40
C = "c" * 40


def _mark(blob=A, count=1, keys=(), last_pass=0):
    return {"blob": blob, "count": count, "keys": list(keys), "last_pass": last_pass}


def _key(path, operator="python:compare-swap", ordinal=0):
    return candidate_key(path, operator, ordinal)


def _cursor(**overrides):
    base = {
        "schema": CURSOR_SCHEMA,
        "mode": ROLLING_MODE,
        "lane": "r2",
        "slice_size": 2,
        "pass_index": 0,
        "write_index": 1,
        "commit": C,
        "list_count": 2,
        "list_digest": candidate_list_digest([_cid(0), _cid(1)]),
        "files": {"src/a.py": _mark(keys=[_key("src/a.py")])},
        "judged_commits": [C],
    }
    base.update(overrides)
    return base


def _with_digest(document):
    """A cursor with its own state digest, the way write_cursor stores one."""
    body = dict(document) if isinstance(document, dict) else document
    if isinstance(body, dict):
        body["state_digest"] = cursor_state_digest(body)
    return body


def test_cursor_round_trips_and_carries_its_own_state_digest(tmp_path):
    path = tmp_path / "cursor.json"
    write_cursor(path, _cursor())
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["state_digest"] == cursor_state_digest(stored)
    assert read_cursor(path) == stored
    # A hand edit after the producer wrote the file is refused, not trusted.
    stored["files"]["src/a.py"]["keys"] = []
    _write(path, json.dumps(stored))
    with pytest.raises(ContractError, match="state_digest"):
        read_cursor(path)


def unsorted_mark():
    """Two distinct keys of one path, deliberately not in sorted order."""
    return _mark(count=2, keys=sorted(
        [_key("a.py"), _key("a.py", ordinal=1)], reverse=True
    ))


def test_cursor_validation_refuses_every_unsound_shape(tmp_path):
    path = tmp_path / "cursor.json"
    cases = (
        (_cursor(schema="other/1"), "expected schema"),
        (_cursor(mode="changed-lines"), f"expected {ROLLING_MODE}"),
        (_cursor(lane=""), "non-empty lane"),
        (_cursor(slice_size=0), "at least 1"),
        (_cursor(write_index=0), "at least 1"),
        (_cursor(pass_index=True), "non-negative integer"),
        (_cursor(list_count="4"), "non-negative integer"),
        (_cursor(commit="HEAD"), "full Git object id"),
        (_cursor(judged_commits=["not-a-commit"]), "full Git object ids"),
        (_cursor(judged_commits="abc"), "full Git object ids"),
        (_cursor(list_digest="nope"), "lowercase SHA-256"),
        (_cursor(files=[]), "keyed by repo-relative path"),
        (_cursor(files={"": _mark()}), "non-empty repo-relative path"),
        (_cursor(files={"a.py": "nope"}), "expected an object"),
        (_cursor(files={"a.py": {**_mark(), "surplus": 1}}), "unknown field"),
        (_cursor(files={"a.py": {key: value for key, value in _mark().items() if key != "blob"}}), "missing field"),
        (_cursor(files={"a.py": _mark(blob="HEAD")}), "blob: expected a full Git object id"),
        (_cursor(files={"a.py": _mark(count=-1)}), "non-negative integer"),
        (_cursor(files={"a.py": _mark(last_pass=-1)}), "non-negative integer"),
        (_cursor(files={"a.py": _mark(keys="abc")}), "candidate-key digests"),
        (_cursor(files={"a.py": _mark(keys=["nope"])}), "candidate-key digests"),
        (_cursor(files={"a.py": _mark(count=2, keys=sorted([_key("a.py"), _key("a.py", ordinal=1)]), last_pass=0)}),
         None),
        (_cursor(files={"a.py": _mark(keys=sorted([_key("a.py"), _key("a.py", ordinal=1)]), count=1)}),
         "judged key"),
        (_cursor(files={"a.py": unsorted_mark()}), "sorted order"),
        (_cursor(files={"a.py": _mark(keys=[_key("a.py"), _key("a.py")])}),
         "sorted order"),
        (_cursor(files={"a.py": _mark(last_pass=1)}), "newer than the cursor"),
        ({**_cursor(), "surplus": 1}, "unknown field"),
        ({key: value for key, value in _cursor().items() if key != "files"}, "missing field"),
        (["not", "an", "object"], "expected schema"),
    )
    for document, message in cases:
        if message is None:
            write_cursor(path, document)
            continue
        with pytest.raises(ContractError, match=message):
            write_cursor(path, _with_digest(document))
        with pytest.raises(ContractError, match=message):
            read_cursor(_write(tmp_path / "bad.json", json.dumps(_with_digest(document))))
    with pytest.raises(ContractError, match="cannot be read"):
        read_cursor(tmp_path / "absent.json")
    with pytest.raises(ContractError, match="not valid JSON"):
        read_cursor(_write(tmp_path / "broken.json", "{"))


def test_a_legacy_cursor_is_refused_with_its_migration_and_rebaseline_migrates(tmp_path):
    legacy = {
        "schema": LEGACY_CURSOR_SCHEMAS[0],
        "mode": ROLLING_MODE,
        "lane": "r2",
        "slice_size": 2,
        "offset": 2,
        "pass_index": 0,
        "list_count": 4,
        "list_digest": candidate_list_digest([_cid(i) for i in range(4)]),
        "slice_digest": candidate_list_digest([_cid(0), _cid(1)]),
        "judged_commits": [C],
    }
    path = _write(tmp_path / "legacy.json", json.dumps(legacy))
    with pytest.raises(ContractError, match="Migrate it explicitly"):
        load_cursor(path)
    with pytest.raises(ContractError, match=LEGACY_CURSOR_SCHEMAS[0]):
        read_cursor(path)
    # --rebaseline is the explicit migration: one fresh pass, no marks carried.
    assert load_cursor(path, rebaseline=True) == (None, LEGACY_CURSOR_SCHEMAS[0])


def test_load_cursor_reads_a_current_cursor_without_a_legacy_note(tmp_path):
    path = tmp_path / "cursor.json"
    write_cursor(path, _cursor())
    cursor, legacy = load_cursor(path)
    assert legacy is None and cursor["lane"] == "r2"


# ------------------------------------------------------------ rolling sweep
def _candidates(*pairs):
    return plan_candidates(_plan(_rows(*pairs)), require_site_identity=True)


def _blobs(**paths):
    return {path: blob for path, blob in paths.items()}


def test_rolling_slice_takes_a_prefix_and_covers_the_file_before_others():
    rows = _candidates(
        ("src/b.py", 3, "python:compare-swap"),
        ("src/a.py", 9, "python:compare-swap"),
        ("src/a.py", 2, "python:compare-swap"),
    )
    blobs = _blobs(**{"src/a.py": A, "src/b.py": B})
    first = rolling_slice(rows, slice_size=2, lane="r2", blobs=blobs)
    # Never-judged candidates are taken in path/line order, so a slice of one
    # file is its PREFIX -- the sweep never jumps over a candidate.
    assert first.selected == (_cid(2), _cid(1))
    assert (first.pass_index, first.new_pass, first.stale_reason) == (0, False, None)
    assert first.never_judged_candidates == 3 and first.changed_candidates == 0
    assert first.files["src/a.py"]["count"] == 2
    assert first.files["src/a.py"]["keys"] == sorted(
        [_key("src/a.py"), _key("src/a.py", ordinal=1)]
    )

    cursor = cursor_document(lane="r2", slice_size=2, result=first, previous=None, commit=C)
    second = rolling_slice(rows, slice_size=2, lane="r2", blobs=blobs, cursor=cursor)
    assert second.selected == (_cid(0),)
    # src/a.py is now fully judged and src/b.py has never been: that is one
    # never-judged candidate, not a pending one of a half-judged file.
    assert second.pending_candidates == 0 and second.never_judged_candidates == 1

    third = rolling_slice(
        rows, slice_size=2, lane="r2", blobs=blobs,
        cursor=cursor_document(lane="r2", slice_size=2, result=second, previous=cursor, commit=C),
    )
    # Every candidate is judged at this content, so the next release starts a
    # RECORDED new pass at the least recently judged file (never an empty
    # slice that claims coverage, and never a silent idle).
    assert third.selected == (_cid(2), _cid(1))
    assert third.new_pass is True and third.pass_index == 1
    assert third.judged_candidates == 3 and third.pending_candidates == 0


def test_a_release_that_edits_a_file_does_not_stale_the_sweep():
    # FD-033: the ids fold in the file hash, so an edit changes them. The sweep
    # key must not care: the edited file is due again (judged whole), every
    # other file's marks survive, and the cursor is never stale.
    before = _candidates(
        ("src/a.py", 2, "python:compare-swap"),
        ("src/b.py", 1, "python:compare-swap"),
        ("src/b.py", 9, "python:compare-swap"),
    )
    blobs = _blobs(**{"src/a.py": A, "src/b.py": B})
    first = rolling_slice(before, slice_size=1, lane="r2", blobs=blobs)
    assert first.selected == (_cid(0),)
    cursor = cursor_document(lane="r2", slice_size=1, result=first, previous=None, commit=C)
    assert sorted(cursor["files"]) == ["src/a.py"]

    # src/a.py was edited: other blob, other ids, two sites. src/b.py did not
    # move, so its two candidates are still never-judged and come after the
    # edited file's sites.
    after = plan_candidates(_plan([
        {"id": "1" * 64, "path": "src/a.py", "lineno": 2, "operator": "python:compare-swap"},
        {"id": "2" * 64, "path": "src/a.py", "lineno": 5, "operator": "python:compare-swap"},
        {"id": "3" * 64, "path": "src/b.py", "lineno": 1, "operator": "python:compare-swap"},
        {"id": "4" * 64, "path": "src/b.py", "lineno": 9, "operator": "python:compare-swap"},
    ]), require_site_identity=True)
    changed = _blobs(**{"src/a.py": "d" * 40, "src/b.py": B})

    edited = rolling_slice(after, slice_size=1, lane="r2", blobs=changed, cursor=cursor)
    assert edited.stale_reason is None
    assert edited.selected == ("1" * 64,)
    assert edited.changed_files == 1 and edited.changed_candidates == 2
    # The edited file's second site comes before any never-judged file, so an
    # edit never splits its own re-judgement across another file's work.
    second = rolling_slice(
        after, slice_size=2, lane="r2", blobs=changed,
        cursor=cursor_document(lane="r2", slice_size=1, result=edited, previous=cursor, commit=C),
    )
    assert second.selected == ("2" * 64, "3" * 64)
    assert second.files["src/a.py"]["keys"] == sorted(
        [_key("src/a.py"), _key("src/a.py", ordinal=1)]
    )
    third = rolling_slice(
        after, slice_size=1, lane="r2", blobs=changed,
        cursor=cursor_document(lane="r2", slice_size=2, result=second,
                               previous=cursor_document(lane="r2", slice_size=1, result=edited,
                                                        previous=cursor, commit=C), commit=C),
    )
    # The sweep resumes where the marks are, never at the top of the list.
    assert third.selected == ("4" * 64,)


def _sweep(files, edits, slice_size):
    """Walk one synthetic target across releases and return what each release judged.

    ``files`` maps a path to its candidate count, and ``edits[release]`` maps a
    path to that release's content epoch: a path whose epoch changed has new
    candidate ids, sites and blob (an edit), while every other path keeps both.
    A candidate is identified by ``(path, line, epoch)`` so coverage is
    asserted per CONTENT, not per rotating Assay id.
    """
    cursor = None
    judged: list[dict] = []
    for release, epoch in enumerate(edits):
        rows = [
            {
                "id": candidate_list_digest([f"{path}|{epoch[path]}|{index}"])[:32] + f"{index:032d}",
                "path": path,
                "lineno": index + 1,
                "operator": "python:compare-swap",
            }
            for path in epoch
            for index in range(files[path])
        ]
        plan = plan_candidates(_plan(rows), require_site_identity=True)
        blobs = {path: candidate_list_digest([f"blob|{path}|{value}"]) for path, value in epoch.items()}
        result = rolling_slice(
            plan, slice_size=slice_size, lane="r2", blobs=blobs, cursor=cursor,
        )
        assert result.stale_reason is None
        judged.append({
            "release": release,
            "selected": [
                (row.path, row.lineno, epoch[row.path])
                for row in plan if row.id in result.selected
            ],
            "count": len(result.selected),
            "changed_files": result.changed_files,
            "pass_index": result.pass_index,
            "new_pass": result.new_pass,
        })
        cursor = cursor_document(
            lane="r2", slice_size=slice_size, result=result, previous=cursor, commit=C
        )
    return judged


def test_the_sweep_covers_the_whole_target_across_releases():
    files = {f"src/f{index}.py": 10 for index in range(6)}
    edits = [{path: 0 for path in files} for _ in range(5)]
    judged = _sweep(files, edits, slice_size=15)
    # 60 candidates, 15 per release: ceil(60/15) = 4 releases sweep the target
    # ONCE, and the fifth release starts pass 1 rather than reporting an empty,
    # coverage-claiming slice.
    assert [item["count"] for item in judged] == [15, 15, 15, 15, 15]
    assert [item["pass_index"] for item in judged] == [0, 0, 0, 0, 1]
    assert [item["new_pass"] for item in judged] == [False, False, False, False, True]
    first_pass = {entry for item in judged[:4] for entry in item["selected"]}
    assert len(first_pass) == 60
    # The fifth release re-judges pass 0's first slice, not a partial tail: a
    # full target is a full new pass, never a half-covered leftover.


def test_the_sweep_keeps_covering_after_edits_between_releases():
    files = {f"src/f{index}.py": 5 for index in range(4)}
    edited = {"src/f0.py": 1, **{path: 0 for path in files if path != "src/f0.py"}}
    edits = [
        {path: 0 for path in files},
        edited,
        edited,
        edited,
        edited,
    ]
    judged = _sweep(files, edits, slice_size=5)
    # 20 candidates, 5 a release, and one file edited between releases 0 and 1:
    # the sweep still covers every path within 5 releases, the edited file is
    # judged WHOLE in the release that edited it (and again at its new content),
    # and no candidate is ever judged when its file did not change.
    assert [item["count"] for item in judged] == [5, 5, 5, 5, 5]
    # Only release 1 changed a file: every later release keeps f0 at its new
    # content, so nothing else is re-due.
    assert [item["changed_files"] for item in judged] == [0, 1, 0, 0, 0]
    covered = {entry for item in judged for entry in item["selected"]}
    for path in files:
        assert any(entry[0] == path for entry in covered), path
    # Both contents of the edited file are covered: the first pass judged
    # (f0, 0), the release after the edit judged (f0, 1) whole.
    assert any(entry[0] == "src/f0.py" and entry[2] == 0 for entry in covered)
    assert any(entry[0] == "src/f0.py" and entry[2] == 1 for entry in covered)


def test_the_sweep_is_deterministic_for_the_same_inputs():
    files = {f"src/f{index}.py": 4 for index in range(3)}
    edits = [{path: 0 for path in files} for _ in range(6)]
    first = _sweep(files, edits, slice_size=3)
    second = _sweep(files, edits, slice_size=3)
    assert first == second
    assert [item["selected"] for item in first][:3] == sorted(
        (item["selected"] for item in first[:3]), key=lambda rows: rows[0]
    )


def test_rolling_slice_accounts_for_missing_and_retired_paths_instead_of_dropping_them():
    rows = _candidates(("src/kept.py", 1, "python:compare-swap"))
    blobs = _blobs(**{"src/kept.py": A})
    cursor = _with_digest(_cursor(files={
        "src/kept.py": _mark(blob=A, keys=[_key("src/kept.py")], last_pass=2),
        "src/gone.py": _mark(blob=B, count=2, keys=[_key("src/gone.py")], last_pass=1),
    }, pass_index=2))
    result = rolling_slice(
        rows, slice_size=1, lane="r2", blobs=blobs, cursor=cursor,
    )
    # The deleted file's two judged candidates are ACCOUNTED as retired. The
    # surviving file was already judged at its content, so this release starts
    # a new pass (pass 3) rather than reporting an empty, coverage-claiming
    # scope or silently dropping the retired two.
    assert result.retired == ({
        "path": "src/gone.py",
        "candidates": 2,
        "last_judged_pass": 1,
        "reason": "no planned candidate remains at this path",
    },)
    assert result.new_pass is True and result.pass_index == 3
    assert result.selected == (_cid(0),)
    # A retired path keeps no mark.
    assert "src/gone.py" not in result.files
    assert result.files["src/kept.py"]["last_pass"] == 3


def test_a_plan_shape_change_makes_a_path_due_again_rather_than_judged():
    # A path whose blob is unchanged but whose planned candidate COUNT changed
    # (an operator-selection change) may not be treated as judged: the ordinal
    # key would otherwise mark an unjudged site as judged.
    rows = _candidates(
        ("src/a.py", 2, "python:compare-swap"),
        ("src/a.py", 5, "python:compare-swap"),
    )
    blobs = _blobs(**{"src/a.py": A})
    cursor = _with_digest(_cursor(files={"src/a.py": _mark(blob=A, count=1, keys=[_key("src/a.py")])}))
    result = rolling_slice(rows, slice_size=2, lane="r2", blobs=blobs, cursor=cursor)
    assert result.selected == (_cid(0), _cid(1))
    assert result.changed_candidates == 2 and result.changed_files == 1
    # The judged key is NOT carried over: the path is re-judged from scratch.
    assert result.files["src/a.py"]["keys"] == sorted(
        [_key("src/a.py"), _key("src/a.py", ordinal=1)]
    )


def test_a_judged_key_the_plan_no_longer_contains_makes_the_path_due_again():
    rows = _candidates(("src/a.py", 2, "python:compare-swap"))
    blobs = _blobs(**{"src/a.py": A})
    cursor = _with_digest(_cursor(files={
        "src/a.py": _mark(blob=A, count=1, keys=[_key("src/a.py", "python:constant")]),
    }))
    result = rolling_slice(rows, slice_size=1, lane="r2", blobs=blobs, cursor=cursor)
    assert result.selected == (_cid(0),)
    assert result.changed_candidates == 1


def test_a_slice_that_does_not_reach_a_path_keeps_its_mark_and_recency():
    rows = _candidates(
        ("src/a.py", 1, "python:compare-swap"),
        ("src/b.py", 1, "python:compare-swap"),
        ("src/b.py", 2, "python:compare-swap"),
    )
    blobs = _blobs(**{"src/a.py": A, "src/b.py": B})
    first = rolling_slice(rows, slice_size=3, lane="r2", blobs=blobs)
    cursor = cursor_document(lane="r2", slice_size=1, result=first, previous=None, commit=C)
    rows_b = plan_candidates(_plan([
        {"id": "1" * 64, "path": "src/a.py", "lineno": 1, "operator": "python:compare-swap"},
        {"id": "2" * 64, "path": "src/b.py", "lineno": 1, "operator": "python:compare-swap"},
        {"id": "3" * 64, "path": "src/b.py", "lineno": 2, "operator": "python:compare-swap"},
        {"id": "4" * 64, "path": "src/c.py", "lineno": 1, "operator": "python:compare-swap"},
    ]), require_site_identity=True)
    changed = _blobs(**{"src/a.py": "d" * 40, "src/b.py": B, "src/c.py": C})
    # src/a.py changed, so it is judged whole first; src/b.py and the brand-new
    # src/c.py are not reached by this one-candidate slice and keep their state
    # (src/b.py its pass stamp, src/c.py still never judged).
    result = rolling_slice(
        rows_b, slice_size=1, lane="r2", blobs=changed, cursor=cursor,
    )
    assert result.selected == ("1" * 64,)
    # src/b.py was not reached by this slice, so its mark (and its pass stamp)
    # is carried over verbatim rather than re-judged or dropped.
    assert result.files["src/b.py"] == cursor["files"]["src/b.py"]
    follow = rolling_slice(
        rows_b, slice_size=1, lane="r2", blobs=changed,
        cursor=cursor_document(lane="r2", slice_size=1, result=result, previous=cursor, commit=C),
    )
    # The never-judged file comes before the already-judged src/b.py, so no
    # candidate of a file is starved behind another file's markers.
    assert follow.selected == ("4" * 64,)


def test_rolling_slice_refuses_a_cursor_written_for_another_lane():
    rows = _candidates(("src/a.py", 1, "python:compare-swap"))
    result = rolling_slice(
        rows, slice_size=1, lane="r3", blobs=_blobs(**{"src/a.py": A}),
        cursor=_with_digest(_cursor(lane="r2")),
    )
    assert result.selected == () and "written for lane 'r2'" in result.stale_reason
    assert result.stale_reason is not None


def test_rolling_slice_refuses_a_bad_slice_size_and_a_path_the_commit_lacks():
    rows = _candidates(("src/a.py", 1, "python:compare-swap"))
    for size in (0, True):
        with pytest.raises(ContractError, match="--slice-size"):
            rolling_slice(rows, slice_size=size, lane="r2", blobs=_blobs(**{"src/a.py": A}))
    with pytest.raises(ContractError, match="the commit does not describe"):
        rolling_slice(rows, slice_size=1, lane="r2", blobs={})


def test_cursor_document_counts_writes_and_judged_commits():
    rows = _candidates(("src/a.py", 1, "python:compare-swap"), ("src/a.py", 2, "python:compare-swap"))
    result = rolling_slice(rows, slice_size=1, lane="r2", blobs=_blobs(**{"src/a.py": A}))
    document = cursor_document(lane="r2", slice_size=1, result=result, previous=None, commit=C)
    assert document["write_index"] == 1 and document["judged_commits"] == [C]
    again = cursor_document(lane="r2", slice_size=1, result=result, previous=document, commit=C)
    assert again["write_index"] == 2 and again["judged_commits"] == [C]
    other = cursor_document(lane="r2", slice_size=1, result=result, previous=again, commit=B)
    assert other["judged_commits"] == [C, B]


# ---------------------------------------------------------- scope document
def test_changed_lines_scope_selects_only_lines_added_since_the_tag():
    plan = _plan(_rows(
        ("src/a.py", 2, "python:compare-swap"),
        ("src/a.py", 3, "python:compare-swap"),
        ("src/a.py", 1, "python:compare-swap"),
        ("src/b.py", 1, "python:compare-swap"),
    ))
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
    plan = _plan(_rows(("src/untouched.py", 1, "python:compare-swap")))
    document = scope_document(
        mode=DEFAULT_SCOPE_MODE, mode_source="default", lane="r2",
        from_tag="tool-v1.0.0", commit="c" * 40, plan=plan,
        diff_text="--- a/src/other.py\n+++ b/src/other.py\n@@ -1 +1 @@\n-a\n+b\n",
    )
    assert document["status"] == "nothing-to-judge"
    assert "no candidate starts on a line added since tool-v1.0.0" in document["reason"]
    assert document["candidate_ids"] == []


def test_scope_document_refuses_an_unknown_mode_and_a_rolling_scope_without_its_inputs():
    with pytest.raises(ContractError, match="scope mode"):
        scope_document(
            mode="everything", mode_source="--mode", lane="r2", from_tag="t",
            commit="c" * 40, plan=_plan(_rows(("a.py", 1, "python:compare-swap"))),
        )
    with pytest.raises(ContractError, match="--slice-size"):
        scope_document(
            mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
            commit="c" * 40, plan=_plan(_rows(("a.py", 1, "python:compare-swap"))), slice_size=None,
        )
    with pytest.raises(ContractError, match="blob ids at commit"):
        scope_document(
            mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
            commit="c" * 40, plan=_plan(_rows(("a.py", 1, "python:compare-swap"))), slice_size=1,
        )


def test_rolling_scope_records_a_stale_cursor_and_a_covered_list():
    plan = _plan(_rows(*[(f"src/{index}.py", 1, "python:compare-swap") for index in range(3)]))
    blobs = {f"src/{index}.py": A for index in range(3)}
    stale = scope_document(
        mode=ROLLING_MODE, mode_source="scope file x", lane="r3", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2, cursor=_with_digest(_cursor(lane="r2")),
        blobs=blobs,
    )
    assert stale["status"] == "stale-cursor"
    assert stale["cursor"] is None and stale["slice"]["stale_reason"]

    fresh = scope_document(
        mode=ROLLING_MODE, mode_source="scope file x", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2, blobs=blobs,
    )
    assert fresh["status"] == "ok" and len(fresh["candidate_ids"]) == 2
    assert fresh["cursor"]["write_index"] == 1 and fresh["cursor"]["pass_index"] == 0
    assert fresh["slice"]["rebaselined"] is False
    assert fresh["slice"]["legacy_cursor"] is None
    empty = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan={"status": "ok", "candidates": []}, slice_size=2, blobs={},
    )
    assert empty["status"] == "nothing-to-judge"
    assert "candidate list is empty" in empty["reason"]


def test_a_fully_judged_target_starts_a_recorded_new_pass_not_an_empty_scope():
    plan = _plan(_rows(("src/a.py", 1, "python:compare-swap")))
    blobs = {"src/a.py": A}
    first = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=1, blobs=blobs,
    )
    covered = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=1, blobs=blobs, cursor=first["cursor"],
    )
    # A finished sweep never reports "nothing to judge" while candidates exist:
    # that status means the LIST is empty. It starts pass 1 here, and the
    # document says so, so a report can see that this release re-judged rather
    # than covered new ground.
    assert covered["status"] == "ok"
    assert covered["slice"]["new_pass"] is True
    assert covered["slice"]["pass_index"] == 1
    assert covered["slice"]["judged_candidates"] == 1
    assert covered["cursor"]["pass_index"] == 1
    # An EMPTY plan is the one honest nothing-to-judge of rolling mode.
    empty = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan={"status": "ok", "candidates": []}, slice_size=1, blobs={},
    )
    assert empty["status"] == "nothing-to-judge"
    assert "candidate list is empty" in empty["reason"]


def test_rolling_scope_rebaseline_is_recorded_in_the_slice():
    plan = _plan(_rows(("a.py", 1, "python:compare-swap"), ("a.py", 2, "python:compare-swap")))
    document = scope_document(
        mode=ROLLING_MODE, mode_source="--mode", lane="r2", from_tag="t",
        commit="c" * 40, plan=plan, slice_size=2, blobs={"a.py": A},
        cursor=_with_digest(_cursor(files={"a.py": _mark(blob=B, keys=[_key("a.py")])})),
        rebaseline=True, legacy_cursor=LEGACY_CURSOR_SCHEMAS[0],
    )
    assert document["status"] == "ok"
    assert document["slice"]["rebaselined"] is True
    assert document["slice"]["legacy_cursor"] == LEGACY_CURSOR_SCHEMAS[0]
    assert document["cursor"]["files"]["a.py"]["keys"] == sorted(
        [_key("a.py"), _key("a.py", ordinal=1)]
    )


# ---------------------------------------------------------------------- CLI
def _scope_argv(tmp_path, plan_rows, *, extra=()):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(plan_rows)), encoding="utf-8")
    repo = _git_repo(tmp_path / "repo")
    return [
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--from-tag", "tool-v1.0.0", *extra,
    ], repo


def test_scope_cli_writes_the_candidate_file_and_a_changed_lines_document(tmp_path, capsys):
    paths = _git_repo(tmp_path / "repo")
    plan_path = tmp_path / "plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(_plan(_rows(
            ("src/a.py", 2, "python:compare-swap"),
            ("src/a.py", 3, "python:compare-swap"),
            ("src/a.py", 4, "python:compare-swap"),
            ("src/b.py", 1, "python:compare-swap"),
        ))),
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
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(_rows(
        ("a.py", 1, "python:compare-swap"), ("a.py", 2, "python:compare-swap"),
    ))))
    explicit = _write(
        tmp_path / "explicit.toml",
        "schema_version = 1\n\n[r2]\nmode = 'rolling-whole-target'\nslice_size = 1\n",
    )
    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'changed-lines'\n")
    # a.py is a real path in the repository, so the sweep can key it.
    _write(repo / "a.py", "one\ntwo\n")
    _commit_all(repo, "add a.py")
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


def test_scope_cli_refuses_a_rolling_plan_path_the_commit_does_not_contain(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(
        _rows(("src/a.py", 1, "python:compare-swap"), ("src/absent.py", 1, "python:compare-swap"))
    )))
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "1",
    ]) == 2
    assert "not files in commit" in capsys.readouterr().err


def test_scope_cli_defaults_to_the_changed_lines_mode_when_the_scope_file_says_so(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(_rows(("src/a.py", 2, "python:compare-swap")))))
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
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(_rows(("src/untouched.py", 1, "python:compare-swap")))))
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
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(_rows(("src/a.py", 2, "python:compare-swap")))))
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
        json.dumps(_plan(_rows(
            ("src/a.py", 1, "python:compare-swap"),
            ("src/a.py", 2, "python:compare-swap"),
            ("src/a.py", 3, "python:compare-swap"),
        ))),
    )
    cursor = tmp_path / "cursor.json"
    common = [
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "2",
    ]
    assert main([*common, "--write-cursor", str(cursor)]) == EXIT_OK
    first = json.loads(capsys.readouterr().out)
    assert first["mode_source"] == "--mode"
    assert first["slice"]["selected_count"] == 2
    assert first["cursor"]["write_index"] == 1
    assert cursor.is_file()

    assert main([*common, "--cursor", str(cursor), "--write-cursor", str(cursor)]) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["candidate_ids"] == [_cid(2)]
    assert second["cursor"]["judged_commits"] == [second["commit"]]
    assert second["cursor"]["write_index"] == 2

    # A lane mismatch is a refusal (exit 3) whose document still prints.
    stored = json.loads(cursor.read_text(encoding="utf-8"))
    stored["lane"] = "other"
    write_cursor(cursor, stored)
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


def test_scope_cli_refuses_a_legacy_cursor_and_migrates_it_only_with_rebaseline(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(
        _rows(("src/a.py", 1, "python:compare-swap"), ("src/a.py", 2, "python:compare-swap"))
    )))
    legacy = _write(tmp_path / "cursor.json", json.dumps({
        "schema": LEGACY_CURSOR_SCHEMAS[0],
        "mode": ROLLING_MODE,
        "lane": "r2",
        "slice_size": 1,
        "offset": 1,
        "pass_index": 0,
        "list_count": 2,
        "list_digest": candidate_list_digest([_cid(0), _cid(1)]),
        "slice_digest": candidate_list_digest([_cid(0)]),
        "judged_commits": ["a" * 40],
    }))
    common = [
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "1",
    ]
    assert main([*common, "--cursor", str(legacy)]) == 2
    assert "Migrate it explicitly" in capsys.readouterr().err
    assert main([*common, "--cursor", str(legacy), "--rebaseline",
                 "--write-cursor", str(legacy)]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["slice"]["rebaselined"] is True
    assert document["slice"]["legacy_cursor"] == LEGACY_CURSOR_SCHEMAS[0]
    assert document["candidate_ids"] == [_cid(0)]
    assert json.loads(legacy.read_text(encoding="utf-8"))["schema"] == CURSOR_SCHEMA


def test_scope_cli_reads_the_cursor_from_the_scope_file(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    _write(repo / "src" / "d.py", "delta\n")
    _commit_all(repo, "add d.py")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(
        _rows(("src/d.py", 1, "python:compare-swap"))
    )))
    _write(repo / "r2-scope.toml", "schema_version = 1\n\n[r2]\nmode = 'rolling-whole-target'\n"
                    "slice_size = 1\ncursor = 'evidence/cursor.json'\n")
    (repo / "evidence").mkdir()
    first = main(["scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
                  "--write-cursor", str(repo / "evidence" / "cursor.json")])
    assert first == EXIT_OK
    assert json.loads(capsys.readouterr().out)["mode_source"] == f"scope file {repo / 'r2-scope.toml'}"
    # The circle is complete: the scope file's cursor resumes the sweep on the
    # next release, which is a recorded new pass rather than a silent repeat
    # or an empty scope that claims coverage.
    assert main(["scope", "r2", "--plan", str(plan_path), "--repo", str(repo)]) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["slice"]["new_pass"] is True and second["slice"]["pass_index"] == 1
    assert second["candidate_ids"] == [_cid(0)]
    assert SCOPE_MODES == ("changed-lines", "rolling-whole-target")


def test_scope_cli_rebaseline_restarts_a_pass_from_the_current_content(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    plan_path = _write(tmp_path / "plan.json", json.dumps(_plan(
        _rows(("src/a.py", 1, "python:compare-swap"), ("src/a.py", 2, "python:compare-swap"))
    )))
    cursor = tmp_path / "cursor.json"
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "1", "--write-cursor", str(cursor),
    ]) == EXIT_OK
    capsys.readouterr()
    stored = json.loads(cursor.read_text(encoding="utf-8"))
    stored["pass_index"] = 4
    write_cursor(cursor, stored)
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--mode", ROLLING_MODE, "--slice-size", "5", "--cursor", str(cursor),
        "--rebaseline", "--write-cursor", str(cursor),
    ]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["slice"]["rebaselined"] is True
    # A rebaseline starts ONE new pass (pass 4 -> 5) from the current content
    # instead of resuming the old marks; --slice-size 5 then takes the whole
    # file in that single new pass.
    assert document["slice"]["pass_index"] == 5
    assert document["cursor"]["pass_index"] == 5
    assert document["slice"]["selected_count"] == 2


# --------------------------------------------- hostile real-git inputs (review)
def _run_git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


def _hostile_repo(tmp_path):
    repo = tmp_path / "hostile"
    repo.mkdir()
    _run_git(repo, "init", "-q", ".")
    (repo / "a_ff.py").write_bytes(b"q=1\n")
    (repo / "b_space name.py").write_bytes(b"x=1\ny=2\n")
    (repo / "c_latin1.py").write_bytes(b"caf\xe9 = 1\n")
    (repo / "d_cr.py").write_bytes(b"k = 1\n")
    (repo / "e_u2028.py").write_bytes(b"z = 1\n")
    (repo / "f_last.py").write_bytes(b"a = 1\nb = 2\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "tag", "tool-v1.0.0")
    # A form feed inside an added line, a path with a space, a non-UTF-8
    # changed file, a lone carriage return and U+2028 inside source lines.
    (repo / "a_ff.py").write_bytes(b"q=1\nA\x0cB = 2\nC = 3\n")
    (repo / "b_space name.py").write_bytes(b"x=1\ny=22\n")
    (repo / "c_latin1.py").write_bytes(b"caf\xe9 = 2\n")
    (repo / "d_cr.py").write_bytes(b"k = 1\nS = 'a\rb'\nT = 2\n")
    (repo / "e_u2028.py").write_bytes(b"z = 1\nS = '\xe2\x80\xa8'\nT = 2\n")
    (repo / "f_last.py").write_bytes(b"a = 1\nb = 22\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "second")
    return repo


def test_scope_exit_statuses_are_the_published_numbers():
    # CONSUMERS.md and SPEC.md publish these literals; a test that compares the
    # constants with themselves would pass if one were changed to 0.
    assert (EXIT_OK, EXIT_STALE_CURSOR, EXIT_NOTHING_TO_JUDGE) == (0, 3, 4)


def test_dirty_or_moved_checkout_is_refused_and_untracked_files_are_not(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    head = resolve_commit(repo)
    refuse_unpinned_checkout(repo, head)  # clean: accepted
    (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    refuse_unpinned_checkout(repo, head)  # untracked files move no tracked line
    (repo / "src" / "a.py").write_text("edited\n", encoding="utf-8")
    with pytest.raises(ContractError, match="uncommitted changes to tracked files"):
        refuse_unpinned_checkout(repo, head)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(_rows(("src/a.py", 2, "python:compare-swap")))), encoding="utf-8")
    assert main([
        "scope", "r2", "--plan", str(plan_path), "--repo", str(repo),
        "--from-tag", "tool-v1.0.0",
    ]) == 2
    assert "uncommitted changes" in capsys.readouterr().err
    with pytest.raises(ContractError, match="is not the checkout's HEAD"):
        refuse_unpinned_checkout(repo, "0" * 40)


def test_hostile_source_bytes_never_shift_or_drop_a_later_file(tmp_path):
    repo = _hostile_repo(tmp_path)
    parsed = parse_added_lines(release_diff(repo, "tool-v1.0.0", resolve_commit(repo)))
    assert {path: sorted(entry.lines) for path, entry in parsed.items()} == {
        "a_ff.py": [2, 3],
        "b_space name.py": [2],
        "c_latin1.py": [1],
        "d_cr.py": [2, 3],
        "e_u2028.py": [2, 3],
        "f_last.py": [2],
    }
    # The same hostile tree is sweepable: every planned path keeps an
    # unambiguous blob id, including the one with a space in its name.
    blobs = planned_file_blobs(repo, resolve_commit(repo), sorted(parsed))
    assert sorted(blobs) == sorted(parsed)


def test_validate_cursor_is_the_same_shape_reader_and_writer_use():
    document = _with_digest(_cursor())
    assert validate_cursor(document) == document
    assert validate_cursor(document, "cursor x") == document


def test_rebaseline_still_reports_retired_paths_from_the_discarded_cursor():
    rows = _candidates(("src/kept.py", 1, "python:compare-swap"))
    blobs = _blobs(**{"src/kept.py": A})
    cursor = _with_digest(_cursor(files={
        "src/kept.py": _mark(blob=A, keys=[_key("src/kept.py")], last_pass=2),
        "src/gone.py": _mark(blob=B, count=3, keys=[_key("src/gone.py")], last_pass=1),
    }, pass_index=2))
    result = rolling_slice(
        rows, slice_size=1, lane="r2", blobs=blobs, cursor=cursor, rebaseline=True,
    )
    # The discarded marks do not become invisible: the retired path is still
    # named, with the evidence the previous cursor held about it.
    assert result.retired == ({
        "path": "src/gone.py",
        "candidates": 3,
        "last_judged_pass": 1,
        "reason": "no planned candidate remains at this path",
    },)
    assert result.new_pass is False
    assert result.pass_index == 3
