#!/usr/bin/env python3
"""P33's witnessed pre-implementation negatives. Carver-owned; do not edit.

Every assertion below must hold on the PRE-implementation tree. Together they
prove the v5 migration is real work rather than an additive tweak:

  1. verdict.schema.v5.json is a legal 2020-12 JSON Schema.
  2. Each v5 template validates against v5 (so the targets are reachable).
  3. Each v5 template is REJECTED by the current v4 schema.
  4. Each v5 template is REJECTED by the current raw verifier (assay.verify).
  5. An existing v4 artifact is REJECTED by v5 -- the migration is breaking,
     which is what makes it a version bump and not a widening.
  6. The keystone claim: the SQL artifact's shape is UNREPRESENTABLE in v4,
     because an R0,R2 lane has nowhere to record language/source_roots/base.

Run from the assay project root:
    PYTHONPATH=src python3 nyxloom-trove/carve-assets/P33/probe_v5_controlled_red.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V4_PATH = ROOT / "src" / "assay" / "schemas" / "verdict.schema.json"
V5_PATH = HERE / "verdict.schema.v5.json"

SUBS = {
    "@ASSAY_VERSION@": "1.3.0",
    "@HEAD_OID@": "c23bbafbc3d52bd1a5d8ab58a23ca8ae61a70d9e",
    "@BASE_OID@": "93c31eebd233b2aa9eb95f5533695a29e7c11516",
    "@STARTED@": "2026-08-11T00:00:00+00:00",
    "@ENDED@": "2026-08-11T00:00:01+00:00",
    "@SQL_HELPER_IDENTITY@": "assay-sql-sites 0.1.0 (libpg_query 17-6.1.0)",
    "@KILLED_REPLACEMENT_SHA@": "a" * 64,
    "@SURVIVED_REPLACEMENT_SHA@": "b" * 64,
    "@EQUIVALENT_REPLACEMENT_SHA@": "c" * 64,
    "@BUDGET_REPLACEMENT_SHA@": "d" * 64,
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def substitute(text: str) -> dict:
    for k, v in SUBS.items():
        text = text.replace(k, v)
    return json.loads(text)


def errors_against(schema: dict, doc: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.path)}: {e.message[:70]}" for e in v.iter_errors(doc)]


v4 = json.loads(V4_PATH.read_text())
v5 = json.loads(V5_PATH.read_text())

# 1 -------------------------------------------------------------------------
try:
    jsonschema.Draft202012Validator.check_schema(v5)
    check("v5 is a legal 2020-12 JSON Schema", True)
except jsonschema.SchemaError as exc:
    check("v5 is a legal 2020-12 JSON Schema", False, str(exc)[:80])

templates = sorted((HERE / "expected").glob("*-v5-template.json"))
check("v5 templates present", len(templates) >= 2, f"{len(templates)} found")

for path in templates:
    doc = substitute(path.read_text())
    name = path.name

    # 2 ---------------------------------------------------------------------
    errs = errors_against(v5, doc)
    check(f"{name} validates against v5", not errs, "; ".join(errs[:2]))

    # 3 ---------------------------------------------------------------------
    v4_errs = errors_against(v4, doc)
    check(f"{name} is REJECTED by the current v4 schema", bool(v4_errs),
          f"{len(v4_errs)} error(s): " + "; ".join(v4_errs[:2]))

    # 4 ---------------------------------------------------------------------
    try:
        from assay import verify as V
        vf = V.verify_text(json.dumps(doc))
        check(f"{name} is REJECTED by the current raw verifier", bool(vf),
              f"{len(vf)} failure(s): " + "; ".join(x[:60] for x in vf[:2]))
    except Exception as exc:  # a hard raise is also a rejection
        check(f"{name} is REJECTED by the current raw verifier", True,
              f"raised {type(exc).__name__}")

# 5 -------------------------------------------------------------------------
v4_asset = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected" / "current-v4-template.json"
if v4_asset.exists():
    old = substitute(v4_asset.read_text().replace("@ATTESTED_OID@", "e" * 40))
    errs = errors_against(v5, old)
    check("an existing v4 artifact is REJECTED by v5 (breaking, as intended)",
          bool(errs), f"{len(errs)} error(s): " + "; ".join(errs[:2]))
else:
    check("P26 v4 asset available for the breaking-change check", False, "missing")

# 6 -------------------------------------------------------------------------
sql = substitute((HERE / "expected" / "sql-r2-v5-template.json").read_text())
resolved = sql["judgment"]["resolved"]
check("keystone: the SQL lane declares R0,R2 with no r1",
      "r1" not in sql["judgment"] and sql["declared_rigor"] == ["R0", "R2"])
check("keystone: language/source_roots/base live in judgment.resolved",
      set(resolved) == {"language", "source_roots", "base"},
      f"language={resolved.get('language')}")
v4_judgment_props = set(v4["$defs"]["judgment"]["properties"])
check("keystone: v4's judgment has NO place for these facts without r1",
      "resolved" not in v4_judgment_props,
      f"v4 judgment properties = {sorted(v4_judgment_props)}")
v4_ops = v4["$defs"]["mutation_operator"]["enum"]
check("keystone: no sql: operator is spellable in v4",
      not any(o.startswith("sql:") for o in v4_ops), f"v4 enum = {v4_ops}")

print()
if failures:
    print(f"{len(failures)} EXPECTATION(S) NOT MET:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all P33 pre-implementation expectations hold")
