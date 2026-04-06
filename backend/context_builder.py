"""
context_builder.py  —  Context Builder Agent

Sits BEFORE the Planner Agent.
Receives ALL attached files simultaneously, assigns roles, and produces
ONE unified context object:

  - Images / screenshots → LAYOUT AUTHORITY (visual structure, style, components)
  - PDFs                 → CONTENT AUTHORITY (features, interactions, copy, requirements)
  - Text/CSV/Markdown    → CONTENT AUTHORITY (data, requirements, copy)
  - Multiple of the same type → all merged together under their role

Output: { unified_prompt, layout_context, content_context, file_roles, analysis_per_file }

The unified_prompt is what gets passed to run_planner().
"""

import os
import re
import json
import base64
from io import BytesIO
from typing import Optional
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from PIL import Image, ImageChops

import logger as log

CONTENT_FILE_TEXT_CHAR_LIMIT = int(os.getenv("CONTEXT_FILE_TEXT_CHAR_LIMIT", "50000"))
from llm_utils import generate_content_with_retry


def _gemini_client():
    from coding import client as c
    return c


# ─────────────────────────────────────────────────────────────────
# REQUEST MODEL
# ─────────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    filename:    str
    file_type:   str                  # MIME type e.g. "image/png", "application/pdf"
    file_base64: Optional[str] = None # base64 for images/PDFs
    media_type:  Optional[str] = None # explicit mime override
    file_text:   Optional[str] = None # plain text for txt/md/csv/docx


class ContextBuildRequest(BaseModel):
    files:       list[FileEntry]
    mode:        str            = "replicate"   # replicate | improve | inspire
    instruction: Optional[str] = ""


# ─────────────────────────────────────────────────────────────────
# ROLE ASSIGNMENT
# ─────────────────────────────────────────────────────────────────

def _assign_role(file_entry: FileEntry) -> str:
    """
    Assign LAYOUT or CONTENT role based on file type.
    Images → layout authority
    Everything else → content authority
    """
    ft = (file_entry.file_type or "").lower()
    fn = (file_entry.filename  or "").lower()

    if ft.startswith("image/"):
        return "layout"

    if ft == "application/pdf" or fn.endswith(".pdf"):
        return "content"

    if ft.startswith("text/") or any(fn.endswith(e) for e in [".txt", ".md", ".csv", ".docx", ".doc"]):
        return "content"

    # Fallback: if it has base64 and no text, treat as layout
    if file_entry.file_base64 and not file_entry.file_text:
        return "layout"

    return "content"


def _decode_image_base64(file_base64: str) -> Image.Image:
    raw = base64.b64decode(file_base64)
    with Image.open(BytesIO(raw)) as img:
        return img.convert("RGB")


def _encode_image_base64(image: Image.Image, media_type: str) -> tuple[str, str]:
    output = BytesIO()
    normalized_media_type = (media_type or "image/png").lower()
    if normalized_media_type == "image/jpeg":
        image.save(output, format="JPEG", quality=95)
        return base64.b64encode(output.getvalue()).decode("utf-8"), "image/jpeg"

    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("utf-8"), "image/png"


def _average_rgb(image: Image.Image) -> tuple[int, int, int]:
    tiny = image.resize((1, 1))
    pixel = tiny.getpixel((0, 0))
    return tuple(int(channel) for channel in pixel[:3])


