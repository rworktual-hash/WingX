import os
import json
import re
import asyncio
from google import genai
from dotenv import load_dotenv

from themes import select_themes_for_prompt
from llm_utils import generate_content_with_retry
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY1"))

IMAGE_PROXY_BASE = os.getenv(
    "IMAGE_PROXY_BASE",
    "http://localhost:9000/api/image-proxy"
)

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────
CODING_SYSTEM_PROMPT = """
You are a senior UI/UX designer generating a professional Figma page layout.

Your task is to generate a JSON array of UI elements that will become the "children" of ONE tall Figma frame representing a full webpage.

The layout must resemble a modern, real production website.
It may also represent a real product screen, dashboard, CRM, inbox, modal, or application view.

════════════════════════════════════════
OUTPUT RULES
════════════════════════════════════════
1. Output ONLY a valid JSON array.
2. Do NOT output markdown.
3. Do NOT output explanations.
4. Do NOT output code fences.
5. Use ONLY simple JSON values (numbers, strings).
6. Colors must be HEX strings (example: "#111111").
7. lineHeight must be a plain number like 1.2
8. letterSpacing must be a plain number like 1.5
9. Coordinates must be integers.
10. All elements must include:
   type, name, x, y
11. Reusable UI can include:
   componentKey, componentName
12. Names must be semantic and stable, suitable for export. Avoid random labels like "Group 1", "Frame copy", or "Text 7".

════════════════════════════════════════
FRAME COORDINATE SYSTEM
════════════════════════════════════════

The frame origin is top-left:

x = 0
y = 0

The page width is always 1440px.

Safe content margins:
Left padding = 120px
Right padding = 120px

Primary content width:
- Landing pages / marketing sites: max ≈ 1200px
- Product screens / dashboards / CRMs / inbox views: use most of the frame width with realistic app gutters

All elements must stay inside the frame width.
Do NOT shrink a full app screen into a tiny centered layout.
If the screenshot includes empty space around the UI, ignore that padding and recreate the actual site/app surface at full size.
Never reproduce screenshot chrome, capture background, editor canvas, or outer framing that sits outside the real product UI.

════════════════════════════════════════
VERTICAL SPACING SYSTEM
════════════════════════════════════════

Use a consistent spacing rhythm:

Small spacing: 16
Medium spacing: 32
Large spacing: 64
Section spacing: 120

Sections should start roughly every 600–900px vertically.

Avoid cramped layouts.

══════════════════════════════════════════
ELEMENT TYPES:
══════════════════════════════════════════

RECTANGLE — backgrounds, cards, dividers, overlays:
{
  "type": "rectangle",
  "name": "Card Background",
  "x": 120, "y": 200,
  "width": 580, "height": 320,
  "backgroundColor": "#1A1A1A",
  "cornerRadius": 16,
  "opacity": 1
}

TEXT — headings, body, labels, captions:
{
  "type": "text",
  "name": "Hero Headline",
  "x": 120, "y": 250,
  "width": 700,
  "text": "Crafting digital\\nexperiences that\\nmatter.",
  "fontSize": 82,
  "fontWeight": "bold",
  "color": "#FFFFFF",
  "lineHeight": 1.1,
  "letterSpacing": 0
}

IMAGE — photos, thumbnails, illustrations:
{
  "type": "image",
  "name": "Hero Image",
  "x": 770, "y": 200,
  "width": 550, "height": 650,
  "borderRadius": 24,
  "backgroundColor": "#2A2A2A",
  "src": "PLACEHOLDER",
  "imageKeyword": "workspace"
}

BUTTON — CTAs, nav buttons, outlined links:
  Filled:
  {
    "type": "button",
    "name": "CTA Button",
    "x": 120, "y": 640,
    "width": 200, "height": 60,
    "text": "View My Work",
    "backgroundColor": "#4F46E5",
    "textColor": "#FFFFFF",
    "cornerRadius": 8,
    "fontSize": 16,
    "fontWeight": "semibold"
  }
  Outlined:
  {
    "type": "button",
    "name": "Secondary Button",
    "x": 340, "y": 640,
    "width": 180, "height": 60,
    "text": "Learn More",
    "backgroundColor": "transparent",
    "textColor": "#FFFFFF",
    "cornerRadius": 8,
    "borderColor": "#FFFFFF",
    "borderWidth": 1,
    "fontSize": 16,
    "fontWeight": "medium"
  }

GROUP — related elements like nav links, skill tags, icon rows:
{
  "type": "group",
  "name": "Skill Tags",
  "x": 120, "y": 2890,
  "children": [
    { "type": "rectangle", "name": "Tag BG 1", "x": 120, "y": 2890, "width": 100, "height": 40, "backgroundColor": "#2A2A2A", "cornerRadius": 20 },
    { "type": "text", "name": "Tag Text 1", "x": 150, "y": 2900, "text": "Figma", "fontSize": 14, "color": "#FFFFFF" }
  ]
}

COMPONENT — reusable UI that should become a real Figma master component with instances:
{
  "type": "component",
  "name": "Global Navigation",
  "componentName": "Navigation/Global Navigation",
  "componentKey": "navigation/global-navigation",
  "x": 0, "y": 0,
  "width": 1440, "height": 88,
  "backgroundColor": "#0F172A",
  "children": [
    { "type": "text", "name": "Brand Label", "x": 40, "y": 28, "text": "Acme CRM", "fontSize": 24, "fontWeight": "bold", "color": "#FFFFFF" },
    { "type": "component", "name": "Nav Item Dashboard", "componentName": "Navigation/Nav Item", "componentKey": "navigation/nav-item", "x": 1020, "y": 24, "width": 96, "height": 40, "children": [
      { "type": "text", "name": "Nav Label", "x": 20, "y": 10, "text": "Dashboard", "fontSize": 14, "fontWeight": "medium", "color": "#E5E7EB" }
    ] }
  ]
}

Inside a COMPONENT, child coordinates are relative to the component's own origin, not the page origin.

If the same reusable button, nav item, sidebar block, or shell component appears more than once, reuse the same componentKey/componentName.
For reusable buttons, you may also keep type="button" and add componentKey/componentName.



════════════════════════════════════════
STRUCTURE REQUIREMENTS
════════════════════════════════════════

Generate the structure that matches the planned frame.

- If the frame is a landing page or marketing website, use the appropriate website sections.
- If the frame is a dashboard / CRM / inbox / product screen, generate a full-fidelity application layout instead.
- Follow the frame description exactly instead of defaulting to a landing page.

════════════════════════════════════════
NAVBAR DESIGN
════════════════════════════════════════

Height: 80–90px

Left side:
Logo text

Right side:
Navigation links

Optional CTA button on far right.

When a navbar is reusable, output it as a reusable COMPONENT and keep its links INSIDE that component.
Navigation links should be reusable components or reusable button-like elements, not loose text floating outside the nav structure.

Example nav items:
Home
About
Services
Pricing
Contact

════════════════════════════════════════
HERO SECTION
════════════════════════════════════════

Starts around y ≈ 140–180

Layout:
Left column:
Headline
Subtext
CTA buttons

Right column:
Large hero image

Hero headline size:
fontSize 72–96
fontWeight "bold"
lineHeight 1.0–1.15

Subtext:
fontSize 18–20
color slightly muted

════════════════════════════════════════
CARD DESIGN
════════════════════════════════════════

Cards must include:

background rectangle
title text
description text
optional icon or image

Card properties:
cornerRadius: 16–20
backgroundColor slightly lighter than page background

Use grid layouts for cards:
2 or 3 columns

════════════════════════════════════════
TYPOGRAPHY HIERARCHY
════════════════════════════════════════

Hero headline:
fontSize 72–96
fontWeight "bold"

Section heading:
fontSize 44–56
fontWeight "bold"

Subheading:
fontSize 24–30
fontWeight "semibold"

Body text:
fontSize 16–18
color muted

Labels / captions:
fontSize 12–14
fontWeight "bold"
letterSpacing 2

════════════════════════════════════════
IMAGE RULES
════════════════════════════════════════

Only use `image` elements for REAL content imagery such as:
- hero/product illustrations
- marketing banners
- article/gallery thumbnails
- full profile photos when the design clearly uses real photos

Do NOT use `image` elements for:
- icons
- SVG-like UI symbols
- mic / phone / speaker / trash / edit / menu / search / bell / close controls
- tiny visuals inside buttons
- badges
- avatars that can be initials
- logos that can be rendered as text or simple shapes

All real content images must include:

src: "PLACEHOLDER"

imageKeyword describing the image.

Examples:
"tech startup office"
"saas dashboard ui"
"developer coding laptop"
"team meeting"

════════════════════════════════════════
FOOTER
════════════════════════════════════════

Near the bottom of the frame include:

Divider line
Company name
Navigation links
Social links

Divider example:
rectangle height = 1
opacity ≈ 0.2

══════════════════════════════════════════
DESIGN QUALITY REQUIREMENTS:
══════════════════════════════════════════
- Use the provided theme colors throughout: background, surfaces, accent, text
- Respect the screenshot/reference proportions and overall scale
- Ignore screenshot capture padding or blank margins around the real UI
- Recreate the actual UI surface so it fills the frame naturally
- For product screens, use realistic outer gutters (roughly 24-48px), not huge empty margins
- Never compress a product screen into a tiny centered website
- Treat the project like one connected product system, not a set of unrelated screens
- Keep shared navigation destinations, ordering, and shell structure consistent across related frames
- If a modal/menu/drawer/popover is open, preserve the same underlying base screen shell from the parent state
- Typography hierarchy:
    Hero heading:  fontSize 72–96, fontWeight "bold", lineHeight 1.0–1.15
    Section title: fontSize 42–56, fontWeight "bold"
    Sub-heading:   fontSize 24–32, fontWeight "semibold"
    Body text:     fontSize 16–20, color slightly muted (e.g. #A0A0A0)
    Labels/caps:   fontSize 12–14, fontWeight "bold", letterSpacing 2
- Layout:
    Navbar:       y=0, height=80–90px. Logo at x=120 y≈28. Nav links right side. CTA button far right.
    Hero:         Starts at y≈150. Big heading left, hero image right.
    Sections:     120px padding top/bottom. Section label (caps) then big heading then content.
    Cards:        backgroundColor slightly lighter than page bg. cornerRadius 12–20.
    Footer:       Near bottom. Thin divider line (rectangle h=1), copyright left, social links right.
- All coordinates MUST be inside the frame (0 to frame width, 0 to frame height)
- Include REALISTIC content for the project domain — real names, descriptions, copy
- Make it look like a professional real website, not a wireframe
- TEXT FIT RULES:
    - Do not create text boxes so narrow that ordinary labels or sentences break awkwardly
    - Expand text widths or surrounding containers so text does not collide with nearby elements
    - Short labels, tabs, buttons, chips, table cells, and names should stay on one line whenever reasonably possible
    - If a paragraph wraps, ensure its container height and the spacing below it prevent overlap
- ASSET FIT RULES:
    - Tiny UI visuals should be rendered as shapes/text/icon-like elements, not external images
    - Use images only when the element is clearly content media, not a control
    - Never use meaningless placeholder labels like P1, C2, IMG, or random initials for icons
    - Utility controls should use simple vector-like icon groups or compact semantic labels
"""


