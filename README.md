# flicker_detect

视频频闪检测项目。当前包含一个 Python Web 仿真工具，提供参数编辑、即时仿真和卷帘快门条纹可视化能力。

## 项目文档

- `README.md`：项目描述与使用入口
- `PLAN.md`：设计构思、实现约束和推进计划

## 当前功能

- 9 个仿真参数编辑：帧率、曝光时间、仿真时间、DC/AC 参数
- 滑块与数值输入双向同步，曝光时间上限跟随帧率联动
- 参数变更后自动防抖并重新仿真
- 绘制 DC、AC、合成光源、帧强度四条曲线
- 按 4K 2160 行模拟行曝光错位，输出单帧频闪条纹图
- 左侧参数区支持进度条选择帧，查看仿真时间内任意帧的行曝光条纹
- 输出帧数、最大/最小亮度比值

## 运行

```powershell
conda activate flicker
python main.py
```

启动后访问 `http://127.0.0.1:8765`。也可以指定端口：

```powershell
python main.py --port 9000
```

## 项目结构

- `flicker_app/engine.py`：仿真计算引擎
- `flicker_app/server.py`：Python 标准库 HTTP 服务
- `flicker_app/static/`：HTML、CSS、Canvas 前端
- `tests/`：引擎与服务测试

## 环境

统一使用 conda 虚拟环境 `flicker`。新增依赖安装到该环境中，并在 `requirements.txt` 中记录。
