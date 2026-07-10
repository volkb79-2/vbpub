# P43 Current Textual Dependency Baseline Report

## Outcome

P43 replaces the historical pre-1.0 dependency range (`textual>=0.58,<1`) with
a current Textual 8.2.8-or-newer baseline (`textual>=8.2.8`) with no artificial
upper ceiling. Source metadata, built-wheel METADATA, clean resolver installation,
packaging-metadata regression tests, and the full test suite all pass.

## Changes Made

### pyproject.toml

Line 10: `dependencies = ["textual>=0.58,<1"]` → `dependencies = ["textual>=8.2.8"]`

The dependency policy is now explicit: latest compatible upstream releases are
preferred; upper bounds require a demonstrated incompatibility and a tracked
removal condition.

### New test file: `groop/tests/test_packaging_metadata.py`

Five regression tests:

1. `test_textual_dependency_present` — pyproject.toml must declare textual.
2. `test_textual_lower_bound_at_least_8_2_8` — lower bound ≥8.2.8.
3. `test_textual_has_no_upper_ceiling` — no `<` in the textual specifier.
4. `test_no_other_upper_ceiling_on_textual` — no comma-separated multi-clause.
5. `test_wheel_metadata_requires_dist` — checks built wheel's METADATA for
   `Requires-Dist: textual>=8.2.8` without `<` (skipped when no wheel exists).

These tests read project metadata only; no network access or application
behavior duplication.

### Documentation updates

- **README.md**: P43 row changed from "Planned" to "Done".
- **ROADMAP.md**: P43 near-term section rewritten as done; remaining v1/v1.5
  packages reduced from 1 to 0.
- **STATUS.md**: P43 mentioned in v1 line, acceptance evidence, and packaging
  section.
- **MEASUREMENTS.md**: P43 packaging evidence section added; clean resolver
  installation recorded; Current Status updated with test counts.
- **RELEASE-READINESS.md**: Item 11 evidence updated; P43 history entry added.

Historical P40 evidence of `textual>=0.58,<1` is preserved and clearly marked
as superseded rather than rewritten or deleted.

## Verification Evidence

### Source metadata

```bash
$ grep textual groop/pyproject.toml
dependencies = ["textual>=8.2.8"]
```

### Built wheel METADATA

```text
Requires-Dist: textual>=8.2.8
```

No `<` upper bound present.

### Packaging-metadata regression tests

```bash
$ python3 -m pytest groop/tests/test_packaging_metadata.py -q
5 passed in 0.17s
```

### Clean resolver installation

Isolated venv with no preinstalled Textual:

```text
Successfully installed groop-0.1.0 ... textual-8.2.8 ...
```

```bash
$ /tmp/p43-clean-venv/bin/groop --version
groop 0.1.0

$ /tmp/p43-clean-venv/bin/groop --replay ... --step --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### Full test suite

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests -q
436 passed, 1 skipped in 48.49s
```

The 5 new packaging-metadata tests are included in the 436 passing count.

### Focused acceptance tests

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_acceptance.py -q
40 passed in 7.29s
```

### P38 TUI smoke

```bash
$ PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
```
Exit `0`, `ok: true`, `frames: 1`, `view: tree`, `profile: auto`.

### Direct replay UI smoke

```bash
$ PYTHONPATH=groop/src python3 groop/src/groop/cli.py \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --step --ui-smoke
```
`ui smoke ok frames=1 view=tree profile=auto`, exit `0`.

### P33 acceptance smoke

```bash
$ PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
```
Exit `0`, `ok: true`, 8 entities, 572 source labels.

### Full-source py_compile

```bash
$ mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
$ python3 -m py_compile "${pyfiles[@]}"
```
Clean exit, no errors.

## Deviations from Handoff

None. All handoff requirements are met:

- [x] Published dependency changed to `textual>=8.2.8` with no upper bound.
- [x] Packaging-metadata regression test proves lower bound ≥8.2.8 and no ceiling.
- [x] Wheel built and METADATA inspected.
- [x] Clean resolver installation selects Textual 8.2.8.
- [x] UI, acceptance, full-suite, replay smoke, P38 TUI smoke, P33 smoke, and
      `py_compile` all pass.
- [x] Docs updated preserving historical evidence.
- [x] Dependency policy explicit.
- [x] Python >=3.11 support preserved.
- [x] zstandard optional dependency unchanged.
- [x] No `textual-dev` added.
- [x] P43-LOG.md and P43-REPORT.md written.

## Contract Changes

None. No shared interfaces were modified.

## Known Gaps / Open Items

None. P43 closes the last planned v1/v1.5 release-confidence package. Manual
live-host acceptance evidence (five-minute TUI CPU/RSS, controlled drift,
docker-group non-root smoke, DAMON/daemon live evidence) remains for a
production-certified release claim per `docs/RELEASE-READINESS.md`.
