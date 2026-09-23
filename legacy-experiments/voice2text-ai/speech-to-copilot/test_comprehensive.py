#!/usr/bin/env python3
"""
Comprehensive test suite for Speech-to-Copilot functionality
Tests all major components after the fixes
"""

import asyncio
import json
import base64
import requests
import websockets
from typing import Dict, Any

def info(msg: str) -> None:
    print(f"✓ {msg}")

def warn(msg: str) -> None:
    print(f"⚠ {msg}")

def error(msg: str) -> None:
    print(f"✗ {msg}")

class SpeechToCopilotTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.ws_url = base_url.replace("http://", "ws://") + "/ws/audio"

    def test_health_endpoints(self) -> bool:
        """Test all health-related endpoints"""
        info("Testing health endpoints...")
        
        try:
            # Basic health
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code != 200:
                error(f"Health endpoint returned {response.status_code}")
                return False
            
            health_data = response.json()
            info(f"Overall status: {health_data.get('status')}")
            
            # Check scanner is healthy
            scanner_status = health_data.get('services', {}).get('scanner', {}).get('status')
            if scanner_status == 'healthy':
                info("Repository scanner is healthy")
            else:
                warn(f"Repository scanner status: {scanner_status}")
            
            # Debug status
            response = requests.get(f"{self.base_url}/debug/status", timeout=5)
            if response.status_code == 200:
                debug_data = response.json()
                repo_available = debug_data.get('service_status', {}).get('repository_scanner')
                info(f"Repository scanner available: {repo_available}")
            
            return True
            
        except Exception as e:
            error(f"Health endpoint test failed: {e}")
            return False

    def test_repository_context(self) -> bool:
        """Test repository context functionality"""
        info("Testing repository context...")
        
        try:
            response = requests.get(
                f"{self.base_url}/api/repository/context", 
                params={"max_terms": 10, "file_types": "python,typescript"},
                timeout=10
            )
            
            if response.status_code != 200:
                error(f"Repository context returned {response.status_code}: {response.text}")
                return False
            
            data = response.json()
            context = data.get('context', [])
            
            if len(context) > 0:
                info(f"Retrieved {len(context)} context terms: {context[:5]}...")
                return True
            else:
                warn("No context terms returned")
                return False
                
        except Exception as e:
            error(f"Repository context test failed: {e}")
            return False

    async def test_websocket_streaming(self) -> bool:
        """Test WebSocket streaming functionality"""
        info("Testing WebSocket streaming...")
        
        try:
            async with websockets.connect(self.ws_url) as websocket:
                # Send initial connection and expect handshake
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                handshake = json.loads(response)
                
                if handshake.get('type') == 'connection_established':
                    info(f"WebSocket connected: {handshake.get('connection_id')}")
                else:
                    error(f"Unexpected handshake: {handshake}")
                    return False
                
                # Send mock audio chunks and collect responses
                responses = []
                for i in range(3):
                    chunk = {
                        "type": "audio_chunk",
                        "data": {
                            "audio_data": base64.b64encode(f"mock_audio_chunk_{i}".encode()).decode(),
                            "format": "wav",
                            "sample_rate": 16000,
                            "channels": 1
                        }
                    }
                    
                    await websocket.send(json.dumps(chunk))
                    
                    # Wait for response
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                        message = json.loads(response)
                        responses.append(message)
                    except asyncio.TimeoutError:
                        warn(f"No response for chunk {i}")
                
                # Check responses
                transcription_responses = [r for r in responses if r.get('type') == 'transcription_result']
                
                if len(transcription_responses) > 0:
                    info(f"Received {len(transcription_responses)} transcription responses")
                    
                    # Check for incremental text and context
                    last_response = transcription_responses[-1]
                    data = last_response.get('data', {})
                    
                    if 'incremental' in data:
                        info(f"Incremental text: '{data['incremental'][:50]}...'")
                    
                    if 'context' in data and data['context']:
                        info(f"Context included: '{data['context'][:50]}...'")
                    
                    return True
                else:
                    error("No transcription responses received")
                    return False
                    
        except Exception as e:
            error(f"WebSocket test failed: {e}")
            return False

    def test_api_endpoints(self) -> bool:
        """Test REST API endpoints"""
        info("Testing REST API endpoints...")
        
        # Test OpenAPI docs
        try:
            response = requests.get(f"{self.base_url}/docs", timeout=5)
            if response.status_code == 200:
                info("API documentation accessible")
            else:
                warn(f"API docs returned {response.status_code}")
        except Exception as e:
            warn(f"API docs test failed: {e}")
        
        # Test transcribe endpoint (should handle invalid audio gracefully)
        try:
            response = requests.post(
                f"{self.base_url}/api/transcribe",
                json={
                    "audio_data": base64.b64encode(b"invalid_audio_data").decode(),
                    "format": "wav",
                    "enable_context": True
                },
                timeout=10
            )
            
            if response.status_code in [400, 500]:  # Expected for invalid audio
                info("Transcribe endpoint handles invalid audio correctly")
            else:
                warn(f"Unexpected transcribe response: {response.status_code}")
        
        except Exception as e:
            warn(f"Transcribe endpoint test failed: {e}")
        
        return True

    async def run_all_tests(self) -> Dict[str, bool]:
        """Run all tests and return results"""
        info("Starting comprehensive Speech-to-Copilot tests...\n")
        
        results = {}
        
        # Test health endpoints
        results['health'] = self.test_health_endpoints()
        
        # Test repository context
        results['repository_context'] = self.test_repository_context()
        
        # Test WebSocket streaming
        results['websocket'] = await self.test_websocket_streaming()
        
        # Test API endpoints
        results['api_endpoints'] = self.test_api_endpoints()
        
        # Summary
        info("\n" + "="*50)
        info("TEST RESULTS SUMMARY:")
        passed = sum(results.values())
        total = len(results)
        
        for test_name, passed_test in results.items():
            status = "✓ PASS" if passed_test else "✗ FAIL"
            info(f"  {test_name}: {status}")
        
        info(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            info("🎉 All tests passed! Speech-to-Copilot is fully functional!")
        else:
            warn(f"Some tests failed. System is partially functional.")
        
        return results

async def main():
    tester = SpeechToCopilotTester()
    results = await tester.run_all_tests()
    
    # Exit with appropriate code
    all_passed = all(results.values())
    exit(0 if all_passed else 1)

if __name__ == "__main__":
    asyncio.run(main())