(() => {
  // 使用严格模式避免隐式全局变量。
  "use strict";

  // 仿真参数定义，驱动左侧滑块、数值框和仿真请求。
  const PARAM_DEFS = [
    { key: "video_frame_rate", label: "视频帧率", unit: "fps", min: 1, max: 240, step: 1, default: 30, decimals: 2 },
    { key: "exposure_time", label: "曝光时间", unit: "s", min: 0.00001, max: 0.033, step: 0.0001, default: 0.001, decimals: 5, dynamicMax: true },
    { key: "simulation_time", label: "仿真时间", unit: "s", min: 0.1, max: 10, step: 0.1, default: 1, decimals: 2 },
    { key: "dc_amplitude", label: "DC 振幅", unit: "", min: 0, max: 50, step: 0.5, default: 5, decimals: 2 },
    { key: "dc_frequency", label: "DC 频率", unit: "Hz", min: 0, max: 20, step: 0.1, default: 0.25, decimals: 2 },
    { key: "dc_initial_phase", label: "DC 初相", unit: "rad", min: 0, max: 6.2832, step: 0.05, default: 0, decimals: 2 },
    { key: "flicker_amplitude", label: "频闪振幅", unit: "", min: 0, max: 50, step: 0.5, default: 1, decimals: 2 },
    { key: "flicker_frequency", label: "频闪频率", unit: "Hz", min: 1, max: 500, step: 1, default: 50, decimals: 1 },
    { key: "ac_initial_phase", label: "AC 初相", unit: "rad", min: 0, max: 6.2832, step: 0.05, default: 1, decimals: 2 },
  ];

  // 曲线定义：标题、颜色和对应结果字段。
  const PLOT_DEFS = [
    { key: "dc", title: "DC 光强", color: "#c2410c", series: "i_dc_disp" },
    { key: "ac", title: "AC 光强", color: "#15803d", series: "i_ac_disp" },
    { key: "total", title: "合成光源", color: "#1d4ed8", series: "i_total_disp" },
    { key: "frame", title: "帧强度", color: "#be123c", series: "frame_strength", markers: true },
  ];

  // 前端运行状态：参数、最近结果、防抖刷新和请求序号。
  const state = {
    params: {},
    lastResult: null,
    refreshTimer: null,
    fetching: false,
    pending: false,
    requestSeq: 0,
    stripeFrameIndex: null,
  };

  // 页面 DOM 引用。
  const statusText = document.getElementById("statusText");
  const metricsText = document.getElementById("metricsText");
  const resetButton = document.getElementById("resetButton");
  let stripeFrameSlider;
  let stripeFrameLabel;
  const paramRows = new Map();

  // 将数值限制在 [min, max] 区间内。
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  // 按指定小数位格式化数值。
  function formatNumber(value, decimals) {
    return String(Number(value.toFixed(decimals)));
  }

  // 滑块 0-10000 映射到参数数值区间。
  function sliderToValue(sliderValue, min, max) {
    return min + (sliderValue / 10000) * (max - min);
  }

  // 参数数值反向映射回滑块位置。
  function valueToSlider(value, min, max) {
    if (max === min) return 0;
    return Math.round(((clamp(value, min, max) - min) / (max - min)) * 10000);
  }

  // 根据 PARAM_DEFS 动态构建参数编辑面板。
  function buildPanel() {
    const container = document.getElementById("parameterRows");

    for (const def of PARAM_DEFS) {
      state.params[def.key] = def.default;

      const row = document.createElement("div");
      row.className = "param-row";

      const label = document.createElement("label");
      label.textContent = def.label;

      const range = document.createElement("input");
      range.type = "range";
      range.className = "param-range";
      range.min = "0";
      range.max = "10000";
      range.step = "1";
      range.value = String(valueToSlider(def.default, def.min, def.max));
      range.setAttribute("aria-label", `${def.label} slider`);

      const number = document.createElement("input");
      number.type = "number";
      number.className = "param-number";
      number.min = String(def.min);
      number.max = String(def.max);
      number.step = String(def.step);
      number.value = formatNumber(def.default, def.decimals);
      number.setAttribute("aria-label", `${def.label} value`);

      const unit = document.createElement("span");
      unit.className = "unit";
      unit.textContent = def.unit;

      row.append(label, range, number, unit);
      container.appendChild(row);

      range.addEventListener("input", () => {
        const min = Number(number.min);
        const max = Number(number.max);
        const value = sliderToValue(Number(range.value), min, max);
        state.params[def.key] = value;
        number.value = formatNumber(value, def.decimals);
        afterParamChanged(def.key);
      });

      number.addEventListener("input", () => {
        const min = Number(number.min);
        const max = Number(number.max);
        const raw = Number(number.value);
        const value = Number.isFinite(raw) ? clamp(raw, min, max) : min;
        number.value = formatNumber(value, def.decimals);
        range.value = String(valueToSlider(value, min, max));
        state.params[def.key] = value;
        afterParamChanged(def.key);
      });

      range.addEventListener(
        "wheel",
        (event) => {
          event.preventDefault();
          const step = Math.max(1, 10000 / 200);
          const delta = event.deltaY < 0 ? step : -step;
          const next = clamp(Number(range.value) + delta, 0, 10000);
          if (next !== Number(range.value)) {
            range.value = String(next);
            range.dispatchEvent(new Event("input"));
          }
        },
        { passive: false },
      );

      paramRows.set(def.key, { row, range, number, def });
    }

    const stripeRow = document.createElement("div");
    stripeRow.className = "param-row";

    const stripeLabel = document.createElement("label");
    stripeLabel.textContent = "条纹帧";

    const stripeRange = document.createElement("input");
    stripeRange.type = "range";
    stripeRange.className = "param-range";
    stripeRange.min = "0";
    stripeRange.max = "0";
    stripeRange.value = "0";
    stripeRange.step = "1";
    stripeRange.disabled = true;
    stripeRange.setAttribute("aria-label", "Stripe frame slider");

    const stripeValue = document.createElement("span");
    stripeValue.className = "stripe-frame-value";
    stripeValue.textContent = "Frame 0/0  0.000s";

    const stripeUnit = document.createElement("span");
    stripeUnit.className = "unit";
    stripeUnit.textContent = "";

    stripeRow.append(stripeLabel, stripeRange, stripeValue, stripeUnit);
    container.appendChild(stripeRow);
    stripeFrameSlider = stripeRange;
    stripeFrameLabel = stripeValue;
  }

  // 参数变化后的公共处理：联动曝光上限并触发防抖刷新。
  function afterParamChanged(key) {
    if (key === "video_frame_rate") updateExposureMaximum();
    scheduleRefresh();
  }

  // 曝光时间上限随帧率联动。
  function updateExposureMaximum() {
    const entry = paramRows.get("exposure_time");
    const fps = Number(state.params.video_frame_rate) || 1;
    const maxExposure = 1 / fps;
    const number = entry.number;
    const previousMax = Number(number.max);

    number.max = String(maxExposure);

    const desired = clamp(Number(state.params.exposure_time), Number(number.min), maxExposure);
    state.params.exposure_time = desired;
    number.value = formatNumber(desired, entry.def.decimals);
    entry.range.value = String(valueToSlider(desired, Number(number.min), maxExposure));

    if (Math.abs(previousMax - maxExposure) > 1e-12) {
      for (const other of paramRows.values()) {
        if (other.def.key !== "exposure_time") {
          const min = Number(other.number.min);
          const max = Number(other.number.max);
          other.range.value = String(valueToSlider(Number(other.number.value), min, max));
        }
      }
    }
  }

  // 防抖调度：正在请求时记录待刷新状态，否则延迟 200ms 后刷新。
  function scheduleRefresh() {
    if (state.fetching) {
      state.pending = true;
      return;
    }
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(refresh, 200);
  }

  // 调用仿真 API 并更新曲线和条纹图。
  async function refresh() {
    clearTimeout(state.refreshTimer);
    if (state.fetching) {
      state.pending = true;
      return;
    }

    state.fetching = true;
    const seq = ++state.requestSeq;
    setStatus("计算中...", "");

    try {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(state.params)) {
        query.set(key, String(value));
      }
      if (state.stripeFrameIndex !== null) {
        query.set("stripe_frame", String(state.stripeFrameIndex));
      }

      const response = await fetch(`/api/simulate?${query.toString()}`);
      const payload = await response.json();
      if (seq !== state.requestSeq) return;

      if (!response.ok) {
        throw new Error(payload.error || "Simulation failed");
      }

      state.lastResult = payload.result;
      syncStripeFrameControl(payload.result);
      drawAll(payload.result);
      setStatus(
        "就绪",
        `Frames: ${payload.result.num_frames} | Max/Min ratio: ${Number(payload.result.max_min_ratio).toFixed(4)}`,
      );
    } catch (error) {
      if (seq === state.requestSeq) {
        setStatus(`错误：${error.message}`, "");
      }
    } finally {
      state.fetching = false;
      if (state.pending) {
        state.pending = false;
        scheduleRefresh();
      }
    }
  }

  // 更新底部状态栏。
  function setStatus(status, metrics) {
    statusText.textContent = status;
    metricsText.textContent = metrics;
  }

  // 根据仿真结果同步条纹帧滑块范围与标签。
  function syncStripeFrameControl(result) {
    const maxFrame = Math.max(0, result.num_frames - 1);
    stripeFrameSlider.disabled = maxFrame <= 0;
    stripeFrameSlider.max = String(maxFrame);
    stripeFrameSlider.value = String(clamp(result.stripe_frame_index, 0, maxFrame));
    state.stripeFrameIndex = result.stripe_frame_index;

    const fps = Number(state.params.video_frame_rate) || 1;
    stripeFrameLabel.textContent =
      `Frame ${result.stripe_frame_index}/${maxFrame}  ` +
      `${(result.stripe_frame_index / fps).toFixed(3)}s`;
  }

  // 重置全部参数并重新仿真。
  resetButton.addEventListener("click", () => {
    for (const entry of paramRows.values()) {
      const { def, range, number } = entry;
      state.params[def.key] = def.default;
      number.value = formatNumber(def.default, def.decimals);
      range.value = String(valueToSlider(def.default, Number(number.min), Number(number.max)));
    }
    updateExposureMaximum();
    state.stripeFrameIndex = null;
    stripeFrameSlider.disabled = true;
    stripeFrameSlider.max = "0";
    stripeFrameSlider.value = "0";
    stripeFrameLabel.textContent = "Frame 0/0  0.000s";
    refresh();
  });

  // 按设备像素比初始化 Canvas，保证高分屏清晰。
  function prepareCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return null;

    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  // 根据数值大小选择适合的坐标刻度格式。
  function formatTick(value) {
    const abs = Math.abs(value);
    if (abs >= 1000) return value.toFixed(0);
    if (abs >= 10) return value.toFixed(1);
    if (abs >= 1) return value.toFixed(2);
    return value.toFixed(4);
  }

  // 绘制单条曲线。
  function drawSeries(canvas, data, options) {
    const ctx = prepareCanvas(canvas);
    if (!ctx) return;

    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const margin = { left: 68, right: 24, top: 38, bottom: 38 };
    const area = {
      x: margin.left,
      y: margin.top,
      w: Math.max(1, width - margin.left - margin.right),
      h: Math.max(1, height - margin.top - margin.bottom),
    };

    const title = options.suffix ? `${options.title}  ${options.suffix}` : options.title;
    ctx.fillStyle = "#1f2937";
    ctx.font = "600 12px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, area.x, area.y - 12);

    if (!data || data.length === 0) {
      ctx.fillStyle = "#6b7280";
      ctx.font = "13px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data", width / 2, height / 2);
      return;
    }

    const values = Array.from(data);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min > 1e-12 ? max - min : 1;
    const denom = Math.max(1, values.length - 1);
    const xMax = options.xMax > 0 ? options.xMax : denom;

    const xAt = (i) => area.x + (i / denom) * area.w;
    const yAt = (value) => area.y + area.h - ((value - min) / range) * area.h;

    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(area.x, area.y, area.w, area.h);

    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let tick = 0; tick <= 4; tick += 1) {
      const yy = area.y + (area.h * tick) / 4;
      ctx.beginPath();
      ctx.moveTo(area.x, yy);
      ctx.lineTo(area.x + area.w, yy);
      ctx.stroke();
    }

    ctx.strokeStyle = "#6b7280";
    ctx.beginPath();
    ctx.moveTo(area.x, area.y);
    ctx.lineTo(area.x, area.y + area.h);
    ctx.lineTo(area.x + area.w, area.y + area.h);
    ctx.stroke();

    ctx.beginPath();
    for (let i = 0; i < values.length; i += 1) {
      const x = xAt(i);
      const y = yAt(values[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = options.color;
    ctx.lineWidth = 1.8;
    ctx.stroke();

    if (options.markers) {
      ctx.fillStyle = options.color;
      for (let i = 0; i < values.length; i += 1) {
        ctx.beginPath();
        ctx.arc(xAt(i), yAt(values[i]), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    ctx.fillStyle = "#6b7280";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = min + (range * tick) / 4;
      const yy = yAt(min + (range * tick) / 4);
      ctx.fillText(formatTick(value), area.x - 8, yy + 3);
      ctx.beginPath();
      ctx.moveTo(area.x - 4, yy);
      ctx.lineTo(area.x, yy);
      ctx.stroke();
    }

    ctx.textAlign = "left";
    ctx.fillText("0", area.x - 6, area.y + area.h + 18);
    ctx.textAlign = "right";
    ctx.fillText(formatTick(xMax), area.x + area.w, area.y + area.h + 18);
  }

  // 绘制单帧卷帘快门条纹图。
  function drawStripe(canvas, result) {
    const ctx = prepareCanvas(canvas);
    if (!ctx) return;

    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const frameLabel = result && result.row_count
      ? `单帧卷帘条纹  frame=${result.stripe_frame_index}`
      : "单帧卷帘条纹";
    ctx.fillStyle = "#1f2937";
    ctx.font = "600 12px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(frameLabel, 16, 24);

    const rows = result ? result.stripe_rows : [];
    if (!rows || rows.length === 0) {
      ctx.fillStyle = "#6b7280";
      ctx.font = "13px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data", width / 2, height / 2);
      return;
    }

    const margin = { left: 16, right: 16, top: 38, bottom: 20 };
    const area = {
      x: margin.left,
      y: margin.top,
      w: Math.max(1, width - margin.left - margin.right),
      h: Math.max(1, height - margin.top - margin.bottom),
    };

    const min = Math.min(...rows);
    const max = Math.max(...rows);
    const range = max - min > 1e-12 ? max - min : 1;

    const offscreen = document.createElement("canvas");
    offscreen.width = 1;
    offscreen.height = rows.length;
    const offCtx = offscreen.getContext("2d");
    const image = offCtx.createImageData(1, rows.length);

    for (let y = 0; y < rows.length; y += 1) {
      const normalized = (rows[y] - min) / range;
      const value = Math.round(clamp(normalized, 0, 1) * 255);
      const offset = y * 4;
      image.data[offset] = value;
      image.data[offset + 1] = value;
      image.data[offset + 2] = value;
      image.data[offset + 3] = 255;
    }

    offCtx.putImageData(image, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(offscreen, area.x, area.y, area.w, area.h);
    ctx.strokeStyle = "#d8dee9";
    ctx.lineWidth = 1;
    ctx.strokeRect(area.x, area.y, area.w, area.h);
  }

  // 统一绘制全部曲线和条纹图。
  function drawAll(result) {
    window.requestAnimationFrame(() => {
      for (const def of PLOT_DEFS) {
        const canvas = document.querySelector(`canvas[data-series="${def.key}"]`);
        const data = result ? result[def.series] : [];
        const xMax = def.key === "frame"
          ? (result ? result.num_frames - 1 : 0)
          : (result && result.time_disp.length ? result.time_disp[result.time_disp.length - 1] : 0);
        const suffix = def.key === "frame" && result && result.max_min_ratio > 0
          ? `ratio=${Number(result.max_min_ratio).toFixed(2)}`
          : "";
        drawSeries(canvas, data, {
          title: def.title,
          color: def.color,
          markers: Boolean(def.markers),
          xMax,
          suffix,
        });
      }
      drawStripe(document.querySelector('canvas[data-series="stripe"]'), result);
    });
  }

  // 绑定条纹帧滑块的输入和滚轮事件。
  function wireStripeFrameControl() {
    stripeFrameSlider.addEventListener("input", () => {
      const frameIndex = Number(stripeFrameSlider.value);
      state.stripeFrameIndex = frameIndex;
      const maxFrame = Number(stripeFrameSlider.max);
      const fps = Number(state.params.video_frame_rate) || 1;
      stripeFrameLabel.textContent =
        `Frame ${frameIndex}/${maxFrame}  ${(frameIndex / fps).toFixed(3)}s`;
      scheduleRefresh();
    });

    stripeFrameSlider.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const min = Number(stripeFrameSlider.min) || 0;
        const max = Number(stripeFrameSlider.max) || 0;
        const step = Math.max(1, Math.round((max - min) / 200));
        const delta = event.deltaY < 0 ? step : -step;
        const next = clamp(Number(stripeFrameSlider.value) + delta, min, max);
        if (next !== Number(stripeFrameSlider.value)) {
          stripeFrameSlider.value = String(next);
          stripeFrameSlider.dispatchEvent(new Event("input"));
        }
      },
      { passive: false },
    );
  }

  // 初始化页面并触发首次仿真。
  buildPanel();
  wireStripeFrameControl();
  updateExposureMaximum();
  drawAll(null);
  refresh();

  if ("ResizeObserver" in window) {
    new ResizeObserver(() => {
      drawAll(state.lastResult);
    }).observe(document.getElementById("plotGrid"));
  } else {
    window.addEventListener("resize", () => {
      drawAll(state.lastResult);
    });
  }

  window.redrawSimulation = () => {
    drawAll(state.lastResult);
  };
})();
