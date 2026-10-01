# Oobabooga Text Generation WebUI - CPU-Only Build
# Source: https://github.com/oobabooga/text-generation-webui
# Updated: December 2025
# Target: CPU-only LLM inference for resource-constrained environments (≤6 cores, ≤10GB RAM)
# 
# Key Technical Notes:
# - Uses CPU-only PyTorch and llama-cpp-python to avoid CUDA dependencies
# - No venv needed (runs in isolated container)
# - Model loading has known oobabooga limitations with llama_cpp_binaries
#   See README.md for workarounds and alternative approaches

FROM python:3.13-slim

# Build arguments for versioning
ARG BUILD_VERSION=dev
ARG BUILD_TIME=unknown

# Labels
LABEL org.opencontainers.image.title="Oobabooga LLM CPU"
LABEL org.opencontainers.image.description="CPU-only oobabooga text-generation-webui for local LLM inference"
LABEL org.opencontainers.image.version="${BUILD_VERSION}"
LABEL org.opencontainers.image.created="${BUILD_TIME}"
LABEL org.opencontainers.image.source="https://github.com/oobabooga/text-generation-webui"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd --gid 1000 llm && \
    useradd --uid 1000 --gid llm --shell /bin/bash --create-home llm

WORKDIR /app

# Clone the official oobabooga repository (shallow clone for smaller image)
# source: https://github.com/oobabooga/text-generation-webui
RUN git clone --depth 1 https://github.com/oobabooga/text-generation-webui.git .

# CRITICAL: Install CPU-only PyTorch FIRST to avoid CUDA dependencies
# This must happen before any other package that might pull in GPU-optimized torch
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install CPU-only llama-cpp-python from dedicated CPU wheel index
# This avoids pulling in GPU-optimized binaries that require CUDA
RUN pip install --no-cache-dir llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# Install oobabooga CPU-only requirements from the cloned repo
# This includes all necessary dependencies (markdown, Pillow, gradio, etc.)
# We need to filter out GPU-specific packages and llama-cpp-binaries
# Note: We intentionally skip llama_cpp_binaries since we use simple llama.cpp loader
RUN cat /app/requirements/full/requirements_cpu_only.txt | \
    grep -v "llama_cpp_binaries" | \
    grep -v "triton-windows" | \
    grep -v "torchao" > /tmp/requirements_filtered.txt && \
    pip install --no-cache-dir -r /tmp/requirements_filtered.txt || true

# Install additional dependencies that may be missed
RUN pip install --no-cache-dir \
    markdown \
    Pillow>=9.5.0 \
    colorama \
    tqdm \
    scipy \
    einops \
    psutil \
    html2text

# Force reinstall CPU-only llama-cpp-python to override any GPU versions
# This is critical - oobabooga requirements may have reinstalled GPU binaries
RUN pip uninstall -y llama-cpp-python llama-cpp-binaries 2>/dev/null || true && \
    pip install --no-cache-dir --force-reinstall llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# Create directories for models and cache
RUN mkdir -p /app/user_data/models /app/user_data/cache /data/models /data/cache && \
    chown -R llm:llm /app /data

# Set environment variables
ENV TRANSFORMERS_CACHE=/data/cache \
    HF_HOME=/data/cache \
    BUILD_VERSION=${BUILD_VERSION} \
    BUILD_TIME=${BUILD_TIME} \
    PYTHONUNBUFFERED=1

# Expose port 80 (standard HTTP)
EXPOSE 80

# Create startup script with RUN instead of COPY to ensure it's updated
RUN echo '#!/bin/bash' > /app/start.sh && \
    echo 'set -e' >> /app/start.sh && \
    echo 'echo "=========================================="' >> /app/start.sh && \
    echo 'echo "Oobabooga LLM Server (CPU-Only)"' >> /app/start.sh && \
    echo 'echo "Build: ${BUILD_VERSION:-dev} at ${BUILD_TIME:-unknown}"' >> /app/start.sh && \
    echo 'echo "=========================================="' >> /app/start.sh && \
    echo 'echo ""' >> /app/start.sh && \
    echo 'mkdir -p /app/user_data/models /app/user_data/cache /data/models /data/cache' >> /app/start.sh && \
    echo 'chmod -R 777 /app/user_data /data 2>/dev/null || true' >> /app/start.sh && \
    echo 'API_PORT=${OOBABOOGA_API_PORT:-80}' >> /app/start.sh && \
    echo 'echo "Starting server with:"' >> /app/start.sh && \
    echo 'echo "  - API mode enabled (port ${API_PORT})"' >> /app/start.sh && \
    echo 'echo "  - CPU-only inference"' >> /app/start.sh && \
    echo 'echo "  - Model directory: /data/models"' >> /app/start.sh && \
    echo 'echo ""' >> /app/start.sh && \
    echo 'exec python server.py --listen --api --api-port ${API_PORT} --model-dir /data/models --cpu' >> /app/start.sh && \
    chmod +x /app/start.sh

# Don't switch to non-root user for dev environment (volume permission issues)
# USER llm

# Health check on port 80
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=60s \
    CMD curl -sf http://localhost:80/v1/models || exit 1

ENTRYPOINT ["/app/start.sh"]