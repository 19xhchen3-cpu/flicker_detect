(() => {
  "use strict";

  const HISTORY_COLORS = [
    "#2563eb",
    "#16a34a",
    "#d97706",
    "#dc2626",
    "#7c3aed",
    "#0891b2",
    "#db2777",
    "#65a30d",
    "#475569",
    "#b45309",
  ];

  const fileInput = document.getElementById("videoInput");
  const loadButton = document.getElementById("loadButton");
  const startButton = document.getElementById("startButton");
  const recomputeButton = document.getElementById("recomputeButton");
  const navDetect = document.getElementById("navDetect");
  const navSimulate = document.getElementById("navSimulate");
  const detectView = document.getElementById("detectView");
  const simulateView = document.getElementById("simulateView");
  const fileName = document.getElementById("fileName");
  const gammaInput = document.getElementById("gammaInput");
  const colsInput = document.getElementById("colsInput");
  const rowsInput = document.getElementById("rowsInput");
  const fpsInput = document.getElementById("fpsInput");
  const detectionFrequencyInput = document.getElementById("detectionFrequencyInput");
  const sourceVideo = document.getElementById("sourceVideo");
  const frameScrubber = document.getElementById("frameScrubber");
  const scrubberLabel = document.getElementById("scrubberLabel");
  const blockOpacityInput = document.getElementById("blockOpacityInput");
  const blockOpacityLabel = document.getElementById("blockOpacityLabel");
  const detectOpacityInput = document.getElementById("detectOpacityInput");
  const detectOpacityLabel = document.getElementById("detectOpacityLabel");
  const showDetectedInput = document.getElementById("showDetectedInput");
  const progressFill = document.getElementById("progressFill");
  const frameStatus = document.getElementById("frameStatus");
  const stats = document.getElementById("stats");
  const previewCanvas = document.getElementById("previewCanvas");
  const chartCanvas = document.getElementById("chartCanvas");
  const chartSummary = document.getElementById("chartSummary");
  const blockTooltip = document.getElementById("blockTooltip");
  const blockSelectGrid = document.getElementById("blockSelectGrid");
  const clearHistoryBlocks = document.getElementById("clearHistoryBlocks");
  const historyLegend = document.getElementById("historyLegend");
  const historyCanvas = document.getElementById("historyCanvas");
  const historySummary = document.getElementById("historySummary");

  let selectedFile = null;
  let metadata = null;
  let converting = false;
  let frameData = [];
  let detectionResults = [];
  let currentDetect = null;
  let sourceObjectUrl = null;
  let currentFrameId = null;
  let detectionVersion = 0;
  let frameDetailVersion = 0;
  let detailCache = new Map();
  let lastMouseEvent = null;
  let preprocessDirty = false;
  let selectedHistoryBlocks = new Set();
  let detectorOptions = [];
  let currentSessionId = null;

  loadButton.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    selectedFile = fileInput.files[0] || null;
    metadata = null;
    frameData = [];
    detectionResults = [];
    currentDetect = null;
    currentFrameId = null;
    currentSessionId = null;
    detailCache.clear();
    frameDetailVersion += 1;
    preprocessDirty = false;
    if (sourceObjectUrl) {
      URL.revokeObjectURL(sourceObjectUrl);
      sourceObjectUrl = null;
    }
    if (selectedFile) {
      fileName.textContent = selectedFile.name;
      startButton.disabled = false;
      sourceObjectUrl = URL.createObjectURL(selectedFile);
      sourceVideo.src = sourceObjectUrl;
      sourceVideo.load();
      setStatus(`已选择：${selectedFile.name}`, "");
    } else {
      fileName.textContent = "未选择视频";
      startButton.disabled = true;
      sourceVideo.removeAttribute("src");
      sourceVideo.load();
      setStatus("等待加载视频", "");
    }
    resetProgress();
    resetScrubber();
    recomputeButton.disabled = true;
    drawChart();
    resetHistoryPanel();
  });

  startButton.addEventListener("click", startConversion);

  recomputeButton.addEventListener("click", () => {
    if (!metadata || frameData.length === 0) return;
    setStatus("手动重算中...", stats.textContent);
    runDetection();
  });

  navDetect.addEventListener("click", () => switchView("detect"));
  navSimulate.addEventListener("click", () => switchView("simulate"));

  function switchView(view) {
    const showDetect = view === "detect";
    detectView.hidden = !showDetect;
    simulateView.hidden = showDetect;
    navDetect.classList.toggle("active", showDetect);
    navSimulate.classList.toggle("active", !showDetect);
    hideBlockTooltip();
    if (!showDetect) {
      window.requestAnimationFrame(() => {
        if (window.redrawSimulation) window.redrawSimulation();
      });
    }
  }

  frameScrubber.addEventListener("input", () => {
    const frameId = Number(frameScrubber.value);
    scrubberLabel.textContent = `Frame ${frameId}/${frameScrubber.max}`;
    drawCurrentFrame(frameId);
  });

  blockOpacityInput.addEventListener("input", () => {
    blockOpacityLabel.textContent = `${blockOpacityInput.value}%`;
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  detectOpacityInput.addEventListener("input", () => {
    detectOpacityLabel.textContent = `${detectOpacityInput.value}%`;
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  showDetectedInput.addEventListener("change", () => {
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  previewCanvas.addEventListener("mousemove", (event) => {
    lastMouseEvent = event;
    handlePreviewMouseMove(event);
  });
  previewCanvas.addEventListener("mouseleave", () => {
    lastMouseEvent = null;
    hideBlockTooltip();
  });

  blockSelectGrid.addEventListener("click", handleHistoryBlockSelect);

  clearHistoryBlocks.addEventListener("click", () => {
    clearHistorySelection();
    drawHistoryCurve();
  });

  sourceVideo.addEventListener("loadedmetadata", () => {
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  [frameScrubber, blockOpacityInput, detectOpacityInput].forEach(
    attachWheelToRange,
  );

  document.querySelectorAll("[data-param]").forEach((input) => {
    input.addEventListener("input", scheduleDetection);
    input.addEventListener("change", scheduleDetection);
  });

  detectionFrequencyInput.addEventListener("change", () => {
    if (!metadata) return;
    const selected = detectorOptions.find(
      (item) => String(item.detection_frequency) === detectionFrequencyInput.value,
    );
    if (selected) applyDetectorParams(selected.params);
  });

  [gammaInput, colsInput, rowsInput].forEach((input) => {
    input.addEventListener("input", () => {
      if (!selectedFile) return;
      preprocessDirty = true;
      startButton.disabled = false;
      setStatus("预处理参数已修改，请重新解析", "");
    });
  });

  async function startConversion() {
    if (!selectedFile || converting) return;

    converting = true;
    startButton.disabled = true;
    frameData = [];
    detectionResults = [];
    currentDetect = null;
    currentFrameId = null;
    detailCache.clear();
    frameDetailVersion += 1;
    preprocessDirty = false;
    resetProgress();
    resetScrubber();
    recomputeButton.disabled = true;
    setStatus("解析中...", "");
    drawChart();
    resetHistoryPanel();

    try {
      if (!currentSessionId) {
        const sessionResponse = await fetch("/api/v1/sessions", {
          method: "POST",
        });
        if (!sessionResponse.ok) throw new Error("创建会话失败");
        const sessionPayload = await sessionResponse.json();
        currentSessionId = sessionPayload.session_id;
      }

      const query = new URLSearchParams({
        name: selectedFile.name,
        cols: colsInput.value,
        rows: rowsInput.value,
        gamma: gammaInput.value,
      });
      const response = await fetch(
        `/api/v1/sessions/${currentSessionId}/video?${query.toString()}`,
        {
          method: "POST",
          body: selectedFile,
        },
      );

      if (!response.ok || !response.body) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || "解析失败");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) handleLine(JSON.parse(line));
        }
      }

      if (buffer.trim()) {
        handleLine(JSON.parse(buffer.trim()));
      }
    } catch (error) {
      setStatus(`错误：${error.message}`, "");
    } finally {
      converting = false;
      startButton.disabled = !selectedFile;
    }
  }

  function handleLine(payload) {
    if (payload.type === "meta") {
      metadata = payload;
      metadata.fps_mode = Math.round(Number(payload.fps));
      buildHistoryBlockGrid(payload.cols, payload.rows);
      applyDetectorOptions(
        payload.detector_options || [],
        payload.detector_params || {},
      );
      setStatus(
        `元数据：${payload.width}×${payload.height}，fps=${Number(payload.fps).toFixed(3)}，` +
        `分块=${payload.cols}×${payload.rows}`,
        "",
      );
    } else if (payload.type === "progress") {
      frameData[payload.frame_id] = payload;
      updateProgress(payload);
      drawPreview(payload.blocks);
    } else if (payload.type === "done") {
      const lastFrame = Math.max(0, payload.processed - 1);
      frameScrubber.max = String(lastFrame);
      frameScrubber.value = String(lastFrame);
      frameScrubber.disabled = payload.processed <= 1;
      scrubberLabel.textContent = `Frame ${lastFrame}/${lastFrame}`;
      setStatus(`完成，共处理 ${payload.processed} 帧`, "");
      runDetection();
      if (lastFrame >= 0) drawCurrentFrame(lastFrame);
      recomputeButton.disabled = false;
    }
  }

  function updateProgress(payload) {
    const percent = Number(payload.percent) || 0;
    progressFill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
    const total = metadata && metadata.frame_count ? metadata.frame_count : 0;
    const detail =
      `frame_id=${payload.frame_id}` +
      (total ? ` / ${total}` : "") +
      `\npercent=${percent.toFixed(2)}%` +
      `\ntimestamp=${Number(payload.timestamp).toFixed(4)}s` +
      `\nmean=${Number(payload.mean).toFixed(3)}` +
      `\nmin=${Number(payload.min).toFixed(3)}` +
      `\nmax=${Number(payload.max).toFixed(3)}`;
    setStatus(detail, detail);
  }

  function applyDetectorParams(params) {
    const rawYMax = numberOr(params.y_max, 16383);
    const yMaxBit = numberOr(
      params.y_max_bit,
      rawYMax > 1 ? Math.round(Math.log2(rawYMax + 1)) : 14,
    );
    const values = {
      detection_frequency: numberOr(params.detection_frequency, 50),
      group_size: numberOr(params.group_size, 4),
      min_frames: numberOr(params.min_frames, 30),
      min_match_groups: numberOr(params.min_match_groups, 2),
      min_group_amp_ratio: numberOr(params.min_group_amp_ratio, 0.001),
      min_amp_pos_ratio: numberOr(params.min_amp_pos_ratio, 0.5),
      same_pattern_ratio: numberOr(params.same_pattern_ratio, 0.8),
      slope_max: numberOr(params.slope_max, 10),
      max_group_amp_cv: numberOr(params.max_group_amp_cv, 0.4),
      y_max_bit: yMaxBit,
    };
    Object.entries(values).forEach(([key, value]) => {
      const input = document.querySelector(`[data-param="${key}"]`);
      if (input) input.value = String(value);
    });
    if (metadata) {
      fpsInput.value = String(metadata.fps_mode || Math.round(metadata.fps));
    }
  }

  function applyDetectorOptions(options, fallbackParams) {
    detectorOptions = Array.isArray(options) ? options : [];
    detectionFrequencyInput.innerHTML = "";

    if (detectorOptions.length === 0) {
      const fallbackFrequency =
        Number(fallbackParams.detection_frequency) || 50;
      const option = document.createElement("option");
      option.value = String(fallbackFrequency);
      option.textContent = `${fallbackFrequency} Hz`;
      detectionFrequencyInput.appendChild(option);
      detectionFrequencyInput.value = String(fallbackFrequency);
      applyDetectorParams(fallbackParams);
      return;
    }

    const preferred =
      Number(fallbackParams.detection_frequency) ||
      Number(detectorOptions[0].detection_frequency);
    let selected = null;
    for (const item of detectorOptions) {
      const option = document.createElement("option");
      option.value = String(item.detection_frequency);
      option.textContent = `${item.detection_frequency} Hz`;
      detectionFrequencyInput.appendChild(option);
      if (Number(item.detection_frequency) === preferred) selected = item;
    }

    if (!selected) selected = detectorOptions[0];
    detectionFrequencyInput.value = String(selected.detection_frequency);
    applyDetectorParams(selected.params);
  }

  function readParams() {
    const params = {};
    document.querySelectorAll("[data-param]").forEach((input) => {
      params[input.dataset.param] = Number(input.value);
    });
    params.group_size = Math.max(2, Math.round(params.group_size || 4));
    params.min_frames = Math.max(1, Math.round(params.min_frames || 30));
    const yMaxBit = Math.max(1, Math.round(params.y_max_bit || 14));
    params.y_max = (2 ** yMaxBit) - 1;
    params.y_max_bit = yMaxBit;
    return params;
  }

  function scheduleDetection() {
    if (!metadata || frameData.length === 0) return;
    const version = ++detectionVersion;
    setStatus("检测参数已修改，重算中...", stats.textContent);
    setTimeout(() => {
      if (version !== detectionVersion) return;
      runDetection();
    }, 40);
  }

  async function runDetection() {
    if (!metadata || frameData.length === 0) return;
    const params = readParams();
    const blockCount = metadata.cols * metadata.rows;
    detailCache.clear();
    frameDetailVersion += 1;
    setStatus("检测重算中...", stats.textContent);
    try {
      if (!currentSessionId) throw new Error("会话不存在，请重新解析视频");
      const response = await fetch(
        `/api/v1/sessions/${currentSessionId}/detect`,
        {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fps: metadata.fps_mode, params }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "检测重算失败");
      }
      const payload = await response.json();
      const results = Array.isArray(payload.results) ? payload.results : [];
      detectionResults = results.map((result) =>
        result ? normalizeResult(result) : emptyResult(blockCount),
      );
      currentDetect = detectionResults[currentFrameId] || null;
      updateDetectionStats(currentDetect);
      drawChart();
      if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
      drawHistoryCurve();
      setStatus(`检测完成，共 ${frameData.length} 帧`, stats.textContent);
    } catch (error) {
      setStatus(`检测失败：${error.message}`, "");
    }
  }

  function refreshFrameDetail(frameId) {
    if (!metadata || !detectionResults[frameId]) return;
    if (!currentSessionId) return;
    if (detailCache.has(frameId)) {
      applyFrameDetail(frameId, detailCache.get(frameId));
      return;
    }
    const params = readParams();
    const version = ++frameDetailVersion;
    const query = new URLSearchParams({
      fps: String(metadata.fps_mode),
      detection_frequency: String(params.detection_frequency),
      group_size: String(params.group_size),
      min_frames: String(params.min_frames),
      min_match_groups: String(params.min_match_groups),
      min_group_amp_ratio: String(params.min_group_amp_ratio),
      min_amp_pos_ratio: String(params.min_amp_pos_ratio),
      same_pattern_ratio: String(params.same_pattern_ratio),
      slope_max: String(params.slope_max),
      max_group_amp_cv: String(params.max_group_amp_cv),
      y_max_bit: String(params.y_max_bit),
    });
    fetch(
      `/api/v1/sessions/${currentSessionId}/frames/${frameId}?${query.toString()}`,
    )
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!payload || !payload.ready) return;
        if (payload.frame_id !== frameId || version !== frameDetailVersion) {
          return;
        }
        detailCache.set(frameId, payload.result);
        applyFrameDetail(frameId, payload.result);
      })
      .catch(() => {});
  }

  function applyFrameDetail(frameId, detail) {
    const target = detectionResults[frameId];
    if (!target) return;
    Object.assign(target, normalizeResult(detail));
    if (frameId !== currentFrameId) return;
    currentDetect = target;
    drawHistoryCurve();
    if (lastMouseEvent) handlePreviewMouseMove(lastMouseEvent);
  }

  function normalizeResult(result) {
    const floatFields = [
      "sign_flip_ratio",
      "mean_abs_diff",
      "diff_cv",
      "avg_ratio_diff",
      "avg_delta_diff",
    ];
    for (const key of floatFields) {
      if (Array.isArray(result[key])) {
        result[key] = Float64Array.from(result[key], (value) =>
          value == null ? NaN : Number(value),
        );
      }
    }
    if (Array.isArray(result.block_flags)) {
      result.block_flags = Uint8Array.from(result.block_flags, (value) =>
        Number(value) || 0,
      );
    }
    if (Array.isArray(result.valid_group_count)) {
      result.valid_group_count = Uint8Array.from(
        result.valid_group_count,
        (value) => Number(value) || 0,
      );
    }
    if (Array.isArray(result.flip_count)) {
      result.flip_count = Uint32Array.from(result.flip_count, (value) =>
        Number(value) || 0,
      );
    }
    return result;
  }

  function emptyResult(blockCount) {
    return {
      block_flags: new Uint8Array(blockCount),
      flicker_block_num: 0,
      sign_flip_ratio: new Float64Array(blockCount),
      mean_abs_diff: new Float64Array(blockCount).fill(NaN),
      diff_cv: new Float64Array(blockCount).fill(NaN),
      avg_ratio_diff: new Float64Array(blockCount).fill(NaN),
      avg_delta_diff: new Float64Array(blockCount).fill(NaN),
      valid_group_count: new Uint8Array(blockCount),
      flip_count: new Uint32Array(blockCount),
      groups: [],
      group_amp_pass: [],
      group_slope: [],
      group_valid: [],
      group_dominant: [],
      group_sign: [],
      group_count: 0,
      position_count: 0,
      min_group_amp: 0,
      k_value: 0,
      slope_max: 0,
      min_match_groups: 0,
      same_pattern_ratio: 0,
      max_group_amp_cv: 0,
    };
  }

  function numberOr(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function drawCurrentFrame(frameId) {
    if (!metadata || !frameData[frameId]) return;
    currentFrameId = frameId;
    currentDetect = detectionResults[frameId] || null;
    hideBlockTooltip();
    updateDetectionStats(currentDetect);
    drawHistoryCurve();
    refreshFrameDetail(frameId);

    const frame = frameData[frameId];
    if (frame.original) {
      paintWithOriginal(frameId);
      return;
    }

    if (!sourceVideo.videoWidth || sourceVideo.readyState < 1) {
      drawPreview(frame.blocks);
      return;
    }

    const targetTime = Number(frame.timestamp) || frameId / metadata.fps;
    if (Math.abs(sourceVideo.currentTime - targetTime) > 0.001) {
      sourceVideo.currentTime = targetTime;
      sourceVideo.addEventListener(
        "seeked",
        () => paintOverlay(frameId),
        { once: true },
      );
      setTimeout(() => {
        if (currentFrameId === frameId) paintOverlay(frameId);
      }, 150);
    } else {
      paintOverlay(frameId);
    }
  }

  function paintOverlay(frameId) {
    const frame = frameData[frameId];
    if (!frame || !metadata) return;
    if (frame.original) {
      paintWithOriginal(frameId);
      return;
    }
    if (!sourceVideo.videoWidth) return;

    const width = 640;
    const height = Math.max(1, Math.round(width * metadata.height / metadata.width));
    const canvas = previewCanvas;
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    drawGrayscaleVideo(ctx, width, height);
    drawBlockOverlay(ctx, width, height, frame.blocks);
  }

  function paintWithOriginal(frameId) {
    const frame = frameData[frameId];
    if (!frame || !frame.original || !metadata) return;

    const image = new Image();
    image.onload = () => {
      if (currentFrameId !== frameId) return;
      const width = 640;
      const height = Math.max(1, Math.round(width * metadata.height / metadata.width));
      const canvas = previewCanvas;
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, width, height);
      ctx.drawImage(image, 0, 0, width, height);
      drawBlockOverlay(ctx, width, height, frame.blocks);
    };
    image.src = `data:image/jpeg;base64,${frame.original}`;
  }

  function drawBlockOverlay(ctx, width, height, blocks) {
    if (!metadata || !blocks) return;
    const blockCanvas = document.createElement("canvas");
    blockCanvas.width = metadata.cols;
    blockCanvas.height = metadata.rows;
    const blockCtx = blockCanvas.getContext("2d");
    const image = blockCtx.createImageData(metadata.cols, metadata.rows);
    const yMax = metadata.y_max || 16383;

    for (let i = 0; i < blocks.length; i += 1) {
      const normalized = Math.min(1, Math.max(0, Number(blocks[i]) / yMax));
      const value = Math.round(normalized * 255);
      const offset = i * 4;
      image.data[offset] = value;
      image.data[offset + 1] = value;
      image.data[offset + 2] = value;
      image.data[offset + 3] = 255;
    }
    blockCtx.putImageData(image, 0, 0);

    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = Number(blockOpacityInput.value) / 100;
    ctx.drawImage(blockCanvas, 0, 0, width, height);
    ctx.globalAlpha = 1;
    ctx.imageSmoothingEnabled = true;

    drawDetectedBlocks(ctx, width, height);
    drawBlockGrid(ctx, width, height);
  }

  function drawDetectedBlocks(ctx, width, height) {
    if (!showDetectedInput.checked || !currentDetect) return;
    const cellW = width / metadata.cols;
    const cellH = height / metadata.rows;
    const alpha = Number(detectOpacityInput.value) / 100;
    ctx.fillStyle = `rgba(22, 163, 74, ${alpha})`;

    for (let b = 0; b < currentDetect.block_flags.length; b += 1) {
      if (!currentDetect.block_flags[b]) continue;
      const col = b % metadata.cols;
      const row = Math.floor(b / metadata.cols);
      ctx.fillRect(
        Math.round(col * cellW),
        Math.round(row * cellH),
        Math.ceil(cellW) + 1,
        Math.ceil(cellH) + 1,
      );
    }
  }

  function drawBlockGrid(ctx, width, height) {
    const cellW = width / metadata.cols;
    const cellH = height / metadata.rows;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();

    for (let col = 1; col < metadata.cols; col += 1) {
      const x = Math.round(col * cellW) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
    }
    for (let row = 1; row < metadata.rows; row += 1) {
      const y = Math.round(row * cellH) + 0.5;
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
    }
    ctx.stroke();
  }

  function drawPreview(blocks) {
    if (!metadata || !blocks || blocks.length === 0) return;
    const cols = metadata.cols;
    const rows = metadata.rows;
    const yMax = metadata.y_max || 16383;
    const canvas = previewCanvas;
    const ctx = canvas.getContext("2d");
    const image = ctx.createImageData(cols, rows);

    for (let i = 0; i < blocks.length; i += 1) {
      const normalized = Math.min(1, Math.max(0, Number(blocks[i]) / yMax));
      const value = Math.round(normalized * 255);
      const offset = i * 4;
      image.data[offset] = value;
      image.data[offset + 1] = value;
      image.data[offset + 2] = value;
      image.data[offset + 3] = 255;
    }

    canvas.width = cols;
    canvas.height = rows;
    ctx.putImageData(image, 0, 0);
  }

  function drawGrayscaleVideo(ctx, width, height) {
    const offscreen = document.createElement("canvas");
    offscreen.width = width;
    offscreen.height = height;
    const offCtx = offscreen.getContext("2d");
    offCtx.drawImage(sourceVideo, 0, 0, width, height);
    const imageData = offCtx.getImageData(0, 0, width, height);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      const gray = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
      data[i] = gray;
      data[i + 1] = gray;
      data[i + 2] = gray;
    }
    offCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(offscreen, 0, 0);
  }

  function updateDetectionStats(detect) {
    if (!detect) {
      stats.textContent = "检测数据生成后显示";
      return;
    }
    const total = metadata.cols * metadata.rows;
    const detail =
      `检测块：${detect.flicker_block_num} / ${total}` +
      `\n阳性比例：${total ? (detect.flicker_block_num / total).toFixed(3) : "0"}` +
      `\nsign_flip_ratio：${avgFinite(detect.sign_flip_ratio)}` +
      `\nmean_abs_diff：${avgFinite(detect.mean_abs_diff)}` +
      `\ndiff_cv：${maxFinite(detect.diff_cv)}` +
      `\navg_ratio_diff：${avgFinite(detect.avg_ratio_diff)}` +
      `\navg_delta_diff：${avgFinite(detect.avg_delta_diff)}`;
    stats.textContent = detail;
  }

  function drawChart() {
    const canvas = chartCanvas;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    if (!detectionResults || detectionResults.length === 0) {
      chartSummary.textContent = "等待数据";
      return;
    }

    const counts = detectionResults.map((result) => result ? result.flicker_block_num : 0);
    const maxCount = Math.max(1, ...counts);
    const frameCount = counts.length;
    const sum = counts.reduce((acc, value) => acc + value, 0);
    chartSummary.textContent =
      `峰值 ${maxCount}，平均 ${(sum / frameCount).toFixed(1)}，帧 ${frameCount}`;

    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = Math.round(height - (height * i) / 4) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    ctx.beginPath();
    for (let i = 0; i < frameCount; i += 1) {
      const x = (i / Math.max(1, frameCount - 1)) * width;
      const y = height - (counts[i] / maxCount) * (height - 8) - 4;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = "rgba(37, 99, 235, 0.12)";
    ctx.fill();
  }

  function drawHistoryCurve() {
    const ctx = historyCanvas.getContext("2d");
    const width = historyCanvas.width;
    const height = historyCanvas.height;
    ctx.clearRect(0, 0, width, height);
    historyLegend.textContent = "";

    if (!metadata || frameData.length === 0) {
      historySummary.textContent = "等待数据";
      return;
    }

    const selected = [...selectedHistoryBlocks].sort((a, b) => a - b);
    if (selected.length === 0) {
      historySummary.textContent = "未选择块";
      return;
    }

    const params = readParams();
    const windowSize = Math.max(1, Math.round(params.min_frames) || 1);
    const frameId = currentFrameId !== null ? currentFrameId : frameData.length - 1;
    if (frameId < 0 || !frameData[frameId]) {
      historySummary.textContent = "无可用帧";
      return;
    }

    const start = Math.max(0, frameId - windowSize + 1);
    const series = [];
    const allValues = [];
    let firstFrame = Infinity;
    let lastFrame = -Infinity;
    for (const blockIndex of selected) {
      const points = [];
      for (let i = start; i <= frameId; i += 1) {
        const frame = frameData[i];
        const value = frame && frame.blocks ? Number(frame.blocks[blockIndex]) : NaN;
        if (!Number.isFinite(value)) continue;
        points.push({ frameId: i, value });
        allValues.push(value);
      }
      if (points.length < 2) continue;
      series.push({
        blockIndex,
        points,
        color: HISTORY_COLORS[series.length % HISTORY_COLORS.length],
      });
      firstFrame = Math.min(firstFrame, points[0].frameId);
      lastFrame = Math.max(lastFrame, points[points.length - 1].frameId);
    }

    if (series.length === 0 || allValues.length === 0) {
      historySummary.textContent = "选中块数据不足";
      return;
    }

    buildHistoryLegend(series);

    const rawMin = Math.min(...allValues);
    const rawMax = Math.max(...allValues);
    const mean = allValues.reduce((sum, value) => sum + value, 0) / allValues.length;
    const range = rawMax - rawMin || 1;
    const padding = range * 0.08;
    const minValue = rawMin - padding;
    const maxValue = rawMax + padding;

    const padLeft = 58;
    const padRight = 14;
    const padTop = 22;
    const padBottom = 26;
    const plotWidth = width - padLeft - padRight;
    const plotHeight = height - padTop - padBottom;
    const frameSpan = Math.max(1, lastFrame - firstFrame);

    const px = (frameIdValue) =>
      padLeft + ((frameIdValue - firstFrame) / frameSpan) * plotWidth;
    const py = (value) =>
      padTop + (1 - (value - minValue) / (maxValue - minValue)) * plotHeight;

    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const ratio = i / 4;
      const value = minValue + ratio * (maxValue - minValue);
      const y = Math.round(py(value)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();
      ctx.fillStyle = "#667085";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(formatHistoryNumber(value), padLeft - 6, y);
    }

    const detect = currentDetect;
    const groups =
      detect && Array.isArray(detect.groups) ? detect.groups : [];
    if (groups.length > 0) {
      for (const item of series) {
        const dominantFlags = detect.group_dominant[item.blockIndex];
        const validFlags = detect.group_valid[item.blockIndex];
        if (!dominantFlags || !validFlags) continue;
        for (let g = 0; g < groups.length; g += 1) {
          const startX = px(groups[g].start_frame);
          const endX = px(groups[g].end_frame);
          if (dominantFlags[g]) {
            ctx.fillStyle = hexToRgba(item.color, 0.16);
          } else if (validFlags[g]) {
            ctx.fillStyle = "rgba(148, 163, 184, 0.10)";
          } else {
            continue;
          }
          ctx.fillRect(startX, padTop, Math.max(1, endX - startX), plotHeight);
        }
      }

      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = "rgba(100, 116, 139, 0.35)";
      const boundaries = [];
      for (const group of groups) boundaries.push(group.start_frame);
      boundaries.push(groups[groups.length - 1].end_frame);
      for (const boundary of boundaries) {
        const x = Math.round(px(boundary)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x, padTop);
        ctx.lineTo(x, height - padBottom);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      ctx.fillStyle = "#475569";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (const group of groups) {
        const startX = px(group.start_frame);
        const endX = px(group.end_frame);
        const label = `G${group.group_index} ${group.start_frame}-${group.end_frame}`;
        ctx.fillText(label, startX + (endX - startX) / 2, 4);
      }
    }

    ctx.strokeStyle = "#dbeafe";
    for (const frameIdValue of [firstFrame, lastFrame]) {
      const x = Math.round(px(frameIdValue)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, padTop);
      ctx.lineTo(x, height - padBottom);
      ctx.stroke();
      ctx.fillStyle = "#667085";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(String(frameIdValue), x, height - padBottom + 7);
    }

    for (const item of series) {
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      item.points.forEach((point, index) => {
        const x = px(point.frameId);
        const y = py(point.value);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      const lastPoint = item.points[item.points.length - 1];
      ctx.fillStyle = item.color;
      ctx.beginPath();
      ctx.arc(px(lastPoint.frameId), py(lastPoint.value), 3.5, 0, Math.PI * 2);
      ctx.fill();
    }

    historySummary.textContent =
      `已选 ${series.length} 块 · 组 ${groups.length} · ` +
      `帧 ${firstFrame}-${lastFrame} · ` +
      `min=${formatHistoryNumber(rawMin)} ` +
      `max=${formatHistoryNumber(rawMax)} ` +
      `mean=${formatHistoryNumber(mean)}`;
  }

  function buildHistoryLegend(series) {
    historyLegend.textContent = "";
    for (const item of series) {
      const label = document.createElement("span");
      label.className = "history-legend-item";
      const swatch = document.createElement("i");
      swatch.className = "history-legend-swatch";
      swatch.style.backgroundColor = item.color;
      label.append(swatch, document.createTextNode(`块 ${item.blockIndex}`));
      historyLegend.appendChild(label);
    }
  }

  function handleHistoryBlockSelect(event) {
    const cell = event.target.closest("[data-block]");
    if (!cell) return;
    const block = Number(cell.dataset.block);
    if (selectedHistoryBlocks.has(block)) {
      selectedHistoryBlocks.delete(block);
      cell.classList.remove("selected");
    } else {
      selectedHistoryBlocks.add(block);
      cell.classList.add("selected");
    }
    drawHistoryCurve();
  }

  function clearHistorySelection() {
    selectedHistoryBlocks.clear();
    blockSelectGrid.querySelectorAll(".selected").forEach((cell) => {
      cell.classList.remove("selected");
    });
  }

  function buildHistoryBlockGrid(cols, rows) {
    selectedHistoryBlocks.clear();
    blockSelectGrid.innerHTML = "";
    blockSelectGrid.style.gridTemplateColumns =
      `repeat(${Math.max(1, cols)}, minmax(0, 1fr))`;
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const block = row * cols + col;
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "block-select-cell";
        cell.dataset.block = String(block);
        cell.title = `块 ${block}`;
        cell.textContent = String(block);
        blockSelectGrid.appendChild(cell);
      }
    }
    drawHistoryCurve();
  }

  function handlePreviewMouseMove(event) {
    if (!metadata || currentFrameId === null) {
      hideBlockTooltip();
      return;
    }
    const rect = previewCanvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      hideBlockTooltip();
      return;
    }

    const scaleX = previewCanvas.width / rect.width;
    const scaleY = previewCanvas.height / rect.height;
    const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left)) * scaleX;
    const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top)) * scaleY;
    const col = Math.min(
      metadata.cols - 1,
      Math.floor(x / (previewCanvas.width / metadata.cols)),
    );
    const row = Math.min(
      metadata.rows - 1,
      Math.floor(y / (previewCanvas.height / metadata.rows)),
    );
    const blockIndex = row * metadata.cols + col;

    blockTooltip.textContent = buildBlockTooltipText(blockIndex);
    blockTooltip.hidden = false;

    const stage = blockTooltip.parentElement;
    const stageRect = stage.getBoundingClientRect();
    let left = event.clientX - stageRect.left + 14;
    let top = event.clientY - stageRect.top + 14;
    const tooltipWidth = blockTooltip.offsetWidth;
    const tooltipHeight = blockTooltip.offsetHeight;
    if (left + tooltipWidth > stageRect.width - 4) {
      left = event.clientX - stageRect.left - tooltipWidth - 8;
    }
    if (top + tooltipHeight > stageRect.height - 4) {
      top = event.clientY - stageRect.top - tooltipHeight - 8;
    }
    blockTooltip.style.left = `${Math.max(4, left)}px`;
    blockTooltip.style.top = `${Math.max(4, top)}px`;
  }

  function hideBlockTooltip() {
    blockTooltip.hidden = true;
  }

  function buildBlockTooltipText(blockIndex) {
    const col = blockIndex % metadata.cols;
    const row = Math.floor(blockIndex / metadata.cols);
    const frame = frameData[currentFrameId];
    const brightness = frame && frame.blocks ? Number(frame.blocks[blockIndex]) : NaN;
    const lines = [`块 ${blockIndex}（行 ${row}，列 ${col}）`];
    if (Number.isFinite(brightness)) {
      lines.push(`亮度 ${formatHistoryNumber(brightness)}`);
    }

    const detect = currentDetect;
    if (!detect || detect.group_count <= 0) {
      lines.push("暂无检测数据");
      return lines.join("\n");
    }

    lines.push(`flip ${detect.flip_count[blockIndex]} / ${detect.min_match_groups}`);
    lines.push(
      `same_pattern_ratio ${formatHistoryNumber(detect.sign_flip_ratio[blockIndex])} / ` +
      `${formatHistoryNumber(detect.same_pattern_ratio)}`,
    );
    lines.push(
      `CV ${formatHistoryNumber(detect.diff_cv[blockIndex])} / ` +
      `${formatHistoryNumber(detect.max_group_amp_cv)}`,
    );
    if (Number.isFinite(detect.mean_abs_diff[blockIndex])) {
      lines.push(`平均振幅差 ${formatHistoryNumber(detect.mean_abs_diff[blockIndex])}`);
    }

    const groups = Array.isArray(detect.groups) ? detect.groups : [];
    const groupRows = detect.group_valid ? detect.group_valid[blockIndex] : undefined;
    if (groupRows) {
      const validCount = groupRows.reduce(
        (sum, value) => sum + (value ? 1 : 0),
        0,
      );
      lines.push(`有效组 ${validCount} / ${detect.group_count}`);
      const groupAmpPass = detect.group_amp_pass[blockIndex] || [];
      const groupSlope = detect.group_slope[blockIndex] || [];
      const groupDominant = detect.group_dominant[blockIndex] || [];
      const groupSign = detect.group_sign[blockIndex] || [];
      for (let g = 0; g < groups.length; g += 1) {
        const group = groups[g];
        const ampPass = groupAmpPass[g] ?? 0;
        const slope = groupSlope[g];
        const slopeText = Number.isFinite(slope)
          ? formatHistoryNumber(slope)
          : "-";
        const status = groupRows[g] ? "有效" : "无效";
        const dominant = groupDominant[g] ? "·主导" : "";
        const sign = groupSign[g] || "-";
        lines.push(
          `G${g} 帧${group.start_frame}-${group.end_frame} ` +
          `amp${ampPass}/${detect.position_count} ` +
          `slope${slopeText}/${formatHistoryNumber(detect.slope_max)} ` +
          `${status}${dominant} sign${sign}`,
        );
      }
    } else if (groups.length > 0) {
      lines.push("分组详情加载中...");
    }

    lines.push(detect.block_flags[blockIndex] ? "判定：命中" : "判定：未命中");
    return lines.join("\n");
  }

  function formatHistoryNumber(value) {
    return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2);
  }

  function hexToRgba(hex, alpha) {
    const value = parseInt(hex.slice(1), 16);
    const red = (value >> 16) & 255;
    const green = (value >> 8) & 255;
    const blue = value & 255;
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function resetHistoryPanel() {
    selectedHistoryBlocks.clear();
    blockSelectGrid.innerHTML = "";
    blockSelectGrid.style.gridTemplateColumns = "";
    historyLegend.textContent = "";
    historySummary.textContent = "等待数据";
    const ctx = historyCanvas.getContext("2d");
    ctx.clearRect(0, 0, historyCanvas.width, historyCanvas.height);
  }

  async function loadDefaultDetectorOptions() {
    try {
      const response = await fetch("/api/v1/options?fps=30");
      if (!response.ok) return;
      const payload = await response.json();
      const options = Array.isArray(payload.options) ? payload.options : [];
      if (options.length === 0) return;
      const preferred =
        options.find((item) => Number(item.detection_frequency) === 50) ||
        options[0];
      applyDetectorOptions(options, preferred.params || {});
    } catch (error) {
      // 接口不可用时保留静态 50 Hz 占位项。
    }
  }

  function avgFinite(array) {
    let sum = 0;
    let count = 0;
    for (const value of array) {
      if (Number.isFinite(value)) {
        sum += value;
        count += 1;
      }
    }
    return count ? (sum / count).toFixed(3) : "NaN";
  }

  function maxFinite(array) {
    let maxValue = NaN;
    for (const value of array) {
      if (Number.isFinite(value) && (Number.isNaN(maxValue) || value > maxValue)) {
        maxValue = value;
      }
    }
    return Number.isNaN(maxValue) ? "NaN" : maxValue.toFixed(3);
  }

  function resetProgress() {
    progressFill.style.width = "0%";
    stats.textContent = "";
  }

  function attachWheelToRange(input) {
    input.addEventListener(
      "wheel",
      (event) => {
        if (input.disabled) return;
        event.preventDefault();
        const step = Number(input.step) || 1;
        const min = Number(input.min);
        const max = Number(input.max);
        const delta = event.deltaY < 0 ? step : -step;
        const next = Math.min(max, Math.max(min, Number(input.value) + delta));
        if (next !== Number(input.value)) {
          input.value = String(next);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      },
      { passive: false },
    );
  }

  function resetScrubber() {
    frameScrubber.max = "0";
    frameScrubber.value = "0";
    frameScrubber.disabled = true;
    scrubberLabel.textContent = "Frame 0/0";
  }

  function setStatus(statusText, detail) {
    frameStatus.textContent = statusText;
    if (detail) stats.textContent = detail;
  }

  loadDefaultDetectorOptions();
})();
