"""
Oobabooga-LLM Service Client
Handles text enhancement and post-processing via oobabooga-llm service
"""

import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path
import os
from datetime import datetime

import httpx
import structlog
from pydantic import BaseModel

from models.enhancement import EnhancementRequest, EnhancementResponse

logger = structlog.get_logger()

class LLMClient:
    """Client for oobabooga-llm service integration"""
    
    def __init__(self, base_url: str = "http://oobabooga-llm:8300", prompts_dir: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=60.0)  # Longer timeout for LLM
        self.logger = logger.bind(service="llm_client")
        self.prompts_dir = Path(prompts_dir or os.getenv("PROMPTS_DIR", "config/prompts"))
        self._prompt_cache: Dict[str, str] = {}

        # Lazy-load: populate cache on first access; keep legacy fallbacks
        self._legacy_prompts = {
            "code": self._load_code_enhancement_prompt(),
            "comment": self._load_comment_enhancement_prompt(),
            "documentation": self._load_documentation_prompt(),
        }
    
    def _load_code_enhancement_prompt(self) -> str:
        """Load code enhancement prompt template"""
        return """You are an expert programming assistant helping to clean up speech-to-text output for code development.

Your task is to fix common speech recognition errors in programming contexts:
- Fix technical terms (e.g., "jay son" → "JSON", "react" → "React")
- Correct programming keywords (e.g., "function" not "funk shun")
- Fix variable names and method calls
- Preserve the developer's intent while making the text technically accurate

Context from repository: {context}

Original speech-to-text: {text}

Provide only the corrected text without explanations:"""
    
    def _load_comment_enhancement_prompt(self) -> str:
        """Load comment enhancement prompt template"""
        return """You are helping to improve speech-to-text output for code comments.

Fix common errors while maintaining natural language flow:
- Correct technical terminology
- Fix grammar and punctuation
- Maintain the conversational tone appropriate for comments
- Use context from the codebase when relevant

Context from repository: {context}

Original speech-to-text: {text}

Provide only the improved comment text:"""
    
    def _load_documentation_prompt(self) -> str:
        """Load documentation enhancement prompt"""
        return """You are helping to create technical documentation from speech-to-text.

Enhance the text for documentation purposes:
- Fix technical terms and concepts
- Improve clarity and structure
- Add appropriate formatting hints
- Maintain professional tone

Context from repository: {context}

Original speech-to-text: {text}

Provide the enhanced documentation text:"""
    
    async def health_check(self) -> bool:
        """Check if oobabooga-llm service is healthy"""
        try:
            # Try to get model info as health check
            response = await self.client.get(f"{self.base_url}/v1/models")
            is_healthy = response.status_code == 200
            
            self.logger.info("LLM service health check",
                           healthy=is_healthy,
                           status_code=response.status_code)
            
            return is_healthy
            
        except Exception as e:
            self.logger.error("LLM service health check failed", error=str(e))
            return False
    
    async def enhance_text(self, request: EnhancementRequest) -> EnhancementResponse:
        """Enhance text using oobabooga-llm service"""
        
        start_time = datetime.now()
        
        self.logger.info("Starting text enhancement",
                        correlation_id=request.correlation_id,
                        intent=request.intent,
                        text_length=len(request.text),
                        has_context=bool(request.context))
        
        try:
            # Select appropriate prompt based on intent
            prompt_template = self._load_prompt(request.intent)
            
            # Build prompt with context
            prompt = prompt_template.format(
                context=request.context or "No repository context available",
                text=request.text
            )
            
            # Prepare payload for oobabooga API
            payload = {
                "prompt": prompt,
                "max_new_tokens": 500,
                "temperature": 0.1,  # Low temperature for consistent corrections
                "top_p": 0.9,
                "repetition_penalty": 1.1,
                "stop": ["\n\n", "Original:", "Context:"]
            }
            
            # Make request to oobabooga-llm service
            response = await self.client.post(
                f"{self.base_url}/v1/completions",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            result_data = response.json()
            
            # Extract enhanced text
            choices = result_data.get("choices", [])
            if choices:
                enhanced_text = choices[0].get("text", "").strip()
            else:
                enhanced_text = request.text  # Fallback to original
            
            # Clean up the response
            enhanced_text = self._clean_response(enhanced_text, request.text)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            response_obj = EnhancementResponse(
                enhanced_text=enhanced_text,
                original_text=request.text,
                processing_time=processing_time,
                correlation_id=request.correlation_id,
                confidence=0.9 if enhanced_text != request.text else 0.5
            )
            
            self.logger.info("Text enhancement completed",
                           correlation_id=request.correlation_id,
                           processing_time=processing_time,
                           original_length=len(request.text),
                           enhanced_length=len(enhanced_text),
                           changed=enhanced_text != request.text)
            
            return response_obj
            
        except httpx.HTTPStatusError as e:
            self.logger.error("LLM service HTTP error",
                            correlation_id=request.correlation_id,
                            status_code=e.response.status_code,
                            response_text=e.response.text)
            
            # Return original text on error
            return EnhancementResponse(
                enhanced_text=request.text,
                original_text=request.text,
                processing_time=0.0,
                correlation_id=request.correlation_id,
                confidence=0.0
            )
            
        except Exception as e:
            self.logger.error("Text enhancement failed",
                            correlation_id=request.correlation_id,
                            error=str(e),
                            exc_info=True)
            
            # Return original text on error
            return EnhancementResponse(
                enhanced_text=request.text,
                original_text=request.text,
                processing_time=0.0,
                correlation_id=request.correlation_id,
                confidence=0.0
            )
    
    async def enhance_text_stream(self, request: EnhancementRequest) -> EnhancementResponse:
        """Enhance text for streaming (faster processing)"""
        
        # For streaming, use a simpler prompt to reduce processing time
        simple_prompt = f"Fix speech-to-text errors in this programming context: {request.text}\n\nCorrected:"
        
        payload = {
            "prompt": simple_prompt,
            "max_new_tokens": 200,
            "temperature": 0.05,  # Even lower temperature for speed
            "top_p": 0.8,
            "stop": ["\n", "Original:", "Fix:"]
        }
        
        start_time = datetime.now()
        
        try:
            response = await self.client.post(
                f"{self.base_url}/v1/completions",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            result_data = response.json()
            
            choices = result_data.get("choices", [])
            enhanced_text = choices[0].get("text", "").strip() if choices else request.text
            enhanced_text = self._clean_response(enhanced_text, request.text)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            return EnhancementResponse(
                enhanced_text=enhanced_text,
                original_text=request.text,
                processing_time=processing_time,
                correlation_id=request.correlation_id,
                confidence=0.8 if enhanced_text != request.text else 0.5
            )
            
        except Exception as e:
            self.logger.error("Streaming text enhancement failed",
                            correlation_id=request.correlation_id,
                            error=str(e))
            
            return EnhancementResponse(
                enhanced_text=request.text,
                original_text=request.text,
                processing_time=0.0,
                correlation_id=request.correlation_id,
                confidence=0.0
            )
    
    def _clean_response(self, enhanced_text: str, original_text: str) -> str:
        """Clean up LLM response"""
        
        # Remove common LLM artifacts
        enhanced_text = enhanced_text.strip()
        
        # Remove prompt echoes
        if enhanced_text.startswith(original_text):
            enhanced_text = enhanced_text[len(original_text):].strip()
        
        # Remove common prefixes
        prefixes_to_remove = [
            "Corrected:",
            "Fixed:",
            "Enhanced:",
            "Improved:",
            "Here's the corrected text:",
            "The corrected version is:"
        ]
        
        for prefix in prefixes_to_remove:
            if enhanced_text.startswith(prefix):
                enhanced_text = enhanced_text[len(prefix):].strip()
        
        # If result is empty or too different, return original
        if not enhanced_text or len(enhanced_text) > len(original_text) * 3:
            return original_text
        
        return enhanced_text
    
    async def get_model_info(self) -> Dict[str, Any]:
        """Get information about loaded LLM model"""
        try:
            response = await self.client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            self.logger.error("Failed to get LLM model info", error=str(e))
            return {"model": "unknown", "capabilities": []}
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
        self.logger.info("LLM client closed")

    # --- New prompt loading logic ---
    def _load_prompt(self, intent: Optional[str]) -> str:
        key = intent or "code"
        if key in self._prompt_cache:
            return self._prompt_cache[key]
        filename_map = {
            "code": "code_enhancement.txt",
            "comment": "comment_enhancement.txt",
            "documentation": "documentation.txt",
        }
        filename = filename_map.get(key, filename_map["code"])
        path = self.prompts_dir / filename
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
            else:
                content = self._legacy_prompts.get(key, self._legacy_prompts["code"])  # fallback
        except Exception:
            content = self._legacy_prompts.get(key, "")
        self._prompt_cache[key] = content
        return content