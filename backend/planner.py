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
import logger as log

load_dotenv()

planner_model = os.getenv("GEMINI_PLANNER_MODEL")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY1"))

# STATE_EXTRACTOR_PROMPT = """
# You are a senior UI/UX architect and product designer for an AI-powered Figma website generator.

# You must be make a figma with buttons where the buttons are need not a Frame or just text 
# You will get the input Via three different way at a same time or sometimes single way based on the user wish.

# Input Types:
#   1) Pdf and Txt file contents.
#   2) user prompt in chat input field.
#   3) Image Reference.

# Example 1:
#     User will provide the information about the generation guide directly via chat filed.
#     based on the user input we can generate the Plan for the figma making.
# Example 2:
#     User will provide the pdf content with full figma design guide and idea, we must make a plan based on that text or pdf files content.
# Example 3:
#     User will attach the screenshot design already existing website templates for sample and at a same time user will provide the details about the figma what actualy thay want i mean what kind of they going making to production level
    
#                                 User Input
#                                     |
#             -------------------------------------------------
#             |                       |                       |
#     Prompt for figma        Pdf or txt file       screen shot reference
#        requirement                  |                       |
#             |                       |                       |
#             -------------------------------------------------
#                                     |
#                       -----------------------------------
#                       |planning for the Figma generation |
#                       ------------------------------------
  
# Figma Tree analayzing process:
#   User will provide the Tree structure about the Figma generation.
#   what is the main intent of the and project structure directoly user will provide those kind of information also.
#   based on that we can make a plan easily what are the pages actualy we need like that 
  
# Example struture:

# CRM Call Management System
# │
# ├── Global Layout
# │ ├── Sidebar
# │ │ ├── Dashboard
# │ │ ├── Calls
# │ │ ├── Voicemail
# │ │ └── Notes
# │ │
# │ ├── Topbar
# │ │ ├── Search
# │ │ ├── Notifications
# │ │ └── User Profile
# │ │
# │ └── Main Content Area
# │ └── Workflow Cards (Feature Flows)
# │
# ├── Dashboard (Inbound & Outbound Overview)
# │ ├── Call List
# │ │ ├── Call Item
# │ │ │ ├── Status (Incoming / Ongoing / Missed)
# │ │ │ ├── Actions
# │ │ │ │ ├── Answer / Call
# │ │ │ │ ├── View Details
# │ │ │ │ ├── Notes
# │ │ │ │ ├── Transfer
# │ │ │ │ └── Hold
# │
# ├── Notes Module (Interaction Flow)
# │ ├── Default State
# │ │ └── "View Notes" Button
# │ │
# │ ├── Action: Click "View Notes"
# │ │ └── Opens Notes Panel
# │ │
# │ ├── Notes Panel
# │ │ ├── Notes List
# │ │ │ └── Note Item
# │ │ │ ├── Content
# │ │ │ ├── Edit Button
# │ │ │ └── Delete Button
# │ │
# │ ├── Edit Flow
# │ │ ├── Action: Click Edit
# │ │ ├── State: Editable Input Field
# │ │ ├── Actions:
# │ │ │ ├── Save → Update Note
# │ │ │ └── Cancel → Revert State
# │ │
# │ ├── Delete Flow
# │ │ ├── Action: Click Delete
# │ │ ├── State: Confirmation Modal
# │ │ ├── Modal Actions:
# │ │ │ ├── Confirm → Delete Note
# │ │ │ └── Cancel → Close Modal
# │
# ├── Voicemail Module
# │ ├── Voicemail List
# │ ├── Action: Select Voicemail
# │ │ └── Opens Voicemail Detail
# │ │
# │ ├── Voicemail Detail
# │ │ ├── Audio Player
# │ │ ├── Transcript Panel
# │ │ └── Actions
# │ │ ├── Callback
# │ │ └── Add Note
# │
# ├── Missed Call Flow
# │ ├── Missed Call Item
# │ ├── Action: Click Item
# │ │ └── Opens Call Detail Panel
# │ │
# │ └── Actions
# │ └── Callback
# │
# ├── Transcript Module
# │ ├── Action: Click "View Transcript"
# │ │ └── Opens Transcript Panel
# │ │
# │ └── Transcript View
# │ └── Scrollable Conversation UI
# │
# ├── Outbound Call Flow
# │ ├── Action: Click "Call"
# │ │ └── Opens Dialer Modal
# │ │
# │ ├── Dialing State
# │ │ └── Ringing Indicator
# │ │
# │ ├── Active Call State
# │ │ └── Call Controls
# │ │ ├── Mute
# │ │ ├── Hold
# │ │ ├── Transfer
# │ │ └── End Call
# │
# ├── Inbound Call Flow
# │ ├── Incoming Call Popup
# │ │ ├── Accept
# │ │ └── Reject
# │ │
# │ ├── Accept Action
# │ │ └── Active Call UI
# │ │
# │ └── Reject Action
# │ └── Call Ends / Moves to Missed
# │
# ├── Call Transfer Flow
# │ ├── Action: Click "Transfer"
# │ │ └── Opens Transfer Modal
# │ │
# │ ├── Transfer Modal
# │ │ ├── Search User / Queue
# │ │ └── Confirm Transfer
# │ │
# │ └── State: انتقال in progress (Transfer State)
# │
# ├── Receiving Transfer Flow
# │ ├── Incoming Transfer Popup
# │ │ ├── Accept
# │ │ └── Reject
# │
# ├── Call Hold Flow
# │ ├── Action: Click "Hold"
# │ │ └── State: On Hold
# │ │
# │ ├── On Hold State
# │ │ └── Resume Button
# │ │
# │ └── Action: Click "Resume"
# │ └── Back to Active Call
# │
# └── UI Components (Design System)
#  ├── Cards
#  ├── Modals
#  ├── Buttons
#  ├── Input Fields
#  ├── Lists
#  ├── Popups
#  └── Status Indicator
 
