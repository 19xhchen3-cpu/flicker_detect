# 频闪检测 API 接口层设计

## 1. 目的

把检测能力做成独立的后端接口：前端不再内置任何算法，只通过 HTTP 调用后端；
后端只暴露接口，不依赖特定前端。这样任意前端（Web、桌面、移动端）都可以接入，
核心算法 `moudle1.py` 保持纯逻辑，不感知 HTTP。

## 2. 分层结构

```text
任意前端（Web / 桌面 / 移动）
        │  HTTP + JSON
        ▼
API 接口层  app/api.py
   - 路由（简单的 if 分支）
   - 会话管理（dict + 锁）
   - 参数解析与校验（查 detector_config 表）
   - 调用核心模块并序列化结果
        │
        ▼
核心计算层（不改动）
   - flicker_detect/moudle1.py        检测算法
   - flicker_detect/preprocess.py     视频预处理
   - flicker_app/engine.py            频闪仿真
```

约束：

- API 层只做编排、状态和序列化，不包含检测算法本身。
- `moudle1.py` 不修改，只接收解析好的 `DetectorParameters`。
- 使用 Python 标准库（`http.server` / `json` / `threading`），不引入框架。

## 3. 接口清单

| 接口 | 方法 | 用途 | 状态 |
| --- | --- | --- | --- |
| `/api/v1/options?fps=30` | GET | 返回该 fps 下所有可检测频率及完整参数 | 无状态 |
| `/api/v1/sessions` | POST | 创建会话，返回 `session_id` | 有状态 |
| `/api/v1/sessions/{id}/video` | POST | 上传视频，NDJSON 流式返回元数据和逐帧块亮度 | 有状态 |
| `/api/v1/sessions/{id}/detect` | POST | 按参数重算全部帧，返回紧凑检测结果 | 有状态 |
| `/api/v1/sessions/{id}/frames/{frameId}` | GET | 返回单帧所在窗口的分组详情 | 有状态 |
| `/api/v1/sessions/{id}` | DELETE | 释放会话资源 | 有状态 |
| `/api/v1/simulate` | POST | 频闪仿真 | 无状态 |

## 4. 统一约定

- 成功：2xx，直接返回业务数据。
- 失败：4xx/5xx，返回统一错误结构：

  ```json
  { "error": { "code": "invalid_params", "message": "..." } }
  ```

- 所有 NaN / Infinity 序列化为 `null`，保证浏览器 `JSON.parse` 可解析。
- 视频上传用 chunked NDJSON 流式返回，避免一次性大 JSON。
- 会话数据只保存在服务端内存，接口不暴露块数据以外的内部结构。

### 错误码

| code | 含义 |
| --- | --- |
| `invalid_params` | 参数缺失、类型错误或查表失败 |
| `not_found` | 会话或帧不存在 |
| `no_video` | 会话尚未上传视频 |
| `internal_error` | 服务端异常 |

## 5. 参数调用流程

参数以 `detector_config.py` 的参数表为唯一事实来源，前端不复制参数表。

1. 前端调 `GET /api/v1/options?fps=30` 获取可检测频率和默认参数，用于下拉框和参数面板。
2. 前端回传选择：`{"fps": 30, "params": {"detection_frequency": 50, ...}}`，
   可选的阈值覆盖（如 `min_match_groups`）也放在 `params` 里。
3. API 层解析：
   - 用 `lookup_detector_parameters(fps, detection_frequency)` 查表拿到整行默认参数；
   - 从会话元数据补充 `cols` / `rows`；
   - 校验前端显式传入的阈值并覆盖默认值；
   - `group_size <= 0`（未配置组合）返回 `invalid_params`；
   - 构造冻结的 `DetectorParameters`。
4. 把 `DetectorParameters` 交给 `moudle1.HistoryBuffer.detect(params)`。

`moudle1` 只按需读取 `params` 的字段：

| 字段 | 用途 |
| --- | --- |
| `group_size` | 分组大小 |
| `history`（= `min_frames`） | 历史窗口长度 |
| `y_max`（由 `y_max_bit` 换算） | 振幅门槛基数 |
| `min_amp_pos_ratio` | K-of-N 的 K |
| `slope_max` / `min_match_groups` / `same_pattern_ratio` / `max_group_amp_cv` | 四个判定门限 |

## 6. 接口示例

### 6.1 获取检测选项

```http
GET /api/v1/options?fps=30
```

```json
{
  "fps": 30,
  "options": [
    {
      "detection_frequency": 50,
      "params": {
        "group_size": 4,
        "min_frames": 30,
        "min_match_groups": 5,
        "min_group_amp_ratio": 0.001,
        "min_amp_pos_ratio": 0.5,
        "same_pattern_ratio": 0.8,
        "slope_max": 10.0,
        "max_group_amp_cv": 0.4,
        "y_max_bit": 14,
        "cols": 16,
        "rows": 12
      }
    }
  ]
}
```

