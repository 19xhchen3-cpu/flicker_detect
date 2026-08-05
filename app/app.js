(() => {
  "use strict";

  // 页面 DOM 引用。
  const fileInput = document.getElementById("videoInput");
  const loadButton = document.getElementById("loadButton");
  const startButton = document.getElementById("startButton");
  const fileName = document.getElementById("fileName");
  const gammaInput = document.getElementById("gammaInput");
  const colsInput = document.getElementById("colsInput");
  const rowsInput = document.getElementById("rowsInput");
  const sourceVideo = document.getElementById("sourceVideo");
  const frameScrubber = document.getElementById("frameScrubber");
  const scrubberLabel = document.getElementById("scrubberLabel");
  const opacityInput = document.getElementById("opacityInput");
  const opacityLabel = document.getElementById("opacityLabel");
  const progressFill = document.getElementById("progressFill");
  const frameStatus = document.getElementById("frameStatus");
  const stats = document.getElementById("stats");
  const previewCanvas = document.getElementById("previewCanvas");

  let selectedFile = null;
  let metadata = null;
  let converting = false;
  let frameData = [];
  let sourceObjectUrl = null;
  let currentFrameId = null;

  // 点击“加载视频”时打开文件选择器。
  loadButton.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    selectedFile = fileInput.files[0] || null;
    metadata = null;
    frameData = [];
    currentFrameId = null;
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
  });

  startButton.addEventListener("click", startConversion);
  frameScrubber.addEventListener("input", () => {
    const frameId = Number(frameScrubber.value);
    scrubberLabel.textContent = `Frame ${frameId}/${frameScrubber.max}`;
    drawCurrentFrame(frameId);
  });

  opacityInput.addEventListener("input", () => {
    opacityLabel.textContent = `${opacityInput.value}%`;
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  sourceVideo.addEventListener("loadedmetadata", () => {
    if (currentFrameId !== null) drawCurrentFrame(currentFrameId);
  });

  // 上传视频并逐帧读取转换结果。
  async function startConversion() {
    if (!selectedFile || converting) return;

    converting = true;
    startButton.disabled = true;
    frameData = [];
    currentFrameId = null;
    resetProgress();
    resetScrubber();
    setStatus("解析中...", "");

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
        throw new Error(error.error || "转换失败");
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
      startButton.disabled = false;
    }
  }

  // 处理 NDJSON 流中的元数据、帧进度和完成信息。
  function handleLine(payload) {
    if (payload.type === "meta") {
      metadata = payload;
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
      setStatus(`完成，共处理 ${payload.processed} 帧`, stats.textContent);
      const lastFrame = Math.max(0, payload.processed - 1);
      frameScrubber.max = String(lastFrame);
      frameScrubber.value = String(lastFrame);
      frameScrubber.disabled = payload.processed <= 1;
      scrubberLabel.textContent = `Frame ${lastFrame}/${lastFrame}`;
      if (lastFrame >= 0) drawCurrentFrame(lastFrame);
    }
  }

  function updateProgress(payload) {
    const percent = Number(payload.percent) || 0;
    const total = metadata && metadata.frame_count ? metadata.frame_count : 0;
    progressFill.style.width = `${Math.min(100, Math.max(0, percent))}%`;

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

  // 把分块亮度画成灰度缩略图。
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

  // 用本地原视频定位到指定帧，并在灰度视频上叠加转换后的分块结果。
  function drawCurrentFrame(frameId) {
    if (!metadata || !frameData[frameId]) return;
    currentFrameId = frameId;

    if (frameData[frameId].original) {
      paintOverlay(frameId);
      return;
    }

    if (!sourceVideo.videoWidth || sourceVideo.readyState < 1) {
      drawPreview(frameData[frameId].blocks);
      return;
    }

    const targetTime = Number(frameData[frameId].timestamp) || frameId / metadata.fps;
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

  // 把原视频帧转成灰度底图，再按透明度叠加分块亮度图。
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

    const blockCanvas = document.createElement("canvas");
    blockCanvas.width = metadata.cols;
    blockCanvas.height = metadata.rows;
    const blockCtx = blockCanvas.getContext("2d");
    const image = blockCtx.createImageData(metadata.cols, metadata.rows);
    const yMax = metadata.y_max || 16383;

    for (let i = 0; i < frame.blocks.length; i += 1) {
      const normalized = Math.min(1, Math.max(0, Number(frame.blocks[i]) / yMax));
      const value = Math.round(normalized * 255);
      const offset = i * 4;
      image.data[offset] = value;
      image.data[offset + 1] = value;
      image.data[offset + 2] = value;
      image.data[offset + 3] = 255;
    }
    blockCtx.putImageData(image, 0, 0);

    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = Number(opacityInput.value) / 100;
    ctx.drawImage(blockCanvas, 0, 0, width, height);
    ctx.globalAlpha = 1;
    ctx.imageSmoothingEnabled = true;
    drawBlockGrid(ctx, width, height);
  }

  // 使用服务端返回的灰度原图作为底图，再叠加分块结果。
  function paintWithOriginal(frameId) {
    const frame = frameData[frameId];
    if (!frame || !frame.original || !metadata) return;

    const image = new Image();
    image.onload = () => {
      if (currentFrameId !== frameId) return;

      const width = 640;
      const height = Math.max(
        1,
        Math.round(width * metadata.height / metadata.width),
      );
      const canvas = previewCanvas;
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, width, height);
      ctx.drawImage(image, 0, 0, width, height);

      const blockCanvas = document.createElement("canvas");
      blockCanvas.width = metadata.cols;
      blockCanvas.height = metadata.rows;
      const blockCtx = blockCanvas.getContext("2d");
      const blockImage = blockCtx.createImageData(metadata.cols, metadata.rows);
      const yMax = metadata.y_max || 16383;

      for (let i = 0; i < frame.blocks.length; i += 1) {
        const normalized = Math.min(
          1,
          Math.max(0, Number(frame.blocks[i]) / yMax),
        );
        const value = Math.round(normalized * 255);
        const offset = i * 4;
        blockImage.data[offset] = value;
        blockImage.data[offset + 1] = value;
        blockImage.data[offset + 2] = value;
        blockImage.data[offset + 3] = 255;
      }
      blockCtx.putImageData(blockImage, 0, 0);

      ctx.imageSmoothingEnabled = false;
      ctx.globalAlpha = Number(opacityInput.value) / 100;
      ctx.drawImage(blockCanvas, 0, 0, width, height);
      ctx.globalAlpha = 1;
      ctx.imageSmoothingEnabled = true;
      drawBlockGrid(ctx, width, height);
    };
    image.src = `data:image/jpeg;base64,${frame.original}`;
  }

  // 在分块结果上绘制网格，确保 12x16 分块边界清晰可见。
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

  // 手动把原视频当前帧转为灰度图，避免依赖浏览器 canvas filter。
  function drawGrayscaleVideo(ctx, width, height) {
    const offscreen = document.createElement("canvas");
    offscreen.width = width;
    offscreen.height = height;
    const offCtx = offscreen.getContext("2d");
    offCtx.drawImage(sourceVideo, 0, 0, width, height);

    const imageData = offCtx.getImageData(0, 0, width, height);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      const gray =
        0.299 * data[i] +
        0.587 * data[i + 1] +
        0.114 * data[i + 2];
      data[i] = gray;
      data[i + 1] = gray;
      data[i + 2] = gray;
    }
    offCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(offscreen, 0, 0);
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
