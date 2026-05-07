"""
Zero-Trust AI Content Moderation Service
Uses Google Gemini (multimodal) to screen text, images, and videos
before they are committed to the public feed.
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types

# ── Configure Gemini ──────────────────────────────────────────────────────────
_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Initialize the new genai Client
# We only initialize it if the key exists, otherwise we'll fail-open later.
_client = genai.Client(api_key=_API_KEY) if _API_KEY else None

# Use the recommended Flash model
_MODEL = "gemini-2.5-flash"

# ── Result type ───────────────────────────────────────────────────────────────
@dataclass
class ModerationResult:
    safe: bool
    reason: str   # Human-readable explanation (logged, not shown to user)
    flags: list   # e.g. ["pii", "threat", "nsfw"]


# ── Shared prompt scaffold ────────────────────────────────────────────────────
_SYSTEM_INSTRUCTION = """
You are a zero-trust content moderation AI for a private family social network.
Your job is to detect content that should NOT be published. Respond ONLY with
a JSON object in this exact format:

{
  "safe": true | false,
  "reason": "brief explanation",
  "flags": ["list", "of", "issue", "types"]
}

Flag types to use (only if applicable):
  - "pii"        : Personally Identifiable Information (phone, email, SSN, address, DOB)
  - "threat"     : Threats, harassment, or targeted abuse
  - "phishing"   : Social engineering, suspicious links, impersonation
  - "nsfw"       : Nudity, sexual content, graphic violence
  - "hate"       : Hate speech, slurs, extremist content
  - "spam"       : Commercial spam, repetitive junk content
  - "misinformation": Dangerous health/safety misinformation

Community context: This is a private invite-only family group. Trust level is
high but zero-trust sanitization is enforced to prevent accidental PII leaks
and to mitigate the impact of any compromised member accounts.
""".strip()

_CONFIG = types.GenerateContentConfig(
    system_instruction=_SYSTEM_INSTRUCTION,
    temperature=0,
    max_output_tokens=256,
    response_mime_type="application/json"
)

def _call_gemini(contents: list, timeout: int = 15) -> ModerationResult:
    """Shared Gemini call with robust JSON parsing and fallback-safe error handling."""
    if not _client:
        # If no API key is configured, pass everything through (fail-open)
        return ModerationResult(safe=True, reason="Moderation skipped (no API key)", flags=[])

    try:
        response = _client.models.generate_content(
            model=_MODEL,
            contents=contents,
            config=_CONFIG,
        )
        raw = response.text.strip()

        data = json.loads(raw)
        return ModerationResult(
            safe=bool(data.get("safe", True)),
            reason=str(data.get("reason", "")),
            flags=list(data.get("flags", [])),
        )

    except json.JSONDecodeError:
        # Gemini returned something unparseable — fail safe (pass)
        return ModerationResult(safe=True, reason="Moderation parse error — passed", flags=[])
    except Exception as e:
        # Any API error — fail safe (pass) so posts aren't silently blocked
        return ModerationResult(safe=True, reason=f"Moderation error: {str(e)}", flags=[])


# ── Audit Logging ─────────────────────────────────────────────────────────────

def log_moderation_event(
    db,
    post_id: str,
    content_type: str,
    result: "ModerationResult",
    reviewed_by: str = "ai",
) -> None:
    """
    Write a moderation audit record to the `moderation_logs` table.
    This is fire-and-forget — failures are swallowed to prevent any
    interference with the moderation verdict itself.

    Args:
        db: Supabase Client instance.
        post_id: The UUID of the post being moderated.
        content_type: One of 'text', 'image', 'video', 'comment'.
        result: The ModerationResult returned by the moderation function.
        reviewed_by: 'ai' for automated decisions, or a user_id for manual overrides.
    """
    try:
        db.table("moderation_logs").insert({
            "post_id": post_id,
            "content_type": content_type,
            "verdict": "approved" if result.safe else "quarantined",
            "flags": result.flags,
            "reason": result.reason,
            "reviewed_by": reviewed_by,
        }).execute()
    except Exception as e:
        # Non-fatal — audit log failure must never block content flow
        print(f"[MODERATION AUDIT] Failed to write log for post {post_id}: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def moderate_text(text: str) -> ModerationResult:
    """
    Synchronously moderate a plain-text payload.
    Called before any DB write — result is immediate (used for captions & text posts).
    """
    if not text or not text.strip():
        return ModerationResult(safe=True, reason="Empty text", flags=[])

    contents = [
        f"Moderate the following user-submitted text:\n\n---\n{text}\n---"
    ]
    return _call_gemini(contents)


def moderate_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> ModerationResult:
    """
    Moderate an image using Gemini's vision capabilities.
    Checks for NSFW content, violence, embedded PII via OCR, and more.
    Called asynchronously (BackgroundTask) after the post record is created.
    """
    if not image_bytes:
        return ModerationResult(safe=True, reason="No image bytes provided", flags=[])

    part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    contents = [
        part,
        (
            "Moderate this image. Check for: NSFW content, graphic violence, "
            "hate symbols, and any PII visible in the image (e.g. phone numbers, "
            "email addresses, government IDs, credit cards). "
            "Respond ONLY with the JSON format specified."
        ),
    ]
    return _call_gemini(contents)


def moderate_video(video_bytes: bytes, mime_type: str = "video/mp4") -> ModerationResult:
    """
    Moderate a video using Gemini's File API (handles long videos).
    Uploads to Gemini temporarily, analyzes, then the file expires automatically.
    Falls back to safe=True on upload failure to prevent blocking the post.
    """
    if not video_bytes:
        return ModerationResult(safe=True, reason="No video bytes provided", flags=[])

    if not _client:
        return ModerationResult(safe=True, reason="Moderation skipped (no API key)", flags=[])

    try:
        import tempfile
        import pathlib

        # Write to temp file so Gemini File API can upload it
        suffix = ".mp4" if "mp4" in mime_type else ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        # Upload to Gemini File API
        video_file = _client.files.upload(file=tmp_path, config={'mime_type': mime_type})

        # Wait for processing (Gemini needs to process video before analysis)
        max_wait = 60  # seconds
        waited = 0
        while video_file.state.name == "PROCESSING" and waited < max_wait:
            time.sleep(3)
            video_file = _client.files.get(name=video_file.name)
            waited += 3

        if video_file.state.name == "FAILED":
            return ModerationResult(safe=True, reason="Gemini video processing failed — passed", flags=[])

        contents = [
            video_file,
            (
                "Moderate this video. Sample frames throughout the video and check for: "
                "NSFW content, graphic violence, hate symbols, threats, and any PII "
                "visible on screen or audible (phone numbers, addresses, emails). "
                "Respond ONLY with the JSON format specified."
            ),
        ]
        result = _call_gemini(contents)

        # Cleanup: delete temp file and Gemini file
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        try:
            _client.files.delete(name=video_file.name)
        except Exception:
            pass  # Non-critical

        return result

    except Exception as e:
        return ModerationResult(safe=True, reason=f"Video moderation error: {str(e)}", flags=[])
