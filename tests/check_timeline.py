"""时间线可见性回归检查（纯标准库，不需要浏览器）。

不打开浏览器，判断模型输出是否可见。
改动 `web/index.html` 时间线渲染、或改动 `state.rounds`/`clicks` 数据口径后必跑；
不带参数默认检查 `output/` 下最新一次运行，也可显式传一个或多个 `state.json`。

用法：
    python tests/check_timeline.py                      # 检查 output/ 下最新一次运行
    python tests/check_timeline.py <state.json 路径>     # 检查指定运行
    python tests/check_timeline.py a.json b.json ...    # 批量检查


背景（2026-10-17 修复的 bug）：同一份"每轮思考"曾只挂在点击条目上，没有 set_region
点击的轮次（只查看 / 只评估 / 只输出总结）思考在任何地方都不落盘，界面上静默消失；
模型正文（末轮的最终总结）则没有任何通往网页的通路。修复后 `state.rounds` 是每轮
决策的唯一权威来源，本脚本用于回归"数据在 → 界面上确实有"。

复刻 web/index.html 里时间线的渲染口径（与前端同一套规则，见文件内 `_timeline_items`
与 `render_groups` 的注释），用真实 `output/<运行ID>/state.json` 断言：

1. index.html 仍提供时间线所需的函数，且没有把历史轮次截断的写法；
2. 每一轮的思考全文都出现在时间线 HTML 里（逐字比对，含 HTML 转义）；
3. 每一轮的模型正文（末轮即最终总结）都出现在时间线 HTML 里；
4. 点击轨迹 `#index→value(原prev)` 每次都出现，且按所属轮次、按顺序排；
5. 每一轮的思考不得与其它轮次串位（轮次顺序递增）；
6. **工具调用与结果不展开显示**（用户明确要求：人类读者只看轨迹；
   完整 `calls` 留在 state.json 里，按需查看）——本项做反向断言，防止以后又展开。

改动 web/index.html 的时间线渲染后，必须同步改本文件。
"""

import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(BASE_DIR, "web", "index.html")

# Windows 控制台默认 GBK：运行产物里含 GBK 无法编码的字符（如 U+2212 − 数学负号），
# 报错信息/轮次文本 print 时会中断检查。强制 stdout/stderr 为 UTF-8（与项目约定一致）。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# 时间线所需的前端函数（缺一即前端结构变了，本脚本的镜像口径需同步复核）
REQUIRED_FUNCS = ("esc", "timelineModel", "toolBrief", "groupHead", "renderGroups", "drawTimeline")

# 工具显示名与顺序（与前端 toolBrief 一致）
TOOL_ORDER = ("view_region", "evaluate", "set_region")
TOOL_NAMES = {"set_region": "设定", "view_region": "查看", "evaluate": "评估"}


def js_escape(s):
    """复刻前端 esc()：& < > " ' 五个字符转义，均为单字符替换，无二次转义"""
    table = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
    return re.sub(r'[&<>"\']', lambda m: table[m.group(0)], s or "")


def load_page():
    with open(INDEX_HTML, encoding="utf-8") as f:
        return f.read()


def tool_brief(tools):
    """复刻前端 toolBrief：固定工具顺序，避免字典序在不同实现下显示不一致"""
    tools = tools or {}
    keys = [k for k in TOOL_ORDER if tools.get(k)]
    keys += [k for k in tools if k not in TOOL_ORDER]
    return " ".join(f"{TOOL_NAMES.get(k, k)}×{tools[k]}" for k in keys)


def group_head(g):
    """复刻前端 groupHead（不带进行中轮的「思考中…」标记）"""
    n = len(g["clicks"])
    stats = " · ".join(x for x in [(f"{n} 格" if n else ""), tool_brief(g["tools"])] if x)
    return f"第 {g['round']} 轮" + (f" · {stats}" if stats else "")


def click_line(clicks):
    """复刻前端点击轨迹行：#index→value(原prev)，同一轮内按点击顺序空格相连"""
    return " ".join(f"#{c['index']}→{c['value']}(原{c['prev']})" for c in clicks)