def build_theme_block(theme: dict) -> str:
    colors = theme.get("colors", [])
    roles  = [
        "Primary Background  (page/frame bg)",
        "Secondary Background  (cards, panels, surfaces)",
        "Primary Accent  (buttons, links, highlights, icons)",
        "Secondary Accent  (secondary buttons, badges, tags)",
        "Text / Foreground  (headings, body copy)",
    ]
    lines = [
        f"THEME: {theme['name']} — {theme['description']}",
        f"Category: {theme['category']} | Animation hint: {theme['animation']}",
        "",
        "Apply these colors throughout:",
    ]
    for role, color in zip(roles, colors):
        lines.append(f"  {color}  →  {role}")
    lines.append("")
    lines.append(f"Frame backgroundColor = \"{colors[0]}\"")
    lines.append(f"Primary Accent color  = \"{colors[2]}\" (use for buttons, section labels, accents)")
    return "\n".join(lines)


def build_image_url(keyword: str, width: int, height: int) -> str:
    seed       = abs(hash(keyword)) % 9999
    picsum_url = f"https://picsum.photos/seed/{seed}/{width}/{height}"
    encoded    = picsum_url.replace(":", "%3A").replace("/", "%2F")
    return f"{IMAGE_PROXY_BASE}?url={encoded}&width={width}&height={height}"


def inject_image_urls(children: list) -> list:
    """Replace src='PLACEHOLDER' with real proxy URLs recursively."""
    for el in children:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "image" and el.get("src") == "PLACEHOLDER":
            keyword = el.get("imageKeyword", el.get("name", "photo"))
            w       = int(el.get("width",  400))
            h       = int(el.get("height", 300))
            el["src"] = build_image_url(keyword, w, h)
        if "children" in el and isinstance(el["children"], list):
            inject_image_urls(el["children"])
    return children


ICON_KEYWORDS = {
    "mic", "microphone", "speaker", "call", "phone", "trash", "delete", "edit",
    "transfer", "search", "notification", "bell", "menu", "kebab", "more",
    "close", "mute", "unmute", "hold", "resume", "voicemail", "play", "pause",
    "arrow", "chevron", "caret", "download", "upload", "filter", "sort", "note",
    "settings", "gear", "avatar", "profile", "user", "icon", "svg", "badge",
    "toggle", "tab", "dots", "ellipsis", "logo",
}
REAL_IMAGE_HINTS = {
    "photo", "hero", "banner", "thumbnail", "cover", "illustration", "gallery",
    "screenshot", "product image", "product shot", "product", "item image",
    "team", "workspace", "device", "laptop", "headphones", "camera",
}
ICON_LABEL_MAP = {
    "search": "SRCH",
    "menu": "...",
    "kebab": "...",
    "more": "...",
    "close": "X",
    "delete": "DEL",
    "trash": "DEL",
    "edit": "EDIT",
    "note": "NOTE",
    "call": "TEL",
    "phone": "TEL",
    "mic": "MIC",
    "speaker": "SPK",
    "transfer": "TRF",
    "filter": "FLT",
    "sort": "SORT",
    "play": "PLAY",
    "pause": "II",
    "download": "DL",
    "upload": "UL",
    "hold": "HOLD",
    "resume": "GO",
    "bell": "BELL",
    "notification": "BELL",
    "settings": "CFG",
    "gear": "CFG",
}


