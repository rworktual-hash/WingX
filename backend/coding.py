import os
import json
import re
import asyncio
from google import genai
from dotenv import load_dotenv

from themes import select_themes_for_prompt
from llm_utils import generate_content_with_retry
from log_writer import write_log
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY1"))
CODING_LAYOUT_TEXT_LIMIT = int(os.getenv("CODING_LAYOUT_TEXT_LIMIT", "240"))
CODING_ATTACHMENT_PRIMARY_LIMIT = int(os.getenv("CODING_ATTACHMENT_PRIMARY_LIMIT", "9000"))
CODING_ATTACHMENT_CONTEXT_LIMIT = int(os.getenv("CODING_ATTACHMENT_CONTEXT_LIMIT", "6000"))
CODING_ATTACHMENT_COMPONENT_LIMIT = int(os.getenv("CODING_ATTACHMENT_COMPONENT_LIMIT", "6000"))
CODING_LOG_FILE = os.getenv("FIGMA_LOG_FILENAME", "figma_debug.log")

IMAGE_PROXY_BASE = os.getenv(
    "IMAGE_PROXY_BASE",
    "https://wingx-2vpp.onrender.com/api/image-proxy"
)


def _write_linewise_log(section: str, content, filename: str = CODING_LOG_FILE):
    text = str(content or "")
    lines = text.splitlines() or [text]
    write_log(f"{section} | BEGIN", filename=filename)
    for line in lines:
        write_log(f"{section} | {line}", filename=filename)
    write_log(f"{section} | END", filename=filename)


def _write_json_log(section: str, payload, filename: str = CODING_LOG_FILE):
    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception:
        text = str(payload)
    _write_linewise_log(section, text, filename=filename)


def _safe_page_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    return slug or "page"


