# 仿真视频生成功能设计（移动目标频闪）

> 状态：**设计草案，尚未实施**。本文定义"仿真视频生成功能"的需求、可调节
> 参数、场景渲染模型、模块接口以及与检测管线的对接方案，供后续排期实现时
> 直接取用。
> 相关文档：[PLAN.md](./PLAN.md)、[moudle1_moving_target_design.md](./moudle1_moving_target_design.md)、
> [pure_logic_detector_design.md](./pure_logic_detector_design.md)。

## 1. 目的与背景

- 对应 [PLAN.md](./PLAN.md) 待办："用仿真引擎生成已知频闪数据，对检测模块做
  自动化验证"，避免检测正确性只依赖人工目测。
- 现有 `flicker_app/engine.py` 的 `FlickerEngine` 只输出光强曲线、帧强度和
  卷帘条纹数据，**不生成视频文件**；现有 `app/simulate.js` 只是前端曲线仿真。
  因此需要新增"生成真实视频文件"的能力，使生成的视频能走完整
  `视频 → 预处理 → 分块 → moudle1 检测` 管线做端到端验证。
- 本次范围聚焦**移动目标频闪**场景：画面中一个频闪光源沿轨迹移动。这正好是
  [moudle1_moving_target_design.md](./moudle1_moving_target_design.md) 第 2.2 节
  记录的检出回归场景（当前单相位 + 跳帧实现只对恰好落在相位边界的突发检出），
  生成的视频可为该回归提供可复现的验证素材，也为后续路线 A（P 相位旋转）的
  对照提供输入。

## 2. 需求

- 生成真实 MP4 视频文件（OpenCV `VideoWriter`），编码后能被
  `flicker_detect/preprocess.py` 正常读取。
- 覆盖移动目标频闪：画面中一个可配置面积、可配置光强波形的频闪亮斑沿可配置
  轨迹移动，背景为导入的真实视频。
- **背景视频叠加**：给定一段视频作为背景，在其上叠加一个强度可调的伪频闪
  光源（可静止或沿轨迹移动），输出新的 MP4；用于在接近真实的画面（真实
  纹理/运动/曝光）上验证频闪检出。**本底一律使用导入的视频，不使用固定
  亮度背景**。
- 全部关键量可调节（见第 3 节），并提供一份与视频同名的 JSON 参考标签文件
  （参数、真实轨迹、逐帧目标亮度、逐块重叠度真值），供自动化断言使用。
- 输出帧率与画面大小**均不可配置**，一律从背景视频读取（`CAP_PROP_FPS` 与
  分辨率），不缩放画布；光强与检测参数按背景视频实际帧率对齐：背景为 30fps
  时默认 50 Hz 驱动频率（光强 100 Hz、视在 10 Hz、`group_size=4`、`P=3`），
  保证生成视频可被现有检测配置覆盖。
- **驱动频率手动可调**：`flicker_frequency`（fd）由用户设定，按 PLAN.md 的
  `fd → F → fa → P` 关系式构建虚拟频闪光源（见 §4.1）；`(fps, fd)` 组合不可
  检测（视在周期 `P` 非整数）时 `validate()` 拒绝。

## 3. 可调节参数

新增 `SimVideoParams`（dataclass，风格与 `flicker_app/engine.py` 的
`FlickerParams` 一致），字段如下：

