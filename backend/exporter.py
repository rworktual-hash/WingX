"""
exporter.py  —  Figma JSON → React/Vite + Tailwind code

Fixes applied:
  1. Only selected frames exported (handled in code.js — selector passed in)
  2. Special characters stripped from all filenames/component names
  3. Responsive layout via CSS scale() — pixel-perfect at all screen sizes
  4. No forced Navbar — auto-detects nav links from frame content and wires them
"""

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def export_to_react(pages: list[dict], project_title: str = "My App") -> dict[str, str]:
    files: dict[str, str] = {}

    # Fix 2: sanitize project title
    app_name = _to_pascal(project_title) or "MyApp"
    app_display = project_title.strip() or "My App"

    # Build route table with safe names
    routes = []
    seen_slugs: set[str] = set()
    for i, page in enumerate(pages):
        raw_name = page.get("name", f"Page{i+1}")
        component_name = _to_safe_component(raw_name, i)
        slug = _to_safe_slug(raw_name, i, seen_slugs)
        seen_slugs.add(slug)
        route_path = "/" if i == 0 else f"/{slug}"
        routes.append({
            "page_name": raw_name,
            "component_name": component_name,
            "route_path": route_path,
            "slug_path": f"/{slug}",
            "file_name": f"pages/{component_name}.jsx",
        })

    # Fix 4: pre-scan all frames to build nav-link maps per page
    nav_map = _detect_nav_links(pages, routes)

    # Generate page components
    for i, page in enumerate(pages):
        route = routes[i]
        frame = page.get("frame", page)
        jsx = _generate_page_component(
            frame=frame,
            component_name=route["component_name"],
            all_routes=routes,
            nav_links=nav_map.get(i, []),
        )
        files[route["file_name"]] = jsx

    files["App.jsx"] = _generate_app(routes)
    files["main.jsx"] = _generate_main()
    files["index.html"] = _generate_index_html(app_display)
    files["vite.config.js"] = _generate_vite_config()
    files["package.json"] = _generate_package_json(_to_safe_slug(app_name, 0, set()))
    files["tailwind.config.js"] = _generate_tailwind_config()
    files["postcss.config.js"] = _generate_postcss_config()
    files["index.css"] = _generate_index_css()

    return files


# ─────────────────────────────────────────────────────────────────
# FIX 4: AUTO-DETECT NAV LINKS
# ─────────────────────────────────────────────────────────────────

def _detect_nav_links(pages: list[dict], routes: list[dict]) -> dict[int, list[dict]]:
    route_lookup: dict[str, str] = {}
    for r in routes:
        route_lookup[r["page_name"].lower()] = r["route_path"]
        route_lookup[r["slug_path"].lstrip("/")] = r["route_path"]
        for word in r["page_name"].lower().split():
            if len(word) > 3:
                route_lookup.setdefault(word, r["route_path"])

    result = {}
    for i, page in enumerate(pages):
        frame = page.get("frame", page)
        links: list[dict] = []
        _scan_for_nav_nodes(
            frame.get("children", []),
            route_lookup,
            links,
            routes[i]["route_path"]
        )
        result[i] = links
    return result


def _scan_for_nav_nodes(nodes: list, route_lookup: dict, links: list, current_route: str):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "").lower()
        node_name = node.get("name", "").lower()
        text = node.get("text", node.get("name", "")).strip()
        text_lower = text.lower()
        y_pos = node.get("y", 9999)

        # Only consider nodes in navbar zone (top 120px of frame)
        if y_pos < 120 and node_type in ("text", "button"):
            target = None
            if text_lower in route_lookup:
                candidate = route_lookup[text_lower]
                if candidate != current_route:
                    target = candidate
            if not target:
                for key, path in route_lookup.items():
                    if len(key) > 3 and key in node_name and path != current_route:
                        target = path
                        break
            if target:
                links.append({
                    "node_name": node.get("name", ""),
                    "text": text,
                    "target_route": target,
                })

        if node.get("children"):
            _scan_for_nav_nodes(node["children"], route_lookup, links, current_route)


# ─────────────────────────────────────────────────────────────────
# PAGE COMPONENT — Fix 3: responsive scale wrapper
# ─────────────────────────────────────────────────────────────────

