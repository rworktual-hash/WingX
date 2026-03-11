"""
nav_extractor.py  —  Parse @nav: frame-name hints into button→route mappings

Designer writes in Figma frame name:
    Add Custom API Page | @nav: Cancel Button -> Custom API Page, Save Button -> Custom API Page | @desc: cancel goes back, save submits

Supports two @nav formats:
  1. Button → Page mapping (preferred):
       @nav: Cancel Button -> Custom API Page, Save Button -> Dashboard
  2. Page list only (legacy fallback):
       @nav: Home Page, Profile Page, Chat Page

Both can be mixed in the same @nav: string.
"""

import re
from typing import Optional
import logger as log


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────

def build_nav_context(pages: list[dict], routes: list[dict]) -> dict[str, dict]:
    """
    Returns a dict keyed by clean page name:
    {
      "Add Custom API Page": {
        "nav_hint":      "@nav: Cancel Button -> Custom API Page, Save Button -> Custom API Page",
        "desc_hint":     "cancel goes back without saving, save submits and returns",
        "button_routes": [
          { "button_text": "Cancel Button", "route_path": "/custom-api-page" },
          { "button_text": "Save Button",   "route_path": "/custom-api-page" },
        ],
        "destinations": [
          { "frame_name": "Custom API Page", "route_path": "/custom-api-page" },
        ]
      }
    }
    """
    route_lookup = _build_route_lookup(routes)
    nav_context: dict[str, dict] = {}

    for page in pages:
        name      = page.get("name", "")
        nav_hint  = page.get("nav_hint")  or ""
        desc_hint = page.get("desc_hint") or ""

        button_routes, destinations = [], []

        if nav_hint.strip():
            button_routes, destinations = _parse_nav_hint(nav_hint, route_lookup)

        nav_context[name] = {
            "nav_hint":      nav_hint  or None,
            "desc_hint":     desc_hint or None,
            "button_routes": button_routes,
            "destinations":  destinations,
        }

        # Logging
        if button_routes:
            for br in button_routes:
                log.success("NAV",
                    f"Page={name!r}  btn={br['button_text']!r} → {br['route_path']!r}"
                )
        elif destinations:
            dest_str = ", ".join(f"{d['frame_name']} → {d['route_path']}" for d in destinations)
            log.success("NAV", f"Page={name!r} destinations: {dest_str}")

        if desc_hint:
            log.info("NAV", f"Page={name!r} @desc: {desc_hint!r}")

        if nav_hint and not button_routes and not destinations:
            log.warn("NAV", f"Page={name!r} @nav hint could not be resolved: {nav_hint!r}")

    return nav_context


def build_nav_prompt_block(page_name: str, nav_context: dict[str, dict]) -> str:
    """
    Builds the text block injected into the LLM prompt.
    @nav  → HARD RULES  (exact button→route wiring)
    @desc → LAYOUT INSTRUCTIONS  (strong guidance)
    """
    ctx           = nav_context.get(page_name, {})
    button_routes = ctx.get("button_routes", [])
    destinations  = ctx.get("destinations",  [])
    desc_hint     = ctx.get("desc_hint",     "")

    if not button_routes and not destinations and not desc_hint:
        return ""

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]

    # ── @nav: hard routing rules ──────────────────────────────────
    if button_routes:
        lines += [
            "NAVIGATION WIRING — HARD RULES (no exceptions):",
            f"  Wire these EXACT buttons on page '{page_name}' to these routes:",
            "",
        ]
        for br in button_routes:
            lines.append(
                f"  • Button / link whose text matches '{br['button_text']}'"
                f"  →  onClick: navigate(\"{br['route_path']}\")"
            )
        lines += [
            "",
            "  Use navigate() for buttons, <Link> for text links.",
            "  Match button text case-insensitively.",
        ]

    elif destinations:
        # Legacy fallback: page-list only
        lines += [
            "NAVIGATION WIRING — HARD RULES (no exceptions):",
            f"  Page '{page_name}' must provide navigation to:",
        ]
        for d in destinations:
            lines.append(f"  • '{d['frame_name']}'  →  route: \"{d['route_path']}\"")
        lines += [
            "",
            "  Match button/link text semantically to each destination.",
        ]

    # ── @desc: layout/content instructions ───────────────────────
    if desc_hint:
        if button_routes or destinations:
            lines.append("")
        lines += [
            "DESIGNER LAYOUT & BEHAVIOUR INSTRUCTIONS (follow closely):",
            f"  {desc_hint}",
        ]

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def get_button_route(button_text: str, page_name: str, nav_context: dict) -> Optional[str]:
    """
    Direct lookup: given a button's text and page, return its exact target route.
    Used by exporter.py renderers — no semantic guessing needed.
    """
    ctx           = nav_context.get(page_name, {})
    button_routes = ctx.get("button_routes", [])
    text_lower    = button_text.strip().lower()

    # 1. Exact text match
    for br in button_routes:
        if br["button_text"].strip().lower() == text_lower:
            return br["route_path"]

    # 2. Word-overlap match within button_routes
    text_words = set(w for w in re.split(r"[\s\-_]+", text_lower) if len(w) > 2)
    for br in button_routes:
        btn_words = set(w for w in re.split(r"[\s\-_]+", br["button_text"].lower()) if len(w) > 2)
        if text_words & btn_words:
            return br["route_path"]

    # 3. Legacy fallback: destination-list matching
    destinations = ctx.get("destinations", [])
    for d in destinations:
        dest_words = set(w for w in re.split(r"[\s\-_]+", d["frame_name"].lower()) if len(w) > 2)
        if text_words & dest_words:
            return d["route_path"]

    return None


