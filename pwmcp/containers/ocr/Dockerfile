# pwmcp OCR sidecar
# ==================
# A stateless HTTP service (containers/ocr/ocr_service.py) that wraps the native Tesseract 5
# CLI: POST an image, get text + per-word confidence and boxes back. It carries NO browser,
# needs NO outbound network, and returns text and confidence only (no decisions, no key
# sending). Build context is the pwmcp/ directory, like the main image:
#   docker build -f containers/ocr/Dockerfile -t pwmcp-ocr:dev .
#
# Freshness / version policy: every package comes from the Debian archive of the pinned
# release (tesseract-ocr 5.x, python3-pil); there is no pinned download, so no >= floor applies.
# No 14-day age window is applied to the Debian BASE DIGEST: it is a rolling OS tag whose newer
# digests are mostly security rebuilds, which a window would only delay. Decided by design,
# 2026-10-27 (PWMCP-11).
#
# The base is pinned by multi-arch manifest DIGEST exactly like the main image's Playwright
# base. DEBIAN_RELEASE and DEBIAN_IMAGE_DIGEST are projections kept identical in this file,
# docker-bake.hcl and cmru.vars by scripts/resolve-playwright-version.py (--refresh resolves the
# digest of debian:<release>-slim from Docker Hub; --check, run by CMRU's prepare phase, fails
# if they disagree or the digest is not a sha256). Do not edit them by hand.
ARG DEBIAN_RELEASE=trixie
ARG DEBIAN_IMAGE_DIGEST=sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
FROM debian:${DEBIAN_RELEASE}-slim@${DEBIAN_IMAGE_DIGEST}

# Same label set as the main image: org.opencontainers.image.source is what links the GHCR
# package to the repository (and lets the package inherit its access and visibility settings).
LABEL org.opencontainers.image.title="pwmcp-ocr" \
      org.opencontainers.image.description="Stateless OCR sidecar for pwmcp: Tesseract behind a small HTTP API (text and confidence only)" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"

# tesseract-ocr-eng is the English traineddata; add tesseract-ocr-<lang> packages for more.
# python3-pil decodes/validates the image and does the optional integer upscaling.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng python3 python3-pil \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin ocr

COPY containers/ocr/ocr_service.py /opt/pwmcp-ocr/ocr_service.py

ENV OCR_PORT=8941 \
    OCR_MAX_BYTES=8388608 \
    OCR_MAX_DIM=4096 \
    OCR_MAX_SCALED_PIXELS=24000000 \
    OCR_BODY_DEADLINE_S=10 \
    OCR_TIMEOUT_S=20 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1000:1000
EXPOSE 8941

# Standalone `docker run` is monitored too (compose carries the same probe). stdlib only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('OCR_PORT', '8941'), timeout=3).status == 200 else 1)"]

# nice: the host is shared with other workloads; recognition is background work.
ENTRYPOINT ["nice", "-n", "10", "python3", "/opt/pwmcp-ocr/ocr_service.py"]
