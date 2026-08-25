"""探针模板：模拟数据流驱动浏览器实测前端交互（browser-ui-test skill）

用法：
1. 复制本文件到项目根目录改名 probe_<场景>.py（如 probe_follow.py）；
   按被测场景调整 pump()：铺历史（轮数/文本长度）、持续追加（是否逐次增长模拟"移动靶"）。
2. 后台启动：python -u probe_<场景>.py（print 已 flush，可读实际端口）。
3. browser_use 子代理 navigate 到 http://127.0.0.1:<port>/ 实测。
4. 测完清理：进程可能脱离终端 wrapper 存活 → netstat 找 PID + taskkill（见 SKILL.md 操作步骤）。
"""
import threading
import time

import agent
import xor_world

PORT = 8899  # 固定端口便于连接；改 0 为随机端口（读打印的实际端口）

httpd, port = agent.start_server(agent.BASE, PORT)
print(f"[0] server port={port}", flush=True)


def pump():
    # 1) 铺历史：多轮思考+点击，reasoning 写长文本撑高时间线容器使其可滚动
    xor_world.init(seed_value=5, seed_index=0)
    line = "第{}段思考。模拟流式 delta 文本，撑高时间线容器使其出现滚动条。"
    for r in range(1, 16):
        agent.write_reasoning_live(r, "\n".join(line.format(r) for _ in range(6)))
        for i in range(4):
            idx = r * 4 + i
            if idx >= 63:
                break
            reason = f"第{r}轮思考：\n" + "\n".join(line.format(r) for _ in range(5))
            xor_world.set_region(idx, idx - 32, reasoning=reason, round_id=r)
            agent.snap(idx)
        time.sleep(0.15)
    # 2) 持续追加实时流：测"移动靶"（scrollHeight 持续变大）时文本要逐次增长，
    #    等长文本 scrollHeight 不变，测不出移动靶效果
    k = 0
    while True:
        k += 1
        agent.write_reasoning_live(16, "第16轮实时思考…" + "追加文本" * (5 + k))
        time.sleep(0.1)


threading.Thread(target=pump, daemon=True).start()
# 3) 主线程保活：不要用 input()（非交互终端立即 EOF → httpd.shutdown() 提前退出）
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
httpd.shutdown()
