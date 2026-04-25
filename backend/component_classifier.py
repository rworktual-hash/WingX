"""
component_classifier.py  —  Universal Component Type System

Takes the raw list of frames extracted from Figma and classifies each one:

  PAGE        → becomes a React Router route  (pages/<Name>.jsx)
  OVERLAY     → floating component, NOT a route  (components/<Name>.jsx)
               types: modal, drawer, popover, tooltip, toast, dialog, bottomsheet
  INLINE      → reusable component embedded in its parent page  (components/<Name>.jsx)
               types: card, table, pagination, badge, checkbox, radio, toggle,
                      input, select, tabs, accordion, sidebar, navbar, footer,
                      hero, banner, form, list, avatar, tag, chip
  TAB         → child of a tabs/modal component — NOT a standalone file
  ACTION      → no visual output, pure behavior hint

Output structure:

  ClassifiedProject {
    pages:      [ PageEntry ]          ← become routes
    components: [ ComponentEntry ]     ← become component files
    tab_groups: { parentName: [tabs] } ← tabs grouped under their parent
    routes:     [ RouteEntry ]         ← route table for App.jsx
  }
"""

import re
from typing import Optional
import logger as log





# ─────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────

# Types that become full React Router routes
PAGE_TYPES = {None, "page"}

# Types that render as floating overlay components (not routes)
OVERLAY_TYPES = {
    "modal", "drawer", "popover", "tooltip",
    "toast", "dialog", "bottomsheet",
}

# Types that render as inline reusable components
INLINE_TYPES = {
    "card", "table", "pagination", "badge", "checkbox", "radio",
    "toggle", "input", "select", "tabs", "accordion", "sidebar",
    "navbar", "footer", "hero", "banner", "form", "list",
    "avatar", "tag", "chip", "scroller", "range", "slider", 
}

# Types that are children of another component — no standalone file
TAB_TYPES = {"tab"}

# Action-only types — no file generated, behavior only
ACTION_PREFIX = "action:"

