(() => {
  "use strict";

  const SIGN_ZERO_EPSILON = 1e-4;
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
  let preprocessDirty = false;
  let selectedHistoryBlocks = new Set();
  let detectorOptions = [];

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

  previewCanvas.addEventListener("mousemove", handlePreviewMouseMove);
  previewCanvas.addEventListener("mouseleave", hideBlockTooltip);

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
    preprocessDirty = false;
    resetProgress();
    resetScrubber();
    recomputeButton.disabled = true;
    setStatus("解析中...", "");
    drawChart();
    resetHistoryPanel();

    const query = new URLSearchParams({
      name: selectedFile.name,
      cols: colsInput.value,
      rows: rowsInput.value,
      gamma: gammaInput.value,
    });

    try {
      const response = await fetch(`/api/convert?${query.toString()}`, {
        method: "POST",
        body: selectedFile,
      });

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

  function runDetection() {
    if (!metadata || frameData.length === 0) return;
    const params = readParams();
    const blockCount = metadata.cols * metadata.rows;
    const history = [];
    const historyFrameIds = [];
    const results = [];

    for (let i = 0; i < frameData.length; i += 1) {
      const frame = frameData[i];
      if (!frame || !frame.blocks) {
        results.push(emptyResult(blockCount));
        continue;
      }
      history.push(Float64Array.from(frame.blocks));
      historyFrameIds.push(frame.frame_id);
      if (history.length > params.min_frames) history.shift();
      if (historyFrameIds.length > params.min_frames) historyFrameIds.shift();
      if (history.length >= params.min_frames) {
        results[i] = detectWindow(
          history,
          params,
          blockCount,
          historyFrameIds,
        );
      } else {
        results[i] = emptyResult(blockCount);
      }
    }

    detectionResults = results;
    currentDetect = results[currentFrameId] || null;
    updateDetectionStats(currentDetect);
    drawChart();
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
    drawHistoryCurve();
    setStatus(
      `检测完成，共 ${frameData.length} 帧`,
      stats.textContent,
    );
  }

  function detectWindow(history, params, blockCount, frameIds) {
    const result = emptyResult(blockCount);
    const groupSize = Math.max(2, Math.round(params.group_size));
    const groupStride = groupSize - 1;
    const positionCount = groupStride;
    const validCount = history.length;
    const groupCount = validCount > 0 ? Math.floor((validCount - 1) / groupStride) : 0;
    result.group_count = groupCount;
    result.position_count = positionCount;
    if (groupCount <= 0) return result;

    const usedFrames = groupSize + (groupCount - 1) * groupStride;
    const dropCount = validCount - usedFrames;
    result.groups = [];
    for (let g = 0; g < groupCount; g += 1) {
      const startIndex = dropCount + g * groupStride;
      const endIndex = startIndex + groupSize - 1;
      result.groups.push({
        group_index: g,
        start_frame: frameIds ? Number(frameIds[startIndex]) : startIndex,
        end_frame: frameIds ? Number(frameIds[endIndex]) : endIndex,
      });
    }
    result.group_amp_pass = Array.from(
      { length: blockCount },
      () => new Uint8Array(groupCount),
    );
    result.group_slope = Array.from(
      { length: blockCount },
      () => new Float64Array(groupCount).fill(NaN),
    );
    result.group_valid = Array.from(
      { length: blockCount },
      () => new Uint8Array(groupCount),
    );
    result.group_dominant = Array.from(
      { length: blockCount },
      () => new Uint8Array(groupCount),
    );
    result.group_sign = Array.from(
      { length: blockCount },
      () => Array.from({ length: groupCount }, () => ""),
    );
    const diffMatrix = [];
    const ampMatrix = [];
    const signMatrix = [];

    for (let g = 0; g < groupCount; g += 1) {
      const diffGroup = [];
      const ampGroup = [];
      const signGroup = [];
      const start = dropCount + g * groupStride;
      for (let p = 0; p < positionCount; p += 1) {
        const diff = new Float64Array(blockCount);
        const amp = new Float64Array(blockCount);
        const sign = new Int8Array(blockCount);
        for (let b = 0; b < blockCount; b += 1) {
          const value = history[start + p + 1][b] - history[start + p][b];
          diff[b] = value;
          amp[b] = Math.abs(value);
          sign[b] = value > SIGN_ZERO_EPSILON ? 1 : (value < -SIGN_ZERO_EPSILON ? -1 : 0);
        }
        diffGroup.push(diff);
        ampGroup.push(amp);
        signGroup.push(sign);
      }
      diffMatrix.push(diffGroup);
      ampMatrix.push(ampGroup);
      signMatrix.push(signGroup);
    }

    for (let g = 0; g < groupCount; g += 1) {
      for (let b = 0; b < blockCount; b += 1) {
        let pattern = "";
        for (let p = 0; p < positionCount; p += 1) {
          const value = signMatrix[g][p][b];
          pattern += value > 0 ? "+" : value < 0 ? "-" : "0";
        }
        result.group_sign[b][g] = pattern;
      }
    }

    const minGroupAmp = params.y_max * params.min_group_amp_ratio;
    const ampGateEnabled = params.min_amp_pos_ratio > 0;
    const kValue = ampGateEnabled ? Math.ceil(positionCount * params.min_amp_pos_ratio) : 0;
    result.min_group_amp = minGroupAmp;
    result.k_value = kValue;
    result.slope_max = Number(params.slope_max) || 0;
    result.min_match_groups = Number(params.min_match_groups) || 0;
    result.same_pattern_ratio = Number(params.same_pattern_ratio) || 0;
    result.max_group_amp_cv = Number(params.max_group_amp_cv) || 0;
    const ampGroupOk = Array.from({ length: groupCount }, () => new Uint8Array(blockCount));

    if (ampGateEnabled) {
      for (let g = 0; g < groupCount; g += 1) {
        for (let b = 0; b < blockCount; b += 1) {
          let passCount = 0;
          for (let p = 0; p < positionCount; p += 1) {
            if (ampMatrix[g][p][b] > minGroupAmp) passCount += 1;
          }
          result.group_amp_pass[b][g] = passCount;
          result.amp_pass_count[b] = Math.max(
            result.amp_pass_count[b],
            passCount,
          );
          ampGroupOk[g][b] = passCount >= kValue ? 1 : 0;
        }
      }
    } else {
      for (let g = 0; g < groupCount; g += 1) ampGroupOk[g].fill(1);
      result.amp_pass_count.fill(positionCount);
      result.group_amp_pass.forEach((passes) => passes.fill(positionCount));
    }

    const slopeGroupOk = Array.from({ length: groupCount }, () => new Uint8Array(blockCount));
    if (params.slope_max > 0) {
      for (let b = 0; b < blockCount; b += 1) {
        for (let g = 0; g < groupCount; g += 1) {
          if (!ampGroupOk[g][b]) continue;
          const start = dropCount + g * groupStride;
          const slope = (history[start + groupStride][b] - history[start][b]) / groupStride;
          result.group_slope[b][g] = slope;
          const absSlope = Math.abs(slope);
          if (!Number.isFinite(result.max_abs_slope[b]) || absSlope > result.max_abs_slope[b]) {
            result.max_abs_slope[b] = absSlope;
          }
          slopeGroupOk[g][b] = absSlope <= params.slope_max ? 1 : 0;
        }
      }
    } else {
      for (let g = 0; g < groupCount; g += 1) slopeGroupOk[g].set(ampGroupOk[g]);
    }

    for (let b = 0; b < blockCount; b += 1) {
      const validIndices = [];
      for (let g = 0; g < groupCount; g += 1) {
        if (slopeGroupOk[g][b]) validIndices.push(g);
      }
      result.valid_group_count[b] = validIndices.length;
      for (const g of validIndices) result.group_valid[b][g] = 1;

      let dominantOk = false;
      let dominantGroups = [];
      let signFlipRatioValue = 0;
      let bestSize = 0;

      if (validIndices.length > 0) {
        const classId = new Int32Array(validIndices.length).fill(-1);
        const assigned = new Uint8Array(validIndices.length);
        let bestLocal = -1;
        let bestRep = -1;

        for (let i = 0; i < validIndices.length; i += 1) {
          if (assigned[i]) continue;
          const matches = [];
          for (let j = i; j < validIndices.length; j += 1) {
            if (assigned[j]) continue;
            if (sameSign(signMatrix, validIndices[i], validIndices[j], positionCount, b)) {
              matches.push(j);
            }
          }
          for (const match of matches) {
            classId[match] = validIndices[i];
            assigned[match] = 1;
          }
          if (matches.length > bestSize) {
            bestSize = matches.length;
            bestLocal = i;
            bestRep = validIndices[i];
          }
        }

        const validCount = validIndices.length;
        signFlipRatioValue = bestSize / validCount;
        const countPass = params.min_match_groups === 0 || bestSize > params.min_match_groups;
        const ratioPass = params.same_pattern_ratio === 0 || signFlipRatioValue > params.same_pattern_ratio;
        dominantOk = countPass && ratioPass;
        if (dominantOk) {
          for (let j = 0; j < validIndices.length; j += 1) {
            if (classId[j] === bestRep) dominantGroups.push(validIndices[j]);
          }
          for (const g of dominantGroups) result.group_dominant[b][g] = 1;
        }
      }

      result.sign_flip_ratio[b] = signFlipRatioValue;
      result.flip_count[b] = bestSize;

      if (dominantGroups.length > 0) {
        for (const g of dominantGroups) {
          const start = dropCount + g * groupStride;
          const slope =
            (history[start + groupStride][b] - history[start][b]) / groupStride;
          const absSlope = Math.abs(slope);
          if (
            !Number.isFinite(result.max_used_abs_slope[b]) ||
            absSlope > result.max_used_abs_slope[b]
          ) {
            result.max_used_abs_slope[b] = absSlope;
          }
        }
      }

      if (!dominantOk || dominantGroups.length === 0) continue;

      const meanAmp = new Float64Array(positionCount);
      for (let p = 0; p < positionCount; p += 1) {
        let sum = 0;
        for (const g of dominantGroups) sum += ampMatrix[g][p][b];
        meanAmp[p] = sum / dominantGroups.length;
      }

      const validPositions = [];
      for (let p = 0; p < positionCount; p += 1) {
        if (meanAmp[p] > minGroupAmp) validPositions.push(p);
      }

      if (validPositions.length === 0) {
        if (params.max_group_amp_cv === 0) result.block_flags[b] = 1;
        continue;
      }

      let maxPosCv = 0;
      let meanAbsSum = 0;
      for (const p of validPositions) {
        const mean = meanAmp[p];
        let varianceSum = 0;
        for (const g of dominantGroups) {
          const delta = ampMatrix[g][p][b] - mean;
          varianceSum += delta * delta;
        }
        const std = Math.sqrt(varianceSum / dominantGroups.length);
        const cv = std / mean;
        if (cv > maxPosCv) maxPosCv = cv;
        meanAbsSum += mean;
      }

      result.mean_abs_diff[b] = meanAbsSum / validPositions.length;
      result.diff_cv[b] = maxPosCv;
      if (params.max_group_amp_cv === 0 || maxPosCv <= params.max_group_amp_cv) {
        result.block_flags[b] = 1;
      }
    }

    result.flicker_block_num = countOnes(result.block_flags);
    computeWindowDiagnostics(result, history, params.min_frames);
    return result;
  }

  function sameSign(signMatrix, g1, g2, positionCount, block) {
    for (let p = 0; p < positionCount; p += 1) {
      if (signMatrix[g1][p][block] !== signMatrix[g2][p][block]) return false;
    }
    return true;
  }

  function computeWindowDiagnostics(result, history, windowSize) {
    const blockCount = result.block_flags.length;
    if (history.length < windowSize) return;
    const window = history.slice(history.length - windowSize);
    for (let b = 0; b < blockCount; b += 1) {
      let minValue = Infinity;
      let maxValue = -Infinity;
      for (const frame of window) {
        if (frame[b] < minValue) minValue = frame[b];
        if (frame[b] > maxValue) maxValue = frame[b];
      }
      result.avg_delta_diff[b] = maxValue - minValue;
      if (minValue > 0) {
        result.avg_ratio_diff[b] = maxValue / minValue;
      } else if (maxValue === 0) {
        result.avg_ratio_diff[b] = NaN;
      } else {
        result.avg_ratio_diff[b] = Infinity;
      }
    }
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
      amp_pass_count: new Uint8Array(blockCount),
      valid_group_count: new Uint8Array(blockCount),
      flip_count: new Uint32Array(blockCount),
      max_abs_slope: new Float64Array(blockCount).fill(NaN),
      max_used_abs_slope: new Float64Array(blockCount).fill(NaN),
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

  function countOnes(array) {
    let count = 0;
    for (const value of array) if (value) count += 1;
    return count;
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

    lines.push(`有效组 ${detect.valid_group_count[blockIndex]} / ${detect.group_count}`);
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
    const groupAmpPass = detect.group_amp_pass[blockIndex] || [];
    const groupSlope = detect.group_slope[blockIndex] || [];
    const groupValid = detect.group_valid[blockIndex] || [];
    const groupDominant = detect.group_dominant[blockIndex] || [];
    const groupSign = detect.group_sign[blockIndex] || [];
    for (let g = 0; g < groups.length; g += 1) {
      const group = groups[g];
      const ampPass = groupAmpPass[g] ?? 0;
      const slope = groupSlope[g];
      const slopeText = Number.isFinite(slope)
        ? formatHistoryNumber(slope)
        : "-";
      const status = groupValid[g] ? "有效" : "无效";
      const dominant = groupDominant[g] ? "·主导" : "";
      const sign = groupSign[g] || "-";
      lines.push(
        `G${g} 帧${group.start_frame}-${group.end_frame} ` +
        `amp${ampPass}/${detect.position_count} ` +
        `slope${slopeText}/${formatHistoryNumber(detect.slope_max)} ` +
        `${status}${dominant} sign${sign}`,
      );
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
      const response = await fetch("/api/options?fps=30");
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
