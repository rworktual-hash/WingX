import os
import json
import re
import asyncio
from google import genai
from dotenv import load_dotenv

from themes import select_themes_for_prompt
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

══════════════════════════════════════════
DESIGN QUALITY REQUIREMENTS:
══════════════════════════════════════════
- Use the provided theme colors throughout: background, surfaces, accent, text
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

    log.info("CODING", f"Generating page={page_name!r}  size={page_width}×{page_height}")

    # ── Select theme — prefer layout_context from screenshot ──────
    if layout_context and layout_context.get("color_palette"):
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

    # ── UI state context block (state-per-frame mode) ─────────────
    ui_state_block = ""
    if page.get("ui_state") or page.get("feature_group"):
        ui_state_block = (
            f"\nUI STATE CONTEXT:\n"
            f"  Feature group: {page.get('feature_group', '')}\n"
            f"  UI state type: {page.get('ui_state', '')}\n"
            f"  This frame shows ONE specific UI moment. Render exactly this state.\n"
        )

    text_prompt = f"""{CODING_SYSTEM_PROMPT}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROJECT:      {project_title}
USER REQUEST: {user_prompt}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{visual_ref_block}
{theme_block}
{ui_state_block}
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
            f"This is the VISUAL REFERENCE screenshot. Study its colors, layout, sidebar, cards, topbar, typography and spacing carefully.\n\nNow generate Figma JSON elements for this frame:\n\n{text_prompt}"
        ]
        log.info("CODING", f"Calling Gemini with screenshot vision for page={page_name!r}")
    else:
        contents = [text_prompt]
        log.info("CODING", f"Calling Gemini (text only) for page={page_name!r}")

    def _blocking_gemini_call():
        return client.models.generate_content(
            model=planner_model,
            contents=contents,
        )

    response = await asyncio.to_thread(_blocking_gemini_call)
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