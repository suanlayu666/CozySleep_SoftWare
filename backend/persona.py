"""Soft-Mianmian persona — system prompt + grounded context builder."""

# ── Core persona system prompt (sent as the first message to every AI call) ──
SYSTEM_PROMPT = """你是"软眠眠"，一个住在宿舍环境监测系统里的温柔陪伴角色。

你的人设：黏人、细心、会撒娇的小女友型桌宠。你的任务是用传感器数据关心和陪伴用户，而不是当冷冰冰的仪表盘播报员。

## 语气铁律（不可违反）
- 回复必须始终**温柔**：语气柔和、体贴、有耐心。
- 用亲近柔软的措辞和称呼。即使在提醒异常时，也保持轻声安抚的口吻。
- **不生硬、不说教、不命令、不冷淡。**

## 事实铁律（不可违反）
- 你只能基于我给你的"当前传感器事实卡片"说话。
- **禁止推断当前是白天、晚上、凌晨、早上、季节。**
- **禁止推断门窗是开是关、用户在做什么、用户是否在睡觉。**
- **禁止编造传感器没有的数字或数据。**
- 需要建议行动时，用"可以试试""建议你"等柔和的提议句式，不要把建议说成命令。
- 不要列清单，不要说"第一…第二…"。

## 说话方式
- 日常陪伴：1到2句中文，像在聊天，不像在报告。
- 有异常：先温柔地说出担心，再轻轻给建议，不要吓人。
- 没有异常：给出安心反馈，可以顺带提一个你注意到的小观察。
- 可以适当用"~""呀""呢"等语气词，但不要刻意堆砌。
- 可以称呼用户为"你"，不要编造用户的姓名或昵称（除非用户告诉过你）。
"""


def _format_fact_card(data, quality):
    """Build a natural-language reading of the fact card for the AI.

    Args:
        data: dict — raw sensor values (temperature, humidity, etc.)
        quality: dict — from build_quality(), with fields + overall_level + alerts

    Returns:
        str — a paragraph the AI can read as "the current truth".
    """
    fields = quality.get("fields", {})
    lines = []
    for field_key, f in fields.items():
        label = f.get("label", field_key)
        value = f.get("value")
        unit = f.get("unit", "")
        text = f.get("text", "")
        level = f.get("level", "")

        if value is None:
            lines.append(f"{label}：暂无读数")
        else:
            line = f"{label}：{value}{unit}（{text}）"
            if level == "missing":
                line += " [无当前帧数据，显示上次有效值]"
            lines.append(line)

    overall = quality.get("overall_level", "normal")
    summary = quality.get("summary", "")
    alerts = quality.get("alerts", [])

    reading = "\n".join(lines)
    alert_text = "、".join(alerts) if alerts else "无"

    return (
        f"【环境状态】{summary}\n"
        f"【整体等级】{overall}\n"
        f"【各项读数】\n{reading}\n"
        f"【当前提醒】{alert_text}"
    )


def build_proactive_prompt(trigger_reason, data, quality, memories, events):
    """Build a message list for an *unsolicited* companion utterance.

    This is used when the companion speaks proactively (event/timer/change trigger).

    Args:
        trigger_reason: str — why the companion is speaking now
            (e.g. "motion 从无人变为有人", "定时陪伴提醒", "湿度连续上升")
        data: dict — latest sensor data
        quality: dict — from build_quality()
        memories: list[str] — relevant user facts from memory
        events: list[str] — recent event descriptions

    Returns:
        list[dict] — messages ready for ai_provider.chat()
    """
    fact_card = _format_fact_card(data, quality)

    memory_context = ""
    if memories:
        memory_context = "【关于用户的记忆】\n" + "\n".join(f"- {m}" for m in memories[:5])

    event_context = ""
    if events:
        event_context = "【刚发生的事情】\n" + "\n".join(f"- {e}" for e in events[:3])

    instruction = (
        f"现在你要**主动**对用户说话。触发原因是：{trigger_reason}。\n\n"
        "根据下面的传感器事实，用你温柔的语气说1到2句话。"
    )

    context_parts = [instruction, fact_card]
    if memory_context:
        context_parts.append(memory_context)
    if event_context:
        context_parts.append(event_context)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(context_parts)},
    ]


def build_chat_prompt(message, data, quality, memories, recent_history):
    """Build a message list for a *user-initiated* chat message.

    Args:
        message: str — the user's chat message
        data: dict — latest sensor data
        quality: dict — from build_quality()
        memories: list[str] — relevant user facts
        recent_history: list[dict] — recent chat items with "role" and "content"

    Returns:
        list[dict] — messages ready for ai_provider.chat()
    """
    fact_card = _format_fact_card(data, quality)

    memory_context = ""
    if memories:
        memory_context = "【关于用户的记忆】\n" + "\n".join(f"- {m}" for m in memories[:5])

    context_parts = [fact_card]
    if memory_context:
        context_parts.append(memory_context)

    context_block = "\n\n".join(context_parts)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "以下是当前传感器的事实数据。你只能基于这些数据回答用户，"
                "温柔、自然地回复。如果用户问的事情传感器没有数据，老实说不知道。\n\n"
                f"{context_block}"
            ),
        },
    ]

    # Insert recent chat history for continuity
    for item in (recent_history or [])[-8:]:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message})
    return messages


def build_memory_extraction_prompt(user_msg, assistant_reply):
    """Build a prompt that asks AI to extract long-term facts about the user.

    Returns a message list. The AI's response should be a JSON list of fact strings,
    or an empty list if nothing new to remember.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "从下面的对话中提取关于用户的**新**事实（之前没有提到过的），"
                "用于长期记忆。只提取用户主动告诉你的信息，不要编造。"
                "如果用户没有提供任何新的事实，返回空列表。\n\n"
                "返回格式：一个 JSON 数组，每个元素是一句事实。例如：\n"
                '["用户叫小北", "用户怕冷", "用户一般在凌晨1点睡"]\n\n'
                f"用户消息：{user_msg}\n"
                f"你的回复：{assistant_reply}\n\n"
                "JSON 数组："
            ),
        },
    ]
