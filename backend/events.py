"""Proactive event detection engine.

Maintains short rolling windows of sensor data to detect "interesting"
moments that should trigger Soft-Mianmian to speak on her own initiative.

Events detected:
- motion 0→1: user returned
- sustained late motion: user still active at system-reported hour
- humidity rising trend: consecutive increases over threshold
- air quality (mq135_adc) declining: consecutive decreases
- quality level jump: normal→warning or normal→critical

Throttling: each event type has a cooldown to avoid spamming.
"""
import time
from collections import deque

# ── Rolling state ──
_window_size = 12  # ~24 seconds at 2s/sample

_humi_window = deque(maxlen=_window_size)
_air_window = deque(maxlen=_window_size)
_prev_motion = None
_prev_quality_level = "normal"

# ── Cooldowns (seconds) ──
_cooldowns = {
    "motion_return": 0,
    "late_motion": 0,
    "humidity_rising": 0,
    "air_declining": 0,
    "quality_jump": 0,
}

_COOLDOWN_SECONDS = {
    "motion_return": 120,   # don't repeat "welcome back" within 2 min
    "late_motion": 600,     # don't repeat late-night reminder within 10 min
    "humidity_rising": 300, # 5 min
    "air_declining": 300,
    "quality_jump": 180,    # 3 min
}


def _can_fire(event_type: str) -> bool:
    now = time.time()
    last = _cooldowns.get(event_type, 0)
    cooldown = _COOLDOWN_SECONDS.get(event_type, 60)
    if now - last < cooldown:
        return False
    _cooldowns[event_type] = now
    return True


def _feed_windows(data: dict):
    """Update rolling windows with latest sensor readings."""
    humi = data.get("humidity")
    if isinstance(humi, (int, float)):
        _humi_window.append(humi)

    air = data.get("mq135_adc")
    if isinstance(air, (int, float)):
        _air_window.append(air)


def _is_consecutive_rising(window, min_count=4) -> bool:
    """Check if the most recent N values are strictly rising."""
    vals = list(window)
    if len(vals) < min_count:
        return False
    recent = vals[-min_count:]
    return all(recent[i] < recent[i + 1] for i in range(len(recent) - 1))


def _is_consecutive_falling(window, min_count=4) -> bool:
    """Check if the most recent N values are strictly falling."""
    vals = list(window)
    if len(vals) < min_count:
        return False
    recent = vals[-min_count:]
    return all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))


def check_events(snapshot: dict) -> list[str]:
    """Analyze the current sensor snapshot and return triggered event descriptions.

    Args:
        snapshot: dict from get_state_snapshot() — must include 'data', 'quality', 'online'

    Returns:
        list[str] — human-readable event descriptions, empty if nothing to report.
    """
    global _prev_motion, _prev_quality_level

    if not snapshot.get("online"):
        return []

    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or {}
    frame = snapshot.get("frame") or {}

    # Only check events on complete frames
    if not frame.get("complete"):
        return []

    _feed_windows(data)
    events = []

    # ── Motion 0→1: user returned ──
    motion = data.get("motion")
    if isinstance(motion, (int, float)):
        motion_int = int(motion)
        if _prev_motion == 0 and motion_int == 1 and _can_fire("motion_return"):
            events.append("人体感应从无人变为有人——用户可能刚回来。")
        _prev_motion = motion_int

    # ── Late night + sustained motion ──
    if isinstance(motion, (int, float)) and int(motion) == 1:
        current_hour = time.localtime().tm_hour
        if (current_hour >= 23 or current_hour <= 5) and _can_fire("late_motion"):
            events.append(f"系统时间显示现在是{current_hour}点，检测到有人在，用户可能还没休息。")

    # ── Quality level jump ──
    current_level = quality.get("overall_level", "normal")
    if _prev_quality_level == "normal" and current_level == "warning":
        if _can_fire("quality_jump"):
            events.append("环境质量从正常变为警告，需要温柔提醒用户。")
    elif _prev_quality_level == "normal" and current_level == "critical":
        if _can_fire("quality_jump"):
            events.append("环境质量从正常变为严重异常，需要立即提醒用户。")
    _prev_quality_level = current_level

    # ── Humidity rising trend ──
    if _is_consecutive_rising(_humi_window, min_count=4) and _can_fire("humidity_rising"):
        recent_humi = list(_humi_window)[-1]
        events.append(f"湿度已连续上升，当前为{recent_humi}%，屋里正在变潮。")

    # ── Air quality declining (ADC dropping = worse air) ──
    if _is_consecutive_falling(_air_window, min_count=4) and _can_fire("air_declining"):
        recent_air = list(_air_window)[-1]
        events.append(f"空气质量 ADC 值持续下降至{recent_air}，空气质量正在变差。")

    return events


def reset_state():
    """Reset all internal state (for testing)."""
    global _prev_motion, _prev_quality_level
    _humi_window.clear()
    _air_window.clear()
    _prev_motion = None
    _prev_quality_level = "normal"
    for key in _cooldowns:
        _cooldowns[key] = 0
