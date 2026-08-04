# Context Log - 双钻讨论：AI 为 XOR 画布选择图案参数（设计阶段）

创建时间: 2026-08-04

## 背景与目标
原始问题见 README.md：能否用 AI 模拟"点击选择图案"？由于选图案本质是选参数，能否让 AI 给 64 个区域（每区 128 种取值）选参数？
目标（用户确认）：探索 AI 创造力边界——纯文本 LLM 能否在 128^64 ≈ 10^134 的参数空间里做出超随机、有设计感的选择。

## 关键认识（讨论过程的 reframe）
1. "模拟点击"是伪命题：点击是人的接口（点→看→判断→再点），AI 的接口是文字/JSON。等价问题是 AI 能否复现"看→想→改"评价循环。
2. 评价信号不一定需要视觉：图案是纯函数，代码指标可代替"看"。
3. 图案可文字精确描述（位平面叠加，a 的二进制第 k 位 = 块大小 2^k 的棋盘格），AI 不用看图也能推理。
4. 单一指标有作弊解（全填 ±63 满棋盘格即满分）：v1 目的是跑通闭环+可视化，审美要等指标库扩展。

## 排除的备选方案
| 备选 | 排除原因 |
|---|---|
| 每轮整网格 JSON 提交 | 不贴"模拟点击"，交互不够 agent 化（后选了逐格点击） |
| 视觉 API 评价（VLM） | 多一个模型依赖+成本，v1 违反"尽可能简单" |
| Flask 实时网页 | 标准库 http.server + 原生 JS 轮询即够，零新框架 |
| 复用早期 DeepSeek API 测试脚本的调用代码 | 层次太低；test-agent 已有完整主循环+工具+日志，应复用它 |
| LangChain 等 agent 框架 | 框架即复杂化，裸 SDK 主循环已够 |

## 相关文件
- DESIGN.md - 设计文档（结论存档，4 顶级章节各 ≤4 子标题）
- README.md - 原始问题 + 状态（保持简洁）
- test-agent-src/ - 上游参考代码（test-agent 原样，运行代码在根目录）

## 决策收敛（2026-08-04）
| 决策点 | 选择 |
|---|---|
| 交互粒度 | 逐格点击 set_region / view_region / evaluate（与 maze 的 move/view 同构） |
| 评价 | 代码指标迭代（v1 黑白平衡度 = 1 - |黑占比-0.5|×2） |
| 种子约束 | 保留（用户给 1 个初始值，AI 填其余） |
| 可视化 | 实时网页（stdlib http.server + 原生 JS 轮询 state.json） |
| 复用 | test-agent 骨架（agent.py / logger.py / tools.py 模式），运行代码放根目录 |
| 依赖 | openai / dotenv / numpy / pillow，全部宽松许可证，无 GPL |

## 待办/未决
- [ ] 实现代码（agent.py / xor_world.py / tools.py / web/index.html / requirements.txt）
- [ ] 发布前删除 test-agent-src，改用上游链接 https://github.com/qp91mn64/minimal-agent
- [ ] 指标作弊解的取舍（v1 接受，v2 扩指标库：多样性/对称度/渐变度/纹理密度）
- [ ] random 基线对照（回答"AI 是否胜过运气"）

## 文档修改记录
### 2026-08-04
- 创建 DESIGN.md（双钻结论存档）
- README.md 精简为原始问题 + 状态 + 结构（2 个同级标题）
- 发布计划同步：README/DESIGN 标注 test-agent-src 发布时删除，改用上游链接
- 本项目 context-log 首次建档
