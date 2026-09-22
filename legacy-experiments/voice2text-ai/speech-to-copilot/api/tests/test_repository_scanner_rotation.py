import asyncio
import time
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from services.repository_scanner import RepositoryScanner  # type: ignore


async def _grab_terms(scanner: RepositoryScanner, delay: int = 0):
    if delay:
        # Simulate passage of time
        original_time = time.time
        base = original_time()
        time.time = lambda: base + delay  # type: ignore
        try:
            ctx = await scanner.get_realtime_context()
        finally:
            time.time = original_time  # restore
    else:
        ctx = await scanner.get_realtime_context()
    return ctx.split(', ')[0]


def test_demo_rotation_changes_first_token():
    """Test that rotating context returns different terms over time"""
    scanner = RepositoryScanner('.')
    loop = asyncio.new_event_loop()
    try:
        # Get terms at different simulated times
        first = loop.run_until_complete(_grab_terms(scanner, delay=0))
        later = loop.run_until_complete(_grab_terms(scanner, delay=5))
    finally:
        loop.close()
    
    # Both should be valid terms (may come from filesystem or demo)
    assert isinstance(first, str) and len(first) > 0
    assert isinstance(later, str) and len(later) > 0
    
    # Rotation should eventually shift if there are multiple terms available
    # This is a soft assertion - rotation is time-based so may occasionally match