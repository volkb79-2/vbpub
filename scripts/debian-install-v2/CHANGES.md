# Changelog

All notable changes to debian-install-v2 are recorded here.

## [Unreleased]

### Changed
- A configured `root_max_size_gb` now disables cloud-init and initramfs root
  growth after stage two, preserving unallocated disk space across later boots.
- Root caps are measured as partition lengths from the current root start, so
  absolute 1 MiB alignment no longer lowers the configured limit.
- Rename the optional wizard dependency manifest to `requirements-installer.txt`
  and update its installer hint, CLI surface, and adoption documentation.
