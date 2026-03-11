"""
exporter.py  —  Figma JSON -> React/Vite code

DEFINITIVE FIX NOTES:
─────────────────────
PROBLEM 1 — Invisible variant states rendering:
  ROOT CAUSE was in code.js extractNode() — it was NOT checking node.visible
  before recursing into children. Inactive variant states (visible=false in Figma)
  were being exported as if visible. Fixed in code.js by checking kid.visible===false
  and skipping those children at extraction time.

  In exporter.py we also skip nodes with visible:false, opacity:0, or empty text
  as a second layer of defence.

PROBLEM 2 — Nav links not working:
  Nav links must fire on ANY node whose name or text matches a button_routes entry,
  not just nodes with type="button". Figma components representing nav items are
  often type="frame" or "instance". We wire navigate() on containers too when
  their name matches a @nav: button mapping.

PROBLEM 3 — _dedupe_variants was too aggressive:
  The 4px-bucket deduplication was accidentally collapsing legitimate sibling
  elements (e.g. sidebar items at similar y-coords). REMOVED. The fix belongs
  in code.js (visible flag), not the exporter.

PROBLEM 4 — Alignment:
  All containers already use position:absolute with exact x/y from Figma.
  The real cause of misalignment was invisible ghost nodes shifting layout.
  With code.js now filtering them at source, alignment should match Figma exactly.
"""

import re
from typing import Optional
from nav_extractor import build_nav_context, get_button_route


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def export_to_react(pages: list[dict], project_title: str = "My App") -> dict[str, str]:
    files: dict[str, str] = {}

    app_name    = _to_pascal(project_title) or "MyApp"
    app_display = project_title.strip() or "My App"

    routes = []
    seen_slugs: set[str] = set()
    for i, page in enumerate(pages):
        raw_name       = page.get("name", f"Page{i+1}")
        component_name = _to_safe_component(raw_name, i)
        slug           = _to_safe_slug(raw_name, i, seen_slugs)
        seen_slugs.add(slug)
        route_path = "/" if i == 0 else f"/{slug}"
        routes.append({
            "page_name":      raw_name,
            "component_name": component_name,
            "route_path":     route_path,
            "slug_path":      f"/{slug}",
            "file_name":      f"pages/{component_name}.jsx",
        })

    nav_context = build_nav_context(pages, routes)

    for i, page in enumerate(pages):
        route = routes[i]
        frame = page.get("frame", page)
        jsx = _generate_page_component(
            frame          = frame,
            component_name = route["component_name"],
            all_routes     = routes,
            nav_context    = nav_context,
            page_name      = route["page_name"],
        )
        files[route["file_name"]] = jsx

    files["App.jsx"]            = _generate_app(routes)
    files["main.jsx"]           = _generate_main()
    files["index.html"]         = _generate_index_html(app_display)
    files["vite.config.js"]     = _generate_vite_config()
    files["package.json"]       = _generate_package_json(_to_safe_slug(app_name, 0, set()))
    files["tailwind.config.js"] = _generate_tailwind_config()
    files["postcss.config.js"]  = _generate_postcss_config()
    files["index.css"]          = _generate_index_css()

    return files


# ─────────────────────────────────────────────────────────────────
# VISIBILITY CHECK
# Skip nodes that are invisible in Figma.
# Note: code.js now also filters visible=false at extraction time,
# but we keep this as a safety net for older exports.
# ─────────────────────────────────────────────────────────────────

def _is_visible(node: dict) -> bool:
    """Return False if node should be completely skipped."""
    if node.get("visible") is False:
        return False
    if node.get("opacity", 1) == 0:
        return False
    # Zero-size ghost nodes
    if node.get("width", 1) == 0 and node.get("height", 1) == 0:
        return False
    # Empty text nodes
    if node.get("type", "").lower() == "text":
        if not (node.get("text") or "").strip():
            return False
    return True


# ─────────────────────────────────────────────────────────────────
# NAV RESOLUTION
# Works on BOTH button nodes AND containers/frames whose name
# matches a @nav: button mapping. This handles the common Figma
# pattern where nav items are component instances, not raw buttons.
# ─────────────────────────────────────────────────────────────────

