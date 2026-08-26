---
name: context-log
description: "保存调试进度和问题排查记录。Invoke when: (1) starting a debugging session, (2) making significant progress on a bug, (3) user explicitly asks to save context, (4) before ending a debugging conversation."
---

# Context Log Skill

排查问题时**必须**存档进度，确保上下文不丢失。一个问题/进展一个独立文件，不同存档之间不能混淆。

## 触发时机

**必须调用此Skill的情况：**
1. 开始排查新问题时 - 创建初始记录
2. 发现重要线索或根因时 - 更新分析结果
3. 修改代码后 - 记录修改内容和位置
4. 用户明确要求保存时
5. 调试对话结束前 - 确保所有发现已记录

## 存档位置

`context-log/` 目录，文件名格式：`<YYYY-MM-DD>_<主题关键词>.md`

## 记录内容模板

```markdown
# Context Log - <问题标题>

创建时间: YYYY-MM-DD

## 问题描述
<错误信息、现象>

## 根因分析
<分析过程、结论>

## 代码位置
- 文件路径:行号 - 修改内容

## 测试结果
| 测试项 | 结果 | 备注 |

## 待确认问题
- [ ] 问题1
- [ ] 问题2

## 代码修改记录
### YYYY-MM-DD
- 文件: 修改内容
```

## 使用方法

1. 调用此Skill后，读取或创建 `context-log/<YYYY-MM-DD>_<主题>.md`
2. 按模板更新内容
3. 确保关键信息（代码位置、修改内容、修改时间）完整

## 注意事项

- 对总结出来的经验方法模板等更新之后，不写“最终版”，而是用“YYYY-MM-DD更新版”标明。
- 对于一个横跨不同代码模块的bug，涉及多个不同存档文件的，分别更新所有涉及到的存档文件。
- 如果涉及到的文件路径变化，旧记录的路径就不要动了，只加上路径变化的说明便于查找；一直用的存档记录，就更新，记录是什么时候，从什么路径变成什么路径。
