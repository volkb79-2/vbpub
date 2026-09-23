"""
Debug and monitoring routes for autonomous development
"""

from datetime import datetime
from typing import Dict, Any
import json

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
import structlog

from utils.auth_utils import verify_token, get_correlation_id

logger = structlog.get_logger()

router = APIRouter(prefix="/debug", tags=["debug"])

# In-memory debug data (simple, not production)
debug_stats = {
    "requests": 0,
    "websocket_connections": 0,
    "transcription_requests": 0,
    "errors": 0,
    "last_activity": None
}

debug_logs = []
MAX_DEBUG_LOGS = 1000

def add_debug_log(level: str, message: str, **kwargs):
    """Add entry to debug log buffer"""
    debug_logs.append({
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "data": kwargs
    })
    
    # Keep only recent logs
    if len(debug_logs) > MAX_DEBUG_LOGS:
        debug_logs.pop(0)

@router.get("/health")
async def debug_health():
    """Comprehensive health check with service status"""
    from main import whisper_client, llm_client, repository_scanner, redis_client, active_connections
    
    health_status = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "whisper": {"available": whisper_client is not None, "healthy": False},
            "llm": {"available": llm_client is not None, "healthy": False},
            "scanner": {"available": repository_scanner is not None, "healthy": True},
            "redis": {"available": redis_client is not None, "healthy": False}
        },
        "connections": {
            "active_websockets": len(active_connections),
            "connection_ids": list(active_connections.keys())
        },
        "stats": debug_stats.copy()
    }
    
    # Test service health
    try:
        if whisper_client:
            health_status["services"]["whisper"]["healthy"] = await whisper_client.health_check()
    except Exception as e:
        logger.warning("Whisper health check failed", error=str(e))
    
    try:
        if llm_client:
            health_status["services"]["llm"]["healthy"] = await llm_client.health_check()
    except Exception as e:
        logger.warning("LLM health check failed", error=str(e))
    
    try:
        if redis_client:
            await redis_client.ping()
            health_status["services"]["redis"]["healthy"] = True
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
    
    return health_status

@router.get("/logs")
async def get_debug_logs(limit: int = 100, level: str = None):
    """Get recent debug logs for autonomous development"""
    filtered_logs = debug_logs
    
    if level:
        filtered_logs = [log for log in debug_logs if log["level"].lower() == level.lower()]
    
    return {
        "logs": filtered_logs[-limit:],
        "total": len(filtered_logs),
        "timestamp": datetime.now().isoformat()
    }

@router.get("/logs/stream")
async def stream_debug_logs():
    """Stream real-time debug logs (SSE)"""
    
    async def log_generator():
        last_seen = len(debug_logs)
        
        while True:
            import asyncio
            await asyncio.sleep(1)  # Poll every second
            
            if len(debug_logs) > last_seen:
                new_logs = debug_logs[last_seen:]
                for log in new_logs:
                    yield f"data: {json.dumps(log)}\n\n"
                last_seen = len(debug_logs)
    
    return StreamingResponse(
        log_generator(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache"}
    )

@router.get("/config")
async def get_debug_config():
    """Get current service configuration"""
    import os
    
    config = {
        "environment": {
            "DEMO_MODE": os.getenv("DEMO_MODE", "false"),
            "API_KEY_REQUIRED": os.getenv("API_KEY_REQUIRED", "false"),
            "WHISPER_SERVICE_URL": os.getenv("WHISPER_SERVICE_URL", "not_set"),
            "LLM_SERVICE_URL": os.getenv("LLM_SERVICE_URL", "not_set"),
            "LOG_LEVEL": os.getenv("LOG_LEVEL", "info")
        },
        "features": {
            "streaming_enabled": True,
            "context_scanning": True,
            "llm_enhancement": True,
            "repository_scanning": True
        },
        "timestamp": datetime.now().isoformat()
    }
    
    return config

@router.post("/test/websocket")
async def test_websocket_connection(correlation_id: str = Depends(get_correlation_id)):
    """Test WebSocket functionality without actual client"""
    from main import active_connections
    
    test_data = {
        "type": "transcription_result",
        "data": {
            "incremental": "test incremental text",
            "text": "full test text",
            "context": "test context terms",
            "chunk_count": 1
        }
    }
    
    sent_count = 0
    for conn_id, websocket in active_connections.items():
        try:
            await websocket.send_json(test_data)
            sent_count += 1
        except Exception as e:
            logger.warning("Failed to send test message", conn_id=conn_id, error=str(e))
    
    add_debug_log("info", "WebSocket test message sent", 
                  sent_count=sent_count, 
                  total_connections=len(active_connections),
                  correlation_id=correlation_id)
    
    return {
        "status": "sent",
        "connections_notified": sent_count,
        "total_connections": len(active_connections),
        "correlation_id": correlation_id,
        "timestamp": datetime.now().isoformat()
    }

@router.get("/stats")
async def get_debug_stats():
    """Get detailed statistics for monitoring"""
    from main import active_connections
    
    stats = debug_stats.copy()
    stats.update({
        "active_connections": len(active_connections),
        "memory_usage": "not_implemented",  # Could add psutil here
        "uptime": "not_implemented",
        "timestamp": datetime.now().isoformat()
    })
    
    return stats

@router.post("/stats/reset")
async def reset_debug_stats(correlation_id: str = Depends(get_correlation_id)):
    """Reset debug statistics"""
    global debug_stats, debug_logs
    
    old_stats = debug_stats.copy()
    debug_stats.update({
        "requests": 0,
        "websocket_connections": 0,
        "transcription_requests": 0,
        "errors": 0,
        "last_activity": datetime.now().isoformat()
    })
    
    debug_logs.clear()
    
    add_debug_log("info", "Debug stats reset", 
                  old_stats=old_stats, 
                  correlation_id=correlation_id)
    
    return {
        "status": "reset",
        "old_stats": old_stats,
        "correlation_id": correlation_id,
        "timestamp": datetime.now().isoformat()
    }

# Middleware to track request stats
def increment_request_counter():
    """Helper to increment request counter"""
    debug_stats["requests"] += 1
    debug_stats["last_activity"] = datetime.now().isoformat()

def increment_websocket_counter():
    """Helper to increment websocket counter"""
    debug_stats["websocket_connections"] += 1

def increment_error_counter():
    """Helper to increment error counter"""
    debug_stats["errors"] += 1