| 参数 | 含义 | 默认 | 说明 |
| --- | --- | --- | --- |
| `frames` | 视频帧数 | 背景视频帧数 | 输出帧数上限（截断用）；须 ≥ 30（见 §6 就绪） |
| `background_video` | 背景视频路径 | 必填 | 以该视频为背景叠加光源；本底不使用固定亮度背景 |
| `flicker_frequency` | 驱动频率 fd（Hz） | 50.0 | **手动可调**；光强按 `abs(sin(2π·fd·t))` 以 2×fd 频闪 |
| `flicker_phase` | AC 初相（rad） | 0.0 | 波形相位，可构造相位边界内/外的突发 |
| `flicker_amplitude` | 频闪强度（灰度幅值） | 100.0 | 叠加后裁切兜底，无合成模式限制 |
| `flicker_dc` | 光源 DC 提升量 | 64 | 光源提升 `boost(t) = flicker_dc + amplitude × \|sin(...)\|` |
| `target_radius` | 频闪面积（目标半径，像素） | 40 | 圆形亮斑半径；面积由半径派生 |
| `trajectory_type` | 运动轨迹类型 | `"line"` | `static` / `line` / `sine` |
| `start_x` / `start_y` | 轨迹起点（像素） | 画面中心偏左 | 目标初始中心 |
| `velocity_x` / `velocity_y` | 轨迹速度（像素/帧） | 0.6 / 0.0 | `line` 型匀速移动 |
| `sine_amplitude` / `sine_frequency` | 正弦摆动幅度/频率 | 30 / 0.2 | `sine` 型横向摆动 |
| `waveform` | 频闪规律 | `"rectified_sin"` | `rectified_sin`：`abs(sin)` 整流波形；`square`：占空比方波 |
| `duty_cycle` | 频闪强度占空比 | 0.5 | 仅 `square` 生效：一个光强周期内 ON 的比例，范围 (0,1] |
| `cols` / `rows` | 参考块网格 | 16 / 12 | 与检测参数表一致；仅用于参考标签 |
| `output_path` | 输出 MP4 路径 | 调用方指定 | 同步写出同名 `.json` 参考文件 |

派生量（模块内计算，不开放配置）：

- **视频帧率 `fps`：从背景视频读取（`CAP_PROP_FPS`），不可配置**；
- **画面大小 `width` / `height`：从背景视频读取
  （`CAP_PROP_FRAME_WIDTH` / `CAP_PROP_FRAME_HEIGHT`），不可配置，不缩放画布**；
- **驱动频率关系（按 PLAN.md 频率定义）**：
  - 驱动频率 `fd = flicker_frequency`（手动可调）；
  - 光强频率 `F = 2 × fd`（整流波形基频，如 fd=50Hz → F=100Hz）；
  - 混叠折叠阶数 `k = round(F / fps)`；
  - 视在频率 `fa = |F − k × fps|`（逐帧采样后的折叠频率）；
  - 视在周期 `P = fps / fa`（须为整数，如 30fps/50Hz → fa=10Hz、P=3）；
  - 与检测参数表一致：`lookup_detector_parameters(fps, fd).group_size = P + 1`；
- 14 bit 换算系数 `k14 = y_max / 255 = 16383/255 ≈ 64.25`；
- 频闪面积占块数：`block_overlap > 0` 的块数（见 §4.5）。

**对照特例**：`flicker_amplitude=0`（目标恒定不闪）或 `target_radius=0`（无目标）
即天然生成"无频闪对照组"视频，无需额外分支代码。

## 4. 场景渲染模型

### 4.1 光强模型

光源提升量波形 `w(t) ∈ [0,1]` 由 `waveform` 决定（光强基频 `F = 2 × flicker_frequency`，
光强周期 `T_F = 1 / F`）：

- `rectified_sin`（默认，与 `FlickerEngine.calculate()` 一致）：
  `w(t) = |sin(2π * flicker_frequency * t + flicker_phase)|`；
- `square`：`w(t) = 1 if frac(2 * flicker_frequency * t + flicker_phase / (2π)) < duty_cycle
  else 0`，即在每个光强周期 `T_F` 内 ON 的比例为 `duty_cycle`（占空比可调）。

**驱动频率 → 视在频率/周期（按 PLAN.md 频率定义构建虚拟光源）**：

```text
fd = flicker_frequency      # 手动可调的驱动频率（如 50 Hz 市电）
F  = 2 × fd                 # 整流波形产生的光强频率（如 100 Hz）
k  = round(F / fps)         # 混叠折叠阶数
fa = |F − k × fps|          # 视在频率（逐帧采样后的折叠频率）
P  = fps / fa               # 视在周期（帧），须为整数才能被检测
```

以背景 30fps、fd=50Hz 为例：F=100Hz、k=round(100/30)=3、fa=|100−90|=10Hz、
P=30/10=3 帧，与检测参数表 `group_size=P+1=4` 完全一致。构建虚拟光源时，波形在
`F` 上生成、按背景 `fps` 帧中点采样，时间序列即呈现 `fa`/`P` 的视在模式。