def _crop_outer_capture_padding(file_base64: str, media_type: str) -> tuple[str, str, dict]:
    try:
        image = _decode_image_base64(file_base64)
    except Exception as exc:
        log.warn("CONTEXT_BUILDER", f"Screenshot crop skipped — decode failed: {exc}")
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "decode_failed"}

    original_width, original_height = image.size
    if original_width < 200 or original_height < 200:
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "too_small"}

    downsample_max = 320
    scale = min(1.0, downsample_max / max(original_width, original_height))
    sample_size = (
        max(1, int(round(original_width * scale))),
        max(1, int(round(original_height * scale))),
    )
    sample = image.resize(sample_size)

    corner_span_x = max(8, sample_size[0] // 18)
    corner_span_y = max(8, sample_size[1] // 18)
    corner_boxes = [
        (0, 0, corner_span_x, corner_span_y),
        (sample_size[0] - corner_span_x, 0, sample_size[0], corner_span_y),
        (0, sample_size[1] - corner_span_y, corner_span_x, sample_size[1]),
        (sample_size[0] - corner_span_x, sample_size[1] - corner_span_y, sample_size[0], sample_size[1]),
    ]
    bg_colors = []
    for box in corner_boxes:
        color = _average_rgb(sample.crop(box))
        if color not in bg_colors:
            bg_colors.append(color)

    if not bg_colors:
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "no_background"}

    min_diff = None
    for bg_color in bg_colors:
        bg_image = Image.new("RGB", sample.size, bg_color)
        diff = ImageChops.difference(sample, bg_image).convert("L")
        min_diff = diff if min_diff is None else ImageChops.darker(min_diff, diff)

    thresholded = min_diff.point(lambda p: 255 if p > 18 else 0)
    foreground = thresholded.load()
    row_cutoff = max(4, int(sample_size[0] * 0.08))
    col_cutoff = max(4, int(sample_size[1] * 0.08))

    active_rows = []
    for y in range(sample_size[1]):
        count = 0
        for x in range(sample_size[0]):
            if foreground[x, y]:
                count += 1
        if count >= row_cutoff:
            active_rows.append(y)

    active_cols = []
    for x in range(sample_size[0]):
        count = 0
        for y in range(sample_size[1]):
            if foreground[x, y]:
                count += 1
        if count >= col_cutoff:
            active_cols.append(x)

    if not active_rows or not active_cols:
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "no_dense_foreground"}

    pad = 3
    left_small = max(0, active_cols[0] - pad)
    right_small = min(sample_size[0], active_cols[-1] + pad + 1)
    top_small = max(0, active_rows[0] - pad)
    bottom_small = min(sample_size[1], active_rows[-1] + pad + 1)

    left = max(0, int(left_small / scale))
    right = min(original_width, int((right_small + 1) / scale))
    top = max(0, int(top_small / scale))
    bottom = min(original_height, int((bottom_small + 1) / scale))

    cropped_width = max(0, right - left)
    cropped_height = max(0, bottom - top)
    if cropped_width < original_width * 0.55 or cropped_height < original_height * 0.55:
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "crop_too_aggressive"}

    if (
        left <= 12 and
        top <= 12 and
        (original_width - right) <= 12 and
        (original_height - bottom) <= 12
    ):
        return file_base64, media_type or "image/png", {"cropped": False, "reason": "no_meaningful_padding"}

    cropped = image.crop((left, top, right, bottom))
    encoded, output_media_type = _encode_image_base64(cropped, media_type)
    return encoded, output_media_type, {
        "cropped": True,
        "bounds": {"left": left, "top": top, "right": right, "bottom": bottom},
        "original_size": {"width": original_width, "height": original_height},
        "cropped_size": {"width": cropped_width, "height": cropped_height},
    }


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────────

LAYOUT_ANALYSIS_PROMPT = """You are a senior UI/UX analyst. You are examining a visual reference (screenshot, mockup, or design image).

Extract ONLY the visual and structural information — do NOT invent content.

Return JSON:
{
  "layout_type": "one of: landing_page | product_screen | dashboard | crm | ecommerce | portfolio | documentation | other",
  "visual_style": "describe color palette, typography style, spacing, card style, and visual tone in 2-3 sentences",
  "detected_sections": ["list of sections visible: e.g. navbar, hero, sidebar, table, modal, footer"],
  "detected_components": ["list of UI components: e.g. data table, search bar, dropdown, button group, stat card"],
  "layout_description": "2-3 sentence description of the overall layout structure and how sections are arranged",
  "color_palette": "primary and secondary colors observed",
  "screen_type": "one of: full_page | modal | drawer | component | partial",
  "outer_padding_present": true,
  "viewport_fill_guidance": "Explain whether the screenshot contains empty capture padding or browser whitespace that should be ignored, and describe how much of the 1440px frame the real UI should occupy."
}

Output ONLY valid JSON. No explanation.
"""

