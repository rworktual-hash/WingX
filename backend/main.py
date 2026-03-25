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
    screenshot_base64:     Optional[str]  = None
    screenshot_media_type: Optional[str]  = "image/png"

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
    return await run_planner(request.prompt)


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
                content_context=request.content_context,
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

    response = gemini_client.models.generate_content(
        model=model,
        contents=prompt,
        config={"temperature": 0.2},
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
            total      = len(pages_plan) + len(comps_plan)

            yield sse_log(log.success("EXPORT",
                f"Planner done — {len(pages_plan)} page(s), {len(comps_plan)} component(s)"
            ))
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
            item_n = 0

            for page in pages_plan:
                item_n += 1
                component_name = page["component_name"]
                route_path     = page["route"]
                file_name      = page["file"]

                frame_data = resolve_page_frame(page, all_frames)
                if not frame_data:
                    yield sse_log(log.warn("EXPORT",
                        f"No frame found for page {page['name']!r} — skipping"
                    ))
                    continue

                context_block = build_page_context(page, product_map)

                yield sse_log(log.info("EXPORT",
                    f"[{item_n}/{total}] Generating PAGE {component_name!r}"
                ))
                yield sse("export_page_start", {
                    "page_number":    item_n,
                    "total_pages":    total,
                    "component_name": component_name,
                    "page_name":      page["name"],
                })

                jsx = await _ai_write_page(
                    component_name = component_name,
                    route_path     = route_path,
                    all_routes     = routing,
                    frame          = frame_data["frame"],
                    project_title  = request.project_title,
                    nav_block      = context_block,
                )
                files[file_name] = jsx

                yield sse_log(log.success("EXPORT",
                    f"[{item_n}/{total}] {file_name} ({len(jsx):,} chars)"
                ))
                yield sse("export_page_done", {
                    "page_number":    item_n,
                    "total_pages":    total,
                    "component_name": component_name,
                    "file_name":      file_name,
                    "file_size":      len(jsx),
                })

            for comp in comps_plan:
                item_n += 1
                component_name = comp["component_name"]
                file_name      = comp["file"]

                resolved = resolve_component_frames(comp, all_frames)
                master   = resolved.get("master")
                tab_data = resolved.get("tabs", {})

                if not master:
                    yield sse_log(log.warn("EXPORT",
                        f"No frame found for component {comp['name']!r} — skipping"
                    ))
                    continue

                merged_frame = _merge_tab_frames(
                    master_frame = master["frame"],
                    tab_data     = tab_data,
                    comp         = comp,
                )

                context_block = build_component_context(comp, product_map)

                yield sse_log(log.info("EXPORT",
                    f"[{item_n}/{total}] Generating COMPONENT "
                    f"{component_name!r} ({comp['type']})"
                    + (f" with {len(tab_data)} tab(s)" if tab_data else "")
                ))
                yield sse("export_page_start", {
                    "page_number":    item_n,
                    "total_pages":    total,
                    "component_name": component_name,
                    "page_name":      comp["name"],
                })

                jsx = await _ai_write_page(
                    component_name = component_name,
                    route_path     = "",
                    all_routes     = routing,
                    frame          = merged_frame,
                    project_title  = request.project_title,
                    nav_block      = context_block,
                )
                files[file_name] = jsx

                yield sse_log(log.success("EXPORT",
                    f"[{item_n}/{total}] {file_name} ({len(jsx):,} chars)"
                ))
                yield sse("export_page_done", {
                    "page_number":    item_n,
                    "total_pages":    total,
                    "component_name": component_name,
                    "file_name":      file_name,
                    "file_size":      len(jsx),
                })

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