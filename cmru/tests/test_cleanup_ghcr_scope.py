"""KI-117: the GHCR wildcard resolves to THIS repository's packages only.

The fake GitHub API lists a FOREIGN package (another repository's image under
the same account) and an unlinked one. Any version listing or deletion that
mentions them, in dry-run or real mode, fails the test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cmru import cli

OLD = "2020-01-01T00:00:00Z"
FOREIGN = "dstdns-controller"
UNLINKED = "orphan"

RECORDS = [
    {"name": "mine", "repository": {"full_name": "owner/repo"}},
    {"name": "mine-ocr", "repository": {"full_name": "Owner/Repo"}},
    {"name": FOREIGN, "repository": {"full_name": "owner/dstdns"}},
    {"name": UNLINKED, "repository": None},
]
PUBLISHING = {"tool": SimpleNamespace(artifacts=("oci-image",))}


class _FakeApi:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(cli, "_list_container_package_records", lambda *_a: list(RECORDS))

        def versions(owner, package, token, owner_type):
            self.requests.append(("VERSIONS", package))
            return [{"id": 11, "updated_at": OLD, "metadata": {"container": {"tags": ["old"]}}}]

        def delete(owner, package, token, version_id, owner_type, dry_run):
            self.requests.append(("DELETE_VERSION_DRY" if dry_run else "DELETE_VERSION", package))

        monkeypatch.setattr(cli, "list_package_versions", versions)
        monkeypatch.setattr(cli, "delete_package_version", delete)

    def touched(self) -> set[str]:
        return {p for _kind, p in self.requests}


def _cleanup(packages):
    return cli.CleanupConfig(
        release_tag_prefixes=[], keep_release_tags=[],
        ghcr_packages=packages, ghcr_delete_packages=[],
    )


def _run(packages, dry_run, configs=PUBLISHING, repo="repo"):
    cli.cleanup_ghcr(
        "owner", "tok", "user", datetime.now(timezone.utc), dry_run, _cleanup(packages),
        repo=repo, configs=configs,
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_wildcard_touches_only_this_repositorys_packages(monkeypatch, dry_run):
    api = _FakeApi()
    api.install(monkeypatch)

    _run(["*"], dry_run)

    assert api.touched() == {"mine", "mine-ocr"}
    assert FOREIGN not in api.touched()
    assert UNLINKED not in api.touched()
    kind = "DELETE_VERSION_DRY" if dry_run else "DELETE_VERSION"
    assert (kind, "mine") in api.requests


@pytest.mark.parametrize("dry_run", [True, False])
def test_explicit_list_never_touches_foreign_package(monkeypatch, dry_run):
    api = _FakeApi()
    api.install(monkeypatch)

    _run(["mine"], dry_run)

    assert api.touched() == {"mine"}


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("configs", [None, {}, {"lib": SimpleNamespace(artifacts=("wheel",))}])
def test_wildcard_refused_without_resolvable_publishing_projects(monkeypatch, dry_run, configs):
    api = _FakeApi()
    api.install(monkeypatch)

    with pytest.raises(ValueError, match="explicitly"):
        _run(["*"], dry_run, configs=configs)

    assert api.requests == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_wildcard_refused_without_repository(monkeypatch, dry_run):
    api = _FakeApi()
    api.install(monkeypatch)
    with pytest.raises(ValueError, match="Run cmru from the repository"):
        _run(["*"], dry_run, repo=None)
    assert api.requests == []


def test_wildcard_with_no_linked_packages_selects_nothing(monkeypatch):
    api = _FakeApi()
    api.install(monkeypatch)
    monkeypatch.setattr(cli, "_list_container_package_records", lambda *_a: [RECORDS[2], RECORDS[3]])
    _run(["*"], False)
    assert api.requests == []


def test_wildcard_plus_explicit_name_dedupes(monkeypatch):
    api = _FakeApi()
    api.install(monkeypatch)
    _run(["*", "mine"], True)
    assert [p for k, p in api.requests if k == "VERSIONS"].count("mine") == 1
    assert FOREIGN not in api.touched()
