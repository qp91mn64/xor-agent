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
            "description": "渲染整幅画布，返回黑白平衡度指标与未设定区域数",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_tool(name, arguments, reasoning=None, round_id=None):
    """执行工具并返回结果文本；reasoning 为模型本轮思考，round_id 为循环轮次，均记录进点击轨迹"""
    if name == "set_region":
        return xor_world.set_region(arguments["index"], arguments["value"], reasoning, round_id)
    if name == "view_region":
        return xor_world.view_region(arguments["index"])
    if name == "evaluate":
        return xor_world.evaluate()
    return f"Unknown tool: {name}"
