#!/usr/bin/env python3
"""
WhisperLive Hybrid Server
Supports both WebSocket streaming and REST API batch transcription
"""

import os
import asyncio
import json
import base64
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel
import uvicorn

# Configuration from environment
MODEL = os.getenv("WHISPER_MODEL", "base")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")
TASK = os.getenv("WHISPER_TASK", "transcribe")
VAD_FILTER = os.getenv("VAD_FILTER", "true").lower() == "true"
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))

# Initialize FastAPI
app = FastAPI(
    title="WhisperLive Hybrid Server",
    description="Real-time streaming and batch transcription",
    version="1.0.0"
)

# Global model instance
whisper_model: Optional[WhisperModel] = None


def load_model():
    """Load Whisper model on startup"""
    global whisper_model
    print(f"[INFO] Loading Whisper model: {MODEL} ({DEVICE}, {COMPUTE_TYPE})")
    whisper_model = WhisperModel(
        MODEL,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        download_root="/data/models"
    )
    print(f"[SUCCESS] Model loaded successfully")


@app.on_event("startup")
async def startup_event():
    """Initialize model on startup"""
    load_model()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy" if whisper_model else "initializing",
        "model": MODEL,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE
    }


@app.post("/asr")
async def transcribe_batch(
    audio_file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    task: Optional[str] = Form("transcribe"),
    encode: bool = Form(True),
    output: str = Form("txt")
):
    """
    Batch transcription endpoint (compatible with whisper-asr-webservice)
    
    Args:
        audio_file: Audio file to transcribe
        language: Language code (default: auto-detect)
        task: transcribe or translate
        encode: Whether to encode response
        output: Output format (txt, json, etc.)
    
    Returns:
        Transcription result
    """
    if not whisper_model:
        raise HTTPException(status_code=503, detail="Model not initialized")
    
    try:
        # Save uploaded file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await audio_file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        # Transcribe
        segments, info = whisper_model.transcribe(
            tmp_path,
            language=language or LANGUAGE if language != "auto" else None,
            task=task,
            vad_filter=VAD_FILTER,
            vad_parameters={"threshold": VAD_THRESHOLD} if VAD_FILTER else None
        )
        
        # Collect segments
        text_segments = []
        full_text = []
        
        for segment in segments:
            text_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip()
            })
            full_text.append(segment.text.strip())
        
        # Clean up temp file
        Path(tmp_path).unlink()
        
        # Return result
        result = {
            "text": " ".join(full_text),
            "segments": text_segments,
            "language": info.language,
            "duration": info.duration
        }
        
        if output == "txt":
            return JSONResponse(content={"text": result["text"]})
        else:
            return JSONResponse(content=result)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_streaming(websocket: WebSocket):
    """
    WebSocket endpoint for real-time streaming transcription
    
    Protocol:
        Client -> Server: {"type": "audio", "data": "<base64>", "format": "pcm", "sample_rate": 16000}
        Server -> Client: {"type": "transcript", "text": "...", "timestamp": 123.45, "is_final": false}
        Server -> Client: {"type": "rollback", "seconds": 3, "reason": "correction_keyword"}
    """
    await websocket.accept()
    
    if not whisper_model:
        await websocket.send_json({
            "type": "error",
            "message": "Model not initialized"
        })
        await websocket.close()
        return
    
    try:
        print("[INFO] WebSocket client connected")
        
        # Audio buffer
        audio_buffer = bytearray()
        chunk_size = 16000 * 5  # 5 seconds at 16kHz
        
        while True:
            # Receive message
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "audio":
                # Decode audio data
                audio_data = base64.b64decode(message["data"])
                audio_buffer.extend(audio_data)
                
                # Process when buffer reaches chunk size
                if len(audio_buffer) >= chunk_size:
                    # Save to temp file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".raw") as tmp:
                        tmp.write(bytes(audio_buffer[:chunk_size]))
                        tmp_path = tmp.name
                    
                    try:
                        # Transcribe chunk
                        segments, _ = whisper_model.transcribe(
                            tmp_path,
                            language=LANGUAGE if LANGUAGE != "auto" else None,
                            task=TASK,
                            vad_filter=VAD_FILTER
                        )
                        
                        # Send transcription
                        for segment in segments:
                            if segment.text.strip():
                                await websocket.send_json({
                                    "type": "transcript",
                                    "text": segment.text.strip(),
                                    "timestamp": segment.start,
                                    "is_final": False
                                })
                    
                    finally:
                        # Clean up
                        Path(tmp_path).unlink()
                        audio_buffer = audio_buffer[chunk_size:]
            
            elif message.get("type") == "end":
                # Process remaining buffer
                if audio_buffer:
                    # Similar processing for remaining audio
                    pass
                break
    
    except Exception as e:
        print(f"[ERROR] WebSocket error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e)
        })
    
    finally:
        print("[INFO] WebSocket client disconnected")
        await websocket.close()


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "WhisperLive Hybrid Server",
        "version": "1.0.0",
        "endpoints": {
            "batch": "/asr (POST)",
            "streaming": "/ws (WebSocket)",
            "health": "/health (GET)"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "80")),
        log_level="info"
    )
