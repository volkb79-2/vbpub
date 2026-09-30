"""B129 / W10 step 1: characterization of the inline guards in ``attestation``,
``provenance``, ``adjudication``, ``mutation`` (shard merge) and ``runner``
(derived infrastructure facts).

Pins accept and refuse, with the exact message and reason code. Run against
the UNCHANGED source; never edited to follow the refactor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_lane

from assay import attestation, provenance
from assay.adjudication import evaluate_provenance
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationStateError, merge_mutation_shards
from assay.runner import resolve_command_plan

# ---- attestation: reviewed path and attestation_dir ----------------------------

COMMIT = "a" * 40


def _record(**overrides):
    values = dict(producer="p", attested_commit=COMMIT, reviewed_paths=("a.py",))
    values.update(overrides)
    return attestation.AttestationRecord(**values)


def test_attestation_record_accepts_a_one_character_reviewed_path():
    assert _record(reviewed_paths=("a",)).reviewed_paths == ("a",)


@pytest.mark.parametrize("path", ["", 1, None, True, b"a", ("a",)])
def test_attestation_record_refuses_an_empty_or_non_string_reviewed_path(path):
    with pytest.raises(ValueError) as caught:
        _record(reviewed_paths=("ok.py", path))
    assert str(caught.value) == (
        f"attestation reviewed path must be a non-empty string, got {path!r}"
    )


@pytest.mark.parametrize("value", ["", 1, None, True, b"a", ["a"]])
def test_attestation_dir_refuses_an_empty_or_non_string_value(value):
    with pytest.raises(AssayError) as caught:
        attestation._validate_attestation_dir(value)
    assert str(caught.value) == (
        f"attestation_dir must be a non-empty string, got {value!r}"
    )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_attestation_dir_accepts_one_character():
    assert attestation._validate_attestation_dir("a") is None


# ---- provenance._installed_wheel_digest: non-empty string, sha256 hex ----------


class _Distribution:
    def __init__(self, direct_url):
        self.direct_url = direct_url

    def read_text(self, name):
        assert name == "direct_url.json"
        return self.direct_url


def _direct_url(archive_info):
    return json.dumps({"url": "https://host/assay-1.0-py3-none-any.whl", "archive_info": archive_info})


def _digest_of(archive_info):
    return provenance._installed_wheel_digest(_Distribution(_direct_url(archive_info)))


HEX64 = "0123456789abcdef" * 4


def test_wheel_digest_accepts_sixty_four_lowercase_hex_characters():
    assert _digest_of({"hashes": {"sha256": HEX64}}) == HEX64


def test_wheel_digest_lowercases_uppercase_hex_before_checking():
    assert _digest_of({"hashes": {"sha256": HEX64.upper()}}) == HEX64


def test_wheel_digest_reads_the_legacy_single_hash_spelling():
    assert _digest_of({"hash": f"sha256={HEX64}"}) == HEX64


@pytest.mark.parametrize(
    "digest",
    [
        HEX64[:63],
        HEX64 + "a",
        "g" + HEX64[1:],
        HEX64[:63] + " ",
        HEX64 + "\n",
        HEX64[:63] + "\n",
        "",
    ],
)
def test_wheel_digest_refuses_a_malformed_digest(digest):
    assert _digest_of({"hashes": {"sha256": digest}}) is None


def test_wheel_digest_refuses_an_empty_legacy_digest():
    assert _digest_of({"hash": "sha256="}) is None


@pytest.mark.parametrize("hashes", [{"sha256": 5}, {"sha256": None}, {}, []])
def test_wheel_digest_without_a_string_digest_or_legacy_hash_is_none(hashes):
    assert _digest_of({"hashes": hashes}) is None


# ---- adjudication.evaluate_provenance: strict schema_version -------------------

HEAD = "1b369e23" + "a" * 32


def _green(**overrides) -> bytes:
    document = {
        "schema_version": 1,
        "instance": "dstdns-dev",
        "commit_under_test": "1b369e23",
        "tree_state": "clean",
        "containers": [],
        "overall": "verified-match",
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


@pytest.mark.parametrize("version", [1, 2])
def test_provenance_accepts_each_accepted_schema_version(version):
    assert evaluate_provenance(_green(schema_version=version), HEAD) == (
        Outcome.PASS,
        None,
    )


@pytest.mark.parametrize("version", [True, False, 0, 3, -1, 1.5, "1", None, [1]])
def test_provenance_refuses_a_bool_non_int_or_unaccepted_schema_version(version):
    assert evaluate_provenance(_green(schema_version=version), HEAD) == (
        Outcome.ERROR,
        ReasonCode.FORMAT_MISMATCH,
    )


def test_provenance_refuses_an_absent_schema_version():
    document = json.loads(_green())
    del document["schema_version"]
    assert evaluate_provenance(json.dumps(document).encode("utf-8"), HEAD) == (
        Outcome.ERROR,
        ReasonCode.FORMAT_MISMATCH,
    )


# ---- mutation.merge_mutation_shards: non-empty lane and commit -----------------


def _shard(**overrides):
    document = {
        "schema_version": 1,
        "lane": "lane",
        "commit": COMMIT,
        "shard_index": 0,
        "shard_count": 1,
        "candidate_ids": [],
    }
    document.update(overrides)
    return document


def test_shard_merge_accepts_a_one_character_lane_and_commit():
    shard = _shard(lane="l", commit="c", candidate_ids=["a" * 64])
    assert merge_mutation_shards([shard]) == ("a" * 64,)


@pytest.mark.parametrize("field", ["lane", "commit"])
@pytest.mark.parametrize("value", ["", 1, None, True, b"x", ["x"]])
def test_shard_merge_refuses_an_empty_or_non_string_lane_or_commit(field, value):
    with pytest.raises(MutationStateError) as caught:
        merge_mutation_shards([_shard(**{field: value})])
    assert str(caught.value) == "shard lane and commit must be non-empty strings"


# ---- runner.resolve_command_plan: derived fact must be a non-empty string ------


def _derived(tmp_path: Path, toml_text: str):
    facts = tmp_path / "ciu.global.toml"
    facts.write_text(toml_text, encoding="utf-8")
    lane = make_lane(infrastructure={"image": "derived:deploy.image"})
    return lambda: resolve_command_plan(
        lane, passthrough_source={}, infrastructure_source=facts
    )


def test_a_derived_fact_accepts_a_one_character_string(tmp_path):
    plan = _derived(tmp_path, "[deploy]\nimage = 'x'\n")()
    assert plan.env_effective["image"] == "x"


@pytest.mark.parametrize(
    "toml_text",
    [
        "[deploy]\nimage = ''\n",
        "[deploy]\nimage = 5\n",
        "[deploy]\nimage = true\n",
        "[deploy]\nimage = ['x']\n",
        "[deploy]\nother = 'x'\n",
        "",
    ],
)
def test_a_derived_fact_refuses_an_absent_non_string_or_empty_value(
    tmp_path, toml_text
):
    with pytest.raises(AssayError) as caught:
        _derived(tmp_path, toml_text)()
    assert str(caught.value) == (
        "infrastructure fact 'image' derived 'deploy.image' is absent, "
        "not a string, or empty"
    )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
