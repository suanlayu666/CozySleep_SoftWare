# Phase 1: Skeleton Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 1110-line `app.py` into focused backend modules (`config.py`, `state.py`, `parser.py`, `quality.py`, `serial_io.py`) without changing any behavior. All existing functionality must work identically after the split.

**Architecture:** Create a `backend/` package. Move config/state/parser/quality/serial_io each into its own module by copy-and-paste migration (no logic changes). `app.py` shrinks to ~80 lines of Flask routes that import from `backend.*`. Characterization tests lock in golden outputs before any code moves.

**Tech Stack:** Python 3.12, Flask, pyserial, pytest (new install), requests, python-dotenv

## Global Constraints

- **下位机程序保持不变** — only upper-computer code changes
- **通讯协议不变** — serial baud rate, JSON frame format, `\n` delimiter, all 6 fields intact
- **行为必须完全等价** — after refactor, `MOCK_SERIAL=true python app.py` produces identical frontend behavior
- **Python interpreter:** `C:\Users\suanlayu\AppData\Local\Programs\Python\Python312\python.exe` (Python 3.12.10)
- **Do NOT use `py` launcher** — it points to a non-existent `D:\python\python.exe`. Use absolute interpreter path or always activate venv first.
- **Chinese in terminal may garble** (Windows GBK vs UTF-8). When running `pytest`, use `-s` only when debugging; assert by value equality, not terminal output.

---

### Task 1: Setup — venv, pytest, directory structure

**Files:**
- Create: `backend/__init__.py`
- Create: `tests/__init__.py`
- Modify: (none — setup only)

**Interfaces:**
- Consumes: nothing
- Produces: a working venv with `python`, `pytest`; `backend/` and `tests/` directories exist

- [ ] **Step 1: Create venv**

```bash
C:\Users\suanlayu\AppData\Local\Programs\Python\Python312\python.exe -m venv venv
```

- [ ] **Step 2: Activate venv and verify**

```bash
source venv/Scripts/activate && python --version && which python
```
Expected: `Python 3.12.10`, and `which python` shows `.../Sleep/venv/Scripts/python`.

- [ ] **Step 3: Install pytest in venv**

```bash
source venv/Scripts/activate && pip install pytest==8.3.4
```

- [ ] **Step 4: Verify pytest works**

```bash
source venv/Scripts/activate && python -m pytest --version
```
Expected: `pytest 8.3.4`

- [ ] **Step 5: Install project deps in venv**

```bash
source venv/Scripts/activate && pip install -r requirements.txt
```

- [ ] **Step 6: Create `backend/__init__.py`**

```python
"""Backend package for soft-sleep dorm environment monitor."""
```

- [ ] **Step 7: Create `tests/__init__.py`**

```python
"""Tests for backend modules."""
```

- [ ] **Step 8: Commit**

```bash
git add backend/__init__.py tests/__init__.py
git commit -m "chore: setup venv, pytest, backend + tests scaffolding"
```

---

### Task 2: Characterization tests for parser — lock in golden values

**Files:**
- Create: `tests/test_parser.py`
- Modify: (none)

**Interfaces:**
- Consumes: `app.parse_line` (existing, before extraction)
- Produces: golden-value tests that pin current parse_line behavior exactly

- [ ] **Step 1: Write the failing test file**

