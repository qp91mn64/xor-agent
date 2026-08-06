# Agent 测试

现有自己写的 Interactive Drawing XOR，点击不同区域即可选择不同的图案填充画布，按下 `s` 可以保存图片。

那么通过点击来选择图案的过程，能不能用AI来模拟呢？或者说，由于选择图案的本质是选择参数，能不能让AI来给不同区域选取不同的参数值？

比如说，指定一个初始值，让AI自行选择剩余区域的值，然后画图的代码读取AI选择的参数值，画出图形？

## 状态

设计见 [技术设计](TECH_DESIGN.md)，依赖与许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)。核心已实现：Agent 主循环 + XOR 画布世界 + 实时决策可视化。

## 快速开始

```powershell
python -m pip install -r requirements.txt   # 安装依赖（openai/dotenv/numpy/pillow）
# 复制 env.example 为 .env，填入 DEEPSEEK_API_KEY
python agent.py --seed 5 --detailed         # 运行；自动打开可视化页面，控制台打印网址
# 可选：--seed-index 0-63 指定种子区域编号（默认 0）；--port 端口（默认固定 8765，被占自动 fallback 随机）；
# --no-open 不自动打开浏览器；--rounds/--clicks 调整终止上限
```

打开浏览器访问打印的网址，可实时看到 AI 每次"点击"后的画布、参数热力图、黑白平衡度演化与 AI 思考（决策可视化；等待模型响应时，控制台与网页实时显示思考过程）。

## 结构

```
test-agent-2/
├── TECH_DESIGN.md           # 技术设计（背景与决策/整体架构/关键机制/实验）
├── DEPENDENCIES.md          # 依赖与许可证
├── agent.py                # Agent 主循环 + 内嵌可视化 HTTP 服务
├── xor_world.py            # XOR 画布世界：状态/渲染/指标/图案语义/快照
├── tools.py                # set_region / view_region / evaluate 工具
├── logger.py               # 日志（复用 test-agent，MIT）
├── selftest.py             # 离线自测（不依赖 API）
├── web/index.html          # 实时决策可视化页（原生 JS，轮询 state.json）
├── context-log/            # 排查/实测记录（一个主题一个文件）
├── Interactive Drawing XOR.html  # 原始画布作品
├── requirements.txt / env.example / .gitignore
└── output/                 # 运行产物：state.json + step_*.png（已 gitignore）
```