# ─────────────────────────────────────────────────────────────────
# INTERNALS
# ─────────────────────────────────────────────────────────────────

def _parse_nav_hint(raw_hint: str, route_lookup: dict) -> tuple[list, list]:
    """
    Parse raw @nav: string into (button_routes, destinations).

    Handles:
      @nav: Cancel Button -> Custom API Page, Save Button -> Custom API Page
      @nav: Home Page, Profile Page
      @nav: Add Button -> Add Page, Home Page   (mixed)
    """
    body    = re.sub(r"(?i)@nav\s*:", "", raw_hint).strip()
    entries = [e.strip() for e in body.split(",") if e.strip()]

    button_routes: list[dict] = []
    destinations:  list[dict] = []
    seen_routes:   set[str]   = set()

    for entry in entries:
        if "->" in entry:
            # ── Button → Page ─────────────────────────────────────
            parts       = entry.split("->", 1)
            button_text = parts[0].strip()
            target_name = parts[1].strip()

            route_path = _resolve_route(target_name, route_lookup)
            if route_path:
                button_routes.append({
                    "button_text": button_text,
                    "route_path":  route_path,
                })
                if route_path not in seen_routes:
                    seen_routes.add(route_path)
                    destinations.append({
                        "frame_name": _title_case(target_name),
                        "route_path": route_path,
                    })
                log.debug("NAV", f"btn={button_text!r} → {route_path!r}")
            else:
                log.warn("NAV", f"Could not resolve target page: {target_name!r}")

        else:
            # ── Legacy: plain page name ───────────────────────────
            route_path = _resolve_route(entry, route_lookup)
            if route_path and route_path not in seen_routes:
                seen_routes.add(route_path)
                destinations.append({
                    "frame_name": _title_case(entry),
                    "route_path": route_path,
                })
                log.debug("NAV", f"dest={entry!r} → {route_path!r}")
            elif not route_path:
                log.warn("NAV", f"Could not resolve page: {entry!r}")

    return button_routes, destinations


def _build_route_lookup(routes: list[dict]) -> dict[str, str]:
    """normalised name → route_path (multiple keys per route for fuzzy matching)"""
    lookup: dict[str, str] = {}
    for r in routes:
        route_path = r["route_path"]
        name       = r["page_name"]

        lookup[_normalise(name)] = route_path

        slug = r.get("slug_path", "").lstrip("/")
        if slug:
            lookup[_normalise(slug.replace("-", " "))] = route_path

        for word in re.split(r"[\s\-_]+", name.lower()):
            if len(word) > 3:
                lookup.setdefault(_normalise(word), route_path)

    return lookup


def _resolve_route(name: str, route_lookup: dict[str, str]) -> Optional[str]:
    """Three-level match: exact → partial → word overlap."""
    key = _normalise(name)

    if key in route_lookup:
        return route_lookup[key]

    for lookup_key, path in route_lookup.items():
        if key in lookup_key or lookup_key in key:
            return path

    name_words  = set(w for w in re.split(r"\s+", key) if len(w) > 3)
    best_score, best_path = 0, None
    for lookup_key, path in route_lookup.items():
        score = len(name_words & set(w for w in re.split(r"\s+", lookup_key) if len(w) > 3))
        if score > best_score:
            best_score, best_path = score, path

    return best_path if best_score > 0 else None


def _normalise(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _title_case(text: str) -> str:
    return " ".join(w.capitalize() for w in text.strip().split())