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
from nav_extractor import build_nav_context, build_nav_prompt_block
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
    prompt: str


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
        start = time.time()
        try:
            # ── Planning ─────────────────────────────────────────
            yield sse("status", {"message": "Planning your design..."})
            yield sse_log(log.info("GENERATE", "Starting planner..."))

            plan = await run_planner(request.prompt)

            yield sse_log(log.success("GENERATE",
                f"Plan ready: {plan['project_title']!r} ({plan['total_pages']} pages)"
            ))
            yield sse("plan_ready", {
                "project_title": plan["project_title"],
                "total_pages":   plan["total_pages"],
                "pages": [{"id": p["id"], "name": p["name"]} for p in plan["pages"]],
            })

            # ── Page generation ───────────────────────────────────
            generated = 0
            for i, page in enumerate(plan["pages"]):
                n     = i + 1
                total = plan["total_pages"]

                yield sse("status", {
                    "message":      f"Generating page {n}/{total}: {page['name']}...",
                    "current_page": n,
                    "total_pages":  total,
                    "page_name":    page["name"],
                })
                yield sse_log(log.info("GENERATE",
                    f"[{n}/{total}] Starting page={page['name']!r}"
                ))

                try:
                    result   = await generate_page_nodes(
                        page=page,
                        project_title=plan["project_title"],
                        user_prompt=request.prompt,
                    )
                    frame    = result["frame"]
                    children = frame.get("children", [])
                    chunks   = _chunk_list(children, CHILDREN_PER_CHUNK)
                    n_chunks = len(chunks)

                    yield sse_log(log.info("GENERATE",
                        f"Streaming page={page['name']!r} — {len(children)} elements in {n_chunks} chunks"
                    ))

                    yield sse("page_start", {
                        "page_id":        result["page_id"],
                        "page_name":      result["page_name"],
                        "page_number":    n,
                        "total_pages":    total,
                        "theme":          result["theme"],
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
                                "page_id":      result["page_id"],
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
                        "page_id":    result["page_id"],
                        "page_name":  result["page_name"],
                        "page_number": n,
                        "total_pages": total,
                    })

                    generated += 1
                    yield sse_log(log.success("GENERATE",
                        f"Page={page['name']!r} fully sent ({n_chunks} chunks)"
                    ))

                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    yield sse_log(log.error("GENERATE",
                        f"Page={page['name']!r} failed — {exc}"
                    ))
                    yield sse("page_error", {
                        "page_id":    page["id"],
                        "page_name":  page["name"],
                        "page_number": n,
                        "error":      str(exc),
                    })

            elapsed = round(time.time() - start, 1)
            yield sse_log(log.success("GENERATE",
                f"Complete — {generated}/{plan['total_pages']} pages in {elapsed}s"
            ))
            yield sse("complete", {
                "project_title":   plan["project_title"],
                "total_pages":     plan["total_pages"],
                "pages_generated": generated,
                "generation_time": f"{elapsed}s",
                "message": f"✅ {generated}/{plan['total_pages']} pages generated in {elapsed}s",
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
    plan   = await run_planner(request.prompt)
    frames = []

    for i, page in enumerate(plan["pages"]):
        try:
            result = await generate_page_nodes(
                page=page,
                project_title=plan["project_title"],
                user_prompt=request.prompt,
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

REACT_SYSTEM_PROMPT = """You are a world-class React + Tailwind CSS developer. You receive a Figma page node tree and write a single, complete, production-quality React component.

════════════════════════════════════════
OUTPUT RULES — READ CAREFULLY
════════════════════════════════════════
1. Output ONLY the raw .jsx file. No markdown. No code fences. No comments outside JSX. No explanation.
2. Default export only. Component name will be given.
3. Allowed imports:
     import React, { useEffect, useState } from "react";
     import { Link, useNavigate } from "react-router-dom";
   Nothing else — no external UI libraries.

════════════════════════════════════════
STYLING — TAILWIND ONLY
════════════════════════════════════════
- Use ONLY Tailwind CSS utility classes for ALL styling. Zero inline style objects.
- Exception: dynamic values that Tailwind cannot express — use style={{}} ONLY for those.
- Map Figma values to Tailwind as appropriate.

════════════════════════════════════════
LAYOUT — RESPONSIVE FLEXBOX/GRID
════════════════════════════════════════
- Do NOT use absolute positioning. Build a real responsive layout.
- Use max-w-7xl mx-auto px-6 for page width constraints
- Use py-16 or py-24 for section vertical spacing

════════════════════════════════════════
NAVIGATION — WIRE EVERYTHING
════════════════════════════════════════
- EVERY button must have an onClick={() => navigate("route")} or be a <Link to="route">
- Logo text or logo image → <Link to="/"> always

WRITE THE COMPLETE JSX FILE NOW."""


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

    route_summary = [{"page": r["page_name"], "route": r["route_path"]} for r in all_routes]
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
    name:      str
    frame:     dict
    nav_hint:  Optional[str] = None   # @nav: destinations from frame name
    desc_hint: Optional[str] = None   # @desc: layout instructions from frame name

class ExportRequest(BaseModel):
    project_title: str = "My App"
    pages: list[ExportPage]


@app.post("/export-react")
async def export_react(request: ExportRequest):
    if not request.pages:
        raise HTTPException(status_code=400, detail="No pages provided")

    log.info("EXPORT", f"project={request.project_title!r}  pages={len(request.pages)}")

    async def stream_export():
        try:
            pages  = [{"name": p.name, "frame": p.frame, "nav_hint": p.nav_hint, "desc_hint": p.desc_hint} for p in request.pages]
            routes = _make_routes(pages)
            files: dict[str, str] = {}
            total  = len(pages)

            # ── Build nav context from frame name hints ───────────
            nav_context = build_nav_context(pages, routes)
            nav_pages   = [n for n, c in nav_context.items() if c.get("destinations")]
            desc_pages  = [n for n, c in nav_context.items() if c.get("desc_hint")]
            if nav_pages:
                yield sse_log(log.success("NAV",
                    f"@nav resolved for: {', '.join(nav_pages)}"
                ))
            else:
                yield sse_log(log.info("NAV",
                    "No @nav hints found — using frame-name routing only"
                ))
            if desc_pages:
                yield sse_log(log.info("NAV",
                    f"@desc instructions found for: {', '.join(desc_pages)}"
                ))

            yield sse_log(log.info("EXPORT",
                f"Starting export — project={request.project_title!r}  pages={total}"
            ))
            yield sse("export_start", {
                "project_title": request.project_title,
                "total_pages":   total,
            })

            for i, (page, route) in enumerate(zip(pages, routes)):
                n = i + 1
                nav_block = build_nav_prompt_block(page["name"], nav_context)
                if nav_block:
                    yield sse_log(log.info("NAV",
                        f"Injecting nav hints into {route['component_name']!r}"
                    ))

                yield sse_log(log.info("EXPORT",
                    f"[{n}/{total}] Writing component={route['component_name']!r}"
                ))
                yield sse("export_page_start", {
                    "page_number":    n,
                    "total_pages":    total,
                    "component_name": route["component_name"],
                    "page_name":      page["name"],
                })

                jsx = await _ai_write_page(
                    component_name=route["component_name"],
                    route_path=route["route_path"],
                    all_routes=routes,
                    frame=page["frame"],
                    project_title=request.project_title,
                    nav_block=nav_block,
                )
                files[route["file_name"]] = jsx

                yield sse_log(log.success("EXPORT",
                    f"[{n}/{total}] {route['file_name']} done ({len(jsx):,} chars)"
                ))
                yield sse("export_page_done", {
                    "page_number":    n,
                    "total_pages":    total,
                    "component_name": route["component_name"],
                    "file_name":      route["file_name"],
                    "file_size":      len(jsx),
                })

            # Boilerplate files
            yield sse_log(log.info("EXPORT", "Writing boilerplate files..."))
            files["App.jsx"]            = _gen_app(routes)
            files["main.jsx"]           = _gen_main()
            files["index.html"]         = _gen_index_html(request.project_title)
            files["vite.config.js"]     = _gen_vite()
            files["package.json"]       = _gen_package(request.project_title)
            files["tailwind.config.js"] = _gen_tailwind()
            files["postcss.config.js"]  = _gen_postcss()
            files["index.css"]          = _gen_css()

            yield sse_log(log.success("EXPORT", f"Complete — {len(files)} files total"))
            yield sse("export_complete", {
                "success":       True,
                "project_title": request.project_title,
                "files":         files,
                "file_count":    len(files),
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
        "::-webkit-scrollbar { width: 6px; }\n"
        "::-webkit-scrollbar-track { background: #0a0a0f; }\n"
        "::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }\n"
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
    """Wrap a log entry as an SSE 'log' event so the UI can display it."""
    line = json.dumps({"type": "log", "payload": log_entry})
    return f"data: {line}\n\n"


def _chunk_list(lst: list, size: int) -> list:
    return [lst[i:i + size] for i in range(0, len(lst), size)] if lst else [[]]