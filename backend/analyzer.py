"""
analyzer.py  —  File analysis via Gemini Vision / text

Extracted from main.py to keep routing clean.
Handles: image, PDF (base64) and plain-text document analysis.
Returns: { analysis, generated_prompt }
"""

import os
import re
import json
from typing import Optional
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import logger as log
from llm_utils import generate_content_with_retry

TEXT_ANALYSIS_CHAR_LIMIT = int(os.getenv("ANALYZER_TEXT_CHAR_LIMIT", "40000"))

# ── Lazy import of Gemini client from coding.py to avoid circular deps
def _gemini_client():
    from coding import client as c
    return c


# ─────────────────────────────────────────────────────────────────
# REQUEST MODEL
# ─────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    mode:        str            = "replicate"   # replicate | improve | inspire
    instruction: Optional[str] = ""
    filename:    Optional[str] = ""
    file_type:   Optional[str] = ""

    # ONE of these will be present:
    file_base64: Optional[str] = None   # base64 string  (images / PDF)
    media_type:  Optional[str] = None   # e.g. "image/png", "application/pdf"
    file_text:   Optional[str] = None   # plain text  (txt / md / csv / docx)


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────

ANALYZE_SYSTEM_PROMPT = """You are an expert UI/UX designer and design analyst.

You will receive either:
  (a) A screenshot or image of an existing design / website / app, OR
  (b) A text document (requirements, spec, copy, CSV data, etc.)

Your task: carefully analyze the input and produce TWO outputs.

── OUTPUT FORMAT (strict JSON, no markdown fences) ──
{
  "analysis": "A concise, insightful 2–4 sentence description of what you see / read and the key design/content observations.",
  "generated_prompt": "A rich, detailed prompt (100–200 words) that will be fed into a Figma design generator. The prompt must describe a complete, professional website or app UI including: page sections, layout, typography style, color palette, imagery style, component types, and any domain-specific content."
}

── MODES ──
replicate : The generated_prompt should faithfully describe recreating the input as a Figma design.
improve   : The generated_prompt should describe an enhanced, more polished version of the input.
inspire   : The generated_prompt should describe a new, creative design inspired by the input's theme/content.

── RULES ──
- Output ONLY valid JSON. No explanation outside the JSON.
- generated_prompt must be self-contained (no references to "the uploaded image").
- generated_prompt must specify a real website type (portfolio, SaaS landing page, dashboard, e-commerce, etc.)
- Include color palette hints, typography style, and section names in generated_prompt.
"""


# ─────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────

async def run_analyze(request: AnalyzeRequest) -> JSONResponse:
    """
    Analyze an uploaded file with Gemini and return
    { analysis, generated_prompt } as a JSONResponse.
    """
    if not request.file_base64 and not request.file_text:
        raise HTTPException(status_code=400, detail="No file content provided")

    model_name = os.getenv("GEMINI_PLANNER_MODEL", "gemini-2.0-flash")

    log.info("ANALYZE", f"mode={request.mode} file={request.filename!r} type={request.file_type!r}")

    instruction_block = f"\nUSER INSTRUCTIONS: {request.instruction}\n" if request.instruction else ""
    mode_block        = f"MODE: {request.mode}\n"

    try:
        contents = _build_contents(request, mode_block, instruction_block)

        log.info("ANALYZE", f"Calling Gemini model={model_name}")
        response = await generate_content_with_retry(
            client=_gemini_client(),
            model=model_name,
            contents=contents,
            config={"temperature": 0.4},
            log_tag="ANALYZE",
            action=f"Analyze file {request.filename!r}",
        )
        raw = response.text.strip()
        log.info("ANALYZE", f"Gemini response received ({len(raw)} chars)")

        parsed      = _parse_json_response(raw)
        analysis    = parsed.get("analysis", "")
        gen_prompt  = parsed.get("generated_prompt", "")

        if len(gen_prompt) < 50:
            gen_prompt = f"Create a modern, professional website design. {gen_prompt}"

        if request.instruction:
            gen_prompt += f". Additional requirements: {request.instruction}"

        log.success("ANALYZE",
            f"Done — analysis={len(analysis)}ch  prompt={len(gen_prompt)}ch",
            extra={"filename": request.filename, "mode": request.mode}
        )

        return JSONResponse({
            "success":          True,
            "mode":             request.mode,
            "filename":         request.filename,
            "analysis":         analysis,
            "generated_prompt": gen_prompt,
        })

    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        log.error("ANALYZE", f"Failed — {exc}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _build_contents(request: AnalyzeRequest, mode_block: str, instruction_block: str) -> list:
    if request.file_base64:
        media_type = request.media_type or "image/png"
        image_part = {
            "inline_data": {
                "mime_type": media_type,
                "data":      request.file_base64,
            }
        }
        text_part = (
            f"{ANALYZE_SYSTEM_PROMPT}\n"
            f"{mode_block}{instruction_block}\n"
            f"Filename: {request.filename}\n\n"
            "Analyze the attached file and return the JSON object."
        )
        log.debug("ANALYZE", f"Sending multimodal request ({media_type})")
        return [image_part, text_part]

    # Plain text document
    source_text = request.file_text or ""
    if TEXT_ANALYSIS_CHAR_LIMIT > 0:
        text_snippet = source_text[:TEXT_ANALYSIS_CHAR_LIMIT]
        if len(source_text) > TEXT_ANALYSIS_CHAR_LIMIT:
            text_snippet += "\n\n[... document truncated ...]"
            log.warn("ANALYZE", f"Document truncated to {TEXT_ANALYSIS_CHAR_LIMIT} chars (was {len(source_text)})")
    else:
        text_snippet = source_text

    full_text = (
        f"{ANALYZE_SYSTEM_PROMPT}\n"
        f"{mode_block}{instruction_block}\n"
        f"Filename: {request.filename}\n\n"
        f"DOCUMENT CONTENT:\n{text_snippet}\n\n"
        "Analyze this document and return the JSON object."
    )
    log.debug("ANALYZE", f"Sending text request ({len(text_snippet)} chars)")
    return [full_text]


def _parse_json_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find('{')
    end     = cleaned.rfind('}')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warn("ANALYZE", "JSON parse failed — wrapping raw response as fallback")
        return {
            "analysis":         raw[:500],
            "generated_prompt": raw if len(raw) < 1000 else raw[:800],
        }
