# P43 Work Log

## Context

- Date: 2026-07-10 UTC
- Branch: `feat/groop-p43-textual-current-baseline`
- Worktree: `.worktrees/-groop-p43-textual-current-baseline`
- Base commit: (current main)

## Changes

### pyproject.toml

Changed `textual>=0.58,<1` to `textual>=8.2.8` — the published dependency now
resolves the current supported Textual release line (≥8.2.8) with no artificial
upper bound.

### Packaging-metadata regression test

Added `groop/tests/test_packaging_metadata.py` with two tests that structurally
parse `pyproject.toml` and its PEP 508 requirement:

1. `test_textual_lower_bound_is_current` — lower bound must be ≥8.2.8.
2. `test_textual_has_no_upper_ceiling` — no `<` or `<=` ceiling.

These test project metadata only; no network access, ignored build artifact,
or application behavior duplication. Fresh wheel METADATA inspection remains
a separate release gate.

### Documentation updates

- `README.md`: P43 row changed from "Planned" to "Done", description finalized.
- `docs/ROADMAP.md`: P43 near-term section written with handoff reference.
- `docs/STATUS.md`: Updated acceptance status and quality gate.
- `MEASUREMENTS.md`: Appended P43 packaging-metadata, wheel, and test evidence.
- `docs/RELEASE-READINESS.md`: Updated packaging section to reflect new lower
  bound and added P43 as release gate prerequisite.

Historical P40 evidence is preserved and clearly marked as superseded rather
than rewritten.

### Build and metadata

- `python3 -m build groop/` produced groop-0.1.0.tar.gz and groop-0.1.0-py3-none-any.whl.
- Wheel METADATA: `Requires-Dist: textual>=8.2.8` — no upper bound.
- Source metadata (PKG-INFO): same.

### Clean resolver installation

Installed wheel into an isolated venv with no preinstalled Textual.
Pip resolved Textual 8.2.8. `groop --version` verified (groop 0.1.0),
UI replay smoke passed (ui smoke ok frames=1 view=tree profile=auto).

## Validation

### Packaging metadata tests

```text
2 passed in 0.03s
```

### Full suite

```text
433 passed, 1 skipped in 47.31s
```

### P38 TUI smoke

Controller clean-venv rerun: exit 0, ok=true, frames=1, view=tree,
profile=auto, wall 0.4614s, RSS 46392KB.

### Acceptance and UI tests

Acceptance: 40 passed in 7.27s. UI/Textual: 59 passed in 10.91s.

### P33 acceptance smoke

exit 0, ok=true, 8 entities, 572 source labels, wall 0.1344s.

### Direct replay UI smoke

"ui smoke ok frames=1 view=tree profile=auto", exit 0.

### py_compile

Clean exit across all groop/src/groop and groop/tests .py files.

## Decisions

- **Dependency policy**: textual >=8.2.8 with no upper bound. Future upstream
  breaks will be caught by the normal test/release validation cycle rather than
  a silent resolver ceiling. This is consistent with the handoff requirement.
- **Test approach**: The packaging-metadata tests read pyproject.toml directly
  rather than importing setuptools metadata. This keeps them fast, avoids
  triggering package discovery, and matches the handoff requirement to "read
  the project metadata; do not duplicate application behavior."
- **Historical docs**: P40 references to `textual>=0.58,<1` are preserved and
  marked as superseded rather than rewritten. This maintains audit trail.

## Blockers

None.

## Controller Review Correction

The agent's first result did not have pytest installed in the clean resolver
venv. Controller review installed pytest there and reran the full suite,
acceptance/UI gates, replay smokes, and `py_compile`. It also replaced the
regex/soft-wheel metadata tests with structural TOML/PEP 508 assertions so a
stale ignored wheel cannot affect the normal suite.

## Handoff Checklist

- [x] pyproject.toml updated.
- [x] Packaging-metadata regression test added.
- [x] Wheel built and METADATA verified.
- [x] Clean resolver installation verified.
- [x] README, ROADMAP, STATUS, MEASUREMENTS, RELEASE-READINESS updated.
- [x] Report file (P43-REPORT.md) written.
- [x] Full test suite passing.
- [x] Feature branch committed.
