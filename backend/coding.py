import os
import json
import re
from google import genai
from dotenv import load_dotenv
from themes import select_themes_for_prompt

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

IMAGE_PROXY_BASE = os.getenv(
    "IMAGE_PROXY_BASE",
    "https://figma-backend-rahul.onrender.com/api/image-proxy"
)

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT — generates the EXACT sample output format
# ─────────────────────────────────────────────────────────────────
CODING_SYSTEM_PROMPT = """
You are an expert Figma UI designer. Generate a JSON array of design elements for ONE Figma page/frame.

The array you output becomes the "children" list of a single tall Figma frame.

══════════════════════════════════════════
OUTPUT RULES:
══════════════════════════════════════════
1. Output ONLY a valid JSON array — no markdown, no explanation, no code fences.
2. Use SIMPLE values: hex color strings, plain numbers. NO Figma API objects.
3. lineHeight is a plain number like 1.1 — NOT an object.
4. letterSpacing is a plain number like 2 — NOT an object.
5. All coordinates (x, y) are relative to this frame (top-left = 0, 0).
6. Every element must have: type, name, x, y.

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
    roles = [
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
    seed = abs(hash(keyword)) % 9999
    picsum_url = f"https://picsum.photos/seed/{seed}/{width}/{height}"
    encoded = picsum_url.replace(":", "%3A").replace("/", "%2F")
    return f"{IMAGE_PROXY_BASE}?url={encoded}&width={width}&height={height}"


def inject_image_urls(children: list) -> list:
    """Replace src='PLACEHOLDER' with real proxy URLs recursively."""
    for el in children:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "image" and el.get("src") == "PLACEHOLDER":
            keyword = el.get("imageKeyword", el.get("name", "photo"))
            w = int(el.get("width", 400))
            h = int(el.get("height", 300))
            el["src"] = build_image_url(keyword, w, h)
        if "children" in el and isinstance(el["children"], list):
            inject_image_urls(el["children"])
    return children


def clean_json_response(raw: str) -> str:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start = cleaned.find('[')
    end = cleaned.rfind(']')
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
    Generate ONE page that matches the sample format exactly.
    Returns { page_id, page_name, theme, frame }
    where frame = { type, name, width, height, backgroundColor, children[] }
    """
    page_name  = page["name"]
    page_width = page.get("width", 1440)
    page_height = page.get("height", 3200)
    page_desc  = page.get("description", page_name)
    images     = page.get("images", [])

    # ── Select theme ──
    themes = select_themes_for_prompt(user_prompt, max_themes=1)
    selected_theme = themes[0] if themes else {
        "name": "Dark Pro",
        "category": "dark",
        "colors": ["#111111", "#1A1A1A", "#4F46E5", "#818CF8", "#FFFFFF"],
        "animation": "fade",
        "description": "Dark background with indigo accent",
    }
    theme_block = build_theme_block(selected_theme)
    bg_color    = selected_theme["colors"][0]

    # ── Format image specs ──
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

    print(f"\n[CODING] '{page_name}' | theme: {selected_theme['name']}")

    response = client.models.generate_content(
        model=planner_model,
        contents=full_prompt
    )

    raw = response.text
    print(f"[CODING] {len(raw)} chars received")

    children = parse_coding_response(raw, page_name)
    children = inject_image_urls(children)

    print(f"[CODING] ✅ '{page_name}': {count_elements(children)} elements")

    frame = {
        "type": "frame",
        "name": page_name,
        "width": page_width,
        "height": page_height,
        "backgroundColor": bg_color,
        "children": children,
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