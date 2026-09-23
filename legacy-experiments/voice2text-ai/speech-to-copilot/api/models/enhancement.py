"""
Text enhancement data models
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

class EnhancementRequest(BaseModel):
    """Request model for text enhancement via LLM"""
    text: str = Field(..., description="Original text to enhance")
    context: Optional[str] = Field(None, description="Repository context for enhancement")
    intent: str = Field(
        default="code",
        description="Enhancement intent: 'code', 'comment', or 'documentation'"
    )
    correlation_id: str = Field(..., description="Request correlation ID")
    
    # Enhancement options
    max_length: int = Field(default=500, description="Maximum length of enhanced text")
    preserve_structure: bool = Field(default=True, description="Preserve original text structure")
    
class EnhancementResponse(BaseModel):
    """Response model for text enhancement"""
    enhanced_text: str = Field(..., description="Enhanced/corrected text")
    original_text: str = Field(..., description="Original input text")
    processing_time: float = Field(..., description="Processing time in seconds")
    correlation_id: str = Field(..., description="Request correlation ID")
    confidence: float = Field(..., description="Enhancement confidence score (0-1)")
    
    # Enhancement metadata
    changes_made: bool = Field(default=False, description="Whether any changes were made")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    
class ContextPrompt(BaseModel):
    """Model for context-aware prompts"""
    base_prompt: str = Field(..., description="Base prompt template")
    context_data: Optional[str] = Field(None, description="Repository context to inject")
    intent: str = Field(..., description="Processing intent")
    
class EnhancementMetrics(BaseModel):
    """Metrics for enhancement quality"""
    original_length: int = Field(..., description="Original text length")
    enhanced_length: int = Field(..., description="Enhanced text length")
    similarity_score: float = Field(..., description="Similarity to original (0-1)")
    processing_time: float = Field(..., description="Time taken for enhancement")
    model_used: str = Field(..., description="LLM model used for enhancement")