"""离线自测（不依赖 API）：渲染、指标、工具、种子锁定、快照。

运行: python tests/selftest.py
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)  # 脚本位于 tests/ 子目录：先让项目根可导入，再 import agent 等

import xor_world
import tools


def test_stream_accumulation():
    """流式累积逻辑离线验证：思考/正文/工具调用增量拼接（不依赖 API）"""
    from types import SimpleNamespace

    import agent

    def mk_chunk(reasoning="", content="", tool_calls=None):
        delta = SimpleNamespace(
            reasoning_content=reasoning or None, content=content or None, tool_calls=tool_calls
        )
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def mk_tc(index, id_="", name="", args=""):
        return SimpleNamespace(
            index=index, id=id_ or None,
            function=SimpleNamespace(name=name or None, arguments=args or None),
        )

    chunks = [
        mk_chunk(reasoning="思考第"),
        mk_chunk(reasoning="一段。"),
        mk_chunk(content="输出"),
        mk_chunk(tool_calls=[mk_tc(0, "call_1", "set_region", '{"index": 1, "value": 2')]),  # 首段不闭合
        mk_chunk(tool_calls=[mk_tc(0, None, None, ', "round": 1}')]),  # arguments 增量拼接
    ]

    class FakeCompletions:
        def create(self, model, messages, tools, stream):
            return iter(chunks)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    try:
        msg = agent.chat_stream(FakeClient(), [], 1)
        assert msg.reasoning_content == "思考第一段。", msg.reasoning_content
        assert msg.content == "输出"
        assert len(msg.tool_calls) == 1
        tc = msg.tool_calls[0]
        assert tc.id == "call_1" and tc.function.name == "set_region"
        assert tc.function.arguments == '{"index": 1, "value": 2, "round": 1}'
        m = agent.assistant_message(msg)
        assert m["tool_calls"][0]["function"]["arguments"] == '{"index": 1, "value": 2, "round": 1}'
        assert m.get("reasoning_content") == "思考第一段。"
    finally:
        agent.clear_reasoning_live()  # 清理测试期间写入的实时思考文件
    print("流式累积 OK")


def test_server():
    """HTTP 可视化服务冒烟测试：/ 映射到 index.html，/output/state.json 可达；白名单外与敏感文件一律 404"""
    import time
    import urllib.error
    import urllib.request

    import agent

    httpd, port = agent.start_server(agent.BASE, 0)
    time.sleep(0.2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            body = r.read().decode("utf-8")
            assert r.status == 200 and "<title>XOR" in body, body[:200]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/output/state.json") as r:
            assert r.status == 200
        # 白名单外一律 404：项目根普通文件、点文件（.env 等）、探针/临时脚本、目录列表（/output/ 不再列目录）
        # 路径穿越回归：原始 ../ 与 URL 编码（%2e%2e=..、%2eenv=.env、含嵌套 %252e）都必须 404
        # （见 context-log/2026-08-22_HTTP路径穿越密钥泄露.md）
        for blocked in ("/README.md", "/agent.py", "/context-log/", "/output/",
                        "/.gitignore", "/probe_ui_launch.py",
                        "/web/../agent.py", "/web/%2e%2e/agent.py", "/web/%2e%2e/%2eenv",
                        "/output/%2e%2e/%2eenv", "/output/%2e%2e/agent.py",
                        "/web/%252e%252e/%252eenv", "/web/%252e%252e/agent.py",
                        "/output/%252e%252e/%252eenv", "/web/%00", "/web/%0a"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{blocked}")
                raise AssertionError(f"白名单外路径应 404: {blocked}")
            except urllib.error.HTTPError as e:
                assert e.code == 404, f"{blocked} 应 404，实际 {e.code}"
        print("HTTP 服务 OK: / 、/output/state.json 200；白名单外/敏感文件 404")
    finally:
        httpd.shutdown()


def test_pattern_desc():
    """图案描述读取机制离线验证：md 给规则（不枚举全部图案）+ 代码按值兜底（覆盖全部 128 个值）"""
    import pattern_desc

    # md 读取：无 BOM（Windows 记事本可能加，会导致提示词首字符不可见）、关键章节齐全
    doc = pattern_desc.pattern_doc()
    assert not doc.startswith("\ufeff"), "pattern_description.md 不应含 UTF-8 BOM"
    for kw in ("# 图案描述", "计算公式", "低三位", "高三位", "多个图案的结合"):
        assert kw in doc, f"pattern_description.md 缺少关键章节: {kw}"

    # 全值域 -64..63：每个值都有确定、非空的结构描述；负值 = 其互补掩码(~a)的描述 +（反色）
    for a in range(-64, 64):
        d = pattern_desc.pattern_description(a)
        assert isinstance(d, str) and d, f"a={a} 描述为空"
        if a == 0:
            assert d == "纯黑"
        elif a == -1:
            assert d == "纯白"
        elif a < -1:
            assert d == pattern_desc.pattern_description(~a) + "（反色）", f"a={a} 与互补值口径不一致"
    print("图案描述读取 OK: md 无 BOM/章节齐全；128 个值全覆盖，负值反色口径一致")


def test_record_round():
    """按轮记录回归（2026-10-17 可视化 bug）：没有 set_region 点击的轮次，思考/正文同样必须落盘。

    原实现把本轮思考塞进 clicks[].reasoning：只查看/只评估/只输出总结的轮次没有点击，
    思考就无处落盘、界面上静默消失（实测 3 份真实运行日志各有 3-4 轮中招，第 1 轮
    5-8 万字符思考全丢）。这里锁死新口径：record_round 与点击无关，且**工具调用的参数与
    结果按调用顺序**落进 calls（可视化据此按时间顺序显示"思考 → 工具调用 → 结果"）。
    """
    xor_world.init(seed_value=7, seed_index=3)
    # 第 1 轮：只看一眼（无 set_region 点击）
    xor_world.record_round(1, reasoning="第一轮思考" * 3, content="先看看种子",
                           calls=[{"name": "view_region", "args": {"index": 3}, "result": "区域 3 当前值 7"}])
    r1 = xor_world.state["rounds"][-1]
    assert r1["clicks"] == 0
    assert r1["reasoning"].startswith("第一轮思考")
    assert r1["content"] == "先看看种子"
    assert r1["calls"][0]["result"] == "区域 3 当前值 7"      # 工具结果必须落盘（原来只在日志里）
    assert r1["tools"] == {"view_region": 1}                  # 计数视图由 calls 派生
    # 第 2 轮：两次点击，工具调用顺序与结果保留
    xor_world.set_region(1, 5, round_id=2)
    xor_world.set_region(2, 6, round_id=2)
    xor_world.record_round(2, reasoning="第二轮思考", content="",
                           calls=[{"name": "set_region", "args": {"index": 1, "value": 5}, "result": "已设为 5"},
                                  {"name": "set_region", "args": {"index": 2, "value": 6}, "result": "已设为 6"}])
    # 第 3 轮：只输出总结（无点击，正文就是最终总结）
    xor_world.record_round(3, reasoning="第三轮思考", content="最终总结正文", calls=[])
    rounds = xor_world.state["rounds"]
    assert [r["round"] for r in rounds] == [1, 2, 3], rounds
    assert [r["clicks"] for r in rounds] == [0, 2, 0], rounds
    assert all(r["reasoning"] for r in rounds), "每轮思考都必须落盘（含无点击轮次）"
    assert rounds[2]["content"] == "最终总结正文"
    assert [c["args"]["index"] for c in rounds[1]["calls"]] == [1, 2], "工具调用必须按调用顺序保存"
    # 点击轨迹不再重复保存思考（按轮存一份即可）
    assert all("reasoning" not in c for c in xor_world.state["clicks"]), xor_world.state["clicks"]
    print("按轮记录 OK: 无点击轮次的思考/正文照样落盘；工具调用与结果按顺序保存；clicks 不再重复存思考")


def test_sample_data():
    """多份样例数据可解析性（回放器 parse_log）：tests/example_data*.txt 全量遍历。

    每份须为结构完整的真实日志：有轮次/工具/模型输出，且以 finish→stats 收尾
    （历史样例格式兼容性回归，新增样例须同样能被回放器解析）。
    """
    import sim_agent  # 复用回放器：样例发现 + 日志解析

    samples = sim_agent.list_samples()
    assert len(samples) >= 1, "tests/ 至少应有一份样例数据"
    for path in samples:
        events, meta = sim_agent.parse_log(path)
        kinds = [e["kind"] for e in events]
        for need in ("round", "tool", "output", "finish", "stats"):
            assert need in kinds, f"{os.path.basename(path)} 缺 {need} 事件"
        assert kinds[-2:] == ["finish", "stats"], \
            f"{os.path.basename(path)} 结尾非 finish→stats: {kinds[-2:]}"
        assert sum(1 for k in kinds if k == "tool") >= 1, \
            f"{os.path.basename(path)} 无工具调用"
        assert isinstance(meta["seed_value"], int) and isinstance(meta["seed_index"], int)
        # 每轮思考都必须能被回放到 web（无点击轮次也要有 think 事件，否则界面上无法出现）
        rounds = {e["round"] for e in events if e["kind"] == "round"}
        thinks = {e["round"] for e in events if e["kind"] == "think"}
        assert thinks == rounds, \
            f"{os.path.basename(path)} 有轮次没有思考事件: {sorted(rounds - thinks)}"
        print(f"  样例 {os.path.basename(path)} OK: 种子 区域{meta['seed_index']}={meta['seed_value']}，"
              f"轮次 {kinds.count('round')}，工具 {kinds.count('tool')}，输出 {kinds.count('output')}")
    print(f"样例数据 OK: 共 {len(samples)} 份，全部可解析且结构完整")


def main():
    # 1. 初始化与种子锁定
    xor_world.init(seed_value=5, seed_index=0)
    assert xor_world.state["grid"][0] == 5
    r = xor_world.set_region(0, 3)
    assert r.startswith("Invalid"), f"种子应锁定: {r}"
    print("种子锁定 OK:", r)

    # 2. 参数校验（越界 index / 越界 value）
    for idx, val in [(64, 0), (-1, 0), (0, 100), (0, -70)]:
        r = xor_world.set_region(idx, val)
        assert r.startswith("Invalid"), f"应拒绝 index={idx} value={val}"
    print("参数校验 OK")

    # 3. 合法点击
    r = xor_world.set_region(1, 1)
    assert not r.startswith("Invalid"), r
    assert xor_world.state["grid"][1] == 1
    print("合法点击 OK:", r)

    # 3b. 单格设定上限：首次 + 2 次调整 = 3 次；第 4 次拒绝，且不带 Invalid 前缀
    #     （带 Invalid 会被主循环计入"连续无效点击"，模型连撞 3 次上限就误终止）
    for v in (1, 2, 3):
        r = xor_world.set_region(5, v)
        assert not r.startswith("Invalid"), r
        assert f"第 {v}/3 次设定" in r, r
    r = xor_world.set_region(5, 7)
    assert not r.startswith("Invalid"), f"超上限不是参数错误，不得带 Invalid 前缀: {r}"
    assert "已设定 3 次（上限 3 次）" in r and "未修改" in r, r
    assert xor_world.state["grid"][5] == 3, xor_world.state["grid"][5]
    assert xor_world.set_count(5) == 3, xor_world.set_count(5)
    print("单格上限 OK:", r)

    # 4. 渲染
    canvas = xor_world.render()
    assert canvas.shape == (512, 512)
    br = xor_world.black_ratio(canvas)
    assert 0.0 <= br <= 1.0
    print(f"渲染 OK, shape={canvas.shape}, 黑占比={br:.3f}")

    # 5. 全填 a=1（细棋盘格，黑白各半）→ 黑占比应恰为 0.5（渲染正确性验证）
    xor_world.state["grid"] = [1] * 64
    br = xor_world.black_ratio()
    assert abs(br - 0.5) < 1e-6, f"全 a=1 黑占比应 0.5: {br}"
    print(f"渲染验证 OK: 全 a=1 → 黑占比 {br:.4f}")

    # 6. 图案语义字典抽查
    assert "纯黑" in xor_world.pattern_description(0)
    assert "纯白" in xor_world.pattern_description(-1)
    print("图案语义:", xor_world.pattern_description(1), "|",
          xor_world.pattern_description(3), "|",
          xor_world.pattern_description(63), "|",
          xor_world.pattern_description(-64))

    # 6b. 图案描述读取机制全量验证（md 读取 + 128 值全覆盖）
    test_pattern_desc()

    # 6c. 0 歧义回归：刻意设 a=0（纯黑）算已设定；未设定只认"未点击过"
    xor_world.set_region(2, 0)
    assert 2 not in xor_world.unset_regions(), "刻意设 a=0 不应计入未设定"
    print("0 歧义 OK: 刻意纯黑 a=0 视为已设定；未设定=未点击过")

    # 6d. 多样例数据可解析性（sim_agent 回放器 parse_log 全量覆盖 tests/example_data*.txt）
    test_sample_data()

    # 7. evaluate / view_region / tools 分派
    print(xor_world.evaluate())
    print(xor_world.view_region(1))
    print(tools.execute_tool("view_region", {"index": 1}))
    print(tools.execute_tool("evaluate", {}))
    assert tools.execute_tool("nope", {}).startswith("Unknown")

    # 8. 快照（每次运行独立文件夹 output/<时间戳>/state.json + state.png，见 agent.init_run_dir）
    import agent
    run_dir = agent.init_run_dir()
    xor_world.snapshot(run_dir)
    assert os.path.exists(os.path.join(run_dir, "state.json"))
    assert os.path.exists(os.path.join(run_dir, "state.png"))  # 渲染图存档随快照写
    assert agent.RUN_ID and os.path.isdir(os.path.join(BASE_DIR, "output", agent.RUN_ID))
    print(f"快照 OK: output/{agent.RUN_ID}/state.json + state.png")

    # 9. coverage
    assert xor_world.coverage() >= 1  # 点击过区域 1
    print("coverage OK:", xor_world.coverage())

    # 10. 流式累积（思考/正文/工具调用增量拼接）
    test_stream_accumulation()

    # 11. HTTP 可视化服务冒烟测试（需先有快照）
    test_server()

    # 12. 按轮记录回归（放在快照/服务测试之后：本项会重置 world 状态）
    test_record_round()

    # 13. 清理：删除本测试创建的运行文件夹（output/ 只应保留真实运行的存档）
    import shutil
    shutil.rmtree(agent.RUN_DIR, ignore_errors=True)
    print(f"已清理测试运行文件夹 output/{agent.RUN_ID}/")

    print("\n全部自测通过 OK")


if __name__ == "__main__":
    main()
