from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


PATCHER_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vanilla_iso_unified_patch", PATCHER_DIR / "unified_patch.py")
assert spec is not None and spec.loader is not None
unified_patch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = unified_patch
spec.loader.exec_module(unified_patch)


def test_applies_exact_hunks_in_memory_without_mutating_source(tmp_path):
    root = tmp_path / "root"
    target = root / "middlewared/plugins/vm/info.py"
    target.parent.mkdir(parents=True)
    original = "from x import y\n\ndef supported():\n    return False\n"
    target.write_text(original, encoding="utf-8")
    patch = """--- a/middlewared/plugins/vm/info.py
+++ b/middlewared/plugins/vm/info.py
@@ -1,4 +1,4 @@
 from x import y
 
 def supported():
-    return False
+    return True
"""

    changed = unified_patch.apply_patch_batch(root, [patch])

    assert changed[Path("middlewared/plugins/vm/info.py")] == original.replace("return False", "return True")
    assert target.read_text(encoding="utf-8") == original


def test_rejects_context_mismatch_without_applying_a_partial_batch(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    good = """--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    bad = """--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-value = 3
+value = 4
"""

    with pytest.raises(unified_patch.UnifiedPatchError, match="exact patch context mismatch"):
        unified_patch.apply_patch_batch(root, [good, bad])
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_rejects_parent_traversal_targets():
    patch = """--- a/../escape.py
+++ b/../escape.py
@@ -1 +1 @@
-before
+after
"""

    with pytest.raises(unified_patch.UnifiedPatchError, match="unsafe patch target path"):
        unified_patch.parse_unified_patch(patch)
