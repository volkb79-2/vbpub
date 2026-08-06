---
schema_version: 1
id: assay-P01-skeleton-lane-config-verdict-schema
project: assay
title: "Project skeleton, assay.toml loader that refuses to default, and the verdict schema"
tier: sonnet5-high
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "pyproject.toml"
    - "src/assay/__init__.py"
    - "src/assay/config.py"
    - "src/assay/verdict.py"
    - "src/assay/schemas/verdict.schema.json"
    - "src/assay/cli.py"
    - "tests/**"
    - "assay.toml"
    - "nyxloom-trove/nyxloom.toml"
  forbid:
    - "../nyxloom/**"
    - "../ciu/**"
    - "../topos/**"
oracles:
  - id: O1
    observable: "a lane omitting `source_roots`, `budget`, `enforcement`, `scope`, `rigor`, `argv`, `env`, `env_passthrough`, `allow_argv_append` or `allow_excluded` fails to load with exit 2 / reason_code BAD_LANE_CONFIG naming the missing field; no field is defaulted"
    negative: "a missing field is filled with a plausible literal, so a project silently gates the wrong tree -- AGENTS.md 4.2a anti-pattern #1, which all four existing copies ship"
    gate: tester-unified
  - id: O2
    observable: "a lane declaring rigor R1 without `judge.coverage`/`judge.fail_under`/`judge.language` fails to load; likewise R2 without `judge.mutation` and R3 without `judge.canary`"
    negative: "a lane claims a rigor level it cannot exercise and loads anyway -- the lane-table-implies-capability failure in data form"
    gate: tester-unified
  - id: O3
    observable: "`source_roots` naming a path absent from the repo fails at load with BAD_LANE_CONFIG"
    negative: "a typo'd root matches no changed file, so the gate returns 0/0 PASS forever -- a laundering gate none of the four copies guards"
    gate: tester-unified
  - id: O4
    observable: "a Verdict for each of the six outcomes validates against `src/assay/schemas/verdict.schema.json`; the schema requires `reason_code` on every non-PASS outcome and FORBIDS a coverage block when outcome is NO_MEASUREMENT"
    negative: "a NO_MEASUREMENT verdict carries `pct: 100.0`, rebuilding the original 0/0-reads-as-100% ambiguity inside the artifact"
    gate: tester-unified
  - id: O5
    observable: "`pip install .` into a venv with no third-party packages succeeds and `python -c 'import assay'` works; `grep -rn '^import \\|^from ' src/assay/ | grep -v -E 'assay|tomllib|json|xml|ast|re|subprocess|pathlib|argparse|dataclasses|typing|os|sys|collections|enum'` returns nothing"
    negative: "a third-party runtime dependency creeps in, weakening the standalone claim the project exists to prove"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "declaring the gate requires rebuilding the shared tester-unified image (see A-O02) -- stop and report; a rebuild re-risks ciu/cmru/topos/nyxloom's gate"
  - "the verdict schema cannot express all three evidence tiers without a field not agreed in decisions.md"
mutexes: []
---

# P01 — skeleton, lane config, verdict schema

The claim to attack: **does the config contract refuse to invent, and can the
verdict schema express every outcome?** No judging logic lands here.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §5 (defaults doctrine applied), §6 (verdict contract),
   §12 (lane file structure). These are the whole specification.
2. `/workspaces/dstdns/AGENTS.md` §4.2a — the doctrine §5 applies.
3. `nyxloom-trove/decisions.md` A-005, A-014..A-019, A-021..A-029, A-032..A-036.

## Work

1. `pyproject.toml` following the estate pattern (`setuptools_scm`, `root = ".."`,
   tag regex `^assay-v(?P<version>[0-9].*)$`), `requires-python = ">=3.11"`,
   **no runtime dependencies**, `[project.optional-dependencies] test = [pytest, pytest-cov]`.
2. `src/assay/config.py` — load `assay.toml`, validate strictly per O1/O2/O3.
   Every field is required or conditionally required; **nothing is defaulted**.
3. `src/assay/verdict.py` — the six outcomes, `reason_code` enums, the `claims[]`
   array with slots for all three tiers, `argv_declared`/`argv_appended`/
   `argv_effective`, `env_declared`/`env_effective`, `declared_unverified`.
4. `src/assay/schemas/verdict.schema.json` — shipped as **data**, so consumers
   validate without importing assay.
5. `src/assay/cli.py` — `assay lanes` only (list and validate declared lanes).
   Other verbs land in their own packages.
6. `assay.toml` for assay itself, and `nyxloom-trove/nyxloom.toml` declaring the
   `tester-unified` gate. See A-O01/A-O02 before declaring the gate.

## Out of scope

Diff parsing, coverage parsing, evaluation, running anything. `assay lanes` must
not execute a lane.
