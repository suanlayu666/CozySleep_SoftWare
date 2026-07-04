"""Rule-based quality judgments — produces the "fact card" AI consumes."""
from backend.state import FIELD_RANGES


def build_quality(data, frame_errors=None, missing_fields=None):
    frame_errors = frame_errors or []
    missing_fields = set(missing_fields or [])
    quality = {}
    alerts = []

    def add(field, label, unit, level, text, detail=""):
        value = data.get(field)
        stale = field in missing_fields and value is not None
        if value is None:
            level = "missing"
            text = "无数据"
            detail = "尚未收到该传感器有效值"
        elif stale:
            level = "warning"
            detail = detail or "当前帧缺失该字段，显示上次有效值"

        item = {
            "label": label,
            "value": value,
            "unit": unit,
            "level": level,
            "text": text,
            "detail": detail,
            "stale": stale,
        }
        quality[field] = item
        if level in {"warning", "critical", "missing"}:
            alerts.append(f"{label}: {text}")

    temp = data.get("temperature")
    if temp is None:
        add("temperature", "温度", "°C", "missing", "无数据")
    elif temp < 18:
        add("temperature", "温度", "°C", "warning", "偏低", "建议关闭门窗或适当升温")
    elif temp > 30:
        add("temperature", "温度", "°C", "warning", "偏高", "建议通风或降温")
    else:
        add("temperature", "温度", "°C", "normal", "舒适")

    humi = data.get("humidity")
    if humi is None:
        add("humidity", "湿度", "%", "missing", "无数据")
    elif humi < 30:
        add("humidity", "湿度", "%", "warning", "偏干", "建议适当加湿")
    elif humi <= 85:
        add("humidity", "湿度", "%", "normal", "可接受", "宿舍环境略潮时仍可先观察")
    elif humi <= 90:
        add("humidity", "湿度", "%", "warning", "偏潮", "建议开窗通风或除湿")
    else:
        add("humidity", "湿度", "%", "critical", "过高", "建议检查传感器并及时通风除湿")

    adc = data.get("mq135_adc")
    if adc is None:
        add("mq135_adc", "空气质量", "ADC", "missing", "无数据")
    elif adc >= 2500:
        add("mq135_adc", "空气质量", "ADC", "normal", "良好", "MQ135 ADC 越小表示空气越差")
    elif adc >= 1500:
        add("mq135_adc", "空气质量", "ADC", "warning", "一般", "建议保持通风")
    else:
        add("mq135_adc", "空气质量", "ADC", "critical", "较差", "建议立即通风或远离污染源")

    mv = data.get("mq135_mv")
    if mv is None:
        add("mq135_mv", "MQ135 电压", "mV", "missing", "无数据")
    else:
        add("mq135_mv", "MQ135 电压", "mV", "normal", "正常")

    motion = data.get("motion")
    if motion is None:
        add("motion", "人体检测", "", "missing", "无数据")
    elif int(motion) == 1:
        add("motion", "人体检测", "", "normal", "有人")
    else:
        add("motion", "人体检测", "", "normal", "无人")

    db = data.get("sound_db")
    if db is None:
        add("sound_db", "噪声", "dB", "missing", "无数据")
    elif db < 45:
        add("sound_db", "噪声", "dB", "normal", "安静")
    elif db <= 60:
        add("sound_db", "噪声", "dB", "normal", "正常")
    elif db <= 75:
        add("sound_db", "噪声", "dB", "warning", "偏吵", "建议降低声源或关闭门窗")
    else:
        add("sound_db", "噪声", "dB", "critical", "噪声较大", "建议尽快排查噪声来源")

    if frame_errors:
        alerts.extend(frame_errors)

    if any(item["level"] == "critical" for item in quality.values()):
        overall_level = "critical"
        summary = "存在需要优先处理的环境异常"
    elif any(item["level"] in {"warning", "missing"} for item in quality.values()) or frame_errors:
        overall_level = "warning"
        summary = "环境基本可用，但有项目需要关注"
    else:
        overall_level = "normal"
        summary = "宿舍环境整体正常"

    return {
        "overall_level": overall_level,
        "summary": summary,
        "fields": quality,
        "alerts": alerts,
    }
