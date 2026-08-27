"""模拟 Agent 回放器（可复用测试工具，常驻 tests/，与 selftest.py 同类）。

解析 tests/example_data.txt（真实详细日志）并按真实时间线回放：按事件顺序驱动
write_reasoning_live（思考流式）+ set_region/view_region/evaluate（工具）+ snap（快照），
浏览器可实测完整决策过程 UI（思考流式 / 点击轨迹 / 画布填充 / 热力图 / 结束状态），
不消耗 API token。

回放速度 = 日志时间戳倒推的真实速度：事件间隔按日志 [时间戳] 差值推进；每轮思考
在「本轮请求耗时 Ns」内均匀流式流出（模型思考发生在 API 请求期间）。speed=1 即
原始运行的真实节奏（example_data.txt 首尾约 3 分 21 秒），speed=N 为 N 倍速。

与一次性探针（probe_*.py，用完即删）不同：本脚本常驻版本控制，数据全部来自
example_data.txt（8 轮循环 / 72 次工具调用 / 63 次成功点击），可反复用于回归实测。

用法：
    python tests/sim_agent.py                   # 默认端口 8765（被占自动 fallback 随机），自动打开浏览器
    python tests/sim_agent.py --speed 5         # 5 倍速（speed=1 为日志时间戳倒推的真实速度）
    python tests/sim_agent.py --no-open --exit  # 不开浏览器、回放完自动退出（CI/无人值守）
    python tests/sim_agent.py --dry-run         # 只解析日志打印事件统计，不启动服务/不回放

浏览器实测：回放期间访问 http://127.0.0.1:<端口>/ 观察思考逐批流入、63 次点击逐步
填满画布、结束状态变为 success。实测方法见 .trae/skills/browser-ui-test/。
"""

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from datetime import datetime

# Windows 控制台默认 GBK：模型输出可能含 GBK 无法编码的字符（如 U+2212 − 数学负号，
# 见 example_data.txt 第 8 轮最终总结），print 会抛 UnicodeEncodeError 中断回放。
# 强制 stdout/stderr 为 UTF-8（与项目 PYTHONIOENCODING=utf-8 约定一致，脚本内自包含）。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)  # 脚本位于 tests/ 子目录：先让项目根可导入，再 import agent 等

import agent
import tools
import xor_world

EXAMPLE_DATA = os.path.join(BASE_DIR, "tests", "example_data.txt")

# --- 日志行格式（SSE 化后的详细日志，见 context-log/2026-08-25_SSE实时推送.md） ---
TS_FMT = "%Y-%m-%d %H:%M:%S"
TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")
SEED_RE = re.compile(r"^种子: 区域 (\d+) = (-?\d+)$")
ROUND_RE = re.compile(r"^--- 第 (\d+) 次循环 ---$")
DURATION_RE = re.compile(r"^本轮请求耗时 ([\d.]+)s$")
THINK_RE = re.compile(r"^模型思考: (.*)$")
OUTPUT_RE = re.compile(r"^模型输出: (.*)$")
TOOL_RE = re.compile(r"^模型调用工具: (\w+) args=(.*)$")
FINISH_RE = re.compile(r"^(成功|失败)：(.+)$")
STATS_RE = re.compile(r"^统计：循环 (\d+) 次，工具调用 (\d+) 次，成功点击 (\d+) 次。$")


