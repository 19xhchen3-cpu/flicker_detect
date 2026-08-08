# 移动端频闪检测 v3 实现设计

本文档定义移动端频闪检测 v3 的完整实现方案。v3 不再使用旧的
`group_frames -> build_features -> evaluate_group_validity` 符号模式链路，改为
“按唯一视在频率检测”的 Goertzel 频域方案。

## 1. 设计目标

- 输入：每帧 `48x64`、14bit、行优先的亮度数据，无需 OpenCV/NumPy。
- 输出：按唯一视在频率 `fa` 输出 192 个块的 `block_flags` 和
  `flicker_block_num`，并给出驱动频率到 `fa` 的映射表。
- 性能：release 模式下 30fps 全部有效驱动频率档位单帧核心计算目标远低于
  2ms，P95 不超过 3ms。
- 内存：release 模式核心状态小于 128KB，帧内零动态分配。
- 抗噪：以频域幅度、幅度稳定性和相位一致性代替旧符号模式，降低过零噪声影响。

## 2. 频率来源

### 2.1 从驱动频率到视在频率

当前检测模型假设光强频率为驱动频率的两倍：

```
F = 2 * fd
```

其中 `fd` 是市电或 LED 驱动频率，例如 50Hz；`F` 是全波整流后的光强频率。

摄像头以 `fps` 采样后，`F` 会被混叠成视在频率：

```
k = round(F / fps)
fa = |F - k * fps|
```

视在周期为：

```
P = fps / fa
```

只有 `P` 为整数时，该档位才可用于整数周期检测。当前
`group_size_for(fps, fd)` 已经保证这一点，并且 `group_size = P + 1`。

### 2.2 30fps 别名表

以 30fps 为例，当前参数表的有效档位如下：

| 驱动频率 fd | 光强频率 F=2fd | 视在频率 fa | 视在周期 P | group_size | 是否有效 |
| ---: | ---: | ---: | ---: | ---: | --- |
| 50Hz | 100Hz | 10Hz | 3 | 4 | 是 |
| 60Hz | 120Hz | 0Hz | - | - | 否 |
| 100Hz | 200Hz | 10Hz | 3 | 4 | 是 |
| 125Hz | 250Hz | 10Hz | 3 | 4 | 是 |
| 200Hz | 400Hz | 10Hz | 3 | 4 | 是 |
| 240Hz | 480Hz | 0Hz | - | - | 否 |
| 500Hz | 1000Hz | 10Hz | 3 | 4 | 是 |

因此 30fps 下，50/100/125/200/500Hz 全部混叠到同一个 10Hz 视在频率。

### 2.3 按唯一 fa 去重

v3 检测单元是“唯一视在频率”，不是驱动频率：

- 同一个 `fa` 只计算一次。
- 多个驱动频率映射到同一个 `fa` 时，共用同一份 `block_flags`。
- 输出结果同时返回 `fa -> 驱动频率列表` 的映射，供上层标注。

这是采样混叠的固有信息限制：在 30fps 均匀时间采样下，无法仅凭块亮度时间序列
区分 50Hz 和 500Hz 驱动频率。如需区分，需要后续卷帘快门空间条纹方案，v1
不实现。

## 3. 数据链路

### 3.1 输入约定

输入固定为：

- `48` 行、`64` 列；
- `uint16`，范围 `0..16383`；
- 行优先，即 `luma[row * 64 + col]`；
- 帧连续；丢帧或场景突变时由调用方调用 `reset()`。

如果平台实际输出 `64x48` 或列优先，接入层负责转置。

### 3.2 4x4 块平均

目标块网格为 `12x16`，共 192 个块。48/12=4，64/16=4，因此每个块正好是
4x4 个采样：

```
sum = Σ luma[r*64 + c]   // 16 个采样，uint32 累加
block[row*16 + col] = (sum + 8) / 16
```

采用 round-to-nearest，避免累计向下取整偏差。该结果与旧 OpenCV
`INTER_AREA` 浮点结果行为一致，但不要求逐位相等。

