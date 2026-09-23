"""
Authentication and correlation utilities
"""

import uuid
import os
from typing import Optional

from fastapi import HTTPException, Header, Depends
try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Configuration
API_KEY_REQUIRED = os.getenv("API_KEY_REQUIRED", "false").lower() == "true"
API_TOKEN = os.getenv("API_TOKEN", "changeme-dev-token")

async def verify_token(x_api_token: Optional[str] = Header(None)) -> bool:
    """Verify API token if authentication is required"""
    if not API_KEY_REQUIRED:
        return True
        
    if not x_api_token:
        logger.warning("Missing API token in request")
        raise HTTPException(
            status_code=401, 
            detail="API token required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if x_api_token != API_TOKEN:
        logger.warning("Invalid API token", token_prefix=x_api_token[:8] if x_api_token else "")
        raise HTTPException(
            status_code=401,
            detail="Invalid API token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return True

def get_correlation_id(x_correlation_id: Optional[str] = Header(None)) -> str:
    """Generate or extract correlation ID for request tracing"""
    if x_correlation_id:
        return x_correlation_id
    return str(uuid.uuid4())[:8]  # Short correlation ID for easier tracking