CONTENT_ANALYSIS_PROMPT = """You are a product analyst reading a requirements or specification document.

Extract ONLY the content, features, and interactions described. Do NOT invent visual details.

Return JSON:
{
  "product_type": "one of: crm | project_management | ecommerce | dashboard | saas | landing_page | documentation | other",
  "features": ["list every distinct feature or screen described"],
  "interactions": ["list key user interactions e.g. 'user clicks Hold to pause workflow'"],
  "entities": ["list domain entities e.g. 'Contact', 'Deal', 'Task', 'Invoice'"],
  "pages_or_screens": ["list all screens or pages mentioned by name"],
  "content_summary": "2-3 sentence summary of what this product is and does",
  "key_workflows": ["list the main workflows described e.g. 'Hold/Resume contact', 'Create deal pipeline'"],
  "explicit_workflow_rows": [
    {
      "row_label": "Row 1",
      "steps": [
        {
          "name": "Home Page1",
          "instruction": "Any explicit instruction given for this page/state"
        },
        {
          "name": "New Project Button click",
          "instruction": "What happens in this click state"
        },
        {
          "name": "New Project Open",
          "instruction": "What the opened state should show"
        }
      ]
    }
  ],
  "explicit_workflow_columns": [
    {
      "column_label": "Column 1",
      "column_title": "Team A",
      "rows": [
        {
          "row_label": "Row 1",
          "steps": [
            {"name": "Home Page1", "instruction": "Explicit instruction for this state"},
            {"name": "New Project Button click", "instruction": "Explicit instruction for this state"},
            {"name": "New Project Open", "instruction": "Explicit instruction for this state"}
          ]
        }
      ]
    }
  ],
  "screen_instructions": [
    {
      "name": "Home Page1",
      "instruction": "Specific instruction for this screen if the document provides one"
    }
  ]
}

IMPORTANT:
- If the document explicitly defines workflow rows, steps, page order, or branch order, capture them in `explicit_workflow_rows` exactly in the same order.
- If the document explicitly defines columns/teams/modules with rows inside each one, capture them in `explicit_workflow_columns` exactly in the same order.
- If the document gives page-by-page instructions, preserve them in `screen_instructions`.
- Do not summarize explicit row order away.

Output ONLY valid JSON. No explanation.
"""

UNIFIER_PROMPT = """You are a senior product designer and architect.

You have been given:
  1. LAYOUT CONTEXT — extracted from visual reference files (screenshots, mockups). This tells you HOW to style and structure the design.
  2. CONTENT CONTEXT — extracted from specification documents (PDFs, text files). This tells you WHAT features and screens to design.
  3. USER INSTRUCTION — any extra guidance from the user.
  4. MODE — replicate | improve | inspire

Your job: combine these two orthogonal sources into ONE unified design prompt.

Rules:
- The layout context defines the visual style, component types, and structural patterns. Follow it.
- The content context defines the features, screens, entities, and workflows. Include ALL of them.
- If the content context contains explicit workflow rows or exact step order, preserve that order exactly.
- Neither overrides the other — they serve different purposes.
- Do NOT default to a "landing page" unless the content explicitly describes one.
- If content describes a CRM, dashboard, or product screen — the output must describe FULL-FIDELITY PRODUCT SCREENS, not documentation boards.
- Each major feature from the content context should become a left-to-right feature flow made of separate full-screen Figma frames.
- The flows should cover all meaningful interaction states without omitting major steps.
- If explicit workflow rows are present, do not reinterpret or compress them into fewer frames. Keep one frame per declared step in the same row order.
- If the screenshot has outer whitespace or capture padding around the real UI, explicitly ignore that padding and describe the actual site/app surface as filling the frame properly.
- If no layout context exists, infer a clean professional style.
- If no content context exists, use the layout as both style and content guide.

Output JSON:
{
  "unified_prompt": "A rich 150-250 word prompt describing the complete design to generate. Specifies: product type, all features/screens to generate as separate frames, visual style from layout reference, color palette, component types, and any key interactions.",
  "design_type": "one of: product_screen | landing_page | dashboard | crm | ecommerce | documentation",
  "frames_to_generate": ["list of frame names to generate, one per major feature/screen"],
  "style_notes": "brief visual style summary"
}

Output ONLY valid JSON. No explanation.
"""


# ─────────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────────

