"""图案描述：值 → 图案的语义知识（单一来源模块）。

- pattern_doc()：读取 pattern_description.md 全文（手写参考文档），嵌入系统提示词，
  给模型"详细的图案描述"，让它按设计意图选值（生成范式，不追求任何量化指标）。
- pattern_description(a)：把具体参数值翻译成结构化图案事实，供 set_region / view_region 工具返回。

文档与代码对照：pattern_description.md 是图案语义的手写参考文档（感知描述/组合建议）；
pattern_description(a) 的口径与之一致（单一位=棋盘格、多位置位=白格并集、负值=反色）。
"""

import os

BASE = os.path.dirname(os.path.abspath(__file__))
DOC_PATH = os.path.join(BASE, "pattern_description.md")


def pattern_doc():
    """返回 pattern_description.md 全文（图案语义参考，供系统提示词使用）"""
    with open(DOC_PATH, encoding="utf-8") as f:
        return f.read()


def pattern_description(a):
    """把参数值 a 的图案翻译成文字（喂给模型的图案语义字典）"""
    if a == 0:
        return "纯黑"
    if a == -1:
        return "纯白"
    mask = a if a >= 0 else ~a
    bits = [k for k in range(6) if (mask >> k) & 1]
    if len(bits) == 1:
        base = f"{2 ** bits[0]}×{2 ** bits[0]}棋盘格"
    else:
        base = "白格=" + "∪".join(f"{2 ** k}×{2 ** k}棋盘" for k in bits)
    if a < 0:
        base += "（反色）"
    return base
