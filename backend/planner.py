from __future__ import annotations

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
import asyncio
from google import genai
from dotenv import load_dotenv
from llm_utils import generate_content_with_retry
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY1"))
PLANNER_EXTRACT_CONTEXT_CHAR_LIMIT = int(os.getenv("PLANNER_EXTRACT_CONTEXT_CHAR_LIMIT", "30000"))
PLANNER_FLOW_CONTEXT_CHAR_LIMIT = int(os.getenv("PLANNER_FLOW_CONTEXT_CHAR_LIMIT", "40000"))
PLANNER_FLOW_STATES_CHAR_LIMIT = int(os.getenv("PLANNER_FLOW_STATES_CHAR_LIMIT", "60000"))
PLANNER_LAYOUT_SECTION_LIMIT = int(os.getenv("PLANNER_LAYOUT_SECTION_LIMIT", "0"))

STATE_EXTRACTOR_PROMPT = """
You are a UI/UX analyst. You receive a content context from a requirements document.

Extract EVERY distinct UI state that needs its own Figma frame, but organize them as feature flows.
Each flow is a left-to-right user journey.
One frame per meaningful UI moment: default view, modal open, after action, error state, popup, panel, expanded card, secondary state.
Cover every interaction: clicks, panels, drawers, menus, modals, transitions, result states.
Do NOT merge meaningful states together.
Do NOT omit workflows.
Only add annotations for important explanatory states, not every frame.
Think about the product as one connected system:
- infer the shared app/site shell before splitting into states
- keep related screens under one stable information architecture
- if multiple states belong to the same area, keep the same top-level navigation destination and shell

Output ONLY a JSON array:
[
  {
    "id": "state_1",
    "name": "Feature Name — Screen Name",
    "screen_title": "Screen Name",
    "feature_group": "Dashboard",
    "flow_step": 1,
    "flow_total": 3,
    "ui_state": "default",
    "description": "Main dashboard showing call list with incoming, ongoing, missed statuses",
    "components": ["sidebar nav", "topbar", "call list", "status badges", "action buttons"],
    "click_target_keywords": ["button name or component that triggers the next state"],
    "annotation_text": "Optional annotation for an important screen",
    "annotation_target_keywords": ["keyword for the component that the annotation should point to"],
    "height": 1080
  }
]

CONTENT CONTEXT:
{content_context}

Output ONLY the JSON array. No explanation.
"""


FREE_PLANNER_PROMPT = """
You are a senior UI/UX architect for an AI-powered Figma generator.

Generate complete feature flows, not isolated screens.
Each feature flow should be a left-to-right user journey made of multiple screen states.
Every screen must represent one meaningful UI moment in that journey.
You may be given a simple prompt only, so you must infer the likely workflows and interaction states without hardcoding to any specific product domain.

CRITICAL RULES:
1. Output ONLY valid JSON. No markdown, no explanations.
2. Frame width ALWAYS 1440px.
3. Height: 900-1080px for app screens, 1800-3200px for landing pages.
4. For every major action or interaction, add a separate frame showing the resulting state.
5. Group related screens under the same `feature_group`.
6. Use sequential screen names that make the flow obvious.
7. Start each feature with the most natural default screen, then show the triggered states after it.
8. Capture all important flows, menus, drawers, modals, expanded cards, secondary tabs, success/error states, and inline interaction states.
9. Add annotations only for key explanatory moments, not every single frame.
10. If the input references a screenshot or existing product screen, ignore any empty capture padding around the UI and plan the actual site/app surface as a full-size design.
11. Include these metadata fields for each page:
   - `screen_title`
   - `flow_step`
   - `flow_total`
   - `click_target_keywords`
     - `annotation_text`
     - `annotation_target_keywords`
12. Infer a stable shared information architecture for the project so related screens reuse the same top-level destinations and shell.
13. For website or product-site requests, structure the plan as action branches from the main home page:
    - Home -> click Demo / Request Demo -> demo flow
    - Home -> click Login / Sign In -> auth flow
    - Home -> click Category / Shop -> browsing flow
    - Home -> click Product -> product-detail flow
    - Home -> click Product -> Add to Cart -> Cart -> Checkout steps -> Confirmation
14. For those website-style flows, repeat the home page as the first step of each branch rather than mixing everything into one giant row.

OUTPUT FORMAT:
{
  "project_title": "string",
  "website_goal": "string",
  "total_pages": number,
  "pages": [
    {
      "id": "page1",
      "name": "Dashboard — Default",
      "screen_title": "Dashboard",
      "feature_group": "Dashboard",
      "flow_step": 1,
      "flow_total": 1,
      "description": "Main dashboard showing call list",
      "click_target_keywords": ["button name"],
      "annotation_text": "Optional explanatory callout for the key component in this screen",
      "annotation_target_keywords": ["component keyword"],
      "width": 1440,
      "height": 1080,
      "sections": [
        {
          "section_name": "Call List",
          "purpose": "Show active and recent calls",
          "components": ["sidebar", "topbar", "call items", "status badges"]
        }
      ],
      "images": []
    }
  ]
}

USER REQUEST:
{user_prompt}
"""

FLOW_SYNTHESIS_PROMPT = """
You are a senior product UX architect.

You will receive:
1. CONTENT CONTEXT extracted from the source material
2. RAW UI STATES extracted from that material

Your job is to transform the raw states into clean, generic feature flows.

You must THINK structurally, not literally.

RULES:
- Do NOT simply preserve the source order if it starts mid-flow.
- Identify the natural parent screen or baseline state for each feature.
- Reorder each feature into a clean left-to-right journey:
  baseline/default screen -> action state -> resulting state -> modal/panel/transition -> completion/error if relevant
- Dedupe repeated or near-duplicate states.
- Keep branches as separate subflows when needed, but keep them understandable.
- Do NOT hardcode a particular domain; reason from the supplied context.
- Only include important annotations, not one on every frame.
- Keep every meaningful interaction, but present it as a coherent product flow.
- Infer the shared information architecture of the product and keep it consistent across the feature flows.
- Prefer stable primary destinations and shared shell patterns over screen-by-screen reinvention.
- For website-style products, convert the plan into action branches from the home page instead of one long sequential sitemap.
- Example branch style:
  Home -> Demo
  Home -> Login
  Home -> Browse Category
  Home -> Product Detail
  Home -> Product Detail -> Add to Cart -> Checkout

Return ONLY a JSON array in this exact shape:
[
  {
    "id": "state_1",
    "name": "Feature Name — Screen Name",
    "screen_title": "Screen Name",
    "feature_group": "Feature Name",
    "flow_step": 1,
    "flow_total": 3,
    "ui_state": "default",
    "description": "What this screen shows and why it exists in the flow",
    "components": ["sidebar", "table", "detail panel"],
    "click_target_keywords": ["button or trigger for the next state"],
    "annotation_text": "",
    "annotation_target_keywords": [],
    "height": 1080
  }
]

CONTENT CONTEXT:
{content_context}

RAW UI STATES:
{ui_states}
"""

