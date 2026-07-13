# P76 REPORT — CIU Stack Metadata (Detection + Frame Fields)

## What was built

A read-only CIU stack metadata detection layer that teaches groop about
`ciu`-managed containers. On a host with `ciu`-deployed stacks, each
container's `Entity` now carries a `CiuMeta` attachment saying which stack
and deploy phase it belongs to, and whether that was detected from labels (a
guarantee) or inferred from naming patterns (a heuristic).

### Detection functions in `collect/dockerjoin.py`

**Label-confirmed detection** (`detect_ciu_from_labels`): Parses the three
proposed `ciu.*` labels from Docker `Config.Labels`:
`ciu.managed="true"` (unambiguous marker), `ciu.stack` (stack directory name),
and `ciu.phase` (`phase_<N>`). The `ciu.phase` label is parsed numerically:
`phase_2` → `phase=2, phase_raw="phase_2"`. Malformed values (`phase_`,
`phase_abc`, absent) yield `phase=None` — never a crash, never sorts as
phase 0.

**Inferred (fallback) detection** (`detect_ciu_inferred`): When no `ciu.*`
labels exist, checks whether the container's `com.docker.compose.project`
matches a known stack directory name (from configurable `stack_roots`) AND
its container name matches ciu's anchored `^<project>-<env>-<name>$` pattern
(CIU-DEPLOY.md S7.8). This is a heuristic, explicitly marked `source="inferred"`
and never conflated with `source="label"`.

**Two tiers, never merged**: Every `CiuMeta` carries a `source` discriminator
— `"label"` (guarantee) or `"inferred"` (heuristic). A consumer can always
tell "ciu says so" from "groop guessed".

**Honest absence**: Three distinguishable states — `ciu=None` (container not
ciu-managed or unreadable), `CiuMeta` with values (ciu-managed with data), and
the inspect-failure path where both `docker` and `ciu` are `None` (unreadable).

### New dataclass in `model.py`

`CiuMeta` with fields: `stack: str|None`, `phase_raw: str|None`,
`phase: int|None`, `source: str` (literal `"label"` | `"inferred"`).
Attached to `Entity.ciu` as an optional field. Includes full serialization
(`ciu_to_jsonable`/`ciu_from_jsonable`) through the canonical model
serializers. Existing frames without `ciu` fields parse unchanged —
no schema break, no golden fixture regeneration needed.

### Configuration in `config.py`

`CiuConfig` with `stack_roots: tuple[Path, ...]` (default empty). When
configured in `config.toml` under `[ciu]`, the named directories' subdirectory
names become known stack names for the inference heuristic. Label-confirmed
detection works unconditionally without any configuration.

### `enrich_entities` extension

The existing `enrich_entities` entry point now accepts an optional
`known_stack_roots: set[str] | None` parameter. CIU detection runs as a
second pass after docker inspect parsing: labels first, inference fallback.
No new subprocess, no `ciu` invocation, no TOML parsing — exactly as the
handoff specifies.

### Documentation updates

- **CONTRACTS.md**: Added `CiuMeta` to the entity model section (§2).
- **ARCHITECTURE.md**: Added CIU detection dataflow arrow and updated module
  map.
- **STATUS.md**: Updated v2 summary percentage to reflect CIU metadata done;
  CIU grouping/actions remains in Not Implemented (deferred).
- **ROADMAP.md**: Marked P76 as implemented.

## Deviations from the handoff doc

None. All named contracts are met as specified.

## Proposed contract changes

None. The new `CiuMeta` dataclass is additive — no interfaces or contracts
were modified. `Entity` gained an optional `ciu` field; existing frames
without it deserialize with `ciu=None`.

## Test evidence

**Environment:** Python 3.14.6, pytest 8.4.2. No live docker or ciu needed
— all tests are fixture-driven.

### Focused P76 tests (55 tests, 0 failures)

```bash
$ cd /workspaces/vbpub/.worktrees/groop-p76-ciu-stack-metadata
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_ciu_metadata.py -q
55 passed, 1 warning in 0.19s
```

| Oracle | Test class | Count | Status |
|---|---|---|---|
| 1. Label-confirmed detection | `TestDetectCiuFromLabels` | 9 | Pass |
| 2. Inferred detection | `TestDetectCiuInferred` | 7 | Pass |
| 3. Negative: non-ciu container | `TestNegativeNonCiu` | 3 | Pass |
| 4. Phase ordering (numeric) | `TestPhaseOrdering` | 3 | Pass |
| 5. Malformed phase labels | `TestMalformedPhase` | 4 | Pass |
| 6. Grouping correctness | `TestGroupingCorrectness` | 2 | Pass |
| 7. Frame-schema compatibility | `TestFrameSchema` | 6 | Pass |
| — Phase parsing unit | `TestParsePhase` | 10 | Pass |
| — enrich_entities integration | `TestEnrichEntitiesIntegration` | 5 | Pass |
| — Config integration | `TestCiuConfig` | 3 | Pass |
| — Honest absence (3 states) | `TestHonestAbsence` | 3 | Pass |

### Existing tests remain green (11 tests, 0 failures)

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_dockerjoin.py groop/tests/test_model_registry.py -q
11 passed, 1 warning in 0.29s
```

### py_compile clean on all changed/new files

```bash
$ python3 -m py_compile \
  groop/src/groop/model.py \
  groop/src/groop/config.py \
  groop/src/groop/collect/dockerjoin.py \
  groop/tests/test_ciu_metadata.py
# no output = clean
```

### git diff --check

```bash
$ git diff --check
# no output = clean
```

## Known gaps / open items

- **Golden frames**: No golden frame regeneration needed — all changes are
  purely additive (new optional `ciu` field on `Entity`, `None` when absent).
  Existing pre-P76 fixtures deserialize with `ciu=None` as verified by
  `test_pre_p76_fixture_still_parses`.
- **Inferred detection requires configuration**: Without `[ciu] stack_roots`
  in `config.toml`, inference is disabled. This is by design — a host not
  running ciu shouldn't produce false positives. Documentation in
  `config.toml` examples would help operators discover this.
- **Live docker inspection not tested**: The handoff correctly restricts
  testing to fixtures. A controller-side live check against this host's real
  ciu-managed containers was not run (no ciu-managed containers on this host).
- **TUI group-by-stack rendering**: Explicitly deferred to a successor package
  per the handoff.
- **ciu-gated actions**: Explicitly deferred to a successor package per the
  handoff.
- **Label schema in ciu itself**: The proposed `ciu.managed`/`ciu.stack`/`ciu.phase`
  label schema (TUI-SPEC §4.3) has not been implemented in the `ciu` package.
  This is a cross-package request: `ciu` should apply these three labels at its
  compose-render step (S8.3 step 13 in `ciu/docs/CIU.md`). Until then, only the
  inference tier will produce results on actual deployments.

## Label schema request (for ciu maintainer)

Per TUI-SPEC §4.3, the following three labels should be applied by `ciu` at
its compose-render step:

| Label | Value | Purpose |
|---|---|---|
| `ciu.managed` | `"true"` | Unambiguous ciu-pipeline marker |
| `ciu.stack` | stack directory name | Groups containers by ciu -d invocation |
| `ciu.phase` | `phase_<N>` | Deploy phase ordering (optional for non-ciu-deploy) |

These are data `ciu` already has at render time. Applying them would let
groop's label-confirmed detection produce authoritative CIU metadata today.