def render_groups(items):
    """复刻前端 renderGroups 的可见内容：每轮 = 组头 → 思考 → 点击轨迹行 → 模型正文。

    返回 (整页可见文本, 每轮点击行文本列表)；**不渲染工具调用与结果**（人类读者只看轨迹，
    完整 calls 留在 state.json），故本函数刻意不引用 g["calls"] 的内容。
    """
    parts = []
    per_round_clicks = []
    for g in items:
        parts.append(group_head(g))
        if g["reasoning"]:
            parts.append(js_escape(g["reasoning"]))
        line = click_line(g["clicks"])
        if line:
            parts.append(line)
        per_round_clicks.append(line)
        if g["content"]:
            parts.append(js_escape(g["content"]))
    return "\n".join(parts), per_round_clicks


def _timeline_items(state, live_round=-1, live_text=""):
    """复刻前端 timelineModel：以 state.rounds 为准，clicks 归入所属轮次；旧格式回退 clicks 分组。

    live_round/live_text 模拟"该轮思考正在流式到达"：实时全文并进同一轮那一组（不新开一组，
    否则会出现两个「第 N 轮」标题、点击行夹在中间——2026-10-17 用户实测的 bug）。
    返回按 round 升序的分组列表（每组含 round/reasoning/content/calls/tools/clicks/live）。
    """
    clicks = state.get("clicks") or []
    rounds = state.get("rounds") or []
    by_round = {}
    order = []
    for r in rounds:
        by_round[r["round"]] = {"round": r["round"], "reasoning": r.get("reasoning") or "",
                                "content": r.get("content") or "", "calls": r.get("calls") or [],
                                "tools": r.get("tools") or {}, "clicks": []}
        order.append(r["round"])
    if not rounds:  # 旧格式：无 rounds，思考挂在 clicks 上
        for c in clicks:
            if c["round"] not in by_round:
                by_round[c["round"]] = {"round": c["round"], "reasoning": c.get("reasoning") or "",
                                        "content": "", "calls": [], "tools": {}, "clicks": []}
                order.append(c["round"])
    for c in clicks:
        if c["round"] not in by_round:
            by_round[c["round"]] = {"round": c["round"], "reasoning": "", "content": "",
                                    "calls": [], "tools": {}, "clicks": []}
            order.append(c["round"])
        by_round[c["round"]]["clicks"].append(c)
    items = [by_round[r] for r in sorted(order)]
    max_recorded = max((r["round"] for r in rounds), default=-1)
    max_seen = max((g["round"] for g in items), default=-1)
    live_group = next((g for g in items if g["round"] == live_round), None)
    if live_group and live_round > max_recorded:
        live_group["reasoning"] = live_text
        live_group["live"] = True
    elif live_group is None and live_text and live_round > max_seen:
        items.append({"round": live_round, "reasoning": live_text, "content": "", "calls": [],
                      "tools": {}, "clicks": [], "live": True})
    items.sort(key=lambda g: g["round"])
    return items


def _ordered_positions(text, needles):
    """按顺序在 text 中依次定位各 needle（从上一个命中之后继续找），返回位置列表。

    这样"同一行出现多次"不会被误判：每个 needle 的命中位置必须在前一个之后，才算按顺序出现。
    """
    pos = []
    start = 0
    for n in needles:
        i = text.find(n, start)
        pos.append(i)
        if i < 0:
            break
        start = i + len(n)
    return pos


def _count_occurrences(text, needle):
    return 0 if not needle else text.count(needle)