DEFAULT_KEYWORDS = [
    "default", "base", "home", "overview", "dashboard", "main", "list", "index",
    "landing", "shell", "workspace", "board", "table", "inbox"
]
SECONDARY_KEYWORDS = [
    "popup", "modal", "drawer", "dialog", "panel", "card", "detail", "selected",
    "open", "expanded", "active", "playing", "ringing", "incoming", "confirm",
    "confirmation", "delete", "edit", "form", "search", "results", "transfer",
    "hold", "resume", "error", "success", "loading", "progress"
]
FEATURE_STOPWORDS = {
    "flow", "interaction", "management", "experience", "state", "screen", "ui",
    "view", "journey", "scenario", "workspace", "module",
}
NAV_GENERIC_LABELS = {"general", "default", "state", "screen", "page", "app", "site"}
NAV_LABEL_PATTERNS = [
    (("home", "landing", "storefront"), "Home"),
    (("dashboard", "workspace"), "Dashboard"),
    (("inbox",), "Inbox"),
    (("category", "catalog", "browse", "browsing", "collection", "shop"), "Shop"),
    (("product detail", "quick view", "detail page", "detail view", "pdp"), "Product"),
    (("cart", "bag", "basket", "mini cart"), "Cart"),
    (("checkout", "shipping", "payment", "confirmation", "order confirmation"), "Checkout"),
    (("search", "results"), "Search"),
    (("profile", "account"), "Account"),
    (("settings", "preferences"), "Settings"),
    (("support", "help"), "Support"),
]
FLOW_RESET_THRESHOLD = 18
NAV_PREFERRED_ORDER = [
    "Home", "Shop", "Products", "Categories", "Pricing", "Demo", "Request Demo",
    "Get Started", "Login", "Sign In", "Account", "Cart", "Checkout", "Dashboard",
    "Agent", "Admin Routing", "Support", "Settings",
]
GENERIC_BASE_TITLES = {
    "home", "home page", "landing", "landing page", "dashboard", "default state",
    "main screen", "main page", "inbox", "inbox main screen", "calls page",
    "overview", "workspace", "board", "screen", "page", "app",
}
ACTION_LABEL_PATTERNS = [
    (("accept", "answer"), "Accept Incoming Call"),
    (("decline", "reject"), "Decline Incoming Call"),
    (("outbound", "dial", "calling", "call out"), "Start Outbound Call"),
    (("incoming", "inbound"), "Handle Incoming Call"),
    (("hold",), "Put Call On Hold"),
    (("resume",), "Resume Call"),
    (("transfer",), "Transfer Call"),
    (("receiving transfer", "incoming transfer"), "Receive Transfer"),
    (("notes", "note"), "Open Notes"),
    (("transcript",), "Open Transcript"),
    (("voicemail",), "Open Voicemail"),
    (("missed call",), "Open Missed Call"),
    (("delete",), "Delete Item"),
    (("edit",), "Edit Item"),
    (("kebab", "three dot", "more", "options", "menu"), "Open Options Menu"),
    (("filter",), "Apply Filters"),
    (("search",), "Search"),
    (("sort",), "Sort Results"),
    (("detail panel", "detail card", "detail"), "View Details"),
    (("modal", "dialog", "popup", "drawer", "panel", "popover", "card"), "Open Secondary State"),
    (("demo", "request demo", "book demo"), "Open Demo"),
    (("login", "sign in", "signin", "auth"), "Open Login"),
    (("category", "catalog", "browse", "collection", "shop"), "Browse Category"),
    (("product detail", "quick view", "product"), "Open Product Detail"),
    (("add to cart", "cart", "bag", "basket"), "Open Cart"),
    (("checkout", "shipping", "payment", "billing"), "Checkout"),
]
STATE_TITLE_PATTERNS = [
    (("incoming popup", "incoming call popup"), "Incoming Call Popup"),
    (("ringing",), "Ringing State"),
    (("calling popover",), "Calling Popover"),
    (("dialer",), "Dialer Open"),
    (("transfer modal",), "Transfer Modal"),
    (("search results",), "Search Results"),
    (("selected", "selected contact", "selected user"), "Selection Made"),
    (("confirm", "confirmation"), "Confirmation State"),
    (("hold",), "On Hold"),
    (("resume", "resumed"), "Resumed Call"),
    (("notes panel",), "Notes Panel"),
    (("formal notes",), "Formal Notes Open"),
    (("informal notes",), "Informal Notes Open"),
    (("transcript",), "Transcript Open"),
    (("voicemail",), "Voicemail Card"),
    (("missed call",), "Missed Call Card"),
    (("delete modal",), "Delete Modal"),
    (("edit modal",), "Edit Modal"),
    (("menu", "kebab", "options"), "Options Menu"),
    (("interaction card", "detail card"), "Interaction Card"),
    (("detail panel",), "Detail Panel"),
    (("detail card",), "Detail Card"),
    (("active call",), "Active Call"),
]
GENERIC_ACTION_LABELS = {
    "Open Secondary State", "View Details", "View Next State", "Feature", "State",
}


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _pretty_title(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip(" -–—:"))
    if not text:
        return "State"
    return " ".join(word[:1].upper() + word[1:] if word else "" for word in text.split(" "))


def _limit_text(value: str, limit: int) -> str:
    if limit <= 0:
        return value
    return value[:limit]


def _clean_feature_label(value: str) -> str:
    title = _pretty_title(value)
    words = [w for w in title.split() if _norm_text(w) not in FEATURE_STOPWORDS]
    cleaned = " ".join(words).strip()
    return cleaned or title or "Feature"


def _split_explicit_steps(text: str) -> list[str]:
    raw_parts = re.split(r"\s*(?:,|->|→|=>)\s*", text or "")
    return [part.strip(" \t-–—:.;") for part in raw_parts if part and part.strip(" \t-–—:.;")]


