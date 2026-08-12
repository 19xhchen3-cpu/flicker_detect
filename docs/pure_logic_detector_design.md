# 频闪检测核心算法纯逻辑版本设计（单函数版）

## 1. 目的

本文档定义一套可直接翻译成嵌入式 C++ 的纯逻辑频闪检测算法。与按功能拆分的版本不同，本版把历史索引、分组、特征构建、K-of-N、闭合斜率、主导符号模式、CV 和最终判定全部放在同一个主函数中。

设计原则：

- 核心检测链路不使用 NumPy/OpenCV，只使用显式循环、一维数组和固定 shape。
- 主函数按 block 做外层循环，每个 block 内部完成所有阶段，不再生成跨 block 的大中间矩阵。
- 所有数组在进入函数前预分配，函数内部只读写固定索引。
- 保留当前 Python 实现的判定语义：分组方式、阈值比较、主导模式、CV 门限均不改变。
- 本版先保留“每次对一个完整历史窗口做一次检测”的语义，流式/相位锁定优化放到后续单独设计。

## 2. 数据布局

统一使用一维数组。当前示例配置：

```text
H = 30          历史窗口帧数
B = 192         块数
group_size = 4  每个周期组的帧数
P = 3           position_count = group_size - 1
G = 9           group_count = floor((H - 1) / P)
```

一维布局：

| 数据 | 概念形状 | 一维索引 |
| --- | --- | --- |
| `history` | `[B][H]` | `history[b * H + h]` |
| `group_starts` | `[G]` | `group_starts[g]` |
| 块内 `amp/sign` | `[G][P]` | `g * P + p` |
| `closure` | `[G]` | `closure[g]` |
| `pattern` | `[G]` | `pattern[g]` |
| `block_flags` | `[B]` | `block_flags[b]` |

历史按块连续存放。主函数中每个 block 只访问自己的 `history[b * H ...]` 连续区间。

## 3. 主函数

所有阶段写在一个函数里，内部按块完成检测。伪代码使用 Python 写法，但循环、索引和控制流与 C++ 一一对应。