def _generate_page_component(
    frame: dict,
    component_name: str,
    all_routes: list[dict],
    nav_links: list[dict],
) -> str:
    bg_color = frame.get("backgroundColor", "#111111")
    children = frame.get("children", [])
    frame_width = frame.get("width", 1440)
    frame_height = frame.get("height", 3200)

    nav_link_map = {nl["node_name"]: nl["target_route"] for nl in nav_links}
    nav_link_text_map = {nl["text"].lower(): nl["target_route"] for nl in nav_links}

    child_jsx_lines = []
    for child in children:
        jsx = _render_node(child, frame_width, frame_height, all_routes,
                           nav_link_map, nav_link_text_map, depth=3)
        if jsx:
            child_jsx_lines.append(jsx)

    children_jsx = "\n".join(child_jsx_lines)
    needs_link = "<Link" in children_jsx

    imports = ['import React, { useEffect } from "react";']
    if needs_link:
        imports.append('import { Link, useNavigate } from "react-router-dom";')
    else:
        imports.append('import { useNavigate } from "react-router-dom";')

    import_block = "\n".join(imports)
    scale_id = f"scale-{component_name.lower()}"

    return f"""{import_block}

export default function {component_name}() {{
  const navigate = useNavigate();

  useEffect(() => {{
    function applyScale() {{
      const el = document.getElementById("{scale_id}");
      if (!el) return;
      const scale = window.innerWidth / {frame_width};
      el.style.transform = `scale(${{scale}})`;
      el.style.transformOrigin = "top left";
      const container = el.parentElement;
      if (container) container.style.minHeight = ({frame_height} * scale) + "px";
    }}
    applyScale();
    window.addEventListener("resize", applyScale);
    return () => window.removeEventListener("resize", applyScale);
  }}, []);

  return (
    <div style={{{{ width: "100vw", overflow: "hidden", backgroundColor: "{bg_color}" }}}}>
      <div
        id="{scale_id}"
        style={{{{
          width: "{frame_width}px",
          minHeight: "{frame_height}px",
          backgroundColor: "{bg_color}",
          position: "relative",
        }}}}
      >
{children_jsx}
      </div>
    </div>
  );
}}
"""


# ─────────────────────────────────────────────────────────────────
# NODE RENDERERS
# ─────────────────────────────────────────────────────────────────

def _render_node(node: dict, frame_width: int, frame_height: int,
                 all_routes: list[dict], nav_link_map: dict,
                 nav_link_text_map: dict, depth: int = 3) -> str:
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type", "").lower()
    indent = "  " * depth

    if node_type == "rectangle":
        return _render_rectangle(node, indent)
    elif node_type == "text":
        return _render_text(node, indent, all_routes, nav_link_map, nav_link_text_map)
    elif node_type == "image":
        return _render_image(node, indent)
    elif node_type == "button":
        return _render_button(node, indent, all_routes, nav_link_map, nav_link_text_map)
    elif node_type == "line":
        return _render_line(node, indent)
    elif node_type in ("group", "frame", "component", "instance") or node.get("children"):
        return _render_container(node, frame_width, frame_height, all_routes,
                                 nav_link_map, nav_link_text_map, indent, depth)
    return ""


def _render_rectangle(node: dict, indent: str) -> str:
    x, y = node.get("x", 0), node.get("y", 0)
    w, h = node.get("width", 100), node.get("height", 40)
    bg = node.get("backgroundColor", node.get("fillColor", "#1A1A1A"))
    radius = node.get("cornerRadius", 0)
    opacity = node.get("opacity", 1)
    border_color = node.get("borderColor", "")
    border_width = node.get("borderWidth", 0)

    style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"), ("backgroundColor", f"'{bg}'"),
        ("borderRadius", f"'{radius}px'") if radius else None,
        ("opacity", str(opacity)) if opacity != 1 else None,
        ("border", f"'{border_width}px solid {border_color}'") if border_color and border_width else None,
    ])
    name = _safe_comment(node.get("name", "rect"))
    return f"{indent}{{/* {name} */}}\n{indent}<div style={{{{ {style} }}}} />"


def _render_line(node: dict, indent: str) -> str:
    x, y, w = node.get("x", 0), node.get("y", 0), node.get("width", 100)
    bg = node.get("backgroundColor", "#333333")
    style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", "'1px'"), ("backgroundColor", f"'{bg}'"),
    ])
    return f"{indent}<div style={{{{ {style} }}}} />"