async def run_context_builder(request: ContextBuildRequest) -> JSONResponse:
    if not request.files:
        raise HTTPException(status_code=400, detail="No files provided")

    model_name = os.getenv("GEMINI_PLANNER_MODEL", "gemini-2.0-flash")
    client     = _gemini_client()

    log.info("CONTEXT_BUILDER", f"Received {len(request.files)} file(s) — mode={request.mode}")

    # ── Step 1: Assign roles ──────────────────────────────────────
    layout_files  = []
    content_files = []
    file_roles    = {}

    for f in request.files:
        role = _assign_role(f)
        file_roles[f.filename] = role
        if role == "layout":
            layout_files.append(f)
        else:
            content_files.append(f)

    log.info("CONTEXT_BUILDER",
        f"Role assignment — layout={len(layout_files)} file(s), content={len(content_files)} file(s)"
    )

    # ── Step 2: Analyze layout files ─────────────────────────────
    layout_analyses = []
    for lf in layout_files:
        try:
            result = await _analyze_layout_file(lf, model_name, client)
            layout_analyses.append({"filename": lf.filename, "analysis": result})
            log.info("CONTEXT_BUILDER", f"Layout analyzed: {lf.filename!r}")
        except Exception as e:
            log.warn("CONTEXT_BUILDER", f"Layout analysis failed for {lf.filename!r}: {e}")

    # ── Step 3: Analyze content files ────────────────────────────
    content_analyses = []
    for cf in content_files:
        try:
            result = await _analyze_content_file(cf, model_name, client)
            content_analyses.append({"filename": cf.filename, "analysis": result})
            log.info("CONTEXT_BUILDER", f"Content analyzed: {cf.filename!r}")
        except Exception as e:
            log.warn("CONTEXT_BUILDER", f"Content analysis failed for {cf.filename!r}: {e}")

    # ── Step 4: Merge all analyses into unified context ───────────
    layout_context  = _merge_layout_analyses(layout_analyses)
    content_context = _merge_content_analyses(content_analyses)

    # ── Step 5: Call unifier to produce unified_prompt ───────────
    unified = await _run_unifier(
        layout_context  = layout_context,
        content_context = content_context,
        instruction     = request.instruction or "",
        mode            = request.mode,
        model_name      = model_name,
        client          = client,
    )

    unified_prompt = unified.get("unified_prompt", "")
    if len(unified_prompt) < 50:
        unified_prompt = "Create a modern, professional web application design."

    if request.instruction:
        unified_prompt += f". Additional requirements: {request.instruction}"

    log.success("CONTEXT_BUILDER",
        f"Unified context built — prompt={len(unified_prompt)}ch  "
        f"frames={len(unified.get('frames_to_generate', []))}  "
        f"type={unified.get('design_type','?')}"
    )

    # ── Extract screenshot base64 from first layout file ─────────
    screenshot_base64     = None
    screenshot_media_type = "image/png"
    if layout_files:
        first = layout_files[0]
        if first.file_base64:
            screenshot_base64     = first.file_base64
            screenshot_media_type = first.media_type or first.file_type or "image/png"
            screenshot_base64, screenshot_media_type, crop_meta = _crop_outer_capture_padding(
                screenshot_base64,
                screenshot_media_type,
            )
            if crop_meta.get("cropped"):
                log.info(
                    "CONTEXT_BUILDER",
                    "Forwarding cropped screenshot "
                    f"({crop_meta['cropped_size']['width']}x{crop_meta['cropped_size']['height']})"
                )
            else:
                log.info("CONTEXT_BUILDER", f"Forwarding screenshot ({len(screenshot_base64)} chars)")

    return JSONResponse({
        "success":               True,
        "mode":                  request.mode,
        "unified_prompt":        unified_prompt,
        "design_type":           unified.get("design_type", "product_screen"),
        "frames_to_generate":    unified.get("frames_to_generate", []),
        "style_notes":           unified.get("style_notes", ""),
        "layout_context":        layout_context,
        "content_context":       content_context,
        "file_roles":            file_roles,
        "files_processed":       len(request.files),
        "screenshot_base64":     screenshot_base64,
        "screenshot_media_type": screenshot_media_type,
    })


# ─────────────────────────────────────────────────────────────────
# ANALYSIS HELPERS
# ─────────────────────────────────────────────────────────────────

