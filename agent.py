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
import posixpath
import queue
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler
from types import SimpleNamespace

from dotenv import load_dotenv, dotenv_values
from openai import OpenAI

import pattern_desc
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

# --- SSE 长连接推送（实时可视化主通道） ---
# EventSource 单向推送：思考 delta / 快照更新到达即推给浏览器，前端无需高频轮询。
# 每个连接一个队列，广播时只保留最新事件（快照语义，丢弃中间版本无妨）。
_sse_lock = threading.Lock()
_sse_clients = set()  # {queue.Queue}


def _sse_broadcast(event, data):
    """向所有 SSE 连接广播一个事件；data 会 json 序列化（单行，无裸换行，SSE data 行安全）"""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.get_nowait()  # 丢弃旧事件，只留最新
            except queue.Empty:
                pass
            q.put_nowait(payload)


def build_system_prompt(seed_value, seed_index):
    return f"""你是一个数字艺术家，正在为一幅 512×512 的 XOR 图案画布选择参数。画布分为 8×8=64 个区域，每区一个参数值 a（整数，-64..63），该值决定区域内的图案。

{pattern_desc.pattern_doc()}

任务：
- 区域 {seed_index}（种子区域）已固定为 {seed_value}，不可修改。
- 请用 set_region 工具为其余 63 个区域逐一选择参数值（一次点击一个区域）。
- 可随时用 view_region 查看某区域当前值与图案描述，用 evaluate 查看整幅画布的进度。

目标：
- 把整幅画布当作你的作品来设计：先想清楚每块区域要什么图案，再选择能画出该图案的参数值。
- 设定后用 view_region 核对实际图案与你的意图是否一致，不一致就调整。
- 每个非种子区域都要被你点击设定过：未点击的区域默认纯黑、视为未处理。刻意选用 a=0（纯黑）作为图案是允许的，但要显式点击设定。
- 建议先 evaluate 了解初始状态，再规划点击顺序。

结束：当画布已足够好或点击次数将尽时，直接输出一段最终总结文字即可（不再调用工具）。"""


def get_api_key_or_none():
    """从 BASE/.env 读取 API 密钥（不依赖 cwd）；未配置返回 None。
    重复调用会重读 .env：支持运行中补配密钥后热加载（等待配置逻辑依赖此行为）。"""
    load_dotenv(os.path.join(BASE, ".env"))
    return os.getenv("DEEPSEEK_API_KEY") or None


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
    """原子写 output/reasoning_live.json（web 兜底轮询显示实时思考）并 SSE 推送；文件不存在时 web 隐藏该面板"""
    try:
        os.makedirs(OUT, exist_ok=True)
        payload = json.dumps({"round": round_id, "reasoning": reasoning}, ensure_ascii=False)
        tmp = REASONING_LIVE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, REASONING_LIVE)
        _sse_broadcast("reasoning", {"round": round_id, "reasoning": reasoning})
    except OSError:
        pass


def clear_reasoning_live():
    try:
        os.remove(REASONING_LIVE)
    except OSError:
        pass
    _sse_broadcast("reasoning", {"round": -1, "reasoning": ""})


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
    if reasoning:
        # 兜底写：流式结束确保 reasoning_live.json 含本轮完整思考。
        # 若 API 一次性返回 reasoning（非增量 delta），上面的增量写只发生在最后一瞬，
        # web 1 秒轮询可能错过写入窗口；此处兜底保证文件必有内容（下一轮覆盖或结束清除）。
        write_reasoning_live(round_id, reasoning)
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
    # HTTP/1.1：SSE 长连接必需（HTTP/1.0 无标准 keep-alive 流）；静态响应均带 Content-Length，兼容连接复用
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        # 先解码再规范化路径再检查：URL 编码的路径穿越（%2e%2e=..、%2eenv=.env）可绕过
        # 未解码路径上的白名单与敏感检查，translate_path 解码 normpath 后落到根目录文件
        # （实测可下载 .env 与 agent.py 源码，见 context-log/2026-08-22_HTTP路径穿越密钥泄露.md）
        raw = urllib.parse.unquote(self.path.split("?")[0])
        if raw == "/events":
            self._serve_sse()
            return
        if raw.endswith("/") and raw != "/":  # 目录请求一律 404（含编码的 /web/%2e%2e/ 等）
            self.send_error(404)
            return
        path = posixpath.normpath(raw)
        # 拒绝控制字符（空字节 %00、CR/LF 等）：文件系统不接受，且实测 %00 会让服务器异常断连而非 404
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
            self.send_error(404)
            return
        if _is_sensitive_path(path):
            self.send_error(404)
            return
        if path in ("/", "/index.html"):
            path = "/web/index.html"
            self.path = path
        # 白名单：只有 /web/ 与 /output/ 前缀可达（规范化后，../ 已消解）
        if not (path.startswith("/web/") or path.startswith("/output/")):
            self.send_error(404)
            return
        if path.endswith(".html"):
            # 页面禁用缓存：浏览器缓存旧版 HTML 会让前端修复不生效（实测多次踩坑），
            # 重新导航/刷新页面必须拿到最新版；测试页（layout_*.html）同样适用
            full = self.translate_path(self.path)
            if not os.path.isfile(full):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(os.path.getsize(full)))
            self.end_headers()
            with open(full, "rb") as f:
                self.wfile.write(f.read())
            return
        super().do_GET()

    def _serve_sse(self):
        """SSE 长连接：浏览器 EventSource 订阅，接收 reasoning（思考增量）与 state（快照）事件。
        每连接一个队列 + 专用线程阻塞消费；空闲 15s 发注释心跳保活。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = queue.Queue()
        with _sse_lock:
            _sse_clients.add(q)
        try:
            while True:
                try:
                    payload = q.get(timeout=15)
                except queue.Empty:
                    payload = ": heartbeat\n\n"  # 空闲保活，防中间层/浏览器超时
                try:
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    break  # 浏览器刷新/关闭连接，正常退出
        finally:
            with _sse_lock:
                _sse_clients.discard(q)

    def log_message(self, *args):
        pass  # 静默访问日志


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # Windows 端口复用坑：固定端口 fallback 必须（见常量区注释）
    daemon_threads = True  # SSE 长连接线程随进程退出，否则 main 结束后脚本挂住不退出

    def handle_error(self, request, client_address):
        """浏览器在响应中途关闭/刷新连接（WinError 10053/10054、BrokenPipe）属正常现象，
        不打印 traceback 刷屏；其余异常仍按默认处理（见 context-log/2026-08-06_HTTP连接中止刷屏修复）"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


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