4x4 平均对空间独立噪声有 4 倍标准差抑制；如果传感器噪声存在空间相关性，
实际收益会低于 4 倍。

## 4. 历史缓冲

### 4.1 容量

所有唯一 `fa` 共享同一份块历史。历史容量取所有启用档位中的最大
`min_frames`：

```
history_capacity = max(bin.min_frames)
```

当前 30fps 表为 30，因此：

```
history[30][192] * sizeof(uint16) = 11,520 bytes
```

### 4.2 环形缓冲

`pushFrame()` 流程：

1. 将 `48x64` 压缩为 `192` 个 `uint16` 块；
2. 写入 `history[write_index]`；
3. `write_index = (write_index + 1) % history_capacity`；
4. `valid_count = min(valid_count + 1, history_capacity)`；
5. 当 `valid_count >= 对应档位的 min_frames` 时，对该档位执行检测。

历史不足时，对应档位返回未就绪；其他档位仍可独立返回。

## 5. 频域检测

### 5.1 目标频率与窗口

对每个唯一 `fa`：

```
omega = 2 * pi * fa / fps = 2 * pi / P
```

窗口长度必须是 `P` 的整数倍，步进必须等于 `P`，才能保证窗口相位对齐。

窗口规则：

- 若 `H >= 8P`：`W = 4P`，`stride = P`；
- 否则若 `H >= 3P`：`W = 2P`，`stride = P`；
- 否则若 `H >= 2P`：`W = P`，`stride = P`；
- 否则：该档位历史不足，返回未就绪。

当前 30fps、`H=30`、`P=3`：

```
W = 12
stride = 3
window_count = (30 - 12) / 3 + 1 = 7
```

### 5.2 线性 detrend

每个窗口先做线性去趋势，减少自动曝光、缓慢亮度漂移在目标 bin 上的泄漏：

```
x_mean = mean(x)
slope = Σ((i - i_mean) * (x_i - x_mean)) / Σ((i - i_mean)^2)
x_detrend_i = x_i - (x_mean + slope * (i - i_mean))
```

该步骤只去除一阶趋势，不处理阶跃曝光或场景突变；阶跃由调用方 `reset()`
处理。

### 5.3 Goertzel

对每个 detrend 后的窗口计算 `fa` 处复系数：

```
coeff = 2 * cos(omega)

s0 = 0
s1 = 0
for n in 0..W-1:
    s2 = s1
    s1 = s0
    s0 = x[n] + coeff * s1 - s2

real = s0 - s1 * cos(omega)
imag = s1 * sin(omega)
mag  = hypot(real, imag)
phase = atan2(imag, real)
```

Goertzel 只计算一个已知频率 bin，复杂度为 `O(W)`，不需要完整 FFT。

### 5.4 幅度估计

针对当前 `0/1` 周期波形，窗口幅度估计为：

```
A_j = mag * P / W
```

该换算针对当前方形周期模型；真实灯具波形可能是正弦、削顶或脉冲形，后续需用
真实数据重新标定系数。

### 5.5 三个判定门

对每个 block、每个唯一 `fa`：

```
mean_amp = mean(A_j)
amp_cv = std(A_j) / mean_amp
r = |mean(exp(i * phase_j))|

pass_amp  = mean_amp > y_max * min_group_amp_ratio
pass_cv   = amp_cv <= max_group_amp_cv
pass_phase = r >= phase_coherence_min

block_flicker = pass_amp && pass_cv && pass_phase
```

默认参数初值：

```
min_group_amp_ratio = 0.001
max_group_amp_cv = 0.4
phase_coherence_min = 0.9
```

`phase_coherence_min=0.9` 对应约 25.6 度的圆标准差，用于排除随机相位噪声和
短时突发。

## 6. 参数与接口

### 6.1 参数变化

v3 继续使用：

