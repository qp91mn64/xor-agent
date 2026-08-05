# 技术设计：XOR 画布 AI 选图案

> 2026-08-04 双钻讨论收敛。原始问题见 [README.md](README.md)，依赖与许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)。

2026-08-05 拆分设计方案文档。

## 一、背景与决策

### 问题定义

画布 512×512，8×8=64 个区域，每区一个整数参数 `a ∈ [-64, 63]`（128 种取值），图案是 `(dx^dy) & a` 的纯函数，负值只换黑白。参数空间 128^64 ≈ 10^134，暴力搜索不可能。

**核心问题：** 让一个纯文本 LLM 为这个纯函数图像选择参数。难点不在"画"（画是确定的纯函数），而在**评价闭环**——AI 看不见图，评价信号断链，它不知道下一步往哪改。

讨论中澄清的三个关键认识：
1. "模拟点击"是伪命题：点击是人的接口（点→看→判断→再点），AI 的接口是文字/JSON。真正的问题是 AI 能否复现"看→想→改"评价循环。
2. 评价信号不一定需要视觉：图案是纯函数，代码可直接算指标（黑白比例等），"计算"可代替"看"。
3. 图案可以文字精确描述（见"图案语义字典"），AI 不用看图也能推理。

拆成三个子问题：**描述**（图案语义文字化）、**评价**（v1 用代码指标代替视觉）、**闭环**（function-calling 逐格点击 + evaluate 观察）。

### 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 交互粒度 | 逐格点击（set_region/evaluate） | 最忠实于"模拟点击"；与 test-agent 的 move/view 同构，主循环可整体复用 |
| 评价闭环 | 代码指标迭代（v1 单指标） | 纯文本闭环，无视觉依赖，最贴 agent 本质 |
| 指标配置 | 黑白平衡度 1 个 | 先跑通闭环，指标库是后续扩展点 |
| 种子约束 | 保留（用户给 1 个初始值，AI 填其余） | 忠于原始问题 |
| 决策可视化 | 实时网页（stdlib http.server + 原生 JS 轮询） | 零新框架、零 CDN |
| 技术栈 | Python | 复用 test-agent 的 agent.py/logger.py/tools.py 模式 |
| 运行代码位置 | 根目录（test-agent-src 仅作上游参考） | 导入/运行简单，来源边界清晰，依赖记账不混淆 |

## 二、整体架构

**一句话**：DeepSeek API ↔ `agent.py` 主循环 → `tools.py` 工具分派 → `xor_world.py`（状态/渲染/指标）→ `output/` 快照 → `web/index.html` 轮询可视化。

```
AI（DeepSeek function calling）
   │ ① 调用工具（set_region / view_region / evaluate）
   ▼
agent.py 主循环 ──执行──▶ tools.py 分派 ──▶ xor_world.py（改 grid / 渲染 / 算指标）
   │                        │
   │ ② 每步 snapshot()       └── 返回结果文本 ──▶ 回填 messages，进入下一轮
   ▼
output/state.json + step_NNN.png
   │ ③ web 每 1 秒轮询
   ▼
web/index.html（画布 / 热力图 / 指标折线 / 点击轨迹）
```

模块职责：

| 模块 | 职责 |
|---|---|
| `agent.py` | 主循环：API 调用、assistant_message 回填、终止条件、成功判定；内嵌 stdlib HTTP 服务 |
| `tools.py` | 工具 schema（发给模型）与 execute_tool 执行分派 |
| `xor_world.py` | 世界：网格状态、图案语义描述、numpy 渲染、指标、种子锁定、快照 |
| `logger.py` | 时间戳日志（复用 test-agent，MIT） |
| `web/index.html` | 轮询 state.json 并渲染（原生 JS，无框架） |
| `selftest.py` | 离线自测（不依赖 API，交付前必跑） |

## 三、关键机制

### 工具协议与主循环

工具（schema 见 tools.py）：

| 工具 | 参数 | 作用 | 对应 maze |
|---|---|---|---|
| `set_region` | index(0-63), value(-64..63) | 点一个区域，设定参数值（种子区域锁定） | move |
| `view_region` | index | 查看区域当前值和图案描述 | view(局部) |
| `evaluate` | 无 | 渲染整幅并返回指标与未设定区域数 | view(整体) |

主循环：AI 调用工具 → 执行 → 结果文本回填 messages → 循环，直到 AI 不再调用工具或触达终止条件。每次工具调用后写一份 state.json + PNG 快照。

