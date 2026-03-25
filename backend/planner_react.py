"""
planner_react.py  —  React Export Planner  (v3 — Single-Shot)

Architecture:
  ONE Gemini call receives the complete frame summary (all pages + all
  components + all assets) and returns the full ProductMap in one go.

  Advantages over v2 two-phase:
    ✓ LLM sees ALL pages AND all components simultaneously
      → much more accurate cross-frame button→modal linking
    ✓ LLM can see page→component→page navigation chains in one pass
    ✓ No async merge step, no inter-phase inconsistency
    ✓ Fewer total tokens (shared context costs vs. two separate prompts)
    ✓ Deterministic ordering (no asyncio.gather race conditions)

  The output JSON schema is a superset of the v2 schema so all callers
  (build_page_context, build_component_context, resolve_*) are unchanged.
"""

import os
import re
import json
from google import genai
from dotenv import load_dotenv
import logger as log

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY1"))
MODEL  = os.getenv("GEMINI_PLANNER_MODEL", "gemini-2.0-flash")


# ─────────────────────────────────────────────────────────────────
# SINGLE-SHOT ANALYSIS PROMPT
# ─────────────────────────────────────────────────────────────────

SINGLE_SHOT_PROMPT = """
You are a senior React architect analyzing a Figma design export.

You will receive ALL Figma frames for an application in one pass.
Each frame has: name, width, height, and nodes (key UI elements inside).

Your job is to produce a complete ProductMap in ONE analysis pass.

════════════════════════════════════════════════════════════════════
STEP 1 — CLASSIFY EVERY FRAME
════════════════════════════════════════════════════════════════════

Read every frame and classify it as either a PAGE or a COMPONENT.

A frame is a PAGE if:
  - Its width >= 1200px (full-width layout)
  - OR its name sounds like a screen: Home, Dashboard, Login, Settings,
    Profile, Feed, Search, Checkout, Onboarding, Overview, Landing, etc.
  - NOT if name contains: Modal, Dialog, Drawer, Toast, Popover,
    Tooltip, Card, Widget, Panel, Form, Table, Nav, Header, Footer,
    Banner, Bar, Menu, Dropdown, Picker, Calendar, Upload, Preview,
    Viewer, Editor, Player, Chat, Badge

A frame is a COMPONENT if:
  - Its width < 1200px
  - OR its name contains any component keyword listed above
  - OR it uses slash notation "ComponentName/TabName" or "ComponentName/State"

Slash notation means tabs/states of the SAME component:
  "Settings Panel/General"   → tab "General" of "Settings Panel"
  "Settings Panel/Security"  → tab "Security" of "Settings Panel"
  Group ALL slash variants under ONE component entry.

════════════════════════════════════════════════════════════════════
STEP 2 — MAP EVERY INTER-FRAME RELATIONSHIP
════════════════════════════════════════════════════════════════════

For every button / clickable element across ALL frames:

  A. If it navigates to another PAGE:
       action: "navigate"
       target: the exact page component_name (PascalCase)
       route: the route path for that page

  B. If it opens a COMPONENT (modal, drawer, etc.):
       action: "open_modal"
       target: the exact component component_name (PascalCase)
       Match by button text ≈ component name using fuzzy comparison:
         Strip "Modal"/"Dialog"/"Drawer"/"Panel"/"Sheet" suffix then compare
         "Add Connector" button ↔ "Connector Modal" → MATCH
         "Open Settings"  button ↔ "Settings Panel" → MATCH
         "New User"        button ↔ "Create User Modal" → MATCH

  C. If it closes/dismisses the parent component:
       action: "close_modal"
       target: "onClose"
       Position heuristic: top-right corner × or close = close_modal

  D. If it switches a tab within the same component:
       action: "switch_tab"
       target: exact tab name

  E. If it submits a form:
       action: "submit_form"

  F. If it navigates back:
       action: "back"

  G. If it logs out:
       action: "logout"

  H. If it triggers search:
       action: "search"

  I. If it toggles a checkbox/switch:
       action: "toggle"

  J. If it collapses/minimizes a panel:
       action: "minimize"

  K. If it opens a file picker:
       action: "file_pick"

  L. If the target page/component is NOT in the design:
       action: "missing_target"
       target: "name you expected"

  M. Anything else still needs a handler:
       action: "unknown"

DUAL DETECTION — check BOTH the element's node NAME and its visible TEXT.
Either one can resolve the action. Never leave a button without an action.

════════════════════════════════════════════════════════════════════
STEP 3 — BUILD FULL PAGE ENTRIES
════════════════════════════════════════════════════════════════════

For each PAGE output:
  - name:                 exact Figma frame name
  - route:                "/" for first page, "/slug" for others
  - component_name:       PascalCase
  - file:                 "pages/ComponentName.jsx"
  - description:          one sentence describing purpose
  - frame_name:           exact Figma frame name (same as name)
  - links_to_pages:       list of page component_names this page navigates TO
  - linked_from_pages:    list of page component_names that link INTO this page
  - imports_components:   list of component component_names used/opened on this page
  - interactive_elements: EVERY button/link/clickable (see format below)

════════════════════════════════════════════════════════════════════
STEP 4 — BUILD FULL COMPONENT ENTRIES
════════════════════════════════════════════════════════════════════

For each COMPONENT output:
  - name:                 exact Figma frame name (or base name for slash groups)
  - component_name:       PascalCase
  - file:                 "components/ComponentName.jsx"
  - type:                 one of: modal | dialog | drawer | bottomsheet | sidebar |
                          toast | popover | tooltip | tabs | table | form | card |
                          navbar | footer | banner | upload | calendar | chart |
                          list | avatar | badge | generic
  - description:          one sentence
  - master_frame:         exact Figma frame name for the "default" state
  - tab_frames:           [] or list of { tab_name, frame_name } for slash groups
  - default_tab:          name of the first/default tab (empty string if no tabs)
  - props:                ["onClose"] (add others if needed)
  - used_in_pages:        list of PAGE component_names that open this component
  - interactive_elements: EVERY button/link/clickable inside this component
  - asset_nodes:          [] or list of @svg/@image assets found inside

════════════════════════════════════════════════════════════════════
STEP 5 — INTERACTIVE ELEMENT FORMAT
════════════════════════════════════════════════════════════════════

Every interactive_element entry MUST follow this exact schema:
{
  "element_name":   "the node name from Figma (or descriptive label)",
  "button_text":    "visible text on the button (empty string if icon-only)",
  "action":         "one of the action codes from Step 2",
  "target":         "component_name / route / tab_name / onClose / expected name",
  "position_hint":  "brief location hint (top-right, bottom of form, etc.)",
  "notes":          "anything else useful for the code generator"
}

════════════════════════════════════════════════════════════════════
STEP 6 — ASSET NODES FORMAT
════════════════════════════════════════════════════════════════════

Nodes named "@svg label" or "@image label" are exported assets.
Strip Figma auto-IDs: "@svg logo/Frame 19923" → label = "logo"

For each asset:
{
  "label":          "canonical label (before the slash)",
  "file":           "/assets/images/label.png",
  "also_button":    true / false,
  "button_action":  "action code if also_button, else null"
}

ALWAYS use .png — never .svg.
The plugin exports ALL @svg and @image nodes as PNG files.

Infer also_button from label keywords:
  close/dismiss/x/cancel/exit      → close_modal
  minimize/collapse/hide            → minimize
  back/return/previous/arrow-left  → back
  menu/hamburger/burger             → toggle
  search/find/magnify               → search
  add/plus/create/new               → open_modal
  edit/pencil/pen/modify            → open_modal
  delete/trash/remove/bin           → submit_form
  settings/gear/cog/config          → open_modal
  logout/signout/sign-out           → logout
  upload/attach/file/browse         → file_pick

════════════════════════════════════════════════════════════════════
STEP 7 — SHARED ASSETS
════════════════════════════════════════════════════════════════════

List every unique @svg/@image asset found across ALL frames:
{
  "label":       "logo",
  "file":        "/assets/images/logo.png",
  "used_in":     ["HomePage", "Dashboard"],
  "also_button": false
}

════════════════════════════════════════════════════════════════════
OUTPUT FORMAT — COMPLETE PRODUCT MAP
════════════════════════════════════════════════════════════════════

Return ONLY valid JSON — no markdown, no fences, no explanations:

{
  "project_name": "string",
  "navigation_flow": "brief description of overall user journey",
  "pages": [ ...page entries as described in Step 3... ],
  "components": [ ...component entries as described in Step 4... ],
  "routing": [
    { "path": "/",          "component": "HomePage" },
    { "path": "/dashboard", "component": "Dashboard" }
  ],
  "shared_assets": [ ...as described in Step 7... ]
}

════════════════════════════════════════════════════════════════════
CRITICAL RULES
════════════════════════════════════════════════════════════════════

1.  Output ONLY valid JSON — nothing else.
2.  component_name MUST be PascalCase everywhere.
3.  First page route = "/", all others = "/kebab-slug".
4.  Every interactive element MUST have an action — no silent dead buttons.
5.  Use dual detection: check BOTH node name AND button text.
6.  For open_modal: fuzzy-match button text to component names.
7.  For navigate: fuzzy-match button text to page names.
8.  ALL tab_frames must be listed — never truncate.
9.  Asset files are ALWAYS .png — never .svg.
10. imports_components on each page must be the union of all open_modal targets.
11. used_in_pages on each component must list every page that opens it.
12. links_to_pages and linked_from_pages must be consistent with navigate actions.
13. Cross-check: if page A navigates to page B, then:
      A.links_to_pages includes B.component_name
      B.linked_from_pages includes A.component_name
14. Cross-check: if page A opens component C, then:
      A.imports_components includes C.component_name
      C.used_in_pages includes A.component_name
"""


