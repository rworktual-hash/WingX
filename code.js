// code.js — Figma Plugin Sandbox
// IMPORTANT: This sandbox cannot make network requests directly.
// All fetch() calls happen in ui.html, which sends results back via postMessage.
//
// Message flow:
  // code.js  →  ui.html : { type: 'generate', prompt }
  // ui.html  →  code.js : { type: 'page_start', ... }
  // ui.html  →  code.js : { type: 'page_chunk', page_id, children: [...] }
  // ui.html  →  code.js : { type: 'page_end', page_id, page_name, ... }
  // ui.html  →  code.js : { type: 'complete', ... }
  // ui.html  →  code.js : { type: 'status', message }
  // ui.html  →  code.js : { type: 'error', message }

  
var PLUGIN_UI_DEFAULT_WIDTH = 600;
var PLUGIN_UI_DEFAULT_HEIGHT = 800;
var PLUGIN_UI_MINIMIZED_WIDTH = 70;
var PLUGIN_UI_MINIMIZED_HEIGHT = 70;

function positionPluginTopRight(width, height) {
  try {
    if (!figma.ui.reposition || !figma.viewport || !figma.viewport.bounds) return;
    var bounds = figma.viewport.bounds;
    var margin = 24;
    var x = Math.round(bounds.x + bounds.width - width - margin);
    var y = Math.round(bounds.y + margin);
    figma.ui.reposition(x, y);
  } catch (err) {
    console.warn("[UI_REPOSITION] Failed:", err && err.message ? err.message : err);
  }
}

figma.showUI(__html__, { width: PLUGIN_UI_DEFAULT_WIDTH, height: PLUGIN_UI_DEFAULT_HEIGHT });

function collectSelectedFramesForExport() {
  var sel = figma.currentPage.selection || [];
  var frames = [];
  var seenFrameIds = {};

  for (var si = 0; si < sel.length; si++) {
    var picked = sel[si];
    var topFrame = null;
    var cur = picked;

    while (cur) {
      if (cur.type === "FRAME" && cur.parent && cur.parent.type === "PAGE") {
        topFrame = cur;
        break;
      }
      cur = cur.parent;
    }

    var target = topFrame;
    if (!target || seenFrameIds[target.id]) continue;
    seenFrameIds[target.id] = true;

    var parsed = parseFrameName(target.name);
    var compType = parsed.compType;
    var clickable = [];
    collectClickableNodes(target, clickable, target);

    frames.push({
      id:             target.id,
      name:           parsed.cleanName,
      rawName:        target.name,
      compType:       compType || null,
      width:          Math.round(target.width  || 0),
      height:         Math.round(target.height || 0),
      clickableNodes: clickable,
    });
  }

  return frames;
}

// ── Selection tracking ────────────────────────────────────────────
// When export panel is active we send full node details so the panel
// can show the selected element and its parent frame.
figma.on("selectionchange", function() {
  var sel = figma.currentPage.selection;

  // Basic count update (always sent)
  figma.ui.postMessage({
    type:  "selection_update",
    count: sel.length,
  });

  if (sel.length === 0) return;

  // ── Add-frame-picking mode: user is selecting a frame to add ─
  if (isPickingAddFrame && sel.length > 0) {
    var frames = collectSelectedFramesForExport();

    if (frames.length > 0) {
      console.log("[ADD_FRAME_PICK] Picked " + frames.length + " frame(s)");
      figma.ui.postMessage({
        type:   "add_frame_picked",
        frame:  frames[0],
        frames: frames,
      });
    }

    isPickingAddFrame = false;
    return; // don't fire selection_detail in same tick
  }

  // ── Link-picking mode: user is selecting a target ────────────
  // First check this BEFORE sending selection_detail so ui.html
  // handles the pick rather than treating it as a normal selection.
  if (isPickingLinkTarget && sel.length > 0) {
    var picked = sel[0];
    // Walk up to the nearest top-level FRAME
    var pickedFrame = null;
    var cur = picked;
    while (cur) {
      if (cur.type === "FRAME" && cur.parent && cur.parent.type === "PAGE") {
        pickedFrame = cur;
        break;
      }
      cur = cur.parent;
    }

    figma.ui.postMessage({
      type:            "link_target_picked",
      sourceNodeId:    linkPickingSourceNodeId,
      targetNodeId:    picked.id,
      targetNodeName:  picked.name,
      targetNodeType:  picked.type,
      targetFrameId:   pickedFrame ? pickedFrame.id   : picked.id,
      targetFrameName: pickedFrame ? parseFrameName(pickedFrame.name).cleanName : parseFrameName(picked.name).cleanName,
    });

    // Exit picking mode automatically
    isPickingLinkTarget     = false;
    linkPickingSourceNodeId = null;
    return; // don't also fire selection_detail in same tick
  }

  // ── Normal selection detail for export panel ─────────────────
  var details = [];
  for (var i = 0; i < sel.length; i++) {
    var node = sel[i];
    var parentFrame = null;
    var walker = node;
    while (walker) {
      if (walker.type === "FRAME" && walker.parent && walker.parent.type === "PAGE") {
        parentFrame = walker;
        break;
      }
      walker = walker.parent;
    }
    details.push({
      nodeId:          node.id,
      nodeName:        node.name,
      nodeType:        node.type,
      width:           Math.round(node.width  || 0),
      height:          Math.round(node.height || 0),
      parentFrameId:   parentFrame ? parentFrame.id   : null,
      parentFrameName: parentFrame ? parseFrameName(parentFrame.name).cleanName : null,
    });
  }
  figma.ui.postMessage({
    type:  "selection_detail",
    nodes: details,
    count: sel.length,
  });
});

// ── Init ────────────────────────────────────────────────────
// ──────
figma.ui.postMessage({
  type: "init",
  backendUrl: "https://wingx-2vpp.onrender.com",
  selectionCount: figma.currentPage.selection.length,
});

// ── Page buffer: reassembles chunked pages ────────────────────────
var pageBuffers = {};
var groupRegistry  = {};   // featureGroup → { bgFrame, nextX, y, frames[] }
var columnRegistry = {};   // columnGroup → { xStart, nextY, maxWidth, label }
var adjacentRegistry = {}; // sourceFrameId → { nextX }
var groupCurrentY  = 0;    // current Y position for next group row
var columnBaseY    = 0;    // base Y for explicit column layouts
var projectBaseX   = 0;    // left edge for the current generated project
var projectBaseY   = 0;    // top edge for the current generated project
var orderedPageIds = [];    // page_ids in the order they were started
var renderQueue    = [];   // frames waiting to be rendered in order
var isRendering    = false; // lock — only one renderFrameInGroup at a time
var componentMasterRegistry = {}; // componentKey -> ComponentNode
var componentLibraryPageRef = null;
var componentLibraryStartX = 80;
var componentLibraryNextX = 80;
var componentLibraryNextY = 80;
var componentLibraryRowH = 0;
var currentProjectComponentScope = "";
var currentProjectStyleGroupLabel = "Project";
var paintStyleRegistry = {};
var localPaintStylesByName = {};
var localPaintStylesPromise = null;
var renderCanvasPageRef = figma.currentPage;
var WORKTUAL_DESIGN_PAGE_NAME = "Worktual AI Design";
var WORKTUAL_COMPONENTS_PAGE_NAME = "Worktual AI Components";
var generationPagesReadyPromise = null;
var pendingGenerationMessages = [];
var isFlushingGenerationMessages = false;

function getOrCreatePageByName(pageName) {
  var pages = figma.root.children || [];
  for (var i = 0; i < pages.length; i++) {
    if (pages[i].type === "PAGE" && pages[i].name === pageName) {
      return pages[i];
    }
  }

  var page = figma.createPage();
  page.name = pageName;
  if (page.parent !== figma.root) {
    figma.root.appendChild(page);
  }
  return page;
}

function getWorktualDesignPage() {
  return getOrCreatePageByName(WORKTUAL_DESIGN_PAGE_NAME);
}

function getWorktualComponentsPage() {
  return getOrCreatePageByName(WORKTUAL_COMPONENTS_PAGE_NAME);
}

function ensureGenerationPagesReady() {
  if (!generationPagesReadyPromise) {
    if (figma.loadAllPagesAsync) {
      generationPagesReadyPromise = figma.loadAllPagesAsync().then(function() {
        renderCanvasPageRef = getWorktualDesignPage();
        componentLibraryPageRef = getWorktualComponentsPage();
      });
    } else {
      generationPagesReadyPromise = Promise.all([
        getWorktualDesignPage().loadAsync(),
        getWorktualComponentsPage().loadAsync(),
      ]).then(function() {
        renderCanvasPageRef = getWorktualDesignPage();
        componentLibraryPageRef = getWorktualComponentsPage();
      });
    }
  }
  return generationPagesReadyPromise;
}

function flushPendingGenerationMessages() {
  if (isFlushingGenerationMessages) return;
  isFlushingGenerationMessages = true;
  try {
    while (pendingGenerationMessages.length > 0) {
      var msg = pendingGenerationMessages.shift();
      switch (msg.type) {
        case "page_start":
          handlePageStart(msg);
          break;
        case "page_chunk":
          handlePageChunk(msg);
          break;
        case "page_end":
          handlePageEnd(msg);
          break;
        case "complete":
          console.log("[COMPLETE]", msg.message || "All pages done");
          var allNodes = getRenderCanvasPage().children.filter(function(n) {
            return !isComponentLibraryNode(n) && (n.type === "FRAME" || n.type === "RECTANGLE");
          });
          if (allNodes.length > 0) figma.viewport.scrollAndZoomIntoView(allNodes);
          break;
      }
    }
  } finally {
    isFlushingGenerationMessages = false;
  }
}

function queueGenerationMessage(msg) {
  pendingGenerationMessages.push(msg);
  ensureGenerationPagesReady().then(function() {
    flushPendingGenerationMessages();
  }).catch(function(err) {
    console.error("[PAGE_LOAD]", err && err.message ? err.message : err);
  });
}

function getRenderCanvasPage() {
  if (renderCanvasPageRef && !renderCanvasPageRef.removed) return renderCanvasPageRef;
  renderCanvasPageRef = getWorktualDesignPage();
  return renderCanvasPageRef;
}

function appendToRenderCanvas(node) {
  var page = getRenderCanvasPage();
  if (node && page) page.appendChild(node);
  return node;
}

function isComponentLibraryNode(node) {
  if (!node || node.removed) return false;
  if (node.type === "COMPONENT") return true;
  if (!node.getPluginData) return false;
  return !!node.getPluginData("wt_component_key");
}

function refreshLocalPaintStylesCache() {
  if (!figma.getLocalPaintStylesAsync) {
    localPaintStylesByName = {};
    return Promise.resolve(localPaintStylesByName);
  }

  return figma.getLocalPaintStylesAsync().then(function(styles) {
    var byName = {};
    var safeStyles = styles || [];
    for (var i = 0; i < safeStyles.length; i++) {
      var style = safeStyles[i];
      if (style && style.name && !style.removed) {
        byName[style.name] = style;
      }
    }
    localPaintStylesByName = byName;
    return byName;
  }).catch(function(err) {
    console.warn("[STYLE_CACHE] Failed to load local paint styles:", err && err.message ? err.message : err);
    localPaintStylesByName = {};
    return localPaintStylesByName;
  });
}

function ensureLocalPaintStylesReady() {
  if (!localPaintStylesPromise) {
    localPaintStylesPromise = refreshLocalPaintStylesCache().finally(function() {
      localPaintStylesPromise = null;
    });
  }
  return localPaintStylesPromise;
}

function getCanvasBounds() {
  var page = getRenderCanvasPage();
  var maxBottom = 0;
  var maxRight = 0;
  var hasNodes = false;
  for (var i = 0; i < page.children.length; i++) {
    var node = page.children[i];
    if (isComponentLibraryNode(node)) continue;
    var y = node.y || 0;
    var h = node.height || 0;
    var x = node.x || 0;
    var w = node.width || 0;
    hasNodes = true;
    maxBottom = Math.max(maxBottom, y + h);
    maxRight = Math.max(maxRight, x + w);
  }
  return {
    hasNodes: hasNodes,
    maxBottom: maxBottom,
    maxRight: maxRight,
  };
}

function normalizeComponentScope(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9/_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^[-/]+|[-/]+$/g, "");
}

function deriveProjectComponentScope(msg) {
  var flowMeta = msg && msg.flow_meta ? msg.flow_meta : {};
  var projectNavigation = flowMeta && flowMeta.project_navigation ? flowMeta.project_navigation : {};
  var navigation = flowMeta && flowMeta.navigation ? flowMeta.navigation : {};
  var parts = [];
  var projectTitle = normalizeComponentScope((msg && msg.project_title) || (flowMeta && flowMeta.project_title) || "");
  var brandName = normalizeComponentScope(
    (projectNavigation && projectNavigation.brand_name) ||
    (navigation && navigation.brand_name) ||
    ""
  );

  if (projectTitle) parts.push("project-" + projectTitle);
  if (brandName) parts.push("brand-" + brandName);

  if (parts.length) {
    return parts.join("__");
  }

  var fallback = normalizeComponentScope(currentProjectComponentScope || (msg && msg.page_name) || "");
  if (fallback) {
    return fallback;
  }
  return "";
}

function getNodeComponentScope(node) {
  if (!node || !node.getPluginData) return "";
  return normalizeComponentScope(node.getPluginData("wt_component_scope"));
}

function normalizeStyleGroupLabel(value) {
  var raw = String(value || "").replace(/[\\/]+/g, " - ").replace(/\s+/g, " ").trim();
  if (!raw) return "Project";
  if (raw.length > 72) raw = raw.slice(0, 72).trim();
  return raw;
}

function deriveProjectStyleGroupLabel(msg) {
  var flowMeta = msg && msg.flow_meta ? msg.flow_meta : {};
  var projectNavigation = flowMeta && flowMeta.project_navigation ? flowMeta.project_navigation : {};
  var navigation = flowMeta && flowMeta.navigation ? flowMeta.navigation : {};
  var candidates = [
    msg && msg.project_title,
    flowMeta && flowMeta.project_title,
    projectNavigation && projectNavigation.brand_name,
    navigation && navigation.brand_name,
  ];

  for (var i = 0; i < candidates.length; i++) {
    var label = normalizeStyleGroupLabel(candidates[i]);
    if (label && label !== "Project") return label;
  }
  return "Project";
}

