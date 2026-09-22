import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.diff_utils import compute_incremental_suffix


def test_incremental_basic_append():
    suffix, prefix_len = compute_incremental_suffix("hello", "hello world")
    assert suffix == " world"
    assert prefix_len == 5


def test_incremental_no_previous():
    suffix, prefix_len = compute_incremental_suffix("", "abc")
    assert suffix == "abc"
    assert prefix_len == 0


def test_incremental_identical():
    suffix, prefix_len = compute_incremental_suffix("same", "same")
    assert suffix == ""
    assert prefix_len == 4


def test_incremental_divergence_midway():
    # Only common prefix should be kept
    suffix, prefix_len = compute_incremental_suffix("abcdef", "abcXYZ")
    assert suffix == "XYZ"
    assert prefix_len == 3


def test_incremental_empty_current():
    suffix, prefix_len = compute_incremental_suffix("abc", "")
    assert suffix == ""
    assert prefix_len == 0
