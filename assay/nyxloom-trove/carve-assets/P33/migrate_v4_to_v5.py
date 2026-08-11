#!/usr/bin/env python3
"""Carver-owned v4 -> v5 schema transform. THE auditable delta for P33.

This script is a locked carve asset. It exists so the v5 schema is reviewable as
a *delta* rather than as a 28 KB diff, and so the transform can be re-run to
prove `verdict.schema.v5.json` was not hand-edited afterwards.

Run from the assay project root:

    python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check

`--check` regenerates in memory and compares against the committed v5 asset,
exiting nonzero on any drift. With no flag it writes the asset.

Design authority: SCHEMA-V5-DESIGN.md, decisions A-220 and A-221.
The implementer may not edit this file or the schema it generates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# B1 repair (A-223): the source is a COMMITTED v4 SNAPSHOT, never the live
# shipped schema. Work item 1 overwrites the live path with v5, so a transform
# reading it could only ever run once -- `--check` was unsatisfiable after the
# work it was required to verify. The snapshot is byte-identical to the shipped
# v4 at the input revision and keeps the delta auditable forever.
V4 = HERE / "verdict.schema.v4-snapshot.json"
V5 = HERE / "verdict.schema.v5.json"

# --- V5-2 / A-221: the language-qualified operator vocabulary ----------------
PYTHON_OPERATORS = [
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
]
# A-221: three faithful analogues of the Python catalogue. No falsy-swap
# analogue -- Go conditions are strictly bool, so Python's duck-typed
# truthiness has no 1:1 mapping and a nil/zero-value operator would be new
# unmeasured design. No arithmetic swap, increment/decrement, or statement
# removal: A-112 governs, and it says reuse the catalogue, never extend it.
GO_OPERATORS = [
    "go:compare-swap",
    "go:boolop-swap",
    "go:bool-const-flip",
]
SQL_OPERATORS = [
    "sql:drop-check",
    "sql:drop-unique",
    "sql:drop-not-null",
    "sql:drop-foreign-key",
    "sql:weaken-delete-action",
    "sql:drop-trigger",
    "sql:widen-check-in",
]


def transform(doc: dict) -> tuple[dict, list[str]]:
    """Return (v5 schema, human-readable changelog)."""
    d = json.loads(json.dumps(doc))  # deep copy; never mutate the v4 input
    defs = d["$defs"]
    log: list[str] = []

    # --- identity -----------------------------------------------------------
    # B8 repair (A-223(h)): the first version tested `"v4" in $id`, but the id is
    # `urn:assay:schema:verdict:4` -- no literal "v4" -- so the rewrite was dead
    # code and the v5 contract self-identified as v4. Set it explicitly.
    d["$id"] = "urn:assay:schema:verdict:5"
    log.append("$id -> urn:assay:schema:verdict:5 (B8/A-223h)")
    sv = d["properties"]["schema_version"]
    if "const" in sv:
        sv["const"] = 5
        log.append("properties.schema_version.const 4 -> 5")
    elif "enum" in sv:
        sv["enum"] = [5]
        log.append("properties.schema_version.enum -> [5]")
    d["title"] = d.get("title", "").replace("v4", "v5")

    # --- V5-1: hoist language / source_roots / base out of judgment.r1 ------
    r1 = defs["judgment_r1"]
    hoisted = {}
    for key in ("language", "source_roots", "base"):
        hoisted[key] = r1["properties"].pop(key)
        r1["required"].remove(key)
    log.append("judgment_r1: dropped language, source_roots, base (V5-1)")

    defs["judgment_resolved"] = {
        "description": (
            "What was judged, shared by every computed tier above R0. Hoisted "
            "out of judgment.r1 in v5 because an R0,R2 lane -- legal since "
            "A-192 -- renders no r1 and would otherwise record no language, "
            "source roots, or comparison base at all, even though R2 scopes "
            "mutation to changed lines against exactly that base. `base` is "
            "REQUIRED ONLY when r1 or r2 is present (A-223a): "
            "JUDGE_FIELDS_BY_RIGOR carries `base` for R1 and R2 and not for R3, "
            "so an R0,R3 lane genuinely has no comparison commit and must not be "
            "made to invent one. That conditional lives on `judgment`, which can "
            "see its own tiers. `source_roots` is the DECLARED project-relative "
            "spelling (A-049), never the relocated absolute scratch paths "
            "(A-149); `base` is the pre-snapshot consumer-repository resolution, "
            "which R1's in-snapshot resolution must equal (A-223f). Both are "
            "cross-object and belong to the model and raw verifier."
        ),
        "type": "object",
        "required": ["language", "source_roots"],
        "properties": hoisted,
        "additionalProperties": False,
    }
    log.append("judgment_resolved: NEW, base conditional (V5-1/A-223a)")

    judgment = defs["judgment"]
    judgment["properties"]["resolved"] = {"$ref": "#/$defs/judgment_resolved"}
    judgment["required"] = ["resolved"]
    # B9 repair (A-223g): v4's minProperties:1 guaranteed at least one real tier.
    # Dropping it silently made `judgment: {resolved}` valid for a run that
    # judged nothing. Restore the guarantee EXPLICITLY rather than by a count
    # that now also counts `resolved`.
    judgment.pop("minProperties", None)
    judgment["allOf"] = [
        {
            "anyOf": [
                {"required": ["r1"]},
                {"required": ["r2"]},
                {"required": ["r3"]},
            ]
        },
        {
            "if": {"anyOf": [{"required": ["r1"]}, {"required": ["r2"]}]},
            "then": {
                "properties": {
                    "resolved": {"required": ["language", "source_roots", "base"]}
                }
            },
        },
    ]
    log.append(
        "judgment: +resolved (required), at-least-one-tier restored explicitly "
        "(B9/A-223g), base required iff r1|r2 (B3/A-223a)"
    )

    # --- V5-2 / A-221: language-qualified operators --------------------------
    defs["mutation_operator"] = {
        "description": (
            "A language-qualified operator name. The prefix MUST equal "
            "judgment.resolved.language; that equality is cross-object and "
            "therefore lives in the model and the raw verifier, not here. Each "
            "language's vocabulary is closed, so a Python lane cannot declare a "
            "SQL operator. Adding a language, or an operator to a language, is "
            "an additive oneOf change and needs no further version bump."
        ),
        "oneOf": [
            {"enum": PYTHON_OPERATORS},
            {"enum": GO_OPERATORS},
            {"enum": SQL_OPERATORS},
        ],
    }
    log.append(
        f"mutation_operator: closed 4-value enum -> per-language oneOf "
        f"(python {len(PYTHON_OPERATORS)}, go {len(GO_OPERATORS)}, "
        f"sql {len(SQL_OPERATORS)}) (V5-2/A-221)"
    )

    # --- V5-3: the equivalent bucket ----------------------------------------
    mutation = defs["mutation"]
    mutation["properties"]["equivalent"] = {
        "description": (
            "Mutants proven to produce no change in the judged artifact, "
            "established by byte comparison of judgment.r2.equivalence_artifact "
            "against the baseline run's. NOT survived: a no-op mutant is "
            "evidence about the mutation, not about the tests. Excluded from "
            "the mutation-score denominator (arithmetic is cross-object and "
            "belongs to the model and raw verifier)."
        ),
        "type": "array",
        "items": {"$ref": "#/$defs/mutant_outcome"},
    }
    mutation["required"].append("equivalent")
    log.append("mutation: +equivalent bucket, required (V5-3)")

    r2 = defs["judgment_r2"]
    r2["properties"]["equivalence_artifact"] = {
        "description": (
            "A project-relative path the lane's own declared command writes "
            "after applying a mutant, whose bytes Assay compares against the "
            "baseline run's. For SQL this is a schema-only dump. Assay compares "
            "two artifacts; it never opens a database connection or receives a "
            "DSN. If absent, the equivalent bucket must be empty -- "
            "both-present-or-both-absent, following A-209's attestation pair."
        ),
        "type": "string",
        "minLength": 1,
    }
    log.append("judgment_r2: +equivalence_artifact (optional) (V5-3)")

    # --- V5-4: kill attribution ---------------------------------------------
    r2["properties"]["kill_attribution"] = {
        "description": (
            "Whether kills in this run are attributed to a declared mechanism "
            "or merely observed. 'unattributed' is honest and common: it means "
            "a killed mutant proves only that the suite failed, not that it "
            "failed for the reason the mutant created. Assay cannot verify a "
            "database's causality, so v5 makes the weakness visible rather "
            "than absent (A-220)."
        ),
        "enum": ["declared", "unattributed"],
    }
    r2["required"].append("kill_attribution")
    r2["properties"]["kill_signal_artifact"] = {
        "description": (
            "A project-relative path the declared command writes, naming the "
            "mechanism that refused. Required when kill_attribution is "
            "'declared'; forbidden when it is 'unattributed'."
        ),
        "type": "string",
        "minLength": 1,
    }
    log.append("judgment_r2: +kill_attribution (required), +kill_signal_artifact (V5-4)")

    defs["mutant_outcome"]["properties"]["kill_signal"] = {
        "description": (
            "The mechanism this kill was attributed to, verbatim from the "
            "declared kill_signal_artifact. Present on every killed entry when "
            "kill_attribution is 'declared'; absent otherwise. Legal ONLY on a "
            "killed entry (A-223e) -- a kill signal on a mutant nothing killed "
            "is a contradiction. Enforced below, per bucket."
        ),
        "type": "string",
        "minLength": 1,
    }
    log.append("mutant_outcome: +kill_signal (optional) (V5-4)")

    # B10 repair (A-223e): kill_signal is legal only in the killed bucket. The
    # constraint is locally expressible, so per A-182 it belongs in the schema
    # rather than in a verifier check nobody can see from the document.
    for bucket in ("survived", "crashed", "budget_exceeded", "equivalent"):
        mutation["properties"][bucket]["items"] = {
            "allOf": [
                {"$ref": "#/$defs/mutant_outcome"},
                {"not": {"required": ["kill_signal"]}},
            ]
        }
    log.append(
        "mutation: kill_signal forbidden on survived/crashed/budget_exceeded/"
        "equivalent items (B10/A-223e)"
    )

    # B6 repair (A-223d): an all-inert run needs its own terminal, or it walks
    # judge_mutation to PASS -- A-026/A-035's 0/0-is-100% bug one layer down.
    # A-050 forbids inventing a reason code without a ruling; A-223(d) is it.
    rc = defs["reason_code"]
    if "enum" in rc and "ALL_MUTANTS_EQUIVALENT" not in rc["enum"]:
        rc["enum"].append("ALL_MUTANTS_EQUIVALENT")
    inc = defs["reason_codes"]["INCONCLUSIVE"]
    if "ALL_MUTANTS_EQUIVALENT" not in inc["enum"]:
        inc["enum"].append("ALL_MUTANTS_EQUIVALENT")
    log.append(
        "reason_code + reason_codes.INCONCLUSIVE: +ALL_MUTANTS_EQUIVALENT "
        "(B6/A-223d)"
    )

    # R2-B1 repair (A-228): the new terminal must inherit every constraint its
    # three siblings carry. Branches 6/7/8 each pin reason -> payload + rigor R2;
    # the first version of this transform gave the new code none, so a
    # payload-free, non-R2 ALL_MUTANTS_EQUIVALENT validated -- verbatim the
    # forgery A-136 closed for NO_MUTANTS, one bucket over.
    defs["claim"]["allOf"].append(
        {
            "description": (
                "A-228/A-223d: ALL_MUTANTS_EQUIVALENT is the SUPPORTED "
                "all-inert result, so it must carry the payload that proves "
                "an analysis ran and every attempted mutant was provably no-op. "
                "A payload-free variant would be indistinguishable from "
                "capability absence, which is the same forgery branch 7 closes "
                "for NO_MUTANTS. Whether killed + survived == 0 with a "
                "non-empty equivalent bucket is cross-object and lives in the "
                "model and raw verifier."
            ),
            "if": {
                "required": ["reason_code"],
                "properties": {"reason_code": {"const": "ALL_MUTANTS_EQUIVALENT"}},
            },
            "then": {
                "required": ["mutation"],
                "properties": {"rigor": {"const": "R2"}},
            },
        }
    )
    log.append(
        "claim.allOf: +branch requiring the mutation payload and rigor R2 for "
        "ALL_MUTANTS_EQUIVALENT (R2-B1/A-228)"
    )

    # Same root cause: the fifth bucket was never propagated into the limit
    # sentinel's own shape, nor into the prose that counts buckets.
    for branch in defs["claim"]["allOf"]:
        then = branch.get("then", {})
        mprops = then.get("properties", {}).get("mutation", {}).get("properties")
        if mprops and "killed" in mprops and "equivalent" not in mprops:
            mprops["equivalent"] = {"maxItems": 0}
            branch["description"] = branch["description"].replace(
                "four empty buckets", "five empty buckets"
            )
            log.append(
                "claim.allOf limit sentinel: +equivalent maxItems 0, prose "
                "four->five buckets (R2-B1/A-228)"
            )
    mutation["description"] = mutation["description"].replace(
        "FOUR identity buckets", "FIVE identity buckets"
    )
    log.append("mutation.description: FOUR -> FIVE identity buckets (A-228)")

    # R2-B4 repair (A-227): the base rule was `if`, never `only-if`. An r3-only
    # judgment carrying a base validated. The producer cannot reach that state
    # (A-062 refuses judge.base on an R0,R3 lane as inert config) but verify.py
    # exists for FOREIGN documents, and A-182 puts a locally expressible
    # conditional in the schema.
    judgment["allOf"].append(
        {
            "description": (
                "A-227: base is required IF r1|r2 and forbidden UNLESS r1|r2. "
                "Closes the only-if half, so a foreign artifact cannot record a "
                "comparison commit for a tier that reads none."
            ),
            "if": {
                "allOf": [
                    {"not": {"required": ["r1"]}},
                    {"not": {"required": ["r2"]}},
                ]
            },
            "then": {
                "properties": {"resolved": {"not": {"required": ["base"]}}}
            },
        }
    )
    log.append("judgment.allOf: +base forbidden unless r1|r2 (R2-B4/A-227)")

    # --- V5-5: helper provenance -------------------------------------------
    d["properties"]["helpers"] = {
        "minItems": 1,
        "description": (
            "A-230a: OMITTED when no helper ran, never serialized as []. "
            "minItems enforces it -- round 5 found the rule was prose-only, so "
            "an empty array validated against everything the rule forbade. Same "
            "discipline as judgment, which is absent rather than empty when "
            "nothing was judged (A-025/A-136). "
            "Every external helper an adapter actually invoked to produce a "
            "claim payload. external_tools (A-013) declares what a lane needs; "
            "this records what ran, so a coverage or mutation claim is "
            "reproducible against a known tool identity. Same reason P27's "
            "carve pinned image digests: a symbolic toolchain reference makes a "
            "verdict unreproducible."
        ),
        "type": "array",
        "items": {
            "type": "object",
            "required": ["role", "tool", "resolved_path", "identity"],
            "properties": {
                "role": {
                    "enum": [
                        "statement-positions",
                        "mutation-sites",
                        "executable-code",
                    ]
                },
                "tool": {"type": "string", "minLength": 1},
                "resolved_path": {"type": "string", "minLength": 1},
                "identity": {
                    "description": (
                        "What the tool reports about itself, verbatim (e.g. "
                        "'go version go1.25.12 linux/amd64'). Never inferred "
                        "from a package name or a tag."
                    ),
                    "type": "string",
                    "minLength": 1,
                },
            },
            "additionalProperties": False,
        },
    }
    log.append("properties.helpers: NEW optional array, closed 3-value role (V5-5)")

    return d, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    v4 = json.loads(V4.read_text())
    if v4["properties"]["schema_version"].get("const") != 4:
        print(
            "REFUSING: source schema is not v4 -- this transform is v4-specific "
            "and re-running it against v5 would double-apply.",
            file=sys.stderr,
        )
        return 2

    v5, log = transform(v4)
    text = json.dumps(v5, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not V5.exists():
            print("MISSING: verdict.schema.v5.json", file=sys.stderr)
            return 1
        if V5.read_text() != text:
            print("DRIFT: committed v5 asset differs from the transform", file=sys.stderr)
            return 1
        print("OK: committed v5 asset matches the transform exactly")
        return 0

    V5.write_text(text)
    print(f"wrote {V5.relative_to(HERE.parents[1])} ({len(text)} bytes)")
    for line in log:
        print("  -", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
