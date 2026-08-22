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
| 块内 `amp` | `[G][P]` | `g * P + p` |
| `closure` | `[G]` | `closure[g]` |
| 块内 `valid_codes` | `[G]` | `valid_codes[i]` |
| `stamps` | `[3^P]` | 按码索引 |
| `counts` | `[3^P]` | 按码索引 |
| `block_flags` | `[B]` | `block_flags[b]` |

符号不再单独保存为 `sign` 数组：三进制模式码 `code` 在位置循环内直接累加
（见 §3）；`valid_codes[i]` 与第 `i` 个有效组（`valid_groups[i]`）平行存放。

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
    stamps = [-1] * pattern_count  # 记录该码最近一次被计数的块号，块间无需清零

    for b in range(B):
        hbase = b * H

        # 本块局部缓冲，对应 C++ 的栈上固定数组
        block_amp = [0.0] * (G * P)
        block_sign = [0] * (G * P)
        closure = [0.0] * G
        amp_group_ok = [0] * G
        group_valid = [0] * G
        pattern = [0] * G
        valid_codes = []      # 与 valid_groups 平行：第 i 个有效组的符号模式码
        valid_groups = []     # 第 i 个有效组的全局组号
        dominant_groups = []
        best_size = 0
        best_code = -1

        # ---------- 1. 分组、特征、K-of-N、斜率、编码与计数 ----------
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
                if k_value > 0 and not amp_reached and a > min_group_amp:
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

            # 有效组才执行条件编码短循环，避免为斜率不合格组浪费编码
            if group_valid[g]:
                code = 0
                for p in range(P):
                    # -1 -> 0, 0 -> 1, +1 -> 2
                    code = code * 3 + (block_sign[g * P + p] + 1)
                pattern[g] = code
                valid_codes.append(code)
                valid_groups.append(g)
                if stamps[code] != b:
                    stamps[code] = b
                    counts[code] = 0
                counts[code] += 1
                if counts[code] > best_size:
                    best_size = counts[code]
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

        # ---------- 2. 跨周期振幅 CV ----------
        # 主导成员收集与振幅求和合并为一遍遍历
        block_ok = False
        if dominant_ok:
            d_count = 0
            sum_amp = [0.0] * P
            for i in range(valid_count):
                if valid_codes[i] == best_code:
                    g = valid_groups[i]
                    dominant_groups.append(g)
                    d_count += 1
                    for p in range(P):
                        sum_amp[p] += block_amp[g * P + p]

            if d_count > 0:
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

        # ---------- 3. 最终判定 ----------
        block_flags[b] = 1 if block_ok else 0
        flicker_block_num += block_flags[b]

    return flicker_block_num
