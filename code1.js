// code.js — Figma Plugin Sandbox
// IMPORTANT: This sandbox cannot make network requests directly.
// All fetch() calls happen in ui.html, which sends results back via postMessage.
//
// Message flow:
//   code.js  →  ui.html : { type: 'generate', prompt }
//   ui.html  →  code.js : { type: 'page_start', ... }
//   ui.html  →  code.js : { type: 'page_chunk', page_id, children: [...] }
//   ui.html  →  code.js : { type: 'page_end', page_id, page_name, ... }
//   ui.html  →  code.js : { type: 'complete', ... }
//   ui.html  →  code.js : { type: 'status', message }
//   ui.html  →  code.js : { type: 'error', message }

figma.showUI(__html__, { width: 600, height: 800 });

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
    var picked = sel[0];
    // Walk up to the nearest top-level FRAME
    var topFrame = null;
    var cur = picked;
    while (cur) {
      if (cur.type === "FRAME" && cur.parent && cur.parent.type === "PAGE") {
        topFrame = cur;
        break;
      }
      cur = cur.parent;
    }
    var target = topFrame || picked;
    var parsed = parseFrameName(target.name);
    var compType = parsed.compType;

    // Collect clickable nodes for this frame
    var clickable = [];
    if (target.type === "FRAME") {
      collectClickableNodes(target, clickable, target);
    }

    console.log("[ADD_FRAME_PICK] Picked frame: '" + parsed.cleanName + "' id=" + target.id);

    figma.ui.postMessage({
      type:      "add_frame_picked",
      frame: {
        id:             target.id,
        name:           parsed.cleanName,
        rawName:        target.name,
        compType:       compType || null,
        width:          Math.round(target.width  || 0),
        height:         Math.round(target.height || 0),
        clickableNodes: clickable,
      },
    });

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
  backendUrl: "http://localhost:9000",
  selectionCount: figma.currentPage.selection.length,
});

// ── Page buffer: reassembles chunked pages ────────────────────────
var pageBuffers = {};

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
      figma.viewport.scrollAndZoomIntoView(figma.currentPage.children);
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
      isPickingAddFrame = true;
      console.log("[ADD_FRAME_PICK] Entering add-frame-picking mode");
      break;

    case "cancel_add_frame_picking":
      isPickingAddFrame = false;
      console.log("[ADD_FRAME_PICK] Cancelled");
      break;

    // ── Build button name map across all frames ───────────────
    case "build_button_name_map":
      handleBuildButtonNameMap();
      break;

    // ── Highlight a node in Figma canvas ─────────────────────
    case "highlight_node":
      handleHighlightNode(msg.nodeId);
      break;

    // ── Unhighlight a node in Figma canvas ───────────────────
    case "unhighlight_node":
      handleUnhighlightNode(msg.nodeId);
      break;
  }
};
  