function titleCaseSegment(value, fallback) {
  var cleaned = String(value || "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) cleaned = fallback || "Base";
  return cleaned.replace(/\b[a-z]/g, function(ch) { return ch.toUpperCase(); });
}

function normalizeColorHex(value) {
  if (!value || typeof value !== "string") return "";
  var raw = value.trim().toLowerCase();
  if (!raw || raw === "transparent") return "transparent";
  raw = raw.replace("#", "");
  if (raw.length === 3) raw = raw[0] + raw[0] + raw[1] + raw[1] + raw[2] + raw[2];
  if (!/^[0-9a-f]{6}$/.test(raw)) return "";
  return "#" + raw;
}

function buttonPaintVariant(data) {
  if (!data) return "Filled";
  var background = normalizeColorHex(data.backgroundColor);
  var border = normalizeColorHex(data.borderColor);
  if (background === "transparent" || !background) {
    return border && border !== "transparent" ? "Outline" : "Ghost";
  }
  if (border && border !== "transparent" && (background === "#ffffff" || background === "#f8f8f8" || background === "#f5f5f5")) {
    return "Outline";
  }
  return "Filled";
}

function inferPaintStyleDescriptor(data, paintRole) {
  var source = data && data.styleSourceData ? data.styleSourceData : data;
  if (!source) return null;

  var blob = [
    source.type || "",
    source.componentKey || "",
    source.componentName || "",
    source.name || "",
    source.comp_type || "",
    data && data.styleGroupRole ? data.styleGroupRole : "",
  ].join(" ").toLowerCase();

  if (source.type === "button" || /\bactions\/button\b/.test(blob) || /\b(btn|button|cta|checkout|cart|subscribe|buy|purchase|save|submit|confirm|cancel|add to cart)\b/.test(blob)) {
    return { family: "Actions", variant: buttonPaintVariant(source) };
  }
  if (/\bnavigation\/global|global nav|top nav|top bar|navbar|header nav\b/.test(blob)) {
    return { family: "Navigation", variant: "Global" };
  }
  if (/\bnavigation\/secondary|secondary nav|secondary tabs|tab bar|sub nav\b/.test(blob)) {
    return { family: "Navigation", variant: "Secondary" };
  }
  if (/\bnavigation\/nav-item|nav item|menu item|sidebar item|tab item\b/.test(blob)) {
    return { family: "Navigation", variant: "Item" };
  }
  if (/\bsidebar|side nav|sidenav|side panel|drawer\b/.test(blob)) {
    return { family: "Navigation", variant: "Sidebar" };
  }
  if (/\bmodal|dialog|drawer body|drawer panel\b/.test(blob)) {
    return { family: "Overlay", variant: "Modal" };
  }
  if (/\bcard|tile|widget|panel|surface|stat card|metric card\b/.test(blob)) {
    return { family: "Surface", variant: "Card" };
  }
  return null;
}

function buildPaintStyleName(data, paintRole, colorHex) {
  var descriptor = inferPaintStyleDescriptor(data, paintRole);
  var normalizedColor = normalizeColorHex(colorHex);
  if (!descriptor || !normalizedColor || normalizedColor === "transparent") return "";

  var roleSegment = paintRole === "stroke" ? "Stroke" : (paintRole === "text" ? "Text" : "Fill");
  var colorSegment = normalizedColor.replace("#", "").toUpperCase();
  return [
    "WingX",
    currentProjectStyleGroupLabel || "Project",
    titleCaseSegment(descriptor.family, "UI"),
    titleCaseSegment(descriptor.variant, "Base"),
    roleSegment + " " + colorSegment,
  ].join("/");
}

function getLocalPaintStyleByName(name) {
  if (!name) return null;
  var style = localPaintStylesByName[name];
  return style && !style.removed ? style : null;
}

function ensureSolidPaintStyle(name, colorHex) {
  var normalizedColor = normalizeColorHex(colorHex);
  if (!name || !normalizedColor || normalizedColor === "transparent") return null;
  var key = name + "::" + normalizedColor;
  if (paintStyleRegistry[key] && !paintStyleRegistry[key].removed) return paintStyleRegistry[key];

  var existing = getLocalPaintStyleByName(name);
  if (existing) {
    paintStyleRegistry[key] = existing;
    return existing;
  }

  var style = figma.createPaintStyle();
  style.name = name;
  style.paints = [{ type: "SOLID", color: hexToRgb(normalizedColor) }];
  paintStyleRegistry[key] = style;
  localPaintStylesByName[name] = style;
  return style;
}

function bindPaintStyle(node, data, paintRole, colorHex) {
  if (!node) return;
  var normalizedColor = normalizeColorHex(colorHex);
  try {
    if (paintRole === "stroke" && node.strokeStyleId !== undefined) {
      node.strokeStyleId = "";
    } else if (node.fillStyleId !== undefined) {
      node.fillStyleId = "";
    }
  } catch (_clearErr) {}

  if (!normalizedColor || normalizedColor === "transparent") return;

  var styleName = buildPaintStyleName(data, paintRole, normalizedColor);
  if (!styleName) return;
  var style = ensureSolidPaintStyle(styleName, normalizedColor);
  if (!style) return;

  try {
    if (paintRole === "stroke" && node.strokeStyleId !== undefined) {
      node.strokeStyleId = style.id;
    } else if (node.fillStyleId !== undefined) {
      node.fillStyleId = style.id;
    }
  } catch (_bindErr) {}
}

// ── Link-picking mode state ───────────────────────────────────────
// When true, the next selectionchange fires as a link target pick
var isPickingLinkTarget     = false;
var linkPickingSourceNodeId = null;

// ── Add-frame-picking mode state ─────────────────────────────────
// When true, the next selectionchange fires as a frame-add pick
var isPickingAddFrame = false;

// ── Messages from ui.html ─────────────────────────────────────────
figma.ui.onmessage = function(msg) {
  if (!msg || !msg.type) return;

  switch (msg.type) {

    case "status":
      console.log("[STATUS]", msg.message);
      break;

    case "resize_plugin_ui":
      try {
        var width = Number(msg.width || 0);
        var height = Number(msg.height || 0);
        if (!width || !height) {
          if (msg.mode === "minimize") {
            width = PLUGIN_UI_MINIMIZED_WIDTH;
            height = PLUGIN_UI_MINIMIZED_HEIGHT;
          } else {
            width = PLUGIN_UI_DEFAULT_WIDTH;
            height = PLUGIN_UI_DEFAULT_HEIGHT;
          }
        }
        width = Math.max(70, Math.round(width));
        height = Math.max(48, Math.round(height));
        figma.ui.resize(width, height);
        if (msg.pinTopRight) {
          positionPluginTopRight(width, height);
        }
      } catch (err) {
        console.warn("[UI_RESIZE] Failed:", err && err.message ? err.message : err);
      }
      break;

    case "page_start":
      queueGenerationMessage(msg);
      break;

    case "page_chunk":
      queueGenerationMessage(msg);
      break;

    case "page_end":
      queueGenerationMessage(msg);
      break;

    case "complete":
      queueGenerationMessage(msg);
      break;

    case "error":
      console.error("[ERROR]", msg.message);
      break;

    case "test_result":
      break;

    // ── Extract live Figma tree for React export ──────────────
    case "extract_figma_tree":
      handleExtractFigmaTree();
      break;

    // ── Export exact frame IDs chosen in the Export Panel ────
    // ui.html passes the frame IDs that the user checked — we
    // fetch those specific frames regardless of Figma selection.
    case "extract_frames_by_ids":
      handleExtractFramesByIds(msg.frameIds || [], msg.projectTitle || "");
      break;

    // ── Lightweight extract: all selected frames + their child nodes ──
    // Called when Export panel opens. Returns frame metadata only (no asset bytes).
    case "extract_export_frames":
      handleExtractExportFrames();
      break;

    // ── Link picking: user clicked "Link" on an element ──────────
    // Enter picking mode — next Figma selection becomes the link target.
    case "start_link_picking":
      isPickingLinkTarget = true;
      linkPickingSourceNodeId = msg.sourceNodeId || null;
      console.log("[LINK_PICK] Entering link-picking mode for node:", linkPickingSourceNodeId);
      break;

    // ── Cancel link picking mode ──────────────────────────────
    case "cancel_link_picking":
      isPickingLinkTarget = false;
      linkPickingSourceNodeId = null;
      break;

    // ── Add frame picking: user clicks a frame to add it to export panel ──
    // Next selectionchange fires as a frame-add pick instead of a normal selection.
    case "start_add_frame_picking":
      var selectedFrames = collectSelectedFramesForExport();
      console.log("[ADD_FRAME_PICK] Adding " + selectedFrames.length + " currently selected frame(s)");
      figma.ui.postMessage({
        type:   "add_frame_picked",
        frame:  selectedFrames[0] || null,
        frames: selectedFrames,
      });
      isPickingAddFrame = false;
      break;

    case "cancel_add_frame_picking":
      isPickingAddFrame = false;
      console.log("[ADD_FRAME_PICK] Cancelled");
      break;

    // ── Build button name map across all frames ───────────────
    case "build_button_name_map":
      handleBuildButtonNameMap(msg.frameIds || []);
      break;

    // ── Extract selected pages/components for advanced generation ──
    case "extract_attachment_selection":
      handleExtractAttachmentSelection(msg);
      break;

    // ── Highlight a node in Figma canvas ─────────────────────
    case "highlight_node":
      handleHighlightNode(msg.nodeId);
      break;

    // ── Unhighlight a node in Figma canvas ───────────────────
    case "unhighlight_node":
      handleUnhighlightNode(msg.nodeId);
      break;

    case "create_auto_layout_component":
      handleCreateAutoLayoutComponent(msg);
      break;
  }
};

function handleCreateAutoLayoutComponent(msg) {
  createAutoLayoutPresetComponent(msg && msg.componentType).then(function(node) {
    figma.currentPage.selection = [node];
    figma.viewport.scrollAndZoomIntoView([node]);
    figma.ui.postMessage({
      type: "auto_layout_component_created",
      componentType: msg && msg.componentType ? msg.componentType : "card",
      nodeId: node.id,
      nodeName: node.name,
    });
  }).catch(function(err) {
    console.error("[AUTO_LAYOUT_COMPONENT]", err && err.message ? err.message : err);
    figma.ui.postMessage({
      type: "auto_layout_component_error",
      componentType: msg && msg.componentType ? msg.componentType : "card",
      error: String(err && err.message ? err.message : err),
    });
  });
}

function ensureAutoLayoutFontsLoaded() {
  return Promise.all([
    figma.loadFontAsync({ family: "Inter", style: "Regular" }),
    figma.loadFontAsync({ family: "Inter", style: "Medium" }),
    figma.loadFontAsync({ family: "Inter", style: "Bold" }),
  ]);
}

function createAutoLayoutTextNode(options) {
  var cfg = options || {};
  var node = figma.createText();
  node.name = cfg.name || "Text";
  node.fontName = cfg.fontName || { family: "Inter", style: "Regular" };
  node.fontSize = cfg.fontSize || 14;
  node.characters = String(cfg.text || "");
  node.fills = [{ type: "SOLID", color: cfg.color || { r: 0.1, g: 0.1, b: 0.12 } }];
  node.textAutoResize = "WIDTH_AND_HEIGHT";
  return node;
}

function createAutoLayoutFrameNode(options, useComponent) {
  var cfg = options || {};
  var frame = useComponent ? figma.createComponent() : figma.createFrame();
  frame.name = cfg.name || (useComponent ? "Component" : "Frame");
  frame.layoutMode = cfg.direction || "VERTICAL";
  frame.primaryAxisSizingMode = cfg.primaryAxisSizingMode || "AUTO";
  frame.counterAxisSizingMode = cfg.counterAxisSizingMode || "AUTO";
  frame.primaryAxisAlignItems = cfg.primaryAxisAlignItems || "MIN";
  frame.counterAxisAlignItems = cfg.counterAxisAlignItems || "MIN";
  frame.itemSpacing = cfg.spacing !== undefined ? cfg.spacing : 8;
  frame.paddingTop = cfg.paddingTop !== undefined ? cfg.paddingTop : (cfg.padding !== undefined ? cfg.padding : 12);
  frame.paddingRight = cfg.paddingRight !== undefined ? cfg.paddingRight : (cfg.padding !== undefined ? cfg.padding : 12);
  frame.paddingBottom = cfg.paddingBottom !== undefined ? cfg.paddingBottom : (cfg.padding !== undefined ? cfg.padding : 12);
  frame.paddingLeft = cfg.paddingLeft !== undefined ? cfg.paddingLeft : (cfg.padding !== undefined ? cfg.padding : 12);
  frame.cornerRadius = cfg.cornerRadius || 0;
  frame.fills = cfg.fills !== undefined ? cfg.fills : [];
  if (cfg.strokes) {
    frame.strokes = cfg.strokes;
    frame.strokeWeight = cfg.strokeWeight || 1;
  }
  return frame;
}

function appendAutoLayoutChildren(parent, children) {
  for (var i = 0; i < children.length; i++) {
    if (children[i]) parent.appendChild(children[i]);
  }
  return parent;
}

function createIconDotNode(name, rgb, size) {
  var dot = figma.createEllipse();
  dot.name = name || "Dot";
  dot.resize(size || 10, size || 10);
  dot.fills = [{ type: "SOLID", color: rgb || { r: 0.24, g: 0.35, b: 0.95 } }];
  return dot;
}

function createAutoLayoutBadgeNode(text) {
  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: "Badge",
    direction: "HORIZONTAL",
    spacing: 6,
    paddingTop: 6,
    paddingRight: 10,
    paddingBottom: 6,
    paddingLeft: 10,
    cornerRadius: 999,
    fills: [{ type: "SOLID", color: { r: 0.92, g: 0.96, b: 1 } }],
    primaryAxisAlignItems: "CENTER",
    counterAxisAlignItems: "CENTER",
  }, false), [
    createAutoLayoutTextNode({
      name: "Badge Label",
      text: text || "Active",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 12,
      color: { r: 0.15, g: 0.4, b: 0.85 },
    })
  ]);
}

function createAutoLayoutButtonNode(labelText, secondary) {
  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: secondary ? "Secondary Button" : "Button",
    direction: "HORIZONTAL",
    spacing: 8,
    paddingTop: 12,
    paddingRight: 16,
    paddingBottom: 12,
    paddingLeft: 16,
    cornerRadius: 10,
    fills: secondary
      ? [{ type: "SOLID", color: { r: 0.94, g: 0.96, b: 1 } }]
      : [{ type: "SOLID", color: { r: 0.24, g: 0.35, b: 0.95 } }],
    strokes: secondary
      ? [{ type: "SOLID", color: { r: 0.82, g: 0.87, b: 0.96 } }]
      : null,
    strokeWeight: 1,
    primaryAxisAlignItems: "CENTER",
    counterAxisAlignItems: "CENTER",
  }, false), [
    createAutoLayoutTextNode({
      name: "Button Label",
      text: labelText || "Click Me",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: secondary ? { r: 0.2, g: 0.32, b: 0.74 } : { r: 1, g: 1, b: 1 },
    })
  ]);
}

function createAutoLayoutInputField(label, value, placeholder) {
  var wrapper = createAutoLayoutFrameNode({
    name: (label || "Field") + " Field",
    direction: "VERTICAL",
    spacing: 6,
    padding: 0,
    fills: [],
  }, false);

  var input = createAutoLayoutFrameNode({
    name: (label || "Field") + " Input",
    direction: "VERTICAL",
    spacing: 4,
    paddingTop: 12,
    paddingRight: 14,
    paddingBottom: 12,
    paddingLeft: 14,
    cornerRadius: 10,
    fills: [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }],
    strokes: [{ type: "SOLID", color: { r: 0.85, g: 0.88, b: 0.93 } }],
    strokeWeight: 1,
  }, false);

  appendAutoLayoutChildren(wrapper, [
    createAutoLayoutTextNode({
      name: "Label",
      text: label || "Label",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 12,
      color: { r: 0.35, g: 0.39, b: 0.47 },
    }),
    appendAutoLayoutChildren(input, [
      createAutoLayoutTextNode({
        name: "Value",
        text: value || placeholder || "Enter value",
        fontName: { family: "Inter", style: "Regular" },
        fontSize: 14,
        color: value ? { r: 0.11, g: 0.12, b: 0.16 } : { r: 0.56, g: 0.59, b: 0.66 },
      })
    ])
  ]);

  return wrapper;
}

function createAutoLayoutListItem(title, subtitle, withBadge) {
  var left = createAutoLayoutFrameNode({
    name: "Item Copy",
    direction: "VERTICAL",
    spacing: 4,
    padding: 0,
    fills: [],
  }, false);

  appendAutoLayoutChildren(left, [
    createAutoLayoutTextNode({
      name: "Item Title",
      text: title || "List item",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: { r: 0.11, g: 0.12, b: 0.16 },
    }),
    createAutoLayoutTextNode({
      name: "Item Subtitle",
      text: subtitle || "Supporting information",
      fontName: { family: "Inter", style: "Regular" },
      fontSize: 12,
      color: { r: 0.45, g: 0.49, b: 0.56 },
    })
  ]);

  var item = createAutoLayoutFrameNode({
    name: "List Item",
    direction: "HORIZONTAL",
    spacing: 12,
    paddingTop: 12,
    paddingRight: 12,
    paddingBottom: 12,
    paddingLeft: 12,
    cornerRadius: 12,
    fills: [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }],
    strokes: [{ type: "SOLID", color: { r: 0.9, g: 0.92, b: 0.95 } }],
    strokeWeight: 1,
    primaryAxisAlignItems: "SPACE_BETWEEN",
    counterAxisAlignItems: "CENTER",
  }, false);

  var children = [
    appendAutoLayoutChildren(createAutoLayoutFrameNode({
      name: "Leading",
      direction: "HORIZONTAL",
      spacing: 10,
      padding: 0,
      fills: [],
      counterAxisAlignItems: "CENTER",
    }, false), [
      createIconDotNode("Status Dot", { r: 0.24, g: 0.35, b: 0.95 }, 10),
      left
    ])
  ];

  if (withBadge) children.push(createAutoLayoutBadgeNode("Live"));
  appendAutoLayoutChildren(item, children);
  return item;
}

function createAutoLayoutNavbarComponent() {
  var navbar = createAutoLayoutFrameNode({
    name: "UI/Navbar",
    direction: "HORIZONTAL",
    spacing: 24,
    paddingTop: 16,
    paddingRight: 20,
    paddingBottom: 16,
    paddingLeft: 20,
    cornerRadius: 12,
    fills: [{ type: "SOLID", color: { r: 0.08, g: 0.1, b: 0.16 } }],
    primaryAxisAlignItems: "SPACE_BETWEEN",
    counterAxisAlignItems: "CENTER",
  }, true);

  var navItems = createAutoLayoutFrameNode({
    name: "Nav Items",
    direction: "HORIZONTAL",
    spacing: 16,
    padding: 0,
    fills: [],
    counterAxisAlignItems: "CENTER",
  }, false);

  appendAutoLayoutChildren(navItems, [
    createAutoLayoutTextNode({
      name: "Home",
      text: "Home",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: { r: 0.9, g: 0.92, b: 0.98 },
    }),
    createAutoLayoutTextNode({
      name: "Profile",
      text: "Profile",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: { r: 0.9, g: 0.92, b: 0.98 },
    }),
    createAutoLayoutTextNode({
      name: "Settings",
      text: "Settings",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: { r: 0.9, g: 0.92, b: 0.98 },
    })
  ]);

  appendAutoLayoutChildren(navbar, [
    createAutoLayoutTextNode({
      name: "Brand",
      text: "WingX",
      fontName: { family: "Inter", style: "Bold" },
      fontSize: 18,
      color: { r: 1, g: 1, b: 1 },
    }),
    appendAutoLayoutChildren(createAutoLayoutFrameNode({
      name: "Navbar Right",
      direction: "HORIZONTAL",
      spacing: 14,
      padding: 0,
      fills: [],
      counterAxisAlignItems: "CENTER",
    }, false), [
      navItems,
      createAutoLayoutButtonNode("Upgrade", false)
    ])
  ]);

  return navbar;
}

function createAutoLayoutStandaloneButtonComponent() {
  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: "UI/Button",
    direction: "HORIZONTAL",
    spacing: 8,
    paddingTop: 12,
    paddingRight: 16,
    paddingBottom: 12,
    paddingLeft: 16,
    cornerRadius: 10,
    fills: [{ type: "SOLID", color: { r: 0.24, g: 0.35, b: 0.95 } }],
    primaryAxisAlignItems: "CENTER",
    counterAxisAlignItems: "CENTER",
  }, true), [
    createAutoLayoutTextNode({
      name: "Label",
      text: "Click Me",
      fontName: { family: "Inter", style: "Medium" },
      fontSize: 14,
      color: { r: 1, g: 1, b: 1 },
    })
  ]);
}

function createAutoLayoutCardComponent() {
  var body = createAutoLayoutFrameNode({
    name: "Card Body",
    direction: "VERTICAL",
    spacing: 12,
    padding: 0,
    fills: [],
  }, false);

  appendAutoLayoutChildren(body, [
    createAutoLayoutBadgeNode("Pro"),
    createAutoLayoutTextNode({
      name: "Title",
      text: "Profile Card",
      fontName: { family: "Inter", style: "Bold" },
      fontSize: 16,
    }),
    createAutoLayoutTextNode({
      name: "Description",
      text: "Every nested section uses Auto Layout so the card grows and reflows automatically when content changes.",
      fontName: { family: "Inter", style: "Regular" },
      fontSize: 14,
      color: { r: 0.35, g: 0.37, b: 0.4 },
    }),
    appendAutoLayoutChildren(createAutoLayoutFrameNode({
      name: "Actions",
      direction: "HORIZONTAL",
      spacing: 8,
      padding: 0,
      fills: [],
      counterAxisAlignItems: "CENTER",
    }, false), [
      createAutoLayoutButtonNode("View Profile", false),
      createAutoLayoutButtonNode("Dismiss", true)
    ])
  ]);

  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: "UI/Card",
    direction: "VERTICAL",
    spacing: 12,
    padding: 16,
    cornerRadius: 12,
    fills: [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }],
    strokes: [{ type: "SOLID", color: { r: 0.88, g: 0.89, b: 0.91 } }],
    strokeWeight: 1,
  }, true), [body]);
}

function createAutoLayoutFormComponent() {
  var fields = createAutoLayoutFrameNode({
    name: "Form Fields",
    direction: "VERTICAL",
    spacing: 12,
    padding: 0,
    fills: [],
  }, false);

  appendAutoLayoutChildren(fields, [
    createAutoLayoutInputField("Full Name", "Kathir Raj", ""),
    createAutoLayoutInputField("Email", "kathir@wingx.ai", ""),
    createAutoLayoutInputField("Role", "", "Product Designer")
  ]);

  var actionRow = createAutoLayoutFrameNode({
    name: "Form Actions",
    direction: "HORIZONTAL",
    spacing: 10,
    padding: 0,
    fills: [],
    counterAxisAlignItems: "CENTER",
  }, false);

  appendAutoLayoutChildren(actionRow, [
    createAutoLayoutButtonNode("Save", false),
    createAutoLayoutButtonNode("Cancel", true)
  ]);

  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: "UI/Form",
    direction: "VERTICAL",
    spacing: 16,
    padding: 18,
    cornerRadius: 14,
    fills: [{ type: "SOLID", color: { r: 0.98, g: 0.99, b: 1 } }],
    strokes: [{ type: "SOLID", color: { r: 0.87, g: 0.9, b: 0.95 } }],
    strokeWeight: 1,
  }, true), [
    createAutoLayoutTextNode({
      name: "Form Title",
      text: "Edit Profile",
      fontName: { family: "Inter", style: "Bold" },
      fontSize: 16,
      color: { r: 0.11, g: 0.12, b: 0.16 },
    }),
    fields,
    actionRow
  ]);
}

