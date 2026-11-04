import json
import shutil

import pytest

from conftest import GOLDEN, project_git
from rigor_record import canonical_json
from rigor_record import check as check_module
from rigor_record.check import check_project, check_waivers, load_privacy_config
from rigor_record.render import RenderError

PROJECT = GOLDEN / "project"
C4 = "4" * 40
V210 = f"rigor/commits/44/{C4}.json"


@pytest.fixture
def tree(tmp_path):
    target = tmp_path / "proj"
    shutil.copytree(PROJECT, target)
    return target


def check(path, **kwargs):
    return check_project(path, git=project_git(), **kwargs)


def messages(problems):
    return [str(p) for p in problems]


def has(problems, fragment):
    return any(fragment in m for m in messages(problems))


def test_the_golden_project_is_clean():
    assert check(PROJECT) == []


def test_a_project_without_records_is_clean(tmp_path):
    assert check(tmp_path) == []
    (tmp_path / "rigor" / "commits").mkdir(parents=True)
    assert check(tmp_path) == []


def test_hand_edited_record_is_detected(tree):
    path = tree / V210
    record = json.loads(path.read_text())
    path.write_text(json.dumps(record))  # valid, but not canonical
    assert has(check(tree), "not in canonical form")


def test_changed_record_makes_the_views_stale(tree):
    path = tree / V210
    record = json.loads(path.read_text())
    record["labels"][0]["published_at"] = "2026-11-05T18:02:11Z"
    path.write_text(canonical_json(record))
    problems = check(tree)
    assert has(problems, "RIGOR.md: differs from its regeneration")
    assert has(problems, "rigor/index.json: differs from its regeneration")


def test_the_views_follow_the_object_ids_git_reports(tree):
    # a record whose targets no longer equal the tree at its commit no longer counts: the views are stale
    problems = check_project(tree, git=project_git(default="0" * 40, trees={}))
    assert has(problems, "RIGOR.md: differs from its regeneration")


def test_invalid_record_filename_and_stray_entries(tree):
    commits = tree / "rigor" / "commits"
    bucket = commits / "44"
    (bucket / f"{C4}.json").rename(bucket / f"{'4' * 39}5.json")
    (bucket / "notes.txt").write_text("x")
    (bucket / "subdir").mkdir()
    (bucket / "link.json").symlink_to(bucket / f"{'4' * 39}5.json")
    (bucket / "broken.json").write_text("{")
    (bucket / "invalid.json").write_text("{}")
    (commits / "zz").mkdir()
    (commits / "stray.json").write_text("{}")
    problems = check(tree)
    assert has(problems, "the path is not <first two hex>/<commit>.json")
    assert has(problems, "notes.txt: only regular <commit>.json")
    assert has(problems, "subdir: only regular <commit>.json")
    assert has(problems, "link.json: only regular <commit>.json")
    assert has(problems, "broken.json: not valid JSON")
    assert has(problems, "invalid.json: $")
    assert has(problems, "commits/zz: only <aa>/ directories")
    assert has(problems, "commits/stray.json: only <aa>/ directories")


def test_unreadable_record_and_views(tree):
    (tree / V210).write_bytes(b"\xff\xfe")
    (tree / "RIGOR.md").write_bytes(b"\xff\xfe")
    problems = check(tree)
    assert has(problems, f"{C4}.json: cannot be read (UnicodeDecodeError)")
    assert has(problems, "RIGOR.md: cannot be read")


def test_missing_and_foreign_views(tree):
    (tree / "RIGOR.md").unlink()
    (tree / "rigor" / "index.json").write_text("not json")
    problems = check(tree)
    assert has(problems, "RIGOR.md: is missing")
    assert has(problems, "index.json: differs from its regeneration")
    assert has(problems, "index.json: not valid JSON")


def test_index_schema_violations_are_reported(tree):
    (tree / "rigor" / "index.json").write_text('{"schema": "x"}')
    assert has(check(tree), "index.json: $")


def test_records_of_two_projects_are_refused(tree):
    path = tree / V210
    record = json.loads(path.read_text())
    record["project"] = "other"
    path.write_text(canonical_json(record))
    assert has(check(tree), "name 2 projects")


def test_a_render_failure_is_reported(tree, monkeypatch):
    def boom(*_a, **_k):
        raise RenderError(["too big"])

    monkeypatch.setattr(check_module, "render_views", boom)
    assert has(check(tree), "RIGOR.md: too big")


def test_hand_written_files_are_validated(tree):
    (tree / "rigor" / "waivers.toml").write_bytes(b"\xff\xfe")
    assert has(check(tree), "waivers.toml: cannot be read")
    (tree / "rigor" / "waivers.toml").write_text("= nope")
    assert has(check(tree), "waivers.toml: $: not valid TOML")
    (tree / "rigor" / "waivers.toml").write_text("schema = 'wrong'")
    assert has(check(tree), "waivers.toml: $")


def test_privacy_lists_apply_to_records(tree):
    assert has(check(tree, hosts=("cgprofile",)), "privacy rule host")


# -- waiver preflight ---------------------------------------------------------------------------------------------
def test_waiver_preflight(tree):
    assert check_waivers(tree, "2.0.0") == []
    assert has(check_waivers(tree, "3.0.0"), "expired: expires_before 3.0.0 is not after 3.0.0")
    assert has(check_waivers(tree, "3.1.0"), "slow-io (R0) expired")
    (tree / "rigor" / "waivers.toml").write_text("schema = 'x'")
    assert has(check_waivers(tree, "2.0.0"), "waivers.toml: $")
    (tree / "rigor" / "waivers.toml").write_bytes(b"\xff\xfe")
    assert has(check_waivers(tree, "2.0.0"), "cannot be read")
    (tree / "rigor" / "waivers.toml").unlink()
    assert check_waivers(tree, "9.0.0") == []


# -- privacy config -----------------------------------------------------------------------------------------------
def test_privacy_config():
    assert load_privacy_config('hosts = ["a.example"]\nusers = ["bob"]\n') == (("a.example",), ("bob",))
    assert load_privacy_config("") == ((), ())
    with pytest.raises(ValueError, match="not valid TOML"):
        load_privacy_config("= x")
    with pytest.raises(ValueError, match="unknown keys: other"):
        load_privacy_config('other = 1')
    with pytest.raises(ValueError, match="hosts must be a list"):
        load_privacy_config('hosts = "a"')
    with pytest.raises(ValueError, match="users must be a list"):
        load_privacy_config('users = [""]')
