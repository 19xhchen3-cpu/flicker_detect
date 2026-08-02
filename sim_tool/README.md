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

## 依赖

依赖统一安装到 conda `flicker` 环境，版本记录在 `requirements.txt`。如需按清单补齐：

```powershell
python -m pip install -r sim_tool\requirements.txt
```
