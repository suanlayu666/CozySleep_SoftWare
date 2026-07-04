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