def check_live_merge(note=print):
    """复刻"流式进行中"这一状态，锁死 2026-10-17 用户实测到的那个 bug：

    进行中的轮次**必须只有一条**——思考（流式到达）与点击（工具已开始执行）并进同一组。
    曾经的写法把两者画成两组，于是出现两个「第 N 轮」小标题，点击行还夹在两条思考之间。
    这里直接喂一组"边流式边执行工具"的状态序列给镜像渲染，断言：
    ① 每个轮次标题只出现一次；② 同一轮内思考 → 点击 → 正文的先后顺序正确。
    """
    note("=== 流式进行中（模拟：第 5 轮思考溢出 + 同轮点击陆续到达 + 第 6 轮流式开始）===")
    fail = 0
    base = {
        "seed_index": 0, "seed_value": 15, "status": "running", "final_reason": "",
        "rounds": [{"round": r, "reasoning": f"第{r}轮思考", "content": "", "calls": [],
                    "tools": {"set_region": 0}, "clicks": 0} for r in range(1, 5)],
        "clicks": [],
    }
    # 第 5 轮：思考流到"第5轮思考（累积中）"，随后 3 次点击陆续执行（state.clicks 增长，rounds 尚无第 5 轮）
    for n in (1, 2, 3):
        state = dict(base)
        state["clicks"] = [{"index": i, "value": i + 40, "prev": 0, "round": 5} for i in range(1, n + 1)]
        items = _timeline_items(state, live_round=5, live_text="第5轮思考")
        rendered, _ = render_groups(items)
        nums = [g["round"] for g in items]
        if len(nums) != len(set(nums)):
            fail += 1
            note(f"  点击 {n} 个时出现重复轮次标题: {nums}!!")
        if _count_occurrences(rendered, "第 5 轮") != 1:
            fail += 1
            note(f"  点击 {n} 个时「第 5 轮」标题出现 {_count_occurrences(rendered, '第 5 轮')} 次（应为 1）!!")
        i_reason = rendered.find("第5轮思考")
        i_click = rendered.find(f"#{n}→")
        i_head = rendered.find("第 5 轮")
        if not (i_head < i_reason < i_click):
            fail += 1
            note(f"  点击 {n} 个时顺序不对：标题@{i_head} 思考@{i_reason} 点击@{i_click}!!")
        if n not in (1, 3):  # 抽样打印，避免刷屏
            continue
        note(f"  第 {n} 个点击到达后：{nums}（「第 5 轮」标题 {_count_occurrences(rendered, '第 5 轮')} 次），"
             f"顺序 标题<思考<点击: {i_head < i_reason < i_click}")
    # 第 6 轮开始流式：第 5 轮已落盘，实时只应作用在第 6 轮
    state6 = dict(base)
    state6["rounds"] = base["rounds"] + [{"round": 5, "reasoning": "第5轮思考（完整）", "content": "第5轮正文",
                                          "calls": [], "tools": {"set_region": 3}, "clicks": 3}]
    state6["clicks"] = [{"index": i, "value": i + 40, "prev": 0, "round": 5} for i in range(1, 4)]
    items6 = _timeline_items(state6, live_round=6, live_text="第6轮思考（流式中）")
    rendered6, _ = render_groups(items6)
    nums6 = [g["round"] for g in items6]
    if len(nums6) != len(set(nums6)) or _count_occurrences(rendered6, "第 6 轮") != 1:
        fail += 1
        note(f"  第 6 轮流式开始时轮次标题异常: {nums6}，「第 6 轮」出现 "
             f"{_count_occurrences(rendered6, '第 6 轮')} 次!!")
    if "第5轮思考（完整）" not in rendered6 or rendered6.find("第5轮思考（完整）") > rendered6.find("第6轮思考（流式中）"):
        fail += 1
        note("  第 5 轮完整思考缺失或被第 6 轮实时文本覆盖!!")
    note(f"  第 6 轮流式开始：{nums6}；第 5 轮保持完整思考，实时只作用在第 6 轮: "
         f"{'OK' if not fail else '有问题'}")
    return fail


