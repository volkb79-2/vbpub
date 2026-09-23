import os
import time
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

TARGET_URL = os.getenv("TARGET_URL", "http://llm-webui:8000")
MODEL_ALIAS = os.getenv("SHIM_MODEL_ALIAS", "postproc-mini")
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "/config/system-prompt.txt")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "256"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

system_prompt = ""
if os.path.exists(SYSTEM_PROMPT_FILE):
    with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
        system_prompt = f.read().strip()

app = FastAPI(title="OpenAI-Compatible Shim", version="0.1.0")

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Message]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None

class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[Choice]
    usage: Usage

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/v1/chat/completions", response_model=ChatResponse)
async def completions(req: ChatRequest):
    prompt_parts = []
    if system_prompt:
        prompt_parts.append(system_prompt + "\n\n")
    for m in req.messages:
        if m.role == "user":
            prompt_parts.append(m.content.strip() + "\n")
        elif m.role == "system":
            prompt_parts.append(m.content.strip() + "\n")
        elif m.role == "assistant":
            # include assistant prior replies to preserve context
            prompt_parts.append(m.content.strip() + "\n")
    prompt = "".join(prompt_parts).strip()

    gen_tokens = min(req.max_tokens or MAX_TOKENS, MAX_TOKENS)

    payload = {
        "prompt": prompt,
        "max_new_tokens": gen_tokens,
        "temperature": req.temperature if req.temperature is not None else 0.7,
        "top_p": req.top_p if req.top_p is not None else 0.95,
        "do_sample": True,
        "typical_p": 1.0,
        "repetition_penalty": 1.1,
        "encoder_repetition_penalty": 1.0,
        "top_k": 40,
        "min_length": 0,
        "no_repeat_ngram_size": 0,
        "num_beams": 1,
        "penalty_alpha": 0,
        "length_penalty": 1,
        "early_stopping": False,
        "seed": -1,
        "add_bos_token": True,
        "truncation_length": 2048,
        "ban_eos_token": False,
        "skip_special_tokens": True,
        "stopping_strings": []
    }

    started = time.time()
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(f"{TARGET_URL}/v1/completions", json=payload)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Backend error: {e}")

    data = r.json()
    text = data.get("results", [{}])[0].get("text", "")
    elapsed = time.time() - started

    # naive token counts (can be replaced by a tokenizer if needed)
    prompt_tokens = len(prompt.split())
    completion_tokens = len(text.split())

    resp = ChatResponse(
        id=f"chatcmpl-shim-{int(started)}",
        object="chat.completion",
        created=int(started),
        model=req.model or MODEL_ALIAS,
        choices=[Choice(index=0, message=Message(role="assistant", content=text.strip()), finish_reason="stop")],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
    if LOG_LEVEL == "debug":
        print(f"[shim] latency={elapsed:.2f}s tokens={completion_tokens}")
    return resp
