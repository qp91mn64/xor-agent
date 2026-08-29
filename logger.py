"""日志记录：带时间戳，输出到控制台并写入文件"""

from datetime import datetime


def get_log_filename(prefix="agent"):
    """生成带时间戳的日志文件名"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.log"


def write_log(message, log_file=None, echo=True):
    """写一行日志，带时间戳；echo=True 时同时打印到控制台"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    if echo:
        print(log_entry)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")


def get_reasoning_content(msg):
    """兼容不同 SDK 版本，安全读取模型返回的思考内容（reasoning_content）"""
    reasoning = getattr(msg, "reasoning_content", None)
    if not reasoning:
        extra = getattr(msg, "model_extra", None) or {}
        reasoning = extra.get("reasoning_content")
    return reasoning


def log_model_response(msg, log_file=None):
    """记录模型一次返回。

    - 思考与工具调用原文：只写日志文件（思考已在流式时实时打印控制台，见 agent.chat_stream）；
    - 最终输出：同时打印控制台。
    """
    reasoning = get_reasoning_content(msg)
    if reasoning:
        write_log(f"模型思考: {reasoning}", log_file, echo=False)
    if msg.tool_calls:
        for tc in msg.tool_calls:
            write_log(f"模型调用工具: {tc.function.name} args={tc.function.arguments}", log_file, echo=False)
    if msg.content:
        write_log(f"模型输出: {msg.content}", log_file)


def log_tool_call(tool_name, call_id, args, result, log_file=None):
    """记录一次工具调用：工具名、调用id、参数、执行结果"""
    write_log(f"{tool_name} id={call_id} args={args} -> {result}", log_file)
