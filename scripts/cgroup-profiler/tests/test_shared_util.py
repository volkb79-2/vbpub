from __future__ import annotations

import os

from lib import util


def test_is_container_id_accepts_only_canonical_full_ids():
    assert util.is_container_id("a" * 64)
    assert not util.is_container_id("A" * 64)
    assert not util.is_container_id("a" * 63)
    assert not util.is_container_id("a" * 64 + "\n")
    assert not util.is_container_id(None)


def test_realpath_is_within_distinguishes_root_descendant_and_prefix_sibling(tmp_path):
    root = tmp_path / "allowed"
    child = root / "child" / "file"
    sibling = tmp_path / "allowed-sibling" / "file"

    assert util.realpath_is_within(str(root), str(root), allow_root=True)
    assert not util.realpath_is_within(str(root), str(root), allow_root=False)
    assert util.realpath_is_within(str(child), str(root), allow_root=False)
    assert not util.realpath_is_within(str(sibling), str(root), allow_root=True)


def test_realpath_is_within_refuses_symlink_escape(tmp_path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    escape = root / "escape"
    escape.symlink_to(outside)

    assert not util.realpath_is_within(
        str(escape / "file"), str(root), allow_root=False
    )


def test_realpath_is_within_refuses_incomparable_paths(tmp_path, monkeypatch):
    def refuse_commonpath(_paths):
        raise ValueError("different roots")

    monkeypatch.setattr(os.path, "commonpath", refuse_commonpath)

    assert not util.realpath_is_within(
        str(tmp_path / "path"), str(tmp_path / "root"), allow_root=False
    )
