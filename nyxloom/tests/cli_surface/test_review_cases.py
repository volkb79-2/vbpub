"""Parser-only probes for every reviewed Nyxloom CLI grammar candidate."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from nyxloom.cli_registry import primary_cli

PROJECT = Path(__file__).resolve().parents[2]
SURFACE_PATH = PROJECT / "docs" / "cli-surface.json"
REVIEW_PATH = PROJECT / "docs" / "cli-review.toml"
SURFACE = json.loads(SURFACE_PATH.read_text(encoding="utf-8"))
CANDIDATES = {item["id"]: item for item in SURFACE["candidates"]}
ROUTES = {item["id"]: item for item in SURFACE["routes"]}
CLI = primary_cli()


def _test_id(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]


def _catalog_cases() -> list[object]:
    review = tomllib.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    cases = [item for item in review.get("cases", ()) if item.get("state") == "active"]
    case_ids = {item["id"] for item in cases}
    missing = sorted(set(CANDIDATES) - case_ids)
    stale = sorted(case_ids - set(CANDIDATES))
    assert not missing and not stale, (
        f"CLI semantic catalog does not match generated surface; "
        f"missing={missing[:3]!r}, stale={stale[:3]!r}"
    )
    assert len(cases) == len(case_ids), "CLI semantic catalog repeats case IDs"
    return [
        pytest.param(
            case,
            id=_test_id(case["id"]),
            marks=pytest.mark.cli_case(case["id"]),
        )
        for case in sorted(cases, key=lambda item: item["id"])
    ]


def _action_for(candidate: dict, route: dict) -> dict:
    action_id = candidate["shape"].get("option_id") or candidate["shape"].get("argument_id")
    if action_id is None and candidate["members"]:
        action_id = candidate["members"][0]
    return next(action for action in route["actions"] if action["id"] == action_id)


def _parse_route(invocation: list[str], route_path: list[str]):
    app = CLI
    depth = 0
    while depth < len(route_path) - 1 and route_path[depth] in app.delegates:
        app = app.delegates[route_path[depth]]
        depth += 1
    return app.parse_args(invocation[depth:])


@pytest.mark.parametrize("case", _catalog_cases())
def test_reviewed_parser_case(case: dict) -> None:
    """Accept each reviewed minimum, spelling, choice, and positional shape."""
    candidate = CANDIDATES[case["id"]]
    route = ROUTES[candidate["route_id"]]
    parsed = _parse_route(case["invocation"], route["path"])
    assert parsed.verb == route["path"][-1]

    if candidate["kind"] in {"option-choice", "argument-choice"}:
        action = _action_for(candidate, route)
        actual = getattr(parsed, action.get("dest", action.get("name")))
        expected = candidate["shape"]["choice"]
        assert actual == expected or expected in actual
    elif candidate["kind"] == "argument-shape":
        action = _action_for(candidate, route)
        actual = getattr(parsed, action["name"])
        if action.get("nargs") in {"*", "+"}:
            assert isinstance(actual, list) and len(actual) >= max(1, action["minimum_values"])
        else:
            assert actual is not None
