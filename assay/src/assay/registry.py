"""The explicit adapter registry (A-097, O2): a lane's declared
``judge.language`` selects a :class:`~assay.adapters.base.LanguageAdapter`
by an EXACT name match against adapters a caller registered — never a
default, never a fallback, never an import-time side effect.

**Why a registry object, not a module-level dict.** A single process-global
mapping that every test mutates (``register(...)`` / forgets to unregister)
is exactly the shared-state hazard AUTHORING.md §3b.B warns about: a test
that registers a fake adapter under the name ``"python"`` would leak into
whichever test runs next on the same worker. :func:`new_registry` instead
builds a fresh, frozen :class:`Registry` from an explicit set of entries
supplied at the call site — there is nothing here for a test to forget to
clean up, and no import of this module can ever come pre-loaded with an
adapter.

This is also the mechanical guarantee behind O2's negative: there is no
``adapters/python.py`` import anywhere in this module, so an unrecognised
``language`` string cannot silently resolve to Python (or to anything) —
:func:`get_adapter` raises when the name is not in the registry it was
handed, full stop.

**P17's addition: a registered adapter names the rigor levels reachable
THROUGH IT, not just its own existence.** Shipping ``adapters/go.py`` proves
the Go adapter's own methods; it does not mean an installed ``assay`` build
actually evaluates Go at any rigor level yet (DESIGN-GUIDE §6/§7: a library
surface is not a product capability until a supported producer path reaches
it — the whole gap the post-series review found). So a :class:`RegistryEntry`
pairs one adapter with the CLOSED set of rigor levels THIS BUILD wires it
into, and a language/rigor pair the build does not wire is refused exactly
the same way an unknown language string is — never a guess, never "the
adapter exists so the level must work."

**This paragraph used to name the CLI's entries** ("naming Python at ``R1``
only, and no entry at all for Go"). It is rewritten rather than re-stated
because that copy went stale three times without anyone noticing: SQL was
registered at ``R2`` (A-242), JavaScript at ``R1`` then ``{"R1","R2"}``
(B036/B046) and Go at ``R1`` (A-394), and the sentence still said
Python-only until Wave C's round-1 adversarial review read it. The claim
this module actually needs does not depend on which entries exist:
whatever :func:`assay.cli._built_in_registry` names, it is a closed
declaration of what THIS BUILD reaches, not a list of the adapters that
happen to exist on disk. That function is the single authority; a second
copy of its contents here can only rot, so there no longer is one.

**External-tool preflighting lives in `assay.runner`, not here (P34/A-253).**
DESIGN-GUIDE §11 and A-013 both say a declared adapter prerequisite tool
that is absent from ``PATH`` should render ``NO_MEASUREMENT`` rather than
fail unpredictably mid-run. A-086 recorded this as a gap in the closed
reason-code vocabulary, and A-087 (P08, the Go adapter) declined to close it
there: Go shipped with ``external_tools = ()``, so "no missing-tool state
becomes reachable, so adding its reason would be decorative."

**A-087's premise is now false, and Wave C is what falsified it.**
:attr:`assay.adapters.go.GoAdapter.external_tools` is ``("go",)`` (B047
item 2), because A-217 rules out re-implementing ``cmd/cover``'s statement
segmentation in Python and so a Go R1 lane genuinely needs a real Go
toolchain to derive statement positions; A-394 then registered that adapter
in :func:`assay.cli._built_in_registry` at ``{"R1"}``. So the sentence this
paragraph used to end with — "every adapter a built-in registry can
advertise today (Python, Go, and P34's own SQL) declares
``external_tools = ()``, so no real entry here ever exercises one" — is
wrong in both halves: the set is no longer Python/Go/SQL, and EVERY real Go
lane exercises the preflight. ``assay lanes --json`` on a Go lane reports
``external_tools: ["go"]``, and in an environment without ``go`` on ``PATH``
(this devcontainer and the registered gate's own image, A-042/A-043) that
lane is refused ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` before its
command runs. The paragraph is rewritten rather than deleted because A-087's
REASONING is the load-bearing half: it explains why the machinery lives in
:mod:`assay.runner` instead of here, which is still true and is not what
went stale. Only its factual premise did. (The identical paragraph in
:func:`assay.cli._built_in_registry` was rewritten for the same reason when
A-394 landed; this copy was missed, and Wave C's round-1 adversarial review
found it — the A-399/A-400 shape, a true-when-written sentence quietly
becoming false, applied to a file the wave never had cause to open.)

**What has NOT changed** is that THIS module still adds no preflight
machinery of its own, and now for a stronger reason than "nothing exercises
it": :func:`assay.runner.run_lane` raises
``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` at the top of every run, for
ANY adapter a caller resolves through this module's :func:`get_adapter` —
general infrastructure that did not wait for a registered adapter to need
it, the same way the rest of `run_lane`'s pre-work refusals do not wait for
a lane that exercises them. Go arriving with a real prerequisite tool is the
first proof that the general shape was the right call; a preflight bolted
onto the registry would now have to be unbolted, or duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .adapters.base import LanguageAdapter
from .errors import AssayError, Outcome, ReasonCode

__all__ = ["Registry", "RegistryEntry", "get_adapter", "new_registry"]

#: Rigor levels a registered adapter may claim reachability for. R0 needs no
#: adapter at all (:mod:`assay.runner` never looks one up for it), so it is
#: deliberately excluded — an entry naming it would claim a capability the
#: registry mechanism has no way to exercise.
_ADAPTER_RIGOR_LEVELS: frozenset[str] = frozenset({"R1", "R2", "R3"})


@dataclass(frozen=True, kw_only=True)
class RegistryEntry:
    """One adapter, paired with the rigor levels THIS BUILD reaches through
    it — never the adapter's own theoretical capability, only what a real
    producer path (:mod:`assay.runner`, wired in by a specific package) has
    actually been connected to.
    """

    adapter: LanguageAdapter
    rigor: frozenset[str]

    def __post_init__(self) -> None:
        if not self.rigor:
            raise ValueError(
                f"registry entry for adapter {self.adapter.name!r} declares "
                f"no reachable rigor level; an entry that reaches nothing "
                f"should not be registered at all"
            )
        unknown = sorted(self.rigor - _ADAPTER_RIGOR_LEVELS)
        if unknown:
            raise ValueError(
                f"registry entry for adapter {self.adapter.name!r} declares "
                f"rigor {unknown}, not in {sorted(_ADAPTER_RIGOR_LEVELS)} — "
                f"R0 needs no adapter, so it is never a reachable level here"
            )


@dataclass(frozen=True, kw_only=True)
class Registry:
    """An immutable set of entries, keyed by :attr:`LanguageAdapter.name`.

    Never constructed directly outside this module — :func:`new_registry` is
    the only way to build one, so duplicate-name detection (below) cannot be
    bypassed by handing :class:`Registry` an already-broken mapping.
    """

    entries: Mapping[str, RegistryEntry]


def new_registry(*entries: RegistryEntry) -> Registry:
    """Build a fresh :class:`Registry` from explicit entries.

    Zero entries is a legal, useful registry — every :func:`get_adapter`
    call against it raises, which is exactly what a project that declared no
    language-bound rigor level needs. Raises :class:`ValueError` for two
    entries sharing a :attr:`~assay.adapters.base.LanguageAdapter.name`:
    silently letting the second shadow the first would make registration
    order an invisible input.
    """
    by_name: dict[str, RegistryEntry] = {}
    for entry in entries:
        name = entry.adapter.name
        if name in by_name:
            raise ValueError(
                f"two entries both declare adapter name {name!r}; adapter "
                f"names must be unique within a registry"
            )
        by_name[name] = entry
    return Registry(entries=MappingProxyType(by_name))


def get_adapter(registry: Registry, language: str, rigor: str) -> LanguageAdapter:
    """The adapter *registry* has registered under *language*, if *rigor* is
    among the levels THIS BUILD reaches through it.

    Raises :class:`~assay.errors.AssayError` (``ERROR``/``BAD_LANE_CONFIG``)
    for either failure, never a fallback: an unknown *language* (O2's
    negative: "an unknown declared language silently selects Python"), or a
    *language* this registry knows but has not wired for *rigor* (e.g.
    ``"go"`` at any rigor before P22, or ``"python"`` at ``R2``/``R3`` before
    P18/P19) — a real adapter existing is not the same claim as this build
    having a producer path to it (DESIGN-GUIDE §7).
    """
    entry = registry.entries.get(language)
    if entry is None:
        raise AssayError(
            f"{language!r} is not a language this registry knows; "
            f"declared adapters: {sorted(registry.entries)}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    if rigor not in entry.rigor:
        raise AssayError(
            f"{language!r} is registered, but this assay build has not "
            f"wired {rigor} evaluation for it; reachable here: "
            f"{sorted(entry.rigor)}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    return entry.adapter
