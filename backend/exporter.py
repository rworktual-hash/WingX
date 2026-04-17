"""
exporter.py  —  Figma JSON → React/Vite code

Supports the Universal Component Type System:
  - Pages    → pages/<Name>.jsx        (React Router routes)
  - Overlays → components/<Name>.jsx   (modal, drawer, popover, toast, etc.)
  - Inline   → components/<Name>.jsx   (tabs, table, form, sidebar, etc.)
  - Tabs     → rendered inside their parent component, not standalone

Fix notes:
  PROBLEM 1 — Invisible variant states: fixed in code.js (visible=false check)
  PROBLEM 2 — Nav links: wired on containers whose name matches @nav: button_routes
  PROBLEM 3 — dedup removed (was too aggressive)
  PROBLEM 4 — Alignment: absolute x/y from Figma, scale via useEffect
  PROBLEM 5 — Colour: never apply a default backgroundColor when fill is absent/transparent
  PROBLEM 6 — Asset images: rendered as <img src="/assets/images/name.png" />
"""

import re
from typing import Optional
from nav_extractor import build_nav_context, get_button_route
from component_classifier import (
    classify, is_overlay, is_inline, is_page, is_tab,
    get_render_strategy, OVERLAY_TYPES, INLINE_TYPES,
    _to_pascal,
)


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def export_to_react(pages: list[dict], project_title: str = "My App") -> dict[str, str]:
    files: dict[str, str] = {}

    app_display = project_title.strip() or "My App"

    # ── Classify all frames ───────────────────────────────────────
    classified  = classify(pages)
    page_frames = classified["pages"]
    comp_frames = classified["components"]
    routes      = classified["routes"]
    comp_map    = classified["component_map"]

    # ── Build nav context (only from page frames) ─────────────────
    nav_context = build_nav_context(page_frames, routes)

    # ── Generate page files ───────────────────────────────────────
    for page in page_frames:
        jsx = _generate_page_component(
            frame          = page["frame"],
            component_name = page["component_name"],
            all_routes     = routes,
            nav_context    = nav_context,
            page_name      = page["name"],
            comp_map       = comp_map,
        )
        files[page["file_name"]] = jsx

    # ── Generate component files ──────────────────────────────────
    for comp in comp_frames:
        strategy = get_render_strategy(comp["comp_type"])
        jsx = _generate_component(
            comp       = comp,
            strategy   = strategy,
            all_routes = routes,
            nav_context= nav_context,
            comp_map   = comp_map,
        )
        files[comp["file_name"]] = jsx

    # ── Boilerplate files ─────────────────────────────────────────
    files["App.jsx"]            = _generate_app(routes)
    files["main.jsx"]           = _generate_main()
    files["index.html"]         = _generate_index_html(app_display)
    files["vite.config.js"]     = _generate_vite_config()
    files["package.json"]       = _generate_package_json(_to_safe_slug(app_display, 0, set()))
    files["tailwind.config.js"] = _generate_tailwind_config()
    files["postcss.config.js"]  = _generate_postcss_config()
    files["index.css"]          = _generate_index_css()

    return files


# ─────────────────────────────────────────────────────────────────
# VISIBILITY CHECK
# ─────────────────────────────────────────────────────────────────

def _is_visible(node: dict) -> bool:
    if node.get("visible") is False:                     return False
    if node.get("opacity", 1) == 0:                      return False
    if node.get("width", 1) == 0 and node.get("height", 1) == 0: return False
    if node.get("type", "").lower() == "text":
        if not (node.get("text") or "").strip():         return False
    return True


# ─────────────────────────────────────────────────────────────────
# NAV RESOLUTION
# ─────────────────────────────────────────────────────────────────

def _resolve_nav(
    node_name: str, node_text: str, page_name: str,
    nav_context: dict, all_routes: list[dict],
) -> Optional[str]:
    route = get_button_route(node_text, page_name, nav_context)
    if route: return route
    route = get_button_route(node_name, page_name, nav_context)
    if route: return route

    ctx          = nav_context.get(page_name, {})
    destinations = ctx.get("destinations", [])
    text_lower   = node_text.strip().lower()
    name_lower   = node_name.strip().lower()

    for dest in destinations:
        dest_lower   = dest["frame_name"].lower()
        target_words = set(w for w in re.split(r"[\s\-_]+", dest_lower) if len(w) > 2)
        text_words   = set(w for w in re.split(r"[\s\-_]+", text_lower) if len(w) > 2)
        name_words   = set(w for w in re.split(r"[\s\-_]+", name_lower) if len(w) > 2)
        if (text_lower == dest_lower or name_lower == dest_lower
                or (target_words and target_words & text_words)
                or (target_words and target_words & name_words)):
            return dest["route_path"]

    return None


# ─────────────────────────────────────────────────────────────────
# PAGE COMPONENT GENERATOR
# ─────────────────────────────────────────────────────────────────

