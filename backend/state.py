"""Shared state — all mutable globals that multiple modules read/write."""
from collections import deque
import threading
from backend.config import HISTORY_MAX, RAW_LOG_MAX, SERIAL_PORT, BAUD_RATE

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

conversation_lock = threading.Lock()
conversation_history = deque(maxlen=24)
companion_cache = {
    "signature": None,
    "payload": None,
    "created_at": 0.0,
}
