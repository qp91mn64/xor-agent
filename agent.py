"""Agent 主循环：DeepSeek function calling，驱动 AI 为 XOR 画布逐格选择图案参数。

用法：
    python agent.py               默认参数（概要日志）
    python agent.py --seed 5      种子区域值（默认 0）
    python agent.py --rounds 30   最多循环次数（默认 30）
    python agent.py --clicks 64   最多点击次数（默认 64）
    python agent.py --port 8123   可视化端口（默认随机分配）
    python agent.py --detailed    详细日志：记录模型思考与工具调用原文
    python agent.py --help        查看帮助

设计见 TECH_DESIGN.md。主循环骨架复用 test-agent 的 minimal-agent（MIT）。
"""

import argparse
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv
from openai import OpenAI

import tools
import xor_world
from logger import (
    get_log_filename,
    get_reasoning_content,
    log_model_response,
    log_tool_call,
    write_log,
)

MODEL = "deepseek-v4-flash"

# 终止条件（TECH_DESIGN.md）
MAX_LOOPS = 30          # 一次循环 = 一次 API 请求
MAX_CLICKS = 64         # set_region 成功一次记一次
MAX_TOOL_CALLS = 96     # 工具调用总数上限（含 view_region / evaluate）
MAX_INVALID = 3         # 连续无效点击（越界/超范围/种子区域）

PATTERN_DICT = """图案语义（理解一个值会画出什么）：
- 每区图案由 (dx^dy) & a 决定（dx,dy 为该区域内像素坐标 0..63）；白格 = (dx^dy)&a != 0 的像素。
- a=0：纯黑；a=-1：纯白。
- 单个二进制位 2^k（1,2,4,8,16,32）：2^k×2^k 的黑白棋盘格，黑白各半。
- 多个位：白格是各位棋盘格的并集（黑格变少；黑格 = dx 与 dy 在这些位上全部相等）。
- a<0：取 ~a 的图案并反色（黑白互换）。
- 参考：a=1 细棋盘格；a=2 是 2×2 块棋盘；a=4 是 4×4 块棋盘；a=3 白底，黑色斜线沿主对角线方向、周期 4（黑占 25%）；a=63 几乎全白只剩主对角线。"""


def build_system_prompt(seed_value, seed_index):
    return f"""你是一个数字艺术家，正在为一幅 512×512 的 XOR 图案画布选择参数。画布分为 8×8=64 个区域，每区一个参数值 a（整数，-64..63），该值决定区域内的图案。

{PATTERN_DICT}

任务：
- 区域 {seed_index}（种子区域）已固定为 {seed_value}，不可修改。
- 请用 set_region 工具为其余 63 个区域逐一选择参数值（一次点击一个区域）。
- 可随时用 view_region 查看某区域当前值，用 evaluate 查看整幅画布指标。

目标：
- 让整幅画布的黑白平衡度尽量高（evaluate 返回该指标，1.0=黑白各半，越接近越好）。
- 尽量让每个非种子区域都有明确图案，不要留下 a=0 的纯黑空区域。
- 建议先 evaluate 了解初始状态，再规划点击顺序。

结束：当画布已足够好或点击次数将尽时，直接输出一段最终总结文字即可（不再调用工具）。"""


def get_api_key():
    """从 .env 读取 API 密钥"""
    load_dotenv()
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("错误：未在 .env 文件中找到 DEEPSEEK_API_KEY（模板见 env.example）")
    return key