def _generate_page_component(
    frame: dict, component_name: str,
    all_routes: list[dict], nav_context: dict,
    page_name: str, comp_map: dict,
) -> str:
    bg_color     = frame.get("backgroundColor", "#ffffff")
    children     = frame.get("children", [])
    frame_width  = int(frame.get("width",  1440))
    frame_height = int(frame.get("height", 900))

    visible_children = [c for c in children if isinstance(c, dict) and _is_visible(c)]

    # Collect which components are referenced in this page
    used_components: set[str] = set()

    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, page_name,
                           depth=4, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    children_jsx = "\n".join(child_lines)
    needs_link   = "<Link" in children_jsx

    # ── Bug 3 fix: collect all minimize state keys from rendered JSX ──
    # _render_container emits setMinimizedXxx(…) — extract every unique key
    minimize_keys = sorted(set(re.findall(r"setMinimized(\w+)\(", children_jsx)))

    # Build component imports
    comp_imports = "\n".join(
        f'import {cn} from "../components/{cn}";'
        for cn in sorted(used_components)
    )

    import_line = (
        'import { Link, useNavigate } from "react-router-dom";'
        if needs_link else
        'import { useNavigate } from "react-router-dom";'
    )

    scale_id = f"__page_frame_{component_name.lower()}__"

    L = []
    L.append('import React, { useEffect, useState } from "react";')
    L.append(import_line)
    if comp_imports:
        L.append(comp_imports)
    L.append("")
    L.append(f"export default function {component_name}() {{")
    L.append("  const navigate = useNavigate();")
    L.append("")
    # State for toggling overlay components
    for cn in sorted(used_components):
        state_var = _to_camel(cn) + "Open"
        L.append(f"  const [{state_var}, set{cn}Open] = useState(false);")
    # State for minimize toggles
    for key in minimize_keys:
        L.append(f"  const [minimized{key}, setMinimized{key}] = useState(false);")
    L.append("")
    L.append(f"  const FRAME_W = {frame_width};")
    L.append(f"  const FRAME_H = {frame_height};")
    L.append("")
    L.append("  useEffect(() => {")
    L.append("    function applyScale() {")
    L.append(f'      const inner = document.getElementById("{scale_id}");')
    L.append("      if (!inner) return;")
    L.append("      const s = window.innerWidth / FRAME_W;")
    L.append("      const scale = s;")
    L.append("      const scaledW = Math.round(FRAME_W * scale);")
    L.append("      const scaledH = Math.round(FRAME_H * scale);")  
    L.append("      inner.style.transform = 'scale(' + scale + ')';")
    L.append("      inner.style.transformOrigin = 'top left';")
    L.append("      if (inner.parentElement) {")
    L.append("        inner.parentElement.style.height = scaledH + 'px';")
    L.append("        inner.parentElement.style.width = scaledW + 'px';")
    L.append("        inner.parentElement.style.overflow= 'hidden';")
    L.append("      }")
    L.append("    }")
    L.append("    applyScale();")
    L.append('    window.addEventListener("resize", applyScale);')
    L.append('    return () => window.removeEventListener("resize", applyScale);')
    L.append("  }, []);")
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ width: \"100%\", minHeight: \"100vh\", overflowX: \"hidden\", backgroundColor: \"{bg_color}\" }}}}>')    
    L.append(f'      <div id="{scale_id}" style={{{{')
    L.append(f'        position: "relative",')
    L.append(f'        width: "{frame_width}px",')
    L.append(f'        height: "{frame_height}px",')
    L.append(f'        backgroundColor: "{bg_color}",')
    L.append(f'        overflow: "hidden",')
    L.append(f'      }}}}>') 
    L.append(children_jsx)
    # Render overlay components at end of page
    for cn in sorted(used_components):
        state_var = _to_camel(cn) + "Open"
        L.append(f'        {{{state_var} && <{cn} onClose={{() => set{cn}Open(false)}} />}}')
    L.append("      </div>")
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# COMPONENT FILE GENERATOR
# Dispatches to the correct renderer based on strategy
# ─────────────────────────────────────────────────────────────────

def _generate_component(
    comp: dict, strategy: str,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    if strategy.startswith("overlay_"):
        return _generate_overlay_component(comp, strategy, all_routes, nav_context, comp_map)
    if strategy == "inline_tabs":
        return _generate_tabs_component(comp, all_routes, nav_context, comp_map)
    if strategy == "inline_table":
        return _generate_table_component(comp, all_routes, nav_context, comp_map)
    if strategy == "inline_form":
        return _generate_form_component(comp, all_routes, nav_context, comp_map)
    # Default inline block
    return _generate_inline_component(comp, all_routes, nav_context, comp_map)


# ── OVERLAY COMPONENT (modal, drawer, popover, toast, etc.) ──────

def _generate_overlay_component(
    comp: dict, strategy: str,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    cn           = comp["component_name"]
    frame        = comp.get("frame", {})
    bg           = frame.get("backgroundColor", "#ffffff")
    w            = comp.get("width",  480)
    h            = comp.get("height", 320)
    comp_type    = comp["comp_type"]
    tabs         = comp.get("tabs", [])
    default_tab  = comp.get("default_tab", tabs[0]["name"] if tabs else None)

    visible_children = [c for c in frame.get("children", [])
                        if isinstance(c, dict) and _is_visible(c)]
    used_components: set[str] = set()

    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, comp["name"],
                           depth=6, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    children_jsx = "\n".join(child_lines)

    # Position strategy per overlay type
    if comp_type == "drawer":
        overlay_style = (
            'position: "fixed", top: 0, right: 0, '
            f'width: "{w}px", height: "100vh", '
            f'backgroundColor: "{bg}", zIndex: 1000, '
            'boxShadow: "-8px 0 32px rgba(0,0,0,0.3)", '
            'overflowY: "auto"'
        )
        backdrop_style = (
            'position: "fixed", inset: 0, '
            'backgroundColor: "rgba(0,0,0,0.5)", zIndex: 999'
        )
    elif comp_type in ("toast",):
        overlay_style = (
            'position: "fixed", bottom: "24px", right: "24px", '
            f'width: "{w}px", '
            f'backgroundColor: "{bg}", zIndex: 1000, '
            'borderRadius: "12px", '
            'boxShadow: "0 8px 32px rgba(0,0,0,0.2)"'
        )
        backdrop_style = None
    elif comp_type in ("popover", "tooltip"):
        overlay_style = (
            'position: "absolute", top: "100%", left: 0, '
            f'width: "{w}px", '
            f'backgroundColor: "{bg}", zIndex: 500, '
            'borderRadius: "8px", '
            'boxShadow: "0 4px 16px rgba(0,0,0,0.15)"'
        )
        backdrop_style = None
    else:
        # modal / dialog / bottomsheet
        overlay_style = (
            'position: "relative", margin: "auto", '
            f'width: "{w}px", maxWidth: "90vw", '
            f'backgroundColor: "{bg}", zIndex: 1001, '
            'borderRadius: "16px", overflow: "hidden", '
            'boxShadow: "0 24px 64px rgba(0,0,0,0.4)"'
        )
        backdrop_style = (
            'position: "fixed", inset: 0, '
            'backgroundColor: "rgba(0,0,0,0.6)", zIndex: 1000, '
            'display: "flex", alignItems: "center", justifyContent: "center"'
        )

    # Build tabs section if this overlay has tabs
    tabs_jsx = ""
    if tabs:
        tabs_jsx = _render_tabs_section(tabs, default_tab, depth=6)

    L = []
    L.append('import React, { useState } from "react";')
    L.append('import { useNavigate } from "react-router-dom";')
    L.append("")
    L.append(f"export default function {cn}({{ onClose }}) {{")
    L.append("  const navigate = useNavigate();")
    if tabs:
        first_tab = default_tab or (tabs[0]["name"] if tabs else "")
        L.append(f'  const [activeTab, setActiveTab] = useState("{first_tab}");')
    L.append("")
    L.append("  return (")

    if backdrop_style:
        L.append(f'    <div style={{{{ {backdrop_style} }}}} onClick={{onClose}}>')
        L.append(f'      <div style={{{{ {overlay_style} }}}} onClick={{e => e.stopPropagation()}}>')
        if tabs_jsx:
            L.append(tabs_jsx)
        L.append(f'        <div style={{{{ position: "relative", width: "{w}px", height: "{h}px" }}}}>') 
        L.append(children_jsx)
        L.append("        </div>")
        L.append("      </div>")
        L.append("    </div>")
    else:
        L.append(f'    <div style={{{{ {overlay_style} }}}}>') 
        if tabs_jsx:
            L.append(tabs_jsx)
        L.append(children_jsx)
        L.append("    </div>")

    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ── TABS COMPONENT ───────────────────────────────────────────────

def _generate_tabs_component(
    comp: dict,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    cn          = comp["component_name"]
    frame       = comp.get("frame", {})
    bg          = frame.get("backgroundColor", "#ffffff")
    w           = comp.get("width",  600)
    h           = comp.get("height", 400)
    tabs        = comp.get("tabs", [])
    default_tab = comp.get("default_tab", tabs[0]["name"] if tabs else "Tab 1")

    visible_children = [c for c in frame.get("children", [])
                        if isinstance(c, dict) and _is_visible(c)]
    used_components: set[str] = set()

    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, comp["name"],
                           depth=4, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    tabs_jsx = _render_tabs_section(tabs, default_tab, depth=4) if tabs else ""

    L = []
    L.append('import React, { useState } from "react";')
    L.append("")
    L.append(f"export default function {cn}() {{")
    first_tab = default_tab or (tabs[0]["name"] if tabs else "")
    L.append(f'  const [activeTab, setActiveTab] = useState("{first_tab}");')
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ position: "relative", width: "{w}px", height: "{h}px", backgroundColor: "{bg}" }}}}>')
    if tabs_jsx:
        L.append(tabs_jsx)
    L.append("\n".join(child_lines))
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ── TABLE COMPONENT ──────────────────────────────────────────────

