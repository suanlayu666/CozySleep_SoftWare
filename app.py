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
from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
from flask_cors import CORS

from backend import state

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
from backend.companion import (
    local_analyze, build_local_companion, local_chat_reply,
)
from backend.brain import (
    get_companion_payload as brain_companion_payload,
    chat as brain_chat,
    init_brain,
)
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
        frame_errors = list(state.latest_frame_errors)
        missing = list(state.latest_missing_fields)
        stats_snapshot = dict(stats)
        serial_snapshot = dict(serial_state)
        raw_line = state.last_raw_line
        ts = state.last_update
        complete_ts = state.last_complete_update
        frame_complete = state.latest_frame_complete

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


# ==================== Conversation state (kept in app.py) ====================
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
    companion = brain_companion_payload(snapshot)
    snapshot["companion"] = companion
    snapshot["conversation"] = get_conversation_history()
    return jsonify(snapshot)


@app.route("/api/stream")
def event_stream():
    """SSE endpoint — pushes companion state updates to the frontend."""
    def generate():
        last_sig = None
        while True:
            try:
                snapshot = get_state_snapshot()
                companion = brain_companion_payload(snapshot)
                snapshot["companion"] = companion
                snapshot["conversation"] = get_conversation_history()

                # Build a lightweight signature to skip redundant pushes
                sig = companion.get("message", "") + str(snapshot.get("last_update", 0))
                if sig != last_sig:
                    last_sig = sig
                    payload = json.dumps(snapshot, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                time.sleep(2)
            except GeneratorExit:
                break
            except Exception as exc:
                print(f"[SSE] Error: {exc}")
                time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
    result = brain_chat(message, snapshot)

    append_conversation("user", message)
    append_conversation("assistant", result["reply"])

    return jsonify({
        "ok": True,
        "source": result["source"],
        "reply": result["reply"],
        "companion": build_local_companion(snapshot),
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


@app.route("/api/memory/facts")
def memory_facts():
    from backend.memory import get_all_user_facts_as_strings, count_user_facts
    facts = get_all_user_facts_as_strings(limit=20)
    return jsonify({"count": len(facts), "facts": facts})


@app.route("/api/tts", methods=["POST"])
def tts_speak():
    """Convert text to speech, return MP3 audio."""
    from backend.config import TTS_API_KEY, TTS_APP_ID, TTS_ACCESS_TOKEN, TTS_VOICE_TYPE, TTS_ENABLED
    if not TTS_ENABLED or not TTS_API_KEY:
        return jsonify({"ok": False, "error": "TTS not configured"}), 503

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400

    # Emotion → TTS style mapping
    emotion = str(payload.get("emotion", "")).strip()
    EMOTION_STYLE = {
        "cozy": "happy",
        "concerned": "sad",
        "alert": "fearful",
        "warning": "neutral",
        "offline": "neutral",
    }
    style = EMOTION_STYLE.get(emotion, "auto")

    try:
        from backend.tts_provider import VolcanoTTS, TTSError
        tts = VolcanoTTS(
            api_key=TTS_API_KEY,
            app_id=TTS_APP_ID,
            access_token=TTS_ACCESS_TOKEN,
            voice_type=TTS_VOICE_TYPE,
        )
        audio = tts.synthesize(text, style=style)
        return Response(audio, mimetype="audio/mpeg")
    except TTSError as exc:
        print(f"[TTS] Error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/memory/reset", methods=["POST"])
def memory_reset():
    from backend.memory import get_user_facts, delete_user_fact
    for f in get_user_facts(limit=100):
        delete_user_fact(f["id"])
    return jsonify({"ok": True, "message": "记忆已清空"})


# ==================== Startup ====================
if __name__ == "__main__":
    target = mock_reader if MOCK_SERIAL else serial_reader
    t = threading.Thread(target=target, daemon=True)
    t.start()

    # Init brain with AI provider if configured
    if AI_API_BASE and AI_API_KEY:
        from backend.ai_provider import DeepSeekProvider
        provider = DeepSeekProvider(
            api_base=AI_API_BASE,
            api_key=AI_API_KEY,
            model=AI_MODEL,
        )
        init_brain(provider)
        print(f"[Brain] AI 大脑已激活: {AI_MODEL}")
    else:
        init_brain(None)
        print("[Brain] AI 未配置，使用本地规则模式")

    print(f"[服务] 后端已启动: http://localhost:{APP_PORT}")
    print(f"[串口] 配置: {SERIAL_PORT}@{BAUD_RATE}, timeout={DEVICE_TIMEOUT}s")
    print(f"[AI]   API 配置: {'已配置' if AI_API_BASE and AI_API_KEY else '未配置（将使用本地规则兜底）'}")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
