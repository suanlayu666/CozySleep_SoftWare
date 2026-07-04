"""Serial I/O — STM32 reader, mock reader, frame processing."""
from datetime import datetime
import json
import time

import serial

from backend.config import SERIAL_PORT, BAUD_RATE, DEVICE_TIMEOUT, MOCK_SERIAL
from backend import state as st
from backend.parser import parse_line


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
    with st.log_lock:
        st.raw_log.append(entry)


def set_serial_state(**updates):
    with st.state_lock:
        st.serial_state.update(updates)


def is_online():
    with st.state_lock:
        ts = st.last_update
    return bool(ts and (time.time() - ts) < DEVICE_TIMEOUT)


def process_frame(line):
    """Apply a received STM32 frame to backend state."""
    parsed = parse_line(line)
    timestamp = time.time()

    with st.state_lock:
        st.stats["received_frames"] += 1
        st.last_raw_line = line

        if parsed.get("ok"):
            st.stats["parsed_frames"] += 1
            st.latest_data.update(parsed["data_update"])
            st.latest_raw_values.clear()
            st.latest_raw_values.update(parsed.get("raw_values", {}))
            st.last_update = timestamp
            st.latest_frame_complete = parsed.get("complete", False)
            st.latest_frame_errors = list(parsed.get("errors", []))
            st.latest_missing_fields = list(parsed.get("missing_fields", []))

            if st.latest_frame_complete:
                st.stats["complete_frames"] += 1
                st.last_complete_update = timestamp
            else:
                st.stats["partial_frames"] += 1
                if parsed.get("sensor_errors"):
                    st.stats["sensor_error_frames"] += 1
        else:
            st.stats["parse_failed_frames"] += 1
            st.latest_frame_complete = False
            st.latest_frame_errors = list(parsed.get("errors", ["parse failed"]))
            st.latest_missing_fields = list(st.REQUIRED_FIELDS)

        snapshot = dict(st.latest_data)

    if parsed.get("ok"):
        with st.history_lock:
            st.history.append({
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


# ==================== Serial readers ====================
def serial_reader():
    """Continuously read STM32 frames from the configured serial port."""
    print(f"[串口] 尝试连接 {SERIAL_PORT}@{BAUD_RATE} ...")
    while True:
        try:
            if st.ser is None or not st.ser.is_open:
                st.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                set_serial_state(
                    connected=True,
                    last_error="",
                    last_open_time=now_str(),
                )
                with st.state_lock:
                    st.stats["serial_reconnects"] += 1
                append_log("INFO", "", f"串口已连接 {SERIAL_PORT}@{BAUD_RATE}")
                print(f"[串口] 已连接 {SERIAL_PORT}@{BAUD_RATE}")

            raw = st.ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="ignore").strip()
            if line:
                process_frame(line)
        except Exception as exc:
            err = str(exc)
            print(f"[串口] 读取错误: {err}")
            append_log("ERROR", "", f"串口错误: {err}")
            with st.state_lock:
                st.stats["serial_errors"] += 1
                st.serial_state.update({"connected": False, "last_error": err})
            try:
                if st.ser and st.ser.is_open:
                    st.ser.close()
            except Exception:
                pass
            st.ser = None
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