def _generate_table_component(
    comp: dict,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    cn    = comp["component_name"]
    frame = comp.get("frame", {})
    bg    = frame.get("backgroundColor", "#ffffff")
    w     = comp.get("width",  800)
    h     = comp.get("height", 400)

    visible_children = [c for c in frame.get("children", [])
                        if isinstance(c, dict) and _is_visible(c)]
    used_components: set[str] = set()
    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, comp["name"],
                           depth=4, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    L = []
    L.append('import React, { useState } from "react";')
    L.append("")
    L.append(f"export default function {cn}() {{")
    L.append('  const [sortCol, setSortCol] = useState(null);')
    L.append('  const [sortDir, setSortDir] = useState("asc");')
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ position: "relative", width: "{w}px", minHeight: "{h}px", backgroundColor: "{bg}", overflowX: "auto" }}}}>')
    L.append("\n".join(child_lines))
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ── FORM COMPONENT ───────────────────────────────────────────────

def _generate_form_component(
    comp: dict,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    cn    = comp["component_name"]
    frame = comp.get("frame", {})
    bg    = frame.get("backgroundColor", "#ffffff")
    w     = comp.get("width",  480)
    h     = comp.get("height", 400)

    visible_children = [c for c in frame.get("children", [])
                        if isinstance(c, dict) and _is_visible(c)]
    used_components: set[str] = set()
    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, comp["name"],
                           depth=4, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    L = []
    L.append('import React, { useState } from "react";')
    L.append('import { useNavigate } from "react-router-dom";')
    L.append("")
    L.append(f"export default function {cn}({{ onSubmit, onCancel }}) {{")
    L.append("  const navigate = useNavigate();")
    L.append('  const [formData, setFormData] = useState({});')
    L.append("")
    L.append("  const handleChange = (field, value) => {")
    L.append("    setFormData(prev => ({ ...prev, [field]: value }));")
    L.append("  };")
    L.append("")
    L.append("  const handleSubmit = () => {")
    L.append("    if (onSubmit) onSubmit(formData);")
    L.append("  };")
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ position: "relative", width: "{w}px", minHeight: "{h}px", backgroundColor: "{bg}" }}}}>')
    L.append("\n".join(child_lines))
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ── GENERIC INLINE COMPONENT ─────────────────────────────────────

def _generate_inline_component(
    comp: dict,
    all_routes: list[dict], nav_context: dict, comp_map: dict,
) -> str:
    cn    = comp["component_name"]
    frame = comp.get("frame", {})
    bg    = frame.get("backgroundColor", "")
    w     = comp.get("width",  400)
    h     = comp.get("height", 200)

    visible_children = [c for c in frame.get("children", [])
                        if isinstance(c, dict) and _is_visible(c)]
    used_components: set[str] = set()
    child_lines = []
    for child in visible_children:
        jsx = _render_node(child, all_routes, nav_context, comp["name"],
                           depth=4, comp_map=comp_map,
                           used_components=used_components, scale="1")
        if jsx:
            child_lines.append(jsx)

    children_jsx = "\n".join(child_lines)
    needs_link   = "<Link" in children_jsx
    import_line  = (
        'import { Link, useNavigate } from "react-router-dom";'
        if needs_link else
        'import { useNavigate } from "react-router-dom";'
    )

    bg_style = f'backgroundColor: "{bg}", ' if bg and bg not in ("transparent", "") else ""

    L = []
    L.append('import React from "react";')
    L.append(import_line)
    L.append("")
    L.append(f"export default function {cn}({{ onClose, ...props }}) {{")
    L.append("  const navigate = useNavigate();")
    L.append("")
    L.append("  return (")
    L.append(f'    <div style={{{{ position: "relative", width: "{w}px", height: "{h}px", {bg_style}overflow: "hidden" }}}}>')
    L.append(children_jsx)
    L.append("    </div>")
    L.append("  );")
    L.append("}")
    L.append("")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# TABS SECTION RENDERER
# Generates the tab bar + conditional content panels
# ─────────────────────────────────────────────────────────────────

