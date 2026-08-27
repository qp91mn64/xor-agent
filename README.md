# XOR 画布选择图案 Agent

现有自己写的 Interactive Drawing XOR，点击不同区域即可选择不同的图案填充画布，按下 `s` 可以保存图片。

那么通过点击来选择图案的过程，能不能用AI来模拟呢？或者说，由于选择图案的本质是选择参数，能不能让AI来给不同区域选取不同的参数值？

比如说，指定一个初始值，让AI自行选择剩余区域的值，然后画图的代码读取AI选择的参数值，画出图形？

实现方式：Vibe Coding，Trae 接入自定义模型，用 DeepSeek API，deepseek-v4-flash（正式版已经上新）。从复用之前的项目 [minimal-agent](https://github.com/qp91mn64/minimal-agent) 代码（偷懒，免得重新发明轮子）开始。

## 状态

设计见 [技术设计](TECH_DESIGN.md)，依赖与许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)。核心已实现：Agent 主循环 + XOR 画布世界 + 实时决策可视化。AI 按 [图案描述](pattern_description.md) 自主设计画布（生成范式，不追求量化指标）。

## 快速开始

```powershell
python -m pip install -r requirements.txt   # 安装依赖（openai/dotenv/numpy/pillow）
# 复制 env.example 为 .env，填入 DEEPSEEK_API_KEY
python agent.py --seed 5 --detailed         # 运行；自动打开可视化页面，控制台打印网址
# 可选：--seed-index 0-63 指定种子区域编号（默认 0）；--port 端口（默认固定 8765，被占自动 fallback 随机）；
# --no-open 不自动打开浏览器；--rounds/--clicks 调整终止上限
```
未配置 `DEEPSEEK_API_KEY` 时程序不退出：服务照常启动，网页显示配置引导，填好保存后自动开始运行（无需重启）。

打开浏览器访问打印的网址，可实时看到 AI 每次"点击"后的画布、参数热力图与 AI 思考（决策可视化；等待模型响应时，控制台与网页实时显示思考过程）。

## 结构

```
xor-agent/
├── .trae/skills/           # 可复用方法
│   ├── browser-ui-test/        # 用探针脚本的浏览器 UI 测试 skill
│   └── context-log/            # 存档上下文 skill（自己用 AI 写的，在 Trae 里面用了几个月）
├── TECH_DESIGN.md           # 技术设计（背景与决策/整体架构/关键机制/实验）
├── DEPENDENCIES.md          # 依赖与许可证
├── agent.py                # Agent 主循环 + 内嵌可视化 HTTP 服务
├── xor_world.py            # XOR 画布世界：状态/渲染/快照
├── pattern_desc.py         # 图案语义单一来源：读 pattern_description.md + 程序化描述
├── pattern_description.md  # 图案语义参考文档（手写，AI 的设计依据）
├── tools.py                # set_region / view_region / evaluate 工具
├── logger.py               # 日志（复用 test-agent，MIT）
├── tests/                  # 自测与测试工具
│   ├── selftest.py         # 离线自测（不依赖 API，交付前必跑）
│   ├── sim_agent.py        # 模拟 Agent 回放器
│   └── example_data.txt    # 模拟 Agent 样例数据（真实日志回放）
├── web/index.html          # 实时决策可视化页（原生 JS，SSE 订阅 /events 实时渲染）
├── context-log/            # 排查/实测记录（一个主题一个文件）
├── Interactive Drawing XOR.html  # 原始画布作品
├── requirements.txt / env.example / .gitignore
└── output/                 # 运行产物：state.json（已 gitignore）
```