async def _analyze_layout_file(file_entry: FileEntry, model_name: str, client) -> dict:
    """Analyze a layout file (image/screenshot) with Gemini vision."""
    media_type = file_entry.media_type or file_entry.file_type or "image/png"
    image_base64 = file_entry.file_base64

    if image_base64:
        image_base64, media_type, crop_meta = _crop_outer_capture_padding(image_base64, media_type)
        if crop_meta.get("cropped"):
            log.info(
                "CONTEXT_BUILDER",
                "Layout screenshot cropped to remove outer capture padding "
                f"({crop_meta['original_size']['width']}x{crop_meta['original_size']['height']} -> "
                f"{crop_meta['cropped_size']['width']}x{crop_meta['cropped_size']['height']})"
            )

    contents = [
        {
            "inline_data": {
                "mime_type": media_type,
                "data":      image_base64,
            }
        },
        f"{LAYOUT_ANALYSIS_PROMPT}\n\nFilename: {file_entry.filename}\n\nAnalyze this visual reference and return the JSON object."
    ]

    response = await generate_content_with_retry(
        client=client,
        model=model_name,
        contents=contents,
        config={"temperature": 0.2},
        log_tag="CONTEXT_BUILDER",
        action=f"Analyze layout file {file_entry.filename!r}",
    )
    return _parse_json(response.text)


async def _analyze_content_file(file_entry: FileEntry, model_name: str, client) -> dict:
    """Analyze a content file (PDF, text, CSV, etc.) with Gemini."""

    if file_entry.file_base64:
        # PDF sent as base64
        media_type = file_entry.media_type or file_entry.file_type or "application/pdf"
        contents = [
            {
                "inline_data": {
                    "mime_type": media_type,
                    "data":      file_entry.file_base64,
                }
            },
            f"{CONTENT_ANALYSIS_PROMPT}\n\nFilename: {file_entry.filename}\n\nAnalyze this document and return the JSON object."
        ]
    else:
        # Plain text
        source_text = file_entry.file_text or ""
        if CONTENT_FILE_TEXT_CHAR_LIMIT > 0:
            text_snippet = source_text[:CONTENT_FILE_TEXT_CHAR_LIMIT]
            if len(source_text) > CONTENT_FILE_TEXT_CHAR_LIMIT:
                text_snippet += "\n\n[... document truncated ...]"
                log.warn(
                    "CONTEXT_BUILDER",
                    f"Content file {file_entry.filename!r} truncated to {CONTENT_FILE_TEXT_CHAR_LIMIT} chars (was {len(source_text)})"
                )
        else:
            text_snippet = source_text
        contents = [
            f"{CONTENT_ANALYSIS_PROMPT}\n\nFilename: {file_entry.filename}\n\nDOCUMENT CONTENT:\n{text_snippet}\n\nAnalyze this document and return the JSON object."
        ]

    response = await generate_content_with_retry(
        client=client,
        model=model_name,
        contents=contents,
        config={"temperature": 0.2},
        log_tag="CONTEXT_BUILDER",
        action=f"Analyze content file {file_entry.filename!r}",
    )
    return _parse_json(response.text)


def _merge_layout_analyses(analyses: list[dict]) -> dict:
    """Merge multiple layout analyses into one combined layout context."""
    if not analyses:
        return {}

    if len(analyses) == 1:
        return analyses[0]["analysis"]

    # Combine: collect all sections/components, use first file's style as base
    merged = {
        "layout_type":         analyses[0]["analysis"].get("layout_type", "other"),
        "visual_style":        " | ".join(a["analysis"].get("visual_style", "") for a in analyses if a["analysis"].get("visual_style")),
        "detected_sections":   list({s for a in analyses for s in a["analysis"].get("detected_sections", [])}),
        "detected_components": list({c for a in analyses for c in a["analysis"].get("detected_components", [])}),
        "layout_description":  " ".join(a["analysis"].get("layout_description", "") for a in analyses),
        "color_palette":       analyses[0]["analysis"].get("color_palette", ""),
        "screen_type":         analyses[0]["analysis"].get("screen_type", "full_page"),
        "outer_padding_present": any(a["analysis"].get("outer_padding_present", False) for a in analyses),
        "viewport_fill_guidance": " | ".join(
            a["analysis"].get("viewport_fill_guidance", "")
            for a in analyses if a["analysis"].get("viewport_fill_guidance")
        ),
        "source_files":        [a["filename"] for a in analyses],
    }
    return merged


