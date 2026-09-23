#!/usr/bin/env python3
import runpy
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

# In a vbpub source checkout the shared CLI helper is a sibling project
# library. Remote bootstrap installs that same package beside this script, so
# this source-tree path is only used for direct local execution.
repo_cli_library = script_dir.parents[1] / "libraries" / "cli-extended" / "src"
if repo_cli_library.is_dir():
    sys.path.insert(0, str(repo_cli_library))

runpy.run_module("debian_install_v2.bootstrap", run_name="__main__")