统一 `boost(t) = flicker_dc + flicker_amplitude * w(t)`。v1 每帧取帧中点采样
`t = (frame_id + 0.5) / fps`（`fps` 为背景视频帧率），得到该帧光源提升量；曝光
窗口积分与卷帘快门条纹作为可选细化（见 §10 风险）。

以背景为 30fps 为例：静止目标块的时间序列是 100 Hz 光强的 30 fps 采样，视在
频率 10 Hz、周期恰好 3 帧（3 帧 = 100 ms = 整数个光强周期）：`rectified_sin`
样本形如 `[0.866·A, 0, 0.866·A]`；`square` 样本形如 `[1,0,1]` 或 `[1,1,0]`
（随相位与 `duty_cycle` 变化）。两者都是周期 3 的重复模式，与检测参数表对齐；
其他帧率按 `lookup_detector_parameters` 重新对齐。

### 4.2 背景（导入的视频）

本底一律使用导入的视频（`background_video`），不使用固定亮度背景。输出视频的
**帧率 `fps` 与画面大小 `width×height` 一律从背景视频读取（`CAP_PROP_FPS` 与
分辨率），均不可配置，不缩放画布**；帧数默认取背景视频帧数，`frames` 可作
上限截断。需要恒定背景的场景（如无频闪对照、光强一致性测试）由测试用一张
恒定灰度的视频文件充当背景即可。

### 4.3 运动轨迹计算

`trajectory_positions(params)` 返回逐帧目标中心 `[(cx, cy)]`：

- `static`：中心固定，等价 `velocity=0`；
- `line`：`center(f) = start + (velocity_x, velocity_y) × f`，匀速直线；
- `sine`：`center(f) = base + (sine_amplitude × sin(2π × sine_frequency × f/fps), 0)`。

轨迹越出画面边界时，v1 采用**边界裁剪**（目标部分出画面），并在参考标签标记
每帧目标是否完全在画面内，便于测试区分"目标完全在画面内"与"部分出画面"两种
情况。

### 4.4 亮度合成与写出

每帧渲染（8 位 BGR，`uint8`）：

1. 取背景：读取背景视频对应帧（原生分辨率 `width×height`，不缩放）；
2. 叠加目标：目标圆内像素（距中心 ≤ `target_radius`）按 §4.6 公式叠加该帧
   光源提升量（`uint8` 裁切）；
3. `cv2.VideoWriter` 按 `fps` 逐帧写入，编码 MP4（优先浏览器可解码的 H.264
   `avc1`，失败回退 `mp4v`/MJPG `.avi`，见 §10）。

### 4.5 逐块真值（与 preprocess 同一口径）

关键设计：参考标签的"目标覆盖块"**不做几何圆-块相交近似**，而是复用
preprocess 的分块口径精确计算：

- 对每帧渲染一张**二值目标掩码**（圆内 1、圆外 0，分辨率同画面）；
- 用与 `preprocess._block_average` 完全相同的 `cv2.resize(INTER_AREA, (cols, rows))`
  把掩码压成 `cols×rows`，得到每块重叠度 `overlap[b] ∈ [0,1]`；
- 由于 gamma=1.0 时查找表是线性映射，块亮度可精确重建：
  `expected_blocks_14[b] = bg_blocks_14[b] + overlap[b]·boost14(t)`
  其中 `bg_blocks_14` 来自对**原始背景视频**单独跑 preprocess 的结果，
  `boost14 = boost(t)·k14`（`k14 = y_max/255`）。

该口径让"光强一致性"测试可以做**精确相等**断言（误差仅浮点舍入），而不是
阈值近似；背景亮度由导入视频决定，不依赖固定灰度。

### 4.6 光源叠加模型

光源以**加法提升**方式叠加在背景视频上，强度可调：

```text
w(t) ∈ [0,1]   # waveform: rectified_sin | square(duty_cycle)，见 §4.1
boost(t) = flicker_dc + flicker_amplitude * w(t)
out(x, y, t) = clip(bg(x, y, t) + boost(t) * mask(x, y), 0, 255)
```