- `fps`
- `fa`
- `P`
- `min_frames`
- `y_max`
- `min_group_amp_ratio`
- `max_group_amp_cv`
- `phase_coherence_min`（新增）

v3 不再使用旧算法的：

- `min_match_groups`
- `same_pattern_ratio`
- `slope_max`
- `min_amp_pos_ratio`

旧参数保留在 Python 参考实现中，仅用于结果对照，不进入移动端运行配置。

### 6.2 C++ 配置

```cpp
struct FlickerV3Config {
    uint32_t fps;
    uint16_t y_max = 16383;

    uint16_t input_rows = 48;
    uint16_t input_cols = 64;
    uint16_t block_rows = 12;
    uint16_t block_cols = 16;

    uint32_t enabled_drive_hz[16];
    uint32_t enabled_drive_count = 0;  // 0 表示启用全部有效档位

    float min_group_amp_ratio = 0.001f;
    float max_group_amp_cv = 0.4f;
    float phase_coherence_min = 0.9f;
};
```

### 6.3 检测结果

```cpp
struct FlickerBinResult {
    uint32_t apparent_frequency_hz;
    uint16_t flicker_block_num;
    uint8_t block_flags[192];

    // debug 构建下有效
    float mean_amp[192];
    float amp_cv[192];
    float phase_coherence[192];
};

struct FlickerV3Result {
    uint32_t frame_id;
    uint32_t bin_count;
    FlickerBinResult bins[8];
};
```

驱动频率映射表单独提供：

```cpp
struct FlickerDriveMapping {
    uint32_t apparent_frequency_hz;
    uint32_t drive_hz[16];
    uint32_t drive_count;
};
```

### 6.4 引擎接口

```cpp
class FlickerV3Detector {
public:
    explicit FlickerV3Detector(const FlickerV3Config& config);

    void reset();
    bool pushFrame(
        const uint16_t luma[48][64],
        uint32_t frame_id,
        FlickerV3Result* out
    );

private:
    uint16_t history_[30][192];
    uint32_t write_index_ = 0;
    uint32_t valid_count_ = 0;
    uint32_t frame_id_ = 0;
};
```

`pushFrame()` 写入 Detector 内部预分配缓冲区，返回指向内部结果或调用方传入
的 `out`；release 模式帧内不产生 `new`/`malloc`。

## 7. Android/JNI 接入

### 7.1 工程形态

建议新增：

```
native/
  CMakeLists.txt
  flicker_v3_detector.h
  flicker_v3_detector.cpp
  flicker_v3_jni.cpp
tools/
  generate_flicker_v3_config.py
```

核心库不依赖 OpenCV、NumPy 或 STL 动态分配。

### 7.2 JNI 接口

```java
class FlickerV3Detector {
    static {
        System.loadLibrary("flicker_v3");
    }

    native long init(int fps);
    native void reset(long handle);
    native boolean pushFrame(
        long handle,
        short[] luma48x64,
        int frameId,
        byte[] blockFlagsOut,
        int[] flickerBlockNumOut
    );
    native void release(long handle);
}
```

每帧 `short[]` 为 3072 个值，约 6KB，拷贝成本可接受；若后续需要进一步优化，
可改为直接 `ByteBuffer` 传递。

### 7.3 编译选项

- `-O2`；
- 关闭 RTTI 和异常（如业务允许）；
- release 不编译 debug 数组；
- 第一版单线程；NEON 和多线程只在真实设备性能不达标时加入。

## 8. 内存与性能预算

### 8.1 内存

| 项目 | 大小 |
| --- | ---: |
| 块历史 `30x192 uint16` | 11,520 B |
| 当前帧块缓冲 `192 uint16` | 384 B |
| 窗口暂存 | <1 KB |
| 每唯一 fa 的 flags | 192 B |
| 每唯一 fa 的 debug 数组 | ~2.3 KB（仅 debug） |

release 模式核心状态目标小于 128KB。

### 8.2 计算量

当前 30fps 只有 1 个唯一 `fa`：

