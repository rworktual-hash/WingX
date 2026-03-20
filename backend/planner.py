"""
planner.py  —  Figma design planner

Takes a user prompt and returns a structured JSON plan describing
the full website layout: pages, sections, components, and image placeholders.
Used by the /generate endpoint to drive Figma frame generation.

This is SEPARATE from planner_react.py which handles React code export.
"""

import os
import json
import re
from google import genai
from dotenv import load_dotenv
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

PLANNER_SYSTEM_PROMPT = """
You are a senior UI/UX architect and product designer for an AI-powered Figma website generator.

Your task is to analyze a user's website request and produce a structured JSON plan that can be used to automatically generate a complete website design in Figma.

The output must describe the full website structure, including pages, sections, layout, components, images, and functional elements.

CRITICAL RULES
1. Output ONLY valid JSON.
2. Do NOT include explanations, markdown, or comments.
3. Each page represents ONE tall Figma frame.
4. Frame width is ALWAYS 1440px.
5. Height must be estimated based on the number of sections (900px–3600px typical).
6. Every page must include sections and UI components.
7. Navigation must be consistent across pages.
8. Include realistic image placeholders when needed.
9. Generate ALL pages a real website requires.

PLANNING REQUIREMENTS

Before generating the JSON plan, internally analyze:
- Website purpose
- Target users
- Required pages
- Navigation structure
- UI components
- Interactive features
- Content hierarchy

Typical pages include but are not limited to:
Home
About
Services / Products
Features
Pricing
Portfolio / Projects
Blog
Contact
FAQ
Dashboard / Login pages (if needed)

Each page must contain multiple SECTIONS such as:
Hero
Features
Testimonials
Stats
Pricing Tables
Call To Action
Forms
Footer

Each section must include UI COMPONENTS such as:
Text blocks
Buttons
Cards
Forms
Navigation bars
Footers
Image blocks
Feature grids
Testimonials
Pricing tables

Image placeholders must include a realistic AI generation prompt describing the visual style.

OUTPUT FORMAT

{
  "project_title": "string",
  "website_goal": "short description of what the website is meant to achieve",
  "target_users": "who this site is for",
  "navigation": [
    "Home",
    "About",
    "Services",
    "Projects",
    "Blog",
    "Contact"
  ],
  "total_pages": number,
  "pages": [
    {
      "id": "page1",
      "name": "Home",
      "description": "Main landing page introducing the brand",
      "width": 1440,
      "height": 3200,
      "sections": [
        {
          "section_name": "Hero",
          "purpose": "Introduce the product with strong call to action",
          "components": [
            "headline",
            "subheadline",
            "primary CTA button",
            "secondary CTA button",
            "hero image"
          ]
        }
      ],
      "images": [
        {
          "id": "img1",
          "placeholder_name": "startup workspace",
          "width": 900,
          "height": 700,
          "image_prompt": "modern startup workspace with laptop, clean desk, soft lighting, minimal design, professional tech environment"
        }
      ]
    }
  ]
}

QUALITY RULES
- Follow real UX best practices
- Maintain logical page flow
- Include meaningful sections
- Include realistic UI components
- Provide clear image prompts
- Ensure pages have balanced layouts
- Avoid empty pages or vague sections
- Avoid duplicate pages
- Ensure navigation matches the pages generated

Always produce a COMPLETE website plan ready for automated Figma design generation.

USER REQUEST:
{user_prompt}
"""


def parse_plan(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\n\nRaw:\n{raw[:800]}")

    pages = data.get("pages", [])
    for i, page in enumerate(pages):
        if "id" not in page:
            page["id"] = f"page{i+1}"
        if "width" not in page:
            page["width"] = 1440
        if "height" not in page:
            page["height"] = 3200
        if "images" not in page:
            page["images"] = []

    return {
        "project_title": data.get("project_title", "Untitled Project"),
        "total_pages":   data.get("total_pages", len(pages)),
        "pages":         pages,
    }


async def run_planner(user_prompt: str) -> dict:
    log.info("PLANNER", f"Starting — prompt: {user_prompt[:80]!r}")

    full_prompt = PLANNER_SYSTEM_PROMPT.replace("{user_prompt}", user_prompt)
    response    = client.models.generate_content(model=planner_model, contents=full_prompt)
    raw_text    = response.text

    log.debug("PLANNER", f"Raw response: {len(raw_text)} chars")

    parsed = parse_plan(raw_text)

    log.success("PLANNER",
        f"Plan ready — project={parsed['project_title']!r}  pages={parsed['total_pages']}",
        extra={"total_pages": parsed["total_pages"]}
    )

    for p in parsed["pages"]:
        log.info("PLANNER",
            f"  → {p['name']}  ({p['width']}×{p['height']}px)  images={len(p['images'])}",
            extra={"page_id": p["id"]}
        )

    return parsed