# ─────────────────────────────────────────────────────────────────
# FRAME SUMMARIZER  (preserved from v2, unchanged)
# ─────────────────────────────────────────────────────────────────

def _summarize_frame_for_planner(frame: dict, max_nodes: int = 250) -> list:
    """
    Walk a Figma frame JSON tree and extract semantically useful nodes.
    Captures: buttons (name + text), text, inputs, containers, images, assets.
    """
    nodes = []

    def walk(node, depth=0):
        if not isinstance(node, dict):
            return
        if len(nodes) >= max_nodes:
            return

        t    = node.get("type", "").lower()
        name = node.get("name", "")
        text = node.get("text", "")
        w    = node.get("width",  0)
        h    = node.get("height", 0)
        x    = node.get("x", 0)
        y    = node.get("y", 0)

        # ── @svg / @image asset nodes ─────────────────────────────
        asset_match = re.match(r"^@(svg|image)\s+([^/]+?)(?:/.*)?$", name, re.IGNORECASE)
        if asset_match:
            asset_label = asset_match.group(2).strip()
            btn_action  = _infer_asset_button_action(asset_label)
            nodes.append({
                "type":          "asset",
                "asset_type":    asset_match.group(1).lower(),
                "label":         asset_label,
                "name":          name,
                "width":         w,
                "height":        h,
                "x":             x,
                "y":             y,
                "also_button":   btn_action is not None,
                "button_action": btn_action,
            })
            return  # don't recurse — asset is a leaf

        # ── Button nodes ──────────────────────────────────────────
        if t == "button":
            btn_text = node.get("text", "").strip() or name
            nodes.append({
                "type":        "button",
                "name":        name[:60],
                "button_text": btn_text[:80],
                "width":       w,
                "height":      h,
                "x":           x,
                "y":           y,
                "depth":       depth,
            })

        # ── Text nodes ────────────────────────────────────────────
        elif t == "text" and text.strip():
            nodes.append({
                "type":   "text",
                "text":   text.strip()[:100],
                "size":   node.get("fontSize", 0),
                "width":  w,
                "height": h,
                "x":      x,
                "y":      y,
            })

        # ── Input nodes ───────────────────────────────────────────
        elif t in ("input", "textarea"):
            nodes.append({
                "type":        "input",
                "name":        name[:50],
                "placeholder": str(node.get("placeholder", node.get("text", name)))[:60],
                "width":       w,
                "height":      h,
            })

        # ── Named containers ──────────────────────────────────────
        elif t in ("frame", "group", "component", "instance"):
            is_auto = bool(re.match(r"^(Frame|Group|Component|Rectangle|Vector)\s+\d+$", name))
            if name and not is_auto and depth < 5:
                nodes.append({
                    "type":   "container",
                    "name":   name[:60],
                    "width":  w,
                    "height": h,
                    "x":      x,
                    "y":      y,
                    "depth":  depth,
                })

        # ── Image nodes ───────────────────────────────────────────
        elif t == "image":
            nodes.append({
                "type":   "image",
                "name":   name[:50],
                "width":  w,
                "height": h,
            })

        for child in node.get("children", []):
            walk(child, depth + 1)

    walk(frame)
    return nodes


def _infer_asset_button_action(label: str) -> str | None:
    """
    Given an @svg/@image label, return the button action it implies,
    or None if purely decorative.
    """
    label_lower = label.lower()
    parts = set(re.split(r"[\s\-_]+", label_lower))

    if parts & {"close", "dismiss", "x", "cross", "cancel", "exit"}:      return "close_modal"
    if parts & {"minimize", "minimise", "collapse", "hide", "shrink"}:     return "minimize"
    if parts & {"back", "return", "previous", "prev", "arrow-left", "chevron-left"}: return "back"
    if parts & {"menu", "hamburger", "burger", "nav-toggle"}:              return "toggle"
    if parts & {"search", "find", "magnify", "magnifier"}:                 return "search"
    if parts & {"add", "plus", "create", "new"}:                           return "open_modal"
    if parts & {"edit", "pencil", "pen", "modify"}:                        return "open_modal"
    if parts & {"delete", "trash", "remove", "bin"}:                       return "submit_form"
    if parts & {"settings", "gear", "cog", "config", "preferences"}:      return "open_modal"
    if parts & {"logout", "signout", "sign-out", "log-out"}:               return "logout"
    if parts & {"upload", "attach", "file", "browse"}:                     return "file_pick"
    return None


# ─────────────────────────────────────────────────────────────────
# SINGLE-SHOT RUNNER
# ─────────────────────────────────────────────────────────────────

