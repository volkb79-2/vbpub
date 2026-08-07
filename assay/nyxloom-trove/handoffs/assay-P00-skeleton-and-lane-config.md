---
schema_version: 1
id: assay-P00-skeleton-and-lane-config
project: assay
title: "Project skeleton and the assay.toml loader that refuses to invent"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "pyproject.toml"
    - ".gitignore"
    - "src/assay/__init__.py"
    - "src/assay/config.py"
    - "src/assay/errors.py"
    - "src/assay/cli.py"
    - "tests/**"
    - "assay.toml"
    - "nyxloom-trove/reports/**"
  forbid:
    - "src/assay/verdict.py"
    - "src/assay/schemas/**"
    - "nyxloom-trove/nyxloom.toml"
    - "nyxloom-trove/decisions.md"
    - "docs/DESIGN-GUIDE.md"
    - "../nyxloom/**"
    - "../ciu/**"
    - "../topos/**"
oracles:
  - id: O1
    observable: "BOTH directions are tested. REJECT: a lane omitting any of `scope`, `rigor`, `enforcement`, `argv`, `env`, `env_passthrough`, `budget`, `allow_argv_append` fails to load with BAD_LANE_CONFIG naming THE MISSING FIELD. ACCEPT: a complete lane loads, and every value round-trips equal to what the TOML declared, with no key present that the file did not declare"
    negative: "the loader rejects everything, which passes a reject-only test while loading nothing -- or it fills a missing field with a plausible literal, so a project silently gates the wrong tree (AGENTS.md 4.2a anti-pattern #1, which all four existing copies ship)"
    gate: tester-unified
  - id: O2
    observable: "the five `judge` fields are CONDITIONALLY required (A-048): an R0-only lane with NO `[judge]` table loads clean; a lane declaring R1 without all five of `judge.{coverage,fail_under,allow_excluded,source_roots,language}` fails; R2 additionally requires `judge.mutation`; R3 additionally requires `judge.canary`; and an R1 lane WITH all five loads"
    negative: "the judge fields are treated as unconditional, so assay's own R0 lane -- the one this package ships -- is rejected by the loader this package ships; or a lane claims a rigor level it cannot exercise and loads anyway"
    gate: tester-unified
  - id: O3
    observable: "`source_roots` resolve against the directory containing `assay.toml` (A-049), proven by a fixture whose project root differs from its repo root: a root that exists relative to the PROJECT loads, the same string relative to the repo root does NOT, and a nonexistent root fails with BAD_LANE_CONFIG"
    negative: "resolution is relative to the process cwd or the repo root, so a typo'd root matches no changed file and the gate returns 0/0 PASS forever -- a laundering gate none of the four copies guards"
    gate: tester-unified
  - id: O4
    observable: "closed vocabularies are enforced (A-053, A-052): `scope` outside S0-S4, `rigor` outside R0-R3, `enforcement` outside {gate, advisory}, and an unparseable `budget` string each fail with BAD_LANE_CONFIG; a valid `budget` is parsed to a numeric duration the loader exposes, not merely checked for presence"
    negative: "an unknown enum value or a malformed duration is carried forward and surfaces as a crash or a wrong timeout inside P07, where the config layer could have caught it"
    gate: tester-unified
  - id: O5
    observable: "dependency purity is proven by an AST WALK (A-060), not grep: a test parses every file under `src/assay/`, collects each Import/ImportFrom root name, and asserts the set is a subset of `sys.stdlib_module_names | {\"assay\"}` -- and the test itself is proven to FAIL when a third-party import is injected into a temp copy. Separately, a venv containing only assay installs the package and imports it"
    negative: "the check cannot fail. The original grep was verified vacuous: `grep -rn` prefixes every line with a path containing `assay`, and the `-v` alternation contains `assay`, so every line was filtered regardless of content -- it passed clean on a file importing requests, flask and a function-level boto3"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a decision id in decisions.md is unimplementable as written -- name the id and STOP; do not work around it"
  - "an oracle can only be satisfied by touching a path in scope.forbid"
mutexes: []
---

# P01a — skeleton and the lane config loader

The claim to attack: **does the config contract refuse to invent?**

Split from the original P01 (A-059) because that package estimated at ~410–520
changed executable lines and its two halves share no code. The verdict model and
its JSON Schema are P01b.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §5 (defaults doctrine applied) and §12 (lane file
   structure, the closed vocabularies, and the `source_roots` resolution base).
2. `/workspaces/dstdns/AGENTS.md` §4.2a — the doctrine §5 applies.
3. `nyxloom-trove/decisions.md` — the whole "session 2" block (A-048…A-060) was
   written to close defects a pre-flight found in this package's first draft.
   A-048, A-049, A-052, A-053, A-056, A-057 and A-060 are all directly binding
   here.

## Work

1. `pyproject.toml`, estate pattern (`setuptools_scm`, `root = ".."`,
   `tag_regex = "^assay-v(?P<version>[0-9].*)$"`), `requires-python = ">=3.11"`,
   **no runtime dependencies**, `test = ["pytest", "pytest-cov", "jsonschema"]`.
   **Set `fallback_version`** (A-057): there is no `assay-v*` tag in the repo,
   and installing from a copied tree without it fails with
   `LookupError: setuptools-scm was unable to detect version`. Verified.
2. `.gitignore` covering `build/` and `src/*.egg-info/` (A-057): an in-place
   `pip install .` writes both into the bind-mounted worktree, which would
   otherwise interact with P02's DIRTY_TREE tests and P11's self-hosting.
3. `src/assay/errors.py` — the typed error carrying an outcome and a
   `reason_code`. `assay lanes` raises it; the CLI maps it to an exit code and
   emits **no artifact** (A-054).
4. `src/assay/config.py` — the loader. Every field required or conditionally
   required; **nothing defaulted**.
5. `src/assay/cli.py` — `assay lanes` only: list and validate declared lanes.
   It must not execute one.
6. `assay.toml` for assay itself: lane named `tester-unified` (P11 O3 requires
   that name), `scope = "S1"`, `rigor = ["R0"]`, **no `[judge]` table** — which
   is now coherent, per A-048. Declare only what is true today; P11 upgrades it.

## Already done for you — do not create these

`nyxloom-trove/nyxloom.toml` exists; the controller closed A-O01/A-O02 and
verified `tester-unified:local` already carries the whole closure, so no image
rebuild is involved. It, `docs/DESIGN-GUIDE.md` and `nyxloom-trove/decisions.md`
are all in `scope.forbid` — they are the specification you implement, not files
to edit. If one is wrong, say so in the LOG and stop.

## On hollow tests, for this package specifically

Every oracle here is trivially satisfiable by a loader that rejects everything.
Each one therefore names both directions, and a test suite that only exercises
the rejecting half has not satisfied the oracle regardless of what the gate
says. Assume this is the thing your review will be judged on.
