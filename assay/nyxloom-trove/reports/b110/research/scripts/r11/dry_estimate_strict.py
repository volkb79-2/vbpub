#!/usr/bin/env python3
"""R11: dry_estimate.py with T1 normalization (identifiers KEPT, only
string constants/annotations/docstrings abstracted) -- a lower bound that
removes T2's false matches such as `a is None and b` across unrelated code."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import dupes
dupes.Normalizer._rn = lambda self, name: name
import dry_estimate
dry_estimate.main()
