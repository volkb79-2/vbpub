"""
Audio processing utilities
"""

import base64
import io
import wave
from typing import Optional, Tuple

import structlog

logger = structlog.get_logger()

async def validate_audio_format(audio_data: str, format: str) -> bool:
    """Validate audio data format and structure"""
    
    try:
        # Decode base64 audio data
        audio_bytes = base64.b64decode(audio_data)
        
        if format.lower() == "wav":
            return await _validate_wav_format(audio_bytes)
        elif format.lower() in ["mp3", "mpeg"]:
            return await _validate_mp3_format(audio_bytes)
        elif format.lower() == "webm":
            return await _validate_webm_format(audio_bytes)
        else:
            logger.warning("Unsupported audio format", format=format)
            return False
            
    except Exception as e:
        logger.error("Audio validation failed", format=format, error=str(e))
        return False

async def _validate_wav_format(audio_bytes: bytes) -> bool:
    """Validate WAV format"""
    try:
        # Check WAV header
        if len(audio_bytes) < 44:  # Minimum WAV header size
            return False
        
        # Check RIFF header
        if audio_bytes[:4] != b'RIFF':
            return False
        
        # Check WAVE format
        if audio_bytes[8:12] != b'WAVE':
            return False
        
        # Try to parse with wave module
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wav_file:
            frames = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            
            # Basic sanity checks
            if frames == 0 or sample_rate == 0 or channels == 0:
                return False
            
            logger.debug("WAV validation successful",
                        frames=frames,
                        sample_rate=sample_rate,
                        channels=channels)
            
            return True
            
    except Exception as e:
        logger.debug("WAV validation failed", error=str(e))
        return False

async def _validate_mp3_format(audio_bytes: bytes) -> bool:
    """Validate MP3 format"""
    try:
        # Check for MP3 frame header (simplified)
        if len(audio_bytes) < 4:
            return False
        
        # Look for MP3 sync word (0xFFE or 0xFFF)
        for i in range(min(1024, len(audio_bytes) - 1)):
            if audio_bytes[i] == 0xFF and (audio_bytes[i + 1] & 0xE0) == 0xE0:
                logger.debug("MP3 validation successful")
                return True
        
        return False
        
    except Exception as e:
        logger.debug("MP3 validation failed", error=str(e))
        return False

async def _validate_webm_format(audio_bytes: bytes) -> bool:
    """Validate WebM format"""
    try:
        # Check for WebM/Matroska header
        if len(audio_bytes) < 4:
            return False
        
        # WebM files start with EBML header (0x1A45DFA3)
        webm_header = bytes.fromhex("1A45DFA3")
        if audio_bytes[:4] == webm_header:
            logger.debug("WebM validation successful")
            return True
        
        return False
        
    except Exception as e:
        logger.debug("WebM validation failed", error=str(e))
        return False

async def get_audio_info(audio_data: str, format: str) -> Optional[dict]:
    """Extract audio metadata"""
    
    try:
        audio_bytes = base64.b64decode(audio_data)
        
        if format.lower() == "wav":
            return await _get_wav_info(audio_bytes)
        elif format.lower() in ["mp3", "mpeg"]:
            return await _get_mp3_info(audio_bytes)
        else:
            return {"format": format, "size": len(audio_bytes)}
            
    except Exception as e:
        logger.error("Failed to get audio info", format=format, error=str(e))
        return None

async def _get_wav_info(audio_bytes: bytes) -> dict:
    """Extract WAV file information"""
    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wav_file:
            return {
                "format": "wav",
                "sample_rate": wav_file.getframerate(),
                "channels": wav_file.getnchannels(),
                "sample_width": wav_file.getsampwidth(),
                "frames": wav_file.getnframes(),
                "duration": wav_file.getnframes() / wav_file.getframerate(),
                "size": len(audio_bytes)
            }
    except Exception as e:
        logger.error("Failed to extract WAV info", error=str(e))
        return {"format": "wav", "size": len(audio_bytes), "error": str(e)}

async def _get_mp3_info(audio_bytes: bytes) -> dict:
    """Extract MP3 file information (basic)"""
    # For now, just return basic info
    # Could use mutagen or similar library for detailed MP3 parsing
    return {
        "format": "mp3",
        "size": len(audio_bytes),
        "estimated_duration": len(audio_bytes) / 16000  # Rough estimate
    }

async def convert_sample_rate(audio_data: str, from_rate: int, to_rate: int) -> str:
    """Convert audio sample rate (placeholder)"""
    # This would require audio processing libraries like librosa or pydub
    # For now, return original data
    logger.warning("Sample rate conversion not implemented",
                  from_rate=from_rate, to_rate=to_rate)
    return audio_data

async def normalize_audio_level(audio_data: str) -> str:
    """Normalize audio levels (placeholder)"""
    # This would require audio processing libraries
    # For now, return original data
    logger.debug("Audio normalization not implemented")
    return audio_data