"""KI-175: ``dist_dir`` must be a strict descendant of ``project_root``.

The legacy no-variant build removes the whole ``dist_dir`` tree, so a value that resolves to the
project, its parent or any outside directory would delete data the build never owned. Every case
here uses a scratch tree with sentinels in the project, its parent and an unrelated sibling and
asserts that nothing is touched.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cmru import bundle
from cmru.config import VariantConfig

PY311 = VariantConfig(name="py311", build_arg="3.11", label=None)

BASE = (
    'project_root = "."\n'
    'dist_dir = "{dist_dir}"\n'
    '[wheel]\nenabled = false\n'
    '[archive]\nname_template = "{template}"\nversion_env = "VERSION"\nformat = "xztar"\n'
    '[copy]\nfiles = ["app.py"]\ndirs = []\n'
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")


class Scratch:
    """A project one level below a parent directory, plus an unrelated sibling directory."""

    def __init__(self, tmp_path: Path, dist_dir: str, template: str = "demo-v{version}.tar.xz"):
        self.parent = tmp_path / "parent"
        self.project = self.parent / "proj"
        self.sibling = tmp_path / "sibling"
        for directory in (self.project, self.sibling):
            directory.mkdir(parents=True)
        (self.project / "app.py").write_text("print('x')\n", encoding="utf-8")
        self.sentinels = {
            "project": self.project / "sentinel.txt",
            "parent": self.parent / "sentinel.txt",
            "sibling": self.sibling / "sentinel.txt",
        }
        for name, path in self.sentinels.items():
            path.write_text(f"{name}\n", encoding="utf-8")
        self.config = self.project / "bundle.toml"
        self.write(dist_dir, template)

    def write(self, dist_dir: str, template: str = "demo-v{version}.tar.xz") -> None:
        self.config.write_text(
            BASE.replace("{dist_dir}", dist_dir).replace("{template}", template), encoding="utf-8",
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.parent.parent)): path.read_bytes()
            for path in sorted(self.parent.parent.rglob("*")) if path.is_file()
        }

    def assert_untouched(self, before: dict[str, bytes]) -> None:
        assert self.snapshot() == before
        for path in self.sentinels.values():
            assert path.is_file()


def _escaping(scratch: Scratch) -> list[str]:
    return [".", "..", "../..", "../sibling-not-here", str(scratch.sibling), str(scratch.project),
            str(scratch.parent)]


@pytest.mark.parametrize("which", range(7))
def test_parse_config_refuses_dist_dir_outside_project(tmp_path, which):
    scratch = Scratch(tmp_path, "dist")
    value = _escaping(scratch)[which]
    scratch.write(value)
    before = scratch.snapshot()

    with pytest.raises(ValueError) as caught:
        bundle.parse_config(scratch.config)

    message = str(caught.value)
    assert "dist_dir" in message and "project_root" in message
    assert str(Path(value if Path(value).is_absolute() else scratch.project / value).resolve()) in message
    scratch.assert_untouched(before)


@pytest.mark.parametrize("which", range(7))
def test_legacy_build_refuses_escaping_dist_dir_before_touching_anything(tmp_path, which):
    scratch = Scratch(tmp_path, "dist")
    scratch.write(_escaping(scratch)[which])
    before = scratch.snapshot()

    with pytest.raises(ValueError, match="dist_dir.*project_root"):
        bundle.run_bundle(scratch.config)

    scratch.assert_untouched(before)


def test_legacy_build_refuses_dist_symlink_to_outside_directory(tmp_path):
    scratch = Scratch(tmp_path, "dist")
    (scratch.project / "dist").symlink_to(scratch.sibling, target_is_directory=True)
    before = scratch.snapshot()

    with pytest.raises(ValueError, match="dist_dir.*project_root"):
        bundle.run_bundle(scratch.config)

    scratch.assert_untouched(before)
    assert (scratch.project / "dist").is_symlink()


def test_variant_build_refuses_escaping_dist_dir(tmp_path):
    scratch = Scratch(tmp_path, str(tmp_path / "sibling"), template="demo-v{version}-{variant}.tar.xz")
    before = scratch.snapshot()

    with pytest.raises(ValueError, match="dist_dir.*project_root"):
        bundle.run_bundle(scratch.config, variant=PY311)

    scratch.assert_untouched(before)


def test_ordinary_dist_dir_still_builds_and_cleans_only_its_own_output(tmp_path):
    scratch = Scratch(tmp_path, "dist")
    stale = scratch.project / "dist" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("stale\n", encoding="utf-8")
    before = scratch.snapshot()

    archive = bundle.run_bundle(scratch.config)

    assert archive.is_file() and archive.parent == (scratch.project / "dist").resolve()
    assert not stale.exists()
    after = scratch.snapshot()
    assert {key: value for key, value in after.items() if "/dist/" not in key} == {
        key: value for key, value in before.items() if "/dist/" not in key
    }


def test_nested_dist_dir_inside_project_is_accepted(tmp_path):
    scratch = Scratch(tmp_path, "out/dist")
    assert bundle.parse_config(scratch.config).dist_dir == (scratch.project / "out" / "dist").resolve()


def test_cleanup_recheck_refuses_dist_dir_that_escapes_after_parse(tmp_path, monkeypatch):
    """A config object that already holds an outside ``dist_dir`` never reaches ``rmtree``."""
    scratch = Scratch(tmp_path, "dist")
    parsed = bundle.parse_config(scratch.config)
    outside = scratch.sibling.resolve()
    unsafe = replace(
        parsed, dist_dir=outside, bundle_dir=outside / "bundle", client_dir=outside / "client",
    )
    monkeypatch.setattr(bundle, "parse_config", lambda *_args: unsafe)
    before = scratch.snapshot()

    with pytest.raises(ValueError, match="dist_dir.*project_root"):
        bundle.run_bundle(scratch.config)

    scratch.assert_untouched(before)


def test_cleanup_recheck_refuses_dist_parent_swapped_to_outside_symlink(tmp_path, monkeypatch):
    """A path component redirected between parse and removal must not be followed into deletion."""
    scratch = Scratch(tmp_path, "out/dist")
    victim = scratch.sibling / "dist"
    victim.mkdir()
    victim_file = victim / "precious.txt"
    victim_file.write_text("precious\n", encoding="utf-8")
    real_dist = scratch.project / "out" / "dist"
    real_dist.mkdir(parents=True)
    (real_dist / "old.txt").write_text("old\n", encoding="utf-8")
    real_log = bundle.log_info

    def swap_after_first_validation(message: str) -> None:
        real_log(message)
        if message == "Preparing dist directories":
            (scratch.project / "out").rename(scratch.project / "out-saved")
            (scratch.project / "out").symlink_to(scratch.sibling, target_is_directory=True)

    monkeypatch.setattr(bundle, "log_info", swap_after_first_validation)

    with pytest.raises(ValueError, match="before cleanup|project_root"):
        bundle.run_bundle(scratch.config)

    assert victim_file.read_text(encoding="utf-8") == "precious\n"
    assert scratch.sentinels["sibling"].is_file()


def test_validate_staging_paths_requires_strict_descendant_of_project_root(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    for dist_dir in (project, tmp_path, tmp_path / "other"):
        with pytest.raises(ValueError, match="dist_dir.*project_root"):
            bundle._validate_staging_paths(
                dist_dir, dist_dir / "bundle", dist_dir / "client", project_root=project,
            )
    bundle._validate_staging_paths(
        project / "dist", project / "dist" / "bundle", project / "dist" / "client",
        project_root=project,
    )


@pytest.mark.parametrize("target", ["dist_dir", "bundle_dir", "client_dir"])
def test_removal_helper_itself_refuses_a_config_whose_dist_dir_is_outside_project(tmp_path, target):
    """The removal helper rechecks on its own, whatever the caller validated earlier."""
    scratch = Scratch(tmp_path, "dist")
    parsed = bundle.parse_config(scratch.config)
    outside = scratch.sibling.resolve()
    victim = outside / "bundle"
    victim.mkdir()
    (victim / "precious.txt").write_text("precious\n", encoding="utf-8")
    unsafe = replace(parsed, dist_dir=outside, bundle_dir=victim, client_dir=outside / "client")
    before = scratch.snapshot()

    with pytest.raises(ValueError, match="dist_dir.*project_root"):
        bundle._remove_staging_tree(unsafe, getattr(unsafe, target))

    scratch.assert_untouched(before)
    assert (victim / "precious.txt").is_file()


def test_unresolvable_project_root_is_refused_before_anything_is_removed(tmp_path, monkeypatch):
    scratch = Scratch(tmp_path, "dist")
    stale = scratch.project / "dist" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("stale\n", encoding="utf-8")
    real_resolve = Path.resolve
    root = scratch.project

    def fail_for_project_root(path, *args, **kwargs):
        if path == root:
            raise OSError("injected project_root resolution failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_for_project_root)

    with pytest.raises(ValueError, match="project_root cannot be resolved safely"):
        bundle._validate_staging_paths(
            root / "dist", root / "dist" / "bundle", root / "dist" / "client", project_root=root,
        )

    assert stale.is_file()