def _write_page_json_dump(page_name: str, page_id: str, payload: dict) -> str:
    logs_dir = os.path.join(os.path.dirname(__file__), "logs", "page_json")
    os.makedirs(logs_dir, exist_ok=True)
    file_name = f"{_safe_page_slug(page_name)}--{_safe_page_slug(page_id)}.json"
    path = os.path.join(logs_dir, file_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def _page_log_filename(page_name: str, page_id: str) -> str:
    return os.path.join(
        "page_logs",
        f"{_safe_page_slug(page_name)}--{_safe_page_slug(page_id)}.log"
    )

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────

CODING_SYSTEM_PROMPT = """
You are a senior UI/UX designer generating a professional Figma page layout.

Your task is to generate a JSON array of UI elements that will become the "children" of ONE tall Figma frame representing a full webpage.

The layout must resemble a modern, real production website.
It may also represent a real product screen, dashboard, CRM, inbox, modal, or application view.


OUTPUT RULES:
------------
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
12. Elements may also include shared color-token metadata:
   styleGroupName, styleGroupFamily
13. Text elements may also include typography metadata:
   textStyleName, textRole
14. Names must be semantic and stable, suitable for export. Avoid random labels like "Group 1", "Frame copy", or "Text 7".
15. Button text must be fit to the button size.
16. The provided text content must be in the centre of components.
17. Always must be provide the high quality Ui design based on the user requirements.
18. Product UI pages must include meaningful primary content beyond the navbar or shell.
19. Do NOT output a nearly empty canvas, blank form shell, or placeholder-only screen.
20. Do NOT generate unwanted intermediary trigger pages. Show the actual resulting state for the planned step.
21. Do NOT reuse footer/legal/copyright text as table rows, form values, card content, or dashboard data.
22. Footer/legal text such as "Terms of Service", "Privacy Policy", or "All rights reserved" may appear at most once in a true footer area only, never repeated inside main content.
23. For user-management tables, rows must contain person-like data such as names, emails, roles, statuses, timestamps, and actions.
24. For bot/dashboard tables, rows must contain bot-like data such as bot names, statuses, phases, timestamps, metrics, or actions.
25. Do NOT fill tables, lists, or cards with repeated shell copy, navigation labels, or legal/footer text.
26. Menus, dropdowns, and popovers must be visually anchored near the trigger that opened them. Do NOT place them as detached floating cards far from the related row/button.
27. Table screens must keep one shared column model across header and body rows. Do NOT duplicate toolbar controls inside data rows.
28. Option cards, role cards, and selection cards must have enough height and text width for both title and description without clipping.


SAMPLE IMAGE REFERENCE:
----------------------
1. If user attached the existing website image or figma design for reference mean analyze those image and extract the colours from those images then based on that generate the new requirement figma design.

Example:
  Nav Bar color
  Icons
  logo(if user mentioned as a logo png or any formate for logo fitting)
  footer
  Button size
  Button colour
  Theams 
  Background colour
  Image generation


FRAME COORDINATE SYSTEM:
-----------------------
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
- Major page sections should align to one shared centered content container.
- If a page uses a card grid, the grid width, pagination width, and footer content width should follow the same container edges.
- Avoid left-heavy layouts with a large unused empty block on the right side of the page.

All elements must stay inside the frame width.
Do NOT shrink a full app screen into a tiny centered layout.
If the screenshot includes empty space around the UI, ignore that padding and recreate the actual site/app surface at full size.
Never reproduce screenshot chrome, capture background, editor canvas, or outer framing that sits outside the real product UI.

VERTICAL SPACING SYSTEM:
-----------------------

Use a consistent spacing rhythm:

Small spacing: 16
Medium spacing: 32
Large spacing: 64
Section spacing: 48-80

Sections should follow a tighter visual rhythm and should not be separated by oversized dead space.
The first body section must sit directly below the navbar with no visible gap.
If the navbar height is 80-90px, the next main section should typically begin around y ≈ 88-104, not 140-180.
The final content section before a footer must leave clear breathing room above the footer and must not visually collide with it.
Reserve roughly 48-96px of bottom spacing between the last major content block and the footer area.

Avoid cramped layouts.

ELEMENT TYPES:
--------------

FRAME — the primary layout container for generated UI blocks:
Use `frame` for navbars, cards, forms, lists, rows, columns, panels, filters, sections, settings blocks, and any structure that should behave like CSS flexbox.
{
  "type": "frame",
  "name": "Profile Card",
  "x": 120, "y": 200,
  "width": 360, "height": 220,
  "backgroundColor": "#FFFFFF",
  "cornerRadius": 16,
  "layoutMode": "VERTICAL",
  "itemSpacing": 12,
  "paddingLeft": 16,
  "paddingRight": 16,
  "paddingTop": 16,
  "paddingBottom": 16,
  "primaryAxisAlignItems": "MIN",
  "counterAxisAlignItems": "MIN",
  "primaryAxisSizingMode": "AUTO",
  "counterAxisSizingMode": "AUTO",
  "children": [
    { "type": "text", "name": "Card Title", "x": 0, "y": 0, "text": "Profile Card", "fontSize": 16, "fontWeight": "bold", "color": "#111111" },
    { "type": "text", "name": "Card Copy", "x": 0, "y": 0, "width": 280, "text": "This card grows automatically when content changes.", "fontSize": 14, "color": "#5B6270" },
    { "type": "button", "name": "Primary Action", "x": 0, "y": 0, "width": 132, "height": 44, "text": "View Profile", "backgroundColor": "#4F46E5", "textColor": "#FFFFFF", "cornerRadius": 10, "fontSize": 14, "fontWeight": "medium" }
  ]
}

RECTANGLE — decorative/background-only shapes, dividers, overlays:
Never use `rectangle` as the main layout container for cards, navbars, forms, lists, sections, or button groups.
{
  "type": "rectangle",
  "name": "Section Divider",
  "x": 120, "y": 200,
  "width": 1200, "height": 1,
  "backgroundColor": "#D1D5DB",
  "opacity": 0.3
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
Buttons are leaf UI components. Their internal Auto Layout is handled by the plugin renderer.
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

GROUP — only for tightly coupled visual/icon clusters when a real layout container is not needed:
Do not use `group` for cards, navbars, forms, lists, rows, columns, or reusable layout blocks. Use `frame` or `component` instead.
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
Never create label-specific reusable component identities such as "Login Button", "Sign In Button", or "Logout Button".
Use one stable reusable base such as "Actions/Button" with componentKey "actions/button", then change only the visible text via the `text` field.
Use flat variant metadata like `componentVariant`, `componentState`, and `componentSize` to express style/state differences such as filled vs outline, default vs hover vs active, or small vs medium vs large.
Apply the same rule to nav items: reuse "Navigation/Nav Item" instead of creating one reusable component per label.
If multiple elements are intentionally sharing the same color token, give them the same `styleGroupName`.
Example: a blue CTA fill, a blue icon, and a blue section label that should stay linked can all use `styleGroupName: "Accent"`.
Use `styleGroupFamily` for broader buckets such as "Brand", "Text", "Surface", "Border", "Success", or "Danger" when helpful.
If multiple text layers intentionally share the same typography, give them the same `textStyleName`.
Example: all page titles can use `textStyleName: "Heading XL"`, section labels can use `textStyleName: "Label / Medium"`, and body copy can use `textStyleName: "Body / Regular"`.
Use `textRole` for semantic hints such as "heading", "subheading", "body", "caption", "label", or "button-label".

AUTO LAYOUT REQUIREMENTS
------------------------

Generated UI must use Auto Layout-style structure wherever appropriate.

- Use `frame` or `component` as the container for all real UI components.
- Prefer a clear layout hierarchy for app/product screens:
  top-level app shell -> horizontal Auto Layout with sidebar + main content
  main content -> vertical Auto Layout with topbar + content sections
  content sections -> vertical Auto Layout for cards/tables/chat/forms/lists
  rows/navbars/toolbars -> horizontal Auto Layout
- For full-page website screens, prefer:
  root frame -> navbar + main content wrapper + optional footer
  main content wrapper -> vertical Auto Layout with non-zero section spacing
- Do not place all page sections directly in the root frame with `itemSpacing: 0`.
- The main content wrapper should preserve visible left/right page margins instead of stretching every section edge-to-edge.
- Always include a real main content wrapper/container on full-page website screens, even if there is only one major section.
- Do not omit the main content container between the navbar and footer.
- Use `layoutMode: "HORIZONTAL"` for rows:
  navbars, tab rows, button rows, filter bars, segmented controls, list rows with left/right content
- Use `layoutMode: "VERTICAL"` for stacked layouts:
  cards, forms, lists, sidebars, modals, settings sections, content columns
- Add `itemSpacing` and padding on every layout container.
- Prefer nested Auto Layout:
  example: card frame -> vertical, action row frame -> horizontal, button inside action row
- Use explicit sizing for compact UI and renderer-sensitive elements:
  icons, badges, pills, avatar chips, compact buttons, input controls, and dense status wrappers -> explicit `width` and `height`
- Use `layoutSizingHorizontal: "HUG"` and `layoutSizingVertical: "HUG"` only when the content is not a critical compact control and the parent layout does not depend on fragile text measurement.
- Use `layoutSizingHorizontal: "FILL"` for flexible children that should expand inside parent rows/columns.
- If a container relies on padding plus text size to create its final shape, prefer a fixed-size wrapper instead of a pure HUG container.
- Use `layoutGrow: 1` on children that should fill the remaining horizontal/vertical space within an Auto Layout parent.
- Use `primaryAxisAlignItems: "SPACE_BETWEEN"` for navbars and rows where left and right content should stay aligned at opposite edges.
- Children inside Auto Layout containers should usually have `x: 0` and `y: 0`.
- Remove manual positioning inside Auto Layout containers. Use `itemSpacing`, padding, nested frames, and `SPACE_BETWEEN` instead of hand-placing children.
- Do not mix absolute positioning with Auto Layout for normal UI layouts.
- Add `minWidth` and `minHeight` to important containers such as cards, panels, tables, chat sections, and sidebars to prevent collapsing.
- Large content sections that contain card rows, tabbed content, recommendation rows, or long text blocks must reserve enough min-height so children never spill outside the parent.
- Wrap small UI elements such as badges, pills, and compact buttons inside their own Auto Layout frames instead of placing raw text directly in larger rows.
- Do not build cards/forms/lists/navbars from loose rectangles and loose text when a frame/container is appropriate.
- Think in CSS flexbox terms: parent frame controls stack/row behavior; child frames control nested sections.
- Do not generate blank surfaces. Every product screen must contain meaningful main content beyond a navbar or header.
- Avoid chrome-only outputs. Do not generate standalone pages that are only a notifications tray, tiny dropdown, empty drawer, or empty placeholder panel unless explicitly requested.
- Buttons must be sized consistently from their label length and padding. Avoid random button widths or tiny CTA labels inside oversized containers.
- Every product/app screen must contain at least one substantial content block such as a form, table, card grid, detail panel, chat panel, dashboard stats section, or settings section.

STRUCTURE REQUIREMENTS
-------------

Generate the structure that matches the planned frame.

- If the frame is a landing page or marketing website, use the appropriate website sections.
- If the frame is a dashboard / CRM / inbox / product screen, generate a full-fidelity application layout instead.
- Follow the frame description exactly instead of defaulting to a landing page.
- Reuse one consistent content width and left/right alignment across headings, filters, grids, pagination, and footer rows within the same page.
- Product detail pages should use the available page width efficiently:
  main media and details should form a balanced two-column layout with no huge dead zone on the right.
  recommendation rows and supporting sections should align to the same content width and leave footer-safe spacing below.

NAVBAR DESIGN
-------------
Height: 80-90px

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

HERO SECTION
------------

Starts immediately below the navbar or top bar.
Do not leave a detached gap between navbar and hero/main body frame.
For full-page websites with an 80-90px navbar, the hero should usually start around y ≈ 88-104.

Layout:
Left column:
Headline
Subtext
CTA buttons

Right column:
Large hero image

Hero headline size:
fontSize 72-96
fontWeight "bold"
lineHeight 1.0-1.15

Subtext:
fontSize 18-20
color slightly muted

CARD DESIGN
-----------

Cards must include:

frame/container
title text
description text
optional icon or image

Card properties:
cornerRadius: 16-20
backgroundColor slightly lighter than page background

Use grid layouts for cards:
2 or 3 columns
- For wider shop/catalog layouts, use 4 columns only when the row still fills the available content width cleanly.

Card spacing and sizing rules:
- Card content must fit completely inside the card with no overlap or clipping.
- If a card includes a bottom CTA such as "Add to Cart", "Learn More", "Book Now", or "Subscribe", reserve clear space for that button inside the card.
- Do not use a fixed card height that is too short for image + title + price + description + bottom CTA.
- Prefer content-driven height for vertical cards with bottom actions.
- Grid rows must respect the tallest card in the row and must never overlap the next row.
- Keep internal card spacing consistent: title -> metadata/price -> description -> CTA.
- Card grids should visually fill the intended content width; do not leave a large unused gap at the row end.
- Align filter chips, grid rows, pagination, and footer/meta rows to the same container width.
- Keep pagination clearly separated from the grid and place it within the same horizontal bounds as the grid.
- Recommendation/similar-item rows near the bottom of a page must have enough container height and bottom spacing so they do not overlap the footer.

Cards, forms, lists, navbars, sidebars, and content sections should be represented as `frame` or `component` containers with Auto Layout metadata, not as a loose rectangle plus floating children.

TABLE LAYOUT
------------
For tables and data rows:

- Build each row as a `frame` with `layoutMode: "HORIZONTAL"`.
- Use the SAME column structure in the header row and every body row.
- Keep column widths consistent across all rows.
- Use fixed-width columns for short structured fields such as:
  role, status, plan, amount, priority, action buttons
- Use fill columns for flexible fields such as:
  timestamps, names, messages, descriptions, notes
- Align row items vertically centered with `counterAxisAlignItems: "CENTER"`.
- Wrap status pills/badges/chips inside their own Auto Layout `frame`.
- Do NOT use `HUG` sizing for columns that need cross-row alignment consistency.
- Never use footer/legal text inside rows. Bad example row values:
  "© 2024 Bot Builder SaaS. All rights reserved."
  "Terms of Service"
  "Privacy Policy"
- Good user-row values:
  "Olivia Chen", "olivia@acmecorp.com", "Admin", "Active", "2 hours ago"
- Good bot-row values:
  "Customer Onboarding Assistant", "Active", "Production", "2 hours ago"
- Actions/menus column should stay compact and fixed-width.
- Search/filter/add buttons belong in the toolbar/header area, not inside body rows.

Example row pattern:
{
  "type": "frame",
  "name": "Table Row",
  "x": 0, "y": 0,
  "width": 960, "height": 56,
  "backgroundColor": "#FFFFFF",
  "layoutMode": "HORIZONTAL",
  "itemSpacing": 16,
  "paddingLeft": 16,
  "paddingRight": 16,
  "paddingTop": 12,
  "paddingBottom": 12,
  "primaryAxisAlignItems": "MIN",
  "counterAxisAlignItems": "CENTER",
  "primaryAxisSizingMode": "FIXED",
  "counterAxisSizingMode": "AUTO",
  "children": [
    { "type": "text", "name": "Name Cell", "x": 0, "y": 0, "text": "Ava Johnson", "fontSize": 14, "fontWeight": "medium", "color": "#111111", "layoutSizingHorizontal": "FIXED", "layoutSizingVertical": "HUG", "width": 220 },
    { "type": "text", "name": "Role Cell", "x": 0, "y": 0, "text": "Admin", "fontSize": 14, "color": "#374151", "layoutSizingHorizontal": "FIXED", "layoutSizingVertical": "HUG", "width": 120 },
    { "type": "frame", "name": "Status Badge", "x": 0, "y": 0, "layoutMode": "HORIZONTAL", "itemSpacing": 6, "paddingLeft": 10, "paddingRight": 10, "paddingTop": 6, "paddingBottom": 6, "cornerRadius": 999, "backgroundColor": "#EEF2FF", "primaryAxisSizingMode": "AUTO", "counterAxisSizingMode": "AUTO", "layoutSizingHorizontal": "FIXED", "layoutSizingVertical": "FIXED", "width": 92, "height": 30, "children": [
      { "type": "text", "name": "Status Label", "x": 0, "y": 0, "text": "Active", "fontSize": 12, "fontWeight": "medium", "color": "#3156D3", "layoutSizingHorizontal": "HUG", "layoutSizingVertical": "HUG" }
    ] },
    { "type": "text", "name": "Timestamp Cell", "x": 0, "y": 0, "text": "2 minutes ago", "fontSize": 13, "color": "#6B7280", "layoutSizingHorizontal": "FILL", "layoutSizingVertical": "HUG", "layoutGrow": 1 }
  ]
}

TYPOGRAPHY HIERARCHY
--------------------

Hero headline:
fontSize 72-96
fontWeight "bold"

Section heading:
fontSize 44-56
fontWeight "bold"

Subheading:
fontSize 24-30
fontWeight "semibold"

Body text:
fontSize 16-18
color muted

Labels / captions:
fontSize 12-14
fontWeight "bold"
letterSpacing 2

IMAGE RULES
Use domain-appropriate imagery only when the design actually calls for real content media.
Do not invent random or irrelevant images.
Examples:
  - Farm website -> farm/agriculture imagery
  - SaaS marketing site -> product/office/team/device imagery
  - CRM or dashboard app -> use screenshots/illustrations only when the UI clearly includes them
        
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

FOOTER
-------

Near the bottom of the frame include:

Divider line
Company name
Navigation links
Social links

Divider example:
rectangle height = 1
opacity ≈ 0.2

DESIGN QUALITY REQUIREMENTS:
---------------------------
- Use the provided theme colors throughout: background, surfaces, accent, text
- Preserve readability over palette purity: if a theme color combination is too low-contrast, adjust text/button foregrounds to a more readable color from the same visual family.
- Respect the screenshot/reference proportions and overall scale
- Ignore screenshot capture padding or blank margins around the real UI
- Recreate the actual UI surface so it fills the frame naturally
- For product screens, use realistic outer gutters (roughly 24-48px), not huge empty margins
- Never compress a product screen into a tiny centered website
- Treat the project like one connected product system, not a set of unrelated screens
- Keep shared navigation destinations, ordering, and shell structure consistent across related frames
- If a modal/menu/drawer/popover is open, preserve the same underlying base screen shell from the parent state
- Typography hierarchy:
    Hero heading:  fontSize 72-96, fontWeight "bold", lineHeight 1.0-1.15
    Section title: fontSize 42-56, fontWeight "bold"
    Sub-heading:   fontSize 24-32, fontWeight "semibold"
    Body text:     fontSize 16-20, color slightly muted (e.g. #A0A0A0)
    Labels/caps:   fontSize 12-14, fontWeight "bold", letterSpacing 2
- Layout:
    Navbar:       y=0, height=80-90px. Logo at x=120 y≈28. Nav links right side. CTA button far right.
    Hero:         Starts at y≈150. Big heading left, hero image right.
    Sections:     120px padding top/bottom. Section label (caps) then big heading then content.
    Cards:        backgroundColor slightly lighter than page bg. cornerRadius 12-20.
    Footer:       Near bottom. Thin divider line (rectangle h=1), copyright left, social links right.
- Prefer Auto Layout containers for structural UI:
    Navbar shell:  `component` or `frame` with `layoutMode: "HORIZONTAL"`
    Card shell:    `frame` with `layoutMode: "VERTICAL"`
    Form shell:    `frame` with `layoutMode: "VERTICAL"` and nested field rows
    List shell:    `frame` with `layoutMode: "VERTICAL"` and nested list-item rows
    Action rows:   `frame` with `layoutMode: "HORIZONTAL"`
- All coordinates MUST be inside the frame (0 to frame width, 0 to frame height)
- Include REALISTIC content for the project domain — real names, descriptions, copy
- Make it look like a professional real website, not a wireframe
- CONTRAST RULES:
    - Headings, body text, prices, labels, nav items, and button text must remain clearly readable against their local background.
    - Do not use pale text on pale surfaces or dark text on dark surfaces.
    - When in doubt, prefer stronger contrast for text and CTAs over strict theme matching.
    - Primary buttons must have obvious label readability against the button fill.
    - Secondary/outline buttons must have readable labels against the page or card background behind them.
- TEXT FIT RULES:
    - Do not create text boxes so narrow that ordinary labels or sentences break awkwardly
    - Expand text widths or surrounding containers so text does not collide with nearby elements
    - Short labels, tabs, buttons, chips, table cells, and names should stay on one line whenever reasonably possible
    - If a paragraph wraps, ensure its container height and the spacing below it prevent overlap
    - Never let text overflow outside its parent card, panel, section, or page bounds
    - If a heading or product name is long, increase the text box width or allow clean multi-line wrapping inside the container
    - Labels inside narrow sidebars, cards, and summary columns must remain readable and stay within the container width
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
    for item in pages[:5]:
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


def _trim_prompt_value(value: str, limit: int = CODING_LAYOUT_TEXT_LIMIT) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit else value[:limit].rstrip() + " ..."


def _compact_layout_context(layout_context: dict | None) -> dict:
    if not isinstance(layout_context, dict):
        return {}
    return {
        "layout_type": layout_context.get("layout_type", ""),
        "screen_type": layout_context.get("screen_type", ""),
        "visual_style": _trim_prompt_value(layout_context.get("visual_style", "")),
        "color_palette": _trim_prompt_value(layout_context.get("color_palette", "")),
        "detected_sections": list((layout_context.get("detected_sections") or [])[:8]),
        "detected_components": list((layout_context.get("detected_components") or [])[:10]),
        "outer_padding_present": bool(layout_context.get("outer_padding_present")),
        "viewport_fill_guidance": _trim_prompt_value(layout_context.get("viewport_fill_guidance", ""), 180),
    }


def _infer_canvas_mode(page: dict, layout_context: dict | None) -> str:
    layout_context = layout_context or {}
    layout_type = str(layout_context.get("layout_type", "") or "").lower()
    screen_type = str(layout_context.get("screen_type", "") or "").lower()
    names = " ".join([
        str(page.get("name", "") or ""),
        str(page.get("screen_title", "") or ""),
        str(page.get("description", "") or ""),
        str(page.get("feature_group", "") or ""),
        str(page.get("ui_state", "") or ""),
    ]).lower()

    overlay_tokens = ["modal", "dialog", "drawer", "popover", "popup", "sheet", "tooltip"]
    document_tokens = [
        "landing", "home", "pricing", "about", "article", "blog", "documentation", "docs",
        "knowledge", "help", "support", "catalog", "category", "product detail", "checkout",
        "cart", "search", "report", "form", "profile", "portfolio", "marketing", "storefront"
    ]
    app_tokens = [
        "dashboard", "workspace", "crm", "portal", "console", "admin", "settings", "table",
        "inbox", "editor", "builder", "analytics", "management", "kanban", "chat", "app screen"
    ]

    if any(token in screen_type for token in overlay_tokens) or any(token in names for token in overlay_tokens):
        return "overlay"
    if any(token in layout_type for token in app_tokens) or any(token in names for token in app_tokens):
        return "app"
    if any(token in screen_type for token in ["document_page", "long_scroll_page", "slide", "sheet", "report"]):
        return "document"
    if any(token in layout_type for token in document_tokens) or any(token in names for token in document_tokens):
        return "document"
    if page.get("feature_group") or page.get("ui_state"):
        return "app"
    return "page"


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
        lines.append(_json_for_prompt(attachment.get("primary_tree"), CODING_ATTACHMENT_PRIMARY_LIMIT))
    if attachment.get("context_trees"):
        lines.append("\nCONTEXT PAGE TREES:")
        lines.append(_json_for_prompt(attachment.get("context_trees"), CODING_ATTACHMENT_CONTEXT_LIMIT))
    if attachment.get("component_trees"):
        lines.append("\nREUSABLE COMPONENT TREES:")
        lines.append(_json_for_prompt(attachment.get("component_trees"), CODING_ATTACHMENT_COMPONENT_LIMIT))

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


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    color = _normalized_color(value)
    if not color or color == "transparent" or not color.startswith("#") or len(color) != 7:
        return None
    try:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    except Exception:
        return None


def _relative_luminance(value: str) -> float | None:
    rgb = _hex_to_rgb(value)
    if not rgb:
        return None

    def channel(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(ch) for ch in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: str, bg: str) -> float:
    fg_l = _relative_luminance(fg)
    bg_l = _relative_luminance(bg)
    if fg_l is None or bg_l is None:
        return 999.0
    lighter = max(fg_l, bg_l)
    darker = min(fg_l, bg_l)
    return (lighter + 0.05) / (darker + 0.05)


def _best_foreground_for_background(bg: str) -> str:
    candidates = ["#111827", "#0F172A", "#FFFFFF", "#F8FAFC"]
    best = "#111827"
    best_ratio = -1.0
    for candidate in candidates:
        ratio = _contrast_ratio(candidate, bg)
        if ratio > best_ratio:
            best = candidate
            best_ratio = ratio
    return best


def _ensure_text_contrast(color: str, background: str, minimum: float = 4.2) -> str:
    fg = _normalized_color(color)
    bg = _normalized_color(background)
    if not fg:
        fg = "#111827"
    if not bg or bg == "transparent":
        return fg
    if _contrast_ratio(fg, bg) >= minimum:
        return fg
    return _best_foreground_for_background(bg)


def _estimate_text_box(text_value: str, font_size: int) -> tuple[int, int]:
    clean = str(text_value or "").strip()
    size = max(10, int(font_size or 16))
    width = max(24, int(len(clean) * max(7, size * 0.55)))
    height = max(18, int(size * 1.4))
    return width, height


def _estimate_button_width(text_value: str, font_size: int) -> int:
    clean = str(text_value or "").strip()
    size = max(12, int(font_size or 16))
    return max(88, min(320, int(len(clean) * max(7, size * 0.58)) + 40))


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


def _component_state_variant(node: dict) -> str:
    blob = " ".join([
        str(node.get("name", "") or ""),
        str(node.get("componentState", "") or ""),
        str(node.get("state", "") or ""),
    ]).lower()
    for token, state in [
        ("hover", "hover"),
        ("active", "active"),
        ("pressed", "active"),
        ("focus", "focus"),
        ("disabled", "disabled"),
        ("selected", "selected"),
        ("current", "selected"),
    ]:
        if token in blob:
            return state
    return "default"


def _component_size_variant(node: dict, role: str) -> str:
    width = int(node.get("width", 0) or 0)
    height = int(node.get("height", 0) or 0)

    if role == "button":
        if height >= 56 or width >= 220:
            return "lg"
        if height <= 36 or width <= 108:
            return "sm"
        return "md"

    if role == "nav-item":
        if height >= 44 or width >= 160:
            return "lg"
        if height and height <= 28:
            return "sm"
        return "md"

    if height >= 320 or width >= 640:
        return "lg"
    if height and height <= 48:
        return "sm"
    return "md"


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

    if role == "global-nav":
        return "shared"

    if role == "secondary-nav":
        return "shared"

    if role == "sidebar":
        return "shared"

    variant_parts = []

    if not variant_parts or not variant_parts[0]:
        variant_parts = [f"{width}x{height}" if width and height else "default"]

    if color and color not in {"", "transparent"}:
        variant_parts.append(color.replace("#", ""))
    return _slugify_component("-".join(part for part in variant_parts if part))


def _apply_component_identity(node: dict, role: str) -> dict:
    updated = dict(node)

    if role == "button":
        updated["componentName"] = "Actions/Button"
        updated["componentKey"] = "actions/button"
        updated["componentVariant"] = _button_style_variant(updated)
        updated["componentState"] = _component_state_variant(updated)
        updated["componentSize"] = _component_size_variant(updated, "button")
        updated["name"] = updated.get("name") or "Button"
        return updated

    if role == "nav-item":
        label = _primary_label(updated) or updated.get("name") or "Nav Item"
        updated["componentName"] = "Navigation/Nav Item"
        updated["componentKey"] = "navigation/nav-item"
        updated["componentState"] = _component_state_variant(updated)
        updated["componentSize"] = _component_size_variant(updated, "nav-item")
        updated["name"] = updated.get("name") or f"Nav Item {label}"
        return updated

    component_meta = {
        "global-nav": ("Navigation/Global Top Bar", "navigation/global-top-bar"),
        "secondary-nav": ("Navigation/Secondary Nav", "navigation/secondary-nav"),
        "sidebar": ("Navigation/Sidebar", "navigation/sidebar"),
        "side-panel": ("Layout/Side Panel", "layout/side-panel"),
    }.get(role)
    if component_meta:
        variant = _component_variant_signature(updated, role)
        comp_name, comp_key = component_meta
        updated["componentName"] = comp_name
        updated["componentKey"] = comp_key if role in {"global-nav", "secondary-nav", "sidebar"} else f"{comp_key}/{variant}"
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
        "componentName": "Navigation/Nav Item",
        "componentKey": "navigation/nav-item",
        "componentState": _component_state_variant(node),
        "componentSize": _component_size_variant(node, "nav-item"),
        "x": int(node.get("x", 0) or 0),
        "y": int(node.get("y", 0) or 0),
        "width": max(1, width),
        "height": max(1, height),
        "backgroundColor": "transparent",
        "children": [child],
    }
    if parent_role == "sidebar":
        wrapped["componentName"] = "Navigation/Sidebar Item"
        wrapped["componentKey"] = "navigation/sidebar-item"
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
            approx_width = _estimate_button_width(text_value, font_size)
            if text_value.strip():
                updated["width"] = min(max(width or 0, approx_width), 360)
            updated["height"] = max(40, int(updated.get("height", 44) or 44))
            updated["textAlign"] = "CENTER"

        stabilized.append(updated)
    return stabilized


def _node_name_blob(node: dict) -> str:
    return " ".join([
        str(node.get("name", "") or ""),
        str(node.get("text", "") or ""),
        str(node.get("componentName", "") or ""),
    ]).lower()


def _find_primary_text(node: dict) -> dict | None:
    if not isinstance(node, dict):
        return None
    if str(node.get("type", "")).lower() == "text" and str(node.get("text", "") or "").strip():
        return node
    for child in node.get("children", []) or []:
        found = _find_primary_text(child)
        if found:
            return found
    return None


def _is_status_badge(node: dict, parent_blob: str = "") -> bool:
    if not isinstance(node, dict):
        return False
    blob = _node_name_blob(node)
    text = _find_primary_text(node)
    text_value = str((text or {}).get("text", "") or "").strip().lower()
    status_words = {"active", "inactive", "pending", "draft", "archived", "paused", "completed"}
    if "badge" in blob or "pill" in blob or "chip" in blob:
        return True
    if "status" in parent_blob and text_value in status_words:
        return True
    return False


def _is_status_cell(node: dict) -> bool:
    blob = _node_name_blob(node)
    return "status cell" in blob or blob.strip() == "status"


def _is_search_control(node: dict) -> bool:
    blob = _node_name_blob(node)
    return any(token in blob for token in ["search box", "search input", "search field", "searchbar", "search bar"])


def _is_menu_item(node: dict, parent_blob: str = "") -> bool:
    blob = _node_name_blob(node)
    return "menu item" in blob or ("dropdown" in parent_blob and str(node.get("type", "")).lower() in {"frame", "component"})


def _normalize_text_box(node: dict, pad_x: int = 0, pad_y: int = 0) -> dict:
    updated = dict(node)
    text_value = str(updated.get("text", "") or "").strip()
    font_size = int(updated.get("fontSize", 14) or 14)
    est_w, est_h = _estimate_text_box(text_value, font_size)
    updated["width"] = max(int(updated.get("width", 0) or 0), est_w + pad_x)
    updated["height"] = max(int(updated.get("height", 0) or 0), est_h + pad_y)
    updated["textAlignHorizontal"] = updated.get("textAlignHorizontal") or "CENTER"
    updated["textAlignVertical"] = updated.get("textAlignVertical") or "CENTER"
    updated["layoutSizingHorizontal"] = "FIXED"
    updated["layoutSizingVertical"] = "FIXED"
    return updated


def _normalize_left_text_box(node: dict, pad_x: int = 0, pad_y: int = 0, min_height: int = 0) -> dict:
    updated = _normalize_text_box(node, pad_x, pad_y)
    updated["textAlignHorizontal"] = "LEFT"
    updated["textAlignVertical"] = updated.get("textAlignVertical") or "CENTER"
    if min_height > 0:
        updated["height"] = max(int(updated.get("height", 0) or 0), min_height)
    return updated


def _normalize_status_badge(node: dict) -> dict:
    updated = dict(node)
    text_node = _find_primary_text(updated)
    label = str((text_node or {}).get("text", "") or "Status").strip()
    font_size = int((text_node or {}).get("fontSize", 12) or 12)
    est_text_w, est_text_h = _estimate_text_box(label, font_size)
    badge_height = max(24, min(28, est_text_h + 10))
    badge_width = max(64, min(120, est_text_w + 28))

    children = updated.get("children", []) or []
    normalized_children = []
    for child in children:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        if child is text_node or _find_primary_text(child) is text_node:
            if str(child_updated.get("type", "")).lower() == "text":
                child_updated = _normalize_text_box(child_updated, 0, 0)
            else:
                child_updated["width"] = max(int(child_updated.get("width", 0) or 0), est_text_w)
                child_updated["height"] = max(int(child_updated.get("height", 0) or 0), est_text_h)
                child_updated["layoutSizingHorizontal"] = "FIXED"
                child_updated["layoutSizingVertical"] = "FIXED"
                if isinstance(child_updated.get("children"), list):
                    child_updated["children"] = [
                        _normalize_text_box(dict(grandchild), 0, 0) if isinstance(grandchild, dict) and str(grandchild.get("type", "")).lower() == "text" else grandchild
                        for grandchild in child_updated["children"]
                    ]
            normalized_children.append(child_updated)
        else:
            normalized_children.append(child_updated)

    updated["children"] = normalized_children
    updated["layoutMode"] = "HORIZONTAL"
    updated["primaryAxisAlignItems"] = "CENTER"
    updated["counterAxisAlignItems"] = "CENTER"
    updated["layoutSizingHorizontal"] = "FIXED"
    updated["layoutSizingVertical"] = "FIXED"
    updated["width"] = max(int(updated.get("width", 0) or 0), badge_width)
    updated["height"] = max(int(updated.get("height", 0) or 0), badge_height)
    updated["paddingLeft"] = 0
    updated["paddingRight"] = 0
    updated["paddingTop"] = 0
    updated["paddingBottom"] = 0
    updated["itemSpacing"] = 0
    updated["cornerRadius"] = max(int(updated.get("cornerRadius", 999) or 999), 999)
    return updated


def _normalize_status_cell(node: dict) -> dict:
    updated = dict(node)
    updated["layoutMode"] = "HORIZONTAL"
    updated["primaryAxisAlignItems"] = "MIN"
    updated["counterAxisAlignItems"] = "CENTER"
    updated["layoutSizingHorizontal"] = "FIXED"
    updated["layoutSizingVertical"] = "FIXED" if int(updated.get("height", 0) or 0) > 0 else updated.get("layoutSizingVertical", "HUG")
    updated["width"] = max(int(updated.get("width", 0) or 0), 120)
    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        if _is_status_badge(child_updated, _node_name_blob(updated)):
            child_updated = _normalize_status_badge(child_updated)
        children.append(child_updated)
    updated["children"] = children
    return updated


def _normalize_search_control(node: dict) -> dict:
    updated = dict(node)
    updated["layoutMode"] = "HORIZONTAL"
    updated["primaryAxisAlignItems"] = updated.get("primaryAxisAlignItems") or "MIN"
    updated["counterAxisAlignItems"] = "CENTER"
    updated["layoutSizingHorizontal"] = "FIXED"
    updated["layoutSizingVertical"] = "FIXED"
    updated["width"] = max(220, min(int(updated.get("width", 0) or 260), 320))
    updated["height"] = max(44, min(int(updated.get("height", 0) or 44), 48))
    updated["paddingLeft"] = max(14, int(updated.get("paddingLeft", 0) or 0))
    updated["paddingRight"] = max(14, int(updated.get("paddingRight", 0) or 0))
    updated["paddingTop"] = 0
    updated["paddingBottom"] = 0
    updated["itemSpacing"] = max(8, int(updated.get("itemSpacing", 0) or 8))
    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        child_blob = _node_name_blob(child_updated)
        if "icon" in child_blob or "search" in child_blob and str(child_updated.get("type", "")).lower() != "text":
            child_updated["width"] = max(16, min(int(child_updated.get("width", 0) or 16), 20))
            child_updated["height"] = max(16, min(int(child_updated.get("height", 0) or 16), 20))
            child_updated["layoutSizingHorizontal"] = "FIXED"
            child_updated["layoutSizingVertical"] = "FIXED"
        primary_text = _find_primary_text(child_updated)
        if primary_text:
            if str(child_updated.get("type", "")).lower() == "text":
                child_updated = _normalize_text_box(child_updated, 8, 0)
                child_updated["textAlignHorizontal"] = "LEFT"
            elif isinstance(child_updated.get("children"), list):
                new_grandchildren = []
                for grandchild in child_updated["children"]:
                    if isinstance(grandchild, dict) and str(grandchild.get("type", "")).lower() == "text":
                        gc = _normalize_text_box(dict(grandchild), 8, 0)
                        gc["textAlignHorizontal"] = "LEFT"
                        new_grandchildren.append(gc)
                    else:
                        new_grandchildren.append(grandchild)
                child_updated["children"] = new_grandchildren
        children.append(child_updated)
    updated["children"] = children
    return updated


def _normalize_menu_item(node: dict) -> dict:
    updated = dict(node)
    updated["layoutMode"] = "HORIZONTAL"
    updated["primaryAxisAlignItems"] = "MIN"
    updated["counterAxisAlignItems"] = "CENTER"
    updated["layoutSizingHorizontal"] = updated.get("layoutSizingHorizontal") or "FILL"
    updated["layoutSizingVertical"] = "FIXED"
    updated["height"] = max(40, min(int(updated.get("height", 0) or 40), 44))
    updated["paddingTop"] = 0
    updated["paddingBottom"] = 0
    updated["paddingLeft"] = max(12, int(updated.get("paddingLeft", 0) or 0))
    updated["paddingRight"] = max(12, int(updated.get("paddingRight", 0) or 0))
    updated["itemSpacing"] = max(8, int(updated.get("itemSpacing", 0) or 8))
    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        if str(child_updated.get("type", "")).lower() == "text":
            child_updated = _normalize_left_text_box(child_updated, 8, 0, min_height=16)
        children.append(child_updated)
    updated["children"] = children
    return updated


def _is_option_card(node: dict) -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("type", "")).lower() not in {"frame", "component"}:
        return False
    blob = _node_name_blob(node)
    if not any(token in blob for token in ["role", "option", "selection", "radio card", "plan card"]):
        return False
    return len(node.get("children", []) or []) >= 2


def _normalize_option_card(node: dict) -> dict:
    updated = dict(node)
    updated["layoutMode"] = updated.get("layoutMode") or "HORIZONTAL"
    updated["counterAxisAlignItems"] = "CENTER"
    updated["itemSpacing"] = max(12, int(updated.get("itemSpacing", 0) or 12))
    updated["paddingLeft"] = max(12, int(updated.get("paddingLeft", 0) or 0))
    updated["paddingRight"] = max(12, int(updated.get("paddingRight", 0) or 0))
    updated["paddingTop"] = max(12, int(updated.get("paddingTop", 0) or 0))
    updated["paddingBottom"] = max(12, int(updated.get("paddingBottom", 0) or 0))
    updated["height"] = max(64, int(updated.get("height", 0) or 64))

    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        child_type = str(child_updated.get("type", "")).lower()
        if child_type == "text":
            child_updated = _normalize_left_text_box(child_updated, 4, 2)
        elif child_type in {"frame", "component"} and _find_primary_text(child_updated):
            nested_children = []
            for grandchild in child_updated.get("children", []) or []:
                if isinstance(grandchild, dict) and str(grandchild.get("type", "")).lower() == "text":
                    nested_children.append(_normalize_left_text_box(dict(grandchild), 4, 2))
                else:
                    nested_children.append(grandchild)
            child_updated["children"] = nested_children
        children.append(child_updated)
    updated["children"] = children
    return updated


def _is_action_menu_surface(node: dict, parent_blob: str = "") -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("type", "")).lower() not in {"frame", "component"}:
        return False
    blob = _node_name_blob(node)
    menu_like = any(token in blob for token in ["options menu", "action menu", "dropdown menu", "module dropdown", "popover menu"])
    parent_menu_like = any(token in parent_blob for token in ["dropdown", "menu", "options"])
    return menu_like or (parent_menu_like and len(node.get("children", []) or []) >= 3)


def _normalize_action_menu_surface(node: dict) -> dict:
    updated = dict(node)
    updated["layoutMode"] = "VERTICAL"
    updated["primaryAxisAlignItems"] = "MIN"
    updated["counterAxisAlignItems"] = "MIN"
    updated["itemSpacing"] = max(4, int(updated.get("itemSpacing", 0) or 4))
    updated["paddingLeft"] = max(10, int(updated.get("paddingLeft", 0) or 0))
    updated["paddingRight"] = max(10, int(updated.get("paddingRight", 0) or 0))
    updated["paddingTop"] = max(10, int(updated.get("paddingTop", 0) or 0))
    updated["paddingBottom"] = max(10, int(updated.get("paddingBottom", 0) or 0))
    updated["width"] = max(188, min(int(updated.get("width", 0) or 220), 280))
    updated["backgroundColor"] = updated.get("backgroundColor") or "#FFFFFF"
    updated["cornerRadius"] = max(10, int(updated.get("cornerRadius", 12) or 12))
    updated["layoutSizingHorizontal"] = "FIXED"

    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_updated = dict(child)
        if _is_menu_item(child_updated, _node_name_blob(updated)):
            child_updated = _normalize_menu_item(child_updated)
        children.append(child_updated)
    updated["children"] = children
    return updated


TABLE_HEADER_TOKENS = {"name", "email", "role", "status", "last login", "last updated", "actions", "bot name", "phase"}


def _is_obvious_table_row(node: dict) -> bool:
    return (
        isinstance(node, dict)
        and str(node.get("type", "")).lower() in {"frame", "component"}
        and str(node.get("layoutMode", "")).upper() == "HORIZONTAL"
        and len(node.get("children", []) or []) >= 3
    )


def _table_rows(node: dict) -> list[dict]:
    return [child for child in (node.get("children") or []) if _is_obvious_table_row(child)]


def _table_header_labels(row: dict) -> list[str]:
    labels = []
    for child in row.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        text = _find_primary_text(child) if str(child.get("type", "")).lower() != "text" else child
        value = str((text or {}).get("text", "") or "").strip().lower()
        if value:
            labels.append(value)
    return labels


def _is_strict_table_container(node: dict) -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("type", "")).lower() not in {"frame", "component"}:
        return False
    blob = _node_name_blob(node)
    if not any(token in blob for token in ["table", "users", "results", "dashboard", "list"]):
        return False
    rows = _table_rows(node)
    if len(rows) < 2:
        return False
    header_labels = _table_header_labels(rows[0])
    header_hits = sum(1 for label in header_labels if label in TABLE_HEADER_TOKENS)
    return header_hits >= 2


def _table_column_width(label: str) -> int | None:
    label = (label or "").lower()
    if "email" in label:
        return 240
    if "bot name" in label or label == "name":
        return 220
    if "role" in label:
        return 140
    if "status" in label:
        return 120
    if "last login" in label or "last updated" in label or "phase" in label:
        return 160
    if "action" in label:
        return 140
    return None


def _normalize_strict_table(node: dict) -> dict:
    updated = dict(node)
    rows = _table_rows(updated)
    if len(rows) < 2:
        return updated

    header_labels = _table_header_labels(rows[0])
    column_widths = {idx: _table_column_width(label) for idx, label in enumerate(header_labels)}
    updated["layoutMode"] = "VERTICAL"
    updated["itemSpacing"] = 0
    updated["paddingLeft"] = int(updated.get("paddingLeft", 0) or 0)
    updated["paddingRight"] = int(updated.get("paddingRight", 0) or 0)
    updated["paddingTop"] = int(updated.get("paddingTop", 0) or 0)
    updated["paddingBottom"] = int(updated.get("paddingBottom", 0) or 0)

    children = []
    for child in updated.get("children", []) or []:
        if not isinstance(child, dict) or not _is_obvious_table_row(child):
            children.append(child)
            continue

        row_updated = dict(child)
        row_updated["counterAxisAlignItems"] = "CENTER"
        row_updated["itemSpacing"] = max(12, int(row_updated.get("itemSpacing", 0) or 12))
        cells = []
        for idx, cell in enumerate(row_updated.get("children", []) or []):
            if not isinstance(cell, dict):
                cells.append(cell)
                continue
            cell_updated = dict(cell)
            target = column_widths.get(idx)
            if target:
                cell_updated["width"] = max(int(cell_updated.get("width", 0) or 0), target)
                cell_updated["minWidth"] = max(int(cell_updated.get("minWidth", 0) or 0), target)
                cell_updated["layoutSizingHorizontal"] = "FIXED"
            elif idx == 0:
                cell_updated["layoutSizingHorizontal"] = cell_updated.get("layoutSizingHorizontal") or "FILL"
                cell_updated["layoutGrow"] = max(int(cell_updated.get("layoutGrow", 0) or 0), 1)
            cells.append(cell_updated)
        row_updated["children"] = cells
        children.append(row_updated)

    updated["children"] = children
    return updated


def normalize_compact_controls(children: list, parent_blob: str = "") -> list:
    normalized = []
    for el in children or []:
        if not isinstance(el, dict):
            continue
        updated = dict(el)
        blob = _node_name_blob(updated)
        if isinstance(updated.get("children"), list):
            updated["children"] = normalize_compact_controls(updated["children"], blob)

        node_type = str(updated.get("type", "")).lower()
        if _is_status_cell(updated):
            updated = _normalize_status_cell(updated)
        elif node_type in {"frame", "component"} and _is_status_badge(updated, parent_blob):
            updated = _normalize_status_badge(updated)
        elif node_type in {"frame", "component"} and _is_search_control(updated):
            updated = _normalize_search_control(updated)
        elif node_type in {"frame", "component"} and _is_option_card(updated):
            updated = _normalize_option_card(updated)
        elif node_type in {"frame", "component"} and _is_action_menu_surface(updated, parent_blob):
            updated = _normalize_action_menu_surface(updated)
        elif node_type in {"frame", "component"} and _is_menu_item(updated, parent_blob):
            updated = _normalize_menu_item(updated)
        elif node_type in {"frame", "component"} and _is_strict_table_container(updated):
            updated = _normalize_strict_table(updated)

        normalized.append(updated)
    return normalized


def _looks_like_blank_shell(el: dict) -> bool:
    if not isinstance(el, dict):
        return False
    node_type = str(el.get("type", "")).lower()
    if node_type not in {"frame", "rectangle"}:
        return False
    width = int(el.get("width", 0) or 0)
    height = int(el.get("height", 0) or 0)
    if width < 900 or height < 420:
        return False
    if el.get("children"):
        return False
    bg = _normalized_color(el.get("backgroundColor"))
    return bg in {"#ffffff", "#f8fafc", "#f5f5f5", "#111111", "#121212", "#000000"}


def _sanitize_button_node(el: dict, parent_bg: str = "") -> dict:
    updated = dict(el)
    label = str(updated.get("text", "") or updated.get("name", "") or "Button").strip() or "Button"
    font_size = int(updated.get("fontSize", 16) or 16)
    est_width = _estimate_button_width(label, font_size)
    current_width = int(updated.get("width", 0) or 0)
    updated["text"] = label
    updated["width"] = min(max(current_width or 0, est_width), 320)
    updated["height"] = max(40, int(updated.get("height", 44) or 44))
    updated["minWidth"] = max(int(updated.get("minWidth", 0) or 0), est_width)
    updated["minHeight"] = max(int(updated.get("minHeight", 0) or 0), updated["height"])
    updated["textAlign"] = "CENTER"
    button_bg = _normalized_color(updated.get("backgroundColor"))
    contrast_bg = parent_bg if button_bg in {"", "transparent"} else button_bg
    updated["textColor"] = _ensure_text_contrast(updated.get("textColor") or "#111827", contrast_bg or "#FFFFFF", minimum=4.5)
    if updated.get("layoutMode") in {"HORIZONTAL", "VERTICAL"}:
        updated["primaryAxisAlignItems"] = updated.get("primaryAxisAlignItems") or "CENTER"
        updated["counterAxisAlignItems"] = updated.get("counterAxisAlignItems") or "CENTER"
    return updated


def _sanitize_text_node(el: dict, parent_bg: str = "") -> dict:
    updated = dict(el)
    text = str(updated.get("text", "") or "").strip()
    if not text:
        return updated
    updated["text"] = text
    has_width = int(updated.get("width", 0) or 0) > 0
    has_height = int(updated.get("height", 0) or 0) > 0
    if updated.get("layoutSizingHorizontal") is None:
        updated["layoutSizingHorizontal"] = "FIXED" if has_width else "HUG"
    if updated.get("layoutSizingVertical") is None:
        updated["layoutSizingVertical"] = "FIXED" if has_height else "HUG"
    font_size = int(updated.get("fontSize", 16) or 16)
    width = int(updated.get("width", 0) or 0)
    if width > 0:
        max_chars_single_line = max(8, int(width / max(6, font_size * 0.48)))
        if "\n" not in text and len(text) > max_chars_single_line:
            if font_size >= 28:
                updated["layoutSizingVertical"] = "HUG"
                updated["lineHeight"] = float(updated.get("lineHeight", 1.1) or 1.1)
            else:
                estimated_needed = min(920, max(width, int(len(text) * font_size * 0.62) + 16))
                updated["width"] = estimated_needed
    elif len(text) > 36 and font_size <= 20:
        updated["layoutSizingHorizontal"] = "FIXED"
        updated["width"] = min(720, max(240, int(len(text) * font_size * 0.56)))
    updated["color"] = _ensure_text_contrast(updated.get("color") or "#111827", parent_bg or "#FFFFFF", minimum=4.4)
    return updated


def _sanitize_container_node(el: dict, parent_bg: str = "") -> dict:
    updated = dict(el)
    local_bg = _normalized_color(updated.get("backgroundColor"))
    effective_bg = parent_bg
    if local_bg not in {"", "transparent"}:
        effective_bg = local_bg
    if isinstance(updated.get("children"), list):
        updated["children"] = sanitize_generated_children(updated["children"], effective_bg)
    if updated.get("layoutMode") in {"HORIZONTAL", "VERTICAL"}:
        updated["itemSpacing"] = max(0, int(updated.get("itemSpacing", 12) or 12))
        updated["paddingLeft"] = int(updated.get("paddingLeft", 0) or 0)
        updated["paddingRight"] = int(updated.get("paddingRight", 0) or 0)
        updated["paddingTop"] = int(updated.get("paddingTop", 0) or 0)
        updated["paddingBottom"] = int(updated.get("paddingBottom", 0) or 0)
    return updated


def _is_footer_legal_text(value: str) -> bool:
    lower = str(value or "").strip().lower()
    return bool(lower) and any(token in lower for token in FOOTER_LEAK_TOKENS)


def _strip_repeated_footer_text(children: list, in_footer_region: bool = False, seen_footer_texts: set[str] | None = None) -> list:
    if seen_footer_texts is None:
        seen_footer_texts = set()

    cleaned = []
    for el in children or []:
        if not isinstance(el, dict):
            continue

        updated = dict(el)
        node_text = str(updated.get("text", "") or "").strip()
        node_name_blob = " ".join([
            str(updated.get("name", "") or ""),
            node_text,
        ]).lower()
        node_y = int(updated.get("y", 0) or 0)
        node_h = int(updated.get("height", 0) or 0)
        node_bottom = node_y + max(0, node_h)
        is_footerish_node = (
            in_footer_region
            or "footer" in node_name_blob
            or "legal" in node_name_blob
            or node_bottom >= 940
        )

        if isinstance(updated.get("children"), list):
            updated["children"] = _strip_repeated_footer_text(
                updated["children"],
                in_footer_region=is_footerish_node,
                seen_footer_texts=seen_footer_texts,
            )

        if str(updated.get("type", "")).lower() == "text" and _is_footer_legal_text(node_text):
            normalized = node_text.lower()
            if is_footerish_node and normalized not in seen_footer_texts:
                seen_footer_texts.add(normalized)
                cleaned.append(updated)
                continue
            if not is_footerish_node:
                continue
            continue

        cleaned.append(updated)

    return cleaned




def sanitize_generated_children(children: list, parent_bg: str = "") -> list:
    sanitized = []
    for el in children:
        if not isinstance(el, dict):
            continue

        if isinstance(el.get("children"), list):
            local_bg = _normalized_color(el.get("backgroundColor"))
            child_bg = parent_bg
            if local_bg not in {"", "transparent"}:
                child_bg = local_bg
            el = {**el, "children": sanitize_generated_children(el["children"], child_bg)}

        if _looks_like_blank_shell(el):
            continue

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

        node_type = str(el.get("type", "")).lower()
        if node_type == "button":
            el = _sanitize_button_node(el, parent_bg)
        elif node_type == "text":
            el = _sanitize_text_node(el, parent_bg)
        elif node_type in {"frame", "component"}:
            el = _sanitize_container_node(el, parent_bg)

        sanitized.append(el)
    return _strip_repeated_footer_text(sanitized)


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


def _is_meaningful_element(el: dict) -> bool:
    if not isinstance(el, dict):
        return False
    node_type = str(el.get("type", "")).lower()
    if node_type in {"text", "button", "image", "frame", "component", "group"}:
        return True
    if node_type == "rectangle":
        return int(el.get("width", 0) or 0) > 8 and int(el.get("height", 0) or 0) > 8
    return False


def _has_navbar_only_layout(children: list, page_width: int) -> bool:
    nav_like = 0
    meaningful_non_nav = 0
    for el in children or []:
        if not isinstance(el, dict):
            continue
        width = int(el.get("width", 0) or 0)
        height = int(el.get("height", 0) or 0)
        name_blob = " ".join([str(el.get("name", "") or ""), str(el.get("text", "") or "")]).lower()
        if width >= int(page_width * 0.7) and 40 <= height <= 120 and any(token in name_blob for token in ["nav", "header", "top bar", "topbar"]):
            nav_like += 1
        elif _is_meaningful_element(el):
            meaningful_non_nav += 1
    return nav_like >= 1 and meaningful_non_nav <= 2


def _count_large_content_blocks(children: list) -> int:
    count = 0
    for el in children or []:
        if not isinstance(el, dict):
            continue
        node_type = str(el.get("type", "")).lower()
        width = int(el.get("width", 0) or 0)
        height = int(el.get("height", 0) or 0)
        name_blob = " ".join([str(el.get("name", "") or ""), str(el.get("text", "") or "")]).lower()
        if node_type in {"frame", "component", "rectangle"} and width >= 220 and height >= 140:
            if any(token in name_blob for token in [
                "form", "card", "panel", "table", "list", "stats", "chart", "content",
                "management", "dashboard", "settings", "user", "login", "forgot password",
                "invite", "modal", "dialog", "detail"
            ]):
                count += 1
    return count


FOOTER_LEAK_TOKENS = [
    "all rights reserved",
    "terms of service",
    "privacy policy",
    "documentation",
]


def _collect_text_values_from_elements(elements: list) -> list[str]:
    values = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        text = str(el.get("text", "") or "").strip()
        if text:
            values.append(text)
        if isinstance(el.get("children"), list):
            values.extend(_collect_text_values_from_elements(el["children"]))
    return values


def _looks_like_table_screen(children: list, is_product_ui: bool) -> bool:
    if not is_product_ui:
        return False
    joined = " ".join(_collect_text_values_from_elements(children)).lower()
    return any(token in joined for token in [
        "email", "role", "status", "last login", "last updated", "actions",
        "bot name", "phase",
    ])


def _footer_leak_score(text_values: list[str]) -> int:
    score = 0
    for value in text_values:
        lower = value.lower()
        if any(token in lower for token in FOOTER_LEAK_TOKENS):
            score += 1
    return score


def _has_entity_like_table_content(text_values: list[str]) -> bool:
    email_like = sum(1 for value in text_values if "@" in value and "." in value)
    status_like = sum(1 for value in text_values if value.strip().lower() in {"active", "inactive", "pending", "draft", "archived", "production", "warning", "failed", "passed"})
    person_or_bot_like = sum(
        1
        for value in text_values
        if len(value.split()) >= 2
        and len(value) <= 48
        and not any(token in value.lower() for token in FOOTER_LEAK_TOKENS)
    )
    return email_like >= 2 or status_like >= 2 or person_or_bot_like >= 4


def _iter_nodes(elements: list):
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        yield el
        if isinstance(el.get("children"), list):
            yield from _iter_nodes(el["children"])


def _node_bottom(el: dict) -> int:
    y = int(el.get("y", 0) or 0)
    h = int(el.get("height", 0) or 0)
    return y + max(0, h)


def _is_large_surface_node(el: dict, page_width: int) -> bool:
    if not isinstance(el, dict):
        return False
    node_type = str(el.get("type", "")).lower()
    if node_type not in {"frame", "component", "rectangle"}:
        return False
    width = int(el.get("width", 0) or 0)
    height = int(el.get("height", 0) or 0)
    bg = _normalized_color(el.get("backgroundColor"))
    if bg in {"", "transparent"}:
        return False
    return width >= int(page_width * 0.7) and height >= 320


def _looks_like_meaningful_content_node(el: dict, page_width: int) -> bool:
    if not isinstance(el, dict):
        return False
    node_type = str(el.get("type", "")).lower()
    width = int(el.get("width", 0) or 0)
    height = int(el.get("height", 0) or 0)
    blob = " ".join([str(el.get("name", "") or ""), str(el.get("text", "") or "")]).lower()
    if node_type == "text" and len(str(el.get("text", "") or "").strip()) >= 6:
        return True
    if node_type == "button":
        return True
    if node_type in {"frame", "component", "rectangle"} and width >= 180 and height >= 80:
        if any(token in blob for token in [
            "table", "list", "form", "card", "modal", "dialog", "panel", "content",
            "dashboard", "management", "chat", "simulator", "qa", "results", "users",
        ]):
            return True
    return False


def _has_oversized_surface_tail(children: list, page_width: int) -> bool:
    nodes = list(_iter_nodes(children))
    meaningful = [el for el in nodes if _looks_like_meaningful_content_node(el, page_width)]
    if not meaningful:
        return False

    content_bottom = max(_node_bottom(el) for el in meaningful)
    tall_surfaces = [el for el in nodes if _is_large_surface_node(el, page_width)]
    if not tall_surfaces:
        return False

    dominant_surface_bottom = max(_node_bottom(el) for el in tall_surfaces)
    dominant_surface_height = max(int(el.get("height", 0) or 0) for el in tall_surfaces)

    # If a very large surface extends far below the meaningful content, it likely created
    # the black/white tail behavior seen in failed generations.
    return dominant_surface_height >= 420 and (dominant_surface_bottom - content_bottom) >= 260


def _infer_screen_class(page_name: str, page_desc: str) -> str:
    hay = " ".join([str(page_name or ""), str(page_desc or "")]).lower()
    if any(token in hay for token in ["options menu", "action menu", "dropdown open", "module dropdown", "popover"]):
        return "menu_state"
    if any(token in hay for token in ["add user", "invite user"]):
        return "add_user"
    if "login" in hay or "sign in" in hay:
        return "login"
    if any(token in hay for token in ["users page", "user management", "admin - users page"]):
        return "users_table"
    if any(token in hay for token in ["product dashboard", "dashboard"]):
        return "dashboard"
    return ""


def _contains_any_text(text_values: list[str], needles: list[str]) -> bool:
    joined = " ".join(text_values).lower()
    return any(needle in joined for needle in needles)


def _has_button_like_action(nodes: list[dict], labels: list[str]) -> bool:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", "")).lower()
        blob = " ".join([
            str(node.get("name", "") or ""),
            str(node.get("text", "") or ""),
        ]).lower()
        if node_type == "button" and any(label in blob for label in labels):
            return True
        if node_type in {"frame", "component"} and any(label in blob for label in labels):
            if int(node.get("width", 0) or 0) >= 80 and int(node.get("height", 0) or 0) >= 32:
                return True
    return False


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
    layout_context = _compact_layout_context(layout_context)
    page_log_file = _page_log_filename(page_name, page["id"])

    log.info("CODING", f"Generating page={page_name!r}  size={page_width}×{page_height}")
    _write_linewise_log(
        f"PAGE_META page={page_name!r}",
        json.dumps({
            "page_id": page["id"],
            "page_name": page_name,
            "page_width": page_width,
            "page_height": page_height,
            "page_desc": page_desc,
            "project_title": project_title,
        }, indent=2, ensure_ascii=False),
        filename=page_log_file,
    )

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

    canvas_mode = _infer_canvas_mode(page, layout_context)

    if canvas_mode in {"app", "overlay"}:
        layout_fit_block = (
            f"\nAPP VIEWPORT FIT RULES:\n"
            f"  This frame is a full product/application screen, not a small website mockup.\n"
            f"  Fill most of the 1440px frame with the actual UI surface.\n"
            f"  Keep app gutters tight and realistic, usually around 16-32px.\n"
            f"  Do NOT preserve blank screenshot margins or browser capture whitespace around the app.\n"
            f"  Do NOT render an extra gray canvas, outer border, or screenshot frame around the interface.\n"
            f"  Sidebars, tables, lists, detail panels, cards, modals, and toolbars must be generously sized, not compressed.\n"
        )
    elif canvas_mode == "document":
        layout_fit_block = (
            f"\nDOCUMENT/PAGE FIT RULES:\n"
            f"  This frame may represent a long-scroll page, article, docs page, report, catalog, or other content-rich surface.\n"
            f"  Use the width confidently while preserving readable content columns and natural section rhythm.\n"
            f"  Ignore screenshot whitespace that sits outside the actual content surface.\n"
            f"  Long-form content may scroll vertically with multiple clearly separated sections.\n"
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
  canvas_mode: {canvas_mode}
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
    def _build_contents():
        prompt = text_prompt
        if screenshot_base64:
            return [
                {"inline_data": {"mime_type": screenshot_media_type, "data": screenshot_base64}},
                f"This is the VISUAL REFERENCE screenshot. It may already be cropped to the real UI bounds.\n"
                f"Study its colors, layout, sidebar, cards, topbar, typography and spacing carefully.\n"
                f"Do not recreate screenshot whitespace, editor canvas, or outer framing beyond the actual product surface.\n\n"
                f"Now generate Figma JSON elements for this frame:\n\n{prompt}"
            ]
        return [prompt]

    def _log_contents(section: str, contents, filename: str = CODING_LOG_FILE):
        if isinstance(contents, list):
            lines = []
            for item in contents:
                if isinstance(item, str):
                    lines.append(item)
                elif isinstance(item, dict) and "inline_data" in item:
                    inline = item.get("inline_data") or {}
                    mime = inline.get("mime_type", "application/octet-stream")
                    data_len = len(str(inline.get("data", "") or ""))
                    lines.append(f"[inline_data mime={mime} chars={data_len}]")
                else:
                    lines.append(json.dumps(item, ensure_ascii=False))
            _write_linewise_log(section, "\n".join(lines), filename=filename)
        else:
            _write_linewise_log(section, contents, filename=filename)

    log.info("CODING", f"Calling Gemini {'with screenshot vision' if screenshot_base64 else '(text only)'} for page={page_name!r}")

    request_contents = _build_contents()
    _log_contents(
        f"CODING_PROMPT page={page_name!r}",
        request_contents,
    )
    _log_contents(
        f"CODING_PROMPT page={page_name!r}",
        request_contents,
        filename=page_log_file,
    )
    response = await generate_content_with_retry(
        client=client,
        model=planner_model,
        contents=request_contents,
        config=None,
        log_tag="CODING",
        action=f"Generate page nodes for {page_name!r}",
    )
    raw = response.text
    log.debug("CODING", f"Raw response: {len(raw)} chars for page={page_name!r}")
    _write_linewise_log(f"CODING_RAW_RESPONSE page={page_name!r}", raw)
    _write_linewise_log(f"CODING_RAW_RESPONSE page={page_name!r}", raw, filename=page_log_file)

    children = parse_coding_response(raw, page_name)
    children = sanitize_generated_children(children)
    children = enforce_reusable_structure(children)
    children = stabilize_generated_children(children)
    children = normalize_compact_controls(children)
    children = inject_image_urls(children)
    _write_json_log(f"CODING_PARSED_CHILDREN page={page_name!r}", children)
    _write_json_log(f"CODING_PARSED_CHILDREN page={page_name!r}", children, filename=page_log_file)

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

    json_dump_payload = {
        "page_id": page["id"],
        "page_name": page_name,
        "project_title": project_title,
        "theme": selected_theme,
        "frame": frame,
    }
    json_dump_path = _write_page_json_dump(page_name, page["id"], json_dump_payload)
    log.info("CODING", f"Page JSON dump saved: {json_dump_path}")
    _write_json_log(
        f"CODING_FINAL_FRAME page={page_name!r}",
        json_dump_payload,
        filename=page_log_file,
    )
    log.info("CODING", f"Page log saved: backend/logs/{page_log_file}")

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
