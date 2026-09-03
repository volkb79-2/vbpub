---
schema_version: 1
id: ciu8-P001-config-schema-v8-and-conformance
project: ciu8
component: schema
title: "Bootstrap ciu8 + v8 config schema: revision=8 gate, closed-key validators from one table-spec, ciu8 schema --json, S3.8.5/S3.8.6 conformance"
tier: luna-high
input_revision: "c6e77ad342b7f66496d88de2db0e1e33c2f25729"
depends_on: []
session: fresh
source: {kind: roadmap}
scope:
  touch:
    - "pyproject.toml"
    - "README.md"
    - "CHANGES.md"
    - ".gitignore"
    - "cmru.toml"
    - "run-gate.toml"
    - "run-gate.py"
    - "assay.toml"
    - "run-ciu8-tests.py"
    - "tools/assay/assay-4.1.0.pyz"
    - "tools/assay/assay-4.1.0.pyz.sha256"
    - "src/ciu8/__init__.py"
    - "src/ciu8/__main__.py"
    - "src/ciu8/cli.py"
    - "src/ciu8/schema_spec.py"
    - "src/ciu8/schema_tables.py"
    - "src/ciu8/schema_gen.py"
    - "tests/conftest.py"
    - "tests/test_schema_spec.py"
    - "tests/test_schema_gen.py"
    - "tests/test_schema_conformance.py"
    - "tests/test_spec_surface_conformance.py"
    - "tests/test_cli.py"
    - "nyxloom-trove/reports/ciu8-P001-config-schema-v8-and-conformance-LOG.md"
    - "nyxloom-trove/reports/ciu8-P001-config-schema-v8-and-conformance-REPORT.md"
    - "nyxloom-trove/reports/ciu8-P001-BRIEF.md"
    - "nyxloom-trove/reports/ciu8-P001-COMPACT.md"
oracles:
  - id: O1
    observable: "From fresh main, the recipe in '## Environment setup' produces a checkout where `python3 -m pip install -e ./ciu8[test]` succeeds and `ciu8 version` prints `ciu8 <version>` and exits 0; `run-gate.py --worktree <path> ciu8` (run from `ciu8/`, tester-unified environment) exits 0 with every assert this project's declared gate names satisfied (tests-pass, changed-line-coverage, assay-verdict)."
    negative: "A cockpit-venv-only pytest run, a gate invoked against the devcontainer instead of tester-unified, a console script that only works via `python -m ciu8.cli`, or a gate that never actually executes (e.g. an empty test suite reported as passing) does not satisfy this oracle."
    gate: tester-unified
  - id: O2
    observable: "`[project] revision = 8` parses and passes schema validation; `[project] revision = 7` (or `revision` absent) is refused with the exact string `[S3.4] config revision 7 is not 8` (or the absent-key message from O3's convention) naming the literal offending value; a non-integer `revision` (e.g. `\"8\"`) is refused as a type violation, not silently coerced."
    negative: "Accepting any integer, comparing as a string ('8' == '8.0' truthiness), coercing a string revision to int before comparing, or a refusal message that does not contain the exact substring 'is not 8' fails this oracle."
    gate: tester-unified
  - id: O3
    observable: "For every TableSpec in `ciu8.schema_tables.ALL_TABLES` (the 3 carver-authored worked tables of this packet PLUS every table transcribed per the traceability table below, citing proposal `CIU-V8-TESTING-GATE-PROPOSAL.md` §4.5 groups A1-A7/B/D/E/F), `ciu8.schema_gen.build_validator(ALL_TABLES)` accepts a minimal valid instance of that table and refuses the SAME instance plus one unrecognised key with `[S3.8] unknown key '<bogus>' in [<concrete table path>]`; every scalar/enum KeySpec's declared valid values are accepted and its declared-invalid probe (wrong type, or a value outside its enum/pattern) is refused naming the key's own `spec_rule`."
    negative: "A validator that only checks the 3 worked-example tables, a validator built from a SEPARATE hand-written key list rather than importing `ALL_TABLES`, or one that accepts an unknown key silently (warns but does not refuse) fails this oracle. MUTATION-CHECKED: deleting any one `KeySpec` from a TableSpec's `keys` tuple must flip that key's own accept-case in test_schema_conformance.py to a failure (either it now refuses a previously-valid instance, or the corresponding unknown-key refuse-case stops citing that key) — a passing suite that stays green after the deletion is a hollow test."
    gate: tester-unified
  - id: O4
    observable: "`ciu8 schema --json` (no `--file`) prints one JSON object keyed by `ciu.toml`, `ciu.site.toml`, `ciu.instance.toml`, `ciu.stack.toml`, `ciu.hosts.toml`, each value a document that passes `jsonschema.Draft202012Validator.check_schema`; `ciu8 schema --json --file ciu.stack.toml` prints only that document; every accept-case from O3's conformance suite for a table in a given file validates `True` against that file's emitted schema via `jsonschema.validate`, and every type/enum-violating refuse-case (excluding cases whose refusal is purely S3.8.3 referential, which S3.8.4 explicitly excludes from JSON Schema's claim) validates `False`."
    negative: "A hand-written JSON Schema literal not built from `ALL_TABLES`, a schema that is not valid Draft 2020-12 (fails `check_schema`), or one where an O3 accept-case fails JSON-Schema validation (the two generators have silently drifted) fails this oracle."
    gate: tester-unified
  - id: O5
    observable: "`tests/test_schema_conformance.py` is generated at collection time from `ciu8.schema_gen.build_conformance_cases(ALL_TABLES)` (via `pytest.mark.parametrize` over its returned list, not hand-typed per-key asserts); the generated case count is >= 2x the number of non-consumer-data KeySpecs across ALL_TABLES (at least one accept + one refuse per key, more for multi-member enums per S3.8.5's 'every closed-vocabulary value')."
    negative: "A test file with the same NAME but hand-authored, fixed-count assertions that do not scale when a KeySpec is added to schema_tables.py fails this oracle — the traceability check is `git grep -c 'def test_' tests/test_schema_conformance.py` returning at most a handful of hand-written wrapper functions, with the bulk of cases coming from the parametrize source being `build_conformance_cases(...)`."
    gate: tester-unified
  - id: O6
    observable: "`tests/test_spec_surface_conformance.py` implements the 3 LIVE extractors of this packet's 'S3.8.6 self-check harness' section (S3.4.7, S6.10, S13.1) exactly matching the packet's stated expected frozensets when run against the CURRENT `/workspaces/vbpub/ciu/docs/SPEC-V8.md` text, diffs each against the corresponding live surface in `ciu8.schema_tables`/`ciu8.schema_spec`, and FAILS on any asymmetric difference; the 6 STUB rule ids (S8.5.4, S16.8, S16.9, S18, S18.2, S18.4) are registered in the same harness table and SKIP (pytest.skip, never pass, never silently absent from the collected test list) with a reason string naming the proposal item that will implement them (V8-7 and V8-12 for S8.5.4, S16.8 and S16.9; V8-22 for S18; spread across V8-2, V8-9 and V8-12 for S18.2; V8-25 for S18.4)."
    negative: "A harness that hardcodes the expected set without re-deriving it from the live document text (defeats S3.8.6's own point — draft.5's T3-06 defect was exactly two hand-kept lists drifting), one that silently passes a STUB rule id instead of skipping it, or one that is not wired into the real gate's collected test count fails this oracle. MUTATION-CHECKED: temporarily deleting `secret_lint_allow` from the `[ciu]` TableSpec's keys must fail the S3.4.7 case; temporarily editing a local COPY of the SPEC-V8.md text (not the real file) to drop one `[ciu]` key from the extractor's input must also fail it — prove both directions."
    gate: tester-unified
  - id: O7
    observable: "`ciu8.schema_spec.TableSpec`, `ciu8.schema_spec.KeySpec`, and `ciu8.schema_tables.ALL_TABLES` are importable from a script OUTSIDE the `ciu8.schema_tables` module that appends one throwaway `TableSpec` (e.g. `path='project.smoke_test_extension'`) via the documented extension point (`ciu8.schema_tables.ALL_TABLES` is a `list`, or a `register()` function is provided — the packet requires the implementer's REPORT to state which) and confirms `build_validator`, `build_json_schema`, and `build_conformance_cases` all pick up the new table without any change to `schema_gen.py`."
    negative: "A registry that requires editing `schema_gen.py`'s generator functions to recognise a new table (the generators are not actually spec-driven, just structured to LOOK it), or one where the extension point is a private/underscore-prefixed name not meant for external import, fails this oracle — this is the forward-dependency proof V8-27/V8-2/V8-13 need."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "any table/key from proposal CIU-V8-TESTING-GATE-PROPOSAL.md §4.5 (groups A1-A7, B, D, E, F) needs a Kind, ScalarType, or KeySpec/TableSpec field this packet's schema_spec.py design does not already provide, AND the gap cannot be closed by adding a same-shaped enum member/scalar-type name (i.e. it needs a genuinely new STRUCTURAL capability, not a new value of an existing field) — stop, do not invent a new dataclass field ad hoc, write BLOCKED naming the exact table/key and what shape it would need"
  - "a table/key needs a referential or graph check (S3.8.3: 'a name that must resolve') to be expressed correctly and this packet's KeySpec/TableSpec format has no field for cross-key or cross-table conditions (S3.8.3 is explicitly `ciu check` stage 4's job, NOT this package's — if you find yourself wanting to write one, STOP: record the table/key/rule as a 'stage 4 forward item' in the REPORT instead of encoding it here)"
  - "the vendored tools/assay/assay-4.1.0.pyz sha256 does not match /workspaces/vbpub/cmru/tools/assay/assay-4.1.0.pyz.sha256's recorded digest (a1a5b09ca63370ed3b533b6a76089ea9f96e7dd0e612436e2e82f7cba688f931 as of this carve -- re-verify, do not trust this copy)"
  - "/workspaces/vbpub/run-gate.toml has no [environments.tester-unified] table, or its image is not tester-unified:local"
  - "the current /workspaces/vbpub/ciu/docs/SPEC-V8.md text for S3.4.7, S6.10, or S13.1 no longer matches the exact expected frozensets given in this packet's 'S3.8.6 self-check harness' section (the document moved since input_revision was frozen) -- do not silently adapt the extractor to the new text; write BLOCKED naming the diff, this is a D-<NNN>-worthy staleness signal for the carver/controller, not a mechanical fix"
  - "checkpoint clause (E-008): ARM at ~120k context tokens or ~60 tool calls (whichever comes first); CUT at the next coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster end; never on a red gate); repeat every ~40-55 calls; STOP when fewer than ~40 calls remain. At the cut: write a continuation brief to `nyxloom-trove/reports/ciu8-P001-BRIEF.md` (current state: which of Part A's 11 bootstrap items and which traceability-table groups are done/pending, exact file:line seams, last green/red gate run) and a self-authored `/compact`-style retention prompt to `nyxloom-trove/reports/ciu8-P001-COMPACT.md` (KEEP: current package/gate state, load-bearing file:line seams named above; DROP: resolved sub-threads), commit both, stop. Do NOT resume or fork yourself across a cut -- the controller dispatches a FRESH successor seeded with the brief (nyxloom LESSONS L23)."
