# DETAILED_LOG 环境变量不生效：根因与修复

日期：2026-08-06

## 现象

- 文档（AGENTS.md L36、env.example L4-6）声称 `DETAILED_LOG=1` 等价命令行 `--detailed`。
- 用户实测 DETAILED_LOG=1 下日志仍无"模型思考"，也无"详细日志已开启"。

## 数据流（完整梳理）

输入侧 A（DETAILED_LOG → detailed 变量）：
- 入口只有两个：shell 环境变量（`os.getenv`，agent.py L258）与 `.env` 文件（`load_dotenv(BASE/.env)`，L256）。
- **`env.example` 无任何代码读取**（grep 证实，.py 中仅 L93 报错文案提到它），只是模板。
- `if detailed: write_log("详细日志已开启...")`（L263）是 detailed 是否为真的唯一落盘证据，且先于"种子:"行写出。

输出侧 B（模型思考 → 日志）：
- `chat_stream` 流式累积 `delta.reasoning_content` → `msg.reasoning_content`（L147-184）。
- `log_model_response(msg, log_file, detailed)`（logger.py L31-45）：detailed=True 且 reasoning 非空才写"模型思考"。

## 已修复的两个缺陷

1. 环境变量实现缺失（turn 1）：`detailed = args.detailed or os.getenv(...)` + --help 文案。
2. 顺序 bug（turn 3）：`detailed` 判断先于 `load_dotenv`（原 load_dotenv 在 get_api_key 里）→ `.env` 里的 DETAILED_LOG 永远读不到。已改为 main() 开头先 load_dotenv（L256）。顺序复现：先算后 load = False，先 load 后算 = True。

## 验证（真实代码，无密钥）

- 输出侧：`log_model_response(detailed=True)` 写入"模型思考: ..."；detailed=False 只写"模型输出"。通过。
- 输入侧（shell 路径）：`$env:DETAILED_LOG="1"; python agent.py --no-open`（沙箱无 .env/密钥，写到启动日志后按预期退出）→ 日志前两行：
  ```
  DETAILED_LOG='1' → detailed=True
  详细日志已开启（--detailed / DETAILED_LOG）
  ```
  通过。

## 最终根因（21:36 假密钥测试确认）

用户流程：改 env.example（取消 L6 注释）→ `cp env.example .env` → 手动把 .env 密钥改成假值 sk-abcd。
21:36 测试日志第一行：`DETAILED_LOG='0'` —— **进程环境里已存在一个 DETAILED_LOG=0**（setx / PowerShell profile 遗留，疑似此前"命令行AI思考刷屏"实验产物），而 load_dotenv 默认 `override=False` 不覆盖已有环境变量 → 0 压过 .env 的 1 → detailed=False。
这解释了 20:58、21:22、21:36 三次失败的共同机制。

## 修复（turn 4）

DETAILED_LOG 判定改为"进程环境 **或** .env 任一命中 1/true/yes 即开启"：
```python
env_val = os.getenv("DETAILED_LOG", "")
env_file_val = (dotenv_values(os.path.join(BASE, ".env")).get("DETAILED_LOG") or "")
on_list = ("1", "true", "yes")
detailed = args.detailed or env_val.strip().lower() in on_list or env_file_val.strip().lower() in on_list
```
- 用 dotenv_values 直读 .env，绕开 override 语义（密钥仍走 load_dotenv，优先级不变）。
- 诊断升级为同时显示两个来源：`DETAILED_LOG 进程环境='0' .env='1' → detailed=True`。
- 验证：模拟"进程环境=0 + .env=1"→ detailed=True；真实代码跑通（`进程环境='1' .env='1' → detailed=True`，fake key 从 .env 生效进 API）。

## 遗留待用户处理

- 定位残留 DETAILED_LOG=0 的来源并清理：`$env:DETAILED_LOG`、`[Environment]::GetEnvironmentVariable('DETAILED_LOG','User'/'Machine')`、$PROFILE。
- 21:36 建的假密钥 .env（sk-abcd）仍在仓库根目录，真实运行前须换真密钥或删除。

## 其他（徒手写的）

在这之前用AI检查一遍发现，DETAILED_LOG=1在代码里面没找到相应实现，当时AI就改代码，而不是删文档里的说法，就是测试结果不符合预期，才一直排查，最后临时用了个假密钥测试。部分记录已经丢失。

假密钥测试的结果详见 [2026-08-06_DETAILED_LOG环境变量不生效-假密钥测试结果.md](2026-08-06_DETAILED_LOG环境变量不生效-假密钥测试结果.md)。

假密钥已经清理掉。

DETAILED_LOG=0 似乎是测试 DETAILED_LOG=1 是否有用的时候，由于误输入 `$env:DETAILED_LOG="1"`，为了避免测不出来，就加了个 `$env:DETAILED_LOG="0"`——问题有可能在这个地方——结果导致 DETAILED_LOG=1 没有起到作用？
