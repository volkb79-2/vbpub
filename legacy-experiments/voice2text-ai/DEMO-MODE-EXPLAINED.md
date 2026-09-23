# Demo Mode Explained

## What is Demo Mode?

Demo Mode is a special operational mode for the Speech-to-Copilot API that allows you to test and develop the system **without requiring the external Whisper transcription and LLM services to be running**. This is incredibly valuable for development, testing, and demonstrations.

## How Demo Mode Works

When `DEMO_MODE=true` is set in the configuration:

### 1. **Simulated Transcription**
Instead of sending audio to the Whisper service, the API returns **simulated progressive transcription**:
- Returns incremental text updates mimicking real transcription behavior
- Generates Python code snippets progressively
- Simulates realistic timing (partial results every few seconds)
- Demonstrates streaming WebSocket functionality

### 2. **Simulated Enhancement**
Instead of calling the LLM service for text enhancement:
- Returns lightly modified versions of the input text
- Adds professional formatting and structure
- Demonstrates the enhancement pipeline without heavy ML operations

### 3. **Mock Repository Context**
Instead of scanning actual repository files:
- Rotates through predefined technical terms
- Demonstrates context-aware vocabulary injection
- Shows how technical terms would be prioritized in real usage

## Benefits of Demo Mode

### ✅ **Development & Testing**
- **No External Dependencies**: Test complete pipeline without Whisper/LLM services
- **Predictable Output**: Consistent dummy responses for testing WebSocket streaming
- **Rapid Iteration**: Develop frontend/integration without waiting for transcription
- **CI/CD Integration**: Automated testing without heavy ML services

### ✅ **Progressive Enhancement**
- **Fallback Mode**: Graceful degradation when external services are unavailable
- **Development Flow**: Perfect for VSCode extension development and testing
- **Demo Presentations**: Show system functionality without complex setup

### ✅ **Architecture Validation**
- **Service Integration**: Validates complete pipeline architecture
- **API Contracts**: Ensures all interfaces work correctly
- **Performance Testing**: Load testing without expensive ML operations

## Enabling/Disabling Demo Mode

### Configuration File Method (Recommended)

Edit `speech-to-copilot/compose.config.sample.toml`:

```toml
[api]
demo_mode = true  # Set to false for production
```

Then restart the service:
```bash
cd speech-to-copilot
python3 ../../scripts/ciu/ciu.py
```

### Environment Variable Method

```bash
# Enable demo mode
export DEMO_MODE=true

# Or set in docker-compose environment
docker compose up -d
```

## Demo Mode Output Examples

### Simulated Transcription Stream

When you send audio (or fake audio data) in demo mode, you'll receive progressive updates:

```json
{
  "type": "partial",
  "text": "def calculate_sum(a, b):",
  "timestamp": 0.5
}
```

```json
{
  "type": "partial", 
  "text": "def calculate_sum(a, b):\n    return a + b",
  "timestamp": 1.0
}
```

```json
{
  "type": "final",
  "text": "def calculate_sum(a, b):\n    return a + b\n\nresult = calculate_sum(10, 20)\nprint(f'Sum: {result}')",
  "timestamp": 2.0
}
```

### Mock Context Rotation

Demo mode cycles through technical terms to demonstrate context awareness:
- First request: `["Python", "FastAPI", "async", "await"]`
- Second request: `["Docker", "container", "kubernetes", "deployment"]`
- Third request: `["PostgreSQL", "database", "query", "transaction"]`
- And so on...

## Testing Demo Mode

### Basic Health Check

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected output:
```json
{
  "status": "healthy",
  "demo_mode": true,
  "services": {
    "whisper": "demo",
    "llm": "demo"
  }
}
```

### Test Transcription Endpoint

```bash
curl -s -X POST http://localhost:8000/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "audio_data": "fake_base64_audio_data",
    "format": "wav",
    "enable_context": true,
    "enable_enhancement": true
  }' | python3 -m json.tool
```

Expected output:
```json
{
  "text": "def example_function():\n    pass",
  "context_used": ["Python", "FastAPI", "async"],
  "enhanced": true,
  "demo_mode": true
}
```

### WebSocket Demo

You can test WebSocket streaming using the provided demo script:

```bash
cd speech-to-copilot/api/scripts
python3 ws_demo.py
```

