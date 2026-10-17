"""假 Agent 回归：用假流式 client 驱动真实 agent.main()，验证终止条件与点击计数口径。

不消耗 API token：把 `agent.chat_stream` 换成一个按脚本逐轮返回工具调用的假函数，
其余（工具执行、快照、按轮落盘、终止判定、结果判定）全走真实主循环。
`tests/sim_agent.py` 干不了这件事——它按日志回放，绕过主循环的计数器与终止判定。

覆盖 6 条用例（2026-10-17 新增，起因见 context-log/2026-10-17_重复点击提前终止.md）：
1. 正常路径：63 格各设定 1 次 → 成功，无提前终止。
2. 每格各设定 2 次（重复 63 次）→ 仍成功：不再有"总点击上限"。
3. 单格设定 4 次 → 第 4 次被拒（返回不带 Invalid），值保持第 3 次的结果。
4. 连撞单格上限 3 次 → 不得被误判成"连续 3 次无效点击"提前终止。
5. 连撞种子区域 3 次 → 仍按"连续 3 次无效点击"终止（无效点击计数没被破坏）。
6. 工具调用达到 240 次上限 → 以该原因终止，成功点击 189 次（63 格 × 3 次）。

用法：python tests/fake_agent.py     # 全部用例；末行打印"全部通过"即成功
"""

import glob
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)  # 脚本位于 tests/ 子目录：先让项目根可导入，再 import agent 等

os.environ.setdefault("DEEPSEEK_API_KEY", "fake-key-for-tests")  # main() 只检查是否存在；无网络请求

import agent
import xor_world

SEED_INDEX = 0


