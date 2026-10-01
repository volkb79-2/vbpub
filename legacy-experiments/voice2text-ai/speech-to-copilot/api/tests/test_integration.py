"""
Integration tests for Speech-to-Copilot API
Tests the basic functionality and WebSocket streaming
"""

import asyncio
import json
import base64
from typing import Dict, Any

import pytest
import websockets
import httpx

# Test configuration
API_BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws/audio"


class TestHealthCheck:
    """Test basic health endpoints"""
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test /health endpoint returns valid response"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_BASE_URL}/health")
            assert response.status_code == 200
            
            data = response.json()
            assert "status" in data
            assert "timestamp" in data
            assert "services" in data
    
    @pytest.mark.asyncio 
    async def test_debug_health_endpoint(self):
        """Test /debug/health endpoint for detailed status"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_BASE_URL}/debug/health")
            assert response.status_code == 200
            
            data = response.json()
            assert "services" in data
            assert "connections" in data
            assert "stats" in data


class TestRepositoryScanner:
    """Test repository scanning functionality"""
    
    @pytest.mark.asyncio
    async def test_scanner_endpoint(self):
        """Test scanner endpoint returns terms"""
        async with httpx.AsyncClient() as client:
            payload = {
                "languages": ["python", "typescript"],
                "max_terms": 10
            }
            response = await client.post(
                f"{API_BASE_URL}/api/context/repository", 
                json=payload
            )
            assert response.status_code == 200
            
            data = response.json()
            assert "context" in data
            assert isinstance(data["context"], list)


class TestWebSocketStreaming:
    """Test WebSocket streaming functionality"""
    
    @pytest.mark.asyncio
    async def test_websocket_connection(self):
        """Test WebSocket connection establishment"""
        try:
            async with websockets.connect(WS_URL) as websocket:
                # Should receive initial connection message from server
                # websockets client returns a connection object without 'open'
                # so instead we wait for the server's initial handshake message
                msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                server_msg = json.loads(msg)
                assert server_msg.get("type") == "connection_established"
        except Exception as e:
            pytest.fail(f"WebSocket connection failed: {e}")
    
    @pytest.mark.asyncio
    async def test_demo_mode_streaming(self):
        """Test incremental streaming in demo mode"""
        received_messages = []
        
        try:
            async with websockets.connect(WS_URL) as websocket:
                # Send mock audio chunks
                for i in range(3):
                    mock_chunk = {
                        "type": "audio_chunk",
                        "data": {
                            "audio_data": base64.b64encode(f"mock_audio_{i}".encode()).decode(),
                            "format": "wav",
                            "sample_rate": 16000,
                            "channels": 1
                        }
                    }
                    
                    await websocket.send(json.dumps(mock_chunk))
                    
                    # Wait for response
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        message = json.loads(response)
                        received_messages.append(message)
                    except asyncio.TimeoutError:
                        pass
                
                # Verify we received incremental updates
                assert len(received_messages) > 0, "Should receive at least one response"
                
                # Check message structure
                for msg in received_messages:
                    if msg.get("type") == "transcription_result":
                        assert "data" in msg
                        assert "incremental" in msg["data"]
                        assert "text" in msg["data"]
        
        except Exception as e:
            pytest.fail(f"WebSocket streaming test failed: {e}")

    @pytest.mark.asyncio
    async def test_context_updates(self):
        """Test that context is included in WebSocket messages"""
        received_context = None
        
        try:
            async with websockets.connect(WS_URL) as websocket:
                # Drain initial handshake message if present
                try:
                    initial = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    # ignore initial server handshake
                except Exception:
                    pass
                # Send a chunk to trigger response
                mock_chunk = {
                    "type": "audio_chunk", 
                    "data": {
                        "audio_data": base64.b64encode(b"test_audio").decode(),
                        "format": "wav"
                    }
                }
                
                await websocket.send(json.dumps(mock_chunk))
                
                # Wait for response with context
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    message = json.loads(response)

                    if message.get("type") == "transcription_result":
                        received_context = message.get("data", {}).get("context")
                
                except asyncio.TimeoutError:
                    pass
                
                # Should receive context
                assert received_context is not None, "Should receive context in response"
                
        except Exception as e:
            pytest.fail(f"Context test failed: {e}")


class TestAPIEndpoints:
    """Test REST API endpoints"""
    
    @pytest.mark.asyncio
    async def test_transcribe_endpoint(self):
        """Test /api/transcribe endpoint"""
        async with httpx.AsyncClient() as client:
            # Create mock audio data
            mock_audio = base64.b64encode(b"mock_wav_data").decode()
            
            payload = {
                "audio_data": mock_audio,
                "format": "wav",
                "language": "en",
                "enable_context": True,
                "enable_enhancement": True,
                "intent": "code"
            }
            
            response = await client.post(
                f"{API_BASE_URL}/api/transcribe",
                json=payload
            )
            
            # In demo mode, should return 200 with mock response
            assert response.status_code == 200
            
            data = response.json()
            assert "text" in data
            assert "processing_time_ms" in data

    @pytest.mark.asyncio
    async def test_debug_status(self):
        """Test debug status endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_BASE_URL}/debug/status")
            assert response.status_code == 200
            
            data = response.json()
            assert "active_connections" in data
            assert "service_status" in data


# Utility functions for manual testing
def create_mock_audio_chunk(text: str = "test") -> str:
    """Create base64 encoded mock audio data"""
    return base64.b64encode(f"mock_wav_{text}".encode()).decode()


async def manual_websocket_test():
    """Manual WebSocket test for development"""
    print("Starting manual WebSocket test...")
    
    try:
        async with websockets.connect(WS_URL) as websocket:
            print("Connected to WebSocket")
            
            for i in range(5):
                # Send mock chunk
                chunk = {
                    "type": "audio_chunk",
                    "data": {
                        "audio_data": create_mock_audio_chunk(f"chunk_{i}"),
                        "format": "wav",
                        "sample_rate": 16000,
                        "channels": 1
                    }
                }
                
                print(f"Sending chunk {i+1}...")
                await websocket.send(json.dumps(chunk))
                
                # Wait for response
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                    message = json.loads(response)
                    print(f"Received: {message}")
                except asyncio.TimeoutError:
                    print("No response received")
                
                await asyncio.sleep(1)
    
    except Exception as e:
        print(f"Manual test failed: {e}")


if __name__ == "__main__":
    # Run manual test
    asyncio.run(manual_websocket_test())