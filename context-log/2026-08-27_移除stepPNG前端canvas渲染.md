# 移除 step_NNN.png，前端 canvas 渲染画布（2026-08-27）

> 决策类型：消除冗余 + 前端渲染替代。关联：context-log/2026-08-25_待修问题清单.md 第 5 条「不依赖图片的加载方案」。

## 面对的问题

用户参考 Interactive Drawing XOR.html（纯前端 p5.js 渲染，注释明确 "no data saved as a file"），怀疑本项目每次工具调用生成的 `output/step_NNN.png` 是多余的。

## 事实核查（改动前）

- PNG 使用链：`xor_world.snapshot(step)` 每次工具调用渲染 512×512 PNG → `output/step_NNN.png`，URL 写入 `state["image"]`；**唯一消费方**是 `web/index.html` 的「当前画布」`<img src=state.image>`。
- 前端已有完整 `grid`（64 值）+ `seed_index`（state.json / SSE 均全量推送），热力图已用 canvas 按 grid 绘制——画主画布只需复刻 `(dx^dy) & a`。
- 渲染是纯函数，Interactive Drawing XOR.html 即纯 JS 26 万像素循环实现，可行且性能无压力。

## 方案

前端用 `<canvas id="canvas-main">` 按 grid 复刻 `(dx^dy)&a` 逐像素渲染；后端 `snapshot()` 只写 state.json（SSE/轮询数据源必须保留）。

## 实施内容

| 文件 | 改动 |
|---|---|
| xor_world.py | `snapshot(out_dir)` 去 step 参数、只写 state.json；删 `save_png`、`state["image"]`、PIL 导入 |
| agent.py | `snap()` 去 step 参数；删 step 计数（5 处） |
| tests/sim_agent.py | `agent.snap()` 去 step 参数（3 处） |
| web/index.html | `<img id="canvas-img">` → `<canvas id="canvas-main" width="512" height="512">`；新增 `drawCanvas(grid)`（ImageData 逐像素）；删 `lastImage` 重载逻辑 |
| tests/selftest.py | 快照断言只保留 state.json |
| requirements.txt / DEPENDENCIES.md | 删 pillow（PIL 不再使用） |
| README.md / TECH_DESIGN.md | 同步去掉 step_NNN.png / state.image 描述 |
| context-log/2026-08-25_待修问题清单.md | 第 5 条补 ✅ 已实施 |

## 关键实现细节（坑）

- **JS 负数 mask 不能用 `~a`**：JS `~` 是 32 位补码，`~(-1)` = -2（0xFFFFFFFE），而 numpy `~(-1)` = 0。前端必须写 `a >= 0 ? a : -a - 1`（numpy 语义等价），否则负值区域图案错乱。
- `render()`（numpy）保留：`black_ratio()` 与 selftest 渲染正确性验证仍用它。

## 测试方式与结果

1. `python tests/selftest.py`：通过（退出码 0，snapshot 断言改后无 step_*.png）。
2. 临时探针（output/，用完即删）：直接回放 example_data.txt 的 72 次工具调用 → 63/63 点击、coverage 63；`snapshot` 后 state.json 含 63 条 clicks、无 image 字段；output/ 根目录无 step_*.png。
3. 浏览器实测（探针写完整 state.json + `agent.start_server` 静态服务 + browser_use 子代理）：
   - `#canvas-main` 为 canvas 非 img，页面 img 元素数 0；
   - 像素统计：非黑 234,944（89.62%）、纯白 255、黑色 27,200（10.38%），棋盘格/网格图案符合预期；
   - 状态栏显示 63 次点击；console 无 JS 错误。

## 遗留（待查，与本次改动无关）

`tests/sim_agent.py` 完整回放（服务+流式）在 trae-sandbox 环境中断：日志停在「第 1 次循环」打印后，退出码 0。已隔离验证：`stream_reasoning` 单独调用正常（1.38s 完成）、工具链路（探针）完整 72 次、`agent.snap()` 初始快照成功（改动点全部覆盖）。判定中断与本次改动无关，建议用户在正常终端跑 `python tests/sim_agent.py --speed 5` 复核；原因未定位。

## 经验

- 批量并行 Edit 同一文件曾出现「部分编辑未落盘」：编辑后必须 Grep/Read 复查确认（AGENTS.md 串行改规则）。
- 本机 HTTP 服务的截图/渲染验证可用 browser_use 子代理 + `browser_evaluate`（本环境不返回脚本值，需经 document.title 中转取值）。
- trae-sandbox 终端不回显 stdout：重定向到文件再 Read 才能拿到输出。