def check_state(state_path, note=print):
    """检查一个 state.json：返回失败项数"""
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    items = _timeline_items(state)
    rendered, per_round_clicks = render_groups(items)
    rounds = state.get("rounds") or []
    new_format = any("content" in r for r in rounds)
    rel = os.path.relpath(state_path, BASE_DIR)
    note(f"=== {rel} ===")
    note(f"  {len(rounds)} 轮已落盘，{len(state.get('clicks') or [])} 次点击，status={state.get('status')}")
    fail = 0

    # 1) 每一轮的思考都必须可见（逐字，含转义）
    for r in rounds:
        text = r.get("reasoning") or ""
        if not text:
            note(f"  第 {r['round']} 轮: 无思考（跳过）")
            continue
        hit = js_escape(text) in rendered
        fail += 0 if hit else 1
        note(f"  第 {r['round']} 轮: 思考 {len(text):>5} 字符 → 时间线可见: {'OK' if hit else '缺失!!'}")

    # 2) 模型正文（末轮即最终总结）必须可见；旧格式当时没存正文，跳过不算失败
    with_content = [r for r in rounds if r.get("content")]
    if not new_format:
        note("  旧格式 state.json（无 rounds）：模型正文当时未存档，本项不适用（跳过）")
    else:
        for r in with_content:
            hit = js_escape(r["content"]) in rendered
            fail += 0 if hit else 1
            note(f"  第 {r['round']} 轮: 正文 {len(r['content']):>5} 字符 → 时间线可见: {'OK' if hit else '缺失!!'}")
        if not with_content:
            fail += 1
            note("  没有任何轮的模型正文（新格式下 content 应至少有一轮非空）: 缺失!!")

    # 3) 点击轨迹：每次点击都要出现，且按所属轮次、按顺序排（紧凑一行，不展开工具结果）
    total_clicks = 0
    for g, line in zip(items, per_round_clicks):
        total_clicks += len(g["clicks"])
        if g["clicks"] and line not in rendered:
            fail += 1
            note(f"  第 {g['round']} 轮: 点击轨迹未出现!!")
    pos = _ordered_positions(rendered, [l for l in per_round_clicks if l])
    if any(p < 0 for p in pos) or pos != sorted(pos):
        fail += 1
        note(f"  点击轨迹跨轮次顺序错乱 {pos}!!")
    note(f"  点击轨迹 {total_clicks} 次，可见与顺序: {'OK' if not fail else '见上方失败项'}")

    # 4) 工具调用与结果**不得**展开显示（用户要求：人类读者只看轨迹；完整 calls 看 state.json）
    expanded = []
    for r in rounds:
        for c in r.get("calls") or []:
            res = (c.get("result") or "").strip()
            if len(res) >= 12 and js_escape(res[:60]) in rendered:
                expanded.append(f"第 {r['round']} 轮 {c['name']}")
    if expanded:
        fail += len(expanded)
        note(f"  以下工具结果被展开到页面上（应只在 state.json 里）: {expanded}!!")
    else:
        note("  工具调用结果未展开（只显示轨迹，符合预期）")

    # 5) 新格式下每轮都得有痕迹（思考/正文/工具调用至少一样），不能是空白组。
    #    旧格式（无 rounds/content/calls 字段）当时就没存这些内容，不按新口径判失败。
    if new_format:
        for r in rounds:
            if not (r.get("reasoning") or r.get("content") or r.get("calls")):
                fail += 1
                note(f"  第 {r['round']} 轮: 空白组（无思考、无正文、无工具调用，异常）!!")
    else:
        note("  旧格式 state.json：当时未存正文/工具调用，跳过\"每轮须有痕迹\"检查")

    # 5) 轮次顺序递增
    nums = [g["round"] for g in items]
    if nums != sorted(nums):
        fail += 1
        note(f"  轮次顺序错乱: {nums}!!")

    note(f"  {len(items)} 组，轮次顺序 {'OK' if nums == sorted(nums) else '错乱'}；检查结果: "
         f"{'全部通过' if not fail else str(fail) + ' 项失败'}")
    return fail


def main():
    html = load_page()
    note = print

    # index.html 结构前置检查：函数齐全 + 没有"只渲染最新 N 组"的截断
    for fn in REQUIRED_FUNCS:
        assert f"function {fn}(" in html, f"index.html 缺少时间线函数 {fn}（本脚本的口径需同步复核）"
    assert ".slice(-20)" not in html and "slice(-20)" not in html, \
        "index.html 又出现了只渲染最新 N 组的截断（历史轮次会静默消失）"
    note(f"index.html 结构 OK：时间线函数齐全（{', '.join(REQUIRED_FUNCS)}），无历史轮次截断")
    fail_live = check_live_merge(note)   # 流式进行中的状态（不需要 state.json）

    args = sys.argv[1:]
    if not args:
        out_root = os.path.join(BASE_DIR, "output")
        runs = sorted(d for d in os.listdir(out_root) if os.path.isdir(os.path.join(out_root, d))) \
            if os.path.isdir(out_root) else []
        if not runs:
            raise SystemExit("output/ 下没有任何运行目录；先跑 `python tests/sim_agent.py --speed 100 --no-open --exit`，"
                             "或显式传入 state.json 路径")
        args = [os.path.join(out_root, runs[-1], "state.json")]
        note(f"未指定 state.json，检查最新运行: output/{runs[-1]}/")

    total = 0
    for path in args:
        if not os.path.isfile(path):
            raise SystemExit(f"找不到 state.json: {path}")
        total += check_state(path, note)
    total += fail_live
    print(f"\n结果: {'全部通过（每轮思考、正文、点击轨迹都可见；工具结果未展开；进行中轮次不重复）' if not total else str(total) + ' 项断言失败'}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
