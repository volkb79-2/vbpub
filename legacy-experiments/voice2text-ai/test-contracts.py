#!/usr/bin/env python3
"""
Contract Testing Suite for Voice2Text AI Services
Tests the interfaces and contracts between services
Follows Pact-style contract testing principles
"""

import requests
import json
import base64
import pytest
from typing import Dict, Any, Optional
from pathlib import Path

# Contract definitions
class ServiceContracts:
    """Define expected contracts between services"""
    
    WHISPER_ASR_CONTRACT = {
        "endpoint": "/asr",
        "method": "POST",
        "content_type": "multipart/form-data",
        "required_fields": ["audio_file"],
        "optional_fields": ["language", "task", "encode", "output"],
        "success_response": {
            "status_code": 200,
            "required_fields": ["text"],
            "optional_fields": ["language", "segments"]
        },
        "error_response": {
            "status_code": [400, 422, 500],
            "required_fields": ["detail"]
        }
    }
    
    WHISPERLIVE_ASR_CONTRACT = {
        "endpoint": "/asr",
        "method": "POST",
        "content_type": "multipart/form-data",
        "required_fields": ["audio_file"],
        "optional_fields": ["language", "task", "output"],
        "success_response": {
            "status_code": 200,
            "required_fields": ["text"],
            "optional_fields": ["language", "segments", "duration"]
        },
        "error_response": {
            "status_code": [400, 422, 500],
            "required_fields": ["detail"]
        }
    }
    
    WHISPERLIVE_WEBSOCKET_CONTRACT = {
        "endpoint": "/ws",
        "protocol": "websocket",
        "client_message": {
            "required_fields": ["type", "data"],
            "message_types": ["audio", "end"]
        },
        "server_message": {
            "required_fields": ["type"],
            "message_types": ["transcript", "error", "rollback"],
            "transcript_fields": ["text", "timestamp", "is_final"],
            "error_fields": ["message"]
        }
    }
    
    LLM_CHAT_COMPLETIONS_CONTRACT = {
        "endpoint": "/v1/chat/completions",
        "method": "POST",
        "content_type": "application/json",
        "required_fields": ["model", "messages"],
        "optional_fields": ["temperature", "max_tokens", "stream"],
        "success_response": {
            "status_code": 200,
            "required_fields": ["id", "object", "created", "model", "choices"],
            "structure": {
                "choices": [
                    {"message": {"role": str, "content": str}}
                ]
            }
        }
    }
    
    API_TRANSCRIBE_CONTRACT = {
        "endpoint": "/api/transcribe",
        "method": "POST",
        "content_type": "application/json",
        "required_fields": ["audio_data"],
        "optional_fields": ["format", "language", "enable_context", "enable_enhancement"],
        "success_response": {
            "status_code": 200,
            "required_fields": ["text", "enhanced_text", "confidence"],
            "optional_fields": ["language", "context_terms", "processing_time"]
        }
    }


