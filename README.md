# Agent 测试

现有自己写的 Interactive Drawing XOR，点击不同区域即可选择不同的图案填充画布，按下 `s` 可以保存图片。

那么通过点击来选择图案的过程，能不能用AI来模拟呢？或者说，由于选择图案的本质是选择参数，能不能让AI来给不同区域选取不同的参数值？

比如说，指定一个初始值，让AI自行选择剩余区域的值，然后画图的代码读取AI选择的参数值，画出图形？

## 状态

设计已收敛并归档至 [设计文档](DESIGN.md)，代码实现尚未开始。

## 结构

```
test-agent-2/
├── DESIGN.md                     # 设计文档（问题定义、方案、工具协议、指标）
├── test-agent-src/               # 上游参考（发布时删除，改用上游链接 minimal-agent）
├── Interactive Drawing XOR.html  # 原始作品
└── agent.py / xor_world.py / tools.py / logger.py / web/ ... （待实现）
```