def assistant_message(msg):
    """把 API 返回的 assistant 消息转成可回填到 messages 的字典"""
    m = {"role": "assistant", "content": msg.content or ""}
    # 官方文档要求：思考模式下工具调用轮次必须回传 reasoning_content，否则 API 返回 400。
    # 2026-08-03 实测（deepseek-v4-flash 0731）未触发 400，保留回传作为防御。
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        m["reasoning_content"] = reasoning
    if msg.tool_calls:
        m["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
    return m


class _Handler(SimpleHTTPRequestHandler):
    """根路径映射到 web/index.html，其余按项目根目录提供静态文件（/output/...）"""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/web/index.html"
        super().do_GET()

    def log_message(self, *args):
        pass  # 静默访问日志


def start_server(root, port=0):
    """启动 stdlib HTTP 服务（后台线程），返回 (httpd, 实际端口)"""
    handler = partial(_Handler, directory=root)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def parse_args():
    p = argparse.ArgumentParser(description="XOR 画布选图案 Agent")
    p.add_argument("--seed", type=int, default=0, help="种子区域值（默认 0）")
    p.add_argument("--seed-index", type=int, default=0, help="种子区域编号 0-63（默认 0）")
    p.add_argument("--rounds", type=int, default=MAX_LOOPS, help=f"最多循环次数（默认 {MAX_LOOPS}）")
    p.add_argument("--clicks", type=int, default=MAX_CLICKS, help=f"最多点击次数（默认 {MAX_CLICKS}）")
    p.add_argument("--port", type=int, default=0, help="可视化端口，0=随机（默认 0）")
    p.add_argument("--detailed", action="store_true", help="详细日志（模型思考+工具调用原文）")
    return p.parse_args()


def main():
    args = parse_args()
    detailed = args.detailed
    log_file = get_log_filename("xor_agent")
    if detailed:
        write_log("详细日志已开启（--detailed）", log_file)

    xor_world.init(seed_value=args.seed, seed_index=args.seed_index)
    write_log(f"种子: 区域 {args.seed_index} = {args.seed}", log_file)
    write_log("画布: 8×8 区域，每区参数 -64..63；种子区域锁定不可修改", log_file)

    # 实时可视化 HTTP 服务（stdlib，无框架）
    httpd, port = start_server(os.path.abspath("."), args.port)
    write_log(f"实时可视化: http://127.0.0.1:{port}/", log_file)

    step = 0
    xor_world.snapshot(step)  # 初始快照
    step += 1

    client = OpenAI(api_key=get_api_key(), base_url="https://api.deepseek.com")
    messages = [{"role": "system", "content": build_system_prompt(args.seed, args.seed_index)}]

    click_count = 0
    tool_call_count = 0
    consecutive_invalid = 0
    reason = ""

    for loops in range(1, args.rounds + 1):
        write_log(f"--- 第 {loops} 次循环 ---", log_file)
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=tools.TOOLS
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            write_log(
                f"API 请求失败（第 {loops} 次循环）: {type(exc).__name__} status_code={status}",
                log_file,
            )
            resp = getattr(exc, "response", None)
            if resp is not None:
                write_log(f"响应体: {resp.text}", log_file)
            raise
        msg = response.choices[0].message
        messages.append(assistant_message(msg))
        log_model_response(msg, log_file, detailed)

        # 模型直接输出文字、不再调用工具
        if not msg.tool_calls:
            reason = "模型直接输出文字，未再调用工具"
            break

        reasoning = get_reasoning_content(msg)
        for tc in msg.tool_calls:
            tool_call_count += 1
            name = tc.function.name
            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                arguments = {}
            result = tools.execute_tool(name, arguments, reasoning=reasoning)
            log_tool_call(name, tc.id, arguments, result, log_file)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            xor_world.snapshot(step)  # 每次工具调用后刷新可视化快照
            step += 1

            if name == "set_region":
                if result.startswith("Invalid"):
                    consecutive_invalid += 1
                    if consecutive_invalid >= MAX_INVALID:
                        reason = f"连续 {MAX_INVALID} 次无效点击"
                        break
                else:
                    consecutive_invalid = 0
                    click_count += 1
                    if click_count >= args.clicks:
                        reason = f"点击达到 {args.clicks} 次"
                        break

            if tool_call_count >= MAX_TOOL_CALLS:
                reason = f"工具调用达到 {MAX_TOOL_CALLS} 次"
                break

        if reason:
            break

    # 结果判定：依据画布状态，而非模型输出
    coverage = xor_world.coverage()
    success = coverage == xor_world.ROWS * xor_world.COLS - 1  # 63 个非种子区域
    final_metric = xor_world.metric()
    xor_world.state["status"] = "success" if success else "incomplete"
    xor_world.state["final_reason"] = reason or "达到循环上限"
    xor_world.snapshot(step)
    result_text = (
        f"{'成功' if success else '失败'}：{reason or '达到循环上限'}。"
        f"已点击 {coverage}/63 个非种子区域，最终黑白平衡度 {final_metric:.3f}。"
    )
    write_log(result_text, log_file)
    write_log(
        f"统计：循环 {loops} 次，工具调用 {tool_call_count} 次，成功点击 {click_count} 次。",
        log_file,
    )
    write_log(f"日志已保存: {log_file}，可视化 http://127.0.0.1:{port}/", log_file)
    print(result_text)


if __name__ == "__main__":
    main()
