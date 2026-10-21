"""Oracle for the preview archives (oldstable, oldstable-backports, testing): visible, never auto-selected."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apt_preferences as prefs  # noqa: E402

ARCHIVES = ("oldstable", "oldstable-backports", "testing")

# The operator's original line. In apt_preferences(5) its three conditions are ANDed, so it matches no
# archive and testing would keep the default priority 500. Kept as the negative control.
OPERATOR_ORIGINAL = """\
# Oldstable / oldstable-backports / testing — visible but never auto-selected
Package: *
Pin: release a=oldstable, a=oldstable-backports, a=testing
Pin-Priority: 100
"""


def test_every_preview_archive_has_its_own_stanza_at_priority_100() -> None:
    stanzas = prefs.parse_preferences((ROOT / "apt" / "preview-archives.pref").read_text(encoding="utf-8"))
    assert prefs.archive_pin_problems(stanzas, ARCHIVES, 100) == []
    assert len(stanzas) == len(ARCHIVES)


def test_the_operators_three_archive_line_is_rejected() -> None:
    problems = prefs.archive_pin_problems(prefs.parse_preferences(OPERATOR_ORIGINAL), ARCHIVES, 100)
    assert any("ALL match" in p for p in problems), problems
    for archive in ARCHIVES:
        assert any(repr(archive) in p for p in problems), (archive, problems)


@pytest.mark.parametrize("priority", [99, 500, 501])
def test_a_wrong_priority_is_rejected(priority: int) -> None:
    text = (ROOT / "apt" / "preview-archives.pref").read_text(encoding="utf-8").replace("Pin-Priority: 100", f"Pin-Priority: {priority}", 1)
    problems = prefs.archive_pin_problems(prefs.parse_preferences(text), ARCHIVES, 100)
    assert problems and any(str(priority) in p for p in problems)


def test_a_missing_archive_is_rejected() -> None:
    text = "Package: *\nPin: release a=oldstable\nPin-Priority: 100\n"
    problems = prefs.archive_pin_problems(prefs.parse_preferences(text), ARCHIVES, 100)
    assert len(problems) == 2


def test_the_testing_source_is_deb822_and_signed_by_the_archive_keyring() -> None:
    fields: dict[str, str] = {}
    for line in (ROOT / "apt" / "testing.sources").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    assert fields["Types"] == "deb"
    assert fields["Suites"] == "testing"
    assert fields["URIs"].startswith("http")
    assert fields["Signed-By"] == "/usr/share/keyrings/debian-archive-keyring.gpg"


def test_the_dockerfile_installs_both_files_and_never_leaves_testing_at_default_priority() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chmod=0644 apt/testing.sources /etc/apt/sources.list.d/testing.sources" in dockerfile
    assert "COPY --chmod=0644 apt/preview-archives.pref /etc/apt/preferences.d/preview-archives" in dockerfile
    # The old ad-hoc testing.list is gone; the skopeo pin is the only temporary pin and is removed again.
    assert "sources.list.d/testing.list" not in dockerfile
    # The only testing pin the Dockerfile writes is the temporary skopeo one, and it is removed again.
    assert dockerfile.count("preferences.d/skopeo-testing") == 2
    assert "rm -f /etc/apt/preferences.d/skopeo-testing" in dockerfile
    assert "Package: skopeo" in dockerfile and "Package: *" not in dockerfile.split("# Skopeo:")[1].split("# Upstream Node.js")[0]
