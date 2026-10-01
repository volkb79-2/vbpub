# WhisperLive - Real-time Streaming Transcription + Batch API
# Based on: https://github.com/collabora/WhisperLive
# CPU-optimized for real-time audio streaming with WebSocket support
# Also provides REST API for batch transcription

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    ffmpeg \
    portaudio19-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install WhisperLive and dependencies
RUN pip install --no-cache-dir \
    git+https://github.com/collabora/WhisperLive.git \
    faster-whisper \
    uvicorn \
    fastapi \
    python-multipart \
    websockets

# Create directories
RUN mkdir -p /data/models /data/audio

# Copy custom server that supports both streaming and batch
COPY server.py /app/server.py

# Expose port 80 (internal - reverse proxy handles external routing)
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:80/health || exit 1

# Start custom server with both WebSocket and REST API
CMD ["python", "/app/server.py"]