async def run_react_planner(frames: list[dict]) -> dict:
    """
    Single Gemini call — analyze all frames and return a complete ProductMap.

    Args:
        frames: list of dicts, each with { name, width, height, frame (JSON tree),
                nav_hint (optional strict button→page links),
                desc_hint (optional freeform instructions) }

    Returns:
        ProductMap dict with pages, components, routing, shared_state, asset_registry
    """
    log.info("REACT_PLANNER", f"Starting v3 (single-shot) — {len(frames)} frame(s)")

    # ── Collect user-defined strict links from nav_hint ───────────
    #
    # HOW NAV HINTS ARRIVE:
    #   code.js parseFrameName() always returns navHint: null.
    #   The ONLY source of nav_hint is ui.html injectPanelContext(),
    #   which formats it as "@nav: ButtonText -> TargetFrameName, ..."
    #   when the user explicitly links buttons in the Export Panel.
    #
    # TWO-TIER SYSTEM:
    #   TIER 1 — Explicit Export Panel links (@nav: format)
    #     → parsed here into user_links
    #     → injected into LLM prompt as STRICT HARD RULES
    #     → _apply_strict_user_links() overrides LLM output after analysis
    #
    #   TIER 3 — LLM inference (no nav_hint at all)
    #     → SINGLE_SHOT_PROMPT Step 2 fuzzy-matches button text to frame names
    #       e.g. "Add Connector" button → "Connector Modal" component (MATCH)
    #       e.g. "Connector Modal/Connected" slash groups handled automatically
    #     → _resolve_all_links() post-processes and corrects LLM output
    #
    # NOTE: desc_hint (per-frame prompt) is handled below in frame summaries.

    user_links: list[dict] = []   # [ { source_frame, button_text, target_name } ]

    for f in frames:
        fname   = f.get("name", "?")
        raw_nav = f.get("nav_hint", "")
        desc    = f.get("desc_hint", "")

        # Log per-frame prompt
        if desc:
            log.info("REACT_PLANNER",
                f"[FRAME-PROMPT] '{fname}' → instructions: \"{desc[:80]}\""
            )

        # Parse nav hints
        if raw_nav and "@nav:" in raw_nav:
            # TIER 1: explicit Export Panel links
            parsed_links = _parse_nav_hint_strict(raw_nav, fname)
            user_links.extend(parsed_links)
            log.info("REACT_PLANNER",
                f"[TIER-1 EXPLICIT] '{fname}' → "
                f"{len(parsed_links)} strict link(s): "
                + ", ".join(
                    f"'{l['button_text']}' → '{l['target_name']}'"
                    for l in parsed_links
                )
            )
        elif raw_nav:
            # nav_hint present but not in @nav: format — unrecognised, skip safely
            log.warn("REACT_PLANNER",
                f"[NAV-HINT SKIP] '{fname}' → nav_hint has no @nav: prefix: "
                f"\"{raw_nav[:80]}\" — LLM will infer instead"
            )
        else:
            # TIER 3: no explicit links — LLM infers from button text + frame names
            log.info("REACT_PLANNER",
                f"[TIER-3 LLM] '{fname}' → no explicit links, "
                f"LLM infers from button text and component names"
            )

    # ── Build frame summaries ─────────────────────────────────────
    frame_summaries = []
    for f in frames:
        name   = f.get("name", "Unnamed")
        width  = f.get("width", 1440)
        height = f.get("height", 900)
        frame  = f.get("frame", {})

        nodes = _summarize_frame_for_planner(frame)

        # Store back for asset registry scanning
        f["_summary_nodes"] = nodes

        summary = {
            "name":   name,
            "width":  width,
            "height": height,
            "nodes":  nodes,
        }

        # Surface user-defined desc_hint as context so LLM understands intent
        desc = f.get("desc_hint", "")
        if desc:
            summary["designer_instructions"] = desc

        frame_summaries.append(summary)

        log.debug("REACT_PLANNER",
            f"  Frame: {name!r} ({width}×{height}) — {len(nodes)} nodes"
            + (f" [desc: {desc[:60]}]" if desc else "")
        )

    # ── Build the prompt ──────────────────────────────────────────
    frames_json = json.dumps(frame_summaries, indent=2)

    # If the user has set explicit links, tell the planner about them
    user_links_block = ""
    if user_links:
        user_links_block = "\n\nUSER-DEFINED BUTTON LINKS (STRICT — do not change these):\n"
        for ul in user_links:
            user_links_block += (
                f"  Frame '{ul['source_frame']}': "
                f"button '{ul['button_text']}' → '{ul['target_name']}'\n"
            )
        user_links_block += (
            "\nFor every link listed above, set the exact action and target as specified.\n"
            "The LLM must honour these links even if it disagrees with the target.\n"
        )

    prompt = (
        f"{SINGLE_SHOT_PROMPT}"
        f"{user_links_block}\n\n"
        f"ALL FRAMES TO ANALYZE ({len(frame_summaries)} total):\n"
        f"{frames_json}"
    )

    log.info("REACT_PLANNER",
        f"Calling Gemini single-shot — prompt size: {len(prompt):,} chars"
        + (f" ({len(user_links)} strict user links)" if user_links else "")
    )

    # ── One Gemini call ───────────────────────────────────────────
    response = client.models.generate_content(
        model    = MODEL,
        contents = prompt,
        config   = {"temperature": 0.0},
    )
    raw = response.text.strip()
    log.debug("REACT_PLANNER", f"Raw response: {len(raw)} chars")

    # ── Parse ─────────────────────────────────────────────────────
    result = _parse_json_response(raw)

    # ── Clean raw frame names that contain old @type:/@desc: hints ─
    # Figma designers sometimes name frames like:
    #   "Connector Modal | @type: modal | @desc: ..."
    # The LLM sees these verbatim and outputs them as component names.
    # Strip everything after the first "|" pipe character.
    def _clean_frame_name(name: str) -> str:
        if "|" in name:
            return name.split("|")[0].strip()
        return name.strip()

    for item in result.get("pages", []) + result.get("components", []):
        for key in ("name", "frame_name", "master_frame"):
            if key in item and item[key]:
                item[key] = _clean_frame_name(item[key])

    # ── Deduplicate components with same cleaned name ─────────────
    # Keep only the first occurrence of each cleaned component name
    seen_comp_names: set[str] = set()
    unique_comps = []
    for comp in result.get("components", []):
        cleaned = _clean_frame_name(comp.get("name", ""))
        comp["name"] = cleaned
        cn_key = _normalise(cleaned)
        if cn_key not in seen_comp_names:
            seen_comp_names.add(cn_key)
            unique_comps.append(comp)
        else:
            log.info("REACT_PLANNER",
                f"  Deduplicated component '{cleaned}' (duplicate removed)")
    result["components"] = unique_comps

    # ── Also clean page names ──────────────────────────────────────
    for page in result.get("pages", []):
        page["name"] = _clean_frame_name(page.get("name", ""))

    # ── Normalise pages ───────────────────────────────────────────
    pages = result.get("pages", [])
    for i, page in enumerate(pages):
        page.setdefault("route",               "/" if i == 0 else f"/{_to_slug(page.get('name', f'page-{i+1}'))}")
        page.setdefault("component_name",      _to_pascal(page.get("name", f"Page{i+1}")))
        page.setdefault("file",                f"pages/{page['component_name']}.jsx")
        page.setdefault("description",         "")
        page.setdefault("frame_name",          page.get("name", ""))
        page.setdefault("links_to_pages",      [])
        page.setdefault("linked_from_pages",   [])
        page.setdefault("imports_components",  [])
        page.setdefault("interactive_elements", [])

    # ── Normalise components ──────────────────────────────────────
    components = result.get("components", [])
    for comp in components:
        comp.setdefault("component_name",      _to_pascal(comp.get("name", "Component")))
        comp.setdefault("file",                f"components/{comp['component_name']}.jsx")
        comp.setdefault("type",                "modal")
        comp.setdefault("description",         "")
        comp.setdefault("master_frame",        comp.get("name", ""))
        comp.setdefault("tab_frames",          [])
        comp.setdefault("default_tab",         "")
        comp.setdefault("props",               ["onClose"])
        comp.setdefault("used_in_pages",       [])
        comp.setdefault("interactive_elements", [])
        comp.setdefault("asset_nodes",         [])

    # ── Attach desc_hint to matching pages/components for code-gen ─
    # desc_hint carries freeform user instructions (e.g. "make it a
    # rule-based chatbot") — store it on the page/comp so build_page_context
    # can emit it as a hard instruction to the code-gen LLM.
    frame_desc_map = {f.get("name", ""): f.get("desc_hint", "") for f in frames}
    for page in pages:
        hint = frame_desc_map.get(page["name"], "") or frame_desc_map.get(page.get("frame_name",""), "")
        if hint:
            page["user_instructions"] = hint
    for comp in components:
        hint = frame_desc_map.get(comp["name"], "")
        if hint:
            comp["user_instructions"] = hint

    # ── Apply designer |default tab hints ────────────────────────
    _apply_default_tab_hints(components, frames)

    # ── Fix routing table ─────────────────────────────────────────
    routing = result.get("routing", [])
    if not routing:
        routing = [
            {"path": p["route"], "component": p["component_name"]}
            for p in pages
        ]
    result["routing"] = routing

    # ── Post-process: fix all modal / nav links ───────────────────
    log.info("REACT_PLANNER", "Resolving cross-frame button links...")
    _resolve_all_links(pages, components, routing)

    # ── Apply strict user-defined links AFTER planner resolution ──
    # These override whatever the LLM decided — no fuzzy matching.
    if user_links:
        log.info("REACT_PLANNER",
            f"Applying {len(user_links)} strict user link(s) as final overrides..."
        )
        _apply_strict_user_links(user_links, pages, components, routing)

    # ── Build asset registry ──────────────────────────────────────
    asset_registry = _build_asset_registry(
        shared_assets = result.get("shared_assets", []),
        all_frames    = frames,
    )

    # ── Assemble final ProductMap ─────────────────────────────────
    product_map = {
        "project_name":    result.get("project_name", "My App"),
        "navigation_flow": result.get("navigation_flow", ""),
        "pages":           pages,
        "components":      components,
        "routing":         routing,
        "shared_state":    _derive_shared_state(pages),
        "asset_registry":  asset_registry,
    }

    # ── Summary log ───────────────────────────────────────────────
    log.success("REACT_PLANNER",
        f"ProductMap ready — {len(pages)} pages, {len(components)} components, "
        f"{len(asset_registry)} assets",
        extra={
            "pages":      [p["name"] for p in pages],
            "components": [c["name"] for c in components],
        }
    )
    for page in pages:
        ie  = len(page.get("interactive_elements", []))
        imp = page.get("imports_components", [])
        ui  = page.get("user_instructions", "")
        log.info("REACT_PLANNER",
            f"  PAGE: {page['name']!r} → {page['route']}  "
            f"({ie} interactions, imports: {imp})"
            + (f"  [instructions: {ui[:40]}]" if ui else "")
        )
    for comp in components:
        ie   = len(comp.get("interactive_elements", []))
        tabs = len(comp.get("tab_frames", []))
        log.info("REACT_PLANNER",
            f"  COMP: {comp['name']!r} ({comp['type']})  "
            f"tabs={tabs}  interactions={ie}"
        )

    return product_map