### 6.2 创建会话

```http
POST /api/v1/sessions
```

```json
{ "session_id": "a1b2c3" }
```

### 6.3 上传视频

```http
POST /api/v1/sessions/a1b2c3/video?name=clip.mp4&cols=16&rows=12&gamma=1.0
Content-Type: application/octet-stream
```

请求体为视频文件原始字节，响应为 NDJSON 流，行类型：

- `meta`：fps、帧数、宽高、分块、`y_max`、`detector_params`、`detector_options`。
- `progress`：`frame_id`、时间戳、块亮度数组、缩略图等。
- `done`：处理帧数。
- `error`：转换失败信息。

### 6.4 重算检测

```http
POST /api/v1/sessions/a1b2c3/detect
Content-Type: application/json
```

```json
{ "fps": 30, "params": { "detection_frequency": 50 } }
```

```json
{
  "meta": {
    "frame_count": 30,
    "block_count": 192,
    "group_count": 9,
    "position_count": 3
  },
  "results": [
    null,
    {
      "frame_id": 29,
      "flicker_block_num": 192,
      "block_flags": [1, 1, 1],
      "sign_flip_ratio": [1.0, 1.0, 1.0],
      "mean_abs_diff": [4096.0, 4096.0, 4096.0],
      "diff_cv": [0.0, 0.0, 0.0],
      "flip_count": [9, 9, 9],
      "avg_ratio_diff": [1.8, 1.8, 1.8],
      "avg_delta_diff": [4000.0, 4000.0, 4000.0],
      "group_count": 9,
      "position_count": 3,
      "groups": [
        { "group_index": 0, "start_frame": 2, "end_frame": 5 }
      ],
      "min_group_amp": 16.383,
      "k_value": 2,
      "slope_max": 10.0,
      "min_match_groups": 5,
      "same_pattern_ratio": 0.8,
      "max_group_amp_cv": 0.4
    }
  ]
}
```

历史不足 `min_frames` 的帧对应 `null`。

### 6.5 单帧分组详情

```http
GET /api/v1/sessions/a1b2c3/frames/29?fps=30&detection_frequency=50
```

```json
{
  "frame_id": 29,
  "ready": true,
  "result": {
    "block_flags": [1, 1, 1],
    "valid_group_count": [9, 9, 9],
    "group_amp_pass": [[3, 3, 3]],
    "group_slope": [[0.0, 0.0, 0.0]],
    "group_valid": [[true, true, true]],
    "group_dominant": [[true, true, true]],
    "group_sign": [["++-", "++-", "++-"]],
    "groups": [
      { "group_index": 0, "start_frame": 2, "end_frame": 5 }
    ]
  }
}
```

分组诊断矩阵（`group_amp_pass` / `group_slope` / `group_valid` /
`group_dominant` / `group_sign`）由 API 层的视图辅助逻辑生成，只用于前端
悬停提示和历史曲线着色，不影响 `moudle1` 的判定结果。

## 7. 下接 moudle1 的方式

- 会话保存逐帧块亮度：`blocks[frame][block]`，服务端内存约 `192 × 帧数` 个浮点数。
- 每次检测构造 `moudle1.HistoryBuffer(block_count, history)`，逐帧
  `push_frame` 后调 `buffer.detect(params)` 得到块级结果。
- 布局、窗口极差/比值、逐组诊断由 API 层的小函数生成：
  - `_window_layout(H, group_size)`：分组布局；
  - `_group_view(blocks, params)`：逐块逐组诊断。
- 这些辅助函数从现有 `app/server.py` 平移过来，`moudle1.py` 保持不变。

## 8. 文件结构

```text
app/
  api.py          # API 层（单文件，纯标准库：路由、会话、参数解析、编排）
  server.py       # 静态页面服务，把 /api/v1/* 交给 api.py
flicker_detect/
  moudle1.py      # 核心检测（不改动）
  detector_config.py
  preprocess.py
docs/
  api_design.md   # 本文档
```

## 9. 实施步骤

1. 新建 `app/api.py`：把现有 `_request_params`、`_compact_result`、
   `_group_view`、`_window_layout` 平移进来，增加会话存储和 `/api/v1/*` 路由。
2. `app/server.py` 静态路由把 `/api/v1/*` 转发给 API 层，静态页面逻辑不变。
3. 前端 `app.js` 改用 `/api/v1/...` 地址并携带 `session_id`。
4. 接口测试覆盖：会话生命周期、上传、重算、单帧详情、错误路径。
5. 更新本文档中的示例和错误码，确认后提交。