def _resolve_nav(
    node_name: str,
    node_text: str,
    page_name: str,
    nav_context: dict,
    all_routes: list[dict],
) -> Optional[str]:
    """
    Try to find a navigate() target for any node.
    Checks both node_text and node_name against @nav: button_routes.
    Returns route path or None.
    """
    # 1. Direct lookup by text
    route = get_button_route(node_text, page_name, nav_context)
    if route:
        return route
    # 2. Direct lookup by name
    route = get_button_route(node_name, page_name, nav_context)
    if route:
        return route

    # 3. Check destinations list (legacy @nav: page-list format)
    ctx          = nav_context.get(page_name, {})
    destinations = ctx.get("destinations", [])
    text_lower   = node_text.strip().lower()
    name_lower   = node_name.strip().lower()

    for dest in destinations:
        dest_lower   = dest["frame_name"].lower()
        target_words = set(w for w in re.split(r"[\s\-_]+", dest_lower) if len(w) > 2)
        text_words   = set(w for w in re.split(r"[\s\-_]+", text_lower) if len(w) > 2)
        name_words   = set(w for w in re.split(r"[\s\-_]+", name_lower) if len(w) > 2)
        if (text_lower == dest_lower
                or name_lower == dest_lower
                or (target_words and target_words & text_words)
                or (target_words and target_words & name_words)):
            return dest["route_path"]

    return None


# ─────────────────────────────────────────────────────────────────
# PAGE COMPONENT  —  locked-ratio scale
#
# ratio = viewport_width / FRAME_W
# The entire frame scales uniformly. Height scales proportionally.
# Aspect ratio is mathematically locked — same as Figma preview.
# ─────────────────────────────────────────────────────────────────

def _generate_page_component(
    frame: dict,
    component_name: str,
    all_routes: list[dict],
    nav_context: dict,
    page_name: str,
) -> str:
    bg_color     = frame.get("backgroundColor", "#ffffff")
    children     = frame.get("children", [])
    frame_width  = int(frame.get("width",  1440))
    frame_height = int(frame.get("height", 900))
    scale_id     = f"frame-{component_name.lower()}"

    visible_children = [c for c in children if isinstance(c, dict) and _is_visible(c)]

    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, page_name, depth=4)
        if jsx:
            child_lines.append(jsx)

    children_jsx = "\n".join(child_lines)
    needs_link   = "<Link" in children_jsx

    import_line = (
        'import { Link, useNavigate } from "react-router-dom";'
        if needs_link else
        'import { useNavigate } from "react-router-dom";'
    )

    L = []
    L.append('import React, { useEffect } from "react";')
    L.append(import_line)
    L.append("")
    L.append(f"export default function {component_name}() {{")
    L.append("  const navigate = useNavigate();")
    L.append(f"  const FRAME_W = {frame_width};")
    L.append(f"  const FRAME_H = {frame_height};")
    L.append("")
    L.append("  useEffect(() => {")
    L.append("    function applyScale() {")
    L.append(f'      const frame = document.getElementById("{scale_id}");')
    L.append("      if (!frame) return;")
    L.append("      const ratio = window.innerWidth / FRAME_W;")
    L.append("      frame.style.transform = `scale(${ratio})`;")
    L.append('      frame.style.transformOrigin = "top left";')
    L.append("      const wrapper = frame.parentElement;")
    L.append("      if (wrapper) wrapper.style.height = Math.round(FRAME_H * ratio) + 'px';")
    L.append("    }")
    L.append("    applyScale();")
    L.append('    window.addEventListener("resize", applyScale);')
    L.append('    return () => window.removeEventListener("resize", applyScale);')
    L.append("  }, []);")
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ width: "100vw", overflow: "hidden", position: "relative", backgroundColor: "{bg_color}" }}}}>') 
    L.append(f'      <div id="{scale_id}" style={{{{')
    L.append(f'        position: "absolute", top: 0, left: 0,')
    L.append(f'        width: "{frame_width}px", height: "{frame_height}px",')
    L.append(f'        backgroundColor: "{bg_color}", overflow: "hidden",')
    L.append(f'      }}}}>') 
    L.append(children_jsx)
    L.append("      </div>")
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# NODE DISPATCHER
# ─────────────────────────────────────────────────────────────────

