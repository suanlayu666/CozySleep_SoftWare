"""
Dorm environment monitor - backend service.

Reads STM32 USART JSON frames, exposes REST APIs for the browser dashboard,
and provides local/AI analysis with safe fallbacks.
"""
import json
import os
import threading
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from backend.config import (
    SERIAL_PORT, BAUD_RATE, DEVICE_TIMEOUT, APP_PORT,
    MOCK_SERIAL, AI_API_BASE, AI_API_KEY, AI_MODEL,
    COMPANION_AI_INTERVAL,
)
from backend.state import (
    state_lock, history_lock, log_lock, conversation_lock,
    latest_data, latest_raw_values, last_raw_line,
    last_update, last_complete_update,
    latest_frame_complete, latest_frame_errors, latest_missing_fields,
    history, raw_log, stats, serial_state,
    DEFAULT_DATA, REQUIRED_FIELDS,
    conversation_history, companion_cache,
)
from backend.quality import build_quality
from backend.serial_io import (
    process_frame, serial_reader, mock_reader,
    append_log, set_serial_state, is_online, now_str,
)

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="assets", static_url_path="/assets")
CORS(app)


# ==================== State snapshot ====================
def get_state_snapshot():
    with state_lock:
        data = dict(latest_data)
        raw_values = dict(latest_raw_values)
        frame_errors = list(latest_frame_errors)
        missing = list(latest_missing_fields)
        stats_snapshot = dict(stats)
        serial_snapshot = dict(serial_state)
        raw_line = last_raw_line
        ts = last_update
        complete_ts = last_complete_update
        frame_complete = latest_frame_complete

    age = round(time.time() - ts, 1) if ts else None
    with log_lock:
        recent_log = list(raw_log)[-30:]

    return {
        "online": bool(ts and age is not None and age < DEVICE_TIMEOUT),
        "data": data,
        "raw_values": raw_values,
        "quality": build_quality(data, frame_errors, missing),
        "frame": {
            "complete": frame_complete,
            "errors": frame_errors,
            "missing_fields": missing,
            "raw": raw_line,
        },
        "serial": serial_snapshot,
        "stats": stats_snapshot,
        "last_update": ts,
        "last_complete_update": complete_ts,
        "last_update_str": datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "无数据",
        "last_complete_update_str": datetime.fromtimestamp(complete_ts).strftime("%H:%M:%S") if complete_ts else "无完整帧",
        "age_seconds": age,
        "device_timeout": DEVICE_TIMEOUT,
        "recent_log": recent_log,
    }


def calc_average():
    with history_lock:
        hist = list(history)
    if not hist:
        with state_lock:
            return dict(latest_data)

    result = {}
    for field in DEFAULT_DATA:
        values = [item[field] for item in hist if isinstance(item.get(field), (int, float))]
        if not values:
            result[field] = None
            continue
        avg = sum(values) / len(values)
        result[field] = int(round(avg)) if field == "motion" else round(avg, 1)
    return result


# ==================== Analysis ====================
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


def call_ai(data):
    quality = build_quality(data)
    local = local_analyze(data)
    prompt = (
        "你是宿舍环境检测系统的分析助手。请基于传感器数据给出简洁、专业、适合答辩展示的分析。\n"
        "请分为三段：环境状态、风险提醒、处理建议。不要夸张，不要编造没有传感器支持的信息。\n\n"
        f"原始数据：{json.dumps(data, ensure_ascii=False)}\n"
        f"本地规则判断：{json.dumps(quality, ensure_ascii=False)}\n"
        f"本地建议：{local['suggestion']}"
    )

    resp = requests.post(
        f"{AI_API_BASE.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "你是严谨的环境监测数据分析助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 500,
            "temperature": 0.4,
        },
        timeout=15,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    return {
        "status": "AI 分析完成",
        "suggestion": content,
        "quality": quality,
    }