// ─────────────────────────────────────────────────────────────────
// PAGE BUFFER HANDLERS
// ─────────────────────────────────────────────────────────────────
function handlePageStart(msg) {
  var page_id = msg.page_id;
  console.log("[PAGE_START]", msg.page_number + "/" + msg.total_pages + ":", msg.page_name,
    "(" + msg.total_children + " elements, " + msg.total_chunks + " chunks)");

  pageBuffers[page_id] = {
    page_name:       msg.page_name,
    page_number:     msg.page_number,
    total_pages:     msg.total_pages,
    frame_meta:      msg.frame_meta,
    total_chunks:    msg.total_chunks,
    received_chunks: 0,
    children:        [],
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

  console.log("[PAGE_END] Rendering '" + buf.page_name + "': " + buf.children.length + " elements");

  var fullFrame = {
    type:            buf.frame_meta.type,
    name:            buf.frame_meta.name,
    width:           buf.frame_meta.width,
    height:          buf.frame_meta.height,
    backgroundColor: buf.frame_meta.backgroundColor,
    children:        buf.children,
  };

  renderFrame(fullFrame).then(function() {
    console.log("[RENDER] Done:", buf.page_name);
    figma.ui.postMessage({ type: "render_done", page_name: buf.page_name, page_number: buf.page_number });
  }).catch(function(err) {
    console.error("[RENDER ERROR]", buf.page_name, ":", err.message || err);
    figma.ui.postMessage({ type: "render_error", page_name: buf.page_name, error: String(err) });
  });

  delete pageBuffers[page_id];
}

// ─────────────────────────────────────────────────────────────────
// RENDERER
// ─────────────────────────────────────────────────────────────────
function renderFrame(frameData) {
  return Promise.all([
    figma.loadFontAsync({ family: "Inter", style: "Regular" }),
    figma.loadFontAsync({ family: "Inter", style: "Medium" }),
    figma.loadFontAsync({ family: "Inter", style: "Semi Bold" }),
    figma.loadFontAsync({ family: "Inter", style: "Bold" }),
  ]).then(function() {
    var frame = figma.createFrame();
    frame.name = frameData.name || "Page";
    frame.resize(frameData.width || 1440, frameData.height || 1080);
    frame.x = getNextFrameX();
    frame.y = 0;
    frame.fills = [{ type: "SOLID", color: hexToRgb(frameData.backgroundColor || "#FFFFFF") }];
    frame.clipsContent = true;

    var children = frameData.children || [];
    return renderChildren(children, frame).then(function() {
      figma.currentPage.appendChild(frame);
      return frame;
    });
  });
}

function renderChildren(children, parent) {
  var promise = Promise.resolve();
  for (var i = 0; i < children.length; i++) {
    (function(child) {
      promise = promise.then(function() {
        return createNode(child, parent).then(function(node) {
          if (node) parent.appendChild(node);
        }).catch(function(e) {
          console.error("[RENDER] Error on '" + (child && child.name) + "':", e.message || e);
        });
      });
    })(children[i]);
  }
  return promise;
}

function getNextFrameX() {
  var frames = figma.currentPage.children.filter(function(n) { return n.type === "FRAME"; });
  if (frames.length === 0) return 0;
  var rightmost = 0;
  for (var i = 0; i < frames.length; i++) {
    var edge = frames[i].x + frames[i].width;
    if (edge > rightmost) rightmost = edge;
  }
  return rightmost + 200;
}

function createNode(data, parent) {
  if (!data || !data.type) return Promise.resolve(null);
  switch (data.type) {
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
  rect.name = data.name || "Rectangle";
  rect.x = data.x || 0;
  rect.y = data.y || 0;
  rect.resize(Math.max(1, data.width || 100), Math.max(1, data.height || 100));
  rect.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || "#CCCCCC") }];
  if (data.cornerRadius) rect.cornerRadius = data.cornerRadius;
  if (data.opacity !== undefined && data.opacity !== null) rect.opacity = data.opacity;
  return rect;
}

// ── Text ──────────────────────────────────────────────────────────
function createText(data) {
  var text = figma.createText();
  text.name = data.name || "Text";
  text.x = data.x || 0;
  text.y = data.y || 0;

  var styleMap = { "bold": "Bold", "semibold": "Semi Bold", "medium": "Medium", "regular": "Regular" };
  var fw = (data.fontWeight || "regular").toLowerCase();
  var fontStyle = styleMap[fw] || "Regular";
  text.fontName = { family: "Inter", style: fontStyle };
  text.characters = String(data.text || "");
  text.fontSize = data.fontSize || 16;
  text.fills = [{ type: "SOLID", color: hexToRgb(data.color || "#000000") }];

  if (data.width) {
    text.textAutoResize = "HEIGHT";
    text.resize(data.width, Math.max(1, text.height || 20));
  } else {
    text.textAutoResize = "WIDTH_AND_HEIGHT";
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

  return text;
}

// ── Image ─────────────────────────────────────────────────────────
function createImage(data) {
  var rect = figma.createRectangle();
  rect.name = data.name || "Image";
  rect.x = data.x || 0;
  rect.y = data.y || 0;
  rect.resize(Math.max(1, data.width || 400), Math.max(1, data.height || 300));
  if (data.borderRadius || data.cornerRadius) {
    rect.cornerRadius = data.borderRadius || data.cornerRadius || 0;
  }

  rect.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || "#CCCCCC") }];

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
  frame.name = data.name || "Button";
  frame.x = data.x || 0;
  frame.y = data.y || 0;
  frame.resize(Math.max(1, data.width || 160), Math.max(1, data.height || 48));
  frame.cornerRadius = data.cornerRadius || 8;
  frame.clipsContent = true;

  if (data.backgroundColor === "transparent") {
    frame.fills = [];
    if (data.borderColor) {
      frame.strokes = [{ type: "SOLID", color: hexToRgb(data.borderColor) }];
      frame.strokeWeight = data.borderWidth || 1;
    }
  } else {
    frame.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || "#6366F1") }];
  }

  var label = figma.createText();
  var styleMap = { "bold": "Bold", "semibold": "Semi Bold", "medium": "Medium", "regular": "Regular" };
  label.fontName = { family: "Inter", style: styleMap[(data.fontWeight || "medium").toLowerCase()] || "Medium" };
  label.characters = String(data.text || "Button");
  label.fontSize = data.fontSize || 16;
  label.fills = [{ type: "SOLID", color: hexToRgb(data.textColor || "#FFFFFF") }];
  label.textAutoResize = "WIDTH_AND_HEIGHT";
  label.x = Math.max(0, Math.floor((frame.width - label.width) / 2));
  label.y = Math.max(0, Math.floor((frame.height - label.height) / 2));
  frame.appendChild(label);
  return frame;
}