# Design a complete CRM Call Management UI system that represents full user workflows, 
# interaction logic, and UI state transitions in a modern SaaS design system.
# � THEME & STYLE (STRICT REQUIREMENT):
# • Use a modern SaaS LIGHT THEME
# • Primary color: Soft blue or purple accent
# • Background: Light gray or off-white (#F7F9FC style)
# • Cards: White with subtle shadow and 8–12px border radius
# • Typography: Clean sans-serif (Inter / SF Pro style)
# • Buttons: Rounded, minimal, with clear hover states
# • Icons: Thin, modern (Feather / Lucide style)
# • Maintain consistent spacing, padding, and grid alignment
# • Use soft dividers, not heavy borders
# � GLOBAL LAYOUT:
# • Left sidebar navigation (Dashboard, Calls, Voicemail, Notes)
# • Topbar with search, profile, and notifications
# • Main content area showing modular workflow cards
# • Each feature must be displayed as a sequence of UI states (left → right flow)
# ⚠ CORE REQUIREMENT:
# For EVERY feature, explicitly show:
# 1 Default UI state
# 2 User action (button click)
# 3 Resulting UI change
# 4 Modal / popup / transition state
# This is NOT a static UI — it must visualize interaction behavior.
# � FEATURE FLOWS:
# 1 INBOUND & OUTBOUND DASHBOARD
# • Call list with statuses (incoming, ongoing, missed)
# • Each call item includes:
# → Call / Answer
# → View Details
# → Notes
# → Transfer
# → Hold
# 2 NOTES INTERACTION (DETAILED)
# • User clicks "View Notes"
# → Opens Notes Card / Side Panel
# • Notes Card:
# → List of notes
# → Each note has:
# ◦ Edit button
# ◦ Delete button
# • Edit Flow:
# → Clicking "Edit" converts note into editable input
# → Show Save / Cancel buttons
# → Save updates note in UI
# • Delete Flow:
# → Clicking "Delete" opens confirmation modal
# → Modal contains:
# ◦ Warning message
# ◦ Confirm button (destructive style)
# ◦ Cancel button
# → Confirm → note removed
# → Cancel → modal closes
# 3 VOICEMAIL INTERACTION
# • Voicemail list
# • Clicking item opens detail card
# • Show:
# → Audio player UI
# → Transcript panel
# → Callback button
# → Add Note action
# • Show:
# → Audio player UI
# → Transcript panel
# → Callback button
# → Add Note action
# 4 MISSED CALL FLOW
# • Missed call appears in list
# • Clicking opens call detail panel
# • Show callback action
# 5 TRANSCRIPT VIEW
# • Clicking "View Transcript"
# → Opens panel with structured conversation UI
# → Scrollable content
# 6 OUTBOUND CALL FLOW
# • Click "Call"
# → Opens dialer modal
# • Dialing state:
# → Ringing indicator
# • Active call UI:
# → Controls:
# ◦ Mute
# ◦ Hold
# ◦ Transfer
# ◦ End Call
# 7 INBOUND CALL FLOW
# • Incoming call popup
# • Buttons:
# → Accept
# → Reject
# • Accept:
# → Opens active call UI
# • Reject:
# → Ends call / marks missed
# 8 CALL TRANSFER
# • Clicking "Transfer"
# → Opens modal
# • Modal includes:
# → Search/select user or queue
# → Confirm transfer button
# • On confirm:
# → Transition state shown
# 9 RECEIVING TRANSFER
# • Incoming transfer popup
# • Accept / Reject actions
# 10 CALL HOLD
# • Clicking "Hold"
# → UI switches to "On Hold" state
# → Show Resume button
# • Clicking "Resume"
# → Returns to active call UI
# � DESIGN SYSTEM DETAILS:
# • Use reusable components (cards, modals, buttons, lists)
# • Show hover, active, and disabled states
# • Use clear visual hierarchy
# • Maintain consistent spacing (8px grid system)
# • Group flows into labeled sections (Notes, Calls, Transfer, etc.)
# � GOAL:
# Generate a full UX workflow board that clearly demonstrates:
# • What each button does
# • How the UI changes after each action
# • All interaction states (before → action → after)
# This should look like a professional product design presentation, not just a collection of 
# screens.
# � System Summary
# This design represents a CRM Call Management System that handles the complete 
# lifecycle of calls — from incoming/outgoing interactions to post-call activities like notes, 
# transcripts, and voicemail.
# It is structured as an interaction-driven workflow system, where every user action (click, 
# accept, delete, transfer, etc.) triggers a clear UI response and state change.
# ⚙ Core Capabilities
# • Call Handling
# ◦ Supports both inbound and outbound calls
# ◦ Includes full call lifecycle: incoming → active → hold → transfer → end
# • Real-Time Call Actions
# ◦ Answer / Reject calls
# ◦ Hold and resume calls
# ◦ Transfer calls between users or queues
# ◦ Mute and end call controls
# • Post-Call & Support Features
# ◦ Notes management (create, edit, delete with confirmation)
# ◦ Voicemail handling with playback and transcript
# ◦ Missed call tracking with quick callback
# ◦ Full conversation transcript viewing
# � Interaction Philosophy
# • Every feature follows:
# Default State → User Action → UI Transition → Resulting State
# • Examples:
# ◦ Clicking Notes → opens notes panel
# ◦ Clicking Delete → opens confirmation modal → confirms → removes note
# ◦ Clicking Hold → switches call to hold state → shows resume option
# This ensures predictable and traceable user behavior.
# � Structural Approach
# The system is divided into:
# 1 Call Types – inbound, outbound
# 2 Call States – ringing, active, hold, missed
# 3 User Actions – call, transfer, hold, notes
# 3 User Actions – call, transfer, hold, notes
# 4 Support Modules – voicemail, transcript, notes
# This separation prevents chaos and keeps the system scalable.
# � Design System
# • Modern SaaS light theme
# • Card-based modular layout
# • Clear visual hierarchy
# • Consistent spacing and reusable components
# • Use of modals, panels, and popups for interactions
# � Final Goal
# To create a complete UX workflow board that:
# • Shows what every button does
# • Clearly visualizes all interaction states
# • Demonstrates how the UI evolves step-by-step
# • Acts as a bridge between design, development, and system logi