function createAutoLayoutListComponent() {
  return appendAutoLayoutChildren(createAutoLayoutFrameNode({
    name: "UI/List",
    direction: "VERTICAL",
    spacing: 10,
    padding: 16,
    cornerRadius: 14,
    fills: [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }],
    strokes: [{ type: "SOLID", color: { r: 0.88, g: 0.89, b: 0.91 } }],
    strokeWeight: 1,
  }, true), [
    createAutoLayoutTextNode({
      name: "List Title",
      text: "Recent Activity",
      fontName: { family: "Inter", style: "Bold" },
      fontSize: 16,
    }),
    createAutoLayoutListItem("Design Sync", "Updated 4 min ago", true),
    createAutoLayoutListItem("Prototype Review", "Waiting for approval", false),
    createAutoLayoutListItem("Team Handoff", "Assets exported successfully", true)
  ]);
}

function createAutoLayoutPresetComponent(componentType) {
  return ensureAutoLayoutFontsLoaded().then(function() {
    var type = String(componentType || "card").toLowerCase();
    var node;

    if (type === "navbar") node = createAutoLayoutNavbarComponent();
    else if (type === "button") node = createAutoLayoutStandaloneButtonComponent();
    else if (type === "form") node = createAutoLayoutFormComponent();
    else if (type === "list") node = createAutoLayoutListComponent();
    else node = createAutoLayoutCardComponent();

    figma.currentPage.appendChild(node);
    node.x = Math.round(figma.viewport.center.x - node.width / 2);
    node.y = Math.round(figma.viewport.center.y - node.height / 2);
    return node;
  });
}

// ─────────────────────────────────────────────────────────────────
// PAGE BUFFER HANDLERS
// ─────────────────────────────────────────────────────────────────
function handlePageStart(msg) {
  var incomingComponentScope = deriveProjectComponentScope(msg);
  var incomingStyleGroupLabel = deriveProjectStyleGroupLabel(msg);
  if (orderedPageIds.length === 0 && renderQueue.length === 0 && !isRendering) {
    renderCanvasPageRef = getWorktualDesignPage();
    if (figma.currentPage.id !== renderCanvasPageRef.id) {
      try { figma.currentPage = renderCanvasPageRef; } catch (_pageErr) {}
    }
    groupRegistry = {};
    columnRegistry = {};
    adjacentRegistry = {};
    componentMasterRegistry = {};
    paintStyleRegistry = {};
    currentProjectComponentScope = incomingComponentScope;
    currentProjectStyleGroupLabel = incomingStyleGroupLabel;
    var bounds = getCanvasBounds();
    projectBaseX = 0;
    projectBaseY = bounds.hasNodes ? bounds.maxBottom + GROUP_GAP_Y : 0;
    groupCurrentY = projectBaseY;
    columnBaseY = projectBaseY;
  } else if (!currentProjectComponentScope && incomingComponentScope) {
    currentProjectComponentScope = incomingComponentScope;
    currentProjectStyleGroupLabel = incomingStyleGroupLabel;
  }

  var page_id      = msg.page_id;
  var page_name    = msg.page_name;
  var flow_group    = (msg.theme && msg.theme.flow_group) ? msg.theme.flow_group :
                      ((msg.flow_meta && msg.flow_meta.flow_group) ? msg.flow_meta.flow_group : "");
  var flow_group_id = (msg.theme && msg.theme.flow_group_id) ? msg.theme.flow_group_id :
                      ((msg.flow_meta && msg.flow_meta.flow_group_id) ? msg.flow_meta.flow_group_id : "");
  var feature_group = (msg.theme && msg.theme.feature_group) ? msg.theme.feature_group : (page_name.split('—')[0].trim() || 'General');
  var group_name    = flow_group || feature_group;
  var group_key     = flow_group_id || group_name;

  console.log("[PAGE_START]", msg.page_number + "/" + msg.total_pages + ":", page_name,
    "group=" + group_name, "key=" + group_key, "(" + msg.total_children + " elements, " + msg.total_chunks + " chunks)");

  // Track order — for in-order streaming
  orderedPageIds.push(page_id);

  pageBuffers[page_id] = {
    page_name:       page_name,
    page_number:     msg.page_number,
    total_pages:     msg.total_pages,
    frame_meta:      msg.frame_meta,
    flow_meta:       msg.flow_meta || {},
    total_chunks:    msg.total_chunks,
    received_chunks: 0,
    children:        [],
    feature_group:   feature_group,
    flow_group:      group_name,
    flow_group_id:   group_key,
    column_group:    (msg.flow_meta && msg.flow_meta.column_group) ? msg.flow_meta.column_group : "",
    column_group_id: (msg.flow_meta && msg.flow_meta.column_group_id) ? msg.flow_meta.column_group_id : "",
    ready:           false,   // true once page_end received
  };
}

function handlePageChunk(msg) {
  var page_id = msg.page_id;
  var buf = pageBuffers[page_id];
  if (!buf) {
    console.error("[PAGE_CHUNK] No buffer for page_id:", page_id);
    return;
  }

  var incoming = msg.children || [];
  for (var i = 0; i < incoming.length; i++) {
    buf.children.push(incoming[i]);
  }
  buf.received_chunks++;

  console.log("[PAGE_CHUNK]", page_id + ":", buf.received_chunks + "/" + buf.total_chunks,
    "(" + incoming.length + " elements, total so far: " + buf.children.length + ")");
}

function handlePageEnd(msg) {
  var page_id = msg.page_id;
  var buf = pageBuffers[page_id];
  if (!buf) {
    console.error("[PAGE_END] No buffer for page_id:", page_id);
    return;
  }

  console.log("[PAGE_END] Buffered '" + buf.page_name + "': " + buf.children.length + " elements — waiting for ordered render");
  buf.ready = true;

  // Drain in order — render all consecutive ready pages from the front of the queue
  drainOrderedQueue();
}

function drainOrderedQueue() {
  while (orderedPageIds.length > 0) {
    var nextId = orderedPageIds[0];
    var buf    = pageBuffers[nextId];
    if (!buf || !buf.ready) break;

    orderedPageIds.shift();

    var fullFrame = {
      page_id:         nextId,
      type:            buf.frame_meta.type,
      name:            buf.frame_meta.name,
      width:           buf.frame_meta.width,
      height:          buf.frame_meta.height,
      backgroundColor: buf.frame_meta.backgroundColor,
      children:        buf.children,
      feature_group:   buf.feature_group,
      flow_group:      buf.flow_group,
      flow_group_id:   buf.flow_group_id,
      column_group:    buf.column_group,
      column_group_id: buf.column_group_id,
      flow_meta:       buf.flow_meta || {},
    };

    // Push into render queue — never render two frames simultaneously
    renderQueue.push({ frame: fullFrame, buf: buf });
    delete pageBuffers[nextId];
  }

  // Kick off sequential render if not already running
  processRenderQueue();
}

function processRenderQueue() {
  if (isRendering) return;           // already rendering one — wait
  if (renderQueue.length === 0) return; // nothing to render

  isRendering = true;
  var item    = renderQueue.shift();

  var renderer = (item.frame.flow_meta && item.frame.flow_meta.followup_source_frame_id)
    ? renderFrameAdjacent
    : renderFrameInGroup;

  ensureLocalPaintStylesReady().then(function() {
    return renderer(item.frame);
  }).then(function(renderedFrame) {
    console.log("[RENDER] Done:", item.buf.page_name);
    figma.ui.postMessage({
      type:        "render_done",
      page_name:   item.buf.page_name,
      page_number: item.buf.page_number,
      page_id:     item.frame.page_id || item.buf.page_id || '',
      frame_id:    renderedFrame && renderedFrame.id ? renderedFrame.id : '',
    });
    isRendering = false;
    processRenderQueue(); // render next in queue
  }).catch(function(err) {
    console.error("[RENDER ERROR]", item.buf.page_name, ":", err.message || err);
    figma.ui.postMessage({
      type:      "render_error",
      page_name: item.buf.page_name,
      page_id:   item.frame.page_id || item.buf.page_id || '',
      error:     String(err),
    });
    isRendering = false;
    processRenderQueue(); // continue even after error
  });
}

// ─────────────────────────────────────────────────────────────────
// RENDERER
// ─────────────────────────────────────────────────────────────────
// Constants for grouped layout
var GROUP_PADDING     = 60;   // padding inside the background container
var GROUP_GAP_X       = 40;   // horizontal gap between frames inside a group
var GROUP_GAP_Y       = 120;  // vertical gap between group rows
var GROUP_LABEL_H     = 48;   // height reserved for group label at top
var GROUP_ANNOTATION_SPACE = 220;
var GROUP_BG_COLOR    = { r: 0.851, g: 0.851, b: 0.851 };  // #D9D9D9
var COLUMN_GAP_X      = 220;
var COLUMN_LABEL_H    = 36;

function getOrCreateExplicitColumn(flowMeta) {
  var columnKey = flowMeta.column_group_id || flowMeta.column_group || "";
  if (!columnKey) return null;

  if (!columnRegistry[columnKey]) {
    var xStart = projectBaseX;
    var maxRight = 0;
    for (var existingKey in columnRegistry) {
      if (!columnRegistry.hasOwnProperty(existingKey)) continue;
      var existing = columnRegistry[existingKey];
      maxRight = Math.max(maxRight, existing.xStart + existing.maxWidth);
    }
    if (maxRight > 0) xStart = maxRight + COLUMN_GAP_X;

    var title = flowMeta.column_title || flowMeta.column_group || flowMeta.column_label || "Column";
    var labelText = figma.createText();
    labelText.name       = title + " — Column Label";
    labelText.fontName   = { family: "Inter", style: "Bold" };
    labelText.characters = title;
    labelText.fontSize   = 24;
    labelText.fills      = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    labelText.x          = xStart;
    labelText.y          = columnBaseY;
    appendToRenderCanvas(labelText);

    columnRegistry[columnKey] = {
      xStart: xStart,
      nextY: columnBaseY + COLUMN_LABEL_H,
      maxWidth: 0,
      label: labelText,
      order: flowMeta.column_group_order || 0,
      title: title,
    };
  }

  return columnRegistry[columnKey];
}

function renderFrameAdjacent(frameData) {
  var flowMeta = frameData.flow_meta || {};
  var sourceId = flowMeta.followup_source_frame_id || '';
  if (!sourceId) return renderFrameInGroup(frameData);

  return Promise.all([
    figma.loadFontAsync({ family: "Inter", style: "Regular" }),
    figma.loadFontAsync({ family: "Inter", style: "Medium" }),
    figma.loadFontAsync({ family: "Inter", style: "Semi Bold" }),
    figma.loadFontAsync({ family: "Inter", style: "Bold" }),
    figma.getNodeByIdAsync(sourceId),
  ]).then(function(results) {
    var sourceFrame = results[4];
    if (!sourceFrame || sourceFrame.type !== 'FRAME') {
      return renderFrameInGroup(frameData);
    }

    var frameW   = frameData.width  || 1440;
    var frameH   = frameData.height || 1080;
    var children = frameData.children || [];
    var reg      = adjacentRegistry[sourceId];
    var baseX    = sourceFrame.x + sourceFrame.width + GROUP_GAP_X;
    var placeX   = reg ? reg.nextX : baseX;
    var placeY   = sourceFrame.y;

    var frame = figma.createFrame();
    frame.name = frameData.name || 'Page';
    frame.resize(frameW, frameH);
    frame.x = placeX;
    frame.y = placeY;
    frame.fills = [{ type: 'SOLID', color: hexToRgb(frameData.backgroundColor || '#FFFFFF') }];
    frame.clipsContent = true;

    return renderChildren(children, frame).then(function() {
      appendToRenderCanvas(frame);
      var label = createScreenLabel(flowMeta.screen_title || frameData.name || 'Screen', frame.x, frame.y - 28);
      appendToRenderCanvas(label);

      adjacentRegistry[sourceId] = {
        nextX: placeX + frameW + GROUP_GAP_X
      };

      return frame;
    });
  });
}

function renderFrameInGroup(frameData) {
  return Promise.all([
    figma.loadFontAsync({ family: "Inter", style: "Regular" }),
    figma.loadFontAsync({ family: "Inter", style: "Medium" }),
    figma.loadFontAsync({ family: "Inter", style: "Semi Bold" }),
    figma.loadFontAsync({ family: "Inter", style: "Bold" }),
  ]).then(function() {
    var groupKey   = frameData.flow_group_id || frameData.flow_group || frameData.feature_group || 'General';
    var groupName  = frameData.flow_group || frameData.feature_group || 'General';
    var frameW     = frameData.width  || 1440;
    var frameH     = frameData.height || 1080;
    var flowMeta   = frameData.flow_meta || {};
    var explicitColumn = getOrCreateExplicitColumn(flowMeta);

    // ── Get or create the group container ──────────────────────────
    if (!groupRegistry[groupKey]) {
      // Calculate Y position for this new group row
      var startY = explicitColumn ? explicitColumn.nextY : groupCurrentY;
      var startX = explicitColumn ? explicitColumn.xStart : projectBaseX;

      // Create the light background rectangle for this group
      var bgRect = figma.createRectangle();
      bgRect.name = groupName + ' — Group';
      bgRect.x    = startX;
      bgRect.y    = startY;
      bgRect.resize(frameW + GROUP_PADDING * 2, frameH + GROUP_PADDING * 2 + GROUP_LABEL_H + GROUP_ANNOTATION_SPACE);
      bgRect.fills        = [{ type: 'SOLID', color: GROUP_BG_COLOR, opacity: 0.1 }];
      bgRect.cornerRadius = 24;
      appendToRenderCanvas(bgRect);

      // Create the group label text
      var labelText = figma.createText();
      labelText.name       = groupName + ' — Label';
      labelText.fontName   = { family: 'Inter', style: 'Semi Bold' };
      labelText.characters = groupName;
      labelText.fontSize   = 22;
      labelText.fills      = [{ type: 'SOLID', color: { r: 1, g: 1, b: 1 } }];
      labelText.x          = startX + GROUP_PADDING;
      labelText.y          = startY + 16;
      appendToRenderCanvas(labelText);

      groupRegistry[groupKey] = {
        bgRect:  bgRect,
        label:   labelText,
        nextX:   startX + GROUP_PADDING,
        y:       startY + GROUP_LABEL_H + GROUP_PADDING,
        frames:  [],
        startY:  startY,
        startX:  startX,
        maxFrameH: frameH,
        requiredHeight: frameH + GROUP_PADDING * 2 + GROUP_LABEL_H + GROUP_ANNOTATION_SPACE,
        columnKey: explicitColumn ? (flowMeta.column_group_id || flowMeta.column_group || "") : "",
      };

      // Reserve Y space — will be updated as more frames are added
      if (explicitColumn) {
        explicitColumn.nextY = startY + groupRegistry[groupKey].requiredHeight + GROUP_GAP_Y;
        explicitColumn.maxWidth = Math.max(explicitColumn.maxWidth, frameW + GROUP_PADDING * 2);
      } else {
        groupCurrentY = startY + groupRegistry[groupKey].requiredHeight + GROUP_GAP_Y;
      }
    }

    var grp = groupRegistry[groupKey];
    grp.maxFrameH = Math.max(grp.maxFrameH, frameH);

    // ── Create the frame inside the group ──────────────────────────
    var frame  = figma.createFrame();
    frame.name = frameData.name || 'Page';
    frame.resize(frameW, frameH);
    frame.x    = grp.nextX;
    frame.y    = grp.y;
    frame.fills        = [{ type: 'SOLID', color: hexToRgb(frameData.backgroundColor || '#FFFFFF') }];
    frame.clipsContent = true;

    var children = frameData.children || [];
    return renderChildren(children, frame).then(function() {
      appendToRenderCanvas(frame);

      var screenLabel = createScreenLabel(flowMeta.screen_title || frameData.name || 'Screen', frame.x, grp.y - 28);
      appendToRenderCanvas(screenLabel);

      var frameRecord = {
        frame: frame,
        label: screenLabel,
        meta: flowMeta,
      };
      grp.frames.push(frameRecord);

      // Advance X for next frame in same group
      grp.nextX += frameW + GROUP_GAP_X;

      // Expand the background rectangle to fit the new frame
      var totalWidth = grp.nextX - GROUP_GAP_X + GROUP_PADDING;
      var minimumHeight = grp.maxFrameH + GROUP_PADDING * 2 + GROUP_LABEL_H + GROUP_ANNOTATION_SPACE;
      grp.requiredHeight = Math.max(grp.requiredHeight, minimumHeight);
      grp.bgRect.resize(totalWidth, grp.requiredHeight);

      if (grp.frames.length > 1) {
        var previous = grp.frames[grp.frames.length - 2];
        if (previous.meta && previous.meta.click_target_keywords && previous.meta.click_target_keywords.length > 0) {
          createClickIndicator(previous.frame, previous.meta.click_target_keywords);
        }
      }

      // Update Y for next group if the current group has grown taller
      var groupBottom = grp.startY + grp.requiredHeight + GROUP_GAP_Y;
      if (grp.columnKey && columnRegistry[grp.columnKey]) {
        var col = columnRegistry[grp.columnKey];
        col.nextY = Math.max(col.nextY, groupBottom);
        col.maxWidth = Math.max(col.maxWidth, totalWidth);
      } else if (groupBottom > groupCurrentY) {
        groupCurrentY = groupBottom;
      }

      return frame;
    });
  });
}

function createScreenLabel(title, x, y) {
  var text = figma.createText();
  text.name = title + " — Screen Label";
  text.fontName = { family: "Inter", style: "Medium" };
  text.characters = title;
  text.fontSize = 13;
  text.fills = [{ type: "SOLID", color: { r: 0.86, g: 0.89, b: 0.95 } }];
  text.x = x;
  text.y = y;
  return text;
}

function createArrowLine(x1, y1, x2, y2, color, name) {
  var dx = x2 - x1;
  var dy = y2 - y1;
  var length = Math.sqrt(dx * dx + dy * dy);
  if (length < 1) return { line: null, arrow: null };

  var line = figma.createLine();
  line.name = name || "Flow Arrow";
  line.x = x1;
  line.y = y1;
  line.resize(Math.max(1, length), 1);
  line.rotation = Math.atan2(dy, dx) * 180 / Math.PI;
  line.strokes = [{ type: "SOLID", color: color }];
  line.strokeWeight = 2;
  line.opacity = 0.9;

  var arrow = figma.createText();
  arrow.name = (name || "Flow Arrow") + " Head";
  arrow.fontName = { family: "Inter", style: "Bold" };
  arrow.characters = "➜";
  arrow.fontSize = 18;
  arrow.fills = [{ type: "SOLID", color: color }];
  arrow.x = x2 - 10;
  arrow.y = y2 - 12;
  arrow.rotation = line.rotation;

  return { line: line, arrow: arrow };
}

