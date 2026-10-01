#!/usr/bin/env python3
"""
Self-contained setup script for oobabooga-llm project.
This script handles all dependencies internally and sets up the project.
"""

import os
import sys
import subprocess
import venv
from pathlib import Path

def run_command(cmd, cwd=None, check=True):
    """Run a command and return the result."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error running command: {cmd}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        sys.exit(1)
    return result

def setup_venv():
    """Set up Python virtual environment."""
    venv_path = Path("venv")
    if not venv_path.exists():
        print("Creating Python virtual environment...")
        venv.create(venv_path, with_pip=True)
    
    # Get the Python executable path
    if os.name == 'nt':  # Windows
        python_exe = venv_path / "Scripts" / "python.exe"
        pip_exe = venv_path / "Scripts" / "pip.exe"
    else:  # Unix-like
        python_exe = venv_path / "bin" / "python"
        pip_exe = venv_path / "bin" / "pip"
    
    return str(python_exe), str(pip_exe)

def install_dependencies(pip_exe):
    """Install required Python dependencies."""
    print("Installing PyYAML...")
    run_command(f"{pip_exe} install PyYAML")

def main():
    """Main setup function."""
    print("=== Oobabooga LLM Self-Contained Setup ===")
    
    # Change to the script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Set up virtual environment
    python_exe, pip_exe = setup_venv()
    
    # Install dependencies
    install_dependencies(pip_exe)
    
    # Create .env.active if it doesn't exist
    if not Path(".env.active").exists():
        if Path(".env.sample").exists():
            print("Generating .env.active from .env.sample...")
            run_command(f"{python_exe} ../ciu.py")
        else:
            print("Error: .env.sample not found!")
            sys.exit(1)
    
    # Run init-model.sh to download the model
    print("Running model initialization...")
    run_command("bash init-model.sh")
    
    # Start the containers
    print("Starting Docker containers...")
    run_command(f"{python_exe} ../ciu.py")
    
    print("Setup complete! The oobabooga LLM service should now be running.")
    print("You can test it by running: ./test-llm.sh")

if __name__ == "__main__":
    main()