def parse_log(path):
    """解析详细日志 → (事件列表, 元信息)。

    事件 kind: round / think / tool / output / finish / stats，均带 ts（日志时间戳的
    epoch 秒）；think 额外带 duration（该轮「本轮请求耗时 Ns」，真实中思考在请求期间流出）。
    多行块（思考/输出）：首行带「模型思考: / 模型输出:」前缀，后续不带时间戳的行
    都是该块的续行，累积到下一行带时间戳为止。
    """
    events = []
    meta = {"seed_value": 15, "seed_index": 0, "first_ts": None}
    cur_round = 0
    pending_duration = 0.0  # 最近一条「本轮请求耗时」→ 下一条思考使用
    buffer = None  # {"kind": "think"|"output", "ts": ..., "lines": [...]}

    def flush_buffer():
        nonlocal buffer
        if buffer is not None:
            ev = {"kind": buffer["kind"], "round": cur_round, "ts": buffer["ts"],
                  "text": "\n".join(buffer["lines"])}
            if buffer["kind"] == "think":
                ev["duration"] = pending_duration
            events.append(ev)
            buffer = None

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = TS_RE.match(line)
            if not m:
                if buffer is not None:
                    buffer["lines"].append(line)
                continue
            ts = datetime.strptime(m.group(1), TS_FMT).timestamp()
            if meta["first_ts"] is None:
                meta["first_ts"] = ts
            content = m.group(2)
            flush_buffer()  # 新日志行到达，提交未完成的多行块
            sm = SEED_RE.match(content)
            if sm:
                meta["seed_index"] = int(sm.group(1))
                meta["seed_value"] = int(sm.group(2))
                continue
            rm = ROUND_RE.match(content)
            if rm:
                cur_round = int(rm.group(1))
                events.append({"kind": "round", "round": cur_round, "ts": ts})
                continue
            dm = DURATION_RE.match(content)
            if dm:
                pending_duration = float(dm.group(1))
                continue
            tm = THINK_RE.match(content)
            if tm:
                buffer = {"kind": "think", "ts": ts, "lines": [tm.group(1)]}
                continue
            om = OUTPUT_RE.match(content)
            if om:
                buffer = {"kind": "output", "ts": ts, "lines": [om.group(1)]}
                continue
            tm2 = TOOL_RE.match(content)
            if tm2:
                name, args_text = tm2.group(1), tm2.group(2)
                try:
                    args = json.loads(args_text)
                except json.JSONDecodeError:
                    args = {}
                events.append({"kind": "tool", "round": cur_round, "ts": ts,
                               "name": name, "args": args})
                continue
            fm = FINISH_RE.match(content)
            if fm:
                events.append({"kind": "finish", "ts": ts,
                               "ok": fm.group(1) == "成功", "reason": fm.group(2)})
                continue
            sm2 = STATS_RE.match(content)
            if sm2:
                events.append({"kind": "stats", "ts": ts,
                               "loops": int(sm2.group(1)),
                               "tool_calls": int(sm2.group(2)), "clicks": int(sm2.group(3))})
                continue
            # 其余（启动诊断、工具结果回执、日志路径等）与回放无关，忽略
    flush_buffer()
    return events, meta


BATCH_SLEEP = 0.05  # 流式批间隔（speed=1）：SSE 帧率约 20 帧/s，接近真实逐 token 平滑观感（0.3s/批会一卡一卡）