def _ascii_initials(text: str, limit: int = 2) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", text or "")
    if not parts:
        return "UI"
    initials = "".join(p[0].upper() for p in parts[:limit])
    return initials or "UI"


def _infer_asset_kind(el: dict) -> str:
    name = (el.get("name") or "")
    keyword = (el.get("imageKeyword") or "")
    hay = f"{name} {keyword}".lower()
    w = int(el.get("width", 0) or 0)
    h = int(el.get("height", 0) or 0)
    small = max(w, h) <= 96 or (w * h) <= 12000

    if any(k in hay for k in ["avatar", "profile", "user photo", "customer photo"]):
        return "avatar"
    if any(k in hay for k in ["logo", "brand"]):
        return "logo"
    if any(k in hay for k in REAL_IMAGE_HINTS) and (not small or min(w, h) >= 72):
        return "content"
    if any(k in hay for k in ICON_KEYWORDS) or small:
        return "icon"
    return "content"


def _icon_keyword(hay: str) -> str:
    ordered = [
        "search", "menu", "kebab", "more", "close", "delete", "trash", "edit",
        "note", "call", "phone", "mic", "speaker", "transfer", "filter", "sort",
        "play", "pause", "download", "upload", "hold", "resume", "bell",
        "notification", "settings", "gear", "chevron", "arrow", "back", "next",
    ]
    for key in ordered:
        if key in hay:
            return key
    return ""


