# WhisperLive - Hybrid Transcription Service

**Purpose**: Batch AND streaming audio transcription  
**Technology**: WhisperLive (Python) + faster-whisper engine

---

## Quick Start

```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/whisper-live

# Generate configuration and start service
python3 ../../scripts/ciu/ciu.py

# Test both modes (after service is healthy)
python3 test-whisperlive-comprehensive.py localhost 80
```

---

## Architecture

### Service Overview
- **Container**: Custom hybrid server (batch + streaming)
- **Engine**: faster-whisper (CPU-optimized)
- **Protocols**: 
  - **REST API** (`/asr`) for batch transcription
  - **WebSocket** (`/ws`) for streaming
- **Internal Port**: 80 (reverse proxy handles external routing)
- **Modes**: Both batch and real-time streaming

### Dual-Mode Support

This service supports **BOTH** batch and streaming in one container:

| Mode | Endpoint | Protocol | Use Case |
|------|----------|----------|----------|
| **Batch** | `POST /asr` | REST API | Complete file uploads |
| **Streaming** | `WebSocket /ws` | WebSocket | Real-time audio input |

### Comparison with whisper-trans

| Service | Batch | Streaming | API Compatibility |
|---------|-------|-----------|-------------------|
| **whisper-trans** | ✅ Only | ❌ No | whisper-asr-webservice |
| **whisper-live** | ✅ Yes | ✅ Yes | whisper-asr-webservice + WebSocket |

---

## Configuration

### Model Selection

Edit `ciu.defaults.toml.j2`:

```toml
[whisperlive]
model = "base"  # Options: tiny, base, small, medium, large
device = "cpu"
compute_type = "int8"  # int8 for CPU, float16 for GPU
```

**Model Comparison** (CPU-only):

| Model | Params | Memory | Speed | Quality |
|-------|--------|--------|-------|---------|
| tiny | 39M | ~1GB | Fast | Basic |
| base | 74M | ~1GB | Good | Good ⭐ Recommended |
| small | 244M | ~2GB | Moderate | Better |
| medium | 769M | ~5GB | Slow | Best |

**Recommendation**: Use `base` model for balanced performance on CPU.

---

## Features

### ✅ Voice Activity Detection (VAD)
- Automatically detects speech vs silence
- Reduces processing of background noise
- Configurable threshold

### ✅ WebSocket Streaming
- Real-time audio chunks (configurable chunk size)
- Low-latency transcription (~100-500ms)
- Supports correction/rephrase detection

### ✅ Multi-language Support
- Auto-detection or explicit language setting
- Transcription or translation tasks

---

## WebSocket Protocol

### Connection
```
ws://hostname/api/whisper/stream
```

### Message Format

**Client → Server (audio data)**:
```json
{
  "type": "audio",
  "data": "<base64-encoded-audio>",
  "format": "pcm_s16le",
  "sample_rate": 16000
}
```

**Server → Client (transcription)**:
```json
{
  "type": "transcript",
  "text": "transcribed text here",
  "timestamp": 1234567890.123,
  "is_final": false
}
```

**Server → Client (correction detection)**:
```json
{
  "type": "rollback",
  "seconds": 3,
  "reason": "correction_keyword"
}
```

---

## Resource Usage

### CPU-Only Performance (7 cores, base model)

| Metric | Value |
|--------|-------|
| **Latency** | 100-500ms per chunk |
| **CPU Usage** | 70-90% during active transcription |
| **Memory** | ~3GB |
| **Concurrent Streams** | 1-2 recommended |

### Optimization Tips

1. **Reduce chunk length** for lower latency (but higher CPU):
   ```toml
   chunk_length_s = 3  # Default: 5
   ```

2. **Use smaller model** for faster processing:
   ```toml
   model = "tiny"  # Faster, less accurate
   ```

3. **Adjust CPU limits** based on available cores:
   ```toml
   resource_cpu_limit = "6"  # Allow more CPU
   ```

---

## Correction/Rephrase Detection

WhisperLive supports automatic correction detection:

### Built-in VAD
- Detects pauses > 2 seconds
- Signals potential correction points

### Keyword Detection (Planned)
Keywords that trigger rollback:
- "no"
- "actually"
- "I mean"
- "let me rephrase"
- "wait"

### Timestamp-based Rollback
When correction detected:
1. Server sends `rollback` message
2. Client removes last N seconds of transcript
3. User continues speaking
4. New text replaces old

---

## Comparison: Batch vs Streaming

### Use Batch (`whisper-trans`) When:
- ✅ Transcribing complete audio files
- ✅ Need highest accuracy (larger model)
- ✅ Processing recorded content
- ✅ No time constraints

### Use Streaming (`whisper-live`) When:
- ✅ Real-time voice input
- ✅ Live dictation
- ✅ Interactive applications
- ✅ Need immediate feedback
- ✅ Support correction/"let me rephrase"

---

## Client Integration

### Python Example

```python
import asyncio
import websockets
import json
import base64

async def stream_audio():
    uri = "ws://localhost/api/whisper/stream"
    
    async with websockets.connect(uri) as websocket:
        # Send audio chunks
        with open("audio.pcm", "rb") as f:
            while True:
                chunk = f.read(16000)  # 1 second at 16kHz
                if not chunk:
                    break
                
                message = {
                    "type": "audio",
                    "data": base64.b64encode(chunk).decode(),
                    "format": "pcm_s16le",
                    "sample_rate": 16000
                }
                await websocket.send(json.dumps(message))
                
                # Receive transcription
                response = await websocket.recv()
                result = json.loads(response)
                
                if result["type"] == "transcript":
                    print(f"Transcript: {result['text']}")
                elif result["type"] == "rollback":
                    print(f"Rollback {result['seconds']} seconds")

asyncio.run(stream_audio())
```

### VS Code Extension Integration

See `../speech-to-copilot` for VS Code extension that supports both:
- Batch mode (upload complete file)
- Streaming mode (real-time dictation)

---

## Monitoring

### Health Check
```bash
curl http://localhost/api/whisper/stream/health
```

### Container Logs
```bash
docker logs -f voice2text-whisperlive-service
```

### Resource Usage
```bash
docker stats voice2text-whisperlive-service
```

---

## Troubleshooting

### High CPU Usage
- Reduce `chunk_length_s` in config
- Use smaller model (`tiny` or `base`)
- Reduce concurrent streams

### High Latency
- Increase `chunk_length_s` for larger batches
- Use faster model (`tiny`)
- Check CPU availability

### Connection Drops
- Check network stability
- Increase `chunk_length_s` to reduce message frequency
- Monitor memory usage (may need to increase limit)

### Poor Accuracy
- Use larger model (`small` or `medium`)
- Increase `vad_threshold` to filter noise
- Ensure audio quality (16kHz sample rate)

---

## Development

### Rebuild Container
```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/whisper-live
docker build -t voice2text/whisperlive:latest .
python3 ../../scripts/ciu/ciu.py
```

### Test Locally
```bash
# Start service
python3 ../../scripts/ciu/ciu.py

# Test WebSocket from another terminal
python3 test-streaming.py
```

---

## References

- **WhisperLive**: https://github.com/collabora/WhisperLive
- **faster-whisper**: https://github.com/SYSTRAN/faster-whisper
- **OpenAI Whisper**: https://github.com/openai/whisper

---

## Related Services

- **whisper-trans**: Batch transcription service
- **speech-to-copilot**: API gateway with WebSocket support
- **reverse-proxy**: TLS termination and routing

---

**Last Updated**: December 12, 2025