---

# ciu8-P001 — Bootstrap ciu8 + v8 config schema (revision=8 gate, closed-key validators, `ciu8 schema --json`, S3.8.5/S3.8.6 conformance)

**Contract class:** Part A (bootstrap) is **2d** — mechanical mirroring of `ciu/`'s
own existing files, every target file's exact content given below. Part B
(schema/conformance) is **2d as carved** — the table-spec DATA FORMAT, the
three generator contracts, the S3.8.6 extraction functions, and three fully
worked `TableSpec` examples are fixed by this packet; the remaining work is
transcription of proposal §4.5's already-enumerated tables into that fixed
format, which is constrained/mechanical, not design-bearing. `tier:
implement-2` for the whole package (AUTHORING.md: only implement-1/
implement-2 are live routes today).

**A structural note on how you got here:** this file lives at
`ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md`,
inside a `ciu8/` subproject that did not exist before this carve. The carver
already ran `nyxloom init /workspaces/vbpub/ciu8` and hand-added this
package's gate declaration to `nyxloom-trove/nyxloom.toml` — that trove
scaffold (`nyxloom-trove/{nyxloom.toml,README.md,backlog.md,decisions.md,
roadmap.md,handoffs/,reports/,archive/,agent-logs/}`) is ALREADY COMMITTED
and is not part of your contract; do not recreate or restructure it. Your
Part A below bootstraps everything else `ciu8/` needs (packaging, the real
gate, the console script) — this is the FIRST implementer-authored content
in the subproject.

## BLOCKED rule

