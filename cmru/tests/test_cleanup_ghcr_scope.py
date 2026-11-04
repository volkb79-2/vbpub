"""KI-117: GHCR cleanup must never reach packages that are not named explicitly.

The fake GitHub API below lists a FOREIGN package (another repository's image
under the same account). Any request that mentions it, in dry-run or real mode,
fails the test.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cmru import cli, config

OLD = "2020-01-01T00:00:00Z"
FOREIGN = "dstdns-controller"


class _FakeApi:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def install(self, monkeypatch) -> None:
        def listing(owner, token, owner_type):
            self.requests.append(("LIST_PACKAGES", ""))
            return ["mine", FOREIGN]

        def versions(owner, package, token, owner_type):
            self.requests.append(("VERSIONS", package))
            return [{"id": 11, "updated_at": OLD, "metadata": {"container": {"tags": ["old"]}}}]

        def delete(owner, package, token, version_id, owner_type, dry_run):
            self.requests.append(("DELETE_VERSION_DRY" if dry_run else "DELETE_VERSION", package))

        monkeypatch.setattr(cli, "list_container_packages", listing)
        monkeypatch.setattr(cli, "list_package_versions", versions)
        monkeypatch.setattr(cli, "delete_package_version", delete)

    def touched(self) -> set[str]:
        return {p for _kind, p in self.requests if p}


def _cleanup(packages):
    return cli.CleanupConfig(
        release_tag_prefixes=[], keep_release_tags=[],
        ghcr_packages=packages, ghcr_delete_packages=[],
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_explicit_list_never_touches_foreign_package(monkeypatch, dry_run):
    api = _FakeApi()
    api.install(monkeypatch)

    cli.cleanup_ghcr(
        "owner", "tok", "user", datetime.now(timezone.utc), dry_run, _cleanup(["mine"]),
    )

    assert api.touched() == {"mine"}
    assert FOREIGN not in api.touched()
    assert ("LIST_PACKAGES", "") not in api.requests  # no owner-wide enumeration


@pytest.mark.parametrize("dry_run", [True, False])
def test_wildcard_is_refused_before_any_request(monkeypatch, dry_run):
    api = _FakeApi()
    api.install(monkeypatch)

    with pytest.raises(ValueError, match="KI-117"):
        cli.cleanup_ghcr(
            "owner", "tok", "user", datetime.now(timezone.utc), dry_run, _cleanup(["*"]),
        )

    assert api.requests == []


def test_wildcard_mixed_with_names_is_refused(monkeypatch):
    api = _FakeApi()
    api.install(monkeypatch)
    with pytest.raises(ValueError, match="KI-117"):
        cli.cleanup_ghcr(
            "owner", "tok", "user", datetime.now(timezone.utc), True, _cleanup(["mine", "*"]),
        )
    assert api.requests == []


def test_config_rejects_wildcard_ghcr_packages(capsys):
    raw = {
        "release_tag_prefixes": ["*"], "keep_release_tags": [],
        "ghcr_packages": ["*"], "ghcr_delete_packages": [],
    }
    with pytest.raises(SystemExit) as exc:
        config._parse_cleanup(raw)
    assert exc.value.code == 2
    assert "KI-117" in capsys.readouterr().err


def test_config_accepts_explicit_names_and_wildcard_release_prefixes():
    parsed = config._parse_cleanup({
        "release_tag_prefixes": ["*"], "keep_release_tags": [],
        "ghcr_packages": ["mine"], "ghcr_delete_packages": [],
    })
    assert parsed.ghcr_packages == ["mine"]
    assert parsed.release_tag_prefixes == ["*"]
