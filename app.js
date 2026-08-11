(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const elements = {
    body: document.body,
    previousButton: $("#previousButton"),
    playButton: $("#playButton"),
    playIcon: $("#playIcon"),
    playLabel: $("#playLabel"),
    nextButton: $("#nextButton"),
    nInput: $("#nInput"),
    speedSelect: $("#speedSelect"),
    themeButton: $("#themeButton"),
    themeIcon: $("#themeIcon"),
    stage: $("#stage"),
    graphCanvas: $("#graphCanvas"),
    stageMessage: $("#stageMessage"),
    hudN: $("#hudN"),
    hudEdges: $("#hudEdges"),
    transitionBadge: $("#transitionBadge"),
    zoomOutButton: $("#zoomOutButton"),
    resetZoomButton: $("#resetZoomButton"),
    zoomInButton: $("#zoomInButton"),
    timeline: $("#timeline"),
    rangeStart: $("#rangeStart"),
    rangeEnd: $("#rangeEnd"),
    recordKicker: $("#recordKicker"),
    graphTitle: $("#graphTitle"),
    metadataLink: $("#metadataLink"),
    edgeCount: $("#edgeCount"),
    averageDegree: $("#averageDegree"),
    hostName: $("#hostName"),
    arrivalType: $("#arrivalType"),
    chartWrap: $("#chartWrap"),
    recordChart: $("#recordChart"),
    chartTooltip: $("#chartTooltip"),
    tooltipCanvas: $("#tooltipCanvas"),
    tooltipTitle: $("#tooltipTitle"),
    tooltipSubtitle: $("#tooltipSubtitle"),
  };

  const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const decimalFormat = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  });

  const MAX_RECORD_CACHE = 24;

  const state = {
    catalog: null,
    summaries: [],
    records: new Map(),
    currentIndex: -1,
    currentRecord: null,
    animation: null,
    navigationToken: 0,
    playing: false,
    playDueAt: 0,
    speed: 1,
    frameHandle: 0,
    lastFrame: 0,
    hoverIndex: -1,
    tooltipToken: 0,
    chartMetrics: null,
    prefersReducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
  };

  const view = {
    zoom: 1,
    panX: 0,
    panY: 0,
    pointers: new Map(),
    gesture: null,
  };

  function clamp(minimum, value, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function smoothstep(a, b, x) {
    if (a === b) return x >= b ? 1 : 0;
    const t = clamp(0, (x - a) / (b - a), 1);
    return t * t * (3 - 2 * t);
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function spring(t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return 1 - Math.pow(1 - t, 3) * Math.cos(3.5 * Math.PI * t);
  }

  function showMessage(message) {
    elements.stageMessage.textContent = message;
    elements.stageMessage.hidden = false;
  }

  function hideMessage() {
    elements.stageMessage.hidden = true;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
    return response.json();
  }

  function normalizeRecord(raw) {
    const coordinates = new Float64Array(raw.n * 2);
    raw.geometry.coordinates.forEach((point, index) => {
      coordinates[2 * index] = Number(point[0]);
      coordinates[2 * index + 1] = Number(point[1]);
    });
    const edges = Uint32Array.from(raw.geometry.edges);
    return {
      ...raw,
      coordinates,
      edgeIndices: edges,
      bounds: raw.geometry.bounds.map(Number),
    };
  }

  function trimRecordCache() {
    while (state.records.size > MAX_RECORD_CACHE) {
      const oldest = state.records.keys().next().value;
      state.records.delete(oldest);
    }
  }

  function loadRecord(index) {
    const summary = state.summaries[index];
    if (!summary) return Promise.reject(new Error(`No record at index ${index}`));
    const key = summary.record;
    if (state.records.has(key)) {
      const cached = state.records.get(key);
      // Map insertion order doubles as a tiny LRU, keeping long playback bounded.
      state.records.delete(key);
      state.records.set(key, cached);
      return cached;
    }
    const promise = fetchJson(key)
      .then(normalizeRecord)
      .catch((error) => {
        state.records.delete(key);
        throw error;
      });
    state.records.set(key, promise);
    trimRecordCache();
    return promise;
  }

  function cssColor(name, fallback) {
    return getComputedStyle(elements.body).getPropertyValue(name).trim() || fallback;
  }

  function visualStyle(n, averageDegree = 0) {
    // Every property is continuous in n; there are deliberately no size breakpoints.
    const progress = clamp(0, Math.log1p(Math.max(1, n)) / Math.log(2001), 1);
    const density = clamp(0, averageDegree / 18, 1);
    return {
      nodeRadius: lerp(7.4, 1.72, Math.pow(progress, 0.78)),
      outlineWidth: lerp(1.15, 0.34, Math.pow(progress, 0.72)),
      edgeWidth: lerp(2.25, 0.58, Math.pow(progress, 0.82)),
      edgeAlpha: lerp(0.58, 0.20, Math.pow(progress, 0.72)) * lerp(1, 0.82, density),
      nodeAlpha: lerp(1, 0.88, Math.pow(progress, 1.3)),
      glowBlur: lerp(18, 0.7, Math.pow(progress, 0.55)),
      glowAlpha: lerp(0.38, 0.035, Math.pow(progress, 0.62)),
      haloRadius: lerp(25, 7, Math.pow(progress, 0.74)),
    };
  }

  function fitBounds(bounds, width, height) {
    const xmin = bounds[0];
    const ymin = bounds[1];
    const xmax = bounds[2];
    const ymax = bounds[3];
    const spanX = Math.max(0, xmax - xmin);
    const spanY = Math.max(0, ymax - ymin);
    const span = Math.max(spanX, spanY, 1);
    const padding = clamp(34, Math.min(width, height) * 0.105, 110);
    const usableWidth = Math.max(1, width - 2 * padding);
    const usableHeight = Math.max(1, height - 2 * padding);
    let scale;
    if (spanX < 1e-10 && spanY < 1e-10) {
      scale = Math.min(width, height) * 0.19;
    } else {
      scale = Math.min(usableWidth / Math.max(spanX, 0.55 * span), usableHeight / Math.max(spanY, 0.55 * span));
    }
    return {
      centerX: (xmin + xmax) / 2,
      centerY: (ymin + ymax) / 2,
      scale,
    };
  }

  function interpolateBounds(a, b, t) {
    if (!a) return b.slice();
    if (!b) return a.slice();
    return a.map((value, index) => lerp(value, b[index], t));
  }

  function resizeCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2.5);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { width: rect.width, height: rect.height, dpr };
  }

  function toScreenPositions(worldPositions, camera, width, height) {
    const result = new Float64Array(worldPositions.length);
    const scale = camera.scale * view.zoom;
    const offsetX = width / 2 + view.panX - camera.centerX * scale;
    const offsetY = height / 2 + view.panY + camera.centerY * scale;
    for (let i = 0; i < worldPositions.length; i += 2) {
      result[i] = worldPositions[i] * scale + offsetX;
      result[i + 1] = -worldPositions[i + 1] * scale + offsetY;
    }
    return result;
  }

  function copyCoordinates(record) {
    return new Float64Array(record.coordinates);
  }

  function pointAt(array, index) {
    return [array[2 * index], array[2 * index + 1]];
  }

  function setPoint(array, index, x, y) {
    array[2 * index] = x;
    array[2 * index + 1] = y;
  }

  function curvePoint(ax, ay, bx, by, t, key, span) {
    const dx = bx - ax;
    const dy = by - ay;
    const length = Math.hypot(dx, dy) || 1;
    const sign = ((key * 2654435761) >>> 31) ? 1 : -1;
    const magnitude = Math.min(0.105 * span, 0.24 * length + 0.018 * span) * sign;
    const mx = (ax + bx) / 2 - (dy / length) * magnitude;
    const my = (ay + by) / 2 + (dx / length) * magnitude;
    const u = 1 - t;
    return [u * u * ax + 2 * u * t * mx + t * t * bx, u * u * ay + 2 * u * t * my + t * t * by];
  }

  function normalizedPolarOrder(record) {
    const centerX = (record.bounds[0] + record.bounds[2]) / 2;
    const centerY = (record.bounds[1] + record.bounds[3]) / 2;
    return Array.from({ length: record.n }, (_, index) => {
      const x = record.coordinates[2 * index] - centerX;
      const y = record.coordinates[2 * index + 1] - centerY;
      return { index, angle: Math.atan2(y, x), radius: Math.hypot(x, y) };
    }).sort((a, b) => a.angle - b.angle || a.radius - b.radius || a.index - b.index);
  }

  function genericMapping(source, target) {
    const sourceOrder = normalizedPolarOrder(source);
    const targetOrder = normalizedPolarOrder(target);
    const count = Math.min(source.n, target.n);
    const pairs = [];
    for (let rank = 0; rank < count; rank += 1) {
      const sourceRank = Math.floor((rank + 0.5) * sourceOrder.length / count);
      const targetRank = Math.floor((rank + 0.5) * targetOrder.length / count);
      pairs.push([sourceOrder[Math.min(sourceRank, sourceOrder.length - 1)].index, targetOrder[Math.min(targetRank, targetOrder.length - 1)].index]);
    }
    const usedSource = new Set(pairs.map((pair) => pair[0]));
    const usedTarget = new Set(pairs.map((pair) => pair[1]));
    return {
      pairs,
      extraSource: Array.from({ length: source.n }, (_, index) => index).filter((index) => !usedSource.has(index)),
      extraTarget: Array.from({ length: target.n }, (_, index) => index).filter((index) => !usedTarget.has(index)),
    };
  }

  function adjacentMapping(source, target, direction) {
    const transitionRecord = direction > 0 ? target : source;
    const transition = transitionRecord.transition;
    if (!transition || Math.abs(source.n - target.n) !== 1) return null;
    const retainedPairs = transition.retainedPairs
      ? transition.retainedPairs.map(([oldIndex, newIndex]) => [Number(oldIndex), Number(newIndex)])
      : transition.oldToNew.map((newIndex, oldIndex) => [oldIndex, Number(newIndex)]);
    const removed = (transition.removedVertices || []).map(Number);
    const added = (transition.addedVertices || [transition.addedVertex]).filter((value) => value != null).map(Number);
    // Records produced before renewal validation could attach deaths to a
    // transmutation. Ignore that partial mapping and remap every cell instead;
    // transmutation must never visually masquerade as structure-preserving
    // death and division.
    if (transition.kind === "transmutation" && removed.length) return null;
    if (direction > 0) {
      return {
        kind: transition.kind,
        transition,
        pairs: retainedPairs,
        extraSource: removed,
        extraTarget: added,
      };
    }
    return {
      kind: transition.kind,
      transition,
      pairs: retainedPairs.map(([oldIndex, newIndex]) => [newIndex, oldIndex]),
      extraSource: added,
      extraTarget: removed,
    };
  }

  function makeAnimation(source, target, sourceIndex, targetIndex) {
    const direction = Math.sign(targetIndex - sourceIndex) || 1;
    const adjacent = Math.abs(targetIndex - sourceIndex) === 1;
    const mapped = adjacent ? adjacentMapping(source, target, direction) : null;
    const mapping = mapped || { kind: "transmutation", transition: null, ...genericMapping(source, target) };
    const transition = mapping.transition;
    const baseDuration = transition?.animation?.durationMs || (mapping.kind === "growth" ? 900 : 1500);
    return {
      source,
      target,
      sourceIndex,
      targetIndex,
      direction,
      kind: mapping.kind || "transmutation",
      pairs: mapping.pairs,
      extraSource: mapping.extraSource,
      extraTarget: mapping.extraTarget,
      transition,
      startedAt: performance.now(),
      duration: state.prefersReducedMotion ? 1 : baseDuration / state.speed,
    };
  }

  function phasePositions(animation, progress) {
    const { source, target, pairs, extraSource, extraTarget, kind, transition } = animation;
    const sourcePositions = new Float64Array(source.coordinates.length);
    const targetPositions = new Float64Array(target.coordinates.length);
    const baseNodes = [];
    const specialNodes = [];
    const sourceCenter = [(source.bounds[0] + source.bounds[2]) / 2, (source.bounds[1] + source.bounds[3]) / 2];
    const targetCenter = [(target.bounds[0] + target.bounds[2]) / 2, (target.bounds[1] + target.bounds[3]) / 2];
    const span = Math.max(
      source.bounds[2] - source.bounds[0], source.bounds[3] - source.bounds[1],
      target.bounds[2] - target.bounds[0], target.bounds[3] - target.bounds[1], 1,
    );

    const animationMetadata = transition?.animation || {};
    const migrationStart = Number(animationMetadata.migrationStart ?? 0.16);
    const migrationEnd = Number(animationMetadata.migrationEnd ?? 0.82);
    const keepsStructure = kind === "growth" || kind === "renewal";
    const migrationT = keepsStructure
      ? easeInOutCubic(progress)
      : easeInOutCubic(smoothstep(migrationStart, migrationEnd, progress));

    pairs.forEach(([sourceIndex, targetIndex], pairIndex) => {
      const ax = source.coordinates[2 * sourceIndex];
      const ay = source.coordinates[2 * sourceIndex + 1];
      const bx = target.coordinates[2 * targetIndex];
      const by = target.coordinates[2 * targetIndex + 1];
      let x;
      let y;
      if (keepsStructure) {
        x = lerp(ax, bx, migrationT);
        y = lerp(ay, by, migrationT);
      } else {
        [x, y] = curvePoint(ax, ay, bx, by, migrationT, pairIndex + 1, span);
      }
      setPoint(sourcePositions, sourceIndex, x, y);
      setPoint(targetPositions, targetIndex, x, y);
      baseNodes.push({ x, y, alpha: 1, scale: 1 });
    });

    extraSource.forEach((sourceIndex, rank) => {
      const ax = source.coordinates[2 * sourceIndex];
      const ay = source.coordinates[2 * sourceIndex + 1];
      const vanishT = keepsStructure ? smoothstep(0.05, 0.86, progress) : smoothstep(0.12, 0.72, progress);
      const x = lerp(ax, sourceCenter[0], 0.18 * vanishT);
      const y = lerp(ay, sourceCenter[1], 0.18 * vanishT);
      setPoint(sourcePositions, sourceIndex, x, y);
      specialNodes.push({ x, y, alpha: 1 - vanishT, scale: 1 - 0.68 * vanishT, role: "vanish", rank });
    });

    extraTarget.forEach((targetIndex, rank) => {
      const bx = target.coordinates[2 * targetIndex];
      const by = target.coordinates[2 * targetIndex + 1];
      let sx = targetCenter[0];
      let sy = targetCenter[1];
      const spawn = transition?.spawns?.find((item) => Number(item.vertex) === targetIndex)?.position
        || (transition?.addedVertex === targetIndex ? transition.spawn : null);
      if (spawn && animation.direction > 0) {
        sx = Number(spawn[0]);
        sy = Number(spawn[1]);
      }
      const birthStart = keepsStructure ? 0.02 : 0.46;
      const birthT = smoothstep(birthStart, 0.96, progress);
      const pushed = keepsStructure ? spring(birthT) : easeInOutCubic(birthT);
      const x = lerp(sx, bx, pushed);
      const y = lerp(sy, by, pushed);
      setPoint(targetPositions, targetIndex, x, y);
      specialNodes.push({ x, y, alpha: birthT, scale: 0.18 + 0.82 * birthT, role: "birth", rank, targetIndex });
    });

    return { sourcePositions, targetPositions, baseNodes, specialNodes };
  }

  function drawEdgeSet(ctx, record, positions, alpha, style, dpr) {
    if (alpha <= 0.001 || !record.edgeIndices.length) return;
    ctx.save();
    ctx.globalAlpha = clamp(0, alpha * style.edgeAlpha, 1);
    ctx.strokeStyle = cssColor("--graph-edge", "#5ee7f4");
    ctx.lineWidth = style.edgeWidth * Math.sqrt(view.zoom) * dpr;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (let offset = 0; offset < record.edgeIndices.length; offset += 2) {
      const a = record.edgeIndices[offset];
      const b = record.edgeIndices[offset + 1];
      const ax = positions[2 * a] * dpr;
      const ay = positions[2 * a + 1] * dpr;
      const bx = positions[2 * b] * dpr;
      const by = positions[2 * b + 1] * dpr;
      if (![ax, ay, bx, by].every(Number.isFinite)) continue;
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawIncidentEdges(ctx, record, positions, vertex, alpha, style, dpr) {
    if (alpha <= 0.001 || vertex == null) return;
    ctx.save();
    ctx.globalAlpha = clamp(0, alpha * style.edgeAlpha, 1);
    ctx.strokeStyle = cssColor("--graph-edge", "#5ee7f4");
    ctx.lineWidth = style.edgeWidth * Math.sqrt(view.zoom) * dpr;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (let offset = 0; offset < record.edgeIndices.length; offset += 2) {
      const a = record.edgeIndices[offset];
      const b = record.edgeIndices[offset + 1];
      if (a !== vertex && b !== vertex) continue;
      const ax = positions[2 * a] * dpr;
      const ay = positions[2 * a + 1] * dpr;
      const bx = positions[2 * b] * dpr;
      const by = positions[2 * b + 1] * dpr;
      if (![ax, ay, bx, by].every(Number.isFinite)) continue;
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawNodes(ctx, nodes, style, dpr, alpha = 1) {
    if (!nodes.length || alpha <= 0.001) return;
    const radius = style.nodeRadius * Math.sqrt(view.zoom) * dpr;
    ctx.save();
    ctx.globalAlpha = alpha * style.nodeAlpha;
    ctx.fillStyle = cssColor("--graph-node", "#f8fbff");
    ctx.strokeStyle = cssColor("--graph-outline", "#06101c");
    ctx.lineWidth = style.outlineWidth * dpr;
    ctx.shadowColor = cssColor("--graph-glow", "#22d3ee");
    ctx.shadowBlur = style.glowBlur * dpr;
    ctx.beginPath();
    nodes.forEach((node) => {
      const r = radius * (node.scale ?? 1);
      ctx.moveTo(node.x * dpr + r, node.y * dpr);
      ctx.arc(node.x * dpr, node.y * dpr, r, 0, Math.PI * 2);
    });
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.stroke();
    ctx.restore();
  }

  function drawSpecialNodes(ctx, nodes, style, dpr, progress) {
    nodes.forEach((node) => {
      const radius = style.nodeRadius * Math.sqrt(view.zoom) * dpr * node.scale;
      if (node.alpha <= 0.001 || radius <= 0.05) return;
      ctx.save();
      ctx.globalAlpha = node.alpha * style.nodeAlpha;
      ctx.fillStyle = cssColor("--graph-node", "#f8fbff");
      ctx.strokeStyle = cssColor("--graph-edge", "#5ee7f4");
      ctx.lineWidth = style.outlineWidth * dpr;
      ctx.shadowColor = cssColor("--graph-glow", "#22d3ee");
      ctx.shadowBlur = style.glowBlur * 1.35 * dpr;
      ctx.beginPath();
      ctx.arc(node.x * dpr, node.y * dpr, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.stroke();
      if (node.role === "birth") {
        const wave = Math.sin(Math.PI * clamp(0, progress, 1));
        ctx.globalAlpha = node.alpha * wave * style.glowAlpha;
        ctx.lineWidth = Math.max(1, 1.1 * dpr);
        ctx.beginPath();
        ctx.arc(node.x * dpr, node.y * dpr, (radius + style.haloRadius * wave * dpr), 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    });
  }

  function nodesFromPositions(positions) {
    const result = [];
    for (let i = 0; i < positions.length; i += 2) result.push({ x: positions[i], y: positions[i + 1], alpha: 1, scale: 1 });
    return result;
  }

  function renderStatic(record, ctx, metrics) {
    const camera = fitBounds(record.bounds, metrics.width, metrics.height);
    const screen = toScreenPositions(record.coordinates, camera, metrics.width, metrics.height);
    const style = visualStyle(record.n, record.averageDegree);
    drawEdgeSet(ctx, record, screen, 1, style, metrics.dpr);
    drawNodes(ctx, nodesFromPositions(screen), style, metrics.dpr);
  }

  function renderAnimation(animation, progress, ctx, metrics) {
    const positions = phasePositions(animation, progress);
    const cameraProgress = easeInOutCubic(progress);
    const worldBounds = interpolateBounds(animation.source.bounds, animation.target.bounds, cameraProgress);
    const camera = fitBounds(worldBounds, metrics.width, metrics.height);
    const sourceScreen = toScreenPositions(positions.sourcePositions, camera, metrics.width, metrics.height);
    const targetScreen = toScreenPositions(positions.targetPositions, camera, metrics.width, metrics.height);
    const baseWorld = new Float64Array(positions.baseNodes.length * 2);
    positions.baseNodes.forEach((node, index) => setPoint(baseWorld, index, node.x, node.y));
    const baseScreen = toScreenPositions(baseWorld, camera, metrics.width, metrics.height);
    const baseNodes = positions.baseNodes.map((node, index) => ({ ...node, x: baseScreen[2 * index], y: baseScreen[2 * index + 1] }));
    const specialWorld = new Float64Array(positions.specialNodes.length * 2);
    positions.specialNodes.forEach((node, index) => setPoint(specialWorld, index, node.x, node.y));
    const specialScreen = toScreenPositions(specialWorld, camera, metrics.width, metrics.height);
    const specialNodes = positions.specialNodes.map((node, index) => ({ ...node, x: specialScreen[2 * index], y: specialScreen[2 * index + 1] }));
    const nInterpolated = lerp(animation.source.n, animation.target.n, cameraProgress);
    const degreeInterpolated = lerp(animation.source.averageDegree, animation.target.averageDegree, cameraProgress);
    const style = visualStyle(nInterpolated, degreeInterpolated);

    if (animation.kind === "growth") {
      if (animation.direction > 0) {
        drawEdgeSet(ctx, animation.source, sourceScreen, 1, style, metrics.dpr);
        animation.extraTarget.forEach((added) => {
          drawIncidentEdges(ctx, animation.target, targetScreen, added, smoothstep(0.18, 0.92, progress), style, metrics.dpr);
        });
      } else {
        drawEdgeSet(ctx, animation.target, targetScreen, 1, style, metrics.dpr);
        animation.extraSource.forEach((disappearing) => {
          drawIncidentEdges(ctx, animation.source, sourceScreen, disappearing, 1 - smoothstep(0.08, 0.82, progress), style, metrics.dpr);
        });
      }
    } else if (animation.kind === "renewal") {
      // Crossfade coincident retained edges without ever blanking the graph;
      // only edges incident to dying/born cells visibly disappear or grow.
      const blend = easeInOutCubic(progress);
      drawEdgeSet(ctx, animation.source, sourceScreen, 1 - blend, style, metrics.dpr);
      drawEdgeSet(ctx, animation.target, targetScreen, blend, style, metrics.dpr);
    } else {
      const metadata = animation.transition?.animation || {};
      const fadeOutEnd = Number(metadata.edgeFadeOutEnd ?? 0.20);
      const fadeInStart = Number(metadata.edgeFadeInStart ?? 0.78);
      drawEdgeSet(ctx, animation.source, sourceScreen, 1 - smoothstep(0, fadeOutEnd, progress), style, metrics.dpr);
      drawEdgeSet(ctx, animation.target, targetScreen, smoothstep(fadeInStart, 1, progress), style, metrics.dpr);
    }

    drawNodes(ctx, baseNodes, style, metrics.dpr);
    drawSpecialNodes(ctx, specialNodes, style, metrics.dpr, progress);

    const spawnNeighbors = animation.transition?.spawns
      ? animation.transition.spawns.flatMap((spawn) => spawn.neighbors || [])
      : animation.transition?.spawnNeighbors || [];
    if ((animation.kind === "growth" || animation.kind === "renewal") && spawnNeighbors.length) {
      // Spawn neighbours are indices in the larger record in both directions.
      const neighborIndices = [...new Set(spawnNeighbors)];
      const sourceArray = animation.direction > 0 ? targetScreen : sourceScreen;
      const pulse = Math.sin(Math.PI * smoothstep(0, 0.72, progress));
      if (pulse > 0.01) {
        ctx.save();
        ctx.globalAlpha = pulse * style.glowAlpha * 0.62;
        ctx.strokeStyle = cssColor("--graph-edge", "#5ee7f4");
        ctx.lineWidth = Math.max(1, 0.9 * metrics.dpr);
        neighborIndices.forEach((index) => {
          const x = sourceArray[2 * index] * metrics.dpr;
          const y = sourceArray[2 * index + 1] * metrics.dpr;
          if (!Number.isFinite(x + y)) return;
          ctx.beginPath();
          ctx.arc(x, y, (style.nodeRadius + style.haloRadius * 0.45 * pulse) * metrics.dpr, 0, Math.PI * 2);
          ctx.stroke();
        });
        ctx.restore();
      }
    }
  }

  function drawStage(now = performance.now()) {
    state.frameHandle = 0;
    const metrics = resizeCanvas(elements.graphCanvas);
    const ctx = elements.graphCanvas.getContext("2d", { alpha: true });
    ctx.clearRect(0, 0, elements.graphCanvas.width, elements.graphCanvas.height);

    if (state.animation) {
      const elapsed = now - state.animation.startedAt;
      const progress = clamp(0, elapsed / state.animation.duration, 1);
      renderAnimation(state.animation, progress, ctx, metrics);
      if (state.animation.kind === "growth") {
        elements.transitionBadge.textContent = "cell division";
      } else if (state.animation.kind === "renewal") {
        const deaths = state.animation.transition.removedVertices.length;
        const divisions = state.animation.transition.addedVertices.length;
        elements.transitionBadge.textContent = `${deaths} death${deaths === 1 ? "" : "s"} · ${divisions} divisions`;
      } else {
        const deaths = state.animation.transition?.removedVertices?.length || 0;
        const divisions = state.animation.transition?.addedVertices?.length || 0;
        elements.transitionBadge.textContent = deaths
          ? `transmutation · ${deaths} death${deaths === 1 ? "" : "s"} · ${divisions} divisions`
          : "transmutation";
      }
      if (progress >= 1) finishAnimation(now);
    } else if (state.currentRecord) {
      renderStatic(state.currentRecord, ctx, metrics);
      elements.transitionBadge.textContent = "still";
      if (state.playing && now >= state.playDueAt) step(1);
    }

    if (state.animation || state.playing) requestStageFrame();
  }

  function requestStageFrame() {
    if (!state.frameHandle) state.frameHandle = requestAnimationFrame(drawStage);
  }

  function updateControls() {
    const index = state.animation ? state.animation.targetIndex : state.currentIndex;
    const summary = state.summaries[index];
    elements.previousButton.disabled = index <= 0;
    elements.nextButton.disabled = index < 0 || index >= state.summaries.length - 1;
    elements.nInput.value = summary ? summary.n : "";
    elements.timeline.value = summary ? summary.n : 1;
    elements.playIcon.textContent = state.playing ? "❚❚" : "▶";
    elements.playLabel.textContent = state.playing ? "Pause" : "Play";
    elements.playButton.setAttribute("aria-pressed", String(state.playing));
  }

  function updateRecordCopy(index, record, arrivalOverride = null) {
    const summary = state.summaries[index];
    const optimality = record.optimality?.status || summary.optimality || "strict atlas record";
    elements.recordKicker.textContent = optimality;
    elements.graphTitle.textContent = record.legend;
    elements.edgeCount.textContent = numberFormat.format(record.edges);
    elements.averageDegree.textContent = decimalFormat.format(record.averageDegree);
    elements.hostName.textContent = record.host?.label || summary.host || "—";
    const arrival = arrivalOverride || record.transition?.kind || (record.n === 1 ? "origin" : "loaded");
    elements.arrivalType.textContent = arrival === "growth"
      ? "Cell division"
      : arrival === "renewal"
        ? "Cell renewal"
        : arrival === "transmutation" ? "Transmutation" : arrival;
    elements.metadataLink.href = summary.record;
    elements.hudN.textContent = `n = ${numberFormat.format(record.n)}`;
    elements.hudEdges.textContent = `${numberFormat.format(record.edges)} edges`;
    document.title = `${record.n} points · ${record.edges} edges — Unit-distance motion atlas`;
  }

  function finishAnimation(now) {
    const animation = state.animation;
    if (!animation) return;
    state.currentIndex = animation.targetIndex;
    state.currentRecord = animation.target;
    state.animation = null;
    state.playDueAt = now + 230 / state.speed;
    updateRecordCopy(state.currentIndex, state.currentRecord);
    updateControls();
    drawChart();
    prefetchAround(state.currentIndex);
  }

  async function navigateTo(index, { animate = true } = {}) {
    index = clamp(0, Math.round(index), state.summaries.length - 1);
    if (index === state.currentIndex && !state.animation) return;
    const token = ++state.navigationToken;
    try {
      const target = await loadRecord(index);
      if (token !== state.navigationToken) return;
      hideMessage();
      if (!state.currentRecord || !animate || state.prefersReducedMotion) {
        state.animation = null;
        state.currentIndex = index;
        state.currentRecord = target;
        state.playDueAt = performance.now() + 230 / state.speed;
        updateRecordCopy(index, target);
      } else {
        const source = state.animation ? state.animation.target : state.currentRecord;
        const sourceIndex = state.animation ? state.animation.targetIndex : state.currentIndex;
        state.animation = makeAnimation(source, target, sourceIndex, index);
        updateRecordCopy(index, target, state.animation.kind);
      }
      updateControls();
      drawChart();
      requestStageFrame();
      prefetchAround(index);
    } catch (error) {
      console.error(error);
      showMessage(`Could not load record ${state.summaries[index]?.n ?? index + 1}: ${error.message}`);
      state.playing = false;
      updateControls();
    }
  }

  function step(direction) {
    const base = state.animation ? state.animation.targetIndex : state.currentIndex;
    const target = clamp(0, base + direction, state.summaries.length - 1);
    if (target === base) {
      state.playing = false;
      updateControls();
      return;
    }
    navigateTo(target);
  }

  function togglePlay() {
    state.playing = !state.playing;
    if (state.playing && state.currentIndex >= state.summaries.length - 1 && !state.animation) {
      navigateTo(0, { animate: false });
    }
    state.playDueAt = performance.now();
    updateControls();
    requestStageFrame();
  }

  function prefetchAround(index) {
    [index - 2, index - 1, index + 1, index + 2].forEach((candidate) => {
      if (candidate >= 0 && candidate < state.summaries.length) loadRecord(candidate).catch(() => {});
    });
  }

  function resetView() {
    view.zoom = 1;
    view.panX = 0;
    view.panY = 0;
    requestStageFrame();
  }

  function zoomAt(factor, clientX, clientY) {
    const rect = elements.graphCanvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const oldZoom = view.zoom;
    const nextZoom = clamp(0.22, oldZoom * factor, 18);
    const ratio = nextZoom / oldZoom;
    view.panX = px - centerX - (px - centerX - view.panX) * ratio;
    view.panY = py - centerY - (py - centerY - view.panY) * ratio;
    view.zoom = nextZoom;
    requestStageFrame();
  }

  function beginPointer(event) {
    elements.stage.setPointerCapture(event.pointerId);
    view.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (view.pointers.size === 1) {
      view.gesture = { type: "pan", x: event.clientX, y: event.clientY, panX: view.panX, panY: view.panY };
    } else if (view.pointers.size === 2) {
      const points = [...view.pointers.values()];
      const dx = points[1].x - points[0].x;
      const dy = points[1].y - points[0].y;
      view.gesture = {
        type: "pinch",
        distance: Math.hypot(dx, dy),
        midpointX: (points[0].x + points[1].x) / 2,
        midpointY: (points[0].y + points[1].y) / 2,
        zoom: view.zoom,
        panX: view.panX,
        panY: view.panY,
      };
    }
  }

  function movePointer(event) {
    if (!view.pointers.has(event.pointerId)) return;
    view.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (view.pointers.size === 1 && view.gesture?.type === "pan") {
      view.panX = view.gesture.panX + event.clientX - view.gesture.x;
      view.panY = view.gesture.panY + event.clientY - view.gesture.y;
      requestStageFrame();
    } else if (view.pointers.size === 2) {
      const points = [...view.pointers.values()];
      const dx = points[1].x - points[0].x;
      const dy = points[1].y - points[0].y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const midpointX = (points[0].x + points[1].x) / 2;
      const midpointY = (points[0].y + points[1].y) / 2;
      if (view.gesture?.type !== "pinch") {
        view.gesture = { type: "pinch", distance, midpointX, midpointY, zoom: view.zoom, panX: view.panX, panY: view.panY };
      }
      const rect = elements.graphCanvas.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const nextZoom = clamp(0.22, view.gesture.zoom * distance / Math.max(1, view.gesture.distance), 18);
      const ratio = nextZoom / view.gesture.zoom;
      view.panX = midpointX - centerX - (view.gesture.midpointX - centerX - view.gesture.panX) * ratio;
      view.panY = midpointY - centerY - (view.gesture.midpointY - centerY - view.gesture.panY) * ratio;
      view.zoom = nextZoom;
      requestStageFrame();
    }
  }

  function endPointer(event) {
    view.pointers.delete(event.pointerId);
    if (view.pointers.size === 1) {
      const point = [...view.pointers.values()][0];
      view.gesture = { type: "pan", x: point.x, y: point.y, panX: view.panX, panY: view.panY };
    } else if (view.pointers.size === 0) {
      view.gesture = null;
    }
  }

  function chartTheme() {
    return {
      grid: cssColor("--chart-grid", "rgba(148,163,184,.13)"),
      axis: cssColor("--chart-axis", "rgba(148,163,184,.55)"),
      line: cssColor("--chart-line", "#67e8f9"),
      fill: cssColor("--chart-fill", "rgba(34,211,238,.075)"),
      marker: cssColor("--chart-marker", "#f8fafc"),
      muted: cssColor("--muted", "#92a2b9"),
    };
  }

  function niceMaximum(value) {
    if (value <= 0) return 1;
    const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
    const normalized = value / magnitude;
    const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return nice * magnitude;
  }

  function drawChart() {
    if (!state.summaries.length) return;
    const metrics = resizeCanvas(elements.recordChart);
    const ctx = elements.recordChart.getContext("2d", { alpha: true });
    ctx.clearRect(0, 0, elements.recordChart.width, elements.recordChart.height);
    const theme = chartTheme();
    const dpr = metrics.dpr;
    const margins = { left: 54, right: 18, top: 18, bottom: 35 };
    const plot = {
      x: margins.left,
      y: margins.top,
      width: Math.max(1, metrics.width - margins.left - margins.right),
      height: Math.max(1, metrics.height - margins.top - margins.bottom),
    };
    const nMin = state.summaries[0].n;
    const nMax = state.summaries[state.summaries.length - 1].n;
    const edgeMax = niceMaximum(Math.max(...state.summaries.map((record) => record.edges)) * 1.03);
    const xFor = (n) => plot.x + (n - nMin) / Math.max(1, nMax - nMin) * plot.width;
    const yFor = (edges) => plot.y + plot.height - edges / edgeMax * plot.height;
    state.chartMetrics = { ...plot, nMin, nMax, edgeMax, xFor, yFor, width: metrics.width, height: metrics.height };

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillStyle = theme.muted;
    ctx.strokeStyle = theme.grid;
    ctx.lineWidth = 1;
    const gridLines = 5;
    for (let i = 0; i <= gridLines; i += 1) {
      const fraction = i / gridLines;
      const y = plot.y + plot.height * fraction;
      ctx.beginPath();
      ctx.moveTo(plot.x, y);
      ctx.lineTo(plot.x + plot.width, y);
      ctx.stroke();
      const value = edgeMax * (1 - fraction);
      ctx.textAlign = "right";
      ctx.fillText(numberFormat.format(value), plot.x - 9, y);
    }
    for (let i = 0; i <= 4; i += 1) {
      const fraction = i / 4;
      const n = Math.round(lerp(nMin, nMax, fraction));
      const x = xFor(n);
      ctx.strokeStyle = theme.grid;
      ctx.beginPath();
      ctx.moveTo(x, plot.y);
      ctx.lineTo(x, plot.y + plot.height);
      ctx.stroke();
      ctx.textAlign = "center";
      ctx.fillStyle = theme.muted;
      ctx.fillText(numberFormat.format(n), x, plot.y + plot.height + 19);
    }

    ctx.beginPath();
    state.summaries.forEach((record, index) => {
      const x = xFor(record.n);
      const y = yFor(record.edges);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(xFor(nMax), plot.y + plot.height);
    ctx.lineTo(xFor(nMin), plot.y + plot.height);
    ctx.closePath();
    ctx.fillStyle = theme.fill;
    ctx.fill();

    ctx.beginPath();
    state.summaries.forEach((record, index) => {
      const x = xFor(record.n);
      const y = yFor(record.edges);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = theme.line;
    ctx.lineWidth = 1.7;
    ctx.lineJoin = "round";
    ctx.stroke();

    const selectedIndex = state.animation ? state.animation.targetIndex : state.currentIndex;
    if (selectedIndex >= 0) {
      const selected = state.summaries[selectedIndex];
      const x = xFor(selected.n);
      const y = yFor(selected.edges);
      ctx.strokeStyle = theme.line;
      ctx.globalAlpha = 0.2;
      ctx.beginPath();
      ctx.moveTo(x, plot.y);
      ctx.lineTo(x, plot.y + plot.height);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = theme.marker;
      ctx.strokeStyle = theme.line;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }

  function chartIndexFromEvent(event) {
    if (!state.chartMetrics) return -1;
    const rect = elements.recordChart.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const fraction = clamp(0, (x - state.chartMetrics.x) / state.chartMetrics.width, 1);
    const n = Math.round(lerp(state.chartMetrics.nMin, state.chartMetrics.nMax, fraction));
    return clamp(0, n - state.summaries[0].n, state.summaries.length - 1);
  }

  function drawMiniature(record) {
    const metrics = resizeCanvas(elements.tooltipCanvas);
    const ctx = elements.tooltipCanvas.getContext("2d", { alpha: true });
    ctx.clearRect(0, 0, elements.tooltipCanvas.width, elements.tooltipCanvas.height);
    const camera = fitBounds(record.bounds, metrics.width, metrics.height);
    const oldView = { zoom: view.zoom, panX: view.panX, panY: view.panY };
    view.zoom = 1;
    view.panX = 0;
    view.panY = 0;
    const screen = toScreenPositions(record.coordinates, camera, metrics.width, metrics.height);
    const style = visualStyle(record.n, record.averageDegree);
    style.nodeRadius = clamp(1.1, style.nodeRadius * 0.58, 3.8);
    style.edgeWidth = clamp(0.42, style.edgeWidth * 0.55, 1.15);
    style.glowBlur *= 0.25;
    drawEdgeSet(ctx, record, screen, 1, style, metrics.dpr);
    drawNodes(ctx, nodesFromPositions(screen), style, metrics.dpr);
    view.zoom = oldView.zoom;
    view.panX = oldView.panX;
    view.panY = oldView.panY;
  }

  async function showChartTooltip(event) {
    const index = chartIndexFromEvent(event);
    if (index < 0) return;
    state.hoverIndex = index;
    const summary = state.summaries[index];
    const rect = elements.chartWrap.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    elements.chartTooltip.hidden = false;
    elements.chartTooltip.style.left = `${clamp(0, x, rect.width - 250)}px`;
    elements.chartTooltip.style.top = `${clamp(82, y, rect.height - 82)}px`;
    elements.tooltipTitle.textContent = `${numberFormat.format(summary.n)} points · ${numberFormat.format(summary.edges)} edges`;
    elements.tooltipSubtitle.textContent = `${summary.host} · ${summary.transition || "origin"}`;
    const token = ++state.tooltipToken;
    try {
      const record = await loadRecord(index);
      if (token !== state.tooltipToken || state.hoverIndex !== index) return;
      drawMiniature(record);
    } catch (error) {
      console.warn(error);
    }
  }

  function hideChartTooltip() {
    state.hoverIndex = -1;
    state.tooltipToken += 1;
    elements.chartTooltip.hidden = true;
  }

  function applyTheme(theme) {
    const selected = theme === "light" ? "light" : "dark";
    elements.body.dataset.theme = selected;
    elements.themeIcon.textContent = selected === "dark" ? "☀" : "☾";
    elements.themeButton.title = selected === "dark" ? "Switch to light mode" : "Switch to dark mode";
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = selected === "dark" ? "#060910" : "#edf3f8";
    localStorage.setItem("unit-distance-theme", selected);
    drawChart();
    requestStageFrame();
    if (!elements.chartTooltip.hidden && state.hoverIndex >= 0) loadRecord(state.hoverIndex).then(drawMiniature).catch(() => {});
  }

  function installEvents() {
    elements.previousButton.addEventListener("click", () => step(-1));
    elements.nextButton.addEventListener("click", () => step(1));
    elements.playButton.addEventListener("click", togglePlay);
    elements.nInput.addEventListener("change", () => {
      const n = clamp(state.summaries[0].n, Number(elements.nInput.value) || 1, state.summaries.at(-1).n);
      navigateTo(n - state.summaries[0].n);
    });
    elements.timeline.addEventListener("input", () => navigateTo(Number(elements.timeline.value) - state.summaries[0].n));
    elements.speedSelect.addEventListener("change", () => {
      state.speed = Number(elements.speedSelect.value) || 1;
      if (state.animation) {
        const progress = clamp(0, (performance.now() - state.animation.startedAt) / state.animation.duration, 1);
        const baseDuration = state.animation.transition?.animation?.durationMs || (state.animation.kind === "growth" ? 900 : 1500);
        state.animation.duration = state.prefersReducedMotion ? 1 : baseDuration / state.speed;
        state.animation.startedAt = performance.now() - progress * state.animation.duration;
      }
    });
    elements.themeButton.addEventListener("click", () => applyTheme(elements.body.dataset.theme === "dark" ? "light" : "dark"));
    elements.zoomInButton.addEventListener("click", () => {
      const rect = elements.graphCanvas.getBoundingClientRect();
      zoomAt(1.25, rect.left + rect.width / 2, rect.top + rect.height / 2);
    });
    elements.zoomOutButton.addEventListener("click", () => {
      const rect = elements.graphCanvas.getBoundingClientRect();
      zoomAt(0.8, rect.left + rect.width / 2, rect.top + rect.height / 2);
    });
    elements.resetZoomButton.addEventListener("click", resetView);
    elements.stage.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomAt(Math.exp(-event.deltaY * 0.0012), event.clientX, event.clientY);
    }, { passive: false });
    elements.stage.addEventListener("pointerdown", beginPointer);
    elements.stage.addEventListener("pointermove", movePointer);
    elements.stage.addEventListener("pointerup", endPointer);
    elements.stage.addEventListener("pointercancel", endPointer);
    elements.recordChart.addEventListener("pointermove", showChartTooltip);
    elements.recordChart.addEventListener("pointerleave", hideChartTooltip);
    elements.recordChart.addEventListener("click", (event) => {
      const index = chartIndexFromEvent(event);
      if (index >= 0) navigateTo(index);
    });
    window.addEventListener("resize", () => {
      drawChart();
      requestStageFrame();
    });
    window.addEventListener("keydown", (event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
      if (event.code === "Space") {
        event.preventDefault();
        togglePlay();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        step(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        step(1);
      } else if (event.key === "0") {
        resetView();
      }
    });
  }

  async function loadCatalog() {
    try {
      return await fetchJson("data/catalog.local.json");
    } catch (localError) {
      console.info("Local catalog unavailable; loading published catalog.", localError);
      return fetchJson("data/catalog.json");
    }
  }

  async function init() {
    installEvents();
    applyTheme(localStorage.getItem("unit-distance-theme") || "dark");
    try {
      state.catalog = await loadCatalog();
      if (state.catalog.schemaVersion !== 2) throw new Error(`Unsupported schema version ${state.catalog.schemaVersion}`);
      state.summaries = state.catalog.records;
      if (!state.summaries.length) throw new Error("The catalog has no records.");
      elements.nInput.min = state.summaries[0].n;
      elements.nInput.max = state.summaries.at(-1).n;
      elements.timeline.min = state.summaries[0].n;
      elements.timeline.max = state.summaries.at(-1).n;
      elements.rangeStart.textContent = numberFormat.format(state.summaries[0].n);
      elements.rangeEnd.textContent = numberFormat.format(state.summaries.at(-1).n);
      await navigateTo(0, { animate: false });
      drawChart();
    } catch (error) {
      console.error(error);
      showMessage(`Could not load the atlas. Run the generator and serve this directory over HTTP. ${error.message}`);
    }
  }

  init();
})();
