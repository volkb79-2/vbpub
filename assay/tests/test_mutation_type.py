""":class:`~assay.mutation.MutationSite` -- the BOUNDED candidate descriptor
that replaced P11's ``Mutant`` (P21, A-180).

The type this module used to cover carried ``mutated_text``: a full copy of
the mutated file, per candidate. That made ``max_mutants`` unenforceable by
construction, because every candidate's entire source had to exist before
anything could count them -- a cap applied afterwards bounded neither memory
nor work. A site carries the smallest thing that still names the experiment
exactly: a byte span and its replacement.

The oracle each test defends is stated in its own docstring, and every
construction-time refusal below is a shape a wrong adapter could otherwise
put into a verdict's identity fields.
"""

from __future__ import annotations

import hashlib

import pytest

from assay.mutation import MutationSite

#: The manifest's own worked example: a non-ASCII prefix so byte offsets and
#: character offsets genuinely differ (`π` is two UTF-8 bytes).
TEXT = "π = 1\ndef f(a, b, c):\n    return a < b < c and True\n"
SOURCE = TEXT.encode("utf-8")


def _site(**overrides) -> MutationSite:
    fields = {
        "start_byte": 36,
        "end_byte": 37,
        "replacement": b"<=",
        "lineno": 3,
        "operator": "compare-swap",
        "description": "Lt->LtE",
    }
    fields.update(overrides)
    return MutationSite(**fields)


def test_a_site_names_the_exact_bytes_it_replaces():
    """The load-bearing property: the span addresses real source bytes, and
    applying the site changes exactly those and nothing else."""
    site = _site()

    assert SOURCE[site.start_byte : site.end_byte] == b"<"
    mutated = site.apply(SOURCE)

    assert mutated.decode("utf-8") == TEXT.replace("a < b", "a <= b", 1)
    assert mutated[: site.start_byte] == SOURCE[: site.start_byte]
    assert mutated[site.start_byte + len(site.replacement) :] == SOURCE[site.end_byte :]


def test_identity_is_the_syntax_site_not_its_description():
    """A-180: ``lineno`` and ``description`` diagnose; they do not
    distinguish. Two sites differing ONLY in those two are the same
    experiment and must compare equal by identity -- while two genuinely
    different spans must not."""
    first = _site()
    relabelled = _site(lineno=99, description="something else entirely")
    other_span = _site(start_byte=40, end_byte=41)

    assert first.identity == relabelled.identity
    assert first.identity != other_span.identity


def test_identity_hashes_the_replacement_bytes_only():
    """A-180: never a hash of the mutated file, and never derived by diffing
    two full texts -- ``<`` -> ``<=`` collapses to a zero-width insertion
    under a minimal diff, which names no syntax token at all."""
    site = _site()

    assert site.replacement_sha256 == hashlib.sha256(b"<=").hexdigest()
    assert site.replacement_sha256 != hashlib.sha256(site.apply(SOURCE)).hexdigest()


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"start_byte": -1}, "start_byte must be >= 0"),
        ({"start_byte": 37, "end_byte": 37}, "empty or reversed"),
        ({"start_byte": 40, "end_byte": 37}, "empty or reversed"),
        ({"start_byte": "36"}, "must be an integer"),
        ({"start_byte": True}, "must be an integer"),
        ({"end_byte": 1.5}, "must be an integer"),
        ({"lineno": 0}, "lineno must be >= 1"),
        ({"lineno": None}, "must be an integer"),
        ({"replacement": b""}, "non-empty bytes"),
        ({"replacement": "<="}, "non-empty bytes"),
        ({"replacement": b"\xff\xfe"}, "not valid UTF-8"),
        ({"operator": "invented-swap"}, "must be one of"),
        ({"operator": ""}, "must be one of"),
        ({"description": ""}, "non-empty string"),
    ],
)
def test_a_site_that_could_never_describe_a_real_experiment_is_refused(
    overrides: dict, match: str
):
    """Every one of these would otherwise reach a verdict's identity fields,
    where a consumer has no source text to check them against."""
    with pytest.raises(ValueError, match=match):
        _site(**overrides)


def test_an_empty_span_is_refused_because_a_no_op_is_not_an_experiment():
    """The zero-width case deserves its own test rather than only a
    parametrised row: it is exactly what a "minimal diff" identity would
    produce for an insertion, and accepting it would let a mutant that
    changes nothing be recorded as one that was killed."""
    with pytest.raises(ValueError, match="always replaces at least one byte"):
        MutationSite(
            start_byte=36,
            end_byte=36,
            replacement=b"<=",
            lineno=3,
            operator="compare-swap",
            description="Lt->LtE",
        )


def test_sites_are_frozen():
    """A descriptor a caller can edit after validation is not validated."""
    site = _site()
    with pytest.raises(Exception):
        site.start_byte = 0  # type: ignore[misc]