function createFlowConnector(prevFrame, nextFrame) {
  var startX = prevFrame.x + prevFrame.width + 10;
  var startY = prevFrame.y + (prevFrame.height / 2);
  var endX   = nextFrame.x - 10;
  var endY   = nextFrame.y + (nextFrame.height / 2);
  var arrow = createArrowLine(startX, startY, endX, endY, { r: 0.51, g: 0.64, b: 1.0 }, "Flow Connector");
  if (arrow.line) appendToRenderCanvas(arrow.line);
  if (arrow.arrow) appendToRenderCanvas(arrow.arrow);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function getNodeCenter(node) {
  if (!node || !node.absoluteTransform) return null;
  return {
    x: node.absoluteTransform[0][2] + ((node.width || 0) / 2),
    y: node.absoluteTransform[1][2] + ((node.height || 0) / 2),
  };
}

function findBestTargetNode(frame, keywords) {
  if (!frame || !keywords || keywords.length === 0) return null;
  var normalized = keywords.map(function(k) { return String(k || "").toLowerCase().trim(); }).filter(Boolean);
  if (normalized.length === 0) return null;

  var nodes = frame.findAll(function(node) { return node.id !== frame.id; });
  var best = null;
  var bestScore = 0;

  for (var i = 0; i < nodes.length; i++) {
    var node = nodes[i];
    var textContent = "";
    if (node.type === "TEXT") textContent = node.characters || "";
    var haystack = ((node.name || "") + " " + textContent).toLowerCase();
    if (!haystack.trim()) continue;

    var score = 0;
    for (var j = 0; j < normalized.length; j++) {
      if (haystack.indexOf(normalized[j]) !== -1) {
        score += 10 + normalized[j].length;
      }
    }

    if (node.type === "TEXT") score += 1;
    if (score > bestScore) {
      bestScore = score;
      best = node;
    }
  }

  return best;
}

function createClickIndicator(frame, keywords) {
  var target = findBestTargetNode(frame, keywords);
  var center = target ? getNodeCenter(target) : { x: frame.x + frame.width / 2, y: frame.y + 120 };
  if (!center) return null;

  var badge = figma.createFrame();
  badge.name = "Click Indicator";
  badge.resize(68, 28);
  badge.cornerRadius = 14;
  badge.fills = [{ type: "SOLID", color: { r: 0.15, g: 0.22, b: 0.42 }, opacity: 0.95 }];
  badge.strokes = [{ type: "SOLID", color: { r: 0.62, g: 0.73, b: 1.0 } }];
  badge.strokeWeight = 1;
  badge.x = clamp(center.x + 12, frame.x + 12, frame.x + frame.width - 80);
  badge.y = clamp(center.y - 42, frame.y + 12, frame.y + frame.height - 40);

  var text = figma.createText();
  text.name = "Click Indicator Label";
  text.fontName = { family: "Inter", style: "Bold" };
  text.characters = "Click";
  text.fontSize = 12;
  text.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  text.x = Math.max(0, Math.round((badge.width - text.width) / 2));
  text.y = Math.max(0, Math.round((badge.height - text.height) / 2));
  badge.appendChild(text);
  appendToRenderCanvas(badge);

  var arrow = createArrowLine(
    badge.x + badge.width / 2,  
    badge.y + badge.height,
    center.x,
    center.y,
    { r: 0.62, g: 0.73, b: 1.0 },
    "Click Indicator Connector"
  );
  if (arrow.line) appendToRenderCanvas(arrow.line);
  if (arrow.arrow) appendToRenderCanvas(arrow.arrow);

  return badge;
}

function renderChildren(children, parent) {
  var rendered = [];
  var promise = Promise.resolve();
  for (var i = 0; i < children.length; i++) {
    (function(child) {
      promise = promise.then(function() {
        return createNode(child, parent).then(function(node) {
          if (node) {
            parent.appendChild(node);
            applyChildAutoLayoutProps(node, child, parent);
            rendered.push({ node: node, data: child });
          }
        }).catch(function(e) {
          console.error("[RENDER] Error on '" + (child && child.name) + "':", e.message || e);
        });
      });
    })(children[i]);
  }
  return promise.then(function() {
    return rendered;
  });
}

// function getNextFrameX() {
//   var frames = figma.currentPage.children.filter(function(n) { return n.type === "FRAME"; });
//   if (frames.length === 0) return 0;
//   var rightmost = 0;
//   for (var i = 0; i < frames.length; i++) {
//     var edge = frames[i].x + frames[i].width;
//     if (edge > rightmost) rightmost = edge;
//   }
//   return rightmost + 200;
// }

function createNode(data, parent) {
  if (!data || !data.type) return Promise.resolve(null);
  if ((data.componentKey || data.componentName || data.type === "component") && data.type !== "text" && data.type !== "image" && data.type !== "line") {
    return createReusableComponentInstance(data, parent);
  }
  switch (data.type) {
    case "frame":     return createContainerFrame(data);
    case "component": return createReusableComponentInstance(data, parent);
    case "rectangle": return Promise.resolve(createRectangle(data));
    case "text":      return Promise.resolve(createText(data));
    case "image":     return createImage(data);
    case "button":    return Promise.resolve(createButton(data));
    case "ellipse":   return Promise.resolve(createEllipse(data));
    case "line":      return Promise.resolve(createLine(data));
    case "group":     return createGroup(data, parent);
    default:
      console.warn("[RENDER] Unknown type:", data.type);
      return Promise.resolve(null);
  }
}

// ── Rectangle ─────────────────────────────────────────────────────
function createRectangle(data) {
  var rect = figma.createRectangle();
  rect.name = normalizeNodeName(data.name, "Rectangle");
  rect.x = data.x || 0;
  rect.y = data.y || 0;
  rect.resize(Math.max(1, data.width || 100), Math.max(1, data.height || 100));
  if (data.backgroundColor === "transparent") {
    rect.fills = [];
  } else {
    rect.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || "#CCCCCC") }];
  }
  bindPaintStyle(rect, data, "fill", data.backgroundColor || "#CCCCCC");
  if (data.cornerRadius) rect.cornerRadius = data.cornerRadius;
  if (data.borderColor) {
    rect.strokes = [{ type: "SOLID", color: hexToRgb(data.borderColor) }];
    rect.strokeWeight = data.borderWidth || 1;
    bindPaintStyle(rect, data, "stroke", data.borderColor);
  }
  if (data.opacity !== undefined && data.opacity !== null) rect.opacity = data.opacity;
  if (data.rotation) rect.rotation = data.rotation;
  applyNodeSizingProps(rect, data, null);
  return rect;
}

function normalizeNodeName(name, fallback) {
  var raw = String(name || fallback || "").replace(/\s+/g, " ").trim();
  if (!raw) raw = fallback || "Layer";
  return raw;
}

function applyColorOverride(node, fillColor, fallbackColor, styleData) {
  var colorValue = fillColor || fallbackColor;
  if (!colorValue) return;
  try {
    if (colorValue === "transparent") {
      node.fills = [];
    } else {
      node.fills = [{ type: "SOLID", color: hexToRgb(colorValue) }];
    }
    bindPaintStyle(node, styleData || {}, "fill", colorValue);
  } catch (_err) {}
}

function applyStrokeOverride(node, borderColor, borderWidth, styleData) {
  if (!borderColor) return;
  try {
    node.strokes = [{ type: "SOLID", color: hexToRgb(borderColor) }];
    node.strokeWeight = borderWidth || 1;
    bindPaintStyle(node, styleData || {}, "stroke", borderColor);
  } catch (_err) {}
}

function normalizeSizingMode(value, fallback) {
  var raw = String(value || fallback || "").toUpperCase();
  if (raw === "HUG" || raw === "AUTO") return "HUG";
  if (raw === "FILL" || raw === "FILL_CONTAINER") return "FILL";
  if (raw === "FIXED") return "FIXED";
  return fallback || "";
}

function applyNodeMinSizeProps(node, data) {
  if (!node || !data) return;
  try {
    var minW = data.minWidth;
    var minH = data.minHeight;

    if (minW === undefined && data.layoutMode && data.width) minW = data.width;
    if (minH === undefined && data.layoutMode && data.height) minH = data.height;

    if (minW !== undefined && node.minWidth !== undefined) {
      node.minWidth = Math.max(1, Number(minW) || 1);
    }
    if (minH !== undefined && node.minHeight !== undefined) {
      node.minHeight = Math.max(1, Number(minH) || 1);
    }
  } catch (_err) {}
}

function applyNodeSizingProps(node, data, parent) {
  if (!node || !data) return;
  try {
    var horizontal = normalizeSizingMode(data.layoutSizingHorizontal, "");
    var vertical = normalizeSizingMode(data.layoutSizingVertical, "");

    if (!horizontal && typeof data.layoutGrow === "number" && data.layoutGrow > 0 && parent && parent.layoutMode && parent.layoutMode !== "NONE") {
      horizontal = "FILL";
    }

    if (node.type === "TEXT") {
      if (horizontal === "FILL") {
        node.layoutSizingHorizontal = "FILL";
      } else {
        node.layoutSizingHorizontal = "HUG";
      }
      node.layoutSizingVertical = vertical === "FIXED" ? "FIXED" : "HUG";
      applyNodeMinSizeProps(node, data);
      return;
    }

    if (horizontal && node.layoutSizingHorizontal !== undefined) {
      node.layoutSizingHorizontal = horizontal;
    }
    if (vertical && node.layoutSizingVertical !== undefined) {
      node.layoutSizingVertical = vertical;
    }
    applyNodeMinSizeProps(node, data);
  } catch (_err) {}
}

function applyChildAutoLayoutProps(node, data, parent) {
  if (!node || !data || !parent || !parent.layoutMode || parent.layoutMode === "NONE") return;
  try {
    try {
      if (node.layoutPositioning !== undefined) node.layoutPositioning = "AUTO";
      node.x = 0;
      node.y = 0;
    } catch (_xyErr) {}
    if (data.layoutAlign) node.layoutAlign = data.layoutAlign;
    if (typeof data.layoutGrow === "number") node.layoutGrow = data.layoutGrow;
    applyNodeSizingProps(node, data, parent);
  } catch (_err) {}
}

function shouldInferAutoLayout(container, data, childEntries) {
  if (!container || !data || !childEntries || childEntries.length < 2) return false;
  if (data.layoutMode && data.layoutMode !== "NONE") return false;
  if (data.autoLayout === false) return false;
  return container.type === "FRAME" || container.type === "COMPONENT";
}

function isNear(value, target, tolerance) {
  return Math.abs((value || 0) - target) <= tolerance;
}

function isBackgroundLikeEntry(entry, container) {
  if (!entry || !entry.node || !container) return false;
  if (entry.data && entry.data.layoutPositioning === "ABSOLUTE") return true;

  var node = entry.node;
  if (node.type !== "RECTANGLE" && node.type !== "ELLIPSE" && node.type !== "LINE") return false;

  var widthRatio = container.width > 0 ? node.width / container.width : 0;
  var heightRatio = container.height > 0 ? node.height / container.height : 0;
  var pinnedLeft = isNear(node.x, 0, 8);
  var pinnedTop = isNear(node.y, 0, 8);
  var pinnedRight = isNear(node.x + node.width, container.width, 8);
  var pinnedBottom = isNear(node.y + node.height, container.height, 8);

  return widthRatio >= 0.8 && heightRatio >= 0.8 && pinnedLeft && pinnedTop && pinnedRight && pinnedBottom;
}

function inferAutoLayoutMode(entries) {
  if (!entries || entries.length < 2) return "VERTICAL";

  var totalDx = 0;
  var totalDy = 0;
  for (var i = 1; i < entries.length; i++) {
    var prev = entries[i - 1].node;
    var current = entries[i].node;
    totalDx += Math.abs(current.x - prev.x);
    totalDy += Math.abs(current.y - prev.y);
  }

  return totalDx >= totalDy ? "HORIZONTAL" : "VERTICAL";
}

function sortEntriesForLayout(entries, layoutMode) {
  return entries.slice().sort(function(a, b) {
    if (layoutMode === "HORIZONTAL") {
      return (a.node.x - b.node.x) || (a.node.y - b.node.y);
    }
    return (a.node.y - b.node.y) || (a.node.x - b.node.x);
  });
}

function averageGap(sortedEntries, layoutMode) {
  if (!sortedEntries || sortedEntries.length < 2) return 16;

  var gaps = [];
  for (var i = 1; i < sortedEntries.length; i++) {
    var prev = sortedEntries[i - 1].node;
    var current = sortedEntries[i].node;
    var gap = layoutMode === "HORIZONTAL"
      ? current.x - (prev.x + prev.width)
      : current.y - (prev.y + prev.height);
    if (isFinite(gap) && gap >= 0) gaps.push(Math.round(gap));
  }

  if (!gaps.length) return 16;

  var total = 0;
  for (var gi = 0; gi < gaps.length; gi++) total += gaps[gi];
  return Math.max(0, Math.round(total / gaps.length));
}

function inferContainerPadding(container, entries) {
  var minX = Infinity;
  var minY = Infinity;
  var maxX = -Infinity;
  var maxY = -Infinity;

  for (var i = 0; i < entries.length; i++) {
    var node = entries[i].node;
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x + node.width);
    maxY = Math.max(maxY, node.y + node.height);
  }

  if (!isFinite(minX) || !isFinite(minY) || !isFinite(maxX) || !isFinite(maxY)) {
    return { left: 16, right: 16, top: 16, bottom: 16 };
  }

  return {
    left: Math.max(0, Math.round(minX)),
    right: Math.max(0, Math.round(container.width - maxX)),
    top: Math.max(0, Math.round(minY)),
    bottom: Math.max(0, Math.round(container.height - maxY)),
  };
}

function reorderAutoLayoutChildren(container, flowEntries, absoluteEntries) {
  var orderedNodes = [];
  for (var i = 0; i < absoluteEntries.length; i++) orderedNodes.push(absoluteEntries[i].node);
  for (var j = 0; j < flowEntries.length; j++) orderedNodes.push(flowEntries[j].node);

  for (var index = 0; index < orderedNodes.length; index++) {
    if (container.children[index] !== orderedNodes[index]) {
      container.insertChild(index, orderedNodes[index]);
    }
  }
}

function inferAndApplyAutoLayout(container, data, childEntries) {
  if (!shouldInferAutoLayout(container, data, childEntries)) return;

  var flowEntries = [];
  var absoluteEntries = [];
  for (var i = 0; i < childEntries.length; i++) {
    var entry = childEntries[i];
    if (isBackgroundLikeEntry(entry, container)) absoluteEntries.push(entry);
    else flowEntries.push(entry);
  }

  if (flowEntries.length < 2) return;

  var layoutMode = inferAutoLayoutMode(flowEntries);
  var sortedFlowEntries = sortEntriesForLayout(flowEntries, layoutMode);
  var spacing = averageGap(sortedFlowEntries, layoutMode);
  var padding = inferContainerPadding(container, sortedFlowEntries);

  container.layoutMode = layoutMode;
  container.primaryAxisAlignItems = "MIN";
  container.counterAxisAlignItems = layoutMode === "HORIZONTAL" ? "CENTER" : "MIN";
  container.primaryAxisSizingMode = data.primaryAxisSizingMode || "AUTO";
  container.counterAxisSizingMode = data.counterAxisSizingMode || "AUTO";
  container.itemSpacing = spacing;
  container.paddingLeft = padding.left;
  container.paddingRight = padding.right;
  container.paddingTop = padding.top;
  container.paddingBottom = padding.bottom;

  if ("layoutWrap" in container) {
    try { container.layoutWrap = "NO_WRAP"; } catch (_err) {}
  }

  reorderAutoLayoutChildren(container, sortedFlowEntries, absoluteEntries);

  for (var ai = 0; ai < absoluteEntries.length; ai++) {
    try {
      absoluteEntries[ai].node.layoutPositioning = "AUTO";
      absoluteEntries[ai].node.x = 0;
      absoluteEntries[ai].node.y = 0;
    } catch (_err) {}
  }

  for (var fi = 0; fi < sortedFlowEntries.length; fi++) {
    var flowNode = sortedFlowEntries[fi].node;
    var flowData = sortedFlowEntries[fi].data || {};
    applyChildAutoLayoutProps(flowNode, flowData, container);
  }
}

function applyInstanceOverrides(targetNode, sourceData) {
  if (!targetNode || !sourceData) return;

  try {
    if (sourceData.opacity !== undefined && sourceData.opacity !== null) targetNode.opacity = sourceData.opacity;
  } catch (_err) {}

  try {
    if (sourceData.cornerRadius !== undefined && sourceData.cornerRadius !== null && targetNode.cornerRadius !== undefined) {
      targetNode.cornerRadius = sourceData.cornerRadius;
    }
  } catch (_err) {}

  var targetType = targetNode.type || "";
  if (targetType === "TEXT") {
    try {
      var styleMap = { "bold": "Bold", "semibold": "Semi Bold", "medium": "Medium", "regular": "Regular" };
      if (sourceData.fontWeight || sourceData.fontSize || sourceData.text) {
        var fw = (sourceData.fontWeight || "regular").toLowerCase();
        targetNode.fontName = { family: "Inter", style: styleMap[fw] || "Regular" };
        if (sourceData.fontSize) targetNode.fontSize = sourceData.fontSize;
        if (sourceData.text !== undefined && sourceData.text !== null) targetNode.characters = String(sourceData.text);
      }
      if (sourceData.color) {
        targetNode.fills = [{ type: "SOLID", color: hexToRgb(sourceData.color) }];
        bindPaintStyle(targetNode, sourceData, "text", sourceData.color);
      }
    } catch (_err) {}
  } else {
    applyColorOverride(targetNode, sourceData.backgroundColor, null, sourceData);
    applyStrokeOverride(targetNode, sourceData.borderColor, sourceData.borderWidth, sourceData);
  }

  var targetChildren = targetNode.children || [];
  var sourceChildren = sourceData.children || [];
  var limit = Math.min(targetChildren.length, sourceChildren.length);
  for (var i = 0; i < limit; i++) {
    applyInstanceOverrides(targetChildren[i], sourceChildren[i]);
  }
}

function applyContainerStyling(frame, data) {
  frame.name = normalizeNodeName(data.name, "Container");
  frame.x = data.x || 0;
  frame.y = data.y || 0;
  var width = Math.max(1, data.width || 100);
  var height = Math.max(1, data.height || 100);
  frame.resize(width, height);

  if (data.backgroundColor === "transparent") {
    frame.fills = [];
  } else if (data.backgroundColor) {
    frame.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor) }];
  } else {
    frame.fills = [];
  }
  bindPaintStyle(frame, data, "fill", data.backgroundColor);

  if (data.cornerRadius) frame.cornerRadius = data.cornerRadius;
  if (typeof data.clipsContent === "boolean") frame.clipsContent = data.clipsContent;
  else frame.clipsContent = true;

  if (data.borderColor) {
    frame.strokes = [{ type: "SOLID", color: hexToRgb(data.borderColor) }];
    frame.strokeWeight = data.borderWidth || 1;
    bindPaintStyle(frame, data, "stroke", data.borderColor);
  }
  if (data.opacity !== undefined && data.opacity !== null) frame.opacity = data.opacity;

  if (data.layoutMode) {
    frame.layoutMode = data.layoutMode;
    frame.primaryAxisAlignItems = data.primaryAxisAlignItems || "MIN";
    frame.counterAxisAlignItems = data.counterAxisAlignItems || "MIN";
    frame.itemSpacing = data.itemSpacing || 0;
    frame.paddingLeft = data.paddingLeft || 0;
    frame.paddingRight = data.paddingRight || 0;
    frame.paddingTop = data.paddingTop || 0;
    frame.paddingBottom = data.paddingBottom || 0;
    if (data.primaryAxisSizingMode) frame.primaryAxisSizingMode = data.primaryAxisSizingMode;
    if (data.counterAxisSizingMode) frame.counterAxisSizingMode = data.counterAxisSizingMode;
  }

  applyNodeSizingProps(frame, data, null);
  applyNodeMinSizeProps(frame, data);
}

