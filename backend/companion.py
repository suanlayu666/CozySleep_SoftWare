"""Companion interaction — rule-based local analysis and chat replies.

These functions provide the fallback "rule voice" when AI is unavailable,
and also serve as fact-card enrichment for the AI grounding pipeline.
"""
from backend.config import SERIAL_PORT, BAUD_RATE
from backend.quality import build_quality


# ── Small helpers ──


def _value_text(data, field, unit=""):
    value = data.get(field)
    if value is None:
        return "--"
    if field == "motion":
        return "有人" if int(value) == 1 else "无人"
    return f"{value}{unit}"


def _primary_alert(quality):
    alerts = quality.get("alerts") or []
    return alerts[0] if alerts else ""


def _sensor_context_text(data):
    return (
        f"温度{_value_text(data, 'temperature', '°C')}，"
        f"湿度{_value_text(data, 'humidity', '%')}，"
        f"空气质量{_value_text(data, 'mq135_adc', 'ADC')}，"
        f"MQ135电压{_value_text(data, 'mq135_mv', 'mV')}，"
        f"人体检测{_value_text(data, 'motion')}，"
        f"声音{_value_text(data, 'sound_db', 'dB')}"
    )


# ── Rule-based analysis ──


def local_analyze(data):
    quality = build_quality(data)
    tips = []
    fields = quality["fields"]

    humi = fields["humidity"]
    if humi["level"] == "critical":
        tips.append("湿度明显偏高，建议先开窗通风或使用除湿设备，并复查 DHT11 读数是否稳定")
    elif humi["level"] == "warning":
        tips.append("湿度需要关注，可以短时间通风并观察变化")

    temp = fields["temperature"]
    if temp["text"] == "偏高":
        tips.append("温度偏高，建议通风或开启降温设备")
    elif temp["text"] == "偏低":
        tips.append("温度偏低，建议关闭门窗或适当升温")

    air = fields["mq135_adc"]
    if air["level"] == "critical":
        tips.append("空气质量较差，建议立即通风并远离明显气味或污染源")
    elif air["level"] == "warning":
        tips.append("空气质量一般，建议保持空气流通")

    noise = fields["sound_db"]
    if noise["level"] in {"warning", "critical"}:
        tips.append("噪声偏高，建议排查声源或关闭门窗隔音")

    if not tips:
        tips.append("当前各项指标适合宿舍日常活动，建议保持现有通风和卫生状态")

    field_text = "，".join(
        f"{item['label']}{item['value'] if item['value'] is not None else '--'}{item['unit']}({item['text']})"
        for item in fields.values()
    )
    return {
        "status": f"{quality['summary']}：{field_text}",
        "suggestion": "；".join(tips) + "。",
        "quality": quality,
    }


# ── Companion payload builder ──