其中 `mask` 为目标圆二值掩码。`flicker_dc` 为恒定提升（光源始终可见），
`flicker_amplitude` 为频闪振荡幅度（强度旋钮）。背景未被覆盖的区域保持原视频
亮度；光源区域亮度按 `waveform`（`rectified_sin` / `square(duty_cycle)`）振荡，
视在频率与检测参数表对齐（§4.1）。

逐块真值见 §4.5，背景本身的运动/纹理由背景视频决定，不作为合成先验。

## 5. 模块设计

- 位置：新增 `flicker_detect/sim_video.py`（核心包内、纯函数 + CLI，便于被
  `tests/` 直接导入；不感知 HTTP）。
- 数据结构：`SimVideoParams`（dataclass，**不含 `fps`/`width`/`height` 字段**）
  + `validate()`。
- `validate()` 校验项：`frames≥1`（端到端测试要求 ≥30）、`flicker_frequency > 0`，
  **`(fps, fd)` 组合须可检测**（`lookup_detector_parameters(fps, fd)` 成功且
  `group_size > 0`，即视在周期 `P` 为整数），否则抛 `ValueError`；
  `flicker_dc + flicker_amplitude` 仅作软上限（叠加后裁切兜底）、
  `target_radius≥0`、`waveform ∈ {"rectified_sin", "square"}`、`square` 时
  `duty_cycle ∈ (0,1]`、轨迹参数合法（`line` 需速度有限、`sine` 需振幅≥0）；
  `background_video` 必填且须是 OpenCV 可读的现有文件；**`fps`/`width`/`height`
  从背景视频读取**，并校验 `cols ≤ width`、`rows ≤ height`（preprocess 要求）；
  `frames` 作输出上限截断（默认取背景视频帧数）。
- 函数：
  - `light_at(params, frame_id, fps) -> float`：帧中点光源提升量（8 bit 空间），
    波形由 `waveform`/`duty_cycle` 决定，`fps` 为背景视频帧率；
  - `trajectory_positions(params, fps) -> list[tuple[int, int]]`：逐帧目标中心，
    `fps` 为背景视频帧率；
  - `block_overlap(params, cx, cy) -> np.ndarray`：二值掩码 → 块重叠度
    `[cols×rows]`，与 preprocess 同一口径；
  - `open_background(params) -> (cv2.VideoCapture, dict)`：打开背景视频并解析
    **`fps`/`width`/`height`（均不可配置）**与帧数；
  - `render_frame(params, cx, cy, frame_id, background_frame) -> np.ndarray`：
    渲染单帧 BGR；`background_frame` 为背景视频帧（原生分辨率），必传；
  - `build_reference(params) -> dict`：参数 + 逐帧中心/提升量/在界 + 逐块最大
    重叠度 + 停留帧数统计（含 §4.5 真值重建所需量）；
  - `generate_video(params, output_path)`：探测编码器 → 打开背景视频 →
    逐帧渲染写出 MP4 → 写同名 `.json`（`build_reference` 结果）；
  - `main()`：CLI 参数解析（`argparse`，覆盖第 3 节全部参数）。
- 参考 JSON schema（`build_reference` 输出）：
  - `params`：全部输入参数原样记录；
  - `derived`：`fps/frames/width/height/cols/rows/y_max/k/light_freq/apparent_freq/P/
    min_group_amp/k_value`（`min_group_amp`、`k_value` 取自
    `lookup_detector_parameters` 换算）；
  - `frames`: `[{frame_id, cx, cy, in_bounds, light8, light14}]`（逐帧）；
  - `block_stats`：`max_overlap`（每块全程最大重叠度）、`stay_frames`（每块
    重叠度 > 0 的帧数）——用于解释移动目标检出/漏检。
- CLI 示例：

```text
# 帧率从背景视频读取，不可配置
python -m flicker_detect.sim_video --background-video real_scene.mp4 \
    --flicker_frequency 50 --flicker_amplitude 60 --flicker_dc 40 \
    --target_radius 60 --trajectory_type line --velocity_x 0.6 \
    --output videos/overlay_50hz.mp4
```

