# syntax=docker/dockerfile:1
FROM repacked AS source

# Export only the canonical release manifest without loading the multi-GiB
# image into Docker's local image store.
FROM scratch AS manifest
COPY --from=source /usr/local/share/modern-debian-tools-python-debug/manifest.md /manifest.md

# The final/publish stage preserves the repacked image filesystem and config.
FROM source AS publish