def _render_text(node: dict, indent: str, all_routes: list[dict],
                 nav_link_map: dict, nav_link_text_map: dict) -> str:
    x, y = node.get("x", 0), node.get("y", 0)
    w = node.get("width", 200)
    raw_text = node.get("text", "")
    text = _escape_jsx_text(raw_text)
    font_size = node.get("fontSize", 16)
    font_weight = _map_font_weight(node.get("fontWeight", "regular"))
    color = node.get("color", node.get("textColor", "#FFFFFF"))
    line_height = node.get("lineHeight", 1.4)
    letter_spacing = node.get("letterSpacing", 0)
    opacity = node.get("opacity", 1)

    if font_size >= 64:   tag = "h1"
    elif font_size >= 42: tag = "h2"
    elif font_size >= 28: tag = "h3"
    elif font_size >= 20: tag = "h4"
    else:                 tag = "p"

    style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("fontSize", f"'{font_size}px'"),
        ("fontWeight", str(font_weight)), ("color", f"'{color}'"),
        ("lineHeight", str(line_height)), ("margin", "'0'"), ("padding", "'0'"),
        ("letterSpacing", f"'{letter_spacing}px'") if letter_spacing else None,
        ("opacity", str(opacity)) if opacity != 1 else None,
    ])
    name = _safe_comment(node.get("name", "text"))
    inner = _render_text_content(text)

    node_name = node.get("name", "")
    text_lower = raw_text.strip().lower()
    target = nav_link_map.get(node_name) or nav_link_text_map.get(text_lower)

    if target:
        link_style = _make_style([
            ("textDecoration", "'none'"), ("color", f"'{color}'"), ("cursor", "'pointer'"),
        ])
        return (f"{indent}{{/* {name} — nav link */}}\n"
                f'{indent}<Link to="{target}" style={{{{ {link_style} }}}}>\n'
                f"{indent}  <{tag} style={{{{ {style} }}}}>{inner}</{tag}>\n"
                f"{indent}</Link>")
    return f"{indent}{{/* {name} */}}\n{indent}<{tag} style={{{{ {style} }}}}>{inner}</{tag}>"


def _render_image(node: dict, indent: str) -> str:
    x, y = node.get("x", 0), node.get("y", 0)
    w, h = node.get("width", 400), node.get("height", 300)
    src = node.get("src", "")
    radius = node.get("borderRadius", node.get("cornerRadius", 0))
    bg = node.get("backgroundColor", "#2A2A2A")
    alt = _safe_str(node.get("name", "image"))
    opacity = node.get("opacity", 1)

    style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"), ("objectFit", "'cover'"),
        ("backgroundColor", f"'{bg}'"),
        ("borderRadius", f"'{radius}px'") if radius else None,
        ("opacity", str(opacity)) if opacity != 1 else None,
    ])
    name = _safe_comment(node.get("name", "image"))

    if src and src != "PLACEHOLDER":
        return f'{indent}{{/* {name} */}}\n{indent}<img src="{src}" alt="{alt}" style={{{{ {style} }}}} />'

    ph_style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"), ("backgroundColor", f"'{bg}'"),
        ("borderRadius", f"'{radius}px'") if radius else None,
        ("display", "'flex'"), ("alignItems", "'center'"), ("justifyContent", "'center'"),
    ])
    return (f'{indent}{{/* {name} */}}\n'
            f'{indent}<div style={{{{ {ph_style} }}}}>\n'
            f'{indent}  <span style={{{{ color: "#666", fontSize: "12px" }}}}>🖼 {alt}</span>\n'
            f'{indent}</div>')


