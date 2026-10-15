# XOR 画布选择图案 Agent

有自己的作品 [Interactive Drawing XOR](https://openprocessing.org/@u594485/2922410)，点击不同区域即可选择不同的图案填充画布，按下 `s` 保存图片。

那么通过点击来选择图案的过程，能不能用 AI 来模拟呢？

或者说，由于本质是选择参数，参数值的除了来自现成文件、鼠标键盘输入、随机数值/噪声、代码编写有规律的数据以外，能不能来自 AI 的“选择”呢？

比如说，指定一个初始值，让 AI 自行选择剩余区域的参数值，然后其余代码把参数值转化为图形？

注意：这里的 Agent 能产出的图片，都能通过原 Interactive Drawing XOR 复现，本质上是现有参数空间里面选择一组参数，不会画出任何全新类型，不同于常见的 AI 画图方式，亦不能仅从像素判断是否有 AI 介入画图。

## 实现方式

Vibe Coding。

AI 编程工具：目前用的 DeepSeek Harness（DSH，会话配置里的模型名为 `deepseek-flash`）。最开始用的 Trae，2026-10-15 迁入 DSH。

模型，开发以及项目内 Agent 用：最开始用的 Deepseek-V4-Flash-0731；后续由于旧模型下线，改用 DeepSeek-V4.1-Flash。通过 DeepSeek API 调用。

起步：从复用之前的项目 [minimal-agent](https://github.com/qp91mn64/minimal-agent) 代码（偷懒，免得重新发明轮子）开始。

## 状态

设计见 [技术设计](TECH_DESIGN.md)，依赖与许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)。核心已实现：Agent 主循环 + XOR 图案绘制 + 实时决策可视化。AI 按 [图案描述](pattern_description.md) 自主设计画布。

目前没有量化指标，Agent 画完即可。早期开发过程中，AI 曾引入黑白平衡度指标，实测发现问题之后弃用。

## 快速开始

```powershell
# 安装依赖（openai/dotenv/numpy/pillow）
python -m pip install -r requirements.txt

# 复制 env.example 为 .env，填入 DEEPSEEK_API_KEY

# 运行；自动打开可视化页面
python agent.py --seed 5
# 可选：--seed-index -64 - 63 指定种子区域编号（默认 0）；
##     --port 端口（默认固定 8765，被占自动 fallback 随机）；
##     --no-open 不自动打开浏览器；--rounds/--clicks 调整终止上限
```
未配置 `DEEPSEEK_API_KEY` 时程序不退出：服务照常启动，网页显示配置引导，填好保存后自动开始运行（无需重启）。

打开浏览器，可实时看到 AI 每次"点击"后的画布、参数示意图与 AI 思考过程。AI 思考过程在命令行与可视化界面同步显示。

## 项目结构

```
xor-agent/
├── .agents
│   ├── skills/                     # 可复用方法（DSH 自动发现）
│   │   └── context-log/                # 存档上下文 skill
│   └── skills-pending/             # 暂不参与自动发现
│       └── browser-ui-test/            # 用探针脚本的浏览器 UI 测试 skill（待按 DSH 工具链重写）
├── context-log/                    # 排查/实测记录
├── tests/                          # 测试
│   ├── selftest.py                     # 离线自测
│   ├── sim_agent.py                    # 模拟 Agent 回放器
│   └── example_data*.txt               # 模拟 Agent 样例数据
├── web/index.html                  # 可视化
├── .gitignore
├── agent.py                        # Agent 主循环 + 内嵌可视化 HTTP 服务
├── DEPENDENCIES.md                 # 依赖与许可证
├── env.example                     # 环境变量模板
├── Interactive Drawing XOR.html    # 原始图案来源
├── logger.py                       # 日志记录
├── tools.py                        # 工具定义
├── pattern_desc.py                 # 图案描述处理
├── pattern_description.md          # 图案描述文档
├── requirements.txt                # 装依赖用
├── TECH_DESIGN.md                  # 技术设计
├── xor_world.py                    # XOR 图案绘制
└── output/                         # 画出的图片在这里；已 gitignore
```

## 许可证

MIT