def _render_tabs_section(tabs: list[dict], default_tab: Optional[str], depth: int) -> str:
    ind  = "  " * depth
    ind2 = "  " * (depth + 1)
    ind3 = "  " * (depth + 2)

    if not tabs:
        return ""

    tab_names = [t["name"] for t in tabs]

    lines = []
    lines.append(f"{ind}{{/* Tab Bar */}}")
    lines.append(f"{ind}<div style={{{{ display: 'flex', borderBottom: '1px solid #e5e7eb' }}}}>")
    for tab in tabs:
        tn = tab["name"]
        lines.append(f"{ind2}<button")
        lines.append(f"{ind3}key=\"{tn}\"")
        lines.append(f"{ind3}onClick={{() => setActiveTab(\"{tn}\")}}")
        lines.append(f"{ind3}style={{{{")
        lines.append(f"{ind3}  padding: '10px 20px', border: 'none', cursor: 'pointer',")
        lines.append(f"{ind3}  background: 'none',")
        lines.append(f"{ind3}  borderBottom: activeTab === \"{tn}\" ? '2px solid #6366F1' : '2px solid transparent',")
        lines.append(f"{ind3}  color: activeTab === \"{tn}\" ? '#6366F1' : '#6b7280',")
        lines.append(f"{ind3}  fontWeight: activeTab === \"{tn}\" ? '600' : '400',")
        lines.append(f"{ind3}}}}}>{tn}</button>")
    lines.append(f"{ind}</div>")

    # Tab content panels
    lines.append(f"{ind}{{/* Tab Content */}}")
    for tab in tabs:
        tn        = tab["name"]
        tab_frame = tab.get("frame") or {}
        tab_bg    = (tab_frame.get("frame") or {}).get("backgroundColor", "")
        lines.append(f"{ind}{{activeTab === \"{tn}\" && (")
        lines.append(f"{ind2}<div style={{{{ position: 'relative' }}}}>")
        lines.append(f"{ind3}{{/* {tn} content */}}")
        lines.append(f"{ind2}</div>")
        lines.append(f"{ind})}}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# NODE DISPATCHER
# ─────────────────────────────────────────────────────────────────

def _render_node(
    node: dict,
    all_routes: list[dict], nav_context: dict,
    page_name: str, depth: int = 4,
    comp_map: dict = None,
    used_components: set = None,
    scale: str = "scale",
    parent_auto_layout: bool = False,
) -> str:
    if not isinstance(node, dict): return ""
    if not _is_visible(node):      return ""

    comp_map        = comp_map or {}
    used_components = used_components if used_components is not None else set()

    node_type = node.get("type", "").lower()
    indent    = "  " * depth

    # ── Asset image node ─────────────────────────────────────────
    if node_type == "asset_image":
        return _render_asset_image(node, indent, scale)

    if node_type == "rectangle":
        return _render_rectangle(node, indent, scale, parent_auto_layout)
    if node_type == "text":
        return _render_text(node, indent, scale, all_routes, nav_context, page_name, parent_auto_layout)
    if node_type == "image":
        return _render_image(node, indent, scale, parent_auto_layout)
    if node_type == "button":
        return _render_button(node, indent, all_routes, nav_context, page_name, scale, parent_auto_layout)
    if node_type == "line":
        return _render_line(node, indent, scale, parent_auto_layout)
    if node_type in ("ellipse", "vector"):
        return _render_vector(node, indent, scale, parent_auto_layout)
    if node_type == "scroller" or node.get("comp_type") == "scroller":
        x = node.get("x", 0); y = node.get("y", 0)
        w = node.get("width", 200); h = node.get("height", 20)
        return (
            f'{indent}<input type="range" style={{{{ '
            f'position: "absolute", '
            f'left: `calc({x}px * ${{{scale}}})`, '
            f'top: `calc({y}px * ${{{scale}}})`, '
            f'width: `calc({w}px * ${{{scale}}})`, '
            f'height: `calc({h}px * ${{{scale}}})`, '
            f'accentColor: "#6366F1" }}}} />'
        )
    if node_type in ("group", "frame", "component", "instance") or node.get("children"):
        return _render_container(node, all_routes, nav_context, page_name,
                                 indent, depth, comp_map, used_components, scale, parent_auto_layout)
    return ""


# ─────────────────────────────────────────────────────────────────
# LEAF RENDERERS
# ─────────────────────────────────────────────────────────────────

def _render_asset_image(node: dict, indent: str, scale: str = "scale") -> str:
    """Render an @svg or @image node as <img> — screenshot from Figma, stored in public/assets/images/"""
    x    = node.get("x", 0);  y = node.get("y", 0)
    w    = node.get("width", 100);  h = node.get("height", 100)

    # ── Bug 2 fix: strip Figma auto-ID suffix from label ─────────
    # code.js may produce assetLabel = "logo/frame-19923" or "logo"
    # We only want the part BEFORE the first "/" — that is the canonical label.
    raw_label = node.get("assetLabel", node.get("name", "asset"))
    label     = raw_label.split("/")[0].strip()          # "logo/frame-19923" → "logo"
    if not label:
        label = "asset"
    # Normalise to kebab-case for a clean filename
    label_slug = re.sub(r"[^\w\-]", "-", label.lower()).strip("-") or "asset"
    file       = label_slug + ".png"

    alt  = _safe_str(label)
    name = _safe_comment(node.get("name", "asset"))
    # /assets/images/ maps to public/assets/images/ in Vite (static public folder)
    # width/height use calc(Xpx * scale) for proportional responsive sizing
    return (
        f'{indent}{{/* {name} */}}\n'
        f'{indent}<img\n'
        f'{indent}  src="/assets/images/{file}"\n'
        f'{indent}  alt="{alt}"\n'
        f'{indent}  style={{{{\n'
        f'{indent}    position: "absolute",\n'
        f'{indent}    left: `calc({x}px * ${{{scale}}})`,\n'
        f'{indent}    top: `calc({y}px * ${{{scale}}})`,\n'
        f'{indent}    width: `calc({w}px * ${{{scale}}})`,\n'
        f'{indent}    height: `calc({h}px * ${{{scale}}})`,\n'
        f'{indent}    objectFit: "contain",\n'
        f'{indent}  }}}}\n'
        f'{indent}/>'
    )


def _has_explicit_size(node: dict, key: str) -> bool:
    value = node.get(key)
    return isinstance(value, (int, float)) and value > 0


def _layout_sizing(node: dict, axis: str) -> str:
    key = "layoutSizingHorizontal" if axis == "horizontal" else "layoutSizingVertical"
    value = str(node.get(key, "") or "").upper()
    return value if value in {"FIXED", "HUG", "FILL"} else ""


def _append_flow_sizing(styles: list[str], node: dict, scale: str) -> None:
    has_width = _has_explicit_size(node, "width")
    has_height = _has_explicit_size(node, "height")
    layout_h = _layout_sizing(node, "horizontal")
    layout_v = _layout_sizing(node, "vertical")
    layout_grow = node.get("layoutGrow", 0)
    min_width = node.get("minWidth", 0)
    min_height = node.get("minHeight", 0)

    if has_width:
        styles.append(f"width: `calc({node.get('width')}px * ${{{scale}}})`")
    elif layout_h == "FILL" or layout_grow:
        styles.append("flex: '1 1 0'")
        styles.append("minWidth: '0'")
    else:
        styles.append("width: 'fit-content'")
        styles.append("maxWidth: '100%'")

    if has_height:
        styles.append(f"height: `calc({node.get('height')}px * ${{{scale}}})`")
    elif layout_v == "FILL":
        styles.append("alignSelf: 'stretch'")
    else:
        styles.append("height: 'fit-content'")

    if isinstance(min_width, (int, float)) and min_width > 0:
        styles.append(f"minWidth: `calc({min_width}px * ${{{scale}}})`")
    if isinstance(min_height, (int, float)) and min_height > 0:
        styles.append(f"minHeight: `calc({min_height}px * ${{{scale}}})`")


def _render_rectangle(node: dict, indent: str, scale: str = "scale", parent_auto_layout: bool = False) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 100);  h = node.get("height", 40)
    bg = node.get("backgroundColor", "")
    gradient = node.get("gradient", "")
    radius   = node.get("cornerRadius", 0)
    opacity  = node.get("opacity", 1)
    bc       = node.get("borderColor", "");  bw = node.get("borderWidth", 0)
    name  = _safe_comment(node.get("name", "rect"))

    sp = [f"boxSizing: 'border-box'"]
    if parent_auto_layout:
        sp.append("position: 'relative'")
        _append_flow_sizing(sp, node, scale)
    else:
        sp.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({h}px * ${{{scale}}})`",
        ])
    if gradient:   sp.append(f"background: '{gradient}'")
    elif bg and bg not in ("transparent", ""): sp.append(f"backgroundColor: '{bg}'")
    if radius:     sp.append(f"borderRadius: `calc({radius}px * ${{{scale}}})`")
    if opacity != 1: sp.append(f"opacity: {opacity}")
    if bc and bw:
        sp.append(f"border: `${{Math.round({bw} * {scale})}}px solid {bc}`")
    else:
        bt_c = node.get("borderTopColor",    ""); bt_w = node.get("borderTopWidth",    0)
        bb_c = node.get("borderBottomColor", ""); bb_w = node.get("borderBottomWidth", 0)
        bl_c = node.get("borderLeftColor",   ""); bl_w = node.get("borderLeftWidth",   0)
        br_c = node.get("borderRightColor",  ""); br_w = node.get("borderRightWidth",  0)
        if bt_w: sp.append(f"borderTop: `${{Math.round({bt_w} * {scale})}}px solid {bt_c}`")
        if bb_w: sp.append(f"borderBottom: `${{Math.round({bb_w} * {scale})}}px solid {bb_c}`")
        if bl_w: sp.append(f"borderLeft: `${{Math.round({bl_w} * {scale})}}px solid {bl_c}`")
        if br_w: sp.append(f"borderRight: `${{Math.round({br_w} * {scale})}}px solid {br_c}`")
    style = ", ".join(sp)
    return f"{indent}{{/* {name} */}}\n{indent}<div style={{{{ {style} }}}} />"


def _render_line(node: dict, indent: str, scale: str = "scale", parent_auto_layout: bool = False) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 100)
    bg = node.get("backgroundColor", node.get("color", "#CCCCCC"))
    sw = node.get("strokeWeight", node.get("height", 1))
    sp = [f"boxSizing: 'border-box'"]
    if parent_auto_layout:
        sp.extend([
            "position: 'relative'",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({sw}px * ${{{scale}}})`",
        ])
    else:
        sp.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({sw}px * ${{{scale}}})`",
        ])
    if bg and bg not in ("transparent", ""): sp.append(f"backgroundColor: '{bg}'")
    return f"{indent}<div style={{{{ {', '.join(sp)} }}}} />"


def _render_vector(node: dict, indent: str, scale: str = "scale", parent_auto_layout: bool = False) -> str:
    if node.get("imageHash"):
        return _render_image(node, indent, scale, parent_auto_layout)
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 40);  h = node.get("height", 40)
    bg = node.get("backgroundColor", "")
    radius  = 9999 if node.get("type", "").lower() == "ellipse" else node.get("cornerRadius", 0)
    opacity = node.get("opacity", 1)
    bc = node.get("borderColor", "");  bw = node.get("borderWidth", 0)
    sp = [f"boxSizing: 'border-box'"]
    if parent_auto_layout:
        sp.append("position: 'relative'")
        _append_flow_sizing(sp, node, scale)
    else:
        sp.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({h}px * ${{{scale}}})`",
        ])
    if bg and bg not in ("transparent", ""): sp.append(f"backgroundColor: '{bg}'")
    if radius:    sp.append(f"borderRadius: `calc({radius}px * ${{{scale}}})`" if radius < 9999 else "borderRadius: '50%'")
    if opacity != 1: sp.append(f"opacity: {opacity}")
    if bc and bw: sp.append(f"border: `${{Math.round({bw}*{scale})}}px solid {bc}`")
    name = _safe_comment(node.get("name", "shape"))
    return f"{indent}{{/* {name} */}}\n{indent}<div style={{{{ {', '.join(sp)} }}}} />"


