#!/usr/bin/env python3
from pathlib import Path
import runpy
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module("debian_install_v2.bootstrap", run_name="__main__")