```python
def detect_window(
    history,           # 一维亮度历史，[B * H]
    slot_order,        # 环形缓冲逻辑帧顺序，[H]
    H,                 # 历史帧数
    B,                 # 块数
    group_size,        # 每周期组帧数
    min_group_amp,     # y_max * min_group_amp_ratio
    k_value,           # ceil(P * min_amp_pos_ratio)
    slope_max,
    min_match_groups,
    same_pattern_ratio,
    max_group_amp_cv,
    block_flags,       # 输出 [B]
    mean_abs_diff=None,
    diff_cv=None,
    sign_flip_ratio=None,
    flip_count=None,
):
    P = group_size - 1
    G = (H - 1) // P if H > 0 else 0
    if k_value == 0:
        min_group_amp = 0.0  # 与当前 Python 实现一致，CV 位置过滤同步关闭
    flicker_block_num = 0

    if G <= 0:
        for b in range(B):
            block_flags[b] = 0
        return 0

    # 分组布局：在同一个函数内完成
    used_frames = group_size + (G - 1) * P
    drop_count = H - used_frames
    group_starts = [drop_count + g * P for g in range(G)]

    pattern_count = 3 ** P
    counts = [0] * pattern_count

    for b in range(B):
        hbase = b * H

        # 本块局部缓冲，对应 C++ 的栈上固定数组
        block_amp = [0.0] * (G * P)
        block_sign = [0] * (G * P)
        closure = [0.0] * G
        amp_group_ok = [0] * G
        group_valid = [0] * G
        pattern = [0] * G
        valid_groups = []

        # ---------- 1. 分组、特征、K-of-N、斜率 ----------
        for g in range(G):
            start = group_starts[g]
            tail = group_starts[g] + P
            pass_count = 0
            amp_reached = False

            for p in range(P):
                d = (
                    history[hbase + slot_order[start + p + 1]]
                    - history[hbase + slot_order[start + p]]
                )
                a = abs(d)
                idx = g * P + p

                block_amp[idx] = a
                if d > SIGN_ZERO_EPSILON:
                    block_sign[idx] = 1
                elif d < -SIGN_ZERO_EPSILON:
                    block_sign[idx] = -1
                else:
                    block_sign[idx] = 0

                # K-of-N 提前退出只跳过阈值比较，不跳过特征构建
                if k_value > 0 and not amp_reached:
                    if a > min_group_amp:
                        pass_count += 1
                        if pass_count >= k_value:
                            amp_reached = True

            first_slot = slot_order[start]
            tail_slot = slot_order[tail]
            closure[g] = history[hbase + tail_slot] - history[hbase + first_slot]
            amp_group_ok[g] = 1 if k_value == 0 else (amp_reached or pass_count >= k_value)

            slope_ok = amp_group_ok[g]
            if slope_max > 0 and slope_ok:
                slope = closure[g] / P
                slope_ok = abs(slope) <= slope_max

            group_valid[g] = slope_ok

            if group_valid[g]:
                code = 0
                for p in range(P):
                    # -1 -> 0, 0 -> 1, +1 -> 2
                    code = code * 3 + (block_sign[g * P + p] + 1)
                pattern[g] = code
                valid_groups.append(g)

        # ---------- 2. 主导符号模式 ----------
        best_size = 0
        best_local = -1
        best_code = -1
        touched = []

        for i, g in enumerate(valid_groups):
            code = pattern[g]
            if counts[code] == 0:
                touched.append(code)
            counts[code] += 1
            if counts[code] > best_size:
                best_size = counts[code]
                best_local = i
                best_code = code

        valid_count = len(valid_groups)
        ratio = best_size / valid_count if valid_count > 0 else 0.0
        count_pass = min_match_groups == 0 or best_size > min_match_groups
        ratio_pass = same_pattern_ratio == 0 or ratio > same_pattern_ratio
        dominant_ok = valid_count > 0 and count_pass and ratio_pass

        if flip_count is not None:
            flip_count[b] = best_size
        if sign_flip_ratio is not None:
            sign_flip_ratio[b] = ratio

        # ---------- 3. 跨周期振幅 CV ----------
        dominant_groups = []
        if dominant_ok:
            for g in valid_groups:
                if pattern[g] == best_code:
                    dominant_groups.append(g)

        block_ok = False
        if dominant_groups:
            d_count = len(dominant_groups)
            sum_amp = [0.0] * P

            for g in dominant_groups:
                for p in range(P):
                    sum_amp[p] += block_amp[g * P + p]

            mean_amp = [value / d_count for value in sum_amp]
            valid_positions = []
            for p in range(P):
                if mean_amp[p] > min_group_amp:
                    valid_positions.append(p)

            if valid_positions:
                max_pos_cv = 0.0
                mean_sum = 0.0

                for p in valid_positions:
                    mean = mean_amp[p]
                    variance_sum = 0.0
                    for g in dominant_groups:
                        delta = block_amp[g * P + p] - mean
                        variance_sum += delta * delta

                    variance = variance_sum / d_count
                    std = sqrt(max(variance, 0.0))
                    pos_cv = std / mean
                    if pos_cv > max_pos_cv:
                        max_pos_cv = pos_cv
                    mean_sum += mean

                if mean_abs_diff is not None:
                    mean_abs_diff[b] = mean_sum / len(valid_positions)
                if diff_cv is not None:
                    diff_cv[b] = max_pos_cv

                if max_group_amp_cv == 0:
                    block_ok = dominant_ok
                else:
                    block_ok = dominant_ok and max_pos_cv <= max_group_amp_cv
            else:
                if max_group_amp_cv == 0:
                    block_ok = dominant_ok

        # ---------- 4. 最终判定 ----------
        block_flags[b] = 1 if block_ok else 0
        flicker_block_num += block_flags[b]

        for code in touched:
            counts[code] = 0

    return flicker_block_num
```

## 4. 主函数内部各阶段说明

### 4.1 分组和特征

- `group_starts` 在函数内部计算一次。
- 每个 block 只遍历自己的历史区间 `history[b * H + ...]`。
- 每个 `(group, position)` 计算相邻帧亮度差 `d`，得到 `amp` 和 `sign`。
- `closure` 直接取周期组首尾帧亮度差 `last - first`，不再逐位置累加 diff。

### 4.2 K-of-N 提前退出

