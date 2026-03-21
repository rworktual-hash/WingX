import os
import json
import re
from google import genai
from dotenv import load_dotenv
from themes import select_themes_for_prompt
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

IMAGE_PROXY_BASE = os.getenv(
    "IMAGE_PROXY_BASE",
    "https://wingx-2vpp.onrender.com/api/image-proxy"
)

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────
CODING_SYSTEM_PROMPT = """
You are a senior UI/UX designer generating a professional Figma page layout.

Your task is to generate a JSON array of UI elements that will become the "children" of ONE tall Figma frame representing a full webpage.

The layout must resemble a modern, real production website.

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
max ≈ 1200px

All elements must stay inside the frame width.

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

════════════════════════════════════════
ELEMENT TYPES
════════════════════════════════════════

RECTANGLE
Used for backgrounds, surfaces, cards, overlays, and dividers.

Properties:
type
name
x
y
width
height
backgroundColor
cornerRadius
opacity (optional)

TEXT
Used for headings, paragraphs, labels.

Properties:
type
name
x
y
width
text
fontSize
fontWeight
color
lineHeight
letterSpacing

IMAGE
Used for photos, illustrations, thumbnails.

Properties:
type
name
x
y
width
height
borderRadius
backgroundColor
src
imageKeyword

src must always be:
"PLACEHOLDER"

imageKeyword must describe the image clearly.

Examples:
"startup office"
"mobile app dashboard"
"team collaboration"
"modern workspace"

BUTTON

Filled button:

type
name
x
y
width
height
text
backgroundColor
textColor
cornerRadius
fontSize
fontWeight

Outlined button additionally includes:

borderColor
borderWidth

GROUP
Groups related UI elements.

Properties:
type
name
x
y
children (array)

Children coordinates remain absolute within the frame.

════════════════════════════════════════
WEBSITE STRUCTURE REQUIREMENTS
════════════════════════════════════════

Generate a realistic landing page with sections such as:

1. Navbar
2. Hero section
3. Features / services
4. About / product explanation
5. Feature cards or product showcase
6. Testimonials or statistics
7. Call-to-action section
8. Footer

════════════════════════════════════════
NAVBAR DESIGN
════════════════════════════════════════

Height: 80–90px

Left side:
Logo text

Right side:
Navigation links

Optional CTA button on far right.

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

All images must include:

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

════════════════════════════════════════
DESIGN QUALITY
════════════════════════════════════════

The design must:

Look like a real production website

Use consistent spacing

Use realistic marketing copy

Avoid overlapping elements

Avoid placeholder lorem ipsum

Use meaningful product descriptions

The layout must feel modern, balanced, and professional.

Generate the JSON layout now.
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


def clean_json_response(raw: str) -> str:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find('[')
    end     = cleaned.rfind(']')
    if start != -1 and end != -1:
        return cleaned[start:end + 1]
    return cleaned


def parse_coding_response(raw: str, page_name: str) -> list:
    cleaned = clean_json_response(raw)
    try:
        children = json.loads(cleaned)
        if not isinstance(children, list):
            raise ValueError("Response must be a JSON array")
        return children
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for '{page_name}': {e}\n\nRaw:\n{raw[:800]}")


async def generate_page_nodes(page: dict, project_title: str, user_prompt: str) -> dict:
    """
    Generate ONE page.
    Returns { page_id, page_name, theme, frame }
    """
    page_name   = page["name"]
    page_width  = page.get("width",  1440)
    page_height = page.get("height", 3200)
    page_desc   = page.get("description", page_name)
    images      = page.get("images", [])

    log.info("CODING", f"Generating page={page_name!r}  size={page_width}×{page_height}")

    # ── Select theme ──────────────────────────────────────────────
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

    log.info("CODING", f"Theme selected: {selected_theme['name']!r}")

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

    full_prompt = f"""{CODING_SYSTEM_PROMPT}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROJECT:      {project_title}
USER REQUEST: {user_prompt}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{theme_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAGE TO GENERATE:
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

    log.info("CODING", f"Calling Gemini for page={page_name!r}")
    response = client.models.generate_content(
        model=planner_model,
        contents=full_prompt
    )
    raw = response.text
    log.debug("CODING", f"Raw response: {len(raw)} chars for page={page_name!r}")

    children = parse_coding_response(raw, page_name)
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