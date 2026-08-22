# 技术设计：XOR 画布 AI 选图案

> 2026-08-04 双钻讨论收敛。原始问题见 [README.md](README.md)，依赖与许可证见 [DEPENDENCIES.md](DEPENDENCIES.md)。

2026-08-05 拆分设计方案文档。

## 一、背景与决策

### 问题定义

画布 512×512，8×8=64 个区域，每区一个整数参数 `a ∈ [-64, 63]`（128 种取值），图案是 `(dx^dy) & a` 的纯函数，负值只换黑白。参数空间 128^64 ≈ 10^134，暴力搜索不可能。

**核心问题：** 让一个纯文本 LLM 为这个纯函数图像选择参数。难点不在"画"（画是确定的纯函数），而在**评价闭环**——AI 看不见图，评价信号断链，它不知道下一步往哪改。

多次讨论，目前的结论：
1. "模拟点击"是伪命题：点击是人的接口（点→看→判断→再点），AI 的接口是文字/JSON。真正的问题是 AI 能否复现"看→想→改"评价循环。
2. 已经不用指标：AI 先想清楚图案意图，选值后用 view_region 回显的图案描述核对是否一致。v1 曾用代码指标（黑白平衡度）代替"看"，实测 AI 刷指标（Vibe Coding用的AI称之为“作弊解”），2026-08-07 已从 AI 目标中移除（见"指标 v1 的去留"）。
3. 图案可以用文字描述（见"图案语义字典"），AI 不用看图也能推理。

拆成三个子问题：**描述**（图案语义文字化，见 pattern_description.md）、**评价**（2026-08-07 起为意图自检，无量化目标）、**闭环**（function-calling 逐格点击 + view_region 回显核对 + evaluate 看进度）。

