# Context Log - Windows 端口复用坑：allow_reuse_address=1 导致固定端口 fallback 失效

创建时间: 2026-08-06
来源: 命令行 AI 思考刷屏修复方案探针测试（probe_fixed_port_auto_open.py）
测试记录: [2026-08-06_命令行AI思考刷屏修复方案测试.md](2026-08-06_命令行AI思考刷屏修复方案测试.md)（用户徒手记录）

## 现象

- 探针 C（固定端口 8123 + 被占时 fallback）在 8123 已被 `python -m http.server 8123 --bind 127.0.0.1` 占用时，**没有打印 fallback**，仍显示 `http://127.0.0.1:8123/`；
- 浏览器打开该网址，显示的是**占用者的内容**（C:\Users\<用户名> 目录列表，第一个链接 #Ai code DeepSeek 2.v#），而非项目目录；
- `netstat` 显示 127.0.0.1:8123 有**两个进程同时 LISTENING**。

## 根因

1. `http.server.HTTPServer` 源码默认 `allow_reuse_address = 1`，`ThreadingHTTPServer` 继承之；
2. Windows 的 SO_REUSEADDR 语义与 Unix 不同：设置后**允许两个 socket 绑定同一已监听端口**（Unix 仅允许 TIME_WAIT 复用）；
3. 因此 `ThreadingHTTPServer(("127.0.0.1", 8123))` 在被占用时**不抛 OSError、"绑定成功"**；
4. 两个进程同时监听 → 浏览器请求被先绑定者（占用者）响应 → 显示错误内容；
5. fallback 的 `except OSError` 分支永远不触发。

## 验证（2026-08-06 实测）

- `netstat -ano | Select-String ":8123"` → 两条 LISTENING 共存（PID 9308、10844）；
- `ThreadingHTTPServer` 绑定已被占用的 8123 → 成功（无异常）；
- `socketserver.TCPServer`（allow_reuse_address=False）绑定已被占用的 8123 → OSError（WinError 10048）；
- 修复后 `make_server(8123)` 返回 None，fallback 到随机端口正常（实测 55795）。

## 影响

- **真实 agent.py 用 ThreadingHTTPServer**：若将来实现"固定默认端口 + 被占时 fallback"，必须显式关闭 `allow_reuse_address`，否则 fallback 永不触发、HTTP 服务与占用者共存（浏览器可能被劫持到错误内容）；
- 随机端口（port=0）不受影响，当前 agent.py 无此问题（隐患预留）。

## 复现步骤与修复前后对比

**复现问题（bug 版）**：
1. 终端 A：`python -m http.server 8123 --bind 127.0.0.1`（占用 8123）
2. 终端 B：运行下方 bug 版探针 → **不打印 fallback**，仍显示 `http://127.0.0.1:8123/`
3. 浏览器打开该网址 → 显示的是**终端 A 的目录**（占用者内容），而非本探针的服务内容
4. 终端 A 会看到浏览器请求的 `GET / 200` 日志（说明请求被占用者响应）

**验证已修复**：终端 B 运行下方修复版 → 打印"端口 8123 被占用，fallback 到随机端口"，浏览器打开的是随机端口 URL 且显示脚本所在目录。

### 修复前（bug 版，完整源码）

```python
"""探针 C（bug 版）：用 ThreadingHTTPServer，allow_reuse_address=1 → Windows 端口复用 → fallback 失效"""
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8123


def main():
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), SimpleHTTPRequestHandler)  # ← 问题根源
        port = PORT
    except OSError:
        print(f"[C] 端口 {PORT} 被占用，fallback 到随机端口")  # ← 永远不会执行
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
        port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"[C] 网址: {url}")
    webbrowser.open(url)
    time.sleep(8)
    httpd.server_close()


if __name__ == "__main__":
    main()
```

### 修复后（关键差异）

1. **服务器类**：`ThreadingHTTPServer`（`allow_reuse_address=1`，Windows SO_REUSEADDR 允许复用已监听端口 → 绑定被占端口不抛 OSError）→ `socketserver.ThreadingTCPServer` 子类 + `allow_reuse_address = False`（绑定被占端口抛 OSError → fallback 生效）。这是唯一修复 bug 所必需的变化。
2. **服务根目录**：默认 cwd → `directory=BASE`（脚本所在目录），落实"不暴露系统盘"约束。
3. **探针可用性（非 bug）**：固定 sleep 8 秒 → `input()` 按 Enter 结束；`server_close()` → `shutdown()+server_close()`（避免 daemon 线程退出时 Fatal Python error）。

