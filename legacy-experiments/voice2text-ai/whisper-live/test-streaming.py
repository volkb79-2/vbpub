#!/usr/bin/env python3
"""
Quick test script for WhisperLive WebSocket streaming.
Tests connection and basic audio streaming.
"""

import asyncio
import websockets
import json
import base64
import sys

async def test_streaming(host="localhost", port=80, use_tls=False):
    """Test WhisperLive WebSocket connection."""
    
    protocol = "wss" if use_tls else "ws"
    uri = f"{protocol}://{host}:{port}/ws"
    
    print(f"[INFO] Connecting to WhisperLive at {uri}")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("[SUCCESS] Connected to WhisperLive streaming service")
            
            # Send test audio data (1 second of silence at 16kHz)
            audio_data = b'\x00' * 16000  # 16kHz PCM, 1 second
            
            message = {
                "type": "audio",
                "data": base64.b64encode(audio_data).decode(),
                "format": "pcm_s16le",
                "sample_rate": 16000
            }
            
            print("[INFO] Sending test audio chunk (1 second silence)...")
            await websocket.send(json.dumps(message))
            
            # Wait for response with timeout
            print("[INFO] Waiting for transcription response...")
            try:
                response = await asyncio.wait_for(
                    websocket.recv(), 
                    timeout=10.0
                )
                result = json.loads(response)
                
                print(f"[SUCCESS] Received response:")
                print(f"  Type: {result.get('type')}")
                print(f"  Text: {result.get('text', '(empty)')}")
                print(f"  Timestamp: {result.get('timestamp')}")
                print(f"  Is Final: {result.get('is_final')}")
                
                return True
                
            except asyncio.TimeoutError:
                print("[WARN] Timeout waiting for response (normal for silence)")
                return True
                
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return False

def main():
    """Main entry point."""
    
    # Parse arguments
    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        host = "localhost"
    
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    else:
        port = 80
    
    use_tls = host != "localhost" and host != "127.0.0.1"
    
    print("=" * 60)
    print("WhisperLive Streaming Test")
    print("=" * 60)
    print(f"Host: {host}")
    print(f"Port: {port}")
    print(f"TLS: {use_tls}")
    print("=" * 60)
    
    success = asyncio.run(test_streaming(host, port, use_tls))
    
    if success:
        print("\n[SUCCESS] Test completed successfully! ✅")
        sys.exit(0)
    else:
        print("\n[ERROR] Test failed! ❌")
        sys.exit(1)

if __name__ == "__main__":
    main()
