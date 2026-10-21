"""Oracle for the apt priority policy: stable 500, stable-backports 600, and the preview archives
(oldstable, oldstable-backports, testing) at 100 - visible, never auto-selected."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apt_preferences as prefs  # noqa: E402

ARCHIVES = ("oldstable", "oldstable-backports", "testing")


def _stanzas(name: str) -> list[prefs.Stanza]:
    return prefs.parse_preferences((ROOT / "apt" / name).read_text(encoding="utf-8"))

OPERATOR_PRIORITIES = """\
# /etc/apt/preferences.d/debian-priorities

# Current stable — normal priority
Package: *
Pin: release a=stable
Pin-Priority: 500

# Current stable backports — preferred over stable
Package: *
Pin: release a=stable-backports
Pin-Priority: 600
"""


def _dockerfile_vendor_hosts() -> set[str]:
    import re

    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    # `deb [options] https://host/path ...` as written into sources.list.d by the Dockerfile
    return set(re.findall(r"\bdeb (?:\[[^\]]*\] )?https?://([^/\s]+)", text)) - {"deb.debian.org"}


def test_debian_priorities_is_the_operators_text_byte_for_byte() -> None:
    assert (ROOT / "apt" / "debian-priorities.pref").read_text(encoding="utf-8").rstrip("\n") == OPERATOR_PRIORITIES.rstrip("\n")


def test_every_vendor_source_the_dockerfile_adds_has_a_700_origin_pin_above_backports() -> None:
    hosts = _dockerfile_vendor_hosts()
    assert hosts >= {"packages.sury.org", "apt.postgresql.org", "packages.redis.io", "download.docker.com"}, hosts
    stanzas = _stanzas("vendor-origins.pref")
    assert prefs.origin_pin_problems(stanzas, tuple(sorted(hosts)), 700) == []
    assert {s.pin for s in stanzas} == {f"origin {h}" for h in hosts}, "pin for a host the Dockerfile no longer adds"
    assert min(s.priority for s in stanzas) > 600


def test_a_vendor_host_without_a_pin_is_rejected() -> None:
    problems = prefs.origin_pin_problems(_stanzas("vendor-origins.pref"), ("packages.example.org",), 700)
    assert problems and "packages.example.org" in problems[0]


def test_vendor_origins_are_installed_next_to_debian_priorities() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chmod=0644 apt/vendor-origins.pref /etc/apt/preferences.d/vendor-origins" in dockerfile
    assert dockerfile.index("apt/vendor-origins.pref") < dockerfile.index("# Core system packages")


def test_stable_is_500_and_stable_backports_is_600_each_in_its_own_stanza_by_archive_name() -> None:
    stanzas = _stanzas("debian-priorities.pref")
    assert prefs.archive_pin_problems(stanzas, ("stable",), 500) == []
    assert prefs.archive_pin_problems(stanzas, ("stable-backports",), 600) == []
    assert len(stanzas) == 2


def test_the_policy_never_pins_by_codename_and_never_names_a_preview_archive() -> None:
    # `n=`/`a=trixie-backports` would be the codename; the archive NAME is what apt matches with `a=`.
    for stanza in _stanzas("debian-priorities.pref"):
        assert [k for k, _ in stanza.conditions] == ["a"], stanza
        assert "trixie" not in stanza.pin
    text = (ROOT / "apt" / "debian-priorities.pref").read_text(encoding="utf-8")
    assert "# Current stable backports" in text and "# Current stable — normal priority" in text


def test_the_preview_archives_do_not_overlap_the_priority_policy() -> None:
    assert {c for s in _stanzas("preview-archives.pref") for c in s.conditions} == {("a", a) for a in ARCHIVES}


def test_the_old_templated_backports_pref_is_gone() -> None:
    assert not (ROOT / "apt" / "backports.pref.in").exists()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "backports.pref.in" not in dockerfile and "preferences.d/backports" not in dockerfile
    assert "COPY --chmod=0644 apt/debian-priorities.pref /etc/apt/preferences.d/debian-priorities" in dockerfile
    # The backports SOURCE stays codename-based (it is rendered, the pin is not).
    assert 'BACKPORTS_SUITE="${DEBIAN_VERSION}-backports"' in dockerfile


def test_the_temporary_skopeo_pin_beats_stable_backports() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert r"Pin: release a=testing\nPin-Priority: 601" in dockerfile

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


def test_the_dockerfile_installs_both_files_after_the_main_install_and_never_leaves_testing_at_default_priority() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copy_sources = "COPY --chmod=0644 apt/testing.sources /etc/apt/sources.list.d/testing.sources"
    copy_pref = "COPY --chmod=0644 apt/preview-archives.pref /etc/apt/preferences.d/preview-archives"
    assert dockerfile.count(copy_sources) == 1 and dockerfile.count(copy_pref) == 1
    # Copied just before the skopeo step, which is placed after the LAST other `apt-get update`, so only the
    # skopeo step ever fetches testing indexes.
    skopeo_at = dockerfile.index("# Skopeo from Debian testing")
    assert dockerfile.index(copy_pref) < skopeo_at and dockerfile.index(copy_sources) < skopeo_at
    assert "# Core system packages" in dockerfile[: dockerfile.index(copy_sources)]
    # The skopeo step's own `apt-get update` is the first one after the COPY and the only one: every other
    # layer that updates apt runs before the testing files exist.
    after_copy = dockerfile[dockerfile.index(copy_sources):]
    assert after_copy.count("apt-get update") == 1 and after_copy.index("apt-get update") > after_copy.index("# Skopeo from Debian testing")
    # The old ad-hoc testing.list is gone; the skopeo pin is the only temporary pin and is removed again.
    assert "sources.list.d/testing.list" not in dockerfile
    skopeo = dockerfile.split("# Skopeo from Debian testing")[1].split("# Upstream Node.js")[0]
    assert "Package: skopeo" in skopeo and "Package: *" not in skopeo
    assert "rm -f \"${pin}\"" in skopeo
    # Fail closed: a missing testing skopeo is an error, never an [INFO] fallback to trixie's.
    assert "exit 1" in skopeo and "keeping trixie" not in skopeo
    # The dry-run output is captured first: `apt-get ... | grep -q` under pipefail can take a SIGPIPE.
    assert "| grep -q" not in skopeo and 'simulated="$(apt-get install --dry-run' in skopeo