def _render_node(
    node: dict,
    all_routes: list[dict],
    nav_context: dict,
    page_name: str,
    depth: int = 4,
) -> str:
    if not isinstance(node, dict):
        return ""
    if not _is_visible(node):
        return ""

    node_type = node.get("type", "").lower()
    indent    = "  " * depth

    if node_type == "rectangle":
        return _render_rectangle(node, indent)
    if node_type == "text":
        return _render_text(node, indent)
    if node_type == "image":
        return _render_image(node, indent)
    if node_type == "button":
        return _render_button(node, indent, all_routes, nav_context, page_name)
    if node_type == "line":
        return _render_line(node, indent)
    if node_type in ("ellipse", "vector"):
        return _render_vector(node, indent)
    if node_type in ("group", "frame", "component", "instance") or node.get("children"):
        return _render_container(node, all_routes, nav_context, page_name, indent, depth)
    return ""


# ─────────────────────────────────────────────────────────────────
# LEAF RENDERERS
# ─────────────────────────────────────────────────────────────────

def _render_rectangle(node: dict, indent: str) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 100);  h = node.get("height", 40)
    bg = node.get("backgroundColor", node.get("fillColor", "#E5E5E5"))
    gradient = node.get("gradient", "")
    radius   = node.get("cornerRadius", 0)
    opacity  = node.get("opacity", 1)
    bc       = node.get("borderColor", "");  bw = node.get("borderWidth", 0)

    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"),
    ]
    parts.append(("background", f"'{gradient}'") if gradient else ("backgroundColor", f"'{bg}'"))
    if radius:  parts.append(("borderRadius", f"'{radius}px'"))
    if opacity != 1:  parts.append(("opacity", str(opacity)))
    if bc and bw:  parts.append(("border", f"'{bw}px solid {bc}'"))

    style = _make_style(parts)
    name  = _safe_comment(node.get("name", "rect"))
    return f"{indent}{{/* {name} */}}\n{indent}<div style={{{{ {style} }}}} />"


def _render_line(node: dict, indent: str) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 100)
    bg = node.get("backgroundColor", node.get("color", "#CCCCCC"))
    sw = node.get("strokeWeight", node.get("height", 1))
    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{sw}px'"), ("backgroundColor", f"'{bg}'"),
    ]
    return f"{indent}<div style={{{{ {_make_style(parts)} }}}} />"


def _render_vector(node: dict, indent: str) -> str:
    if node.get("imageHash"):
        return _render_image(node, indent)
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 40);  h = node.get("height", 40)
    bg = node.get("backgroundColor", "#AAAAAA")
    radius  = 9999 if node.get("type", "").lower() == "ellipse" else node.get("cornerRadius", 0)
    opacity = node.get("opacity", 1)
    bc = node.get("borderColor", "");  bw = node.get("borderWidth", 0)

    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"), ("backgroundColor", f"'{bg}'"),
    ]
    if radius:  parts.append(("borderRadius", f"'{radius}px'"))
    if opacity != 1:  parts.append(("opacity", str(opacity)))
    if bc and bw:  parts.append(("border", f"'{bw}px solid {bc}'"))
    name = _safe_comment(node.get("name", "shape"))
    return f"{indent}{{/* {name} */}}\n{indent}<div style={{{{ {_make_style(parts)} }}}} />"


def _render_text(node: dict, indent: str) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0);  w = node.get("width", 200)
    raw_text       = node.get("text", "")
    font_size      = node.get("fontSize", 16)
    font_weight    = _map_font_weight(node.get("fontWeight", 400))
    color          = node.get("color", node.get("textColor", "#000000"))
    line_height    = node.get("lineHeight", 1.4)
    letter_spacing = node.get("letterSpacing", 0)
    opacity        = node.get("opacity", 1)
    text_align     = node.get("textAlign", node.get("textAlignHorizontal", "left")).lower()

    tag = "h1" if font_size >= 64 else "h2" if font_size >= 42 else "h3" if font_size >= 28 else "h4" if font_size >= 20 else "p"

    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("fontSize", f"'{font_size}px'"),
        ("fontWeight", str(font_weight)), ("color", f"'{color}'"),
        ("lineHeight", str(line_height)), ("margin", "'0'"),
        ("padding", "'0'"), ("boxSizing", "'border-box'"),
    ]
    if text_align in ("center", "right", "justify"):
        parts.append(("textAlign", f"'{text_align}'"))
    if letter_spacing:
        parts.append(("letterSpacing", f"'{letter_spacing}px'"))
    if opacity != 1:
        parts.append(("opacity", str(opacity)))

    style = _make_style(parts)
    name  = _safe_comment(node.get("name", "text"))
    inner = _render_text_content(_escape_jsx_text(raw_text))
    return f"{indent}{{/* {name} */}}\n{indent}<{tag} style={{{{ {style} }}}}>{inner}</{tag}>"