# ==================== Companion interaction ====================
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
        "emotion": "cozy",
        "action": "breathing",
        "stance": "安心陪伴中",
        "message": "我刚看了一眼宿舍，整体状态是舒服的。",
        "detail": f"{context}。{detail}",
        "suggestion": suggestion,
        "summary": quality.get("summary", "宿舍环境整体正常"),
        "alerts": quality.get("alerts", []),
    }


def _companion_signature(snapshot):
    data = snapshot.get("data") or {}

    def bucket(field, step=1):
        value = data.get(field)
        if not isinstance(value, (int, float)):
            return value
        return int(round(value / step) * step)

    payload = {
        "online": snapshot.get("online"),
        "complete": (snapshot.get("frame") or {}).get("complete"),
        "level": (snapshot.get("quality") or {}).get("overall_level"),
        "temperature": bucket("temperature", 1),
        "humidity": bucket("humidity", 2),
        "mq135_adc": bucket("mq135_adc", 80),
        "sound_db": bucket("sound_db", 3),
        "alerts": ((snapshot.get("quality") or {}).get("alerts") or [])[:2],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def call_companion_ai(snapshot, local_payload):
    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or {}
    prompt = (
        '你是"软眠眠"，一个温柔、会观察传感器数据的宿舍环境伴侣。'
        "请基于当前数据主动对用户说话，像一个角色在陪伴用户，而不是仪表盘说明。"
        "要求：1到2句中文；不要列清单；不要编造传感器没有的数据；有异常要明确提醒；"
        "不要推断当前是凌晨、晚上、白天或明天，也不要推断门窗状态和用户行为；"
        "没有异常就给出安心反馈，可以顺带提一个小观察。\n\n"
        f"传感器数据：{json.dumps(data, ensure_ascii=False)}\n"
        f"本地规则判断：{json.dumps(quality, ensure_ascii=False)}\n"
        f"本地角色草稿：{json.dumps(local_payload, ensure_ascii=False)}"
    )

    resp = requests.post(
        f"{AI_API_BASE.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "你是宿舍环境监测系统里的互动角色，回答要温柔、简短、基于数据。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 180,
            "temperature": 0.7,
        },
        timeout=8,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    payload = dict(local_payload)
    payload.update({
        "source": "ai",
        "message": content,
        "generated_at": now_str(),
    })
    return payload


def get_companion_payload(snapshot):
    local_payload = build_local_companion(snapshot)
    signature = _companion_signature(snapshot)

    if not snapshot.get("online") or not AI_API_BASE or not AI_API_KEY:
        return local_payload

    now = time.time()
    with conversation_lock:
        cached = companion_cache.get("payload")
        cached_signature = companion_cache.get("signature")
        cached_at = companion_cache.get("created_at", 0.0)
        if cached and cached_signature == signature and (now - cached_at) < COMPANION_AI_INTERVAL:
            return cached
        if cached and (now - cached_at) < COMPANION_AI_INTERVAL and local_payload.get("emotion") != "alert":
            return local_payload

    try:
        ai_payload = call_companion_ai(snapshot, local_payload)
        with conversation_lock:
            companion_cache.update({
                "signature": signature,
                "payload": ai_payload,
                "created_at": now,
            })
        return ai_payload
    except Exception as exc:
        print(f"[AI] 角色主动发言失败，回退本地规则: {exc}")
        with conversation_lock:
            companion_cache.update({
                "signature": signature,
                "payload": local_payload,
                "created_at": now,
            })
        return local_payload


def get_conversation_history():
    with conversation_lock:
        return list(conversation_history)


def append_conversation(role, content):
    item = {
        "role": role,
        "content": content,
        "time": now_str(),
    }
    with conversation_lock:
        conversation_history.append(item)
    return item


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


def call_companion_chat_ai(message, snapshot, companion):
    recent = get_conversation_history()[-8:]
    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or {}

    messages = [
        {
            "role": "system",
            "content": (
                '你是"软眠眠"，宿舍环境检测系统里的 AI 互动角色。'
                "你基于 STM32 传感器数据回答用户，语气温柔、自然、简洁。"
                "不要编造没有传感器支持的信息，不要说自己能直接控制硬件；"
                "不要推断当前时间段、季节、门窗状态或用户正在做什么；"
                "需要行动时请建议用户去开窗、除湿、降噪或检查传感器。"
            ),
        },
        {
            "role": "system",
            "content": (
                f"当前传感器数据：{json.dumps(data, ensure_ascii=False)}\n"
                f"本地规则判断：{json.dumps(quality, ensure_ascii=False)}\n"
                f"当前角色状态：{json.dumps(companion, ensure_ascii=False)}"
            ),
        },
    ]
    for item in recent:
        role = "assistant" if item.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": item.get("content", "")})
    messages.append({"role": "user", "content": message})

    resp = requests.post(
        f"{AI_API_BASE.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": AI_MODEL,
            "messages": messages,
            "max_tokens": 360,
            "temperature": 0.65,
        },
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ==================== HTTP API ====================
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/sensor")
def get_sensor():
    return jsonify(get_state_snapshot())


@app.route("/api/status")
def get_status():
    return jsonify(get_state_snapshot())


@app.route("/api/history")
def get_history():
    with history_lock:
        data = list(history)
    return jsonify({"count": len(data), "history": data})


@app.route("/api/raw-log")
def get_raw_log():
    with log_lock:
        data = list(raw_log)
    return jsonify({"count": len(data), "log": data})


@app.route("/api/serial/ports")
def get_serial_ports():
    try:
        from serial.tools import list_ports
        ports = [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in list_ports.comports()
        ]
    except Exception:
        ports = []
    return jsonify({
        "configured_port": SERIAL_PORT,
        "baud_rate": BAUD_RATE,
        "ports": ports,
    })


@app.route("/api/companion/state")
def companion_state():
    snapshot = get_state_snapshot()
    companion = get_companion_payload(snapshot)
    snapshot["companion"] = companion
    snapshot["conversation"] = get_conversation_history()
    return jsonify(snapshot)


@app.route("/api/companion/chat", methods=["POST"])
def companion_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({
            "ok": False,
            "source": "none",
            "reply": "你可以直接在对话框里问我宿舍现在适不适合休息，或者该不该通风。",
        }), 400

    snapshot = get_state_snapshot()
    companion = build_local_companion(snapshot)
    source = "rule"

    if AI_API_BASE and AI_API_KEY:
        try:
            reply = call_companion_chat_ai(message, snapshot, companion)
            source = "ai"
        except Exception as exc:
            print(f"[AI] 角色对话失败，回退本地规则: {exc}")
            reply = local_chat_reply(message, snapshot, companion)
    else:
        reply = local_chat_reply(message, snapshot, companion)

    append_conversation("user", message)
    append_conversation("assistant", reply)

    return jsonify({
        "ok": True,
        "source": source,
        "reply": reply,
        "companion": companion,
        "conversation": get_conversation_history(),
    })


@app.route("/api/ai-analyze", methods=["POST"])
def ai_analyze():
    data = calc_average()
    if not any(value is not None for value in data.values()):
        return jsonify({
            "ok": False,
            "source": "none",
            "status": "暂无有效数据",
            "suggestion": "上位机还没有收到可用于分析的传感器数据，请先确认串口连接和下位机发送状态。",
        })

    if AI_API_BASE and AI_API_KEY:
        try:
            result = call_ai(data)
            return jsonify({"ok": True, "source": "ai", **result})
        except Exception as exc:
            print(f"[AI] 调用失败，回退本地规则: {exc}")

    result = local_analyze(data)
    return jsonify({"ok": True, "source": "rule", **result})


# ==================== Startup ====================
if __name__ == "__main__":
    target = mock_reader if MOCK_SERIAL else serial_reader
    t = threading.Thread(target=target, daemon=True)
    t.start()

    print(f"[服务] 后端已启动: http://localhost:{APP_PORT}")
    print(f"[串口] 配置: {SERIAL_PORT}@{BAUD_RATE}, timeout={DEVICE_TIMEOUT}s")
    print(f"[AI]   API 配置: {'已配置' if AI_API_BASE and AI_API_KEY else '未配置（将使用本地规则兜底）'}")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