## 6. 与检测管线的对接

- 生成的 MP4 直接复用现有链路：

```text
MP4 → preprocess.inspect_video / iter_video_frames(cols=16, rows=12, gamma=1.0)
    → moudle1.detect_window_incremental(state, blocks, frame_id)
```

- **就绪帧数**：moudle1 需要 `G = (H-1)//P = 9` 组才出结论（H=30、P=3），即前
  27 帧返回 `None`。端到端测试断言时须跳过前 27 帧，或先跑足 30 帧再取结论。

- **阈值换算（与 detector_config 对齐后确认可检出）**：以下以背景视频为
  30fps 为例（`lookup_detector_parameters(30, 50)`）；其他帧率按背景实际 `fps`
  重新查参数后做同口径换算。
  - `y_max = 16383`、`min_group_amp = 16383×0.001 ≈ 16.4`（14 bit）；
  - `K = ceil(3×0.5) = 2`（P=3、`min_amp_pos_ratio=0.5`）；
  - 静止全覆盖块：样本 `[0.866·A, 0, 0.866·A]`，差分 `≈0.866·A·k14`；8 bit 振幅
    `A≥1` 时换算后仍远超 `min_group_amp`，K-of-N 通过；周期 3 闭合差为 0，
    `slope_max` 门通过；同模式重复 9 组，`min_match_groups=5`、`same_pattern_
    ratio=0.8` 通过 → **默认阈值即可检出**。

- 参考标签用于自动化断言，验证两类结论：
  1. **检出能力**：已知频闪频率（30fps/50Hz）视频中，目标覆盖块（`overlap` 高于
     阈值）应被检出，背景块不误检；
  2. **移动目标回归**（对应 moudle1_moving_target_design.md 2.2）：目标同一块
     停留 ≥ 6 组的慢速场景应检出；通过 `flicker_phase`/起点构造恰好落在相位
     边界（帧 27..30）与不落在相位边界（帧 26..29）的两种突发，记录当前单相位
     实现的检出差异（预期边界外漏检），作为路线 A 的对照基线。
- **补偿口径**：移动突发用例为隔离"相位对齐行为"，检测时采用
  `min_match_groups=0、same_pattern_ratio=0`（与 moving target 设计 2.2 一致）；
  慢速停留用例保留默认阈值。
- **背景视频叠加的真值**：目标覆盖块由 `overlap > 0` 判定；期望块序列 =
  原始背景视频的 preprocess 块序列 + `overlap × boost14(t)`（§4.5），测试据此
  断言"叠加贡献"被正确渲染与检出；背景视频本身的运动/纹理不再作为合成先验。

## 7. 测试建议

新增 `tests/test_sim_video.py`：

1. **文件与元数据**：生成成功、`cv2.VideoCapture` 可读；**fps、宽高均与背景
   视频一致**（均不可配置）、帧数 ≤ 背景帧数；
2. **参考标签正确性**：`trajectory_positions` 逐帧坐标与理论值一致；
   `block_overlap` 与二值掩码直接分块平均一致；
3. **光强一致性**：以恒定灰度视频文件为背景（测试内生成），用 `block_overlap`
   + §4.5 公式重建期望块时间序列，与 `preprocess` 输出精确一致
   （`np.allclose`，容差仅浮点舍入）；
4. **端到端检出**：以 30fps 恒定灰度视频为背景（测试内生成），叠加 50Hz 频闪
   光源生成视频，经 预处理 → moudle1，跳过前 27
   帧后：目标停留 ≥ 6 组的块被检出、背景块不误检；
5. **移动突发相位用例**：相位边界内/外两种突发，采用补偿口径记录检出差异
   （当前预期：边界外漏检），作为回归基线；
6. **无频闪对照组**：`flicker_amplitude=0` 或 `target_radius=0` 生成的视频经同
   链路检测，`flicker_block_num == 0`；
7. **参数校验**：非法参数（`flicker_dc+amplitude>255`、`cols > 背景视频宽度`
   或 `rows > 背景视频高度`、负半径、`duty_cycle` 越界、`background_video`
   不存在等）抛 `ValueError`；