- 阈值比较只在 `k_value > 0` 时进行。
- 达到 `pass_count >= k_value` 后只停止阈值比较，仍然继续构建剩余 `diff/amp/sign/closure`。
- 因此提前退出不会影响后续斜率、主导模式和 CV 所需数据。
- `k_value == 0` 时门禁关闭，所有组直接通过振幅门。
- `k_value == 0` 时，为了与当前 Python 实现保持一致，`min_group_amp` 同步置为
  0，因此 CV 位置过滤也同时关闭；不要理解为只关闭 K-of-N 而保留 CV 振幅门槛。

### 4.3 闭合斜率

- 直接使用 `closure[g] / P`，不重新堆叠 brightness。
- 只在通过 K-of-N 的组上比较 `abs(slope) <= slope_max`。
- `slope_max == 0` 时门禁关闭，`group_valid` 直接等于 `amp_group_ok`。

### 4.4 主导符号模式

- 只统计 `group_valid` 的组。
- 每个组的符号序列编码成一个小整数，用 `counts` 数组统计。
- 平票时保留最早出现的组，只在 `counts[code] > best_size` 时更新。
- `valid_count == 0` 时该块直接判定不通过。

### 4.5 CV

- 只对主导组计算。
- 只有 `mean_amp > min_group_amp` 的位置参与 CV。
- 当 `k_value == 0` 时，按 4.2 的约定 `min_group_amp` 为 0，等价于所有平均振幅
  非零的位置都参与 CV。
- 使用总体方差 `variance_sum / d_count`，与当前 `ddof=0` 一致。
- `max_group_amp_cv == 0` 时门禁关闭。

### 4.6 最终判定

- `block_ok` 最终写入 `block_flags[b]`。
- `flicker_block_num` 在本函数内累计。
- 诊断量通过可选输出数组写回，不进入判定路径。

## 5. 需要保持的边界语义

- `group_count = floor((H - 1) / P)`。
- `drop_count = H - used_frames`，从最旧端丢弃。
- `amp > min_group_amp` 为严格大于。
- `pass_count >= k_value` 为大于等于。
- `abs(slope) <= slope_max` 为小于等于。
- 主导模式平票时选择最早出现的组。
- `best_size > min_match_groups` 为严格大于。
- `sign_flip_ratio > same_pattern_ratio` 为严格大于。
- CV 使用总体标准差 `ddof=0`。
- 参数为 0 时对应门禁关闭，不能简单当作阈值 0。

## 6. 后续 C++ 映射

- `history` 可用 `std::vector<uint16_t>` 或固定数组，长度为 `B * H`。
- block 内的 `block_amp/block_sign/closure/pattern` 可以直接用栈上固定数组。
- `counts` 大小固定为 `3^P`，当前 `P=3` 时为 27。
- `group_starts` 可在函数内计算一次，或由调用方传入。
- 索引 `b * H + ...`、`g * P + p` 与 C++ 写法完全一致。
- `sqrt` 可替换为平方比较：`variance <= max_group_amp_cv^2 * mean^2`。

## 7. 与历史帧预处理模块的对接

前面的预处理模块逐帧输出 `blocks[B]`，因此需要一个历史缓冲区把逐帧数据写入 `detect_window()` 需要的 block-major 一维历史。

```python
class HistoryBuffer:
    def __init__(self, B, H):
        self.history = [0] * (B * H)   # [B][H]，block-major
        self.slot_order = [0] * H
        self.write_index = 0
        self.valid_count = 0

    def push_frame(self, blocks, frame_id):
        for b in range(B):
            self.history[b * H + self.write_index] = blocks[b]

        self.write_index = (self.write_index + 1) % H
        if self.valid_count < H:
            self.valid_count += 1

        if self.valid_count == H:
            oldest = self.write_index
            for h in range(H):
                self.slot_order[h] = (oldest + h) % H

    def detect(self, ...):
        if self.valid_count >= H:
            return detect_window(
                self.history,
                self.slot_order,
                H, B, ...
            )
```

说明：

- `write_index` 写满后指向最旧帧；
- `slot_order[h] = (oldest + h) % H` 把环形槽位映射成逻辑时间顺序；
- `detect_window()` 内部统一使用 `slot_order` 访问历史，`group_starts` 仍表示逻辑帧号起点；
- 丢帧、VFR 大间隔或场景突变时，可以重置 `valid_count` 和 `write_index`，避免把不连续历史拼进同一个检测窗口。

本版暂不加入相位锁定、滚动直方图和低频次决策，这些属于下一步流式优化，应在当前单函数版本对齐后再设计。
