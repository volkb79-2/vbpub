# P10 — successor brief (for P11 mutation construction, P13 standalone wheel)

## `assemble_verdict`'s new signature — P11 and P13 both call this

`runner.assemble_verdict` gained two new KEYWORD-ONLY parameters, both
defaulting to `()`:

```python
def assemble_verdict(
    *,
    lane: Lane,
    commit: str,
    result: CommandResult,
    claims: tuple[Claim, ...],
    assay_version: str,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
) -> Verdict:
```

Every call site through P09 is unaffected — the defaults reproduce the old
behavior exactly (`declared_evidence=()`, `evidence=()`), proven by
`tests/test_runner_verdict_fixtures.py` staying green, completely
unmodified by this package. If your work doesn't touch attested/adjudicated
evidence, you can ignore both new parameters and nothing changes for you.

If you DO pass `evidence`/`declared_evidence`: they must cover each other
exactly by `(source, key)` identity (`EvidenceDeclaration.identity` /
`Evidence.identity`) or `assemble_verdict` raises
`AssayError`/`ERROR`/`BAD_LANE_CONFIG` BEFORE constructing a `Verdict` — the
same shape as the pre-existing `claims`/`declared_rigor` coverage guard.
Evidence statuses fold into the SAME `rollup` claims already use — an
`ERROR`/`UNREADABLE_ARTIFACT` evidence entry outranks a passing R0/R1/R3
claim in the final outcome, exactly as if it were a claim.

## `assay.attestation` — the new module, if useful as a template or a dependency

Three layers, each independently importable:

* `AttestationRecord` (frozen `kw_only`): `producer: str`,
  `attested_commit: str`, `reviewed_paths: tuple[str, ...]`.
* `parse_attestation(text, *, source_name) -> AttestationRecord` /
  `load_attestation_file(path) -> AttestationRecord` — pure format loading,
  no git. Raises `AssayError`/`ERROR`/`UNREADABLE_ARTIFACT` on anything
  malformed.
* `evaluate_attestation(repo, *, key, head, record: AttestationRecord | None)
  -> Evidence` — the git-dependent core: equal-or-ancestor for
  `record.attested_commit` against `head`, then every declared reviewed
  path's existence at `record.attested_commit`, then a path-scoped diff
  against `head`. NEVER raises for a judged outcome — every git-level
  failure (unrelated/malformed/descendant attested commit, or a missing
  reviewed path) is caught and returned as `ERROR`/`UNREADABLE_ARTIFACT`, a
  changed reviewed path is `NO_MEASUREMENT`/`STALE_ATTESTATION`, `record=
  None` is `NO_MEASUREMENT`/`MISSING_ATTESTATION`, everything else is
  `PASS`.
* `load_attested_evidence(repo, *, head, declared: Sequence[
  EvidenceDeclaration], attestations_dir: Path) -> tuple[Evidence, ...]` —
  the orchestration entry point: rejects a duplicate `(source, key)` in
  `declared` and a non-`"attested"` source, both `BAD_LANE_CONFIG`; resolves
  each declared key against `attestations_dir/<key>.json`.

**No real `assay.toml` wiring exists yet** — `declared` is always a
caller-supplied list today (A-111's deliberate deferral, matching how full
CLI/rigor wiring across R0-R3 is already deferred to P14). If your work
needs a lane file to declare attested evidence, that TOML shape does not
exist and is not reserved anywhere in `config.py` — you would be the one to
design and add it, and `config.py` is NOT in P10's forbidden-but-touched
set, so it is open for a future package (unlike `verdict.py`/`schemas`,
which stay complete and closed).

## Adjudicated evidence (Tier 2) — still entirely unbuilt

`load_attested_evidence` actively REJECTS a `source="adjudicated"` entry in
its `declared` list (`BAD_LANE_CONFIG`) — there is no adjudicated loader,
no registry, and building one was explicitly out of scope
(`escalate_if: "implementation would require an adjudicator registry or
policy engine"`). `EvidenceDeclaration`/`Evidence` both already accept
`source="adjudicated"` structurally (untouched, from P01b/A-078), but
nothing in this codebase produces one yet.

## For P13 (standalone wheel proof)

`attestation.py` has ZERO new runtime dependencies — stdlib only (`json`,
`dataclasses`, `pathlib`, `typing`), matching the project's zero-dependency
constraint. It imports `assay.git` and `assay.verdict`
(`Evidence`/`EvidenceDeclaration`, both pre-existing) and `assay.errors` —
nothing outside the package. If P13's wheel-build fixture project exercises
the runner/CLI path, `assemble_verdict`'s two new parameters being optional
means no existing fixture needs updating to keep working; a NEW fixture
exercising attested evidence would need its own `attestations_dir` with
`<key>.json` files (see `tests/test_attestation_pipeline_integration.py`
for the exact shape) and a real git history — the `git_repo`/`GitRepo`
`tmp_path` fixture in `tests/conftest.py` is unmodified and reusable as-is.