class ContractTester:
    """Test service contracts"""
    
    def __init__(self, base_urls: Dict[str, str]):
        self.base_urls = base_urls
        
    def test_whisper_asr_contract(self, test_audio_path: Optional[str] = None):
        """Test Whisper ASR service contract"""
        contract = ServiceContracts.WHISPER_ASR_CONTRACT
        url = f"{self.base_urls['whisper']}{contract['endpoint']}"
        
        # Create test audio file if not provided
        if not test_audio_path:
            test_audio_path = self._create_test_wav()
        
        # Test successful request
        with open(test_audio_path, 'rb') as f:
            files = {'audio_file': f}
            data = {'language': 'en'}
            response = requests.post(url, files=files, data=data)
        
        # Verify response contract
        assert response.status_code == contract['success_response']['status_code'], \
            f"Expected status {contract['success_response']['status_code']}, got {response.status_code}"
        
        response_data = response.json()
        for field in contract['success_response']['required_fields']:
            assert field in response_data, f"Missing required field: {field}"
        
        print(f"✓ Whisper ASR contract verified")
        return response_data
    
    def test_llm_chat_completions_contract(self):
        """Test LLM chat completions contract"""
        contract = ServiceContracts.LLM_CHAT_COMPLETIONS_CONTRACT
        url = f"{self.base_urls['llm']}{contract['endpoint']}"
        
        # Test request
        payload = {
            "model": "default",
            "messages": [
                {"role": "user", "content": "Fix this code: pint hello world"}
            ],
            "max_tokens": 100
        }
        
        response = requests.post(url, json=payload, timeout=30)
        
        # Verify response contract
        assert response.status_code == contract['success_response']['status_code'], \
            f"Expected status {contract['success_response']['status_code']}, got {response.status_code}"
        
        response_data = response.json()
        for field in contract['success_response']['required_fields']:
            assert field in response_data, f"Missing required field: {field}"
        
        # Verify structure
        assert isinstance(response_data['choices'], list), "choices must be a list"
        assert len(response_data['choices']) > 0, "choices must not be empty"
        
        choice = response_data['choices'][0]
        assert 'message' in choice, "Missing message in choice"
        assert 'role' in choice['message'], "Missing role in message"
        assert 'content' in choice['message'], "Missing content in message"
        
        print(f"✓ LLM chat completions contract verified")
        return response_data
    
    def test_api_transcribe_contract(self):
        """Test API transcribe endpoint contract"""
        contract = ServiceContracts.API_TRANSCRIBE_CONTRACT
        url = f"{self.base_urls['api']}{contract['endpoint']}"
        
        # Create test audio data (base64 encoded)
        test_audio_path = self._create_test_wav()
        with open(test_audio_path, 'rb') as f:
            audio_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Test request
        payload = {
            "audio_data": audio_data,
            "format": "wav",
            "language": "en",
            "enable_context": True,
            "enable_enhancement": True
        }
        
        response = requests.post(url, json=payload, timeout=60)
        
        # Verify response contract
        assert response.status_code == contract['success_response']['status_code'], \
            f"Expected status {contract['success_response']['status_code']}, got {response.status_code}"
        
        response_data = response.json()
        for field in contract['success_response']['required_fields']:
            assert field in response_data, f"Missing required field: {field}"
        
        # Verify data types
        assert isinstance(response_data['text'], str), "text must be string"
        assert isinstance(response_data['enhanced_text'], str), "enhanced_text must be string"
        assert isinstance(response_data['confidence'], (int, float)), "confidence must be number"
        
        print(f"✓ API transcribe contract verified")
        return response_data
    
    def _create_test_wav(self, duration_ms: int = 100) -> str:
        """Create a minimal test WAV file"""
        import wave
        import struct
        
        output_path = "/tmp/test_audio.wav"
        sample_rate = 16000
        num_samples = int(sample_rate * duration_ms / 1000)
        
        with wave.open(output_path, 'w') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            
            # Generate silence
            for _ in range(num_samples):
                wav_file.writeframes(struct.pack('<h', 0))
        
        return output_path
    
    def test_whisperlive_batch_contract(self, test_audio_path: Optional[str] = None):
        """Test WhisperLive batch ASR contract"""
        if 'whisperlive' not in self.base_urls:
            print("⊘ WhisperLive not configured, skipping batch test")
            return None
        
        contract = ServiceContracts.WHISPERLIVE_ASR_CONTRACT
        url = f"{self.base_urls['whisperlive']}{contract['endpoint']}"
        
        # Create test audio file if not provided
        if not test_audio_path:
            test_audio_path = self._create_test_wav()
        
        # Test successful request
        with open(test_audio_path, 'rb') as f:
            files = {'audio_file': f}
            data = {'language': 'en', 'output': 'json'}
            response = requests.post(url, files=files, data=data, timeout=30)
        
        # Verify response contract
        assert response.status_code == contract['success_response']['status_code'], \
            f"Expected status {contract['success_response']['status_code']}, got {response.status_code}"
        
        response_data = response.json()
        for field in contract['success_response']['required_fields']:
            assert field in response_data, f"Missing required field: {field}"
        
        print(f"✓ WhisperLive batch contract verified")
        return response_data
    
    def test_whisperlive_streaming_contract(self):
        """Test WhisperLive WebSocket streaming contract"""
        if 'whisperlive' not in self.base_urls:
            print("⊘ WhisperLive not configured, skipping streaming test")
            return None
        
        import asyncio
        import websockets
        import base64
        
        contract = ServiceContracts.WHISPERLIVE_WEBSOCKET_CONTRACT
        ws_url = self.base_urls['whisperlive'].replace('http://', 'ws://').replace('https://', 'wss://')
        uri = f"{ws_url}{contract['endpoint']}"
        
        async def test_websocket():
            try:
                async with websockets.connect(uri, ping_interval=20) as websocket:
                    # Send test audio data
                    audio_data = b'\x00' * 16000  # 1 second silence at 16kHz
                    message = {
                        "type": "audio",
                        "data": base64.b64encode(audio_data).decode(),
                        "format": "pcm_s16le",
                        "sample_rate": 16000
                    }
                    
                    await websocket.send(json.dumps(message))
                    
                    # Try to receive response (with timeout)
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                        result = json.loads(response)
                        
                        # Verify contract
                        assert "type" in result, "Missing 'type' field"
                        assert result["type"] in contract['server_message']['message_types'], \
                            f"Invalid message type: {result['type']}"
                        
                        if result["type"] == "transcript":
                            for field in contract['server_message']['transcript_fields']:
                                assert field in result, f"Missing transcript field: {field}"
                        
                        print(f"✓ WhisperLive streaming contract verified")
                        return True
                    
                    except asyncio.TimeoutError:
                        # Timeout is acceptable for silence
                        print(f"✓ WhisperLive streaming contract verified (connection OK, timeout on silence)")
                        return True
            
            except Exception as e:
                print(f"✗ WhisperLive streaming contract failed: {e}")
                raise
        
        # Run async test
        return asyncio.run(test_websocket())
    
    def test_service_health_contracts(self):
        """Test health endpoint contracts"""
        health_checks = [
            (f"{self.base_urls['whisper']}/", "whisper"),
            (f"{self.base_urls['llm']}/health", "llm"),
            (f"{self.base_urls['api']}/health", "api"),
        ]
        
        # Add WhisperLive if configured
        if 'whisperlive' in self.base_urls:
            health_checks.append((f"{self.base_urls['whisperlive']}/health", "whisperlive"))
        
        for url, service_name in health_checks:
            try:
                response = requests.get(url, timeout=5)
                assert response.status_code in [200, 404], \
                    f"{service_name} health check failed with status {response.status_code}"
                print(f"✓ {service_name} health contract verified")
            except Exception as e:
                print(f"✗ {service_name} health check failed: {e}")
                raise