def _render_text(node: dict, indent: str, scale: str = "scale",
                 all_routes: list = None, nav_context: dict = None, page_name: str = "",
                 parent_auto_layout: bool = False) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0);  w = node.get("width", 0)
    h  = node.get("height", 0)
    raw_text       = node.get("text", "")
    font_size      = node.get("fontSize", 16)
    font_weight    = _map_font_weight(node.get("fontWeight", 400))
    color          = node.get("color", node.get("textColor", ""))
    line_height    = node.get("lineHeight", 1.4)
    letter_spacing = node.get("letterSpacing", 0)
    opacity        = node.get("opacity", 1)
    text_align     = node.get("textAlign", node.get("textAlignHorizontal", "left")).lower()
    text_align_vertical = str(node.get("textAlignVertical", "top") or "top").lower()
    is_nav_link    = node.get("isNavLink", False)

    tag = "h1" if font_size >= 64 else "h2" if font_size >= 42 else "h3" if font_size >= 28 else "h4" if font_size >= 20 else "p"

    sp = [
        f"fontSize: `calc({font_size}px * ${{{scale}}})`",
        f"fontWeight: {font_weight}",
        f"lineHeight: {line_height}",
        f"margin: '0'",
        f"padding: '0'",
        f"boxSizing: 'border-box'",
    ]
    if parent_auto_layout:
        _append_flow_sizing(sp, node, scale)
    else:
        sp.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
        ])
        if _has_explicit_size(node, "width"):
            sp.append(f"width: `calc({w}px * ${{{scale}}})`")
        else:
            sp.append("width: 'fit-content'")
            sp.append("maxWidth: '100%'")
        if h:
            sp.append(f"minHeight: `calc({h}px * ${{{scale}}})`")

    if h and text_align_vertical in {"center", "bottom"}:
        align_map = {"center": "center", "bottom": "flex-end"}
        sp.append("display: 'flex'")
        sp.append(f"alignItems: '{align_map[text_align_vertical]}'")
    if color and color not in ("transparent", ""): sp.append(f"color: '{color}'")
    if text_align in ("center", "right", "justify"): sp.append(f"textAlign: '{text_align}'")
    if letter_spacing: sp.append(f"letterSpacing: `calc({letter_spacing}px * ${{{scale}}})`")
    if opacity != 1: sp.append(f"opacity: {opacity}")

    style = ", ".join(sp)
    name  = _safe_comment(node.get("name", "text"))
    inner = _render_text_content(_escape_jsx_text(raw_text))

    # ── If this text node is inside a navbar, render as a nav Link ──
    if is_nav_link:
        node_name = node.get("name", raw_text)
        target    = _resolve_nav(node_name, raw_text, page_name, nav_context or {}, all_routes or [])
        nav_style = ", ".join(sp + ["cursor: 'pointer'", "textDecoration: 'none'"])
        if target:
            return (
                f"{indent}{{/* {name} (nav link) */}}\n"
                f"{indent}<Link to=\"{target}\" style={{{{ {nav_style} }}}}>{inner}</Link>"
            )
        else:
            return (
                f"{indent}{{/* {name} (nav link) */}}\n"
                f"{indent}<span style={{{{ {nav_style} }}}} onClick={{() => {{}}}}>{inner}</span>"
            )

    return f"{indent}{{/* {name} */}}\n{indent}<{tag} style={{{{ {style} }}}}>{inner}</{tag}>"