def build_local_companion(snapshot):
    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or build_quality(data)
    frame = snapshot.get("frame") or {}
    serial_snapshot = snapshot.get("serial") or {}

    if not snapshot.get("online"):
        port = serial_snapshot.get("port", SERIAL_PORT)
        baud = serial_snapshot.get("baud_rate", BAUD_RATE)
        return {
            "source": "rule",
            "emotion": "offline",
            "action": "sleepy",
            "stance": "等下位机上线",
            "message": "我现在还没有收到宿舍的新数据，先安静待机。",
            "detail": f"请确认 STM32 正在通过串口发送 JSON 行帧，当前配置是 {port} @ {baud}。",
            "suggestion": "等收到完整数据后，我会自动判断温湿度、空气质量、人体检测和声音情况。",
            "summary": "等待传感器数据",
            "alerts": [],
        }

    level = quality.get("overall_level", "normal")
    context = _sensor_context_text(data)
    alert = _primary_alert(quality)
    suggestion = local_analyze(data)["suggestion"]

    if not frame.get("complete"):
        return {
            "source": "rule",
            "emotion": "warning",
            "action": "checking",
            "stance": "校验数据帧",
            "message": "我收到了一些宿舍数据，但这一帧还不够完整。",
            "detail": alert or "右侧日志里可以看到缺失字段或解析错误。",
            "suggestion": "先检查下位机 JSON 字段名、换行符和串口发送频率，保证每帧都包含 temp、humi、mq135_adc、mq135_mv、motion、sound_db。",
            "summary": quality.get("summary", "数据帧需要关注"),
            "alerts": quality.get("alerts", []),
        }

    if level == "critical":
        return {
            "source": "rule",
            "emotion": "alert",
            "action": "alerting",
            "stance": "需要马上处理",
            "message": f"我有点担心，{alert or '宿舍里有明显异常'}。",
            "detail": f"当前读数是：{context}。我建议先处理最异常的那一项。",
            "suggestion": suggestion,
            "summary": quality.get("summary", "存在异常"),
            "alerts": quality.get("alerts", []),
        }

    if level == "warning":
        return {
            "source": "rule",
            "emotion": "concerned",
            "action": "thinking",
            "stance": "温柔提醒中",
            "message": f"我注意到一点需要照顾的地方：{alert or '有指标进入提醒范围'}。",
            "detail": f"当前读数是：{context}。可以先小幅调整，再观察几分钟变化。",
            "suggestion": suggestion,
            "summary": quality.get("summary", "有项目需要关注"),
            "alerts": quality.get("alerts", []),
        }

    humidity = data.get("humidity")
    detail = "环境整体稳定，适合日常学习和休息。"
    if isinstance(humidity, (int, float)) and humidity >= 80:
        detail = "湿度略靠上但仍在可接受范围，我会继续盯着它有没有继续升高。"

    return {
        "source": "rule",
        "emotion": "happy",
        "action": "breathing",
        "stance": "安心陪伴中",
        "message": "我刚看了一眼宿舍，整体状态是舒服的。",
        "detail": f"{context}。{detail}",
        "suggestion": suggestion,
        "summary": quality.get("summary", "宿舍环境整体正常"),
        "alerts": quality.get("alerts", []),
    }


# ── Rule-based chat reply ──


def local_chat_reply(message, snapshot, companion):
    if not snapshot.get("online"):
        return "我现在还没收到下位机的新数据，所以只能先帮你检查连接：确认串口号、波特率和 JSON 换行发送都正常。"

    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or build_quality(data)
    fields = quality.get("fields") or {}
    context = _sensor_context_text(data)

    if any(word in message for word in ("开窗", "通风", "空气", "闷", "异味")):
        air = fields.get("mq135_adc", {})
        humi = fields.get("humidity", {})
        if air.get("level") in {"warning", "critical"} or humi.get("level") in {"warning", "critical"}:
            return f"我会建议你先通风一会儿。现在{context}，主要需要关注的是{_primary_alert(quality) or '空气或湿度'}。"
        return f"现在空气质量读数还不错，暂时不用特意开窗。{context}，如果你主观觉得闷，可以短时间通风再观察。"

    if any(word in message for word in ("睡", "休息", "适合")):
        noise = fields.get("sound_db", {})
        if quality.get("overall_level") == "normal" and noise.get("level") == "normal":
            return f"我觉得现在适合休息。{context}，声音比较安静，温湿度也没有明显异常。"
        return f"先别急着睡，我建议处理一下提醒项：{_primary_alert(quality) or quality.get('summary')}。处理后再看数据会更安心。"

    if any(word in message for word in ("湿", "潮", "除湿")):
        humi = fields.get("humidity", {})
        return f"湿度现在是{_value_text(data, 'humidity', '%')}，判断为{humi.get('text', '--')}。{humi.get('detail') or '可以继续观察变化。'}"

    if any(word in message for word in ("温度", "热", "冷")):
        temp = fields.get("temperature", {})
        return f"温度现在是{_value_text(data, 'temperature', '°C')}，判断为{temp.get('text', '--')}。{temp.get('detail') or '体感上如果不舒服，可以按实际情况调整。'}"

    if any(word in message for word in ("吵", "声音", "噪声", "分贝")):
        noise = fields.get("sound_db", {})
        return f"声音现在是{_value_text(data, 'sound_db', 'dB')}，判断为{noise.get('text', '--')}。{noise.get('detail') or '目前声音环境比较稳定。'}"

    if "adc" in message.lower() or "mq135" in message.lower():
        air = fields.get("mq135_adc", {})
        return f"MQ135 ADC 现在是{_value_text(data, 'mq135_adc', 'ADC')}，判断为{air.get('text', '--')}。这里的规则是数值越小空气越差。"

    return f"{companion.get('message')} {companion.get('detail')}"
