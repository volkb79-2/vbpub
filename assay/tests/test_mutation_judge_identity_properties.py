"""B088 -- the judge-identity invariants, stated over GENERATED input.

`test_mutation_judge_identity.py` names specific, chosen cases (an empty
tree, one entry, a moved file, a delimiter-injection attempt). This module
states the same invariants as properties, because the class of defect B088
belongs to -- an identity that silently collides for two things that are not
the same -- is exactly what chosen examples are worst at finding, and what
nyxloom `reference/TESTING-METHODOLOGY.md`'s "Property-based testing and
Hypothesis" section documents two real 2026-09-10 bugs about.

**Why a separate module.** Hypothesis is not part of assay's runtime closure
(A-005: zero runtime dependencies) and is declared only in the `test` extra.
A module-level `importorskip` in the behavioural file would make a missing
Hypothesis skip the B088 regression tests too, silently -- the failure mode
where a suite reports green having tested nothing. Skipping this file alone
is honest and loses only the generated cases.

**Determinism under a gate.** `derandomize=True` and `database=None` per that
same section and `run-gate-project/LANE-AUTHORING.md` §4: a lane must give
the same answer for the same commit, and Hypothesis's default generation is
not reproducible across runs. `@settings` is declared INLINE rather than
through a registered `gate`/`nightly` profile -- assay registers no profiles
today, and adding profile registration to `tests/conftest.py` is a
lane-authoring change, not part of this bug fix. `deadline=None`: the digest
is pure and fast, but this host is shared with a production game server and
runs one gate container at a time under `nice`, so a wall-clock per-example
deadline measures the host's load, not the code. `max_examples=200` keeps all
three properties well inside a second on an idle host.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from assay import isolation
from assay.mutation import judge_sha256

hypothesis = pytest.importorskip("hypothesis")
strategies = pytest.importorskip("hypothesis.strategies")

given = hypothesis.given
settings = hypothesis.settings

_SETTINGS = settings(
    derandomize=True, database=None, deadline=None, max_examples=200
)

#: Paths are generated as arbitrary text, NOT as well-formed POSIX paths: a
#: digest that is only injective over tidy inputs is not injective, and the
#: separator-collision case this is really hunting for lives in the untidy
#: ones.
_PATHS = strategies.text(max_size=12)
_OIDS = strategies.text("0123456789abcdef", min_size=1, max_size=8)
_ENTRIES = strategies.lists(strategies.tuples(_PATHS, _OIDS), max_size=6)


def _dedupe(pairs):
    """One path names one leaf, keyed the way the manifest itself keys it.

    (Round-1 review finding 3) This used to key on the generated RAW TEXT
    while `_manifest` keyed on `PurePosixPath(text)`, which normalizes --
    ``""`` and ``"."``, ``"a"`` and ``"a/"``, ``"a/b"`` and ``"a//b"`` are
    each one path but were two keys. The "identical content iff identical
    digest" property was therefore FALSE as stated, and green only because
    `derandomize=True` plus the default alphabet never reached those inputs:
    a latent failure that, when a Hypothesis upgrade found it, would have
    read as "the tree digest collides" and sent someone hunting a bug that
    does not exist. Normalizing here states the property over what is
    actually hashed.
    """
    return sorted({PurePosixPath(path): oid for path, oid in pairs}.items())


def _manifest(pairs):
    return isolation._Manifest(
        entries=tuple(
            isolation._Entry(
                path=path,
                mode=isolation._MODE_REGULAR,
                oid=oid,
                size=1,
            )
            for path, oid in _dedupe(pairs)
        ),
        directories=(),
        omitted=(),
    )


@given(first=_ENTRIES, second=_ENTRIES)
@_SETTINGS
def test_two_trees_digest_alike_if_and_only_if_their_content_is_alike(first, second):
    """The central property, in both directions -- and they fail differently.

    "Different content, same digest" is a stale verdict silently trusted:
    B088 itself, and the worst outcome a fix for it could have. "Same
    content, different digest" is a resume that never resumes -- visible,
    merely expensive, but it would quietly discard B066's entire point. An
    `is`-comparison of the two equalities catches either.
    """
    assert (
        isolation._manifest_sha256(_manifest(first))
        == isolation._manifest_sha256(_manifest(second))
    ) is (_dedupe(first) == _dedupe(second))


@given(pairs=_ENTRIES)
@_SETTINGS
def test_the_tree_digest_ignores_traversal_order(pairs):
    """Incidental factors must not reach the identity. Order of discovery is
    one; file mtime is another, and is structurally absent -- the manifest is
    built from Git objects and carries no timestamp for a digest to read."""
    entries = _manifest(pairs).entries
    shuffled = isolation._Manifest(
        entries=tuple(reversed(entries)), directories=(), omitted=()
    )
    assert isolation._manifest_sha256(
        isolation._Manifest(entries=entries, directories=(), omitted=())
    ) == isolation._manifest_sha256(shuffled)


@given(
    target=strategies.text(max_size=10),
    omitted=strategies.lists(strategies.text(max_size=10), max_size=3),
)
@_SETTINGS
def test_a_symlink_target_cannot_impersonate_another_manifest(target, omitted):
    """Round-1 review finding 4, generalized into a property.

    The first serialization joined fields with `\\0` and argued no field
    could contain one. That was wrong about exactly one field: a symlink
    target is blob content decoded as UTF-8, so it CAN contain `\\0` -- and
    the reviewer built two different manifests with one identical digest
    through it. Netstring encoding removes the assumption rather than
    restating it, and this generates the attack rather than pinning the one
    example that was found.
    """
    def build(symlink_target, omitted_paths):
        return isolation._Manifest(
            entries=(
                isolation._Entry(
                    path=PurePosixPath("link"),
                    mode=isolation._MODE_SYMLINK,
                    oid="a" * 40,
                    size=1,
                    target=symlink_target,
                ),
            ),
            directories=(),
            omitted=tuple(PurePosixPath(path) for path in omitted_paths),
        )

    reference = build("q", ())
    candidate = build(target, omitted)
    same = (target, sorted(str(PurePosixPath(p)) for p in omitted)) == ("q", [])
    assert (
        isolation._manifest_sha256(candidate) == isolation._manifest_sha256(reference)
    ) is same


@given(
    argv=strategies.lists(strategies.text(max_size=12), max_size=5),
    other=strategies.lists(strategies.text(max_size=12), max_size=5),
    tree=strategies.text(max_size=8),
)
@_SETTINGS
def test_two_judges_digest_alike_if_and_only_if_their_command_is_alike(
    argv, other, tree
):
    """The same property one level up, over the resolved command.

    Generated argv elements may contain the serialization's own separator,
    embedded newlines, or nothing at all -- a naive join would let
    `("a\\x00b",)` impersonate `("a", "b")`, and this is the property that
    refuses it.
    """
    from conftest import make_lane, make_plan

    def digest(elements):
        return judge_sha256(
            tree_sha256=tree, plan=make_plan(make_lane(argv=tuple(elements)))
        )

    assert (digest(argv) == digest(other)) is (list(argv) == list(other))