```
192 blocks * 7 windows * 12 samples ≈ 16,128 次 Goertzel 迭代
```

加上 detrend，单帧核心计算量约为数万次整数/浮点运算，远低于 2ms 预算。

如果启用多个唯一 `fa`，计算量按唯一 bin 数线性增加。

## 9. 抗噪设计

### 9.1 噪声来源

- 传感器读出噪声；
- 低照度下的光子散粒噪声；
- 编码/压缩噪声；
- 自动曝光和自动增益造成的低频漂移。

### 9.2 抑制手段

- `4x4` 平均：空间独立噪声标准差降至原来的 1/4。
- 窗口 detrend：去除 DC 和线性曝光漂移。
- Goertzel：在 30 帧窗口内相干累加目标频率，随机噪声不会相干叠加。
- 幅度 CV：排除只在少数窗口出现的瞬态能量。
- 相位一致性：排除相位随机、短时突发和频率泄漏。

### 9.3 合成仿真参考

30fps/50Hz、方形周期信号、block 级高斯噪声：

| 幅度 | sigma | TPR | FPR |
| ---: | ---: | ---: | ---: |
| 20 | 5 | ~0.94 | ~0 |
| 20 | 10 | ~0.81 | ~0 |
| 30 | 10 | ~1.00 | ~0 |
| 50 | 20 | ~0.99 | ~0.02-0.05 |

如果噪声来自 48x64 像素级，4x4 平均后 block 级 sigma 除以 4，实际表现会优于
上述 block 级数字。真实阈值必须用手机实测数据标定。

## 10. 测试与验收

### 10.1 单元测试

- 块平均：全黑、全白、已知梯度、噪声帧，验证 48x64 -> 12x16 映射。
- 别名表：30fps 下 50/100/125/200/500Hz 都映射到 10Hz，结果一致。
- Goertzel 干净信号：幅度 20、无噪声时 192 个 block 全部检出。
- 纯噪声：TPR 接近 0，FPR 可控。
- 线性趋势：叠加曝光漂移后仍能检出。
- 幅度漂移：CV 超阈值时拒绝。
- 相位突变：`r < phase_coherence_min` 时拒绝。
- 历史不足：返回未就绪；写满 30 帧后恢复。
- 环形缓冲：连续 push 超过 30 帧，结果与整窗计算一致。

### 10.2 真实数据验收

- 至少一组静态场景、已知频闪光源的手机拍摄数据；
- 标定 `phase_coherence_min`、幅度阈值和 CV 阈值；
- 统计 TPR/FPR，而不是只看平均检出块数；
- 记录 P50/P95 和内存峰值。

### 10.3 性能验收

- 30fps、全部有效驱动频率档位；
- 真实手机 release 模式核心计算目标 <2ms，P95 <3ms；
- 内存峰值 <256KB（含 JNI 缓冲）；
- debug 构建不参与性能验收。

## 11. 实施步骤

1. 编写 `tools/generate_flicker_v3_config.py`，从 `detector_config.py` 生成
   唯一 `fa` 参数表和驱动频率映射。
2. 实现 `flicker_v3_detector.h/cpp` 核心。
3. 实现 C API 和 JNI 包装。
4. 实现 Python 参考实现，用于生成黄金测试数据。
5. 编写 C++/Python 单元测试、噪声测试和性能测试。
6. 在真实手机数据上标定阈值。

## 12. 假设与局限

- 场景为静态/近静态，曝光、增益和帧率在运行期间锁定。
- 输入固定为 `48x64`、14bit、行优先；其他格式由接入层转换。
- v1 只能检测“是否存在目标 `fa`”，不能区分混叠到同一 `fa` 的多个驱动频率。
- 波形幅度换算基于当前 `0/1` 周期模型，真实灯具波形需重新标定。
- 当前参数表只有 30fps 的完整有效档位；其他 fps 需先补齐参数。
- 运动场景、卷帘快门空间条纹、自动曝光自适应不进入 v1。
