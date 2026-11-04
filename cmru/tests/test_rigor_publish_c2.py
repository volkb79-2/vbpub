"""Rigor C-2: the release-body handlers publish the record step's outputs.

``wheel-publish``, ``tarball-publish`` (single and ``--variant``) and
``oci-image-release`` append ``release-body.md`` under ``## Rigor at publication``
and upload ``record.json`` as ``<prefix>-v<V>-rigor.json`` plus ``.sha256``. All of
it is inert when ``CMRU_RECORD_JSON`` / ``CMRU_RECORD_BODY`` are absent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, exit_codes, handlers, release, rigor_publish
from tests.test_oci_image_release import (
    EXPECTED_BODY, TAG, FakeGitHub, _publish,
)
from tests.test_oci_image_release import image_release  # noqa: F401  (fixture)
from tests.test_tarball_variants_publish import FakeGH, _args, _write_dist
from tests.test_wheel_publish_extra_assets import _wheel

RECORD = b'{"schema": 1, "project": "demo"}\n'
BODY = "| Gate | Result |\n|---|---|\n| R0 | pass |\n"
APPENDIX = f"## Rigor at publication\n\n{BODY.strip()}\n"


@pytest.fixture
def record_env(monkeypatch, tmp_path):
    out = tmp_path / "record-out"
    out.mkdir()
    (out / "record.json").write_bytes(RECORD)
    (out / "release-body.md").write_text(BODY, encoding="utf-8")
    monkeypatch.setenv("CMRU_RECORD_JSON", str(out / "record.json"))
    monkeypatch.setenv("CMRU_RECORD_BODY", str(out / "release-body.md"))
    return out


class SizedGH(FakeGH):
    """The variants fake, with asset sizes (the read-back compares them)."""

    def list_assets(self, release_id):
        for found in self.releases.values():
            if found["id"] == release_id:
                return [{"name": n, "size": len(b)} for n, b in found["assets"].items()]
        return []


def _assert_rigor_assets(assets: dict, name: str) -> None:
    assert assets[name] == RECORD
    assert assets[name + ".sha256"].decode().split()[0] == hashlib.sha256(RECORD).hexdigest()


# --- oci-image-release ------------------------------------------------------------


class AssetGitHub(FakeGitHub):
    """The OCI fake plus the asset list / delete / upload routes."""

    def __init__(self) -> None:
        super().__init__()
        self.blobs: dict[str, bytes] = {}
        self.lose_uploads = False

    def handle(self, method, url, data):
        if url.startswith("https://uploads.example/"):
            release_id = int(url.split("/")[3])
            name = url.split("?name=")[1]
            self.calls.append((method, f"upload:{name}"))
            if not self.lose_uploads:
                self.blobs[name] = data
                self.assets[release_id].append(
                    {"id": 900 + len(self.blobs), "name": name, "size": len(data)}
                )
            return 201, "{}"
        return super().handle(method, url, data)

    def _serve(self, method, path, data):
        if method == "GET" and path == "/immutable-releases":
            return 404, "{}"
        if method == "GET" and path.endswith("/assets"):
            return 200, json.dumps(self.assets[int(path.split("/")[2])])
        if method == "DELETE" and path.startswith("/releases/assets/"):
            wanted = int(path.rsplit("/", 1)[1])
            for items in self.assets.values():
                items[:] = [a for a in items if a["id"] != wanted]
            return 204, ""
        return super()._serve(method, path, data)


@pytest.fixture
def oci(image_release, monkeypatch):  # noqa: F811
    api = AssetGitHub()
    api.tags.update(image_release.api.tags)
    monkeypatch.setattr(
        release.GitHubReleases, "_request",
        lambda _gh, method, url, data=None, content_type=None: api.handle(method, url, data),
    )
    image_release.api = api
    return image_release


def test_oci_appends_the_appendix_and_uploads_exactly_the_record_and_sidecar(oci, record_env):
    assert _publish(oci.project) == 0
    api = oci.api
    assert api.releases[TAG]["body"] == EXPECTED_BODY + "\n\n" + APPENDIX
    assert sorted(api.blobs) == ["demo-v2.0.0-rigor.json", "demo-v2.0.0-rigor.json.sha256"]
    _assert_rigor_assets(api.blobs, "demo-v2.0.0-rigor.json")
    # The body write is verified BEFORE any asset call; assets are read back after.
    methods = [call[0] for call in api.calls]
    first_upload = next(i for i, c in enumerate(api.calls) if c[1].startswith("upload:"))
    assert ("POST", "/releases") in api.calls[:first_upload]
    assert api.calls[-1] == ("GET", "/releases/100/assets")
    assert "DELETE" not in methods


def test_oci_without_a_record_stays_assetless(oci):
    assert _publish(oci.project) == 0
    assert oci.api.blobs == {}
    assert not any("assets" in c[1] or c[1].startswith("upload") for c in oci.api.calls)
    assert oci.api.releases[TAG]["body"] == EXPECTED_BODY


def test_oci_replaces_only_the_same_named_assets_on_an_existing_release(oci, record_env):
    api = oci.api
    api.seed_release(TAG, assets=(
        {"id": 1, "name": "demo-v2.0.0-rigor.json", "size": 3},
        {"id": 2, "name": "unrelated.bin", "size": 9},
    ))
    api.tags[TAG] = oci.commit
    assert _publish(oci.project) == 0
    names = sorted(a["name"] for a in api.assets[100])
    assert names == ["demo-v2.0.0-rigor.json", "demo-v2.0.0-rigor.json.sha256", "unrelated.bin"]
    assert ("DELETE", "/releases/assets/1") in api.calls
    assert ("DELETE", "/releases/assets/2") not in api.calls
    assert api.blobs["demo-v2.0.0-rigor.json"] == RECORD


def test_oci_refuses_when_the_uploaded_asset_is_not_read_back(oci, record_env, capsys):
    oci.api.lose_uploads = True
    assert _publish(oci.project) == exit_codes.REFUSED
    assert "lacks the rigor asset" in capsys.readouterr().err


# --- record file handling ---------------------------------------------------------


def test_nothing_declared_is_inert(monkeypatch):
    monkeypatch.delenv("CMRU_RECORD_JSON", raising=False)
    monkeypatch.delenv("CMRU_RECORD_BODY", raising=False)
    assert rigor_publish.appendix_from_env() is None
    with rigor_publish.staged_assets("demo", "1.0.0") as staged:
        assert staged == []


@pytest.mark.parametrize("missing", ["CMRU_RECORD_JSON", "CMRU_RECORD_BODY"])
def test_one_variable_without_the_other_refuses(record_env, monkeypatch, missing, capsys):
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as raised:
        rigor_publish.appendix_from_env()
    assert raised.value.code == exit_codes.REFUSED
    assert "must be set together" in capsys.readouterr().err


def test_body_must_be_nonempty_bounded_utf8_regular(record_env, tmp_path):
    body = record_env / "release-body.md"
    body.write_bytes(b"")
    with pytest.raises(SystemExit):
        rigor_publish.appendix_from_env()
    body.write_bytes(b"x" * (8 * 1024 + 1))
    with pytest.raises(SystemExit):
        rigor_publish.appendix_from_env()
    body.write_bytes(b"\xff\xfe")
    with pytest.raises(SystemExit):
        rigor_publish.appendix_from_env()
    body.unlink()
    body.symlink_to(record_env / "record.json")
    with pytest.raises(SystemExit):
        rigor_publish.appendix_from_env()


def test_record_must_be_nonempty_bounded_regular(record_env):
    record = record_env / "record.json"
    record.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(SystemExit):
        with rigor_publish.staged_assets("demo", "1.0.0"):
            pass  # pragma: no cover


def test_verify_uploaded_needs_a_release_id(capsys):
    with pytest.raises(SystemExit):
        rigor_publish.verify_uploaded(object(), None, "demo-v1.0.0", [Path(__file__)])
    assert "no release ID" in capsys.readouterr().err
    rigor_publish.verify_uploaded(object(), None, "demo-v1.0.0", [])  # nothing staged: fine


# --- wheel-publish ----------------------------------------------------------------


class WheelGH:
    def __init__(self) -> None:
        self.assets: dict[str, bytes] = {}
        self.bodies: list[str] = []

    def asset_download_url(self, tag, name):
        return f"https://example.test/{tag}/{name}"

    def publish(self, tag, title, notes, assets, **_options):
        if tag.endswith("-latest"):
            return {"id": 2}
        self.bodies.append(notes)
        self.assets = {Path(a).name: Path(a).read_bytes() for a in assets}
        return {"id": 1}

    def list_assets(self, release_id):
        return [{"name": n, "size": len(b)} for n, b in self.assets.items()]


@pytest.fixture
def wheel_publish(monkeypatch, tmp_path):
    gh = WheelGH()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: gh)
    for key, value in (("GITHUB_PUSH_PAT", "t"), ("GITHUB_USERNAME", "o"), ("GITHUB_REPO", "r")):
        monkeypatch.setenv(key, value)
    project = tmp_path / "assay"
    _wheel(project / "dist")
    handlers.main(["wheel-publish", "--prefix", "assay", "--cwd", str(project)])
    return gh


def test_wheel_publish_carries_the_appendix_and_the_record(monkeypatch, tmp_path, record_env):
    gh = WheelGH()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: gh)
    for key, value in (("GITHUB_PUSH_PAT", "t"), ("GITHUB_USERNAME", "o"), ("GITHUB_REPO", "r")):
        monkeypatch.setenv(key, value)
    project = tmp_path / "assay"
    _wheel(project / "dist")
    assert handlers.main(["wheel-publish", "--prefix", "assay", "--cwd", str(project)]) == 0
    assert gh.bodies[0].endswith("\n\n" + APPENDIX)
    assert gh.bodies[0].count("## Rigor at publication") == 1
    _assert_rigor_assets(gh.assets, "assay-v1.2.3-rigor.json")


def test_wheel_publish_without_a_record_is_unchanged(wheel_publish, monkeypatch):
    assert sorted(wheel_publish.assets) == [
        "assay-1.2.3-py3-none-any.whl", "assay-1.2.3-py3-none-any.whl.sha256",
    ]
    assert "Rigor at publication" not in wheel_publish.bodies[0]


def test_keystone_ignores_rigor_assets_for_a_dev_build(tmp_path):
    asset = tmp_path / "demo-1.0.0.dev1.tar.gz"
    asset.write_bytes(b"x")
    rigor = tmp_path / "demo-v1.0.0.dev1-rigor.json"
    rigor.write_bytes(RECORD)
    published: list = []

    class GH:
        def publish(self, tag, title, notes, assets, **_o):
            published.append([Path(a).name for a in assets])
            return {"id": 1}

    release.publish_versioned(
        GH(), prefix="demo", version="1.0.0.dev1", asset_path=asset, rigor_assets=[rigor],
    )
    assert published == [["demo-1.0.0.dev1.tar.gz", "demo-1.0.0.dev1.tar.gz.sha256"]]


# --- tarball-publish --------------------------------------------------------------


@pytest.fixture
def tarball(monkeypatch, tmp_path):
    for name in ("CMRU_BUILD_OUTPUT_ROOT", "CMRU_BUILD_OUTPUT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(handlers, "_require_env", lambda _name: "configured")
    holder = SimpleNamespace(gh=SizedGH(), cwd=tmp_path)
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *_a, **_k: holder.gh)
    _write_dist(tmp_path / "dist")
    return holder


def test_variant_tarball_publish_carries_the_record_on_the_versioned_release(tarball, record_env):
    handlers.cmd_tarball_publish(_args(tarball.cwd))
    versioned = tarball.gh.releases["tls-edge-v1.2.3"]
    _assert_rigor_assets(versioned["assets"], "tls-edge-v1.2.3-rigor.json")
    assert versioned["body"].endswith("\n\n" + APPENDIX)
    assert "tls-edge-v1.2.3-rigor.json" not in tarball.gh.releases["tls-edge-latest"]["assets"]


def test_variant_tarball_publish_refuses_when_the_record_is_not_read_back(tarball, record_env):
    tarball.gh.drop = {"tls-edge-v1.2.3-rigor.json"}
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_tarball_publish(_args(tarball.cwd))
    assert raised.value.code == exit_codes.REFUSED


def test_variant_tarball_publish_without_a_record_is_unchanged(tarball):
    handlers.cmd_tarball_publish(_args(tarball.cwd))
    names = tarball.gh.releases["tls-edge-v1.2.3"]["assets"]
    assert not any("rigor" in n for n in names)
    assert "Rigor at publication" not in tarball.gh.releases["tls-edge-v1.2.3"]["body"]


def test_single_tarball_publish_carries_the_record(tarball, record_env):
    for stale in (tarball.cwd / "dist").glob("*py314*"):
        stale.unlink()
    handlers.cmd_tarball_publish(_args(
        tarball.cwd, variant=[], extra_asset=[], glob="tls-edge-v*-py313.tar.xz",
    ))
    versioned = tarball.gh.releases["tls-edge-v1.2.3"]
    _assert_rigor_assets(versioned["assets"], "tls-edge-v1.2.3-rigor.json")
    assert versioned["body"].endswith("\n\n" + APPENDIX)


# --- dry run ----------------------------------------------------------------------


def test_dry_run_preview_shows_the_appendix_only_for_a_record_step_project():
    with_step = SimpleNamespace(record_step="record")
    without = SimpleNamespace(record_step="")
    shown = cli._with_rigor_preview(with_step, "BODY")
    assert shown.startswith("BODY\n\n## Rigor at publication\n\n")
    assert "'record'" in shown and "-rigor.json" in shown
    assert cli._with_rigor_preview(without, "BODY") == "BODY"


# --- immutable releases (review M8): refuse before any mutation ---------------------


def _serve_setting(api, response):
    original = api._serve

    def serve(method, path, data):
        if path == "/immutable-releases":
            return response
        return original(method, path, data)

    api._serve = serve


def test_oci_refuses_before_any_write_when_the_repository_has_immutable_releases(
    oci, record_env, capsys,
):
    _serve_setting(oci.api, (200, '{"enabled": true, "enforced_by_owner": false}'))
    assert _publish(oci.project) == 1  # a clean _die refusal
    assert "immutable releases enabled" in capsys.readouterr().err
    assert not any(c[0] in ("POST", "PATCH", "DELETE") or c[1].startswith("upload") for c in oci.api.calls)


@pytest.mark.parametrize("response", [
    (200, '{"enabled": false}'), (403, "{}"), (404, "{}"), (200, "not json"), (200, "[]"),
])
def test_an_unknown_or_disabled_setting_does_not_block_the_attach(oci, record_env, response):
    _serve_setting(oci.api, response)
    assert _publish(oci.project) == 0
    assert "demo-v2.0.0-rigor.json" in oci.api.blobs


def test_oci_refuses_an_immutable_release_before_deleting_an_asset(oci, record_env, capsys):
    api = oci.api
    api.seed_release(TAG, assets=({"id": 1, "name": "demo-v2.0.0-rigor.json", "size": 3},))
    api.releases[TAG]["immutable"] = True
    api.tags[TAG] = oci.commit
    assert _publish(oci.project) == 1  # a clean _die refusal
    assert "the published release is immutable" in capsys.readouterr().err
    assert ("DELETE", "/releases/assets/1") not in api.calls
    assert api.blobs == {}
