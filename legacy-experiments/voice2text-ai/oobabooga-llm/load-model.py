#!/usr/bin/env python3
"""
Model loading script for oobabooga text-generation-webui.
This script loads a GGUF model using the llama.cpp loader with proper parameters.
"""

import requests
import json
import time
import os
import sys

def wait_for_api(base_url="http://localhost:5000", timeout=60):
    """Wait for the oobabooga API to be ready."""
    print("Waiting for oobabooga API to be ready...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{base_url}/v1/models", timeout=5)
            if response.status_code == 200:
                print("API is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    
    print(f"API did not become ready within {timeout} seconds")
    return False

def load_model(base_url="http://localhost:5000"):
    """Load the GGUF model using the llama.cpp loader."""
    
    # Get environment variables - use the actual model file name
    model_name = os.getenv('MODEL_NAME', os.getenv('MODEL_HF_FILE', 'tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf'))
    model_threads = int(os.getenv('MODEL_THREADS', '6'))
    model_context_length = int(os.getenv('MODEL_CONTEXT_LENGTH', '1024'))
    
    print(f"Loading model: {model_name}")
    print(f"Threads: {model_threads}, Context length: {model_context_length}")
    
    # First, check if any model is already loaded and unload it
    try:
        print("Checking for existing loaded models...")
        response = requests.post(f"{base_url}/v1/internal/model/unload", timeout=30)
        if response.status_code == 200:
            print("Unloaded any existing model")
        time.sleep(2)
    except:
        pass  # Continue if unload fails
    
    # Use simple llama.cpp loader (not server loader which needs llama_cpp_binaries)
    # Try "llama.cpp-python" or "ExLlamav2" as alternative loaders that don't need the server
    payload = {
        "model_name": model_name,
        "args": {
            "loader": "llama.cpp-python",  # Use the Python bindings directly
            "n_ctx": model_context_length,
            "threads": model_threads,
            "threads_batch": model_threads,
            "n_batch": 16,
            "n_gpu_layers": 0,  # Force CPU only
            "no_mmap": True,    # Load into memory directly
            "mlock": False,
            "cpu": True,        # Force CPU-only llama.cpp
            "no_mul_mat_q": False,
            "numa": False,
            "use_fast": True    # Use fast tokenizer
        }
    }
    
    try:
        print("Sending model load request...")
        response = requests.post(
            f"{base_url}/v1/internal/model/load",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=300  # 5 minute timeout for model loading
        )
        
        if response.status_code == 200:
            result = response.text.strip().strip('"')
            if result == "OK":
                print("✅ Model loaded successfully!")
                return True
            else:
                print(f"❌ Model loading returned: {result}")
                return False
        else:
            print(f"❌ Model loading failed with status {response.status_code}: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Model loading timed out after 5 minutes")
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Model loading failed with error: {e}")
        return False

def verify_model_loaded(base_url="http://localhost:5000"):
    """Verify that the model is properly loaded by testing the API."""
    print("Verifying model is working...")
    
    # Test with a simple chat completion instead
    test_payload = {
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
        "max_tokens": 10,
        "temperature": 0.7
    }
    
    try:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json=test_payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                message = result['choices'][0].get('message', {})
                text = message.get('content', '')
                print(f"✅ Model verification successful! Generated: {text.strip()}")
                return True
            else:
                print("❌ Model verification failed: No choices in response")
                return False
        else:
            print(f"❌ Model verification failed with status {response.status_code}: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Model verification failed with error: {e}")
        return False

def main():
    """Main function to load and verify the model."""
    base_url = os.getenv('OOBABOOGA_API_URL', 'http://localhost:5000')
    
    print("🚀 Starting model loading process...")
    
    # Wait for API to be ready
    if not wait_for_api(base_url):
        sys.exit(1)
    
    # Load the model
    if not load_model(base_url):
        sys.exit(1)
    
    # Wait a bit for model to fully initialize
    print("Waiting for model initialization...")
    time.sleep(10)
    
    # Verify model is working
    if not verify_model_loaded(base_url):
        sys.exit(1)
    
    print("🎉 Model loading completed successfully!")

if __name__ == "__main__":
    main()