# ─────────────────────────────────────────────────────────────────
# USER NAV HINT PARSER
# Parses "@nav: Button Text -> Target Frame" strings from the
# Export Panel into structured strict link objects.
# ─────────────────────────────────────────────────────────────────

def _parse_nav_hint_strict(raw_nav: str, source_frame: str) -> list[dict]:
    """
    Parse "@nav: Login Btn -> Login Page, Sign Up -> Signup" into:
    [ { source_frame, button_text, target_name }, ... ]
    """
    links = []
    # Strip @nav: prefix
    body = re.sub(r"(?i)@nav\s*:\s*", "", raw_nav).strip()
    for entry in body.split(","):
        entry = entry.strip()
        if "->" not in entry:
            continue
        parts       = entry.split("->", 1)
        button_text = parts[0].strip()
        target_name = parts[1].strip()
        if button_text and target_name:
            links.append({
                "source_frame": source_frame,
                "button_text":  button_text,
                "target_name":  target_name,
            })
    return links


# ─────────────────────────────────────────────────────────────────
# STRICT USER LINK APPLIER
# Runs AFTER the planner's own _resolve_all_links pass.
# For every user-defined link, find the matching interactive element
# and force-set its action+target — overriding whatever the LLM chose.
# If no matching element exists, inject a new one.
# ─────────────────────────────────────────────────────────────────

def _apply_strict_user_links(
    user_links: list[dict],
    pages:      list[dict],
    components: list[dict],
    routing:    list[dict],
) -> None:
    """
    Hard-wire every user-defined button→target link.
    Target resolution order:
      1. Exact frame name match among pages
      2. Exact frame name match among components
      3. Fuzzy word-overlap match among pages
      4. Fuzzy word-overlap match among components
    """
    # Build lookups
    page_by_name: dict[str, dict] = {}
    for p in pages:
        page_by_name[_normalise(p["name"])]           = p
        page_by_name[_normalise(p["component_name"])] = p

    comp_by_name: dict[str, dict] = {}
    for c in components:
        comp_by_name[_normalise(c["name"])]           = c
        comp_by_name[_normalise(c["component_name"])] = c

    route_by_cn: dict[str, str] = {r["component"]: r["path"] for r in routing}

    def _find_target(target_name: str) -> tuple[str, str, str]:
        """
        Returns (action, target_cn, route_or_empty).
        action is 'navigate' for pages, 'open_modal' for components.
        Handles slash notation: "Connector Modal/Connected" → look for "Connector Modal"
        """
        # Strip pipe hints from target name too (in case user copied a raw frame name)
        if "|" in target_name:
            target_name = target_name.split("|")[0].strip()

        key = _normalise(target_name)

        # Exact page match
        if key in page_by_name:
            p  = page_by_name[key]
            cn = p["component_name"]
            return ("navigate", cn, route_by_cn.get(cn, p.get("route", "/")))

        # Exact component match
        if key in comp_by_name:
            c  = comp_by_name[key]
            cn = c["component_name"]
            return ("open_modal", cn, "")

        # Slash notation — try the part before the first slash
        if "/" in target_name:
            base = target_name.split("/")[0].strip()
            base_key = _normalise(base)
            if base_key in page_by_name:
                p  = page_by_name[base_key]
                cn = p["component_name"]
                return ("navigate", cn, route_by_cn.get(cn, p.get("route", "/")))
            if base_key in comp_by_name:
                c  = comp_by_name[base_key]
                cn = c["component_name"]
                return ("open_modal", cn, "")

        # Fuzzy page match
        tw = set(w for w in re.split(r"[\s\-_/()]+", target_name.lower()) if len(w) > 2)
        best_score, best_page = 0, None
        for p in pages:
            pw = set(w for w in re.split(r"[\s\-_/()]+", p["name"].lower()) if len(w) > 2)
            s  = len(tw & pw)
            if s > best_score:
                best_score, best_page = s, p
        if best_page and best_score >= 1:
            cn = best_page["component_name"]
            return ("navigate", cn, route_by_cn.get(cn, best_page.get("route", "/")))

        # Fuzzy component match
        best_score, best_comp = 0, None
        for c in components:
            cw = set(w for w in re.split(r"[\s\-_/()]+", c["name"].lower()) if len(w) > 2)
            s  = len(tw & cw)
            if s > best_score:
                best_score, best_comp = s, c
        if best_comp and best_score >= 1:
            cn = best_comp["component_name"]
            return ("open_modal", cn, "")

        log.warn("REACT_PLANNER",
            f"Strict link: target '{target_name}' not found in pages or components"
        )
        return ("missing_target", _to_pascal(target_name.replace("/","").replace("|","")), "")

    # Source frame lookup
    source_frame_map: dict[str, dict] = {}
    for p in pages:
        source_frame_map[_normalise(p["name"])] = ("page", p)
    for c in components:
        source_frame_map[_normalise(c["name"])] = ("comp", c)

    for ul in user_links:
        src_key    = _normalise(ul["source_frame"])
        btn_text   = ul["button_text"].strip()
        target_raw = ul["target_name"].strip()

        if src_key not in source_frame_map:
            log.warn("REACT_PLANNER",
                f"Strict link: source frame '{ul['source_frame']}' not found — skipped"
            )
            continue

        kind, owner = source_frame_map[src_key]
        is_page     = kind == "page"
        owner_cn    = owner["component_name"]

        action, target_cn, route = _find_target(target_raw)

        log.info("REACT_PLANNER",
            f"  STRICT OVERRIDE: '{ul['source_frame']}' btn='{btn_text}' "
            f"→ {action}:{target_cn}"
            + (f" ({route})" if route else "")
        )

        # Find existing interactive element — try multiple match strategies:
        # 1. Button text exact match
        # 2. Button text substring match
        # 3. Element name exact match (catches "Frame 2611788" type links)
        # 4. Element name substring match
        elements    = owner.get("interactive_elements", [])
        btn_lower   = btn_text.lower()
        matched_el  = None
        for el in elements:
            el_text = (el.get("button_text", "") or "").lower()
            el_name = (el.get("element_name", "") or "").lower()
            if (el_text == btn_lower or
                (el_text and (btn_lower in el_text or el_text in btn_lower)) or
                el_name == btn_lower or
                (el_name and (btn_lower in el_name or el_name in btn_lower))):
                matched_el = el
                break

        if matched_el:
            # Hard override
            matched_el["action"] = action
            matched_el["target"] = target_cn
            if route:
                matched_el["route"] = route
            matched_el["notes"]  = f"[STRICT USER LINK → {target_raw}]"
        else:
            # Inject a new element — user defined this link explicitly
            new_el = {
                "element_name": btn_text,
                "button_text":  btn_text,
                "action":       action,
                "target":       target_cn,
                "position_hint": "user-defined link",
                "notes":        f"[STRICT USER LINK → {target_raw}]",
            }
            if route:
                new_el["route"] = route
            owner.setdefault("interactive_elements", []).append(new_el)
            log.info("REACT_PLANNER",
                f"  Injected new element '{btn_text}' on '{ul['source_frame']}'"
            )

        # Sync reverse lookups
        if action == "navigate" and is_page:
            target_page = page_by_name.get(_normalise(target_cn))
            if not target_page:
                for p in pages:
                    if p["component_name"] == target_cn:
                        target_page = p
                        break
            if target_page:
                if target_cn not in owner.get("links_to_pages", []):
                    owner.setdefault("links_to_pages", []).append(target_cn)
                if owner_cn not in target_page.get("linked_from_pages", []):
                    target_page.setdefault("linked_from_pages", []).append(owner_cn)

        elif action == "open_modal":
            # Add to imports_components if a page, add page to used_in_pages on comp
            target_comp = comp_by_name.get(_normalise(target_cn))
            if not target_comp:
                for c in components:
                    if c["component_name"] == target_cn:
                        target_comp = c
                        break
            if target_comp:
                if is_page and target_cn not in owner.get("imports_components", []):
                    owner.setdefault("imports_components", []).append(target_cn)
                if owner_cn not in target_comp.get("used_in_pages", []):
                    target_comp.setdefault("used_in_pages", []).append(owner_cn)

    log.success("REACT_PLANNER",
        f"Strict user links applied — {len(user_links)} link(s) enforced"
    )


