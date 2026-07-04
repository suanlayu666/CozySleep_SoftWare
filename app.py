"""
Dorm environment monitor - backend service.

Reads STM32 USART JSON frames, exposes REST APIs for the browser dashboard,
and provides local/AI analysis with safe fallbacks.
"""

from collections import deque
from datetime import datetime
import json
import os
import threading
import time

import requests
import serial
try:
    from serial.tools import list_ports
except Exception:  # pragma: no cover - pyserial may be partially installed.
    list_ports = None

from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="assets", static_url_path="/assets")
CORS(app)

# ==================== Config ====================
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
APP_PORT = int(os.getenv("APP_PORT", "5000"))
HISTORY_MAX = int(os.getenv("HISTORY_MAX", "120"))
RAW_LOG_MAX = int(os.getenv("RAW_LOG_MAX", "80"))
MOCK_SERIAL = os.getenv("MOCK_SERIAL", "false").strip().lower() in {"1", "true", "yes", "on"}

# OpenAI-compatible API. Leave key empty to use local rules only.
AI_API_BASE = os.getenv("AI_API_BASE", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")


# ==================== Sensor protocol ====================
FIELD_ALIASES = {
    "temperature": ("temp", "temperature"),
    "humidity": ("humi", "humidity"),
    "mq135_adc": ("mq135_adc", "air_adc"),
    "mq135_mv": ("mq135_mv", "air_mv"),
    "motion": ("motion", "pir"),
    "sound_db": ("sound_db", "db", "decibel"),
}

REQUIRED_FIELDS = tuple(FIELD_ALIASES.keys())

FIELD_RANGES = {
    "temperature": (-20, 80),
    "humidity": (0, 100),
    "mq135_adc": (0, 4095),
    "mq135_mv": (0, 3600),
    "motion": (0, 1),
    "sound_db": (0, 130),
}

DEFAULT_DATA = {
    "temperature": None,
    "humidity": None,
    "mq135_adc": None,
    "mq135_mv": None,
    "motion": None,
    "sound_db": None,
}


# ==================== Shared state ====================
state_lock = threading.Lock()
history_lock = threading.Lock()
log_lock = threading.Lock()

latest_data = dict(DEFAULT_DATA)
latest_raw_values = {}
last_raw_line = ""
last_update = 0.0
last_complete_update = 0.0
latest_frame_complete = False
latest_frame_errors = []
latest_missing_fields = list(REQUIRED_FIELDS)

history = deque(maxlen=HISTORY_MAX)
raw_log = deque(maxlen=RAW_LOG_MAX)

stats = {
    "received_frames": 0,
    "parsed_frames": 0,
    "complete_frames": 0,
    "partial_frames": 0,
    "parse_failed_frames": 0,
    "sensor_error_frames": 0,
    "serial_errors": 0,
    "serial_reconnects": 0,
}

serial_state = {
    "port": SERIAL_PORT,
    "baud_rate": BAUD_RATE,
    "connected": False,
    "last_error": "",
    "last_open_time": None,
}

ser = None


# ==================== Helpers ====================
def now_str():
    return datetime.now().strftime("%H:%M:%S")


def append_log(level, raw="", message="", parsed=None):
    entry = {
        "time": now_str(),
        "level": level,
        "raw": raw,
        "message": message,
    }
    if parsed is not None:
        entry["complete"] = parsed.get("complete", False)
        entry["errors"] = parsed.get("errors", [])
        entry["missing_fields"] = parsed.get("missing_fields", [])
    with log_lock:
        raw_log.append(entry)


def set_serial_state(**updates):
    with state_lock:
        serial_state.update(updates)


def is_online():
    with state_lock:
        ts = last_update
    return bool(ts and (time.time() - ts) < DEVICE_TIMEOUT)


def _pick_value(obj, aliases):
    for key in aliases:
        if key in obj:
            return obj[key], key
    return None, None


def _coerce_field(field, value):
    if value is None or value == "":
        raise ValueError("empty value")

    if field == "motion":
        coerced = int(float(value))
        if coerced not in (0, 1):
            raise ValueError("motion must be 0 or 1")
        return coerced

    coerced = float(value)
    if coerced.is_integer():
        coerced = int(coerced)
    return coerced


def _validate_range(field, value):
    lo, hi = FIELD_RANGES[field]
    if value < lo or value > hi:
        raise ValueError(f"{field} out of range [{lo}, {hi}]")


def _parse_json_frame(line):
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("JSON frame must be an object")

    data_update = {}
    raw_values = {}
    invalid_fields = []
    sensor_errors = []

    error_value = obj.get("error")
    if error_value:
        sensor_errors.append(str(error_value))

    for field, aliases in FIELD_ALIASES.items():
        raw, source_key = _pick_value(obj, aliases)
        if source_key is None:
            continue

        raw_values[field] = raw
        try:
            value = _coerce_field(field, raw)
            _validate_range(field, value)
            data_update[field] = value
        except (TypeError, ValueError) as exc:
            invalid_fields.append({"field": field, "value": raw, "error": str(exc)})

    missing_fields = [field for field in REQUIRED_FIELDS if field not in data_update]
    errors = list(sensor_errors)
    if invalid_fields:
        errors.extend([f"{item['field']}: {item['error']}" for item in invalid_fields])

    complete = not errors and not missing_fields
    ok = bool(data_update) and not invalid_fields

    return {
        "ok": ok,
        "source": "json",
        "complete": complete,
        "data_update": data_update,
        "raw_values": raw_values,
        "sensor_errors": sensor_errors,
        "invalid_fields": invalid_fields,
        "missing_fields": missing_fields,
        "errors": errors,
    }


def _parse_csv_frame(line):
    parts = [part.strip() for part in line.split(",") if part.strip()]
    if parts and parts[0].upper().startswith("ENV"):
        parts = parts[1:]
    if len(parts) < 6:
        raise ValueError("CSV frame must have 6 sensor values")

    # Fallback order for a compact STM32 frame:
    # temp,humi,mq135_adc,mq135_mv,motion,sound_db
    fields = ("temperature", "humidity", "mq135_adc", "mq135_mv", "motion", "sound_db")
    data_update = {}
    invalid_fields = []
    for field, raw in zip(fields, parts):
        try:
            value = _coerce_field(field, raw)
            _validate_range(field, value)
            data_update[field] = value
        except (TypeError, ValueError) as exc:
            invalid_fields.append({"field": field, "value": raw, "error": str(exc)})

    missing_fields = [field for field in REQUIRED_FIELDS if field not in data_update]
    errors = [f"{item['field']}: {item['error']}" for item in invalid_fields]
    return {
        "ok": bool(data_update) and not invalid_fields,
        "source": "csv",
        "complete": not errors and not missing_fields,
        "data_update": data_update,
        "raw_values": dict(zip(fields, parts)),
        "sensor_errors": [],
        "invalid_fields": invalid_fields,
        "missing_fields": missing_fields,
        "errors": errors,
    }


def parse_line(line):
    """Parse one STM32 line into a structured frame result."""
    text = line.strip()
    if not text:
        return {"ok": False, "complete": False, "errors": ["empty line"]}

    try:
        return _parse_json_frame(text)
    except json.JSONDecodeError:
        try:
            return _parse_csv_frame(text)
        except ValueError as exc:
            return {
                "ok": False,
                "complete": False,
                "data_update": {},
                "raw_values": {},
                "sensor_errors": [],
                "invalid_fields": [],
                "missing_fields": list(REQUIRED_FIELDS),
                "errors": [f"parse failed: {exc}"],
            }
    except ValueError as exc:
        return {
            "ok": False,
            "complete": False,
            "data_update": {},
            "raw_values": {},
            "sensor_errors": [],
            "invalid_fields": [],
            "missing_fields": list(REQUIRED_FIELDS),
            "errors": [f"parse failed: {exc}"],
        }


def process_frame(line):
    """Apply a received STM32 frame to backend state."""
    global last_raw_line, last_update, last_complete_update
    global latest_frame_complete, latest_frame_errors, latest_missing_fields

    parsed = parse_line(line)
    timestamp = time.time()

    with state_lock:
        stats["received_frames"] += 1
        last_raw_line = line

        if parsed.get("ok"):
            stats["parsed_frames"] += 1
            latest_data.update(parsed["data_update"])
            latest_raw_values.clear()
            latest_raw_values.update(parsed.get("raw_values", {}))
            last_update = timestamp
            latest_frame_complete = parsed.get("complete", False)
            latest_frame_errors = list(parsed.get("errors", []))
            latest_missing_fields = list(parsed.get("missing_fields", []))

            if latest_frame_complete:
                stats["complete_frames"] += 1
                last_complete_update = timestamp
            else:
                stats["partial_frames"] += 1
                if parsed.get("sensor_errors"):
                    stats["sensor_error_frames"] += 1
        else:
            stats["parse_failed_frames"] += 1
            latest_frame_complete = False
            latest_frame_errors = list(parsed.get("errors", ["parse failed"]))
            latest_missing_fields = list(REQUIRED_FIELDS)

        snapshot = dict(latest_data)

    if parsed.get("ok"):
        with history_lock:
            history.append({
                "time": now_str(),
                "timestamp": round(timestamp, 1),
                "complete": parsed.get("complete", False),
                **snapshot,
            })

    if parsed.get("complete"):
        append_log("OK", line, "完整帧", parsed)
    elif parsed.get("ok"):
        append_log("WARN", line, "部分有效帧", parsed)
    else:
        append_log("ERROR", line, "解析失败", parsed)

    return parsed


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


# ==================== Serial readers ====================
def serial_reader():
    """Continuously read STM32 frames from the configured serial port."""
    global ser
    print(f"[串口] 尝试连接 {SERIAL_PORT}@{BAUD_RATE} ...")
    while True:
        try:
            if ser is None or not ser.is_open:
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                set_serial_state(
                    connected=True,
                    last_error="",
                    last_open_time=now_str(),
                )
                with state_lock:
                    stats["serial_reconnects"] += 1
                append_log("INFO", "", f"串口已连接 {SERIAL_PORT}@{BAUD_RATE}")
                print(f"[串口] 已连接 {SERIAL_PORT}@{BAUD_RATE}")

            raw = ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="ignore").strip()
            if line:
                process_frame(line)
        except Exception as exc:
            err = str(exc)
            print(f"[串口] 读取错误: {err}")
            append_log("ERROR", "", f"串口错误: {err}")
            with state_lock:
                stats["serial_errors"] += 1
                serial_state.update({"connected": False, "last_error": err})
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(3)


def mock_reader():
    """Generate stable frames for UI testing without STM32 hardware."""
    print("[串口] MOCK_SERIAL 已启用，将生成模拟传感器数据")
    samples = [
        {"temp": 23, "humi": 82, "mq135_adc": 2886, "mq135_mv": 2329, "motion": 1, "sound_db": 28},
        {"temp": 24, "humi": 81, "mq135_adc": 2868, "mq135_mv": 2315, "motion": 1, "sound_db": 31},
        {"temp": 23, "humi": 83, "mq135_adc": 2904, "mq135_mv": 2342, "motion": 0, "sound_db": 27},
    ]
    idx = 0
    set_serial_state(connected=True, last_error="", last_open_time=now_str())
    append_log("INFO", "", "模拟串口已启动")
    while True:
        frame = json.dumps(samples[idx % len(samples)], ensure_ascii=False)
        process_frame(frame)
        idx += 1
        time.sleep(2)


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
    ports = []
    if list_ports:
        ports = [
            {
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
            }
            for port in list_ports.comports()
        ]
    return jsonify({
        "configured_port": SERIAL_PORT,
        "baud_rate": BAUD_RATE,
        "ports": ports,
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
