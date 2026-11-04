"""KI-115: ``cmru handler tarball-publish --variant`` publishes a multi-variant release.

Handler-level tests drive ``cmd_tarball_publish`` with a fake GitHub release client
(a recording stand-in that models releases, assets, tags and ids); keystone-level tests
cover the retained-build guards of ``publish_versioned_variants``; the end-to-end test at
the bottom runs a real ``cmru release`` against a real local bare origin with the real
handler and only the GitHub HTTP layer faked.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import handlers, release

OUTPUT_ID = "20261104T120000Z_" + "a" * 40
SOURCE_COMMIT = "a" * 40
SOURCE_DATE = "2026-11-04T12:00:00+00:00"
LABEL_313 = "CPython 3.13, x86_64 glibc"
LABEL_314 = "CPython 3.14, x86_64 glibc"
SPECS = [f"py313={LABEL_313}", f"py314={LABEL_314}"]


class FakeGH:
    """Models just enough of GitHub Releases: tags, releases, assets, ids."""

    def __init__(self, *, existing=(), tags=None, latest=None, drop=(), mid_upload_failure=None):
        self.releases: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.tag_commits = dict(tags or {})
        self.latest = latest
        self.drop = set(drop)
        self.mid_upload_failure = mid_upload_failure
        for tag in existing:
            self.releases[tag] = {"id": len(self.releases) + 1, "tag_name": tag, "assets": {}}

    # reads ------------------------------------------------------------------------
    def resolve_latest(self, prefix):
        return self.latest

    def get_release_by_tag(self, tag):
        found = self.releases.get(tag)
        return None if found is None else {"id": found["id"], "tag_name": tag}

    def get_tag_commit(self, tag):
        return self.tag_commits.get(tag)

    def asset_download_url(self, tag, name):
        return f"https://example.invalid/{tag}/{name}"

    def list_assets(self, release_id):
        for found in self.releases.values():
            if found["id"] == release_id:
                return [{"name": name} for name in found["assets"]]
        return []

    # write ------------------------------------------------------------------------
    def publish(self, tag, title, notes, assets, *, recreate=False, target_commitish=None,
                require_existing_release=False, expected_release_id=None,
                expected_tag_commit=None):
        self.calls.append({
            "tag": tag, "recreate": recreate, "target_commitish": target_commitish,
            "require_existing_release": require_existing_release,
            "expected_release_id": expected_release_id,
            "expected_tag_commit": expected_tag_commit, "notes": notes,
        })
        if require_existing_release:
            assert tag in self.releases, "existing-only publish created a release"
        if recreate or tag not in self.releases:
            self.releases[tag] = {"id": len(self.releases) + 1, "tag_name": tag, "assets": {}}
        found = self.releases[tag]
        found["body"] = notes
        for index, asset in enumerate(assets):
            if self.mid_upload_failure is not None and index == self.mid_upload_failure:
                raise RuntimeError("simulated upload failure")
            if Path(asset).name in self.drop:
                continue
            found["assets"][Path(asset).name] = Path(asset).read_bytes()
        return {"id": found["id"]}


def _variant_bytes(name: str) -> bytes:
    return f"tarball for {name}".encode()


def _write_dist(dist: Path, version: str = "1.2.3", variants=("py313", "py314"),
                prefix: str = "tls-edge") -> dict[str, Path]:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "VERSION").write_text(version + "\n", encoding="utf-8")
    (dist / "get.py").write_text("# generated installer\n", encoding="utf-8")
    paths = {}
    for name in variants:
        path = dist / f"{prefix}-v{version}-{name}.tar.xz"
        path.write_bytes(_variant_bytes(name))
        paths[name] = path
    return paths


def _args(cwd: Path, **overrides) -> SimpleNamespace:
    values = dict(
        cwd=str(cwd), prefix="tls-edge", glob="tls-edge-v*.tar.xz", notes_env=None,
        version_env=None, version_file="dist/VERSION", variant=list(SPECS),
        asset_suffix=".tar.xz", extra_asset=["dist/get.py"],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def plain(monkeypatch, tmp_path):
    """Non-retained publication: dist/ in the project directory, fake GitHub client."""
    for name in ("CMRU_BUILD_OUTPUT_ROOT", "CMRU_BUILD_OUTPUT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    holder = SimpleNamespace(gh=FakeGH())
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: holder.gh)
    holder.dist = tmp_path / "dist"
    holder.paths = _write_dist(holder.dist)
    holder.cwd = tmp_path
    return holder


# ─── plain (non-retained) publication ────────────────────────────────────────────────
def test_two_variants_publish_under_one_tag_with_sidecars_extras_and_latest_json(plain, capsys):
    handlers.cmd_tarball_publish(_args(plain.cwd))

    versioned = plain.gh.releases["tls-edge-v1.2.3"]["assets"]
    assert sorted(versioned) == sorted([
        "tls-edge-v1.2.3-py313.tar.xz", "tls-edge-v1.2.3-py313.tar.xz.sha256",
        "tls-edge-v1.2.3-py314.tar.xz", "tls-edge-v1.2.3-py314.tar.xz.sha256",
        "get.py",
    ])
    for name in ("py313", "py314"):
        digest = hashlib.sha256(_variant_bytes(name)).hexdigest()
        sidecar = versioned[f"tls-edge-v1.2.3-{name}.tar.xz.sha256"].decode()
        assert sidecar.split()[0] == digest
    latest = json.loads(plain.gh.releases["tls-edge-latest"]["assets"]["latest.json"])
    assert [v["name"] for v in latest["variants"]] == ["py313", "py314"]
    assert [v["label"] for v in latest["variants"]] == [LABEL_313, LABEL_314]
    assert latest["tag"] == "tls-edge-v1.2.3"
    body = plain.gh.releases["tls-edge-v1.2.3"]["body"]
    assert "tls-edge-v1.2.3-py313.tar.xz" in body and "tls-edge-v1.2.3-py314.tar.xz" in body
    out = capsys.readouterr().out
    assert "TLS-EDGE_RELEASE_ASSETS=" in out
    assert "tls-edge-v1.2.3-py313.tar.xz.sha256" in out
    assert "TLS-EDGE_VARIANT_PY313=tls-edge-v1.2.3-py313.tar.xz sha256=" in out


def test_republishing_is_idempotent(plain):
    handlers.cmd_tarball_publish(_args(plain.cwd))
    first = dict(plain.gh.releases["tls-edge-v1.2.3"]["assets"])
    handlers.cmd_tarball_publish(_args(plain.cwd))
    assert plain.gh.releases["tls-edge-v1.2.3"]["assets"] == first
    assert sorted(plain.gh.releases) == ["tls-edge-latest", "tls-edge-v1.2.3"]


def test_variant_without_label_and_notes_env_and_version_env(plain, monkeypatch):
    monkeypatch.setenv("DEMO_NOTES", "Opening line.")
    monkeypatch.setattr(
        handlers, "_require_env", lambda name: "1.2.3" if name == "DEMO_VERSION" else "configured",
    )
    handlers.cmd_tarball_publish(_args(
        plain.cwd, variant=["py313", "py314"], extra_asset=[], notes_env="DEMO_NOTES",
        version_file=None, version_env="DEMO_VERSION",
    ))
    assert plain.gh.releases["tls-edge-v1.2.3"]["body"].startswith("Opening line.")
    latest = json.loads(plain.gh.releases["tls-edge-latest"]["assets"]["latest.json"])
    assert [v["label"] for v in latest["variants"]] == [None, None]
    assert "get.py" not in plain.gh.releases["tls-edge-v1.2.3"]["assets"]


def test_a_missing_variant_is_refused_before_any_upload(plain):
    plain.paths["py314"].unlink()
    with pytest.raises(SystemExit, match=r"(?s)partial multi-variant release.*py314"):
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert plain.gh.calls == [] and plain.gh.releases == {}


def test_a_tarball_that_is_no_declared_variant_is_refused(plain):
    (plain.dist / "tls-edge-v1.2.3-py315.tar.xz").write_bytes(b"extra")
    with pytest.raises(SystemExit, match="py315.*no declared variant"):
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert plain.gh.calls == []


def test_a_stale_tarball_from_another_version_is_refused_not_renamed(plain):
    stale = plain.dist / "tls-edge-v1.2.2-py313.tar.xz"
    plain.paths["py313"].rename(stale)
    with pytest.raises(SystemExit) as refusal:
        handlers.cmd_tarball_publish(_args(plain.cwd))
    message = str(refusal.value)
    assert "variant 'py313': no tls-edge-v1.2.3-py313.tar.xz" in message
    assert "tls-edge-v1.2.2-py313.tar.xz matches" in message
    assert plain.gh.calls == []


def test_every_problem_is_reported_together(plain):
    plain.paths["py313"].unlink()
    (plain.dist / "tls-edge-v1.2.3-pyx.tar.xz").write_bytes(b"x")
    with pytest.raises(SystemExit) as refusal:
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert "py313" in str(refusal.value) and "pyx" in str(refusal.value)


def test_two_files_with_one_variant_asset_name_are_refused(plain, monkeypatch):
    twin = plain.cwd / "twin" / plain.paths["py313"].name
    twin.parent.mkdir()
    twin.write_bytes(b"twin")
    real = [plain.paths["py313"], twin, plain.paths["py314"]]
    monkeypatch.setattr(
        handlers.Path, "glob",
        lambda self, pattern: iter(real) if self.name == "dist" else iter(()),
    )
    with pytest.raises(SystemExit, match="2 files named tls-edge-v1.2.3-py313.tar.xz"):
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert plain.gh.calls == []


@pytest.mark.parametrize(("spec", "message"), [
    ("bad name", "the name must be letters"),
    ("py313=", "an empty label"),
])
def test_malformed_variant_specs_are_refused(plain, spec, message):
    with pytest.raises(SystemExit, match=message):
        handlers.cmd_tarball_publish(_args(plain.cwd, variant=[spec]))


def test_duplicate_variant_declarations_are_refused(plain):
    with pytest.raises(SystemExit, match="declared twice"):
        handlers.cmd_tarball_publish(_args(plain.cwd, variant=["py313", "py313"]))


def test_an_extra_asset_matching_nothing_is_refused_before_upload(plain):
    with pytest.raises(SystemExit, match="--extra-asset 'dist/nope.py' matched no existing file"):
        handlers.cmd_tarball_publish(_args(plain.cwd, extra_asset=["dist/nope.py"]))
    assert plain.gh.calls == []


def test_extra_asset_without_variant_is_refused(plain):
    with pytest.raises(SystemExit, match="only valid with --variant"):
        handlers.cmd_tarball_publish(_args(plain.cwd, variant=[]))
    assert plain.gh.calls == []


def test_an_extra_asset_named_like_a_variant_asset_is_refused(plain, capsys):
    clash = plain.cwd / "clash" / "tls-edge-v1.2.3-py313.tar.xz"
    clash.parent.mkdir()
    clash.write_bytes(b"clash")
    with pytest.raises(SystemExit):
        handlers.cmd_tarball_publish(_args(plain.cwd, extra_asset=["clash/*.tar.xz"]))
    assert "asset name collision" in capsys.readouterr().err
    assert plain.gh.calls == []


def test_an_upload_that_lost_an_asset_never_moves_the_latest_pointer(plain, capsys):
    plain.gh.drop = {"tls-edge-v1.2.3-py314.tar.xz"}
    with pytest.raises(SystemExit):
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert "is missing ['tls-edge-v1.2.3-py314.tar.xz'] after upload" in capsys.readouterr().err
    assert "tls-edge-latest" not in plain.gh.releases
    # The retry replaces same-named assets and completes the release.
    plain.gh.drop = set()
    handlers.cmd_tarball_publish(_args(plain.cwd))
    assert "tls-edge-v1.2.3-py314.tar.xz" in plain.gh.releases["tls-edge-v1.2.3"]["assets"]
    assert "tls-edge-latest" in plain.gh.releases


def test_an_interrupted_upload_leaves_no_latest_pointer(plain):
    plain.gh.mid_upload_failure = 3
    with pytest.raises(RuntimeError, match="simulated upload failure"):
        handlers.cmd_tarball_publish(_args(plain.cwd))
    assert "tls-edge-latest" not in plain.gh.releases


def test_single_asset_tarball_publish_is_unchanged(plain, monkeypatch):
    published = []
    monkeypatch.setattr(
        handlers, "_publish_versioned_artifacts",
        lambda _gh, **kwargs: published.append(kwargs) or {"ok": True},
    )
    single = plain.dist / "solo-v1.2.3.tar.xz"
    single.write_bytes(b"solo")
    handlers.cmd_tarball_publish(SimpleNamespace(
        cwd=str(plain.cwd), prefix="solo", glob="solo-v*.tar.xz", notes_env=None,
        version_env=None, version_file="dist/VERSION",
    ))
    assert published == [{
        "prefix": "solo", "version": "1.2.3", "asset_path": single,
        "notes": None, "extra_assets": None, "build_output": None,
    }]


def test_extra_asset_on_a_single_asset_publish_is_refused(plain):
    with pytest.raises(SystemExit, match="only valid with --variant"):
        handlers.cmd_tarball_publish(SimpleNamespace(
            cwd=str(plain.cwd), prefix="solo", glob="solo-v*.tar.xz", notes_env=None,
            version_env=None, version_file="dist/VERSION", extra_asset=["dist/get.py"],
        ))


def test_the_handler_dry_run_accepts_the_variant_grammar(capsys):
    from cmru import cli

    code = cli.main([
        "handler", "tarball-publish", "--prefix", "p", "--cwd", ".", "--glob", "g",
        "--version-env", "V", "--variant", "a", "--variant", "b=B",
        "--extra-asset", "get.py", "--dry-run",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "'variant': ['a', 'b=B']" in out and "'asset_suffix': '.tar.xz'" in out


# ─── retained build-output publication ───────────────────────────────────────────────
def _retained(monkeypatch, tmp_path, *, variants=("py313", "py314"), omit=(), version="1.2.3"):
    artifact_root = tmp_path / "outputs" / OUTPUT_ID
    dist = artifact_root / "dist"
    paths = _write_dist(dist, version=version, variants=variants)
    inventoried = [p for p in sorted(dist.iterdir()) if p.name not in omit]
    manifest = {
        "schema_version": 1, "kind": "cmru-local-build", "publication": "eligible",
        "project": "tls-edge", "build_id": OUTPUT_ID, "source_commit": SOURCE_COMMIT,
        "source_commit_date": SOURCE_DATE, "source_tree_changes": [], "logs": [],
        "artifacts": [{"directory": "dist", "files": [
            {"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
             "bytes": str(len(p.read_bytes()))} for p in inventoried
        ]}],
    }
    (artifact_root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
    for key, value in {
        "CMRU_BUILD_OUTPUT_ROOT": str(artifact_root), "CMRU_BUILD_OUTPUT_ID": OUTPUT_ID,
        "CMRU_BUILD_OUTPUT_PROJECT": "tls-edge", "CMRU_BUILD_SOURCE_COMMIT": SOURCE_COMMIT,
        "CMRU_BUILD_SOURCE_DATE": SOURCE_DATE,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    if omit:
        # The real validator already refuses a file the inventory does not list; hand the
        # handler the inventory directly to pin ITS selection to inventoried files only.
        record = {"artifact_root": artifact_root, "manifest": manifest}
        monkeypatch.setattr(handlers, "_build_output_record", lambda _args: record)
    snapshot = lambda: {  # noqa: E731
        p.relative_to(artifact_root): p.read_bytes()
        for p in artifact_root.rglob("*") if p.is_file()
    }
    return SimpleNamespace(root=artifact_root, dist=dist, paths=paths, snapshot=snapshot,
                           cwd=tmp_path / "project")


def _targets_gh(**kwargs):
    return FakeGH(
        existing=("tls-edge-v1.2.3", "tls-edge-latest"),
        tags={"tls-edge-v1.2.3": SOURCE_COMMIT, "tls-edge-latest": "c" * 40}, **kwargs,
    )


def test_retained_variants_publish_to_existing_targets_without_touching_the_record(
    monkeypatch, tmp_path,
):
    retained = _retained(monkeypatch, tmp_path)
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    before = retained.snapshot()

    handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=["get.py"]))

    assert retained.snapshot() == before
    assert sorted(gh.releases["tls-edge-v1.2.3"]["assets"]) == sorted([
        "tls-edge-v1.2.3-py313.tar.xz", "tls-edge-v1.2.3-py313.tar.xz.sha256",
        "tls-edge-v1.2.3-py314.tar.xz", "tls-edge-v1.2.3-py314.tar.xz.sha256", "get.py",
    ])
    assert [c["tag"] for c in gh.calls] == ["tls-edge-v1.2.3", "tls-edge-latest"]
    assert all(c["require_existing_release"] and not c["recreate"] for c in gh.calls)
    assert gh.calls[0]["expected_tag_commit"] == SOURCE_COMMIT
    assert gh.calls[1]["expected_tag_commit"] == "c" * 40
    assert "latest.json" in gh.releases["tls-edge-latest"]["assets"]


def test_retained_variant_outside_the_inventory_is_refused_before_any_upload(
    monkeypatch, tmp_path,
):
    retained = _retained(monkeypatch, tmp_path, omit=("tls-edge-v1.2.3-py314.tar.xz",))
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    with pytest.raises(SystemExit, match="variant 'py314'"):
        handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=["get.py"]))
    assert gh.calls == []


def test_the_real_validator_refuses_an_uninventoried_file_before_the_handler_selects(
    monkeypatch, tmp_path,
):
    retained = _retained(monkeypatch, tmp_path)
    (retained.dist / "tls-edge-v1.2.3-py315.tar.xz").write_bytes(b"sneaked in")
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    with pytest.raises(RuntimeError):
        handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=[]))
    assert gh.calls == []


def test_retained_extra_asset_outside_the_inventory_is_refused(monkeypatch, tmp_path):
    retained = _retained(monkeypatch, tmp_path, omit=("get.py",))
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    with pytest.raises(SystemExit, match="matched no existing file"):
        handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=["get.py"]))
    assert gh.calls == []


def test_retained_bytes_changed_after_validation_are_refused_before_upload(
    monkeypatch, tmp_path,
):
    retained = _retained(monkeypatch, tmp_path)
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    original_copy = shutil.copy2

    def tampering_copy(source, target, **kwargs):
        original_copy(source, target, **kwargs)
        if Path(source).name.endswith("py314.tar.xz"):
            Path(target).write_bytes(b"tampered")

    monkeypatch.setattr(handlers.shutil, "copy2", tampering_copy)
    with pytest.raises(RuntimeError, match="differs from build.json"):
        handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=[]))
    assert gh.calls == []


def test_retained_staging_name_collisions_are_refused(monkeypatch, tmp_path):
    retained = _retained(monkeypatch, tmp_path)
    gh = _targets_gh()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    with pytest.raises(SystemExit, match="asset name collision"):
        handlers.cmd_tarball_publish(_args(
            retained.cwd, extra_asset=["tls-edge-v1.2.3-py313.tar.xz"],
        ))
    assert gh.calls == []


@pytest.mark.parametrize(("gh_kwargs", "message"), [
    ({"tags": {"tls-edge-v1.2.3": "d" * 40, "tls-edge-latest": "c" * 40}}, "does not match existing"),
])
def test_retained_variants_refuse_a_tag_at_another_commit(
    monkeypatch, tmp_path, gh_kwargs, message,
):
    retained = _retained(monkeypatch, tmp_path)
    gh = FakeGH(existing=("tls-edge-v1.2.3", "tls-edge-latest"), **gh_kwargs)
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: gh)
    with pytest.raises(SystemExit):
        handlers.cmd_tarball_publish(_args(retained.cwd, extra_asset=[]))
    assert gh.calls == []


# ─── keystone guards (publish_versioned_variants) ────────────────────────────────────
def _artifacts(tmp_path, names=("py313", "py314"), version="2.0.0"):
    dist = tmp_path / "keystone"
    dist.mkdir(exist_ok=True)
    out = []
    for name in names:
        path = dist / f"demo-v{version}-{name}.tar.xz"
        path.write_bytes(name.encode())
        out.append(release.VariantArtifact(name=name, asset_path=path))
    return out


def _keystone(gh, variants, **kwargs):
    kwargs.setdefault("version", "2.0.0")
    return release.publish_versioned_variants(
        gh, prefix="demo", variants=variants, asset_suffix=".tar.xz", **kwargs,
    )


def _existing(**kwargs):
    return FakeGH(
        existing=("demo-v2.0.0", "demo-latest"),
        tags={"demo-v2.0.0": SOURCE_COMMIT, "demo-latest": "c" * 40}, **kwargs,
    )


def test_keystone_refuses_duplicate_variants_and_missing_files_before_upload(tmp_path):
    gh = FakeGH()
    variants = _artifacts(tmp_path)
    with pytest.raises(SystemExit):
        _keystone(gh, [variants[0], variants[0]])
    ghost = release.VariantArtifact(name="ghost", asset_path=tmp_path / "ghost.tar.xz")
    with pytest.raises(SystemExit):
        _keystone(gh, [variants[0], ghost])
    with_bad_extra = release.VariantArtifact(
        name="x", asset_path=variants[0].asset_path, extra_assets=[tmp_path / "nope"],
    )
    with pytest.raises(SystemExit):
        _keystone(gh, [with_bad_extra])
    with pytest.raises(SystemExit):
        _keystone(gh, variants, extra_assets=[tmp_path / "missing.py"])
    assert gh.calls == []


def test_keystone_refuses_colliding_and_reserved_upload_names(tmp_path, capsys):
    gh = FakeGH()
    variants = _artifacts(tmp_path)
    clash = tmp_path / "clash" / "demo-v2.0.0-py313.tar.xz"
    clash.parent.mkdir()
    clash.write_bytes(b"clash")
    with pytest.raises(SystemExit):
        _keystone(gh, variants, extra_assets=[clash])
    assert "asset name collision" in capsys.readouterr().err
    reserved = tmp_path / "reserved" / "latest.json"
    reserved.parent.mkdir()
    reserved.write_text("{}")
    with pytest.raises(SystemExit):
        _keystone(gh, variants, extra_assets=[reserved])
    assert "reserved" in capsys.readouterr().err
    assert gh.calls == []


def test_keystone_existing_only_refuses_ref_creation_and_dev_versions(tmp_path, capsys):
    variants = _artifacts(tmp_path)
    with pytest.raises(SystemExit):
        _keystone(_existing(), variants, require_existing_targets=True,
                  target_commitish="main", latest_pointer_recreate=False)
    with pytest.raises(SystemExit):
        _keystone(_existing(), variants, require_existing_targets=True)
    assert "cannot create or recreate Git refs" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _keystone(_existing(), _artifacts(tmp_path, version="2.0.0.dev1"),
                  version="2.0.0.dev1", require_existing_targets=True,
                  latest_pointer_recreate=False)
    assert "needs a release version" in capsys.readouterr().err


class _Odd(FakeGH):
    """A client whose answers for one tag are malformed."""

    def __init__(self, tag, record=None, commit="keep", **kwargs):
        super().__init__(**kwargs)
        self.odd_tag, self.odd_record, self.odd_commit = tag, record, commit

    def get_release_by_tag(self, tag):
        if tag == self.odd_tag:
            return self.odd_record
        return super().get_release_by_tag(tag)

    def get_tag_commit(self, tag):
        if tag == self.odd_tag and self.odd_commit != "keep":
            return self.odd_commit
        return super().get_tag_commit(tag)


@pytest.mark.parametrize(("record", "commit", "message"), [
    (None, "keep", "cannot verify the existing GitHub Release demo-latest"),
    ([], "keep", "malformed GitHub Release record"),
    ({"id": 0, "tag_name": "demo-latest"}, "keep", "cannot verify the existing GitHub Release ID"),
    ({"id": 9, "tag_name": "other"}, "keep", "malformed GitHub Release identity"),
    ({"id": 9, "tag_name": "demo-latest"}, None, "cannot verify the existing Git tag demo-latest"),
])
def test_keystone_existing_only_refuses_unverifiable_targets(
    tmp_path, capsys, record, commit, message,
):
    gh = _Odd("demo-latest", record, commit, existing=("demo-v2.0.0",),
              tags={"demo-v2.0.0": SOURCE_COMMIT, "demo-latest": "c" * 40})
    with pytest.raises(SystemExit):
        _keystone(gh, _artifacts(tmp_path), require_existing_targets=True,
                  latest_pointer_recreate=False, expected_tag_commit=SOURCE_COMMIT)
    assert message in capsys.readouterr().err
    assert gh.calls == []


def test_keystone_existing_only_refuses_a_tag_at_another_commit(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _keystone(_existing(), _artifacts(tmp_path), require_existing_targets=True,
                  latest_pointer_recreate=False, expected_tag_commit="e" * 40)
    assert "does not match existing release tag" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _keystone(_existing(), _artifacts(tmp_path), require_existing_targets=True,
                  latest_pointer_recreate=False, expected_tag_commit=None)


def test_keystone_existing_only_publishes_both_targets_by_id(tmp_path):
    gh = _existing()
    _keystone(gh, _artifacts(tmp_path), require_existing_targets=True,
              latest_pointer_recreate=False, expected_tag_commit=SOURCE_COMMIT,
              verify_assets=True)
    assert [(c["tag"], c["expected_release_id"]) for c in gh.calls] == [
        ("demo-v2.0.0", 1), ("demo-latest", 2),
    ]


def test_keystone_keeps_the_pointer_on_a_newer_release(tmp_path, capsys):
    gh = _existing(latest={"version": "3.0.0"})
    _keystone(gh, _artifacts(tmp_path), require_existing_targets=True,
              latest_pointer_recreate=False, expected_tag_commit=SOURCE_COMMIT)
    assert [c["tag"] for c in gh.calls] == ["demo-v2.0.0"]
    assert "Keeping demo-latest on newer demo-v3.0.0" in capsys.readouterr().out


def test_keystone_rechecks_for_a_newer_release_right_before_the_pointer_moves(
    tmp_path, capsys,
):
    gh = _existing()
    answers = iter([None, {"version": "3.0.0"}])
    gh.resolve_latest = lambda _prefix: next(answers)
    _keystone(gh, _artifacts(tmp_path), require_existing_targets=True,
              latest_pointer_recreate=False, expected_tag_commit=SOURCE_COMMIT)
    assert [c["tag"] for c in gh.calls] == ["demo-v2.0.0"]
    assert "Keeping demo-latest on newer" in capsys.readouterr().out


def test_keystone_existing_only_without_a_latest_pointer_touches_only_the_release(tmp_path):
    gh = FakeGH(existing=("demo-v2.0.0",), tags={"demo-v2.0.0": SOURCE_COMMIT})
    _keystone(gh, _artifacts(tmp_path), latest_pointer=False, require_existing_targets=True,
              expected_tag_commit=SOURCE_COMMIT)
    assert [c["tag"] for c in gh.calls] == ["demo-v2.0.0"]


def test_keystone_without_a_latest_pointer_publishes_only_the_release(tmp_path):
    gh = FakeGH()
    _keystone(gh, _artifacts(tmp_path), latest_pointer=False)
    assert [c["tag"] for c in gh.calls] == ["demo-v2.0.0"]


def test_keystone_dev_build_moves_only_the_latest_target(tmp_path):
    gh = FakeGH(existing=("demo-latest",), tags={"demo-latest": "c" * 40})
    result = _keystone(gh, _artifacts(tmp_path, version="2.0.0.dev1"),
                       version="2.0.0.dev1", latest_pointer_recreate=False)
    assert result["release_tag"] is None
    assert [c["tag"] for c in gh.calls] == ["demo-latest"]
    assert gh.calls[0]["recreate"] is False


def test_keystone_verification_needs_a_release_id(tmp_path, capsys):
    gh = FakeGH()
    gh.publish = lambda *_a, **_k: None
    with pytest.raises(SystemExit):
        _keystone(gh, _artifacts(tmp_path), verify_assets=True)
    assert "no release ID was returned" in capsys.readouterr().err


def test_keystone_default_call_shape_is_unchanged(tmp_path):
    gh = FakeGH()
    result = _keystone(gh, _artifacts(tmp_path))
    assert result["release_tag"] == "demo-v2.0.0"
    assert result["assets"] == [
        "demo-v2.0.0-py313.tar.xz", "demo-v2.0.0-py313.tar.xz.sha256",
        "demo-v2.0.0-py314.tar.xz", "demo-v2.0.0-py314.tar.xz.sha256",
    ]
    assert [c["recreate"] for c in gh.calls] == [False, True]
    assert not any(c["require_existing_release"] for c in gh.calls)


# ─── end to end: real git, real `cmru release`, fake GitHub HTTP only ─────────────────
_FAKE_GITHUB = '''\
"""Stands in for the GitHub HTTP layer inside every `cmru` process of the e2e test."""
import hashlib
import json
import os
from pathlib import Path


def _load():
    path = Path(os.environ["FAKE_GH_STATE"])
    return json.loads(path.read_text()) if path.exists() else {}


def _save(state):
    Path(os.environ["FAKE_GH_STATE"]).write_text(json.dumps(state))


class FakeGitHub:
    def __init__(self, owner, repo, token):
        self.owner, self.repo = owner, repo

    def asset_download_url(self, tag, name):
        return f"https://example.invalid/{tag}/{name}"

    def get_release_by_tag(self, tag):
        found = _load().get(tag)
        return None if found is None else {"id": found["id"], "tag_name": tag, "body": found["body"]}

    def list_assets(self, release_id):
        return [{"name": n} for f in _load().values() if f["id"] == release_id for n in f["assets"]]

    def publish(self, tag, title, notes, assets, *, recreate=False, target_commitish=None, **_):
        state = _load()
        if recreate or tag not in state:
            state[tag] = {"id": len(state) + 1, "body": notes, "assets": {}}
        state[tag]["body"] = notes
        for asset in assets:
            data = Path(asset).read_bytes()
            state[tag]["assets"][Path(asset).name] = {
                "sha256": hashlib.sha256(data).hexdigest(), "text": data.decode("utf-8", "replace"),
            }
        _save(state)
        return {"id": state[tag]["id"]}


def install():
    import cmru.handlers
    cmru.handlers.GitHubReleases = FakeGitHub
'''

_TARBALL_TOML = """\
schema_version = 1

