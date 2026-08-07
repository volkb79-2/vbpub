# P09 — successor brief (for P10 attested evidence, P11 mutation)

## P10 (attested evidence) — does not touch your files

P10's own scope FORBIDS `verdict.py` and `schemas/` and does not list
`canary.py`; it touches `attestation.py` (new), `config.py`, `runner.py`,
`tests/**`. I did not modify `runner.py` at all — `execute_command`,
`build_r0_claim`, `evaluate_r1`, `assemble_verdict` are byte-identical to
what P08 left. `Evidence`/`EvidenceDeclaration` (verdict.py) are also
untouched by this package. No interaction expected; verify against a fresh
`git diff` if in doubt.

## P11 (mutation) — the adapter protocol's FINAL shape

`adapters/base.py`'s `LanguageAdapter` Protocol is now **five attributes,
SIX methods** (P07 added `statement_spans`, P09 added the two `inject_*`
methods below — this package's own deliberate extension, A-084/A-105):

```python
name: str
source_globs: tuple[str, ...]
excluded_dir_names: frozenset[str]
requires_span_attribution: bool
external_tools: tuple[str, ...]

def is_test_path(self, rel_path: str) -> bool: ...
def has_executable_code(self, rel_path: str, text: str) -> bool: ...
def normalize_coverage_key(self, key: str) -> str: ...
def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None: ...
def inject_import_break(self, text: str) -> tuple[str, str]: ...       # (text) -> (text, description)
def inject_uncovered_line(self, text: str) -> tuple[str, str]: ...     # same shape
```

Your `generate_mutants(text, lines) -> mutants | UNSUPPORTED` (A-011/A-084)
is the ONE remaining reserved extension — DESIGN-GUIDE §11's full
seven-capability sketch is now complete after your package. Both
`PythonAdapter` and `GoAdapter` implement `inject_import_break`/
`inject_uncovered_line` already; you add a seventh method to both classes,
you do not touch the two you're inheriting.

**Reusable groundwork for your own mutation work:**
* `PythonAdapter`'s `_inject_import_break`/`_inject_uncovered_line` (module-
  level pure functions in `adapters/python.py`, wrapped by the instance
  methods) show the house pattern for a pure `(text) -> (text, str)`
  transform with no filesystem access — your `generate_mutants` should
  follow the identical shape.
* `GoAdapter`'s injectors are BOTH plain trailing appends
  (`_append_snippet` in `adapters/go.py`) because Go has no executable
  top-level statement — if your mutation operators need an INSERTION point
  inside existing Go code (not just an append), you cannot reuse
  `_append_snippet` and will need your own scan; P08's own
  `_scan_signature_for_body` (the point right after a function's opening
  `{`) is the proven-correct anchor for "inside a real function body" if
  you need one.

## `CanaryResult`'s shape, if useful as a template

`verdict.py`'s new `CanaryResult` (frozen `kw_only`, A-092): `mechanism: str`,
`description: str`, `control_outcome: Outcome`,
`transformed_outcome: Outcome | None`, `expected_reason_code: ReasonCode | None`
(must be FAIL-shaped when present), `observed_reason_code: ReasonCode | None`
(must pair with `transformed_outcome` exactly like `Claim`'s own
`status`/`reason_code` rule). `Claim.canary` is gated to `rigor == "R3"`
— read `verdict.py`'s `Claim.__post_init__` canary block and the schema's
`canary` `$def`/`allOf` branches as your own template for whatever P10's
attested-evidence payload needs (P10 is schema-forbidden, so this may not
apply, but the CONSTRUCTION-time discipline — omit-not-null,
pairing-validated-at-construction, independent hand-written fixtures
compared against real `jsonschema` — is the house pattern regardless of
which file owns it).
