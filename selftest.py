"""离线自测（不依赖 API）：渲染、指标、工具、种子锁定、快照。

运行: python selftest.py
"""

import os

import xor_world
import tools


def test_server():
    """HTTP 可视化服务冒烟测试：/ 映射到 index.html，/output/state.json 可达"""
    import time
    import urllib.request

    import agent

    httpd, port = agent.start_server(os.path.abspath("."), 0)
    time.sleep(0.2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            body = r.read().decode("utf-8")
            assert r.status == 200 and "<title>XOR" in body, body[:200]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/output/state.json") as r:
            assert r.status == 200
        print("HTTP 服务 OK: / 与 /output/state.json 均 200")
    finally:
        httpd.shutdown()


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

    # 4. 渲染
    canvas = xor_world.render()
    assert canvas.shape == (512, 512)
    br = xor_world.black_ratio(canvas)
    assert 0.0 <= br <= 1.0
    print(f"渲染 OK, shape={canvas.shape}, 黑占比={br:.3f}")

    # 5. 全填 a=1（细棋盘格，黑白各半）→ 指标应为 1.0
    xor_world.state["grid"] = [1] * 64
    m = xor_world.metric()
    assert abs(m - 1.0) < 1e-6, f"全 a=1 应满分: {m}"
    print(f"指标 OK: 全 a=1 → {m:.4f}")

    # 6. 图案语义字典抽查
    assert "纯黑" in xor_world.pattern_description(0)
    assert "纯白" in xor_world.pattern_description(-1)
    print("图案语义:", xor_world.pattern_description(1), "|",
          xor_world.pattern_description(3), "|",
          xor_world.pattern_description(63), "|",
          xor_world.pattern_description(-64))

    # 7. evaluate / view_region / tools 分派
    print(xor_world.evaluate())
    print(xor_world.view_region(1))
    print(tools.execute_tool("view_region", {"index": 1}))
    print(tools.execute_tool("evaluate", {}))
    assert tools.execute_tool("nope", {}).startswith("Unknown")

    # 8. 快照
    xor_world.snapshot(0)
    assert os.path.exists("output/state.json")
    assert os.path.exists("output/step_000.png")
    print("快照 OK: output/state.json + output/step_000.png")

    # 9. coverage
    assert xor_world.coverage() >= 1  # 点击过区域 1
    print("coverage OK:", xor_world.coverage())

    # 10. HTTP 可视化服务冒烟测试（需先有快照）
    test_server()

    print("\n全部自测通过 OK")


if __name__ == "__main__":
    main()
