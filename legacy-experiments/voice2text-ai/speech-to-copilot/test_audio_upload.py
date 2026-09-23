#!/usr/bin/env python3
"""
Test script for speech-to-copilot with real audio file
"""
import os
import sys
import asyncio
import aiohttp
import json
import base64
from pathlib import Path

async def test_audio_upload(audio_file_path, api_url="http://localhost:8001"):
    """Test audio file upload to the speech-to-copilot API"""
    
    if not os.path.exists(audio_file_path):
        print(f"❌ Audio file not found: {audio_file_path}")
        return False
    
    print(f"🎯 Testing audio upload: {audio_file_path}")
    print(f"📡 API URL: {api_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            # First check if API is healthy
            async with session.get(f"{api_url}/health") as response:
                if response.status != 200:
                    print(f"❌ API health check failed: {response.status}")
                    return False
                health_data = await response.json()
                print(f"✅ API Health: {health_data}")
            
            # Read and encode the audio file
            with open(audio_file_path, 'rb') as audio_file:
                audio_bytes = audio_file.read()
                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                
                # Create the JSON request
                request_data = {
                    "audio_data": audio_b64,
                    "format": "m4a",  # Since our test file is M4A
                    "language": "de",  # German, since it's likely German audio
                    "enable_context": True,
                    "enable_enhancement": True,
                    "intent": "code"
                }
                
                async with session.post(f"{api_url}/api/transcribe", 
                                      json=request_data,
                                      headers={'Content-Type': 'application/json'}) as response:
                    print(f"📤 Upload Status: {response.status}")
                    
                    if response.status == 200:
                        result = await response.json()
                        print(f"✅ Transcription Result:")
                        print(f"   Text: {result.get('text', 'No text returned')}")
                        print(f"   Confidence: {result.get('confidence', 'N/A')}")
                        print(f"   Processing Time: {result.get('processing_time', 'N/A')}s")
                        if result.get('context_suggestions'):
                            print(f"   Context Suggestions: {result['context_suggestions']}")
                        return True
                    else:
                        error_text = await response.text()
                        print(f"❌ Upload failed: {response.status} - {error_text}")
                        return False
                        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        return False

async def main():
    # Test with the demo audio file
    audio_file = "testdata/Aufnahme.m4a"
    
    print("🎤 Speech-to-Copilot Audio Test")
    print("=" * 50)
    
    # Test the actual audio file (even though it's m4a)
    success = await test_audio_upload(audio_file)
    
    if success:
        print("🎉 Test completed successfully!")
        return 0
    else:
        print("💥 Test failed!")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))