```

## 4. 主函数内部各阶段说明

### 4.1 分组和特征

- `group_starts` 在函数内部计算一次。
- 每个 block 只遍历自己的历史区间 `history[b * H + ...]`。
- 每个 `(group, position)` 计算相邻帧亮度差 `d`，得到 `amp` 和符号 digit。
- 符号模式编码保留为“斜率门通过后的条件短循环”，只对有效组再扫一遍 `P`
  个位置；不把编码合并进位置循环，避免为斜率不合格组也付出编码成本。
- 若未来 profiling 证明第二遍循环开销才是瓶颈，再改为位置循环内直接累加
  `code = code * 3 + digit`，并同步去掉 `block_sign/pattern` 数组。
- `closure` 直接取周期组首尾帧亮度差 `last - first`，不再逐位置累加 diff。

### 4.2 K-of-N 提前退出

- 阈值比较只在 `k_value > 0` 时进行。
- 达到 `pass_count >= k_value` 后只停止阈值比较，仍然继续构建剩余 `diff/amp/code/closure`。
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
- 每个组的符号序列编码成一个小整数；编码保留为有效组上的条件短循环，计数在
  组通过斜率门后立即进行，不再有独立的计数循环。
- `counts` 按码统计；`stamps[code] != b` 时先把该码计数清零，代替块尾整表清零，
  避免跨块计数残留。
- 只在 `counts[code] > best_size` 时严格更新 best_size 与 best_code。
- `valid_count == 0` 时该块直接判定不通过。
- 不能提前退出计数循环：`best_size` 必须为最终值（`flip_count` 诊断量与 CV 的
  成员收集都依赖它），运行期无法证明剩余组不会改变计数结果，提前退出会导致
  漏判或换掉主导类。
- 安全剪枝（计算时间优先实现）：`min_match_groups > 0` 且
  `valid_group_count <= min_match_groups` 时，`best_size <= valid_group_count`
  恒成立，`count_pass` 必为假，可整块跳过计数与 CV；仅当不需要精确
  `flip_count`/`sign_flip_ratio` 诊断量时才能连计数一起跳过。
  `min_match_groups` 越高该剪枝触发越频繁。

### 4.5 CV

- 只对主导组计算。
- 主导成员的收集（`valid_codes[i] == best_code`）与振幅求和合并为一遍遍历，
  不再单独扫描一遍有效组。
- 计算时间优先时可进一步用 Welford 单趟统计，在收集主导组的同时维护均值与
  平方偏差和，省掉第二趟 `variance_sum` 遍历；方差仍取 `ddof=0` 总体方差。
- 只有 `mean_amp > min_group_amp` 的位置参与 CV。
- 当 `k_value == 0` 时，按 4.2 的约定 `min_group_amp` 为 0，等价于所有平均振幅
  非零的位置都参与 CV。
- 使用总体方差 `variance_sum / d_count`，与当前 `ddof=0` 一致。
- `max_group_amp_cv == 0` 时门禁关闭。

### 4.6 最终判定

- `block_ok` 最终写入 `block_flags[b]`。
- `flicker_block_num` 在本函数内累计。
- 诊断量通过可选输出数组写回，不进入判定路径。

### 4.7 全门禁关闭且无诊断量时的短路

- 当 `min_match_groups == 0`、`same_pattern_ratio == 0`、
  `max_group_amp_cv == 0`，且 `flip_count/sign_flip_ratio/mean_abs_diff/diff_cv`
  四个诊断量参数全部为 `None` 时，主导模式与 CV 门禁全部关闭。
- 该配置下 `dominant_ok` 恒等于 `valid_count > 0`，`block_ok` 恒等于
  `dominant_ok`（含 CV 禁用时的 `elif` 分支），因此可以直接短路：

    # 阶段 1 内，有效组分支：
    if group_valid[g]:
        valid_codes.append(code)
        valid_groups.append(g)
        if not pattern_gates_disabled:
            ...计数与 best 跟踪...

    # 阶段 1 之后：
    if pattern_gates_disabled:
        block_ok = valid_count > 0
    else:
        ...主导模式门槛与 CV 完整路径...

- 阶段 1（特征与 K-of-N/斜率门）仍需完整执行，因为 `valid_count` 由它决定；
  短路只跳过符号模式计数与 CV。
- 任一诊断量被请求时不能走该分支，否则诊断输出会与完整路径不一致。

### 4.8 计算时间优先的优化顺序

- 第一优先：安全剪枝。`min_match_groups > 0` 且
  `valid_group_count <= min_match_groups` 时整块跳过计数与 CV，收益随
  `min_match_groups` 增大而增加。
- 第二优先：CV 单趟统计。主导成员收集、振幅求和、方差统计合并为一趟
  `O(G×P)` 遍历，优先采用 Welford 保持数值稳定。
- 第三优先：流式增量检测。当前每次调用都对完整历史窗口重算，跨帧复用已算过
  的分组、符号编码与计数是更大的时间收益来源，后续单独设计。
- 第四优先：C++ 数值替换。`sqrt` 改平方比较、浮点改整数/定点、固定 `P`
  时展开位置循环。
- 最后才是编码合并。只有 profiling 证明“第二遍编码循环的开销”是实际瓶颈时，
  才把编码并入位置循环；该改动会增加斜率不合格组的无用编码计算。

## 5. 需要保持的边界语义

- `group_count = floor((H - 1) / P)`。
- `drop_count = H - used_frames`，从最旧端丢弃。
- `amp > min_group_amp` 为严格大于。
- `pass_count >= k_value` 为大于等于。
- `abs(slope) <= slope_max` 为小于等于。
- 主导模式计数在阶段 1 内按组顺序即时进行，`stamps` 块标记不改变组间顺序。
- `best_size > min_match_groups` 为严格大于。
- `sign_flip_ratio > same_pattern_ratio` 为严格大于。
- CV 使用总体标准差 `ddof=0`。
- 参数为 0 时对应门禁关闭，不能简单当作阈值 0。

## 5.1 主导模式门槛的调参边界

- `min_match_groups` 与 `same_pattern_ratio` 约束的是同一个量 `best_size`：
  两者同时满足等价于 `best_size > max(min_match_groups, same_pattern_ratio × valid_count)`，
  给定有效组数时只有一个门槛实际生效。
- 默认 `min_match_groups=2`、`same_pattern_ratio=0.8` 下，两者要求相同时
  `valid_count = 2 / 0.8 = 2.5`：有效组较多时比例门槛主导（要求模式高度一致，
  防止 5:4 之类的模式分裂），有效组很少时数量门槛主导（否决两三个组碰巧同
  符号的伪周期）。
- 调参只动一个时可能被另一个覆盖：收紧模式一致性调 `same_pattern_ratio`，
  收紧最少证据量调 `min_match_groups`；两者都提高会同时收紧两档，配合 §4.4
  的安全剪枝（`valid_group_count <= min_match_groups` 整块跳过）能按调参强度
  成比例地减少计算量。

## 6. 后续 C++ 映射

- `history` 可用 `std::vector<uint16_t>` 或固定数组，长度为 `B * H`。
- block 内的 `block_amp/block_sign/pattern/closure/valid_codes` 可以直接用栈上
  固定数组；若未来改为位置循环内直接累加 digit，再移除 `block_sign` 与
  `pattern` 数组。
- `counts` 与 `stamps` 大小固定为 `3^P`，当前 `P=3` 时为 27；块间用
  `stamps[code] != b` 标记代替整表清零，不再需要 `touched` 重置循环。
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