This will:
1. Connect to the WebSocket endpoint
2. Send simulated audio chunks
3. Receive and display progressive transcription updates
4. Demonstrate the complete streaming workflow

## When to Use Demo Mode

### ✅ Use Demo Mode When:
- Developing the frontend/UI without backend services
- Testing API integration and WebSocket streaming
- Running automated tests in CI/CD pipelines
- Demonstrating the system to stakeholders
- Debugging frontend issues without backend complexity
- Developing VSCode extension integration

### ❌ Disable Demo Mode When:
- Testing actual transcription accuracy
- Validating Whisper model performance
- Testing LLM enhancement quality
- Benchmarking end-to-end latency
- Running production workloads
- Testing with real audio recordings

## Transitioning from Demo to Production

### Step 1: Verify External Services
```bash
# Ensure Whisper is running
curl http://localhost:9000/docs

# Ensure LLM is running  
curl http://localhost:8300/health
```

### Step 2: Update Configuration
Edit `compose.config.sample.toml`:
```toml
[api]
demo_mode = false
whisper_service_url = "http://whisper-service:9000"
llm_service_url = "http://openai-shim:8300"
```

### Step 3: Restart Service
```bash
cd speech-to-copilot
python3 ../../scripts/ciu/ciu.py
```

### Step 4: Test Real Integration
```bash
# Upload actual audio file
curl -X POST http://localhost:8000/api/transcribe \
  -F "audio_file=@test.wav"
```

## Demo Mode Implementation Details

The demo mode is implemented in the FastAPI service with:

### Service Stubs
- `whisper_client.py`: Returns simulated transcription when in demo mode
- `llm_client.py`: Returns mock-enhanced text when in demo mode
- `repository_scanner.py`: Rotates through predefined technical terms

### Configuration Check
```python
DEMO_MODE = os.getenv('DEMO_MODE', 'false').lower() == 'true'

if DEMO_MODE:
    # Use simulated responses
    return mock_transcription(audio_data)
else:
    # Call actual Whisper service
    return await whisper_client.transcribe(audio_data)
```

### Progressive Simulation
Demo mode implements realistic timing:
```python
async def simulate_streaming_transcription():
    chunks = ["def ", "calculate_sum", "(a, b):", "\n    return a + b"]
    for chunk in chunks:
        await asyncio.sleep(0.5)  # Simulate processing time
        yield {"type": "partial", "text": chunk}
```

## FAQ

**Q: Does demo mode require any external services?**  
A: No, demo mode is completely self-contained. Only the Speech-to-Copilot API service needs to be running.

**Q: Is the simulated output realistic?**  
A: Yes, it mimics the timing, structure, and behavior of real transcription, making it excellent for testing integration without actual ML models.

**Q: Can I mix demo and real services?**  
A: Yes, you can configure Whisper to be real while LLM is in demo mode (or vice versa) by using environment variables for each service.

**Q: Does demo mode affect performance testing?**  
A: Demo mode is much faster than real transcription (no ML inference), so it's not suitable for performance benchmarking. Use it for functional testing only.

**Q: Is demo mode enabled by default?**  
A: Yes, in the current configuration. This makes initial setup and testing easier. Change to `false` for production use.

## Troubleshooting Demo Mode

### Demo Mode Not Working

If demo mode doesn't seem to be working:

1. **Check Configuration**
   ```bash
   docker exec speech-to-copilot-api env | grep DEMO_MODE
   ```

2. **Check Logs**
   ```bash
   docker logs speech-to-copilot-api | grep -i demo
   ```

3. **Verify Health Endpoint**
   ```bash
   curl http://localhost:8000/health
   # Should show "demo_mode": true
   ```

### Accidentally Running Real Services in Demo Mode

If external services are running but demo mode is enabled:
- External services will be idle (not called)
- System will use simulated responses instead
- No impact on functionality, just wasted resources
- Simply disable demo mode to use real services

## Summary

Demo Mode is a **powerful development and testing feature** that:
- ✅ Enables rapid development without complex dependencies
- ✅ Provides predictable, testable behavior
- ✅ Demonstrates complete system functionality
- ✅ Facilitates CI/CD and automated testing
- ✅ Acts as a fallback when services are unavailable

**Keep demo mode enabled during development, and disable it for production deployments.**
