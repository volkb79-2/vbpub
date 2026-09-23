#!/usr/bin/env python3
"""
LocalAI Model Download Hook
Downloads configured models before starting LocalAI service
"""

import os
import sys
import tomllib
from pathlib import Path
import subprocess
import hashlib


class PreComposeHook:
    """Pre-compose hook for downloading LocalAI models."""
    
    def __init__(self, env: dict = None):
        self.env = env or {}
        self.script_dir = Path(__file__).parent
        
    def run(self, env: dict) -> dict:
        """Download models before compose starts."""
        print("[INFO] LocalAI Model Download Hook")
        
        # Load configuration
        config_file = self.script_dir / "ciu.defaults.toml.j2"
        if not config_file.exists():
            print(f"[WARN] Config file not found: {config_file}")
            return {}
        
        with open(config_file, 'rb') as f:
            config = tomllib.load(f)
        
        # Get models directory
        models_dir = self.script_dir / "vol-localai-models"
        models_dir.mkdir(exist_ok=True, parents=True)
        
        print(f"[INFO] Models directory: {models_dir}")
        
        # Download each configured model
        localai_config = config.get('localai', {})
        models = localai_config.get('models', {})
        
        if not models:
            print("[WARN] No models configured")
            return {}
        
        print(f"[INFO] Found {len(models)} model(s) to download")
        
        for model_key, model_config in models.items():
            model_name = model_config.get('name', model_key)
            model_file = model_config.get('file')
            model_url = model_config.get('url')
            
            if not model_file or not model_url:
                print(f"[WARN] Skipping {model_name}: missing file or url")
                continue
            
            model_path = models_dir / model_file
            
            # Check if model already exists
            if model_path.exists():
                size_mb = model_path.stat().st_size / (1024 * 1024)
                print(f"[INFO] {model_name}: Already downloaded ({size_mb:.1f} MB)")
                continue
            
            print(f"[INFO] {model_name}: Downloading from {model_url}")
            print(f"[INFO]   Expected size: {model_config.get('size_mb', 'unknown')} MB")
            print(f"[INFO]   Description: {model_config.get('description', 'N/A')}")
            
            # Download using curl with progress
            try:
                subprocess.run([
                    'curl', '-L',
                    '-o', str(model_path),
                    '--progress-bar',
                    '--fail',
                    '--create-dirs',
                    model_url
                ], check=True)
                
                downloaded_size = model_path.stat().st_size / (1024 * 1024)
                print(f"[SUCCESS] {model_name}: Downloaded ({downloaded_size:.1f} MB)")
                
            except subprocess.CalledProcessError as e:
                print(f"[ERROR] Failed to download {model_name}: {e}")
                # Don't fail completely, continue with other models
                continue
            except Exception as e:
                print(f"[ERROR] Unexpected error downloading {model_name}: {e}")
                continue
        
        # Create model configuration files for LocalAI
        self._create_model_configs(models_dir, models)
        
        return {}
    
    def _create_model_configs(self, models_dir: Path, models: dict):
        """Create YAML configuration files for each model."""
        print("[INFO] Creating model configuration files...")
        
        for model_key, model_config in models.items():
            model_name = model_config.get('name', model_key)
            model_file = model_config.get('file')
            
            if not model_file:
                continue
            
            # Create YAML config for LocalAI
            config_content = f"""name: {model_name}
backend: llama-cpp
parameters:
  model: {model_file}
  temperature: 0.7
  top_k: 40
  top_p: 0.9
  max_tokens: 2048
context_size: 2048
threads: 6
batch_size: 512
f16: false
gpu_layers: 0
stopwords:
  - "### Human:"
  - "### Assistant:"
template:
  chat: |
    {{{{ .Input }}}}
  completion: |
    {{{{ .Input }}}}
"""
            
            config_file = models_dir / f"{model_name}.yaml"
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            print(f"[INFO] Created config: {config_file.name}")
        
        print("[SUCCESS] Model configurations created")


if __name__ == '__main__':
    # Standalone execution for testing
    hook = PreComposeHook()
    result = hook.run({})
    print(f"[DEBUG] Hook returned: {result}")