```python
"""Characterization tests for parse_line — golden values captured 2026-07-04."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


def normalize(result):
    """Extract stable fields from a parse result for comparison."""
    return {
        "ok": result["ok"],
        "source": result.get("source"),
        "complete": result["complete"],
        "data_update": result.get("data_update", {}),
        "sensor_errors": result.get("sensor_errors", []),
        "missing_fields": result.get("missing_fields", []),
        "errors": result.get("errors", []),
    }


class TestParseGolden:
    """Golden-value tests: if these break, behavior has changed."""

    def test_json_normal_frame_all_6_fields(self):
        """Normal JSON frame with all 6 sensor fields."""
        line = '{"temp":26,"humi":61,"mq135_adc":2886,"mq135_mv":2329,"motion":1,"sound_db":48}'
        r = normalize(app.parse_line(line))
        assert r["ok"] is True
        assert r["source"] == "json"
        assert r["complete"] is True
        assert r["data_update"] == {
            "temperature": 26,
            "humidity": 61,
            "mq135_adc": 2886,
            "mq135_mv": 2329,
            "motion": 1,
            "sound_db": 48,
        }
        assert r["missing_fields"] == []
        assert r["errors"] == []

    def test_json_dht11_error_frame(self):
        """DHT11 failed — sensors report an error field, partial data still parsed."""
        line = '{"error":"DHT11_Failed","mq135_adc":2886,"motion":1,"sound_db":48}'
        r = normalize(app.parse_line(line))
        assert r["ok"] is True
        assert r["complete"] is False
        assert r["data_update"] == {
            "mq135_adc": 2886,
            "motion": 1,
            "sound_db": 48,
        }
        assert r["sensor_errors"] == ["DHT11_Failed"]
        assert set(r["missing_fields"]) == {"temperature", "humidity", "mq135_mv"}

    def test_csv_fallback_6_values(self):
        """6 comma-separated values fall back to CSV parsing."""
        line = "26,61,2886,2329,1,48"
        r = normalize(app.parse_line(line))
        assert r["ok"] is True
        assert r["source"] == "csv"
        assert r["complete"] is True
        assert r["data_update"] == {
            "temperature": 26,
            "humidity": 61,
            "mq135_adc": 2886,
            "mq135_mv": 2329,
            "motion": 1,
            "sound_db": 48,
        }

    def test_empty_line_returns_invalid(self):
        """Empty line should be rejected."""
        r = normalize(app.parse_line("   "))
        assert r["ok"] is False
        assert r["complete"] is False
        assert "empty line" in " ".join(r["errors"])

    def test_garbage_line_parse_failed(self):
        """Completely unrecognizable input should fail with parse error."""
        r = normalize(app.parse_line("not,json,or,csv"))
        assert r["ok"] is False
        assert r["complete"] is False

    def test_motion_coerces_to_0_or_1(self):
        """Motion field must coerce to int 0 or 1."""
        r = normalize(app.parse_line(
            '{"temp":26,"humi":61,"mq135_adc":2886,"mq135_mv":2329,"motion":0,"sound_db":48}'
        ))
        assert r["data_update"]["motion"] == 0

    def test_temperature_float_to_int(self):
        """Float temperature .0 becomes int."""
        r = normalize(app.parse_line(
            '{"temp":26.0,"humi":61,"mq135_adc":2886,"mq135_mv":2329,"motion":1,"sound_db":48}'
        ))
        assert r["data_update"]["temperature"] == 26
```

- [ ] **Step 2: Run tests — must all pass against current app.py**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/test_parser.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 3: Commit golden tests**

```bash
git add tests/test_parser.py
git commit -m "test: add parser characterization tests (golden values)"
```

---

### Task 3: Characterization tests for quality — lock in golden values

**Files:**
- Create: `tests/test_quality.py`

**Interfaces:**
- Consumes: `app.build_quality` (existing)
- Produces: golden-value tests that pin current quality judgments

- [ ] **Step 1: Write the test file**

