"""
FastAPI Orchestrator Service for Speech-to-Copilot
Coordinates between SvelteKit webapp, whisper-trans, and oobabooga-llm services
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, WebSocket, HTTPException, Depends, BackgroundTasks, Header
from starlette.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, Field
import redis.asyncio as redis
from redis.asyncio import Redis

from services.whisper_client import WhisperClient
from services.llm_client import LLMClient  
# Placeholder imports for future components (scanner & audio processor stubs may not yet exist)
try:
    from services.repository_scanner import RepositoryScanner  # type: ignore
except Exception:  # pragma: no cover
    RepositoryScanner = None  # type: ignore
try:
    from services.audio_processor import AudioProcessor  # type: ignore
except Exception:  # pragma: no cover
    AudioProcessor = None  # type: ignore

from models.audio import AudioData, TranscriptionRequest, TranscriptionResponse
from models.enhancement import EnhancementRequest, EnhancementResponse
from utils.audio_utils import validate_audio_format
from utils.context_utils import build_context_prompt
from utils.diff_utils import compute_incremental_suffix
from utils.auth_utils import get_correlation_id, verify_token

# Structured logging setup
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

import os

# Configuration
API_KEY_REQUIRED = os.getenv("API_KEY_REQUIRED", "false").lower() == "true"
API_TOKEN = os.getenv("API_TOKEN", os.getenv("API_TOKEN_INTERNAL", "changeme-dev-token"))

def get_demo_mode() -> bool:
    """Dynamically check if demo mode is enabled from environment."""
    return os.getenv("DEMO_MODE", "false").lower() == "true"

# Service clients
whisper_client: Optional[WhisperClient] = None
llm_client: Optional[LLMClient] = None  
repository_scanner: Optional[Any] = None  # dynamic import fallback
audio_processor: Optional[Any] = None
redis_client: Optional[Redis] = None

# WebSocket connections
active_connections: Dict[str, WebSocket] = {}

def ensure_repository_scanner():
    """Ensure repository scanner is initialized, with lazy fallback"""
    global repository_scanner
    logger.debug(f"ensure_repository_scanner called, current scanner: {type(repository_scanner)}, is_none: {repository_scanner is None}")
    
    if repository_scanner is None:
        logger.info("Lazy initializing repository scanner")
        try:
            logger.debug(f"RepositoryScanner class available: {RepositoryScanner is not None}")
            if RepositoryScanner:
                workspace_path = os.getenv("WORKSPACE_PATH", "/home/vb/repos/vbpro")
                repository_scanner = RepositoryScanner(workspace_path)
                logger.info("Repository scanner lazy initialized successfully", workspace_path=workspace_path, type=str(type(repository_scanner)))
            else:
                raise ImportError("RepositoryScanner class not available")
        except Exception as e:
            logger.warning("Using dummy repository scanner due to initialization error", error=str(e))
            class DummyRepositoryScanner:
                async def health_check(self): 
                    logger.debug("DummyRepositoryScanner health_check called")
                    return True
                async def get_context(self, file_types=None, max_terms=20):
                    logger.debug("DummyRepositoryScanner get_context called") 
                    return "python, javascript, typescript, async, function, class, import, return"
                async def get_realtime_context(self):
                    logger.debug("DummyRepositoryScanner get_realtime_context called")
                    return "python, javascript, typescript, async"
                async def scan_repository(self, languages, force_refresh=False):
                    logger.debug("DummyRepositoryScanner scan_repository called")
                    return {"files_scanned": 0, "terms_extracted": 8, "languages": languages, "fallback": True}
            repository_scanner = DummyRepositoryScanner()
            logger.info("Dummy repository scanner created", type=str(type(repository_scanner)))
    
    logger.debug(f"ensure_repository_scanner returning: {type(repository_scanner)}")
    return repository_scanner

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    global whisper_client, llm_client, repository_scanner, audio_processor, redis_client
    
    # Startup
    logger.info("Starting Speech-to-Copilot API service")
    
    # Initialize Redis connection (optional in demo mode)
    try:
        redis_client_url = os.getenv("REDIS_URL", "redis://redis:6379")
        redis_client_instance = redis.from_url(redis_client_url, decode_responses=True)
        await redis_client_instance.ping()
        redis_client = redis_client_instance
        logger.info("Redis connection established", url=redis_client_url)
    except Exception as e:  # pragma: no cover - optional for demo
        demo_mode = get_demo_mode()
        if demo_mode:
            logger.warning("Redis not available; continuing in DEMO_MODE", error=str(e))
        else:
            logger.error("Redis connection failed", error=str(e))
            raise

    # Demo mode dummy service clients
    demo_mode = get_demo_mode()
    if demo_mode:
        logger.warning("Starting in DEMO_MODE: using dummy whisper & LLM clients")

        class DummyTranscription(BaseModel):  # lightweight stand-in
            text: str
            confidence: float = 0.9

        class DummyWhisperClient:
            def __init__(self):
                self._full_script = (
                    "def main():\n    print(\"hello world\")\n    return 0\n" * 2
                )

            async def health_check(self):
                return True

            async def transcribe(self, audio_data: str, format: str, language: Optional[str] = None):
                # Return whole script for batch
                return DummyTranscription(text=self._full_script[:120])

            async def transcribe_stream(self, audio_bytes: bytes, format: str = "wav"):
                # Use length of bytes to determine progressive slice
                factor = max(1, len(audio_bytes) // 400)  # grows every ~400 bytes
                length = min(len(self._full_script), factor * 10)
                return DummyTranscription(text=self._full_script[:length])

        class DummyEnhancementResponse(BaseModel):
            enhanced_text: str

        class DummyLLMClient:
            def __init__(self):
                pass

            async def health_check(self):
                return True

            async def enhance_text(self, enhancement_request):
                return DummyEnhancementResponse(enhanced_text=enhancement_request.text)

            async def enhance_text_stream(self, enhancement_request):
                return DummyEnhancementResponse(enhanced_text=enhancement_request.text)

        whisper_client = DummyWhisperClient()
        llm_client = DummyLLMClient()
    else:
        # Initialize real service clients
        whisper_client = WhisperClient(os.getenv("WHISPER_SERVICE_URL", "http://whisper-trans:9000"))
        llm_client = LLMClient(os.getenv("LLM_SERVICE_URL", "http://oobabooga-llm:8300"))
        try:
            await whisper_client.health_check()
            await llm_client.health_check()
            logger.info("All external services are healthy")
        except Exception as e:
            logger.error("External service health check failed", error=str(e))

    # Initialize repository scanner (always create, even if RepositoryScanner class import failed)
    logger.info("Initializing repository scanner...", RepositoryScanner_available=RepositoryScanner is not None)
    try:
        if RepositoryScanner:
            workspace_path = os.getenv("WORKSPACE_PATH", "/home/vb/repos/vbpro")  # Default to current workspace
            repository_scanner = RepositoryScanner(workspace_path)
            logger.info("RepositoryScanner initialized successfully", workspace_path=workspace_path, type=str(type(repository_scanner)))
        else:
            # Create dummy scanner if import failed
            class DummyRepositoryScanner:
                async def health_check(self): return True
                async def get_context(self, file_types=None, max_terms=20):
                    return "python, fastapi, websocket, async, await, class, function, import"
                async def get_realtime_context(self):
                    # Return a short comma-separated context snippet
                    return "python, fastapi, websocket, async"
                async def scan_repository(self, languages, force_refresh=False):
                    return {"files_scanned": 0, "terms_extracted": 8, "languages": languages, "cached": True, "terms": ["python", "fastapi", "websocket"]}
            repository_scanner = DummyRepositoryScanner()
            logger.warning("Using dummy RepositoryScanner (import failed)")
    except Exception as e:
        logger.error("RepositoryScanner initialization failed", error=str(e), exc_info=True)
        # Create minimal fallback
        class DummyRepositoryScanner:
            async def health_check(self): return True
            async def get_context(self, file_types=None, max_terms=20):
                return "python, javascript, typescript, async, function"
            async def scan_repository(self, languages, force_refresh=False):
                return {"files_scanned": 0, "terms_extracted": 5, "languages": languages, "fallback": True}
        repository_scanner = DummyRepositoryScanner()
        logger.warning("Using fallback DummyRepositoryScanner due to error")
    
    logger.info("Repository scanner setup complete", scanner_type=str(type(repository_scanner)), scanner_is_none=repository_scanner is None)
    try:
        audio_processor = AudioProcessor() if AudioProcessor else None
    except Exception as e:  # pragma: no cover
        logger.warning("AudioProcessor initialization failed", error=str(e))
        audio_processor = None
    
    yield
    
    # Shutdown
    logger.info("Shutting down Speech-to-Copilot API service")
    if redis_client:
        await redis_client.close()

# FastAPI app initialization
app = FastAPI(
    title="Speech-to-Copilot API",
    description="Orchestrates speech-to-text processing with repository context enhancement",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
try:
    from routers.debug import router as debug_router
    app.include_router(debug_router)
    logger.info("Debug router included")
except ImportError:
    logger.warning("Debug router not available")

# Request models
class AudioStreamData(BaseModel):
    audio_data: str = Field(..., description="Base64 encoded audio data")
    format: str = Field(default="wav", description="Audio format")
    sample_rate: int = Field(default=16000, description="Sample rate in Hz")
    channels: int = Field(default=1, description="Number of audio channels")

class HealthCheckResponse(BaseModel):
    status: str
    timestamp: datetime
    services: Dict[str, Any]
    version: str = "0.1.0"

# Dependency injection - imported from utils.auth_utils

# Health check endpoints
@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Comprehensive health check for all services"""
    correlation_id = str(uuid.uuid4())
    
    logger.info("Health check requested", correlation_id=correlation_id)
    
    services_status = {}
    overall_status = "healthy"
    
    # Check Redis
    try:
        await redis_client.ping()
        services_status["redis"] = {"status": "healthy", "response_time_ms": 0}
    except Exception as e:
        services_status["redis"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"
    
    # Check Whisper service
    try:
        whisper_healthy = await whisper_client.health_check()
        services_status["whisper"] = {"status": "healthy" if whisper_healthy else "unhealthy"}
    except Exception as e:
        services_status["whisper"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"
    
    # Check LLM service
    try:
        llm_healthy = await llm_client.health_check()
        services_status["llm"] = {"status": "healthy" if llm_healthy else "unhealthy"}
    except Exception as e:
        services_status["llm"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"
    
    # Check repository scanner
    try:
        scanner = ensure_repository_scanner()
        scanner_healthy = await scanner.health_check()
        services_status["scanner"] = {"status": "healthy" if scanner_healthy else "unhealthy"}
    except Exception as e:
        services_status["scanner"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"
    
    logger.info("Health check completed", 
                correlation_id=correlation_id, 
                overall_status=overall_status,
                services=services_status)
    
    return HealthCheckResponse(
        status=overall_status,
        timestamp=datetime.now(),
        services=services_status
    )

# Audio processing endpoints
@app.post("/api/transcribe", response_model=TranscriptionResponse, dependencies=[Depends(verify_token)])
async def transcribe_audio(
    request: TranscriptionRequest,
    correlation_id: str = Depends(get_correlation_id),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Process audio for transcription"""
    
    logger.info("Transcription request received", 
                correlation_id=correlation_id,
                audio_length=len(request.audio_data),
                format=request.format)
    
    start_time = datetime.now()
    
    try:
        # Validate audio format (skip in demo mode)
        demo_mode = get_demo_mode()
        if not demo_mode and not await validate_audio_format(request.audio_data, request.format):
            raise HTTPException(status_code=400, detail="Invalid audio format")
        
        # Get repository context if requested (before transcription for vocabulary correction)
        context = ""
        context_terms = None
        if request.enable_context:
            scanner = ensure_repository_scanner()
            context_terms = await scanner.get_context(
                file_types=request.context_languages,
                max_terms=20
            )
            # Convert list to string if necessary
            if isinstance(context_terms, list):
                context = ", ".join(context_terms)
            else:
                context = str(context_terms)
                context_terms = context.split(", ")
        
        # Process audio through whisper with context for vocabulary correction
        transcription = await whisper_client.transcribe(
            audio_data=request.audio_data,
            format=request.format,
            language=request.language,
            context_terms=context_terms
        )
        
        # Enhance text via LLM if requested
        enhanced_text = transcription.text
        if request.enable_enhancement:
            enhancement_request = EnhancementRequest(
                text=transcription.text,
                context=context,
                intent=request.intent,
                correlation_id=correlation_id
            )
            
            enhancement_response = await llm_client.enhance_text(enhancement_request)
            enhanced_text = enhancement_response.enhanced_text
        
        # Calculate processing time
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        response = TranscriptionResponse(
            text=enhanced_text,
            original_text=transcription.text,
            confidence=transcription.confidence,
            processing_time_ms=processing_time,
            correlation_id=correlation_id,
            context_used=bool(context),
            enhancement_used=request.enable_enhancement,
            vocabulary_corrected=transcription.vocabulary_corrected,
            self_correction_detected=transcription.self_correction_detected,
            correction_metadata=transcription.correction_metadata
        )
        
        # Cache result for potential reuse
        background_tasks.add_task(
            cache_transcription_result,
            correlation_id,
            response
        )
        
        logger.info("Transcription completed",
                    correlation_id=correlation_id,
                    processing_time_ms=processing_time,
                    confidence=transcription.confidence,
                    text_length=len(enhanced_text))
        
        return response
        
    except Exception as e:
        logger.error("Transcription failed",
                     correlation_id=correlation_id,
                     error=str(e),
                     exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

# WebSocket endpoint for real-time streaming
@app.websocket("/ws/audio")
async def websocket_audio_stream(websocket: WebSocket):
    """Handle real-time audio streaming and transcription"""
    connection_id = str(uuid.uuid4())
    await websocket.accept()
    active_connections[connection_id] = websocket
    logger.info("WebSocket connection established", connection_id=connection_id)

    audio_buffer = bytearray()
    chunk_count = 0
    last_transcript: str = ""
    last_context: str = ""
    last_emit_time: Optional[datetime] = None
    demo_accumulator: str = ""  # used only in DEMO_MODE to build progressive output

    try:
        # Initial handshake
        await websocket.send_json(
            {
                "type": "connection_established",
                "connection_id": connection_id,
                "timestamp": datetime.now().isoformat(),
                "demo_mode": get_demo_mode(),
            }
        )

        while True:
            try:
                data = await websocket.receive_json()
                if data.get("type") == "audio_chunk":
                    correlation_id = data.get("correlation_id", str(uuid.uuid4()))
                    chunk_count += 1
                    audio_data = AudioStreamData(**data.get("data", {}))
                    demo_mode = get_demo_mode()
                    if demo_mode:
                        # Pure synthetic incremental build (no real audio processing)
                        demo_accumulator_line = f"# chunk {chunk_count} added\n"
                        demo_accumulator += demo_accumulator_line
                        # base script reused from dummy whisper client
                        transcription = await whisper_client.transcribe_stream(b"", format=audio_data.format)
                        base_text = transcription.text
                        new_raw = (demo_accumulator + base_text)[: 60 + (chunk_count * 30)].rstrip()
                        # diff using utility
                        incremental_part, _ = compute_incremental_suffix(last_transcript, new_raw)
                        context_snippet = ""
                        context_changed = False
                        if repository_scanner:
                            try:
                                context_candidate = await repository_scanner.get_realtime_context()
                                if context_candidate != last_context:
                                    context_snippet = context_candidate
                                    context_changed = True
                                    last_context = context_candidate
                            except Exception:
                                context_snippet = ""
                        await websocket.send_json(
                            {
                                "type": "transcription_result",
                                "correlation_id": correlation_id,
                                "data": {
                                    "text": new_raw,
                                    "original_text": new_raw,
                                    "confidence": getattr(transcription, "confidence", 0.9),
                                    "chunk_count": chunk_count,
                                    "incremental": incremental_part,
                                    **({"context": context_snippet} if context_changed else {}),
                                    "context_changed": context_changed,
                                    "timestamp": datetime.now().isoformat(),
                                },
                            }
                        )
                        logger.info(
                            "Demo incremental emitted",
                            connection_id=connection_id,
                            chunk_count=chunk_count,
                            incremental_len=len(incremental_part),
                            total_len=len(new_raw),
                        )
                        last_transcript = new_raw
                        continue
                    # REAL (non-demo) path below
                    if audio_processor:
                        processed_audio = await audio_processor.process_chunk(
                            audio_data.audio_data,
                            audio_data.format,
                            audio_data.sample_rate,
                        )
                    else:
                        processed_audio = b""  # should not happen in real mode
                    audio_buffer.extend(processed_audio)
                    # Adaptive cadence: lower threshold if last emit was >3s ago, higher if <1s
                    base = audio_data.sample_rate * 2  # ~2s
                    if last_emit_time:
                        elapsed = (datetime.now() - last_emit_time).total_seconds()
                        if elapsed > 3:
                            min_buffer_size = int(base * 0.5)  # speed up
                        elif elapsed < 1:
                            min_buffer_size = int(base * 1.5)  # slow down to reduce churn
                        else:
                            min_buffer_size = base
                    else:
                        min_buffer_size = base
                    if len(audio_buffer) < min_buffer_size:
                        continue
                    transcription = await whisper_client.transcribe_stream(
                        bytes(audio_buffer), format=audio_data.format
                    )
                    new_raw = transcription.text.strip()
                    if not new_raw:
                        audio_buffer.clear()
                        continue
                    incremental_part, _ = compute_incremental_suffix(last_transcript, new_raw)
                    if incremental_part.strip():
                        context = ""
                        if repository_scanner:
                            try:
                                context = await repository_scanner.get_realtime_context()
                            except Exception as e:
                                logger.warning("Realtime context failed", error=str(e))
                        enhanced_text = new_raw
                        if context and len(incremental_part) > 8:
                            enhancement_request = EnhancementRequest(
                                text=new_raw,
                                context=context,
                                intent="code",
                                correlation_id=correlation_id,
                            )
                            enhancement_response = await llm_client.enhance_text_stream(
                                enhancement_request
                            )
                            enhanced_text = enhancement_response.enhanced_text
                        context_snippet = ""
                        context_changed = False
                        if repository_scanner:
                            try:
                                context_candidate = await repository_scanner.get_realtime_context()
                                if context_candidate != last_context:
                                    context_snippet = context_candidate
                                    context_changed = True
                                    last_context = context_candidate
                            except Exception:
                                context_snippet = ""
                        await websocket.send_json(
                            {
                                "type": "transcription_result",
                                "correlation_id": correlation_id,
                                "data": {
                                    "text": enhanced_text,
                                    "original_text": new_raw,
                                    "confidence": getattr(transcription, "confidence", 0.9),
                                    "chunk_count": chunk_count,
                                    "incremental": incremental_part,
                                    **({"context": context_snippet} if context_changed else {}),
                                    "context_changed": context_changed,
                                    "timestamp": datetime.now().isoformat(),
                                },
                            }
                        )
                        logger.info(
                            "Streaming transcription incremental sent",
                            connection_id=connection_id,
                            correlation_id=correlation_id,
                            chunk_count=chunk_count,
                            incremental_len=len(incremental_part),
                            total_len=len(enhanced_text),
                            demo_mode=get_demo_mode(),
                        )
                    last_transcript = new_raw
                    last_emit_time = datetime.now()
                    audio_buffer.clear()
                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                logger.info("WebSocket client disconnected", connection_id=connection_id)
                break
            except Exception as e:
                logger.error(
                    "Error processing WebSocket message",
                    connection_id=connection_id,
                    error=str(e),
                    exc_info=True,
                )
                try:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": str(e),
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                except Exception:
                    # WebSocket might be closed, just log and break
                    logger.debug("Could not send error message - WebSocket closed")
                    break
    except Exception as e:
        logger.error(
            "WebSocket connection error",
            connection_id=connection_id,
            error=str(e),
            exc_info=True,
        )
    finally:
        if connection_id in active_connections:
            del active_connections[connection_id]
        logger.info("WebSocket connection closed", connection_id=connection_id)

# Repository scanning endpoints
@app.get("/api/repository/scan", dependencies=[Depends(verify_token)])
async def scan_repository(
    languages: Optional[List[str]] = None,
    correlation_id: str = Depends(get_correlation_id)
):
    """Trigger repository scan for context building"""
    
    logger.info("Repository scan requested",
                correlation_id=correlation_id,
                languages=languages)
    
    try:
        scan_result = await repository_scanner.scan_repository(
            languages=languages or ["typescript", "python", "rust"],
            force_refresh=True
        )
        
        logger.info("Repository scan completed",
                    correlation_id=correlation_id,
                    files_scanned=scan_result.get("files_scanned", 0),
                    terms_extracted=scan_result.get("terms_extracted", 0))
        
        return {
            "status": "completed",
            "correlation_id": correlation_id,
            "scan_result": scan_result,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Repository scan failed",
                     correlation_id=correlation_id,
                     error=str(e),
                     exc_info=True)
        raise HTTPException(status_code=500, detail=f"Repository scan failed: {str(e)}")

@app.get("/api/repository/context")
async def get_repository_context(
    file_types: str = "python,typescript,javascript",
    max_terms: int = 100,
    correlation_id: str = Depends(get_correlation_id)
):
    """Get current repository context for text enhancement"""
    
    try:
        # Use helper to ensure repository scanner is initialized
        scanner = ensure_repository_scanner()
        
        context = await scanner.get_context(
            file_types=file_types.split(",") if file_types else ["python"],
            max_terms=max_terms
        )
        
        return {
            "context": context,
            "correlation_id": correlation_id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get repository context",
                     correlation_id=correlation_id,
                     error=str(e))
        raise HTTPException(status_code=500, detail=f"Context retrieval failed: {str(e)}")


# Compatibility endpoint expected by some tests/tools
@app.post("/api/context/repository")
async def post_repository_context(payload: dict):
    """Compatibility wrapper to return repository context when POSTed with
    languages and max_terms. Mirrors the behavior of /api/repository/context.
    """
    try:
        languages = payload.get("languages") or []
        max_terms = int(payload.get("max_terms", 100))
        scanner = ensure_repository_scanner()
        context = await scanner.get_context(file_types=languages, max_terms=max_terms)
        return {"context": context}
    except Exception as e:
        logger.error("Compatibility context endpoint failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# Debug and monitoring endpoints
@app.get("/debug/status")
async def debug_status():
    """Development debugging endpoint"""
    return {
        "active_connections": len(active_connections),
        "connection_ids": list(active_connections.keys()),
        "service_status": {
            "whisper_client": whisper_client is not None,
            "llm_client": llm_client is not None,
            "repository_scanner": repository_scanner is not None,
            "redis_client": redis_client is not None
        },
        "service_details": {
            "whisper_type": str(type(whisper_client)),
            "llm_type": str(type(llm_client)),
            "repository_type": str(type(repository_scanner)),
            "redis_type": str(type(redis_client))
        },
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/test/audio", dependencies=[Depends(verify_token)])
async def test_audio_pipeline(
    test_mode: bool = True,
    mock_audio: bool = True,
    correlation_id: str = Depends(get_correlation_id)
):
    """Test complete audio processing pipeline"""
    
    if not test_mode:
        raise HTTPException(status_code=400, detail="Test mode required")
    
    logger.info("Audio pipeline test started",
                correlation_id=correlation_id,
                mock_audio=mock_audio)
    
    try:
        # Mock audio data or use test file
        if mock_audio:
            test_audio = "mock_audio_data_base64_encoded"
        else:
            # Load test audio file
            test_audio = await audio_processor.load_test_audio()
        
        # Create test transcription request
        request = TranscriptionRequest(
            audio_data=test_audio,
            format="wav",
            language="en",
            enable_context=True,
            enable_enhancement=True,
            intent="code"
        )
        
        # Process through pipeline
        response = await transcribe_audio(request, correlation_id)
        
        logger.info("Audio pipeline test completed",
                    correlation_id=correlation_id,
                    success=True,
                    processing_time_ms=response.processing_time_ms)
        
        return {
            "status": "success",
            "pipeline_test": True,
            "correlation_id": correlation_id,
            "result": response,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Audio pipeline test failed",
                     correlation_id=correlation_id,
                     error=str(e))
        raise HTTPException(status_code=500, detail=f"Pipeline test failed: {str(e)}")

# Background tasks
async def cache_transcription_result(correlation_id: str, result: TranscriptionResponse):
    """Cache transcription result for debugging and analytics"""
    try:
        cache_key = f"transcription:{correlation_id}"
        cache_data = {
            "result": result.dict(),
            "timestamp": datetime.now().isoformat(),
            "ttl": 3600  # 1 hour
        }
        
        await redis_client.setex(
            cache_key,
            3600,
            json.dumps(cache_data)
        )
        
        logger.debug("Transcription result cached",
                     correlation_id=correlation_id,
                     cache_key=cache_key)
        
    except Exception as e:
        logger.warning("Failed to cache transcription result",
                       correlation_id=correlation_id,
                       error=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="debug"
    )

# Streaming Strategy Notes (Documentation Aid):
# - whisper-trans exposes only multipart POST /asr (no native low-latency chunk streaming)
# - We simulate "streaming" by buffering short intervals client-side (e.g. first at ~2s, then every 3-4s)
# - Backend may asynchronously invoke /asr on rolling window and emit diff results via WebSocket
# - Future optimization: maintain last N seconds context to reduce full reprocessing cost