# ─────────────────────────────────────────────────────────────────
# CROSS-FRAME LINK RESOLVER
# Fixes open_modal / navigate targets after the LLM response.
# Single pass — works on both pages AND components.
# ─────────────────────────────────────────────────────────────────

_STRIP_SUFFIXES = re.compile(
    r"\s*(Modal|Dialog|Drawer|Panel|Sheet|Popup|Overlay|Window|Sidebar|Picker|Viewer|Editor)$",
    re.IGNORECASE,
)


def _resolve_all_links(
    pages: list[dict],
    components: list[dict],
    routing: list[dict],
) -> None:
    """
    One unified pass that:
      1. Fixes open_modal targets against the known component list
      2. Fixes navigate targets against the known routing table
      3. Keeps imports_components / used_in_pages / links_to / linked_from in sync
    """
    # ── Index: component_name → comp dict ────────────────────────
    comp_by_name: dict[str, dict] = {c["component_name"]: c for c in components}

    # ── Index: normalised stripped name → component_name ─────────
    comp_lookup: dict[str, str] = {}
    for comp in components:
        cn   = comp["component_name"]
        name = comp.get("name", cn)
        for variant in (name, cn, _STRIP_SUFFIXES.sub("", name).strip()):
            comp_lookup[_normalise(variant)] = cn
            # Also add each significant word individually
            for word in re.split(r"[\s\-_]+", variant.lower()):
                if len(word) > 3:
                    comp_lookup.setdefault(_normalise(word), cn)

    # ── Index: page component_name → route ───────────────────────
    route_by_cn: dict[str, str] = {}
    for r in routing:
        route_by_cn[r.get("component", "")] = r.get("path", "/")
    page_by_cn: dict[str, dict] = {p["component_name"]: p for p in pages}

    # ── Helper: best component match for an open_modal target ────
    def _best_comp(target: str) -> str | None:
        if target in comp_by_name:
            return target
        key = _normalise(_STRIP_SUFFIXES.sub("", target).strip())
        if key in comp_lookup:
            return comp_lookup[key]
        # Word overlap fallback
        tw = set(w for w in re.split(r"[\s\-_]+", target.lower()) if len(w) > 3)
        best_score, best_cn = 0, None
        for comp in components:
            nw = set(w for w in re.split(r"[\s\-_]+", comp.get("name", "").lower()) if len(w) > 3)
            score = len(tw & nw)
            if score > best_score:
                best_score, best_cn = score, comp["component_name"]
        return best_cn if best_score > 0 else None

    # ── Helper: best page route for a navigate target ─────────────
    def _best_route(target: str) -> str | None:
        # exact
        if target in route_by_cn:
            return route_by_cn[target]
        # normalised
        nt = _normalise(target)
        for cn, route in route_by_cn.items():
            if _normalise(cn) == nt:
                return route
        # word overlap
        tw = set(w for w in re.split(r"[\s\-_]+", target.lower()) if len(w) > 3)
        best_score, best_route = 0, None
        for p in pages:
            pw = set(w for w in re.split(r"[\s\-_]+", p["name"].lower()) if len(w) > 3)
            score = len(tw & pw)
            if score > best_score:
                best_score, best_route = score, p["route"]
        return best_route

    # ── Fix all interactive elements ──────────────────────────────
    def _fix_elements(elements: list[dict], owner_cn: str, is_page: bool) -> None:
        for el in elements:
            action = el.get("action", "")
            target = el.get("target", "")

            if action in ("open_modal", "missing_target") and target not in ("onClose", ""):
                resolved = _best_comp(target)
                if resolved:
                    el["action"] = "open_modal"
                    el["target"] = resolved
                    # Sync reverse lookups
                    comp = comp_by_name.get(resolved)
                    if comp and owner_cn not in comp.get("used_in_pages", []):
                        comp.setdefault("used_in_pages", []).append(owner_cn)
                    if is_page:
                        page = page_by_cn.get(owner_cn)
                        if page and resolved not in page.get("imports_components", []):
                            page.setdefault("imports_components", []).append(resolved)
                else:
                    el["action"] = "missing_target"
                    el["target"] = ""   # ← clear target so no broken import is ever emitted
                    log.warn("REACT_PLANNER",
                        f"{owner_cn} btn={el.get('element_name','?')!r} "
                        f"target={target!r} — no matching component, import suppressed"
                    )

            elif action == "navigate" and target:
                resolved_route = _best_route(target)
                if resolved_route:
                    el["route"] = resolved_route
                    # Sync links_to / linked_from
                    if is_page:
                        from_page = page_by_cn.get(owner_cn)
                        # Find target page by route
                        for tp in pages:
                            if tp["route"] == resolved_route:
                                if tp["component_name"] not in from_page.get("links_to_pages", []):
                                    from_page.setdefault("links_to_pages", []).append(tp["component_name"])
                                if owner_cn not in tp.get("linked_from_pages", []):
                                    tp.setdefault("linked_from_pages", []).append(owner_cn)

    # Run fixer on pages
    for page in pages:
        _fix_elements(
            page.get("interactive_elements", []),
            owner_cn = page["component_name"],
            is_page  = True,
        )

    # Run fixer on components
    for comp in components:
        _fix_elements(
            comp.get("interactive_elements", []),
            owner_cn = comp["component_name"],
            is_page  = False,
        )

    log.success("REACT_PLANNER", "Cross-frame link resolution complete")