def stream_reasoning(round_id, text, duration, speed):
    """把思考全文按字符块均匀铺满 duration 秒写入 reasoning_live，模拟真实流式 delta 到达。

    duration = 该轮 API 请求耗时（思考在请求期间持续流出）；块大小按文本长度自适应，
    批间隔 BATCH_SLEEP/speed；文本流完后若未到 duration，剩余时间继续 sleep（模型沉默
    思考阶段，不推事件），总流式时长 = duration/speed。
    """
    if not text:
        return
    if duration <= 0:
        agent.write_reasoning_live(round_id, text)
        return
    n = max(1, int(duration / BATCH_SLEEP))   # 目标批数 ≈ 时长 / 批间隔
    step = max(1, -(-len(text) // n))          # 每批字符数（向上取整，批数 ≤ n，剩余时长补足）
    t0 = time.monotonic()
    last = ""
    for i in range(0, len(text), step):
        last = text[:i + step]
        agent.write_reasoning_live(round_id, last)
        time.sleep(BATCH_SLEEP / speed)
    if last != text:  # 兜底确保完整文本已写入（与 chat_stream 行为一致）
        agent.write_reasoning_live(round_id, text)
    remain = duration / speed - (time.monotonic() - t0)
    if remain > 0:
        time.sleep(remain)


def replay(events, meta, speed=1.0, port=8765, no_open=False, exit_after=False):
    xor_world.init(seed_value=meta["seed_value"], seed_index=meta["seed_index"])
    httpd, port = agent.start_server(agent.BASE, port)
    if httpd is None:
        print(f"端口 {port} 被占用，fallback 到随机端口", flush=True)
        httpd, port = agent.start_server(agent.BASE, 0)
    if httpd is None:
        raise SystemExit("错误：无法启动 HTTP 服务")
    url = f"http://127.0.0.1:{port}/"
    print(f"模拟回放 {os.path.basename(EXAMPLE_DATA)} → {url}", flush=True)
    if not no_open:
        webbrowser.open(url)

    agent.snap()  # 初始快照
    round_reasoning = {}
    tool_count = 0
    click_count = 0
    cur_round = 0
    # 回放节奏 = 日志时间戳倒推：事件间隔按 ts 差推进；think 特殊处理（见下）。
    prev_ts = meta.get("first_ts") or events[0]["ts"]
    min_gap = 0.2 / speed  # 同秒事件的最小间隔：日志秒级精度丢失轮内亚秒时序，保留点击轨迹逐点可见

    for ev in events:
        kind = ev["kind"]
        if kind == "think":
            # 思考发生在 API 请求期间（从本轮循环开始即流出），不按事件间等待，
            # 直接流式铺满「本轮请求耗时」；结束后时间推进到请求结束（= 工具调用时刻）
            round_reasoning[cur_round] = ev["text"]
            stream_reasoning(cur_round, ev["text"], ev.get("duration", 0), speed)
            prev_ts = ev["ts"]
            print(f"  思考 {len(ev['text'])} 字符（流式写入，耗时 {ev.get('duration', 0):.1f}s）", flush=True)
            continue

        wait = max(min_gap, (ev["ts"] - prev_ts) / speed)
        if wait > 0:
            time.sleep(wait)
        prev_ts = ev["ts"]

        if kind == "round":
            cur_round = ev["round"]
            print(f"\n--- 第 {cur_round} 次循环 ---", flush=True)
        elif kind == "tool":
            tool_count += 1
            result = tools.execute_tool(ev["name"], ev["args"],
                                        reasoning=round_reasoning.get(cur_round, ""),
                                        round_id=cur_round)
            if ev["name"] == "set_region" and not result.startswith("Invalid"):
                click_count += 1
            brief = result if len(result) <= 60 else result[:60] + "…"
            print(f"  [{tool_count}] {ev['name']} {ev['args']} -> {brief}", flush=True)
            agent.snap()
        elif kind == "output":
            print(f"  模型输出: {ev['text']}", flush=True)
        elif kind == "finish":
            xor_world.state["status"] = "success" if ev["ok"] else "incomplete"
            xor_world.state["final_reason"] = ev["reason"]
            print(f"\n结果: {ev['reason']}", flush=True)
        elif kind == "stats":
            print(f"统计(日志): 循环 {ev['loops']} 次，工具调用 {ev['tool_calls']} 次，成功点击 {ev['clicks']} 次",
                  flush=True)

    # 结束快照 + 数据完整性校验（example_data.txt 的数据是否被完整用完）
    xor_world.state["status"] = xor_world.state.get("status", "running")
    agent.snap()
    unset = xor_world.unset_regions()
    print(f"统计(实际): 工具调用 {tool_count} 次，成功点击 {click_count} 次，覆盖 {xor_world.coverage()}/63", flush=True)
    if unset:
        print(f"警告: 仍有未设定区域 {unset}（数据未回放完整）", flush=True)
    else:
        print("数据回放完整：63 个非种子区域全部设定（example_data.txt 数据用完）", flush=True)
    agent.clear_reasoning_live()  # 运行结束，删除实时思考中间文件（web 面板随之隐藏）

    if exit_after:
        httpd.shutdown()
        return
    print("回放完成。浏览器可继续查看（Ctrl+C 退出）", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    httpd.shutdown()


def main():
    p = argparse.ArgumentParser(description="用 example_data.txt 真实日志回放模拟 Agent 完整决策过程")
    p.add_argument("--port", type=int, default=8765, help="可视化端口（默认 8765，被占自动 fallback 随机）")
    p.add_argument("--speed", type=float, default=1.0,
                   help="回放速度倍率（默认 1.0 = 日志时间戳/请求耗时倒推的真实速度；越大越快）")
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--exit", action="store_true", help="回放完自动退出（默认保持服务供浏览器查看）")
    p.add_argument("--dry-run", action="store_true", help="只解析日志打印事件统计，不启动服务不回放")
    args = p.parse_args()

    if not os.path.exists(EXAMPLE_DATA):
        raise SystemExit(f"缺少样例数据: {EXAMPLE_DATA}（应为 tests/example_data.txt）")
    events, meta = parse_log(EXAMPLE_DATA)

    loops = sum(1 for e in events if e["kind"] == "round")
    tools_ = sum(1 for e in events if e["kind"] == "tool")
    set_region_calls = sum(1 for e in events if e["kind"] == "tool" and e["name"] == "set_region")
    real_sec = events[-1]["ts"] - meta["first_ts"]
    print(f"解析 {os.path.basename(EXAMPLE_DATA)}: 种子 区域{meta['seed_index']}={meta['seed_value']}，"
          f"{loops} 轮，工具调用 {tools_} 次（set_region {set_region_calls} 次），"
          f"真实时长 {real_sec:.0f}s", flush=True)
    if args.dry_run:
        for e in events:
            if e["kind"] in ("round", "think", "finish", "stats"):
                dur = f" duration={e.get('duration', 0):.1f}" if e["kind"] == "think" else ""
                print(f"  ts={e['ts']:.0f}{dur}", e)
        return

    replay(events, meta, speed=args.speed, port=args.port,
           no_open=args.no_open, exit_after=args.exit)


if __name__ == "__main__":
    main()