def _render_button(node: dict, indent: str, all_routes: list[dict],
                   nav_link_map: dict, nav_link_text_map: dict) -> str:
    x, y = node.get("x", 0), node.get("y", 0)
    w, h = node.get("width", 160), node.get("height", 48)
    text = _escape_jsx_text(node.get("text", "Button"))
    bg = node.get("backgroundColor", "#4F46E5")
    text_color = node.get("textColor", "#FFFFFF")
    radius = node.get("cornerRadius", 8)
    font_size = node.get("fontSize", 16)
    font_weight = _map_font_weight(node.get("fontWeight", "semibold"))
    border_color = node.get("borderColor", "")
    border_width = node.get("borderWidth", 0)
    opacity = node.get("opacity", 1)

    style = _make_style([
        ("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'"),
        ("width", f"'{w}px'"), ("height", f"'{h}px'"), ("backgroundColor", f"'{bg}'"),
        ("color", f"'{text_color}'"), ("borderRadius", f"'{radius}px'"),
        ("fontSize", f"'{font_size}px'"), ("fontWeight", str(font_weight)),
        ("cursor", "'pointer'"), ("border", f"'{border_width}px solid {border_color or 'transparent'}'"),
        ("display", "'flex'"), ("alignItems", "'center'"), ("justifyContent", "'center'"),
        ("transition", "'opacity 0.2s'"),
        ("opacity", str(opacity)) if opacity != 1 else None,
    ])
    name = _safe_comment(node.get("name", "button"))

    node_name = node.get("name", "")
    text_lower = node.get("text", "").strip().lower()
    target = (nav_link_map.get(node_name) or nav_link_text_map.get(text_lower)
              or _find_route_for_text(node.get("text", ""), all_routes))

    hover = "onMouseEnter={e => e.currentTarget.style.opacity='0.85'} onMouseLeave={e => e.currentTarget.style.opacity='1'}"
    if target:
        return (f"{indent}{{/* {name} */}}\n"
                f'{indent}<button style={{{{ {style} }}}} onClick={{() => navigate("{target}")}} {hover}>\n'
                f"{indent}  {text}\n{indent}</button>")
    return (f"{indent}{{/* {name} */}}\n"
            f"{indent}<button style={{{{ {style} }}}} {hover}>\n"
            f"{indent}  {text}\n{indent}</button>")


def _render_container(node: dict, frame_width: int, frame_height: int,
                      all_routes: list[dict], nav_link_map: dict,
                      nav_link_text_map: dict, indent: str, depth: int) -> str:
    x, y = node.get("x", 0), node.get("y", 0)
    w, h = node.get("width"), node.get("height")
    bg = node.get("backgroundColor", "")
    radius = node.get("cornerRadius", 0)
    children = node.get("children", [])
    name = _safe_comment(node.get("name", "group"))

    parts = [("position", "'absolute'"), ("left", f"'{x}px'"), ("top", f"'{y}px'")]
    if w: parts.append(("width", f"'{w}px'"))
    if h: parts.append(("height", f"'{h}px'"))
    if bg and bg not in ("transparent", ""):
        parts.append(("backgroundColor", f"'{bg}'"))
    if radius:
        parts.append(("borderRadius", f"'{radius}px'"))

    style = _make_style(parts)
    child_lines = []
    for child in children:
        jsx = _render_node(child, frame_width, frame_height, all_routes,
                           nav_link_map, nav_link_text_map, depth + 1)
        if jsx:
            child_lines.append(jsx)

    return (f"{indent}{{/* {name} */}}\n"
            f"{indent}<div style={{{{ {style} }}}}>\n"
            + "\n".join(child_lines) + f"\n{indent}</div>")


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

    return f"""import React from "react";
import {{ BrowserRouter, Routes, Route, Navigate }} from "react-router-dom";
{imports}

export default function App() {{
  return (
    <BrowserRouter>
      <Routes>
{chr(10).join(route_els)}
      </Routes>
    </BrowserRouter>
  );
}}
"""


# ─────────────────────────────────────────────────────────────────
# BOILERPLATE FILES
# ─────────────────────────────────────────────────────────────────

def _generate_main() -> str:
    return ('import React from "react";\n'
            'import ReactDOM from "react-dom/client";\n'
            'import App from "./App";\n'
            'import "./index.css";\n\n'
            'ReactDOM.createRoot(document.getElementById("root")).render(\n'
            '  <React.StrictMode><App /></React.StrictMode>\n'
            ');\n')

def _generate_index_html(app_name: str) -> str:
    safe = re.sub(r'[<>"\'&]', "", app_name)
    return (f'<!DOCTYPE html>\n<html lang="en">\n  <head>\n'
            f'    <meta charset="UTF-8" />\n'
            f'    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
            f'    <title>{safe}</title>\n'
            f'    <link rel="preconnect" href="https://fonts.googleapis.com" />\n'
            f'    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />\n'
            f'  </head>\n  <body>\n'
            f'    <div id="root"></div>\n'
            f'    <script type="module" src="/src/main.jsx"></script>\n'
            f'  </body>\n</html>\n')