def _parse_explicit_workflow_layout_from_prompt(user_prompt: str) -> tuple[list[dict], list[dict], list[dict]]:
    if not user_prompt:
        return [], [], []

    columns = []
    rows = []
    screen_instructions = []
    lines = [line.strip() for line in user_prompt.splitlines() if line.strip()]
    seen_instruction_keys = set()
    current_column = None

    for line in lines:
        column_match = re.match(r"^\s*(column\s*\d+)\s*[:\-]?\s*(.*)$", line, re.IGNORECASE)
        if column_match:
            column_label = column_match.group(1).strip()
            column_title = column_match.group(2).strip(" \t-–—:") or column_label
            current_column = {
                "column_label": column_label,
                "column_title": column_title,
                "rows": [],
            }
            columns.append(current_column)
            continue

        row_match = re.match(r"^\s*(row\s*\d+)\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if row_match:
            row_label = row_match.group(1).strip()
            step_text = row_match.group(2).strip()
            steps = [{"name": step} for step in _split_explicit_steps(step_text)]
            if steps:
                row_obj = {"row_label": row_label, "steps": steps}
                if current_column:
                    current_column["rows"].append(row_obj)
                else:
                    rows.append(row_obj)
            continue

        instr_match = re.match(r"^\s*([^:]{1,120}?|[^-]{1,120}?)\s*[-:]\s*(.+)$", line)
        if instr_match:
            left = instr_match.group(1).strip(" \t-–—:")
            right = instr_match.group(2).strip()
            if left and right and not re.match(r"^(row|column)\s*\d+$", left, re.IGNORECASE):
                key = (_norm_text(left), _norm_text(right))
                if key not in seen_instruction_keys:
                    seen_instruction_keys.add(key)
                    screen_instructions.append({"name": left, "instruction": right})

    return columns, rows, screen_instructions


def _extract_explicit_workflow_rows(content_context: dict | None, user_prompt: str) -> tuple[list[dict], list[dict], dict[str, str]]:
    columns = []
    rows = []
    instruction_map: dict[str, str] = {}

    for item in (content_context or {}).get("screen_instructions", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        if name and instruction:
            instruction_map[_norm_text(name)] = instruction

    context_columns = (content_context or {}).get("explicit_workflow_columns", []) or []
    for column in context_columns:
        if not isinstance(column, dict):
            continue
        column_label = str(column.get("column_label", "")).strip() or f"Column {len(columns) + 1}"
        column_title = str(column.get("column_title", "")).strip() or column_label
        column_rows = []
        for row in column.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            row_label = str(row.get("row_label", "")).strip() or f"Row {len(column_rows) + 1}"
            steps = []
            for step in row.get("steps", []) or []:
                if isinstance(step, dict):
                    step_name = str(step.get("name", "")).strip()
                    step_instruction = str(step.get("instruction", "")).strip()
                else:
                    step_name = str(step).strip()
                    step_instruction = ""
                if not step_name:
                    continue
                if step_instruction:
                    instruction_map.setdefault(_norm_text(step_name), step_instruction)
                steps.append({"name": step_name, "instruction": step_instruction})
            if steps:
                column_rows.append({"row_label": row_label, "steps": steps})
        if column_rows:
            columns.append({
                "column_label": column_label,
                "column_title": column_title,
                "rows": column_rows,
            })

    context_rows = (content_context or {}).get("explicit_workflow_rows", []) or []
    for row in context_rows:
        if not isinstance(row, dict):
            continue
        row_label = str(row.get("row_label", "")).strip() or f"Row {len(rows) + 1}"
        steps = []
        for step in row.get("steps", []) or []:
            if isinstance(step, dict):
                step_name = str(step.get("name", "")).strip()
                step_instruction = str(step.get("instruction", "")).strip()
            else:
                step_name = str(step).strip()
                step_instruction = ""
            if not step_name:
                continue
            if step_instruction:
                instruction_map.setdefault(_norm_text(step_name), step_instruction)
            steps.append({"name": step_name, "instruction": step_instruction})
        if steps:
            rows.append({"row_label": row_label, "steps": steps})

    prompt_columns, prompt_rows, prompt_instructions = _parse_explicit_workflow_layout_from_prompt(user_prompt)
    if prompt_columns:
        if not columns:
            columns = prompt_columns
        else:
            existing_column_signatures = {
                (
                    _norm_text(column.get("column_label", "")),
                    _norm_text(column.get("column_title", "")),
                    tuple(
                        tuple(_norm_text(step.get("name", "")) for step in row.get("steps", []))
                        for row in column.get("rows", [])
                    ),
                )
                for column in columns
            }
            for column in prompt_columns:
                signature = (
                    _norm_text(column.get("column_label", "")),
                    _norm_text(column.get("column_title", "")),
                    tuple(
                        tuple(_norm_text(step.get("name", "")) for step in row.get("steps", []))
                        for row in column.get("rows", [])
                    ),
                )
                if signature not in existing_column_signatures:
                    columns.append(column)
                    existing_column_signatures.add(signature)

    if prompt_rows:
        if not rows:
            rows = prompt_rows
        else:
            existing_signatures = {
                tuple(_norm_text(step.get("name", "")) for step in row.get("steps", []))
                for row in rows
            }
            for row in prompt_rows:
                signature = tuple(_norm_text(step.get("name", "")) for step in row.get("steps", []))
                if signature not in existing_signatures:
                    rows.append(row)
                    existing_signatures.add(signature)
    for item in prompt_instructions:
        instruction_map.setdefault(_norm_text(item["name"]), item["instruction"])

    return columns, rows, instruction_map


def _explicit_step_click_keywords(step_name: str) -> list[str]:
    text = step_name or ""
    lower = text.lower()
    patterns = [
        r"(.+?)\s+button\s+click$",
        r"click\s+(.+)$",
        r"(.+?)\s+clicked$",
        r"(.+?)\s+open$",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            label = _pretty_title(match.group(1).strip(" -–—:"))
            if label:
                return [label]
    return []


def _explicit_step_height(product_type: str, step_name: str) -> int:
    text = _norm_text(step_name)
    if product_type in {"landing_page", "ecommerce", "saas"}:
        if any(k in text for k in ["checkout", "cart", "payment", "billing", "form", "product", "category", "home", "about", "pricing"]):
            return 2400
    return 1080


def _build_explicit_step_pages(
    steps: list[dict],
    instruction_map: dict[str, str],
    product_type: str,
    flow_name: str,
    flow_group_id: str,
    root_name: str,
    row_label: str,
    row_index: int,
    column_meta: dict | None = None,
) -> list[dict]:
    pages = []
    flow_total = len(steps)
    column_meta = column_meta or {}
    column_label = column_meta.get("column_label", "")
    column_title = column_meta.get("column_title", "")
    column_group = column_title or column_label
    column_group_id = column_meta.get("column_group_id", "")
    column_order = column_meta.get("column_order")

    for step_index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            step_name = str(step.get("name", "")).strip()
            step_instruction = str(step.get("instruction", "")).strip()
        else:
            step_name = str(step).strip()
            step_instruction = ""
        if not step_name:
            continue

        instruction = step_instruction or instruction_map.get(_norm_text(step_name), "")
        prev_name = ""
        next_name = ""
        if step_index > 1:
            prev = steps[step_index - 2]
            prev_name = str(prev.get("name", "")).strip() if isinstance(prev, dict) else str(prev).strip()
        if step_index < flow_total:
            nxt = steps[step_index]
            next_name = str(nxt.get("name", "")).strip() if isinstance(nxt, dict) else str(nxt).strip()

        click_keywords = _explicit_step_click_keywords(step_name)
        annotation_text = ""
        if click_keywords and next_name:
            annotation_text = f"Click {', '.join(click_keywords[:2])} to open {next_name}."

        page = {
            "id": f"explicit_{column_order or 0}_{row_index}_{step_index}",
            "name": f"{flow_name} — {step_name}",
            "screen_title": step_name,
            "feature_group": root_name or row_label,
            "flow_step": step_index,
            "flow_total": flow_total,
            "ui_state": "default" if step_index == 1 else "explicit_step",
            "description": instruction or f"Explicit workflow step {step_index} from {row_label}.",
            "click_target_keywords": click_keywords,
            "annotation_text": annotation_text,
            "annotation_target_keywords": click_keywords[:],
            "width": 1440,
            "height": _explicit_step_height(product_type, step_name),
            "sections": [{
                "section_name": "Main Content",
                "purpose": instruction or step_name,
                "components": [],
            }],
            "images": [],
            "flow_group": flow_name,
            "flow_group_id": flow_group_id,
            "flow_group_step": step_index,
            "flow_group_total": flow_total,
            "journey": {
                "previous_screen": prev_name,
                "next_screen": next_name,
                "previous_page_name": prev_name,
                "next_page_name": next_name,
                "branch_root": root_name or row_label,
                "branch_trigger": step_name if step_index > 1 else "",
                "branch_goal": next_name or step_name,
                "branch_kind": "explicit_workflow",
            },
            "branch_root": root_name or row_label,
            "branch_trigger": step_name if step_index > 1 else "",
            "branch_goal": next_name or step_name,
            "branch_kind": "explicit_workflow",
            "project_navigation": {},
            "navigation": {},
            "row_label": row_label,
            "row_order": row_index,
        }
        if column_group:
            page["column_group"] = column_group
            page["column_group_id"] = column_group_id or f"column_{_norm_text(column_group).replace(' ', '_') or column_order or 1}"
            page["column_group_order"] = column_order or 1
            page["column_label"] = column_label or column_group
            page["column_title"] = column_title or column_group
        pages.append(page)

    return pages


def _build_plan_from_explicit_rows(rows: list[dict], instruction_map: dict[str, str], user_prompt: str, content_context: dict | None = None) -> dict:
    product_type = str((content_context or {}).get("product_type", "") or "").strip().lower()
    project_title = _default_title_from_prompt(user_prompt)
    pages = []

    for row_index, row in enumerate(rows, start=1):
        steps = row.get("steps", []) or []
        if not steps:
            continue
        row_label = str(row.get("row_label", "")).strip() or f"Row {row_index}"
        root_name = str(steps[0].get("name", "")).strip() if isinstance(steps[0], dict) else str(steps[0]).strip()
        flow_name = f"{row_label} — {root_name}" if root_name else row_label
        flow_group_id = f"explicit_row_{row_index}_{_norm_text(flow_name).replace(' ', '_') or row_index}"
        pages.extend(_build_explicit_step_pages(
            steps=steps,
            instruction_map=instruction_map,
            product_type=product_type,
            flow_name=flow_name,
            flow_group_id=flow_group_id,
            root_name=root_name,
            row_label=row_label,
            row_index=row_index,
        ))

    return {
        "project_title": project_title or "Product Design",
        "total_pages": len(pages),
        "pages": pages,
        "navigation_model": {},
    }


def _build_plan_from_explicit_columns(columns: list[dict], instruction_map: dict[str, str], user_prompt: str, content_context: dict | None = None) -> dict:
    product_type = str((content_context or {}).get("product_type", "") or "").strip().lower()
    project_title = _default_title_from_prompt(user_prompt)
    pages = []

    for column_index, column in enumerate(columns, start=1):
        column_label = str(column.get("column_label", "")).strip() or f"Column {column_index}"
        column_title = str(column.get("column_title", "")).strip() or column_label
        column_group_id = f"explicit_column_{column_index}_{_norm_text(column_title).replace(' ', '_') or column_index}"
        for row_index, row in enumerate(column.get("rows", []) or [], start=1):
            steps = row.get("steps", []) or []
            if not steps:
                continue
            row_label = str(row.get("row_label", "")).strip() or f"Row {row_index}"
            root_name = str(steps[0].get("name", "")).strip() if isinstance(steps[0], dict) else str(steps[0]).strip()
            flow_name = f"{column_title} / {row_label} — {root_name}" if root_name else f"{column_title} / {row_label}"
            flow_group_id = f"{column_group_id}_row_{row_index}_{_norm_text(root_name or row_label).replace(' ', '_') or row_index}"
            pages.extend(_build_explicit_step_pages(
                steps=steps,
                instruction_map=instruction_map,
                product_type=product_type,
                flow_name=flow_name,
                flow_group_id=flow_group_id,
                root_name=root_name,
                row_label=row_label,
                row_index=row_index,
                column_meta={
                    "column_label": column_label,
                    "column_title": column_title,
                    "column_group_id": column_group_id,
                    "column_order": column_index,
                },
            ))

    return {
        "project_title": project_title or "Product Design",
        "total_pages": len(pages),
        "pages": pages,
        "navigation_model": {},
    }


def _match_pattern_label(text: str, patterns: list[tuple[tuple[str, ...], str]]) -> str:
    for needles, label in patterns:
        if all(needle in text for needle in needles):
            return label
    for needles, label in patterns:
        if any(needle in text for needle in needles):
            return label
    return ""


def _is_generic_base_title(value: str) -> bool:
    norm = _norm_text(value)
    return not norm or norm in GENERIC_BASE_TITLES


def _split_feature_and_screen(state: dict, fallback_idx: int) -> tuple[str, str]:
    feature = state.get("feature_group") or ""
    screen = state.get("screen_title") or ""
    name = state.get("name") or ""

    if "—" in name:
        parts = [p.strip() for p in name.split("—", 1)]
        if not feature and parts[0]:
            feature = parts[0]
        if not screen and len(parts) > 1 and parts[1]:
            screen = parts[1]
    elif " - " in name:
        parts = [p.strip() for p in name.split(" - ", 1)]
        if not feature and parts[0]:
            feature = parts[0]
        if not screen and len(parts) > 1 and parts[1]:
            screen = parts[1]

    if not feature:
        feature = screen or name or f"Feature {fallback_idx}"
    if not screen:
        screen = name or f"State {fallback_idx}"

    return _clean_feature_label(feature), _pretty_title(screen)


def _infer_nav_label(page: dict) -> str:
    screen = page.get("screen_title", "") or ""
    feature = page.get("feature_group", "") or ""
    description = page.get("description", "") or ""
    combined = " ".join([screen, feature, description]).lower()

    if "category" in screen.lower():
        prefix = re.split(r"category", screen, flags=re.IGNORECASE)[0].strip(" -–—:")
        if prefix and _norm_text(prefix) not in NAV_GENERIC_LABELS:
            return _pretty_title(prefix)

    for patterns, label in NAV_LABEL_PATTERNS:
        if any(p in combined for p in patterns):
            return label

    feature_label = _clean_feature_label(feature)
    if _norm_text(feature_label) not in NAV_GENERIC_LABELS:
        return feature_label

    screen_label = _pretty_title(screen)
    if _norm_text(screen_label) not in NAV_GENERIC_LABELS:
        return screen_label

    return "Home"


def _page_kind(page: dict) -> str:
    hay = _norm_text(" ".join([
        page.get("name", ""),
        page.get("screen_title", ""),
        page.get("feature_group", ""),
        page.get("description", ""),
        " ".join(page.get("components", []) or []),
    ]))

    if any(k in hay for k in ["login", "sign in", "signin", "auth", "register", "create account"]):
        return "auth"
    if any(k in hay for k in ["request demo", "demo", "get started", "book demo", "contact sales"]):
        return "demo"
    if any(k in hay for k in ["checkout", "shipping", "payment", "billing", "place order", "review order"]):
        return "checkout"
    if any(k in hay for k in ["order confirmation", "thank you", "receipt", "confirmation"]):
        return "confirmation"
    if any(k in hay for k in ["cart", "bag", "basket", "mini cart"]):
        return "cart"
    if any(k in hay for k in ["product detail", "quick view", "detail page", "detail view", "pdp", "product page"]):
        return "product"
    if any(k in hay for k in ["category", "catalog", "browse", "browsing", "collection", "shop", "results"]):
        return "browse"
    if any(k in hay for k in ["home", "landing", "storefront", "default", "overview", "dashboard", "inbox", "main"]):
        return "base"
    return "feature"


def _sort_nav_links(labels: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for preferred in NAV_PREFERRED_ORDER:
        for label in labels:
            if label == preferred and label not in seen:
                ordered.append(label)
                seen.add(label)
    for label in labels:
        if label not in seen:
            ordered.append(label)
            seen.add(label)
    return ordered


def _is_website_style(pages: list[dict]) -> bool:
    kinds = {_page_kind(page) for page in pages}
    return bool(kinds & {"auth", "demo", "browse", "product", "cart", "checkout", "confirmation"}) or any(
        _infer_nav_label(page) in {"Home", "Shop", "Product", "Cart", "Checkout"}
        for page in pages
    )


def _infer_nav_layout(pages: list[dict]) -> str:
    if _is_website_style(pages):
        return "topbar"

    sidebar_hits = 0
    topbar_hits = 0

    for page in pages:
        haystacks = [
            page.get("name", ""),
            page.get("screen_title", ""),
            page.get("description", ""),
            " ".join(page.get("components", []) or []),
        ]
        for section in page.get("sections", []) or []:
            haystacks.extend([
                section.get("section_name", ""),
                section.get("purpose", ""),
                " ".join(section.get("components", []) or []),
            ])
        hay = _norm_text(" ".join(haystacks))
        if any(k in hay for k in ["sidebar", "side nav", "left rail"]):
            sidebar_hits += 1
        if any(k in hay for k in ["navbar", "navigation", "topbar", "header", "menu"]):
            topbar_hits += 1

    if sidebar_hits >= max(2, topbar_hits + 1):
        return "sidebar"
    if topbar_hits > 0 and sidebar_hits > 0:
        return "hybrid"
    return "topbar"


def _clone_page_for_flow(page: dict, suffix: str) -> dict:
    clone = dict(page)
    clone["id"] = f"{page.get('id', 'page')}_{suffix}"
    return clone


def _branch_label_for_feature(feature_name: str, feature_pages: list[dict], fallback_home: str) -> str:
    for page in feature_pages:
        if _page_kind(page) != "base":
            label = _clean_feature_label(page.get("screen_title", "") or page.get("name", ""))
            if label and _norm_text(label) not in NAV_GENERIC_LABELS:
                return f"{fallback_home} -> {label}"
    feature_label = _clean_feature_label(feature_name)
    return f"{fallback_home} -> {feature_label or 'Feature'}"


def _website_branch_order(page: dict) -> tuple[int, int, str]:
    kind = _page_kind(page)
    text = _norm_text(" ".join([
        page.get("name", ""),
        page.get("screen_title", ""),
        page.get("description", ""),
    ]))

    if kind == "base":
        rank = 0
    elif kind == "auth":
        rank = 10
    elif kind == "demo":
        rank = 12
    elif kind == "browse":
        rank = 20
    elif kind == "product":
        rank = 30
    elif kind == "cart":
        rank = 40
    elif kind == "checkout":
        rank = 50
    elif kind == "confirmation":
        rank = 70
    else:
        rank = 24

    if "home page" in text or "landing page" in text:
        rank = min(rank, 5 if kind != "base" else 0)
    if any(k in text for k in ["about", "story", "our story"]):
        rank = min(rank, 22)
    if any(k in text for k in ["sustainability", "mission", "values"]):
        rank = min(rank, 26)
    if any(k in text for k in ["events", "tours"]):
        rank = min(rank, 28)
    if any(k in text for k in ["select date", "select time", "schedule", "booking form", "registration"]):
        rank = min(rank, 36)
    if any(k in text for k in ["shipping", "address"]):
        rank = 52
    if any(k in text for k in ["billing", "payment", "upi", "card"]):
        rank = 58
    if any(k in text for k in ["confirmed", "confirmation", "thank you", "receipt"]):
        rank = 72

    return rank, _state_rank(page)[1], page.get("name", "")


def _app_branch_order(page: dict) -> tuple[int, int, str]:
    rank, tie = _state_rank(page)
    text = _norm_text(" ".join([
        page.get("name", ""),
        page.get("screen_title", ""),
        page.get("description", ""),
        page.get("ui_state", ""),
    ]))

    if any(k in text for k in ["incoming popup", "incoming call popup", "dialer", "calling popover"]):
        rank = min(rank, 10)
    if any(k in text for k in ["menu", "options", "kebab", "popover"]):
        rank = min(rank, 20)
    if any(k in text for k in ["panel", "card", "detail", "notes", "transcript", "voicemail"]):
        rank = min(rank, 24)
    if any(k in text for k in ["hold", "resume", "transfer", "search results", "selected contact"]):
        rank = min(rank, 28)
    if any(k in text for k in ["confirm", "success", "deleted", "saved", "completed", "ended"]):
        rank = max(rank, 58)

    return rank, tie, page.get("name", "")


def _infer_action_family(page: dict, feature_name: str, root_title: str) -> str:
    click_text = " ".join(page.get("click_target_keywords", []) or [])
    text = _norm_text(" ".join([
        click_text,
        page.get("screen_title", ""),
        page.get("feature_group", ""),
        feature_name,
        page.get("description", ""),
        page.get("ui_state", ""),
    ]))

    action_label = _match_pattern_label(text, ACTION_LABEL_PATTERNS)
    if action_label:
        return action_label

    feature_label = _clean_feature_label(feature_name)
    if feature_label and not _is_generic_base_title(feature_label) and _norm_text(feature_label) != _norm_text(root_title):
        return feature_label

    screen_label = _clean_feature_label(page.get("screen_title", "") or page.get("name", ""))
    if screen_label and not _is_generic_base_title(screen_label) and _norm_text(screen_label) != _norm_text(root_title):
        return screen_label

    if click_text.strip():
        return f"Open {_pretty_title(click_text.strip())}"

    return "View Next State"


def _derive_effective_screen_title(
    page: dict,
    root_title: str,
    branch_action: str,
    previous_title: str = "",
) -> str:
    screen_title = _pretty_title(page.get("screen_title", "") or page.get("name", "Screen"))
    text = _norm_text(" ".join([
        page.get("screen_title", ""),
        page.get("name", ""),
        page.get("description", ""),
        page.get("ui_state", ""),
        " ".join(page.get("click_target_keywords", []) or []),
    ]))

    matched_state = _match_pattern_label(text, STATE_TITLE_PATTERNS)
    if matched_state:
        screen_title = matched_state

    if _is_generic_base_title(screen_title) or _norm_text(screen_title) == _norm_text(previous_title):
        if matched_state:
            screen_title = matched_state
        elif branch_action and not _is_generic_base_title(branch_action):
            if _norm_text(root_title) == _norm_text(screen_title) or _is_generic_base_title(screen_title):
                screen_title = f"{root_title} with {branch_action}"
            else:
                screen_title = branch_action
        else:
            feature_label = _clean_feature_label(page.get("feature_group", ""))
            if feature_label and not _is_generic_base_title(feature_label):
                screen_title = feature_label

    if _norm_text(screen_title) == _norm_text(root_title) and branch_action and not _is_generic_base_title(branch_action):
        screen_title = f"{root_title} with {branch_action}"

    return screen_title


def _dedupe_branch_pages(pages: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for page in pages:
        key = (
            _norm_text(page.get("screen_title", "")),
            _norm_text(page.get("description", ""))[:160],
            _norm_text(" ".join(page.get("click_target_keywords", []) or [])),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(page)
    return deduped


def _build_app_interaction_flows(
    pages: list[dict],
    anchor_page: dict,
    by_feature: dict[str, list[dict]],
) -> list[dict]:
    flow_groups = []
    root_title = _clean_feature_label(anchor_page.get("screen_title", "Dashboard")) or "Dashboard"
    anchor_feature_key = anchor_page.get("feature_group", "") or anchor_page.get("name", "")

    for feature_name, feature_pages in by_feature.items():
        if feature_name == anchor_feature_key:
            continue

        ordered_pages = sorted(feature_pages, key=_app_branch_order)
        base_pages = []
        family_pages: dict[str, list[dict]] = {}
        feature_family = _infer_action_family({}, feature_name, root_title)
        feature_is_specific = (
            feature_family
            and feature_family not in GENERIC_ACTION_LABELS
            and _norm_text(feature_family) != _norm_text(root_title)
            and _norm_text(feature_family) != _norm_text(_clean_feature_label(feature_name))
        )

        for page in ordered_pages:
            state_text = _norm_text(" ".join([
                page.get("screen_title", ""),
                page.get("description", ""),
                page.get("ui_state", ""),
                " ".join(page.get("click_target_keywords", []) or []),
            ]))
            has_secondary_state = any(
                token in state_text
                for token in ["popup", "modal", "dialog", "panel", "drawer", "popover", "card", "detail", "incoming", "ringing", "hold", "resume", "transfer", "notes", "transcript", "voicemail", "missed call"]
            )
            is_base_like = (
                _page_kind(page) == "base"
                or (
                    _norm_text(page.get("screen_title", "")) == _norm_text(root_title)
                    and not has_secondary_state
                    and not page.get("click_target_keywords")
                )
            )
            if is_base_like:
                base_pages.append(page)
                continue

            family = feature_family if feature_is_specific else _infer_action_family(page, feature_name, root_title)
            family_pages.setdefault(family, []).append(page)

        if not family_pages:
            family_label = _infer_action_family(ordered_pages[0], feature_name, root_title) if ordered_pages else _clean_feature_label(feature_name)
            family_pages[family_label] = ordered_pages[:]

        for family, branch_states in family_pages.items():
            branch_pages = [_clone_page_for_flow(anchor_page, f"{_norm_text(feature_name).replace(' ', '_') or 'flow'}_{_norm_text(family).replace(' ', '_') or 'branch'}_root")]
            branch_pages.extend(_dedupe_branch_pages(base_pages[:1] + branch_states))
            branch_name = f"{root_title} -> {family}"
            branch_goal = ""
            non_base_states = [page for page in branch_states if _page_kind(page) != "base"]
            if non_base_states:
                branch_goal = _derive_effective_screen_title(non_base_states[-1], root_title, family)
            flow_groups.append({
                "name": branch_name,
                "pages": branch_pages,
                "branch_root": root_title,
                "branch_trigger": family,
                "branch_goal": branch_goal,
                "branch_kind": "interaction",
            })

    if not flow_groups:
        ordered_pages = sorted(pages, key=_app_branch_order)
        flow_groups.append({
            "name": root_title,
            "pages": ordered_pages,
            "branch_root": root_title,
            "branch_trigger": "",
            "branch_goal": "",
            "branch_kind": "linear",
        })

    return flow_groups


def _attach_project_structure(pages: list[dict]) -> tuple[list[dict], dict]:
    if not pages:
        return [], {"layout": "topbar", "primary_links": []}

    primary_links = []
    seen_links = set()
    feature_active = {}

    for page in pages:
        label = _infer_nav_label(page)
        feature_key = _norm_text(page.get("feature_group", "") or page.get("screen_title", ""))
        if feature_key and feature_key not in feature_active:
            feature_active[feature_key] = label
        if label and label not in seen_links:
            primary_links.append(label)
            seen_links.add(label)

    primary_links = _sort_nav_links(primary_links)
    if len(primary_links) > 8:
        primary_links = primary_links[:8]

    layout = _infer_nav_layout(pages)
    anchor_candidates = sorted(pages, key=_state_rank)
    anchor_page = anchor_candidates[0] if anchor_candidates else pages[0]
    website_style = _is_website_style(pages)

    by_feature: dict[str, list[dict]] = {}
    for page in pages:
        key = page.get("feature_group", "") or page.get("name", "")
        by_feature.setdefault(key, []).append(page)

    flow_groups = []

    def push_flow(name: str, branch_pages: list[dict], include_anchor: bool = True):
        if not branch_pages:
            return
        slug = _norm_text(name).replace(" ", "_") or "root"
        pages_for_flow = []
        if include_anchor:
            pages_for_flow.append(_clone_page_for_flow(anchor_page, f"{slug}_start"))
        for idx, page in enumerate(branch_pages, start=1):
            pages_for_flow.append(_clone_page_for_flow(page, f"{slug}_{idx}"))
        flow_groups.append({
            "name": name,
            "pages": pages_for_flow,
        })

    home_label = _clean_feature_label(anchor_page.get("screen_title", "Home")) or "Home"

    if website_style:
        page_groups = {
            "auth": [],
            "demo": [],
            "browse": [],
            "product": [],
            "cart": [],
            "checkout": [],
            "confirmation": [],
            "feature": [],
        }
        anchor_feature_key = anchor_page.get("feature_group", "") or anchor_page.get("name", "")
        for feature_name, feature_pages in by_feature.items():
            ordered_feature_pages = sorted(feature_pages, key=_website_branch_order)
            if feature_name == anchor_feature_key:
                continue
            kind = _page_kind(ordered_feature_pages[0])
            if kind in page_groups:
                page_groups[kind].append((feature_name, ordered_feature_pages))
            else:
                page_groups["feature"].append((feature_name, ordered_feature_pages))

        for feature_name, feature_pages in page_groups["demo"]:
            push_flow(_branch_label_for_feature(feature_name, feature_pages, home_label), feature_pages)

        for feature_name, feature_pages in page_groups["auth"]:
            push_flow(_branch_label_for_feature(feature_name, feature_pages, home_label), feature_pages)

        browse_seeds = []
        for feature_name, feature_pages in page_groups["browse"]:
            push_flow(_branch_label_for_feature(feature_name, feature_pages, home_label), feature_pages)
            browse_seeds.extend(feature_pages[:1])

        for feature_name, feature_pages in page_groups["feature"]:
            push_flow(_branch_label_for_feature(feature_name, feature_pages, home_label), feature_pages)

        product_pages = [page for _, pages_list in page_groups["product"] for page in pages_list]
        cart_pages = [page for _, pages_list in page_groups["cart"] for page in pages_list]
        checkout_pages = [page for _, pages_list in page_groups["checkout"] for page in pages_list]
        confirmation_pages = [page for _, pages_list in page_groups["confirmation"] for page in pages_list]

        if product_pages:
            product_branch = []
            if browse_seeds:
                product_branch.extend([browse_seeds[0]])
            product_branch.extend(sorted(product_pages, key=_website_branch_order))
            push_flow(f"{home_label} -> Product Detail", product_branch)

        if cart_pages or checkout_pages or confirmation_pages:
            purchase_branch = []
            if product_pages:
                purchase_branch.append(sorted(product_pages, key=_website_branch_order)[0])
            purchase_branch.extend(sorted(cart_pages, key=_website_branch_order))
            purchase_branch.extend(sorted(checkout_pages, key=_website_branch_order))
            purchase_branch.extend(sorted(confirmation_pages, key=_website_branch_order))
            push_flow(f"{home_label} -> Checkout", purchase_branch)

        if not flow_groups:
            feature_order = []
            for feature_name in by_feature:
                if feature_name != anchor_feature_key:
                    feature_order.append(feature_name)
            if not feature_order:
                anchor_pages = sorted(by_feature.get(anchor_feature_key, pages), key=_website_branch_order)
                if anchor_pages:
                    flow_groups.append({
                        "name": home_label,
                        "pages": anchor_pages,
                    })
            else:
                current_flow = None
                for feature_name in feature_order:
                    feature_pages = sorted(by_feature[feature_name], key=_website_branch_order)
                    first_rank = _state_rank(feature_pages[0])[0] if feature_pages else 50
                    starts_new_flow = current_flow is None or first_rank <= FLOW_RESET_THRESHOLD
                    if starts_new_flow:
                        current_flow = {
                            "name": _branch_label_for_feature(feature_name, feature_pages, home_label),
                            "pages": [_clone_page_for_flow(anchor_page, _norm_text(feature_name).replace(" ", "_") or "root")] + list(feature_pages),
                            "branch_root": home_label,
                            "branch_trigger": _clean_feature_label(feature_name),
                            "branch_goal": "",
                            "branch_kind": "website",
                        }
                        flow_groups.append(current_flow)
                    else:
                        current_flow["pages"].extend(feature_pages)
    else:
        flow_groups = _build_app_interaction_flows(pages, anchor_page, by_feature)

    def _flow_group_name(features: list[str]) -> str:
        cleaned = []
        for feature in features:
            label = _clean_feature_label(feature)
            if label and label not in cleaned:
                cleaned.append(label)
        if not cleaned:
            return "Main Flow"
        if len(cleaned) == 1:
            return cleaned[0] + " Flow"
        return cleaned[0] + " To " + cleaned[-1]

    enriched = []
    for flow_index, flow in enumerate(flow_groups, start=1):
        flow_name = flow.get("name") or _flow_group_name(flow.get("features", []))
        flow_id = f"flow_{flow_index}_{_norm_text(flow_name).replace(' ', '_') or flow_index}"
        flow_pages = flow["pages"]
        flow_total = len(flow_pages)
        branch_root = flow.get("branch_root", home_label)
        branch_trigger = flow.get("branch_trigger", "")
        branch_goal = flow.get("branch_goal", "")
        branch_kind = flow.get("branch_kind", "flow")
        previous_screen_title = ""
        for idx, page in enumerate(flow_pages):
            prev_page = flow_pages[idx - 1] if idx > 0 else None
            next_page = flow_pages[idx + 1] if idx + 1 < len(flow_pages) else None
            if idx == 0 and branch_root:
                effective_screen_title = branch_root
            else:
                effective_screen_title = _derive_effective_screen_title(
                    page,
                    branch_root or home_label,
                    branch_trigger or _clean_feature_label(flow_name),
                    previous_screen_title,
                )
            active_label = _infer_nav_label(page)
            navigation = {
                "layout": layout,
                "primary_links": primary_links,
                "active_label": active_label,
            }
            journey = {
                "previous_screen": previous_screen_title if prev_page else "",
                "next_screen": _derive_effective_screen_title(
                    next_page,
                    branch_root or home_label,
                    branch_trigger or _clean_feature_label(flow_name),
                    effective_screen_title,
                ) if next_page else "",
                "previous_page_name": prev_page.get("name", "") if prev_page else "",
                "next_page_name": next_page.get("name", "") if next_page else "",
                "branch_root": branch_root,
                "branch_trigger": branch_trigger,
                "branch_goal": branch_goal,
                "branch_kind": branch_kind,
            }
            annotation_text = page.get("annotation_text", "")
            annotation_target_keywords = page.get("annotation_target_keywords", [])
            if not annotation_text and next_page and page.get("click_target_keywords"):
                click_label = ", ".join(page.get("click_target_keywords", [])[:2]).strip()
                next_label = journey["next_screen"] or next_page.get("screen_title", "") or next_page.get("name", "")
                if click_label and next_label:
                    annotation_text = f"Click {click_label} to open {next_label}."
                    annotation_target_keywords = page.get("click_target_keywords", [])
            enriched.append({
                **page,
                "name": f"{flow_name} — {effective_screen_title}",
                "screen_title": effective_screen_title,
                "flow_group_id": flow_id,
                "flow_group": flow_name,
                "flow_group_step": idx + 1,
                "flow_group_total": flow_total,
                "annotation_text": annotation_text,
                "annotation_target_keywords": annotation_target_keywords,
                "navigation": navigation,
                "journey": journey,
                "branch_root": branch_root,
                "branch_trigger": branch_trigger,
                "branch_goal": branch_goal,
                "branch_kind": branch_kind,
                "project_navigation": {
                    "layout": layout,
                    "primary_links": primary_links,
                },
            })
            previous_screen_title = effective_screen_title

    return enriched, {
        "layout": layout,
        "primary_links": primary_links,
    }


def _state_rank(state: dict) -> tuple[int, int]:
    text = " ".join([
        state.get("screen_title", ""),
        state.get("name", ""),
        state.get("ui_state", ""),
        state.get("description", ""),
    ])
    norm = _norm_text(text)

    score = 50
    if any(k in norm for k in DEFAULT_KEYWORDS):
        score = 0
    elif any(k in norm for k in ["category", "catalog", "browse", "browsing", "results", "collection", "shop"]):
        score = 12
    elif any(k in norm for k in ["quick view", "product detail", "detail page", "detail view", "pdp"]):
        score = 18
    elif "call detail panel" in norm or "detail panel" in norm or "detail card" in norm:
        score = 18
    elif any(k in norm for k in ["cart", "mini cart", "bag", "basket"]):
        score = 32
    elif any(k in norm for k in ["panel", "card", "selected", "detail", "active"]):
        score = 24
    elif any(k in norm for k in ["menu", "options", "dropdown", "popover"]):
        score = 30
    elif any(k in norm for k in ["shipping", "address"]):
        score = 44
    elif any(k in norm for k in ["payment", "billing"]):
        score = 48
    elif any(k in norm for k in ["review", "place order"]):
        score = 56
    elif any(k in norm for k in ["order confirmation", "thank you", "receipt"]):
        score = 68
    elif any(k in norm for k in ["modal", "dialog", "popup", "confirm", "confirmation"]):
        score = 40
    elif any(k in norm for k in ["success", "saved", "deleted", "completed", "ended", "logged"]):
        score = 60

    # Prefer simpler/core states when rank ties
    tie = len(norm.split())
    return score, tie


def _global_baseline_screen(states: list[dict]) -> dict | None:
    candidates = []
    for idx, state in enumerate(states):
        feature, screen = _split_feature_and_screen(state, idx + 1)
        enriched = {**state, "feature_group": feature, "screen_title": screen}
        rank = _state_rank(enriched)
        if rank[0] <= 18:
            candidates.append((rank, enriched))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _dedupe_states(states: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for idx, state in enumerate(states, start=1):
        feature, screen = _split_feature_and_screen(state, idx)
        key = (
            _norm_text(feature),
            _norm_text(screen),
            _norm_text(state.get("ui_state", "")),
            _norm_text(state.get("description", ""))[:120],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append({
            **state,
            "feature_group": feature,
            "screen_title": screen,
            "name": f"{feature} — {screen}",
        })
    return result


def _synthesize_baseline_state(feature_group: str, first_state: dict, global_base: dict | None, idx: int) -> dict:
    base_title = (global_base or {}).get("screen_title") or "Default State"
    base_desc = (global_base or {}).get("description") or ""
    description = (
        f"Baseline screen for the {feature_group} flow before the user triggers the next interaction."
    )
    if base_desc:
        description += " " + base_desc

    return {
        "id": f"synth_{idx}",
        "name": f"{feature_group} — {base_title}",
        "screen_title": base_title,
        "feature_group": feature_group,
        "ui_state": "default",
        "description": description,
        "components": first_state.get("components", []),
        "click_target_keywords": first_state.get("click_target_keywords", []),
        "annotation_text": "",
        "annotation_target_keywords": [],
        "width": first_state.get("width", 1440),
        "height": first_state.get("height", 1080),
    }


def _cleanup_flow_states(ui_states: list[dict]) -> list[dict]:
    cleaned = _dedupe_states(ui_states)
    if not cleaned:
        return []

    global_base = _global_baseline_screen(cleaned)

    grouped: dict[str, list[dict]] = {}
    for state in cleaned:
        grouped.setdefault(state["feature_group"], []).append(state)

    ordered = []
    synth_idx = 1
    for feature_group, states in grouped.items():
        states = sorted(states, key=_state_rank)
        if states:
            first_rank = _state_rank(states[0])[0]
            if first_rank > 18:
                states.insert(0, _synthesize_baseline_state(feature_group, states[0], global_base, synth_idx))
                synth_idx += 1

        total = len(states)
        for step, state in enumerate(states, start=1):
            ordered.append({
                **state,
                "flow_step": step,
                "flow_total": total,
                "annotation_target_keywords": state.get("annotation_target_keywords", []),
                "click_target_keywords": state.get("click_target_keywords", []),
            })
    return ordered


def _normalise_pages(pages: list[dict]) -> list[dict]:
    normalised = []
    total = len(pages)
    for i, page in enumerate(pages, start=1):
        p = dict(page)
        p.setdefault("id", f"frame{i}")
        p.setdefault("name", f"Frame {i}")
        p.setdefault("screen_title", p.get("name", f"Frame {i}"))
        p.setdefault("feature_group", p.get("name", f"Frame {i}").split("—")[0].strip())
        p.setdefault("flow_step", i)
        p.setdefault("flow_total", total)
        p.setdefault("click_target_keywords", [])
        p.setdefault("annotation_text", "")
        p.setdefault("annotation_target_keywords", [])
        p.setdefault("width", 1440)
        p.setdefault("height", 1080)
        p.setdefault("images", [])
        p.setdefault("sections", [])
        p.setdefault("flow_group_id", p.get("flow_group", p.get("feature_group", "main_flow")))
        p.setdefault("flow_group", p.get("feature_group", "Main Flow"))
        p.setdefault("flow_group_step", p.get("flow_step", i))
        p.setdefault("flow_group_total", p.get("flow_total", total))
        p.setdefault("navigation", {})
        p.setdefault("journey", {})
        p.setdefault("project_navigation", {})
        p.setdefault("branch_root", "")
        p.setdefault("branch_trigger", "")
        p.setdefault("branch_goal", "")
        p.setdefault("branch_kind", "")
        p.setdefault("column_group", "")
        p.setdefault("column_group_id", "")
        p.setdefault("column_group_order", 0)
        p.setdefault("column_label", "")
        p.setdefault("column_title", "")
        p.setdefault("row_label", "")
        p.setdefault("row_order", 0)
        normalised.append(p)
    return normalised


async def _run_flow_synthesis(ui_states: list[dict], content_context: dict) -> list[dict]:
    content_str = json.dumps(content_context or {}, indent=2)
    state_str = json.dumps(ui_states, indent=2)
    prompt = FLOW_SYNTHESIS_PROMPT.replace(
        "{content_context}",
        _limit_text(content_str, PLANNER_FLOW_CONTEXT_CHAR_LIMIT),
    ).replace(
        "{ui_states}",
        _limit_text(state_str, PLANNER_FLOW_STATES_CHAR_LIMIT),
    )

    response = await generate_content_with_retry(
        client=client,
        model=planner_model,
        contents=prompt,
        config={"temperature": 0.2},
        log_tag="PLANNER",
        action="Run flow synthesis",
    )
    return _parse_state_list(response.text)


def _call_flow_page(
    idx: int,
    feature_group: str,
    screen_title: str,
    description: str,
    flow_step: int,
    flow_total: int,
    click_target_keywords: list | None = None,
    annotation_text: str | None = None,
    annotation_target_keywords: list | None = None,
    ui_state: str = "default",
    components: list | None = None,
) -> dict:
    return {
        "id": f"flow_{idx}",
        "name": f"{feature_group} — {screen_title}",
        "screen_title": screen_title,
        "description": description,
        "ui_state": ui_state,
        "feature_group": feature_group,
        "flow_step": flow_step,
        "flow_total": flow_total,
        "click_target_keywords": click_target_keywords or [],
        "annotation_text": annotation_text or "",
        "annotation_target_keywords": annotation_target_keywords or [],
        "width": 1440,
        "height": 1080,
        "sections": [{
            "section_name": "Main Content",
            "purpose": description,
            "components": components or [],
        }],
        "images": [],
    }
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        
def _default_title_from_prompt(user_prompt: str, layout_context: dict | None = None) -> str:
    prompt = (user_prompt or "").strip()
    if prompt:
        first_sentence = re.split(r"[.!?\n]", prompt, maxsplit=1)[0].strip(" '\"")
        if first_sentence:
            compact = re.sub(r"\s+", " ", first_sentence)
            if len(compact) <= 60:
                return compact

    layout_type = _pretty_title((layout_context or {}).get("layout_type", ""))
    if layout_type and layout_type != "State":
        return layout_type
    return "Product Design"


def _frames_from_layout_context(layout_context: dict | None, frames_to_generate: list[str] | None) -> list[str]:
    frames = [str(frame).strip() for frame in (frames_to_generate or []) if str(frame).strip()]
    if frames:
        return frames

    detected_sections = layout_context.get("detected_sections", []) if layout_context else []
    sections = [str(section).strip() for section in detected_sections if str(section).strip()]
    if sections:
        if PLANNER_LAYOUT_SECTION_LIMIT > 0:
            sections = sections[:PLANNER_LAYOUT_SECTION_LIMIT]
        return [_pretty_title(section) for section in sections]

    layout_type = _pretty_title((layout_context or {}).get("layout_type", "")) if layout_context else ""
    return [layout_type or "Main Screen"]


def _build_plan_from_layout_context(
    user_prompt: str,
    layout_context: dict | None = None,
    frames_to_generate: list[str] | None = None,
) -> dict:
    layout_context = layout_context or {}
    frames = _frames_from_layout_context(layout_context, frames_to_generate)
    total = len(frames)
    layout_type = _pretty_title(layout_context.get("layout_type", ""))
    feature_group = layout_type if layout_type and layout_type != "State" else "Dashboard"

    layout_description = re.sub(r"\s+", " ", layout_context.get("layout_description", "")).strip()
    visual_style = re.sub(r"\s+", " ", layout_context.get("visual_style", "")).strip()
    viewport_fill_guidance = re.sub(r"\s+", " ", layout_context.get("viewport_fill_guidance", "")).strip()
    detected_components = [str(component).strip() for component in layout_context.get("detected_components", []) if str(component).strip()]
    detected_sections = [str(section).strip() for section in layout_context.get("detected_sections", []) if str(section).strip()]

    sections = [{
        "section_name": "Main Content",
        "purpose": layout_description or "Recreate the supplied interface as a full-frame product screen.",
        "components": detected_components[:16],
    }]

    annotation_text = ""
    if layout_context.get("outer_padding_present") and viewport_fill_guidance:
        annotation_text = viewport_fill_guidance

    pages = []
    for i, frame_name in enumerate(frames):
        name = frame_name or f"Frame {i + 1}"
        screen_title = name.split("—")[-1].strip() if "—" in name else name
        description_parts = [f"{screen_title} screen derived from the supplied visual reference."]
        if layout_description:
            description_parts.append(layout_description)
        if visual_style:
            description_parts.append(f"Visual style: {visual_style}")
        pages.append({
            "id": f"layout_frame_{i + 1}",
            "name": name,
            "screen_title": screen_title,
            "description": " ".join(description_parts).strip(),
            "ui_state": "default" if i == 0 else "derived",
            "feature_group": feature_group,
            "flow_step": i + 1,
            "flow_total": total,
            "click_target_keywords": [],
            "annotation_text": annotation_text if i == 0 else "",
            "annotation_target_keywords": detected_sections[:2],
            "width": 1440,
            "height": 1080,
            "sections": sections,
            "images": [],
        })

    title = _default_title_from_prompt(user_prompt, layout_context)
    return {"project_title": title, "total_pages": len(pages), "pages": pages}


def _coerce_pages_from_alternative_shapes(data: dict) -> list[dict]:
    for key in ("pages", "frames", "screens", "states", "ui_states"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return value

    feature_flows = data.get("feature_flows")
    if isinstance(feature_flows, list):
        flattened = []
        for flow in feature_flows:
            if not isinstance(flow, dict):
                continue
            flow_pages = None
            for key in ("pages", "frames", "screens", "states"):
                candidate = flow.get(key)
                if isinstance(candidate, list) and candidate:
                    flow_pages = candidate
                    break
            if not flow_pages:
                continue
            feature_name = flow.get("feature_group") or flow.get("name") or "Feature"
            flow_total = len(flow_pages)
            for idx, page in enumerate(flow_pages):
                if isinstance(page, dict):
                    cloned = dict(page)
                elif isinstance(page, str):
                    cloned = {"name": page}
                else:
                    continue
                cloned.setdefault("feature_group", feature_name)
                cloned.setdefault("flow_step", idx + 1)
                cloned.setdefault("flow_total", flow_total)
                flattened.append(cloned)
        if flattened:
            return flattened

    frame_names = data.get("frames_to_generate")
    if isinstance(frame_names, list) and frame_names:
        return [{"name": str(frame)} for frame in frame_names if str(frame).strip()]

    return []


def parse_plan(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    list_start = cleaned.find('[')
    obj_start = cleaned.find('{')
    if list_start != -1 and (obj_start == -1 or list_start < obj_start):
        list_end = cleaned.rfind(']')
        if list_end != -1:
            cleaned = cleaned[list_start:list_end + 1]
    else:
        start = cleaned.find('{')
        end   = cleaned.rfind('}')
        if start != -1 and end != -1:
            cleaned = cleaned[start:end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\n\nRaw:\n{raw[:800]}")

    if isinstance(data, list):
        data = {
            "project_title": "Product Design",
            "total_pages": len(data),
            "pages": data,
        }

    pages = _coerce_pages_from_alternative_shapes(data)
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            page = {"name": str(page)}
            pages[i] = page
        if "id" not in page:
            page["id"] = f"frame{i+1}"
        if "screen_title" not in page:
            page["screen_title"] = page.get("name", f"Frame {i+1}")
        if "feature_group" not in page:
            page["feature_group"] = page.get("name", f"Frame {i+1}").split("—")[0].strip()
        if "flow_step" not in page:
            page["flow_step"] = i + 1
        if "flow_total" not in page:
            page["flow_total"] = len(pages)
        if "click_target_keywords" not in page:
            page["click_target_keywords"] = []
        if "annotation_text" not in page:
            page["annotation_text"] = ""
        if "annotation_target_keywords" not in page:
            page["annotation_target_keywords"] = []
        if "width" not in page:
            page["width"] = 1440
        if "height" not in page:
            page["height"] = 1080
        if "images" not in page:
            page["images"] = []
        if "sections" not in page:
            page["sections"] = []

    return {
        "project_title": data.get("project_title", "Untitled Project"),
        "total_pages":   data.get("total_pages", len(pages)),
        "pages":         pages,
        "navigation_model": data.get("navigation_model", {}),
    }


def _parse_state_list(raw: str) -> list:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start = cleaned.find('[')
    end   = cleaned.rfind(']')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        log.warn("PLANNER", "State extraction JSON failed — falling back to generic flow planner")
        return []


def _build_plan_from_states(ui_states: list, user_prompt: str) -> dict:
    pages = []
    for i, state in enumerate(ui_states):
        pages.append({
            "id":            state.get("id", f"frame{i+1}"),
            "name":          state.get("name", f"Frame {i+1}"),
            "screen_title":  state.get("screen_title", state.get("name", f"Frame {i+1}")),
            "description":   state.get("description", ""),
            "ui_state":      state.get("ui_state", "default"),
            "feature_group": state.get("feature_group", ""),
            "flow_step":     state.get("flow_step", i + 1),
            "flow_total":    state.get("flow_total", len(ui_states)),
            "click_target_keywords": state.get("click_target_keywords", []),
            "annotation_text": state.get("annotation_text", ""),
            "annotation_target_keywords": state.get("annotation_target_keywords", []),
            "width":         1440,
            "height":        state.get("height", 1080),
            "sections": [{
                "section_name": "Main Content",
                "purpose":      state.get("description", ""),
                "components":   state.get("components", []),
            }],
            "images": [],
        })
    title = "CRM System" if "crm" in user_prompt.lower() else "Product Design"
    return {"project_title": title, "total_pages": len(pages), "pages": pages}

async def run_planner(
    user_prompt: str,
    layout_context: dict = None,
    content_context: dict = None,
    frames_to_generate: list[str] | None = None,
    mode: str | None = None,
) -> dict:
    log.info("PLANNER", f"Starting — prompt: {user_prompt[:80]!r}")

    explicit_columns, explicit_rows, explicit_instruction_map = _extract_explicit_workflow_rows(content_context, user_prompt)
    if explicit_columns or explicit_rows:
        if explicit_columns:
            log.info("PLANNER", f"EXPLICIT COLUMN MODE — preserving {len(explicit_columns)} column(s) exactly")
            parsed = _build_plan_from_explicit_columns(
                columns=explicit_columns,
                instruction_map=explicit_instruction_map,
                user_prompt=user_prompt,
                content_context=content_context,
            )
        else:
            log.info("PLANNER", f"EXPLICIT FLOW MODE — preserving {len(explicit_rows)} workflow row(s) exactly")
            parsed = _build_plan_from_explicit_rows(
                rows=explicit_rows,
                instruction_map=explicit_instruction_map,
                user_prompt=user_prompt,
                content_context=content_context,
            )
        parsed["pages"] = _normalise_pages(parsed["pages"])
        parsed["total_pages"] = len(parsed["pages"])
        log.success("PLANNER",
            f"Plan ready — {parsed['project_title']!r}  frames={parsed['total_pages']}"
        )
        return parsed

    has_content = bool(
        content_context and (
            content_context.get("features") or
            content_context.get("key_workflows") or
            content_context.get("pages_or_screens")
        )
    )
    has_layout = bool(
        layout_context and (
            layout_context.get("layout_type") or
            layout_context.get("detected_sections") or
            layout_context.get("detected_components") or
            layout_context.get("layout_description")
        )
    )

    if has_content:
        log.info("PLANNER", "STRUCTURED MODE — extracting UI states from document")
        context_str      = json.dumps(content_context, indent=2)
        extractor_prompt = STATE_EXTRACTOR_PROMPT.replace(
            "{content_context}",
            _limit_text(context_str, PLANNER_EXTRACT_CONTEXT_CHAR_LIMIT),
        )

        ext_response = await generate_content_with_retry(
            client=client,
            model=planner_model,
            contents=extractor_prompt,
            config={"temperature": 0.2},
            log_tag="PLANNER",
            action="Extract structured UI states",
        )
        ui_states    = _parse_state_list(ext_response.text)

        if ui_states:
            log.info("PLANNER", f"Extracted {len(ui_states)} UI states")
            for s in ui_states[:20]:
                log.info("PLANNER", f"  → {s.get('name','?')}")

            synthesized = await _run_flow_synthesis(ui_states, content_context or {})
            if synthesized:
                log.info("PLANNER", f"Flow synthesis returned {len(synthesized)} state(s)")
                ui_states = synthesized
            else:
                log.warn("PLANNER", "Flow synthesis empty — using heuristic flow cleanup")

            cleaned_states = _cleanup_flow_states(ui_states)
            parsed = _build_plan_from_states(cleaned_states or ui_states, user_prompt)
            parsed["pages"] = _normalise_pages(parsed["pages"])
            parsed["pages"], parsed["navigation_model"] = _attach_project_structure(parsed["pages"])
            parsed["total_pages"] = len(parsed["pages"])
            log.success("PLANNER",
                f"Plan ready — {parsed['project_title']!r}  frames={parsed['total_pages']}"
            )
            return parsed
        else:
            log.warn("PLANNER", "State extraction empty — falling back to generic flow planner")

    if has_layout and (frames_to_generate or not has_content):
        log.info("PLANNER", "LAYOUT MODE — building plan from screenshot-derived context")
        parsed = _build_plan_from_layout_context(
            user_prompt=user_prompt,
            layout_context=layout_context,
            frames_to_generate=frames_to_generate,
        )
        parsed["pages"] = _normalise_pages(_cleanup_flow_states(parsed["pages"]) or parsed["pages"])
        parsed["pages"], parsed["navigation_model"] = _attach_project_structure(parsed["pages"])
        parsed["total_pages"] = len(parsed["pages"])
        log.success("PLANNER",
            f"Plan ready — project={parsed['project_title']!r}  pages={parsed['total_pages']}",
            extra={"mode": mode or "replicate", "total_pages": parsed["total_pages"]}
        )
        return parsed

    # GENERIC FLOW MODE
    log.info("PLANNER", "FLOW MODE — generic flow plan from prompt")
    full_prompt = FREE_PLANNER_PROMPT.replace("{user_prompt}", user_prompt)

    response = await generate_content_with_retry(
        client=client,
        model=planner_model,
        contents=full_prompt,
        config={"temperature": 0.3},
        log_tag="PLANNER",
        action="Build generic flow plan",
    )
    raw_text  = response.text
    log.debug("PLANNER", f"Raw response: {len(raw_text)} chars")

    parsed = parse_plan(raw_text)
    parsed["pages"] = _normalise_pages(_cleanup_flow_states(parsed["pages"]) or parsed["pages"])
    parsed["pages"], parsed["navigation_model"] = _attach_project_structure(parsed["pages"])
    parsed["total_pages"] = len(parsed["pages"])
    log.success("PLANNER",
        f"Plan ready — project={parsed['project_title']!r}  pages={parsed['total_pages']}",
        extra={"total_pages": parsed["total_pages"]}
    )
    for p in parsed["pages"]:
        log.info("PLANNER",
            f"  → {p['name']}  ({p['width']}×{p['height']}px)  images={len(p.get('images',[]))}",
            extra={"page_id": p["id"]}
        )
    return parsed
