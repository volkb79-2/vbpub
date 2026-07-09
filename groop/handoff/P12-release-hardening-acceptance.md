# P12 — Release hardening, acceptance evidence, and packaging

**Cut:** v1/v1.5 stabilization. **Depends:** P1-P11. Branch:
`feat/groop-p12-release-hardening`. Follow `groop/README.md` workflow protocol.

## Goal

Turn the current feature-complete prototype into a release-candidate-quality
tool by filling the missing acceptance evidence, packaging checks, and small
testability gaps documented in `docs/STATUS.md` and `MEASUREMENTS.md`.

## Scope — in

1. Populate `MEASUREMENTS.md` with repeatable v1 CPU/RSS evidence on the
   reference host or a documented comparable host.
2. Add a packaging check:
   - build sdist/wheel;
   - install through `pipx` or an equivalent isolated wheel install;
   - verify `groop --version`, `--once --json`, and replay UI smoke.
3. Add a deterministic fixture-facing way to inject canned systemd data for
   manual CLI fixture runs, without weakening live defaults.
4. Add a release checklist document or section linking tests, packaging,
   measurements, and known gaps.
5. Re-run full merged-main gates and update status docs if evidence changes.

## Scope — out

- New product features.
- BPF provider implementation.
- Daemon implementation.
- Root-mutating live acceptance beyond documenting what cannot be run safely.

## Acceptance

- `MEASUREMENTS.md` has filled CPU/RSS and packaging sections with concrete
  command output summaries.
- Full test suite and compile pass.
- Wheel/sdist isolated install passes.
- README/status docs no longer describe packaging/perf evidence as missing if
  this package successfully collects it.

## Notes

- Prefer adding tests for reproducibility hooks over adding one-off scripts.
- If `pidstat`, `pipx`, or `build` is unavailable, document the missing tool and
  use the closest defensible substitute without mutating system packages.