def _generate_vite_config() -> str:
    return ('import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\n'
            'export default defineConfig({\n  plugins: [react()],\n  server: { port: 3000 },\n});\n')

def _generate_package_json(slug: str) -> str:
    safe = re.sub(r"[^a-z0-9\-]", "", slug.lower()) or "my-app"
    return ('{\n'
            f'  "name": "{safe}",\n'
            '  "version": "0.1.0",\n  "private": true,\n  "type": "module",\n'
            '  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },\n'
            '  "dependencies": { "react": "^18.2.0", "react-dom": "^18.2.0", "react-router-dom": "^6.22.0" },\n'
            '  "devDependencies": {\n'
            '    "@vitejs/plugin-react": "^4.2.1", "autoprefixer": "^10.4.17",\n'
            '    "postcss": "^8.4.35", "tailwindcss": "^3.4.1", "vite": "^5.1.0"\n'
            '  }\n}\n')

def _generate_tailwind_config() -> str:
    return ('/** @type {import(\'tailwindcss\').Config} */\n'
            'export default {\n'
            '  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],\n'
            '  theme: { extend: { fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] } } },\n'
            '  plugins: [],\n};\n')

def _generate_postcss_config() -> str:
    return 'export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n'

def _generate_index_css() -> str:
    return ('@tailwind base;\n@tailwind components;\n@tailwind utilities;\n\n'
            '*, *::before, *::after { box-sizing: border-box; }\n'
            'body { margin: 0; padding: 0; font-family: "Inter", system-ui, sans-serif; '
            '-webkit-font-smoothing: antialiased; overflow-x: hidden; }\n'
            'html { scroll-behavior: smooth; }\n'
            '::-webkit-scrollbar { width: 6px; }\n'
            '::-webkit-scrollbar-track { background: #0a0a0f; }\n'
            '::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }\n')


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _to_safe_component(name: str, index: int) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    words = re.split(r"[\s\-_]+", cleaned.strip())
    pascal = "".join(w.capitalize() for w in words if w and re.match(r"\w", w[0]))
    if not pascal or not pascal[0].isalpha():
        pascal = f"Page{index + 1}"
    return pascal or f"Page{index + 1}"

def _to_safe_slug(name: str, index: int, seen: set) -> str:
    cleaned = re.sub(r"[^\w\s\-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", cleaned.strip()).strip("-")
    slug = re.sub(r"-+", "-", slug) or f"page-{index + 1}"
    original = slug
    counter = 2
    while slug in seen:
        slug = f"{original}-{counter}"
        counter += 1
    return slug

def _to_pascal(name: str) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    return "".join(w.capitalize() for w in re.split(r"[\s\-_]+", cleaned.strip()) if w)

def _map_font_weight(weight) -> int:
    if isinstance(weight, int):
        return weight
    return {"thin":100,"extralight":200,"light":300,"regular":400,"normal":400,
            "medium":500,"semibold":600,"bold":700,"extrabold":800,"black":900
            }.get(str(weight).lower(), 400)

def _safe_comment(name: str) -> str:
    return re.sub(r"[{}\*\/]", "", name).strip()

def _safe_str(name: str) -> str:
    return re.sub(r'["\'\`<>]', "", name).strip()

def _escape_jsx_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("{", "&#123;").replace("}", "&#125;").replace("<", "&lt;").replace(">", "&gt;")

def _render_text_content(text: str) -> str:
    if "\n" in text or "\\n" in text:
        return "<br />".join(text.replace("\\n", "\n").split("\n"))
    return text

def _make_style(parts: list) -> str:
    return ", ".join(f"{k}: {v}" for item in parts if item for k, v in [item])

def _find_route_for_text(text: str, routes: list[dict]) -> Optional[str]:
    tl = text.lower().strip()
    for r in routes:
        if r["page_name"].lower() in tl or tl in r["page_name"].lower():
            return r["route_path"]
    for kw in ["home", "about", "contact", "pricing", "login", "signup"]:
        if kw in tl:
            for r in routes:
                if kw in r["page_name"].lower():
                    return r["route_path"]
    return None