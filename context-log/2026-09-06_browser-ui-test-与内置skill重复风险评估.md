# browser-ui-test 与内置 skill 重复/侵权风险评估与决策（2026-09-06）

> 疑问：Trae 内置浏览器测试 skill 与本项目沉淀的 `.trae/skills/browser-ui-test` 是否"写重复"、有无侵权风险？Trae 内置 skill 开源还是闭源？必要时是否应删除项目 skill 并关掉内置 skill 规避？
> 结论：不重复、无侵权点，项目 skill 保留，无需规避。已核实证据如下。

## 涉及文件（证据链）

- 内置：`C:\Users\f\.trae-cn\builtin\global\skills\TRAE-browseruse\SKILL.md`（2026-07-01 最后修改）
- 项目：`.trae/skills/browser-ui-test/SKILL.md`（2026-08-26 创建，git commit 95a9906「新增用探针脚本的浏览器 UI 测试的项目内部 skill」）
- 实战来源：`context-log/2026-08-25_滚动跟随修复.md`（第 108 行注明"浏览器实测方法论不在此重复，见 browser-ui-test skill"，其中各条"坑"在该日志有同日原文对应）

## 对比结果：内容不重叠

- 内置 TRAE-browseruse（约 620 行）= **通用浏览器工具 API 手册**：Exec 沙箱调用规则、lock/unlock 流程、工具参数表、ref 生命周期、通用浏览/填表最佳实践。与具体项目无关。
- 项目 browser-ui-test（48 行）= **本项目时序敏感 UI 的探针测试方法论**：探针脚本注入模拟流、省 token、移动靶、真实滚动 vs 合成事件、"合成 dispatch + 异步原生事件双触发是测试伪影"、Windows 进程清理等。内容绑定本项目内部模块（agent.py / xor_world.py / index.html）。
- 唯一"重叠"：几个工具名（browser_navigate、browser_evaluate、browser_console_messages、browser_scroll）——属平台工具功能性名称（事实），引用不构成复制表达。
- 分层处理：项目版刻意未抄内置的"必须在 Exec 里 await tools.* 调用"机制，改走 browser_use 子代理，本身就避免了重复。

## Trae 内置 skill 开源/闭源

- Trae IDE 是北京引力弹弓科技有限公司的**专有（闭源）产品**，使用受 [Trae 用户协议](https://www.trae.cn/terms-of-service/cn) 约束；官方论坛答复：IDE 本体未开源，仅独立的 trae-agent CLI 以 MIT 在 GitHub 开源（https://github.com/bytedance/trae-agent）。
- 内置 skill 随闭源客户端分发（`~/.trae-cn/builtin/`），TRAE-browseruse 目录**无 LICENSE 文件**（仅 office 类第三方库 docx/pptx 带 LICENSE）。
- 易混淆点：官方称 "Skills are built on the open agent skills standard"（https://www.trae.ai/blog/trae_tutorial_0115）指 **SKILL.md 格式规范开放**（agentskills.io）与**社区生态开放**（可导入 anthropics/skills）；**厂商内置 skill 文本没有单独开源授权**。即：格式开放 ≠ 内置内容开源。

## 法律判断（普通常识层面，非法律意见）

- 版权保护"表达"，不保护思想/方法/接口名称。两文件语言、结构、例子完全不同 → 无复制行为，侵权风险低。
- "看过再写"不是版权侵权构成要件，关键是有没有复制具体表达；"没查看过内置 skill"也不构成有效法律抗辩（无法证明记忆清白）。真正有效的防御是**原创证据链**（本项目的 context-log + git commit 时间戳即是）。

## 决策与理由

1. **保留 browser-ui-test，不删除**：删除不降低任何风险，反而丢弃带日期/提交记录的原创性证据。
2. **不靠"关内置 skill + 没看过"规避**：只影响 AI 加载行为，不改变法律状态。
3. **沉淀原则维持**：context-log skill 已写明"从实战经验（而不是其他 skill）出发"，即 clean-room 做法，继续执行。
4. **可选优化（未做）**：项目版与内置版触发描述有重叠（内置 "test frontend UI" vs 项目"需验证前端交互行为"），可能同时加载；如需错开，收紧项目版 description 到"探针驱动的时序敏感验证、省 token"即可，用措辞而非删除解决。
5. **将来若公开发布**（发布到 GitHub/社区等，可选场景）：因未使用内置文本，对 Trae 无署名义务；如需可在文档中自述一句沉淀来源（如"沉淀自本项目 2026-08 浏览器实测"），此说明纯属可选，非法律要求。

## 参考来源

- Trae 用户协议：https://www.trae.cn/terms-of-service/cn
- Trae 官方 Skills 文档（内置 skill 列表）：https://docs.trae.ai/ide/skills
- Trae Skills 最佳实践（开放 Agent Skills 标准）：https://www.trae.ai/blog/trae_tutorial_0115
- 官方论坛"trae 是开源的吗"：https://forum.trae.cn/t/topic/2824
