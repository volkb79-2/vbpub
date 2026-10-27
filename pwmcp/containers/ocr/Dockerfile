# pwmcp OCR sidecar
# ==================
# A stateless HTTP service (containers/ocr/ocr_service.py) that wraps the native Tesseract 5
# CLI: POST an image, get text + per-word confidence and boxes back. It carries NO browser,
# needs NO outbound network, and returns text and confidence only (no decisions, no key
# sending). Build context is the pwmcp/ directory, like the main image:
#   docker build -f containers/ocr/Dockerfile -t pwmcp-ocr:dev .
#
# Freshness / version policy: every package comes from the Debian archive of the pinned
# release (tesseract-ocr 5.x, python3-pil); there is no pinned download, so no >= floor with
# a 14-day vetting window applies here.
# KNOWN VIOLATION of the repo's digest-pin convention: the base is referenced by TAG ONLY.
# The main image pins its base by manifest digest; pinning this one is part of the release
# wiring that is still awaiting an operator decision (KNOWN_ISSUES_TODO_BACKLOG.md PWMCP-11).
# Do not treat the tag-only base as acceptable for a release.
ARG DEBIAN_RELEASE=trixie
FROM debian:${DEBIAN_RELEASE}-slim

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
