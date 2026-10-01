#!/usr/bin/env python3
"""
Simple test to verify we can use llama-cpp-python directly without oobabooga.
This tests direct model loading and inference.
"""

import os
import sys

def test_direct_llama():
    """Test direct llama-cpp-python usage."""
    try:
        from llama_cpp import Llama
        
        # Model path (adjust as needed)
        model_path = "/data/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
        
        print(f"Loading model from: {model_path}")
        print("This may take a minute...")
        
        # Load model
        llm = Llama(
            model_path=model_path,
            n_ctx=2048,
            n_threads=6,
            n_gpu_layers=0,  # CPU only
            verbose=False
        )
        
        print("✅ Model loaded successfully!")
        
        # Test generation
        prompt = "Hello, this is a test. Please respond."
        print(f"\nTest prompt: {prompt}")
        
        output = llm(
            prompt,
            max_tokens=50,
            temperature=0.7,
            stop=["</s>", "\n\n"]
        )
        
        response = output['choices'][0]['text'].strip()
        print(f"✅ Model response: {response}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_direct_llama()
    sys.exit(0 if success else 1)