# ─────────────────────────────────────────────────────────────────
# DEFAULT TAB HINT APPLICATOR
# Reads the |default designer hint from raw frame dicts
# ─────────────────────────────────────────────────────────────────

def _apply_default_tab_hints(components: list[dict], all_frames: list[dict]) -> None:
    """
    Override Gemini's default_tab with the designer's |default naming hint
    if present on any source frame.
    """
    for comp in components:
        comp_name  = comp.get("name", "")
        tab_frames = comp.get("tab_frames", [])
        if not tab_frames:
            continue
        for frame in all_frames:
            frame_default = frame.get("default_tab", "")
            if not frame_default:
                continue
            fname    = frame.get("name", "")
            slash_ix = fname.find("/")
            parent   = fname[:slash_ix].strip() if slash_ix != -1 else fname.strip()
            if _normalise(parent) == _normalise(comp_name):
                comp["default_tab"] = frame_default
                log.info("REACT_PLANNER",
                    f"Default tab override: {comp_name!r} → {frame_default!r}")
                break


# ─────────────────────────────────────────────────────────────────
# ASSET REGISTRY BUILDER
# ─────────────────────────────────────────────────────────────────

def _build_asset_registry(
    shared_assets: list[dict],
    all_frames: list[dict],
) -> dict[str, dict]:
    """
    Merge assets from:
      1. Node summaries cached in frame["_summary_nodes"]
      2. shared_assets from the LLM response

    Returns: { label → { label, file, also_button, button_action, occurrences } }
    """
    registry: dict[str, dict] = {}

    # From frame node scans
    for frame in all_frames:
        frame_name = frame.get("name", "")
        for node in frame.get("_summary_nodes", []):
            if node.get("type") != "asset":
                continue
            label = node["label"]
            # Sanitize: spaces → dashes, lowercase
            safe_label = label.replace(" ", "-").lower()
            if label not in registry:
                registry[label] = {
                    "label":         label,
                    "file":          f"/assets/images/{safe_label}.png",
                    "also_button":   node.get("also_button", False),
                    "button_action": node.get("button_action"),
                    "occurrences":   [],
                }
            registry[label]["occurrences"].append(frame_name)

    # From LLM shared_assets (may add new entries or override file path)
    for asset in shared_assets:
        label = asset.get("label", "")
        if not label:
            continue
        safe_label = label.replace(" ", "-").lower()
        # Ensure .png
        file_path = asset.get("file", f"/assets/images/{safe_label}.png")
        file_path = re.sub(r"\.svg([\"'\s)]|$)", r".png\1", file_path)
        if label not in registry:
            registry[label] = {
                "label":         label,
                "file":          file_path,
                "also_button":   asset.get("also_button", False),
                "button_action": asset.get("button_action"),
                "occurrences":   asset.get("used_in", []),
            }
        else:
            registry[label]["file"] = file_path

    log.info("REACT_PLANNER",
        f"Asset registry: {len(registry)} unique assets: {list(registry.keys())[:12]}"
    )
    return registry


# ─────────────────────────────────────────────────────────────────
# SHARED STATE DERIVER
# ─────────────────────────────────────────────────────────────────

def _derive_shared_state(pages: list[dict]) -> list[dict]:
    """
    For every open_modal action on every page, emit a useState declaration
    so the context builder can emit correct React code.
    """
    shared = []
    seen: set[str] = set()
    for page in pages:
        page_cn = page["component_name"]
        for el in page.get("interactive_elements", []):
            if el.get("action") == "open_modal":
                target = el.get("target", "")
                if not target or target in seen:
                    continue
                seen.add(target)
                state_name = target[0].lower() + target[1:] + "Open"
                shared.append({
                    "name":    state_name,
                    "type":    "boolean",
                    "default": False,
                    "used_in": [page_cn],
                    "opens":   target,
                })
    return shared


# ─────────────────────────────────────────────────────────────────
# CONTEXT BUILDERS  (unchanged API — used by main.py)
# ─────────────────────────────────────────────────────────────────

