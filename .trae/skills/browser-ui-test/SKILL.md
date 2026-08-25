---
name: "browser-ui-test"
description: "用模拟数据流探针驱动浏览器实测前端交互（滚动跟随、流式渲染等时序敏感行为），不消耗 API token。Invoke when：需验证前端交互/渲染行为，或静态分析无法确认浏览器实际行为时。"
---

# 探针驱动的浏览器 UI 实测

## 适用场景与必备条件

**适用场景**
- 前端交互行为验证：滚动跟随、按钮浮现/隐藏、流式渲染更新等——静态读代码无法确认浏览器实际行为的部分。
- 时序敏感场景：内容持续追加下的滚动/跟随（"移动靶"），需要真实渲染环境复现。
- 不消耗 API token 的 UI 实测：用模拟数据流代替真实 Agent 运行（真实运行会烧 token）。

**必备条件**
- 项目有本地可启动的 HTTP 服务：本项目 `agent.start_server(root, port)`（stdlib http.server）。
- 有可注入模拟数据的入口：本项目 `agent.write_reasoning_live / xor_world.set_region / agent.snap`（→ SSE 广播 + state.json）。
- 探针脚本导入项目模块即可，**不需要 API key**（参考 `selftest.py` / 已删的 probe_sse.py 模式）。

## 操作步骤

1. **写探针脚本**（复用 agent.py / xor_world.py）：模板见同目录 `probe_template.py`——复制到项目根目录改名 `probe_<场景>.py`，按场景调整 `pump()`：
   - 先铺历史：多轮 `set_region(..., reasoning=长文本, round_id=r)` + `snap(idx)`，长 reasoning 撑高时间线容器使其可滚动。
   - 再持续追加实时流：`write_reasoning_live` 高频推送，模拟 SSE delta。
   - 测"移动靶"（scrollHeight 持续变化）时，追加文本要**逐次增长**（如 `"追加文本" * (5 + k)`），等长文本 scrollHeight 不会变。
   - 主线程保活：`while True: time.sleep(1)`；**不要用 `input()`**——非交互终端立即 EOF，`httpd.shutdown()` 提前退出探针。
   - 固定端口（便于连接）或随机端口 + `print(..., flush=True)` 打印实际端口。
2. **启动探针**（后台运行）。Windows 沙箱注意：终端 wrapper 退出后 python 进程可能仍存活占用端口 → 用 `netstat -ano | Select-String ":<port>"` 找 PID，`taskkill /PID <n> /F` 清理（StopCommand 杀不掉脱离 wrapper 的进程）。
3. **用 browser_use 子代理驱动浏览器**：
   - `browser_navigate` 到 `http://127.0.0.1:<port>/`，sleep 数秒等数据流铺满。
   - 用 `browser_evaluate` 读/操纵页面状态；真实滚动用原生滚动路径（见下）。
   - 逐段报告每步 JSON 状态，最后汇总。

## 实战要点与坑

- **`browser_evaluate` 返回值可能一律为 null**：改用 `console.log(JSON.stringify(...))` 输出 + `browser_console_messages` 捕获（返回值通道在本环境不可靠）。
- **页面级 `let` 全局变量可直接读**：如 `autoFollow`——不是 `window` 属性，但全局词法作用域对 evaluate 可见。
- **`browser_scroll` 对内部滚动容器可能滚的是窗口**（容器 scrollTop 不变）：用 evaluate 原生设置 `el.scrollTop = x`，或走真实滚动代理。
- **键盘 PageUp/PageDown 与合成 WheelEvent 是非受信事件，不触发默认滚动**：验证"滚动监听逻辑"可用 `el.scrollTop = x; el.dispatchEvent(new Event('scroll'))` 走同一监听器代码路径；验证"真实滚动路径"需原生滚动。
- **程序化设置 scrollTop 会触发浏览器异步原生 scroll 事件**（CSSOM 规范）：若同时 dispatch 合成 scroll 事件，同一变更会双触发，方向判定（`scrollTop > lastScrollTop`）会误判（相等 → 判"向上滚"）——这是**测试伪影**；生产代码应在程序化滚动后同步 `lastScrollTop` 防御（本项目 index.html 已如此）。
- **移动靶类断言不要依赖精确 dist 值**：改为"重定位到目标后 dispatch、立即读状态"，重复多次，接受个别次落在阈值外，看趋势。
- **失败先溯因区分"测试伪影"与"真实 bug"**：合成事件 + 原生异步事件叠加导致的假失败（本项目 browser 实测步骤 5 案例），溯因后再下结论。

## 局限与验证边界

- 自动化验证的是**逻辑分支与状态机**；真实手感（滚轮动量、触控、焦点行为）仍需人工实测闭环。
- 合成事件与真实输入存在差异，不能完全替代真人操作。
- 自动化对**键盘/焦点通道**覆盖有限（div 不可聚焦、按钮点击后抢焦点等），这类问题以人工复现为准。
