# flicker_detect

视频频闪检测项目，目标是针对不同视频帧率提供多种检测方案，量化视频中的频闪现象，并输出频率、强度等指标。

## 项目文档

- `README.md`：项目描述与使用入口
- `PLAN.md`：设计构思、实现约束和推进计划
- `sim_tool/README.md`：视频单帧频闪仿真工具说明

## 环境

项目统一使用 conda 虚拟环境 `flicker`，当前环境已包含：

- Python 3.13
- NumPy 2.4
- OpenCV 4.13
- Matplotlib 3.11

后续新增依赖统一安装到该环境中，并在项目文档中记录，不使用系统或全局 Python 环境。

## 当前状态

- 仓库已初始化并连接到 GitHub 远端
- 设计与实现约束记录在 `PLAN.md`
- 仿真工具已迁入 `sim_tool`，包含核心仿真脚本、demo 和设计文档
