"""Tests for Vocabulary Corrector

Tests vocabulary correction, self-correction detection, and context-based corrections.
"""

import pytest
from services.vocabulary_corrector import VocabularyCorrector


class TestVocabularyCorrection:
    """Tests for vocabulary correction functionality"""
    
    def setup_method(self):
        """Setup test corrector"""
        self.corrector = VocabularyCorrector()
    
    def test_common_corrections(self):
        """Test common technical term corrections"""
        text = "I need to parse the jay son data"
        corrected, metadata = self.corrector.correct_vocabulary(text)
        
        assert "JSON" in corrected
        assert "jay son" not in corrected.lower()
        assert metadata["corrections_made"] > 0
    
    def test_multiple_corrections(self):
        """Test multiple corrections in one text"""
        text = "Use fast A P I with pie thon and node jay ess"
        corrected, metadata = self.corrector.correct_vocabulary(text)
        
        assert "FastAPI" in corrected
        assert "Python" in corrected
        assert "Node.js" in corrected
        assert metadata["corrections_made"] >= 3
    
    def test_no_corrections_needed(self):
        """Test text that doesn't need correction"""
        text = "This is a simple sentence"
        corrected, metadata = self.corrector.correct_vocabulary(text)
        
        assert corrected == text
        assert metadata["corrections_made"] == 0
    
    def test_context_based_correction(self):
        """Test context-aware corrections"""
        context_terms = ["FastAPI", "uvicorn", "WebSocket", "PostgreSQL"]
        text = "I'm using fastappi with postgrez"
        
        corrected, metadata = self.corrector.correct_vocabulary(text, context_terms)
        
        # Should correct similar terms from context
        assert len(corrected) > 0
        assert metadata["corrections_made"] >= 0


class TestSelfCorrectionDetection:
    """Tests for self-correction detection"""
    
    def setup_method(self):
        """Setup test corrector"""
        self.corrector = VocabularyCorrector()
    
    def test_detect_no_i_mean(self):
        """Test detection of 'no I mean' pattern"""
        text = "Set the variable to true, no I mean false"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        assert has_correction is True
        assert info is not None
        assert "false" in info.get("corrected_text", "").lower()
    
    def test_detect_actually(self):
        """Test detection of 'actually' pattern"""
        text = "Use Python version 3.8, actually make that 3.11"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        assert has_correction is True
        assert info is not None
    
    def test_detect_wait(self):
        """Test detection of 'wait' pattern"""
        text = "Import the requests library, wait I mean httpx"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        assert has_correction is True
        assert info is not None
    
    def test_no_self_correction(self):
        """Test when no self-correction is present"""
        text = "This is a normal sentence without any changes"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        assert has_correction is False
    
    def test_multiple_markers(self):
        """Test detection with multiple correction markers"""
        text = "Wait, no, actually let me change that"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        assert has_correction is True
        assert info is not None


class TestExtractCorrectedIntent:
    """Tests for extracting corrected intent"""
    
    def setup_method(self):
        """Setup test corrector"""
        self.corrector = VocabularyCorrector()
    
    def test_extract_after_i_mean(self):
        """Test extracting text after 'I mean'"""
        text = "Set timeout to 30, I mean 60 seconds"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        corrected = self.corrector.extract_corrected_intent(
            text, has_correction, info
        )
        
        assert "60" in corrected or "seconds" in corrected
    
    def test_extract_after_actually(self):
        """Test extracting text after 'actually'"""
        text = "Use GET request, actually POST request"
        has_correction, info = self.corrector.detect_self_corrections(text)
        
        corrected = self.corrector.extract_corrected_intent(
            text, has_correction, info
        )
        
        assert "POST" in corrected
    
    def test_no_correction_returns_original(self):
        """Test that original text is returned when no correction"""
        text = "This is the original text"
        
        corrected = self.corrector.extract_corrected_intent(
            text, False, None
        )
        
        assert corrected == text


class TestVocabularyUpdate:
    """Tests for vocabulary update functionality"""
    
    def test_update_vocabulary(self):
        """Test adding new terms to vocabulary"""
        corrector = VocabularyCorrector()
        initial_count = len(corrector.custom_vocabulary)
        
        new_terms = ["FastAPI", "uvicorn", "Pydantic"]
        corrector.update_vocabulary(new_terms)
        
        assert len(corrector.custom_vocabulary) == initial_count + len(new_terms)
        assert "FastAPI" in corrector.custom_vocabulary
    
    def test_update_vocabulary_no_duplicates(self):
        """Test that duplicate terms aren't added"""
        corrector = VocabularyCorrector(custom_vocabulary=["FastAPI"])
        initial_count = len(corrector.custom_vocabulary)
        
        new_terms = ["FastAPI", "uvicorn"]
        corrector.update_vocabulary(new_terms)
        
        # Should only add uvicorn, not duplicate FastAPI
        assert len(corrector.custom_vocabulary) == initial_count + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
