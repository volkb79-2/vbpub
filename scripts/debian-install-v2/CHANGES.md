# Changelog

All notable changes to debian-install-v2 are recorded here.

## [Unreleased]

### Changed
- A configured `root_max_size_gb` now installs cloud-init and initramfs root
  growth guards after stage one validates the capped tail-swap plan, before
  stage-two setup and the first reboot, and repeats them in stage two.
- Root caps are measured as partition lengths from the current root start, so
  absolute 1 MiB alignment no longer lowers the configured limit.
- Rename the optional wizard dependency manifest to `requirements-wizard.txt`
  and update its installer hint, CLI surface, and adoption documentation.

### Added
- `root_max_size_gb` caps the ext root partition and leaves space after root
  and managed swap unallocated (wizard field, plan line, and user docs). A cap
  requires managed tail-swap partitions directly after root so boot-time and
  stage-two growth cannot consume it.

### Fixed
- Remote bootstrap imports the config validator from freshly fetched source
  even when no profile is selected.
- Root caps that `untouched` or `grow-only` cannot satisfy are refused before
  the swap-fit check, with the cap named in the error.