[runtime]
kind = "none"

[github]
owner = "o"
repo = "r"
owner_type = "user"
token_source = "cmru"

[targets]
host = "github"
registry = ["ghcr.io"]

[project]
id = "demo"
description = "demo"
template_revision = 4
prefix = "demo-v"
artifacts = ["tarball"]
scm_dist = "demo"

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
changelog = "CHANGES.md"
commit_generated = ["CHANGES.md"]
artifact_dirs = ["dist"]

[[project.variants]]
name = "py313"
label = "CPython 3.13"

[[project.variants]]
name = "py314"
label = "CPython 3.14"

[steps.run-tests]
quiet = true
commands = [ { label = "gate", argv = ["sh", "-c", "true"], cwd = "." } ]

[steps.build]
quiet = true
commands = [ { label = "build", argv = ["sh", "-c", 'mkdir -p dist; for v in $BUILD_VARIANTS; do echo "bundle $v" > "dist/demo-v0.1.0-$v.tar.xz"; done; echo "# get" > dist/get.py'], cwd = "." } ]

[steps.push]
quiet = true
commands = [ { label = "push", argv = @PUSH_ARGV@, cwd = "." } ]
"""

# The real `cmru.handlers.main` (the documented `python -m cmru.handlers` adapter) with only
# the GitHub client replaced; the step runner binds its own `cmru` launcher, so the fake is
# installed by a tiny `python -c` instead of the PATH wrapper.
_HANDLER_ARGS = [
    "tarball-publish", "--prefix", "demo", "--cwd", ".", "--glob", "demo-v*.tar.xz",
    "--version-env", "EXPECTED_VERSION", "--variant", "py313=CPython 3.13",
    "--variant", "py314=CPython 3.14", "--extra-asset", "dist/get.py",
]


def _e2e_env(tmp_path, monkeypatch, variants):
    from tests import test_release_end_to_end_real_git as real

    import cli_extended
    import cmru
    import worktree

    fake_dir = tmp_path / "fakegh"
    fake_dir.mkdir()
    (fake_dir / "fakegh.py").write_text(_FAKE_GITHUB, encoding="utf-8")
    sources = [
        str(Path(module.__file__).resolve().parents[1])
        for module in (cmru, cli_extended, worktree)
    ]
    code = (
        "import sys, fakegh, cmru.handlers as h; fakegh.install(); "
        f"sys.exit(h.main({_HANDLER_ARGS!r}))"
    )
    argv = ["env", f"PYTHONPATH={os.pathsep.join([str(fake_dir), *sources])}",
            sys.executable, "-c", code]
    monkeypatch.setattr(real, "_CMRU_TOML", _TARBALL_TOML.replace("@PUSH_ARGV@", json.dumps(argv)))
    env = real._Env(tmp_path)
    state = tmp_path / "fake-gh.json"
    env.env.update({
        "FAKE_GH_STATE": str(state), "EXPECTED_VERSION": "0.1.0",
        "BUILD_VARIANTS": " ".join(variants), "GITHUB_USERNAME": "o", "GITHUB_REPO": "r",
    })
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_CONFIG_GLOBAL"):
        monkeypatch.setenv(key, env.env[key])
    return env, state


def test_e2e_real_git_release_publishes_every_variant_under_one_tag(tmp_path, monkeypatch):
    env, state = _e2e_env(tmp_path, monkeypatch, ("py313", "py314"))

    result = env.release()

    assert result.returncode == 0, result.log
    assert "refs/tags/demo-v0.1.0" in env.origin_tags()
    published = json.loads(state.read_text())
    assert sorted(published["demo-v0.1.0"]["assets"]) == sorted([
        "demo-v0.1.0-py313.tar.xz", "demo-v0.1.0-py313.tar.xz.sha256",
        "demo-v0.1.0-py314.tar.xz", "demo-v0.1.0-py314.tar.xz.sha256", "get.py",
    ])
    sidecar = published["demo-v0.1.0"]["assets"]["demo-v0.1.0-py313.tar.xz.sha256"]["text"]
    assert sidecar.split()[0] == published["demo-v0.1.0"]["assets"][
        "demo-v0.1.0-py313.tar.xz"]["sha256"]
    latest = json.loads(published["demo-latest"]["assets"]["latest.json"]["text"])
    assert [v["name"] for v in latest["variants"]] == ["py313", "py314"]
    assert [v["label"] for v in latest["variants"]] == ["CPython 3.13", "CPython 3.14"]
    assert "demo-v0.1.0-py314.tar.xz" in published["demo-v0.1.0"]["body"]


def test_e2e_real_git_release_refuses_a_partial_variant_set_and_publishes_nothing(
    tmp_path, monkeypatch,
):
    env, state = _e2e_env(tmp_path, monkeypatch, ("py313",))

    result = env.release()

    assert result.returncode != 0
    assert "refusing a partial multi-variant release" in result.log
    assert "variant 'py314'" in result.log
    assert not state.exists()
