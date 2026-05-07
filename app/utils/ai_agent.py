"""
Shared AI utilities for the FamSilo AI Agent Suite.
All three agents (Facilitator, Concierge, Daily Briefing) use this module.
"""
import os
from typing import AsyncGenerator
from google import genai
from google.genai import types

_API_KEY = os.getenv("GEMINI_API_KEY", "")
_client = genai.Client(api_key=_API_KEY) if _API_KEY else None

FLASH_MODEL = "gemini-2.5-flash"
EMBED_MODEL = "gemini-embedding-exp-03-07"  # 3072-dim, we'll truncate to 768


# ── Text Generation ──────────────────────────────────────────────────────────

def generate_text(prompt: str, system_instruction: str = "") -> str:
    """Generate a single text completion (non-streaming)."""
    if not _client:
        return "AI service unavailable — no API key configured."
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.8,
            max_output_tokens=1024,
        )
        response = _client.models.generate_content(
            model=FLASH_MODEL,
            contents=prompt,
            config=config,
        )
        return response.text.strip()
    except Exception as e:
        print(f"[AI Agent] generate_text error: {e}")
        return "I'm having trouble generating content right now. Please try again."


# ── Streaming Text ───────────────────────────────────────────────────────────

async def stream_text(prompt: str, system_instruction: str = "") -> AsyncGenerator[str, None]:
    """
    Async generator that yields text chunks from Gemini.
    Use with FastAPI StreamingResponse for SSE.
    """
    if not _client:
        yield "AI service unavailable."
        return
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            max_output_tokens=2048,
        )
        # Use the synchronous streaming API and yield each chunk
        for chunk in _client.models.generate_content_stream(
            model=FLASH_MODEL,
            contents=prompt,
            config=config,
        ):
            if chunk.text:
                yield chunk.text
    except Exception as e:
        print(f"[AI Agent] stream_text error: {e}")
        yield "\n\n[Error generating response]"


# ── Embeddings ───────────────────────────────────────────────────────────────

def generate_embedding(text: str) -> list[float]:
    """
    Generate a text embedding vector for RAG.
    Uses text-embedding-004 (768-dim, stable).
    """
    if not _client:
        return []
    try:
        response = _client.models.embed_content(
            model="text-embedding-004",
            contents=text,
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"[AI Agent] generate_embedding error: {e}")
        return []