COMPONENT_TYPES = OVERLAY_TYPES | INLINE_TYPES


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def classify(frames: list[dict]) -> dict:
    """
    Main entry point.

    Args:
        frames: list of frame dicts from extract_complete, each with:
                { name, frame, nav_hint, desc_hint, comp_type,
                  parent_ref, default_tab, node_id, width, height }

    Returns:
        {
          "pages":      [ { name, frame, route, component_name, ... } ],
          "components": [ { name, frame, comp_type, component_name, ... } ],
          "tab_groups": { "ParentName": [ { name, frame, index, ... } ] },
          "routes":     [ { page_name, component_name, route_path, slug_path, file_name } ],
          "component_map": { "ComponentName": { comp_type, file_name, ... } }
        }
    """
    pages      = []
    components = []
    tab_groups = {}   # parent_name → [tab frames]
    actions    = []

    seen_slugs: set[str] = set()

    for frame in frames:
        comp_type  = (frame.get("comp_type") or "").strip().lower() or None
        parent_ref = frame.get("parent_ref", "")
        name       = frame.get("name", "Unnamed")

        # ── Action nodes — no file, just log ─────────────────────
        if comp_type and comp_type.startswith(ACTION_PREFIX):
            actions.append({
                "name":        name,
                "action_type": comp_type[len(ACTION_PREFIX):],
            })
            log.info("CLASSIFY", f"Action node: {name!r} → {comp_type}")
            continue

        # ── Tab children — group under parent ────────────────────
        if comp_type in TAB_TYPES:
            parent = parent_ref or _infer_parent(name, frames)
            if parent not in tab_groups:
                tab_groups[parent] = []
            tab_groups[parent].append({
                "name":        name,
                "frame":       frame.get("frame"),
                "index":       len(tab_groups[parent]),
                "nav_hint":    frame.get("nav_hint"),
                "desc_hint":   frame.get("desc_hint"),
                "default_tab": frame.get("default_tab"),
            })
            log.info("CLASSIFY", f"Tab child: {name!r} → parent={parent!r}")
            continue

        # ── Component (overlay or inline) ─────────────────────────
        if comp_type in COMPONENT_TYPES:
            component_name = _to_pascal(name)

            # ── Deduplication: skip if same component name already added ──
            # In Figma, designers often have multiple frames for the same
            # modal (e.g. different states). We keep only the FIRST one.
            existing_names = [c["component_name"] for c in components]
            if component_name in existing_names:
                log.info("CLASSIFY",
                    f"Skipping duplicate component: {name!r} ({component_name} already classified)")
                continue

            components.append({
                "name":           name,
                "frame":          frame.get("frame"),
                "comp_type":      comp_type,
                "component_name": component_name,
                "file_name":      f"components/{component_name}.jsx",
                "nav_hint":       frame.get("nav_hint"),
                "desc_hint":      frame.get("desc_hint"),
                "default_tab":    frame.get("default_tab"),
                "parent_ref":     parent_ref,
                "width":          frame.get("width", 0),
                "height":         frame.get("height", 0),
                "node_id":        frame.get("node_id", ""),
            })
            log.info("CLASSIFY", f"Component: {name!r} → type={comp_type!r}  file=components/{component_name}.jsx")
            continue

        # ── Default: PAGE → route ─────────────────────────────────
        component_name = _to_safe_component(name, len(pages))
        slug           = _to_safe_slug(name, len(pages), seen_slugs)
        seen_slugs.add(slug)
        route_path     = "/" if len(pages) == 0 else f"/{slug}"

        pages.append({
            "name":           name,
            "frame":          frame.get("frame"),
            "comp_type":      comp_type or "page",
            "component_name": component_name,
            "route_path":     route_path,
            "slug_path":      f"/{slug}",
            "file_name":      f"pages/{component_name}.jsx",
            "nav_hint":       frame.get("nav_hint"),
            "desc_hint":      frame.get("desc_hint"),
            "default_tab":    frame.get("default_tab"),
            "width":          frame.get("width", 1440),
            "height":         frame.get("height", 900),
            "node_id":        frame.get("node_id", ""),
        })
        log.info("CLASSIFY", f"Page: {name!r} → route={route_path!r}  file=pages/{component_name}.jsx")

    # ── Build routes table ────────────────────────────────────────
    routes = [
        {
            "page_name":      p["name"],
            "component_name": p["component_name"],
            "route_path":     p["route_path"],
            "slug_path":      p["slug_path"],
            "file_name":      p["file_name"],
        }
        for p in pages
    ]

    # ── Build component_map for quick lookup by name ──────────────
    component_map = {
        c["component_name"]: {
            "comp_type": c["comp_type"],
            "file_name": c["file_name"],
            "name":      c["name"],
        }
        for c in components
    }

    # ── Attach tab groups to their parent component ───────────────
    for comp in components:
        name = comp["name"]
        if name in tab_groups:
            comp["tabs"] = tab_groups[name]
            log.info("CLASSIFY",
                f"Attached {len(tab_groups[name])} tab(s) to component {name!r}")

    # ── Summary log ──────────────────────────────────────────────
    log.success("CLASSIFY",
        f"Classification complete — "
        f"{len(pages)} pages, {len(components)} components, "
        f"{sum(len(v) for v in tab_groups.values())} tabs, "
        f"{len(actions)} actions"
    )

    return {
        "pages":         pages,
        "components":    components,
        "tab_groups":    tab_groups,
        "routes":        routes,
        "component_map": component_map,
        "actions":       actions,
    }


# ─────────────────────────────────────────────────────────────────
# COMPONENT TYPE HELPERS  (mirrors code.js inferComponentType)
# ─────────────────────────────────────────────────────────────────

def is_overlay(comp_type: Optional[str]) -> bool:
    """True if this component type should render as a floating overlay."""
    return (comp_type or "").lower() in OVERLAY_TYPES


def is_inline(comp_type: Optional[str]) -> bool:
    """True if this component should render inline inside a page."""
    return (comp_type or "").lower() in INLINE_TYPES


