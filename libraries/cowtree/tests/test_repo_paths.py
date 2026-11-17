from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cowtree import repo_paths
from cowtree.errors import BackendFailed, PathRefused, RepoPathsInvalid, RootInvalid, UsageError
from cowtree.repo_paths import (
    PathEntry,
    Resolved,
    find_repo_root,
    load_repo_paths,
    parse_repo_paths,
)

from .fakes import REPO_PATHS_TEXT, FakeBtrfs, FakeOps, btrfs_area, write_repo

REAL_REPO_ROOT = Path(__file__).parents[3]
WRAPPER = Path(__file__).parents[1] / "repo-path"


def _cfg(body: str) -> str:
    return 'schema = 1\nrepo = "r"\n' + body


# -- config validation -------------------------------------------------------------------------------------------

REFUSALS = [
    ("schema = \n", "invalid TOML"),
    ('[paths.a]\nrepo = "x"\n[paths.a]\nrepo = "y"\n', "invalid TOML"),
    ('schema = 1\nextra = 1\n[paths.a]\nrepo = "x"\n', "unknown top-level keys: extra"),
    ('schema = 2\n[paths.a]\nrepo = "x"\n', "schema must be 1, not 2"),
    ('[paths.a]\nrepo = "x"\n', "schema must be 1, not None"),
    ('schema = true\n[paths.a]\nrepo = "x"\n', "schema must be 1, not True"),
    ('schema = "1"\n[paths.a]\nrepo = "x"\n', "schema must be 1"),
    ("schema = 1\n", "top-level repo must be one directory name"),
    ('schema = 1\n[paths.a]\nrepo = "x"\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = ""\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = 3\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = "a/b"\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = "a\\\\b"\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = ".."\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = ".hidden"\n', "top-level repo must be one directory name"),
    ('schema = 1\nrepo = "shared"\n', "top-level repo must be one directory name .*shared"),
    ('schema = 1\nrepo = "r"\n', "paths must be a table"),
    ('schema = 1\nrepo = "r"\npaths = 1\n', "paths must be a table"),
    ('schema = 1\nrepo = "r"\n[paths]\na = 1\n', "paths.a must be a table"),
    ('schema = 1\nrepo = "r"\n[paths.""]\nrepo = "x"\n', "name must not be empty"),
    (_cfg('[paths.a]\nrepo = "x"\nfoo = 1\nbar = 2\n'), "paths.a has unknown keys: bar, foo"),
    (_cfg('[paths.a]\ncow = "x"\n'), "paths.a.repo must be a non-empty string"),
    (_cfg('[paths.a]\nrepo = ""\n'), "paths.a.repo must be a non-empty string"),
    (_cfg("[paths.a]\nrepo = 3\n"), "paths.a.repo must be a non-empty string"),
    (_cfg('[paths.a]\nrepo = "x"\ncow = ""\n'), "paths.a.cow must be a non-empty string"),
    (_cfg('[paths.a]\nrepo = "/abs/x"\n'), "paths.a.repo must be relative, not absolute"),
    (_cfg('[paths.a]\nrepo = "x"\ncow = "/abs/x"\n'), "paths.a.cow must be relative, not absolute"),
    (_cfg('[paths.a]\nrepo = "a/../b"\n'), "paths.a.repo must not contain"),
    (_cfg('[paths.a]\nrepo = ".."\n'), "paths.a.repo must not contain"),
    (_cfg('[paths.a]\nrepo = "a//b"\n'), "paths.a.repo must not contain"),
    (_cfg('[paths.a]\nrepo = "./a"\n'), "paths.a.repo must not contain"),
    (_cfg('[paths.a]\nrepo = "a/"\n'), "paths.a.repo must not contain"),
    (_cfg('[paths.a]\nrepo = "x"\ncow = "../x"\n'), "paths.a.cow must not contain"),
    (_cfg('[paths.a]\nrepo = "{other}/x"\n'), "paths.a.repo may only use the {project} placeholder"),
    (_cfg('[paths.a]\nrepo = "{project/x"\n'), "paths.a.repo may only use the {project} placeholder"),
    (_cfg('[paths.a]\nrepo = "x}"\n'), "paths.a.repo may only use the {project} placeholder"),
    (_cfg('[paths.a]\nrepo = "{project}/x"\ncow = "{p}/x"\n'), "paths.a.cow may only use the {project} placeholder"),
    (_cfg('[paths.a]\nrepo = "{project}/x"\ncow = "x"\n'), "{project} must appear in both repo and cow or in neither"),
    (_cfg('[paths.a]\nrepo = "x"\ncow = "{project}/x"\n'), "{project} must appear in both repo and cow or in neither"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = "gzip"\n'), "paths.a.compression must be none or zstd"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = "zstd:3"\n'), "paths.a.compression must be none or zstd"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = "zstd:15"\n'), "paths.a.compression must be none or zstd"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = "lzo"\n'), "paths.a.compression must be none or zstd"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = "ZSTD"\n'), "paths.a.compression must be"),
    (_cfg('[paths.a]\nrepo = "x"\ncompression = 3\n'), "paths.a.compression must be"),
]


