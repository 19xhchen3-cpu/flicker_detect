(() => {
  "use strict";

  // 仿真视频生成参数定义：range 用滑块+数值框，select 用下拉。
  const PARAM_DEFS = [
    { key: "flicker_frequency", label: "驱动频率 fd", unit: "Hz", type: "range", min: 1, max: 500, step: 1, default: 50, decimals: 1 },
    { key: "flicker_phase", label: "AC 初相", unit: "rad", type: "range", min: 0, max: 6.2832, step: 0.05, default: 0, decimals: 2 },
    { key: "flicker_amplitude", label: "频闪强度", unit: "", type: "range", min: 0, max: 255, step: 1, default: 100, decimals: 1 },
    { key: "flicker_dc", label: "光源 DC 提升", unit: "", type: "range", min: 0, max: 255, step: 1, default: 64, decimals: 1 },
    { key: "target_radius", label: "目标半径", unit: "px", type: "range", min: 1, max: 500, step: 1, default: 40, decimals: 0 },
    { key: "trajectory_type", label: "运动轨迹", unit: "", type: "select", options: [["static", "静止"], ["line", "直线"], ["sine", "正弦"]], default: "line" },
    { key: "velocity_x", label: "速度 x", unit: "px/帧", type: "range", min: -5, max: 5, step: 0.05, default: 0.6, decimals: 2 },
    { key: "velocity_y", label: "速度 y", unit: "px/帧", type: "range", min: -5, max: 5, step: 0.05, default: 0, decimals: 2 },
    { key: "sine_amplitude", label: "摆动幅度", unit: "px", type: "range", min: 0, max: 500, step: 1, default: 30, decimals: 1 },
    { key: "sine_frequency", label: "摆动频率", unit: "Hz", type: "range", min: 0, max: 5, step: 0.05, default: 0.2, decimals: 2 },
    { key: "waveform", label: "频闪波形", unit: "", type: "select", options: [["rectified_sin", "整流正弦"], ["square", "占空比方波"]], default: "rectified_sin" },
    { key: "duty_cycle", label: "占空比", unit: "", type: "range", min: 0.05, max: 1, step: 0.01, default: 0.5, decimals: 2 },
    { key: "frames", label: "输出帧数", unit: "帧", type: "range", min: 0, max: 10000, step: 1, default: 0, decimals: 0, zeroIsEmpty: true },
    { key: "cols", label: "分块列数", unit: "", type: "range", min: 1, max: 200, step: 1, default: 16, decimals: 0 },
    { key: "rows", label: "分块行数", unit: "", type: "range", min: 1, max: 200, step: 1, default: 12, decimals: 0 },
  ];

  const genVideoInput = document.getElementById("genVideoInput");
  const genLoadButton = document.getElementById("genLoadButton");
  const genGenerateButton = document.getElementById("genGenerateButton");
  const genFileName = document.getElementById("genFileName");
  const genResetButton = document.getElementById("genResetButton");
  const genParamRows = document.getElementById("genParamRows");
  const genVideo = document.getElementById("genVideo");
  const genStatus = document.getElementById("genStatus");
  const genMeta = document.getElementById("genMeta");
  const genDownload = document.getElementById("genDownload");

  const state = {
    params: {},
    rows: new Map(),
    selectedFile: null,
    generating: false,
  };

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function formatNumber(value, decimals) {
    return String(Number(value.toFixed(decimals)));
  }

  function sliderToValue(sliderValue, min, max) {
    return min + (sliderValue / 10000) * (max - min);
  }

  function valueToSlider(value, min, max) {
    if (max === min) return 0;
    return Math.round(((clamp(value, min, max) - min) / (max - min)) * 10000);
  }

  function makeUnitSpan(unit) {
    const span = document.createElement("span");
    span.className = "gen-unit";
    span.textContent = unit;
    return span;
  }

  // 动态构建光源参数编辑面板。
  function buildPanel() {
    for (const def of PARAM_DEFS) {
      state.params[def.key] = def.default;
      const row = document.createElement("div");
      row.className = "gen-param-row";
      const label = document.createElement("label");
      label.textContent = def.label;

      if (def.type === "select") {
        const select = document.createElement("select");
        select.className = "gen-param-select";
        for (const [value, text] of def.options) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = text;
          select.appendChild(option);
        }
        select.value = def.default;
        select.addEventListener("change", () => {
          state.params[def.key] = select.value;
        });
        row.append(label, select, makeUnitSpan(def.unit));
        genParamRows.appendChild(row);
        state.rows.set(def.key, { def, select });
        continue;
      }

      const range = document.createElement("input");
      range.type = "range";
      range.className = "gen-param-range";
      range.min = "0";
      range.max = "10000";
      range.step = "1";
      range.value = String(valueToSlider(def.default, def.min, def.max));
      range.setAttribute("aria-label", `${def.label} slider`);

      const number = document.createElement("input");
      number.type = "number";
      number.className = "gen-param-number";
      number.min = String(def.min);
      number.max = String(def.max);
      number.step = String(def.step);
      number.value = formatNumber(def.default, def.decimals);
      number.setAttribute("aria-label", `${def.label} value`);

      range.addEventListener("input", () => {
        const value = sliderToValue(Number(range.value), def.min, def.max);
        state.params[def.key] = value;
        number.value = formatNumber(value, def.decimals);
      });

      number.addEventListener("input", () => {
        const raw = Number(number.value);
        const value = Number.isFinite(raw) ? clamp(raw, def.min, def.max) : def.min;
        number.value = formatNumber(value, def.decimals);
        range.value = String(valueToSlider(value, def.min, def.max));
        state.params[def.key] = value;
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

      row.append(label, range, number, makeUnitSpan(def.unit));
      genParamRows.appendChild(row);
      state.rows.set(def.key, { def, range, number });
    }
  }

  function setStatus(text) {
    genStatus.textContent = text;
  }

  function resetResult() {
    genVideo.removeAttribute("src");
    genVideo.load();
    genDownload.hidden = true;
    genMeta.textContent = "";
  }

  genLoadButton.addEventListener("click", () => {
    genVideoInput.click();
  });

  genVideoInput.addEventListener("change", () => {
    state.selectedFile = genVideoInput.files[0] || null;
    resetResult();
    if (state.selectedFile) {
      genFileName.textContent = state.selectedFile.name;
      genGenerateButton.disabled = false;
      setStatus(`已选择背景视频：${state.selectedFile.name}`);
    } else {
      genFileName.textContent = "未选择视频";
      genGenerateButton.disabled = true;
      setStatus("等待上传背景视频");
    }
  });

  genGenerateButton.addEventListener("click", generate);

  // 调用生成接口并展示结果。
  async function generate() {
    if (!state.selectedFile || state.generating) return;
    state.generating = true;
    genGenerateButton.disabled = true;
    setStatus("生成中...");

    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(state.params)) {
      if (value === null || value === undefined || value === "") continue;
      const def = state.rows.get(key)?.def;
      if (def && def.zeroIsEmpty && Number(value) === 0) continue;
      query.set(key, String(value));
    }

    try {
      const response = await fetch(`/api/v1/generate?${query.toString()}`, {
        method: "POST",
        body: state.selectedFile,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "生成失败");
      showResult(payload);
      setStatus(
        `生成完成：${payload.output.frames} 帧 / ${payload.output.width}×${payload.output.height} / ${payload.output.codec}`,
      );
    } catch (error) {
      setStatus(`错误：${error.message}`);
    } finally {
      state.generating = false;
      if (state.selectedFile) genGenerateButton.disabled = false;
    }
  }

  function showResult(payload) {
    const output = payload.output;
    const derived = payload.derived || {};
    genVideo.src = output.video_url;
    genVideo.load();
    genVideo.play().catch(() => {});
    genDownload.href = output.video_url;
    genDownload.download = "flicker_video.mp4";
    genDownload.hidden = false;

    const rows = [
      ["帧率", `${output.fps} fps`],
      ["画面", `${output.width} × ${output.height}`],
      ["帧数", String(output.frames)],
      ["编码", output.codec],
      ["光强频率 F", `${derived.light_freq} Hz`],
      ["视在频率 fa", `${derived.apparent_freq} Hz`],
      ["视在周期 P", String(derived.P)],
      ["group_size", String(derived.group_size)],
      ["min_group_amp", String(derived.min_group_amp)],
      ["k_value", String(derived.k_value)],
    ];
    genMeta.innerHTML = rows
      .map(([k, v]) => `<div><span class="k">${k}</span>：${v}</div>`)
      .join("");
  }

  genResetButton.addEventListener("click", () => {
    for (const [key, entry] of state.rows.entries()) {
      const def = entry.def;
      state.params[key] = def.default;
      if (def.type === "select") {
        entry.select.value = def.default;
      } else {
        entry.number.value = formatNumber(def.default, def.decimals);
        entry.range.value = String(valueToSlider(def.default, def.min, def.max));
      }
    }
  });

  // 切回生成页时若结果已生成则继续播放。
  window.redrawGenerate = () => {
    if (genVideo.src) genVideo.play().catch(() => {});
  };

  buildPanel();
})();