# Example ended.
# ===================================================================================
# You are a UI/UX analyst. You receive a content context from a requirements document.

# Extract EVERY distinct UI state that needs its own Figma frame.
# One frame per UI moment: default view, modal open, after action, error state, popup, panel.
# Name each state: "Feature — State Description"
# Cover every interaction: button clicks, modals, panels, transitions, popups, result states.
# Do NOT merge states — keep them separate.

# Output ONLY a JSON array:
# [
#   {
#     "id": "state_1",
#     "name": "Dashboard — Default State",
#     "feature_group": "Dashboard",
#     "ui_state": "default",
#     "description": "Main dashboard showing call list with incoming, ongoing, missed statuses",
#     "components": ["sidebar nav", "topbar", "call list", "status badges", "action buttons"],
#     "height": 1080
#   }
# ]

# CONTENT CONTEXT:
# {content_context}

# Output ONLY the JSON array. No explanation.
# """

# FREE_PLANNER_PROMPT = """
# You are a senior UI/UX architect for an AI-powered Figma generator.

# Generate one frame per major screen or interaction state.

# CRITICAL RULES:
# 1. Output ONLY valid JSON. No markdown, no explanations.
# 2. Frame width ALWAYS 1440px.
# 3. Height: 900-1080px for app screens, 1800-3200px for landing pages.
# 4. For every major button or action, add a separate frame showing the result state.

