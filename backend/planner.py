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
You are a senior UI/UX planner for a Figma design generation system.

Your job: given a user's design request, output a JSON plan listing every PAGE to generate.
Each page becomes ONE tall Figma frame (like a full scrollable webpage).

RULES:
1. Output ONLY valid JSON — no markdown, no explanation, no code fences.
2. Each page is a SEPARATE Figma frame. Width is always 1440px.
3. Height depends on content — typically 900px to 3600px per page.
4. List all image placeholders needed per page.
5. Generate ALL pages a real website would need (e.g. Hero, About, Projects, Contact).

OUTPUT FORMAT (follow exactly):
{
  "project_title": "string",
  "total_pages": number,
  "pages": [
    {
      "id": "page1",
      "name": "string — e.g. Hero Section",
      "description": "brief description of what this page contains",
      "width": 1440,
      "height": 3200,
      "images": [
        {
          "id": "img1",
          "placeholder_name": "short keyword e.g. workspace",
          "width": 550,
          "height": 650,
          "image_prompt": "detailed AI image generation prompt"
        }
      ]
    }
  ]
}
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

    full_prompt = f"{PLANNER_SYSTEM_PROMPT}\n\nUser Request: {user_prompt}"
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