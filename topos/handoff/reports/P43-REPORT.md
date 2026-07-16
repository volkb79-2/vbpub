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

### New test file: `topos/tests/test_packaging_metadata.py`

Two regression tests structurally parse `pyproject.toml`:

1. `test_textual_lower_bound_is_current` — lower bound ≥8.2.8.
2. `test_textual_has_no_upper_ceiling` — no `<` or `<=` ceiling.

These tests read project metadata only; no network access, ignored build
artifact, or application behavior duplication. Fresh wheel METADATA inspection
remains a separate release gate.

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
$ grep textual topos/pyproject.toml
dependencies = ["textual>=8.2.8"]
```

### Built wheel METADATA

```text
Requires-Dist: textual>=8.2.8
```

No `<` upper bound present.

### Packaging-metadata regression tests

```bash
$ /tmp/p43-clean-venv/bin/python -m pytest topos/tests/test_packaging_metadata.py -q
2 passed in 0.03s
```

### Clean resolver installation

Isolated venv with no preinstalled Textual:

```text
Successfully installed topos-0.1.0 ... textual-8.2.8 ...
```

```bash
$ /tmp/p43-clean-venv/bin/topos --version
topos 0.1.0

$ /tmp/p43-clean-venv/bin/topos --replay ... --step --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### Full test suite

```bash
$ PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest topos/tests -q
433 passed, 1 skipped in 47.31s
```

The two new packaging-metadata tests are included in the passing count.

### Focused acceptance tests

```bash
$ PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest \
    topos/tests/test_acceptance.py -q
40 passed in 7.27s
```

### UI tests

The five UI/Textual test modules pass in the same clean resolved environment:

```text
59 passed in 10.91s
```

### P38 TUI smoke

```bash
$ PYTHONPATH=topos/src python3 -m topos.acceptance tui-smoke \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --json
```
Exit `0`, `ok: true`, `frames: 1`, `view: tree`, `profile: auto`.

### Direct replay UI smoke

```bash
$ PYTHONPATH=topos/src python3 topos/src/topos/cli.py \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl \
  --step --ui-smoke
```
`ui smoke ok frames=1 view=tree profile=auto`, exit `0`.

### P33 acceptance smoke

```bash
$ PYTHONPATH=topos/src python3 -m topos.acceptance smoke \
  --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch \
  --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --json
```
Exit `0`, `ok: true`, 8 entities, 572 source labels.

### Full-source py_compile

```bash
$ mapfile -d '' pyfiles < <(find topos/src/topos topos/tests -name '*.py' -print0)
$ python3 -m py_compile "${pyfiles[@]}"
```
Clean exit, no errors.

## Controller Review Correction

The initial agent result installed the wheel into the clean resolver venv but
ran pytest through the managed environment. Controller review installed pytest
into the clean venv and reran the full suite, focused acceptance/UI tests,
direct replay smoke, P38 TUI smoke, and `py_compile` there. The initial regex
metadata tests and soft ignored-wheel check were also replaced with structural
TOML/PEP 508 parsing; fresh wheel inspection remains an explicit release gate.

## Deviations from Handoff

None remaining. All handoff requirements are met:

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