def build_page_context(page: dict, product_map: dict) -> str:
    """
    Build the instruction block injected into the LLM prompt for a PAGE.
    """
    asset_registry = product_map.get("asset_registry", {})
    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"PAGE: {page['name']}")
    lines.append(f"ROUTE: {page['route']}")
    lines.append(f"DESCRIPTION: {page.get('description', '')}")
    lines.append(f"NAVIGATION FLOW: {product_map.get('navigation_flow', '')}")

    # ── User instructions (from Export Panel per-frame prompt + global prompt) ──
    user_instr = page.get("user_instructions", "")
    if user_instr:
        log.info("REACT_PLANNER",
            f"[FRAME-PROMPT->LLM] Page '{page['name']}' — injecting designer instructions: "
            f"\"{user_instr[:80]}\""
        )
        lines.append("")
        lines.append("ADDITIONAL DESIGNER INSTRUCTIONS (apply on top of exact Figma fidelity):")
        lines.append(f"  {user_instr}")
        lines.append("  NOTE: These instructions add functionality/behaviour but do NOT change")
        lines.append("  the visual design — keep all exact colors, sizes, and positions from Figma.")
        lines.append("")
    else:
        log.debug("REACT_PLANNER",
            f"Page '{page['name']}' — no additional designer instructions"
        )

    lines.append("")

    # All routes
    lines.append("ALL ROUTES (for navigate() calls):")
    for r in product_map.get("routing", []):
        lines.append(f"  {r.get('path','/'):<28} → {r.get('component','')}")
    lines.append("")

    # Components imported/opened by this page
    imports = page.get("imports_components", [])
    if imports:
        lines.append("COMPONENTS USED ON THIS PAGE:")
        for comp_name in imports:
            comp = _find_component(comp_name, product_map)
            if comp:
                lines.append(
                    f"  {comp_name}  ({comp['type']})  "
                    f"→ import from \"../components/{comp_name}\""
                )
        lines.append("")

    # State declarations
    page_cn = page["component_name"]
    modal_states = [
        s for s in product_map.get("shared_state", [])
        if page_cn in s.get("used_in", [])
    ]
    if modal_states:
        lines.append("STATE DECLARATIONS (add at top of component):")
        for s in modal_states:
            setter = "set" + s["name"][0].upper() + s["name"][1:]
            lines.append(
                f"  const [{s['name']}, {setter}] = useState({json.dumps(s['default'])});"
            )
        lines.append("")

    # Interactive elements
    elements = page.get("interactive_elements", [])
    if elements:
        lines.append("INTERACTIVE ELEMENTS — WIRE EVERY ONE (no silent dead buttons):")
        lines.append("")
        for el in elements:
            lines += _format_element_instruction(el, product_map, is_component=False)
        lines.append("")

    # Modal rendering instructions
    if imports:
        lines.append("MODAL RENDERING (add at end of JSX return, BEFORE closing div):")
        for comp_name in imports:
            comp = _find_component(comp_name, product_map)
            if not comp:
                continue
            comp_type = comp.get("type", "modal")
            if comp_type in ("modal", "dialog", "drawer", "bottomsheet", "popover", "toast"):
                state_var = comp_name[0].lower() + comp_name[1:] + "Open"
                lines.append(
                    f"  {{{state_var} && "
                    f"<{comp_name} onClose={{() => set{comp_name}Open(false)}} />}}"
                )
        lines.append("")

    # Asset lookup table
    all_assets = list(asset_registry.values())
    if all_assets:
        lines.append("ASSET FILE LOOKUP TABLE (EXACT paths — all .png, NEVER .svg):")
        for asset in all_assets:
            btn_note = f"  [button: {asset['button_action']}]" if asset.get("also_button") else ""
            lines.append(f"  {asset['label']!r:30} → src=\"{asset['file']}\"{btn_note}")
        lines.append("  ANY @svg or @image node → /assets/images/<label>.png — NEVER .svg")
        lines.append("")

    lines.append("GENERAL RULES FOR THIS PAGE:")
    lines.append("  - DO NOT leave any button with an empty onClick")
    lines.append("  - Text must not overflow its container — use overflow:hidden + ellipsis")
    lines.append("  - Match Figma colors exactly — do not default to black or white")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_component_context(comp: dict, product_map: dict) -> str:
    """
    Build the instruction block injected into the LLM prompt for a COMPONENT.
    """
    asset_registry = product_map.get("asset_registry", {})
    comp_type      = comp.get("type", "modal")
    cn             = comp["component_name"]

    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"COMPONENT: {comp['name']}")
    lines.append(f"TYPE: {comp_type.upper()}")
    lines.append(f"DESCRIPTION: {comp.get('description', '')}")
    lines.append(f"PROPS: {comp.get('props', ['onClose'])}")
    lines.append(f"USED IN PAGES: {comp.get('used_in_pages', [])}")

    # ── User instructions ──────────────────────────────────────────
    user_instr = comp.get("user_instructions", "")
    if user_instr:
        lines.append("")
        lines.append("ADDITIONAL DESIGNER INSTRUCTIONS (apply on top of exact Figma fidelity):")
        lines.append(f"  {user_instr}")
        lines.append("  NOTE: Keep all exact colors, sizes, and positions from Figma.")

    lines.append("")

    lines += _component_type_rules(comp_type, cn)

    # Tabs
    tab_frames = comp.get("tab_frames", [])
    if tab_frames:
        default_tab = comp.get("default_tab") or tab_frames[0]["tab_name"]
        lines.append(f"TABS ({len(tab_frames)} total):")
        lines.append(f'  DEFAULT (useState initial value MUST be): "{default_tab}"')
        for tf in tab_frames:
            marker = " ← OPENS FIRST" if tf["tab_name"] == default_tab else ""
            lines.append(f"  • {tf['tab_name']}{marker}")
        lines.append("")
        lines.append(f'const [activeTab, setActiveTab] = useState("{default_tab}");  // do NOT change this default')
        lines.append("")
        lines.append("RENDER EACH TAB CONTENT from the Figma JSON tree:")
        for tf in tab_frames:
            lines.append(
                f"  {{activeTab === \"{tf['tab_name']}\" && "
                f"(<div>{{/* {tf['tab_name']} content */}}</div>)}}"
            )
        lines.append("")
        lines.append("TAB OVERFLOW RULES:")
        lines.append("  - Each tab panel must have overflow:hidden or overflowY:auto")
        lines.append("")

    # Interactive elements
    elements = comp.get("interactive_elements", [])
    if elements:
        lines.append("INTERACTIVE ELEMENTS — WIRE EVERY ONE:")
        lines.append("")
        for el in elements:
            lines += _format_element_instruction(el, product_map, is_component=True)
        lines.append("")

    # Asset lookup
    all_asset_list = list(asset_registry.values())
    if all_asset_list:
        lines.append("ASSET FILE LOOKUP TABLE (EXACT paths — all .png, NEVER .svg):")
        for asset in all_asset_list:
            btn_note = f"  [button: {asset['button_action']}]" if asset.get("also_button") else ""
            lines.append(f"  {asset['label']!r:30} → src=\"{asset['file']}\"{btn_note}")
        lines.append("")

    # Routes
    lines.append("ALL ROUTES (for navigate() if needed):")
    for r in product_map.get("routing", []):
        lines.append(f"  {r.get('path','/'):<28} → {r.get('component','')}")

    lines.append("")
    lines.append("SIZING + COLOR RULES:")
    lines.append("  - Use exact pixel sizes from the Figma frame JSON")
    lines.append("  - Match Figma colors exactly — do not default to black or white")
    lines.append("  - Scrollbar background must match component background color")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _component_type_rules(comp_type: str, cn: str) -> list[str]:
    """Return rendering rules for a given component type."""
    base = [
        "COMPONENT RENDERING RULES:",
        f"  export default function {cn}({{ onClose, ...props }}) {{",
    ]
    if comp_type in ("modal", "dialog"):
        return base + [
            "  Render a FIXED backdrop: position fixed, inset 0, bg black/60, z-index 50",
            "  Center the modal: display flex, alignItems center, justifyContent center",
            "  Modal box: position relative, borderRadius 16px, overflow hidden",
            "  Clicking the BACKDROP calls onClose()",
            "  The modal box stops propagation: onClick={e => e.stopPropagation()}",
            "  Close/Cancel/× button MUST call onClose()",
            "  DO NOT use React Router inside this component",
            "",
        ]
    elif comp_type == "drawer":
        return base + [
            "  Fixed right panel: position fixed, top 0, right 0, height 100vh",
            "  Dark backdrop on the left calls onClose()",
            "  Add smooth slide-in: transition transform 0.3s ease",
            "",
        ]
    elif comp_type == "bottomsheet":
        return base + [
            "  Fixed bottom panel: position fixed, bottom 0, left 0, right 0",
            "  Dark backdrop above calls onClose()",
            "  Add smooth slide-up: transition transform 0.3s ease",
            "",
        ]
    elif comp_type == "toast":
        return base + [
            "  Fixed bottom-right: position fixed, bottom 24px, right 24px",
            "  Auto-dismiss after 4 seconds using useEffect + setTimeout",
            "  Calling onClose() removes it",
            "",
        ]
    elif comp_type == "sidebar":
        return base + [
            "  Renders as a fixed left panel or an inline block",
            "  Width should match Figma exactly",
            "  Background color MUST match Figma design (do not default to black)",
            "",
        ]
    else:
        return base + [
            "  Self-contained reusable component",
            "  Background color MUST match Figma design exactly",
            "",
        ]