# OUTPUT FORMAT:
# {
#   "project_title": "string",
#   "website_goal": "string",
#   "total_pages": number,
#   "pages": [
#     {
#       "id": "page1",
#       "name": "Dashboard — Default",
#       "description": "Main dashboard showing call list",
#       "width": 1440,
#       "height": 1080,
#       "sections": [
#         {
#           "section_name": "Call List",
#           "purpose": "Show active and recent calls",
#           "components": ["sidebar", "topbar", "call items", "status badges"]
#         }
#       ],
#       "images": []
#     }
#   ]
# }

# USER REQUEST:
# {user_prompt}
# """

STATE_EXTRACTOR_PROMPT = """
You are a UI/UX analyst. You receive a content context from a requirements document.

Extract EVERY distinct UI state that needs its own Figma frame.
One frame per UI moment: default view, modal open, after action, error state, popup, panel.
Name each state: "Feature — State Description"
Cover every interaction: button clicks, modals, panels, transitions, popups, result states.
Do NOT merge states — keep them separate.

Output ONLY a JSON array:
[
  {
    "id": "state_1",
    "name": "Dashboard — Default State",
    "feature_group": "Dashboard",
    "ui_state": "default",
    "description": "Main dashboard showing call list with incoming, ongoing, missed statuses",
    "components": ["sidebar nav", "topbar", "call list", "status badges", "action buttons"],
    "height": 1080
  }
]

CONTENT CONTEXT:
{content_context}

Output ONLY the JSON array. No explanation.
"""

FREE_PLANNER_PROMPT = """
You are a senior UI/UX architect for an AI-powered Figma generator.

Generate one frame per major screen or interaction state.

CRITICAL RULES:
1. Output ONLY valid JSON. No markdown, no explanations.
2. Frame width ALWAYS 1440px.
3. Height: 900-1080px for app screens, 1800-3200px for landing pages.
4. For every major button or action, add a separate frame showing the result state.

OUTPUT FORMAT:
{
  "project_title": "string",
  "website_goal": "string",
  "total_pages": number,
  "pages": [
    {
      "id": "page1",
      "name": "Dashboard — Default",
      "description": "Main dashboard showing call list",
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

def parse_plan(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start = cleaned.find('{')
    end   = cleaned.rfind('}')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\n\nRaw:\n{raw[:800]}")

    pages = data.get("pages", [])
    for i, page in enumerate(pages):
        if "id" not in page:
            page["id"] = f"frame{i+1}"
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
        log.warn("PLANNER", "State extraction JSON failed — falling back to free mode")
        return []


def _build_plan_from_states(ui_states: list, user_prompt: str) -> dict:
    pages = []
    for i, state in enumerate(ui_states):
        pages.append({
            "id":            state.get("id", f"frame{i+1}"),
            "name":          state.get("name", f"Frame {i+1}"),
            "description":   state.get("description", ""),
            "ui_state":      state.get("ui_state", "default"),
            "feature_group": state.get("feature_group", ""),
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

async def run_planner(user_prompt: str, content_context: dict = None) -> dict:
    log.info("PLANNER", f"Starting — prompt: {user_prompt[:80]!r}")

    has_content = bool(
        content_context and (
            content_context.get("features") or
            content_context.get("key_workflows") or
            content_context.get("pages_or_screens")
        )
    )

    if has_content:
        log.info("PLANNER", "STRUCTURED MODE — extracting UI states from document")
        context_str      = json.dumps(content_context, indent=2)
        extractor_prompt = STATE_EXTRACTOR_PROMPT.replace("{content_context}", context_str[:6000])

        def _call_extractor():
            return client.models.generate_content(
                model=planner_model,
                contents=extractor_prompt,
                config={"temperature": 0.2},
            )

        ext_response = await asyncio.to_thread(_call_extractor)
        ui_states    = _parse_state_list(ext_response.text)

        if ui_states:
            log.info("PLANNER", f"Extracted {len(ui_states)} UI states")
            for s in ui_states:
                log.info("PLANNER", f"  → {s.get('name','?')}")
            parsed = _build_plan_from_states(ui_states, user_prompt)
            log.success("PLANNER",
                f"Plan ready — {parsed['project_title']!r}  frames={parsed['total_pages']}"
            )
            return parsed
        else:
            log.warn("PLANNER", "State extraction empty — falling back to free mode")

    # FREE MODE
    log.info("PLANNER", "FREE MODE — generic plan from prompt")
    full_prompt = FREE_PLANNER_PROMPT.replace("{user_prompt}", user_prompt)

    def _call_free():
        return client.models.generate_content(
            model=planner_model,
            contents=full_prompt,
            config={"temperature": 0.3},
        )

    response = await asyncio.to_thread(_call_free)
    raw_text  = response.text
    log.debug("PLANNER", f"Raw response: {len(raw_text)} chars")

    parsed = parse_plan(raw_text)
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