8. **不破坏现有回归**：`tests/test_moudle1.py` 等原有用例保持通过。
9. **背景视频叠加**：以一段生成的静态视频作为 `background_video`，叠加光源后：
   `preprocess` 输出与 `bg_blocks + overlap·boost14` 精确一致（§4.6）；叠加块
   （`overlap > 0`）被检出、背景块不误检；背景视频比 `frames` 短时输出按较短者
   截断且参考标签记录实际帧数。
10. **占空比波形**：`waveform="square"` 且不同 `duty_cycle`（如 0.3/0.5/0.8）生成
    的视频：逐帧提升量序列与 §4.1 方波公式一致；静止目标覆盖块仍被默认阈值检出
    （周期 3 模式）；方波 ON 帧占比与 `duty_cycle` 近似一致（受采样相位影响）。
11. **驱动频率/视在频率关系**：同一背景帧率下用不同 `flicker_frequency`
    （如 30fps 下的 50/100/125/200/500 Hz），逐帧块序列的视在周期与
    PLAN.md 关系式 `F=2fd`、`fa=|F−round(F/fps)·fps|`、`P=fps/fa` 一致；
    `(fps, fd)` 不可检测组合（`P` 非整数）在 `validate()` 阶段抛 `ValueError`。

## 8. 改动量测算

| 改动点 | 内容 | 预估行数 |
| --- | --- | --- |
| `flicker_detect/sim_video.py` | 参数 + 校验 + 轨迹 + 渲染（背景视频叠加 + 波形/占空比）+ 重叠度 + 参考标签 + 写出 + CLI | 210~310 |
| `tests/test_sim_video.py` | 上述 10 类用例 | 170~250 |
| 端到端验证脚本 | 生成 → 预处理 → 检测 → 断言汇总 | 40~80 |

合计约 420~640 行，无新增第三方依赖（numpy / opencv 已在 requirements.txt）。

## 9. 实施步骤

1. 新增 `flicker_detect/sim_video.py`：`SimVideoParams` + `validate`；
2. 实现 `light_at` / `trajectory_positions` / `block_overlap` / `render_frame`；
3. 实现背景读取与叠加：`open_background` + §4.6 叠加渲染；
4. 实现 `build_reference` / `generate_video`（含编码器探测与回退）与 `main()` CLI；
5. 新增 `tests/test_sim_video.py` 并跑通全部用例（第 7 节）；
6. 端到端验证：生成视频 → `preprocess` → `moudle1`，核对检出与移动目标回归基线；
7. 更新 [PLAN.md](./PLAN.md) 待办状态、`README` 使用说明；同步 GitHub。

> 运行约束：按 [PLAN.md](./PLAN.md) 必须约束 1，统一在 conda `flicker` 环境执行
> （当前默认 Python 缺少 `cv2`，无法直接跑通依赖 OpenCV 的测试）。

## 10. 风险与边界

- **编码器依赖**：OpenCV `VideoWriter` 写 MP4 依赖系统 ffmpeg/编码器；实现时优先
  用 `avc1`（H.264，浏览器 `<video>` 可解码，供前端页内预览），失败回退
  `mp4v`/MJPG（`.avi`），并在返回元数据中记录实际编码，避免静默失败。
- **slope_max 换算紧**：`slope_max=10`（14 bit/帧）换算到 8 bit 约 `0.156`/帧，
  很紧。静止全覆盖块因周期闭合差为 0 能通过；但移动目标跨块的重叠度变化会在
  块时间序列上引入非周期分量，闭合斜率门大概率判废——这正是 moving target 设计
  记录的回归，不是本模块缺陷；参考标签的 `stay_frames` 用于解释。
- **位深口径**：视频 8 bit、检测满量程 16383；preprocess gamma 查找表（gamma=1.0
  线性）做映射，振幅门槛按 14 bit 空间生效，已由 §6 校验可检出。若未来换
  16 bit 编码，需重测换算系数。