@pytest.mark.parametrize(("text", "message"), REFUSALS)
def test_every_validation_rule_refuses_with_a_typed_error_naming_the_source(text, message):
    with pytest.raises(RepoPathsInvalid, match=message) as caught:
        parse_repo_paths(text, "SOURCE.toml")
    assert "SOURCE.toml" in str(caught.value)
    assert caught.value.code == "path"


def test_valid_variants_are_accepted_and_typed():
    repo, entries = parse_repo_paths(
        _cfg(
            '[paths.a]\nrepo = "x"\ncompression = "zstd"\n'
            '[paths.b]\nrepo = "{project}/x"\n'
            '[paths.c]\nrepo = "y"\ncow = "z/w"\ncompression = "zstd"\n'
            '[paths.d]\nrepo = "y"\ncow = "z"\ncompression = "none"\n'
        )
    )
    assert repo == "r"
    assert entries == {
        "a": PathEntry("a", "x", None, "zstd"),
        "b": PathEntry("b", "{project}/x", None, None),
        "c": PathEntry("c", "y", "z/w", "zstd"),
        "d": PathEntry("d", "y", "z", "none"),
    }


def test_the_real_repo_config_loads_and_resolves_each_of_its_four_names():
    repo_paths_ = load_repo_paths(REAL_REPO_ROOT)
    assert repo_paths_.repo == "vbpub"
    assert set(repo_paths_.entries) == {"iso", "vm-images", "vm-lab", "downloads"}
    assert repo_paths_.entries["iso"] == PathEntry("iso", "downloads/iso", "downloads/iso", "none")
    assert repo_paths_.entries["vm-images"].compression == "zstd"
    assert repo_paths_.entries["vm-lab"] == PathEntry("vm-lab", "{project}/vm-lab", "{project}/vm-lab", "zstd")
    assert repo_paths_.entries["downloads"] == PathEntry("downloads", "downloads", None, None)
    root = REAL_REPO_ROOT
    expected = {
        "iso": root / "downloads/iso",
        "vm-images": root / "downloads/vm-images",
        "vm-lab": root / "scripts/sample/vm-lab",
        "downloads": root / "downloads",
    }
    for name, path in expected.items():
        got = repo_paths_.resolve(name, project="scripts/sample", environ={}, ops=FakeOps())
        assert got == Resolved(path, "plain", name, "scripts/sample")


# -- loading and root discovery ----------------------------------------------------------------------------------


def test_load_reads_the_file_and_takes_the_repo_name_from_the_config_not_the_directory(tmp_path):
    root = write_repo(tmp_path, name="the-directory")
    loaded = load_repo_paths(root)
    assert loaded.root == root and loaded.repo == "myrepo"
    assert loaded.entries["iso"].cow == "downloads/iso"