// ── Ellipse ───────────────────────────────────────────────────────
function createEllipse(data) {
  var el = figma.createEllipse();
  el.name = data.name || "Ellipse";
  el.x = data.x || 0;
  el.y = data.y || 0;
  el.resize(Math.max(1, data.width || 100), Math.max(1, data.height || 100));
  el.fills = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || data.color || "#CCCCCC") }];
  return el;
}

// ── Line ──────────────────────────────────────────────────────────
function createLine(data) {
  var line = figma.createLine();
  line.name = data.name || "Line";
  line.x = data.x || 0;
  line.y = data.y || 0;
  line.resize(Math.max(1, data.width || 100), 0);
  line.strokes = [{ type: "SOLID", color: hexToRgb(data.backgroundColor || data.color || "#333333") }];
  line.strokeWeight = data.strokeWeight || 1;
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
    group.name = data.name || "Group";
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
    var promise = Promise.resolve();

    var _assetMap = buildAssetMap();

    frames.forEach(function(frame) {
      promise = promise.then(function() {

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
    var promise         = Promise.resolve();

    var _assetMap = buildAssetMap();

    frames.forEach(function(frame) {
      promise = promise.then(function() {
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

  figma.root.children.forEach(function(page) {
    page.children && page.children.forEach(function(node) {
      scanNode(node);
    });
  });

  console.log("[ASSET_MAP] Built map with " + Object.keys(map).length + " asset(s).");
  return map;
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
      var activeFill = null;
      for (var fi = 0; fi < node.fills.length; fi++) {
        var f = node.fills[fi];
        if (f.visible !== false && (f.opacity === undefined || f.opacity > 0)) {
          activeFill = f; break;
        }
      }
      if (activeFill) {
        if (activeFill.type === "SOLID") {
          base.backgroundColor = rgbToHex(activeFill.color);
        } else if (activeFill.type === "IMAGE" && activeFill.imageHash) {
          base.imageFill       = true;
          base.imageHash       = activeFill.imageHash;
          base.backgroundColor = "#CCCCCC";
        }
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
    if (node.strokes && node.strokes.length > 0) {
      var s0 = node.strokes[0];
      if (s0.type === "SOLID"
          && s0.visible !== false
          && (s0.opacity === undefined || s0.opacity > 0)) {
        var sc = rgbToHex(s0.color);
        if (typeof node.strokeTopWeight    === "number" ||
            typeof node.strokeBottomWeight === "number" ||
            typeof node.strokeLeftWeight   === "number" ||
            typeof node.strokeRightWeight  === "number") {
          if ((node.strokeTopWeight    || 0) > 0) { base.borderTopColor    = sc; base.borderTopWidth    = node.strokeTopWeight; }
          if ((node.strokeBottomWeight || 0) > 0) { base.borderBottomColor = sc; base.borderBottomWidth = node.strokeBottomWeight; }
          if ((node.strokeLeftWeight   || 0) > 0) { base.borderLeftColor   = sc; base.borderLeftWidth   = node.strokeLeftWeight; }
          if ((node.strokeRightWeight  || 0) > 0) { base.borderRightColor  = sc; base.borderRightWidth  = node.strokeRightWeight; }
        } else if ((node.strokeWeight || 0) > 0) {
          base.borderColor = sc;
          base.borderWidth = node.strokeWeight;
        }
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
    if (node.strokes && node.strokes.length > 0) {
      var ls = node.strokes[0];
      if (ls.type === "SOLID" && ls.visible !== false && (ls.opacity === undefined || ls.opacity > 0)) {
        base.backgroundColor = rgbToHex(ls.color);
      }
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
  if (result.length >= 120) return; // raised cap

  var name  = node.name  || "";
  var t     = node.type;
  var depth = 0;
  var cur   = node;
  while (cur && cur !== rootFrame) { depth++; cur = cur.parent; }
  if (depth > 7) return;

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

  // Recurse into children (not into INSTANCE internals to avoid noise)
  if (t !== "INSTANCE") {
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

function handleBuildButtonNameMap() {
  figma.currentPage.loadAsync().then(function() {
    var allFrames = figma.currentPage.children.filter(function(n) { return n.type === "FRAME"; });
    var map = {};   // normalisedName → [ entries ]
    var raw = {};   // normalisedName → original display name (first seen)

    allFrames.forEach(function(frame) {
      var parsed    = parseFrameName(frame.name);
      var frameName = parsed.cleanName;
      var clickable = [];
      collectClickableNodes(frame, clickable, frame);

      clickable.forEach(function(n) {
        var norm = n.nodeName.toLowerCase().replace(/\s+/g, " ").trim();
        if (!map[norm]) { map[norm] = []; raw[norm] = n.nodeName; }
        map[norm].push({
          nodeId:    n.nodeId,
          nodeName:  n.nodeName,
          frameName: frameName,
          frameId:   frame.id,
          x: n.x, y: n.y, w: n.w, h: n.h,
        });
      });
    });

    figma.ui.postMessage({ type: "button_name_map_ready", map: map });
  }).catch(function(err) {
    figma.ui.postMessage({ type: "button_name_map_ready", map: {} });
  });
}

// ─────────────────────────────────────────────────────────────────
// NODE HIGHLIGHT / UNHIGHLIGHT
// ─────────────────────────────────────────────────────────────────

var _highlightOriginalStrokes = {};  // nodeId → original strokes snapshot

function handleHighlightNode(nodeId) {
  if (!nodeId) return;
  var node = figma.getNodeById(nodeId);
  if (!node) return;

  // Scroll + zoom into view
  figma.viewport.scrollAndZoomIntoView([node]);

  // Save original strokes
  _highlightOriginalStrokes[nodeId] = JSON.parse(JSON.stringify(node.strokes || []));

  // Apply highlight: bright indigo border + drop shadow effect
  try {
    node.strokes = [{ type: "SOLID", color: { r: 0.388, g: 0.4, b: 0.965 }, opacity: 1 }];
    node.strokeWeight = 2;
    node.strokeAlign  = "OUTSIDE";
    node.effects = (node.effects || []).concat([{
      type:    "DROP_SHADOW",
      color:   { r: 0.388, g: 0.4, b: 0.965, a: 0.6 },
      offset:  { x: 0, y: 0 },
      radius:  8,
      spread:  2,
      visible: true,
      blendMode: "NORMAL",
    }]);
  } catch(e) {
    console.warn("[HIGHLIGHT] Could not apply highlight to node:", nodeId, e);
  }
}

function handleUnhighlightNode(nodeId) {
  if (!nodeId) return;
  var node = figma.getNodeById(nodeId);
  if (!node) return;

  try {
    // Restore original strokes
    if (_highlightOriginalStrokes[nodeId] !== undefined) {
      node.strokes = _highlightOriginalStrokes[nodeId];
      delete _highlightOriginalStrokes[nodeId];
    }
    // Remove any DROP_SHADOW effects we added
    node.effects = (node.effects || []).filter(function(e) {
      return !(e.type === "DROP_SHADOW"
        && e.color && Math.round(e.color.r * 100) === 39
        && e.offset && e.offset.x === 0 && e.offset.y === 0
        && e.radius === 8);
    });
  } catch(e) {
    console.warn("[UNHIGHLIGHT] Could not restore node:", nodeId, e);
  }
}