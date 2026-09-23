"""
Whisper-Trans Service Client
Handles communication with existing whisper-trans service
Includes vocabulary correction post-processing
"""

import asyncio
import base64
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

import httpx
import structlog
from pydantic import BaseModel

logger = structlog.get_logger()

class TranscriptionResult(BaseModel):
    text: str
    confidence: float = 0.0
    language: Optional[str] = None
    processing_time: Optional[float] = None
    vocabulary_corrected: bool = False
    self_correction_detected: bool = False
    correction_metadata: Optional[Dict[str, Any]] = None

class WhisperClient:
    """Client for whisper-trans service integration"""
    
    def __init__(
        self,
        base_url: str = "http://whisper-trans:9000",
        enable_vocabulary_correction: bool = True
    ):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=30.0)
        self.logger = logger.bind(service="whisper_client")
        
        # Initialize vocabulary corrector if enabled
        self.enable_vocabulary_correction = enable_vocabulary_correction
        self.vocabulary_corrector = None
        
        if enable_vocabulary_correction:
            try:
                from services.vocabulary_corrector import VocabularyCorrector
                self.vocabulary_corrector = VocabularyCorrector()
                self.logger.info("Vocabulary correction enabled")
            except ImportError:
                self.logger.warning("VocabularyCorrector not available, correction disabled")
                self.enable_vocabulary_correction = False
    
    async def health_check(self) -> bool:
        """Check if whisper-trans service is healthy"""
        try:
            response = await self.client.get(f"{self.base_url}/health")
            is_healthy = response.status_code == 200
            
            self.logger.info("Whisper service health check",
                           healthy=is_healthy,
                           status_code=response.status_code)
            
            return is_healthy
            
        except Exception as e:
            self.logger.error("Whisper service health check failed", error=str(e))
            return False
    
    async def transcribe(
        self,
        audio_data: str,
        format: str = "wav",
        language: Optional[str] = None,
        context_terms: Optional[List[str]] = None
    ) -> TranscriptionResult:
        """Transcribe audio using whisper-trans service
        
        Args:
            audio_data: Base64 encoded audio data
            format: Audio format (wav, mp3, etc.)
            language: Language code (optional)
            context_terms: Technical terms for vocabulary correction
        """
        
        start_time = datetime.now()
        correlation_id = f"whisper_{int(start_time.timestamp())}"
        
        self.logger.info("Starting whisper transcription",
                        correlation_id=correlation_id,
                        format=format,
                        language=language,
                        audio_size=len(audio_data))
        
        try:
            # Prepare payload for whisper-trans API
            payload = {
                "audio": audio_data,
                "format": format,
                "task": "transcribe"
            }
            
            if language:
                payload["language"] = language
            
            # Convert base64 to bytes for multipart upload
            audio_bytes = base64.b64decode(audio_data)
            
            # Prepare multipart form data for whisper-trans /asr endpoint
            files = {
                'audio_file': ('audio.wav', audio_bytes, 'audio/wav')
            }
            
            params = {
                'output': 'json',
                'task': 'transcribe',
                'vad_filter': 'true'
            }
            
            if language:
                params['language'] = language
            
            # Make request to whisper-trans /asr endpoint
            response = await self.client.post(
                f"{self.base_url}/asr",
                files=files,
                params=params
            )
            
            response.raise_for_status()
            result_data = response.json()
            
            # Extract transcription results
            text = result_data.get("text", "")
            confidence = result_data.get("confidence", 0.0)
            detected_language = result_data.get("language")
            
            # Apply vocabulary correction if enabled
            vocabulary_corrected = False
            self_correction_detected = False
            correction_metadata = None
            
            if self.enable_vocabulary_correction and self.vocabulary_corrector and text:
                # Detect self-corrections first
                has_correction, correction_info = self.vocabulary_corrector.detect_self_corrections(text)
                self_correction_detected = has_correction
                
                if has_correction:
                    # Extract the corrected intent
                    text = self.vocabulary_corrector.extract_corrected_intent(
                        text, has_correction, correction_info
                    )
                    correction_metadata = correction_info
                
                # Apply vocabulary corrections
                corrected_text, vocab_metadata = self.vocabulary_corrector.correct_vocabulary(
                    text, context_terms
                )
                
                if corrected_text != text:
                    vocabulary_corrected = True
                    text = corrected_text
                    
                    # Merge metadata
                    if correction_metadata:
                        correction_metadata["vocabulary_corrections"] = vocab_metadata
                    else:
                        correction_metadata = vocab_metadata
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            result = TranscriptionResult(
                text=text,
                confidence=confidence,
                language=detected_language,
                processing_time=processing_time,
                vocabulary_corrected=vocabulary_corrected,
                self_correction_detected=self_correction_detected,
                correction_metadata=correction_metadata
            )
            
            self.logger.info("Whisper transcription completed",
                           correlation_id=correlation_id,
                           processing_time=processing_time,
                           confidence=confidence,
                           text_length=len(text),
                           detected_language=detected_language,
                           vocabulary_corrected=vocabulary_corrected,
                           self_correction_detected=self_correction_detected)
            
            return result
            
        except httpx.HTTPStatusError as e:
            self.logger.error("Whisper service HTTP error",
                            correlation_id=correlation_id,
                            status_code=e.response.status_code,
                            response_text=e.response.text)
            
            # Return empty result on error
            return TranscriptionResult(text="", confidence=0.0)
            
        except Exception as e:
            self.logger.error("Whisper transcription failed",
                            correlation_id=correlation_id,
                            error=str(e),
                            exc_info=True)
            
            # Return empty result on error
            return TranscriptionResult(text="", confidence=0.0)
    
    async def transcribe_stream(
        self,
        audio_chunk: bytes,
        format: str = "wav"
    ) -> TranscriptionResult:
        """Transcribe audio chunk for streaming"""
        
        # Convert bytes to base64 for API
        audio_b64 = base64.b64encode(audio_chunk).decode('utf-8')
        
        # Use regular transcribe method for now
        # In future, could implement streaming-specific endpoint
        return await self.transcribe(audio_b64, format)
    
    async def get_supported_languages(self) -> list[str]:
        """Get list of supported languages from whisper service"""
        try:
            response = await self.client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            
            data = response.json()
            languages = data.get("languages", ["en", "de", "fr", "es", "it"])
            
            self.logger.info("Retrieved supported languages",
                           languages_count=len(languages))
            
            return languages
            
        except Exception as e:
            self.logger.error("Failed to get supported languages", error=str(e))
            return ["en"]  # Fallback to English
    
    async def get_model_info(self) -> Dict[str, Any]:
        """Get information about loaded whisper model"""
        try:
            response = await self.client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            self.logger.error("Failed to get model info", error=str(e))
            return {"model": "unknown", "languages": ["en"]}
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
        self.logger.info("Whisper client closed")