function createContainerFrame(data) {
  var frame = figma.createFrame();
  applyContainerStyling(frame, data);
  var children = data.children || [];
  return renderChildren(children, frame).then(function(renderedChildren) {
    inferAndApplyAutoLayout(frame, data, renderedChildren);
    return frame;
  });
}

function getComponentPageAsync() {
  componentLibraryPageRef = getWorktualComponentsPage();
  return componentLibraryPageRef.loadAsync().then(function() {
    var page = componentLibraryPageRef;
    var children = page.children || [];
    componentLibraryStartX = 80;
    var hasExistingComponents = false;
    var maxRight = componentLibraryStartX - 120;
    var currentRowTop = 80;
    var currentRowHeight = 0;

    for (var ci = 0; ci < children.length; ci++) {
      var child = children[ci];
      if (!isComponentLibraryNode(child)) continue;
      hasExistingComponents = true;
      maxRight = Math.max(maxRight, (child.x || 0) + (child.width || 0));
      if ((child.y || 0) >= currentRowTop) {
        currentRowTop = child.y || 0;
        currentRowHeight = Math.max(currentRowHeight, child.height || 0);
      }
    }

    if (hasExistingComponents) {
      componentLibraryNextX = Math.max(componentLibraryStartX, maxRight + 120);
      componentLibraryNextY = currentRowTop || 80;
      componentLibraryRowH = Math.max(0, currentRowHeight);
    } else {
      componentLibraryNextX = componentLibraryStartX;
      componentLibraryNextY = 80;
      componentLibraryRowH = 0;
    }

    return page;
  });
}

function componentRegistryKey(data) {
  var raw = data.componentKey || data.componentName || data.name || "Reusable Component";
  var normalized = String(raw).toLowerCase().replace(/[^a-z0-9/_-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  return currentProjectComponentScope ? (currentProjectComponentScope + "::" + normalized) : normalized;
}

function findExistingMasterComponent(key, data) {
  if (!key) return Promise.resolve(null);
  if (componentMasterRegistry[key] && !componentMasterRegistry[key].removed) return Promise.resolve(componentMasterRegistry[key]);
  return getComponentPageAsync().then(function(page) {
    var children = page.children || [];
    var wantedName = normalizeNodeName(data && (data.componentName || data.name), "");
    var wantedScope = currentProjectComponentScope;
    for (var i = 0; i < children.length; i++) {
      var node = children[i];
      if (node.type !== "COMPONENT") continue;
      if (node.getPluginData && node.getPluginData("wt_component_key") === key) {
        if (node.setPluginData && wantedScope && getNodeComponentScope(node) !== wantedScope) {
          node.setPluginData("wt_component_scope", wantedScope);
        }
        componentMasterRegistry[key] = node;
        return node;
      }
      if (wantedName && normalizeNodeName(node.name, "") === wantedName) {
        if (wantedScope && getNodeComponentScope(node) !== wantedScope) continue;
        if (node.setPluginData) {
          node.setPluginData("wt_component_key", key);
          node.setPluginData("wt_component_scope", wantedScope || "");
        }
        componentMasterRegistry[key] = node;
        return node;
      }
    }
    return null;
  });
}

function placeMasterComponent(master) {
  return getComponentPageAsync().then(function(page) {
    if (!master.parent || master.parent.id !== page.id) {
      page.appendChild(master);
    }
    master.x = componentLibraryNextX;
    master.y = componentLibraryNextY;
    componentLibraryRowH = Math.max(componentLibraryRowH, master.height || 0);
    componentLibraryNextX += (master.width || 0) + 120;
    if (componentLibraryNextX > (componentLibraryStartX + 3200)) {
      componentLibraryNextX = componentLibraryStartX;
      componentLibraryNextY += componentLibraryRowH + 120;
      componentLibraryRowH = 0;
    }
    return master;
  });
}

function createMasterFromDefinition(data) {
  var master = figma.createComponent();
  var renderedChildren = [];
  applyContainerStyling(master, {
    type: data.type,
    componentKey: data.componentKey,
    componentName: data.componentName,
    comp_type: data.comp_type,
    name: data.componentName || data.name || "Reusable Component",
    x: 0,
    y: 0,
    width: data.width || 100,
    height: data.height || 48,
    backgroundColor: data.backgroundColor,
    cornerRadius: data.cornerRadius,
    borderColor: data.borderColor,
    borderWidth: data.borderWidth,
    clipsContent: data.clipsContent,
    layoutMode: data.layoutMode,
    itemSpacing: data.itemSpacing,
    paddingLeft: data.paddingLeft,
    paddingRight: data.paddingRight,
    paddingTop: data.paddingTop,
    paddingBottom: data.paddingBottom,
    primaryAxisAlignItems: data.primaryAxisAlignItems,
    counterAxisAlignItems: data.counterAxisAlignItems,
    primaryAxisSizingMode: data.primaryAxisSizingMode,
    counterAxisSizingMode: data.counterAxisSizingMode,
    opacity: data.opacity,
  });

  var renderPromise;
  if (data.type === "button") {
    renderPromise = Promise.resolve().then(function() {
      var labelData = {
        type: "text",
        name: normalizeNodeName((data.name || "Button") + " Label", "Button Label"),
        x: 16,
        y: 12,
        width: Math.max(1, (data.width || 160) - 32),
        text: data.text || "Button",
        fontSize: data.fontSize || 16,
        fontWeight: data.fontWeight || "medium",
        color: data.textColor || "#FFFFFF",
        textAlign: "CENTER",
        styleGroupRole: "button-text",
        styleSourceData: data,
      };
      return createText(labelData);
    }).then(function(label) {
      master.appendChild(label);
      applyChildAutoLayoutProps(label, { layoutAlign: "CENTER" }, master);
      if (!master.layoutMode || master.layoutMode === "NONE") {
        label.x = Math.max(0, Math.floor((master.width - label.width) / 2));
        label.y = Math.max(0, Math.floor((master.height - label.height) / 2));
      }
    });
  } else {
    renderPromise = renderChildren(data.children || [], master).then(function(entries) {
      renderedChildren = entries || [];
    });
  }

  return renderPromise.then(function() {
    inferAndApplyAutoLayout(master, data, renderedChildren);
    return master;
  });
}

function ensureMasterComponent(data) {
  var key = componentRegistryKey(data);
  return findExistingMasterComponent(key, data).then(function(existing) {
    if (existing) return existing;

    return createMasterFromDefinition(data).then(function(master) {
      master.name = normalizeNodeName(data.componentName || data.name, "Reusable Component");
      if (master.setPluginData) {
        master.setPluginData("wt_component_key", key);
        master.setPluginData("wt_component_scope", currentProjectComponentScope || "");
      }
      return placeMasterComponent(master).then(function(placedMaster) {
        componentMasterRegistry[key] = placedMaster;
        return placedMaster;
      });
    });
  });
}

function createReusableComponentInstance(data, parent) {
  return ensureMasterComponent(data).then(function(master) {
    var instance = master.createInstance();
    instance.name = normalizeNodeName(data.name || data.componentName || master.name, master.name);
    instance.x = data.x || 0;
    instance.y = data.y || 0;
    var targetW = Math.max(1, data.width || master.width || 1);
    var targetH = Math.max(1, data.height || master.height || 1);
    if (Math.abs(instance.width - targetW) > 1 || Math.abs(instance.height - targetH) > 1) {
      try { instance.resize(targetW, targetH); } catch (_err) {}
    }
    if (data.opacity !== undefined && data.opacity !== null) instance.opacity = data.opacity;
    applyInstanceOverrides(instance, data);
    return instance;
  });
}

// ── Text ──────────────────────────────────────────────────────────
function createText(data) {
  var text = figma.createText();
  text.name = normalizeNodeName(data.name, "Text");
  text.x = data.x || 0;
  text.y = data.y || 0;
  var targetWidth  = data.width;
  var targetHeight = typeof data.height === "number" ? Math.max(1, data.height) : 0;

  var styleMap = { "bold": "Bold", "semibold": "Semi Bold", "medium": "Medium", "regular": "Regular" };
  var fw = (data.fontWeight || "regular").toLowerCase();
  var fontStyle = styleMap[fw] || "Regular";
  text.fontName = { family: "Inter", style: fontStyle };
  text.characters = String(data.text || "");
  text.fontSize = data.fontSize || 16;
  text.fills = [{ type: "SOLID", color: hexToRgb(data.color || "#000000") }];
  if (data.styleSourceData || data.styleGroupRole) {
    bindPaintStyle(text, data, "text", data.color || "#000000");
  }

  if (data.lineHeight && typeof data.lineHeight === "number") {
    text.lineHeight = { value: data.lineHeight * 100, unit: "PERCENT" };
  }
  if (data.letterSpacing && typeof data.letterSpacing === "number") {
    text.letterSpacing = { value: data.letterSpacing, unit: "PIXELS" };
  }

  var align = (data.textAlign || data.textAlignHorizontal || "").toUpperCase();
  if (align === "CENTER" || align === "RIGHT" || align === "JUSTIFIED") {
    text.textAlignHorizontal = align;
  }

  var naturalWidth = Math.max(1, text.width || 1);
  var textValue    = String(data.text || "");
  var hasLineBreak = textValue.indexOf("\n") !== -1;
  if (targetWidth && targetWidth < naturalWidth * 0.82 && naturalWidth < 640) {
    targetWidth = Math.min(640, Math.ceil(naturalWidth + 16));
  }

  if (targetWidth && !hasLineBreak && textValue.length <= 28 && targetWidth < naturalWidth) {
    targetWidth = Math.min(520, Math.ceil(naturalWidth + 12));
  }

  if (targetWidth) {
    text.textAutoResize = "HEIGHT";
    text.resize(targetWidth, Math.max(1, targetHeight || text.height || 20));
    if (!hasLineBreak && text.height > (text.fontSize || 16) * 1.75 && naturalWidth < 720) {
      var widened = Math.min(720, Math.ceil(naturalWidth + 20));
      if (widened > targetWidth) {
        text.resize(widened, Math.max(1, targetHeight || text.height || 20));
      }
    }
  } else {
    text.textAutoResize = "WIDTH_AND_HEIGHT";
  }

  if (targetWidth && targetHeight) {
    var minFontSize = Math.max(10, Math.min(data.fontSize || 16, 12));
    while (text.height > targetHeight && text.fontSize > minFontSize) {
      text.fontSize = Math.max(minFontSize, text.fontSize - 1);
      text.resize(targetWidth, targetHeight);
    }
  }

  if (targetHeight && text.textAlignVertical !== undefined) {
    text.textAlignVertical = "CENTER";
  }

  applyNodeSizingProps(text, data, null);

  return text;
}

// ── Image ─────────────────────────────────────────────────────────
function createImage(data) {
  var rect = figma.createRectangle();
  rect.name = normalizeNodeName(data.name, "Image");
  rect.x = data.x || 0;
  rect.y = data.y || 0;
  rect.resize(Math.max(1, data.width || 400), Math.max(1, data.height || 300));
  if (data.borderRadius || data.cornerRadius) {
    rect.cornerRadius = data.borderRadius || data.cornerRadius || 0;
  }

  rect.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || "#CCCCCC") }];
  applyNodeSizingProps(rect, data, null);

  if (!data.src || data.src === "PLACEHOLDER") {
    return Promise.resolve(rect);
  }

  return new Promise(function(resolve) {
    var msgId = "img_" + Date.now() + "_" + Math.floor(Math.random() * 9999);

    var timeout = setTimeout(function() {
      console.warn("[IMAGE] Timeout fetching:", data.src);
      resolve(rect);
    }, 15000);

    function handler(response) {
      if (!response || response.type !== "image_data" || response.msgId !== msgId) return;
      clearTimeout(timeout);
      figma.ui.onmessage = originalHandler;

      if (response.error) {
        console.warn("[IMAGE] Failed:", data.src, response.error);
        resolve(rect);
        return;
      }

      try {
        var bytes = new Uint8Array(response.bytes);
        var imgHash = figma.createImage(bytes).hash;
        rect.fills = [{ type: "IMAGE", scaleMode: "FILL", imageHash: imgHash }];
      } catch (e) {
        console.warn("[IMAGE] createImage failed:", e.message);
      }
      resolve(rect);
    }

    var originalHandler = figma.ui.onmessage;
    figma.ui.onmessage = function(msg) {
      if (msg && msg.type === "image_data" && msg.msgId === msgId) {
        handler(msg);
      } else if (originalHandler) {
        originalHandler(msg);
      }
    };

    figma.ui.postMessage({ type: "fetch_image", src: data.src, msgId: msgId });
  });
}

// ── Button ────────────────────────────────────────────────────────
function createButton(data) {
  var frame = figma.createFrame();
  var hasExplicitWidth = typeof data.width === "number" && data.width > 0;
  var hasExplicitHeight = typeof data.height === "number" && data.height > 0;
  var paddingLeft = data.paddingLeft !== undefined ? data.paddingLeft : 20;
  var paddingRight = data.paddingRight !== undefined ? data.paddingRight : 20;
  var paddingTop = data.paddingTop !== undefined ? data.paddingTop : 12;
  var paddingBottom = data.paddingBottom !== undefined ? data.paddingBottom : 12;
  applyContainerStyling(frame, {
    type: "button",
    componentKey: data.componentKey,
    componentName: data.componentName,
    comp_type: data.comp_type,
    name: data.name || "Button",
    x: data.x || 0,
    y: data.y || 0,
    width: Math.max(1, data.width || 120),
    height: Math.max(1, data.height || 44),
    backgroundColor: data.backgroundColor || "#6366F1",
    cornerRadius: data.cornerRadius || 8,
    borderColor: data.borderColor,
    borderWidth: data.borderWidth,
    clipsContent: true,
    layoutMode: data.layoutMode || "HORIZONTAL",
    primaryAxisAlignItems: data.primaryAxisAlignItems || "CENTER",
    counterAxisAlignItems: data.counterAxisAlignItems || "CENTER",
    itemSpacing: data.itemSpacing || 0,
    paddingLeft: paddingLeft,
    paddingRight: paddingRight,
    paddingTop: paddingTop,
    paddingBottom: paddingBottom,
    primaryAxisSizingMode: data.primaryAxisSizingMode || (hasExplicitWidth ? "FIXED" : "AUTO"),
    counterAxisSizingMode: data.counterAxisSizingMode || (hasExplicitHeight ? "FIXED" : "AUTO"),
  });
  if (data.backgroundColor === "transparent") frame.fills = [];

  var label = createText({
    type: "text",
    name: normalizeNodeName((data.name || "Button") + " Label", "Button Label"),
    text: String(data.text || "Button"),
    width: hasExplicitWidth ? Math.max(1, frame.width - paddingLeft - paddingRight) : undefined,
    height: hasExplicitHeight ? Math.max(1, frame.height - paddingTop - paddingBottom) : undefined,
    fontSize: data.fontSize || 16,
    fontWeight: data.fontWeight || "medium",
    color: data.textColor || "#FFFFFF",
    textAlign: "CENTER",
    layoutSizingHorizontal: hasExplicitWidth ? "FILL" : "HUG",
    layoutSizingVertical: "HUG",
    styleGroupRole: "button-text",
    styleSourceData: data,
  });
  var minButtonWidth = Math.max(1, Math.ceil(label.width + paddingLeft + paddingRight));
  if (hasExplicitWidth && frame.width < minButtonWidth) {
    frame.resize(minButtonWidth, frame.height);
  }
  if (!hasExplicitWidth) {
    try { frame.primaryAxisSizingMode = "AUTO"; } catch (_err) {}
  }
  if (!hasExplicitHeight) {
    try { frame.counterAxisSizingMode = "AUTO"; } catch (_err) {}
  }
  frame.appendChild(label);
  applyChildAutoLayoutProps(label, { layoutAlign: "CENTER", layoutGrow: hasExplicitWidth ? 1 : 0 }, frame);
  try {
    label.textAlignHorizontal = "CENTER";
    if (label.textAlignVertical !== undefined) label.textAlignVertical = "CENTER";
  } catch (_labelErr) {}
  applyNodeMinSizeProps(frame, {
    minWidth: data.minWidth !== undefined ? data.minWidth : minButtonWidth,
    minHeight: data.minHeight !== undefined ? data.minHeight : Math.max(36, data.height || 44),
  });
  applyNodeSizingProps(frame, data, null);
  return frame;
}

// ── Ellipse ───────────────────────────────────────────────────────
function createEllipse(data) {
  var el = figma.createEllipse();
  el.name = normalizeNodeName(data.name, "Ellipse");
  el.x = data.x || 0;
  el.y = data.y || 0;
  el.resize(Math.max(1, data.width || 100), Math.max(1, data.height || 100));
  if (data.backgroundColor === "transparent") {
    el.fills = [];
  } else {
    el.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || data.color || "#CCCCCC") }];
  }
  bindPaintStyle(el, data, "fill", data.backgroundColor || data.color || "#CCCCCC");
  if (data.borderColor) {
    el.strokes = [{ type: "SOLID", color: hexToRgb(data.borderColor) }];
    el.strokeWeight = data.borderWidth || 1;
    bindPaintStyle(el, data, "stroke", data.borderColor);
  }
  if (data.opacity !== undefined && data.opacity !== null) el.opacity = data.opacity;
  if (data.rotation) el.rotation = data.rotation;
  applyNodeSizingProps(el, data, null);
  return el;
}

// ── Line ──────────────────────────────────────────────────────────
function createLine(data) {
  var line = figma.createLine();
  line.name = normalizeNodeName(data.name, "Line");
  line.x = data.x || 0;
  line.y = data.y || 0;
  line.resize(Math.max(1, data.width || 100), 0);
  line.strokes = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || data.color || "#333333") }];
  line.strokeWeight = data.strokeWeight || 1;
  bindPaintStyle(line, data, "stroke", data.backgroundColor || data.color || "#333333");
  if (data.opacity !== undefined && data.opacity !== null) line.opacity = data.opacity;
  if (data.rotation) line.rotation = data.rotation;
  applyNodeSizingProps(line, data, null);
  return line;
}

// ── Group ─────────────────────────────────────────────────────────
function createGroup(data, parent) {
  var groupChildren = data.children || [];
  if (groupChildren.length === 0) return Promise.resolve(null);

  var nodes = [];
  var promise = Promise.resolve();

  for (var i = 0; i < groupChildren.length; i++) {
    (function(child) {
      promise = promise.then(function() {
        return createNode(child, parent).then(function(node) {
          if (node) {
            parent.appendChild(node);
            nodes.push(node);
          }
        }).catch(function(e) {
          console.error("[GROUP] Child error '" + (child && child.name) + "':", e.message || e);
        });
      });
    })(groupChildren[i]);
  }

  return promise.then(function() {
    if (nodes.length === 0) return null;
    var group = figma.group(nodes, parent);
    group.name = normalizeNodeName(data.name, "Group");
    return group;
  });
}

// ─────────────────────────────────────────────────────────────────
// EXTRACT: Read LIVE Figma canvas → plain JSON  (for React export)
// ─────────────────────────────────────────────────────────────────