def _tc(i, name, index=None, value=None):
    """构造一个工具调用（与 SDK 返回的结构同形）"""
    args = {}
    if name in ("set_region", "view_region"):
        args["index"] = index
    if name == "set_region":
        args["value"] = value
    return SimpleNamespace(
        id=f"call_{i}", type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _sets(base, indices, value_of):
    """一批 set_region 调用（id 用 base 偏移，保证轮内 id 唯一）"""
    return [_tc(base + i, "set_region", index, value_of(index)) for i, index in enumerate(indices)]


def _run(rounds):
    """按 rounds 脚本跑一次真实 main()；返回 (stdout 文本, 日志末行统计)。

    rounds：列表，每轮是工具调用列表；脚本用尽后模型直接输出文字结束。
    每次运行产生的日志与 output/<运行ID>/ 文件夹在结束时删除（output/ 只保留真实运行存档）。
    """
    logs_before = set(glob.glob(os.path.join(BASE_DIR, "xor_agent_*.log")))
    dirs_before = set(glob.glob(os.path.join(BASE_DIR, "output", "*")))

    script = list(rounds)

    def fake_chat_stream(client, messages, round_id):
        if not script:
            return SimpleNamespace(content="最终总结：完成。", reasoning_content="收尾", tool_calls=None)
        return SimpleNamespace(content=None, reasoning_content=f"第{round_id}轮思考", tool_calls=script.pop(0))

    agent.chat_stream = fake_chat_stream
    agent.RUN_ID = agent.RUN_DIR = agent.REASONING_LIVE = None  # 每次运行独立文件夹（幂等缓存需重置）
    sys.argv = ["agent.py", "--no-open", "--port", "0"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            agent.main()
    finally:
        for f in set(glob.glob(os.path.join(BASE_DIR, "xor_agent_*.log"))) - logs_before:
            os.remove(f)
        for d in set(glob.glob(os.path.join(BASE_DIR, "output", "*"))) - dirs_before:
            shutil.rmtree(d, ignore_errors=True)

    out = buf.getvalue()
    stats = [l for l in out.splitlines() if l.startswith("[") and "统计：" in l]
    return out, (stats[-1] if stats else "")


def case_normal():
    """1. 正常路径：63 格各 1 次"""
    out, stats = _run([_sets(0, range(1, 64), lambda i: i)])
    assert xor_world.coverage() == 63, xor_world.coverage()
    assert xor_world.state["status"] == "success", xor_world.state["status"]
    assert xor_world.state["final_reason"] == "模型直接输出文字，未再调用工具", xor_world.state["final_reason"]
    assert "成功点击 63 次" in stats and "重复调整 0 次" in stats, stats
    print("1. 正常路径 OK：63 格各 1 次 → 成功，重复调整 0 次")


def case_repeat_once():
    """2. 每格各 2 次：重复 63 次也不得提前终止"""
    rounds = [_sets(0, range(1, 64), lambda i: i), _sets(100, range(1, 64), lambda i: i)]
    out, stats = _run(rounds)
    assert xor_world.coverage() == 63, xor_world.coverage()
    assert xor_world.state["status"] == "success", xor_world.state["status"]
    assert "成功点击 126 次" in stats and "重复调整 63 次" in stats, stats
    assert "点击达到" not in out, [l for l in out.splitlines() if "点击达到" in l]
    print("2. 每格 2 次 OK：126 次点击仍成功（总点击上限已去掉）")


def case_cap_reject():
    """3. 单格第 4 次被拒，值保持第 3 次的结果"""
    first = [_tc(i, "set_region", 5, v) for i, v in enumerate((1, 2, 3, 7))]
    out, _ = _run([first])
    assert xor_world.state["grid"][5] == 3, xor_world.state["grid"][5]
    assert xor_world.set_count(5) == 3, xor_world.set_count(5)
    assert "已设定 3 次（上限 3 次）" in out, [l for l in out.splitlines() if "上限" in l]
    assert "Invalid" not in out, [l for l in out.splitlines() if "Invalid" in l]
    print("3. 单格上限 OK：第 4 次被拒、值不变、不计 Invalid")


def case_cap_no_invalid_termination():
    """4. 连撞单格上限 3 次不得终止运行"""
    rounds = [
        _sets(0, [5, 5, 5], lambda i: 1),        # 用满 3 次
        _sets(100, [5, 5, 5], lambda i: 2),      # 连续 3 次被拒
        _sets(200, [6], lambda i: 7),            # 运行应继续
    ]
    _run(rounds)
    assert xor_world.state["final_reason"] == "模型直接输出文字，未再调用工具", xor_world.state["final_reason"]
    assert xor_world.state["grid"][6] == 7, xor_world.state["grid"][6]
    assert xor_world.set_count(5) == 3, xor_world.set_count(5)
    print("4. 撞上限不误终止 OK：连续 3 次超上限后运行继续")


def case_seed_invalid():
    """5. 连撞种子区域 3 次仍按连续无效点击终止"""
    rounds = [[_tc(i, "set_region", SEED_INDEX, 5) for i in range(3)]]
    _run(rounds)
    # 每轮 3 个调用：第 3 个触发终止，前两个已执行
    assert xor_world.state["final_reason"] == "连续 3 次无效点击", xor_world.state["final_reason"]
    assert xor_world.state["grid"][SEED_INDEX] == 0, xor_world.state["grid"][SEED_INDEX]
    assert xor_world.coverage() == 0, xor_world.coverage()
    print("5. 种子不可变 OK：连续 3 次无效点击 → 终止，种子值未变")


def case_tool_cap():
    """6. 工具调用 240 次上限：63 格用满 3 次（189 次点击）+ 51 次 evaluate"""
    rounds = [
        _sets(0, range(1, 64), lambda i: 1),
        _sets(100, range(1, 64), lambda i: 2),
        _sets(200, range(1, 64), lambda i: 3),
        [_tc(300 + i, "evaluate") for i in range(60)],
    ]
    out, stats = _run(rounds)
    assert xor_world.state["final_reason"] == f"工具调用达到 {agent.MAX_TOOL_CALLS} 次", xor_world.state["final_reason"]
    assert "成功点击 189 次" in stats and "重复调整 126 次" in stats, stats
    assert xor_world.coverage() == 63, xor_world.coverage()
    print(f"6. 工具调用上限 OK：{agent.MAX_TOOL_CALLS} 次终止，点击 189 次（63 格 × 3）")


def main():
    xor_world.init(seed_value=0, seed_index=SEED_INDEX)
    case_normal()
    case_repeat_once()
    case_cap_reject()
    case_cap_no_invalid_termination()
    case_seed_invalid()
    case_tool_cap()
    print("\n全部通过 OK")


if __name__ == "__main__":
    main()
