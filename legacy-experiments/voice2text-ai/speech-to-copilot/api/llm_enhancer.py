"""
Enhanced LLM Post-Processing for Transcribed Audio
Improves code transcriptions, fixes common speech recognition errors,
and applies context-aware enhancements
"""

from typing import Dict, List, Optional, Any
import re
import logging

logger = logging.getLogger(__name__)


class LLMEnhancer:
    """Enhanced post-processing for transcribed audio"""
    
    # Common speech-to-text errors in code context
    CODE_CORRECTIONS = {
        'pint': 'print',
        'function define': 'def',
        'funk shun': 'function',
        'import numpy': 'import numpy',
        'import pandas': 'import pandas',
        'for loop': 'for',
        'while loop': 'while',
        'if statement': 'if',
        'else if': 'elif',
        'return value': 'return',
        'class definition': 'class',
        'async await': 'async/await',
        'try catch': 'try/except',
        'dictionary': 'dict',
        'list comprehension': 'list comp',
    }
    
    def __init__(self, llm_client):
        """Initialize with LLM client"""
        self.llm_client = llm_client
    
    def enhance_code_transcription(
        self,
        text: str,
        context: Optional[List[str]] = None,
        language: str = "python"
    ) -> Dict[str, Any]:
        """
        Enhance code transcription with LLM post-processing
        
        Args:
            text: Raw transcribed text
            context: Optional context terms from repository
            language: Programming language (default: python)
        
        Returns:
            Dict with enhanced_text, corrections, confidence
        """
        
        # Step 1: Apply quick corrections
        corrected_text = self._apply_quick_corrections(text)
        
        # Step 2: Build context-aware prompt
        prompt = self._build_enhancement_prompt(
            corrected_text, 
            context, 
            language
        )
        
        # Step 3: Get LLM enhancement
        try:
            enhanced = self._call_llm(prompt)
            
            return {
                'enhanced_text': enhanced,
                'original_text': text,
                'quick_corrections': corrected_text != text,
                'llm_enhanced': True,
                'confidence': self._calculate_confidence(text, enhanced)
            }
            
        except Exception as e:
            logger.error(f"LLM enhancement failed: {e}")
            return {
                'enhanced_text': corrected_text,
                'original_text': text,
                'quick_corrections': corrected_text != text,
                'llm_enhanced': False,
                'error': str(e)
            }
    
    def _apply_quick_corrections(self, text: str) -> str:
        """Apply quick pattern-based corrections"""
        corrected = text
        
        # Apply code corrections
        for wrong, correct in self.CODE_CORRECTIONS.items():
            pattern = re.compile(re.escape(wrong), re.IGNORECASE)
            corrected = pattern.sub(correct, corrected)
        
        # Fix common spacing issues
        corrected = re.sub(r'\s+', ' ', corrected)  # Multiple spaces
        corrected = re.sub(r'\s+([.,;:])', r'\1', corrected)  # Space before punctuation
        
        return corrected.strip()
    
    def _build_enhancement_prompt(
        self,
        text: str,
        context: Optional[List[str]],
        language: str
    ) -> str:
        """Build context-aware enhancement prompt"""
        
        base_prompt = f"""You are a code transcription assistant. Fix and improve this voice-transcribed {language} code.

TASK: Transform the speech-to-text output into valid, clean {language} code.

RULES:
1. Fix obvious speech recognition errors (e.g., "pint" → "print")
2. Use proper {language} syntax and conventions
3. Keep the original intent - don't add new functionality
4. Format code properly with correct indentation
5. Fix typos and spacing issues
6. Preserve variable/function names mentioned in speech

"""
        
        # Add context if available
        if context and len(context) > 0:
            context_str = ', '.join(context[:10])
            base_prompt += f"""CONTEXT: This code is from a project using: {context_str}
Use these terms when appropriate.

"""
        
        base_prompt += f"""TRANSCRIBED TEXT:
{text}

CORRECTED {language.upper()} CODE:"""
        
        return base_prompt
    
    def _call_llm(self, prompt: str, max_tokens: int = 200) -> str:
        """Call LLM for enhancement"""
        
        # Format as chat message
        messages = [
            {
                "role": "system",
                "content": "You are an expert programmer who fixes voice-transcribed code. "
                          "Respond only with the corrected code, no explanations."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        # Call LLM
        response = self.llm_client.chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3  # Low temperature for deterministic corrections
        )
        
        # Extract text from response
        if isinstance(response, dict) and 'choices' in response:
            return response['choices'][0]['message']['content'].strip()
        else:
            return str(response).strip()
    
    def _calculate_confidence(self, original: str, enhanced: str) -> float:
        """Calculate confidence score for enhancement"""
        
        # Simple heuristic based on amount of change
        if original == enhanced:
            return 0.5  # No change, medium confidence
        
        # Calculate edit distance ratio
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, original, enhanced).ratio()
        
        # More changes = lower confidence (unless very different, then high confidence)
        if ratio > 0.8:
            return 0.7  # Small changes
        elif ratio > 0.5:
            return 0.6  # Moderate changes  
        else:
            return 0.8  # Large changes (likely needed corrections)


class EnhancementStrategies:
    """Different enhancement strategies for different contexts"""
    
    @staticmethod
    def code_snippet_strategy(text: str) -> str:
        """Strategy for short code snippets"""
        # Focus on syntax fixing
        pass
    
    @staticmethod
    def command_strategy(text: str) -> str:
        """Strategy for CLI commands"""
        # Focus on command syntax
        pass
    
    @staticmethod
    def comment_strategy(text: str) -> str:
        """Strategy for code comments"""
        # Focus on natural language
        pass


# Example usage
if __name__ == "__main__":
    # Mock LLM client for testing
    class MockLLMClient:
        def chat_completion(self, messages, max_tokens=100, temperature=0.3):
            # Simple mock response
            return {
                'choices': [{
                    'message': {
                        'content': 'print("Hello, World!")'
                    }
                }]
            }
    
    enhancer = LLMEnhancer(MockLLMClient())
    
    # Test enhancement
    result = enhancer.enhance_code_transcription(
        "pint hello world",
        context=["FastAPI", "uvicorn", "async"],
        language="python"
    )
    
    print(f"Original: {result['original_text']}")
    print(f"Enhanced: {result['enhanced_text']}")
    print(f"Confidence: {result.get('confidence', 0):.2f}")