def _render_image(node: dict, indent: str) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 400);  h = node.get("height", 300)
    src        = node.get("src", "")
    image_hash = node.get("imageHash", "")
    radius     = node.get("borderRadius", node.get("cornerRadius", 0))
    bg         = node.get("backgroundColor", "#DDDDDD")
    alt        = _safe_str(node.get("name", "image"))
    opacity    = node.get("opacity", 1)
    name       = _safe_comment(node.get("name", "image"))

    # ── Phase 1.2: resolve FIGMA_IMAGE: hash → real proxy URL ─────
    # code.js now exports imageHash for all image-fill nodes.
    # We resolve it here to a URL the backend can serve.
    # The proxy endpoint: GET /api/image-proxy?hash=<imageHash>
    IMAGE_PROXY = "https://figma-backend-rahul.onrender.com/api/image-proxy"

    if image_hash and (not src or src in ("", "PLACEHOLDER") or src.startswith("FIGMA_IMAGE:")):
        # Use the real proxy URL — renders an actual <img> tag
        src = f"{IMAGE_PROXY}?hash={image_hash}"

    base = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"),
    ]
    if radius:      base.append(("borderRadius", f"'{radius}px'"))
    if opacity != 1: base.append(("opacity", str(opacity)))

    # Real URL (either external or resolved proxy) → <img> tag
    if src and src not in ("", "PLACEHOLDER") and not src.startswith("FIGMA_IMAGE:"):
        style = _make_style(base + [("objectFit", "'cover'"), ("backgroundColor", f"'{bg}'")])
        return (
            f'{indent}{{/* {name} */}}\n'
            f'{indent}<img\n'
            f'{indent}  src="{src}"\n'
            f'{indent}  alt="{alt}"\n'
            f'{indent}  style={{{{ {style} }}}}\n'
            f'{indent}  onError={{e => {{ e.currentTarget.style.opacity="0.3"; }}}}\n'
            f'{indent}/>'
        )

    # Hash present but proxy URL couldn't be built — render as bg-image div
    if src.startswith("FIGMA_IMAGE:"):
        proxy_url = f"{IMAGE_PROXY}?hash={image_hash}"
        style = _make_style(base + [
            ("backgroundImage",    f"'url({proxy_url})'"),
            ("backgroundSize",     "'cover'"),
            ("backgroundPosition", "'center'"),
            ("backgroundColor",    f"'{bg}'"),
        ])
        return f'{indent}{{/* {name} */}}\n{indent}<div style={{{{ {style} }}}} />'

    # No hash, no src — skip entirely (no grey box)
    return ""


def _render_button(
    node: dict, indent: str,
    all_routes: list[dict], nav_context: dict, page_name: str,
) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 160);  h = node.get("height", 48)
    text        = _escape_jsx_text(node.get("text", "Button"))
    bg          = node.get("backgroundColor", "#4F46E5")
    text_color  = node.get("textColor", "#FFFFFF")
    radius      = node.get("cornerRadius", 8)
    font_size   = node.get("fontSize", 16)
    font_weight = _map_font_weight(node.get("fontWeight", "semibold"))
    bc = node.get("borderColor", "");  bw = node.get("borderWidth", 0)
    opacity = node.get("opacity", 1)
    name = _safe_comment(node.get("name", "button"))
    border_val = f"{bw}px solid {bc}" if bc and bw else "none"

    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"),
        ("backgroundColor", f"'{bg}'"), ("color", f"'{text_color}'"),
        ("borderRadius", f"'{radius}px'"), ("fontSize", f"'{font_size}px'"),
        ("fontWeight", str(font_weight)), ("cursor", "'pointer'"),
        ("border", f"'{border_val}'"), ("display", "'flex'"),
        ("alignItems", "'center'"), ("justifyContent", "'center'"),
        ("transition", "'opacity 0.2s'"), ("boxSizing", "'border-box'"),
        ("whiteSpace", "'nowrap'"),
    ]
    if opacity != 1:  parts.append(("opacity", str(opacity)))

    style  = _make_style(parts)
    target = _resolve_nav(node.get("name", ""), node.get("text", ""),
                          page_name, nav_context, all_routes)
    hover  = "onMouseEnter={e=>e.currentTarget.style.opacity='0.85'} onMouseLeave={e=>e.currentTarget.style.opacity='1'}"

    click = f" onClick={{() => navigate(\"{target}\")}}" if target else ""
    return (
        f"{indent}{{/* {name} */}}\n"
        f"{indent}<button style={{{{ {style} }}}}{click} {hover}>\n"
        f"{indent}  {text}\n"
        f"{indent}</button>"
    )