修复版完整源码见下文"附：探针脚本代码"。

### 修复写法（探针 C 已实现并验证）

```python
class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # Windows 上强制"端口被占即失败"，fallback 才有效

def make_server(port):
    handler = partial(SimpleHTTPRequestHandler, directory=BASE)
    try:
        return _Server(("127.0.0.1", port), handler)
    except OSError:
        return None
```

## 安全约束（用户提出）

HTTP 服务根目录必须显式固定为**脚本所在目录**（`os.path.dirname(os.path.abspath(__file__))`），不依赖 cwd——否则从系统盘（如 C:\Users\<用户名>）启动会暴露该目录内容。

补充（2026-08-06）：安全约束已进一步收紧为路径白名单——服务只放行 `/web/` 与 `/output/` 两个前缀，其余一律 404，目录请求不列目录（详见 [2026-08-06_http服务敏感文件拒绝.md](2026-08-06_http服务敏感文件拒绝.md)）。"根目录固定 BASE"是目录级约束（防 cwd 漂移），"白名单"是路径级过滤（防根目录内容被下载），两者互补。

## 附：探针脚本代码（防清理时丢失）

### probe_fixed_port_auto_open.py（完整，含修复核心）

```python
"""探针 C：固定端口 + 自动打开浏览器（组合方案）。

测试目的：验证"网址可预测 + 浏览器自动打开"叠加的效果，以及端口占用时 fallback 是否生效。
徒手测试：
  python probe_fixed_port_auto_open.py
  python probe_fixed_port_auto_open.py --no-open
  模拟端口占用（必须绑 127.0.0.1 才能真正占用本探针的地址）：
    另开终端先执行 `python -m http.server 8123 --bind 127.0.0.1`，再运行本脚本，
    观察是否打印"端口 8123 被占用，fallback 到随机端口"。

注：本探针用 allow_reuse_address=False 的服务器类。若直接用 http.server.ThreadingHTTPServer，
Windows 的 SO_REUSEADDR 语义会允许与占用者"共存绑定"同一端口（fallback 永远不触发、
浏览器请求会被占用者响应），真实 agent.py 也存在同样隐患。
"""

import argparse
import os
import socketserver
import threading
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler

PORT = 8123
BASE = os.path.dirname(os.path.abspath(__file__))  # 服务根目录=脚本所在目录，不暴露 cwd/系统盘


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # Windows 上强制"端口被占即失败"，fallback 才有效


def make_server(port):
    handler = partial(SimpleHTTPRequestHandler, directory=BASE)
    try:
        return _Server(("127.0.0.1", port), handler)
    except OSError:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = p.parse_args()

    httpd = make_server(PORT)
    if httpd is None:
        print(f"[C] 端口 {PORT} 被占用，fallback 到随机端口")
        httpd = make_server(0)
        port = httpd.server_address[1]
    else:
        port = PORT
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    urllib.request.urlopen(url)  # 确认服务就绪
    print(f"[C] 网址: {url}")
    if not args.no_open:
        opened = webbrowser.open(url)
        print(f"[C] 自动打开浏览器：{'成功' if opened else '失败（webbrowser.open 返回 False）'}")
    else:
        print("[C] --no-open 生效：不自动打开浏览器")
    input("[C] 观察浏览器是否自动打开且 URL 正确；按 Enter 结束...")
    httpd.shutdown()
    httpd.server_close()


if __name__ == "__main__":
    main()
```

### 其他探针关键点（同批清理）

- `probe_auto_open.py`（方案 B）：随机端口 + 自动打开；服务根目录 `partial(SimpleHTTPRequestHandler, directory=BASE)`，BASE=脚本目录；
- `probe_fixed_port.py`（方案 A）：固定端口 fallback；用 `socketserver.TCPServer`（默认 allow_reuse_address=False，fallback 天然正常）；同样 directory=BASE；
- `probe_ui_launch.py`（方案 D）：UI 启动原型；两个坑——① 子进程 stdout 管道在 Windows 下默认 GBK 编码，父进程按 utf-8 解码会崩（UnicodeDecodeError），需 `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` + `errors="replace"`；② `python -c` 内联脚本不能写 `import sys,time; for i in range(50): ...`——分号只能连接**简单语句**，`for` 是复合语句，跟在分号后 SyntaxError，必须换行分隔。

## 备注

- 探针为临时测试脚本，选定方案后删除；本文件保留修复逻辑与安全约束，后续改 agent.py 时复用。