### 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 交互粒度 | 逐格点击（set_region/evaluate） | 最忠实于"模拟点击"；与 test-agent 的 move/view 同构，主循环可整体复用 |
| 评价闭环 | 意图自检（生成范式）：描述→选值→view_region 回显核对 | 忠于原始问题"根据图案描述选图案"，无需量化目标 |
| 图案描述 | pattern_description.md（手写参考，运行时读入系统提示词）+ pattern_desc.py 程序化描述 | 单一来源，AI 拿到详细图案语义后自行设计 |
| 指标 | 无（黑白平衡度仅作快照记录，供可视化旁观） | v1 曾作 AI 目标，实测刷指标作弊（见下），2026-08-07 移除 |
| 种子约束 | 保留（用户给 1 个初始值，AI 填其余；区域编号 `--seed-index` 0-63，默认 0） | 忠于原始问题 |
| 决策可视化 | 实时网页（stdlib http.server + 原生 JS 轮询） | 零新框架、零 CDN |
| 技术栈 | Python | 复用 test-agent 的 agent.py/logger.py/tools.py 模式 |
| 运行代码位置 | 根目录（上游参考 test-agent-src 已删除，改用上游链接 [minimal-agent](https://github.com/qp91mn64/minimal-agent)） | 导入/运行简单，来源边界清晰，依赖记账不混淆 |

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
| `xor_world.py` | 世界：网格状态、numpy 渲染、指标记录（旁观）、种子锁定、快照 |
| `pattern_desc.py` | 图案语义单一来源：读 pattern_description.md + 单值程序化描述 |
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
| `evaluate` | 无 | 返回整幅画布进度（已设定/未设定区域数） | view(整体) |

主循环（流式）：AI 调用工具 → 执行 → 结果文本回填 messages → 循环，直到 AI 不再调用工具或触达终止条件。每次工具调用后写一份 state.json + PNG 快照。模型可能一次请求返回多个工具调用（批量并行，实测一轮 16~31 个，或者一轮填满 63 格），点击按轮次（round）记录，供可视化按轮分组。

终止条件（仅前两条可调：`--rounds` / `--clicks`，其余为代码常量）：
- 最多 N 次循环（一次循环 = 一次 API 请求，默认 30，`--rounds`）
- 最多 M 次 set_region（默认 64，`--clicks`）
- 连续 3 次无效点击（越界 / 超范围 / 种子区域；常量 MAX_INVALID）
- AI 直接输出文字不再调用工具
- 工具调用总数上限 96 次（含 view_region / evaluate；常量 MAX_TOOL_CALLS）

成功判定不看模型输出，看画布状态：已点击的非种子区域数 == 63 即成功（覆盖率）。口径：未设定一律按"未点击过"统计（`unset_regions()` 以点击轨迹为准，成功判定与 evaluate 一致）——grid 初始全 0，0 既是纯黑图案也是未点击的初始态，刻意设 a=0（纯黑）算已设定，否则"刻意选纯黑"会被误报成未设定。

### 图案语义字典

来源：手写参考文档 [pattern_description.md](pattern_description.md)（低/高三位感知描述、叠加规则、组合建议），运行时由 `pattern_desc.pattern_doc()` 读入并嵌入系统提示词；`pattern_desc.pattern_description(a)` 提供单值的程序化描述（工具返回用），两者口径一致。核心事实：
- 每个区域值 `a` 决定 `(dx^dy) & a` 的图案（白格 = (dx^dy)&a != 0）；**单个位 2^k → 2^k×2^k 棋盘格**。
- a=0 纯黑；a=1 是 1px 细棋盘格（50% 黑）；a=2 是 2×2 块棋盘；a=4 是 4×4 块棋盘。
- 多位置位 = 各棋盘格的**白格并集**（黑格变少）；a=63（全位）黑格只剩主对角线，约 98% 白——不是最密。
- 负值 = 反色版（对应 ~a 的图案，黑白互换）；a=-1 全白。

### 指标 v1 的去留（2026-08-07）

v1 曾以黑白平衡度 `1 - |黑像素占比 - 0.5| × 2`（渲染后数像素，精确）作为 AI 的优化目标，为的是给"评价闭环"一个可量化的信号。**已知坑（诚实记录）**：单指标存在作弊解——任一单一位值（1/2/4/8/16/32，黑白各半）即可满分。2026-08-05 实测证实：两轮运行均以 1.000 满分收尾，产出图案单调（"1,2,4,8,16,32 循环渐变"和"大片 a=1 + 单个 15"）。

这与原始问题（"根据图案描述选图案"）相悖：AI 不是在选图案，而是在刷分数。2026-08-07 回到生成范式：指标不再是 AI 目标，`evaluate` 只报进度；`metric()`/`metric_history` 保留，仅作快照记录供可视化旁观。

### 实时可视化

**架构一句话**：agent 每走一步把状态落盘到 `output/`（JSON+PNG），`web/index.html` 每 1 秒轮询 `output/state.json` 并渲染；中间只有一个标准库 HTTP 服务，无框架、无 CDN、无 WebSocket。

**服务端（agent.py，标准库实现）**
- `start_server()`：`socketserver.ThreadingTCPServer` 子类（`allow_reuse_address=False`，Windows 端口复用坑：必须显式关闭，否则固定端口 fallback 永不触发、与占用者共存，见 context-log）+ `SimpleHTTPRequestHandler`（`directory=BASE`，BASE=脚本所在目录，不依赖 cwd，防止从系统盘启动暴露目录）。固定绑定 `127.0.0.1`，端口默认固定 8765（被占时自动 fallback 随机并打印实际端口），`--port` 可覆盖（0=随机）；后台守护线程运行。
- 路径白名单：`/` 或 `/index.html` 改写为 `/web/index.html`；只放行 `/web/` 与 `/output/` 前缀的文件，以 `/` 结尾的目录请求一律 404（不列目录、不枚举文件），其余路径一律 404——所以 `/output/step_003.png`、`/output/state.json` 可直接 fetch，而 `.env`、`*.py` 等根目录文件不可达。
- 敏感文件拒绝（白名单之外的第二道防线）：`.env` 等点文件、`*.log`、`probe_*`/`_` 前缀的探针临时脚本一律返回 404，防止密钥/日志被本机 HTTP 下载（`_is_sensitive_path`）。`do_GET` 先 `unquote` 再 `normpath` 再检查，URL 编码的路径穿越（`%2e%2e`=`.`/`..`、`%2eenv`=`.env`、嵌套 `%252e`）与含 ASCII 控制字符（`%00`/`%0a`）的路径同样 404（实测可下载 `.env`，见 context-log/2026-08-22_HTTP路径穿越密钥泄露.md）。
- 自动打开浏览器：启动后 `webbrowser.open(url)`（`--no-open` 可关），避免思考流把网址刷出视野。
- `xor_world.snapshot(step)`：**每次工具调用后**渲染 512×512 PNG → 写 `output/step_NNN.png`；更新 `state["image"]`（URL 路径）；指标追加进 `metric_history`；写 `output/state.json`（含 grid、seed_index、seed_value、clicks（每条含 reasoning、round）、metric_history、status、final_reason、image）。启动时先做一次 step 0 快照。
- 无密钥不退出：启动后若 `.env` 未配置 `DEEPSEEK_API_KEY`，不报错退出——`state["status"]` 置为 `no_key` 并快照，网页显示配置引导（复制 env.example 填 key）；主线程每 2 秒重读 `.env`，检测到密钥后 `status` 恢复 `running` 并自动开始运行（无需重启）。不能在这里直接 `SystemExit`：进程退出会杀死服务线程，浏览器只剩一个连不上的死页面。
- 流式实时思考：API 调用用 `stream=True`，思考增量（reasoning_content）实时打印到控制台，并原子写 `output/reasoning_live.json`（`{round, reasoning}`）供 web 轮询；一轮结束后由下一轮覆盖，运行结束删除。流式仅改变传输方式，不改变 token 计费，无额外成本。

**客户端（web/index.html，原生 JS）**
- `setInterval(refresh, 1000)` 每 1 秒 `fetch('/output/state.json', {cache:'no-store'})`，拿到状态后整体重渲染；`refreshLive` 同频轮询 `/output/reasoning_live.json` 显示流式实时思考（文件不存在即隐藏面板）。
- 渲染五块：当前画布（`<img>` 指向 state.image，加 `?t=时间戳` 防缓存）；参数热力图（canvas 2D，值 -64..63 映射色相，低值红、高值蓝）；黑白平衡度折线（canvas 2D）；点击轨迹时间线（按 round 分组、最新 15 组倒序，一组显示一次思考 + 该批格子，reasoning 经 `esc()` 转义防 HTML 注入）；实时思考面板（流式等待期间显示模型思考增量）。

**为什么用"轮询文件"而不是实时推送**：主循环是同步的（一轮 = 一次 API 请求），每步落盘一次，与 1 秒轮询天然匹配，实现最简单。代价是刷新延迟 ≤1 秒，对观察 AI 的点击节奏足够。流式等待期间通过 `reasoning_live.json` 提供实时反馈——首次请求实测约 47 秒纯等待，无反馈会误以为卡死。

**地址与端口（唯一约定）**：HTTP 服务固定绑定 `127.0.0.1`（本机回环地址；访问 URL 一律用 `127.0.0.1`，不用 `localhost` 字样），端口默认固定 **8765**（避开 8123 等知名端口；被占时自动 fallback 随机并打印实际端口），`--port` 可覆盖（0=随机）。控制台打印的访问地址格式固定为 `http://127.0.0.1:<port>/`，并在启动后自动打开浏览器（`--no-open` 关闭）。v1 仅支持本机浏览器访问，不绑 `0.0.0.0`、不做跨设备方案。

## 四、实验设计与扩展

### 基线对照

生成范式下没有量化目标，random 对照不能靠数值比较，改为人工评价。原始画布作品 Interactive Drawing XOR.html，改用随机初始化 grid，打开之后直接保存图片，徒手比较即可，不需要单独脚本。

### 后续扩展点

1. 支持输入一个主题，AI按照主题创作。
2. 批量提交模式（set_regions 一次改多个区域）。
