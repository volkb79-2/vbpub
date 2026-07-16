# P76 - CIU Stack Metadata (Detection + Frame Fields)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P57 (merged), P59 (merged)
> **Base:** main after P58 merge
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** a named contract cannot be met as specified; correct detection would require running `ciu` as a subprocess or parsing `ciu.global.toml` from the collector

<!--
CARVE NOTE (2026-07-13, frontier pass #2 on P58 v4, controller-workflow-v2 §8):
Carve source: ROADMAP-DRIVEN (source 2). ROADMAP.md "Optional plugins / future
surfaces" lists "GPU, ZFS, CIU grouping/actions". ZFS drained as P71, GPU carved
as P74 -- **CIU is the last un-carved item in that bucket**, and the bucket is the
one §8 exists to stop us from orbiting past. The area is NOT cold: TUI-SPEC.md §4.3
("CIU-managed stack integration") already specifies detection, the label schema,
and the numeric-phase-ordering rule, and `ciu/docs/CIU.md` + `ciu/docs/CIU-DEPLOY.md`
resolve from this repo root (they are real paths in vbpub, not a sibling workspace
-- verified at carve time; cf. the P69 cross-repo-cite lesson in the deciding log).

Scope decision: this is the METADATA slice only -- detection plus frame fields.
TUI group-by-stack rendering and ciu-gated batch actions (TUI-SPEC §4.3
"Grouping/selection" and "Gating ops through ciu") are deliberately deferred to
successors; they need this metadata to exist first. Carving all three at once
would produce exactly the oversized package that keeps failing review.
-->

## Goal

Teach the collector to recognize `ciu`-managed containers and attach stack/phase
metadata to their entities, so that "these 14 containers are one stack, in deploy
phase 2" becomes a fact in the frame instead of something an operator infers from
container names. This is the metadata foundation the TUI-SPEC §4.3 grouping and
ciu-gated-action surfaces are both blocked on.

## Dependency And Workflow

- Extends the existing docker enrichment. `topos/src/topos/collect/dockerjoin.py`
  **already** runs `docker inspect` and parses `Config.Labels` (it lifts
  `com.docker.compose.project` into `DockerMeta.compose_project`). CIU detection is
  a small extension of that existing parse -- **not** a new subprocess, not a new
  collection pass, and not a `ciu.global.toml` reader.
- Branch: `feat/topos-p76-ciu-stack-metadata`
- Worktree: `.worktrees/topos-p76-ciu-stack-metadata`
- Touch only `topos/**`; write P76-LOG.md/P76-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`topos/README.md` (Workflow protocol), this handoff, `topos/TUI-SPEC.md` §4.3
(the authoritative spec for this package -- read it first and in full),
`topos/src/topos/collect/dockerjoin.py`, `topos/src/topos/model.py` (`DockerMeta`),
`topos/src/topos/registry.py`, and P71's ZFS provider + its tests as the exemplar
for an honest-absence provider. For the ciu domain: `ciu/docs/CIU-DEPLOY.md` S7.1
(numbered `[deploy.phases.phase_<N>]` tables) and S7.8 (the anchored
`^<project>-<env>-<name>$` container-name pattern). Do not read UI, DAMON/BPF,
daemon, or record/replay code.

## Required Contracts

### Detection (two tiers, never conflated)

TUI-SPEC §4.3 defines a label schema that `ciu` **has not shipped yet**. So
detection has two tiers and the frame must always say which one produced a row:

1. **Label-confirmed.** The container carries `ciu.managed="true"`, and optionally
   `ciu.stack` (stack directory name, e.g. `infra/redis-core`) and `ciu.phase`
   (`phase_<N>`). Parsed from the same `Config.Labels` dict `dockerjoin` already
   reads. This is a guarantee.
2. **Inferred (fallback, the only tier that works today).** No `ciu.*` labels, but
   the container's `com.docker.compose.project` matches a known stack directory name
   **and** its name matches ciu's anchored `^<project>-<env>-<name>$` pattern. This
   is a heuristic, explicitly called such in TUI-SPEC §4.3.

**Hard contract: the two tiers are never merged into one boolean.** Every
ciu-annotated entity carries a `source`/`confirmed` discriminator distinguishing
label-confirmed from inferred. A consumer must be able to tell "ciu says so" from
"topos guessed". Collapsing these is a merge blocker.

### Honest absence (the P71/P74 rule, restated because it is the trap here)

A host with **no ciu-managed containers** and a host where **ciu metadata could not
be read** must not render identically, and neither may render like a ciu-managed
host with empty fields. Three distinct states -- not-ciu-managed, ciu-managed, and
unknown/unreadable -- and the frame must express all three. `None`-valued fields are
omitted, never serialized as empty strings that read like real values.

### Phase ordering (the specific numeric trap)

`ciu-deploy` executes phases in **numeric**, not lexicographic, order
(`CIU-DEPLOY.md` S7.1): `phase_1` runs before `phase_10`, and `phase_2` runs before
`phase_10`. Any ordering, sorting, or comparison this package exposes must be
numeric. A string sort putting `phase_10` before `phase_2` is a merge blocker, and
it is the single most likely defect in this package.

- Parse `phase_<N>` into an integer phase number; keep the raw label string too.
- A malformed phase label (`phase_`, `phase_x`, `phase_-1`, absent) is not a crash:
  it is an unknown phase, recorded as such, and it must not sort as phase 0.

### Surface

- Extend `DockerMeta` (or add a parallel `CiuMeta` attached to `Entity`) with the
  ciu fields; follow whichever shape keeps the existing frame schema additive.
  Existing frames without ciu fields must still parse -- **no frame-schema break**,
  and existing fixtures must not need regeneration.
- The stack roots used by the inference heuristic are configuration, not a
  hardcoded absolute path. Default to a sensible discovery rule and make it
  overridable through the existing config mechanism; do not add a fixture/test-only
  CLI flag (the P45 `--fixture-root` review lesson -- seams are Python-API-only).
- No subprocess beyond the `docker inspect` call that already exists. No `ciu`
  invocation. No TOML parsing.

## Required Deterministic Tests

Fixture-driven, no live docker, no live ciu:
- Label-confirmed detection: all three labels present -> confirmed, stack and phase
  populated, phase number parsed.
- Inferred detection: no `ciu.*` labels, compose project matches a known stack root,
  name matches the anchored pattern -> inferred, discriminator says inferred.
- **Negative:** a plain non-ciu container (e.g. a bare `docker run` container with no
  compose labels) is not annotated at all -- and this is distinguishable in the frame
  from a ciu container whose labels are unreadable.
- **Phase ordering, engineered to fail a string sort:** a stack with `phase_1`,
  `phase_2`, `phase_10` must order 1, 2, 10. Assert the exact ordering. This test must
  fail if the implementation sorts phases as strings.
- Malformed phase labels (`phase_`, `phase_abc`, missing) -> unknown phase, no crash,
  does not sort as phase 0.
- Grouping correctness: N containers across 2 stacks and 3 phases group into exactly
  the expected sets, asserted on the exact membership, not on counts alone.
- Frame-schema compatibility: an existing pre-P76 fixture frame still parses, and a
  frame with no ciu-managed containers serializes without ciu noise.

## Gates And Evidence

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P76 tests> -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <all changed/new files>
git diff --check
```

Use the package venv (`/usr/local/py-utils/venvs/pytest/bin/python`) if bare
`python3` trips an unrelated `-W error` deprecation at import; state in the REPORT
which interpreter produced each result. A live check against this host's real
ciu-managed containers is controller-side evidence, not an agent claim -- if you run
it, say so and show the output; if you cannot, say that instead.

Update `docs/ARCHITECTURE.md` (where ciu metadata enters the frame), `CONTRACTS.md`
(the ciu fields and the confirmed/inferred discriminator), `docs/ROADMAP.md`
(Optional-plugins bucket: CIU metadata landed; grouping/actions remain),
`docs/STATUS.md`.

## Out Of Scope

- **TUI group-by-stack rendering** and whole-stack/whole-phase multi-select
  (TUI-SPEC §4.3 "Grouping/selection") -- a successor package consuming this metadata.
- **Gating mutating actions through ciu** (TUI-SPEC §4.3 "Gating ops through ciu")
  -- a successor package; it depends on this metadata plus the P72 action verbs.
- Proposing or implementing the label schema **inside `ciu` itself** (`ciu/` is a
  different package in this repo with its own review process; TUI-SPEC §4.3 is
  explicit that this is "topos asking ciu for a small favor", not topos's change to
  make). Record the request in the REPORT.
- Parsing `ciu.global.toml`, invoking `ciu`/`ciu-deploy`, or any new subprocess.