function handleExtractFigmaTree() {
  var currentPage = figma.currentPage;

  currentPage.loadAsync().then(function() {

    var selectedFrames = figma.currentPage.selection.filter(function(n) {
      return n.type === "FRAME";
    });

    var frames;
    if (selectedFrames.length > 0) {
      frames = selectedFrames.slice().sort(function(a, b) { return a.x - b.x; });
      console.log("[EXTRACT] Using " + frames.length + " selected frame(s)");
    } else {
      frames = currentPage.children
        .filter(function(n) { return n.type === "FRAME"; })
        .sort(function(a, b) { return a.x - b.x; });
      console.log("[EXTRACT] No selection — using all " + frames.length + " frame(s)");
    }

    if (frames.length === 0) {
      figma.ui.postMessage({
        type: "extract_error",
        error: "No frames found. Select the frames you want to export, or make sure your designs are inside Frames."
      });
      return;
    }

    figma.ui.postMessage({ type: "extract_start", total: frames.length });

    var result = [];
    var assetExportQueue = []; // holds { nodeId, name, width, height, node }

    var promise = buildAssetMap().then(function(_assetMap) {
      var chain = Promise.resolve();
      frames.forEach(function(frame) {
        chain = chain.then(function() {

          // ── Parse ALL hints from frame name ──────────────────────
          var parsed    = parseFrameName(frame.name);
          var cleanName = parsed.cleanName;
          var navHint   = parsed.navHint;
          var descHint  = parsed.descHint;
          var compType  = parsed.compType;   // @type value
          var parentRef = parsed.parentRef;  // @parent value
          var defaultTab = parsed.defaultTab; // @default value

          if (navHint)   console.log("[EXTRACT] @nav    for '" + cleanName + "':", navHint);
          if (descHint)  console.log("[EXTRACT] @desc   for '" + cleanName + "':", descHint);
          if (compType)  console.log("[EXTRACT] @type   for '" + cleanName + "':", compType);
          if (parentRef) console.log("[EXTRACT] @parent for '" + cleanName + "':", parentRef);
          if (defaultTab) console.log("[EXTRACT] @default for '" + cleanName + "':", defaultTab);

          // ── Collect @svg / @image asset nodes inside this frame ──
          collectAssetNodes(frame, assetExportQueue);

          var frameData = extractNode(frame, frame.x, frame.y, _assetMap);
          frameData.x = 0;
          frameData.y = 0;

          result.push({
            name:        cleanName,
            frame:       frameData,
            nav_hint:    navHint,
            desc_hint:   descHint,
            comp_type:   compType,    // NEW — component classification
            parent_ref:  parentRef,   // NEW — parent component name
            default_tab: defaultTab,  // NEW — default active tab
            node_id:     frame.id,    // NEW — Figma node ID (for asset export)
            width:       frame.width,
            height:      frame.height,
          });

          figma.ui.postMessage({ type: "extract_page_done", page_name: cleanName });
          return Promise.resolve();
        });
      });
      return chain;
    });

    // ── After all frames extracted:
    //   Step 1 — send extract_complete with total_assets count
    //   Step 2 — stream asset bytes (asset_exported messages)
    //   Step 3 — send asset_export_complete → ui.html starts React export
    promise = promise.then(function() {
      var rootName  = figma.root.name || "";
      var isGeneric = rootName === "" || rootName.toLowerCase() === "untitled" || rootName.toLowerCase() === "draft";
      var projectTitle = isGeneric
        ? (result.length > 0 ? result[0].name + " Site" : "My App")
        : rootName;

      // Send page data immediately — total_assets tells ui.html to wait
      figma.ui.postMessage({
        type:          "extract_complete",
        pages:         result,
        project_title: projectTitle,
        file_key:      figma.fileKey || "",
        total_assets:  assetExportQueue.length,
      });

      // Now stream asset bytes — asset_export_complete fires when all done
      // ui.html will call runExportReact() only on asset_export_complete
      return exportAssetNodes(assetExportQueue);

    }).catch(function(err) {
      figma.ui.postMessage({ type: "extract_error", error: String(err) });
    });

  }).catch(function(err) {
    figma.ui.postMessage({ type: "extract_error", error: String(err) });
  });
}

// ─────────────────────────────────────────────────────────────────
// EXTRACT SPECIFIC FRAMES BY ID
// Called by the Export Panel with the exact frame IDs the user
// checked — completely ignores Figma's live selection.
//
// Walk order: sort by x position (left → right) so pages come
// out in a natural reading order.
// ─────────────────────────────────────────────────────────────────

function handleExtractFramesByIds(frameIds, projectTitleHint) {
  if (!frameIds || frameIds.length === 0) {
    figma.ui.postMessage({
      type:  "extract_error",
      error: "No frame IDs provided.",
    });
    return;
  }

  figma.currentPage.loadAsync().then(function() {

    // Build id → node map for ALL top-level frames on the page
    var idMap = {};
    figma.currentPage.children.forEach(function(n) {
      if (n.type === "FRAME") idMap[n.id] = n;
    });

    // Pick only the requested frames, in x-order
    var frames = frameIds
      .map(function(id) { return idMap[id]; })
      .filter(Boolean)
      .sort(function(a, b) { return a.x - b.x; });

    if (frames.length === 0) {
      figma.ui.postMessage({
        type:  "extract_error",
        error: "None of the selected frame IDs were found on this page. " +
               "Make sure you haven't switched pages since opening the Export panel.",
      });
      return;
    }

    console.log("[EXTRACT_BY_IDS] Exporting " + frames.length + " frame(s):", frames.map(function(f){ return f.name; }));

    figma.ui.postMessage({ type: "extract_start", total: frames.length });

    var result          = [];
    var assetExportQueue = [];

    var promise = buildAssetMap().then(function(_assetMap) {
      var chain = Promise.resolve();
      frames.forEach(function(frame) {
        chain = chain.then(function() {
          var parsed     = parseFrameName(frame.name);
          var cleanName  = parsed.cleanName;

          if (parsed.navHint)    console.log("[EXTRACT_BY_IDS] @nav    for '" + cleanName + "':", parsed.navHint);
          if (parsed.descHint)   console.log("[EXTRACT_BY_IDS] @desc   for '" + cleanName + "':", parsed.descHint);
          if (parsed.compType)   console.log("[EXTRACT_BY_IDS] @type   for '" + cleanName + "':", parsed.compType);

          collectAssetNodes(frame, assetExportQueue);

          var frameData = extractNode(frame, frame.x, frame.y, _assetMap);
          frameData.x   = 0;
          frameData.y   = 0;

          result.push({
            name:        cleanName,
            frame:       frameData,
            nav_hint:    parsed.navHint,
            desc_hint:   parsed.descHint,
            comp_type:   parsed.compType,
            parent_ref:  parsed.parentRef,
            default_tab: parsed.defaultTab,
            node_id:     frame.id,
            width:       frame.width,
            height:      frame.height,
          });

          figma.ui.postMessage({ type: "extract_page_done", page_name: cleanName });
          return Promise.resolve();
        });
      });
      return chain;
    });

    promise = promise.then(function() {
      // Use hint from Export Panel if available, else derive from file/frame names
      var rootName     = figma.root.name || "";
      var isGeneric    = !rootName || rootName.toLowerCase() === "untitled" || rootName.toLowerCase() === "draft";
      var projectTitle = projectTitleHint && projectTitleHint !== "My App"
        ? projectTitleHint
        : (isGeneric
            ? (result.length > 0 ? result[0].name + " App" : "My App")
            : rootName);

      figma.ui.postMessage({
        type:          "extract_complete",
        pages:         result,
        project_title: projectTitle,
        file_key:      figma.fileKey || "",
        total_assets:  assetExportQueue.length,
      });

      return exportAssetNodes(assetExportQueue);

    }).catch(function(err) {
      figma.ui.postMessage({ type: "extract_error", error: String(err) });
    });

  }).catch(function(err) {
    figma.ui.postMessage({ type: "extract_error", error: String(err) });
  });
}

function getProjectTitleFromRoot(fallback) {
  var rootName = figma.root.name || "";
  var isGeneric = !rootName || rootName.toLowerCase() === "untitled" || rootName.toLowerCase() === "draft";
  if (!isGeneric) return rootName;
  return fallback || "My App";
}

function getTopLevelPageFrame(node) {
  var cur = node;
  while (cur) {
    if (cur.type === "FRAME" && cur.parent && cur.parent.type === "PAGE") return cur;
    cur = cur.parent;
  }
  return null;
}

function pushUnique(list, value, limit) {
  if (!value && value !== 0) return;
  var normalized = String(value).trim();
  if (!normalized) return;
  if (list.indexOf(normalized) !== -1) return;
  list.push(normalized);
  if (limit && list.length > limit) list.length = limit;
}

function normalizeSummaryText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function looksLikeExtractedButton(node) {
  if (!node) return false;
  var t = String(node.type || "").toLowerCase();
  var nameLower = String(node.name || "").toLowerCase();
  var w = Number(node.width || 0);
  var h = Number(node.height || 0);
  if (/\b(btn|button|cta|action|link|chip|tag|toggle|switch|menu|dropdown|select|trigger|open|launch|next|back|save|submit|cancel|confirm|delete|add|edit|create)\b/.test(nameLower)) {
    return true;
  }
  if ((t === "frame" || t === "component" || t === "instance" || t === "group") &&
      w > 20 && w < 500 && h > 16 && h < 120 &&
      (node.backgroundColor || node.borderColor || node.cornerRadius)) {
    return true;
  }
  return false;
}

function collectExtractedSummary(node, summary) {
  if (!node || !summary) return;

  var type = String(node.type || "").toLowerCase();
  summary.nodeCount += 1;
  summary.typeCounts[type] = (summary.typeCounts[type] || 0) + 1;

  if (node.backgroundColor) pushUnique(summary.colors, node.backgroundColor, 10);
  if (node.color) pushUnique(summary.colors, node.color, 10);

  var name = normalizeSummaryText(node.name || "");
  var inferredType = node.comp_type || inferComponentType(name);
  if (inferredType) pushUnique(summary.detectedComponents, inferredType, 16);

  var sectionPatterns = [
    ["navbar", "navbar"],
    ["header", "header"],
    ["hero", "hero"],
    ["sidebar", "sidebar"],
    ["footer", "footer"],
    ["table", "table"],
    ["form", "form"],
    ["modal", "modal"],
    ["drawer", "drawer"],
    ["chart", "chart"],
    ["tabs", "tabs"],
    ["card", "cards"],
    ["search", "search"],
    ["filter", "filters"],
    ["toolbar", "toolbar"],
    ["menu", "menu"],
  ];
  var loweredName = name.toLowerCase();
  for (var i = 0; i < sectionPatterns.length; i++) {
    if (loweredName.indexOf(sectionPatterns[i][0]) !== -1) {
      pushUnique(summary.detectedSections, sectionPatterns[i][1], 14);
    }
  }

  if (type === "text") {
    var textValue = normalizeSummaryText(node.text || "");
    if (textValue) {
      if (node.isNavLink) pushUnique(summary.navigationLinks, textValue.slice(0, 48), 12);
      else pushUnique(summary.textSamples, textValue.slice(0, 80), 16);
    }
  }

  if (looksLikeExtractedButton(node)) {
    var label = normalizeSummaryText(node.text || node.name || "");
    if (label) pushUnique(summary.buttonLabels, label.slice(0, 60), 12);
  }

  var children = node.children || [];
  for (var ci = 0; ci < children.length; ci++) {
    collectExtractedSummary(children[ci], summary);
  }
}

function buildAttachmentSummary(extractedNode, meta) {
  var summary = {
    description: "",
    feature_group: "",
    navigation: { layout: "", primary_links: [] },
    detected_sections: [],
    detected_components: [],
    button_labels: [],
    text_samples: [],
    colors: [],
    node_count: 0,
    type_counts: {},
  };

  var raw = {
    detectedSections: [],
    detectedComponents: [],
    navigationLinks: [],
    buttonLabels: [],
    textSamples: [],
    colors: [],
    nodeCount: 0,
    typeCounts: {},
  };
  collectExtractedSummary(extractedNode, raw);

  summary.detected_sections = raw.detectedSections;
  summary.detected_components = raw.detectedComponents;
  summary.button_labels = raw.buttonLabels;
  summary.text_samples = raw.textSamples;
  summary.colors = raw.colors;
  summary.node_count = raw.nodeCount;
  summary.type_counts = raw.typeCounts;
  summary.navigation = {
    layout: raw.detectedSections.indexOf("sidebar") !== -1 ? "sidebar" : (raw.navigationLinks.length ? "topbar" : ""),
    primary_links: raw.navigationLinks.slice(0, 10),
  };

  var featureGroup = String(meta.name || "Selection").split(/[-–—>|/]/)[0].trim() || String(meta.name || "Selection");
  summary.feature_group = featureGroup;

  var descriptionBits = [];
  descriptionBits.push((meta.kind === "component" ? "Component" : "Page") + " '" + meta.name + "'");
  if (summary.detected_sections.length) descriptionBits.push("sections: " + summary.detected_sections.slice(0, 6).join(", "));
  if (summary.detected_components.length) descriptionBits.push("components: " + summary.detected_components.slice(0, 8).join(", "));
  if (summary.navigation.primary_links.length) descriptionBits.push("navigation: " + summary.navigation.primary_links.slice(0, 8).join(", "));
  if (summary.button_labels.length) descriptionBits.push("actions: " + summary.button_labels.slice(0, 6).join(", "));
  if (summary.text_samples.length) descriptionBits.push("content: " + summary.text_samples.slice(0, 6).join(", "));
  summary.description = descriptionBits.join(". ");

  return summary;
}

function compactAttachmentTree(node, budget) {
  if (!node || !budget || budget.remaining <= 0) return null;
  budget.remaining -= 1;

  var compact = {
    type: node.type,
    name: node.name || "",
    x: Math.round(node.x || 0),
    y: Math.round(node.y || 0),
    width: Math.round(node.width || 0),
    height: Math.round(node.height || 0),
  };

  if (node.text) compact.text = String(node.text).slice(0, 120);
  if (node.backgroundColor) compact.backgroundColor = node.backgroundColor;
  if (node.color) compact.color = node.color;
  if (node.cornerRadius !== undefined) compact.cornerRadius = node.cornerRadius;
  if (node.borderColor) compact.borderColor = node.borderColor;
  if (node.borderWidth) compact.borderWidth = node.borderWidth;
  if (node.layoutMode) {
    compact.layoutMode = node.layoutMode;
    compact.itemSpacing = node.itemSpacing || 0;
    compact.paddingLeft = node.paddingLeft || 0;
    compact.paddingRight = node.paddingRight || 0;
    compact.paddingTop = node.paddingTop || 0;
    compact.paddingBottom = node.paddingBottom || 0;
    if (node.primaryAxisSizingMode) compact.primaryAxisSizingMode = node.primaryAxisSizingMode;
    if (node.counterAxisSizingMode) compact.counterAxisSizingMode = node.counterAxisSizingMode;
  }
  if (typeof node.layoutGrow === "number") compact.layoutGrow = node.layoutGrow;
  if (node.layoutAlign) compact.layoutAlign = node.layoutAlign;
  if (node.layoutPositioning && node.layoutPositioning !== "AUTO") compact.layoutPositioning = node.layoutPositioning;
  if (node.comp_type) compact.comp_type = node.comp_type;
  if (node.isNavLink) compact.isNavLink = true;

  var children = node.children || [];
  if (children.length && budget.remaining > 0) {
    compact.children = [];
    for (var i = 0; i < children.length; i++) {
      if (budget.remaining <= 0) break;
      var child = compactAttachmentTree(children[i], budget);
      if (child) compact.children.push(child);
    }
  }

  return compact;
}

function handleExtractAttachmentSelection(msg) {
  var slot = msg && msg.slot ? msg.slot : "context_pages";
  var requestId = msg && msg.requestId ? msg.requestId : "";

  figma.currentPage.loadAsync().then(function() {
    var selection = figma.currentPage.selection || [];
    if (!selection.length) {
      figma.ui.postMessage({
        type: "attachment_selection_error",
        requestId: requestId,
        slot: slot,
        error: "Select at least one page or component in Figma first.",
      });
      return;
    }

    var nodes = [];
    var seen = {};
    var i;

    if (slot === "components") {
      for (i = 0; i < selection.length; i++) {
        var selectedNode = selection[i];
        if (!selectedNode || seen[selectedNode.id]) continue;
        seen[selectedNode.id] = true;
        nodes.push(selectedNode);
      }
    } else {
      for (i = 0; i < selection.length; i++) {
        var frame = getTopLevelPageFrame(selection[i]);
        if (!frame || seen[frame.id]) continue;
        seen[frame.id] = true;
        nodes.push(frame);
      }
    }

    if (!nodes.length) {
      figma.ui.postMessage({
        type: "attachment_selection_error",
        requestId: requestId,
        slot: slot,
        error: slot === "components"
          ? "The current selection does not contain any usable components."
          : "The current selection does not contain any top-level pages.",
      });
      return;
    }

    buildAssetMap().then(function(assetMap) {
      return nodes.map(function(node) {
        var parentFrame = slot === "components" ? getTopLevelPageFrame(node) : node;
        var parsed = parseFrameName(node.name || "");
        var cleanName = parsed.cleanName || node.name || "Selection";
        var extracted = extractNode(node, node.x, node.y, assetMap);
        var compactTree = compactAttachmentTree(extracted, { remaining: slot === "primary_page" ? 220 : 140 });
        var kind = slot === "components" ? "component" : "page";
        var summary = buildAttachmentSummary(extracted, { name: cleanName, kind: kind });

        return {
          nodeId: node.id,
          nodeName: cleanName,
          rawName: node.name || cleanName,
          nodeType: node.type,
          width: Math.round(node.width || 0),
          height: Math.round(node.height || 0),
          parentFrameId: parentFrame ? parentFrame.id : "",
          parentFrameName: parentFrame ? parseFrameName(parentFrame.name || "").cleanName : "",
          summary: summary,
          tree: compactTree,
        };
      });
    }).then(function(items) {
      figma.ui.postMessage({
        type: "attachment_selection_ready",
        requestId: requestId,
        slot: slot,
        projectTitle: getProjectTitleFromRoot(items[0] ? items[0].nodeName + " App" : "My App"),
        items: items,
      });
    }).catch(function(err) {
      figma.ui.postMessage({
        type: "attachment_selection_error",
        requestId: requestId,
        slot: slot,
        error: String(err),
      });
    });
  }).catch(function(err) {
    figma.ui.postMessage({
      type: "attachment_selection_error",
      requestId: requestId,
      slot: slot,
      error: String(err),
    });
  });
}

function collectAssetNodes(node, queue) {
  if (!node) return;

  var nameLower = (node.name || "").toLowerCase().trim();

  // Check if this node is tagged as an asset
  if (nameLower.indexOf("@svg") === 0 || nameLower.indexOf("@image") === 0) {
    var assetType = nameLower.indexOf("@svg") === 0 ? "svg" : "image";

    // Extract the label after @svg/@image — e.g. "@svg company logo" → "company logo"
    var label = node.name
      .replace(/^@svg\s*/i, "")
      .replace(/^@image\s*/i, "")
      .trim()
      .replace(/\s+/g, "-")
      .toLowerCase()
      || ("asset-" + node.id);

    queue.push({
      nodeId:    node.id,
      assetType: assetType,
      label:     label,
      width:     Math.round(node.width  || 0),
      height:    Math.round(node.height || 0),
      node:      node,       // keep reference for exportAsync
      fileName:  label + ".png",
    });

    console.log("[ASSET] Found " + assetType + " node: '" + label + "' (" + node.width + "×" + node.height + ")");
    // Don't recurse into asset nodes — we export the whole thing as one image
    return;
  }

  // Recurse into children
  var children = node.children || [];
  for (var i = 0; i < children.length; i++) {
    collectAssetNodes(children[i], queue);
  }
}

