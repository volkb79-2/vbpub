# syntax=docker/dockerfile:1

# Re-export an existing immutable image without changing its filesystem or
# configuration. The exporter selects gzip/zstd and its level; this Dockerfile
# deliberately adds no layer.
ARG SOURCE_IMAGE
FROM ${SOURCE_IMAGE}
