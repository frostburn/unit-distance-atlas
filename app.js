(() => {
  "use strict";

  const elements = {
    body: document.body,
    previousButton: document.querySelector("#previousButton"),
    nextButton: document.querySelector("#nextButton"),
    nInput: document.querySelector("#nInput"),
    themeButton: document.querySelector("#themeButton"),
    themeIcon: document.querySelector("#themeIcon"),
    stage: document.querySelector("#stage"),
    artworkLayer: document.querySelector("#artworkLayer"),
    stageMessage: document.querySelector("#stageMessage"),
    zoomOutButton: document.querySelector("#zoomOutButton"),
    resetZoomButton: document.querySelector("#resetZoomButton"),
    zoomInButton: document.querySelector("#zoomInButton"),
    recordKicker: document.querySelector("#recordKicker"),
    graphTitle: document.querySelector("#graphTitle"),
    edgeCount: document.querySelector("#edgeCount"),
    averageDegree: document.querySelector("#averageDegree"),
    hostName: document.querySelector("#hostName"),
    searchRun: document.querySelector("#searchRun"),
    svgLink: document.querySelector("#svgLink"),
    metadataLink: document.querySelector("#metadataLink"),
    chartWrap: document.querySelector("#chartWrap"),
    chart: document.querySelector("#recordChart"),
    chartTooltip: document.querySelector("#chartTooltip"),
    tooltipPreview: document.querySelector("#tooltipPreview"),
    tooltipTitle: document.querySelector("#tooltipTitle"),
    tooltipSubtitle: document.querySelector("#tooltipSubtitle"),
  };

  const state = {
    records: [],
    selectedIndex: -1,
    hoverIndex: -1,
    tooltipRecordIndex: -1,
    chartMetrics: null,
    svgCache: new Map(),
    metadataCache: new Map(),
    selectionToken: 0,
    tooltipToken: 0,
    theme: "dark",
  };

  const view = {
    scale: 1,
    x: 0,
    y: 0,
    artworkBounds: null,
    pointers: new Map(),
    gesture: null,
  };

  const integerFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const decimalFormatter = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  });

  function showStageMessage(message) {
    elements.stageMessage.textContent = message;
    elements.stageMessage.hidden = false;
  }

  function hideStageMessage() {
    elements.stageMessage.hidden = true;
  }

  async function fetchText(url, cache) {
    if (!cache.has(url)) {
      const request = fetch(url, { cache: "no-store" }).then((response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}: ${url}`);
        }
        return response.text();
      });
      cache.set(url, request);
      request.catch(() => cache.delete(url));
    }
    return cache.get(url);
  }

  async function fetchMetadata(url) {
    if (!state.metadataCache.has(url)) {
      const request = fetch(url, { cache: "no-store" }).then(async (response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}: ${url}`);
        }
        return response.json();
      });
      state.metadataCache.set(url, request);
      request.catch(() => state.metadataCache.delete(url));
    }
    return state.metadataCache.get(url);
  }

  function parseSvg(text) {
    const documentNode = new DOMParser().parseFromString(text, "image/svg+xml");
    if (documentNode.querySelector("parsererror")) {
      throw new Error("Generated SVG could not be parsed");
    }
    const svg = documentNode.documentElement;
    if (svg.localName !== "svg") {
      throw new Error("Expected an SVG document");
    }
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("focusable", "false");
    return document.importNode(svg, true);
  }

  function putSvg(container, text) {
    container.replaceChildren(parseSvg(text));
    if (container === elements.artworkLayer) {
      view.artworkBounds = null;
      applyViewTransform();
    }
  }

  function nearestRecordIndex(targetN) {
    const records = state.records;
    if (!records.length) return -1;
    if (targetN <= records[0].n) return 0;
    if (targetN >= records[records.length - 1].n) return records.length - 1;

    let low = 0;
    let high = records.length - 1;
    while (low + 1 < high) {
      const middle = (low + high) >> 1;
      if (records[middle].n < targetN) low = middle;
      else high = middle;
    }
    return targetN - records[low].n <= records[high].n - targetN ? low : high;
  }

  function updateNavigation() {
    const index = state.selectedIndex;
    elements.previousButton.disabled = index <= 0;
    elements.nextButton.disabled = index < 0 || index >= state.records.length - 1;
    if (index >= 0) {
      elements.nInput.value = String(state.records[index].n);
    }
  }

  function updateCaption(record, metadata) {
    elements.recordKicker.textContent = `Record ${state.selectedIndex + 1} of ${state.records.length}`;
    elements.graphTitle.textContent = `${integerFormatter.format(record.n)} points`;
    elements.edgeCount.textContent = integerFormatter.format(metadata.edges);
    elements.averageDegree.textContent = decimalFormatter.format(metadata.averageDegree);
    elements.hostName.textContent = metadata.host?.label ?? record.host ?? "—";

    const search = metadata.search ?? {};
    const restart = search.restart ?? "?";
    const total = search.restartsInBatch ?? "?";
    const seed = search.runSeed ?? "?";
    elements.searchRun.textContent = `restart ${restart}/${total} · seed ${seed}`;

    elements.svgLink.href = record.svg;
    elements.metadataLink.href = record.metadata;
    elements.svgLink.setAttribute("download", `${String(record.n).padStart(4, "0")}.svg`);
    document.title = `${integerFormatter.format(record.n)} points · Unit-distance atlas`;
  }

  async function selectRecord(index, { updateUrl = true } = {}) {
    if (!state.records.length) return;
    index = Math.max(0, Math.min(index, state.records.length - 1));
    const record = state.records[index];
    state.selectedIndex = index;
    state.hoverIndex = -1;
    updateNavigation();
    drawChart();
    resetZoom();
    showStageMessage(`Loading ${integerFormatter.format(record.n)}-point graph…`);

    const token = ++state.selectionToken;
    try {
      const [svgText, metadata] = await Promise.all([
        fetchText(record.svg, state.svgCache),
        fetchMetadata(record.metadata),
      ]);
      if (token !== state.selectionToken) return;
      putSvg(elements.artworkLayer, svgText);
      updateCaption(record, metadata);
      hideStageMessage();
      if (updateUrl) {
        const url = new URL(window.location.href);
        url.searchParams.set("n", String(record.n));
        try {
          history.replaceState(null, "", url);
        } catch {
          // Harmless in restricted embedded previews.
        }
      }
    } catch (error) {
      if (token !== state.selectionToken) return;
      elements.artworkLayer.replaceChildren();
      showStageMessage(`Could not load this graph: ${error.message}`);
      console.error(error);
    }
  }

  function selectByN(value) {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed)) return;
    const index = nearestRecordIndex(parsed);
    if (index >= 0) selectRecord(index);
  }

  function cssVariable(name) {
    return getComputedStyle(elements.body).getPropertyValue(name).trim();
  }

  function niceStep(range, desiredTicks) {
    if (!(range > 0)) return 1;
    const rough = range / Math.max(1, desiredTicks);
    const exponent = Math.floor(Math.log10(rough));
    const magnitude = 10 ** exponent;
    const fraction = rough / magnitude;
    let niceFraction;
    if (fraction <= 1) niceFraction = 1;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 5) niceFraction = 5;
    else niceFraction = 10;
    return niceFraction * magnitude;
  }

  function drawChart() {
    const canvas = elements.chart;
    const rect = elements.chartWrap.getBoundingClientRect();
    if (!rect.width || !rect.height || !state.records.length) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const pixelWidth = Math.max(1, Math.round(rect.width * dpr));
    const pixelHeight = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }

    const context = canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);

    const compact = rect.width < 560;
    const margin = {
      left: compact ? 53 : 68,
      right: compact ? 14 : 24,
      top: 20,
      bottom: compact ? 47 : 54,
    };
    const plotWidth = Math.max(1, rect.width - margin.left - margin.right);
    const plotHeight = Math.max(1, rect.height - margin.top - margin.bottom);
    const xMin = state.records[0].n;
    const xMaxRaw = state.records[state.records.length - 1].n;
    const xMax = xMaxRaw === xMin ? xMin + 1 : xMaxRaw;
    const maximumEdgeCount = Math.max(...state.records.map((record) => record.edges), 1);
    const yStep = niceStep(maximumEdgeCount, compact ? 4 : 6);
    const yMax = Math.ceil(maximumEdgeCount / yStep) * yStep;

    const xForN = (n) => margin.left + ((n - xMin) / (xMax - xMin)) * plotWidth;
    const yForEdges = (edges) => margin.top + plotHeight - (edges / yMax) * plotHeight;

    state.chartMetrics = {
      rect,
      margin,
      plotWidth,
      plotHeight,
      xMin,
      xMax,
      yMax,
      xForN,
      yForEdges,
    };

    const grid = cssVariable("--chart-grid");
    const axis = cssVariable("--chart-axis");
    const muted = cssVariable("--muted");
    const line = cssVariable("--chart-line");
    const fill = cssVariable("--chart-fill");
    const selected = cssVariable("--chart-selected");

    context.lineWidth = 1;
    context.strokeStyle = grid;
    context.fillStyle = muted;
    context.font = `${compact ? 10 : 11}px ui-sans-serif, system-ui, sans-serif`;
    context.textBaseline = "middle";

    for (let value = 0; value <= yMax + yStep * 0.25; value += yStep) {
      const y = yForEdges(value);
      context.beginPath();
      context.moveTo(margin.left, y);
      context.lineTo(margin.left + plotWidth, y);
      context.stroke();
      context.textAlign = "right";
      context.fillText(integerFormatter.format(value), margin.left - 9, y);
    }

    const xStep = niceStep(xMax - xMin, compact ? 4 : 7);
    const firstXTick = Math.ceil(xMin / xStep) * xStep;
    for (let value = firstXTick; value <= xMax + xStep * 0.1; value += xStep) {
      const x = xForN(value);
      context.beginPath();
      context.moveTo(x, margin.top);
      context.lineTo(x, margin.top + plotHeight);
      context.stroke();
      context.textAlign = "center";
      context.textBaseline = "top";
      context.fillText(integerFormatter.format(value), x, margin.top + plotHeight + 9);
    }

    context.strokeStyle = axis;
    context.beginPath();
    context.moveTo(margin.left, margin.top);
    context.lineTo(margin.left, margin.top + plotHeight);
    context.lineTo(margin.left + plotWidth, margin.top + plotHeight);
    context.stroke();

    context.fillStyle = muted;
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.fillText("points", margin.left + plotWidth / 2, rect.height - 5);
    context.save();
    context.translate(compact ? 13 : 15, margin.top + plotHeight / 2);
    context.rotate(-Math.PI / 2);
    context.fillText("unit-distance edges", 0, 0);
    context.restore();

    const records = state.records;
    context.beginPath();
    records.forEach((record, index) => {
      const x = xForN(record.n);
      const y = yForEdges(record.edges);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.lineTo(xForN(records[records.length - 1].n), margin.top + plotHeight);
    context.lineTo(xForN(records[0].n), margin.top + plotHeight);
    context.closePath();
    context.fillStyle = fill;
    context.fill();

    context.beginPath();
    records.forEach((record, index) => {
      const x = xForN(record.n);
      const y = yForEdges(record.edges);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = line;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.stroke();

    const markerIndex = state.hoverIndex >= 0 ? state.hoverIndex : state.selectedIndex;
    if (markerIndex >= 0 && records[markerIndex]) {
      const record = records[markerIndex];
      const x = xForN(record.n);
      const y = yForEdges(record.edges);
      context.strokeStyle = state.hoverIndex >= 0 ? line : axis;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(x, margin.top);
      context.lineTo(x, margin.top + plotHeight);
      context.stroke();

      context.fillStyle = selected;
      context.beginPath();
      context.arc(x, y, state.hoverIndex >= 0 ? 5 : 4, 0, Math.PI * 2);
      context.fill();
      context.strokeStyle = line;
      context.lineWidth = 2;
      context.stroke();
    }
  }

  function chartIndexAtClientX(clientX) {
    const metrics = state.chartMetrics;
    if (!metrics) return -1;
    const rect = elements.chartWrap.getBoundingClientRect();
    const x = Math.max(
      metrics.margin.left,
      Math.min(clientX - rect.left, metrics.margin.left + metrics.plotWidth),
    );
    const fraction = (x - metrics.margin.left) / metrics.plotWidth;
    const approximateN = metrics.xMin + fraction * (metrics.xMax - metrics.xMin);
    return nearestRecordIndex(approximateN);
  }

  function positionTooltip(clientX, clientY) {
    const wrapRect = elements.chartWrap.getBoundingClientRect();
    const localX = clientX - wrapRect.left;
    const localY = clientY - wrapRect.top;
    const tooltipRect = elements.chartTooltip.getBoundingClientRect();
    const gap = 14;
    let left = localX + gap;
    if (left + tooltipRect.width > wrapRect.width - 8) {
      left = localX - tooltipRect.width - gap;
    }
    left = Math.max(8, Math.min(left, wrapRect.width - tooltipRect.width - 8));
    let top = localY - tooltipRect.height / 2;
    top = Math.max(8, Math.min(top, wrapRect.height - tooltipRect.height - 8));
    elements.chartTooltip.style.left = `${left}px`;
    elements.chartTooltip.style.top = `${top}px`;
  }

  async function showChartTooltip(index, clientX, clientY) {
    if (index < 0) return;
    const record = state.records[index];
    state.hoverIndex = index;
    drawChart();
    elements.tooltipTitle.textContent = `${integerFormatter.format(record.n)} points`;
    elements.tooltipSubtitle.textContent = `${integerFormatter.format(record.edges)} unit distances`;
    elements.chartTooltip.hidden = false;
    requestAnimationFrame(() => positionTooltip(clientX, clientY));

    if (state.tooltipRecordIndex === index) return;
    state.tooltipRecordIndex = index;
    elements.tooltipPreview.textContent = "Loading preview…";
    const token = ++state.tooltipToken;
    try {
      const svgText = await fetchText(record.svg, state.svgCache);
      if (token !== state.tooltipToken || state.hoverIndex !== index) return;
      putSvg(elements.tooltipPreview, svgText);
      requestAnimationFrame(() => positionTooltip(clientX, clientY));
    } catch (error) {
      if (token !== state.tooltipToken) return;
      elements.tooltipPreview.textContent = "Preview unavailable";
      console.error(error);
    }
  }

  function hideChartTooltip() {
    state.hoverIndex = -1;
    state.tooltipRecordIndex = -1;
    state.tooltipToken += 1;
    elements.chartTooltip.hidden = true;
    drawChart();
  }

  function clampScale(value) {
    return Math.max(0.35, Math.min(24, value));
  }

  function applyViewTransform() {
    const svg = elements.artworkLayer.firstElementChild;
    if (!svg || svg.localName !== "svg") return;

    if (!view.artworkBounds) {
      // Measure the CSS layout at its natural size. Changing the SVG's box
      // instead of transforming a fixed-size layer keeps the browser painting
      // the vectors at the current zoom rather than enlarging a cached bitmap.
      svg.style.removeProperty("top");
      svg.style.removeProperty("left");
      svg.style.removeProperty("width");
      svg.style.removeProperty("height");
      const svgRect = svg.getBoundingClientRect();
      const layerRect = elements.artworkLayer.getBoundingClientRect();
      view.artworkBounds = {
        top: svgRect.top - layerRect.top,
        left: svgRect.left - layerRect.left,
        width: svgRect.width,
        height: svgRect.height,
      };
    }

    const bounds = view.artworkBounds;
    svg.style.top = `${view.y + bounds.top * view.scale}px`;
    svg.style.left = `${view.x + bounds.left * view.scale}px`;
    svg.style.width = `${bounds.width * view.scale}px`;
    svg.style.height = `${bounds.height * view.scale}px`;
  }

  function resetZoom() {
    view.scale = 1;
    view.x = 0;
    view.y = 0;
    view.pointers.clear();
    view.gesture = null;
    applyViewTransform();
  }

  function zoomAt(clientX, clientY, factor) {
    const rect = elements.stage.getBoundingClientRect();
    const pointX = clientX - rect.left;
    const pointY = clientY - rect.top;
    const oldScale = view.scale;
    const newScale = clampScale(oldScale * factor);
    const worldX = (pointX - view.x) / oldScale;
    const worldY = (pointY - view.y) / oldScale;
    view.scale = newScale;
    view.x = pointX - worldX * newScale;
    view.y = pointY - worldY * newScale;
    applyViewTransform();
  }

  function zoomAtStageCenter(factor) {
    const rect = elements.stage.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function localPointer(event) {
    const rect = elements.stage.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function beginPanGesture(pointerId) {
    const point = view.pointers.get(pointerId);
    if (!point) return;
    view.gesture = {
      type: "pan",
      pointerId,
      startPointerX: point.x,
      startPointerY: point.y,
      startX: view.x,
      startY: view.y,
    };
  }

  function beginPinchGesture() {
    const points = [...view.pointers.values()].slice(0, 2);
    if (points.length < 2) return;
    const [a, b] = points;
    const middleX = (a.x + b.x) / 2;
    const middleY = (a.y + b.y) / 2;
    const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    view.gesture = {
      type: "pinch",
      startDistance: distance,
      startScale: view.scale,
      worldX: (middleX - view.x) / view.scale,
      worldY: (middleY - view.y) / view.scale,
    };
  }

  function setTheme(theme, persist = true) {
    state.theme = theme === "light" ? "light" : "dark";
    elements.body.dataset.theme = state.theme;
    const dark = state.theme === "dark";
    elements.themeIcon.textContent = dark ? "☀" : "☾";
    const label = dark ? "Switch to light mode" : "Switch to dark mode";
    elements.themeButton.title = label;
    elements.themeButton.setAttribute("aria-label", label);
    if (persist) {
      try {
        localStorage.setItem("unitDistanceAtlasTheme", state.theme);
      } catch {
        // Some embedded/file contexts intentionally deny local storage.
      }
    }
    requestAnimationFrame(drawChart);
  }

  async function fetchCatalog() {
    for (const url of ["data/catalog.local.json", "data/catalog.json"]) {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) return response.json();
      if (url.endsWith("catalog.json")) {
        throw new Error(`${response.status} ${response.statusText}`);
      }
    }
    throw new Error("No catalog is available");
  }

  function bindEvents() {
    elements.previousButton.addEventListener("click", () => selectRecord(state.selectedIndex - 1));
    elements.nextButton.addEventListener("click", () => selectRecord(state.selectedIndex + 1));
    elements.nInput.addEventListener("change", () => selectByN(elements.nInput.value));
    elements.nInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        selectByN(elements.nInput.value);
        elements.nInput.blur();
      }
    });

    elements.themeButton.addEventListener("click", () => {
      setTheme(state.theme === "dark" ? "light" : "dark");
    });

    elements.zoomInButton.addEventListener("click", () => zoomAtStageCenter(1.3));
    elements.zoomOutButton.addEventListener("click", () => zoomAtStageCenter(1 / 1.3));
    elements.resetZoomButton.addEventListener("click", resetZoom);
    elements.stage.addEventListener("dblclick", resetZoom);
    elements.stage.addEventListener("dragstart", (event) => event.preventDefault());

    elements.stage.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * 0.0015);
        zoomAt(event.clientX, event.clientY, factor);
      },
      { passive: false },
    );

    elements.stage.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      elements.stage.setPointerCapture(event.pointerId);
      view.pointers.set(event.pointerId, localPointer(event));
      if (view.pointers.size === 1) beginPanGesture(event.pointerId);
      else beginPinchGesture();
      event.preventDefault();
    });

    elements.stage.addEventListener("pointermove", (event) => {
      if (!view.pointers.has(event.pointerId)) return;
      view.pointers.set(event.pointerId, localPointer(event));

      if (view.pointers.size >= 2) {
        if (view.gesture?.type !== "pinch") beginPinchGesture();
        const points = [...view.pointers.values()].slice(0, 2);
        const [a, b] = points;
        const middleX = (a.x + b.x) / 2;
        const middleY = (a.y + b.y) / 2;
        const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const gesture = view.gesture;
        if (gesture?.type === "pinch") {
          const scale = clampScale(gesture.startScale * (distance / gesture.startDistance));
          view.scale = scale;
          view.x = middleX - gesture.worldX * scale;
          view.y = middleY - gesture.worldY * scale;
          applyViewTransform();
        }
      } else if (view.pointers.size === 1) {
        const [pointerId, point] = view.pointers.entries().next().value;
        if (view.gesture?.type !== "pan" || view.gesture.pointerId !== pointerId) {
          beginPanGesture(pointerId);
        }
        const gesture = view.gesture;
        if (gesture?.type === "pan") {
          view.x = gesture.startX + point.x - gesture.startPointerX;
          view.y = gesture.startY + point.y - gesture.startPointerY;
          applyViewTransform();
        }
      }
      event.preventDefault();
    });

    const endPointer = (event) => {
      if (view.pointers.has(event.pointerId)) view.pointers.delete(event.pointerId);
      if (view.pointers.size === 1) {
        const pointerId = view.pointers.keys().next().value;
        beginPanGesture(pointerId);
      } else if (view.pointers.size >= 2) {
        beginPinchGesture();
      } else {
        view.gesture = null;
      }
    };
    elements.stage.addEventListener("pointerup", endPointer);
    elements.stage.addEventListener("pointercancel", endPointer);
    elements.stage.addEventListener("lostpointercapture", endPointer);

    elements.chart.addEventListener("pointermove", (event) => {
      if (event.pointerType === "touch") return;
      const index = chartIndexAtClientX(event.clientX);
      showChartTooltip(index, event.clientX, event.clientY);
    });
    elements.chart.addEventListener("pointerleave", hideChartTooltip);
    elements.chart.addEventListener("click", (event) => {
      const index = chartIndexAtClientX(event.clientX);
      if (index >= 0) selectRecord(index);
      hideChartTooltip();
    });

    window.addEventListener("keydown", (event) => {
      const tag = event.target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "button" || tag === "a" || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        selectRecord(state.selectedIndex - 1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        selectRecord(state.selectedIndex + 1);
      } else if (event.key === "0") {
        resetZoom();
      }
    });

    const chartResizeObserver = new ResizeObserver(() => drawChart());
    chartResizeObserver.observe(elements.chartWrap);

    const stageResizeObserver = new ResizeObserver(() => {
      view.artworkBounds = null;
      applyViewTransform();
    });
    stageResizeObserver.observe(elements.stage);
  }

  async function initialise() {
    let storedTheme = null;
    try {
      storedTheme = localStorage.getItem("unitDistanceAtlasTheme");
    } catch {
      // Default to dark mode when storage is unavailable.
    }
    setTheme(storedTheme === "light" ? "light" : "dark", false);
    bindEvents();

    try {
      const catalog = await fetchCatalog();
      state.records = [...(catalog.records ?? [])].sort((a, b) => a.n - b.n);
      if (!state.records.length) {
        throw new Error("The catalog contains no graph records. Run generate.py first.");
      }

      elements.nInput.min = String(state.records[0].n);
      elements.nInput.max = String(state.records[state.records.length - 1].n);
      drawChart();

      const requestedN = Number.parseInt(new URL(window.location.href).searchParams.get("n") ?? "", 10);
      const initialIndex = Number.isFinite(requestedN)
        ? nearestRecordIndex(requestedN)
        : state.records.length - 1;
      await selectRecord(initialIndex, { updateUrl: !Number.isFinite(requestedN) });
    } catch (error) {
      const fileHint = window.location.protocol === "file:"
        ? " Browsers block local fetches from file:// URLs; run “python serve.py” and open the displayed http:// address."
        : "";
      showStageMessage(`Could not load an atlas catalog: ${error.message}.${fileHint}`);
      console.error(error);
    }
  }

  initialise();
})();