- **采样简化**：v1 用帧中点采样代替曝光窗口积分，低帧率 + 大曝光组合下波形相位
  有偏差；默认 30fps/短曝光可忽略。卷帘条纹效果本期不做。
- **块突发语义**：移动目标跨块时单块时间序列是短突发而非连续周期，检出依赖停留
  时长（见 moudle1_moving_target_design.md 第 3 节能力边界）。
- **轨迹越界**：v1 采用裁剪语义并写入参考标签，不自动回绕，避免语义歧义。
- **背景视频读取约束**：输出 fps/分辨率默认取背景视频，不做缩放/重采样；背景
  视频帧率低于检测参数表配置时，叠加频闪的视在频率与 `lookup_detector_parameters`
  不再对齐，测试需按背景实际 fps 重新查参数。高分辨率背景（如 4K）逐帧渲染
  较慢，测试用低分辨率背景控制耗时。
- **时长截断**：背景视频比 `frames` 短时按较短者截断输出，并在参考标签记录
  实际帧数，避免越界读帧。
- **背景自身运动干扰**：背景视频的真实运动/纹理/曝光变化会混入块时间序列，
  可能干扰目标频闪或产生额外变化分量；这正是"接近真实画面"验证的价值，测试
  断言以 §4.6 重建公式为准，不要求背景静止。
- **JSON 体积**：`frames` 逐帧数组与 `block_stats` 规模为 `帧数 × 192`，默认
  90×192 可接受；不把逐帧逐块重叠度矩阵全量写入 JSON（体积大），测试需要精确
  序列时直接调 `block_overlap()` 重建。

## 11. 决策记录

- 2026-08-28：确认需求——生成真实视频文件（MP4），聚焦移动目标频闪场景。
- 已确认可调节参数：帧数、运动轨迹、频闪规律、频闪强度、画面大小、
  频闪面积（第 3 节）；视频帧率不可配置，从背景视频读取。
- 已选定方案：`flicker_detect/sim_video.py` 纯函数 + CLI，输出 MP4 + 同名 JSON
  参考标签；复用现有 预处理 → moudle1 链路做端到端验证。
- 已确认：逐块真值用"二值掩码 → 与 preprocess 同口径的 INTER_AREA 分块"精确
  计算（§4.5），不做几何近似；移动突发用例采用补偿口径（§6）。
- 2026-08-28：新增"背景视频叠加接口"——以真实视频为背景叠加强度可调的伪频闪
  光源（§2、§3 `background_video`、§4.6），光源可静止或沿轨迹移动；叠加采用
  加法提升模型，真值用 §4.6 重建公式。
- 2026-08-28：确认**本底只使用导入的视频**，移除固定亮度背景（`background_level`
  参数删除、合成纯背景模式取消）；恒定背景场景由测试用恒定灰度视频文件充当。
- 2026-08-28：频闪强度增加可调**占空比**——新增 `waveform="square"` 与
  `duty_cycle` 参数（§4.1），仅对方波生效；默认 `rectified_sin` 保持不变，仍与
  检测参数表周期对齐。
- 2026-08-28：确认**视频帧率不可配置**——移除可调参数中的 `fps`，输出帧率一律
  从背景视频读取（`CAP_PROP_FPS`），在导入视频上叠加强度可调的伪频闪光源生成
  频闪视频；帧率相关派生量（视在频率/P）按背景实际 fps 查表对齐。
- 2026-08-28：确认**画面大小不可配置**——移除可调参数中的 `width`/`height`，
  输出分辨率一律从背景视频读取（`CAP_PROP_FRAME_WIDTH`/`HEIGHT`），不缩放画布；
  `cols`/`rows` 分块网格仍可配置，须 ≤ 背景视频分辨率。
- 2026-08-28：确认**驱动频率手动可调**——`flicker_frequency`（fd）由用户设定，
  按 PLAN.md 频率定义 `fd → F=2fd → fa=|F−k·fps| → P=fps/fa` 关系式构建虚拟
  频闪光源（§4.1）；`(fps, fd)` 视在周期非整数的组合在 `validate()` 拒绝。
- **尚未实施**：待排期后按第 8 节改动量与第 9 节实施步骤落地。
