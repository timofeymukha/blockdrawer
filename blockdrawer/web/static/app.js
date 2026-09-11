/* BlockDrawer browser client.
 *
 * The server owns the model, history, and preferences. This script owns the
 * viewport, selection, and editing modes, and mirrors the Tk application's
 * behaviour: same modes, same panels, same shortcuts, same status messages.
 */
"use strict";

(function () {
  const TOKEN = window.BLOCKDRAWER_TOKEN;
  const APP_NAME = "BlockDrawer";
  const EDGE_TYPES = ["line", "arc", "polyLine", "spline"];
  const BOUNDARY_TYPES = ["patch", "symmetry", "wall", "cyclic", "empty"];
  const PROJECTION_DIRECTIONS = [
    ["Orthogonal (shortest path)", "orthogonal"],
    ["Along x", "x"],
    ["Along y", "y"],
  ];
  const MAX_VISIBLE_CONTROL_POINTS = 250;
  const MIN_SPLIT_FRACTION = 1.0e-4;
  const FIT_RELATIVE_TOLERANCE = "1e-08";
  const DEFAULT_FIT_MAX_POINTS = "250";
  const MIN_PPU = 1.0e-3; // far below Tk's 10 so large domains can be framed
  const MAX_PPU = 10000000.0;

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------

  let S = null; // latest server snapshot
  const edgesById = new Map();
  const verticesById = new Map();
  const curvesById = new Map();

  const U = {
    view: { x: 0.5, y: 0.5, ppu: 450 },
    sel: { vertex: null, edge: null, cp: null, curve: null, cpoint: null },
    boundaryMode: false,
    activeBoundary: null,
    spacingMode: false,
    spacingFirst: null,
    exportMode: false,
    split: null, // { edge, fraction, firstCells, secondCells }
    dragSplit: false,
    projection: null, // { stage, kind, vertices, edges, curves, direction, fit, tol, max }
    vertexPlacement: false,
    blockSelection: null, // null | [ids]
    propagate: false,
    drag: null, // { kind, target, changed, inflight, pending }
    pan: null,
    lastPressed: null,
    exportDraft: null,
    boundaryNameDraft: "",
    uiScale: 1,
    fittedOnce: false,
    statusError: false,
  };

  const hitItems = []; // rebuilt every draw, in draw order
  let redrawRequested = false;
  const view = { width: 1, height: 1 }; // CSS pixel size, refreshed on resize
  let fontFamily = "sans-serif";
  // Client-built mesh preview (port of preview.py) plus a cached bitmap layer
  // so panning and zooming never re-stroke hundreds of thousands of segments.
  const previewData = {
    version: -1, coarsening: 0, lineCount: 0, lengths: null, offsets: null, coords: null, bounds: null,
    stats: null,
  };
  const previewLayer = {
    canvas: null, ctx: null, version: -1, coarsening: 0, width: 0, height: 0, dpr: 0,
    vx: 0, vy: 0, ppu: 0, refreshTimer: null,
    strokeMs: null, // smoothed cost of one full re-stroke (screen-resolution equivalent)
    samples: 0,
  };
  const PREVIEW_DIRECT_BUDGET_MS = 16; // one 60 Hz frame: below this, re-stroke every frame
  const PREVIEW_WARMUP_FRAMES = 2; // ignore JIT warm-up before trusting the estimate
  const PREVIEW_SETTLE_MS = 50;
  const PREVIEW_SUPERSAMPLE = 1.5; // layer resolution relative to the screen

  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const els = {
    menus: document.getElementById("menus"),
    toolbar: document.getElementById("toolbar"),
    banner: document.getElementById("banner"),
    docTitle: document.getElementById("doc-title"),
    sidebarTitle: document.getElementById("sidebar-title"),
    selectionTitle: document.getElementById("selection-title"),
    panel: document.getElementById("panel"),
    help: document.getElementById("sidebar-help"),
    status: document.getElementById("status"),
    hud: document.getElementById("hud"),
    modalRoot: document.getElementById("modal-root"),
    canvasHost: document.getElementById("canvas-host"),
  };

  // ------------------------------------------------------------------
  // Small utilities
  // ------------------------------------------------------------------

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "html") node.innerHTML = value;
        else if (key.startsWith("on") && typeof value === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (key === "disabled" || key === "checked" || key === "selected") {
          node[key] = Boolean(value);
        } else if (key === "value") node.value = value;
        else node.setAttribute(key, value);
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function fmt(value) {
    if (value === 0) return "0";
    return Number(value).toPrecision(8).replace(/\.?0+$/, "").replace(/\.?0+e/, "e");
  }

  function fmtGrading(value) {
    if (value === 0) return "0";
    return Number(value).toPrecision(12).replace(/\.?0+$/, "").replace(/\.?0+e/, "e");
  }

  function fmtPercent(fraction) {
    return Number(fraction * 100).toPrecision(12).replace(/\.?0+$/, "");
  }

  function plural(count, word, suffix) {
    return count + " " + word + (count !== 1 ? (suffix === undefined ? "s" : suffix) : "");
  }

  function px(value) {
    return value * U.uiScale;
  }

  function edgeLabel(id) {
    const edge = edgesById.get(id);
    if (!edge) return id;
    return edge.vertices[0] + " — " + edge.vertices[1];
  }

  function parsePositiveInteger(text, label) {
    const trimmed = String(text).trim();
    if (!/^\d+$/.test(trimmed) || parseInt(trimmed, 10) < 1) {
      throw new Error(label + " must be a positive integer");
    }
    return parseInt(trimmed, 10);
  }

  function parseNumber(text, label) {
    const value = Number(String(text).trim());
    if (String(text).trim() === "" || !Number.isFinite(value)) {
      throw new Error(label + " must be a number");
    }
    return value;
  }

  function splitFractionFromText(text) {
    let value = String(text).trim();
    if (value.endsWith("%")) value = value.slice(0, -1).trim();
    const percentage = Number(value);
    if (value === "" || !Number.isFinite(percentage)) {
      throw new Error("Current split must be a percentage between 0 and 100");
    }
    if (!(percentage > 0 && percentage < 100)) {
      throw new Error("Current split must be strictly between 0 and 100 percent");
    }
    return percentage / 100;
  }

  function niceGridStep(raw) {
    const exponent = Math.floor(Math.log10(Math.max(raw, 1e-12)));
    const fraction = raw / Math.pow(10, exponent);
    let nice;
    if (fraction <= 1) nice = 1;
    else if (fraction <= 2) nice = 2;
    else if (fraction <= 5) nice = 5;
    else nice = 10;
    return nice * Math.pow(10, exponent);
  }

  function visibleControlPointIndices(count, selectedIndex) {
    if (count <= 0) return [];
    const stride = Math.max(1, Math.ceil(count / MAX_VISIBLE_CONTROL_POINTS));
    const indices = new Set();
    for (let index = 0; index < count; index += stride) indices.add(index);
    indices.add(count - 1);
    if (selectedIndex !== null && selectedIndex >= 0 && selectedIndex < count) {
      indices.add(selectedIndex);
    }
    return Array.from(indices).sort((a, b) => a - b);
  }

  // ------------------------------------------------------------------
  // Status and errors
  // ------------------------------------------------------------------

  function setStatus(message, isError) {
    U.statusError = Boolean(isError);
    els.status.textContent = message;
    els.status.classList.toggle("error", U.statusError);
  }

  function showError(title, error) {
    const message = error && error.message ? error.message : String(error);
    setStatus(message, true);
  }

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------

  async function apiPost(action, body) {
    const response = await fetch("/api/" + action, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-BlockDrawer-Token": TOKEN,
      },
      body: JSON.stringify(body || {}),
    });
    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error("The server returned an unreadable response");
    }
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || ("Request failed (" + response.status + ")"));
    }
    return data;
  }

  async function apiGet(path) {
    const separator = path.includes("?") ? "&" : "?";
    const response = await fetch(path + separator + "token=" + encodeURIComponent(TOKEN), {
      headers: { "X-BlockDrawer-Token": TOKEN },
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || ("Request failed (" + response.status + ")"));
    }
    return data;
  }

  /** Apply a registry command, record it, and refresh from the reply. */
  async function command(op, args, options) {
    const data = await apiPost("command", { op: op, args: args || {} });
    applyState(data.state, options || {});
    return data.result;
  }

  // ------------------------------------------------------------------
  // State application
  // ------------------------------------------------------------------

  function indexState() {
    edgesById.clear();
    verticesById.clear();
    curvesById.clear();
    for (const edge of S.edges) edgesById.set(edge.id, edge);
    for (const vertex of S.vertices) verticesById.set(vertex.id, vertex);
    for (const curve of S.curves) curvesById.set(curve.id, curve);
  }

  function prefs() {
    return S.preferences;
  }

  /** Install a snapshot and reconcile UI state, like Tk's _restore_model. */
  function applyState(state, options) {
    const opts = options || {};
    const previousScale = S ? S.preferences.ui_scale : null;
    S = state;
    indexState();
    if (previousScale !== S.preferences.ui_scale) applyUiScale();

    if (!verticesById.has(U.sel.vertex)) U.sel.vertex = null;
    if (!edgesById.has(U.sel.edge)) {
      U.sel.edge = null;
      U.sel.cp = null;
    } else if (U.sel.cp !== null) {
      const count = edgesById.get(U.sel.edge).control_points.length;
      U.sel.cp = count ? Math.min(U.sel.cp, count - 1) : null;
    }
    if (!curvesById.has(U.sel.curve)) {
      U.sel.curve = null;
      U.sel.cpoint = null;
    } else {
      const count = curvesById.get(U.sel.curve).points.length;
      U.sel.cpoint = U.sel.cpoint === null ? 0 : Math.min(U.sel.cpoint, count - 1);
    }
    if (U.activeBoundary !== null && !S.boundaries.some((b) => b.name === U.activeBoundary)) {
      U.activeBoundary = S.boundaries.length ? S.boundaries[0].name : null;
    }
    if (U.activeBoundary === null && S.boundaries.length && (U.boundaryMode || opts.reset)) {
      U.activeBoundary = S.boundaries[0].name;
    }
    if (U.split && !edgesById.has(U.split.edge)) clearSplit();
    if (U.spacingFirst !== null && !edgesById.has(U.spacingFirst)) U.spacingFirst = null;
    if (U.projection) {
      U.projection.vertices = U.projection.vertices.filter((id) => verticesById.has(id));
      U.projection.edges = U.projection.edges.filter((id) => edgesById.has(id));
      U.projection.curves = U.projection.curves.filter((id) => curvesById.has(id));
      if (!U.projection.vertices.length && !U.projection.edges.length) U.projection.kind = null;
    }
    if (opts.reset) {
      clearAllModes();
      U.sel = { vertex: null, edge: null, cp: null, curve: null, cpoint: null };
      U.activeBoundary = S.boundaries.length ? S.boundaries[0].name : null;
      U.exportDraft = null;
    }
    if (!prefs().show_block_mesh) {
      // Mirror apply_visibility: mesh selection and mesh modes cannot persist.
      clearSplit();
      U.projection = null;
      U.sel.vertex = null;
      U.sel.edge = null;
      U.sel.cp = null;
      U.blockSelection = null;
      U.vertexPlacement = false;
      U.spacingMode = false;
      U.spacingFirst = null;
      U.boundaryMode = false;
    }
    if (!prefs().show_geometry) {
      U.projection = null;
      U.sel.curve = null;
      U.sel.cpoint = null;
    }
    updateTitle();
    if (opts.valuesOnly) {
      // Mid-drag updates only move coordinates; the chrome cannot change.
      syncPanelValues();
    } else {
      renderToolbar();
      renderMenus();
      renderPanel();
    }
    requestRedraw();
  }

  function updateTitle() {
    const name = S.session.name;
    const marker = S.session.dirty ? "*" : "";
    document.title = marker + name + " — " + APP_NAME;
    els.docTitle.textContent = marker + name + (S.session.path ? "  ·  " + S.session.path : "");
  }

  function applyUiScale() {
    const value = S.preferences.ui_scale;
    U.uiScale = value === "auto" ? 1 : Number(value);
    document.documentElement.style.setProperty("--ui-scale", String(U.uiScale));
    resizeCanvas();
  }

  // ------------------------------------------------------------------
  // Mode helpers (ports of the Tk _clear_* helpers)
  // ------------------------------------------------------------------

  function clearSplit() {
    U.split = null;
    U.dragSplit = false;
  }

  function clearSelectionAndDrags() {
    U.sel = { vertex: null, edge: null, cp: null, curve: null, cpoint: null };
    U.drag = null;
  }

  function clearAllModes() {
    clearSplit();
    U.exportMode = false;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.boundaryMode = false;
    U.vertexPlacement = false;
    U.blockSelection = null;
    U.drag = null;
  }

  function anyModeActive() {
    return Boolean(
      U.split || U.exportMode || U.boundaryMode || U.spacingMode || U.projection
    );
  }

  // ------------------------------------------------------------------
  // View transform
  // ------------------------------------------------------------------

  function canvasSize() {
    return view;
  }

  function toScreen(x, y) {
    return [
      view.width / 2 + (x - U.view.x) * U.view.ppu,
      view.height / 2 - (y - U.view.y) * U.view.ppu,
    ];
  }

  function toWorld(sx, sy) {
    return [
      U.view.x + (sx - view.width / 2) / U.view.ppu,
      U.view.y - (sy - view.height / 2) / U.view.ppu,
    ];
  }

  function fitView() {
    if (!S) return;
    const xs = [];
    const ys = [];
    if (prefs().show_block_mesh) {
      for (const vertex of S.vertices) { xs.push(vertex.x); ys.push(vertex.y); }
      for (const edge of S.edges) {
        for (const point of edge.control_points) { xs.push(point[0]); ys.push(point[1]); }
        if (edge.type !== "line") {
          for (const point of edge.path) { xs.push(point[0]); ys.push(point[1]); }
        }
      }
    }
    if (prefs().show_geometry) {
      for (const curve of S.curves) {
        for (const point of curve.points) { xs.push(point[0]); ys.push(point[1]); }
        for (const point of curve.path) { xs.push(point[0]); ys.push(point[1]); }
      }
    }
    if (!xs.length) return;
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = Math.max(maxX - minX, 0.25);
    const spanY = Math.max(maxY - minY, 0.25);
    const { width, height } = canvasSize();
    const usableWidth = Math.max(width - px(100), px(100));
    const usableHeight = Math.max(height - px(100), px(100));
    U.view.x = (minX + maxX) / 2;
    U.view.y = (minY + maxY) / 2;
    U.view.ppu = Math.min(usableWidth / spanX, usableHeight / spanY);
    U.view.ppu = Math.max(MIN_PPU * U.uiScale, Math.min(U.view.ppu, 2000 * U.uiScale));
    requestRedraw();
  }

  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    view.width = Math.max(1, rect.width);
    view.height = Math.max(1, rect.height);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    requestRedraw();
  }

  // ------------------------------------------------------------------
  // Drawing
  // ------------------------------------------------------------------

  function requestRedraw() {
    if (redrawRequested) return;
    redrawRequested = true;
    window.requestAnimationFrame(() => {
      redrawRequested = false;
      draw();
    });
  }

  function font(points, weight) {
    return (weight === "bold" ? "600 " : "") + px(points * 1.25) + "px " + fontFamily;
  }

  /**
   * Per-entity level of detail. Each vertex is scaled by its shortest
   * incident edge on screen and each edge by its own chord, so a zoomed-out
   * mesh shows small markers while a fine boundary-layer block next to a
   * large farfield block keeps both readable at the same time.
   */
  function computeDetail() {
    const edgeChord = new Map();
    const vertexMin = new Map();
    for (const edge of S.edges) {
      const a = verticesById.get(edge.vertices[0]);
      const b = verticesById.get(edge.vertices[1]);
      if (!a || !b) continue;
      const length = Math.hypot(b.x - a.x, b.y - a.y) * U.view.ppu;
      edgeChord.set(edge.id, length);
      for (const id of edge.vertices) {
        const current = vertexMin.get(id);
        if (current === undefined || length < current) vertexMin.set(id, length);
      }
    }
    return { edgeChord, vertexMin };
  }

  function detailScale(lengthPx, fullAt) {
    if (lengthPx === undefined) return 1;
    return Math.max(0.15, Math.min(1, lengthPx / px(fullAt)));
  }

  function pathScreenLength(points) {
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      total += Math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]);
    }
    return total;
  }

  function strokePolyline(points, color, width, dash) {
    if (points.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let index = 1; index < points.length; index += 1) {
      ctx.lineTo(points[index][0], points[index][1]);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.setLineDash(dash || []);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function fillCircle(x, y, radius, fill, outline, outlineWidth) {
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (outline) { ctx.strokeStyle = outline; ctx.lineWidth = outlineWidth || 1; ctx.stroke(); }
  }

  function fillSquare(x, y, radius, fill, outline, outlineWidth) {
    ctx.beginPath();
    ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    if (outline) { ctx.strokeStyle = outline; ctx.lineWidth = outlineWidth || 1; ctx.stroke(); }
  }

  function text(x, y, value, color, points, weight, align, baseline) {
    ctx.font = font(points, weight);
    ctx.fillStyle = color;
    ctx.textAlign = align || "center";
    ctx.textBaseline = baseline || "middle";
    ctx.fillText(value, x, y);
  }

  function inView(point, bounds, margin) {
    return point[0] >= bounds.minX - margin && point[0] <= bounds.maxX + margin &&
      point[1] >= bounds.minY - margin && point[1] <= bounds.maxY + margin;
  }

  function anyInView(points, bounds, margin) {
    // A path that crosses the whole viewport still needs drawing, so also
    // accept bounding-box overlap.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const point of points) {
      if (point[0] < minX) minX = point[0];
      if (point[0] > maxX) maxX = point[0];
      if (point[1] < minY) minY = point[1];
      if (point[1] > maxY) maxY = point[1];
    }
    return !(maxX < bounds.minX - margin || minX > bounds.maxX + margin ||
      maxY < bounds.minY - margin || minY > bounds.maxY + margin);
  }

  function draw() {
    if (!S) return;
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = canvasSize();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    hitItems.length = 0;

    const topLeft = toWorld(-px(40), -px(40));
    const bottomRight = toWorld(width + px(40), height + px(40));
    const bounds = {
      minX: Math.min(topLeft[0], bottomRight[0]),
      maxX: Math.max(topLeft[0], bottomRight[0]),
      minY: Math.min(topLeft[1], bottomRight[1]),
      maxY: Math.max(topLeft[1], bottomRight[1]),
    };
    const margin = 0;
    const detail = computeDetail();
    U.detail = detail;

    drawGrid(width, height);
    if (prefs().show_mesh_preview) {
      ensurePreviewBuilt();
      if (previewData.coords) drawPreview(bounds);
    }
    if (!prefs().show_block_mesh) {
      if (prefs().show_geometry) drawCurves(bounds, margin);
      updateHud();
      return;
    }

    const nodeBatches = new Map(); // color -> { full: Path2D, small: Path2D }
    for (const edge of S.edges) drawEdge(edge, bounds, margin, detail, nodeBatches);
    flushNodeBatches(nodeBatches);
    drawSpacingLinks(bounds);
    drawVertices(bounds, detail);
    if (prefs().show_geometry) drawCurves(bounds, margin);
    if (U.split) drawSplitMarker(bounds);
    updateHud();
  }

  function updateHud() {
    let textValue = fmt(U.view.ppu) + " px/unit";
    if (prefs().show_mesh_preview && previewData.coords) {
      textValue += " · preview " + previewData.lineCount + " lines";
    }
    els.hud.textContent = textValue;
  }

  // ------------------------------------------------------------------
  // Mesh preview: OpenFOAM's edge-weighted transfinite interpolation in JS
  // ------------------------------------------------------------------

  function sampleIndices(cells, coarsening) {
    const indices = [];
    for (let index = 0; index <= cells; index += coarsening) indices.push(index);
    if (indices[indices.length - 1] !== cells) indices.push(cells);
    return indices;
  }

  /** Node fractions and positions of an edge in canonical order, indices 0..cells. */
  function edgeSamples(edge) {
    const a = verticesById.get(edge.vertices[0]);
    const b = verticesById.get(edge.vertices[1]);
    const cells = edge.cells;
    const fractions = new Float64Array(cells + 1);
    const xs = new Float64Array(cells + 1);
    const ys = new Float64Array(cells + 1);
    xs[0] = a.x; ys[0] = a.y; fractions[0] = 0;
    xs[cells] = b.x; ys[cells] = b.y; fractions[cells] = 1;
    for (let index = 1; index < cells; index += 1) {
      const node = edge.nodes[index - 1];
      xs[index] = node[0];
      ys[index] = node[1];
      fractions[index] = edge.node_fractions ? edge.node_fractions[index - 1] : index / cells;
    }
    return { cells, fractions, xs, ys };
  }

  function directedSample(samples, follows, localIndex, out) {
    const canonicalIndex = follows ? localIndex : samples.cells - localIndex;
    const fraction = samples.fractions[canonicalIndex];
    out.fraction = follows ? fraction : 1 - fraction;
    out.x = samples.xs[canonicalIndex];
    out.y = samples.ys[canonicalIndex];
  }

  function buildPreview(coarsening) {
    const started = performance.now();
    const samplesByEdge = new Map();
    for (const edge of S.edges) samplesByEdge.set(edge.id, edgeSamples(edge));
    const lengths = [];
    const chunks = [];
    let totalPoints = 0;
    let sampledNodes = 0;
    const bottom = {}, right = {}, top = {}, left = {};
    for (const block of S.blocks) {
      const [v0, v1, v2, v3] = block.vertices;
      const ids = [v0, v1, v2, v3];
      const edgeIds = [];
      const follows = [];
      for (let side = 0; side < 4; side += 1) {
        const first = ids[side], second = ids[(side + 1) % 4];
        const canonical = first < second;
        edgeIds.push(canonical ? first + "-" + second : second + "-" + first);
        follows.push(canonical);
      }
      // Local directions: bottom v0->v1, right v1->v2, top v3->v2, left v0->v3.
      const sBottom = samplesByEdge.get(edgeIds[0]);
      const sRight = samplesByEdge.get(edgeIds[1]);
      const sTop = samplesByEdge.get(edgeIds[2]);
      const sLeft = samplesByEdge.get(edgeIds[3]);
      if (!sBottom || !sRight || !sTop || !sLeft) continue;
      const xCells = sBottom.cells, yCells = sRight.cells;
      const xIndices = sampleIndices(xCells, coarsening);
      const yIndices = sampleIndices(yCells, coarsening);
      const nx = xIndices.length, ny = yIndices.length;
      sampledNodes += nx * ny;
      const c00 = verticesById.get(v0), c10 = verticesById.get(v1), c11 = verticesById.get(v2), c01 = verticesById.get(v3);
      // Precompute edge samples per index.
      const bottoms = new Float64Array(nx * 3), tops = new Float64Array(nx * 3);
      const rights = new Float64Array(ny * 3), lefts = new Float64Array(ny * 3);
      for (let i = 0; i < nx; i += 1) {
        directedSample(sBottom, follows[0], xIndices[i], bottom);
        bottoms[i * 3] = bottom.fraction; bottoms[i * 3 + 1] = bottom.x; bottoms[i * 3 + 2] = bottom.y;
        directedSample(sTop, !follows[2], xIndices[i], top);
        tops[i * 3] = top.fraction; tops[i * 3 + 1] = top.x; tops[i * 3 + 2] = top.y;
      }
      for (let j = 0; j < ny; j += 1) {
        directedSample(sRight, follows[1], yIndices[j], right);
        rights[j * 3] = right.fraction; rights[j * 3 + 1] = right.x; rights[j * 3 + 2] = right.y;
        directedSample(sLeft, !follows[3], yIndices[j], left);
        lefts[j * 3] = left.fraction; lefts[j * 3 + 1] = left.x; lefts[j * 3 + 2] = left.y;
      }
      const grid = new Float64Array(nx * ny * 2);
      for (let j = 0; j < ny; j += 1) {
        const rf = rights[j * 3], rx = rights[j * 3 + 1], ry = rights[j * 3 + 2];
        const lf = lefts[j * 3], lx = lefts[j * 3 + 1], ly = lefts[j * 3 + 2];
        for (let i = 0; i < nx; i += 1) {
          const bf = bottoms[i * 3], bx = bottoms[i * 3 + 1], by = bottoms[i * 3 + 2];
          const tf = tops[i * 3], tx = tops[i * 3 + 1], ty = tops[i * 3 + 2];
          let w0 = (1 - bf) * (1 - lf), w1 = bf * (1 - rf), w2 = tf * rf, w3 = (1 - tf) * lf;
          const sum = w0 + w1 + w2 + w3;
          w0 /= sum; w1 /= sum; w2 /= sum; w3 /= sum;
          const bottomWeight = w0 + w1, topWeight = w2 + w3, leftWeight = w0 + w3, rightWeight = w1 + w2;
          const sbx = c00.x + bf * (c10.x - c00.x), sby = c00.y + bf * (c10.y - c00.y);
          const stx = c01.x + tf * (c11.x - c01.x), sty = c01.y + tf * (c11.y - c01.y);
          const slx = c00.x + lf * (c01.x - c00.x), sly = c00.y + lf * (c01.y - c00.y);
          const srx = c10.x + rf * (c11.x - c10.x), sry = c10.y + rf * (c11.y - c10.y);
          const zx = w0 * c00.x + w1 * c10.x + w2 * c11.x + w3 * c01.x;
          const zy = w0 * c00.y + w1 * c10.y + w2 * c11.y + w3 * c01.y;
          const cx = bottomWeight * (bx - sbx) + topWeight * (tx - stx) + leftWeight * (lx - slx) + rightWeight * (rx - srx);
          const cy = bottomWeight * (by - sby) + topWeight * (ty - sty) + leftWeight * (ly - sly) + rightWeight * (ry - sry);
          const px0 = (bottomWeight * sbx + topWeight * stx + leftWeight * slx + rightWeight * srx + zx) / 3 + cx;
          const py0 = (bottomWeight * sby + topWeight * sty + leftWeight * sly + rightWeight * sry + zy) / 3 + cy;
          grid[(j * nx + i) * 2] = px0;
          grid[(j * nx + i) * 2 + 1] = py0;
        }
      }
      // Interior rows then interior columns, as preview.py emits them.
      for (let j = 0; j < ny; j += 1) {
        if (yIndices[j] === 0 || yIndices[j] === yCells) continue;
        const line = new Float32Array(nx * 2);
        for (let i = 0; i < nx; i += 1) {
          line[i * 2] = grid[(j * nx + i) * 2];
          line[i * 2 + 1] = grid[(j * nx + i) * 2 + 1];
        }
        chunks.push(line); lengths.push(nx); totalPoints += nx;
      }
      for (let i = 0; i < nx; i += 1) {
        if (xIndices[i] === 0 || xIndices[i] === xCells) continue;
        const line = new Float32Array(ny * 2);
        for (let j = 0; j < ny; j += 1) {
          line[j * 2] = grid[(j * nx + i) * 2];
          line[j * 2 + 1] = grid[(j * nx + i) * 2 + 1];
        }
        chunks.push(line); lengths.push(ny); totalPoints += ny;
      }
    }
    const coords = new Float32Array(totalPoints * 2);
    const offsets = new Uint32Array(lengths.length);
    const boxes = new Float32Array(lengths.length * 4);
    let offset = 0;
    for (let line = 0; line < chunks.length; line += 1) {
      const chunk = chunks[line];
      coords.set(chunk, offset);
      offsets[line] = offset;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (let k = 0; k < chunk.length; k += 2) {
        const x = chunk[k], y = chunk[k + 1];
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
      boxes[line * 4] = minX; boxes[line * 4 + 1] = minY; boxes[line * 4 + 2] = maxX; boxes[line * 4 + 3] = maxY;
      offset += chunk.length;
    }
    previewData.version = S.version;
    previewData.coarsening = coarsening;
    previewData.lineCount = lengths.length;
    previewData.lengths = Uint32Array.from(lengths);
    previewData.offsets = offsets;
    previewData.coords = coords;
    previewData.bounds = boxes;
    previewData.stats = {
      block_count: S.blocks.length,
      line_count: lengths.length,
      sampled_node_count: sampledNodes,
      point_count: totalPoints,
      build_ms: performance.now() - started,
    };
    previewLayer.version = -1; // force a fresh bitmap
    previewLayer.strokeMs = null; // re-measure whether direct drawing fits the budget
    previewLayer.samples = 0;
    syncPreviewPanel();
  }

  function ensurePreviewBuilt() {
    if (!S) return;
    const coarsening = prefs().preview_coarsening;
    if (previewData.version === S.version && previewData.coarsening === coarsening) return;
    buildPreview(coarsening);
  }

  function flushNodeBatches(batches) {
    for (const [color, batch] of batches) {
      if (batch.full) {
        ctx.fillStyle = "#ffffff";
        ctx.fill(batch.full);
        ctx.strokeStyle = color;
        ctx.lineWidth = px(1);
        ctx.stroke(batch.full);
      }
      if (batch.small) {
        ctx.globalAlpha = 0.7;
        ctx.fillStyle = color;
        ctx.fill(batch.small);
        ctx.globalAlpha = 1;
      }
    }
  }

  function drawGrid(width, height) {
    const step = niceGridStep(px(75) / U.view.ppu);
    const left = toWorld(0, 0);
    const right = toWorld(width, height);
    const minX = Math.min(left[0], right[0]), maxX = Math.max(left[0], right[0]);
    const minY = Math.min(left[1], right[1]), maxY = Math.max(left[1], right[1]);
    let x = Math.ceil(minX / step) * step;
    let guard = 0;
    while (x <= maxX + step * 0.01 && guard < 500) {
      const [sx] = toScreen(x, 0);
      const axis = Math.abs(x) < step * 1e-6;
      strokePolyline([[sx, 0], [sx, height]], axis ? "#9fb3c8" : "#e4e7eb", px(axis ? 2 : 1));
      if (!axis) text(sx + px(3), height - px(4), fmt(x), "#829ab1", 8, "normal", "left", "bottom");
      x += step;
      guard += 1;
    }
    let y = Math.ceil(minY / step) * step;
    guard = 0;
    while (y <= maxY + step * 0.01 && guard < 500) {
      const [, sy] = toScreen(0, y);
      const axis = Math.abs(y) < step * 1e-6;
      strokePolyline([[0, sy], [width, sy]], axis ? "#9fb3c8" : "#e4e7eb", px(axis ? 2 : 1));
      if (!axis) text(px(4), sy - px(3), fmt(y), "#829ab1", 8, "normal", "left", "bottom");
      y += step;
      guard += 1;
    }
    text(width - px(8), height - px(8), "x", "#52606d", 9, "normal", "right", "bottom");
    text(px(8), px(8), "y", "#52606d", 9, "normal", "left", "top");
  }

  /**
   * Blit the cached preview bitmap. An exact hit (same view) is a plain copy;
   * a pan is a translated copy; a zoom is a scaled copy. Any inexact blit
   * schedules a full re-render once the view has settled for 120 ms, so
   * interaction never waits for hundreds of thousands of segments.
   */
  function drawPreview(bounds) {
    const dpr = window.devicePixelRatio || 1;
    const layer = previewLayer;
    // Adaptive: when a full re-stroke fits the frame budget, draw it fresh
    // every frame and never show a scaled bitmap. Only expensive previews
    // fall back to the cached layer.
    if (layer.samples < PREVIEW_WARMUP_FRAMES || layer.strokeMs < PREVIEW_DIRECT_BUDGET_MS) {
      const started = performance.now();
      strokePreviewInto(ctx, bounds);
      recordStrokeCost(performance.now() - started, 1);
      return;
    }
    const fresh = layer.canvas && layer.version === previewData.version &&
      layer.coarsening === previewData.coarsening &&
      layer.width === view.width && layer.height === view.height && layer.dpr === dpr;
    if (!fresh) {
      renderPreviewLayer(dpr);
    }
    const exact = layer.ppu === U.view.ppu && layer.vx === U.view.x && layer.vy === U.view.y;
    if (exact) {
      ctx.drawImage(layer.canvas, 0, 0, view.width, view.height);
      return;
    }
    const scale = U.view.ppu / layer.ppu;
    const [centerX, centerY] = toScreen(layer.vx, layer.vy);
    // Supersampled layer pixels stay sharp when downscaled; once the zoom
    // exceeds the spare resolution, pixel duplication keeps line art crisper
    // than bilinear blur for the few milliseconds before the re-stroke.
    const upscaling = scale > PREVIEW_SUPERSAMPLE;
    ctx.imageSmoothingEnabled = !upscaling;
    if ("imageSmoothingQuality" in ctx) ctx.imageSmoothingQuality = "medium";
    ctx.drawImage(
      layer.canvas,
      centerX - (view.width / 2) * scale,
      centerY - (view.height / 2) * scale,
      view.width * scale,
      view.height * scale,
    );
    ctx.imageSmoothingEnabled = true;
    if (layer.refreshTimer !== null) window.clearTimeout(layer.refreshTimer);
    layer.refreshTimer = window.setTimeout(() => {
      layer.refreshTimer = null;
      layer.version = -1;
      requestRedraw();
    }, PREVIEW_SETTLE_MS);
  }

  /** Track re-stroke cost; the first frame is JIT warm-up and is discarded. */
  function recordStrokeCost(ms, pixelScale) {
    const layer = previewLayer;
    layer.samples += 1;
    if (layer.samples === 1) return;
    // Layer renders cover more pixels; normalize toward screen-resolution cost.
    const normalized = ms / Math.max(1, pixelScale);
    layer.strokeMs = layer.strokeMs === null ? normalized : layer.strokeMs * 0.7 + normalized * 0.3;
  }

  function renderPreviewLayer(dpr) {
    const layer = previewLayer;
    if (!layer.canvas) {
      layer.canvas = document.createElement("canvas");
      layer.ctx = layer.canvas.getContext("2d");
    }
    const density = dpr * PREVIEW_SUPERSAMPLE;
    const pixelWidth = Math.max(1, Math.round(view.width * density));
    const pixelHeight = Math.max(1, Math.round(view.height * density));
    if (layer.canvas.width !== pixelWidth || layer.canvas.height !== pixelHeight) {
      layer.canvas.width = pixelWidth;
      layer.canvas.height = pixelHeight;
    }
    const target = layer.ctx;
    target.setTransform(density, 0, 0, density, 0, 0);
    target.clearRect(0, 0, view.width, view.height);
    const topLeft = toWorld(-px(40), -px(40));
    const bottomRight = toWorld(view.width + px(40), view.height + px(40));
    const bounds = {
      minX: Math.min(topLeft[0], bottomRight[0]), maxX: Math.max(topLeft[0], bottomRight[0]),
      minY: Math.min(topLeft[1], bottomRight[1]), maxY: Math.max(topLeft[1], bottomRight[1]),
    };
    const started = performance.now();
    strokePreviewInto(target, bounds);
    recordStrokeCost(performance.now() - started, PREVIEW_SUPERSAMPLE);
    layer.version = previewData.version;
    layer.coarsening = previewData.coarsening;
    layer.width = view.width;
    layer.height = view.height;
    layer.dpr = dpr;
    layer.vx = U.view.x;
    layer.vy = U.view.y;
    layer.ppu = U.view.ppu;
  }

  function strokePreviewInto(target, bounds) {
    const { lengths, offsets, coords, bounds: boxes, lineCount } = previewData;
    const cx = view.width / 2, cy = view.height / 2, ppu = U.view.ppu, vx = U.view.x, vy = U.view.y;
    const minDelta = 0.6; // skip sub-pixel steps when zoomed out
    const ctx = target;
    ctx.beginPath();
    for (let line = 0; line < lineCount; line += 1) {
      const b = line * 4;
      if (boxes[b + 2] < bounds.minX || boxes[b] > bounds.maxX ||
        boxes[b + 3] < bounds.minY || boxes[b + 1] > bounds.maxY) continue;
      const count = lengths[line];
      let index = offsets[line];
      let lastX = cx + (coords[index] - vx) * ppu;
      let lastY = cy - (coords[index + 1] - vy) * ppu;
      ctx.moveTo(lastX, lastY);
      index += 2;
      for (let point = 1; point < count; point += 1, index += 2) {
        const sx = cx + (coords[index] - vx) * ppu;
        const sy = cy - (coords[index + 1] - vy) * ppu;
        if (point < count - 1 && Math.abs(sx - lastX) < minDelta && Math.abs(sy - lastY) < minDelta) continue;
        ctx.lineTo(sx, sy);
        lastX = sx;
        lastY = sy;
      }
    }
    ctx.strokeStyle = "#7fa9c2";
    ctx.lineWidth = px(1);
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  function previewInfoText() {
    const stats = previewData.stats;
    if (!stats || previewData.version !== S.version) return "Preview has not been built yet.";
    return plural(stats.block_count, "block") + " · " + stats.line_count + " interior lines · " +
      stats.sampled_node_count + " sampled nodes · built in the browser in " +
      stats.build_ms.toFixed(1) + " ms.";
  }

  function syncPreviewPanel() {
    const node = els.panel.querySelector('[data-sync="preview.info"]');
    if (node) node.textContent = previewInfoText();
  }

  function edgeColor(edge) {
    const selected = edge.id === U.sel.edge;
    const spacingStaged = U.spacingMode && edge.id === U.spacingFirst;
    const projectionSelected = U.projection && U.projection.edges.includes(edge.id);
    const boundaryColor = edge.boundary
      ? S.boundaries.find((b) => b.name === edge.boundary)?.color || null
      : null;
    let color;
    if (projectionSelected) color = "#9c36b5";
    else if (spacingStaged) color = "#7048a8";
    else if (boundaryColor) color = boundaryColor;
    else if (U.boundaryMode && !edge.exterior) color = "#9aa5b1";
    else color = selected ? "#e8590c" : "#334e68";
    const activeBoundaryEdge = U.boundaryMode && edge.boundary && edge.boundary === U.activeBoundary;
    const width = projectionSelected || activeBoundaryEdge || spacingStaged ? 5
      : selected ? 4 : boundaryColor ? 3 : 2;
    return { color, width, boundaryColor, selected };
  }

  function drawEdge(edge, bounds, margin, detail, nodeBatches) {
    if (!anyInView(edge.path, bounds, margin)) return;
    const { color, width, boundaryColor, selected } = edgeColor(edge);
    const lod = detailScale(detail.edgeChord.get(edge.id), 70);
    const screen = edge.path.map((p) => toScreen(p[0], p[1]));
    strokePolyline(screen, color, px(width));
    hitItems.push({ kind: "edge", target: edge.id, type: "poly", points: screen, width: px(width) });

    if (prefs().show_edge_nodes && edge.nodes.length) {
      // Marker size follows the on-screen node spacing: full markers when
      // they are clearly separated, small dots when crowded, nothing when
      // the markers would merge into the edge itself.
      const spacing = pathScreenLength(screen) / edge.cells;
      let batch = nodeBatches.get(color);
      if (!batch) {
        batch = { full: null, small: null };
        nodeBatches.set(color, batch);
      }
      if (spacing >= px(6)) {
        const radius = px(2.4);
        if (!batch.full) batch.full = new Path2D();
        for (const node of edge.nodes) {
          if (!inView(node, bounds, 0)) continue;
          const [x, y] = toScreen(node[0], node[1]);
          batch.full.moveTo(x + radius, y);
          batch.full.arc(x, y, radius, 0, Math.PI * 2);
          hitItems.push({ kind: "edge", target: edge.id, type: "point", x, y, r: radius + px(2) });
        }
      } else if (spacing >= px(2.5)) {
        const radius = Math.max(px(0.6), spacing * 0.22);
        if (!batch.small) batch.small = new Path2D();
        for (const node of edge.nodes) {
          if (!inView(node, bounds, 0)) continue;
          const [x, y] = toScreen(node[0], node[1]);
          batch.small.moveTo(x + radius, y);
          batch.small.arc(x, y, radius, 0, Math.PI * 2);
        }
      }
    }

    const showLabel = lod >= 0.5 || selected || (U.boundaryMode && boundaryColor);
    if (prefs().show_edge_cell_counts && showLabel && inView(edge.midpoint, bounds, 0)) {
      const [mx, my] = toScreen(edge.midpoint[0], edge.midpoint[1]);
      text(mx, my - px(11), String(edge.cells), boundaryColor || (selected ? "#9c3d10" : "#52606d"),
        9, selected ? "bold" : "normal");
      hitItems.push({ kind: "edge", target: edge.id, type: "point", x: mx, y: my - px(11), r: px(10) });
    }

    if (prefs().show_edge_interpolation_points && edge.control_points.length) {
      const points = edge.control_points;
      const dense = points.length > MAX_VISIBLE_CONTROL_POINTS;
      const selectedIndex = selected ? U.sel.cp : null;
      const scale = selected ? 1 : Math.max(0.35, lod);
      for (const index of visibleControlPointIndices(points.length, selectedIndex)) {
        const point = points[index];
        if (!inView(point, bounds, 0)) continue;
        const [x, y] = toScreen(point[0], point[1]);
        const pointSelected = selected && index === U.sel.cp;
        const radius = px(pointSelected ? 8 : dense ? 3 : 6) * (pointSelected ? 1 : scale);
        fillCircle(x, y, radius, "#7048a8", pointSelected ? "#e8590c" : "#ffffff", px(pointSelected ? 2 : 1.5 * scale));
        hitItems.push({
          kind: "control_point", target: [edge.id, index], type: "point", x, y, r: Math.max(radius, px(4)) + px(2),
        });
        if (edge.type !== "arc" && (!dense || pointSelected) && radius >= px(4)) {
          text(x, y, String(index + 1), "#ffffff", 7, "bold");
        }
      }
    }
  }

  function drawSpacingLinks(bounds) {
    const markerLength = px(18);
    for (const link of S.spacing_links) {
      const vertex = verticesById.get(link.vertex);
      if (!vertex || !inView([vertex.x, vertex.y], bounds, 0)) continue;
      const [cx, cy] = toScreen(vertex.x, vertex.y);
      link.edges.forEach((edgeId, index) => {
        const tip = link.legs[index];
        const [tx, ty] = toScreen(tip[0], tip[1]);
        const dx = tx - cx, dy = ty - cy;
        const distance = Math.hypot(dx, dy);
        if (distance <= 0) return;
        const scale = Math.min(1, markerLength / distance);
        const end = [cx + dx * scale, cy + dy * scale];
        strokePolyline([[cx, cy], end], "#0b8f87", px(4));
        hitItems.push({ kind: "edge", target: edgeId, type: "poly", points: [[cx, cy], end], width: px(4) });
      });
    }
  }

  function drawVertices(bounds, detail) {
    for (const vertex of S.vertices) {
      if (!inView([vertex.x, vertex.y], bounds, 0)) continue;
      const lod = detailScale(detail.vertexMin.get(vertex.id), 60);
      const scale = Math.max(0.2, lod);
      const showIds = prefs().show_vertex_ids && lod >= 0.5;
      const [x, y] = toScreen(vertex.x, vertex.y);
      const selected = vertex.id === U.sel.vertex;
      const projectionSelected = U.projection && U.projection.vertices.includes(vertex.id);
      const stagedIndex = U.blockSelection ? U.blockSelection.indexOf(vertex.id) : -1;
      const emphasized = selected || projectionSelected || stagedIndex >= 0;
      const radius = emphasized ? px(9) * Math.max(0.6, scale) : px(7) * scale;
      const fill = stagedIndex >= 0 ? "#7048a8" : projectionSelected ? "#9c36b5"
        : selected ? "#e8590c" : "#1971c2";
      fillCircle(x, y, radius, fill, "#ffffff", px(2) * Math.max(0.5, scale));
      hitItems.push({ kind: "vertex", target: vertex.id, type: "point", x, y, r: Math.max(radius, px(4)) + px(2) });
      if (stagedIndex >= 0 && radius >= px(4)) text(x, y, String(stagedIndex + 1), "#ffffff", 7, "bold");
      if (showIds || (prefs().show_vertex_ids && emphasized)) {
        const offset = Math.max(px(6), radius + px(3));
        text(x + offset, y + offset, vertex.id, emphasized ? "#102a43" : "#243b53", 9, emphasized ? "bold" : "normal", "left", "top");
        hitItems.push({ kind: "vertex", target: vertex.id, type: "point", x: x + offset + px(11), y: y + offset + px(5), r: px(12) });
      }
    }
  }

  function drawCurves(bounds, margin) {
    for (const curve of S.curves) {
      if (!anyInView(curve.path, bounds, margin)) continue;
      const selected = curve.id === U.sel.curve;
      const projectionSelected = U.projection && U.projection.curves.includes(curve.id);
      const screen = curve.path.map((p) => toScreen(p[0], p[1]));
      const color = projectionSelected ? "#087f5b" : selected ? "#006d77" : "#0096a6";
      const width = px(projectionSelected ? 5 : selected ? 4 : 3);
      strokePolyline(screen, color, width, [px(7), px(4)]);
      hitItems.push({ kind: "geometry_curve", target: curve.id, type: "poly", points: screen, width });

      if (inView(curve.label_point, bounds, 0)) {
        const [lx, ly] = toScreen(curve.label_point[0], curve.label_point[1]);
        text(lx, ly - px(13), curve.name, projectionSelected ? "#087f5b" : "#006d77", 9,
          selected || projectionSelected ? "bold" : "normal");
        hitItems.push({ kind: "geometry_curve", target: curve.id, type: "point", x: lx, y: ly - px(13), r: px(14) });
      }
      if (!curve.show_points) continue;
      curve.points.forEach((point, index) => {
        if (!inView(point, bounds, 0)) return;
        const [x, y] = toScreen(point[0], point[1]);
        const pointSelected = selected && index === U.sel.cpoint;
        const radius = px(pointSelected ? 8 : 6);
        fillSquare(x, y, radius, pointSelected ? "#e67700" : "#12b8b0", "#ffffff", px(2));
        hitItems.push({ kind: "geometry_point", target: [curve.id, index], type: "point", x, y, r: radius + px(2) });
        text(x, y, String(index + 1), "#ffffff", 7, "bold");
      });
    }
  }

  function drawSplitMarker(bounds) {
    const edge = edgesById.get(U.split.edge);
    if (!edge || !U.split.point) return;
    if (!inView(U.split.point, bounds, 0)) return;
    const [x, y] = toScreen(U.split.point[0], U.split.point[1]);
    const radius = px(10);
    ctx.beginPath();
    ctx.moveTo(x, y - radius);
    ctx.lineTo(x + radius, y);
    ctx.lineTo(x, y + radius);
    ctx.lineTo(x - radius, y);
    ctx.closePath();
    ctx.fillStyle = "#9c36b5";
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = px(2);
    ctx.stroke();
    text(x, y - radius - px(8), (U.split.fraction * 100).toFixed(1) + "%", "#7b2cbf", 9, "bold");
    hitItems.push({ kind: "split_marker", target: U.split.edge, type: "point", x, y, r: radius + px(3) });
  }

  // ------------------------------------------------------------------
  // Hit testing
  // ------------------------------------------------------------------

  function distanceToSegment(px0, py0, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const lengthSquared = dx * dx + dy * dy;
    let t = lengthSquared === 0 ? 0 : ((px0 - ax) * dx + (py0 - ay) * dy) / lengthSquared;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px0 - (ax + t * dx), py0 - (ay + t * dy));
  }

  function itemHit(item, x, y, slack) {
    if (item.type === "point") return Math.hypot(item.x - x, item.y - y) <= item.r + slack;
    const tolerance = item.width / 2 + slack;
    for (let index = 1; index < item.points.length; index += 1) {
      const a = item.points[index - 1], b = item.points[index];
      if (distanceToSegment(x, y, a[0], a[1], b[0], b[1]) <= tolerance) return true;
    }
    return false;
  }

  /** Topmost item under the pointer, like Tk's "current" tag. */
  function targetAt(x, y, kinds) {
    const slack = px(4);
    for (let index = hitItems.length - 1; index >= 0; index -= 1) {
      const item = hitItems[index];
      if (kinds && !kinds.includes(item.kind)) continue;
      if (itemHit(item, x, y, slack)) return { kind: item.kind, target: item.target };
    }
    return null;
  }

  function projectionTargetAt(x, y) {
    const stage = U.projection;
    let kinds;
    if (stage.stage === "curves") kinds = ["geometry_point", "geometry_curve"];
    else if (stage.kind === "edge") kinds = ["control_point", "edge"];
    else if (stage.kind === "vertex") kinds = ["vertex"];
    else kinds = ["vertex", "control_point", "edge"];
    for (const kind of kinds) {
      const found = targetAt(x, y, [kind]);
      if (found) return found;
    }
    return null;
  }

  // ------------------------------------------------------------------
  // Pointer interaction
  // ------------------------------------------------------------------

  function pointerPosition(event) {
    const rect = canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  canvas.addEventListener("contextmenu", (event) => event.preventDefault());

  canvas.addEventListener("pointerdown", (event) => {
    if (!S) return;
    canvas.focus();
    closeMenus();
    const [x, y] = pointerPosition(event);
    if (event.button === 1 || event.button === 2) {
      U.pan = { sx: x, sy: y, vx: U.view.x, vy: U.view.y };
      canvas.classList.add("grabbing");
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    onLeftPress(x, y, event);
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!S) return;
    const [x, y] = pointerPosition(event);
    if (U.pan) {
      U.view.x = U.pan.vx - (x - U.pan.sx) / U.view.ppu;
      U.view.y = U.pan.vy + (y - U.pan.sy) / U.view.ppu;
      requestRedraw();
      return;
    }
    if (U.dragSplit) {
      updateSplitFromPointer(x, y);
      return;
    }
    if (U.drag) onLeftDrag(x, y);
  });

  function endPointer(event) {
    if (U.pan) {
      U.pan = null;
      canvas.classList.remove("grabbing");
      return;
    }
    onLeftRelease();
  }

  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);

  canvas.addEventListener("dblclick", (event) => {
    if (!S || anyModeActive()) return;
    const [x, y] = pointerPosition(event);
    const target = targetAt(x, y) || U.lastPressed;
    if (!target || target.kind !== "edge") return;
    const edge = edgesById.get(target.target);
    if (!edge) return;
    U.sel = { vertex: null, edge: edge.id, cp: edge.control_points.length ? 0 : null, curve: null, cpoint: null };
    if (edge.exterior) addSelectedBlock();
  });

  canvas.addEventListener("wheel", (event) => {
    if (!S) return;
    event.preventDefault();
    const [x, y] = pointerPosition(event);
    const [bx, by] = toWorld(x, y);
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    U.view.ppu = Math.max(MIN_PPU * U.uiScale, Math.min(MAX_PPU * U.uiScale, U.view.ppu * factor));
    const { width, height } = canvasSize();
    U.view.x = bx - (x - width / 2) / U.view.ppu;
    U.view.y = by + (y - height / 2) / U.view.ppu;
    requestRedraw();
  }, { passive: false });

  function onLeftPress(x, y, event) {
    let target = targetAt(x, y);
    if (U.projection) target = projectionTargetAt(x, y);
    U.lastPressed = target;
    U.drag = null;

    if (U.split) {
      U.dragSplit = true;
      updateSplitFromPointer(x, y);
      setStatus("Positioning split marker; release it anywhere, then press Enter or use Execute split.");
      return;
    }
    if (U.exportMode) {
      setStatus("Export settings are open; press E or Esc to return to editing.");
      return;
    }
    if (U.projection) {
      toggleProjectionTarget(target);
      return;
    }
    if (U.spacingMode) {
      let current = null;
      if (target && target.kind === "edge") current = target.target;
      else if (target && target.kind === "control_point") current = target.target[0];
      if (current === null) {
        setStatus("Spacing links: click a mesh edge, or press Esc to finish.");
        return;
      }
      selectSpacingLinkEdge(current);
      return;
    }
    if (U.boundaryMode) {
      boundaryClick(target);
      return;
    }
    if (U.vertexPlacement) {
      if (target) {
        setStatus("Vertex placement: click an empty canvas location.");
        return;
      }
      const [wx, wy] = toWorld(x, y);
      placeVertex(wx, wy);
      return;
    }
    if (U.blockSelection) {
      if (target && target.kind === "vertex") toggleBlockVertex(target.target);
      else setStatus("New block mode: click an existing vertex or press Esc.");
      return;
    }
    if (!target) {
      U.sel = { vertex: null, edge: null, cp: null, curve: null, cpoint: null };
    } else if (target.kind === "vertex") {
      U.sel = { vertex: target.target, edge: null, cp: null, curve: null, cpoint: null };
      U.drag = { kind: "vertex", target: target.target, changed: false, inflight: false, pending: null };
    } else if (target.kind === "edge") {
      const edge = edgesById.get(target.target);
      U.sel = { vertex: null, edge: edge.id, cp: edge.control_points.length ? 0 : null, curve: null, cpoint: null };
    } else if (target.kind === "control_point") {
      const [edgeId, index] = target.target;
      U.sel = { vertex: null, edge: edgeId, cp: index, curve: null, cpoint: null };
      U.drag = { kind: "control_point", target: [edgeId, index], changed: false, inflight: false, pending: null };
    } else if (target.kind === "geometry_curve") {
      const curveId = target.target;
      const pointIndex = curveId === U.sel.curve && U.sel.cpoint !== null ? U.sel.cpoint : 0;
      U.sel = { vertex: null, edge: null, cp: null, curve: curveId, cpoint: pointIndex };
    } else if (target.kind === "geometry_point") {
      const [curveId, index] = target.target;
      U.sel = { vertex: null, edge: null, cp: null, curve: curveId, cpoint: index };
      U.drag = { kind: "geometry_point", target: [curveId, index], changed: false, inflight: false, pending: null };
    }
    renderPanel();
    renderToolbar();
    renderMenus();
    requestRedraw();
  }

  function onLeftDrag(x, y) {
    const drag = U.drag;
    if (!drag) return;
    const [wx, wy] = toWorld(x, y);
    drag.pending = [wx, wy];
    flushDrag();
  }

  function flushDrag() {
    const drag = U.drag;
    if (!drag || drag.inflight || !drag.pending) return;
    const [wx, wy] = drag.pending;
    drag.pending = null;
    drag.inflight = true;
    apiPost("drag", { kind: drag.kind, target: drag.target, x: wx, y: wy })
      .then((data) => {
        drag.changed = true;
        applyState(data.state, { valuesOnly: true });
        let name;
        if (drag.kind === "vertex") name = drag.target;
        else if (drag.kind === "control_point") name = "Point " + (drag.target[1] + 1);
        else name = "Geometry point " + (drag.target[1] + 1);
        setStatus(name + ": (" + fmt(wx) + ", " + fmt(wy) + ")");
      })
      .catch((error) => setStatus(error.message, true))
      .finally(() => {
        drag.inflight = false;
        if (U.drag === drag) flushDrag();
        else if (drag.finish) drag.finish();
      });
  }

  function onLeftRelease() {
    if (U.dragSplit) {
      U.dragSplit = false;
      setStatus("Split location set. Reposition it if needed, then press Enter or use Execute split.");
      return;
    }
    const drag = U.drag;
    U.drag = null;
    if (!drag) return;
    const finish = () => {
      if (!drag.changed) return;
      apiPost("drag_end", {})
        .then((data) => applyState(data.state, {}))
        .catch((error) => setStatus(error.message, true));
    };
    if (drag.inflight) drag.finish = finish;
    else finish();
  }

  // ------------------------------------------------------------------
  // Editing actions (ports of editing.py)
  // ------------------------------------------------------------------

  async function placeVertex(x, y) {
    try {
      const result = await command("add_vertex", { x, y }, {});
      U.vertexPlacement = false;
      U.lastPressed = null;
      U.sel = { vertex: result.vertex, edge: null, cp: null, curve: null, cpoint: null };
      renderPanel();
      requestRedraw();
      setStatus("Added standalone vertex " + result.vertex + " at (" + fmt(x) + ", " + fmt(y) + ").");
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  async function boundaryClick(target) {
    if (!target || target.kind !== "edge") {
      setStatus("Boundary mode: click an exterior edge, or press Esc to finish.");
      return;
    }
    const edge = edgesById.get(target.target);
    if (U.activeBoundary === null) {
      setStatus("Add and select a boundary before assigning edges.");
      return;
    }
    if (!edge.exterior) {
      setStatus("Internal edges cannot belong to a boundary patch.");
      return;
    }
    const existing = edge.boundary;
    const replacement = existing === U.activeBoundary ? null : U.activeBoundary;
    try {
      await command("set_edge_boundary", { edge: edge.id, name: replacement });
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    const label = edgeLabel(edge.id);
    if (replacement === null) setStatus("Unassigned edge " + label + ".");
    else if (existing === null) setStatus("Assigned edge " + label + " to '" + replacement + "'.");
    else setStatus("Reassigned edge " + label + " from '" + existing + "' to '" + replacement + "'.");
  }

  function toggleSpacingLinkMode() {
    const activating = !U.spacingMode;
    clearSplit();
    U.exportMode = false;
    U.projection = null;
    U.boundaryMode = false;
    U.spacingMode = activating;
    U.spacingFirst = activating && edgesById.has(U.sel.edge) ? U.sel.edge : null;
    U.vertexPlacement = false;
    U.blockSelection = null;
    U.sel.vertex = null;
    U.sel.cp = null;
    U.sel.curve = null;
    U.sel.cpoint = null;
    U.drag = null;
    refreshChrome();
    if (!activating) setStatus("Finished linking edge spacing.");
    else if (U.spacingFirst === null) setStatus("Spacing links: select the first edge of an incident pair.");
    else setStatus("Spacing links: selected the driver edge; now select an incident edge.");
  }

  async function selectSpacingLinkEdge(edgeId) {
    U.sel = { vertex: null, edge: edgeId, cp: null, curve: null, cpoint: null };
    const first = U.spacingFirst;
    if (first === null) {
      U.spacingFirst = edgeId;
      refreshChrome();
      setStatus("Spacing links: " + edgeLabel(edgeId) + " is the driver; select an incident edge.");
      return;
    }
    if (edgeId === first) {
      U.spacingFirst = null;
      refreshChrome();
      setStatus("Cleared the staged pair. Select a first edge when ready.");
      return;
    }
    try {
      const link = await command("add_spacing_link", { driver_edge: first, follower_edge: edgeId });
      U.spacingFirst = null;
      renderPanel();
      requestRedraw();
      setStatus("Linked cell widths at " + link.vertex + "; " + edgeLabel(first) + " drove " + edgeLabel(edgeId) + ".");
    } catch (error) {
      showError("Cannot link edge spacing", error);
      refreshChrome();
    }
  }

  async function synchronizeSelectedSpacingLinks() {
    if (U.sel.edge === null) return;
    const edge = edgesById.get(U.sel.edge);
    if (!edge.spacing_links.length) {
      setStatus("The selected edge has no spacing links to synchronize.");
      return;
    }
    try {
      const result = await command("synchronize_spacing_links", { edge: edge.id });
      setStatus("Synchronized " + plural(result.affected_edges.length, "spacing-linked edge") + " from the selected edge.");
    } catch (error) {
      showError("Cannot synchronize spacing links", error);
      syncPanelValues();
    }
  }

  async function removeSpacingLink(firstEdge, secondEdge) {
    try {
      const link = await command("remove_spacing_link", { first_edge: firstEdge, second_edge: secondEdge });
      U.spacingFirst = null;
      renderPanel();
      requestRedraw();
      setStatus("Removed the spacing link at vertex " + link.vertex + ".");
    } catch (error) {
      showError("Cannot remove spacing link", error);
    }
  }

  function toggleBoundaryMode() {
    U.boundaryMode = !U.boundaryMode;
    clearSplit();
    U.exportMode = false;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.vertexPlacement = false;
    U.blockSelection = null;
    clearSelectionAndDrags();
    if (U.boundaryMode && !S.boundaries.some((b) => b.name === U.activeBoundary)) {
      U.activeBoundary = S.boundaries.length ? S.boundaries[0].name : null;
    }
    refreshChrome();
    setStatus(U.boundaryMode
      ? "Boundary mode: select or add a patch, then click exterior edges."
      : "Finished setting boundaries.");
  }

  function toggleExportMode() {
    const activating = !U.exportMode;
    clearSplit();
    U.exportMode = activating;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.boundaryMode = false;
    U.vertexPlacement = false;
    U.blockSelection = null;
    clearSelectionAndDrags();
    if (activating) U.exportDraft = exportDraftFromModel();
    refreshChrome();
    setStatus(activating ? "Configure extrusion and z-face patches, then export." : "Closed export settings.");
  }

  function exportDraftFromModel() {
    const settings = S.settings;
    return {
      z_cells: String(settings.z_cells),
      z_min: fmt(settings.z_min),
      z_max: fmt(settings.z_max),
      scale: fmt(settings.scale),
      z_min_patch_name: settings.z_min_patch.name,
      z_min_patch_type: settings.z_min_patch.type,
      z_max_patch_name: settings.z_max_patch.name,
      z_max_patch_type: settings.z_max_patch.type,
    };
  }

  async function addBoundary() {
    const name = U.boundaryNameDraft.trim();
    try {
      const boundary = await command("add_boundary", { name });
      U.activeBoundary = boundary.name;
      U.boundaryNameDraft = "";
      renderPanel();
      requestRedraw();
      setStatus("Added boundary '" + boundary.name + "'; click exterior edges to assign it.");
    } catch (error) {
      showError("Cannot add boundary", error);
    }
  }

  async function removeActiveBoundary() {
    const name = U.activeBoundary;
    if (name === null) return;
    const boundary = S.boundaries.find((b) => b.name === name);
    const assigned = boundary ? boundary.edge_count : 0;
    try {
      await command("remove_boundary", { name });
      U.activeBoundary = S.boundaries.length ? S.boundaries[0].name : null;
      renderPanel();
      requestRedraw();
      setStatus("Removed boundary '" + name + "' and unassigned " + assigned + " edge(s). Use Undo to restore it.");
    } catch (error) {
      showError("Cannot remove boundary", error);
    }
  }

  async function applyBoundaryDefinition(kind, neighbour) {
    const name = U.activeBoundary;
    if (name === null) return;
    try {
      const result = await command("set_boundary_type", {
        name, type: kind, neighbour_patch: kind === "cyclic" ? (neighbour || null) : null,
      });
      if (kind === "cyclic") setStatus("Paired cyclic boundaries '" + name + "' and '" + neighbour + "'.");
      else setStatus("Set '" + name + "' to " + kind + "; updated " + result.affected.join(", ") + ".");
    } catch (error) {
      showError("Cannot set boundary type", error);
      renderPanel();
    }
  }

  async function applyVertex(xText, yText) {
    if (U.sel.vertex === null) return;
    try {
      await command("move_vertex", {
        vertex: U.sel.vertex, x: parseNumber(xText, "X"), y: parseNumber(yText, "Y"),
      }, { valuesOnly: true });
      setStatus("Moved vertex " + U.sel.vertex + ".");
    } catch (error) {
      showError("Invalid vertex coordinates", error);
      syncPanelValues();
    }
  }

  async function applyEdgeCells(text) {
    if (U.sel.edge === null) return;
    try {
      const cells = parsePositiveInteger(text, "Cells");
      const result = await command("set_edge_cells", { edge: U.sel.edge, cells });
      const affected = result.affected_edges.length;
      setStatus("Set " + cells + " cells on " + plural(affected, "topology-linked edge") + ".");
    } catch (error) {
      showError("Invalid cell count", error);
      syncPanelValues();
    }
  }

  async function applyEdgeGrading(parameter, valueText) {
    if (U.sel.edge === null) return;
    const edgeId = U.sel.edge;
    let value;
    try {
      value = parseNumber(valueText, "Grading value");
      const result = await command("set_edge_grading", {
        edge: edgeId, parameter, value, propagate: U.propagate,
      }, { valuesOnly: !U.spacingMode && !(edgesById.get(edgeId)?.spacing_links.length) });
      const labels = {
        cell_ratio: "cell-to-cell ratio",
        total_ratio: "total expansion ratio",
        start_width: "start-cell width",
        end_width: "end-cell width",
      };
      const edge = edgesById.get(edgeId);
      const affectedCount = U.propagate ? edge.constraint_count : 1;
      const unsynchronized = (result.spacing_links || []).filter((link) => link.synchronized === false).length;
      let spacingText = "";
      if (unsynchronized) {
        spacingText = " Applied the grading, but " + unsynchronized + " spacing link" +
          (unsynchronized !== 1 ? "s remain" : " remains") + " out of sync; press L to review or synchronize.";
      } else if ((result.spacing_links || []).length) {
        spacingText = " Synchronized spacing-linked edges.";
      }
      setStatus("Set " + labels[parameter] + " for edge " + edge.vertices[0] + " → " + edge.vertices[1] +
        "; updated " + plural(affectedCount, "cell-count-linked edge") + "." + spacingText);
      if (edge.spacing_links.length) renderPanel();
    } catch (error) {
      showError("Invalid edge grading", error);
      syncPanelValues();
    }
  }

  async function applyEdgeType(kind) {
    if (U.sel.edge === null) return;
    const edgeId = U.sel.edge;
    try {
      await command("set_edge_type", { edge: edgeId, type: kind });
      U.sel.cp = kind === "line" ? null : 0;
      renderPanel();
      requestRedraw();
      setStatus("Set edge " + edgeLabel(edgeId) + " to " + kind + ".");
    } catch (error) {
      showError("Invalid edge type", error);
      renderPanel();
    }
  }

  async function applyControlPoint(xText, yText) {
    if (U.sel.edge === null || U.sel.cp === null) return;
    const edgeId = U.sel.edge, index = U.sel.cp;
    try {
      await command("set_control_point", {
        edge: edgeId, index, x: parseNumber(xText, "Point X"), y: parseNumber(yText, "Point Y"),
      }, { valuesOnly: true });
      setStatus("Moved interpolation point " + (index + 1) + " on edge " + edgeLabel(edgeId) + ".");
    } catch (error) {
      showError("Invalid interpolation point", error);
      syncPanelValues();
    }
  }

  async function addEdgeControlPoint() {
    if (U.sel.edge === null || U.sel.cp === null) return;
    try {
      const result = await command("add_control_point", { edge: U.sel.edge, after_index: U.sel.cp });
      U.sel.cp = result.index;
      renderPanel();
      requestRedraw();
      setStatus("Added " + result.type + " interpolation point " + (result.index + 1) + ".");
    } catch (error) {
      showError("Cannot add interpolation point", error);
    }
  }

  async function removeEdgeControlPoint() {
    if (U.sel.edge === null || U.sel.cp === null) return;
    const removed = U.sel.cp;
    try {
      const result = await command("remove_control_point", { edge: U.sel.edge, index: removed });
      U.sel.cp = Math.min(removed, result.control_points.length - 1);
      renderPanel();
      requestRedraw();
      setStatus("Removed " + result.type + " interpolation point " + (removed + 1) + ".");
    } catch (error) {
      showError("Cannot remove interpolation point", error);
    }
  }

  async function resetEdgeControlPoints() {
    if (U.sel.edge === null) return;
    try {
      const result = await command("reset_control_points", { edge: U.sel.edge });
      setStatus("Reset " + result.type + " points to equidistant chord positions.");
    } catch (error) {
      showError("Cannot reset interpolation points", error);
    }
  }

  async function applyEdgeControlPointCount(text) {
    if (U.sel.edge === null) return;
    const edgeId = U.sel.edge;
    try {
      const count = parsePositiveInteger(text, "Interpolation point count");
      const result = await command("set_control_point_count", { edge: edgeId, count });
      U.sel.cp = Math.min(U.sel.cp || 0, count - 1);
      renderPanel();
      requestRedraw();
      setStatus("Set " + result.type + " edge " + edgeLabel(edgeId) + " to " +
        plural(count, "equidistant interpolation point") + ".");
    } catch (error) {
      showError("Invalid interpolation point count", error);
      syncPanelValues();
    }
  }

  async function addGeometryCurve() {
    const { width } = canvasSize();
    const visibleWidth = Math.max(width / U.view.ppu, 0.5);
    const halfSpan = Math.max(0.25, Math.min(1.0, visibleWidth * 0.2));
    try {
      const result = await command("add_curve", {
        points: [[U.view.x - halfSpan, U.view.y], [U.view.x + halfSpan, U.view.y]],
      });
      selectGeometryCurve(result.curve, 0);
      await ensureGeometryVisible();
      renderPanel();
      requestRedraw();
      setStatus("Added reference curve '" + result.name + "'; edit or drag its points.");
    } catch (error) {
      showError("Cannot add geometry curve", error);
    }
  }

  async function importGeometryCurve() {
    const path = await fileDialog({
      title: "Import reference-curve points", mode: "open", extensions: [".txt", ".dat", ".csv"],
    });
    if (!path) return;
    try {
      const result = await command("import_curve", { path, name: uniqueCurveName(basename(path)) });
      selectGeometryCurve(result.curve, 0);
      await ensureGeometryVisible();
      fitView();
      renderPanel();
      setStatus("Imported " + result.point_count + " points as geometry curve '" + result.name + "'.");
    } catch (error) {
      showError("Could not import geometry curve", error);
    }
  }

  async function replaceGeometryCurvePointsFromFile() {
    const curveId = U.sel.curve;
    if (curveId === null) return;
    const path = await fileDialog({
      title: "Replace reference-curve points", mode: "open", extensions: [".txt", ".dat", ".csv"],
    });
    if (!path) return;
    try {
      const result = await command("replace_curve_points_from_file", { curve: curveId, path });
      U.sel.cpoint = 0;
      fitView();
      renderPanel();
      setStatus("Replaced '" + result.name + "' with " + result.point_count + " imported points.");
    } catch (error) {
      showError("Could not replace geometry points", error);
    }
  }

  function basename(path) {
    const name = path.split(/[\\/]/).pop() || "curve";
    return name.replace(/\.[^.]*$/, "");
  }

  function uniqueCurveName(requested) {
    const base = requested.trim() || "curve";
    const used = new Set(S.curves.map((curve) => curve.name));
    if (!used.has(base)) return base;
    let index = 2;
    while (used.has(base + "_" + index)) index += 1;
    return base + "_" + index;
  }

  async function applyGeometryCurveName(name) {
    const curveId = U.sel.curve;
    if (curveId === null) return;
    try {
      const result = await command("rename_curve", { curve: curveId, name });
      setStatus("Renamed geometry curve to '" + result.name + "'.");
    } catch (error) {
      showError("Invalid geometry curve name", error);
      syncPanelValues();
    }
  }

  async function applyGeometryPointVisibility(visible) {
    const curveId = U.sel.curve;
    if (curveId === null) return;
    try {
      const result = await command("set_curve_show_points", { curve: curveId, visible });
      setStatus("Geometry points for '" + result.name + "' are " + (result.show_points ? "shown" : "hidden") + ".");
    } catch (error) {
      showError("Cannot change point visibility", error);
    }
  }

  async function applyGeometryCurvePoint(xText, yText) {
    const curveId = U.sel.curve, index = U.sel.cpoint;
    if (curveId === null || index === null) return;
    try {
      await command("set_curve_point", {
        curve: curveId, index, x: parseNumber(xText, "Point X"), y: parseNumber(yText, "Point Y"),
      }, { valuesOnly: true });
      setStatus("Moved geometry point " + (index + 1) + ".");
    } catch (error) {
      showError("Invalid geometry point", error);
      syncPanelValues();
    }
  }

  async function addGeometryCurvePoint() {
    const curveId = U.sel.curve, index = U.sel.cpoint;
    if (curveId === null || index === null) return;
    try {
      const result = await command("add_curve_point", { curve: curveId, after_index: index });
      U.sel.cpoint = result.index;
      renderPanel();
      requestRedraw();
      setStatus("Added geometry point " + (result.index + 1) + ".");
    } catch (error) {
      showError("Cannot add geometry point", error);
    }
  }

  async function removeGeometryCurvePoint() {
    const curveId = U.sel.curve, index = U.sel.cpoint;
    if (curveId === null || index === null) return;
    try {
      const result = await command("remove_curve_point", { curve: curveId, index });
      U.sel.cpoint = Math.min(index, result.point_count - 1);
      renderPanel();
      requestRedraw();
      setStatus("Removed geometry point " + (index + 1) + ".");
    } catch (error) {
      showError("Cannot remove geometry point", error);
    }
  }

  async function deleteGeometryCurve() {
    const curveId = U.sel.curve;
    if (curveId === null) return;
    const name = curvesById.get(curveId).name;
    try {
      await command("remove_curve", { curve: curveId });
      U.sel.curve = null;
      U.sel.cpoint = null;
      U.drag = null;
      renderPanel();
      requestRedraw();
      setStatus("Deleted geometry curve '" + name + "'.");
    } catch (error) {
      showError("Cannot delete geometry curve", error);
    }
  }

  function selectGeometryCurve(curveId, pointIndex) {
    U.sel = { vertex: null, edge: null, cp: null, curve: curveId, cpoint: pointIndex };
  }

  async function ensureGeometryVisible() {
    if (prefs().show_geometry) return;
    await setVisibility({ show_geometry: true });
  }

  // --- projection ------------------------------------------------------

  async function startProjection() {
    if (!S.curves.length) {
      setStatus("Add or import at least one reference curve before projecting.");
      return;
    }
    if (!prefs().show_block_mesh || !prefs().show_geometry) {
      await setVisibility({ show_block_mesh: true, show_geometry: true });
    }
    clearSplit();
    U.exportMode = false;
    U.boundaryMode = false;
    U.vertexPlacement = false;
    U.blockSelection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.projection = {
      stage: "entities", kind: null, vertices: [], edges: [], curves: [],
      direction: "orthogonal", fit: false, tol: FIT_RELATIVE_TOLERANCE, max: DEFAULT_FIT_MAX_POINTS,
    };
    clearSelectionAndDrags();
    refreshChrome();
    setStatus("Projection: select either mesh vertices or mesh edges, then continue.");
  }

  function continueProjection() {
    const p = U.projection;
    if (!p || p.stage !== "entities") return;
    if (!p.vertices.length && !p.edges.length) {
      setStatus("Select at least one mesh vertex or edge first.");
      return;
    }
    p.stage = "curves";
    p.curves = [];
    refreshChrome();
    setStatus("Projection: select one or more reference curves, choose a direction, then apply.");
  }

  function backProjection() {
    const p = U.projection;
    if (!p || p.stage !== "curves") return;
    p.stage = "entities";
    p.curves = [];
    refreshChrome();
    setStatus("Projection: adjust the selected mesh entities.");
  }

  function cancelProjection() {
    if (!U.projection) return;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    refreshChrome();
    setStatus("Cancelled projection selection.");
  }

  function toggleProjectionTarget(target) {
    const p = U.projection;
    if (p.stage === "entities") {
      let kind = null, identifier = null;
      if (target && target.kind === "vertex") { kind = "vertex"; identifier = target.target; }
      else if (target && target.kind === "edge") { kind = "edge"; identifier = target.target; }
      else if (target && target.kind === "control_point") { kind = "edge"; identifier = target.target[0]; }
      if (kind === null) {
        setStatus("Projection: click a mesh vertex or edge, or press Esc.");
        return;
      }
      if (p.kind !== null && p.kind !== kind) {
        setStatus("Vertices and edges cannot be mixed. Deselect all current entities before switching type.");
        return;
      }
      p.kind = kind;
      if (kind === "vertex") p.fit = false;
      const list = kind === "vertex" ? p.vertices : p.edges;
      const position = list.indexOf(identifier);
      if (position >= 0) list.splice(position, 1);
      else list.push(identifier);
      if (!list.length) p.kind = null;
      refreshChrome();
      setStatus("Projection: selected " + (p.vertices.length + p.edges.length) + " mesh entities.");
      return;
    }
    if (p.stage !== "curves") return;
    let curveId = null;
    if (target && target.kind === "geometry_curve") curveId = target.target;
    else if (target && target.kind === "geometry_point") curveId = target.target[0];
    if (curveId === null) {
      setStatus("Projection: click a reference curve, or press Esc to cancel.");
      return;
    }
    const position = p.curves.indexOf(curveId);
    if (position >= 0) p.curves.splice(position, 1);
    else p.curves.push(curveId);
    refreshChrome();
    setStatus("Projection: selected " + p.curves.length + " target curves.");
  }

  async function applyProjection() {
    const p = U.projection;
    if (!p || p.stage !== "curves") return;
    let tolerance = Number(FIT_RELATIVE_TOLERANCE);
    let maxPoints = Number(DEFAULT_FIT_MAX_POINTS);
    if (p.fit) {
      tolerance = Number(p.tol);
      maxPoints = Number(p.max);
      if (!Number.isFinite(tolerance) || tolerance <= 0) {
        showError("Invalid spline fit settings", new Error("Relative fit tolerance must be a positive number"));
        return;
      }
      if (!Number.isInteger(maxPoints) || maxPoints < 1) {
        showError("Invalid spline fit settings", new Error("Maximum spline points must be a positive integer"));
        return;
      }
    }
    try {
      const result = await command("project", {
        curves: p.curves, direction: p.direction, vertices: p.vertices, edges: p.edges,
        fit: p.fit, fit_tolerance: tolerance, fit_max_points: maxPoints,
      });
      U.projection = null;
      clearSelectionAndDrags();
      refreshChrome();
      let message = "Projected " + plural(result.projected_point_count, "mesh point") + " " + p.direction + ".";
      if (result.converted_arcs.length) {
        message += " Converted " + plural(result.converted_arcs.length, "arc") + " to spline.";
      }
      if (result.fitted_edges.length && result.max_fit_error !== null) {
        message += " Fitted " + plural(result.fitted_edges.length, "edge") + " with " +
          plural(result.fit_interpolation_point_count, "spline point") +
          " (maximum measured distance " + Number(result.max_fit_error).toPrecision(6) +
          "; target " + Number(result.fit_tolerance).toPrecision(6) +
          (result.fit_tolerance_met ? " met" : "; target not met") + ").";
      }
      setStatus(message);
    } catch (error) {
      showError("Cannot project selected entities", error);
    }
  }

  // --- split / combine / blocks ------------------------------------------

  async function startEdgeSplit() {
    if (U.sel.edge === null || !edgesById.has(U.sel.edge)) {
      setStatus("Select a mesh edge before starting a split.");
      return;
    }
    U.exportMode = false;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.boundaryMode = false;
    U.vertexPlacement = false;
    U.blockSelection = null;
    U.sel.vertex = null;
    U.sel.cp = null;
    U.sel.curve = null;
    U.sel.cpoint = null;
    U.drag = null;
    U.dragSplit = false;
    const edge = edgesById.get(U.sel.edge);
    U.split = { edge: edge.id, fraction: 0.5, firstCells: null, secondCells: null, point: edge.midpoint };
    try {
      let fraction = 0.5;
      if (edge.cells > 1) {
        // Start on the central existing mesh node, like the Tk editor.
        const probe = await apiPost("split_cells", { edge: edge.id, fraction: 0.5 });
        fraction = probe.result.fraction;
        const nodeIndex = Math.max(1, Math.floor(edge.cells / 2));
        const nodes = edge.nodes;
        if (nodes.length === edge.cells - 1) {
          const node = nodes[nodeIndex - 1];
          const snapped = await apiPost("edge_fraction", { edge: edge.id, x: node[0], y: node[1] });
          applySplitInfo(snapped.result);
        } else {
          applySplitInfo(probe.result);
        }
      } else {
        const probe = await apiPost("split_cells", { edge: edge.id, fraction });
        applySplitInfo(probe.result);
      }
    } catch (error) {
      setStatus(error.message, true);
    }
    refreshChrome();
    setStatus("Split mode: position the marker, then press Enter or use Execute split.");
  }

  function applySplitInfo(info) {
    if (!U.split || U.split.edge !== info.edge) return;
    U.split.fraction = info.fraction;
    U.split.firstCells = info.first_cells;
    U.split.secondCells = info.second_cells;
    U.split.point = info.point;
    syncSplitPanel();
    requestRedraw();
  }

  let splitQueryInflight = false;
  let splitQueryPending = null;

  function updateSplitFromPointer(x, y) {
    if (!U.split) return;
    splitQueryPending = toWorld(x, y);
    flushSplitQuery();
  }

  function flushSplitQuery() {
    if (splitQueryInflight || !splitQueryPending || !U.split) return;
    const [wx, wy] = splitQueryPending;
    splitQueryPending = null;
    splitQueryInflight = true;
    apiPost("edge_fraction", { edge: U.split.edge, x: wx, y: wy })
      .then((data) => applySplitInfo(data.result))
      .catch((error) => setStatus(error.message, true))
      .finally(() => {
        splitQueryInflight = false;
        flushSplitQuery();
      });
  }

  function cancelEdgeSplit() {
    if (!U.split) return;
    clearSplit();
    refreshChrome();
    setStatus("Cancelled edge split.");
  }

  async function executeEdgeSplit(fractionText) {
    if (!U.split) return;
    let fraction = U.split.fraction;
    if (fractionText !== undefined) {
      try {
        fraction = splitFractionFromText(fractionText);
      } catch (error) {
        showError("Cannot split edge", error);
        return;
      }
    }
    const edgeId = U.split.edge;
    try {
      const result = await command("split_edge", { edge: edgeId, fraction });
      clearSplit();
      U.sel = {
        vertex: null, edge: result.cut_edges.length ? result.cut_edges[0] : null,
        cp: null, curve: null, cpoint: null,
      };
      refreshChrome();
      setStatus("Split " + plural(result.new_block_ids.length, "block") + " at " +
        fmtPercent(result.fraction) + "% into " + result.first_cells + " + " + result.second_cells + " cells.");
    } catch (error) {
      showError("Cannot split edge", error);
      requestRedraw();
    }
  }

  async function combineSelectedBlocks() {
    if (anyModeActive()) {
      setStatus("Finish the active editing mode before combining blocks.");
      return;
    }
    if (U.sel.edge === null) {
      setStatus("Select an internal edge before combining blocks.");
      return;
    }
    try {
      const result = await command("combine_blocks", { edge: U.sel.edge });
      U.sel = {
        vertex: null, edge: result.merged_edges.length ? result.merged_edges[0] : null,
        cp: null, curve: null, cpoint: null,
      };
      refreshChrome();
      setStatus("Combined " + plural(result.removed_block_ids.length, "block pair") + "; removed " +
        plural(result.removed_edges.length, "internal edge") + ".");
    } catch (error) {
      showError("Cannot combine blocks", error);
    }
  }

  async function addSelectedBlock() {
    if (U.sel.edge === null) {
      setStatus("Select an exterior edge before adding a block.");
      return;
    }
    try {
      const result = await command("add_block", { edge: U.sel.edge });
      U.sel = { vertex: null, edge: result.opposite_edge, cp: null, curve: null, cpoint: null };
      refreshChrome();
      setStatus("Added " + result.block + ". Its outer edge is selected for quick extension.");
    } catch (error) {
      showError("Cannot add block", error);
    }
  }

  function startBlockFromVertices() {
    clearSplit();
    U.exportMode = false;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.boundaryMode = false;
    U.vertexPlacement = false;
    U.blockSelection = [];
    clearSelectionAndDrags();
    refreshChrome();
    setStatus("New block mode: select four existing vertices; press Esc to cancel.");
  }

  function startVertexPlacement() {
    clearSplit();
    U.exportMode = false;
    U.projection = null;
    U.spacingMode = false;
    U.spacingFirst = null;
    U.boundaryMode = false;
    U.vertexPlacement = true;
    U.blockSelection = null;
    clearSelectionAndDrags();
    refreshChrome();
    setStatus("Vertex placement: click an empty canvas location; press Esc to cancel.");
  }

  function cancelVertexPlacement() {
    if (!U.vertexPlacement) return;
    U.vertexPlacement = false;
    refreshChrome();
    setStatus("Cancelled standalone vertex placement.");
  }

  function cancelBlockFromVertices() {
    if (!U.blockSelection) return;
    U.blockSelection = null;
    refreshChrome();
    setStatus("Cancelled new block vertex selection.");
  }

  async function toggleBlockVertex(vertexId) {
    const staged = U.blockSelection;
    if (!staged) return;
    const position = staged.indexOf(vertexId);
    if (position >= 0) staged.splice(position, 1);
    else if (staged.length < 4) staged.push(vertexId);
    else {
      setStatus("Four vertices are staged; deselect one or press Esc to restart.");
      return;
    }
    refreshChrome();
    if (staged.length < 4) {
      setStatus("Selected " + staged.length + " of 4 vertices.");
      return;
    }
    try {
      const result = await command("add_block_from_vertices", { vertices: staged.slice() });
      U.blockSelection = null;
      clearSelectionAndDrags();
      refreshChrome();
      setStatus("Added " + result.block + " from four existing vertices.");
    } catch (error) {
      setStatus("Cannot create block: " + error.message.replace(/^[^:]*: /, "") + ". Deselect a vertex or press Esc.", true);
    }
  }

  async function deleteSelectedEdge() {
    if (U.sel.edge === null) {
      setStatus("Select an edge before deleting it.");
      return;
    }
    const edge = edgesById.get(U.sel.edge);
    if (!edge.can_delete) {
      setStatus("Cannot delete this edge: at least one block must remain.");
      return;
    }
    try {
      const result = await command("remove_edge", { edge: edge.id });
      clearSelectionAndDrags();
      refreshChrome();
      setStatus("Deleted edge " + edge.vertices[0] + " — " + edge.vertices[1] + " and " +
        plural(result.removed_blocks.length, "incident block") + ".");
    } catch (error) {
      showError("Cannot delete edge", error);
    }
  }

  function deleteSelectedEntity() {
    if (U.sel.curve !== null) {
      deleteGeometryCurve();
      return;
    }
    deleteSelectedEdge();
  }

  // ------------------------------------------------------------------
  // Session actions
  // ------------------------------------------------------------------

  async function confirmDiscard() {
    if (!S.session.dirty) return true;
    const answer = await choiceDialog(
      "Unsaved changes",
      "Save this BlockDrawer session before continuing?",
      [["save", "Save", "primary"], ["discard", "Don't save", ""], ["cancel", "Cancel", ""]],
    );
    if (answer === "cancel" || answer === null) return false;
    if (answer === "save") return save();
    return true;
  }

  async function newSession() {
    if (!(await confirmDiscard())) return;
    try {
      const data = await apiPost("new", {});
      applyState(data.state, { reset: true });
      fitView();
      setStatus("Started a new session.");
    } catch (error) {
      showError("Could not start a new session", error);
    }
  }

  async function openSession() {
    if (!(await confirmDiscard())) return;
    const path = await fileDialog({ title: "Open BlockDrawer session", mode: "open", extensions: [".json"] });
    if (!path) return;
    await loadSessionPath(path);
  }

  async function openRecentSession(path) {
    if (!(await confirmDiscard())) return;
    await loadSessionPath(path, true);
  }

  async function loadSessionPath(path, fromRecent) {
    try {
      const data = await apiPost("open", { path });
      applyState(data.state, { reset: true });
      fitView();
      let message = "Loaded " + S.session.name + ".";
      if (data.warning) message += " " + data.warning;
      setStatus(message);
    } catch (error) {
      if (fromRecent && /Could not read/.test(error.message)) {
        try {
          const data = await apiPost("recent_remove", { path });
          applyState(data.state, {});
        } catch (inner) { /* ignore */ }
        setStatus("Recent session no longer exists: " + path, true);
        return;
      }
      showError("Could not open session", error);
    }
  }

  async function save() {
    if (!S.session.path) return saveAs();
    try {
      const data = await apiPost("save", {});
      applyState(data.state, {});
      let message = "Saved " + S.session.name + ".";
      if (data.warning) message += " " + data.warning;
      setStatus(message);
      return true;
    } catch (error) {
      showError("Could not save session", error);
      return false;
    }
  }

  async function saveAs() {
    const path = await fileDialog({
      title: "Save BlockDrawer session", mode: "save", extensions: [".json"],
      initialName: S.session.path ? S.session.name : "mesh-blocks.json",
    });
    if (!path) return false;
    try {
      const data = await apiPost("save", { path });
      applyState(data.state, {});
      let message = "Saved " + S.session.name + ".";
      if (data.warning) message += " " + data.warning;
      setStatus(message);
      return true;
    } catch (error) {
      showError("Could not save session", error);
      return false;
    }
  }

  async function exportDictionary() {
    const draft = U.exportDraft || exportDraftFromModel();
    let settings;
    try {
      settings = {
        z_cells: parsePositiveInteger(draft.z_cells, "Z cells"),
        z_min: parseNumber(draft.z_min, "zMin"),
        z_max: parseNumber(draft.z_max, "zMax"),
        scale: parseNumber(draft.scale, "Scale"),
        z_min_patch_name: draft.z_min_patch_name.trim(),
        z_min_patch_type: draft.z_min_patch_type,
        z_max_patch_name: draft.z_max_patch_name.trim(),
        z_max_patch_type: draft.z_max_patch_type,
      };
    } catch (error) {
      showError("Invalid export settings", error);
      return;
    }
    const path = await fileDialog({
      title: "Export OpenFOAM dictionary", mode: "save", initialName: "blockMeshDict",
      initialDir: S.session.path ? dirname(S.session.path) : null,
    });
    if (!path) return;
    try {
      const data = await apiPost("export", { path, settings });
      applyState(data.state, {});
      U.exportDraft = exportDraftFromModel();
      renderPanel();
      setStatus("Exported " + path + ". Run OpenFOAM blockMesh to generate the mesh.");
    } catch (error) {
      showError("Could not export blockMeshDict", error);
    }
  }

  function dirname(path) {
    const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
    return index > 0 ? path.slice(0, index) : path;
  }

  async function undo() {
    try {
      const data = await apiPost("undo", {});
      if (!data.changed) {
        setStatus("Nothing to undo.");
        return;
      }
      restoreModel(data.state);
      setStatus("Undid the last edit.");
    } catch (error) {
      showError("Could not undo", error);
    }
  }

  async function redo() {
    try {
      const data = await apiPost("redo", {});
      if (!data.changed) {
        setStatus("Nothing to redo.");
        return;
      }
      restoreModel(data.state);
      setStatus("Redid the last edit.");
    } catch (error) {
      showError("Could not redo", error);
    }
  }

  function restoreModel(state) {
    clearSplit();
    U.projection = null;
    U.spacingFirst = null;
    U.blockSelection = null;
    U.vertexPlacement = false;
    U.drag = null;
    applyState(state, {});
  }

  async function closeEditor() {
    if (!(await confirmDiscard())) return;
    try {
      await apiPost("shutdown", {});
    } catch (error) { /* the server is going away */ }
    document.body.innerHTML = "<div style=\"padding:3rem;font-family:sans-serif;color:#52606d\">" +
      "BlockDrawer closed. You can close this tab.</div>";
  }

  async function setVisibility(flags) {
    try {
      const data = await apiPost("visibility", { flags });
      applyState(data.state, {});
      const visible = [];
      if (prefs().show_block_mesh) visible.push("block mesh");
      if (prefs().show_geometry) visible.push("geometry");
      if (prefs().show_mesh_preview) visible.push("mesh preview");
      const label = visible.length ? visible.join(" and ") : "grid only";
      setStatus(data.warning ? "Showing " + label + "; " + data.warning : "Showing " + label + ".");
    } catch (error) {
      showError("Could not change visibility", error);
    }
  }

  async function applyMeshPreviewCoarsening(text) {
    let value;
    try {
      value = parsePositiveInteger(text, "Preview coarsening");
    } catch (error) {
      showError("Invalid preview coarsening", error);
      return;
    }
    try {
      const data = await apiPost("preview_coarsening", { value });
      applyState(data.state, {});
      setStatus(data.warning ? "Preview coarsening set to " + value + "; " + data.warning
        : "Preview coarsening set to " + value + ".");
    } catch (error) {
      showError("Invalid preview coarsening", error);
    }
  }

  async function applyUiScaleChoice(value) {
    try {
      const data = await apiPost("ui_scale", { value });
      applyState(data.state, {});
      fitView();
      const label = value === "auto" ? "system automatic" : value + "× system";
      setStatus(data.warning ? "UI scale set to " + label + ", but " + data.warning
        : "UI scale set to " + label + " and saved in " + S.session.config_path + ".");
    } catch (error) {
      showError("Could not change UI scale", error);
    }
  }

  async function clearRecentFiles() {
    if (!prefs().recent_files.length) {
      setStatus("The recent-session menu is already empty.");
      return;
    }
    try {
      const data = await apiPost("recent_clear", {});
      applyState(data.state, {});
      setStatus(data.warning ? "Cleared the recent-session menu. " + data.warning : "Cleared the recent-session menu.");
    } catch (error) {
      showError("Could not clear recent files", error);
    }
  }

  // ------------------------------------------------------------------
  // Panels (ports of panels.py)
  // ------------------------------------------------------------------

  function refreshChrome() {
    renderPanel();
    renderToolbar();
    renderMenus();
    requestRedraw();
  }

  function heading(textValue, cls) {
    return el("h3", { class: "panel-heading " + (cls || ""), text: textValue });
  }

  function note(textValue, cls) {
    return el("p", { class: "note " + (cls || ""), text: textValue });
  }

  function field(label, input) {
    return el("div", { class: "field" }, el("label", { text: label }), input);
  }

  function valueField(label, value, cls, sync) {
    return field(label, el("div", { class: "value " + (cls || ""), text: value, "data-sync": sync }));
  }

  function textInput(value, onConfirm, sync, extra) {
    const input = el("input", { class: "control", type: "text", value, "data-sync": sync, ...(extra || {}) });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        event.stopPropagation();
        onConfirm(input.value);
      }
    });
    return input;
  }

  function selectInput(options, value, onChange) {
    const select = el("select", { class: "control" });
    for (const option of options) {
      const [optionValue, label] = Array.isArray(option) ? option : [option, option];
      select.append(el("option", { value: optionValue, text: label, selected: optionValue === value }));
    }
    select.value = value;
    select.addEventListener("change", () => onChange(select.value));
    return select;
  }

  function button(label, onClick, cls, disabled) {
    return el("button", { class: "btn " + (cls || ""), type: "button", onClick, disabled: Boolean(disabled) }, label);
  }

  function checkbox(label, checked, onChange) {
    const input = el("input", { type: "checkbox", checked });
    input.addEventListener("change", () => onChange(input.checked));
    return el("label", { class: "check" }, input, el("span", { text: label }));
  }

  function setSidebar(title, cardTitle, help) {
    els.sidebarTitle.textContent = title;
    els.selectionTitle.textContent = cardTitle;
    els.help.textContent = help;
  }

  const DEFAULT_HELP = "Mouse wheel: zoom\nMiddle/right drag: pan\n" +
    "Double-click an exterior edge: add block\nV: place vertex · N: connect 4 vertices\n" +
    "S: split edge · Shift+S: combine\nB: boundaries · L: link spacing · P: project\n" +
    "M: preview · E: export · Esc: cancel";

  function renderPanel() {
    if (!S) return;
    const panel = els.panel;
    panel.replaceChildren();
    if (U.exportMode) {
      setSidebar("Export", "blockMeshDict export",
        "Configure extrusion and the automatic z-face patches.\nSettings take effect when the dictionary is exported.\nE or Esc: close export");
      buildExportPanel(panel);
      return;
    }
    if (U.projection) {
      setSidebar("Projection", "Project onto geometry",
        "P: restart projection selection\nClick a selected item again to deselect it.\nEsc: cancel projection");
      buildProjectionPanel(panel);
      return;
    }
    if (U.boundaryMode) {
      setSidebar("Boundaries", "Boundary patches",
        "Click an exterior edge to assign it to the selected patch.\nClick it again to unassign it.\nB or Esc: finish boundaries");
      buildBoundaryPanel(panel);
      return;
    }
    if (U.spacingMode) {
      setSidebar("Spacing links", "Cell spacing",
        "Select two incident edges to link their cell widths.\nCell counts require synchronized links; infeasible grading leaves links out of sync.\nL or Esc: finish spacing links");
      buildSpacingLinkPanel(panel);
      return;
    }
    if (U.split) {
      setSidebar("Split block", "Conformal edge split",
        "Click or drag on the selected edge to place the split.\nReposition it as often as needed, then press Enter or use Execute split.\nEsc: cancel split");
      buildSplitPanel(panel);
      return;
    }
    if (prefs().show_mesh_preview) {
      setSidebar("Mesh preview", "Visualization mesh",
        "The preview is visual only and is never exported.\nPan and zoom normally while it is visible.\nM: hide mesh preview");
      buildMeshPreviewPanel(panel);
      return;
    }
    setSidebar("Properties", "Selection", DEFAULT_HELP);
    if (U.vertexPlacement) {
      panel.append(
        heading("Add standalone vertex"),
        note("Click an empty canvas location. The new vertex can be moved normally and selected with N for a new block.", "purple"),
        button("Cancel vertex placement (Esc)", cancelVertexPlacement, "block"),
      );
    } else if (U.blockSelection) {
      const staged = U.blockSelection;
      panel.append(
        heading("New block from vertices"),
        note("Click four existing vertices in any order. Click a staged vertex again to deselect it."),
        note("Selected: " + staged.length + " / 4", "strong"),
        note(staged.length ? staged.map((id, index) => (index + 1) + ". " + id).join("\n") : "No vertices selected yet.", "purple"),
        button("Cancel vertex selection (Esc)", cancelBlockFromVertices, "block"),
      );
    } else if (U.sel.curve !== null) {
      buildGeometryCurvePanel(panel);
    } else if (U.sel.vertex !== null) {
      const vertex = verticesById.get(U.sel.vertex);
      const xInput = textInput(fmt(vertex.x), () => applyVertex(xInput.value, yInput.value), "vertex.x");
      const yInput = textInput(fmt(vertex.y), () => applyVertex(xInput.value, yInput.value), "vertex.y");
      panel.append(
        heading("Vertex " + vertex.id),
        field("X", xInput),
        field("Y", yInput),
        button("Apply coordinates", () => applyVertex(xInput.value, yInput.value), "block"),
      );
    } else if (U.sel.edge !== null) {
      buildEdgePanel(panel);
    } else {
      panel.append(heading("Nothing selected"), note("Click a mesh vertex, edge, or geometry curve."));
    }
  }

  function buildEdgePanel(panel) {
    const edge = edgesById.get(U.sel.edge);
    const points = edge.control_points;
    if (points.length) {
      if (U.sel.cp === null || U.sel.cp >= points.length) U.sel.cp = 0;
    } else {
      U.sel.cp = null;
    }
    const cellsInput = textInput(String(edge.cells), (value) => applyEdgeCells(value), "edge.cells");
    panel.append(
      heading("Edge " + edge.vertices[0] + " — " + edge.vertices[1]),
      field("Cells", cellsInput),
      field("Type", selectInput(EDGE_TYPES, edge.type, applyEdgeType)),
      note("Changing this value updates " + plural(edge.constraint_count, "linked edge") +
        ". The canvas shows the current graded mesh-node positions."),
      button("Apply cell count", () => applyEdgeCells(cellsInput.value), "block"),
    );
    buildEdgeGradingControls(panel, edge, true);

    if (points.length && U.sel.cp !== null) {
      const index = U.sel.cp;
      const [pointX, pointY] = points[index];
      panel.append(heading(edge.type === "arc" ? "Arc interpolation point" : edge.type + " interpolation points", "sub"));
      if (edge.type !== "arc") {
        const countInput = textInput(String(points.length), (value) => applyEdgeControlPointCount(value), "edge.point_count");
        panel.append(field("Point count", el("div", { class: "with-button" }, countInput,
          button("Set", () => applyEdgeControlPointCount(countInput.value), "small"))));
        panel.append(field("Selected point", selectInput(
          points.map((_, i) => [String(i), String(i + 1)]), String(index),
          (value) => { U.sel.cp = parseInt(value, 10); renderPanel(); requestRedraw(); },
        )));
      }
      const pxInput = textInput(fmt(pointX), () => applyControlPoint(pxInput.value, pyInput.value), "point.x");
      const pyInput = textInput(fmt(pointY), () => applyControlPoint(pxInput.value, pyInput.value), "point.y");
      panel.append(
        field("Point X", pxInput),
        field("Point Y", pyInput),
        button("Apply point coordinates", () => applyControlPoint(pxInput.value, pyInput.value), "block"),
      );
      if (edge.type !== "arc") {
        panel.append(el("div", { class: "row" },
          button("Add", addEdgeControlPoint),
          button("Remove", removeEdgeControlPoint, "", points.length <= 1),
          button("Reset", resetEdgeControlPoints),
        ));
      }
      panel.append(note(edge.type !== "arc"
        ? "Purple points are numbered in path order and can be dragged on the canvas."
        : "The purple point can also be dragged on the canvas.", "purple"));
    }

    if (edge.exterior) panel.append(button("Add block on this side", addSelectedBlock, "block"));
    else panel.append(note("Internal edge — already shared by two blocks"));
    const incident = edge.blocks.length;
    panel.append(button(
      edge.can_delete ? "Delete edge and " + plural(incident, "block") : "Cannot delete the final block",
      deleteSelectedEdge, "block danger", !edge.can_delete,
    ));
  }

  function buildEdgeGradingControls(panel, edge, includeHelp) {
    const grading = edge.grading;
    panel.append(heading("Grading " + edge.vertices[0] + " → " + edge.vertices[1], "sub"));
    panel.append(checkbox("Propagate to " + plural(edge.constraint_count, "cell-count-linked edge"),
      U.propagate, (checked) => { U.propagate = checked; }));
    panel.append(valueField("Edge length", fmtGrading(grading.length), "", "grading.length"));
    const rows = [
      ["Cell/cell ratio", "cell_ratio"],
      ["Total ratio", "total_ratio"],
      ["Start width", "start_width"],
      ["End width", "end_width"],
    ];
    for (const [label, parameter] of rows) {
      const input = textInput(fmtGrading(grading[parameter]), (value) => applyEdgeGrading(parameter, value), "grading." + parameter);
      panel.append(field(label, el("div", { class: "with-button" }, input,
        button("Set", () => applyEdgeGrading(parameter, input.value), "small"))));
    }
    if (includeHelp) {
      panel.append(note("Set any one value; the other three are recomputed. Total ratio is end width / start width in the arrow direction. Propagation preserves physical direction when linked edge arrows are reversed."));
      if (edge.spacing_links.length) {
        const unsynchronized = edge.spacing_links.filter((link) => !link.synchronized).length;
        panel.append(note("Spacing links: " + edge.spacing_links.length +
          (unsynchronized ? " (" + unsynchronized + " out of sync)." : " (matched).") +
          " Press L to review or synchronize.", unsynchronized ? "warn" : "ok"));
      }
    }
  }

  function buildSpacingLinkPanel(panel) {
    const staged = U.spacingFirst;
    if (U.sel.edge === null) {
      panel.append(heading("Select a driver edge"),
        note("Then select a second edge sharing one vertex. The second edge is regraded to match the driver's cell width."));
      return;
    }
    const edge = edgesById.get(U.sel.edge);
    const stageText = staged === edge.id ? "Driver selected—click the second incident edge."
      : staged !== null ? "Driver: " + edgeLabel(staged) : "Click an edge to begin another pair.";
    const cellsInput = textInput(String(edge.cells), (value) => applyEdgeCells(value), "edge.cells");
    panel.append(
      heading("Edge " + edge.vertices[0] + " — " + edge.vertices[1]),
      note(stageText, staged !== null ? "purple" : ""),
      field("Cells", cellsInput),
      button("Apply cell count", () => applyEdgeCells(cellsInput.value), "block"),
      note("Cell count also updates " + plural(edge.constraint_count, "opposite-edge topology constraint") + "."),
    );
    buildEdgeGradingControls(panel, edge, false);
    panel.append(el("div", { class: "divider" }));
    panel.append(heading("Endpoint links (" + edge.spacing_links.length + ")", "sub"));
    if (!edge.spacing_links.length) panel.append(note("This edge is not linked at either endpoint."));
    for (const link of edge.spacing_links) {
      panel.append(note("At " + link.vertex + ": " + edgeLabel(link.other_edge) + "\nWidths " +
        fmtGrading(link.width) + " / " + fmtGrading(link.other_width) +
        " (" + (link.synchronized ? "matched" : "out of sync") + ")", link.synchronized ? "ok" : "warn"));
      panel.append(button("Remove link at " + link.vertex, () => removeSpacingLink(edge.id, link.other_edge), "block"));
    }
    panel.append(button("Synchronize links from this edge", synchronizeSelectedSpacingLinks, "block", !edge.spacing_links.length));
  }

  function buildMeshPreviewPanel(panel) {
    const input = textInput(String(prefs().preview_coarsening), (value) => applyMeshPreviewCoarsening(value), "preview.coarsening");
    const info = note(previewInfoText(), "blue");
    info.dataset.sync = "preview.info";
    panel.append(
      heading("Structured mesh visualization"),
      info,
      field("Coarsening factor", input),
      button("Apply coarsening", () => applyMeshPreviewCoarsening(input.value), "block"),
      note("A factor of 1 uses every edge subdivision. A factor of 10 uses every tenth subdivision and always retains block corners. Curved and graded boundary locations are respected."),
    );
    // The preview panel still allows normal selection editing underneath.
    panel.append(el("div", { class: "divider" }));
    if (U.sel.vertex !== null) {
      const vertex = verticesById.get(U.sel.vertex);
      const xInput = textInput(fmt(vertex.x), () => applyVertex(xInput.value, yInput.value), "vertex.x");
      const yInput = textInput(fmt(vertex.y), () => applyVertex(xInput.value, yInput.value), "vertex.y");
      panel.append(heading("Vertex " + vertex.id, "sub"), field("X", xInput), field("Y", yInput),
        button("Apply coordinates", () => applyVertex(xInput.value, yInput.value), "block"));
    } else if (U.sel.edge !== null) {
      const edge = edgesById.get(U.sel.edge);
      const cellsInput = textInput(String(edge.cells), (value) => applyEdgeCells(value), "edge.cells");
      panel.append(heading("Edge " + edge.vertices[0] + " — " + edge.vertices[1], "sub"),
        field("Cells", cellsInput), button("Apply cell count", () => applyEdgeCells(cellsInput.value), "block"));
    } else {
      panel.append(note("Select a vertex or edge to edit it while the preview is visible."));
    }
  }

  function buildSplitPanel(panel) {
    const edge = edgesById.get(U.split.edge);
    const affectedEdges = edge.constraint_count;
    const affectedBlocks = S.blocks.filter((block) => {
      const ids = new Set();
      for (let index = 0; index < 4; index += 1) {
        const a = block.vertices[index], b = block.vertices[(index + 1) % 4];
        ids.add(a < b ? a + "-" + b : b + "-" + a);
      }
      return S.edges.some((candidate) => ids.has(candidate.id) && sameComponent(candidate, edge));
    }).length;
    const fractionInput = textInput(fmtPercent(U.split.fraction), (value) => executeEdgeSplit(value), "split.fraction");
    panel.append(
      heading("Edge " + edge.vertices[0] + " — " + edge.vertices[1], "purple"),
      note("The cut propagates across " + plural(affectedBlocks, "block") + " and splits " + plural(affectedEdges, "aligned edge") + "."),
      field("Current split (%)", fractionInput),
      valueField("Cell allocation", splitCellsText(), "purple", "split.cells"),
      note("The cell counts are chosen at the nearest existing mesh node. Arcs and polyLines retain their paths; splines are resampled from the original curve."),
      button("Execute split", () => executeEdgeSplit(fractionInput.value), "block primary"),
    );
  }

  function sameComponent(candidate, edge) {
    // The server reports constraint sizes per edge; two edges of equal
    // constraint component size that share a block are treated as aligned.
    // This is only used for the informational block count in the split panel.
    return candidate.constraint_count === edge.constraint_count &&
      candidate.cells === edge.cells;
  }

  function splitCellsText() {
    if (!U.split || U.split.firstCells === null) return "…";
    return U.split.firstCells + " + " + U.split.secondCells + " cells";
  }

  function syncSplitPanel() {
    const fractionInput = els.panel.querySelector('[data-sync="split.fraction"]');
    if (fractionInput && U.split) fractionInput.value = fmtPercent(U.split.fraction);
    const cells = els.panel.querySelector('[data-sync="split.cells"]');
    if (cells) cells.textContent = splitCellsText();
  }

  function buildExportPanel(panel) {
    const draft = U.exportDraft || (U.exportDraft = exportDraftFromModel());
    const bind = (key) => textInput(draft[key], () => { draft[key] = inputs[key].value; exportDictionary(); }, "export." + key);
    const inputs = {
      z_cells: bind("z_cells"), z_min: bind("z_min"), z_max: bind("z_max"), scale: bind("scale"),
      z_min_patch_name: bind("z_min_patch_name"), z_max_patch_name: bind("z_max_patch_name"),
    };
    for (const [key, input] of Object.entries(inputs)) {
      input.addEventListener("input", () => { draft[key] = input.value; });
    }
    const typeChanged = (which) => (value) => {
      if (value === "cyclic") {
        draft.z_min_patch_type = "cyclic";
        draft.z_max_patch_type = "cyclic";
      } else if (draft.z_min_patch_type === "cyclic" || draft.z_max_patch_type === "cyclic") {
        draft.z_min_patch_type = value;
        draft.z_max_patch_type = value;
      } else {
        draft[which] = value;
      }
      renderPanel();
    };
    panel.append(
      heading("Extrusion"),
      field("Z cells", inputs.z_cells),
      field("zMin", inputs.z_min),
      field("zMax", inputs.z_max),
      field("Scale", inputs.scale),
      note("Z cells defaults to 1 for a pseudo-2D mesh."),
      el("div", { class: "divider" }),
      heading("Automatic z-face patches"),
      field("zMin name", inputs.z_min_patch_name),
      field("zMin type", selectInput(BOUNDARY_TYPES, draft.z_min_patch_type, typeChanged("z_min_patch_type"))),
      field("zMax name", inputs.z_max_patch_name),
      field("zMax type", selectInput(BOUNDARY_TYPES, draft.z_max_patch_type, typeChanged("z_max_patch_type"))),
      note("The two patches are always written. Selecting cyclic for either face pairs both faces and writes reciprocal neighbourPatch entries automatically."),
      button("Export blockMeshDict…", exportDictionary, "block primary"),
    );
  }

  function buildProjectionPanel(panel) {
    const p = U.projection;
    if (p.stage === "entities") {
      const count = p.vertices.length + p.edges.length;
      const selected = p.kind === "vertex" ? p.vertices.join(", ")
        : p.kind === "edge" ? p.edges.map((id) => edgesById.get(id).vertices.join("—")).join(", ") : "None";
      panel.append(
        heading("1. Select mesh entities", "violet"),
        note("Click one or more mesh vertices or edges. The first selection fixes the entity type; vertices and edges cannot be mixed."),
        note("Selected: " + count + "\n" + selected, "violet"),
        button("Next: select target curves", continueProjection, "block primary", !count),
        button("Cancel projection (Esc)", cancelProjection, "block"),
      );
      return;
    }
    const selectedNames = p.curves.filter((id) => curvesById.has(id)).map((id) => curvesById.get(id).name);
    panel.append(
      heading("2. Select target curves", "green"),
      note("Click one or more teal reference curves. Each source point uses the nearest valid projection among them."),
      note("Targets: " + (selectedNames.join(", ") || "None"), "green"),
      field("Direction", selectInput(PROJECTION_DIRECTIONS.map(([label, value]) => [value, label]), p.direction,
        (value) => { p.direction = value; })),
      note("Along x/y moves parallel to that axis. Orthogonal uses the shortest path to the selected curves."),
    );
    const fitCheck = checkbox("Fit edge as spline", p.fit, (checked) => { p.fit = checked; renderPanel(); });
    fitCheck.querySelector("input").disabled = p.kind !== "edge";
    panel.append(fitCheck,
      note("Fit greedily adds points where they reduce the maximum geometric distance most. It stops at the requested tolerance or point limit."));
    if (p.kind === "edge" && p.fit) {
      const tolInput = textInput(p.tol, () => { p.tol = tolInput.value; applyProjection(); }, "projection.tol");
      const maxInput = textInput(p.max, () => { p.max = maxInput.value; applyProjection(); }, "projection.max");
      tolInput.addEventListener("input", () => { p.tol = tolInput.value; });
      maxInput.addEventListener("input", () => { p.max = maxInput.value; });
      panel.append(field("Relative tolerance", tolInput), field("Maximum points per edge", maxInput),
        note("The absolute target is the relative tolerance multiplied by the fitted curve-section size (with a 1e-12 floor)."));
    }
    panel.append(
      button("Project selected entities", applyProjection, "block primary", !selectedNames.length),
      button("Back to mesh entities", backProjection, "block"),
      button("Cancel projection (Esc)", cancelProjection, "block"),
    );
  }

  function buildGeometryCurvePanel(panel) {
    const curve = curvesById.get(U.sel.curve);
    if (!curve) return;
    if (U.sel.cpoint === null || U.sel.cpoint >= curve.points.length) U.sel.cpoint = 0;
    const index = U.sel.cpoint;
    const nameInput = textInput(curve.name, (value) => applyGeometryCurveName(value), "curve.name", { class: "control", style: "font-family: var(--font)" });
    const [pointX, pointY] = curve.points[index];
    const xInput = textInput(fmt(pointX), () => applyGeometryCurvePoint(xInput.value, yInput.value), "cpoint.x");
    const yInput = textInput(fmt(pointY), () => applyGeometryCurvePoint(xInput.value, yInput.value), "cpoint.y");
    panel.append(
      heading("Reference geometry curve", "teal"),
      field("Name", nameInput),
      button("Apply name", () => applyGeometryCurveName(nameInput.value), "block"),
      checkbox("Show curve points", curve.show_points, applyGeometryPointVisibility),
      field("Selected point", selectInput(curve.points.map((_, i) => [String(i), String(i + 1)]), String(index),
        (value) => { U.sel.cpoint = parseInt(value, 10); renderPanel(); requestRedraw(); })),
      field("Point X", xInput),
      field("Point Y", yInput),
      button("Apply point coordinates", () => applyGeometryCurvePoint(xInput.value, yInput.value), "block"),
      el("div", { class: "row" },
        button("Add point", addGeometryCurvePoint),
        button("Remove point", removeGeometryCurvePoint, "", curve.points.length <= 2)),
      button("Replace points from file…", replaceGeometryCurvePointsFromFile, "block"),
      button("Delete geometry curve", deleteGeometryCurve, "block danger"),
      note("Teal dashed curves and square points are reference geometry. They are saved with the session but are not exported to blockMeshDict. Drag a point to move it.", "teal"),
    );
  }

  function buildBoundaryPanel(panel) {
    const names = S.boundaries.map((b) => b.name);
    if (!names.includes(U.activeBoundary)) U.activeBoundary = names.length ? names[0] : null;
    const list = el("div", { class: "list" });
    for (const boundary of S.boundaries) {
      list.append(el("div", {
        class: "list-item" + (boundary.name === U.activeBoundary ? " selected" : ""),
        onClick: () => {
          U.activeBoundary = boundary.name;
          renderPanel();
          requestRedraw();
          setStatus("Active boundary: '" + boundary.name + "'.");
        },
      }, el("span", { class: "swatch", style: "background:" + boundary.color }),
        el("span", { text: boundary.name, style: "color:" + boundary.color + ";font-weight:600" }),
        el("span", { class: "size", text: boundary.edge_count + " edge(s)", style: "margin-left:auto;color:var(--text-faint);font-size:0.85em" })));
    }
    if (!S.boundaries.length) list.append(el("div", { class: "list-item dim", text: "No patches yet" }));
    const nameInput = textInput(U.boundaryNameDraft, (value) => { U.boundaryNameDraft = value; addBoundary(); }, "boundary.name",
      { placeholder: "patch name", style: "font-family: var(--font)" });
    nameInput.addEventListener("input", () => { U.boundaryNameDraft = nameInput.value; });
    panel.append(heading("Named patches"), list,
      el("div", { class: "with-button" }, nameInput, button("Add", () => { U.boundaryNameDraft = nameInput.value; addBoundary(); }, "small")));
    if (U.activeBoundary === null) {
      panel.append(note("The list is empty. Enter a name such as inlet, walls, or frontAndBack, then press Add."));
      return;
    }
    const boundary = S.boundaries.find((b) => b.name === U.activeBoundary);
    const neighbours = names.filter((name) => name !== boundary.name);
    let neighbourValue = boundary.neighbour_patch || (neighbours.length ? neighbours[0] : "");
    const neighbourSelect = selectInput(neighbours, neighbourValue, (value) => {
      neighbourValue = value;
      applyBoundaryDefinition("cyclic", value);
    });
    neighbourSelect.disabled = boundary.type !== "cyclic";
    panel.append(
      el("div", { class: "field" },
        el("span", { class: "swatch", style: "background:" + boundary.color + ";width:1.4rem;height:1rem" }),
        el("span", { text: boundary.edge_count + " assigned edge(s)" })),
      field("Type", selectInput(BOUNDARY_TYPES, boundary.type, (value) => applyBoundaryDefinition(value, neighbourValue))),
      field("Neighbour", neighbourSelect),
      note("Cyclic patches are paired reciprocally. OpenFOAM infers the ordinary cyclic transform from the two matching patches."),
      button("Remove boundary", removeActiveBoundary, "block danger"),
    );
  }

  /** Update panel inputs in place without rebuilding (Tk's _sync_property_values). */
  function syncPanelValues() {
    const set = (key, value) => {
      const node = els.panel.querySelector('[data-sync="' + key + '"]');
      if (!node) return;
      if (node.tagName === "INPUT") node.value = value;
      else node.textContent = value;
    };
    if (U.sel.vertex !== null && verticesById.has(U.sel.vertex)) {
      const vertex = verticesById.get(U.sel.vertex);
      set("vertex.x", fmt(vertex.x));
      set("vertex.y", fmt(vertex.y));
    }
    if (U.sel.edge !== null && edgesById.has(U.sel.edge)) {
      const edge = edgesById.get(U.sel.edge);
      set("edge.cells", String(edge.cells));
      set("edge.point_count", String(edge.control_points.length));
      set("grading.length", fmtGrading(edge.grading.length));
      for (const key of ["cell_ratio", "total_ratio", "start_width", "end_width"]) {
        set("grading." + key, fmtGrading(edge.grading[key]));
      }
      if (U.sel.cp !== null && edge.control_points[U.sel.cp]) {
        set("point.x", fmt(edge.control_points[U.sel.cp][0]));
        set("point.y", fmt(edge.control_points[U.sel.cp][1]));
      }
    }
    if (U.sel.curve !== null && curvesById.has(U.sel.curve)) {
      const curve = curvesById.get(U.sel.curve);
      set("curve.name", curve.name);
      if (U.sel.cpoint !== null && curve.points[U.sel.cpoint]) {
        set("cpoint.x", fmt(curve.points[U.sel.cpoint][0]));
        set("cpoint.y", fmt(curve.points[U.sel.cpoint][1]));
      }
    }
    syncSplitPanel();
  }

  // ------------------------------------------------------------------
  // Toolbar and menus
  // ------------------------------------------------------------------

  function shortcutLabel(action) {
    const combos = prefs().shortcuts[action] || [];
    return combos.length ? combos[0] : "";
  }

  function renderToolbar() {
    if (!S) return;
    const bar = els.toolbar;
    bar.replaceChildren();
    const selectedEdge = U.sel.edge !== null ? edgesById.get(U.sel.edge) : null;
    const modeLocksAdd = anyModeActive() || U.vertexPlacement || Boolean(U.blockSelection);
    bar.append(
      button("Undo", undo, "", !S.session.can_undo),
      button("Redo", redo, "", !S.session.can_redo),
      el("span", { class: "sep" }),
      button("Add block", addSelectedBlock, "", modeLocksAdd || !(selectedEdge && selectedEdge.exterior)),
      button(["Add vertex ", el("kbd", { text: shortcutLabel("add_vertex") })], startVertexPlacement, U.vertexPlacement ? "active" : ""),
      button(U.boundaryMode ? "Done boundaries" : "Set boundaries", toggleBoundaryMode, U.boundaryMode ? "active" : ""),
      button(U.spacingMode ? "Done linking" : "Link spacing", toggleSpacingLinkMode, U.spacingMode ? "active" : ""),
      button("Add curve", addGeometryCurve),
      button("Import curve…", importGeometryCurve),
      button("Fit view", fitView),
      el("span", { class: "spacer" }),
      button(U.exportMode ? "Close export" : ["Export ", el("kbd", { text: shortcutLabel("export_block_mesh_dict") })],
        toggleExportMode, U.exportMode ? "active primary" : "primary"),
    );
  }

  let openMenu = null;

  function closeMenus() {
    if (openMenu) {
      openMenu.classList.remove("open");
      const list = openMenu.querySelector(".menu-list");
      if (list) list.remove();
      openMenu = null;
    }
  }

  function menuDefinitions() {
    const selectedEdge = U.sel.edge !== null ? edgesById.get(U.sel.edge) : null;
    const modeLocked = anyModeActive();
    const canDelete = !modeLocked && (U.sel.curve !== null || (selectedEdge && selectedEdge.can_delete));
    const recent = prefs().recent_files;
    return [
      {
        label: "File",
        items: [
          { label: "New", accel: "new_session", run: newSession },
          { label: "Open…", accel: "open_session", run: openSession },
          {
            label: "Open Recent",
            submenu: [
              ...(recent.length
                ? recent.map((path) => ({ label: basenameWithExt(path) + " — " + dirname(path), run: () => openRecentSession(path) }))
                : [{ label: "No Recent Files", disabled: true }]),
              { separator: true },
              { label: "Clear Menu", run: clearRecentFiles, disabled: !recent.length },
            ],
          },
          { separator: true },
          { label: "Save", accel: "save_session", run: save },
          { label: "Save As…", accel: "save_session_as", run: saveAs },
          { separator: true },
          { label: "Export blockMeshDict…", accel: "export_block_mesh_dict", run: toggleExportMode },
          { separator: true },
          { label: "Exit", run: closeEditor },
        ],
      },
      {
        label: "Edit",
        items: [
          { label: "Undo", accel: "undo", run: undo, disabled: !S.session.can_undo },
          { label: "Redo", accel: "redo", run: redo, disabled: !S.session.can_redo },
          { separator: true },
          { label: "Add standalone vertex", accel: "add_vertex", run: startVertexPlacement },
          { label: "New block from 4 vertices", accel: "new_block", run: startBlockFromVertices },
          { label: "Set boundaries", accel: "set_boundaries", run: toggleBoundaryMode },
          { label: "Link edge spacing", accel: "link_spacing", run: toggleSpacingLinkMode },
          { label: "Project onto geometry", accel: "project", run: startProjection },
          { label: "Split selected edge", accel: "split_edge", run: startEdgeSplit, disabled: modeLocked || !selectedEdge },
          { label: "Combine blocks across edge", accel: "combine_blocks", run: combineSelectedBlocks, disabled: modeLocked || !(selectedEdge && selectedEdge.can_combine) },
          { separator: true },
          { label: "Delete selected entity", accel: "delete_edge", run: deleteSelectedEntity, disabled: !canDelete },
        ],
      },
      {
        label: "View",
        items: [
          { label: "Fit topology", accel: "fit_view", run: fitView },
          { separator: true },
          { label: "Block mesh", check: prefs().show_block_mesh, run: () => setVisibility({ show_block_mesh: !prefs().show_block_mesh }) },
          { label: "Geometry", accel: "toggle_geometry", check: prefs().show_geometry, run: () => setVisibility({ show_geometry: !prefs().show_geometry }) },
          { label: "Mesh preview", accel: "toggle_mesh_preview", check: prefs().show_mesh_preview, run: () => setVisibility({ show_mesh_preview: !prefs().show_mesh_preview }) },
          { separator: true },
          { label: "Vertex IDs", check: prefs().show_vertex_ids, run: () => setVisibility({ show_vertex_ids: !prefs().show_vertex_ids }) },
          { label: "Edge cell counts", check: prefs().show_edge_cell_counts, run: () => setVisibility({ show_edge_cell_counts: !prefs().show_edge_cell_counts }) },
          { label: "Mesh subdivision nodes", check: prefs().show_edge_nodes, run: () => setVisibility({ show_edge_nodes: !prefs().show_edge_nodes }) },
          { label: "Edge interpolation points", check: prefs().show_edge_interpolation_points, run: () => setVisibility({ show_edge_interpolation_points: !prefs().show_edge_interpolation_points }) },
          { separator: true },
          {
            label: "UI scale",
            submenu: [["auto", "System automatic"], ["1.0", "1.0×"], ["1.25", "1.25×"], ["1.5", "1.5×"], ["2.0", "2.0×"]]
              .map(([value, label]) => ({ label, check: prefs().ui_scale === value, run: () => applyUiScaleChoice(value) })),
          },
        ],
      },
    ];
  }

  function basenameWithExt(path) {
    return path.split(/[\\/]/).pop() || path;
  }

  function renderMenus() {
    if (!S) return;
    const wasOpen = openMenu ? openMenu.dataset.label : null;
    closeMenus();
    els.menus.replaceChildren();
    for (const definition of menuDefinitions()) {
      const menu = el("div", { class: "menu", "data-label": definition.label });
      const trigger = el("button", { type: "button", text: definition.label });
      trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        if (openMenu === menu) { closeMenus(); return; }
        closeMenus();
        openMenuFor(menu, definition.items);
      });
      trigger.addEventListener("mouseenter", () => {
        if (openMenu && openMenu !== menu) {
          closeMenus();
          openMenuFor(menu, definition.items);
        }
      });
      menu.append(trigger);
      els.menus.append(menu);
      if (wasOpen === definition.label) openMenuFor(menu, definition.items);
    }
  }

  function openMenuFor(menu, items) {
    menu.classList.add("open");
    menu.append(buildMenuList(items, false));
    openMenu = menu;
  }

  function buildMenuList(items, isSubmenu) {
    const list = el("div", { class: "menu-list" + (isSubmenu ? " submenu" : "") });
    for (const item of items) {
      if (item.separator) { list.append(el("div", { class: "menu-separator" })); continue; }
      const entry = el("button", { class: "menu-item", type: "button", disabled: Boolean(item.disabled) },
        el("span", { class: "check", text: item.check ? "✓" : "" }),
        el("span", { class: "label", text: item.label }),
        item.submenu ? el("span", { class: "arrow", text: "▸" }) : el("span", { class: "accel", text: item.accel ? shortcutLabel(item.accel) : "" }),
      );
      if (item.submenu) {
        let sub = null;
        entry.addEventListener("mouseenter", () => {
          list.querySelectorAll(".submenu").forEach((node) => node.remove());
          sub = buildMenuList(item.submenu, true);
          entry.append(sub);
        });
        entry.addEventListener("click", (event) => event.stopPropagation());
      } else {
        entry.addEventListener("click", (event) => {
          event.stopPropagation();
          closeMenus();
          if (item.run) item.run();
        });
      }
      list.append(entry);
    }
    return list;
  }

  document.addEventListener("click", closeMenus);

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------

  const MODIFIERS = { ctrl: "ctrl", control: "ctrl", shift: "shift", alt: "alt", option: "alt", cmd: "meta", command: "meta", meta: "meta" };

  function matchesCombo(event, combo) {
    const parts = combo.split("+").map((part) => part.trim()).filter(Boolean);
    const wanted = { ctrl: false, shift: false, alt: false, meta: false };
    let key = null;
    for (const part of parts) {
      const modifier = MODIFIERS[part.toLowerCase()];
      if (modifier) wanted[modifier] = true;
      else key = part;
    }
    if (key === null) return false;
    if (event.ctrlKey !== wanted.ctrl || event.altKey !== wanted.alt || event.metaKey !== wanted.meta) return false;
    const lowered = key.toLowerCase();
    const named = {
      esc: "Escape", escape: "Escape", enter: "Enter", return: "Enter", delete: "Delete", del: "Delete",
      backspace: "Backspace", space: " ", tab: "Tab", home: "Home", end: "End",
      pageup: "PageUp", pagedown: "PageDown", left: "ArrowLeft", right: "ArrowRight", up: "ArrowUp", down: "ArrowDown",
    };
    if (lowered === "numpadenter" || lowered === "kp_enter") return event.code === "NumpadEnter";
    if (lowered === "numpaddelete" || lowered === "kp_delete") return event.code === "NumpadDecimal" && event.key === "Delete";
    if (named[lowered]) {
      if (lowered === "enter" || lowered === "return") {
        return event.key === "Enter" && event.code !== "NumpadEnter" && event.shiftKey === wanted.shift;
      }
      return event.key === named[lowered] && event.shiftKey === wanted.shift;
    }
    if (key.length === 1) {
      if (event.key.length !== 1 || event.key.toLowerCase() !== lowered) return false;
      return event.shiftKey === wanted.shift;
    }
    return event.key.toLowerCase() === lowered && event.shiftKey === wanted.shift;
  }

  function isTextInput(target) {
    return target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT");
  }

  const ACTIONS = {
    new_session: () => newSession(),
    open_session: () => openSession(),
    save_session: () => save(),
    save_session_as: () => saveAs(),
    export_block_mesh_dict: () => toggleExportMode(),
    undo: () => undo(),
    redo: () => redo(),
    delete_edge: () => {
      if (U.exportMode || U.split || U.projection || U.spacingMode) return;
      deleteSelectedEntity();
    },
    split_edge: () => startEdgeSplit(),
    execute_split: () => { if (U.split) executeEdgeSplit(); },
    combine_blocks: () => combineSelectedBlocks(),
    new_block: () => startBlockFromVertices(),
    add_vertex: () => startVertexPlacement(),
    set_boundaries: () => toggleBoundaryMode(),
    link_spacing: () => toggleSpacingLinkMode(),
    project: () => startProjection(),
    toggle_geometry: () => setVisibility({ show_geometry: !prefs().show_geometry }),
    toggle_mesh_preview: () => setVisibility({ show_mesh_preview: !prefs().show_mesh_preview }),
    cancel: () => escape(),
    fit_view: () => fitView(),
  };

  // Actions that Tk binds only outside text inputs.
  const CANVAS_ONLY_ACTIONS = new Set([
    "delete_edge", "split_edge", "execute_split", "combine_blocks", "new_block", "add_vertex",
    "set_boundaries", "link_spacing", "project", "toggle_geometry", "toggle_mesh_preview",
    "export_block_mesh_dict",
  ]);

  function escape() {
    if (U.split) { cancelEdgeSplit(); return true; }
    if (U.exportMode) { toggleExportMode(); return true; }
    if (U.projection) { cancelProjection(); return true; }
    if (U.boundaryMode) { toggleBoundaryMode(); return true; }
    if (U.spacingMode) { toggleSpacingLinkMode(); return true; }
    if (U.vertexPlacement) { cancelVertexPlacement(); return true; }
    if (U.blockSelection) { cancelBlockFromVertices(); return true; }
    return false;
  }

  document.addEventListener("keydown", (event) => {
    if (!S || !els.modalRoot.hidden) return;
    const inText = isTextInput(event.target);
    for (const [action, combos] of Object.entries(prefs().shortcuts)) {
      for (const combo of combos) {
        if (!matchesCombo(event, combo)) continue;
        if (inText && CANVAS_ONLY_ACTIONS.has(action)) return;
        if (inText && action === "cancel") event.target.blur();
        const handler = ACTIONS[action];
        if (!handler) return;
        event.preventDefault();
        handler();
        return;
      }
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (S && S.session.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  // ------------------------------------------------------------------
  // Dialogs
  // ------------------------------------------------------------------

  function openModal(node) {
    els.modalRoot.replaceChildren(node);
    els.modalRoot.hidden = false;
  }

  function closeModal() {
    els.modalRoot.hidden = true;
    els.modalRoot.replaceChildren();
    canvas.focus();
  }

  function choiceDialog(title, message, choices) {
    return new Promise((resolve) => {
      const footer = el("div", { class: "modal-footer" });
      for (const [value, label, cls] of choices) {
        footer.append(button(label, () => { closeModal(); resolve(value); }, cls));
      }
      const modal = el("div", { class: "modal narrow" },
        el("div", { class: "modal-header", text: title }),
        el("div", { class: "modal-body" }, note(message)),
        footer);
      modal.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { closeModal(); resolve(null); }
      });
      openModal(modal);
      footer.querySelector("button").focus();
    });
  }

  /**
   * Browse the server's filesystem. Returns the chosen absolute path or null.
   * options: { title, mode: 'open'|'save', extensions: ['.json'], initialName, initialDir }
   */
  function fileDialog(options) {
    return new Promise((resolve) => {
      let currentDir = null;
      let entries = [];
      let selectedName = options.initialName || "";
      const extensions = options.extensions || null;
      const errorNote = el("p", { class: "error" });
      const pathInput = el("input", { class: "control", type: "text", value: "" });
      const nameInput = el("input", { class: "control", type: "text", value: selectedName, placeholder: "file name" });
      const list = el("div", { class: "file-list" });
      const finish = (value) => { closeModal(); resolve(value); };

      const confirm = () => {
        const name = nameInput.value.trim();
        if (!name) { errorNote.textContent = "Enter a file name."; return; }
        const isAbsolute = name.startsWith("/") || /^[A-Za-z]:[\\/]/.test(name);
        let path = isAbsolute ? name : joinPath(currentDir, name);
        if (options.mode === "save" && extensions && extensions.length === 1 && !path.toLowerCase().endsWith(extensions[0])) {
          if (!/\.[^./\\]+$/.test(path)) path += extensions[0];
        }
        finish(path);
      };

      async function load(dir) {
        errorNote.textContent = "";
        try {
          const data = await apiGet("/api/files?dir=" + encodeURIComponent(dir || ""));
          currentDir = data.result.dir;
          entries = data.result.entries;
          pathInput.value = currentDir;
          list.replaceChildren();
          if (data.result.parent) {
            list.append(el("div", { class: "file-entry dir", onClick: () => load(data.result.parent) },
              el("span", { class: "icon", text: "↑" }), el("span", { class: "name", text: ".." })));
          }
          for (const entry of entries) {
            const matches = entry.is_dir || !extensions || extensions.some((ext) => entry.name.toLowerCase().endsWith(ext));
            const row = el("div", { class: "file-entry" + (entry.is_dir ? " dir" : "") + (matches ? "" : " dim") },
              el("span", { class: "icon", text: entry.is_dir ? "▸" : "·" }),
              el("span", { class: "name", text: entry.name }),
              entry.is_dir ? null : el("span", { class: "size", text: formatSize(entry.size) }));
            row.addEventListener("click", () => {
              if (entry.is_dir) { load(joinPath(currentDir, entry.name)); return; }
              list.querySelectorAll(".selected").forEach((node) => node.classList.remove("selected"));
              row.classList.add("selected");
              nameInput.value = entry.name;
            });
            row.addEventListener("dblclick", () => {
              if (entry.is_dir) return;
              nameInput.value = entry.name;
              confirm();
            });
            list.append(row);
          }
        } catch (error) {
          errorNote.textContent = error.message;
        }
      }

      pathInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); load(pathInput.value); }
      });
      nameInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); confirm(); }
      });
      const modal = el("div", { class: "modal" },
        el("div", { class: "modal-header", text: options.title }),
        el("div", { class: "modal-body" },
          el("div", { class: "path-bar" }, pathInput, button("Go", () => load(pathInput.value), "small"),
            button("Home", () => load("~"), "small")),
          list,
          field("File name", nameInput),
          extensions ? note("Showing " + extensions.join(", ") + " files; other files are dimmed.") : null,
          errorNote),
        el("div", { class: "modal-footer" },
          button("Cancel", () => finish(null)),
          button(options.mode === "save" ? "Save" : "Open", confirm, "primary")));
      modal.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.stopPropagation(); finish(null); }
      });
      openModal(modal);
      load(options.initialDir || (S.session.path ? dirname(S.session.path) : ""));
      nameInput.focus();
      nameInput.select();
    });
  }

  function joinPath(dir, name) {
    if (!dir) return name;
    const separator = dir.includes("\\") && !dir.includes("/") ? "\\" : "/";
    return dir.endsWith(separator) ? dir + name : dir + separator + name;
  }

  function formatSize(size) {
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / (1024 * 1024)).toFixed(1) + " MB";
  }

  // ------------------------------------------------------------------
  // Live updates
  // ------------------------------------------------------------------

  function connectEvents() {
    const source = new EventSource("/api/events?token=" + encodeURIComponent(TOKEN) + "&version=" + (S ? S.version : -1));
    source.addEventListener("state", async (event) => {
      const payload = JSON.parse(event.data);
      if (S && payload.version === S.version) return;
      if (U.drag && U.drag.inflight) return; // our own drag; the reply carries the state
      try {
        const data = await apiGet("/api/state");
        if (!S || data.state.version !== S.version) applyState(data.state, {});
      } catch (error) { /* transient */ }
    });
    source.addEventListener("external_change", (event) => {
      const payload = JSON.parse(event.data);
      showBanner(payload.path);
    });
    source.onerror = () => {
      source.close();
      window.setTimeout(connectEvents, 2000);
    };
  }

  function showBanner(path) {
    const banner = els.banner;
    banner.replaceChildren(
      el("span", { text: basenameWithExt(path) + " changed on disk (for example, edited by an agent or another tool)." }),
      el("span", { class: "spacer" }),
      button("Reload from disk", async () => {
        try {
          const data = await apiPost("reload", {});
          applyState(data.state, { reset: true });
          hideBanner();
          setStatus("Reloaded " + S.session.name + " from disk.");
        } catch (error) {
          showError("Could not reload", error);
        }
      }, "small primary"),
      button("Keep my version", hideBanner, "small"),
    );
    banner.hidden = false;
  }

  function hideBanner() {
    els.banner.hidden = true;
    els.banner.replaceChildren();
  }

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------

  async function boot() {
    canvas.tabIndex = 0;
    fontFamily = getComputedStyle(document.documentElement).getPropertyValue("--font").trim() || "sans-serif";
    new ResizeObserver(() => resizeCanvas()).observe(els.canvasHost);
    resizeCanvas();
    try {
      const data = await apiGet("/api/state");
      applyState(data.state, { reset: true });
      applyUiScale();
      fitView();
      if (S.session.config_warning) setStatus(S.session.config_warning, true);
      else setStatus("Select a vertex to move it, or select an exterior edge to add a block.");
      connectEvents();
    } catch (error) {
      setStatus("Could not reach the BlockDrawer server: " + error.message, true);
    }
  }

  // Debug hook for automated tests and troubleshooting.
  window.__blockdrawer = {
    state: () => S,
    ui: () => U,
    toScreen: (x, y) => toScreen(x, y),
    toWorld: (x, y) => toWorld(x, y),
    hitItems: () => hitItems,
    preview: () => previewData,
    buildPreview: () => { ensurePreviewBuilt(); return previewData.stats; },
    detail: () => U.detail,
    previewLayer: () => ({ strokeMs: previewLayer.strokeMs, samples: previewLayer.samples, ppu: previewLayer.ppu, width: previewLayer.canvas ? previewLayer.canvas.width : 0 }),
    drawOnce: () => { const t = performance.now(); draw(); return performance.now() - t; },
  };

  boot();
})();