// ─────────────────────────────────────────────────────────────────
// ASSET MAP BUILDER
// Scans ALL pages to find every @svg/@image tagged node and builds
// a map of  baseName → assetLabel  so non-tagged nodes with the
// same name are automatically treated as the same asset.
// ─────────────────────────────────────────────────────────────────

function buildAssetMap() {
  var map = {}; // baseName (original case) → assetLabel (kebab slug)

  function scanNode(node) {
    if (!node) return;
    var nameRaw   = node.name || "";
    var nameLower = nameRaw.toLowerCase().trim();

    if (nameLower.indexOf("@svg") === 0 || nameLower.indexOf("@image") === 0) {
      var baseName = nameRaw
        .replace(/^@svg\s*/i, "")
        .replace(/^@image\s*/i, "")
        .trim();

      var assetLabel = baseName
        .replace(/\s+/g, "-")
        .toLowerCase() || ("asset-" + node.id);

      if (baseName && !map[baseName]) {
        map[baseName] = assetLabel;
        console.log("[ASSET_MAP] Registered: '" + baseName + "' → '" + assetLabel + "'");
      }
    }

    var children = node.children || [];
    for (var i = 0; i < children.length; i++) {
      scanNode(children[i]);
    }
  }

  var pages = figma.root.children || [];
  var loadPromises = pages.map(function(page) {
    return page.loadAsync().catch(function() {
      return null;
    });
  });

  return Promise.all(loadPromises).then(function() {
    pages.forEach(function(page) {
      var children = page.children || [];
      children.forEach(function(node) {
        scanNode(node);
      });
    });
    console.log("[ASSET_MAP] Built map with " + Object.keys(map).length + " asset(s).");
    return map;
  });
}

// ─────────────────────────────────────────────────────────────────
// ASSET EXPORTER
// Calls exportAsync() on each queued asset node at 1x (exact Figma size)
// Sends bytes to ui.html to be bundled into the zip
// ─────────────────────────────────────────────────────────────────

function exportAssetNodes(queue) {
  if (queue.length === 0) {
    console.log("[ASSET] No asset nodes found.");
    figma.ui.postMessage({ type: "asset_export_complete", total: 0 });
    return Promise.resolve();
  }

  console.log("[ASSET] Exporting " + queue.length + " asset node(s) at 2x...");
  figma.ui.postMessage({ type: "asset_export_start", total: queue.length });

  var promise = Promise.resolve();

  queue.forEach(function(asset) {
    promise = promise.then(function() {
      // 2x scale = high-resolution screenshot matching design proportions
      return asset.node.exportAsync({
        format:     "PNG",
        constraint: { type: "SCALE", value: 2 },
      }).then(function(bytes) {
        var kb = Math.round(bytes.length / 1024);
        console.log("[ASSET] Exported '" + asset.label + "' " + asset.width + "x" + asset.height + " 2x — " + kb + "KB");

        figma.ui.postMessage({
          type:      "asset_exported",
          fileName:  asset.fileName,
          assetType: asset.assetType,
          label:     asset.label,
          width:     asset.width,   // 1x design-space width (for CSS)
          height:    asset.height,  // 1x design-space height (for CSS)
          bytes:     Array.from(bytes),
        });

        return Promise.resolve();
      }).catch(function(err) {
        console.warn("[ASSET] Export failed for '" + asset.label + "':", err.message || err);
        figma.ui.postMessage({
          type:     "asset_export_failed",
          fileName: asset.fileName,
          label:    asset.label,
          error:    String(err.message || err),
        });
        return Promise.resolve();
      });
    });
  });

  return promise.then(function() {
    figma.ui.postMessage({ type: "asset_export_complete", total: queue.length });
    console.log("[ASSET] All asset exports complete.");
  });
}

// ─────────────────────────────────────────────────────────────────
// NODE EXTRACTOR  —  walk live Figma tree → plain JSON
// ─────────────────────────────────────────────────────────────────

function extractNode(node, offsetX, offsetY, assetMap) {
  if (!node) return null;
  if (node.visible === false || node.opacity === 0) return null;

  var ox = offsetX || 0;
  var oy = offsetY || 0;

  var base = {
    type:    node.type.toLowerCase(),
    name:    node.name,
    x:       Math.round((node.x || 0) - ox),
    y:       Math.round((node.y || 0) - oy),
    width:   Math.round(node.width  || 0),
    height:  Math.round(node.height || 0),
    opacity: (node.opacity !== undefined) ? node.opacity : 1,
    visible: true,
  };

  // ── Mark asset nodes so exporter.py renders them correctly ──
  var nameLower = (node.name || "").toLowerCase().trim();
  if (nameLower.indexOf("@svg") === 0 || nameLower.indexOf("@image") === 0) {
    var assetLabel = node.name
      .replace(/^@svg\s*/i, "")
      .replace(/^@image\s*/i, "")
      .trim()
      .replace(/\s+/g, "-")
      .toLowerCase()
      || ("asset-" + node.id);

    base.isAsset    = true;
    base.assetLabel = assetLabel;
    base.assetFile  = assetLabel + ".png";
    base.type       = "asset_image";
    // Return early — don't recurse, entire node exported as one PNG
    return base;
  }

  // ── Auto-match: non-tagged node whose name matches a registered asset ──
  var nodeNameRaw = node.name || "";
  if (assetMap && assetMap[nodeNameRaw]) {
    var matchedLabel = assetMap[nodeNameRaw];
    base.isAsset    = true;
    base.assetLabel = matchedLabel;
    base.assetFile  = matchedLabel + ".png";
    base.type       = "asset_image";
    console.log("[ASSET_MAP] Auto-matched '" + nodeNameRaw + "' → '" + matchedLabel + "'");
    return base;
  }

  var t = node.type;

  // ── Container types ──────────────────────────────────────────
  if (t === "FRAME" || t === "COMPONENT" || t === "INSTANCE" || t === "GROUP") {
    if (node.fills && node.fills.length > 0) {
      var fill = node.fills[0];
      var fillVisible = (fill.visible !== false) && ((fill.opacity === undefined) || fill.opacity > 0);
      if (fillVisible && fill.type === "SOLID") {
        base.backgroundColor = rgbToHex(fill.color);
      } else if (fillVisible && fill.type === "IMAGE" && fill.imageHash) {
        base.imageFill       = true;
        base.imageHash       = fill.imageHash;
        base.backgroundColor = "#CCCCCC";
      }
    }

    if (typeof node.cornerRadius === "number") base.cornerRadius = node.cornerRadius;
    if (typeof node.clipsContent === "boolean") base.clipsContent = node.clipsContent;

    if (node.strokes && node.strokes.length > 0) {
      var s = node.strokes[0];
      if (s.type === "SOLID" && s.visible !== false && (s.opacity === undefined || s.opacity > 0)) {
        if (typeof node.strokeTopWeight === "number" || typeof node.strokeBottomWeight === "number" ||
            typeof node.strokeLeftWeight === "number" || typeof node.strokeRightWeight === "number") {
          if ((node.strokeTopWeight    || 0) > 0) { base.borderTopColor    = rgbToHex(s.color); base.borderTopWidth    = node.strokeTopWeight; }
          if ((node.strokeBottomWeight || 0) > 0) { base.borderBottomColor = rgbToHex(s.color); base.borderBottomWidth = node.strokeBottomWeight; }
          if ((node.strokeLeftWeight   || 0) > 0) { base.borderLeftColor   = rgbToHex(s.color); base.borderLeftWidth   = node.strokeLeftWeight; }
          if ((node.strokeRightWeight  || 0) > 0) { base.borderRightColor  = rgbToHex(s.color); base.borderRightWidth  = node.strokeRightWeight; }
        } else if ((node.strokeWeight || 0) > 0) {
          base.borderColor = rgbToHex(s.color);
          base.borderWidth = node.strokeWeight;
        }
      }
    }

    // ── Strokes on container nodes ────────────────────────────
    if (node.strokes && node.strokes.length > 0 && node.strokes[0].type === "SOLID") {
      var sc = rgbToHex(node.strokes[0].color);
      if (typeof node.strokeTopWeight    === "number" ||
          typeof node.strokeBottomWeight === "number" ||
          typeof node.strokeLeftWeight   === "number" ||
          typeof node.strokeRightWeight  === "number") {
        if (node.strokeTopWeight    > 0) { base.borderTopColor    = sc; base.borderTopWidth    = node.strokeTopWeight; }
        if (node.strokeBottomWeight > 0) { base.borderBottomColor = sc; base.borderBottomWidth = node.strokeBottomWeight; }
        if (node.strokeLeftWeight   > 0) { base.borderLeftColor   = sc; base.borderLeftWidth   = node.strokeLeftWeight; }
        if (node.strokeRightWeight  > 0) { base.borderRightColor  = sc; base.borderRightWidth  = node.strokeRightWeight; }
      } else {
        base.borderColor = rgbToHex(node.strokes[0].color);
        base.borderWidth = node.strokeWeight || 1;
      }
    }

    // ── Auto-layout (flex) props ──────────────────────────────
    if (node.layoutMode && node.layoutMode !== "NONE") {
      base.layoutMode              = node.layoutMode;              // "HORIZONTAL" | "VERTICAL"
      base.primaryAxisAlignItems   = node.primaryAxisAlignItems   || "MIN";  // MIN | CENTER | MAX | SPACE_BETWEEN
      base.counterAxisAlignItems   = node.counterAxisAlignItems   || "MIN";
      base.itemSpacing             = typeof node.itemSpacing       === "number" ? node.itemSpacing : 0;
      base.paddingTop              = typeof node.paddingTop        === "number" ? node.paddingTop    : 0;
      base.paddingBottom           = typeof node.paddingBottom     === "number" ? node.paddingBottom : 0;
      base.paddingLeft             = typeof node.paddingLeft       === "number" ? node.paddingLeft   : 0;
      base.paddingRight            = typeof node.paddingRight      === "number" ? node.paddingRight  : 0;
      if (node.primaryAxisSizingMode) base.primaryAxisSizingMode = node.primaryAxisSizingMode;
      if (node.counterAxisSizingMode) base.counterAxisSizingMode = node.counterAxisSizingMode;
    }
    if (typeof node.layoutGrow === "number") {
      base.layoutGrow = node.layoutGrow;
    }
    if (node.layoutAlign) {
      base.layoutAlign = node.layoutAlign;
    }
    if (node.layoutPositioning && node.layoutPositioning !== "AUTO") {
      base.layoutPositioning = node.layoutPositioning;
    }
    // ── Parse component hints embedded in child frame names ──
    // This lets nested frames carry @type hints for sub-components
    var parsedName = parseFrameName(node.name);
    if (parsedName.compType) {
      base.comp_type   = parsedName.compType;
      base.parent_ref  = parsedName.parentRef;
      base.default_tab = parsedName.defaultTab;
    }

    base.children = [];
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) {
      var kid = kids[i];
      if (kid.visible === false || kid.opacity === 0) continue;
      var child = extractNode(kid, 0, 0, assetMap);
      if (child) base.children.push(child);
    }
    return base;
  }

  // ── Rectangle ────────────────────────────────────────────────
  if (t === "RECTANGLE") {
    if (node.fills && node.fills.length > 0) {
      var fill = node.fills[0];
      if (fill.type === "SOLID") {
        base.backgroundColor = rgbToHex(fill.color);
      } else if (fill.type === "IMAGE") {
        base.type      = "image";
        base.imageHash = fill.imageHash || "";
        base.src       = fill.imageHash ? "FIGMA_IMAGE:" + fill.imageHash : "PLACEHOLDER";
        base.imageKeyword = node.name;
      }
    }
    if (typeof node.cornerRadius === "number") base.cornerRadius = node.cornerRadius;
    if (node.strokes && node.strokes.length > 0 && node.strokes[0].type === "SOLID") {
      var sc = rgbToHex(node.strokes[0].color);
      var sa = node.strokeAlign || "INSIDE";
      if (typeof node.strokeTopWeight    === "number" ||
          typeof node.strokeBottomWeight === "number" ||
          typeof node.strokeLeftWeight   === "number" ||
          typeof node.strokeRightWeight  === "number") {
        // Individual side strokes
        if (node.strokeTopWeight    > 0) { base.borderTopColor    = sc; base.borderTopWidth    = node.strokeTopWeight; }
        if (node.strokeBottomWeight > 0) { base.borderBottomColor = sc; base.borderBottomWidth = node.strokeBottomWeight; }
        if (node.strokeLeftWeight   > 0) { base.borderLeftColor   = sc; base.borderLeftWidth   = node.strokeLeftWeight; }
        if (node.strokeRightWeight  > 0) { base.borderRightColor  = sc; base.borderRightWidth  = node.strokeRightWeight; }
      } else {
        base.borderColor = sc;
        base.borderWidth = node.strokeWeight || 1;
      }
    }
    return base;
  }

  // ── Text ─────────────────────────────────────────────────────
  if (t === "TEXT") {
    base.type = "text";
    base.text = node.characters || "";

    if (typeof node.fontSize === "number") base.fontSize = node.fontSize;

    if (node.fontName && node.fontName.style) {
      var styleMap = {
        "thin": 100, "extralight": 200, "light": 300,
        "regular": 400, "medium": 500,
        "semi bold": 600, "semibold": 600,
        "bold": 700, "extrabold": 800, "black": 900,
      };
      var styleLower = node.fontName.style.toLowerCase();
      base.fontWeight = styleMap[styleLower] || 400;
      base.fontStyle  = node.fontName.style;
      base.fontFamily = node.fontName.family || "Inter";
    }

    if (node.fills && node.fills.length > 0 && node.fills[0].type === "SOLID") {
      base.color = rgbToHex(node.fills[0].color);
    }

    if (node.lineHeight && typeof node.lineHeight === "object" && node.lineHeight.unit === "PERCENT") {
      base.lineHeight = node.lineHeight.value / 100;
    } else if (typeof node.lineHeight === "number") {
      base.lineHeight = node.lineHeight;
    }

    if (node.letterSpacing && typeof node.letterSpacing === "object") {
      base.letterSpacing = node.letterSpacing.value || 0;
    }

    if (node.textAlignHorizontal) base.textAlign = node.textAlignHorizontal.toLowerCase();

    // ── Nav-link detection: is this TEXT inside a navbar/nav frame? ──
    var parentName = (node.parent && node.parent.name) ? node.parent.name.toLowerCase() : "";
    var ancestorIsNav = false;
    var walker = node.parent;
    while (walker) {
      var wn = (walker.name || "").toLowerCase();
      if (/\b(navbar|nav\s*bar|navigation|header|topbar|top.?bar|menu)\b/.test(wn)) {
        ancestorIsNav = true; break;
      }
      if (walker.type === "FRAME" && walker.parent && walker.parent.type === "PAGE") break;
      walker = walker.parent;
    }
    if (ancestorIsNav) {
      base.isNavLink = true;
    }

    return base;
  }

  // ── Ellipse / Vector / Star / Polygon ────────────────────────
  if (t === "ELLIPSE" || t === "VECTOR" || t === "STAR" || t === "POLYGON" || t === "BOOLEAN_OPERATION") {
    base.type = "rectangle";
    if (node.fills && node.fills.length > 0) {
      var fill = node.fills[0];
      if (fill.type === "SOLID") {
        base.backgroundColor = rgbToHex(fill.color);
      } else if (fill.type === "IMAGE" && fill.imageHash) {
        base.type      = "image";
        base.imageHash = fill.imageHash;
        base.src       = "FIGMA_IMAGE:" + fill.imageHash;
      }
    }
    if (t === "ELLIPSE") base.cornerRadius = 9999;
    return base;
  }

  // ── Line ─────────────────────────────────────────────────────
  if (t === "LINE") {
    base.type = "line";
    if (node.strokes && node.strokes.length > 0 && node.strokes[0].type === "SOLID") {
      base.backgroundColor = rgbToHex(node.strokes[0].color);
    }
    return base;
  }

  return base;
}


function parseFrameName(fullName) {
  if (!fullName) return {
    cleanName:  "Page",
    navHint:    null,
    descHint:   null,
    compType:   null,
    parentRef:  null,
    defaultTab: null,
  };

  var parts     = fullName.split("|");
  var cleanName = parts[0].trim();
  var compType  = null;
  var parentRef = null;
  var defaultTab = null;

  for (var i = 1; i < parts.length; i++) {
    var segment  = parts[i].trim();
    var segLower = segment.toLowerCase();

    if (segLower.indexOf("@type:") === 0) {
      compType = segment.replace(/^@type\s*:\s*/i, "").trim().toLowerCase();

    } else if (segLower.indexOf("@parent:") === 0) {
      parentRef = segment.replace(/^@parent\s*:\s*/i, "").trim();

    } else if (segLower.indexOf("@default:") === 0) {
      defaultTab = segment.replace(/^@default\s*:\s*/i, "").trim();

    } else if (segLower === "default") {
      var slashIdx = cleanName.lastIndexOf("/");
      if (slashIdx !== -1) {
        defaultTab = cleanName.slice(slashIdx + 1).trim();
      } else {
        defaultTab = cleanName.trim();
      }
    }
    // @nav: and @desc: are intentionally ignored here.
    // Button linking is now handled exclusively by the Export Panel UI.
  }

  if (!compType) {
    compType = inferComponentType(cleanName);
  }

  return {
    cleanName:  cleanName,
    navHint:    null,   // always null — Export Panel sets links directly
    descHint:   null,   // always null — Export Panel sets prompts directly
    compType:   compType,
    parentRef:  parentRef,
    defaultTab: defaultTab,
  };
}

// ─────────────────────────────────────────────────────────────────
// COMPONENT TYPE INFERENCE
// Auto-detects component type from the frame name when @type is absent.
// This is a safety net — explicit @type always takes priority.
// ─────────────────────────────────────────────────────────────────

function inferComponentType(name) {
  if (!name) return null;
  var n = name.toLowerCase();

  // Overlay types
  if (/\bmodal\b/.test(n))       return "modal";
  if (/\bdrawer\b/.test(n))      return "drawer";
  if (/\bpopover\b/.test(n))     return "popover";
  if (/\btooltip\b/.test(n))     return "tooltip";
  if (/\btoast\b/.test(n))       return "toast";
  if (/\bdialog\b/.test(n))      return "dialog";
  if (/\bbottomsheet\b/.test(n)) return "bottomsheet";
  if (/\bbottom.?sheet\b/.test(n)) return "bottomsheet";

  // Tab children (must come before "tabs" to catch "tab" first)
  // A lone "tab" in name with no "tabs" (plural) → it's a tab child
  if (/\btab\b/.test(n) && !/\btabs\b/.test(n)) return "tab";

  // Inline component types
  if (/\btabs\b/.test(n))        return "tabs";
  if (/\bscroller\b|\bslider\b|\bscrollbar\b/.test(n)) return "scroller";
  if (/\bsidebar\b/.test(n))     return "sidebar";
  if (/\bnavbar\b|nav\s*bar/.test(n)) return "navbar";
  if (/\bfooter\b/.test(n))      return "footer";
  if (/\bheader\b/.test(n))      return "navbar";
  if (/\baccordion\b/.test(n))   return "accordion";
  if (/\btable\b/.test(n))       return "table";
  if (/\bpagination\b/.test(n))  return "pagination";
  if (/\bform\b/.test(n))        return "form";
  if (/\bcard\b/.test(n))        return "card";
  if (/\bbadge\b/.test(n))       return "badge";
  if (/\bcheckbox\b/.test(n))    return "checkbox";
  if (/\bradio\b/.test(n))       return "radio";
  if (/\btoggle\b/.test(n))      return "toggle";
  if (/\bchip\b/.test(n))        return "chip";
  if (/\btag\b/.test(n))         return "tag";
  if (/\bavatar\b/.test(n))      return "avatar";
  if (/\bbanner\b/.test(n))      return "banner";
  if (/\bhero\b/.test(n))        return "hero";
  if (/\blist\b/.test(n))        return "list";

  // Action types — detected from name patterns
  if (/\bclose\s*(btn|button)?\b/.test(n))  return "action:close";
  if (/\bcancel\s*(btn|button)?\b/.test(n)) return "action:cancel";
  if (/\bback\s*(btn|button)?\b/.test(n))   return "action:back";
  if (/\bsubmit\s*(btn|button)?\b/.test(n)) return "action:submit";

  // Default → page (full route)
  return null;
}

