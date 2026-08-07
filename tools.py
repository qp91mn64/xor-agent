"""工具定义（schema，发给模型）与执行分发表"""

import xor_world

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_region",
            "description": (
                "为某个区域选择图案参数值（模拟一次点击）。"
                "index 0-63，行优先编号（第0行0-7，第1行8-15...）。"
                "value -64..63。种子区域不可修改。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0, "maximum": 63},
                    "value": {"type": "integer", "minimum": -64, "maximum": 63},
                },
                "required": ["index", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_region",
            "description": "查看某个区域的当前参数值与图案描述",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0, "maximum": 63},
                },
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate",
            "description": "渲染整幅画布，返回进度（已设定/未设定区域数；未设定=尚未点击过；生成范式下不提供量化指标）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _require_int(arguments, key):
    """从 arguments 提取整数参数；缺键/非 dict/类型错误时返回 (None, Invalid 信息)。

    模型输出不可信：可能缺 key、把 index/value 传成字符串或浮点（如 "5"、5.0）、
    甚至整个 arguments 解析成非对象。这里把非法输入挡在工具边界外，返回 Invalid
    文本喂给 agent 的连续无效点击计数，而不是抛 KeyError/TypeError 崩掉主循环。
    """
    if not isinstance(arguments, dict) or key not in arguments:
        return None, f"Invalid: 缺少参数 {key}"
    v = arguments[key]
    if isinstance(v, bool) or not isinstance(v, int):
        return None, f"Invalid: 参数 {key} 应为整数，实际为 {v!r}"
    return v, None


def execute_tool(name, arguments, reasoning=None, round_id=None):
    """执行工具并返回结果文本；reasoning 为模型本轮思考，round_id 为循环轮次，均记录进点击轨迹"""
    if name == "set_region":
        index, err = _require_int(arguments, "index")
        if err:
            return err
        value, err = _require_int(arguments, "value")
        if err:
            return err
        return xor_world.set_region(index, value, reasoning, round_id)
    if name == "view_region":
        index, err = _require_int(arguments, "index")
        if err:
            return err
        return xor_world.view_region(index)
    if name == "evaluate":
        return xor_world.evaluate()
    return f"Unknown tool: {name}"