def _render_image(node: dict, indent: str, scale: str = "scale", parent_auto_layout: bool = False) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 400);  h = node.get("height", 300)
    src        = node.get("src", "")
    image_hash = node.get("imageHash", "")
    radius     = node.get("borderRadius", node.get("cornerRadius", 0))
    bg         = node.get("backgroundColor", "")
    alt        = _safe_str(node.get("name", "image"))
    opacity    = node.get("opacity", 1)
    name       = _safe_comment(node.get("name", "image"))

    IMAGE_PROXY = "http://localhost:9000/api/image-proxy"

    if image_hash and (not src or src in ("", "PLACEHOLDER") or src.startswith("FIGMA_IMAGE:")):
        src = f"{IMAGE_PROXY}?hash={image_hash}"

    base = [f"boxSizing: 'border-box'"]
    if parent_auto_layout:
        base.append("position: 'relative'")
        _append_flow_sizing(base, node, scale)
    else:
        base.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({h}px * ${{{scale}}})`",
        ])
    if radius:      base.append(f"borderRadius: `calc({radius}px * ${{{scale}}})`")
    if opacity != 1: base.append(f"opacity: {opacity}")

    if src and src not in ("", "PLACEHOLDER") and not src.startswith("FIGMA_IMAGE:"):
        img_parts = base + ["objectFit: 'cover'"]
        if bg and bg not in ("transparent", ""): img_parts.append(f"backgroundColor: '{bg}'")
        style = ", ".join(img_parts)
        return (
            f'{indent}{{/* {name} */}}\n'
            f'{indent}<img\n'
            f'{indent}  src="{src}"\n'
            f'{indent}  alt="{alt}"\n'
            f'{indent}  style={{{{ {style} }}}}\n'
            f'{indent}  onError={{e => {{ e.currentTarget.style.display="none"; }}}}\n'
            f'{indent}/>'
        )

    if src.startswith("FIGMA_IMAGE:"):
        proxy_url = f"{IMAGE_PROXY}?hash={image_hash}"
        div_parts = base + [
            f"backgroundImage: 'url({proxy_url})'",
            f"backgroundSize: 'cover'",
            f"backgroundPosition: 'center'",
        ]
        if bg and bg not in ("transparent", ""): div_parts.append(f"backgroundColor: '{bg}'")
        style = ", ".join(div_parts)
        return f'{indent}{{/* {name} */}}\n{indent}<div style={{{{ {style} }}}} />'

    return ""


def _render_button(
    node: dict, indent: str,
    all_routes: list[dict], nav_context: dict, page_name: str,
    scale: str = "scale",
    parent_auto_layout: bool = False,
) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 160);  h = node.get("height", 48)
    text        = _escape_jsx_text(node.get("text", "Button"))
    node_name   = node.get("name", "button")
    bg          = node.get("backgroundColor", "")
    text_color  = node.get("textColor", "")
    radius      = node.get("cornerRadius", 8)
    font_size   = node.get("fontSize", 16)
    font_weight = _map_font_weight(node.get("fontWeight", "semibold"))
    bc = node.get("borderColor", "");  bw = node.get("borderWidth", 0)
    opacity = node.get("opacity", 1)
    name = _safe_comment(node_name)

    sp = [
        f"borderRadius: `calc({radius}px * ${{{scale}}})`",
        f"fontSize: `calc({font_size}px * ${{{scale}}})`",
        f"fontWeight: {font_weight}",
        f"cursor: 'pointer'",        
        f"display: 'flex'",
        f"alignItems: 'center'",
        f"justifyContent: 'center'",
        f"transition: 'opacity 0.2s'",
        f"boxSizing: 'border-box'",
        f"whiteSpace: 'nowrap'",
        f"outline: 'none'",
    ]
    if parent_auto_layout:
        _append_flow_sizing(sp, node, scale)
    else:
        sp = [
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
            f"width: `calc({w}px * ${{{scale}}})`",
            f"height: `calc({h}px * ${{{scale}}})`",
        ] + sp
    if bc and int(bw) > 0:
        sp.append(f"border: `${{Math.round({bw}*{scale})}}px solid {bc}`")
    else:
        sp.append("border: 'none'")
    if bg and bg not in ("transparent", ""): sp.append(f"backgroundColor: '{bg}'")
    if text_color and text_color not in ("transparent", ""): sp.append(f"color: '{text_color}'")
    if opacity != 1: sp.append(f"opacity: {opacity}")

    style  = ", ".join(sp)
    target = _resolve_nav(node_name, node.get("text", ""), page_name, nav_context, all_routes)

    # ── Auto-wire action buttons by name/text ────────────────────
    nl = node_name.lower();  tl = text.lower()
    if target:
        click = f' onClick={{() => navigate("{target}")}}'
    elif any(k in nl or k in tl for k in ("close", "cancel", "dismiss", "×", "✕", "x btn")):
        click = " onClick={() => { if (typeof onClose === 'function') onClose(); else window.history.back(); }}"
    elif any(k in nl or k in tl for k in ("back", "return", "previous", "prev", "go back")):
        click = " onClick={() => window.history.back()}"
    elif any(k in nl or k in tl for k in ("minimise", "minimize")):
        # Bug 3 fix: use the parent container's minimize state key if provided.
        # This ensures the button and the collapsible panel share the same state.
        # parent_minimize_key is passed down from _render_container.
        effective_key = node.get("_minimize_key") or re.sub(r"[^\w]", "", node_name.title()) or "Panel"
        click = f" onClick={{() => setMinimized{effective_key}(prev => !prev)}}"
    else:
        click = ""

    hover = "onMouseEnter={e=>e.currentTarget.style.opacity='0.85'} onMouseLeave={e=>e.currentTarget.style.opacity='1'}"

    return (
        f"{indent}{{/* {name} */}}\n"
        f"{indent}<button style={{{{ {style} }}}}{click} {hover}>\n"
        f"{indent}  {text}\n"
        f"{indent}</button>"
    )


def _is_minimize_node(node: dict) -> bool:
    """Return True if this node is a minimize/minimise button."""
    nl = node.get("name", "").lower()
    tl = node.get("text", "").lower()
    return any(k in nl or k in tl for k in ("minimise", "minimize"))


# ─────────────────────────────────────────────────────────────────
# CONTAINER RENDERER
# ─────────────────────────────────────────────────────────────────

def _render_container(
    node: dict,
    all_routes: list[dict], nav_context: dict, page_name: str,
    indent: str, depth: int,
    comp_map: dict, used_components: set,
    scale: str = "scale",
    parent_auto_layout: bool = False,
) -> str:
    x  = node.get("x", 0);  y = node.get("y", 0)
    w  = node.get("width", 0);  h = node.get("height", 0)
    bg        = node.get("backgroundColor", "")
    radius    = node.get("cornerRadius", 0)
    opacity   = node.get("opacity", 1)
    name      = _safe_comment(node.get("name", "group"))
    node_type = node.get("type", "").lower()
    node_pascal = _to_pascal(node.get("name", ""))
    if comp_map and node_pascal in comp_map and used_components is not None:
        used_components.add(node_pascal)
        return (
            f"{indent}{{/* {node_pascal} — shared component */}}\n"
            f"{indent}<{node_pascal} />"
        )
    is_frame_type = node_type in ("frame", "component", "instance")
    clips = is_frame_type or node.get("clipsContent", False)

    image_hash     = node.get("imageHash", "")
    has_image_fill = node.get("imageFill", False) and bool(image_hash)
    IMAGE_PROXY    = "http://localhost:9000/api/image-proxy"

    # ── Bug 1 fix ─────────────────────────────────────────────────
    # A FRAME/COMPONENT node in Figma defines its OWN coordinate space.
    # Its children's x/y are relative to the frame's top-left corner,
    # NOT the page origin.
    #
    # Correct rendering:
    #   - The container itself: position absolute, placed at its x/y on parent
    #   - The inner wrapper:    position relative (establishes new origin for children)
    #   - Children:             position absolute, left/top = their local x/y
    #
    # Without the inner wrapper, a sidebar at page-x=0,y=80 with a button
    # at local y=600 would render at page-y=600, not sidebar-y=600.
    #
    # For GROUP nodes, Figma children coords are also local, so same fix applies.
    has_children = bool(node.get("children"))

    layout_mode = node.get("layoutMode", "")
    uses_flow_children = layout_mode in ("HORIZONTAL", "VERTICAL")

    cp_outer = [f"boxSizing: 'border-box'"]
    if parent_auto_layout:
        cp_outer.append("position: 'relative'")
        _append_flow_sizing(cp_outer, node, scale)
    else:
        cp_outer.extend([
            f"position: 'absolute'",
            f"left: `calc({x}px * ${{{scale}}})`",
            f"top: `calc({y}px * ${{{scale}}})`",
        ])
        if _has_explicit_size(node, "width"):
            cp_outer.append(f"width: `calc({w}px * ${{{scale}}})`")
        elif uses_flow_children:
            cp_outer.append("width: 'fit-content'")
            cp_outer.append("maxWidth: '100%'")
        else:
            cp_outer.append("width: '0px'")
        if _has_explicit_size(node, "height"):
            cp_outer.append(f"height: `calc({h}px * ${{{scale}}})`")
        elif uses_flow_children:
            cp_outer.append("height: 'fit-content'")
        else:
            cp_outer.append("height: '0px'")
    if has_image_fill:
        proxy_url = f"{IMAGE_PROXY}?hash={image_hash}"
        cp_outer += [
            f"backgroundImage: 'url({proxy_url})'",
            f"backgroundSize: 'cover'",
            f"backgroundPosition: 'center'",
        ]
    elif bg and bg not in ("transparent", ""):
        cp_outer.append(f"backgroundColor: '{bg}'")
    if radius:   cp_outer.append(f"borderRadius: `calc({radius}px * ${{{scale}}})`")
    if clips:    cp_outer.append(f"overflow: 'hidden'")
    if opacity != 1: cp_outer.append(f"opacity: {opacity}")

    # ── Individual side borders on containers ─────────────────
    bc = node.get("borderColor", ""); bw = node.get("borderWidth", 0)
    if bc and bw:
        cp_outer.append(f"border: `${{Math.round({bw} * {scale})}}px solid {bc}`")
    else:
        bt_c = node.get("borderTopColor",    ""); bt_w = node.get("borderTopWidth",    0)
        bb_c = node.get("borderBottomColor", ""); bb_w = node.get("borderBottomWidth", 0)
        bl_c = node.get("borderLeftColor",   ""); bl_w = node.get("borderLeftWidth",   0)
        br_c = node.get("borderRightColor",  ""); br_w = node.get("borderRightWidth",  0)
        if bt_w: cp_outer.append(f"borderTop: `${{Math.round({bt_w} * {scale})}}px solid {bt_c}`")
        if bb_w: cp_outer.append(f"borderBottom: `${{Math.round({bb_w} * {scale})}}px solid {bb_c}`")
        if bl_w: cp_outer.append(f"borderLeft: `${{Math.round({bl_w} * {scale})}}px solid {bl_c}`")
        if br_w: cp_outer.append(f"borderRight: `${{Math.round({br_w} * {scale})}}px solid {br_c}`")

    # ── Auto-layout → flexbox ──────────────────────────────────
    if layout_mode in ("HORIZONTAL", "VERTICAL"):
        flex_dir   = "row" if layout_mode == "HORIZONTAL" else "column"
        axis_map   = {"MIN": "flex-start", "CENTER": "center", "MAX": "flex-end", "SPACE_BETWEEN": "space-between"}
        justify    = axis_map.get(node.get("primaryAxisAlignItems", "MIN"), "flex-start")
        align      = axis_map.get(node.get("counterAxisAlignItems", "MIN"), "flex-start")
        gap        = node.get("itemSpacing", 0)
        pt         = node.get("paddingTop",    0)
        pb         = node.get("paddingBottom", 0)
        pl         = node.get("paddingLeft",   0)
        pr         = node.get("paddingRight",  0)
        cp_outer.append(f"display: 'flex'")
        cp_outer.append(f"flexDirection: '{flex_dir}'")
        cp_outer.append(f"justifyContent: '{justify}'")
        cp_outer.append(f"alignItems: '{align}'")
        if gap:  cp_outer.append(f"gap: `calc({gap}px * ${{{scale}}})`")
        if pt:   cp_outer.append(f"paddingTop: `calc({pt}px * ${{{scale}}})`")
        if pb:   cp_outer.append(f"paddingBottom: `calc({pb}px * ${{{scale}}})`")
        if pl:   cp_outer.append(f"paddingLeft: `calc({pl}px * ${{{scale}}})`")
        if pr:   cp_outer.append(f"paddingRight: `calc({pr}px * ${{{scale}}})`")
    if isinstance(node.get("layoutGrow"), (int, float)) and node.get("layoutGrow"):
        cp_outer.append(f"flexGrow: {int(node.get('layoutGrow', 0))}")
    layout_align = node.get("layoutAlign", "")
    if layout_align == "STRETCH":
        cp_outer.append("alignSelf: 'stretch'")
    elif layout_align == "CENTER":
        cp_outer.append("alignSelf: 'center'")
    elif layout_align == "MAX":
        cp_outer.append("alignSelf: 'flex-end'")

    # Nav link detection
    target = _resolve_nav(node.get("name", ""), "", page_name, nav_context, all_routes)
    if target:
        cp_outer.append(f"cursor: 'pointer'")

    # Minimize detection — check if this container is a minimizable panel.
    # Detect by name patterns: "sidebar", "panel", "drawer", or any container
    # that contains a minimize button among its direct children.
    raw_kids     = node.get("children", [])
    visible_kids = [c for c in raw_kids if isinstance(c, dict) and _is_visible(c)]

    # Check if any direct child is a minimize button
    has_minimize_child = any(
        _is_minimize_node(c) for c in visible_kids
    )
    minimize_state_key = None
    if has_minimize_child:
        minimize_state_key = re.sub(r"[^\w]", "", node.get("name", "Panel").title()) or "Panel"

    outer_style = ", ".join(cp_outer)
    click_attr  = f' onClick={{() => navigate("{target}")}}'  if target else ""
    hover_attr  = " onMouseEnter={e=>e.currentTarget.style.opacity='0.9'} onMouseLeave={e=>e.currentTarget.style.opacity='1'}" if target else ""

    # Render children — they keep their local x/y coords
    child_lines = []
    for child in visible_kids:
        # Stamp the parent's minimize key onto any minimize button child
        # so it toggles the same state as the container wrapper.
        if minimize_state_key and _is_minimize_node(child):
            child = dict(child)                          # shallow copy — don't mutate original
            child["_minimize_key"] = minimize_state_key
        jsx = _render_node(
            child,
            all_routes,
            nav_context,
            page_name,
            depth + 1,
            comp_map,
            used_components,
            scale,
            uses_flow_children,
        )
        if jsx:
            child_lines.append(jsx)

    children_str = "\n".join(child_lines)

    # ── Build the two-layer structure ─────────────────────────────
    # Outer div: absolute positioned at x/y, sized w/h, has bg/radius/overflow
    # Inner div: position relative, same w/h — children anchor to THIS origin
    if has_children and not uses_flow_children:
        inner_style = (
            f"position: 'relative', "
            f"width: '100%', "
            f"height: '100%'"
        )

        if minimize_state_key:
            # Minimizable panel: show collapsed bar when minimized
            restore_bar = (
                f'{indent}  {{minimized{minimize_state_key} && (\n'
                f'{indent}    <div\n'
                f'{indent}      onClick={{() => setMinimized{minimize_state_key}(false)}}\n'
                f'{indent}      style={{{{ {outer_style}, cursor: \'pointer\', '
                f'display: \'flex\', alignItems: \'center\', justifyContent: \'center\','
                f'height: `calc(36px * ${{{scale}}})` }}}}\n'
                f'{indent}    >\n'
                f'{indent}      <span style={{{{ fontSize: `calc(12px * ${{{scale}}})`, '
                f'color: \'{bg or "#666"}\' }}}}>▶ Restore</span>\n'
                f'{indent}    </div>\n'
                f'{indent}  )}}\n'
            )
            return (
                f"{indent}{{/* {name} — minimizable panel */}}\n"
                f"{restore_bar}"
                f"{indent}{{!minimized{minimize_state_key} && (\n"
                f"{indent}<div style={{{{ {outer_style} }}}}{click_attr}{hover_attr}>\n"
                f"{indent}  <div style={{{{ {inner_style} }}}}>\n"
                f"{children_str}\n"
                f"{indent}  </div>\n"
                f"{indent}</div>\n"
                f"{indent})}}"
            )

        return (
            f"{indent}{{/* {name} */}}\n"
            f"{indent}<div style={{{{ {outer_style} }}}}{click_attr}{hover_attr}>\n"
            f"{indent}  <div style={{{{ {inner_style} }}}}>\n"
            f"{children_str}\n"
            f"{indent}  </div>\n"
            f"{indent}</div>"
        )

    if has_children and uses_flow_children:
        return (
            f"{indent}{{/* {name} */}}\n"
            f"{indent}<div style={{{{ {outer_style} }}}}{click_attr}{hover_attr}>\n"
            f"{children_str}\n"
            f"{indent}</div>"
        )

    # Leaf container (no children) — single div is fine
    return (
        f"{indent}{{/* {name} */}}\n"
        f"{indent}<div style={{{{ {outer_style} }}}}{click_attr}{hover_attr} />"
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
# BOILERPLATE GENERATORS  (unchanged from original)
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
        '<!DOCTYPE html>\n<html lang="en">\n  <head>\n'
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
        "html { scroll-behavior: smooth; }\n\n"
        # Neutral scrollbar — transparent track so it adapts to any bg color
        "::-webkit-scrollbar { width: 6px; height: 6px; }\n"
        "::-webkit-scrollbar-track { background: transparent; }\n"
        "::-webkit-scrollbar-thumb { background: rgba(128,128,128,0.4); border-radius: 3px; }\n"
        "::-webkit-scrollbar-thumb:hover { background: rgba(128,128,128,0.7); }\n"
        "* { scrollbar-width: thin; scrollbar-color: rgba(128,128,128,0.4) transparent; }\n"
    )


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _to_safe_component(name: str, index: int) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    pascal  = "".join(w.capitalize() for w in re.split(r"[\s\-_]+", cleaned.strip())
                      if w and re.match(r"\w", w[0]))
    return pascal if pascal and pascal[0].isalpha() else f"Page{index + 1}"

def _to_safe_slug(name: str, index: int, seen: set) -> str:
    slug = re.sub(r"-+", "-", re.sub(r"[\s_]+", "-",
           re.sub(r"[^\w\s\-]", "", name.lower()).strip()).strip("-")) or f"page-{index + 1}"
    orig, counter = slug, 2
    while slug in seen:
        slug = f"{orig}-{counter}"; counter += 1
    return slug

def _to_camel(name: str) -> str:
    pascal = name
    return pascal[0].lower() + pascal[1:] if pascal else name

def _map_font_weight(weight) -> int:
    if isinstance(weight, int): return weight
    return {"thin":100,"extralight":200,"light":300,"regular":400,"normal":400,
            "medium":500,"semibold":600,"bold":700,"extrabold":800,"black":900
            }.get(str(weight).lower(), 400)

def _safe_comment(name: str) -> str:
    return re.sub(r"[{}\*\/]", "", name).strip()

def _safe_str(name: str) -> str:
    return re.sub(r'["\'`<>]', "", name).strip()

def _escape_jsx_text(text: str) -> str:
    return (text.replace("&", "&amp;").replace("{", "&#123;")
                .replace("}", "&#125;").replace("<", "&lt;").replace(">", "&gt;"))

def _render_text_content(text: str) -> str:
    if "\n" in text or "\\n" in text:
        return "<br />".join(text.replace("\\n", "\n").split("\n"))
    return text

def _make_style(parts: list) -> str:
    return ", ".join(f"{k}: {v}" for k, v in parts)
