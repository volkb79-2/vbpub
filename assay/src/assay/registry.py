"""The explicit adapter registry (A-097, O2): a lane's declared
``judge.language`` selects a :class:`~assay.adapters.base.LanguageAdapter`
by an EXACT name match against adapters a caller registered — never a
default, never a fallback, never an import-time side effect.

**Why a registry object, not a module-level dict.** A single process-global
mapping that every test mutates (``register(...)`` / forgets to unregister)
is exactly the shared-state hazard AUTHORING.md §3b.B warns about: a test
that registers a fake adapter under the name ``"python"`` would leak into
whichever test runs next on the same worker. :func:`new_registry` instead
builds a fresh, frozen :class:`Registry` from an explicit set of adapters
supplied at the call site — there is nothing here for a test to forget to
clean up, and no import of this module can ever come pre-loaded with an
adapter.

This is also the mechanical guarantee behind O2's negative: there is no
``adapters/python.py`` import anywhere in this module, so an unrecognised
``language`` string cannot silently resolve to Python (or to anything) —
:func:`get_adapter` raises when the name is not in the registry it was
handed, full stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .adapters.base import LanguageAdapter
from .errors import AssayError, Outcome, ReasonCode

__all__ = ["Registry", "get_adapter", "new_registry"]


@dataclass(frozen=True, kw_only=True)
class Registry:
    """An immutable set of adapters, keyed by :attr:`LanguageAdapter.name`.

    Never constructed directly outside this module — :func:`new_registry` is
    the only way to build one, so duplicate-name detection (below) cannot be
    bypassed by handing :class:`Registry` an already-broken mapping.
    """

    adapters: Mapping[str, LanguageAdapter]


def new_registry(*adapters: LanguageAdapter) -> Registry:
    """Build a fresh :class:`Registry` from explicit adapters.

    Zero adapters is a legal, useful registry — every :func:`get_adapter`
    call against it raises, which is exactly what a project that declared no
    language-bound rigor level needs. Raises :class:`ValueError` for two
    adapters sharing a :attr:`~assay.adapters.base.LanguageAdapter.name`:
    silently letting the second shadow the first would make registration
    order an invisible input.
    """
    by_name: dict[str, LanguageAdapter] = {}
    for adapter in adapters:
        if adapter.name in by_name:
            raise ValueError(
                f"two adapters both declare name {adapter.name!r}; "
                f"adapter names must be unique within a registry"
            )
        by_name[adapter.name] = adapter
    return Registry(adapters=MappingProxyType(by_name))


def get_adapter(registry: Registry, language: str) -> LanguageAdapter:
    """The adapter *registry* has registered under *language*.

    Raises :class:`~assay.errors.AssayError` (``ERROR``/``BAD_LANE_CONFIG``)
    when *language* is not a name this registry knows — never a fallback to
    some "default" adapter (O2's negative names this exact hazard: "an
    unknown declared language silently selects Python").
    """
    try:
        return registry.adapters[language]
    except KeyError:
        raise AssayError(
            f"{language!r} is not a language this registry knows; "
            f"declared adapters: {sorted(registry.adapters)}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        ) from None
