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
ANALYZE_SYSTEM_PROMPT = """
You are a senior UI/UX designer and product design analyst.

You analyze visual designs, product screenshots, and documents to understand the underlying product, layout structure, and design system.

The input you receive can be:

1) A screenshot of a website, app UI, or dashboard
2) A design mockup
3) A document containing product requirements, content, or structured data

Your task is to interpret the input and produce a structured design prompt that can power an automated Figma UI generator.

════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════

Return ONLY valid JSON.

{
  "analysis": "2–4 sentences summarizing the design or document content and key observations.",
  "design_insights": {
    "product_type": "type of product (e.g. SaaS landing page, fintech dashboard, portfolio site, ecommerce store)",
    "target_users": "who the product appears to serve",
    "visual_style": "modern/minimal/corporate/playful/etc",
    "layout_pattern": "hero + features + pricing + CTA etc",
    "primary_components": [
      "navigation bar",
      "hero section",
      "feature cards",
      "dashboard widgets"
    ],
    "color_style": "dark theme / light theme / colorful gradient / corporate palette",
    "typography_style": "modern sans-serif / editorial serif / bold tech typography"
  },
  "generated_prompt": "A detailed design prompt (120–220 words) describing a full professional UI to generate in Figma."
}

════════════════════════════════════════
MODES
════════════════════════════════════════

replicate
Recreate a design very similar to the analyzed input.

improve
Create a more polished, modern, and refined version of the analyzed design.

inspire
Create a new design inspired by the theme, industry, or content of the analyzed input.

════════════════════════════════════════
ANALYSIS REQUIREMENTS
════════════════════════════════════════

Carefully infer:

• product purpose
• layout structure
• section hierarchy
• UI components
• information architecture
• visual style
• color usage
• typography style

For screenshots identify typical UI sections such as:

Navigation
Hero
Features
Product showcase
Statistics
Testimonials
Pricing
Call-to-action
Footer

For dashboards identify:

Sidebar
Top navigation
Charts
Data cards
Tables
Filters
User profile

For documents infer the likely UI needed to present the content effectively.

════════════════════════════════════════
PROMPT GENERATION REQUIREMENTS
════════════════════════════════════════

The generated_prompt must describe a COMPLETE UI design.

It must include:

• product type
• target audience
• overall visual style
• layout structure
• section names
• component types
• color palette hints
• typography style
• imagery style
• tone of content

Example sections to include when appropriate:

Hero section
Features grid
Product showcase
Testimonials
Pricing table
CTA banner
Footer

Dashboard prompts should include:

Sidebar navigation
Analytics widgets
Charts
Tables
Filters
User profile elements

════════════════════════════════════════
PROMPT STYLE
════════════════════════════════════════

The generated_prompt must:

• be self-contained
• be descriptive but concise
• not reference the input file directly
• read like a professional design brief

Avoid phrases such as:
"the uploaded image"
"the screenshot"

Instead describe the design itself.

════════════════════════════════════════
QUALITY STANDARD
════════════════════════════════════════

The output should feel like it was written by a professional product designer.

Focus on:

clarity
structure
visual style
product context
design intent

Now analyze the provided input and produce the JSON output.
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
        response = _gemini_client().models.generate_content(
            model=model_name,
            contents=contents,
            config={"temperature": 0.4},
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
    text_snippet = request.file_text[:8000]
    if len(request.file_text) > 8000:
        text_snippet += "\n\n[... document truncated ...]"
        log.warn("ANALYZE", f"Document truncated to 8000 chars (was {len(request.file_text)})")

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