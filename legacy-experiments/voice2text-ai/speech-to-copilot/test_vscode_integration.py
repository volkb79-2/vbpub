"""
Simple test to verify VSCode extension can connect to the API
"""

import asyncio
import json
import websockets

async def test_vscode_integration():
    print("Testing VSCode extension integration with running API...")
    
    try:
        # Connect to the same WebSocket endpoint the extension would use
        async with websockets.connect("ws://localhost:8000/ws/audio") as websocket:
            print("✓ WebSocket connection established")
            
            # Receive handshake
            handshake = await websocket.recv()
            handshake_data = json.loads(handshake)
            print(f"✓ Handshake received: {handshake_data['type']}")
            
            # Simulate what the VSCode extension would do - send audio chunk
            mock_audio_chunk = {
                "type": "audio_chunk",
                "data": {
                    "audio_data": "dGVzdCBhdWRpbyBkYXRh",  # base64 "test audio data"
                    "format": "wav",
                    "sample_rate": 16000,
                    "channels": 1
                }
            }
            
            await websocket.send(json.dumps(mock_audio_chunk))
            print("✓ Audio chunk sent")
            
            # Wait for transcription response
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            response_data = json.loads(response)
            
            if response_data.get('type') == 'transcription_result':
                incremental_text = response_data.get('data', {}).get('incremental', '')
                context = response_data.get('data', {}).get('context', '')
                
                print(f"✓ Received transcription result")
                print(f"  Incremental text: '{incremental_text[:50]}...'")
                if context:
                    print(f"  Context: '{context[:30]}...'")
                
                # This is the text that would be inserted into the VSCode editor
                print(f"✓ Text ready for insertion into VSCode: '{incremental_text.strip()}'")
                
                return True
            else:
                print(f"✗ Unexpected response type: {response_data.get('type')}")
                return False
                
    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        return False

async def main():
    success = await test_vscode_integration()
    
    if success:
        print("\n🎉 VSCode extension integration test PASSED!")
        print("The API is ready for VSCode extension to connect and receive transcriptions.")
    else:
        print("\n❌ VSCode extension integration test FAILED!")
        
    return success

if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)