```python
"""Characterization tests for build_quality — golden values captured 2026-07-04."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


class TestQualityGolden:

    def test_all_normal_readings(self):
        """Comfortable dorm environment — all fields normal."""
        data = {
            "temperature": 26,
            "humidity": 61,
            "mq135_adc": 2886,
            "mq135_mv": 2329,
            "motion": 1,
            "sound_db": 48,
        }
        q = app.build_quality(data)
        assert q["overall_level"] == "normal"
        assert q["summary"] == "宿舍环境整体正常"
        assert q["fields"]["temperature"]["text"] == "舒适"
        assert q["fields"]["temperature"]["level"] == "normal"
        assert q["fields"]["humidity"]["text"] == "可接受"
        assert q["fields"]["humidity"]["level"] == "normal"
        assert q["fields"]["mq135_adc"]["text"] == "良好"
        assert q["fields"]["mq135_adc"]["level"] == "normal"
        assert q["fields"]["motion"]["text"] == "有人"
        assert q["fields"]["sound_db"]["text"] == "正常"

    def test_critical_multiple_fields(self):
        """High humidity + bad air + loud noise + dead temp sensor -> overall critical."""
        data = {
            "temperature": None,
            "humidity": 95,
            "mq135_adc": 1200,
            "mq135_mv": 2000,
            "motion": 0,
            "sound_db": 80,
        }
        q = app.build_quality(data)
        assert q["overall_level"] == "critical"
        assert q["fields"]["temperature"]["level"] == "missing"
        assert q["fields"]["temperature"]["text"] == "无数据"
        assert q["fields"]["humidity"]["level"] == "critical"
        assert q["fields"]["humidity"]["text"] == "过高"
        assert q["fields"]["mq135_adc"]["level"] == "critical"
        assert q["fields"]["mq135_adc"]["text"] == "较差"
        assert q["fields"]["sound_db"]["level"] == "critical"
        assert q["fields"]["sound_db"]["text"] == "噪声较大"

    def test_warning_temperature_low(self):
        """Temperature below 18 — warning."""
        q = app.build_quality({
            "temperature": 16,
            "humidity": 50,
            "mq135_adc": 2800,
            "mq135_mv": 2200,
            "motion": 0,
            "sound_db": 40,
        })
        assert q["overall_level"] == "warning"
        assert q["fields"]["temperature"]["level"] == "warning"
        assert q["fields"]["temperature"]["text"] == "偏低"

    def test_warning_humidity_borderline(self):
        """Humidity 86-90 range — warning (偏潮)."""
        q = app.build_quality({
            "temperature": 24,
            "humidity": 88,
            "mq135_adc": 2800,
            "mq135_mv": 2200,
            "motion": 1,
            "sound_db": 50,
        })
        assert q["fields"]["humidity"]["level"] == "warning"
        assert q["fields"]["humidity"]["text"] == "偏潮"

    def test_warning_air_quality_borderline(self):
        """MQ135 ADC 1500-2499 range — warning (一般)."""
        q = app.build_quality({
            "temperature": 24,
            "humidity": 60,
            "mq135_adc": 1800,
            "mq135_mv": 1500,
            "motion": 0,
            "sound_db": 35,
        })
        assert q["fields"]["mq135_adc"]["level"] == "warning"
        assert q["fields"]["mq135_adc"]["text"] == "一般"

    def test_all_fields_missing(self):
        """No sensor data at all — everything missing."""
        q = app.build_quality({})
        assert q["overall_level"] == "warning"
        for f in ("temperature", "humidity", "mq135_adc", "mq135_mv", "motion", "sound_db"):
            assert q["fields"][f]["level"] == "missing", f"{f} should be missing"

    def test_alerts_list_populated(self):
        """Alerts list contains entries for warning/critical/missing fields."""
        q = app.build_quality({"temperature": 16, "humidity": 50,
            "mq135_adc": 2800, "mq135_mv": 2200, "motion": 0, "sound_db": 40})
        assert len(q["alerts"]) > 0
        assert isinstance(q["alerts"][0], str)
```

- [ ] **Step 2: Run tests against current app.py**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/test_quality.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_quality.py
git commit -m "test: add quality characterization tests (golden values)"
```

---

### Task 4: Extract `config.py`

**Files:**
- Create: `backend/config.py`
- Modify: (none — app.py still unchanged; config.py is additive)

**Interfaces:**
- Consumes: environment variables via `os.getenv` + `python-dotenv`
- Produces: module-level constants usable by all other backend modules

- [ ] **Step 1: Write `backend/config.py`**

```python
"""Centralized configuration — all os.getenv calls live here."""
import os
from dotenv import load_dotenv

load_dotenv()

SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
APP_PORT = int(os.getenv("APP_PORT", "5000"))
HISTORY_MAX = int(os.getenv("HISTORY_MAX", "120"))
RAW_LOG_MAX = int(os.getenv("RAW_LOG_MAX", "80"))
MOCK_SERIAL = os.getenv("MOCK_SERIAL", "false").strip().lower() in {"1", "true", "yes", "on"}

