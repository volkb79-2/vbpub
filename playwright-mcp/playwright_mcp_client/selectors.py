from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple


@dataclass(frozen=True)
class LayoutSelectors:
    required: Tuple[str, ...]
    optional: Tuple[str, ...] = ()

    def as_list(self) -> list[str]:
        return list(self.required)


def default_layout_selectors() -> LayoutSelectors:
    return LayoutSelectors(required=("nav", "main", "footer"))


def validate_selectors(selectors: LayoutSelectors) -> None:
    if not selectors.required:
        raise ValueError("Layout selectors require at least one required selector")
    for selector in selectors.required:
        if not selector or not selector.strip():
            raise ValueError("Layout selectors cannot include empty selectors")


def merge_selectors(*groups: Iterable[str]) -> LayoutSelectors:
    merged: list[str] = []
    for group in groups:
        for selector in group:
            merged.append(selector)
    return LayoutSelectors(required=tuple(merged))