def is_page(comp_type: Optional[str]) -> bool:
    """True if this frame should become a route."""
    return (comp_type or "").lower() in PAGE_TYPES


def is_tab(comp_type: Optional[str]) -> bool:
    """True if this frame is a tab child."""
    return (comp_type or "").lower() in TAB_TYPES


def is_action(comp_type: Optional[str]) -> bool:
    """True if this is a pure action node."""
    return (comp_type or "").startswith(ACTION_PREFIX)


def get_action_type(comp_type: str) -> str:
    """Extract action type from e.g. 'action:close' → 'close'."""
    return comp_type[len(ACTION_PREFIX):] if comp_type.startswith(ACTION_PREFIX) else ""


# ─────────────────────────────────────────────────────────────────
# RENDER STRATEGY
# Tells exporter.py HOW to render each component type
# ─────────────────────────────────────────────────────────────────

RENDER_STRATEGY = {
    # Overlays — floating divs with backdrop, toggled by state
    "modal":       "overlay_modal",
    "dialog":      "overlay_modal",
    "drawer":      "overlay_drawer",
    "bottomsheet": "overlay_drawer",
    "popover":     "overlay_popover",
    "tooltip":     "overlay_tooltip",
    "toast":       "overlay_toast",

    # Inline components — standard absolute-positioned JSX
    "tabs":        "inline_tabs",
    "accordion":   "inline_accordion",
    "table":       "inline_table",
    "form":        "inline_form",
    "sidebar":     "inline_block",
    "navbar":      "inline_block",
    "footer":      "inline_block",
    "card":        "inline_block",
    "hero":        "inline_block",
    "banner":      "inline_block",
    "pagination":  "inline_block",
    "list":        "inline_block",
    "badge":       "inline_block",
    "checkbox":    "inline_block",
    "radio":       "inline_block",
    "toggle":      "inline_block",
    "input":       "inline_block",
    "select":      "inline_block",
    "chip":        "inline_block",
    "tag":         "inline_block",
    "avatar":      "inline_block",
}


def get_render_strategy(comp_type: Optional[str]) -> str:
    """Return the render strategy key for this component type."""
    return RENDER_STRATEGY.get((comp_type or "").lower(), "page")


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _infer_parent(tab_name: str, all_frames: list[dict]) -> str:
    """
    If a tab frame has no @parent: hint, try to guess which component
    owns it by looking for a tabs/modal frame whose name is a prefix
    or close match of the tab name.
    """
    tab_lower = tab_name.lower()
    best_match = "UnknownParent"
    best_score = 0

    for frame in all_frames:
        ct = (frame.get("comp_type") or "").lower()
        if ct not in ("tabs", "modal", "dialog"):
            continue
        parent_lower = frame.get("name", "").lower()
        # Word overlap score
        pw = set(w for w in re.split(r"[\s\-_]+", parent_lower) if len(w) > 2)
        tw = set(w for w in re.split(r"[\s\-_]+", tab_lower)    if len(w) > 2)
        score = len(pw & tw)
        if score > best_score:
            best_score = score
            best_match = frame.get("name", "UnknownParent")

    return best_match


def _to_pascal(name: str) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    return "".join(
        w.capitalize()
        for w in re.split(r"[\s\-_]+", cleaned.strip())
        if w and re.match(r"\w", w[0])
    ) or "Component"


def _to_safe_component(name: str, index: int) -> str:
    pascal = _to_pascal(name)
    return pascal if pascal and pascal[0].isalpha() else f"Page{index + 1}"


def _to_safe_slug(name: str, index: int, seen: set) -> str:
    slug = re.sub(
        r"-+", "-",
        re.sub(r"[\s_]+", "-",
               re.sub(r"[^\w\s\-]", "", name.lower()).strip()
               ).strip("-")
    ) or f"page-{index + 1}"

    original, counter = slug, 2
    while slug in seen:
        slug = f"{original}-{counter}"
        counter += 1
    return slug