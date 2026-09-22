"""Minimal WebSocket demo client for DEMO_MODE.

Sends a series of fake audio_chunk messages and prints incremental transcription
responses from the server. Requires the FastAPI service running locally:

    poetry run uvicorn main:app --reload --port 8000

Run this script via:

    poetry run python scripts/ws_demo.py
"""
import asyncio
import json
import uuid
import base64

import websockets
import os

WS_URL = os.getenv("WS_URL", "ws://localhost:8000/ws/audio")

# Pre-encoded small dummy payload ("dummy")
DUMMY_B64 = base64.b64encode(b"dummy").decode()

async def run_demo():
    async with websockets.connect(WS_URL) as ws:
        hello = await ws.recv()
        print("[handshake]", hello)

        for i in range(1, 6):
            frame = {
                "type": "audio_chunk",
                "correlation_id": str(uuid.uuid4()),
                "data": {
                    "audio_data": DUMMY_B64,
                    "format": "wav",
                    "sample_rate": 16000,
                    "channels": 1,
                },
            }
            await ws.send(json.dumps(frame))
            # Loop until we get transcription_result (ignore pings)
            while True:
                msg = await ws.recv()
                try:
                    payload = json.loads(msg)
                except Exception:
                    print("[non-json]", msg)
                    break
                if payload.get("type") == "transcription_result":
                    data = payload.get("data", {})
                    ctx = data.get("context", "")
                    if ctx:
                        ctx_preview = ctx.split()[:4]
                        ctx_disp = " ".join(ctx_preview)
                    else:
                        ctx_disp = "-"
                    print(f"[update {i}] inc='{data.get('incremental')}' len={len(data.get('text',''))} ctx={ctx_disp}")
                    break
            await asyncio.sleep(0.5)

        await ws.send(json.dumps({"type": "ping"}))
        # Read a few frames then exit
        for _ in range(2):
            try:
                pong = await asyncio.wait_for(ws.recv(), timeout=1.0)
                print("[tail]", pong)
            except Exception:
                break

if __name__ == "__main__":
    asyncio.run(run_demo())