def test_a_worktree_resolves_to_the_same_cow_folders_but_keeps_its_own_plain_fallback(tmp_path):
    main = load_repo_paths(write_repo(tmp_path, name="vbpub"))
    worktree = load_repo_paths(write_repo(tmp_path, name="rp-01-worktree"))
    area, environ, ops = btrfs_area(tmp_path)
    plain_main = main.resolve("iso", environ={}, ops=ops)
    plain_worktree = worktree.resolve("iso", environ={}, ops=ops)
    assert plain_main.path == main.root / "downloads/iso"
    assert plain_worktree.path == worktree.root / "downloads/iso"
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    got_main = main.resolve("iso", environ=environ, ops=ops)
    got_worktree = worktree.resolve("iso", environ=environ, ops=ops)
    assert got_main.mode == got_worktree.mode == "cow"
    assert got_main.path == got_worktree.path == area / "myrepo" / "downloads" / "iso"


def test_load_refuses_a_missing_file_and_an_invalid_one_naming_the_file(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(UsageError, match="cannot read") as missing:
        load_repo_paths(empty)
    assert missing.value.code == "usage"
    broken = write_repo(tmp_path, text="schema = 7\n", name="broken")
    with pytest.raises(RepoPathsInvalid, match="schema must be 1") as caught:
        load_repo_paths(broken)
    assert str(broken / "vm-lab.toml") in str(caught.value)


def test_root_discovery_walks_up_from_a_nested_directory_and_from_a_file(tmp_path):
    root = write_repo(tmp_path)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("x", encoding="utf-8")
    assert find_repo_root(nested) == root
    assert find_repo_root(str(nested / "file.txt")) == root
    assert find_repo_root(root) == root


def test_root_discovery_takes_the_nearest_config_and_never_the_current_directory(tmp_path, monkeypatch):
    outer = write_repo(tmp_path, name="outer")
    inner = outer / "inner"
    inner.mkdir()
    (inner / "vm-lab.toml").write_text(REPO_PATHS_TEXT, encoding="utf-8")
    other = write_repo(tmp_path, name="other")
    monkeypatch.chdir(other)
    assert find_repo_root(inner / "deep") == inner
    assert find_repo_root(outer) == outer


def test_root_discovery_without_any_config_is_a_usage_error(tmp_path, monkeypatch):
    # A config name no real directory above the temporary one can hold, so the result never depends on where the
    # temporary directory sits.
    monkeypatch.setattr(repo_paths, "CONFIG_NAME", "vm-lab-never-present.toml")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    with pytest.raises(UsageError, match="no vm-lab-never-present.toml at or above"):
        find_repo_root(nowhere)


# -- resolve -----------------------------------------------------------------------------------------------------


def test_unknown_name_lists_the_defined_ones(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    with pytest.raises(UsageError, match="unknown path name 'nope'; defined: bare, downloads, iso") as caught:
        loaded.resolve("nope", environ={})
    assert caught.value.code == "usage"


def test_per_project_entry_needs_a_project_and_a_plain_one_ignores_it(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    with pytest.raises(UsageError, match="per project"):
        loaded.resolve("vm-lab", environ={})
    with pytest.raises(UsageError, match="per project"):
        loaded.provision("local-lab", environ={})
    got = loaded.resolve("iso", project="scripts/x", environ={}, ops=FakeOps())
    assert got == Resolved(loaded.root / "downloads/iso", "plain", "iso", "scripts/x")
    got = loaded.resolve("local-lab", project="scripts/x", environ={}, ops=FakeOps())
    assert got == Resolved(loaded.root / "scripts/x/local-lab", "plain", "local-lab", "scripts/x")


@pytest.mark.parametrize("project", ["/abs", "../x", "a/../b", "", ".", "a//b", "a/", "./a", 3])
@pytest.mark.parametrize("name", ["vm-lab", "iso"])
def test_a_bad_project_is_refused_even_when_the_entry_does_not_use_it(tmp_path, name, project):
    loaded = load_repo_paths(write_repo(tmp_path))
    with pytest.raises(UsageError, match="project must be a relative directory"):
        loaded.resolve(name, project=project, environ={})


def test_cow_mode_when_every_condition_holds(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    (area / "myrepo" / "p" / "q" / "vm-lab").mkdir(parents=True)
    got = loaded.resolve("iso", environ=environ, ops=ops)
    assert got == Resolved(area / "myrepo" / "downloads" / "iso", "cow", "iso", None)
    got = loaded.resolve("vm-lab", project="p/q", environ=environ, ops=ops)
    assert got == Resolved(area / "myrepo" / "p" / "q" / "vm-lab", "cow", "vm-lab", "p/q")
    assert got.as_dict() == {
        "name": "vm-lab", "project": "p/q", "path": str(area / "myrepo/p/q/vm-lab"), "mode": "cow",
    }


def test_plain_mode_when_there_is_no_cow_area_setting(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    got = loaded.resolve("iso", environ={}, ops=FakeOps(filesystem="btrfs"))
    assert got == Resolved(loaded.root / "downloads/iso", "plain", "iso", None)


def test_plain_mode_when_cowtree_mode_is_off(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    got = loaded.resolve("iso", environ={**environ, "COWTREE_MODE": "off"}, ops=ops)
    assert got.mode == "plain" and got.path == loaded.root / "downloads/iso"


@pytest.mark.parametrize("filesystem", ["ext4", "xfs"])
def test_a_set_root_on_another_filesystem_than_btrfs_is_loud_naming_the_type(tmp_path, filesystem):
    loaded = load_repo_paths(write_repo(tmp_path))
    root = tmp_path / "other-area"
    (root / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    environ = {"MDT_COW_ROOT": str(root)}
    with pytest.raises(RootInvalid, match=f"filesystem '{filesystem}', not btrfs") as caught:
        loaded.resolve("iso", environ=environ, ops=FakeOps(filesystem=filesystem))
    assert caught.value.code == "root-invalid"
    with pytest.raises(RootInvalid, match=f"filesystem '{filesystem}', not btrfs"):
        loaded.provision("iso", environ=environ, ops=FakeOps(filesystem=filesystem), btrfs=FakeBtrfs().tools())


@pytest.mark.parametrize("environ", [{}, {"MDT_COW_ROOT": ""}])
def test_an_unset_or_empty_root_means_plain_for_resolve_and_provision(tmp_path, environ):
    loaded = load_repo_paths(write_repo(tmp_path))
    fake = FakeBtrfs()
    expected = Resolved(loaded.root / "downloads/iso", "plain", "iso", None)
    assert loaded.resolve("iso", environ=environ, ops=FakeOps(filesystem="ext4")) == expected
    assert loaded.provision("iso", environ=environ, ops=FakeOps(filesystem="ext4"), btrfs=fake.tools()) == expected
    assert fake.calls == []


def test_provision_with_the_mode_off_is_plain_even_with_a_set_root(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs()
    got = loaded.provision("iso", environ={**environ, "COWTREE_MODE": "off"}, ops=ops, btrfs=fake.tools())
    assert got.mode == "plain" and fake.calls == [] and not (area / "myrepo").exists()


def test_a_broken_area_is_loud_not_a_silent_fallback(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    environ = {"MDT_COW_ROOT": str(tmp_path / "does-not-exist")}
    with pytest.raises(RootInvalid):
        loaded.resolve("iso", environ=environ, ops=FakeOps(filesystem="btrfs"))
    with pytest.raises(RootInvalid):
        loaded.provision("iso", environ=environ, ops=FakeOps(filesystem="btrfs"))


def test_plain_mode_when_the_cow_directory_is_missing_or_not_a_directory(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    assert loaded.resolve("iso", environ=environ, ops=ops).mode == "plain"
    (area / "myrepo" / "downloads").mkdir(parents=True)
    (area / "myrepo" / "downloads" / "iso").write_text("a file", encoding="utf-8")
    assert loaded.resolve("iso", environ=environ, ops=ops).mode == "plain"


def test_plain_mode_for_an_entry_without_cow_never_consults_the_area(tmp_path, monkeypatch):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    (area / "myrepo" / "downloads").mkdir(parents=True)
    monkeypatch.setattr(repo_paths, "detect", lambda **_kwargs: pytest.fail("detect must not run"))
    got = loaded.resolve("downloads", environ=environ, ops=ops)
    assert got == Resolved(loaded.root / "downloads", "plain", "downloads", None)
    got = loaded.provision("downloads", environ=environ, ops=ops, btrfs=FakeBtrfs().tools())
    assert got.mode == "plain"


def test_resolve_reads_the_process_environment_by_default(tmp_path, monkeypatch):
    loaded = load_repo_paths(write_repo(tmp_path))
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    assert loaded.resolve("iso", ops=FakeOps()).mode == "plain"


class WriteForbiddenOps(FakeOps):
    """Fake ops on which every write-capable operation fails the test."""

    def _forbidden(self, *_args, **_kwargs):
        raise AssertionError("resolve must not write")

    mkdir = subvol_create = snapshot_create = subvol_destroy = setxattr = write = unlink = rmdir = ficlone = _forbidden
    symlink = fchmod = futime = utime = subvol_setflags = _forbidden


class DeniedOps(FakeOps):
    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        raise PermissionError(13, "denied")


def test_resolving_performs_no_write_and_never_calls_detect(tmp_path, monkeypatch):
    root = write_repo(tmp_path)
    loaded = load_repo_paths(root)
    area = tmp_path / "area"
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    environ = {"MDT_COW_ROOT": str(area)}
    ops = WriteForbiddenOps(filesystem="btrfs")
    monkeypatch.setattr(repo_paths, "detect", lambda **_kwargs: pytest.fail("resolve must not call detect"))
    before = (sorted(os.listdir(root)), sorted(os.listdir(area)))
    got = loaded.resolve("iso", environ=environ, ops=ops)
    assert got.mode == "cow" and got.path == area / "myrepo" / "downloads" / "iso"
    assert loaded.resolve("vm-lab", project="p", environ=environ, ops=ops).mode == "plain"
    assert (sorted(os.listdir(root)), sorted(os.listdir(area))) == before
    assert not (area / ".cowtree").exists()


def test_a_permission_error_while_looking_gives_the_plain_path_not_an_error(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area = tmp_path / "area"
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    got = loaded.resolve("iso", environ={"MDT_COW_ROOT": str(area)}, ops=DeniedOps(filesystem="btrfs"))
    assert got == Resolved(loaded.root / "downloads/iso", "plain", "iso", None)


def test_a_set_root_that_is_relative_a_file_or_missing_is_loud_and_a_bad_mode_too(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    ops = FakeOps(filesystem="btrfs")
    a_file = tmp_path / "file"
    a_file.write_text("x", encoding="utf-8")
    for value in ("relative/dir", str(tmp_path / "missing"), str(a_file), str(tmp_path) + "/"):
        with pytest.raises(RootInvalid, match="MDT_COW_ROOT"):
            loaded.resolve("iso", environ={"MDT_COW_ROOT": value}, ops=ops)
    with pytest.raises(RootInvalid, match="COWTREE_MODE"):
        loaded.resolve("iso", environ={"COWTREE_MODE": "bogus"}, ops=ops)


def test_a_symlinked_root_is_refused_by_resolve_exactly_as_detect_refuses_it(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, _environ, ops = btrfs_area(tmp_path)
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    link = tmp_path / "link-to-area"
    link.symlink_to(area)
    environ = {"MDT_COW_ROOT": str(link)}
    with pytest.raises(RootInvalid, match="must not contain symlinks"):
        loaded.resolve("iso", environ=environ, ops=ops)
    with pytest.raises(RootInvalid, match="must not contain symlinks"):
        loaded.provision("iso", environ=environ, ops=ops, btrfs=FakeBtrfs().tools())


def test_an_os_error_on_the_folder_below_the_root_gives_plain_as_another_uid_would_see(tmp_path, monkeypatch):
    # Another uid can stat the area root but not enter it: the stat of <root>/<repo>/<cow> fails with EACCES.
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    target = area / "myrepo" / "downloads" / "iso"
    target.mkdir(parents=True)
    real_stat = os.stat

    def stat_denied_below_the_root(path, *args, **kwargs):
        if str(path) == str(target):
            raise PermissionError(13, "denied")
        return real_stat(path, *args, **kwargs)

    assert loaded.resolve("iso", environ=environ, ops=ops).mode == "cow"
    with monkeypatch.context() as patched:
        patched.setattr(repo_paths.os, "stat", stat_denied_below_the_root)
        got = loaded.resolve("iso", environ=environ, ops=ops)
    assert got == Resolved(loaded.root / "downloads/iso", "plain", "iso", None)


def test_an_invalid_mode_raises_in_resolve_and_provision_alike(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    ops = FakeOps(filesystem="btrfs")
    for environ in ({"COWTREE_MODE": "bogus"}, {"COWTREE_MODE": "bogus", "MDT_COW_ROOT": str(tmp_path)}):
        with pytest.raises(RootInvalid, match="COWTREE_MODE"):
            loaded.resolve("iso", environ=environ, ops=ops)
        with pytest.raises(RootInvalid, match="COWTREE_MODE"):
            loaded.provision("iso", environ=environ, ops=ops, btrfs=FakeBtrfs().tools())
    # an entry without cow never looks at the mode in either verb
    assert loaded.resolve("downloads", environ={"COWTREE_MODE": "bogus"}, ops=ops).mode == "plain"
    assert loaded.provision("downloads", environ={"COWTREE_MODE": "bogus"}, ops=ops).mode == "plain"


def test_resolving_creates_nothing_in_the_repo_or_below_the_cow_mirror(tmp_path):
    root = write_repo(tmp_path)
    loaded = load_repo_paths(root)
    area, environ, ops = btrfs_area(tmp_path)
    before = sorted(os.listdir(root))
    loaded.resolve("iso", environ=environ, ops=ops)
    loaded.resolve("vm-lab", project="p", environ=environ, ops=ops)
    assert sorted(os.listdir(root)) == before
    assert os.listdir(area) == []


# -- provision ---------------------------------------------------------------------------------------------------


def test_provision_creates_parents_then_the_subvolume_then_the_property(tmp_path):
    root = write_repo(tmp_path)
    loaded = load_repo_paths(root)
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs()
    got = loaded.provision("vm-lab", project="p/q", environ=environ, ops=ops, btrfs=fake.tools())
    target = area / "myrepo" / "p" / "q" / "vm-lab"
    assert got == Resolved(target, "cow", "vm-lab", "p/q")
    assert fake.calls == [
        ("btrfs", "subvolume", "create", str(target)),
        ("btrfs", "property", "set", str(target), "compression", "zstd"),
    ]
    assert target.parent.is_dir() and not os.path.islink(target.parent)
    assert not (root / "p").exists()
    assert loaded.resolve("vm-lab", project="p/q", environ=environ, ops=ops).mode == "cow"


def test_provision_is_idempotent_and_skips_a_property_that_already_matches(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs()
    target = area / "myrepo" / "downloads" / "vm-images"
    loaded.provision("vm-images", environ=environ, ops=ops, btrfs=fake.tools())
    fake.calls.clear()
    got = loaded.provision("vm-images", environ=environ, ops=ops, btrfs=fake.tools())
    assert got == Resolved(target, "cow", "vm-images", None)
    assert fake.calls == [("btrfs", "property", "get", str(target), "compression")]


def test_provision_corrects_a_different_property_and_treats_unset_as_none(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    iso = area / "myrepo" / "downloads" / "iso"
    images = area / "myrepo" / "downloads" / "vm-images"
    for target in (iso, images):
        target.mkdir(parents=True)
    fake = FakeBtrfs(props={str(iso): "zstd", str(images): ""})
    fake.subvolumes.update({str(iso), str(images)})
    loaded.provision("iso", environ=environ, ops=ops, btrfs=fake.tools())
    loaded.provision("vm-images", environ=environ, ops=ops, btrfs=fake.tools())
    assert fake.calls == [
        ("btrfs", "property", "get", str(iso), "compression"),
        ("btrfs", "property", "set", str(iso), "compression", "none"),
        ("btrfs", "property", "get", str(images), "compression"),
        ("btrfs", "property", "set", str(images), "compression", "zstd"),
    ]
    fake.calls.clear()
    fake.props[str(iso)] = ""
    loaded.provision("iso", environ=environ, ops=ops, btrfs=fake.tools())
    assert fake.calls == [("btrfs", "property", "get", str(iso), "compression")]


def test_provision_without_a_declared_compression_sets_no_property(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs()
    target = area / "myrepo" / "bare"
    loaded.provision("bare", environ=environ, ops=ops, btrfs=fake.tools())
    loaded.provision("bare", environ=environ, ops=ops, btrfs=fake.tools())
    assert fake.calls == [
        ("btrfs", "subvolume", "create", str(target)),
        ("btrfs", "property", "get", str(target), "compression"),
    ]


def test_provision_refuses_an_existing_plain_directory_and_runs_no_command(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    target = area / "myrepo" / "downloads" / "iso"
    target.mkdir(parents=True)
    fake = FakeBtrfs()
    with pytest.raises(PathRefused, match="iso: .*existing plain directory, not a subvolume") as caught:
        loaded.provision("iso", environ=environ, ops=ops, btrfs=fake.tools())
    assert str(target) in str(caught.value) and fake.calls == []


def test_provision_creates_nothing_without_a_usable_area(tmp_path):
    root = write_repo(tmp_path)
    loaded = load_repo_paths(root)
    fake = FakeBtrfs()
    got = loaded.provision("iso", environ={}, ops=FakeOps(filesystem="btrfs"), btrfs=fake.tools())
    assert got == Resolved(root / "downloads/iso", "plain", "iso", None)
    got = loaded.provision("downloads", environ={}, ops=FakeOps(filesystem="btrfs"), btrfs=fake.tools())
    assert got.mode == "plain"
    assert fake.calls == [] and not (root / "downloads").exists()


@pytest.mark.parametrize("failing", [("subvolume", "create"), ("property", "get"), ("property", "set")])
def test_provision_turns_a_failed_command_into_a_backend_error(tmp_path, failing):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs(fail=failing)
    if failing[0] == "property" and failing[1] == "get":
        target = area / "myrepo" / "downloads" / "vm-images"
        target.mkdir(parents=True)
        fake.subvolumes.add(str(target))
    with pytest.raises(BackendFailed, match="exited 1: boom") as caught:
        loaded.provision("vm-images", environ=environ, ops=ops, btrfs=fake.tools())
    assert caught.value.code == "backend"


def test_provision_uses_the_default_command_boundary_when_none_is_passed(tmp_path, monkeypatch):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    fake = FakeBtrfs()
    monkeypatch.setattr(repo_paths, "Btrfs", fake.tools)
    got = loaded.provision("iso", environ=environ, ops=ops)
    assert got.mode == "cow" and fake.calls[0][:3] == ("btrfs", "subvolume", "create")


# -- the default command boundary --------------------------------------------------------------------------------


def test_default_runner_returns_the_status_and_combined_output():
    assert repo_paths._capture([sys.executable, "-c", "print('hi')"]) == (0, "hi\n")
    status, output = repo_paths._capture(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"]
    )
    assert status == 3 and "out" in output and "err" in output


def test_default_runner_reports_a_missing_program_as_a_backend_error():
    with pytest.raises(BackendFailed, match="cannot run"):
        repo_paths._capture(["/nonexistent/cowtree-test-binary"])


def test_default_subvolume_test_looks_only_at_the_root_inode_number(monkeypatch):
    # No real inode is used: a fresh tmpfs directory could legitimately carry any number.
    assert repo_paths.Btrfs().is_subvolume is repo_paths._is_subvolume
    with monkeypatch.context() as patched:
        patched.setattr(repo_paths.os, "lstat", lambda _path: SimpleNamespace(st_ino=256))
        assert repo_paths._is_subvolume("/any") is True
    with monkeypatch.context() as patched:
        patched.setattr(repo_paths.os, "lstat", lambda _path: SimpleNamespace(st_ino=257))
        assert repo_paths._is_subvolume("/any") is False


def test_provision_wraps_a_failing_makedirs_in_a_typed_backend_error(tmp_path):
    loaded = load_repo_paths(write_repo(tmp_path))
    area, environ, ops = btrfs_area(tmp_path)
    (area / "myrepo").write_text("a file where the repo folder should be", encoding="utf-8")
    fake = FakeBtrfs()
    with pytest.raises(BackendFailed, match="makedirs") as caught:
        loaded.provision("vm-images", environ=environ, ops=ops, btrfs=fake.tools())
    assert caught.value.code == "backend" and caught.value.errno is not None and fake.calls == []
    with pytest.raises(BackendFailed, match="makedirs") as caught:
        loaded.provision("vm-lab", project="a\x00b", environ=environ, ops=ops, btrfs=fake.tools())
    assert caught.value.errno is None and fake.calls == []


def test_property_output_parsing_and_compression_comparison():
    assert repo_paths._property_value("") == ""
    assert repo_paths._property_value("warning: x\ncompression=zstd:3\n") == "zstd:3"
    assert repo_paths._same_compression("", "none") and repo_paths._same_compression("none", "none")
    assert repo_paths._same_compression("zstd", "zstd")
    assert not repo_paths._same_compression("zstd", "none") and not repo_paths._same_compression("", "zstd")


# -- the shell wrapper -------------------------------------------------------------------------------------------


_NO_EXEC_BIT = "the checkout snapshot lost the executable bit of libraries/cowtree/repo-path (committed as git mode 100755)"


def test_wrapper_runs_the_source_next_to_it():
    assert WRAPPER.is_file()
    text = WRAPPER.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert '"$here/src' in text and 'python3 -m cowtree path "$@"' in text
    assert (WRAPPER.parent / "src" / "cowtree" / "repo_paths.py").is_file()


def test_wrapper_resolves_a_path_without_an_installed_package(tmp_path):
    if not os.access(WRAPPER, os.X_OK):
        pytest.skip(_NO_EXEC_BIT)
    root = write_repo(tmp_path)
    environ = {k: v for k, v in os.environ.items() if k not in {"MDT_COW_ROOT", "COWTREE_MODE", "PYTHONPATH"}}
    environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{environ.get('PATH', '')}"
    done = subprocess.run(
        [str(WRAPPER), "vm-lab", "--project", "scripts/x", "--repo-root", str(root)],
        env=environ, capture_output=True, text=True, check=False,
    )
    assert (done.returncode, done.stdout.strip()) == (0, str(root / "scripts/x/vm-lab")), done.stderr