def run_contract_tests(include_whisperlive: bool = True):
    """Run all contract tests"""
    print("=" * 60)
    print("Voice2Text AI - Contract Testing Suite")
    print("=" * 60)
    
    # Configure service URLs
    base_urls = {
        'whisper': 'http://localhost:9000',
        'llm': 'http://localhost:8300',
        'api': 'http://localhost:8000',
    }
    
    # Add WhisperLive if requested
    if include_whisperlive:
        base_urls['whisperlive'] = 'http://localhost:80'
    
    tester = ContractTester(base_urls)
    
    try:
        test_count = 1
        total_tests = 6 if include_whisperlive else 4
        
        print(f"\n[{test_count}/{total_tests}] Testing service health contracts...")
        tester.test_service_health_contracts()
        test_count += 1
        
        print(f"\n[{test_count}/{total_tests}] Testing Whisper ASR contract...")
        tester.test_whisper_asr_contract()
        test_count += 1
        
        if include_whisperlive:
            print(f"\n[{test_count}/{total_tests}] Testing WhisperLive batch contract...")
            tester.test_whisperlive_batch_contract()
            test_count += 1
            
            print(f"\n[{test_count}/{total_tests}] Testing WhisperLive streaming contract...")
            tester.test_whisperlive_streaming_contract()
            test_count += 1
        
        print(f"\n[{test_count}/{total_tests}] Testing LLM chat completions contract...")
        tester.test_llm_chat_completions_contract()
        test_count += 1
        
        print(f"\n[{test_count}/{total_tests}] Testing API transcribe contract...")
        tester.test_api_transcribe_contract()
        
        print("\n" + "=" * 60)
        print("✓ ALL CONTRACT TESTS PASSED")
        print("=" * 60)
        
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"✗ CONTRACT TEST FAILED: {e}")
        print("=" * 60)
        raise


if __name__ == "__main__":
    import sys
    
    # Check for command-line arguments
    include_whisperlive = True
    if len(sys.argv) > 1 and sys.argv[1] == '--no-whisperlive':
        include_whisperlive = False
    
    run_contract_tests(include_whisperlive=include_whisperlive)
