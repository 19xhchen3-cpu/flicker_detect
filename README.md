# flicker_detect

视频频闪检测项目。项目使用单一 Web 工作台，同时提供视频预处理与频闪检测、光源频闪仿真两个视图。

## 项目文档

- `README.md`：项目描述与使用入口
- `PLAN.md`：设计构思、实现约束和推进计划

## 当前功能

- 视频上传、灰度化、gamma 提亮和 `cols × rows` 分块预处理
- 按 fps 查表选择检测频率，参数变更后自动或手动重算
- 检测块叠加、检测块数图表、逐块悬停诊断
- 历史亮度多块选择与同图曲线对比，显示周期组边界和有效/主导组
- 9 个仿真参数编辑：帧率、曝光时间、仿真时间、DC/AC 参数
- 滑块与数值输入双向同步，曝光时间上限跟随帧率联动
- 参数变更后自动防抖并重新仿真
- 绘制 DC、AC、合成光源、帧强度四条曲线
- 按 4K 2160 行模拟行曝光错位，输出单帧频闪条纹图
- 输出帧数、最大/最小亮度比值

## 运行

```powershell
conda activate flicker
python main.py
```

启动后访问 `http://127.0.0.1:8766`。页面顶部导航栏可在“视频检测”和“频闪仿真”之间切换。也可以指定端口或自动打开浏览器：

```powershell
python main.py --port 9000 --open-browser
```

## 项目结构

- `main.py`：统一 Web 工作台入口
- `app/`：统一前端页面、前端脚本和 HTTP 服务
- `flicker_detect/`：视频预处理、检测配置和检测算法模块
- `flicker_app/engine.py`：仿真计算引擎
- `flicker_app/server.py`：旧版仿真服务，保留兼容
- `tests/`：引擎与服务测试

## 环境

统一使用 conda 虚拟环境 `flicker`。新增依赖安装到该环境中，并在 `requirements.txt` 中记录。
