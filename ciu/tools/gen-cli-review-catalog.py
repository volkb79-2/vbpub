#!/usr/bin/env python3
"""Write ``docs/cli-review.toml`` from the exported surface (``docs/cli-surface.json``).

Every candidate the surface exporter lists (minimum syntax, argument shapes, option
spellings and choices, exclusive members and conflicts, declared constraints) becomes ONE active
catalog row with a concrete invocation. The row's expected outcome is the one the REAL registry
produces when the invocation runs through ``tests/tests/cli_review_harness.py`` (implementation
layer recorded, nothing executed), so the catalog is a CHARACTERIZATION of the grammar: it pins
spellings, accepted shapes and the library-enforced refusals, not the behaviour behind a verb
(that is pinned by each verb's own test module). Re-run after ``cli-extended surface sync`` when
the grammar changes:

    cd ciu && PYTHONPATH=src:../libraries/worktree/src:tests/tests python3 tools/gen-cli-review-catalog.py
    cli-extended surface sync && cli-extended surface check

Rows are keyed by the exporter's case id and signature, so an unchanged case keeps its row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "tests"))

import cli_review_harness as harness  # noqa: E402

MANIFEST = ROOT / "docs" / "cli-surface.json"
CATALOG = ROOT / "docs" / "cli-review.toml"
TEST_FILE = "tests/tests/test_ciu_cli_review_cases.py"

RATIONALE = {
    "minimum": "The route's minimum executable syntax is accepted and reaches its handler.",
    "argument-shape": "The positional is accepted in the shape the registration declares.",
    "argument-choice": "A declared choice of the positional is accepted.",
    "option-spelling": "The option is accepted in exactly this spelling (no prefix abbreviation).",
    "option-choice": "A declared choice of the option is accepted.",
    "exclusive-member": "One member of a mutually exclusive group is accepted alone.",
    "exclusive-conflict": "Two members of one exclusive group are refused by the shared parser before any handler runs.",
    "constraint-requires": "An option used without the option its declared Requires rule names is refused before any handler runs.",
    "constraint-conflict": "Two options a declared Conflicts rule forbids together are refused before any handler runs.",
}


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    routes = {route["id"]: route for route in manifest["routes"]}
    actions: dict[str, dict] = {}
    for route in routes.values():
        for action in route["actions"]:
            actions[action["id"]] = action

    def action_for(option_id: str) -> dict:
        """The route action, or a flag-only stand-in for a library-owned control."""
        found = actions.get(option_id)
        if found is not None:
            return found
        flag = option_id.rsplit("/", 1)[1]
        return {"canonical": flag, "minimum_values": 0, "kind": "option", "scope": "global",
                "choices": None, "type": None, "exclusive_group": None}

    def by_flag_of(route: dict) -> dict[str, dict]:
        table = {a["canonical"]: a for a in route["actions"] if a.get("canonical")}
        for flag in ("--json", "--yes", "--dry-run", "--debug-raw"):
            table.setdefault(flag, action_for(f"option:x/{flag}"))
        return table

    def value_tokens(action: dict, choice: str | None = None) -> list[str]:
        if not action.get("minimum_values"):
            return []
        if choice is not None:
            return [choice]
        if action.get("choices"):
            return [str(action["choices"][0])]
        kind = (action.get("type") or {}).get("callable", "")
        if action.get("canonical") == "--check" and action.get("kind") == "option" and action.get("minimum_values"):
            return ["git"]  # a real doctor check name
        return ["1"] if kind.endswith("int") else ["x"]

    def flag_of(action_id: str) -> str:
        return actions[action_id]["canonical"]

    def with_requirements(route: dict, tokens: list[str], chosen: list[str]) -> list[str]:
        """Add what the route's declared Requires rules demand for the chosen options."""
        by_flag = by_flag_of(route)
        queue = list(chosen)
        seen = set(chosen)
        while queue:
            current = queue.pop(0)
            for rule in route["constraints"]:
                if rule["kind"] == "requires" and rule["option"] == current:
                    need = rule["any_of"][0]
                    if need not in seen and need in by_flag:
                        tokens += [need, *value_tokens(by_flag[need])]
                        seen.add(need)
                        queue.append(need)
        return tokens

    # Flags a handler (not the grammar) insists on in v7: `migrate` has only its --check mode.
    handler_required = {("migrate",): ["--check"]}

    def base(route: dict, skip_groups: frozenset[str] = frozenset(),
             skip_flags: frozenset[str] = frozenset()) -> list[str]:
        tokens = list(route["path"]) + handler_required.get(tuple(route["path"]), [])
        for action in route["actions"]:
            if action["kind"] == "argument" and action["required"]:
                tokens.append(str((action.get("choices") or ["x"])[0]))
        taken_groups: set[str] = set()
        for action in route["actions"]:
            if action["kind"] != "option" or action["scope"] == "global":
                continue
            group = action.get("exclusive_group")
            if action.get("required") or (group and action.get("exclusive_required")):
                if action["canonical"] in skip_flags or group in skip_groups:
                    continue
                if group:
                    if group in taken_groups:
                        continue
                    taken_groups.add(group)
                tokens += [action["canonical"], *value_tokens(action)]
        chosen = [t for t in tokens if t.startswith("--")]
        return with_requirements(route, tokens, chosen)

    rows: list[str] = [
        "schema_version = 1",
        'cli_id = "ciu"',
        "max_candidates = 4000",
        "",
        "# Generated by tools/gen-cli-review-catalog.py from docs/cli-surface.json: a",
        "# CHARACTERIZATION catalog. Each row's invocation runs through the real registry in",
        "# tests/tests/cli_review_harness.py (implementation layer recorded, nothing executed) and",
        "# the recorded exit status / stderr fragment is what the registry produced. It pins the",
        "# GRAMMAR (spellings, shapes, choices, exclusive groups, declared constraints) and the",
        "# library-owned verbs; behaviour behind a verb lives in that verb's own test module.",
        "",
    ]
    count = 0
    for candidate in manifest["candidates"]:
        route = routes[candidate["route_id"]]
        shape = candidate["shape"]
        kind = candidate["kind"]
        if kind in ("minimum", "argument-shape", "argument-choice"):
            argv = base(route)
            if kind != "minimum":
                action = actions[shape["argument_id"]]
                if not action["required"]:
                    argv.append(str(shape.get("choice") or (action.get("choices") or ["x"])[0]))
        elif kind in ("option-spelling", "option-choice"):
            action = action_for(shape["option_id"])
            group = action.get("exclusive_group")
            skip = frozenset([group]) if group else frozenset()
            flag = shape.get("spelling") or action["canonical"]
            argv = base(route, skip_groups=skip, skip_flags=frozenset([action["canonical"]]))
            argv += [flag, *value_tokens(action, shape.get("choice"))]
            argv = with_requirements(route, argv, [action["canonical"]])
        elif kind == "exclusive-member":
            action = actions[shape["selected_option"]]
            group = action.get("exclusive_group")
            argv = base(route, skip_groups=frozenset([group] if group else []))
            argv += [action["canonical"], *value_tokens(action)]
            argv = with_requirements(route, argv, [action["canonical"]])
        elif kind == "exclusive-conflict":
            first, second = (actions[o] for o in shape["options"][:2])
            group = first.get("exclusive_group")
            argv = base(route, skip_groups=frozenset([group] if group else []))
            for action in (first, second):
                argv += [action["canonical"], *value_tokens(action)]
        elif kind == "constraint-requires":
            by_flag = by_flag_of(route)
            action = by_flag[shape["option"]]
            argv = base(route, skip_flags=frozenset([shape["option"], *shape["any_of"]]))
            argv += [shape["option"], *value_tokens(action)]
        elif kind == "constraint-conflict":
            by_flag = by_flag_of(route)
            argv = base(route, skip_flags=frozenset(shape["options"]))
            for flag in shape["options"][:2]:
                argv += [flag, *value_tokens(by_flag[flag])]
        else:  # pragma: no cover - a new exporter kind needs a generator rule
            raise SystemExit(f"unhandled candidate kind {kind!r}")
        status, _out, err, _recorded = harness.run(argv)
        refused = status != 0
        fragment = "[ERROR]" if status == 2 else ""
        if status == 1:
            effects = ("The library-owned verb runs for real in the sandbox and reports a failing "
                       "environment/skills check (exit 1); nothing outside the sandbox is touched.")
        elif refused:
            effects = "Refused by the shared parser/constraints before any handler runs; nothing is changed."
        else:
            effects = "Parse-level case: the sandbox records the implementation call and executes nothing."
        rows += [
            "[[cases]]",
            f"id = {_q(candidate['id'])}",
            'state = "active"',
            f'decision = "{"refuse" if refused else "accept"}"',
            f"reviewed_signature = {_q(candidate['signature'])}",
            f"rationale = {_q(RATIONALE[kind])}",
            f"invocation = {json.dumps(argv, ensure_ascii=False)}",
            f"expected_exit_status = {status}",
            'expected_stdout_contains = ""',
            f"expected_stderr_contains = {_q(fragment)}",
            f"effects = [{_q(effects)}]",
            f"test_ids = [{_q(f'{TEST_FILE}::test_reviewed_case[{harness.short_id(candidate['id'])}]')}]",
            "",
        ]
        count += 1
        del err
    CATALOG.write_text("\n".join(rows), encoding="utf-8")
    print(f"wrote {count} cases to {CATALOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
