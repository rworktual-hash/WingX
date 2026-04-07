import os
import json
import re
import time
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from planner  import run_planner
from coding   import generate_page_nodes
from analyzer import run_analyze, AnalyzeRequest
from context_builder import run_context_builder, ContextBuildRequest
import logger as log
from llm_utils import generate_content_with_retry

app = FastAPI(title="Worktual AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHILDREN_PER_CHUNK = 8


class PromptRequest(BaseModel):
    prompt:                str
    layout_context:        Optional[dict] = None
    content_context:       Optional[dict] = None
    frames_to_generate:    Optional[list[str]] = None
    mode:                  Optional[str] = None
    screenshot_base64:     Optional[str]  = None
    screenshot_media_type: Optional[str]  = "image/png"
    memory_context:        Optional[dict] = None
    selected_node:         Optional[dict] = None
    source_prompt:         Optional[str] = None


class FollowupGenerateRequest(BaseModel):
    prompt:                str
    selected_node:         dict
    memory_context:        Optional[dict] = None
    layout_context:        Optional[dict] = None
    content_context:       Optional[dict] = None
    screenshot_base64:     Optional[str] = None
    screenshot_media_type: Optional[str] = "image/png"
    source_prompt:         Optional[str] = None


class AttachmentFollowupRequest(BaseModel):
    prompt:                str
    primary_page:          dict
    context_pages:         Optional[list[dict]] = None
    components:            Optional[list[dict]] = None
    layout_context:        Optional[dict] = None
    content_context:       Optional[dict] = None
    screenshot_base64:     Optional[str] = None
    screenshot_media_type: Optional[str] = "image/png"
    project_title:         Optional[str] = None

# ─────────────────────────────────────────────────────────────────
# /  —  Health check
# ─────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    log.info("HEALTH", "Health check requested")
    return {"status": "running"}


# ─────────────────────────────────────────────────────────────────
# /logs  —  Return recent in-memory log entries
# ─────────────────────────────────────────────────────────────────
@app.get("/logs")
def get_logs(n: int = 200):
    return JSONResponse({"logs": log.get_recent(n)})


# ─────────────────────────────────────────────────────────────────
# /analyze
# ─────────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze_file(request: AnalyzeRequest):
    return await run_analyze(request)

@app.post("/analyze-context")
async def analyze_context(request: ContextBuildRequest):
    return await run_context_builder(request)

# ─────────────────────────────────────────────────────────────────
# /api/image-proxy  —  Proxy external images (Picsum, etc.) to Figma
# ─────────────────────────────────────────────────────────────────
import urllib.parse, httpx

@app.get("/api/image-proxy")
async def image_proxy(url: str = "", hash: str = ""):
    target = url or ""
    if not target and hash:
        target = f"https://picsum.photos/seed/{hash[:8]}/400/300"
    if not target:
        raise HTTPException(status_code=400, detail="No url provided")
    try:
        decoded = urllib.parse.unquote(target)
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as cli:
            r = await cli.get(decoded)
            r.raise_for_status()
            from fastapi.responses import Response
            return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image proxy failed: {exc}")

# ─────────────────────────────────────────────────────────────────
# /plan
# ─────────────────────────────────────────────────────────────────
@app.post("/plan")
async def plan_route(request: PromptRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    return await run_planner(
        user_prompt=request.prompt,
        layout_context=request.layout_context,
        content_context=request.content_context,
        frames_to_generate=request.frames_to_generate,
        mode=request.mode,
    )


# ─────────────────────────────────────────────────────────────────
# /generate — SSE stream, one page at a time, children chunked
# ─────────────────────────────────────────────────────────────────
@app.post("/generate")
async def generate(request: PromptRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    log.info("GENERATE", f"Request received — prompt: {request.prompt[:80]!r}")
    
    async def stream_pages():
        import asyncio
        start = time.time()
        try:
            yield sse("status", {"message": "Planning your design..."})
            yield sse_log(log.info("GENERATE", "Starting planner..."))

            plan = await run_planner(
                user_prompt=request.prompt,
                layout_context=request.layout_context,
                content_context=request.content_context,
                frames_to_generate=request.frames_to_generate,
                mode=request.mode,
            )

            yield sse_log(log.success("GENERATE",
                f"Plan ready: {plan['project_title']!r} ({plan['total_pages']} pages)"
            ))
            yield sse("plan_ready", {
                "project_title": plan["project_title"],
                "total_pages":   plan["total_pages"],
                "pages": [{"id": p["id"], "name": p["name"]} for p in plan["pages"]],
            })

            total = plan["total_pages"]
            pages = plan["pages"]

            yield sse("status", {
                "message":     f"Generating all {total} frame(s) simultaneously (max 3 at once)...",
                "total_pages": total,
            })
            for i, page in enumerate(pages):
                yield sse_log(log.info("GENERATE",
                    f"[{i+1}/{total}] Queued: {page['name']!r}"
                ))

            # ── Queue-based streaming: render each frame as it completes ──
            # asyncio.Queue bridges concurrent tasks → async generator yields
            result_queue = asyncio.Queue()
            semaphore    = asyncio.Semaphore(3)

            async def _generate_one(page, index):
                async with semaphore:
                    log.info("GENERATE", f"Concurrent slot acquired for page={page['name']!r}")
                    try:
                        result = await generate_page_nodes(
                            page=page,
                            project_title=plan["project_title"],
                            user_prompt=request.prompt,
                            layout_context=request.layout_context,
                            screenshot_base64=request.screenshot_base64,
                            screenshot_media_type=request.screenshot_media_type or "image/png",
                        )
                        await result_queue.put((index, page, result, None))
                    except Exception as exc:
                        await result_queue.put((index, page, None, exc))

            # Fire all tasks — semaphore controls max 3 in-flight
            tasks = [
                asyncio.create_task(_generate_one(page, i))
                for i, page in enumerate(pages)
            ]

            # ── Ordered streaming via pending buffer ──────────────────
            # As tasks complete they push to result_queue in any order.
            # We hold out-of-order results in pending{} and only stream
            # when the NEXT expected index is ready — preserving order.
            pending      = {}   # index → (page, result, exc)
            next_to_send = 0
            received     = 0
            generated    = 0

            while received < total:
                index, page, result, exc = await result_queue.get()
                received += 1
                pending[index] = (page, result, exc)

                # Drain all consecutive ready results from the front
                while next_to_send in pending:
                    p, res, err = pending.pop(next_to_send)
                    n = next_to_send + 1
                    next_to_send += 1

                    if err is not None:
                        import traceback
                        log.error("GENERATE", f"Page={p['name']!r} failed — {err}")
                        yield sse_log(log.error("GENERATE", f"Page={p['name']!r} failed — {err}"))
                        yield sse("page_error", {
                            "page_id":     p["id"],
                            "page_name":   p["name"],
                            "page_number": n,
                            "error":       str(err),
                        })
                        continue

                    frame    = res["frame"]
                    children = frame.get("children", [])
                    chunks   = _chunk_list(children, CHILDREN_PER_CHUNK)
                    n_chunks = len(chunks)

                    yield sse_log(log.info("GENERATE",
                        f"Streaming page={p['name']!r} — {len(children)} elements in {n_chunks} chunks"
                    ))
                    yield sse("page_start", {
                        "page_id":        res["page_id"],
                        "page_name":      res["page_name"],
                        "page_number":    n,
                        "total_pages":    total,
                        "theme": {
                            **res["theme"],
                            "feature_group": p.get("feature_group", res["page_name"].split("—")[0].strip()),
                            "flow_group_id": p.get("flow_group_id", p.get("flow_group", p.get("feature_group", res["page_name"].split("—")[0].strip()))),
                            "flow_group": p.get("flow_group", p.get("feature_group", res["page_name"].split("—")[0].strip())),
                        },
                        "flow_meta": {
                            "feature_group": p.get("feature_group", ""),
                            "flow_group_id": p.get("flow_group_id", p.get("flow_group", p.get("feature_group", ""))),
                            "flow_group": p.get("flow_group", p.get("feature_group", "")),
                            "screen_title": p.get("screen_title", p.get("name", res["page_name"])),
                            "flow_step": p.get("flow_step"),
                            "flow_total": p.get("flow_total"),
                            "flow_group_step": p.get("flow_group_step"),
                            "flow_group_total": p.get("flow_group_total"),
                            "click_target_keywords": p.get("click_target_keywords", []),
                            "annotation_text": p.get("annotation_text", ""),
                            "annotation_target_keywords": p.get("annotation_target_keywords", []),
                            "branch_root": p.get("branch_root", ""),
                            "branch_trigger": p.get("branch_trigger", ""),
                            "branch_goal": p.get("branch_goal", ""),
                            "branch_kind": p.get("branch_kind", ""),
                            "column_group": p.get("column_group", ""),
                            "column_group_id": p.get("column_group_id", ""),
                            "column_group_order": p.get("column_group_order", 0),
                            "column_label": p.get("column_label", ""),
                            "column_title": p.get("column_title", ""),
                            "row_label": p.get("row_label", ""),
                            "row_order": p.get("row_order", 0),
                            "followup_source_frame_id": p.get("followup_source_frame_id", ""),
                            "followup_source_frame_name": p.get("followup_source_frame_name", ""),
                            "navigation": p.get("navigation", {}),
                            "project_navigation": p.get("project_navigation", {}),
                        },
                        "total_children": len(children),
                        "total_chunks":   n_chunks,
                        "frame_meta": {
                            "type":            frame["type"],
                            "name":            frame["name"],
                            "width":           frame["width"],
                            "height":          frame["height"],
                            "backgroundColor": frame["backgroundColor"],
                        },
                    })

                    for ci, chunk in enumerate(chunks):
                        chunk_payload = json.dumps({
                            "type": "page_chunk",
                            "payload": {
                                "page_id":      res["page_id"],
                                "chunk_index":  ci,
                                "total_chunks": n_chunks,
                                "children":     chunk,
                            }
                        })
                        if len(chunk_payload) > 16000:
                            yield sse_log(log.warn("GENERATE",
                                f"Chunk {ci} is {len(chunk_payload):,} chars — oversized"
                            ))
                        yield f"data: {chunk_payload}\n\n"

                    yield sse("page_end", {
                        "page_id":     res["page_id"],
                        "page_name":   res["page_name"],
                        "page_number": n,
                        "total_pages": total,
                    })
                    generated += 1
                    yield sse_log(log.success("GENERATE",
                        f"Page={p['name']!r} fully sent ({n_chunks} chunks)"
                    ))

            elapsed = round(time.time() - start, 1)
            yield sse_log(log.success("GENERATE",
                f"Complete — {generated}/{total} pages in {elapsed}s"
            ))
            yield sse("complete", {
                "project_title":   plan["project_title"],
                "total_pages":     total,
                "pages_generated": generated,
                "generation_time": f"{elapsed}s",
                "message": f"✅ {generated}/{total} pages generated in {elapsed}s",
            })

        except Exception as exc:
            import traceback
            traceback.print_exc()
            yield sse_log(log.error("GENERATE", f"Fatal error — {exc}"))
            yield sse("error", {"message": str(exc)})

    return StreamingResponse(
        stream_pages(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────
# /generate-full — non-streaming
# ─────────────────────────────────────────────────────────────────
@app.post("/generate-full")
async def generate_full(request: PromptRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    log.info("GENERATE-FULL", f"Request — prompt: {request.prompt[:80]!r}")
    start  = time.time()
    plan   = await run_planner(
        user_prompt=request.prompt,
        content_context=request.content_context,
    )
    frames = []

    for i, page in enumerate(plan["pages"]):
        try:
            result = await generate_page_nodes(
                page=page,
                project_title=plan["project_title"],
                user_prompt=request.prompt,
                layout_context=request.layout_context,
                screenshot_base64=request.screenshot_base64,
                screenshot_media_type=request.screenshot_media_type or "image/png",
            )
            frames.append(result["frame"])
            log.success("GENERATE-FULL",
                f"Page={page['name']!r} done ({i+1}/{plan['total_pages']})"
            )
        except Exception as exc:
            log.error("GENERATE-FULL", f"Page={page['name']!r} failed — {exc}")

    elapsed = round(time.time() - start, 1)
    log.success("GENERATE-FULL", f"Done — {len(frames)} pages in {elapsed}s")

    return JSONResponse({
        "success":        True,
        "prompt":         request.prompt,
        "project_title":  plan["project_title"],
        "model":          os.getenv("GEMINI_PLANNER_MODEL", "gemini"),
        "generationTime": f"{elapsed}s",
        "design":         {"frames": frames},
        "timestamp":      datetime.datetime.utcnow().isoformat() + "Z",
    })


@app.post("/generate-followup")
async def generate_followup(request: FollowupGenerateRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if not request.selected_node:
        raise HTTPException(status_code=400, detail="Selected node is required")

    log.info("FOLLOWUP", f"Request received — node={request.selected_node.get('nodeName') or request.selected_node.get('node_name')!r}")

    async def stream_followup():
        start = time.time()
        try:
            yield sse("status", {"message": "Preparing selected-element generation..."})
            yield sse_log(log.info("FOLLOWUP", "Building follow-up page from selection..."))

            page, project_title, followup_prompt = _build_followup_page(request)

            yield sse("plan_ready", {
                "project_title": project_title,
                "total_pages": 1,
                "pages": [{"id": page["id"], "name": page["name"]}],
            })
            yield sse_log(log.info("FOLLOWUP", f"[1/1] Queued: {page['name']!r}"))

            result = await generate_page_nodes(
                page=page,
                project_title=project_title,
                user_prompt=followup_prompt,
                layout_context=request.layout_context,
                screenshot_base64=request.screenshot_base64,
                screenshot_media_type=request.screenshot_media_type or "image/png",
            )

            frame = result["frame"]
            children = frame.get("children", [])
            chunks = _chunk_list(children, CHILDREN_PER_CHUNK)
            n_chunks = len(chunks)

            yield sse_log(log.info("FOLLOWUP", f"Streaming page={page['name']!r} — {len(children)} elements in {n_chunks} chunks"))
            yield sse("page_start", {
                "page_id": result["page_id"],
                "page_name": result["page_name"],
                "page_number": 1,
                "total_pages": 1,
                "theme": {
                    **result["theme"],
                    "feature_group": page.get("feature_group", ""),
                    "flow_group_id": page.get("flow_group_id", page.get("name", "")),
                    "flow_group": page.get("flow_group", page.get("name", "")),
                },
                "flow_meta": {
                    "feature_group": page.get("feature_group", ""),
                    "flow_group_id": page.get("flow_group_id", page.get("name", "")),
                    "flow_group": page.get("flow_group", page.get("name", "")),
                    "screen_title": page.get("screen_title", page.get("name", result["page_name"])),
                    "flow_step": page.get("flow_step"),
                    "flow_total": page.get("flow_total"),
                    "flow_group_step": page.get("flow_group_step"),
                    "flow_group_total": page.get("flow_group_total"),
                    "click_target_keywords": page.get("click_target_keywords", []),
                    "annotation_text": page.get("annotation_text", ""),
                    "annotation_target_keywords": page.get("annotation_target_keywords", []),
                    "branch_root": page.get("branch_root", ""),
                    "branch_trigger": page.get("branch_trigger", ""),
                    "branch_goal": page.get("branch_goal", ""),
                    "branch_kind": page.get("branch_kind", ""),
                    "followup_source_frame_id": page.get("followup_source_frame_id", ""),
                    "followup_source_frame_name": page.get("followup_source_frame_name", ""),
                    "navigation": page.get("navigation", {}),
                    "project_navigation": page.get("project_navigation", {}),
                },
                "total_children": len(children),
                "total_chunks": n_chunks,
                "frame_meta": {
                    "type": frame["type"],
                    "name": frame["name"],
                    "width": frame["width"],
                    "height": frame["height"],
                    "backgroundColor": frame["backgroundColor"],
                },
            })

            for ci, chunk in enumerate(chunks):
                yield f"data: {json.dumps({'type': 'page_chunk', 'payload': {'page_id': result['page_id'], 'chunk_index': ci, 'total_chunks': n_chunks, 'children': chunk}})}\n\n"

            yield sse("page_end", {
                "page_id": result["page_id"],
                "page_name": result["page_name"],
                "page_number": 1,
                "total_pages": 1,
            })

            elapsed = round(time.time() - start, 1)
            yield sse_log(log.success("FOLLOWUP", f"Complete — 1/1 pages in {elapsed}s"))
            yield sse("complete", {
                "project_title": project_title,
                "total_pages": 1,
                "pages_generated": 1,
                "generation_time": f"{elapsed}s",
                "message": f"✅ Follow-up page generated in {elapsed}s",
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            yield sse_log(log.error("FOLLOWUP", f"Fatal error — {exc}"))
            yield sse("error", {"message": str(exc)})

    return StreamingResponse(
        stream_followup(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/generate-attachment-followup")
async def generate_attachment_followup(request: AttachmentFollowupRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if not request.primary_page:
        raise HTTPException(status_code=400, detail="Primary page is required")

    primary_name = (
        request.primary_page.get("nodeName")
        or request.primary_page.get("name")
        or request.primary_page.get("parentFrameName")
        or "Primary Page"
    )
    log.info("ATTACH-FLOW", f"Request received — primary={primary_name!r}")

    async def stream_followup():
        start = time.time()
        try:
            yield sse("status", {"message": "Preparing attachment-based generation..."})
            yield sse_log(log.info("ATTACH-FLOW", "Building temporary context from selected attachments..."))

            page, project_title, followup_prompt = _build_attachment_followup_page(request)

            yield sse("plan_ready", {
                "project_title": project_title,
                "total_pages": 1,
                "pages": [{"id": page["id"], "name": page["name"]}],
            })
            yield sse_log(log.info("ATTACH-FLOW", f"[1/1] Queued: {page['name']!r}"))

            result = await generate_page_nodes(
                page=page,
                project_title=project_title,
                user_prompt=followup_prompt,
                layout_context=request.layout_context,
                screenshot_base64=request.screenshot_base64,
                screenshot_media_type=request.screenshot_media_type or "image/png",
            )

            frame = result["frame"]
            children = frame.get("children", [])
            chunks = _chunk_list(children, CHILDREN_PER_CHUNK)
            n_chunks = len(chunks)

            yield sse_log(log.info("ATTACH-FLOW", f"Streaming page={page['name']!r} — {len(children)} elements in {n_chunks} chunks"))
            yield sse("page_start", {
                "page_id": result["page_id"],
                "page_name": result["page_name"],
                "page_number": 1,
                "total_pages": 1,
                "theme": {
                    **result["theme"],
                    "feature_group": page.get("feature_group", ""),
                    "flow_group_id": page.get("flow_group_id", page.get("name", "")),
                    "flow_group": page.get("flow_group", page.get("name", "")),
                },
                "flow_meta": {
                    "feature_group": page.get("feature_group", ""),
                    "flow_group_id": page.get("flow_group_id", page.get("name", "")),
                    "flow_group": page.get("flow_group", page.get("name", "")),
                    "screen_title": page.get("screen_title", page.get("name", result["page_name"])),
                    "flow_step": page.get("flow_step"),
                    "flow_total": page.get("flow_total"),
                    "flow_group_step": page.get("flow_group_step"),
                    "flow_group_total": page.get("flow_group_total"),
                    "click_target_keywords": page.get("click_target_keywords", []),
                    "annotation_text": page.get("annotation_text", ""),
                    "annotation_target_keywords": page.get("annotation_target_keywords", []),
                    "branch_root": page.get("branch_root", ""),
                    "branch_trigger": page.get("branch_trigger", ""),
                    "branch_goal": page.get("branch_goal", ""),
                    "branch_kind": page.get("branch_kind", ""),
                    "followup_source_frame_id": page.get("followup_source_frame_id", ""),
                    "followup_source_frame_name": page.get("followup_source_frame_name", ""),
                    "navigation": page.get("navigation", {}),
                    "project_navigation": page.get("project_navigation", {}),
                },
                "total_children": len(children),
                "total_chunks": n_chunks,
                "frame_meta": {
                    "type": frame["type"],
                    "name": frame["name"],
                    "width": frame["width"],
                    "height": frame["height"],
                    "backgroundColor": frame["backgroundColor"],
                },
            })

            for ci, chunk in enumerate(chunks):
                yield f"data: {json.dumps({'type': 'page_chunk', 'payload': {'page_id': result['page_id'], 'chunk_index': ci, 'total_chunks': n_chunks, 'children': chunk}})}\n\n"

            yield sse("page_end", {
                "page_id": result["page_id"],
                "page_name": result["page_name"],
                "page_number": 1,
                "total_pages": 1,
            })

            elapsed = round(time.time() - start, 1)
            yield sse_log(log.success("ATTACH-FLOW", f"Complete — 1/1 pages in {elapsed}s"))
            yield sse("complete", {
                "project_title": project_title,
                "total_pages": 1,
                "pages_generated": 1,
                "generation_time": f"{elapsed}s",
                "message": f"✅ Attachment-based page generated in {elapsed}s",
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            yield sse_log(log.error("ATTACH-FLOW", f"Fatal error — {exc}"))
            yield sse("error", {"message": str(exc)})

    return StreamingResponse(
        stream_followup(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────
# /export-react
# ─────────────────────────────────────────────────────────────────

REACT_SYSTEM_PROMPT = """
You are a senior frontend engineer specializing in React and Tailwind CSS.

Your task is to convert a provided Figma page node tree into a single, clean, production-quality React component.

The result must represent a real responsive webpage.

════════════════════════════════════════
OUTPUT RULES
════════════════════════════════════════
1. Output ONLY the raw JSX file.
2. Do NOT output markdown.
3. Do NOT output code fences.
4. Do NOT explain anything.
5. Do NOT include comments outside JSX.
6. The component must use a default export.
7. The component name will be provided.

════════════════════════════════════════
ALLOWED IMPORTS
════════════════════════════════════════

Only these imports are allowed:

import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

No other libraries are allowed.

Do NOT import UI frameworks such as:
- Material UI
- Chakra
- Ant Design
- shadcn
- bootstrap
- lodash
- framer-motion

════════════════════════════════════════
TAILWIND CSS RULES
════════════════════════════════════════

Use Tailwind CSS utilities for ALL styling.

Do NOT use CSS files.

Do NOT use styled-components.

Inline style={{}} is ONLY allowed when Tailwind cannot represent a value such as:

- dynamic width
- dynamic height
- transform values
- background images from variables

Typical Tailwind patterns:

container:
max-w-7xl mx-auto px-6

section spacing:
py-16
py-24

grid layouts:
grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8

flex layouts:
flex items-center justify-between

text hierarchy:
text-4xl font-bold
text-xl text-gray-600

buttons:
bg-black text-white px-6 py-3 rounded-lg hover:bg-gray-800 transition

════════════════════════════════════════
LAYOUT RULES
════════════════════════════════════════

The layout must be responsive.

Use:
- flex
- grid
- container layouts

DO NOT use:
position:absolute
position:fixed
position:relative offsets for layout hacks.

Spacing must rely on:
gap
padding
margin

Pages must follow this structure when possible:

<header>
hero section
feature sections
content sections
cta section
<footer>

════════════════════════════════════════
FIGMA NODE INTERPRETATION
════════════════════════════════════════

Map common Figma nodes to React components:

TEXT → <p>, <h1>, <h2>, <span>

RECTANGLE with image fill → <img>

FRAME / GROUP → <div>

BUTTON → <button>

INPUT → <input>

ICON → inline svg or <img>

If Figma contains stacked layouts, convert to flex or grid.

════════════════════════════════════════
IMAGES — CRITICAL RULES
════════════════════════════════════════

RULE 1 — Asset images from Figma (nodes named @image or @svg):
  Use the exact path from the node JSON:
  <img src="/assets/images/filename.png" alt="description" className="..." />
  These files are bundled in the zip under public/assets/images/
  ALWAYS .png — NEVER .svg even if the node name starts with @svg
  Spaces in filenames become dashes: @svg zoho-crm 1 → /assets/images/zoho-crm-1.png

RULE 2 — Image fills from Figma (rectangles with imageHash):
  Use the proxy URL already in the node JSON — do NOT replace it.

RULE 3 — NEVER use https://placehold.co or any placeholder service.
  If no image source is available, use a colored div instead:
  <div className="w-full h-48 bg-gray-200 rounded-lg" />

RULE 4 — Always include alt text on every <img>.

════════════════════════════════════════
BUTTON & COMPONENT SIZING — CRITICAL
════════════════════════════════════════

Use EXACT pixel sizes from the Figma JSON for:
  - Button width and height
  - Input field sizes
  - Modal/dialog width and height
  - Icon sizes

Do NOT arbitrarily resize components. If Figma says a button is 120px × 40px,
render it at exactly that size (you may use Tailwind w-[120px] h-[40px]).

════════════════════════════════════════
MODAL WIRING — CRITICAL
════════════════════════════════════════

If the CONTEXT BLOCK says a button opens a modal:
  1. Import the modal component at the top of the file
  2. Declare useState for the modal: const [xyzOpen, setXyzOpen] = useState(false)
  3. Wire the button: onClick={() => setXyzOpen(true)}
  4. Render the modal at the END of the JSX return:
     {xyzOpen && <XyzModal onClose={() => setXyzOpen(false)} />}

This is MANDATORY — do not skip it even if the button seems minor.

════════════════════════════════════════
NAVIGATION
════════════════════════════════════════

Initialize navigation:

const navigate = useNavigate();

Rules:

Logo/brand image → <Link to="/"> (use actual asset src, not placeholder)

Navigation links:
<Link to="/about">About</Link>

Buttons that navigate:
onClick={() => navigate("/contact")}

Do NOT leave buttons without functionality.

════════════════════════════════════════
FORMS
════════════════════════════════════════

If a form exists:

Use controlled inputs with useState.

Example pattern:

const [email, setEmail] = useState("");

<input
  type="email"
  value={email}
  onChange={(e) => setEmail(e.target.value)}
  className="border rounded-lg px-4 py-2 w-full"
/>

════════════════════════════════════════
ACCESSIBILITY
════════════════════════════════════════

Use semantic HTML:

<header>
<nav>
<main>
<section>
<footer>

Buttons must be <button>

Clickable text must be <Link>

Images must have alt attributes.

════════════════════════════════════════
CODE QUALITY
════════════════════════════════════════

The code must be:

clean
readable
properly indented
logically structured

Group sections clearly.

Avoid unnecessary nesting.

Avoid unused variables.

Avoid inline anonymous functions when possible except for navigation.

════════════════════════════════════════
FINAL REQUIREMENT
════════════════════════════════════════

Generate ONE complete React component that renders the full page.

Write the complete JSX file now.
"""

def _make_routes(pages: list[dict]) -> list[dict]:
    routes = []
    seen: set[str] = set()
    for i, page in enumerate(pages):
        name = page["name"]
        slug = re.sub(r"[^\w\s\-]", "", name.lower())
        slug = re.sub(r"[\s_]+", "-", slug.strip()).strip("-") or f"page-{i+1}"
        slug = re.sub(r"-+", "-", slug)
        original = slug
        c = 2
        while slug in seen:
            slug = f"{original}-{c}"; c += 1
        seen.add(slug)
        pascal = "".join(w.capitalize() for w in re.split(r"[\s\-_]+", re.sub(r"[^\w\s\-]", " ", name)) if w)
        if not pascal or not pascal[0].isalpha():
            pascal = f"Page{i+1}"
        routes.append({
            "page_name":       name,
            "component_name":  pascal,
            "route_path":      "/" if i == 0 else f"/{slug}",
            "slug_path":       f"/{slug}",
            "file_name":       f"pages/{pascal}.jsx",
        })
    return routes


def _summarise_frame(frame: dict) -> dict:
    def clean(node):
        if not isinstance(node, dict):
            return node
        keep = {}
        for k, v in node.items():
            if k == "opacity" and v == 1:
                continue
            if k == "src" and v == "PLACEHOLDER":
                keep[k] = "PLACEHOLDER"
                continue
            if isinstance(v, list):
                keep[k] = [clean(c) for c in v if c is not None]
            elif isinstance(v, dict):
                keep[k] = clean(v)
            else:
                keep[k] = v
        return keep
    return clean(frame)



async def _ai_write_page(component_name: str, route_path: str,
                          all_routes: list[dict], frame: dict,
                          project_title: str,
                          nav_block: str = "") -> str:
    from coding import client as gemini_client

    route_summary = [{"page": r.get("page_name") or r.get("component",""), "route": r.get("route_path") or r.get("path","/") } for r in all_routes]
    cleaned_frame = _summarise_frame(frame)
    model         = os.getenv("GEMINI_PLANNER_MODEL", "gemini-2.0-flash")

    log.info("EXPORT", f"Writing component={component_name!r}  route={route_path!r}")

    prompt = (
        f"{REACT_SYSTEM_PROMPT}\n\n"
        f"PROJECT: {project_title}\n"
        f"COMPONENT NAME: {component_name}\n"
        f"THIS PAGE ROUTE: {route_path}\n"
        f"ALL ROUTES: {json.dumps(route_summary)}\n\n"
        + (f"{nav_block}\n\n" if nav_block else "")
        + f"FIGMA NODE TREE:\n"
        f"{json.dumps(cleaned_frame, separators=(',', ':'))}"
    )

    response = await generate_content_with_retry(
        client=gemini_client,
        model=model,
        contents=prompt,
        config={"temperature": 0.2},
        log_tag="EXPORT",
        action=f"Write React component {component_name!r}",
    )
    raw = response.text.strip()
    raw = re.sub(r"^```(?:jsx?|javascript|typescript|tsx?)?\s*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```\s*$", "", raw.strip()).strip()

    # Fix: .svg → .png in all /assets/images/ paths
    raw = re.sub(
        r'(/assets/images/[\w\-. ]+?)\.svg(["\'\\s)])',
        r'\1.png\2',
        raw
    )
    # Fix: spaces → dashes in /assets/images/ filenames
    raw = re.sub(
        r'(/assets/images/)([\w\-. ]+?)(\.png)',
        lambda m: m.group(1) + m.group(2).replace(' ', '-') + m.group(3),
        raw
    )

    if not raw.endswith(("}", "};", ");")):
        open_divs  = raw.count("<div") + raw.count("<section") + raw.count("<nav") + raw.count("<footer")
        close_divs = raw.count("</div>") + raw.count("</section>") + raw.count("</nav>") + raw.count("</footer>")
        missing    = open_divs - close_divs
        if missing > 0:
            raw += "\n" + ("</div>\n" * missing)
        if "export default function" in raw and not raw.rstrip().endswith("}"):
            raw += "\n}\n"

    log.success("EXPORT", f"Component={component_name!r} done ({len(raw)} chars)")
    return raw


class ExportPage(BaseModel):
    name:  str
    frame: dict
    nav_hint:    Optional[str] = None
    desc_hint:   Optional[str] = None
    comp_type:   Optional[str] = None
    parent_ref:  Optional[str] = None
    default_tab: Optional[str] = None
    node_id:     Optional[str] = None
    width:       Optional[int] = 1440
    height:      Optional[int] = 900

class ExportRequest(BaseModel):
    project_title: str = "My App"
    pages: list[ExportPage]


@app.post("/export-react")
async def export_react(request: ExportRequest):
    if not request.pages:
        raise HTTPException(status_code=400, detail="No pages provided")

    log.info("EXPORT", f"project={request.project_title!r}  frames={len(request.pages)}")

    async def stream_export():
        try:
            import asyncio
            from planner_react import (
                run_react_planner,
                build_page_context,
                build_component_context,
                resolve_page_frame,
                resolve_component_frames,
            )

            all_frames = [
                {
                    "name":        p.name,
                    "width":       p.width  or 1440,
                    "height":      p.height or 900,
                    "frame":       p.frame,
                    "default_tab": p.default_tab or "",
                    "comp_type":   p.comp_type   or "",
                    "nav_hint":    p.nav_hint    or "",
                    "desc_hint":   p.desc_hint   or "",
                }
                for p in request.pages
            ]

            yield sse_log(log.info("EXPORT", "Running React planner..."))
            yield sse("export_status", {
                "message": "🧠 Analysing your design — understanding pages, components and interactions..."
            })

            product_map = await run_react_planner(all_frames)

            pages_plan = product_map.get("pages", [])
            comps_plan = product_map.get("components", [])
            routing    = product_map.get("routing", [])

            yield sse_log(log.success("EXPORT",
                f"Planner done — {len(pages_plan)} page(s), {len(comps_plan)} component(s)"
            ))

            work_items = []
            sequence_n = 0

            for page in pages_plan:
                frame_data = resolve_page_frame(page, all_frames)
                if not frame_data:
                    yield sse_log(log.warn("EXPORT",
                        f"No frame found for page {page['name']!r} — skipping"
                    ))
                    continue

                sequence_n += 1
                work_items.append({
                    "kind": "page",
                    "sequence": sequence_n,
                    "component_name": page["component_name"],
                    "route_path": page["route"],
                    "file_name": page["file"],
                    "display_name": page["name"],
                    "frame": frame_data["frame"],
                    "nav_block": build_page_context(page, product_map),
                })

            for comp in comps_plan:
                resolved = resolve_component_frames(comp, all_frames)
                master   = resolved.get("master")
                tab_data = resolved.get("tabs", {})

                if not master:
                    yield sse_log(log.warn("EXPORT",
                        f"No frame found for component {comp['name']!r} — skipping"
                    ))
                    continue

                sequence_n += 1
                work_items.append({
                    "kind": "component",
                    "sequence": sequence_n,
                    "component_name": comp["component_name"],
                    "route_path": "",
                    "file_name": comp["file"],
                    "display_name": comp["name"],
                    "frame": _merge_tab_frames(
                        master_frame=master["frame"],
                        tab_data=tab_data,
                        comp=comp,
                    ),
                    "nav_block": build_component_context(comp, product_map),
                    "comp_type": comp.get("type", ""),
                    "tab_count": len(tab_data),
                })

            total = len(work_items)

            yield sse("export_plan_ready", {
                "pages":      [{"name": p["name"], "route": p["route"]} for p in pages_plan],
                "components": [{"name": c["name"], "type": c["type"]}   for c in comps_plan],
                "total":      total,
            })
            yield sse("export_start", {
                "project_title": request.project_title,
                "total_pages":   total,
            })

            files: dict[str, str] = {}
            semaphore    = asyncio.Semaphore(3)
            result_queue = asyncio.Queue()

            for item in work_items:
                if item["kind"] == "page":
                    yield sse_log(log.info(
                        "EXPORT",
                        f"[{item['sequence']}/{total}] Queued PAGE {item['component_name']!r}"
                    ))
                else:
                    extra = ""
                    if item.get("tab_count"):
                        extra = f" with {item['tab_count']} tab(s)"
                    yield sse_log(log.info(
                        "EXPORT",
                        f"[{item['sequence']}/{total}] Queued COMPONENT "
                        f"{item['component_name']!r} ({item.get('comp_type','generic')}){extra}"
                    ))

            async def _render_one(item):
                async with semaphore:
                    try:
                        jsx = await _ai_write_page(
                            component_name=item["component_name"],
                            route_path=item["route_path"],
                            all_routes=routing,
                            frame=item["frame"],
                            project_title=request.project_title,
                            nav_block=item["nav_block"],
                        )
                        await result_queue.put((item["sequence"], item, jsx, None))
                    except Exception as exc:
                        await result_queue.put((item["sequence"], item, None, exc))

            tasks = [asyncio.create_task(_render_one(item)) for item in work_items]
            pending = {}
            next_to_send = 1
            received = 0

            while received < total:
                sequence, item, jsx, exc = await result_queue.get()
                received += 1
                pending[sequence] = (item, jsx, exc)

                while next_to_send in pending:
                    item, jsx, exc = pending.pop(next_to_send)
                    if exc is not None:
                        raise exc

                    yield sse("export_page_start", {
                        "page_number":    item["sequence"],
                        "total_pages":    total,
                        "component_name": item["component_name"],
                        "page_name":      item["display_name"],
                    })

                    files[item["file_name"]] = jsx

                    yield sse_log(log.success("EXPORT",
                        f"[{item['sequence']}/{total}] {item['file_name']} ({len(jsx):,} chars)"
                    ))
                    yield sse("export_page_done", {
                        "page_number":    item["sequence"],
                        "total_pages":    total,
                        "component_name": item["component_name"],
                        "file_name":      item["file_name"],
                        "file_size":      len(jsx),
                    })
                    next_to_send += 1

            await asyncio.gather(*tasks)

            yield sse_log(log.info("EXPORT", "Writing boilerplate files..."))

            app_routes = [
                {
                    "component_name": p["component_name"],
                    "route_path":     p["route"],
                    "slug_path":      p["route"],
                    "file_name":      p["file"],
                }
                for p in pages_plan
            ]

            files["App.jsx"]            = _gen_app(app_routes)
            files["main.jsx"]           = _gen_main()
            files["index.html"]         = _gen_index_html(request.project_title)
            files["vite.config.js"]     = _gen_vite()
            files["package.json"]       = _gen_package(request.project_title)
            files["tailwind.config.js"] = _gen_tailwind()
            files["postcss.config.js"]  = _gen_postcss()
            files["index.css"]          = _gen_css()

            yield sse_log(log.success("EXPORT",
                f"Complete — {len(files)} files  "
                f"({len(pages_plan)} pages + {len(comps_plan)} components)"
            ))
            yield sse("export_complete", {
                "success":       True,
                "project_title": request.project_title,
                "files":         files,
                "file_count":    len(files),
                "summary": {
                    "pages":      len(pages_plan),
                    "components": len(comps_plan),
                    "routes":     [{"name": p["name"], "path": p["route"]} for p in pages_plan],
                },
            })

        except Exception as exc:
            import traceback; traceback.print_exc()
            yield sse_log(log.error("EXPORT", f"Failed — {exc}"))
            yield sse("export_error", {"message": str(exc)})

    return StreamingResponse(
        stream_export(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


def _merge_tab_frames(
    master_frame: dict,
    tab_data: dict,
    comp: dict,
) -> dict:
    import copy
    merged = copy.deepcopy(master_frame)

    if not tab_data:
        return merged

    tab_children = []
    for tab_name, frame_dict in tab_data.items():
        tab_frame = frame_dict.get("frame", {})
        tab_children.append({
            "type":     "group",
            "name":     f"__tab_content__/{tab_name}",
            "x":        0,
            "y":        0,
            "width":    tab_frame.get("width",  merged.get("width",  480)),
            "height":   tab_frame.get("height", merged.get("height", 400)),
            "children": tab_frame.get("children", []),
        })

    existing = merged.get("children", [])
    merged["children"] = existing + tab_children
    merged["__tab_names__"] = list(tab_data.keys())

    return merged


# ─────────────────────────────────────────────────────────────────
# BOILERPLATE GENERATORS
# ─────────────────────────────────────────────────────────────────

def _gen_app(routes: list[dict]) -> str:
    imports   = "\n".join(
        f'import {r["component_name"]} from "./{r["file_name"].replace(".jsx", "")}";'
        for r in routes
    )
    route_els = []
    for r in routes:
        route_els.append(f'        <Route path="{r["route_path"]}" element={{<{r["component_name"]} />}} />')
        if r["slug_path"] != r["route_path"]:
            route_els.append(f'        <Route path="{r["slug_path"]}" element={{<{r["component_name"]} />}} />')
    first = routes[0]["route_path"] if routes else "/"
    route_els.append(f'        <Route path="*" element={{<Navigate to="{first}" replace />}} />')
    return (
        'import React from "react";\n'
        'import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";\n'
        f'{imports}\n\n'
        'export default function App() {\n'
        '  return (\n    <BrowserRouter>\n      <Routes>\n'
        + "\n".join(route_els) +
        '\n      </Routes>\n    </BrowserRouter>\n  );\n}\n'
    )

def _gen_main() -> str:
    return (
        'import React from "react";\n'
        'import ReactDOM from "react-dom/client";\n'
        'import App from "./App";\n'
        'import "./index.css";\n\n'
        'ReactDOM.createRoot(document.getElementById("root")).render(\n'
        '  <React.StrictMode><App /></React.StrictMode>\n);\n'
    )

def _gen_index_html(title: str) -> str:
    t = re.sub(r'[<>"\'&]', "", title)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n  <head>\n'
        '    <meta charset="UTF-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f'    <title>{t}</title>\n'
        '    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />\n'
        '  </head>\n  <body>\n'
        '    <div id="root"></div>\n'
        '    <script type="module" src="/src/main.jsx"></script>\n'
        '  </body>\n</html>\n'
    )

def _gen_vite() -> str:
    return (
        'import { defineConfig } from "vite";\n'
        'import react from "@vitejs/plugin-react";\n\n'
        'export default defineConfig({\n'
        '  plugins: [react()],\n'
        '  server: { port: 3000 },\n'
        '});\n'
    )

def _gen_package(title: str) -> str:
    slug = re.sub(r"[^a-z0-9\-]", "", title.lower().replace(" ", "-")) or "my-app"
    return (
        '{\n'
        f'  "name": "{slug}",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },\n'
        '  "dependencies": {\n'
        '    "react": "^18.2.0",\n'
        '    "react-dom": "^18.2.0",\n'
        '    "react-router-dom": "^6.22.0"\n'
        '  },\n'
        '  "devDependencies": {\n'
        '    "@vitejs/plugin-react": "^4.2.1",\n'
        '    "autoprefixer": "^10.4.17",\n'
        '    "postcss": "^8.4.35",\n'
        '    "tailwindcss": "^3.4.1",\n'
        '    "vite": "^5.1.0"\n'
        '  }\n'
        '}\n'
    )

def _gen_tailwind() -> str:
    return (
        "/** @type {import('tailwindcss').Config} */\n"
        "export default {\n"
        '  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],\n'
        "  theme: {\n"
        "    extend: {\n"
        '      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },\n'
        "    },\n"
        "  },\n"
        "  plugins: [],\n"
        "};\n"
    )

def _gen_postcss() -> str:
    return "export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n"

def _gen_css() -> str:
    return (
        "@tailwind base;\n"
        "@tailwind components;\n"
        "@tailwind utilities;\n\n"
        "@layer base {\n"
        "  *, *::before, *::after { box-sizing: border-box; }\n"
        "  html { scroll-behavior: smooth; }\n"
        "  body {\n"
        "    margin: 0; padding: 0;\n"
        "    font-family: 'Inter', system-ui, -apple-system, sans-serif;\n"
        "    -webkit-font-smoothing: antialiased;\n"
        "    -moz-osx-font-smoothing: grayscale;\n"
        "    overflow-x: hidden;\n"
        "  }\n"
        "}\n\n"
        "@layer utilities {\n"
        "  .no-scrollbar::-webkit-scrollbar { display: none; }\n"
        "  .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }\n"
        "}\n\n"
        "::-webkit-scrollbar { width: 6px; height: 6px; }\n"
        "::-webkit-scrollbar-track { background: transparent; }\n"
        "::-webkit-scrollbar-thumb { background: rgba(128,128,128,0.4); border-radius: 3px; }\n"
        "::-webkit-scrollbar-thumb:hover { background: rgba(128,128,128,0.7); }\n"
        "* { scrollbar-width: thin; scrollbar-color: rgba(128,128,128,0.4) transparent; }\n"
    )


# ─────────────────────────────────────────────────────────────────
# SSE HELPERS
# ─────────────────────────────────────────────────────────────────

def sse(event_type: str, data: dict) -> str:
    line = json.dumps({"type": event_type, "payload": data})
    if len(line) > 16000:
        log.warn("SSE", f"Event '{event_type}' is {len(line):,} chars — consider chunking")
    return f"data: {line}\n\n"


def sse_log(log_entry: dict) -> str:
    line = json.dumps({"type": "log", "payload": log_entry})
    return f"data: {line}\n\n"


def _chunk_list(lst: list, size: int) -> list:
    return [lst[i:i + size] for i in range(0, len(lst), size)] if lst else [[]]


def _compact_memory_summary(memory_context: dict | None) -> str:
    memory_context = memory_context or {}
    pages = memory_context.get("pages", []) or []
    page_lines = []
    for page in pages[:18]:
        if not isinstance(page, dict):
            continue
        bits = [
            page.get("screen_title") or page.get("name") or "Screen",
            page.get("feature_group") or "",
            page.get("flow_group") or "",
        ]
        nav = page.get("navigation") or {}
        if nav.get("primary_links"):
            bits.append("nav=" + ", ".join(nav.get("primary_links", [])[:8]))
        page_lines.append(" | ".join([b for b in bits if b]))

    theme = memory_context.get("preferred_theme") or {}
    theme_line = ""
    if theme:
        theme_line = f"Preferred theme: {theme.get('name','')} | colors: {', '.join(theme.get('colors', [])[:5])}"

    nav_model = memory_context.get("navigation_model") or {}
    nav_line = ""
    if nav_model.get("primary_links"):
        nav_line = f"Shared navigation: {', '.join(nav_model.get('primary_links', [])[:10])} | layout={nav_model.get('layout', '')}"

    return "\n".join(
        [line for line in [
            theme_line,
            nav_line,
            "Existing pages:",
            *page_lines,
        ] if line]
    ).strip()


def _preferred_theme_from_layout_context(layout_context: dict | None) -> dict:
    layout_context = layout_context or {}
    raw = " ".join([
        str(layout_context.get("color_palette", "") or ""),
        str(layout_context.get("visual_style", "") or ""),
    ])
    colors = re.findall(r'#[0-9A-Fa-f]{3,8}', raw)
    deduped = []
    for color in colors:
        if color not in deduped:
            deduped.append(color)
        if len(deduped) >= 5:
            break
    if not deduped:
        return {}

    visual_style = str(layout_context.get("visual_style", "") or "").lower()
    return {
        "name": "Selected Attachment Theme",
        "colors": deduped,
        "animation": "fade" if "minimal" in visual_style else "smooth",
    }


def _preferred_theme_from_attachment_items(items: list[dict]) -> dict:
    colors = []
    for item in items:
        summary = _attachment_item_summary(item)
        for color in (summary.get("colors") or []):
            color = str(color).strip()
            if color and color not in colors:
                colors.append(color)
            if len(colors) >= 5:
                break
        if len(colors) >= 5:
            break
    if not colors:
        return {}
    return {
        "name": "Selected Attachment Theme",
        "colors": colors[:5],
        "animation": "smooth",
    }


def _attachment_item_summary(item: dict | None) -> dict:
    item = item or {}
    summary = item.get("summary") or {}
    return summary if isinstance(summary, dict) else {}


def _merge_navigation(pages: list[dict]) -> dict:
    primary_links = []
    layout = ""
    for page in pages:
        nav = (page.get("navigation") or {}) if isinstance(page, dict) else {}
        if not layout and nav.get("layout"):
            layout = nav.get("layout")
        for label in (nav.get("primary_links") or []):
            label = str(label).strip()
            if label and label not in primary_links:
                primary_links.append(label)
    return {"layout": layout, "primary_links": primary_links}


def _build_attachment_memory_context(request: AttachmentFollowupRequest) -> tuple[dict, list[str]]:
    context_pages = request.context_pages or []
    components = request.components or []
    primary = request.primary_page or {}
    all_items = list(context_pages) + ([primary] if primary else []) + list(components)

    pages = []
    page_lines = []
    seen_ids = set()

    def add_page(item: dict, is_primary: bool = False):
        if not isinstance(item, dict):
            return
        node_id = item.get("nodeId") or item.get("node_id") or ""
        if node_id and node_id in seen_ids:
            return
        if node_id:
            seen_ids.add(node_id)
        summary = _attachment_item_summary(item)
        name = (
            item.get("nodeName")
            or item.get("node_name")
            or item.get("parentFrameName")
            or "Screen"
        )
        entry = {
            "page_id": node_id or f"selected_{len(pages) + 1}",
            "frame_id": item.get("nodeId") or item.get("node_id") or "",
            "name": name,
            "screen_title": name,
            "feature_group": summary.get("feature_group") or name.split("—")[0].strip() or name,
            "flow_group": summary.get("feature_group") or name,
            "width": int(item.get("width") or 1440),
            "height": int(item.get("height") or 1080),
            "navigation": summary.get("navigation") or {},
            "description": summary.get("description") or "",
            "ui_state": "selected_reference" if not is_primary else "primary_reference",
        }
        pages.append(entry)

        line = name
        if summary.get("description"):
            line += " — " + str(summary.get("description")).strip()
        page_lines.append(line)

    for page in context_pages:
        add_page(page, False)
    add_page(primary, True)

    component_lines = []
    for component in components:
        if not isinstance(component, dict):
            continue
        summary = _attachment_item_summary(component)
        name = component.get("nodeName") or component.get("node_name") or "Component"
        line = name
        if summary.get("description"):
            line += " — " + str(summary.get("description")).strip()
        component_lines.append(line)

    return {
        "project_title": request.project_title or "Selected Project",
        "source_prompt": request.prompt.strip(),
        "preferred_theme": _preferred_theme_from_attachment_items(all_items) or _preferred_theme_from_layout_context(request.layout_context),
        "navigation_model": _merge_navigation(pages),
        "pages": pages,
        "selected_components": component_lines,
    }, component_lines


def _json_for_prompt(value: object, max_chars: int = 14000) -> str:
    raw = json.dumps(value, indent=2, ensure_ascii=True)
    return raw if len(raw) <= max_chars else (raw[:max_chars] + "\n...TRUNCATED...")


def _find_shell_nodes(tree: dict | None) -> dict:
    result = {"nav": None, "secondary_nav": None, "sidebar": None, "table_like": None}
    if not isinstance(tree, dict):
        return result

    def walk(node: dict):
        if not isinstance(node, dict):
            return
        name = str(node.get("name", "") or "").lower()
        width = int(node.get("width") or 0)
        height = int(node.get("height") or 0)
        x = int(node.get("x") or 0)
        y = int(node.get("y") or 0)
        node_type = str(node.get("type", "") or "").lower()

        if not result["nav"] and any(k in name for k in ["navbar", "topbar", "header", "global nav"]) and y <= 120:
            result["nav"] = {"name": node.get("name", ""), "x": x, "y": y, "width": width, "height": height}
        if not result["secondary_nav"] and any(k in name for k in ["secondary nav", "tab", "sub nav", "breadcrumb"]) and y <= 220:
            result["secondary_nav"] = {"name": node.get("name", ""), "x": x, "y": y, "width": width, "height": height}
        if not result["sidebar"] and any(k in name for k in ["sidebar", "side nav", "left rail", "sidenav"]):
            result["sidebar"] = {"name": node.get("name", ""), "x": x, "y": y, "width": width, "height": height}
        if not result["table_like"] and (("table" in name) or ("grid" in name) or ("row" in name and width > 300) or node_type == "line"):
            result["table_like"] = {"name": node.get("name", ""), "x": x, "y": y, "width": width, "height": height}

        for child in (node.get("children") or []):
            if all(result.values()):
                break
            walk(child)

    walk(tree)
    return result


def _build_attachment_followup_page(request: AttachmentFollowupRequest) -> tuple[dict, str, str]:
    primary = request.primary_page or {}
    context_pages = request.context_pages or []
    memory_context, component_lines = _build_attachment_memory_context(request)

    source_frame_name = (
        primary.get("nodeName")
        or primary.get("node_name")
        or primary.get("parentFrameName")
        or "Primary Screen"
    )
    source_frame_id = (
        primary.get("nodeId")
        or primary.get("node_id")
        or primary.get("parentFrameId")
        or ""
    )
    width = int(primary.get("width") or 1440)
    height = int(primary.get("height") or 1080)
    primary_summary = _attachment_item_summary(primary)
    primary_tree = primary.get("tree") if isinstance(primary, dict) else None
    project_title = memory_context.get("project_title") or "Selected Project"
    navigation = primary_summary.get("navigation") or (memory_context.get("navigation_model") or {})
    shell_nodes = _find_shell_nodes(primary_tree)

    context_lines = []
    for item in context_pages:
        if not isinstance(item, dict):
            continue
        summary = _attachment_item_summary(item)
        name = item.get("nodeName") or item.get("node_name") or "Context Page"
        line = name
        if summary.get("description"):
            line += " — " + str(summary.get("description")).strip()
        context_lines.append(line)

    page_name = f"{source_frame_name} -> Variant"
    screen_title = request.prompt.strip()[:72] or f"{source_frame_name} Variant"
    followup_desc = (
        f"Generate a new page or page variant beside '{source_frame_name}' using only the selected pages and components as context. "
        f"Do not overwrite the source page. User expectation: {request.prompt.strip()}."
    )
    if primary_summary.get("description"):
        followup_desc += " Primary page summary: " + str(primary_summary.get("description")).strip()

    user_prompt = (
        f"ATTACHMENT-BASED PAGE GENERATION TASK:\n"
        f"- Existing project title: {project_title}\n"
        f"- Primary source screen: {source_frame_name}\n"
        f"- Generate a brand-new page next to the source screen.\n"
        f"- Never replace or draw on top of the source screen.\n"
        f"- Use only the selected pages/components as context for style, shell, theme, and interaction expectations.\n"
        f"- Keep the same header, navigation treatment, spacing rhythm, UI kit, and visual language as the attached references.\n"
        f"- User request: {request.prompt.strip()}\n"
    )
    if primary_summary.get("description"):
        user_prompt += f"\nPRIMARY PAGE:\n- {source_frame_name}: {primary_summary.get('description')}\n"
    if context_lines:
        user_prompt += "\nSELECTED CONTEXT PAGES:\n" + "\n".join(f"- {line}" for line in context_lines[:10]) + "\n"
    if component_lines:
        user_prompt += "\nSELECTED COMPONENTS:\n" + "\n".join(f"- {line}" for line in component_lines[:12]) + "\n"
    if request.layout_context:
        user_prompt += (
            "\nVISUAL ANALYSIS FROM ATTACHED REFERENCES:\n"
            f"- Layout type: {request.layout_context.get('layout_type', 'unknown')}\n"
            f"- Visual style: {request.layout_context.get('visual_style', '')}\n"
            f"- Color palette: {request.layout_context.get('color_palette', '')}\n"
            f"- Sections: {', '.join(request.layout_context.get('detected_sections', []) or [])}\n"
            f"- Components: {', '.join(request.layout_context.get('detected_components', []) or [])}\n"
        )
    user_prompt += (
        "\nSTRUCTURAL ACCURACY RULES:\n"
        f"- Preserve the primary page frame size exactly at {width}x{height}.\n"
        "- Do not resize the global shell arbitrarily.\n"
        "- Keep reusable shell components such as global nav, secondary nav, side panels, and repeated buttons at the same dimensions unless the prompt explicitly asks to change them.\n"
        "- Do not flatten reusable UI into loose text. If something is a reusable button/nav/side panel/component, return it as a reusable component definition.\n"
        "- Keep table separators, row dividers, and structural lines when they exist in the selected references.\n"
        "- Use stable semantic naming suitable for export. Avoid random names.\n"
    )
    if shell_nodes.get("nav"):
        user_prompt += f"- Global nav size lock: {shell_nodes['nav']}\n"
    if shell_nodes.get("secondary_nav"):
        user_prompt += f"- Secondary nav size lock: {shell_nodes['secondary_nav']}\n"
    if shell_nodes.get("sidebar"):
        user_prompt += f"- Sidebar size lock: {shell_nodes['sidebar']}\n"
    if shell_nodes.get("table_like"):
        user_prompt += f"- Table/list structure reference: {shell_nodes['table_like']}\n"
    if primary_tree:
        user_prompt += "\nPRIMARY PAGE TREE (compact Figma JSON):\n" + _json_for_prompt(primary_tree, 18000) + "\n"
    if context_pages:
        context_trees = [
            {
                "name": item.get("nodeName", ""),
                "tree": item.get("tree"),
            }
            for item in context_pages[:4] if isinstance(item, dict) and item.get("tree")
        ]
        if context_trees:
            user_prompt += "\nCONTEXT PAGE TREES (compact Figma JSON):\n" + _json_for_prompt(context_trees, 14000) + "\n"
    if request.components:
        component_trees = [
            {
                "name": item.get("nodeName", ""),
                "tree": item.get("tree"),
            }
            for item in (request.components or [])[:8] if isinstance(item, dict) and item.get("tree")
        ]
        if component_trees:
            user_prompt += "\nREUSABLE COMPONENT TREES (compact Figma JSON):\n" + _json_for_prompt(component_trees, 14000) + "\n"

    page = {
        "id": f"attach_followup_{int(time.time() * 1000)}",
        "name": page_name,
        "screen_title": screen_title,
        "feature_group": primary_summary.get("feature_group") or source_frame_name,
        "flow_step": 1,
        "flow_total": 1,
        "flow_group": page_name,
        "flow_group_id": f"attach_followup_{re.sub(r'[^a-z0-9]+', '_', page_name.lower()).strip('_') or 'page'}",
        "flow_group_step": 1,
        "flow_group_total": 1,
        "description": followup_desc,
        "ui_state": "attachment_followup",
        "width": width,
        "height": height,
        "images": [],
        "sections": [{
            "section_name": "Main Content",
            "purpose": followup_desc,
            "components": [],
        }],
        "click_target_keywords": list((primary_summary.get("button_labels") or [])[:4]),
        "annotation_text": "Generated from selected pages/components",
        "annotation_target_keywords": list((primary_summary.get("button_labels") or [])[:4]),
        "navigation": navigation,
        "project_navigation": memory_context.get("navigation_model") or navigation,
        "journey": {
            "previous_screen": source_frame_name,
            "next_screen": "",
            "previous_page_name": source_frame_name,
            "next_page_name": "",
            "branch_root": source_frame_name,
            "branch_trigger": "selected attachments",
            "branch_goal": request.prompt.strip(),
            "branch_kind": "attachment_followup",
        },
        "branch_root": source_frame_name,
        "branch_trigger": "selected attachments",
        "branch_goal": request.prompt.strip(),
        "branch_kind": "attachment_followup",
        "memory_context": memory_context,
        "attachment_context": {
            "primary_tree": primary_tree,
            "context_trees": [item.get("tree") for item in context_pages if isinstance(item, dict) and item.get("tree")][:4],
            "component_trees": [item.get("tree") for item in (request.components or []) if isinstance(item, dict) and item.get("tree")][:8],
            "shell_nodes": shell_nodes,
        },
        "followup_source_frame_id": source_frame_id,
        "followup_source_frame_name": source_frame_name,
    }
    return page, project_title, user_prompt


def _build_followup_page(request: FollowupGenerateRequest) -> tuple[dict, str, str]:
    selected = request.selected_node or {}
    memory_context = request.memory_context or {}
    source_frame_name = (
        selected.get("parentFrameName")
        or selected.get("parent_frame_name")
        or selected.get("sourceFrameName")
        or "Current Screen"
    )
    source_frame_id = (
        selected.get("parentFrameId")
        or selected.get("parent_frame_id")
        or selected.get("sourceFrameId")
        or ""
    )
    node_name = (
        selected.get("nodeName")
        or selected.get("node_name")
        or selected.get("name")
        or "Selected Element"
    )
    node_type = (
        selected.get("nodeType")
        or selected.get("node_type")
        or selected.get("type")
        or "NODE"
    )

    source_page_meta = None
    for page in (memory_context.get("pages", []) or []):
        if not isinstance(page, dict):
            continue
        if source_frame_id and page.get("frame_id") == source_frame_id:
            source_page_meta = page
            break
        if page.get("name") == source_frame_name or page.get("screen_title") == source_frame_name:
            source_page_meta = page
            break

    width = int((source_page_meta or {}).get("width", 1440) or 1440)
    height = int((source_page_meta or {}).get("height", 1080) or 1080)
    navigation = (source_page_meta or {}).get("navigation") or (memory_context.get("navigation_model") or {})
    project_title = memory_context.get("project_title") or "Product Design"

    page_name = f"{source_frame_name} -> {node_name}"
    screen_title = request.prompt.strip()[:72] or f"{node_name} Result"
    memory_summary = _compact_memory_summary(memory_context)

    followup_desc = (
        f"Generate the next page or state that appears after the user clicks '{node_name}' on '{source_frame_name}'. "
        f"Selected node type: {node_type}. User expectation: {request.prompt.strip()}."
    )
    if source_page_meta and source_page_meta.get("description"):
        followup_desc += " Source page summary: " + str(source_page_meta.get("description")).strip()

    user_prompt = (
        (request.source_prompt or memory_context.get("source_prompt") or "").strip() + "\n\n"
        f"FOLLOW-UP GENERATION TASK:\n"
        f"- Existing project title: {project_title}\n"
        f"- Source screen: {source_frame_name}\n"
        f"- Selected interactive element: {node_name} ({node_type})\n"
        f"- Generate the next page/state that opens after this interaction.\n"
        f"- Keep the same theme, navigation labels/order/placement, shell, spacing, component system, and visual language.\n"
        f"- The new page must feel like it belongs to the already generated product.\n"
        f"- User expectation for this new page: {request.prompt.strip()}\n"
    )
    if memory_summary:
        user_prompt += f"\nPROJECT MEMORY:\n{memory_summary}\n"

    page = {
        "id": f"followup_{int(time.time() * 1000)}",
        "name": page_name,
        "screen_title": screen_title,
        "feature_group": (source_page_meta or {}).get("feature_group", source_frame_name),
        "flow_step": 1,
        "flow_total": 1,
        "flow_group": page_name,
        "flow_group_id": f"followup_{re.sub(r'[^a-z0-9]+', '_', page_name.lower()).strip('_') or 'page'}",
        "flow_group_step": 1,
        "flow_group_total": 1,
        "description": followup_desc,
        "ui_state": "followup_selection",
        "width": width,
        "height": height,
        "images": [],
        "sections": [{
            "section_name": "Main Content",
            "purpose": followup_desc,
            "components": [],
        }],
        "click_target_keywords": [str(node_name)],
        "annotation_text": f"Generated from selected element: {node_name}",
        "annotation_target_keywords": [str(node_name)],
        "navigation": navigation,
        "project_navigation": memory_context.get("navigation_model") or navigation,
        "journey": {
            "previous_screen": source_frame_name,
            "next_screen": "",
            "previous_page_name": source_frame_name,
            "next_page_name": "",
            "branch_root": source_frame_name,
            "branch_trigger": node_name,
            "branch_goal": request.prompt.strip(),
            "branch_kind": "followup_selection",
        },
        "branch_root": source_frame_name,
        "branch_trigger": node_name,
        "branch_goal": request.prompt.strip(),
        "branch_kind": "followup_selection",
        "memory_context": memory_context,
        "followup_source_frame_id": source_frame_id,
        "followup_source_frame_name": source_frame_name,
    }
    return page, project_title, user_prompt
