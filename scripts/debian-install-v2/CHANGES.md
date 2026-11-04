# Changelog

All notable changes to debian-install-v2 are recorded here.

## [Unreleased]

### Changed
- A configured `root_max_size_gb` now installs cloud-init and initramfs root
  growth guards after stage one validates the capped tail-swap plan and before
  it installs the initramfs shrink hook and rebuilds images, then sets up stage
  two and reboots; stage two repeats them.
- Root caps are measured as partition lengths from the current root start, so
  absolute 1 MiB alignment no longer lowers the configured limit.
- Rename the optional wizard dependency manifest to `requirements-wizard.txt`
  and update its installer hint, CLI surface, and adoption documentation.

### Added
- `tools/cpu_benchmark.py` (HB-1, sha256-pinned in `tools/cpu_benchmark.py.sha256`): shared stdlib tool, logic lifted
  from the v1 `geekbench_runner.py` and generalised over the Geekbench major (6 or 7, default newest = 7 via CDN probing;
  records `tool_major`, per-major normalisation baselines, refuses to mix majors), that runs
  Geekbench once per host and writes `/var/lib/mdt/host-facts/cpu-benchmark.json` (0644, scores, CPU, conditions,
  result URL) and `cpu-benchmark.claim.json` (0600, claim link). Scores from the CLI output, else the result page
  (ported from the v1 runner); `--resolve` re-reads the page without running; refuses to re-measure without `--force`
  and under CPU pressure; hermetic tests in mdt-cockpit `host-setup/tests/test_cpu_benchmark.py`. The installer step
  and `mdt host-setup cpu-benchmark` follow (HB-2, HB-3).
- `root_max_size_gb` caps the ext root partition and leaves space after root
  and managed swap unallocated (wizard field, plan line, and user docs). A cap
  requires managed tail-swap partitions directly after root so boot-time and
  stage-two growth cannot consume it.

### Fixed
- Remote bootstrap imports the config validator from freshly fetched source
  even when no profile is selected.
- Root caps that `untouched` or `grow-only` cannot satisfy are refused before
  the swap-fit check, with the cap named in the error.
