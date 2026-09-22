"""
Audio processing data models
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class AudioData(BaseModel):
    """Base audio data model"""
    audio_data: str = Field(..., description="Base64 encoded audio data")
    format: str = Field(default="wav", description="Audio format (wav, mp3, webm)")
    sample_rate: int = Field(default=16000, description="Sample rate in Hz")
    channels: int = Field(default=1, description="Number of audio channels")
    duration: Optional[float] = Field(None, description="Audio duration in seconds")

class TranscriptionRequest(BaseModel):
    """Request model for audio transcription"""
    audio_data: str = Field(..., description="Base64 encoded audio data")
    format: str = Field(default="wav", description="Audio format")
    language: Optional[str] = Field(None, description="Expected language code (e.g., 'en', 'de')")
    
    # Enhancement options
    enable_context: bool = Field(default=True, description="Use repository context for corrections")
    enable_enhancement: bool = Field(default=True, description="Post-process text via LLM")
    
    # Context configuration
    context_languages: List[str] = Field(
        default=["typescript", "python", "rust"], 
        description="Programming languages for context extraction"
    )
    
    # Processing intent
    intent: str = Field(
        default="code",
        description="Processing intent: 'code', 'comment', or 'documentation'"
    )

class TranscriptionResponse(BaseModel):
    """Response model for audio transcription"""
    text: str = Field(..., description="Final processed text")
    original_text: str = Field(..., description="Raw whisper transcription")
    confidence: float = Field(..., description="Transcription confidence score (0-1)")
    
    # Processing metadata
    processing_time_ms: float = Field(..., description="Total processing time in milliseconds")
    correlation_id: str = Field(..., description="Request correlation ID")
    
    # Enhancement metadata
    context_used: bool = Field(..., description="Whether repository context was used")
    enhancement_used: bool = Field(..., description="Whether LLM enhancement was applied")
    
    # Vocabulary correction metadata (new)
    vocabulary_corrected: bool = Field(default=False, description="Whether vocabulary corrections were applied")
    self_correction_detected: bool = Field(default=False, description="Whether self-correction was detected")
    correction_metadata: Optional[dict] = Field(None, description="Details about corrections made")
    
    # Optional metadata
    detected_language: Optional[str] = Field(None, description="Detected language code")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")

class AudioStreamChunk(BaseModel):
    """Model for streaming audio chunks"""
    chunk_data: str = Field(..., description="Base64 encoded audio chunk")
    chunk_index: int = Field(..., description="Chunk sequence number")
    format: str = Field(default="wav", description="Audio format")
    sample_rate: int = Field(default=16000, description="Sample rate in Hz")
    is_final: bool = Field(default=False, description="Whether this is the final chunk")
    
class StreamingTranscriptionResponse(BaseModel):
    """Response model for streaming transcription"""
    text: str = Field(..., description="Transcribed text for this chunk")
    is_partial: bool = Field(default=True, description="Whether this is a partial result")
    chunk_index: int = Field(..., description="Corresponding chunk index")
    confidence: float = Field(..., description="Confidence score for this segment")
    correlation_id: str = Field(..., description="Request correlation ID")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")