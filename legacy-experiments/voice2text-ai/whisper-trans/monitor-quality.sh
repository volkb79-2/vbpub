#!/bin/bash
echo "=== Whisper Service Quality Monitor ==="

# Check service status
echo "Service Status:"
docker-compose ps

# Check resource usage
echo -e "\nResource Usage:"
docker stats whisper-quality --no-stream

# Check model performance
echo -e "\nTesting transcription quality..."
time curl -s -X POST -H "content-type: multipart/form-data" \
  -F "audio_file=@test-audio.mp3" \
  "http://localhost:9000/asr?output=json&task=transcribe&vad_filter=true" \
  | jq '.text' 2>/dev/null || echo "JSON parsing failed"

# Check logs for errors
echo -e "\nRecent errors:"
docker-compose logs whisper-service --tail=20 | grep -i error || echo "No errors found"

echo -e "\n=== Quality Tips ==="
echo "1. Use longer audio samples (>30s) for better context"
echo "2. Ensure good audio quality (clear speech, minimal background noise)"
echo "3. Specify language when known for better accuracy"
echo "4. Use word_timestamps=true for better alignment"
echo "5. Enable VAD filtering for cleaner transcription"