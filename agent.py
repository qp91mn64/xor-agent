"""Agent 主循环：DeepSeek function calling，驱动 AI 为 XOR 画布逐格选择图案参数。

用法：
    python agent.py               默认参数（概要日志）
    python agent.py --seed 5      种子区域值（默认 0）
    python agent.py --rounds 30   最多循环次数（默认 30）
    python agent.py --clicks 64   最多点击次数（默认 64）
    python agent.py --port 8765   可视化端口（默认固定 8765，被占时自动 fallback 随机）
    python agent.py --no-open     不自动打开浏览器
    python agent.py --detailed    详细日志：记录模型思考与工具调用原文（或环境变量 DETAILED_LOG=1）
    python agent.py --help        查看帮助

设计见 TECH_DESIGN.md。主循环骨架复用 test-agent 的 minimal-agent（MIT）。
"""

import argparse
import json
import os
import socketserver
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler
from types import SimpleNamespace

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

# 固定默认端口：避开 8123（Home Assistant 默认端口）等知名端口；被占时自动 fallback 随机。
# Windows 端口复用坑：必须用 allow_reuse_address=False（见 context-log/2026-08-06_allow-reuse-address-port-reuse.md），
# 否则 ThreadingHTTPServer 会与占用者"共存绑定"同一端口，fallback 永不触发。
DEFAULT_PORT = 8765

# 服务根目录固定为脚本所在目录（不依赖 cwd），防止从系统盘启动时暴露目录内容；
# 运行产物（快照/实时思考/日志）也一律相对 BASE，否则从别处启动时 .env 找不到、web 轮询 /output/ 404
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")

# 实时思考中间文件：流式等待期间 web 轮询显示，本轮结束删除
REASONING_LIVE = os.path.join(OUT, "reasoning_live.json")

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
    """从 BASE/.env 读取 API 密钥（不依赖 cwd）"""
    load_dotenv(os.path.join(BASE, ".env"))
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("错误：未在 .env 文件中找到 DEEPSEEK_API_KEY（模板见 env.example）")
    return key


def assistant_message(msg):
    """把 API 返回的 assistant 消息转成可回填到 messages 的字典（兼容 SDK 对象与流式 SimpleNamespace）"""
    m = {"role": "assistant", "content": msg.content or ""}
    # 官方文档要求：思考模式下工具调用轮次必须回传 reasoning_content，否则 API 返回 400。
    # 2026-08-03 实测（deepseek-v4-flash 0731）未触发 400，保留回传作为防御。
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        m["reasoning_content"] = reasoning
    if msg.tool_calls:
        m["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return m


def write_reasoning_live(round_id, reasoning):
    """原子写 output/reasoning_live.json（web 轮询显示实时思考）；文件不存在时 web 隐藏该面板"""
    try:
        os.makedirs(OUT, exist_ok=True)
        payload = json.dumps({"round": round_id, "reasoning": reasoning}, ensure_ascii=False)
        tmp = REASONING_LIVE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, REASONING_LIVE)
    except OSError:
        pass


def clear_reasoning_live():
    try:
        os.remove(REASONING_LIVE)
    except OSError:
        pass


def chat_stream(client, messages, round_id):
    """流式调用并累积 delta（思考/正文/工具调用），返回 SimpleNamespace 形式的 msg。

    思考增量实时打印到控制台，并写入 reasoning_live.json 供 web 轮询。
    流式传输本身不改变 token 计费（仅传输方式不同），无额外成本。
    """
    stream = client.chat.completions.create(
        model=MODEL, messages=messages, tools=tools.TOOLS, stream=True
    )
    content = ""
    reasoning = ""
    tool_acc = {}  # tool_call index -> {"id","name","arguments"}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if not delta:
            continue
        r = getattr(delta, "reasoning_content", None)
        if r:
            reasoning += r
            print(r, end="", flush=True)
            write_reasoning_live(round_id, reasoning)
        if delta.content:
            content += delta.content
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                acc = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments
    print()  # 思考（若有）结束换行；无思考时也换行，避免与后续输出粘连
    tool_calls = [
        SimpleNamespace(
            id=tool_acc[i]["id"], type="function",
            function=SimpleNamespace(name=tool_acc[i]["name"], arguments=tool_acc[i]["arguments"]),
        )
        for i in sorted(tool_acc)
    ]
    return SimpleNamespace(
        content=content or None,
        reasoning_content=reasoning or None,
        tool_calls=tool_calls or None,
    )


def _is_sensitive_path(path):
    """拒绝敏感文件：点文件（.env 等）、*.log、探针/临时脚本——防止密钥/日志被本机 HTTP 下载"""
    name = path.split("?")[0].lstrip("/")
    for part in name.split("/"):
        low = part.lower()
        if low.startswith(".") or low.endswith(".log"):
            return True
        if low.startswith("probe_") or low.startswith("_"):
            return True
    return False