AI_API_BASE = os.getenv("AI_API_BASE", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")
COMPANION_AI_INTERVAL = int(os.getenv("COMPANION_AI_INTERVAL", "35"))
```

- [ ] **Step 2: Verify import works**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -c "from backend.config import SERIAL_PORT, BAUD_RATE; print(SERIAL_PORT, BAUD_RATE)"
```
Expected: `COM3 115200` (or whatever .env says)

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "refactor: extract config.py from app.py"
```

---

### Task 5: Extract `state.py`

**Files:**
- Create: `backend/state.py`

**Interfaces:**
- Consumes: `backend.config` (HISTORY_MAX, RAW_LOG_MAX, DEVICE_TIMEOUT, SERIAL_PORT, BAUD_RATE)
- Produces to other modules (exact signatures):
  - `latest_data: dict` — shared dict updated by parser
  - `latest_raw_values: dict`
  - `last_raw_line: str`
  - `last_update: float`
  - `last_complete_update: float`
  - `latest_frame_complete: bool`
  - `latest_frame_errors: list`
  - `latest_missing_fields: list`
  - `history: deque` (maxlen HISTORY_MAX)
  - `raw_log: deque` (maxlen RAW_LOG_MAX)
  - `stats: dict`
  - `serial_state: dict`
  - `ser: Optional[serial.Serial]` — mutable, shared across threads
  - `conversation_history: deque` (maxlen 24)
  - `companion_cache: dict`
  - Locks: `state_lock`, `history_lock`, `log_lock`, `conversation_lock`
  - `DEFAULT_DATA: dict`
  - `FIELD_ALIASES: dict`, `REQUIRED_FIELDS: tuple`, `FIELD_RANGES: dict`

- [ ] **Step 1: Write `backend/state.py`**

```python
"""Shared state — all mutable globals that multiple modules read/write."""
from collections import deque
import threading
from backend.config import HISTORY_MAX, RAW_LOG_MAX, DEVICE_TIMEOUT, SERIAL_PORT, BAUD_RATE

# --- Sensor protocol ---
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

# --- Locks ---
state_lock = threading.Lock()
history_lock = threading.Lock()
log_lock = threading.Lock()
conversation_lock = threading.Lock()

# --- Latest sensor snapshot (guarded by state_lock) ---
latest_data = dict(DEFAULT_DATA)
latest_raw_values = {}
last_raw_line = ""
last_update = 0.0
last_complete_update = 0.0
latest_frame_complete = False
latest_frame_errors = []
latest_missing_fields = list(REQUIRED_FIELDS)

# --- History ---
history = deque(maxlen=HISTORY_MAX)
raw_log = deque(maxlen=RAW_LOG_MAX)

# --- Statistics ---
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

# --- Serial state ---
serial_state = {
    "port": SERIAL_PORT,
    "baud_rate": BAUD_RATE,
    "connected": False,
    "last_error": "",
    "last_open_time": None,
}

ser = None  # pyserial handle, shared across threads

# --- Conversation ---
conversation_history = deque(maxlen=24)
companion_cache = {
    "signature": None,
    "payload": None,
    "created_at": 0.0,
}
```

- [ ] **Step 2: Commit**

```bash
git add backend/state.py
git commit -m "refactor: extract state.py from app.py"
```

---

### Task 6: Extract `parser.py`

**Files:**
- Create: `backend/parser.py`
- Modify: `tests/test_parser.py` — redirect imports from `app` → `backend.parser`

**Interfaces:**
- Consumes: `backend.state` (FIELD_ALIASES, REQUIRED_FIELDS, FIELD_RANGES)
- Produces: `parse_line(line: str) -> dict`
  - Return dict keys: `ok`, `source`, `complete`, `data_update`, `raw_values`, `sensor_errors`, `invalid_fields`, `missing_fields`, `errors`

- [ ] **Step 1: Write `backend/parser.py`**

Copy the following functions verbatim from `app.py` (no logic changes — pure relocation):
- `_pick_value(obj, aliases)` → paste unchanged
- `_coerce_field(field, value)` → paste unchanged
- `_validate_range(field, value)` → paste unchanged
- `_parse_json_frame(line)` → paste unchanged
- `_parse_csv_frame(line)` → paste unchanged
- `parse_line(line)` → paste unchanged

Then add the import at the top:

```python
"""Frame parser — STM32 JSON/CSV line → structured sensor data."""
import json
from backend.state import FIELD_ALIASES, REQUIRED_FIELDS, FIELD_RANGES
```

Remove the now-unused global-level constants (`FIELD_ALIASES`, `REQUIRED_FIELDS`, `FIELD_RANGES`) from the file — they're imported from `state`.

- [ ] **Step 2: Update `tests/test_parser.py` imports**

Replace:
```python
import app
```
with:
```python
from backend import parser
```

And change every `app.parse_line` call to `parser.parse_line`.

- [ ] **Step 3: Run tests to verify extraction didn't break parsing**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/test_parser.py -v
```
Expected: all 7 tests still PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/parser.py tests/test_parser.py
git commit -m "refactor: extract parser.py from app.py"
```

---

### Task 7: Extract `quality.py`

**Files:**
- Create: `backend/quality.py`
- Modify: `tests/test_quality.py` — redirect imports

**Interfaces:**
- Consumes: `backend.state` (FIELD_RANGES)
- Produces: `build_quality(data: dict, frame_errors: list | None = None, missing_fields: list | None = None) -> dict`
  - Return dict keys: `overall_level`, `summary`, `fields`, `alerts`

- [ ] **Step 1: Write `backend/quality.py`**

Copy the `build_quality` function verbatim from `app.py`. Add:

```python
"""Rule-based quality judgments — produces the "fact card" AI consumes."""
from backend.state import FIELD_RANGES
```

No other changes. The 130-line function body is copied exactly.

- [ ] **Step 2: Update `tests/test_quality.py` imports**

Replace `import app` → `from backend import quality`, and change every `app.build_quality` → `quality.build_quality`.

- [ ] **Step 3: Run tests**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/test_quality.py -v
```
Expected: all 7 tests still PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/quality.py tests/test_quality.py
git commit -m "refactor: extract quality.py from app.py"
```

---

### Task 8: Extract `serial_io.py`

**Files:**
- Create: `backend/serial_io.py`

**Interfaces:**
- Consumes: `backend.config` (SERIAL_PORT, BAUD_RATE, MOCK_SERIAL), `backend.state`, `backend.parser.parse_line`
- Produces: `serial_reader()`, `mock_reader()`, `process_frame(line: str) -> dict`
  - Also: helper functions `append_log`, `set_serial_state`, `is_online`, `now_str`

- [ ] **Step 1: Write `backend/serial_io.py`**

Copy verbatim from `app.py`:
- `now_str()` → paste unchanged
- `append_log(level, raw="", message="", parsed=None)` → paste unchanged
- `set_serial_state(**updates)` → paste unchanged
- `is_online()` → paste unchanged
- `process_frame(line)` → paste unchanged
- `serial_reader()` → paste unchanged
- `mock_reader()` → paste unchanged

Add imports at top:

```python
"""Serial I/O — STM32 reader, mock reader, frame processing."""
from datetime import datetime
import json
import time
import serial
from backend.config import SERIAL_PORT, BAUD_RATE, DEVICE_TIMEOUT, MOCK_SERIAL
from backend.state import (
    state_lock, history_lock, log_lock,
    latest_data, latest_raw_values, last_raw_line,
    last_update, last_complete_update,
    latest_frame_complete, latest_frame_errors, latest_missing_fields,
    history, raw_log, stats, serial_state, ser,
    DEFAULT_DATA, REQUIRED_FIELDS,
)
from backend.parser import parse_line
```

Note: `process_frame` mutates `latest_data`, `stats`, etc. via `state_lock`. The logic is unchanged — only the import source changed.

- [ ] **Step 2: Commit**

```bash
git add backend/serial_io.py
git commit -m "refactor: extract serial_io.py from app.py"
```

---

### Task 9: Slim down `app.py` to only Flask routes

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `backend.config`, `backend.state`, `backend.parser`, `backend.quality`, `backend.serial_io`

- [ ] **Step 1: Rewrite `app.py`**

Replace all code above the `# ==================== HTTP API ====================` comment block with imports from `backend.*`. The file becomes approximately 130 lines.

```python
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
    HISTORY_MAX, MOCK_SERIAL,
    AI_API_BASE, AI_API_KEY, AI_MODEL, COMPANION_AI_INTERVAL,
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
from backend.parser import parse_line
from backend.quality import build_quality
from backend.serial_io import (
    process_frame, serial_reader, mock_reader,
    append_log, set_serial_state, is_online, now_str,
)

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="assets", static_url_path="/assets")
CORS(app)

# ── Helpers (thin, app-level) ──


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


# ── Analysis (kept in app.py — they're thin facades over AI calls) ──


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


# ── Companion interaction ──

# (Copy the remaining companion functions verbatim from the old app.py:
#  _value_text, _primary_alert, _sensor_context_text,
#  build_local_companion, _companion_signature,
#  call_companion_ai, get_companion_payload,
#  get_conversation_history, append_conversation,
#  local_chat_reply, call_companion_chat_ai)


# ── HTTP API ──

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


# (Copy remaining routes verbatim:
#  /api/companion/state, /api/companion/chat, /api/ai-analyze)


# ── Startup ──

if __name__ == "__main__":
    target = mock_reader if MOCK_SERIAL else serial_reader
    t = threading.Thread(target=target, daemon=True)
    t.start()

    print(f"[服务] 后端已启动: http://localhost:{APP_PORT}")
    print(f"[串口] 配置: {SERIAL_PORT}@{BAUD_RATE}, timeout={DEVICE_TIMEOUT}s")
    print(f"[AI]   API 配置: {'已配置' if AI_API_BASE and AI_API_KEY else '未配置（将使用本地规则兜底）'}")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
```

IMPORTANT: The companion functions (`_value_text`, `_primary_alert`, `_sensor_context_text`, `build_local_companion`, `_companion_signature`, `call_companion_ai`, `get_companion_payload`, `get_conversation_history`, `append_conversation`, `local_chat_reply`, `call_companion_chat_ai`) and the three companion routes (`/api/companion/state`, `/api/companion/chat`, `/api/ai-analyze`) must be copied verbatim from the original `app.py` — do NOT rewrite or change any logic. These functions use `build_quality`, `get_state_snapshot`, `AI_API_BASE`, `AI_API_KEY`, `AI_MODEL`, `COMPANION_AI_INTERVAL`, `SERIAL_PORT`, `BAUD_RATE`, `now_str`, `conversation_lock`, `conversation_history`, `companion_cache` — all of which are now imported from `backend.*` or defined above in the new `app.py`.

- [ ] **Step 2: Run all characterization tests against refactored app.py**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/ -v
```
Expected: all 14 tests PASS (7 parser + 7 quality).

- [ ] **Step 3: Start the app with mock data and verify frontend**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && MOCK_SERIAL=true python app.py
```
Open `http://localhost:5000` in a browser. Verify: companion appears, mock frames stream, data drawer shows metrics, quality pills update. Confirm it looks identical to pre-refactor behavior.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "refactor: slim down app.py to Flask routes + companion logic"
```

---

### Task 10: Integration smoke test + cleanup

**Files:**
- Modify: (none)

- [ ] **Step 1: Full test suite**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && python -m pytest tests/ -v
```
Expected: all 14 tests PASS.

- [ ] **Step 2: Verify old `app.py` globals are not imported anywhere else**

```bash
source venv/Scripts/activate && cd d:/VScode/Sleep && grep -rn "import app" --include="*.py" backend/ tests/ 2>&1; echo "exit: $?"
```
Expected: no matches (exit 1 from grep means no files found).

- [ ] **Step 3: Manual smoke test — connect real STM32 or leave MOCK_SERIAL=true**

Start the app and confirm the frontend dashboard works identically to before the refactor.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: finalize Phase 1 skeleton refactor"
```

---

## Phase 1 Completion Checklist

After all 10 tasks:

- [ ] `app.py` is <200 lines (down from 1110)
- [ ] `backend/config.py` owns all env-var reads
- [ ] `backend/state.py` owns all shared mutable state
- [ ] `backend/parser.py` exports `parse_line` — golden tests pass
- [ ] `backend/quality.py` exports `build_quality` — golden tests pass
- [ ] `backend/serial_io.py` exports `serial_reader`, `mock_reader`, `process_frame`
- [ ] `MOCK_SERIAL=true python app.py` produces frontend behavior identical to pre-refactor
- [ ] All 14 characterization tests pass
- [ ] No module imports `app` (circular import impossible)
