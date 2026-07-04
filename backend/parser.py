"""Frame parser — STM32 JSON/CSV line → structured sensor data."""
import json
from backend.state import FIELD_ALIASES, REQUIRED_FIELDS, FIELD_RANGES


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