终止条件（参数可调，`--rounds` / `--clicks`）：
- 最多 N 次循环（一次循环 = 一次 API 请求，默认 30）
- 最多 M 次 set_region（默认 64）
- 连续 3 次无效点击（越界 / 超范围 / 种子区域）
- AI 直接输出文字不再调用工具

成功判定不看模型输出，看画布状态：已点击的非种子区域数 == 63 即成功（覆盖率），并记录最终指标。

### 图案语义字典

- 每个区域值 `a` 决定 `(dx^dy) & a` 的图案（白格 = (dx^dy)&a != 0）；**单个位 2^k → 2^k×2^k 棋盘格**。
- a=0 纯黑；a=1 是 1px 细棋盘格（50% 黑）；a=2 是 2×2 块棋盘；a=4 是 4×4 块棋盘。
- 多位置位 = 各棋盘格的**白格并集**（黑格变少）；a=63（全位）黑格只剩主对角线，约 98% 白——不是最密。
- 负值 = 反色版（对应 ~a 的图案，黑白互换）；a=-1 全白。

### 指标 v1

`黑白平衡度 = 1 - |黑像素占比 - 0.5| × 2`（渲染后数像素，精确）。

**已知坑（诚实记录）**：单指标存在作弊解——全填 a=1（或其他单一位值，黑白各半）即满分。v1 目的不是求好作品，而是把"闭环+可视化"跑通。后续扩展：图案多样性、网格对称度、邻域渐变度、纹理密度，再谈审美。

### 实时可视化

**架构一句话**：agent 每走一步把状态落盘到 `output/`（JSON+PNG），`web/index.html` 每 1 秒轮询 `output/state.json` 并渲染；中间只有一个标准库 HTTP 服务，无框架、无 CDN、无 WebSocket。

**服务端（agent.py，标准库实现）**
- `start_server()`：`ThreadingHTTPServer` + `SimpleHTTPRequestHandler`（`directory=项目根目录`），固定绑定 `127.0.0.1`，端口默认 0（由系统随机分配空闲端口），`--port` 可指定固定值；后台守护线程运行。
- 路径映射：`/` 或 `/index.html` 改写为 `/web/index.html`；其余按项目根目录静态提供，所以 `/output/step_003.png`、`/output/state.json` 可直接 fetch。
- `xor_world.snapshot(step)`：**每次工具调用后**渲染 512×512 PNG → 写 `output/step_NNN.png`；更新 `state["image"]`（URL 路径）；指标追加进 `metric_history`；写 `output/state.json`（含 grid、clicks、reasoning、status、final_reason）。启动时先做一次 step 0 快照。

**客户端（web/index.html，原生 JS）**
- `setInterval(refresh, 1000)` 每 1 秒 `fetch('/output/state.json', {cache:'no-store'})`，拿到状态后整体重渲染。
- 渲染四块：当前画布（`<img>` 指向 state.image，加 `?t=时间戳` 防缓存）；参数热力图（canvas 2D，值 -64..63 映射色相，低值红、高值蓝）；黑白平衡度折线（canvas 2D）；点击轨迹时间线（最新 30 条倒序，reasoning 经 `esc()` 转义防 HTML 注入）。

**为什么用"轮询文件"而不是实时推送**：主循环是同步的（一轮 = 一次 API 请求），每步落盘一次，与 1 秒轮询天然匹配，实现最简单。代价是刷新延迟 ≤1 秒，对观察 AI 的点击节奏足够。

**地址与端口（唯一约定）**：HTTP 服务固定绑定 `127.0.0.1`（本机回环地址；访问 URL 一律用 `127.0.0.1`，不用 `localhost` 字样），端口默认 0（由系统随机分配空闲端口），`--port` 可覆盖。控制台打印的访问地址格式固定为 `http://127.0.0.1:<port>/`。v1 仅支持本机浏览器访问，不绑 `0.0.0.0`、不做跨设备方案。

## 四、实验设计与扩展

### 基线对照

回答"AI 是否胜过运气"：内置 random 策略，随机 set_region 同样的点击次数，跑同样的指标。比较最终指标，得出"纯文本 AI 在这个空间里是否超越了随机"——这是"探索 AI 创造力边界"的可量化答案。

### 后续扩展点

1. 指标库扩展：图案多样性、网格对称度、邻域渐变度、纹理密度 + 复合分。
2. 视觉 API 评价（AI 生成 → 渲染 → 视觉模型看图 → 文字评价 → 再改）。
3. 批量提交模式（set_regions 一次改多个区域）。