// ── Utilities ─────────────────────────────────────────────────────

function rgbToHex(color) {
  if (!color) return "#888888";
  var r = Math.round((color.r || 0) * 255).toString(16).padStart(2, "0");
  var g = Math.round((color.g || 0) * 255).toString(16).padStart(2, "0");
  var b = Math.round((color.b || 0) * 255).toString(16).padStart(2, "0");
  return "#" + r + g + b;
}

function hexToRgb(hex) {
  if (!hex || typeof hex !== "string") return { r: 0.8, g: 0.8, b: 0.8 };
  hex = hex.replace("#", "").trim();
  if (hex.length === 3) {
    hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
  }
  if (hex.length !== 6) return { r: 0.8, g: 0.8, b: 0.8 };
  return {
    r: parseInt(hex.substring(0, 2), 16) / 255,
    g: parseInt(hex.substring(2, 4), 16) / 255,
    b: parseInt(hex.substring(4, 6), 16) / 255,
  };
}

// ─────────────────────────────────────────────────────────────────
// EXPORT PANEL METADATA EXTRACTOR
// Lightweight scan — returns frame names + detected buttons only.
// Used to populate the Export Panel UI (no asset byte export here).
// Full tree + assets are extracted later when user clicks "Generate".
// ─────────────────────────────────────────────────────────────────

function handleExtractPanelMetadata() {
  var currentPage = figma.currentPage;

  currentPage.loadAsync().then(function() {

    // Use selected frames, or all frames if nothing selected
    var selectedFrames = figma.currentPage.selection.filter(function(n) {
      return n.type === "FRAME";
    });

    var frames;
    if (selectedFrames.length > 0) {
      frames = selectedFrames.slice().sort(function(a, b) { return a.x - b.x; });
    } else {
      frames = currentPage.children
        .filter(function(n) { return n.type === "FRAME"; })
        .sort(function(a, b) { return a.x - b.x; });
    }

    if (frames.length === 0) {
      figma.ui.postMessage({
        type:  "panel_metadata_error",
        error: "No frames found on this page.",
      });
      return;
    }

    // Derive a project title from the Figma file root name
    var rootName = figma.root.name || "";
    var isGeneric = rootName === "" ||
      rootName.toLowerCase() === "untitled" ||
      rootName.toLowerCase() === "draft";
    var projectTitle = isGeneric
      ? (frames.length > 0 ? parseFrameName(frames[0].name).cleanName + " App" : "My App")
      : rootName;

    // Build lightweight frame descriptors
    var frameList = [];
    frames.forEach(function(frame) {
      var parsed = parseFrameName(frame.name);

      // Collect all button-like nodes inside this frame
      var buttons = [];
      collectButtonNodes(frame, buttons, frame.x, frame.y);

      frameList.push({
        id:        frame.id,
        name:      parsed.cleanName,
        raw_name:  frame.name,
        width:     Math.round(frame.width),
        height:    Math.round(frame.height),
        comp_type: parsed.compType || null,
        buttons:   buttons,
      });
    });

    figma.ui.postMessage({
      type:          "panel_metadata_ready",
      frames:        frameList,
      project_title: projectTitle,
      total:         frameList.length,
    });

  }).catch(function(err) {
    figma.ui.postMessage({
      type:  "panel_metadata_error",
      error: String(err),
    });
  });
}


// ─────────────────────────────────────────────────────────────────
// BUTTON NODE COLLECTOR  (for Export Panel)
// Walks a node tree and collects every button / clickable-looking node.
// ─────────────────────────────────────────────────────────────────

function collectButtonNodes(node, buttons, frameX, frameY) {
  if (!node) return;
  if (buttons.length >= 60) return; // cap per frame

  var name = node.name || "";
  var t    = node.type;

  // Direct button type
  var isButton = (t === "FRAME" || t === "COMPONENT" || t === "INSTANCE") &&
    isButtonLike(name, node);

  if (isButton) {
    // Try to find visible text inside this button
    var labelText = extractFirstText(node) || name;
    buttons.push({
      id:         node.id,
      name:       name,
      label:      labelText.slice(0, 80),
      x:          Math.round((node.absoluteBoundingBox ? node.absoluteBoundingBox.x : node.x) - frameX),
      y:          Math.round((node.absoluteBoundingBox ? node.absoluteBoundingBox.y : node.y) - frameY),
      width:      Math.round(node.width || 0),
      height:     Math.round(node.height || 0),
    });
    // Don't recurse into button children — treat button as a leaf
    return;
  }

  var children = node.children || [];
  for (var i = 0; i < children.length; i++) {
    collectButtonNodes(children[i], buttons, frameX, frameY);
  }
}


// Returns true if a frame/component looks like a button
function isButtonLike(name, node) {
  var nameLower = name.toLowerCase();

  // Explicit button names
  if (/\b(btn|button|cta|link|action)\b/.test(nameLower)) return true;

  // Small rectangular frames (typical button size)
  var w = node.width  || 0;
  var h = node.height || 0;
  if (w > 30 && w < 400 && h > 20 && h < 80) {
    // Only include if it has fills (not a layout container)
    if (node.fills && node.fills.length > 0 &&
        node.fills[0].type !== "NONE") {
      return true;
    }
  }

  return false;
}


// Extracts the first meaningful text from a node tree
function extractFirstText(node) {
  if (node.type === "TEXT" && node.characters) {
    return node.characters.trim();
  }
  var children = node.children || [];
  for (var i = 0; i < children.length; i++) {
    var found = extractFirstText(children[i]);
    if (found) return found;
  }
  return "";
}


// ─────────────────────────────────────────────────────────────────
// EXPORT FRAME EXTRACTOR  (new — for Export Panel)
//
// Scans ALL top-level frames on the current page.
// For each frame returns:
//   id, name, comp_type (page / modal / sidebar / etc.)
//   width, height
//   clickableNodes: every named child node that looks interactive
//     (buttons, components, instances, named frames with fills)
//     Each node: { nodeId, nodeName, label, nodeType, x, y, w, h }
//
// This is the data the Export Panel uses for:
//   • Frame cards (one per frame)
//   • Element inspector (shown when user selects something in Figma)
//   • Button linker (link source element → target frame)
// ─────────────────────────────────────────────────────────────────

function handleExtractExportFrames() {
  figma.currentPage.loadAsync().then(function() {

    // Get ALL top-level frames on the page
    var allFrames = figma.currentPage.children
      .filter(function(n) { return n.type === "FRAME"; })
      .sort(function(a, b) { return a.x - b.x; });

    if (allFrames.length === 0) {
      figma.ui.postMessage({
        type:  "export_frames_error",
        error: "No frames found on this page. Create some frames first.",
      });
      return;
    }

    // ── Detect which top-level frames are currently selected ────
    // Only top-level FRAME nodes count — child elements are walked
    // up to their root frame so selecting any child pre-checks its frame.
    var selectionSet = {};
    var sel = figma.currentPage.selection;
    for (var s = 0; s < sel.length; s++) {
      var node = sel[s];
      // Walk up to find the top-level frame
      var cur = node;
      while (cur && !(cur.type === "FRAME" && cur.parent && cur.parent.type === "PAGE")) {
        cur = cur.parent;
      }
      if (cur && cur.type === "FRAME") {
        selectionSet[cur.id] = true;
      }
    }

    var selectedIds = Object.keys(selectionSet);
    var hasSelection = selectedIds.length > 0;

    console.log(
      "[EXPORT_PANEL] Total frames: " + allFrames.length +
      " | Selected frames: " + selectedIds.length +
      (hasSelection ? " → pre-checking selection only" : " → pre-checking all (no selection)")
    );

    var rootName    = figma.root.name || "";
    var isGeneric   = !rootName || rootName.toLowerCase() === "untitled" || rootName.toLowerCase() === "draft";
    var projectTitle = isGeneric
      ? (allFrames.length > 0 ? parseFrameName(allFrames[0].name).cleanName + " App" : "My App")
      : rootName;

    var frameList = [];
    allFrames.forEach(function(frame) {
      var parsed   = parseFrameName(frame.name);
      var compType = parsed.compType;

      // Collect interactive/clickable child nodes
      var clickable = [];
      collectClickableNodes(frame, clickable, frame);

      frameList.push({
        id:             frame.id,
        name:           parsed.cleanName,
        rawName:        frame.name,
        compType:       compType || null,
        width:          Math.round(frame.width),
        height:         Math.round(frame.height),
        clickableNodes: clickable,
      });

      console.log(
        "[EXPORT_PANEL] Frame: '" + parsed.cleanName + "'" +
        " | type=" + (compType || "page") +
        " | " + Math.round(frame.width) + "×" + Math.round(frame.height) +
        " | clickable=" + clickable.length +
        (selectionSet[frame.id] ? " | ✓ SELECTED" : "")
      );
    });

    figma.ui.postMessage({
      type:             "export_frames_ready",
      frames:           frameList,
      projectTitle:     projectTitle,
      // IDs that should be pre-checked. If user had no selection, send all IDs
      // so behaviour is unchanged (all checked by default).
      selectedFrameIds: hasSelection ? selectedIds : allFrames.map(function(f){ return f.id; }),
      hadSelection:     hasSelection,
    });

  }).catch(function(err) {
    figma.ui.postMessage({
      type:  "export_frames_error",
      error: String(err),
    });
  });
}


// ─────────────────────────────────────────────────────────────────
// CLICKABLE NODE COLLECTOR
// Walks a frame tree and returns every node that is likely interactive:
// COMPONENT instances, FRAME children named like buttons/interactions,
// or any named child that has rounded corners + fill (looks like a button).
// Depth limited to 5 levels to stay fast.
// ─────────────────────────────────────────────────────────────────

function collectClickableNodes(node, result, rootFrame) {
  if (!node || !node.visible) return;
  if (result.length >= 300) return; // raised cap

  var name  = node.name  || "";
  var t     = node.type;
  var depth = 0;
  var cur   = node;
  while (cur && cur !== rootFrame) { depth++; cur = cur.parent; }
  if (depth > 15) return;

  var nameLower = name.toLowerCase();
  var isClickable = false;

  // INSTANCE = component placed on canvas → always clickable
  if (t === "INSTANCE" || t === "COMPONENT") {
    isClickable = true;
  }
  // Explicit action / navigation keywords in name
  else if (t === "FRAME" && /\b(btn|button|cta|link|action|click|tap|press|icon|chip|tag|badge|toggle|switch|close|back|nav|menu|dropdown|select|trigger|open|launch|go|next|prev|submit|cancel|confirm|delete|add|edit|create|save|login|logout|signup|register)\b/.test(nameLower)) {
    isClickable = true;
  }
  // Small frame with corner radius + fill (typical button / menu item shape)
  else if (t === "FRAME") {
    var w = node.width || 0, h = node.height || 0;
    var cr = node.cornerRadius || 0;
    // Wider range: also catch small dropdown menus (can be taller)
    if (w > 16 && w < 600 && h > 12 && h < 200) {
      if (node.fills && node.fills.length > 0 && node.fills[0].type !== "NONE") {
        isClickable = true;
      }
    }
  }

  if (isClickable) {
    var label = extractFirstText(node) || name;
    var absX = 0, absY = 0;
    if (node.absoluteBoundingBox && rootFrame.absoluteBoundingBox) {
      absX = Math.round(node.absoluteBoundingBox.x - rootFrame.absoluteBoundingBox.x);
      absY = Math.round(node.absoluteBoundingBox.y - rootFrame.absoluteBoundingBox.y);
    }
    result.push({
      nodeId:   node.id,
      nodeName: name,
      label:    label.slice(0, 80),
      nodeType: t,
      x:        absX,
      y:        absY,
      w:        Math.round(node.width  || 0),
      h:        Math.round(node.height || 0),
    });
  }

  // Recurse into children
  // We DO recurse into INSTANCE internals so buttons nested inside
  // component instances (e.g. a sidebar component) are not missed.
  // We only skip recursion if the node was already added as clickable
  // AND it is an INSTANCE — to avoid double-counting its children
  // as separate buttons when the instance itself is the button.
  var shouldRecurse = true;
  if (isClickable && (t === "INSTANCE" || t === "COMPONENT")) {
    // Don't recurse into a button-like instance — it's one unit
    shouldRecurse = false;
  }
  if (shouldRecurse) {
    var children = node.children || [];
    for (var i = 0; i < children.length; i++) {
      collectClickableNodes(children[i], result, rootFrame);
    }
  }
}

// ─────────────────────────────────────────────────────────────────
// BUTTON NAME MAP
// Scans all top-level frames on current page, builds a map of
// normalised button name → [ { nodeId, nodeName, frameName, frameId } ]
// Near-match: lowercased + whitespace collapsed
// ─────────────────────────────────────────────────────────────────

function handleBuildButtonNameMap(frameIds) {
  // Scan ALL Figma pages to find the selected frames — frame IDs from
  // the Export Panel may belong to any page, not just currentPage.
  // Deduplicates frames (same ID selected multiple times) and buttons
  // (same nodeId found in multiple frames).

  var idSet    = {};
  var hasFilter = false;
  (frameIds || []).forEach(function(id) {
    idSet[id]  = true;
    hasFilter  = true;
  });

  // Load every page so we can search across them
  var allPages = figma.root.children;
  var loadPromises = allPages.map(function(p) { return p.loadAsync(); });

  Promise.all(loadPromises).then(function() {
    var map          = {};  // normalisedName → [ entries ]
    var seenNodeIds  = {};  // button nodeId → true  (dedup buttons)
    var seenFrameIds = {};  // frame id → true        (dedup same frame added N times)

    allPages.forEach(function(page) {
      page.children.forEach(function(node) {
        if (node.type !== "FRAME") return;

        // If a filter list was given, only include frames in that list
        if (hasFilter && !idSet[node.id]) return;

        // Deduplicate — same frame ID on multiple passes
        if (seenFrameIds[node.id]) return;
        seenFrameIds[node.id] = true;

        var parsed    = parseFrameName(node.name);
        var frameName = parsed.cleanName;
        var clickable = [];
        collectClickableNodes(node, clickable, node);

        clickable.forEach(function(n) {
          // Deduplicate — same button nodeId in different frames
          if (seenNodeIds[n.nodeId]) return;
          seenNodeIds[n.nodeId] = true;

          var norm = n.nodeName.toLowerCase().replace(/\s+/g, " ").trim();
          if (!map[norm]) { map[norm] = []; }
          map[norm].push({
            nodeId:    n.nodeId,
            nodeName:  n.nodeName,
            frameName: frameName,
            frameId:   node.id,
            pageName:  page.name,
            x: n.x, y: n.y, w: n.w, h: n.h,
          });
        });
      });
    });

    var frameCount = Object.keys(seenFrameIds).length;
    figma.ui.postMessage({ type: "button_name_map_ready", map: map });
    console.log(
      "[BUTTON_MAP] Built map — " + Object.keys(map).length +
      " unique names from " + frameCount + " frame(s) across " +
      allPages.length + " page(s)."
    );

  }).catch(function(err) {
    console.error("[BUTTON_MAP] Failed:", err);
    figma.ui.postMessage({ type: "button_name_map_ready", map: {} });
  });
}

// ─────────────────────────────────────────────────────────────────
// NODE HIGHLIGHT / UNHIGHLIGHT
// Temporarily applies a coloured border + drop shadow to a node
// so the user can see which button they are hovering in the list.
// ─────────────────────────────────────────────────────────────────

var _highlightOriginalStrokes  = {};  // nodeId → original strokes snapshot
var _highlightOriginalEffects  = {};  // nodeId → original effects snapshot
var _highlightOriginalWeight   = {};  // nodeId → original strokeWeight

function handleHighlightNode(nodeId) {
  if (!nodeId) return;
  figma.getNodeByIdAsync(nodeId).then(function(node) {
    if (!node) return;

    // Scroll into view
    figma.viewport.scrollAndZoomIntoView([node]);

    // Save originals
    try {
      _highlightOriginalStrokes[nodeId] = JSON.parse(JSON.stringify(node.strokes  || []));
      _highlightOriginalEffects[nodeId] = JSON.parse(JSON.stringify(node.effects  || []));
      _highlightOriginalWeight[nodeId]  = node.strokeWeight || 0;

      // Apply highlight: indigo border + glow shadow
      node.strokes      = [{ type: "SOLID", color: { r: 0.388, g: 0.400, b: 0.965 }, opacity: 1 }];
      node.strokeWeight = 2;
      node.strokeAlign  = "OUTSIDE";
      node.effects      = (JSON.parse(JSON.stringify(node.effects || []))).concat([{
        type:      "DROP_SHADOW",
        color:     { r: 0.388, g: 0.400, b: 0.965, a: 0.55 },
        offset:    { x: 0, y: 0 },
        radius:    10,
        visible:   true,
        blendMode: "NORMAL",
      }]);
      console.log("[HIGHLIGHT] Applied to node:", nodeId);
    } catch(e) {
      console.warn("[HIGHLIGHT] Could not apply highlight:", e);
    }
  }).catch(function(e) {
    console.warn("[HIGHLIGHT] getNodeByIdAsync failed:", e);
  });
}

function handleUnhighlightNode(nodeId) {
  if (!nodeId) return;
  figma.getNodeByIdAsync(nodeId).then(function(node) {
    if (!node) return;

    try {
      if (_highlightOriginalStrokes[nodeId] !== undefined) {
        node.strokes      = _highlightOriginalStrokes[nodeId];
        node.strokeWeight = _highlightOriginalWeight[nodeId] || 0;
        delete _highlightOriginalStrokes[nodeId];
        delete _highlightOriginalWeight[nodeId];
      }
      if (_highlightOriginalEffects[nodeId] !== undefined) {
        node.effects = _highlightOriginalEffects[nodeId];
        delete _highlightOriginalEffects[nodeId];
      }
      console.log("[UNHIGHLIGHT] Restored node:", nodeId);
    } catch(e) {
      console.warn("[UNHIGHLIGHT] Could not restore node:", e);
    }
  }).catch(function(e) {
    console.warn("[UNHIGHLIGHT] getNodeByIdAsync failed:", e);
  });
}
