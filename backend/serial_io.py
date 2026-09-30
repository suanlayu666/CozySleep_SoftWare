"""Serial I/O — STM32 reader, mock reader, frame processing."""
from datetime import datetime
import json
import time

import serial
from serial.tools import list_ports

from backend.config import SERIAL_PORT, BT_PORT, BAUD_RATE, DEVICE_TIMEOUT, MOCK_SERIAL
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
def _candidate_ports():
    """Try the last configured port first, then USB ports, then other ports."""
    ports = [port for port in list_ports.comports() if port.device != BT_PORT]

    def priority(port):
        if port.device == SERIAL_PORT:
            return (0, port.device)
        description = (port.description or "").lower()
        is_usb = port.vid is not None or "usb" in description
        return (1 if is_usb else 2, port.device)

    return [port.device for port in sorted(ports, key=priority)]


def _is_device_frame(parsed):
    data = parsed.get("data_update", {})
    return (parsed.get("ok") and parsed.get("source") == "json"
            and "mq135_adc" in data and "sound_db" in data)


def _probe_port(port_name, probe_seconds=6):
    """Only claim a port after it sends a recognizable device sensor frame."""
    connection = serial.Serial(port_name, BAUD_RATE, timeout=0.5)
    deadline = time.monotonic() + probe_seconds
    try:
        while time.monotonic() < deadline:
            raw = connection.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if line and _is_device_frame(parse_line(line)):
                return connection, line
    except Exception:
        connection.close()
        raise
    connection.close()
    return None, None


def serial_reader():
    """Discover the sensor port and rediscover it after disconnects."""
    print(f"[串口] 自动寻找设备，波特率 {BAUD_RATE} ...")
    last_valid_at = 0.0
    while True:
        if st.ser is None:
            set_serial_state(connected=False, port=None)
            try:
                candidates = _candidate_ports()
            except Exception as exc:
                candidates = []
                set_serial_state(last_error=f"枚举串口失败: {exc}")

            for port_name in candidates:
                try:
                    connection, first_line = _probe_port(port_name)
                except (OSError, serial.SerialException) as exc:
                    print(f"[串口] 跳过 {port_name}: {exc}")
                    continue
                if connection is None:
                    continue

                st.ser = connection
                last_valid_at = time.monotonic()
                set_serial_state(
                    connected=True,
                    port=port_name,
                    last_error="",
                    last_open_time=now_str(),
                )
                with st.state_lock:
                    st.stats["serial_reconnects"] += 1
                append_log("INFO", "", f"自动识别设备串口 {port_name}@{BAUD_RATE}")
                print(f"[串口] 已识别设备: {port_name}@{BAUD_RATE}")
                process_frame(first_line)
                break

            if st.ser is None:
                set_serial_state(last_error="未找到发送有效传感器数据的串口")
                time.sleep(3)
                continue

        try:
            raw = st.ser.readline()
            if raw:
                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    parsed = process_frame(line)
                    if _is_device_frame(parsed):
                        last_valid_at = time.monotonic()
            if time.monotonic() - last_valid_at > 12:
                raise TimeoutError("设备数据中断，重新寻找串口")
        except (OSError, serial.SerialException, TimeoutError) as exc:
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


def bluetooth_reader():
    """Continuously read STM32 frames from the JDY-31 Bluetooth COM port."""
    if not BT_PORT:
        print("[蓝牙] 未配置 BT_PORT，跳过蓝牙读取")
        return

    print(f"[蓝牙] 尝试连接蓝牙 {BT_PORT}@{BAUD_RATE} ...")
    bt_ser = None
    while True:
        try:
            if bt_ser is None or not bt_ser.is_open:
                bt_ser = serial.Serial(BT_PORT, BAUD_RATE, timeout=1)
                append_log("INFO", "", f"蓝牙已连接 {BT_PORT}@{BAUD_RATE}")
                print(f"[蓝牙] 已连接 {BT_PORT}@{BAUD_RATE}")

            raw = bt_ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="ignore").strip()
            if line:
                process_frame(line)
        except Exception as exc:
            err = str(exc)
            print(f"[蓝牙] 读取错误: {err}")
            try:
                if bt_ser and bt_ser.is_open:
                    bt_ser.close()
            except Exception:
                pass
            bt_ser = None
            time.sleep(3)