# ─────────────────────────────────────────────────────────────────
# CONTAINER RENDERER
# Handles FRAME / GROUP / COMPONENT / INSTANCE
#
# KEY: If the container's name matches a @nav: button mapping,
# we wrap it in a clickable div with navigate(). This handles
# Figma nav items that are component instances rather than buttons.
# ─────────────────────────────────────────────────────────────────

def _render_container(
    node: dict,
    all_routes: list[dict], nav_context: dict, page_name: str,
    indent: str, depth: int,
) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 0);  h = node.get("height", 0)
    bg      = node.get("backgroundColor", "")
    radius  = node.get("cornerRadius", 0)
    opacity = node.get("opacity", 1)
    name    = _safe_comment(node.get("name", "group"))
    node_type = node.get("type", "").lower()

    is_frame_type = node_type in ("frame", "component", "instance")
    clips = is_frame_type or node.get("clipsContent", False)

    image_hash     = node.get("imageHash", "")
    has_image_fill = node.get("imageFill", False) and bool(image_hash)
    IMAGE_PROXY    = "https://figma-backend-rahul.onrender.com/api/image-proxy"

    parts = [
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"),
    ]
    if has_image_fill:
        # Resolve imageHash → actual proxy URL for CSS background-image
        proxy_url = f"{IMAGE_PROXY}?hash={image_hash}"
        parts += [
            ("backgroundImage",    f"'url({proxy_url})'"),
            ("backgroundSize",     "'cover'"),
            ("backgroundPosition", "'center'"),
        ]
    elif bg and bg not in ("transparent", ""):
        parts.append(("backgroundColor", f"'{bg}'"))
    if radius:   parts.append(("borderRadius", f"'{radius}px'"))
    if clips:    parts.append(("overflow", "'hidden'"))
    if opacity != 1: parts.append(("opacity", str(opacity)))

    style = _make_style(parts)

    # Check if this container should be a nav link
    target = _resolve_nav(node.get("name", ""), "", page_name, nav_context, all_routes)
    if target:
        parts.append(("cursor", "'pointer'"))
        style = _make_style(parts)

    # Render visible children only
    raw_kids     = node.get("children", [])
    visible_kids = [c for c in raw_kids if isinstance(c, dict) and _is_visible(c)]

    child_lines = []
    for child in visible_kids:
        jsx = _render_node(child, all_routes, nav_context, page_name, depth + 1)
        if jsx:
            child_lines.append(jsx)

    children_str = "\n".join(child_lines)

    click_attr = f' onClick={{() => navigate("{target}")}}'  if target else ""
    hover_attr = " onMouseEnter={e=>e.currentTarget.style.opacity='0.9'} onMouseLeave={e=>e.currentTarget.style.opacity='1'}" if target else ""

    return (
        f"{indent}{{/* {name} */}}\n"
        f"{indent}<div style={{{{ {style} }}}}{click_attr}{hover_attr}>\n"
        + children_str +
        f"\n{indent}</div>"
    )


# ─────────────────────────────────────────────────────────────────
# APP.JSX
# ─────────────────────────────────────────────────────────────────

def _generate_app(routes: list[dict]) -> str:
    imports = "\n".join(
        f'import {r["component_name"]} from "./{r["file_name"].replace(".jsx", "")}";'
        for r in routes
    )
    route_els = []
    for r in routes:
        route_els.append(f'        <Route path="{r["route_path"]}" element={{<{r["component_name"]} />}} />')
        slug = r.get("slug_path", "")
        if slug and slug != r["route_path"]:
            route_els.append(f'        <Route path="{slug}" element={{<{r["component_name"]} />}} />')
    first = routes[0]["route_path"] if routes else "/"
    route_els.append(f'        <Route path="*" element={{<Navigate to="{first}" replace />}} />')

    L = [
        'import React from "react";',
        'import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";',
        imports, "",
        "export default function App() {",
        "  return (", "    <BrowserRouter>", "      <Routes>",
        "\n".join(route_els),
        "      </Routes>", "    </BrowserRouter>", "  );", "}", "",
    ]
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# BOILERPLATE
# ─────────────────────────────────────────────────────────────────

