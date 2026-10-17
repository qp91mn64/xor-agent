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
from PIL import Image

from pattern_desc import pattern_description

ROWS = 8
COLS = 8
WIDTH = 512
HEIGHT = 512
AREA = WIDTH // COLS  # 64
DATA_MAX = 63
DATA_MIN = ~DATA_MAX  # -64
MAX_SET_PER_REGION = 3  # 同一非种子区域最多成功设定次数（首次 + 2 次调整）：允许调整，但不许反复刷同一格

# 区域内像素坐标的 XOR 表：X[dx, dy] = dx ^ dy（64×64）
_X = np.arange(AREA)[:, None] ^ np.arange(AREA)[None, :]

state = {
    "grid": None,          # list[64]：每区参数值
    "seed_index": 0,
    "seed_value": 0,
    "clicks": [],          # [{index, value, prev, round}]：点击轨迹（决策可视化用）
    "rounds": [],          # [{round, reasoning, content, tools, calls, clicks}]：按轮决策记录（唯一权威来源）
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
    state["rounds"] = []
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


def record_round(round_id, reasoning=None, content=None, calls=None):
    """落一轮的决策记录到 state["rounds"]（可视化唯一权威来源）。

    一轮 = 一次 API 请求（agent 主循环的一轮；模拟回放同构）。思考/正文由调用方在
    请求结束后一次性传入——**不能只挂在点击条目上**：只查看/只评估、以及最后输出
    总结的轮次没有 set_region 点击，挂在点击上会让这些轮的思考在任何地方都不落盘、
    界面上静默消失（2026-10-17 修复的 bug 根因）。

    calls：本轮工具调用列表，**按实际调用顺序**记录 `{"name", "args", "result"}`。
    可视化据此按时间顺序显示「思考 → 工具调用 → 工具结果 → 模型正文」——真实运行里
    工具结果是模型决策的直接依据（终端也在打印），只在 state 里存"点击"会让网页丢失
    这段因果；只存计数更会连顺序一起丢掉。
    """
    state["rounds"].append({
        "round": round_id,
        "reasoning": reasoning or "",
        "content": content or "",
        "calls": calls or [],
        "tools": _tool_counts(calls),  # 计数视图：无点击轮次靠它一眼看出"这轮做了什么"
        "clicks": sum(1 for c in state["clicks"] if c["round"] == round_id),
    })


def _tool_counts(calls):
    """工具调用次数统计 {"set_region": n, "view_region": n, ...}（便于阅读，顺序信息在 calls）"""
    counts = {}
    for c in calls or []:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    return counts


def set_count(index):
    """该区域已成功设定的次数（以点击轨迹为准，与 unset_regions/coverage 同一数据源）"""
    return sum(1 for c in state["clicks"] if c["index"] == index)


def set_region(index, value, round_id=None):
    """set_region 工具：为区域 index 设定参数值 value（模拟一次点击）

    round_id：当前循环轮次（一轮 = 一次 API 请求），用于时间线按轮分组；本轮思考
    由 record_round 按轮记录，不在此处重复保存。

    同一区域最多成功设定 MAX_SET_PER_REGION 次（首次 + 2 次调整）：模型按提示词要求
    "核对后不一致就调整"时必会重设同一格（2026-08-25 实测：先用 1~4 号格试负值语义、
    再按正式设计回改 3 格），但无上限时重复点击会吃掉整体预算（原来总点击上限 64，
    64 次点击只覆盖 61 格就提前终止，见 context-log/2026-10-17_重复点击提前终止.md）。超上限的
    返回**不带 "Invalid" 前缀**：它不是参数错误，不能被主循环的"连续无效点击"计数，
    否则模型连撞 3 次上限就会以"连续 3 次无效点击"提前终止。
    """
    if not (0 <= index < ROWS * COLS):
        return f"Invalid: index {index} 超出范围 0-63"
    if not (DATA_MIN <= value <= DATA_MAX):
        return f"Invalid: value {value} 超出范围 {DATA_MIN}..{DATA_MAX}"
    if index == state["seed_index"]:
        return f"Invalid: 区域 {index} 是种子区域（固定为 {state['seed_value']}），不可修改"
    used = set_count(index)
    if used >= MAX_SET_PER_REGION:
        a = state["grid"][index]
        return (f"区域 {index} 已设定 {used} 次（上限 {MAX_SET_PER_REGION} 次），本次未修改；"
                f"当前值 {a}：{pattern_description(a)}")
    prev = state["grid"][index]
    state["grid"][index] = value
    state["clicks"].append({
        "index": index, "value": value, "prev": prev, "round": round_id,
    })
    return (f"区域 {index}（第{index // COLS}行第{index % COLS}列）已设为 {value}：{pattern_description(value)}"
            f"（第 {used + 1}/{MAX_SET_PER_REGION} 次设定）。原先为 {prev}：{pattern_description(prev)}")


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
    """写 <out_dir>/state.json 与 <out_dir>/state.png（画布当前状态的渲染图）。

    state.json 供实时可视化（SSE 推送 + web 轮询兜底）；state.png 是同一画布状态的
    512×512 灰度渲染图（numpy render + PIL 存盘），随快照覆盖写、运行结束即最终画布，
    供后台存档/离线查看——web 前端不读此图，按 grid 用 canvas 复刻渲染。
    """
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    Image.fromarray(render()).save(os.path.join(out_dir, "state.png"))
