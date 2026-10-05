"""Reusable argparse value types."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


class _AllSentinel:
    """The result of the ``all`` token when no explicit choices are known."""

    def __repr__(self) -> str:
        return "SelectorList.ALL"


_ALL = _AllSentinel()


class SelectorList:
    """Argparse ``type`` for ``all`` or a separator-delimited list of names.

    ``SelectorList(("a", "b"))("a,b")`` returns ``("a", "b")`` in the order
    given. The bare ``all`` token returns the full ``choices`` tuple, or
    :data:`SelectorList.ALL` when no choices were declared (the consumer
    resolves it against data only known at run time).
    """

    ALL = _ALL

    def __init__(
        self,
        choices: Sequence[str] | None = None,
        *,
        all_token: str = "all",
        separator: str = ",",
    ) -> None:
        if not isinstance(all_token, str) or not all_token:
            raise ValueError("all_token must be a non-empty string")
        if all_token != all_token.strip():
            raise ValueError("all_token must not have surrounding whitespace")
        if not isinstance(separator, str) or len(separator) != 1 or separator.isspace():
            raise ValueError("separator must be a single non-space character")
        if separator in all_token:
            raise ValueError("all_token must not contain the separator")
        names: tuple[str, ...] | None = None
        if choices is not None:
            if isinstance(choices, str):
                raise ValueError("choices must be a sequence of names, not one string")
            names = tuple(choices)
            if not names:
                raise ValueError("choices must be None or non-empty")
            for name in names:
                if not isinstance(name, str) or not name:
                    raise ValueError("choices must be non-empty strings")
                if name != name.strip():
                    raise ValueError(
                        f"choice {name!r} must not have surrounding whitespace"
                    )
                if name == all_token:
                    raise ValueError(f"choice {name!r} collides with the all token")
                if separator in name:
                    raise ValueError(f"choice {name!r} contains the separator")
            if len(set(names)) != len(names):
                raise ValueError("choices must be unique")
        self.choices = names
        self.all_token = all_token
        self.separator = separator

    def __call__(self, text: str) -> tuple[str, ...] | _AllSentinel:
        if text.strip() == self.all_token:
            return self.choices if self.choices else _ALL
        names = [item.strip() for item in text.split(self.separator)]
        if any(not name for name in names):
            raise argparse.ArgumentTypeError(f"empty selector item in {text!r}")
        if self.all_token in names:
            raise argparse.ArgumentTypeError(
                f"{self.all_token!r} cannot be combined with other names"
            )
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise argparse.ArgumentTypeError(f"duplicate selector {name!r}")
            seen.add(name)
        if self.choices is not None:
            for name in names:
                if name not in self.choices:
                    raise argparse.ArgumentTypeError(
                        f"unknown selector {name!r}; choose from "
                        f"{', '.join(self.choices)} or {self.all_token}"
                    )
        return tuple(names)

    def __repr__(self) -> str:
        return (
            f"SelectorList(choices={self.choices!r}, all_token={self.all_token!r}, "
            f"separator={self.separator!r})"
        )
