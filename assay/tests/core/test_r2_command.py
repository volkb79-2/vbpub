from __future__ import annotations

import hashlib

import pytest

from assay.r2_command import (
    R2_APPENDED,
    R2_TRANSFORM_ID,
    UnrecognizedCoverageOption,
    collection_digest,
    transform_argv,
)


def test_r2_transform_removes_only_the_three_declared_coverage_forms():
    argv = (
        "-q",
        "--ignore=tests/slow.py",
        "--cov=src/assay",
        "--cov-branch",
        "--cov-report=json:coverage.json",
        "tests",
    )

    assert transform_argv(argv) == (
        "-q", "--ignore=tests/slow.py", "tests"
    )
    assert R2_TRANSFORM_ID == "assay-r2-pytest-nocov/1"
    assert R2_APPENDED == ("-p", "no:pytest_cov")


@pytest.mark.parametrize(
    "argv",
    [
        ("-q",),
        ("--cov=src", "--cov=tests"),
        ("--cov-branch", "--cov-branch"),
        ("--cov-report=term", "--cov-report=json:x"),
        ("tests/--cov=positional.py",),
    ],
)
def test_r2_transform_preserves_supported_order_and_duplicates(argv):
    transformed = transform_argv(argv)
    assert isinstance(transformed, tuple)
    if argv == ("--cov=src", "--cov=tests"):
        assert transformed == ()
    elif argv == ("--cov-branch", "--cov-branch"):
        assert transformed == ()
    elif argv[0].startswith("--cov-report="):
        assert transformed == ()
    else:
        assert transformed == argv


@pytest.mark.parametrize(
    "token",
    [
        "--cov",
        "--cov=",
        "--cov-report=",
        "--cov-config=x",
        "--cov-append",
        "--no-cov-on-fail",
        "--no-cov",
        "--covx",
        "--cov-fail-under=90",
        "--no-cov-extra",
    ],
)
def test_r2_transform_refuses_unrecognized_coverage_options(token):
    with pytest.raises(UnrecognizedCoverageOption, match="--") as raised:
        transform_argv((token,))
    assert str(raised.value) == token


def test_r2_transform_refuses_non_string_tokens_without_guessing():
    with pytest.raises(TypeError, match="entries must be strings"):
        transform_argv(("-q", 3))  # type: ignore[arg-type]


def test_collection_digest_is_ordered_duplicate_preserving_utf8_byte_netstrings():
    ids = ("tests/test_a.py::test_é", "tests/test_a.py::test_é", "z")
    expected_payload = b"".join(
        str(len(encoded)).encode("ascii") + b":" + encoded + b"," 
        for encoded in (node_id.encode("utf-8") for node_id in ids)
    )
    digest = hashlib.sha256(expected_payload).hexdigest()

    assert collection_digest(ids) == digest
    assert collection_digest(ids[::-1]) != digest
    assert collection_digest(ids[:-1]) != digest


def test_collection_digest_accepts_empty_and_refuses_non_string_node_ids():
    assert collection_digest(()) == hashlib.sha256(b"").hexdigest()
    with pytest.raises(TypeError, match="node IDs must be strings"):
        collection_digest(("tests/test_a.py::test_x", None))  # type: ignore[arg-type]
