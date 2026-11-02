# Changes

## Unreleased

- A configured `root_max_size_gb` now disables cloud-init and initramfs root
  growth after stage two, preserving unallocated disk space across later boots.
- Root caps are measured as partition lengths from the current root start, so
  absolute 1 MiB alignment no longer lowers the configured limit.
