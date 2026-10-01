"""Vocabulary Corrector

Post-processes transcription output to fix common speech recognition errors
with technical terminology and detect self-corrections.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher

try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class VocabularyCorrector:
    """Corrects common speech-to-text errors in technical vocabulary"""
    
    def __init__(self, custom_vocabulary: Optional[List[str]] = None):
        """Initialize vocabulary corrector
        
        Args:
            custom_vocabulary: Optional list of technical terms to use for correction
        """
        self.custom_vocabulary = custom_vocabulary or []
        
        # Common speech-to-text misrecognitions for technical terms
        self.common_corrections = {
            # Programming languages
            "jay son": "JSON",
            "jason": "JSON",
            "pie thon": "Python",
            "tie script": "TypeScript",
            "java script": "JavaScript",
            "node jay ess": "Node.js",
            "react jay ess": "React.js",
            
            # Common terms
            "A P I": "API",
            "fast A P I": "FastAPI",
            "fast API": "FastAPI",
            "A S R": "ASR",
            "L L M": "LLM",
            "whisper": "Whisper",
            "web socket": "WebSocket",
            "git hub": "GitHub",
            "docker": "Docker",
            "kubernetes": "Kubernetes",
            
            # Database terms
            "S Q L": "SQL",
            "no S Q L": "NoSQL",
            "post gres": "Postgres",
            "my S Q L": "MySQL",
            "mongo D B": "MongoDB",
            "redis": "Redis",
            
            # Common methods/functions
            "a sync": "async",
            "await": "await",
            "funk shun": "function",
            "method": "method",
            "class": "class",
            
            # Cloud/Infrastructure
            "A W S": "AWS",
            "azure": "Azure",
            "G C P": "GCP",
            "lambda": "Lambda",
            
            # Common coding patterns
            "get request": "GET request",
            "post request": "POST request",
            "put request": "PUT request",
            "delete request": "DELETE request",
        }
        
        # Patterns for self-correction detection
        self.self_correction_patterns = [
            # "no I mean X" or "no X"
            r'\b(?:no|wait|actually|I mean|sorry)\s+(.+)',
            # "not X but Y" or "X, no Y"
            r'\b(.+?),?\s+(?:no|wait|actually)\s+(.+)',
            # "correction: X"
            r'\bcorrection[:\s]+(.+)',
            # "let me rephrase: X"
            r'\blet me (?:rephrase|say that again)[:\s]+(.+)',
        ]
        
        self.self_correction_regex = [re.compile(pattern, re.IGNORECASE) 
                                      for pattern in self.self_correction_patterns]
    
    def correct_vocabulary(
        self,
        text: str,
        context_terms: Optional[List[str]] = None
    ) -> Tuple[str, Dict[str, any]]:
        """Apply vocabulary corrections to transcribed text
        
        Args:
            text: Original transcribed text
            context_terms: Additional technical terms from repository context
            
        Returns:
            Tuple of (corrected_text, correction_metadata)
        """
        if not text:
            return text, {"corrections_made": 0, "corrections": []}
        
        corrected = text
        corrections_made = []
        
        # Apply common corrections
        for mistake, correction in self.common_corrections.items():
            # Case-insensitive replacement
            pattern = re.compile(re.escape(mistake), re.IGNORECASE)
            matches = pattern.findall(corrected)
            
            if matches:
                corrected = pattern.sub(correction, corrected)
                corrections_made.append({
                    "original": mistake,
                    "corrected": correction,
                    "count": len(matches)
                })
        
        # Apply context-based corrections
        if context_terms:
            corrected, context_corrections = self._apply_context_corrections(
                corrected, context_terms
            )
            corrections_made.extend(context_corrections)
        
        metadata = {
            "corrections_made": len(corrections_made),
            "corrections": corrections_made,
            "original_length": len(text),
            "corrected_length": len(corrected)
        }
        
        logger.debug("Vocabulary correction applied",
                    corrections=len(corrections_made),
                    original_len=len(text),
                    corrected_len=len(corrected))
        
        return corrected, metadata
    
    def _apply_context_corrections(
        self,
        text: str,
        context_terms: List[str]
    ) -> Tuple[str, List[Dict[str, any]]]:
        """Apply context-aware corrections based on repository terms
        
        Args:
            text: Text to correct
            context_terms: Technical terms from repository context
            
        Returns:
            Tuple of (corrected_text, list_of_corrections)
        """
        corrected = text
        corrections = []
        
        # Split text into words
        words = text.split()
        
        for term in context_terms:
            # Skip very short terms
            if len(term) < 4:
                continue
            
            # Look for similar words in the text
            for i, word in enumerate(words):
                # Clean word of punctuation for comparison
                clean_word = re.sub(r'[^\w]', '', word.lower())
                clean_term = term.lower()
                
                # Check similarity
                similarity = SequenceMatcher(None, clean_word, clean_term).ratio()
                
                # If similar enough (but not exact), consider it a correction candidate
                if 0.7 <= similarity < 1.0 and len(clean_word) >= 4:
                    # Replace the word while preserving surrounding punctuation
                    pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
                    if pattern.search(corrected):
                        corrected = pattern.sub(term, corrected, count=1)
                        corrections.append({
                            "original": word,
                            "corrected": term,
                            "similarity": similarity,
                            "source": "context"
                        })
        
        return corrected, corrections
    
    def detect_self_corrections(self, text: str) -> Tuple[bool, Optional[Dict[str, any]]]:
        """Detect if the user is self-correcting in their speech
        
        Args:
            text: Transcribed text to analyze
            
        Returns:
            Tuple of (has_self_correction, correction_info)
        """
        if not text:
            return False, None
        
        for pattern_regex in self.self_correction_regex:
            match = pattern_regex.search(text)
            if match:
                # Extract the corrected portion
                corrected_part = match.group(1) if match.lastindex >= 1 else text
                
                correction_info = {
                    "detected": True,
                    "pattern": pattern_regex.pattern,
                    "corrected_text": corrected_part.strip(),
                    "full_match": match.group(0),
                    "confidence": 0.8  # High confidence on pattern match
                }
                
                logger.info("Self-correction detected",
                           pattern=pattern_regex.pattern,
                           corrected_text=corrected_part[:50])
                
                return True, correction_info
        
        # Check for discourse markers indicating correction
        correction_markers = [
            "no", "wait", "actually", "I mean", "sorry",
            "correction", "rather", "instead"
        ]
        
        # Count correction markers
        text_lower = text.lower()
        marker_count = sum(1 for marker in correction_markers if marker in text_lower)
        
        if marker_count >= 2:
            # Multiple correction markers suggest self-correction
            return True, {
                "detected": True,
                "pattern": "multiple_markers",
                "marker_count": marker_count,
                "confidence": min(0.5 + (marker_count * 0.1), 0.9)
            }
        
        return False, None
    
    def extract_corrected_intent(
        self,
        text: str,
        has_self_correction: bool = False,
        correction_info: Optional[Dict[str, any]] = None
    ) -> str:
        """Extract the final intended text after self-corrections
        
        Args:
            text: Original text
            has_self_correction: Whether self-correction was detected
            correction_info: Information about the correction
            
        Returns:
            The corrected/intended text
        """
        if not has_self_correction or not correction_info:
            return text
        
        # If we detected a self-correction with extracted text
        if "corrected_text" in correction_info:
            return correction_info["corrected_text"]
        
        # For multiple markers, try to extract the latter part
        # (users typically correct by saying the new version after markers)
        for marker in ["actually", "I mean", "correction", "rather", "no wait"]:
            if marker in text.lower():
                parts = text.lower().split(marker, 1)
                if len(parts) == 2:
                    # Return the part after the correction marker
                    return parts[1].strip()
        
        return text
    
    def update_vocabulary(self, new_terms: List[str]):
        """Update custom vocabulary with new technical terms
        
        Args:
            new_terms: List of new technical terms to add
        """
        # Add unique terms
        for term in new_terms:
            if term and term not in self.custom_vocabulary:
                self.custom_vocabulary.append(term)
        
        logger.info("Vocabulary updated",
                   total_terms=len(self.custom_vocabulary),
                   new_terms=len(new_terms))


__all__ = ["VocabularyCorrector"]
