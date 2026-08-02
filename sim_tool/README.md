# sim_tool

视频单帧频闪仿真工具，用于生成光强时序，并按相机帧率和曝光时间计算每帧亮度曲线，输出最大亮度、最小亮度及最大最小亮度比值。

## 文件

- `my_flicker.py`：主仿真脚本
- `demo/`：示例素材
- `docs/video_flicker_detection_design.md`：频闪检测设计文档

## 运行

在项目根目录执行：

```powershell
conda activate flicker
python sim_tool\my_flicker.py
```

也可以直接进入 `sim_tool` 目录执行：

```powershell
conda activate flicker
python my_flicker.py
```

Windows 上不要使用 `python3` 启动本工具：`flicker` 环境只提供 `python.exe`，`python3` 可能会命中 MSYS2 或系统自带 Python，导致找不到 `numpy` 等环境依赖。

如果不想手动激活环境，可以直接运行：

```powershell
run_sim.bat
```

## 依赖

依赖统一安装到 conda `flicker` 环境，版本记录在 `requirements.txt`。如需按清单补齐：

```powershell
python -m pip install -r sim_tool\requirements.txt
```
