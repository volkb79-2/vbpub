#!/usr/bin/env python3
"""Write ``docs/cli-review.toml`` from the exported surface (``docs/cli-surface.json``).

Every candidate the surface exporter lists (minimum syntax, argument shapes, option spellings
and choices, exclusive members and conflicts, declared constraints) becomes ONE active
catalog row with a concrete invocation. The outcome recorded on the row is the one the REAL
registry produced when that invocation ran through ``tests/cli_review_harness.py``, so the
catalog is a CHARACTERIZATION of the grammar: it pins spellings, accepted value shapes, and
the refusals the shared parser or a verb's own input validation produces. The behaviour
*behind* a verb is pinned by that verb's own test module (see ``docs/SPEC.md``, "Verb
semantics beyond the catalog").

Rows are keyed by the exporter's case id and signature, so an unchanged case keeps its row.
Re-run after ``cli-extended surface sync`` whenever the grammar changes:

    cd fleetd && PYTHONPATH=src:tests python3 tools/gen-cli-review-catalog.py
    cli-extended surface sync && cli-extended surface check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import conftest  # noqa: E402,F401  -- publishes this checkout's stand-in distribution metadata
import cli_review_harness as harness  # noqa: E402

MANIFEST = ROOT / "docs" / "cli-surface.json"
CATALOG = ROOT / "docs" / "cli-review.toml"
TEST_FILE = "tests/test_cli_review_cases.py"

RATIONALE = {
    "minimum": "The route's minimum executable syntax is accepted and reaches its handler or its own input validation.",
    "argument-shape": "The positional is accepted in the shape the registration declares.",
    "argument-choice": "A declared choice of the positional is accepted.",
    "option-spelling": "The option is accepted in exactly this spelling (no prefix abbreviation).",
    "option-choice": "A declared choice of the option is accepted.",
    "exclusive-member": "One member of a mutually exclusive group is accepted alone.",
    "exclusive-conflict": "Two members of one exclusive group are refused by the shared parser before any handler runs.",
    "constraint-requires": "An option used without the option its declared Requires rule names is refused before any handler runs.",
    "constraint-conflict": "Two options a declared Conflicts rule forbids together are refused before any handler runs.",
}

# Per-route input vocabulary, so a row's recorded outcome does not depend on the machine that
# generated it. Values are harness tokens (`@<fixture in tests/fixtures>`). A forced option is
# ALWAYS supplied by `base()`: `facts collect` without `--root` would read the generating
# machine's `/etc/fleetd/facts.toml` and its real `/proc`, which is exactly the host-dependence a
# reviewed case must not have.
ROUTE_REQUIRED_POSITIONALS = {
    "route:entrypoint:fleet/facts/validate": "@host-facts.valid.json",
    "route:entrypoint:fleet/guard/validate": "@guard.valid.toml",
    "route:entrypoint:fleet/run": "@invalid-plan.json",
}
ROUTE_FORCED_OPTIONS = {
    "route:entrypoint:fleet/facts/collect": {"--root": "@facts-root"},
    "route:entrypoint:fleet/plan": {
        "--candidates": "@candidate-table.valid.json",
        "--hosts": "@fleet-host-set.valid.json",
        "--demand": "@lane-demand.r2.toml",
    },
}
# The recorded stdout fragment for a route that finishes successfully on fixture inputs.
ROUTE_STDOUT = {
    "route:entrypoint:fleet/facts/validate": "valid host-facts/1",
    "route:entrypoint:fleet/guard/validate": "valid guard.toml",
}

EFFECTS_SUCCESS = (
    "Completed in the sandbox against its throw-away HOME and the fixture inputs named in the "
    "invocation; no host was contacted and nothing outside the sandbox was changed."
)
EFFECTS_LIBRARY_CHECK = (
    "The library-owned skills check runs for real in the sandbox and reports that the throw-away "
    "HOME holds no current skill (exit 1); nothing outside the sandbox is touched."
)
EFFECTS_REFUSED = (
    "Refused by the shared parser, a declared constraint or the verb's own input validation before "
    "any host is contacted; no campaign state and no file outside the sandbox is changed."
)


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    routes = {route["id"]: route for route in manifest["routes"]}
    actions = {
        action["id"]: action for route in routes.values() for action in route["actions"]
    }

    def action_for(option_id: str) -> dict:
        found = actions.get(option_id)
        if found is not None:
            return found
        # A library-owned control (--json, --dry-run, --debug-raw) has no route action.
        return {"canonical": option_id.rsplit("/", 1)[1], "minimum_values": 0,
                "kind": "option", "scope": "global", "choices": None, "type": None,
                "exclusive_group": None}

    def value_tokens(route: dict, action: dict, choice: str | None = None) -> list[str]:
        if not action.get("minimum_values"):
            return []
        if choice is not None:
            return [choice]
        route_values = ROUTE_FORCED_OPTIONS.get(route["id"], {})
        if action["canonical"] in route_values:
            return [route_values[action["canonical"]]]
        if action.get("choices"):
            return [str(action["choices"][0])]
        converter = (action.get("type") or {}).get("callable", "")
        return ["1"] if converter.endswith(("int", "float")) else ["x"]

    def with_requirements(route: dict, tokens: list[str], chosen: list[str]) -> list[str]:
        by_flag = {a["canonical"]: a for a in route["actions"] if a.get("canonical")}
        queue, seen = list(chosen), set(chosen)
        while queue:
            current = queue.pop(0)
            for rule in route["constraints"]:
                if rule["kind"] == "requires" and rule["option"] == current:
                    need = rule["any_of"][0]
                    if need not in seen and need in by_flag:
                        tokens += [need, *value_tokens(route, by_flag[need])]
                        seen.add(need)
                        queue.append(need)
        return tokens

    def base(
        route: dict,
        skip_groups: frozenset[str] = frozenset(),
        skip_flags: frozenset[str] = frozenset(),
    ) -> list[str]:
        """The route's minimum invocation with every required input supplied."""
        tokens = list(route["path"])
        for action in route["actions"]:
            if action["kind"] == "argument" and action["required"]:
                tokens.append(ROUTE_REQUIRED_POSITIONALS.get(
                    route["id"], str((action.get("choices") or ["x"])[0])))
        taken_groups: set[str] = set()
        for action in route["actions"]:
            if action["kind"] != "option" or action["scope"] == "global":
                continue
            group = action.get("exclusive_group")
            required = action.get("required") or (group and action.get("exclusive_required"))
            if not required or action["canonical"] in skip_flags or group in skip_groups:
                continue
            if group:
                if group in taken_groups:
                    continue
                taken_groups.add(group)
            tokens += [action["canonical"], *value_tokens(route, action)]
        forced = ROUTE_FORCED_OPTIONS.get(route["id"], {})
        supplied = {token for token in tokens if token.startswith("--")}
        for flag, value in forced.items():
            if flag not in supplied and flag not in skip_flags:
                tokens += [flag, value]
        chosen = [token for token in tokens if token.startswith("--")]
        return with_requirements(route, tokens, chosen)

    def append_option(route: dict, argv: list[str], flag: str, action: dict,
                      choice: str | None = None) -> list[str]:
        if flag in ROUTE_FORCED_OPTIONS.get(route["id"], {}):
            return [*argv, flag, ROUTE_FORCED_OPTIONS[route["id"]][flag]]
        return [*argv, flag, *value_tokens(route, action, choice)]

    rows: list[str] = [
        "schema_version = 1",
        'cli_id = "fleet"',
        "",
        "# Generated by tools/gen-cli-review-catalog.py from docs/cli-surface.json: a",
        "# CHARACTERIZATION catalog, not a wish list. Each row's invocation runs through the real",
        "# registry in tests/cli_review_harness.py and the recorded exit status is what the registry",
        "# produced: it pins the GRAMMAR (spellings, shapes, choices, exclusive groups, declared",
        "# constraints) and the input refusals that precede any host contact. Behaviour behind a",
        "# verb lives in that verb's own test module (`docs/SPEC.md`, 'Verb semantics beyond the",
        "# catalog').",
        "",
    ]
    for candidate in manifest["candidates"]:
        route = routes[candidate["route_id"]]
        shape, kind = candidate["shape"], candidate["kind"]
        if kind in ("minimum", "argument-shape", "argument-choice"):
            argv = base(route)
            if kind != "minimum":
                action = actions[shape["argument_id"]]
                if not action["required"]:
                    argv.append(str(shape.get("choice") or (action.get("choices") or ["x"])[0]))
        elif kind in ("option-spelling", "option-choice"):
            action = action_for(shape["option_id"])
            group = action.get("exclusive_group")
            argv = base(
                route,
                skip_groups=frozenset([group]) if group else frozenset(),
                skip_flags=frozenset([action["canonical"]]),
            )
            argv = append_option(
                route, argv, shape.get("spelling") or action["canonical"], action, shape.get("choice"))
            argv = with_requirements(route, argv, [action["canonical"]])
        elif kind == "exclusive-member":
            action = actions[shape["selected_option"]]
            group = action.get("exclusive_group")
            argv = base(route, skip_groups=frozenset([group]) if group else frozenset())
            argv += [action["canonical"], *value_tokens(route, action)]
            argv = with_requirements(route, argv, [action["canonical"]])
        elif kind == "exclusive-conflict":
            first, second = (actions[option_id] for option_id in shape["options"][:2])
            group = first.get("exclusive_group")
            argv = base(route, skip_groups=frozenset([group]) if group else frozenset())
            for action in (first, second):
                argv += [action["canonical"], *value_tokens(route, action)]
        elif kind == "constraint-requires":
            by_flag = {a["canonical"]: a for a in route["actions"] if a.get("canonical")}
            action = by_flag[shape["option"]]
            argv = base(route, skip_flags=frozenset([shape["option"], *shape["any_of"]]))
            argv += [shape["option"], *value_tokens(route, action)]
        elif kind == "constraint-conflict":
            by_flag = {a["canonical"]: a for a in route["actions"] if a.get("canonical")}
            argv = base(route, skip_flags=frozenset(shape["options"]))
            for flag in shape["options"][:2]:
                argv += [flag, *value_tokens(route, by_flag[flag])]
        else:  # pragma: no cover - a new exporter kind needs a generator rule
            raise SystemExit(f"unhandled candidate kind {kind!r}")

        status, _stdout, stderr = harness.run(argv)
        stdout_fragment = ROUTE_STDOUT.get(route["id"], "") if status == 0 else ""
        stderr_fragment = "[ERROR]" if "[ERROR]" in stderr else ""
        effects = (
            EFFECTS_REFUSED if status == 2
            else EFFECTS_LIBRARY_CHECK if status == 1
            else EFFECTS_SUCCESS
        )
        rows += [
            "[[cases]]",
            f"id = {_q(candidate['id'])}",
            'state = "active"',
            f'decision = "{"refuse" if status == 2 else "accept"}"',
            f"reviewed_signature = {_q(candidate['signature'])}",
            f"rationale = {_q(RATIONALE[kind])}",
            f"invocation = {json.dumps(argv, ensure_ascii=False)}",
            f"expected_exit_status = {status}",
            f"expected_stdout_contains = {_q(stdout_fragment)}",
            f"expected_stderr_contains = {_q(stderr_fragment)}",
            f"effects = [{_q(effects)}]",
            f"test_ids = [{_q(f'{TEST_FILE}::test_reviewed_case[{harness.short_id(candidate["id"])}]')}]",
            "",
        ]
    CATALOG.write_text("\n".join(rows), encoding="utf-8")
    print(f"wrote {len(manifest['candidates'])} cases to {CATALOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
