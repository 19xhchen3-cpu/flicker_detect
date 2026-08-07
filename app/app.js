(() => {
  "use strict";

  const SIGN_ZERO_EPSILON = 1e-4;

  const fileInput = document.getElementById("videoInput");
  const loadButton = document.getElementById("loadButton");
  const startButton = document.getElementById("startButton");
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
    drawChart();
  });

  startButton.addEventListener("click", startConversion);

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

  sourceVideo.addEventListener("loadedmetadata", () => {
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  document.querySelectorAll("[data-param]").forEach((input) => {
    input.addEventListener("input", scheduleDetection);
  });

  detectionFrequencyInput.addEventListener("input", () => {
    if (!metadata) return;
    const fpsMode = metadata.fps_mode || 30;
    const groupSize = groupSizeFor(fpsMode, Number(detectionFrequencyInput.value));
    if (groupSize) {
      document.getElementById("groupSizeInput").value = String(groupSize);
    }
    scheduleDetection();
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
    setStatus("解析中...", "");
    drawChart();

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
      applyDetectorParams(payload.detector_params || {});
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
      y_max: numberOr(params.y_max, 16383),
    };
    Object.entries(values).forEach(([key, value]) => {
      const input = document.querySelector(`[data-param="${key}"]`);
      if (input) input.value = String(value);
    });
    if (metadata) {
      fpsInput.value = String(metadata.fps_mode || Math.round(metadata.fps));
    }
  }

  function readParams() {
    const params = {};
    document.querySelectorAll("[data-param]").forEach((input) => {
      params[input.dataset.param] = Number(input.value);
    });
    params.group_size = Math.max(2, Math.round(params.group_size || 4));
    params.min_frames = Math.max(1, Math.round(params.min_frames || 30));
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
    const results = [];

    for (let i = 0; i < frameData.length; i += 1) {
      const frame = frameData[i];
      if (!frame || !frame.blocks) {
        results.push(emptyResult(blockCount));
        continue;
      }
      history.push(Float64Array.from(frame.blocks));
      if (history.length > params.min_frames) history.shift();
      if (history.length >= params.min_frames) {
        results[i] = detectWindow(history, params, blockCount);
      } else {
        results[i] = emptyResult(blockCount);
      }
    }

    detectionResults = results;
    currentDetect = results[currentFrameId] || null;
    updateDetectionStats(currentDetect);
    drawChart();
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
    setStatus(
      `检测完成，共 ${frameData.length} 帧`,
      stats.textContent,
    );
  }

  function detectWindow(history, params, blockCount) {
    const result = emptyResult(blockCount);
    const groupSize = Math.max(2, Math.round(params.group_size));
    const groupStride = groupSize - 1;
    const positionCount = groupStride;
    const validCount = history.length;
    const groupCount = validCount > 0 ? Math.floor((validCount - 1) / groupStride) : 0;
    if (groupCount <= 0) return result;

    const usedFrames = groupSize + (groupCount - 1) * groupStride;
    const dropCount = validCount - usedFrames;
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

    const minGroupAmp = params.y_max * params.min_group_amp_ratio;
    const ampGateEnabled = params.min_amp_pos_ratio > 0;
    const kValue = ampGateEnabled ? Math.ceil(positionCount * params.min_amp_pos_ratio) : 0;
    const ampGroupOk = Array.from({ length: groupCount }, () => new Uint8Array(blockCount));

    if (ampGateEnabled) {
      for (let g = 0; g < groupCount; g += 1) {
        for (let b = 0; b < blockCount; b += 1) {
          let passCount = 0;
          for (let p = 0; p < positionCount; p += 1) {
            if (ampMatrix[g][p][b] > minGroupAmp) passCount += 1;
          }
          ampGroupOk[g][b] = passCount >= kValue ? 1 : 0;
        }
      }
    } else {
      for (let g = 0; g < groupCount; g += 1) ampGroupOk[g].fill(1);
    }

    const slopeGroupOk = Array.from({ length: groupCount }, () => new Uint8Array(blockCount));
    if (params.slope_max > 0) {
      for (let b = 0; b < blockCount; b += 1) {
        for (let g = 0; g < groupCount; g += 1) {
          if (!ampGroupOk[g][b]) continue;
          const start = dropCount + g * groupStride;
          const slope = (history[start + groupStride][b] - history[start][b]) / groupStride;
          slopeGroupOk[g][b] = Math.abs(slope) <= params.slope_max ? 1 : 0;
        }
      }
    } else {
      for (let g = 0; g < groupCount; g += 1) slopeGroupOk[g].fill(1);
    }

    for (let b = 0; b < blockCount; b += 1) {
      const validIndices = [];
      for (let g = 0; g < groupCount; g += 1) {
        if (slopeGroupOk[g][b]) validIndices.push(g);
      }

      let dominantOk = false;
      let dominantGroups = [];
      let signFlipRatioValue = 0;

      if (validIndices.length > 0) {
        const classId = new Int32Array(validIndices.length).fill(-1);
        const assigned = new Uint8Array(validIndices.length);
        let bestLocal = -1;
        let bestRep = -1;
        let bestSize = 0;

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
        }
      }

      result.sign_flip_ratio[b] = signFlipRatioValue;

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
    };
  }

  function countOnes(array) {
    let count = 0;
    for (const value of array) if (value) count += 1;
    return count;
  }

  function groupSizeFor(fps, driveFrequency) {
    if (!fps || !driveFrequency) return null;
    const intensity = 2 * driveFrequency;
    const aliasCount = Math.round(intensity / fps);
    const apparentFrequency = Math.abs(intensity - aliasCount * fps);
    if (apparentFrequency === 0) return null;
    const periodFrames = fps / apparentFrequency;
    const rounded = Math.round(periodFrames);
    if (Math.abs(periodFrames - rounded) > 1e-9) return null;
    return rounded + 1;
  }

  function numberOr(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function drawCurrentFrame(frameId) {
    if (!metadata || !frameData[frameId]) return;
    currentFrameId = frameId;
    currentDetect = detectionResults[frameId] || null;
    updateDetectionStats(currentDetect);

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
})();