def _generate_main() -> str:
    return (
        'import React from "react";\n'
        'import ReactDOM from "react-dom/client";\n'
        'import App from "./App";\n'
        'import "./index.css";\n\n'
        'ReactDOM.createRoot(document.getElementById("root")).render(\n'
        "  <React.StrictMode><App /></React.StrictMode>\n"
        ");\n"
    )

def _generate_index_html(app_name: str) -> str:
    safe = re.sub(r'[<>"\'&]', "", app_name)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n  <head>\n"
        '    <meta charset="UTF-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"    <title>{safe}</title>\n"
        '    <link rel="preconnect" href="https://fonts.googleapis.com" />\n'
        '    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />\n'
        "  </head>\n  <body>\n"
        '    <div id="root"></div>\n'
        '    <script type="module" src="/src/main.jsx"></script>\n'
        "  </body>\n</html>\n"
    )

def _generate_vite_config() -> str:
    return (
        'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\n'
        "export default defineConfig({ plugins: [react()], server: { port: 3000 } });\n"
    )

def _generate_package_json(slug: str) -> str:
    safe = re.sub(r"[^a-z0-9\-]", "", slug.lower()) or "my-app"
    return (
        '{\n'
        f'  "name": "{safe}",\n  "version": "0.1.0",\n  "private": true,\n  "type": "module",\n'
        '  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },\n'
        '  "dependencies": { "react": "^18.2.0", "react-dom": "^18.2.0", "react-router-dom": "^6.22.0" },\n'
        '  "devDependencies": {\n'
        '    "@vitejs/plugin-react": "^4.2.1",\n'
        '    "autoprefixer": "^10.4.17",\n    "postcss": "^8.4.35",\n'
        '    "tailwindcss": "^3.4.1",\n    "vite": "^5.1.0"\n  }\n}\n'
    )

def _generate_tailwind_config() -> str:
    return (
        "/** @type {import('tailwindcss').Config} */\n"
        "export default {\n"
        '  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],\n'
        '  theme: { extend: { fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] } } },\n'
        "  plugins: [],\n};\n"
    )

def _generate_postcss_config() -> str:
    return "export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n"

def _generate_index_css() -> str:
    return (
        "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n\n"
        "*, *::before, *::after { box-sizing: border-box; }\n"
        "body {\n  margin: 0;\n  padding: 0;\n"
        '  font-family: "Inter", system-ui, sans-serif;\n'
        "  -webkit-font-smoothing: antialiased;\n  overflow-x: hidden;\n}\n"
        "html { scroll-behavior: smooth; }\n"
        "::-webkit-scrollbar { width: 6px; }\n"
        "::-webkit-scrollbar-track { background: #f1f1f1; }\n"
        "::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }\n"
    )


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _to_safe_component(name: str, index: int) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    pascal  = "".join(w.capitalize() for w in re.split(r"[\s\-_]+", cleaned.strip()) if w and re.match(r"\w", w[0]))
    return pascal if pascal and pascal[0].isalpha() else f"Page{index + 1}"

def _to_safe_slug(name: str, index: int, seen: set) -> str:
    slug = re.sub(r"-+", "-", re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s\-]", "", name.lower()).strip()).strip("-")) or f"page-{index + 1}"
    orig, counter = slug, 2
    while slug in seen:
        slug = f"{orig}-{counter}"; counter += 1
    return slug

def _to_pascal(name: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[\s\-_]+", re.sub(r"[^\w\s\-]", " ", name).strip()) if w)

def _map_font_weight(weight) -> int:
    if isinstance(weight, int): return weight
    return {"thin":100,"extralight":200,"light":300,"regular":400,"normal":400,
            "medium":500,"semibold":600,"bold":700,"extrabold":800,"black":900}.get(str(weight).lower(), 400)

def _safe_comment(name: str) -> str:
    return re.sub(r"[{}\*\/]", "", name).strip()

def _safe_str(name: str) -> str:
    return re.sub(r'["\'`<>]', "", name).strip()

def _escape_jsx_text(text: str) -> str:
    return text.replace("&","&amp;").replace("{","&#123;").replace("}","&#125;").replace("<","&lt;").replace(">","&gt;")

def _render_text_content(text: str) -> str:
    if "\n" in text or "\\n" in text:
        return "<br />".join(text.replace("\\n", "\n").split("\n"))
    return text

def _make_style(parts: list) -> str:
    return ", ".join(f"{k}: {v}" for k, v in parts)