def _merge_content_analyses(analyses: list[dict]) -> dict:
    """Merge multiple content analyses into one combined content context."""
    if not analyses:
        return {}

    if len(analyses) == 1:
        return analyses[0]["analysis"]

    explicit_rows = []
    explicit_columns = []
    seen_row_signatures = set()
    seen_column_signatures = set()
    screen_instructions = []
    seen_screen_instruction = set()
    for entry in analyses:
        analysis = entry["analysis"]
        for column in analysis.get("explicit_workflow_columns", []) or []:
            if not isinstance(column, dict):
                continue
            rows = column.get("rows", []) or []
            signature = (
                (column.get("column_label") or "").strip().lower(),
                (column.get("column_title") or "").strip().lower(),
                tuple(
                    (
                        (row.get("row_label") or "").strip().lower(),
                        tuple((str(step.get("name", "")).strip().lower(), str(step.get("instruction", "")).strip().lower()) for step in (row.get("steps", []) or []) if isinstance(step, dict)),
                    )
                    for row in rows if isinstance(row, dict)
                ),
            )
            if signature in seen_column_signatures:
                continue
            seen_column_signatures.add(signature)
            explicit_columns.append(column)

        for row in analysis.get("explicit_workflow_rows", []) or []:
            if not isinstance(row, dict):
                continue
            steps = row.get("steps", []) or []
            signature = (
                (row.get("row_label") or "").strip().lower(),
                tuple((str(step.get("name", "")).strip().lower(), str(step.get("instruction", "")).strip().lower()) for step in steps if isinstance(step, dict)),
            )
            if signature in seen_row_signatures:
                continue
            seen_row_signatures.add(signature)
            explicit_rows.append(row)

        for item in analysis.get("screen_instructions", []) or []:
            if not isinstance(item, dict):
                continue
            signature = (
                str(item.get("name", "")).strip().lower(),
                str(item.get("instruction", "")).strip().lower(),
            )
            if signature in seen_screen_instruction:
                continue
            seen_screen_instruction.add(signature)
            screen_instructions.append(item)

    merged = {
        "product_type":     analyses[0]["analysis"].get("product_type", "other"),
        "features":         list({f for a in analyses for f in a["analysis"].get("features", [])}),
        "interactions":     list({i for a in analyses for i in a["analysis"].get("interactions", [])}),
        "entities":         list({e for a in analyses for e in a["analysis"].get("entities", [])}),
        "pages_or_screens": list({p for a in analyses for p in a["analysis"].get("pages_or_screens", [])}),
        "content_summary":  " ".join(a["analysis"].get("content_summary", "") for a in analyses),
        "key_workflows":    list({w for a in analyses for w in a["analysis"].get("key_workflows", [])}),
        "explicit_workflow_columns": explicit_columns,
        "explicit_workflow_rows": explicit_rows,
        "screen_instructions": screen_instructions,
        "source_files":     [a["filename"] for a in analyses],
    }
    return merged


async def _run_unifier(
    layout_context: dict,
    content_context: dict,
    instruction: str,
    mode: str,
    model_name: str,
    client,
) -> dict:
    """Call Gemini to produce the final unified prompt."""

    layout_block  = f"LAYOUT CONTEXT:\n{json.dumps(layout_context,  indent=2)}\n\n" if layout_context  else "LAYOUT CONTEXT: none — infer a clean professional style.\n\n"
    content_block = f"CONTENT CONTEXT:\n{json.dumps(content_context, indent=2)}\n\n" if content_context else "CONTENT CONTEXT: none — use layout as both style and content guide.\n\n"
    instr_block   = f"USER INSTRUCTION: {instruction}\n\n" if instruction else ""
    mode_block    = f"MODE: {mode}\n\n"

    full_prompt = (
        f"{UNIFIER_PROMPT}\n\n"
        f"{layout_block}"
        f"{content_block}"
        f"{instr_block}"
        f"{mode_block}"
        "Now produce the unified JSON output."
    )

    response = await generate_content_with_retry(
        client=client,
        model=model_name,
        contents=full_prompt,
        config={"temperature": 0.3},
        log_tag="CONTEXT_BUILDER",
        action="Build unified context",
    )
    return _parse_json(response.text)


# ─────────────────────────────────────────────────────────────────
# JSON HELPER
# ─────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find('{')
    end     = cleaned.rfind('}')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warn("CONTEXT_BUILDER", "JSON parse failed — returning raw fallback")
        return {"raw": raw[:1000]}