class _Handler(SimpleHTTPRequestHandler):
    """最小暴露：只放行 web/ 与 output/ 两个子目录的文件（/ 映射到 /web/index.html），其余一律 404；目录请求也 404，避免列目录枚举文件"""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        if _is_sensitive_path(self.path):
            self.send_error(404)
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            path = "/web/index.html"
            self.path = path
        # 白名单：只有 /web/ 与 /output/ 前缀可达；以 / 结尾的目录请求一律 404（无 index.html 不列目录）
        if not (path.startswith("/web/") or path.startswith("/output/")) or path.endswith("/"):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, *args):
        pass  # 静默访问日志


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # Windows 端口复用坑：固定端口 fallback 必须（见常量区注释）


def start_server(root, port=0):
    """启动 stdlib HTTP 服务（后台线程），返回 (httpd, 实际端口)；端口被占时返回 (None, None)"""
    handler = partial(_Handler, directory=root)
    try:
        httpd = _Server(("127.0.0.1", port), handler)
    except OSError:
        return None, None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def parse_args():
    p = argparse.ArgumentParser(description="XOR 画布选图案 Agent")
    p.add_argument("--seed", type=int, default=0, help="种子区域值（默认 0）")
    p.add_argument("--seed-index", type=int, default=0, help="种子区域编号 0-63（默认 0）")
    p.add_argument("--rounds", type=int, default=MAX_LOOPS, help=f"最多循环次数（默认 {MAX_LOOPS}）")
    p.add_argument("--clicks", type=int, default=MAX_CLICKS, help=f"最多点击次数（默认 {MAX_CLICKS}）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"可视化端口，0=随机（默认固定 {DEFAULT_PORT}，被占时自动 fallback 随机）")
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--detailed", action="store_true",
                   help="详细日志（模型思考+工具调用原文）；也可用环境变量 DETAILED_LOG=1/true/yes 开启")
    return p.parse_args()


def main():
    args = parse_args()
    # 必须先加载 .env 再读环境变量：DETAILED_LOG 可能写在 .env 里。
    # 原来 load_dotenv 只在 get_api_key() 里调用，晚于下面的 detailed 判断，导致 .env 里的 DETAILED_LOG 永远读不到
    load_dotenv(os.path.join(BASE, ".env"))
    # 详细日志开关：命令行 --detailed 或环境变量 DETAILED_LOG=1/true/yes（与 AGENTS.md、env.example 约定一致）
    detailed = args.detailed or os.getenv("DETAILED_LOG", "").strip().lower() in ("1", "true", "yes")
    log_file = os.path.join(BASE, get_log_filename("xor_agent"))  # 日志固定写到 BASE（不依赖 cwd）
    if detailed:
        write_log("详细日志已开启（--detailed / DETAILED_LOG）", log_file)

    xor_world.init(seed_value=args.seed, seed_index=args.seed_index)
    write_log(f"种子: 区域 {args.seed_index} = {args.seed}", log_file)
    write_log("画布: 8×8 区域，每区参数 -64..63；种子区域锁定不可修改", log_file)

    # 实时可视化 HTTP 服务（stdlib，无框架）：固定端口，被占时 fallback 随机；自动打开浏览器
    httpd, port = start_server(BASE, args.port)
    if httpd is None:
        write_log(f"端口 {args.port} 被占用，fallback 到随机端口", log_file)
        httpd, port = start_server(BASE, 0)
    if httpd is None:
        raise SystemExit("错误：无法启动 HTTP 服务")
    url = f"http://127.0.0.1:{port}/"
    write_log(f"实时可视化: {url}", log_file)
    if not args.no_open:
        opened = webbrowser.open(url)
        write_log(f"自动打开浏览器（--no-open 可关闭）：{'成功' if opened else '失败'}", log_file)

    step = 0
    xor_world.snapshot(step, out_dir=OUT)  # 初始快照
    step += 1

    client = OpenAI(api_key=get_api_key(), base_url="https://api.deepseek.com")
    messages = [{"role": "system", "content": build_system_prompt(args.seed, args.seed_index)}]

    click_count = 0
    tool_call_count = 0
    consecutive_invalid = 0
    reason = ""

    loops = 0  # --rounds 0 时 for 循环不执行，避免统计日志引用未定义变量
    for loops in range(1, args.rounds + 1):
        write_log(f"--- 第 {loops} 次循环 ---", log_file)
        print(f"[请求模型 第{loops}次] ", end="", flush=True)
        t0 = time.monotonic()
        try:
            msg = chat_stream(client, messages, loops)
        except Exception as exc:
            clear_reasoning_live()
            status = getattr(exc, "status_code", None)
            write_log(
                f"API 请求失败（第 {loops} 次循环）: {type(exc).__name__} status_code={status}",
                log_file,
            )
            resp = getattr(exc, "response", None)
            if resp is not None:
                write_log(f"响应体: {resp.text}", log_file)
            raise
        write_log(f"本轮请求耗时 {time.monotonic() - t0:.1f}s", log_file)
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
            result = tools.execute_tool(name, arguments, reasoning=reasoning, round_id=loops)
            log_tool_call(name, tc.id, arguments, result, log_file)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            xor_world.snapshot(step, out_dir=OUT)  # 每次工具调用后刷新可视化快照
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
    xor_world.snapshot(step, out_dir=OUT)
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
    clear_reasoning_live()  # 运行结束，删除实时思考中间文件（web 面板随之隐藏）
    print(result_text)


if __name__ == "__main__":
    main()