def snap(step):
    """快照 + SSE 广播 state（web 实时收到更新，无需轮询等文件）"""
    xor_world.snapshot(step, out_dir=OUT)
    _sse_broadcast("state", xor_world.state)


def main():
    args = parse_args()
    # 必须先加载 .env 再读环境变量：DETAILED_LOG 可能写在 .env 里。
    # 原来 load_dotenv 只在 get_api_key() 里调用，晚于下面的 detailed 判断，导致 .env 里的 DETAILED_LOG 永远读不到
    load_dotenv(os.path.join(BASE, ".env"))  # 密钥等仍走 .env→os.environ（默认不覆盖已有环境变量）
    # 详细日志开关：--detailed，或 DETAILED_LOG 为 1/true/yes（进程环境或 .env 任一命中即开启）。
    # 必须再用 dotenv_values 直读 .env：若进程环境残留 DETAILED_LOG=0（setx/profile 遗留），
    # load_dotenv 默认 override=False 不会用 .env 的 1 覆盖它，会静默压掉开关（实测根因，见 context-log）。
    env_val = os.getenv("DETAILED_LOG", "")
    env_file_val = (dotenv_values(os.path.join(BASE, ".env")).get("DETAILED_LOG") or "")
    on_list = ("1", "true", "yes")
    detailed = args.detailed or env_val.strip().lower() in on_list or env_file_val.strip().lower() in on_list
    log_file = os.path.join(BASE, get_log_filename("xor_agent"))  # 日志固定写到 BASE（不依赖 cwd）
    # 启动诊断（保留）：同时显示进程环境与 .env 两个来源，排查"设了但没生效"直接看日志即可定位
    write_log(f"DETAILED_LOG 进程环境={env_val!r} .env={env_file_val!r} → detailed={detailed}", log_file)
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
    snap(step)  # 初始快照
    step += 1

    # 无密钥不退出：服务已启动，网页显示配置引导，后台轮询 .env，检测到密钥后自动开始。
    # 不能在这里直接 SystemExit：进程退出会杀死服务线程，浏览器只剩一个连不上的死页面（原 bug 根因）。
    api_key = get_api_key_or_none()
    if api_key is None:
        xor_world.state["status"] = "no_key"
        snap(step)
        step += 1
        write_log("未找到 DEEPSEEK_API_KEY：进入等待配置状态（网页显示引导，配置后自动续跑，无需重启）", log_file)
        print("未找到 DEEPSEEK_API_KEY：网页已显示配置引导。将 env.example 复制为 .env 并填写密钥，保存后自动继续。")
        while api_key is None:
            time.sleep(2)
            api_key = get_api_key_or_none()
        write_log("检测到 DEEPSEEK_API_KEY，自动开始运行", log_file)
        xor_world.state["status"] = "running"
        snap(step)
        step += 1

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
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

            snap(step)  # 每次工具调用后刷新可视化快照（快照 + SSE 广播）
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
    xor_world.state["status"] = "success" if success else "incomplete"
    xor_world.state["final_reason"] = reason or "达到循环上限"
    snap(step)
    result_text = (
        f"{'成功' if success else '失败'}：{reason or '达到循环上限'}。"
        f"已点击 {coverage}/63 个非种子区域。"
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
