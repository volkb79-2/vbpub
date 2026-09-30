"""The two dataclass decorators every ``assay`` record uses (B129, I1).

Every dataclass under ``src/assay`` is declared with exactly one of these, so the
flags ``frozen=True`` / ``kw_only=True`` live in two places instead of eighty-one
(``tests/core/test_dataclass_contract.py`` pins the per-class result and the
``dataclass_transform`` values below).

* ``record``: frozen and keyword-only. The default for a record.
* ``positional_record``: frozen, positional construction allowed. Only for the
  classes that are constructed positionally (the ``config`` lane records,
  ``go_modfile.ModuleDeclaration`` and the two ``_Worst`` accumulators).

A core leaf module: stdlib imports only (``tests/core/test_trust_boundary.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar, dataclass_transform

_T = TypeVar("_T")


@dataclass_transform(frozen_default=True, kw_only_default=True, field_specifiers=(field,))
def record(cls: type[_T]) -> type[_T]:
    return dataclass(frozen=True, kw_only=True)(cls)


@dataclass_transform(frozen_default=True, field_specifiers=(field,))
def positional_record(cls: type[_T]) -> type[_T]:
    return dataclass(frozen=True)(cls)
