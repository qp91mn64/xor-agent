"""XOR 画布世界：网格状态、图案渲染、指标与快照。

复刻 Interactive Drawing XOR.html 的 (dx^dy) & a 逻辑（numpy 实现）：
- 画布 512×512，8×8=64 个区域，每区参数 a ∈ [-64, 63]。
- a ≥ 0：白格 = (dx^dy)&a != 0 的像素，黑格 = (dx^dy)&a == 0。
- a < 0：掩码取 ~a，且黑白互换（a 与 ~a 互为反色）。
- a=0 全黑；a=-1 全白。
"""

import json
import os

import numpy as np

from pattern_desc import pattern_description

ROWS = 8
COLS = 8
WIDTH = 512
HEIGHT = 512
AREA = WIDTH // COLS  # 64
DATA_MAX = 63
DATA_MIN = ~DATA_MAX  # -64

# 区域内像素坐标的 XOR 表：X[dx, dy] = dx ^ dy（64×64）
_X = np.arange(AREA)[:, None] ^ np.arange(AREA)[None, :]

state = {
    "grid": None,          # list[64]：每区参数值
    "seed_index": 0,
    "seed_value": 0,
    "clicks": [],          # [{index, value, prev, reasoning}]：点击轨迹（决策可视化用）
    "status": "running",
    "final_reason": "",
}


def init(seed_value=0, seed_index=0):
    """初始化网格：全部 0（纯黑），仅种子区域设为 seed_value"""
    global state
    state["grid"] = [0] * (ROWS * COLS)
    state["grid"][seed_index] = seed_value
    state["seed_index"] = seed_index
    state["seed_value"] = seed_value
    state["clicks"] = []
    state["status"] = "running"
    state["final_reason"] = ""


def render():
    """渲染整幅 512×512 灰度图，返回 numpy 数组（0=黑，255=白）"""
    canvas = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    for y in range(ROWS):
        for x in range(COLS):
            a = state["grid"][y * COLS + x]
            mask = a if a >= 0 else ~a
            pattern = (_X & mask) != 0  # 白格位置
            if a >= 0:
                canvas[y * AREA:(y + 1) * AREA, x * AREA:(x + 1) * AREA] = np.where(pattern, 255, 0)
            else:
                canvas[y * AREA:(y + 1) * AREA, x * AREA:(x + 1) * AREA] = np.where(pattern, 0, 255)
    return canvas


def black_ratio(canvas=None):
    """整幅画布的黑像素占比"""
    if canvas is None:
        canvas = render()
    return float((canvas == 0).mean())


def set_region(index, value, reasoning=None, round_id=None):
    """set_region 工具：为区域 index 设定参数值 value（模拟一次点击）

    reasoning：模型本轮思考（一轮一次，同一轮内所有点击共享，供可视化按轮分组）；
    round_id：当前循环轮次（一轮 = 一次 API 请求），用于时间线分组。
    """
    if not (0 <= index < ROWS * COLS):
        return f"Invalid: index {index} 超出范围 0-63"
    if not (DATA_MIN <= value <= DATA_MAX):
        return f"Invalid: value {value} 超出范围 {DATA_MIN}..{DATA_MAX}"
    if index == state["seed_index"]:
        return f"Invalid: 区域 {index} 是种子区域（固定为 {state['seed_value']}），不可修改"
    prev = state["grid"][index]
    state["grid"][index] = value
    state["clicks"].append({
        "index": index, "value": value, "prev": prev,
        "reasoning": reasoning or "", "round": round_id,
    })
    return (f"区域 {index}（第{index // COLS}行第{index % COLS}列）已设为 {value}：{pattern_description(value)}"
            f"。原先为 {prev}：{pattern_description(prev)}")


def view_region(index):
    """view_region 工具：查看区域当前值与图案描述"""
    if not (0 <= index < ROWS * COLS):
        return f"Invalid: index {index} 超出范围 0-63"
    a = state["grid"][index]
    tag = "（种子区域）" if index == state["seed_index"] else ""
    return f"区域 {index}（第{index // COLS}行第{index % COLS}列）{tag}当前值 {a}：{pattern_description(a)}"


def unset_regions():
    """未设定区域 = 从未被点击过的非种子区域（以点击轨迹为准）。

    a=0（纯黑）是合法图案，不算未设定：grid 初始全 0，未点击与刻意设 0 无法靠值区分，
    只能靠"是否点击过"判定，避免 0 的歧义（纯黑图案 vs 未设定哨兵）。
    """
    clicked = {c["index"] for c in state["clicks"]}
    return [i for i in range(ROWS * COLS) if i != state["seed_index"] and i not in clicked]


def evaluate():
    """evaluate 工具：报告整幅画布进度（未设定 = 未点击过；生成范式：不提供量化指标，无优化目标）"""
    unset = unset_regions()
    filled = ROWS * COLS - 1 - len(unset)
    if unset:
        shown = "、".join(str(i) for i in unset[:10])
        suffix = " 等" if len(unset) > 10 else ""
        return (f"已设定 {filled}/63 个非种子区域，未设定 {len(unset)} 个（尚未点击，默认纯黑）："
                f"{shown}{suffix}。")
    return f"已设定 {filled}/63 个非种子区域，全部完成。"


def coverage():
    """已点击过的非种子区域数（按区域去重），用于成功判定"""
    return len({c["index"] for c in state["clicks"]})


def snapshot(out_dir="output"):
    """写 output/state.json（供实时可视化 SSE 推送与轮询兜底；画布由前端按 grid 渲染，无需 PNG）"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