def _format_element_instruction(
    el: dict, product_map: dict, is_component: bool
) -> list[str]:
    """Format one interactive element as code instruction lines."""
    action = el.get("action", "unknown")
    target = el.get("target", "")
    name_  = el.get("element_name", "element")
    btext  = el.get("button_text", "")
    notes  = el.get("notes", "")
    hint   = el.get("position_hint", "")

    display = f'"{name_}" (text: "{btext}")' if btext and btext != name_ else f'"{name_}"'
    result  = []

    if action == "open_modal":
        state_var = (target[0].lower() + target[1:] + "Open") if target else "modalOpen"
        setter    = f"set{target}Open" if target else "setModalOpen"
        # Only emit import if target is a real resolved component name (not missing)
        safe_target = _to_pascal(target) if target else ""
        result += [
            f"  • {display}  →  MUST open modal: {safe_target}",
            f"    onClick: () => {setter}(true)",
            f"    import {safe_target} from '../components/{safe_target}'",
            f"    declare: const [{state_var}, {setter}] = useState(false)",
            f"    render at end of JSX: {{{state_var} && <{safe_target} onClose={{() => {setter}(false)}} />}}",
        ]

    elif action == "close_modal":
        result += [
            f"  • {display}  →  MUST call: onClose()",
            f"    onClick: () => onClose()",
        ]
    elif action == "navigate":
        route = el.get("route") or _find_route(target, product_map)
        result += [
            f"  • {display}  →  navigate(\"{route}\")",
            f"    onClick: () => navigate(\"{route}\")",
        ]
    elif action == "switch_tab":
        result += [
            f"  • {display}  →  switch tab to \"{target}\"",
            f"    onClick: () => setActiveTab(\"{target}\")",
        ]
    elif action == "submit_form":
        handler = f"handle{_to_pascal(name_.replace(' ', ''))}"
        result += [
            f"  • {display}  →  submit form",
            f"    onClick: () => {handler}()",
            f"    implement {handler} = () => {{ /* validate + submit */ }}",
        ]
    elif action == "back":
        result += [
            f"  • {display}  →  go back",
            f"    onClick: () => navigate(-1)",
        ]
    elif action == "logout":
        result += [
            f"  • {display}  →  logout",
            f"    onClick: () => {{ /* clear auth */ navigate('/') }}",
        ]
    elif action == "search":
        result += [
            f"  • {display}  →  search",
            f"    onChange/onClick: trigger search handler",
        ]
    elif action == "toggle":
        result += [
            f"  • {display}  →  toggle state",
            f"    onChange: (e) => setToggleState(e.target.checked)",
        ]
    elif action == "expand":
        result += [
            f"  • {display}  →  expand/collapse",
            f"    onClick: () => setExpanded(prev => !prev)",
        ]
    elif action == "file_pick":
        result += [
            f"  • {display}  →  open file picker",
            f"    onClick: () => document.getElementById('fileInput').click()",
            f"    add: <input type='file' id='fileInput' style={{{{display:'none'}}}} onChange={{handleFileChange}} />",
        ]
    elif action == "minimize":
        restore = (
            "CORRECT pattern:\n"
            "  {minimized && (\n"
            "    <div className='w-10 flex-col items-center py-3 border-r shrink-0 flex'>\n"
            "      <button onClick={()=>setMinimized(false)} title='Expand'>&#9654;</button>\n"
            "    </div>\n"
            "  )}\n"
            "  {!minimized && (\n"
            "    <aside> ... panel content with minimize button ... </aside>\n"
            "  )}\n"
            "WRONG: style={{display: minimized ? 'none' : 'block'}} — hides restore too"
        )
        result += [
            f"  • {display}  →  minimize/collapse parent panel",
            "    const [minimized, setMinimized] = useState(false)",
            "    minimize button: onClick={()=>setMinimized(true)}",
            "    CRITICAL: ALWAYS render a restore bar outside the collapsing panel.",
            restore,
        ]
    elif action == "missing_target":
        result += [
            f"  • {display}  →  target NOT IN DESIGN: \"{target}\"",
            f"    onClick: () => alert('Page/Component \"{target}\" not added in design yet')",
        ]
    else:
        handler = f"handle{_to_pascal(name_.replace(' ', ''))}"
        note_str = notes or hint or "no further info"
        result += [
            f"  • {display}  →  unknown action ({note_str})",
            f"    onClick: () => {{ {handler}(); console.log('{name_} clicked'); }}",
            f"    implement {handler} = () => {{ /* TODO */ }}",
        ]

    return result


# ─────────────────────────────────────────────────────────────────
# FRAME RESOLVERS  (unchanged API — used by main.py)
# ─────────────────────────────────────────────────────────────────

def resolve_page_frame(page: dict, all_frames: list[dict]) -> dict | None:
    """Find the Figma frame for a given page entry."""
    frame_name = page.get("frame_name") or page.get("name", "")
    lookup     = {f.get("name", ""): f for f in all_frames}

    if frame_name in lookup:
        return lookup[frame_name]
    for fname, fdata in lookup.items():
        if fname.lower() == frame_name.lower():
            return fdata
    norm = frame_name.replace("/", " ").strip()
    for fname, fdata in lookup.items():
        if fname.replace("/", " ").strip().lower() == norm.lower():
            return fdata
    return _fuzzy_match(frame_name, all_frames)


def resolve_component_frames(comp: dict, all_frames: list[dict]) -> dict:
    """Find master + tab frames for a component."""
    master_name = comp.get("master_frame", comp.get("name", ""))
    tab_frames  = comp.get("tab_frames", [])
    lookup      = {_normalise(f.get("name", "")): f for f in all_frames}

    master_data = (
        lookup.get(_normalise(master_name))
        or _fuzzy_match(master_name, all_frames)
    )
    if not master_data:
        comp_lower = comp["name"].lower()
        for f in all_frames:
            if f.get("name", "").lower().startswith(comp_lower):
                master_data = f
                break

    tab_data: dict[str, dict] = {}
    for tf in tab_frames:
        tab_name   = tf["tab_name"]
        frame_name = tf["frame_name"]
        found = (
            lookup.get(_normalise(frame_name))
            or _fuzzy_match(frame_name, all_frames)
        )
        if found:
            tab_data[tab_name] = found
        else:
            log.warn("REACT_PLANNER",
                f"Tab frame {frame_name!r} not found for {comp['name']!r}"
            )

    return {"master": master_data, "tabs": tab_data}


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _parse_json_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find("{")
    end     = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.error("REACT_PLANNER", f"JSON parse failed: {e}")
        log.debug("REACT_PLANNER", f"Raw snippet: {raw[:600]}")
        return {}


def _find_component(name: str, product_map: dict) -> dict | None:
    for c in product_map.get("components", []):
        if c.get("component_name") == name or c.get("name") == name:
            return c
    return None


def _find_route(target: str, product_map: dict) -> str:
    t = target.lower()
    for r in product_map.get("routing", []):
        if r.get("component", "").lower() == t:
            return r["path"]
    for p in product_map.get("pages", []):
        if p["component_name"].lower() == t or p["name"].lower() == t:
            return p["route"]
    return "/"


def _fuzzy_match(name: str, frames: list[dict]) -> dict | None:
    name_words = set(w for w in re.split(r"[\s/\-_]+", name.lower()) if len(w) > 2)
    best_score, best_frame = 0, None
    for f in frames:
        fw = set(w for w in re.split(r"[\s/\-_]+", f.get("name", "").lower()) if len(w) > 2)
        score = len(name_words & fw)
        if score > best_score:
            best_score, best_frame = score, f
    return best_frame if best_score > 0 else None


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _to_pascal(name: str) -> str:
    cleaned = re.sub(r"[^\w\s\-]", " ", name)
    return "".join(
        w.capitalize()
        for w in re.split(r"[\s\-_/]+", cleaned.strip())
        if w and re.match(r"\w", w[0])
    ) or "Component"


def _to_slug(name: str) -> str:
    return re.sub(r"-+", "-",
        re.sub(r"[\s_/]+", "-",
               re.sub(r"[^\w\s\-]", "", name.lower()).strip()
        ).strip("-")
    ) or "page"


def _to_camel(name: str) -> str:
    p = _to_pascal(name)
    return p[0].lower() + p[1:] if p else name