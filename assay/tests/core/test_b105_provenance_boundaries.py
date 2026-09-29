"""Exact artifact-identification boundary coverage for B105."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import types
import zipfile
import zipimport

from assay import provenance
from assay.verdict import JudgeProvenance


class Distribution:
    metadata = {"Name": "assay", "Version": "1.2.3"}

    def __init__(self, root, direct_url=None):
        self.root = root
        self.direct_url = direct_url

    def locate_file(self, name):
        return self.root / name

    def read_text(self, name):
        assert name == "direct_url.json"
        return self.direct_url


def test_sha256_file_reads_multiple_bounded_chunks(tmp_path):
    artifact = tmp_path / "artifact.whl"
    payload = b"a" * (provenance._CHUNK + 17)
    artifact.write_bytes(payload)

    assert provenance._sha256_file(artifact) == hashlib.sha256(payload).hexdigest()


def test_zipapp_archive_refuses_a_loader_without_archive_name(tmp_path):
    archive = tmp_path / "temporary.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("assay/__init__.py", "")
    loader = zipimport.zipimporter(str(archive))
    loader.archive = ""
    assert provenance._zipapp_archive(types.SimpleNamespace(__loader__=loader)) is None


def test_installed_wheel_digest_returns_none_when_metadata_read_fails():
    class UnreadableDistribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            raise OSError("metadata directory vanished")

    assert provenance._installed_wheel_digest(UnreadableDistribution()) is None


def test_installed_wheel_digest_refuses_non_object_and_non_string_url():
    for document in ("[]", json.dumps({"archive_info": {}, "url": 1})):
        dist = Distribution(
            root=Path("/unused"),
            direct_url=document,
        )
        assert provenance._installed_wheel_digest(dist) is None


def test_identify_judge_reports_unimported_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "assay", None)
    identity, reason = provenance.identify_judge()
    assert identity is None
    assert "package is not imported" in reason


def test_identify_judge_reports_missing_installed_distribution(monkeypatch, tmp_path):
    def missing(name):
        assert name == "assay"
        raise provenance.PackageNotFoundError(name)

    monkeypatch.setattr(provenance, "distribution", missing)
    module = types.SimpleNamespace(__file__=str(tmp_path / "assay.py"))
    identity, reason = provenance.identify_judge(module=module)
    assert identity is None
    assert "no installed distribution metadata" in reason


def test_identify_judge_reports_module_without_file(tmp_path):
    dist = Distribution(root=tmp_path, direct_url=None)
    identity, reason = provenance.identify_judge(
        module=types.SimpleNamespace(__loader__=None),
        dist=dist,
    )
    assert identity is None
    assert "no __file__" in reason


def test_identify_judge_hashes_the_archive_reported_by_zipimport(tmp_path):
    archive = tmp_path / "assay-1.2.3.pyz"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("assay/__init__.py", "__version__ = '1.2.3'\n")
    loader = zipimport.zipimporter(str(archive))
    module = types.SimpleNamespace(
        __file__=f"{archive}/assay/__init__.py",
        __loader__=loader,
    )

    identity, reason = provenance.identify_judge(
        module=module,
        dist=Distribution(root=archive),
    )
    assert reason is None
    assert identity == JudgeProvenance(
        name="assay",
        version="1.2.3",
        artifact="zipapp",
        digest_algorithm="sha256",
        digest=hashlib.sha256(archive.read_bytes()).hexdigest(),
    )


def test_identify_judge_refuses_removed_zipapp_archive(tmp_path):
    archive = tmp_path / "removed.pyz"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("assay/__init__.py", "")
    loader = zipimport.zipimporter(str(archive))
    archive.unlink()
    module = types.SimpleNamespace(
        __file__=f"{archive}/assay/__init__.py",
        __loader__=loader,
    )

    identity, reason = provenance.identify_judge(
        module=module,
        dist=Distribution(root=archive),
    )
    assert identity is None
    assert "not a readable file now" in reason
