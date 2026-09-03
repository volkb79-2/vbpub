"""The closed mutation-operator vocabulary, in one cycle-safe leaf module.

P21 work item 2. Before this module the same four operator names lived in
three places that could drift independently: :data:`assay.mutation.
MUTATION_OPERATORS` (a ``frozenset``, so it carried no order),
``config._load_mutation``'s load-time cross-check (which reached it through a
function-body-local import to dodge a real ``config -> mutation -> config``
cycle), and the shipped JSON Schema's own ``mutation_operator`` enum. The
model and the raw verifier closed neither: :class:`~assay.verdict.
MutantOutcome.operator` and :class:`~assay.verdict.JudgmentR2.operators` both
accepted any non-empty string, so an artifact naming an operator no adapter
can produce was schema-invalid and model-valid at the same time.

This module exists to make that impossible by construction. It imports
NOTHING -- not even :mod:`assay.errors` -- so every layer that needs the
vocabulary can import it at module level without opening a cycle, which is
what removes the deferred-import workaround from :mod:`assay.config` and lets
:mod:`assay.verdict` close the vocabulary without importing
:mod:`assay.mutation`'s execution orchestration (that import direction is the
cycle A-114 originally cited as the reason the model could NOT close it).

The value is an ORDERED tuple, not a set. Order is part of the contract:
``judgment.r2.operators`` records the lane's own declared, order-preserving
selection, and the shipped schema's enum is required to list these exact
members in this exact order (``tests/test_verdict_schema_is_packaged.py``
asserts set equality AND order, so a hand-edited schema cannot drift from the
tuple below without a red test).

**P33/V5-2: the vocabulary is language-qualified and closed PER LANGUAGE.**
Through v4 it was one flat four-value list of bare Python operator names, so
nothing could express SQL's catalogue at all and a flat extension would have
let a Python lane declare ``drop-check``. v5 qualifies every name with its
language and closes each language separately, which makes a cross-language
declaration a load-time error rather than a run that reports operators it
could not possibly have applied.

The rename is a RENAME, not an alias (A-220/V5-2): ``compare-swap`` has no
accepted bare spelling anywhere -- not in config, not in the artifact, not
here. That absence is exactly what makes this a version bump rather than a
widening, and it is why a v4 artifact is refused on its version rather than
re-read under the new vocabulary.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "ADJUDICATED_EVIDENCE_KEYS",
    "COVERAGE_PRODUCERS_BY_FORMAT",
    "COVERAGE_PRODUCER_REQUIRED_FORMATS",
    "STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE",
    "ARC_BEARING_COVERAGE_PRODUCERS",
    "REFUSED_COVERAGE_PRODUCERS",
    "INGESTED_OPERATOR_NAMESPACES",
    "INGESTED_OPERATOR_RE",
    "is_ingested_operator",
    "MUTATION_OPERATORS",
    "MUTATION_OPERATORS_BY_LANGUAGE",
    "WITHDRAWN_MUTATION_OPERATORS",
    "operator_language",
]

#: Operators that are still SPELLABLE in a schema-v7 artifact but that no
#: lane may declare and no adapter produces (B034/A-326).
#:
#: B015's two "semantic" Python families shipped in 2.3.0 and were measured
#: in the 2.1.0->2.3.0 review-gap audit to be a byte-identical SUBSET of
#: ``python:compare-swap``'s own output: same span, same replacement bytes,
#: 87 sites over ``src/assay/**.py``, zero of them new. Co-selecting them
#: with ``compare-swap`` emitted every shared site twice, and the enum
#: predicate matched any ``name.attr`` access rather than an enum member.
#: They are withdrawn from the PRODUCER and from lane DECLARATION.
#:
#: **A-326's deferral is DISCHARGED here (A-331).** A-326 kept both names in
#: :data:`MUTATION_OPERATORS_BY_LANGUAGE` and in the packaged schema's
#: ``oneOf`` for exactly one reason -- released ``assay verify`` builds
#: ACCEPT a v7 document naming either operator, and released ``assay run``
#: builds EMITTED such documents, so deleting the spellings mid-v7 would have
#: stopped real artifacts from verifying -- and said in as many words that
#: "the spelling therefore stays until the next bump, where it is dropped".
#: B035 IS that bump: under v8 every v7 document is already refused on
#: ``schema_version`` alone, so the compatibility the deferral was buying is
#: gone and the deletion now costs nothing it did not already cost.
#:
#: This set therefore survives the deletion, but its members are no longer a
#: subset of :data:`MUTATION_OPERATORS`. That is the point: a consumer who
#: still has ``python:enum-comparison-swap`` in a lane file must get the
#: named "withdrawn, and here is why" refusal rather than a bare "unknown
#: operator", so ``config._load_mutation`` tests this set BEFORE it tests
#: catalogue membership.
WITHDRAWN_MUTATION_OPERATORS: frozenset[str] = frozenset(
    {"python:uuid-equality-swap", "python:enum-comparison-swap"}
)

#: The closed per-language mutation catalogue (A-112/A-114/A-221). Ordered,
#: and the order is normative for the shipped schema's own per-language
#: ``oneOf`` branches.
#:
#: * ``python`` -- the original four, adopted verbatim from
#:   ``/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`` and
#:   DESIGN-GUIDE §11's own TOML example, now qualified. B015 added two
#:   further "semantic" families, ``python:uuid-equality-swap`` and
#:   ``python:enum-comparison-swap``; both were **withdrawn** (B034/A-326),
#:   and at the v7->v8 cut their SPELLINGS are gone too (A-331) -- see
#:   :data:`WITHDRAWN_MUTATION_OPERATORS`. Python is the original four again,
#:   in the original order.
#: * ``go`` (A-221) -- three faithful analogues of the Python catalogue,
#:   transcribed under A-112 rather than invented. There is deliberately NO
#:   ``falsy-swap`` analogue: Python's exploits duck-typed truthiness, while
#:   every Go condition is a strict ``bool``, so a ``nil``-swap or zero-value
#:   operator would be new, unmeasured mutation-testing design. Arithmetic
#:   swap, increment/decrement and statement removal are excluded for the
#:   same reason. **Declarable but unproducible in this build (A-225):** no
#:   adapter generates them and ``cli._built_in_registry`` registers Python
#:   only, so a Go R2 lane is refused ``ERROR``/``BAD_LANE_CONFIG`` before
#:   anything executes. P29 lands the producer.
#: * ``sql`` (A-220) -- the seven DDL operators v5 exists to make
#:   expressible. ``weaken-delete-action`` names the operator CLASS
#:   (``RESTRICT``->``CASCADE``, ``RESTRICT``->``NO ACTION``) rather than one
#:   instance, which keeps the catalogue honest and finite. Also declarable
#:   but unproducible here; P34 lands the adapter.
MUTATION_OPERATORS_BY_LANGUAGE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "python": (
            "python:compare-swap",
            "python:boolop-swap",
            "python:bool-const-flip",
            "python:falsy-swap",
        ),
        "go": (
            "go:compare-swap",
            "go:boolop-swap",
            "go:bool-const-flip",
        ),
        "sql": (
            "sql:drop-check",
            "sql:drop-unique",
            "sql:drop-not-null",
            "sql:drop-foreign-key",
            "sql:weaken-delete-action",
            "sql:drop-trigger",
            "sql:widen-check-in",
        ),
    }
)

#: Every qualified operator name, in schema order (python, then go, then
#: sql). This is the whole closed catalogue -- membership here says a name is
#: spellable, NOT that the lane declaring it may use it. The
#: prefix-equals-resolved-language rule is a cross-object relation and lives
#: in :mod:`assay.config` (at load) and in the model and raw verifier (for an
#: artifact), which is where the language is actually known.
MUTATION_OPERATORS: tuple[str, ...] = tuple(
    operator
    for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()
    for operator in operators
)


#: (B045, schema v9) The closed coverage-PRODUCER vocabulary, keyed by the
#: format the producer writes. A producer is the toolchain that WROTE a
#: coverage artifact; a format is the document shape it wrote. Through v8
#: assay knew only the second, and `coverage-istanbul-json` is one format
#: several producers disagree about -- about what `branchMap` MEANS (A-344)
#: and about whether a line ran at all (A-346). B038 and B040 exist to force
#: exactly this: the producer becomes a DECLARED fact.
#:
#: **Declared, never sniffed (A-007).** Deriving the producer from the
#: artifact's own shape ("every `branchMap` entry is typed `branch` with one
#: location") is the declaration-versus-sniffing collapse
#: `coverage.py`'s module docstring forbids, and it would already have broken
#: between the two Vitest majors measured in B040 (Vitest 4's v8 provider
#: emits multi-line extents where Vitest 3's emitted single-line ones).
#:
#: **A per-producer FORMAT was rejected** (B045): registering
#: `coverage-istanbul-json-v8` as a second format name would bind a TRUST
#: property to a format name, and the identical document from nyc, Jest-babel
#: and `@vitest/coverage-istanbul` would then need three names for one shape.
#:
#: A format absent from this table -- `lcov`, `cobertura` -- has NO open
#: producer vocabulary, and declaring `producer` on such a lane is refused
#: rather than accepted-and-ignored. That is DESIGN-GUIDE §5's "no
#: speculative names" applied literally: a vocabulary opens when a consumer
#: needs it and can say what each name MEANS, not in advance.
#:
#: **`go-cover` was in that list until Wave C, and A-398 is why it left.**
#: A-354 closed it deliberately and flagged itself as "the B045 call most
#: open to challenge": B045's contract text listed `go-test`/`covdata`, but
#: shipping them in a build with no Go lane would have been a vocabulary
#: nothing could produce, check or explain. The Go wave is the condition
#: A-354 named for reopening it, and it has now been met -- `go` is
#: registered (A-394), so a lane declaring one of these names is a lane this
#: build actually runs.
COVERAGE_PRODUCERS_BY_FORMAT: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        # The babel-plugin-istanbul family (nyc/istanbul, Jest's default
        # `babel` provider, `@vitest/coverage-istanbul`, `vite-plugin-
        # istanbul`) share ONE instrumenter, so they are one producer for
        # every purpose assay has: they agree about `branchMap` (real arcs,
        # one location per arm) and they were measured correct on every
        # case in `probe-js-provider-defect`. The three v8-remapping
        # producers are SPELLABLE so the refusal can name them and say why
        # -- see `REFUSED_COVERAGE_PRODUCERS`.
        "coverage-istanbul-json": ("istanbul", "vitest-v8", "jest-v8", "c8"),
        # One format, one producer. Optional rather than required for that
        # exact reason: there is no second producer to disagree with, so an
        # omitted value cannot be a silently wrong answer -- but a DECLARED
        # value must still be the right one, so the name is closed here
        # rather than accepted free-form.
        "coverage-py-json": ("coverage.py",),
        # (B047 item 3 / A-398) Two producers of ONE format, and the fact
        # that they do not disagree is why `go-cover` is NOT added to
        # `COVERAGE_PRODUCER_REQUIRED_FORMATS` below:
        #
        #   `go-test`  -- `go test -coverprofile=<f>`, the unit-test path.
        #   `covdata`  -- `go tool covdata textfmt -i=<GOCOVERDIR> -o=<f>`
        #                 over the binary counter data a `go build -cover`
        #                 binary writes at run time. This is the INTEGRATION
        #                 path: it measures a real process doing real work
        #                 (an S3 lane), not a test binary.
        #
        # Both emit `cmd/cover`'s own textfmt -- the same `mode:` header and
        # the same `file:startLine.startCol,endLine.endCol numStmts count`
        # records, produced by the same instrumenter. `covdata textfmt`
        # converts a counter format into that text; it does not define a
        # second one. So assay's parser is producer-independent here, and
        # (unlike `coverage-istanbul-json`, where `vitest-v8` and `istanbul`
        # genuinely disagree about whether a line ran) there is no reading
        # of the artifact that depends on which name is declared.
        #
        # The names are still CLOSED rather than free-form, and still worth
        # having, because they record HOW the evidence was obtained -- which
        # is a real provenance difference a reviewer cares about even when
        # the bytes are interchangeable: `covdata` evidence can cover code no
        # unit test executes.
        "go-cover": ("go-test", "covdata"),
    }
)

#: (B045) The formats for which `judge.coverage.producer` is REQUIRED, not
#: merely permitted. `coverage-istanbul-json` is required because its
#: producers DISAGREE: no implied value is correct in every context, which is
#: precisely DESIGN-GUIDE §5's test for when a default is a hazard rather
#: than a policy. Every other format's key is optional.
COVERAGE_PRODUCER_REQUIRED_FORMATS: frozenset[str] = frozenset(
    {"coverage-istanbul-json"}
)

#: (A-406, DA-R1) Languages whose adapter declares
#: ``requires_statement_attribution``, mapped to the coverage formats that
#: CARRY the block extents such an adapter is judged from. A language absent
#: from this mapping places no constraint on ``judge.coverage.format``;
#: `assay.config` refuses at LOAD time when a language present here declares
#: a format that is not in its set.
#:
#: **Why the fact lives here and not on the adapter.** The check has to run at
#: config load, and :mod:`assay.config` must not import :mod:`assay.adapters`
#: -- "there is no ``adapters/python.py`` import anywhere in this module" is
#: :mod:`assay.registry`'s own mechanical guarantee behind O2, and dragging
#: the registry into the lane-file loader to answer one question would trade
#: that guarantee for a convenience. :mod:`assay.vocabulary` is where
#: language-scoped and format-scoped facts already live
#: (``MUTATION_OPERATORS_BY_LANGUAGE`` one field over is the same shape), and
#: `config` already imports it. The cost is that this mapping and
#: :attr:`assay.adapters.base.LanguageAdapter.requires_statement_attribution`
#: are two statements of one fact, so they are checked against each other by
#: a test that derives the languages from the built-in registry rather than
#: naming them: ``tests/test_config_statement_attribution_format.py``.
#:
#: **Why it exists at all.** ``judge.language`` and ``judge.coverage.format``
#: are independent by design, so a Go lane could declare ``format = "lcov"``.
#: lcov CONVERTED from a Go coverprofile (the `gcov2lcov` family) carries the
#: naive block expansion -- signatures, closing braces and ``case`` labels as
#: executable lines -- which is exactly the over-approximation A-392 exists to
#: refuse, and it arrives carrying no block extents at all, so nothing
#: downstream could tell it from statement truth. Found by adversarial review
#: round 1 as a reachable hole in the vacuous-attribution branch; DA-R1 ruled
#: that the lane is refused at load, before anything runs.
STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE: Mapping[str, frozenset[str]] = (
    MappingProxyType({"go": frozenset({"go-cover"})})
)

#: (B004/A-430) The closed set of registered Tier-2 adjudicator keys a lane
#: may declare under ``judge.evidence[].key`` when ``source = "adjudicated"``.
#: :mod:`assay.config` checks a declared key against this set at LOAD time.
#:
#: **Why the fact lives here and not in :mod:`assay.adjudication` itself,
#: which owns the real registry (:data:`assay.adjudication.ADJUDICATORS`).**
#: :mod:`assay.verdict` imports FROM :mod:`assay.config` (for
#: `LANE_SCHEMA_VERSION`/`JudgeConfig`), and :mod:`assay.adjudication` imports
#: FROM :mod:`assay.verdict` (for `Evidence`/`EvidenceDeclaration`) the same
#: way :mod:`assay.attestation` already does one tier over -- so
#: `config -> adjudication -> verdict -> config` is a real cycle, not merely
#: an undesirable one, the moment `config.py` imports `adjudication.py`
#: directly. `assay.vocabulary` imports nothing (a true leaf, exactly as
#: `STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE` above already relies on), so
#: it is the one module both `config.py` and `adjudication.py` can import
#: without opening that cycle. Precisely DA-R1's shape ("the check has to run
#: at config load, and `assay.config` must not import" the module that owns
#: the real registry) applied to a different registry and a different reason
#: (an import CYCLE here, rather than DA-R1's `assay.adapters`-specific O2
#: guarantee) for the same resolution.
#:
#: The cost, exactly as `STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE`'s own
#: comment states it: this set and `assay.adjudication.ADJUDICATORS` are two
#: statements of one fact and can drift, so a test derives one from the other
#: and asserts equality (``tests/test_adjudication_registry.py``).
ADJUDICATED_EVIDENCE_KEYS: frozenset[str] = frozenset({"image-provenance"})

#: (B045/B038(a)) The producers whose `branchMap` really is a set of ARCS --
#: one location and one count per branch ARM -- and can therefore answer
#: `branch_capability = "reported"`. Every other producer of the same format
#: keeps `branches = None` and `"unavailable"`, which is A-344's measured
#: refusal, not an omission: `@vitest/coverage-v8` emits one location and one
#: count per branch RECORD describing v8's own executed ranges, so a single
#: translation cannot be honest for both.
ARC_BEARING_COVERAGE_PRODUCERS: frozenset[str] = frozenset({"istanbul"})

#: (B045/B040(b)) Producers that are SPELLABLE -- so the refusal can name the
#: producer and its reason rather than reporting an unknown value -- but that
#: no lane may declare. The value is the reason, quoted into the loader's own
#: message. This is `WITHDRAWN_MUTATION_OPERATORS`' pattern one field over,
#: and for the same reason: "that is not a known producer" is a much weaker
#: message than "that producer is known, measured, and unsound; here is the
#: fix".
#:
#: `vitest-v8` is refused on MEASURED evidence (A-346/B040): over
#: `probe-js-provider-defect`, five functions whose every line below a guard
#: provably never runs, it reports those lines as EXECUTED whenever a ternary
#: appears earlier in the same block -- PASS at 100.0% where
#: `@vitest/coverage-istanbul` correctly FAILs at 0.0%. It reproduces on both
#: released Vitest majors, a one-line ternary triggers it, and
#: `experimentalAstAwareRemapping` does not fix it.
#:
#: `jest-v8` and `c8` are refused on a DIFFERENT and weaker ground, and the
#: two grounds are deliberately not blurred: they remap v8 ranges through the
#: same layer. For `c8` this is now measured, not assumed (B042 item 2,
#: `probe-js-provider-defect-c8/`); `jest-v8` remains unmeasured and is
#: refused as unproven rather than as proven-defective. Either way a
#: consumer gets a refusal naming the fix instead of a green verdict over
#: coverage nothing has qualified.
REFUSED_COVERAGE_PRODUCERS: Mapping[str, str] = MappingProxyType(
    {
        "vitest-v8": (
            "'@vitest/coverage-v8' reports never-executed lines as executed "
            "when a ternary appears earlier in the same block (A-346/B040, "
            "measured on both released Vitest majors over "
            "tests/fixtures/coverage/probe-js-provider-defect); a lane "
            "gating on it can PASS at 100% over code that never ran. Fix: "
            "set `provider: 'istanbul'` in the Vitest coverage config, "
            "install @vitest/coverage-istanbul, and declare "
            "producer = \"istanbul\""
        ),
        "jest-v8": (
            "Jest's `coverageProvider: \"v8\"` remaps v8 ranges through the "
            "same layer @vitest/coverage-v8 does and has NOT been measured "
            "against a committed witness (B042 item 2), so assay will not "
            "gate on it. Fix: use Jest's default `coverageProvider: "
            "\"babel\"`, which shares istanbul's instrumenter, and declare "
            "producer = \"istanbul\""
        ),
        "c8": (
            "`c8` remaps v8 ranges the same way and reproduces the same "
            "false greens (B042 item 2, measured: "
            "tests/fixtures/coverage/probe-js-provider-defect-c8/). Fix: "
            "instrument with nyc/istanbul or "
            "@vitest/coverage-istanbul and declare producer = \"istanbul\""
        ),
    }
)

#: (B046, schema v9) Namespaces under which an INGESTED R2 producer's own
#: mutator names are admitted as operator identities. A foreign tool's
#: mutator taxonomy is DATA, not assay's closed catalogue: Stryker alone
#: emitted nine distinct `mutatorName` values in the single committed real
#: run (`tests/fixtures/mutation/PROVENANCE.md`), none of them one of assay's
#: native operator names, and the set grows with the tool rather than with
#: assay. Mapping them onto `MUTATION_OPERATORS` would be a lie about which
#: mutation actually ran; leaving them unqualified would let a foreign name
#: collide with a native one.
#:
#: The namespace is therefore a PREFIX assay owns and the tool's name is the
#: suffix, verbatim. `INGESTED_OPERATOR_RE` is the shipped predicate and is
#: normative for the schema's own `mutation_operator` pattern branch: no
#: assay-native name can match it (every native name's prefix is a LANGUAGE,
#: and no language is named `stryker`), and no ingested name can be confused
#: for a native one.
INGESTED_OPERATOR_NAMESPACES: tuple[str, ...] = ("stryker",)

#: The pattern the schema's `mutation_operator` gains as a v9 branch. Kept
#: here so the schema, the model and the parser cannot drift -- the same
#: single-owner discipline `MUTATION_OPERATORS` already gets.
INGESTED_OPERATOR_RE = re.compile(
    r"^(?:" + "|".join(INGESTED_OPERATOR_NAMESPACES) + r"):[A-Za-z0-9]+$"
)


def is_ingested_operator(operator: str) -> bool:
    """True iff *operator* is an ingested producer's namespaced mutator name.

    Derived from :data:`INGESTED_OPERATOR_RE` rather than by re-splitting on
    ``:``, so the model, the config loader and the schema all answer this
    question with one implementation.
    """
    return bool(INGESTED_OPERATOR_RE.fullmatch(operator))


def operator_language(operator: str) -> str | None:
    """The language *operator* is qualified with, or ``None`` if it carries
    no ``<language>:`` prefix at all.

    Derived by splitting rather than by table lookup, so an operator this
    module does not know still reports the language it CLAIMS -- which is
    what lets a caller say "a python lane cannot declare ``sql:drop-check``"
    instead of the much weaker "that is not a known operator".
    """
    language, separator, _ = operator.partition(":")
    if not separator or not language:
        return None
    return language
