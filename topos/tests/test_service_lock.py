"""Strict service wheel pins and daemon lock parsing."""

from __future__ import annotations

import pytest

from topos.service.lock import LockError, parse_lock, parse_pin


def test_pin_parses_version_and_sha256() -> None:
    digest = "a" * 64
    assert parse_pin(f"0.7.2rc1@sha256:{digest}", "--wheel") == ("0.7.2rc1", digest)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "latest@sha256:" + "a" * 64,
        "0.7.0@sha256:" + "A" * 64,
        "0.7.0@sha256:" + "a" * 63,
        "0.7.0 " + "a" * 64,
    ),
)
def test_pin_refuses_non_strict_values(value: str) -> None:
    with pytest.raises(LockError, match="<version>@sha256:<64 lowercase hex>"):
        parse_pin(value, "--daemon-lock")


def test_lock_parses_hashed_requirements_and_ignores_comments() -> None:
    digest = "b" * 64
    contents = f"# runtime wheels\n\ntextual==8.2.8 --hash=sha256:{digest}\n"
    assert parse_lock(contents)[0].name == "textual"
    assert parse_lock(contents)[0].version == "8.2.8"
    assert parse_lock(contents)[0].sha256 == digest


@pytest.mark.parametrize(
    ("contents", "message"),
    (
        ("textual>=8.2.8\n", "must pin one version and sha256 hash"),
        ("topos==0.7.0 --hash=sha256:" + "a" * 64 + "\n", "must not include topos"),
        ("cli_extended==0.5.3 --hash=sha256:" + "a" * 64 + "\n", "must not include cli_extended"),
        ("augeaswrap==0.1.0 --hash=sha256:" + "a" * 64 + "\n", "must not include augeaswrap"),
        (
            "textual==8.2.8 --hash=sha256:" + "a" * 64 + "\n"
            "textual==8.2.9 --hash=sha256:" + "b" * 64 + "\n",
            "duplicate requirement",
        ),
    ),
)
def test_lock_refuses_unhashed_first_party_or_duplicate_rows(contents: str, message: str) -> None:
    with pytest.raises(LockError, match=message):
        parse_lock(contents)
