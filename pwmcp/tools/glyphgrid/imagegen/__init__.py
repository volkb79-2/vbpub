"""imagegen: deterministic test-image generator with exact ground truth for OCR measurement.

Font + text screen + transform chain -> PNG + sidecar JSON truth.  See IMAGEGEN-FINDINGS.md.
Uses glyphgrid/fontfiles through their public functions only.
"""
import os
import sys

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:  # glyphgrid.py / fontfiles.py live one level up and import each other by bare name
    sys.path.insert(0, _PARENT)

FORMAT = "imagegen-truth/1"