If a named contract item cannot be met exactly as specified here, or the
work requires touching a file outside `scope.touch`/outside `## Out of
scope / forbid` below, STOP: write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu8-P001-config-schema-v8-and-conformance-LOG.md`,
commit whatever is already green, and exit. Do not improvise a workaround,
do not invent a schema_spec.py field not in this packet, and do not touch a
forbidden path to make a gate pass.

## Context to read first, in order

1. This file, in full, before touching anything else.
2. `/workspaces/vbpub/ciu/docs/CIU-V8-HANDOFF-2026-09-03.md` — the design
   session's own handoff note (orientation: what draft.6/draft.7 changed,
   the operator's `ciu8` subproject decision).
3. `/workspaces/vbpub/ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.4 (the
   "What still needs to be built" table, V8-1's own row) and §4.5 in full
   (tables A1-A7, B, D, E, F — the exhaustive per-key enumeration this
   packet's transcription work in Part B item 5 walks top to bottom). Note:
   this file's own §4.5, NOT `SPEC-V8.md`, is where the "read §4.5" table
   enumeration lives — `SPEC-V8.md` has no §4.5 of its own (it uses `S<n>`
   numbering); §4.5's own citations point INTO `SPEC-V8.md`'s `S<n>.<m>`
   rules, which is what you actually transcribe field-by-field.
4. `/workspaces/vbpub/ciu/docs/SPEC-V8.md` §S3.8 (S3.8.1-S3.8.6, all six
   rules are packed into two lines under the `### S3.8` heading — read
   slowly) — this is the normative source for everything in Part B. Then
   read, in this order, every section S3.8.6 itself names plus every
   section a table you transcribe cites: S3.3 (top-level table closed set),
   S3.4 (`[project]`/`[ciu]`, S3.4.1-S3.4.7), S5.2 (`[service.<n>]`), S5.4
   (`[realization.<n>]`), S6.1-S6.10 (stack file, endpoints, bindings,
   hooks), S7.2/S7.3/S7.5/S7.6 (hosts/networks/bundles/layouts), S9.2
   (realness), S10.3/S10.6 (vault pointer, secrets store), S13.1-S13.2
   (governance), S14.2 (instance/generated/host files), S16.2-S16.5
   (testing tables), S18/S18.2/S18.4 (CLI/env/envelope — read for the S3.8.6
   harness's STUB rows, you do not implement these surfaces).
5. `/workspaces/vbpub/ciu/src/ciu/config_model.py` — v7's hand-written
   validators. Read (never import, never copy verbatim): `RESERVED_GLOBAL_TABLES`
   (line ~169), `validate_user_tables` (~818), `validate_service_registry`
   (~934), `validate_stack_shape`/`validate_stack_provisioning` (~1110-1360).
   This is the PER-TABLE, HAND-WRITTEN shape V8-1 replaces with ONE
   declarative spec + generators — read it to see what NOT to repeat, not
   as a template to port.
6. `/workspaces/vbpub/ciu/pyproject.toml`, `/workspaces/vbpub/ciu/run-gate.toml`,
   `/workspaces/vbpub/ciu/assay.toml`, `/workspaces/vbpub/ciu/cmru.toml`,
   `/workspaces/vbpub/ciu/run-ciu-tests.py`, `/workspaces/vbpub/ciu/.gitignore`,
   `/workspaces/vbpub/ciu/src/ciu/__init__.py`, `/workspaces/vbpub/ciu/src/ciu/__main__.py`
   — the v7 files Part A mirrors. Copy the SHAPE, substitute every `ciu` ->
   `ciu8`, `7.x` -> `8.0.0.dev`, `ciu-v` -> `ciu8-v`.
7. `/workspaces/vbpub/run-gate.toml` (central, vbpub root) — confirms
   `[environments.tester-unified]` is inherited, not redeclared per-project.
8. `/workspaces/vbpub/AGENTS.md` "Manual tester-unified gate runs — the four
   traps" section, and this session's own host-load-sharing rule (serial
   pytest under nice/ionice, ONE gate container at a time, `docker update
   --cpus=3` right after launch, no builds concurrent with suites — the host
   runs a production game server alongside these agents).
9. `/workspaces/vbpub/ciu/nyxloom-trove/handoffs/ciu-P07-assay-qualification.md`
   and `/workspaces/vbpub/ciu/nyxloom-trove/handoffs/ciu-P49-ciu89-probe-container-override-ciu90-governance-cpu-quota.md`
   — two examples of this project family's own handoff/process conventions
   (LOG/REPORT split, checkpoint clause, "real gate required" discipline).

## Out of scope / forbid

Do not edit anything under `/workspaces/vbpub/ciu/` (v7 stays maintenance-only;
its `config_model.py` is read-only reference material — reusable ideas are
copied and adapted into `ciu8/`, the FILE is never imported, and the v7 tree
itself is never modified by this package). Do not edit
`/workspaces/vbpub/nyxloom/` (the nyxloom product), `/workspaces/vbpub/run-gate-project/`
(only symlinked to, never edited), or `/workspaces/vbpub/cmru/` (only its
vendored `tools/assay/assay-4.1.0.pyz` is READ, to copy into `ciu8/`, never
edited in place). Do not modify `ciu8/nyxloom-trove/nyxloom.toml`,
`README.md`, `backlog.md`, `decisions.md`, or `roadmap.md` — those are
already committed trove scaffolding, not this package's contract (a genuine
need to change the gate declaration in `nyxloom.toml` is itself a BLOCKED
trigger, not a silent edit). Do not implement `ciu check`, `ciu instance
init`, resolution/merge logic (S3.1, S3.6), referential validation (S3.8.3,
`ciu check` stage 4+), or any verb beyond `ciu8 version` and `ciu8 schema
--json` — those belong to V8-2/V8-11/V8-13/V8-27 and later, and are listed
only so you recognise a request to build them as OUT of this package's
scope, triggering BLOCKED rather than silent scope creep.

## Branch / worktree

- Branch: `ciu8-p001-config-schema-v8-and-conformance`
- Worktree: `/workspaces/vbpub/.worktrees/ciu8-p001-config-schema-v8-and-conformance`

## Environment setup

This package has no live stack, no ciu instance, and no Docker dependency
for LOCAL fast iteration — only the final gate run needs tester-unified.

```bash
git -C /workspaces/vbpub worktree add \
  .worktrees/ciu8-p001-config-schema-v8-and-conformance \
  -b ciu8-p001-config-schema-v8-and-conformance main
cd /workspaces/vbpub/.worktrees/ciu8-p001-config-schema-v8-and-conformance/ciu8
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e .[test]
# fast local loop while building Part B (NOT the ship gate):
python3 run-ciu8-tests.py
```

The REAL gate (authoritative; AUTHORING.md rule 4 — never the cockpit venv
above) runs inside `tester-unified`, per this session's host-load rule
(check `docker ps` / `pgrep -af tester-unified-gate.sh` first; wait rather
than race another agent's container; `docker update --cpus=3` on your own
container right after it starts):

```bash
cd /workspaces/vbpub/.worktrees/ciu8-p001-config-schema-v8-and-conformance/ciu8
./run-gate.py --worktree /workspaces/vbpub/.worktrees/ciu8-p001-config-schema-v8-and-conformance ciu8
```

Read the verdict in a SEPARATE step (`cat` the printed evidence path, or
`ciu8/ciu-gate-evidence/ciu8/last` -> `verdict.json`) — never off a piped
tail (LESSONS L4).

## Implementation packet (normative)

### Part A — bootstrap the `ciu8` subproject skeleton

Mechanical. Every file below mirrors an existing `ciu/` file 1:1 in shape;
substitute names/versions as shown. Do these first, in order — Part B
cannot produce a green gate without them.

**A1. `pyproject.toml`** — mirror `/workspaces/vbpub/ciu/pyproject.toml`'s
`[build-system]` (identical pins: `setuptools==82.0.1`, `wheel==0.47.0`,
`setuptools_scm==10.0.5`), with:
- `[project] name = "ciu8"`, `requires-python = ">=3.11"`, `license = "MIT"`.
- `dependencies = []` (V8-1 needs only stdlib `tomllib`/`json`; do not add
  Jinja2/PyYAML/tomli_w — nothing in this package renders templates or
  writes TOML).
- `[project.optional-dependencies] test = ["pytest>=8", "pytest-cov>=5.0",
  "pytest-xdist>=3.6", "jsonschema>=4.18"]` (jsonschema is required for O4's
  `check_schema`/`validate` round-trip — it is a TEST dependency here, V8-1
  does not need it at runtime to only EMIT a dict).
- `[tool.setuptools.packages.find] where = ["src"]`.
- `[tool.setuptools_scm] root = ".."`, `tag_regex = "^ciu8-v(?P<version>[0-9].*)$"`,
  `git_describe_command = ["git", "describe", "--dirty", "--tags", "--long",
  "--match", "ciu8-v*"]`, `version_file = "src/ciu8/_version.py"`.
- `[project.scripts] ciu8 = "ciu8.cli:main"`.

**A2. `src/ciu8/__init__.py`** — byte-identical shape to
`ciu/src/ciu/__init__.py` (try `._version`, fall back to
`importlib.metadata.version("ciu8")`, then `"0.0.0+unknown"`), docstring
`"""ciu8 package."""`.

**A3. `src/ciu8/__main__.py`** — byte-identical shape to
`ciu/src/ciu/__main__.py`, `from .cli import main`.

**A4. `src/ciu8/cli.py`** — a compiling skeleton, this exact shape (fill in
imports from Part B once `schema_gen.py`/`schema_tables.py` exist; until
then `schema` MUST still parse args and fail loudly, not silently, if
called before Part B lands):

```python
"""ciu8 CLI entrypoint. V8-1 bootstrap scope: `version` and `schema --json`
only. SPEC-V8.md S18's full verb table (ciu init, instance, up, gate, ...)
lands with later checkpoint items -- do not add verbs here."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .schema_gen import build_json_schema
from .schema_tables import ALL_TABLES

_FILES = ("ciu.toml", "ciu.site.toml", "ciu.instance.toml", "ciu.stack.toml", "ciu.hosts.toml")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="ciu8")
    sub = parser.add_subparsers(dest="verb")
    sub.add_parser("version")
    schema_p = sub.add_parser("schema")
    schema_p.add_argument("--json", action="store_true", required=True)
    schema_p.add_argument("--file", choices=_FILES, default=None)

    args = parser.parse_args(argv)

    if args.verb == "version":
        print(f"ciu8 {__version__}")
        raise SystemExit(0)

    if args.verb == "schema":
        schemas = build_json_schema(ALL_TABLES)
        payload = schemas[args.file] if args.file else schemas
        print(json.dumps(payload, indent=2, sort_keys=True))
        raise SystemExit(0)

    parser.print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
```

**A5. `run-gate.py`** — a symlink, exactly mirroring `ciu/run-gate.py`. From
inside `ciu8/`, create a symlink named `run-gate.py` whose target, resolved,
is `/workspaces/vbpub/run-gate-project/run-gate.py` — the identical file
`ciu/run-gate.py` already points at (one shared parser, D-110). `ciu8/`
sits at the same depth under the vbpub checkout root as `ciu/` does, so a
relative target of the same shape as `ciu/run-gate.py`'s own resolves
correctly; verify with `readlink -f run-gate.py` after creating it.

**A6. `run-gate.toml`** — mirror `ciu/run-gate.toml`'s shape exactly:

```toml
# ciu8 project gate lanes -- parsed ONLY by run-gate.py (one parser, D-110).
# Environment facts come from the CENTRAL vbpub-root run-gate.toml (nearest
# ancestor); judgment policy lives in assay.toml [lanes.ciu8].
schema_version = 1

[lanes.ciu8]
kind = "assay"
assay_lane = "ciu8"
environment = "tester-unified"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-4.1.0.pyz"]
budget = "30m"

[lanes.ciu8.pins.assay]
version = "4.1.0"
sha256 = "tools/assay/assay-4.1.0.pyz.sha256"
```

**A7. `assay.toml`** — mirror `ciu/assay.toml`'s lane shape, `argv` pointing
at the new runner (A9 below):

```toml
schema_version = 2

[lanes.ciu8]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "run-ciu8-tests.py"]
env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }
env_passthrough = ["HOME", "PATH", "TERM", "LANG"]
budget = "20m"
allow_argv_append = false

[lanes.ciu8.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = []

[lanes.ciu8.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
require_branch = true
base = "origin/main"

[lanes.ciu8.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"
```

Note: `env_passthrough` drops `CGROUP_PARENT_DEV_BACKGROUND` (unlike
`ciu/assay.toml`) because V8-1 has no governance test reading it yet; add it
back only if/when a test genuinely needs it (do not cargo-cult ciu's own
passthrough list without a reason — same "no unexplained copy" discipline
this packet expects of you elsewhere). `unsafe_symlink_omissions` starts
empty; `ciu8/` has no unsafe fixture symlinks yet (unlike ciu's
`topos/tests/fixtures/...` — do not invent one).

**A8. `cmru.toml`** — mirror `ciu/cmru.toml` exactly except: `[project] id
= "ciu8"`, `description = "..."` (your own one-liner), `prefix = "ciu8-v"`,
`scm_dist = "ciu8"`; `[steps.run-tests]` argv `["cmru", "tester-gate",
"--cwd", ".", "--", "/opt/tester-venv/bin/python", "run-ciu8-tests.py"]`;
`[steps.push] argv` `--prefix "ciu8"`. Keep `[github]`/`[targets]` tables
byte-identical (same repo). Drop `CIU_RELEASE_NOTES`'s ciu-specific wording,
write ciu8's own.

**A9. `run-ciu8-tests.py`** — mirror `ciu/run-ciu-tests.py` exactly except
`--cov=ciu8` (not `--cov=ciu`) and the module docstring's project name;
keep `--dist loadfile` (same xdist-coverage-merge hazard applies to any
module `schema_gen`/hookkit-style dynamic loading might introduce later —
harmless to keep now, expensive to discover missing later).

**A10. Vendor the pinned judge.** Copy (not symlink — pins are
independently updatable per project, ciu8 should not silently follow ciu's
or cmru's future re-pins) `/workspaces/vbpub/cmru/tools/assay/assay-4.1.0.pyz`
and its `.sha256` into `ciu8/tools/assay/`. Verify before committing:
`sha256sum tools/assay/assay-4.1.0.pyz` must equal the digest already in the
`.sha256` file AND the literal digest named in this handoff's `escalate_if`
(`a1a5b09ca63370ed3b533b6a76089ea9f96e7dd0e612436e2e82f7cba688f931`) — a
mismatch on EITHER comparison is that escalate_if trigger, not a "just
re-copy it" fix. This pin (4.1.0) is chosen because it is the freshest
verified-working zipapp in the estate as of this carve (`ciu/`'s own pin is
stale at 3.2.0; `nyxloom/`'s is 4.0.0) — coincidentally the same version
SPEC-V8.md S16.3 names as ciu 8.0.0's own eventual minimum drivable judge,
which is a nice consistency but NOT the reason for this choice; re-verify
against `/workspaces/vbpub/cmru/tools/assay/` at dispatch time in case a
fresher pin has landed since this carve froze.

**A11. `.gitignore`** — minimal, accurate to what THIS package's code
actually produces (do not copy `ciu/.gitignore`'s full v7-specific list —
most of it names files no ciu8 code writes yet):

```
__pycache__/
*.egg-info/
.venv/
dist/
.coverage
coverage.json
.pytest_cache/
.hypothesis/
src/ciu8/_version.py
ciu-gate-evidence/
.assay/
```

**A12. `README.md`, `CHANGES.md`** — short. README: one paragraph stating
what `ciu8` is (the v8 rebuild per `/workspaces/vbpub/ciu/docs/SPEC-V8.md`,
pointing at the design docs, noting v7 `ciu/` stays authoritative until the
8.0.0 cutover). CHANGES.md: a single `## [Unreleased]` heading with one
bullet for this package's own additions once Part B lands (this project has
shipped no release yet, so there is no prior history to preserve).

### Part B — the v8 config schema (proposal V8-1, SPEC-V8.md §S3.8)

#### B1. The table-spec format — `src/ciu8/schema_spec.py`

This is the ONE declarative definition S3.8.4 requires. Every later
checkpoint item that touches configuration (V8-27's `[ciu] inherit`
closed list, V8-2's instance/host file tables, V8-13's `ciu check` stage 3,
V8-14's template-context consumer-data distinction) imports FROM here —
keep these names and shapes stable once you commit them (this is O7's
forward-dependency proof).

```python
"""ciu8.schema_spec -- the single declarative table-spec format every v8
declaration-file table is described in (SPEC-V8.md S3.8.4's "single
definition", I1). schema_gen.py's three generators (build_validator,
build_json_schema, build_conformance_cases) consume ONLY this format --
no other module may hand-write a per-table key list, an enum tuple, or a
JSON Schema fragment; every such fact lives exactly once, in
schema_tables.ALL_TABLES. Forward users: V8-27 ([ciu] inherit's closed
list), V8-2 (instance/generated/host file tables), V8-13 (ciu check stage
3 imports build_validator directly), V8-14 (KeySpec.consumer_data decides
what a template context exposes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["scalar", "enum", "literal", "list", "table", "map", "subtables"]
ScalarType = Literal[
    "str", "int", "bool", "name", "path", "url", "hostname", "dns_name",
    "envname", "duration", "size", "level", "version-floor",
]

# S1.4 TypedFact grammar (S5.6), one place -- every `list of TypedFacts`
# field (verify, provides, probe, binds.<local>.facts, ...) reuses this.
TYPEDFACT_PATTERN = (
    r"^(pg:(db|role|schema)/[a-z][a-z0-9_]*"
    r"|minio:(user|bucket)/[a-z][a-z0-9_]*"
    r"|vault:secret/[a-z0-9_.\-/]+(#[a-z][a-z0-9_]*)?"
    r"|http:/[^\s]*"
    r"|pki:issuer/[a-z][a-z0-9_]*)$"
)

# S1.4 `name` grammar -- the default key_pattern for any 'map'/'subtables'
# KeySpec that does not declare its own (e.g. envname for compose_env).
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


@dataclass(frozen=True)
class KeySpec:
    """One key of one TableSpec (or, for kind='map'/'subtables', the shape
    every dynamically-named entry must satisfy). `spec_rule` is REQUIRED,
    an exact `S<n>.<m>` citation -- an empty spec_rule is a transcription
    defect (test_schema_spec.py asserts every KeySpec.spec_rule matches
    `^S[0-9]+(\\.[0-9]+)*$`).

    kind='scalar'    -> `type` names a ScalarType; `pattern` overrides the
                        type's default only when SPEC-V8.md states one
                        explicitly (landscape_id's `^[a-z][a-z0-9-]{0,62}$`,
                        TypedFact fields' TYPEDFACT_PATTERN); `min`/`max`
                        bound a numeric `type='int'` (e.g. endpoint `port`:
                        min=1, max=65535) instead of a hand-rolled regex.
    kind='enum'      -> `enum` is the CLOSED tuple of legal literal strings.
    kind='literal'   -> `type` + `literal`: exactly one legal value (S3.4.1's
                        `revision`: type='int', literal=8).
    kind='list'      -> `item` is a KeySpec describing one element.
    kind='table'     -> `item` is a nested TableSpec: a FIXED (non-dynamic)
                        sub-table, e.g. `testing.lanes.<l>.requires`.
    kind='map'       -> `item` is a KeySpec describing the VALUE at each
                        dynamically-named key (e.g. `compose_env.<VAR>` ->
                        str); the key itself matches `key_pattern` (default
                        NAME_PATTERN).
    kind='subtables' -> this key opens a namespace of DYNAMICALLY-NAMED
                        CHILD TABLES, each shaped like a SEPARATE TableSpec
                        registered in ALL_TABLES whose `path` is
                        `f"{this_table.path}.{key.name}.<name>"` (e.g.
                        `ciu_stack.<svc>` has KeySpec("endpoints",
                        "subtables", ...) and ALL_TABLES separately carries
                        the TableSpec `ciu_stack.<svc>.endpoints.<e>`).
                        Prefer 'subtables' over deep 'map'-of-'table'
                        nesting so every genuinely-shaped table gets its
                        own top-level ALL_TABLES entry, uniform with how
                        top-level tables work (a design decision, not a
                        SPEC-V8.md requirement -- record it as such in your
                        REPORT if you choose otherwise).
    """
    name: str
    kind: Kind
    spec_rule: str
    type: ScalarType | None = None
    enum: tuple[str, ...] | None = None
    literal: object | None = None
    required: bool = False
    default: object | None = None
    pattern: str | None = None
    min: int | None = None
    max: int | None = None
    key_pattern: str | None = None
    item: "KeySpec | TableSpec | None" = None
    consumer_data: bool = False
    description: str = ""


@dataclass(frozen=True)
class TableSpec:
    """One table S3.8.1 closes. `path` is the dotted table path as it
    appears in the DECLARING file (before S3.6 re-rooting for a stack
    file); a literal `<name>` segment means "one dynamically-chosen key at
    this level" (S1.4 `name` grammar unless narrowed). `file` names which
    of the four DISTINCT closures this table belongs to -- `ciu.site.toml`
    is NOT a separate `file` value: S3.1.1 makes it a sparse override of
    exactly `ciu.toml`'s own table set, so build_json_schema (B2) derives
    `ciu.site.toml`'s schema FROM every `file="ciu.toml"` TableSpec by
    stripping `required` (this rule is decided HERE, by this packet -- do
    not re-litigate it in Part B).
    `closed=False` marks a table S3.8.1 calls "consumer data" outright
    (free-form beyond its own declared `keys`, e.g. `[registry.*]` beyond
    `postgresql.database`)."""
    path: str
    file: Literal["ciu.toml", "ciu.instance.toml", "ciu.stack.toml", "ciu.hosts.toml"]
    spec_rule: str
    keys: tuple[KeySpec, ...] = field(default_factory=tuple)
    closed: bool = True
    description: str = ""
```

#### B2. The three generators — `src/ciu8/schema_gen.py`

Exact signatures (fill in bodies; this is ordinary code construction against
the fixed format above, not contract discovery):

```python
@dataclass(frozen=True)
class SchemaError:
    rule: str        # e.g. "[S3.8]" or "[S3.4]" -- see message-format rules below
    message: str      # the full refusal string, verbatim format rules below
    table_path: str   # the CONCRETE table path, e.g. "ciu_stack.web.endpoints.http"
    key: str | None


def validate_table(instance: dict, spec: TableSpec, *, concrete_path: str) -> list[SchemaError]:
    """Validate ONE table instance against ONE TableSpec. Returns ALL
    violations (never raises, never stops at the first) -- mirrors v7's
    existing 'list every violation' convention (config_model.py's
    validate_user_tables). `concrete_path` is `spec.path` with every
    `<name>` segment replaced by the actual key used (for the message)."""


def validate_config(merged: dict, tables: list[TableSpec]) -> list[SchemaError]:
    """Walk every table `tables` declares (resolving 'subtables'/'map'
    dynamic entries against sibling TableSpecs in the same `tables` list by
    path-prefix match) and return the full combined list of SchemaErrors.
    Top-level tables not covered by any TableSpec in `tables` are a
    SchemaError too (S3.3's closed top-level set) -- EXCEPT this is only
    checked when `tables` includes a TableSpec whose path has no dot (a
    top-level table); do not hardcode S3.3's table list separately from
    schema_tables.py's own top-level entries."""


def build_json_schema(tables: list[TableSpec]) -> dict[str, dict]:
    """One JSON Schema document per FILE (see TableSpec.file's 4 values),
    keyed by filename string including the derived 'ciu.site.toml' (B1's
    rule). Draft 2020-12. Mapping, KeySpec.kind -> fragment:
    scalar str/name/path/url/hostname/dns_name/envname/duration/size/
      version-floor -> {"type": "string", "pattern": <the type's default
      regex, defined once in this module as a dict keyed by ScalarType,
      overridden by KeySpec.pattern when set>}
    scalar int -> {"type": "integer"} (+ "minimum"/"maximum" from min/max)
    scalar bool -> {"type": "boolean"}
    scalar level -> {"type": "string", "enum": ["live","seeded","simulated","mock"]}
    enum -> {"type": "string", "enum": list(KeySpec.enum)}
    literal -> {"const": KeySpec.literal}
    list -> {"type": "array", "items": <item's own fragment>}
    table -> {"type": "object", "properties": {...}, "required": [...],
      "additionalProperties": false} built recursively from the nested
      TableSpec
    map -> {"type": "object", "patternProperties": {key_pattern or
      NAME_PATTERN: <item fragment>}, "additionalProperties": false}
    subtables -> {"type": "object", "patternProperties": {NAME_PATTERN:
      <the CHILD TableSpec's own object fragment, found by path-prefix
      match in the same `tables` list -- reuse the 'table' logic above>}}
    A TableSpec's own top-level object: "type":"object", "properties" from
    its non-consumer_data keys, "required" from required=True keys,
    "additionalProperties": KeySpec fragments for any consumer_data=True
    key (permissive), else false when `closed=True`, true when
    `closed=False`."""


@dataclass(frozen=True)
class ConformanceCase:
    table_path: str    # TableSpec.path (may carry a <name> placeholder)
    key: str | None
    outcome: Literal["accept", "refuse"]
    instance: dict     # a MINIMAL valid instance of the table, with this
                        # case's value/omission applied to `key`
    expect_error_substring: str | None  # for outcome='refuse' only


def build_conformance_cases(tables: list[TableSpec]) -> list[ConformanceCase]:
    """S3.8.5: for every key, every closed-vocabulary value, one accepted
    and one refused example. Per KeySpec.kind:
    scalar   -> 1 accept (a type-valid literal) + 1 refuse (wrong type, or
                a pattern-violating value when `pattern` is set)
    enum     -> N accept (one per `enum` member) + 1 refuse (a value
                outside `enum`)
    literal  -> 1 accept (the literal) + 1 refuse (any other same-typed
                value -- for `revision`, use 7)
    required -> when required=True, 1 additional refuse case: the key
                entirely absent
    Every TableSpec ALSO gets exactly one table-level refuse case: a
    minimal valid instance plus one extra unrecognised key (only when
    `closed=True`), expect_error_substring =
    f"[S3.8] unknown key '<bogus>' in [{concrete_path}]"."""
```

**Message-format rules (verbatim where SPEC-V8.md gives a literal string):**
- Unknown key: `[S3.8] unknown key '<key>' in [<concrete table path>]`
  (S3.8.1's own example, `[S3.8] unknown key 'memroy_max' in [governance]`).
- `revision` violation ONLY: `[S3.4] config revision <got> is not 8`
  (S3.4.1's own example, verbatim — this is a narrow, explicitly-named
  special case for this ONE key, not a general per-table-spec-rule tagging
  scheme).
- Every other type/enum/pattern/required violation: tag `[S3.8]` (S3.8.2:
  "a violation is an ERROR"), message names the concrete table path, the
  key, and what was wrong (exact wording is your choice — not spec-mandated
  beyond the tag — but must be deterministic given the same input, since
  O3/O5's tests assert on it).

#### B3. Three fully worked `TableSpec` entries — `src/ciu8/schema_tables.py`

Transcribe these VERBATIM (they are carver-authored, already checked
against SPEC-V8.md's current text — do not re-derive them, just adapt
syntax if your `schema_spec.py` implementation needs it). They demonstrate:
a scalar+enum+pattern top-level table (`[project]`), a `subtables`-opened
dynamically-keyed nested table (`ciu_stack.<svc>.endpoints.<e>`), and a
mixed list/enum/duration/nested-table lane definition
(`testing.lanes.<l>`) — every KIND your transcription work in B4 needs is
exercised by at least one of these three.

```python
from .schema_spec import KeySpec, TableSpec, TYPEDFACT_PATTERN

PROJECT_TABLE = TableSpec(
    path="project", file="ciu.toml", spec_rule="S3.4.1",
    keys=(
        KeySpec("name", "scalar", "S3.4.1", type="name", required=True),
        KeySpec("revision", "literal", "S3.4.1", type="int", literal=8, required=True),
        KeySpec("log_level", "enum", "S3.4.1",
                enum=("DEBUG", "INFO", "WARN", "ERROR"), default="INFO"),
        KeySpec("landscape_id", "scalar", "S3.4.1", type="str",
                pattern=r"^[a-z][a-z0-9-]{0,62}$"),
    ),
)

CIU_STACK_SERVICE_ENDPOINT_TABLE = TableSpec(
    path="ciu_stack.<svc>.endpoints.<e>", file="ciu.stack.toml", spec_rule="S6.3",
    keys=(
        KeySpec("port", "scalar", "S6.3", type="int", required=True, min=1, max=65535),
        KeySpec("protocol", "enum", "S6.3",
                enum=("tcp", "udp", "http", "https"), default="tcp"),
        KeySpec("publish", "enum", "S6.3",
                enum=("instance", "host", "proxy"), default="instance"),
        KeySpec("host_port", "scalar", "S6.3", type="int", min=1, max=65535),
        KeySpec("host_bind", "scalar", "S6.3", type="str"),
        # `listen` is required on host_network services and refused
        # elsewhere (S6.3) -- a cross-key conditional, S3.8.3/`ciu check`
        # stage 8 territory (see this handoff's escalate_if #2). This
        # KeySpec only checks `listen`'s own shape when present.
        KeySpec("listen", "scalar", "S6.3", type="str"),
        KeySpec("allow_from", "list", "S6.3",
                item=KeySpec("_", "scalar", "S6.3", type="str")),
        KeySpec("path", "scalar", "S6.3", type="str"),
    ),
)

TESTING_LANE_TABLE = TableSpec(
    path="testing.lanes.<l>", file="ciu.toml", spec_rule="S16.5",
    keys=(
        KeySpec("kind", "enum", "S16.5",
                enum=("command", "assay", "sequence"), required=True),
        # `environment` required for command/assay, forbidden for
        # sequence -- cross-key conditional (S3.8.3, ciu check stage 12),
        # not enforced by this table-spec layer.
        KeySpec("environment", "scalar", "S16.5", type="name"),
        KeySpec("argv", "list", "S16.5",
                item=KeySpec("_", "scalar", "S16.5", type="str")),
        KeySpec("assay_lane", "scalar", "S16.5", type="str"),
        KeySpec("lanes", "list", "S16.5",
                item=KeySpec("_", "scalar", "S16.5", type="name")),
        KeySpec("stop_on", "enum", "S16.5", enum=("FAIL", "never"), default="FAIL"),
        KeySpec("description", "scalar", "S16.5", type="str"),
        KeySpec("clean_tree", "scalar", "S16.5", type="bool", default=True),
        KeySpec("budget", "scalar", "S16.5", type="duration"),
        KeySpec("required_env", "list", "S16.5",
                item=KeySpec("_", "scalar", "S16.5", type="envname")),
        KeySpec("artifacts", "list", "S16.5",
                item=KeySpec("_", "scalar", "S16.5", type="str")),
        KeySpec("requires", "subtables", "S16.5",
                description="child TableSpec testing.lanes.<l>.requires (B4 group A7)"),
        KeySpec("require_provenance", "scalar", "S16.5", type="bool", default=False),
        KeySpec("resources", "subtables", "S16.5",
                description="child TableSpec testing.lanes.<l>.resources (B4 group A7)"),
        KeySpec("enabled", "scalar", "S16.5", type="bool", default=True),
    ),
)

# B4's transcription work appends every remaining table here. One list,
# grouped with `# --- <proposal §4.5 group> ---` banner comments in the
# SAME order as the proposal table, so a reader can diff against it
# directly.
ALL_TABLES: list[TableSpec] = [
    PROJECT_TABLE,
    CIU_STACK_SERVICE_ENDPOINT_TABLE,
    TESTING_LANE_TABLE,
    # --- A1 continued: [project.registry], [project.health],
    #     [project.compose_env.<VAR>], [project.control.<flag>], [ciu],
    #     [ciu.instances] ---
    # --- A2: [service.<n>] ---
    # --- A3: [realization.<n>] ---
    # --- A4: [network.<n>] ---
    # --- A5: [bundles.<b>], [layouts.<l>], [realness] ---
    # --- A6: [vault], [registry], [governance] ---
    # --- A7 continued: [testing], [testing.externals.<n>],
    #     [testing.judge], [testing.environments.<e>],
    #     testing.lanes.<l>.requires, testing.lanes.<l>.resources ---
    # --- B: [ciu.instance], [ciu.instance.generated], [ciu.host] ---
    # --- D: [hosts.<h>] ---
    # --- E continued: ciu_stack.<svc> itself, .binds.<local>,
    #     .hostdir.<purpose>, .configfile.<name>, .secrets.<key>, [hooks] ---
    # --- F: secrets file ---
]
```

`ALL_TABLES` is a plain module-level `list` (not a tuple, not hidden behind
a private name) specifically so O7's extension proof (an external script
`ALL_TABLES.append(...)`) works without a registration function — this is
the extension point; state this choice explicitly in your REPORT.

#### B4. Transcription work — every remaining table

For each row below: read the cited `S<n>.<m>` rule(s) in
`/workspaces/vbpub/ciu/docs/SPEC-V8.md` (verbatim source of truth for exact
key names/types/defaults/vocab) and the matching row of
`/workspaces/vbpub/ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.5 (a
pre-digested key|type|reason|owner|example table for the same rule — use it
to catch a key SPEC-V8.md's prose buries), then add one `TableSpec` to
`ALL_TABLES` using B3's three examples as your pattern. A `<sub-table>`
free-form escape (S6.2's "consumer data goes in a sub-table") is
`closed=False` with no `keys` (or only the few keys the rule DOES name, if
any); do not enumerate consumer-owned sub-table contents.

| # | table(s) | SPEC-V8.md rule | proposal §4.5 group |
|---|---|---|---|
| 1 | `project.registry`, `project.health`, `project.compose_env.<VAR>` (map), `project.control.<flag>` (map) | S3.4.2-S3.4.4 | A1 |
| 2 | `ciu`, `ciu.instances` | S3.4.7, S14.6 | A1 |
| 3 | `service.<n>` | S5.2 | A2 |
| 4 | `realization.<n>` (all 3 kinds: `ciu_stack`/`external`/`joined` share one TableSpec with per-kind optional keys, OR three TableSpecs sharing `path="realization.<n>"` distinguished by a `kind` discriminant in your validator — your choice, record it) | S5.4 | A3 |
| 5 | `network.<n>` | S7.3 | A4 |
| 6 | `bundles.<b>`, `layouts.<l>`, `realness` | S7.5, S7.6, S9.2 | A5 |
| 7 | `vault`, `registry` (closed=False beyond `postgresql.database`), `governance` (reuse a single `RK` tuple constant — see B5 below, O6 needs it) | S10.3, S3.4.6, S13.2 | A6 |
| 8 | `testing`, `testing.externals.<n>`, `testing.judge`, `testing.environments.<e>` | S16.2-S16.4 | A7 |
| 9 | `testing.lanes.<l>.requires`, `testing.lanes.<l>.resources` (the two `subtables` children `TESTING_LANE_TABLE` opens) | S16.5.1, S16.5.3 | A7 |
| 10 | `ciu.instance`, `ciu.instance.generated`, `ciu.host` | S14.2 | B |
| 11 | `hosts.<h>` (file `ciu.hosts.toml`) | S7.2 (S7.2.4 is the `ciu host enroll` VERB, out of scope — the key set it eventually WRITES into is unchanged from S7.2's existing list) | D |
| 12 | `ciu_stack.<svc>` itself (the parent `TESTING_LANE_TABLE`-style table `CIU_STACK_SERVICE_ENDPOINT_TABLE` is its `endpoints` child); `.binds.<local>`, `.hostdir.<purpose>`, `.configfile.<name>`, `.secrets.<key>`, `[hooks]` (this is B5's live S6.10 surface — transcribe it precisely, see below), `[ciu_stack.secrets.<key>]` | S6.2, S6.4, S6.8-S6.10 | E |
| 13 | secrets file table(s) (`secrets.<realization>.<svc>.<key>` etc.) | S10.6 | F |

#### B5. The S3.8.6 self-check harness — `tests/test_spec_surface_conformance.py`

This is the hardest, most novel part (T3-06's defect class: two lists kept
equal by hand, silently drifting). Build it as a small table of `(rule_id,
extractor, live_or_stub)` rows, driven by pytest so each rule_id is its own
collected test (never one giant test hiding 9 sub-checks).

**Reading the document.** `SPEC-V8.md` stays authoritative at
`/workspaces/vbpub/ciu/docs/SPEC-V8.md` until the 8.0.0 promotion (V8-21,
out of scope here). Compute the path relative to the checkout root, never
hardcode the absolute `/workspaces/vbpub` prefix (a different clone/CI
mount would break it): `Path(__file__).resolve().parents[2] / "ciu" /
"docs" / "SPEC-V8.md"` (two `.parents` up from `ciu8/tests/` reaches
`ciu8/`'s parent, i.e. the vbpub checkout root — verify this resolves
correctly from wherever your actual test file ends up and adjust the
`.parents` index if your directory depth differs; the ASSAY snapshot for
this lane is `repository-minus-unsafe-symlinks`, i.e. the WHOLE monorepo,
so the file is present in the gate's sandbox the same way `ciu/`'s own
cross-references into sibling files already work in this estate).

**3 LIVE rule ids** — build a real extractor, a real implementation-surface
getter, assert equality (symmetric difference empty):

1. **S3.4.7** (`[ciu]` closed key list). Extractor: locate the exact
   substring `` "S3.4.7 `[ciu]`:" `` in the document text, take everything
   up to (not including) the next occurrence of `` "`[ciu.instances]`:" ``,
   apply `` r"`([a-z][a-z0-9_]*)`\s*\(" `` over that slice, collect group 1
   into a `frozenset`. Expected result AS OF THIS CARVE (input_revision
   `b19880bc`) — assert your extractor produces exactly this before wiring
   it to anything else:
   `frozenset({"standalone_root", "inherit", "require_fqdn",
   "auto_connect_network", "exit_on", "user_tables", "registry_validator",
   "secret_lint_allow"})`.
   Implementation surface: `{k.name for k in CIU_TABLE.keys}` where
   `CIU_TABLE` is the `TableSpec(path="ciu", ...)` from B4 item 2.
2. **S6.10** (`[hooks]` closed key set: the 4 phase/allow-list names, PLUS
   the 5 entry-table keys). Extractor, two unions: (a) locate `` "-
   `[hooks]`:" `` up to `` "- `[governance]`:" ``, apply `` r"`(pre_secrets|
   pre_compose|post_compose|env_allow)`" `` (a closed alternation — these
   four names are the ones the rule's own prose calls "the three phase
   lists ... and `env_allow`"; if the document ever renames one, this
   alternation must be updated by hand, which is EXACTLY the coupling
   S3.8.6 exists to catch, not a design flaw); (b) within the same slice,
   after the literal substring `"exactly these keys:"` and before
   `"Any other key"`, apply `` r"`([a-z][a-z0-9_]*)`\s*\(" ``. Expected
   result: `frozenset({"pre_secrets", "pre_compose", "post_compose",
   "env_allow", "run", "service", "provides", "secrets", "inputs"})`.
   Implementation surface: union of `{k.name for k in HOOKS_TABLE.keys}`
   and `{k.name for k in HOOKS_ENTRY_TABLE.keys}` (the two TableSpecs B4
   item 12 asks you to transcribe for `[hooks]`).
3. **S13.1** (governance `RK` vocabulary). Extractor: locate `"### S13.1
   Vocabulary"` up to `"### S13.2"`, within the sentence starting
   `"The **resource key set** \`RK\` = "` apply `` r"`([a-z][a-z0-9_]*)`" ``
   restricted to that sentence (stop at its terminating period). Expected
   result: `frozenset({"memory_max", "memory_swap_max", "memory_high",
   "memory_low", "memory_min", "cpu_weight", "cpu_max", "io_weight",
   "pids_max"})`. Implementation surface: define `RK: frozenset[str]` ONCE
   in `schema_spec.py` (this exact frozenset) and have the `governance`
   TableSpec's per-key KeySpecs (B4 item 7) and this test BOTH read it —
   this is the cross-consistency proof O7 also wants: one source, two
   consumers.

**6 STUB rule ids** — register in the SAME table, each wired to a
surface-getter that raises `NotImplementedError`, and the test for it calls
`pytest.skip(reason=...)` BEFORE calling the getter (never after — never
let the skip depend on catching the NotImplementedError, that would hide a
getter that was accidentally implemented incorrectly rather than left
absent). Reasons, exact proposal-item pointers:
- **S8.5.4** (gate probe/assumed vocab) — `pytest.skip("owned by V8-7 (init
  graph/waves/gates) and V8-12 (ciu gate), not V8-1")`
- **S16.8** (outcome vocabulary) — `pytest.skip("owned by V8-12 (ciu gate)")`
- **S16.9** (LaneResult fields) — `pytest.skip("owned by V8-12 (ciu gate)")`
- **S18** (CLI verb table) — `pytest.skip("owned by V8-22 (verb
  dispositions) and spread across nearly every later checkpoint item")`
- **S18.2** (env vars CIU reads) — `pytest.skip("spread across V8-2
  (instance/state root), V8-9 (secrets), V8-12 (gate) -- no single owner")`
- **S18.4** (JSON envelope `api` names) — `pytest.skip("owned by V8-25
  (query surface and artifact headers)")`

A rule id that is neither LIVE nor in this STUB list is a bug in this test
file — assert the harness's own rule-id set equals exactly these 9 (the
S3.8.6 sentence's own citation: S3.4.7, S6.10, S8.5.4, S13.1, S16.8, S16.9,
S18, S18.2, S18.4) as a meta-test, so a future edit cannot silently drop
one.

#### B6. Bounds and provenance

- Coverage floor: 100% line AND branch (mirrors `ciu/`'s own convention,
  `assay.toml`'s `fail_under = 100.0`/`require_branch = true` above — not
  independently invented, the SAME estate-wide bar).
- Judge floor: `>=4.1.0` (A10's vendored pin; matches SPEC-V8.md S16.3's own
  eventual number for unrelated reasons — do not conflate "this dev-gate's
  judge pin" with "the runtime `testing.judge.version` floor a v8 project
  declares", which is V8-12's concept, not built here).
- `nyxloom lint`'s L10 size thresholds: default (warn 10000 / error 18000
  "tokens", chars/4) — this handoff itself is sized against that; your own
  future handoffs in this trove inherit the same default unless
  `nyxloom-trove/nyxloom.toml` gains a `[lint.l10]` override (do not add one
  without a reason).

#### B7. Traceability (fill in with real file/test names in your REPORT)

| work item | owner module | oracle | fixture/test | controlled break |
|---|---|---|---|---|
| bootstrap skeleton | pyproject.toml, src/ciu8/*, run-gate.*, assay.toml, cmru.toml | O1 | `./run-gate.py ciu8` green | delete `[project.scripts]` -> `ciu8` not found |
| `revision=8` gate | schema_tables.PROJECT_TABLE, schema_gen | O2 | test_schema_conformance.py revision cases | change literal to 7 -> both 7 and 8 accepted |
| closed-key validators | schema_gen.validate_config | O3, O5 | test_schema_conformance.py (generated) | remove one KeySpec -> its cases fail per O3's mutation clause |
| `ciu8 schema --json` | schema_gen.build_json_schema, cli.py | O4 | test_cli.py + test_schema_gen.py round-trip | hand-edit one JSON fragment -> round-trip test catches divergence from validator |
| S3.8.5 conformance generator | schema_gen.build_conformance_cases | O5 | test_schema_conformance.py parametrize source | hardcode cases instead of generating -> O5's count check fails |
| S3.8.6 self-check | tests/test_spec_surface_conformance.py | O6 | 3 live + 6 stub rows | mutate SCHEMA or a local doc-text copy -> live rows fail |
| extension point | schema_tables.ALL_TABLES (plain list) | O7 | external append-and-rebuild script | privatize the name -> O7's external import fails |

### Degrees of freedom

Private helper function names inside `schema_gen.py`; the exact prose
wording of non-mandated refusal messages (B2's "message-format rules"
section pins the two MANDATED ones); whether `realization.<n>`'s three
kinds share one TableSpec with a `kind` discriminant or three separate ones
(B4 row 4); file layout within `src/ciu8/` beyond the module names this
packet fixes (`schema_spec.py`, `schema_gen.py`, `schema_tables.py`, `cli.py`
are fixed names — you may NOT rename or merge them, since O7 and B5 name
them directly). Nothing about serialized shapes (KeySpec/TableSpec field
names, the JSON Schema mapping table, the two mandated message formats, the
`ALL_TABLES` list-not-tuple decision) is a degree of freedom.

## Test constraints (mandatory — read before writing any test)

- **A.** Nothing may make a verdict depend on how fast the machine is: no
  `time.sleep`+assert, no wall-clock deadlines. This package has no
  concurrency to wait on; if you find yourself wanting a sleep, you are
  solving the wrong problem.
- **B.** Nothing may depend on test order, worker assignment, or a sibling
  test: no mutated process-global state (`os.environ`, module attributes)
  left unrestored; fresh `tmp_path` per test that needs a file.
- **C.** No hollow tests: assert the CONTRACT (a specific refusal string, a
  specific accepted/rejected value), never just "no exception raised".
- **D.** No coverage-exclusion pragmas on changed lines (nyxloom's gate
  rejects them; the literal token anywhere on a line is enough to trigger
  rejection, including inside a comment describing the rule).
- **E.** Network/clock/filesystem are inputs: reading `SPEC-V8.md` from a
  checkout-relative path (B5) is filesystem access to a COMMITTED, static
  file — not a live/mutable input — so it does not violate this rule; do
  not add a real network call or a `datetime.now()`-dependent assertion
  anywhere in this package.
- For every test you write, ask: could this flip its verdict on a slower
  machine, a different worker, or a different run order? If yes, it is not
  an oracle yet — fix it before committing.

## Process requirements

- Fresh implementer, zero prior context beyond this document and the files
  it names.
- **Real gate required**: `./run-gate.py --worktree <path> ciu8`. Read the
  verdict in a separate step, never off a piped tail.
- LOG per commit (self-hash rule); REPORT with per-oracle evidence (O1-O7),
  including which extension mechanism you chose for O7 and which
  `realization.<n>` modeling choice you made for B4 row 4.
- Checkpoint clause: see `escalate_if`'s final entry — it is normative, not
  a suggestion.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01RJ3wqoyy8ZzHmj7ZK1qEnJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge, per the `carve`
  skill's own rule (never a fork for this review).
- **Host is shared** with a production game server (8 cores): serial pytest
  under nice/ionice, ONE gate container at a time across all agents on this
  host, `docker update --cpus=3` right after your container launches, no
  builds concurrent with suites.
- Closing discipline: claim only what you ran, with the real numbers/output
  from the actual gate run — a receipt is evidence to check, not truth
  (AUTHORING.md rule 7); the reviewer verifies against real `git log/status/
  diff`, not your REPORT's prose.

**BLOCKED:** emit for any `escalate_if` trigger with exact evidence — the
extracted text, the diffing sets, the exact table/key that needed a
structural capability this packet does not provide. Never invent a
schema_spec.py field, a referential check inside the table-spec layer, or a
silent adaptation of the S3.8.6 extractors to text that has moved since
`input_revision`.