def _base_icon_children(x: int, y: int, w: int, h: int, bg: str) -> list[dict]:
    return [{
        "type": "rectangle",
        "name": "Icon BG",
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "backgroundColor": bg,
        "cornerRadius": min(12, max(6, min(w, h) // 3)),
    }]


def _make_icon_group(el: dict) -> dict:
    x = int(el.get("x", 0) or 0)
    y = int(el.get("y", 0) or 0)
    w = max(20, int(el.get("width", 24) or 24))
    h = max(20, int(el.get("height", 24) or 24))
    hay = f"{el.get('name', '')} {el.get('imageKeyword', '')}".lower()
    kind = _icon_keyword(hay)
    bg = "#EEF2FF" if max(w, h) <= 40 else "#E5E7EB"
    fg = "#4F46E5" if max(w, h) <= 40 else "#374151"
    inset = max(4, min(w, h) // 5)
    mid_x = x + (w // 2)
    mid_y = y + (h // 2)
    children = _base_icon_children(x, y, w, h, bg)

    if kind in {"menu", "kebab", "more"}:
        dot = max(3, min(5, min(w, h) // 5))
        gap = max(dot + 2, w // 4)
        start_x = mid_x - gap
        for i in range(3):
            children.append({
                "type": "ellipse",
                "name": "Menu Dot",
                "x": start_x + i * gap,
                "y": mid_y - dot // 2,
                "width": dot,
                "height": dot,
                "backgroundColor": fg,
            })
    elif kind == "search":
        circle = min(w, h) - inset * 2 - 4
        children.extend([
            {
                "type": "ellipse",
                "name": "Search Ring",
                "x": x + inset,
                "y": y + inset,
                "width": max(10, circle),
                "height": max(10, circle),
                "backgroundColor": "transparent",
                "borderColor": fg,
                "borderWidth": 2,
            },
            {
                "type": "line",
                "name": "Search Handle",
                "x": x + inset + circle - 2,
                "y": y + inset + circle - 1,
                "width": max(8, w // 4),
                "color": fg,
                "strokeWeight": 2,
                "rotation": 42,
            },
        ])
    elif kind == "close":
        children.extend([
            {
                "type": "line",
                "name": "Close Slash 1",
                "x": x + inset,
                "y": y + inset,
                "width": max(10, w - inset * 2),
                "color": fg,
                "strokeWeight": 2,
                "rotation": 45,
            },
            {
                "type": "line",
                "name": "Close Slash 2",
                "x": x + inset,
                "y": y + h - inset,
                "width": max(10, w - inset * 2),
                "color": fg,
                "strokeWeight": 2,
                "rotation": -45,
            },
        ])
    elif kind in {"delete", "trash"}:
        body_w = max(10, w - inset * 2)
        body_h = max(10, h - inset * 2 - 4)
        children.extend([
            {
                "type": "rectangle",
                "name": "Trash Body",
                "x": x + inset,
                "y": y + inset + 4,
                "width": body_w,
                "height": body_h,
                "backgroundColor": "transparent",
                "borderColor": fg,
                "borderWidth": 2,
                "cornerRadius": 4,
            },
            {
                "type": "line",
                "name": "Trash Lid",
                "x": x + inset - 1,
                "y": y + inset + 2,
                "width": body_w + 2,
                "color": fg,
                "strokeWeight": 2,
            },
            {
                "type": "line",
                "name": "Trash Handle",
                "x": mid_x - 4,
                "y": y + inset - 1,
                "width": 8,
                "color": fg,
                "strokeWeight": 2,
            },
        ])
    elif kind == "mic":
        children.extend([
            {
                "type": "ellipse",
                "name": "Mic Head",
                "x": mid_x - max(4, w // 8),
                "y": y + inset,
                "width": max(8, w // 4),
                "height": max(10, h // 3),
                "backgroundColor": "transparent",
                "borderColor": fg,
                "borderWidth": 2,
            },
            {
                "type": "line",
                "name": "Mic Stem",
                "x": mid_x,
                "y": y + inset + max(10, h // 3),
                "width": max(8, h // 5),
                "color": fg,
                "strokeWeight": 2,
                "rotation": 90,
            },
            {
                "type": "line",
                "name": "Mic Base",
                "x": mid_x - max(6, w // 5),
                "y": y + h - inset - 4,
                "width": max(12, w // 2),
                "color": fg,
                "strokeWeight": 2,
            },
        ])
    elif kind == "speaker":
        children.extend([
            {
                "type": "rectangle",
                "name": "Speaker Body",
                "x": x + inset,
                "y": mid_y - max(5, h // 6),
                "width": max(6, w // 5),
                "height": max(10, h // 3),
                "backgroundColor": fg,
                "cornerRadius": 2,
            },
            {
                "type": "line",
                "name": "Speaker Wave 1",
                "x": x + inset + max(8, w // 4),
                "y": mid_y - max(6, h // 5),
                "width": max(8, w // 4),
                "color": fg,
                "strokeWeight": 2,
                "rotation": 55,
            },
            {
                "type": "line",
                "name": "Speaker Wave 2",
                "x": x + inset + max(8, w // 4),
                "y": mid_y + max(3, h // 10),
                "width": max(8, w // 4),
                "color": fg,
                "strokeWeight": 2,
                "rotation": -55,
            },
        ])
    elif kind in {"filter", "sort"}:
        widths = [w - inset * 2, max(10, w - inset * 2 - 6), max(8, w - inset * 2 - 12)]
        for idx, line_w in enumerate(widths):
            children.append({
                "type": "line",
                "name": "Filter Line",
                "x": x + inset + idx * 2,
                "y": y + inset + idx * max(5, h // 5),
                "width": line_w,
                "color": fg,
                "strokeWeight": 2,
            })
    elif kind in {"edit", "note"}:
        children.extend([
            {
                "type": "line",
                "name": "Pencil",
                "x": x + inset,
                "y": y + h - inset - 4,
                "width": max(12, w - inset * 2),
                "color": fg,
                "strokeWeight": 3,
                "rotation": -35,
            },
            {
                "type": "rectangle",
                "name": "Edit Tip",
                "x": x + w - inset - 6,
                "y": y + inset + 4,
                "width": 6,
                "height": 6,
                "backgroundColor": fg,
                "cornerRadius": 2,
            },
        ])
    else:
        label = ICON_LABEL_MAP.get(kind or "", _ascii_initials((el.get("name") or el.get("imageKeyword") or "icon"), 3))
        children.append({
            "type": "text",
            "name": (el.get("name") or "Icon") + " Label",
            "x": x + max(4, w // 6),
            "y": y + max(4, h // 5),
            "width": max(12, w - max(8, w // 3)),
            "text": label,
            "fontSize": max(9, min(13, int(min(w, h) * 0.34))),
            "fontWeight": "bold",
            "color": fg,
            "lineHeight": 1.0,
            "letterSpacing": 0,
        })

    return {
        "type": "group",
        "name": (el.get("name") or "UI Icon") + " Group",
        "x": x,
        "y": y,
        "children": children
    }


def _make_avatar_group(el: dict) -> dict:
    x = int(el.get("x", 0) or 0)
    y = int(el.get("y", 0) or 0)
    size = max(24, min(64, int(max(el.get("width", 40) or 40, el.get("height", 40) or 40))))
    initials = _ascii_initials(el.get("name") or el.get("imageKeyword") or "User", 2)
    return {
        "type": "group",
        "name": (el.get("name") or "Avatar") + " Group",
        "x": x,
        "y": y,
        "children": [
            {
                "type": "ellipse",
                "name": (el.get("name") or "Avatar") + " Circle",
                "x": x,
                "y": y,
                "width": size,
                "height": size,
                "backgroundColor": "#6366F1",
            },
            {
                "type": "text",
                "name": (el.get("name") or "Avatar") + " Initials",
                "x": x + max(6, size // 4),
                "y": y + max(5, size // 4),
                "width": max(14, size - max(12, size // 2)),
                "text": initials,
                "fontSize": max(10, min(16, int(size * 0.34))),
                "fontWeight": "bold",
                "color": "#FFFFFF",
                "lineHeight": 1.0,
                "letterSpacing": 0,
            }
        ]
    }


def _make_logo_group(el: dict) -> dict:
    x = int(el.get("x", 0) or 0)
    y = int(el.get("y", 0) or 0)
    w = max(48, int(el.get("width", 96) or 96))
    h = max(24, int(el.get("height", 32) or 32))
    label = _ascii_initials(el.get("name") or el.get("imageKeyword") or "Logo", 3)
    return {
        "type": "group",
        "name": (el.get("name") or "Logo") + " Group",
        "x": x,
        "y": y,
        "children": [
            {
                "type": "rectangle",
                "name": (el.get("name") or "Logo") + " BG",
                "x": x,
                "y": y,
                "width": h,
                "height": h,
                "backgroundColor": "#111827",
                "cornerRadius": 8,
            },
            {
                "type": "text",
                "name": (el.get("name") or "Logo") + " Mark",
                "x": x + max(5, h // 5),
                "y": y + max(4, h // 5),
                "width": max(12, h - max(8, h // 3)),
                "text": label[:2],
                "fontSize": max(10, min(14, int(h * 0.38))),
                "fontWeight": "bold",
                "color": "#FFFFFF",
                "lineHeight": 1.0,
                "letterSpacing": 0,
            },
            {
                "type": "text",
                "name": (el.get("name") or "Logo") + " Text",
                "x": x + h + 8,
                "y": y + max(3, h // 6),
                "width": max(24, w - h - 8),
                "text": _pretty_logo_text(el.get("name") or el.get("imageKeyword") or "Brand"),
                "fontSize": max(11, min(16, int(h * 0.42))),
                "fontWeight": "semibold",
                "color": "#111827",
                "lineHeight": 1.0,
                "letterSpacing": 0,
            },
        ]
    }


def _pretty_logo_text(text: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", text or "")
    if not parts:
        return "Brand"
    return " ".join(p[:1].upper() + p[1:] for p in parts[:2])


def _build_navigation_block(page: dict) -> str:
    nav = page.get("project_navigation") or page.get("navigation") or {}
    primary = nav.get("primary_links", []) or []
    active = (page.get("navigation") or {}).get("active_label", "")
    layout = nav.get("layout", "topbar")
    if not primary:
        return ""

    nav_order = " | ".join(primary)
    placement = (
        "Render this as one horizontal top navigation row."
        if layout == "topbar"
        else "Keep the navigation placement consistent with the shared product shell."
    )
    return (
        f"\nPROJECT NAVIGATION SYSTEM:\n"
        f"  Shared nav layout: {layout}\n"
        f"  Stable primary destinations: {nav_order}\n"
        f"  Required primary link count: {len(primary)}\n"
        f"  Active destination for this frame: {active or primary[0]}\n"
        f"  {placement}\n"
        f"  Render all primary destinations on every relevant page in this same order.\n"
        f"  Keep the same destination labels and ordering across related screens.\n"
        f"  Do not invent extra top-level nav items or switch to a different IA unless the reference clearly requires it.\n"
    )


def _build_journey_block(page: dict) -> str:
    journey = page.get("journey") or {}
    prev_screen = journey.get("previous_screen", "")
    next_screen = journey.get("next_screen", "")
    branch_root = journey.get("branch_root", "") or page.get("branch_root", "")
    branch_trigger = journey.get("branch_trigger", "") or page.get("branch_trigger", "")
    branch_goal = journey.get("branch_goal", "") or page.get("branch_goal", "")
    branch_kind = journey.get("branch_kind", "") or page.get("branch_kind", "")
    if not prev_screen and not next_screen:
        return ""
    return (
        f"\nFLOW CONTEXT:\n"
        f"  Flow row: {page.get('flow_group', page.get('feature_group', ''))}\n"
        f"  Current step: {page.get('flow_group_step', page.get('flow_step', ''))} of {page.get('flow_group_total', page.get('flow_total', ''))}\n"
        f"  Branch root: {branch_root or page.get('feature_group', '')}\n"
        f"  Branch trigger: {branch_trigger or 'None'}\n"
        f"  Branch goal: {branch_goal or 'None'}\n"
        f"  Branch kind: {branch_kind or 'flow'}\n"
        f"  Previous screen: {prev_screen or 'None'}\n"
        f"  Next screen: {next_screen or 'None'}\n"
        f"  Preserve the same shell and include the trigger that naturally leads to the next screen when appropriate.\n"
    )


def _build_memory_block(page: dict) -> str:
    memory = page.get("memory_context") or {}
    if not memory:
        return ""

    preferred_theme = memory.get("preferred_theme") or {}
    pages = memory.get("pages", []) or []
    nav_model = memory.get("navigation_model") or {}
    page_lines = []
    for item in pages[:12]:
        if not isinstance(item, dict):
            continue
        line = " | ".join([part for part in [
            item.get("screen_title") or item.get("name") or "Screen",
            item.get("feature_group") or "",
            item.get("flow_group") or "",
        ] if part])
        if line:
            page_lines.append("  - " + line)

    memory_lines = []
    if preferred_theme:
        memory_lines.append(
            "  Preferred theme: " +
            str(preferred_theme.get("name", "")) +
            (" | colors: " + ", ".join(preferred_theme.get("colors", [])[:5]) if preferred_theme.get("colors") else "")
        )
    if nav_model.get("primary_links"):
        memory_lines.append(
            "  Shared nav: " + ", ".join(nav_model.get("primary_links", [])[:10]) +
            f" | layout={nav_model.get('layout', '')}"
        )
    if page.get("followup_source_frame_name"):
        memory_lines.append("  Source frame: " + str(page.get("followup_source_frame_name")))
    if page.get("branch_trigger"):
        memory_lines.append("  Trigger element: " + str(page.get("branch_trigger")))
    if page_lines:
        memory_lines.append("  Existing screens:")
        memory_lines.extend(page_lines)

    if not memory_lines:
        return ""
    return "\nPROJECT MEMORY:\n" + "\n".join(memory_lines) + "\n"


def _json_for_prompt(value: object, max_chars: int = 18000) -> str:
    raw = json.dumps(value, indent=2, ensure_ascii=True)
    return raw if len(raw) <= max_chars else (raw[:max_chars] + "\n...TRUNCATED...")


def _build_attachment_context_block(page: dict) -> str:
    attachment = page.get("attachment_context") or {}
    if not attachment:
        return ""

    shell = attachment.get("shell_nodes") or {}
    lines = [
        "\nATTACHMENT STRUCTURE CONTEXT:",
        "  The selected Figma trees below are the source of truth for shell/layout preservation.",
        "  Keep the same frame dimensions, shell structure, nav sizes, and reusable component architecture unless the prompt explicitly asks for a change.",
        "  Do not simplify real buttons/navs/panels into loose text.",
        "  Preserve dividers, table lines, and structural separators when they are present in the reference trees.",
        "  Emit reusable UI as COMPONENT nodes or as button nodes with componentKey/componentName.",
    ]
    if shell.get("nav"):
        lines.append(f"  Global nav lock: {shell.get('nav')}")
    if shell.get("secondary_nav"):
        lines.append(f"  Secondary nav lock: {shell.get('secondary_nav')}")
    if shell.get("sidebar"):
        lines.append(f"  Sidebar lock: {shell.get('sidebar')}")
    if shell.get("table_like"):
        lines.append(f"  Table/list reference: {shell.get('table_like')}")

    if attachment.get("primary_tree"):
        lines.append("\nPRIMARY PAGE TREE:")
        lines.append(_json_for_prompt(attachment.get("primary_tree"), 22000))
    if attachment.get("context_trees"):
        lines.append("\nCONTEXT PAGE TREES:")
        lines.append(_json_for_prompt(attachment.get("context_trees"), 16000))
    if attachment.get("component_trees"):
        lines.append("\nREUSABLE COMPONENT TREES:")
        lines.append(_json_for_prompt(attachment.get("component_trees"), 16000))

    return "\n".join(lines) + "\n"


def _slugify_component(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "item"


def _pretty_component_words(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").replace("/", " ").replace("-", " ")).strip()
    if not text:
        return "Item"
    return " ".join(part[:1].upper() + part[1:] for part in text.split(" ") if part)


def _normalized_color(value) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text == "transparent":
        return "transparent"
    if not text.startswith("#"):
        return text
    if len(text) == 4:
        return "#" + "".join(ch * 2 for ch in text[1:])
    return text


def _estimate_text_box(text_value: str, font_size: int) -> tuple[int, int]:
    clean = str(text_value or "").strip()
    size = max(10, int(font_size or 16))
    width = max(24, int(len(clean) * max(7, size * 0.55)))
    height = max(18, int(size * 1.4))
    return width, height


def _collect_text_values(node: dict, limit: int = 8) -> list[str]:
    values: list[str] = []

    def walk(current: dict):
        if len(values) >= limit or not isinstance(current, dict):
            return
        node_type = str(current.get("type", "")).lower()
        if node_type in {"text", "button"}:
            text_value = str(current.get("text", "") or "").strip()
            if text_value:
                values.append(text_value)
        for child in current.get("children", []) or []:
            if len(values) >= limit:
                break
            walk(child)

    walk(node)
    return values


def _primary_label(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    if str(node.get("type", "")).lower() == "button":
        return str(node.get("text", "") or "").strip()
    for value in _collect_text_values(node, limit=3):
        if value:
            return value.strip()
    return ""


def _button_style_variant(node: dict) -> str:
    background = _normalized_color(node.get("backgroundColor"))
    border = _normalized_color(node.get("borderColor"))
    if background in {"", "transparent"}:
        return "outline" if border else "ghost"
    if border and background in {"#ffffff", "#f8f8f8", "#f5f5f5"}:
        return "outline"
    return "filled"


def _button_kind(label: str) -> str:
    lower = re.sub(r"\s+", " ", (label or "").strip().lower())
    if not lower:
        return "button"
    for token, kind in [
        ("cancel", "cancel"),
        ("close", "close"),
        ("back", "back"),
        ("save", "save"),
        ("submit", "submit"),
        ("add ", "add"),
        ("create", "create"),
        ("edit", "edit"),
        ("delete", "delete"),
        ("remove", "remove"),
        ("view", "view"),
        ("details", "details"),
        ("run", "run"),
        ("assign", "assign"),
        ("login", "login"),
        ("sign in", "login"),
        ("reset password", "reset-password"),
    ]:
        if token in lower:
            return kind
    return _slugify_component(lower)


def _classify_reusable_role(node: dict, parent_role: str = "") -> str:
    if not isinstance(node, dict):
        return ""

    node_type = str(node.get("type", "")).lower()
    name_text = re.sub(r"\s+", " ", str(node.get("name", "") or "")).strip()
    name_norm = name_text.lower()
    width = int(node.get("width", 0) or 0)
    height = int(node.get("height", 0) or 0)
    labels = [value.lower() for value in _collect_text_values(node, limit=6)]
    label_blob = " ".join(labels)

    if node_type == "button":
        return "button"

    if node_type == "text":
        short_text = str(node.get("text", "") or "").strip()
        if parent_role in {"secondary-nav", "sidebar", "tabs"} and short_text and len(short_text) <= 28:
            return "nav-item"
        if parent_role == "global-nav" and short_text and len(short_text) <= 24 and int(node.get("x", 0) or 0) > 56:
            return "nav-item"
        return ""

    if any(token in name_norm for token in ["sidebar", "side nav", "sidenav", "left rail"]):
        return "sidebar"
    if any(token in name_norm for token in ["secondary nav", "secondary navigation", "secondary tabs", "sub nav", "tabs", "tab bar"]):
        return "secondary-nav"
    if any(token in name_norm for token in ["global nav", "global navigation", "top nav", "top bar", "top navigation", "header nav", "navbar", "navigation/global"]):
        return "global-nav"
    if any(token in name_norm for token in ["side panel", "detail panel", "drawer panel"]):
        return "side-panel"

    if width >= 960 and 40 <= height <= 120 and (len(labels) >= 2 or any(token in label_blob for token in ["dashboard", "settings", "users", "delivery", "console", "results", "knowledge"])):
        return "secondary-nav"
    if width >= 960 and 40 <= height <= 120 and (background := _normalized_color(node.get("backgroundColor"))) and background != "transparent":
        return "global-nav"
    if width <= 420 and height >= 240 and any(token in label_blob for token in ["menu", "settings", "users", "roles", "permissions", "dashboard"]):
        return "sidebar"

    is_small_action = 24 <= height <= 72 and 56 <= width <= 320
    if is_small_action:
        label = _primary_label(node).lower()
        if label and len(label) <= 40 and not any(token in name_norm for token in ["table", "row", "card", "panel", "nav", "sidebar"]):
            return "button"

    return ""


def _component_variant_signature(node: dict, role: str) -> str:
    labels = [_slugify_component(text) for text in _collect_text_values(node, limit=6)]
    labels = [label for label in labels if label]
    width = int(node.get("width", 0) or 0)
    height = int(node.get("height", 0) or 0)
    color = _normalized_color(node.get("backgroundColor"))

    if role == "button":
        label = _primary_label(node)
        kind = _button_kind(label)
        style = _button_style_variant(node)
        return f"{style}/{kind}"

    if role == "nav-item":
        label = _primary_label(node) or (labels[0] if labels else "item")
        return _slugify_component(label)

    variant_parts = []
    if role in {"secondary-nav", "sidebar"} and labels:
        variant_parts.append("-".join(labels[:5]))
    elif role == "global-nav":
        variant_parts.append("-".join(labels[:4]) if labels else "")

    if not variant_parts or not variant_parts[0]:
        variant_parts = [f"{width}x{height}" if width and height else "default"]

    if color and color not in {"", "transparent"}:
        variant_parts.append(color.replace("#", ""))
    return _slugify_component("-".join(part for part in variant_parts if part))


def _apply_component_identity(node: dict, role: str) -> dict:
    updated = dict(node)
    variant = _component_variant_signature(updated, role)

    if role == "button":
        label = _primary_label(updated) or updated.get("name") or "Button"
        kind = _button_kind(label)
        updated["componentName"] = f"Actions/{_pretty_component_words(kind)} Button"
        updated["componentKey"] = f"actions/button/{variant}"
        updated["name"] = updated.get("name") or f"{_pretty_component_words(kind)} Button"
        return updated

    if role == "nav-item":
        label = _primary_label(updated) or updated.get("name") or "Nav Item"
        updated["componentName"] = f"Navigation/Nav Item/{_pretty_component_words(label)}"
        updated["componentKey"] = f"navigation/nav-item/{variant}"
        updated["name"] = updated.get("name") or f"Nav Item {label}"
        return updated

    component_meta = {
        "global-nav": ("Navigation/Global Top Bar", "navigation/global-top-bar"),
        "secondary-nav": ("Navigation/Secondary Nav", "navigation/secondary-nav"),
        "sidebar": ("Navigation/Sidebar", "navigation/sidebar"),
        "side-panel": ("Layout/Side Panel", "layout/side-panel"),
    }.get(role)
    if component_meta:
        comp_name, comp_key = component_meta
        updated["componentName"] = f"{comp_name}/{_pretty_component_words(variant)}"
        updated["componentKey"] = f"{comp_key}/{variant}"
        return updated

    return updated


def _wrap_text_as_component(node: dict, parent_role: str) -> dict:
    label = str(node.get("text", "") or "").strip()
    if not label:
        return dict(node)

    font_size = int(node.get("fontSize", 16) or 16)
    width = int(node.get("width", 0) or 0)
    height = int(node.get("height", 0) or 0)
    if width <= 0 or height <= 0:
        est_w, est_h = _estimate_text_box(label, font_size)
        width = width or est_w
        height = height or est_h

    child = dict(node)
    child["x"] = 0
    child["y"] = 0

    wrapped = {
        "type": "component",
        "name": f"Nav Item {label}",
        "componentName": f"Navigation/Nav Item/{_pretty_component_words(label)}",
        "componentKey": f"navigation/nav-item/{_slugify_component(label)}",
        "x": int(node.get("x", 0) or 0),
        "y": int(node.get("y", 0) or 0),
        "width": max(1, width),
        "height": max(1, height),
        "backgroundColor": "transparent",
        "children": [child],
    }
    if parent_role == "sidebar":
        wrapped["componentName"] = f"Navigation/Sidebar Item/{_pretty_component_words(label)}"
        wrapped["componentKey"] = f"navigation/sidebar-item/{_slugify_component(label)}"
    return wrapped


def enforce_reusable_structure(children: list, parent_role: str = "") -> list:
    normalized = []
    for el in children:
        if not isinstance(el, dict):
            continue

        updated = dict(el)
        role = _classify_reusable_role(updated, parent_role)

        if isinstance(updated.get("children"), list):
            updated["children"] = enforce_reusable_structure(updated["children"], role or parent_role)

        if role == "nav-item" and str(updated.get("type", "")).lower() == "text":
            updated = _wrap_text_as_component(updated, parent_role)
            role = "nav-item"

        if role and str(updated.get("type", "")).lower() not in {"image", "line"}:
            updated = _apply_component_identity(updated, role)

        normalized.append(updated)
    return normalized


def stabilize_generated_children(children: list) -> list:
    stabilized = []
    for el in children:
        if not isinstance(el, dict):
            continue

        updated = dict(el)
        if updated.get("type") == "group" and isinstance(updated.get("children"), list):
            updated["children"] = stabilize_generated_children(updated["children"])

        if updated.get("type") == "text":
            text_value = str(updated.get("text", "") or "")
            font_size = int(updated.get("fontSize", 16) or 16)
            width = int(updated.get("width", 0) or 0)
            approx_width = int(len(text_value.strip()) * max(7, font_size * 0.52))
            if width and text_value.strip() and "\n" not in text_value:
                if len(text_value) <= 28 and width < approx_width:
                    updated["width"] = min(max(width, approx_width + 12), 520)
                elif len(text_value) > 28 and width < int(approx_width * 0.72):
                    updated["width"] = min(max(width, int(approx_width * 0.72) + 16), 640)

        if updated.get("type") == "button":
            text_value = str(updated.get("text", "") or "")
            width = int(updated.get("width", 0) or 0)
            font_size = int(updated.get("fontSize", 16) or 16)
            approx_width = int(len(text_value.strip()) * max(7, font_size * 0.55)) + 36
            if width and text_value.strip() and width < approx_width:
                updated["width"] = min(max(width, approx_width), 360)

        stabilized.append(updated)
    return stabilized


def sanitize_generated_children(children: list) -> list:
    sanitized = []
    for el in children:
        if not isinstance(el, dict):
            continue

        if el.get("type") == "group" and isinstance(el.get("children"), list):
            el = {**el, "children": sanitize_generated_children(el["children"])}

        if el.get("type") == "image":
            kind = _infer_asset_kind(el)
            if kind == "icon":
                sanitized.append(_make_icon_group(el))
                continue
            if kind == "avatar":
                sanitized.append(_make_avatar_group(el))
                continue
            if kind == "logo":
                sanitized.append(_make_logo_group(el))
                continue

        sanitized.append(el)
    return sanitized


def clean_json_response(raw: str) -> str:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find('[')
    end     = cleaned.rfind(']')
    if start != -1 and end != -1:
        return cleaned[start:end + 1]
    return cleaned


def _repair_json_control_chars(text: str) -> str:
    out = []
    in_string = False
    escape = False

    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == "\"":
                out.append(ch)
                in_string = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            code = ord(ch)
            if code < 32:
                out.append(" ")
                continue
            out.append(ch)
            continue

        if ch == "\"":
            in_string = True
        out.append(ch)

    repaired = "".join(out)
    repaired = re.sub(r",(\s*[\]}])", r"\1", repaired)
    return repaired


def parse_coding_response(raw: str, page_name: str) -> list:
    cleaned = clean_json_response(raw)
    try:
        children = json.loads(cleaned)
        if not isinstance(children, list):
            raise ValueError("Response must be a JSON array")
        return children
    except json.JSONDecodeError as e:
        repaired = _repair_json_control_chars(cleaned)
        if repaired != cleaned:
            try:
                children = json.loads(repaired)
                if not isinstance(children, list):
                    raise ValueError("Response must be a JSON array")
                log.warn("CODING", f"Recovered malformed JSON for page={page_name!r} via control-char repair")
                return children
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Invalid JSON for '{page_name}': {e}\n\nRaw:\n{raw[:800]}")


async def generate_page_nodes(page: dict, project_title: str, user_prompt: str, layout_context: dict = None, screenshot_base64: str = None, screenshot_media_type: str = "image/png") -> dict:
    """
    Generate ONE page.
    Returns { page_id, page_name, theme, frame }
    """
    page_name   = page["name"]
    page_width  = page.get("width",  1440)
    page_height = page.get("height", 3200)
    page_desc   = page.get("description", page_name)
    images      = page.get("images", [])
    memory_context = page.get("memory_context") or {}

    log.info("CODING", f"Generating page={page_name!r}  size={page_width}×{page_height}")

    # ── Select theme — prefer layout_context from screenshot ──────
    if memory_context.get("preferred_theme") and not (layout_context and layout_context.get("color_palette")):
        preferred_theme = memory_context.get("preferred_theme") or {}
        preferred_colors = list(preferred_theme.get("colors", []) or [])
        if len(preferred_colors) < 5:
            fallback_colors = ["#111111", "#1A1A1A", "#4F46E5", "#818CF8", "#FFFFFF"]
            for color in fallback_colors:
                if len(preferred_colors) >= 5:
                    break
                preferred_colors.append(color)
        selected_theme = {
            "name": preferred_theme.get("name", "Project Memory Theme"),
            "category": "memory",
            "colors": preferred_colors,
            "animation": preferred_theme.get("animation", "fade"),
            "description": "Theme inherited from previously generated project memory",
        }
        theme_block = build_theme_block(selected_theme)
        bg_color = selected_theme["colors"][0]
        log.info("CODING", f"Theme inherited from memory: {selected_theme['name']!r}")

    elif layout_context and layout_context.get("color_palette"):
        # Screenshot was provided — build theme directly from extracted visual style
        raw_palette   = layout_context.get("color_palette", "")
        visual_style  = layout_context.get("visual_style", "")
        components    = layout_context.get("detected_components", [])
        sections      = layout_context.get("detected_sections", [])

        # Parse color palette string into individual hex colors
        hex_colors = re.findall(r'#[0-9A-Fa-f]{3,8}', raw_palette + " " + visual_style)

        if len(hex_colors) >= 2:
            # Enough colors extracted — build theme from screenshot
            while len(hex_colors) < 5:
                hex_colors.append(hex_colors[-1])
            selected_theme = {
                "name":        "Screenshot Reference",
                "category":    "custom",
                "colors":      hex_colors[:5],
                "animation":   "fade",
                "description": f"Theme extracted from reference screenshot. Style: {visual_style[:120]}",
            }
            log.info("CODING", f"Theme from screenshot — colors: {hex_colors[:5]}")
        else:
            # Colors not parseable — pass the visual description as extra context to Gemini
            selected_theme = None
            log.info("CODING", "Screenshot provided but no hex colors found — injecting visual description")

        # Build a richer theme block that describes the visual style in words
        if selected_theme:
            theme_block = build_theme_block(selected_theme)
        else:
            theme_block = (
                f"VISUAL STYLE REFERENCE (from screenshot):\n"
                f"  Style description: {visual_style}\n"
                f"  Color palette description: {raw_palette}\n"
                f"  Detected sections: {', '.join(sections[:8])}\n"
                f"  Detected components: {', '.join(components[:8])}\n"
                f"  IMPORTANT: Reproduce this visual style as faithfully as possible.\n"
                f"  Use colors, spacing, and component patterns that match this description.\n"
            )
            # Still need a bg_color fallback — pick dark or light based on keywords
            bg_color = "#111111" if any(w in visual_style.lower() for w in ["dark", "black", "night"]) else "#FFFFFF"
            selected_theme = {
                "name": "Screenshot Reference",
                "colors": [bg_color, bg_color, "#4F46E5", "#818CF8", "#FFFFFF"],
                "animation": "fade",
            }

        bg_color = selected_theme["colors"][0]
        log.info("CODING", f"Using screenshot-derived theme for page={page_name!r}")

    else:
        # No screenshot — fall back to keyword-based theme selection
        themes         = select_themes_for_prompt(user_prompt, max_themes=1)
        selected_theme = themes[0] if themes else {
            "name":        "Dark Pro",
            "category":    "dark",
            "colors":      ["#111111", "#1A1A1A", "#4F46E5", "#818CF8", "#FFFFFF"],
            "animation":   "fade",
            "description": "Dark background with indigo accent",
        }
        theme_block = build_theme_block(selected_theme)
        bg_color    = selected_theme["colors"][0]
        log.info("CODING", f"Theme selected from keywords: {selected_theme['name']!r}")

    # ── Format image specs ────────────────────────────────────────
    images_section = ""
    if images:
        images_section = "\nIMAGE PLACEHOLDERS — create one 'image' element for each:\n"
        for img in images:
            images_section += (
                f"  name: \"{img['placeholder_name']}\"\n"
                f"  imageKeyword: \"{img['placeholder_name']}\"\n"
                f"  width: {img['width']}, height: {img['height']}\n"
                f"  src: \"PLACEHOLDER\"\n"
                f"  intent: {img['image_prompt']}\n\n"
            )

    # Build visual reference block from layout_context if available
    visual_ref_block = ""
    if layout_context:
        visual_ref_block = (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"VISUAL REFERENCE (from user's screenshot):\n"
            f"  Layout type:    {layout_context.get('layout_type', 'unknown')}\n"
            f"  Screen type:    {layout_context.get('screen_type', 'full_page')}\n"
            f"  Visual style:   {layout_context.get('visual_style', '')}\n"
            f"  Color palette:  {layout_context.get('color_palette', '')}\n"
            f"  Sections seen:  {', '.join(layout_context.get('detected_sections', []))}\n"
            f"  Components:     {', '.join(layout_context.get('detected_components', []))}\n"
            f"  CRITICAL: Your output must visually match this reference style.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if layout_context.get("detected_components"):
            component_text = ", ".join(layout_context.get("detected_components", []))
            visual_ref_block += (
                f"  Keep the shell/navigation pattern consistent with these detected components: {component_text}\n"
                f"  Do not arbitrarily switch between top nav and left sidebar across related screens.\n"
            )

    # ── UI state context block (state-per-frame mode) ─────────────
    ui_state_block = ""
    if page.get("ui_state") or page.get("feature_group"):
        ui_state_block = (
            f"\nUI STATE CONTEXT:\n"
            f"  Feature group: {page.get('feature_group', '')}\n"
            f"  UI state type: {page.get('ui_state', '')}\n"
            f"  This frame shows ONE specific UI moment. Render exactly this state.\n"
        )

    navigation_block = _build_navigation_block(page)
    journey_block = _build_journey_block(page)
    memory_block = _build_memory_block(page)
    attachment_block = _build_attachment_context_block(page)

    is_product_ui = bool(
        page.get("feature_group") or
        page.get("ui_state") or
        (layout_context and layout_context.get("layout_type") in ["product_screen", "dashboard", "crm", "ecommerce"])
    )

    if is_product_ui:
        layout_fit_block = (
            f"\nAPP VIEWPORT FIT RULES:\n"
            f"  This frame is a full product/application screen, not a small website mockup.\n"
            f"  Fill most of the 1440px frame with the actual UI surface.\n"
            f"  Keep app gutters tight and realistic, usually around 16-32px.\n"
            f"  Do NOT preserve blank screenshot margins or browser capture whitespace around the app.\n"
            f"  Do NOT render an extra gray canvas, outer border, or screenshot frame around the interface.\n"
            f"  Sidebars, tables, lists, detail panels, cards, modals, and toolbars must be generously sized, not compressed.\n"
        )
    else:
        layout_fit_block = (
            f"\nPAGE FIT RULES:\n"
            f"  Use the frame width confidently and avoid unnecessary empty margins.\n"
            f"  Ignore any screenshot whitespace that is outside the real website content area.\n"
        )

    screenshot_guidance_block = ""
    if layout_context and (
        layout_context.get("outer_padding_present") or
        layout_context.get("viewport_fill_guidance")
    ):
        screenshot_guidance_block = (
            f"\nSCREENSHOT FIT GUIDANCE:\n"
            f"  outer_padding_present: {layout_context.get('outer_padding_present', False)}\n"
            f"  guidance: {layout_context.get('viewport_fill_guidance', '')}\n"
            f"  IMPORTANT: treat any surrounding screenshot whitespace as capture padding, not as part of the UI layout.\n"
            f"  Reconstruct the actual app bounds only. The final frame should show the product UI itself, not the screenshot container.\n"
        )

    text_prompt = f"""{CODING_SYSTEM_PROMPT}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROJECT:      {project_title}
USER REQUEST: {user_prompt}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{visual_ref_block}
{theme_block}
{ui_state_block}
{navigation_block}
{journey_block}
{memory_block}
{attachment_block}
{layout_fit_block}
{screenshot_guidance_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRAME TO GENERATE:
  name:        {page_name}
  description: {page_desc}
  width:       {page_width}px
  height:      {page_height}px
  All element coordinates are relative to this frame.
  x range: 0 – {page_width}
  y range: 0 – {page_height}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{images_section}
REMINDER:
  lineHeight   → plain number like 1.1  (NOT an object)
  letterSpacing → plain number like 2   (NOT an object)
  src on images → "PLACEHOLDER"        (backend replaces it)
  colors       → hex strings like "#4F46E5"

Generate the children array now. Output ONLY the JSON array.
"""

    if screenshot_base64:
        contents = [
            {"inline_data": {"mime_type": screenshot_media_type, "data": screenshot_base64}},
            f"This is the VISUAL REFERENCE screenshot. It may already be cropped to the real UI bounds.\n"
            f"Study its colors, layout, sidebar, cards, topbar, typography and spacing carefully.\n"
            f"Do not recreate screenshot whitespace, editor canvas, or outer framing beyond the actual product surface.\n\n"
            f"Now generate Figma JSON elements for this frame:\n\n{text_prompt}"
        ]
        log.info("CODING", f"Calling Gemini with screenshot vision for page={page_name!r}")
    else:
        contents = [text_prompt]
        log.info("CODING", f"Calling Gemini (text only) for page={page_name!r}")

    response = await generate_content_with_retry(
        client=client,
        model=planner_model,
        contents=contents,
        config=None,
        log_tag="CODING",
        action=f"Generate page nodes for {page_name!r}",
    )
    raw = response.text
    log.debug("CODING", f"Raw response: {len(raw)} chars for page={page_name!r}")

    children = parse_coding_response(raw, page_name)
    children = sanitize_generated_children(children)
    children = enforce_reusable_structure(children)
    children = stabilize_generated_children(children)
    children = inject_image_urls(children)

    elem_count = count_elements(children)
    log.success("CODING",
        f"Page={page_name!r} done — {elem_count} elements",
        extra={"page_id": page["id"], "elements": elem_count}
    )

    frame = {
        "type":            "frame",
        "name":            page_name,
        "width":           page_width,
        "height":          page_height,
        "backgroundColor": bg_color,
        "children":        children,
    }

    return {
        "page_id":   page["id"],
        "page_name": page_name,
        "theme": {
            "name":      selected_theme["name"],
            "colors":    selected_theme["colors"],
            "animation": selected_theme["animation"],
        },
        "frame": frame,
    }


def count_elements(elements: list) -> int:
    count = 0
    for el in elements:
        count += 1
        if "children" in el and isinstance(el["children"], list):
            count += count_elements(el["children"])
    return count
